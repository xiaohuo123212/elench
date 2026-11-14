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

from typenames import canon, display
import os
from dataclasses import dataclass, field
import re
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
    #: **要不要碰 `target` 那一行**。默认 False ⇒ 构造时就拒绝。
    #: 只有"修正格式/槽位"这类修复才该是 True；**改写成本表达式永远不许**。
    #: （必须放在最后一个字段：前面的构造都是位置传参。）
    touches_target: bool = False

    def __post_init__(self) -> None:
        """**硬规则在构造点就拒绝**：任何 restart 都不许触碰 `.tu` 的 `target`。

        以前这条只写在 docstring 里、再由 `test_restarts.py` 事后查一遍——
        而且只查了债务修法清单那一处。**诊断带 actions 之后，能改文件的地方变多了**，
        靠测试守纪律等于没守：新增一条诊断就可能悄悄绕过。

        契约是**持久的**，实现是**一次性的**；"不达标就改承诺"这条路必须不存在。
        （对应 Anthropic 那条：技术上做得到，也不让做——所以要**在能力边界上拒绝**，
        不是在使用时自觉。）
        """
        blob = " ".join([str(self.patch.get("text", "")),
                         str(self.patch.get("file", "")),
                         " ".join(self.argv or [])])
        if "target" in blob and not self.touches_target:
            raise ValueError(
                "restart 不许触碰 target（契约持久、实现一次性；"
                f"要改 target 请人工编辑）：{self.label}")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label,
                "argv": list(self.argv), "patch": dict(self.patch)}


def _line_patch(src: Optional[str], file: str, line: int, fn, label: str):
    """把某一行的文本按 `fn` 改写，做成一条 `edit` 修法；拿不到那行就返回 None。

    **宁可返回 None（给不出修法），也不给一条会改错行的修法。**
    """
    lines = (src or "").splitlines()
    if not (0 < line <= len(lines)):
        return None
    old = lines[line - 1]
    try:
        new = fn(old)
    except Exception:                                  # noqa: BLE001
        return None
    if new is None or new == old or not new.strip():
        return None
    return Restart("edit", label, patch={"file": file, "line": line,
                                         "text": new.strip()})


def _ren_obj(s: str, old: str):
    """把 `objective <old>;` 里的取值换成 `time`（找不到就返回 None）。"""
    i = s.find("objective")
    if i < 0 or not old:
        return None
    j = s.find(old, i)
    if j < 0:
        return None
    return s[:j] + "time" + s[j + len(old):]


def _rewrite_cond(s: str, var: str, vec: str):
    """把 `while (<任意>)` 的条件改写成 `var < vec.length()`。"""
    import re as _re
    if not var or not vec:
        return None
    new, n = _re.subn(r"while\s*\([^)]*\)",
                      "while (" + var + " < " + vec + ".length())", s, count=1)
    return new if n else None


def _first_vec_param(fn):
    """函数里第一个容器形参的名字（找不到就 None）——修 while 条件要用它。"""
    for p in walk(fn, "param"):
        # 类型是 **node** 不是字符串：`type-ref → type-name(name="Vec") → type-args`
        vals, stack = [], [p]
        while stack:
            n = stack.pop()
            v = getattr(n, "value", None)
            if isinstance(v, str):
                vals.append(v)
            nm = getattr(n, "name", None)
            if isinstance(nm, str):
                vals.append(nm)
            stack.extend(getattr(n, "children", None) or [])
        if any("Vec" in x for x in vals):
            return getattr(p, "name", None)
    return None


def _target_slot_fix(src, file: str, line: int, bad: str):
    """槽位名不合法 → 换成 `max`。**显式声明 touches_target**，且只改槽位名。"""
    lines = (src or "").splitlines()
    if not (0 < line <= len(lines)) or not bad:
        return None
    old = lines[line - 1]
    if bad + ":" not in old:
        return None
    new = old.replace(bad + ":", "max:", 1)
    return Restart("edit", "把槽位改成 `max`（上界的默认写法）",
                   patch={"file": file, "line": line, "text": new.strip()},
                   touches_target=True)


def _acts(a) -> list:
    """`actions=[...]` 的便捷写法：拿不到修法就给空列表。"""
    return [a] if a is not None else []


def _spec_skeleton(ai_src: Optional[str], fn) -> str:
    """从 `.eln` 的函数签名**抄**出一个 spec 骨架。

    只处理单行签名；多行签名**不瞎猜**，返回一句提示——宁可给不出，也不给错的。
    """
    lines = (ai_src or "").splitlines()
    if not (0 < getattr(fn, "line", 0) <= len(lines)):
        return "spec <函数名>(...) { effects {}; }"
    head = lines[fn.line - 1].strip()
    if "{" not in head:
        return f"// 多行签名：请手动为 `{getattr(fn, 'name', '')}` 写 spec"
    sig = head.split("{", 1)[0].strip()
    for kw in ("func ", "fn "):
        if sig.startswith(kw):
            sig = "spec " + sig[len(kw):]
            break
    return f"{sig} {{ effects {{}}; }}"


def _guarantees_line(spec) -> int:
    """spec 里第一条 `guarantees` 子句所在的行（找不到就退回 spec 的行）。"""
    body = next((c for c in (spec.children or []) if c.kind == "spec-body"), None)
    for cl in (body.children if body else []):
        if cl.kind == "clause" and (cl.name or "") == "guarantees":
            return getattr(cl, "line", 0) or getattr(spec, "line", 0)
    return getattr(spec, "line", 0)


#: 实现体里**会绑定名字**的节点：局部变量、循环变量、`match` 的模式绑定。
#: 只做"声明扫描"，不做作用域/流敏感分析——见 `_guarantee_visible_names`。
_LOCAL_BIND_KINDS = ("var-decl", "val-decl", "for", "binding")


def _guarantee_visible_names(spec: Node, impl: Optional[Node]) -> Set[str]:
    """`guarantees` 里**静态可见**的名字：形参 + `ret`/`result` + 实现体声明过的局部名。

    为什么需要它：谓语里写错名字（`is_sorted(ys)` 而 `ys` 根本不存在）以前
    `check` 一声不吭，直到 `run` 求值才炸。`guarantees` 在**函数帧里**求值
    （见 `Interp._check_guarantees`），所以形参和局部变量都够得着。

    **取舍**：这里是"实现体里有没有出现过这个声明"的宽松判定，不是完整的
    词法作用域 / 流敏感分析——一个只在某条分支里声明的局部名也算可见。
    流敏感分析的复杂度远大于收益，而**真正的未绑定**仍由 `run` 在求值时
    报 `E-Guarantee-Violated`（`未绑定的名字 …`）。代价是：拼错成另一个
    恰好存在的局部名时静态查不出来——那是名字语义问题，不是作用域问题。
    """
    from guarantees import RETURN_NAMES
    names: Set[str] = set(RETURN_NAMES)
    # 有实现时以**实现**的形参名为准：`run` 的帧里绑的是实现的名字，
    # 而 F-2 只比类型、不比名字（`.tu` 写 `xs`、`.eln` 写 `ys` 是能过的）。
    src = impl if impl is not None else spec
    ps = next((c for c in (src.children or []) if c.kind == "params"), None)
    for p in (ps.children if ps else []):
        if getattr(p, "kind", "") == "param" and p.name:
            names.add(p.name)
    if impl is not None:
        for k in _LOCAL_BIND_KINDS:
            for nd in walk(impl, k):
                if getattr(nd, "name", ""):
                    names.add(nd.name)
    return names


def _guarantee_refs(expr: Node, acc: Optional[Set[str]] = None) -> Set[str]:
    """`guarantees` 表达式里被**当变量引用**的名字（调用名不算）。

    `call` 的第一个孩子是被调者（`path`/`field`），它不是变量——只递归它的
    `args`，否则 `is_sorted` 会被当成一个未定义的名字。
    """
    if acc is None:
        acc = set()
    if expr is None:
        return acc
    k = getattr(expr, "kind", "")
    if k == "path":
        if expr.name:
            acc.add(expr.name)
        return acc
    if k == "call":
        args = next((c for c in expr.children if c.kind == "args"), None)
        for a in (args.children if args else []):
            _guarantee_refs(a, acc)
        return acc
    for c in expr.children:
        _guarantee_refs(c, acc)
    return acc


def _demote_guarantees(tu_src: Optional[str], line: int) -> str:
    """把某一行的 `guarantees X;` **注释掉**（`assume` 已于 2026-11-04 移除）。"""
    lines = (tu_src or "").splitlines()
    if not (0 < line <= len(lines)):
        return "// 拿不到这一行，请手动删掉这条 guarantees"
    return "// " + lines[line - 1].strip() + "   ← 不可检查，已注释"


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


#: 这些子句的名字会被**别的阶段按名字消费**，所以 `.tu` 与 `.eln` 的形参名
#: 必须一致：`target` 交给 `cost`、`requires` 同时交给 `cost` 的 facts 与运行期、
#: `guarantees` 交给运行期（见 `E-Spec-ParamName`）。
_NAME_SENSITIVE_CLAUSES = ("target", "requires", "guarantees")


def _is_container_param(p: Node) -> bool:
    """形参声明是不是容器（与 `cost_interp._is_container_type` 同一口径）。"""
    ty = next((c for c in (getattr(p, "children", None) or [])
               if getattr(c, "kind", "") in ("type-ref", "type-name")), None)
    if ty is None:
        return False
    if ty.kind == "type-ref" and ty.children:
        ty = ty.children[0]
    return (ty.kind == "type-name"
            and canon(ty.name or "") in ("vector", "array"))


def _clause_param_refs(clause: Node, params: List[Node]) -> Dict[str, Set[str]]:
    """子句里**按名字**出现的形参 → `{形参名: {子句里用的拼写}}`。

    只收 `path`（调用名不算）。`target` 还多一条绑定魔法：形参里恰好一个
    容器时，`O(n)` 的 `n` 指那个容器（`cost_interp.CostInterp._target_cost`）。
    所以 `n` 也要算成对那个形参的引用——否则
    `spec f(const vector<u64>& xs) { target { max: O(n) } }` 在实现把 `xs`
    改名成 `ys` 时查不出来。`requires` 没有这条魔法（运行期就在实现帧里
    求值，`n` 只是普通名字），所以只在 target 上认。

    返回拼写是为了**消息里的改法能写对**：`O(n)` 的改法是把 `n` 改成
    `ys`，不是把 `xs` 改成 `ys`。
    """
    names = {p.name for p in params if getattr(p, "name", "")}
    if not names:
        return {}
    conts = [p.name for p in params
             if getattr(p, "name", "") and _is_container_param(p)]
    magic = conts[0] if (len(conts) == 1
                         and (clause.name or "") == "target") else None
    out: Dict[str, Set[str]] = {}
    for nd in walk(clause, "path"):
        nm = nd.name or ""
        if nm in names:
            out.setdefault(nm, set()).add(nm)
        elif nm == "n" and magic is not None and nm not in _MODULE_CONST_SIZES:
            out.setdefault(magic, set()).add("n")
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
    """收集本函数里「绑定到尺寸量、且在作用域内不变」的变量名。

    两类，**同一口径**（与 cost 侧 `_decl_inits` 一致）：

      · `const T n = <尺寸表达式>;` —— const 语义保证不变；
      · `T n = <尺寸表达式>;` 且**全函数没有任何 assign / assign-op 指向它**
        —— 只做语法判定、不做数据流。被改写过的 `var` **不算**：它的值可能
        变大或变小，拿声明处的尺寸当上界会低估（2026-11-12，与 cost 的
        `_written_names` 同一条规则）。

    以前只收 `const`，于是 `u64 m = xs.size(); while (i < m)` 明明能推出轮数，
    `check` 却报 `E-While-NoBound`——**checker 与 cost 对同一个程序给出相反结论**。
    """
    written = set()
    for a in (list(walk(fn, "assign")) + list(walk(fn, "assign-op"))):
        lhs = a.children[0] if (a.children or []) else None
        if getattr(lhs, "kind", "") == "path" and getattr(lhs, "name", ""):
            written.add(lhs.name)
    names = set()
    for d in walk(fn, "const-decl"):
        if len(d.children) >= 2 and _is_size_expr(d.children[1], frozenset()):
            names.add(d.name)
    for d in walk(fn, "var-decl"):
        if not d.name or len(d.children) < 2:
            continue
        if not (getattr(d, "value", None) or {}).get("has_init", True):
            continue
        if d.name in written:
            continue
        if _is_size_expr(d.children[-1], frozenset()):
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
                if canon(nm) in ("vector", "array") or nm in INT_PARAM_TYPES:
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
        if op in ("&&", "||"):
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
    if cond.kind == "unary" and (cond.value or "") == "!":
        return False
    return False


def _pred_of(cond: Node):
    """T13 来源 ②：条件是不是一个谓词调用；返回 (谓词名, 节点) 或 (None, None)。

    **2026-11-13**：判据改走 `bound.predicate_call_of`（成本域那一份），
    于是 `!p(x)` / `p(x) == false` / `p(x) != true` 与裸 `p(x)` 一样被认出。
    以前这里只认 `cond.kind == "call"`，而 `cost` 已经能算这三种写法的 κ
    ⇒ **check 喊"无法确定迭代次数"、cost 却算出具体界**，两个命令互相矛盾。
    """
    if cond is None:
        return None, None
    from cost_domain.bound import predicate_call_of
    try:
        pc = predicate_call_of(cond)
    except Exception:              # 判据再稳也不许把检查器弄崩
        pc = None
    if pc is not None and pc.name:
        return pc.name.split(".")[-1], pc
    if cond.kind == "call" and cond.name:      # 兜底：bound 认不出时保持旧口径
        return cond.name.split(".")[-1], cond
    return None, None


# ---------------------------------------------------------------- repeats（2026-11-09）
#
# `repeats` 是**契约的一部分**，但以前 `check` 完全不管它：
#   · `repeats alpha(n)`（未定义的名字）→ check 无诊断，`cost` 静默给 ⊤；
#   · `repeats` 写在一个**不当 while 条件**的函数上 → 永远不生效，也没有提示。
#
# 运行期那半在 `Interp._repeats_limit`（循环跑过声明上界就报
# `E-Repeats-Exceeded`）；这里管**静态**那半：名字必须解析得出来、
# 声明必须有消费者。

#: repeats 里允许出现的「圈数函数」。它们不是用户函数，也没有实现——
#: 成本域把 `log(...)` 当成 `log2`（`bound.expr_to_bound`）。
_REPEATS_FUNCS = frozenset({"log", "log2", "ln", "min", "max"})


def _repeats_clause(spec: Node) -> Optional[Node]:
    """spec 的 `repeats` 子句（没有 → None）。"""
    for c in _clauses(spec):
        if (c.name or "") == "repeats" and c.children:
            return c
    return None


def _repeats_visible_names(spec: Node) -> Set[str]:
    """repeats 里允许出现的名字：该 spec 的形参 + 模块级常量。"""
    out = set(_MODULE_CONST_SIZES)
    params = _params_of(spec)
    for p in (params.children if params is not None else []):
        if getattr(p, "name", ""):
            out.add(p.name)
    return out


def _repeats_undefined(spec: Node, clause: Node,
                       declared: Set[str]) -> List[str]:
    """repeats 表达式里**解析不出来**的名字 / 函数名（排好序）。

    口径：repeats 只能引用 ① 该 spec 的形参；② 模块级常量；
    ③ `log` / `log2` 等圈数函数；④ 本模块声明过的函数（允许，虽然少见）。
    其它名字在 cost 域里认不出，κ 只会退成 ⊤——**以前 check 对此静默**。
    """
    visible = _repeats_visible_names(spec)
    miss: Set[str] = set()
    # **被调函数的路径不是"引用"**：parser 把 `log(hi - lo)` 表示成
    # `call(name=log)`，且 `children[0]` 再挂一个 `path log`。只按 `call.name`
    # 判函数名，`path` 这一遍要跳过这个 callee（否则 `log` 会被当成未定义变量）。
    callee_paths = set()
    for c in walk(clause, "call"):
        ch = getattr(c, "children", ()) or ()
        if ch and getattr(ch[0], "kind", "") == "path":
            callee_paths.add(id(ch[0]))
        nm = (c.name or "").split(".")[-1]
        if nm and nm not in _REPEATS_FUNCS and nm not in declared:
            miss.add(nm)
    for p in walk(clause, "path"):
        if id(p) in callee_paths:
            continue
        nm = p.name or ""
        if nm and nm not in visible:
            miss.add(nm)
    return sorted(miss)


