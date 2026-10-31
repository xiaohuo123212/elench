#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""jsonval.py —— JSON 解析（**递归类型**的第一个用户）

## 产物是 Elench 的枚举值

```elench
enum Json { Null, Bool(u64), Num(u64), Str(Vec<u8>), Arr(Vec<Json>), Obj(Vec<Entry>), }
struct Entry { key: Vec<u8>, value: Json, }
```

**递归**由 `Arr(Vec<Json>)` 表达——运行期就是 `EnumVal("Json","Arr", [EnumVal(...), ...])`，
**值本身有限**，所以打印/相等/match 都不需要新机制。

## 口径

| 项 | 值 |
|---|---|
| 时间 | `O(n(text))`（单趟扫描） |
| 分配 | `1 + 节点数`，且**节点数 ≤ n(text)** → 上界 `1 + n(text)` |
| 失败 | `Err(Json::BadToken / UnexpectedEof / Trailing / BadEscape / Unsupported)`，payload 是**字节位置** |

## 明确不支持（**报错，不静默**）

- 浮点、负数、指数（本语言没有浮点/负数类型）
- 深度 > 256（防栈溢出）
"""
from typing import Any, List, Tuple

MAX_DEPTH = 256


class JsonError(Exception):
    """解析失败：`code` 是 `Json` 的变体名，`pos` 是字节位置。"""

    def __init__(self, code: str, pos: int):
        self.code = code
        self.pos = pos
        super().__init__(f"{code} @ {pos}")


class _P:
    __slots__ = ("b", "i", "n", "nodes")

    def __init__(self, data: bytes):
        self.b = data
        self.i = 0
        self.n = len(data)
        self.nodes = 0            # 已生成的节点数（分配口径用）

    # ---------- 基础
    def ws(self) -> None:
        while self.i < self.n and self.b[self.i] in b" \t\r\n":
            self.i += 1

    def peek(self) -> int:
        return self.b[self.i] if self.i < self.n else -1

    def take(self) -> int:
        if self.i >= self.n:
            raise JsonError("UnexpectedEof", self.i)
        c = self.b[self.i]
        self.i += 1
        return c

    def expect(self, ch: bytes) -> None:
        if self.i >= self.n:
            raise JsonError("UnexpectedEof", self.i)
        if self.b[self.i:self.i + 1] != ch:
            raise JsonError("BadToken", self.i)
        self.i += 1

    # ---------- 值
    def value(self, depth: int):
        if depth > MAX_DEPTH:
            raise JsonError("Unsupported", self.i)
        self.ws()
        c = self.peek()
        if c < 0:
            raise JsonError("UnexpectedEof", self.i)
        if c == ord("{"):
            return self.obj(depth)
        if c == ord("["):
            return self.arr(depth)
        if c == ord('"'):
            self.nodes += 1
            return ("str", self.string())
        if c == ord("t"):
            self.lit(b"true")
            self.nodes += 1
            return ("bool", 1)
        if c == ord("f"):
            self.lit(b"false")
            self.nodes += 1
            return ("bool", 0)
        if c == ord("n"):
            self.lit(b"null")
            self.nodes += 1
            return ("null", None)
        if c == ord("-"):
            raise JsonError("Unsupported", self.i)      # 没有负数类型
        if c in b"0123456789":
            self.nodes += 1
            return ("num", self.number())
        raise JsonError("BadToken", self.i)

    def lit(self, word: bytes) -> None:
        if self.b[self.i:self.i + len(word)] != word:
            raise JsonError("BadToken", self.i)
        self.i += len(word)

    def number(self) -> int:
        start = self.i
        while self.i < self.n and self.b[self.i] in b"0123456789":
            self.i += 1
        # 小数 / 指数：本语言没有浮点 → 明确报错
        if self.i < self.n and self.b[self.i:self.i + 1] in (b".", b"e", b"E"):
            raise JsonError("Unsupported", self.i)
        if self.i == start:
            raise JsonError("BadToken", start)
        return int(self.b[start:self.i])

    def string(self) -> bytes:
        self.expect(b'"')
        out = bytearray()
        while True:
            if self.i >= self.n:
                raise JsonError("UnexpectedEof", self.i)
            c = self.b[self.i]
            self.i += 1
            if c == ord('"'):
                return bytes(out)
            if c != ord("\\"):
                out.append(c)
                continue
            if self.i >= self.n:
                raise JsonError("UnexpectedEof", self.i)
            e = self.b[self.i]
            self.i += 1
            simple = {ord('"'): b'"', ord("\\"): b"\\", ord("/"): b"/",
                      ord("n"): b"\n", ord("t"): b"\t", ord("r"): b"\r",
                      ord("b"): b"\b", ord("f"): b"\f"}
            if e in simple:
                out += simple[e]
                continue
            if e == ord("u"):
                if self.i + 4 > self.n:
                    raise JsonError("UnexpectedEof", self.i)
                hexs = self.b[self.i:self.i + 4]
                try:
                    cp = int(hexs, 16)
                except ValueError:
                    raise JsonError("BadEscape", self.i) from None
                self.i += 4
                out += chr(cp).encode("utf-8")
                continue
            raise JsonError("BadEscape", self.i)

    def arr(self, depth: int):
        self.expect(b"[")
        self.nodes += 1
        items: List[Any] = []
        self.ws()
        if self.peek() == ord("]"):
            self.i += 1
            return ("arr", items)
        while True:
            items.append(self.value(depth + 1))
            self.ws()
            c = self.take()
            if c == ord("]"):
                return ("arr", items)
            if c != ord(","):
                raise JsonError("BadToken", self.i - 1)

    def obj(self, depth: int):
        self.expect(b"{")
        self.nodes += 1
        pairs: List[Tuple[bytes, Any]] = []
        self.ws()
        if self.peek() == ord("}"):
            self.i += 1
            return ("obj", pairs)
        while True:
            self.ws()
            if self.peek() != ord('"'):
                raise JsonError("BadToken", self.i)
            k = self.string()
            self.ws()
            self.expect(b":")
            v = self.value(depth + 1)
            pairs.append((k, v))
            self.ws()
            c = self.take()
            if c == ord("}"):
                return ("obj", pairs)
            if c != ord(","):
                raise JsonError("BadToken", self.i - 1)


def _to_value(tag: str, payload: Any, EnumVal, StructVal, Text):
    """把解析产物换成 Elench 的枚举/结构体值。"""
    if tag == "null":
        return EnumVal("Json", "Null")
    if tag == "bool":
        return EnumVal("Json", "Bool", payload)
    if tag == "num":
        return EnumVal("Json", "Num", payload)
    if tag == "str":
        return EnumVal("Json", "Str", Text(payload))
    if tag == "arr":
        return EnumVal("Json", "Arr",
                       [_to_value(t, p, EnumVal, StructVal, Text) for t, p in payload])
    if tag == "obj":
        entries = []
        for k, (t, p) in payload:
            entries.append(StructVal("Entry", {
                "key": Text(k),
                "value": _to_value(t, p, EnumVal, StructVal, Text)}))
        return EnumVal("Json", "Obj", entries)
    raise AssertionError(tag)


def parse(data: bytes):
    """返回 `(value, nodes)`；失败抛 `JsonError`。"""
    from interp import EnumVal, StructVal, Text      # 延迟导入：避免循环
    p = _P(data)
    p.ws()
    if p.i >= p.n:
        raise JsonError("UnexpectedEof", p.i)
    tag, payload = p.value(0)
    p.ws()
    if p.i < p.n:
        raise JsonError("Trailing", p.i)
    return _to_value(tag, payload, EnumVal, StructVal, Text), p.nodes
