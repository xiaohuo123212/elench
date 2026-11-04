#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""requires.py —— **调用点验证**（v0.71 第一步）

## 要解决的问题

`requires` 如果只在运行期断言，它和函数体里的 `if` 没有本质区别：
**都是运行时才发现、都要付检查的钱，还多一个"违约时行为未定义"**。

**只有编译器能在调用点替你验证，`requires` 才值钱。**

## 这一版能证明什么（**只做三种谓词**）

| 谓词形状 | 从哪里得到事实 |
|---|---|
| `length(X) > 0` / `>= N` / `== N` | `if (X.length() > 0) { … }` 的**真分支** |
| `is_sorted(X)` | 前面调过 `sort(X)`；或 `if (is_sorted(X)) { … }` |
| `K > 0` / `K <= N`（标量） | `if (K > 0) { … }` 的真分支 |

**证明不了就报 `E-Require-Unproven`**，并在消息里给出两条修法：

1. **加 `if` 守卫**（编译器就能看见）；
2. **把这项检查放回函数体**（如果它其实是"域内结果"，按规则 1 就该在体里）。

## 为什么这一版不做"签字"

我们语言里的 `assume` **已于 2026-11-04 移除**（它是不可验证的声明），
是**成本侧的信任锚**，会被打进报告（`假设：…`）。
**拿它给 `requires` 签字会把两个语义混在一起**——所以这一版宁可报错，
等真有"确实证不出来但必须放行"的用例，再加一个**独立**的签字形式。

## 不做的

