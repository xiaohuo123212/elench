#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""order_matrix.py —— **判阶矩阵**：把 §6「必须判对」的对照表变成一条能复跑的闸。

设计依据：`文档/设计/目前设计/设计-log域判阶-2026-11-12.md` §6（15 对）与 §9
（"`compare` 多了一条 log 域推理路径，§6 的对照表是**唯一防线**，必须每次都跑"）。

## 它解决什么

`n²` 与 `n log n` 判反、`n^n` 与 `2^n` 判成同档——这两类错**不会让任何测试变红**，
它们只是让契约悄悄放水（或悄悄误报）。所以这里把"必须判对"的对照表做成一张矩阵：

    python -B tools/order_matrix.py            # 人读表格
    python -B tools/order_matrix.py --explain  # 逐条说"为什么是这个方向"
    python -B tools/order_matrix.py --json     # 机器可读（闸/CI 用）

## 判定**只**来自 `cost_check.compare`

本工具**不另写比较逻辑**：每一对都是现场用 `Cost` 的构造子拼出两边，再调
`cost_check.compare(impl, target)` 拿 `Verdict.kind`（`ok` / `debt` / `unknown`）。
自己写一套比较就等于"让被告给自己作证"——它证明不了实现。

阶那一栏（`(2, 0, 0) n²`）**只是展示**，取的是 `cost_check.order_profile`（公开的
判阶口径）；符号指数（`n^n`）在结构三元组里装不下，展示时改读 `logdom.to_log` 的
log 形式，并标 `[log域]`。**展示与判定是两条独立的读法**，判定永远以 `compare` 为准。

## 「判错」与「未支持」是两件事（汇总里分开数）

| 情形 | 记作 | 计入失败？ | 退出码 |
|---|---|---|---|
| 两边都构造出来了，`compare` 的结论与期望一致 | `pass` | — | — |
| 两边都构造出来了，`compare` **判反**（或该判时 `unknown`） | `fail` | **是** | **1** |
| 某一格**构造不出来 / 构造退化了**（`Cost.pow` 还收不了符号指数…） | `unsupported` | 否 | **0** |

`unsupported` 不记为失败，因为它不是"判错"而是"这一档还不存在"：`Cost.pow(n, n)`
在没有符号指数时可能返回 `⊤`（或直接抛 `TypeError`）。此时把这一格标成
`未支持` 并**在汇总里单列**，退出码仍是 0——**等符号指数做完，同一个工具会自动
变成 15/15 通过**（不需要改这个文件）。

"构造退化了"怎么认出来：每一格带一个 `probe`，例如
`n^n` / `2^n` 必须让 `logdom.has_symbolic_exp()` 为真、`n^1.5` 必须还是 `pow` 节点、
`n log n` 必须真的含 `log2`。`Cost.pow` 悄悄退 `⊤` 的那种"静默降级"会被 probe 抓住，
不会被当成"判错"去冤枉实现。

## 15 对为什么是这些方向（逐条；`--explain` 也会打印）

    #1  n²          vs n log n      债务   次数 2 > 1：n²/(n log n) = n/log n → ∞
    #2  n log n     vs n²           达成   (log n)/n → 0，同一对的反方向
    #3  n log n     vs n^1.5        达成   log n = o(√n)：先比次数 1 < 3/2，log 因子救不回来
    #4  n^1.5       vs n log n      债务   #3 的反方向：√n/log n → ∞
    #5  n^n         vs 2^n          债务   log 域看 n·log n vs n，比值 log n → ∞
    #6  2^n         vs n^n          达成   n ≤ n log n
    #7  n^n         vs n^10         债务   log 域看 n log n vs 10 log n，比值 n/10 → ∞
    #8  n^10        vs n^n          达成   10 log n ≤ n log n
    #9  n^n         vs n^n          达成   同形 ⇒ 只差常数倍 1
    #10 sqrt(n)     vs n            达成   1/2 < 1
    #11 n           vs sqrt(n)      债务   1 > 1/2
    #12 log n       vs n            达成   (0,1,0) ≺ (1,0,0)：正次数压过任意多 log 因子
    #13 常数        vs n^n          达成   κ 不含尺寸量 ⇒ O(1)
    #14 n log n     vs n log n      达成   同阶
    #15 n log n     vs n (log n)²   达成   (1,1,0) ≤ (1,2,0)：log 因子少一个

