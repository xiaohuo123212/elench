#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_gaps.py —— 五类 κ 缺口的**微例钉子**（①递归 ②内建 ③min/max ④函数值 ⑤括号）

## 这份测试钉什么

四类「κ 比实测少」的缺口，每类一个**最小复现**。修前数字是用
`src/cost_interp.py.bak`（本轮改动前的副本）实测的，修后在同一份样例上重测：

| # | 微例（n=16） | 实测 ops | 修前 κ | 修后 κ | 修前缺口 |
|---|---|---:|---:|---:|---:|
| ① | `down(xs, 8)`（无深度声明） | 104 | 33 | 33 | **+71**（未声明 → 修前行为） |
| ① | `down(xs, 8)`（`recursion depth <= 8`） | 104 | 33 | **160** | 0 |
| ① | `main8(xs)` 调 `down(xs, 8)` | 108 | 20 | **164** | 0 |
| ② | `print("x")` | 6 | 5 | **6** | 0 |
| ② | `map(xs, visit)` | 36 | 66 | 67 | 0（尺寸项本来就在） |
| ② | `filter(xs, keep)` | 68 | 98 | 99 | 0 |
| ② | `fold(xs, 0, addv)` | 69 | 98 | 100 | 0 |
| ② | `count_if(xs, keep)` | 68 | 98 | 99 | 0 |
| ② | `sort(xs)` | 4 | 65 | 68 | 0 |
| ② | `sort_by(xs, cmpv)`（比较器无 target） | 124 | 257 | 260 | 0 |
| ② | `for_each(m, visit2)` | 15 | inf | inf | 符号项（`k(visit2)`） |
| ③ | `max(b, 1)` / `min(b, 1)` | 5 | 4 | **5** | 0 |
| ④ | `host_u(xs)`（`g = countf` 静态可求） | 10 | 6 | **22** | 0 |
| ④ | `host_v(xs)`（`g = idf` 静态可求） | 8 | 6 | **8** | 0 |
| ④ | `host_vn(xs)`（`g(idf(xs))`） | 11 | 6 | **11** | 0 |
| ④ | `apply_u(xs, g)`（调用点不知道 g） | — | 3 | inf + `k(g)` | 符号项 |
| ⑤ | `rotr(x,n)` / `ch` / `maj` / `Σ0` / `σ0`（叶子单独测） | 13/11/15/51/39 | 10/9/12/42/32 | **13/11/15/51/39** | 0 |
| ⑤ | `(x)` / `((x))` / `(((x)))` | 3/4/5 | 2/2/2 | **3/4/5** | 0 |
| ⑤ | `just_call(x)`（`return rotr(x, 2);`） | 17 | 14 | **17** | 0 |

**判据**：每个微例都断 `ops ≤ κ`（符号项那两条断 `κ` 里出现 `k(...)`、数值给
`inf`——`inf ≥ ops` 成立，但**不作为数值证据**）。「修前 κ」同时用来断
**κ 只增不减**：修后的 κ 必须 ≥ 上表左列的旧值。

## 为什么 ② 里有些微例修前就成立

`map/filter/fold/count_if` 的 `n × κ(f)` 尺寸项本来就在，缺的只是**实参表达式**
（`xs` 求值那 1 步）；所以修前缺口是负的（κ 有余量），修后仍然负——这不是
「没修」，是**尺寸项把实参那 1 步盖住了**。真正被实参卡住的是 `print`（κ 5、
ops 6）和 `sort_by`（比较器很小的时候）。③ 的 `min/max` 差的是**内建派发**
那 1 步，不是实参。

## ④ 的两条支路

- **静态可求**：`host_u(xs)` 里 `apply_u(xs, countf)`，实参是具名函数 ⇒
  被调体 `countf` 的 κ 计入调用方 ⇒ 缺口 **0**（修前缺口恰好 = κc）。
- **符号**：直接分析 `apply_u(xs, g)` 本身 ⇒ κ 里是 `k(g)`（契约项），
  `cost_check.evaluate` 给 `inf`。`tools/kappa_vs_ops.py` 必须**打印 inf 并
  跳过该点**，不许在 `int(inf)` 上崩——本文件最后一段用临时目录真跑一遍工具。

