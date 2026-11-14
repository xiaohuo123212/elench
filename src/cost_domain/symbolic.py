#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""symbolic.py —— G27：符号域（`Symbolic`）的成本式与代数。

**符号域解决什么问题**：不知道输入多长时，解释器拿什么往下算。

答案：**关于尺寸 n 的一个式子**。它只有六种成分：

    Cost ::= Number(1)                   常数
           | Size("xs")                  尺寸量（xs.length()）
           | Add(a, b)                   顺序：成本相加
           | Mul(n, a)                   循环：圈数乘每圈成本
           | Max(a, b)                   分支：取较大者
           | Log2(a)                     对数
           | Log2(Log2(a))               双层对数（`loglog n`；不新增 kind，2026-11-09）
           | Pow(a, p/q)                 有理次幂（`sqrt(n)` = `n^(1/2)`，2026-11-09）
           | Pow(a, b)                   符号指数（`n^n`、`2^n`，2026-11-12）
           | Div(a, b)                   除法（`n / log n`，2026-11-09）
           | Par([a, b, …])              并行：判阶取 max、总量取 Σ

**符号指数为什么**（2026-11-12）：`10_backtrack.place` 的激活数是 `nⁿ`，
指数位置上放的是尺寸量自己。旧实现把指数**写死成 `Fraction`**，于是变量指数
只能退 ⊤。**但"能写出来"只是一半**：判阶必须跟着换成 log 域
（`nⁿ → n log n`、`2ⁿ → n`、`n^10 → 10 log n`），否则三者会挤进同一档。
log 变换在 `cost_domain/logdom.py`（`to_log`），`compare` 只在**任一侧含符号
指数**时才走那条路——不含的既有路径逐字节不变。

**形状**（不与既有形状冲突，这是唯一一处"`value=None`"的 pow）：

    Cost("pow", Fraction(p, q), (base,))     ← 常数指数（旧形状，一字不变）
    Cost("pow", None, (base, exp_cost))      ← 符号指数（`exp_cost` 也是 Cost）

**为什么 `value=None` 而不是新开一个 kind**：`render` / `to_dict` /
`variables` / `evaluate` / `substitute_*` 都要认识它，新 kind 意味着这六处
各加一条分支，而且 `_profile` 还得再记一个"家族外"；把符号指数当成
"指数位置上的另一个操作数"则所有遍历 `args` 的代码（`variables`、
`_collect_vars`、`substitute_*`、`par_blocks`）**自动**看得到它。
`value` 仍然是"常数指数"，所以旧形状的 `value.numerator` 一处都不用改。

**为什么只有这些（现在是十种）**：成本上界只会"往上加、取更大、重复"。
减法、比较都不会出现在上界里；**除法是唯一的例外**（2026-11-09）：数论里
最核心的量级就是 `π(x) ≈ x / log x`，没有除法连"`n / log n` 的阶"都写不出来。
但它被**严格限制**：只允许"分母是 log 的幂"或"分母是常数"，分母是尺寸量
（`n / m`）或 ⊤ 一律退 ⊤ —— 见 `Cost.div` 与 `cost_check._profile` 的规则表。
少一种成分，就少一处可能的错；除法进来的代价是 `_profile` 多三条边界规则。

**`Pow` 为什么必须有**：`√n`、`n^(2/3)` 这类阶是真的会出现（数论、分块、
树上倍增的某些界），而旧域只能表达整数次幂——写 `O(sqrt(n))` 会被
target 解析器判成"认不出"，于是契约**整个不生效**（`tests/cases/test_cost_orders.py`
的分数阶断言就是这一条的回归测试）。**常数**指数只允许**非负有理数**：
成本恒非负且随尺寸单调不减，负指数（`n^-1`）不是任何成本的上界。
**符号**指数（`nⁿ`、`2ⁿ`）是 2026-11-12 加的，见上面的形状说明；它只在
κ 侧由成本分析产生（`.tu` 的 target 里仍只收字面量指数——那里的阶是一个
"关于指数的表达式"，没有 target 语法可写，也没有 log 域之外的判定口径）。

**`Par` 为什么不合成 `Add`**（2026-11-09）：`Σ_i κ_i` 在**阶**上与 `max_i 阶(κ_i)`
等价（B-24），所以过去的 κ 只留一个和就够判阶了。但那样**任务结构就丢了**：
既拿不到"最慢的那个任务"的表达式，也没法给大小核算墙钟。`Par` 保留了分量，
`value`（数值口径）仍是 Σ —— 与 B-24 一字不差；只是**表达式**不再被压平。
它**机器无关**：核数/核权重只在报告层（`par.py` 的 `wall_est`）出现。

**三条化简规则（规格 T13-c 与 §7.3 的可执行形式）**

  1. 常数能合并就合并：`Add(1, 1) = 2`
  2. 常数与 Size 能比就比：`Max(3, 5) = 5`，`Max(3, n) = Max(3, n)`（留原样）
  3. `Mul(0, a) = 0`，`Mul(1, a) = a`，`Add(0, a) = a`

