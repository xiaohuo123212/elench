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
    v = run('u64 f(const vector<u64>& xs) {\n'
            '    u64 s = 0;\n    for (u64 i = 0; i < 3; i = i + 1) { s = s + 1; }\n    return s;\n}\n')
    check(v == 3, "0..3 执行 3 次", str(v))
    v0 = run('u64 f(const vector<u64>& xs) {\n'
             '    u64 s = 0;\n    for (u64 i = 0; i < 0; i = i + 1) { s = s + 1; }\n    return s;\n}\n')
    check(v0 == 0, "0..0 执行 0 次", str(v0))


def test_break_continue():
    print("[2] break / continue")
    v = run('u64 f(const vector<u64>& xs) {\n'
            '    u64 i = 0;\n    while (true) { i = i + 1; break; }\n    return i;\n}\n')
    check(v == 1, "break 跳出", str(v))
    v2 = run('u64 f(const vector<u64>& xs) {\n'
             '    u64 s = 0;\n    u64 i = 0;\n'
             '    while (i < 5) { i = i + 1; if (i == 3) { continue; } s = s + 1; }\n'
             '    return s;\n}\n')
    check(v2 == 4, "continue 跳过 i==3", str(v2))


def test_div():
    print("[3] 除法/取模返回整数，除零报错")
    check(run('u64 f(const vector<u64>& xs) {\n    u64 a = 6;\n'
              '    u64 b = 2;\n    return a / b;\n}\n') == 3, "6/2 = 3")
    check(run('u64 f(const vector<u64>& xs) {\n    u64 a = 6;\n'
              '    u64 b = 4;\n    return a % b;\n}\n') == 2, "6%4 = 2")
    try:
        run('u64 f(const vector<u64>& xs) {\n    u64 a = 6;\n'
            '    u64 b = 0;\n    return a / b;\n}\n')
        check(False, "除零应报错", "没抛")
    except InterpError as exc:
        check("E-Div-ByZero" in str(exc), "除零 → E-Div-ByZero", str(exc))
        # 除零是**语言诊断**：消息要能教人怎么改，而不是一句"除以零"。
        check("消息给出 `requires b != 0;` 修法",
              "requires b != 0" in str(exc), str(exc))


def test_assign_op():
    print("[4] 复合赋值回绕")
    v = run('u64 f(const vector<u64>& xs) {\n'
            '    u64 a = 18446744073709551615;\n    a += 1;\n    return a;\n}\n')
    check(v == 0, "2^64-1 += 1 → 0", str(v))


def test_field_and_cast():
    print("[5] 结构体字段赋值 / as 截断")
    tu = "struct P { x: u64 }\n" + TU
    v = run('u64 f(const vector<u64>& xs) {\n'
            '    P p = P { x: 1 };\n    p.x = 5;\n    return p.x;\n}\n', tu=tu)
    check(v == 5, "p.x = 5", str(v))
    check(run('u64 f(const vector<u64>& xs) {\n    return 300 as u8;\n}\n') == 44,
          "300 as u8 → 44")
    check(run('u64 f(const vector<u64>& xs) {\n    return 7 as u64;\n}\n') == 7,
          "7 as u64 恒等")


def test_named_args():
    print("[6] 具名实参按名字绑定（不是按位置）")
    tu = ("spec g(u64 a, u64 b) -> u64 { effects {}; }\n"
          "spec f(u64 a, u64 b) -> u64 { effects {}; " + TARGET + "}\n")
    el = ("u64 g(u64 a, u64 b) { return a * 10 + b; }\n"
          "u64 f(u64 a, u64 b) { return g(b = 1, a = 2); }\n")
    tu_m = parse_source(tu, "t.tu").module
    el_m = parse_source(el, "t.eln").module
    v = Interp(el_m, tu_m).call("f", [2, 1])
    check(v == 21, "g(b=1, a=2) → 21（按位置会是 12）", str(v))


def printed(eln):
    """跑 `f`，把 `print` 的输出收成字符串（断言"打出来的是什么"）。"""
    import contextlib
    import io
    tu_m = parse_source(TU, "t.tu").module
    el = parse_source(eln, "t.eln").module
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        Interp(el, tu_m).call("f", [[1, 2, 3]])
    return buf.getvalue()


