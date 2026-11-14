#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_halving.py —— **区间对半分的递归**（`merge_sort`）能算出 κ

## 这份测试钉什么

`merge_sort` 一条执行路径上有 **2** 个自调用点，旧判据按「树递归 ⇒ ⊤」处理。
但它**不是**指数：

    merge_sort(xs, lo, mid);        // [lo, mid)
    merge_sort(xs, mid, hi);        // [mid, hi)

两次调用拿的区间**互不相交、并集是父区间、各自 ≤ 一半**。于是

  · 第 k 层的区间长度 ≤ (hi-lo)/2^k，且同层区间互不相交 ⇒ 第 k 层 ≤ 2^k 个激活；
  · 叶子（不再自调用）的区间长度 ≥ 1 且互不相交 ⇒ **叶子数 ≤ hi - lo**；
  · 二叉树内部激活数 = 叶子数 - 1 < 叶子数
  ⇒ **总激活数 ≤ 2 × (hi - lo)**。

这是**可证明的上界**，不是"假设程序会停"：`fib(n-1) + fib(n-2)` 的两棵子树吃
的是**同一组输入**（每层把整个区间再试一遍），所以那里这条论证不成立、仍是 ⊤。

| 形状 | 判据 | κ |
|---|---|---|
| 2 个自调用点，拿 **互不相交的两半**（`[lo,mid)` / `[mid,hi)`） | 见 `_halving_span` | **按层求和**：`(2s+1)·A + B·s·(log2(s+1)+1)`，`s = hi - lo` |
| 2 个自调用点，拿**同一组输入**（`fib`） | 区间不划分 | **⊤** |
| 自调用在轮数推不出 ≤1 的循环里（`place` 的 `for col`） | 每层可能跑 n 次 | **⊤** |

**为什么是"按层求和"而不是"激活数 × 单层"**（2026-11-12 夜修）：

    T(s) = 2·T(s/2) + 单层(s)，单层 = A + B·s
    ⇒ T(s) = Σ_{k<K} [2^k·A + B·s] = A·(2^K − 1) + B·s·K = Θ(s log s)

旧式把**入口区间**的单层成本乘上激活数（`2s × (A + B·s)`），每一层都乘同一个
`s` ⇒ `O(s²)`。后果不是"松一点"：作者写 `target O(n log n)`（正确）会被判
「阶不够」= **假债务**，比 ⊤ 更坏。层数 `K = ceil(log2 s)` 的两个 `+1` 余量
留在 `(2s+1)` 与 `log2(s+1)+1` 里（见 `_halving_level_sum`）。
这份文件只钉"形状 + 激活数"；`O(n log n)` 判定翻转与 `place` 不被覆盖
由 `test_kappa_merge_log.py` 钉。

## 四类断言（只比表达式形状不算验证）

1. **等价性**：同一逻辑的三种写法（早退 `< 2` / `<= 1`、两次调用顺序交换、
   `(lo+hi)/2` / `(lo+hi)>>1`）的 κ **逐字节相同**，解释器实跑的返回与 ops 也相同。
2. **激活数上界**：≥3 个规模真跑解释器，断 `真实激活数 ≤ 2×(hi-lo)`——这是
   本任务的核心论证，必须逐规模实测，不能只在 n=1 上看一眼。
3. **ops ≤ κ**：同一批规模上断 `ops ≤ κ`（κ 是**上界**，不是估计值）。
4. **κ 的形状**：`log2(...)` 与 `hi - lo` 的**乘积**（按层求和的痕迹），
   且 κ 里**没有** `mid` 这类被调方绝对下标。

跑法：

    cd tests/cases; $env:PYTHONIOENCODING="utf-8"; python test_kappa_halving.py
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
    ch = parse_source(eln, "half.eln").module
    decl = parse_source(tu, "half.tu").module if tu else None
    return ch, decl


def ci_of(eln: str, tu: str = "") -> CostInterp:
    ch, decl = load(eln, tu)
    return CostInterp(ch, decl)


def kappa(eln: str, tu: str, fn: str, env: dict):
    """`(Cost, 数值)`：含符号项/⊤ 时数值是 inf。"""
    k = ci_of(eln, tu).cost_of(fn)
    return k, kappa_at(k, dict(env))


