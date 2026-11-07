#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_piece_boundary.py —— L-10：`?!` 分段的**边界取值规则**

## 规则（本测试钉住的就是它）

分段是**闭区间** `[lo, hi]`，按**整数**取值，**先匹配先赢**：

| 断言 | 含义 |
|---|---|
| `n == lo` 落在该段 | 左端闭 |
| `n == hi` 落在该段（**不是**下一段） | 右端也闭 |
| 段与段之间**不能重叠**，否则先声明的赢 | 由构造方保证（`lo-1` / `hi+1` 哨兵） |
| `n` 落在所有段外 → 用 `args[0]`（回退） | 越界不静默 |

**为什么要钉**：以前文档只写"越界按回退"，边界点取哪一段**没写**。
两段相邻时（`[0,16]` 与 `[16,64]`）`n=16` 会同时命中——**先匹配先赢**是唯一确定的说法。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cost_domain.symbolic import Cost        # noqa: E402
from cost_domain.cost_check import evaluate   # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


# 三段：n ∈ [0,16] → n；n ∈ [17,64] → 100；其余 → 999（回退）
PW = Cost("piece", ("n", ((0, 16, Cost.size("n")), (17, 64, Cost.num(100)))),
          (Cost.num(999),))


def at(n):
    return evaluate(PW, {"n": float(n)})


print("[1] 左端闭：n == lo 落在该段")
check("n=0 → 0", at(0) == 0.0, at(0))
check("n=16 → 16", at(16) == 16.0, at(16))

print("[2] 右端也闭：n == hi 仍是本段，不是下一段")
check("n=17 → 100（下一段起点）", at(17) == 100.0, at(17))
check("n=64 → 100", at(64) == 100.0, at(64))

print("[3] 段外 → 回退值（不静默钉常数）")
check("n=65 → 999", at(65) == 999.0, at(65))
check("n=-1 → 999", at(-1) == 999.0, at(-1))

print("[4] 重叠时段与段之间「先匹配先赢」")
OVERLAP = Cost("piece", ("n", ((0, 16, Cost.num(1)), (16, 64, Cost.num(2)))),
               (Cost.num(0),))
check("n=16 命中第一段（1，不是 2）", evaluate(OVERLAP, {"n": 16.0}) == 1.0,
      evaluate(OVERLAP, {"n": 16.0}))

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