def test_u64_literal_context():
    print("[7] 大整数字面量：装不进 i64 的正字面量是 u64 值")
    # 2026-11-09：字面量在值层是任意精度 int，没按上下文标 UInt，于是
    # `2^64-1 - 1` 走 i64 补码回绕成 -2、`0-1 == 2^64-1` 得 False。
    v = run('u64 f(const vector<u64>& xs) {\n'
            '    return 18446744073709551615 - 1;\n}\n')
    check(v == 18446744073709551614, "2^64-1 - 1 = 2^64-2（不再回绕成 -2）", str(v))
    v = run('u64 f(const vector<u64>& xs) {\n'
            '    return 0 - 1 == 18446744073709551615;\n}\n')
    check(v is True, "0-1 与 2^64-1 按无符号相等（不再 False）", str(v))
    v = run('u64 f(const vector<u64>& xs) {\n'
            '    return (0 - 1) == 18446744073709551615;\n}\n')
    check(v is True, "(0-1) 与字面量相等（与 const u64 同一条规则）", str(v))
    v = run('i64 f(const vector<u64>& xs) {\n'
            '    i64 y = 18446744073709551615;\n    return y;\n}\n')
    check(v == -1, "i64 仍是补码：装进去是 -1", str(v))
    check(printed('u64 f(const vector<u64>& xs) {\n'
                  '    print(0 - 1);\n    return 0;\n}\n') == "-1\n",
          "两个都是字面量的 0-1 仍按 i64 得 -1（既有语义不变）")
    check(printed('u64 f(const vector<u64>& xs) {\n'
                  '    print(-9223372036854775808);\n    return 0;\n}\n')
          == "-9223372036854775808\n",
          "`-9223372036854775808`（i64 最小值）仍打得对（拓宽只在运算处）")


def test_int_literal_radix_run():
    print("[8] 进制字面量：run 得对（以前 check 绿、run 抛宿主 ValueError）")
    # 2026-11-09：parser 把 token.text 原样存进 lit-int，解释器 `int(text)`
    # 按十进制重解析 → `u64 x = 0x10;` 运行期
    # `ValueError: invalid literal for int() with base 10: '0x10'`。
    # 现在 parser 存 token.value（词法层算好的数）。
    check(run('u64 f(const vector<u64>& xs) {\n'
              '    u64 x = 0x10;\n    return x;\n}\n') == 16, "0x10 → 16")
    check(run('u64 f(const vector<u64>& xs) {\n'
              '    return 0X1F;\n}\n') == 31, "0X1F（大写前缀）→ 31")
    check(run('u64 f(const vector<u64>& xs) {\n'
              '    return 0b1010;\n}\n') == 10, "0b1010 → 10")
    check(run('u64 f(const vector<u64>& xs) {\n'
              '    return 1_000_000;\n}\n') == 1000000, "1_000_000 → 1000000")
    # u64 上界：值必须原样落地，不能截成 i64（-1）或被当成十进制文本。
    check(run('u64 f(const vector<u64>& xs) {\n'
              '    u64 x = 0xFFFFFFFFFFFFFFFF;\n    return x;\n}\n')
          == 18446744073709551615, "0xFFFFFFFFFFFFFFFF → u64 最大值")
    check(run('u64 f(const vector<u64>& xs) {\n'
              '    return 0xFFFF_FFFF_FFFF_FFFF;\n}\n')
          == 18446744073709551615, "0xFFFF_FFFF_FFFF_FFFF（下划线）→ u64 最大值")
    # **既有 u64 语义不回归**：> i64 的字面量在运算里按 u64（`_widen_oob_literals`）。
    check(run('u64 f(const vector<u64>& xs) {\n'
              '    return 0xFFFFFFFFFFFFFFFF - 1;\n}\n')
          == 18446744073709551614, "0xFFFF…FF - 1 = 2^64-2（仍走 u64 无符号语义）")
    check(printed('u64 f(const vector<u64>& xs) {\n'
                  '    print(18446744073709551615);\n    return 0;\n}\n')
          == "18446744073709551615\n", "print(18446744073709551615) 不变")

    # 溢出 u64 的字面量在**词法层**报错（见 lexer U64_MAX）：
    # `run` 走同一份诊断，所以不会再出现"check 绿、run 崩"。
    bad = parse_source('u64 f() { return 0x1_0000_0000_0000_0000; }',
                       "t.eln")
    check(any(d.code == "E-Lex-IntOverflow" for d in bad.diagnostics),
          "0x1_0000_0000_0000_0000 → E-Lex-IntOverflow（不静默回绕）",
          "; ".join(d.code for d in bad.diagnostics))


