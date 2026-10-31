#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

"""ast_util.py —— 共享 AST 工具（主批 A–D Step 3）

**为什么有这个文件**：`_descendants` 原先在 **四个地方**各写了一遍
（`bound.py`、`cost_interp.py`、`interp.py`、`isolated.py`），
其中三份完全一样、一份（`bound.py`）少一个 `None` 保护。
重复实现的问题不是"难看"，是**改一处忘一处**——这正是 A–D Step 2 抓到的漂移类型。

**这个文件只放"纯 AST 工具"**：不涉及任何值域语义，两个值域都能用。
"""
from typing import Any, List, Optional


def descendants(n: Optional[Any], kind: str, acc: Optional[List] = None) -> List:
    """递归收集某类节点（含 `n` 自身）。

    `None` 安全：传 `None` 返回空列表（四个旧实现里有三个是这样，
    `bound.py` 那份没有保护——统一到安全版本，因为**更严格的那份是错的**）。
    """
    if acc is None:
        acc = []
    if n is None:
        return acc
    if getattr(n, "kind", None) == kind:
        acc.append(n)
    for c in getattr(n, "children", ()) or ():
        descendants(c, kind, acc)
    return acc


def const_int_value(c: Optional[Any]) -> Optional[int]:
    """从 `const NAME: T = <整数>;` 里取出整数；取不到返回 `None`。"""
    if c is None:
        return None
    for ch in getattr(c, "children", ()) or ():
        if getattr(ch, "kind", None) == "lit-int":
            try:
                return int(ch.value)
            except (TypeError, ValueError):
                return None
    return None
