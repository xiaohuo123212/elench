#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_chase.py —— **索引追逐循环**的 κ（`15_unionfind` 那 4 个 ⊤，2026-11-12）

## 靶子

```cpp
u64 root = x;
while (d.parent[root] != root) { root = d.parent[root]; }   // ← 索引追逐
```

这条 `while` 没有计数器，`bound_of_condition` 的 `i < e` 形状一个都不沾，
于是 `find_root` / `unite` / `group_sizes` / `main` 四个函数都是 ⊤。

## 为什么可以给 `n(C)`（引用仓库**既有**口径，不是新假设）

`文档/语法清单.md`：

- 「`κ` 的口径：**对终止执行的**上界」——`κ` 界的是**会终止的那些执行**；
- 「良定义执行下**圈数 ≤ n(C)**——`i >= n(C)` 的那一次读就越界」（`bound.py`
  的"下标合法"口径同一条）。

在「这次执行会终止」的前提下：每轮都读 `C[x]` ⇒ 良定义执行里 `x ∈ [0, n(C))`；
若某个下标被走过两次，因为 `C` 在体里不变（或只被写回成守卫的另一侧），
轨迹进入周期、永不退出——与"会终止"矛盾。所以走过的下标互不相同 ⇒
**圈数 ≤ n(C)**。

## 这份测试钉什么

| # | 判据 |
|---|---|
| ① | `15_unionfind` 的 4 个函数从 ⊤ 变具体式（`n(d.parent)` 写出来） |
| ② | **等价性**：`C[x] != x` 与 `x != C[x]` 的 κ **逐字节相同**、run 输出逐字节相同 |
| ③ | **实测**：解释器返回的真实迭代次数 ≤ `n(C)`、`ops ≤ κ`，**≥3 个规模** |
| ④ | 常量守卫 `C[x] != 0` 也收（形状 A 的 `other` 可以是循环不变量） |
| ⑤ | **拒绝形状仍 ⊤**：`push` / 两处写 x / `x = C[C[x]]` / `map` / `break` / `i64` / 下标不是 x / 复合下标 / 体内写 C / 路径压缩写回值对不上 |
| ⑥ | `--explain` 开关前后 κ **逐字节相同**（注记只进侧表） |

跑法：

    cd tests/cases; $env:PYTHONIOENCODING="utf-8"; python test_kappa_chase.py
"""

import contextlib
import io as _io
import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
for _p in (SRC, ROOT):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

from cost_check import evaluate as kappa_at             # noqa: E402
from cost_interp import CostInterp                      # noqa: E402
from interp import Interp, StructVal                     # noqa: E402
from interp import ops_enable, ops_of, ops_reset         # noqa: E402
from parser import parse_source                          # noqa: E402

_passed = 0
_failed = []


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


# ---------------------------------------------------------------- 程序侧
#: 与 `demo/corpus/15_unionfind.cpp` 的 `find_root` **同形**（含路径压缩的第二个
#: 循环）。第一个循环是"纯追逐"，第二个是"路径压缩式追逐"。
CH = """
struct Dsu {
    parent: vector<u64>,
    rank: vector<u64>,
}

u64 find_root(Dsu& d, u64 x) {
    u64 root = x;
    while (d.parent[root] != root) { root = d.parent[root]; }
    while (d.parent[x] != root) {
        u64 next = d.parent[x];
        d.parent[x] = root;
        x = next;
    }
    return root;
}

u64 chase_cnt(const vector<u64>& C, u64 x) {
    u64 n = 0;
    while (C[x] != x) { x = C[x]; n = n + 1; }
    return n;
}

u64 chase_cnt_alt(const vector<u64>& C, u64 x) {
    u64 n = 0;
    while (x != C[x]) { x = C[x]; n = n + 1; }
    return n;
}

u64 chase_const(const vector<u64>& C, u64 x) {
    u64 n = 0;
    while (C[x] != 0) { x = C[x]; n = n + 1; }
    return n;
}

// ---- 拒绝形状：每一条都必须仍是 ⊤ ----

u64 r_push(vector<u64>& C, u64 x) {
    u64 n = 0;
    while (C[x] != x) { C.push_back(x); x = C[x]; n = n + 1; }
    return n;
}

