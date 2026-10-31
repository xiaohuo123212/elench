#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""cost_check.py —— 静态闭环：把算出来的成本式与 spec 的 `target` 比。

**这一步是 §7.0.1 说的「静态负责契约」**：
    κ_impl（算出来的）  vs  κ_target（spec 里声明的）

## `O(...)` 是「至多常数倍」，不是「精确相等」

这一条必须先说清楚，否则结论全错：

    target { time: { max: O(n) } }

意思是 **∃c, N：∀n > N，κ_impl(n) ≤ c·n**。
如果按"精确 ≤"去比，那任何函数都超标——因为任何真实函数都有常数开销
（`9 + 3n` 在 n = 2 时是 15 > 2）。

所以判据是**比值是否有界**：

    r(n) = κ_impl(n) / κ_target(n)
    r 在 n 很大时**有界** → 达成
    r **随 n 增长**      → 超标（阶不够）

## 怎么判断"有界"

在 n = 2^8 … 2^40 上算 r，对 log2(r) 做最小二乘拟合：

    斜率 ≈ 0 → 有界（达成）
    斜率 > 0.05 → 每翻一倍 n，比值涨 3.5% 以上 → 阶不够（超标）

**为什么不用 SMT**：表达式里有 `log2` 与 `max`，SMT 要么编不出来要么很慢；
而我们只需要"阶够不够"这个判断，数值拟合足够，而且**错了也不会撒谎**——
斜率只说明"观察到的趋势"，不宣称"证明了"。

## 多尺寸变量