跑法：

    cd src; $env:PYTHONIOENCODING="utf-8"; python test_kappa_gaps.py
"""

import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但**这批测试原先住在 `src/`**，里面大量
#: `os.path.join(HERE, "..", "文档")` / `os.path.join(ROOT, "tools")` 都假设
#: 「本文件在 src/、上一级是仓库根」。搬家时改 200 处路径比让 `HERE` 继续指 src 贵得多，
#: 所以保留旧语义：`HERE` = src/，`CASEDIR` = 本文件真正所在的目录。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import io
import math
import os
import sys
import tempfile

# **src 优先**：`tools/` 里也有 `.py`，别让它遮住 `interp` / `parser`
sys.path.insert(0, os.path.join(ROOT, "tools"))

from cost_check import evaluate as kappa_at            # noqa: E402
from cost_interp import CostInterp                     # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset   # noqa: E402
from parser import parse_source                        # noqa: E402
from symbolic import Cost                              # noqa: E402

import kappa_vs_ops as kvo                             # noqa: E402

_passed = 0
_failed = []

#: 修前 κ（`cost_interp.py.bak` 实测）。用来断「κ 只增不减」。
PRE = {
    "down8": 33.0, "main8": 20.0,
    "print": 5.0, "map": 66.0, "filter": 98.0, "fold": 98.0,
    "count_if": 98.0, "sort": 65.0, "sort_by": 257.0,
    "max": 4.0, "min": 4.0,
    "host_u": 6.0, "host_v": 6.0, "host_vn": 6.0,
}


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def load(eln: str, tu: str = ""):
    ch = parse_source(eln, "gaps.eln").module
    decl = parse_source(tu, "gaps.tu").module if tu else None
    return ch, decl


def kappa(eln: str, tu: str, fn: str, env: dict) -> float:
    """κ(fn) 在给定尺寸赋值下的数值；含符号项时是 `inf`。"""
    ch, decl = load(eln, tu)
    k = CostInterp(ch, decl).cost_of(fn)
    return kappa_at(k, dict(env))


def kappa_cost(eln: str, tu: str, fn: str):
    ch, decl = load(eln, tu)
    return CostInterp(ch, decl).cost_of(fn)


def ops_of_call(fn: str, args, eln: str, tu: str = "") -> int:
    """整棵调用树的实测操作数。"""
    ch, decl = load(eln, tu)
    ops_enable(True)
    try:
        ops_reset()
        Interp(ch, decl).call(fn, args)
        return ops_of()
    finally:
        ops_enable(False)


def xs(n: int = 16):
    return [i for i in range(n)]


# ---------------------------------------------------------------- ① 递归深度
RECUR = """
u64 down(const vector<u64>& xs, u64 i) {
    if (i == 0) { return xs.length(); }
    return 1 + down(xs, i - 1);
}
u64 main8(const vector<u64>& xs) { return down(xs, 8); }
"""


def tu_rec(unbounded: bool = False) -> str:
    """`recursion depth <= N` 是**唯一**的深度声明（规格 C11-b）。"""
    return """
