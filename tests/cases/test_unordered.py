#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_unordered.py —— `unordered_map` / `unordered_set`（承诺 O(n)）

## 口径（用户裁定）

**承诺最坏 O(n)**——哈希表就是这样，把它写成 O(n) 才是诚实的。

**迭代顺序未指定**：不要依赖，也不要把 `map` 的"键序、可复现"套过来。

**在我们的模型里它每一项都比 `map` 差**（O(n) vs O(log n)）；
存在的意义是与 C++ 对齐，不是更快。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source      # noqa: E402
from interp import Interp, InterpError  # noqa: E402
from cost_interp import CostInterp    # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


TU = """spec m() -> u64 { effects {}; target { time: { max: O(1) }, allocation: { max: 1 }, }; }
spec s() -> u64 { effects {}; target { time: { max: O(1) }, allocation: { max: 1 }, }; }
spec g() -> u64 { effects {}; target { time: { max: O(1) }, allocation: { max: 1 }, }; }
spec h() -> u64 { effects {}; target { time: { max: O(1) }, allocation: { max: 1 }, }; }
"""

SRC = """u64 m() {
    unordered_map<u64, u64> t = unordered_map<u64, u64>::new();
    t.insert(3, 30);
    t.insert(1, 10);
    return t.get(3) + t.length();
}
u64 s() {
    unordered_set<u64> t = unordered_set<u64>::new();
    t.insert(5);
    t.insert(5);
    return t.contains(5) * 10 + t.length();
}
u64 g() {
    unordered_map<u64, u64> t = unordered_map<u64, u64>::new();
    return t.get(1);
}
u64 h() {
    map<u64, u64> t = map<u64, u64>::new();
    return t.get(1);
}
"""

it = Interp(parse_source(SRC, "a.eln").module, None)

print("[1] 语义与 map/set 相同")
check("unordered_map 读写 = 32", it.call("m", []) == 32, it.call("m", []))
check("unordered_set 去重 = 11", it.call("s", []) == 11, it.call("s", []))

print("[2] 缺键报错（不静默给 0）")
try:
    it.call("g", [])
    check("缺键报错", False, "没有抛")
except InterpError as e:
    check("缺键报错", "contains" in str(e), str(e))

print("[3] **成本是 O(n)，不是 O(log n)**")
ci = CostInterp(parse_source(SRC, "a.eln").module, parse_source(TU, "a.tu").module)
k_u = ci.cost_of("g").render()
k_m = ci.cost_of("h").render()
check("unordered 的 κ 含 n(t) 线性项", "n(t)" in k_u and "log2" not in k_u, k_u)
check("map 的 κ 仍是 log2", "log2(n(t))" in k_m, k_m)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
