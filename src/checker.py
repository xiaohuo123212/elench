#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
checker.py —— M0 检查器

M0 能查的（不含成本分析）：
  F-1  每个实现函数 MUST 有对应的 spec
  F-2  同名函数的签名 MUST 一致
  F-4  `.eln` MUST NOT 有声明（const/enum/struct/trait/impl/spec/choose）
  F-8  `.eln` MUST NOT 有 use
  F-9  kernel MUST NOT 被 `?!` 之外的地方直接调用
  B-2  `?!label` 的 label MUST 已在 `.tu` 声明
  B-8  over 元素数 MUST 在 1..4
  B-9  自指 / B-9-a 扩展调用图无环
  B-10 over MUST 引用已声明的 kernel
  B-15 `?!` MUST NOT 构成整个函数体
  B-16 pending 与实际缺体 MUST 双向一致

M0 **不**能查的（需要成本分析，属 M2）：
  · cost 的计算与 `target` 的比较 → 状态 `done` / `debt` 无法判定
  · effects 的验证
  · requires / guarantees 的验证
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import stdlib
from parser import Node, parse_source


# ---------------------------------------------------------------- 诊断

@dataclass
class Restart:
    """**修法的一条具体动作**（Common Lisp 里 restart 的思路）。

    以前诊断只给一句 `修法：…` 文本——读者还得自己翻译成命令、自己敲。
    restart 把"能做什么"结构化成**可执行的一条**，`--apply=N` 就能直接跑。

    **硬规则：任何 restart 都不许修改 `.tu` 里的 `target`。**
    契约是持久的，实现是一次性的；"不达标就改承诺"这条路必须不存在
    （`test_restarts.py` 有一条断言专门守这个）。
    """
    kind: str                    # "command"（能执行）| "edit"（只打印 diff）| "info"（只说明）
    label: str                   # 一句话：如「重测并生成分档表」
    argv: List[str] = field(default_factory=list)      # kind=command
    patch: Dict[str, Any] = field(default_factory=dict)  # kind=edit：{file,line,text}

    def to_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label,
                "argv": list(self.argv), "patch": dict(self.patch)}


@dataclass
class Diag:
    code: str
    message: str
    line: int = 0
    col: int = 0
    start: int = 0
    end: int = 0
    severity: str = "error"
    actions: List[Restart] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "line": self.line, "col": self.col,
                "start": self.start, "end": self.end,
                "severity": self.severity,
                "actions": [a.to_dict() for a in self.actions]}


# ---------------------------------------------------------------- 结果

@dataclass
class FnInfo:
    name: str
    kind: str                    # "fn" | "kernel"
    has_impl: bool
    has_target: bool
    is_pub: bool = False
    line: int = 0
    state: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        """对外输出用完整单词：`has_implementation` 而不是 `has_impl`。"""
        return {
            "name": self.name,
            "kind": self.kind,
            "has_implementation": self.has_impl,
            "has_target": self.has_target,
            "is_public": self.is_pub,
            "line": self.line,
            "state": self.state,
            "note": self.note,
        }


@dataclass
class CheckResult:
    module: str = ""
    functions: List[FnInfo] = field(default_factory=list)
    diagnostics: List[Diag] = field(default_factory=list)
    choices: Dict[str, dict] = field(default_factory=dict)
    candidates: Dict[str, Dict[str, Node]] = field(default_factory=dict)
    labels_used: Set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        # v0.68：只有 error 级诊断才算失败；warning 级（如 T13 宽松模式的
        # E-While-NoBound）只提示，不阻断。
        return not any(d.severity == "error" for d in self.diagnostics)

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "ok": self.ok,
            "functions": [f.to_dict() for f in self.functions],
            "choices": self.choices,
            "labels_used": sorted(self.labels_used),
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }


# ---------------------------------------------------------------- 工具

def _spec_body(spec: Node) -> Optional[Node]:
    for c in spec.children:
        if c.kind == "spec-body":
            return c
    return None


def _clauses(spec: Node) -> List[Node]:
    body = _spec_body(spec)
    return [x for x in body.children if x.kind == "clause"] if body else []


def _has_target(spec: Node) -> bool:
    return any(c.name == "target" for c in _clauses(spec))


def _params_of(node: Node) -> Optional[Node]:
    for c in node.children:
        if c.kind == "params":
            return c
    return None


def _return_of(node: Node) -> Optional[Node]:
    """返回类型节点（params 之后的第一个 type-* 或 type-name）。"""
    seen_params = False
    for c in node.children:
        if c.kind == "params":
            seen_params = True
            continue
        if c.kind in ("block", "spec-body"):
            return None
        if seen_params and c.kind.startswith("type"):
            return c
    return None


def type_str(t: Optional[Node]) -> str:
    if t is None:
        return ""
    if t.kind == "type-name":
        args = ", ".join(type_str(c) for c in t.children)
        return f"{t.name}<{args}>" if args else str(t.name)
    if t.kind == "type-const":
        return str(t.value)
    if t.kind == "type-args":
        return ", ".join(type_str(c) for c in t.children)
    if t.kind == "type-slice":
        return "[]" + type_str(t.children[0])
    if t.kind == "type-tuple":
        return "(" + ", ".join(type_str(c) for c in t.children) + ")"
    if t.kind == "type-ref":
        return type_str(t.children[0]) + "&"
    return "?"


def param_list(node: Node) -> List[Tuple[str, str]]:
    """[(名字或空, 类型字符串)]"""
    p = _params_of(node)
    if p is None:
        return []
    out = []
    for c in p.children:
        if c.kind == "param":
            out.append((c.name or "", type_str(c.children[0]) if c.children else "?"))
        else:
            out.append(("", type_str(c)))
    return out


def walk(node: Node, kind: str, acc=None) -> List[Node]:
    if acc is None:
        acc = []
    if node.kind == kind:
        acc.append(node)
    for c in node.children:
        walk(c, kind, acc)
    return acc


def callee_name(call: Node) -> Optional[str]:
    """`call` 的第一个孩子是被调者；只认简单的 path 形式。"""
    if not call.children:
        return None
    head = call.children[0]
    if head.kind == "path":
        return head.name
    if head.kind == "field":
        # a.b(...) 形式：取最后一段
        return head.name
    return None


# ---------------------------------------------------------------- 状态

def derive_state(has_impl: bool, has_target: bool) -> Tuple[str, str]:
    if not has_impl and not has_target:
        return "draft", "只有签名"
    if not has_impl and has_target:
        return "planned", "意图已定、实现待写"
    if has_impl and not has_target:
        return "unchecked", "有实现但无 target"
    return "?", "done / debt 由 elench cost 判定"


# ---------------------------------------------------------------- 主检查

# ---------------------------------------------------------------- T13

SIZE_CALLS = ("length", "size", "capacity", "len")


def _is_size_expr(n: Node, const_sizes: set = frozenset()) -> bool:
    """这个表达式是不是「尺寸量」。

    认三种：
      · 尺寸方法调用：`xs.length()` / `s.size()` …
      · 形参名（切片/容器的长度在进入函数时就定了）
      · 由 `const` 绑定到尺寸的变量名（如 `const u64 n = xs.length();`）
    不认：`var` 绑定的变量（循环里可能被改，需要数据流分析——按设计不做）。
    """
    if n is None:
        return False
    if n.kind in ("method-call", "call"):
        return (n.name or "").split(".")[-1] in SIZE_CALLS
    if n.kind == "path":
        return (n.name or "") in const_sizes
    if n.kind == "binary" and (n.value or "") in ("+", "-"):
        # `xs.length() + 1` / `xs.length() - 1` —— **常量偏移的尺寸量仍是尺寸量**。
        # 否则 `const u64 width = b.length() + 1; while (i < width)` 会被拒，
        # 而「尺寸 + 1」是二维表最自然的写法（LCS 的 width 就是它）。
        a, b = (n.children + [None, None])[:2]
        if _is_size_expr(a, const_sizes) and _is_int_literal(b):
            return True
        if _is_size_expr(b, const_sizes) and _is_int_literal(a):
            return True
        return False
    return False


def _is_int_literal(n: Optional[Node]) -> bool:
    """`3` / `-3` —— 整数常量。"""
    if n is None:
        return False
    if n.kind == "lit-int":
        return True
    if n.kind in ("unary", "step") and (n.value or "") in ("-", "+") and n.children:
        return _is_int_literal(n.children[0])
    return False


def _collect_const_sizes(fn: Node) -> set:
    """收集本函数里「由 const 绑定到尺寸量」的变量名。

    只看 `const T n = <尺寸表达式>;` 这一种形态——这是一次局部绑定，
    不是数据流分析：它的值在整个作用域内不变（const 语义保证）。
    """
    names = set()
    for d in walk(fn, "const-decl"):
        if len(d.children) >= 2 and _is_size_expr(d.children[1], frozenset()):
            names.add(d.name)
    return names


#: 整数形参类型。**标量整数形参也算尺寸**——
#: 它在函数入口即固定，`while (i < dim)` 的迭代次数就是 `dim`，
#: 是一个**可写出来、可辩护**的上界。T13 要的是"上界可写"，不是"上界小"。
INT_PARAM_TYPES = frozenset({
    "u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64",
})


#: 当前模块的模块级常量名（由 check_module 在入口填）
_MODULE_CONST_SIZES: set = set()


def _collect_param_sizes(fn: Node) -> set:
    """可作为 `while` 上界的形参名。

    两类：
      1. 容器参数（`Vec` / `FixedVec`）——上界是它的 `length()`
      2. **整数标量参数**——上界就是它本身（v0.69 扩）
    """
    names = set()
    for pm in walk(fn, "param"):
        if not pm.children:
            continue
        ty = pm.children[0]
        if ty.kind in ("type-ref", "type-name", "type-slice"):
            base = ty.children[0] if (ty.kind == "type-ref" and ty.children) else ty
            if base.kind == "type-name":
                nm = base.name or ""
                if nm in ("Vec", "FixedVec") or nm in INT_PARAM_TYPES:
                    names.add(pm.name)
    return names


def _monotone_decreasing_names(fn: Node) -> set:
    """本函数里「只做减法」的变量名（`while (j > 0)` 可以用它们当上界）。"""
    from bound import monotone_decreasing
    return set(monotone_decreasing(fn))


