#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
test_formatter.py —— M0 格式化器测试

重点验证 M0 验收 ①：**幂等**（fmt(fmt(x)) == fmt(x)）且确定。
另外验证 token 不变、注释不丢、无超宽行。

运行：
  cd src; $env:PYTHONIOENCODING="utf-8"; python test_formatter.py
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

from formatter import MAX_LINE, format_source
from lexer import Kind, Lexer

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


def toks(src):
    return [t.text for t in Lexer(src).run().tokens if t.kind != Kind.EOF]


def comments(src):
    return [t.text.strip() for t in Lexer(src).run().trivia]


def read(name):
    with open(os.path.join(SAMPLE, name), "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- 1. 幂等

def test_idempotent():
    print("[1] 幂等与确定性（验收 ①）")
    for name in ("calculator.tu", "calculator.eln", "search.tu", "search.eln"):
        src = read(name)
        o1 = format_source(src)
        o2 = format_source(o1)
        o3 = format_source(src)
        check(o1 == o2, f"{name}: fmt 幂等", "第二次输出不同")
        check(o1 == o3, f"{name}: fmt 确定", "同输入两次不同")


# ---------------------------------------------------------------- 2. 语义保持

def test_preserve():
    print("[2] token 与注释不变")
    for name in ("calculator.tu", "calculator.eln", "search.tu", "search.eln"):
        src = read(name)
        out = format_source(src)
        check(toks(src) == toks(out), f"{name}: token 序列不变",
              "有 token 被改动/丢失")
        check(comments(src) == comments(out), f"{name}: 注释不丢不改",
              f"{len(comments(src))} → {len(comments(out))}")


# ---------------------------------------------------------------- 3. 宽度

def test_width():
    print("[3] 行宽 ≤ 100（验收 ③）")
    for name in ("calculator.tu", "calculator.eln", "search.tu", "search.eln"):
        out = format_source(read(name))
        over = [ln for ln in out.splitlines() if len(ln) > MAX_LINE]
        check(not over, f"{name}: 无超宽行",
              f"{len(over)} 行超宽，首行 {len(over[0]) if over else 0} 列")

    long_params = ("spec f(" + ", ".join(f"p{i}: u64" for i in range(12))
                   + ") -> u64 { effects pure; }")
    out = format_source(long_params)
    over = [ln for ln in out.splitlines() if len(ln) > MAX_LINE]
    check(not over, "长参数表被折行",
          f"仍有 {len(over)} 行超宽")
    check(format_source(out) == out, "折行后仍幂等")


# ---------------------------------------------------------------- 4. 布局细节

def test_layout():
    print("[4] 布局细节")

    # 块花括号换行、内联花括号保持一行
    src = "spec a() -> u64 { effects pure; target { time: { max: O(1) }, } }"
    out = format_source(src)
    check("effects pure;" in out, "clause 独占一行", out)
    check("{ max: O(1) }" in out, "内联花括号保持一行", out)
    check("}" in out, "`}` 同一行", out)

    # pub / kernels 与 func 不断行
    src2 = "pub u64 a() { return 1; }\nkernels u64 k() { return 1; } kernels"
    out2 = format_source(src2)
    check("pub u64 a" in out2, "pub 不换行", out2)
    check("} kernels" in out2 or "kernels u64 k" in out2, "kernels 与宿主同行", out2)

    # 空格规则
    cases = [
        ("a = xs.at(0);", "xs.at(0)", "方法调用不拆开"),
        ("if (!ok) { return 1; }", "if (!ok)", "`!` 后有空格"),
        ("const vector<u64> a = b;", "vector<u64>", "泛型不拆开"),
        ("a = b?;", "b?", "`?` 紧贴表达式"),
        ("a = (b + c) / 2;", "(b + c) / 2", "括号内正常空格"),
        ("a = v;", "v", "普通语句"),
    ]
    for src3, want, label in cases:
        body = "u64 t() { " + src3 + " return 1; }"
        o = format_source(body)
        check(want in o, label, o.strip().replace("\n", " | "))

    # else 与 } 同行
    src4 = "u64 t() { if (a) { return 1; } else { return 2; } }"
    out4 = format_source(src4)
    check("} else {" in out4, "`} else {` 同一行", out4.replace("\n", " | "))


# ---------------------------------------------------------------- 5. 边界
    # v0.68（C2 反转）：条件 MUST 带括号，fmt 保留它
    for src5, want, label in [
        ("u64 t(x: u64) { if (x == 0) { return 1; } return x; }",
         "if (x == 0) {", "if 条件保留括号"),
        ("u64 t(x: u64) { while (x < 9) { x = x + 1; } return x; }",
         "while (x < 9) {", "while 条件保留括号"),
        ("u64 t(xs: vector<u64>) { for (v in xs) { return 1; } return 0; }",
         "for (v in xs) {", "for 迭代对象保留括号"),
    ]:
        o = format_source(src5)
        check(want in o, label, o.strip().replace("\n", " | "))
        check(format_source(o) == o, label + "（幂等）")

    # 子表达式括号也不动
    for src6, want, label in [
        ("u64 t(x: u64) { if ((x > 0) && (x < 9)) { return 1; } return 0; }",
         "if ((x > 0) && (x < 9)) {", "子表达式括号保留"),
        ("u64 t(x: u64) { while ((x) < 10) { x = x + 1; } return x; }",
         "while ((x) < 10) {", "嵌套括号保留"),
    ]:
        o = format_source(src6)
        check(want in o, label, o.strip().replace("\n", " | "))


def test_edges():
    print("[5] 边界输入")
    check(format_source("") == "", "空输入 → 空输出")
    check(format_source("\n\n") == "", "仅空白 → 空输出")

    # 词法有错：原样返回，不把坏文件改坏
    bad = "const X: u64 = '';  // 空字符字面量，词法应报错"
    check(format_source(bad) == bad, "词法错误时原样返回")

    # 只有注释
    c = "// 只有一行注释"
    out = format_source(c)
    check(out.strip() == c, "只有注释时保留", out)

    # 幂等：再次格式化注释文件
    check(format_source(out) == out, "注释文件幂等")


# ---------------------------------------------------------------- main

# ---------------------------------------------------------------- 6. `?!` 独占行缩进

def test_dispatch_indent():
    print("[6] `?!` 独占行 MUST 缩进恰好一级（规格 C13 第 7 条）")
    # 故意写成 8 空格；格式化后必须回到 4 空格（一级）
    src = """u64! f(const vector<u64>& xs, u64 k) {
    const u64 n = xs.length();
        ?!search_impl(xs, k);
    return 0;
}"""
    out = format_source(src)
    lines = out.split("\n")
    disp = [ln for ln in lines if "?!" in ln]
    check(len(disp) == 1, "只有一行含 ?!", str(disp))
    if disp:
        ind = len(disp[0]) - len(disp[0].lstrip(" "))
        check(ind == 4, "`?!` 独占行缩进一级（4 空格）", f"实际 {ind} 空格：{disp[0]!r}")

    # 幂等：再格式化一遍不变
    check(format_source(out) == out, "`?!` 缩进是幂等的")

    # 块内嵌套时也按层数走（两层 → 8 空格）
    src2 = """u64 g(u64 n) {
    if (n > 0) {
            ?!sum_kernel(n);
    }
    return 0;
}"""
    out2 = format_source(src2)
    d2 = [ln for ln in out2.split("\n") if "?!" in ln]
    check(d2 and (len(d2[0]) - len(d2[0].lstrip(" "))) == 8,
          "嵌套一层时 `?!` 缩进两级（8 空格）", f"{d2}")


# ---------------------------------------------------------------- 7. `++` / `--`

def glued_pairs(src):
    """相邻（源码紧贴、中间无空白）的同类 `+` / `-` 对。

    **2026-11-11 起这条只用于确认"有空白的一对没被合并"**：`++` / `--` 已经
    是独立 token（`Kind.INC` / `Kind.DEC`），词法器只合成**紧挨着**的两个
    同类符号。所以 `i + +j` 的 token 流里仍是两个 `+`，`glued_pairs` 必须为空。
    """
    ts = [t for t in Lexer(src).run().tokens if t.kind != Kind.EOF]
    return [(a.text, b.text) for a, b in zip(ts, ts[1:])
            if a.text in ("+", "-") and a.text == b.text and a.end == b.start]


def incdec_tokens(src):
    ts = [t for t in Lexer(src).run().tokens if t.kind != Kind.EOF]
    return [(t.kind.name, t.text) for t in ts if t.kind in (Kind.INC, Kind.DEC)]


def test_incdec():
    print("[7] `++` / `--`：独立 token + 前/后缀排版 + 有空白的一对不合并")

    # ---- token 证据：`++` / `--` 是一个 token，不是两个 `+` / `-`
    for src, want in (
        ("i++;", ["i", "++", ";"]),
        ("++i;", ["++", "i", ";"]),
        ("i--;", ["i", "--", ";"]),
        ("--i;", ["--", "i", ";"]),
        ("i + +j;", ["i", "+", "+", "j", ";"]),      # 有空白 → 两个 PLUS
        ("i-- - 1;", ["i", "--", "-", "1", ";"]),
        ("a * b;", ["a", "*", "b", ";"]),
        ("i++ + 1;", ["i", "++", "+", "1", ";"]),
    ):
        got = [t.text for t in Lexer(src).run().tokens if t.kind != Kind.EOF]
        check(got == want, f"`{src}` 词法 → {want}", got)

    cases = [
        ("u64 t() { u64 i = 0; i++; return i; }",
         "i++;", "后缀 i++"),
        ("u64 t() { u64 i = 0; i--; return i; }",
         "i--;", "后缀 i--"),
        ("u64 t() { u64 i = 0; ++i; return i; }",
         "++i;", "前缀 ++i"),
        ("u64 t() { u64 i = 0; --i; return i; }",
         "--i;", "前缀 --i"),
        ("u64 t(bool p) { u64 i = 0; if (p) ++i; return i; }",
         "if (p) ++i;", "单语句体里的前缀 ++（不与条件的 `)` 粘住）"),
        ("u64 t(vector<u64>& xs) { xs[0]++; return xs[0]; }",
         "xs[0]++;", "下标后缀 ++"),
        ("u64 t(u64 n) { u64 s = 0; for (u64 j = 0; j < n; j++) { s += j; } return s; }",
         "j++)", "三段式 for 第三段的 j++"),
    ]
    for src, want, label in cases:
        out = format_source(src)
        check(want in out, label, out.strip().replace("\n", " | "))
        check(len(incdec_tokens(out)) == 1, label + "（产物里仍是 1 个 INC/DEC token）",
              str(incdec_tokens(out)))
        check(format_source(out) == out, label + "（幂等）")

    # 三段式 for 的第三段也可以是前缀 `++j`
    out = format_source(
        "u64 t(u64 n) { u64 s = 0; for (u64 j = 0; j < n; ++j) { s += j; } return s; }")
    check("++j)" in out, "for 第三段的前缀 ++j", out.strip().replace("\n", " | "))

    # `i + +j` / `i - -j`：**有空白**的一对是两个独立运算符，不许合成 `++` / `--`。
    # 词法层已经保证（不紧邻就不合并）；fmt 必须保持这个 token 流。
    for spaced, label in (("i + +j", "i + +j"), ("i - -j", "i - -j")):
        src = "u64 t(u64 i, u64 j) { return %s; }" % spaced
        out = format_source(src)
        check(spaced in out, f"`{spaced}` 保持两个独立运算符",
              out.strip().replace("\n", " | "))
        check(incdec_tokens(out) == [], f"`{spaced}` 的产物里没有 INC/DEC token",
              str(incdec_tokens(out)))
        check(glued_pairs(out) == [], f"`{spaced}` 的两个符号不许相邻",
              str(glued_pairs(out)))
        check(format_source(out) == out, f"`{spaced}` 幂等")

    # 语义钉子：修好的产物必须还能过 parser（以前 `i + +;` 解析错）
    from parser import parse_source
    probe = ("u64 t(u64 n) {\n"
             "    u64 i = 0;\n"
             "    i++;\n"
             "    ++i;\n"
             "    i--;\n"
             "    --i;\n"
             "    i += 1;\n"
             "    for (u64 j = 0; j < n; j++) {\n"
             "        i += j;\n"
             "    }\n"
             "    return i;\n"
             "}\n")
    out_p = format_source(probe)
    check(toks(probe) == toks(out_p), "自增自减探针：token 序列不变")
    check(parse_source(out_p, "incdec.eln").ok,
          "格式化产物仍是合法 Elench（过 parser）", out_p.replace("\n", " | "))


def main():
    test_idempotent()
    test_preserve()
    test_width()
    test_layout()
    test_edges()
    test_dispatch_indent()
    test_incdec()

    print()
    total = _passed + len(_failed)
    if _failed:
        print(f"失败 {len(_failed)} / {total}")
        for f in _failed:
            print("  - " + f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
