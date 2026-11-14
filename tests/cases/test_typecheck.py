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

SAMPLE = os.path.join(HERE, "..", "文档", "样例")

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
    c = codes(TU, "u64 f(u64 x) {\n    return y;\n}")
    check("E-Name-Undefined" in c, "用了未声明的 y", str(c))

    c2 = codes(TU, "u64 f(u64 x) {\n    return x;\n}")
    check("E-Name-Undefined" not in c2, "形参 x 是声明的", str(c2))


def test_shadowing():
    print("[2] T-3 禁止遮蔽（用户定）")
    src = """u64 f(u64 x) {
    u64 y = 1;
    if (x > 0) {
        u64 y = 2;
    }
    return y;
}"""
    c = codes(TU, src)
    check("E-Name-Shadowed" in c, "内层重新声明 y → E-Name-Shadowed", str(c))

    ok = """u64 f(u64 x) {
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
    c = codes(TU, "u64 f(u64 x) {\n    return true;\n}")
    check("E-Type-Return" in c, "返回 u64 却给 bool", str(c))

    c2 = codes(TU, "u64 f(u64 x) {\n    return x;\n}")
    check("E-Type-Return" not in c2, "返回 u64 给 u64", str(c2))


def test_assign():
    print("[4] T-9 赋值/声明类型")
    c = codes(TU, "u64 f(u64 x) {\n    u64 a = true;\n    return a;\n}")
    check("E-Type-Assign" in c, "u64 用 bool 初始化", str(c))


def test_cond():
    print("[5] T-10 条件必须是 bool")
    c = codes(TU, "u64 f(u64 x) {\n    if (x) { return 1; }\n    return 0;\n}")
    check("E-Type-Cond" in c, "if 条件用了 u64", str(c))


def test_try():
    print("[6] T-6 `?` 只能用于 Result")
    c = codes(TU, "u64 f(u64 x) {\n    return x?;\n}")
    check("E-Try-NotResult" in c, "对 u64 用 `?`", str(c))

    ok = """spec g(u64 x) -> u64! { effects {}; }
