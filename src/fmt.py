#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""fmt.py —— 花括号格式串的**解析 + 渲染**（`{}` / `{:>8}` / `{:08x}` / `{:.6f}`）

## 为什么单独一个模块

**检查器**（编译期）和**解释器**（运行期）要用**同一份解析规则**——
两边各写一份，迟早会不一致（我们已经吃过"两套实现漂移"的亏）。

## 语法（全集）

| 写法 | 含义 | 收的值 |
|---|---|---|
| `{{` / `}}` | 字面量 `{` / `}` | — |
| `{}` | 默认渲染（**与 `print(x)` 同一个结果**） | 全部 |
| `{:d}` `{:u}` | 十进制整数（`{:u}` 只收非负） | 整数 |
| `{:x}` / `{:X}` | 十六进制（小写 / 大写，**只收非负**） | 整数 |
| `{:f}` / `{:.Nf}` | 定点小数，默认 6 位；`N` = 0..100 | 浮点 |
| `{:s}` | 文本（`vector<u8>` / 字符串） | 文本 |
| 宽度 `{:8}` | 补到宽 8（默认右对齐） | 全部 |
| 零填充 `{:08}` | 补到宽 8，**补 `0`**（不是空格） | 数字 |
| 对齐 `{:<8}` / `{:>8}` | 左 / 右对齐 | 全部（文本也能用） |

**叠写顺序**：`{:[<|>][0][宽度][.精度][d|u|x|X|f|s]}`，例如 `{:08x}`、`{:>10.2f}`、`{:<8}`。

## 两条硬规则

1. **静默错值比报错更糟**：宁可 `E-Format-BadSpec`，也不给一个"看起来正常"的错值。
   `0` 是**零填充**标志（`{:08x}` → `000000ff`），不是宽度 `8` 的前导字符；
   浮点**不截断成整数**（`{}` + `3.14159265` → `3.14159265`）。
2. **不支持的写法一律报错，并在文案里列出支持集**（见 `_HINT`）：
   `{:b}` / `{:o}` / `{:e}` / `{:g}` / `{:+}` / `{:^8}` / `{:08s}` / `{:<08}` /
   `{:.2}`（有精度没 `f`）/ `{:x}` 收负值或浮点 / `{:s}` 收数字 —— 全部 `E-Format-*`。