def run_call(fn: str, args, eln: str, tu: str = ""):
    """`(返回值, ops, 每个函数的实际调用次数)`。

    激活数取解释器自己的 `calls` 计数**按被调函数名**那一条——它正好是
    "这一次运行里 `fn` 被进入了几次"，比自己埋计数器可信。
    """
    ch, decl = load(eln, tu)
    it = Interp(ch, decl)
    ops_enable(True)
    try:
        ops_reset()
        v = it.call(fn, args)
        return v, ops_of(), dict(it.calls)
    finally:
        ops_enable(False)


def xs(n: int):
    """`n` 个降序元素：归并排序在它上面**每次都真的两路都走**。"""
    return [i for i in range(n)][::-1]


def _is_level_sum(render: str) -> bool:
    """κ 里有**乘在区间长度上**的 `log2` 因子（按层求和的痕迹）。

    去掉空白后找 `)×(log2(`：`B×((s+1)/2)×(log2(…) + 1)` 的中间那一段。
    只找 `log2(` 是不够的——那是"出现过对数"，不是"层数乘在区间长度上"。
    """
    return ")×(log2(" in "".join(render.split())


# ---------------------------------------------------------------- 语料

#: 靶子形状：`merge_sort`（区间对半分）+ 一个线性的 `merge_halves`。
HALVING_ELN = """
u64 merge_halves(vector<u64>& xs, u64 lo, u64 mid, u64 hi) {
    u64 i = lo;
    u64 s = 0;
    while (i < hi) { s = s + xs[i]; i = i + 1; }
    return s;
}

void merge_sort(vector<u64>& xs, u64 lo, u64 hi) {
    if (hi - lo < 2) { return; }
    u64 mid = lo + (hi - lo) / 2;
    merge_sort(xs, lo, mid);
    merge_sort(xs, mid, hi);
    merge_halves(xs, lo, mid, hi);
}

u64 entry(vector<u64>& xs) {
    merge_sort(xs, 0, xs.size());
    return xs[0];
}
"""


def tu_halving() -> str:
    return """
spec merge_halves(vector<u64>& xs, u64 lo, u64 mid, u64 hi) -> u64 {
    effects {};
    target { time: { max: O(hi) }, allocation: { max: 0 }, };
}
spec merge_sort(vector<u64>& xs, u64 lo, u64 hi) -> () {
    effects {};
    recursion depth <= 32;
    target { time: { max: O(xs.size() * xs.size()) }, allocation: { max: 0 }, };
}
spec entry(vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.size() * xs.size()) }, allocation: { max: 0 }, };
}
"""


# ---------------------------------------------------------------- [1] 靶子

#: 实测规模的档位：三步都覆盖"奇数长度 / 2 的幂 / 稍大"。
SIZES = (3, 4, 8, 16, 32, 64)


def test_target_shape():
    print("[1] `merge_sort`：κ 不是 ⊤，且**真实激活数 ≤ 2×(hi-lo)**（逐规模实测）")
    k, kv = kappa(HALVING_ELN, tu_halving(), "merge_sort", {"xs": 16, "lo": 0, "hi": 16})
    check(not k.is_top(), "merge_sort：κ 不是 ⊤", k.render())
    check("hi - lo" in k.render(),
          "激活数项用**区间长度** `hi - lo` 表达（不是常数、不是 D）", k.render())
    # **按层求和**（2026-11-12 夜）：每个自调用点记 `LS(ceil(s/2))`，其中
    # `LS(s) = (2s+1)·A + B·s·(log2(s+1)+1)`（见 `_halving_level_sum`）。
    # 修前是 `(hi - lo) + 1` 当激活数 × 单层 ⇒ 每层乘同一个入口长度 ⇒ O(n²)。
    check(_is_level_sum(k.render()),
          "每个自调用点按**层求和**记：`… ×s×(log2(…)+1)`（子树 ≤ 父区间的一半）",
          k.render())
    check(k.render().count("log2(") == 2,
          "两个自调用点各一个 `log2` 因子（层数是 log）", k.render())
    check("mid" not in k.render(),
          "κ 里**没有** `mid`（绝对下标是被调方口径的泄漏，也是 O(n²) 的根因）",
          k.render())
    check(kv < float("inf"), "κ 在 (xs=16, lo=0, hi=16) 处**求得出有限值**", str(kv))
    print()
    print("       n   真实激活数   2×(hi-lo)   上界成立   ops        κ")
    for n in SIZES:
        _v, o, calls = run_call("merge_sort", [xs(n), 0, n], HALVING_ELN, tu_halving())
        act = calls.get("merge_sort", 0)
        _k2, kv2 = kappa(HALVING_ELN, tu_halving(), "merge_sort",
                         {"xs": n, "lo": 0, "hi": n})
        bound = 2 * n
        print(f"      {n:3d}   {act:8d}   {bound:8d}   {str(act <= bound):8s}   "
              f"{o:8d}   {kv2:10.0f}")
        check(act <= bound, f"n={n}：激活数 {act} ≤ 2×(hi-lo) = {bound}", str(act))
        check(o <= kv2, f"n={n}：ops {o} ≤ κ {kv2:g}", f"{o} > {kv2}")
    # 激活数**恰好**是 2n-1（叶子数 = n，内部 = n-1）：说明上界只差常数 1
    for n in (4, 8, 16):
        _v, _o, calls = run_call("merge_sort", [xs(n), 0, n], HALVING_ELN, tu_halving())
        check(calls.get("merge_sort", 0) == 2 * n - 1,
              f"n={n}：激活数**正好**是 2n-1（叶子 n 个 + 内部 n-1 个）",
              str(calls.get("merge_sort", 0)))
    print()