#: 256 项位移表 + 一个字节串；`text.at(0)` 是 u8 值 99。
_U8_INDEX_PRELUDE = (
    'u64 f(const vector<u64>& xs) {\n'
    '    vector<u64> t;\n'
    '    u64 i = 0;\n'
    '    while (i < 256) { t.push(i * 10); i = i + 1; }\n'
    '    vector<u8> text;\n'
    '    text.push(99);\n')


def test_u8_element_as_index():
    print("[9] `vector<u8>` 元素当索引：越界判断用真实数值，不按 8 位回绕")
    # 2026-11-09（KMP/BMH 移植实测）：`u8 idx = text.at(0);` 得 99，
    # `t.at(idx)` 的越界判断走 `UInt.__ge__` → 99 % 256 >= 256 % 256
    # → `99 >= 0` → True → 误报 `E-Index-OutOfRange：下标 99 越界（长度 256）`。
    var = (_U8_INDEX_PRELUDE +
           '    u8 idx = text.at(0);\n'
           '    return t.at(idx);\n}\n')
    got = run(var)
    check(got == 990, "u8 变量当索引 → 990（不再误判越界）", str(got))

    inline = (_U8_INDEX_PRELUDE +
              '    return t.at(text.at(0));\n}\n')
    got = run(inline)
    check(got == 990, "内联 `t.at(text.at(0))` → 990", str(got))

    # 上界本身不能松：u8 值 255 在 256 项表里合法（旧代码 255 % 256 = 255 也过，
    # 但这里断言"修完仍取到正确元素"）。
    check(run(_U8_INDEX_PRELUDE +
              '    u8 idx = 255 as u8;\n    return t.at(idx);\n}\n') == 2550,
          "u8 索引 255 + 长度 256 → 2550（真正的边界）")

    # **真越界仍要抓**：修的是"比较用真实数值"，不是把越界检查关掉。
    small = ('u64 f(const vector<u64>& xs) {\n'
             '    vector<u64> t;\n    t.push(1);\n'
             '    u8 idx = 99;\n    return t.at(idx);\n}\n')
    try:
        run(small)
        check(False, "长度 1 的容器用 u8 99 当索引 → E-Index-OutOfRange", "没抛")
    except InterpError as exc:
        check("E-Index-OutOfRange" in str(exc) and "容器长度 1" in str(exc),
              "长度 1 的容器用 u8 99 当索引 → E-Index-OutOfRange（真实上界）",
              str(exc))

    # **u8 的算术回绕是正确语义，不许被这次修复改掉。**
    check(run('u64 f(const vector<u64>& xs) {\n'
              '    u8 a = 200;\n    return a + 100;\n}\n') == 44,
          "u8 a = 200; a + 100 → 44（算术回绕不变）")
    check(run('u64 f(const vector<u64>& xs) {\n'
              '    u8 a = 250;\n    return a + 10;\n}\n') == 4,
          "u8 250 + 10 → 4（跨 256 仍回绕）")

    # 负索引仍报 `E-Index-Negative`（不是被当成巨大的无符号值）。
    try:
        run('u64 f(const vector<u64>& xs) {\n'
            '    i64 i = 0 - 1;\n    return xs[i];\n}\n')
        check(False, "负索引 → E-Index-Negative", "没抛")
    except InterpError as exc:
        check("E-Index-Negative" in str(exc), "i64 负索引 → E-Index-Negative", str(exc))


