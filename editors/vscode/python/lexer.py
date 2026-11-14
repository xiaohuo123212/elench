# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

"""dsh lexer —— M0 第一步（Python 骨架）

覆盖规格 v0.38 的词法层：
- C3 的 23 个行首关键字（缩写形式；长写法是错误）
- 上下文子句关键字（lex 成 IDENT，由 parser 认）
- 字符字面量（L18.1：封闭转义集）
- 数字字面量（10 / 0x / 0b / 下划线分隔 / 浮点；整数字面量必须装进 u64）
- 注释（// 行注释、/// 文档注释、/* 块注释 */）→ 作为 trivia 保留，不进 token 流
- 运算符与标点

设计取舍（M0 阶段）：
- **宽松超集**：非法输入不抛异常，产出 ERROR token 并继续，让 parser 能一次报多个错
- 每个 token 带 (line, col, start, end)，供 span 精确的 JSON 诊断
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from preproc import preprocess


#: `u64` 的上界。**整数字面量必须装得下**：本语言没有比 `u64` 更宽的整数
#: 类型，所以 `0x1_0000_0000_0000_0000` 这类值没有合法的运行时表示。
#: 这里按**错误**处理（不静默按 2^64 回绕）——十六进制是"写位模式"的记法，
#: 多一位就是打错了，静默变成 0 是移植 SHA-256/CRC 表时最难查的那类 bug。
U64_MAX = (1 << 64) - 1


# ---------------------------------------------------------------- token 种类

class Kind(str, Enum):
    # 行首关键字（C3；v0.68 起 `fn` 改为 `func`）
    FN = "func"
    STRUCT = "struct"
    ENUM = "enum"
    TRAIT = "trait"
    IMPL = "impl"
    SPEC = "spec"
    CHOOSE = "choose"
    REWRITE = "rewrite"
    USE = "use"
    PUB = "pub"
    CONST = "const"
    VAL = "val"          # v0.68 起废弃（应写 `const Type name`），仅用于给出精确诊断
    VAR = "var"          # v0.68 起废弃（应写 `Type name`），仅用于给出精确诊断
    IF = "if"
    ELSE = "else"
    FOR = "for"
    WHILE = "while"
    PARALLEL = "parallel"   # v0.71：并发构造（规格 §13.6，B-23…B-27）
    MATCH = "match"
    RETURN = "return"
    SCOPE = "scope"
    SPAWN = "spawn"
    UNSAFE = "unsafe"
    ASM = "asm"
    EXTERN = "extern"
    EXTERNAL = "external"
    ASSUME = "assume"        # **已移除的语法**（2026-11-04）。保留词法只为给出迁移报错
    KERNELS = "kernels"      # 上下文关键字：出现在 func 体之后
    ERROR_DECL = "error"     # 模块级错误类型声明：`error CalcError;`
    BREAK = "break"          # v0.72：循环提前退出
    CONTINUE = "continue"    # v0.72：跳过本轮

    # 非行首但仍是关键字
    END = "end"          # v0.68 起废弃（块只用 `{}`），仅用于给出精确诊断
    IN = "in"
    # **词形逻辑运算符已退役（2026-11-11）**：`and` / `or` / `not` 不再产出这三个
    # token（遇到时词法器报 `E-Logic-WordOp` 并教符号写法）。枚举值保留**只为
    # 兼容**：`highlight.py`（对上色的词表）与编辑器镜像仍按名字引用它们；
    # 删掉枚举会让这些模块 import 时就 AttributeError。
    AND = "and"
    OR = "or"
    NOT = "not"
    AS = "as"
    TRUE = "true"
    FALSE = "false"
    SELF = "self"
    DYN = "dyn"

    IDENT = "ident"
    INT = "int"
    FLOAT = "float"
    CHAR = "char"
    STRING = "string"      # 词法识别但 M0 不解析（规格里没有字符串字面量）

    # 标点 / 运算符
    LPAREN = "("
    RPAREN = ")"
    LBRACE = "{"
    RBRACE = "}"
    LBRACKET = "["
    RBRACKET = "]"
    COMMA = ","
    SEMI = ";"
    COLON = ":"
    DOT = "."
    PATHSEP = "::"      # 只用于静态成员调用 `FixedVec<T, N>::make()`，不作为枚举分隔
    ARROW = "->"
    FATARROW = "=>"
    QUESTION = "?"
    QUESTION_BANG = "?!"
    AMP = "&"
    AMPAMP = "&&"      # 逻辑与（唯一的写法；词形 `and` 已退役）
    PIPE = "|"
    PIPEPIPE = "||"    # 逻辑或（唯一的写法；词形 `or` 已退役）
    PIPE_GT = "|>"
    CARET = "^"
    TILDE = "~"
    SHL = "<<"
    PLUS = "+"
    MINUS = "-"
    #: **自增 / 自减是独立的词法 token**（2026-11-11）。
    #:
    #: 以前 `i++` 是 `i` `+` `+` 三个 token，parser 靠「两个 `+` 的源码位置
    #: 相邻」猜它是自增——**AST 里没有"相邻"这种信息**，转译到 C++ 时输出
    #: 不确定，formatter 也只能再把相邻的一对贴回去。现在词法器按最长匹配
    #: 把**紧挨着**的两个同类符号合成一个 token；中间有空白（`i + +j`）不合成。
    INC = "++"
    DEC = "--"
    STAR = "*"
    #: **幂运算符**（2026-11-10）。以前没有这个 token，`a ** b` 被词法拆成
    #: 两个 `STAR`、再被语法读成「`a * (*b)`」——`*` 的前缀形式是解引用，
    #: 运行期恒等，于是 `2 ** 10` 得到 `20`。**能 check、能 run、给错值**。
    STAR_STAR = "**"
    SLASH = "/"
    PERCENT = "%"
    LT = "<"
    GT = ">"
    LE = "<="
    GE = ">="
    EQ = "="
    EQEQ = "=="
    NE = "!="
    PLUS_EQ = "+="
    MINUS_EQ = "-="
    STAR_EQ = "*="
    SLASH_EQ = "/="
    BANG = "!"
    RANGE = ".."
    OMINUS = "⊖"

    ERROR = "error-token"   # 词法错误占位 token（不是关键字）
    EOF = "eof"


KEYWORDS = {k.value: k for k in Kind
            if k.name.isupper() and k.value.isalpha()
            # `Kind.INT` / `FLOAT` / `CHAR` / `STRING` 是**字面量**的 token 种类，
            # 不是关键字——它们的 `value` 恰好是 `int` / `float` / `char` /
            # `string` 这四个**词**。以前它们被当关键字收进表里，于是
            # `int main()` 的 `int` 是 `Kind.INT`（"整数字面量"），
            # `char c;` 的 `char` 是 `Kind.CHAR`……R1-A 要让 C++ 第一屏能吃，
            # 这四个词必须先变成普通标识符（字面量走各自的构造路径，不查这张表）。
            and k not in (Kind.VAL, Kind.VAR, Kind.END, Kind.ERROR,
                          Kind.INT, Kind.FLOAT, Kind.CHAR, Kind.STRING,
                          # 词形逻辑运算符已退役：不进关键字表，交给
                          # `WORD_OPERATORS` 报 E-Logic-WordOp（见 `_ident`）。
                          Kind.AND, Kind.OR, Kind.NOT)}
KEYWORDS[Kind.ERROR_DECL.value] = Kind.ERROR_DECL

# v0.68 下线的关键字：**只是词表，不再在词法层报错**（2026-11-10）。
#
# 为什么从"词形判据"改成"位置判据"：这批词同时是**合法标识符**——
# `auto it = xs.end();` 的 `end` 是成员名，`size_t end` / `long long val`
# 是参数名 / 变量名。按词形拦会把真实的 C++ 代码误伤成"退役关键字"，
# 而读者拿到的诊断和病因毫无关系。
#
# 现在词法器对这些词**只产出 IDENT**，诊断由 parser 在**语法位置**给：
#   · `end` 只在**块终结符位置**（紧跟块收尾 `}`，或后跟种类名 `fn`/`if`…）
#     → `E-End-Removed`；
#   · `val` / `var` / `let` 只在**声明形状**（语句起始 + 后面跟标识符）
#     → `E-Decl-OldKeyword`；
#   · `fn` 同理（旧函数声明形状）→ `E-Func-KeywordRemoved`。
# 见 `parser.Parser._at_end_terminator` / `parse_let` / `parse_stmt`。
#
# 这张表仍然保留：编辑器语法文件（`editors/vscode/syntaxes/eln.tmLanguage.json`）
# 的高亮集合与 `tests/cases/test_editors.py` 的"已下线词 ⊆ 本表"都靠它。
REMOVED_KEYWORDS = {
    "fn": "E-Func-KeywordRemoved",
    "val": "E-Decl-OldKeyword",
    "var": "E-Decl-OldKeyword",
    # `let` 从来不是 Elench 的关键字（它借自 Rust/JS）。以前它退化成
    # 普通标识符，于是 `let n = 7;` 被当成「类型名 = let」的声明，
    # 报 `E-Type-Assign 声明为 let` + 级联 `E-Type-Return 实际 let`——
    # 两条都指不到病因。现在和 val/var 一样给 E-Decl-OldKeyword。
    "let": "E-Decl-OldKeyword",
    "end": "E-End-Removed",
    # 词形逻辑运算符（2026-11-11 退役）：诊断在 `_ident` 里由
    # `WORD_OPERATORS` 报（同时教符号写法）；收进这张表是为了**编辑器词表**
    # （tmLanguage 的 removed 规则）和 `test_editors.py` 的集合比对一致。
    "or": "E-Logic-WordOp",
    "and": "E-Logic-WordOp",
    "not": "E-Logic-WordOp",
}

# 长写法：M0 只用于给出**更好的错误信息**（C3：长写法是错误，不是兼容形式）
#
# **词形逻辑运算符已退役（2026-11-11）**：`or` / `and` / `not` 不再是关键字。
# 用户定的方向是「同一个概念只给一种写法」——符号 `||` / `&&` / `!` 是唯一写法。
# 这里保留**词形 → (符号, token)** 的映射：命中时词法器报 `E-Logic-WordOp`，
# 并把替换后的运算符 token 交给 parser。**为什么要产出替换 token 而不是
# ERROR 占位**：`if (a and b)` 里若给 ERROR，parser 会在同一处再报一条
# 「期望表达式」的级联噪声；给 `&&` 则只有一条诊断，且说得清改法。
WORD_OPERATORS = {
    "or": ("||", Kind.PIPEPIPE),
    "and": ("&&", Kind.AMPAMP),
    "not": ("!", Kind.BANG),
}

# **`public` 不在这张表里**（2026-11-10）：它是 C++ 的**成员访问说明符**，
# `struct S { public: x: u64, }` 必须能写。它按普通标识符进词法，
# 由 parser 判断位置：struct 体里的 `public:` 接受（并忽略——本语言没有
# 访问控制）；顶层的 `public func f()` 仍报 `E-Keyword-LongForm`（应写 `pub`）。
LONG_FORM_HINT = {
    "function": "func",
    "structure": "struct",
    "enumeration": "enum",
    "implementation": "impl",
    "specification": "spec",
    "constant": "const",
    "variable": "var",
    "mutable": "mut",
    "assembly": "asm",
    "external": "extern",
}

OPERATORS = [
    ("?!", Kind.QUESTION_BANG),
    ("::", Kind.PATHSEP),
    ("|>", Kind.PIPE_GT),
    ("||", Kind.PIPEPIPE),
    ("&&", Kind.AMPAMP),
    ("<<", Kind.SHL),
    ("..", Kind.RANGE),
    ("->", Kind.ARROW),
    ("=>", Kind.FATARROW),
    ("<=", Kind.LE),
    (">=", Kind.GE),
    ("==", Kind.EQEQ),
    ("!=", Kind.NE),
    # **`++` / `--` 必须排在 `+` / `-` 前面**：这张表按顺序做最长匹配（先命中
    # 先赢），写到 `+` 后面就永远轮不到它们。
    ("++", Kind.INC),
    ("--", Kind.DEC),
    ("+=", Kind.PLUS_EQ),
    ("-=", Kind.MINUS_EQ),
    ("*=", Kind.STAR_EQ),
    # **`**` 必须排在 `*` 前面**：这里按顺序做最长匹配（先命中先赢），
    # 放到 `*` 后面就永远轮不到它。
    ("**", Kind.STAR_STAR),
    ("/=", Kind.SLASH_EQ),
    ("(", Kind.LPAREN),
    (")", Kind.RPAREN),
    ("{", Kind.LBRACE),
    ("}", Kind.RBRACE),
    ("[", Kind.LBRACKET),
    ("]", Kind.RBRACKET),
    (",", Kind.COMMA),
    (";", Kind.SEMI),
    (":", Kind.COLON),
    (".", Kind.DOT),
    ("?", Kind.QUESTION),
    ("&", Kind.AMP),
    ("|", Kind.PIPE),
    ("^", Kind.CARET),
    ("~", Kind.TILDE),
    ("+", Kind.PLUS),
    ("-", Kind.MINUS),
    ("*", Kind.STAR),
    ("/", Kind.SLASH),
    ("%", Kind.PERCENT),
    ("<", Kind.LT),
    (">", Kind.GT),
    ("=", Kind.EQ),
    ("!", Kind.BANG),
    ("⊖", Kind.OMINUS),
]

# L18.1 封闭转义集
ESCAPES = {
    "'": 39,
    "\\": 92,
    "n": 10,
    "t": 9,
    "r": 13,
    "0": 0,
}


# ---------------------------------------------------------------- 数据结构

@dataclass
class Token:
    kind: Kind
    text: str
    line: int
    col: int
    start: int
    end: int
    value: Optional[object] = None      # INT/FLOAT/CHAR 的数值


@dataclass
class Diagnostic:
    code: str
    message: str
    line: int
    col: int
    start: int
    end: int


@dataclass
class LexResult:
    tokens: List[Token] = field(default_factory=list)
    trivia: List[Token] = field(default_factory=list)     # 注释：归属 CST，不进 token 流
    diagnostics: List[Diagnostic] = field(default_factory=list)


# ---------------------------------------------------------------- 主体

class Lexer:
    def __init__(self, src: str, path: str = "<input>", defines=None):
        # **去掉 UTF-8 BOM**：Windows 记事本 / PowerShell 5 的 `Out-File -Encoding UTF8`
        # 都会在文件头写 U+FEFF；不剥掉的话第一个 token 就是"无法识别的字符"。
        if src.startswith("\ufeff"):
            src = src[1:]
        self.src = src
        self.path = path
        self.defines = defines
        self.i = 0
        self.line = 1
        self.col = 1
        self.n = len(src)
        self.result = LexResult()

    # -- 位置 --
    def _advance(self, k: int = 1):
        for _ in range(k):
            if self.i < self.n:
                if self.src[self.i] == "\n":
                    self.line += 1
                    self.col = 1
                else:
                    self.col += 1
                self.i += 1

    def _peek(self, off: int = 0) -> str:
        j = self.i + off
        return self.src[j] if j < self.n else ""

    def _err(self, code: str, msg: str, start: int, line: int, col: int):
        self.result.diagnostics.append(
            Diagnostic(code, msg, line, col, start, self.i)
        )
        self.result.tokens.append(
            Token(Kind.ERROR, self.src[start:self.i], line, col, start, self.i)
        )

    # -- 主循环 --
    def run(self) -> LexResult:
        # **先预处理再分析**（规格 §8.10，2026-11-10）：`#define` / `#ifdef` 一族
        # 在词法之前展开成真正的程序。返回文本**与原文逐行对齐**（指令行换成
        # 空行、宏体在定义时折成一行），所以下面的 token 行号仍然指**原文件**的行。
        _pp = preprocess(self.src, self.path, self.defines)
        self.src = _pp.text
        self.n = len(self.src)
        self.result.diagnostics.extend(
            Diagnostic(d.code, d.message, d.line, d.col, 0, 0)
            for d in _pp.diagnostics)

        while self.i < self.n:
            ch = self._peek()

            # 空白
            if ch in " \t\r\n":
                self._advance()
                continue

            # 注释
            if ch == "/" and self._peek(1) == "/":
                self._comment()
                continue

            # **块注释 `/* … */`（2026-11-13）**：与 C 一致——**跨行**、**不嵌套**、
            # 内容不是代码。以前这里是"不存在"的专属错误：`/` `*` 被当成两个
            # 运算符，后面整段注释正文变成表达式片段，报的是「期望 ';'」之类
            # 完全无关的诊断（真实 C 库 cJSON / inih / tinyexpr 的 37–88% 诊断
            # 都是这一条引起的）。现在整段跳过，**行号照常推进**。
            if ch == "/" and self._peek(1) == "*":
                self._block_comment()
                continue

            start, line, col = self.i, self.line, self.col

            # 字符字面量
            if ch == "'":
                self._char()
                continue

            # 字符串字面量（本语言没有；词法识别后报错，给出明确诊断）
            if ch == '"':
                self._string()
                continue

            # 数字
            if ch.isdigit():
                self._number()
                continue

            # **C++ 原始字符串 `R"( … )"`**（2026-11-10）：原始字符串里**没有
            # 转义**，`\s` 就是两个字符。真实 C++ 文件里的 `std::regex` 常量
            # 几乎都这么写（`R"([\s\S]*?)"`）——按普通字符串解码会误报
            # `E-Lex-BadEscape`，而它在 C++ 里完全合法。放在标识符之前：
            # `R` 紧贴 `"` 才是原始字符串（`R "x"` 仍是标识符 + 字符串）。
            if ch == "R" and self._peek(1) == '"':
                self._raw_string()
                continue

            # 标识符 / 关键字
            if ch.isalpha() or ch == "_":
                self._ident()
                continue

            # **预处理器指令 `#include` / `#define`**（2026-11-10）：以前每个
            # `#` 都是一条 `E-Lex-UnknownChar`，再级联一条解析错误。这里整行
            # 吃掉、给一条带改法的诊断，且**不产出 ERROR token**（不再级联）。
            if ch == "#" and self._at_line_start():
                self._preprocessor()
                continue

            # 运算符
            for text, kind in OPERATORS:
                if self.src.startswith(text, self.i):
                    self._advance(len(text))
                    self.result.tokens.append(
                        Token(kind, text, line, col, start, self.i)
                    )
                    break
            else:
                self._advance()
                self._err(
                    "E-Lex-UnknownChar",
                    f"无法识别的字符 {ch!r}",
                    start, line, col,
                )

        self.result.tokens.append(
            Token(Kind.EOF, "", self.line, self.col, self.i, self.i)
        )
        return self.result

    # -- 各类 --
    def _comment(self):
        start, line, col = self.i, self.line, self.col
        doc = self.src.startswith("///", self.i)
        while self.i < self.n and self._peek() != "\n":
            self._advance()
        self.result.trivia.append(
            Token(Kind.STRING if doc else Kind.STRING, self.src[start:self.i],
                  line, col, start, self.i, value="doc" if doc else "line")
        )

    def _at_line_start(self) -> bool:
        """当前位置是否是一行里第一个非空白字符（`#…` 只在行首当预处理）。"""
        j = self.i - 1
        while j >= 0 and self.src[j] in " \t":
            j -= 1
        return j < 0 or self.src[j] == "\n"

    def _preprocessor(self):
        """`#…` 整行：按指令**分三类**处理（R1-A，2026-11-10）。

        产品目标是「一个 C++ 程序只改文件名就能当 `.eln` 用」，所以真实 C++ 文件
        的第一屏（`#include` 一片）**必须吃得下**：

        | 指令 | 处理 | 理由 |
        |---|---|---|
        | `#include <…>` / `#include "…"` | **标准头：接受、忽略、不报错**；**非标准头：`E-Lex-IncludeUnresolved`**（2026-11-13） | 内建类型与门面头已覆盖标准库；第三方头不读 ⇒ 必须明说，否则 check 通过是假的 |
        | `#pragma …` | **接受、忽略、不报错** | 编译期提示，与语义无关 |
        | `#define …` / 其它（`#if` / `#ifdef` / `#undef` …） | **报错（暂不支持）** | 宏会改变成本与语义；**静默吞掉**等于假装展开过 |

        **为什么不产出 ERROR token**：词法已经说清「这一行是什么」，再让 parser
        在占位 token 上报「这里不该出现 'error-token'」只是级联噪声。
        """
        start, line, col = self.i, self.line, self.col
        while self.i < self.n and self._peek() != "\n":
            self._advance()
        text = self.src[start:self.i].strip()
        # 指令名：`#` 之后、空白之前（`#include` / `#define` / `#pragma` …）
        body = text[1:].lstrip() if text.startswith("#") else text
        name = body.split(None, 1)[0] if body else ""
        # **标准头接受并忽略、非标准头报错**：`#include`（两种引号形式）；`#pragma` 一律忽略
        if name in ("include", "pragma"):
            return
        if name == "define":
            self.result.diagnostics.append(
                Diagnostic(
                    "E-Lex-MacroUnsupported",
                    f"`#define` 暂不支持：宏会影响成本（本语言的 κ 按静态类型推导，"
                    f"展开后的代码若来自宏，上界就无从界定）。\n"
                    f"改法：常量写 `const u64 N = 10;`（模块顶层），"
                    f"「函数式宏」写具名函数，条件编译去掉后只留实际分支。",
                    line, col, start, self.i))
            return
        # 其它指令（`#if` / `#ifdef` / `#undef` / `#error` / `#line` …）：
        # 都是**有语义**的，不能忽略——忽略会让两个分支同时生效。
        self.result.diagnostics.append(
            Diagnostic(
                "E-Lex-Preprocessor",
                f"不支持的预处理指令：`{text}`。\n"
                f"本语言只接受**标准头** `#include <…>`（忽略）与 `#pragma …`（忽略），非标准头报 E-Lex-IncludeUnresolved；"
                f"`#define` / 条件编译暂不支持。\n"
                f"改法：外部函数用 `.tu` / `.eln` 顶层的 "
                f"`extern f(…) -> T from \"lib\";`；常量用 "
                f"`const N: u64 = 10;`；条件编译去掉后只留实际分支。",
                line, col, start, self.i))

    def _block_comment(self):
        """`/* … */` 块注释（2026-11-13）：**跳过**，进 trivia，不进 token 流。

        与 C 一致的三条判据（这里是**唯一的实现点**）：

        - **跨行**：逐字符走 `_advance()`，换行照常 `line += 1` / `col = 1`。
          这就是「行号必须仍然准」的保证——注释占几行，它后面的 token 就报
          第几行，诊断的 `line`/`col` 与没有注释时**同一套坐标**。
        - **不嵌套**：`/* /* */` 在**第一个** `*/` 结束（C 就是这样；
          `/* /* */ */` 的后半段 ` */` 是普通代码，会照常报错）。判据只看
          「当前两个字符是不是 `*/`」，不维护深度。
        - **未闭合**：到文件尾仍没有 `*/` ⇒ `E-Lex-BlockCommentUnterminated`，
          位置指向**开注释的那一对 `/*`**（不是 EOF），消息里给出行号与改法。
          **不许静默吞到文件末尾**：那样后面的代码全被吃掉，诊断数会假性下降。

        **不产出 ERROR token**：注释正文不是代码，占位 token 只会让 parser 在
        同一位置再报一条「期望表达式，实际是 'error-token'」的级联噪声。
        未闭合时也一样——它已经在文件尾，后面没有 token 可级联。

        注释文本进 `trivia`（与 `//` 同一套结构，`value="block"`）：格式化器 /
        文档工具需要知道原文；词法之后的任何阶段都看不到它。
        """
        start, line, col = self.i, self.line, self.col
        self._advance(2)                       # `/*`
        while self.i < self.n and not (self._peek() == "*" and self._peek(1) == "/"):
            self._advance()
        if self.i < self.n:
            self._advance(2)                   # `*/`
            closed = True
        else:
            closed = False
        self.result.trivia.append(
            Token(Kind.STRING, self.src[start:self.i],
                  line, col, start, self.i, value="block")
        )
        if closed:
            return
        self.result.diagnostics.append(
            Diagnostic(
                "E-Lex-BlockCommentUnterminated",
                f"块注释 `/*` 没有闭合：从 {line}:{col} 开始，一直到文件末尾"
                f"（第 {self.line} 行）都没有找到 `*/`。\n"
                f"改法：① 补上 `*/`；② 或者整段改成 `//` 行注释（每行一条）。\n"
                f"注意 `/* … */` **不嵌套**：`/* /* */` 在第一个 `*/` 就结束"
                f"（与 C 一致），所以里面再写 `/*` 不会把结束符拖后。",
                line, col, start, self.i))

    def _ident(self):
        start, line, col = self.i, self.line, self.col
        while self.i < self.n and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        text = self.src[start:self.i]

        if text in KEYWORDS:
            self.result.tokens.append(Token(KEYWORDS[text], text, line, col, start, self.i))
            return

        # **词形逻辑运算符已退役**（2026-11-11）：`or` / `and` / `not`。
        # 报一条专属诊断并教符号写法；同时产出**替换后的运算符 token**，
        # 这样 parser 不会在同一点再报级联错误（见 `WORD_OPERATORS`）。
        if text in WORD_OPERATORS:
            sym, kind = WORD_OPERATORS[text]
            self.result.diagnostics.append(Diagnostic(
                "E-Logic-WordOp",
                f"`{text}` 是已退役的词形运算符：本语言只保留符号写法。\n"
                f"改法：`or` → `||`、`and` → `&&`、`not` → `!`"
                f"（这一处把 `{text}` 写成 `{sym}`）。\n"
                f"例：`if (a && b)` / `if (!p)` / `return p || q;`。\n"
                f"理由：同一个概念只给一种写法——两套拼写会让读者（和模型）"
                f"先判「这是不是两种不同的东西」，再猜优先级。",
                line, col, start, self.i))
            self.result.tokens.append(Token(kind, sym, line, col, start, self.i))
            return

        # 长写法：C3 明确是错误，不是兼容形式 —— 给出"应改成什么"
        if text in LONG_FORM_HINT:
            self._err(
                "E-Keyword-LongForm",
                f"关键字 MUST 用缩写：`{text}` 应写作 `{LONG_FORM_HINT[text]}`",
                start, line, col,
            )
            return

        # **退役关键字一律产出 IDENT**（2026-11-10）。
        #
        # 以前这里有两条"词形"分支：成员位置的 `end` 降级成 IDENT、其余
        # `end`/`val`/`var`/`let`/`fn` 直接报 `E-End-Removed` / `E-Decl-OldKeyword`
        # / `E-Func-KeywordRemoved`。词法层看不到语法位置，于是
        # `size_t end`（参数名）、`long long val`（变量名）都被误伤。
        #
        # 现在**诊断全交给 parser**（位置判据：块终结符 / 声明形状），
        # 词法器只负责出 `IDENT`。`REMOVED_KEYWORDS` 保留为词表（编辑器一致
        # 性测试用），不再在这里查。
        self.result.tokens.append(Token(Kind.IDENT, text, line, col, start, self.i))

    def _emit_int(self, text: str, val: int, start: int, line: int, col: int):
        """登记一个整数字面量 token（`0x` / `0b` / 十进制共用）。

        **超出 `u64` 就报错**，不静默回绕：字面量是编译期常量，没有"运行时
        操作数位宽"可以让它回绕；`0x1_0000_0000_0000_0000` 多半是打错了一位，
        静默变 `0` 会让 SHA-256 的 K 表悄悄算错。十进制走同一条规则——
        否则 `0x1_0000_0000_0000_0000` 报错、`18446744073709551616` 却回绕，
        同一个值两种结局。
        """
        if val > U64_MAX:
            self._err(
                "E-Lex-IntOverflow",
                f"整数字面量超出 u64 范围：{text}（u64 最大 {U64_MAX}）。"
                f"本语言没有比 u64 更宽的整数类型，也不会静默按 2^64 回绕",
                start, line, col)
            return
        self.result.tokens.append(
            Token(Kind.INT, text, line, col, start, self.i, val))

    def _number(self):
        start, line, col = self.i, self.line, self.col
        is_float = False

        # **`0o17` 八进制不存在**（2026-11-10）：以前词法成 `0` + `o17` 两个
        # token，解析器在 `o17` 处报一句无关的「期望 ';'，实际是 'ident'」。
        # 这里整段吃掉并给改法（十进制或 `0x`）。
        if self._peek() == "0" and self._peek(1) in ("o", "O"):
            self._advance(2)
            while self.i < self.n and (self._peek().isdigit() or self._peek() == "_"):
                self._advance()
            text = self.src[start:self.i]
            digits = text[2:].replace("_", "")
            try:
                dec = int(digits, 8) if digits else None
            except ValueError:
                dec = None
            if dec is None:
                how = "写十进制（如 `15`）或 `0x` 十六进制（如 `0xF`）"
            else:
                how = f"写十进制 `{dec}` 或 `0x{dec:X}`"
            self._err(
                "E-Lex-OctalUnsupported",
                f"八进制字面量 `{text}` 不支持：本语言只有十进制、"
                f"`0x` 十六进制、`0b` 二进制。{how}。", start, line, col)
            return

        if self._peek() == "0" and self._peek(1) in ("x", "X"):
            self._advance(2)
            while self.i < self.n and (self._peek().isdigit() or self._peek().lower() in "abcdef" or self._peek() == "_"):
                self._advance()
            text = self.src[start:self.i]
            try:
                val = int(text.replace("_", ""), 16)
            except ValueError:
                self._err("E-Lex-BadNumber", f"十六进制字面量无效：{text}", start, line, col)
                return
            self._emit_int(text, val, start, line, col)
            return

        if self._peek() == "0" and self._peek(1) in ("b", "B"):
            self._advance(2)
            while self.i < self.n and (self._peek() in "01" or self._peek() == "_"):
                self._advance()
            text = self.src[start:self.i]
            try:
                val = int(text.replace("_", ""), 2)
            except ValueError:
                self._err("E-Lex-BadNumber", f"二进制字面量无效：{text}", start, line, col)
                return
            self._emit_int(text, val, start, line, col)
            return

        while self.i < self.n and (self._peek().isdigit() or self._peek() == "_"):
            self._advance()

        # 浮点：小数点后必须跟数字，且不能是 `..`
        if self._peek() == "." and self._peek(1).isdigit():
            is_float = True
            self._advance()
            while self.i < self.n and (self._peek().isdigit() or self._peek() == "_"):
                self._advance()

        # 指数
        if self._peek() in ("e", "E"):
            save = self.i
            self._advance()
            if self._peek() in ("+", "-"):
                self._advance()
            if self._peek().isdigit():
                is_float = True
                while self.i < self.n and self._peek().isdigit():
                    self._advance()
            else:
                self.i = save      # 不是指数，回退

        text = self.src[start:self.i]
        clean = text.replace("_", "")
        if is_float:
            self.result.tokens.append(Token(Kind.FLOAT, text, line, col, start, self.i, float(clean)))
        else:
            self._emit_int(text, int(clean), start, line, col)

    def _char(self):
        """L18.1：单引号内恰好一个码位；封闭转义集。"""
        start, line, col = self.i, self.line, self.col
        self._advance()      # 吃掉 '

        if self.i >= self.n or self._peek() == "\n":
            self._err("E-CharLit-Unterminated", "字符字面量未闭合", start, line, col)
            return

        if self._peek() == "'":
            self._advance()
            self._err("E-CharLit-Empty", "空字符字面量；空字节请写 '\\0'", start, line, col)
            return

        val = None
        if self._peek() == "\\":
            self._advance()
            esc = self._peek()
            if esc in ESCAPES:
                val = ESCAPES[esc]
                self._advance()
            elif esc == "x":
                self._advance()
                h = self.src[self.i:self.i + 2]
                if len(h) == 2 and all(c in "0123456789abcdefABCDEF" for c in h):
                    val = int(h, 16)
                    self._advance(2)
                else:
                    self._err("E-CharLit-BadEscape",
                              f"`\\x` 后必须是两位十六进制，得到 {h!r}", start, line, col)
                    return
            else:
                self._err("E-CharLit-BadEscape",
                          f"不在封闭转义集内：`\\{esc}`。合法：\\' \\\\ \\n \\t \\r \\0 \\xNN",
                          start, line, col)
                return
        else:
            ch = self._peek()
            if ord(ch) >= 128:
                first = ch.encode("utf-8")[0]
                self._err("E-CharLit-NonAscii",
                          f"非 ASCII 字符 {ch!r}；其 UTF-8 首字节写作 '\\x{first:02x}'",
                          start, line, col)
                self._advance()
                return
            if ord(ch) < 32:
                self._err("E-CharLit-BadEscape",
                          "控制字符必须转义", start, line, col)
                self._advance()
                return
            val = ord(ch)
            self._advance()

        if self.i >= self.n or self._peek() == "\n":
            self._err("E-CharLit-Unterminated", "字符字面量未闭合", start, line, col)
            return

        if self._peek() != "'":
            self._err("E-CharLit-Multi", "字符字面量含多个码位", start, line, col)
            while self.i < self.n and self._peek() not in ("'", "\n"):
                self._advance()
            if self._peek() == "'":
                self._advance()
            return

        self._advance()      # 吃掉结尾 '
        self.result.tokens.append(
            Token(Kind.CHAR, self.src[start:self.i], line, col, start, self.i, val)
        )

    def _raw_string(self):
        r"""C++ 原始字符串 `R"delim( … )delim"` → **字节序列**（2026-11-10）。

        ## 为什么必须支持

        真实 C++ 文件里 `std::regex` 的常量几乎都写成
        `R"([\`]{3}[\s\S]*?[\`]{3})"`。原始字符串里**没有转义**：`\s` 就是
        反斜杠 + `s` 两个字符。以前它走普通字符串路径，于是 `\s` / `\S` 各报
        一条 `E-Lex-BadEscape`——**在 C++ 里它们完全合法**，这正是"真实文件
        改名后被误伤"。正确的修法是**认出原始字符串**，而不是放宽转义：
        普通字符串 `"\s"` 仍然报错（C++ 里也非法）。

        ## 形状

        `R"delim( 正文 )delim"`，`delim` 可为空（此时收尾是 `)"`），最多
        16 个字符、只允许字母数字下划线。正文里的反斜杠、引号、换行都是字面量。
        """
        start, line, col = self.i, self.line, self.col
        self._advance(2)                        # `R"`
        delim = []
        while self.i < self.n and self._peek() not in "(\n":
            delim.append(self._peek())
            self._advance()
        text_delim = "".join(delim)
        if (self._peek() != "(" or len(text_delim) > 16
                or any(not (c.isalnum() or c == "_") for c in text_delim)):
            self._err(
                "E-Lex-BadRawString",
                f"`R\"` 后面必须是合法的原始字符串定界符：`R\"( 正文 )\"` 或 "
                f"`R\"tag( 正文 )tag\"`（tag ≤16 个字母/数字/下划线）。\n"
                f"若你想写的是普通字符串，`R` 与引号之间要有空格；"
                f"普通字符串里 `\\s` 这类转义仍然非法。",
                start, line, col)
            return
        self._advance()                         # `(`
        close = ")" + text_delim + '"'
        body_start = self.i
        while self.i < self.n and not self.src.startswith(close, self.i):
            self._advance()
        if self.i >= self.n:
            self._err("E-Lex-UnterminatedString",
                      "C++ 原始字符串没有结束标记 "
                      f"`{close}`（原始字符串跨行，定界符必须成对）",
                      start, line, col)
            return
        body = self.src[body_start:self.i]
        self._advance(len(close))
        self.result.tokens.append(
            Token(Kind.STRING, self.src[start:self.i], line, col, start, self.i,
                  body.encode("utf-8"))
        )

    def _string(self):
        r"""字符串字面量 → **字节序列**（v0.71）。

        设计见 `临时计划-字符串与print.md`：
        · 类型是不可变 `Vec<u8>`；**编译期常量、零分配**（成本 1 个操作）；
        · 转义只支持最小集：`\n` `\t` `\\` `\"` `\xNN`。
        """
        start, line, col = self.i, self.line, self.col
        self._advance()
        out = bytearray()
        while self.i < self.n and self._peek() not in ('"', "\n"):
            ch = self._peek()
            if ch == "\\":
                self._advance()
                esc = self._peek()
                if esc == "n":
                    out.append(10)
                elif esc == "t":
                    out.append(9)
                elif esc == "\\":
                    out.append(92)
                elif esc == '"':
                    out.append(34)
                elif esc == "x":
                    self._advance()
                    h = self.src[self.i:self.i + 2]
                    try:
                        out.append(int(h, 16))
                    except ValueError:
                        self._err(
                            "E-Lex-BadEscape",
                            f"非法的十六进制转义 \\x{h}：`\\x` 后必须是**两位**"
                            f"十六进制（如 `\\x41`）。\n"
                            f"字符串支持的转义集合：`\\n` `\\t` `\\\\` `\\\"` `\\xNN`；"
                            f"`\\s` / `\\S` / `\\d` 这类正则写法**没有**转义含义，"
                            f"要字面反斜杠就写 `\\\\s`。",
                            self.i, self.line, self.col)
                    self._advance()
                else:
                    self._err(
                        "E-Lex-BadEscape",
                        f"不支持的转义 \\{esc}。\n"
                        f"字符串支持的转义集合只有：`\\n`（换行）、`\\t`（制表）、"
                        f"`\\\\`（反斜杠）、`\\\"`（引号）、`\\xNN`（两位十六进制字节）。\n"
                        f"`\\s` / `\\S` / `\\d` / `\\w` 是**正则**写法，不是本语言的"
                        f"转义——C++ 里同样非法；要字面反斜杠写 `\\\\s`。",
                        self.i, self.line, self.col)
                self._advance()
                continue
            out.extend(ch.encode("utf-8"))
            self._advance()
        if self._peek() != '"':
            self._err("E-Lex-UnterminatedString", "字符串字面量没有结束引号", start, line, col)
            return
        self._advance()
        self.result.tokens.append(
            Token(Kind.STRING, self.src[start:self.i], line, col, start, self.i, bytes(out))
        )


def lex(src: str, path: str = "<input>", defines=None) -> LexResult:
    return Lexer(src, path, defines).run()