def _check_repeats(res, tu_items, impls: Dict[str, Node],
                   specs: Dict[str, Node], module: str = "") -> None:
    """`repeats` 的静态检查：名字可解析 + 声明有消费者。"""
    declared: Set[str] = set(specs) | set(impls)
    for it in tu_items:
        if getattr(it, "kind", "") == "kernel" and getattr(it, "name", ""):
            declared.add(it.name)
    # 「谁被当成循环条件用过」——`and`/`or` 组合里的调用也算（不误报）。
    used: Set[str] = set()
    for fn in impls.values():
        for wh in walk(fn, "while"):
            cond = wh.children[0] if wh.children else None
            for c in walk(cond, "call"):
                nm = (c.name or "").split(".")[-1]
                if nm:
                    used.add(nm)
    for spec in tu_items:
        if getattr(spec, "kind", "") != "spec" or not getattr(spec, "name", ""):
            continue
        clause = _repeats_clause(spec)
        if clause is None:
            continue
        miss = _repeats_undefined(spec, clause, declared)
        if miss:
            _p = ", ".join(sorted(_repeats_visible_names(spec)
                                  - set(_MODULE_CONST_SIZES))) or "（没有形参）"
            res.diagnostics.append(Diag(
                "E-Repeats-UndefinedName",
                f"{spec.name} 的 repeats 引用了未定义的名字 "
                f"`{'`, `'.join(miss)}`：repeats 运行时只按**谓词自己的形参**"
                f"（{_p}）与模块级常量求值，别的名字求不出来。"
                f"以前这里**静默通过**，`cost` 只会给出 ⊤——"
                f"现在它是 error，因为一条验不了的上界等于没有上界",
                clause.line, clause.col, severity="error"))
            continue
        # **声明必须有消费者**：repeats 只约束「以这个函数为条件的 while」。
        # 写在自己的函数体上（函数体里没有这样的循环）永远不生效。
        # 只在**有实现**时提示：没有实现的 spec 可能被别的模块当条件用。
        if spec.name in impls and spec.name not in used:
            res.diagnostics.append(Diag(
                "W-Repeats-Unused",
                f"{spec.name} 声明了 repeats，但它没有被任何 `while` 当作条件调用——"
                f"这条上界**不会生效**（repeats 只约束「以它为条件的循环」，"
                f"写在自己函数体里的循环上没用）。"
                f"修法：① 把 while 的条件包成这个谓词；② 或删掉这条 repeats",
                clause.line, clause.col, severity="warning"))


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
            # **注意优先级**：`parser.py` 通常已经先抛同名错误（文案带"必写"），
            # 所以这条只是兜底；两条文案必须一致，否则用户会看到两种说法。
            res.diagnostics.append(Diag(
                "E-Bench-MissingScale",
                "benchmodel 缺少 `scale` 段（测哪些规模，必写）", bm.line, bm.col,
                actions=[Restart(
                    "edit", "补一个 scale 骨架（把 xs 换成你的容器名）",
                    patch={"file": (module or "a") + ".tu",
                           "line": bm.line,
                           "text": "scale { xs in range(1, 4096, *2); }"})]))


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


def _recursion_depth_value(spec: Node):
    """`recursion depth <= N;` 的**值**：`(N, None)` / `(None, 原文)` / `None`（没写）。

    与 `interp._recursion_depth_of` 同一口径（**运行期与检查器读同一份形状**，
    否则又是"两边各自解释"）。三态里第三态必须报错，见 `check_recursion_depth`。
    """
    for c in _clauses(spec):
        if (c.name or "") != "recursion":
            continue
        kids = getattr(c, "children", ()) or ()
        for ch in kids:
            if getattr(ch, "kind", "") != "binary" or (ch.value or "") not in ("<=", "<"):
                continue
            grand = getattr(ch, "children", ()) or ()
            if len(grand) < 2:
                continue
            lhs, rhs = grand[0], grand[1]
            if getattr(lhs, "kind", "") != "path" or (lhs.name or "") != "depth":
                continue
            if getattr(rhs, "kind", "") == "lit-int":
                try:
                    n = int(rhs.value)
                except (TypeError, ValueError):
                    n = None
                if n is not None and n >= 1:
                    return (n, None)
            return (None, f"depth {ch.value} {getattr(rhs, 'value', '?')}")
        return (None, "recursion depth")
    return None


def check_recursion_depth(impls: Dict[str, Node],
                          specs: Dict[str, Node]) -> List[Diag]:
    """C11-b：**有递归就必须声明 `recursion depth <= N`**，否则编译期报错。

    为什么要报错而不是警告：深度是契约的一部分。不声明时 κ 只算**一层**，
    于是"承诺上界"会**沉默地**小于真实成本（`down(xs,8)`：κ=33 / ops=104）。
    这类错误在成本表上看起来一切正常，是最坏的一种。

    2026-11-10 补：**声明读不出正整数也报错**（`E-Recursion-BadDepth`）。
    以前 `recursion depth <= 0;` / `recursion depth <= n;` 会被
    「有这条子句」放行，而 cost 域读不出值时**当成没声明**（只算一层）——
    两头都放过去，κ 又小于实测。现在静态报错，运行期另外抛
    `E-Recursion-Uncheckable`（`interp._check_rec_depth`）。
    """
    diags: List[Diag] = []
    # ① **声明本身必须可读**：对所有 spec 查（不只是递归的）——写成
    #    `<= 0` / `<= n` 的声明在运行期是"不可校验"，在 cost 域是"没有"，
    #    留在非递归函数上也是同一句空话。
    bad: Dict[str, str] = {}
    for name, spec in sorted(specs.items()):
        got = _recursion_depth_value(spec)
        if got is None:
            continue                        # 没写：交给 ② 按"有没有递归"判
        val, raw = got
        if val is not None:
            continue
        bad[name] = raw or "recursion depth"
        diags.append(Diag(
            "E-Recursion-BadDepth",
            f"`{name}` 的 `recursion depth` 写成了 `{raw}`，读不出正整数 ——"
            f"这条上界**无法校验**（cost 会当成没声明、只按一层计 κ），"
            f"等于承诺了一句空话（C11-b）。出路：写成 "
            f"`recursion depth <= N;`，N 是 **>= 1 的整数常量**",
            spec.line, spec.col))
    # ② **有递归就必须有可读的深度声明**（坏声明已在 ① 报过，不重复）。
    for name, path in sorted(_recursion_cycles(impls).items()):
        spec = specs.get(name)
        if spec is None:
            continue        # 没有 spec 由别的诊断负责（E-Spec-Missing）
        if name in bad:
            continue
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


def check_exp_risk(ai_mod: Optional[Node],
                   tu_mod: Optional[Node]) -> List[Diag]:
    """`W-Exp-Risk`：树递归 ⇒ **「最坏可能指数级，除非你能证明不是」**（警告）。

    ## 为什么要有它

    `cost` 从 2026-11-09（`e7f5202`）起就把树递归判成 `κ = ⊤`，并在「原因」行
    说清楚为什么。但**只有跑 `cost` 才看得见**：`check` 一声不吭，作者可能永远
    不知道自己的递归是树形的——移植正则时实测 k=5/10/15 的操作数是
    ×29.30 / ×29.02（理想 `2^5 = 32`），确实是**指数**，而当时 `check` 零诊断。

    ## 判定只有一份（不许另写一套）

    这里**不做任何递归形状分析**，直接问 `CostInterp._rec_tree_like()`
    （它内部就是 `_self_call_shape`，也就是 `cost` 判 ⊤ 时用的那一个）。
    触发条件与 `cost` 的 ⊤ 原因逐字对应：

      · 一条执行路径上有 **≥2 个自调用点**（`fib(n-1) + fib(n-2)`）；
      · **或**自调用落在循环体里（每层可能跑 L 次，L 静态不可知）。

    **`per_path == 0` 不报**（互递归 `a↔b` / 经函数值转发）：那种形状是「链」不是
    「树」——`_rec_linear()` 的注释写得很清楚：这条边在环上，但按名字数不到自调用点，
    「一次」的模型本身不贴形状 ⇒ 不猜。`cost` 那边对它的原因行也按同一口径写
    （`_note_tree_rec` 区分「树递归」与「互递归：自调用点在环上」，2026-11-10）。
    在这里报「指数」同样是猜——宁可不说。`E-Recursion-Unbounded` 仍然管
    「互递归没写深度」。

    **只在 `.tu` 真的声明了深度时报**（`recursion depth <= N`，或常数 `repeats`）：
    没有深度时 `cost` 根本走不到那条 ⊤ 分支（它退到 target / 内联），而且
    `E-Recursion-Unbounded` 已经把「没深度」报成 error 了，不重复报。

    ## 为什么是 warning 不是 error

    树递归**合法**，很多算法本来就是树形的（归并/快速排序是分治；朴素快速幂、
    朴素 fib 是树形）。它的问题只是「调用树规模不是 深度 × 单次成本」——
    **最坏可能指数级**，要作者自己确认。所以这条不阻断 `build`，`check` 里看得见。
    """
    out: List[Diag] = []
    if ai_mod is None:
        return out
    try:
        from cost_interp import CostInterp
        ci = CostInterp(ai_mod, tu_mod)
    except Exception:                                      # noqa: BLE001
        return out      # 分析本身失败不该让 check 崩掉，也不许编一条诊断
    try:
        for name in sorted(ci.fns):
            # **判定复用**：`_rec_tree_like` 就是 `cost` 判 ⊤ 的那一个
            # （内部是 `_self_call_shape`）——不在这里重写递归形状分析。
            if not ci._rec_tree_like(name):
                continue
            # **visited 守卫（②，2026-11-12）之后不再是"最坏可能指数级"**：
            # 每个顶点至多进一次 ⇒ 激活数 ≤ `n(标记容器)`，`cost` 已经给出有限 κ。
            # 这时再报「可能是指数」就与 κ 自相矛盾（一条提示不许比结论更悲观）。
            if ci._visited_depth(name) is not None:
                continue
            # **区间对半分（②'，2026-11-12）同理**：两次自调用拿的是
            # `[lo, mid)` 与 `[mid, hi)`——**互不相交、各 ≤ 一半** ⇒ 激活数
            # ≤ `2 × (hi - lo)`，`cost` 已经给出有限 κ。`fib` / `place` 那种
            # "同一区间反复试"过不了这条判据，仍然照报。
            if ci._halving_span(name) is not None:
                continue
            _depth = ci.depth_of(name)
            if _depth is None:
                continue
            _per, _loop = ci._self_call_shape(name)
            _why = []
            if _per >= 2:
                _why.append(f"一条执行路径上有 {_per} 个自调用点")
            if _loop:
                _why.append("自调用落在循环体里——每层可能跑多次")
            fn = ci.fns[name]
            out.append(Diag(
                "W-Exp-Risk",
                f"`{name}` 是树递归（{'；'.join(_why)}）：调用树规模不是「深度 × 单次成本」"
                f"——**最坏可能指数级，除非你能证明不是**（移植正则回溯实测 k=5/10/15 时"
                f"操作数 ×29.30 / ×29.02，理想 2^5 = 32）。树递归本身合法（归并/快排这类"
                f"分治就是树形），这里只是风险提示，不阻断构建。"
                f"去哪儿看：`elench cost <模块>` 的「原因」行（写着 κ 为什么退 ⊤）、"
                f"spec 里的 `repeats`（循环上界）与 `recursion depth <= N`（深度上界，"
                f"当前 {_depth}）。若确认是指数：记忆化 / 剪枝 / 换算法，而不是去改循环变量。",
                getattr(fn, "line", 0), getattr(fn, "col", 0), severity="warning"))
    except Exception:                                      # noqa: BLE001
        pass        # 单条形状算不出来 → 少一条提示，也不让整个 check 崩
    return out


def _check_while_bounds(res, all_fns, specs, strict: bool,
                        ai_src: Optional[str] = None,
                        fn_file: Optional[str] = None) -> None:
    """给每个 `while` 判定上界来源；③ 类按严格模式分档处理。"""
    from bound import progresses, width_bounded_loop, halving_interval_loop
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
                    f"② 若确实由外部条件控制，改成有界循环，并把那个上界**写成 benchmodel 的输入**",
                    wh.line, wh.col, severity="error"))
                continue

            # 来源 ①-c：**位宽有界**（2026-11-12）——`while (x != 0) { x = x & (x - 1); }`
            # / `x = x >> 1` / `x = x / 2`，`x` 是无符号整数 ⇒ 每轮至少少一位，
            # 轮数 ≤ 类型位宽。判据只此一份（`bound.width_bounded_loop`）：
            # `check` 与 `cost` MUST NOT 给出相反结论。
            #
            # **命中条件但证不出时**（`x != 7` / 有符号 / 体内多处写…）不在这里
            # 出诊断：下面 ③ 那条带**可应用的修法**（`改成 i < xs.length()`），
            # 把它挤掉等于让作者少一条出路。具体拒因只作为 ③ 的那句话的前缀。
            _wb_note = ""
            _wb, _wb_why, _wb_hit = width_bounded_loop(wh, fn)
            if _wb is not None:
                continue
            if _wb_hit:
                _wb_note = _wb_why

            # 来源 ①-d：**区间减半**（2026-11-13）——`while (lo < hi) { mid = lo +
            # (hi - lo) / 2; … }` 的轮数是 `log2(s₀) + 1`。判据只此一份
            # （`bound.halving_interval_loop`，与 `cost` 同一个入口）：
            # **`check` 与 `cost` MUST NOT 给出相反结论**——`cost` 已经能给出
            # `log2(n(xs)) + 1` 的循环，`check` 不该再说"无法确定迭代次数"。
            if halving_interval_loop(wh, fn)[0] is not None:
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
            _v = getattr(cond.children[0], "name", None) if (
                cond is not None and getattr(cond, "children", None)) else None
            _vec = _first_vec_param(fn)
            # **具体拒因当前缀**（2026-11-12）：`x != 7` / 有符号 / 体内多处写
            # 退 ⊤ 时，读者需要知道是哪一条前提没过；出路仍是原来那三条。
            _lead = f"{_wb_note}。" if _wb_note else ""
            res.diagnostics.append(Diag(
                "E-While-NoBound",
                f"无法确定 while 的迭代次数。{_lead}{hint}",
                wh.line, wh.col,
                severity="error" if strict else "warning",
                actions=_acts(_line_patch(
                    ai_src, fn_file or ((module or "a") + ".eln"), wh.line,
                    lambda s, a=_v, b=_vec: _rewrite_cond(s, a, b),
                    f"把条件改成 `{_v} < {_vec}.length()`（若这是遍历整个容器）"))))


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


def _canon_type_str(s: str) -> str:
    """把类型字符串里的容器名换成规范名（`Vec` / `vector` 视为同一个）。

    改名期两套名字都要能用：`.tu` 写 `Vec<u8>&`、`.eln` 写 `vector<u8>&`
    必须判为**同一个签名**——否则一半改一半不改就报 E-Spec-SigMismatch。
    """
    if not s:
        return s
    for _old, _new in (("FixedVec", "array"), ("Vec", "vector"), ("Map", "map"),
                       ("Set", "set"), ("Deque", "deque"), ("Option", "optional")):
        s = re.sub(r"\b" + _old + r"\b", _new, s)
    return s


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


