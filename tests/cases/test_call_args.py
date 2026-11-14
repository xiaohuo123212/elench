#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_call_args.py —— 普通 `f(x)` 的**实参计费**（call 节点）

## 钉住什么

`cost_interp._call` 只处理 `a.f(x)` 之外的那些 `f(x)`。修前它有三条路径
**一个实参操作都不记**（`args` 节点在 `_expr` 里走兜底 `Cost.num(0)`）：

| `_call` 分支 | 修前 | 现在 |
|---|---|---|
| ① 有 target | `1 + target` | `1 + target + Σ实参` |
| ② 无 target → 内联 | `1 + 被调体` | `1 + 被调体 + Σ实参` |
| ③ 兜底（含函数值 `g(x)` / 枚举构造） | `1` | `1 + Σ实参` |

解释器 `_args` 对**每个实参表达式**求值一次（每个 `_eval` 起点 `_bump()` 记 1），
但 `args` 节点本身不求值 —— 所以 κ 必须逐个实参加，且**只加一次**。

## 判据

设 `κc = κ(compute)`（`cost_of` 的**函数体**成本，不含调用节点）：

    Δ := κ(f(compute(xs))) − κ(f(xs))

- **下界**：`Δ ≥ κc`（实参没被丢）——本测试更强，断言 `Δ == κc + 1`，
  多出的 **1** 是 `compute(xs)` 这个**调用节点**（`_call` 的 `OP_COST`）本身；
- **上界**：`Δ ≤ κc + 1`（**不是** `2×(κc+1)`）——这就是「防双重计费」的那条线：
  真要是把被调体算了两遍，Δ 会变成 `2κc + …`。

再加上 `ops ≤ κ`（`interp.ops_of`，整棵调用树）。

## 函数值调用：④ 之后有两条支路

`g(x)`（`g: func(A)->R`）在 `cost_interp` 里**不在 `self.fns`**，所以不能当普通
函数调。④ 之后按「调用点知不知道 `g` 绑到哪个具名函数」分两支：

| 情形 | κ | 为什么 |
|---|---|---|
| 调用点知道（`apply(xs, compute)`，实参是本模块具名函数） | `1 + Σargs + κ(compute)` | 具名函数是有限集合，被调体可静态求 |
| 不知道（直接分析 `apply(xs, g)` 本身） | `1 + Σargs + k(g)` | 契约项由**调用方**保证，记 0 是低估、记 inf 是诚实 |

于是本节的断言从「缺口恰好等于 κc」改成「**静态可求时缺口 0**」；符号支路
另测「κ 含 `k(g)`，`evaluate` 给 inf」——`tools/kappa_vs_ops.py` 必须能打印
`inf` 并跳过该点，不许在 `int(inf)` 上崩。

## 已知的、**不**归这里管的两件事

- 递归的**深度**：本文件的 `RECUR_TU` **没有** `recursion depth` 声明，所以
  这里钉的仍是「一层递归」下的**实参**计费（`i=2`）。带声明的深度乘数见
  `test_kappa_gaps.py` ①——那里 `down(xs, 8)` 的 κ 按 `recursion depth <= 8`
  乘到 `(8+1) × κ(体一次)`。
- **FFI**（`extern` 名字）的实参仍是另一条账，本轮不动。`print` / `map` /
  `filter` / `fold` / `count_if` / `sort` / `sort_by` 的实参已在 ② 修过
  （见 `test_kappa_gaps.py`）；`stdlib` / `min` / `max` / `method-call`
  在这里继续当「**已经计过、不许再加**」的对照组。

跑法：

    cd src; $env:PYTHONIOENCODING="utf-8"; python test_call_args.py
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

import os
import sys

# **src 优先**：`tools/` 里也有 `.py`，别让它遮住 `interp` / `parser`
sys.path.insert(0, os.path.join(ROOT, "tools"))

from cost_check import evaluate as kappa_at            # noqa: E402
from cost_interp import CostInterp                     # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset   # noqa: E402
from parser import parse_source                        # noqa: E402
from symbolic import Cost                              # noqa: E402

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
    ch = parse_source(eln, "call_args.eln").module
    decl = parse_source(tu, "call_args.tu").module if tu else None
    return ch, decl


