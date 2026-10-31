#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
formatter.py —— 最小重排格式化器（M0）

设计要点
--------
1. **token 级**，不走 AST：注释是 trivia，AST 里没有位置，走 AST 必然丢注释。
2. **块 vs 内联花括号**：靠「配对的 `}` 后面是不是 `end`」判定（C5 保证块都有 `end`）。
   块 → 换行缩进；内联（如 `{ max: O(1) }`）→ 同一行。
3. **幂等是构造性的**：布局只由 token 序列与括号嵌套决定，与源码换行无关；
   fmt 不增删 token（只动空白），re-lex 出的 token 序列恒等 → 第二次必然同一结果。
4. **宽度**：一行超过 MAX_LINE 时，在最内层含逗号的 `(` / `[` 组按逗号折行。

不做什么
--------
· 不重排 token 顺序（"一种写法"靠 R2 + checker，不靠 fmt 改写）
· 词法有错时原样返回，避免把坏文件改得更坏
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import List, Optional, Tuple

from lexer import Kind, Lexer, Token

INDENT = " " * 4
MAX_LINE = 100

NO_SPACE_BEFORE = {
    Kind.COMMA, Kind.SEMI, Kind.RPAREN, Kind.RBRACKET,
    Kind.DOT, Kind.COLON, Kind.QUESTION, Kind.PATHSEP,
}
NO_SPACE_AFTER = {
    Kind.LPAREN, Kind.LBRACKET, Kind.DOT, Kind.QUESTION_BANG, Kind.PATHSEP,
}
SPACED = {
    Kind.PLUS, Kind.MINUS, Kind.STAR, Kind.SLASH, Kind.PERCENT, Kind.OMINUS,
    Kind.EQ, Kind.EQEQ, Kind.NE, Kind.LT, Kind.LE, Kind.GT, Kind.GE,
    Kind.ARROW, Kind.FATARROW, Kind.PIPE, Kind.PIPE_GT, Kind.RANGE,
    Kind.PLUS_EQ, Kind.MINUS_EQ, Kind.STAR_EQ, Kind.SLASH_EQ,
}
LINE_LEADING = {
    Kind.FN, Kind.SPEC, Kind.CHOOSE, Kind.STRUCT, Kind.ENUM,
    Kind.TRAIT, Kind.IMPL, Kind.CONST, Kind.USE, Kind.REWRITE, Kind.EXTERN,
    Kind.VAL, Kind.VAR, Kind.RETURN, Kind.IF, Kind.WHILE, Kind.FOR,
    Kind.MATCH, Kind.SCOPE, Kind.SPAWN, Kind.UNSAFE, Kind.ASM, Kind.PUB,
    Kind.ERROR_DECL,
}
# `kernels` 不是行首词：它接在 fn 体的 `}` 后面（`} kernels L(...) {`）
PREFIX = {Kind.STAR, Kind.AMP, Kind.MINUS, Kind.BANG, Kind.NOT}

OPENERS = {Kind.LPAREN: Kind.RPAREN, Kind.LBRACKET: Kind.RBRACKET}
CLOSERS = {Kind.RPAREN, Kind.RBRACKET}

_END_NAMES = {
    "fn", "spec", "choose", "struct", "enum", "trait", "impl",
    "if", "while", "for", "match", "rewrite", "scope",
}


@dataclass
class Piece:
    kind: Kind
    text: str
    line: int
    col: int
    start: int
    end: int
    is_comment: bool = False
    dead: bool = False          # 已被消费（如 end 后的种类名）


def _pieces(src: str) -> Tuple[List[Piece], list]:
    lex = Lexer(src).run()
    items: List[Piece] = []
    for t in lex.tokens:
        if t.kind == Kind.EOF:
            continue
        items.append(Piece(t.kind, t.text, t.line, t.col, t.start, t.end))
    for t in lex.trivia:
        items.append(Piece(t.kind, t.text, t.line, t.col, t.start, t.end, True))
    items.sort(key=lambda p: p.start)
    return items, lex.diagnostics


def _live(items: List[Piece]) -> List[Piece]:
    return [p for p in items if not p.dead]


