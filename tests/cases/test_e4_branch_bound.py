#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_e4_branch_bound.py —— 循环次数由分支决定时，上界要靠得住（E4）

## 这条守的是什么

```elench
i = 0
if (c) { i = xs.length() }
while (i > 0) { i = i - 1 }
```

最坏转 `xs.length()` 圈。**但循环上界只能从「初值」来**，而 `i` 的初值由那条
`if` 决定——这正是 E4 说的「难点不是值，是控制流」。

修好之前：`monotone_decreasing` 把「循环外被赋过值」的计数器直接剔除，
于是 `while (i > 0)` 拿不到初值，退回**变量本身** → κ 里出现 `n(i)` → 判决 `unknown`。

修好之后：区分「循环体外赋值」（改初值）与「循环体内赋值」（必须仍是减法），
初值是字面量 0 时用体外赋值覆盖（`max(0, x) = x`，u64 计数器不可能小于 0）。

所以这里断言两件事：**κ 里要有 `n(xs)`，不能有 `n(i)`**。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source        # noqa: E402
from cost_interp import CostInterp     # noqa: E402
from cost_check import compare         # noqa: E402

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
"""

CH = """func f(const vector<u64>& xs) -> u64 {
    u64 i = 0;
    if (xs.length() > 0) {
        i = xs.length();
    }
    u64 s = 0;
    while (i > 0) {
        s = s + 1;
        i = i - 1;
    }
    return s;
}
"""

ci = CostInterp(parse_source(CH, "f.eln").module,
                parse_source(TU, "f.tu").module)
k = ci.cost_of("f").render()

print("[1] κ 里是 n(xs)，不是 n(i)")
check("含 n(xs)", "n(xs)" in k, k)
check("不含 n(i)", "n(i)" not in k, k)

print("[2] 判决不再是 unknown")
tgt = ci.target_cost("f", "time")
check("判 ok（同阶）", compare(ci.cost_of("f"), tgt).kind == "ok",
      compare(ci.cost_of("f"), tgt).kind)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
