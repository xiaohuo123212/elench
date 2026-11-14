#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_hof_cost.py —— 高阶函数的成本要按**调用点**算（G1）

## 改前

`apply(xs, f)` 里调 `f(...)`，κ 写成 `… + k(f)`（未知符号）。
判决那一步把「不含尺寸量」当成「是常数」，于是判 **✓ 达成** ——
哪怕传进去的是 O(n²) 的函数，也照样说达成。**这是假上界。**

## 改后

1. `k(f)` 在 κ 里 → **判 unknown**（不给你假的上界）；
2. 被调函数的 target 若建立在 `k(f)` 上 → **调用方不许信任它**，
   退回内联；内联时 `_fnvalue_bindings` 会把形参绑到实参的具名函数，
   于是 O(n²) 的回调真的算出平方项。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source          # noqa: E402
from cost_interp import CostInterp       # noqa: E402
from cost_check import compare           # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


TU = """spec apply(const vector<u64>& xs, func(u64) -> u64 f) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec g(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

SQ = """u64 sq(u64 x) {
    u64 s = 0;
    u64 i = 0;
    while (i < x) {
        u64 j = 0;
        while (j < x) {
            s = s + 1;
            j = j + 1;
        }
        i = i + 1;
    }
    return s;
}

u64 apply(const vector<u64>& xs, func(u64) -> u64 f) {
    return f(xs.at(0));
}

u64 g(const vector<u64>& xs) {
    return apply(xs, sq);
}
"""

ONE = """u64 one(u64 x) { return x + 1; }

u64 apply(const vector<u64>& xs, func(u64) -> u64 f) {
    return f(xs.at(0));
}

u64 g(const vector<u64>& xs) {
    return apply(xs, one);
}
"""


def verdict(ci, name):
    return compare(ci.cost_of(name), ci.target_cost(name, "time")).kind


def ci_of(ch_src):
    return CostInterp(parse_source(ch_src, "a.eln").module,
                      parse_source(TU, "a.tu").module)


print("[1] 泛型契约（含 k(f)）不许判 ok")
ci = ci_of(SQ)
k = ci.cost_of("apply").render()
check("κ 里有 k(f)", "k(f)" in k, k)
check("判决不是 ok", verdict(ci, "apply") != "ok", verdict(ci, "apply"))

print("[2] 传 O(n²) 的函数 → 调用点算出平方项，判 unknown")
ci = ci_of(SQ)
kg = ci.cost_of("g").render()
check("κ(g) 里有平方项（n(x)×…n(x)）", kg.count("n(x)") >= 2, kg)
check("判决不是 ok", verdict(ci, "g") != "ok", verdict(ci, "g"))

print("[3] 传 O(1) 的函数 → 常数，判 ok，且 κ 里没有 k(f)")
ci = ci_of(ONE)
kg = ci.cost_of("g").render()
check("κ(g) 不含 k(f)", "k(f)" not in kg, kg)
check("判决是 ok", verdict(ci, "g") == "ok", verdict(ci, "g"))

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
