#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_map_cost.py —— map/set 的成本必须是 `log2`，不是常数

## 为什么这条测试重要

如果 `m.get(k)` / `m.insert(k, v)` 按普通方法调用记**常数**，
κ 就会**偏小**：调用方以为它是 O(1)，而承诺是 O(log n)。

**偏小的上界比没有上界更糟**——这正是 G1 修掉的那类问题。

## 定位时踩的坑（记下来）

第一版把逻辑加在 `_call_inner` 上，**κ 一字未变**。
方法调用在函数体里走的是 `_k_expr_method_call`，不是 `_call_inner`
（那个入口主要服务具名函数调用）。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source      # noqa: E402
from cost_interp import CostInterp    # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


TU = """spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 1 }, };
}
"""

SRC = """func f(const vector<u64>& xs) -> u64 {
    map<u64, u64> m = map<u64, u64>::new();
    u64 i = 0;
    while (i < xs.length()) {
        m.insert(i, xs.at(i));
        i = i + 1;
    }
    return m.get(0);
}
"""

ci = CostInterp(parse_source(SRC, "a.eln").module, parse_source(TU, "a.tu").module)
k = ci.cost_of("f").render()

print("[1] map 操作在 κ 里是 log2，不是常数")
check("κ 含 log2", "log2(" in k, k)
check("log2 的参数是容器尺寸", "n(m)" in k, k)

print("[2] 不是所有方法调用都加 log2（普通方法不受影响）")
TU2 = """spec g(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""
SRC2 = """func g(const vector<u64>& xs) -> u64 {
    return xs.at(0);
}
"""
ci2 = CostInterp(parse_source(SRC2, "b.eln").module, parse_source(TU2, "b.tu").module)
k2 = ci2.cost_of("g").render()
check("xs.at 的 κ 不含 log2", "log2(" not in k2, k2)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