def _dec_loop_can_bound(cond: Optional[Node], dec_vars: set) -> bool:
    """`while (j > 0)` / `while (j >= 1)` 且 j 单调递减 → 可推（上界 = 初值）。"""
    if cond is None or not dec_vars:
        return False
    if cond.kind != "binary" or (cond.value or "") not in (">", ">="):
        return False
    left = cond.children[0] if cond.children else None
    return (left is not None and left.kind == "path"
            and (left.name or "") in dec_vars)


def _cond_can_bound(cond: Node, const_sizes: set = frozenset()) -> bool:
    """T13 来源 ①：条件形态里含尺寸比较。

    支持：
      i < xs.length()      （任一侧是尺寸调用）
      a < b and c < d      （and 组合，任一可推即算可推）
      not P                 → 不能靠形态推，交给 ②
    """
    if cond is None:
        return False
    if cond.kind == "binary":
        op = cond.value or ""
        if op in ("and", "or"):
            return any(_cond_can_bound(c, const_sizes) for c in cond.children)
        if op in ("<", "<=", ">", ">="):
            if any(_is_size_expr(c, const_sizes) for c in cond.children):
                return True
            # **常量上界**：`while (k < 3000)` / `while (k < LIMIT)`（LIMIT 是 const）
            # 也算可推——`bound_of_condition` 已经会把它算成 const 界，
            # 检查器不该在这里唱反调。
            for c in cond.children:
                if getattr(c, "kind", "") == "lit-int":
                    return True
                if getattr(c, "kind", "") == "path" and c.name and c.name.isupper():
                    return True
            return False

        return False
        return False
    if cond.kind == "unary" and (cond.value or "") == "not":
        return False
    return False


def _pred_of(cond: Node):
    """T13 来源 ②：条件是不是一个谓词调用；返回 (谓词名, 节点) 或 (None, None)。"""
    if cond is None:
        return None, None
    if cond.kind == "call" and cond.name:
        return cond.name.split(".")[-1], cond
    return None, None


# ---------------------------------------------------------------- benchmodel

MAX_SCALE_POINTS = 64


def _eval_range_points(node: Node, consts=None):
    """把 `range(起, 止[, 步长])` 求成点列表；不合法返回 (None, 错误说明)。

    `consts` 是模块级整数常量表（`const MAX_TOKENS: u64 = 4096;`），
    所以 `range(1, MAX_TOKENS, *2)` 合法——常量在编译期已知，不影响确定性。
    """
    if node is None or node.kind != "call":
        return None, "scale 里 MUST 写 `range(起, 止[, 步长])`"
    args = next((c for c in node.children if c.kind == "args"), None)
    if args is None or len(args.children) < 2:
        return None, "range 至少要有起、止两个参数"

    def int_of(n):
        if n.kind == "lit-int":
            try:
                return int(n.value)
            except (TypeError, ValueError):
                return None
        # `-10` 会被 parse_range_args 包成 `step v=-`（因为它把首字符当步长符号），
        # 也可能是 unary('-')。两种都要认，否则报错说不清原因。
        if n.kind in ("step", "unary") and (n.value or "") in ("-", "+") and n.children:
            inner = int_of(n.children[0])
            if inner is None:
                return None
            return -inner if (n.value or "") == "-" else inner
        if n.kind == "path" and consts is not None:
            return consts.get(n.name)
        return None

    lo = int_of(args.children[0])
    hi = int_of(args.children[1])
    if lo is None or hi is None:
        return None, "range 的起止 MUST 是整数字面量"

    step_node = None
    for c in args.children[2:]:
        if c.kind == "step":
            step_node = c
    if step_node is None:
        step_op, step_val = "+", 1
    else:
        step_op = step_node.value or "+"
        step_val = int_of(step_node.children[0])
        if step_val is None:
            return None, "range 的步长 MUST 是整数字面量（暂不支持变量）"

    if lo < 0 or hi < 0:
        return None, "尺寸 MUST ≥ 0（尺寸是长度，不可能为负）"
    if lo > hi:
        return None, "range 的起点 MUST NOT 大于终点"

    pts = []
    if step_op == "*":
        if step_val <= 1:
            return None, f"步长 `*{step_val}` 违反「序列严格递增」（k MUST > 1）"
        # 起点 0：0×k 永远是 0，按字面会得到 0,0,0… → 必须单独处理。
        # 规则（规格 §4.7.2）：序列为 0, 1, k, k², …
        if lo == 0:
            pts.append(0)
            cur = 1
        else:
            cur = lo
        while cur <= hi and len(pts) <= MAX_SCALE_POINTS:
            pts.append(cur)
            cur = cur * step_val
    elif step_op == "+":
        if step_val < 1:
            return None, f"步长 `+{step_val}` 违反「序列严格递增」（k MUST ≥ 1）"
        cur = lo
        while cur <= hi and len(pts) <= MAX_SCALE_POINTS:
            pts.append(cur)
            cur = cur + step_val
    else:
        return None, f"步长运算符只能是 `*` 或 `+`，实际是 {step_op!r}"

    if len(pts) > MAX_SCALE_POINTS:
        return None, f"scale 点数超过上限 {MAX_SCALE_POINTS}"
    return pts, None


#: 内置生成器（标准库函数）。MB-7 要求生成器是普通函数——
#: 这几个的 `spec` / `cost` / `effects` 由标准库提供，**用户不需要在自己 `.tu` 里重写**。
#: 其余名字 MUST 在用户的 `.tu` 里有 `spec`，否则报 `E-Bench-BadGenerator`。
BUILTIN_GENERATORS = frozenset({
    "sorted_u64s", "random_u64s", "make_u8s", "make_texts", "any_of",
})


def _check_benchmodel(res, specs, choosers, impls, consts=None) -> None:
    """MB-1..MB-12（§4.7.3）。"""
    for name, sp in specs.items():
        body = _spec_body(sp)
        if body is None:
            continue
        bm = None
        for c in body.children:
            if c.kind == "benchmodel":
                if bm is not None:
                    res.diagnostics.append(Diag(
                        "E-Bench-Duplicate",
                        f"spec {name} 只能有一个 benchmodel（v0.69）", c.line, c.col))
                bm = c
            elif c.kind == "clause" and (c.name or "") == "model":
                res.diagnostics.append(Diag(
                    "E-Clause-OldModel",
                    "`model` 已下线（v0.69）：改用 `benchmodel { data { … } scale { … } }`",
                    c.line, c.col))

        has_choice = any(v.get("host") == name for v in choosers.values())

        if bm is None:
            if has_choice:
                res.diagnostics.append(Diag(
                    "E-Bench-NoModel",
                    f"spec {name} 里有 `?!`，MUST 写 `benchmodel`（要测，就得说测什么数据、哪些规模）",
                    sp.line, sp.col))
            continue
        if not has_choice:
            res.diagnostics.append(Diag(
                "W-Bench-Unused",
                f"spec {name} 没有 `?!`，benchmodel 不会被用到",
                bm.line, bm.col, severity="warning"))

        # ---- 形参名
        params = {c.name for c in (_params_of(sp).children if _params_of(sp) else [])}

        # ---- data 里的参数
        data = next((c for c in bm.children if c.kind == "bench-data"), None)
        scale = next((c for c in bm.children if c.kind == "bench-scale"), None)
        in_data = set()
        if data is not None:
            for b in data.children:
                if b.kind == "binding":
                    in_data.add(b.name)
                    if b.name not in params:
                        res.diagnostics.append(Diag(
                            "E-Bench-UnknownParam",
                            f"data 里的 {b.name} 不是 spec 的形参", b.line, b.col))

        # ---- MB-13：data 的声明顺序 MUST 等于签名形参顺序
        #      （顺序错会静默把实参错位——实测跑错东西，且不报错）
        if data is not None:
            sig_order = [c.name for c in
                         (_params_of(sp).children if _params_of(sp) else [])]
            got_order = [b.name for b in data.children if b.kind == "binding"]
            common = [x for x in got_order if x in sig_order]
            expect = [x for x in sig_order if x in got_order]
            if common != expect:
                res.diagnostics.append(Diag(
                    "E-Bench-OrderMismatch",
                    f"data 的顺序 MUST 与签名形参顺序一致："
                    f"签名是 `{', '.join(sig_order)}`，data 写成了 `{', '.join(got_order)}`"
                    f"（顺序错会静默把实参错位）",
                    data.line, data.col))

        # ---- MB-7 / E-Bench-BadGenerator：生成器 MUST 是普通函数（有 spec）
        if data is not None:
            for b in data.children:
                if b.kind != "binding" or not b.children:
                    continue
                callee = b.children[0]
                gname = None
                if callee.kind == "call":
                    gname = callee.name
                elif callee.kind == "path":
                    gname = callee.name
                if not gname:
                    continue
                # 内置生成器（标准库）也有 spec，只是不在用户的 .tu 里——免于本检查
                if gname in BUILTIN_GENERATORS:
                    continue
                if gname not in specs:
                    res.diagnostics.append(Diag(
                        "E-Bench-BadGenerator",
                        f"生成器 `{gname}` 不是一个已声明的函数（MB-7 要求它有 "
                        f"`spec`）；拼错名字或忘了声明 spec",
                        b.line, b.col))

        # ---- MB-1：每个形参都要有 data
        for pn in sorted(params - in_data):
            res.diagnostics.append(Diag(
                "E-Bench-MissingParam",
                f"data 缺少形参 {pn} 的生成方式（每个参数都要说值从哪来）",
                bm.line, bm.col))

        # ---- scale
        scaled: Set[str] = set()
        if scale is not None:
            for b in scale.children:
                if b.kind != "scale-binding":
                    continue
                scaled.add(b.name)
                if b.name not in in_data:
                    res.diagnostics.append(Diag(
                        "E-Bench-UnknownParam",
                        f"scale 里的 {b.name} 没有在 data 中出现", b.line, b.col))
                pts, err = _eval_range_points(b.children[0] if b.children else None, consts)
                if err is not None:
                    res.diagnostics.append(Diag(
                        "E-Bench-BadStep", f"{b.name} 的规模写法有问题：{err}",
                        b.line, b.col))
                elif len(pts) < 2:
                    res.diagnostics.append(Diag(
                        "E-Bench-BadStep",
                        f"{b.name} 的规模只有 {len(pts)} 个点，至少要 2 个（否则无法比较）",
                        b.line, b.col))

        # ---- MB-8：成本式里出现的尺寸变量都要有 scale，且至少 2 个
        #      （成本式检查放在 §7.8 的实现里；此处只查"一个都没写"）
        if scale is None or not scaled:
            res.diagnostics.append(Diag(
                "E-Bench-MissingScale",
                "benchmodel 缺少 `scale` 段（测哪些规模）", bm.line, bm.col))