def kappa(eln: str, tu: str, fn: str, n: int) -> float:
    """κ(fn) 在「容器长度 = n」处的数值（尺寸变量统一按 n 求值）。"""
    ch, decl = load(eln, tu)
    k = CostInterp(ch, decl).cost_of(fn)
    return kappa_at(k, {v: n for v in k.variables()})


def ops(fn: str, args, eln: str, tu: str = "") -> int:
    """整棵调用树的实测操作数（`ops_of()` 不给名字 = 总数）。"""
    ch, decl = load(eln, tu)
    ops_enable(True)
    try:
        ops_reset()
        Interp(ch, decl).call(fn, args)
        return ops_of()
    finally:
        ops_enable(False)


def xs(n):
    return [i for i in range(n)]


# ---------------------------------------------------------------- ① 用户函数
#: 无 target → 走**内联**分支；`compute` 返回**新容器**（prvalue）。
#:
#: **A-full（2026-11-09）**：`compute` 原先写 `return xs;`（`const&` 形参）。
#: 值语义下那是**一次真实的容器拷贝**（借用值返回 = 拷贝，设计 §3.2），
#: 于是 κ 会比解释器 ops 多出 n(xs)+1——本文件钉的是**实参记账**（Δ 与 ops 对得上），
#: 不是拷贝本身，所以改成返回 prvalue；拷贝路径由 `test_cost.py::test_value_semantics` 钉。
INLINE = """
u64 f(const vector<u64>& xs) { return xs.length(); }
vector<u64> compute(const vector<u64>& xs) { return vector<u64>::make(); }
u64 plain(const vector<u64>& xs) { return f(xs); }
u64 nested(const vector<u64>& xs) { return f(compute(xs)); }
"""

TARGET = """
spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""


def test_user_fn():
    print("[1] 用户函数（内联）：`f(compute(xs))` 比 `f(xs)` 多出 compute 的成本")
    n = 16
    kc = kappa(INLINE, "", "compute", n)
    kp = kappa(INLINE, "", "plain", n)
    kn = kappa(INLINE, "", "nested", n)
    op = ops("plain", [xs(n)], INLINE)
    on = ops("nested", [xs(n)], INLINE)
    delta = kn - kp
    check(kc == 2, f"κ(compute) = 2（`return xs;` → 语句 1 + 路径 1）", str(kc))
    check(delta == kc + 1,
          f"Δ = κ(nested) − κ(plain) = {delta:g} = κc {kc:g} + 1（+1 是调用节点）",
          f"κ(plain)={kp:g} κ(nested)={kn:g}")
    check(delta <= kc + 1, "上界：Δ ≤ κc + 1（没有把被调体算两遍）", str(delta))
    check(op <= kp, f"ops(plain) {op} ≤ κ(plain) {kp:g}")
    check(on <= kn, f"ops(nested) {on} ≤ κ(nested) {kn:g}")
    # 实测差与 κ 差应当一致（都是「多求值一个 compute(xs)」）
    check(on - op == delta,
          f"ops 增量 {on - op} = κ 增量 {delta:g}（两边同一件事）",
          f"{on - op} vs {delta:g}")
    print()


def test_user_fn_target():
    print("[2] 用户函数（有 target）：target 是**被调**的成本，实参由**调用方**另算")
    n = 64
    kc = kappa(INLINE, "", "compute", n)          # compute 本身不带 target → 内联
    kp = kappa(INLINE, TARGET, "plain", n)
    kn = kappa(INLINE, TARGET, "nested", n)
    op = ops("plain", [xs(n)], INLINE, TARGET)
    on = ops("nested", [xs(n)], INLINE, TARGET)
    delta = kn - kp
    check(delta == kc + 1,
          f"Δ = {delta:g} = κc {kc:g} + 1（target 之外，实参也进了 κ）",
          f"κ(plain)={kp:g} κ(nested)={kn:g}")
    check(delta <= kc + 1, "上界：Δ ≤ κc + 1（target 与实参不重叠）", str(delta))
    check(delta >= kc, "下界：Δ ≥ κc（实参没被 target 吞掉）", str(delta))
    check(op <= kp, f"ops(plain) {op} ≤ κ(plain) {kp:g}")
    check(on <= kn, f"ops(nested) {on} ≤ κ(nested) {kn:g}")
    print()


# ---------------------------------------------------------------- ② 递归
RECUR = """
u64 down(const vector<u64>& xs, u64 i) {
    if (i == 0) { return xs.length(); }
    return 1 + down(xs, i - 1);
}
u64 down_nested(const vector<u64>& xs, u64 i) {
    if (i == 0) { return xs.length(); }
    return 1 + down(compute(xs), i - 1);
}
vector<u64> compute(const vector<u64>& xs) { return vector<u64>::make(); }
"""

RECUR_TU = """
spec down(const vector<u64>& xs, u64 i) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec down_nested(const vector<u64>& xs, u64 i) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""


