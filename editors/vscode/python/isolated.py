#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""isolated.py —— `dsh bench <fn>`：单函数隔离测量（规格 §13.5 B-18…B-22）

## 它和 `dsh measure` 不是一回事

| | `dsh measure` | `dsh bench`（本文件） |
|---|---|---|
| 对象 | 整条链路 + 替换候选 | **单个函数** |
| 含 `?!` | 必须有 | **MUST NOT 含**（B-18） |
| 结果标记 | 直接生成分档表 | **`isolated: true` + 失真清单**（B-19） |
| 写入成本表 | 会 | **MUST NOT**（表里只装 L1 静态值） |
| 反馈粒度 | 原始时序 | **只给判定与相对比值**（B-20） |
| 顺序 | 无 | **自底向上**（B-21） |
| 计划态 | 无 | **可用**（B-22） |

## 为什么"只给比值不给纳秒"（B-20）

**绝对值没有可比性**：换台机器、换个 Python 版本，纳秒全变。
**但比值（快/慢、相对基线）在相同环境下是稳的。**

更重要的：**给出纳秒会诱使人把它写进成本表**，而 B-19 明令禁止。
**不给原始数字，是为了让"权威成本只来自静态分析"这条规矩不被绕过。**

## 为什么"含 `?!` 就不许用"（B-18）

`?!` 的赢家由**分档表**决定，而分档表要**整个链路**一起测才有意义。
**单函数隔离测量拿不到分档表**，测出来的东西不代表真实运行。
所以遇到 `?!` 直接拒绝，并指向 `dsh measure`。
"""

from typing import Any, Dict, List, Optional, Tuple

from parser import Node
from ast_util import descendants as _descendants


class BenchError(Exception):
    """`dsh bench` 的拒绝原因（不是崩溃，是"这个函数不能这样测"）。"""


# ---------------------------------------------------------------- 闭包分析
def _body_of(fn: Node) -> Optional[Node]:
    return next((c for c in fn.children if c.kind == "block"), None)


def _callees(fn: Node, fns: Dict[str, Node]) -> List[str]:
    """本函数**直接调用**的其他函数名（只做一层，不递归）。"""
    body = _body_of(fn)
    if body is None:
        return []
    out = []
    for call in _descendants(body, "call"):
        nm = call.name or ""
        if nm and nm in fns and nm not in out:
            out.append(nm)
    # 方法调用不算（`xs.at(i)` 是标准库，不是用户函数）
    return out


def check_measurable(fn_name: str, fns: Dict[str, Node],
                     pending_labels: Optional[set] = None) -> List[str]:
    """判断这个函数能不能做隔离测量；不能就抛 BenchError。

    返回**失真清单**（B-19 要求 MUST 带）。
    """
    fn = fns.get(fn_name)
    if fn is None:
        raise BenchError(f"找不到函数 {fn_name}")
    body = _body_of(fn)
    if body is None:
        raise BenchError(f"{fn_name} 没有函数体（还是 pending）")

    # ---- B-18：函数体内 MUST NOT 含 `?!`
    disps = _descendants(body, "dispatch")
    if disps:
        labels = sorted({(d.name or "?") for d in disps})
        raise BenchError(
            f"{fn_name} 的函数体里含 `?!`（{', '.join(labels)}）——\n"
            f"  单函数隔离测量拿不到分档表，测出来不代表真实运行（B-18）。\n"
            f"  改用 `dsh measure {fn_name}`：它按替换法把整条链路一起测。")

    # ---- B-18：测量闭包 MUST 全部有实现
    missing = []
    for dep in _callees(fn, fns):
        if _body_of(fns[dep]) is None:
            missing.append(dep)
    if missing:
        raise BenchError(
            f"{fn_name} 的测量闭包不完整：{', '.join(missing)} 还没有实现（B-18）。\n"
            f"  先实现它们，再测 {fn_name}——否则测的是半条链路。")

    # ---- B-19：失真清单（每次都给，不是"出错才给"）
    distortions = [
        "解释执行，不是编译产物——常数项比真实机器高两到三个数量级",
        "未计入 GC / 内存分配的真实代价",
        "标准库方法（`length` / `at` / `push`）的成本未单独建模",
        "同一进程内重复调用，可能受缓存预热影响",
    ]
    return distortions


# ---------------------------------------------------------------- 测量

def ratio_of(baseline_ns: float, this_ns: float) -> Optional[float]:
    """相对比值：`this / baseline`。**只给比值，不给原始时序**（B-20）。"""
    if not baseline_ns:
        return None
    return this_ns / baseline_ns


def verdict(ratio: Optional[float]) -> str:
    """把比值翻译成人话——**判定，不是数字**。"""
    if ratio is None:
        return "无法判定"
    if ratio < 0.9:
        return "更快"
    if ratio > 1.1:
        return "更慢"
    return "持平"


def report(fn_name: str, ratios: Dict[str, Optional[float]],
           distortions: List[str],
           deps: List[Tuple[str, List[str]]]) -> str:
    """生成 `dsh bench` 的输出。

    **B-21 自底向上**：先报被调用的函数（`deps`），再报被测函数。
    **B-19**：MUST 带 `isolated: true` 与失真清单。
    **B-20**：只给判定与相对比值，**不给纳秒**。
    **B-19**：MUST NOT 写任何 `*.cost.yaml`——本函数不碰文件系统。
    """
    out = []
    out.append("isolated: true        （单函数隔离测量，不进权威成本表）")
    out.append(f"function: {fn_name}")
    out.append("")

    # ---- B-21：自底向上
    if deps:
        out.append("  依赖（自底向上，先测的被调用者）：")
        for dep, dep_deps in deps:
            out.append(f"    {dep:<24} 依赖 {', '.join(dep_deps) if dep_deps else '（无）'}")
        out.append("")

    out.append("  相对比值（同环境内可比，绝对值不给——B-20）：")
    # 按传入顺序（= 规模升序）列出——按字符串排序会把 n=1024 排到 n=16 前面
    for k in ratios:
        r = ratios[k]
        shown = "—" if r is None else f"×{r:.2f}"
        out.append(f"    {k:<24} {shown:>8}   {verdict(r)}")
    out.append("")
    out.append("  失真清单（B-19 MUST 带）：")
    for d in distortions:
        out.append(f"    - {d}")
    out.append("")
    out.append("  注：本次测量 MUST NOT 写入任何 `*.cost.yaml`——")
    out.append("      权威成本表只装 L1 静态值（B-19）。")
    return "\n".join(out)