def _check_sort_calls(res, all_fns) -> None:
    """`sort` / `sort_by` 的实参形状（L3）。

    **比较器 MUST 是具名函数**且签名 `(T, T) -> bool`。
    理由：C 的 `qsort` 打穿成本契约，不是因为「带比较器」，是因为
    「比较器是任意代码」。具名函数是有限集合，`O(n log n × κ(cmp))` 才写得出来。
    """
    by_name = {fn.name: fn for fn in all_fns}
    for fn in all_fns:
        for call in walk(fn, "call"):
            nm = call.name or ""
            if nm not in ("sort", "sort_by"):
                continue
            args_node = next((c for c in call.children if c.kind == "args"), None)
            items = args_node.children if args_node is not None else []
            if nm == "sort":
                if len(items) != 1:
                    res.diagnostics.append(Diag(
                        "E-Sort-BadArgs",
                        f"sort 要 1 个实参（容器），给了 {len(items)} 个",
                        call.line, call.col))
                continue
            if len(items) != 2:
                res.diagnostics.append(Diag(
                    "E-Sort-BadArgs",
                    f"sort_by 要 2 个实参（容器 + 具名比较器），给了 {len(items)} 个",
                    call.line, call.col))
                continue
            cmp_node = items[1]
            if cmp_node.kind != "path":
                res.diagnostics.append(Diag(
                    "E-Sort-BadComparator",
                    "sort_by 的比较器 MUST 是具名函数"
                    f"（给的是 {cmp_node.kind}）——"
                    "匿名比较器是任意代码，成本不可静态界定，会打穿成本契约",
                    cmp_node.line, cmp_node.col))
                continue
            target = by_name.get(cmp_node.name or "")
            if target is None:
                res.diagnostics.append(Diag(
                    "E-Sort-BadComparator",
                    f"sort_by 的比较器 `{cmp_node.name}` 不是本模块的函数",
                    cmp_node.line, cmp_node.col))
                continue
            params = walk(target, "param")
            if len(params) != 2:
                res.diagnostics.append(Diag(
                    "E-Sort-BadComparator",
                    f"比较器 `{target.name}` 要 2 个形参，它有 {len(params)} 个",
                    cmp_node.line, cmp_node.col))


def _has_recursion_depth(spec: Node) -> bool:
    """`.tu` 里是否写了 `recursion depth <= N;`（规格 C11-b）。"""
    return any((c.name or "") == "recursion" for c in _clauses(spec))


def _recursion_cycles(fns: Dict[str, Node]) -> Dict[str, List[str]]:
    """普通调用图上的**递归**：`{函数名: 一条回到自己的路径}`。

    只走 `call` 边；`?!` 的候选环是另一套规则（见 `_check_cycles`）。
    环上**每个**成员都会出现在返回值的键里（互递归 a→b→a 时两个都算）。
    """
    edges: Dict[str, Set[str]] = {}
    for name, fn in fns.items():
        out = set()
        for call in walk(fn, "call"):
            nm = callee_name(call)
            if nm and nm in fns:
                out.add(nm)
        edges[name] = out

    cyc: Dict[str, List[str]] = {}
    for start in sorted(edges):
        # 从 start 的后继出发，看能否沿 `call` 边走回 start
        stack: List[Tuple[str, List[str]]] = [
            (m, [start, m]) for m in sorted(edges[start], reverse=True)]
        seen = set()
        while stack:
            node, path = stack.pop()
            if node == start:
                cyc[start] = path
                break
            if node in seen:
                continue
            seen.add(node)
            for m in sorted(edges.get(node, ()), reverse=True):
                stack.append((m, path + [m]))
    return cyc


def check_recursion_depth(impls: Dict[str, Node],
                          specs: Dict[str, Node]) -> List[Diag]:
    """C11-b：**有递归就必须声明 `recursion depth <= N`**，否则编译期报错。

    为什么要报错而不是警告：深度是契约的一部分。不声明时 κ 只算**一层**，
    于是"承诺上界"会**沉默地**小于真实成本（`down(xs,8)`：κ=33 / ops=104）。
    这类错误在成本表上看起来一切正常，是最坏的一种。
    """
    diags: List[Diag] = []
    for name, path in sorted(_recursion_cycles(impls).items()):
        spec = specs.get(name)
        if spec is None:
            continue        # 没有 spec 由别的诊断负责（E-Spec-Missing）
        if _has_recursion_depth(spec):
            continue
        diags.append(Diag(
            "E-Recursion-Unbounded",
            f"`{name}` 有递归（{' → '.join(path)}），但 `.tu` 没有 "
            f"`recursion depth <= N`：κ 只能按一层计，**会小于真实成本**（C11-b）。"
            f"出路：① 在 `spec {name}` 里补 `recursion depth <= 8;`（写真实上界）；"
            f"② 改写成循环；③ 若确实无界，这个函数不能带 `target` 承诺",
            spec.line, spec.col))
    return diags


def _check_while_bounds(res, all_fns, specs, strict: bool) -> None:
    """给每个 `while` 判定上界来源；③ 类按严格模式分档处理。"""
    from bound import progresses
    for fn in all_fns:
        # 模块级 `const N: u64 = 256;` 也是编译期常量，
        # 拿它当循环上界（`while (k < N)`）完全可辩护 —— 一并算进来。
        # 递减循环（甲）：本函数里「只做减法」的变量，`while (j > 0)` 可用
        dec_vars = _monotone_decreasing_names(fn)
        const_sizes = (_collect_const_sizes(fn)
                       | _collect_param_sizes(fn)
                       | set(_MODULE_CONST_SIZES))
        for wh in walk(fn, "while"):
            cond = wh.children[0] if wh.children else None

            # **E21（unsound）**：循环体不推进计数器 ⇒ 死循环。
            # 这一条 MUST 是 error，**不受 strict 影响**：它不是风格问题，是上界不成立。
            ok, prog_why = progresses(wh)
            if not ok:
                res.diagnostics.append(Diag(
                    "E-Loop-CounterUnchanged",
                    f"{prog_why} —— 这会变成死循环（成本为 ∞），不能给出有限上界。"
                    f"出路：① 在体内推进它（`i = i + 1` / `q.pop()`）；"
                    f"② 若确实由外部条件控制，改成有界循环并配 `assume`",
                    wh.line, wh.col, severity="error"))
                continue

            # 来源 ①：条件形态可推
            if _cond_can_bound(cond, const_sizes):
                continue

            # 来源 ①-b：**递减循环**（`while (j > 0)` 且 j 体内只做减法）
            # 上界 = j 的初值。插入排序、堆的上浮/下沉都是这个形状——
            # 不认它，作者会被逼改成选择排序（失去稳定性）或固定圈数（成本式被撑大）。
            if _dec_loop_can_bound(cond, dec_vars):
                continue

            # 来源 ②：条件是谓词且该谓词声明了 repeats
            name, _ = _pred_of(cond)
            if name is not None:
                sp = specs.get(name)
                if sp is not None:
                    body = next((c for c in sp.children if c.kind == "spec-body"), None)
                    has_repeats = any(
                        c.kind == "clause" and (c.name or "") == "repeats"
                        for c in (body.children if body else [])
                    )
                    if has_repeats:
                        continue
                    # 谓词存在但没写 repeats → 给出精确 hint
                    res.diagnostics.append(Diag(
                        "E-While-NoBound",
                        f"while 条件调用了 {name}()，但 {name} 的 spec 没有 repeats；"
                        f"请在 spec {name} 里加 `repeats <表达式>;`（T13 来源②）",
                        wh.line, wh.col,
                        severity="error" if strict else "warning"))
                    continue

            # 来源 ③：都推不出
            hint = ("三条出路：① 若条件可用尺寸比较表达，改写成 `while (i < xs.length())`；"
                    "② 若条件是个函数，把上界写进该函数的 spec 的 repeats；"
                    "③ 否则改写成 for + break")
            res.diagnostics.append(Diag(
                "E-While-NoBound",
                f"无法确定 while 的迭代次数。{hint}",
                wh.line, wh.col,
                severity="error" if strict else "warning"))


def _fn_type_params_of(fn) -> set:
    """函数签名里的类型参数名：`func id<T>(…)` → {"T"}。"""
    out = set()
    for c in getattr(fn, "children", []) or []:
        if getattr(c, "kind", "") == "type-params":
            for p in c.children:
                out.add(p.name or "")
    return out


def _type_compatible(spec_t: str, impl_t: str, tparams: set) -> bool:
    """`Vec<u64>` 与 `Vec<T>` 是否相容：**类型参数当通配符**。"""
    if not tparams:
        return False
    import re
    def norm(s):
        s = s.replace(" ", "")
        for p in tparams:
            s = re.sub(r"\b" + re.escape(p) + r"\b", "?", s)
        return s
    a, b = norm(spec_t), norm(impl_t)
    if "?" not in b and "?" not in a:
        return False
    # 逐字符比：`?` 匹配任意标识符片段（简化：去掉泛型实参名字后比较外层结构）
    def shape(s):
        return re.sub(r"<[^<>]*>", "<*>", s)
    return shape(a) == shape(b) or "?" in b


def _params_compatible(a, b, tparams: set) -> bool:
    if len(a) != len(b):
        return False
    for (_, ta), (_, tb) in zip(a, b):
        if ta == tb:
            continue
        if not _type_compatible(ta, tb, tparams):
            return False
    return True


# ---------------------------------------------------------------- 泛型推断（A 第一批）
#
# **这一批只做"推断 + 登记 + 报错"**，不做代码生成：
# 解释器是动态的，单态化在语义上不必要；但**类型参数必须能定下来**，
# 否则"这个函数到底对什么类型成立"这件事没人知道。

