#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""borrow.py —— 借用检查（分支敏感，基于 CFG + 活跃性）

**为什么有这个文件**

我们选了 C++ 后端（决定清单 §10.47）。**Rust 会替我们证借用安全，C++ 不会。**
没有这一层，`const u64& r = xs.at(0); xs.push(1); return r;` 会静默编译通过，
运行时读越界——**那是在交付 UB，比不做工具更糟。**

**算法（分支敏感）**

1. 为函数体建 CFG（`cfg.build`）；
2. 算活跃性不动点（`cfg.liveness`）：`live_in[n]` = 执行到 n 之前"之后还会用到"的变量；
3. 收集借用事件：`const T& r = <以 x 为根的表达式>` → 借用 `(x → r)`；
4. **判定**：若某节点**修改** `x`，且 `r ∈ live_in[该节点]`，则借用仍活着 → 报 `E-Borrow-Conflict`。

> **这就是 Rust MIR borrowck 的思路（liveness-based NLL）。**
> 与"一条时间线"的旧近似相比，它能正确处理**分支**：一支改、另一支不改，照样判得准。

**规则覆盖**（对应规格 T7-a）

| 规则 | 状态 |
|---|---|
| BR-5 结构体不得存引用 | ✅ |
| BR-1 借用期间不得可变借用 | ✅ 分支敏感 |
| BR-2 NLL | ✅ **基于活跃性**（分支敏感，非近似） |
| BR-4 返回引用归属不明 | ✅ |
  | BR-3 方法返回引用默认借自 `self` | ✅ **由 `_base_name` 构造实现**（取表达式里第一个 `path`：`xs.at(0)` → `xs`）。方向保守——`s.field.at(0)` 记成借自 `s`，宁可多报 |

**保守方向**：宁可多报，不可漏报——**漏报就是 UB**。
"""
from typing import List, Optional, Set, Tuple

import cfg as cfgmod
from ast_util import descendants


def _base_name(node) -> Optional[str]:
    """表达式的"被借对象"：取第一个 path（`xs.at(0)` → `xs`）。"""
    for p in descendants(node, "path"):
        if p.name:
            return p.name
    return None


def _check_struct_refs(tu_mod, out) -> None:
    """BR-5：结构体字段不得为引用。"""
    if tu_mod is None:
        return
    for st in descendants(tu_mod, "struct"):
        for f in st.children:
            ty = f.children[0] if f.children else None
            if ty is not None and getattr(ty, "kind", None) == "type-ref":
                out.append((
                    "E-Borrow-StructRef",
                    f"结构体 `{st.name}` 的字段 `{f.name}` 不能是引用"
                    f"（BR-5：v0 不支持生命周期标注，引用存进结构体会逃出作用域）",
                    f.line, f.col,
                ))


def _fn_body(fn):
    return next((c for c in fn.children if getattr(c, "kind", None) == "block"), None)


def _check_fn(fn, out) -> None:
    body = _fn_body(fn)
    if body is None:
        return
    g = cfgmod.build(body)
    live = cfgmod.liveness(g)

    # 语句 → 节点（简单语句一一对应；控制流节点的 st 是 if/while 本身）
    node_of = {id(n.st): n for n in g.nodes.values() if n.st is not None}

    borrows: List[Tuple[str, str, object]] = []      # (被借对象, 引用变量, 声明语句)
    for st in descendants(body, "const-decl"):
        if not st.children or st.name is None:
            continue
        ty = st.children[0]
        if getattr(ty, "kind", None) != "type-ref":
            continue
        base = _base_name(st.children[-1])
        if base:
            borrows.append((base, st.name, st))

    for base, ref_name, st in borrows:
        for m in g.nodes.values():
            if base in m.muts and ref_name in live.get(m.id, set()):
                # 用被修改节点自己的位置报错，读者能直接跳过去
                where = m.st if m.st is not None else st
                out.append((
                    "E-Borrow-Conflict",
                    f"`{base}` 在借用 `{ref_name}` 还活着的时候被修改"
                    f"（BR-1/BR-2：借用期间不得可变借用）",
                    where.line, where.col,
                ))


def _check_ambiguous_returns(ch_mod, out) -> None:
    """BR-4：返回引用但有 ≥2 个引用参数 → 无法唯一确定归属，v0 直接拒绝。"""
    for fn in descendants(ch_mod, "fn"):
        ret = None
        for c in fn.children:
            if getattr(c, "kind", None) == "type-ref":
                ret = c
        if ret is None:
            continue
        params = next((c for c in fn.children if getattr(c, "kind", None) == "params"), None)
        ref_params = [p.name or "?" for p in (params.children if params else [])
                      if p.children and getattr(p.children[0], "kind", None) == "type-ref"]
        if len(ref_params) >= 2:
            out.append((
                "E-Borrow-Ambiguous",
                f"`{fn.name}` 返回引用，但有 {len(ref_params)} 个引用参数"
                f"（{', '.join(ref_params)}）——无法唯一确定借自谁（BR-4）",
                fn.line, fn.col,
            ))


def check_borrows(tu_mod, ch_mod) -> List[Tuple[str, str, int, int]]:
    """分支敏感版本（默认）。"""
    out = []
    _check_struct_refs(tu_mod, out)
    if ch_mod is not None:
        for fn in descendants(ch_mod, "fn"):
            _check_fn(fn, out)
        _check_ambiguous_returns(ch_mod, out)
    return out


# ---------------- 旧近似（保留给差分测试） ----------------

def check_borrows_conservative(tu_mod, ch_mod):
    """**旧版**：按源码顺序压成一条时间线（不分支敏感）。

    保留它是为了**差分测试**：新旧两版对同一段程序给出不同结论时，
    必须逐条归因——"新报旧不报"要么是新抓到的真漏报（好），要么是新误报（坏）。
    """
    out = []
    _check_struct_refs(tu_mod, out)
    if ch_mod is None:
        return out
    for fn in descendants(ch_mod, "fn"):
        body = _fn_body(fn)
        if body is None:
            continue
        stmts = _flatten(body)
        refs = []
        for idx, st in enumerate(stmts):
            if st.kind in ("const-decl", "var-decl") and st.children:
                ty = st.children[0]
                if getattr(ty, "kind", None) == "type-ref" and st.name:
                    base = _base_name(st.children[-1])
                    if base:
                        refs.append((st.name, base, idx))

        def last_use(name, after):
            best = after
            for i in range(after + 1, len(stmts)):
                if any(p.name == name for p in descendants(stmts[i], "path")):
                    best = i
            return best

        for ref_name, base, at in refs:
            end = last_use(ref_name, at)
            if end == at:
                continue
            for i in range(at + 1, end + 1):
                st = stmts[i]
                hit = False
                if st.kind == "assign" and st.children:
                    lhs = st.children[0]
                    hit = getattr(lhs, "kind", None) == "path" and lhs.name == base
                if not hit:
                    for mc in descendants(st, "method-call"):
                        if (mc.children and getattr(mc.children[0], "kind", None) == "path"
                                and mc.children[0].name == base
                                and (mc.name or "") in cfgmod.MUT_METHODS):
                            hit = True
                            break
                if hit:
                    out.append((
                        "E-Borrow-Conflict",
                        f"`{base}` 在借用 `{ref_name}` 还活着的时候被修改",
                        st.line, st.col,
                    ))
    _check_ambiguous_returns(ch_mod, out)
    return out


def _flatten(block) -> list:
    out = []
    for st in block.children:
        out.append(st)
        for b in st.children:
            if getattr(b, "kind", None) == "block":
                out.extend(_flatten(b))
    return out
