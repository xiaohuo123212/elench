#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_borrow_and_arity.py —— 三条曾经"静默放行"的边界。

1. **用户函数 arity**：`g(1)` 传给两参函数，`check` 以前零诊断 → `E-Call-Arity`；
2. **`const T&` 不可写**：`xs.push(0)` 以前会改掉调用方数据 → `E-Borrow-Const`；
3. **`array<T,N>` 的容量可以是常量表达式**（`N + 1`），超容 push 必须报错
   （以前表达式容量 → None → 静默退化成普通 `vector`）。
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

from checker import check_module
from interp import Interp, InterpError
from parser import parse_source

_passed = 0
_failed = []


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def codes(tu, eln):
    r = check_module(tu, eln, "t.tu")
    return [d.code for d in r.diagnostics if d.severity == "error"]


def run(tu, eln, args=()):
    tu_m = parse_source(tu, "t.tu").module
    el = parse_source(eln, "t.eln").module
    return Interp(el, tu_m).call("f", list(args))


def test_arity():
    print("[1] 用户函数 arity")
    c = codes("spec g(u64 a, u64 b) -> u64 { effects {}; }\n"
              "spec f(const vector<u64>& xs) -> u64 { effects {}; }\n",
              "u64 g(u64 a, u64 b) { return a; }\n"
              "u64 f(const vector<u64>& xs) { return g(1); }\n")
    check("E-Call-Arity" in c, "少给实参 → E-Call-Arity", str(c))
    c2 = codes("spec g(u64 a) -> u64 { effects {}; }\n"
               "spec f(const vector<u64>& xs) -> u64 { effects {}; }\n",
               "u64 g(u64 a) { return a; }\n"
               "u64 f(const vector<u64>& xs) { return g(1); }\n")
    check("E-Call-Arity" not in c2, "匹配时不报", str(c2))


def test_const_borrow():
    print("[2] const 形参不可写")
    tu = "spec f(const vector<u64>& xs) -> u64 { effects {}; }\n"
    el = "u64 f(const vector<u64>& xs) { xs.push(0); return xs.length(); }\n"
    data = [1, 2]
    try:
        run(tu, el, [data])
        check(False, "const 形参调 push → 应报错", "没抛")
    except InterpError as exc:
        check("E-Borrow-Const" in str(exc), "const 形参调 push → E-Borrow-Const", str(exc)[:60])
    check(data == [1, 2], "调用方数据没被改", str(data))

    # 可变借用仍可写
    tu2 = "spec f(vector<u64>& xs) -> u64 { effects {}; }\n"
    el2 = "u64 f(vector<u64>& xs) { xs.push(0); return xs.length(); }\n"
    d2 = [1, 2]
    check(run(tu2, el2, [d2]) == 3, "可变借用照常可写")




def test_fixedvec_capacity():
    print("[3] array：容量支持常量表达式，超容 push 报错")
    from interp import _fixed_capacity
    tu_src = "const N: u64 = 2;\nspec f(array<u64, N + 1> v) -> u64 { effects {}; }\n"
    tu = parse_source(tu_src, "t.tu").module
    specs = [c for c in tu.children if c.kind == "spec"]
    check(bool(specs), "array<u64, N + 1> 能解析", tu_src)
    if specs:
        ps = next(c for c in specs[0].children if c.kind == "params")
        ty = ps.children[0].children[0]
        check(_fixed_capacity(ty, {"N": 2}) == 3, "N + 1 → 3",
              str(_fixed_capacity(ty, {"N": 2})))

    tu2 = "spec f(const vector<u64>& xs) -> u64 { effects {}; }\n"
    el2 = ("u64 f(const vector<u64>& xs) {\n"
           "    array<u64, 3> v = array<u64, 3>::make();\n"
           "    v.push(1); v.push(2); v.push(3); v.push(4);\n"
           "    return v.length();\n}\n")
    try:
        got = run(tu2, el2, [[1]])
    except Exception as exc:                            # noqa: BLE001
        check(True, "超容 push → 报错", str(exc)[:60])
    else:
        check(False, "超容 push → 应报错", str(got))


def main() -> int:
    test_arity()
    test_const_borrow()
    test_fixedvec_capacity()
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
