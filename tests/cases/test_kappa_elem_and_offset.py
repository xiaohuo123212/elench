#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_elem_and_offset.py —— 两个 ⊤ 家族（2026-11-12）

## 为什么要这份测试

语料 `demo/corpus` 的两类最自然写法算不出 κ：

    for (k = 0; k < g[v].size(); ++k) …                  // g[v] 是外层容器的元素
    for (i = from; i + needle.size() <= hay.size(); ++i) // 归纳变量 + 尺寸量

本次把两条都做成**上界**（不是"看起来像符号"）：

| 写法 | κ 里的量 | 语义 |
|---|---|---|
| `g[v].size()` | `L(g)` | `L(g) := max_i n(g[i])`：**元素长度的上界**。`v` 合法 ⇒ `n(g[v]) ≤ L(g)` |
| `i + C <= N` | `N + 1` | `i` 从非负起、每轮严格增大 ⇒ 轮数 ≤ `N + 1`（`<` 那一侧是 `N`） |

## 判据（本文件钉的就是它）

1. **κ 是上界**：解释器真的跑（`ops ≤ κ`），不是"形状对就算过"。
2. **`n(g)` 不是 `g[v].size()` 的上界**：`vector<vector<u64>>` 里每行可以比行数长。
   本文件专门造一组 `n(g)=2、每行 100` 的数据：按 `n(g)` 算的"界"会**低于实测**，
   按 `L(g)` 算的不会。这条是把「① n(g[v]) ≤ n(g)」路线**拒绝掉**的证据。
3. **前提不成立就 ⊤**：`g` 不是 const 形参（可能被改）⇒ ⊤；`i` 的初值可能是负的
   （`i64`）⇒ ⊤；偏移量是变量（`i + k`，`k` 可能为负）⇒ ⊤；计数器会减小 ⇒ ⊤。
4. **等价性**：同一逻辑的两种写法 κ **逐字节相同**、`run` 输出**逐字节相同**。
5. **两边都真的进契约**：偏移上界的 target（`O(hay.size()*needle.size())`）
   过 `check`；`L(g)` 的 target 由 `cost` 判达成（`check` 的形状白名单还差一个
   名字，见 §8/§9 的说明——那是 `src/checker.py`，不在本次可改范围）。

跑法：

    cd src; $env:PYTHONIOENCODING="utf-8"; python test_kappa_elem_and_offset.py
"""

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

import io
import os
import random
import subprocess
import sys
import tempfile

from cost_check import compare, evaluate as kappa_at
from cost_interp import CostInterp
from interp import Interp, ops_enable, ops_of, ops_reset
from parser import parse_source

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


# ---------------------------------------------------------------- 声明侧
TU_ALL = """spec f_elem(const vector<vector<u64>>& g) -> u64 {
    target { time: { max: O(g.size() * L(g)) }, allocation: { max: 0 }, };
}

spec f_elem_alt(const vector<vector<u64>>& g) -> u64 {
    target { time: { max: O(g.size() * L(g)) }, allocation: { max: 0 }, };
}

spec f_off(const vector<u8>& hay, const vector<u8>& needle, u64 from) -> u64 {
    target { time: { max: O(hay.size() * needle.size()) }, allocation: { max: 0 }, };
}

spec f_off_alt(const vector<u8>& hay, const vector<u8>& needle, u64 from) -> u64 {
    target { time: { max: O(hay.size() * needle.size()) }, allocation: { max: 0 }, };
}
"""

#: 前提不成立 → 必须 ⊤（没有 target，也不参与 check）。
TU_TOP = """spec f_elem_mut(vector<vector<u64>>& g) -> u64 {
    effects {};
}

spec f_off_i64(const vector<u8>& hay, i64 from) -> u64 {
    effects {};
}

spec f_off_var(const vector<u8>& hay, const vector<u8>& needle, i64 k) -> u64 {
    effects {};
}

spec f_off_dec(const vector<u8>& hay, const vector<u8>& needle, u64 from) -> u64 {
    effects {};
}

spec f_off_shadow(const vector<u8>& hay, const vector<u8>& needle, i64 from) -> u64 {
    effects {};
}
"""

CH_ALL = """u64 f_elem(const vector<vector<u64>>& g) {
    u64 sum = 0;
    u64 v = 0;
    while (v < g.size()) {
        u64 k = 0;
        while (k < g[v].size()) {
            sum = sum + g[v][k];
            k = k + 1;
        }
        v = v + 1;
    }
    return sum;
}

