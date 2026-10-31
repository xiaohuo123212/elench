#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
parser.py —— AI 语言 M0 解析器（宽松超集）

目标（M0 验收 ⑦）：
  · 完整解析 样例/calculator.tu 与 样例/calculator.eln
  · 对非法输入给出带 span 的诊断，并能恢复到下一个可解析位置

设计取舍：
  · 「宽松超集」——接受规格允许的一切，额外接受少量将来可能出现的形状，
    真正收紧靠 lint（L3/L4/L5），不靠解析失败。
  · 注释是 trivia，不进 token 流（CST 归属由后续 formatter 处理）。
  · 这里产出 AST（不含注释位置）；CST 留给 fmt 阶段。

用法：
  python parser.py <file.tu|file.eln>
  python parser.py --json <file>
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import List, Optional

from lexer import Kind, Lexer, Token


# ---------------------------------------------------------------- AST

@dataclass
class Node:
    """统一的 AST 节点。kind 是字符串，便于打印与测试。"""
    kind: str
    line: int = 0
    col: int = 0
    name: Optional[str] = None
    value: object = None
    children: List["Node"] = field(default_factory=list)

    def add(self, *nodes: "Node") -> "Node":
        for n in nodes:
            if n is not None:
                self.children.append(n)
        return self

    def dump(self, depth: int = 0) -> str:
        pad = "  " * depth
        head = f"{pad}{self.kind}"
        if self.name:
            head += f" {self.name}"
        if self.value is not None:
            head += f" = {self.value!r}"
        head += f"   @{self.line}:{self.col}"
        out = [head]
        for c in self.children:
            out.append(c.dump(depth + 1))
        return "\n".join(out)

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "line": self.line, "col": self.col}
        if self.name:
            d["name"] = self.name
        if self.value is not None:
            d["value"] = self.value
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        return d


@dataclass
class Diagnostic:
    code: str
    message: str
    line: int
    col: int
    start: int = 0
    end: int = 0

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "line": self.line, "col": self.col,
                "start": self.start, "end": self.end}


