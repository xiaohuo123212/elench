#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_linear_rec.py —— **线性递归**在声明深度后算出 κ（P0-R，2026-11-12）

## 这份测试钉什么

`recursion depth <= N`（C11-b）以前只进 `E-Recursion-Unbounded`，**不进 κ**：
递归函数一律 `κ = ⊤`（内联成环就放弃）。修后：

| 形状 | 判据 | κ |
|---|---|---|
| 每层至多 1 个自调用（链：fact / sum_list / count_down / 早退二分） | P ≤ 1 | `D × 单层 + 叶子层` |
| 一条路径 **2** 个自调用、且两次拿**互不相交的两半**（`merge_sort`） | **区间对半分** | `(hi - lo) + 1` 当激活数（见 `test_kappa_halving.py`） |
| 一条路径 ≥2 个自调用、两次拿**同一组输入**（`fib(n-1)+fib(n-2)`） | P ≥ 2 | **⊤**（指数不许记成线性） |
| 自调用在**轮数上界推不出 ≤ 1** 的循环里（`place` 的 `for col`） | P > 1 | **⊤** |
| 自调用在**轮数上界 ≤ 1** 的循环里（`while (k < 1)`） | P = 1 | 有限（本文件 [2]） |

## `D × 单层 + 叶子层` 的两项

- **单层** = `_rec_once()`：函数体算一次，自调用点切成 `1 + Σ实参`。
  那个 `1 + Σ实参` **必须留**——解释器每层真的付调用 + 实参求值，记 0 会破界。
- **叶子层** = `_rec_leaf()`：函数体里自调用点**整条切 0**，剩下路径的成本上界。
  最后一层不再调用，但它走的分支（`return xs.length()` 之类）要钱。
  修前用 `1 + Σ实参` 顶这一项：叶子分支更重时就**小于**真实成本（破界方向）。
  现在写 `D × 单层 + max(1 + Σ实参, 叶子层)`，两项都是上界，取大者。

## 两类断言（只比表达式形状不算验证）

1. **等价性**：同一逻辑三种写法（`n == 0` / `0 == n` / `n <= 0`）的 κ **逐字节
   相同**，解释器实跑的返回值与 ops 也逐字节相同。
2. **实测 ≤ 上界**：每个递归函数都在**不同 D** 下真跑一遍解释器，断
   `ops ≤ κ`；并断 `κ(D+1) − κ(D) == 单层`（证明 D 确实是乘上去的）。

跑法：

    cd tests/cases; $env:PYTHONIOENCODING="utf-8"; python test_kappa_linear_rec.py
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

import io                                               # noqa: E402
import os                                               # noqa: E402
import subprocess                                       # noqa: E402
import sys                                              # noqa: E402
import tempfile                                         # noqa: E402

from cost_check import evaluate as kappa_at             # noqa: E402
from cost_interp import CostInterp                      # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset  # noqa: E402
from parser import parse_source                         # noqa: E402

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


def load(eln: str, tu: str = ""):
    ch = parse_source(eln, "lin.eln").module
    decl = parse_source(tu, "lin.tu").module if tu else None
    return ch, decl


def ci_of(eln: str, tu: str = "") -> CostInterp:
    ch, decl = load(eln, tu)
    return CostInterp(ch, decl)


def kappa(eln: str, tu: str, fn: str, env: dict):
    """`(Cost, 数值)`：含符号项/⊤ 时数值是 inf。"""
    k = ci_of(eln, tu).cost_of(fn)
    return k, kappa_at(k, dict(env))


def ops_of_call(fn: str, args, eln: str, tu: str = "") -> int:
    ch, decl = load(eln, tu)
    ops_enable(True)
    try:
        ops_reset()
        Interp(ch, decl).call(fn, args)
        return ops_of()
    finally:
        ops_enable(False)


def call_value(fn: str, args, eln: str, tu: str = ""):
    ch, decl = load(eln, tu)
    return Interp(ch, decl).call(fn, args)


def xs(n: int = 16):
    return [i for i in range(n)]


# ---------------------------------------------------------------- 语料