#: **二分式递归（带返回值的分治）**：`return f(lo,mid) + f(mid,hi);`。
#: 自调用在**表达式**里而不是语句位置——判据只看"两次各拿哪个区间"，
#: 与结果怎么用无关，所以这种写法也要算出来。
BISECT_ELN = """
u64 bisect(const vector<u64>& xs, u64 lo, u64 hi) {
    if (hi - lo <= 1) { return hi - lo; }
    u64 mid = (lo + hi) / 2;
    return bisect(xs, lo, mid) + bisect(xs, mid, hi);
}
"""


def tu_bisect() -> str:
    return """
spec bisect(const vector<u64>& xs, u64 lo, u64 hi) -> u64 {
    effects {};
    recursion depth <= 32;
}
"""


def test_bisect_probe():
    print("[1-b] 二分式递归（返回值在表达式里）：同样逐规模实测激活数 / ops ≤ κ")
    k = ci_of(BISECT_ELN, tu_bisect()).cost_of("bisect")
    check(not k.is_top(), "bisect：κ 不是 ⊤", k.render())
    check("hi - lo" in k.render(), "bisect：激活数项仍是 `hi - lo`", k.render())
    print("       n   真实激活数   2×(hi-lo)   上界成立   ops        κ")
    for n in (4, 8, 16, 32):
        v, o, calls = run_call("bisect", [list(range(n)), 0, n], BISECT_ELN, tu_bisect())
        act = calls.get("bisect", 0)
        _k2, kv2 = kappa(BISECT_ELN, tu_bisect(), "bisect",
                         {"xs": n, "lo": 0, "hi": n})
        print(f"      {n:3d}   {act:8d}   {2 * n:8d}   {str(act <= 2 * n):8s}   "
              f"{o:8d}   {kv2:10.0f}")
        check(act <= 2 * n, f"n={n}：激活数 {act} ≤ 2×(hi-lo) = {2 * n}", str(act))
        check(o <= kv2, f"n={n}：ops {o} ≤ κ {kv2:g}", f"{o} > {kv2}")
        check(v == n, f"n={n}：返回值 {v} = n（递归把整个区间数完了）", str(v))
    print()


# ---------------------------------------------------------------- [2] 等价写法

#: 同一逻辑的五种写法。只换**拼写**，不换语义（`eqa`…`eqe` 是各自独立的函数名）。
EQ_TMPL = """
void %(name)s(vector<u64>& xs, u64 lo, u64 hi) {
    if (hi - lo %(GUARD)s) { return; }
    u64 mid = %(MID)s;
%(CALLS)s
}
"""

_CALLS_FWD = "    %(name)s(xs, lo, mid);\n    %(name)s(xs, mid, hi);"
_CALLS_REV = "    %(name)s(xs, mid, hi);\n    %(name)s(xs, lo, mid);"


def _eq_src(name: str, guard: str, mid: str, calls: str) -> str:
    return EQ_TMPL % {"name": name, "GUARD": guard, "MID": mid,
                      "CALLS": calls % {"name": name}}


def tu_eq(name: str) -> str:
    return """
spec %s(vector<u64>& xs, u64 lo, u64 hi) -> () {
    effects {};
    recursion depth <= 32;
}
""" % name


