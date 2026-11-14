#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""logdom.py —— **log 域判阶**（`设计-log域判阶-2026-11-12.md` 的第 3 步）。

## 为什么需要它

`Cost.pow` 现在能装**符号指数**（`nⁿ`、`2ⁿ`、`n^10`），但 `cost_check._profile`
的三元组 `(次数, log 因子数, loglog 因子数)` 比不出它们：

    n^n   不是"n 的多项式 × log 的幂"——指数位置上是一个随 n 增长的式
    2^n   同上
    n^10  是"log 的幂"？也不是：它是 n 的 10 次

**取 log 就都变回多项式了**（恒等式，对正数）：

    log(a·b)   = log a + log b
    log(a^b)   = b · log a        ← **幂变乘积，符号指数落进乘法**
    log(a+b)   ≤ log(2·max(a,b))  = max(log a, log b) + 1
    log(max(a,b)) = max(log a, log b)
    log(log2 a) = log2(log2 a)    ← 只是多一层

于是：

    log(n^n)  = n · log2 n   → 三元组 (1, 1, 0)  「n log n」
    log(2^n)  = n            → (1, 0, 0)         「n」
    log(n^10) = 10 · log2 n  → (0, 1, 0)         「log n」

三者在新坐标系里 **n log n > n > log n** ⇒ `n^n > 2^n > n^10`。
这就是 `compare` 用来判「达成 / 债务」的那三个三元组，**一个都没变**。

## 两句必须说清楚的话

### ① 变换只做**上界**，而"常数偏移"不影响任何判定

`to_log(c)` 返回 `L(c)`，满足 `L(c) = log2(c) + δ(c)`，其中 `δ` 是**有界**的
（`a+b` 那一行的 `+1`、`a/常数` 丢掉的 `−log2(常数)`、`0` 常数被抬到
`log2(1)=0`）。三元组看不见常数，所以

    order(L(c)) == order(log2 c)        （逐条相等，不是近似）

`compare` 判的是 big-O（容许常数倍），于是：

    order(L(κ)) < order(L(t))  ⇒ logκ = o(log t) ⇒ κ = t^{o(1)} ≤ C·t   → 达成 ✓
    order(L(κ)) > order(L(t))  ⇒ logκ/log t → ∞  ⇒ κ/t → ∞              → 债务 ✓
    order 相同                  ⇒ logκ = Θ(log t) ⇒ κ = t^{Θ(1)}        → **判不了**

**第三行是唯一不安全的一档**：常数因子在 log 域里会变成**乘幂**关系
（`nⁿ` 与 `n^(n/2)` 的 log 形式都是 `n log n`，但前者是后者的 `n^(n/2)` 倍）。
所以 `order` 相同时**只有两种情形判达成**：两份 log 形式**结构相同**，或者
只差**常数阶的加项**（`n^n` 与 `2·n^n`、`n^n + 5`：log 意义上的 `+1` 就是
原式的 `×2`，仍是常数倍）。其余**一律 unknown**——宁可说"判不了"，
不给一个可能错的 ✓。

### ② `to_log` 失败 ⇒ `compare` 退 **unknown**（绝不退"达成"或"债务"）

## §3 的规则表（逐条实现）

| 形状 | log 形式 | 依据 |
|---|---|---|
| `常数 c` | `log2(c)`（`c ≤ 1` 时取 0，向上取整） | 定义 |
| `尺寸量 n` | `log2(n)` | 定义 |
| `a + b` | `max(log a, log b) + 1` | `log(a+b) ≤ log(2·max)+1`（**上界方向**） |
| `a · b` | `log a + log b` | 恒等式 |
| `max(a,b)` | `max(log a, log b)` | 单调 |
| `pow(a, 常数 k)` | `k · log a` | 恒等式 |
| `pow(a, 符号 b)` | `b · log a` | 恒等式（a>1）——**本方案的核心** |
| `log2(a)` | `log2(log2 a)` | 定义（超过两层拒绝，见 §4⑤） |
| `sqrt(a)` | `(1/2)·log a` | 就是 `pow(a, 1/2)`，同一条 |
| `除法 a/b` | `log a − log b` | **出现减法 ⇒ 拒绝**，分母是常数除外（§4①） |
| `piece` | **拒绝** | 各段阶可能不同（§4②） |
| `top`（⊤） | `⊤` | 未知仍是未知 |

## §4 的拒绝（**真的拒绝**，不是"尽力"）