#: `_call_inner` 里有专门分支的内建——不算"未声明调用"
_CALL_BUILTIN = {"min", "max", "Ok", "Err", "Some", "None", "panic", "unwrap",
                 "expect", "assert", "print", "println", "dbg",
                 "for_each", "map", "filter", "fold", "count_if", "sort", "sort_by", "swap",
                "push_heap", "pop_heap"}


def unknown_calls(tu_mod, ch_mod) -> list:
    """**E18**：找出分析器不认识的调用名。

    判据：不在用户函数里、不是 stdlib、不是已声明 `extern`、不是语言内建。
    方法调用（`xs.length()`）走成员派发，不在这个兜底里。

    为什么需要它：`_call_inner` 遇到这种调用会记 `1 + 实参`，
    **把被调函数体当成 0 成本**——对内部一无所知时这不是上界。
    """
    try:
        import stdlib as _sl
        from bound import _descendants as _desc
    except Exception:                                  # noqa: BLE001
        return []
    fns, externs = set(), set()
    for m in (tu_mod, ch_mod):
        if m is None:
            continue
        for n in (getattr(m, "children", None) or []):
            if getattr(n, "kind", "") in ("func", "fn", "kernel") and getattr(n, "name", None):
                fns.add(n.name)
    # **以 `CostInterp` 自己的登记表为准**——它已经把三种写法都解析过了
    # （顶层 `extern`、spec 里的 `external { … }`、`external "lib" { … }`），
    # 我们再猜一遍形状只会漂。构造失败就退化成"没有已声明项"。
    try:
        from cost_interp import CostInterp as _CI
        _ci = _CI(ch_mod, tu_mod)
        for _a in ("externs", "extern_spec_names", "extern_names"):
            externs |= set(getattr(_ci, _a, set()) or set())
    except Exception:                                  # noqa: BLE001
        pass

    out = {}
    for c in (_desc(ch_mod, "call") if ch_mod is not None else []):
        nm = getattr(c, "name", None) or ""
        if "." in nm or "::" in nm:
            continue
        if (not nm or nm in fns or nm in externs
                or nm in _CALL_BUILTIN or _sl.is_stdlib(nm)):
            continue
        out.setdefault(nm, getattr(c, "line", None))
    return sorted(out.items(), key=lambda kv: (kv[1] or 0))


def check_module(tu_src: str, ai_src: Optional[str], module: str = "<module>",
                strict: bool = False, overflow: str = "off",
                has_tu: bool = True) -> CheckResult:
    """检查一个模块。`has_tu=False` 表示**只有 `.eln`、没有 `.tu`**（规格 §8.9）。

    `has_tu` 只影响 F-1（实现无 spec）的报法：没有 `.tu` 时"每个函数都没有
    spec"是必然的，逐条报只会把 `check` 的输出淹掉——改为汇总一条 info。
    其余检查（签名、effects、类型…）都只在有 `spec` 时才跑，天然为空。
    """
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
                                    d.line, d.col, d.start, d.end,
                                    severity=getattr(d, "severity", "error")))

    ai = None
    if ai_src is not None:
        ai = parse_source(ai_src, module + ".eln")
        for d in ai.diagnostics:
            res.diagnostics.append(Diag(d.code, "[.eln] " + d.message,
                                        d.line, d.col, d.start, d.end,
                                        severity=getattr(d, "severity", "error")))

    # **E18（方案 B）**：未声明的外部调用 → 报错，而不是悄悄记 1。
    # 记 1 等于把被调函数体当成 0 成本；对一个可能跑 10⁶ 步的调用，κ 就不再是上界。
    for _nm, _ln in unknown_calls(tu.module if tu else None,
                                  ai.module if ai is not None else None):
        res.diagnostics.append(Diag(
            "E-Call-Undeclared",
            f"调用了未声明的外部函数 `{_nm}`：它的成本无法推导，"
            f"而「记 1 个操作」**不是上界**（E18）。"
            f"在 spec 里声明一行：`external {{ {_nm}: O(n); }};`（把 `O(n)` 换成它真实的界）",
            _ln or 1, 1, severity="error"))

    tu_items = tu.module.children if tu.module else []
    ai_items = ai.module.children if (ai is not None and ai.module is not None) else []

    # ---- `guarantees`：可检查 / 不可检查（v0.72；2026-11-09 加严）
    #
    # 以前 `guarantees` 是"能写、没人看"——一个**假的承诺位**。
    # 现在分两类：可检查的进运行时校验（`I-Guarantee-Runtime`），
    # 不可检查的**必须报 error**——承诺位不能写一个永远不会生效的谓词
    # （以前只是 warning，于是 `run` 静默跳过它）。
    #
    # 2026-11-09 堵的洞：用户在 `.eln` 里写一个**恒真的** `is_sorted`，
    # 就能把 `guarantees is_sorted(xs)` 架空（`_call` 用户函数优先）。
    # 现在求值只走内置谓词（见 `Interp._eval_guarantee_expr`）；但同名的
    # 用户函数留在模块里会让读者误以为承诺调的是它——所以这里报 error，
    # 要求改名。**同名的普通函数只要没被 guarantees 用到，仍完全合法。**
    try:
        import guarantees as _gt
        _impls = {c.name: c for c in ai_items if getattr(c, "kind", "") == "fn"}
        _pred_used: Set[str] = set()        # 本模块 guarantees 里用到的谓词名
        for _it in tu_items:
            if _it.kind != "spec" or not _gt.has_guarantees(_it):
                continue
            _ok, _bad = _gt.classify(_it)
            for _e in _ok:
                # 谓词调用可能嵌在 `not …` 里，所以要**递归**收集。
                for _c in walk(_e, "call"):
                    _pred_used.add((_c.name or "").split(".")[-1])
            if _bad:
                # **块形 `guarantees { e1; e2; }` 要指到具体哪一条**（2026-11-11）：
                # 展开后的每条断言各是一个 clause，parser 在表达式上挂了
                # `clause_no`。这里用同一个分类器（`_gt._unsupported`）找出
                # 第一条不可检查的表达式，消息就能说「第 k 条」。
                _first_bad = None
                _body = next((c for c in (_it.children or [])
                              if getattr(c, "kind", "") == "spec-body"), None)
                for _cl in (_body.children if _body else []):
                    if getattr(_cl, "kind", "") != "clause" \
                            or (getattr(_cl, "name", "") or "") != "guarantees":
                        continue
                    for _e in (getattr(_cl, "children", None) or []):
                        if _gt._unsupported(_e):
                            _first_bad = _e
                            break
                    if _first_bad is not None:
                        break
                _no = getattr(_first_bad, "clause_no", 0) if _first_bad else 0
                _at = (f"（第 {_no} 条，@{getattr(_first_bad, 'line', 0)} 行）"
                       if _no else "")
                _bad_line = (getattr(_first_bad, "line", 0) if _first_bad else 0) \
                    or _guarantees_line(_it)
                res.diagnostics.append(Diag(
                    "E-Guarantee-Uncheckable",
                    f"{_it.name} 的 guarantees{_at} 无法检查：{_bad[0]}。"
                    f"**不可检查的承诺永远不会生效**，所以这是 error 不是 warning——"
                    f"请改写，或把这行注释掉（`assume` 已移除）",
                    _it.line, _it.col, severity="error",
                    actions=[Restart(
                        # **以前这里写着字面占位符 `<原来的条件>`**：真应用了
                        # 就是往文件里写这行字。现在给出**真实的那一行**。
                        "edit", "把这行 guarantees 注释掉（旧的「降级成 assume」已随 assume 一起移除）",
                        # **行号取 `guarantees` 子句那一行，不是 spec 那一行**——
                        # 取 spec 行会改到 `spec a(...) {` 上去（实测踩过）。
                        patch={"file": (module or "a") + ".tu",
                               "line": _bad_line,
                               "text": _demote_guarantees(tu_src, _bad_line)})]))
                continue
            if not _ok:
                continue
            # ---- 谓词实参里的名字必须**静态可见**（形参 / ret / 局部）
            #
            # `guarantees` 在函数帧里求值，所以局部变量也够得着；但拼错
            # 形参名（`is_sorted(ys)`）以前 `check` 一声不吭、`run` 才炸。
            _visible = _guarantee_visible_names(_it, _impls.get(_it.name))
            _miss = sorted({_n for _e in _ok for _n in _guarantee_refs(_e)}
                           - _visible)
            if _miss:
                res.diagnostics.append(Diag(
                    "E-Guarantee-Uncheckable",
                    f"{_it.name} 的 guarantees 引用了未定义的名字 "
                    f"`{'`, `'.join(_miss)}`：既不是形参、也不是 `ret`/`result`，"
                    f"实现体里也没有声明（运行期求值会直接失败）",
                    _it.line, _it.col, severity="error"))
                continue
            # **`T!` 上的 guarantees 语义（2026-11-09）**：`run` 会先解包
            # `Ok(值)` 再判谓词（`ret` 指向 Ok 里的值），`Err` 时跳过。
            # 消息里写明这一点，免得作者以为 `ret` 是 `Ok(...)` 包装壳。
            _f = "（`T!`：`Ok` 时对解包后的值校验，`Err` 时跳过）" \
                if (_it.value or {}).get("fallible") else ""
            res.diagnostics.append(Diag(
                "I-Guarantee-Runtime",
                f"{_it.name} 的 guarantees（{_gt.describe(_ok)}）"
                f"将在 run 时校验{_f}",
                _it.line, _it.col, severity="info"))
        # ---- 用户函数与「本模块 guarantees 用到的内置谓词」同名 → error
        #
        # 这里**只**在名字真的被 guarantees 用到时报：用户需要一个同名的
        # 普通函数（不用于承诺）是合法用法，不能一刀切禁掉。
        for _fn in ai_items:
            if getattr(_fn, "kind", "") != "fn":
                continue
            _nm = (_fn.name or "").split(".")[-1]
            if _nm in _pred_used and _nm in _gt.CHECKABLE:
                res.diagnostics.append(Diag(
                    "E-Guarantee-Uncheckable",
                    f"`{_nm}` 是**语言内置谓词名**，而本模块的 guarantees 用了它："
                    f"后置条件只会调用内置谓词，**不会调用**你写的 `{_nm}`"
                    f"（承诺的语义不能由用户实现顶替）。请给你的函数改名",
                    _fn.line, _fn.col, severity="error"))
    except Exception:                                      # noqa: BLE001
        pass        # 分类失败不该让整个检查崩掉

    # ---- 严格模式：L3 / L4 / L5
    if strict:
        res.diagnostics.extend(strict_lints(tu_src, tu, ".tu"))
        if ai is not None and ai_src is not None:
            res.diagnostics.extend(strict_lints(ai_src, ai, ".eln"))

    # ---- F-4 / F-8：.eln 不得有声明与 use
    #
    # **P0 兼容层（2026-11-11）**：`--compat` 打开时 `struct` 是**接受的拼写**
    # （`.cpp` 改名 `.eln` 就能用；C++ 里 struct 本来就和实现写在同一个文件），
    # 但只降成 warning——`--strict` 仍按 F-4 判 error，因为"声明在 .tu、
    # 实现在 .eln"是这门语言的分工，不是可以默认丢掉的东西。
    # 其余种类（`const` / `enum` / `spec` / `choose` / `use` / `error-decl`）
    # **一条都不放开**。
    ILLEGAL_IN_AI = {"const", "enum", "struct", "spec", "choose",
                     "use", "error-decl"}
    from compat import enabled as _compat_on
    _compat = _compat_on()
    for it in ai_items:
        if it.kind in ILLEGAL_IN_AI:
            code = "E-Ai-HasUse" if it.kind == "use" else "E-Ai-HasDecl"
            if _compat and it.kind == "struct" and not strict:
                res.diagnostics.append(Diag(
                    "W-Compat-StructInEln",
                    f"`.eln` 里声明了 `struct {it.name or ''}`：`--compat` 接受它"
                    f"（C++ 的程序本来就把 struct 和实现写在一个文件里），"
                    f"但本语言的分工是「声明在 `.tu`、实现在 `.eln`」——"
                    f"要长期维护就把这个 struct 挪到 `.tu`；"
                    f"`--strict` 仍会按 F-4 拒绝。",
                    it.line, it.col, severity="warning"))
                continue
            res.diagnostics.append(Diag(
                code, f".eln 中不得出现 {it.kind}（F-4/F-8）", it.line, it.col))

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
                    it.line, it.col,
                    actions=_acts(_line_patch(
                        tu_src, (module or "a") + ".tu", it.line,
                        lambda s: "// " + s.strip() + "   // ← 重复的 error 声明",
                        "把重复的那一行注释掉（保留第一个）"))))
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

    # ---- repeats：名字必须可解析 + 声明必须有消费者（2026-11-09）
    #
    # 以前 `repeats` 是**纯声明**：`check` 不看、`run` 不看，只有 `cost` 读。
    # 于是 `repeats alpha(n)` 静默通过（κ 退 ⊤），`repeats` 写在一个不当
    # while 条件的函数上永远不生效——两种都是"写了等于没写，还没有任何提示"。
    # 运行期的断言见 `Interp._repeats_limit`。
    try:
        _check_repeats(res, tu_items, impls, specs, module)
    except Exception:                                      # noqa: BLE001
        pass        # 静态检查自身出问题不该让整个 check 崩掉

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
                    "E-Clause-Unknown", f"未知子句 {c.name}", c.line, c.col,
                    actions=_acts(_line_patch(
                        tu_src, (module or "a") + ".tu", c.line,
                        lambda s: "// " + s.strip() + "   // ← 未知子句，已注释",
                        "把这一行注释掉（保留痕迹，不直接删）"))))
            if c.kind == "clause" and c.name == "target":
                # **未知成本维度**：`target { peak: … }` 以前会被**静默接受**——
                # 写了维度却什么都不检查，等于一个看不见的空承诺（G8 的教训）。
                _KNOWN_DIMS = {"time", "allocation"}
                for dim in c.children:
                    if dim.kind == "dim" and (dim.name or "") not in _KNOWN_DIMS:
                        res.diagnostics.append(Diag(
                            "E-Target-Dim-Unknown",
                            "成本维度 %s 还没实现（现有：time / allocation）。" % dim.name
                            + "峰值与栈占用是有意不做，理由见 文档/设计/未来设计/挂起-G8峰值与栈.md",
                            dim.line, dim.col))
                for dim in c.children:
                    for slot in dim.children:
                        if slot.kind == "slot" and slot.name not in ("min", "max", "exact"):
                            _bad = slot.name or ""
                            res.diagnostics.append(Diag(
                                "E-Target-BadSlot",
                                f"target 槽位只能是 min/max/exact，实际 {slot.name}",
                                slot.line, slot.col,
                                actions=_acts(_target_slot_fix(
                                    tu_src, (module or "a") + ".tu", slot.line, _bad))))

    # ---- F-1：实现必须有声明
    #
    # **2026-11-10 起 `.tu` 可选**（规格 §8.9 / R1-B）。产品目标是「一个 C++ 程序
    # 只改文件名就能当 `.eln` 用」，而真实 C++ 程序**没有 `.tu`**。
    # 语义因此定死：**没有 spec = 没有契约声明 = 不报错**。
    #   · 有 `.tu`、只是某个函数漏了 spec → `warning`（提示契约缺口，不拦 run/build）
    #   · 根本没有 `.tu` 文件（`has_tu=False`）→ 不逐条报，只汇总一条 info
    #   · `--strict`（`strict=True`）→ 保持旧行为：`error`（我们自己仓库自检用）
    # 注意**不要**把 warning 叫成"通过"：`check` 的结论仍是「N 条警告」，
    # `cost` 会把它们记成「N 个函数未声明契约」（不算债务、不算错误）。
    _no_spec = [(name, fn) for name, fn in impls.items()
                if name not in specs and name not in kernels_decl]
    if _no_spec and not has_tu and not strict:
        res.diagnostics.append(Diag(
            "I-Fn-NoSpec-Module",
            f"本模块没有 .tu，成本契约不参与判定"
            f"（{len(_no_spec)} 个函数未声明契约；要契约就补 <模块>.tu，"
            f"仓库自检用 --strict）",
            1, 1, severity="info"))
    else:
        for name, fn in _no_spec:
            # 只有"真的没有 .tu"才走汇总；有 .tu 时逐条指明是哪个函数漏了。
            res.diagnostics.append(Diag(
                "E-Fn-NoSpec",
                f"函数 {name} 在 .tu 中没有对应 spec（F-1）"
                + ("" if strict else "——没有 spec 就没有契约声明，不阻断"),
                fn.line, fn.col,
                severity="error" if strict else "warning",
                actions=[Restart("edit", "在 .tu 末尾插入 spec 骨架（target 要你自己填）",
                                 patch={"file": (module or "a") + ".tu",
                                        "line": len((tu_src or "").splitlines()) + 1,
                                        "text": _spec_skeleton(ai_src, fn)})]))

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
        a = [(n, _canon_type_str(ty)) for n, ty in a]
        b = [(n, _canon_type_str(ty)) for n, ty in b]
        if not _params_compatible(a, b, _tparams):
            res.diagnostics.append(Diag(
                "E-Spec-SigMismatch",
                f"{name} 的参数类型不一致：.tu=({', '.join(t for _, t in a)}) "
                f"vs .ai=({', '.join(t for _, t in b)})",
                fn.line, fn.col))
            continue
        # **`const` 限定符也必须一致**（2026-11-09，`E-Spec-ConstMismatch`）。
        # `param_list` 只比**类型字符串**，而 `const` 记在 `param.value` 上——
        # 于是 `.tu` 写 `const vector<u64>& xs`、`.eln` 写 `vector<u64>& xs`
        # 被判成同一个签名，check 干净通过，run 把**调用方的向量**改了
        # （实测 `xs.push(99)` 后调用方长度 3 → 4）。这是 const 的第二种撒谎方式：
        # 契约说只读、实现却可写，两个文件各自看都"没问题"。
        _sp = list((_params_of(spec).children if _params_of(spec) else []))
        _ip = list((_params_of(fn).children if _params_of(fn) else []))
        for _i, (_pn, _pt) in enumerate(a):
            if _i >= len(_sp) or _i >= len(_ip):
                break
            _cs = isinstance(_sp[_i].value, dict) and bool(_sp[_i].value.get("const"))
            _ci = isinstance(_ip[_i].value, dict) and bool(_ip[_i].value.get("const"))
            if _cs == _ci:
                continue
            _nm = _pn or _ip[_i].name or f"第 {_i + 1} 个形参"
            _tu_ty = type_str(_sp[_i].children[0]) if _sp[_i].children else _pt
            _ai_ty = type_str(_ip[_i].children[0]) if _ip[_i].children else _pt
            if _cs:
                _msg = (f"{name} 的形参 `{_nm}` 两边限定符不一致："
                        f".tu 是只读借用 `const {_tu_ty}`，.eln 是可变借用 `{_ai_ty}`——"
                        f"被调方可以改调用方的数据。两边的 `const` MUST 一致"
                        f"（要改数据就两边都去掉 `const`，只读就两边都写 `const`）。")
            else:
                _msg = (f"{name} 的形参 `{_nm}` 两边限定符不一致："
                        f".tu 是可变借用 `{_tu_ty}`，.eln 是只读借用 `const {_ai_ty}`——"
                        f"契约说可以改、实现却不改。两边的 `const` MUST 一致。")
            res.diagnostics.append(Diag(
                "E-Spec-ConstMismatch", _msg,
                _ip[_i].line or fn.line, _ip[_i].col or fn.col))
        # **形参名也必须一致**（2026-11-09，`E-Spec-ParamName`）。
        #
        # `target` / `requires` 里的名字是按 **spec** 写的，而 `cost` 的 κ、
        # `run` 的函数帧用的是 **实现** 的名字：`.tu` 写 `xs`、`.eln` 写 `ys`
        # 时签名（只比类型）看着一致，契约却判不了——`check` 一声不吭，
        # `cost` 才给 E23「尺寸量不一致」，`run` 才报「未绑定的名字 xs」。
        # 名字不一致本身是**静态可判**的，所以要在 check 阶段报。
        #
        # **不误报的边界**：只有 spec 里有子句**按名字引用了这个形参**才报
        # （`O(1)` 的 target、纯 `effects {}` 的 spec 都不报——那时名字无关紧要）；
        # 没有实现文件时 F-2 根本不会进来。
        #
        # `guarantees` 有一半已被 `E-Guarantee-Uncheckable` 覆盖（引用了实现帧里
        # 看不见的名字），这里只补它漏掉的**影子**情形：spec 名恰好被实现的
        # 局部变量顶掉，guarantee 会静默绑到错的那个值。
        _refs: Dict[str, List[str]] = {}
        _spell: Dict[str, Set[str]] = {}
        _vis: Optional[Set[str]] = None
        for _cl in _clauses(spec):
            _cn = _cl.name or ""
            if _cn not in _NAME_SENSITIVE_CLAUSES:
                continue
            _hit = _clause_param_refs(_cl, _sp)
            if _cn == "guarantees":
                if _vis is None:
                    _vis = _guarantee_visible_names(spec, fn)
                _hit = {_n: _s for _n, _s in _hit.items() if _n in _vis}
            for _n, _s in _hit.items():
                _refs.setdefault(_n, []).append(_cn)
                _spell.setdefault(_n, set()).update(_s)
        for _i, (_pn, _pt) in enumerate(a):
            if _i >= len(_ip) or not _pn or _pn not in _refs:
                continue
            _in = _ip[_i].name or ""
            if _in == _pn:
                continue
            _where = "、".join(sorted(set(_refs[_pn])))
            _in_disp = f"`{_in}`" if _in else "（没有名字）"
            # 引用它时用的拼写：通常是形参名本身；`O(n)` 的魔法绑定是 `n`。
            _used = "、".join(f"`{s}`" for s in sorted(_spell.get(_pn) or {_pn}))
            _hurt: List[str] = []
            if "target" in _refs[_pn]:
                _hurt.append("`cost` 会报 E23「尺寸量不一致」")
            if "requires" in _refs[_pn]:
                _hurt.append("`run` 会报「未绑定的名字」")
            if "guarantees" in _refs[_pn]:
                _hurt.append("`run` 求值时会静默绑到错的那个值")
            _why = ("（" + "；".join(_hurt) + "）") if _hurt else ""
            _msg = (f"{name} 的 spec 形参叫 `{_pn}`，实现叫 {_in_disp}："
                    f"{_where} 里的名字按 spec 写，两边不一致时契约判不了{_why}。"
                    f"把两边改成同一个名字——改 `.tu` 的 `{_pn}`，"
                    f"或把 `{_where}` 里的 {_used} 改成 `{_in or '…'}`。")
            res.diagnostics.append(Diag(
                "E-Spec-ParamName", _msg,
                _ip[_i].line or fn.line, _ip[_i].col or fn.col))
        ra, rb = type_str(_return_of(spec)), type_str(_return_of(fn))
        ra, rb = _canon_type_str(ra), _canon_type_str(rb)
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
                        c.line, c.col,
                        actions=_acts(_line_patch(
                            tu_src, (module or "a") + ".tu", c.line,
                            lambda s, o=objective: _ren_obj(s, o),
                            "把 objective 改成 `time`（最常用的那个）"))))

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

    # **没有 `.tu` 时的状态表**：以前只从 spec 生成，于是 `check bare.eln`
    # 明明有函数却打「✓ 通过（0 个函数）」——和同一份输出里的
    # 「N 个函数未声明契约」自相矛盾。这里把"没有声明的实现"补进表里，
    # state 记 unchecked（没有契约可判），**不改任何判定**。
    if not has_tu:
        for name, fn in impls.items():
            if name in specs or name in kernels_decl:
                continue
            st, _note = derive_state(True, False)
            res.functions.append(FnInfo(
                name=name, kind="fn", has_impl=True, has_target=False,
                is_pub=bool((fn.value or {}).get("pub")),
                line=fn.line, state=st, note="未声明契约（没有 .tu）"))

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
    # `.eln` 的文件名：用模块名拼（`check_module` 拿不到路径）。
    _check_while_bounds(res, all_fns, specs, strict,
                        ai_src=ai_src, fn_file=(module or "a") + ".eln")
    _check_sort_calls(res, all_fns)

    # ---- C11-b：递归必须有 `recursion depth` 声明（否则 κ 沉默地偏小）
    res.diagnostics.extend(check_recursion_depth(impls, specs))

    # ---- W-Exp-Risk：树递归 ⇒ 「最坏可能指数级」的**风险提示**（warning，不阻断）
    # 判定复用 `cost` 的 `_self_call_shape`（不另写一套）；只在深度已声明时报。
    res.diagnostics.extend(check_exp_risk(
        ai.module if ai is not None else None,
        tu.module if tu is not None else None))

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

    # **大括号初始化的元素个数与定长容量不符**
    # 常量（`const LCS_CAP: u64 = 4096;`）声明在 `.tu` 里，所以两个模块的常量都要看。
    res.diagnostics.extend(_check_list_init_arity(
        ai.module if ai is not None else None,
        tu.module if tu is not None else None))

    # **常量下标越界**：容量与下标都在编译期已知，不留到运行期
    res.diagnostics.extend(_check_const_index(
        ai.module if ai is not None else None,
        tu.module if tu is not None else None))

    # **结构体没有 `new`**：以前静默造出一个 EnumVal
    res.diagnostics.extend(_check_struct_new(ai.module if ai is not None else None,
                                             tu.module if tu is not None else None))

    # **未实现的容器类型**：`deque<T>` 以前 check 过、run 才炸（默认值退化成 0）
    _tu_mod = tu.module if tu is not None else None
    for _mod in (_tu_mod, ai.module if ai is not None else None):
        res.diagnostics.extend(_check_unimplemented_containers(_mod, _tu_mod))

    # **`unordered_*` 的键必须可哈希**：`unordered_map<vector<u8>, u64>` 以前
    # check 干净通过、run 才 `TypeError`（Python `dict` 的键要求）。
    for _mod in (_tu_mod, ai.module if ai is not None else None):
        res.diagnostics.extend(_check_unhashable_keys(_mod, _tu_mod))

    # **`const T&` 不许被改写**：`sort(xs)` / `xs[0] = …` / `xs.push(…)` 以前
    # check 干净通过，run 把**调用方的数据**改掉（或 run 期才报 E-Borrow-Const）。
    res.diagnostics.extend(_check_const_mutation(
        ai.module if ai is not None else None, _tu_mod))

    # **定长数组套容器**：`array<array<u64, 2>, 2>` 的内层元素是裸整数 0
    res.diagnostics.extend(_check_nested_containers(
        _tu_mod, ai.module if ai is not None else None))

    # **target 的成本式认不出**：以前 check 说没事、cost 说没有 target
    res.diagnostics.extend(_check_target_form(tu.module if tu is not None else None))

    # **`sizes` 子句**：只能绑形状量，且必须真被 target 用上（G1-b）。
    # 以前它能解析、却没有任何消费者——写了等于没写。
    res.diagnostics.extend(_check_sizes(tu.module if tu is not None else None))


    # **requires 调用点验证**：证明不了就报错（不靠运行期断言兜底）
    #
    # **`W-` 开头的一律按 warning 收**（2026-11-12）：`requires` 里用旧拼写
    # `.length()` 时这里会捎一条 `W-Builtin-Deprecated`；Diag 默认 severity
    # 是 error，照默认收会把一条"建议改名"变成阻断构建的错误。
    import requires as _requires
    for _c, _m, _ln, _col in _requires.check(tu.module if tu else None,
                                             ai.module if ai is not None else None):
        res.diagnostics.append(Diag(_c, _m, _ln, _col,
                                    severity=("warning" if _c.startswith("W-")
                                              else "error")))

    # **`.tu` 侧的弃用拼写**（2026-11-13）：`sizes` / `target` / `benchmodel` /
    # `repeats` / `guarantees` / `requires` 的表达式**不进 `typecheck`**，于是
    # 同一条 `.length()` 在 `.eln` 里报、在 `.tu` 里静默——同一个弃用两种待遇。
    # 只报 warning（`.length()` 仍能跑，与 `.eln` 侧同一口径）。
    #
    # **与 `requires.py` 去重**：`requires` 的调用点检查已经会为**确实被调用**的
    # spec 报同一条（位置是那个 `.length()` 节点的 `.`）；这里只补它没覆盖到的
    # （没有调用点、或条件不是纯比较式）。两处取的是**同一个 AST 节点**，所以
    # 位置只差一列（本函数按方法名起始列报，它按 `.` 报）——两个键都查。
    _dep_seen = {(d.line, d.col) for d in res.diagnostics
                 if d.code == "W-Builtin-Deprecated"}
    for _d in _check_tu_deprecated_spelling(tu.module if tu is not None else None):
        if (_d.line, _d.col) in _dep_seen or (_d.line, _d.col - 1) in _dep_seen:
            continue
        _dep_seen.add((_d.line, _d.col))
        res.diagnostics.append(_d)
    return res