def test_equivalence():
    print("[2] 等价性：同逻辑的三种写法，κ / 返回值 / ops **逐字节相同**")
    variants = (
        ("eqa", "< 2", "lo + (hi - lo) / 2", _CALLS_FWD),      # 基准
        ("eqb", "<= 1", "lo + (hi - lo) / 2", _CALLS_FWD),     # 早退换写法
        ("eqc", "< 2", "lo + (hi - lo) / 2", _CALLS_REV),      # 两次调用换顺序
        ("eqd", "< 2", "(lo + hi) / 2", _CALLS_FWD),           # 中点换写法（/）
        ("eqe", "< 2", "(lo + hi) >> 1", _CALLS_FWD),          # 中点换写法（>>）
    )
    renders, values, opss, acts = {}, {}, {}, {}
    for name, guard, mid, calls in variants:
        eln = _eq_src(name, guard, mid, calls)
        k, _kv = kappa(eln, tu_eq(name), name, {"xs": 16, "lo": 0, "hi": 16})
        check(not k.is_top(), f"{name}（{guard} / {mid}）：κ 有限", k.render())
        renders[name] = k.render()
        v, o, c = run_call(name, [xs(16), 0, 16], eln, tu_eq(name))
        values[name] = v
        opss[name] = o
        acts[name] = c.get(name, 0)
    # `< 2` 与 `<= 1` 是同一件事；两次调用交换顺序不改变成本之和。
    check(renders["eqa"] == renders["eqb"] == renders["eqc"],
          "早退 `<2` / `<=1`、调用顺序互换：κ **逐字节相同**",
          f"{renders['eqa']} vs {renders['eqb']} vs {renders['eqc']}")
    # `(lo+hi)/2` 与 `(lo+hi)>>1`：位运算与除 2 同值，各自只算 1 个操作。
    check(renders["eqd"] == renders["eqe"],
          "中点 `(lo+hi)/2` 与 `(lo+hi)>>1`：κ **逐字节相同**",
          f"{renders['eqd']} vs {renders['eqe']}")
    check(len({values[n] for n, *_ in variants}) == 1,
          "五种写法的返回值相同", str(values))
    # 早退/顺序那一组：ops 必须逐字节相同。
    check(opss["eqa"] == opss["eqb"] == opss["eqc"],
          "早退 `<2` / `<=1`、调用顺序互换：解释器 ops **相同**", str(opss))
    check(opss["eqd"] == opss["eqe"],
          "中点 `(lo+hi)/2` 与 `(lo+hi)>>1`：解释器 ops **相同**", str(opss))
    check(len({acts[n] for n, *_ in variants}) == 1,
          "五种写法的**激活数**相同", str(acts))
    # 两种中点写法**不是**同一段代码：`lo + (hi - lo)/2` 每层多付 2 个操作
    # （`lo` 再取一次、多一个 `-`）。所以它们的 ops（以及 κ 的常数项）**该不同**，
    # 而激活数（形状）**该相同**——这正是"形状判据"与"常数记账"的分界。
    _nonleaf = acts["eqa"] - 16            # 激活数 − 叶子数 = 内部激活数
    check(opss["eqa"] - opss["eqd"] == 2 * _nonleaf,
          f"两种中点的 ops 差 = 每层多算的 2 个操作 × 内部激活数 {_nonleaf}",
          f"{opss['eqa']} - {opss['eqd']}")
    check(renders["eqa"] != renders["eqd"],
          "`lo + (hi - lo)/2` 比 `(lo+hi)/2` 多一个 `+` ⇒ κ 的常数项不同（该不同）")
    check(renders["eqa"].count("log2(") == renders["eqd"].count("log2("),
          "但两种中点的**层求和项个数相同**（各 2 个自调用点）")
    print()


# ---------------------------------------------------------------- [3] 拒绝的形状

#: 同一组输入反复试（朴素 fib）：**不是**区间对半分。
FIB_ELN = """
u64 fib(u64 n) {
    if (n < 2) { return n; }
    return fib(n - 1) + fib(n - 2);
}
"""

#: 自调用在 `for col` 里（N 皇后）：每层可能跑 n 次。
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

#: 区间对半分，但 `mid` 在两次调用之间**被改写**（第一次调用之后又算了一次）——
#: 第二次拿到的 mid 与第一次不同 ⇒ `[lo,mid)` 与 `[mid,hi)` 不再是一个划分。
MID_REWRITE_ELN = """
void ms_rw(vector<u64>& xs, u64 lo, u64 hi) {
    if (hi - lo < 2) { return; }
    u64 mid = lo + (hi - lo) / 2;
    ms_rw(xs, lo, mid);
    mid = lo + (hi - lo) / 4;
    ms_rw(xs, mid, hi);
}
"""

