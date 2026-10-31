#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""fmt.py —— 花括号格式串的解析（`{}` / `{:>2}` / `{:x}` / `{:s}`）

## 为什么单独一个模块

**检查器**（编译期）和**解释器**（运行期）要用**同一份解析规则**——
两边各写一份，迟早会不一致（我们已经吃过"两套实现漂移"的亏）。

## 语法

| 写法 | 含义 |
|---|---|
| `{{` / `}}` | 字面量 `{` / `}` |
| `{}` | 一个洞，默认格式 |
| `{:>N}` / `{:<N}` | 右/左对齐，宽 N |
| `{:x}` | 十六进制（仅数字） |
| `{:s}` | 强制当作字节串 |

**没有浮点**：`{:.2}` → `E-Format-BadSpec`。
"""
from typing import List, Tuple


class FmtError(Exception):
    def __init__(self, code: str, msg: str, pos: int = 0):
        self.code = code
        self.msg = msg
        self.pos = pos
        super().__init__(f"{code}: {msg}")


#: 一个片段：`("lit", 文本)` 或 `("hole", (align, width, kind))`
Part = Tuple[str, object]


def parse(fmt: str) -> List[Part]:
    """把格式串拆成片段；不合法抛 `FmtError`。"""
    parts: List[Part] = []
    lit: List[str] = []
    i = 0
    n = len(fmt)
    while i < n:
        ch = fmt[i]
        if ch == "{":
            if i + 1 < n and fmt[i + 1] == "{":
                lit.append("{")
                i += 2
                continue
            end = fmt.find("}", i + 1)
            if end < 0:
                raise FmtError("E-Format-BadSpec", "格式串里有没闭合的 `{`", i)
            inner = fmt[i + 1:end]
            if lit:
                parts.append(("lit", "".join(lit)))
                lit = []
            parts.append(("hole", _spec(inner, i)))
            i = end + 1
            continue
        if ch == "}":
            if i + 1 < n and fmt[i + 1] == "}":
                lit.append("}")
                i += 2
                continue
            raise FmtError("E-Format-BadSpec", "孤立的 `}`（要写 `}}`）", i)
        lit.append(ch)
        i += 1
    if lit:
        parts.append(("lit", "".join(lit)))
    return parts


def _spec(inner: str, pos: int):
    """`""` / `">2"` / `"x"` / `"s"` / `"<8x"` → (align, width, kind)。"""
    if inner == "":
        return (None, None, None)
    if not inner.startswith(":"):
        raise FmtError("E-Format-BadSpec", f"洞里的写法 `{{{inner}}}` 不合法", pos)
    spec = inner[1:]
    if spec.startswith("."):
        raise FmtError("E-Format-BadSpec",
                       "不支持浮点格式（`{:.2}`）——本语言还没有浮点类型", pos)
    align = None
    if spec[:1] in ("<", ">"):
        align = spec[0]
        spec = spec[1:]
    digits = ""
    while spec[:1].isdigit():
        digits += spec[0]
        spec = spec[1:]
    width = int(digits) if digits else None
    kind = None
    if spec:
        if spec in ("x", "s"):
            kind = spec
        else:
            raise FmtError("E-Format-BadSpec", f"不认识的说明符 `{spec}`", pos)
    return (align, width, kind)


def apply(spec, value) -> str:
    """按说明符把一个值渲染成文本（`value` 是 list[int] 或 int）。"""
    align, width, kind = spec
    if isinstance(value, list):
        s = bytes(int(x) & 0xFF for x in value).decode("utf-8", "replace")
        if kind == "x":
            raise FmtError("E-Format-ArgType", "`{:x}` 要数字，给的是字节串")
    else:
        v = int(value)
        s = format(v, "x") if kind == "x" else str(v)
    if width is not None and len(s) < width:
        pad = " " * (width - len(s))
        s = (s + pad) if align == "<" else (pad + s)
    return s