def test_recursion():
    print("[3] 递归：递归那一跳的**实参**同样要算（i=2，一层递归）")
    n, i = 64, 2
    kc = kappa(RECUR, RECUR_TU, "compute", n)
    kd = kappa(RECUR, RECUR_TU, "down", n)
    kx = kappa(RECUR, RECUR_TU, "down_nested", n)
    od = ops("down", [xs(n), i], RECUR, RECUR_TU)
    ox = ops("down_nested", [xs(n), i], RECUR, RECUR_TU)
    delta = kx - kd
    check(delta == kc + 1,
          f"Δ = {delta:g} = κc {kc:g} + 1（递归实参也算进去）",
          f"κ(down)={kd:g} κ(down_nested)={kx:g}")
    check(delta <= kc + 1, "上界：Δ ≤ κc + 1（没有重复计递归体）", str(delta))
    check(od <= kd, f"ops(down) {od} ≤ κ(down) {kd:g}")
    check(ox <= kx, f"ops(down_nested) {ox} ≤ κ(down_nested) {kx:g}")
    check(ox - od == delta, f"ops 增量 {ox - od} = κ 增量 {delta:g}",
          f"{ox - od} vs {delta:g}")
    print()


# ---------------------------------------------------------------- ③ 函数值
FNVAL = """
vector<u64> compute(const vector<u64>& xs) { return vector<u64>::make(); }
u64 apply_id(const vector<u64>& xs, func(vector<u64>) -> u64 g) { return g(xs); }
u64 apply_nested(const vector<u64>& xs, func(vector<u64>) -> u64 g) {
    return g(compute(xs));
}
u64 host_id(const vector<u64>& xs) { return apply_id(xs, compute); }
u64 host_nested(const vector<u64>& xs) { return apply_nested(xs, compute); }
"""

FNVAL_TU = """
spec apply_id(const vector<u64>& xs, func(vector<u64>) -> u64 g) -> u64 { effects {}; }
spec apply_nested(const vector<u64>& xs, func(vector<u64>) -> u64 g) -> u64 { effects {}; }
"""