```
① 除法（log 形式出现减法）       → None（分母是**常数**时例外，见下）
② 分段 piece                     → None
③ 符号幂的底数是常数 ≤ 1          → None（a<1 递减、a=1 恒 1）
⑤ loglog 以上嵌套（变换后 > 2 层）→ None（三元组只有两个 log 分量）
   另加：ext(...) / k(f) / par(...) / 陌生节点 → None（**没写进 §3 表**的形状
   一律拒绝：没有论证过的变换就是猜）
```

**① 的例外"分母是常数"**：`log(a/d) = log a − log2(d)`，`d` 是常数时那一项
也是常数。Cost 没有减法节点，所以：

  · `d > 1` → **丢掉** `−log2(d)`：这是**上界方向**（多算一点，不影响判阶）；
  · `0 < d < 1` → **加上** `log2(1/d)`（`a / (1/2) = 2a`，那是真的变大，
    丢掉会**低估**，所以必须加）；
  · `d ≤ 0` / 除零 → None（没有量级）。

**⑤ 为什么按"嵌套层数"而不是"三元组"判**：`loglog n` 的 log 形式是
`log2(log2(log2 n))`（三层）。设计文档要求"超过两层就退 ⊤，不许硬塞"——
硬塞成 loglog 等于把目标**放宽一档**（`logloglog n ≺ loglog n`）。
本实现据此**按语法层数**拒绝（比"三元组装不下"更严一点：例如
`log2(n log n)` 的 log 形式里含一个三层 log 子项，也拒绝）。保守方向，
不会假达成。

## 饱和