u64 f_elem_alt(const vector<vector<u64>>& g) {
    u64 sum = 0;
    u64 v = 0;
    while (v < g.size()) {
        u64 k = 0;
        while (g[v].size() > k) {
            sum = sum + g[v][k];
            k = k + 1;
        }
        v = v + 1;
    }
    return sum;
}

u64 f_off(const vector<u8>& hay, const vector<u8>& needle, u64 from) {
    u64 i = from;
    u64 hits = 0;
    while (i + needle.size() <= hay.size()) {
        u64 k = 0;
        while (k < needle.size()) {
            if (hay[i + k] == needle[k]) { hits = hits + 0; }
            k = k + 1;
        }
        hits = hits + 1;
        i = i + 1;
    }
    return hits;
}

u64 f_off_alt(const vector<u8>& hay, const vector<u8>& needle, u64 from) {
    u64 i = from;
    u64 hits = 0;
    while (hay.size() >= i + needle.size()) {
        u64 k = 0;
        while (k < needle.size()) {
            if (hay[i + k] == needle[k]) { hits = hits + 0; }
            k = k + 1;
        }
        hits = hits + 1;
        i = i + 1;
    }
    return hits;
}
"""

CH_TOP = """u64 f_elem_mut(vector<vector<u64>>& g) {
    u64 sum = 0;
    u64 v = 0;
    while (v < g.size()) {
        u64 k = 0;
        while (k < g[v].size()) {
            sum = sum + g[v][k];
            k = k + 1;
        }
        v = v + 1;
    }
    return sum;
}

u64 f_off_i64(const vector<u8>& hay, i64 from) {
    i64 i = from;
    u64 hits = 0;
    while (i + 2 <= hay.size()) {
        hits = hits + 1;
        i = i + 1;
    }
    return hits;
}

u64 f_off_var(const vector<u8>& hay, const vector<u8>& needle, i64 k) {
    u64 i = 0;
    u64 hits = 0;
    while (i + k <= hay.size()) {
        hits = hits + 1;
        i = i + 1;
    }
    return hits;
}

u64 f_off_dec(const vector<u8>& hay, const vector<u8>& needle, u64 from) {
    u64 i = from;
    u64 hits = 0;
    while (i + needle.size() <= hay.size()) {
        hits = hits + 1;
        i = i - 1;
    }
    return hits;
}