def test_fn_value():
    print("[4] 函数值调用 `g(x)`：具名函数实参 → 被调体**静态可求**，缺口 0")
    n = 16
    kc = kappa(FNVAL, FNVAL_TU, "compute", n)
    kh = kappa(FNVAL, FNVAL_TU, "host_id", n)
    khn = kappa(FNVAL, FNVAL_TU, "host_nested", n)
    oh = ops("host_id", [xs(n)], FNVAL, FNVAL_TU)
    ohn = ops("host_nested", [xs(n)], FNVAL, FNVAL_TU)
    delta = khn - kh
    check(delta == kc + 1,
          f"Δ = {delta:g} = κc {kc:g} + 1（函数值实参也进 κ，且只进一次）",
          f"κ(host_id)={kh:g} κ(host_nested)={khn:g}")
    # ④：调用点知道 `g = compute`（具名函数实参）⇒ 被调体**计入 κ**，
    # 不再是「缺口恰好等于 κc」。而**具名函数实参**解释器不求值 ⇒ 不虚加 1。
    check(kh == 6 + kc,
          f"κ(host_id) = 6 + κc = {6 + kc:g}（被调体 + 实参 xs 1，具名函数实参 0）",
          str(kh))
    check(oh <= kh, f"ops(host_id) {oh} ≤ κ(host_id) {kh:g}")
    check(ohn <= khn, f"ops(host_nested) {ohn} ≤ κ(host_nested) {khn:g}")
    check(oh - kh == 0,
          f"ops − κ = {oh - kh:g}：静态可求时**缺口 0**（修前是 κc {kc:g}）",
          f"ops={oh} κ={kh:g}")
    check(ohn - khn == 0,
          f"ops − κ = {ohn - khn:g}：换成 compute(xs) 后缺口仍为 0",
          f"ops={ohn} κ={khn:g}")
    check(ohn - oh == delta, f"ops 增量 {ohn - oh} = κ 增量 {delta:g}",
          f"{ohn - oh} vs {delta:g}")
    # **符号那一支**：直接分析 `apply_id` 本身（调用点不知道 g 是哪个函数）
    # ⇒ κ 里是 `k(g)`（不是 0），数值求值给 inf。工具必须能处理它。
    _ch, _decl = load(FNVAL, FNVAL_TU)
    k_apply = CostInterp(_ch, _decl).cost_of("apply_id")
    check("k(g)" in k_apply.render(),
          "κ(apply_id) 含符号项 k(g)（函数值形参的契约项）", k_apply.render())
    check(kappa_at(k_apply, {}) == float("inf"),
          "含符号项时 evaluate 返回 inf（工具应打印 inf 并跳过）",
          str(kappa_at(k_apply, {})))
    print()


# ---------------------------------------------------------------- ④ 对照组
#: 这些分支**修前就已经**计了实参：多一个 `compute(xs)`，κ 只许多 `κc + 1`，
#: 多出两倍就是「又加了一遍」。
#:
#: **A-full（2026-11-09）**：`compute_v` 原先写 `return xs;`（`const&` 形参）。
#: 值语义下那是**一次真实的容器拷贝**（设计 §3.2：借用值返回 = 拷贝），
#: κ 会**故意**大于解释器 ops（解释器不拷贝，实测是下界）——
#: 本组断言的目的是「实参没有被重复计费」，所以改成返回 prvalue，
#: 让 κ 与 ops 重新可比；"借用值返回要记拷贝"由 `test_cost.py` 的
#: `test_value_semantics` 单独钉住。
#:
#: **2026-11-10（D2）**：`g_std` 以前写 `sum_of(xs)`。现在 stdlib 的尺寸是
#: **按实参推导**的：`sum_of(compute_v(xs))` 的尺寸项 = `compute_v` 的返回尺寸
#: （`make()` → 常数 0），而 `sum_of(xs)` 是 `n(xs)` —— 两者的尺寸项不再相同，
#: `Δ` 会少一个 `n(xs)`（不是重复计费，是两边尺寸不同）。本组要隔离的是
#: 「多一个嵌套调用」的账，所以两边都用**同一形状**（空 vector）作实参。
#:
#: **注意本组断言测的是什么**：`Δ == κc + 1` 与 `ops 增量 == Δ` 测的是
#: **「实参没有被重复计费」**（多一个 `compute(xs)`，κ 只许多它的成本 + 1 个
#: 调用节点），**不是**「两边的尺寸项守恒」。后者在尺寸推导之后本来就不该成立
#: （空 vector 的尺寸就是 0），所以 fixture 才改成两边同形状。
GUARD = """
u64 compute(const vector<u64>& xs) { return 1; }
vector<u64> compute_v(const vector<u64>& xs) { return vector<u64>::make(); }
u64 g_std(const vector<u64>& xs) { return sum_of(vector<u64>::make()); }
u64 g_std_c(const vector<u64>& xs) { return sum_of(compute_v(xs)); }
u64 g_max(const vector<u64>& xs, u64 b) { return max(b, 1); }
u64 g_max_c(const vector<u64>& xs, u64 b) { return max(b, compute(xs)); }
u64 g_mc(const vector<u64>& xs) { return xs.at(1); }
u64 g_mc_c(const vector<u64>& xs) { return xs.at(compute(xs)); }
"""


