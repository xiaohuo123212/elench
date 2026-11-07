#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""emit.py —— **一种输出，三种格式**：按扩展名选渲染器。

## 为什么要单独一个文件

`--out` 以前只在已删除的 `tightness` 里有，而且是内联的 `if endswith(".json")` 链。
每个命令各写一遍，就是又一个"两套真值源"——改一处、漏一处。

## 约定

- 扩展名决定格式：`.md` / `.json` / `.csv`，其它一律按 Markdown；
- 没给 `out` 就打到 stdout；
- 显式 `--format` 优先于扩展名（调用方传 `fmt`）。
"""
from __future__ import annotations

import io
import json
from typing import Any, Dict, List, Optional


def _csv(rows: List[Dict[str, Any]], cols: List[str]) -> str:
    buf = [",".join(cols)]
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c, "")
            if isinstance(v, float):
                v = "%.4f" % v
            cells.append(str(v))
        buf.append(",".join(cells))
    return "\n".join(buf) + "\n"


def emit(md: str, rows: List[Dict[str, Any]], out: Optional[str] = None,
         fmt: Optional[str] = None, cols: Optional[List[str]] = None,
         meta: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """把 `md` / `rows` 按 `out` 的扩展名（或 `fmt`）写出；没给 out 就打印 md。"""
    if not out:
        print(md, end="")
        return None
    low = (fmt or out.rsplit(".", 1)[-1]).lower()
    if low == "json":
        payload = dict(meta or {})
        payload["rows"] = rows
        io.open(out, "w", encoding="utf-8", newline="\n").write(
            json.dumps(payload, ensure_ascii=False, indent=2))
    elif low == "csv":
        io.open(out, "w", encoding="utf-8", newline="\n").write(
            _csv(rows, cols or (list(rows[0]) if rows else [])))
    else:
        io.open(out, "w", encoding="utf-8", newline="\n").write(md)
    print(f"已写出：{out}")
    return out