**`max` 的取舍**：能比就比，比不了**保留原样，不硬猜**。
判断"谁更快"只认三条增长速度顺序：常数 < log2 < 线性。其余一律保留 `max`。
"""

from fractions import Fraction
import math
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------- 饱和
#: **饱和阈值**（2026-11-12）：值超过它就返回"极大"标记，不再真算。
#:
#: 为什么是 1e18：与仓库既有的"大到不可判"口径一致（`dsh.py` 的
#: `bound >= 1e18` 判"不可判定"、`html_report.py` 的 `float(kap) < 1e18`）。
#: **它不影响任何判定**——判阶只看 `order_of` 的三元组；它只保护两处：
#:
#:   1. `Cost.pow` 的常数折叠：`4096^4096` 有 15,000 位十进制，**不许构造**；
#:   2. `cost_check.evaluate`：`pow` 用 log 空间先判大小，超了就返回这个值。
#:
#: `mul` / `add` **不做**饱和：`LADDER` 上界是 2^40，而所有天文数字都只能
#: 从 `pow` 出来（`pow` 已封顶），其余节点的浮点运算到不了溢出。少动一处，
#: 既有路径（比值那一栏）就逐字节不变。
MAG_CAP = 1e18

#: `log2(MAG_CAP) ≈ 59.79`：算 `pow` 的位数时用它当阈值（不必真算结果）。
_LOG2_CAP = math.log2(MAG_CAP)


def _pow_over_cap(v: Fraction, e: Fraction) -> bool:
    """`v ^ e` 会不会超过 `MAG_CAP`？**先估算，不构造**。

    `MAG_CAP` 是"极大"的标记值，所以"超过 1e18"就是"饱和"的判据；
    `v^e ≤ 1e18 ⟺ e·log2(v) ≤ log2(1e18)`。估算只用浮点，避免
    `4096^4096` 这类 15,000 位的中间结果。
    """
    try:
        fv = float(v)
    except (OverflowError, ValueError):
        return True
    if fv <= 1.0:
        return False
    try:
        return float(e) * math.log2(fv) > _LOG2_CAP
    except (OverflowError, ValueError):
        return True


# ---------------------------------------------------------------- 符号值

#: **哨兵段的边界**。`?!` 的承诺里用 `[-PIECE_BIG, lo-1]` 与 `[hi+1, PIECE_BIG]`
#: 两段来表达"低于下界跑第一个候选 / 高于上界跑最后一个"，
#: 这样不必给 `Cost.piece` 加第二个 fallback 参数（那要动九处）。
PIECE_BIG = 1 << 62


#: 这类 `sym` 名字里带运算符（`hi - lo`）——乘/加/除/幂里必须加括号，
#: 否则 `(hi - lo)×(hi - lo)` 会印成 `hi - lo×hi - lo`（另一个式子）。
_COMPOUND_CHARS = " +-*/×÷"


def _compound(c) -> bool:
    """渲染结果是不是**带运算符的整体**（乘 / 加 / 除 / 幂里要加括号）。

    只有**派生尺寸量**（`Cost("sym", "hi - lo")`）会是这种形状：
    容器尺寸印成 `n(xs)`、标量形参印成裸名，都不需要括号。
    `log2(hi - lo)` **不用**额外括号——函数自己的括号已经框住了。
    """
    if getattr(c, "kind", "") != "sym":
        return False
    name = str(getattr(c, "value", "") or "")
    return any(ch in name for ch in _COMPOUND_CHARS)


class Cost:
    """符号域的一个成本值。不可变。"""

    __slots__ = ("kind", "value", "args")

    def __init__(self, kind: str, value=None, args=()):
        self.kind = kind
        self.value = value
        self.args = tuple(args)

    # ---------- 构造
    @staticmethod
    def num(n: int) -> "Cost":
        """常数节点。**有理数会原样保留**（`Fraction(3,2)` 存成 3/2，不截断）。

        为什么不是 `int(n)`（2026-11-09，除法之后）：`div(num(3), num(2))`
        会产生 `3/2`，旧实现 `int()` 会把它悄悄记成 **1**——那是**低估**，
        而 `Cost.num` 是 `add` / `mul` / `pow` 的公共出口。
        整数仍然存成 `int`，所以既有的 `render` / `to_dict` 输出一字不变。
        """
        if isinstance(n, Fraction):
            return Cost("num", int(n) if n.denominator == 1 else n)
        return Cost("num", int(n))

    @staticmethod
    def size(name: str) -> "Cost":
        return Cost("size", name)

    @staticmethod
    def add(a: "Cost", b: "Cost") -> "Cost":
        # **⊤ 会吸收一切**：`⊤ + 1 = ⊤`。否则 "1 + ⊤ + 1" 会让上层
        # `is_top()` 判不出来（`read_file` 的成本就该是 ⊤）。
        if a.kind == "top" or b.kind == "top":
            return Cost.top()
        if a.kind == "num" and a.value == 0:
            return b
        if b.kind == "num" and b.value == 0:
            return a
        if a.kind == "num" and b.kind == "num":
            return Cost.num(a.value + b.value)
        if a.kind == "piece" or b.kind == "piece":
            if a.kind == "piece" and b.kind == "piece":
                z = _zipper(a, b, lambda ca, cb: Cost.add(ca, cb))
                if z is not None:
                    return z
                return _raw_max(a, b)
            if a.kind == "piece":
                return _map_segments(a, lambda c: Cost.add(c, b))
            return _map_segments(b, lambda c: Cost.add(a, c))
        return Cost("add", None, (a, b))

    @staticmethod
    def mul(n: "Cost", a: "Cost") -> "Cost":
        if n.kind == "num":
            if n.value == 0:
                return Cost.num(0)
            if n.value == 1:
                return a
            if a.kind == "num":
                return Cost.num(n.value * a.value)
        if a.kind == "num" and a.value == 0:
            return Cost.num(0)
        # **与除法的约分**（2026-11-09）：`(n / log n) × log n = n`。
        # 这是**精确等式**（渐近地看，分母 > 0 之后），不是"猜"：
        # 同一个表达式同时出现在分子与分母上，约掉它不改变量级。
        # 不做这一条也不影响判阶（`_profile` 的 log 因子相加仍会算出 (1,0)），
        # 但它让 `n / log n × log n` 打印成 `n`，报告里不会出现假的 `÷`。
        if n.kind == "div" and _same(n.args[1], a):
            return n.args[0]
        if a.kind == "div" and _same(a.args[1], n):
            return a.args[0]
        # **有理次幂的乘法**（2026-11-09）：同底幂相乘 = 指数相加。
        # `sqrt(n) × sqrt(n) = n`、`sqrt(n) × n = n^(3/2)`。
        # **只在至少一边是 `pow` 时才合并**：`n × n` 保持原样（既有 κ 的
        # 渲染一字不变，golden 不动），判阶本来就走 `_profile` 的指数相加。
        if n.kind == "pow" or a.kind == "pow":
            pn, pa = _power_parts(n), _power_parts(a)
            if pn is not None and pa is not None and _same(pn[0], pa[0]):
                return Cost.pow(pn[0], pn[1] + pa[1])
        if n.kind == "piece" or a.kind == "piece":
            # 两个分段相乘：断点求并后逐段乘（乘积仍分段）
            if n.kind == "piece" and a.kind == "piece":
                z = _zipper(n, a, lambda x, y: Cost.mul(x, y))
                if z is not None:
                    return z
                return _raw("mul", n, a)
            # 分段 × 变量：每段乘的东西不同，形状保持不住 → 退回普通节点
            # （**数值仍精确**：evaluate 会递归进参数，分段照样按 n 取值）
            if n.kind == "piece":
                return _raw("mul", n, a)
            if n.kind == "num":
                return _map_segments(a, lambda c: Cost.mul(n, c))
            return _raw("mul", n, a)
        return Cost("mul", None, (n, a))

    @staticmethod
    def div(a: "Cost", b: "Cost") -> "Cost":
        """**除法**：`a / b`。只为一件事存在 —— `π(x) ≈ x / log x` 这类量级。

        形状：`Cost("div", None, (a, b))`；打印成 `n(xs) / log2(n(xs))`。

        **为什么允许除法**（2026-11-09）：`Cost` 原先刻意没有除法（"上界只会
        往上加"），于是 `target { time: { max: O(n / log(n)) } }` **写不出来**——
        target 解析层认不出，契约整个不生效。数论/筛法里 `n / log n` 是最核心的
        一档，缺了它，`Cost` 连"比 `n` 小一点"都表达不了。

        **化简（逐条，其余一律保留结构）**：

          · `a / 1 = a`
          · `num(c) / num(d) = num(c/d)`（**d ≠ 0**；d = 0 时保留节点，
            求值得 `inf` —— 除零不是"可以偷偷折成 ⊤"的东西，要看得见）
          · `a / a = 1`（同尺寸量：`n / n`）
          · `(x × b) / b = x`（约掉分子里的同因子）
          · `⊤ / x = ⊤`、`x / ⊤ = ⊤`（上界未知 → 整式未知，**不许猜**）

        **刻意不化简的例子**：`n / log n`（这正是要表达的东西）、
        `n / 2`（保留系数，判阶时按常数分母处理）、`(a + b) / c`。

        **为什么 `x / ⊤ = ⊤` 而不是 `0`**：分母未知，可能是一个很小的量
        （`log n`），那商就比 `x` 还大——按 0 算会**低估**，上界不成立。
        """
        if b.kind == "num" and b.value == 1:
            return a
        if a.is_top() or b.is_top():
            return Cost.top()
        if a.kind == "num" and b.kind == "num" and b.value != 0:
            try:
                # `Fraction` 能直接吃 `int` 与 `Fraction`（`num` 的 value 两者都可能是）
                v = Fraction(a.value) / Fraction(b.value)
            except (TypeError, ValueError, ZeroDivisionError):
                return Cost.top()
            # `Cost.num` 会把整数折成 int、分数原样留下（**不截断**）
            return Cost.num(v)
        # **`0 / 0` 不许折成 1**：分子分母都是常数 0 时留在节点里，
        # 求值得 inf（除零），是看得见的错，不是"约掉等于 1"。
        _b_is_zero = b.kind == "num" and b.value == 0
        if not _b_is_zero and _same(a, b):
            return Cost.num(1)
        if a.kind == "mul":
            if _same(a.args[0], b):
                return a.args[1]
            if _same(a.args[1], b):
                return a.args[0]
        return Cost("div", None, (a, b))

    @staticmethod
    def max(a: "Cost", b: "Cost") -> "Cost":
        # **⊤ 必须被吸收**：`max(⊤, x) = ⊤`。
        # 以前它返回 `max(⊤, x)` 节点且 `is_top()` 为 False——下游会把
        # "无法判定"当成"有界"，这是最危险的一类静默。
        if a.is_top() or b.is_top():
            return Cost.top()
        if a.kind == "num" and b.kind == "num":
            return Cost.num(max(a.value, b.value))
        # **`max(0, x) = x`**（E4）：成本恒非负，0 是这一格的底。
        # 不化简的话，`if (c) { i = xs.length() }` 合并出 `max(0, n(xs))`，
        # 而循环上界的提取器认不出 `max` 节点，会退回符号 `n(i)` → 判决 `unknown`。
        if a.kind == "num" and a.value == 0:
            return b
        if b.kind == "num" and b.value == 0:
            return a
        if _same(a, b):
            return a
        if a.kind == "piece" or b.kind == "piece":
            if a.kind == "piece" and b.kind == "piece":
                z = _zipper(a, b, lambda ca, cb: Cost.max(ca, cb))
                if z is not None:
                    return z
                return _raw_max(a, b)
            if a.kind == "piece" and b.kind != "piece":
                return _map_segments(a, lambda c: Cost.max(c, b))
            if b.kind == "piece" and a.kind != "piece":
                return _map_segments(b, lambda c: Cost.max(a, c))
            return _raw_max(a, b)
        faster: Optional[Cost] = _strictly_faster(a, b)
        if faster is not None:
            return faster
        return Cost("max", None, (a, b))

    @staticmethod
    def par(tasks) -> "Cost":
        """**并行块**：`parallel { {t1} … {tn} }` 的 κ，保留每个任务的分量。

        ```
        value(par) = Σ_i value(κ_i)        分配 / 总量口径（B-24 的 Σ）
        order(par) = max_i order(κ_i)      判阶口径（B-24 的 max）
        ```

        **两条口径都是 B-24 原文**，不是新发明：规格说 κ MUST 为各任务之和，
        且「判阶时 MUST 等价于 `max_i 阶(κ_i)`」——因为 `Σ O(f_i) = O(max_i f_i)`。
        以前只留 Σ，判阶靠这个恒等式**间接**成立；现在把分量留下，
        两条口径都能**直接读**，而数值一个都没变。

        **不变量**（构造时保证）：

        - `par()`（零任务）→ `0`。空并行块不花时间。
        - **任一分量是 ⊤ → 整块 ⊤**。有一个任务的上界不知道，Σ 与 max 都不知道；
          直接折成 ⊤ 而不是"留个带 ⊤ 分量的 par"，是为了让 `Cost.add` 的
          `kind == "top"` 吸收规则继续生效（否则 `add(par_with_top, x)` 会漏判）。
        - **一个任务也保留 par 节点**（不化简成那一个分量）。代价是表达式里多一层，
          好处是 `cost --cores=` 的视图对"单任务并行"也说得出一句话；
          数学上没有代价：`order(par(a)) = order(a)`、`value(par(a)) = value(a)`。

        与既有代数的对接（逐处见各自的注释）：

        - `add`/`mul`/`max`/`log2`：当成普通节点，**不特判**——
          `add(par, c)` 的阶 = max(阶 par, 阶 c)，值 = Σ + c，两条都对；
          `mul(n, par)` 的阶 = 阶 n + max 阶（次数相加），值 = n·Σ，也对。
        - `_profile`（判阶）：`max_i profile(κ_i)`；任一分量判不了 → `None`（不猜）。
        - `evaluate`（数值）：Σ（与 `value(par)` 同口径）。
        - `render`：`par(a, b, …)`——**不退化成一个和**，任务结构看得见。
        - `_rank`（只被 `piece` 用）：`max_i rank(κ_i)`。
        - `substitute_sizes`：逐分量递归（否则调用点换尺寸会**漏掉**任务内部的名字，
          那是低估 → 上界不成立）。
        """
        items = tuple(tasks)
        if any(t.is_top() for t in items):
            return Cost.top()
        if not items:
            return Cost.num(0)
        return Cost("par", None, items)

    @staticmethod
    def log2(a: "Cost") -> "Cost":
        if a.kind == "num":
            if a.value <= 1:
                return Cost.num(0)
            if a.value == 2:
                return Cost.num(1)
        if a.kind == "piece":
            # **log 不动断点**：`log2(f(n))` 在 f 的每个分段上仍是同一个分段，
            # 逐段取 log 即可（数值精确）。早先写成"退回普通节点"是过于保守。
            return _map_segments(a, Cost.log2)
        return Cost("log2", None, (a,))

    @staticmethod
    def loglog(a: "Cost") -> "Cost":
        """**loglog 阶**：`log2(log2(a))`，印成 `log2(log2(n(xs)))`。

        **不新增 `kind`**（2026-11-09）：`Log2` 的嵌套已经能打印、能求值
        （`evaluate` 递归进 `args`），judge（`cost_check._profile`）认这一层
        嵌套就是 `(0, 0, 1)`。为它单开一个节点只会让 `render` / `to_dict` /
        `substitute_sizes` / `_same` 各多一条分支，换不来任何表达能力。

        这个构造器只是**给这层嵌套一个说得出口的名字**——测试与报告里写
        `Cost.loglog(n)` 比每处手写两层 `Cost.log2` 清楚。化简跟着 `Log2` 走：
        `loglog(常数≤1) = 0`、`piece` 逐段做。

        **`⊤ → ⊤`**：上界未知的量取 loglog 仍然未知。若保留成 `log2(log2(⊤))`
        节点，`evaluate` 会把 `log2(inf)` 算成 `0`（那里为了不崩把非正/∞
        映成 0），于是一个未知上界会在数值兜底里**冒充常数**——这正是
        "κ 必须是上界"要堵的静默。这里显式吸收。
        """
        if a.is_top():
            return Cost.top()
        return Cost.log2(Cost.log2(a))

    @staticmethod
    def pow(base: "Cost", exp) -> "Cost":
        """**幂**：`base ^ exp`。指数有两种，**形状不同但互不冲突**：

            Cost("pow", Fraction(p, q), (base,))     ← 常数指数（旧形状，逐字不变）
            Cost("pow", None, (base, exp))           ← 符号指数（2026-11-12）

        `exp` 可以是 `Fraction` / `int`（常数指数）或 `Cost`（符号指数，
        `nⁿ`、`2ⁿ`）。`Cost.num` 当指数传进来时**归一成常数指数**——它是
        "指数位置上的常数"，不是"符号"。

        **常数指数**必须非负有界（成本随尺寸单调不减）：负指数不是任何
        成本的上界，退回 `⊤`（"判不了"，不猜）。**符号指数**不检查符号：
        指数位置上的 `Cost` 是"尺寸量/计数"（`n`、`n/2`），由构造保证非负；
        真出现"负的 Cost 指数"（不该有）也会让 `to_log` 的 log 形式变负阶。

        化简（逐条；**能化简就化简，化不了就留节点，不许退 ⊤**）：

          · `x^0 = 1`、`x^1 = x`
          · `pow(pow(x, e1), e2) = pow(x, e1·e2)`（成本恒非负，幂可结合；
            符号指数也照做：`(n^n)^(1/2) = n^(n/2)`）
          · `0^e = 0`、`1^e = 1`（e > 0）
          · `k^(p/1) = k^p`（整数底整数指数 → 整数）
          · `k^(p/q)` 恰好是整数时也折成整数（`4^(1/2) = 2`）
          · **折叠有上界**：结果超过 `MAG_CAP`（1e18）就**不折叠**，留节点
            —— 否则 `Cost.pow(num(4096), 4096)` 会在这里构造一个 15,000 位的
            整数（`evaluate` 那边同样饱和，见本模块的 `MAG_CAP` 与
            `cost_check._pow_value`）。
          · `⊤^e = ⊤`（"上界未知"的幂仍未知，不许当成有界）
          · `piece` 逐段做幂（与 `log2` 同一处理：不动断点）

        **符号指数为什么只留节点**：它的阶要靠 log 域（`log(a^b) = b·log a`）
        才比得出来，见 `cost_domain/logdom.py`。表达式层没有别的化简可做。

        **未化简的形状不是错**：`2^(1/2)` 保留成节点，`evaluate` 会算出浮点。
        """
        if isinstance(exp, Cost):
            if exp.kind == "num":
                # 指数位置上的常数 → 走常数分支（形状与旧实现完全一致）
                exp = Fraction(exp.value)
            else:
                return Cost._pow_symbolic(base, exp)
        try:
            e = exp if isinstance(exp, Fraction) else Fraction(exp)
        except (TypeError, ValueError, ZeroDivisionError):
            return Cost.top()
        if e < 0:
            # 负指数 = 随 n 变小，不是成本上界。**不猜**。
            return Cost.top()
        if e == 0:
            return Cost.num(1)
        if e == 1:
            return base
        if base.kind == "top":
            return Cost.top()
        if base.kind == "pow":
            # `(x^e1)^e2 = x^(e1·e2)`：成本值恒非负，这条对实数幂成立。
            if base.value is None:
                # 里层是符号指数：指数的乘积**也是 Cost**（`e1·e2`）。
                return Cost.pow(base.args[0], Cost.mul(base.args[1], Cost.num(e)))
            return Cost.pow(base.args[0], base.value * e)
        if base.kind == "piece":
            return _map_segments(base, lambda c: Cost.pow(c, e))
        if base.kind == "num":
            # **用 `Fraction` 而不是 `int`**：`div` 引入后常数可能是 `3/2`，
            # `int()` 会把它截成 1，`(3/2)^2` 就算成 1（低估）。
            v = Fraction(base.value)
            if v < 0:
                # 负底数的分数幂不是实数。成本域里不该出现负常数，
                # 真出现了也只说"判不了"——**不猜**。
                return Cost.top()
            if v == 0:
                return Cost.num(0)
            if v == 1:
                return Cost.num(1)
            # **折叠的上界**（2026-11-12）：`4096^4096` 有 15,000 位十进制。
            # 先估算位数，超了就直接留节点——**不许真构造那个大整数**。
            if _pow_over_cap(v, e):
                return Cost("pow", e, (base,))
            if e.denominator == 1:
                return Cost.num(v ** int(e.numerator))
            try:
                exact = v ** float(e)
                fv = float(exact)
                # **精确但太大也不折**：`Cost.num` 会成为一个巨大整数，
                # 而 `evaluate` 的 `float(value)` 会 OverflowError。
                if fv != float("inf") and fv <= MAG_CAP and fv.is_integer():
                    return Cost.num(int(exact))
            except (OverflowError, ValueError, TypeError):
                pass
        return Cost("pow", e, (base,))

    @staticmethod
    def _pow_symbolic(base: "Cost", exp: "Cost") -> "Cost":
        """**符号指数**：`base ^ exp`，`exp` 是一个 `Cost`（尺寸量 / 计数式）。

        化简（与常数指数那套同源，逐条）：

          · `⊤^e = ⊤`、`a^⊤ = ⊤`（任一侧未知就不猜）
          · `x^0 = 1`、`x^1 = x` —— 指数是 `Cost.num` 时已在 `pow` 里归一，
            这里只兜 `num` 形状的 `Cost`（`_exp_num`）
          · `0^e = 0`、`1^e = 1`
          · **常数底 ≤ 1 一律 ⊤**：`a<1` 时 `a^b` 随 b **递减**，不是任何成本
            的上界；`a=1` 恒 1 已在上面化简。负底数同样 ⊤（不猜）。
          · `(x^e1)^e2 = x^(e1·e2)`（两个指数都是 Cost 时相乘）
          · `piece` 底数逐段做幂（不动断点）

        **不化简的形状照留**：`n(xs)^n(xs)` 是一个合法节点，`evaluate` 会
        饱和求值，判阶走 `to_log`。
        """
        if base.kind == "top" or exp.kind == "top":
            return Cost.top()
        _e = _exp_num(exp)
        if _e is not None:
            return Cost.pow(base, _e)          # 指数其实是常数：走常数分支
        if base.kind == "num":
            v = Fraction(base.value)
            if v == 0:
                return Cost.num(0)
            if v == 1:
                return Cost.num(1)
            # `0 < v < 1`（递减）、`v < 0`（非实数幂）都不是成本上界。
            if v < 1:
                return Cost.top()
        if base.kind == "pow":
            inner = base.args[1] if base.value is None else Cost.num(base.value)
            return Cost.pow(base.args[0], Cost.mul(inner, exp))
        if base.kind == "piece":
            return _map_segments(base, lambda c: Cost.pow(c, exp))
        return Cost("pow", None, (base, exp))

    @staticmethod
    def sqrt(base: "Cost") -> "Cost":
        """`√base`：就是 `pow(base, 1/2)`（打印成 `sqrt(...)`）。"""
        return Cost.pow(base, Fraction(1, 2))

    @staticmethod
    def top() -> "Cost":
        """⊤：上界未知。"""
        return Cost("top")

    @staticmethod
    def piece(var: str, segments, fallback: "Cost") -> "Cost":
        """**分段成本式**：`n(var)` 落在哪一段，就用那一段的式子。

        为什么需要它（2026-11-01）：`?!` 的承诺如果只写 `max(甲,乙)`，
        就被**最慢候选的阶**绑住（`count` 的 `O(n²)` 候选只在 n≤12 用，
        却把整条线抬成 `O(n²)`）。分段能只在该段用该候选的式子。

        代价（必须写在这里）：**分段只在"运行期真的按同一份分段选候选"时才成立**。
        表与本分段不同源时它就是**假上界**——`gap` 曾经这样破界过
        （n=8192 承诺 426,001 / 实测 729,109）。

        不变量（构造时检查）：段按 lo 升序、首尾相接、每段成本本身不含 piece。
        """
        segs = tuple((int(lo), int(hi), c) for lo, hi, c in segments)
        if not segs:
            return fallback
        for i, (lo, hi, c) in enumerate(segs):
            if lo > hi:
                raise ValueError(f"分段区间非法：[{lo},{hi}]")
            if c.kind == "piece":
                raise ValueError("分段里不许再套分段（代数运算应退回 max）")
            if i and segs[i - 1][1] + 1 != lo:
                raise ValueError(
                    f"分段不连续：上一段结束 {segs[i-1][1]}，这一段从 {lo} 开始")
        return Cost("piece", (var, segs), (fallback,))

    # ---------- 查询
    def is_top(self) -> bool:
        if self.kind == "piece":
            _, segs = self.value
            return self.args[0].is_top() or any(c.is_top() for _, _, c in segs)
        return self.kind == "top"

    def is_const(self) -> bool:
        if self.kind == "piece":
            _, segs = self.value
            return self.args[0].is_const() and all(c.is_const() for _, _, c in segs)
        return self.kind == "num"

    def const_value(self) -> Optional[float]:
        """**能折成一个数吗**：能就返回它（`par` 取 Σ），否则 `None`。

        为什么与 `is_const()` 分开：`is_const()` 在 `cost_interp` 里被用来做
        "`inner.value == 0` 就跳过"这类**形状**判断（分配维度），那里只认 `num`
        最省事；而 `compare_exact`（分配维度的判据）需要的是**数值**。
        `par(2, 3)` 不是一个 `num` 节点，却确实是 5 —— 交给这里回答。

        **只认 num / par**：`piece`（分段）没有单一数值，`sym`/`size`/`top`
        更不是数。`piece` 的分段逐个都是常数时也返回 `None`——分配维度按
        "随规模增长"处理是**保守**的方向，不在这里猜。
        """
        if self.kind == "num":
            return float(self.value)
        if self.kind == "par":
            vals = [t.const_value() for t in self.args]
            if any(v is None for v in vals):
                return None
            return sum(vals)                       # type: ignore[arg-type]
        return None

    def variables(self) -> List[str]:
        out: List[str] = []
        _collect_vars(self, out)
        return sorted(set(out))

    def _resub_pow(self, f) -> "Cost":
        """`substitute_*` 系列共用的 pow 重建：底数（和符号指数）都过一遍 `f`。

        **符号指数也是 `args`**，所以四个 `substitute_*` / `rename_elems`
        都必须换它——漏一处的后果是"调用点替换后指数里留着别人的形参名"，
        求值时按 1 算 → 低估上界。常数指数的形状与旧实现逐字节相同。
        """
        base = f(self.args[0])
        if self.value is None:
            return Cost.pow(base, f(self.args[1]))
        return Cost.pow(base, self.value)

    def substitute_syms(self, table: Dict[str, "Cost"]) -> "Cost":
        """把 `sym(名字)` 换成 `table[名字]`（**标量形参的实参实例化**）。

        与 `substitute_sizes` 平行：那个换 `size`（容器尺寸），这个换 `sym`
        （标量形参 / 圈数符号）。用途只有一个——内联时把被调函数 κ 里的
        **标量形参**换成调用点的实参表达式（`append_range(…, 0, to.size())`
        的 `hi` → `n(to)`）。不换的话调用方 κ 里会留下一个**不属于它的名字**，
        值说明不了（`hi` 是哪个调用的 hi？），判定与求值都不可信。

        **只做结构替换，不做算术化简**：`table` 里的 Cost 已经是按实参表达式
        推出来的上界（`_count_of_expr`），换进去只会更大或不变。
        """
        if not table:
            return self
        if self.kind == "sym":
            got = table.get(str(self.value))
            return got if got is not None else self
        if self.kind in ("num", "top", "ext", "size"):
            return self
        if self.kind in ("add", "mul", "max", "div"):
            l = self.args[0].substitute_syms(table)
            r = self.args[1].substitute_syms(table)
            if self.kind == "add":
                return Cost.add(l, r)
            if self.kind == "mul":
                return Cost.mul(l, r)
            if self.kind == "div":
                return Cost.div(l, r)
            return Cost.max(l, r)
        if self.kind == "par":
            return Cost.par([t.substitute_syms(table) for t in self.args])
        if self.kind == "log2":
            return Cost.log2(self.args[0].substitute_syms(table))
        if self.kind == "pow":
            return self._resub_pow(lambda x: x.substitute_syms(table))
        if self.kind == "piece":
            var, segs = self.value
            return Cost.piece(
                var,
                [(lo, hi, c.substitute_syms(table)) for lo, hi, c in segs],
                self.args[0].substitute_syms(table))
        return self

    def rename_elems(self, mapping: Dict[str, str]) -> "Cost":
        """把 `L(名字)` 的**名字**换掉（`L(形参)` → `L(实参容器)`）。

        `substitute_sizes` 只认 `size(名字)`：`L(g)` 是 `sym` 节点（复合名），
        内联时不会被它换掉——不换的话调用方 κ 里会留下**被调函数的形参名**，
        求值侧查不到、判定侧尺寸量对不上。与 `_receiver_size_renames` 同一类修法。

        `mapping`：被调方容器名 → 调用方容器名。没命中的 `L(...)` 原样保留，
        **由调用方判泄漏**（`elem_names`）——这里不猜。
        """
        if not mapping:
            return self
        if self.kind == "sym":
            v = str(self.value or "")
            if v.startswith("L(") and v.endswith(")"):
                inner = v[2:-1]
                if inner in mapping:
                    return Cost("sym", f"L({mapping[inner]})")
            return self
        if self.kind in ("num", "top"):
            return self
        if self.kind in ("add", "mul", "max", "div"):
            l = self.args[0].rename_elems(mapping)
            r = self.args[1].rename_elems(mapping)
            if self.kind == "add":
                return Cost.add(l, r)
            if self.kind == "mul":
                return Cost.mul(l, r)
            if self.kind == "div":
                return Cost.div(l, r)
            return Cost.max(l, r)
        if self.kind == "par":
            return Cost.par([t.rename_elems(mapping) for t in self.args])
        if self.kind == "log2":
            return Cost.log2(self.args[0].rename_elems(mapping))
        if self.kind == "pow":
            return self._resub_pow(lambda x: x.rename_elems(mapping))
        if self.kind == "piece":
            var, segs = self.value
            return Cost.piece(
                var,
                [(lo, hi, c.rename_elems(mapping)) for lo, hi, c in segs],
                self.args[0].rename_elems(mapping))
        return self

    def substitute_elems(self, table: Dict[str, "Cost"]) -> "Cost":
        """把 `L(名字)` **整体**换成 `table[名字]`（元素长度上界的实例化）。

        与 `rename_elems`（只换名字）和 `substitute_sizes`（只换 `size`）平行：
        `L(g)` 是一个 **sym 节点**，前两者都碰不到它。调用点要把它换成推出来的
        上界（`L(g) → 7`）时只能走这一条。

        **方向**：`table` 里的值必须是 `L(名字)` 的**上界**（只大不小）——
        本方法只做结构替换，不做算术化简；换进去的式子更大或相等，所以仍是上界。
        没命中 / 值是 `None` 的 `L(...)` 原样保留，由调用方判泄漏（不猜）。
        """
        if not table:
            return self
        if self.kind == "sym":
            v = str(self.value or "")
            if v.startswith("L(") and v.endswith(")"):
                got = table.get(v[2:-1])
                if got is not None:
                    return got
            return self
        if self.kind in ("num", "top", "ext", "size"):
            return self
        if self.kind in ("add", "mul", "max", "div"):
            l = self.args[0].substitute_elems(table)
            r = self.args[1].substitute_elems(table)
            if self.kind == "add":
                return Cost.add(l, r)
            if self.kind == "mul":
                return Cost.mul(l, r)
            if self.kind == "div":
                return Cost.div(l, r)
            return Cost.max(l, r)
        if self.kind == "par":
            return Cost.par([t.substitute_elems(table) for t in self.args])
        if self.kind == "log2":
            return Cost.log2(self.args[0].substitute_elems(table))
        if self.kind == "pow":
            return self._resub_pow(lambda x: x.substitute_elems(table))
        if self.kind == "piece":
            var, segs = self.value
            return Cost.piece(
                var,
                [(lo, hi, c.substitute_elems(table)) for lo, hi, c in segs],
                self.args[0].substitute_elems(table))
        return self

    def elem_names(self) -> List[str]:
        """κ 里所有 `L(名字)` 的**名字**（判断内联后有没有留下形参名）。"""
        out: List[str] = []

        def walk(c):
            if c.kind == "sym":
                v = str(c.value or "")
                if v.startswith("L(") and v.endswith(")"):
                    out.append(v[2:-1])
            if c.kind == "piece":
                for _lo, _hi, sub in c.value[1]:
                    walk(sub)
            for a in getattr(c, "args", ()):
                walk(a)

        walk(self)
        return out

    # ---------- 替换（E19 / R0 共用的唯一机制）
    def substitute_sizes(self, table: Dict[str, "Cost"]) -> "Cost":
        """把成本式里的 `size(x)` 换成 `table[x]`。

        **为什么需要它**：被调函数的 κ 是用**它自己的形参名**写的，
        `while (v < E)` 体内的 κ 是用**局部变量**写的。两者在调用点/循环入口
        都要换成"调用者看得懂的尺寸"，否则成本式里会留下查不到的名字
        （求值时按 1 算 → 低估 → 上界不成立，见 E19 / E20）。

        **用构造器而不是直接 `Cost(kind, ...)`**：构造器会把常数折起来
        （`num × num` → 常数），替换后能继续化简。
        """
        if not table:
            return self
        k = self.kind
        if k == "size":
            return table.get(self.value, self)
        if k in ("num", "top"):
            return self
        if k in ("add", "mul", "max", "div"):
            l = self.args[0].substitute_sizes(table)
            r = self.args[1].substitute_sizes(table)
            if k == "add":
                return Cost.add(l, r)
            if k == "mul":
                return Cost.mul(l, r)
            if k == "div":
                # **分子分母都要换**：漏了分母就等于除以一个查不到的名字
                # （求值按 1 算）——量级会被高估，但契约便不再是"这一个"式子。
                return Cost.div(l, r)
            return Cost.max(l, r)
        if k == "par":
            # **逐分量递归**：漏掉一个分量 = 任务内部留着一个查不到的名字，
            # 求值时按 1 算 → 低估 → 上界不成立（E19/E20 的同一类错）。
            return Cost.par([t.substitute_sizes(table) for t in self.args])
        if k == "log2":
            return Cost.log2(self.args[0].substitute_sizes(table))
        if k == "pow":
            # **底数里的尺寸名也要换**：漏掉它 = 求值时按 1 算 → 低估。
            # **指数里的也要换**（2026-11-12）：`pow(x, n(y))` 的指数也是
            # 一个尺寸量；漏了它，调用点替换后指数里留着被调方的形参名，
            # 求值时按 1 算 → 上界不成立。
            return self._resub_pow(
                lambda x: x.substitute_sizes(table))
        if k == "piece":
            var, segs = self.value
            return Cost.piece(var,
                              [(lo, hi, c.substitute_sizes(table)) for lo, hi, c in segs],
                              self.args[0].substitute_sizes(table))
        return self

    # ---------- 输出
    #: 内部 kind → 对外名字。**对外一律写完整单词，不写简写**——
    #: 这份产物是给人看、也给 agent 读的，`op`/`mul` 这类简写会逼人查表。
    _EXTERNAL_NAME = {
        "num": "constant",
        "size": "size_variable",
        "add": "addition",
        "mul": "multiplication",
        "div": "division",
        "max": "maximum",
        "par": "parallel",
        "log2": "logarithm_base_2",
        "pow": "power",
        "top": "unknown",
        "piece": "piecewise",
    }

    def to_dict(self) -> dict:
        """机器可读的成本签名（喂给 agent 的预算先验用，切口 1）。

        形如：

            {"operator": "addition",
             "operands": [{"operator": "constant", "value": 4}, ...]}

        **键与取值都用完整单词**：`operator` / `operands` / `constant` /
        `multiplication` / `logarithm_base_2` / `unknown`（即 ⊤）。
        """
        name = self._EXTERNAL_NAME.get(self.kind, self.kind)
        if self.kind == "num":
            # **分数常数的 JSON 表示**（2026-11-09）：`div` 之后的常数可能是
            # `Fraction`，`json.dumps` 不认它。整数仍输出 `int`（旧输出逐字不变），
            # 分数输出 `float`（`1.5`）——精确的有理数在 `render` 里看得到。
            v = self.value
            if isinstance(v, Fraction):
                v = int(v) if v.denominator == 1 else float(v)
            return {"operator": name, "value": v}
        if self.kind == "size":
            return {"operator": name, "variable": self.value}
        if self.kind == "top":
            return {"operator": name}
        if self.kind == "piece":
            var, segs = self.value
            return {"operator": name,
                    "variable": var,
                    "segments": [{"lo": lo, "hi": hi, "cost": c.to_dict()}
                                 for lo, hi, c in segs],
                    "outside": self.args[0].to_dict()}
        if self.kind == "par":
            # **两条口径都写出来**：读 JSON 的 agent 只看 `operator: parallel`
            # 不会知道"阶取 max、数值取 Σ"——那正是这次要保住的信息。
            return {"operator": name,
                    "order": "maximum",
                    "value": "sum",
                    "operands": [a.to_dict() for a in self.args]}
        if self.kind == "pow":
            # **指数必须写出来**：只写 operands 的话 `sqrt(n)` 与 `n²` 的
            # JSON 一模一样，读的人无从分辨。写成 `"1/2"` 是精确的有理数。
            #
            # **符号指数**（2026-11-12）没有"数值"可写成字符串，所以
            # `exponent` 这一格直接放**指数子树的 JSON**（对象）——读的人
            # 一眼看得出"指数不是常数"。常数指数的输出逐字不变。
            if self.value is None:
                return {"operator": name,
                        "exponent": self.args[1].to_dict(),
                        "operands": [self.args[0].to_dict()]}
            e = self.value
            return {"operator": name,
                    "exponent": f"{e.numerator}/{e.denominator}",
                    "operands": [a.to_dict() for a in self.args]}
        return {"operator": name,
                "operands": [a.to_dict() for a in self.args]}

    @staticmethod
    def from_dict(data: dict) -> "Cost":
        """`to_dict()` 的**逆**：机器可读的成本签名 → `Cost`。

        **为什么需要它**（2026-11-12）：符号指数在 JSON 里的 `exponent` 是
        一个**对象**（指数子树）。只读 JSON 的消费者（agent / 报告 / 跨工具
        管道）要能把它读回一个 `Cost`，否则"机器可读的成本签名"到 `n^n`
        这一档就断了——`render` 的逆（`tools/cost_baseline.py::parse_kappa`）
        只认常数指数。

        只认 `to_dict()` 会产出的形状；认不出就抛 `ValueError`（**不猜**）。
        `operator` 用完整单词（与 `_EXTERNAL_NAME` 一致）：
        `constant` / `size_variable` / `addition` / `multiplication` /
        `division` / `maximum` / `parallel` / `logarithm_base_2` / `power` /
        `unknown` / `piecewise`。

        **`power` 的 `exponent`** 有两种：字符串 `"p/q"`（常数指数）或
        **对象**（符号指数的子树）。两种都读得回来。
        """
        if not isinstance(data, dict):
            raise ValueError(f"成本签名必须是对象：{data!r}")
        op = data.get("operator")

        def sub(key="operands"):
            raw = data.get(key)
            if raw is None:
                raise ValueError(f"{op!r} 缺少 {key}")
            return raw

        if op == "constant":
            v = data.get("value", 0)
            if isinstance(v, float):
                v = Fraction(v)
            try:
                return Cost.num(v)
            except (TypeError, ValueError):
                raise ValueError(f"常数不是数：{v!r}") from None
        if op == "size_variable":
            name = data.get("variable")
            if not isinstance(name, str):
                raise ValueError("size_variable 缺少 variable")
            return Cost.size(name)
        if op == "unknown":
            return Cost.top()
        if op == "logarithm_base_2":
            ops = sub()
            if len(ops) != 1:
                raise ValueError("logarithm_base_2 只该有一个操作数")
            return Cost.log2(Cost.from_dict(ops[0]))
        if op == "power":
            ops = sub()
            if len(ops) != 1:
                raise ValueError("power 只该有一个操作数（底数）")
            base = Cost.from_dict(ops[0])
            e = data.get("exponent")
            if isinstance(e, dict):
                return Cost.pow(base, Cost.from_dict(e))
            if isinstance(e, str) and "/" in e:
                p, q = e.split("/", 1)
                try:
                    return Cost.pow(base, Fraction(int(p), int(q)))
                except (TypeError, ValueError, ZeroDivisionError):
                    raise ValueError(f"指数不是有理数：{e!r}") from None
            if isinstance(e, int):
                return Cost.pow(base, Fraction(e))
            raise ValueError(f"power 的 exponent 认不出：{e!r}")
        if op in ("addition", "multiplication", "division", "maximum"):
            ops = sub()
            if len(ops) != 2:
                raise ValueError(f"{op} 只该有两个操作数")
            a, b = Cost.from_dict(ops[0]), Cost.from_dict(ops[1])
            if op == "addition":
                return Cost.add(a, b)
            if op == "multiplication":
                return Cost.mul(a, b)
            if op == "division":
                return Cost.div(a, b)
            return Cost.max(a, b)
        if op == "parallel":
            return Cost.par([Cost.from_dict(x) for x in sub()])
        if op == "piecewise":
            var = data.get("variable")
            if not isinstance(var, str):
                raise ValueError("piecewise 缺少 variable")
            segs = []
            for s in data.get("segments") or []:
                try:
                    segs.append((int(s["lo"]), int(s["hi"]),
                                 Cost.from_dict(s["cost"])))
                except (KeyError, TypeError, ValueError):
                    raise ValueError(f"piecewise 的段表坏了：{s!r}") from None
            return Cost.piece(var, segs, Cost.from_dict(data.get("outside")))
        raise ValueError(f"认不出的成本签名 operator={op!r}")

    def render(self) -> str:
        k = self.kind
        if k == "num":
            return str(self.value)
        if k == "size":
            return f"n({self.value})"
        if k == "sym":
            # **裸符号**：`external { read_file: O(F) }` 里的 `F`。
            # 不用 `n(F)`——那个写法读作"F 的规模"，而这里 F 本身就是"未知量"。
            #
            # **复合名原样印**（2026-11-10）：尺寸差 `hi - lo` 也是一个 `sym`
            # （派生尺寸量，不是容器，所以不能写成 `n(hi - lo)`）。
            # 名字里带运算符时，**在乘/加/除/幂的位子上要加括号**
            # （`(hi - lo)×(hi - lo)`）——那由 `_compound` 在那些渲染分支里管；
            # 这里保持原样，`log2(hi - lo)` 才不会变成 `log2((hi - lo))`。
            return str(self.value)
        if k == "ext":
            # **外部项的显式名字**：`ext(read_file)`。
            # 不用 ⊤：⊤ 说"整个上界不知道"，ext 说"除这一项，其余都算得出来"。
            return f"ext({self.value})"
        if k == "top":
            return "⊤"
        if k == "log2":
            return f"log2({self.args[0].render()})"
        if k == "pow":
            # **人读的写法**：`sqrt(n)`、`n^(2/3)`、`n^2`、`n(xs)^(n(xs))`。
            # 底数是加法/乘法/幂时加括号，否则 `n + 1^2` 会被读错。
            base = self.args[0]
            inner = base.render()
            if base.kind in ("add", "max", "mul", "pow", "log2") or _compound(base):
                inner = f"({inner})"
            if self.value is None:
                # **符号指数一律带括号**（2026-11-12）：`n(xs)^(n(xs))`。
                # 不写成 `n(xs)^n(xs)` 是因为 `2^n(xs)` 读起来像
                # `(2^n)(xs)`——指数是"整条成本式"时必须看得出边界。
                return f"{inner}^({self.args[1].render()})"
            e = self.value
            if e.denominator == 1:
                return f"{inner}^{e.numerator}"
            if e.numerator == 1 and e.denominator == 2:
                # 平方根是最常见的一档：写 `sqrt(...)`，不写 `^(1/2)`。
                return f"sqrt({base.render()})"
            return f"{inner}^({e.numerator}/{e.denominator})"
        if k == "add":
            # **复合 `sym` 也要括号**：`x + (hi - lo)` 若印成 `x + hi - lo`
            # 会被读成 `(x + hi) - lo`——同一个符号在乘/加里都是"一个整体"。
            left = self.args[0].render()
            right = self.args[1].render()
            if _compound(self.args[0]):
                left = f"({left})"
            if _compound(self.args[1]):
                right = f"({right})"
            return f"{left} + {right}"
        if k == "div":
            # **`n(xs) / log2(n(xs))`**：` / ` 两侧留空格，读起来是一目了然的分数；
            # 分母本身是乘法/加法/除法时加括号（`a / (b×c)`），否则 `a×b / c`
            # 会被读成别的意思。**`pow` / `log2` 不加**：`n / sqrt(n)` 已经无歧义。
            a, b = self.args
            left = a.render()
            right = b.render()
            if a.kind in ("add", "max", "div") or _compound(a):
                left = f"({left})"
            if b.kind in ("add", "max", "mul", "div") or _compound(b):
                right = f"({right})"
            return f"{left} / {right}"
        if k == "mul":
            n, a = self.args
            left = n.render()
            right = a.render()
            # `n × (a + b)` 要括号；`n × (m / log m)` 也加——虽然数学上与
            # `(n×m) / log m` 同值，但打印出来要看得出"这里有一层除法"。
            # **复合 `sym` 同理**：`(hi - lo)×(hi - lo)`，不印成
            # `hi - lo×hi - lo`（那是另一个式子）。
            if a.kind in ("add", "max", "div") or _compound(a):
                right = f"({right})"
            if n.kind in ("add", "max", "div") or _compound(n):
                left = f"({left})"
            return f"{left}×{right}"
        if k == "max":
            return f"max({self.args[0].render()}, {self.args[1].render()})"
        if k == "par":
            # **印出结构，不印和**：`par(3n + 5, n + 12, n)`。
            # 印成 `8n + 40` 就等于把这次要保住的信息又扔了。
            return "par(" + ", ".join(t.render() for t in self.args) + ")"
        if k == "piece":
            var, segs = self.value
            parts = []
            below = above = False
            for lo, hi, c in segs:
                # **哨兵段**（`PIECE_BIG`）：不印天文数字，印成 `n<…` / `n>…`。
                # 它们不是"真实区间"，而是"越界时跑哪个候选"的显式表达。
                if lo <= -PIECE_BIG // 2:
                    parts.append(f"n({var})<{hi + 1}: {c.render()}")
                    below = True
                    continue
                if hi >= PIECE_BIG // 2:
                    parts.append(f"n({var})>{lo - 1}: {c.render()}")
                    above = True
                    continue
                parts.append(f"{lo}≤n({var})≤{hi}: {c.render()}")
            tail = "" if (below and above) else f"；越界: {self.args[0].render()}"
            return "{" + "; ".join(parts) + tail + "}"
        return "?"

    def __repr__(self) -> str:
        return f"Cost({self.render()})"


#: **形状退回计数**：每次"分段保持不住、退回普通节点"都记一笔。
#: 为什么要计数：代数闭包会悄悄退化成 max（等于白做分段），
#: 所以测试要能断言"这 10 个样例的退回次数 ≤ N"。
_FALLBACKS = {"n": 0}

#: **单条 κ 最多几段**。超过就退回 `max`（sound，松）。
#:
#: 为什么要上限：代数运算会把两个分段"求并"，段数相加；再套几层就是几十段。
#: 几十段在语义上是**灾难**——分段的含义是"运行期在这里换候选"，
#: 而代数产生的断点运行期并不存在（它只是数学拼接）。段数一多，
#: 读的人会以为运行时在几十处切换；渲染、逐段比较、JSON 签名也都会膨胀。
MAX_SEGMENTS = 16


def fallback_count() -> int:
    return _FALLBACKS["n"]


def reset_fallback_count() -> None:
    _FALLBACKS["n"] = 0


def _raw(kind: str, a: Cost, b: Optional[Cost] = None) -> Cost:
    """构造一个**裸节点**（不做化简），用于"保持不住分段"的场合。

    注意：这不是"变松"——`evaluate` 会递归进参数，分段在参数里照样按 n 取值，
    所以数值上仍然精确。丢的只是"结果仍被识别为分段"这个形状。
    """
    _FALLBACKS["n"] += 1
    return Cost(kind, None, (a,) if b is None else (a, b))


def _raw_max(a: Cost, b: Cost) -> Cost:
    """两段结构不一致时的保守合并：`max(a, b)` 对两者都是上界。"""
    _FALLBACKS["n"] += 1
    return Cost("max", None, (a, b))


def _map_segments(p: Cost, f) -> Cost:
    """对每一段（含 fallback）套同一个运算。"""
    var, segs = p.value
    return Cost.piece(var, [(lo, hi, f(c)) for lo, hi, c in segs], f(p.args[0]))


def _seg_compatible(a: Cost, b: Cost) -> bool:
    """两段结构完全一致（同一个变量、同样的区间）才能逐段合并。"""
    if a.kind != "piece" or b.kind != "piece":
        return False
    va, sa = a.value
    vb, sb = b.value
    if va != vb or len(sa) != len(sb):
        return False
    return all((la, ha) == (lb, hb) for (la, ha, _), (lb, hb, _) in zip(sa, sb))


def _seg_of(segs, n: int) -> Optional[Cost]:
    """段表里覆盖 n 的那一段的成本。"""
    for lo, hi, c in segs:
        if lo <= n <= hi:
            return c
    return None


def _zipper(a: Cost, b: Cost, op) -> Optional[Cost]:
    """把两个分段**按断点求并**后逐段合并：`op(ca, cb)`。

    返回 None 表示合并不了（区间不同/有空隙/段数超过 `MAX_SEGMENTS`），
    调用方应退回 `max`（sound，只是松）。
    """
    if a.kind != "piece" or b.kind != "piece":
        return None
    va, sa = a.value
    vb, sb = b.value
    if va != vb:
        return None
    if (sa[0][0], sa[-1][1]) != (sb[0][0], sb[-1][1]):
        return None                      # 覆盖区间不同：不猜
    starts = sorted({lo for lo, _, _ in sa} | {lo for lo, _, _ in sb})
    end = sa[-1][1]
    parts = []
    for i, lo in enumerate(starts):
        hi = (starts[i + 1] - 1) if i + 1 < len(starts) else end
        if lo > hi:
            continue
        ca = _seg_of(sa, lo)
        cb = _seg_of(sb, lo)
        if ca is None or cb is None:
            return None
        parts.append((lo, hi, op(ca, cb)))
        if len(parts) > MAX_SEGMENTS:
            _FALLBACKS["n"] += 1
            return None
    if not parts:
        return None
    # 只剩一段也要保持成 piece：它的**越界行为**（fallback）与段内不同，
    # 直接返回那一段会把越界算错。
    return Cost.piece(va, parts, op(a.args[0], b.args[0]))


def _same(a: Cost, b: Cost) -> bool:
    if a.kind != b.kind:
        return False
    if a.kind in ("num", "size", "sym", "ext"):
        # **必须比 value**：`sym`/`ext` 的 args 为空，只比 args 会让
        # `max(sym a, sym b)` 判成"相等"并返回 a —— 静默丢掉一个分支，
        # `if (c) { read_file } else { write_file }` 会少算外部项（不是上界）。
        return a.value == b.value
    if a.kind == "pow":
        # **指数也是身份**：`pow(n, 1/2)` 与 `pow(n, 1/3)` 的 args 都是 `(n,)`，
        # 只比 args 会把 `sqrt(n) × n^(1/3)` 合并成 `pow(n, 1/2)` —— 低估。
        #
        # **符号指数**（`value is None`）：指数是 `args[1]`，也要比——否则
        # `n^n` 与 `n^10` 会被当成同一个式子（`log` 域判阶的第 9 条就靠这个）。
        if a.value is None:
            return (len(a.args) == 2 and len(b.args) == 2
                    and _same(a.args[0], b.args[0])
                    and _same(a.args[1], b.args[1]))
        if b.value is None:
            return False
        return a.value == b.value and _same(a.args[0], b.args[0])
    return len(a.args) == len(b.args) and all(_same(x, y) for x, y in zip(a.args, b.args))


def _exp_num(exp: "Cost") -> Optional[Fraction]:
    """`Cost` 形状的指数**其实是常数**时返回它的 `Fraction`，否则 `None`。

    只认 `Cost.num`：别的一律当符号指数（`n`、`n/2`、`k`）。这条让
    `Cost.pow(x, Cost.num(3))` 落到与 `Cost.pow(x, 3)` **同一个形状**上。
    """
    if exp is not None and exp.kind == "num":
        v = exp.value
        return v if isinstance(v, Fraction) else Fraction(v)
    return None


def _power_parts(c: Cost):
    """把一个乘因子看成 `(底数, 指数)`：`pow(x, e) → (x, e)`，尺寸/符号 → `(x, 1)`。

    `num` 与其余形状返回 `None`——把常数折进底数会让 `2 × sqrt(n)` 变成
    `sqrt(2n)` 之类的假合并（数学上等价，但会把系数混进阶的表达式里，
    读的人以为看到的是尺寸量）。**只合并"同底幂"这一种确定的情况。**

    **符号指数的幂返回 `None`**（2026-11-12）：指数不是 `Fraction`，
    `pn[1] + pa[1]` 那种"指数相加"在这里不成立（两个 Cost 相加要另走
    `Cost.add`，而 `n^n × n^n = n^(2n)` 的表达式层化简不值得为它加一条
    合并规则——判阶走 log 域，`mul` 的阶本来就是指数相加）。
    """
    if c.kind == "pow":
        if c.value is None:
            return None
        return c.args[0], c.value
    if c.kind in ("size", "sym"):
        return c, Fraction(1)
    return None


def _rank(c: Cost) -> Optional[int]:
    """增长速度的粗略排序：常数 0 < log2 1 < 线性 2 < 乘积 3 < 其它 None。"""
    if c.kind == "num":
        return 0
    if c.kind == "log2":
        return 1
    if c.kind in ("size", "ext"):
        return 2
    if c.kind == "sym":
        # **`k(f)` 不是尺寸，是「某个函数的成本」**——它有多大完全取决于
        # 调用点传进来的是谁（可能是 O(1)，也可能是 O(n²)）。按线性算就是
        # 给了一个**假上界**：实测 `apply(xs, sq)`（sq 是 O(x²)）判 ✓ 达成。
        # 其余 `sym`（如 `external { read_file: O(F) }` 里的 F）是作者声明的
        # 尺寸类量，仍按线性处理。
        if str(c.value or "").startswith("k("):
            return None
        return 2
    if c.kind == "mul":
        return 3
    if c.kind == "par":
        # `par` 的阶 = 各分量阶的最大值（B-24）。任一分量判不了 → 整块判不了。
        ranks = [_rank(t) for t in c.args]
        if any(r is None for r in ranks):
            return None
        return max(ranks) if ranks else 0
    if c.kind == "piece":
        _, segs = c.value
        ranks = [_rank(t) for _, _, t in segs] + [_rank(c.args[0])]
        if any(r is None for r in ranks):
            return None
        return max(ranks)
    return None


def _strictly_faster(a: Cost, b: Cost) -> Optional[Cost]:
    """a 是不是**确定**比 b 快（增长更慢）？是则返回 b，否则 None。

    只认**三条**确定的顺序，其余一律返回 None（保留 `max`，不硬猜）：

      常数 < log2(任何尺寸)         log2 随 n 增长，最终一定超过任何常数
      log2(n) < n                  显然
      n < n × k（k > 1）           显然

    **不认「常数 < 线性」**：常数可能是 1000，而 n 可能是 3，
    此时 max 的结果由谁大决定——**留着 `max` 才诚实**。
    """
    if a.kind == "num" and b.kind == "log2":
        return b
    if a.kind == "log2" and b.kind == "num":
        return a
    if a.kind == "log2" and b.kind == "size":
        return b
    if a.kind == "size" and b.kind == "log2":
        return a
    if a.kind == "size" and b.kind == "mul":
        k, inner = b.args
        if k.kind == "num" and k.value > 1 and inner.kind == "size" and inner.value == a.value:
            return b
    if b.kind == "size" and a.kind == "mul":
        k, inner = a.args
        if k.kind == "num" and k.value > 1 and inner.kind == "size" and inner.value == b.value:
            return a
    return None


def _collect_vars(c: Cost, out: List[str]) -> None:
    if c.kind == "size":
        out.append(c.value)
    if c.kind == "sym":
        # **标量形参的裸符号也是尺寸量**（`dim` / `k` / `hi`）。
        # `k(f)` 例外：它是函数参数的成本符号，不是尺寸。
        _v = str(c.value or "")
        if not _v.startswith("k("):
            out.append(_v)
    if c.kind == "piece":
        var, segs = c.value
        out.append(var)
        for _, _, s in segs:
            _collect_vars(s, out)
    for a in c.args:
        _collect_vars(a, out)


# ---------------------------------------------------------------- 便捷构造

def seq(*items: Cost) -> Cost:
    """顺序语句：全部相加。"""
    total = Cost.num(0)
    for it in items:
        total = Cost.add(total, it)
    return total


def ones(n: int) -> Cost:
    return Cost.num(n)


# ---------------------------------------------------------------- 自检

if __name__ == "__main__":
    n = Cost.size("xs")
    cases = [
        ("1 + 1", Cost.add(Cost.num(1), Cost.num(1)), "2"),
        ("0 + n", Cost.add(Cost.num(0), n), "n(xs)"),
        ("max(3, 5)", Cost.max(Cost.num(3), Cost.num(5)), "5"),
        ("max(3, n)", Cost.max(Cost.num(3), n), "max(3, n(xs))"),
        ("max(n, log2(n))", Cost.max(n, Cost.log2(n)), "n(xs)"),
        ("n × 1", Cost.mul(Cost.num(1), n), "n(xs)"),
        ("2 × n", Cost.mul(Cost.num(2), n), "2×n(xs)"),
        ("n × (a+1)", Cost.mul(Cost.size("xs"), Cost.add(Cost.size("xs"), Cost.num(1))), "n(xs)×(n(xs) + 1)"),
        ("log2(1)", Cost.log2(Cost.num(1)), "0"),
        ("log2(n)", Cost.log2(n), "log2(n(xs))"),
        # **loglog 不新增 kind**：就是两层 log2，打印与求值都现成（2026-11-09）。
        ("loglog(n)", Cost.loglog(n), "log2(log2(n(xs)))"),
        # **par 印结构不印和**：两个任务各 3 与 5，总量是 8，但 render 必须看得出
        # 是两个任务——这正是"最大核视图"要读的东西。
        ("par(3, 5)", Cost.par([Cost.num(3), Cost.num(5)]), "par(3, 5)"),
        # **除法**：`n / log n` 是 π(x) 那一档的核心写法（2026-11-09）。
        ("n / log n", Cost.div(n, Cost.log2(n)), "n(xs) / log2(n(xs))"),
        ("n / 1", Cost.div(n, Cost.num(1)), "n(xs)"),
        ("6 / 3", Cost.div(Cost.num(6), Cost.num(3)), "2"),
        ("n / n", Cost.div(n, n), "1"),
        ("(n × log n) / log n",
         Cost.div(Cost.mul(n, Cost.log2(n)), Cost.log2(n)), "n(xs)"),
        ("(n / log n) × log n",
         Cost.mul(Cost.div(n, Cost.log2(n)), Cost.log2(n)), "n(xs)"),
        ("⊤ / n", Cost.div(Cost.top(), n), "⊤"),
        ("n / ⊤", Cost.div(n, Cost.top()), "⊤"),
        # **符号指数**（2026-11-12）：形状 `Cost("pow", None, (base, exp))`。
        # 化简（`x^0` / `x^1` / `(x^a)^b` / `0^e` / `1^e` / `⊤^e`）逐条在这里。
        ("n^n", Cost.pow(n, n), "n(xs)^(n(xs))"),
        ("2^n", Cost.pow(Cost.num(2), n), "2^(n(xs))"),
        ("n^10（常数）", Cost.pow(n, 10), "n(xs)^10"),
        ("n^0", Cost.pow(n, Cost.num(0)), "1"),
        ("n^1", Cost.pow(n, Cost.num(1)), "n(xs)"),
        ("(n^n)^(1/2)", Cost.pow(Cost.pow(n, n), Fraction(1, 2)), "n(xs)^(n(xs)×1/2)"),
        ("0^n", Cost.pow(Cost.num(0), n), "0"),
        ("1^n", Cost.pow(Cost.num(1), n), "1"),
        ("⊤^n", Cost.pow(Cost.top(), n), "⊤"),
        ("n^⊤", Cost.pow(n, Cost.top()), "⊤"),
    ]
    bad = 0
    for label, got, want in cases:
        ok = got.render() == want
        print(f"  {'✓' if ok else '✗'} {label:18} → {got.render():28} (期望 {want})")
        bad += 0 if ok else 1
    raise SystemExit(1 if bad else 0)
