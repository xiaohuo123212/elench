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
import json
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

SRC = """u64 f(const vector<u64>& xs) {
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
SRC2 = """u64 g(const vector<u64>& xs) {
    return xs.at(0);
}
"""
ci2 = CostInterp(parse_source(SRC2, "b.eln").module, parse_source(TU2, "b.tu").module)
k2 = ci2.cost_of("g").render()
check("xs.at 的 κ 不含 log2", "log2(" not in k2, k2)

# ---------------------------------------------------------------- ① find 族
#
# **2026-11-11 κ 低估审计**：`find` / `insert` / `erase` 真实是**扫一遍**
# （find 线性查找；insert/erase 搬后半段），κ 却记常数或 log2。
# 这里钉**方向**：κ 里必须出现接收者的尺寸量 `n(xs)`，且不许出现 log2
# （log2 是 map/set 的阶，出现在序列上是记错了容器）。
print("[3] 序列的 find/insert/erase 是 O(n)（κ 含 n(xs)）")
VEC = """
u64 find_once(const vector<u64>& xs, u64 k) { return xs.find(k); }
u64 insert_once(vector<u64>& xs, u64 k, u64 v) { xs.insert(k, v); return 0; }
u64 erase_once(vector<u64>& xs, u64 k) { xs.erase(k); return 0; }
u64 at_once(const vector<u64>& xs) { return xs.at(0); }
"""
ci3 = CostInterp(parse_source(VEC, "c.eln").module, None)
for fn in ("find_once", "insert_once", "erase_once"):
    r = ci3.cost_of(fn).render()
    check(f"{fn}：κ 含尺寸量 n(xs)", "n(xs)" in r, r)
    check(f"{fn}：κ 不含 log2（那是 map 的阶）", "log2(" not in r, r)
r_at = ci3.cost_of("at_once").render()
check("xs.at 仍是 O(1)：κ 不含 n(xs)", "n(xs)" not in r_at, r_at)

print("[4] map.keys() 建列表是 O(n)，不是 O(log n)")
KEYSRC = """
u64 keys_n(const map<u64, u64>& m) { return m.keys().length(); }
"""
ci4 = CostInterp(parse_source(KEYSRC, "d.eln").module, None)
rk = ci4.cost_of("keys_n").render()
check("keys：κ 含 n(m)（建 n 元素列表）", "n(m)" in rk, rk)
check("keys：κ 不含 log2（查找才是 log n）", "log2(" not in rk, rk)
ra = ci4.alloc_of("keys_n").render()
check("keys：分配上界 ≥ 1（建新列表要分配）", ra != "0", ra)

# ---------------------------------------------------------------- ③ D1 守卫
#
# **同一语义必须同一个 κ**：`m[k] = v` 与 `m.insert(k, v)` 都是"存在则覆盖"。
# 逐字节比 `render()` 与 `to_dict()`（比"都在 O(log n)"强：常数/形状也不许漂）。
print("[5] `m[k] = v` 与 `m.insert(k, v)`：κ 与分配逐字节相同（D1）")
D1 = """
u64 via_index(u64 n) {
    map<u64, u64> m = map<u64, u64>::new();
    u64 i = 0;
    while (i < n) { m[i] = i; i = i + 1; }
    return m.length();
}
u64 via_insert(u64 n) {
    map<u64, u64> m = map<u64, u64>::new();
    u64 i = 0;
    while (i < n) { m.insert(i, i); i = i + 1; }
    return m.length();
}
"""
ci5 = CostInterp(parse_source(D1, "e.eln").module, None)
a, b = ci5.cost_of("via_index"), ci5.cost_of("via_insert")
check("κ 文本逐字节相同", a.render() == b.render(), f"\n  {a.render()}\n  {b.render()}")
check("κ 结构（to_dict）也相同",
      json.dumps(a.to_dict(), sort_keys=True) == json.dumps(b.to_dict(), sort_keys=True),
      f"\n  {a.to_dict()}\n  {b.to_dict()}")
check("κ 含 log2(n(m))（下标写也是 AVL 插入）", "log2(n(m))" in a.render(), a.render())
aa, ab = ci5.alloc_of("via_index"), ci5.alloc_of("via_insert")
check("分配上界逐字节相同（alloc 也是 1 次/元素）", aa.render() == ab.render(),
      f"\n  index: {aa.render()}\n  insert: {ab.render()}")
check("分配上界不是 0", aa.render() != "0", aa.render())

# ---------------------------------------------------------------- ② 自由函数
print("[6] `map_put` / `set_add` 每次写一个元素：κ 含 log2，不是常数")
FREE = """
u64 free_put(u64 n) {
    HashMap<u64, u64> h = map_new();
    u64 i = 0;
    while (i < n) { map_put(h, i, i); i = i + 1; }
    return map_len(h);
}
u64 free_set(u64 n) {
    Set<u64> s = set_new();
    u64 i = 0;
    while (i < n) { set_add(s, i); i = i + 1; }
    return set_len(s);
}
"""
ci6 = CostInterp(parse_source(FREE, "f.eln").module, None)
for fn in ("free_put", "free_set"):
    rf = ci6.cost_of(fn).render()
    check(f"{fn}：κ 含 log2（不是常数）", "log2(" in rf, rf)
    check(f"{fn}：尺寸量能归约到轮数 n（不留下 n(h)/n(s) 的 E23）",
          "n(h)" not in rf and "n(s)×" not in rf.replace("n(s) +", ""), rf)

# ---------------------------------------------------------------- 同族内建
print("[7] `utf8_char_at` / `utf8_byte_of_char` 扫整串：κ 含 n(s)")
TXT = """
u64 char_at_once(const vector<u8>& s, u64 i) { return utf8_char_at(s, i); }
u64 byte_of_once(const vector<u8>& s, u64 i) { return utf8_byte_of_char(s, i); }
"""
ci7 = CostInterp(parse_source(TXT, "g.eln").module, None)
for fn in ("char_at_once", "byte_of_once"):
    rt = ci7.cost_of(fn).render()
    check(f"{fn}：κ 含尺寸量 n(s)", "n(s)" in rt, rt)

print("[8] `m[k]` 读 / `m[k] += 1`：AVL 查找 + 缺键插入的分配")
READ = """
u64 read_once(const map<u64, u64>& m) { return m[7]; }
u64 read_vec(const vector<u64>& xs) { return xs[0]; }
u64 inc_once(u64 n) {
    map<u64, u64> m = map<u64, u64>::new();
    u64 i = 0;
    while (i < n) { m[i] += 1; i = i + 1; }
    return m.length();
}
"""
ci8 = CostInterp(parse_source(READ, "h.eln").module, None)
rr = ci8.cost_of("read_once").render()
check("`m[k]` 读：κ 含 log2(n(m))（AVL 查找）", "log2(n(m))" in rr, rr)
ra8 = ci8.alloc_of("read_once").render()
check("`m[k]` 读：分配 ≥ 1（缺键会插入 V{}）", ra8 != "0", ra8)
rv = ci8.cost_of("read_vec").render()
check("`xs[i]` 读：κ 不含 n(xs)（仍是 O(1)）", "n(xs)" not in rv, rv)
check("`xs[i]` 读：分配 0", ci8.alloc_of("read_vec").render() == "0",
      ci8.alloc_of("read_vec").render())
ri = ci8.cost_of("inc_once").render()
check("`m[k] += 1`：κ 含 log2（读 + 写两半）", ri.count("log2") >= 2, ri)
check("`m[k] += 1`：分配 ≥ n（每轮至多 1 次）",
      ci8.alloc_of("inc_once").render() != "0",
      ci8.alloc_of("inc_once").render())

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