def test_no_double_count():
    print("[5] 对照组：已经计过实参的分支，**不许再加一次**")
    n = 16
    # `known_gap`：**本文件不再允许任何缺口**。③ 修完之后 min/max 的内建派发
    # 那 1 步也进了 κ（`max(b,1)`：ops 5 = κ 5），所以 min/max 的 known_gap 从 1
    # 改成 0 —— 「缺口 ≤1，不许扩大」这条临时豁免**作废**。
    for a, b, cfn, label, known_gap in (
            ("g_std", "g_std_c", "compute_v", "stdlib", 0),
            ("g_max", "g_max_c", "compute", "min/max", 0),
            ("g_mc", "g_mc_c", "compute", "method-call", 0)):
        kc = kappa(GUARD, "", cfn, n)
        ka = kappa(GUARD, "", a, n)
        kb = kappa(GUARD, "", b, n)
        delta = kb - ka
        check(delta == kc + 1,
              f"{label}：Δ = {delta:g} = κc {kc:g} + 1（不是 2×）",
              f"κ({a})={ka:g} κ({b})={kb:g}")
        args_a = [xs(n), 7] if a.startswith("g_max") else [xs(n)]
        args_b = [xs(n), 7] if b.startswith("g_max") else [xs(n)]
        oa = ops(a, args_a, GUARD)
        ob = ops(b, args_b, GUARD)
        check(oa - ka <= known_gap and ob - kb <= known_gap,
              f"{label}：ops {oa}/{ob} ≤ κ {ka:g}/{kb:g}（缺口 0）",
              f"缺口 {oa - ka:g}/{ob - kb:g}")
        check(ob - oa == delta, f"{label}：ops 增量 {ob - oa} = κ 增量 {delta:g}")
    print()


# ---------------------------------------------------------------- ⑥ 调用点：用 κ 不用 target
#: 被调函数 `inner` **声明** `O(1)`（承诺），实际 κ = 16。调用点必须用 κ：
#: 修前调用方拿 target 记 1，`outer` 每轮只记 9 —— n=20 时 κ=184 < 实测 427。
#: 同一处缺口在 `_tier_b/sha256` 上是 14 倍（κ=21,566 vs ops=308,126，
#: `compress` 声明 O(1)、实际每块 ~18k）。
LOOSE = """
u64 inner(u64 n) {
    u64 a = n + 1;
    u64 b = a * 3;
    u64 c = b - 2;
    return c + n;
}
u64 outer(u64 n) {
    u64 s = 0;
    for (u64 i = 0; i < n; i = i + 1) { s = s + inner(i); }
    return s;
}
"""

LOOSE_TU = """
spec inner(u64 n) -> u64 {
    effects {};
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}
spec outer(u64 n) -> u64 {
    effects {};
    target { time: { max: O(n) }, allocation: { max: 0 }, };
}
"""

#: **只有 spec、没有实现**：target 是唯一信息 → 按声明记账，并登记
#: `ci.target_trusted`（被迫的信任，要能在报告里指出来）。
SPEC_ONLY = """
u64 caller(u64 n) { return 1 + ghost(n); }
"""

SPEC_ONLY_TU = """
spec ghost(u64 n) -> u64 {
    effects {};
    target { time: { max: O(n) }, allocation: { max: 0 }, };
}
"""

#: 被调函数的 κ 是 ⊤（`while (i != 0)` 推不出轮数）⇒ 调用方也必须是 ⊤。
TOP_CALLEE = """
u64 dark(u64 n) {
    u64 i = n;
    while (i != 0) { i = i - 1; }
    return i;
}
u64 caller_top(u64 n) { return 1 + dark(n); }
"""

TOP_CALLEE_TU = """
spec dark(u64 n) -> u64 {
    effects {};
    target { time: { max: O(n) }, allocation: { max: 0 }, };
}
spec caller_top(u64 n) -> u64 {
    effects {};
    target { time: { max: O(n) }, allocation: { max: 0 }, };
}
"""


