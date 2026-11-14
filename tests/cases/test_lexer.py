# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

"""dsh lexer 测试 —— 跑真实样例 + 边界用例

用法：python test_lexer.py
退出码 0 = 全过；1 = 有失败
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

import sys
from pathlib import Path

# Windows 控制台默认 GBK，装不下 ⊖ / 中文诊断 —— 统一按 UTF-8 输出
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from lexer import Kind, lex

SAMPLE_DIR = Path(ROOT) / "文档" / "样例"

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

# v0.68 下线的关键字（2026-11-10）：**词法器只出 IDENT，不再按词形报错**。
# 判据移到 parser 的**语法位置**（`end` 只在块终结符、`val`/`var`/`let` 只在
# 声明形状、`fn` 只在旧函数声明形状）——这几个词同时是合法标识符：
# `size_t end` / `long long val` / `xs.end()`。诊断的断言在 test_parser.py。
for word in ("fn", "val", "var", "let", "end"):
    r = lex(word)
    check(f"`{word}` 词法层是普通 IDENT",
          [t.kind for t in r.tokens[:-1]] == [Kind.IDENT],
          str([(t.kind.value, t.text) for t in r.tokens]))
    check(f"`{word}` 词法层不报 E-（诊断按语法位置由 parser 报）",
          not [d for d in r.diagnostics if d.code.startswith("E-")],
          "; ".join(d.code for d in r.diagnostics))

# **成员位置上的 `end` 不是退役关键字**（2026-11-10）：`xs.end()` 是 C++ 的
# 迭代器接口，不是"块尾写 end"的老写法。词法层现在**任何位置**都不报
# E-End-Removed（改由 parser 按位置判 / 报「本语言没有迭代器」）。
r = lex("xs.end()")
check("成员位置 `end` 不报 E-End-Removed",
      not any(d.code == "E-End-Removed" for d in r.diagnostics),
      "; ".join(d.code for d in r.diagnostics))
check("成员位置 `end` 是普通 IDENT（交给 parser 统一报错）",
      any(t.kind == Kind.IDENT and t.text == "end" for t in r.tokens),
      str([(t.kind.value, t.text) for t in r.tokens]))
r = lex("xs . // 注释\nend")
check("`end` 的判据与空格/注释无关（词法器不再按前一个 token 判）",
      not any(d.code == "E-End-Removed" for d in r.diagnostics))

# **`public` 不再是长写法错误**（2026-11-10）：它是 C++ 的成员访问说明符，
# `struct S { public: x: u64, }` 必须能写。位置判据在 parser：
# struct 体里的 `public:` 接受并忽略；顶层 `public func f()` 报 E-Keyword-LongForm。
r = lex("public: u64 x;")
check("`public` 词法层是普通 IDENT（不报 E-Keyword-LongForm）",
      not [d for d in r.diagnostics if d.code == "E-Keyword-LongForm"]
      and r.tokens[0].kind == Kind.IDENT
      and r.tokens[0].text == "public",
      str([(t.kind.value, t.text) for t in r.tokens]))
r = lex("private: protected:")
check("`private` / `protected` 词法层也是普通 IDENT",
      [t.kind for t in r.tokens[:-1]] == [Kind.IDENT, Kind.COLON, Kind.IDENT, Kind.COLON],
      str([(t.kind.value, t.text) for t in r.tokens]))

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

# **进制前缀的边界（2026-11-09）**：词法一直认得 0x/0b，但值没被 parser 用上
# （存的是源码文本，解释器按十进制重解析 → `0x10` run 崩）。值的断言在这里；
# "AST 存数值"在 test_parser，"run 得对"在 test_interp_surface。
check("0X10 大写前缀", lex("0X10").tokens[0].value == 16)
check("0xFFFF_FFFF_FFFF_FFFF（u64 最大）",
      lex("0xFFFF_FFFF_FFFF_FFFF").tokens[0].value == 18446744073709551615)
check("0b1010_1010（下划线分隔）", lex("0b1010_1010").tokens[0].value == 170)
check("u64 最大值不报错",
      not lex("18446744073709551615").diagnostics,
      str([d.code for d in lex("18446744073709551615").diagnostics]))
check("0x 空 → E-Lex-BadNumber",
      any(d.code == "E-Lex-BadNumber" for d in lex("0x").diagnostics))
check("0xG → E-Lex-BadNumber",
      any(d.code == "E-Lex-BadNumber" for d in lex("0xG").diagnostics))
check("0x 溢出 u64 → E-Lex-IntOverflow",
      any(d.code == "E-Lex-IntOverflow"
          for d in lex("0x1_0000_0000_0000_0000").diagnostics))
check("十进制溢出 u64 → E-Lex-IntOverflow（同一条规则）",
      any(d.code == "E-Lex-IntOverflow"
          for d in lex("18446744073709551616").diagnostics))

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

# 位运算符（2026-11-09）： `& | ^ ~ <<` 是 token；`>>` **不是**——
# 它必须保持两个 `>`，否则 `Vec<Vec<u8>>` 的嵌套泛型收尾会被撕开。
ks = kinds("& | ^ ~ <<")
check("位运算符 & | ^ ~ << 全部识别",
      ks[:5] == [Kind.AMP, Kind.PIPE, Kind.CARET, Kind.TILDE, Kind.SHL], str(ks[:5]))
ks = kinds("a >> b")
check("`>>` 是两个 GT（保护嵌套泛型）",
      ks[:3] == [Kind.IDENT, Kind.GT, Kind.GT], str(ks[:3]))
check("`&&` 不静默拆成 & &", kinds("a && b")[1] == Kind.AMPAMP)
check("`||` 不静默拆成 | |", kinds("a || b")[1] == Kind.PIPEPIPE)

# `>>` 的两个 `>` 相邻才构成右移；词法不合并，所以位置仍精确
r = lex("a >> b")
gts = [t for t in r.tokens if t.kind == Kind.GT]
check("`a >> b` 的两个 GT 位置相邻",
      len(gts) == 2 and gts[0].end == gts[1].start, str(gts))

# ---------------------------------------------------------------- 7. 没有字符串
print("\n[7] 字符串字面量：解码为字节（v0.71 起支持）")

r = lex('"hello"')
toks = [x for x in r.tokens if x.kind.value == "string"]
check("产出一个 string token", len(toks) == 1)
check("解码后的字节正确", len(toks) == 1 and bytes(toks[0].value) == b"hello")

# **C++ 原始字符串 `R"( … )"`**（2026-11-10）：原始字符串里**没有转义**。
# 真实 C++ 文件的 `std::regex` 常量几乎都这么写：`R"([\s\S]*?)"`。
# 以前它走普通字符串路径 → `\s` / `\S` 各报一条 E-Lex-BadEscape（C++ 里合法）。
_r = lex(r'R"(a\sb)"')
check("`R\"( … )\"` 是 STRING 且零诊断",
      [t.kind for t in _r.tokens[:-1]] == [Kind.STRING]
      and not [d for d in _r.diagnostics if d.code.startswith("E-")],
      str([d.code for d in _r.diagnostics]))
check("原始字符串里的 `\\s` 是**两个字面字符**（不解码）",
      bytes(_r.tokens[0].value) == b"a\\sb", repr(_r.tokens[0].value))
_r = lex(r'R"tag(a\s"b)tag"')
check("带定界符的原始字符串：正文里的 `\"` 不结束字面量",
      bytes(_r.tokens[0].value) == b'a\\s"b' and not _r.diagnostics,
      repr(_r.tokens[0].value))
# **普通字符串不放宽**：`"\s"` 仍然报错（C++ 里也非法），且消息列出支持集合。
_r = lex(r'"\s"')
_bad = [d for d in _r.diagnostics if d.code == "E-Lex-BadEscape"]
check("普通字符串 `\\s` **仍然**报 E-Lex-BadEscape（不放宽）", len(_bad) == 1,
      str([d.code for d in _r.diagnostics]))
check("E-Lex-BadEscape 的消息里写明**支持的转义集合**",
      bool(_bad) and all(s in _bad[0].message for s in ("\\n", "\\t", "\\\\", "\\xNN")),
      _bad[0].message if _bad else "")
_r = lex(r'R"(没写结束标记')
check("未闭合的原始字符串报 E-Lex-UnterminatedString",
      any(d.code == "E-Lex-UnterminatedString" for d in _r.diagnostics),
      str([d.code for d in _r.diagnostics]))

# ---------------------------------------------------------------- 8. 位置信息
print("\n[8] span 精确")

r = lex("fn foo() {\n  val x = 1;\n}")
# 2026-11-10：`val` 是普通 IDENT（退役关键字的诊断按语法位置由 parser 报）
val_tok = next(t for t in r.tokens if t.kind == Kind.IDENT and t.text == "val")
check("val 在第 2 行第 3 列", (val_tok.line, val_tok.col) == (2, 3),
      f"得到 {val_tok.line}:{val_tok.col}")

# ---------------------------------------------------------------- 9. `**`
print("\n[9] `**`：**一个** token（不是两个 `*`）")

# 2026-11-10 之前：`2 ** 10` 是 `INT STAR STAR INT`，语法层读成
# `2 * (*10)`——前缀 `*` 是解引用、运行期恒等，于是 `2 ** 10` = 20。
# 因为「能 check、能 run、给错值」，词法这一层必须单独钉住：`**` 是**一个**
# token，且**不影响** `a * b`。
ks = kinds("2 ** 10")
check("`**` 是一个 STAR_STAR", ks[:3] == [Kind.INT, Kind.STAR_STAR, Kind.INT], str(ks[:4]))
check("`**` 不再被拆成 STAR STAR", ks.count(Kind.STAR) == 0, str(ks[:4]))

check("`a * b` 仍是单个 STAR（乘法不受影响）",
      kinds("a * b")[:3] == [Kind.IDENT, Kind.STAR, Kind.IDENT], str(kinds("a * b")[:4]))
check("`a * (b)` 仍是单个 STAR",
      kinds("a * (b)")[:3] == [Kind.IDENT, Kind.STAR, Kind.LPAREN],
      str(kinds("a * (b)")[:4]))

# `**=` 没有这个运算符：词法给 `STAR_STAR` + `EQ`（不会静默变成 `*=`，
# 也不会被当成 `*` 的复合赋值）。语法层随后会报"期望表达式"。
check("`a **= b` → STAR_STAR + EQ（不会静默变成 `*=`）",
      kinds("a **= b")[:3] == [Kind.IDENT, Kind.STAR_STAR, Kind.EQ],
      str(kinds("a **= b")[:4]))

# 注释里的 `**` 只是文本（这才是仓库里 `**` 出现最多的地方：Markdown 粗体）
check("注释里的 `**` 不产生 STAR_STAR",
      Kind.STAR_STAR not in kinds("// 强调**这句**不算运算符\nfunc"),
      str(kinds("// 强调**这句**不算运算符\nfunc")))

# ---------------------------------------------------------------- 10. 预处理（§8.10）
print("\n[10] 预处理：先展开再分析（`#include` 丢弃 / `#define` 展开 / 条件编译）")

from preproc import preprocess as _pp_preprocess, set_defines as _pp_set_defines
_pp_set_defines({})          # 测试不继承命令行 `-D`（默认空）

# 产品目标是「一个 C++ 程序只改文件名就能当 `.eln` 用」，所以真实 C++ 文件
# 的第一屏必须吃得下：**标准头**（`<vector>` 之类）不报错（我们已有内建类型与门面头）。
# 非标准头（`#include "my.h"` / 第三方库）**必须报** `E-Lex-IncludeUnresolved`——
# 以前连它们也静默丢，那正是"看起来通过"（见 tests/cases/test_include_diag.py）。
_r = lex("#include <vector>\n#include <cstdint>\n#include <cstddef>\n")
check("`#include`（标准头）不报诊断",
      [d.code for d in _r.diagnostics] == [], str([d.code for d in _r.diagnostics]))
check("`#include` 不产生 token（整行吃掉）",
      [t.kind for t in _r.tokens if t.kind != Kind.EOF] == [])

_r = lex("#pragma once\n")
check("`#pragma` 不报诊断",
      [d.code for d in _r.diagnostics] == [], str([d.code for d in _r.diagnostics]))

# **对象式宏**：`#define N 10` 之后 `N` 就是 `10`（先展开再分析）。
_r = lex("#define N 10\nu64 x = N;\n")
check("`#define N 10` 不报诊断（宏已支持）",
      [d.code for d in _r.diagnostics] == [], str([d.code for d in _r.diagnostics]))
check("`N` 展开成整数字面量 10",
      [t.text for t in _r.tokens if t.kind == Kind.INT] == ["10"],
      str([(t.kind.value, t.text) for t in _r.tokens]))
# **行号映射**：展开后的 token 行号 = **使用处**在原文件里的行号（第 2 行），
# 不是宏定义处（第 1 行）——诊断必须指对地方。
check("展开后的 token 行号指回原文件（使用处第 2 行）",
      [t.line for t in _r.tokens if t.kind == Kind.INT] == [2],
      str([(t.text, t.line) for t in _r.tokens]))

# **函数式宏**：参数按记号替换。`ADD(2, 3)` → `((2) + (3))`。
_r = lex("#define ADD(a, b) ((a) + (b))\nu64 x = ADD(2, 3);\n")
_texts = [t.text for t in _r.tokens if t.kind != Kind.EOF]
check("函数式宏参数按记号替换（`ADD(2, 3)` → `((2) + (3))`）",
      _texts == ["u64", "x", "=", "(", "(", "2", ")", "+", "(", "3", ")", ")", ";"],
      str(_texts))
check("函数式宏不报诊断",
      [d.code for d in _r.diagnostics] == [], str([d.code for d in _r.diagnostics]))

# `#`（字符串化）/ `##`（记号粘贴）**明确报错**，不静默降级。
_r = lex("#define STR(x) #x\n")
check("函数式宏里的 `#` 报 E-Lex-MacroUnsupported",
      [d.code for d in _r.diagnostics] == ["E-Lex-MacroUnsupported"],
      str([d.code for d in _r.diagnostics]))
_r = lex("#define CAT(a, b) a ## b\n")
check("函数式宏里的 `##` 报 E-Lex-MacroUnsupported",
      [d.code for d in _r.diagnostics] == ["E-Lex-MacroUnsupported"],
      str([d.code for d in _r.diagnostics]))

# **条件编译**：没有 `-D` 时未定义的宏为假 → 走 `#else`（真实 C++ 行为）。
_cond_src = "#ifdef X\nu64 a = 1;\n#else\nu64 b = 2;\n#endif\n"
_r = lex(_cond_src)
check("无 -D：`#ifdef X` 为假，走 `#else` 分支",
      [t.text for t in _r.tokens if t.kind != Kind.EOF] == ["u64", "b", "=", "2", ";"],
      str([(t.kind.value, t.text) for t in _r.tokens]))
check("无 -D：条件编译不报诊断",
      [d.code for d in _r.diagnostics] == [], str([d.code for d in _r.diagnostics]))
_r = lex(_cond_src, defines={"X": "1"})
check("有 -D X：走 `#if` 分支",
      [t.text for t in _r.tokens if t.kind != Kind.EOF] == ["u64", "a", "=", "1", ";"],
      str([(t.kind.value, t.text) for t in _r.tokens]))
# 死分支里的语法**不参与分析**（这正是「先预处理再分析」的意义）
_r = lex("#ifdef X\n@@@ 这是死分支 ###\n#endif\nu64 x = 1;\n", defines={})
check("死分支里的非法字符不再报错（整块丢弃）",
      [d.code for d in _r.diagnostics] == [], str([d.code for d in _r.diagnostics]))

# `#error`：**报错并带原文**。
_r = lex("#error 别编这段\n")
check("`#error` 报 E-Lex-PreprocessorError",
      [d.code for d in _r.diagnostics] == ["E-Lex-PreprocessorError"],
      str([d.code for d in _r.diagnostics]))
check("`#error` 的文案带原文",
      "别编这段" in "".join(d.message for d in _r.diagnostics),
      "".join(d.message for d in _r.diagnostics))

# **不支持的指令必须明确报错**（不许当注释静默删掉）。
_r = lex("#line 42\n")
check("`#line` 报 E-Lex-Preprocessor（不静默吞）",
      [d.code for d in _r.diagnostics] == ["E-Lex-Preprocessor"],
      str([d.code for d in _r.diagnostics]))

# **环检测 / 层数上限**：宏展开必须有终止条件。
_r = lex("#define A A\nu64 x = A;\n")
check("自引用宏报 E-Lex-MacroRecursion",
      [d.code for d in _r.diagnostics] == ["E-Lex-MacroRecursion"],
      str([d.code for d in _r.diagnostics]))
_r = lex("#define A B\n#define B A\nu64 x = A;\n")
check("互相引用（A→B→A）报 E-Lex-MacroRecursion",
      [d.code for d in _r.diagnostics] == ["E-Lex-MacroRecursion"],
      str([d.code for d in _r.diagnostics]))

# 条件块没关：明确报错，不静默。
_r = lex("#ifdef X\nu64 a = 1;\n")
check("`#ifdef` 没有 `#endif` 报 E-Lex-Preprocessor",
      "E-Lex-Preprocessor" in [d.code for d in _r.diagnostics],
      str([d.code for d in _r.diagnostics]))

# **来源映射**：宏展开要记下"由宏 `M`（定义在 3:9）展开"，供 dsh 补进诊断。
_pr = _pp_preprocess("#define BAD x = ;\nu64 r = BAD;\n", "p.eln", {})
check("宏展开来源记录定义位置（第 1 行、宏名所在列）",
      bool(_pr.notes.get(2)) and "定义在 1:9" in _pr.notes[2][0],
      str(_pr.notes))

# `int` / `float` / `char` / `string` 这四个词**不是**关键字：
# 它们以前是「字面量 token 种类」被误收进 KEYWORDS 表，于是 `int main()` 的
# `int` 是 `Kind.INT`（"整数字面量"），`char c;` 的 `char` 是 `Kind.CHAR`。
for word in ("int", "float", "char", "string"):
    check(f"`{word}` 是 IDENT（不是字面量 token）",
          kinds(word)[0] == Kind.IDENT, str(kinds(word)))
# 字面量仍然走各自的 token 种类（不受上面影响）
check("`1` 仍是 INT", kinds("1")[0] == Kind.INT)
check("`1.5` 仍是 FLOAT", kinds("1.5")[0] == Kind.FLOAT)
check("`'a'` 仍是 CHAR", kinds("'a'")[0] == Kind.CHAR)

# ---------------------------------------------------------------- 汇总
print(f"\n{'='*50}")
print(f"通过 {oks}，失败 {len(fails)}")
if fails:
    print("失败项：")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("全过")
