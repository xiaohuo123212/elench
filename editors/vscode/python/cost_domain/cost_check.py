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

**首选结构判据**（2026-11-08）。κ 与 target 本来就是 `Cost` 表达式，
`kind`/`args` 就在手上：把它归约成「关于尺寸量 n 的多项式次数 × log 因子 ×
loglog 因子」，按字典序直接比（次数 → log → loglog）。`n log n` 的阶恒为
(1, 1, 0)，**与采样区间无关**——不再出现"换一组 n 判定就翻"。

**结构判不了 → unknown，不猜**（2026-11-10）。数值拟合（在 n = 2^8 … 2^40 上
算 r，再对 log2(r) 做最小二乘）**不再参与判定**：它的斜率依赖采样区间——
同一对成本式换一组 `LADDER`，`ok` / `debt` 就会翻转。拿它当结论等于把决策
交给采样区间，而「判不了」和「超标」是两条完全不同的信息（后者会让人去优化
一个可能本来就合格的实现）。所以 `_order_le` 返回 `None` 时**一律报
`unknown`**，并说清是哪一类：

  · **家族外**：形状明确超出 `n^d·log^l n·loglog^m n` 这个族（三层
    `log(loglog n)`、`log` 因子的非整数次幂、非常数分母 `n / m`、负指数、
    `ext(...)`、`k(f)`）——`_profile` 认得出「这不在族里」，只能 unknown；
  · **没实现的形状**：陌生节点——没有结构证据，同样 unknown；
  · **分段**：`piece` **能**判（各段阶的 max；任一段判不了才 unknown），
    所以不再出现在"没实现"这一类里；
  · **⊤**：上界本身未知，更不该猜；
  · **多个尺寸量**：由下面的「逐项支配」判据处理——**可证超出判 `debt`**，
    只有「不可比」才 `unknown`（见下节的三分法）；
  · **多个尺寸量 + log 因子**：由本文件末的「多维 profile」（次数 + **同维** log
    次数，2026-11-13）处理——(C)/(A) 两档仍判 ok / debt，其余才 `unknown`。

数值比值仍会算（`_ratios`），但只作为 `ok` / `debt` 的**参考**打印，**不决定结论**。
保留它的理由：用户仍然想知道"大概差多远"，但那个数不再冒充证据。

**`parallel` 的两条口径**（规格 §13.6 B-24）：`par` 节点判阶取 **max**、
数值取 **Σ**。所以 `_profile(par)` = 各分量 profile 的**字典序最大值**，
而 `evaluate(par)` = 各分量之和。两条口径**同时**成立是因为
`Σ O(f_i) = O(max_i f_i)`——判阶用 max 不是放松，是恒等式。

**为什么还留着 `_ratios` / `_slope`**：它们只用来给已判定的 `ok` / `debt`
印一个"参考比值"，以及给工具（`dispatch.py` / `cost_explain.py`）做排序用的
粗指标，**不再是判定通道**。判定永远先走结构判据；结构判据给不出答案时，
结论是 `unknown`。

**为什么不用 SMT**：表达式里有 `log2` 与 `max`，SMT 要么编不出来要么很慢；
而我们只需要"阶够不够"。结构判据能直接回答绝大多数形状；答不了的形状
（第三层 log、分段、外部项……）属于**判不了**，用数值斜率硬答一次只是
把"判不了"伪装成"判过了"——这正是 2026-11-10 修掉的洞。

## 多尺寸变量（2026-11-09 改）

**旧行为是假达成**：多尺寸时跳过结构判据、走数值拟合，而 `_ratios` 只把
字母序第一个尺寸量设成 n、**其余走 `env.get(..., 1.0)` 当成 1**。于是
`κ = n×m` 对 `target = n+m` 的比值恒定（57），二次实现能过线性契约——
「O(n×m)」与「O(n+m)」判不出区别。

新行为：

1. **结构判据逐尺寸量判**：把 κ 与 target 都展开成**单项式指数向量集合**
   （`n^1·m^2` → `(1,2)`），target 必须在**每一个尺寸量**上都支配 κ
   （κ 的每个单项式 e 都存在 target 的单项式 f 使 e ≤ f 逐分量成立）才判
   「达成」。达不成时**分三种**（2026-11-10 改——旧实现把后两种都算 unknown）：

   | 情形 | 判据 | 结论 |
   |---|---|---|
   | **(A) 可证超出** | ∃ κ 的单项式 e：**∀** target 的单项式 f，`e ≥ f`（逐分量）且 `e ≠ f`（至少一维严格大） | **debt** |
   | **(B) 不可比** | 有的维 κ 大、有的维 target 大（`κ = n·m` vs `target = n²+m`） | **unknown** |
   | **(C) 逐项支配** | ∀ e ∃ f：`e ≤ f` 逐分量 | **ok** |

   （(B) 的例子里 target **两个**尺寸量都得出现：只出现一个时更早的一步就按
   E23「尺寸量不一致」判 unknown 了——那是"两个名字是不是同一个量"的问题，
   不是次数问题。）

   **(A) 为什么是"证明"而不是猜**：e ≥ f 且 e ≠ f ⇒ `x^(e−f)` 的各维指数
   都 ≥ 0、至少一维 > 0，所以 `x^(e−f) = Π xᵢ^(eᵢ−fᵢ)` 随尺寸量增大**无界**。
   于是

       κ/target ≥ c_e·x^e / (Σ_f d_f·x^f) = c_e / (Σ_f d_f·x^(f−e)) → ∞

   ——**不存在**常数 C 使 `κ ≤ C·target`。注意这里**不要求**每一维都严格大：
   `e ≥ f` 是逐分量全不低，严格只出现在某一维（`κ=n·m` 对 `target=n`：m 那维
   1 > 0，n 那维打平，比值按 m 拉开）。反过来，(B) 恰好是"κ 的某一维大、
   另一维小"——`κ=n·m` 对 `target=n²`，`m ≫ n` 时 κ 大、`m ≪ n` 时 target 大，
   **结论真的取决于关系**，那才是"判不了"。

   旧实现的洞：只要不是"逐项支配"就一律 unknown 并写「两个尺寸量的关系未知」。
   于是 `κ = n×m` 对 `target = n + m`（**可证**超出：target 每一项都缺一维）
   和 `κ = n×m` 对 `target = n² + m`（**不可比**：`m ≫ n` 时 κ 大、`m ≪ n` 时
   target 大）拿到同一句话——用户无从知道自己该不该优化（2026-11-10 修）。
   `debt` 文案写明**哪个尺寸量高了几次**，`unknown` 文案写明**谁大取决于关系**。

2. **数值比值只作参考**：`_ratios` 把**所有**尺寸量都取同一个 n（同规模假设），
   输出标注「按同规模假设：所有尺寸量 = n；比值仅供参考」。**不同规模的
   比值不是证据**，`_ratios` / `_slope` 的结果**不再单独决定「达成」**。
3. 单尺寸量的判定**逐条不变**（回归红线：144 行 tightness / golden 22/22 /
   `kappa_vs_ops` 破界 0）。

## 多尺寸变量 + log 因子（2026-11-13）

**要解决的现场**：`02_csv_stats.count_fields` 的 κ 是

    κ = 3 + 2 + n(lines)×(… + L(lines) + L(lines)×(…) + log₂(n(lines)) + …) + …

**两个尺寸量 + log 因子**。改前无论 target 怎么写都判不了：单尺寸量的
`_profile` 一次只看一个尺寸量；多尺寸量的 `_monomials` 一遇 `log2` 就返回 None
⇒「含 log ⇒ 展不开 ⇒ unknown」，而少写一个尺寸量又退 E23。

**多维 profile**：一个单项式记成

    (e , l)     eᵢ = xᵢ 的多项式次数（Fraction）
                lᵢ = log₂(xᵢ) 的因子次数（int，可负）
        值 = Πᵢ xᵢ^eᵢ · (log₂ xᵢ)^lᵢ

例：`n × L² × log₂ n` → `e=(1,2)`、`l=(1,0)`；常数 → `e=l=0`。
比较仍走**三分法**，只是每一维多一个 log 次数：

    (C) ok    ∀ (e,l) ∈ κ  ∃ (f,m) ∈ target:  e ≤ f **且** l ≤ m（都逐分量）
    (A) debt  ∃ (e,l) ∈ κ  ∀ (f,m) ∈ target:  逐维「次数严格高」或「次数打平且
                                              log 次数不低」，至少一维严格
    其余      → unknown

**(C) 的证明**：`e ≤ f` 且 `l ≤ m` 时
`x^e·log^l x / (x^f·log^m x) = x^(e−f)·log^(l−m) x ≤ 1`（x ≥ 2，两个指数都非正），
所以 κ 的每个单项式都被 target 的某个单项式压住 ⇒ `κ ≤ C·target`。
**(A) 的证明**：沿"所有尺寸量一起变大"的方向每个因子都不衰减、至少一个到 ∞
（次数高一档 `x^δ` 盖过任意 log 的幂；次数打平时 `log^δ` 自己无界）。

**为什么 log 那一维不许被"次数更高"吸收**（与单尺寸量字典序的**唯一**区别，
也正是这条判据保守的地方）：单尺寸量时 `n log n ≺ n²` 靠字典序——同一个变量的
`x²` 确实压得住 `x·log x`。多尺寸量时每一维是**独立变量**：`xs·log₂(ys)` 对
`xs·ys` 的比是 `log ys / ys → 0`，数学上成立，但它要求先知道两个尺寸量的关系
（"ys 足够大"）——和「`n·m` 与 `n²` 不可比」是同一条理由，**没写进 `requires`
就不许假设**。所以这条判据是**充分不必要**的：说 ok 就真 ok、说 debt 就真 debt，
其余一律 unknown，并在文案里说清是"log 这一维没被压住"还是"次数上就不可比"。

**与纯多项式口径的关系**：`l` 全为 0 时 (C)/(A) 逐条退化成既有的
`_monomials` / `_multi_dominates` / `_multi_exceed` 规则——所以新路只在
`_multi_dominates` 返回 None（有一侧展不开）时才被调用，**既有判定一个字节都
不经过它**。

