#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_alloc_compare.py —— 分配维度的三种比较语义（钉住 H3）

## 为什么要有这个测试

`compare_exact` 有三条分支，**行为反直觉**，此前只有代码没有测试：

| 情形 | 判法 |
|---|---|
| 两边常数 | 数值比（`impl ≤ target` 才算达成） |
| target 常数、impl 带符号 | **debt**（H3：承诺 0 次，实际每轮 1 次） |
| 其它（target 带符号） | 退回 `compare`，按阶 |

一条容易踩的：**`allocation: { max: O(1) }` 被当成常数 1**，
于是"每轮分配一次"判 debt——写 `O(1)` 和写 `1` 等价。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cost_check import compare_exact      # noqa: E402
from cost_domain.symbolic import Cost     # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


print("[1] 两边常数 → 数值比")
check("1 ≤ 2 达成", compare_exact(Cost.num(1), Cost.num(2)).kind == "ok")
check("3 > 2 债务", compare_exact(Cost.num(3), Cost.num(2)).kind == "debt")
check("0 ≤ 0 达成", compare_exact(Cost.num(0), Cost.num(0)).kind == "ok")

print("[2] H3：target 是常数、impl 带符号 → debt（不是 unknown）")
sym = Cost.size("xs")
check("n(xs) vs 常数 0 → debt", compare_exact(sym, Cost.num(0)).kind == "debt")
check("n(xs) vs 常数 1 → debt", compare_exact(sym, Cost.num(1)).kind == "debt")

print("[3] target 带符号 → 退回按阶")
check("n(xs) vs n(xs) → ok", compare_exact(sym, Cost.size("xs")).kind == "ok")

print("[4] ⊤（推不出）→ unknown，不撒谎")
check("⊤ vs 常数 → unknown", compare_exact(Cost.top(), Cost.num(1)).kind == "unknown")

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