def _is_prefix(items: List[Piece], k: int) -> bool:
    if k < 0 or items[k].dead or items[k].kind not in PREFIX:
        return False
    pi = _prev_live(items, k)
    if pi < 0:
        return True
    prev = items[pi]
    return (prev.kind in SPACED or prev.kind in LINE_LEADING
            or prev.kind in (Kind.LPAREN, Kind.LBRACKET, Kind.COMMA,
                             Kind.LBRACE, Kind.SEMI, Kind.COLON,
                             Kind.FATARROW, Kind.ARROW, Kind.EQ)
            or prev.text in ("and", "or", "not"))


def _prev_live(items: List[Piece], k: int) -> int:
    """k 之前最近的一个"活"token 下标（跳过 dead 与注释）；没有返回 -1。"""
    j = k - 1
    while j >= 0:
        if not items[j].dead and not items[j].is_comment:
            return j
        j -= 1
    return -1


def _looks_generic(items: List[Piece], k: int) -> bool:
    """`A < B` 是泛型还是比较？只有「两侧都是标识符，且前面紧跟类型位置」才算泛型。"""
    j = k
    if j >= len(items) or items[j].kind not in (Kind.IDENT, Kind.INT):
        return False
    # 向后找配对的 `>`
    depth = 0
    while j < len(items):
        kk = items[j].kind
        if kk == Kind.LT:
            depth += 1
        elif kk == Kind.GT:
            depth -= 1
            if depth == 0:
                return True
        elif kk in (Kind.SEMI, Kind.LBRACE, Kind.RBRACE, Kind.RPAREN, Kind.LBRACKET):
            return False
        j += 1
    return False


def _prev_is_generic_open(items: List[Piece], k: int) -> bool:
    """`<` 是泛型开括号吗？往回看：标识符后紧跟 `<` 且后方有成对 `>` 才算。"""
    pi = _prev_live(items, k)
    if pi < 0 or items[pi].kind != Kind.LT:
        return False
    return _looks_generic(items, pi)


def _prev_is_generic_close(items: List[Piece], k: int) -> bool:
    """`>` 是泛型收尾还是比较号：往前找配对的 `<`，且其间没有分号/花括号。"""
    j = _prev_live(items, k)
    depth = 0
    while j >= 0:
        kk = items[j].kind
        if kk == Kind.GT:
            depth += 1
        elif kk == Kind.LT:
            depth -= 1
            if depth == 0:
                return True
        elif kk in (Kind.SEMI, Kind.LBRACE, Kind.RBRACE):
            return False
        j -= 1
    return False


def _space_between(items: List[Piece], k: int) -> bool:
    if k == 0:
        return False
    cur = items[k]
    pi = _prev_live(items, k)
    if pi < 0:
        return False
    prev = items[pi]
    if cur.is_comment or prev.is_comment:
        return True
    if cur.kind in NO_SPACE_BEFORE:
        return False
    if prev.kind in NO_SPACE_AFTER:
        return False
    # 泛型 `Vec<u8>`：`<` 紧贴类型名（仅当两侧都是类型标识符、且不构成比较时）
    if cur.kind == Kind.LT and prev.kind == Kind.IDENT and _looks_generic(items, k):
        return False
    # 比较号 `<`：两侧都留空格
    if cur.kind == Kind.LT:
        return True
    # 比较号 `>` 之后也要空格
    if cur.kind not in (Kind.SEMI, Kind.COMMA, Kind.RPAREN) and prev.kind == Kind.GT \
            and not _prev_is_generic_close(items, k):
        return True
    if cur.kind == Kind.GT and _prev_is_generic_close(items, k):
        return False
    # `<` 之后：泛型内不留空格，比较号留空格
    if prev.kind == Kind.LT and not _prev_is_generic_open(items, k):
        return True
    # `T!`：表示可能失败，紧贴返回类型
    if cur.kind == Kind.BANG and prev.kind in (Kind.IDENT, Kind.GT, Kind.RBRACKET, Kind.RPAREN):
        return False
    if prev.kind == Kind.LT and _prev_is_generic_open(items, k):
        return False
    # `T&`：`&` 紧贴类型（C++ 写法）
    if cur.kind == Kind.AMP and prev.kind in (Kind.IDENT, Kind.GT, Kind.RBRACKET):
        return False
    if cur.kind == Kind.IDENT and prev.kind == Kind.AMP:
        return True
    if cur.kind in (Kind.LPAREN, Kind.LBRACKET):
        return not (prev.kind == Kind.IDENT
                    or prev.kind in (Kind.RPAREN, Kind.RBRACKET))
    if cur.kind == Kind.LBRACE:
        return True
    if prev.kind == Kind.LBRACE:
        return True        # 内联 `{ x }`；块花括号不会走到这里（会换行）
    # 切片类型 `[]u8`：`]` 与类型名之间不留空格
    if cur.kind == Kind.IDENT and prev.kind == Kind.RBRACKET:
        return False
    # 前缀运算符必须在 SPACED 之前判，否则 `*xs` 会被当成乘法
    if _is_prefix(items, pi) and prev.kind != Kind.NOT:
        return False
    if _is_prefix(items, k) and cur.kind != Kind.NOT:
        return True
    if prev.kind in SPACED or cur.kind in SPACED:
        return True
    return True