#: 三个**每层至多 1 次自调用**的探针（题面点名的形状）。
LINEAR_ELN = """
u64 fact(u64 n) {
    if (n <= 1) { return 1; }
    return n * fact(n - 1);
}

u64 sum_list(const vector<u64>& xs, u64 i) {
    if (i >= xs.length()) { return 0; }
    return xs[i] + sum_list(xs, i + 1);
}

u64 count_down(u64 n) {
    if (n == 0) { return 0; }
    return 1 + count_down(n - 1);
}

u64 call_fact(u64 n) { return fact(n); }
u64 call_sum(const vector<u64>& xs) { return sum_list(xs, 0); }
u64 call_cd(u64 n) { return count_down(n); }
"""


def tu_linear(d: int = 16) -> str:
    return """
spec fact(u64 n) -> u64 {
    effects {};
    recursion depth <= %d;
    target { time: { max: O(n) }, allocation: { max: 0 }, };
}
spec sum_list(const vector<u64>& xs, u64 i) -> u64 {
    effects {};
    recursion depth <= %d;
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec count_down(u64 n) -> u64 {
    effects {};
    recursion depth <= %d;
    target { time: { max: O(n) }, allocation: { max: 0 }, };
}
spec call_fact(u64 n) -> u64 { effects {}; target { time: { max: O(n) }, allocation: { max: 0 }, }; }
spec call_sum(const vector<u64>& xs) -> u64 { effects {}; target { time: { max: O(xs.length()) }, allocation: { max: 0 }, }; }
spec call_cd(u64 n) -> u64 { effects {}; target { time: { max: O(n) }, allocation: { max: 0 }, }; }
""" % (d, d, d)


def test_linear_probes():
    print("[1] 线性递归：fact / sum_list / count_down 都算出具体界，且实测 ≤ κ")
    tu = tu_linear()
    for fn, env in (("fact", {"n": 8}),
                    ("sum_list", {"xs": 16, "i": 0}),
                    ("count_down", {"n": 8})):
        k, kv = kappa(LINEAR_ELN, tu, fn, env)
        check(not k.is_top(), f"{fn}：κ 不是 ⊤（声明 D=16）", k.render())
        check("?" not in set(k.variables()), f"{fn}：κ 里没有 n(?)", k.render())
        check(kv < float("inf"), f"{fn}：κ({env}) 是有限值", str(kv))
        # **不同深度各测一遍**：ops(d) ≤ κ(d)（κ 也按同一档尺寸赋值）。
        # `sum_list` 的深度是 `列表长度 - 起点`，所以这一档变长度、起点固定 0。
        for d in (1, 2, 4, 8):
            if fn == "sum_list":
                env_d = {"xs": d, "i": 0}
                od = ops_of_call(fn, [xs(d), 0], LINEAR_ELN, tu)
            else:
                env_d = {"n": d}
                od = ops_of_call(fn, [d], LINEAR_ELN, tu)
            kd, kdv = kappa(LINEAR_ELN, tu, fn, env_d)
            check(od <= kdv, f"{fn} d={d}：ops {od} ≤ κ {kdv:g}")
    # 调用方也覆盖（一次调用压 16 层）
    for fn, env, arg in (("call_fact", {"n": 8}, [8]),
                         ("call_sum", {"xs": 16}, [xs(16)]),
                         ("call_cd", {"n": 8}, [8])):
        k, kv = kappa(LINEAR_ELN, tu, fn, env)
        o = ops_of_call(fn, arg, LINEAR_ELN, tu)
        check(not k.is_top() and o <= kv, f"{fn}：ops {o} ≤ κ {kv:g}")
    print()


#: **无 target** 的 spec：κ 的每个常数项都能直接读出来（D 的乘数关系要看纯式）。
SCALE_ELN = """
u64 scale(u64 n) {
    if (n == 0) { return 0; }
    return scale(n - 1);
}
"""


def tu_scale(d: int) -> str:
    return """
spec scale(u64 n) -> u64 {
    effects {};
    recursion depth <= %d;
}
""" % d