u64 f(u64 x) {
    return g(x)?;
}"""
    c2 = codes(TU, ok)
    check("E-Try-NotResult" not in c2, "对 `T!` 函数用 `?` 合法", str(c2))


# ---------------------------------------------------------------- 枚举

def test_variant():
    print("[7] T-8 枚举变体存在性")
    c = codes(TU, "u64 f(u64 x) {\n    const Color c = Color::Purple;\n    return 1;\n}")
    check("E-Variant-Unknown" in c, "Color 没有 Purple", str(c))

    c2 = codes(TU, "u64 f(u64 x) {\n    const Color c = Color::Red;\n    return 1;\n}")
    check("E-Variant-Unknown" not in c2, "Color::Red 合法", str(c2))

    # error 类型是开放的（E3 缺口），不校验变体
    c3 = codes(TU, "u64 f(u64 x) {\n    const MyErr e = MyErr::Whatever;\n    return 1;\n}")
    check("E-Variant-Unknown" not in c3, "error 类型变体开放（E3 缺口）", str(c3))


def test_exhaustive():
    print("[8] T-7 match 穷尽性")
    src = """u64 f(Color c) {
    match c {
        Color::Red => { return 1; }
    }
    return 0;
}"""
    c = codes(TU, src)
    check("E-Match-NonExhaustive" in c, "只覆盖 Red → 不穷尽", str(c))

    ok = """u64 f(Color c) {
    match c {
        Color::Red => { return 1; }
        Color::Green => { return 2; }
        Color::Blue => { return 3; }
    }
    return 0;
}"""
    check("E-Match-NonExhaustive" not in codes(TU, ok), "三个变体全覆盖", str(codes(TU, ok)))

    wild = """u64 f(Color c) {
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
u64 f(bool b) {
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
    ok = typecheck(parse_source(TU, "t.tu").module, parse_source("""pub u64! g(Token t) {
    match t {
        Token::Number(n) => { return n; }
        Token::Plus => { return 0; }
    }
}""", "t.eln").module)
    check(not ok, "模式绑定 n 被声明", str([d.code for d in ok]))

    # 未绑定的名字仍要报
    bad = typecheck(parse_source(TU, "t.tu").module, parse_source("""pub u64! g(Token t) {
    match t {
        Token::Number(n) => { return m; }
        Token::Plus => { return 0; }
    }
}""", "t.eln").module)
    check(any(d.code == "E-Name-Undefined" for d in bad),
          "未绑定的 m 仍报 E-Name-Undefined", str([d.code for d in bad]))


def test_bitwise():
    """位运算：只收整数、结果类型按既有「无符号优先」规则（2026-11-09）。"""
    print("[?] 位运算类型")
    ok = codes(TU, """u64 f(u64 x) {
    u64 y = 12;
    return (x & y) | (x ^ y) | (x >> 1) | (x << 1);
}""")
    check("E-Type-Bitwise" not in ok, "u64 位运算放行", str(ok))

    ok = codes(TU, """u64 f(u64 x) {
    i64 y = -1;
    return x | y;
}""")
    check("E-Type-Bitwise" not in ok, "u64 与 i64 混算放行（无符号优先）", str(ok))

    bad = codes(TU, """u64 f(u64 x) {
    f64 y = 1.5;
    return x & y;
}""")
    check("E-Type-Bitwise" in bad, "浮点位运算被拒", str(bad))

    bad = codes(TU, """u64 f(u64 x) {
    return x & (x == 1);
}""")
    check("E-Type-Bitwise" in bad, "bool 参与位运算被拒（不静默当 int）", str(bad))

    bad = codes(TU, """u64 f(u64 x) {
    f64 y = 1.5;
    return ~y;
}""")
    check("E-Type-Bitwise" in bad, "`~` 对浮点被拒", str(bad))

    ok = codes(TU, """u64 f(u64 x) {
    return ~x;
}""")
    check("E-Type-Bitwise" not in ok, "`~u64` 放行", str(ok))

    # 结果类型（静态）——规则与运行期 `_ubin` 的位宽一致
    from typecheck import Ty, _bitwise_result_ty
    pairs = [
        ("u64", "u64", "u64"), ("u8", "u64", "u64"), ("u64", "i64", "u64"),
        ("u8", "i64", "u8"), ("i8", "i64", "i64"), ("i64", "i64", "i64"),
    ]
    for a, b, want in pairs:
        got = _bitwise_result_ty(Ty("name", a), Ty("name", b)).render()
        check(got == want, f"{a} & {b} 的静态类型 → {want}", got)

    # 移位的结果类型**只看左操作数**（C/Rust 规则，2026-11-09）：移位量只提供
    # 值。旧实现按「两边最宽」推导成 u64，运行期也真的在 64 位上算——于是
    # `u32 x; u64 n; x << (32 - n)` 得 2^32，`u32 m` 版本得 0。
    from typecheck import _shift_result_ty
    pairs = [
        ("u32", "u64", "u32"), ("u8", "u64", "u8"), ("u64", "u32", "u64"),
        ("i64", "u64", "i64"), ("i64", "i64", "i64"), ("u32", "i64", "u32"),
    ]
    for a, b, want in pairs:
        got = _shift_result_ty(Ty("name", a), Ty("name", b)).render()
        check(got == want, f"{a} << {b} 的静态类型 → {want}", got)


def test_pow():
    """`**` 的类型规则（2026-11-10）：浮点任意指数；整数只收非负字面量常量。"""
    print("[?] `**`：整数只收常量指数，浮点任意")

    def no_pow_err(c):
        return ("E-Type-Pow-ExponentLiteral" not in c
                and "E-Type-Pow-NegativeExponent" not in c)

    for name, src in (
        ("整数 ** 字面量指数", "u64 f(u64 x) {\n    return x ** 2;\n}"),
        ("指数 0", "u64 f(u64 x) {\n    return x ** 0;\n}"),
        ("括号包起来的字面量", "u64 f(u64 x) {\n    return x ** (2);\n}"),
        # 右结合后指数是 `2 ** 3`——常量表达式，可折，放行
        ("右结合的 `x ** 2 ** 3`",
         "u64 f(u64 x) {\n    return x ** 2 ** 3;\n}"),
        # 平方根通路：`u64 ** f64` 提升成浮点幂
        ("`u64 ** f64`（平方根通路）",
         "f64 f(u64 x) {\n    return x ** 0.5;\n}"),
        ("`f64 ** u64`（任意指数）",
         "f64 f(f64 x, u64 n) {\n    return x ** n;\n}"),
        ("浮点负指数", "f64 f(f64 x) {\n    return x ** -1.0;\n}"),
    ):
        c = codes(TU, src)
        check(no_pow_err(c), f"放行：{name}", str(c))

    bad = codes(TU, "u64 f(u64 x) {\n    return x ** -1;\n}")
    check("E-Type-Pow-NegativeExponent" in bad,
          "整数负指数 → E-Type-Pow-NegativeExponent（0.5 不是整数）", str(bad))

    bad = codes(TU, "u64 f(u64 x, u64 n) {\n    return x ** n;\n}")
    check("E-Type-Pow-ExponentLiteral" in bad,
          "运行期指数 → E-Type-Pow-ExponentLiteral（溢出语义说不清）", str(bad))

    bad = codes(TU, """u64 f(u64 x) {
    const u64 k = 10;
    return x ** k;
}""")
    check("E-Type-Pow-ExponentLiteral" in bad,
          "`const` 名字也不算常量（要求写非负字面量）", str(bad))

    # 结果类型：整数幂**随左操作数**（`u8 ** 2` 仍是 u8，没被提升成 u64）
    tu = parse_source(TU, "t.tu").module
    ch = parse_source("""u64 f(u8 x, u64 y) {
    u8 a = x ** 2;
    u64 b = y ** 2;
    return b;
}""", "t.eln").module
    diags = [d.code for d in typecheck(tu, ch) if d.code.startswith("E-Type-")]
    check(diags == [], "`u8 ** 2` 赋给 u8 不报（结果类型随左操作数）", str(diags))


def test_aggregate_guards():
    """struct / 元组 / 函数值字段 / 局部引用 / 常量初值：**check 必须挡**。

    这一组全是"check 全绿、run 才炸"（宿主 `TypeError` / `InterpError`）
    或"run 也不炸但静默错值"的写法。判据都是静态已知的，所以钉在类型层。
    """
    print("[?] 聚合值 / 局部引用 / 常量初值：静态诊断")
    TU2 = """struct Point { x: u64, y: u64, }
spec probe() -> u64 { effects {}; }"""

    def one(ch: str):
        return codes(TU2, ch)

    def has(code, ch, label):
        c = one(ch)
        check(code in c, label, str(c))

    has("E-Type-StructMethod",
        "u64 probe() {\n    const Point p = Point { x: 1, y: 2 };\n"
        "    return p.sum();\n}", "`p.sum()`：struct 没有成员函数")
    has("E-Type-StructOp",
        "u64 probe() {\n    const Point a = Point { x: 1, y: 2 };\n"
        "    const Point b = Point { x: 3, y: 4 };\n    const Point c = a + b;\n"
        "    return c.x;\n}", "`a + b`：struct 不能相加")
    has("E-Type-StructOp",
        "u64 probe() {\n    const Point a = Point { x: 1, y: 2 };\n"
        "    const Point b = Point { x: 1, y: 3 };\n"
        "    if (a < b) { return 1; }\n    return 0;\n}", "`a < b`：struct 没有顺序")
    has("E-Type-StructOp",
        "u64 probe() {\n    vector<Point> ps = { Point { x: 3, y: 0 } };\n"
        "    sort(ps);\n    return ps.at(0).x;\n}", "`sort(vector<struct>)`：元素不可比较")
    has("E-Format-ArgType",
        "u64 probe() {\n    const Point a = Point { x: 1, y: 2 };\n"
        "    print(\"p={}\", a);\n    return 0;\n}", "`print(\"p={}\", struct)`：没有标量形态")
    has("E-Type-UnknownField",
        "u64 probe() {\n    const (u64, u64) p = (4, 5);\n"
        "    return p.first;\n}", "`p.first`：元组只有位置字段")
    has("E-Type-UnknownField",
        "u64 probe() {\n    const Point p = Point { x: 1, y: 2 };\n"
        "    return p.z;\n}", "`p.z`：struct 没有这个字段（消息列字段）")
    has("E-Ref-Local",
        "u64 probe() {\n    vector<u64> ys = {1, 2, 3};\n"
        "    vector<u64>& r = ys;\n    r[0] = 9;\n    return ys.at(0);\n}",
        "局部 `T&`：本语言没有局部引用（以前是静默拷贝）")
    has("E-Const-NotConstant",
        "const X: u64 = NOPE + 1;\nfunc probe() -> u64 { return X; }",
        "常量初值引用了不存在的名字")
    has("E-Const-NotConstant",
        "const A: u64 = B;\nconst B: u64 = 1;\n"
        "u64 probe() { return A; }",
        "常量初值引用**后面**才声明的常量（装载时求不出）")
    c = codes("const X: u64 = true;\nspec probe() -> u64 { effects {}; }",
              "u64 probe() { return X; }")
    check("E-Type-Assign" in c,
          "常量初值类型不符（`u64 = true`）→ E-Type-Assign", str(c))

    # 对照：合法的写法不能被误杀
    ok = one("u64 probe() {\n    const Point a = Point { x: 1, y: 2 };\n"
             "    const Point b = Point { x: 1, y: 2 };\n"
             "    if (a == b) { return a.x + b.y; }\n    return 0;\n}")
    check("E-Type-StructOp" not in ok and "E-Type-UnknownField" not in ok,
          "struct 的 `==` 与字段读放行", str(ok))
    ok = codes("const N: u64 = 10 * 2;\nconst M: u64 = N + 1;\n"
               "spec probe() -> u64 { effects {}; }",
               "u64 probe() { return N + M; }")
    check("E-Const-NotConstant" not in ok,
          "常量算术初值（`10 * 2` / `N + 1`）放行", str(ok))

    TU3 = """struct Op { f: func(u64) -> u64, }
spec inc(u64 x) -> u64 { effects {}; }
spec probe() -> u64 { effects {}; }"""
    ch3 = parse_source(
        "u64 inc(u64 x) { return x + 1; }\n"
        "u64 probe() { const Op o = Op { f: inc }; return 0; }",
        "t.eln").module
    c3 = [d.code for d in typecheck(parse_source(TU3, "t.tu").module, ch3)]
    check("E-Type-FuncValueField" in c3,
          "struct 字段声明成函数类型 → E-Type-FuncValueField", str(c3))

    # `u64&` 形参：写不回调用方（静默）→ 静态拒绝；容器 / struct 引用仍可用
    TU4 = """struct Point { x: u64, y: u64, }
spec bump(u64& x) -> u64 { effects {}; }
spec touch(vector<u64>& xs) -> u64 { effects {}; }
spec bump_point(Point& p) -> u64 { effects {}; }"""
    c4 = codes(TU4, "u64 bump(u64& x) { x = 42; return x; }\n"
                    "u64 touch(vector<u64>& xs) { xs[0] = 1; return xs.at(0); }\n"
                    "u64 bump_point(Point& p) { p.x = 1; return p.x; }")
    check("E-Ref-Scalar" in c4, "`u64& x` 形参 → E-Ref-Scalar（写不回调用方）", str(c4))
    check(c4.count("E-Ref-Scalar") == 1,
          "容器 / struct 引用形参不误报（只有标量那一条）", str(c4))


def test_wrapped_to_scalar():
    """K5（2026-11-10）：`Option` / `Result` **不许静默当标量**。

    事故形态：`vec_pop` 登记 `ret="any"` → 类型是 `UNKNOWN` → `UNKNOWN` 与任何
    类型兼容，于是

        u64 x = vec_pop(h);   // check 全绿
        print(x == 9);        // 运行期打印 False —— 值在，但不是你以为的值

    `x == 9` **不报类型错**、静默给 `false`，是这里最坏的一条：错值不像错类型
    那样会被发现。所以这一组把「赋值 / 返回 / 比较 / 算术 / 下标的静默降级」
    全钉在静态层，并留两条**正确写法**作正对照（别把正常用它也挡了）。
    """
    print("[?] K5：Option / Result 不能静默当标量")
    _h = "    vector<u64> h;\n    h.push_back(9);\n"

    def one(body: str, ret: str = "u64"):
        return codes(TU, f"func f(u64 x) -> {ret} {{\n{_h}{body}}}")

    c = one("    u64 y = vec_pop(h);\n    return y;\n")
    check("E-Type-OptionToScalar" in c,
          "`u64 y = vec_pop(h)` → E-Type-OptionToScalar", str(c))

    c = one("    bool b = vec_pop(h);\n    return 0;\n")
    check("E-Type-OptionToScalar" in c,
          "`bool b = vec_pop(h)` 同样挡（目标类型真的查了）", str(c))

    c = one("    return vec_pop(h);\n")
    check("E-Type-Return" in c,
          "`-> u64` 里 `return vec_pop(h)` → E-Type-Return", str(c))

    c = one("    return vec_pop(h) == 9;\n", ret="bool")
    check("E-Type-OptionToScalar" in c,
          "`vec_pop(h) == 9` 不再静默为 false", str(c))

    c = one("    return vec_pop(h) + 1;\n")
    check("E-Type-OptionToScalar" in c,
          "`vec_pop(h) + 1` 不再落到宿主 TypeError", str(c))

    c = one("    u64 y = parse_u64(\"9\");\n    return y;\n")
    check("E-Type-OptionToScalar" in c,
          "`u64 y = parse_u64(...)`（Result）也挡", str(c))

    c = one("    u64 y = vec_pop(h)[0];\n    return y;\n")
    check("E-Type-OptionToScalar" in c,
          "`vec_pop(h)[0]`（对包装值取下标）也挡", str(c))

    ok = one("    optional<u64> o = vec_pop(h);\n    return option_unwrap(o);\n")
    check(not ok, "正确写法 ①：`optional<u64> o` + `option_unwrap`", str(ok))

    ok = one("    return option_unwrap_or(vec_pop(h), 0);\n")
    check(not ok, "正确写法 ②：`option_unwrap_or(vec_pop(h), 0)` 直接得 u64", str(ok))

    # `utf8_chars_vec` 登记的是 `ret="vec-u64"`，旧实现漏了这个分支 →
    # 把 `vector<u64>` 说成 `u64`（假阳性，同样属于"登记没人消费"）。
    ok = codes(TU, "u64 f(u64 x) {\n"
                   "    vector<u64> v = utf8_chars_vec(\"ab\");\n"
                   "    return v.size();\n}")
    check(not ok, "`utf8_chars_vec` 的返回类型现在是 vector<u64>", str(ok))


def test_map_free_fns():
    """②（2026-11-10，`_tier_b/structs` K2）：`map_*` 自由函数只吃 `HashMap`。

    事故形态：`map_put(map<u8,u64> m, k, v)` **check 全绿、run 抛 TypeError**
    （"map_put 的第一个实参不是 HashMap"）；`unordered_map` 同样；而文档的
    「映射表」4 条没写适用范围。

    现在两种类型**名义上分开**：`map_new()` 返回 `HashMap<?,?>`（哈希 + 有界
    探测，`O(1)`），语言的 `map<K,V>` 是另一回事（平衡树，`O(log n)`）。
    所以 `check` 就能挡住"自由函数作用在语言 map 上"，并给出方法式写法。
    """
    print("[?] ②：`map_put` / `map_get` / `map_has` / `map_len` 只接受 HashMap")

    for call in ("map_put(m, 1, 10)", "map_get(m, 1)", "map_has(m, 1)",
                 "map_len(m)"):
        c = codes(TU, "u64 f(u64 x) {\n"
                      "    map<u64, u64> m;\n"
                      f"    return {call};\n}}")
        check("E-Type-ArgMismatch" in c,
              f"语言 map 上的 `{call}` → E-Type-ArgMismatch", str(c))

    c = codes(TU, "u64 f(u64 x) {\n"
                  "    unordered_map<u64, u64> m;\n"
                  "    return map_len(m);\n}")
    check("E-Type-ArgMismatch" in c,
          "`unordered_map` 上的 `map_len` 同样挡", str(c))

    ok = codes(TU, "u64 f(u64 x) {\n"
                   "    HashMap<u64, u64> h = map_new();\n"
                   "    map_put(h, 1, 10);\n"
                   "    map_has(h, 1);\n"
                   "    return map_len(h);\n}")
    check(not ok, "`HashMap<…> h = map_new()` + 自由函数 → 不报错", str(ok))

    ok = codes(TU, "u64 f(u64 x) {\n"
                   "    map<u64, u64> m;\n"
                   "    m.insert(1, 10);\n"
                   "    return m.size();\n}")
    check(not ok, "语言 map 的方法式写法 → 不报错", str(ok))

    # `map<…> m = map_new();` 是**旧写法**：现在两边类型不同，必须报出来
    # （否则作者会以为 m 是语言 map，接着 `m.insert` 撞 TypeError）。
    c = codes(TU, "u64 f(u64 x) {\n"
                  "    map<u64, u64> m = map_new();\n"
                  "    return 0;\n}")
    check("E-Type-Assign" in c,
          "`map<…> m = map_new()` → E-Type-Assign（类型不同）", str(c))


def test_ref_scalar_const():
    """`const u64&` 不是写回契约（R1-A，2026-11-10）。

    事故形态：`u64 add_one(const u64& x)` 是 C++ 里最普通的**只读**形参写法，
    但 `E-Ref-Scalar` 以前不看 `const`，于是**每一个**照 C++ 写的函数签名
    都被报错——`rename-and-go` 在第一个形参就断（实测：`u64 add_one(const u64&)`）。
    `const` 形参在函数体里本来就改不了，解释器按值传，语义与 C++ 完全一致。
    可写的 `T&`（标量）仍然拒：那才是"写不回调用方"。
    """
    print("[?] R1-A：`const T&` 只读形参不报 E-Ref-Scalar，`T&` 仍报")

    ok = codes(TU, "u64 add_one(const u64& x) {\n    return x + 1;\n}")
    check("E-Ref-Scalar" not in ok, "`const u64&` 形参不报 E-Ref-Scalar", str(ok))

    bad = codes(TU, "u64 add_two(u64& x) {\n    x = x + 1;\n    return x;\n}")
    check("E-Ref-Scalar" in bad, "可写的 `u64&` 形参照旧报 E-Ref-Scalar", str(bad))

    ok = codes(TU, "u64 total(const std::vector<u64>& xs) {\n"
                   "    return xs.size();\n}")
    check("E-Ref-Scalar" not in ok, "`const std::vector<u64>&` 形参同样不报", str(ok))


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
    test_bitwise()
    test_pow()
    test_aggregate_guards()
    test_wrapped_to_scalar()
    test_map_free_fns()
    test_ref_scalar_const()

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