def _check_list_init_arity(mod, decl_mod=None) -> List[Diag]:
    """**`array<T, N> xs = {…}` 的元素个数 MUST 等于 N**（`E-Array-InitLen`）。

    大括号初始化允许作者直接写出初值；但定长数组的长度是类型的一部分，
    少写多写都不是"能跑就行"——那是**声明与初值不一致**，必须在检查期挡住。

    **容量能静态求出就照常比较**（字面量、模块级 `const`、它们的简单算术）；
    **求不出来时给一条 warning**（`W-Array-InitLen-Unknown`），
    不再静默跳过——静默等于"以为查了其实没查"。
    """
    if mod is None:
        return []
    consts = _const_int_values(decl_mod)      # `.tu` 里的 const 是容量来源
    consts.update(_const_int_values(mod))     # 实现文件里的（若有）也认
    diags: List[Diag] = []
    for n in _all_nodes(mod):
        if getattr(n, "kind", "") not in ("var-decl", "const-decl", "val-decl"):
            continue
        if not n.children:
            continue
        init = n.children[-1]
        if getattr(init, "kind", "") != "list-init":
            continue
        ty = next((c for c in n.children
                   if getattr(c, "kind", "") in ("type-name", "type-ref")), None)
        if ty is None:
            continue
        if getattr(ty, "kind", "") == "type-ref" and ty.children:
            ty = ty.children[0]
        head = (getattr(ty, "name", "") or "").strip().lower()
        if head not in ("array", "fixedvec"):
            continue
        cap = _type_capacity_expr(ty, consts)
        got = len(init.children)
        if cap is None:
            diags.append(Diag(
                "W-Array-InitLen-Unknown",
                f"`{n.name}` 的容量无法静态求出，**没有校验**元素个数"
                f"（这里给了 {got} 个）",
                n.line, n.col, severity="warning"))
            continue
        if got != cap:
            diags.append(Diag(
                "E-Array-InitLen",
                f"`{n.name}` 声明的是定长数组，容量 {cap}，"
                f"但大括号里给了 {got} 个元素（MUST 相等）",
                n.line, n.col))
    return diags


def _const_int_values(mod) -> dict:
    """模块级 `const u64 NAME = <int>;` 的值表（支持 `N*2` 这类简单算术）。

    复用 `_int_of_expr`：字面量、常量名、`+ - * / %`、括号。
    **刻意不做**：函数调用、循环、字符串——求不出就返回 None，
    由调用方给出 `W-Array-InitLen-Unknown`，而不是猜一个数。
    """
    consts: dict = {}
    for item in getattr(mod, "children", []):
        if getattr(item, "kind", "") != "const" or not getattr(item, "name", None):
            continue
        init = item.children[-1] if item.children else None
        v = _int_of_expr(init, consts)
        if v is not None:
            consts[item.name] = v
    return consts


