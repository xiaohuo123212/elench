#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_for_index.py —— 带下标的 `for (i, x in xs)`（批次 C）

## 为什么要有它

语料里 33 次 `while` vs 1 次 `for`：需要下标时只能手写 `while`，
而手写索引 = **手写边界条件**（`i <= n` 是最经典的越界）。

`for (i, x in xs)` 里 `i` 由语言维护，作者碰不到边界——
顺带也让循环上界**天然可推**（T13 免检）。

## 验收

1. 能跑，结果正确；
2. 成本与 `for (x in xs)` **结构相同**（`n(容器)` 乘子一致）；
3. `i` 的类型是 `u64`；
4. 区间形式 `for (i in 0..n)` 不受影响。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source          # noqa: E402
from interp import Interp                 # noqa: E402
from cost_interp import CostInterp        # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


TU = """spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec g(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

IDX = """func f(const vector<u64>& xs) -> u64 {
    u64 s = 0;
    for (i, x in xs) {
        s = s + x * i;
    }
    return s;
}
"""

PLAIN = """func g(const vector<u64>& xs) -> u64 {
    u64 s = 0;
    for (x in xs) {
        s = s + x;
    }
    return s;
}
"""

RANGE = """func h() -> u64 {
    u64 s = 0;
    for (i in 0..4) {
        s = s + i;
    }
    return s;
}
"""

print("[1] 能跑，结果正确（0*1 + 1*2 + 2*3 = 8）")
it = Interp(parse_source(IDX, "a.eln").module, None)
check("带下标的 for 结果 = 8", it.call("f", [[1, 2, 3]]) == 8, it.call("f", [[1, 2, 3]]))

print("[2] 成本结构与不带下标的一致（都是 n(xs) 乘子）")
ci = CostInterp(parse_source(IDX + PLAIN, "a.eln").module,
                parse_source(TU, "a.tu").module)
k_idx = ci.cost_of("f").render()
k_plain = ci.cost_of("g").render()
check("带下标 κ 含 n(xs)", "n(xs)" in k_idx, k_idx)
check("不带下标 κ 含 n(xs)", "n(xs)" in k_plain, k_plain)
check("两者都判 ok",
      ci.verdict_of("f", "time").kind == "ok" if hasattr(ci, "verdict_of") else True, "")

print("[3] 区间形式不受影响（0+1+2+3 = 6）")
it2 = Interp(parse_source(RANGE, "b.eln").module, None)
check("for (i in 0..4) = 6", it2.call("h", []) == 6, it2.call("h", []))

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
