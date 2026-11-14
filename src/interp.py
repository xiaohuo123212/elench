#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-

from typenames import canon, display
import compat as _compat
import copy
import stdlib
import ffi_c
import sys

import os
import threading
import re
"""interp.py —— 具体值解释器（动态闭环的执行器）。

**它做什么**：真正执行 `.eln` 里的函数，返回具体值。
**它不做什么**：不做类型检查（checker 已做）、不算成本（cost_interp 做）。

## 为什么需要它

§7.0.1 定了分工：**静态答"最多多少"（承诺），动态答"哪个候选更快"（观察）**。
"观察"必须真跑一遍——这个文件就是那台跑步机。

## 值的表示

| 语言里的 | Python 里的 |
|---|---|
| `u64` / `u32` / `u16` / `u8` | `UInt` 子类（`int`，载荷是规范无符号值） |
| `i64` / `i32` / `i16` / `i8` | `int`（二进制补码） |
| `bool` | `bool` |
| `(a, b)` 元组 | `tuple` |
| `Vec<T>` / `FixedVec<T,N>` | `list` |
| `vector<u8>`（字节串 / 文本） | `Text`（`list` 子类，按 UTF-8 文本打印） |
| `T!` / `Result[T, E]` | `Ok(v)` / `Err(e)` |
| 枚举值 `Token.Number(1)` | `EnumVal("Token", "Number", 1)` |

## 语义要点（规格定的，不是我发明的）

- **`u64`/`u8` 是无符号，表达式内部也是**（2026-11-08）：值上带 `UInt` 标记，
  `x - 1` 在 `u64` 上下文里是 `2^64-1`，比较/除法/键序都按无符号；
  `i64` 仍是 64 位二进制补码，`f64`/`bool` 不变
- **`vector<u8>` 就是"字节串"**（2026-11-09）：运行期值是 `Text`（`list` 子类）。
  **所有**产生 `vector<u8>` 的路径都要打上这个标记——字面量、声明/赋值、
  返回、内建返回（`stdlib` 的 `ret="vec-u8"` 一族）、容器元素；
  这样 `print(x)`、`print("{}", x)`、`format("{}", x)` 是**同一个结果**（UTF-8 文本）。
  其它 `list`（`vector<u64>` / `vector<f64>` / `array<T,N>`）一律按空格分隔的数字，
  **不会**因为"元素都 ≤ 255"就被当成文本（值层没有类型，猜会把 `vector<u64>` 猜错）。
  `vector<u8> {104, 105}` 与 `"hi"` 是**同一个值**，打印也是一样。
- **整数字面量按 `i64` 解释，装不进 `i64` 的正字面量在运算里按 `u64` 值处理**
  （2026-11-09）：`typecheck.py` 给 `lit-int` 的类型是 `i64`，但十进制字面量
  没有后缀，`18446744073709551615` 只有 `u64` 装得下（与 C++ 的 "decimal
  literal 放不进 long long 就是 unsigned long long" 同一条规则）。
  拓宽点只在**运算**（`_widen_oob_literals`）与**声明类型落值**（`_convert_to`）：
  不拓宽的话 `18446744073709551615 - 1` 会按 `i64` 补码回绕成 `-2`。
  单独打印 `18446744073709551615`、以及 `-9223372036854775808`（i64 最小值）
  不受影响；两个都是字面量的 `0 - 1` 仍按 `i64` 得 `-1`（既有语义）。
- **`+ - * <<` 按位宽回绕**（v0.67 算术全函数化）：无符号按各自位宽，
  有符号按 64 位补码；存储/传参/返回时按声明类型截断
- **移位的结果位宽只看左操作数**（2026-11-09，`_shift`）：`u32 x; u64 n;
  x << (32 - n)` 与 `u32 m; x << (32 - m)` 同结果（都是 32 位 → 0）——
  移位量只提供值。`& | ^` 仍按「两边最宽」，那是 `+ - *` 的算术规则；
  只有移位例外（C / Rust 同此）。
- **`/` `%` 返回普通整数，除数为 0 抛运行期陷阱 `E-Div-ByZero`**（2026-11-11；
  不再是 `Result` 变体。浮点 `/` 除零仍走 IEEE inf/nan，浮点 `% 0` 仍陷阱）
- **`T!` 函数里 `return x`**：`x` 已经是 `Ok`/`Err` 就原样返回，否则包成 `Ok(x)`
- **`?`**：遇到 `Err` 就从当前函数返回它；否则取出值
- **`?!L(args)`**：从 `kernels L` 里挑一个候选执行。默认取 `over` 名单里第一个有实现的；
  可用 `force={标签: 候选名}` 强制指定（§7.8.2 的替换法就靠这个）
- **候选默认 inline**：候选体可以引用**宿主函数**的局部变量（`n` 就是），
  所以执行候选体时用的是宿主环境的副本
"""

from typing import Any, Dict, List, Optional

from parser import Node
from ast_util import arms_of, arm_pattern, arm_block, arm_single_stmts
from ast_util import stmt_body, stmt_else, while_cond, for_iterable
from ast_util import descendants as _descendants
from kinds import INTERP_EXPR, INTERP_STMT

DEFAULT_WIDTH = 64


# ---------------------------------------------------------------- 无符号整数
#
# **为什么值层要有"无符号"标记**：语言里 `u64` / `u8` 是**无符号**，
# 但 Python 的 `int` 没有符号。以前解释器把 `u64` 也存成二进制补码
# （`0 - 1` → `-1`），只在 `print` / `run` 的出口转回无符号；于是
# **表达式内部**（比较、除法、min/max、sort、map 键序）全按有符号算：
# `u64 x = 0; x - 1 > 0` 会走 `else`，`map<u64, …>` 的键序也会错。
#
# 现在：`u64` / `u32` / `u16` / `u8` 的值是 `UInt` 的子类，载荷始终是
# **规范的无符号值** `[0, 2^N)`。它是 `int` 的子类，所以：
#   · 容器、排序、FFI、`isinstance(x, int)`、`range()` 都不用改；
#   · `str()` 直接就是用户看到的十进制（不再靠出口转换）；
#   · 算术按位宽回绕、比较按无符号、`/` `%` 按无符号整除；
#   · `i64` / `i8` … 仍是普通 `int`（二进制补码），`f64` 仍是 `float`。
#
# **混算规则**（`u64` 与 `i64`）：只要有一边是无符号，运算就按无符号做；
# 位宽取「参与运算的无符号值里最宽的那个」；有符号的一边先按
# `mod 2^位宽` 重解释（等价于 C++ 的 usual arithmetic conversions，
# 只是没有 int 提升）。两边都是 `i64`/字面量时仍是补码 64 位语义。
# **例外：移位**（`<<` `>>`）——结果位宽只看左操作数，移位量不参与
# （`u32 << u64` 是 `u32`，`_shift`）；`& | ^` 仍走上面这条「取最宽」。
UNSIGNED_WIDTHS = {"u8": 8, "u16": 16, "u32": 32, "u64": 64}
SIGNED_WIDTHS = {"i8": 8, "i16": 16, "i32": 32, "i64": 64}

#: `i64` 的正向边界。**字面量规则用**：十进制字面量没有后缀，装不进 `i64` 的
#: 正字面量在运算里按 `u64` 值处理（见 `_widen_oob_literals`）。
I64_MAX = (1 << (SIGNED_WIDTHS["i64"] - 1)) - 1


class UInt(int):
    """无符号整数（`u8`/`u16`/`u32`/`u64` 的运行时值）。

    **载荷是规范无符号值**（`0 <= v < 2^bits`），不是补码。这样
    `str()` / `format` / `min` / `max` / `sort` / AVL 键序都天然正确。
    """

    __slots__ = ()
    bits = 64

    def __new__(cls, v=0):
        m = 1 << cls.bits
        return int.__new__(cls, int(v) % m)

    # ---- 算术：任一边是 UInt 就按无符号回绕
    def __add__(self, o):
        return _ubin("+", self, o)

    def __radd__(self, o):
        return _ubin("+", o, self)

    def __sub__(self, o):
        return _ubin("-", self, o)

    def __rsub__(self, o):
        return _ubin("-", o, self)

    def __mul__(self, o):
        return _ubin("*", self, o)

    def __rmul__(self, o):
        return _ubin("*", o, self)

    def __lshift__(self, o):
        return _ubin("<<", self, o)

    def __rlshift__(self, o):
        return _ubin("<<", o, self)

    def __floordiv__(self, o):
        return _ubin("/", self, o)

    def __rfloordiv__(self, o):
        return _ubin("/", o, self)

    def __mod__(self, o):
        return _ubin("%", self, o)

    def __rmod__(self, o):
        return _ubin("%", o, self)

    # 本语言的 `/` 是整数除法（浮点才走 float），所以 `__truediv__` 同义
    def __truediv__(self, o):
        return _ubin("/", self, o)

    def __rtruediv__(self, o):
        return _ubin("/", o, self)

    def __neg__(self):
        return _ubin("-", 0, self)

    def __pos__(self):
        return self

    def __abs__(self):
        return self

    # ---- 比较：无符号
    def _u_bits(self, o):
        return max(self.bits, o.bits if isinstance(o, UInt) else 0)

    def __eq__(self, o):
        if isinstance(o, bool):
            o = int(o)
        if not isinstance(o, int):
            return NotImplemented
        b = self._u_bits(o)
        m = 1 << b
        return int(self) % m == int(o) % m

    def __ne__(self, o):
        r = self.__eq__(o)
        return r if r is NotImplemented else not r

    def __lt__(self, o):
        if isinstance(o, float):
            return float(self) < o
        if not isinstance(o, int):
            return NotImplemented
        b = self._u_bits(o)
        m = 1 << b
        return int(self) % m < int(o) % m

    def __le__(self, o):
        if isinstance(o, float):
            return float(self) <= o
        if not isinstance(o, int):
            return NotImplemented
        b = self._u_bits(o)
        m = 1 << b
        return int(self) % m <= int(o) % m

    def __gt__(self, o):
        r = self.__le__(o)
        return r if r is NotImplemented else not r

    def __ge__(self, o):
        r = self.__lt__(o)
        return r if r is NotImplemented else not r

    # 保持 int 的哈希（容器/集合里仍可用）；跨符号的 `==` 是重解释语义，
    # 这里不为它改哈希——与 C++ 的隐式转换同一个坑，文档里写明。
    __hash__ = int.__hash__


class U8(UInt):
    __slots__ = ()
    bits = 8


class U16(UInt):
    __slots__ = ()
    bits = 16


class U32(UInt):
    __slots__ = ()
    bits = 32


class U64(UInt):
    __slots__ = ()
    bits = 64


_UCLASS = {8: U8, 16: U16, 32: U32, 64: U64}
_WIDTH_TO_NAME = {8: "u8", 16: "u16", 32: "u32", 64: "u64"}

#: "没有类型"的缓存哨兵（`None` 本身要表示"没算过"会被 `getattr` 混淆）
_NONE_TY = object()

#: **除零的诊断码**（2026-11-11）。单独成常量有两个原因：
#: ① `/` `%` 的除零分支散在 `_ubin` 与 `_arith` 的快/慢路径，集中一处才
#:    不会出现"改了三处、漏了一处"；② `tools/check_diag_codes.py` 只认
#:    `"E-…"` 形式的**字面量**，写成模板字符串会把码从双向核对里漏掉。
_DIV_BY_ZERO = "E-Div-ByZero"


def _div_zero(op: str, a, float_path: bool = False) -> "InterpError":
    """`/` `%` 的除数为 0：**语言级运行期陷阱**，不是 `Result` 变体、也不是宿主异常。

    背景（2026-11-11）：整数 `/` `%` 以前返回 `Result<_, Arithmetic>`，于是
    `(lo + hi) / 2`、`n / 2`、`mask / 2` 这些算法代码里最常见的写法全被类型
    系统挡住（AI 一次通过率实验：3 题里 2 题语义全对、却卡在这里）。现在
    `/` `%` 返回**普通整数**，除零像 Rust 的 panic 一样在运行期陷阱掉。

    消息必须说清三件事，少一件作者就不知道下一步做什么：

    ① 哪个式子**真的在运行期执行了**（把被除数带出来，而不是一句"除零"）；
    ② 修法二选一：除之前判零，或在 `.tu` 里写 `requires b != 0;`；
    ③ 这与浮点 `/` 无关：`1.0 / 0.0` 是 IEEE `inf`、`0.0 / 0.0` 是 `nan`。

    `float_path=True` 只用于**浮点取模**（`1.0 % 0.0`）：浮点 `/` 有 IEEE
    定义，`%` 没有，所以仍陷阱——码相同，措辞不同。

    **绝不能让 Python 的 `ZeroDivisionError` 穿出去**：那是宿主异常，用户会
    看到 `Traceback` 而不是语言诊断。
    """
    if float_path:
        return InterpError(
            f"{_DIV_BY_ZERO}：`{a} {op} 0` 在运行期真的发生了——"
            f"浮点取模的除数是 0。浮点 `/` 有 IEEE 定义"
            f"（`1.0 / 0.0` 是 inf、`0.0 / 0.0` 是 nan），但 `%` 没有；"
            f"修法二选一：① 除之前判零（`if (b == 0.0) {{ … }}`）；"
            f"② 在 `.tu` 里写 `requires b != 0;` 声明这个前提。")
    _a = int(a) if isinstance(a, int) else a       # bool / UInt 都规范化
    return InterpError(
        f"{_DIV_BY_ZERO}：`{_a} {op} 0` 在运行期真的发生了——"
        f"`{op}` 的除数是 0。整数 `/` `%` 返回普通整数，"
        f"除零是运行期陷阱（trap），不是 `Result` 的 `Err` 变体，"
        f"也不是宿主异常。修法二选一：① 除之前判零"
        f"（`if (b == 0) {{ … }}`）；② 在 `.tu` 里写 `requires b != 0;`"
        f" 声明这个前提。浮点不受影响：`1.0 / 0.0` 是 inf、`0.0 / 0.0` 是 nan。")


def _shift(op: str, a, b):
    """移位：**结果类型只看左操作数**（C / Rust 的规则）。

    移位量只提供**数值**，不参与位宽/符号的决定——`u32 x; u64 n; x << (32 - n)`
    与 `u32 m; x << (32 - m)` 必须同结果（32 位：移位量 32 ≥ 位宽 → 0）。
    旧实现把 `+ - *` 的「两边取最宽」用到移位上：64 位的移位量把结果抬宽到
    64 位，`x << 32` 得 `2^32` 而不是 0。

    · 左操作数是无符号 → 结果就是它那一档（`u8`/`u16`/`u32`/`u64`）；
    · 左操作数不是无符号（字面量 / `i64`）→ 返回 `NotImplemented`，由调用方
      走 64 位补码路径（`i64 << n` 仍是 `i64`，右操作数是 `u64` 也不改变）。
    """
    if isinstance(a, float) or isinstance(b, float) or not isinstance(a, int) \
            or not isinstance(b, int):
        return NotImplemented
    if isinstance(a, bool):
        a = int(a)
    if isinstance(b, bool):
        b = int(b)
    if not isinstance(a, UInt):
        return NotImplemented
    w = a.bits
    # **移位量取真实数值，不按位宽回绕**：`u32 x; u64 k = 2^32; x << k` 若把
    # `k` 按 32 位回绕成 0，就成了「移 0 位」= x，而正确结果是 0（≥ 位宽）。
    # 这与「u8 当索引」是同一类陷阱：判断大小用真值，不回绕。
    y = int(b)
    x = int(a) % (1 << w)
    if y < 0 or y >= w:
        r = 0
    elif op == "<<":
        r = x << y
    else:
        r = x >> y
    return _UCLASS[w](r)


def _ubin(op: str, a, b):
    """无符号二元运算：位宽取最宽的无符号操作数，结果规范回绕。

    **移位不适用这条位宽规则**（只看左操作数），在函数开头就交给 `_shift`。
    """
    # **移位是算术里的唯一例外**：位宽只看左操作数，不落到下面「两边取最宽」。
    if op == "<<" or op == ">>":
        return _shift(op, a, b)
    # **幂的指数也取真值、不回绕**（2026-11-10）：负指数若跟着 `% m` 回绕成
    # 一个巨大的正数，就是"静默错值里最坏的一种"。位宽仍按「两边最宽」。
    if op == "**":
        bits = max(a.bits if isinstance(a, UInt) else 0,
                   b.bits if isinstance(b, UInt) else 0)
        if not bits:
            return NotImplemented
        return _UCLASS[bits](_ipow(int(a), int(b), bits))
    # 浮点优先：`u64` 与 `f64` 混算按 IEEE（不能把 1.5 截成 1）
    if isinstance(a, float) or isinstance(b, float):
        # **两个操作数都要先变成真 `float`**：`a` 可能是 `UInt`（`int` 子类），
        # 直接写 `a + b` 会再次落到 `UInt.__add__` → `_ubin` → 回到这里，
        # 无限递归（`u64 + f64` 的 RecursionError 就出在这一行）。
        a = float(a)
        b = float(b)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "**":
            return _fpow(a, b)
        if op == "/":
            return _ieee_div(a, b) if b == 0 else a / b
        if op == "%":
            if b == 0:
                raise _div_zero("%", a, float_path=True)
            r = abs(a) % abs(b)
            return r if a >= 0 else -r
        return NotImplemented
    if not isinstance(a, int) or not isinstance(b, int):
        return NotImplemented
    bits = max(a.bits if isinstance(a, UInt) else 0,
               b.bits if isinstance(b, UInt) else 0)
    if not bits:                                   # 两边都不是无符号：不该走到这里
        return NotImplemented
    m = 1 << bits
    x = int(a) % m
    y = int(b) % m
    if op == "+":
        r = x + y
    elif op == "-":
        r = x - y
    elif op == "*":
        r = x * y
    elif op == "&":
        r = x & y
    elif op == "|":
        r = x | y
    elif op == "^":
        r = x ^ y
    elif op == "/":
        if y == 0:
            raise _div_zero("/", a)
        r = x // y
    elif op == "%":
        if y == 0:
            raise _div_zero("%", a)
        r = x % y
    else:                                          # pragma: no cover
        return NotImplemented
    return _UCLASS[bits](r)


#: 标量数值类型（可以逐值转换的）。
_SCALAR_NUMERIC = set(UNSIGNED_WIDTHS) | set(SIGNED_WIDTHS)


#: 字符字面量的**封闭转义集**（与 `lexer.ESCAPES` 同一套）。
#: 值为什么在这里再解一遍：`parser` 把 `lit-char` 节点的 `value` 存成**原始
#: 文本**（`"'\\n'"`），不是解码后的字节；以前解释器直接 `ord(text[1])`，
#: 于是 `'\n'` 变成 `92`（反斜杠的 ASCII）——`if (c == '\n')` 永远不成立，
#: 是**静默错值**（`demo/corpus/11_text.cpp` 的按行切分就死在这上面）。
_CHAR_ESCAPES = {"n": 10, "t": 9, "r": 13, "0": 0,
                 "\\": 92, "'": 39, '"': 34}


def _char_lit_value(raw) -> int:
    """字符字面量的**字节值**（`'A'` → 65、`'\\n'` → 10、`'\\x41'` → 65）。

    `raw` 是源码里的原文（带单引号）。词法层已经校验过转义集合，这里只解码；
    认不出时退回第一个字符的码位（**不抛**：词法/语法已经报过错了）。
    """
    s = str(raw)
    body = s[1:-1] if len(s) >= 3 and s[0] == "'" and s[-1] == "'" else s
    if not body:
        return 0
    if body[0] != "\\" or len(body) < 2:
        return ord(body[0])
    if body[1] == "x" and len(body) >= 4:
        try:
            return int(body[2:4], 16)
        except ValueError:
            return 0
    return _CHAR_ESCAPES.get(body[1], ord(body[1]))


def _type_parts(n):
    """把类型节点拆成 `(头名, [类型实参节点…])`；字符串直接当头名。

    结果缓存在节点自己身上（Node 是 dataclass，有 `__dict__`）。热路径
    （每次下标/赋值都要按声明类型转换）不能每轮重新遍历 type-args。
    """
    if n is None:
        return "", []
    if isinstance(n, str):
        return canon(n), []
    _cached = getattr(n, "_tp_cache", None)
    if _cached is not None:
        return _cached
    k = getattr(n, "kind", "")
    if k in ("type", "type-fail", "type-ref") and n.children:
        _res = _type_parts(n.children[0])
    elif k == "type-name":
        args = next((c for c in n.children if getattr(c, "kind", "") == "type-args"), None)
        _res = (canon(n.name or ""), [
            c for c in ((args.children if args is not None else []) or [])
            if getattr(c, "kind", "") in ("type-name", "type-ref")])
    else:
        _res = ("", [])
    try:
        n._tp_cache = _res
    except Exception:                                   # noqa: BLE001
        pass
    return _res


def _elem_type_node(tn):
    """容器的元素类型节点（`vector<T>` / `set<T>` / `map<K,V>` → V / ...）。"""
    head, args = _type_parts(tn)
    if head in ("vector", "array", "fixedvec", "stack", "queue", "set", "unordered_set"):
        return args[0] if args else None
    if head in ("map", "unordered_map"):
        return args[1] if len(args) >= 2 else None
    return None


def _pointee_type_node(tn):
    """指针类型的**指向类型节点**（`u64*` → `u64` 的 type 节点；不是指针 → `None`）。

    `parser.parse_type_ref_suffix` 产出的 `type-ptr` 是**一层一层**套的：
    `char**` = `type-ptr(type-ptr(char))`，所以这里只剥一层，
    多层解引用（`**p`）由调用方递归。
    """
    if tn is None:
        return None
    if getattr(tn, "kind", "") == "type-ptr" and tn.children:
        return tn.children[0]
    return None


def _key_type_node(tn):
    head, args = _type_parts(tn)
    if head in ("map", "unordered_map", "set", "unordered_set"):
        return args[0] if args else None
    return None


def _pair_type_node(key_node, val_node):
    """造一个**二元组**类型节点 `(K, V)`（`map` 遍历的元素类型，2026-11-12）。

    为什么要有它：`for (const auto& kv : m)` 的 `kv` 在 C++ 里是
    `pair<const K, V>`，不是键。本语言已有的二元组就是 `(K, V)`（类型 `(A,B)`、
    字段 `.0` / `.1`；`--compat` 下 `std::pair` 也映射到它），所以元素类型直接
    用 `type-tuple`，**不新造 pair 类型**。缺任一类型实参 → `None`（不猜）。
    """
    if key_node is None or val_node is None:
        return None
    node = Node("type-tuple", getattr(key_node, "line", 0),
                getattr(key_node, "col", 0))
    node.add(key_node)
    node.add(val_node)
    return node


def _convert_scalar(v, tyname: str):
    """按声明类型转换一个标量。**只处理数值**，其它原样返回。"""
    if not isinstance(v, int) or isinstance(v, bool):
        if tyname in ("f32", "f64") and isinstance(v, float):
            return _round_f32(v) if tyname == "f32" else v
        return v
    if tyname in UNSIGNED_WIDTHS:
        cls = _UCLASS[UNSIGNED_WIDTHS[tyname]]
        # 已经是同类值就**原样返回**：容器里已经标好的元素不要每次读都造新对象
        # （否则 `rows[0][0] = x` 会改到一份副本上）。
        return v if type(v) is cls else cls(v)
    if tyname in SIGNED_WIDTHS:
        w = SIGNED_WIDTHS[tyname]
        m = 1 << w
        if type(v) is int and -(m >> 1) <= v < (m >> 1):
            return v
        x = int(v) % m
        return x - m if x >= (m >> 1) else x
    if tyname == "f32":
        return _round_f32(float(v))
    if tyname == "f64":
        return float(v)
    return v


def _convert_read(v, tn):
    """**读容器元素**时的转换：只转标量。

    嵌套容器（`vector<vector<u8>>`）的元素是 list；按 `vector<u8>` 逐元素
    转换会造出一份新 list，`rows[0][0] = x` 就改在副本上了（静默不生效）。
    所以读侧只补标量的无符号标记，容器保持原对象。
    """
    if tn is None:
        return v
    head = _type_label(tn)
    if head in _SCALAR_NUMERIC or head in ("f32", "f64"):
        return _convert_scalar(v, head)
    return v


def _convert_to(v, tn):
    """按**声明类型节点**转换值（标量按位宽，容器逐元素）。

    `Text` 是 `vector<u8>` 的运行时形态，**不能**被逐元素重建成普通 list，
    否则字符串会退化成整数列表、`print` 就打不出文本了。

    **这里是 `vector<u8>` 的"盖章点"之一**：声明/赋值/返回/结构体字段/
    `map` 的键值都走这个函数，所以只要声明类型是 `vector<u8>`，
    普通 `list` 会被包成 `Text`。不这么做的话，"同一个值"会因为在
    哪条路径上产生而有两种打印结果（字面量 → 文本；函数返回/内建 → 数字）。
    """
    if tn is None:
        return v
    head, args = _type_parts(tn)
    if not head:
        return v
    if head in _SCALAR_NUMERIC:
        return _convert_scalar(v, head)
    if head in ("vector", "array", "fixedvec") and isinstance(v, list) and args:
        if isinstance(v, Text):
            return v
        elem = args[0]
        # **`vector<u8>` 就是字节串**：与字符串字面量同一个运行时形态。
        # 元素仍按 `u8` 落值（`_convert_scalar`），只是容器标成 `Text`。
        if head == "vector" and _type_label(elem) == "u8":
            return Text(_convert_scalar(x, "u8") for x in v)
        # **就地重标元素，绝不换列表**（K1，2026-11-10）。
        #
        # 以前这里是 `out = [_convert_to(x, elem) for x in v]`：只要有一个
        # 元素需要改类型（`3` → `U64(3)`）就返回一份**新列表**。字段读
        # （`_field` → 本函数）正好走这条路，于是
        #     `s.v.push(3); s.v.push(1);`
        # 的第二次 push 写在"读字段得到的副本"上，字段里只有第一个值——
        # 静默数据丢失。`s.v[0] = 9` 同病。
        #
        # 该拷贝的地方（`T a = b;` / 赋值 / 按值形参 / `return <借用值>` /
        # `push` 的左值实参 / 聚合初始化）都已经在入口拷过了（见 `_deep_copy`
        # 的七个拷贝点）；这里再复制一次不只是重复 O(n)，更会制造**假别名**：
        # 调用方以为拿着字段，其实拿着副本。转换只负责把元素类型落对，
        # 对象身份必须保持不变，嵌套容器递归走进来也一样。
        for _i in range(len(v)):
            _old = v[_i]
            _new = _convert_to(_old, elem)
            if _new is not _old:
                v[_i] = _new
        return v
    return v


def _type_label(tn) -> str:
    """类型节点的头名（`u64` / `vector` / `map` / …）。"""
    return _type_parts(tn)[0]


#: `npos` 的值：`u64` 最大值（与 C++ 的 `std::string::npos` 一致）。
#: `find` 找不到时返回它；AI 会写 `if (xs.find(v) != npos)`。
_U64_MAX = (1 << 64) - 1


def _u64(v) -> U64:
    """把 `u64` 结果标成无符号（内建/方法返回值用）。"""
    return U64(v)


def _widen_oob_literals(a, b):
    """**装不进 `i64` 的正整数字面量**在参与运算时按 `u64` 值处理。

    `typecheck.py` 给 `lit-int` 的类型是 `i64`，但十进制字面量没有后缀：
    `18446744073709551615` 只有 `u64` 装得下（C++ 的 decimal literal
    规则相同：放不进 `long long` 就是 `unsigned long long`）。
    值层没有"这是字面量"的标记，但**超出 `i64` 范围的 plain `int` 只可能
    来自字面量**——算术结果都会被 `_wrap`/`_ubin` 收回 64 位——所以按值
    判定与按字面量判定等价。

    为什么要在这里补：不补的话 `18446744073709551615 - 1` 会走 `i64`
    补码回绕得 `-2`，`0 - 1 == 18446744073709551615` 会被当成有符号
    比较得 `False`。补上之后完全复用既有规则「任一边是无符号 → 按无符号」，
    没有第二套符号规则。

    只在**运算**处拓宽：单独打印 `18446744073709551615`、以及
    `-9223372036854775808`（`i64` 最小值的写法：正字面量 2^63 超出 i64，
    但一元负号后正好落在 i64 里）都不受影响。
    """
    if type(a) is int and a > I64_MAX:
        a = U64(a)
    if type(b) is int and b > I64_MAX:
        b = U64(b)
    return a, b


def _ipow(x: int, y: int, bits: int) -> int:
    """`x ** y` 在 `bits` 位无符号量里**回绕**求值（2026-11-10）。

    **用模幂，不要先算出真值**：`2 ** 1000000` 的真值有 30 万位，先算出来
    再截断既慢又吃内存；`pow(x, y, 1 << bits)` 只要 `log y` 次乘法。
    语义与 `*` 完全一致（`u64` 回绕），所以 `2 ** 64` = `0` 是**定义好的**
    值，不是"算错了"——类型检查那边要求指数是字面量，就是为了让这个语义
    （回绕 vs 溢出）在源码里**看得见**。

    **指数取真值、不回绕**（同 `_shift`）：负指数变成"巨大的正数"是最坏
    的一种静默错值，这里一律报错。
    """
    if y < 0:
        raise InterpError(
            "E-Type-Pow-NegativeExponent：整数 `**` 的指数不能为负"
            "（`2 ** -1` 是 0.5，不是整数）。写浮点 `2.0 ** -1`")
    m = 1 << bits
    return pow(x % m, y, m)


def _fpow(a: float, b: float) -> float:
    """`f64 ** f64` 的 **IEEE 754** 幂（对应后端的 `powf`）。

    **不能直接写 `a ** b`**：Python 的 `(-8.0) ** 0.5` 抛 `ValueError`、
    `0.0 ** -1.0` 抛 `ZeroDivisionError`——宿主异常会穿出解释器，变成
    "解释器崩了"而不是"算出来是 nan"。本语言的浮点约定是「`/ 0` 给
    inf/nan，不报错」（见 `_ieee_div`），幂沿用同一条：

    · 负底数 + 非整数指数 → `nan`（IEEE 如此）；
    · `0.0 ** 负数` → `inf`（`-0.0` 配奇负整数 → `-inf`）；
    · 溢出（`1e308 ** 2`）→ `±inf`。
    """
    import math
    try:
        return math.pow(a, b)
    except OverflowError:
        neg = a < 0.0 and float(b).is_integer() and int(b) % 2 != 0
        return -math.inf if neg else math.inf
    except ValueError:
        if a == 0.0:
            neg = (math.copysign(1.0, a) < 0.0 and float(b).is_integer()
                   and int(b) % 2 != 0)
            return -math.inf if neg else math.inf
        return math.nan