def _int_of_expr(n, consts: dict) -> Optional[int]:
    """尽力求一个整数常量：字面量 / 常量名 / 四则与取模。求不出返回 None。"""
    if n is None:
        return None
    k = getattr(n, "kind", "")
    if k in ("lit-int", "type-const"):
        try:
            return int(n.value)
        except (TypeError, ValueError):
            return None
    if k in ("path", "type-name"):
        nm = (getattr(n, "name", "") or "").strip()
        if nm.isdigit():
            return int(nm)
        return consts.get(nm)
    if k == "paren" and n.children:
        return _int_of_expr(n.children[0], consts)
    if k == "binary" and len(n.children) >= 2:
        a = _int_of_expr(n.children[0], consts)
        b = _int_of_expr(n.children[1], consts)
        if a is None or b is None:
            return None
        op = n.value
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op in ("/", "%") and b != 0:
            return a // b if op == "/" else a % b
        return None
    return None


def _type_capacity_expr(ty, consts: dict) -> Optional[int]:
    """从 `array<T, N>` 的 type-name 节点求容量：字面量 / 常量 / 简单算术。"""
    for c in ty.children:
        if getattr(c, "kind", "") != "type-args":
            continue
        for a in c.children:
            v = _int_of_expr(a, consts)
            if v is not None:
                return v
    return None


def _type_capacity_literal(ty) -> Optional[int]:
    """从 `array<T, N>` 的 type-name 节点取字面量 N；常量名/表达式返回 None。"""
    for c in ty.children:
        if getattr(c, "kind", "") != "type-args":
            continue
        for a in c.children:
            if getattr(a, "kind", "") == "type-const":
                try:
                    return int(a.value)
                except (TypeError, ValueError):
                    return None
            if getattr(a, "kind", "") == "type-name":
                nm = (a.name or "").strip()
                if nm.isdigit():
                    return int(nm)
    return None


def _check_const_index(mod, decl_mod=None) -> List[Diag]:
    """**常量下标越界在 check 阶段报**（`E-Index-OutOfRange`）。

    `array<u64,4> a; a[9] = 99;` 以前 check 干净通过、run 才 `IndexError`。
    定长容器的容量是类型的一部分，下标又是常量——**两个都在编译期已知**，
    没有理由留到运行期。

    只对**容量能求出**的定长容器判定；`vector` 与未知容量不判定。
    """
    if mod is None:
        return []
    consts = _const_int_values(decl_mod)
    consts.update(_const_int_values(mod))
    diags: List[Diag] = []
    for fn in _all_nodes(mod):
        if getattr(fn, "kind", "") != "fn":
            continue
        caps: Dict[str, int] = {}
        for n in _all_nodes(fn):
            if getattr(n, "kind", "") not in ("var-decl", "const-decl"):
                continue
            ty = next((c for c in n.children
                       if getattr(c, "kind", "") in ("type-name", "type-ref")), None)
            if ty is None:
                continue
            if getattr(ty, "kind", "") == "type-ref" and ty.children:
                ty = ty.children[0]
            head = (getattr(ty, "name", "") or "").strip().lower()
            if head not in ("array", "fixedvec"):
                continue
            cap = _type_capacity_expr(ty, consts)
            if cap is not None and n.name:
                caps[n.name] = cap
        if not caps:
            continue
        for n in _all_nodes(fn):
            if getattr(n, "kind", "") != "index" or len(n.children) < 2:
                continue
            base, idx = n.children[0], n.children[1]
            if getattr(base, "kind", "") != "path":
                continue
            cap = caps.get(base.name)
            if cap is None:
                continue
            v = _int_of_expr(idx, consts)
            if v is None or v < cap:
                continue
            diags.append(Diag(
                "E-Index-OutOfRange",
                f"`{base.name}` 的容量是 {cap}，常量下标 {v} 越界"
                f"（合法范围 0..{cap - 1}）",
                n.line, n.col))
    return diags


def _check_struct_new(mod, decl_mod=None) -> List[Diag]:
    """**结构体没有 `new`**（`E-Type-NoSuchMethod`）。

    `Point p = Point::new(3, 4);` 以前 check 静默通过，run 把它当"枚举构造"
    造出一个 `Point.new((3, 4))` 的值——**契约语言里最不该有的静默**：
    程序"跑通"了，语义完全是另一个东西。

    规则：`T::m(...)` 里 T 是**结构体**时，构造只有 `T { 字段: … }` 一种写法。
    """
    if mod is None:
        return []
    # **结构体声明在 `.tu` 里**（`.eln` 侧不写 struct），所以两个模块都要扫
    structs = {n.name: n for n in _all_nodes(decl_mod) if getattr(n, "kind", "") == "struct"}
    structs.update({n.name: n for n in _all_nodes(mod) if getattr(n, "kind", "") == "struct"})
    if not structs:
        return []
    diags: List[Diag] = []
    for n in _all_nodes(mod):
        if getattr(n, "kind", "") != "call":
            continue
        nm = n.name or ""
        # **解析器把 `Point::new` 归一成 `Point.new`**（实测 AST：
        # `call name='Point.new'`）。两种写法都要认，否则这条检查等于没写。
        sep = "::" if "::" in nm else ("." if "." in nm else "")
        if not sep:
            continue
        ty, _, meth = nm.partition(sep)
        st = structs.get(ty.strip())
        if st is None:
            continue
        fields = [f.name for f in st.children if getattr(f, "name", None)]
        hint = ", ".join(f"{f}: …" for f in fields) if fields else "…"
        diags.append(Diag(
            "E-Type-NoSuchMethod",
            f"结构体 `{ty}` 没有方法 `{meth}`；构造请写 `{ty} {{ {hint} }}`",
            n.line, n.col))
    return diags


def _check_unimplemented_containers(mod, decl_mod=None) -> List[Diag]:
    """**没实现的容器类型不许过 check**（`E-Type-UnimplementedContainer`）。

    `deque` 的情况**不是"完全没写"**（2026-11-08 查清）：
    `stdlib.py` 里有 `DequeVal`（环形缓冲）和 `deque_new` / `deque_push_*` /
    `deque_pop_*` / `deque_len` 一组内建，**内建路径能跑**；
    但 `interp.py` 的**容器构造分支**（`_default_value_of` 默认值、
    `deque<T>::new()`、`.push()` 等方法分派）**没有 deque**。
    结果是 `deque<u64> d;` 被兜底成整数 `0`，`d.push(1)` 在 run 期
    才炸 `object of type 'int' has no len()`——**check 说没事、run 才炸**。

    所以检查期直接拒绝 `deque<T>`，并列出已实现的容器（作者要知道替代品）。
    `deque_new()` 内建虽然能跑，但声明它的类型同样会被这条挡住：**容器类型
    没有接线的半边不能只靠内建补**，否则又回到"一半能用"的状态。
    """
    if mod is None:
        return []
    user_types = {canon(getattr(n, "name", "") or "")
                  for n in _all_nodes(decl_mod or mod)
                  if getattr(n, "kind", "") in ("struct", "enum")}
    user_types |= {canon(getattr(n, "name", "") or "")
                   for n in _all_nodes(mod)
                   if getattr(n, "kind", "") in ("struct", "enum")}
    diags: List[Diag] = []
    for n in _all_nodes(mod):
        if getattr(n, "kind", "") != "type-name":
            continue
        head = canon(getattr(n, "name", "") or "")
        if head in user_types or head not in UNIMPLEMENTED_CONTAINERS:
            continue
        why = UNIMPLEMENTED_CONTAINERS[head]
        diags.append(Diag(
            "E-Type-UnimplementedContainer",
            f"容器 `{n.name}` 尚未实现：{why}。"
            f"已实现的容器：{' / '.join(IMPLEMENTED_CONTAINERS)}。"
            f"要两端增删，用 `vector` + 头尾两个索引。",
            n.line, n.col))
    return diags


#: **已实现**的内建容器：`E-Type-UnimplementedContainer` 的消息里要列全。
IMPLEMENTED_CONTAINERS = ("vector", "array", "map", "set",
                          "unordered_map", "unordered_set", "stack", "queue")

#: **没有接线**的容器类型名 → 缺的是什么（方案/理由见检查函数 docstring）。
UNIMPLEMENTED_CONTAINERS = {
    "deque": "环形缓冲只在 `deque_new()` / `deque_push_*` 内建里，"
             "容器构造（`deque<T> d;` / `deque<T>::new()`）与方法（`d.push(1)`）没有接线",
}


def _check_unhashable_keys(mod, decl_mod=None) -> List[Diag]:
    """**`unordered_map` / `unordered_set` 的键必须可哈希**（`E-Type-UnhashableKey`）。

    **2026-11-12 起，容器类键放开**：`vector<T>` / `array<T,N>`（含
    `vector<u8>` / `Text`）可以做键。哈希按**元素逐个混入**（FNV-1a，
    `stdlib.hash_key`），冲突后按**逐元素相等**比较；代价是
    `get/insert/remove` 为 **O(n + 键长)**，不是 O(1)（κ 已按容器键加 `n(键)` 项，
    见 `文档/语法清单.md` 与 `文档/内建函数.md`）。

    **仍拒绝**：

    - `stack` / `queue`：运行时也是 `list`，但 C++ 的
      `std::hash<std::stack>` / `std::hash<std::queue>` 不存在，语义也没有
      "按元素"的共识——不假装支持。
    - `map` / `set` / `unordered_*`：值只有对象身份，没有逐值相等；
      拿它当键会把内容相同的两块表当成两个键——语义是错的。
    - `struct`：没有字段哈希契约（字段可能含容器/浮点），C++ 的
      `std::hash<struct>` 也不存在。要当键请把字段编码成标量或 `vector<...>`。

    **判断依据是运行期实际值**（`interp.py`）：`vector` / `array` 是 `list`
    子类，现在由 `interp._HashKey` 包装后进 `dict`——哈希 + 逐值相等都成立。
    `struct` 的 `StructVal` 定义了 `__eq__` 却没定义 `__hash__`；`map` / `set`
    连 `__eq__` 都没有（身份语义）。

    **只报证明得了的**：`T` 这样的类型参数、没见过的类型名一律跳过（不猜）。
    同一行上同一键类型只报一次（声明与 `::new()` 会各出现一个 type-name 节点）。
    """
    if mod is None:
        return []
    structs = {canon(getattr(n, "name", "") or "")
               for n in _all_nodes(decl_mod or mod)
               if getattr(n, "kind", "") == "struct"}
    structs |= {canon(getattr(n, "name", "") or "")
                for n in _all_nodes(mod)
                if getattr(n, "kind", "") == "struct"}
    diags: List[Diag] = []
    # sig（行, 容器头, 键类型）→ (列, 行, 消息)。声明与 `::new()` 会各出现一个
    # type-name 节点；取**同一行最靠左**的那个，报在声明位置而不是构造位置。
    cands: Dict[tuple, tuple] = {}
    for n in _all_nodes(mod):
        if getattr(n, "kind", "") != "type-name":
            continue
        head = canon(getattr(n, "name", "") or "")
        if head not in ("unordered_map", "unordered_set"):
            continue
        args = next((c for c in n.children
                     if getattr(c, "kind", "") == "type-args"), None)
        key = next((c for c in (args.children if args is not None else [])
                    if getattr(c, "kind", "") in ("type-name", "type-ref")), None)
        if key is None:
            continue
        why = _unhashable_key_reason(key, structs)
        if why is None:
            continue
        keystr = _type_str_of_node(key)
        sig = (getattr(n, "line", 0) or 0, head, keystr)
        what = "键" if head == "unordered_map" else "元素"
        pos = "键类型" if head == "unordered_map" else "元素类型"
        if why == "list":
            msg = (f"{head} 的{pos} `{keystr}` 不可哈希：`stack` / `queue` 没有"
                   f"逐元素哈希契约（C++ 的 `std::hash<std::stack>` 也不存在）。"
                   f"{what}必须是标量（u64 / i64 / bool / enum）或"
                   f"`vector<...>` / `array<...,N>`。")
            order = "字节字典序" if keystr in ("vector<u8>", "Vec<u8>") else "元素字典序"
            msg += (f"需要容器类{what}请改用 `map`——它只要求全序，"
                    f"`{keystr}` 可按{order}。")
        elif why == "object":
            msg = (f"{head} 的{pos} `{keystr}` 不可哈希：容器值只有对象身份，"
                   f"没有逐值相等（两块内容相同的表会被当成两个键）。"
                   f"{what}必须是标量（u64 / i64 / bool / enum）或"
                   f"`vector<...>` / `array<...,N>`。"
                   f"`{keystr}` 也没有 `<`，`map` 同样不能用；"
                   f"请先把内容编码成 `vector<...>` 或标量再当{what}。")
        else:                                       # struct
            msg = (f"{head} 的{pos} `{keystr}` 不可哈希：结构体没有逐值哈希。"
                   f"{what}必须是标量（u64 / i64 / bool / enum）或"
                   f"`vector<...>` / `array<...,N>`。"
                   f"结构体也没有 `<`，`map` 同样不能用；"
                   f"请把字段编码成标量（或 `vector<...>`）再当{what}。")
        col = getattr(n, "col", 0) or 0
        old = cands.get(sig)
        if old is None or col < old[0]:
            cands[sig] = (col, getattr(n, "line", 0) or 0, msg)
    for _col, _line, _msg in cands.values():
        diags.append(Diag("E-Type-UnhashableKey", _msg, _line, _col))
    diags.sort(key=lambda d: (d.line or 0, d.col or 0))
    return diags


#: 运行时值是 **Python `list`** 的容器（`list.__hash__ is None`）。
LIST_BACKED_CONTAINERS = frozenset({
    "vector", "array", "fixedvec", "stack", "queue",
})

#: **可以**做 `unordered_*` 键的容器（2026-11-12）：哈希按元素混入、相等按元素比较。
#: `vector<u8>`（`Text`）是其中最常见的一个——C++ 的 `unordered_map<string, …>`。
HASHABLE_CONTAINER_HEADS = frozenset({"vector", "array", "fixedvec"})

#: 容器类：值**只有对象身份**，没有逐值哈希/相等（不炸，但拿它当键语义是错的）。
OBJECT_CONTAINERS = frozenset({
    "map", "set", "unordered_map", "unordered_set",
})


def _unhashable_key_reason(ty, structs: set) -> Optional[str]:
    """键类型节点的"不可哈希"类别：`"list"` / `"object"` / `"struct"` / `None`。

    `None` = **不判**（标量、枚举、`optional`、类型参数、没见过的类型名）。
    `optional<T>` 不判：运行期是 `EnumVal`（有 `__hash__`）。
    `deque` 不判：它由 `E-Type-UnimplementedContainer` 负责，别报两遍。
    """
    while getattr(ty, "kind", "") == "type-ref" and getattr(ty, "children", None):
        ty = ty.children[0]
    head = canon(getattr(ty, "name", "") or "")
    if not head:
        return None
    if head in structs:
        return "struct"
    if head in HASHABLE_CONTAINER_HEADS:
        # 2026-11-12：`vector<T>` / `array<T,N>` 由 `interp._HashKey` 提供
        # 「逐元素哈希 + 逐元素相等」，可以做 `unordered_*` 的键。
        return None
    if head in LIST_BACKED_CONTAINERS:
        # 只剩 `stack` / `queue`（它们也在 `LIST_BACKED_CONTAINERS` 里）。
        return "list"
    if head in OBJECT_CONTAINERS:
        return "object"
    return None


# ---------------------------------------------------------------- const 引用

#: **已实现**的容器头。`const T&` 的只读检查对它们（以及 `.tu` 里声明的结构体）生效。
CONST_MUTABLE_HEADS = frozenset({
    "vector", "array", "fixedvec", "map", "set",
    "unordered_map", "unordered_set", "stack", "queue",
})

