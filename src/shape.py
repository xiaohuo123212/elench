#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""shape.py —— FFI 调用前的**形状校验**（与后端语言无关）。

## 为什么需要它

`extern` 已经能声明、能调用、能记成本。但**参数对不对，以前完全交给 ctypes**：

| 你写的 | 以前 |
|---|---|
| `strlen(1, 2)` | ctypes 抛 `TypeError: this function takes 1 argument` —— 能读，但不是我们的错误码 |
| `strlen(123)` | ctypes 抛 `TypeError: bytes or integer address expected` |
| 声明 `u64` 却传 `-1` | **静默变成 18446744073709551615** |

最后一条是真正的洞：**不报错、悄悄变成另一个数**。上界再严，参数一歪就全错。

## 为什么校验器与后端无关

校验检查的是"实参是否符合 **`.tu` 声明的类型**"，与后面接的是 C、Python 还是 WASM
**没有关系**。所以：

```
shape.validate(...)   ← 只此一份
  └─ ffi_c.marshal(...)  ← 每个后端一张表
```

**接一个新语言 = 加一个编组表，不动校验器。** 多后端编组的设计见
`文档/设计/目前设计/未做/挂起/挂起-多语言FFI编组.md`；本轮只做 C ABI。

## 两种失败必须分开

| 错误码 | 含义 | 谁来修 |
|---|---|---|
| `E-FFI-Shape` | **调用者写错了**（个数/类型/取值） | 改调用点 |
| `E-FFI-Unsupported` | **后端不支持这个类型**（不是写错） | 换类型或换后端 |

混在一起，人会去改一个没写错的调用。
"""
from __future__ import annotations
import re

from typenames import canon, display

from typing import Any, List, Sequence, Tuple

U64_MAX = 2 ** 64 - 1


class ShapeError(Exception):
    """形状不对：个数 / 类型 / 取值。"""

    code = "E-FFI-Shape"

    def __init__(self, message: str):
        super().__init__(f"{self.code}：{message}")


class UnsupportedError(Exception):
    """后端不支持这个声明类型（**不是调用者写错**）。"""

    code = "E-FFI-Unsupported"

    def __init__(self, message: str):
        super().__init__(f"{self.code}：{message}")


def _is_vec(t: str) -> bool:
    # **名字后面可能跟着 `<…>` 和 `&`**（`Vec<u8>&`），所以不能直接
    # `canon(t)`——只认整词会漏掉带修饰的写法。先取头部再规范化。
    _head = (t or "").split("<")[0].split("&")[0].strip()
    return canon(_head) in ("vector", "array") or t.startswith("[")


def _check_one(fn: str, i: int, ty: str, v: Any) -> None:
    """第 i 个实参（0 基）是否符合声明类型。"""
    where = f"{fn} 的第 {i + 1} 个参数（声明 {ty}）"
    if _is_vec(ty):
        # **定容 `array<T, N>` 的长度是静态可查的**：声明里就有 N。
        # `vector<T>` 没有声明长度，**查不了**——这一条要写进文档，
        # 免得有人以为"长度校验"对所有容器都做了。
        _m = re.match(r"\s*array\s*<[^,>]+,\s*(\d+)\s*>", ty or "")
        if _m:
            _want = int(_m.group(1))
            try:
                _got = len(v) if not isinstance(v, str) else len(v.encode("utf-8"))
            except TypeError:
                raise ShapeError(f"{where}期望长度为 {_want} 的定容数组，实际是 {type(v).__name__}")
            if _got != _want:
                raise ShapeError(f"{where}长度必须是 {_want}（定容数组），实际 {_got}")
            if not isinstance(v, (bytes, bytearray, str)):
                if not (isinstance(v, (list, tuple)) and all(isinstance(x, int) for x in v)):
                    raise ShapeError(f"{where}期望字节串或整数列表，实际是 {type(v).__name__}")
            return
        if isinstance(v, (bytes, bytearray, str)):
            return
        if isinstance(v, (list, tuple)) and all(isinstance(x, int) for x in v):
            return
        raise ShapeError(f"{where}期望字节串（Vec<u8>）或整数列表，实际是 {type(v).__name__}")
    if ty in ("u64",):
        if not isinstance(v, int):
            raise ShapeError(f"{where}期望整数，实际是 {type(v).__name__}")
        if v < 0:
            raise ShapeError(f"{where}不能为负（u64 是无符号；负数会被 ctypes "
                             f"静默变成 {U64_MAX + 1 + v}）")
        if v > U64_MAX:
            raise ShapeError(f"{where}超出 u64 范围（最大 {U64_MAX}），实际 {v}")
        return
    if ty in ("i64",):
        if not isinstance(v, int):
            raise ShapeError(f"{where}期望整数，实际是 {type(v).__name__}")
        return
    if ty in ("bool",):
        if not isinstance(v, (bool, int)):
            raise ShapeError(f"{where}期望布尔，实际是 {type(v).__name__}")
        return
    if ty in ("float", "f64", "f32"):
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ShapeError(f"{where}期望浮点数，实际是 {type(v).__name__}")
        return
    raise UnsupportedError(f"后端不认识声明类型 {ty}（{where}）")


def validate(fn: str, params: Sequence[Tuple[str, str]], args: Sequence[Any]) -> List[Any]:
    """按 `.tu` 签名检查实参；通过就原样返回（编组交给后端）。"""
    if len(args) != len(params):
        raise ShapeError(f"{fn} 期望 {len(params)} 个参数，实际给了 {len(args)} 个")
    for i, ((_pname, ty), v) in enumerate(zip(params, args)):
        _check_one(fn, i, ty, v)
    return list(args)