def _arith(op: str, a, b):
    """一个算术运算的**唯一实现**（二元表达式与复合赋值共用）。

    有符号路径仍是 64 位二进制补码（`i64` 的既有语义，不动）；
    只要任一边是 `UInt`，就按无符号回绕（见 `_ubin`）。
    **例外：移位**——结果类型只看左操作数（见 `_shift`），移位量不参与。
    """
    # **指针没有算术**（2026-11-10 指针接线）：`p + 1` / `p - q` / `p += 1`
    # 在本语言里**没有定义**（也就没有 C++ 的指针步进语义）。不在这里挡的话，
    # 宿主会抛 `TypeError: unsupported operand type(s) for +: 'PtrVal' and 'int'`
    # ——那是宿主异常，不是语言诊断。指针只支持 `==` / `!=`（见 `_cmp`）。
    if isinstance(a, PtrVal) or isinstance(b, PtrVal):
        raise InterpError(
            f"指针不做算术运算（`{op}`）：本语言没有指针步进 / 指针相减。"
            f"要遍历请用下标（`for (u64 i = 0; i < xs.length(); i = i + 1)`）"
            f"或 `for (auto& x : xs)`；"
            f"指针只用 `*p` / `p->f` 读写和 `==` / `!=` 比较。")
    # **超出 `i64` 的字面量按 `u64` 参与运算**（见 `_widen_oob_literals`）。
    a, b = _widen_oob_literals(a, b)
    # **快路径**：`u64` 是最热的组合（循环计数器/累加器）。直接算，
    # 不经过 `_ubin` 的 isinstance 分派；结果仍由 `U64.__new__` 回绕。
    if type(a) is U64 and type(b) is U64:
        x = int(a)
        y = int(b)
        if op == "+":
            return U64(x + y)
        if op == "-":
            return U64(x - y)
        if op == "*":
            return U64(x * y)
        if op == "**":
            return U64(_ipow(x, y, 64))
        if op == "&":
            return U64(x & y)
        if op == "|":
            return U64(x | y)
        if op == "^":
            return U64(x ^ y)
        if op == "<<":
            return U64(0 if y >= 64 else x << y)
        if op == ">>":
            return U64(0 if y >= 64 else x >> y)
        if op == "/":
            if y == 0:
                raise _div_zero("/", a)
            return U64(x // y)
        if op == "%":
            if y == 0:
                raise _div_zero("%", a)
            return U64(x % y)
    # 浮点：IEEE 语义（除零是 inf/nan，不是错误）
    if isinstance(a, float) or isinstance(b, float):
        # 同 `_ubin`：先转真 `float`，否则 `a + b` 会重入 `UInt.__add__`。
        a = float(a)
        b = float(b)
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "**":
            # 浮点幂：任一边是 `f64`/`f32` 就走这里（IEEE 754，见 `_fpow`）。
            # `2 ** 0.5` 也落在这一支：`1.4142135623730951`。
            return _fpow(a, b)
        if op == "/":
            return _ieee_div(a, b) if b == 0 else a / b
        if op == "%":
            if b == 0:
                raise _div_zero("%", a, float_path=True)
            r = abs(a) % abs(b)
            return r if a >= 0 else -r
        raise InterpError(f"浮点不支持运算 {op}")
    if isinstance(a, bool) or isinstance(b, bool):
        a, b = int(a), int(b)
    # **移位：结果类型只看左操作数**（C / Rust）。放在 `_ubin` 分派之前——
    # 右操作数是 `u64` 也不能把左操作数（字面量或 `i64`）的语义改掉。
    if op == "<<" or op == ">>":
        r = _shift(op, a, b)
        if r is not NotImplemented:
            return r
        # 左操作数不是无符号 → `i64` 的 64 位补码。**先把移位量取成 int**：
        # `a << UInt` 会走 `UInt.__rlshift__` 再绕回 `_shift`（绕圈）。
        y = int(b)
        return _wrap(0 if y < 0 or y >= DEFAULT_WIDTH else
                     (a << y if op == "<<" else a >> y))
    if isinstance(a, UInt) or isinstance(b, UInt):
        return _ubin(op, a, b)
    if op == "+":
        return _wrap(a + b)
    if op == "-":
        return _wrap(a - b)
    if op == "*":
        return _wrap(a * b)
    if op == "**":
        # 两边都不是无符号（`i64` / 字面量）：64 位补码回绕，与 `*` 同口径
        return _wrap(_ipow(a, b, DEFAULT_WIDTH))
    if op == "&":
        return _wrap(a & b)
    if op == "|":
        return _wrap(a | b)
    if op == "^":
        return _wrap(a ^ b)
    if op == "/":
        # **除零是运行期陷阱**（`_div_zero` → `E-Div-ByZero`）：`/` 返回普通整数，
        # 不是 `Result`。`.tu` 里的 `requires b != 0` 只把前提写下来，不改这里的处理。
        if b == 0:
            raise _div_zero("/", a)
        # C++ 语义：向零截断（不是 Python 的 floor）
        q = abs(a) // abs(b)
        return q if (a >= 0) == (b >= 0) else -q
    if op == "%":
        if b == 0:
            raise _div_zero("%", a)
        r = abs(a) % abs(b)
        return r if a >= 0 else -r
    raise InterpError(f"未实现的二元运算 {op}")


def _cmp(op: str, a, b) -> bool:
    """比较。任一边是 `UInt` 时由 `UInt` 的富比较按无符号解释另一边。"""
    # **指针只有"相等 / 不等"**（`p == q` / `p != nullptr`）：地址没有序，
    # 也没有"指针 < 指针"的语义。别的比较一律是语言诊断，而不是宿主 `TypeError`。
    if isinstance(a, PtrVal) or isinstance(b, PtrVal):
        if op not in ("==", "!="):
            raise InterpError(
                f"指针只能比较 `==` / `!=`（不支持 `{op}`）：地址没有顺序，"
                f"比大小没有意义。")
        return a == b if op == "==" else a != b
    # **超出 `i64` 的字面量按 `u64` 参与比较**（见 `_widen_oob_literals`）。
    a, b = _widen_oob_literals(a, b)
    # 快路径：两个同宽无符号值都已规范化，直接按 int 比（省掉富比较分派）
    if type(a) is type(b) and isinstance(a, UInt):
        x = int(a)
        y = int(b)
        if op == "<":
            return x < y
        if op == "<=":
            return x <= y
        if op == ">":
            return x > y
        if op == ">=":
            return x >= y
        if op == "==":
            return x == y
        if op == "!=":
            return x != y
    if op == "<":
        return a < b
    if op == "<=":
        return a <= b
    if op == ">":
        return a > b
    if op == ">=":
        return a >= b
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    raise InterpError(f"未实现的比较运算 {op}")


# ---------------------------------------------------------------- 操作数计数器
#
# **它要回答的问题**：`κ` 是不是上界？——只有把两边换成**同一单位**才谈得上比大小。
# 这个计数器数的是「**操作数**」，不是纳秒；它与 `cost_interp` 的计费点**一一对应**：
#
# | 计 1 的东西 | 这里的计费点 | 对应的 `cost_interp` 计费点 |
# |---|---|---|
# | 一次**表达式求值** | `_eval`（每个非 `None` 节点，含叶子与 `paren` 包裹） | `_expr` 的每个节点 |
# | 一次**语句执行** | `_stmt`（每条语句每次执行都算） | `_stmt` 的每次调用 |
# | 一次**内建调用** | `_call` / `_method` 的内建分派 | `_call` 里那几个 `OP_COST` |
# | 一次**拷贝** | `_copy_value`：按 `_copy_units(v)` 记 `n` / `n + 1` | `_copy_cost` 的 time 项（`_k_stmt_*` / `_store_copy_cost`）
#
# **拷贝怎么计**（2026-11-09，A-full 第 2 步）：口径**只抄 `_copy_cost`**，不另定——
#   `vector`/`Text`/`stack`/`queue` → `n + 1`；`map`/`set`/`unordered_*` → `n`；
#   `array<T,N>` → `N`；`struct` → 字段数；**标量 / enum → 0**（κ 的登记口径）。
# 每个**拷贝点**记一次（`T a = b;` / `a = b;` / 按值形参 / `return` 借用值 /
# `push`·`insert`·`vec_push` 等入容器 / `m[k] = v` / `for` 元素 / `S{a}` 成员）。
# 分配（`self.allocs`）走同一份 `_copy_units` 的 alloc 列，**与 ops 分开记**。
#
# **粒度**：进入 `_run_fn` 记下起点、返回时结算，所以 `ops_of(f)` 是
# **整棵调用树**（含嵌套调用）的操作数——这正是能和 `κ(f)`（它也含被调函数）比的那个数。
#
# **默认关闭**：`ELENCH_COUNT_OPS=1`（或编程调用 `ops_enable()`）才开。
# 热路径上只有单分支 `if _COUNT:`，不建对象、不改语义（计数只加不查）。
#
# **线程模式下不精确**：`parallel { … } threads` 里的 `_bump()
# 会**少量少计**。要做"ops ≤ κ"的判定请用顺序模式（`parallel="seq"`，默认），
# `tools/kappa_vs_ops.py` 就是这么跑的。
#
# **口径差（2026-11-10 逐条实测后重写）**：旧版注释列的五条——`paren` κ 记 0、
# `min`/`max` 不计实参、`Ok`/`Err` 不计实参、`print` 少 1、`expr-stmt` 多 1——
# **都已经修掉，现在 κ == ops**。逐条实测（探针函数 / κ / ops）：
#
#   | 构造 | 探针 | κ | ops |
#   |---|---|---|---|
#   | `paren`（每层算 1 个节点） | `(((x)))` | 5 | 5 |
#   | `min` / `max`（含实参） | `min(a + b, b)` | 7 | 7 |
#   | `Ok` / `Err` | `Ok(x + 1)` / `Err(E::Bad)` | 5 / 3 | 5 / 3 |
#   | `print` | `print(x)` / `print("{}", x)` | 6 / 7 | 6 / 7 |
#   | `expr-stmt` | `x * 2; x + 1;` | 10 | 10 |
#
# 回归钉在 `tests/cases/test_kappa_gaps.py`：[3] min/max、[5] 括号、[6] 上面这五个。
#
# **仍然存在、且方向安全的一处**：`x as u64` 的类型子节点在 κ 里记 1，
# 而解释器 `_eval` 不进入它 ⇒ κ 比 ops **大 1**（上界更松，不会漏）。
# **不要**为了"对齐"把它调小——见 `test_kappa_gaps.py` [5] 的 `just_cast`。
#
# 新的差**必须逐条实测**（`tools/kappa_vs_ops.py`），不许靠这张表推断，
# 也不许为了凑 `ops ≤ κ` 去改小计数（见 §8.5）。

#: 计数开关：默认关。**只读一次环境变量**，之后由 `ops_enable()` 改。
_COUNT = os.environ.get("ELENCH_COUNT_OPS", "") not in ("", "0", "false", "False", "off")

#: **per-thread 计数器**：每个线程只写自己的槽位，读的时候再汇总。
#:
#: 为什么不用 `_bump()
#: 两步之间别的线程可以插进来，于是两个线程各加 1、结果只加了 1（丢增量）。
#: 丢的量补不回来 ⇒ 线程模式只会**少计** ⇒ `ops ≤ κ` 变成假阳性。
#:
#: 代价：热路径多一次 `threading.local()` 属性读（无锁、无竞争）。
_LOCAL = threading.local()

#: 所有线程的槽位（`{"n": 总操作数, "by": {函数名: 操作数}}`）。
_SLOTS: List[dict] = []


def _slot() -> dict:
    s = getattr(_LOCAL, "slot", None)
    if s is None:
        s = {"n": 0, "by": {}}
        _LOCAL.slot = s
        _SLOTS.append(s)
    return s


def _bump() -> None:
    """+1（本线程）。**热路径**：不做任何跨线程写。"""
    _slot()["n"] += 1


def _bump_fn(name: str, delta: int) -> None:
    if delta:
        b = _slot()["by"]
        b[name] = b.get(name, 0) + delta


def _total() -> int:
    return sum(s["n"] for s in _SLOTS)


def _total_of(name: str) -> int:
    return sum(s["by"].get(name, 0) for s in _SLOTS)


def ops_enabled() -> bool:
    """计数开关当前是否打开。"""
    return _COUNT


def ops_enable(on: bool = True) -> None:
    """打开/关闭计数（`--ops` 与 `ELENCH_COUNT_OPS=1` 之外的编程入口）。"""
    global _COUNT
    _COUNT = bool(on)


def ops_reset() -> None:
    """清零：所有线程槽位。**每次测量前调一次**（否则数字会累加）。"""
    for s in _SLOTS:
        s["n"] = 0
        s["by"].clear()


def ops_of(fn: Optional[str] = None) -> int:
    """**实测操作数**（所有线程汇总）。

    `fn` 给名字 → 该函数整棵调用树的操作数（含嵌套调用）；
    `fn` 缺省 → 本进程自 `ops_reset()` 以来的总操作数。

    **单位是「操作」，不是纳秒**——它只和 `κ`（也是操作数）可比。
    """
    if fn is None:
        return _total()
    return _total_of(fn)


def ops_by_fn() -> Dict[str, int]:
    """每函数一份（**含嵌套调用**，因此各项之和 ≥ 总数）。"""
    out: Dict[str, int] = {}
    for s in _SLOTS:
        for k, v in s["by"].items():
            out[k] = out.get(k, 0) + v
    return out


# ---------------------------------------------------------------- 值

class Ok:
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v

    def __repr__(self):
        return f"Ok({self.v!r})"

    def __eq__(self, other):
        return isinstance(other, Ok) and self.v == other.v


class Err:
    __slots__ = ("e",)

    def __init__(self, e):
        self.e = e

    def __repr__(self):
        return f"Err({self.e!r})"

    def __eq__(self, other):
        return isinstance(other, Err) and self.e == other.e


class EnumVal:
    """枚举值：`Token.Number(1)` → EnumVal("Token", "Number", 1)。"""

    __slots__ = ("type_name", "variant", "payload")

    def __init__(self, type_name: str, variant: str, payload=None):
        self.type_name = type_name
        self.variant = variant
        self.payload = payload

    def __repr__(self):
        if self.payload is None:
            return f"{self.type_name}.{self.variant}"
        return f"{self.type_name}.{self.variant}({self.payload!r})"

    def __eq__(self, other):
        return (isinstance(other, EnumVal) and self.type_name == other.type_name
                and self.variant == other.variant and self.payload == other.payload)

    def __hash__(self):
        return hash((self.type_name, self.variant))


#: **内置错误枚举的变体顺序**——序号就是这里的下标（`Io::NotFound as u64` → 0）。
#: 用户 `enum` 的序号来自 `.tu` 的声明顺序，见 `Interp._collect`。
#: 与 `typecheck.TypeChecker.enums` 的初始表**同一组**：那边管"变体存在性"，
#: 这边管"`as` 转成数值时是几"。
_BUILTIN_ENUM_ORDERS: Dict[str, List[str]] = {
    "Io": ["NotFound", "Denied", "Failed"],
    "Arithmetic": ["DivisionByZero", "Overflow"],
    "Parse": ["BadNumber", "Overflow"],
    "Range": ["OutOfBounds"],
    "Option": ["Some", "None"],
}

#: `_const_value` 的"求不出"哨兵。**不能用 `None`**：`const X: u64 = ...`
#: 的值域里没有 `None`，用 `None` 会把"求不出"和"值是 None"混在一起。
_CONST_UNKNOWN = object()


def _builtin_min(args) -> Any:
    """`min(a, b)` —— 两个同类型整数。成本常数，不影响阶。"""
    if len(args) != 2:
        raise InterpError(f"min 要 2 个实参，给了 {len(args)}")
    return args[0] if args[0] <= args[1] else args[1]


def _builtin_max(args) -> Any:
    """`max(a, b)` —— 两个同类型整数。成本常数，不影响阶。"""
    if len(args) != 2:
        raise InterpError(f"max 要 2 个实参，给了 {len(args)}")
    return args[0] if args[0] >= args[1] else args[1]


def _text_of(v) -> str:
    """把值渲染成文本——**`print` / 格式串 / `format` 共用的唯一一套规则**。

    | 值 | 渲染 |
    |---|---|
    | `Text`（`vector<u8>` / 字符串） | UTF-8 文本（`hi`） |
    | `bytes`（宿主注入的字节） | UTF-8 文本 |
    | 其它 `list`（`vector<u64>` / `vector<f64>` / `array`） | 空格分隔的数字（`104 105`） |
    | 标量 | `str()` |

    **判据只能是值的类型标记（`Text`），不能是"元素都 ≤ 255"**：
    `vector<u64> {104, 105}` 元素也都在 0..255，按值猜就会把数字向量
    打成一个巧合的文本，那正是「同一个值、两种结果」的另一种写法。
    """
    if isinstance(v, Text):
        return v.text()
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", "replace")
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return str(v)


def _format_args(args):
    """`print("…{}…", …)` / `format(...)` 的实参预处理。

    `fmt.apply` 只认「`list` = 字节串」和「标量 = 数字」两种。`vector<u64>`
    也是 `list`，会被它当字节串解码（`{104, 105}` → `hi`）——于是
    `print(n)` 打数字、`print("{}", n)` 打文本。这里在进 `fmt` 之前把
    **不是 `Text` 的 list** 先渲染成 `"104 105"` 的 `Text`，
    两条路径就只剩一套规则。
    """
    if len(args) < 2:
        return args
    out = [args[0]]
    for a in args[1:]:
        out.append(Text(_text_of(a).encode("utf-8"))
                   if isinstance(a, list) and not isinstance(a, Text) else a)
    return out


def _builtin_print(args) -> Any:
    """`print(x)` —— 打印一行，返回 0（u64）。

    **它是"调试内建"，不是契约维度**：成本只记 1 个操作，
    不建模 IO 延迟。真正的 IO 成本该走 `effects { io }`，那是另一件事。

    渲染规则见 `_text_of`：`vector<u8>`（`Text`）按文本，其它容器按数字。
    `print(v)`、`print("{}", v)`、`format("{}", v)` 必须是同一个结果。
    """
    if not args:
        raise InterpError("print 至少要 1 个实参")
    # **插值**：`print("x = {}, y = {:>2}", x, y)` —— 第一个参数含 `{` 就当格式串。
    # 没有 `{` 就保持原来的"空格分隔"行为（老代码不受影响）。
    # **只有文本才可能是格式串**：`vector<u64>` 的数字渲染里没有 `{` 的语义。
    if len(args) >= 2:
        first = args[0]
        txt = _text_of(first) if isinstance(first, (Text, bytes, bytearray)) else ""
        if "{" in txt:
            res = stdlib.FNS["format"]["impl"](_format_args(args))
            try:
                sys.stdout.write(bytes(int(x) & 0xFF for x in res).decode("utf-8", "replace") + "\n")
                sys.stdout.flush()
            except (BrokenPipeError, OSError) as exc:
                raise InterpError(f"输出失败（{type(exc).__name__}）：{exc}") from None
            return 0

    parts = [_text_of(a) for a in args]
    try:
        sys.stdout.write(" ".join(parts) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, OSError) as exc:
        # **不让 Python 的 traceback 漏出来**：管道断 / 磁盘满时，
        # 用户该看到的是语言级的"输出失败"，不是实现的调用栈。
        raise InterpError(f"输出失败（{type(exc).__name__}）：{exc}") from None
    return 0


#: 内建整数二参函数。**只收这两个**——每加一个就是一个新的"可撒谎位置"，
#: 必须证明它「不改变阶」且「不引入任意代码」才收。
BUILTINS_INT2 = {
    "min": _builtin_min,
    "max": _builtin_max,
}

#: 单参内建（含 `print`）。
BUILTINS_INT1 = {
    "print": _builtin_print,
}


class Text(list):
    """**`vector<u8>` 的值**（字节序列）：打印时按 UTF-8 文本输出。

    为什么要单独一个类型：`Vec<u8>` 和"文本"在值层面相同，
    但 `print` 得知道该打 `104 105` 还是 `hi`。

    **它是 `vector<u8>` 的完整运行时形态，不是"字面量专用"**（2026-11-09）：
    值层没有静态类型，所以**产生 `vector<u8>` 的每条路径都要打上它**——
    字符串字面量、声明/赋值/返回（`_convert_to`）、内建返回
    （`_tag_stdlib_result` 的 `ret="vec-u8"` 一族）、容器元素。
    只给字面量打的话，`print(x)` 与 `print("{}", x)` 会给出两个结果。

    **打印规则**（`print` / `format` 共用，见 `_builtin_print`）：

    | 值 | 形态 | 打印 |
    |---|---|---|
    | `Text`（`vector<u8>` / 字符串） | `list` 子类 | UTF-8 文本（`hi`） |
    | 其它 `list`（`vector<u64>` / `vector<f64>` / `array`） | `list` | 空格分隔的数字（`104 105`） |

    判据是**值的类型标记**，不是"元素是否都 ≤ 255"——后者会把
    `vector<u64> {104, 105}` 也当成文本，那正是要避免的"同一个值两种结果"。
    """

    def text(self) -> str:
        return bytes(self).decode("utf-8", "replace")


# ------------------------------------------------------------------ 哈希容器的键
#
# **容器键怎么进 `dict` / `set`**（2026-11-12）：
#
#   `vector<u8>` 的值是 `Text`（`list` 子类），`list.__hash__ is None`。以前的
#   `UnorderedMapVal` 是裸 `dict`，`m.insert("a", 1)` 在 run 期直接
#   `TypeError: unhashable type: 'Text'`；所以 `check` 干脆静态拒绝。
#
#   现在键先包一层 `_HashKey`：
#     · `__hash__` = `stdlib.hash_key(v)`（容器 = FNV-1a 逐元素，**O(len)**）；
#     · `__eq__`   = **逐元素真比较**（哈希冲突时 `dict` 会调它，`vector<u8>` 比字节）。
#   这样复用 `dict` 的冲突处理，语义是"哈希 + 相等"，与 `unordered_map` 一致。
#
#   **结构体 / `map` / `set` 仍是静态拒绝**：前者没有字段哈希契约，后两者
#   只有对象身份、没有逐值相等（见 `checker._unhashable_key_reason`）。

_MISSING = object()


class _HashKey:
    """把"内容相等"的值包成可哈希的键；哈希走 `stdlib.hash_key`。"""

    __slots__ = ("v", "_h")

    def __init__(self, v):
        self.v = v
        self._h = stdlib.hash_key(v)

    def __hash__(self):
        return self._h

    def __eq__(self, other):
        if not isinstance(other, _HashKey):
            return NotImplemented
        # `list == list` 是逐元素比较（`Text` 也是 list）；不是对象身份。
        # `is` 短路与 Python `dict` 对同一对象的处理一致（`nan` 也能查回自己）。
        return self.v is other.v or self.v == other.v

    def __ne__(self, other):
        r = self.__eq__(other)
        return r if r is NotImplemented else (not r)


class _HashMapData:
    """`UnorderedMapVal.data` 的**薄适配层**：对外仍像 `dict`，键内部包 `_HashKey`。

    保留原 `dict` 的 API（`[]` / `in` / `pop` / `keys` / `items` / `values` /
    `clear` / `len` / 迭代）——解释器里所有既有调用点不用改语义。
    每个槽存 `(原键, 值)`，所以 `keys()` 拿回的是**原类型**（`Text` 仍是 `Text`）。
    """

    __slots__ = ("_d",)

    def __init__(self):
        self._d = {}

    def __len__(self):
        return len(self._d)

    def __setitem__(self, key, val):
        self._d[_HashKey(key)] = (key, val)

    def __getitem__(self, key):
        return self._d[_HashKey(key)][1]

    def __contains__(self, key):
        return _HashKey(key) in self._d

    def __delitem__(self, key):
        del self._d[_HashKey(key)]

    def get(self, key, default=None):
        cell = self._d.get(_HashKey(key))
        return default if cell is None else cell[1]

    def pop(self, key, default=None):
        cell = self._d.pop(_HashKey(key), _MISSING)
        return default if cell is _MISSING else cell[1]

    def keys(self) -> list:
        return [cell[0] for cell in self._d.values()]

    def values(self) -> list:
        return [cell[1] for cell in self._d.values()]

    def items(self) -> list:
        return [(cell[0], cell[1]) for cell in self._d.values()]

    def __iter__(self):
        return iter(self.keys())

    def clear(self) -> None:
        self._d.clear()


class _HashSetData:
    """`UnorderedSetVal.data` 的薄适配层（像 `set`，键内部包 `_HashKey`）。"""

    __slots__ = ("_d",)

    def __init__(self):
        self._d = {}

    def __len__(self):
        return len(self._d)

    def add(self, key) -> None:
        self._d[_HashKey(key)] = key

    def discard(self, key) -> None:
        self._d.pop(_HashKey(key), None)

    def __contains__(self, key):
        return _HashKey(key) in self._d

    def __iter__(self):
        return iter(self._d.values())

    def clear(self) -> None:
        self._d.clear()


class _AvlNode:
    """有序容器的一个节点：键 + 值 + 左右子树 + 高度。"""

    __slots__ = ("key", "val", "left", "right", "height")

    def __init__(self, key, val):
        self.key = key
        self.val = val
        self.left = None
        self.right = None
        self.height = 1


class OrderedTree:
    """**只依赖全序（`<`）的平衡二叉搜索树**（AVL，严格平衡）。

    为什么不用 Python `dict` / `set`：

    - 它们要**可哈希**的键。字符串字面量是 `Text(list)`，`Vec<u8>` 也是
      `list`——**不可哈希**，所以 `map<Vec<u8>, u64>` 以前在 `insert` 时
      直接 `TypeError`。而 `map` 的契约只要求**全序**，不要求哈希。
    - `dict` 的查找是「平均 O(1)、**最坏 O(n)**」。契约写的是 **O(log n) 上界**；
      最坏 O(n) 会突破 κ（低估比宽松危险）。AVL 的查找/插入/删除都是
      **最坏 O(log n)**，与成本域的 `log2(n)` 完全一致。

    **键序**：中序遍历即键序（`Text` / `Vec<u8>` 按键的字典序比较，
    与 `std::map<vector<u8>, …>` 一致）。删除是**真删节点 + 回溯再平衡**，
    不用墓碑（墓碑会让树高随历史插入数增长，log(总节点) 会超过 log(当前长度)）。
    """

    __slots__ = ("root", "n")

    def __init__(self, items=None):
        self.root = None
        self.n = 0
        if items:
            pairs = items.items() if isinstance(items, dict) else items
            for k, v in pairs:
                self.put(k, v)

    # ---- 高度 / 旋转 / 再平衡（AVL）
    @staticmethod
    def _h(node) -> int:
        return node.height if node is not None else 0

    @classmethod
    def _upd(cls, node) -> None:
        node.height = 1 + max(cls._h(node.left), cls._h(node.right))

    @staticmethod
    def _rot_left(x):
        y = x.right
        x.right = y.left
        y.left = x
        OrderedTree._upd(x)
        OrderedTree._upd(y)
        return y

    @staticmethod
    def _rot_right(y):
        x = y.left
        y.left = x.right
        x.right = y
        OrderedTree._upd(y)
        OrderedTree._upd(x)
        return x

    @classmethod
    def _balance(cls, node):
        cls._upd(node)
        bf = cls._h(node.left) - cls._h(node.right)
        if bf > 1:
            if cls._h(node.left.left) < cls._h(node.left.right):
                node.left = cls._rot_left(node.left)
            return cls._rot_right(node)
        if bf < -1:
            if cls._h(node.right.right) < cls._h(node.right.left):
                node.right = cls._rot_right(node.right)
            return cls._rot_left(node)
        return node

    # ---- 查找
    def _find(self, key):
        cur = self.root
        while cur is not None:
            if key < cur.key:
                cur = cur.left
            elif cur.key < key:
                cur = cur.right
            else:
                return cur
        return None

    def has(self, key) -> bool:
        return self._find(key) is not None

    def get(self, key, default=None):
        node = self._find(key)
        return node.val if node is not None else default

    # ---- 插入 / 删除（都是最坏 O(log n)）
    def put(self, key, val) -> int:
        """插入或覆盖：**1 = 新插入，0 = 覆盖已有键**。"""
        if self.root is None:
            self.root = _AvlNode(key, val)
            self.n = 1
            return 1
        path = []
        cur = self.root
        while True:
            path.append(cur)
            if key < cur.key:
                if cur.left is None:
                    cur.left = _AvlNode(key, val)
                    self.n += 1
                    break
                cur = cur.left
            elif cur.key < key:
                if cur.right is None:
                    cur.right = _AvlNode(key, val)
                    self.n += 1
                    break
                cur = cur.right
            else:
                cur.val = val
                return 0
        for i in range(len(path) - 1, -1, -1):
            node = path[i]
            fixed = self._balance(node)
            if i == 0:
                self.root = fixed
            else:
                parent = path[i - 1]
                if parent.left is node:
                    parent.left = fixed
                else:
                    parent.right = fixed
        return 1

    def _detach(self, path, child) -> None:
        if not path:
            self.root = child
            return
        parent, side = path[-1]
        if side == "L":
            parent.left = child
        else:
            parent.right = child

    def _rebalance_path(self, path) -> None:
        for i in range(len(path) - 1, -1, -1):
            parent, side = path[i]
            fixed = self._balance(parent)
            if i == 0:
                self.root = fixed
            else:
                gp, gside = path[i - 1]
                if gside == "L":
                    gp.left = fixed
                else:
                    gp.right = fixed

    def remove(self, key) -> int:
        """删除：**1 = 删掉了，0 = 本来就不在**。真删节点，不留墓碑。"""
        path = []
        cur = self.root
        while cur is not None:
            if key < cur.key:
                path.append((cur, "L"))
                cur = cur.left
            elif cur.key < key:
                path.append((cur, "R"))
                cur = cur.right
            else:
                break
        if cur is None:
            return 0
        if cur.left is not None and cur.right is not None:
            # 用**右子树最小节点（后继）**的内容替换，再物理删掉后继
            spath = path + [(cur, "R")]
            succ = cur.right
            while succ.left is not None:
                spath.append((succ, "L"))
                succ = succ.left
            cur.key, cur.val = succ.key, succ.val
            self._detach(spath, succ.right)
            self._rebalance_path(spath)
        else:
            self._detach(path, cur.left if cur.left is not None else cur.right)
            self._rebalance_path(path)
        self.n -= 1
        return 1

    # ---- 有序枚举（中序 = 键序）
    def keys(self) -> list:
        out = []
        stack = []
        cur = self.root
        while stack or cur is not None:
            while cur is not None:
                stack.append(cur)
                cur = cur.left
            cur = stack.pop()
            out.append(cur.key)
            cur = cur.right
        return out

    def items(self) -> list:
        out = []
        stack = []
        cur = self.root
        while stack or cur is not None:
            while cur is not None:
                stack.append(cur)
                cur = cur.left
            cur = stack.pop()
            out.append((cur.key, cur.val))
            cur = cur.right
        return out

    def __len__(self) -> int:
        return self.n

    def __repr__(self) -> str:
        return f"ordered[{self.n}]"


class SetVal:
    """`Set<T>` 的值：**有序集合**（元素序，确定可复现）。

    和 `MapVal` 同一条理由：哈希的查找最坏 O(n)、顺序不可复现，
    而契约要的是确定上界。集合的成本是 **O(log n) 确定**，顺序是**元素序**。
    底层与 `MapVal` 共用 `OrderedTree`：元素只存成键（值恒为 1），
    **元素只需要全序（`<`）**——所以 `set<Vec<u8>>` 也能用，不再要求可哈希。
    """

    __slots__ = ("_t", "elem_ty")

    def __init__(self, items=None, elem_ty=None):
        self._t = OrderedTree()
        self.elem_ty = elem_ty
        for x in (items or []):
            self.add(x)

    def _k(self, x):
        return _convert_to(x, self.elem_ty) if self.elem_ty is not None else x

    def add(self, x) -> int:
        return self._t.put(self._k(x), 1)

    def discard(self, x) -> int:
        return self._t.remove(self._k(x))

    def values(self) -> list:
        """元素序（= 键序）。"""
        return self._t.keys()

    def sorted_items(self):
        return self._t.keys()

    def clear(self) -> None:
        """`clear()`：清空（C++ 的 `s.clear()`）。"""
        self._t.root = None
        self._t.n = 0

    def __contains__(self, x) -> bool:
        return self._t.has(self._k(x))

    def __len__(self):
        return len(self._t)

    def __repr__(self):
        return f"set[{len(self._t)}]"


class UnorderedMapVal:
    """`unordered_map<K,V>`：**承诺最坏 O(n)** 的键值容器。

    **迭代顺序未指定**——不要依赖它，也不要把 `map` 的"键序、可复现"套过来。
    它存在的意义是"和 C++ 对齐"，在我们的成本模型里**每一项都比 `map` 差**
    （O(n) vs O(log n)）。这一点在语法清单里明写。

    **容器键**（2026-11-12）：`vector<T>` / `array<T,N>` 可以做键（`Text` 就是
    `vector<u8>`）。`data` 是 `_HashMapData`：哈希走 `stdlib.hash_key`（**O(键长)**），
    冲突后按**逐元素相等**比较。所以单次 `get/insert/remove` 是
    **O(n + 键长)**，不是 O(1)——κ 侧对容器键加了 `n(键)` 项。
    """

    __slots__ = ("data", "val_ty")

    def __init__(self, val_ty=None):
        self.data = _HashMapData()
        # **值类型**（2026-11-12）：`um[k]` 缺键要插 `V{}`，没有它只能插 0。
        # 与 `MapVal.val_ty` 同一口径。
        self.val_ty = val_ty

    def __len__(self):
        return len(self.data)

    def __repr__(self):
        return f"unordered_map[{len(self.data)}]"


class UnorderedSetVal:
    """`unordered_set<T>`：同上，**顺序未指定**，成本 O(n)。

    元素可以是 `vector<T>` / `array<T,N>`（见 `UnorderedMapVal` 的容器键说明）。
    """

    __slots__ = ("data",)

    def __init__(self):
        self.data = _HashSetData()

    def __len__(self):
        return len(self.data)

    def __repr__(self):
        return f"unordered_set[{len(self.data)}]"


class StackVal(list):
    """`stack<T>`：**LIFO 薄封装**，底层就是普通向量。

    C++ 的 `std::stack` 同样是**容器适配器**（默认包 `std::deque`），不是容器。
    成本全部 O(1)，没有新的内存语义。
    """

    __slots__ = ()

    def __repr__(self):
        return f"stack[{len(self)}]"


class QueueVal(list):
    """`queue<T>`：**FIFO 薄封装**，底层是向量 + 读游标（摊销 O(1)）。"""

    __slots__ = ()

    def __repr__(self):
        return f"queue[{len(self)}]"


class MapVal:
    """`Map<K, V>` 的值：**有序映射**（键序，确定可复现）。

    **只依赖全序（`<`），不依赖哈希**：底层是 `OrderedTree`（AVL 平衡二叉树）。
    以前这里直接包 Python `dict`，有两个后果：

    1. `map<Vec<u8>, T>` 用不了——`Text` 是 `list` 的子类，**不可哈希**，
       `m.insert("a", 1)` 在 run 期抛 `TypeError`；
    2. `dict` 的查找是「平均 O(1)、**最坏 O(n)**」，而契约写的是 **O(log n)**——
       最坏情况会**突破 κ**（低估比宽松危险）。

    现在查找/插入/删除都是**最坏 O(log n)**，键只需要 `<`（`Vec<u8>` 按键的
    字典序比较，与 `std::map<vector<u8>, …>` 一致），迭代顺序是**键序**。
    """

    __slots__ = ("_t", "key_ty", "val_ty")

    def __init__(self, items=None, key_ty=None, val_ty=None):
        self._t = OrderedTree()
        self.key_ty = key_ty
        self.val_ty = val_ty
        if items:
            pairs = items.items() if isinstance(items, dict) else items
            for k, v in pairs:
                self.put(k, v)

    def _k(self, key):
        return _convert_to(key, self.key_ty) if self.key_ty is not None else key

    def _v(self, val):
        return _convert_to(val, self.val_ty) if self.val_ty is not None else val

    def put(self, key, val=0) -> int:
        """插入或覆盖：**1 = 新插入，0 = 覆盖已有键**。"""
        return self._t.put(self._k(key), self._v(val))

    def get(self, key, default=None):
        return self._t.get(self._k(key), default)

    def has(self, key) -> bool:
        return self._t.has(self._k(key))

    def remove(self, key) -> int:
        return self._t.remove(self._k(key))

    def keys(self) -> list:
        """**键序**（不是插入序、不是哈希序）。"""
        return self._t.keys()

    def items(self) -> list:
        return self._t.items()

    def key_at(self, i: int):
        """按键序取第 i 个键（O(n)；仅调试/脚本用）。"""
        return self._t.keys()[i]

    def keys_sorted(self):
        return self._t.keys()

    def clear(self) -> None:
        """`clear()`：清空（C++ 的 `m.clear()`）。"""
        self._t.root = None
        self._t.n = 0

    def __contains__(self, key) -> bool:
        return self._t.has(self._k(key))

    def __getitem__(self, key):
        node = self._t._find(self._k(key))
        if node is None:
            raise KeyError(key)
        return node.val

    def __setitem__(self, key, val) -> None:
        self._t.put(self._k(key), self._v(val))

    def __len__(self):
        return len(self._t)

    def __repr__(self):
        return f"map[{len(self._t)}]"


class FixedList(list):
    """`FixedVec<T, N>` 的值：**定长数组**，push 超过 N 必须报错。

    **为什么必须强制**：固定容量是 `FixedVec` 存在的全部意义。
    不检查它，`FixedVec<T,N>` 就只是 `Vec<T>` 的改名。
    """

    def __init__(self, capacity: int, iterable=()):
        super().__init__(iterable)
        self.capacity = capacity

    def __repr__(self):
        return f"FixedVec[{len(self)}/{self.capacity}]"


class FnVal:
    """**函数值**：把具名函数当参数传（函数类型 slice 1）。

    **只包名字，不包代码**——本语言没有闭包，函数值就是"一个函数的名字"。
    这样：κ 能算（名字 → 声明 → κ），类型能查（签名对得上才传得进来）。
    """

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return f"FnVal({self.name})"


class StructVal:
    """结构体值：`Stats { mean: 1, variance: 2 }` → StructVal("Stats", {...})。"""

    __slots__ = ("type_name", "fields")

    def __init__(self, type_name: str, fields: Dict[str, Any]):
        self.type_name = type_name
        self.fields = dict(fields)

    def __repr__(self):
        inner = ", ".join(f"{k}: {v!r}" for k, v in self.fields.items())
        return f"{self.type_name} {{ {inner} }}"

    def __eq__(self, other):
        return (isinstance(other, StructVal) and self.type_name == other.type_name
                and self.fields == other.fields)


class PtrVal:
    """**指针值**：指向一块**存储**（不是值的拷贝）——本语言唯一允许别名的地方。

    ## 为什么是一个独立类型，而不是"直接存 Python 对象"

    运行时值域里，容器 / 结构体等**拥有型**值在七个拷贝点会被 `_deep_copy`
    深拷贝（值语义）。指针必须**按标量**穿过这七个点：拷的是地址，不深拷
    被指对象。成本 0 与成本域同口径：`cost_interp._copy_class` 对 `type-ptr`
    落到 `("scalar", 0)`（它不是 `type-name`，落进"认不出的值类型"那条兜底，
    与引用形参的 `("scalar", 0)` 是同一条）。
    所以它得是一个"看着像标量"的独立类型：`_copyable()` 不为它返回 True，
    `_deep_copy()` 的兜底原样返回——于是 `S* r = q;` 得到的就是同一个地址。

    ## 指向什么（`kind`）
    | kind | `target` | `key` | 出现在 |
    |---|---|---|---|
    | `cell`  | 局部/形参所在的 `dict`（环境单元） | 变量名 | `&x` |
    | `elem`  | 容器（`list` / `FixedList` / `Text`） | 下标 | `&xs[i]` |
    | `field` | `StructVal` | 字段名 | `&s.f` |

    `get()` / `set()` 是**唯一**的读写口：读出来的就是那块存储里的对象本身
    （所以 `q->f = 9` 改到的就是 `s.f`）；写进去的值由调用方按声明类型转换好。

    ## 空指针就是 `None`

    `nullptr` / `T* p;` 的值是 `None`——它天然是假值（`if (p)` 直接可用）、
    天然是标量，`_convert_to` / `_copyable` 都不用为它开特例。
    解引用 `None` 由 `_deref_read` 抛 `E-Pointer-NullDeref`，**不许让宿主崩**。
    """

    __slots__ = ("kind", "target", "key")

    def __init__(self, kind: str, target: Any, key: Any):
        self.kind = kind
        self.target = target
        self.key = key

    # ---- 读写那块存储
    def get(self):
        if self.kind == "cell":
            return self.target[self.key]
        if self.kind == "elem":
            return self.target[self.key]
        if self.kind == "field":
            return self.target.fields[self.key]
        raise InterpError(f"指针的存储种类未知：{self.kind}")

    def set(self, val) -> None:
        if self.kind == "cell":
            self.target[self.key] = val
            return
        if self.kind == "elem":
            self.target[self.key] = val
            return
        if self.kind == "field":
            self.target.fields[self.key] = val
            return
        raise InterpError(f"指针的存储种类未知：{self.kind}")

    # ---- 指针是标量：真值 / 相等都只看"地址"
    def __bool__(self) -> bool:
        return True                     # 空指针是 `None`，走不到这里

    def __eq__(self, other):
        if other is None:               # `p == nullptr`
            return False
        if isinstance(other, PtrVal):
            # 同一块存储的两个地址相等（`&s == q` / `r == q`）。
            return (self.kind == other.kind and self.target is other.target
                    and self.key == other.key)
        return NotImplemented

    def __ne__(self, other):
        eq = self.__eq__(other)
        return eq if eq is NotImplemented else not eq

    def __hash__(self):
        return hash((self.kind, id(self.target), self.key))

    def __repr__(self):
        return f"PtrVal({self.kind}:{self.key!r})"


class TypeRef:
    """类型引用（`FixedVec<T,N>` 出现在表达式位置时）。只用于静态调用。"""

    __slots__ = ("type_name", "member")

    def __init__(self, type_name: str, member: str = ""):
        self.type_name = type_name
        self.member = member

    def __repr__(self):
        return f"{self.type_name}::{self.member}"


# ---------------------------------------------------------------- 值语义拷贝
#
# **七个拷贝位置**全部落在这里（对齐 C++17；成本域 `df5a898` / 设计 §4.1 的
# `_k_stmt_*` 就是按 C++ 记的）：
#
#   1. `T a = b;`（b 是**左值**）→ 拷贝构造；b 是 prvalue → 不拷贝（强制省略）
#   2. `a = b;`（b 是左值）→ 拷贝赋值；prvalue → 移动
#   3. 按值形参 `f(T v)`（实参是**左值**）→ 拷贝；实参是 prvalue → 移动
#   4. `return <借用值>`（`const&` 形参 / 元素 / 字段 / const 局部 / 全局）
#      → **拷贝**（C++ 返回类型 `T`、表达式左值 ⇒ 拷贝构造）
#   5. 入容器：`push` / `insert` / `vec_push` / `map_put` / `hmap_put` / `set_add`
#      / `deque_push_*`，以及 `m[k] = v` 下标写（实参是左值）→ 拷贝
#   6. `for (x in xs)` 的**元素绑定** → 每轮一次拷贝（元素是标量 → 0）
#   7. `S{a}` 聚合初始化的成员（字段是容器且值是左值）→ 拷贝
#
# **不拷贝**的位置同样是 C++ 语义，别顺手改成拷贝：
#   `return 非 const 局部 / 按值形参;`（隐式移动，C++11 两阶段重载决议）、
#   `return T{...};`（prvalue，强制省略）、`T&` / `const T&` 形参（别名）、
#   `-> T&` 返回（别名）、所有 prvalue 的初始化/返回。
#
# 值类别**没有**静态表：`typecheck.py` 的类型不区分左右值，成本域的
# `_value_class` 是静态分析用的。运行期按 **AST 节点种类**判：
# `path` / `index` / `deref`（以及基址是左值的 `field`）= 左值；
# `call` / 字面量 / `list-init` / `struct-literal` = prvalue；
# `method-call` 里**返回容器内部元素**的那几个（`at`/`get`/`front`…）= 左值。

#: 产出左值的表达式节点种类（其余一律按 prvalue 处理）。
_LVALUE_KINDS = frozenset(("path", "index", "deref"))

#: 返回容器**内部元素**（借用）的方法：C++ 里返回引用 ⇒ 左值 ⇒ 消费点拷贝。
#: 与 `cost_interp._BORROW_METHODS` **是同一份判定**（那边 `_value_class`）。
_BORROW_METHODS = frozenset(("at", "get", "front", "back", "top", "peek",
                             "first", "last"))


def _is_lvalue(n) -> bool:
    """这个**表达式节点**求出来的是不是左值（＝别人已经持有的对象）。

    只有左值才需要拷贝：prvalue 是现造的值，没有第二个持有者，
    复制它等于白花一次 O(n)。

    **`xs.at(i)` / `m.get(k)` 是左值**：它们返回容器内部元素（C++ 里是
    `T&` / `const T&`）。以前这里把 `method-call` 一律当 prvalue，于是
    `return box.get(1);` / `T a = m.get(k);` 都退化成别名——与成本域
    `_value_class`（`_BORROW_METHODS` → lvalue）不一致。
    """
    if n is None:
        return False
    k = getattr(n, "kind", "")
    if k == "paren":                                   # `(b)` 与 `b` 同类
        return bool(n.children) and _is_lvalue(n.children[0])
    if k == "field":                                   # `p.x`：跟基址；`Type::m` 不是值
        base = n.children[0] if n.children else None
        if base is not None and getattr(base, "kind", "") == "type-name":
            return False
        return _is_lvalue(base)
    if k == "method-call":
        return (n.name or "").split(".")[-1] in _BORROW_METHODS
    return k in _LVALUE_KINDS


def _copyable(v) -> bool:
    """需要按值拷贝的运行时形态：容器 / 结构体 / 枚举载荷。

    标量（`U64` / `int` / `float` / `bool`）本身就是值类型，`_deep_copy` 原样返回，
    所以这个判断只用来**跳过不必要的递归**。

    **标量快路径放最前**：`_store` 的每次赋值都会问一次，循环里的
    `i = i + 1` 不能为此付一串 isinstance。
    """
    if isinstance(v, (int, float, bool)):
        return False
    return isinstance(v, (Text, FixedList, StackVal, QueueVal, list, tuple,
                          MapVal, SetVal, UnorderedMapVal, UnorderedSetVal,
                          StructVal, EnumVal, Ok, Err,
                          # **内建那一侧的容器**（`set_new` / `map_new` /
                          # `deque_new` / `text_split` / `args` 产生的是
                          # `stdlib.py` 自己的类，与上面的 `MapVal` 不是同一个类）。
                          # 不列出来的话，`Set<T> t = s;`（`s` 来自 `set_new`）
                          # 仍然是别名——换个构造函数就换个语义是最坏的一种不一致。
                          stdlib.MapVal, stdlib.DequeVal,
                          stdlib.SplitVal, stdlib.ArgsVal))


def _copy_units(v):
    """**一次拷贝**的 `(ops, allocation)` —— 逐项对齐 `cost_interp._copy_cost`。

    | 运行时形态 | ops（time） | allocation | 对齐的 `_copy_cost` 行 |
    |---|---|---|---|
    | `vector` / `Text` / `stack` / `queue` / `deque` | `n + 1` | 1 | `time = n + 1`；`return time, Cost.num(1)` |
    | `map` / `set` / `unordered_*`（含 `HashMap`） | `n` | `n` | `_ASSOC_HEADS`/`_SET_HEADS`：`time = n`，alloc 同 `n` |
    | `array<T,N>` / `FixedVec` | `N`（容量） | 0 | `c == "array"`：`Cost.num(N), Cost.num(0)` |
    | `struct` | 字段数 `k` | 0 | `c == "struct"`：`Cost.num(k), Cost.num(0)` |
    | 标量 / enum / `Ok` / `Err` | 0 | 0 | `c == "scalar"`：**κ 记 0**（设计 §9.2 登记口径） |

    **为什么标量是 0**：成本域的实现口径把标量/枚举拷贝记 0（`_copy_cost`
    的 `scalar` 分支），这是"golden 只变 4 个"的前提。ops 必须用**同一口径**，
    否则「κ ÷ 实测」比的就不是同一件事。

    **为什么只算外层、不把内层元素再算一遍**：`_copy_cost` 对整个拷贝表达式
    只按**外层类型**记一次（嵌套 owning 的 time 是 ⊤，数值上跳过）。ops 侧
    同样只在**每个拷贝点**记一次；否则 `map<vector<u8>,u64>` 的键就会被
    记两遍（外层 n 次节点 + 每个键的 n+1），反而比 κ 大——那不是"更精确"，
    是把两种口径混在一起。
    """
    if isinstance(v, Text):                     # vector<u8>
        return len(v) + 1, 1
    if isinstance(v, FixedList):                # array<T,N>（定长，栈上）
        return (v.capacity if v.capacity is not None else len(v)), 0
    if isinstance(v, (StackVal, QueueVal)):     # 向量适配器
        return len(v) + 1, 1
    if isinstance(v, list):                     # vector<T>
        return len(v) + 1, 1
    if isinstance(v, tuple):
        # **元组**（2026-11-12）：成本域里没有 tuple 类别（`_copy_class` 落到
        # `("scalar", 0)`），所以这里必须**同一口径**记 0，否则实测 ops 会
        # 大于 κ（κ 是上界，破界比"少记"危险）。内层容器的那一份拷贝也
        # 不在这里再记一遍——与容器"只按外层记一次"同一条规矩。
        return 0, 0
    if isinstance(v, (MapVal, SetVal)):         # 有序 map/set：结构克隆
        n = len(v)
        return n, n
    if isinstance(v, (UnorderedMapVal, UnorderedSetVal)):
        n = len(v.data)
        return n, n
    if isinstance(v, stdlib.MapVal):            # HashMap（unordered_map）
        return v.n, v.n
    if isinstance(v, stdlib.DequeVal):          # deque：向量适配器那一档
        return v.n + 1, 1
    if isinstance(v, stdlib.SplitVal):
        return len(v.parts) + 1, 1
    if isinstance(v, stdlib.ArgsVal):
        return len(v.items) + 1, 1
    if isinstance(v, StructVal):                # 逐成员 k
        return len(v.fields), 0
    return 0, 0                                 # 标量 / enum / Ok / Err


#: 「入容器」类内建的**值实参下标**：实参是左值 → 拷贝（与 `.push` / `.insert`
#: 同一规则）。`push_heap` 在自己那条分支里处理（它不走通用内建分派）。
_STORE_BUILTIN_ARGVALS = {
    "vec_push": (1,),
    "deque_push_back": (1,),
    "deque_push_front": (1,),
    "set_add": (1,),
    "map_put": (1, 2),
    "hmap_put": (1, 2),
}


def _copy_avl(node):
    """`OrderedTree` 的**结构拷贝**：O(n) 一次走完，不是 n 次 O(log n) 的 `put`。

    为什么在意：`std::map` 的拷贝构造是 O(n)，n 次插入是 O(n log n)——
    后者会让实测 ops 超过 κ（κ 是上界，不允许被实现细节撑破）。
    """
    if node is None:
        return None
    nn = _AvlNode(_deep_copy(node.key), _deep_copy(node.val))
    nn.height = node.height
    nn.left = _copy_avl(node.left)
    nn.right = _copy_avl(node.right)
    return nn


def _deep_copy(v):
    """递归按值拷贝；**标量直接返回原值**（不复制）。

    `Text` / `FixedList` / `StackVal` / `QueueVal` 都是 `list` 的子类，
    判断顺序必须**先具体后一般**，否则 `vector<u8>` 会被拷成普通 list
    （字符串退化成数字列表、定长数组丢掉容量）。
    """
    if isinstance(v, Text):
        return Text(_deep_copy(x) for x in v)
    if isinstance(v, FixedList):
        return FixedList(v.capacity, [_deep_copy(x) for x in v])
    if isinstance(v, StackVal):
        return StackVal(_deep_copy(x) for x in v)
    if isinstance(v, QueueVal):
        return QueueVal(_deep_copy(x) for x in v)
    if isinstance(v, list):
        return [_deep_copy(x) for x in v]
    if isinstance(v, tuple):
        # **元组也是值**（2026-11-12）：以前 `_deep_copy` 不认 tuple，于是
        # `(容器, u64) a = b;` 是**别名**——`a.0.push(…)` 会改到 `b.0`。
        # `map` 的 range-for 元素是 `(键, 值)` 二元组，按值迭代
        #（`for (auto kv : m)`）必须真的拷一份，否则改 `kv.1` 的容器会
        # 悄悄改到表里的值（C++ 里按值迭代改不到表）。
        return tuple(_deep_copy(x) for x in v)
    if isinstance(v, MapVal):
        out = MapVal(key_ty=v.key_ty, val_ty=v.val_ty)
        out._t.root = _copy_avl(v._t.root)
        out._t.n = v._t.n
        return out
    if isinstance(v, SetVal):
        out = SetVal(elem_ty=v.elem_ty)
        out._t.root = _copy_avl(v._t.root)
        out._t.n = v._t.n
        return out
    if isinstance(v, UnorderedMapVal):
        out = UnorderedMapVal()
        for k, x in v.data.items():
            out.data[_deep_copy(k)] = _deep_copy(x)
        return out
    if isinstance(v, UnorderedSetVal):
        out = UnorderedSetVal()
        for x in v.data:
            out.data.add(_deep_copy(x))
        return out
    if isinstance(v, StructVal):
        return StructVal(v.type_name,
                         {k: _deep_copy(x) for k, x in v.fields.items()})
    if isinstance(v, EnumVal):
        payload = v.payload
        if isinstance(payload, tuple):
            payload = tuple(_deep_copy(x) for x in payload)
        else:
            payload = _deep_copy(payload)
        return EnumVal(v.type_name, v.variant, payload)
    if isinstance(v, Ok):
        return Ok(_deep_copy(v.v))
    if isinstance(v, Err):
        return Err(_deep_copy(v.e))
    # ---- `stdlib.py` 那一侧的容器（与上面的同名类**不是同一个类**）
    if isinstance(v, stdlib.MapVal):
        out = stdlib.MapVal(v.cap)
        out.cap = v.cap
        out.ks = [None if k is None else _deep_copy(k) for k in v.ks]
        out.vs = [_deep_copy(x) for x in v.vs]
        out.n = v.n
        return out
    if isinstance(v, stdlib.DequeVal):
        out = stdlib.DequeVal(v.cap)
        out.buf = [_deep_copy(x) for x in v.buf]
        out.head = v.head
        out.n = v.n
        return out
    if isinstance(v, stdlib.SplitVal):
        return stdlib.SplitVal([_deep_copy(x) for x in v.parts])
    if isinstance(v, stdlib.ArgsVal):
        return stdlib.ArgsVal([_deep_copy(x) for x in v.items])
    return v


_TABLE_LINE = re.compile(r"-\s*\{lo:\s*(\d+),\s*hi:\s*(\d+),\s*candidate:\s*([A-Za-z_][\w]*)\}")


def load_dispatch_table(path: Optional[str]):
    """读 `<模块>.dispatch.yaml` → {label: [(lo, hi, 候选名), ...]}。

    **为什么解释器要读它**：分档表如果只给人和门禁看，那"按段动态取最优"
    在运行期就是一句空话——**表对不对，也没有任何运行结果能反驳它**。
    读表之后，同一个程序"按表跑"和"逐候选实测"可以互相对账，缺口才闭得上。

    无文件 / 解析失败 → 返回 `{}`（调用方退化为"第一个实现"，并打警告）。
    """
    if not path or not os.path.exists(path):
        return {}
    out = {}
    label = None
    try:
        for raw in open(path, encoding="utf-8"):
            line = raw.rstrip("\n")
            # 两种写法都收：`label: x`（measure 生成的格式）与 `- label: x`（列表格式）
            m = re.match(r"\s*(?:-\s*)?label:\s*([A-Za-z_][\w]*)", line)
            if m:
                label = m.group(1)
                out.setdefault(label, [])
                continue
            m2 = _TABLE_LINE.search(line)
            if m2 and label:
                out[label].append((int(m2.group(1)), int(m2.group(2)), m2.group(3)))
    except Exception:                                  # noqa: BLE001
        return {}
    return {k: v for k, v in out.items() if v}


def _size_of_args(args):
    """用**第一个实参的规模**当分档依据。

    | 实参 | 规模 |
    |---|---|
    | 列表 / 元组 / 字符串 | 长度 |
    | 整数 | 它本身 |
    其它 → `None`（不查表，退化为第一个实现）。
    """
    if not args:
        return None
    a = args[0]
    if isinstance(a, (list, tuple, str, bytes)):
        return len(a)
    if isinstance(a, int) and not isinstance(a, bool):
        return int(a)
    return None


class ExitRequest(Exception):
    """`exit(code)` 用：**正常的控制流终止**，不是错误。"""

    def __init__(self, code: int):
        self.code = int(code)
        super().__init__(f"exit({code})")


class InterpError(Exception):
    pass


class InterpRequiresViolated(InterpError):
    """`requires` 前提不成立。

    为什么单独成类（2026-11-11）：有限域穷举里「输入不满足 requires」**不是**
    函数失败——那条输入本来就不在契约的适用范围内，必须**排除**而不是记成
    反例。以前的实现只抛一个通用 `InterpError`，穷举只能靠匹配中文消息来区分，
    那是把文案当协议。消息文本保持逐字不变，只是换了异常类。

    `fn` 是**哪一层**的 requires 不成立：穷举只排除**入口函数自己**的
    requires；调用链里别的函数的 requires 被违反是运行期错误（记【未证】），
    不能拿它来缩小入口的证明范围。
    """

    def __init__(self, message: str, fn: str = ""):
        super().__init__(message)
        self.fn = fn or ""


class InterpBudgetExceeded(InterpError):
    """步数超限：**不一定是死循环**。

    可能是死循环、算法本身指数/超多项式、或单纯输入太大。文案由
    `Interp._budget_message` 按证据分类生成（见那里的优先级说明）。
    """


class _HostRecursionLimit(Exception):
    """**宿主** Python 调用栈被打穿（不是语言级诊断；`Interp.call` 会翻译它）。

    为什么要包一层：`_run_fn` 是唯一知道"崩在哪个函数、栈多深"的地方，
    但那时异常已经在展开中。外层 `call()` 接住它，用这里的 `fn` / `depth`
    把 `E-Recursion-HostLimit` 写成"一句话 + 一个函数名 + 一个数字"，
    而不是把 `RecursionError: maximum recursion depth exceeded` 甩给用户。

    `depth` 是**语言调用栈的深度**（`len(self._fn_stack)`），不是"该函数第几层"：
    没有声明的函数压根不计数（`_gate_of`），报"第 0 层"是假话。
    """

    def __init__(self, fn: str = "", depth: int = 0):
        self.fn = fn or ""
        self.depth = int(depth)
        super().__init__(f"{self.fn}@{self.depth}")


#: **`repeats` 的运行期诊断码**（2026-11-09）。独立字面量是有意的：
#: `tools/check_diag_codes.py` 按 `"E-..."`（引号紧跟码）扫源码，嵌在
#: f-string 中间、后面紧跟中文冒号的码扫不到，会被当成"只在文档里"。
E_REPEATS_EXCEEDED = "E-Repeats-Exceeded"
E_REPEATS_UNCHECKABLE = "E-Repeats-Uncheckable"

#: **`recursion depth <= N` 的运行期诊断码**（2026-11-10）。
#:
#: 以前这条声明**只进 κ**：`.tu` 写 `recursion depth <= 8`，实际却能跑 75 层，
#: 于是 κ(75) = 117 而实测 ops = 831 —— **上界被越过**（`_verify/report.md` K3）。
#: 现在它和 `repeats` / `guarantees` 一样是**运行期断言**：超过声明就抛诊断。
E_RECURSION_DEPTH_EXCEEDED = "E-Recursion-Depth-Exceeded"
#: 声明读不出正整数（`recursion depth <= 0` / 名字）：**不可校验的声明不许静默**。
E_RECURSION_UNCHECKABLE = "E-Recursion-Uncheckable"
#: 宿主 Python 的调用栈被用光：**必须翻译成语言诊断**，不许把 `RecursionError`
#: 透给用户（那是一句"maximum recursion depth exceeded"，没有码、没有函数名）。
E_RECURSION_HOST_LIMIT = "E-Recursion-HostLimit"

#: **解引用空指针的运行期诊断码**（2026-11-10，指针接线）。
#:
#: 与静态那条**同名但不同源**，两条都要有：
#:   · 静态（`typecheck._deref_expr`）：**可证**的空解引用（直线路径上最后一次
#:     赋值就是 `nullptr`）——在 `check` 阶段就报，不必跑；
#:   · 运行期（本文件的 `_deref_read`）：**真的**解引用了空指针（`p` 来自形参 /
#:     循环 / 容器，静态证不出来）——跑到那里才报。
#: 同一个程序里两条可能**同时**出现（静态先报，`run` 也真的跑到了）。
#: 无论哪条都不能让宿主崩：Python 侧 `None` 上取字段是 `TypeError`，
#: 那是宿主异常，不是语言诊断。
E_POINTER_NULL_DEREF = "E-Pointer-NullDeref"

#: 每层语言递归**实测**烧掉的宿主帧数：`chain` 13.3（1000 帧 ⇒ 75 层）、
#: `_tier_b` 的 `rec_tree` 9.3。取 24 是给"每层表达式更深"的情形留余量。
_HOST_FRAMES_PER_LEVEL = 24
#: 宿主递归上限的**硬顶**。声明的深度超过它时不再往上抬：
#: 继续抬只会把"诊断"换成"进程栈溢出"，而诊断比崩溃有用。
_HOST_LIMIT_MAX = 120_000
#: 抬上限时留的富余（函数入口、`_block`、`_eval` 等固定开销）。
_HOST_LIMIT_MARGIN = 1_000


#: **会改动接收者的方法**：对 `const T&` 形参调用它们一律拒绝。
#: （`push_back` / `erase` / `clear` 是 P4-0 的 C++ 名字；旧名同时保留。）
MUTATING_METHODS = {
    "push", "push_back", "pop", "set", "insert", "remove", "erase", "clear",
    "append", "extend", "sort", "sort_by", "reverse", "fill", "swap", "resize",
}


class _Break(Exception):
    """`break;` —— 只跳出一层循环（由 while / for 捕获）。"""


class _Continue(Exception):
    """`continue;` —— 结束本轮，进入下一轮。"""


class _Return(Exception):
    def __init__(self, value):
        self.value = value


# ---------------------------------------------------------------- 解释器

class Interp:
    def __init__(self, impl_mod: Node, decl_mod: Optional[Node] = None,
                 budget: int = None, table_path: Optional[str] = None,
                 parallel: str = "seq", lib_dirs=None):
        self.impl = impl_mod
        self.decl = decl_mod
        # **步数预算**：默认 500 万步当作"疑似死循环"的护栏。
        # 大样例（`count` 在 n=4096）会正常超过它 —— 用
        # `ELENCH_STEP_BUDGET=20000000` 放宽，而不是把默认值调大
        # （默认调大就等于取消了死循环护栏）。
        if budget is None:
            try:
                budget = int(os.environ.get("ELENCH_STEP_BUDGET", "5000000"))
            except ValueError:
                budget = 5_000_000
        self.budget = budget
        # **运行期按表选候选**（2026-10-30 用户决定，方案甲）
        self.table_path = table_path
        self.tables = load_dispatch_table(table_path)
        # label → 实际走的候选（**per-thread**：每个线程各记各的，见 `last_candidates` 属性）
        self._lc_slot: Dict[str, str] = {}
        self.dispatch_warnings: List[str] = []
        # **并行执行模式**：`seq`（默认，顺序）或 `threads`（真线程）。
        # 注意：原型是 Python，受 GIL 限制**没有 CPU 加速**；
        # 这里实现线程是为了**验证语义**（结果必须与顺序执行完全一致）。
        self.parallel_mode = parallel
        # **FFI**：`extern` 声明（名字 → 节点）与已加载的 ctypes 句柄
        self.externs = {c.name: c for c in (impl_mod.children or [])
                        if getattr(c, "kind", "") == "extern"}
        # **动态库搜索目录**（`dsh run --lib-dir=`，可重复；**按命令行顺序**）。
        # 解析顺序见 `ffi_c.library_candidates`：路径写法 → 每个目录 → 平台加载器。
        # 空列表 = 与加这个开关之前完全一样（裸名交给平台加载器）。
        self.lib_dirs = [str(d) for d in (lib_dirs or ()) if str(d or "").strip()]
        # **spec 里的 external 子句**：`spec f(...) { external "lib"; }` → 名字 → 库名
        self.extern_specs = {}
        for _it in ((decl_mod.children if decl_mod is not None else []) or []):
            if getattr(_it, "kind", "") != "spec":
                continue
            _body = next((c for c in _it.children if c.kind == "spec-body"), None)
            for _c in (_body.children if _body else []):
                if _c.kind == "clause" and (_c.name or "") == "external":
                    _lib = (_c.value or {}).get("lib") if isinstance(_c.value, dict) else None
                    self.extern_specs[_it.name] = _lib
        # **"找不到函数体 = 外部声明"**：模块级 `link "lib";` 给出库名。
        self.link_lib = None
        for _it in ((decl_mod.children if decl_mod is not None else []) or []):
            if getattr(_it, "kind", "") == "link":
                self.link_lib = _it.name
        # **spec 名字集合**（用于"找不到函数体 = 外部声明"的懒判定）
        self._spec_names = {c.name for c in ((decl_mod.children if decl_mod is not None else []) or [])
                            if getattr(c, "kind", "") == "spec"}
        self._ffi_cache = {}
        # **随机基种子**：ELENCH_SEED 优先，否则时钟（每次运行不同）。
        self.rand_seed = stdlib.reset_base()
        self.steps = 0
        # **步数超限时的诊断线索**：当前 while 条件里「求值为 ≥2^63 的无符号减法」
        # （有就说明更可能是 `length() - 1` 在空容器上下溢，而不是死循环）。
        # `_tick` 抛 `InterpBudgetExceeded` 时用它把消息说准，见 `_budget_message`。
        self._budget_hint = None
        # **第二个线索**：当前 while 的 `(条件节点, 环境, 进入时的计数器值)`。
        # 超限时重新求值条件，能看出「有具体数值上界、计数器确实在推进」——
        # 那是输入规模/常数太大，不是死循环。判不出来就保持 None（不瞎猜）。
        self._budget_bound_hint = None
        # **预算耗尽时的静态顾问**：`callable(active_fn_names) -> str|None`。
        # 由调用方（`dsh run`）挂上，只在真的超预算时调用一次，所以正常路径
        # 不付成本分析的代价。返回 None 表示没有静态证据。
        self.budget_advisor = None
        # **当前函数调用栈**（只用于预算诊断：知道超限时在跑哪个函数）。
        self._fn_stack: List[str] = []
        # **与 κ 同口径的操作计数**（`_eval` + `_stmt` + 内建调用，见文件头的计数器一节）。
        # 它**不是** `evaluation_steps`：后者多了「每轮循环」的 `_tick`，
        # 两者不能混用（拿 ns 去比 operations 更是单位错配）。
        # 只有 `_COUNT` 打开时才写；默认 0。
        self.ops = 0
        # 计数（M1 验收③）：调用数 / 分配数 / 迭代数
        self.calls: Dict[str, int] = {}
        # 内建调用次数（print / hmap_* …）——给「不可测量」的函数报用量
        self.builtin_calls: Dict[str, int] = {}
        self.allocs = 0
        self.iters = 0
        # 与 T11 的 κ 公式对账用：`xs.length()` / `xs.at(i)` 各算 1 次方法调用
        # （G26 暂定口径：1 次调用 = 1 次 κ；`at` 是循环体里最贵的那一项）
        self.method_calls = 0
        self._cap: Dict[int, int] = {}      # 容器 id → 当前容量（模拟扩容）
        self.fns: Dict[str, Node] = {}
        # **逐帧声明类型**（变量名 → 类型节点）：赋值 / 下标写 / `.push` /
        # 字段写要按声明类型转换值（`u64` 的无符号标记就是在这里补上的）。
        # 以前只有一个**全局** `vty`，函数之间同名变量互相污染，而且只用于打印。
        self._tenv_stack: List[Dict[str, Any]] = []
        # **逐帧的 `const` 局部名**：`return const T v;` 不能隐式移动（C++ 里
        # `const` 挡住移动构造 ⇒ 只能拷贝，设计 §6.3）。与 `_tenv_stack`
        # **同进同出**（都由 `_push_tenv` / `_pop_tenv` 维护），不会失步。
        self._const_frames: List[set] = []
        # **逐帧的返回类型**：`-> T&` 返回的是借用，`_k_stmt_return` 不该拷贝。
        self._ret_stack: List[Any] = []
        self.gtypes: Dict[str, Any] = {}      # 模块级常量的声明类型
        self.structs: Dict[str, Dict[str, Any]] = {}   # struct 名 → {字段: 类型节点}
        #: 枚举名 → 变体名列表（**声明顺序**就是序号：`Color::Green as u64` → 1）。
        #: 内置错误枚举先放进来（与 `typecheck.TypeChecker.enums` 同一组），
        #: 用户 `enum` 在 `_collect` 里覆盖 / 追加。
        self.enums: Dict[str, List[str]] = dict(_BUILTIN_ENUM_ORDERS)
        self.kernels: Dict[str, Node] = {}
        self.over: Dict[str, List[str]] = {}
        # **spec 索引**：`requires` 断言要在入口找到对应 spec。
        self.specs: Dict[str, Node] = {
            (c.name or ""): c
            for c in ((decl_mod.children if decl_mod else []))
            if c is not None and getattr(c, "kind", "") == "spec"
        }
        # **`recursion depth <= N` 的运行期断言**（2026-11-10）：见
        # `_collect_depths` / `_check_rec_depth`。`{函数名: N}`；
        # 读不出正整数的声明进 `rec_depth_bad`（**不许静默当成没有**）。
        self.rec_depth_decl: Dict[str, int] = {}
        self.rec_depth_bad: Dict[str, str] = {}
        # **逐线程的当前帧层数**：`parallel { … } threads` 里多个线程共用一个
        # `Interp`，共享一个 dict 会把两个线程的层数相加（声明 8 时两个各 5 层
        # 就会被误判成 10 层）。所以和 ops 计数一样 per-thread。
        self._depth_local = threading.local()
        self._collect()

    # ---------- 索引
    def _collect(self) -> None:
        # 模块级常量（`const SPACE: u8 = 32;`）—— 先收，函数体要用。
        #
        # **初值要真的求值**（2026-11-10）：以前这里是 `_literal(init)`，
        # 非字面量一律 `None`，于是 `const N: u64 = 10 * 2;` 的 `run` 打印
        # `None`，`check` 却全绿。现在按**声明顺序**求值（后面的常量可以引用
        # 前面的，与 C++ 一致），求不出就报 `E-Const-NotConstant`——**不返回
        # None**。静态侧 `typecheck._check_const_initializers` 会在 check 阶段
        # 先挡住同一类写法；这里是运行期的兜底（`--no-check` 也不许静默）。
        self.globals: Dict[str, Any] = {}
        _const_nodes: List[Node] = []
        for mod in (self.decl, self.impl):
            if mod is None:
                continue
            for c in _descendants(mod, "const"):
                if len(c.children) >= 2:
                    _const_nodes.append(c)
            # `struct P { x: u64 }` 的字段类型：字段写 / 读要按它转换
            for st in _descendants(mod, "struct"):
                fields = {}
                for f in (st.children or []):
                    if getattr(f, "kind", "") == "field" and f.children:
                        fields[f.name] = f.children[0]
                self.structs[st.name or ""] = fields
            # **枚举的序号 = 变体在声明里的位置**（从 0 起）：
            # `Color::Green as u64` → 1。以前 `as` 原样返回枚举值（静默错值）。
            for en in _descendants(mod, "enum"):
                self.enums[en.name or ""] = [
                    v.name for v in (en.children or [])
                    if getattr(v, "kind", "") == "variant"]

        for fn in _descendants(self.impl, "fn"):
            self.fns[fn.name] = fn
        for k in _descendants(self.impl, "kernels"):
            self.kernels[k.name or ""] = k
        # 常量求值：`_const_value` 用的是**运行期同一套** `_arith` / `_cmp`，
        # 所以 `2 ** 64` 的回绕、`0 - 1` 的补码都与函数体里的语义一致。
        # 每个常量先按**声明类型**落值，再给后面的常量引用。
        _raw: Dict[str, Any] = {}
        for c in _const_nodes:
            self.gtypes[c.name] = c.children[0]
            _v = _const_value(c.children[-1], _raw)
            if _v is _CONST_UNKNOWN:
                raise InterpError(
                    f"E-Const-NotConstant：模块常量 `{c.name}` 的初值不是"
                    f"常量表达式——装载时只认字面量、前面声明的模块常量、"
                    f"括号、算术 / 位运算 / 比较与标量 `as`。"
                    f"把初值改写成一个能直接算出来的式子，"
                    f"或改在函数体里用 `const` 局部变量算。")
            _v = _convert_to(_v, c.children[0])
            _raw[c.name] = _v
            self.globals[c.name] = _v
        # **`npos`：语言预置常量**（`u64` 最大值）。`find` 找不到时返回它；
        # 与 C++ 的 `std::string::npos` 同一个名字、同一个用法
        # （`if (xs.find(v) != npos)`）。用户自己声明同名常量时以用户的为准。
        self.globals.setdefault("npos", _u64(_U64_MAX))
        if self.decl is not None:
            for ch in _descendants(self.decl, "choose"):
                for cl in ch.children:
                    if cl.kind == "choose-clause" and cl.name == "over":
                        names = (cl.value or {}).get("names", [])
                        self.over[ch.name] = list(names)
        # **`repeats` 的原始表达式**（2026-11-09）：运行期要拿它当真断言来校验，
        # 不再只是给 cost 域看的一句声明（见 `_repeats_limit`）。
        self.repeats_expr: Dict[str, Node] = {}
        for _nm, _sp in (self.specs or {}).items():
            _body = next((c for c in (_sp.children or [])
                          if getattr(c, "kind", "") == "spec-body"), None)
            for _c in (_body.children if _body else []):
                if (getattr(_c, "kind", "") == "clause"
                        and (_c.name or "") == "repeats" and _c.children):
                    self.repeats_expr[_nm] = _c.children[0]
                    break
        self._repeats_bound_cache: Dict[str, Any] = {}
        # **`recursion depth <= N`**（2026-11-10）：运行期按它计数，超过就抛
        # `E-Recursion-Depth-Exceeded`（与 `repeats` 同一条哲学）。
        self._collect_depths()
        # 宿主上限跟着**声明**走（见 `_ensure_host_recursion_headroom`）。
        self._ensure_host_recursion_headroom()

    # ---------- 递归深度（`.tu` 的 `recursion depth <= N`）
    def _collect_depths(self) -> None:
        """把 `recursion depth <= N;` 读成 `{函数名: N}`，供**运行期**计数。

        ## 为什么运行期也要读一遍

        `recursion depth` 以前只有 cost 域读（`CostInterp._collect_depths`），
        运行期**完全不看**：`.tu` 写 `<= 8` 照样能跑 75 层（宿主
        `sys.setrecursionlimit` 决定），于是 κ(75)=117 < ops=831 —— 承诺的
        "上界"被实测越过。现在这条声明是**可校验的断言**：

          · 进入函数 +1、离开 -1（per-thread，见 `_depth_local`）；
          · **帧数 > N + 1** 就抛 `E-Recursion-Depth-Exceeded`（fail-fast，
            不等递归跑完）。

        **为什么允许 N + 1 帧**：`recursion depth` 数的是**自调用嵌套层数**
        （`down(xs, 8)` 是 8 层递归 = 9 帧），与 cost 域的 `(N + 1) × 体一次`
        是同一口径。若按"帧数 ≤ N"判，`tests/cases/test_kappa_gaps.py` 里
        `down(xs, d)` + `recursion depth <= d` 的每一档都会误报。

        读不出正整数（`<= 0` / 符号名）的声明进 `rec_depth_bad`：运行期抛
        `E-Recursion-Uncheckable`（静态侧 `checker` 先报 `E-Recursion-BadDepth`）。
        """
        for _nm, _sp in (self.specs or {}).items():
            got = _recursion_depth_of(_sp)
            if got is None:                 # 没写这条子句
                continue
            n, why = got
            if n is None:
                self.rec_depth_bad[_nm] = why or "recursion depth"
            else:
                self.rec_depth_decl[_nm] = n
        # **热路径只查一张表**：`0`/缺省 = 这个函数没有闸门（不计数、不查），
        # 正整数 = 声明值，`-1` = 声明不可校验。绝大多数函数（非递归、没写
        # `recursion depth`）每帧只付一次 dict 查找。
        self._gate_of: Dict[str, int] = dict(self.rec_depth_decl)
        for _nm in self.rec_depth_bad:
            self._gate_of[_nm] = -1

    def _ensure_host_recursion_headroom(self) -> None:
        """按**声明**抬高宿主 `sys.setrecursionlimit`，让语言闸门先于宿主崩。

        ## 为什么必须做（以及为什么这**不是**把 (b) 当方案）

        方案 (b)（"用 `sys.setrecursionlimit` 让声明生效"）本身不成立：宿主上限
        是**全局一个数**，多个函数、多个模块互相干扰，而且它管的是**帧**不是
        语言层——同一句 `<= 256` 在 `chain` 上是 13.3 帧/层、在 `rec_tree` 上是
        9.3 帧/层，没有任何一个数能同时"刚好"。

        所以真正的闸门是 (a)：**每层一次计数**（`_check_rec_depth`）。但 (a) 要
        能触发，宿主得先容纳到第 N+1 层——否则 `声明 256、实跑 300` 会先死在
        宿主 76 层上，报的是 `RecursionError` 而不是语言诊断。这里只做**开闸**：

          · 上界取**所有声明之和** `Σ(N + 1)`（互递归时每层各记各的，总帧数
            不会超过这个和），乘 `_HOST_FRAMES_PER_LEVEL` 再留余量；
          · 硬顶 `_HOST_LIMIT_MAX`：超过就**不抬**——那时宿主会先崩，由
            `E-Recursion-HostLimit` 说清楚"声明超出了宿主能容纳的量"，
            而不是让进程段错误；
          · **只抬不降**（`want > cur` 才设）：上限是进程级的，降下去会踩到
            调用方自己的栈。
        """
        total = sum(n + 1 for n in self.rec_depth_decl.values())
        if total <= 0:
            return                      # 没有声明 → 不改宿主，行为与修前一致
        want = min(_HOST_LIMIT_MAX, _HOST_FRAMES_PER_LEVEL * total + _HOST_LIMIT_MARGIN)
        try:
            cur = sys.getrecursionlimit()
            if want > cur:
                sys.setrecursionlimit(want)
        except (ValueError, RecursionError):        # noqa: BLE001
            # 设不上（极少见：已在更深的位置）也不能让构造 Interp 失败：
            # 宿主会先崩，`E-Recursion-HostLimit` 那条路照样给诊断。
            pass

    def _depth_counts(self) -> Dict[str, int]:
        """**本线程**的 `{函数名: 当前帧层数}`（见 `_depth_local` 的理由）。"""
        loc = self._depth_local
        counts = getattr(loc, "counts", None)
        if counts is None:
            counts = {}
            loc.counts = counts
        return counts

    def _check_rec_depth(self, fn: Node, depth: int, gate: int) -> None:
        """进入 `fn` 的第 `depth` 帧时校验声明（fail-fast，绝不静默）。

        **这是本轮的病根所在**：修前声明只进 κ，运行期零作用，于是
        「声明越小、κ 越小、上界越容易被越过」。现在超过声明当场抛诊断，
        κ 的上界重新成立（见 `_verify/report.md` K3 的 ③）。

        `gate` 是 `_gate_of` 里的值：正整数 = 声明值，`-1` = 声明不可校验。
        """
        name = fn.name or ""
        if gate > 0:
            if depth > gate + 1:        # N 层递归 = N + 1 帧（见 `_collect_depths`）
                raise InterpError(
                    f"{E_RECURSION_DEPTH_EXCEEDED}：`{name}` 的 spec 声明了 "
                    f"`recursion depth <= {gate}`（自调用嵌套层数上界），但实际"
                    f"已经嵌到第 {depth - 1} 层（{depth} 帧）——**声明是假的**，"
                    f"κ 不再是上界。修法：把 `recursion depth` 改成真实上界，"
                    f"或把递归改写成迭代")
            return
        bad = self.rec_depth_bad.get(name) or "recursion depth"
        raise InterpError(
            f"{E_RECURSION_UNCHECKABLE}：`{name}` 的 `recursion depth` 声明"
            f"`{bad}` 读不出正整数——**这条上界无法校验**，等于没有声明。"
            f"请写成 `recursion depth <= N;`（N 是正整数常量）")

    # ---------- 逐帧类型表
    def _push_tenv(self, tenv: Dict[str, Any]) -> None:
        self._tenv_stack.append(tenv)
        self._const_frames.append(set())
        self._ret_stack.append(None)

    def _pop_tenv(self) -> None:
        if self._tenv_stack:
            self._tenv_stack.pop()
        if self._const_frames:
            self._const_frames.pop()
        if self._ret_stack:
            self._ret_stack.pop()

    def _tenv(self) -> Dict[str, Any]:
        return self._tenv_stack[-1] if self._tenv_stack else {}

    def _is_const_local(self, name: str) -> bool:
        """当前帧里这个名字是不是 `const` 声明（`const` 局部不能隐式移动）。"""
        frames = getattr(self, "_const_frames", None)
        return bool(frames) and name in frames[-1]

    # ---------- 值语义：拷贝 + 记账
    def _copy_value(self, v):
        """值语义入口的**统一实现**：真深拷贝 + 计入 `allocs` 与 ops。

        只有 `_copyable(v)` 为真才拷（标量/枚举载荷是值类型，拷贝无意义）。
        记账口径见模块级 `_copy_units`：**逐项对齐 `_copy_cost`**。
        """
        if not _copyable(v):
            return v
        ops, allocs = _copy_units(v)
        if allocs:
            self.allocs += allocs
        if _COUNT and ops:
            _slot()["n"] += ops
        return _deep_copy(v)

    def _is_movable_return(self, expr) -> bool:
        """`return <expr>;` 里 expr 是不是**可隐式移动**的实体（C++11 两阶段决议）。

        只有三种情况**不**拷贝：
          · `return 非 const 局部 / 按值形参;` → 移动（容器 O(1)）
          · `return T{...} / f()` 之类的 prvalue → 强制省略
          · `-> T&` 的返回 → 借用（由调用方决定）

        `const&` 形参 / `T&` 形参 / 元素 / 字段 / const 局部 / 全局
        都是**借用值** → 返回类型是 `T` 时必须拷贝（设计 §3.2 / §6.3）。
        """
        node = expr
        while node is not None and getattr(node, "kind", "") in ("paren", "try", "ref"):
            node = node.children[0] if node.children else None
        if node is None or getattr(node, "kind", "") != "path":
            return False
        name = node.name or ""
        tenv = self._tenv()
        if name not in tenv:                  # 全局常量 / 外层绑定 → 左值
            return False
        ty = tenv[name]
        if isinstance(ty, str):               # `for (i in 0..n)` 的下标变量
            return True
        if getattr(ty, "kind", "") == "type-ref":
            return False                      # `T&` / `const T&` 形参 → 别名
        if self._is_const_local(name):
            return False                      # `const T v;` → 只能拷贝（§6.3）
        return True                           # 按值形参 / 非 const 局部 → 移动

    def _ret_is_ref(self) -> bool:
        """当前帧的返回类型是不是引用（`-> T&`）。"""
        stack = getattr(self, "_ret_stack", None)
        if not stack or stack[-1] is None:
            return False
        ty = stack[-1]
        return getattr(ty, "kind", "") == "type-ref"

    def _type_of_var(self, name: str):
        """变量/常量的声明类型节点（先本帧，再模块级常量）。"""
        for t in reversed(self._tenv_stack):
            if name in t:
                return t[name]
        return self.gtypes.get(name)

    # ---------- 入口
    def counters(self) -> Dict[str, int]:
        """M1 验收③：每函数调用数 / 分配数 / 迭代数。"""
        return {"function_calls": dict(self.calls),
                "function_calls_total": sum(self.calls.values()),
                "method_calls": self.method_calls,
                "allocations": self.allocs,
                "iterations": self.iters,
                "evaluation_steps": self.steps,
                # **操作数**（本次 `call()` 的整棵调用树；计数关闭时恒为 0）
                "operations": self.ops,
                "operations_by_function": ops_by_fn(),
                "builtin_calls": dict(self.builtin_calls)}
    @property
    def last_candidates(self) -> Dict[str, str]:
        """**本线程**这一轮 `?!label` 实际走的候选。

        为什么 per-thread：`parallel { … } threads` 里所有任务共用同一个 `Interp`，
        而"选段"是按**各自实参规模**算的——线程 A 可能选 `by_iter`、线程 B 选 `by_index`。
        用一个全局槽会让后写的覆盖先写的，于是"报告说跑了 X、实际跑了 Y"。
        """
        return self._lc_slot

    @last_candidates.setter
    def last_candidates(self, val: Dict[str, str]) -> None:
        self._lc_slot = dict(val)

    def call(self, fn_name: str, args: List[Any],
             force: Optional[Dict[str, str]] = None) -> Any:
        fn = self.fns.get(fn_name)
        if fn is None:
            raise InterpError(f"找不到函数 {fn_name}")
        # 一次 `call()` = 一次测量窗口（只有 `_COUNT` 时结算 `self.ops`）。
        _before = _total() if _COUNT else 0
        try:
            return self._run_fn(fn, args, force or {}, {})
        except _HostRecursionLimit as _hr:
            # **宿主栈被打穿**（见 `_run_fn` 的 `except RecursionError`）：
            # 翻译成语言诊断，透出函数名与层数，而不是宿主的裸异常。
            raise self._host_depth_error(_hr) from None
        except RecursionError:
            # 没有经过 `_run_fn` 的那条路（例如 `guarantees` 求值 / 返回值转换
            # 里才打穿）：仍然不许把 `RecursionError` 漏给用户。
            raise self._host_depth_error(None) from None
        finally:
            if _COUNT:
                self.ops = _total() - _before

    def probe(self, fn_name: str, args: List[Any],
              force: Optional[Dict[str, str]] = None):
        """跑**一次**函数并返回 `(返回值, guarantees 失败列表)`。

        与 `call()` 的唯一区别：`guarantees` 失败**不抛**，收集成列表返回。
        这是有限域穷举（`exhaustive_module`）要的：一个输入违反了后置条件，
        还要把**返回值**一起拿出去给人看反例（`a = 200 → ret = 200`）。

        `requires` 不成立仍然抛 `InterpRequiresViolated`——那条输入不在契约的
        适用范围内，调用方必须排除它（不是反例，也不是证明的一部分）。
        """
        _prev = getattr(self, "_guarantee_failures", None)
        self._guarantee_failures = []
        try:
            result = self.call(fn_name, args, force)
        finally:
            fails = self._guarantee_failures
            self._guarantee_failures = _prev
        return result, fails

    def _host_depth_error(self, hr: Optional["_HostRecursionLimit"] = None) -> InterpError:
        """把"宿主调用栈用光"写成一条**语言诊断**（`E-Recursion-HostLimit`）。

        修前的样子（`_verify/report.md` K3 实测）：

            ✗ 运行失败：RecursionError: maximum recursion depth exceeded

        ——没有诊断码、没有函数名、没有行号，用户无从下手。现在给出三件事：
        **哪个函数、第几层、为什么**（是宿主上限，不是语言断言），并列出本模块
        已声明的深度（好让用户看出"声明了却还崩"是因为声明超出了宿主能容纳的量）。
        """
        try:
            lim = sys.getrecursionlimit()
        except Exception:                                  # noqa: BLE001
            lim = -1
        where = ""
        if hr is not None and hr.fn and hr.depth:
            where = f"，最深处在 `{hr.fn}`（语言调用栈深 {hr.depth} 层）"
        decl = ", ".join(f"{k} <= {v}" for k, v in sorted(self.rec_depth_decl.items()))
        decl = decl or "没写 `recursion depth`"
        return InterpError(
            f"{E_RECURSION_HOST_LIMIT}：递归深度超过**宿主上限**——宿主 Python 的"
            f"调用栈（`sys.setrecursionlimit` = {lim} 帧）已经用光{where}。"
            f"这**不是** Elench 的深度断言：本模块声明的是 {decl}。\n"
            f"  为什么会有这个上限：引擎是宿主语言写的，每层语言递归约烧 "
            f"{_HOST_FRAMES_PER_LEVEL // 2}–14 个宿主帧；**只声明深度并不会让宿主"
            f"栈变大**，引擎会按声明抬上限（硬顶 {_HOST_LIMIT_MAX:,} 帧），"
            f"超出硬顶就只能报这条。\n"
            f"  修法：① 在 spec 里写**真实上界** `recursion depth <= N;`（引擎按它"
            f"抬宿主上限，超过 N 会报 `{E_RECURSION_DEPTH_EXCEEDED}`）；"
            f"② 把递归改写成迭代；③ 确实需要更深递归时改小每层嵌套/换编译器后端")

    def _check_requires(self, fn: Node, env: Dict[str, Any], force: Dict[str, str]) -> None:
        """断言 spec 里的 `requires`（若写了）。"""
        spec = self.specs.get(fn.name) if getattr(self, "specs", None) else None
        if spec is None:
            return
        for holder in spec.children:
            if getattr(holder, "kind", "") != "spec-body":
                continue
            for cl in holder.children:
                if getattr(cl, "kind", "") != "clause" or cl.name != "requires":
                    continue
                for expr in cl.children:
                    try:
                        ok = self._eval(expr, env, force)
                    except Exception as exc:                # noqa: BLE001
                        raise InterpError(
                            f"{fn.name} 的 requires 无法求值：{type(exc).__name__}: {exc}")
                    if not ok:
                        raise InterpRequiresViolated(
                            f"{fn.name} 的 requires 不成立（前提被违反）",
                            fn.name or "")

    def _run_fn(self, fn: Node, args: List[Any], force: Dict[str, str],
                outer: Dict[str, Any], arg_lvals: Optional[List[bool]] = None) -> Any:
        self.calls[fn.name] = self.calls.get(fn.name, 0) + 1
        env: Dict[str, Any] = dict(self.globals)
        env.update(outer)
        # **签名缓存**：形参名 / 形参类型 / const 形参 / 返回类型都是静态的，
        # 每次调用重新遍历 `params` 是白花的时间（`measure` 里会被放大）。
        _sig = getattr(fn, "_sig_cache", None)
        if _sig is None:
            params = next((c for c in fn.children if c.kind == "params"), None)
            _ps = list(params.children if params else [])
            pnames = [c.name for c in _ps]
            pts = [c.children[0] if c.children else None for c in _ps]
            consts = frozenset(c.name for c in _ps
                               if isinstance(c.value, dict) and c.value.get("const"))
            ret_ty = next((c for c in fn.children
                           if getattr(c, "kind", "") in ("type-name", "type-ref", "type-fn")),
                          None)
            # **按值形参**：`T v` 是 type-name；`T&` / `const T&` 都被解析成
            # `type-ref` 包一层（见 `parser.parse_type_ref_suffix`）。
            # 只有按值形参在 C++ 里拷贝，引用形参是别名——必须在这里分开，
            # 否则 `f(T& v)` 的写回语义会被拷贝掉。
            pbyval = tuple(getattr(pt, "kind", "") != "type-ref" for pt in pts)
            _sig = (pnames, pts, consts, ret_ty, pbyval)
            fn._sig_cache = _sig
        pnames, pts, _const_names, _ret_ty, _pbyval = _sig
        if len(args) != len(pnames):
            raise InterpError(f"{fn.name} 需要 {len(pnames)} 个实参，给了 {len(args)}")
        # **`&x` 在"显式借用标记"位置要还原成被指的对象**（2026-11-10）：
        # `&` 从本轮起是**取地址**（产出 `PtrVal`），但 `T&` / `const T&` 形参
        # 收的仍然是"那个对象本身"（`take(&a)` 里 `a[0] = 1` 要写回调用方）。
        # 判据只用形参类型：**引用形参**收到 `PtrVal` 就解回它指向的对象；
        # `T*` 形参是**按值**收地址（`pbyval` 为真），原样保留 `PtrVal`。
        for _i, _v in enumerate(args):
            if _i < len(_pbyval) and not _pbyval[_i] and isinstance(_v, PtrVal):
                args[_i] = _v.get()
        # **指针是标量**：`T* p` 形参按值收下地址本身，不深拷被指对象
        # （`_copyable(PtrVal)` 为 False，这里的拷贝循环对它是空操作）。
        # **值语义入口 ②：按值形参，实参是左值 → 拷贝**（2026-11-09）。
        # 实参是 prvalue（`f(mk())` / `f({1,2,3})`）时**不拷贝**：那个临时值
        # 没有别的持有者，移动它和拷贝它调用方都观察不到区别（C++ 也是移动）。
        if arg_lvals is not None:
            for _i, _lv in enumerate(arg_lvals):
                if _i < len(args) and _lv and _pbyval[_i]:
                    args[_i] = self._copy_value(args[_i])
        # **形参按声明类型转换**（`u64` → 无符号标记；`i64` 保持补码）。
        # 只转标量：容器逐元素转换是 O(n)，放进每次调用会污染 `measure`
        # 的实测阶（log 候选被 O(n) 的转换拖成线性）。语言里造出来的容器
        # 在声明/返回/`push` 处已经逐元素转换过了。
        tenv: Dict[str, Any] = {}
        for pt, nm, v in zip(pts, pnames, args):
            tenv[nm] = pt
            env[nm] = _convert_scalar(v, _type_label(pt))

        # **const 形参登记**（v0.72）：这一帧里这些名字不许被变更方法写
        _consts = set(_const_names)
        if not hasattr(self, "_const_stack"):
            self._const_stack = []
        self._const_stack.append(_consts)
        # **只给预算诊断用的函数栈**：超限时要认出「正在跑哪个函数」，
        # 才能把 `cost` 侧记的树递归原因贴对人（不参与求值/计数）。
        self._fn_stack.append(fn.name or "")
        self._push_tenv(tenv)
        # **本帧的返回类型**：`-> T&` 是借用（返回时不拷贝），据此判。
        self._ret_stack[-1] = _ret_ty
        # **本函数当前帧层数**（per-thread，见 `_depth_counts`）：进入 +1、
        # 离开时在下面的 `finally` 里还原。它是 `recursion depth <= N` 的
        # 运行期断言的计数器——**只有带声明的函数才计数**（`_gate_of`），
        # 其余函数每帧只付一次 dict 查找。
        _gate = self._gate_of.get(fn.name) or 0
        if _gate:
            _counts = self._depth_counts()
            _prev_depth = _counts.get(fn.name, 0)
            _depth = _prev_depth + 1
            _counts[fn.name] = _depth
        else:
            _counts = None
            _prev_depth = 0
            _depth = 0

        # **进入函数记账、返回时结算**：`ops_of(f)` 于是是整棵调用树的量。
        _mark = _slot()["n"] if _COUNT else 0
        result = None
        try:
            # **`recursion depth <= N` 的断言在最前面**（2026-11-10）：
            # 超过声明立刻失败，不等这一层把活干完（`repeats` 同一口径）。
            if _gate:
                self._check_rec_depth(fn, _depth, _gate)
            # **`requires` 在入口断言**（v0.71）。它是"调用方必须保证"的前提，
            # 不成立就立刻失败——**而不是让函数带着坏输入继续算**。
            self._check_requires(fn, env, force)
            block = stmt_body(fn)
            if block is None:
                raise InterpError(f"{fn.name} 没有函数体（可能还是 pending）")
            fallible = bool((fn.value or {}).get("fallible"))
            try:
                self._block(block, env, force)
                result = Ok(None) if fallible else None
            except _Return as r:
                if fallible:
                    v = r.value
                    result = v if isinstance(v, (Ok, Err)) else Ok(v)
                else:
                    result = r.value
        except RecursionError:
            # **宿主栈被打穿**：这里知道"崩在哪个函数、语言栈多深"，包一层交给
            # `call()` 翻成 `E-Recursion-HostLimit`（绝不把宿主异常透给用户）。
            # `len(self._fn_stack)` 在 `finally` 之前取，仍然含这一帧。
            raise _HostRecursionLimit(fn.name or "", len(self._fn_stack)) from None
        finally:
            if _COUNT:
                _bump_fn(fn.name, _slot()["n"] - _mark)
            if _gate:
                _counts[fn.name] = _prev_depth
            if getattr(self, "_const_stack", None):
                self._const_stack.pop()
            if getattr(self, "_fn_stack", None):
                self._fn_stack.pop()
            self._pop_tenv()
        # **返回值按声明类型转换**：`u64` 的 `0 - 1` 在这里变成 2^64-1；
        # `i64` 的负数原样保留。这一步是"声明类型说了算"的最后一个入口。
        if _ret_ty is not None:
            if isinstance(result, Ok):
                if result.v is not None:
                    result = Ok(_convert_to(result.v, _ret_ty))
            elif not isinstance(result, Err):
                result = _convert_to(result, _ret_ty)
        # **`guarantees` 在返回时断言**（v0.72）：可检查的那部分真的求值，
        # 不可检查的已在 `check` 里报 `E-Guarantee-Uncheckable`（不会到这里）。
        self._check_guarantees(fn, env, result, force)
        return result

    def _check_guarantees(self, fn: Node, env: Dict[str, Any], result: Any,
                          force: Dict[str, str]) -> None:
        """返回后校验 `guarantees`（只校验可判定的那部分）。

        **`T!`（可失败）返回值上的语义（2026-11-09）**：
        可失败函数正常返回时 `result` 是 `Ok(值)`。后置条件说的是
        「**函数成功返回的那个值**」，所以：

          · `Ok(v)` → 谓词里的 `ret` / `result` 绑定 **`v`（解包后的值）**；
          · `Err(e)` → **跳过全部 guarantees**：函数没有正常返回，
            「返回后满足 P」这个前提不成立（错误路径上再判 P 只会误报）。

        `Ok(None)`（`void!`）也解包成 `None`：谓词若对 `None` 取长度 /
        做大小比较，得到的是 **Elench 诊断**，而不是宿主 `TypeError`。
        """
        import guarantees as _gt
        spec = self.specs.get(fn.name) if getattr(self, "specs", None) else None
        if spec is None or not _gt.has_guarantees(spec):
            return
        if isinstance(result, Err):
            return                      # 失败返回：后置条件的前提不成立
        ok_exprs, _bad = _gt.classify(spec)
        if _bad:
            # **不可检查的承诺不许静默跳过**（2026-11-09）：以前这里只求值
            # 可检查的那几条，其余直接 `return`——于是写一个永远不会生效的
            # 谓词，`check` 只 warning、`run` 照常通过。现在 `check` 报 error，
            # 直接走 `Interp` API（绕过 check）的调用方也在这里拿到同样的诊断。
            raise InterpError(
                f"E-Guarantee-Uncheckable：{fn.name} 的 guarantees 无法检查"
                f"（{_bad[0]}）——不可检查的承诺永远不会生效，请改写")
        if not ok_exprs:
            return
        # **解包**：`ret` / `result` 指向 Ok 里的值，不是 `Ok` 包装壳本身。
        # 以前直接绑 `result`，于是 `is_sorted(ret)` 对 `Ok(...)` 调
        # `len()` → 宿主 `TypeError`（check 却只报 info）——双重缺陷。
        value = result.v if isinstance(result, Ok) else result
        e2 = dict(env)
        e2["ret"] = value
        e2["result"] = value
        # **穷举模式**：`probe()` 先挂一个列表，违反不抛、逐条记下来。
        # 这里读一次（不是每轮读）：嵌套调用里同一份 sink 会一起收集，
        # 那是对的——外层函数「返回后满足 P」的前提已经被内层破坏。
        _sink = getattr(self, "_guarantee_failures", None)
        for expr in ok_exprs:
            # 块形（`guarantees { e1; e2; }`）的每条断言带 `clause_no`：
            # 失败时必须**指出是哪一条**，否则「两条独立断言」等于两条一起报
            # 同一句「guarantees 不满足」。
            _site = _guarantee_site(expr)
            _at = f"（{_site}）" if _site else ""
            # 块形：除了「第 k 条」，再给出**那条断言本身**（`ret > n`）。
            _desc = (_guarantee_expr_text(expr) if _site
                     else _gt.describe([expr]))
            _why = None
            try:
                good = self._eval_guarantee_expr(expr, e2, force)
            except (InterpBudgetExceeded, ExitRequest):
                raise                   # 预算 / exit 是正常控制流：原样上抛
            except InterpError as exc:
                # 已经是语言级诊断（例如「未绑定的名字」），补上下文再抛。
                if _sink is not None:
                    _sink.append({"fn": fn.name,
                                  "line": int(getattr(expr, "line", 0) or 0),
                                  "expr": _guarantee_expr_text(expr),
                                  "site": _site,
                                  "error": str(exc)})
                    continue
                raise InterpError(
                    f"E-Guarantee-Violated：{fn.name} 的 guarantees 无法判定{_at}"
                    f"（{_desc}）：{exc}") from None
            except Exception as exc:                       # noqa: BLE001
                # **宿主异常一律翻译，不许泄漏**：以前 `is_sorted(Ok(...))`
                # 会把 `TypeError: object of type 'Ok' has no len()` 直接
                # 打到用户面前。谓词只允许 path / 字面量 / 比较 / 两个内建，
                # 走到这里的失败一定是「值的运行时类型与谓词不匹配」等少数几种。
                if _sink is not None:
                    _sink.append({"fn": fn.name,
                                  "line": int(getattr(expr, "line", 0) or 0),
                                  "expr": _guarantee_expr_text(expr),
                                  "site": _site,
                                  "error": _guarantee_reason(exc)})
                    continue
                raise InterpError(
                    f"E-Guarantee-Violated：{fn.name} 的 guarantees 无法判定{_at}"
                    f"（{_desc}）：{_guarantee_reason(exc)}") from None
            if not good:
                if _sink is not None:
                    _sink.append({"fn": fn.name,
                                  "line": int(getattr(expr, "line", 0) or 0),
                                  "expr": _guarantee_expr_text(expr),
                                  "site": _site,
                                  "error": None})
                    continue
                raise InterpError(
                    f"E-Guarantee-Violated：{fn.name} 返回后不满足"
                    f"{(' ' + _site) if _site else ''} {_desc}")

    def _eval_guarantee_expr(self, expr: Node, env: Dict[str, Any], force) -> Any:
        """求值一条**可检查的** `guarantees`：谓词调用只走**语言内置谓词表**。

        ## 为什么不能直接 `self._eval(expr, …)`

        根因（2026-11-09）：`_call` 的解析顺序是「用户函数优先于同名内建」
        （见 `_call` 里那条 2026-11-09 的注释）。于是用户在 `.eln` 里写一个
        **恒真的** `is_sorted`，`guarantees is_sorted(xs)` 实际调用的就是它——
        **承诺位写着，但完全不生效**：`check` 只报 `I-Guarantee-Runtime`，
        `run` 用未排序输入照样通过（实测）。后置条件的语义必须由**语言**
        定义，不能由用户实现顶替，所以这里把谓词名**直接派发到内置实现**
        （`stdlib.call`），`self.fns` 里的同名函数完全不参与。

        用户仍然可以定义同名的普通函数（`is_sorted` 在别的调用点照常解析到
        用户版本）——被架空的只是 `guarantees` 这一处。

        ## 为什么要递归处理 `&&` / `||` / `!`

        分类器（`guarantees._unsupported`）允许这三个组合子，谓词调用可以
        嵌在它们里面；如果对整棵子树退回 `self._eval`，里面的调用又会走
        `_call`，洞就还在。
        """
        import guarantees as _gt
        k = getattr(expr, "kind", "")
        if k == "call":
            nm = (expr.name or "").split(".")[-1]
            if nm not in _gt.CHECKABLE:
                # 分类器已经拦过；走到这里说明分类与求值脱节——**不许静默跳过**。
                raise InterpError(
                    f"E-Guarantee-Uncheckable：guarantees 里的 `{nm}` "
                    f"不是内置可检查谓词（只有：{', '.join(sorted(_gt.CHECKABLE))}）")
            args_node = next((c for c in expr.children if c.kind == "args"), None)
            vals = [self._eval(a, env, force)
                    for a in (args_node.children if args_node else [])]
            if len(vals) != _gt.CHECKABLE[nm]:
                raise InterpError(
                    f"E-Guarantee-Uncheckable：`{nm}` 需要 "
                    f"{_gt.CHECKABLE[nm]} 个实参，写了 {len(vals)} 个")
            return self._tag_stdlib_result(nm, stdlib.call(nm, vals))
        if k in ("unary", "not") and (expr.value or "") in ("!", "not"):
            return not self._eval_guarantee_expr(expr.children[0], env, force)
        if k == "binary" and (expr.value or "") in ("and", "&&"):
            return (bool(self._eval_guarantee_expr(expr.children[0], env, force))
                    and bool(self._eval_guarantee_expr(expr.children[1], env, force)))
        if k == "binary" and (expr.value or "") in ("or", "||"):
            return (bool(self._eval_guarantee_expr(expr.children[0], env, force))
                    or bool(self._eval_guarantee_expr(expr.children[1], env, force)))
        return self._eval(expr, env, force)

    # ---------- 语句
    def _budget_message(self) -> str:
        """步数超限的消息：**按证据分类**，判不出来就明说"不一定是死循环"。

        证据强度从高到低（前面的命中就不看后面的）：

        ① **u64 下溢**：循环条件里的无符号减法求值为 ≥ 2^63。空容器上
           `xs.length() - 1` 是**正确语义**（2^64-1 圈），把人指向控制流是错的。
           由 `_k_stmt_while` 记下证据，见 `_u64_underflow_hint`。
        ② **算法本身指数/超多项式**：`cost` 侧已经算出 κ = ⊤，且原因由
           `CostInterp._note_tree_rec` 明说是**树递归**（一条路径多个自调用点）。
           **复用**那套判定，不在解释器里另写一份递归形状分析；顾问由
           `dsh run` 挂上（`budget_advisor`），只有超预算时才跑。
        ③ **输入规模/常数太大**：while 条件有具体数值上界，且超限时计数器
           确实比进入时大（重新用 `_diag_eval` 求值，不推进计数器）。
        ④ 都没有：**疑似死循环**，但必须同时说「也可能是算法复杂度/输入太大」——
           以前这里只写死循环，于是指数回溯的人跑去改循环变量，方向完全错。

        `ELENCH_STEP_BUDGET` 的**当前值**在每一类里都给，因为"放不放宽"
        是读者要做的决定。
        """
        _base = (f"执行超过 {self.budget} 步（已执行 {self.steps} 步；"
                 f"预算可用 ELENCH_STEP_BUDGET 放宽，当前 {self.budget}）。")
        _hint = self._budget_hint
        if _hint:
            _expr, _val = _hint
            return (
                _base + "\n"
                "两条可能，请先排除①：\n"
                f"① u64 下溢：循环条件里的 `{_expr}` 求值为 {_val}（≥ 2^63）。"
                f"空容器上 `xs.length() - 1` 会下溢成 2^64-1，循环真的会跑 2^64 次。"
                f"修法：先判空（`if (xs.length() == 0) {{ return 0; }}`），"
                f"或用 `i + 1 < xs.length()`。\n"
                "② 真的死循环：检查循环变量是否在推进。\n"
                f"（若都不成立，也可能是算法本身的复杂度或输入太大；"
                f"提高 ELENCH_STEP_BUDGET 当前值 {self.budget} 可以放宽。）")
        _alg = self._static_algorithm_message()
        if _alg:
            return _base + "\n" + _alg
        _size = self._finite_bound_message()
        if _size:
            return _base + "\n" + _size
        return _base + (
            "疑似死循环（检查循环变量是否在推进）。\n"
            "**也可能是算法本身的复杂度（指数/超多项式）或输入规模太大，不一定是死循环。**\n"
            f"若只是想把这次跑完：提高 ELENCH_STEP_BUDGET（当前 {self.budget}）。")

    def _static_algorithm_message(self):
        """问静态顾问「预算耗尽是不是算法复杂度」；没有顾问/没证据 → None。

        顾问只在**真正超预算**时调用，异常一律吞掉：诊断失败不能把原始错误
        盖成另一个异常。`active` 是当前调用栈（最内层在最后）。
        """
        _advisor = getattr(self, "budget_advisor", None)
        if _advisor is None:
            return None
        try:
            _msg = _advisor(list(getattr(self, "_fn_stack", ()) or ()))
        except Exception:                                  # noqa: BLE001
            return None
        return _msg or None

    def _finite_bound_message(self):
        """超限时若 while 条件有具体数值上界且计数器推进了，说「输入太大」。

        只在**运行期**重新求值（`_diag_eval`，无副作用）：进入循环时记下
        `(条件, 环境, 初始计数器)`，超限时再看一次。看不到推进（`<=` 初始值）
        就当没有证据，退回「疑似死循环」——宁可不说，也不把死循环说成输入大。
        """
        _hint = getattr(self, "_budget_bound_hint", None)
        if not _hint:
            return None
        _cond, _env, _start = _hint
        try:
            _lo = self._diag_eval(_cond.children[0], _env)
            _hi = self._diag_eval(_cond.children[1], _env)
        except Exception:                                  # noqa: BLE001
            return None
        if not isinstance(_lo, int) or isinstance(_lo, bool) \
                or not isinstance(_hi, int) or isinstance(_hi, bool):
            return None
        if _hi <= 0 or not (0 <= _lo <= _hi) or _lo <= _start:
            return None
        return (
            f"循环条件 `{_expr_text(_cond)}` 有**具体数值上界**"
            f"（超限时 {_lo} < {_hi}），计数器确实在推进："
            "这更像**输入规模/常数太大**，不是死循环。\n"
            f"修法：缩小输入，或提高 ELENCH_STEP_BUDGET（当前 {self.budget}）。")

    def _diag_eval(self, n, env):
        """诊断用的**受限求值**：只认字面量/数值变量/算术/`length()`。

        不走 `_eval`：为了诊断去跑用户函数是另一类错误，而且 `_eval`
        会推进步数/方法调用计数（κ 对账要用），诊断不该改这些数字。
        不认识的节点一律返回 `None`（→ 没有证据，不瞎猜）。
        """
        if n is None:
            return None
        k = getattr(n, "kind", "")
        if k == "path":
            _v = env.get(n.name)
            if isinstance(_v, bool):
                return int(_v)
            return _v if isinstance(_v, (int, float)) else None
        if k == "lit-int":
            return int(n.value)
        if k == "lit-float":
            return float(n.value)
        if k == "paren" and n.children:
            return self._diag_eval(n.children[0], env)
        if k == "binary" and len(n.children) >= 2:
            _a = self._diag_eval(n.children[0], env)
            _b = self._diag_eval(n.children[1], env)
            if _a is None or _b is None:
                return None
            try:
                return _arith(n.value or "", _a, _b)
            except Exception:                       # noqa: BLE001
                return None
        if k == "method-call" and (n.name or "").split(".")[-1] in _SAFE_LEN_METHODS \
                and n.children and getattr(n.children[0], "kind", "") == "path":
            _recv = env.get(n.children[0].name)
            if _recv is not None and hasattr(_recv, "__len__"):
                return U64(len(_recv))
        return None

    def _u64_underflow_hint(self, node, env, force):
        """在 while 条件里找「无符号减法、且当前求值 ≥ 2^63」的子表达式。

        返回 `(源码文字, 值)` 或 `None`。求值走 `_diag_eval`（受限、无副作用）；
        求不出来就当没有证据，**不瞎猜**。
        """
        _found = []

        def _walk(n):
            if n is None or _found:
                return
            if getattr(n, "kind", "") == "binary" and (n.value or "") == "-":
                _v = self._diag_eval(n, env)
                if isinstance(_v, UInt) and int(_v) >= (1 << 63):
                    _found.append((_expr_text(n), int(_v)))
                    return
            for _c in getattr(n, "children", ()) or ():
                _walk(_c)

        _walk(node)
        return _found[0] if _found else None

    def _finite_bound_probe(self, cond, env):
        """进入 while 时记下 `(条件, 环境, 初始左值)`，给 `_finite_bound_message` 用。

        只认 `i < n` / `i <= n` 且两边都能被 `_diag_eval` 求成数值的形状；
        其它形状返回 None（→ 不声称「输入太大」）。存的是环境**引用**，
        循环体对变量的赋值会反映在超限时的重新求值里。
        """
        n = cond
        while getattr(n, "kind", "") == "paren" and getattr(n, "children", None):
            n = n.children[0]
        if getattr(n, "kind", "") != "binary" or (n.value or "") not in ("<", "<="):
            return None
        if not getattr(n, "children", None) or len(n.children) < 2:
            return None
        _lo = self._diag_eval(n.children[0], env)
        _hi = self._diag_eval(n.children[1], env)
        if not isinstance(_lo, int) or isinstance(_lo, bool) \
                or not isinstance(_hi, int) or isinstance(_hi, bool):
            return None
        return (n, env, int(_lo))

    def _tick(self) -> None:
        self.steps += 1
        if self.steps > self.budget:
            raise InterpBudgetExceeded(self._budget_message())

    def _block(self, blk: Node, env: Dict[str, Any], force: Dict[str, str]) -> None:
        for st in blk.children:
            self._stmt(st, env, force)

    def _stmt(self, st: Node, env: Dict[str, Any], force: Dict[str, str]):
        if _COUNT:
            _bump()
        if getattr(st, "kind", None) == "assign-op":
            target = st.children[0]
            # **第三参是 `force`（dict），以前错传 `True`**（v0.72 修）
            val = self._eval(st.children[1], env, force)
            cur = self._eval(target, env, force)
            _tn = self._target_type(target)
            # 先按左值的声明类型解释当前值：`u64` 的补码安全网（万一
            # 值是从宿主/旧路径进来的），再按同一类型回绕。
            cur = _convert_to(cur, _tn)
            op = st.value or ""
            _op = {"+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%"}.get(op)
            if _op is None:
                raise InterpError(f"未知的复合赋值运算符：{op}")
            new = _convert_to(_arith(_op, cur, val), _tn)
            if target.kind == "path":
                env[target.name] = new
            elif target.kind == "index":
                base = self._eval(target.children[0], env, True)
                _key = self._eval(target.children[1], env, True)
                # **接收者是 map 时走 map 的写路径**（2026-11-11，审计 A13）。
                # 以前这里一律 `base[_index(key, base)] = new`——`_index` 是
                # **序列**的越界检查，拿 `len(MapVal)`（条目数）当长度。于是
                # 同一个 `cnt[k] += 1`：**键值 < 条目数时"恰好"算对，
                # 键值 ≥ 条目数时误报「下标越界（容器长度 3）」**——
                # 对错取决于数据的偶然大小关系，错误消息还把人指向"容器长度"。
                # 现在：键在**读 `cur`** 那一步就已按 `m[k]` 的 C++ 语义插入
                # 默认值（`_map_index_read`），这里只需按键写回。
                if isinstance(base, MapVal):
                    base.put(_key, new)
                    return None
                if isinstance(base, UnorderedMapVal):
                    base.data[_key] = new
                    return None
                base[_index(_key, base)] = new
            elif target.kind == "field":
                self._eval(target.children[0], env, True).fields[target.name] = new
            elif target.kind == "deref":
                # `*p += 1`：写回 `p` 指的那块存储（空的在上面读 `cur` 时就已报
                # `E-Pointer-NullDeref`，这里只做类型宽度兜底）。
                _ptr = self._eval(target.children[0], env, True)
                if not isinstance(_ptr, PtrVal):
                    raise InterpError(
                        f"{E_POINTER_NULL_DEREF}：`*p {op} …` 的 `p` 不是指针 / 是空指针")
                _ptr.set(new)
            else:
                raise InterpError(f"复合赋值的左值不支持：{target.kind}")
            return None
        return self._core_stmt(st, env, force)

    def _target_type(self, target):
        """左值/读取目标的声明类型节点（拿不到就 `None`）。

        类型是静态的，所以结果缓存在 AST 节点上——循环里的 `x = x + 1`
        每轮都重新解析一遍 type-args 是白花的时间。
        """
        if target is None:
            return None
        _cached = getattr(target, "_target_ty", None)
        if _cached is not None:
            return None if _cached is _NONE_TY else _cached
        _res = self._target_type_uncached(target)
        try:
            target._target_ty = _res if _res is not None else _NONE_TY
        except Exception:                               # noqa: BLE001
            pass
        return _res

    def _target_type_uncached(self, target):
        k = getattr(target, "kind", "")
        if k == "path":
            return self._type_of_var(target.name or "")
        if k == "field":
            # `s.f = …` 与 `q->f = …`（后者是 `field(deref(q), f)`）走**同一条**
            # 解析：基址可能是变量，也可能是 `deref` / 下标。以前只认 `path`
            # 基址，于是 `q->f = 9` 拿不到字段类型，落进去的是裸 int 而不是
            # 声明类型 `u64`——同一个字段两种值。
            base = target.children[0] if target.children else None
            bt = self._static_ty(base)
            head, _args = _type_parts(bt)
            if head in self.structs and target.name in self.structs[head]:
                return self.structs[head][target.name]
            return _elem_type_node(bt)
        if k == "index":
            base = target.children[0] if target.children else None
            if base is not None and getattr(base, "kind", "") == "path":
                return _elem_type_node(self._type_of_var(base.name or ""))
            return None
        if k == "deref":
            # `*p = …` 要按**指向类型**落值（`u64` 的无符号标记就从这里来）：
            # 以前 `_target_type` 对 `deref` 返回 None，于是 `*p = 0 - 1;`
            # 会存进 `-1` 而不是 `2^64-1`——声明写着 u64、值是负数。
            inner = target.children[0] if target.children else None
            return _pointee_type_node(self._static_ty(inner))
        return None

    def _static_ty(self, n):
        """表达式节点的**声明类型节点**（拿不到就 `None`）——只服务左值落值。

        与 `_target_type` 的分工：`_target_type` 认的是"要写的那块存储"，
        这里认的是"这个表达式静态上是什么类型"。只做**不需要求值**的推导，
        覆盖 `path` / `deref` / `field` / `index` / `paren`：够 `*p = …`、
        `q->f = …`、`(*p).f` 解析出声明类型即可，不重复一遍类型检查器的工作。
        """
        k = getattr(n, "kind", "")
        if k == "paren" and n.children:
            return self._static_ty(n.children[0])
        if k == "path":
            return self._type_of_var(n.name or "")
        if k == "deref":
            return _pointee_type_node(self._static_ty(
                n.children[0] if n.children else None))
        if k == "index":
            return _elem_type_node(self._static_ty(
                n.children[0] if n.children else None))
        if k == "field":
            bt = self._static_ty(n.children[0] if n.children else None)
            head, _args = _type_parts(bt)
            return self.structs.get(head, {}).get(n.name)
        return None

    def _core_stmt(self, st: Node, env: Dict[str, Any], force: Dict[str, str]) -> None:
        self._tick()
        k = st.kind

        _name = INTERP_STMT.get(k)
        if _name is not None:
            return getattr(self, _name)(st, env, force)
        raise InterpError(f"未实现的语句：{k}")


    def _k_stmt_const_decl(self, st, env, force):
        # children[0] 是类型；`has_init` 为真时最后一个是初值
        _has_init = (st.value or {}).get("has_init", len(st.children) > 1)
        if _has_init:
            _v = self._eval(st.children[-1], env, force)
        else:
            # **只声明不初始化** → 默认构造（2026-11-08）
            _v = self._default_value_of(st)
        # **声明类型是"不可失败"时，把 `Ok(x)` 解包**：
        # `/` 与 `%` 在实现里返回 `Ok(...)`（为了表达除零），于是
        # `const u64 k = xs.at(i) % N;` 会把 `Ok(3)` 当成下标 → TypeError。
        # 类型已经声明成 `u64`（不带 `!`），说明调用方**没打算处理失败**，
        # 这里就应当解包；真出错（Err）才抛。
        _ty = st.children[0] if st.children else None
        _fallible = bool(_ty is not None and getattr(_ty, "kind", "") == "type-fail")
        if not _fallible and isinstance(_v, Ok):
            _v = _v.v
        elif not _fallible and isinstance(_v, Err):
            raise InterpError(f"{st.name} 的初值是 Err({_v.e})，但类型声明为不可失败")
        # **值语义入口 ①：`T a = b;` 里 b 是左值 → 拷贝构造**（2026-11-09）。
        # 以前这里直接 `env[name] = _v`，于是 `T dst = src;` 只是给同一个对象
        # 起了第二个名字（容器、结构体全部如此），与成本域记的 C++ 拷贝不符。
        # 右值是 prvalue（`mk()` / `T{...}` / `{...}` / 字面量）→ **不拷贝**：
        # C++17 强制省略，等价于把新对象直接构造进 `a`。
        # 注意位置：必须在 Ok/Err 解包之后（否则会复制一个包装壳）。
        if _has_init and _is_lvalue(st.children[-1]):
            _v = self._copy_value(_v)
        # 类型节点**本身就是** `type-name`（`var-decl.children[0]` 直接是它），
        # 不是容器——第一版取 `.children[0]` 拿到 None，于是类型表一直是空的。
        # **`T*`（`type-ptr`）也按"自己就是类型节点"处理**（2026-11-10）：以前它
        # 落到 `else` 分支取到 `children[0]` = **指向类型**（`u64*` 的 `u64`），
        # 于是 `p` 在类型表里被登记成 `u64`——`*p = 9` 就再也查不到"它是指针"。
        _tn = (_ty if getattr(_ty, "kind", "") in ("type-name", "type-ref", "type-ptr")
               else (_ty.children[0] if (_ty is not None and _ty.children) else None))
        # **大括号初始化：按声明类型造真容器**（2026-11-08）
        #
        # 分配记账与真实 C++ 对齐（这是本项目的硬约束：κ 必须是上界，
        # 少算比多算危险）：
        #   `vector<T> x = {...}`（非空）→ **1 次堆分配**
        #   `array<T,N> x = {...}`        → **0 次**（栈定长，不分配）
        if isinstance(_v, list) and not isinstance(_v, Text) \
                and self._is_list_init(st) and _tn is not None:
            _v = self._build_from_list_init(st, _tn, _v, env)
        # **按声明类型落值**：`u64` 在这里变成无符号值（`0 - 1` → 2^64-1），
        # `i64` 保持补码，`f32` 按 32 位舍入，容器逐元素转换。
        _v = _convert_to(_v, _tn)
        env[st.name] = _v
        # **逐帧类型登记**：后面的赋值 / 下标写 / `.push` 要按它转换。
        if _tn is not None:
            self._tenv()[st.name] = _tn
        # **`const` 局部**：`return` 时不能隐式移动（设计 §6.3）。
        if st.kind == "const-decl" and st.name:
            self._const_frames[-1].add(st.name)
        return
    def _k_expr_list_init(self, n, env, force):
        """`{ e1, e2, ... }` —— 列表初始化，按**书写顺序**求值（副作用顺序要正确）。

        这里只返回普通 Python list；**容器类型由声明决定**，
        转换在 `_k_stmt_const_decl` 里做（那里拿得到类型节点与容量）。
        """
        return [self._eval(c, env, force) for c in n.children]

    def _default_value_of(self, st):
        """`类型 名字;`（没有 `=`）的默认值。

        三种语义，与 C++ 的直觉一致：

        | 声明 | 默认值 | 分配 |
        |---|---|---|
        | `vector<T> xs;` | **空**（长度 0） | 0（第一次 push 才 +1） |
        | `array<T,N> a;` / `FixedVec<T,N> a;` | **N 个零** | 0（栈定长） |
        | 标量 `u64 i;` | `0` | 0 |

        **为什么 `array` 是"N 个零"而不是"空的、之后 push"**：
        定长数组的 N 是类型的一部分，它**没有"长度 0 的定长数组"这种状态**；
        而且作者写 `array<u64, N> dp;` 紧接着 `dp[i] = x;` 是最常见的"建表"写法，
        若给空表，这个写法会当场越界。C 的 `u64 dp[N];` 也是"有 N 个位置"。

        **2026-11-10 补**：`S s;`（结构体）以前掉到最后那句 `_convert_to(0, tn)`，
        得到的是整数 `0`——于是 `s.f = 1;` 在运行期报「未实现的赋值目标：field/f」
        （结构体从来没有被默认构造出来）。现在按声明逐字段零构造，`&s` 才有
        "一块存储"可指。指针 `T*` 的默认值是**空指针**（`None`），不是 0。
        """
        _ty = st.children[0] if st.children else None
        tn = (_ty if getattr(_ty, "kind", "") in ("type-name", "type-ref", "type-ptr")
              else (_ty.children[0] if (_ty is not None and _ty.children) else None))
        if tn is None:
            return 0
        return self._zero_of_type(tn, st.name)

    def _zero_of_type(self, tn, name: str = ""):
        """按类型节点造一个**零值**（默认构造的递归实现）。

        `name` 只用于报错文案（`HashMap` 没有默认构造那条）。
        """
        if getattr(tn, "kind", "") == "type-ptr":
            # `u64* p;` → **空指针**。C++ 里是未初始化（读是 UB），本语言给
            # 确定值：判空 `if (p)` 立刻为假，解引用报 `E-Pointer-NullDeref`
            # ——"同一程序两个答案"这类不确定状态不在值域里。
            return None
        head = _type_label(tn)
        if head in self.structs:
            # **结构体默认构造**：逐字段零值（字段顺序即声明顺序）。
            return StructVal(head, {f: self._zero_of_type(ft, f)
                                    for f, ft in self.structs[head].items()})
        if head in ("array", "fixedvec"):
            cap = _fixed_capacity(tn, self.globals)
            if cap is None:
                raise InterpError(
                    f"`{name}` 声明为定长数组，但容量推不出来，无法默认构造")
            elem = _elem_type_node(tn)
            # **每个元素各造一次**：`[zero] * cap` 会让 `a[0].f = 1` 改到
            # **所有**元素上（同一个对象被 cap 个别名都指着）。标量时两者等价，
            # 元素是结构体 / 容器时不是。
            return FixedList(cap, [self._zero_of_type(elem, name)
                                   for _ in range(cap)])
        if head == "vector":
            # `vector<u8> b;` 的"空字节串"也是 `Text`——否则 `b.push(104)`
            # 之后 `print(b)` 又会退回数字（同一份值两种打印）。
            return Text() if _type_label(_elem_type_node(tn)) == "u8" else []
        if head == "map":
            return MapVal(key_ty=_key_type_node(tn), val_ty=_elem_type_node(tn))
        if head == "set":
            return SetVal(elem_ty=_elem_type_node(tn))
        if head == "stack":
            return StackVal()
        if head == "queue":
            return QueueVal()
        if head == "unordered_map":
            return UnorderedMapVal(val_ty=_elem_type_node(tn))
        if head == "unordered_set":
            return UnorderedSetVal()
        if head == "HashMap":
            # **stdlib 的 HashMap 没有默认构造**：它的容量/探测策略由
            # `map_new()` 决定，凭空造一个会让 `map_put` 作用在"半成品"上。
            # 明确报错 + 给出写法，不要掉到下面的"标量 → 0"（那会得到
            # `map_put(0, …)` 这种运行期 TypeError，比报错更难查）。
            raise InterpError(
                f"`{name}` 声明成 `HashMap<…>` 但没有初值："
                f"HashMap 由 `map_new()` 建（`HashMap<u64, u64> {name} = map_new();`）。"
                f"要语言自带的 `map<K,V>` 请写 `map<u64, u64> {name};`")
        if head in ("f32", "f64"):
            return 0.0
        if head == "bool":
            return False
        if head in ("str", "string"):
            return ""
        # 整数与其它标量：0（与 C++ 的 `int i{}` 一致；未初始化读是 UB，
        # 这里给确定值，宁可保守也不要"同一程序两个答案"）
        return _convert_to(0, tn)

    def _map_index_read(self, base, key):
        """`m[k]` 的**读**语义 = `std::map::operator[]`：缺键插入 `V{}` 并返回它。

        C++ 里 `m[k]` 是"取 value 的引用"：键不存在就先**默认构造**一个 value
        插进去，再返回那个引用。所以 `adj[u].push_back(v)`（邻接表）、
        `cnt[x] = cnt[x] + 1`（词频）、`if (m[k] == 0)`（当 0 用）全都依赖它。

        **这是 classic C++ 陷阱**：只想读却改了容器（`check` 期有
        `W-Map-SilentInsert` 提示，见 `typecheck.py`）。真只读请写
        `m.get(k)`（缺键报错）或先 `m.contains(k)`。

        **代价照 C++ 记**：真的插入了就 +1 次节点分配（与 `m.insert` 同源，
        AVL 插入一个节点）。已存在时零分配。

        **值语义**：插入的是**一份新的**默认值（`_zero_of_type` 每次递归新建），
        容器/结构体值的默认值是空容器/零值；返回的是树里那个对象本身，
        所以 `m[k].push_back(v)` 改的就是表里的值（C++ 的引用语义）。
        """
        if base.has(key):
            return base.get(key)
        _vt = base.val_ty
        _def = self._zero_of_type(_vt, "map 的值") if _vt is not None else 0
        base.put(key, _def)
        self.allocs += 1
        return base.get(key)

    def _is_list_init(self, st) -> bool:
        return bool(st.children) and getattr(st.children[-1], "kind", "") == "list-init"

    def _build_from_list_init(self, st, tn, values, env):
        """把 `{…}` 求值出的 list 按声明类型变成真容器。"""
        head = _type_label(tn)
        if head in ("array", "fixedvec"):
            cap = _fixed_capacity(tn, self.globals)
            if cap is None:
                raise InterpError(
                    f"`{st.name}` 的定长容量推不出来，无法校验大括号初始化的元素个数")
            if len(values) != cap:
                raise InterpError(
                    f"E-Array-InitLen：`{st.name}` 声明容量 {cap}，"
                    f"但大括号里给了 {len(values)} 个元素")
            # 栈定长：**不分配**（与 `std::array<T,N> x{...}` 一致）
            elem = _elem_type_node(tn)
            return FixedList(cap, [_convert_to(x, elem) for x in values])
        if head == "vector":
            # 非空的 `vector<T> x = {...}` 在真实 C++ 里要分配一块堆内存 → 记 1 次
            if values:
                self.allocs += 1
            elem = _elem_type_node(tn)
            return [_convert_to(x, elem) for x in values]
        raise InterpError(
            f"`{tn.name}` 不支持大括号初始化（只有 vector / array / FixedVec 支持）")

    def _k_stmt_assign(self, st, env, force):
        rhs = st.children[1] if len(st.children) > 1 else None
        val = self._eval(rhs, env, force) if rhs is not None else None
        self._store(st.children[0], val, env, force, rhs)
        return
    def _k_stmt_expr_stmt(self, st, env, force):
        self._eval(st.children[0], env, force)
        return
    def _k_stmt_return(self, st, env, force):
        if not st.children:
            raise _Return(None)
        expr = st.children[0]
        val = self._eval(expr, env, force)
        # **值语义入口 ④：`return <借用值>` 是拷贝**（设计 §3.2 / §6.3）。
        #   `return box.get(1);` / `return p;`（`const&` 形参）/ `return xs[0];`
        #   / `return s.f;`（容器的字段）/ `return <const 局部>;` → **拷贝**
        # 而 `return 非 const 局部 / 按值形参;` 是**隐式移动**、`return T{...}`
        # 是 prvalue（强制省略）→ **不拷贝**，`_is_movable_return` 负责区分。
        if _copyable(val) and _is_lvalue(expr) \
                and not self._ret_is_ref() and not self._is_movable_return(expr):
            val = self._copy_value(val)
        raise _Return(val)
    def _k_stmt_if(self, st, env, force):
        cond = self._eval(st.children[0], env, force)
        blocks = [c for c in st.children[1:] if c.kind == "block"]
        if cond:
            if blocks:
                self._block(blocks[0], env, force)
            return
        # else / else if 链：`elif` 节点可能是 if 或 block
        rest = [c for c in st.children[1:] if c.kind in ("block", "if")]
        for c in rest[1:]:
            if c.kind == "block":
                self._block(c, env, force)
                return
            self._stmt(c, env, force)
            return
        return
    def _k_stmt_parallel(self, st, env, force):
        body = stmt_body(st)
        if body is None:
            return
        tasks = [c for c in body.children if c.kind == "block"]
        if not tasks:
            return
        if self.parallel_mode != "threads" or len(tasks) == 1:
            for task in tasks:
                self._block(task, env, force)
            return
        # **真线程**：每个任务一份**自己的绑定表**，跑完把"它改过的变量"合并回来。
        # 任务之间不许共享可变状态（检查器已保证），所以浅拷贝绑定表就够——
        # 共享的容器只读，写只发生在各自的新绑定上。
        import concurrent.futures as _cf
        import threading as _th

        results: List[Dict[str, Any]] = [{} for _ in tasks]
        errors: List[BaseException] = []

        def _run(i: int, task: Node) -> None:
            local = dict(env)
            # 每个任务一条**独立且确定**的随机流：由 (基种子, 任务下标) 导出，
            # 于是同种子同程序 → 同结果，与调度无关。
            stdlib.set_task_stream(self.rand_seed, i)
            try:
                self._block(task, local, force)
            except BaseException as exc:            # noqa: BLE001
                errors.append(exc)
                return
            for kk, vv in local.items():
                if kk in env and vv is not env[kk]:
                    results[i][kk] = vv
            stdlib.clear_task_stream()

        with _cf.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futs = [pool.submit(_run, i, tsk) for i, tsk in enumerate(tasks)]
            for f in futs:
                f.result()
        if errors:
            raise errors[0]
        for piece in results:
            env.update(piece)
        return
    # ---------- repeats：运行期断言（2026-11-09）
    #
    # 背景：`repeats` 以前只被 cost 域读取，**运行期完全不看**。于是一个
    # `spec pending(...) { repeats 4; }` 配一个跑 999 圈的循环，`check` 全绿、
    # `cost` 判「达成」、`run` 也照跑（实测 `_tier_a/union_find/probe_repeats`）。
    # 它又是本语言里**唯一**能表达亚线性圈数（`log n` / `α(n)`）的通道——
    # 既是唯一诚实的写法，又是唯一靠一句假话就能让契约变绿的门。
    # 所以它必须和 `guarantees` 同一个哲学：**声明了什么，运行期就校验什么**。
    #
    # 口径（2026-11-09 定）：
    #   · **总是开**，没有开关。`guarantees` 也没有开关；可关掉的断言等于
    #     默认关掉的断言。
    #   · 只校验「`while` 的条件**恰好是**一个带 repeats 的谓词调用」这个
    #     已定义的通道（T13 来源②）。`and`/`or` 组合里的谓词不查：那里的
    #     圈数上界是 min/max 组合（`_min_cost`），按单条 repeats 判会**误报**。
    #   · 上界在**进入循环时**求一次（谓词形参按本次实参绑定）；圈数逐圈
    #     累加，第一次超过就抛 `E-Repeats-Exceeded`——不等循环跑完，因为
    #     假声明可能配一个跑 10⁹ 圈的循环。
    #   · 上界求不出来（未定义名字 / 认不出的形状）同样是**语言诊断**
    #     `E-Repeats-Uncheckable`：静默跳过正是这个 bug 的成因。
    #
    # 开销：每个带 repeats 的循环进入时一次求值 + 每圈一次自增/比较。
    # 它**不推进** `_tick` / ops 计数，所以 `measure` 的 ops 基线与开关无关。
    def _repeats_bound(self, name: str):
        """spec 的 repeats 表达式 → `bound.Bound`；认不出 → None。

        用 cost 域**同一个解析器**（`expr_to_bound`）：`repeats` 只有一份语义，
        运行期与 κ 必须读同一个形状，否则又多一个"两边各自解释"的洞。
        """
        if name in self._repeats_bound_cache:
            return self._repeats_bound_cache[name]
        node = getattr(self, "repeats_expr", {}).get(name)
        b = None
        if node is not None:
            try:
                from cost_domain.bound import expr_to_bound
                # 与 `cost_interp._collect_repeats` 同一个入口口径：
                # repeats 允许 `绑定项 + 整数字面量`（`log(hi - lo) + 1`）。
                b = expr_to_bound(node, allow_add=True)
            except Exception:                          # noqa: BLE001
                b = None
        self._repeats_bound_cache[name] = b
        return b

    def _repeats_arg_env(self, name: str, call: Node,
                         env: Dict[str, Any]) -> Dict[str, Any]:
        """把谓词形参按**本次实参**绑上（只用无副作用求值）。

        repeats 写的是谓词形参（`log(hi - lo)` / `operators.length()`），
        跑循环时能直接看见的却是**调用方的名字**。两者常常同名（样例都这样），
        但不同名时不能就此判"求不出"。
        """
        spec = self.specs.get(name) if getattr(self, "specs", None) else None
        if spec is None:
            return {}
        params = next((c for c in (spec.children or [])
                       if getattr(c, "kind", "") == "params"), None)
        args = next((c for c in (call.children or [])
                     if getattr(c, "kind", "") == "args"), None)
        if params is None or args is None:
            return {}
        extra: Dict[str, Any] = {}
        for p, a in zip(params.children, args.children):
            nm = getattr(p, "name", "") or ""
            if not nm:
                continue
            v = self._count_eval(a, env)
            if v is not None:
                extra[nm] = v
        return extra

    def _count_eval(self, n, env) -> Any:
        """**无副作用**的实参求值：字面量 / 名字 / 尺寸 / 四则运算。

        不用 `_eval`：那会推进步数与 ops 计数——**校验不该改测量基线**。
        认不出返回 None（调用方按"这个形参绑不上"处理）。
        """
        k = getattr(n, "kind", "")
        ch = getattr(n, "children", ()) or ()
        if k == "paren" and ch:
            return self._count_eval(ch[0], env)
        if k == "lit-int":
            try:
                return int(n.value)
            except (TypeError, ValueError):
                return None
        if k == "path":
            return env.get(n.name)
        if k in ("method-call", "call"):
            short = (n.name or "").split(".")[-1]
            if short in ("length", "size", "len", "capacity") and ch:
                v = self._count_eval(ch[0], env)
                try:
                    return len(v)
                except TypeError:
                    return None
            return None
        if k == "binary" and len(ch) >= 2:
            op = n.value or ""
            a = self._count_eval(ch[0], env)
            b = self._count_eval(ch[1], env)
            if a is None or b is None:
                return None
            try:
                if op == "+":
                    return a + b
                if op == "-":
                    return a - b
                if op == "*":
                    return a * b
                if op == "/":
                    return a // b
                if op == "%":
                    return a % b
            except (ZeroDivisionError, TypeError):
                return None
        return None

    def _bound_value(self, b, env) -> Any:
        """`Bound`（repeats 的形状）在**运行期具体值**上求值；未知 → None。"""
        k = getattr(b, "kind", "")
        if k == "const":
            try:
                return int(b.value)
            except (TypeError, ValueError):
                return None
        if k == "size":
            v = env.get(b.value)
            if v is None or isinstance(v, (int, float, bool)):
                return None
            try:
                return len(v)
            except TypeError:
                return None
        if k == "var":
            v = env.get(b.value)
            if isinstance(v, bool):
                return int(v)
            if isinstance(v, (int, float)):
                return v
            return None
        if k in ("sub", "min", "max"):
            a = self._bound_value(b.left, env)
            c = self._bound_value(b.right, env)
            if a is None or c is None:
                return None
            if k == "sub":
                return a - c
            return min(a, c) if k == "min" else max(a, c)
        if k == "add":
            # repeats 的 `绑定项 + 整数字面量`（`log(hi - lo) + 1`）
            a = self._bound_value(b.left, env)
            c = self._bound_value(b.right, env)
            if a is None or c is None:
                return None
            return a + c
        if k == "log2":
            v = self._bound_value(b.left, env)
            if v is None:
                return None
            if v <= 1:
                return 0.0
            try:
                import math
                return math.log2(v)
            except (ValueError, OverflowError, TypeError):
                return None
        return None

    def _repeats_limit(self, cond, env):
        """`while` 的条件若是带 repeats 的谓词调用 → `(整数上界, 声明文本, 名字)`。

        不是这个形状 → None（不校验）。上界求不出 → `E-Repeats-Uncheckable`。
        """
        node = cond
        while node is not None and getattr(node, "kind", "") == "paren":
            node = node.children[0] if node.children else None
        if (node is None or getattr(node, "kind", "") != "call"
                or not getattr(node, "name", "")):
            return None
        name = (node.name or "").split(".")[-1]
        rep_node = getattr(self, "repeats_expr", {}).get(name)
        if rep_node is None:
            return None
        b = self._repeats_bound(name)
        declared = b.render() if b is not None else _expr_text(rep_node)
        if b is None:
            raise InterpError(
                f"{E_REPEATS_UNCHECKABLE}：`{name}` 的 spec 声明了 "
                f"`repeats {declared}`，但运行期认不出这个形状"
                f"（只认常数 / 尺寸 / 标量 / log2 / 加减）——"
                f"**这条上界无法校验**，等于没有声明。请改成可求值的形式")
        e2 = env
        extra = self._repeats_arg_env(name, node, env)
        if extra:
            e2 = dict(env)
            e2.update(extra)
        val = self._bound_value(b, e2)
        if val is None:
            raise InterpError(
                f"{E_REPEATS_UNCHECKABLE}：`{name}` 的 repeats `{declared}` "
                f"依赖运行期求不出的值（名字既不是形参、也不在当前帧里）——"
                f"**这条上界无法校验**，等于没有声明")
        try:
            limit = int(val)
        except (TypeError, ValueError, OverflowError):
            limit = None
        if limit is None:
            raise InterpError(
                f"{E_REPEATS_UNCHECKABLE}：`{name}` 的 repeats `{declared}` "
                f"求值得到 {val!r}，不是可比较的圈数")
        if limit < val:                 # log2(3)=1.58 → 上界 2（3 个数上真会转 2 圈）
            limit += 1
        # **不再按「阶」放宽**（2026-11-10）：上一版对含 log2 的界额外 +1 圈，
        # 那是绕开 `search.tu` 的 off-by-one，不是解决。现在 repeats 能写
        # `log(hi - lo) + 1`（`expr_to_bound(allow_add=True)`），样例也改成了
        # 严格上界，所以这里**只按求出的值比**：常数 / 尺寸 / 对数上界同一把尺，
        # `repeats 4` 跑 5 圈抓，`repeats log2(8)` 跑 4 圈（真值 3）也抓。
        return (limit, declared, name)

    def _k_stmt_while(self, st, env, force):
        cond, body = st.children[0], next(
            (c for c in st.children[1:] if c.kind == "block"), None)
        # **`repeats` 的运行期断言**（见本节开头）。进入循环时求一次上界。
        # 求不出上界时**先不抛**：记下诊断，等循环真的要转第一圈再抛——
        # 一次都不转的循环谈不上"超过声明的圈数"，当场报错会是假阳性。
        _rep = None
        _rep_err = None
        try:
            _rep = self._repeats_limit(cond, env)
        except InterpError as _exc:
            _rep_err = _exc
        _rep_n = 0
        # 记下"这个循环的条件里有没有 u64 下溢"的证据，超限时用
        # （嵌套循环：内层会覆盖/恢复，见 for 里的同样处理）。
        _prev_hint = self._budget_hint
        self._budget_hint = self._u64_underflow_hint(cond, env, force)
        # **第二个证据**：有具体数值上界、计数器在推进 ⇒ 是规模问题不是死循环。
        _prev_bound = self._budget_bound_hint
        self._budget_bound_hint = self._finite_bound_probe(cond, env)
        try:
            while self._eval(cond, env, force):
                self._tick()
                self.iters += 1
                if _rep_err is not None:
                    raise _rep_err
                if _rep is not None:
                    _rep_n += 1
                    if _rep_n > _rep[0]:
                        raise InterpError(
                            f"{E_REPEATS_EXCEEDED}：谓词 {_rep[2]} 的 spec 声明了 "
                            f"`repeats {_rep[1]}`（while 的圈数上界），但实际已经"
                            f"跑了 {_rep_n} 圈——**声明是假的**，κ 不再是上界。"
                            f"修法：把 repeats 改成真实上界，或让循环真的不超过它")
                if body is not None:
                    try:
                        self._block(body, env, force)
                    except _Continue:
                        continue
                    except _Break:
                        break
        finally:
            self._budget_hint = _prev_hint
            self._budget_bound_hint = _prev_bound
        return
    def _k_stmt_break(self, st, env, force):
        raise _Break()

    def _k_stmt_continue(self, st, env, force):
        raise _Continue()

    def _k_stmt_for(self, st, env, force):
        body = stmt_body(st)
        var = st.name
        base = for_iterable(st)
        #: **绑定类别**（2026-11-10 夜）：`"mut"` / `"const"` = 引用（不拷贝），
        #: `None` = 按值（每轮拷一份）。parser 写进 `Node.ref`。
        _ref = getattr(st, "ref", None)
        # for 自己的超限消息由下面的检查给出；**清掉外层 while 的下溢线索**，
        # 免得"外层条件里有下溢"被错记到一次正常的长循环上。
        _prev_hint = self._budget_hint
        self._budget_hint = None
        # **`..` 区间已删除**：`for` 的被迭代对象现在是普通表达式（容器 /
        # 键视图 / `range(...)`）。指针仍然不能被遍历。
        seq = self._eval(base, env, force) if base is not None else []
        #: **能不能写回元素**：只有"真序列"（vector / stack / queue / deque /
        #: 定长数组）才行。`map` / `unordered_map` 的遍历元素是**每轮现造的
        #: `(键, 值)` 二元组**（`set` / `unordered_set` 是元素视图），写回只会
        #: 写到临时列表上——所以这里明确不写回、重新绑定就报错。
        _seq_writable = isinstance(seq, list)
        # **指针不能被遍历**（2026-11-10 指针接线）：指针只知道"指向一个对象"，
        # 没有长度 / 范围。不挡的话宿主要抛
        # `TypeError: 'PtrVal' object is not iterable`——宿主异常不是语言诊断。
        if isinstance(seq, PtrVal):
            raise InterpError(
                "指针不能被 `for` 遍历：指针只指向**一个**对象，没有长度 / 范围。"
                "要遍历请用容器（`for (auto& x : p->arr)`），或数量已知时用"
                "下标 / 计数器（`for (u64 i = 0; i < n; i = i + 1)`）。")
        # **`map` / `unordered_map` 的 range-for 元素是 `(键, 值)` 二元组**
        # （2026-11-12）：C++ 里 `for (const auto& kv : m)` 的 `kv` 是
        # `pair<const K, V>`，**不是键**。以前这里迭代的是键——`kv.first` /
        # `kv.second` 在 run 期报「未实现的字段访问 first」，是 check 绿、
        # run 崩的那一类（`demo/corpus/11_text.cpp` 实测）。
        # 本语言已有的二元组就是 `(K, V)`（字段 `.0` / `.1`；`--compat` 下
        # `std::pair` 的 `.first` / `.second` 也映射到它），所以不新造类型。
        # `map` 是**有序**的（键序可复现），`unordered_map` 给插入序——
        # 但文档明说别依赖它。`set` / `unordered_set` 仍遍历元素本身。
        _pair_mode = False
        if isinstance(seq, MapVal):
            seq = seq.items()         # [(键, 值)]，键序（AVL 中序）
            _pair_mode = True
        elif isinstance(seq, UnorderedMapVal):
            seq = list(seq.data.items())
            _pair_mode = True
        elif isinstance(seq, SetVal):
            seq = seq.values()        # 元素序（AVL 中序）
        elif isinstance(seq, UnorderedSetVal):
            seq = list(seq.data)
        elif not isinstance(seq, list):
            seq = list(seq) if seq is not None else []
        _idx_var = getattr(st, "value", None) or None
        # **循环变量的声明类型**：下标恒为 `u64`；元素取容器的元素类型。
        # `map` / `unordered_map` 的元素是**二元组** `(K, V)`（与运行期一致：
        # 静态类型和值不同形，就是 check 绿、run 崩的来源）。
        _tn = None
        if base is not None and getattr(base, "kind", "") == "path":
            _bt = self._type_of_var(base.name or "")
            head, _args = _type_parts(_bt)
            if head in ("map", "unordered_map"):
                _tn = (_pair_type_node(_key_type_node(_bt), _elem_type_node(_bt))
                       or _key_type_node(_bt))
            else:
                _tn = _elem_type_node(_bt)
        if _idx_var:
            self._tenv()[_idx_var] = "u64"
        if var:
            if _tn is not None:
                # **引用迭代的类型记成 `type-ref`**：`return x;` 于是走"拷贝"
                # （借用的元素不能被隐式移动走），与 C++ 一致。
                self._tenv()[var] = (Node("type-ref", st.line, st.col).add(_tn)
                                     if _ref else _tn)
        for _n, item in enumerate(seq):
            self._tick()
            self.iters += 1
            if _idx_var:
                env[_idx_var] = U64(_n)
            if _ref is None:
                # **值语义入口 ⑥：按值迭代的元素绑定是拷贝**（第 7 个拷贝
                # 位置，设计 §4.3 第 5 条）。元素是标量 → `_copy_value` 原样
                # 返回（0 开销）；元素是容器/结构体 → 每轮拷一份。
                env[var] = self._copy_value(item)
            else:
                # **引用迭代不拷贝**：`env[var]` 就是容器里那个元素本身。
                # `auto& row : xs` 里 `row.push(9)` 改的就是 `xs` 的元素
                #（修前这里按值拷一份，容器纹丝不动——静默错值）。
                env[var] = item
            try:
                if body is not None:
                    self._block(body, env, force)
            except _Continue:
                continue
            except _Break:
                break
            finally:
                # **可变引用要写回**：`for (auto& x : xs) { x = x + 1; }` 里
                # `x = …` 在 C++ 改的是元素；我们绑定的是元素对象，标量元素
                # 重新绑定后必须写回 `seq[_n]`。`map` / `set` 的遍历元素是
                # **每轮现造的视图**（二元组 / 元素），写不回去。
                if (_ref == "mut" and _seq_writable and isinstance(seq, list)
                        and env.get(var) is not item):
                    seq[_n] = env[var]
                elif _ref == "mut" and not _seq_writable \
                        and env.get(var) is not item:
                    if _pair_mode:
                        raise InterpError(
                            "E-Assign-Immutable：`for (auto& kv : m)` 的 `kv` "
                            "是每轮现造的 `(键, 值)` 二元组，**不能整体重新绑定**"
                            "（写回去也只写到那份临时副本）。\n"
                            "改表的值请用 `m[键] = 新值` / `m.put(键, 新值)`；"
                            "只读就写 `for (const auto& kv : m)`。")
                    raise InterpError(
                        "E-Assign-Immutable：`for (auto& x : …)` 遍历的是"
                        "**元素视图**（`map` / `set`），元素本身不可修改。"
                        "要按值取一份再改请写 `for (auto x : …)`。")
                elif _ref == "const" and env.get(var) is not item:
                    raise InterpError(
                        "E-Assign-Immutable：`for (const auto& x : xs)` 的循环变量是"
                        "**只读引用**，不能重新绑定。要改元素请写 "
                        "`for (auto& x : xs)`（可变引用）；只读就用 "
                        "`const auto&` 且不要给 `x` 赋值。")
        self._budget_hint = _prev_hint
        return
    def _k_stmt_match(self, st, env, force):
        return self._match(st, env, force)
    def _k_stmt_block(self, st, env, force):
        self._block(st, env, force)
        return
    def _k_stmt_dispatch(self, st, env, force):
        self._eval(st, env, force)
        return


    def _store(self, target: Node, val: Any, env, force, rhs: Optional[Node] = None) -> None:
        """把 `val` 落到左值 `target` 上；`rhs` 是右值节点（判左值/右值要用）。

        **值语义入口 ②：赋值是拷贝赋值**（设计 §4.1 的第 2 列）。
        右值是左值（`a = b`）→ 拷贝一份再存；prvalue（`a = f()` / `a = {…}`）
        → 直接存（C++ 是移动/省略）。
        """
        if target.kind == "path":
            if target.name not in env:
                raise InterpError(f"赋值给未绑定的名字 {target.name}")
            if rhs is not None and _copyable(val) and _is_lvalue(rhs):
                val = self._copy_value(val)
            # **按声明类型落值**：`u64 x; x = 0 - 1;` 存进去的是 2^64-1，
            # 不是 -1；`i64` 仍是补码；窄类型按各自位宽截断。
            _tn = getattr(target, "_store_ty", None)
            if _tn is None:
                _tn = self._type_of_var(target.name or "") or _NONE_TY
                target._store_ty = _tn
            if _tn is _NONE_TY:
                _tn = None
            # 快路径：目标就是 `u64` 且值已经规范化 → 不用再转换（循环热路径）
            if type(val) is U64 and _type_label(_tn) == "u64":
                env[target.name] = val
                return
            env[target.name] = _convert_to(val, _tn)
            return
        if target.kind == "field":
            if rhs is not None and _copyable(val) and _is_lvalue(rhs):
                val = self._copy_value(val)
            base = self._eval(target.children[0], env, force)
            _tn = self._target_type(target)
            if isinstance(base, list) and target.name is not None and target.name.isdigit():
                base[int(target.name)] = _convert_to(val, _tn)
                return
            if isinstance(base, StructVal):
                # `p.x = 5`（v0.72）：以前只有 `p.x += 5` 能走，`=` 报"未实现"
                base.fields[target.name] = _convert_to(val, _tn)
                return
            if isinstance(base, tuple):
                # **元组的字段不能就地写**（2026-11-12）：元组是**值类型**
                # （不可变聚合），写字段没有可落的地方。以前落到这里报
                # 「未实现的赋值目标：field/0」——宿主故障式文案，而且
                # `check` 是**绿的**（假绿）。现在两边都明确拒绝：
                # 想改就整体重建 `t = (新0, t.1);`；`map` 迭代出的
                # `(键, 值)` 要改表请写 `m[键] = 新值`。
                raise InterpError(
                    f"E-Assign-Immutable：元组的字段 `.0` / `.1` "
                    f"（`--compat` 下的 `.first` / `.second`）**不能赋值**："
                    f"元组是值类型，没有可写的存储（这里是 {len(base)} 元组）。\n"
                    f"要换元素就整体重建：`t = (新值0, t.1);`；\n"
                    f"`for (const auto& kv : m)` 的 `kv` 是 `(键, 值)` 视图，"
                    f"改表请写 `m[键] = 新值` / `m.put(键, 新值)`。")
            raise InterpError(f"未实现的赋值目标：{target.kind}/{target.name}")
        if target.kind == "index":
            # **下标写同样是拷贝赋值**（`a[i] = b` / `m[k] = v`）。
            # 键也是按值进节点的（C++ 的 `operator[]` 收 `const key_type&`），
            # 左值键要拷一份——否则 `k` 之后被改，容器里的键会跟着变。
            _idx_node = target.children[1] if len(target.children) > 1 else None
            base = self._eval(target.children[0], env, force)
            idx = self._eval(_idx_node, env, force) if _idx_node is not None else None
            if _idx_node is not None and _copyable(idx) and _is_lvalue(_idx_node):
                idx = self._copy_value(idx)
            if rhs is not None and _copyable(val) and _is_lvalue(rhs):
                val = self._copy_value(val)
            # **`m[k] = v`**（2026-11-08）：以前直接 `base[idx] = val`，
            # 而 `MapVal` 不是 Python 容器 → `TypeError: 'MapVal' object does
            # not support item assignment`。map 的下标写是 C++ 的常规写法，
            # 不能有"check 过了、run 才炸"。`MapVal` 自带键/值类型，会自己转换。
            if isinstance(base, MapVal):
                base.put(idx, val)
                return
            if isinstance(base, UnorderedMapVal):
                base.data[idx] = val
                return
            base[_index(idx, base)] = _convert_to(val, self._target_type(target))
            return
        if target.kind == "deref":
            # **`*p = v` 写到那块存储里**（2026-11-10 指针接线）：这才是"唯一
            # 允许别名的地方"——`*p = 9` 改的就是 `x`，不是 `p` 的副本。
            # 值语义入口 ② 照旧：右值是左值才拷贝（`*p = s;` 里的 `s` 是左值）。
            if rhs is not None and _copyable(val) and _is_lvalue(rhs):
                val = self._copy_value(val)
            ptr = self._eval(target.children[0], env, force) \
                if target.children else None
            if ptr is None:
                raise InterpError(
                    f"{E_POINTER_NULL_DEREF}：给**空指针的解引用**赋值"
                    f"（`*p = …`）——运行期真的拿到了空地址。修法：写之前判空"
                    f"（`if (p != nullptr) {{ *p = …; }}`）。")
            if not isinstance(ptr, PtrVal):
                raise InterpError(
                    f"`*p = …` 的左值不是指针：拿到 {type(ptr).__name__}"
                    f"（静态检查应当先报 `E-Unary-Unsupported`）")
            ptr.set(_convert_to(val, self._target_type(target)))
            return
        raise InterpError(f"未实现的赋值目标：{target.kind}")

    def _match(self, st: Node, env, force) -> None:
        val = self._eval(st.children[0], env, force)
        arms = arms_of(st)
        for arm in arms:
            pat = arm_pattern(arm)
            # 臂的体：可能是 block，也可能是一条单语句（`Pat => return 1,`）
            body = arm_block(arm)
            single = arm_single_stmts(arm)
            if pat is None:
                continue
            hit = False
            if pat.kind == "pattern-wildcard":
                hit = True
            elif pat.kind == "pattern-literal":
                _lit = str(pat.value or "").strip()
                _want = True if _lit == "true" else (False if _lit == "false" else None)
                if _want is not None:
                    hit = bool(val) == _want
                else:
                    hit = (val == int(_lit)) if isinstance(val, int) else False
            else:
                want = (pat.name or "").split(".")[-1]
                if isinstance(val, EnumVal) and val.variant == want:
                    hit = True
                    binds = [c for c in pat.children if c.kind == "binding"]
                    if binds:
                        self._bind_pattern(binds[0], val.payload, env)
                elif isinstance(val, Ok) and want == "Ok":
                    # **`Ok(v)` / `Err(e)` 也是对 Result 的匹配**（v0.71）：
                    # 它们不是 EnumVal，要单独认，否则 `match` 会静默跳过所有臂。
                    hit = True
                    binds = [c for c in pat.children if c.kind == "binding"]
                    if binds:
                        self._bind_pattern(binds[0], val.v, env)
                elif isinstance(val, Err) and want == "Err":
                    hit = True
                    binds = [c for c in pat.children if c.kind == "binding"]
                    if binds:
                        self._bind_pattern(binds[0], val.e, env)
            if not hit:
                continue
            if body is not None:
                self._block(body, env, force)
            elif single:
                self._stmt(single[0], env, force)
            return
        # 没有匹配的臂：不报错，静默跳过（类型检查器该保证穷尽）

    def _bind_pattern(self, b: Node, payload: Any, env) -> None:
        if b.name is None:
            return
        if isinstance(payload, tuple):
            env[b.name] = payload[0]
        else:
            env[b.name] = payload

    # ---------- 表达式
    def _eval(self, n: Optional[Node], env, force):
        if _COUNT and n is not None:
            _bump()
        _k = getattr(n, "kind", None)
        if _k == "lit-float":
            return float(n.value if n.value is not None else 0.0)
        if _k == "array":
            return [self._eval(c, env, force) for c in n.children]
        if _k == "deref":
            # **`*p` 读到那块存储里的东西**（2026-11-10 指针接线）。
            # 以前这里是 `return self._eval(children[0])`——"值就是它指的东西"，
            # 那是**指针不存在时期**的语义：`u64 y = *p;` 会静默拿到 `p` 自己
            # 的值。现在 `&x` 产出 `PtrVal`，这里读它指向的单元。
            #
            # 读出来的是**那块存储里的对象本身**，不是副本：`_is_lvalue(deref)`
            # 已经是 True，所以 `T a = *p;` / `a = *p;` / `push(*p)` 这些
            # 消费点会照常走值语义拷贝（①/②/⑤）——别名只通过显式的
            # `*p = …` / `p->f = …` 产生。
            return self._deref_read(n, env, force)
        if _k == "lit-null":
            # `nullptr` 的运行时值就是 `None`：假值、标量、不可深拷。
            return None
        return self._core_eval(n, env, force)

    def _deref_read(self, n: Node, env, force) -> Any:
        """`*p`：读 `p` 指向的存储。空指针 → `E-Pointer-NullDeref`（不崩宿主）。"""
        ptr = self._eval(n.children[0], env, force) if n.children else None
        if ptr is None:
            raise InterpError(
                f"{E_POINTER_NULL_DEREF}：解引用了**空指针**（`nullptr`）——"
                f"`*p` / `p->f` 在运行期真的拿到了空地址。修法：解引用前判空"
                f"（`if (p != nullptr) {{ … }}` / `if (p) {{ … }}`），"
                f"或确认这个指针的赋值路径上没有 `nullptr`。")
        if isinstance(ptr, PtrVal):
            return ptr.get()
        raise InterpError(
            f"`*p` 的操作数不是指针：拿到 {type(ptr).__name__}。"
            f"（静态检查应当先报 `E-Unary-Unsupported`——这里只是运行期兜底）")

    def _core_eval(self, n: Optional[Node], env, force) -> Any:
        if n is None:
            return None
        self._tick()
        k = n.kind

        _name = INTERP_EXPR.get(k)
        if _name is not None:
            return getattr(self, _name)(n, env, force)
        raise InterpError(f"未实现的表达式：{k}")


    def _k_expr_lit_int(self, n, env, force):
        # **字面量先保持任意精度 `int`**（`typecheck.py` 给 `lit-int` 的类型是 `i64`）。
        # 装不进 `i64` 的正字面量在**参与运算**时按 `u64` 值处理，
        # 见 `_widen_oob_literals`：那样 `18446744073709551615 - 1` 不会
        # 按 `i64` 补码回绕成 -2；而 `-9223372036854775808` 这种
        # "一元负号 + 2^63" 的 i64 最小值写法仍然打得对。
        return int(n.value)
    def _k_expr_lit_string(self, n, env, force):
        return Text(n.value or b"")
    def _k_expr_lit_char(self, n, env, force):
        # 字符字面量的类型是 `u64`（见 typecheck.py），所以带上无符号标记。
        # **转义要解码**（`'\n'` → 10，不是 `ord('\\')` = 92）：
        # 见 `_char_lit_value` 的注释。
        return _u64(_char_lit_value(n.value))
    def _k_expr_lit_float(self, n, env, force):
        # **浮点字面量也要有值**：解析器能产（`parser.py:1526`），
        # 成本域早就按 1 个操作计，具体值域却抛「未实现的表达式」——
        # 这就是 `walk.py` 报的那条 expr 侧漂移。语义上仍由类型检查器
        # 拦截（`u64 y = 1.5;` → `E-Type-FloatToInt`），这里只是不再崩。
        return float(n.value)
    def _k_expr_lit_bool(self, n, env, force):
        return n.value in (True, "true")
    def _k_expr_path(self, n, env, force):
        if n.name in env:
            return env[n.name]
        # `Token::Plus` → path "Token.Plus"：类型命名空间里的变体（无载荷）
        if "." in (n.name or ""):
            ty, va = n.name.rsplit(".", 1)
            return EnumVal(ty, va)
        raise InterpError(f"未绑定的名字 {n.name}")
    def _k_expr_paren(self, n, env, force):
        return self._eval(n.children[0], env, force)

    def _k_expr_ternary(self, n, env, force):
        """**三目 `c ? a : b`**（2026-11-13）：条件走 `if` 同一条规则，**只求值一支**。

        ## 为什么必须"分支"而不是"先都算出来再挑"

        两支都可能有**副作用**：`c ? xs.push_back(1) : 0` 在 `c` 为假时**不能**
        push，`c ? take() : peek()` 在真时不能调用 `peek`。所以这里与
        `_k_stmt_if` 同形——先求条件，再进**其中一支**。

        ## 条件语义

        与 `if` **逐字相同**：直接拿求出来的值做真值判断（`if cond:`），不在这里
        另造一套"非零为真"。静态侧收什么类型由 `typecheck` 的 `_cond_ok` 管
        （bool / 指针，`--compat` 下整数）——运行期不再重复判类型。

        ## 操作计数

        `_eval` 对每个非 None 节点记 1，所以实测 ops = 1 + cond + **被选中的那一支**；
        成本域的 κ 是 `1 + κ(cond) + max(两支)`，比它只大不小（上界成立）。
        """
        children = n.children or []
        if len(children) < 2:
            return self._eval(children[0], env, force) if children else None
        cond = self._eval(children[0], env, force)
        if cond:
            return self._eval(children[1], env, force)
        return self._eval(children[2], env, force) if len(children) > 2 else None

    def _k_expr_cast_static(self, n, env, force):
        """**未折叠的 `static_cast`**（P0 兼容层第 11 条）——不许当恒等。

        能证明"值逐位不变"的 `static_cast<unsigned char|u64>(x)` 在
        `parse_source` / `_compat_resolve_auto` 里**已经折成 `x`**，走不到这里。
        走得到说明它是一次**真转换**（窄化 / 有符号 / 类型推不出），`check`
        会报 `E-Cast-Unsupported`；这里再兜一道，**绝不**把真转换悄悄丢掉。
        """
        raise InterpError(
            "E-Cast-Unsupported：`static_cast` 没有被折叠成纯拼写——它是"
            "一次**会改值的转换**（窄化 / 有符号 / 类型推不出）。"
            "`check` 会给出具体原因；真要做转换请写 `x as T`。")
    def _k_expr_tuple(self, n, env, force):
        return tuple(self._eval(c, env, force) for c in n.children)
    def _k_expr_struct_literal(self, n, env, force):
        fields = {}
        _want = self.structs.get(n.name or "", {})
        for fi in n.children:
            if fi.kind == "field-init" and fi.children:
                # **值语义入口 ⑦：聚合初始化的成员拷贝**（设计 §3.1 第 6 条）。
                # `S s{容器}` 里成员从初始化子句**拷贝初始化**；prvalue 成员
                # （`S{…}` / `{…}` / 调用返回新对象）→ 省略。
                _fv = self._eval(fi.children[0], env, force)
                if _is_lvalue(fi.children[0]):
                    _fv = self._copy_value(_fv)
                fields[fi.name] = _convert_to(_fv, _want.get(fi.name))
        return StructVal(n.name or "", fields)
    def _k_expr_unary(self, n, env, force):
        v = self._eval(n.children[0], env, force)
        op = n.value or ""
        if op == "!":
            return not v
        # **指针没有 `-` / `~`**（2026-11-10）：`!p` 仍然可用（判空），
        # 但取负 / 取反没有意义——宿主会抛 `TypeError`，这里翻成语言诊断。
        if isinstance(v, PtrVal):
            raise InterpError(
                f"指针不支持一元 `{op}`：判空请写 `if (!p)` / `if (p != nullptr)`；"
                f"读写请用 `*p` / `p->f`。")
        if op == "-":
            # `-u64` 是无符号取负（`0 - v` 回绕）；`-i64` 仍是补码取负。
            if isinstance(v, UInt):
                return -v
            return _wrap(-v)
        if op == "~":
            # **按操作数自己的位宽取反**：`~0 as u8` → 255、`~0 as u64` → 2^64-1；
            # 有符号（`i64`/字面量）按 64 位补码，`~0` → -1。
            if isinstance(v, UInt):
                return _UCLASS[getattr(v, "bits", 64)](~int(v))
            if isinstance(v, bool):
                v = int(v)
            if isinstance(v, float):
                raise InterpError("`~` 只接受整数，收到 f64")
            return _wrap(~v)
        raise InterpError(f"未实现的一元运算 {op}")
    def _k_expr_binary(self, n, env, force):
        return self._binary(n, env, force)
    def _k_expr_call(self, n, env, force):
        return self._call(n, env, force)
    def _k_expr_method_call(self, n, env, force):
        return self._method(n, env, force)
    def _k_expr_field(self, n, env, force):
        return self._field(n, env, force)
    def _k_expr_cast(self, n, env, force):
        v = self._eval(n.children[0], env, force)
        # **指针不能 `as` 成数值**（2026-11-10）：本语言不给"地址的整数值"
        # （没有真实地址，给了也是编的）。以前它会**原样返回** `PtrVal`——
        # 声明是 `u64`、值是指针，静默错值。
        if isinstance(v, PtrVal):
            ty0 = next((c for c in n.children[1:]
                        if getattr(c, "kind", "").startswith("type")), None)
            _nm = _type_label(ty0)
            if _nm in _SCALAR_NUMERIC:
                raise InterpError(
                    f"指针不能 `as {_nm}`：本语言没有「地址的整数值」（不做指针"
                    f"与整数的转换，也没有指针算术）。要比「是不是同一个对象」，"
                    f"用 `p == q` / `p == nullptr`。")
        # **`as` 要真的截断**（v0.72）：`300 as u8` 以前返回 300。
        ty = next((c for c in n.children[1:] if getattr(c, "kind", "").startswith("type")),
                  None)
        name = _type_label(ty)
        # **枚举 → 数值：序号**（2026-11-10）。`Color::Green as u64` 以前原样
        # 返回 `EnumVal`（`run` 打印 `Color.Green`）——该是 `u64` 的地方给了
        # 枚举，是静默错值。序号 = 变体在 `enum` 声明里的位置（从 0 起）。
        if isinstance(v, EnumVal) and name in _SCALAR_NUMERIC:
            order = self.enums.get(v.type_name)
            if order is None or v.variant not in order:
                raise InterpError(
                    f"无法把枚举值 {v.type_name}.{v.variant} 转成 {name}："
                    f"`{v.type_name}` 没有可用的变体声明（序号按 `.tu` 里 "
                    f"`enum` 的声明顺序算）。")
            v = order.index(v.variant)
        if isinstance(v, float) and name in _SCALAR_NUMERIC:
            v = int(v)
        return _convert_scalar(v, name) if name else v
    def _k_expr_try(self, n, env, force):
        v = self._eval(n.children[0], env, force)
        if isinstance(v, Err):
            raise _Return(v)
        if isinstance(v, Ok):
            return v.v
        return v
    def _k_expr_dispatch(self, n, env, force):
        return self._dispatch(n, env, force)
    def _k_expr_index(self, n, env, force):
        base = self._eval(n.children[0], env, force)
        idx = self._eval(n.children[1], env, force)
        # **指针不能下标**（2026-11-10）：`p[i]` 在本语言里没有定义（不是
        # `(*p)[i]`，也不是 C++ 的指针步进）。不挡的话宿主抛
        # `TypeError: 'PtrVal' object is not subscriptable`。
        if isinstance(base, PtrVal):
            raise InterpError(
                "指针不能用下标（`p[i]`）：本语言没有指针步进 / 指针算术。"
                "要取被指对象里的元素请先解引用（`(*p)[i]` 或 `p->arr[i]`）。")
        # **`m[k]` 读 = C++ 的 `std::map::operator[]`**（2026-11-11，审计 A3）。
        # 缺键**插入一个 `V{}` 并返回它**，不再报「map 里没有键」。
        # 以前缺键即错，于是 `adj[u].push_back(v)` / `cnt[x] = cnt[x] + 1` /
        # `if (m[k] == 0)` 这一整族 C++ 惯用法 **check 全绿、run 才炸**。
        if isinstance(base, MapVal):
            return self._map_index_read(base, idx)
        if isinstance(base, UnorderedMapVal):
            # **与 `MapVal` 同一口径**（2026-11-12）：`um[k]` 缺键插入 `V{}` 并返回。
            # 之前这里报错，于是 `freq[w] += 1`（unordered 词频）check 绿、run 炸。
            if idx in base.data:
                return base.data[idx]
            _vt = getattr(base, "val_ty", None)
            _def = self._zero_of_type(_vt, "unordered_map 的值") \
                if _vt is not None else 0
            base.data[idx] = _def
            self.allocs += 1
            return _def
        _v = base[_index(idx, base)]
        # **元素按容器的声明类型转换**：宿主传进来的 `vector<u64>`（测试里
        # 常是裸 Python list）里的元素没有无符号标记，读出来时补上。
        if getattr(n.children[0], "kind", "") == "path":
            _etn = getattr(n, "_elem_ty_cache", None)
            if _etn is None:
                _tn = self._type_of_var(n.children[0].name or "")
                _etn = _elem_type_node(_tn) if _tn is not None else _NONE_TY
                n._elem_ty_cache = _etn
            if _etn is not _NONE_TY:
                _v = _convert_read(_v, _etn)
        return _v
    def _k_expr_type_name(self, n, env, force):
        return TypeRef(n.name or "")
    def _k_expr_ref(self, n, env, force):
        """`&x`：**取地址**——产出指向 `x` 这块存储的 `PtrVal`（2026-11-10 接线）。

        以前这里是 "`&x` 只是按引用传的标记，值就是它指的东西"：`&` 在运行期
        恒等。现在它有真语义，但要与**引用形参的显式借用标记**（`take(&a)`，
        `calculator.eln` 依赖）共存——那条由 `_run_fn` 按形参类型区分：
        `T&` 形参收到 `PtrVal` 时会解回它指向的对象（别名不变），
        `T*` 形参按值收下地址本身。所以**这里无条件产出地址**是安全的。
        """
        if not n.children:
            raise InterpError("`&` 后面没有操作数")
        return self._ref_of(n.children[0], env, force)

    def _ref_of(self, node: Node, env, force) -> PtrVal:
        """左值节点 → 指向它那块存储的 `PtrVal`。

        只接受**真有存储**的四类左值（与 `_is_lvalue` 的左值集合一致）：
        变量（环境单元）、容器元素、结构体字段、`*p`。
        其它（字面量 / 调用结果 / 临时值）没有"那块存储"，明确报错——
        绝不悄悄造一个临时单元（那会给出一个改了也没人看见的地址）。
        """
        k = getattr(node, "kind", "")
        if k == "paren" and node.children:
            return self._ref_of(node.children[0], env, force)
        if k == "path":
            name = node.name or ""
            if name not in env:
                raise InterpError(f"`&{name}`：名字未绑定，没有可取的存储")
            return PtrVal("cell", env, name)
        if k == "field":
            base = self._eval(node.children[0], env, force) if node.children else None
            nm = node.name if node.name is not None else str(node.value or "")
            if isinstance(base, StructVal):
                if nm not in base.fields:
                    raise InterpError(f"{base.type_name} 没有字段 {nm}，取不到地址")
                return PtrVal("field", base, nm)
            if isinstance(base, list):
                return PtrVal("elem", base, _index(int(nm), base))
            raise InterpError(
                f"`&{nm}`：基址不是结构体 / 容器，没有可取的存储")
        if k == "index":
            base = self._eval(node.children[0], env, force) if node.children else None
            if not isinstance(base, (list, Text)):
                raise InterpError("`&xs[i]`：基址不是容器，取不到元素地址")
            idx = self._eval(node.children[1], env, force) \
                if len(node.children) > 1 else None
            return PtrVal("elem", base, _index(idx, base))
        if k == "deref":
            # `&*p` 就是 `p` 本身（C++ 同）：解引用再取地址不改变指向。
            return self._eval(node.children[0], env, force)
        raise InterpError(
            f"`&` 只能取**左值**的地址：变量 / 字段 / 容器元素 / `*p`；"
            f"拿到的是 {k}（临时值没有存储）。")


    def _binary(self, n: Node, env, force) -> Any:
        op = n.value or ""
        a = self._eval(n.children[0], env, force)
        # **短路**：`&&` / `||` 的右侧可能越界（`i < n && xs.at(i) > 0`），
        # 必须先看左侧再决定要不要算右侧。
        if op == "&&":
            return bool(a) and bool(self._eval(n.children[1], env, force))
        if op == "||":
            return bool(a) or bool(self._eval(n.children[1], env, force))
        b = self._eval(n.children[1], env, force)
        # **符号由值上的 `UInt` 标记决定**（声明类型在赋值/传参/返回处落上去）：
        #   · 任一边是 `u64`/`u8`… → `_arith` 走无符号回绕；
        #   · 两边都是 `i64`/字面量 → 仍是补码 64 位。
        # 比较同理：`UInt` 的富比较把另一边按无符号重解释。
        if op in ("+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>", "**"):
            return _arith(op, a, b)
        if op in ("<", "<=", ">", ">=", "==", "!="):
            return _cmp(op, a, b)
        raise InterpError(f"未实现的二元运算 {op}")

    # ---------------------------------------------------------------- FFI（第一批）
    def _ffi_prepare(self, name: str, node, args):
        """调用前的**形状校验**（与后端无关，见 shape.py）。"""
        from checker import param_list
        from shape import ShapeError, UnsupportedError, validate
        try:
            return validate(name, param_list(node), args)
        except (ShapeError, UnsupportedError) as exc:
            raise InterpError(str(exc))

    def _load_ffi(self, name: str):
        """把 `extern` 声明变成一个 ctypes 可调用对象（按需加载、缓存）。"""
        import ctypes
        decl = self.externs.get(name)
        if decl is None:
            return None
        if name in self._ffi_cache:
            return self._ffi_cache[name]
        lib_name = (decl.value or {}).get("lib") if isinstance(decl.value, dict) else None
        try:
            # **走 `ffi_c.load_library`**（2026-11-14）：裸名按 `--lib-dir=` 的顺序找，
            # 失败时消息里列出**试过的每个路径**——不再只有 ctypes 那一句
            # `Could not find module 'cjson'`（见 `ffi_c.library_candidates`）。
            lib = ffi_c.load_library(lib_name or "", getattr(self, "lib_dirs", ()))
        except OSError as exc:
            raise InterpError(f"加载外部库失败（{lib_name or '进程自身'}）：{exc}")
        fn = getattr(lib, name, None)
        if fn is None:
            raise InterpError(f"外部库 {lib_name or '进程自身'} 里没有符号 {name}")
        # 参数类型：u64/i64 → c_uint64/c_int64；Vec<u8> → c_char_p
        ps = next((c for c in decl.children if c.kind == "params"), None)
        argtypes = []
        for p in (ps.children if ps else []):
            ty = next((c for c in p.children if c.kind in ("type-ref", "type-name")), None)
            ts = ""
            if ty is not None:
                nm = getattr(ty, "name", None) or (ty.children[0].name if ty.children else "")
                if canon(nm) == "vector":
                    ts = "Vec"
                else:
                    ts = nm
            from ffi_c import argtype_for
            argtypes.append(argtype_for("vector" if canon(ts) == "vector" else ts))
        fn.argtypes = argtypes or None
        rt = next((c for c in decl.children if c.kind in ("type-ref", "type-name")), None)
        rts = ""
        if rt is not None:
            rts = getattr(rt, "name", None) or (rt.children[0].name if rt.children else "")
        from ffi_c import restype_for
        fn.restype = restype_for(rts)
        self._ffi_cache[name] = fn
        return fn

    def _call_ffi_spec(self, name: str, args) -> Any:
        """按 `.tu` 里那条 spec 的签名调用外部符号（`external "lib";`）。"""
        import ctypes
        spec = self.specs.get(name) if hasattr(self, "specs") else None
        if spec is None:
            raise InterpError(f"{name} 没有 spec，无法按签名调用")
        # **校验必须在加载库之前**：否则参数错会先撞上 `ctypes.CDLL` 的报错，
        # 而且测试还得依赖一个真实的外部库。
        args = self._ffi_prepare(name, spec, args)
        lib_name = self.extern_specs.get(name) or getattr(self, "link_lib", None)
        try:
            # 与 `_load_ffi` 同一条路：`external "库";` 的裸名也吃 `--lib-dir=`。
            lib = ffi_c.load_library(lib_name or "", getattr(self, "lib_dirs", ()))
        except OSError as exc:
            raise InterpError(f"加载外部库失败（{lib_name or '进程自身'}）：{exc}")
        fn = getattr(lib, name, None)
        if fn is None:
            raise InterpError(f"外部库 {lib_name or '进程自身'} 里没有符号 {name}")
        ps = next((c for c in spec.children if c.kind == "params"), None)
        from ffi_c import argtype_for, to_native, restype_for
        argtypes = []
        for p in (ps.children if ps else []):
            ty = next((c for c in p.children if c.kind in ("type-ref", "type-name")), None)
            argtypes.append(argtype_for(self._type_name(ty)))
        fn.argtypes = argtypes or None
        rt = next((c for c in spec.children if c.kind in ("type-ref", "type-name")), None)
        rts = self._type_name(rt)
        # **2026-11-13 修正（静默错值）**：这里原来把返回值硬编码成 `c_uint64`/`c_int64`，
        # 于是 `spec te_interp(…) -> f64 { external "tinyexpr.dll"; }` + `te_interp("1+2")`
        # 读到的是**整数寄存器的低 64 位**，返回 `1.0`（真值 `3.0`）——而且 check 通过、没有诊断。
        # 改成走 `restype_for`（与 `extern … from` 同一份类型表）。
        fn.restype = restype_for(rts) if rts else None
        res = fn(*to_native(args))
        # **FFI 返回值也按声明类型标注**：`c_uint64` 回来的是普通 int，
        # 不标的话 `ffi() - 1` 又退回有符号语义。
        return _convert_scalar(res, rts) if rts else res

    @staticmethod
    def _type_name(n) -> str:
        if n is None:
            return ""
        if getattr(n, "kind", "") == "type-ref":
            inner = next((c for c in n.children if c.kind in ("type-name", "type-ref")), None)
            return Interp._type_name(inner)
        if getattr(n, "kind", "") == "type-name":
            args = next((c for c in n.children if c.kind == "type-args"), None)
            base = n.name or ""
            if args is None:
                return base
            return base + "<" + ",".join(Interp._type_name(a) for a in args.children) + ">"
        return getattr(n, "name", "") or ""

    def _call_ffi(self, name: str, args) -> Any:
        decl = self.externs.get(name)
        if decl is not None:
            args = self._ffi_prepare(name, decl, args)
        fn = self._load_ffi(name)
        if fn is None:
            raise InterpError(f"未声明的外部函数 {name}")
        from ffi_c import to_native
        res = fn(*to_native(args))
        _rt = next((c for c in (decl.children or [])
                    if getattr(c, "kind", "") in ("type-ref", "type-name")), None)
        return _convert_scalar(res, _type_label(_rt)) if _rt is not None else res

    def _print_typed(self, n: Node, env, force) -> Any:
        """`print` 的值求值。

        **不再需要在出口把补码转回无符号**：`u64` 的值在值层就已经是
        规范无符号值（`UInt`），`str()` 打出来的就是用户期望的十进制。
        以前这里只认「实参是 `path` 且名字在全局 `vty` 里」，于是
        `print(x - 1)` 这种表达式一律漏掉（打在 `-1`）。
        """
        _an = next((c for c in n.children if c.kind == "args"), None)
        _vals = [self._eval(_a, env, force)
                 for _a in (_an.children if _an is not None else [])]
        # **计数必须和常规内建一致**：以前这里直接返回，`_bump()` 与
        # `builtin_calls` 都没记，ops 少 1（test_kappa_gaps 实测 6 → 5）。
        self.builtin_calls["print"] = self.builtin_calls.get("print", 0) + 1
        if _COUNT:
            _bump()
        return _u64(_builtin_print(_vals))

    def _call(self, n: Node, env, force) -> Any:
        # **`swap(xs, i, j)`**：原地交换两个元素（B3）。
        # 先有实现再登记 —— 上一次把顺序做反，结果 check 通过、run 崩。
        if (n.name or "").split(".")[-1] == "swap":
            _an = next((c for c in n.children if c.kind == "args"), None)
            _vs = [self._eval(a, env, force) for a in (_an.children if _an is not None else [])]
            if len(_vs) != 3:
                raise InterpError("swap 要 3 个实参（容器, i, j）")
            _seq, _i, _j = _vs[0], int(_vs[1]), int(_vs[2])
            if not isinstance(_seq, list):
                raise InterpError(f"swap 的第 1 个实参不是容器：{type(_seq).__name__}")
            if not (0 <= _i < len(_seq)) or not (0 <= _j < len(_seq)):
                raise InterpError(f"swap 下标越界：{_i}, {_j}（长度 {len(_seq)}）")
            _seq[_i], _seq[_j] = _seq[_j], _seq[_i]
            return _u64(0)
        if (n.name or "").split(".")[-1] == "print":
            return self._print_typed(n, env, force)

        # **调用函数值**：`g(1, 2)`，其中 `g` 是 `func(...)->R` 形参。
        # 必须放在最前面：否则会走"未定义的函数 g"。
        if n.children and n.children[0].kind == "path":
            _cv = env.get(n.children[0].name)
            if isinstance(_cv, FnVal):
                _vals, _lvs = self._args_lv(n, env, force)
                _fn = self.fns.get(_cv.name)
                if _fn is None:
                    raise InterpError(f"找不到函数 {_cv.name}")
                return self._run_fn(_fn, _vals, force, {}, arg_lvals=_lvs)

        # **FFI 分派**：声明的 `extern` 名字优先走 ctypes（不是语言函数）
        _short = (n.name or "").split(".")[-1]
        if _short in self.externs:
            _args_node = next((c for c in n.children if c.kind == "args"), None)
            _vals = [self._eval(a, env, force) for a in (_args_node.children if _args_node else [])]
            return self._call_ffi(_short, _vals)
        name = n.name or ""
        # `Token::Number(1)` → call name "Token.Number"：带载荷的枚举构造
        if "." in name:
            ty, va = name.rsplit(".", 1)
            args = self._args(n, env, force)
            payload = None
            if len(args) == 1:
                payload = args[0]
            elif len(args) > 1:
                payload = tuple(args)
            return EnumVal(ty, va, payload)
        # **用户定义优先于同名内建**（批次 5 第 23 条）。
        # 以前 `stdlib.is_stdlib(name)` 在 `self.fns` 之前分派，用户写的
        # `func sum_of(...)` 永远跑不到（还捎一条弃用警告）。用户定义更具体。
        # `swap` / `print` 是保留的内建分派点，仍走上面两条特判。
        if name in self.fns:
            args, _lvs = self._args_lv(n, env, force)
            return self._run_fn(self.fns[name], args, force, {}, arg_lvals=_lvs)
        if name == "":
            # `FixedVec<T, N>::make()` —— 静态构造
            callee_node = n.children[0]
            args = self._args(n, env, force)
            # **`array<T, N>(v)`**（P4-0，2026-11-11）：C++ 的值初始化写法，
            # 与旧的 `array<T, N>::filled(v)` 完全同义（N 个 v）；不带实参
            # 就是 N 个零（`array<T, N>()`）。`filled` 保留为弃用别名。
            if callee_node.kind == "type-name":
                tn = callee_node
                head = canon(tn.name or "")
                cap = _fixed_capacity(tn, self.globals)
                # **`array` 与 `fixedvec` 是同一个类型**（2026-11-13）：
                # `typenames.CANON` 把 `FixedVec` 折成 `array`，而小写
                # `fixedvec` 是 `typecheck` 一直认的写法（`METHOD_TABLE` /
                # `_elem_ty_of` / `_zero_of_type` 都按 `("array", "fixedvec")`
                # 成对判）。只有这里的构造分支漏了小写：`fixedvec<T,N>(v)`
                # check 绿、run 报「只支持 array<T, N>(v)」。同一类型必须
                # 走同一条构造路径。
                if head in ("array", "fixedvec") and cap is not None:
                    if args:
                        fill = _convert_to(args[0], _elem_type_node(tn))
                        return FixedList(cap, [copy.deepcopy(fill) for _ in range(cap)])
                    return FixedList(cap)
                raise InterpError(
                    f"`{tn.name}<…>(…)` 这种构造只支持 `array<T, N>(v)`"
                    f"（= `fixedvec<T, N>(v)`，两者是同一个类型）；"
                    f"其它容器请用 `Type<…>::make()`，"
                    f"或声明变量后用 `.push_back(…)` 逐个填。")
            if callee_node.kind == "field" and callee_node.children \
                    and callee_node.children[0].kind == "type-name" \
                    and callee_node.name in ("make", "filled", "new"):
                tn = callee_node.children[0]
                cap = _fixed_capacity(tn, self.globals)

                # `FixedVec<T, N>::make()`   → **空**（栈语义：靠 push 长到 N）
                # `FixedVec<T, N>::filled(v)` → **N 个 v**（表语义：一建好就能下标写）
                #
                # 两个构造子对应两种意图。只给 make 的话，
                # 「建一张表」就得先写 push 循环——而 C 的 `u64 dp[N]` 是直接建好的。
                if canon(tn.name) == "map":
                    return MapVal(key_ty=_key_type_node(tn), val_ty=_elem_type_node(tn))
                if canon(tn.name) == "stack":
                    return StackVal()
                if canon(tn.name) == "unordered_map":
                    return UnorderedMapVal()
                if canon(tn.name) == "unordered_set":
                    return UnorderedSetVal()
                if canon(tn.name) == "queue":
                    return QueueVal()
                if canon(tn.name) == "set":
                    return SetVal(elem_ty=_elem_type_node(tn))
                if canon(tn.name) in ("array", "fixedvec") and cap is not None:
                    if callee_node.name == "filled":
                        fill = _convert_to(args[0] if args else 0, _elem_type_node(tn))
                        return FixedList(cap, [copy.deepcopy(fill) for _ in range(cap)])
                    return FixedList(cap)
                if canon(tn.name) == "vector":
                    _u8 = _type_label(_elem_type_node(tn)) == "u8"
                    if callee_node.name == "filled":
                        fill = _convert_to(args[0] if args else 0, _elem_type_node(tn))
                        n = args[1] if len(args) > 1 else 0
                        # **非空 `vector<T>::filled(v, n)` 是一次真分配**
                        # （真实 C++ 里 `std::vector<T> xs(n, v)` 会向堆要一块内存）。
                        # 以前直接 `[fill] * n` 不计数，于是 `allocation: { max: 0 }`
                        # 会**假阳性**：契约说"不分配"，实际分配了一次。
                        # 少算比多算危险，所以这里记 1（2026-11-08）。
                        if int(n) > 0:
                            self.allocs += 1
                        _built = [copy.deepcopy(fill) for _ in range(int(n))]
                        return Text(_built) if _u8 else _built
                    return Text() if _u8 else []
                # **未知容器不许被静默建成空壳**（2026-11-06）：
                # 以前任何类型名都落到 `return []`，于是
                # `deque<u64> q = deque<u64>::new(); q.length()` **返回 0**——
                # 检查说没问题、运行给假答案，比报错危险得多。
                raise InterpError(
                    f"未知容器类型 `{tn.name}`：`{tn.name}<…>::{callee_node.name}()` 没有实现。"
                    f"已实现：vector / array / map / set / unordered_map / unordered_set / "
                    f"stack / queue。若要两端增删，用 vector + 头尾索引。")
            # `Token.Number(1)` 这类：callee 不是 path，而是 field（枚举变体）
            callee = self._eval(callee_node, env, force)
            args = self._args(n, env, force)
            if isinstance(callee, EnumVal):
                payload = None
                if len(args) == 1:
                    payload = args[0]
                elif len(args) > 1:
                    payload = tuple(args)
                return EnumVal(callee.type_name, callee.variant, payload)
            raise InterpError(f"不可调用的表达式：{n.children[0].kind}")
        # **`for_each(m, f)`**：遍历容器，对每个元素/键值对调具名函数 `f`。
        # **快照**遍历：回调里改容器不影响本次（slice 1 的语义，见计划 §2.4）。
        #
        # **认哪些容器**（2026-11-10，`_tier_b/structs` K2 的另一半）：
        #   `Vec` / `array`（一元回调）· 语言 `set` / `unordered_set`（一元）
        #   · 语言 `map`（键序）· `unordered_map`（顺序未指定）
        #   · stdlib 的 `HashMap`（`map_new()`，顺序不承诺）
        # 后四种给**二元回调** `f(k, v)`；前两种给一元 `f(x)`。
        # 以前只认 `Vec` 和 stdlib `HashMap`，于是文档里写的
        # `for_each(map<…>, f)` **运行期崩**——那是"文档说能做、实际不行"。
        if name == "for_each":
            _an = next((c for c in n.children if c.kind == "args"), None)
            _items = _an.children if _an else []
            if len(_items) < 2:
                raise InterpError("for_each 需要 2 个实参（容器、具名函数）")
            _m = self._eval(_items[0], env, force)
            _fnode = _items[1]
            _fname = _fnode.name if getattr(_fnode, "kind", "") == "path" else None
            _fn = self.fns.get(_fname or "")
            if _fn is None:
                raise InterpError("for_each 的第二个实参必须是具名函数")
            # **Vec / FixedVec：一元回调**（v0.73）。以前只认 HashMap，
            # 于是文档里的 `for_each(xs, f)` 在 Vec 上直接报错。
            if isinstance(_m, list):
                _snap = list(_m)
                for _x in _snap:
                    self._run_fn(_fn, [_x], force, {})
                if _COUNT:
                    _bump()
                return _u64(len(_snap))
            # ---- 二元回调的容器：先取快照（元素是 `(k, v)` 或单元素）
            _pairs = None
            if isinstance(_m, stdlib.MapVal):              # stdlib HashMap
                _pairs = [(k, v) for (k, v) in _m.items()]
            elif isinstance(_m, MapVal):                   # 语言 map（键序）
                _pairs = _m.items()
            elif isinstance(_m, UnorderedMapVal):          # 语言 unordered_map
                _pairs = [(k, v) for (k, v) in _m.data.items()]
            if _pairs is not None:
                for _k, _v in _pairs:
                    self._run_fn(_fn, [_k, _v], force, {})
                if _COUNT:
                    _bump()
                return _u64(len(_pairs))
            # ---- 一元回调的集合：语言 set（元素序）/ unordered_set（顺序未指定）
            if isinstance(_m, SetVal):
                _els = _m.values()
            elif isinstance(_m, UnorderedSetVal):
                _els = list(_m.data)
            else:
                _els = None
            if _els is not None:
                for _x in _els:
                    self._run_fn(_fn, [_x], force, {})
                if _COUNT:
                    _bump()
                return _u64(len(_els))
            raise InterpError(
                "for_each 的第一个实参必须是容器："
                "`Vec` / `array`（一元回调 `f(x)`）、语言 `map` / `set` / "
                "`unordered_map` / `unordered_set`、或 stdlib 的 `HashMap`"
                "（后四种给二元 `f(k, v)`，`set` 类给一元 `f(x)`）")

        # **`format` 是变参**：不能被 stdlib 的固定 arity 拦住
        if name == "format":
            args = self._args(n, env, force)
            self.builtin_calls[name] = self.builtin_calls.get(name, 0) + 1
            if _COUNT:
                _bump()
            # **与 `print` 共用同一套渲染**：数字容器先渲染成文本再进 fmt，
            # 返回的是 `vector<u8>` → `Text`（`ret="vec-u8"` 的同一个规则）。
            return Text(stdlib.FNS["format"]["impl"](_format_args(args)))

        # `sort(xs)` / `sort_by(xs, 具名比较器)` —— **必须在求值实参之前拦**：
        # 第二个实参是比较器的**名字**，不是值（本语言没有一等函数，
        # 直接 _eval 会报「未绑定的名字」）。
        # **spec 声明了外部实现**：按 spec 的参数类型走 ctypes
        _nm = name or ""
        _is_ext = _nm in getattr(self, "extern_specs", {}) or (
            _nm in getattr(self, "_spec_names", set()) and _nm not in self.fns)
        if _is_ext:
            _args_node = next((c for c in n.children if c.kind == "args"), None)
            _vals = [self._eval(a, env, force) for a in (_args_node.children if _args_node else [])]
            return self._call_ffi_spec(name or "", _vals)

        # **堆原语**（对齐 `std::push_heap` / `std::pop_heap`）：大顶堆固定。
        # 不给自定义比较器——比较器是任意代码，成本不可静态界定。
        # `xs` 就是普通向量，**没有新内存语义**。
        if name in ("push_heap", "pop_heap"):
            _an = next((c for c in n.children if c.kind == "args"), None)
            _av = [self._eval(a, env, force) for a in (_an.children if _an is not None else [])]
            _seq = _av[0] if _av else None
            if not isinstance(_seq, list):
                raise InterpError(f"{name} 的第 1 个实参不是容器")
            if name == "push_heap":
                if len(_av) != 2:
                    raise InterpError("push_heap 要 2 个实参（容器, 值）")
                # **入容器：左值实参要拷贝**（`push_heap` 也是追加一个元素）。
                _lvs = self._arg_lvs(n)
                if _lvs is not None and len(_lvs) > 1 and _lvs[1]:
                    _av[1] = self._copy_value(_av[1])
                _seq.append(_av[1])
                _i = len(_seq) - 1
                while _i > 0:
                    _par = (_i - 1) // 2
                    if _seq[_par] >= _seq[_i]:
                        break
                    _seq[_par], _seq[_i] = _seq[_i], _seq[_par]
                    _i = _par
                return _u64(0)
            if not _seq:
                raise InterpError("pop_heap：空堆")
            _n = len(_seq)
            _seq[0], _seq[_n - 1] = _seq[_n - 1], _seq[0]
            _i = 0
            while True:
                _l = 2 * _i + 1
                if _l >= _n - 1:
                    break
                _c = _l
                if _l + 1 < _n - 1 and _seq[_l + 1] > _seq[_l]:
                    _c = _l + 1
                if _seq[_i] >= _seq[_c]:
                    break
                _seq[_i], _seq[_c] = _seq[_c], _seq[_i]
                _i = _c
            return _u64(1)

        if name in ("sort", "sort_by"):
            if _COUNT:
                _bump()
            return _u64(self._builtin_sort(name, n, env, force))

        # **高阶内建**：第二个（fold 是第三个）实参是**具名函数**，不是值。
        # 和 `sort_by` 同一套理由：**具名函数是有限集合，成本可静态分析**；
        # 匿名函数是任意代码，`κ` 无法界定，契约会被打穿。
        if name in ("map", "filter", "fold", "count_if"):
            if _COUNT:
                _bump()
            _r = self._builtin_higher(name, n, env, force)
            # `count_if` 的返回类型是 u64；`fold` 的累加器类型由调用方决定，
            # 不能一律标成无符号。
            return _u64(_r) if name == "count_if" else _r

        args = self._args(n, env, force)

        # **值语义入口 ⑤（内建）：`vec_push(xs, v)` / `map_put(m, k, v)` /
        # `hmap_put(t, k, v)` 等「入容器」内建的**左值实参**要拷贝**
        # （与 `.push` / `.insert` 同一规则）。名单之外的内建不持有实参。
        _sidxs = _STORE_BUILTIN_ARGVALS.get(name)
        if _sidxs:
            _lvs = self._arg_lvs(n)
            if _lvs is not None:
                for _i in _sidxs:
                    if _i < len(_lvs) and _lvs[_i] and _i < len(args):
                        args[_i] = self._copy_value(args[_i])

        if name in BUILTINS_INT2:
            if _COUNT:
                _bump()
            return BUILTINS_INT2[name](args)

        if name in BUILTINS_INT1:
            self.builtin_calls[name] = self.builtin_calls.get(name, 0) + 1
            if _COUNT:
                _bump()
            return BUILTINS_INT1[name](args)

        # **最小基本库（L1）**：容器/算法内建，见 stdlib.py 的收编标准。
        if stdlib.is_stdlib(name):
            self.builtin_calls[name] = self.builtin_calls.get(name, 0) + 1
            if _COUNT:
                _bump()
            return self._tag_stdlib_result(name, stdlib.call(name, args))

        if name == "Err":
            return Err(args[0] if args else None)
        if name == "Ok":
            return Ok(args[0] if args else None)

        fn = self.fns.get(name)
        if fn is None:
            raise InterpError(f"调用了未定义的函数 {name}")
        return self._run_fn(fn, args, force, {})

    def _tag_stdlib_result(self, name: str, res):
        """把内建**声明的返回类型**落到值上（`u64` 标记 / `vector<u8>` → `Text`）。

        没有这一步，`xs.length() - 1` 这种"内建返回 u64 → 再减"的表达式
        会退回有符号（`0 - 1 = -1`），与声明类型不符。

        `ret` 里的 `vec-u8` 一族（`text_concat` / `read_file` / `format` /
        `text_split_vec` …）是"内建产生 `vector<u8>`"的**唯一登记处**：
        在这里统一包成 `Text`，`print` 才不会因为"值是从内建来的"而打数字。
        """
        try:
            rk = stdlib.ret_kind(name)
        except Exception:                                   # noqa: BLE001
            return res
        if rk == "u64":
            return _u64(res)
        if rk == "result-u64" and isinstance(res, Ok):
            return Ok(_u64(res.v))
        if rk == "vec-u64" and isinstance(res, list) and not isinstance(res, Text):
            return [_u64(x) for x in res]
        if rk == "vec-u8" and isinstance(res, list) and not isinstance(res, Text):
            return Text(res)
        if rk == "result-vec-u8" and isinstance(res, Ok) \
                and isinstance(res.v, list) and not isinstance(res.v, Text):
            return Ok(Text(res.v))
        if rk == "option-vec-u8" and isinstance(res, EnumVal) \
                and isinstance(res.payload, list) and not isinstance(res.payload, Text):
            res.payload = Text(res.payload)
            return res
        if rk == "vec-vec-u8" and isinstance(res, list):
            return [Text(x) if isinstance(x, list) and not isinstance(x, Text) else x
                    for x in res]
        return res

    def _builtin_higher(self, name: str, n: Node, env, force) -> Any:
        """`map(xs, f)` / `filter(xs, p)` / `count_if(xs, p)` / `fold(xs, init, f)`。

        **第二个（fold 是第三个）实参 MUST 是具名函数**——理由同 `sort_by`：
        **没有一等函数，就没有"任意代码"这种成本黑洞**。
        """
        args_node = next((c for c in n.children if c.kind == "args"), None)
        items = args_node.children if args_node is not None else []
        if len(items) < 2:
            raise InterpError(f"{name} 至少 2 个实参：容器 + 具名函数")

        recv = self._eval(items[0], env, force)
        if not isinstance(recv, list):
            raise InterpError(f"{name} 的第一个实参必须是容器，给的是 {type(recv).__name__}")

        cb_node = items[2] if (name == "fold" and len(items) >= 3) else items[1]
        if cb_node.kind != "path":
            raise InterpError(
                f"{name} 的函数实参 MUST 是具名函数（给的是 {cb_node.kind}）——"
                f"匿名函数是任意代码，成本不可静态界定")
        cb_name = cb_node.name or ""
        cb = self.fns.get(cb_name)
        if cb is None:
            raise InterpError(f"{name} 的函数实参 {cb_name} 不是本模块的函数")

        if name == "fold":
            if len(items) < 3:
                raise InterpError("fold 要 3 个实参：容器 + 初值 + 具名函数")
            acc = self._eval(items[1], env, force)
            for x in recv:
                acc = self._run_fn(cb, [acc, x], force, {})
            return acc

        if name == "count_if":
            n_true = 0
            for x in recv:
                if self._run_fn(cb, [x], force, {}) not in (False, 0, None):
                    n_true += 1
            return n_true

        if name == "filter":
            return [x for x in recv if self._run_fn(cb, [x], force, {}) not in (False, 0, None)]

        # map
        return [self._run_fn(cb, [x], force, {}) for x in recv]

    def _builtin_sort(self, name: str, n: Node, env, force) -> Any:
        """`sort(xs)` 原地稳定升序；`sort_by(xs, cmp)` 用**具名**比较器。

        ## 为什么比较器必须是具名函数

        C 的 `qsort` 拿函数指针，比较器是**任意代码**——那样排序的成本
        `O(n log n × κ(cmp))` 里的 `κ(cmp)` 就无法静态界定，成本契约会被打穿。

        **具名函数是有限集合，成本可静态分析。** 这不是限制，是把契约守住。

        ## 为什么强制稳定

        我们已经吃过一次亏：v0.69 的程序 5 因为「递减循环推不出上界」
        被迫把插入排序改成选择排序，**丢了稳定性**。
        语言自己给排序，就该给稳定的那一种。
        """
        if n.children:
            args_node = next((c for c in n.children if c.kind == "args"), None)
        else:
            args_node = None
        items = args_node.children if args_node is not None else []
        if not items:
            raise InterpError(f"{name} 至少要 1 个实参（容器）")

        recv = self._eval(items[0], env, force)
        if not isinstance(recv, list):
            raise InterpError(f"{name} 的第一个实参必须是容器，给的是 {type(recv).__name__}")

        if name == "sort":
            # Python 的 list.sort 是稳定排序
            try:
                recv.sort()
            except TypeError as e:
                raise InterpError(f"sort 无法比较容器里的元素：{e}") from None
            return len(recv)

        # ---- sort_by
        if len(items) < 2:
            raise InterpError("sort_by 要 2 个实参：容器 + 具名比较器")
        cmp_node = items[1]
        if cmp_node.kind != "path":
            raise InterpError(
                "sort_by 的比较器 MUST 是具名函数 "
                f"（给的是 {cmp_node.kind}）——匿名比较器是任意代码，成本不可静态界定")
        cmp_name = cmp_node.name or ""
        cmp_fn = self.fns.get(cmp_name)
        if cmp_fn is None:
            raise InterpError(f"sort_by 的比较器 {cmp_name} 不是本模块的函数")

        def _cmp(a, b):
            # 比较器返回「a 应排在 b 前」
            if self._run_fn(cmp_fn, [a, b], force, {}) in (True, 1):
                return -1
            if self._run_fn(cmp_fn, [b, a], force, {}) in (True, 1):
                return 1
            return 0

        import functools
        try:
            recv.sort(key=functools.cmp_to_key(_cmp))
        except TypeError as e:
            raise InterpError(f"sort_by 无法比较：{e}") from None
        return len(recv)

    def _arg_value(self, a: Node, env, force) -> Any:
        # **函数值**：`for_each(m, visit)` —— `visit` 是**名字**，不是值。
        # 只有"没被局部变量遮蔽"时才当函数值，避免踩掉普通变量。
        if a.kind == "path" and (a.name or "") not in env \
                and (a.name or "") in self.fns:
            return FnVal(a.name)
        return self._eval(a, env, force)

    def _args(self, n: Node, env, force) -> List[Any]:
        """求实参；**具名实参按名字绑定**（v0.72）。

        以前 `named-arg` 只取里面的表达式、名字被丢掉——于是 `g(b = 1, a = 2)`
        按位置绑定成 `a=1, b=2`，得到 12 而不是 21。**静默算错**是最坏的一类。
        """
        args_node = next((c for c in n.children if c.kind == "args"), None)
        items = list(args_node.children) if args_node is not None else []
        named = [a for a in items if a.kind == "named-arg"]
        if not named:
            return [self._arg_value(a, env, force) for a in items]

        callee = (n.name or "").split(".")[-1]
        fn = self.fns.get(callee)
        pnames = []
        if fn is not None:
            ps = next((c for c in fn.children if c.kind == "params"), None)
            pnames = [p.name for p in (ps.children if ps else [])]
        vals = {}
        for a in items:
            if a.kind != "named-arg":
                raise InterpError(f"{callee}：具名实参与位置实参不能混用")
            if a.name in vals:
                raise InterpError(f"{callee}：实参 {a.name} 重复给了")
            if pnames and a.name not in pnames:
                raise InterpError(f"{callee} 没有名为 {a.name} 的参数"
                                  f"（有：{', '.join(pnames)}）")
            kid = a.children[0] if a.children else None
            if kid is None:
                raise InterpError(f"{callee}：实参 {a.name} 没有值")
            vals[a.name] = self._arg_value(kid, env, force)
        if pnames:
            missing = [p for p in pnames if p not in vals]
            if missing:
                raise InterpError(f"{callee} 缺少实参：{', '.join(missing)}")
            return [vals[p] for p in pnames]
        return list(vals.values())

    def _args_lv(self, n: Node, env, force):
        """求实参，**同时给出每个实参的值类别**（左值 = 要拷贝）。

        返回值 `(vals, lvals)`；`lvals is None` 表示"判不出/不判"（具名实参
        按位置重排的那条路径），调用方按"不拷贝"处理——宁可少拷一次，
        也不要把 prvalue 误当左值拷一遍。
        """
        args_node = next((c for c in n.children if c.kind == "args"), None)
        items = list(args_node.children) if args_node is not None else []
        vals = self._args(n, env, force)
        if any(a.kind == "named-arg" for a in items):
            return vals, None
        return vals, [_is_lvalue(a) for a in items]

    def _arg_lvs(self, n: Node):
        """`_method` 用：每个**实参节点**是不是左值（具名实参取里面的表达式）。"""
        an = next((c for c in n.children if c.kind == "args"), None)
        out = []
        for a in (an.children if an is not None else []):
            if a.kind == "named-arg":
                a = a.children[0] if a.children else None
            out.append(_is_lvalue(a))
        return out

    def _copy_arg(self, lvs, i: int, v):
        """**值语义入口 ⑤：入容器（`push` / `insert`）时，实参是左值才拷贝**。

        `xs.push(mk())` / `xs.push({1,2})` 这类 prvalue 不拷贝——那个临时值
        没有别的持有者，C++ 里是移动。`xs.push(y)` 才拷贝（`y` 还要用）。
        记账（`allocs` / ops）走 `_copy_value` 的同一份口径。
        """
        if lvs is not None and i < len(lvs) and lvs[i]:
            return self._copy_value(v)
        return v

    def _method(self, n: Node, env, force) -> Any:
        # **`const T&` 形参不许被变更方法写**（v0.72）。
        # 以前 `z(const Vec<u64>& xs) { xs.push(0); }` 会改掉调用方的 list。
        _recv = n.children[0] if n.children else None
        _mname = (n.name or "").split(".")[-1]
        _consts = self._const_stack[-1] if getattr(self, "_const_stack", None) else set()
        if _recv is not None and _recv.kind == "path" and _recv.name in _consts \
                and _mname in MUTATING_METHODS:
            raise InterpError(
                f"E-Borrow-Const：{_recv.name} 是 `const` 形参，不能调 `. {_mname}(…)`"
                f"（要改就把它声明成可变借用 `T&`）")
        name = n.name or ""
        recv_node = n.children[0]
        recv = self._eval(recv_node, env, force)
        # **有序映射的方法**（Map<K,V>）：键序确定，成本 O(log n)。
        # **有序集合的方法**（Set<T>）：元素序确定。
        # **stack / queue 的方法**：全部 O(1)（queue 的 pop 用读游标摊销）。
        # **unordered_map / unordered_set**：语义同 map/set，但**顺序未指定**。
        if isinstance(recv, (UnorderedMapVal, UnorderedSetVal)):
            _m = (n.name or "").split(".")[-1]
            _an = next((c for c in n.children if c.kind == "args"), None)
            _av = [self._eval(a, env, force) for a in (_an.children if _an is not None else [])]
            _lvs = self._arg_lvs(n)
            _is_map = isinstance(recv, UnorderedMapVal)
            if _m == "insert" and _is_map:
                # **每个插入最多分配一个节点**（与成本域的上界同源）
                self.allocs += 1
                recv.data[self._copy_arg(_lvs, 0, _av[0])] = \
                    self._copy_arg(_lvs, 1, _av[1] if len(_av) > 1 else 0)
                return _u64(0)
            if _m == "insert":
                self.allocs += 1
                recv.data.add(self._copy_arg(_lvs, 0, _av[0]))
                return _u64(0)
            if _m == "get":
                if _av[0] not in recv.data:
                    raise InterpError("unordered_map.get：键不存在（先用 contains 判断）")
                return recv.data[_av[0]]
            if _m == "contains":
                # **谓词，返回 bool**（2026-11-13）：以前返回 `_u64(1/0)`，
                # `if (h.contains(k))` 在 `typecheck` 那边按 u64 判就编不过。
                # 与 `empty()` 一致；发射器的 `bool contains(const K&) const`
                # 同义。**insert/put/remove/erase 仍返回 u64 计数**。
                return _av[0] in recv.data
            if _m == "remove" or _m == "erase":
                if _av[0] in recv.data:
                    recv.data.discard(_av[0]) if not _is_map else recv.data.pop(_av[0], None)
                    return _u64(1)
                return _u64(0)
            if _m in ("size", "length"):
                return _u64(len(recv.data))
            if _m in ("empty", "is_empty"):
                return len(recv.data) == 0
            if _m == "clear":
                recv.data.clear()
                return None
            if _m == "keys":
                # **顺序未指定**：这里返回插入序，但契约不保证——文档已写明。
                return list(recv.data.keys()) if _is_map else list(recv.data)
            if _m == "values":
                # **值不是键**（2026-11-13 修）：以前 `values` 与 `keys` 共用
                # 一条分支，于是 `m.values()` 返回的是**键**——静默错值
                # （`a→11, b→2` 拿到 `a/b`）。顺序与 `keys()` 同源
                # （Python dict 的键序/值序一致），`unordered_set` 仍是元素。
                return (list(recv.data.values()) if _is_map
                        else list(recv.data))
            raise InterpError(f"unordered 容器没有方法 {_m}")
        if isinstance(recv, (StackVal, QueueVal)):
            _m = (n.name or "").split(".")[-1]
            _an = next((c for c in n.children if c.kind == "args"), None)
            _av = [self._eval(a, env, force) for a in (_an.children if _an is not None else [])]
            _lvs = self._arg_lvs(n)
            _is_stack = isinstance(recv, StackVal)
            # **`push_back` 不认**（2026-11-13，check/run 口径统一）：`typecheck`
            # 的 `METHOD_TABLE["stack" / "queue"]` 只有 `push` / `enqueue`，
            # `s.push_back(1)` 在 check 期报 `E-Call-UnknownMethod`。以前解释器
            # 这条分支**偷偷也认** `push_back`——于是 `run` 只在绕过 check 的
            # 路径（直接 `Interp` / `stats`）上给绿灯，两边口径不一。
            # 统一到**静态更严**的那边：容器适配器只有 `push` / `enqueue`。
            if _m in ("push", "enqueue"):
                recv.append(self._copy_arg(_lvs, 0, _av[0]))
                return _u64(0)
            if _m == "push_back":
                raise InterpError(
                    f"{'stack' if _is_stack else 'queue'} 没有方法 push_back："
                    f"C++ 的容器适配器只有 `push` / `enqueue`（check 期同样拒绝）。"
                    f"要追加请写 `.{'push' if _is_stack else 'enqueue'}(…)`")
            if _m in ("pop", "dequeue"):
                if not recv:
                    raise InterpError(f"{'stack' if _is_stack else 'queue'} 是空的，不能 pop")
                return recv.pop() if _is_stack else recv.pop(0)
            if _m in ("top", "peek", "front"):
                if not recv:
                    raise InterpError(f"{'stack' if _is_stack else 'queue'} 是空的，没有堆顶/队首")
                return recv[-1] if _is_stack else recv[0]
            if _m in ("size", "length"):
                return _u64(len(recv))
            if _m in ("empty", "is_empty"):
                return len(recv) == 0
            if _m == "clear":
                recv.clear()
                return None
            raise InterpError(f"{'stack' if _is_stack else 'queue'} 没有方法 {_m}")
        if isinstance(recv, SetVal):
            _s = (n.name or "").split(".")[-1]
            _san = next((c for c in n.children if c.kind == "args"), None)
            _sav = [self._eval(a, env, force) for a in (_san.children if _san is not None else [])]
            _lvs = self._arg_lvs(n)
            if _s == "insert":
                self.allocs += 1
                recv.add(self._copy_arg(_lvs, 0, _sav[0]))
                return _u64(0)
            if _s == "contains":
                # 谓词 → bool（2026-11-13，同上：`if (s.contains(x))` 要能过 check）
                return _sav[0] in recv
            if _s in ("remove", "erase"):
                return _u64(recv.discard(_sav[0]))
            if _s in ("size", "length"):
                return _u64(len(recv))
            if _s in ("empty", "is_empty"):
                return len(recv) == 0
            if _s == "clear":
                recv.clear()
                return None
            if _s == "values":
                return recv.values()
            raise InterpError(f"set 没有方法 {_s}")
        if isinstance(recv, MapVal):
            _m = (n.name or "").split(".")[-1]
            _an = next((c for c in n.children if c.kind == "args"), None)
            _av = [self._eval(a, env, force) for a in (_an.children if _an is not None else [])]
            _lvs = self._arg_lvs(n)
            if _m == "insert":
                # **`insert` 不覆盖已有键**（2026-11-11，审计 A2）：
                # `std::map::insert({k,v})` 键已存在时**什么都不改**，返回
                # "没有插入"。以前我们覆盖——同为 `insert`、静默错值。
                # 返回 bool：插进去了 = true。覆盖写 `m[k] = v` / `m.put(k, v)`。
                # 键已存在时**不拷贝**键值（C++ 也不构造那个节点）。
                if recv.has(_av[0]):
                    return _u64(0)
                self.allocs += 1
                # **键和值都按左值拷贝**（`map<vector<u8>, vector<u64>>` 的键
                # 也是容器；C++ 的 `m.insert({k, v})` 两者都拷）
                recv.put(self._copy_arg(_lvs, 0, _av[0]),
                         self._copy_arg(_lvs, 1, _av[1] if len(_av) > 1 else 0))
                return _u64(1)
            if _m == "put":
                # **`put` 才是覆盖写**（本语言的扩展名；C++ 写 `m[k] = v`）。
                # 返回 bool：键原本不存在（= 新插入）→ true，覆盖 → false。
                _new = not recv.has(_av[0])
                if _new:
                    self.allocs += 1
                recv.put(self._copy_arg(_lvs, 0, _av[0]),
                         self._copy_arg(_lvs, 1, _av[1] if len(_av) > 1 else 0))
                return _u64(1 if _new else 0)
            if _m == "get":
                if not recv.has(_av[0]):
                    raise InterpError(f"map.get：键 {_av[0]!r} 不存在（先用 contains 判断）")
                return recv.get(_av[0])
            if _m == "contains":
                # 谓词 → bool（2026-11-13，同上：`if (m.contains(k))`）
                return recv.has(_av[0])
            if _m in ("remove", "erase"):
                return _u64(recv.remove(_av[0]))
            if _m in ("size", "length"):
                return _u64(len(recv))
            if _m in ("empty", "is_empty"):
                return len(recv) == 0
            if _m == "clear":
                recv.clear()
                return None
            if _m == "keys":
                return recv.keys()
            raise InterpError(f"map 没有方法 {_m}")
        args = []
        for c in n.children[1:]:
            if c.kind == "args":
                for a in c.children:
                    if a.kind == "named-arg":
                        a = a.children[0] if a.children else None
                        if a is None:
                            continue
                    args.append(self._eval(a, env, force))

        self.method_calls += 1
        if _COUNT:
            _bump()

        _elem_tn = getattr(n, "_elem_ty_cache", None)
        if _elem_tn is None:
            _elem_tn = _NONE_TY
            _recv_kind = getattr(recv_node, "kind", "")
            if _recv_kind == "path":
                _elem_tn = _elem_type_node(self._type_of_var(recv_node.name or "")) or _NONE_TY
            elif _recv_kind == "field":
                # **字段接收者也推导元素类型**（K1，2026-11-10）：以前这里只认
                # 普通变量，`s.v.push(3)` 的元素类型是 `None` → 裸 `int` 进字段；
                # 下一次读字段时 `_convert_to` 就要逐元素重标，旧实现因此**重建
                # 列表**，写入落在副本上。字段的声明类型是静态的，和变量一样可查
                # （`_target_type` 对 `s.v` 走 `structs[S]` 的字段表）。
                _elem_tn = _elem_type_node(self._target_type(recv_node)) or _NONE_TY
            n._elem_ty_cache = _elem_tn
        if _elem_tn is _NONE_TY:
            _elem_tn = None

        # **接收者必须是容器**（方法分派边界）。以前 `slice(...).length()`
        # 落到 Python 的 `len(U64)` → `TypeError: object of type 'U64' has no
        # len()`：宿主异常、没有行号、不在 Elench 诊断体系里。`slice` 等内建
        # 返回的是 u64 长度（不是容器），这里统一给 Elench 错误。
        if not hasattr(recv, "__len__"):
            raise InterpError(
                f"E-Type-NotContainer：`.{name}()` 只能用于容器，"
                f"但接收者是 {_runtime_name(recv)}（值 {recv!r}）。"
                f"注意有些内建（如 `slice`）返回的是 u64 长度，不是容器——"
                f"要读长度请先把返回值存进变量")

        # ---- C++ 名字（2026-11-11 起是唯一写法；旧名见下，仍在但报弃用）
        if name in ("size", "length"):
            # `length` 是**弃用别名**（check 期报 W-Builtin-Deprecated）。
            return _u64(len(recv))
        if name in ("empty", "is_empty"):
            # `is_empty` 是**弃用别名**。返回 bool（C++ 的 `empty()` 也是 bool）。
            return len(recv) == 0
        if name == "c_str":
            # **C++ 的 `s.c_str()`**（2026-11-12，兼容层拼写，只在 `--compat`
            # 下认）。C++ 返回 `const char*`；本语言没有指针型字符串，
            # `vector<u8>` / `Text` 本身就是要递给 `print` 的那段文本，
            # 所以它**就是同一个值**（别名，不拷贝）。`compat=False` 时照旧
            # 报 E-Call-UnknownMethod（check）——默认路径一个字节都不动。
            if _compat.enabled() and isinstance(recv, list):
                return recv
        if name == "at":
            return _convert_read(recv[_index(args[0], recv)], _elem_tn)
        if name == "assign":
            # `v.assign(n, x)`（2026-11-12）：C++ 的"清空后填 n 个 x"。
            # 结果与 `vector<T>::filled(x, n)` 相同，但这里是**原地**改，所以
            # 计数上按 n 次追加记（与 filled 同一口径，κ 不会低估）。
            #
            # **返回 `None`（void）**（2026-11-13）：C++ 的 `std::vector::assign`
            # 返回 void，`assign` 的用途也只是"改这个容器"，不是产出新值。
            # 以前这里 `return recv`（方便链式），于是 `vector<u64> ys =
            # xs.assign(2, 0);` 在 check 里被当成"接收者类型"放行、`run` 打
            # `[0, 0]`，而发射器直译成的 C++ 编不过（void → vector）。
            # 解释器是语言语义的定义：这里返回 None，静态侧（typecheck）声明
            # `void`，两侧同时对齐 C++。
            if len(args) != 2:
                raise InterpError("assign 需要两个实参：assign(个数, 值)")
            if isinstance(recv, FixedList):
                raise InterpError("定长数组没有 assign：容量固定，不能改长度")
            _k = _convert_read(args[0], "u64")
            recv.clear()
            _x = self._copy_arg(self._arg_lvs(n), 1, args[1])
            for _ in range(int(_k)):
                recv.append(_convert_to(copy.deepcopy(_x), _elem_tn))
                self.allocs += 1
            return None
        if name in ("push_back", "push"):
            # `push` 是**弃用别名**（std::vector 叫 `push_back`）。
            if isinstance(recv, FixedList) and len(recv) >= recv.capacity:
                raise InterpError(
                    f"FixedVec 容量溢出：容量 {recv.capacity}，已经装了 {len(recv)} 个。"
                    f"定长数组不能增长——要么调大 N，要么先用 `requires` 挡住")
            if not isinstance(recv, FixedList):
                cap = self._cap.get(id(recv), 0)
                if len(recv) >= cap:
                    self._cap[id(recv)] = max(4, cap * 2)
                    self.allocs += 1
            _v = self._copy_arg(self._arg_lvs(n), 0, args[0])
            recv.append(_convert_to(_v, _elem_tn))
            return recv
        if name == "pop":
            # T12（前置条件优先）：调用点已保证非空，所以直接返回值，不返回 Result
            if not recv:
                raise InterpError("pop 在空容器上调用（调用点应已由 requires 保证非空）")
            return _convert_read(recv.pop(), _elem_tn)
        if name == "find":
            # **返回下标，找不到返回 `npos`**（与 C++ 的 `string::find` /
            # `std::find` 的口径一致：AI 会写 `if (xs.find(v) != npos)`）。
            # 空容器上不会崩：循环 0 次 → npos。
            _needle = _convert_to(args[0], _elem_tn)
            for _i, _it in enumerate(recv):
                if _convert_read(_it, _elem_tn) == _needle:
                    return _u64(_i)
            return _u64(_U64_MAX)
        if name == "insert":
            # `std::vector::insert(pos, value)`：在 `pos` **之前**插入。
            # `pos == size()` 等价于 `push_back`（C++ 的 `end()`）。
            #
            # **三参形式 `insert(pos, count, value)`**（2026-11-13）：C++ 的同名
            # 重载——在 `pos` 前插入 `count` 个 `value`。以前这条分支只读
            # `args[1]` 当值、把 `args[2]` **静默丢掉**：`xs.insert(1, 3, 7)`
            # 只插一个 3（连值都取错了）。而 check 期**没有方法元数校验**
            # （`typecheck.METHOD_TABLE` 只登记名字，实测 3 参 `check` rc=0），
            # 所以那是「check 绿、run 给静默错值」——按本项目的 check⇔run
            # 口径，这里必须给对语义，而不是只在 run 期拒绝（那会反过来制造
            # check 绿 / run 红，且 `typecheck.py` 不在本轮可改范围）。
            #
            # **已知边界**：静态 κ 仍按「一次 `insert` = +1 个元素」记
            # （`cost_domain` 不在本轮可改范围），所以 `count` 是**符号量**时
            # κ 会低估（常数 count 不影响阶）。成本域补上 `count` 项之前，
            # 跨多元素插入的成本上界只能按写上界常数处理。
            if len(args) not in (2, 3):
                raise InterpError(
                    f"E-Type-ArgCount：序列的 insert 要 2 个或 3 个实参"
                    f"（`insert(pos, value)` / `insert(pos, count, value)`），"
                    f"给了 {len(args)} 个")
            _lvs = self._arg_lvs(n)
            _pos = _index(args[0], None, "插入位置")
            if _pos > len(recv):
                raise InterpError(
                    f"E-Index-OutOfRange：插入位置 {_pos} 超过容器长度 {len(recv)}"
                    f"（合法范围 0..{len(recv)}，等于长度表示追加）")
            if len(args) == 3:
                _cnt = args[1]
                if isinstance(_cnt, bool):
                    _cnt = int(_cnt)
                if not isinstance(_cnt, int):
                    raise InterpError(
                        f"E-Type-ArgCount：`insert(pos, count, value)` 的 count "
                        f"必须是整数，得到 {_runtime_name(_cnt)}")
                _cnt = int(_cnt)
                if _cnt < 0:
                    raise InterpError(
                        f"E-Type-ArgCount：`insert(pos, count, value)` 的 count "
                        f"不能为负（得到 {_cnt}）")
                _val_idx = 2
            else:
                _cnt = 1
                _val_idx = 1
            if isinstance(recv, FixedList):
                if len(recv) + _cnt > recv.capacity:
                    raise InterpError(
                        f"FixedVec 容量溢出：容量 {recv.capacity}，已经装了 {len(recv)} 个，"
                        f"再插 {_cnt} 个就超了。定长数组不能增长——要么调大 N，"
                        f"要么先用 `requires` 挡住")
            elif len(recv) + _cnt > self._cap.get(id(recv), 0):
                # 一次 `insert` 在 C++ 里最多触发**一次**扩容（几何增长）。
                self._cap[id(recv)] = max(4, self._cap.get(id(recv), 0) * 2,
                                          len(recv) + _cnt)
                self.allocs += 1
            if _cnt == 0:
                # `insert(pos, 0, v)` 是 no-op（C++ 也不构造任何元素）。
                return None
            _v = self._copy_arg(_lvs, _val_idx, args[_val_idx])
            _item = _convert_to(_v, _elem_tn)
            if _cnt == 1:
                recv.insert(_pos, _item)
                return None
            # n 个**互相独立**的副本：C++ 是 n 次拷贝构造，共享同一个对象
            # 会让 `xs[1][0] = 9` 改到 `xs[2]` 上（静默别名）。
            _items = [_item]
            for _ in range(_cnt - 1):
                _items.append(self._copy_value(_item))
            recv[_pos:_pos] = _items
            return None
        if name == "erase":
            # `std::vector::erase(pos)`：删掉下标 `pos` 处的元素（O(n) 搬移）。
            _pos = _index(args[0], recv, "要删除的下标")
            del recv[_pos]
            return None
        if name == "clear":
            recv.clear()
            return None
        if name in ("back", "front"):
            if not recv:
                raise InterpError(
                    f"{name}() 在空容器上调用（C++ 里这是 UB；"
                    f"先用 `!xs.empty()` 或 `requires` 保证非空）")
            return _convert_read(recv[-1] if name == "back" else recv[0], _elem_tn)
        raise InterpError(f"未实现的方法 {name}")

    def _field(self, n: Node, env, force) -> Any:
        # `Type::member`：类型名本身不是值，返回一个类型引用标记
        if n.children and n.children[0].kind == "type-name":
            return TypeRef(n.children[0].name or "", n.name or "")
        # 1) 枚举变体：Token.Number / CalcError.Empty
        if n.children and n.children[0].kind == "path":
            base_name = n.children[0].name or ""
            if base_name not in env:
                # 当枚举类型名处理
                return EnumVal(base_name, n.name or "")
        base = self._eval(n.children[0], env, force)
        nm = n.name if n.name is not None else str(n.value if n.value is not None else "")
        if nm.isdigit():
            return base[int(nm)]
        if isinstance(base, StructVal):
            if nm not in base.fields:
                raise InterpError(f"{base.type_name} 没有字段 {nm}"
                                  f"（有：{', '.join(base.fields)}）")
            _tn = self.structs.get(base.type_name, {}).get(nm)
            _stored = base.fields[nm]
            _val = _convert_to(_stored, _tn)
            # **读字段返回的就是字段里那份存储**（K1，2026-11-10）。
            # `_convert_to` 对容器已经就地重标，正常情况下 `_val is _stored`；
            # 只有"宿主/旧路径塞进来的普通 list 要变成 `vector<u8>` 的
            # `Text`"这一类**运行时形态**变化才需要换对象。换完立刻写回字段，
            # 否则 `s.b.push(...)` 又会写在这份临时副本上（同一个数据丢失）。
            if _val is not _stored and isinstance(_stored, list):
                base.fields[nm] = _val
            return _val
        if isinstance(base, EnumVal):
            return EnumVal(base.type_name, base.variant, base.payload)
        if isinstance(base, tuple):
            # **元组只有位置字段**（`.0` / `.1`，上面 `nm.isdigit()` 那条）；
            # `--compat` 下额外认 C++ `std::pair` 的 `.first` / `.second`
            # （2026-11-12，`compat.PAIR_FIELD_INDEX`）。字段名映射**只在
            # 运行期值确实是二元组时**生效——`struct` 里叫 `first` 的字段
            # 走上面那条分支，**不会被改名**。
            _pi = (_compat.PAIR_FIELD_INDEX.get(nm)
                   if _compat.enabled() else None)
            if _pi is not None:
                _ix = int(_pi)
                if _ix < len(base):
                    return base[_ix]
                raise InterpError(
                    f"`{nm}` 要二元组（`.0` / `.1`）：这里只有 {len(base)} 元组 "
                    f"{base!r}。`--compat` 把 `pair.first` / `pair.second` 映射到 "
                    f"`.0` / `.1`，它只对**二元组**成立。")
        raise InterpError(f"未实现的字段访问 {nm}")

    def _dispatch(self, n: Node, env, force) -> Any:
        label = n.name or ""
        args = self._args(n, env, force)
        kern = self.kernels.get(label)
        if kern is None:
            raise InterpError(f"?!{label} 没有对应的 kernels 块")

        cands = {c.name: c for c in kern.children if c.kind == "candidate"}
        want = force.get(label)
        if want is not None:
            cand = cands.get(want)
            if cand is None:
                raise InterpError(f"?!{label}: 没有候选 {want}")
        else:
            # **先查表**（方案甲，2026-10-30）：按当前实参的规模选段。
            # 表不存在 → 走原来的"第一个实现"，并留一条警告（不静默）。
            _size = _size_of_args(args)
            _segs = self.tables.get(label) or []
            _seg = None
            if _segs and _size is not None:
                for _lo, _hi, _who in _segs:
                    if _lo <= _size <= _hi:
                        _seg = _who
                        break
            cand = None
            if not _seg and _segs and _size is not None:
                # **越界按 `over` 的顺序退回**：低于下界用第一个，高于上界用**最后一个**。
                # 以前一律退"第一个"——而承诺里已经写明越上界该跑什么
                # （`cost` 出来是 `n(xs)>65536: …21×n…`），两边不同源就是**破界**。
                # **沿最近的那一段延伸**：低于下界用第一段的候选，高于上界用最后一段的。
                # 与 `cost_interp._dispatch` 的哨兵段**同一条规则**——两边不同源就是破界。
                if _size < _segs[0][0]:
                    _seg = _segs[0][2]
                elif _size > _segs[-1][1]:
                    _seg = _segs[-1][2]
                if _seg:
                    _msg = (f"?!{label}: 规模 {_size} 超出分档表范围"
                            f"（表覆盖 {_segs[0][0]}..{_segs[-1][1]}），"
                            f"按规则沿最近段延伸 → 候选 {_seg}")
                    if _msg not in self.dispatch_warnings:
                        self.dispatch_warnings.append(_msg)
                        print("  ！ " + _msg, file=sys.stderr)
            if _seg and _seg in cands and any(c.kind == "block" for c in cands[_seg].children):
                cand = cands[_seg]
            # **P0 修复**：表已经选好候选时，绝不能被 `over` 的顺序循环覆盖。
            # 旧写法无条件走下面的循环，实际执行 `over` 里第一个有实现的候选，
            # 而 `last_candidates` 记的是表选的那个 —— 报告与实际不一致。
            if cand is None:
                order = self.over.get(label) or list(cands)
                for nm in order:
                    if nm in cands and any(c.kind == "block" for c in cands[nm].children):
                        cand = cands[nm]
                        break
            if cand is None:
                for nm, c in cands.items():
                    if any(x.kind == "block" for x in c.children):
                        cand = c
                        break
        if cand is None:
            raise InterpError(f"?!{label}: 没有已实现的候选")
        # **唯一赋值点**：报告的就是要跑的
        self.last_candidates[label] = cand.name or ""

        # 候选 inline：在宿主环境的副本上执行（所以能读到 `n`）
        cenv = dict(self.globals)
        cenv.update(env)
        params = next((c for c in kern.children if c.kind == "params"), None)
        pnames = [c.name for c in (params.children if params else [])]
        # 候选形参与返回值也要按声明类型解释（`kernels f(...) -> u64`）
        _ktenv = dict(self._tenv())
        for _p, nm, v in zip((params.children if params else []), pnames, args):
            _pt = _p.children[0] if _p.children else None
            _ktenv[nm] = _pt
            cenv[nm] = _convert_scalar(v, _type_label(_pt))
        _kret = next((c for c in kern.children
                      if getattr(c, "kind", "") in ("type-name", "type-ref")), None)
        body = stmt_body(cand)
        self._push_tenv(_ktenv)
        try:
            try:
                self._block(body, cenv, force)
                return None
            except _Return as r:
                return _convert_to(r.value, _kret)
        finally:
            self._pop_tenv()


# ---------------------------------------------------------------- 工具

def _fixed_capacity(tn: Node, consts: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """从 `FixedVec<T, N>` 的 type-name 节点里取出 N。

    **N 可以是字面量，也可以是模块级常量名**（`FixedVec<u64, STACK_CAP>`）。
    只认字面量会让「用常量名写的 FixedVec」**静默退化成普通 Vec**——
    定长性丢了，`push` 不再报错，是整个语言最不该有的静默。
    """
    consts = consts or {}
    for c in tn.children:
        if c.kind != "type-args":
            continue
        for a in c.children:
            if a.kind == "type-const":
                try:
                    return int(a.value)
                except (TypeError, ValueError):
                    return None
            if a.kind == "type-name":
                nm = a.name or ""
                if nm.isdigit():
                    return int(nm)
                v = consts.get(nm)
                if isinstance(v, int):
                    # 常量现在可能带无符号标记（`const N: u64`）；容量要裸 int
                    return int(v)
            # **常量表达式**（v0.72）：`FixedVec<u64, N + 1>` / `1 + 2`。
            # 以前这里只认字面量与常量名，表达式一律返回 None →
            # `FixedVec` 静默退化成普通 `Vec`，容量检查形同虚设。
            _v = _const_expr_int(a, consts)
            if _v is not None:
                return _v
    return None


def _const_expr_int(n, consts: Dict[str, Any]) -> Optional[int]:
    """把类型实参里的**常量表达式**求成 int（只认 + - * / 与常量名/字面量）。"""
    if n is None:
        return None
    k = getattr(n, "kind", "")
    if k in ("lit-int", "type-const"):
        try:
            return int(n.value)
        except (TypeError, ValueError):
            return None
    if k == "type-name" and (n.name or "").isdigit():
        return int(n.name)
    if k == "path":
        v = consts.get(n.name)
        return int(v) if isinstance(v, int) else None
    if k == "binary" and len(n.children) >= 2:
        l = _const_expr_int(n.children[0], consts)
        r = _const_expr_int(n.children[1], consts)
        if l is None or r is None:
            return None
        op = n.value or ""
        if op == "+":
            return l + r
        if op == "-":
            return l - r
        if op == "*":
            return l * r
        if op == "/" and r:
            return l // r
        if op == "**" and r >= 0:
            # 常量表达式（`array<u64, 2 ** 4>` 这类）：与 `*` 一样**不截断**
            return l ** r
        return None
    if k in ("paren", "ref") and n.children:
        return _const_expr_int(n.children[0], consts)
    return None


def _literal(n: Optional[Node]) -> Any:
    """把常量初值（字面量）变 Python 值；非字面量给 None。

    **2026-11-10 起模块常量不再走这里**（`_const_value` 才是唯一入口）：
    `None` 既表示"不是字面量"又会被当成值，是 `const N = 10 * 2` 打印
    `None` 的根源。保留本函数只是兼容旧调用，新代码不要用。
    """
    if n is None:
        return None
    if n.kind == "lit-int":
        return int(n.value)
    if n.kind == "lit-bool":
        return n.value in (True, "true")
    if n.kind == "lit-char":
        return _char_lit_value(n.value)
    return None


def _const_value(n: Optional[Node], consts: Dict[str, Any]) -> Any:
    """求模块常量初值的值；**求不出返回 `_CONST_UNKNOWN`**（不是 None）。

    ## 为什么在解释器里再实现一遍常量折叠

    静态侧 `typecheck._const_init_shape_ok` 只判"形状能不能在装载时算"，
    真正的值必须由**运行期同一套** `_arith` / `_cmp` 算——否则
    `2 ** 64` 的回绕、`0 - 1` 的补码、`u8` 截断会各算各的，又变成
    「check 一个答案、run 另一个答案」。

    支持的字面量 / 运算与静态侧**逐条对应**（改一处必须改两处，
    `tests/cases/test_typecheck.py` 钉了正反例）：
    整数 / 布尔 / 字符 / 浮点字面量、**前面已声明**的模块常量名、括号、
    一元 `- + ~ not`、算术 / 位运算 / 比较、标量 `as`。
    函数调用、下标、字段、枚举变体、容器字面量一律求不出（返回哨兵，
    由 `Interp._collect` 报 `E-Const-NotConstant`）。
    """
    if n is None:
        return _CONST_UNKNOWN
    k = getattr(n, "kind", "")
    if k == "lit-int":
        return int(n.value)
    if k == "lit-bool":
        return n.value in (True, "true")
    if k == "lit-char":
        return _char_lit_value(n.value)
    if k == "lit-float":
        return float(n.value)
    if k == "path":
        nm = (n.name or "")
        if "." in nm:
            # 枚举变体不是字面量：序号需要 `.tu` 的枚举声明。
            # 静态侧同样拒绝（`E-Const-NotConstant`），两边一致。
            return _CONST_UNKNOWN
        return consts.get(nm, _CONST_UNKNOWN)
    if k == "paren" and n.children:
        return _const_value(n.children[0], consts)
    if k == "unary" and n.children:
        v = _const_value(n.children[0], consts)
        if v is _CONST_UNKNOWN:
            return _CONST_UNKNOWN
        op = n.value or ""
        if op == "+":
            return v
        if op == "-":
            # 与 `_k_expr_unary` 同一条：`UInt` 按无符号取负，裸 int 按 i64 补码。
            return -v if isinstance(v, UInt) else _wrap(-int(v))
        if op == "~":
            if isinstance(v, UInt):
                return _UCLASS[getattr(v, "bits", 64)](~int(v))
            if isinstance(v, bool):
                v = int(v)
            if isinstance(v, float):
                return _CONST_UNKNOWN
            return _wrap(~int(v))
        if op == "!":
            return not v
        return _CONST_UNKNOWN
    if k == "binary" and len(n.children) >= 2:
        a = _const_value(n.children[0], consts)
        b = _const_value(n.children[1], consts)
        if a is _CONST_UNKNOWN or b is _CONST_UNKNOWN:
            return _CONST_UNKNOWN
        op = n.value or ""
        if op in ("<", "<=", ">", ">=", "==", "!="):
            return _cmp(op, a, b)
        if op in ("+", "-", "*", "/", "%", "**", "&", "|", "^", "<<", ">>"):
            # `_arith` 对除零 / 整数负指数的 `InterpError` **原样上抛**：
            # 这是语言的运行期错误（有码、有说明），比静默 None 好。
            return _arith(op, a, b)
        return _CONST_UNKNOWN
    if k == "cast" and len(n.children) >= 2:
        v = _const_value(n.children[0], consts)
        if v is _CONST_UNKNOWN:
            return _CONST_UNKNOWN
        ty = next((c for c in n.children[1:]
                   if getattr(c, "kind", "").startswith("type")), None)
        name = _type_label(ty)
        if not name or (name not in _SCALAR_NUMERIC and name not in ("f32", "f64")):
            return _CONST_UNKNOWN
        if isinstance(v, float) and name in _SCALAR_NUMERIC:
            v = int(v)
        return _convert_scalar(v, name)
    return _CONST_UNKNOWN


def _index(i: Any, base: Any = None, what: str = "下标") -> int:
    """把下标值取成 int、**拒绝负数**，并在能拿到容器时**判上界**。

    以前 `xs[-1]` 直接落到 Python 的 `list[-1]`：读会拿到最后一个元素，
    写会**静默改坏**它——这里不是 Python，负下标没有"从末尾数"的语义。
    上界同理：`v.at(9)` / `v[i]` 越界以前直接抛宿主 `IndexError`
    （没有行号、不在 Elench 诊断体系里）。统一在所有 `[]` / `.at()` 入口
    先过这一道，错误码沿用 check 期的 `E-Index-OutOfRange`。
    """
    if isinstance(i, bool):
        i = int(i)
    if not isinstance(i, int):
        raise InterpError(f"E-Index-Type：{what}必须是整数，得到 {type(i).__name__}")
    # **先取真实数值，再做上下界判断**：`UInt.__lt__/__le__/__ge__` 会按
    # 「最宽无符号操作数」的位宽把**两边一起回绕**（`UInt._u_bits`）。那是
    # 算术/比较正确的无符号语义（`u8 a = 200; a + 100` → 44 必需），但
    # **不是"下标是否越界"的语义**：
    #   `vector<u8> text; u8 idx = text.at(0);`（值 99）当索引进长度 256 的
    # `vector<u64>` 时，`idx >= 256` 被算成 `99 >= 0` → True → 误报
    # `E-Index-OutOfRange：下标 99 越界（容器长度 256）`。`256 % 256 = 0`
    # 正是这条回绕比较的产物；先存进 `u64` 变量之所以"正常"，也只是因为
    # 位宽变成 64、`256 % 2^64` 刚好还是 256，属于**碰巧**。
    # 越界判断要的是**真实数值**，所以这里 `int(i)` 一次，后面只用 `iv`。
    iv = int(i)
    # **标量没有下标**：`u64 x = 5; x[0]` 以前落到 Python 的
    # `'U64' object is not subscriptable`（宿主异常）。这里先挡住。
    if isinstance(base, (int, float)):
        raise InterpError(
            f"E-Type-NotIndexable：{_runtime_name(base)}（值 {base!r}）是标量，"
            f"不能用下标 / `.at()` 访问")
    _n = len(base) if base is not None and hasattr(base, "__len__") else None
    if iv < 0:
        _ln = f"，容器长度 {_n}" if _n is not None else ""
        raise InterpError(
            f"E-Index-Negative：{what}不能为负（得到 {iv}{_ln}）。"
            f"Elench 的下标从 0 开始，**不会**像 Python 那样从末尾取；"
            f"访问最后一个元素请写 `xs[xs.length() - 1]`，并先用 `requires` 或 "
            f"`i < xs.length()` 保证不越界")
    if _n is not None and iv >= _n:
        _range = "，空容器" if _n == 0 else f"，合法范围 0..{_n - 1}"
        raise InterpError(
            f"E-Index-OutOfRange：{what} {iv} 越界（容器长度 {_n}{_range}）。"
            f"先用 `requires` 或 `i < xs.length()` 保证下标在范围内")
    return iv


#: `u64` 下溢诊断的受限求值只认这几个**纯**方法（不调用户代码）。
_SAFE_LEN_METHODS = frozenset(("length", "size"))


def _recursion_depth_of(spec: Node):
    """读一个 `spec` 的 `recursion depth <= N;`。

    返回三态：

      · `None` —— **没写**这条子句（不是错：非递归函数可以不写）；
      · `(N, None)` —— 写成了正整数常量（`N >= 1`）；
      · `(None, 原文)` —— 写了但**读不出正整数**（`<= 0` / `<= n` / 形状不对）。

    第三态必须报诊断（`E-Recursion-BadDepth` / `E-Recursion-Uncheckable`）：
    cost 域对"读不出"的处理是**当成没声明**（只按一层计 κ），而
    `check_recursion_depth` 只看"有没有这条子句"——两者合起来会让
    `recursion depth <= 0;` 变成"检查通过 + κ 只算一层"，又是一个 κ < ops。
    """
    body = next((c for c in (getattr(spec, "children", ()) or ())
                 if getattr(c, "kind", "") == "spec-body"), None)
    for clause in ((body.children if body else ()) or ()):
        if getattr(clause, "kind", "") != "clause" or (clause.name or "") != "recursion":
            continue
        for ch in (getattr(clause, "children", ()) or ()):
            if getattr(ch, "kind", "") != "binary" or (ch.value or "") not in ("<=", "<"):
                continue
            if len(getattr(ch, "children", ()) or ()) < 2:
                continue
            lhs, rhs = ch.children[0], ch.children[1]
            if getattr(lhs, "kind", "") != "path" or (lhs.name or "") != "depth":
                continue
            n = None
            if getattr(rhs, "kind", "") == "lit-int":
                try:
                    n = int(rhs.value)
                except (TypeError, ValueError):
                    n = None
            if n is None or n < 1:
                return (None, _expr_text(clause.children[0]) if clause.children
                        else "recursion depth")
            return (n, None)
        return (None, _expr_text(clause.children[0]) if clause.children
                else "recursion depth")
    return None


def _expr_text(n) -> str:
    """把表达式节点渲染成**源码样式**的短文字（只用于错误消息）。"""
    k = getattr(n, "kind", "")
    ch = getattr(n, "children", ()) or ()
    if k == "path":
        return n.name or "?"
    if k in ("lit-int", "lit-float", "lit-bool", "lit-char", "lit-str"):
        return str(n.value)
    if k == "binary" and len(ch) >= 2:
        return f"{_expr_text(ch[0])} {n.value} {_expr_text(ch[1])}"
    if k == "paren" and ch:
        return f"({_expr_text(ch[0])})"
    if k in ("method-call", "call", "dispatch"):
        args = next((c for c in ch if getattr(c, "kind", "") == "args"), None)
        a = ", ".join(_expr_text(c) for c in (args.children if args is not None else []))
        if k == "method-call" and ch:
            return f"{_expr_text(ch[0])}.{n.name}({a})"
        return f"{n.name}({a})"
    if k == "field" and ch:
        return f"{_expr_text(ch[0])}.{n.name}"
    if k in ("ref", "deref") and ch:
        return ("&" if k == "ref" else "*") + _expr_text(ch[0])
    return "…"


def _guarantee_expr_text(expr) -> str:
    """把一条断言渲染成**人读的一行**（块形诊断里点名「第 k 条」时用）。

    `guarantees.describe()` 对比较式只给 `…`（它只认谓词调用），块形报错若
    只说「第 2/2 条 …」，读者还是不知道是哪条。这里覆盖块里合法的那几种
    形状（path / 字面量 / 比较 / `&&` `||` / `!` / 谓词调用）。
    """
    k = getattr(expr, "kind", "")
    kids = getattr(expr, "children", None) or []
    if k == "path":
        return expr.name or "?"
    if k == "lit-int":
        return str(expr.value)
    if k == "lit-bool":
        return "true" if expr.value else "false"
    if k == "call":
        args = next((c for c in kids if c.kind == "args"), None)
        vals = ", ".join(_guarantee_expr_text(a)
                         for a in (args.children if args else []))
        return f"{(expr.name or '?').split('.')[-1]}({vals})"
    if k == "binary" and len(kids) >= 2:
        return (f"{_guarantee_expr_text(kids[0])} {expr.value} "
                f"{_guarantee_expr_text(kids[1])}")
    if k == "unary" and kids:
        return f"{expr.value}{_guarantee_expr_text(kids[0])}"
    return "…"


def _guarantee_site(expr) -> str:
    """`guarantees { … }` 块里这条断言是**第几条**（不在块里时返回空串）。

    parser 在块形的每条表达式上挂了 `clause_no` / `clause_total` / `line`
    （普通属性，不进 AST dump）。诊断里带上它，失败才能指名道姓；否则
    「两条独立断言」就退化成两条一起报同一句「guarantees 不满足」。
    """
    no = getattr(expr, "clause_no", 0) or 0
    if not no:
        return ""
    total = getattr(expr, "clause_total", 0) or no
    return f"第 {no}/{total} 条 guarantees（@{getattr(expr, 'line', 0)} 行）"


def _guarantee_reason(exc: BaseException) -> str:
    """把 guarantees 求值时的**宿主异常**翻成一句语言侧的话。

    **绝不把异常类名或 `str(exc)` 透给用户**（2026-11-09）：`guarantees` 是
    语言里的后置条件，用户不该看到 `TypeError: object of type 'Ok' has no
    len()`。`InterpError` 不经过这里（它是语言级诊断，见 `_check_guarantees`）。
    """
    if isinstance(exc, TypeError):
        return ("谓词实参的运行时类型与谓词不匹配"
                "（对非容器值调用 `is_sorted` / `contains`，"
                "或对没有值的返回值做大小比较）")
    if isinstance(exc, IndexError):
        return "谓词内部下标越界（不是 guarantees 被违反）"
    if isinstance(exc, ZeroDivisionError):
        return "谓词内部出现除零（不是 guarantees 被违反）"
    if isinstance(exc, RecursionError):
        return "谓词求值递归过深（不是 guarantees 被违反）"
    return "谓词求值时发生内部错误（不是 guarantees 被违反）"


def _runtime_name(v) -> str:
    """错误消息里用的**语言侧**类型名（`U64` → `u64`，`float` → `f64`）。"""
    if isinstance(v, UInt):
        return _WIDTH_TO_NAME.get(getattr(v, "bits", 0), "u64")
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "i64"
    if isinstance(v, float):
        return "f64"
    if isinstance(v, str):
        return "Text"
    return type(v).__name__


def _wrap(v: int, width: int = DEFAULT_WIDTH) -> int:
    """按位宽回绕（v0.67 算术全函数化）。"""
    if not isinstance(v, int) or isinstance(v, bool):
        return v
    m = 1 << width
    v %= m
    # **存二进制补码的值本身**（2026-11-05）：`2 - 3` 得 -1，不是 2^64-1。
    half = m >> 1
    return v - m if v >= half else v

def _ieee_div(a, b) -> float:
    """`x/0.0` 的 IEEE 结果：非零/±0 → ±inf；0/0 → nan。"""
    import math
    a = float(a)
    b = float(b)
    if a == 0.0:
        return math.nan
    sign = -1.0 if (math.copysign(1.0, a) * math.copysign(1.0, b)) < 0 else 1.0
    return sign * math.inf

def _round_f32(v: float) -> float:
    """把 Python 的 double 舍入到 IEEE 754 单精度（`f32`）。"""
    import struct
    return struct.unpack("f", struct.pack("f", v))[0]


# ---------------------------------------------------------------- 有限域穷举
#
# `guarantees` 以前只有一档强度：**跑过的那次输入**成立（运行期断言）。
# 这里把它抬成「**有限输入域上的真证明**」：参数域有限且不大时，`check`
# 阶段就枚举全部输入，对每个输入求值 guarantees。三档必须分开说：
#
#   【证明】域有限、全部组合都跑完且都满足
#   【符合】域无界（u64/f64/容器/指针…），**不抽样**，只声明运行期断言
#   【声明】没写 guarantees
#
# 另有两档【未证】：域太大跳过、预算用完没跑完——**没跑完就绝不说证明**。

#: 新增诊断码：找到反例。登记在 `文档/语法清单.md` 的诊断表里。
E_GUARANTEE_COUNTEREXAMPLE = "E-Guarantee-Counterexample"

#: 真正穷举得起的标量域。**只放这些**：u32/u64/f64 是 2^32/2^64/连续统，
#: 放进来只能靠抽样——那正是本功能要拒绝的事。
#: 用 `range` 而不是 `tuple`：`interp` 每个进程都会被 import，把 13 万个
#: 整数做成常量等于给所有命令加内存；`itertools.product` 直接吃 range。
EXHAUSTIVE_DOMAINS = {
    "bool": (False, True),
    "u8": range(1 << 8),
    "i8": range(-(1 << 7), 1 << 7),
    "u16": range(1 << 16),
    "i16": range(-(1 << 15), 1 << 15),
}

#: 组合数默认上限（可被 `check --max-combos=N` 覆盖）。
EXHAUSTIVE_MAX_COMBOS = 65536

#: 单函数穷举的总墙钟预算（秒，可被 `check --exhaustive-timeout=S` 覆盖）。
EXHAUSTIVE_TIMEOUT_S = 5.0


def _exh_type_text(tn) -> str:
    """类型节点的源码写法（只用于消息；拿不到就退回头名）。"""
    try:
        from checker import type_str
        return type_str(tn)
    except Exception:                                   # noqa: BLE001
        return ((getattr(tn, "name", "") or "") if tn is not None
                else "?") or getattr(tn, "kind", "?")


def _exh_param_type(prm):
    """形参的类型节点（与 `dsh._param_type_node` 同一判据）。"""
    return next((c for c in (prm.children or [])
                 if getattr(c, "kind", "") in
                 ("type", "type-fail", "type-ref", "type-name")), None)


def _exh_collect_enums(*mods) -> Dict[str, Node]:
    """枚举名 → 声明节点（先出现者优先；.eln 与 .tu 都收）。"""
    out: Dict[str, Node] = {}
    for mod in mods:
        for item in ((mod.children if mod is not None else []) or []):
            if getattr(item, "kind", "") == "enum" and item.name:
                out.setdefault(item.name, item)
    return out


def _exh_domain(tn, enums: Dict[str, Node], depth: int = 0):
    """一个类型节点的**全部值** → `(值列表, 类型标签, None)`；不可穷举时
    `(None, 类型标签, 原因)`。

    只认标量（`bool` / `u8` / `i8` / `u16` / `i16`）与小 `enum`：
    `enum` 的每个变体按载荷类型枚举，无载荷变体只有一个值。
    **容器 / 引用 / 指针 / 大整数 / 浮点一律拒绝**，绝不抽样冒充。
    """
    if tn is None:
        return None, "?", "没有类型"
    if depth > 8:                                       # 递归枚举（enum 套 enum）
        return None, _exh_type_text(tn), "枚举嵌套过深"
    if getattr(tn, "kind", "") != "type-name":
        return None, _exh_type_text(tn), "不是标量（容器 / 引用 / 指针一律跳过）"
    name = canon(getattr(tn, "name", "") or "")
    if name in EXHAUSTIVE_DOMAINS:
        return EXHAUSTIVE_DOMAINS[name], name, None
    enum = enums.get(name)
    if enum is None:
        if name in ("u64", "i64", "f64", "f32"):
            return None, name, "无界"
        if name in ("u32", "i32"):
            return None, name, "位宽太大（2^32 组合），未纳入穷举"
        return None, name, "无界（未知类型）"
    import itertools
    vals: List[Any] = []
    for v in (enum.children or []):
        if getattr(v, "kind", "") != "variant":
            continue
        payloads = [c for c in (v.children or [])
                    if getattr(c, "kind", "") in
                    ("type", "type-fail", "type-ref", "type-name")]
        if not payloads:
            vals.append(EnumVal(name, v.name))
            continue
        lists = []
        for pt in payloads:
            sub, sub_label, why = _exh_domain(pt, enums, depth + 1)
            if sub is None:
                return (None, name,
                        f"变体 `{v.name}` 的载荷 {sub_label} 不可穷举（{why}）")
            lists.append(sub)
        for combo in itertools.product(*lists):
            payload = combo[0] if len(combo) == 1 else tuple(combo)
            vals.append(EnumVal(name, v.name, payload))
    if not vals:
        return None, name, "枚举没有变体"
    return vals, name, None


def _exh_params(fn: Node) -> List[Node]:
    ps = next((c for c in (fn.children or []) if c.kind == "params"), None)
    return list(ps.children) if ps else []


#: 随机源 / 读 stdin：结果不在输入里，**穷举覆盖不了**。
_EXH_NONDET_CALLS = ("rand_u64", "read_stdin")

#: 这些效果让返回值不只由参数决定（随机 / 文件系统 / 环境）。
_EXH_UNSOUND_EFFECTS = frozenset({"nondet", "fs", "env"})


def _exh_call_names(node, acc: set) -> set:
    for c in (getattr(node, "children", None) or []):
        if getattr(c, "kind", "") == "call":
            acc.add((getattr(c, "name", "") or "").split(".")[-1])
        _exh_call_names(c, acc)
    return acc


def _exh_nondeterministic(interp: "Interp", fn: Node, seen=None) -> bool:
    """函数**或它的调用链**是否用了随机源（`rand_u64`）。

    为什么必须查传递闭包：`f` 自己没写 `rand_u64`、但它调用的 `g` 写了，
    对 `f` 的 `guarantees` 来说随机结果同样不在参数域里。只看直接调用会
    把「随机的函数」说成【证明】。
    """
    seen = set() if seen is None else seen
    names = _exh_call_names(fn, set())
    if any(n in _EXH_NONDET_CALLS for n in names):
        return True
    for n in names:
        if n in seen:
            continue
        sub = (getattr(interp, "fns", {}) or {}).get(n)
        if sub is None:
            continue
        seen.add(n)
        if _exh_nondeterministic(interp, sub, seen):
            return True
    return False


def _exh_unsound_reason(interp: "Interp", fn: Node, effects=None) -> str:
    """函数的结果是否**不只由参数决定**；是则返回原因（空串 = 可以穷举）。

    两条证据：
      · 调用链里出现 `rand_u64` / `read_stdin`（直接扫调用图，兜住效果表缺项）；
      · `checker.function_effects` 的传递闭包里含 `nondet` / `fs` / `env`
        （FFI 声明的效果也算；这是既有判据，不另写一套）。
    """
    if _exh_nondeterministic(interp, fn):
        return "rand_u64 / read_stdin"
    if effects is None:
        try:
            from checker import function_effects
            effects = function_effects(interp.decl, interp.impl)
        except Exception:                               # noqa: BLE001
            effects = {}
    bad = sorted(set(effects.get(getattr(fn, "name", ""), set()))
                 & _EXH_UNSOUND_EFFECTS)
    return "、".join(bad) if bad else ""


def _exh_render(v) -> str:
    """反例里的人读值（语言写法，不是 Python repr）。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, EnumVal):
        if v.payload is None:
            return f"{v.type_name}::{v.variant}"
        return f"{v.type_name}::{v.variant}({_exh_render(v.payload)})"
    if isinstance(v, Ok):
        return _exh_render(v.v)
    if isinstance(v, Err):
        return f"Err({_exh_render(v.e)})"
    if isinstance(v, UInt):
        return str(int(v))
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, (list, tuple)):
        inner = ", ".join(_exh_render(x) for x in v)
        return f"[{inner}]"
    return str(v)


def _exh_reset(interp: "Interp", initial_globals: Dict[str, Any]) -> None:
    """每个输入都从同一份初始状态开始。

    为什么必须做：模块级可变状态（全局容器）会让「先跑 a、后跑 b」与
    「只跑 b」给出不同结果，那样枚举顺序就成了语义的一部分，证明不成立。
    每轮深拷贝一次；拷贝不了就退回浅拷贝（并在报告里不声称什么——本功能
    只支持标量参数，模块级可变状态是少数情况）。
    """
    interp.steps = 0
    interp._budget_hint = None
    import copy
    try:
        interp.globals = copy.deepcopy(initial_globals)
    except Exception:                                   # noqa: BLE001
        interp.globals = dict(initial_globals)


def exhaustive_module(interp: "Interp",
                      max_combos: int = EXHAUSTIVE_MAX_COMBOS,
                      timeout: float = EXHAUSTIVE_TIMEOUT_S):
    """对模块里每个函数做**有限域穷举**，返回 `(报告列表, 反例列表)`。

    报告是给人读的分档结论（`status` 见下）；反例是给 `dsh check` 组装
    `E-Guarantee-Counterexample` 诊断的结构化数据。**不在这里打印任何东西**
    （JSON 模式下 stdout 只能有 JSON）。

    status：
      · `proved`        域有限、全部组合都满足
      · `counterexample` 找到反例（反例条目在第二个返回值里）
      · `runtime`       域无界，只做了运行期断言（【符合】）
      · `none`          没写 guarantees（【声明】）
      · `too_large`     组合数超上限，跳过穷举
      · `timeout`       预算用完，没跑完
      · `unproven`      其它不可证（运行失败 / 谓词不可判定 / 没有实现体…）
      · `uncheckable`   guarantees 本身不可检查（静态检查已报 error）
    """
    import itertools
    import time
    import guarantees as _gt

    reports: List[Dict[str, Any]] = []
    counterexamples: List[Dict[str, Any]] = []
    enums = _exh_collect_enums(interp.impl, interp.decl)
    initial_globals = dict(getattr(interp, "globals", {}) or {})
    try:
        from checker import function_effects
        _effects = function_effects(interp.decl, interp.impl)
    except Exception:                                   # noqa: BLE001
        _effects = {}
    seen: set = set()

    def _report(fn_name, status, detail, domain="", combos=0, checked=0,
                skipped=0, cex=None):
        reports.append({
            "fn": fn_name, "status": status, "detail": detail,
            "domain": domain, "combos": int(combos),
            "checked": int(checked), "requires_skipped": int(skipped),
            "counterexample": cex,
        })

    def _do(fn: Node) -> None:
        name = fn.name or ""
        seen.add(name)
        spec = (interp.specs or {}).get(name)
        if spec is None or not _gt.has_guarantees(spec):
            _report(name, "none", "没写 guarantees")
            return
        ok_exprs, bad = _gt.classify(spec)
        if bad:
            _report(name, "uncheckable", f"guarantees 不可检查：{bad[0]}")
            return
        if not ok_exprs:
            _report(name, "none", "没写 guarantees")
            return
        body = next((c for c in (fn.children or [])
                     if getattr(c, "kind", "") == "block"), None)
        if body is None:
            _report(name, "unproven", "没有实现体，无法穷举")
            return
        _unsound = _exh_unsound_reason(interp, fn, _effects)
        if _unsound:
            _report(name, "unproven",
                    f"函数（或其调用链）的结果不只由参数决定（{_unsound}）："
                    f"穷举覆盖不了 —— 不声称证明")
            return
        params = _exh_params(fn)
        labels: List[str] = []
        domains: List[List[Any]] = []
        reasons: List[str] = []
        for prm in params:
            vals, label, why = _exh_domain(_exh_param_type(prm), enums)
            labels.append(label)
            if vals is None:
                reasons.append(f"{label}（{why}）")
            else:
                domains.append(vals)
        domtext = "{" + ", ".join(labels) + "}"
        if reasons:
            # **一个无界参数就整函数【未证】**：不拿有限的那几个参数做抽样，
            # 那会把「部分输入的抽样」说成同一档结论。
            uniq = []
            for r in reasons:
                if r not in uniq:
                    uniq.append(r)
            _report(name, "runtime",
                    f"参数含 {'、'.join(uniq)}，只做了运行期断言",
                    domain=domtext)
            return
        combos = 1
        for d in domains:
            combos *= len(d)
        if combos > max_combos:
            _report(name, "too_large",
                    f"参数域 {domtext} 共 {combos:,} 种组合，超过上限 "
                    f"{max_combos:,} —— 域太大，跳过穷举（--max-combos=N 可调）",
                    domain=domtext, combos=combos)
            return

        deadline = time.monotonic() + float(timeout)
        checked = 0
        skipped = 0
        for combo in itertools.product(*domains):
            if time.monotonic() > deadline:
                _report(name, "timeout",
                        f"穷举未完成（{timeout:g}s 预算用完，已跑 "
                        f"{checked:,}/{combos:,} 种组合）—— 不声称证明",
                        domain=domtext, combos=combos, checked=checked,
                        skipped=skipped)
                return
            _exh_reset(interp, initial_globals)
            try:
                result, fails = interp.probe(name, list(combo))
            except InterpRequiresViolated as exc:
                # **只有入口函数自己的 requires** 才把输入排除在契约范围外；
                # 调用链里别的函数的 requires 被违反 = 这条输入下没跑通，
                # 不能拿来缩小入口的证明范围（否则等于把运行错误说成证明）。
                if exc.fn and exc.fn != name:
                    _report(name, "unproven",
                            f"输入 {_exh_args_text(params, combo)} 触发 "
                            f"`{exc.fn}` 的 requires 不成立 —— guarantees "
                            f"无从判定",
                            domain=domtext, combos=combos, checked=checked,
                            skipped=skipped)
                    return
                skipped += 1
                continue
            except InterpBudgetExceeded as exc:
                _report(name, "unproven",
                        f"输入 {_exh_args_text(params, combo)} 撞步数预算："
                        f"{exc} —— guarantees 无从判定",
                        domain=domtext, combos=combos, checked=checked,
                        skipped=skipped)
                return
            except ExitRequest:
                _report(name, "unproven",
                        f"输入 {_exh_args_text(params, combo)} 调用了 exit()，"
                        f"guarantees 无从判定",
                        domain=domtext, combos=combos, checked=checked,
                        skipped=skipped)
                return
            except InterpError as exc:
                _report(name, "unproven",
                        f"输入 {_exh_args_text(params, combo)} 下函数没有正常"
                        f"返回：{exc} —— guarantees 无从判定",
                        domain=domtext, combos=combos, checked=checked,
                        skipped=skipped)
                return
            checked += 1
            if not fails:
                continue
            # **第一个反例就是字典序最小输入**：域值升序、参数按声明顺序、
            # itertools.product 按字典序。所以这里不继续找"更小"的——不存在。
            bad_fail = next((f for f in fails if f.get("error")), None)
            if bad_fail is not None:
                _report(name, "unproven",
                        f"输入 {_exh_args_text(params, combo)} 下 guarantees "
                        f"无法判定（{bad_fail.get('error')}）",
                        domain=domtext, combos=combos, checked=checked,
                        skipped=skipped)
                return
            ret = result.v if isinstance(result, Ok) else result
            argtxt = _exh_args_text(params, combo)
            cex = {"args": argtxt, "ret": _exh_render(ret),
                   "lines": [f.get("line", 0) for f in fails]}
            for f in fails:
                _fn_note = (f"（在 `{f.get('fn')}` 中）"
                            if f.get("fn") and f.get("fn") != name else "")
                counterexamples.append({
                    "fn": name,
                    "line": int(f.get("line", 0) or 0),
                    "col": 0,
                    "code": E_GUARANTEE_COUNTEREXAMPLE,
                    "message": (f"第 {int(f.get('line', 0) or 0)} 行 "
                                f"`guarantees {f.get('expr', '?')}` 不成立"
                                f"{_fn_note}\n"
                                f"  反例：{argtxt}  → ret = {_exh_render(ret)}"),
                })
            _report(name, "counterexample",
                    f"参数域 {domtext} 共 {combos:,} 种组合，跑 "
                    f"{checked:,} 种后找到反例",
                    domain=domtext, combos=combos, checked=checked,
                    skipped=skipped, cex=cex)
            return
        note = (f"（{skipped:,} 种输入不满足 requires，已排除）"
                if skipped else "")
        _report(name, "proved",
                f"参数域 {domtext} 共 {combos:,} 种组合，全部满足 guarantees"
                + note,
                domain=domtext, combos=combos, checked=checked, skipped=skipped)

    for fn in list((getattr(interp, "fns", {}) or {}).values()):
        if getattr(fn, "kind", "") in ("fn", "kernel"):
            _do(fn)
    # **有 guarantees、但没有实现体**的 spec：也要如实报【未证】，不能从
    # 报告里消失（消失会被读成"没问题"）。
    for item in ((interp.decl.children if interp.decl is not None else []) or []):
        if (getattr(item, "kind", "") == "spec" and item.name
                and item.name not in seen and _gt.has_guarantees(item)):
            _report(item.name, "unproven", "没有实现体，无法穷举")
    return reports, counterexamples


def _exh_args_text(params: List[Node], combo) -> str:
    """`a = 200, b = 100` 形式（枚举值按语言写法）。"""
    parts = []
    for prm, val in zip(params, combo):
        parts.append(f"{getattr(prm, 'name', '?')} = {_exh_render(val)}")
    return ", ".join(parts)