def _one_line(text: str) -> str:
    return text.replace("\r", "").rstrip()


class Formatter:
    def __init__(self, items: List[Piece]):
        self.items = items
        self.out: List[str] = []
        self.cur = ""
        self.depth = 0
        self.groups: List[dict] = []      # 未闭合的 ( / [ 组
        self.brace: List[bool] = []       # True = 内联花括号
        self._block_cache: dict = {}

    # ---- 结构判定 ----
    def _match_of(self, i: int, open_k: Kind, close_k: Kind) -> int:
        depth = 0
        for j in range(i, len(self.items)):
            kj = self.items[j].kind
            if kj == open_k:
                depth += 1
            elif kj == close_k:
                depth -= 1
                if depth == 0:
                    return j
        return len(self.items) - 1

    def _brace_is_block(self, i: int) -> bool:
        """块花括号（换行缩进）vs 内联花括号（同一行）。

        判据：看 `{` 前一个 token。
          `:` / `,` / `{` / `effects` 之后 → 内联（成本槽位、效果集）
          其余（签名、`target`、`model`、`if`、`match`、`=>` …）→ 块
        """
        if i in self._block_cache:
            return self._block_cache[i]
        prev = None
        k = i - 1
        while k >= 0:
            if not self.items[k].dead and not self.items[k].is_comment:
                prev = self.items[k]
                break
            k -= 1

        inline = False
        if prev is not None:
            if prev.kind in (Kind.COLON, Kind.COMMA, Kind.LBRACE):
                inline = True
            elif prev.text == "effects":
                inline = True
            elif prev.kind in (Kind.IDENT, Kind.GT):
                # `Type { field: expr, ... }` —— 结构体字面量，MUST 内联
                # 判据：`{` 后面紧跟 `ident :`（块不会以这个形状开头）
                nxt = [x for x in self.items[i + 1:i + 5]
                       if not x.dead and not x.is_comment]
                if (len(nxt) >= 2 and nxt[0].kind == Kind.IDENT
                        and nxt[1].kind == Kind.COLON):
                    inline = True
        self._block_cache[i] = not inline
        return not inline

    # ---- 输出原语 ----
    def _flush(self):
        if self.cur.strip():
            self.out.append(self.cur.rstrip())
        self.cur = ""

    def _emit(self, text: str, space: bool):
        if not self.cur:
            self.cur = INDENT * self.depth
        elif space:
            self.cur += " "
        self.cur += text

    def _flat_len(self, i: int, j: int) -> int:
        n = 0
        for k in range(i, j + 1):
            if k > i and _space_between(self.items, k):
                n += 1
            n += len(self.items[k].text)
        return n

    def _top_commas(self, i: int, j: int) -> List[int]:
        open_k = self.items[i].kind
        close_k = OPENERS[open_k]
        depth = 0
        res = []
        for k in range(i, j + 1):
            kk = self.items[k].kind
            if kk == open_k:
                depth += 1
            elif kk == close_k:
                depth -= 1
            elif kk == Kind.COMMA and depth == 1:
                res.append(k)
        return res

    # ---- 主循环 ----
    def run(self) -> str:
        items = self.items
        n = len(items)
        i = 0
        prev_src_line = 0

        while i < n:
            p = items[i]
            if p.dead:
                i += 1
                continue

            # 空行（源码里跨了空行且当前在行首）
            if p.line - prev_src_line >= 2 and not self.cur.strip() and self.out:
                if self.out[-1].strip():
                    self.out.append("")
            prev_src_line = max(prev_src_line, p.line)

            if p.is_comment:
                self._emit(_one_line(p.text), bool(self.cur.strip()))
                self._flush()
                i += 1
                continue

            k = p.kind

            if k == Kind.LBRACE:
                if self._brace_is_block(i):
                    self._emit("{", True)
                    self._flush()
                    self.depth += 1
                    self.brace.append(False)
                else:
                    self._emit("{", True)
                    self.brace.append(True)
                i += 1
                continue

            if k == Kind.RBRACE:
                inline = self.brace.pop() if self.brace else False
                if inline:
                    self._emit("}", True)
                    i += 1
                    continue
                if self.cur.strip():
                    self._flush()
                self.depth = max(0, self.depth - 1)
                self._emit("}", False)
                nxt = items[i + 1] if i + 1 < n else None
                # `} end X` / `} else` / `} kernels L(...)` 留在同一行，其余换行
                # v0.68：`end` 已下线，只保留 `} else` / `} kernels` 同行
                same_line = (nxt is not None and
                             (nxt.kind in (Kind.END, Kind.ELSE, Kind.KERNELS)))
                if not same_line:
                    self._flush()
                i += 1
                continue

            if k == Kind.END:
                name = ""
                if i + 1 < n and not items[i + 1].dead and \
                        (items[i + 1].kind == Kind.IDENT
                         or items[i + 1].text in _END_NAMES):
                    name = " " + items[i + 1].text
                    items[i + 1].dead = True
                    i += 1
                self._emit("end" + name, True)
                self._flush()
                i += 1
                continue

            if k == Kind.SEMI:
                self._emit(";", False)
                self._flush()
                i += 1
                continue

            if k == Kind.COMMA:
                self._emit(",", False)
                if self.groups and self.groups[-1]["wrapped"]:
                    self._flush()
                elif not self.groups and self.brace and self.brace[-1] is False:
                    # 块花括号顶层的逗号（enum 变体、struct 字段、target 维度）
                    self._flush()
                i += 1
                continue

            if k in OPENERS:
                i = self._open_group(i)
                continue

            if k in CLOSERS:
                wrapped = bool(self.groups) and self.groups[-1]["wrapped"]
                if wrapped:
                    self._flush()
                    self.depth = max(0, self.depth - 1)
                self._emit(p.text, False)
                if self.groups:
                    self.groups.pop()
                if wrapped:
                    self._flush()
                i += 1
                continue

            if k in LINE_LEADING and self.cur.strip() and self.depth == 0:
                prev = None
                j = i - 1
                while j >= 0:
                    if not items[j].dead and not items[j].is_comment:
                        prev = items[j]
                        break
                    j -= 1
                # `pub fn` / `kernel fn` 是同一条声明的两个词，不能断行
                if not (prev is not None and prev.text in ("pub", "kernel")):
                    self._flush()

            self._emit(p.text, _space_between(items, i))
            i += 1

        self._flush()
        return self._postprocess()

    # ---- 组与折行 ----
    def _open_group(self, i: int) -> int:
        items = self.items
        open_k = items[i].kind
        close_k = OPENERS[open_k]
        j = self._match_of(i, open_k, close_k)

        col = len(self.cur) + (1 if _space_between(items, i) else 0)
        flat = self._flat_len(i, j)
        commas = self._top_commas(i, j)

        if col + flat > MAX_LINE and commas:
            # 折行：`(` 后换行，逗号后换行，`)` 前换行
            self._emit(items[i].text, _space_between(items, i))
            self._flush()
            self.depth += 1
            self.groups.append({"open": i, "close": j, "wrapped": True})
            return i + 1

        self._emit(items[i].text, _space_between(items, i))
        self.groups.append({"open": i, "close": j, "wrapped": False})
        return i + 1

    # ---- 收尾 ----
    def _postprocess(self) -> str:
        lines = [ln.rstrip() for ln in self.out]
        # 折行的续行：以上一行最后一个 `(` 的列位置对齐
        fixed: List[str] = []
        for ln in lines:
            if fixed and fixed[-1].rstrip().endswith(("(", ",")) and ln and not ln.startswith(" "):
                prev = fixed[-1]
                col = prev.rfind("(")
                if col >= 0 and prev.rstrip().endswith("("):
                    ln = " " * (col + 1) + ln.strip()
                elif prev.rstrip().endswith(","):
                    ln = prev[:len(prev) - len(prev.lstrip())] + "    " + ln.strip()
            fixed.append(ln)
        lines = fixed
        # 超过 MAX_LINE 的函数签名：在最后一个逗号后折行
        wrap: List[str] = []
        for ln in lines:
            if len(ln) > MAX_LINE and ln.rstrip().endswith("{") and "(" in ln:
                idx = ln.rfind(", ", 0, ln.rfind(")"))
                if idx > 0 and idx < MAX_LINE:
                    head = ln[:idx + 1]
                    tail = ln[idx + 2:]
                    indent = " " * (head.rfind("(") + 1)
                    wrap.append(head)
                    wrap.append(indent + tail)
                    continue
            wrap.append(ln)
        lines = wrap
        res: List[str] = []
        for ln in lines:
            if not ln.strip():
                if res and res[-1].strip():
                    res.append("")
                continue
            res.append(ln)
        while res and not res[-1].strip():
            res.pop()
        return "\n".join(res) + "\n"