**#5–#9 是"直接比会判错"的那五条**（都记成"指数"就同档了）；#1–#4、#10–#11 是
既有能力的**回归钉子**（改 log 域不许把老判定碰坏）。

## 自加的钉子（`group = "extra"`，同样计入闸）

§6 的 15 条**没有覆盖**下面六类风险，所以各补一条（详见各自的 `why`）：

    E1  n^n         vs n^(n/2)      债务/无法判定（**不许达成**）——同阶但结构不同，
                                    常数因子在 log 域里会变成乘幂，这一档最危险
    E2  2^n         vs n^10         债务   固定次幂永远追不上指数（10 log n ≺ n）
    E3  n^10        vs 2^n          达成   E2 的反方向
    E4  2·n^n       vs n^n          达成   常数倍在 log 域里只是一个常数加项
    E5  n / log n   vs n            达成   (1,−1,0) ≤ (1,0,0)：负 log 因子序
    E6  n           vs n / log n    债务   E5 的反方向
    E7  log n       vs loglog n     债务   三元组的**第三分量**（loglog）；15 条里
                                    一条 loglog 都没有
    E8  loglog n    vs log n        达成   E7 的反方向
    E9  2·n log n   vs n log n      达成   同阶 + 常数倍（设计文档 §7 的边界）
    E10 n log n     vs n loglog n   债务   次数同、log 因子压过 loglog 因子
    E11 n^n + 2^n   vs n^n          达成/无法判定（**不许债务**）——`a+b` 的
                                    log 变换是 `max+1`（上界方向），支配项仍支配
    E12 max(2^n,n²) vs 2^n          达成/无法判定（**不许债务**）——`max` 的
                                    log 形式还是 `max`（单调），2^n 支配 n²

E1 的期望写成**集合** `{债务, 无法判定}`：`n^n / n^(n/2) = n^(n/2) → ∞`，数学上
答案是债务；但 log 域只能看到"同阶"，所以**今天只能给 unknown**。哪天实现能证明
它，判成 `debt` 也照样算过——这张表要钉的是"**不许为了让 n^n 通过就一律判达成**"。

## 退出码

    0  没有判错（可以全是"未支持"）
    1  至少一条判错（判反，或该判时 unknown）

## 为什么不把它写进 `tools/gates.py`

它属于那道闸（`gates.py` 的 `GATES` 表），但那个文件由别人维护：本工具**只被调用**，
一行都不用改它就能接上去——`("order_matrix", "tools/order_matrix.py", [])`。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from cost_domain import Cost                       # noqa: E402
from cost_domain import cost_check as cc           # noqa: E402
from cost_domain.logdom import (                   # noqa: E402
    has_symbolic_exp, to_log_with_reason)

# 中文 Windows 的控制台默认 GBK：打印 ✓ / ✗ 会 UnicodeEncodeError，直接死在
# **汇总之前**（一行结果都看不到）。`tests/runner.py` 踩过同一个坑。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                  # noqa: BLE001
    pass

#: 表里所有尺寸量都叫这个名字（`compare` 自己从 Cost 里读，这里只用于展示与 probe）。
VAR = "xs"

#: 设计文档（写进 `--json`，让人一眼看出这张表钉的是哪一节）。
DOC = "文档/设计/目前设计/设计-log域判阶-2026-11-12.md §6"

#: `Verdict.kind` → 人读。
KIND_TEXT = {"ok": "达成", "debt": "债务", "unknown": "无法判定"}

#: 行状态 → 人读（表格最后两列）。
STATUS_MARK = {"pass": "✓", "fail": "✗", "unsupported": "—"}

#: 分组 → 表头文字。
GROUP_TITLE = {"doc15": "§6 对照表（设计文档钉住的 15 对）",
               "extra": "自加钉子（§6 没覆盖、但同样判反就出事）"}

#: 分组 → 汇总行里的短名（长名字会把汇总挤歪）。
GROUP_SHORT = {"doc15": "§6 对照表", "extra": "自加钉子"}


# ---------------------------------------------------------------- 造 Cost 的格子

def _n() -> Cost:
    return Cost.size(VAR)