#: 会改**接收者**的方法名（`E-Const-Mutate`）。
#:
#: **只列今天真能写出来的**（`typecheck.METHOD_TABLE` 里存在的变异方法）。
#: **P4-0（2026-11-11）**：C++ 名字 `push_back` / `erase` / `clear` 进来了；
#: `push` 仍是弃用别名，所以两者都列。
CONST_MUT_METHODS = frozenset({
    "push", "push_back", "pop",       # vector / array / stack / queue
    "insert", "remove", "erase",      # map / set / unordered_*
    "clear",
    "enqueue", "dequeue",             # stack / queue
})

#: 会改**第一个实参**的内建函数。逐条实测（2026-11-08）：
#:
#: | 内建 | 改法 | 实测 |
#: |---|---|---|
#: | `sort(xs)` | 原地排序 | ✅ `{5,3,9}` → `{3,5,9}` |
#: | `sort_by(xs, cmp)` | 原地排序 | ✅ `{5,3,9}` → `{9,5,3}` |
#: | `reverse(xs)` | 原地反转 | ✅ `{5,3,9}` → `{9,3,5}` |
#: | `fill(xs, v)` | 逐个赋值 | ✅ → `{0,0,0}` |
#: | `dedup(xs)` | 原地紧缩 | ✅ `{5,5,3}` → `{5,3,3}`（尾巴不清理） |
#: | `slice(xs, lo, hi)` | **原地截断** | ✅ `{5,3,9}` → `{3}`（返回新长度） |
#: | `rotate(xs, k)` | 原地左旋 | ✅ `{5,3,9}` → `{3,9,5}` |
#: | `fill_range(xs, lo, hi, v)` | 区间赋值 | ✅ → `{0,0,9}` |
#: | `swap(xs, i, j)` | 原地交换 | ✅ `{5,3,9}` → `{3,5,9}` |
#: | `hmap_put(xs, k, v)` | 写槽位 | ✅ 之后 `hmap_get(xs,1) == 42` |
#: | `vec_push(xs, v)` | 末尾追加 | ✅ 长度 3 → 4 |
#: | `vec_pop(xs)` | 弹出末尾 | ✅ 长度 3 → 2 |
#:
#: **不在表里的**：`map_put` / `set_add` 改的是 stdlib 的 `MapVal`，而语言的
#: `map` / `set` 是解释器自己的 `MapVal`——实测传进去就是
#: `TypeError: map_put 的第一个实参不是 HashMap`，走不到"改数据"那一步。
#: `copy` / `copy_slice` / `is_sorted` / `contains` / `sum_of` 等是只读的。
CONST_MUT_BUILTINS = frozenset({
    "sort", "sort_by", "reverse", "fill", "dedup", "slice", "rotate",
    "fill_range", "swap", "hmap_put", "vec_push", "vec_pop",
})

#: **运行期**才报的诊断码（由 `interp.py` 抛出，check 期拦不住）。
#:
#: 这里登记一份**裸字面量**是有原因的：`tools/check_diag_codes.py` 只认
#: 文件里完整出现的 `"E-…"`，而 `interp.py` 把这些码写在 f-string 里
#: （`f"E-Type-NotContainer：…"`），扫不到。不登记的话，把码补进
#: `文档/语法清单.md` 反而会让"文档有、代码没有"变红——**文档是对的，工具没看见**。
RUNTIME_DIAG_CODES = (
    "E-Type-NotContainer",     # 对非容器值调容器方法（slice(...).length()）
    "E-Type-NotIndexable",     # 对标量做下标 / .at()
)


def _type_head(n) -> str:
    """类型节点的容器头（穿过 `type-ref`）。"""
    while getattr(n, "kind", "") == "type-ref" and getattr(n, "children", None):
        n = n.children[0]
    if getattr(n, "kind", "") == "type-name":
        return canon(getattr(n, "name", "") or "")
    return ""


#: **返回容器内部引用**的方法：`xs.at(0)` / `st.top()` 拿到的是容器里的那个元素，
#: 对它 `.push(…)` 就是改调用方的数据（实测：`const vector<vector<u64>>& xs` 上
#: `xs.at(0).push(9)` 会让调用方的内层从 2 个变 3 个）。`keys()` / `values()` /
#: `copy()` 返回**副本**（实测 `m.keys().push(99)` 不影响 `m.length()`），不能算。
CONST_ALIAS_METHODS = frozenset({"at", "top", "peek", "front"})


def _root_ident(n) -> Optional[str]:
    """表达式的根标识符：`xs` / `xs[0]` / `xs.at(0)` / `xs[0].f` 都归到 `xs`。"""
    cur = n
    guard = 0
    while cur is not None and guard < 64:
        guard += 1
        k = getattr(cur, "kind", "")
        if k == "path":
            return cur.name
        if k in ("index", "field", "deref") and cur.children:
            cur = cur.children[0]
            continue
        # `xs.at(0).push(…)`：`at` 返回的是容器内部元素的引用，继续往下找根。
        if (k == "method-call" and cur.children
                and (cur.name or "").split(".")[-1] in CONST_ALIAS_METHODS):
            cur = cur.children[0]
            continue
        return None
    return None


def _writes_index(n) -> bool:
    """赋值目标里有没有下标写（`xs[0] = …` / `xs[0][1] = …`）。"""
    cur = n
    guard = 0
    while cur is not None and guard < 64:
        guard += 1
        if getattr(cur, "kind", "") == "index":
            return True
        if getattr(cur, "kind", "") in ("field", "deref") and cur.children:
            cur = cur.children[0]
            continue
        return False
    return False


def _writes_field(n) -> bool:
    """赋值目标里有没有**字段写**（`p.x = …` / `p.x.y = …`）。"""
    cur = n
    guard = 0
    while cur is not None and guard < 64:
        guard += 1
        if getattr(cur, "kind", "") == "field":
            return True
        if getattr(cur, "kind", "") in ("index", "deref") and cur.children:
            cur = cur.children[0]
            continue
        return False
    return False


def _check_const_mutation(mod, decl_mod=None) -> List[Diag]:
    """**`const T&` 形参（与 `const` 绑定）不许被改写**（`E-Const-Mutate`）。

    语法清单 §3：`vector<u64>&` 是**参数位置的引用**；加了 `const` 就是
    只读借用。§4 的 `const` 是**常量绑定**（现有 `E-Assign-Immutable` 已经
    按"不可赋值"执行，连 `t.0 = 3` 都拦）。

    以前这两条都只是"标注"：
    `void clobber(const vector<u64>& xs) { sort(xs); }` 的 check 是
    「✓ 没有诊断」，run 把调用方的向量**排了序**——`const` 一点约束力都没有。
    解释器只在 `.push()` 这类**方法**上拦（`E-Borrow-Const`），
    `sort` / 下标写 / `enqueue` 全都绕过去了，而且是 run 期才拦。

    所以这里覆盖四类改写，全部在 check 期报：

    1. 变异方法：`xs.push` / `xs.pop` / `xs.insert` / `xs.remove` /
       `xs.enqueue` / `xs.dequeue`，以及**拿到内部引用**的链式写法
       （`xs.at(0).push(…)` / `st.top().push(…)`；`.at()` / `.top()` /
       `.peek()` / `.front()` 返回的是容器里的那个元素）；
    2. 下标写：`xs[i] = v`（含 `xs[i][j] = v`）；结构体的**字段写**
       `p.x = v`（结构体没有方法，但字段写照样改到调用方）；
    3. 变异内建：`sort(xs)` / `sort_by(xs, f)` / `slice(xs, lo, hi)` 等
       （清单见 `CONST_MUT_BUILTINS`）；
    4. 把 `const` 借用的东西交给**可变引用**形参（`g(xs)`，
       `g(vector<u64>& ys)`）——被调方一改，调用方的数据照样变。

    不报：非 `const` 引用（`vector<u64>&`）的一切改写；只读操作
    （`length` / `at` / `[]` / `is_sorted` / `contains`）。
    """
    if mod is None:
        return []
    fns = [n for n in _all_nodes(mod) if getattr(n, "kind", "") == "fn"]
    # **结构体声明在 `.tu` 里**：`const Point& p; p.x = 1;` 也是改调用方。
    structs = {canon(getattr(n, "name", "") or "")
               for _m in (mod, decl_mod) for n in _all_nodes(_m)
               if getattr(n, "kind", "") == "struct"}

    # 可变引用形参表：`g(vector<u64>& ys)` → {"g": {0}}
    mut_ref: Dict[str, Set[int]] = {}
    for fn in fns:
        prms = _params_of(fn)
        idx: Set[int] = set()
        for i, prm in enumerate(prms.children if prms else []):
            ty = prm.children[0] if prm.children else None
            if getattr(ty, "kind", "") != "type-ref":
                continue
            if isinstance(prm.value, dict) and prm.value.get("const"):
                continue
            idx.add(i)
        if idx and fn.name:
            mut_ref[fn.name] = idx

    # **用户定义的函数遮蔽内建**（2026-11-13，探针库真实误报）：
    # 用户写 `vector<u8> slice(const vector<u8>&, u64, u64)` 时，调用点
    # 被当成**内建的原地 slice**，于是报 `E-Const-Mutate` —— 而它根本没改。
    # 实测 3 个文本/协议库中 2 个踩到（7 条误报）。
    # 与 `requires` / 弃用检查的同一条口径：**同名用户定义优先，内建只在没人定义时才生效**。
    user_fn_names = {fn.name for fn in fns if fn.name}

    diags: List[Diag] = []
    for fn in fns:
        body = next((c for c in fn.children
                     if getattr(c, "kind", "") == "block"), None)
        if body is None:
            continue
        # 名字 → (类型字符串, 声明行, 来源, 类别)
        consts: Dict[str, Tuple[str, int, str, str]] = {}
        prms = _params_of(fn)
        for prm in (prms.children if prms else []):
            if not (isinstance(prm.value, dict) and prm.value.get("const")):
                continue
            ty = prm.children[0] if prm.children else None
            head = _type_head(ty)
            kind = ("容器" if head in CONST_MUTABLE_HEADS
                    else "结构体" if head in structs else "")
            if kind and prm.name:
                consts[prm.name] = (type_str(ty) or head,
                                    prm.line or 0, "const 形参", kind)
        for d in walk(body, "const-decl"):
            ty = d.children[0] if d.children else None
            head = _type_head(ty)
            kind = ("容器" if head in CONST_MUTABLE_HEADS
                    else "结构体" if head in structs else "")
            if kind and d.name:
                consts[d.name] = (type_str(ty) or head,
                                  d.line or 0, "const 绑定", kind)
        # **遮蔽时的保守方向**：同名又出现过可变绑定（`vector<u64> v;`），
        # 就无法确定这一处指的是哪个 `v`——不报（宁可漏报，不要误报）。
        mutable_names = {x.name for x in walk(body, "var-decl") if x.name}
        for nm in list(consts):
            if nm in mutable_names:
                del consts[nm]
        if not consts:
            continue

        def base_ty(nm: str) -> str:
            """`vector<u64>&` → `vector<u64>`（`const` 局部没有 `&`，别切掉 `>`）。"""
            ty = consts[nm][0]
            return ty[:-1] if ty.endswith("&") else ty

        def fail(n, nm: str, what: str, hint: str) -> None:
            ty, ln, how, _kind = consts[nm]
            scope = ("改它会改到调用方的数据。" if how == "const 形参"
                     else "`const` 绑定是常量，不能偷偷改。")
            diags.append(Diag(
                "E-Const-Mutate",
                f"{how} `{nm}`（`{ty}`，第 {ln} 行）被{what}改写："
                f"`const` 是只读的，{scope}{hint}",
                n.line, n.col))

        for n in _all_nodes(body):
            k = getattr(n, "kind", "")
            if k == "method-call":
                nm = _root_ident(n.children[0] if n.children else None)
                meth = (n.name or "").split(".")[-1]
                if nm in consts and meth in CONST_MUT_METHODS:
                    fail(n, nm, f" `.{meth}(…)` ",
                         f"要改就把它声明成可变借用 `{base_ty(nm)}&`"
                         f"（局部绑定则去掉 `const`）。")
            elif k in ("assign", "assign-op"):
                tgt = n.children[0] if n.children else None
                nm = _root_ident(tgt)
                if nm not in consts:
                    continue
                kind = consts[nm][3]
                # `const` 局部绑定已经由 `E-Assign-Immutable` 负责，
                # 再报一条就是同一行两条错误；这里只补**形参**的洞。
                if consts[nm][2] != "const 形参":
                    continue
                if kind == "结构体" and _writes_field(tgt):
                    fail(n, nm, "字段写「.字段 = 」",
                         f"要改就声明成可变借用 `{base_ty(nm)}&`（去掉 `const`）；"
                         f"只想读就保持 `const {base_ty(nm)}&` 形参。")
                elif kind == "容器" and _writes_index(tgt):
                    fail(n, nm, "下标写「[i] = 」",
                         f"要改就声明成可变借用 `{base_ty(nm)}&`（去掉 `const`），"
                         "或把结果放进一个新容器返回。")
            elif k == "call":
                name = callee_name(n) or ""
                args_n = next((c for c in n.children
                               if getattr(c, "kind", "") == "args"), None)
                args = list(args_n.children) if args_n is not None else []
                if (name in CONST_MUT_BUILTINS
                        and name not in user_fn_names   # 用户定义遮蔽内建
                        and args):
                    nm = _root_ident(args[0])
                    if nm in consts:
                        fail(n, nm, f" `{name}(…)` ",
                             "这一族内建是**原地**的（返回长度/旧值），"
                             "不是复制；要保留原值请用 `copy(...)` 先复制。")
                elif name in mut_ref and args:
                    for i in sorted(mut_ref[name]):
                        if i >= len(args):
                            continue
                        nm = _root_ident(args[i])
                        if nm in consts:
                            fail(n, nm,
                                 f" 以可变借用传给 `{name}` 的第 {i + 1} 个形参",
                                 f"`{name}` 的形参是可变借用，会改到它："
                                 f"请把它声明成 `{base_ty(nm)}&`（去掉 `const`），"
                                 f"或把 `{name}` 的形参改成 `const {base_ty(nm)}&`。")
    diags.sort(key=lambda d: (d.line or 0, d.col or 0))
    return diags


