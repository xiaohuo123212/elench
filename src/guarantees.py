#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""guarantees.py —— `guarantees` 子句的**可判定性分类**。

## 现状（2026-11 之前）

`guarantees` 一直是"**能写、没人看**"：`parser.py` 只要求它有表达式，
`checker` 不读、`interp` 不求值、样例里 0 处使用。
这等于给作者留了一个**假的承诺位**——写上去显得严谨，实际上没有任何东西被保证。

## 现在的规则

| 分类 | 判定 | 后果 |
|---|---|---|
| **可检查** | 谓词在 `CHECKABLE` 里，且实参都是可求值的路径 | `check` 报 `I-Guarantee-Runtime`；`run` 在返回后**真的求值**，失败抛 `E-Guarantee-Violated` |
| **不可检查** | 其它形状（未知谓词、对返回值做算术、调用用户函数…） | `check` 报 `E-Guarantee-Uncheckable`（warning），**要求改写或降级成 `assume`** |

**返回值怎么引用**：写 `ret`（也接受 `result`）。`guarantees is_sorted(xs);`
表示"返回后 `xs` 仍有序"；`guarantees ret > 0;` 表示"返回值必须为正"。

## 为什么不做成"全都能检查"

因为那就等于在静态检查里跑运行时求值——对任意谓词、任意实现，`check` 都得真跑一遍，
既慢又可能不终止（函数可能读文件、可能死循环）。**能静态说清的才进检查器，
其余必须显式降级**，这正是"七个可信位"里 #6 的立场。
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

#: 可检查谓词 → 参数个数。这些名字都能在 `stdlib.py` 里找到同名实现，
#: 所以运行时求值不需要另写一套语义。
CHECKABLE = {
    "is_sorted": 1,
    "contains": 2,
}

#: 返回值的两个可接受写法（`ret` 是文档里的写法，`result` 是别名）。
RETURN_NAMES = ("ret", "result")


def _name(n) -> str:
    return ((getattr(n, "name", "") or "").split(".")[-1])


def _unsupported(n) -> Optional[str]:
    """返回 `None` 表示可检查；否则返回"为什么不行"的一句话。"""
    if n is None:
        return "空的 guarantees"
    k = getattr(n, "kind", "")
    if k == "call":
        nm = _name(n)
        if nm not in CHECKABLE:
            return f"未知谓词 `{nm}`（可检查的只有：{', '.join(sorted(CHECKABLE))}）"
        args = next((c for c in n.children if c.kind == "args"), None)
        vals = list(args.children) if args else []
        if len(vals) != CHECKABLE[nm]:
            return f"`{nm}` 需要 {CHECKABLE[nm]} 个实参，写了 {len(vals)} 个"
        for a in vals:
            if a.kind != "path":
                return f"`{nm}` 的实参必须是变量名（写成了 {a.kind}）"
        return None
    if k == "path":
        return None                      # `guarantees ret;` —— 返回值必须为真
    if k in ("num", "lit-int", "lit-bool"):
        return None
    if k == "binary" and (n.value in ("&&", "||")):
        for c in n.children:
            why = _unsupported(c)
            if why:
                return why
        return None
    if k == "binary" and (n.value in ("==", "!=", "<", "<=", ">", ">=")):
        # 比较式：两边必须是"变量名 / 字面量"——`guarantees ret > 0;` 是最常见的后置条件
        for c in n.children:
            if getattr(c, "kind", "") not in ("path", "num", "lit-int", "lit-bool"):
                return f"比较式的两边必须是变量名或字面量（出现了 {c.kind}）"
        return None
    if k in ("unary", "not") and getattr(n, "value", "") in ("!", "not"):
        return _unsupported(n.children[0] if n.children else None)
    return f"不支持的表达式形状 `{k}`"


def classify(spec) -> Tuple[List[Any], List[str]]:
    """把一个 spec 的 `guarantees` 分成 (可检查的表达式, 不可检查的原因)。

    一个函数可以写多条 `guarantees`；每条单独分类。
    """
    ok: List[Any] = []
    bad: List[str] = []
    body = next((c for c in spec.children if c.kind == "spec-body"), None)
    for cl in (body.children if body else []):
        if cl.kind != "clause" or (cl.name or "") != "guarantees":
            continue
        for expr in cl.children:
            why = _unsupported(expr)
            if why:
                bad.append(why)
            else:
                ok.append(expr)
    return ok, bad


def has_guarantees(spec) -> bool:
    body = next((c for c in spec.children if c.kind == "spec-body"), None)
    return any(c.kind == "clause" and (c.name or "") == "guarantees"
               for c in (body.children if body else []))


def describe(exprs: List[Any]) -> str:
    """给消息用的一句话描述。"""
    parts = []
    for e in exprs:
        parts.append(f"{_name(e)}(...)" if getattr(e, "kind", "") == "call" else "…")
    return "；".join(parts)
