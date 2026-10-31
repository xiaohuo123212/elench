#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
highlight.py —— 语法高亮（终端 ANSI + 供编辑器用的 token 分类）

两条硬约束
----------
1. **不改文本**：`strip_ansi(highlight(src)) == src` 恒成立。
   高亮只插入转义序列，绝不增删字符——否则复制粘贴会坏。
2. **不猜**：颜色只由词法器给出的 token 种类决定，不做"看到一个词像类型就染蓝"这种启发式。

用法
----
  dsh highlight <文件>            输出带 ANSI 的文本
  dsh highlight <文件> --tokens   输出 (种类, 文本) 列表，供编辑器插件使用
"""

from __future__ import annotations

import re
from typing import List, Tuple

from lexer import Kind, Lexer, Token

RESET = "\x1b[0m"

# 前景色 + 样式
C = {
    "keyword":   "\x1b[1;35m",   # 亮紫：关键字
    "comment":   "\x1b[2;37m",   # 暗灰：注释
    "char":      "\x1b[32m",     # 绿：字符字面量
    "string":    "\x1b[32m",
    "number":    "\x1b[36m",     # 青：数字
    "operator":  "\x1b[33m",     # 黄：运算符
    "punct":     "",             # 标点不染色（染色反而吵）
    "ident":     "\x1b[0m",      # 默认：标识符
    "dispatch":  "\x1b[1;31m",   # 亮红：?! 决策点（语言的核心记号）
    "special":   "\x1b[1;36m",   # 亮青：⊖ 等特殊记号
    "error":     "\x1b[1;41m",   # 红底：词法错误
    "ctx":       "\x1b[35m",     # 品红：上下文关键字（词法上是 ident）
}

KEYWORD_KINDS = {
    Kind.FN, Kind.STRUCT, Kind.ENUM, Kind.TRAIT, Kind.IMPL, Kind.SPEC,
    Kind.CHOOSE, Kind.REWRITE, Kind.USE, Kind.PUB, Kind.CONST, Kind.VAL,
    Kind.VAR, Kind.IF, Kind.ELSE, Kind.FOR, Kind.WHILE, Kind.MATCH,
    Kind.RETURN, Kind.SCOPE, Kind.SPAWN, Kind.UNSAFE, Kind.ASM,
    Kind.EXTERN, Kind.KERNELS, Kind.END, Kind.ERROR_DECL, Kind.IN, Kind.AND, Kind.OR,
    Kind.NOT, Kind.AS, Kind.TRUE, Kind.FALSE, Kind.SELF, Kind.DYN,
}

OPERATOR_KINDS = {
    Kind.PIPE_GT, Kind.RANGE, Kind.ARROW, Kind.FATARROW, Kind.LE, Kind.GE,
    Kind.EQEQ, Kind.NE, Kind.PLUS_EQ, Kind.MINUS_EQ, Kind.STAR_EQ,
    Kind.SLASH_EQ, Kind.PLUS, Kind.MINUS, Kind.STAR, Kind.SLASH,
    Kind.PERCENT, Kind.LT, Kind.GT, Kind.EQ, Kind.AMP, Kind.PIPE,
    Kind.BANG, Kind.QUESTION,
}

# 上下文关键字：词法器把它们当 ident，但语义上是关键字
CONTEXTUAL = {
    "effects", "requires", "guarantees", "benchmodel", "target", "sizes",
    "repeats", "recursion", "from", "over", "pending",
    "data", "scale", "seed", "noinline",
    "objective", "min", "max", "exact", "pure", "pending",
    "alloc", "io", "atomic", "panic", "unwind", "unsafe", "diverge",
    "spawn", "unknown",
    "objective",
}

PUNCT_KINDS = {
    Kind.LPAREN, Kind.RPAREN, Kind.LBRACE, Kind.RBRACE, Kind.LBRACKET,
    Kind.RBRACKET, Kind.COMMA, Kind.SEMI, Kind.COLON, Kind.DOT,
}


def category(kind: Kind, text: str = "") -> str:
    if kind == Kind.QUESTION_BANG:
        return "dispatch"
    if kind == Kind.OMINUS:
        return "special"
    if kind in KEYWORD_KINDS:
        return "keyword"
    if kind == Kind.IDENT and text in CONTEXTUAL:
        return "ctx"
    if kind in OPERATOR_KINDS:
        return "operator"
    if kind in PUNCT_KINDS:
        return "punct"
    if kind == Kind.CHAR:
        return "char"
    if kind == Kind.STRING:
        return "string"
    if kind in (Kind.INT, Kind.FLOAT):
        return "number"
    if kind == Kind.IDENT:
        return "ident"
    if kind == Kind.ERROR:
        return "error"
    return "ident"


def token_spans(src: str) -> List[Tuple[int, int, str]]:
    """把 src 切成 (start, end, 类别) 的有序切片，覆盖整个文本。"""
    lex = Lexer(src).run()
    pieces: List[Tuple[int, int, str]] = []

    for t in lex.tokens:
        if t.kind == Kind.EOF:
            continue
        pieces.append((t.start, t.end, category(t.kind, t.text)))
    for t in lex.trivia:
        pieces.append((t.start, t.end, "comment"))

    pieces.sort()
    # 补上 token 之间的空白，保证切片连续覆盖
    out: List[Tuple[int, int, str]] = []
    pos = 0
    for s, e, cat in pieces:
        if s > pos:
            out.append((pos, s, "plain"))
        if e > s:
            out.append((s, e, cat))
        pos = max(pos, e)
    if pos < len(src):
        out.append((pos, len(src), "plain"))
    return out


def highlight(src: str, color: bool = True) -> str:
    if not color:
        return src
    buf: List[str] = []
    for s, e, cat in token_spans(src):
        text = src[s:e]
        code = C.get(cat)
        if code and cat != "ident":
            buf.append(code + text + RESET)
        else:
            buf.append(text)
    return "".join(buf)


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def tokens_of(src: str) -> List[Tuple[str, str]]:
    """(类别, 文本)，跳过纯空白，供编辑器/测试使用。"""
    return [(cat, src[s:e]) for s, e, cat in token_spans(src) if cat != "plain"]