spec down(const vector<u64>& xs, u64 i) -> u64 {
    effects {};
""" + ("" if unbounded else "    recursion depth <= 8;\n") + """
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec main8(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""


def test_recursion_depth():
    print("[1] 递归：κ 按 `.tu` 的 `recursion depth <= N` 乘「函数体只算一次」")
    n = 16
    # ①-a 声明 D=8：`down(xs, 8)` 的 ops ≤ κ，且 κ 从 33 涨到 160
    k8 = kappa(RECUR, tu_rec(), "down", {"xs": n, "i": 8})
    o8 = ops_of_call("down", [xs(n), 8], RECUR, tu_rec())
    check(o8 == 104, f"ops(down, i=8) = 104（修前实测，作为基准）", str(o8))
    check(k8 >= PRE["down8"],
          f"κ(down, i=8) = {k8:g} ≥ 修前 {PRE['down8']:g}（只增不减）", str(k8))
    check(o8 <= k8, f"ops {o8} ≤ κ {k8:g}（修前 κ=33，缺口 71）")
    # **2026-11-12（P0-R）**：153 → 160。递归合并从「`1+Σ实参` + D×单层」改成
    # 「D×单层 + max(1+Σ实参, **叶子层**)」——叶子层 = 自调用整条切 0 的体成本
    # （这里 12）。修前那个 5 = `1 + Σ实参` 只是**碰巧**盖住叶子层（`down` 的
    # 叶子分支也是 5）；叶子更重的递归会小于真实成本（破界方向），所以补上。
    check(k8 == 160, f"κ(down, i=8) = 160 = 8 × 17（单层）+ max(5, 12)（尾部）", str(k8))

    # ①-b 深度**声明**变大 → κ 严格增长（同一份实现、同一档 n）
    seq = []
    for d in (1, 2, 4, 8, 16):
        kd = kappa(RECUR, tu_rec_for(d), "down", {"xs": n, "i": d})
        od = ops_of_call("down", [xs(n), d], RECUR, tu_rec_for(d))
        seq.append(kd)
        check(od <= kd, f"D={d:2d}：ops {od} ≤ κ {kd:g}")
    check(all(a < b for a, b in zip(seq, seq[1:])),
          f"声明深度 1→16：κ 严格增长 {seq}", str(seq))

    # ①-b' **运行期闸门让 κ 重新成为上界**（2026-11-10 / K3 的 ③）。
    #
    # 修前这条声明只进 κ：声明 8 照样能跑 75 层，κ(75)=117 而实测 ops=831
    # ——「上界」被越过。现在声明 8 跑到第 9 层就抛诊断，跑不到 κ 之外。
    # 边界不误伤：`down(xs, d)` + `recursion depth <= d`（d 帧 = d+1 层）
    # 正好在上界内，上面那一轮 (1,2,4,8,16) 全部照常通过。
    from interp import InterpError as _IErr
    try:
        _bad = ops_of_call("down", [xs(n), 20], RECUR, tu_rec())   # 声明 8、实跑 20 层
        check(False, "声明 8、实跑 down(xs,20) → 必须抛 E-Recursion-Depth-Exceeded",
              f"没抛，ops={_bad}")
    except _IErr as exc:
        check("E-Recursion-Depth-Exceeded" in str(exc),
              "声明 8、实跑 down(xs,20) → E-Recursion-Depth-Exceeded", str(exc))

    # ①-c 调用方也覆盖：`main8` 一次调用会压 8 层
    km = kappa(RECUR, tu_rec(), "main8", {"xs": n})
    om = ops_of_call("main8", [xs(n)], RECUR, tu_rec())
    check(km >= PRE["main8"],
          f"κ(main8) = {km:g} ≥ 修前 {PRE['main8']:g}（只增不减）", str(km))
    check(om <= km, f"ops(main8) {om} ≤ κ {km:g}（修前 κ=20，缺口 88）")

    # ①-d **没有声明**：保持修前那一层（不假装覆盖），这是「诚实的不覆盖」
    k0 = kappa(RECUR, tu_rec(unbounded=True), "down", {"xs": n, "i": 8})
    o0 = ops_of_call("down", [xs(n), 8], RECUR, tu_rec(unbounded=True))
    check(k0 == PRE["down8"],
          f"无 `recursion depth` 时 κ 保持修前 {PRE['down8']:g}（不猜深度）",
          str(k0))
    check(o0 > k0, f"（已知未覆盖）无声明时 ops {o0} > κ {k0:g} —— 见报告")
    print()


def tu_rec_for(d: int) -> str:
    return """
spec down(const vector<u64>& xs, u64 i) -> u64 {
    effects {};
    recursion depth <= %d;
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec main8(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
""" % d


# ---------------------------------------------------------------- ①-b 树递归
#
# **2026-11-09**：`_diff/fib_rec` 破界（κ=2,064 < 实测 31,926）。
# 根因：`recursion depth <= N` 的合并写成了 `N × 体一次`，这只对**一条链**成立；
# `fib(n-1) + fib(n-2)` 每层分成两支，真实是 Θ(2ⁿ)，κ 却记成了 O(n²)——
# 于是**指数函数被判「对 O(n²) 达成」**（假通过，不是"松"，是"破界"）。
# 修法：树递归 ⇒ ⊤（`Cost` 文法没有幂，也不许把指数写成线性）；
# 单链（`down`、早退二分）保持原来的线性合并。
TREE = """
u64 fib(u64 n) {
    if (n < 2) { return n; }
    return fib(n - 1) + fib(n - 2);
}
u64 main64(u64 n) { return fib(n); }
"""

TREE_TU = """
spec fib(u64 n) -> u64 { effects {}; recursion depth <= 8;
    target { time: { max: O(n * n) }, allocation: { max: 0 }, }; }
spec main64(u64 n) -> u64 { effects {}; recursion depth <= 8;
    target { time: { max: O(n * n) }, allocation: { max: 0 }, }; }
"""

#: 两个自调用点在**互斥分支**里（早退二分）：一条路径只走一个 ⇒ 仍是链。
BSEARCH = """
u64 bs(const vector<u64>& xs, u64 lo, u64 hi, u64 k) {
    if (lo >= hi) { return 0; }
    const u64 mid = lo + (hi - lo) / 2;
    if (xs[mid] == k) { return 1; }
    if (xs[mid] < k) { return bs(xs, mid + 1, hi, k); }
    return bs(xs, lo, mid, k);
}
"""

BSEARCH_TU = """
spec bs(const vector<u64>& xs, u64 lo, u64 hi, u64 k) -> u64 {
    effects {};
    recursion depth <= 8;
    target { time: { max: O(log(xs.length())) }, allocation: { max: 0 }, };
}
"""

#: **同一个调用点**在循环里：每层可能跑 L 次（L 静态不可知）⇒ 树规模非线性。
LOOPREC = """
u64 h(u64 n) {
    if (n < 2) { return n; }
    u64 s = 0;
    for (u64 i = 0; i < 2; i = i + 1) { s = s + h(n - 1); }
    return s;
}
"""

LOOPREC_TU = """
spec h(u64 n) -> u64 { effects {}; recursion depth <= 8;
    target { time: { max: O(n) }, allocation: { max: 0 }, }; }
"""


def test_tree_recursion():
    print("[1-b] 树递归：一条路径多个自调用 ⇒ ⊤（不许记成 D × 单次成本）")
    kf = kappa_cost(TREE, TREE_TU, "fib")
    check(kf.is_top(), "fib（两个自调用点）⇒ ⊤", kf.render())
    check(kappa_at(kf, {}) == float("inf"), "fib：evaluate 给 inf（⊤）",
          str(kappa_at(kf, {})))
    km = kappa_cost(TREE, TREE_TU, "main64")
    check(km.is_top(), "调用方 main64 也跟着 ⊤（拿不到整棵调用树上界）", km.render())
    kl = kappa_cost(LOOPREC, LOOPREC_TU, "h")
    check(kl.is_top(), "自调用在循环里（同一调用点每层多次）⇒ ⊤", kl.render())

    # 单链保持线性合并：`down(xs, 8)` 的 160 钉在 ①-a；这里补「早退二分」
    # ——两个自调用点，但**互斥**（一条路径只走一个）⇒ 仍是链，不许误判 ⊤。
    kb = kappa_cost(BSEARCH, BSEARCH_TU, "bs")
    check(not kb.is_top(),
          "早退二分（两个互斥分支各一个自调用）仍是链，不误判 ⊤", kb.render())
    n = 16
    ob = ops_of_call("bs", [xs(n), 0, n, 10 ** 9], BSEARCH, BSEARCH_TU)
    kvb = kappa_at(kb, {"xs": n, "lo": 0, "hi": n, "k": 10 ** 9})
    check(ob <= kvb, f"二分：ops {ob} ≤ κ {kvb:g}", f"κ={kvb:g}")
    print()


# ---------------------------------------------------------------- ② 内建实参
BUILTINS = """
u64 visit(u64 v) { return v; }
u64 visit2(const vector<u8>& k, u64 v) { return v; }
bool keep(u64 v) { return v > 0; }
u64 addv(u64 a, u64 b) { return a + b; }
bool cmpv(u64 a, u64 b) { return a < b; }
u64 p1() { print("x"); return 0; }
u64 p2() {
    HashMap<vector<u8>, u64> m = map_new();
    map_put(m, "a", 1);
    return for_each(m, visit2);
}
vector<u64> map1(const vector<u64>& xs) { return map(xs, visit); }
vector<u64> filt(const vector<u64>& xs) { return filter(xs, keep); }
u64 fold1(const vector<u64>& xs) { return fold(xs, 0, addv); }
u64 cnt(const vector<u64>& xs) { return count_if(xs, keep); }
vector<u64> srt(const vector<u64>& xs) { return sort(xs); }
vector<u64> srtb(const vector<u64>& xs) { return sort_by(xs, cmpv); }
"""

#: 比较器**不写 target**：`sort_by` 的 `n log n × κ(cmp)` 用**函数体** κ。
#: （若写 `target { max: O(1) }`，那个 1 是"至多常数倍"不是精确值，模型仍偏松——
#:  见报告「仍未覆盖」。）
BUILTINS_TU = ""


def test_builtin_args():
    print("[2] 内建分支：`print` / HOF / `for_each` / `sort(_by)` 的实参进 κ")
    n = 16
    cases = (
        ("print", "p1", [], 5.0, 6),
        ("map", "map1", [xs(n)], 66.0, 36),
        ("filter", "filt", [xs(n)], 98.0, 68),
        ("fold", "fold1", [xs(n)], 98.0, 69),
        ("count_if", "cnt", [xs(n)], 98.0, 68),
        ("sort", "srt", [xs(n)], 65.0, 4),
        ("sort_by", "srtb", [xs(n)], 257.0, 124),
    )
    for key, fn, args, pre, want_ops in cases:
        k = kappa(BUILTINS, BUILTINS_TU, fn, {"xs": n})
        o = ops_of_call(fn, args, BUILTINS, BUILTINS_TU)
        check(o == want_ops, f"{key}：ops = {want_ops}（基准）", str(o))
        check(k >= pre, f"{key}：κ {k:g} ≥ 修前 {pre:g}（只增不减）", str(k))
        check(o <= k, f"{key}：ops {o} ≤ κ {k:g}")
    # **真正被实参卡住的那一条**：`print("x")` 修前 κ=5 < ops=6
    kp = kappa(BUILTINS, BUILTINS_TU, "p1", {"xs": n})
    check(kp == 6 and PRE["print"] == 5,
          f"print：κ 5 → {kp:g}（补上实参那 1 步）", str(kp))
    # **符号项**：`for_each(m, visit2)` 的 k(visit2) 是契约项 ⇒ κ = inf。
    # `inf ≥ ops` 成立，但**不作为数值证据**——只钉「符号在、工具不崩」。
    kf = kappa(BUILTINS, BUILTINS_TU, "p2", {"m": 1})
    of = ops_of_call("p2", [], BUILTINS, BUILTINS_TU)
    check("k(visit2)" in kappa_cost(BUILTINS, BUILTINS_TU, "p2").render(),
          "for_each：κ 含 k(visit2)（契约项）",
          kappa_cost(BUILTINS, BUILTINS_TU, "p2").render())
    check(math.isinf(kf) and of <= kf,
          f"for_each：ops {of} ≤ κ inf（符号项，非数值证据）", str(kf))
    print()


# ---------------------------------------------------------------- ③ min/max
MINMAX = """
u64 mx(u64 b) { return max(b, 1); }
u64 mn(u64 b) { return min(b, 1); }
"""


def test_minmax_dispatch():
    print("[3] `min/max`：补上内建派发那 1 步（修前 ops 5 vs κ 4）")
    for fn, key in (("mx", "max"), ("mn", "min")):
        k = kappa(MINMAX, "", fn, {"b": 7})
        o = ops_of_call(fn, [7], MINMAX)
        check(o == 5, f"{key}：ops = 5（基准）", str(o))
        check(k >= PRE[key], f"{key}：κ {k:g} ≥ 修前 {PRE[key]:g}", str(k))
        check(o <= k, f"{key}：ops {o} ≤ κ {k:g}")
        check(o - k == 0, f"{key}：缺口 {o - k:g} = 0（修前 1）")
    print()


# ---------------------------------------------------------------- ④ 函数值
FNVAL = """
u64 countf(const vector<u64>& xs) { return xs.length(); }
vector<u64> idf(const vector<u64>& xs) { return vector<u64>::make(); }
u64 apply_u(const vector<u64>& xs, func(vector<u64>) -> u64 g) { return g(xs); }
vector<u64> apply_v(const vector<u64>& xs, func(vector<u64>) -> vector<u64> g) {
    return g(xs);
}
vector<u64> apply_vn(const vector<u64>& xs, func(vector<u64>) -> vector<u64> g) {
    return g(idf(xs));
}
u64 host_u(const vector<u64>& xs) { return apply_u(xs, countf); }
vector<u64> host_v(const vector<u64>& xs) { return apply_v(xs, idf); }
vector<u64> host_vn(const vector<u64>& xs) { return apply_vn(xs, idf); }
"""

FNVAL_TU = """
spec countf(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec apply_u(const vector<u64>& xs, func(vector<u64>) -> u64 g) -> u64 { effects {}; }
spec apply_v(const vector<u64>& xs, func(vector<u64>) -> vector<u64> g) -> vector<u64> { effects {}; }
"""

#: `for_each` 的 κ 是**符号**（`k(visit2)`）：工具必须打印 inf、跳过该点。
FGAP = """
u64 visit2(const vector<u8>& k, u64 v) { return v; }
u64 f(const vector<u64>& xs) {
    HashMap<vector<u8>, u64> m = map_new();
    map_put(m, "a", 1);
    return for_each(m, visit2);
}
"""

FGAP_TU = """
spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}
"""


def test_fnvalue():
    print("[4] 函数值调用：具名函数实参 → 被调体进 κ；否则 k(g) 符号项")
    n = 16
    # `host_u` 的被调体 `countf` **有 target**（`O(xs.length())`），所以 κ 用的是
    # target，比函数体松 ⇒ 缺口是负的（有余量）。`host_v` / `host_vn` 的被调体
    # 没有 target（走内联），κ 正好等于 ops —— 那两条断**缺口 0**。
    # **A-full（2026-11-09）**：`idf` 原先写 `return xs;`（`const&` 形参），值语义下
    # 那是**一次真实的容器拷贝**，κ 会**故意**大于解释器 ops（解释器不拷贝 ⇒ ops 是
    # 下界，设计 §10.1）。本测试要钉的是**函数值派发**的记账，所以 `idf` 改成返回
    # prvalue，让 κ 与 ops 仍可逐项对账。
    for fn, pre, want_ops, exact in (("host_u", 6.0, 10, False),
                                     ("host_v", 6.0, 8, True),
                                     ("host_vn", 6.0, 11, True)):
        k = kappa(FNVAL, FNVAL_TU, fn, {"xs": n})
        o = ops_of_call(fn, [xs(n)], FNVAL, FNVAL_TU)
        check(o == want_ops, f"{fn}：ops = {want_ops}（基准）", str(o))
        check(k >= pre, f"{fn}：κ {k:g} ≥ 修前 {pre:g}（只增不减）", str(k))
        check(o <= k, f"{fn}：ops {o} ≤ κ {k:g}")
        if exact:
            check(o - k == 0, f"{fn}：缺口 {o - k:g} = 0（修前 = κ(被调体)）")
        else:
            check(o - k <= 0,
                  f"{fn}：缺口 {o - k:g} ≤ 0（被调体有 target，κ 偏松）")

    # 调用点**不知道** g 绑到谁：κ 里必须有 `k(g)`，数值是 inf（不是 0）
    ka = kappa_cost(FNVAL, FNVAL_TU, "apply_u")
    check("k(g)" in ka.render(),
          f"apply_u：κ 含符号项 k(g)（修前是 3，**低估**）", ka.render())
    check(math.isinf(kappa_at(ka, {})),
          "apply_u：evaluate 给 inf（不是数值）", str(kappa_at(ka, {})))
    check(kappa_at(Cost("sym", "k(g)"), {}) == float("inf"),
          "Cost(sym)：evaluate 给 inf（符号项的统一约定）")

    # 工具：**同一份符号 κ** 必须在 kappa_vs_ops 里「打印 inf + 跳过」，不许崩
    tmp = tempfile.mkdtemp(prefix="kgap_")
    try:
        for name, text in (("fgap.eln", FGAP), ("fgap.tu", FGAP_TU)):
            with io.open(os.path.join(tmp, name), "w",
                         encoding="utf-8", newline="\n") as fh:
                fh.write(text.lstrip("\n"))
        rows = kvo.measure_one(tmp, "fgap", 8)
        check(len(rows) == 1 and rows[0]["status"] == "kappa_inf",
              "kappa_vs_ops：κ 含符号 → status=kappa_inf（不崩）",
              str(rows[:1]))
        if rows:
            check("inf" in str(rows[0].get("detail", "")).lower()
                  or rows[0]["status"] == "kappa_inf",
                  "kappa_vs_ops：该点被**跳过**（不判成立、不判破界）",
                  str(rows[0]))
        summary = kvo.summarize(rows, [8])
        check(("fgap", "f") in summary.get("symbolic", []),
              "kappa_vs_ops：summarize 把它放进 symbolic 栏（不进 ok_full）",
              str(summary.get("symbolic")))
        check(not summary.get("broken"),
              "kappa_vs_ops：符号项**不**算破界", str(summary.get("broken")))
        table = kvo.render_table(rows, [8])
        check("inf" in table, "kappa_vs_ops：表格里照实打印 inf", table)
    finally:
        for name in ("fgap.eln", "fgap.tu"):
            p = os.path.join(tmp, name)
            if os.path.exists(p):
                os.remove(p)
        os.rmdir(tmp)
    print()


# ---------------------------------------------------------------- ⑤ 括号（叶子）
#
# **2026-11-09**：叶子函数 κ < ops 的根因。`paren` 在 κ 里原先**免费**
# （`_k_expr_paren` 只返回里面的成本），而解释器 `_eval` 对**每个非 None 节点**
# 记 1（含 `paren` 包裹，见 `test_units.py` 的单位定义）——于是**每层括号在 κ 里
# 少 1**。大函数里被常数余量盖住，**只有把 SHA-256 的叶子单独测才看得见**：
# `rotr` κ=10 < ops=13、`big_sigma0` κ=42 < ops=51。
# 修法：括号节点补记 `OP_COST`（κ 只增不减），**不动解释器计数器**
# ——它数的就是 AST 节点，改它等于重新定义单位、还会让所有历史 ops 一起变。
LEAF = """
u32 rotr(u32 x, u32 n) {
    return (x >> n) | (x << (32 - n));
}
u32 ch(u32 x, u32 y, u32 z) {
    return (x & y) ^ (~x & z);
}
u32 maj(u32 x, u32 y, u32 z) {
    return (x & y) ^ (x & z) ^ (y & z);
}
u32 big_sigma0(u32 x) {
    return rotr(x, 2) ^ rotr(x, 13) ^ rotr(x, 22);
}
u32 small_sigma0(u32 x) {
    return rotr(x, 7) ^ rotr(x, 18) ^ (x >> 3);
}
u32 just_var(u32 x) { return x; }
u32 just_paren(u32 x) { return (x); }
u32 just_paren2(u32 x) { return ((x)); }
u32 just_paren3(u32 x) { return (((x))); }
u32 just_call(u32 x) { return rotr(x, 2); }
u32 just_binop(u32 x, u32 y) { return x ^ y; }
u32 just_not(u32 x) { return ~x; }
u32 just_shift(u32 x) { return x >> 2; }
u32 just_min(u32 x, u32 y) { return min(x, y); }
u32 just_max(u32 x, u32 y) { return max(x, y); }
u64 just_cast(u32 x) { return x as u64; }
"""


def test_leaf_parens():
    print("[5] 叶子函数：κ ≥ ops，且缺口 0（修前每层括号少 1）")
    #: (函数, 实参, 修前 κ, 实测 ops)。修前 κ 是 `_k_expr_paren` 旧实现下的值。
    cases = (
        ("rotr", [5, 3], 10, 13),
        ("ch", [1, 2, 3], 9, 11),
        ("maj", [1, 2, 3], 12, 15),
        ("big_sigma0", [5], 42, 51),
        ("small_sigma0", [5], 32, 39),
        ("just_call", [5], 14, 17),
        ("just_paren", [5], 2, 3),
        ("just_paren2", [5], 2, 4),
        ("just_paren3", [5], 2, 5),
    )
    for fn, args, pre, want_ops in cases:
        k = kappa(LEAF, "", fn, {})
        o = ops_of_call(fn, args, LEAF)
        check(o == want_ops, f"{fn}：ops = {want_ops}（基准）", str(o))
        check(k >= pre, f"{fn}：κ {k:g} ≥ 修前 {pre:g}（只增不减）", str(k))
        check(o <= k, f"{fn}：ops {o} ≤ κ {k:g}（上界成立）")
        check(o - k == 0, f"{fn}：缺口 {o - k:g} = 0（修前 {o - pre:g}）")

    # 括号层数必须**线性**进 κ：每多一层 +1（解释器同样 +1）
    kv = kappa(LEAF, "", "just_var", {})
    k1 = kappa(LEAF, "", "just_paren", {})
    k3 = kappa(LEAF, "", "just_paren3", {})
    check(k3 - kv == 3 and k1 - kv == 1,
          f"括号：κ(var)={kv:g} → 1 层 {k1:g} → 3 层 {k3:g}（每层 +1）",
          f"{kv:g}/{k1:g}/{k3:g}")

    # 单运算符 / `min`/`max` **不是**括号问题：修前就对得上，修后必须不变
    for fn, args, want in (("just_binop", [5, 3], 4), ("just_not", [5], 3),
                           ("just_shift", [5], 4), ("just_min", [5, 3], 5),
                           ("just_max", [5, 3], 5)):
        k = kappa(LEAF, "", fn, {})
        o = ops_of_call(fn, args, LEAF)
        check(k == want and o == want,
              f"{fn}：κ = ops = {want}（修前就成立，不被括号修法带偏）",
              f"κ={k:g} ops={o}")

    # `as` 的**类型子节点**在 κ 里也记了 1（解释器不进入它）⇒ κ 比 ops 大 1。
    # 方向安全（上界只会更松），所以**不**为了对齐把它调小。
    kc = kappa(LEAF, "", "just_cast", {})
    oc = ops_of_call("just_cast", [5], LEAF)
    check(kc >= oc, f"just_cast：κ {kc:g} ≥ ops {oc}（多记 1，安全方向）",
          f"κ={kc:g} ops={oc}")
    print()


#: `Ok` / `Err` / `print` / `expr-stmt` 的 κ vs ops（2026-11-10 实测，全部相等）。
#:
#: 这几条曾是 `interp.py` 文件头「已知的口径差」里点名的：`Ok`/`Err` 说 κ 不计实参、
#: `print` 说 κ 少 1、`expr-stmt` 说每句多 1。2026-11-10 逐条实测（见下面断言）
#: κ 与 ops **完全相等**——那段注释已按实测改写，这里把结论钉住。
MISC_TU = """
error E;
"""
MISC = """
u64! okk(u64 x) { return Ok(x + 1); }
u64! errk(u64 x) { return Err(E::Bad); }
u64 pr(u64 x) { print(x); return x; }
u64 prf(u64 x) { print("{}", x); return x; }
u64 stmt2(u64 x) { x * 2; x + 1; return x; }
"""


def test_leaf_misc():
    print("[6] 叶子：`Ok` / `Err` / `print` / `expr-stmt` 的 κ = ops（修前注释已过时）")
    for fn, args in (("okk", [5]), ("errk", [5]), ("pr", [5]),
                     ("prf", [5]), ("stmt2", [5])):
        k = kappa(MISC, MISC_TU, fn, {})
        o = ops_of_call(fn, args, MISC, MISC_TU)
        check(o - k == 0, f"{fn}：κ = ops = {o:g}（缺口 0）", f"κ={k:g} ops={o}")
    print()


def main() -> int:
    test_recursion_depth()
    test_tree_recursion()
    test_builtin_args()
    test_minmax_dispatch()
    test_fnvalue()
    test_leaf_parens()
    test_leaf_misc()

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
