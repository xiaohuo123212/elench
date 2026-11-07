#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_log_loop.py —— L-01：每轮 ×2 / ÷2 的循环要给出 log2 界

## 修之前（实测）

    while (i < n) { i = i * 2; }   →  κ ≤ 2 + n(n)×9 + …     O(n)，**误拒**
    while (i > 1) { i = i / 2; }   →  κ ≤ 2 + n(i)×9 + …     与 target 的 n 不一致，**判不了**

## 修之后

两者都是 `log2(...)`，带 `O(log(n))` target 的程序判 **达成**。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source        # noqa: E402
from cost_interp import CostInterp      # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


CH = """func dbl(u64 n) -> u64 {
    u64 i = 1;
    while (i < n) {
        i = i * 2;
    }
    return i;
}
func half(u64 n) -> u64 {
    u64 i = n;
    while (i > 1) {
        i = i / 2;
    }
    return i;
}
"""
TU = """spec dbl(u64 n) -> u64 { effects { }; target { time: { max: O(log(n)) }, allocation: { max: 0 }, }; }
spec half(u64 n) -> u64 { effects { }; target { time: { max: O(log(n)) }, allocation: { max: 0 }, }; }
"""

ci = CostInterp(parse_source(CH, "a.eln").module, parse_source(TU, "a.tu").module)
k_dbl = ci.cost_of("dbl").render()
k_half = ci.cost_of("half").render()

print("[1] 两个循环的 κ 都含 log2")
check("倍增循环含 log2", "log2(" in k_dbl, k_dbl)
check("减半循环含 log2", "log2(" in k_half, k_half)

print("[2] 不再退化成 O(n)")
check("倍增不含 n(n)×9 那种线性项", "n(n)×9" not in k_dbl, k_dbl)
check("减半不再用 n(i)", "n(i)" not in k_half, k_half)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