"""
from typing import List, Tuple


class FmtError(Exception):
    def __init__(self, code: str, msg: str, pos: int = 0):
        self.code = code
        self.msg = msg
        self.pos = pos
        super().__init__(f"{code}: {msg}")


#: 一个片段：`("lit", 文本)` 或 `("hole", (align, width, kind))`
#:
#: **洞的 payload 必须保持三元组**：`checker.py` 按 `_align, _w, kind = hole`
#: 解包（它只用 `kind` 判断 `{:x}` 的实参类型）。新属性按下表编码进去，
#: 不新增第 4 个元素：
#:
#: | 位置 | 取值 |
#: |---|---|
#: | `align` | `None`（默认右对齐）/ `"<"`（左对齐）/ `">"`（显式右对齐）/ `"0"`（零填充） |
#: | `width` | `None` 或正整数 |
#: | `kind` | `None` / `"d"` / `"u"` / `"x"` / `"X"` / `"s"` / `"f"` / `"f.<精度>"` |
Part = Tuple[str, object]

#: 宽度 / 精度的上限：**防"一个格式串吃光内存"**（`{:999999999}` 会分配 1 GB 空白）。
MAX_WIDTH = 10000
MAX_PREC = 100

#: 支持集提示。所有 `E-Format-BadSpec` 都带上它——用户看到的必须是
#: 「能写什么」，而不是只有一句「不支持」。
_HINT = ("支持的完整写法：`{}`（默认）、`{:d}`/`{:u}`（十进制）、`{:x}`/`{:X}`（十六进制，非负整数）、"
         "`{:f}`/`{:.Nf}`（定点小数，N=0..100）、`{:s}`（文本）；"
         "可叠加宽度 `N`、零填充 `0N`、对齐 `<`/`>`（`{:08x}`、`{:>8}`、`{:<8}`、`{:10.2f}`）")


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


def _bad(inner: str, why: str, pos: int) -> FmtError:
    """生成一条 `E-Format-BadSpec`：`为什么不行` + `支持集`。"""
    return FmtError("E-Format-BadSpec", f"`{{{inner}}}` 不合法：{why}。{_HINT}", pos)


def _spec(inner: str, pos: int):
    """`""` / `">2"` / `"x"` / `"s"` / `"08x"` / `".6f"` → `(align, width, kind)`。"""
    if inner == "":
        return (None, None, None)
    if not inner.startswith(":"):
        raise FmtError("E-Format-BadSpec",
                       f"洞里的写法 `{{{inner}}}` 不合法：洞只能是 `{{}}` 或 `{{:<说明符>}}`。"
                       f"{_HINT}", pos)
    spec = inner[1:]

    align = None
    if spec[:1] in ("<", ">"):
        align = spec[0]
        spec = spec[1:]

    digits = ""
    while spec[:1].isdigit():
        digits += spec[0]
        spec = spec[1:]
    # **`0` 是零填充标志，不是宽度的一部分**：`{:08x}` = 零填充到宽 8。
    # 以前这里把 "08" 整个当宽度，于是 `{:08x}` 静默退化成**空格**填充
    # （`      ff`）——用户以为拿到了 `000000ff`。
    zero = False
    if digits.startswith("0"):
        zero = True
        rest = digits[1:]
        width = int(rest) if rest else None
    else:
        width = int(digits) if digits else None

    prec = None
    if spec.startswith("."):
        ds = ""
        spec = spec[1:]
        while spec[:1].isdigit():
            ds += spec[0]
            spec = spec[1:]
        if not ds:
            raise _bad(inner, "精度后面缺数字（写 `{:.2f}`）", pos)
        prec = int(ds)

    kind = None
    if spec:
        if spec in ("d", "u", "x", "X", "f", "s"):
            kind = spec
        else:
            raise _bad(inner, f"不认识的说明符 `{spec}`", pos)

    # ---------------------------------------------------------------- 组合校验
    # **精度只对定点小数有意义**：`{:.2}` 想表达什么？`{:.2f}` 还是 `{:2}`？
    # 说不清就报错，不猜。
    if prec is not None and kind != "f":
        raise _bad(inner, "只写了精度、没写形态 `f`（精度只用于定点小数，例：`{:.2f}`）", pos)
    if zero and width is None:
        raise _bad(inner, "`0` 是零填充标志，后面必须跟宽度（`{:08x}`，不是 `{:0x}`）", pos)
    if zero and align == "<":
        raise _bad(inner, "零填充只能右对齐，`{:<08}` 没有意义（要左对齐就别写 `0`）", pos)
    if zero and kind == "s":
        raise _bad(inner, "`{:s}` 是文本，零填充只对数字有意义", pos)
    if width is not None and width > MAX_WIDTH:
        raise _bad(inner, f"宽度 {width} 太大（最多 {MAX_WIDTH}）", pos)
    if prec is not None and prec > MAX_PREC:
        raise _bad(inner, f"精度 {prec} 太大（最多 {MAX_PREC}）", pos)

    if zero:
        align = "0"
    if kind == "f" and prec is not None:
        kind = f"f.{prec}"
    return (align, width, kind)


def _split_kind(kind):
    """`"f.2"` → `("f", 2)`；其它 → `(kind, None)`。"""
    if kind is None:
        return (None, None)
    if kind.startswith("f."):
        return ("f", int(kind[2:]))
    return (kind, None)


def _desc(value) -> str:
    """给错误文案用的"值是什么"，不需要 import interp。"""
    name = type(value).__name__
    friendly = {
        "EnumVal": "枚举值",
        "StructVal": "struct 值",
        "Ok": "Result 的 `Ok`",
        "Err": "Result 的 `Err`",
        "Text": "文本",
        "bool": "bool",
        "int": "整数",
        "float": "浮点值",
        "list": "容器",
        "bytes": "字节串",
    }.get(name)
    return f"{friendly} `{name}`" if friendly else f"`{name}` 值"


def apply(spec, value) -> str:
    """按说明符把一个值渲染成文本。

    **默认 `{}` 与 `print(x)` 同一个结果**（这是本模块的对外承诺）：
    `vector<u8>` 按 UTF-8 文本、浮点按最短往返（`3.14159265`）、
    `bool` 按 `True`/`False`、枚举 / Result 按 `repr`。

    `value` 的一族：`int`（含 `UInt` 子类）、`float`、`bool`、`list`/`bytes`
    （**字节串**，解释器在进来之前已把数字容器渲染成文本）、以及枚举 / struct /
    Result 这些"只有 repr 形态"的值——它们只接受 `{}`。
    """
    align, width, kind = spec
    base, prec = _split_kind(kind)
    zero = (align == "0")

    if isinstance(value, bool):
        s = _render_bool(value, base, zero)
    elif isinstance(value, float):
        s = _render_float(value, base, prec)
    elif isinstance(value, int):
        s = _render_int(value, base)
    elif isinstance(value, (bytes, bytearray)):
        s = _render_text(bytes(value), base, zero)
    elif isinstance(value, str):
        # 宿主字符串（测试 / 内建注入的便捷形态）：与 `vector<u8>` 一样按文本。
        s = _render_text(value.encode("utf-8"), base, zero)
    elif isinstance(value, list):
        # `list` = 字节串（`Text` / `vector<u8>`）。数字容器由调用方先渲染成文本，
        # 见 `interp._format_args`——两处规则必须一致，否则 `print(n)` 与
        # `print("{}", n)` 会给出两个结果。
        s = _render_text(bytes(int(x) & 0xFF for x in value), base, zero)
    else:
        if base is not None:
            raise FmtError("E-Format-ArgType",
                           f"`{{:{base}}}` 不能用于{_desc(value)}：它只有默认渲染 `{{}}`"
                           f"（与 `print(x)` 一致）")
        if zero:
            raise FmtError("E-Format-ArgType",
                           f"零填充不能用于{_desc(value)}：零填充只对数字有意义")
        s = str(value)

    return _pad(s, width, align, zero)


def _render_bool(v: bool, base, zero: bool) -> str:
    if base is None and not zero:
        return "True" if v else "False"
    raise FmtError("E-Format-ArgType",
                   f"`bool` 只有默认渲染 `{{}}`（→ `True`/`False`）；"
                   f"数字说明符（`{{:d}}` / `{{:x}}` …）与零填充要整数："
                   f"要 `0`/`1` 就写 `if` 分支，或先显式转成整数")


def _render_int(v: int, base) -> str:
    if base in (None, "d"):
        return str(v)
    if base == "u":
        if v < 0:
            raise FmtError("E-Format-ArgType",
                           f"`{{:u}}` 只收非负整数（无符号十进制），给的是负值 {v}："
                           f"有符号十进制用 `{{:d}}`；要补码表示请先写 `({v}) as u64`"
                           f"（宽度由目标类型决定，格式串不替你猜）")
        return str(v)
    if base in ("x", "X"):
        if v < 0:
            raise FmtError("E-Format-ArgType",
                           f"`{{:{base}}}` 只收非负整数（无符号十六进制），给的是负值 {v}："
                           f"先写成无符号（`({v}) as u64`）再格式化，"
                           f"或在代码里显式决定补码宽度——`{v}` 的十六进制是什么"
                           f"取决于宽度，不能由格式串替你猜")
        return format(v, base)
    if base == "f":
        raise FmtError("E-Format-ArgType",
                       f"`{{:f}}` 要浮点值（f32/f64），给的是整数 {v}："
                       f"用 `{{}}` / `{{:d}}`，或写成 `{v}.0`")
    if base == "s":
        raise FmtError("E-Format-ArgType",
                       f"`{{:s}}` 要文本（`vector<u8>` / 字符串），给的是整数 {v}")
    # parse() 只放行上面几种；到这里说明两处实现漂移了。
    raise FmtError("E-Format-BadSpec", f"不认识的说明符 `{base}`。{_HINT}")


def _render_float(v: float, base, prec) -> str:
    if base is None:
        # **不许 `int(v)` 截断**：`print("{}", 3.14159265)` 曾经打 `3`。
        return str(v)
    if base == "f":
        digits = 6 if prec is None else prec
        return format(v, f".{digits}f")
    if base in ("d", "u", "x", "X"):
        raise FmtError("E-Format-ArgType",
                       f"`{{:{base}}}` 要整数，给的是浮点值 {v}："
                       f"浮点用 `{{}}`（默认）或 `{{:.Nf}}`（定点）——"
                       f"不提供「静默截断成整数」的写法")
    if base == "s":
        raise FmtError("E-Format-ArgType", f"`{{:s}}` 要文本，给的是浮点值 {v}")
    raise FmtError("E-Format-BadSpec", f"不认识的说明符 `{base}`。{_HINT}")


def _render_text(data: bytes, base, zero: bool) -> str:
    if base not in (None, "s"):
        raise FmtError("E-Format-ArgType",
                       f"`{{:{base}}}` 要数字，给的是文本 `{data.decode('utf-8', 'replace')}`")
    if zero:
        raise FmtError("E-Format-ArgType", "零填充只对数字有意义，文本不能零填充")
    return data.decode("utf-8", "replace")


def _pad(s: str, width, align, zero: bool) -> str:
    """补到 `width`：零填充插在符号之后（`-42` → `-0042`），空格按对齐方向补。"""
    if width is None or len(s) >= width:
        return s
    n = width - len(s)
    if zero:
        if s[:1] in ("+", "-"):
            return s[0] + "0" * n + s[1:]
        return "0" * n + s
    fill = " " * n
    return (s + fill) if align == "<" else (fill + s)
