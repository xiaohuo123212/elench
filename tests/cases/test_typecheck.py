#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_typecheck.py —— 名字解析 + 类型检查

运行：
  cd src; $env:PYTHONIOENCODING="utf-8"; python test_typecheck.py
"""

import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但**这批测试原先住在 `src/`**，里面大量
#: `os.path.join(HERE, "..", "文档")` / `os.path.join(ROOT, "tools")` 都假设
#: 「本文件在 src/、上一级是仓库根」。搬家时改 200 处路径比让 `HERE` 继续指 src 贵得多，
#: 所以保留旧语义：`HERE` = src/，`CASEDIR` = 本文件真正所在的目录。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from parser import parse_source
from typecheck import typecheck

SAMPLE = os.path.join(HERE, "..", "文档", "设计", "目前设计", "样例")

_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def codes(tu_src, ch_src):
    tu = parse_source(tu_src, "t.tu").module
    ch = parse_source(ch_src, "t.eln").module
    return [d.code for d in typecheck(tu, ch)]


TU = """enum Color { Red, Green, Blue }
error MyErr;
spec f(u64 x) -> u64 { effects {}; }"""


# ---------------------------------------------------------------- 名字

def test_undefined():
    print("[1] T-1 未声明的名字")
    c = codes(TU, "func f(u64 x) -> u64 {\n    return y;\n}")
    check("E-Name-Undefined" in c, "用了未声明的 y", str(c))

    c2 = codes(TU, "func f(u64 x) -> u64 {\n    return x;\n}")
    check("E-Name-Undefined" not in c2, "形参 x 是声明的", str(c2))


def test_shadowing():
    print("[2] T-3 禁止遮蔽（用户定）")
    src = """func f(u64 x) -> u64 {
    u64 y = 1;
    if (x > 0) {
        u64 y = 2;
    }
    return y;
}"""
    c = codes(TU, src)
    check("E-Name-Shadowed" in c, "内层重新声明 y → E-Name-Shadowed", str(c))

    ok = """func f(u64 x) -> u64 {
    u64 a = 1;
    if (x > 0) {
        u64 b = 2;
    }
    return a;
}"""
    check("E-Name-Shadowed" not in codes(TU, ok), "不同名字不报", str(codes(TU, ok)))


# ---------------------------------------------------------------- 类型

def test_return():
    print("[3] T-4 返回类型")
    c = codes(TU, "func f(u64 x) -> u64 {\n    return true;\n}")
    check("E-Type-Return" in c, "返回 u64 却给 bool", str(c))

    c2 = codes(TU, "func f(u64 x) -> u64 {\n    return x;\n}")
    check("E-Type-Return" not in c2, "返回 u64 给 u64", str(c2))


def test_assign():
    print("[4] T-9 赋值/声明类型")
    c = codes(TU, "func f(u64 x) -> u64 {\n    u64 a = true;\n    return a;\n}")
    check("E-Type-Assign" in c, "u64 用 bool 初始化", str(c))


def test_cond():
    print("[5] T-10 条件必须是 bool")
    c = codes(TU, "func f(u64 x) -> u64 {\n    if (x) { return 1; }\n    return 0;\n}")
    check("E-Type-Cond" in c, "if 条件用了 u64", str(c))


def test_try():
    print("[6] T-6 `?` 只能用于 Result")
    c = codes(TU, "func f(u64 x) -> u64 {\n    return x?;\n}")
    check("E-Try-NotResult" in c, "对 u64 用 `?`", str(c))

    ok = """spec g(u64 x) -> u64! { effects {}; }
func f(u64 x) -> u64 {
    return g(x)?;
}"""
    c2 = codes(TU, ok)
    check("E-Try-NotResult" not in c2, "对 `T!` 函数用 `?` 合法", str(c2))


# ---------------------------------------------------------------- 枚举

def test_variant():
    print("[7] T-8 枚举变体存在性")
    c = codes(TU, "func f(u64 x) -> u64 {\n    const Color c = Color::Purple;\n    return 1;\n}")
    check("E-Variant-Unknown" in c, "Color 没有 Purple", str(c))

    c2 = codes(TU, "func f(u64 x) -> u64 {\n    const Color c = Color::Red;\n    return 1;\n}")
    check("E-Variant-Unknown" not in c2, "Color::Red 合法", str(c2))

    # error 类型是开放的（E3 缺口），不校验变体
    c3 = codes(TU, "func f(u64 x) -> u64 {\n    const MyErr e = MyErr::Whatever;\n    return 1;\n}")
    check("E-Variant-Unknown" not in c3, "error 类型变体开放（E3 缺口）", str(c3))


def test_exhaustive():
    print("[8] T-7 match 穷尽性")
    src = """func f(Color c) -> u64 {
    match c {
        Color::Red => { return 1; }
    }
    return 0;
}"""
    c = codes(TU, src)
    check("E-Match-NonExhaustive" in c, "只覆盖 Red → 不穷尽", str(c))

    ok = """func f(Color c) -> u64 {
    match c {
        Color::Red => { return 1; }
        Color::Green => { return 2; }
        Color::Blue => { return 3; }
    }
    return 0;
}"""
    check("E-Match-NonExhaustive" not in codes(TU, ok), "三个变体全覆盖", str(codes(TU, ok)))

    wild = """func f(Color c) -> u64 {
    match c {
        Color::Red => { return 1; }
        _ => { return 0; }
    }
}"""
    check("E-Match-NonExhaustive" not in codes(TU, wild), "有 `_` 就穷尽",
          str(codes(TU, wild)))


def test_args():
    print("[9] T-5 实参类型")
    src = """spec g(u64 x) -> u64 { effects {}; }
func f(bool b) -> u64 {
    return g(b);
}"""
    c = codes(TU, src)
    check("E-Type-ArgMismatch" in c, "把 bool 传给 u64 形参", str(c))


# ---------------------------------------------------------------- 真实样例

def test_samples():
    print("[10] 三套真实样例必须零诊断")
    for base in ("calculator", "search", "sum"):
        tu = open(os.path.join(SAMPLE, base + ".tu"), encoding="utf-8").read()
        ch = open(os.path.join(SAMPLE, base + ".eln"), encoding="utf-8").read()
        c = codes(tu, ch)
        check(c == [], f"{base} 零诊断", str(c))


def test_match_binding():
    """`Token::Number(n)` 的绑定名 MUST 被声明（否则最常用的 enum+match 写法过不了）。"""
    print("[?] match 模式绑定")
    TU = """error E;
enum Token { Number(u64), Plus }
spec g(Token t) -> u64 {
    effects {};
}
"""
    ok = typecheck(parse_source(TU, "t.tu").module, parse_source("""pub func g(Token t) -> u64! {
    match t {
        Token::Number(n) => { return n; }
        Token::Plus => { return 0; }
    }
}""", "t.eln").module)
    check(not ok, "模式绑定 n 被声明", str([d.code for d in ok]))

    # 未绑定的名字仍要报
    bad = typecheck(parse_source(TU, "t.tu").module, parse_source("""pub func g(Token t) -> u64! {
    match t {
        Token::Number(n) => { return m; }
        Token::Plus => { return 0; }
    }
}""", "t.eln").module)
    check(any(d.code == "E-Name-Undefined" for d in bad),
          "未绑定的 m 仍报 E-Name-Undefined", str([d.code for d in bad]))


def main():
    test_match_binding()
    test_undefined()
    test_shadowing()
    test_return()
    test_assign()
    test_cond()
    test_try()
    test_variant()
    test_exhaustive()
    test_args()
    test_samples()

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
