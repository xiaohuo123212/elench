#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_heap.py —— 堆原语 `push_heap` / `pop_heap`

## 为什么是原语，不是容器

C++ 的 `priority_queue` 是**容器适配器**（底层 vector + `std::push_heap`/`pop_heap`），
不是容器。我们照同样形状做：**给原语，不给新内存语义**。

成本 **O(log2 n)**，和 C++ 一致；大顶堆固定（自定义比较器是任意代码，成本不可界定）。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source            # noqa: E402
from interp import Interp, InterpError      # noqa: E402
from cost_interp import CostInterp          # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


TU = """spec k() -> u64 { effects {}; target { time: { max: O(1) }, allocation: { max: 0 }, }; }
spec e() -> u64 { effects {}; target { time: { max: O(1) }, allocation: { max: 0 }, }; }
"""

SRC = """u64 k() {
    vector<u64> h = vector<u64>::make();
    push_heap(h, 3);
    push_heap(h, 9);
    push_heap(h, 5);
    u64 top = h.at(0);
    pop_heap(h);
    u64 second = h.at(0);
    return top * 10 + second;
}
u64 e() {
    vector<u64> h = vector<u64>::make();
    pop_heap(h);
    return 0;
}
"""

print("[1] 大顶堆：top = 9，弹一次后 top = 5 → 95")
it = Interp(parse_source(SRC, "a.eln").module, None)
check("堆顶顺序正确", it.call("k", []) == 95, it.call("k", []))

print("[2] 空堆 pop_heap 要报错（不静默）")
try:
    it.call("e", [])
    check("空堆报错", False, "没有抛")
except InterpError as ex:
    check("空堆报错", "空堆" in str(ex), str(ex))

print("[3] 成本是 log2，不是常数")
ci = CostInterp(parse_source(SRC, "a.eln").module, parse_source(TU, "a.tu").module)
k = ci.cost_of("k").render()
check("κ 含 log2", "log2(" in k, k)
check("参数是容器尺寸", "n(h)" in k, k)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
