#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_sort.py —— 内建排序（L3：`sort` / `sort_by`）

## 为什么语言要给排序

十个手写程序里，**排序被重写了不止一遍**（merge 自写 7 处）。
更要紧的是：**不同人写的排序稳定性不一样**。

**我们已经吃过一次亏**：程序 5 因为「递减循环推不出上界」被迫把插入排序
改成选择排序，**丢了稳定性**（实测：相同键的相对次序被打破）。

**语言自己给排序，就该给稳定的那一种** —— 这样「稳不稳定」不再是作者的运气。

## 为什么比较器必须具名

C 的 `qsort` 拿函数指针，比较器是**任意代码** → `O(n log n × κ(cmp))` 里的
`κ(cmp)` 无法静态界定 → **成本契约被打穿**。

**具名函数是有限集合，成本可静态分析。** 所以 `sort_by` 收具名比较器，
不收匿名/闭包。
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from checker import check_module
from cost_interp import CostInterp
from interp import Err, Interp, Ok, StructVal
from parser import parse_source

_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def interp_of(src):
    return Interp(parse_source(src, "t.eln").module, None)


def unwrap(v):
    return v.v if isinstance(v, Ok) else v


# ---------------------------------------------------------------- sort 基本

def test_sort_basic():
    print("[1] sort：基本情形")
    SRC = """func run(u64 k) -> u64! {
    Vec<u64> xs = Vec<u64>::make();
    if (k == 0) { }
    if (k == 1) { xs.push(7); }
    if (k == 2) { xs.push(1); xs.push(2); xs.push(3); }
    if (k == 3) { xs.push(3); xs.push(2); xs.push(1); }
    if (k == 4) { xs.push(5); xs.push(1); xs.push(9); xs.push(3); }
    if (k == 5) { xs.push(4); xs.push(4); xs.push(4); }
    const u64 n = sort(xs);
    u64 acc = 0;
    u64 i = 0;
    while (i < n) {
        acc = acc * 10 + xs.at(i);
        i = i + 1;
    }
    return acc;
}"""
    it = interp_of(SRC)
    CASES = [
        (0, 0, "空容器"),
        (1, 7, "单元素"),
        (2, 123, "已有序"),
        (3, 123, "逆序"),
        (4, 1359, "随机"),
        (5, 444, "全同值"),
    ]
    for k, want, label in CASES:
        got = unwrap(it.call("run", [k]))
        check(got == want, f"sort {label}", f"实际 {got}，期望 {want}")

    # 返回值 = 元素个数
    check(unwrap(it.call("run", [4])) == 1359, "返回值可串起来用")


# ---------------------------------------------------------------- 稳定性

def test_stability():
    print("[2] 稳定性：相同键的相对次序 MUST 保持")
    # 用 struct：按 start 排，检查相同 start 的两个元素次序
    TU = """struct Pair { key: u64, tag: u64 }
"""
    SRC = """struct Pair { key: u64, tag: u64 }
func by_key(Pair a, Pair b) -> bool {
    return a.key < b.key;
}
func run() -> u64! {
    Vec<Pair> xs = Vec<Pair>::make();
    xs.push(Pair { key: 1, tag: 11 });
    xs.push(Pair { key: 0, tag: 20 });
    xs.push(Pair { key: 1, tag: 12 });
    xs.push(Pair { key: 1, tag: 13 });
    sort_by(xs, by_key);
    // 三个 key=1 的元素，tag 必须仍是 11, 12, 13
    return xs.at(1).tag * 100 + xs.at(2).tag * 10 + xs.at(3).tag;
}"""
    it = interp_of(SRC)
    got = unwrap(it.call("run", []))
    # 期望 = 11*100 + 12*10 + 13 = 1233（我先前手算成 111213，错了）
    check(got == 1233, "sort_by 稳定（tag 保持 11,12,13）", f"实际 {got}")


# ---------------------------------------------------------------- sort_by

