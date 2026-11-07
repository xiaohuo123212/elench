#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""ast_compare.py —— 两个 `.tu`/`.eln` 的 AST 是否**等价**（忽略位置）

L-11 的第 3 条验收要用它：`tu → yaml → tu'` 只保证 **AST 等价**（不保证字节）。
"""
import os
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(R, "src"))

from parser import parse_source    # noqa: E402


def shape(node):
    """把 AST 压成只含 kind/name/value/children 的元组树（丢弃行列）。"""
    if node is None:
        return None
    kids = tuple(shape(c) for c in (getattr(node, "children", None) or []) if c is not None)
    return (getattr(node, "kind", "?"), getattr(node, "name", None),
            getattr(node, "value", None), kids)


def ast_equal(path_a: str, path_b: str) -> bool:
    import io
    # **同一个标签**：module 节点的 name 就是文件名，标签不同会造成假差异。
    a = parse_source(io.open(path_a, encoding="utf-8").read(), "m").module
    b = parse_source(io.open(path_b, encoding="utf-8").read(), "m").module
    return shape(a) == shape(b)
