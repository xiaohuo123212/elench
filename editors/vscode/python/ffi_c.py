#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""ffi_c.py —— FFI 的 **C ABI 后端**：只负责"合法值 → ctypes 参数"。

校验在 `shape.py`（与后端无关）。这里只做**编组**：

| `.tu` 类型 | ctypes |
|---|---|
| `u64` | `c_uint64` |
| `i64` | `c_int64` |
| `bool` | `c_uint64`（C 没有 bool 的稳定 ABI） |
| `Vec<u8>` / `[u8]` | `c_char_p`（`bytes` 直接传） |

认不出的类型抛 `E-FFI-Unsupported`——**这是后端能力问题，不是调用者写错**，
所以不能报成 `E-FFI-Shape`（否则人会去改一个没写错的调用）。

## 多语言怎么办

**加一个后端文件，不动校验器**：`ffi_py.py` / `ffi_wasm.py` 各提供同样的
`argtype_for` / `restype_for` / `to_native`。设计见
`文档/设计/目前设计/未做/挂起/挂起-多语言FFI编组.md`。
"""
from __future__ import annotations

from typenames import canon, display
import ctypes
from typing import Any, List

from shape import UnsupportedError


def argtype_for(ty: str):
    """`.tu` 声明类型 → ctypes 参数类型。"""
    _head = (ty or "").split("<")[0].split("&")[0].strip()
    if canon(_head) in ("vector", "array") or ty.startswith("["):
        return ctypes.c_char_p
    if ty == "i64":
        return ctypes.c_int64
    if ty in ("u64", "bool"):
        return ctypes.c_uint64
    raise UnsupportedError(f"C ABI 后端不支持参数类型 {ty}")


def restype_for(ty: str):
    """`.tu` 返回类型 → ctypes 返回类型（空表示 void）。"""
    if not ty:
        return None
    if ty == "i64":
        return ctypes.c_int64
    if ty in ("u64", "bool"):
        return ctypes.c_uint64
    raise UnsupportedError(f"C ABI 后端不支持返回类型 {ty}")


def to_native(args: List[Any]) -> List[Any]:
    """把**已通过校验**的值编组成 ctypes 能吃的形状。"""
    out = []
    for a in args:
        if isinstance(a, (bytes, bytearray)):
            out.append(bytes(a))
        elif isinstance(a, str):
            out.append(a.encode("utf-8"))
        elif isinstance(a, (list, tuple)):
            out.append(bytes(int(x) & 0xFF for x in a))
        elif isinstance(a, bool):
            out.append(int(a))
        else:
            out.append(int(a))
    return out