def test_sort_by():
    print("[3] sort_by：具名比较器")
    SRC = """func desc(u64 a, u64 b) -> bool {
    return a > b;
}
func asc(u64 a, u64 b) -> bool {
    return a < b;
}
func run(u64 which) -> u64! {
    Vec<u64> xs = Vec<u64>::make();
    xs.push(5);
    xs.push(1);
    xs.push(9);
    if (which == 0) {
        sort_by(xs, asc);
    } else {
        sort_by(xs, desc);
    }
    return xs.at(0) * 100 + xs.at(2);
}"""
    it = interp_of(SRC)
    check(unwrap(it.call("run", [0])) == 109, "升序比较器", str(unwrap(it.call("run", [0]))))
    check(unwrap(it.call("run", [1])) == 901, "降序比较器", str(unwrap(it.call("run", [1]))))


# ---------------------------------------------------------------- 拒绝

def test_reject():
    print("[4] 比较器不合规 MUST 报错")
    TU = "spec run(Vec<u64>& xs) -> u64 {\n    effects { alloc };\n}\n"
    bad_name = check_module(TU, """func run(Vec<u64>& xs) -> u64 {
    sort_by(xs, nope);
    return 0;
}""", "m")
    check(any(d.code == "E-Sort-BadComparator" for d in bad_name.diagnostics),
          "比较器名字不存在 → E-Sort-BadComparator")

    bad_arity = check_module(TU, """func bad(u64 a) -> bool { return true; }
spec bad(u64 a) -> bool {
    effects {};
}
func run(Vec<u64>& xs) -> u64 {
    sort_by(xs, bad);
    return 0;
}""", "m")
    check(any(d.code == "E-Sort-BadComparator" for d in bad_arity.diagnostics),
          "比较器形参不是 2 个 → E-Sort-BadComparator",
          str([d.code for d in bad_arity.diagnostics]))

    bad_args = check_module(TU, """func run(Vec<u64>& xs) -> u64 {
    sort(xs, xs);
    return 0;
}""", "m")
    check(any(d.code == "E-Sort-BadArgs" for d in bad_args.diagnostics),
          "sort 给 2 个实参 → E-Sort-BadArgs")

    # 运行时：比较器名不是本模块函数
    it = interp_of("""func run() -> u64! {
    Vec<u64> xs = Vec<u64>::make();
    xs.push(1);
    sort_by(xs, ghost);
    return 0;
}""")
    try:
        it.call("run", [])
        check(False, "运行时比较器不存在应报错")
    except Exception as e:                          # noqa: BLE001
        check("不是本模块的函数" in str(e), "运行时比较器不存在报错", str(e)[:50])


# ---------------------------------------------------------------- 成本

def test_cost():
    print("[5] 成本：O(n log n)；sort_by 乘比较器成本")
    SRC = """func asc(u64 a, u64 b) -> bool {
    return a < b;
}
func run(Vec<u64>& xs) -> u64! {
    sort(xs);
    return xs.at(0);
}
func run2(Vec<u64>& xs) -> u64! {
    sort_by(xs, asc);
    return xs.at(0);
}"""
    ci = CostInterp(parse_source(SRC, "t.eln").module, None)
    r1 = ci.cost_of("run").render()
    check("log2(n(xs))" in r1, "sort 的成本含 log2(n)", r1)

    ci2 = CostInterp(parse_source(SRC, "t.eln").module, None)
    r2 = ci2.cost_of("run2").render()
    check("log2(n(xs))" in r2, "sort_by 的成本也含 log2(n)", r2)
    # sort_by = n log n × κ(cmp)；sort = n log n。比较器成本必须真的乘上去。
    check(r2 != r1, "sort_by 的式子与 sort 不同（乘了比较器）", f"{r1} vs {r2}")


def main():
    test_sort_basic()
    test_stability()
    test_sort_by()
    test_reject()
    test_cost()

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