def test_shift_result_width():
    print("[10] 移位的结果位宽只看左操作数（移位量只提供值）")
    # 2026-11-09（SHA-256 移植实测）：`32 - n` 的类型跟着形参走，而旧实现把
    # 移位的位宽按「两边最宽」算，于是 u64 的移位量把结果抬到 64 位：
    #   u32 x = 1; u64 n = 0; x << (32 - n) → 4294967296（错）
    #   u32 m = 0;             x << (32 - m) → 0（对）
    # 同一个 x、同一个移位值 32，只差移位量的类型，结果差 2^32 倍。
    u64_n = run('u64 f(const vector<u64>& xs) {\n'
                '    u32 x = 1;\n    u64 n = 0;\n'
                '    return x << (32 - n);\n}\n')
    u32_m = run('u64 f(const vector<u64>& xs) {\n'
                '    u32 x = 1;\n    u32 m = 0;\n'
                '    return x << (32 - m);\n}\n')
    check(u64_n == 0 and u32_m == 0,
          "u32 << (32 - u64 n) 与 u32 << (32 - u32 m) 都是 0（32 位）",
          f"u64 n → {u64_n}，u32 m → {u32_m}")
    check(u64_n == u32_m,
          "移位量类型不同、结果相同（正确性不再依赖形参类型）", f"{u64_n} vs {u32_m}")

    # 移位量按**真实数值**判断，不能被左操作数的位宽回绕截断：2^32 若在 32 位里
    # 回绕成 0，就成了「移 0 位」= x（与「u8 当索引」同一类陷阱）。
    check(run('u64 f(const vector<u64>& xs) {\n'
              '    u32 x = 1;\n    u64 k = 4294967296;\n'
              '    return x << k;\n}\n') == 0,
          "u32 << u64(2^32) → 0（移位量不回绕成 0）")
    check(run('u64 f(const vector<u64>& xs) {\n'
              '    u32 x = 1;\n    u64 k = 18446744073709551615;\n'
              '    return x >> k;\n}\n') == 0,
          "u32 >> u64(2^64-1) → 0（按真值判越界）")

    # `u64` 左操作数仍在 64 位上算（结果位宽跟着左操作数，不是永远 32 位）。
    check(run('u64 f(const vector<u64>& xs) {\n'
              '    u64 x = 1;\n    u32 k = 32;\n'
              '    return x << k;\n}\n') == 4294967296,
          "u64 << u32(32) → 4294967296（左操作数是 64 位）")

    # SHA-256 的 rotr：`u32` 形参 vs `u64` 形参必须同结果。旧实现下
    # `rotr_u64(1, 0)` 会算成 4294967297（`x << 32` 在 64 位上）。
    rotr = ('u64 rotr32(u32 x, u32 n) {\n'
            '    return (x >> n) | (x << (32 - n));\n}\n'
            'u64 rotr64(u32 x, u64 n) {\n'
            '    return (x >> n) | (x << (32 - n));\n}\n'
            'u64 f(const vector<u64>& xs) {\n'
            '    print(rotr32(1, 0));\n'
            '    print(rotr64(1, 0));\n'
            '    print(rotr32(305419896, 12));\n'
            '    print(rotr64(305419896, 12));\n'
            '    return 0;\n}\n')
    out = [int(t) for t in printed(rotr).split()]
    check(out[:2] == [1, 1] and out[2:] == [1736516421, 1736516421],
          "rotr：u32 形参与 u64 形参同结果（1 / 1736516421）", str(out))

    # 有符号左操作数仍是 64 位补码（`i64 << n` → `i64`），既有规则不变。
    i64_tu = ("spec f(const vector<u64>& xs) -> i64 { effects {}; "
              + TARGET + "}\n")
    check(run('i64 f(const vector<u64>& xs) {\n'
              '    i64 v = 0 - 8;\n    i64 n = 1;\n'
              '    return v >> n;\n}\n', tu=i64_tu) == -4,
          "i64 -8 >> 1 → -4（算术右移，仍是 i64）")
    check(run('i64 f(const vector<u64>& xs) {\n'
              '    i64 v = 0 - 8;\n    i64 n = 1;\n'
              '    return v << n;\n}\n', tu=i64_tu) == -16,
          "i64 -8 << 1 → -16（补码回绕，仍是 i64）")
    # 右操作数是 `u64` 也不改变结果类型：移位的类型只看左操作数。
    check(run('i64 f(const vector<u64>& xs) {\n'
              '    i64 v = 0 - 8;\n    u64 n = 1;\n'
              '    return v >> n;\n}\n', tu=i64_tu) == -4,
          "i64 -8 >> u64(1) → -4（移位量类型不参与）")

    # **`& | ^` 仍按「两边最宽」**（算术规则，不许跟移位一起改）。
    check(run('u64 f(const vector<u64>& xs) {\n'
              '    u32 a = 4294967295;\n    u64 b = 4294967296;\n'
              '    return a | b;\n}\n') == 8589934591,
          "u32 | u64 仍按 64 位（→ 8589934591）")
    check(run('u64 f(const vector<u64>& xs) {\n'
              '    u8 a = 200;\n    return a << 1;\n}\n') == 144,
          "u8 200 << 1 → 144（u8 位宽回绕不变）")


def main() -> int:
    test_range_for()
    test_break_continue()
    test_div()
    test_assign_op()
    test_field_and_cast()
    test_named_args()
    test_u64_literal_context()
    test_int_literal_radix_run()
    test_u8_element_as_index()
    test_shift_result_width()
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