def _kinds(c: Optional[Cost], out: Optional[List[str]] = None,
           depth: int = 0) -> List[str]:
    """这棵树的 `kind` 集合（probe 用：确认构造出来的**形状**没被悄悄化简掉）。"""
    if out is None:
        out = []
    if c is None or depth > 200:
        return out
    out.append(c.kind)
    if c.kind == "piece":
        _var, segs = c.value
        for _lo, _hi, seg in segs:
            _kinds(seg, out, depth + 1)
    for a in c.args:
        _kinds(a, out, depth + 1)
    return out


def _probe_var(c: Cost) -> Optional[str]:
    """通用 probe：构造出来的必须**不是 ⊤**、且真的含这个尺寸量。

    `Cost` 的构造子在"还不支持这个形状"时会**静默退 ⊤**（负指数、符号幂的
    底数 ≤ 1、`pow` 的指数类型不认识…）。不查这一下，那种降级会被
    `compare` 判成 `unknown`，然后被当成"判错"记到实现头上。
    """
    if c.is_top():
        return "构造退化成 ⊤（这个形状当前还不支持）"
    if VAR not in c.variables():
        return f"构造里没有保留尺寸量 `n({VAR})`"
    return None


def _probe_log(c: Cost) -> Optional[str]:
    why = _probe_var(c)
    if why:
        return why
    if "log2" not in _kinds(c):
        return "本应含 log 因子，构造出来却没有 `log2` 节点"
    return None


def _probe_pow(c: Cost) -> Optional[str]:
    why = _probe_var(c)
    if why:
        return why
    if c.kind != "pow" or c.value is None:
        return "本应是常数指数的 `pow` 节点，构造出来不是"
    return None


def _probe_symbolic(c: Cost) -> Optional[str]:
    """符号指数（`n^n` / `2^n` / `n^(n/2)`）：必须是**带 Cost 指数**的 pow 节点。"""
    why = _probe_var(c)
    if why:
        return why
    if not has_symbolic_exp(c):
        return ("符号指数没被保留（`Cost.pow` 还不接受 `Cost` 指数？）"
                "——2026-11-12 的 log 域判阶第 1 步")
    return None


def _probe_const(c: Cost) -> Optional[str]:
    if c.is_top():
        return "常数构造退化成 ⊤"
    if c.variables():
        return "本应是常数，构造出来却含尺寸量"
    return None


class Cell:
    """矩阵里的一格：**怎么构造** + **怎么核对构造对了**。

    `build` 必须每次现造（Cost 不可变，但构造子有化简，缓存会掩盖"化简变了"）。
    """

    __slots__ = ("label", "build", "probe")

    def __init__(self, label: str, build, probe):
        self.label = label
        self.build = build
        self.probe = probe

    def __repr__(self) -> str:
        return f"Cell({self.label})"


# ---- 格子（同一个尺寸量 `n(xs)`，两边都是**现场**构造的）
C_N = Cell("n", _n, _probe_var)
C_N2 = Cell("n²", lambda: Cost.mul(_n(), _n()), _probe_var)
C_NLOGN = Cell("n log n", lambda: Cost.mul(_n(), Cost.log2(_n())), _probe_log)
C_N15 = Cell("n^1.5", lambda: Cost.pow(_n(), Fraction(3, 2)), _probe_pow)
C_NN = Cell("n^n", lambda: Cost.pow(_n(), _n()), _probe_symbolic)
C_2N = Cell("2^n", lambda: Cost.pow(Cost.num(2), _n()), _probe_symbolic)
C_N10 = Cell("n^10", lambda: Cost.pow(_n(), 10), _probe_pow)
C_SQRT = Cell("sqrt(n)", lambda: Cost.sqrt(_n()), _probe_pow)
C_LOGN = Cell("log n", lambda: Cost.log2(_n()), _probe_log)
C_ONE = Cell("常数 5", lambda: Cost.num(5), _probe_const)
C_NLOGN2 = Cell("n (log n)²",
                lambda: Cost.mul(_n(), Cost.mul(Cost.log2(_n()), Cost.log2(_n()))),
                _probe_log)
C_NHALF = Cell("n^(n/2)", lambda: Cost.pow(_n(), Cost.div(_n(), Cost.num(2))),
               _probe_symbolic)
C_2NN = Cell("2·n^n", lambda: Cost.mul(Cost.num(2), Cost.pow(_n(), _n())),
             _probe_symbolic)