**家族边界**（仍是 None ⇒ unknown）：跨维的 `log(n·m)`（要先知道哪一维更大）、
`loglog`（要第二层 log）、底数带 log 的幂（`√(n log n)`）、分母带尺寸量
（`n/m`）、`⊤` / `ext` / `k(f)`、陌生节点。
"""

import math
import sys
from fractions import Fraction
from typing import Dict, List, Optional, Set, Tuple

from .symbolic import Cost, MAG_CAP, _same
from .logdom import has_symbolic_exp, to_log_with_reason

#: `log2(MAG_CAP)`：`pow` 求值时先在 log 空间比这个阈值，**不构造**大整数。
_LOG2_MAG = math.log2(MAG_CAP)

# n 的梯子：2^8 .. 2^40。太小会被常数项淹没，太大没必要。
LADDER = [2 ** k for k in range(8, 41)]

# 斜率阈值：log2(比值) 对 k 的斜率。0.05 ≈ 每翻倍涨 3.5%。
SLOPE_TOL = 0.05

#: 多尺寸量时的输出标注（2026-11-09）。
#: **必须写全"所有尺寸量 = n"**：旧实现只把第一个设成 n、其余当 1，
#: 提示语却写"按同规模假设"——那句话与行为不符，正是假达成的来源之一。
SCALE_NOTE = "（按同规模假设：所有尺寸量 = n；比值仅供参考，不作判定依据）"


# ---------------------------------------------------------------- 求值

def _split_size_diff(name: str):
    """`"hi - lo"` → `("hi", "lo")`；拆不开 → `None`。

    **只按顶层 ` - ` 拆**（括号里的不算），且必须恰好一处：`size_diff_name`
    拼出来的**差值尺寸量**就是这个形状（`hi - lo` / `n(xs) - lo` /
    `(n(xs) + hi) - lo`）。嵌套的 `a - b - c` 不收（口径不明，不猜）。
    """
    depth = 0
    cuts = []
    i = 0
    while i < len(name):
        ch = name[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and name.startswith(" - ", i):
            cuts.append(i)
            i += 3
            continue
        i += 1
    if len(cuts) != 1:
        return None
    return (name[:cuts[0]], name[cuts[0] + 3:])


def _operand_value(txt: str, env: Dict[str, float]) -> Optional[float]:
    """差值操作数的数值：`hi` / `n(xs)` / 字面量；查不到 → `None`。"""
    t = (txt or "").strip()
    while t.startswith("(") and t.endswith(")"):
        t = t[1:-1].strip()
    if not t:
        return None
    if t in env:
        return float(env[t])
    if t.startswith("n(") and t.endswith(")") and t[2:-1] in env:
        return float(env[t[2:-1]])
    try:
        return float(t)
    except ValueError:
        return None


def _pow_value(x: float, e: float) -> float:
    """**饱和**的幂求值：`x ** e`，超过 `MAG_CAP` 就返回它（"极大"标记）。

    为什么必须饱和（2026-11-12）：`n^n` 在 n=4096 时约 15,000 位十进制，
    `4096.0 ** 4096.0` 会 OverflowError、而先算整数更糟（真的构造那个大数）。
    这里**在 log 空间判大小**：`x^e ≤ MAG_CAP ⟺ e·log2(x) ≤ log2(MAG_CAP)`，
    超了直接给标记值，一步乘法都不多做。

    **只影响 `pow`**：`mul`/`add` 不做饱和——LADDER 上界是 2^40，而所有
    天文数字都只能从 `pow` 出来（这里已封顶）。这样既有 κ/target 的
    "参考比值"那一栏**逐字节不变**（它们的值本来就 ≤ 1e18 或早有 inf 口径）。

    边角（逐条）：`e = 0` → 1；`x = 0` → 0（成本恒非负，`0^0` 取 1 也行，
    这里跟随 `Cost.pow` 的 `x^0 = 1`）；`x = 1` → 1；`x` 或 `e` 是 inf
    （⊤ 参与）→ +inf（"未知"不许被当成有限数）。
    """
    if e == 0:
        return 1.0
    if x == 0:
        return 0.0
    if x == 1:
        return 1.0
    try:
        bits = e * math.log2(x) if x > 0 else float("inf")
    except (OverflowError, ValueError):
        return float("inf")
    if bits != bits or bits == float("inf"):
        return float("inf")
    if bits > _LOG2_MAG:
        return MAG_CAP
    try:
        return float(x ** e)
    except (OverflowError, ValueError):
        return MAG_CAP


def evaluate(c: Optional[Cost], env: Dict[str, float], depth: int = 0) -> float:
    """在给定尺寸赋值下求出成本式的数值；⊤ 视为 +inf。"""
    if c is None:
        return 0.0
    if depth > 200:
        return float("inf")
    k = c.kind
    if k == "num":
        try:
            return float(c.value)
        except (OverflowError, ValueError):
            # 折叠守卫（`Cost.pow`）之后不该有巨大整数常数；万一有人直接
            # `Cost.num(10**400)`，也**不崩**——按"极大"标记算。
            return MAG_CAP
    if k == "size":
        return float(env.get(c.value, 1.0))
    if k == "sym":
        # `k(visit)`：**给不出值就是 ⊤**（不许按 1 算——那会低估上界）
        v = str(c.value)
        got = env.get(v)
        if got is not None:
            return float(got)
        # **差值尺寸量**（`hi - lo`，见 `cost_interp.size_diff_name`）：它是
        # 派生出来的尺寸，两个操作数才是环境里的量。不认这一步，任何带
        # `hi - lo` 的 κ 都求成 +∞（等于 ⊤），区间二分那类函数就永远测不了。
        # 操作数查不到 → 仍然 +∞（不猜）。
        _d = _split_size_diff(v)
        if _d is not None:
            _a = _operand_value(_d[0], env)
            _b = _operand_value(_d[1], env)
            if _a is not None and _b is not None:
                return _a - _b
        return float("inf")
    if k == "top":
        return float("inf")
    if k == "add":
        return evaluate(c.args[0], env, depth + 1) + evaluate(c.args[1], env, depth + 1)
    if k == "mul":
        return evaluate(c.args[0], env, depth + 1) * evaluate(c.args[1], env, depth + 1)
    if k == "div":
        # **除零 → +∞，不崩**。除法的值域本来就含无穷（`n / log(1)`），
        # 所以这里不必区分"分母是 0"与"分母是 ⊤"——两者都只能给 inf。
        # **分母为负也退 inf**：成本式不该出现负数，真出现了宁可说"上界未知"，
        # 也不给一个凭空的有限数。
        a = evaluate(c.args[0], env, depth + 1)
        b = evaluate(c.args[1], env, depth + 1)
        if b <= 0 or math.isnan(b) or math.isnan(a):
            return float("inf")
        return a / b
    if k == "max":
        return max(evaluate(c.args[0], env, depth + 1), evaluate(c.args[1], env, depth + 1))
    if k == "par":
        # **数值口径 = Σ**（B-24 的"工作量"）。**判阶不走这里**——判阶看
        # `_profile` 的 max 口径。两条口径分开，是因为 `parallel` 的承诺本来
        # 就是两条（规格 §13.6 B-24）：工作量 Σ、阶 max。
        #
        # Σ 仍是**上界**：`parallel` 是顺序语义（v0.71），真实操作数就是这个和；
        # 墙钟（依赖核数与权重的那个数）**不在** κ 里，见 `par.wall_estimate`。
        return sum(evaluate(t, env, depth + 1) for t in c.args)
    if k == "piece":
        # **分段**：先看 n 落在哪一段，再用那一段的式子。
        # 越界用 `outside`——那正是运行期"表覆盖不到、退回第一个候选"的行为，
        # 所以它仍然是一个成立的上界（而不是像旧实现那样悄悄钉一个常数）。
        var, segs = c.value
        n = float(env.get(var, 1.0))
        for lo, hi, sub in segs:
            if lo <= n <= hi:
                return evaluate(sub, env, depth + 1)
        return evaluate(c.args[0], env, depth + 1)
    if k == "log2":
        x = evaluate(c.args[0], env, depth + 1)
        if x <= 0 or math.isinf(x):
            return 0.0
        return math.log2(x)
    if k == "pow":
        # **有理次幂 / 符号指数**：`sqrt(n)`、`n^(2/3)`、`n^n`。
        # 常数指数的形状是 `value=Fraction`，符号指数是 `value=None` +
        # `args[1]`（2026-11-12）。**求值都走 `_pow_value`（饱和）**：
        # `n^n` 在 n=4096 时是 15,000 位数，**不许构造它**。
        x = evaluate(c.args[0], env, depth + 1)
        if c.value is None:
            e = evaluate(c.args[1], env, depth + 1)
        else:
            e = float(c.value if isinstance(c.value, Fraction) else Fraction(c.value))
        if x < 0 or e < 0:
            return float("inf")          # 成本不该为负：宁可 ⊤，不造假数
        return _pow_value(x, e)
    return float("inf")


def cost_vars(c: Optional[Cost]) -> List[str]:
    return [] if c is None else c.variables()


# ---------------------------------------------------------------- 结论

class Verdict:
    """一次比较的结论。

    `detail`（2026-11-10）是**只给 `debt` 用**的「为什么超」正文：`note` 在
    `debt` 下从来不打印（见 `render`），而多尺寸量的「可证超出」**必须**把
    「哪个尺寸量高了几次」说出来——否则又回到"知道超了、不知道超在哪"。
    为 None 时渲染逐字节不变（既有输出不受影响）。
    """

    def __init__(self, kind: str, note: str = "", slope=None,
                 ratio=None, sample_n=None, impl=None, target=None,
                 multi=False, detail: Optional[str] = None):
        self.kind = kind          # ok / debt / unknown
        self.note = note
        self.slope = slope
        self.ratio = ratio
        self.sample_n = sample_n
        self.impl = impl
        self.target = target
        self.multi = multi
        self.detail = detail

    def render(self) -> str:
        if self.kind == "ok":
            r = f"，比值 ≈ {self.ratio:.2f}" if self.ratio is not None else ""
            m = SCALE_NOTE if self.multi else ""
            return f"达成：阶够{r}{m}"
        if self.kind == "debt":
            r = f"，比值 ≈ {self.ratio:.2f}" if self.ratio is not None else ""
            s = f"，斜率 {self.slope:.3f}" if self.slope is not None else ""
            m = SCALE_NOTE if self.multi else ""
            if self.detail:
                # **证明文案**自带"为什么"，比"阶不够"有用得多。参考比值照旧
                # 印出来（标注同规模假设、不作判定依据），否则读者只有结论。
                _rr = (f"；参考比值 ≈ {_ratio_text(self.ratio)}"
                       f"（按同规模假设：所有尺寸量 = n，不作判定依据）"
                       if self.ratio is not None else "")
                return f"**超标**：{self.detail}{_rr}"
            return f"**超标**：阶不够{r}{s}{m}"
        return f"无法判定：{self.note}"

    def __repr__(self) -> str:
        return f"Verdict({self.kind})"


# ---------------------------------------------------------------- 趋势

def _ratios(impl: Cost, target: Cost, vars) -> List[float]:
    """**同规模假设下**的比值序列：所有尺寸量都取同一个 n。

    `vars` 可以是单个名字（单尺寸）或名字列表（多尺寸）。**每一个**都设成
    同一个 n——这里不许再出现 `env.get(..., 1.0)` 那种"第二个尺寸当常数 1"
    的读法：它会让 `κ = n×m` 对 `target = n+m` 的比值恒定 57，二次实现
    直接过线性契约（2026-11-09 实测复现）。

    **它只是参考值**：比值有界不等于阶够（见 `compare` 的多尺寸分支），
    调用方 MUST NOT 拿它单独判「达成」。
    """
    if isinstance(vars, str):
        names = [vars]
    else:
        names = list(vars)
    out = []
    for n in LADDER:
        env = {v: float(n) for v in names}
        a = evaluate(impl, env)
        b = evaluate(target, env)
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


# ---------------------------------------------------------------- 结构判据

# 成本式是表达式树，阶可以**直接读**：
#
#     （多项式次数 d，log 因子数 l，loglog 因子数 m）
#         ⇔  阶为 n^d · (log n)^l · (loglog n)^m
#
# 比较按**字典序**：先比 d，d 相同再比 l，l 相同再比 m。d 小者阶低；完全相等
# 算「阶相等」（big-O 下达成）。
#
# **`d` 是 `Fraction`**（2026-11-09）：`sqrt(n)` 的 1/2、`n^(2/3)` 的 2/3 都
# 直接进这个字典序，不用另开一套比较。
# **`l` 可以为负**（2026-11-09）：`n / log n` 记 `(1, -1, 0)`，字典序照用——
# 同次数下 `-1 < 0`，所以 `n / log n ≺ n`。`d` 仍恒非负（见 `div` 的边界表）。
# **把 `m` 放在最后**（2026-11-09 的 loglog 一步）：`loglog n = (0,0,1)` 与
# `log n = (0,1,0)` 的第一、二位都相同，第三位 `0 < 1` 让 `loglog n ≺ log n`
# **自动成立**——不需要为"loglog 比 log 小"单写一条规则。数学上也正好：
# `loglog n / log n → 0`，而任何固定的 `loglog^k n` 都还是 `≺ log n`。
# 常数因子（`2n` 的 2、`n×7 + 9` 的 7/9）不参与——
# `add`/`max` 只留支配项，所以 `2 + n×7 + 2` 就是 `n`。

def _profile(c: Optional[Cost], var: str) -> Optional[Tuple[Fraction, int, int]]:
    """把成本式归约成 `(多项式次数, log 因子数, loglog 因子数)`；判不了返回 None。

    次数是 **`Fraction`**（2026-11-09 起，不再只是整数）：`sqrt(n)` 记
    `(1/2, 0, 0)`、`n^(2/3)` 记 `(2/3, 0, 0)`、`n^(3/2)` 记 `(3/2, 0, 0)`。
    这样 `√n ≺ n`、`n^(2/3) ≺ n` 是**直接比出来的**，而不是靠数值斜率猜。

    **loglog 用的是 `log2` 的嵌套**（`Cost.loglog(a)` = `Cost.log2(Cost.log2(a))`，
    2026-11-09）：不新增节点、不改渲染（印成 `log2(log2(n(xs)))`）、
    `evaluate` 本来就会递归进 `args`，所以"能打印、能求值"两条同时满足。
    代价是 `_profile` 必须**认识这一层嵌套**——见下面 `log2` 的规则。

    规则（逐条）：

      num                     → (0, 0, 0)  常数不含尺寸
      size(var) / sym(var)    → (1, 0, 0)  线性；`sym` 是标量形参（`dim` / `hi`）
      log2(sub)               → 见下面的**三档**（不抬多项式次数）
      pow(base, e)            → (d·e, 0, 0) 底数必须是**纯多项式**
                                          （log 与 loglog 因子都为 0）；`e` 非负
      mul(a, b)               → 三个分量**各自相加**（分数照样相加）
      div(a, b)               → **只认"分母是 log/loglog 的幂或常数"**；其余 None
      add(a, b) / max(a, b)   → 取支配项（字典序最大者）
      par(a, b, …)            → **取各分量里最大的那个**（B-24：判阶 = max_i 阶）
      piece{段: κᵢ}           → **取各段（含越界回退）里最大的那个**；任一段
                                判不了 → None（与 par 同一口径，见下面的 `piece` 段）
      top / ext / k(f) / 未知 kind / 别的尺寸量
                              → None（判不了）

    **`log2` 的三档**（第三档是这一步新加的）：

    | 子式 profile | `log2(sub)` | 例 |
    |---|---|---|
    | `(0, 0, 0)` 常数 | `(0, 0, 0)` | `log2(8)` → `3`（构造时就折成常数） |
    | `d > 0` | `(0, 1, 0)` | `log2(n log n)` = `log n + loglog n` → **仍是 log n** |
    | `d = 0, l > 0` | `(0, 0, 1)` | `log2(log n)` → **loglog n** |
    | 其余（`d=0,l≤0` 的非纯常数） | **None** | 三层 `loglog(loglog n)`、`log(1/log n)` |

    **`log(n log n)` 的化简**（第二档）：`log(n log n) = log n + loglog n`
    = **Θ(log n)**——低阶的 loglog 项不改变阶，所以记 `(0,1,0)` 是**精确的阶**，
    不是"丢一项"（上界 `log(n log n) ≤ 2 log n` 对 `n ≥ 2` 成立）。同理
    `log(n²)`、`log(√n)`、`log(n / log n)` 都归到 `log n`。这条**只用了
    "log n 是最高阶的 log 项"这一个事实**，与 `d>0` 的分支天然一致。

    **三层 log 为什么退 None**（不折算到 loglog）：`loglog(loglog n)`
    = `log logloglog n`，是家族的**下一个**分量。今天为它加一层，明天
    `loglog(loglog(loglog n))` 又要加——家族会无限延伸，而判阶的字典序
    每加一位就多一条边界规则。**家族到此为止**：认出三层就说明"这份
    target 超出了本判据能表达的范围"，返回 None（调用方按既有规则退数值
    拟合 / ⊤），而不是把它近似成 loglog——那会给出一个**比真实阶更松**
    的承诺（`logloglog n ≺ loglog n`，近似成 loglog 等于把目标放宽一档）。

    **`pow` 为什么只在"纯多项式底"上给次数**：`(n^d·log^l n·loglog^m n)^e`
    的 log/loglog 因子是它们的 e 次方——那已经不在「整数 log 因子数 +
    整数 loglog 因子数」这个家族里（`√(n log n) = n^(1/2)·log^(1/2) n`）。
    **家族外一律 None**，不把 `log^(1/2)` 近似成 `log` 或丢掉（那两边都会
    给出一个假阶）。底数是常数（`(0,0,0)`）时 `d·e = 0`，仍是常数。

    **`div` 的四条边界**（2026-11-09，log / loglog 因子因此**可以为负**）：

    | 分母 | 归约 | 例 |
    |---|---|---|
    | `(0, l, m)`，`l,m ≥ 0` 且非全 0 | `(pa.d, pa.l-l, pa.m-m)` | `n/log n → (1,-1,0)`；`n/loglog n → (1,0,-1)` |
    | 常数（`(0,0,0)`、值 > 0） | `pa` | `n/2 → (1,0,0)`；`n/(3×4) → (1,0,0)` |
    | 分母带正次数（`n/m`） | **`None`（退 ⊤）** | 两变量关系没写进 requires，**不许猜** |
    | `⊤` / 认不出 / 常数 ≤ 0 / 分母有**负** log 因子 | **`None`** | `n/(1/log n)`；`div(x,0)` 求值是 inf |

    负 log 因子让字典序多出一档：`n/log²n ≺ n/log n ≺ n ≺ n log n`——
    同次数时 `-2 < -1 < 0 < 1`，**不需要另写比较规则**。
    loglog 因子同理：`n/loglog n ≺ n ≺ n loglog n`（第三位 `-1 < 0 < 1`）。
    次数不同时次数说话：`n / log^99 n` 仍 `≻ √n`，因为 `1 > 1/2`
    （任何 log 的幂都压不过一个正的次数差）。这条是"负因子能不能塞进
    这个字典序"的**判据**：能，只要次数仍非负。

    **判不了的边界**：`⊤` 说明上界本身未知；`ext` 是外部项、`k(f)` 是
    函数参数的成本（多大看调用点）。

    **`piece` 为什么能判阶**（2026-11-10）：`piece{段1: κ₁, 段2: κ₂, …}`
    是分段函数——`n` 落在哪一段就**只有那一段的式子**，所以

        order(piece) = max_i order(κᵢ)      （字典序最大，与 `par` 同一口径）

    这正是"上界对每个 n 都成立 ⇔ 每段都成立"的判阶版：只要每段的阶都
    ≤ target 的阶，整个分段就 ≤ target；**有一段超了就是债务**。段条件里的
    尺寸量（`n < 1024` 之类）只决定"用哪一段"，不改变"每段各自要比"这件事，
    所以取 max 是 sound 的。越界回退（`c.args[0]`）也是可能被取到的值，
    一并进 max——漏了它就会在声明区间外给一个**不是上界**的阶。

    **任一段判不了 → 整体 None**（保守）：不能跳过那一段只取已知段的 max，
    那个未知段可能才是最大的（低估）。这跟 `par` 的"一个分量判不了 →
    整块判不了"是同一条理由，不是偷懒。
    """
    if c is None:
        return None
    k = c.kind
    if k == "num":
        return (Fraction(0), 0, 0)
    if k == "size":
        return (Fraction(1), 0, 0) if c.value == var else None
    if k == "sym":
        _v = str(c.value or "")
        if _v.startswith("k("):
            return None              # 函数参数的成本：多大看调用点
        return (Fraction(1), 0, 0) if _v == var else None
    if k in ("log2", "log"):
        sub = _profile(c.args[0], var)
        if sub is None:
            return None
        d, l, m = sub
        if d == 0 and l == 0 and m == 0:
            return (Fraction(0), 0, 0)   # log(常数) 仍是常数
        if d > 0:
            # `log(n^d · log^l n · loglog^m n) = d·log n + 低阶项`
            # → Θ(log n)。`log(n log n) = log n + loglog n` 就落在这里。
            return (Fraction(0), 1, 0)
        if l > 0:
            # `log(log^l n · loglog^m n) = l·loglog n + 低阶项`
            # → Θ(loglog n)。`log(log n)` 就落在这里。
            return (Fraction(0), 0, 1)
        # `d=0,l≤0` 且不是纯常数：三层 log（`log(loglog n)`）、
        # `log(1/log n)` 这类**家族外**的形状。**不猜**。
        return None
    if k == "pow":
        if c.value is None:
            # **符号指数**（`n^n`、`2^n`）：`n^(d·e)` 里的 d 不是次数而是一个
            # **随尺寸增长的式**，`(次数, log, loglog)` 三元组装不下它。
            # 它不在这个家族里——判阶走 `cost_domain/logdom.py` 的 log 域
            # （`log(n^n) = n·log n`）；这里返回 None 是**如实说"这一层判不了"**，
            # 不是把 n^n 当成常数（那是假达成）。
            return None
        sub = _profile(c.args[0], var)
        if sub is None:
            return None
        d, l, m = sub
        if l != 0 or m != 0:
            # 底数带 log/loglog 因子 → 结果不在 (n^d·log^l·loglog^m) 家族里，不猜。
            return None
        e = c.value if isinstance(c.value, Fraction) else Fraction(c.value)
        if e < 0:
            return None              # 负指数不是成本上界（构造函数本应挡住）
        return (d * e, 0, 0)
    if k == "mul":
        a = _profile(c.args[0], var)
        b = _profile(c.args[1], var)
        if a is None or b is None:
            return None
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
    if k == "div":
        # **除法只认这些分母**（见 docstring 的表）。这是"负因子"的唯一来源，
        # 也是本判据里**最容易把上界猜低**的一处，所以边界写死：
        #
        #   分母 = log/loglog 的幂 (0,l,m)，l,m≥0 且非全 0
        #                           → (d, l-l', m-m')
        #   分母 = 常数 (0,0,0)      → (d, l, m)
        #   分母带正次数 / 负 log 因子 / ⊤ / 认不出 → None（退 ⊤，**不猜**）
        #
        # **为什么不给 `n / m` 一个数**：`n / m` 的阶是 `1 - order(m)`——
        # 而 `m` 与 `n` 的关系（同规模？常数？`√n`？）**没写进 requires**
        # 就不知道。按 1 算会高估、按 0 算会低估，两种都是撒谎。
        pa = _profile(c.args[0], var)
        pb = _profile(c.args[1], var)
        if pa is None or pb is None:
            return None                     # 分子/分母认不出 → ⊤
        if pb[0] == 0 and pb[1] == 0 and pb[2] == 0:
            # 分母是常数：除以正常数不改量级。**但 0 与负数不行**——
            # `div(x, 0)` 是 +∞，`div(x, -2)` 会翻符号，两者都不是"阶"。
            cv = c.args[1].const_value()
            if cv is not None and cv <= 0:
                return None
            return pa
        if pb[0] == 0 and pb[1] >= 0 and pb[2] >= 0 and (pb[1] or pb[2]):
            # 分母是 log / loglog 的幂（或二者的乘积）→ 对应因子变负。
            return (pa[0], pa[1] - pb[1], pa[2] - pb[2])
        return None                         # 分母带正次数 / 负因子 / 认不出 → 不猜
    if k in ("add", "max"):
        a = _profile(c.args[0], var)
        b = _profile(c.args[1], var)
        if a is None or b is None:
            return None
        return a if a >= b else b    # 支配项（三元组的字典序比较）
    if k == "par":
        # **B-24 的判阶口径**：`order(par) = max_i order(κ_i)`。
        # 这不是"放松"：`Σ` 的阶本来就等于 `max` 的阶（`Σ O(f_i) = O(max f_i)`），
        # 所以判出来的结论与"先求和再取阶"**逐条相同**；变的是**报告**能直接说
        # "瓶颈是哪个任务"，而不再只能靠恒等式间接成立。
        #
        # **一个分量判不了 → 整块判不了**（返回 None → 调用方报 unknown，不猜）。
        # 不能跳过它只取已知分量的 max：那个未知分量可能才是最大的（低估）。
        subs = [_profile(t, var) for t in c.args]
        if any(s is None for s in subs):
            return None
        return max(subs) if subs else (Fraction(0), 0, 0)
    if k == "piece":
        # **分段**：`n` 落在哪一段，那段就是全部成本 → 判阶 = 各段阶的 max。
        # 见 docstring 的「`piece` 为什么能判阶」。三件事写死在这里：
        #
        #   ① **取字典序最大者**（`max(subs)`），不是逐分量最大值——与 `par`
        #      同一条口径。分量之间不是独立的：`(2, -1, 0)`（n²/log n）与
        #      `(1, 5, 0)`（n·log⁵n）里前者更大，逐分量 max 会造出 `(2, 5, 0)`
        #      这个**没有任何一段实际达到**的阶（松，且会给出假债务）。
        #   ② **越界回退也进 max**：`args[0]` 是 `n` 超出声明区间时的式子，
        #      它同样是这段成本的一个可能取值；漏掉它，声明区间外的阶就
        #      不是上界了（`Cost.piece` 的 `render` 在缺哨兵时会印"越界:"）。
        #   ③ **任一段判不了 → None**：未知的那段可能才是最大的，跳过 = 低估。
        val = c.value
        if not (isinstance(val, tuple) and len(val) == 2):
            return None                  # 形状坏了：不猜
        _var, segs = val
        subs = [_profile(seg, var) for _lo, _hi, seg in segs]
        if c.args:
            subs.append(_profile(c.args[0], var))
        if any(s is None for s in subs):
            return None
        return max(subs) if subs else (Fraction(0), 0, 0)
    return None                       # top / ext / 陌生 kind


def order_profile(c: Optional[Cost], var: str) -> Optional[Tuple[Fraction, int, int]]:
    """**公开的判阶口径**：`(多项式次数, log 因子数, loglog 因子数)`；判不了返回 `None`。

    次数是 `Fraction`（`sqrt(n)` → `1/2`）。就是 `_profile` 的别名——
    `parallel` 的视图（`par.py`）必须与 `compare` 用**同一份**判阶逻辑，
    否则会出现"报告说 n、判定说 n²"的自相矛盾。
    """
    return _profile(c, var)


def _order_le(impl: Optional[Cost], target: Optional[Cost],
              var: str) -> Optional[bool]:
    """κ 的阶是否 ≤ target 的阶（结构判据）。

    True  = 阶不高于 target（达成）；
    False = 超阶（债务）；
    None  = 结构判不了（家族外 / ⊤ / ext / k(f) / 陌生节点 / 分段里有认不出的段）
            → 调用方报 **unknown**（2026-11-10 起**不再**退回数值拟合）。
    """
    a = _profile(impl, var)
    b = _profile(target, var)
    if a is None or b is None:
        return None
    return a <= b


def _profile_gap(c: Optional[Cost], var: str, depth: int = 0):
    """为什么 `_profile(c, var)` 是 None：`(类别, 人读原因)`；能判阶则 None。

    这个分类**只决定 unknown 的说明文字，不决定结论**——四类都是 unknown：

      "family"         —— 形状明确在 `n^d·log^l·loglog^m` 族外
                          （三层 log / log 的非整数次幂 / `n/m` / 负指数 /
                          `ext(...)` / `k(f)`）。**认得出"不在族里"**。
      "unimplemented"  —— 陌生节点 / 坏掉的节点：本判据还没有对应的结构规则。
                          （`piece` 2026-11-10 起**不再**属于这一类：它逐段归约。）
      "top"            —— ⊤：上界本身未知。
      "multi"          —— 另有尺寸量（正常走不到：多尺寸量分支先处理）。

    规则必须与 `_profile` **逐条镜像**：`_profile` 在哪儿返回 None，
    这里就在同一处给出原因；否则会出现"判不了但说不出为什么"。
    """
    if c is None:
        return ("top", "没有成本式")
    if depth > 200:
        return ("unimplemented", "表达式嵌套过深")
    k = c.kind
    if k == "num":
        return None
    if k in ("size", "sym"):
        name = str(c.value or "")
        if k == "sym" and name.startswith("k("):
            return ("family", f"函数参数的成本 {name}（多大看调用点）")
        if name != var:
            return ("multi", f"另有尺寸量 {name}")
        return None
    if k == "top":
        return ("top", " ⊤（循环上界推不出）")
    if k == "ext":
        return ("family", f" 外部项 ext({c.value})")
    if k == "piece":
        # **逐段镜像** `_profile`：整段判不了 ⇔ 至少一段（或越界回退）判不了。
        # 原因按"最能解释"的报：**任一段是家族外就报 family**（那是具体的形状
        # 问题，比"陌生节点"有用），否则报第一个非 None 的原因。
        # `piece` 本身**不是** unimplemented——它的规则在 `_profile` 里。
        val = c.value
        if not (isinstance(val, tuple) and len(val) == 2):
            return ("unimplemented", " 分段节点的段表坏了")
        _var, segs = val
        gaps = [_profile_gap(seg, var, depth + 1) for _lo, _hi, seg in segs]
        if c.args:
            gaps.append(_profile_gap(c.args[0], var, depth + 1))
        for g in gaps:
            if g is not None and g[0] == "family":
                return g
        for g in gaps:
            if g is not None:
                return g
        return None
    if k in ("log2", "log"):
        sub = _profile(c.args[0], var)
        if sub is None:
            return (_profile_gap(c.args[0], var, depth + 1)
                    or ("family", " log 的子式判不了阶"))
        d, l, m = sub
        if d == 0 and l <= 0 and (l != 0 or m != 0):
            if l == 0 and m > 0:
                return ("family", " 三层 log（log(loglog n)）")
            return ("family", " log(1/log n) 这类 log 因子的非正次数")
        return None
    if k == "pow":
        if c.value is None:
            return ("family", " 符号指数（`n^n` 这类）：三元组装不下它"
                              "（判阶走 log 域）")
        sub = _profile(c.args[0], var)
        if sub is None:
            return (_profile_gap(c.args[0], var, depth + 1)
                    or ("family", " 幂的底数判不了阶"))
        d, l, m = sub
        if l != 0 or m != 0:
            return ("family", " 底数带 log/loglog 因子取幂（√(n log n)）")
        e = c.value if isinstance(c.value, Fraction) else Fraction(c.value)
        if e < 0:
            return ("family", " 负指数不是成本上界")
        return None
    if k in ("mul", "add", "max", "par"):
        for a in c.args:
            g = _profile_gap(a, var, depth + 1)
            if g is not None:
                return g
        return None
    if k == "div":
        if len(c.args) < 2:
            return ("unimplemented", " 除法节点缺分母")
        pa = _profile(c.args[0], var)
        pb = _profile(c.args[1], var)
        if pa is None:
            return (_profile_gap(c.args[0], var, depth + 1)
                    or ("family", " 除法的分子判不了阶"))
        if pb is None:
            return (_profile_gap(c.args[1], var, depth + 1)
                    or ("family", " 除法的分母判不了阶"))
        if pb[0] == 0 and pb[1] == 0 and pb[2] == 0:
            cv = c.args[1].const_value()
            if cv is not None and cv <= 0:
                return ("family", " 分母是常数 ≤ 0（求值是 +∞ 或翻符号）")
            return None
        if pb[0] == 0 and pb[1] >= 0 and pb[2] >= 0 and (pb[1] or pb[2]):
            return None
        return ("family", " 分母带尺寸量（n/m）或负 log 因子")
    return ("unimplemented", f" 陌生的成本节点 {k!r}")


def _structural_unknown(impl: Optional[Cost], target: Optional[Cost],
                        var: str) -> "Verdict":
    """`_order_le` 返回 None 时的**唯一**结论：unknown，并说清是哪一类 None。

    **为什么不再退回数值拟合**：`_slope(_ratios(...))` 的符号依赖 `LADDER`
    （2^8…2^40）。同一对成本式换一组采样区间就会从 `ok` 翻成 `debt`——
    2026-11-09 的 `n log n` vs `O(n)` 就是这么翻的。把这种数当结论，
    等于把"判不了"藏起来、让用户去优化一个可能本来就合格的实现。
    """
    gi = _profile_gap(impl, var)
    gt = _profile_gap(target, var)
    # **先报 target 侧**：用户改 target 就能恢复可判定性（κ 是算出来的，不该为他改）。
    side, gap = ("target", gt)
    if gap is None or gap[0] == "multi":
        side, gap = ("κ", gi)
    if gap is None:
        return Verdict("unknown",
                       note="结构判据判不了阶；不退回数值拟合"
                            "（拟合结论会随采样区间翻转）")
    tag, why = gap
    if tag == "family":
        head = f"阶表达式超出可判定的族（{side}{why}）"
    elif tag == "unimplemented":
        head = f"结构判据还没实现这种形状（{side}{why}）"
    elif tag == "top":
        head = f"上界未知（{side}{why}）"
    else:
        head = f"结构判据判不了（{side}{why}）"
    return Verdict(
        "unknown",
        note=head + "；**不用数值拟合给结论**——拟合斜率随采样区间（LADDER）变，"
                    "同一对式子会从 ok 翻成 debt")



# ---------------------------------------------------------------- 多尺寸量：逐项支配
#
# **为什么单靠 `_profile` 不够**：它一次只看**一个**尺寸量，另一个只能当
# "判不了"（None）或当 1。`κ = n×m` 与 `target = n+m` 在"n=m"的读数下
# 比值恒定，在"m=1"的读数下也恒定——两种读数都判不出二次 vs 线性。
#
# **判据**（2026-11-09）：把两边都展开成**单项式指数向量集合**，再逐项比。
#
#     κ = 7 + 28·n(text)·n(pattern)        → {(0,0), (1,0), (0,1), (1,1)}
#     target = n(text) + n(pattern)        → {(1,0), (0,1)}
#     (1,1) 不被任何 target 单项式逐分量支配 → **不是 O(target)**
#
# 判"达成"要**所有** κ 的单项式都被 target 的某个单项式支配：
# `e ≤ f`（逐分量）时 `x^e ≤ x^f ≤ target(x)`（x ≥ 1），
# 于是 κ ≤ (Σ系数)·target——这是 big-O 意义上的**充分条件**，不是猜。
#
# **只认纯多项式**：`log2` / 非整数次幂 / 非常数分母 / `⊤` / `ext` / `k(f)`
# 一律返回 None（→ unknown）。log 因子与分数次幂在多变量下的比较需要另一套
# 词典序，这里不替它猜——宁可 unknown，不许假达成。
#
# **三分法（2026-11-10）**：判「达成」失败时不要再一律 unknown。三档判据：
#
#     (C) ok       ∀ e ∈ κ   ∃ f ∈ target:  e ≤ f（逐分量）
#     (A) debt     ∃ e ∈ κ   ∀ f ∈ target:  e ≥ f（逐分量）且 e ≠ f
#     (B) unknown  其余（有的维大、有的维小 → 谁大取决于两个尺寸量的关系）
#
# (A) 是**证明**：每个 f 的 `x^(e−f)` 各维指数都 ≥ 0、至少一维 > 0，
# 所以 `x^(e−f) → ∞`，κ/target ≥ c_e·x^e / Σ_f d_f·x^f → ∞。任何常数 C 都
# 压不住——这正是用户该去优化的那种"超标"。**反例护栏**：`κ=n·m` 对
# `target=n²` 不满足 (A)（e=(1,1) 在 n 上 1 < 2），落在 (B)——那才是判不了。
#
# **`add`/`max`/`par` 都取并集**：big-O 下 `max(a,b) ≤ a+b ≤ 2·max(a,b)`，
# 三者同阶，所以"要上界"就取并集（对 κ 是保守的；对 target 也成立，因为
# 每个单项式都被某个分量压住，而该分量 ≤ target）。
#
# **`piece` 只允许在 κ 一侧、且取**全部分支的并集**：分段值 = 某一支的值
# ≤ 各支之和，所以并集仍是 κ 的上界。target 侧的分段返回 None——它的阶
# 取决于 n 落在哪一段，本判据不替它挑一段。
#
# **含 log 的那一类不在这个函数里**（2026-11-13）：`_monomials` 遇到 `log2`
# 一律返回 None，由下面的「多维 profile（次数 + 同维 log 次数）」接手——
# 见 `_log_monomials` / `_multi_log_compare`。两条判据的口径在这里对齐：
# `l` 全 0 时 (C)/(A) 逐条退化成上面这两条规则。

def _ratio_text(r: Optional[float]) -> str:
    """把参考比值印短一点（多尺寸的比值可能到 1e14）。"""
    if r is None:
        return "—"
    return f"{r:.2f}" if abs(r) < 1e7 else f"{r:.3e}"


def _monomials(c, idx: Dict[str, int], allow_piece: bool = False,
               depth: int = 0) -> Optional[Set[Tuple[int, ...]]]:
    """纯多项式成本式 → 单项式指数向量集合；判不了返回 None。

    指数向量的第 i 位是 `idx` 里第 i 个尺寸量的次数。规则见上面的注释块。
    """
    if c is None or depth > 200:
        return None
    k = c.kind
    zero = tuple(0 for _ in idx)
    if k == "num":
        return {zero}
    if k in ("size", "sym"):
        name = str(c.value or "")
        if k == "sym" and name.startswith("k("):
            return None              # 函数参数的成本：多大看调用点
        i = idx.get(name)
        if i is None:
            return None              # 别的尺寸量（不在 vs 里）→ 判不了
        e = [0] * len(idx)
        e[i] = 1
        return {tuple(e)}
    if k in ("add", "max", "par"):
        out: Set[Tuple[int, ...]] = set()
        for a in c.args:
            sub = _monomials(a, idx, allow_piece, depth + 1)
            if sub is None:
                return None
            out |= sub
        return out
    if k == "mul":
        out: Set[Tuple[int, ...]] = set()
        for a in c.args:
            sub = _monomials(a, idx, allow_piece, depth + 1)
            if sub is None:
                return None
            out = (set(sub) if not out else
                   {tuple(x + y for x, y in zip(p, q))
                    for p in out for q in sub})
        return out
    if k == "div":
        # 只认**正常数分母**（`n / 2` 与 n 同阶）。分母带尺寸量 → 阶是负的、
        # 取决于两个尺寸量的关系，不猜。
        den = c.args[1] if len(c.args) > 1 else None
        if den is not None and den.kind == "num":
            try:
                if float(den.value) > 0:
                    return _monomials(c.args[0], idx, allow_piece, depth + 1)
            except (TypeError, ValueError):
                pass
        return None
    if k == "piece":
        if not allow_piece:
            return None
        parts = list(c.args)
        _v = c.value
        if isinstance(_v, tuple) and len(_v) == 2:
            parts += [sub for _lo, _hi, sub in _v[1]]
        out = set()
        for sub in parts:
            m = _monomials(sub, idx, allow_piece, depth + 1)
            if m is None:
                return None
            out |= m
        return out if out else {zero}
    return None                      # log2 / pow / top / ext / 陌生 kind


def _multi_dominates(impl: Optional[Cost], target: Optional[Cost],
                     vs: List[str]) -> Optional[bool]:
    """**多尺寸量的结构判据**：target 是否在每个尺寸量上都支配 κ。

    True  = κ 的每个单项式都被 target 的某个单项式逐分量压住 → 达成；
    False = 至少有一项压不住（例：κ 有 `n×m`，target 只有 `n` 与 `m`）→ 不判达成；
    None  = 展不开（含 log / 分段 / ⊤ / 非常数分母）→ 不判，也不猜。
    """
    idx = {v: i for i, v in enumerate(vs)}
    im = _monomials(impl, idx, allow_piece=True)
    tm = _monomials(target, idx, allow_piece=False)
    if im is None or tm is None:
        return None
    for e in im:
        if not any(all(x <= y for x, y in zip(e, f)) for f in tm):
            return False
    return True


def _mono_text(e: Tuple[int, ...], vs: List[str]) -> str:
    """指数向量 → `n×m` / `n²` / `1`（**只用于文案**，不参与判定）。"""
    parts: List[str] = []
    for i, p in enumerate(e):
        if not p:
            continue
        name = vs[i]
        if p == 1:
            parts.append(name)
        elif p == 2:
            parts.append(f"{name}²")
        else:
            parts.append(f"{name}^{p}")
    return "×".join(parts) if parts else "1"


def _multi_exceed(impl: Optional[Cost], target: Optional[Cost],
                  vs: List[str]) -> Optional[dict]:
    """**(A) 可证超出**：返回见证；返回 None 表示**证不出来**（不许猜）。

    判据（充分条件，是**证明**不是估计）——存在 κ 的单项式 e，使 target 的
    **每一个**单项式 f 都满足

        e ≥ f（逐分量）  且  e ≠ f（至少一维严格更大）

    这时 `x^(e−f)` 的各维指数都 ≥ 0、至少一维 > 0，`Π xᵢ^(eᵢ−fᵢ)` 随尺寸量
    增大**无界**，于是

        κ/target ≥ c_e·x^e / (Σ_f d_f·x^f) = c_e / (Σ_f d_f·x^(f−e)) → ∞

    ——**不存在**常数 C 使 `κ ≤ C·target`。「超标」于是是可证的。

    **不要求每一维都严格大**：`κ = n×m` 对 `target = n` 里 `n` 那维打平、
    `m` 那维 1 > 0，比值 = `m` 照样无界。**反例必须留在 unknown**：
    `κ = n×m` 对 `target = n²` —— e=(1,1) 在 `n` 上 1 < 2，`e ≥ f` 就不成立
    （`m ≪ n` 时反而达标），那是 (B) 不可比。

    返回 `{"e": e, "M": M, "above": [(维号, e 的次数, M), …], "tm": tm}`：
    `M` 是 target 各维次数的**逐维最大值**；`above` 是 e 严格超过 `M` 的那几维
    （可能为空——空表示 `e == M` 但 `M` 不是任何**单个** target 单项式，
    即 target 的每一项都缺一维）。
    """
    idx = {v: i for i, v in enumerate(vs)}
    # **`piece` 不走这里**（`allow_piece=False`）：判「超出」要的是 κ 的**下界**，
    # 而分段只有**某一区间**生效——`piece{1..10: n×m; 越界: n}` 的 `n×m` 在
    # 大 n 处根本不跑。按并集取会拿一个"只在 n ≤ 10 成立"的高次项去判债务，
    # 那是**假债务**（大 n 处 κ = n = O(target)）。判达成用并集是上界（sound），
    # 判超出用并集不是下界（unsound）——所以这里直接放弃，退 unknown。
    im = _monomials(impl, idx, allow_piece=False)
    tm = _monomials(target, idx, allow_piece=False)
    if not im or not tm:
        return None

    # 取**最大**的那个见证（文案好读：一次性说清最高的那项）
    for e in sorted(im, key=lambda t: (sum(t), t), reverse=True):
        if all(all(x >= y for x, y in zip(e, f)) and e != f for f in tm):
            M = tuple(max(f[i] for f in tm) for i in range(len(vs)))
            above = [(i, e[i], M[i]) for i in range(len(vs)) if e[i] > M[i]]
            return {"e": e, "M": M, "above": above, "tm": tm}
    return None


def _exceed_note(w: dict, vs: List[str]) -> str:
    """(A) 的文案：**说清哪个尺寸量高了几次**（不是一句"关系未知"）。"""
    e, M, tm = w["e"], w["M"], w["tm"]
    mono = f"`{_mono_text(e, vs)}`"
    if w["above"]:
        bits = "、".join(f"在 `{vs[i]}` 上的次数 {x} 高于 target 的最高次数 {y}"
                         for i, x, y in w["above"])
        eq = [f"`{vs[i]}`" for i in range(len(vs)) if e[i] == M[i]]
        tail = f"（{'、'.join(eq)} 再小也补不回来）" if eq else ""
        gap = _mono_text(tuple(e[i] - M[i] for i in range(len(vs))), vs)
        return (f"可证超出：κ 的 {mono} {bits}{tail}——"
                f"它们一大，κ/target 至少按 `{gap}` 无界增长，不存在常数 C 能压住")
    # e == M 但没有任何**单个** target 单项式同时到各维最高：每一项都缺一维。
    misses = []
    for f in sorted(tm):
        d = "、".join(f"`{vs[i]}` 上 {e[i]} > {f[i]}"
                      for i in range(len(vs)) if e[i] > f[i])
        misses.append(f"target 的 `{_mono_text(f, vs)}` 项：{d}")
    return (f"可证超出：κ 的 {mono} 对 target 的每一项都严格领先"
            f"（{'；'.join(misses)}）——这些尺寸量一大，κ/target 就无界增长，"
            f"不存在常数 C 能压住")


def _incomparable_note_from(im, tm, vs: List[str]) -> Optional[str]:
    """`_incomparable_note` 的**判据无关**内核：拿两组次数向量说话。

    拆出来的唯一理由是「多尺寸量 + log」那条路要用**抹掉 log 的投影**来说
    「谁大取决于关系」——它和纯多项式那条路说的是同一件事，文案必须一致。
    """
    if not im or not tm:
        return None
    cand = [e for e in im
            if not any(all(x <= y for x, y in zip(e, f)) for f in tm)]
    if not cand:
        return None
    e = max(cand, key=lambda t: (sum(t), t))
    # 取**最接近**的那一项 target：κ 比它低的维最多（文案才有信息量）
    f = max(tm, key=lambda t: (sum(1 for i in range(len(vs)) if e[i] < t[i]),
                               sum(1 for i in range(len(vs)) if e[i] > t[i]), t))
    hi = [f"`{vs[i]}`" for i in range(len(vs)) if e[i] > f[i]]
    lo = [f"`{vs[i]}`" for i in range(len(vs)) if e[i] < f[i]]
    dims = [f"`{vs[i]}`" for i in range(len(vs)) if e[i] != f[i]]
    if not lo:
        return None                       # 兜底：交给通用文案
    out = (f"不可比：κ 的 `{_mono_text(e, vs)}` 在 {'、'.join(hi)} 上高于 "
           f"target 的 `{_mono_text(f, vs)}`、在 {'、'.join(lo)} 上低于它——")
    out += (f"两个尺寸量的关系未知（{'、'.join(dims)} 谁增长快不确定），"
            f"结论随关系翻转")
    return out


def _incomparable_note(impl: Optional[Cost], target: Optional[Cost],
                       vs: List[str]) -> Optional[str]:
    """(B) 的文案：给出「一维大、一维小」的**具体**对照，说明为什么判不了。"""
    idx = {v: i for i, v in enumerate(vs)}
    # 与 `_multi_exceed` 同一口径：**不展开 piece**（分段的某一支不是全局成立
    # 的下界，拿它描述"谁大"会指错方向）。展不开就退回通用文案。
    im = _monomials(impl, idx, allow_piece=False)
    tm = _monomials(target, idx, allow_piece=False)
    return _incomparable_note_from(im, tm, vs)


# ---------------------------------------------------------------- 多尺寸量 + log 因子
#
# **要解决的问题**（2026-11-13）：`_monomials` 只认纯多项式——一出现 `log2` 就
# 返回 None，于是**多尺寸量 + log 因子**的成本式（`02_csv_stats.count_fields`：
# `n(lines)×(… + L(lines) + L(lines)×… + log2(n(lines)) + …)`）无论 target 怎么写
# 都判不了：判据展不开 ⇒ unknown，少写一个尺寸量 ⇒ E23。真实语料里这是一整类。
#
# **多维 profile 的定义**：一个单项式记成
#
#       (e , l)     e = 各尺寸量的**多项式次数**（Fraction）
#                   l = 各尺寸量的**log 因子次数**（int，可负）
#       值 = Πᵢ xᵢ^eᵢ · (log₂ xᵢ)^lᵢ
#
# 例：`n × L² × log₂(n)` → e=(1,2)、l=(1,0)。常数 → e 全 0、l 全 0。
#
# **比较规则（乘积序，不是字典序）**：把每个 `(尺寸量, 它的 log)` 当成**两个
# 独立的维**，然后：
#
#     (C) ok    ∀ (e,l) ∈ κ   ∃ (f,m) ∈ target:  e ≤ f（逐分量）且 l ≤ m（逐分量）
#     (A) debt  ∃ (e,l) ∈ κ   ∀ (f,m) ∈ target:  逐维「次数严格高」或「次数打平
#                                              且 log 次数不低」，且至少一维严格
#     其余       → unknown
#
# **(C) 为什么 sound**：`e ≤ f` 且 `l ≤ m` 时
# `x^e·log^l x / (x^f·log^m x) = x^(e−f)·log^(l−m) x ≤ 1`（x ≥ 2，两个指数都
# 非正），所以 κ 的每个单项式都被 target 的某个单项式逐点压住 ⇒ κ ≤ C·target。
#
# **(A) 为什么 sound**：沿"所有尺寸量一起变大"的方向，
# `κ/target ≥ c_e·x^(e−f)·log^(l−m) x`；逐维条件保证每个因子都不衰减、至少一个
# 增长到 ∞（次数高一档 `x^δ` 盖过任何 log 的幂；次数打平时 `log^δ` 自己无界），
# 所以不存在常数 C 使 κ ≤ C·target。
#
# **为什么 log 那一维不许被"次数更高"吸收**（这是与单尺寸量字典序的
# **唯一**区别，也是本判据保守的地方）：单尺寸量时 `n log n ≺ n²` 靠字典序
# （先比次数、再比 log）——同一个变量的 `x^2` 确实压住 `x·log x`。多尺寸量时
# 每个维是**独立变量**：`xs·log₂(ys)` 对 `xs·ys` 的比是 `log ys / ys → 0`，
# 数学上**成立**，但本判据**不跨维吸收**，只按"同一维上的 log 因子必须同向
# 压住"来比。理由与"`n·m` 与 `n²` 不可比"同一条：`log` 是**与它作用的那一维
# 绑定**的阶，把它折进另一维的幂里要额外假设"那一维也够大"——而尺寸量之间的
# 关系没写进 `requires` 就不许假设。于是这条判据是**充分不必要**的：
# 它说 ok 就真的 ok，说 debt 就真的 debt，其余一律 unknown。
#
# **与既有纯多项式口径的关系**：`l` 全为 0 时上面 (C)/(A) 逐条退化成
# `_monomials` / `_multi_dominates` / `_multi_exceed` 的规则（`e ≤ f` 逐分量、
# `e ≥ f` 且 `e ≠ f`）——所以这条新路只在 `_multi_dominates` 返回 None
# （即有一侧展不开）时才被调用，既有判定**一个字节都不经过它**。

def _log_monomials(c, idx: Dict[str, int], allow_piece: bool = False,
                   depth: int = 0) -> Optional[Set[Tuple[Tuple[Fraction, ...],
                                                          Tuple[int, ...]]]]:
    """成本式 → 单项式 `(次数向量, log 次数向量)` 的集合；判不了返回 None。

    规则与 `_monomials` 逐条对齐，只有两处扩展：

      `log2(sub)`  → sub 必须**只涉及一个尺寸量**（其余维次数与 log 次数**全 0**）
                     且那一维次数 > 0 → `(该维 log 次数 +1)`，次数清零
                     （`log(x^a·log^b x) = a·log x + b·loglog x = Θ(log x)`）；
                     子式是纯常数 → 仍记常数。**跨维的 log**（`log(n·m)`）与
                     **loglog**（`log(log n)`）返回 None：前者要先知道"哪一维更
                     大"，后者要第二层 log —— 两个都是没写进 requires 的关系。
      `pow(base, e)` → 底数的 log 次数必须全 0，次数按 `e` 缩放（分数也行）。
                      `√(n log n)` 这类底数带 log 的幂仍是 None（同 `_profile`）。

    其余（`add`/`max`/`par` 取并集、`mul` 逐分量相加、`div` 只认 log 幂与正常数
    分母、`piece` 只在 `allow_piece` 时取并集、⊤/ext/k(f)/陌生节点 → None）
    与 `_monomials` **同口径**。
    """
    if c is None or depth > 200:
        return None
    k = c.kind
    n = len(idx)
    zero_e = tuple(Fraction(0) for _ in range(n))
    zero_l = tuple(0 for _ in range(n))
    if k == "num":
        return {(zero_e, zero_l)}
    if k in ("size", "sym"):
        name = str(c.value or "")
        if k == "sym" and name.startswith("k("):
            return None              # 函数参数的成本：多大看调用点
        i = idx.get(name)
        if i is None:
            return None              # 别的尺寸量（不在 vs 里）→ 判不了
        e = [Fraction(0)] * n
        e[i] = Fraction(1)
        return {(tuple(e), zero_l)}
    if k in ("log2", "log"):
        sub = _log_monomials(c.args[0], idx, allow_piece, depth + 1)
        if sub is None:
            return None
        out = set()
        for e, l in sub:
            if not any(e) and not any(l):
                out.add((zero_e, zero_l))       # log(常数) 仍是常数
                continue
            nz = [i for i, x in enumerate(e) if x != 0]
            if len(nz) == 1 and e[nz[0]] > 0 and not any(l[i] for i in range(n) if i != nz[0]):
                # 只涉及一个尺寸量且它的次数 > 0 → Θ(log 那一维)。
                # 那一维自己的 log 次数（loglog 项）被 log 压成低阶，丢掉不改变阶。
                l2 = [0] * n
                l2[nz[0]] = 1
                out.add((zero_e, tuple(l2)))
                continue
            return None       # 跨维的 log / loglog / log(负次数) —— 不猜
        return out
    if k == "pow":
        if c.value is None:
            return None                  # 符号指数（n^n / 2^n）：三元组装不下
        sub = _log_monomials(c.args[0], idx, allow_piece, depth + 1)
        if sub is None:
            return None
        ex = c.value if isinstance(c.value, Fraction) else Fraction(c.value)
        if ex < 0:
            return None                  # 负指数不是成本上界
        out = set()
        for e, l in sub:
            if any(l):
                return None              # 底数带 log 因子 → 家族外（同 _profile）
            out.add((tuple(x * ex for x in e), zero_l))
        return out
    if k == "mul":
        out = set()
        for a in c.args:
            sub = _log_monomials(a, idx, allow_piece, depth + 1)
            if sub is None:
                return None
            out = (set(sub) if not out else
                   {(tuple(x + y for x, y in zip(p[0], q[0])),
                     tuple(x + y for x, y in zip(p[1], q[1])))
                    for p in out for q in sub})
        return out
    if k == "div":
        den = c.args[1] if len(c.args) > 1 else None
        sub = _log_monomials(den, idx, allow_piece, depth + 1)
        num = _log_monomials(c.args[0], idx, allow_piece, depth + 1)
        if sub is None or num is None:
            return None
        # 分母只能是**一个**正常数（`n / 2`）或**纯 log 幂**（`n / log n`）；
        # `n / m` 这类分母带尺寸量的仍返回 None（与 `_profile` 的同一条边界）。
        if len(sub) != 1:
            return None
        de, dl = next(iter(sub))
        if not any(de):
            if not any(dl):
                cv = den.const_value()
                if cv is not None and cv <= 0:
                    return None          # div(x, 0) 是 +∞，div(x, -2) 翻符号
                return num
            if all(x >= 0 for x in dl):
                return {(tuple(x - y for x, y in zip(e, de)),
                         tuple(x - y for x, y in zip(l, dl))) for e, l in num}
        return None
    if k in ("add", "max", "par"):
        out = set()
        for a in c.args:
            sub = _log_monomials(a, idx, allow_piece, depth + 1)
            if sub is None:
                return None
            out |= sub
        return out if out else {(zero_e, zero_l)}
    if k == "piece":
        if not allow_piece:
            return None
        parts = list(c.args)
        _v = c.value
        if isinstance(_v, tuple) and len(_v) == 2:
            parts += [sub for _lo, _hi, sub in _v[1]]
        out = set()
        for sub in parts:
            m = _log_monomials(sub, idx, allow_piece, depth + 1)
            if m is None:
                return None
            out |= m
        return out if out else {(zero_e, zero_l)}
    return None                      # top / ext / 陌生 kind


def _mono_le(a, b) -> bool:
    """单项式 `a` 是否**逐维**不高于 `b`（次数与 log 次数**都**要 ≤）。"""
    (ea, la), (eb, lb) = a, b
    return (all(x <= y for x, y in zip(ea, eb))
            and all(x <= y for x, y in zip(la, lb)))


def _mono_leads(a, b) -> bool:
    """`a` 是否沿"所有尺寸一起变大"**无界领先** `b`（逐维不衰减 + 至少一维严格）。"""
    (ea, la), (eb, lb) = a, b
    for i in range(len(ea)):
        if ea[i] > eb[i]:
            continue
        if ea[i] == eb[i] and la[i] >= lb[i]:
            continue
        return False           # 这一维在衰减（次数更低，或次数打平而 log 更少）
    return (ea, la) != (eb, lb)


def _mono_log_text(m, vs: List[str]) -> str:
    """单项式 → `n×L²×log₂(lines)`（**只用于文案**，不参与判定）。"""
    e, l = m
    parts: List[str] = []
    if any(e):
        parts.append(_mono_text(tuple(int(x) if x.denominator == 1 else x
                                      for x in e), vs))
    for i, p in enumerate(l):
        if not p:
            continue
        base = f"log₂({vs[i]})"
        if p == 1:
            parts.append(base)
        elif p == -1:
            parts.append(f"1/{base}")
        else:
            parts.append(f"{base}^{p}")
    return "×".join(parts) if parts else "1"


def _log_dominates(im, tm) -> bool:
    """(C)：κ 的每个单项式都被 target 的某个单项式逐维压住。"""
    for m in im:
        if not any(_mono_le(m, f) for f in tm):
            return False
    return True


def _log_exceed(im, tm, vs: List[str]):
    """(A)：返回见证 `(κ 单项式, 它在哪些维严格领先)`；证不出 → None。"""
    for m in sorted(im, key=lambda t: (sum(t[0]), sum(t[1]), t), reverse=True):
        if all(_mono_leads(m, f) for f in tm):
            (ea, la) = m
            above = []
            for i in range(len(ea)):
                eb_max = max(f[0][i] for f in tm)
                lb_max = max(f[1][i] for f in tm if f[0][i] == eb_max)
                if ea[i] > eb_max:
                    above.append(f"在 `{vs[i]}` 上的次数 {ea[i]} 高于 target 的最高次数 {eb_max}")
                elif ea[i] == eb_max and la[i] > lb_max:
                    above.append(f"在 `{vs[i]}` 上的 log 因子 {la[i]} 高于 target 的最高 log 因子 {lb_max}")
            return {"m": m, "above": above, "tm": tm}
    return None


def _log_free(m):
    """把 log 因子抹掉，只留次数向量（**只用于解释 unknown 的原因**）。"""
    return m[0]


def _multi_log_compare(impl: Optional[Cost], target: Optional[Cost],
                       vs: List[str], ratio: Optional[float]) -> Optional["Verdict"]:
    """**多尺寸量 + log 因子**的比较；判不了返回 None（调用方用既有文案）。

    只在 `_multi_dominates` 返回 None（有一侧含 log / 分数次幂 / 展不开）时被调用。
    三档与纯多项式口径**同一套**（(C) ok / (A) debt / 其余 unknown），区别只在
    `_mono_le` / `_mono_leads` 逐维把 log 次数一起比——见上面的规则块。
    """
    idx = {v: i for i, v in enumerate(vs)}
    im = _log_monomials(impl, idx, allow_piece=True)
    tm = _log_monomials(target, idx, allow_piece=False)
    if im is None or tm is None:
        return None                       # 展不开：交给既有文案，不猜
    _pairs = "、".join(f"`{v}`" for v in vs)
    if _log_dominates(im, tm):
        return Verdict("ok", ratio=ratio, multi=True,
                       note=("结构判据（多尺寸量逐项支配，**含 log 因子**）："
                             "κ 的每个单项式都被 target 的某个单项式在"
                             "「次数 + 同维 log 次数」上逐项压住"))
    # **判「超出」要 κ 的下界**：`piece` 的并集只是上界（某一段只在小区间生效），
    # 与 `_multi_exceed` 的 `allow_piece=False` 同一条 sound 性边界。
    im_low = _log_monomials(impl, idx, allow_piece=False)
    w = _log_exceed(im_low, tm, vs) if im_low else None
    if w is not None:
        m, above = w["m"], w["above"]
        bits = "；".join(above)
        return Verdict(
            "debt", ratio=ratio, multi=True,
            detail=(f"可证超出：κ 的 `{_mono_log_text(m, vs)}` 对 target 的**每一项**都"
                    f"沿所有尺寸一起变大的方向领先（{bits or '每一项都缺一维'}）——"
                    f"κ/target 无界增长，不存在常数 C 能压住"),
            note="结构判据（多尺寸量，含 log 因子）：可证超出")
    # **unknown**：先说清是"log 这一维没被压住"还是"次数上就不可比"。
    # 前者是本判据**故意保守**的那一档（不跨维吸收 log），后者是真的不可比。
    if all(any(all(x <= y for x, y in zip(_log_free(m), _log_free(f))) for f in tm)
           for m in im):
        blocked = [m for m in im if not any(_mono_le(m, f) for f in tm)]
        show = max(blocked, key=lambda t: (sum(t[0]), sum(t[1]), t))
        why = (f"含 log 因子的多尺寸量：本判据把 `log₂(某一维)` 当成**与那一维绑定**的"
               f"独立一维，只认「同一维的 log 次数也被 target 压住」；"
               f"`{_mono_log_text(show, vs)}` 这一项的次数维已经被压住，缺的只是"
               f"**同维 log 因子**。**跨维吸收**（`log₂ m ≤ m`）虽然成立，但它要求先知道"
               f"尺寸量之间的大小关系（没写进 `requires`），所以这条**不是本判据能展开**"
               f"的方向——不给「达成」，也不给「债务」")
    else:
        why = _incomparable_note_from([_log_free(m) for m in im],
                                      [_log_free(f) for f in tm], vs) or (
            f"不可比：κ 至少有一项在某个尺寸量上的次数高于 target 能给的最高次数，"
            f"但没有任何一项能压住 target 的每一项（{_pairs} 谁增长快不确定）")
        why = ("含 log 因子的多尺寸量（log 因子**不跨维吸收**）：" + why)
    if ratio is not None:
        why += (f"；比值 ≈ {_ratio_text(ratio)} 只是按同规模假设"
                f"（所有尺寸量 = n）算出的参考值，不作判定依据")
    return Verdict("unknown", ratio=ratio, multi=True, note=why)


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
    # **`par` 也要能按数值比**（2026-11-09）：`parallel { {a} {b} }` 的分配是
    # Σ（B-24），`par` 节点本身不是 `num`，所以 `is_const()` 为假、`.value` 是 None。
    # 用 `const_value()`（num → 值，par → Σ，其余 → None）取数：
    # 少了这一步，`allocation: { max: 0 }` 遇到全常数的并行块会掉进
    # `compare()` 的"常数一律达成"，把超支**静默放过**。
    iv, tv = impl.const_value(), target.const_value()
    if iv is not None and tv is not None:
        r = (iv / tv) if tv else None
        if iv <= tv:
            return Verdict("ok", ratio=r)
        return Verdict("debt", ratio=r,
                       note=f"分配 {_num_text(iv)} 次 > target 的 {_num_text(tv)} 次")
    if tv is not None:
        # **H3（分配维度）**：target 是常数（例如 `max: 0`）而分配上界带符号
        # （例如 `n(xs)×1`）→ 规模一大就超过任何常数，判 debt。
        # 以前判 unknown，于是"承诺 0 次分配、实际每轮分配"能过门禁。
        return Verdict("debt", note=(f"分配上界 {impl.render()} 随规模增长，"
                                     f"而 target 是常数 {_num_text(tv)} 次"))
    return compare(impl, target)


def _num_text(v: float) -> str:
    """把 `const_value()` 的浮点印成人读的整数（`5.0` → `5`）。"""
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _divisor_may_shrink(c, var: str) -> bool:
    """这个表达式里有没有**除以"随规模增长 / 判不了"的量**的分母 ?

    被 `compare` 用来挡住一类**不能判**的情形：`O(n / m)`、`O(1 / n)` 的阶是
    **负的**，而且取决于两个尺寸量的关系（`m ≈ n` → 商是 1；`m ≈ n²` → 商是 1/n）。
    "同规模假设"在这里会把这两种读法混成一种，所以直接退 ⊤。
    它同时补上"κ 是常数 ⇒ 任何 target 都满足"那条规则的隐含前提
    （target 的阶 ≥ 0）。

    **判不了也当"可能缩小"**——宁可 unknown，不给假 ✓。
    `n / log n`（分母 `(0, 1, 0)`）与 `n / loglog n`（分母 `(0, 0, 1)`）
    **不算**：它们仍然趋于无穷，阶分别是 (1,-1,0) / (1,0,-1)，
    结构判据能给出确定答案。
    """
    if c is None:
        return False
    if c.kind == "div":
        pb = _profile(c.args[1], var)
        if pb is None or pb[0] > 0:
            return True
    for a in (getattr(c, "args", None) or []):
        if _divisor_may_shrink(a, var):
            return True
    if c.kind == "piece":
        _v = c.value
        if isinstance(_v, tuple) and len(_v) == 2:
            for _lo, _hi, sub in _v[1]:
                if _divisor_may_shrink(sub, var):
                    return True
    return False


def _has_sym(c) -> bool:
    """成本式里有没有**未解释的符号**（`sym`，例如 `k(f)`）。"""
    if c is None:
        return False
    if c.kind == "sym":
        return True
    for a in (getattr(c, "args", None) or []):
        if _has_sym(a):
            return True
    if c.kind == "piece":
        _v = c.value
        if isinstance(_v, tuple) and len(_v) == 2:
            for _lo, _hi, sub in _v[1]:
                if _has_sym(sub):
                    return True
    return False


def _sym_names(c) -> List[str]:
    """成本式里的**裸符号名**（`sym`），用于尺寸量集合的配对。

    **为什么必须收集它**（2026-11-08）：target 里的标量形参写成 `O(dim)` 时
    `_target_cost` 产出的是 `sym("dim")`，而 κ 里循环轮数是 `size("dim")`。
    旧实现只收集 `size`，于是 target 侧看起来「没有尺寸量」——`compare()`
    走「target 是常数」的分支，把**正确的承诺判成超标**。
    """
    out: List[str] = []
    if c is None:
        return out
    if c.kind == "sym":
        # `k(f)` 是**函数参数的成本符号**，不是尺寸量；它进集合会让
        # 「κ 只含 k(f)」被当成「有尺寸量」，判定反而变成假的。
        if not str(c.value or "").startswith("k("):
            out.append(str(c.value))
    for a in (getattr(c, "args", None) or []):
        out.extend(_sym_names(a))
    if c.kind == "piece":
        _v = c.value
        if isinstance(_v, tuple) and len(_v) == 2:
            for _lo, _hi, sub in _v[1]:
                out.extend(_sym_names(sub))
    return out


def _sym_of(node):
    """把 `hi` / `xs.length()` / 整数字面量翻成一个尺寸符号名。

    **字面量也要**（2026-11-10）：`requires 1 <= n;` 是「尺寸量有下界」的
    标准写法，而 `hi - lo` 这类**尺寸差**要成立就得证明下界
    （`requires lo <= hi`）。只认 path / `.length()` 时这种子界读不出来，
    差值一律退 ⊤。返回 `str(int)`，与 `Cost` 的 `num` 渲染同形，
    所以 `_prove_le` 能把 `num(1) <= size(n)` 接到同一条事实链上。
    """
    if node is None:
        return None
    if getattr(node, "kind", "") == "path":
        return node.name
    if getattr(node, "kind", "") == "lit-int":
        try:
            return str(int(node.value))
        except (TypeError, ValueError):
            return None
    if getattr(node, "kind", "") in ("method-call", "call") and \
            (getattr(node, "name", "") or "").split(".")[-1] in ("length", "size", "len"):
        recv = node.children[0] if getattr(node, "children", None) else None
        if getattr(recv, "kind", "") == "path":
            return recv.name
    return None


#: 比较运算符 → 归一成 `小 <= 大`。**严格比较也收**（整数域）：
#: `a > b` ⇒ `b < a` ⇒ `b <= a`，这是**弱化**，不会把假的顺序当真的用。
_LE_NORMALISE = {"<": (0, 1), "<=": (0, 1), ">": (1, 0), ">=": (1, 0)}


def facts_of_spec(spec) -> dict:
    """从 spec 的 `requires` 里提取 `A <= B` 事实 → {A: {B, …}}。

    **这就是"声明的假设"的载体**（A1，2026-11-06）：
    只有写进 `requires` 的才作数，**不从用法推断**（`xs[hi]` 不能推出 `hi < len`）。

    **四个比较方向都归一成 `小 <= 大`**（2026-11-10）：以前只认 `<` / `<=`，
    于是 `requires hi >= lo;`（人最自然写的那一句）**一条事实都不产生**，
    target 里的 `O(hi - lo)` 就永远证明不了 `lo <= hi`。归一化是弱化
    （`a >= b` ⇒ `b <= a`、`a > b` ⇒ `b <= a`），不会多出一条假顺序。
    """
    out = {}
    if spec is None:
        return out
    body = next((c for c in (getattr(spec, "children", None) or [])
                 if getattr(c, "kind", "") == "spec-body"), None)
    for c in (getattr(body, "children", None) or []):
        if getattr(c, "kind", "") != "clause" or getattr(c, "name", "") != "requires":
            continue
        for k in (getattr(c, "children", None) or []):
            if getattr(k, "kind", "") != "binary":
                continue
            swap = _LE_NORMALISE.get(getattr(k, "value", "") or "")
            if swap is None:
                continue
            a, b = (k.children + [None, None])[:2]
            kids = (a, b) if swap == (0, 1) else (b, a)
            x, y = _sym_of(kids[0]), _sym_of(kids[1])
            if x and y and x != y:
                out.setdefault(x, set()).add(y)
    return out


def _rename_cost(c, mapping):
    """把 Cost 里的 `size/sym(小符号)` 换成 `size(大符号)`。

    **`sym` 也要换**（P1，2026-11-08）：标量形参在 κ 里渲染成裸符号
    （`hi`），而 `requires hi <= xs.length()` 把它弱化成 `n(xs)`。
    只换 `size` 会让 requires 事实对裸符号失效（test_requires_facts 判 unknown）。
    """
    if c is None:
        return c
    if getattr(c, "kind", "") in ("size", "sym") and c.value in mapping:
        return type(c).size(mapping[c.value])
    args = getattr(c, "args", ())
    if args:
        return type(c)(c.kind, c.value, [_rename_cost(a, mapping) for a in args])
    return c


def _ord_text(o: Optional[Tuple[Fraction, int, int]]) -> str:
    """三元组印成 `(1, 1, 0)`（**只用于 log 域判阶的说明文字**）。"""
    if o is None:
        return "?"
    d, l, m = o
    ds = str(int(d)) if d.denominator == 1 else f"{d.numerator}/{d.denominator}"
    return f"({ds}, {l}, {m})"


def _lead_log(c: Optional[Cost], var: str, depth: int = 0) -> Optional[Cost]:
    """把 log 域式子里的**常数阶加项 / max 分支**丢掉；全是常数阶则返回 None。

    **为什么只丢常数阶、只丢在 `add`/`max` 里**（这是"同阶"那一档的唯一
    可判依据）：log 域里的 `+ 常数` 对应**原式的一个常数倍**
    （`log κ = 1 + log t` ⇒ `κ = 2·t`），所以丢掉它不改变 big-O。
    但 **`mul`/`div` 里的常数因子不能丢**：log 域里的乘数是"幂"
    （`2·log n` 与 `log n` 对应的原式差 n 倍）——丢了就会把
    `n^(2n)` 与 `n^n` 判成同阶。

    用途只有一个：`compare` 在"两边三元组相同"时，判断两份 log 形式是不是
    **只差常数倍**（`n^n` 与 `2·n^n`、`n^n + 5`）。真的结构不同
    （`n^n` 与 `n^(n/2)`）就返回"不同"，调用方判 unknown。
    """
    if c is None or depth > 200:
        return c
    p = _profile(c, var)
    if p is None:
        return c
    if p == (Fraction(0), 0, 0):
        return None
    if c.kind in ("add", "max") and len(c.args) == 2:
        a = _lead_log(c.args[0], var, depth + 1)
        b = _lead_log(c.args[1], var, depth + 1)
        if a is None:
            return b
        if b is None:
            return a
        return Cost(c.kind, c.value, (a, b))
    return c


def _hits_cap(c: Cost, var: str) -> bool:
    """这个式子在 LADDER 顶端是不是已经到饱和阈值（比值那一栏不再可信）。"""
    if var is None:
        return False
    try:
        return evaluate(c, {var: float(LADDER[-1])}) >= MAG_CAP
    except (TypeError, ValueError, OverflowError):
        return False


def _compare_log_domain(impl: Cost, target: Cost, var: str,
                        multi: bool) -> "Verdict":
    """**log 域判阶**（`设计-log域判阶-2026-11-12.md` 第 4 步）。

    只在**任一侧含符号指数**时被 `compare` 调用——不含的既有路径一个字节
    都不动。判据：

        log(κ) 的阶 <  log(target) 的阶   → 达成（`κ = t^{o(1)} ≤ C·t`）
        log(κ) 的阶 >  log(target) 的阶   → 债务（`κ/t → ∞`）
        两者相同                          → 只有"只差常数倍"才判达成，否则 unknown

    **`to_log` 失败 ⇒ unknown**，绝不退"达成"或"债务"（这两条都是结论，
    而变换被拒绝只说明"这一层判不了"）。

    **同阶为什么不能一律判达成**：log 域的"同阶"是 `κ = t^{Θ(1)}`，
    常数因子在 log 域里会变成**乘幂**关系——`n^n` 与 `n^(n/2)` 的 log 形式
    都是 `n log n`，但前者是后者的 `n^(n/2)` 倍。所以同阶时只认
    "**两份 log 形式结构相同，或只差常数阶加项**"（那对应原式的常数倍）。
    """
    if multi:
        return Verdict("unknown", multi=True,
                       note="含符号指数的比较目前只有**单尺寸量**口径"
                            "（log 域的三元组一次只看一个尺寸量）："
                            "两个以上的尺寸量时不猜")
    lk, why_k = to_log_with_reason(impl)
    if lk is None:
        return Verdict("unknown",
                       note=f"log 域判阶：**κ 的 log 变换被拒绝**（{why_k}）——"
                            "这一层判不了，不给「达成/债务」的结论")
    lt, why_t = to_log_with_reason(target)
    if lt is None:
        return Verdict("unknown",
                       note=f"log 域判阶：**target 的 log 变换被拒绝**（{why_t}）——"
                            "这一层判不了，不给「达成/债务」的结论")
    pk = _profile(lk, var)
    pt = _profile(lt, var)
    if pk is None or pt is None:
        return Verdict("unknown",
                       note=f"log 域里仍归约不出阶（κ: {_ord_text(pk)}，"
                            f"target: {_ord_text(pt)}）——不猜")
    # **参考比值**：`pow` 的求值会饱和（1e18），两个值都到顶时比值恒为 1，
    # 那个数没有信息量，所以**不给**（比给一个 1.00 诚实）。
    rs = _ratios(impl, target, [var])
    capped = _hits_cap(impl, var) or _hits_cap(target, var)
    ratio = rs[-1] if (rs and not capped) else None
    cap_note = ("；值已到饱和阈值 1e18，比值栏不给数" if capped else "")
    if pk < pt:
        return Verdict("ok", ratio=ratio,
                       note=f"log 域判据：log(κ) 的阶 {_ord_text(pk)} ≤ "
                            f"log(target) 的阶 {_ord_text(pt)}{cap_note}")
    if pk > pt:
        return Verdict(
            "debt", ratio=ratio,
            detail=(f"log 域判据：log(κ) 的阶 {_ord_text(pk)} 高于 "
                    f"log(target) 的阶 {_ord_text(pt)}——κ 增长更快"
                    "（变量指数的比较在这里按**指数**判，不按底数）"),
            note="log 域判据：log(κ) 的阶高于 log(target)")
    na = _lead_log(lk, var)
    nb = _lead_log(lt, var)
    if na is not None and nb is not None and _same(na, nb):
        return Verdict("ok", ratio=ratio,
                       note=f"log 域判据：两侧 log 形式同阶且只差常数倍"
                            f"（{_ord_text(pk)}）{cap_note}")
    return Verdict(
        "unknown", ratio=ratio,
        note=(f"log 域里两侧同阶（都是 {_ord_text(pk)}）但结构不同："
              "常数因子在 log 域里会变成**乘幂**关系"
              "（`n^n` 与 `n^(n/2)` 的 log 形式都是 `n log n`），"
              "无法证明 `κ ≤ C·target` ——不猜"))


def compare(impl: Optional[Cost], target: Optional[Cost], facts: Optional[dict] = None) -> Verdict:
    """把 κ_impl 与 κ_target 按 **big-O** 比。"""
    if impl is None:
        return Verdict("unknown", note="没有算出 κ_impl")
    if target is None:
        return Verdict("unknown", note="spec 没有 time 的 target")
    if impl.is_top():
        return Verdict("unknown", note="κ_impl 是 ⊤（有循环上界推不出）")
    if target.is_top():
        return Verdict("ok", note="target 是 ⊤，任何成本都满足")

    # **尺寸量集合**包括 `size` 与**裸符号**（target 里的标量形参 `dim` / `hi`）。
    # 只收 `size` 会漏掉 target 侧的标量维度（见 `_sym_names` 的说明）。
    ci_size, ct_size = set(cost_vars(impl)), set(cost_vars(target))
    ci = ci_size | set(_sym_names(impl))
    ct = ct_size | set(_sym_names(target))
    vs = sorted(ci | ct)
    multi = len(vs) > 1

    # **`n(?)` 不是尺寸量，是"未知"的标记**（D4 兜底）。
    # 把它当成普通尺寸量去配对，会得到"尺寸量不一致（E23）"或按同规模假设
    # 算出的假比值——两种都是把"不知道"冒充成"知道"。构造点（`cost_interp`）
    # 已经改成"推导或 ⊤"，这里只兜最后一道：出现就判不了。
    if "?" in ci or "?" in ct:
        return Verdict("unknown", multi=multi,
                       note="κ/target 里有 `n(?)`（未知尺寸）："
                            "这是“不知道”的标记，不当作尺寸量参与判定")

    # **D1（2026-11-09，除法带来的新边界）**：target 里**除以一个随规模增长的
    # 量**（`O(n / m)`、`O(1 / n)`）时，它的阶是**负的**，而且取决于两个尺寸量
    # 的关系——那正是"同规模假设"（把所有尺寸设成同一个 n）最不该插嘴的地方：
    # `n / m` 在 m = n 时是 1、在 m = n² 时是 1/n，两种读法给出相反的判决。
    # 所以**结构上就判不了**，直接 ⊤，不退回数值拟合、也不套同规模假设。
    #
    # 这条同时修掉一个旧洞：`κ 是常数 ⇒ 任何 target 都满足` 的前提是
    # "target 的阶 ≥ 0"，而 `n / m` 可以被压到 O(1) 以下。
    # `n / log n`（分母是 log 的幂）与 `n / loglog n`（分母是 loglog 的幂）
    # **不**走这里：它们仍趋于无穷，阶分别是 (1,-1,0) / (1,0,-1)，
    # 结构判据能给出确定的答案。
    if vs and _divisor_may_shrink(target, vs[0]):
        return Verdict("unknown", multi=multi,
                       note="target 里有**除以随规模增长的量**（或分母判不了）："
                            "这样的 target 阶是负的、且取决于两个尺寸量的关系"
                            "（`n / m` 在 m≈n 时是 1、在 m≈n² 时是 1/n），"
                            "这里不猜——要么把那个量换成常数或 log 的幂，要么人工判")

    # **E23：κ 与 target 用的尺寸量不一致时，比比值没有意义。**
    # 真例子：`calculator` 的 κ 用局部 `tokens`/`operators`、target 用形参 `text`；
    #        `topk` 的 κ 用 `xs`/`k`、target 只有 `xs`（因为 target 里写不了两个尺寸相乘）。
    # 旧行为把所有尺寸设成同一个 n 再比 —— 那是在比两件不同的事，
    # 会给出天文数字（6.6e12 / 3.5e13）并**误报债务**。
    # 新行为：**不判债务，只报尺寸量不一致**；比值只作参考信息。
    # κ 不含任何尺寸量 ⇒ 它是**常数**，big-O 意义下任何阶的 target 都满足。
    # （E22 之后 `calculator.evaluate` 就是这种：定容容器把成本压成了常数。）
    if not ci:
        # **G1**：κ 里没有尺寸量**不等于**它是常数——可能是 `k(f)` 这种
        # 未解释的符号（被调函数是谁要看调用点）。实测 `apply(xs, sq)`
        # （sq 是 O(x²)）以前走到这里被判「κ 是常数，任何阶都满足」→ 假的 ✓。
        if _has_sym(impl):
            return Verdict("unknown",
                           note="κ 含未解释的符号（如 k(f)）：被调函数多大要看调用点，"
                                "这里判不了阶，不给你一个假的上界")
        a = evaluate(impl, {})
        b = evaluate(target, {}) if ct_size else None
        return Verdict("ok", ratio=(a / b) if b else None,
                       note="κ 是常数（不含尺寸量），任何阶的 target 都满足")
    if ci != ct and facts:
        # **A1（2026-11-06）：用声明的 requires 把 κ 的符号弱化到 target 的符号上。**
        # 例：`requires hi <= xs.length()` ⇒ `n(hi)` 可以按 `n(xs)` 计。
        # **只认 facts**（来自 requires），不推断；弱化不动 target。
        _m = {}
        for _s in ci:
            if _s in ct:
                continue
            _cand = (facts.get(_s) or set()) & ct
            if not _cand:
                _m = None
                break
            _m[_s] = sorted(_cand)[0]
        if _m:
            _w = _rename_cost(impl, _m)
            _v = compare(_w, target, None)
            _used = ", ".join(f"{a}≤{b}" for a, b in sorted(_m.items()))
            _n = f"（按 requires 弱化：{_used}）"
            return Verdict(_v.kind, ratio=_v.ratio, multi=_v.multi,
                           note=(_v.note or "") + _n)
    if ci != ct:
        # **H3（2026-11-02）**：target 写成常数（O(1)）而 κ 含尺寸量时，
        # 过去一律判 unknown —— 于是"声明 O(1)、实现 O(n)"**必然判不出**。
        # 但这不是判不出：任何随规模增长的量都 ≰ O(1)，判 debt 即可。
        if not ct:
            rs0 = _ratios(impl, target, vs)
            return Verdict("debt", multi=multi,
                           ratio=(rs0[-1] if rs0 else None),
                           note=("target 是常数（O(1)），而 κ 含尺寸量 "
                                 + "、".join(sorted(ci)) + "：随规模增长必然超阶"))
        rs0 = _ratios(impl, target, vs)
        extra = (f"；若按同规模假设（所有尺寸量 = n），比值约 {_ratio_text(rs0[-1])}"
                 f"（仅供参考，不作判定）") if rs0 else ""
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

    # ---------------------------------------------------------------- log 域（符号指数）
    #
    # **只在任一侧含符号指数时**才走这条路（2026-11-12，设计的第 4 步）：
    # 不含的一段代码都不经过，既有判定路径**逐字节不变**（这是硬约束）。
    #
    # 为什么不能直接 `_profile`：`n^n`、`2^n` 的指数位置上是一个**关于尺寸的
    # 式子**，三元组装不下（`_profile` 如实返回 None）。取 log 之后就都变回
    # `n^d·log^l·loglog^m` 了：`log(n^n) = n log n → (1,1,0)`、
    # `log(2^n) = n → (1,0,0)`、`log(n^10) = 10 log n → (0,1,0)`。
    # 三条规则、四条拒绝、以及"同阶只有结构相同才判达成"的理由，
    # 全在 `logdom.py` 的模块文档与 `_compare_log_domain` 里。
    if has_symbolic_exp(impl) or has_symbolic_exp(target):
        return _compare_log_domain(impl, target, vs[0], multi)

    # ---------------------------------------------------------------- 多尺寸量
    #
    # **旧洞（2026-11-09）**：这里以前直接 `struct = None`、退回数值拟合，
    # 而 `_ratios` 只把 `vs[0]` 设成 n、其余当 1 —— 于是
    #   `κ = n×m` vs `target = n+m` 的比值恒定 57 → 「✓ 达成」
    #   二次实现过线性契约，`O(n×m)` 与 `O(n+m)` 两个 target 同时判达成。
    #
    # **新规则（2026-11-10 三分法）**：多尺寸量不是"能证明支配就 ok、否则一律
    # unknown"，而是三档（判据见上面的注释块与 `_multi_exceed`）：
    #
    #   (C) 逐项支配          → ok
    #   (A) ∃e 严格领先 target 的每一项 → **debt**（可证超出，文案说清哪维高几次）
    #   (B) 其余（有的维大有的维小）  → unknown（**只有这一种**是"判不了"）
    #
    # 「两个尺寸量谁增长快」仍然没有写进 requires，本工具不替作者假设"同规模"
    # ——假设正是那个假达成的来源。但 (A) **不需要**任何关系假设：`e ≥ f` 且
    # `e ≠ f` 时比值沿**所有**尺寸量一起变大的方向都无界。
    if multi:
        rs = _ratios(impl, target, vs)
        dom = _multi_dominates(impl, target, vs)
        _pairs = "、".join(f"`{v}`" for v in vs)
        if dom is True:
            return Verdict(
                "ok", ratio=(rs[-1] if rs else None), multi=True,
                note=("结构判据（多尺寸量逐项支配）：target 在每个尺寸量上的"
                      "次数都不低于 κ"))
        if dom is False:
            _w = _multi_exceed(impl, target, vs)
            if _w is not None:
                return Verdict("debt", ratio=(rs[-1] if rs else None), multi=True,
                               note="结构判据（多尺寸量）：可证超出（逐项严格领先）",
                               detail=_exceed_note(_w, vs))
            _why = (_incomparable_note(impl, target, vs)
                    or (f"不可比：κ 至少有一项在某个尺寸量上的次数高于 target "
                        f"能给的最高次数，但没有一项能压住 target 的每一项"
                        f"（{_pairs} 谁增长快不确定）"))
        else:
            # **展不开的那一侧**（含 log 因子 / 分数次幂 / ⊤ …）。
            # 2026-11-13：先试**多维 profile（次数 + 同维 log 次数）**——
            # 它对「多尺寸量 + log 项」这一整类给出 (C)/(A) 两档**可证**结论；
            # 仍然证不出时它自己给 unknown 的说明（含"不跨维吸收 log"那条
            # 保守边界）。它返回 None 才退回原来的"展不开"文案。
            _lv = _multi_log_compare(impl, target, vs, rs[-1] if rs else None)
            if _lv is not None:
                return _lv
            _why = (f"两个尺寸量的关系未知（{_pairs} 谁增长快不确定）："
                    f"κ 或 target 不是本判据能展开的纯多项式（含 log / 分段 / ⊤ 等）")
        if rs:
            _why += (f"；比值 ≈ {_ratio_text(rs[-1])} 只是按同规模假设"
                     f"（所有尺寸量 = n）算出的参考值，不作判定依据")
        return Verdict("unknown", ratio=(rs[-1] if rs else None), multi=True,
                       note=_why)

    # ---------------------------------------------------------------- 单尺寸量
    # **结构判据优先**（2026-11-08）：结构给 True/False 就照它判；
    # 给 None 就 **unknown**（2026-11-10 起不再退回数值拟合，见 `_structural_unknown`）。
    var = vs[0]
    rs = _ratios(impl, target, [var])

    # **结构判据优先**（2026-11-08）：阶从表达式树直接读，不再靠采样拟合。
    struct = _order_le(impl, target, var)
    if struct is True:
        return Verdict("ok", ratio=(rs[-1] if rs else None),
                       note="结构判据：κ 的阶 ≤ target 的阶")
    if struct is False:
        return Verdict("debt", ratio=(rs[-1] if rs else None),
                       note="结构判据：κ 的阶高于 target 的阶")

    # **结构判不了 → unknown**（2026-11-10）。这里以前会拿 `_slope(rs)` 拟合：
    # 斜率 > 0.05 判 debt、否则判 ok。那条路的问题不是"不够准"，而是**它在猜**：
    # 斜率符号依赖 `LADDER`（换一组 n 就翻），而"判不了"与"超标"是两条不同的
    # 信息——后者会让用户去优化一个可能本来就合格的实现。现在把两种 None 都
    # 如实报出来：家族外（三层 log / ext / k(f) / n/m …）与陌生节点。
    # `piece` 不在这一类：2026-11-10 起逐段归约（各段阶的 max），
    # 只有"某一段判不了"才会落到这里，且原因报到那一段上。
    #
    # 注意：`rs` 仍然算了，但它**只**服务于上面 ok/debt 的"参考比值"。
    # 这里不再读它，所以 `LADDER` 换多少都不会改变任何判定。
    return _structural_unknown(impl, target, var)


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
    from .symbolic import Cost as C

    n = C.size("xs")
    cases = [
        # (标签, impl, target, 期望)
        ("n vs O(n)", n, n, "ok"),
        ("3n+9 vs O(n)", C.add(C.mul(C.num(3), n), C.num(9)), n, "ok"),
        ("n² vs O(n)", C.mul(n, n), n, "debt"),
        ("n vs O(log n)", n, C.log2(n), "debt"),
        ("log2 n vs O(n)", C.log2(n), n, "ok"),
        ("n log n vs O(n)", C.mul(n, C.log2(n)), n, "debt"),
        ("n vs O(n log n)", n, C.mul(n, C.log2(n)), "ok"),
        # **loglog**（2026-11-09）：`n log n` 对 `O(n log log n)` 是**债务**。
        # 没有 loglog 分量时 `log(log(n))` 被折成 `log n`，这一条会判达成——
        # 那正是这一步要消灭的假 ✓。
        ("n log n vs O(n loglog n)",
         C.mul(n, C.log2(n)), C.mul(n, C.loglog(n)), "debt"),
        ("n loglog n vs O(n log n)",
         C.mul(n, C.loglog(n)), C.mul(n, C.log2(n)), "ok"),
        ("n log n vs O(n log n)",
         C.mul(n, C.log2(n)), C.mul(n, C.log2(n)), "ok"),
        ("n² vs O(n²)", C.mul(n, n), C.mul(n, n), "ok"),
        ("n² vs O(n log n)", C.mul(n, n), C.mul(n, C.log2(n)), "debt"),
        ("常数 100 vs O(1)", C.num(100), C.num(1), "ok"),
        ("常数 5 vs O(n)", C.num(5), n, "ok"),
        # **parallel：判阶取 max**（B-24）。四个任务都是 n → 阶仍是 n，
        # 不是 n⁴；一个任务退化成 n² → 整块就是 n²（超）。
        ("par(4×n) vs O(n)", C.par([n, n, n, n]), n, "ok"),
        ("par(n², n) vs O(n)", C.par([C.mul(n, n), n]), n, "debt"),
        ("par(n, n) vs O(n²)", C.par([n, n]), C.mul(n, n), "ok"),
    ]
    bad = 0
    for label, a, b, want in cases:
        v = compare(a, b)
        mark = "✓" if v.kind == want else "✗"
        print(f"  {mark} {label:26} → {v.kind:6} 期望 {want:6} | {v.render()}")
        bad += 0 if v.kind == want else 1
    print(f"\n{'全部通过' if not bad else f'失败 {bad} 项'}")
    raise SystemExit(1 if bad else 0)