def _type_str_of_node(n) -> str:
    """把类型节点还原成字符串（`Vec<T>` / `Vec<u64>` / `u64`）。"""
    if n is None:
        return ""
    if getattr(n, "kind", "") in ("type-name", "type-ref"):
        args = next((c for c in n.children if c.kind == "type-args"), None)
        if n.kind == "type-ref":
            inner = next((c for c in n.children if c.kind in ("type-name", "type-ref")), None)
            base = _type_str_of_node(inner) if inner is not None else (n.name or "")
        else:
            base = n.name or ""
        if args is None:
            return base
        return base + "<" + ", ".join(_type_str_of_node(a) for a in args.children) + ">"
    return getattr(n, "name", "") or ""


def _unify(pattern: str, actual: str, bind: dict) -> bool:
    """`Vec<T>` vs `Vec<u64>` → bind {T: u64}；冲突返回 False。"""
    import re as _re
    if not pattern:
        return True
    if "<" not in pattern:
        if pattern.isupper() and len(pattern) <= 2:      # `T` / `K` / `V`
            if pattern in bind:
                return bind[pattern] == actual
            bind[pattern] = actual
            return True
        return pattern == actual or not actual
    pm = _re.match(r"^([^<]+)<(.*)>$", pattern)
    am = _re.match(r"^([^<]+)<(.*)>$", actual or "")
    if not pm or not am:
        return False
    if pm.group(1).strip() != am.group(1).strip():
        return False
    ps, as_ = pm.group(2), am.group(2)
    # 顶层逗号切分
    def split(s):
        out, depth, cur = [], 0, ""
        for ch in s:
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
            if ch == "," and depth == 0:
                out.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            out.append(cur.strip())
        return out
    p_parts, a_parts = split(ps), split(as_)
    if len(p_parts) != len(a_parts):
        return False
    return all(_unify(x, y, bind) for x, y in zip(p_parts, a_parts))


def _local_types(fn) -> dict:
    """函数内"名字 → 类型字符串"（形参 + 局部声明）。"""
    env = {}
    ps = next((c for c in fn.children if c.kind == "params"), None)
    for p in (ps.children if ps else []):
        ty = next((c for c in p.children if c.kind in ("type-ref", "type-name")), None)
        env[p.name or ""] = _type_str_of_node(ty)
    for n in _all_nodes(fn):
        if getattr(n, "kind", "") in ("var-decl", "const-decl"):
            ty = next((c for c in n.children if c.kind in ("type-ref", "type-name")), None)
            env[n.name or ""] = _type_str_of_node(ty)
    return env


def _check_generic_calls(impl_mod, specs) -> List[Diag]:
    diags: List[Diag] = []
    generic = {}
    for fn in _all_nodes(impl_mod):
        if getattr(fn, "kind", "") != "fn":
            continue
        tp = next((c for c in fn.children if c.kind == "type-params"), None)
        if tp is not None:
            generic[fn.name or ""] = (fn, [p.name for p in tp.children])
    if not generic:
        return diags
    for owner in _all_nodes(impl_mod):
        if getattr(owner, "kind", "") != "fn":
            continue
        env = _local_types(owner)
        for call in _all_nodes(owner):
            if getattr(call, "kind", "") != "call":
                continue
            short = (call.name or "").split(".")[-1]
            if short not in generic:
                continue
            callee, tparams = generic[short]
            bind: dict = {}
            ps = next((c for c in callee.children if c.kind == "params"), None)
            plist = ps.children if ps else []
            args = next((c for c in call.children if c.kind == "args"), None)
            alist = args.children if args else []
            for pat_param, a in zip(plist, alist):
                pt = next((c for c in pat_param.children
                           if c.kind in ("type-ref", "type-name")), None)
                pat = _type_str_of_node(pt)
                at = env.get(a.name or "", "") if getattr(a, "kind", "") == "path" else ""
                _unify(pat, at, bind)
            missing = [p for p in tparams if p not in bind]
            if missing:
                diags.append(Diag(
                    "E-Type-InferFailed",
                    f"调用泛型函数 {short} 时推不出类型参数 {', '.join(missing)}——"
                    f"给实参加类型标注，或在声明里用具体类型",
                    call.line, call.col))
    return diags