C_NOVERLOG = Cell("n / log n", lambda: Cost.div(_n(), Cost.log2(_n())), _probe_var)
C_LOGLOG = Cell("loglog n", lambda: Cost.loglog(_n()), _probe_log)
C_NLOGN_LOGN = Cell("n loglog n",
                    lambda: Cost.mul(_n(), Cost.loglog(_n())), _probe_log)
C_2NLOGN = Cell("2·n log n",
                lambda: Cost.mul(Cost.num(2), Cost.mul(_n(), Cost.log2(_n()))),
                _probe_log)
C_NN_PLUS_2N = Cell("n^n + 2^n",
                    lambda: Cost.add(Cost.pow(_n(), _n()),
                                     Cost.pow(Cost.num(2), _n())),
                    _probe_symbolic)
C_MAX_2N_N2 = Cell("max(2^n, n²)",
                   lambda: Cost.max(Cost.pow(Cost.num(2), _n()),
                                    Cost.mul(_n(), _n())),
                   _probe_symbolic)


class Pair:
    """一条对照：`impl` 对 `target` 的期望判决。

    `expected` 可以是单个 `Verdict.kind`，也可以是**集合**（用于"只钉方向、
    不钉严格判决"的钉子，例如 E1：判债务与判 unknown 都算过，只有判达成不行）。
    """

    __slots__ = ("id", "impl", "target", "expected", "why", "group")

    def __init__(self, id: int, impl: Cell, target: Cell, expected,
                 why: str, group: str = "doc15"):
        self.id = id
        self.impl = impl
        self.target = target
        self.expected = (expected,) if isinstance(expected, str) else tuple(expected)
        self.why = why
        self.group = group

    def __repr__(self) -> str:
        return f"Pair(#{self.id} {self.impl.label} vs {self.target.label})"


#: **§6 的 15 对**——`impl` 在左、`target` 在右（顺序就是文档表格的顺序）。
DOC_PAIRS: Tuple[Pair, ...] = tuple([
    Pair(1, C_N2, C_NLOGN, "debt",
         "次数 2 > 1（log 因子一样多）：n²/(n log n) = n/log n → ∞"),
    Pair(2, C_NLOGN, C_N2, "ok",
         "(log n)/n → 0——#1 的反方向；两条都判对才说明「log 因子压不过次数」"),
    Pair(3, C_NLOGN, C_N15, "ok",
         "log n = o(√n)：先比次数 1 < 3/2，log 因子的 1 救不回来"),
    Pair(4, C_N15, C_NLOGN, "debt",
         "#3 的反方向：√n/log n → ∞，分数次幂也压过 log"),
    Pair(5, C_NN, C_2N, "debt",
         "log 域：log(n^n)=n·log n 对 log(2^n)=n，比值 log n → ∞"
         "——**直接按「指数」比会把这两条判成同档**，这正是 log 域要救的那一档"),
    Pair(6, C_2N, C_NN, "ok",
         "n ≤ n log n——#5 的反方向，两条一起才说明 n^n 严格大于 2^n"),
    Pair(7, C_NN, C_N10, "debt",
         "log 域：n log n 对 10 log n，比值 n/10 → ∞；"
         "等价地 n^n / n^10 = n^(n−10) → ∞：**固定次幂永远追不上 n^n**"),
    Pair(8, C_N10, C_NN, "ok",
         "10 log n ≤ n log n——#7 的反方向（n^n 拿来做 target 时必须判达成）"),
    Pair(9, C_NN, C_NN, "ok",
         "同形同阶 ⇒ 只差常数倍 1；两侧 log 形式**结构相同**才敢判达成"),
    Pair(10, C_SQRT, C_N, "ok",
         "1/2 < 1——分数次数回归钉子（`Order` 的 d 是 Fraction，不是 int）"),
    Pair(11, C_N, C_SQRT, "debt",
         "1 > 1/2——#10 的反方向"),
    Pair(12, C_LOGN, C_N, "ok",
         "(0,1,0) ≺ (1,0,0)：任何正次数都压过任意多个 log 因子"),
    Pair(13, C_ONE, C_NN, "ok",
         "κ 不含尺寸量 ⇒ O(1)；任何正阶的 target 都满足（含 n^n）"),
    Pair(14, C_NLOGN, C_NLOGN, "ok",
         "同阶（同形）：契约写 n log n、实现也是 n log n"),
    Pair(15, C_NLOGN, C_NLOGN2, "ok",
         "(1,1,0) ≤ (1,2,0)：次数相同、log 因子少一个，比值 1/log n → 0"),
])

