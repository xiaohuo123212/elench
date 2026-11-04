#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_contract_metric.py —— 成本契约两个指标的口径

## 它守的是什么

1. **筛子只有下界**：big-O 吸收常数，`28n` 就是 `O(n)`——用上界筛会把正常实现判成不合规。
2. **不知道 ≠ 真空**：`ratio is None` 不能被当成"契约形同虚设"。
3. **阈值换了结论不翻**（这里是 2 个真空 / 19 个总数，三档一致）。
4. 逐点指标**只吃测量结果**，不自己决定怎么测。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from contract_metric import (DEFAULT_FLOOR, SENSITIVITY_FLOORS,  # noqa: E402
                             is_compliant, is_vacuous, pointwise_ratio,
                             sensitivity)

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


print("[1] 真空判定只有下界")
check("ratio 28（常数因子）不算真空", not is_vacuous(28.2))
check("ratio 0.0005 算真空", is_vacuous(0.00055))
check("ratio 恰在阈值上不算真空", not is_vacuous(DEFAULT_FLOOR))
check("ratio 略低于阈值算真空", is_vacuous(DEFAULT_FLOOR * 0.99))

print("[2] 不知道 ≠ 真空")
check("ratio=None 不算真空", not is_vacuous(None))
check("ratio=None 但 ok 仍算合规", is_compliant("ok", None))

print("[3] 主指标 = 达标 且 非真空")
check("ok + 紧 → 合规", is_compliant("ok", 5.0))
check("ok + 真空 → 不合规", not is_compliant("ok", 0.0001))
check("debt + 紧 → 不合规", not is_compliant("debt", 5.0))

print("[4] 阈值敏感性：这批数据三档结论一致")
rows = [{"verdict": "ok", "ratio": 28.2},
        {"verdict": "ok", "ratio": 0.00055},
        {"verdict": "ok", "ratio": 5.2e-08},
        {"verdict": "ok", "ratio": 3.0}]
sens = sensitivity(rows)
check("三档都跑", len(sens) == len(SENSITIVITY_FLOORS), str(sens))
check("每档都是 2 个合规", all(s["compliant"] == 2 for s in sens), str(sens))
check("每档都是 2 个真空", all(s["vacuous"] == 2 for s in sens), str(sens))

print("[5] 逐点指标只吃测量结果")
ratio, bad = pointwise_ratio(lambda n: 10 * n, [1, 2, 4, 8], lambda n: 10 * n)
check("全部成立 → 1.0", ratio == 1.0 and bad == [], f"{ratio} {bad}")
ratio, bad = pointwise_ratio(lambda n: 10 * n, [1, 2, 4], lambda n: 100 * n)
check("全不成立 → 0.0", ratio == 0.0 and bad == [1, 2, 4], f"{ratio} {bad}")
ratio, _ = pointwise_ratio(lambda n: n, [1, 2, 4], lambda n: n if n > 1 else 99)
check("部分成立 → 2/3", abs(ratio - 2 / 3) < 1e-9, str(ratio))

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