def check_module(tu_src: str, ai_src: Optional[str], module: str = "<module>",
                strict: bool = False, overflow: str = "off") -> CheckResult:
    res = CheckResult(module=module)

    tu = parse_source(tu_src, module + ".tu")
    # 模块级常量名（`const N: u64 = 256;`）——循环上界可以用它们
    _MODULE_CONST_SIZES.clear()
    for mod_ast in ([tu.module] if tu.module else []):
        for it in mod_ast.children:
            if it.kind == "const" and it.name:
                _MODULE_CONST_SIZES.add(it.name)
    for d in tu.diagnostics:
        res.diagnostics.append(Diag(d.code, "[.tu] " + d.message,
                                    d.line, d.col, d.start, d.end))

    ai = None
    if ai_src is not None:
        ai = parse_source(ai_src, module + ".eln")
        for d in ai.diagnostics:
            res.diagnostics.append(Diag(d.code, "[.eln] " + d.message,
                                        d.line, d.col, d.start, d.end))

    tu_items = tu.module.children if tu.module else []
    ai_items = ai.module.children if (ai is not None and ai.module is not None) else []

    # ---- `guarantees`：可检查 / 不可检查（v0.72）
    #
    # 以前 `guarantees` 是"能写、没人看"——一个**假的承诺位**。
    # 现在分两类：可检查的进运行时校验（`I-Guarantee-Runtime`），
    # 不可检查的**必须**报出来（`E-Guarantee-Uncheckable`），要求改写或降级成 `assume`。
    try:
        import guarantees as _gt
        for _it in tu_items:
            if _it.kind != "spec" or not _gt.has_guarantees(_it):
                continue
            _ok, _bad = _gt.classify(_it)
            if _bad:
                res.diagnostics.append(Diag(
                    "E-Guarantee-Uncheckable",
                    f"{_it.name} 的 guarantees 无法检查：{_bad[0]}；"
                    f"可检查的谓词只有 {', '.join(sorted(_gt.CHECKABLE))}，"
                    f"否则请改写（或降级写成 `assume`）",
                    _it.line, _it.col, severity="warning",
                    actions=[Restart(
                        "info", "降级成 `assume`（把承诺说成假设）",
                        patch={"file": module + ".tu", "line": _it.line,
                               "text": "assume <原来的条件>;"})]))
            elif _ok:
                res.diagnostics.append(Diag(
                    "I-Guarantee-Runtime",
                    f"{_it.name} 的 guarantees（{_gt.describe(_ok)}）将在 run 时校验",
                    _it.line, _it.col, severity="info"))
    except Exception:                                      # noqa: BLE001
        pass        # 分类失败不该让整个检查崩掉

    # ---- 严格模式：L3 / L4 / L5
    if strict:
        res.diagnostics.extend(strict_lints(tu_src, tu, ".tu"))
        if ai is not None and ai_src is not None:
            res.diagnostics.extend(strict_lints(ai_src, ai, ".eln"))

    # ---- F-4 / F-8：.ai 不得有声明与 use
    ILLEGAL_IN_AI = {"const", "enum", "struct", "trait", "impl", "spec", "choose",
                     "use", "error-decl"}
    for it in ai_items:
        if it.kind in ILLEGAL_IN_AI:
            code = "E-Ai-HasUse" if it.kind == "use" else "E-Ai-HasDecl"
            res.diagnostics.append(Diag(
                code, f".ai 中不得出现 {it.kind}（F-4/F-8）", it.line, it.col))

    # ---- 用户函数调用的 arity（v0.72）
    #
    # 以前只校验内建（`stdlib.arity`），**用户函数完全不查**：
    # `g(1)` 传给两参函数，`check` 零诊断，直到运行期才炸。
    try:
        _spec_params = {}
        for _it in tu_items:
            if _it.kind != "spec" or not _it.name:
                continue
            _ps = next((c for c in _it.children if c.kind == "params"), None)
            _spec_params[_it.name] = len(_ps.children) if _ps else 0
        if ai is not None and ai.module is not None:
            for _call in walk(ai.module, "call"):
                _nm = (_call.name or "").split(".")[-1]
                if _nm not in _spec_params:
                    continue
                _an = next((c for c in _call.children if c.kind == "args"), None)
                _got = len(_an.children) if _an else 0
                _want = _spec_params[_nm]
                if _got != _want:
                    res.diagnostics.append(Diag(
                        "E-Call-Arity",
                        f"{_nm} 需要 {_want} 个实参，给了 {_got} 个",
                        _call.line, _call.col))
    except Exception:                                      # noqa: BLE001
        pass

    # ---- 收集声明
    specs: Dict[str, Node] = {}
    kernels_decl: Dict[str, Node] = {}
    choosers: Dict[str, Node] = {}
    error_decl: Optional[Node] = None

    for it in tu_items:
        if it.kind == "error-decl":
            if error_decl is not None:
                res.diagnostics.append(Diag(
                    "E-Dup-Error", "模块级 `error` 声明只能有一个（v0.68）",
                    it.line, it.col))
            error_decl = it
        elif it.kind == "spec":
            specs[it.name] = it
        elif it.kind == "kernel":
            kernels_decl[it.name] = it
        elif it.kind == "choose":
            choosers[it.name] = it

    # ---- 收集实现
    impls: Dict[str, Node] = {}
    kernels_impl: Dict[str, Node] = {}
    for it in ai_items:
        if it.kind == "fn":
            impls[it.name] = it
        elif it.kind == "kernel":
            kernels_impl[it.name] = it

    # ---- 重名：同名声明/实现只能出现一次
    _dup_check(res, tu_items, "spec", "E-Duplicate-Spec")
    _dup_check(res, tu_items, "kernel", "E-Duplicate-Kernel")
    _dup_check(res, tu_items, "choose", "E-Duplicate-Choose")
    _dup_check(res, ai_items, "fn", "E-Duplicate-Fn")
    _dup_check(res, ai_items, "kernel", "E-Duplicate-Kernel")

    # ---- 未知子句名 / target 槽位 / objective 取值
    KNOWN_CLAUSES = {
        "effects", "requires", "guarantees", "benchmodel", "target",
"sizes", "repeats", "recursion", "from", "external",
    }
    OBJECTIVES = {"time", "p99", "mem", "size"}
    # 旧名（v0.62 及以前）→ 给出改名提示，而不是笼统的"取值非法"
    OLD_OBJECTIVES = {"minimize_expected_time", "minimize_p99",
                      "minimize_peak_mem", "minimize_code_size"}
    for it in tu_items:
        if it.kind != "spec":
            continue
        body = _spec_body(it)
        if body is None:
            continue
        for c in body.children:
            if c.kind == "clause" and c.name not in KNOWN_CLAUSES:
                res.diagnostics.append(Diag(
                    "E-Clause-Unknown", f"未知子句 {c.name}", c.line, c.col))
            if c.kind == "clause" and c.name == "target":
                for dim in c.children:
                    for slot in dim.children:
                        if slot.kind == "slot" and slot.name not in ("min", "max", "exact"):
                            res.diagnostics.append(Diag(
                                "E-Target-BadSlot",
                                f"target 槽位只能是 min/max/exact，实际 {slot.name}",
                                slot.line, slot.col))

    # ---- F-1：实现必须有声明
    for name, fn in impls.items():
        if name not in specs and name not in kernels_decl:
            res.diagnostics.append(Diag(
                "E-Fn-NoSpec", f"函数 {name} 在 .tu 中没有对应 spec（F-1）",
                fn.line, fn.col))

    # ---- F-2：签名一致
    for name, fn in impls.items():
        spec = specs.get(name)
        if spec is None:
            continue
        a = param_list(spec)
        b = param_list(fn)
        # **泛型函数的签名允许用类型参数**（`func id<T>(const Vec<T>&)` 实现 `Vec<u64>` 的 spec）。
        # 第一批只做"类型参数是通配符"：`Vec<T>` 可与任意 `Vec<X>` 相容。
        _tparams = _fn_type_params_of(fn)
        if not _params_compatible(a, b, _tparams):
            res.diagnostics.append(Diag(
                "E-Spec-SigMismatch",
                f"{name} 的参数类型不一致：.tu=({', '.join(t for _, t in a)}) "
                f"vs .ai=({', '.join(t for _, t in b)})",
                fn.line, fn.col))
            continue
        ra, rb = type_str(_return_of(spec)), type_str(_return_of(fn))
        if ra != rb and not _type_compatible(ra, rb, _tparams):
            res.diagnostics.append(Diag(
                "E-Spec-SigMismatch",
                f"{name} 的返回类型不一致：.tu={ra or '(无)'} vs .ai={rb or '(无)'}",
                fn.line, fn.col))

    # ---- 候选声明：来自 spec 里的 choose（v0.66）
    choosers: Dict[str, dict] = {}
    for spec_name, spec in specs.items():
        body = _spec_body(spec)
        if body is None:
            continue
        for c in body.children:
            if c.kind != "choose":
                continue
            label = c.name or ""
            if label in choosers:
                res.diagnostics.append(Diag(
                    "E-Duplicate-Choose", f"choose {label} 重复定义", c.line, c.col))
                continue
            over: List[str] = []
            pending: List[str] = []
            objective = ""
            for cc in c.children:
                if cc.kind != "choose-clause":
                    continue
                if cc.name == "over":
                    v = cc.value
                    if isinstance(v, dict):
                        over = list(v.get("names") or [])
                        pending = list(v.get("pending") or [])
                    else:
                        over = list(v or [])
                elif cc.name == "pending":
                    res.diagnostics.append(Diag(
                        "E-Choose-PendingClause",
                        "v0.66 起没有 pending 子句：写在 over 里，如 `over {a, b pending}`",
                        cc.line, cc.col))
                elif cc.name == "objective":
                    objective = str(cc.value or "")

            choosers[label] = {
                "node": c, "host": spec_name, "over": over, "pending": pending,
                "objective": objective,
                "params": param_list(c),
                "ret": type_str(_return_of(c)),
            }
            res.choices[label] = {"over": over, "pending": pending,
                                  "objective": objective, "host": spec_name}

            if objective:
                if objective in OLD_OBJECTIVES:
                    res.diagnostics.append(Diag(
                        "E-Choose-OldObjective",
                        f"objective 已改名：{objective} → 用 time/p99/mem/size",
                        c.line, c.col))
                elif objective not in OBJECTIVES:
                    res.diagnostics.append(Diag(
                        "E-Choose-BadObjective",
                        f"objective 取值非法：{objective}（仅 time/p99/mem/size）",
                        c.line, c.col))

            if not over:
                res.diagnostics.append(Diag(
                    "E-Choose-Empty", f"choose {label} 的 over 不能为空（B-8）",
                    c.line, c.col))
            if len(over) > 4:
                res.diagnostics.append(Diag(
                    "E-Choose-TooManyCandidates",
                    f"choose {label} 有 {len(over)} 个候选，上限 4（B-8）",
                    c.line, c.col))
            if len(set(over)) != len(over):
                res.diagnostics.append(Diag(
                    "E-Choose-DuplicateCandidate",
                    f"choose {label} 的 over 里有重复候选", c.line, c.col))
            for pn in pending:
                if pn not in over:
                    res.diagnostics.append(Diag(
                        "E-Choose-PendingMismatch",
                        f"choose {label}：{pn} 标了 pending，但不在 over 名单里",
                        c.line, c.col))

    # ---- 实现侧：`} kernels L(...) { 候选* }`，可嵌套（N-1 上限 2 层）
    kernels_impl: Dict[str, Dict[str, Node]] = {}

    def collect(owner: Node, depth: int):
        for ch in owner.children:
            if ch.kind != "kernels":
                continue
            label = ch.name or ""
            # v0.68 撤回嵌套：kernels 只能挂在 fn 体之后，MUST NOT 出现在候选体里
            if depth > 1:
                res.diagnostics.append(Diag(
                    "E-Kernels-Nested",
                    f"kernels {label} 嵌套在候选体里：v0.68 起 kernels 不嵌套"
                    "（需要「选了 A 才需要 B」时，把 B 提升为独立函数）",
                    ch.line, ch.col))

            # 与 choose 的签名对账
            info = choosers.get(label)
            if info is not None:
                kp = [ty for _, ty in param_list(ch)]
                cp = [ty for _, ty in info["params"]]
                if kp != cp:
                    res.diagnostics.append(Diag(
                        "E-Spec-SigMismatch",
                        f"kernels {label} 的参数类型与 choose 不一致："
                        f"({', '.join(kp)}) vs ({', '.join(cp)})",
                        ch.line, ch.col))
                kr = type_str(_return_of(ch))
                if kr != info["ret"]:
                    res.diagnostics.append(Diag(
                        "E-Spec-SigMismatch",
                        f"kernels {label} 的返回类型与 choose 不一致："
                        f"{kr or '(无)'} vs {info['ret'] or '(无)'}",
                        ch.line, ch.col))

            bucket = kernels_impl.setdefault(label, {})
            for cand in ch.children:
                if cand.kind != "candidate":
                    continue
                if cand.name in bucket:
                    res.diagnostics.append(Diag(
                        "E-Duplicate-Candidate",
                        f"候选 {cand.name} 在 kernels {label} 中重复",
                        cand.line, cand.col))
                bucket[cand.name] = cand
                collect(cand, depth + 1)      # 仍递归，以便对嵌套报 E-Kernels-Nested

    for fn in impls.values():
        collect(fn, 1)

    res.candidates = {label: {n: c for n, c in b.items()}
                      for label, b in kernels_impl.items()}

    # ---- B-10：实现的候选必须在 over 名单里
    for label, bucket in kernels_impl.items():
        info = choosers.get(label)
        if info is None:
            continue
        declared = set(info["over"])
        for cand_name, cand in bucket.items():
            if cand_name not in declared:
                res.diagnostics.append(Diag(
                    "E-Choose-Undeclared",
                    f"候选 {cand_name} 有实现，但不在 choose {label} 的 over 名单里（B-10）",
                    cand.line, cand.col))

    # ---- B-16：pending 与实现双向对账
    for label, info in choosers.items():
        bucket = kernels_impl.get(label, {})
        for cand in info["over"]:
            implemented = cand in bucket
            listed = cand in info["pending"]
            if listed and implemented:
                res.diagnostics.append(Diag(
                    "E-Choose-PendingMismatch",
                    f"choose {label}：{cand} 已实现，但仍标着 pending（声明过期）",
                    info["node"].line, info["node"].col))
            if (not listed) and (not implemented):
                res.diagnostics.append(Diag(
                    "E-Choose-PendingMismatch",
                    f"choose {label}：{cand} 没有实现，却没标 pending（漏报）",
                    info["node"].line, info["node"].col))

    # ---- v0.68：`T!` 与 error 声明的一致性
    for fn in impls.values():
        fv = fn.value or {}
        fallible = bool(fv.get("fallible"))
        uses_q = len(walk(fn, "try")) > 0
        if fallible and error_decl is None:
            res.diagnostics.append(Diag(
                "E-Error-DeclMissing",
                f"函数 {fn.name} 用 `T!` 表示可能失败，但模块没有 `error` 声明",
                fn.line, fn.col))
        if uses_q and not fallible:
            res.diagnostics.append(Diag(
                "E-Fallible-Missing",
                f"函数 {fn.name} 里有 `?`，返回类型 MUST 写成 `T!`",
                fn.line, fn.col))
        for r in walk(fn, "return"):
            if not fallible or not r.children:
                continue
            v = r.children[0]
            if v.kind == "call" and (v.name or "").split(".")[-1] in ("Ok", "Err"):
                if (v.name or "").split(".")[-1] == "Ok":
                    res.diagnostics.append(Diag(
                        "E-Return-OkInFallible",
                        f"`{fn.name}` 是 `T!` 函数：`return x` 已自动成为 `Ok(x)`，MUST NOT 再写 `Ok(...)`",
                        r.line, r.col))

    # ---- ?! 使用处（B-3 / 宿主 / 实参个数）
    all_fns: List[Node] = list(impls.values())
    for fn in all_fns:
        for d in walk(fn, "dispatch"):
            label = d.name or ""
            res.labels_used.add(label)
            info = choosers.get(label)
            if info is None:
                res.diagnostics.append(Diag(
                    "E-Choose-Undeclared",
                    f"?!{label} 没有对应的 choose 声明（B-3）", d.line, d.col))
                continue
            if info["host"] != fn.name:
                res.diagnostics.append(Diag(
                    "E-Choose-WrongHost",
                    f"?!{label} 只能写在宿主 {info['host']} 里（当前在 {fn.name}）",
                    d.line, d.col))
            args = next((x for x in d.children if x.kind == "args"), None)
            nargs = len(args.children) if args is not None else 0
            if nargs != len(info["params"]):
                res.diagnostics.append(Diag(
                    "E-Choose-ArityMismatch",
                    f"?!{label} 传了 {nargs} 个实参，choose 声明了 {len(info['params'])} 个参数",
                    d.line, d.col))

    # ---- B-4：每个 choose 至少被引用一次（防装饰）
    # 纯计划态（宿主还没有 .ai 实现）时豁免：此时本来就没地方写 ?!
    for label, info in choosers.items():
        if info["host"] not in impls:
            continue
        if label not in res.labels_used:
            res.diagnostics.append(Diag(
                "E-Choose-Unused",
                f"choose {label} 声明了但没有任何 ?! 使用（B-4）",
                info["node"].line, info["node"].col))

    # ---- B-15：?! 不得构成整个函数体
    for fn in all_fns:
        body = next((c for c in fn.children if c.kind == "block"), None)
        if body is None or len(body.children) != 1:
            continue
        only = body.children[0]
        if only.kind == "return" and only.children:
            inner = only.children[0]
            if inner.kind == "dispatch":
                res.diagnostics.append(Diag(
                    "E-Choose-WholeBody",
                    f"{fn.name} 的函数体只有 return ?!…；B-15 要求宿主有自己的逻辑",
                    fn.line, fn.col))

    # ---- 状态表
    for name, spec in specs.items():
        has_impl = name in impls
        ht = _has_target(spec)
        st, note = derive_state(has_impl, ht)
        res.functions.append(FnInfo(
            name=name, kind="fn", has_impl=has_impl, has_target=ht,
            is_pub=bool((impls.get(name).value or {}).get("pub")) if name in impls else False,
            line=spec.line, state=st, note=note))

    # ---- 可变性：给 `let` 绑定的变量赋值 → E-Assign-Immutable
    for fn in all_fns:
        body = next((c for c in fn.children if c.kind == "block"), None)
        if body is not None:
            res.diagnostics.extend(_check_immutability(body))

    # ---- L6：可能溢出的算术（开关：off / warn / error）
    if overflow in ("warn", "error"):
        sev = "error" if overflow == "error" else "warning"
        for fn in all_fns:
            for b in walk(fn, "binary"):
                if (b.value or "") not in ("+", "-", "*", "<<"):
                    continue
                res.diagnostics.append(Diag(
                    "L6-Overflow",
                    f"算术 {b.value} 可能回绕（按位宽 mod 2^N）；严格模式提示（{sev}）",
                    b.line, b.col, severity=sev))

    # ---- E-6-b：类型命名空间里的成员 MUST 用 `::`（v0.69）
    _enum_names = {it.name for it in tu_items if it.kind == "enum"}
    if _enum_names:
        for mod in (tu.module, ai.module if ai is not None else None):
            if mod is None:
                continue
            for node in walk(mod, "field") + walk(mod, "method-call"):
                if not node.children:
                    continue
                base = node.children[0]
                if base.kind == "path" and (base.name or "") in _enum_names:
                    res.diagnostics.append(Diag(
                        "E-Member-WrongSep",
                        f"`{base.name}.{node.name or ''}` 用了 `.`：枚举变体与静态成员 MUST 用 `::`"
                        f"（v0.69），例如 `{base.name}::{node.name or ''}`",
                        node.line, node.col))

    # ---- MB：benchmodel（v0.69）
    _int_consts: Dict[str, int] = {}
    for it in tu_items:
        if it.kind == "const" and len(it.children) >= 2 and it.children[-1].kind == "lit-int":
            try:
                _int_consts[it.name] = int(it.children[-1].value)
            except (TypeError, ValueError):
                pass
    _check_benchmodel(res, specs, choosers, impls, _int_consts)

    # ---- T13：while 的迭代上界（三种来源 + 两档处理）
    _check_while_bounds(res, all_fns, specs, strict)
    _check_sort_calls(res, all_fns)

    # ---- C11-b：递归必须有 `recursion depth` 声明（否则 κ 沉默地偏小）
    res.diagnostics.extend(check_recursion_depth(impls, specs))

    # ---- span 兜底：checker 自己造的诊断只有 line/col，这里换算成偏移
    _resolve_spans(res.diagnostics, tu_src, ai_src)

    # **E30**：借用检查（v0 最小实现）。放在最后追加，不影响既有诊断顺序。
    from borrow import check_borrows
    for _code, _msg, _ln, _col in check_borrows(tu.module if tu else None,
                                                ai.module if ai is not None else None):
        res.diagnostics.append(Diag(_code, _msg, _ln, _col))


    # **§13.6 周边检查**：B-25 / B-26（parallel 与 ?!）
    res.diagnostics.extend(_check_concurrency_cost(tu.module if tu else None,
                                                   ai.module if ai is not None else None))
    res.diagnostics.extend(_check_parallel(tu.module if tu else None,
                                            ai.module if ai is not None else None))

    # **效果检查**：实际用到的效果 ⊆ spec 声明
    # **FFI：每个 extern 必须声明 effects**（并记下来给调用方用）
    res.diagnostics.extend(_collect_extern_effects(
        ai.module if ai is not None else None))

    res.diagnostics.extend(_check_effects(tu.module if tu else None,
                                          ai.module if ai is not None else None))

    # **格式化：能在编译期查的就不留到运行期**
    res.diagnostics.extend(_check_format_calls(ai.module if ai is not None else None))

    # **泛型调用必须能定下类型参数**（A 第一批）
    res.diagnostics.extend(_check_generic_calls(ai.module if ai is not None else None, specs))

    # **每个 match 必须有 `_` 兜底臂**（否则没命中会被静默跳过）
    res.diagnostics.extend(_check_match_default(ai.module if ai is not None else None))

    # **并行任务之间不许共享可变状态**
    res.diagnostics.extend(_check_parallel_share(ai.module if ai is not None else None))

    # **浮点字面量赋给整数类型**（§12.2：以前零诊断通过）
    res.diagnostics.extend(_check_float_to_int(ai.module if ai is not None else None))


    # **requires 调用点验证**：证明不了就报错（不靠运行期断言兜底）
    import requires as _requires
    for _c, _m, _ln, _col in _requires.check(tu.module if tu else None,
                                             ai.module if ai is not None else None):
        res.diagnostics.append(Diag(_c, _m, _ln, _col))
    return res