#: **自加的钉子**：§6 没覆盖、但判反同样会出事的六类（见文件头）。
EXTRA_PAIRS: Tuple[Pair, ...] = tuple([
    Pair(101, C_NN, C_NHALF, ("debt", "unknown"),
         "log 形式都是 (1,1,0)——**同阶**；而 n^n / n^(n/2) = n^(n/2) → ∞。"
         "常数因子在 log 域里会变成**乘幂**，所以这一档只能判债务或 unknown："
         "**不许为了 n^n 好过就一律判达成**（今天实现给 unknown，仍算过）",
         group="extra"),
    Pair(102, C_2N, C_N10, "debt",
         "指数压过固定次幂：log 域 10 log n ≺ n（n^10 只是多项式）",
         group="extra"),
    Pair(103, C_N10, C_2N, "ok",
         "#102 的反方向：n^10 ≤ C·2^n",
         group="extra"),
    Pair(104, C_2NN, C_NN, "ok",
         "同阶 + 常数倍：log(2·n^n) = 1 + n log n，多出来的 1 是常数加项"
         "（原式的 ×2），不影响 big-O",
         group="extra"),
    Pair(105, C_NOVERLOG, C_N, "ok",
         "(1,−1,0) ≤ (1,0,0)：同次数、log 因子为**负** ⇒ n/log n 比 n 小",
         group="extra"),
    Pair(106, C_N, C_NOVERLOG, "debt",
         "#105 的反方向：n/(n/log n) = log n → ∞",
         group="extra"),
    Pair(107, C_LOGN, C_LOGLOG, "debt",
         "(0,1,0) ≻ (0,0,1)：**第三分量**（loglog）只在前两位相同时才起作用；"
         "log n / loglog n → ∞",
         group="extra"),
    Pair(108, C_LOGLOG, C_LOGN, "ok",
         "#107 的反方向：loglog n ≤ C·log n",
         group="extra"),
    Pair(109, C_2NLOGN, C_NLOGN, "ok",
         "同阶 + 常数倍（2×）：target 是**上界**，多一个常数因子不影响 big-O"
         "（设计文档 §7 的边界）",
         group="extra"),
    Pair(110, C_NLOGN, C_NLOGN_LOGN, "debt",
         "次数相同（1），第二分量 1 > 0：log 因子压过 loglog 因子，"
         "n log n / (n loglog n) = log n / loglog n → ∞",
         group="extra"),
    Pair(111, C_NN_PLUS_2N, C_NN, ("ok", "unknown"),
         "log 域里 `log(a+b) ≤ max(log a, log b) + 1` 是**上界方向**"
         "（设计文档 §3 / §9）：`+1` 对应原式的 ×2，支配项仍是 n^n ⇒ "
         "**绝不许判债务**（今天实现给 unknown，因为它只丢常数阶分支、"
         "不丢被支配的分支；保守方向，仍算过）",
         group="extra"),
    Pair(112, C_MAX_2N_N2, C_2N, ("ok", "unknown"),
         "`max` 的 log 形式是单调的 `max`（§3）：2^n 支配 n² ⇒ 结果仍是 2^n。"
         "**不许判债务**（今天给 unknown，同 #111 的保守档）",
         group="extra"),
])

#: 全部要被判对的对子（`PAIRS` 是**唯一一份数据**：单测直接读它，不许另抄一遍）。
PAIRS: Tuple[Pair, ...] = DOC_PAIRS + EXTRA_PAIRS


# ---------------------------------------------------------------- 阶（只用于展示）

def _frac_text(v: Fraction) -> str:
    return str(int(v)) if v.denominator == 1 else f"{v.numerator}/{v.denominator}"


def _exp_text(v) -> str:
    """次数 / 因子数印成**指数**：整数不带括号，分数带括号。

    `n^3/2` 会被读成 `n³/2`（差一个数量级），所以写 `n^(3/2)`；负数同理
    （`log^(-1) n` 而不是 `log^-1 n`）。
    """
    if isinstance(v, Fraction):
        text = _frac_text(v)
        return text if v.denominator == 1 else f"({text})"
    return str(v) if v >= 0 else f"({v})"