def test_depth_scaling():
    print("[2] `D × 单层 + 叶子层`：κ 随 D **线性**增长，增量就是单层")
    seq = []
    once_v = leaf_v = None
    for d in (1, 2, 4, 8):
        ci = ci_of(SCALE_ELN, tu_scale(d))
        k = ci.cost_of("scale")
        kv = kappa_at(k, {"n": 0})
        once_v = kappa_at(ci._rec_once("scale"), {"n": 0})
        leaf_v = kappa_at(ci._rec_leaf("scale"), {"n": 0})
        seq.append((d, kv))
        o = ops_of_call("scale", [d], SCALE_ELN, tu_scale(d))
        check(o <= kv, f"D={d}: ops {o} ≤ κ {kv:g}")
        # 叶子层真的被加上去了（不是被 1+Σ实参 顶掉）
        check(kv >= d * once_v + leaf_v,
              f"D={d}: κ {kv:g} ≥ D×单层 {d * once_v:g} + 叶子层 {leaf_v:g}")
    diffs = [seq[i + 1][1] - seq[i][1] for i in range(len(seq) - 1)]
    # D 从 1→2 是 +1 层，2→4 是 +2 层，4→8 是 +4 层
    check(diffs == [once_v, 2 * once_v, 4 * once_v],
          f"κ(D) 的增量 = 层数 × 单层 {once_v:g}：{diffs}", str(diffs))
    check(all(a[1] < b[1] for a, b in zip(seq, seq[1:])),
          f"声明深度变大 ⇒ κ 严格变大：{seq}", str(seq))
    print()


# ---------------------------------------------------------------- 循环乘数

#: 自调用在**轮数上界 1** 的循环里：P = 1 ⇒ 线性。
LOOP1_ELN = """
u64 g1(u64 n) {
    if (n == 0) { return 0; }
    u64 s = 0;
    for (u64 i = 0; i < 1; i = i + 1) { s = s + g1(n - 1); }
    return s;
}
"""

#: `while (k < 1)`：同一条判据，只是写法不同。
WHILE1_ELN = """
u64 w1(u64 n) {
    if (n == 0) { return 0; }
    u64 s = 0;
    u64 k = 0;
    while (k < 1) { s = s + w1(n - 1); k = k + 1; }
    return s;
}
"""

#: 轮数上界 2 ⇒ P = 2 ⇒ **仍是 ⊤**（每层可能两次自调用）。
LOOP2_ELN = """
u64 w2(u64 n) {
    if (n == 0) { return 0; }
    u64 s = 0;
    u64 k = 0;
    while (k < 2) { s = s + w2(n - 1); k = k + 1; }
    return s;
}
"""

#: 轮数是符号量（`for (auto& x : xs)` = `n(xs)`）⇒ P 推不出 ≤1 ⇒ ⊤。
FORXS_ELN = """
u64 fs(const vector<u64>& xs, u64 n) {
    if (n == 0) { return 0; }
    u64 s = 0;
    for (auto& x : xs) { s = s + fs(xs, n - 1); }
    return s;
}
"""

#: 嵌套两层**轮数 1** 的循环：乘积仍是 1 ⇒ 线性。
NEST1_ELN = """
u64 ns1(u64 n) {
    if (n == 0) { return 0; }
    u64 s = 0;
    u64 a = 0;
    while (a < 1) {
        u64 b = 0;
        while (b < 1) { s = s + ns1(n - 1); b = b + 1; }
        a = a + 1;
    }
    return s;
}
"""


def tu_u64(fn: str, d: int = 8, target: str = "O(n)") -> str:
    return """
spec %s(u64 n) -> u64 {
    effects {};
    recursion depth <= %d;
    target { time: { max: %s }, allocation: { max: 0 }, };
}
""" % (fn, d, target)


def tu_fs(d: int = 8) -> str:
    return """
spec fs(const vector<u64>& xs, u64 n) -> u64 {
    effects {};
    recursion depth <= %d;
    target { time: { max: O(n) }, allocation: { max: 0 }, };
}
""" % d