def _check_float_to_int(mod) -> List[Diag]:
    """**浮点字面量赋给整数类型 MUST 报错**（§12.2）。

    以前 `u64 y = 1.5;` 零诊断通过 —— 类型检查的洞：
    静默截断在成本契约语言里尤其危险（数字看着对，实际少了半截）。
    """
    if mod is None:
        return []
    int_types = {"u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64",
                 "usize", "isize", "byte"}
    out: List[Diag] = []

    def walk(n) -> None:
        if n is None:
            return
        k = getattr(n, "kind", "")
        if k in ("var-decl", "val-decl", "const-decl"):
            kids = getattr(n, "children", []) or []
            if len(kids) >= 2:
                tn = kids[0]
                tname = (getattr(tn, "name", "") or "").split("<")[0].strip()
                expr = kids[1]
                if tname in int_types and getattr(expr, "kind", "") == "lit-float":
                    out.append(Diag(
                        "E-Type-FloatToInt",
                        f"浮点字面量不能赋给整数类型 {tname}（隐式截断）；"
                        f"请写成整数，或把变量类型改成 f64",
                        getattr(expr, "line", 0) or getattr(n, "line", 0),
                        getattr(expr, "col", 0) or getattr(n, "col", 0)))
        for c in getattr(n, "children", []) or []:
            walk(c)

    walk(mod)
    return out


def _check_immutability(body: Node) -> List[Diag]:
    """遍历语句，记录每个绑定的可变性；给 `let` 赋值即报错。

    作用域用栈：块、if/while/for/match 的体各开一层。
    """
    diags: List[Diag] = []
    scopes: List[Dict[str, bool]] = [{}]     # name -> is_var

    def lookup(name: str) -> Optional[bool]:
        for s in reversed(scopes):
            if name in s:
                return s[name]
        return None

    def root_name(node: Optional[Node]) -> Optional[str]:
        """取赋值目标的根标识符：x / x.f / x[i] / *x 都归到 x。"""
        cur = node
        guard = 0
        while cur is not None and guard < 64:
            guard += 1
            if cur.kind == "path":
                return cur.name
            if cur.kind in ("field", "index", "deref") and cur.children:
                cur = cur.children[0]
                continue
            return None
        return None

    def walk_block(block: Node):
        scopes.append({})
        for st in block.children:
            k = st.kind
            if k in ("val-decl", "var-decl", "const-decl"):
                # v0.68：`const T n = ...` 不可赋值；`T n = ...` 可赋值
                scopes[-1][st.name] = (k != "const-decl")
            elif k in ("assign", "assign-op"):
                nm = root_name(st.children[0] if st.children else None)
                if nm is not None and lookup(nm) is False:
                    diags.append(Diag(
                        "E-Assign-Immutable",
                        f"不能给 `const` 绑定的 {nm} 赋值（去掉 const 即可写）",
                        st.line, st.col))
            elif k == "block":
                walk_block(st)
            else:
                for c in st.children:
                    if c.kind == "block":
                        walk_block(c)
        scopes.pop()

    walk_block(body)
    pass  # (removed misplaced _check_parallel call)
    return diags






EFFECT_VOCAB = {"io", "fs", "env", "nondet", "alloc"}


def _spec_clauses(spec) -> Dict[str, List]:
    """把 spec 的子句按名字归类（effects / requires / …）。

    **子句挂在 `spec-body` 下面**（不是 spec 的直接子节点），
    这是第一版写错的地方：effects 名字还存在 `clause.value` 里（列表），
    不是 children。
    """
    out: Dict[str, List] = {}
    if spec is None:
        return out
    for holder in spec.children:
        if getattr(holder, "kind", "") != "spec-body":
            continue
        for c in holder.children:
            if getattr(c, "kind", "") == "clause" and getattr(c, "name", ""):
                out.setdefault(c.name, []).append(c)
    return out


def _declared_effects(spec) -> Tuple[set, List]:
    """返回 (声明的效果集合, 未知效果名的位置)。"""
    names: set = set()
    bad: List = []
    for cl in _spec_clauses(spec).get("effects", []):
        raw = cl.value if isinstance(getattr(cl, "value", None), list) else []
        for nm in raw:
            if nm in ("", "pure"):
                continue
            if nm not in EFFECT_VOCAB:
                bad.append(cl)
            else:
                names.add(nm)
    return names, bad