def _check_nested_containers(*mods) -> List[Diag]:
    """**定长数组的元素类型不能是容器**（`E-Type-NestedContainer`）。

    `array<array<u64, 2>, 2> g;` 的 check 是「✓ 没有诊断」，run 才炸：

    ```
    TypeError: 'int' object does not support item assignment   // g[0][0] = 7
    TypeError: 'int' object is not subscriptable                // print(g[0][0])
    ```

    **原因在 `interp._default_value_of`**（实测 2026-11-08）：`array<T, N>`
    的默认值是 `FixedList(cap, [zero] * cap)`，而 `zero` 是
    `_convert_to(0, 元素类型)`——元素类型是容器时它退化成**裸整数 0**。
    所以 `array<C, N>`（C 是任何容器）的元素全是 `int`，一旦当容器用就崩。

    **只报这一种**。逐个实测过的"能用"的嵌套形态：

    | 形态 | 结果 |
    |---|---|
    | `vector<vector<u64>>` | ✅ push / `v[0].push(9)` / `v[0].at(0)` 都对 |
    | `map<u64, vector<u64>>` | ✅ `insert` / `get` / `print(m.get(1))` 都对 |
    | `map<u64, array<u64, 2>>` | ✅ |
    | `vector<array<u64, 2>>` | ✅ push 后读对 |
    | `array<vector<u64>, 2>` | ❌ **未赋值的元素**上 `.length()` / `.push()` 崩 |
    | `stack<vector<u64>>` | ✅ |

    `array<vector<u64>, 2>` 也报：它只有"每个元素都先赋过值"才偶然能用，
    而"先读后写"是这种声明最常见的写法（见上表）。出路都一样：**展平**。
    """
    diags: List[Diag] = []
    seen: Dict[tuple, tuple] = {}
    for owner in mods:
        for n in _all_nodes(owner):
            if getattr(n, "kind", "") != "type-name":
                continue
            head = canon(getattr(n, "name", "") or "")
            if head not in ("array", "fixedvec"):
                continue
            args = next((c for c in n.children
                         if getattr(c, "kind", "") == "type-args"), None)
            kids = list(args.children) if args is not None else []
            if not kids:
                continue
            elem = kids[0]
            ehead = _type_head(elem)
            if ehead not in CONST_MUTABLE_HEADS:
                continue
            estring = type_str(elem)
            # 展平示例：`array<array<u64, 2>, 2>` → `array<u64, 2 * 2>` + `g[r * 2 + c]`
            outer_n = _type_capacity_literal(n)
            inner_n = _type_capacity_literal(elem)
            if outer_n and inner_n:
                flat = (f"`array<u64, {outer_n} * {inner_n}> g;`，"
                        f"用 `g[r * {inner_n} + c]`（0 ≤ r < {outer_n}、"
                        f"0 ≤ c < {inner_n}）")
            else:
                flat = "`array<u64, R * C> g;`，用 `g[r * C + c]`"
            msg = (f"`{head}` 的元素类型是容器 `{estring}`：**嵌套容器暂不支持**——"
                   f"内层元素会被默认构造成裸整数 `0`，当容器用就崩"
                   f"（`TypeError: 'int' object ...`）。"
                   f"请展平为一维 + 下标换算：{flat}。"
                   f"`vector<vector<…>>` / `map<K, vector<…>>` 是可用的，"
                   f"只有定长数组套容器不行。")
            # 同一个类型在 `.tu` / `.eln` 各出现一次（`check <x>.eln` 时
            # 两个模块甚至是同一份源码）→ 按「行 + 消息」去重，别报两遍。
            key = (getattr(n, "line", 0) or 0, msg)
            col = getattr(n, "col", 0) or 0
            old = seen.get(key)
            if old is None or col < old[0]:
                seen[key] = (col, getattr(n, "line", 0) or 0, msg)
    for _col, _line, _msg in seen.values():
        diags.append(Diag("E-Type-NestedContainer", _msg, _line, _col))
    diags.sort(key=lambda d: (d.line or 0, d.col or 0))
    return diags


def _check_target_form(tu_mod) -> List[Diag]:
    """**target 里的成本式必须认得出**（`E-Target-BadForm`）。

    以前 `target { time: { max: O(n(xs)) } }` 的 check 说"没有诊断"，
    而 cost 说"spec 没有 time 的 target"——**契约凭空消失了**：
    作者以为写了界，工具以为没写。这比报错危险得多。

    **两层判据**（2026-11-09 补第二层）：
      ① 形状：`_target_cost_shape_ok` 不认识的表达式直接报；
      ② 语义：用 `CostInterp` 真解一遍——形状看着合法、但**绑不上**的
         （裸 `n` 没有可绑的容器/形参、`O(xs.at(0))` 之类）也要报。
         只做 ① 会漏掉"形参名恰好叫 `n`"这一类：它是 `path`，形状永远合法。
    """
    if tu_mod is None:
        return []
    # ② 语义层：`{(line, col, dim, slot): 错误}`——两边看到的是同一份 AST 节点。
    # **键里必须带 dim/slot**：同一行里 `time.max` 与 `allocation.max` 的
    # line/col 是一样的（parser 把位置记在子句开头），只按位置配对会把
    # `time` 的失败算到 `allocation` 头上、同一条诊断报两遍。
    _bad: Dict[tuple, dict] = {}
    try:
        from cost_interp import target_form_errors as _tfe
        for _e in _tfe(tu_mod):
            _bad[(_e.get("line"), _e.get("col"),
                  _e.get("dim"), _e.get("slot"))] = _e
    except Exception:                                  # noqa: BLE001
        _bad = {}
    diags: List[Diag] = []
    for clause in _all_nodes(tu_mod):
        if getattr(clause, "kind", "") != "clause" or clause.name != "target":
            continue
        for dim in clause.children:
            if getattr(dim, "kind", "") != "dim":
                continue
            for slot in dim.children:
                if getattr(slot, "kind", "") != "slot" or not slot.children:
                    continue
                if not _target_cost_shape_ok(slot.children[0]):
                    diags.append(Diag(
                        "E-Target-BadForm",
                        f"target.{dim.name}.{slot.name} 的成本式认不出："
                        f"支持 整数 / O(size) / x.size() / log(x) / k(f) / 四则运算（`+ - * /`）"
                        f" / 尺寸差（`hi - lo`，须能证明 `lo <= hi`）"
                        f" / 幂（`n ** 2` / `n ^ 2`、`n ** 0.5`、`n ** (2/3)`，"
                        f"指数须是**常数**；底数可以是字段链，如 `recv.n_ ^ 8`）"
                        f" / sqrt(x) / root(x, q) / pow(x, p/q)"
                        f"；除法只认 `n / log(n)` / `n / 2`（分母是另一个尺寸量时判不了阶）",
                        slot.line, slot.col))
                    continue
                _hit = _bad.get((slot.line, slot.col, dim.name, slot.name))
                if _hit is not None:
                    diags.append(Diag(
                        "E-Target-BadForm",
                        str(_hit.get("message") or "target 的成本式认不出"),
                        slot.line, slot.col))
    return diags


#: `sizes` 右侧允许的**形状量**写法（规格 §7.2 G1-b 的清单，照抄，不扩）。
SIZES_ALLOWED = ("形参名 / 容器形参的 `.length()` / 整数常量 / "
                 "它们的 `+ * min max`")


def _field_root_name(n: Optional[Node]) -> Optional[str]:
    """`s.v` / `a.b.c` 的最左名字（`s` / `a`）；不是纯字段链 → None。

    只用来判「这个字段链的根是不是形参」——`sizes nv = s.v.size();` 里的
    `s.v` 是**形参 s 的字段**，O(1) 可求；本地的字段链仍然不是形状量。
    """
    while n is not None and getattr(n, "kind", "") == "field":
        kids = getattr(n, "children", None) or []
        n = kids[0] if kids else None
    if n is None or getattr(n, "kind", "") != "path":
        return None
    return getattr(n, "name", "") or None


def _size_expr_ok(n, conts: Set[str], scalars: Set[str],
                  bound: Set[str], consts: Set[str]) -> bool:
    """`sizes` 右侧是不是**形状量**（能在 O(1) 内由形参求出）。

    规格 §7.2：可绑定量 MUST 是形状量——"`length`、`capacity`、形参本身、
    以及它们的常量组合（`min`/`max`/`+`/`*`）"。**只按这份清单收**：
    `-` / `/` / `%` / 比较 / 其它函数调用一律不收（不在清单里，放行就是
    替设计发明语义）。右边引用实现里的局部变量也在这里被挡住——
    `sizes table = table.length()` 里的 `table` 不是形参，会报 `E-Sizes-NotShape`。
    """
    if n is None:
        return False
    k = getattr(n, "kind", "")
    if k == "lit-int":
        return True
    if k == "path":
        return (getattr(n, "name", "") or "") in (conts | scalars | bound | consts)
    if k == "paren":
        return bool(n.children) and _size_expr_ok(
            n.children[0], conts, scalars, bound, consts)
    if k in ("method-call", "call"):
        fn = (getattr(n, "name", "") or "").split(".")[-1]
        if fn in ("length", "size", "capacity", "len"):
            recv = (n.children[0]
                    if getattr(n, "children", None) and n.children else None)
            if recv is None:
                return False
            if getattr(recv, "kind", "") == "path":
                return (recv.name or "") in conts
            if getattr(recv, "kind", "") == "field":
                # **结构体字段的尺寸也是形状量**（2026-11-12）：`s.v.size()`
                # 是 `n(s.v)`，由形参 `s` 在 O(1) 内求出（只多一次字段取址）。
                # 根必须是形参——引用实现里的局部仍然拒收（与上面同一条界线）。
                root = _field_root_name(recv)
                return root is not None and (root in conts or root in scalars)
            return False
        if fn in ("min", "max"):
            args = next((c for c in n.children
                         if getattr(c, "kind", "") == "args"), None)
            argv = list(getattr(args, "children", None) or [])
            return (len(argv) == 2
                    and _size_expr_ok(argv[0], conts, scalars, bound, consts)
                    and _size_expr_ok(argv[1], conts, scalars, bound, consts))
        return False
    if k == "binary" and (getattr(n, "value", "") or "") in ("+", "*") \
            and len(getattr(n, "children", None) or []) >= 2:
        return (_size_expr_ok(n.children[0], conts, scalars, bound, consts)
                and _size_expr_ok(n.children[1], conts, scalars, bound, consts))
    return False


def _check_sizes(tu_mod) -> List[Diag]:
    """`sizes` 子句：**名字 MUST 绑形状量，且 MUST 真被 target 消费**（G1-b）。

    以前 `sizes` 能解析、但 `src/` 里没有任何消费者：写
    `sizes table = n + 1;` 之后，target 里的 `table` 只是一个**自由尺寸量**
    （渲染成 `n(table)`），判定退化成 ⊤——**契约看着更精确，其实一个字没生效**。
    死语法比缺语法更糟：用户会以为它在工作。

    三条界线：

    | 检查 | 码 |
    |---|---|
    | 右侧只能是形状量（不能引用实现里的局部） | `E-Sizes-NotShape` |
    | 名字不许与形参重名（target 里指谁说不清） | `E-Sizes-ParamShadow` |
    | 同一 spec 里不许重复绑定 | `E-Sizes-Dup` |
    | 绑了不用 → 警告（绑了不消费等于没写） | `W-Sizes-Unused` |
    | 用在 `requires`/`guarantees`（**暂未接线**）→ 报错，不许留到运行期才炸 | `E-Sizes-Position` |
    """
    if tu_mod is None:
        return []
    consts = set(_const_int_values(tu_mod))
    diags: List[Diag] = []
    for spec in (getattr(tu_mod, "children", None) or []):
        if getattr(spec, "kind", "") != "spec":
            continue
        body = _spec_body(spec)
        if body is None:
            continue
        clauses = [c for c in (getattr(body, "children", None) or [])
                   if getattr(c, "kind", "") == "clause"]
        size_clauses = [c for c in clauses
                        if (getattr(c, "name", "") or "") == "sizes"]
        if not size_clauses:
            continue
        pnodes = list(getattr(_params_of(spec), "children", None) or [])
        conts = {p.name for p in pnodes
                 if getattr(p, "name", "") and _is_container_param(p)}
        scalars = {p.name for p in pnodes
                   if getattr(p, "name", "") and not _is_container_param(p)}
        bound: Dict[str, Node] = {}
        for cl in size_clauses:
            for b in (getattr(cl, "children", None) or []):
                if getattr(b, "kind", "") != "size-bind":
                    continue
                nm = getattr(b, "name", "") or ""
                expr = b.children[0] if getattr(b, "children", None) else None
                if nm in bound:
                    diags.append(Diag(
                        "E-Sizes-Dup",
                        f"`sizes` 里 `{nm}` 绑了两次（同一 spec 里名字只能绑一次）",
                        b.line, b.col))
                    continue
                if nm in conts or nm in scalars:
                    diags.append(Diag(
                        "E-Sizes-ParamShadow",
                        f"`sizes {nm} = …` 与形参 `{nm}` 重名：target 里的 "
                        f"`{nm}` 到底指绑定量还是形参说不清。请换一个名字。",
                        b.line, b.col))
                if not _size_expr_ok(expr, conts, scalars, set(bound), consts):
                    diags.append(Diag(
                        "E-Sizes-NotShape",
                        f"`sizes {nm} = …` 的右侧不是**形状量**（规格 §7.2）："
                        f"只能写 {SIZES_ALLOWED}；且 MUST 能在 O(1) 内由形参求出，"
                        f"**不能引用实现里的局部变量**。"
                        f"要约束局部容器的尺寸，请把尺寸用形参表达出来。",
                        b.line, b.col))
                bound[nm] = b
        # ---- 用在哪：requires/guarantees 暂未接线；绑了不用 → 警告
        used: Set[str] = set()
        _pos_reported: Set[tuple] = set()
        for cl in clauses:
            cname = getattr(cl, "name", "") or ""
            if cname not in ("requires", "guarantees", "target"):
                continue
            for nd in _all_nodes(cl):
                if getattr(nd, "kind", "") != "path":
                    continue
                nm = getattr(nd, "name", "") or ""
                if nm not in bound:
                    continue
                used.add(nm)
                if cname in ("requires", "guarantees") and (nm, cname) not in _pos_reported:
                    _pos_reported.add((nm, cname))
                    diags.append(Diag(
                        "E-Sizes-Position",
                        f"`sizes` 绑定的 `{nm}` 目前只在 `target` 里生效"
                        f"（`{cname}` 还没接线）。在 `{cname}` 里请直接写形状"
                        f"表达式（如 `xs.length()`）。",
                        cl.line, cl.col))
        for nm, b in bound.items():
            if nm in used:
                continue
            diags.append(Diag(
                "W-Sizes-Unused",
                f"`sizes` 绑定了 `{nm}`，但本 spec 的 requires/guarantees/target "
                f"里没有用到——**绑了不消费等于没写**。",
                b.line, b.col, severity="warning"))
    return diags


# ---------------------------------------------------------------- 弃用拼写（`.tu` 侧，2026-11-13）
#
# **事故形态**：同一条 `.length()`，写进 `.eln` 报 `W-Builtin-Deprecated`
# （`typecheck` 的 method-call 分支），写进 `.tu` 的 `sizes` / `target` 却
# 一声不响。读者会据此以为"`.tu` 里那是正确写法"——而它 2 个月后就要移除。
#
# **为什么 `.tu` 里没人报**：这些子句的表达式各有**自己的消费者**
# （`sizes` / `target` → `cost_interp`，`benchmodel` → `bench`，
# `repeats` / `guarantees` / `requires` → 运行期与 `requires.py`），
# **都不经过 `typecheck`**。所以这里补一个只走 AST 的检查器。
#
# **复用既有机制，不另造一套**（判据与消息都对齐 `typecheck`）：
#   · 容器方法旧名 → `typecheck.DEPRECATED_METHODS`（`length`→`size`、
#     `is_empty`→`empty`、顺序容器上的 `push`→`push_back`）；
#   · 内建旧名 → `stdlib.DEPRECATED_BUILTINS` / `stdlib.replacement_for`。
#   消息的**第一个反引号里必须是旧名**——`diag_json._fix_deprecated` 靠它
#   生成 `fix: replace "length"→"size"`（`--format=json` 的机器修法）。
#
# **只报 warning**：`.length()` 仍能跑，这是既有口径（见
# `typecheck.DEPRECATED_METHODS` 的注释：一次改成 error 会让存量代码同时红）。

#: `.tu` 里**表达式会被解析**的子句名。这份清单就是"覆盖范围"本身：
#: 少一个 ⇒ 那个子句里的旧拼写又回到静默。
_TU_EXPR_CLAUSES = ("sizes", "target", "benchmodel", "repeats",
                    "guarantees", "requires")

#: **所有容器**上都是旧名 的容器方法（`METHOD_TABLE` 里没有任何容器收
#: `length` / `is_empty`，所以判据不需要接收者类型）。
_TU_METHOD_ALWAYS = {"length": "size", "is_empty": "empty"}

#: `push` 只对**顺序容器**是旧名（`stack` / `queue` 的 `push` 本来就是
#: C++ 的名字，报了就是误报——与 `typecheck._PUSH_DEPRECATED_HEADS` 同一条界线）。
_TU_PUSH_HEADS = frozenset({"vector", "array", "fixedvec"})

#: 在 `requires` / `guarantees` 里**是谓词、不是"算长度"的旧内建**。
#: `is_sorted(xs)` 是本语言写排序前提/承诺的**现行写法**（见
#: `requires.py` 的 `_analyze`），虽然 `stdlib` 里它也在弃用表里（它在
#: `.eln` 里是"用循环代替"的糖）。两处语义不同，不能一律按弃用报。
_TU_PREDICATE_OK = frozenset({"is_sorted"})