def test_loop_multiplier():
    print("[2-b] 循环乘数：乘积 ≤ 1 ⇒ 线性；≥2 / 符号 ⇒ 仍 ⊤")
    # 轮数 1：两种写法（三段式 for desugar 成 while）都必须算出有限 κ
    for name, eln in (("g1", LOOP1_ELN), ("w1", WHILE1_ELN)):
        ci = ci_of(eln, tu_u64(name))
        k = ci.cost_of(name)
        kv = kappa_at(k, {"n": 8})
        o = ops_of_call(name, [8], eln, tu_u64(name))
        check(not k.is_top(), f"{name}（循环轮数 1）：κ 不是 ⊤", k.render())
        check(o <= kv, f"{name}：ops {o} ≤ κ {kv:g}")
        check(ci._rec_linear(name), f"{name}：判成线性（_rec_linear）")
    # 轮数 2：P = 2 ⇒ 树递归
    ci = ci_of(LOOP2_ELN, tu_u64("w2"))
    k = ci.cost_of("w2")
    why = ci.top_reasons.get("w2", "")
    check(k.is_top(), "w2（循环轮数 2）⇒ ⊤", k.render())
    check("是树递归" in why and "循环" in why,
          "w2：原因写「循环里每层可能跑多次」", why)
    # 符号轮数：`for (auto& x : xs)` ⇒ `n(xs)`，推不出 ≤1 ⇒ ⊤
    ci = ci_of(FORXS_ELN, tu_fs())
    k = ci.cost_of("fs")
    check(k.is_top(), "fs（for x : xs，轮数 n(xs)）⇒ ⊤", k.render())
    check(ci._rec_tree_like("fs"), "fs：判成树递归（_rec_tree_like）")
    # 嵌套两层轮数 1：乘积 1 ⇒ 线性
    ci = ci_of(NEST1_ELN, tu_u64("ns1"))
    k = ci.cost_of("ns1")
    kv = kappa_at(k, {"n": 8})
    o = ops_of_call("ns1", [8], NEST1_ELN, tu_u64("ns1"))
    check(not k.is_top(), "ns1（两层轮数 1 的嵌套循环）：κ 不是 ⊤", k.render())
    check(o <= kv, f"ns1：ops {o} ≤ κ {kv:g}")
    print()


# ---------------------------------------------------------------- 树递归

TREE_ELN = """
u64 fib(u64 n) {
    if (n < 2) { return n; }
    return fib(n - 1) + fib(n - 2);
}
"""

MS_ELN = """
void merge_sort(vector<u64>& xs, u64 lo, u64 hi) {
    if (hi - lo < 2) { return; }
    u64 mid = lo + (hi - lo) / 2;
    merge_sort(xs, lo, mid);
    merge_sort(xs, mid, hi);
}
"""

PLACE_ELN = """
u64 place(u64 row, u64 n) {
    if (row == n) { return 1; }
    u64 s = 0;
    for (u64 col = 0; col < n; col = col + 1) {
        s = s + place(row + 1, n);
    }
    return s;
}
"""


def tu_generic(fn: str, params: str, d: int = 8, target: str = "O(n)") -> str:
    return """
spec %s(%s) -> u64 {
    effects {};
    recursion depth <= %d;
    target { time: { max: %s }, allocation: { max: 0 }, };
}
""" % (fn, params, d, target)