成本式里有多个尺寸时，本工具**把所有尺寸都设成同一个 n** 再比。
这是简化，结论要标注「按同规模假设」——真实的最坏组合可能需要人来判。
"""

import math
import sys
from typing import Dict, List, Optional, Tuple

from symbolic import Cost

# n 的梯子：2^8 .. 2^40。太小会被常数项淹没，太大没必要。
LADDER = [2 ** k for k in range(8, 41)]

# 斜率阈值：log2(比值) 对 k 的斜率。0.05 ≈ 每翻倍涨 3.5%。
SLOPE_TOL = 0.05


# ---------------------------------------------------------------- 求值

def evaluate(c: Optional[Cost], env: Dict[str, float], depth: int = 0) -> float:
    """在给定尺寸赋值下求出成本式的数值；⊤ 视为 +inf。"""
    if c is None:
        return 0.0
    if depth > 200:
        return float("inf")
    k = c.kind
    if k == "num":
        return float(c.value)
    if k == "size":
        return float(env.get(c.value, 1.0))
    if k == "top":
        return float("inf")
    if k == "add":
        return evaluate(c.args[0], env, depth + 1) + evaluate(c.args[1], env, depth + 1)
    if k == "mul":
        return evaluate(c.args[0], env, depth + 1) * evaluate(c.args[1], env, depth + 1)
    if k == "max":
        return max(evaluate(c.args[0], env, depth + 1), evaluate(c.args[1], env, depth + 1))
    if k == "log2":
        x = evaluate(c.args[0], env, depth + 1)
        if x <= 0 or math.isinf(x):
            return 0.0
        return math.log2(x)
    return float("inf")


def cost_vars(c: Optional[Cost]) -> List[str]:
    return [] if c is None else c.variables()


# ---------------------------------------------------------------- 结论

class Verdict:
    """一次比较的结论。"""

    def __init__(self, kind: str, note: str = "", slope=None,
                 ratio=None, sample_n=None, impl=None, target=None,
                 multi=False):
        self.kind = kind          # ok / debt / unknown
        self.note = note
        self.slope = slope
        self.ratio = ratio
        self.sample_n = sample_n
        self.impl = impl
        self.target = target
        self.multi = multi

    def render(self) -> str:
        if self.kind == "ok":
            r = f"，比值 ≈ {self.ratio:.2f}" if self.ratio is not None else ""
            m = "（按同规模假设）" if self.multi else ""
            return f"达成：阶够{r}{m}"
        if self.kind == "debt":
            r = f"，比值 ≈ {self.ratio:.2f}" if self.ratio is not None else ""
            s = f"，斜率 {self.slope:.3f}" if self.slope is not None else ""
            m = "（按同规模假设）" if self.multi else ""
            return f"**超标**：阶不够{r}{s}{m}"
        return f"无法判定：{self.note}"

    def __repr__(self) -> str:
        return f"Verdict({self.kind})"


# ---------------------------------------------------------------- 趋势

def _ratios(impl: Cost, target: Cost, var: str) -> List[float]:
    out = []
    for n in LADDER:
        a = evaluate(impl, {var: n})
        b = evaluate(target, {var: n})
        if b <= 0 or math.isinf(a) or math.isinf(b) or math.isnan(a) or math.isnan(b):
            return []
        out.append(a / b)
    return out


def _slope(ratios: List[float]) -> float:
    """log2(比值) 对 log2(n) 的斜率（LADDER 就是 2^k，所以 k 直接当 x）。"""
    if len(ratios) < 3:
        return 0.0
    xs = list(range(len(ratios)))
    ys = [math.log2(r) if r > 0 else -50.0 for r in ratios]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0


def compare_exact(impl: Optional[Cost], target: Optional[Cost]) -> Verdict:
    """**分配维度的比较**：常数之间按**数值**比，不按阶比。

    为什么不能复用 `compare`：`allocation: { max: 0 }` 是**数个数**，
    不是"只要常数就算达成"。`compare` 对两个常数一律判 ok（big-O 意义下都对），
    那会让"说 0 次分配、实际 1 次"被静默放过——**正是 E36 要消灭的那类洞**。

    规则：
      · 两个都是常数 → 数值比（impl ≤ target 才算达成）；
      · target 是常数、impl 是符号 → **unknown**（推不出上界就不撒谎）；
      · 其它（含 target 带符号）→ 退回 `compare`（按阶）。
    """
    if impl is None:
        return Verdict("unknown", note="没有算出分配上界")
    if target is None:
        return Verdict("unknown", note="spec 没有 allocation 的 target")
    if impl.is_top():
        return Verdict("unknown", note="分配上界是 ⊤（循环轮数推不出）")
    if impl.is_const() and target.is_const():
        if impl.value <= target.value:
            return Verdict("ok", ratio=(float(impl.value) / target.value) if target.value else None)
        return Verdict("debt", ratio=(float(impl.value) / target.value) if target.value else None,
                       note=f"分配 {impl.value} 次 > target 的 {target.value} 次")
    if target.is_const() and not impl.is_const():
        return Verdict("unknown",
                       note=f"分配上界含符号（{impl.render()}），推不出是否 ≤ {target.value}")
    return compare(impl, target)


def compare(impl: Optional[Cost], target: Optional[Cost]) -> Verdict:
    """把 κ_impl 与 κ_target 按 **big-O** 比。"""
    if impl is None:
        return Verdict("unknown", note="没有算出 κ_impl")
    if target is None:
        return Verdict("unknown", note="spec 没有 time 的 target")
    if impl.is_top():
        return Verdict("unknown", note="κ_impl 是 ⊤（有循环上界推不出）")
    if target.is_top():
        return Verdict("ok", note="target 是 ⊤，任何成本都满足")

    vs = sorted(set(cost_vars(impl)) | set(cost_vars(target)))
    multi = len(vs) > 1

    # **E23：κ 与 target 用的尺寸量不一致时，比比值没有意义。**
    # 真例子：`calculator` 的 κ 用局部 `tokens`/`operators`、target 用形参 `text`；
    #        `topk` 的 κ 用 `xs`/`k`、target 只有 `xs`（因为 target 里写不了两个尺寸相乘）。
    # 旧行为把所有尺寸设成同一个 n 再比 —— 那是在比两件不同的事，
    # 会给出天文数字（6.6e12 / 3.5e13）并**误报债务**。
    # 新行为：**不判债务，只报尺寸量不一致**；比值只作参考信息。
    ci, ct = set(cost_vars(impl)), set(cost_vars(target))
    # κ 不含任何尺寸量 ⇒ 它是**常数**，big-O 意义下任何阶的 target 都满足。
    # （E22 之后 `calculator.evaluate` 就是这种：定容容器把成本压成了常数。）
    if not ci:
        a = evaluate(impl, {})
        b = evaluate(target, {}) if ct else None
        return Verdict("ok", ratio=(a / b) if b else None,
                       note="κ 是常数（不含尺寸量），任何阶的 target 都满足")
    if ci != ct:
        rs0 = _ratios(impl, target, vs[0])
        extra = f"；若假设所有尺寸同阶，比值约 {rs0[-1]:.2f}（仅供参考，不作判定）" if rs0 else ""
        return Verdict("unknown", multi=multi,
                       ratio=(rs0[-1] if rs0 else None),
                       note=(f"κ 用的尺寸量 {sorted(ci)} 与 target 用的 {sorted(ct)} 不一致"
                             f"（E23）{extra}"))

    if not vs:
        # 双常量：big-O 意义下任何常数都是 O(1)，所以一律达成。
        # 比值只作信息展示——"target 定得松"由作者负责，语言不判（用户定的口径）。
        a = evaluate(impl, {})
        b = evaluate(target, {})
        return Verdict("ok", ratio=(a / b) if b else None)

    # 多尺寸：全部设成同一个 n（简化，结论要标注）
    var = vs[0]
    rs = _ratios(impl, target, var)
    if not rs:
        return Verdict("unknown", note="求值失败（可能有 ⊤ 或除零）", multi=multi)

    sl = _slope(rs)
    last = rs[-1]
    if sl > SLOPE_TOL:
        return Verdict("debt", slope=sl, ratio=last, multi=multi)
    return Verdict("ok", slope=sl, ratio=last, multi=multi)


def trend(impl: Optional[Cost], target: Optional[Cost]) -> str:
    v = compare(impl, target)
    if v.kind == "unknown":
        return "—"
    if v.slope is None:
        return "—"
    if v.slope > SLOPE_TOL:
        return f"升（每翻倍涨 {100 * (2 ** v.slope - 1):.1f}%）"
    if v.slope < -SLOPE_TOL:
        return "降（target 很松）"
    return "平（比值稳定）"


# ---------------------------------------------------------------- 自检

if __name__ == "__main__":
    from symbolic import Cost as C

    n = C.size("xs")
    cases = [
        # (标签, impl, target, 期望)
        ("n vs O(n)", n, n, "ok"),
        ("3n+9 vs O(n)", C.add(C.mul(C.num(3), n), C.num(9)), n, "ok"),
        ("n² vs O(n)", C.mul(n, n), n, "debt"),
        ("n vs O(log n)", n, C.log2(n), "debt"),
        ("log2 n vs O(n)", C.log2(n), n, "ok"),
        ("n log n vs O(n log n)",
         C.mul(n, C.log2(n)), C.mul(n, C.log2(n)), "ok"),
        ("n² vs O(n²)", C.mul(n, n), C.mul(n, n), "ok"),
        ("n² vs O(n log n)", C.mul(n, n), C.mul(n, C.log2(n)), "debt"),
        ("常数 100 vs O(1)", C.num(100), C.num(1), "ok"),
    ]
    bad = 0
    for label, a, b, want in cases:
        v = compare(a, b)
        mark = "✓" if v.kind == want else "✗"
        print(f"  {mark} {label:26} → {v.kind:6} 期望 {want:6} | {v.render()}")
        bad += 0 if v.kind == want else 1
    print(f"\n{'全部通过' if not bad else f'失败 {bad} 项'}")
    raise SystemExit(1 if bad else 0)
