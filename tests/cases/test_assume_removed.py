#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_assume_removed.py —— `assume` 已移除（2026-11-04）

## 它守的是什么

1. **`assume` 不再是合法语法**：解析阶段就报 `E-Assume-Removed`，
   而且报错里必须写**改法**（不是干巴巴一句"不认识 assume"）。
2. **没有声明就不猜**（从 `test_assume.py` 迁来）：没有 `.tu` 时，
   `while (i < n)` 的界保持 `n(n)`，不能凭空收缩成某个尺寸。

> 第二条原来挂在 `assume` 的测试里，因为当时"能不能猜"是它的核心。
> `assume` 没了，这条断言依然成立、依然重要：**推不出就得说推不出**。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source, ParseError       # noqa: E402
from cost_interp import CostInterp                # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


CH = """u64 f(const vector<u64>& xs) {
    u64 i = 0;
    while (i < n) { i = i + 1; }
    return i;
}
"""

print("[1] assume 解析即报错，且报错里给改法")
for src in ("assume n <= xs.length();\nspec f() -> u64 {}",
            "assume n = 5;\nspec f() -> u64 {}"):
    r = parse_source(src, "a.tu")
    diags = getattr(r, "diagnostics", []) or []
    hit = next((d for d in diags if getattr(d, "code", "") == "E-Assume-Removed"), None)
    check(f"报 E-Assume-Removed（{src.splitlines()[0][:22]}）",
          hit is not None, [getattr(d, "code", "?") for d in diags])
    msg = getattr(hit, "message", "") if hit is not None else ""
    check("报错里给了改法（benchmodel / external / 真实尺寸）",
          ("benchmodel" in msg or "external" in msg or "尺寸" in msg), msg[:80])

print("[2] 没有声明时仍是 n(n)（不能凭空猜）")
ci = CostInterp(parse_source(CH, "a.eln").module, None)
k = ci.cost_of("f").render()
check("n(n) 保留", "n(n)" in k, k)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