def test_tree_still_top():
    print("[3] 树递归：**不认识的形状仍 ⊤**；认识的三种（两半 / visited / ④ 幂界）给具体式")
    # 一条路径 2 个自调用点：朴素 fib
    ci = ci_of(TREE_ELN, tu_generic("fib", "u64 n"))
    k = ci.cost_of("fib")
    check(k.is_top(), "fib（两个自调用点）⇒ ⊤", k.render())
    why = ci.top_reasons.get("fib", "")
    check("是树递归" in why and "2 个自调用点" in why,
          "fib：原因写「一条执行路径上有 2 个自调用点」", why)
    # **归并排序不再是 ⊤**（2026-11-12）：它一条路径上虽然也有 2 个自调用点，
    # 但两次拿的是 `[lo, mid)` 与 `[mid, hi)`——**互不相交、各 ≤ 一半**，
    # 所以激活数有可证上界 `2 × (hi - lo)`。判据与实测在
    # `test_kappa_halving.py` 里钉（[1]~[5]）；这里只留"它不该是 ⊤"这一条，
    # 免得两处各写一份数字。
    ci = ci_of(MS_ELN, """
spec merge_sort(vector<u64>& xs, u64 lo, u64 hi) -> () {
    effects {};
    recursion depth <= 32;
}
""")
    k = ci.cost_of("merge_sort")
    check(not k.is_top() and "hi - lo" in k.render(),
          "merge_sort（区间对半分）⇒ 具体式，含激活数上界 hi - lo", k.render())
    check("merge_sort" not in ci.top_reasons,
          "merge_sort：top_reasons 里没有它（不是 ⊤ 就没有原因行）",
          ci.top_reasons.get("merge_sort", ""))
    # 自调用在 for col 里：place —— **2026-11-12 夜起由判据 ④ 覆盖**
    # （`_tree_activation_bound`：激活数 ≤ `(D+1)·B^D`），不再是 ⊤。
    # 这里只钉"它仍然是树递归、不是链"这一条；④ 的判据 / 幂界 / 解释器实测在
    # `tests/cases/test_kappa_tree_depth.py`，不在这里重复。
    ci = ci_of(PLACE_ELN, tu_generic("place", "u64 row, u64 n"))
    k = ci.cost_of("place")
    check(not k.is_top(), "place（自调用在 for col 里）⇒ 具体式（④ 的幂界）",
          k.render())
    check("9×n^8" in k.render(), "place：乘数是 `(D+1)·B^D = 9×n^8`", k.render())
    check("place" not in ci.top_reasons,
          "place：top_reasons 里没有它（不再 ⊤）", str(ci.top_reasons)[:200])
    check(ci._rec_tree_like("place") and not ci._rec_linear("place"),
          "place：_rec_tree_like=True / _rec_linear=False（仍是树递归，不是链）")
    print()


# ---------------------------------------------------------------- 叶子层

LEAF_ELN = """
u64 lh(const vector<u64>& xs, u64 n) {
    if (n == 0) { return xs.length() + xs.length() + xs.length(); }
    return lh(xs, n - 1);
}
"""


def tu_lh(d: int) -> str:
    return """
spec lh(const vector<u64>& xs, u64 n) -> u64 {
    effects {};
    recursion depth <= %d;
    target { time: { max: O(n) }, allocation: { max: 0 }, };
}
""" % d


def test_leaf_term():
    print("[4] 叶子层：最后一层不再调用，但它走的分支也要钱")
    seq = []
    for d in (1, 2, 4, 8):
        ci = ci_of(LEAF_ELN, tu_lh(d))
        k = ci.cost_of("lh")
        kv = kappa_at(k, {"n": d, "xs": 16})
        once_v = kappa_at(ci._rec_once("lh"), {"n": 0, "xs": 16})
        leaf_v = kappa_at(ci._rec_leaf("lh"), {"n": 0, "xs": 16})
        o = ops_of_call("lh", [xs(16), d], LEAF_ELN, tu_lh(d))
        seq.append((d, kv, once_v))
        check(o <= kv, f"D={d}: ops {o} ≤ κ {kv:g}")
        check(kv >= d * once_v + leaf_v,
              f"D={d}: κ {kv:g} ≥ D×单层 {d * once_v:g} + 叶子层 {leaf_v:g}")
    # 叶子比「每层调用开销」重：修前那个 `1+Σ实参` 顶不住（这就是补 leaf 的理由）
    c_v = 1 + 4      # 1（调用）+ Σ实参（`xs` 1 + `n-1` 3）
    check(kappa_at(ci_of(LEAF_ELN, tu_lh(8))._rec_leaf("lh"),
                   {"n": 0, "xs": 16}) > c_v,
          "这个探针的叶子层 > 每层调用开销（旧式 `1+Σ实参` 会盖不住）")
    diffs = [seq[i + 1][1] - seq[i][1] for i in range(len(seq) - 1)]
    check(diffs == [seq[0][2], 2 * seq[0][2], 4 * seq[0][2]],
          f"κ(D) 的增量仍 = 层数 × 单层：{diffs}", str(diffs))
    print()