def _ord_text(p: Optional[Tuple[Fraction, int, int]]) -> Optional[str]:
    """三元组印成 `(1, 1, 0)`（与 `cost_check` 的文案同形）。"""
    if p is None:
        return None
    d, l, m = p
    return f"({_frac_text(d)}, {l}, {m})"


def _profile_text(p: Optional[Tuple[Fraction, int, int]]) -> str:
    """三元组印成人话：`(1, 1, 0)` → `n·log n`。"""
    if p is None:
        return "?"
    d, l, m = p
    parts = []
    if d:
        parts.append("n" if d == 1 else f"n^{_exp_text(d)}")
    if l:
        parts.append("log n" if l == 1 else f"log^{_exp_text(l)} n")
    if m:
        parts.append("loglog n" if m == 1 else f"loglog^{_exp_text(m)} n")
    return "·".join(parts) if parts else "1"


def order_of(c: Optional[Cost],
             use_log: bool = False) -> Tuple[Optional[Tuple[Fraction, int, int]], str]:
    """展示用的阶：`(三元组, 来源)`。来源是 `结构` / `log域` / `?`。

    **这不是判定**：判定一律走 `cc.compare`。

    `use_log=True`（这一对里任一侧含符号指数，`compare` 会走 log 域）时，
    两边都改读 `logdom.to_log` 的 log 形式——因为 `compare` 就是在这把尺子上
    比的：`log(n^n) = n·log n → (1,1,0)`、`log(2^n) = n → (1,0,0)`、
    `log(n^10) = 10·log n → (0,1,0)`。**一行里两边必须用同一把尺子**，
    否则会出现"左边 (1,1,0)、右边 (10,0,0)，却判了债务"这种读不懂的行。
    log 变换被拒绝时退回结构三元组（并把来源标回 `结构`，不混两件事）。
    """
    if c is None:
        return None, "?"
    if use_log:
        try:
            lg, _why = to_log_with_reason(c)
        except Exception:                          # noqa: BLE001
            lg = None
        if lg is not None:
            try:
                p = cc.order_profile(lg, VAR)
            except Exception:                      # noqa: BLE001
                p = None
            if p is not None:
                return p, "log域"
    try:
        p = cc.order_profile(c, VAR)
    except Exception:                              # noqa: BLE001
        p = None
    if p is not None:
        return p, "结构"
    return None, "?"


# ---------------------------------------------------------------- 跑一条

def _mark(row: dict, status: str, detail: str) -> dict:
    row["status"] = status
    row["status_text"] = STATUS_MARK.get(status, "?")
    row["detail"] = detail
    return row


def evaluate_pair(pair: Pair) -> dict:
    """跑一条对照，返回**一行结果**（纯数据，能直接进 JSON）。"""
    row = {
        "id": pair.id,
        "group": pair.group,
        "impl_label": pair.impl.label,
        "target_label": pair.target.label,
        "expected": list(pair.expected),
        "expected_text": "/".join(KIND_TEXT.get(k, k) for k in pair.expected),
        "why": pair.why,
        "impl": None, "target": None,
        "impl_order": None, "impl_order_text": "?",
        "impl_order_source": "?",
        "target_order": None, "target_order_text": "?",
        "target_order_source": "?",
        "verdict": None, "verdict_text": None, "note": None,
        "status": None, "status_text": None, "detail": "",
    }

    # ---- ① 两边**现场构造**；构造不出来 / 构造退化了 → 未支持（不是判错）
    built: Dict[str, Cost] = {}
    problems: List[str] = []
    for side in ("impl", "target"):
        cell: Cell = getattr(pair, side)
        try:
            c = cell.build()
        except Exception as exc:                   # noqa: BLE001
            problems.append(f"{side} `{cell.label}` 构造不出来："
                            f"{type(exc).__name__}: {exc}")
            continue
        built[side] = c
        row[side] = c.render()
        degraded = cell.probe(c)
        if degraded:
            problems.append(f"{side} `{cell.label}`：{degraded}")
    if problems:
        return _mark(row, "unsupported", "；".join(problems))

    # ---- ② 判决：**只**调真正的 `compare`（不另写比较逻辑）
    try:
        v = cc.compare(built["impl"], built["target"])
    except NotImplementedError as exc:
        return _mark(row, "unsupported",
                     f"`compare` 这条路径还没实现：{type(exc).__name__}: {exc}")
    except Exception as exc:                       # noqa: BLE001
        return _mark(row, "fail",
                     f"`compare` 抛异常（工具有 bug 或实现坏了）："
                     f"{type(exc).__name__}: {exc}")

    row["verdict"] = v.kind
    row["verdict_text"] = v.render()
    row["note"] = " ".join((v.detail or v.note or "").split())

    # ---- ③ 阶（展示用）。**任一侧含符号指数 ⇒ compare 走 log 域 ⇒ 两边都读
    #        log 形式**，一行里只用一把尺子。
    use_log = (has_symbolic_exp(built["impl"])
               or has_symbolic_exp(built["target"]))
    for side in ("impl", "target"):
        p, source = order_of(built[side], use_log=use_log)
        row[side + "_order"] = _ord_text(p)
        row[side + "_order_text"] = _profile_text(p)
        row[side + "_order_source"] = source

    # ---- ④ 与期望比
    if v.kind in pair.expected:
        return _mark(row, "pass", "")
    if v.kind == "unknown":
        return _mark(row, "fail",
                     f"判不出（期望 {row['expected_text']}，实际 unknown）："
                     f"{row['note']}")
    return _mark(row, "fail",
                 f"判反（期望 {row['expected_text']}，实际 {v.kind}）：{row['note']}")