def _used_effects(fn, fns, memo, stack) -> set:
    """函数体内实际用到的效果：调用图的并集 + 内建效果。"""
    name = fn.name or ""
    if name in memo:
        return memo[name]
    if name in stack:
        return set()
    stack.add(name)
    used: set = set()
    for d in _all_nodes(fn):
        if d.kind in ("call", "method-call") and getattr(d, "name", ""):
            short = (d.name or "").split(".")[-1]
            used |= set(stdlib.effects_of(short))
            callee = fns.get(short)
            if callee is not None:
                used |= _used_effects(callee, fns, memo, stack)
    # **高阶内建**：`map(xs, f)` 的效果包含 `f` 的效果（实参是**名字**，不是调用）
    for d in _all_nodes(fn):
        if d.kind == "call" and (d.name or "") in ("map", "filter", "fold", "count_if"):
            _an = next((c for c in d.children if c.kind == "args"), None)
            _items = _an.children if _an is not None else []
            for _it in _items[1:]:
                if _it.kind == "path" and (_it.name or "") in fns:
                    used |= _used_effects(fns[_it.name], fns, memo, stack)

    # **FFI**：调用 `extern` 声明的函数，效果取它自己声明的那些
    for d in _all_nodes(fn):
        if d.kind == "call" and getattr(d, "name", ""):
            short = (d.name or "").split(".")[-1]
            ext = _EXTERN_EFFECTS.get(short)
            if ext:
                used |= set(ext)
    stack.discard(name)
    memo[name] = used
    return used




def _task_io(task) -> Tuple[set, set]:
    """收集一个任务块的 (写到的变量, 读到的变量)。"""
    written: set = set()
    read: set = set()
    stack = [task]
    while stack:
        x = stack.pop()
        if x is None:
            continue
        if x.kind == "assign" and x.children:
            tgt = x.children[0]
            if tgt.kind == "path" and tgt.name:
                written.add(tgt.name)
            elif tgt.kind == "index" and tgt.children and tgt.children[0].kind == "path":
                written.add(tgt.children[0].name or "")
            stack.extend(x.children[1:])
            continue
        if x.kind in ("const-decl", "var-decl", "typed-decl") and x.name:
            written.add(x.name)
            stack.extend([c for c in x.children if c.kind != "type-name"])
            continue
        if x.kind == "path" and x.name:
            read.add(x.name)
        stack.extend(getattr(x, "children", []) or [])
    return written, read






# ---------------------------------------------------------------- 格式化检查（v0.71）
#
# **能在编译期查的，就不留到运行期**：
#   · 占位符个数 vs 实参个数        → `E-Format-Arity`
#   · 说明符与实参类型（`{:x}` 给字节串）→ `E-Format-ArgType`
#   · 格式串本身不合法（未闭合、`{:.2}`）→ `E-Format-BadSpec`
#   · 格式串不是字面量                → `W-Format-Dynamic`（警告，运行期才查）

def _check_format_calls(impl_mod) -> List[Diag]:
    diags: List[Diag] = []
    if impl_mod is None:
        return diags
    import fmt as _fmt
    for fn in _all_nodes(impl_mod):
        if getattr(fn, "kind", "") != "fn":
            continue
        env = _local_types(fn)
        for call in _all_nodes(fn):
            if getattr(call, "kind", "") != "call":
                continue
            short = (call.name or "").split(".")[-1]
            if short not in ("format", "print"):
                continue
            args = next((c for c in call.children if c.kind == "args"), None)
            items = args.children if args else []
            if not items:
                continue
            first = items[0]
            # **只有第一个实参是字面量字符串时才检查**（`print(1, 2)` 不是格式化）
            if getattr(first, "kind", "") != "lit-string":
                if short == "format" or any(
                        getattr(x, "kind", "") == "lit-string" for x in items[1:]):
                    if len(items) > 1:
                        diags.append(Diag(
                            "W-Format-Dynamic",
                            "格式串不是字面量 → 无法在编译期检查占位符与实参是否匹配",
                            call.line, call.col))
                continue
            text = first.value
            if isinstance(text, bytes):
                text = text.decode("utf-8", "replace")
            if short == "print" and "{" not in text:
                continue                      # 普通打印，不管
            try:
                parts = _fmt.parse(text)
            except _fmt.FmtError as exc:
                diags.append(Diag(exc.code, f"{exc.msg}（位置 {exc.pos}）", first.line, first.col))
                continue
            holes = [pl for k, pl in parts if k == "hole"]
            given = items[1:]
            if len(holes) != len(given):
                diags.append(Diag(
                    "E-Format-Arity",
                    f"格式串有 {len(holes)} 个洞，但给了 {len(given)} 个实参",
                    call.line, call.col))
                continue
            for hole, arg in zip(holes, given):
                _align, _w, kind = hole
                if kind != "x":
                    continue
                ty = ""
                if getattr(arg, "kind", "") == "path":
                    ty = env.get(arg.name or "", "")
                if ty.startswith("Vec") or ty == "Vec":
                    diags.append(Diag(
                        "E-Format-ArgType",
                        f"`{{:x}}`（十六进制）要数字，但 `{arg.name}` 是 `{ty}`",
                        arg.line, arg.col))
    return diags


def _check_match_default(ch_mod) -> List[Diag]:
    """**每个 `match` MUST 以 `_` 兜底臂结尾**（v0.71）。

    ## 为什么是"强制兜底"而不是"穷尽性分析"

    穷尽性分析要做"模式集合 ⊇ 变体集合"的推导——对枚举可行，对**跨模块的错误类型**
    和**将来可能新增的变体**都不牢靠。而**强制 `_`** 只要看最后一条臂：

    - **实现成本**：一个循环；
    - **保证强度**：**"没命中"这件事从此不可能发生**——静默跳过这一类 bug 被根除；
    - **代价**：多写一行 `_ => { return …; }`。

    将来若要做穷尽性提示，可以在"模式已覆盖全部变体但仍写了 `_`"时给 **冗余警告**
    ——**那是在此基础上的增强，不是替代**。
    """
    diags: List[Diag] = []
    if ch_mod is None:
        return diags
    for n in _all_nodes(ch_mod):
        if getattr(n, "kind", "") != "match":
            continue
        arms = [c for c in getattr(n, "children", [])[1:] if getattr(c, "kind", "") == "arm"]
        if not arms:
            diags.append(Diag("E-Match-NoDefault",
                              "match 至少要有 `_` 兜底臂", n.line, n.col))
            continue
        last_pat = None
        for c in reversed(arms[-1].children):
            if c.kind in ("pattern", "pattern-wildcard"):
                last_pat = c
                break
        if last_pat is None or last_pat.kind != "pattern-wildcard":
            diags.append(Diag("E-Match-NoDefault",
                              "match MUST 以 `_` 兜底臂结尾——否则没命中时会被**静默跳过**；"
                              "加一条 `_ => { return <默认值>; }`",
                              n.line, n.col))
    return diags


def _check_parallel_share(ch_mod) -> List[Diag]:
    """**并行任务之间不许共享可变状态**（v0.71）。

    C++ 的说法：`std::thread` 里捕获 `int&` 然后两边都写 = 数据竞争 = UB。
    Rust 用 `Send`/`Sync` 在**编译期**挡；我们这一版用**变量集合相交**来挡：

    - 两个任务都写同一个变量 → 报
    - 一个任务写、另一个任务读同一个变量 → 也报（读者看到的是哪个值？）
    - 变量在任务内部声明（每个任务各自一份）→ 不报
    """
    diags: List[Diag] = []
    if ch_mod is None:
        return diags
    for n in _all_nodes(ch_mod):
        if n.kind != "parallel":
            continue
        body = next((c for c in n.children if c.kind == "block"), None)
        if body is None:
            continue
        tasks = [c for c in body.children if c.kind == "block"]
        if len(tasks) < 2:
            continue
        infos = [_task_io(x) for x in tasks]
        for i in range(len(tasks)):
            for j in range(i + 1, len(tasks)):
                wi, ri = infos[i]
                wj, rj = infos[j]
                clash = (wi & wj) | (wi & rj) | (ri & wj)
                if clash:
                    diags.append(Diag(
                        "E-Parallel-ShareMut",
                        f"任务 {i + 1} 与任务 {j + 1} 共享了可变状态 {sorted(clash)}："
                        f"并行里读写同一个变量，结果取决于调度；"
                        f"改成「各任务写自己的局部变量，再用返回值合并」",
                        n.line, n.col))
    return diags


def node_effects(node, ch_mod) -> set:
    """任意 AST 节点（函数体 / 候选块）实际用到的效果。"""
    if ch_mod is None or node is None:
        return set()
    fns = {c.name: c for c in ch_mod.children if c.kind == "fn"}
    return _used_effects(node, fns, {}, set())


def function_effects(tu_mod, ch_mod) -> Dict[str, set]:
    """每个函数**实际用到**的效果（传递闭包）——给 measure/bench 判"可不可测"。"""
    if ch_mod is None:
        return {}
    fns = {c.name: c for c in ch_mod.children if c.kind == "fn"}
    memo: Dict[str, set] = {}
    return {name: _used_effects(fn, fns, memo, set()) for name, fn in fns.items()}


#: `extern` 声明的效果（模块加载时由 `_collect_extern_effects` 填）
_EXTERN_EFFECTS: Dict[str, set] = {}


def _collect_extern_effects(impl_mod) -> List[Diag]:
    """**每个 `extern` MUST 声明 effects**（FFI 是最容易偷偷做副作用的地方）。

    没有声明 → `E-Extern-NoEffects`：宁可让作者写 `effects {}` 明确表示"这是纯函数"，
    也不许默认放行。
    """
    _EXTERN_EFFECTS.clear()
    diags: List[Diag] = []
    if impl_mod is None:
        return diags
    for c in impl_mod.children or []:
        if getattr(c, "kind", "") != "extern":
            continue
        eff = next((x for x in c.children if x.kind == "extern-effects"), None)
        if eff is None:
            diags.append(Diag("E-Extern-NoEffects",
                              f"extern {c.name} MUST 声明 `effects {{ … }}`"
                              f"（纯函数就写 `effects {{}}`）", c.line, c.col))
            _EXTERN_EFFECTS[c.name or ""] = set()
            continue
        _EXTERN_EFFECTS[c.name or ""] = {x.name for x in eff.children if x.name}
    return diags