#: 没有早退（`hi - lo` 永不为小）⇒ 叶子数没有界，激活数上界推不出。
NO_BASE_ELN = """
void ms_nb(vector<u64>& xs, u64 lo, u64 hi) {
    u64 mid = lo + (hi - lo) / 2;
    ms_nb(xs, lo, mid);
    ms_nb(xs, mid, hi);
}
"""

#: `mid` 不是中点（恒等于 lo）⇒ 左半是空区间、右半是整个父区间 ⇒ 深度无界。
BAD_MID_ELN = """
void ms_bm(vector<u64>& xs, u64 lo, u64 hi) {
    if (hi - lo < 2) { return; }
    u64 mid = lo;
    ms_bm(xs, lo, mid);
    ms_bm(xs, mid, hi);
}
"""

#: `lo` / `hi` 按**引用**：子调用能改父层的端点，"区间不变"不成立。
BYREF_ELN = """
void ms_br(vector<u64>& xs, u64& lo, u64& hi) {
    if (hi - lo < 2) { return; }
    u64 mid = lo + (hi - lo) / 2;
    ms_br(xs, lo, mid);
    ms_br(xs, mid, hi);
}
"""

#: 两次自调用是"两半"，但**另外**还经 `ms_ih` 绕回自己（混合递归）：
#: 那条边的深度不在「一层 × 激活数」的乘数里 ⇒ 切掉就是少算 ⇒ ⊤。
INDIRECT_ELN = """
void ms_ih(vector<u64>& xs, u64 lo, u64 hi) { ms_mix(xs, lo, hi); }

void ms_mix(vector<u64>& xs, u64 lo, u64 hi) {
    if (hi - lo < 2) { return; }
    u64 mid = lo + (hi - lo) / 2;
    ms_mix(xs, lo, mid);
    ms_mix(xs, mid, hi);
    ms_ih(xs, lo, hi);
}
"""


def _is_top(eln: str, tu: str, fn: str) -> tuple:
    ci = ci_of(eln, tu)
    k = ci.cost_of(fn)
    return k, ci


def test_rejected():
    print("[3] 拒绝的形状：每一条都仍 ⊤（缺一即不猜）")
    cases = (
        ("fib", FIB_ELN,
         "spec fib(u64 n) -> u64 { effects {}; recursion depth <= 8; }",
         "同一组输入反复试（两棵子树吃同样的 n-1 / n-2）"),
        ("ms_rw", MID_REWRITE_ELN,
         "spec ms_rw(vector<u64>& xs, u64 lo, u64 hi) -> () { effects {}; recursion depth <= 8; }",
         "`mid` 在两次调用之间被改写（划分不再成立）"),
        ("ms_nb", NO_BASE_ELN,
         "spec ms_nb(vector<u64>& xs, u64 lo, u64 hi) -> () { effects {}; recursion depth <= 8; }",
         "没有 `hi - lo <= C` 的早退（叶子数没有界）"),
        ("ms_bm", BAD_MID_ELN,
         "spec ms_bm(vector<u64>& xs, u64 lo, u64 hi) -> () { effects {}; recursion depth <= 8; }",
         "`mid = lo`（右半还是整个父区间 ⇒ 深度无界）"),
        ("ms_br", BYREF_ELN,
         "spec ms_br(vector<u64>& xs, u64& lo, u64& hi) -> () { effects {}; recursion depth <= 8; }",
         "`lo` / `hi` 按引用（子调用能改父层端点）"),
    )
    for fn, eln, tu, why in cases:
        k, ci = _is_top(eln, tu, fn)
        check(k.is_top(), f"{fn} ⇒ ⊤（{why}）", k.render())
        check(fn in ci.top_reasons, f"{fn}：记下了 ⊤ 的原因", str(ci.top_reasons))
    # `_halving_span` 本身也要说「不认」——判据与 κ 是同一份，不该一处松一处紧。
    for fn, eln, _tu, _why in cases:
        ci = ci_of(eln)
        check(ci._halving_span(fn) is None, f"{fn}：`_halving_span` 返回 None")
    # **`place` 不在这张表里**（2026-11-12 夜，判据 ④）：n 叉递归现在由
    # `_tree_activation_bound` 给 **幂界** `(D+1)·B^D`，不再是 ⊤。这一节要钉的
    # 是「**按层求和**只对"两半"成立」——`_halving_span` 仍然不认它（上面那一轮
    # 也顺带钉住这条：`_halving_span("place") is None`）。④ 的判据、幂界与
    # 解释器实测在 `tests/cases/test_kappa_tree_depth.py`。
    ci = ci_of(PLACE_ELN,
               "spec place(u64 row, u64 n) -> u64 { effects {}; recursion depth <= 8; }")
    k_place = ci.cost_of("place")
    check(not k_place.is_top() and "9×n^8" in k_place.render(),
          "place：④ 给的是幂界 `9×n^8×…`（不是按层求和，也不是 ⊤）",
          k_place.render())
    check(ci._halving_span("place") is None,
          "place：`_halving_span` **仍然**不认（形状不是两半）")
    # **混合递归**：形状是"两半"（`_halving_span` 认），但另有一条经
    # `ms_ih` 绕回自己的边——那条边的深度不在「一层 × 激活数」的乘数里，
    # 切掉就是少算 ⇒ κ 仍是 ⊤（原因行写清楚是哪条边）。
    ci = ci_of(INDIRECT_ELN)
    try:
        k_out = ci.cost_of("ms_mix")
        _raised = ""
    except Exception as exc:                            # noqa: BLE001
        k_out, _raised = None, str(exc)
    check(ci._halving_span("ms_mix") is not None,
          "ms_mix：形状判据**认**（两次确实各拿一半）")
    check(k_out is None or k_out.is_top(),
          "ms_mix：但 κ 仍是 ⊤（经 `ms_ih` 绕回自己的那条边没被乘数覆盖）",
          (k_out.render() if k_out is not None else _raised))
    print()