def test_call_uses_kappa_not_target():
    print("[6] 调用点用被调函数的 **κ**，不用它的 target")
    n = 20
    ch, decl = load(LOOSE, LOOSE_TU)
    ci = CostInterp(ch, decl)
    ki = kappa_at(ci.cost_of("inner"), {"n": n})
    ko = kappa_at(ci.cost_of("outer"), {"n": n})
    check(ki == 16, "κ(inner) = 16（它的 target 只声明了 O(1)）", str(ki))
    check(ko >= n * ki,
          f"κ(outer) = {ko:g} ≥ n×κ(inner) = {n * ki:g}（不是 n×target = {n}）",
          f"κ(outer)={ko:g}")
    op = ops("outer", [n], LOOSE, LOOSE_TU)
    check(op <= ko,
          f"ops(outer) {op} ≤ κ(outer) {ko:g}（修前 κ=184 < ops=427）",
          f"ops={op} κ={ko:g}")

    # 没有实现 → 只能用声明的 target；这是被迫的信任，必须留下标记
    ch, decl = load(SPEC_ONLY, SPEC_ONLY_TU)
    ci = CostInterp(ch, decl)
    k = kappa_at(ci.cost_of("caller"), {"n": n})
    check(k == n + 5, f"κ(caller) = {k:g} = n + 5（按声明的 target O(n)）", str(k))
    check("ghost" in getattr(ci, "target_trusted", {}),
          "没有实现的被调函数登记在 ci.target_trusted（标注「按声明的 target」）",
          str(getattr(ci, "target_trusted", None)))

    # 被调函数的 κ 是 ⊤ ⇒ 调用方也是 ⊤（不许拿 target 把它救回来）
    ch, decl = load(TOP_CALLEE, TOP_CALLEE_TU)
    ci = CostInterp(ch, decl)
    check(ci.cost_of("dark").is_top(), "κ(dark) 是 ⊤（循环轮数推不出）")
    check(ci.cost_of("caller_top").is_top(),
          "κ(caller_top) 也是 ⊤（⊤ 通过调用点传播，target 不能兜底）",
          ci.cost_of("caller_top").render())
    print()


# ---------------------------------------------------------------- ⑦ 内建同名
#: **用户函数优先于同名内建**（与解释器 `_call` 同序，interp.py:3914-3919）。
#: 成本域以前把内建分支写在前头：`_diff/dedup` 的用户 `dedup` 被当成内建
#: `dedup` 记账，`main` 的 κ=107 而实测 ops=1005 —— **假上界**。
#: 这里钉两个方向：
#:   · 用户函数盖得住内建 → 按**用户实现**的 κ（常数）；
#:   · `print`/`swap` 是解释器在用户函数**之前**特判的 → 用户盖不住，
#:     成本域也必须按内建记（`print` 不产生 n(xs) 那一项）。
SHADOW = """
u64 sum_of(const vector<u64>& xs) {
    u64 s = 0;
    for (u64 i = 0; i < 5; i = i + 1) { s = s + xs.at(0); }
    return s;
}
u64 dedup(const vector<u64>& xs) {
    u64 s = 0;
    for (u64 i = 0; i < 5; i = i + 1) { s = s + xs.at(0); }
    return s;
}
u64 probe_sum(const vector<u64>& xs) { return sum_of(xs); }
u64 probe_dedup(const vector<u64>& xs) { return dedup(xs); }
"""


def test_builtin_shadowing():
    print("[7] 用户函数优先于同名内建（与解释器同序）")
    ci = CostInterp(parse_source(SHADOW, "shadow.eln").module, None)
    k_sum = ci.cost_of("sum_of")
    k_dedup = ci.cost_of("dedup")
    # 用户实现是常数（循环 5 次）：κ(probe) 只能是常数，不能出现内建的 n(xs)。
    for nm, callee, own in (("probe_sum", "sum_of", k_sum),
                            ("probe_dedup", "dedup", k_dedup)):
        k = ci.cost_of(nm)
        want = Cost.add(own, Cost.num(3))
        check(k.render() == want.render(),
              f"κ({nm}) = κ({callee}) + 3（按用户实现记账）",
              f"{k.render()} vs {want.render()}")
        check("n(xs)" not in k.render(),
              f"κ({nm}) 没有内建模型的 n(xs)", k.render())
    print()


def main() -> int:
    test_user_fn()
    test_user_fn_target()
    test_recursion()
    test_fn_value()
    test_no_double_count()
    test_call_uses_kappa_not_target()
    test_builtin_shadowing()

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