u64 r_two_writes(const vector<u64>& C, u64 x) {
    while (C[x] != x) { x = C[x]; x = C[x]; }
    return x;
}

u64 r_nested(const vector<u64>& C, u64 x) {
    while (C[x] != x) { x = C[C[x]]; }
    return x;
}

u64 r_map(map<u64, u64>& M, u64 x) {
    while (M[x] != x) { x = M[x]; }
    return x;
}

u64 r_break(const vector<u64>& C, u64 x) {
    while (C[x] != x) {
        if (C[x] == 0) { break; }
        x = C[x];
    }
    return x;
}

u64 r_i64(const vector<u64>& C, i64 x) {
    while (C[x] != x) { x = C[x]; }
    return 0;
}

u64 r_key_not_x(const vector<u64>& C, u64 i, u64 x) {
    while (C[i] != x) { x = C[i]; }
    return x;
}

u64 r_index_expr(const vector<u64>& C, u64 x) {
    while (C[x + 1] != x) { x = C[x]; }
    return x;
}

u64 r_elem_write(vector<u64>& C, u64 x) {
    while (C[x] != x) { C[x] = x; x = C[x]; }
    return x;
}

u64 r_tmp_wrong(vector<u64>& C, u64 x, u64 root) {
    while (C[x] != root) {
        u64 next = C[x];
        C[x] = 0;
        x = next;
    }
    return x;
}
"""


def _mods():
    ch = parse_source(CH, "chase.eln").module
    return ch, CostInterp(ch, None)


def _ci(explain=False):
    ch = parse_source(CH, "chase.eln").module
    return ch, CostInterp(ch, None, explain=explain)


def _run(ch, fn_name, args):
    """解释器跑一次，返回 `(返回值, 该函数的 ops, stdout)`。"""
    ops_enable(True)
    try:
        ops_reset()
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            val = Interp(ch, None).call(fn_name, args)
        return val, ops_of(fn_name), buf.getvalue()
    finally:
        ops_enable(False)


def _chain(n):
    """父指针链：`C[i] = i+1`，末位自指。从 0 出发恰好走 n-1 步。"""
    return list(range(1, n)) + [n - 1]


# ---------------------------------------------------------------- ① 并查集 4 个
def test_unionfind_four_concrete():
    print("[1] `15_unionfind` 同形：4 类调用全部从 ⊤ 变具体式")
    ch, ci = _mods()
    k_root = ci.cost_of("find_root")
    txt = k_root.render()
    check(not k_root.is_top(), "find_root 的 κ 不是 ⊤", txt)
    check("n(d.parent)" in txt, "κ 用成员容器的尺寸量 `n(d.parent)` 写出来", txt)
    check(k_root.variables() == ["d.parent"],
          "κ 的尺寸量恰是 `d.parent`", str(k_root.variables()))

    # 级联：调用 find_root 的函数不再因为被调方是 ⊤ 而变 ⊤（unite 同形）。
    for fn in ("chase_cnt", "chase_cnt_alt", "chase_const"):
        k = ci.cost_of(fn)
        check(not k.is_top(), f"{fn} 的 κ 不是 ⊤", k.render())

    # 实测：链长 n-1，两个循环各走 n-1 步；ops ≤ κ。
    for n in (4, 8, 16, 32):
        d = StructVal("Dsu", {"parent": _chain(n), "rank": [0] * n})
        got, ops, _out = _run(ch, "find_root", [d, 0])
        kv = kappa_at(k_root, {"d.parent": n})
        check(got == n - 1, f"n={n}: find_root 返回根 {n - 1}（走到了链尾）", str(got))
        check(ops <= kv, f"n={n}: ops {ops} ≤ κ {kv:g}（κ 仍是上界）",
              f"ops={ops} κ={kv}")


# ---------------------------------------------------------------- ② 等价性
def test_equivalence():
    print("[2] 等价性：`C[x] != x` vs `x != C[x]` —— κ 与 run 逐字节相同")
    ch, ci = _mods()
    t1 = ci.cost_of("chase_cnt").render()
    t2 = ci.cost_of("chase_cnt_alt").render()
    check(t1 == t2, "两种写法的 κ **逐字节相同**", f"{t1!r}\n         {t2!r}")
    for n in (4, 16, 64):
        C = _chain(n)
        v1, o1, s1 = _run(ch, "chase_cnt", [C, 0])
        v2, o2, s2 = _run(ch, "chase_cnt_alt", [C, 0])
        check(v1 == v2 and s1 == s2,
              f"n={n}: run 返回值 / 输出逐字节相同", f"{v1!r} vs {v2!r}")
        check(o1 == o2, f"n={n}: ops 也相同（同一份成本）", f"{o1} vs {o2}")


# ---------------------------------------------------------------- ③ 实测
def test_measured_bound():
    print("[3] 实测真实迭代次数 ≤ n(C)，且 ops ≤ κ（≥3 个规模）")
    ch, ci = _mods()
    k_cnt = ci.cost_of("chase_cnt")
    k_zero = ci.cost_of("chase_const")
    scales = []
    for n in (4, 16, 64):
        C = _chain(n)
        cnt, ops, _ = _run(ch, "chase_cnt", [C, 0])
        kv = kappa_at(k_cnt, {"C": n})
        scales.append((n, cnt, ops, kv))
        check(cnt <= n, f"n={n}: 真实迭代 {cnt} ≤ n(C) {n}（界成立）",
              f"{cnt}>{n}")
        # 夹具的链长就是 n-1：证明确实跑到了正题，不是早退路径。
        check(cnt == n - 1, f"n={n}: 夹具链长恰为 {n - 1}（不是早退）", str(cnt))
        check(ops <= kv, f"n={n}: ops {ops} ≤ κ {kv:g}", f"ops={ops} κ={kv}")
    check(len(scales) >= 3, "至少 3 个规模都测了", str(scales))

    # 常量守卫：`C[x] != 0`（末位指向 0）。
    for n in (4, 16, 64):
        C = list(range(1, n)) + [0]
        cnt, ops, _ = _run(ch, "chase_const", [C, 0])
        kv = kappa_at(k_zero, {"C": n})
        check(cnt <= n, f"常量守卫 n={n}: 真实迭代 {cnt} ≤ n(C) {n}", str(cnt))
        check(ops <= kv, f"常量守卫 n={n}: ops {ops} ≤ κ {kv:g}",
              f"ops={ops} κ={kv}")


# ---------------------------------------------------------------- ④ 拒绝形状
def test_refusals_stay_top():
    print("[4] 拒绝形状：每一条都必须仍是 ⊤")
    _ch, ci = _mods()
    cases = [
        ("r_push", "体内 `push`（下标空间会变）"),
        ("r_two_writes", "体内两处写 `x`"),
        ("r_nested", "`x = C[C[x]]`（多步）"),
        ("r_map", "`C` 是 `map`（下标不是序号）"),
        ("r_break", "体内有 `break`"),
        ("r_i64", "`x` 是 `i64`（可能为负 ⇒ 下标越界）"),
        ("r_key_not_x", "下标不是被追逐的 `x`（`x < n(C)` 没有被守卫见证）"),
        ("r_index_expr", "下标是 `x + 1`（复合下标，越界没有被守卫读见证）"),
        ("r_elem_write", "形状 A 体内写了 `C`"),
        ("r_tmp_wrong", "路径压缩写回值 ≠ 守卫另一侧"),
    ]
    for fn, why in cases:
        k = ci.cost_of(fn)
        check(k.is_top(), f"{fn} 仍是 ⊤ —— {why}", k.render())


# ---------------------------------------------------------------- ⑤ explain
def test_explain_does_not_change_kappa():
    print("[5] `--explain` 只写侧表：开关前后 κ 逐字节相同")
    _ch, off = _ci(explain=False)
    _ch2, on = _ci(explain=True)
    for fn in ("find_root", "chase_cnt", "chase_cnt_alt", "chase_const"):
        a = off.cost_of(fn).render()
        b = on.cost_of(fn).render()
        check(a == b, f"{fn}: explain 开关前后 κ 逐字节相同", f"{a!r} vs {b!r}")


def main() -> int:
    test_unionfind_four_concrete()
    test_equivalence()
    test_measured_bound()
    test_refusals_stay_top()
    test_explain_does_not_change_kappa()

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    _sys.exit(main())
