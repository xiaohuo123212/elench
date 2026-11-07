#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_interp_surface.py —— 语言表面六项：区间 for / break-continue / 除法 /
复合赋值 / 字段赋值 / `as`。每项都是**先坏后修**，所以断言写的是"修好后的行为"。
"""
import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import sys

from interp import Interp, InterpError
from parser import parse_source

_passed = 0
_failed = []

TARGET = "target { time: { max: O(n) }, allocation: { max: 0 } }; "
TU = "spec f(const vector<u64>& xs) -> u64 { effects {}; " + TARGET + "}\n"


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def run(eln, tu=None):
    tu_m = parse_source(tu or TU, "t.tu").module
    el = parse_source(eln, "t.eln").module
    return Interp(el, tu_m).call("f", [[1, 2, 3]])


def test_range_for():
    print("[1] 区间 for 是半开 [a,b)，不再静默 0 次")
    v = run('func f(const vector<u64>& xs) -> u64 {\n'
            '    u64 s = 0;\n    for (i in 0..3) { s = s + 1; }\n    return s;\n}\n')
    check(v == 3, "0..3 执行 3 次", str(v))
    v0 = run('func f(const vector<u64>& xs) -> u64 {\n'
             '    u64 s = 0;\n    for (i in 0..0) { s = s + 1; }\n    return s;\n}\n')
    check(v0 == 0, "0..0 执行 0 次", str(v0))


def test_break_continue():
    print("[2] break / continue")
    v = run('func f(const vector<u64>& xs) -> u64 {\n'
            '    u64 i = 0;\n    while (true) { i = i + 1; break; }\n    return i;\n}\n')
    check(v == 1, "break 跳出", str(v))
    v2 = run('func f(const vector<u64>& xs) -> u64 {\n'
             '    u64 s = 0;\n    u64 i = 0;\n'
             '    while (i < 5) { i = i + 1; if (i == 3) { continue; } s = s + 1; }\n'
             '    return s;\n}\n')
    check(v2 == 4, "continue 跳过 i==3", str(v2))


def test_div():
    print("[3] 除法/取模返回整数，除零报错")
    check(run('func f(const vector<u64>& xs) -> u64 {\n    u64 a = 6;\n'
              '    u64 b = 2;\n    return a / b;\n}\n') == 3, "6/2 = 3")
    check(run('func f(const vector<u64>& xs) -> u64 {\n    u64 a = 6;\n'
              '    u64 b = 4;\n    return a % b;\n}\n') == 2, "6%4 = 2")
    try:
        run('func f(const vector<u64>& xs) -> u64 {\n    u64 a = 6;\n'
            '    u64 b = 0;\n    return a / b;\n}\n')
        check(False, "除零应报错", "没抛")
    except InterpError as exc:
        check("E-Arith-DivZero" in str(exc), "除零 → E-Arith-DivZero", str(exc))


def test_assign_op():
    print("[4] 复合赋值回绕")
    v = run('func f(const vector<u64>& xs) -> u64 {\n'
            '    u64 a = 18446744073709551615;\n    a += 1;\n    return a;\n}\n')
    check(v == 0, "2^64-1 += 1 → 0", str(v))


def test_field_and_cast():
    print("[5] 结构体字段赋值 / as 截断")
    tu = "struct P { x: u64 }\n" + TU
    v = run('func f(const vector<u64>& xs) -> u64 {\n'
            '    P p = P { x: 1 };\n    p.x = 5;\n    return p.x;\n}\n', tu=tu)
    check(v == 5, "p.x = 5", str(v))
    check(run('func f(const vector<u64>& xs) -> u64 {\n    return 300 as u8;\n}\n') == 44,
          "300 as u8 → 44")
    check(run('func f(const vector<u64>& xs) -> u64 {\n    return 7 as u64;\n}\n') == 7,
          "7 as u64 恒等")


def test_named_args():
    print("[6] 具名实参按名字绑定（不是按位置）")
    tu = ("spec g(u64 a, u64 b) -> u64 { effects {}; }\n"
          "spec f(u64 a, u64 b) -> u64 { effects {}; " + TARGET + "}\n")
    el = ("func g(u64 a, u64 b) -> u64 { return a * 10 + b; }\n"
          "func f(u64 a, u64 b) -> u64 { return g(b = 1, a = 2); }\n")
    tu_m = parse_source(tu, "t.tu").module
    el_m = parse_source(el, "t.eln").module
    v = Interp(el_m, tu_m).call("f", [2, 1])
    check(v == 21, "g(b=1, a=2) → 21（按位置会是 12）", str(v))


def main() -> int:
    test_range_for()
    test_break_continue()
    test_div()
    test_assign_op()
    test_field_and_cast()
    test_named_args()
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
