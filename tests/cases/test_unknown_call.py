#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_unknown_call.py —— E18 必须**可见**

E18：分析器不认识的调用会被记成 `1 + 实参`，而被调函数体当成 0 成本。
对被调内部一无所知时**这不是上界**（它可能跑 10⁶ 步）。

本轮先做「不许静默」：检测出来就印一行警告。
语义该改成「报错」还是「记 ⊤」还没定（`C-决定与索引.md` N-14）。

## 两条断言

1. **真的未知** → 报出来，且带上名字；
2. **方法调用 / 语言内建 / stdlib / 已声明 extern** → **不许报**。
   第二条比第一条重要：误报会把警告变成噪音，噪音会让真问题被无视。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source        # noqa: E402
from dsh import _unknown_calls         # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def names(tu_src, ch_src):
    tu = parse_source(tu_src, "a.tu").module
    ch = parse_source(ch_src, "a.eln").module
    return [n for n, _ in _unknown_calls(tu, ch)]


TU = """spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

print("[1] 真的未知调用 → 报出来")
got = names(TU, """func f(const vector<u64>& xs) -> u64 {
    u64 y = mystery_fn(xs);
    return y;
}
""")
check("抓到 mystery_fn", got == ["mystery_fn"], got)

print("[2] 不该报的四类")
cases = [
    ("方法调用", """func f(const vector<u64>& xs) -> u64 {
    return xs.length();
}
"""),
    ("语言内建 max", """func f(const vector<u64>& xs) -> u64 {
    return max(xs.length(), 1);
}
"""),
    ("高阶内建 sort_by", """func f(vector<u64>& xs) -> u64 {
    sort_by(xs, cmp);
    return xs.length();
}
"""),
    ("用户函数", """func helper(const vector<u64>& xs) -> u64 { return xs.length(); }
func f(const vector<u64>& xs) -> u64 { return helper(xs); }
"""),
]
for label, src in cases:
    got = names(TU, src)
    check(f"{label} 不报", got == [], got)

print("[3] 已声明的 extern 不报")
got = names("extern read_file(const vector<u8>& p) -> u64;\n" + TU,
            """func f(const vector<u64>& xs) -> u64 {
    return read_file(xs);
}
""")
check("extern 不报", got == [], got)


print("[4] 方案 B：`check` 报 E-Call-Undeclared，声明后不再报")
from checker import check_module                    # noqa: E402

_bad = check_module(TU, """func f(const vector<u64>& xs) -> u64 {
    return mystery_fn(xs);
}
""")
_codes = [d.code for d in _bad.diagnostics]
check("未声明 → E-Call-Undeclared", "E-Call-Undeclared" in _codes, _codes[:4])
_msg = next((d.message for d in _bad.diagnostics if d.code == "E-Call-Undeclared"), "")
check("报错里给出要补的那一行", "external {" in _msg and "mystery_fn" in _msg, _msg[:70])

_good = check_module(
    """extern strlen(const vector<u8>& s) -> u64 from "ucrtbase" effects { io };
""" + TU, """func f(const vector<u8>& s) -> u64 {
    return strlen(s);
}
""")
check("已声明 extern → 不报", "E-Call-Undeclared" not in [d.code for d in _good.diagnostics],
      [d.code for d in _good.diagnostics][:4])

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
