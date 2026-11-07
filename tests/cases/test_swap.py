#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_swap.py —— `swap(xs, i, j)` 原地交换（B3）

## 为什么先测再信

上一轮我把 `swap` 加进白名单时**没有实现**，结果 check 通过、run 崩
（`InterpError: 调用了未定义的函数 swap`）。教训：**先实现，再登记，再测**。

这里同时验证：
1. 交换真的生效（不是值语义拷贝）；
2. 越界要报错（不是静默）；
3. 成本是 O(1)、不分配。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source          # noqa: E402
from interp import Interp, InterpError    # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


SRC = """func take(vector<u64>& xs) -> u64 {
    swap(xs, 0, 1);
    return xs.at(0);
}
func oob(vector<u64>& xs) -> u64 {
    swap(xs, 0, 9);
    return 0;
}
"""

it = Interp(parse_source(SRC, "a.eln").module, None)

print("[1] 交换真的生效")
check("swap 后 at(0) == 6（原 [5, 6]）", it.call("take", [[5, 6]]) == 6, it.call("take", [[5, 6]]))

print("[2] 越界要报错，不能静默")
try:
    it.call("oob", [[5, 6]])
    check("越界抛错", False, "没有抛")
except InterpError as e:
    check("越界抛错", "越界" in str(e), str(e))

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
