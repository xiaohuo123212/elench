#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_map_set.py —— 有序 map / set（B2 第一块）

## 口径（已定）

**有序，不是哈希**：查找/插入承诺 **O(log n) 确定**，迭代顺序 = **键序**，
键只需要**全序**（不需要哈希函数）。

理由：哈希是「最坏 O(n)、平均 O(1)」，顺序不可复现——把「上界」变成「平均值」，
正是 G1 修掉的那类问题。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source            # noqa: E402
from interp import Interp, InterpError      # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


SRC = """func m() -> u64 {
    map<u64, u64> t = map<u64, u64>::new();
    t.insert(3, 30);
    t.insert(1, 10);
    return t.get(3);
}
func order() -> u64 {
    map<u64, u64> t = map<u64, u64>::new();
    t.insert(3, 30);
    t.insert(1, 10);
    t.insert(2, 20);
    return t.keys().at(0) * 100 + t.length();
}
func missing() -> u64 {
    map<u64, u64> t = map<u64, u64>::new();
    return t.get(9);
}
func s() -> u64 {
    set<u64> t = set<u64>::new();
    t.insert(5);
    t.insert(2);
    t.insert(5);
    return t.values().at(0) * 10 + t.length();
}
"""

it = Interp(parse_source(SRC, "a.eln").module, None)

print("[1] map 基本读写")
check("get(3) == 30", it.call("m", []) == 30, it.call("m", []))

print("[2] **键序**：最小键 1，长度 3 → 103（确定、可复现）")
check("keys() 第 0 个是 1", it.call("order", []) == 103, it.call("order", []))

print("[3] 取不存在的键 → 报错（不静默给 0）")
try:
    it.call("missing", [])
    check("缺键报错", False, "没有抛")
except InterpError as e:
    check("缺键报错且提示 contains", "contains" in str(e), str(e))

print("[4] set 去重 + 元素序")
check("最小元素 2、去重后长度 2 → 22", it.call("s", []) == 22, it.call("s", []))

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
