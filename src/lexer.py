# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

"""dsh lexer —— M0 第一步（Python 骨架）

覆盖规格 v0.38 的词法层：
- C3 的 23 个行首关键字（缩写形式；长写法是错误）
- 上下文子句关键字（lex 成 IDENT，由 parser 认）
- 字符字面量（L18.1：封闭转义集）
- 数字字面量（10 / 0x / 0b / 下划线分隔 / 浮点）
- 注释（// 行注释、/// 文档注释）→ 作为 trivia 保留，不进 token 流
- 运算符与标点

设计取舍（M0 阶段）：
- **宽松超集**：非法输入不抛异常，产出 ERROR token 并继续，让 parser 能一次报多个错
- 每个 token 带 (line, col, start, end)，供 span 精确的 JSON 诊断
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


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
    ASSUME = "assume"        # 模块级环境假设：`assume n <= xs.length();`
    KERNELS = "kernels"      # 上下文关键字：出现在 func 体之后
    ERROR_DECL = "error"     # 模块级错误类型声明：`error CalcError;`
    BREAK = "break"          # v0.72：循环提前退出
    CONTINUE = "continue"    # v0.72：跳过本轮

    # 非行首但仍是关键字
    END = "end"          # v0.68 起废弃（块只用 `{}`），仅用于给出精确诊断
    IN = "in"
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
    PIPE = "|"
    PIPE_GT = "|>"
    PLUS = "+"
    MINUS = "-"
    STAR = "*"
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
            and k not in (Kind.VAL, Kind.VAR, Kind.END, Kind.ERROR)}
KEYWORDS[Kind.ERROR_DECL.value] = Kind.ERROR_DECL

# v0.68 下线的关键字：仍然词法识别，但由 parser 报精确诊断
REMOVED_KEYWORDS = {
    "fn": "E-Func-KeywordRemoved",
    "val": "E-Decl-OldKeyword",
    "var": "E-Decl-OldKeyword",
    "end": "E-End-Removed",
}

# 长写法：M0 只用于给出**更好的错误信息**（C3：长写法是错误，不是兼容形式）
LONG_FORM_HINT = {
    "function": "func",
    "structure": "struct",
    "enumeration": "enum",
    "implementation": "impl",
    "specification": "spec",
    "public": "pub",
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
    ("..", Kind.RANGE),
    ("->", Kind.ARROW),
    ("=>", Kind.FATARROW),
    ("<=", Kind.LE),
    (">=", Kind.GE),
    ("==", Kind.EQEQ),
    ("!=", Kind.NE),
    ("+=", Kind.PLUS_EQ),
    ("-=", Kind.MINUS_EQ),
    ("*=", Kind.STAR_EQ),
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
    def __init__(self, src: str):
        # **去掉 UTF-8 BOM**：Windows 记事本 / PowerShell 5 的 `Out-File -Encoding UTF8`
        # 都会在文件头写 U+FEFF；不剥掉的话第一个 token 就是"无法识别的字符"。
        if src.startswith("\ufeff"):
            src = src[1:]
        self.src = src
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

            # 标识符 / 关键字
            if ch.isalpha() or ch == "_":
                self._ident()
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

    def _ident(self):
        start, line, col = self.i, self.line, self.col
        while self.i < self.n and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        text = self.src[start:self.i]

        if text in KEYWORDS:
            self.result.tokens.append(Token(KEYWORDS[text], text, line, col, start, self.i))
            return

        # 长写法：C3 明确是错误，不是兼容形式 —— 给出"应改成什么"
        if text in LONG_FORM_HINT:
            self._err(
                "E-Keyword-LongForm",
                f"关键字 MUST 用缩写：`{text}` 应写作 `{LONG_FORM_HINT[text]}`",
                start, line, col,
            )
            return

        # v0.68 下线的关键字：仍产出 token（让 parser 能继续解析），但先报一条精确诊断
        if text in REMOVED_KEYWORDS:
            hint = {
                "fn": "v0.68 起函数关键字是 `func`",
                "val": "v0.68 起用 `const 类型 名 = 值;`（类型前置）",
                "var": "v0.68 起用 `类型 名 = 值;`（类型前置，不写关键字）",
                "end": "v0.68 起块只用 `}` 收尾，不再写 `end`",
            }[text]
            # 只记诊断，不插 ERROR 占位 token（下面会产出真正的 token）
            self.result.diagnostics.append(
                Diagnostic(REMOVED_KEYWORDS[text],
                           f"`{text}` 已下线：{hint}",
                           line, col, start, self.i)
            )
            kind_map = {"fn": Kind.FN, "val": Kind.VAL,
                        "var": Kind.VAR, "end": Kind.END}
            self.result.tokens.append(Token(kind_map[text], text,
                                            line, col, start, self.i))
            return

        self.result.tokens.append(Token(Kind.IDENT, text, line, col, start, self.i))

    def _number(self):
        start, line, col = self.i, self.line, self.col
        is_float = False

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
            self.result.tokens.append(Token(Kind.INT, text, line, col, start, self.i, val))
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
            self.result.tokens.append(Token(Kind.INT, text, line, col, start, self.i, val))
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
            self.result.tokens.append(Token(Kind.INT, text, line, col, start, self.i, int(clean)))

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
                        self._err("E-Lex-BadEscape", f"非法的十六进制转义 \\x{h}",
                                  self.i, self.line, self.col)
                    self._advance()
                else:
                    self._err("E-Lex-BadEscape", f"不支持的转义 \\{esc}",
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


def lex(src: str) -> LexResult:
    return Lexer(src).run()