# ---------------------------------------------------------------- 入口

def _strip_cond_parens(items: List[Piece]) -> None:
    """去掉条件外面那层冗余圆括号（R2：一种写法）。

    只在**整条条件被一对括号包住**时动手，例如
        if (x == 0) {        →  if x == 0 {
        while (i < n) {      →  while i < n {
        for v in (xs) {      →  for v in xs {
        match (t) {          →  match t {
    括号不是"整条"时一律不动：
        if (a) and (b) {     →  保持（括号属于子表达式）
        while (x) < 10 {     →  保持
    """
    n = len(items)
    for i, p in enumerate(items):
        if p.kind != Kind.LPAREN or p.dead:
            continue

        # 前一个有效 token 必须是 if / while / match / in
        prev = None
        k = i - 1
        while k >= 0:
            if not items[k].dead and not items[k].is_comment:
                prev = items[k]
                break
            k -= 1
        if prev is None:
            continue
        if prev.kind not in (Kind.IF, Kind.WHILE, Kind.MATCH, Kind.IN):
            continue

        # 找到配对的右括号
        depth = 0
        j = -1
        for m in range(i, n):
            if items[m].kind == Kind.LPAREN:
                depth += 1
            elif items[m].kind == Kind.RPAREN:
                depth -= 1
                if depth == 0:
                    j = m
                    break
        if j < 0:
            continue

        # 右括号后必须是 `{`（说明这对括号包住了整条条件）
        nxt = None
        k = j + 1
        while k < n:
            if not items[k].dead and not items[k].is_comment:
                nxt = items[k]
                break
            k += 1
        if nxt is None or nxt.kind != Kind.LBRACE:
            continue

        items[i].dead = True
        items[j].dead = True


_GEN_SPACE = re.compile(r"(\b[A-Za-z_][A-Za-z_0-9]*)\s*<\s*([A-Za-z_][A-Za-z_0-9]*(?:\s*,\s*[A-Za-z_0-9]+)?)\s*>")


def _fix_generics(text: str) -> str:
    """把 `Vec < u8 >` 收拾成 `Vec<u8>`（只在明显是泛型的位置动手）。"""
    def rep(m):
        name, args = m.group(1), m.group(2)
        if name in ("if", "while", "for", "match", "return", "and", "or"):
            return m.group(0)
        return f"{name}<{args}>"
    return _GEN_SPACE.sub(rep, text)


def format_source(src: str) -> str:
    items, diags = _pieces(src)
    if diags:
        return src
    if not items:
        return ""
    return _fix_generics(Formatter(items).run())


def format_file(path: str) -> Tuple[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    return src, format_source(src)