u64 f_off_shadow(const vector<u8>& hay, const vector<u8>& needle, i64 from) {
    i64 i = from;
    {
        u64 i = 0;
        while (i < 3) {
            i = i + 1;
        }
    }
    u64 hits = 0;
    while (i + needle.size() <= hay.size()) {
        hits = hits + 1;
        i = i + 1;
    }
    return hits;
}
"""

#: 单独切出来给子进程用（模块级字符串拼接简单粗暴，避免再抄一遍）。
_TU_OFF = ("spec f_off(const vector<u8>& hay, const vector<u8>& needle, u64 from) -> u64 {\n"
           "    target { time: { max: O(hay.size() * needle.size()) }, "
           "allocation: { max: 0 }, };\n}\n\n"
           "spec f_off_alt(const vector<u8>& hay, const vector<u8>& needle, u64 from) -> u64 {\n"
           "    target { time: { max: O(hay.size() * needle.size()) }, "
           "allocation: { max: 0 }, };\n}\n")
_CH_OFF = ("u64 f_off(const vector<u8>& hay, const vector<u8>& needle, u64 from) {\n"
           "    u64 i = from;\n"
           "    u64 hits = 0;\n"
           "    while (i + needle.size() <= hay.size()) {\n"
           "        hits = hits + 1;\n"
           "        i = i + 1;\n"
           "    }\n"
           "    return hits;\n"
           "}\n\n"
           "u64 f_off_alt(const vector<u8>& hay, const vector<u8>& needle, u64 from) {\n"
           "    u64 i = from;\n"
           "    u64 hits = 0;\n"
           "    while (hay.size() >= i + needle.size()) {\n"
           "        hits = hits + 1;\n"
           "        i = i + 1;\n"
           "    }\n"
           "    return hits;\n"
           "}\n")


def _mods(tu_src, ch_src, name):
    tu = parse_source(tu_src, name + ".tu").module
    ch = parse_source(ch_src, name + ".eln").module
    return tu, ch, CostInterp(ch, tu)


def _run(mods, fn, args):
    """跑一次，返回 `(返回值, ops, 循环迭代总数)`。"""
    _tu, ch, _ci = mods
    ops_enable(True)
    try:
        ops_reset()
        it = Interp(ch, None)
        out = it.call(fn, args)
        return out, ops_of(fn), it.iters
    finally:
        ops_enable(False)


def _rows(n, seed):
    """造一组**行长不相等、且可以比行数长**的邻接表。"""
    rng = random.Random(seed)
    return [[rng.randrange(0, 100) for _ in range(rng.randrange(1, 2 * n + 1))]
            for _ in range(n)]


# ---------------------------------------------------------------- [1] L(g)
def test_elem_basic():
    print("[1] 内层容器尺寸：`g[v].size()` → `L(g)`（元素长度的上界）")
    mods = _mods(TU_ALL, CH_ALL, "elem_basic")
    _tu, _ch, ci = mods
    k = ci.cost_of("f_elem")
    text = k.render()
    check(k.kind != "unknown", "f_elem 的 κ 不是 ⊤", text)
    check("L(g)" in text, "κ 里是元素长度上界 `L(g)`", text)
    check("L(g)" in k.variables(),
          "`L(g)` 是一个**尺寸量**（进 variables）", str(k.variables()))
    t = ci.target_cost("f_elem")
    check(t is not None and t.render() == "n(g)×L(g)",
          "target `O(g.size() * L(g))` 与 κ 用同一个尺寸量",
          t.render() if t else None)
    check(compare(k, t).kind == "ok", "判定达成（同阶）", compare(k, t).render())

    for n in (4, 8, 16):
        g = _rows(n, 100 + n)
        L = max(len(r) for r in g)
        out, ops, iters = _run(mods, "f_elem", [g])
        kv = kappa_at(k, {"g": len(g), "L(g)": L})
        check(ops <= kv,
              f"n={n}: ops {ops} ≤ κ {kv:g}（L(g)={L}，行长可以 > n）",
              f"ops={ops} κ={kv}")
        check(iters <= len(g) * (L + 1),
              f"n={n}: 真实迭代 {iters} ≤ 推出的轮数 n(g)×(L(g)+1) = "
              f"{len(g) * (L + 1)}", f"iters={iters}")
        check(out == sum(sum(r) for r in g), f"n={n}: 返回值与求和一致", str(out))


# ---------------------------------------------------------------- [2] n(g) 不是界
def test_ng_is_not_a_bound():
    print("[2] 反证：`n(g)` **不是** `g[v].size()` 的上界（所以不走 ① 那条路）")
    mods = _mods(TU_ALL, CH_ALL, "elem_ng")
    _tu, _ch, ci = mods
    k = ci.cost_of("f_elem")
    text = k.render()

    # n(g) = 2、每行 100：行长比行数长得多。
    g = [[7] * 100, [7] * 100]
    _, ops, iters = _run(mods, "f_elem", [g])

    # 把 κ 里的 `L(g)` **错误地**当成 n(g) 求值（这就是"n(g[v]) ≤ n(g)"那条路）。
    wrong = kappa_at(k, {"g": len(g), "L(g)": len(g)})
    right = kappa_at(k, {"g": len(g), "L(g)": max(len(r) for r in g)})
    check(ops > wrong,
          f"按 n(g)={len(g)} 当 L 求值：κ={wrong:g} < ops={ops} ⇒ **不是上界**",
          f"wrong={wrong} ops={ops}")
    check(ops <= right,
          f"按真实 L(g)=100 求值：κ={right:g} ≥ ops={ops} ⇒ 上界成立",
          f"right={right} ops={ops}")
    check("n(g)" in text and "L(g)" in text,
          "κ 同时含 n(g) 与 L(g)（两个独立尺寸量）", text)


# ---------------------------------------------------------------- [3] 等价性 A
def test_elem_equivalence():
    print("[3] 等价性：`k < g[v].size()` vs `g[v].size() > k` —— κ 与 run 逐字节相同")
    a = _mods(TU_ALL, CH_ALL, "elem_eq")
    _tu, _ch, ci = a
    t1 = ci.cost_of("f_elem").render()
    t2 = ci.cost_of("f_elem_alt").render()
    check(t1 == t2, "两种写法的 κ **逐字节相同**", f"{t1!r}\n         {t2!r}")
    g = _rows(9, 4242)
    o1, _, _ = _run(a, "f_elem", [g])
    o2, _, _ = _run(a, "f_elem_alt", [g])
    check(o1 == o2, "两种写法的 run 输出逐字节相同", f"{o1!r} vs {o2!r}")


# ---------------------------------------------------------------- [4] 前提
def test_elem_premises():
    print("[4] 前提：外层容器不是 const 形参 ⇒ ⊤（`L(g)` 不再是全程上界）")
    mods = _mods(TU_TOP, CH_TOP, "elem_top")
    _tu, _ch, ci = mods
    k = ci.cost_of("f_elem_mut")
    check(k.is_top(), "非 const 的 `vector<vector<u64>>& g` ⇒ κ = ⊤", k.render())
    check("L(g)" not in k.render(), "⊤ 里没有伪造的 `L(g)`", k.render())


# ---------------------------------------------------------------- [5] 偏移上界
def test_offset_basic():
    print("[5] 偏移上界：`i + needle.size() <= hay.size()` → 轮数 ≤ `n(hay) + 1`")
    mods = _mods(TU_ALL, CH_ALL, "off_basic")
    _tu, _ch, ci = mods
    k = ci.cost_of("f_off")
    text = k.render()
    check(k.kind != "unknown", "f_off 的 κ 不是 ⊤", text)
    check("(n(hay) + 1)" in text, "轮数上界是 `n(hay) + 1`（`<=` 多留一圈）", text)
    check("n(needle)" in text, "内层上界 `n(needle)` 仍在 κ 里", text)
    t = ci.target_cost("f_off")
    check(t is not None and t.render() == "n(hay)×n(needle)",
          "target `O(hay.size() * needle.size())` 解析成同一个式子",
          t.render() if t else None)
    check(compare(k, t).kind == "ok", "判定达成（同阶）", compare(k, t).render())

    for n in (8, 16, 32):
        hay = [i % 7 for i in range(n)]
        needle = [1, 2, 3]
        hits, ops, iters = _run(mods, "f_off", [hay, needle, 0])
        kv = kappa_at(k, {"hay": n, "needle": len(needle)})
        check(ops <= kv, f"n={n}: ops {ops} ≤ κ {kv:g}", f"ops={ops} κ={kv}")
        check(hits <= n + 1,
              f"n={n}: 真实迭代 {hits} ≤ 推出的轮数 n(hay)+1 = {n + 1}",
              f"hits={hits}")
        # 真实内层轮数 = hits × n(needle)，也要 ≤ κ 里的那一项
        check(iters <= (n + 1) * (len(needle) + 1),
              f"n={n}: 全部循环迭代 {iters} ≤ (n(hay)+1)×(n(needle)+1) = "
              f"{(n + 1) * (len(needle) + 1)}", f"iters={iters}")

    # `from` 非 0、hay 里只有 9：真轮数是 n - c - from + 1，仍 ≤ n + 1
    hits, ops, _ = _run(mods, "f_off", [[9] * 40, [1, 2, 3], 5])
    kv = kappa_at(k, {"hay": 40, "needle": 3})
    check(hits <= 40 + 1 and ops <= kv,
          f"from=5：hits={hits} ≤ n+1=41，ops {ops} ≤ κ {kv:g}",
          f"hits={hits} ops={ops} κ={kv}")


# ---------------------------------------------------------------- [6] 等价性 B
def test_offset_equivalence():
    print("[6] 等价性：`i + C <= N` vs `N >= i + C` —— κ 与 run 逐字节相同")
    a = _mods(TU_ALL, CH_ALL, "off_eq")
    _tu, _ch, ci = a
    t1 = ci.cost_of("f_off").render()
    t2 = ci.cost_of("f_off_alt").render()
    check(t1 == t2, "两种写法的 κ **逐字节相同**", f"{t1!r}\n         {t2!r}")
    hay = [3, 1, 4, 1, 5, 9, 2, 6]
    needle = [1, 5]
    o1, _, _ = _run(a, "f_off", [hay, needle, 0])
    o2, _, _ = _run(a, "f_off_alt", [hay, needle, 0])
    check(o1 == o2, "两种写法的 run 输出逐字节相同", f"{o1!r} vs {o2!r}")


# ---------------------------------------------------------------- [7] 偏移前提
def test_offset_premises():
    print("[7] 前提不成立 ⇒ ⊤：i64 初值 / 变量偏移 / 计数器减小")
    mods = _mods(TU_TOP, CH_TOP, "off_top")
    _tu, _ch, ci = mods
    cases = [
        ("f_off_i64", "计数器声明成 `i64`：初值可能为负 ⇒ 轮数可以超过 n"),
        ("f_off_var", "偏移量是变量 `k`（可能为负）⇒「轮数 ≤ n」会偏小"),
        ("f_off_dec", "计数器每轮**减小**（`i = i - 1`）⇒ 轮数无界"),
        ("f_off_shadow",
         "内层 `u64 i` 遮蔽外层 `i64 i`：只有**每一处**声明都非负才能收"),
    ]
    for fn, why in cases:
        k = ci.cost_of(fn)
        check(k.is_top(), f"{fn}: {why} ⇒ ⊤", k.render())


# ---------------------------------------------------------------- [8] check
def test_check_offset_targets():
    print("[8] `check` 接受偏移上界的 target（`O(hay.size()*needle.size())`）")
    d = tempfile.mkdtemp(prefix="elnoff-")
    try:
        tu_p = os.path.join(d, "m.tu")
        ch_p = os.path.join(d, "m.eln")
        io.open(tu_p, "w", encoding="utf-8", newline="\n").write(_TU_OFF)
        io.open(ch_p, "w", encoding="utf-8", newline="\n").write(_CH_OFF)
        p = subprocess.run([sys.executable, "-B",
                            os.path.join(SRC, "elench.py"), "check", tu_p, ch_p],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=ROOT)
        out = (p.stdout or "") + (p.stderr or "")
        check(p.returncode == 0, "check 退出码 0", out[-300:])
        for code in ("E-Target-BadForm", "E-While-NoBound", "E-Spec-SigMismatch"):
            check(code not in out, f"check 不报 `{code}`", out[-300:])
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- [9] cost
def test_cost_elem_target():
    print("[9] `cost` 用 `L(g)` 判达标（`check` 的形状白名单还没放行 L——见文件头）")
    d = tempfile.mkdtemp(prefix="elnelem-")
    try:
        tu_p = os.path.join(d, "m.tu")
        ch_p = os.path.join(d, "m.eln")
        elem_tu = ("spec f_elem(const vector<vector<u64>>& g) -> u64 {\n"
                   "    target { time: { max: O(g.size() * L(g)) }, "
                   "allocation: { max: 0 }, };\n}\n")
        elem_ch = ("u64 f_elem(const vector<vector<u64>>& g) {\n"
                   "    u64 sum = 0;\n"
                   "    u64 v = 0;\n"
                   "    while (v < g.size()) {\n"
                   "        u64 k = 0;\n"
                   "        while (k < g[v].size()) {\n"
                   "            sum = sum + g[v][k];\n"
                   "            k = k + 1;\n"
                   "        }\n"
                   "        v = v + 1;\n"
                   "    }\n"
                   "    return sum;\n"
                   "}\n")
        io.open(tu_p, "w", encoding="utf-8", newline="\n").write(elem_tu)
        io.open(ch_p, "w", encoding="utf-8", newline="\n").write(elem_ch)
        p = subprocess.run([sys.executable, "-B",
                            os.path.join(SRC, "elench.py"), "cost", tu_p, ch_p],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=ROOT)
        out = (p.stdout or "") + (p.stderr or "")
        check(p.returncode == 0, "cost 退出码 0", out[-300:])
        check("达成" in out, "`O(g.size() * L(g))` 判达成", out[-300:])
        check("target 解析失败" not in out, "`L(g)` 不被当成认不出的 target", out[-300:])
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    test_elem_basic()
    test_ng_is_not_a_bound()
    test_elem_equivalence()
    test_elem_premises()
    test_offset_basic()
    test_offset_equivalence()
    test_offset_premises()
    test_check_offset_targets()
    test_cost_elem_target()

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