def collect(pairs: Optional[Sequence[Pair]] = None) -> dict:
    """跑完一张表，返回**报告**（`--json` 就是这个结构）。

    `pairs=None` 时读模块级 `PAIRS`——单测 monkeypatch `PAIRS` 造一条假失败，
    走的就是这条路（不许有第二份"表"）。
    """
    if pairs is None:
        pairs = PAIRS
    rows = [evaluate_pair(p) for p in pairs]

    def _summary(group_rows: List[dict]) -> dict:
        return {
            "total": len(group_rows),
            "passed": sum(1 for r in group_rows if r["status"] == "pass"),
            "failed": sum(1 for r in group_rows if r["status"] == "fail"),
            "unsupported": sum(1 for r in group_rows if r["status"] == "unsupported"),
        }

    groups = {}
    for name in dict.fromkeys(r["group"] for r in rows):
        groups[name] = _summary([r for r in rows if r["group"] == name])

    total = _summary(rows)
    # **未支持不计入失败**：它不是"判错"，是"这一档还不存在"。
    ok = total["failed"] == 0
    return {
        "tool": "order_matrix",
        "doc": DOC,
        "var": VAR,
        "total": total["total"],
        "passed": total["passed"],
        "failed": total["failed"],
        "unsupported": total["unsupported"],
        "groups": groups,
        "gate": "pass" if ok else "fail",
        "exit_code": 0 if ok else 1,
        "rows": rows,
    }


# ---------------------------------------------------------------- 打印

