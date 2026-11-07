#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_requires_facts.py —— A1：用声明的 requires 弱化尺寸符号

## 规则

`requires hi <= xs.length();` ⇒ `n(hi)` 可以按 `n(xs)` 计。

**三条硬约束**：
1. **只认 requires**，不从用法推断（`xs[hi]` 推不出 `hi < len`）；
2. 没有 requires 时**行为不变**（仍是 E23 unknown）；
3. 报告里要能看见用了哪条假设（note 里带「按 requires 弱化：hi≤xs」）。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source                      # noqa: E402
from cost_interp import CostInterp                    # noqa: E402
from cost_check import compare, facts_of_spec         # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


CH = """func p(vector<u64>& xs, u64 hi) -> u64 {
    u64 j = 0;
    while (j < hi) { j = j + 1; }
    return j;
}
"""
TU_REQ = "spec p(vector<u64>& xs, u64 hi) -> u64 { requires hi <= xs.length(); effects { }; target { time: { max: O(xs.length()) }, allocation: { max: 0 }, }; }\n"
TU_NOREQ = "spec p(vector<u64>& xs, u64 hi) -> u64 { effects { }; target { time: { max: O(xs.length()) }, allocation: { max: 0 }, }; }\n"


def run(tu_src):
    tu = parse_source(tu_src, "a.tu").module
    ci = CostInterp(parse_source(CH, "a.eln").module, tu)
    spec = next(c for c in tu.children if c.kind == "spec")
    f = facts_of_spec(spec)
    return ci.cost_of("p"), ci.target_cost("p"), f


print("[1] 有 requires → 弱化后判达标")
k, tg, f = run(TU_REQ)
check("facts 提取到 hi≤xs", f.get("hi") == {"xs"}, f)
v = compare(k, tg, f)
check("判 ok", v.kind == "ok", v.kind)
check("note 里标出假设", "requires" in (v.note or ""), v.note)

print("[2] 没有 requires → 行为不变（unknown）")
k2, tg2, f2 = run(TU_NOREQ)
check("facts 为空", not f2, f2)
check("仍是 unknown", compare(k2, tg2, f2).kind == "unknown", "")

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
