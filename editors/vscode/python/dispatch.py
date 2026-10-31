#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""dispatch.py —— 分档表的生成（§7.8 的三层筛）。

**要生成什么**：对每个 `?!L`，一张「输入规模 → 用哪个候选」的表。

## 三层筛

```
① 比阶（从 target 读）
   · 阶不同 → 知道"大输入谁赢"；仍需 ② 找分界
   · 阶相同 → 直接进 ②
② 按已知阶拟合 → 解交叉点（纯算，不测）
   · 交叉点在实际输入范围外 → 直接定，不测
   · 两候选差价 < 查表成本   → 不分档
   · 否则 → 进 ③
③ 实测（替换法）→ 得赢家
```

## 关键口径（用户定的，别改）

| 口径 | 内容 |
|---|---|
| **数据优先** | 一旦有测量数据，选谁**一律看数据**；静态只回答"要不要测" |
| **差价 < 查表成本 → 不分档** | 为 3 ns 建表，收益不如那次查表本身 |
| **表太大 → 降维** | 降维是"少用几维数据"，不是"不用数据" |
| **范围外** | 取阶低（或系数小）的那个 |

## 本模块不做什么

**不执行代码**。测量由 `bench.measure()` 提供（可注入）；
本模块负责"要不要测""测出来之后怎么变成表"。
"""

import math
from typing import Callable, Dict, List, Optional, Tuple

from cost_check import evaluate, _ratios, _slope, SLOPE_TOL
from symbolic import Cost

# 一次查表 + 跳转的成本（纳秒）。差价小于它就"不值得分档"。
LOOKUP_COST_NS = 3.0

# 多维表的总格子数上限（§7.8.4）
MAX_CELLS = 64


# ---------------------------------------------------------------- 阶

def order_of(c: Optional[Cost]) -> int:
    """粗略增长阶：0 常数 / 1 log / 2 线性 / 3 线性×log / 4+ 更高次。

    只用于"哪个候选在大输入上更强"和"要不要测"，**不用于选具体赢家**。
    """
    if c is None or c.is_top():
        return 99
    k = c.kind
    if k == "num":
        return 0
    if k == "log2":
        return 1
    if k == "size":
        return 2
    if k == "add":
        return max(order_of(c.args[0]), order_of(c.args[1]))
    if k == "max":
        return max(order_of(c.args[0]), order_of(c.args[1]))
    if k == "mul":
        # n × log2(n) → 3；n × n → 4
        return order_of(c.args[0]) + order_of(c.args[1])
    return 9


# ---------------------------------------------------------------- 交叉点

def crossing(a: Cost, b: Cost, var: str,
             lo: int = 1, hi: int = 2 ** 40) -> Optional[int]:
    """解 a(n) = b(n)；返回**第一个**翻转点，找不到返回 None。

    只做**整数二分**（§7.8：不求解析解，只求数量级）。

    **为什么先粗扫再二分**：`log` 与线性可能**交叉两次**——
    n 很小时常数项让 log 赢，稍大一点线性反超，再大 log 又被压住。
    只在两端看符号会漏掉这种"翻两次"的情形。
    """

    def d(n: int) -> float:
        return evaluate(a, {var: n}) - evaluate(b, {var: n})

    # 粗扫：用 2 的幂 + 几个小整数，找第一个符号翻转的区间
    probes = sorted({1, 2, 3, 4, 5, 8} | {2 ** k for k in range(4, 41)
                                          if lo <= 2 ** k <= hi})
    probes = [x for x in probes if lo <= x <= hi]
    if not probes:
        probes = [lo, hi]
    prev = None
    for x in probes:
        dx = d(x)
        if math.isnan(dx):
            return None
        if prev is not None:
            pn, pv = prev
            if (pv <= 0) != (dx <= 0):
                # 在 (pn, x] 里二分
                lo2, hi2, dlo2 = pn, x, pv
                while hi2 - lo2 > 1:
                    mid = (lo2 + hi2) // 2
                    dm = d(mid)
                    if math.isnan(dm):
                        return None
                    if (dm <= 0) == (dlo2 <= 0):
                        lo2, dlo2 = mid, dm
                    else:
                        hi2 = mid
                return hi2
        prev = (x, dx)
    return None


# ---------------------------------------------------------------- 判据

class Plan:
    """一个 `?!` 的分档计划。"""

    def __init__(self, label: str):
        self.label = label
        self.candidates: List[str] = []
        self.need_measure = False
        self.reason = ""
        self.segments: List[Tuple[int, int, str]] = []   # (lo, hi, 候选名)
        self.notes: List[str] = []

    def render(self) -> str:
        if not self.need_measure:
            out = [f"  ?!{self.label}: 不分档 —— {self.reason}"]
            out.append("      · 这是**静态判断、未实测**：若成本式的系数与实际时序不符，"
                       "可能选错（只影响快慢，不违反契约）")
            for nt in self.notes:
                out.append(f"      · {nt}")
            return "\n".join(out)
        out = [f"  ?!{self.label}: 需要测量 —— {self.reason}"]
        for lo, hi, who in self.segments:
            out.append(f"      n ∈ [{lo:,}, {hi:,}] → {who}")
        for nt in self.notes:
            out.append(f"      · {nt}")
        return "\n".join(out)


def plan_dispatch(label: str,
                  costs: Dict[str, Optional[Cost]],
                  var: str,
                  size_lo: int,
                  size_hi: int,
                  lookup_ns: float = LOOKUP_COST_NS) -> Plan:
    """对一个 `?!` 做三层筛，返回计划。

    `costs`：候选名 → 成本式（自信算出来的，见 cost_interp）。
    `size_lo` / `size_hi`：`benchmodel.scale` 给出的实际输入范围。
    """
    p = Plan(label)
    names = [n for n, c in costs.items() if c is not None and not c.is_top()]
    p.candidates = names

    if len(names) < 2:
        p.reason = "可用候选少于 2 个，无从分档"
        return p

    # ---- ① 比阶
    orders = {n: order_of(costs[n]) for n in names}
    best = min(orders.values())
    p.notes.append("阶：" + "、".join(f"{n}=O(1)" if orders[n] == 0 else
                                     f"{n}=阶{orders[n]}" for n in names))

    # ---- ② 解交叉点（只需比较「阶最低的那批」与其余的边界）
    #      简化：对每对候选解交叉点，取落在范围内且最小的那个作为分档边界
    boundaries: List[Tuple[int, str, str]] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            if orders[a] == orders[b]:
                # 同阶：仍可能交叉（常数项不同），解一次
                x = crossing(costs[a], costs[b], var, size_lo, size_hi)
                if x is not None:
                    boundaries.append((x, a, b))
            else:
                x = crossing(costs[a], costs[b], var, size_lo, size_hi)
                if x is not None:
                    boundaries.append((x, a, b))

    if not boundaries:
        # 范围内没有翻转 → 一个候选全程赢
        winner = min(names, key=lambda n: evaluate(costs[n], {var: size_hi}))
        p.reason = f"范围内无交叉点，{winner} 全程更省"
        p.segments = [(size_lo, size_hi, winner)]
        # 差价够不够大到值得分档？
        others = [n for n in names if n != winner]
        if others:
            others.sort(key=lambda n: evaluate(costs[n], {var: size_hi}))
            cw = evaluate(costs[winner], {var: size_hi})
            c2 = evaluate(costs[others[-1]], {var: size_hi})
            if abs(c2 - cw) < lookup_ns:
                p.notes.append(f"差价 {abs(c2 - cw):.1f} < 查表成本 {lookup_ns:.1f}，不值得分档")
        return p

    # 有交叉点 → 需要按段定，段内的赢家由**成本式**给（还没测，属于"待测"）
    boundaries.sort()
    pts = [(size_lo, "lo")] + [(x, "b") for x, _, _ in boundaries] + [(size_hi, "hi")]
    segs: List[Tuple[int, int, str]] = []
    for k in range(len(pts) - 1):
        lo = pts[k][0]
        hi = pts[k + 1][0]
        if hi <= lo:
            continue
        mid = (lo + hi) // 2
        win = min(names, key=lambda n: evaluate(costs[n], {var: mid}))
        segs.append((lo, hi, win))
    # 合并相邻同名段
    merged: List[Tuple[int, int, str]] = []
    for lo, hi, w in segs:
        if merged and merged[-1][2] == w:
            merged[-1] = (merged[-1][0], hi, w)
        else:
            merged.append((lo, hi, w))
    p.segments = merged
    if len(merged) <= 1:
        # 交叉点存在，但**分段后只有一个赢家**（比如两端都是同一个候选赢，
        # 中间被另一个短暂反超但被合并掉了）→ 不需要测。
        p.need_measure = False
        p.reason = f"范围内虽有 {len(boundaries)} 个交叉点，但分段后同一候选全程更省"
    else:
        p.need_measure = True
        p.reason = (f"范围内有 {len(boundaries)} 个交叉点，分 {len(merged)} 段；"
                    f"上表只是**静态估计**，赢家 MUST 由实测确认")

    # ---- 差价判据：任一段内两候选差价 < 查表成本 → 该段不值得分档
    thin = 0
    for lo, hi, w in merged:
        mid = (lo + hi) // 2
        cw = evaluate(costs[w], {var: mid})
        rest = [evaluate(costs[n], {var: mid}) for n in names if n != w]
        if rest and min(rest) - cw < lookup_ns:
            thin += 1
    if thin:
        p.notes.append(f"{thin} 段的最佳与次佳差价 < {lookup_ns:.1f} ns，"
                       f"这些段实际不值得分档")
    return p


# ---------------------------------------------------------------- 从实测生成表

def table_from_measure(label: str,
                       sizes: List[int],
                       measured: Dict[str, Dict[int, float]],
                       lookup_ns: float = LOOKUP_COST_NS) -> List[Tuple[int, int, str]]:
    """用**实测中位数**（`bench.measure` 的输出）生成分段表。

    `measured`：候选名 → {规模: 纳秒}。
    数据优先：每个规模点上直接取最快的候选；相邻同名的点合并成区间。
    """
    names = [n for n in measured if measured[n]]
    if not names:
        return []
    pts: List[Tuple[int, str]] = []
    for sz in sizes:
        vals = [(measured[n][sz], n) for n in names if sz in measured[n]]
        if not vals:
            continue
        vals.sort()
        best_ns, best = vals[0]
        if len(vals) > 1 and vals[1][0] - best_ns < lookup_ns:
            # 差价小于查表成本 → 不值得分档，沿用上一个赢家
            best = pts[-1][1] if pts else best
        pts.append((sz, best))
    if not pts:
        return []
    segs: List[Tuple[int, int, str]] = []
    start = pts[0][0]
    cur = pts[0][1]
    for k in range(1, len(pts)):
        if pts[k][1] != cur:
            segs.append((start, pts[k][0] - 1, cur))
            start, cur = pts[k][0], pts[k][1]
    segs.append((start, sizes[-1] if sizes else pts[-1][0], cur))
    # 合并同名
    merged: List[Tuple[int, int, str]] = []
    for lo, hi, w in segs:
        if merged and merged[-1][2] == w:
            merged[-1] = (merged[-1][0], hi, w)
        else:
            merged.append((lo, hi, w))
    return merged


# ---------------------------------------------------------------- 自检

if __name__ == "__main__":
    n = Cost.size("xs")
    lin = Cost.add(Cost.mul(Cost.num(4), n), Cost.num(5))
    lg = Cost.add(Cost.mul(Cost.num(7), Cost.log2(n)), Cost.num(8))
    quad = Cost.mul(n, n)

    print("=== 线性 vs 对数 ===")
    print(plan_dispatch("search_impl", {"lin": lin, "lg": lg}, "xs", 1, 1 << 20).render())
    print()
    print("=== 线性 vs 平方（平方只在极小规模赢） ===")
    print(plan_dispatch("x", {"lin": lin, "quad": quad}, "xs", 1, 1 << 20).render())
    print()
    print("=== 两个几乎一样（差价 < 查表成本） ===")
    a = Cost.add(Cost.mul(Cost.num(3), n), Cost.num(5))
    b = Cost.add(Cost.mul(Cost.num(3), n), Cost.num(6))
    print(plan_dispatch("y", {"a": a, "b": b}, "xs", 1, 1 << 20).render())