`to_log` 自己不下场求值；饱和在 `cost_check.evaluate` 的 `pow` 分支
（`MAG_CAP = 1e18`，见 `symbolic.MAG_CAP`）。判阶只看三元组，
与阈值取值无关；阈值只保护"报告里的比值"那一栏不崩。
"""

from fractions import Fraction
from typing import List, Optional, Tuple

from .symbolic import Cost

#: log 域里允许的最大 log2 嵌套层数（2 = `loglog n`；3 = `logloglog n` → 拒绝）。
MAX_LOG_DEPTH = 2

#: 递归深度上限：坏掉的表达式树不能把这里拖死。
MAX_DEPTH = 200


# ---------------------------------------------------------------- 查询

def has_symbolic_exp(c: Optional[Cost], depth: int = 0) -> bool:
    """这棵成本式里有没有**符号指数**（`Cost("pow", None, (base, exp))`）？

    `compare` 用它决定"要不要走 log 域"：**没有符号指数就一个字节都不动**
    既有判定路径（这是 `设计-log域判阶` 第 4 步的硬要求）。
    """
    if c is None or depth > MAX_DEPTH:
        return False
    if c.kind == "pow" and c.value is None:
        return True
    if c.kind == "piece":
        _var, segs = c.value
        if any(has_symbolic_exp(s, depth + 1) for _lo, _hi, s in segs):
            return True
    return any(has_symbolic_exp(a, depth + 1) for a in c.args)


def _has_piece(c: Optional[Cost], depth: int = 0) -> bool:
    if c is None or depth > MAX_DEPTH:
        return False
    if c.kind == "piece":
        return True
    return any(_has_piece(a, depth + 1) for a in c.args)


# ---------------------------------------------------------------- 变换

def to_log(c: Optional[Cost]) -> Optional[Cost]:
    """`c` 的 log2 形式（上界）；**拒绝**时返回 `None`。

    调用方 MUST 把 `None` 当成"判不了"（unknown），**不许**当成"达成/债务"。
    要看拒绝原因用 `to_log_with_reason`。
    """
    return to_log_with_reason(c)[0]


def to_log_with_reason(c: Optional[Cost]) -> Tuple[Optional[Cost], Optional[str]]:
    """`(log 形式, None)` 或 `(None, 拒绝原因)`。"""
    return _conv(c, 0)


def _mul_num(e: Fraction, x: Cost) -> Cost:
    """`e · x`（`e` 是常数）：`e=0` → 0、`e=1` → x、其余留乘法节点。"""
    if e == 0:
        return Cost.num(0)
    if e == 1:
        return x
    return Cost.mul(Cost.num(e), x)


def _mul_exp(e: Cost, x: Cost) -> Cost:
    """`e · x`（`e` 是**符号**指数，`x` 是底数的 log 形式）。

    `x` 是常数（`log2(常数底)`，例如 `2^n` 的 `log2(2) = 1`）时先化简：
    `n × 1 = n`、`n × 0 = 0`。不化简的话 `2^n` 的 log 形式会印成
    `n(xs)×1`——三元组虽然一样，但读的人看不出它就是 `n`。
    """
    if x.kind == "num":
        v = x.value
        if v == 0:
            return Cost.num(0)
        if v == 1:
            return e
        return Cost.mul(e, x)
    return Cost.mul(e, x)


def _log_depth(c: Optional[Cost], depth: int = 0) -> int:
    """log2 的**嵌套层数**：`log2(n)` = 1、`log2(log2(n))` = 2、三层 = 3。

    `add` / `max` 取各分支的最大值（最深的那个决定"这是不是还在家族里"）。
    """
    if c is None or depth > MAX_DEPTH:
        return 0
    if c.kind == "log2":
        return 1 + _log_depth(c.args[0], depth + 1)
    if c.kind == "piece":
        _var, segs = c.value
        inner = [_log_depth(s, depth + 1) for _lo, _hi, s in segs]
        return max(inner + [_log_depth(a, depth + 1) for a in c.args] or [0])
    if c.args:
        return max(_log_depth(a, depth + 1) for a in c.args)
    return 0


def _conv(c: Optional[Cost], depth: int) -> Tuple[Optional[Cost], Optional[str]]:
    """§3 的规则表 + §4 的拒绝（内部递归版）。

    返回 `(log 形式, None)` 或 `(None, 原因)`。**任何"化不了"都是拒绝**，
    绝不返回一个"差不多的"式子——判阶的结论只允许来自这里能证明的形状。
    """
    if c is None:
        return None, "没有成本式"
    if depth > MAX_DEPTH:
        return None, "表达式嵌套过深"
    k = c.kind

    if k == "num":
        # **常数**：`log2(c)`。`c ≤ 1` 时真值是 `≤ 0`（c=0 是 −∞），
        # 抬到 0 是**上界方向**（多算一点，不影响三元组）。
        v = c.value if isinstance(c.value, Fraction) else Fraction(c.value)
        if v < 0:
            return None, "负常数（log 无定义）"
        if v <= 1:
            return Cost.num(0), None
        return Cost.log2(Cost.num(v)), None

    if k == "size":
        return Cost.log2(c), None

    if k == "sym":
        if str(c.value or "").startswith("k("):
            return None, f"函数参数的成本 {c.value}（多大要看调用点）"
        return Cost.log2(c), None

    if k == "top":
        return Cost.top(), None

    if k == "ext":
        return None, f"外部项 ext({c.value})"

    if k in ("add", "max"):
        la, why = _conv(c.args[0], depth + 1)
        if la is None:
            return None, why
        lb, why = _conv(c.args[1], depth + 1)
        if lb is None:
            return None, why
        if la.is_top() or lb.is_top():
            return Cost.top(), None
        if k == "max":
            return Cost.max(la, lb), None
        # `a + b`：`log(a+b) ≤ max(log a, log b) + 1`（上界方向）。
        return Cost.add(Cost.max(la, lb), Cost.num(1)), None

    if k == "mul":
        la, why = _conv(c.args[0], depth + 1)
        if la is None:
            return None, why
        lb, why = _conv(c.args[1], depth + 1)
        if lb is None:
            return None, why
        if la.is_top() or lb.is_top():
            return Cost.top(), None
        return Cost.add(la, lb), None

    if k == "div":
        den = c.args[1]
        if den.kind != "num":
            # **§4①**：`log(a/b) = log a − log b`，Cost 没有减法节点。
            return None, ("除法（log 形式会出现减法；只有**常数分母**能当常数项处理）")
        try:
            dv = Fraction(den.value)
        except (TypeError, ValueError, ZeroDivisionError):
            return None, "除法的分母不是可用的常数"
        if dv <= 0:
            return None, "常数分母 ≤ 0（求值是 +∞ 或翻符号）"
        la, why = _conv(c.args[0], depth + 1)
        if la is None:
            return None, why
        if la.is_top():
            return Cost.top(), None
        if dv == 1:
            return la, None
        if dv > 1:
            # 丢掉 `−log2(dv)`：**上界方向**（常数项，三元组看不见）。
            return la, None
        # `0 < dv < 1`：`a/dv` 比 `a` **大**，必须把 `+log2(1/dv)` 加回去。
        return Cost.add(la, Cost.log2(Cost.num(1 / dv))), None

    if k == "log2":
        sub, why = _conv(c.args[0], depth + 1)
        if sub is None:
            return None, why
        if sub.is_top():
            return Cost.top(), None
        out = Cost.log2(sub)
        if _log_depth(out) > MAX_LOG_DEPTH:
            # **§4⑤**：`loglog n` 的 log 形式是三层 log，超出三元组的两个分量。
            return None, ("超过两层 log（loglog 及以上嵌套）："
                          f"log 形式已到 {_log_depth(out)} 层，三元组装不下")
        return out, None

    if k == "pow":
        return _conv_pow(c, depth)

    if k == "piece":
        # **§4②**：不同段的阶可能不同；log 域里 `max` 与分段的交互没有论证过。
        return None, "分段 piece（各段的阶可能不同，log 域没有论证过）"

    if k == "par":
        # **没写进 §3 表的形状一律拒绝**（这里只是把原因说清楚）：
        # `par` 的判阶口径是 max、数值口径是 Σ，`log(Σ)` 与 `max(log)` 只差
        # 一个常数——数学上可以做，但设计文档没有论证过，**不在这里发挥**。
        return None, "parallel 块（log 域与 `par` 的两条口径没论证过，不猜）"

    return None, f"陌生的成本节点 {k!r}"


def _conv_pow(c: Cost, depth: int) -> Tuple[Optional[Cost], Optional[str]]:
    """`pow` 的两条：常数指数 `k·log a`、**符号指数** `b·log a`。"""
    base = c.args[0]
    if c.value is not None:
        try:
            e = c.value if isinstance(c.value, Fraction) else Fraction(c.value)
        except (TypeError, ValueError, ZeroDivisionError):
            return None, "指数不是可用的数"
        if e < 0:
            return None, "负指数（不是成本上界）"
        if e == 0:
            return Cost.num(0), None
        lb, why = _conv(base, depth + 1)
        if lb is None:
            return None, why
        if lb.is_top():
            return Cost.top(), None
        return _mul_num(e, lb), None

    # ---- 符号指数（本方案的核心）
    exp = c.args[1]
    if exp.kind == "num":
        # 形状上不该出现（构造器会归一成常数指数），兜一下。
        return _conv_pow(Cost.pow(base, Fraction(exp.value)), depth)
    if exp.kind == "top" or base.kind == "top":
        return Cost.top(), None
    if _has_piece(exp):
        return None, "指数里有分段 piece"
    if base.kind == "num":
        v = Fraction(base.value)
        if v == 1:
            return Cost.num(0), None     # `1^b = 1`（构造器本应已化简）
        if v <= 1:
            # **§4③**：`log(a^b)` 要求 a > 1。`0 < a < 1` 时 a^b **递减**，
            # 不是任何成本的上界；a ≤ 0 连实数幂都不是。**不猜**。
            return None, "符号幂的底数是常数 ≤ 1（a<1 递减、负底数无定义）"
    lb, why = _conv(base, depth + 1)
    if lb is None:
        return None, why
    if lb.is_top():
        return Cost.top(), None
    if lb.kind == "num" and lb.value == 0:
        # 底数的 log 是 0 ⇒ 底数 ≤ 1：同上，不能当增长用。
        return None, "符号幂的底数判出来 ≤ 1（log 形式是 0）"
    # **核心恒等式**：`log(a^b) = b · log a`（a>1）。`b` 原样进乘法。
    return _mul_exp(exp, lb), None


# ---------------------------------------------------------------- 自检

if __name__ == "__main__":
    n = Cost.size("xs")
    cases = [
        # (标签, 原式, 期望的 log 形式 / None = 拒绝)
        ("n", n, "log2(n(xs))"),
        ("n²", Cost.mul(n, n), "log2(n(xs)) + log2(n(xs))"),
        ("n log n", Cost.mul(n, Cost.log2(n)),
         "log2(n(xs)) + log2(log2(n(xs)))"),
        ("n^n", Cost.pow(n, n), "n(xs)×log2(n(xs))"),
        ("2^n", Cost.pow(Cost.num(2), n), "n(xs)"),
        ("n^10", Cost.pow(n, Cost.num(10)), "10×log2(n(xs))"),
        ("sqrt(n)", Cost.sqrt(n), "1/2×log2(n(xs))"),
        ("n + n", Cost.add(n, n), "log2(n(xs)) + 1"),
        ("n / 2", Cost.div(n, Cost.num(2)), "log2(n(xs))"),
        ("n / m", Cost.div(n, Cost.size("ys")), None),
        ("piece", Cost.piece("xs", [(1, 10, n)], n), None),
        ("loglog n", Cost.loglog(n), None),
        ("ext", Cost("ext", "read_file"), None),
        ("⊤", Cost.top(), "⊤"),
    ]
    bad = 0
    for label, got, want in cases:
        out, why = to_log_with_reason(got)
        text = "None" if out is None else out.render()
        ok = text == want or (want is None and out is None)
        print(f"  {'✓' if ok else '✗'} {label:12} → {text:44}"
              f"{'' if why is None else '  [' + why + ']'}")
        bad += 0 if ok else 1
    raise SystemExit(1 if bad else 0)
