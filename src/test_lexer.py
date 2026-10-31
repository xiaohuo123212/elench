# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

"""dsh lexer 测试 —— 跑真实样例 + 边界用例

用法：python test_lexer.py
退出码 0 = 全过；1 = 有失败
"""

import sys
from pathlib import Path

# Windows 控制台默认 GBK，装不下 ⊖ / 中文诊断 —— 统一按 UTF-8 输出
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from lexer import Kind, lex

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "文档" / "设计" / "目前设计" / "样例"

fails = []
oks = 0


def check(name, cond, detail=""):
    global oks
    if cond:
        oks += 1
        print(f"  PASS  {name}")
    else:
        fails.append(name)
        print(f"  FAIL  {name}  {detail}")


def kinds(src):
    return [t.kind for t in lex(src).tokens]


# ---------------------------------------------------------------- 1. 真实样例
print("\n[1] 真实样例：calculator.tu / calculator.eln")

for fname in ("calculator.tu", "calculator.eln"):
    p = SAMPLE_DIR / fname
    if not p.exists():
        check(f"{fname} 存在", False, str(p))
        continue
    src = p.read_text(encoding="utf-8")
    r = lex(src)
    errs = [d for d in r.diagnostics if d.code.startswith("E-")]
    check(f"{fname} 词法零错误", not errs,
          "; ".join(f"{d.code}@{d.line}:{d.col} {d.message}" for d in errs[:3]))
    check(f"{fname} 产出 EOF", r.tokens[-1].kind == Kind.EOF)
    check(f"{fname} 有常量/函数（token 非空）", len(r.tokens) > 20, f"{len(r.tokens)} tokens")

# ---------------------------------------------------------------- 2. 关键字
print("\n[2] C3 关键字：缩写合格，长写法报错")

ks = kinds("func struct enum trait impl spec choose rewrite use pub const "
           "if else for while match return scope spawn unsafe asm extern error")
expected = [Kind.FN, Kind.STRUCT, Kind.ENUM, Kind.TRAIT, Kind.IMPL, Kind.SPEC,
            Kind.CHOOSE, Kind.REWRITE, Kind.USE, Kind.PUB, Kind.CONST,
            Kind.IF, Kind.ELSE, Kind.FOR, Kind.WHILE, Kind.MATCH,
            Kind.RETURN, Kind.SCOPE, Kind.SPAWN, Kind.UNSAFE, Kind.ASM, Kind.EXTERN,
            Kind.ERROR_DECL]
check("23 个行首关键字全部识别（v0.68：func 取代 fn，新增 error）",
      ks[:-1] == expected, f"得到 {len(ks)-1} 个")

# v0.68 下线的关键字：仍然识别，但要报专用诊断
for word, code in (("fn", "E-Func-KeywordRemoved"), ("val", "E-Decl-OldKeyword"),
                   ("var", "E-Decl-OldKeyword"), ("end", "E-End-Removed")):
    r = lex(word)
    check(f"`{word}` 报 {code}", any(d.code == code for d in r.diagnostics))

r = lex("function foo() {}")
check("长写法 function 报 E-Keyword-LongForm",
      any(d.code == "E-Keyword-LongForm" for d in r.diagnostics))
check("长写法报错信息给出应改成 func",
      any("`func`" in d.message for d in r.diagnostics))

# ---------------------------------------------------------------- 3. 字符字面量
print("\n[3] 字符字面量（L18.1 封闭转义集）")

cases = [
    ("'0'", 48), ("' '", 32), ("'A'", 65),
    ("'\\n'", 10), ("'\\t'", 9), ("'\\r'", 13), ("'\\0'", 0),
    ("'\\''", 39), ("'\\\\'", 92),
    ("'\\x41'", 65), ("'\\xff'", 255),
]
for src, want in cases:
    r = lex(src)
    tok = r.tokens[0]
    check(f"{src} == {want}", tok.kind == Kind.CHAR and tok.value == want,
          f"得到 kind={tok.kind} value={tok.value}")

bad = [
    ("''", "E-CharLit-Empty"),
    ("'ab'", "E-CharLit-Multi"),
    ("'中'", "E-CharLit-NonAscii"),
    ("'\\q'", "E-CharLit-BadEscape"),
    ("'\\x4'", "E-CharLit-BadEscape"),
    ("'a", "E-CharLit-Unterminated"),
]
for src, want in bad:
    r = lex(src)
    check(f"{src!r} 报 {want}", any(d.code == want for d in r.diagnostics),
          "; ".join(d.code for d in r.diagnostics))

r = lex("'中'")
check("非 ASCII 诊断给出 \\xe4 写法",
      any("\\xe4" in d.message for d in r.diagnostics),
      "; ".join(d.message for d in r.diagnostics))

# ---------------------------------------------------------------- 4. 数字
print("\n[4] 数字字面量")

check("42", lex("42").tokens[0].value == 42)
check("0xFF", lex("0xFF").tokens[0].value == 255)
check("0b1010", lex("0b1010").tokens[0].value == 10)
check("1_000_000", lex("1_000_000").tokens[0].value == 1000000)
check("1.5 是 float", lex("1.5").tokens[0].kind == Kind.FLOAT)
check("1e9 是 float", lex("1e9").tokens[0].kind == Kind.FLOAT)

# `0..n` 必须切成 INT 和 RANGE，不能吞成浮点
ks = kinds("0..n")
check("0..n 切成 0 / .. / n",
      ks[:3] == [Kind.INT, Kind.RANGE, Kind.IDENT], str(ks[:3]))

# ---------------------------------------------------------------- 5. 注释
print("\n[5] 注释进 trivia，不进 token 流")

r = lex("// line\n/// doc\nfunc")
check("两条注释进 trivia", len(r.trivia) == 2, f"{len(r.trivia)}")
check("注释不进 token 流", [t.kind for t in r.tokens] == [Kind.FN, Kind.EOF])

# ---------------------------------------------------------------- 6. 运算符
print("\n[6] 运算符：最长匹配优先")

ks = kinds("|> .. -> => <= >= == != += .. ? & ⊖")
check("|> 优先于 |", ks[0] == Kind.PIPE_GT)
check(".. 优先于 .", ks[1] == Kind.RANGE)
check("-> 整体识别", ks[2] == Kind.ARROW)
check("=> 整体识别", ks[3] == Kind.FATARROW)
check("⊖ 识别", Kind.OMINUS in ks)

# ---------------------------------------------------------------- 7. 没有字符串
print("\n[7] 字符串字面量：解码为字节（v0.71 起支持）")

r = lex('"hello"')
toks = [x for x in r.tokens if x.kind.value == "string"]
check("产出一个 string token", len(toks) == 1)
check("解码后的字节正确", len(toks) == 1 and bytes(toks[0].value) == b"hello")

# ---------------------------------------------------------------- 8. 位置信息
print("\n[8] span 精确")

r = lex("fn foo() {\n  val x = 1;\n}")
val_tok = next(t for t in r.tokens if t.kind == Kind.VAL)
check("val 在第 2 行第 3 列", (val_tok.line, val_tok.col) == (2, 3),
      f"得到 {val_tok.line}:{val_tok.col}")

# ---------------------------------------------------------------- 汇总
print(f"\n{'='*50}")
print(f"通过 {oks}，失败 {len(fails)}")
if fails:
    print("失败项：")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("全过")