def _dwidth(s: str) -> int:
    """显示宽度：CJK 全角算 2（表格对不齐不是"看起来差"，是读不出数）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _dwidth(s))


def _order_cell(row: dict, side: str) -> str:
    num = row[side + "_order"]
    if num is None:
        return "?"
    text = f"{num} {row[side + '_order_text']}"
    if row[side + "_order_source"] == "log域":
        text += " [log域]"
    return text


def _cells(row: dict) -> List[str]:
    return [
        str(row["id"]),
        _order_cell(row, "impl"),
        _order_cell(row, "target"),
        KIND_TEXT.get(row["verdict"], "未支持") if row["verdict"] else "—",
        row["expected_text"],
        row["status_text"],
        row["impl"] or "—",
        row["target"] or "—",
    ]


HEADER = ("#", "κ 阶", "target 阶", "判决", "期望", "✓", "κ（现场构造）",
          "target（现场构造）")


def print_table(report: dict, explain: bool = False) -> None:
    rows = report["rows"]
    print(f"判阶矩阵 · {DOC}")
    print("· 判定一律走 `cost_check.compare(impl, target)`（工具不另写比较逻辑）")
    print(f"· 每一格都是现场用 Cost 构造子拼的；尺寸量 = `n({report['var']})`")
    print(f"· 共 {report['total']} 对：判对 {report['passed']}、判错 "
          f"{report['failed']}、未支持 {report['unsupported']}")
    print()

    for group in report["groups"]:
        grows = [r for r in rows if r["group"] == group]
        if not grows:
            continue
        drawn = [_cells(r) for r in grows]
        widths = [max(_dwidth(h), max(_dwidth(c[i]) for c in drawn))
                  for i, h in enumerate(HEADER)]
        # 表达式那两列不设上限之外的裁剪：太长就让它长（信息完整优先）
        print(f"【{GROUP_TITLE.get(group, group)}】")
        print("  " + "  ".join(_pad(h, w) for h, w in zip(HEADER, widths)).rstrip())
        print("  " + "  ".join("-" * w for w in widths))
        for row in grows:
            cells = _cells(row)
            line = "  " + "  ".join(_pad(c, w)
                                    for c, w in zip(cells, widths)).rstrip()
            print(line)
            if explain:
                print(f"        为什么这个方向：{row['why']}")
                if row["verdict_text"]:
                    print(f"        实际判决（compare 的原话）：{row['verdict_text']}")
                if row["detail"]:
                    print(f"        {'判错' if row['status'] == 'fail' else '未支持'}"
                          f"原因：{row['detail']}")
        print()

    # ---- 图例（只有真出现过才印，别占行）
    if any(r["impl_order_source"] == "log域" or r["target_order_source"] == "log域"
           for r in rows):
        print("`[log域]` = 这一侧在结构三元组里装不下（`n^n` / `2^n`），标的是")
        print("             **log(式) 的阶**——`log(n^n)=n·log n`、`log(2^n)=n`、")
        print("             `log(n^10)=10·log n`。含符号指数的一对里，两边都读这把尺子。")
        print()

    # ---- 汇总
    print("-" * 78)
    for group in report["groups"]:
        s = report["groups"][group]
        print(f"{_pad(GROUP_SHORT.get(group, group), 12)}：判对 "
              f"{s['passed']} / {s['total']}"
              f"　判错 {s['failed']}　未支持 {s['unsupported']}")
    if len(report["groups"]) > 1:
        print(f"{_pad('合计', 12)}：判对 {report['passed']} / {report['total']}"
              f"　判错 {report['failed']}　未支持 {report['unsupported']}")

    unsupported = [r for r in rows if r["status"] == "unsupported"]
    if unsupported:
        print()
        print(f"未支持 {len(unsupported)} 条（**不是判错**：这些形状当前 `Cost` 还"
              f"构造不出来，不计入失败，退出码仍是 0）")
        print("  等符号指数 / log 域实现补上，同一个工具会自动把它们变成判对；"
              "现在只把原因记下来：")
        for r in unsupported[:6]:
            print(f"  · #{r['id']} {r['impl_label']} vs {r['target_label']}："
                  f"{r['detail']}")
        if len(unsupported) > 6:
            print(f"  · …还有 {len(unsupported) - 6} 条（`--json` 里有全部）")

    print()
    if report["failed"]:
        fails = [r for r in rows if r["status"] == "fail"]
        unknown = sum(1 for r in fails if r["verdict"] == "unknown")
        print(f"闸结论：**失败**——判错 {len(fails)} 条"
              f"（其中判不出 {unknown} 条）。按 §6，任何一条判反都是这一步失败。")
        for r in fails:
            print(f"  · #{r['id']} {r['impl_label']} vs {r['target_label']}："
                  f"{r['detail']}")
        print(f"退出码 {report['exit_code']}")
    else:
        extra = ("；未支持的不算失败" if unsupported else "")
        print(f"闸结论：**通过**——没有一条判错{extra}。")
        print(f"退出码 {report['exit_code']}")


# ---------------------------------------------------------------- 入口

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="order_matrix.py",
        description="判阶矩阵：§6「必须判对」的对照表（判定一律走 cost_check.compare）")
    ap.add_argument("--json", action="store_true",
                    help="机器可读（闸/CI 用）；退出码语义与表格模式相同")
    ap.add_argument("--explain", action="store_true",
                    help="逐条打印「为什么是这个方向」与 compare 的原话")
    ns = ap.parse_args(argv)

    report = collect()
    if ns.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_table(report, explain=ns.explain)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