def _check_effects(tu_mod, ch_mod) -> List[Diag]:
    """**效果检查**：函数实际用到的效果 MUST ⊆ spec 声明的效果。

    这是"可撒谎位置"之外**唯一能自动验证**的契约维度：
    `print` 用了 `io`，而 `.tu` 写 `effects {}` → `E-Effect-Missing`。
    """
    diags: List[Diag] = []
    if ch_mod is None:
        return diags
    specs = {c.name: c for c in (tu_mod.children if tu_mod else []) if c.kind == "spec"}
    fns = {c.name: c for c in ch_mod.children if c.kind == "fn"}
    memo: Dict[str, set] = {}

    for name, fn in fns.items():
        spec = specs.get(name)
        if spec is None:
            continue
        declared, bad = _declared_effects(spec)
        for b in bad:
            raw = b.value if isinstance(getattr(b, "value", None), list) else []
            unknown = [x for x in raw if x and x != "pure" and x not in EFFECT_VOCAB]
            diags.append(Diag("E-Effect-Unknown",
                              f"未知效果 {unknown}（可用：{'/'.join(sorted(EFFECT_VOCAB))}）",
                              b.line, b.col))
        used = _used_effects(fn, fns, memo, set())
        missing = used - declared
        if missing:
            diags.append(Diag("E-Effect-Missing",
                              f"{name} 实际用到了 {sorted(missing)}，"
                              f"但 spec 只声明了 {sorted(declared) or '无'}",
                              fn.line, fn.col))
    return diags


def _all_nodes(root) -> List:
    """递归收集所有节点（含自身）。"""
    out = []
    stack = [root]
    while stack:
        x = stack.pop()
        if x is None:
            continue
        out.append(x)
        stack.extend(getattr(x, "children", []) or [])
    return out


def _check_parallel(tu_mod, ch_mod) -> List[Diag]:
    """§13.6 的周边检查（B-25 / B-26）。

    - **B-25**：`parallel` 里 MUST NOT 含 `?!`（分档靠测量，并行里测不准）。
    - **B-26**：`?!` 候选里 MUST NOT 含 `parallel`（候选要能被隔离测量）。
    """
    diags: List[Diag] = []
    if ch_mod is None:
        return diags
    nodes = _all_nodes(ch_mod)
    for n in nodes:
        if n.kind == "candidate":
            for d in _all_nodes(n):
                if d is not n and d.kind == "parallel":
                    diags.append(Diag(
                        "E-Parallel-Measured",
                        "候选里不能有 parallel：候选要能被隔离测量（B-26）",
                        d.line, d.col))
    return diags


def _check_concurrency_cost(tu_mod, ch_mod) -> List[Diag]:
    """**G25（2026-11-02）**：有并发（`parallel`/`spawn`）的函数，其 `time` 契约
    在 v0 里**不可证伪**。

    为什么必须说出来：v0 **没有内存模型、没有竞争规则**，κ 按单线程语义累加。
    于是"并发下实际耗时"与"承诺的 time 上界"之间没有可比关系——
    审稿人第一个反例就是"你承诺 O(n)，但两个任务一起跑当然不同"。

    我们不假装能判：给出**警告级**诊断，并在 `elench cost` 里同样打一行。
    """
    diags: List[Diag] = []
    if ch_mod is None or tu_mod is None:
        return diags
    specs = {getattr(c, "name", ""): c for c in getattr(tu_mod, "children", []) or []
             if getattr(c, "kind", "") == "spec"}
    for fn in getattr(ch_mod, "children", []) or []:
        if getattr(fn, "kind", "") not in ("func", "fn"):
            continue
        par = next((n for n in _all_nodes(fn) if n.kind in ("parallel", "spawn")), None)
        if par is None:
            continue
        sp = specs.get(getattr(fn, "name", ""))
        has_time = False
        if sp is not None:
            for c in getattr(sp, "children", []) or []:
                if getattr(c, "kind", "") == "spec-body":
                    for cl in getattr(c, "children", []) or []:
                        if getattr(cl, "kind", "") == "clause" and (cl.name or "") == "target":
                            has_time = any(getattr(d, "name", "") == "time"
                                           for d in _all_nodes(cl))
        if has_time:
            diags.append(Diag(
                "W-Concurrency-Time",
                f"`{fn.name}` 使用并发，但 v0 的 time 契约按**单线程语义**计 κ；"
                f"并发下的实际耗时/竞争不在承诺范围内（G25：无内存模型）",
                par.line, par.col, severity="warning"))
    return diags


def _line_col_to_offset(src: str, line: int, col: int) -> Tuple[int, int]:
    """1-based (line, col) → 0-based 字节偏移区间 [start, end)。"""
    if line < 1:
        return 0, 0
    pos = 0
    cur = 1
    while cur < line:
        nl = src.find("\n", pos)
        if nl < 0:
            return len(src), len(src)
        pos = nl + 1
        cur += 1
    start = min(pos + max(0, col - 1), len(src))
    end = min(start + 1, len(src))
    return start, end


def _resolve_spans(diags: List[Diag], tu_src: str, ai_src: Optional[str]):
    tu_lines = tu_src.count("\n") + 1
    for d in diags:
        if d.start or d.end:
            continue
        if d.line <= tu_lines:
            d.start, d.end = _line_col_to_offset(tu_src, d.line, d.col)
        elif ai_src is not None:
            d.start, d.end = _line_col_to_offset(ai_src, d.line, d.col)


def _dup_check(res: CheckResult, items: List[Node], kind: str, code: str):
    seen = {}
    for it in items:
        if it.kind != kind or not it.name:
            continue
        if it.name in seen:
            res.diagnostics.append(Diag(
                code, f"{kind} {it.name} 重复定义", it.line, it.col))
        else:
            seen[it.name] = it


def _check_cycles(all_fns: List[Node], choosers: Dict[str, Node],
                  specs: Dict[str, Node], kernels_decl: Dict[str, Node]) -> List[Diag]:
    """扩展调用图：普通调用边 ∪ `?!` 到每个候选的边。有环即报 B-9-a。"""
    diags: List[Diag] = []

    # 函数名 -> 被调名集合（含 ?! 的全部候选）
    edges: Dict[str, Set[str]] = {}
    edges_line: Dict[str, Tuple[int, int]] = {}
    for fn in all_fns:
        edges_line.setdefault(fn.name, (fn.line, fn.col))
        out: Set[str] = set()
        for call in walk(fn, "call"):
            nm = callee_name(call)
            if nm:
                out.add(nm)
        for d in walk(fn, "dispatch"):
            ch = choosers.get(d.name or "")
            if ch is None:
                continue
            for c in ch.children:
                if c.kind == "choose-clause" and c.name == "over":
                    out.update(c.value or [])
        edges[fn.name] = out

    # B-9：?! 的宿主不得是它自己 choose 的候选
    for fn in all_fns:
        for d in walk(fn, "dispatch"):
            ch = choosers.get(d.name or "")
            if ch is None:
                continue
            for c in ch.children:
                if c.kind == "choose-clause" and c.name == "over":
                    if fn.name in (c.value or []):
                        diags.append(Diag(
                            "E-Choose-SelfReference",
                            f"?!{d.name} 的候选包含宿主 {fn.name} 自身（B-9）",
                            d.line, d.col))

    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {n: WHITE for n in edges}
    stack: List[str] = []

    def dfs(n: str) -> None:
        color[n] = GRAY
        stack.append(n)
        for m in sorted(edges.get(n, ())):
            if m not in edges:
                continue
            if color.get(m, WHITE) == GRAY:
                cycle = stack[stack.index(m):] + [m]
                diags.append(Diag(
                    "E-Choose-Cycle",
                    "扩展调用图有环（B-9-a）：" + " → ".join(cycle),
                    edges_line.get(cycle[0], (1, 1))[0],
                    edges_line.get(cycle[0], (1, 1))[1]))
            elif color.get(m, WHITE) == WHITE:
                dfs(m)
        stack.pop()
        color[n] = BLACK

    for n in sorted(edges):
        if color[n] == WHITE:
            dfs(n)

    return diags


# ---------------------------------------------------------------- 严格模式（L3 / L4 / L5）

MAX_LINE = 100
MAX_TUPLE = 3


def strict_lints(src: str, res_parse, label: str) -> List[Diag]:
    """把宽松解析下"能过但不规范"的东西提升为错误。

    L3  end 省略种类（parser 记为 soft）
    L4  元组元素 > 3
    L5  行宽 > 100 列
    """
    out: List[Diag] = []

    # L3：parser 的 soft 通道
    for d in getattr(res_parse, "soft", []) or []:
        out.append(Diag(d.code, f"[{label}] {d.message}", d.line, d.col))

    # L4：元组上限（类型与表达式都查）
    if res_parse.module is not None:
        for kind in ("type-tuple", "tuple"):
            for node in walk(res_parse.module, kind):
                if len(node.children) > MAX_TUPLE:
                    what = "元组类型" if kind == "type-tuple" else "元组"
                    out.append(Diag(
                        "L4-TupleTooLong",
                        f"[{label}] {what}有 {len(node.children)} 个元素，"
                        f"上限 {MAX_TUPLE}（L4）",
                        node.line, node.col))

    # L5：行宽
    for i, line in enumerate(src.splitlines(), 1):
        if len(line) > MAX_LINE:
            out.append(Diag(
                "L5-LineTooLong",
                f"[{label}] 第 {i} 行有 {len(line)} 列，上限 {MAX_LINE}（L5）",
                i, MAX_LINE + 1))

    return out


# ---------------------------------------------------------------- 文件层

def check_files(tu_path: str, ai_path: Optional[str] = None,
                strict: bool = False, overflow: str = "off") -> CheckResult:
    with open(tu_path, "r", encoding="utf-8") as f:
        tu_src = f.read()
    ai_src = None
    if ai_path is None:
        guess = os.path.splitext(tu_path)[0] + ".eln"
        if os.path.exists(guess):
            ai_path = guess
    if ai_path and os.path.exists(ai_path):
        with open(ai_path, "r", encoding="utf-8") as f:
            ai_src = f.read()
    module = os.path.splitext(os.path.basename(tu_path))[0]
    return check_module(tu_src, ai_src, module, strict=strict, overflow=overflow)
