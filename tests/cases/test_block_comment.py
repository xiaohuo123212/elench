# -*- coding: utf-8 -*-
"""块注释 `/* … */`（2026-11-13）—— 真实 C 库里 3/3 都撞到的第一个语法缺口。

## 为什么它值得一条专门的测试

从 GitHub 拉来的三个真实 C 库（cJSON / inih / tinyexpr）里，**三个都用块注释**。
在支持之前，`/` `*` 被当成两个运算符，注释正文整段变成表达式片段，于是：

| 库 | 支持前的诊断数 |
|---|---|
| cJSON | 277（其中 220 条就是 `E-Lex-BlockComment`） |
| inih | 26（23 条） |
| tinyexpr | 62（23 条） |

把块注释机械改写成 `//` 之后，三个库的诊断数分别掉到 57 / 3 / 39。
**一处词法改动砍掉 37–88% 的诊断**，所以它必须是"跳过"，不能再是"报错"。

## 这条测试钉住什么

| 要求 | 判据 |
|---|---|
| 单行 / 跨行 / 行内 | 三种都能跳过，零诊断 |
| 与 `//` 混用 | `//` 里的 `/*` 是文本；块注释里的 `//` 也是文本 |
| **不嵌套** | `/* /* */` 在**第一个** `*/` 结束（与 C 一致） |
| 未闭合 | `E-Lex-BlockCommentUnterminated`，位置指**开注释处**（不静默吞到文件尾） |
| **行号必须准** | 第 1–10 行是块注释、第 11 行有错 ⇒ 诊断报 **11**（这是硬要求） |
| 注释正文不影响任何东西 | token 流里没有注释；正文里的 `{` `"` `'` `if` 不产生诊断 |
| 注释进 trivia | `value="block"`（格式化 / 文档工具要用），与 `//` 同一套结构 |
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from lexer import Kind, lex                      # noqa: E402
from parser import parse_source                  # noqa: E402

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


def codes_of(src, name="t.eln"):
    r = parse_source(src, name)
    return [d.code for d in r.diagnostics], r


def non_eof(r):
    return [t for t in r.tokens if t.kind != Kind.EOF]


# ---------------------------------------------------------------- ① 五种写法

def test_spellings():
    print("[1] 单行 / 跨行 / 行内 / 与 `//` 混用 / 未闭合")

    # 单行
    codes, r = codes_of("u64 f() {\n    /* 一行注释 */\n    return 1;\n}\n")
    check(not codes, "单行 `/* … */` 零诊断", codes)

    # 跨行（注释里有看起来像代码的东西）
    src = ("u64 f() {\n"
           "    /* 第一行\n"
           "       if (k == \"call\") { return 1; }\n"
           "       括号与引号：{ ( [ \" '\n"
           "       第三行 */\n"
           "    return 1;\n"
           "}\n")
    codes, r = codes_of(src)
    check(not codes, "跨行块注释零诊断（正文里的代码 / 括号 / 引号不影响词法）", codes)

    # 行内（两侧都有代码）
    codes, r = codes_of("u64 f() { /* 前 */ return /* 后 */ 2; }\n")
    check(not codes, "行内 `/* … */`（前后都有代码）零诊断", codes)
    check([t.text for t in non_eof(lex("u64 f() { /* 前 */ return /* 后 */ 2; }\n"))]
          == ["u64", "f", "(", ")", "{", "return", "2", ";", "}"],
          "行内块注释不产出 token（`return` `2` 仍在正确位置）",
          [t.text for t in non_eof(lex("u64 f() { /* 前 */ return /* 后 */ 2; }\n"))])

    # 与 `//` 混用
    codes, r = codes_of("u64 f() {\n"
                        "    // 行注释里有 /* 三个字符\n"
                        "    /* 块注释里有 // 两个斜杠 */\n"
                        "    return 1;   // 行尾注释 /* 也不开始块注释\n"
                        "}\n")
    check(not codes, "`//` 与 `/* */` 混用：各自的标记互不干扰", codes)

    # 未闭合
    codes, r = codes_of("u64 f() {\n    /* 没有闭合\n    return 1;\n}\n")
    check("E-Lex-BlockCommentUnterminated" in codes,
          "未闭合的 `/*` 报 E-Lex-BlockCommentUnterminated", codes)
    diag = next((d for d in r.diagnostics
                 if d.code == "E-Lex-BlockCommentUnterminated"), None)
    check(diag is not None and (diag.line, diag.col) == (2, 5),
          "未闭合诊断的位置是**开注释处**（不是文件末尾）",
          None if diag is None else (diag.line, diag.col))
    check("没有闭合" in (diag.message if diag else ""),
          "未闭合诊断的消息说清「没有闭合」+ 改法", (diag.message[:120] if diag else ""))

    # 未闭合时也**不静默吞掉**：EOF 之前最后一个 token 之后不再有 token
    r = lex("u64 x; /* 没有闭合\n更多\n更多\n")
    check([t.text for t in non_eof(r)] == ["u64", "x", ";"],
          "未闭合块注释吞掉的是**注释正文**（不产出 ERROR token 级联）",
          [t.text for t in non_eof(r)])


# ---------------------------------------------------------------- ② 行号

def test_line_numbers():
    print("[2] 行号准确性（硬要求）：1–10 行是块注释，第 11 行有错 ⇒ 报第 11 行")
    src = ("/* 块注释第 1 行\n"
           "   第 2 行\n"
           "   第 3 行\n"
           "   第 4 行\n"
           "   第 5 行\n"
           "   第 6 行\n"
           "   第 7 行\n"
           "   第 8 行\n"
           "   第 9 行\n"
           "*/\n"
           "u64 f() { u64 y = ; }\n")
    lines = src.split("\n")
    check(len(lines) >= 11 and lines[0].startswith("/*") and lines[9] == "*/"
          and lines[10].startswith("u64 f()"),
          "夹具正确：注释占 1–10 行，第 11 行是错误行", lines[:11])

    r = parse_source(src, "line.eln")
    first = next((d for d in r.diagnostics if d.code.startswith("E-")), None)
    check(first is not None and first.line == 11,
          "**诊断报第 11 行**（不是 1，也不是 12）",
          None if first is None else (first.code, first.line, first.col))

    # 第一个 token 的行号也必须是 11（token 与诊断同一套坐标）
    rl = lex(src)
    first_tok = non_eof(rl)[0]
    check((first_tok.text, first_tok.line, first_tok.col) == ("u64", 11, 1),
          "注释之后第一个 token 在 11:1", (first_tok.text, first_tok.line,
                                         first_tok.col))

    # 对照：同一错误、把 10 行注释换成 10 行 `//`，行号必须完全一样
    src_line_comments = "".join(f"// 第 {i} 行\n" for i in range(1, 11)) + \
        "u64 f() { u64 y = ; }\n"
    r2 = parse_source(src_line_comments, "line2.eln")
    first2 = next((d for d in r2.diagnostics if d.code.startswith("E-")), None)
    check(first2 is not None and first.line == first2.line,
          "块注释与 `//` 行注释占同样行数时，诊断**同一行**",
          (first.line if first else None, first2.line if first2 else None))

    # 注释**只跨几行**也照样准：单个 token 的行号
    rl2 = lex("u64 a;\n/* x\ny\nz */\nu64 b;\n")
    toks = {t.text: (t.line, t.col) for t in non_eof(rl2) if t.kind == Kind.IDENT}
    check(toks.get("b") == (5, 5), "跨 3 行的块注释之后，`b` 在 5:5", toks)


# ---------------------------------------------------------------- ③ 不嵌套

def test_non_nesting():
    print("[3] `/* /* */` **不嵌套**（与 C 一致）：第一个 `*/` 就结束")
    r = lex("/* 外层 /* 内层 */ u64 x;")
    check([t.text for t in non_eof(r)] == ["u64", "x", ";"],
          "`/* /* */` 在第一个 `*/` 结束，后面的 `u64 x;` 是代码",
          [t.text for t in non_eof(r)])
    check(not r.diagnostics, "嵌套样子的块注释零诊断", [d.code for d in r.diagnostics])

    r = lex("/* /* */ */ u64 x;")
    check([t.kind for t in non_eof(r)[:2]] == [Kind.STAR, Kind.SLASH],
          "`/* /* */ */`：结束符之后剩下的 `*` `/` 是**普通运算符**",
          [t.kind.value for t in non_eof(r)[:3]])

    r = lex("/*/")
    check([d.code for d in r.diagnostics] == ["E-Lex-BlockCommentUnterminated"],
          "`/*/` 是**未闭合**（不是空注释）", [d.code for d in r.diagnostics])


# ---------------------------------------------------------------- ④ trivia

def test_trivia():
    print("[4] 注释进 trivia、不进 token 流（与 `//` 同一套结构）")
    r = lex("u64 x;\n/* 跨\n行 */\n// 行\nu64 y;\n")
    check([t.kind for t in r.tokens if t.kind != Kind.EOF]
          == [Kind.IDENT, Kind.IDENT, Kind.SEMI, Kind.IDENT, Kind.IDENT, Kind.SEMI],
          "token 流只有 `u64 x ; u64 y ;`（块注释不产出 token）",
          [t.kind.value for t in r.tokens])
    blocks = [t for t in r.trivia if t.value == "block"]
    lines = [t for t in r.trivia if t.value == "line"]
    check(len(blocks) == 1 and len(lines) == 1
          and "跨\n行" in blocks[0].text,
          "块注释进 trivia（`value=\"block\"`，保留换行），`//` 仍是 `\"line\"`",
          [(t.value, t.text[:20]) for t in r.trivia])


# ---------------------------------------------------------------- ⑤ 不吃字符串里的 `/`

def test_not_eating_strings():
    print("[5] 字符串 / 字符字面量里的 `/*` `//` 仍是字面量内容")
    # 本语言目前把字符串当错误（没有字符串字面量），但字符字面量是合法的
    r = lex("u8 a = '/'; u8 b = '*';")
    check([t.kind for t in non_eof(r)] == [Kind.IDENT, Kind.IDENT, Kind.EQ,
                                           Kind.CHAR, Kind.SEMI, Kind.IDENT,
                                           Kind.IDENT, Kind.EQ, Kind.CHAR,
                                           Kind.SEMI],
          "字符字面量 `'/'` `'*'` 不被当成块注释开头",
          [t.kind.value for t in non_eof(r)])
    check(not r.diagnostics, "字符字面量零诊断", [d.code for d in r.diagnostics])


def main() -> int:
    test_spellings()
    test_line_numbers()
    test_non_nesting()
    test_trivia()
    test_not_eating_strings()
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