- 跨函数推理（只在本函数体内收集事实）
- 数组内容谓词（如"所有元素 > 0"）
- 否定事实（`else` 分支里的 `<=` 只对最简单形状生效）
"""
from typing import Dict, List, Optional, Tuple

from parser import Node


# ---------------------------------------------------------------- 事实

def _method_chain(n: Node) -> List[str]:
    """`xs.length()` → ["xs", "length"]；`xs.at(i)` → ["at"]（不认识就别猜）。"""
    if n is None or n.kind != "method-call":
        return []
    recv = None
    for c in n.children:
        if c.kind == "path":
            recv = c.name
            break
    return [recv or "", n.name or ""]


def _num(n: Node) -> Optional[int]:
    if n is not None and n.kind == "lit-int":
        try:
            return int(n.value)
        except (TypeError, ValueError):
            return None
    return None


def fact_of(cond: Node) -> Optional[Tuple[str, str, str, int]]:
    """把一个条件变成事实：`(kind, target, op, n)`。

    kind ∈ {"len", "sorted"}；`sorted` 时 op/n 无意义（用 `("==", 1)` 占位）。
    """
    if cond is None:
        return None
    if cond.kind == "call" and (cond.name or "").split(".")[-1] == "is_sorted":
        args = next((c for c in cond.children if c.kind == "args"), None)
        items = args.children if args is not None else []
        if items and items[0].kind == "path":
            return ("sorted", items[0].name or "", "==", 1)
        return None
    if cond.kind != "binary" or len(cond.children) < 2:
        return None
    left, right = cond.children[0], cond.children[1]
    op = (cond.value or {}) if isinstance(cond.value, dict) else cond.value
    if isinstance(op, dict):
        op = op.get("op")
    if isinstance(op, str) and op.startswith("Op."):
        op = op[3:]
    chain = _method_chain(left)
    if len(chain) == 2 and chain[1] == "length":
        num = _num(right)
        if num is not None:
            return ("len", chain[0], op or ">", num)
    if left.kind == "path" and right.kind == "lit-int":
        num = _num(right)
        if num is not None:
            return ("scalar", left.name or "", op or ">", num)
    return None


# ---------------------------------------------------------------- 收集

def _calls_in(node: Node) -> List[Node]:
    out = []
    stack = [node]
    while stack:
        x = stack.pop()
        if x is None:
            continue
        if x.kind == "call":
            out.append(x)
        stack.extend(getattr(x, "children", []) or [])
    return out


def _bound(lo, hi, op: str, num: int):
    """把 `X op num` 折成区间下界/上界。"""
    if op == ">":
        lo = num + 1 if lo is None else max(lo, num + 1)
    elif op == ">=":
        lo = num if lo is None else max(lo, num)
    elif op == "<":
        hi = num - 1 if hi is None else min(hi, num - 1)
    elif op == "<=":
        hi = num if hi is None else min(hi, num)
    elif op == "==":
        lo = num if lo is None else max(lo, num)
        hi = num if hi is None else min(hi, num)
    return lo, hi


def _walk(block: Node, facts: Dict[Tuple[str, str], tuple], sorted_set: set,
          out: List[Tuple[Node, Dict[Tuple[str, str], tuple], set]]) -> None:
    """按语句顺序收集事实，并在每个调用点记录"当时知道什么"。"""
    for st in getattr(block, "children", []) or []:
        if st.kind == "if":
            cond = st.children[0] if st.children else None
            f = fact_of(cond)
            body = next((c for c in st.children if c.kind == "block"), None)
            if f is not None and body is not None:
                kind, target, op, num = f
                if kind == "sorted":
                    _walk(body, facts, sorted_set | {target}, out)
                else:
                    inner = dict(facts)
                    lo, hi = inner.get((kind, target), (None, None))
                    inner[(kind, target)] = _bound(lo, hi, op, num)
                    _walk(body, inner, set(sorted_set), out)
            continue
        if st.kind == "expr-stmt" and st.children:
            c = st.children[0]
            # `sort(xs);` 之后 xs 就是有序的（内建的后置条件）
            if c.kind == "call" and (c.name or "").split(".")[-1] in ("sort", "sort_by"):
                args = next((x for x in c.children if x.kind == "args"), None)
                items = args.children if args is not None else []
                if items and items[0].kind == "path":
                    sorted_set = sorted_set | {items[0].name}
        for c in _calls_in(st):
            out.append((c, dict(facts), set(sorted_set)))


# ---------------------------------------------------------------- 判定

def _provable(pred: Tuple[str, str, str, int],
              args: List[Node],
              facts: Dict[Tuple[str, str], int],
              sorted_set: set,
              param_index: Dict[str, int]) -> bool:
    kind, target, op, num = pred
    if kind == "sorted":
        return target in sorted_set
    # 形参名 → 实参名（只支持实参就是变量名的情形）
    if target in param_index:
        idx = param_index[target]
        if idx >= len(args) or args[idx].kind != "path":
            return False
        actual = args[idx].name or ""
    else:
        actual = target
    lo, hi = facts.get((kind, actual), (None, None))
    if op == ">":
        return lo is not None and lo > num
    if op == ">=":
        return lo is not None and lo >= num
    if op == "<":
        return hi is not None and hi < num
    if op == "<=":
        return hi is not None and hi <= num
    if op == "==":
        return lo is not None and hi is not None and lo == hi == num
    return False


def check(tu_mod, ch_mod) -> List:
    """返回 `[(code, message, line, col), …]`。"""
    out: List = []
    if ch_mod is None or tu_mod is None:
        return out
    specs = {c.name: c for c in tu_mod.children if c.kind == "spec"}
    fns = {c.name: c for c in ch_mod.children if c.kind == "fn"}

    requires_by_fn: Dict[str, List[Tuple[str, str, str, int]]] = {}
    for name, spec in specs.items():
        preds = []
        for holder in spec.children:
            if holder.kind != "spec-body":
                continue
            for cl in holder.children:
                if cl.kind == "clause" and cl.name == "requires":
                    for expr in cl.children:
                        f = fact_of(expr)
                        if f is not None:
                            preds.append(f)
        requires_by_fn[name] = preds

    for fname, fn in fns.items():
        body = next((c for c in fn.children if c.kind == "block"), None)
        if body is None:
            continue
        sites: List[Tuple[Node, Dict, set]] = []
        _walk(body, {}, set(), sites)
        for call, facts, sorted_set in sites:
            callee = (call.name or "").split(".")[-1]
            preds = requires_by_fn.get(callee) or []
            if not preds:
                continue
            params = next((c for c in fns[callee].children if c.kind == "params"), None)
            pnames = [p.name for p in (params.children if params else [])]
            pidx = {n: i for i, n in enumerate(pnames)}
            args = next((c for c in call.children if c.kind == "args"), None)
            items = list(args.children) if args is not None else []
            for pred in preds:
                if not _provable(pred, items, facts, sorted_set, pidx):
                    shape = (f"{pred[1]}.length() {pred[2]} {pred[3]}"
                             if pred[0] == "len" else
                             f"is_sorted({pred[1]})" if pred[0] == "sorted" else
                             f"{pred[1]} {pred[2]} {pred[3]}")
                    out.append((
                        "E-Require-Unproven",
                        f"{fname} 调用 {callee} 前必须保证 `{shape}`，但检查器证明不了；"
                        f"修法：①在前面加 `if` 守卫；②或把这项检查放回 {callee} 的函数体",
                        call.line, call.col))
    return out