# ---------------------------------------------------------------- [4] 调用方

def test_caller_instantiation():
    print("[4] 调用方：`merge_sort(xs, 0, xs.size())` 的 κ 有限，`hi - lo` 换成 n(xs)")
    k = ci_of(HALVING_ELN, tu_halving()).cost_of("entry")
    check(not k.is_top(), "entry（调 merge_sort 的那个）κ 不是 ⊤", k.render())
    check("hi - lo" not in k.render(),
          "调用方的 κ 里**不再有** `hi - lo`（被调方的形参名不许留下）", k.render())
    for n in (4, 8, 16, 32):
        v, o, calls = run_call("entry", [xs(n)], HALVING_ELN, tu_halving())
        k2 = ci_of(HALVING_ELN, tu_halving()).cost_of("entry")
        kv2 = kappa_at(k2, {"xs": n})
        check(o <= kv2, f"n={n}：ops {o} ≤ κ(entry) {kv2:g}", f"{o} > {kv2}")
        check(calls.get("merge_sort", 0) <= 2 * n,
              f"n={n}：entry 里 merge_sort 的激活数 {calls.get('merge_sort', 0)} ≤ 2n",
              str(calls))
    print()


# ---------------------------------------------------------------- [5] CLI

def run_cli(argv, cwd):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = SRC
    p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "src", "dsh.py")] + argv,
                       cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def test_cli():
    print("[5] `dsh cost --explain`：merge_sort 打印**具体式**（不是 ⊤）")
    tmp = tempfile.mkdtemp(prefix="halving_")
    try:
        tu_p = os.path.join(tmp, "half.tu")
        eln_p = os.path.join(tmp, "half.eln")
        for path, text in ((tu_p, tu_halving()), (eln_p, HALVING_ELN)):
            with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        rc, out = run_cli(["cost", tu_p, eln_p, "--explain"], tmp)
        check(rc == 0, "区间二分探针：cost 退出码 0", str(rc))
        line = next((ln for ln in out.splitlines() if "merge_sort" in ln and "κ ≤" in ln), "")
        check("⊤" not in line, "merge_sort：`κ ≤ …` 里没有 ⊤", line)
        check("hi - lo" in line, "merge_sort：κ 用 `hi - lo` 表达激活数", line)
        check("✓ entry" in out or "✓ merge_sort" in out,
              "target 判为达成（`O(n²)` 的上界盖得住）",
              [ln for ln in out.splitlines() if "✓" in ln][:3])
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
    test_target_shape()
    test_bisect_probe()
    test_equivalence()
    test_rejected()
    test_caller_instantiation()
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
