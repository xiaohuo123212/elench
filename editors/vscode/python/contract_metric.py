#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""contract_metric.py —— 成本契约的两个评估指标（一个是静态的，一个是实测的）

## 为什么要两个

`verdict == "ok"` 只说"κ 与 target 同阶"，它回答不了两个不同的问题：

| 问题 | 指标 | 数据来源 |
|---|---|---|
| **这条契约还在约束东西吗？** | `is_vacuous` / `is_compliant`（静态） | κ 与 target 的比值 |
| **代码真的够快吗？** | `pointwise_ratio`（实测） | 跑出来的 ops |

**两个都报，并报它们不一致的样本**——不一致本身就是信息。

## 阈值为什么只有**下界**

big-O **吸收常数**：`28n` 就是 `O(n)`。用一个上界去筛比值，等于把 big-O
吸收掉的常数又加回来，会把"O(n) 的实现配 O(n) 的目标"判成不合规。

所以筛子只在下界：**目标比上界宽松 100 倍以上**时，任何一阶以内的实现差异都
无法被这条契约区分——**它不再是一个可反驳的约束**（这是"判别力"论证，不是概率论证）。

阈值必须**事先定**并报多档敏感性（见 `SENSITIVITY_FLOORS`）。
方法论脉络：practical significance / effect size / SESOI
（如 NLPStatTest, arXiv 2011.13231；Least Difference in Means, arXiv 2205.12958）。
注意区别：**这里是确定性比值，没有抽样噪声**，所以做的是"决策稳健性"，不是功效分析。
"""
from typing import Dict, List, Optional, Tuple

#: 真空判定的默认下界：κ/target 低于它就认为契约形同虚设
DEFAULT_FLOOR = 0.01

#: 敏感性分析用的三档（论文里必须都报）
SENSITIVITY_FLOORS = (0.1, 0.01, 0.001)


def is_vacuous(ratio: Optional[float], floor: float = DEFAULT_FLOOR) -> bool:
    """目标是否形同虚设（比上界宽松 1/floor 倍以上）。

    `ratio = κ / target`。**只判下界**；`ratio > 1` 只是常数因子，不算问题。
    `ratio is None`（算不出来）时返回 False——**不把"不知道"当成"真空"**。
    """
    if ratio is None:
        return False
    return ratio < floor


def is_compliant(verdict: str, ratio: Optional[float],
                 floor: float = DEFAULT_FLOOR) -> bool:
    """主指标：**既达标，又不是空契约**。"""
    return verdict == "ok" and not is_vacuous(ratio, floor)


def sensitivity(rows: List[Dict], floors=SENSITIVITY_FLOORS) -> List[Dict]:
    """同一批数据在几档阈值下的合规计数——**换阈值结论会不会翻**。

    `rows` 每项至少要有 `verdict` 和 `ratio` 两个键。
    """
    out = []
    for f in floors:
        ok_c = sum(1 for r in rows if is_compliant(r.get("verdict"), r.get("ratio"), f))
        vac = sum(1 for r in rows if is_vacuous(r.get("ratio"), f))
        out.append({"floor": f, "compliant": ok_c, "vacuous": vac,
                    "total": len(rows)})
    return out


def pointwise_ratio(target, grid, measure) -> Tuple[float, List[int]]:
    """实测指标：在 `grid` 上逐点比 `ops(n) ≤ target(n)`，返回 (成立比例, 违反的 n)。

    `target(n) -> float`，`measure(n) -> float`（真实运行得到的 ops）。
    **本函数不跑代码**——测什么由调用方决定，避免把"怎么测"藏进指标里。
    """
    if not grid:
        return 0.0, []
    ok, bad = 0, []
    for n in grid:
        try:
            if measure(n) <= target(n):
                ok += 1
            else:
                bad.append(n)
        except Exception:                                # noqa: BLE001
            bad.append(n)
    return ok / len(grid), bad