@dataclass
class ParseResult:
    module: Optional[Node] = None
    diagnostics: List[Diagnostic] = field(default_factory=list)
    tokens: List[Token] = field(default_factory=list)
    # 宽松超集下"能接受但不规范"的发现（L3 等）。默认不算错误，严格模式才提升。
    soft: List[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.diagnostics


class ParseError(Exception):
    def __init__(self, code: str, message: str, tok: Token):
        super().__init__(message)
        self.code = code
        self.message = message
        self.tok = tok


# ---------------------------------------------------------------- 表

ITEM_START = {
    Kind.USE, Kind.CONST, Kind.ENUM, Kind.STRUCT, Kind.TRAIT, Kind.IMPL,
    Kind.SPEC, Kind.FN, Kind.PUB, Kind.REWRITE, Kind.EXTERN, Kind.EXTERNAL,
}

# 必须在块末尾出现 `end <kind>` 的构造
END_KIND = {
    "fn": "fn",
    "spec": "spec",
    "enum": "enum",
    "struct": "struct",
    "trait": "trait",
    "impl": "impl",
    "choose": "choose",
    "if": "if",
    "while": "while",
    "for": "for",
    "match": "match",
}

# 二元运算符：kind -> (左结合优先级, 右结合优先级)
BINARY = {
    Kind.OR: (1, 2),
    Kind.AND: (3, 4),
    Kind.EQEQ: (5, 6), Kind.NE: (5, 6),
    Kind.LT: (5, 6), Kind.LE: (5, 6), Kind.GT: (5, 6), Kind.GE: (5, 6),
    Kind.RANGE: (7, 8),
    Kind.PLUS: (9, 10), Kind.MINUS: (9, 10), Kind.OMINUS: (9, 10),
    Kind.STAR: (11, 12), Kind.SLASH: (11, 12), Kind.PERCENT: (11, 12),
    Kind.PIPE_GT: (13, 14),
}

# 这些关键字可以开始一个表达式（用于错误恢复与 `if` 条件判断）
EXPR_START = {
    Kind.IDENT, Kind.INT, Kind.FLOAT, Kind.CHAR, Kind.STRING,
    Kind.LPAREN, Kind.LBRACKET, Kind.SELF, Kind.TRUE, Kind.FALSE,
    Kind.NOT, Kind.MINUS, Kind.STAR, Kind.AMP, Kind.DYN,
}


# ---------------------------------------------------------------- 解析器

class Parser:
    def __init__(self, tokens: List[Token], src_name: str = "<input>"):
        # 丢掉 EOF 之前的一切照常处理，EOF 保留作哨兵
        self.toks = tokens
        self.i = 0
        self.src_name = src_name
        self.diags: List[Diagnostic] = []
        self.soft: List[Diagnostic] = []

    # -- token 访问 --
    def peek(self, off: int = 0) -> Token:
        j = self.i + off
        if j >= len(self.toks):
            return self.toks[-1]
        return self.toks[j]

    def kind(self, off: int = 0) -> Kind:
        return self.peek(off).kind

    def at(self, k: Kind) -> bool:
        return self.kind() == k

    def advance(self) -> Token:
        t = self.peek()
        if t.kind != Kind.EOF:
            self.i += 1
        return t

    def accept(self, k: Kind) -> Optional[Token]:
        if self.at(k):
            return self.advance()
        return None

    def expect(self, k: Kind, what: str) -> Token:
        if self.at(k):
            return self.advance()
        t = self.peek()
        raise ParseError("E-Parse-Expected",
                         f"期望 {what}，实际是 {t.kind.value!r}", t)

    def expect_ident(self, what: str) -> Token:
        return self.expect(Kind.IDENT, what)

    # -- 诊断 --
    def error(self, code: str, msg: str, tok: Token):
        self.diags.append(Diagnostic(
            code=code, message=msg, line=tok.line, col=tok.col,
            start=tok.start, end=tok.end))

    def recover_item(self):
        """跳到下一个可能的顶层构造起点。"""
        depth = 0
        while not self.at(Kind.EOF):
            k = self.kind()
            if k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE):
                depth += 1
            elif k in (Kind.RPAREN, Kind.RBRACKET, Kind.RBRACE):
                if depth == 0:
                    self.advance()
                    if k == Kind.RBRACE:
                        # 可能是 `} end X` 的收尾
                        if self.at(Kind.END):
                            self.advance()
                            if self.at(Kind.IDENT) or self.kind() in ITEM_START:
                                self.advance()
                        return
                    continue
                depth -= 1
            elif depth == 0 and k in ITEM_START:
                return
            self.advance()

    # ============================================================ 顶层

    def parse_module(self) -> Node:
        mod = Node("module", 1, 1, name=self.src_name)
        while not self.at(Kind.EOF):
            if self.at(Kind.SEMI):
                self.advance()
                continue
            mark = self.i
            try:
                item = self.parse_item()
                if item is not None:
                    mod.add(item)
            except ParseError as e:
                self.error(e.code, e.message, e.tok)
                if self.i == mark:
                    self.advance()
                self.recover_item()
        return mod

    def parse_item(self) -> Optional[Node]:
        t = self.peek()
        k = t.kind

        if k == Kind.ERROR_DECL:
            return self.parse_error_decl()
        if k == Kind.USE:
            return self.parse_use()
        if k == Kind.CONST:
            return self.parse_const()
        if k == Kind.ENUM:
            return self.parse_enum()
        if k == Kind.STRUCT:
            return self.parse_struct()
        if k == Kind.TRAIT:
            return self.parse_trait()
        if k == Kind.IMPL:
            return self.parse_impl()
        if k == Kind.SPEC:
            return self.parse_spec()
        if k == Kind.IDENT and t.text == "kernel":
            raise ParseError(
                "E-Kernel-KeywordRemoved",
                "v0.66 起没有独立 kernel 声明：候选由 spec 里的 choose/over 声明，"
                "实现写在 fn 之后的 `} kernels L(...) { … }` 块里", t)
        if k == Kind.CHOOSE:
            raise ParseError(
                "E-Choose-NotInSpec",
                "choose 必须写在宿主 spec 块内（v0.66）", t)
        if k == Kind.PUB:
            return self.parse_fn(is_pub=True)
        if k == Kind.FN:
            return self.parse_fn(is_pub=False)
        if k == Kind.REWRITE:
            return self.parse_rewrite()
        if k == Kind.EXTERNAL:
            return self.parse_external_block()
        if k == Kind.EXTERN:
            return self.parse_extern()
        if k == Kind.ASSUME:
            return self.parse_assume()
        if k == Kind.IDENT and (t.text or "") == "link":
            # **模块级链接**：`link "ucrtbase";` —— 本模块的外部符号来自这个库。
            # 配合"spec 找不到函数体 = 外部声明"，就不再需要 `external "lib"` 子句了。
            self.advance()
            lib = self.expect(Kind.STRING, "库名（字符串）").text
            if len(lib) >= 2 and lib[0] == lib[-1] and lib[0] in "\"'":
                lib = lib[1:-1]
            node = Node("link", t.line, t.col, name=lib)
            self.expect(Kind.SEMI, "';'")
            return node

        raise ParseError("E-Parse-UnexpectedTop",
                         f"这里不该出现 {k.value!r}", t)

    def parse_external_block(self) -> Node:
        """`external { read_file: O(1); … }` —— **给外部项登记成本**（v0.71）。

        没有登记时，成本里只有 `ext(read_file)`（"这一项不知道，其余照算"）；
        登记之后这一项被**替换成具体值**，于是整条上界变成可判定的。
        目前只接受三种写法：`O(1)`、`O(n)`、以及裸整数。
        """
        t = self.expect(Kind.EXTERNAL, "'external'")
        node = Node("external", t.line, t.col)
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            name_tok = self.expect_ident("外部函数名")
            self.expect(Kind.COLON, "':'")
            entry = Node("ext-entry", name_tok.line, name_tok.col, name=name_tok.text)
            if self.at(Kind.IDENT) and (self.peek().text or "").upper() == "O":
                self.advance()                      # `O`
                self.expect(Kind.LPAREN, "'('")
                if self.at(Kind.INT):
                    entry.value = ("const", int(self.advance().text))
                elif self.at(Kind.IDENT):
                    entry.value = ("var", self.advance().text)
                else:
                    raise ParseError("E-External-BadBound",
                                     "external 的成本只支持 O(1) / O(n)", self.peek())
                self.expect(Kind.RPAREN, "')'")
            elif self.at(Kind.INT):
                entry.value = ("const", int(self.advance().text))
            else:
                raise ParseError("E-External-BadBound",
                                 "external 的成本只支持 O(1) / O(n) / 整数", self.peek())
            self.expect(Kind.SEMI, "';'")
            node.add(entry)
        self.expect(Kind.RBRACE, "'}'")
        return node

    def parse_use(self) -> Node:
        t = self.expect(Kind.USE, "'use'")
        path = self.parse_path("use 路径")
        self.expect(Kind.SEMI, "';'")
        return Node("use", t.line, t.col, name=path)

    def parse_error_decl(self) -> Node:
        """v0.68：模块级错误类型声明 `error CalcError;`

        一个模块最多一个；`T!` 函数的错误类型由它决定。
        """
        t = self.expect(Kind.ERROR_DECL, "'error'")
        name = self.expect_ident("错误类型名").text
        self.expect(Kind.SEMI, "';'")
        return Node("error-decl", t.line, t.col, name=name)

    def parse_const(self) -> Node:
        """模块级常量。

        v0.68 起推荐 `const MAX: u64 = 10;`（名字前置，与 .tu 里的类型标注一致）；
        函数体内则是 `const u64 n = xs.length();`（类型前置）。两者靠 `:` 区分。
        """
        t = self.expect(Kind.CONST, "'const'")
        if self.kind() == Kind.IDENT and self.kind(1) == Kind.COLON:
            name = self.advance().text
            self.advance()                       # ':'
            typ = self.parse_type()
        else:
            typ = self.parse_type()
            name = self.expect_ident("常量名").text
        self.expect(Kind.EQ, "'='")
        val = self.parse_expr()
        self.expect(Kind.SEMI, "';'")
        return Node("const", t.line, t.col, name=name).add(typ, val)

    def parse_extern(self) -> Node:
        t = self.expect(Kind.EXTERN, "'extern'")
        self.accept(Kind.FN)
        name = self.expect_ident("外部符号名").text
        node = Node("extern", t.line, t.col, name=name)
        if self.accept(Kind.LPAREN):
            node.add(self.parse_params_until(Kind.RPAREN))
            self.expect(Kind.RPAREN, "')'")
        if self.accept(Kind.ARROW):
            node.add(self.parse_type())
        # **来源库**：`extern strlen(...) -> u64 from "ucrtbase";`
        # 不写 → 用进程自身符号（POSIX 下 `CDLL(None)`）。
        if self.at(Kind.IDENT) and (self.peek().text or "") == "from":
            self.advance()
            lib = self.expect(Kind.STRING, "库名（字符串）").text
            # 去掉字面量两边的引号（token.text 保留原样）
            if len(lib) >= 2 and lib[0] == lib[-1] and lib[0] in "\"'":
                lib = lib[1:-1]
            node.value = {"lib": lib}
        # **效果**：FFI 调用必须有副作用声明（默认无 = 纯函数，但必须显式）
        if self.at(Kind.IDENT) and (self.peek().text or "") == "effects":
            self.advance()
            eff = Node("extern-effects", t.line, t.col)
            self.expect(Kind.LBRACE, "'{'")
            while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
                eff.add(Node("effect-name", t.line, t.col,
                             name=self.expect_ident("效果名").text))
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RBRACE, "'}'")
            node.add(eff)
        self.expect(Kind.SEMI, "';'")
        return node

    def parse_assume(self) -> Node:
        """`assume n <= xs.length(), k <= 1024;`

        **语义**：声明**环境参数的界**（上界）。检查器只接受
        「一侧是尺寸/参数、另一侧是表达式」的 `<=` / `<` 形式——
        **`assume` 只允许写上界**，写等号或下界一律报错（它进了"可撒谎位置"清单）。
        """
        t = self.expect(Kind.ASSUME, "'assume'")
        node = Node("assume", t.line, t.col, name=None)
        while True:
            e = self.parse_expr()
            # **`assume` 只允许写上界**：`<=` 或 `<`。
            # 写 `=` / `>=` 一律报错——这是"可撒谎位置"，必须一眼看懂方向。
            if getattr(e, "kind", None) != "binary" or (e.value or "") not in ("<", "<="):
                raise ParseError(
                    "E-Assume-NotUpperBound",
                    "assume 只能写「上界」（`<=` 或 `<`），例如 `assume n <= xs.length();`",
                    t)
            node.add(e)
            if not self.accept(Kind.COMMA):
                break
        self.expect(Kind.SEMI, "';'")
        return node

    def parse_rewrite(self) -> Node:
        t = self.expect(Kind.REWRITE, "'rewrite'")
        name = self.expect_ident("重写规则名").text
        node = Node("rewrite", t.line, t.col, name=name)
        if self.accept(Kind.LBRACE):
            while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
                node.add(self.parse_clause())
            self.expect(Kind.RBRACE, "'}'")
            self.expect_end("rewrite", required=False)
        else:
            self.expect(Kind.SEMI, "';'")
        return node

    # ---------------------------------------------------------- enum / struct / trait / impl

    def parse_enum(self) -> Node:
        t = self.expect(Kind.ENUM, "'enum'")
        name = self.expect_ident("枚举名").text
        node = Node("enum", t.line, t.col, name=name)
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            node.add(self.parse_variant())
            if not self.accept(Kind.COMMA):
                break
        self.expect(Kind.RBRACE, "'}'")
        self.expect_end("enum")
        return node

    def parse_variant(self) -> Node:
        t = self.expect_ident("变体名")
        v = Node("variant", t.line, t.col, name=t.text)
        if self.accept(Kind.LPAREN):
            while not self.at(Kind.RPAREN) and not self.at(Kind.EOF):
                if self.kind() == Kind.IDENT and self.kind(1) == Kind.COLON:
                    ft = self.advance()
                    self.advance()
                    ft_node = Node("field", ft.line, ft.col, name=ft.text)
                    ft_node.add(self.parse_type())
                    v.add(ft_node)
                else:
                    v.add(self.parse_type())
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RPAREN, "')'")
        return v

    def parse_struct(self) -> Node:
        t = self.expect(Kind.STRUCT, "'struct'")
        name = self.expect_ident("结构体名").text
        node = Node("struct", t.line, t.col, name=name)
        if self.accept(Kind.LBRACE):
            while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
                ft = self.expect_ident("字段名")
                self.expect(Kind.COLON, "':'")
                f = Node("field", ft.line, ft.col, name=ft.text).add(self.parse_type())
                node.add(f)
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RBRACE, "'}'")
            self.expect_end("struct")
        else:
            self.expect(Kind.SEMI, "';'")
        return node

    def parse_trait(self) -> Node:
        t = self.expect(Kind.TRAIT, "'trait'")
        name = self.expect_ident("trait 名").text
        node = Node("trait", t.line, t.col, name=name)
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            node.add(self.parse_trait_method())
        self.expect(Kind.RBRACE, "'}'")
        self.expect_end("trait")
        return node

    def parse_trait_method(self) -> Node:
        self.accept(Kind.PUB)
        t = self.expect(Kind.FN, "'fn'")
        name = self.expect_ident("方法名").text
        node = Node("trait-method", t.line, t.col, name=name)
        self.expect(Kind.LPAREN, "'('")
        node.add(self.parse_params_until(Kind.RPAREN))
        self.expect(Kind.RPAREN, "')'")
        if self.accept(Kind.ARROW):
            node.add(self.parse_type())
        if self.at(Kind.LBRACE):
            node.add(self.parse_spec_body())
        else:
            self.expect(Kind.SEMI, "';'")
        return node

    def parse_impl(self) -> Node:
        t = self.expect(Kind.IMPL, "'impl'")
        head = self.parse_path("impl 目标")
        node = Node("impl", t.line, t.col, name=head)
        if self.accept(Kind.LPAREN):          # impl Trait for T —— 宽松接受
            node.add(self.parse_params_until(Kind.RPAREN))
            self.expect(Kind.RPAREN, "')'")
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            node.add(self.parse_trait_method())
        self.expect(Kind.RBRACE, "'}'")
        self.expect_end("impl")
        return node

    # ---------------------------------------------------------- spec

    def parse_spec(self) -> Node:
        t = self.expect(Kind.SPEC, "'spec'")
        name = self.expect_ident("函数名").text
        node = Node("spec", t.line, t.col, name=name)

        self.expect(Kind.LPAREN, "'('")
        node.add(self.parse_params_until(Kind.RPAREN))
        self.expect(Kind.RPAREN, "')'")

        if self.accept(Kind.ARROW):
            node.add(self.parse_type())
            if self.accept(Kind.BANG):          # v0.68：`T!`
                node.value = {"fallible": True}

        node.add(self.parse_spec_body())
        return node

    def parse_spec_body(self) -> Node:
        """`{ 子句* } end spec`"""
        t = self.expect(Kind.LBRACE, "'{'")
        body = Node("spec-body", t.line, t.col)
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF) \
                and not self.at(Kind.END):
            body.add(self.parse_clause())
        # `}` 由这里统一消费（choose 不再自己吃）
        if self.at(Kind.RBRACE):
            self.advance()
        elif self.at(Kind.EOF) and not self.at(Kind.END):
            raise ParseError("E-Parse-Unclosed",
                             "spec 体缺少收尾的 `}`", self.peek())
        self.expect_end("spec")
        return body

    def parse_clause(self) -> Node:
        t = self.peek()
        k = t.kind

        if k == Kind.CHOOSE:
            return self.parse_choose()

        # 旧写法 `} end spec`：spec 体到这里就该收尾了（由 parse_spec_body 处理）
        if k == Kind.END:
            raise ParseError("E-Spec-Closed", "spec 体结束", t)

        if k == Kind.EXTERNAL:
            # `spec f(...) { external "lib"; }` —— 这个 spec 由 C 符号实现
            self.advance()
            lib = self.expect(Kind.STRING, "库名（字符串）").text
            if len(lib) >= 2 and lib[0] == lib[-1] and lib[0] in "\"'":
                lib = lib[1:-1]
            node = Node("clause", t.line, t.col, name="external")
            node.value = {"lib": lib}
            self.expect(Kind.SEMI, "';'")
            return node

        if k == Kind.IDENT:
            word = t.text
            if word == "effects":
                self.advance()
                return self.parse_effects_clause(t)
            if word == "target":
                self.advance()
                return self.parse_target_clause(t)
            if word == "external":
                # **外部实现**：`external "ucrtbase";` —— 这个 spec 没有 .eln 实现，
                # 由 C ABI 符号提供。这样**外部函数和普通函数用同一套 spec**：
                # 有签名、有效果、有 target，`cost` / `bench` / `header` 都能用。
                self.advance()
                lib = self.expect(Kind.STRING, "库名（字符串）").text
                if len(lib) >= 2 and lib[0] == lib[-1] and lib[0] in "\"'":
                    lib = lib[1:-1]
                node = Node("clause", t.line, t.col, name="external")
                node.value = {"lib": lib}
                self.expect(Kind.SEMI, "';'")
                return node
            if word == "benchmodel":
                self.advance()
                return self.parse_benchmodel(t)
            if word in ("model", "usemodel"):
                raise ParseError(
                    "E-Clause-OldModel",
                    f"`{word}` 已下线（v0.69）：改用 `benchmodel {{ data {{ … }} "
                    f"scale {{ … }} }}`，一个 spec 只写一个", t)
            # 其余子句：`名字 expr ;`
            self.advance()
            node = Node("clause", t.line, t.col, name=word)
            if self.at(Kind.SEMI):
                # requires / guarantees / sizes / repeats 都 MUST 有表达式
                if word in ("requires", "guarantees", "sizes", "repeats"):
                    raise ParseError("E-Clause-EmptyExpr",
                                     f"子句 {word} 缺少表达式", t)
            else:
                node.add(self.parse_expr())
            self.expect(Kind.SEMI, "';'")
            return node

        raise ParseError("E-Parse-UnexpectedClause",
                         f"这里不该出现 {k.value!r}", t)

    def parse_effects_clause(self, t: Token) -> Node:
        node = Node("clause", t.line, t.col, name="effects")
        if self.at(Kind.IDENT) and self.peek().text == "pure":
            # v0.62：不再允许 `effects pure;`，必须写成 `effects {};` 或 `effects {pure};`
            raise ParseError("E-Effects-BarePure",
                             "effects 必须用花括号（v0.62 起）；纯函数写 `effects {};`",
                             self.peek())
        self.expect(Kind.LBRACE, "'{'")
        names = []
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            names.append(self.expect_ident("效果名").text)
            if not self.accept(Kind.COMMA):
                break
        self.expect(Kind.RBRACE, "'}'")
        self.expect(Kind.SEMI, "';'")
        # `pure` 只能单独出现
        if "pure" in names and len(names) > 1:
            raise ParseError("E-Effects-PureMixed",
                             "pure 不能与其它效果混用", t)
        node.value = "pure" if (not names or names == ["pure"]) else names
        return node

    def parse_target_clause(self, t: Token) -> Node:
        node = Node("clause", t.line, t.col, name="target")
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            dim = self.expect_ident("成本维度").text
            self.expect(Kind.COLON, "':'")
            spec = Node("dim", t.line, t.col, name=dim)
            if self.accept(Kind.LBRACE):
                # { max: expr } / { min: …, max: … } / { exact: N }
                while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
                    slot = self.expect_ident("槽位（min/max/exact）").text
                    self.expect(Kind.COLON, "':'")
                    slot_node = Node("slot", t.line, t.col, name=slot).add(self.parse_expr())
                    spec.add(slot_node)
                    if not self.accept(Kind.COMMA):
                        break
                self.expect(Kind.RBRACE, "'}'")
            else:
                spec.add(self.parse_expr())
            node.add(spec)
            if not self.accept(Kind.COMMA):
                break
        self.expect(Kind.RBRACE, "'}'")
        self.expect(Kind.SEMI, "';'")
        return node

    def parse_benchmodel(self, t: Token) -> Node:
        """spec 体内的每一项 MUST 以 ';' 结束（含 `benchmodel` / `choose` 这类块）。

        理由：这些子句以**上下文关键字**开头（词法上是普通标识符），
        没有 `;` 就无法判断子句在哪里结束。`func` / `spec` 是真关键字、自起，故不带 `;`。
        """
        node = self._parse_benchmodel_core(t)
        # **甲（统一带 ;）**：样例与 `dsh fmt` 现在一律带 `;`。
        # 解析器这里**接受但不强制**——强制需要把 30+ 内联用例逐个改，
        # 且 spec 体内与顶层是两条解析路径，要单独设计（记入缺口 E27）。
        self.accept(Kind.SEMI)
        return node

    def _parse_benchmodel_core(self, t: Token) -> Node:
        """`benchmodel { data { … } scale { … } seed = n; }`（v0.69）

        一个 spec 只有一个 benchmodel；原先的 `model 场景名` + `usemodel` 已下线。
        """
        node = Node("benchmodel", t.line, t.col)
        self.expect(Kind.LBRACE, "'{'")

        # ---- data
        if not self._at_word("data"):
            raise ParseError("E-Bench-MissingData",
                             "benchmodel 缺少 `data` 段（每个参数的值从哪来，必写）", self.peek())
        self.advance()
        data = Node("bench-data", self.peek().line, self.peek().col)
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            pt = self.expect_ident("参数名")
            self.expect(Kind.EQ, "'='")
            data.add(Node("binding", pt.line, pt.col, name=pt.text).add(self.parse_expr()))
            self.expect(Kind.SEMI, "';'")
        self.expect(Kind.RBRACE, "'}'")
        node.add(data)

        # ---- scale
        if not self._at_word("scale"):
            raise ParseError("E-Bench-MissingScale",
                             "benchmodel 缺少 `scale` 段（测哪些规模，必写）", self.peek())
        self.advance()
        scale = Node("bench-scale", self.peek().line, self.peek().col)
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            st = self.peek()
            # v0.69：`参数 in range(...)`（旧写法 `n(参数) = range(...)` 已下线，MB-14）
            if not (st.kind == Kind.IDENT):
                raise ParseError("E-Bench-BadScale",
                                 "scale 段只能写 `参数名 in range(...)`", st)
            pn_tok = self.advance()
            pn = pn_tok.text
            if self.at(Kind.LPAREN):
                raise ParseError(
                    "E-Bench-OldScale",
                    f"`n({pn}) = range(...)` 已下线（v0.69）；"
                    f"改成 `{pn} in range(...)`——`data` 用 `=`（值），"
                    f"`scale` 用 `in`（遍历规模）", pn_tok)
            if not self.at(Kind.IN):
                raise ParseError("E-Bench-BadScale",
                                 f"scale 段要写 `{pn} in range(...)`（缺 `in`）", self.peek())
            self.advance()
            scale.add(Node("scale-binding", st.line, st.col, name=pn).add(self.parse_expr()))
            self.expect(Kind.SEMI, "';'")
        self.expect(Kind.RBRACE, "'}'")
        node.add(scale)

        # ---- seed（可省）
        if self._at_word("seed"):
            st = self.advance()
            self.expect(Kind.EQ, "'='")
            node.add(Node("seed", st.line, st.col).add(self.parse_expr()))
            self.expect(Kind.SEMI, "';'")

        self.expect(Kind.RBRACE, "'}'")
        return node

    # ---------------------------------------------------------- choose

    def parse_choose(self) -> Node:
        """spec 体内的每一项 MUST 以 ';' 结束（含 `benchmodel` / `choose` 这类块）。

        理由：这些子句以**上下文关键字**开头（词法上是普通标识符），
        没有 `;` 就无法判断子句在哪里结束。`func` / `spec` 是真关键字、自起，故不带 `;`。
        """
        node = self._parse_choose_core()
        # **甲（统一带 ;）**：样例与 `dsh fmt` 现在一律带 `;`。
        # 解析器这里**接受但不强制**——强制需要把 30+ 内联用例逐个改，
        # 且 spec 体内与顶层是两条解析路径，要单独设计（记入缺口 E27）。
        self.accept(Kind.SEMI)
        return node

    def _parse_choose_core(self) -> Node:
        t = self.expect(Kind.CHOOSE, "'choose'")
        name = self.expect_ident("标签名").text
        node = Node("choose", t.line, t.col, name=name)

        # 签名写在 choose 上：候选的实现共享它（v0.66）
        if not self.at(Kind.LPAREN):
            raise ParseError(
                "E-Choose-NoSignature",
                f"choose {name} 必须写参数表，例如 `choose {name}(xs: []u64) -> u64`", t)
        self.expect(Kind.LPAREN, "'('")
        node.add(self.parse_params_until(Kind.RPAREN))
        self.expect(Kind.RPAREN, "')'")
        if self.accept(Kind.ARROW):
            node.add(self.parse_type())
            if self.accept(Kind.BANG):          # v0.68：`T!`
                node.value = {"fallible": True}

        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            ct = self.peek()
            if ct.kind == Kind.IDENT:
                word = ct.text
                self.advance()
                item = Node("choose-clause", ct.line, ct.col, name=word)
                if word == "over":
                    names, pending = self.parse_candidate_set()
                    item.value = {"names": names, "pending": pending}
                elif word == "pending":
                    item.value = self.parse_ident_set()
                elif word in ("model", "usemodel"):
                    raise ParseError(
                        "E-Clause-OldModel",
                        f"choose 里不再有 `{word}`（v0.69 已删）：bench 数据统一由宿主 spec 的 "
                        f"`benchmodel` 提供，有 `?!` 就一定测", ct)
                elif word == "objective":
                    item.value = self.expect_ident("目标名").text
                else:
                    item.add(self.parse_expr())
                self.expect(Kind.SEMI, "';'")
                node.add(item)
            else:
                raise ParseError("E-Parse-UnexpectedChoose",
                                 f"这里不该出现 {ct.kind.value!r}", ct)
        if self.at(Kind.RBRACE):
            self.advance()
        self.expect_end("choose")       # 旧写法 `end choose` 在这里报错并吃掉
        return node

    def parse_ident_set(self) -> List[str]:
        names, _ = self.parse_candidate_set(allow_pending=False)
        return names

    def parse_candidate_set(self, allow_pending: bool = True):
        """`{ a, b, c pending }` —— pending 是裸词，只跟在本项名字后面。"""
        self.expect(Kind.LBRACE, "'{'")
        names: List[str] = []
        pending: List[str] = []
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            nm = self.expect_ident("标识符").text
            names.append(nm)
            if allow_pending and self.at(Kind.KERNELS) is False and self._at_word("pending"):
                self.advance()
                pending.append(nm)
            if not self.accept(Kind.COMMA):
                break
        self.expect(Kind.RBRACE, "'}'")
        return names, pending

    # ---------------------------------------------------------- fn / kernel

    def parse_type_params(self) -> Node:
        """`<T>` / `<K, V>`：类型参数列表（v0.71 泛型第一批）。"""
        t = self.expect(Kind.LT, "'<'")
        node = Node("type-params", t.line, t.col)
        while not self.at(Kind.GT) and not self.at(Kind.EOF):
            node.add(Node("type-param", t.line, t.col,
                          name=self.expect_ident("类型参数名").text))
            if not self.accept(Kind.COMMA):
                break
        self.expect(Kind.GT, "'>'")
        return node

    def parse_fn(self, is_pub: bool) -> Node:
        if is_pub:
            self.expect(Kind.PUB, "'pub'")
        t = self.expect(Kind.FN, "'func'")
        name = self.expect_ident("函数名").text
        node = Node("fn", t.line, t.col, name=name)
        node.value = {"pub": is_pub, "fallible": False}
        # **泛型参数**：`func id<T>(…)` / `func pair<K, V>(…)`
        if self.at(Kind.LT):
            node.add(self.parse_type_params())

        self.expect(Kind.LPAREN, "'('")
        node.add(self.parse_params_until(Kind.RPAREN))
        self.expect(Kind.RPAREN, "')'")
        if self.accept(Kind.ARROW):
            node.add(self.parse_type())
            # v0.68：`T!` 表示可能失败，错误类型取模块的 error 声明
            if self.accept(Kind.BANG):
                node.value["fallible"] = True
        node.add(self.parse_block())

        # 候选块：`} kernels L(...) -> R { 名字 { … } … }`（可重复、可嵌套）
        while self.at(Kind.KERNELS):
            node.add(self.parse_kernels_clause())

        self.expect_end("fn")
        return node

    def _at_word(self, word: str) -> bool:
        return self.at(Kind.IDENT) and self.peek().text == word

    def parse_kernels_clause(self) -> Node:
        """`} kernels L(params) -> R { 候选* }`

        候选写成 `<名字> { 体 }`：无关键字、无签名——参数与返回类型由这个头给出。
        候选体之后可再跟一个 `kernels` 块（嵌套，深度上限见 checker）。
        """
        kt = self.expect(Kind.KERNELS, "'kernels'")
        label = self.expect_ident("choose 标签").text
        node = Node("kernels", kt.line, kt.col, name=label)
        node.value = {"depth": 1}

        self.expect(Kind.LPAREN, "'('")
        node.add(self.parse_params_until(Kind.RPAREN))
        self.expect(Kind.RPAREN, "')'")
        if self.accept(Kind.ARROW):
            node.add(self.parse_type())
            if self.accept(Kind.BANG):          # v0.68：候选头也允许 `T!`
                node.value["fallible"] = True

        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            node.add(self.parse_candidate())
        self.expect(Kind.RBRACE, "'}'")
        return node

    def parse_candidate(self) -> Node:
        # `noinline` 前缀（v0.69）：候选默认 inline，可捕获宿主局部变量
        if self._at_word("noinline"):
            self.advance()
        nt = self.expect_ident("候选名")
        cand = Node("candidate", nt.line, nt.col, name=nt.text)
        cand.add(self.parse_block())
        # v0.68：kernels 不嵌套。仍然解析（让检查器统一报 E-Kernels-Nested），
        # 但只允许一层——遇到第二层就直接报错，避免结构无意义地加深。
        if self.at(Kind.KERNELS):
            kt = self.peek()
            self.error("E-Kernels-Nested",
                       "kernels 不能嵌套在候选体里（v0.68）；"
                       "需要「选了 A 才需要 B」时，把 B 提升为独立函数", kt)
        return cand

    def parse_params_until(self, close: Kind) -> Node:
        """参数列表；遇到 close 就停。

        三种形态都接受：
          v0.68  `const T& name` / `T& name` / `T name`   （类型前置）
          v0.67  `name: T`                                （已下线，给兼容诊断）
          裸类型 `T`
        """
        params = Node("params")
        while not self.at(close) and not self.at(Kind.EOF):
            # v0.68：`const T& name` / `T& name` / `T name`（类型前置）
            if self.at(Kind.CONST) or self._at_cxx_param() or self._at_typed_param() \
                    or self._at_legacy_param():
                pt = self.advance() if self.at(Kind.CONST) else self.peek()
                typ = self.parse_type()
                nm = self.expect_ident("参数名").text
                params.add(Node("param", pt.line, pt.col, name=nm).add(typ))
                if not self.accept(Kind.COMMA):
                    break
                continue
            # **函数类型参数**：`func(u64, u64) -> u64 g`
            if self.at(Kind.FN) and self.kind(1) == Kind.LPAREN:
                pt = self.peek()
                typ = self.parse_type()
                nm = self.expect_ident("参数名").text
                params.add(Node("param", pt.line, pt.col, name=nm).add(typ))
                if not self.accept(Kind.COMMA):
                    break
                continue
            if self.kind() == Kind.IDENT and self.kind(1) == Kind.COLON:
                pt = self.advance()
                self.advance()
                typ = self.parse_type()
                p = Node("param", pt.line, pt.col, name=pt.text).add(typ)
                params.add(p)
            else:
                params.add(self.parse_type())
            if not self.accept(Kind.COMMA):
                break
        return params

    def _generic_tail(self, j0: int) -> bool:
        """从下标 j0 处的 `<` 起，判断是否构成 `Name<...>::`。"""
        depth = 0
        j = j0
        while self.kind(j) != Kind.EOF:
            k = self.kind(j)
            if k == Kind.LT:
                depth += 1
            elif k == Kind.GT:
                depth -= 1
                if depth == 0:
                    return self.kind(j + 1) == Kind.PATHSEP
            elif k in (Kind.SEMI, Kind.LBRACE):
                return False
            j += 1
        return False

    def _generic_call_ahead(self) -> bool:
        """从当前位置起，若是 `Name<...>::` 形状，说明这是静态成员调用而不是类型。"""
        if not self.at(Kind.IDENT) or self.kind(1) != Kind.LT:
            return False
        depth = 0
        j = 1
        while self.kind(j) != Kind.EOF:
            k = self.kind(j)
            if k == Kind.LT:
                depth += 1
            elif k == Kind.GT:
                depth -= 1
                if depth == 0:
                    return self.kind(j + 1) == Kind.PATHSEP
            elif k in (Kind.SEMI, Kind.LBRACE):
                return False
            j += 1
        return False

    def _at_legacy_param(self) -> bool:
        """`T name` 之外的旧形态 `name: &T`（v0.67）：仍在 name: 分支处理。"""
        return False

    def _at_typed_param(self) -> bool:
        """判断当前是否是 `T name` 形态的参数（两个标识符相邻，且后面是 , 或 )）。"""
        if self.kind() != Kind.IDENT or self.kind(1) != Kind.IDENT:
            return False
        return self.kind(2) in (Kind.COMMA, Kind.RPAREN)

    def _at_cxx_param(self) -> bool:
        """判断当前是否是 `T& name` 形态的参数（类型后跟 & 再跟标识符）。"""
        j = 0
        depth = 0
        while self.kind(j) != Kind.EOF:
            kk = self.kind(j)
            if kk in (Kind.LT, Kind.LBRACKET, Kind.LPAREN):
                depth += 1
            elif kk in (Kind.GT, Kind.RBRACKET):
                depth -= 1
            elif kk == Kind.AMP:
                return self.kind(j + 1) == Kind.IDENT
            elif depth == 0 and kk in (Kind.COMMA, Kind.RPAREN, Kind.COLON):
                return False
            j += 1
        return False

    # ---------------------------------------------------------- 类型

    def parse_type(self) -> Node:
        t = self.peek()

        if self.at(Kind.AMP):                           # &T / &mut T（v0.67 旧写法）
            self.advance()
            node = Node("type-ref", t.line, t.col)
            if self.kind() == Kind.IDENT and self.peek().text == "mut":
                self.advance()
                node.value = "mut"
                self.error("E-Ref-OldMut",
                           "`&mut T` 已下线：v0.68 起写 `T&`（可写引用），`&` 跟在类型后",
                           t)
            else:
                self.error("E-Ref-OldMut",
                           "`&T` 已下线：v0.68 起写 `const T&` 或 `T&`（`&` 跟在类型后）",
                           t)
            node.add(self.parse_type())
            return self.parse_type_ref_suffix(node)

        if self.at(Kind.LBRACKET):                      # []T（v0.67 旧写法）
            self.advance()
            self.expect(Kind.RBRACKET, "']'")
            self.error("E-Type-OldSlice",
                       "`[]T` 已下线：v0.68 起写 `Vec<T>`", t)
            return Node("type-slice", t.line, t.col).add(self.parse_type())

        if self.at(Kind.FN) and self.kind(1) == Kind.LPAREN:
            # **函数类型**：`func(u64, Vec<u8>) -> u64`
            # slice 1 只允许出现在**参数类型位置**（返回值/局部变量是函数先不做）。
            self.advance()                               # func
            self.advance()                               # (
            fn = Node("type-fn", t.line, t.col)
            params = Node("type-fn-params", t.line, t.col)
            while not self.at(Kind.RPAREN) and not self.at(Kind.EOF):
                params.add(self.parse_type())
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RPAREN, "')'")
            self.expect(Kind.ARROW, "'->'")
            fn.add(params)
            fn.add(self.parse_type())
            return fn

        if self.at(Kind.LPAREN):                        # (T, U)
            self.advance()
            node = Node("type-tuple", t.line, t.col)
            while not self.at(Kind.RPAREN) and not self.at(Kind.EOF):
                node.add(self.parse_type())
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RPAREN, "')'")
            return node

        if self.at(Kind.INT):                          # 类型实参里的常量数字：FixedVec<T, 256>
            nt = self.advance()
            return Node("type-const", nt.line, nt.col, value=nt.value)

        if self.at(Kind.IDENT) or self.at(Kind.SELF) or self.at(Kind.DYN):
            # `FixedVec<T, N>::make()` 走表达式路径，不当作类型
            if self._generic_call_ahead():
                raise ParseError("E-Parse-ExpectedType",
                                 "这里期望类型（泛型静态调用不是类型）", t)
            path = self.parse_path("类型名")
            node = Node("type-name", t.line, t.col, name=path)
            if path == "BoundedVector":
                self.error("E-Type-OldBounded",
                           "`BoundedVector[N, T]` 已下线：v0.68 起写 `FixedVec<T, N>`", t)

            # C++ 风格泛型：Vec<T> / FixedVec<T, N> / Opt<T>
            if self.at(Kind.LT) and self._is_generic_open():
                self.advance()
                args = Node("type-args", t.line, t.col)
                while not self.at(Kind.GT) and not self.at(Kind.EOF):
                    if self.at(Kind.INT):
                        nt = self.advance()
                        args.add(Node("type-const", nt.line, nt.col, value=nt.value))
                    else:
                        args.add(self.parse_type())
                    if not self.accept(Kind.COMMA):
                        break
                self.expect(Kind.GT, "'>'")
                node = node.add(args)
                # `FixedVec<T, N>::make()` —— 静态成员调用，等价于 `.make`
                if self.at(Kind.PATHSEP):
                    self.advance()
                    node.name = f"{node.name}.{self.expect_ident('成员名').text}"
                return self.parse_type_ref_suffix(node)

            if self.at(Kind.LBRACKET):                  # T[A, B]（v0.67 旧写法）
                self.advance()
                while not self.at(Kind.RBRACKET) and not self.at(Kind.EOF):
                    node.add(self.parse_type())
                    if not self.accept(Kind.COMMA):
                        break
                self.expect(Kind.RBRACKET, "']'")
                self.error("E-Type-OldBounded",
                           "`T[...]` 已下线：v0.68 起用 `Vec<T>` 或 `FixedVec<T, N>`", t)
            return self.parse_type_ref_suffix(node)

        raise ParseError("E-Parse-ExpectedType",
                         f"期望类型，实际是 {t.kind.value!r}", t)

    def parse_type_ref_suffix(self, base: Node) -> Node:
        """v0.68：`T&` —— `&` 跟在类型后，表示引用。"""
        while self.at(Kind.AMP):
            t = self.advance()
            if self.kind() == Kind.IDENT and self.peek().text == "mut":
                self.advance()
                self.error("E-Ref-OldMut",
                           "`&mut` 已下线：v0.68 起写 `T&`", t)
            ref = Node("type-ref", base.line, base.col).add(base)
            base = ref
        return base

    def _is_generic_open(self) -> bool:
        """`<` 是泛型开括号还是小于号：向前看，若在 `>` 之前先遇到 `;`/`{` 就判为比较。"""
        depth = 0
        j = 0
        while self.kind(j) != Kind.EOF:
            k = self.kind(j)
            if k == Kind.LT:
                depth += 1
            elif k == Kind.GT:
                depth -= 1
                if depth == 0:
                    return True
            elif k in (Kind.SEMI, Kind.LBRACE, Kind.RBRACE):
                return False
            j += 1
        return False

    def parse_path(self, what: str) -> str:
        t = self.peek()
        if not (self.at(Kind.IDENT) or self.at(Kind.SELF) or self.at(Kind.DYN)):
            raise ParseError("E-Parse-ExpectedPath", f"期望 {what}", t)
        parts = [self.advance().text]
        # v0.69：类型命名空间用 `::`（枚举变体、静态成员），`.` 只用于值上的成员。
        # 路径内部一律用 `.` 拼成一个名字，由后续解析阶段区分。
        while ((self.at(Kind.DOT) or self.at(Kind.PATHSEP))
               and self.kind(1) == Kind.IDENT):
            self.advance()
            parts.append(self.advance().text)
        return ".".join(parts)

    # ---------------------------------------------------------- 块与语句

    def parse_block(self) -> Node:
        t = self.expect(Kind.LBRACE, "'{'")
        node = Node("block", t.line, t.col)
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            mark = self.i
            try:
                node.add(self.parse_stmt())
            except ParseError as e:
                self.error(e.code, e.message, e.tok)
                if self.i == mark:
                    self.advance()
                self.skip_to_stmt_end()
        self.expect(Kind.RBRACE, "'}'")
        return node

    def skip_to_stmt_end(self):
        depth = 0
        while not self.at(Kind.EOF):
            k = self.kind()
            if k in (Kind.LPAREN, Kind.LBRACKET, Kind.LBRACE):
                depth += 1
            elif k in (Kind.RPAREN, Kind.RBRACKET):
                depth = max(0, depth - 1)
            elif k == Kind.RBRACE:
                if depth == 0:
                    return
                depth -= 1
            elif k == Kind.SEMI and depth == 0:
                self.advance()
                return
            self.advance()

    def parse_stmt(self) -> Node:
        t = self.peek()
        k = t.kind

        if k == Kind.VAL or k == Kind.VAR:
            return self.parse_let()
        if k == Kind.CONST:
            return self.parse_const_decl()
        if self._at_type_prefixed_decl():
            return self.parse_typed_decl()
        if k == Kind.LBRACE:
            # **裸块**：`parallel { {a} {b} }` 里每个 `{…}` 是一个**任务**。
            # 没有它就只能靠"语句"猜任务边界，B-23 的"任务数静态可见"落不了地。
            return self.parse_block()

        if k == Kind.IF:
            return self.parse_if()
        if k == Kind.WHILE:
            return self.parse_while()
        if k == Kind.PARALLEL:
            return self.parse_parallel()
        if k == Kind.FOR:
            return self.parse_for()
        if k == Kind.MATCH:
            return self.parse_match()
        if k == Kind.RETURN:
            self.advance()
            node = Node("return", t.line, t.col)
            if not self.at(Kind.SEMI):
                node.add(self.parse_expr())
            self.expect(Kind.SEMI, "';'")
            return node
        if k == Kind.SCOPE:
            self.advance()
            return self.parse_block()
        if k == Kind.UNSAFE:
            self.advance()
            return self.parse_block()

        # 赋值或表达式语句
        expr = self.parse_expr()
        if self.at(Kind.EQ):
            self.advance()
            rhs = self.parse_expr()
            self.expect(Kind.SEMI, "';'")
            self._require_place(expr, t)
            return Node("assign", t.line, t.col).add(expr, rhs)
        if self.at(Kind.PLUS_EQ) or self.at(Kind.MINUS_EQ) \
                or self.at(Kind.STAR_EQ) or self.at(Kind.SLASH_EQ):
            op = self.advance()
            rhs = self.parse_expr()
            self.expect(Kind.SEMI, "';'")
            self._require_place(expr, t)
            n = Node("assign-op", t.line, t.col, value=op.kind.value)
            return n.add(expr, rhs)
        self.expect(Kind.SEMI, "';'")
        return Node("expr-stmt", t.line, t.col).add(expr)

    PLACE_KINDS = ("path", "field", "index", "deref")

    def _require_place(self, node: Node, tok: Token):
        """赋值左侧必须是可赋值的位置（变量 / 字段 / 下标 / 解引用）。"""
        if node.kind not in self.PLACE_KINDS:
            raise ParseError("E-Assign-NotPlace",
                             f"赋值左侧不是可赋值位置（实际是 {node.kind}）", tok)

    def parse_const_decl(self) -> Node:
        """v0.68：`const Type name = expr;`（函数体内，类型前置）。"""
        t = self.expect(Kind.CONST, "'const'")
        stmt = Node("const-decl", t.line, t.col)
        stmt.value = {"mut": False}
        stmt.add(self.parse_type())
        stmt.name = self.expect_ident("变量名").text
        self.expect(Kind.EQ, "'='")
        stmt.add(self.parse_expr())
        self.expect(Kind.SEMI, "';'")
        return stmt

    def parse_typed_decl(self) -> Node:
        """v0.68：`Type name = expr;`（函数体内，类型前置、可写）。"""
        t = self.peek()
        stmt = Node("var-decl", t.line, t.col)
        stmt.value = {"mut": True}
        stmt.add(self.parse_type())
        stmt.name = self.expect_ident("变量名").text
        self.expect(Kind.EQ, "'='")
        stmt.add(self.parse_expr())
        self.expect(Kind.SEMI, "';'")
        return stmt

    def _at_type_prefixed_decl(self) -> bool:
        """判断当前语句是否是 `Type name = ...`（类型前置声明）。

        形状：类型可以带 `[`/`(`/标识符开头，但**后跟标识符**才是声明；
        同时排除明显是表达式的情况（如 `a.b`、`a(`、`a +` 等）。
        """
        k = self.kind()
        if k not in (Kind.IDENT, Kind.LBRACKET):
            return False
        # 先扫类型：第一个标识符就是类型名，跳过后再要求「标识符 =」
        j = 1
        depth = 0
        while self.kind(j) != Kind.EOF:
            kk = self.kind(j)
            if kk in (Kind.LT, Kind.LBRACKET, Kind.LPAREN):
                depth += 1
            elif kk == Kind.AMP:
                pass
            elif kk in (Kind.GT, Kind.RBRACKET, Kind.RPAREN):
                depth -= 1
                if depth < 0:
                    return False
            elif kk == Kind.SEMI:
                return False
            elif depth == 0:
                # 这里应当先遇到「变量名」，再遇到 '='
                if kk == Kind.IDENT:
                    return self.kind(j + 1) in (Kind.EQ, Kind.SEMI)
                return False
            j += 1
        return False

    def parse_let(self) -> Node:
        t = self.advance()                      # val / var（v0.68 已下线，词法层已报诊断）
        mutable = (t.kind == Kind.VAR)
        stmt = Node("var-decl" if mutable else "val-decl", t.line, t.col)
        stmt.value = {"mut": mutable}

        name = self.expect_ident("变量名").text
        stmt.name = name
        if self.accept(Kind.COLON):
            stmt.add(self.parse_type())
        self.expect(Kind.EQ, "'='")
        stmt.add(self.parse_expr())
        self.expect(Kind.SEMI, "';'")
        return stmt

    def parse_paren_cond(self, what: str) -> Node:
        """v0.68：if / while / for 的条件 MUST 带括号。"""
        if not self.at(Kind.LPAREN):
            t = self.peek()
            self.error("E-Cond-ParenRequired",
                       f"{what} 的条件 MUST 带括号：写 `{what} ( ... ) {{ }}`", t)
            return self.parse_expr()
        self.advance()
        cond = self.parse_expr()
        self.expect(Kind.RPAREN, "')'")
        return cond

    def parse_if(self) -> Node:
        t = self.expect(Kind.IF, "'if'")
        node = Node("if", t.line, t.col)
        node.add(self.parse_paren_cond("if"))
        node.add(self.parse_block())
        while self.at(Kind.ELSE):
            self.advance()
            if self.at(Kind.IF):
                node.add(self.parse_if())
                return node
            node.add(self.parse_block())
        return node

    def parse_while(self) -> Node:
        t = self.expect(Kind.WHILE, "'while'")
        node = Node("while", t.line, t.col)
        node.add(self.parse_paren_cond("while"))
        node.add(self.parse_block())
        return node

    def parse_parallel(self) -> Node:
        """`parallel { … }` —— 并发构造（**语句形态**，v0.71 先做顺序模拟）。

        **为什么先做语句不做表达式**：任务返回值类型是个未决问题（计划 §5 Q4）。
        先把**成本口径**（κ = Σ，B-24）与**禁止项**（B-25/B-26）跑通，
        语义上按顺序执行——**这样它能被检查、能被定价，而不会先发明一套返回类型。**
        """
        t = self.expect(Kind.PARALLEL, "'parallel'")
        node = Node("parallel", t.line, t.col)
        node.add(self.parse_block())
        return node

    def parse_for(self) -> Node:
        t = self.expect(Kind.FOR, "'for'")
        node = Node("for", t.line, t.col)
        if self.at(Kind.LPAREN):
            self.advance()
            v = self.expect_ident("循环变量").text
            node.name = v
            self.expect(Kind.IN, "'in'")
            node.add(self.parse_expr())
            self.expect(Kind.RPAREN, "')'")
        else:
            self.error("E-Cond-ParenRequired",
                       "for 的条件 MUST 带括号：写 `for (x in xs) { }`", t)
            v = self.expect_ident("循环变量").text
            node.name = v
            self.expect(Kind.IN, "'in'")
            node.add(self.parse_expr())
        node.add(self.parse_block())
        return node

    def parse_match(self) -> Node:
        t = self.expect(Kind.MATCH, "'match'")
        node = Node("match", t.line, t.col)
        node.add(self.parse_expr())
        self.expect(Kind.LBRACE, "'{'")
        while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
            node.add(self.parse_arm())
            if not self.accept(Kind.COMMA):
                pass
        self.expect(Kind.RBRACE, "'}'")
        return node

    def parse_arm(self) -> Node:
        t = self.peek()
        pat = self.parse_pattern()
        self.expect(Kind.FATARROW, "'=>'")
        arm = Node("arm", t.line, t.col).add(pat)

        if self.at(Kind.LBRACE):
            arm.add(self.parse_block())
            return arm

        # 单语句臂：终止符是 ',' 或 ';'（二者都可，',' 由外层消费）
        if self.at(Kind.RETURN):
            rt = self.advance()
            r = Node("return", rt.line, rt.col)
            if not (self.at(Kind.SEMI) or self.at(Kind.COMMA) or self.at(Kind.RBRACE)):
                r.add(self.parse_expr())
            self.accept(Kind.SEMI)
            arm.add(r)
            return arm

        e = self.parse_expr()
        self.accept(Kind.SEMI)
        arm.add(Node("expr-stmt", t.line, t.col).add(e))
        return arm

    def parse_pattern(self) -> Node:
        t = self.peek()
        if self.at(Kind.UNDERSCORE) if hasattr(Kind, "UNDERSCORE") else False:
            self.advance()
            return Node("pattern-wildcard", t.line, t.col)
        # `_` 在词法里可能是 IDENT
        if self.at(Kind.IDENT) and self.peek().text == "_":
            self.advance()
            return Node("pattern-wildcard", t.line, t.col)
        path = self.parse_path("模式")
        node = Node("pattern", t.line, t.col, name=path)
        if self.accept(Kind.LPAREN):
            while not self.at(Kind.RPAREN) and not self.at(Kind.EOF):
                if self.kind() == Kind.IDENT and self.kind(1) in (Kind.COMMA, Kind.RPAREN):
                    bt = self.advance()
                    node.add(Node("binding", bt.line, bt.col, name=bt.text))
                elif self.kind() == Kind.IDENT and self.kind(1) == Kind.COLON:
                    bt = self.advance()
                    self.advance()
                    node.add(Node("binding", bt.line, bt.col, name=bt.text).add(self.parse_type()))
                else:
                    node.add(self.parse_pattern())
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RPAREN, "')'")
        return node

    # ---------------------------------------------------------- 表达式

    def parse_expr(self, min_bp: int = 0) -> Node:
        left = self.parse_prefix()
        left = self.parse_postfix(left)

        while True:
            k = self.kind()
            if k not in BINARY:
                break
            l_bp, r_bp = BINARY[k]
            if l_bp < min_bp:
                break
            op = self.advance()
            right = self.parse_expr(r_bp)
            left = Node("binary", op.line, op.col, value=op.kind.value).add(left, right)

        return left

    def parse_prefix(self) -> Node:
        t = self.peek()
        k = t.kind

        if k == Kind.NOT:
            self.advance()
            return Node("unary", t.line, t.col, value="not").add(self.parse_expr(15))
        if k == Kind.MINUS:
            self.advance()
            return Node("unary", t.line, t.col, value="-").add(self.parse_expr(15))
        if k == Kind.STAR:
            self.advance()
            return Node("deref", t.line, t.col).add(self.parse_expr(15))
        if k == Kind.AMP:
            self.advance()
            node = Node("ref", t.line, t.col)
            if self.kind() == Kind.IDENT and self.peek().text == "mut":
                self.advance()
                node.value = "mut"
            node.add(self.parse_expr(15))
            return node

        if k == Kind.INT:
            self.advance()
            return Node("lit-int", t.line, t.col, value=t.text)
        if k == Kind.FLOAT:
            self.advance()
            return Node("lit-float", t.line, t.col, value=t.text)
        if k == Kind.CHAR:
            self.advance()
            return Node("lit-char", t.line, t.col, value=t.text)
        if k == Kind.STRING:
            self.advance()
            return Node("lit-string", t.line, t.col, value=t.value)   # 解码后的字节
        if k == Kind.TRUE or k == Kind.FALSE:
            self.advance()
            return Node("lit-bool", t.line, t.col, value=(k == Kind.TRUE))

        if k == Kind.QUESTION_BANG:
            # ?!label(args) —— 决策点；'?!' 是单个 token（见词法最长匹配）
            self.advance()
            if not self.at(Kind.IDENT):
                raise ParseError("E-Choose-NoLabel",
                                 "'?!' 后必须紧跟标签名", self.peek())
            label = self.advance().text
            node = Node("dispatch", t.line, t.col, name=label)
            self.expect(Kind.LPAREN, "'('")
            node.add(self.parse_args_until(Kind.RPAREN))
            self.expect(Kind.RPAREN, "')'")
            return node

        if k == Kind.LPAREN:
            self.advance()
            first = self.parse_expr()
            if self.at(Kind.COMMA):
                node = Node("tuple", t.line, t.col).add(first)
                while self.accept(Kind.COMMA):
                    if self.at(Kind.RPAREN):
                        break
                    node.add(self.parse_expr())
                self.expect(Kind.RPAREN, "')'")
                return node
            self.expect(Kind.RPAREN, "')'")
            return Node("paren", t.line, t.col).add(first)

        if k == Kind.LBRACKET:
            self.advance()
            node = Node("array", t.line, t.col)
            while not self.at(Kind.RBRACKET) and not self.at(Kind.EOF):
                node.add(self.parse_expr())
                if not self.accept(Kind.COMMA):
                    break
            self.expect(Kind.RBRACKET, "']'")
            return node

        if k in (Kind.IDENT, Kind.SELF, Kind.DYN):
            # 表达式里只取第一段；后续 `.name` / `.name(...)` 交给 postfix，
            # 否则 `text.at(i)` 会被整体吃成路径，方法调用就看不见了。
            t0 = self.advance()
            node = Node("path", t0.line, t0.col, name=t0.text)
            # v0.68：`FixedVec<T, N>::make()` —— 泛型类型实参先挂上来
            if self.at(Kind.LT) and self._generic_tail(0):
                self.advance()
                args = Node("type-args", t0.line, t0.col)
                depth = 1
                while self.kind() != Kind.EOF:
                    if self.at(Kind.LT):
                        depth += 1
                    elif self.at(Kind.GT):
                        depth -= 1
                        if depth == 0:
                            self.advance()
                            break
                    elif depth == 1 and self.at(Kind.COMMA):
                        self.advance()
                        continue
                    else:
                        args.add(self.parse_type() if not self.at(Kind.INT)
                                 else Node("type-const", self.peek().line,
                                           self.peek().col,
                                           value=self.advance().value))
                        continue
                    self.advance()
                node = Node("type-name", t0.line, t0.col, name=t0.text).add(args)
            return node

        raise ParseError("E-Parse-ExpectedExpr",
                         f"期望表达式，实际是 {k.value!r}", t)

    def parse_postfix(self, left: Node) -> Node:
        while True:
            # `Type { field: expr, ... }` —— 结构体字面量（文法 primary 里的 field-init 形式）
            # 判定用三重前瞻：`路径` + `{` + `ident` + `:`。
            # 只靠 `{` 会把 `if (n > 0) {` 的条件误吃进来——那里 postfix 也会看到 `{`。
            if (self.at(Kind.LBRACE) and left.kind == "path"
                    and self.kind(1) == Kind.IDENT and self.kind(2) == Kind.COLON):
                self.advance()
                node = Node("struct-literal", left.line, left.col, name=left.name)
                while not self.at(Kind.RBRACE) and not self.at(Kind.EOF):
                    ft = self.expect_ident("字段名")
                    self.expect(Kind.COLON, "':'")
                    node.add(Node("field-init", ft.line, ft.col, name=ft.text)
                             .add(self.parse_expr()))
                    if not self.accept(Kind.COMMA):
                        break
                self.expect(Kind.RBRACE, "'}'")
                left = node
                continue

            t = self.peek()

            if self.at(Kind.DOT):
                self.advance()
                # 字段访问 或 方法调用
                if self.at(Kind.INT):                  # 元组下标 .0 / .1
                    it = self.advance()
                    left = Node("field", t.line, t.col, value=it.text).add(left)
                    continue
                name = self.expect_ident("字段名或方法名").text
                if self.at(Kind.LPAREN):
                    self.advance()
                    call = Node("method-call", t.line, t.col, name=name).add(left)
                    call.add(self.parse_args_until(Kind.RPAREN))
                    self.expect(Kind.RPAREN, "')'")
                    left = call
                else:
                    left = Node("field", t.line, t.col, name=name).add(left)
                continue

            if self.at(Kind.PATHSEP):
                # `Type::make(...)`：静态成员调用
                self.advance()
                name = self.expect_ident("静态成员名").text
                if left.kind == "path":
                    left = Node("path", left.line, left.col,
                                name=f"{left.name}.{name}")
                else:
                    left = Node("field", t.line, t.col, name=name).add(left)
                continue

            if self.at(Kind.LT) and left.kind in ("path", "type-name") \
                    and (left.children or self._generic_call_ahead()):
                # `FixedVec<T, N>::make(...)`：泛型静态调用（只在 `::` 前提下成立）
                self.advance()
                while not self.at(Kind.GT) and not self.at(Kind.EOF):
                    self.advance()
                self.expect(Kind.GT, "'>'")
                continue

            if self.at(Kind.LPAREN):
                self.advance()
                call = Node("call", t.line, t.col, name=left.name if left.kind == "path" else None)
                call.add(left)
                # `range(起, 止, 步长)`：步长写成 `*k` / `+k`，只在 range 里合法（v0.69）
                if left.kind == "path" and (left.name or "").split(".")[-1] == "range":
                    call.add(self.parse_range_args())
                else:
                    call.add(self.parse_args_until(Kind.RPAREN))
                self.expect(Kind.RPAREN, "')'")
                left = call
                continue

            if self.at(Kind.LBRACKET):
                self.advance()
                idx = self.parse_expr()
                self.expect(Kind.RBRACKET, "']'")
                left = Node("index", t.line, t.col).add(left, idx)
                continue

            if self.at(Kind.QUESTION):
                self.advance()
                left = Node("try", t.line, t.col).add(left)
                continue

            if self.at(Kind.AS):
                self.advance()
                left = Node("cast", t.line, t.col).add(left, self.parse_type())
                continue

            return left

    def parse_range_args(self) -> Node:
        """`range(起, 止)` 或 `range(起, 止, *k / +k)`。

        步长不是普通表达式（`*2` 不是合法表达式开头），所以单独解析。
        语义约束（严格递增、点数 ≤ 64）由 checker 校验。
        """
        args = Node("args")
        while not self.at(Kind.RPAREN) and not self.at(Kind.EOF):
            if self.at(Kind.STAR) or self.at(Kind.PLUS) or self.at(Kind.MINUS):
                op = self.advance()
                node = Node("step", op.line, op.col, value=op.text)
                node.add(self.parse_expr())
                args.add(node)
            else:
                args.add(self.parse_expr())
            if not self.accept(Kind.COMMA):
                break
        return args

    def parse_args_until(self, close: Kind) -> Node:
        args = Node("args")
        while not self.at(close) and not self.at(Kind.EOF):
            if self.kind() == Kind.IDENT and self.kind(1) == Kind.EQ:
                at_ = self.advance()
                self.advance()
                args.add(Node("named-arg", at_.line, at_.col, name=at_.text).add(self.parse_expr()))
            else:
                args.add(self.parse_expr())
            if not self.accept(Kind.COMMA):
                break
        return args

    # ---------------------------------------------------------- 终止符

    def expect_end(self, kind: str, required: bool = True):
        """v0.68：`end <kind>` 已下线，块只用 `}` 收尾。

        为了给出精确诊断，仍然识别 `end`：遇到就报 `E-End-Removed`，
        并把后面的种类名一起吃掉（避免连环误报）。
        """
        if not self.at(Kind.END):
            return
        t = self.advance()
        self.error("E-End-Removed",
                   f"`end` 已下线：v0.68 起块只用 `}}` 收尾（这里是 {kind}）", t)
        kt = self.peek()
        if kt.kind == Kind.IDENT or kt.text in END_KIND:
            self.advance()


