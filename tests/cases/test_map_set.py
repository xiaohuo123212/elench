#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_map_set.py —— 有序 map / set（B2 第一块）

## 口径（已定）

**有序，不是哈希**：查找/插入承诺 **O(log n) 确定**，迭代顺序 = **键序**，
键只需要**全序**（不需要哈希函数）。

**实现是 AVL 平衡二叉树**（`OrderedTree`）：查找/插入/删除最坏 O(log n)。
所以 `Vec<u8>` / 字符串字面量键可以直接用（以前内部是 `dict`，`Text` 是
`list` 子类、不可哈希，`m.insert("a", 1)` 会在 run 期 `TypeError`）。

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


SRC = """u64 m() {
    map<u64, u64> t = map<u64, u64>::new();
    t.insert(3, 30);
    t.insert(1, 10);
    return t.get(3);
}
u64 order() {
    map<u64, u64> t = map<u64, u64>::new();
    t.insert(3, 30);
    t.insert(1, 10);
    t.insert(2, 20);
    return t.keys().at(0) * 100 + t.length();
}
u64 missing() {
    map<u64, u64> t = map<u64, u64>::new();
    return t.get(9);
}
u64 s() {
    set<u64> t = set<u64>::new();
    t.insert(5);
    t.insert(2);
    t.insert(5);
    return t.values().at(0) * 10 + t.length();
}
u64 text_keys() {
    map<Vec<u8>, u64> t;
    t.insert("b", 2);
    t.insert("a", 1);
    t.insert("a", 10);
    t["c"] = 3;
    t.remove("b");
    return t.length() * 100000 + t.get("a") * 1000
           + t.keys().at(0).at(0) + t.keys().at(1).at(0) + t.contains("b") * 10;
}
u64 text_set() {
    set<Vec<u8>> t;
    t.insert("z");
    t.insert("x");
    t.insert("z");
    t.insert("y");
    return t.length() * 1000000 + t.values().at(0).at(0) * 10000
           + t.values().at(1).at(0) * 100 + t.values().at(2).at(0);
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

print("[5] **键只要全序**：`Vec<u8>` / 字符串字面量键（以前不可哈希，run 期 TypeError）")
# 键序 "a" < "c"；长度 2；get("a")=1（`insert` 同键**不覆盖**，见 A2）；
# contains("b")=0 → 2*100000 + 1*1000 + 97 + 99 = 201196
check("map<Vec<u8>,u64> 可插可查按键序 → 201196",
      it.call("text_keys", []) == 201196, it.call("text_keys", []))
check("insert 同键**不覆盖**（仍是先写的 1）",
      (it.call("text_keys", []) // 1000) % 100 == 1, it.call("text_keys", []))
check("set<Vec<u8>> 去重 + 元素序 → 4212222",
      it.call("text_set", []) == 4212222, it.call("text_set", []))

print("[6] 实现是**平衡二叉搜索树**（最坏 O(log n)），不是 dict / 退化链")
from interp import MapVal, SetVal            # noqa: E402
_m = MapVal()
for _i in range(1000):                       # 升序插入是朴素 BST 的最坏输入
    _m.put(_i, _i)
_h = _m._t.root.height
check("1000 个升序键的树高 ≤ 14（AVL 上界）", _h <= 14, _h)
check("1000 个键仍按键序取出", _m.keys() == list(range(1000)))
_s = SetVal()
for _i in range(1000):
    _s.add(_i)
check("set 也是平衡树（树高 ≤ 14）", _s._t.root.height <= 14, _s._t.root.height)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