def _tu_param_type_heads(spec) -> Dict[str, str]:
    """spec 形参名 → 声明类型的头名（`canon` 之后：`vector` / `stack` …）。

    只用来判 `push` 是不是旧名（顺序容器才是）。取不到就**不猜**——
    未知类型不报，宁可漏一条也不误报 `stack.push`。
    """
    out: Dict[str, str] = {}
    _p = _params_of(spec)
    for p in (getattr(_p, "children", None) or []):
        ty = next((c for c in (getattr(p, "children", None) or [])
                   if getattr(c, "kind", "") in ("type-ref", "type-name")), None)
        if ty is not None and getattr(ty, "kind", "") == "type-ref":
            ty = ty.children[0] if ty.children else None
        if ty is None or getattr(ty, "kind", "") != "type-name":
            continue
        nm = getattr(p, "name", "") or ""
        if nm:
            out[nm] = canon(getattr(ty, "name", "") or "")
    return out


def _deprecated_method_msg(name: str, new: str) -> str:
    """与 `typecheck` 的 method-call 分支**逐字一致**（fix 生成器按这个词取旧名）。"""
    return (f"`{name}` 已弃用（v0.73 移除）：改用 `.{new}()`"
            f"（C++ 的名字；旧名仍能跑，但新代码请写新名）")


def _check_tu_deprecated_spelling(tu_mod) -> List[Diag]:
    """`.tu` 的表达式子句里的弃用拼写 → `W-Builtin-Deprecated`（warning）。

    覆盖 `_TU_EXPR_CLAUSES` 里的六种子句；判据：

    | 形态 | 报 | 不报 |
    |---|---|---|
    | 容器方法 `x.length()` / `x.is_empty()` | 一律（所有容器的旧名） | — |
    | 容器方法 `x.push(…)` | 接收者是形参且类型是 `vector`/`array`/`fixedvec` | `stack` / `queue`（C++ 的本名）；类型认不出 |
    | 内建 `sum_of(xs)` / `contains(xs, k)` … | 在 `stdlib` 弃用表里 | 本模块**自己声明了同名 spec**（与 `.eln` 的"用户定义优先"一致）；`requires`/`guarantees` 里的 `is_sorted` |
    """
    if tu_mod is None:
        return []
    specs = [c for c in (getattr(tu_mod, "children", None) or [])
             if getattr(c, "kind", "") == "spec"]
    #: 用户自己声明了同名函数 ⇒ 那个名字**不是**内建（`.eln` 侧同一条规则）。
    declared = {getattr(s, "name", "") or "" for s in specs}
    diags: List[Diag] = []
    for spec in specs:
        heads = _tu_param_type_heads(spec)
        body = _spec_body(spec)
        if body is None:
            continue
        for cl in (getattr(body, "children", None) or []):
            cname = getattr(cl, "name", "") or ""
            if getattr(cl, "kind", "") == "clause":
                if cname not in _TU_EXPR_CLAUSES:
                    continue
            elif getattr(cl, "kind", "") == "benchmodel":
                # **`benchmodel` 不是 `clause` 节点**（它有 `bench-data` /
                # `bench-scale` 子节点），所以不能只走 `_clauses`——
                # 那样 `data { xs = gen(xs.length()) }` 会整块漏掉。
                cname = "benchmodel"
            else:
                continue
            for nd in _all_nodes(cl):
                k = getattr(nd, "kind", "")
                nm = getattr(nd, "name", "") or ""
                if k == "call":
                    if (not nm or nm in declared
                            or not stdlib.is_deprecated(nm)):
                        continue
                    if nm in _TU_PREDICATE_OK and cname in ("requires",
                                                            "guarantees"):
                        continue
                    # 位置取**名字起始列**（`call` 节点自己在 `(` 上，名字在
                    # 它的 `path` 子节点里）——`diag_json` 靠这一列把 fix
                    # 认到 `.tu` 上；落在 `(` 上会让它在两份文件里挑错一份。
                    callee = next((c for c in (getattr(nd, "children", None) or [])
                                   if getattr(c, "kind", "") == "path"), None)
                    diags.append(Diag(
                        "W-Builtin-Deprecated",
                        f"`{nm}` 已弃用（v0.73 移除）：改用 "
                        + stdlib.replacement_for(nm),
                        getattr(nd, "line", 0),
                        getattr(callee or nd, "col", 0),
                        severity="warning"))
                    continue
                if k != "method-call" or not nm:
                    continue
                new = _TU_METHOD_ALWAYS.get(nm)
                if new is None and nm == "push":
                    recv = next((c for c in (getattr(nd, "children", None) or [])
                                 if getattr(c, "kind", "") == "path"), None)
                    if (recv is not None
                            and heads.get(recv.name or "") in _TU_PUSH_HEADS):
                        new = "push_back"
                if new is None:
                    continue
                # `method-call` 的 `col` 在 `.` 上，方法名从下一列开始。
                # **用名字列**：`diag_json._locate` 平手时会倾向 `.eln`，
                # 只有"正好从诊断列开始"才稳定选到 `.tu`（否则同一行的
                # `.eln` 都可能抢走 fix，改错文件比不给 fix 更糟）。
                diags.append(Diag(
                    "W-Builtin-Deprecated",
                    _deprecated_method_msg(nm, new),
                    getattr(nd, "line", 0), getattr(nd, "col", 0) + 1,
                    severity="warning"))
    return diags


def _shape_call_args(n) -> list:
    """调用节点的实参列表（`_target_cost_shape_ok` 用）。"""
    args = next((c for c in n.children if getattr(c, "kind", "") == "args"), None)
    return list(getattr(args, "children", None) or [])


def _target_exponent_shape_ok(n) -> bool:
    """指数位只许**字面量比**：`2`、`3`、`2/3`、`0.5`。

    与 `cost_interp._target_literal_fraction` 同一份口径（两处各写一遍是
    因为 checker 不能反向 import cost_interp 的模块级函数——它会拖进整个
    解释器）。**对不上会的后果**：形状层放行、语义层拒绝 → 报
    `E-Target-BadForm`（这条路本来就是给"认不出"用的，不算静默）。
    """
    if n is None:
        return False
    while getattr(n, "kind", "") == "paren" and n.children:
        n = n.children[0]
    k = getattr(n, "kind", "")
    if k in ("lit-int", "lit-float"):
        return True
    if k == "binary" and (n.value or "") == "/" and len(n.children) >= 2:
        return (_target_exponent_shape_ok(n.children[0])
                and _target_exponent_shape_ok(n.children[1]))
    return False


def _target_field_chain_ok(n) -> bool:
    """`recv.n_` / `a.b.c` 这类**纯字段链**（允许外层括号）。

    只认 `path` 打底、`field` 逐层往上；`f().n_` / `xs.at(0).n_` 这类
    带调用的链**不猜**（语义层同样认不出，两边口径一致）。
    """
    while getattr(n, "kind", "") == "paren" and n.children:
        n = n.children[0]
    k = getattr(n, "kind", "")
    if k == "path":
        return True
    if k == "field":
        kids = list(getattr(n, "children", None) or [])
        return (bool(getattr(n, "name", "") or "")
                and len(kids) >= 1
                and _target_field_chain_ok(kids[0]))
    return False


def _target_cost_shape_ok(n) -> bool:
    """`cost_interp._target_cost` 认得的表达式形状（两边保持同一份判断）。"""
    if n is None:
        return False
    k = getattr(n, "kind", "")
    if k in ("lit-int", "lit-float", "path"):
        return True
    # **字段链就是它的尺寸量**（2026-11-12）：κ 侧 `recv.n_` 渲染成
    # `n(recv.n_)`，target 侧必须能写同一个量（`O(recv.n_)`）。以前这里
    # 只有 `path`，于是 `O(recv.n_)` 被 `check` 报 `E-Target-BadForm`，
    # 而 `cost` 能判——**同一份 .tu 两条命令结论不同**，这本身就是 bug。
    if k == "field":
        return _target_field_chain_ok(n)
    if k == "paren" and n.children:
        return _target_cost_shape_ok(n.children[0])
    if k in ("method-call", "call"):
        fn = (n.name or "").split(".")[-1]
        if fn in ("k", "length", "size", "capacity", "len"):
            return True
        # **`L(g)`**（2026-11-12）：元素长度上界量 `max_i n(g[i])`。
        # `cost_interp._target_cost` 已经认它（`demo/corpus` 的 `O(g.size()*L(g))`
        # 能判达成），形状层必须同步放行——否则 `.tu` 写了 target 却过不了 `check`。
        if fn == "L":
            argv = _shape_call_args(n)
            return len(argv) == 1 and _target_cost_shape_ok(argv[0])
        if fn in ("log", "log2", "O", "o"):
            args = next((c for c in n.children if c.kind == "args"), None)
            return bool(args is not None and args.children
                        and _target_cost_shape_ok(args.children[0]))
        # **分数次幂**（2026-11-09）：`sqrt(x)` / `root(x, q)` / `pow(x, p/q)`。
        # 指数位只收字面量比（见 `_target_exponent_shape_ok`）。
        if fn == "sqrt":
            argv = _shape_call_args(n)
            return (len(argv) == 1 and _target_cost_shape_ok(argv[0]))
        # **`min` / `max`**（G1-b 的形状量组合）：`_target_cost` 已支持，
        # 形状层要同步放行，否则 `sizes m = min(...)` 会被报成认不出。
        if fn in ("min", "max"):
            argv = _shape_call_args(n)
            return (len(argv) == 2 and _target_cost_shape_ok(argv[0])
                    and _target_cost_shape_ok(argv[1]))
        if fn in ("root", "pow"):
            argv = _shape_call_args(n)
            return (len(argv) == 2 and _target_cost_shape_ok(argv[0])
                    and _target_exponent_shape_ok(argv[1]))
        return False
    if k == "binary" and len(n.children) >= 2:
        # **运算符要白名单**（2026-11-09）：形状层以前对任何 `binary` 都放行，
        # 于是 `O(4 % 2)`、`O(n < m)` 这类会"形状通过、语义拒绝"——报出来
        # 的话（E-Target-BadForm 的提示）与 `_target_cost` 的实际能力不符。
        # 现在两处认同一组：`+ - * /`（`/` = 成本式的除法，见 `Cost.div`）。
        #
        # **`**` / `^` 幂**（2026-11-10 / 2026-11-12）：`.eln` 里 `**` 是幂，
        # target 里现在两个都认（`_target_cost` 复用 `Cost.pow`）——契约语言
        # 里 `n ^ 8` 是数学记号，没人会想成按位异或。这里**只检查底数**：
        # 指数由语义层判（`_target_star_star_exponent`），因为"指数必须是常数"
        # 这句话只有语义层说得出来；形状层若先按 `_target_exponent_shape_ok`
        # 拦住，`O(n ** k)` 就只剩通用提示，看不出**为什么**不行。
        # 两者的分工：形状层管"是不是这条路的形状"，语义层管"这个指数能不能用"。
        # **底数可以是字段链**（`recv.n_ ^ 8`）：那是 `n(recv.n_)` 的同一个量。
        if (n.value or "") in ("**", "^"):
            return _target_cost_shape_ok(n.children[0])
        if (n.value or "") not in ("+", "-", "*", "/"):
            return False
        return (_target_cost_shape_ok(n.children[0])
                and _target_cost_shape_ok(n.children[1]))
    return False


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






#: 可声明的效果名。**`panic` 必须在这里**（L-05，2026-11-06）：
#: 它是能让程序中止的效果；不在词表里，写了会被 E-Effect-Unknown 拒，
#: 那就等于"宁可让人不敢声明"，与"诚实优先"相反。
EFFECT_VOCAB = {"io", "fs", "env", "nondet", "alloc", "unsafe", "panic"}


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
                # 名字后面可能跟着 `<…>`（`Vec<u8>`），先取头部再规范化。
                _head = (ty or "").split("<")[0].split("&")[0].strip()
                if canon(_head) in ("vector", "array"):
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
        # **① FFI 必须写在 `unsafe { }` 里**（E-Unsafe-Required）。
        # extern 是 `.eln` 的**模块级节点**：`extern f(...) -> T from "lib" effects { … };`
        _ffi = {c.name for c in (ch_mod.children or [])
                if c.kind == "extern" and getattr(c, "name", None)}
        if _ffi:
            _bad = []
            def _scan(node, in_unsafe):
                if (getattr(node, "kind", "") == "block"
                        and getattr(node, "value", None) == "unsafe"):
                    in_unsafe = True
                if (getattr(node, "kind", "") == "call" and not in_unsafe
                        and (node.name or "").rsplit(".", 1)[-1] in _ffi):
                    _bad.append(node)
                for _c in (getattr(node, "children", None) or []):
                    _scan(_c, in_unsafe)
            _scan(fn, False)
            for _nd in _bad:
                diags.append(Diag("E-Unsafe-Required",
                                  "调用了外部函数 %s，但它不在 `unsafe { }` 块里。" % _nd.name
                                  + "外部调用是语言管不了的边界。改法：把它包进 unsafe 块，"
                                  + "并让 spec 的 effects 声明 unsafe。",
                                  _nd.line, _nd.col))
        # **unsafe 的两条规则**（2026-11-05）：让它从装饰变成约束。
        # 前置已解决：`EFFECT_VOCAB` 里有 unsafe，`effects { unsafe }` 也能解析了。
        _ublk = next((n for n in _all_nodes(fn)
                          if getattr(n, "kind", "") == "block" and getattr(n, "value", None) == "unsafe"), None)
        if _ublk is not None and "unsafe" not in declared:
            diags.append(Diag("E-Unsafe-Undeclared",
                              "这里有 `unsafe { }` 块，但 effects 没声明 `unsafe`。"
                              "改法：在 spec 的 effects 里加上 unsafe，或把不安全操作移出该块。",
                              _ublk.line, _ublk.col))
        if _ublk is None and "unsafe" in declared:
            diags.append(Diag("E-Unsafe-Overdeclared",
                              "effects 声明了 `unsafe`，但函数体内没有 `unsafe { }` 块——虚报。"
                              "改法：去掉 effects 里的 unsafe，或把危险操作放进 unsafe 块。",
                              getattr(fn, "line", 0), getattr(fn, "col", 0)))
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

def check_files(tu_path: Optional[str], ai_path: Optional[str] = None,
                strict: bool = False, overflow: str = "off") -> CheckResult:
    """从文件检查。**`tu_path=None` 合法**：表示只有 `.eln`（`.tu` 可选）。

    以前这里默认 `tu_path` 一定是 `.tu`：`dsh check bare.eln` 会把同一个
    `.eln` 同时当成 `.tu` 解析一遍——于是诊断带 `[.tu]` 前缀**出现两次**，
    而且 `E-Fn-NoSpec` 连 `run` 都挡住（`--strict` 之外不该这样）。
    """
    from parser import read_source_text, SourceEncodingError
    # 没有 `.tu` 时模块名取实现文件名——以前这里 `basename(None)` 会 TypeError。
    module = os.path.splitext(os.path.basename(tu_path or ai_path or "module"))[0]
    has_tu = bool(tu_path)
    try:
        tu_src = read_source_text(tu_path) if has_tu else ""
        ai_src = None
        if ai_path is None and tu_path:
            guess = os.path.splitext(tu_path)[0] + ".eln"
            if os.path.exists(guess):
                ai_path = guess
        if ai_path and os.path.exists(ai_path):
            ai_src = read_source_text(ai_path)
    except SourceEncodingError as e:
        # **诊断而不是 traceback**：源文件只接受 UTF-8（含无 BOM）。
        # 用 check 的输出格式报，读者一眼看到是哪个文件、怎么改。
        return CheckResult(module=module, diagnostics=[
            Diag("E-File-NotUtf8", str(e), 0, 0)])
    return check_module(tu_src, ai_src, module, strict=strict, overflow=overflow,
                        has_tu=has_tu)