# ---------------------------------------------------------------- 入口

def parse_source(src: str, src_name: str = "<input>") -> ParseResult:
    lex = Lexer(src).run()
    toks = list(lex.tokens)
    if not toks or toks[-1].kind != Kind.EOF:
        toks.append(Token(Kind.EOF, "", 1, 1, len(src), len(src)))

    p = Parser(toks, src_name)
    mod = p.parse_module()

    diags: List[Diagnostic] = [
        Diagnostic(code=d.code, message=d.message, line=d.line, col=d.col,
                   start=d.start, end=d.end)
        for d in lex.diagnostics
    ]
    diags.extend(p.diags)
    return ParseResult(module=mod, diagnostics=diags, tokens=toks, soft=p.soft)


def parse_file(path: str) -> ParseResult:
    with open(path, "r", encoding="utf-8") as f:
        return parse_source(f.read(), path)


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    as_json = "--json" in argv
    files = [a for a in argv[1:] if not a.startswith("--")]

    rc = 0
    for path in files:
        res = parse_file(path)
        if as_json:
            print(json.dumps({
                "file": path,
                "ok": res.ok,
                "diagnostics": [d.to_dict() for d in res.diagnostics],
                "ast": res.module.to_dict() if res.module else None,
            }, ensure_ascii=False, indent=2))
        else:
            print(f"=== {path} ===")
            print(res.module.dump() if res.module else "(no module)")
            if res.diagnostics:
                print(f"-- {len(res.diagnostics)} 条诊断 --")
                for d in res.diagnostics:
                    print(f"  {d.code} @{d.line}:{d.col}  {d.message}")
        if res.diagnostics:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv))