# ---------------------------------------------------------------- 等价性

#: 同一逻辑三种写法（守卫等价 + 操作数交换）：κ / 返回值 / ops 都必须逐字节相同。
EQ_TMPL = """
u64 {name}(u64 n) {{
    if ({guard}) {{ return 0; }}
    return 1 + {name}(n - 1);
}}
"""


def tu_eq(name: str) -> str:
    return """
spec %s(u64 n) -> u64 {
    effects {};
    recursion depth <= 8;
    target { time: { max: O(n) }, allocation: { max: 0 }, };
}
""" % name


def test_equivalence():
    print("[5] 等价性：同一逻辑的三种写法，κ / 返回值 / ops 逐字节相同")
    variants = (("qa", "n == 0"), ("qb", "0 == n"), ("qc", "n <= 0"))
    renders = []
    values = []
    opss = []
    for name, guard in variants:
        eln = EQ_TMPL.format(name=name, guard=guard)
        k, kv = kappa(eln, tu_eq(name), name, {"n": 8})
        renders.append(k.render())
        values.append(call_value(name, [8], eln, tu_eq(name)))
        opss.append(ops_of_call(name, [8], eln, tu_eq(name)))
        check(not k.is_top(), f"{name}（{guard}）：κ 有限", k.render())
    check(renders[0] == renders[1] == renders[2],
          "三种写法的 κ **逐字节相同**", f"{renders}")
    check(values[0] == values[1] == values[2],
          "三种写法的返回值相同", str(values))
    check(opss[0] == opss[1] == opss[2],
          "三种写法的解释器 ops 相同", str(opss))
    print()


# ---------------------------------------------------------------- CLI

def run_cli(argv, cwd):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = SRC
    p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "src", "dsh.py")] + argv,
                       cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def test_cli():
    print("[6] `dsh cost --explain`：具体式 + target 能判；树递归打印 ⊤")
    tmp = tempfile.mkdtemp(prefix="linrec_")
    try:
        tu_p = os.path.join(tmp, "lin.tu")
        eln_p = os.path.join(tmp, "lin.eln")
        for path, text in ((tu_p, tu_linear()), (eln_p, LINEAR_ELN)):
            with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        rc, out = run_cli(["cost", tu_p, eln_p, "--explain"], tmp)
        check(rc == 0, "线性递归探针：cost 退出码 0", str(rc))
        for fn in ("fact", "sum_list", "count_down"):
            check(f"✓ {fn}" in out, f"target 判为达成：`✓ {fn}`")
        check("κ ≤ ⊤" not in out, "线性递归探针里没有 ⊤")
        # 树递归：place 形状 —— **2026-11-12 夜起**由判据 ④ 给幂界
        # `(D+1)·B^D`，不再是 `κ ≤ ⊤`。
        tu2 = os.path.join(tmp, "place.tu")
        eln2 = os.path.join(tmp, "place.eln")
        for path, text in ((tu2, tu_generic("place", "u64 row, u64 n")),
                           (eln2, PLACE_ELN)):
            with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        rc, out = run_cli(["cost", tu2, eln2, "--explain"], tmp)
        line = next((ln for ln in out.splitlines() if "place" in ln and "κ ≤" in ln), "")
        check("9×n^8" in line, "树递归 place：`κ ≤ 9×n^8×…`（④ 的幂界）", line)
        check("⊤" not in line, "树递归 place：不再是 `κ ≤ ⊤`", line)
    finally:
        for name in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass
    print()


def main() -> int:
    test_linear_probes()
    test_depth_scaling()
    test_loop_multiplier()
    test_tree_still_top()
    test_leaf_term()
    test_equivalence()
    test_cli()

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
