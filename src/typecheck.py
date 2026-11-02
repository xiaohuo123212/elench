#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""typecheck.py —— 名字解析 + 类型检查（M1 的第一项交付）。

**为什么两件事放一个文件**：**名字解析的产物就是类型的载体**——
不知道 `Token` 是类型还是变量，就没法判断 `Token::Plus` 是什么类型。
分两个 pass 只是徒增一次遍历。

## 设计取舍：**未知类型与任何类型兼容**

一个只做"确凿错误"的检查器，比一个"到处误报"的检查器有用得多。

所以本检查器有 `UNKNOWN` 类型，它与任何类型都可兼容。**只有当两边都确定且不兼容时才报错。**

这样：
- **样例全部能过**（泛型、trait、闭包等处一律 UNKNOWN）
- **确凿的错误照样抓住**（返回 `u64` 的函数 `return true;`）

## 已实现的检查

| # | 规则 | 错误码 |
|---|---|---|
| T-1 | 未声明的名字 | `E-Name-Undefined` |
| T-2 | 同一作用域重复声明 | `E-Name-Duplicate` |
| T-3 | **遮蔽外层名字**（用户定：禁止） | `E-Name-Shadowed` |
| T-4 | 返回类型与签名不符 | `E-Type-Return` |
| T-5 | 实参类型与形参不符 | `E-Type-ArgMismatch` |
| T-6 | `?` 用在非 `Result` 上 | `E-Try-NotResult` |
| T-7 | `match` 臂不穷尽且无 `_` | `E-Match-NonExhaustive` |
| T-8 | 枚举变体不存在 | `E-Variant-Unknown` |
| T-9 | 赋值类型不符 | `E-Type-Assign` |
| T-10 | 条件不是 `bool` | `E-Type-Cond` |
"""
import stdlib

from typing import Any, Dict, List, Optional, Set, Tuple

from parser import Node

# ---------------------------------------------------------------- 类型

UNKNOWN = "?"          # 与任何类型兼容
NUMERIC = {"u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64"}
FLOATS = {"f32", "f64"}
INT_WIDTH = {"u8": 8, "u16": 16, "u32": 32, "u64": 64,
             "i8": 8, "i16": 16, "i32": 32, "i64": 64}


class Ty:
    """一个类型。`kind` ∈ name / tuple / ref / fn / unknown。"""

    __slots__ = ("kind", "name", "args", "mut")

    def __init__(self, kind: str, name: str = "", args=(), mut: bool = False):
        self.kind = kind
        self.name = name
        self.args = tuple(args)
        self.mut = mut

    def __repr__(self) -> str:
        return self.render()

    def render(self) -> str:
        if self.kind == "unknown":
            return UNKNOWN
        if self.kind == "tuple":
            return "(" + ", ".join(a.render() for a in self.args) + ")"
        if self.kind == "ref":
            return ("const " if not self.mut else "") + self.args[0].render() + "&"
        if self.kind == "name":
            if self.args:
                return f"{self.name}<" + ", ".join(a.render() for a in self.args) + ">"
            return self.name
        if self.kind == "fn":
            return "fn"
        return "?"


TY_UNKNOWN = Ty("unknown")
TY_BOOL = Ty("name", "bool")
TY_U64 = Ty("name", "u64")
TY_I64 = Ty("name", "i64")
TY_VOID = Ty("name", "void")


def compatible(a: Ty, b: Ty) -> bool:
    """a 能不能赋给 b。UNKNOWN 与任何类型兼容。"""
    if a.kind == "unknown" or b.kind == "unknown":
        return True
    if a.kind == "ref":
        a = a.args[0]
    if b.kind == "ref":
        b = b.args[0]
    if a.kind != b.kind:
        return False
    if a.kind == "fn":
        # **函数类型**：slice 1 只比对**参数个数**。
        # 深度比对签名要处理"调用方把 `fn` 当值传"的递归，先记成待办；
        # 这里放行不会制造假上界（κ 由 `k(f)` 表达，签名错是另一个问题）。
        return len(a.args) == len(b.args)
    if a.kind == "tuple":
        return (len(a.args) == len(b.args)
                and all(compatible(x, y) for x, y in zip(a.args, b.args)))
    if a.kind == "name":
        if a.name == b.name:
            return (len(a.args) == len(b.args)
                    and all(compatible(x, y) for x, y in zip(a.args, b.args)))
        # 数值类型之间一律按 UNKNOWN 处理（N2 禁止隐式转换，这里不代替 N2，
        # 只是不制造误报；真正的 N2 检查由 checker 的字面量规则负责）
        if a.name in NUMERIC and b.name in NUMERIC:
            return True
        return False
    return False


def ty_of_node(n: Optional[Node]) -> Ty:
    """把 AST 的类型节点变成 Ty。"""
    if n is None:
        return TY_UNKNOWN
    k = n.kind
    if k == "type-name":
        args = []
        for c in n.children:
            if c.kind == "type-args":
                args = [ty_of_node(x) for x in c.children]
        return Ty("name", n.name or "", args)
    if k == "type-fn":
        # **函数类型**：`func(u64, Vec<u8>) -> u64` → Ty("fn", "", [参数..., 返回])
        ps = next((c for c in n.children if c.kind == "type-fn-params"), None)
        ret = n.children[-1] if n.children else None
        args = [ty_of_node(c) for c in (ps.children if ps else [])]
        if ret is not None and ret.kind != "type-fn-params":
            args.append(ty_of_node(ret))
        return Ty("fn", "", args)
    if k == "type-ref":
        return Ty("ref", args=[ty_of_node(n.children[0])] if n.children else [TY_UNKNOWN],
                  mut=(n.value == "mut"))
    if k == "type-tuple":
        return Ty("tuple", args=[ty_of_node(c) for c in n.children])
    if k == "type-slice":
        return Ty("name", "Vec", [ty_of_node(n.children[0])] if n.children else [TY_UNKNOWN])
    return TY_UNKNOWN


# ---------------------------------------------------------------- 符号表

class Sym:
    __slots__ = ("name", "kind", "ty", "node")

    def __init__(self, name: str, kind: str, ty: Ty, node: Optional[Node] = None):
        self.name = name
        self.kind = kind          # type / func / var / const / variant / error
        self.ty = ty
        self.node = node


class Diag:
    __slots__ = ("code", "message", "line", "col", "severity")

    def __init__(self, code: str, message: str, line: int = 0, col: int = 0,
                 severity: str = "error"):
        self.code = code
        self.message = message
        self.line = line
        self.col = col
        self.severity = severity

    def __repr__(self) -> str:
        return f"{self.code}@{self.line}:{self.col} {self.message}"


class Scope:
    def __init__(self, parent: Optional["Scope"] = None):
        self.parent = parent
        self.names: Dict[str, Sym] = {}

    def declare(self, s: Sym) -> Optional[Sym]:
        """声明；返回被遮蔽的外层同名符号（有的话）。"""
        if s.name in self.names:
            return self.names[s.name]       # 同作用域重复
        self.names[s.name] = s
        outer = self.parent
        while outer is not None:
            if s.name in outer.names:
                return outer.names[s.name]
            outer = outer.parent
        return None

    def lookup(self, name: str) -> Optional[Sym]:
        sc: Optional[Scope] = self
        while sc is not None:
            if name in sc.names:
                return sc.names[name]
            sc = sc.parent
        return None


# ---------------------------------------------------------------- 检查器

class TypeChecker:
    def __init__(self, tu_mod: Optional[Node], ch_mod: Optional[Node]):
        self.tu = tu_mod
        self.eln = ch_mod
        self.diags: List[Diag] = []
        self.globals = Scope()
        self.types: Dict[str, Node] = {}        # 类型名 → 声明节点
        # 枚举名 → 变体名集合。**内置 `Io`** 是语言提供的错误枚举，
        # 用户不必在每个 .tu 里重写（I/O 失败需要它）。
        self.enums: Dict[str, Set[str]] = {
            # **内置错误枚举**：每个程序都会遇到，让每个 .tu 重写一遍不合理。
            # 变体由编译器给，**不增加可撒谎位置**。
            "Io": {"NotFound", "Denied", "Failed"},
            "Arithmetic": {"DivisionByZero", "Overflow"},
            "Parse": {"BadNumber", "Overflow"},
            "Range": {"OutOfBounds"},
            "Option": {"Some", "None"},
        }
        self.enum_payload: Dict[Tuple[str, str], Ty] = {}
        self.open_errors: Set[str] = set()
        self.funcs: Dict[str, Node] = {}
        self.specs: Dict[str, Node] = {}
        self._collect()

    # ---------- 收集
    def _collect(self) -> None:
        for mod in (self.tu, self.eln):
            if mod is None:
                continue
            for it in mod.children:
                if it.kind in ("struct", "enum", "trait"):
                    self.types[it.name] = it
                    self.globals.declare(Sym(it.name, "type", Ty("name", it.name), it))
                    if it.kind == "enum":
                        vs = set()
                        for v in it.children:
                            if v.kind != "variant":
                                continue
                            vs.add(v.name)
                            payload = (ty_of_node(v.children[0])
                                       if v.children else TY_UNKNOWN)
                            self.enum_payload[(it.name, v.name)] = payload
                        self.enums[it.name] = vs
                elif it.kind == "error-decl":
                    self.globals.declare(Sym(it.name, "error", TY_UNKNOWN, it))
                    # ⚠ 已知缺口（记为 E3）：`error X;` 只声明类型名，
                    # **变体从未被声明**，却在使用处出现。所以这里把 error 类型
                    # 记为"开放"：任何变体都接受，不做存在性校验。
                    self.open_errors.add(it.name)
                elif it.kind == "const":
                    ty = ty_of_node(it.children[0]) if it.children else TY_UNKNOWN
                    self.globals.declare(Sym(it.name, "const", ty, it))
                elif it.kind == "fn":
                    self.funcs[it.name] = it
                    self.globals.declare(Sym(it.name, "func", self._fn_type(it), it))
                elif it.kind == "spec":
                    self.specs[it.name] = it
                    self.globals.declare(Sym(it.name, "func", self._spec_type(it), it))

    def _params_of(self, n: Node) -> List[Tuple[str, Ty]]:
        p = next((c for c in n.children if c.kind == "params"), None)
        out = []
        for c in (p.children if p else []):
            if c.kind == "param":
                out.append((c.name or "", ty_of_node(c.children[0]) if c.children else TY_UNKNOWN))
            elif c.children:
                out.append(("", ty_of_node(c.children[0])))
        return out

    def _ret_of(self, n: Node) -> Ty:
        base = next((ty_of_node(c) for c in n.children if c.kind.startswith("type")),
                    TY_VOID)
        if (n.value or {}).get("fallible"):
            return Ty("name", "Result", [base, Ty("name", "CalcError")])
        return base

    def _fn_type(self, n: Node) -> Ty:
        # **带返回类型**：与 `func(...)->R` 的 Ty 形状一致（末尾是返回类型）
        ret = ty_of_node(next((c for c in n.children if c.kind in ("type-name", "type-ref", "type-fn")), None))
        return Ty("fn", name=n.name or "",
                  args=[t for _, t in self._params_of(n)] + [ret])

    def _spec_type(self, n: Node) -> Ty:
        ret = ty_of_node(next((c for c in n.children if c.kind in ("type-name", "type-ref", "type-fn")), None))
        return Ty("fn", name=n.name or "",
                  args=[t for _, t in self._params_of(n)] + [ret])

    # ---------- 入口
    def check(self) -> List[Diag]:
        for mod in (self.tu, self.eln):
            if mod is None:
                continue
            for it in mod.children:
                if it.kind == "fn":
                    self._check_fn(it)
        return self.diags

    def _err(self, code: str, msg: str, n: Optional[Node]) -> None:
        self.diags.append(Diag(code, msg, getattr(n, "line", 0) if n else 0,
                               getattr(n, "col", 0) if n else 0))

    def _warn(self, code: str, msg: str, n: Optional[Node]) -> None:
        self.diags.append(Diag(code, msg, getattr(n, "line", 0) if n else 0,
                               getattr(n, "col", 0) if n else 0,
                               severity="warning"))

    def _check_fn(self, fn: Node) -> None:
        sc = Scope(self.globals)
        for name, ty in self._params_of(fn):
            if name:
                self._decl(sc, Sym(name, "var", ty, fn))
        block = next((c for c in fn.children if c.kind == "block"), None)
        body_scope = sc
        if block is not None:
            ret = self._ret_of(fn)
            fallible = bool((fn.value or {}).get("fallible"))
            body_scope = self._block(block, sc, ret, fallible)
        # 内核候选体也要查（它们共享宿主的作用域）
        for kern in fn.children:
            if kern.kind != "kernels":
                continue
            for cand in kern.children:
                if cand.kind != "candidate":
                    continue
                b = next((c for c in cand.children if c.kind == "block"), None)
                if b is not None:
                    # 候选默认 inline：能读宿主局部变量（`n`），所以用宿主的整块作用域
                    self._block(b, body_scope, self._ret_of(kern), True)

    def _decl(self, sc: Scope, s: Sym) -> None:
        # 同一作用域重复
        if s.name in sc.names:
            self._err("E-Name-Duplicate",
                      f"`{s.name}` 在同一作用域内重复声明", s.node)
            return
        # 遮蔽外层（用户定：禁止）
        outer = sc.parent
        while outer is not None:
            if s.name in outer.names:
                self._err("E-Name-Shadowed",
                          f"`{s.name}` 遮蔽了外层同名符号；本语言禁止遮蔽",
                          s.node)
                return
            outer = outer.parent
        sc.names[s.name] = s

    # ---------- 语句
    def _block(self, blk: Node, outer: Scope, ret: Ty, fallible: bool) -> Scope:
        sc = Scope(outer)
        for st in blk.children:
            self._stmt(st, sc, ret, fallible)
        return sc

    def _stmt(self, st: Node, sc: Scope, ret: Ty, fallible: bool) -> None:
        k = st.kind

        if k in ("const-decl", "var-decl"):
            ty = ty_of_node(st.children[0]) if st.children else TY_UNKNOWN
            self._decl(sc, Sym(st.name, "var", ty, st))
            if len(st.children) >= 2:
                vt = self._expr(st.children[-1], sc)
                if not compatible(vt, ty):
                    self._err("E-Type-Assign",
                              f"`{st.name}` 声明为 {ty.render()}，初值是 {vt.render()}",
                              st)
            return

        if k == "assign":
            vt = self._expr(st.children[1], sc)
            tt = self._place(st.children[0], sc)
            if not compatible(vt, tt):
                self._err("E-Type-Assign",
                          f"赋值类型不符：左边 {tt.render()}，右边 {vt.render()}", st)
            return

        if k == "return":
            if st.children:
                vt = self._expr(st.children[0], sc)
                want = ret
                if fallible:
                    # `T!` 里 `return x` 自动包 Ok；也允许直接返回 Result
                    if vt.kind == "name" and vt.name == "Result":
                        return
                    want = ret.args[0] if (ret.kind == "name" and ret.args) else TY_UNKNOWN
                if not compatible(vt, want):
                    self._err("E-Type-Return",
                              f"返回类型不符：签名 {want.render()}，实际 {vt.render()}", st)
            return

        if k == "if":
            ct = self._expr(st.children[0], sc) if st.children else TY_BOOL
            if not compatible(ct, TY_BOOL):
                self._err("E-Type-Cond",
                          f"if 的条件应是 bool，实际 {ct.render()}", st)
            for c in st.children[1:]:
                if c.kind == "block":
                    self._block(c, sc, ret, fallible)
                elif c.kind == "if":
                    self._stmt(c, sc, ret, fallible)
            return

        if k == "while":
            ct = self._expr(st.children[0], sc) if st.children else TY_BOOL
            if not compatible(ct, TY_BOOL):
                self._err("E-Type-Cond",
                          f"while 的条件应是 bool，实际 {ct.render()}", st)
            for c in st.children[1:]:
                if c.kind == "block":
                    self._block(c, sc, ret, fallible)
            return

        if k == "for":
            for c in st.children:
                if c.kind == "block":
                    inner = Scope(sc)
                    if st.name:
                        self._decl(inner, Sym(st.name, "var", TY_UNKNOWN, st))
                    self._block(c, inner, ret, fallible)
            return

        if k == "match":
            self._match(st, sc, ret, fallible)
            return

        if k == "block":
            self._block(st, sc, ret, fallible)
            return

        if k == "expr-stmt" and st.children:
            self._expr(st.children[0], sc)
            return

        if k == "dispatch":
            self._expr(st, sc)
            return

    def _place(self, n: Node, sc: Scope) -> Ty:
        if n.kind == "path":
            s = sc.lookup(n.name or "")
            if s is None:
                self._err("E-Name-Undefined", f"未声明的名字 `{n.name}`", n)
                return TY_UNKNOWN
            return s.ty
        return self._expr(n, sc)

    def _match(self, st: Node, sc: Scope, ret: Ty, fallible: bool) -> None:
        scrut = self._expr(st.children[0], sc) if st.children else TY_UNKNOWN
        arms = [c for c in st.children[1:] if c.kind == "arm"]
        covered: Set[str] = set()
        has_wild = False
        for arm in arms:
            pat = next((c for c in arm.children
                        if c.kind in ("pattern", "pattern-wildcard")), None)
            if pat is not None and pat.kind == "pattern-wildcard":
                has_wild = True
            elif pat is not None and pat.name:
                parts = pat.name.split(".")
                if len(parts) == 2:
                    ty_name, variant = parts
                    covered.add(variant)
                    if (ty_name in self.enums and ty_name not in self.open_errors
                            and variant not in self.enums[ty_name]):
                        self._err("E-Variant-Unknown",
                                  f"枚举 {ty_name} 没有变体 {variant}", pat)
            inner = Scope(sc)
            # `Token::Number(n)` —— 绑定名是 **pattern 的子节点**，不是 arm 的直接子节点。
            # 不在这里声明，`return n` 就会报 `E-Name-Undefined`（速查里最常用的
            # enum + match 写法都过不了）。
            if pat is not None:
                for b in pat.children:
                    if b.kind == "binding":
                        # 载荷类型未跟踪（E12 的一部分），先给 UNKNOWN——它对任何类型都兼容
                        self._decl(inner, Sym(b.name or "", "var", TY_UNKNOWN, b))
            if arm is not None:
                for b in arm.children:
                    if b.kind == "binding":
                        self._decl(inner, Sym(b.name or "", "var", TY_UNKNOWN, b))
                    elif b.kind == "block":
                        self._block(b, inner, ret, fallible)
                    elif b.kind not in ("pattern", "pattern-wildcard"):
                        self._stmt(b, inner, ret, fallible)

        # 穷尽性：scrutinee 是已知枚举，且没有 `_`
        if (not has_wild and scrut.kind == "name"
                and scrut.name in self.enums):
            missing = self.enums[scrut.name] - covered
            if missing:
                self._err("E-Match-NonExhaustive",
                          f"match 未穷尽 {scrut.name}：缺 {sorted(missing)}，也没有 `_` 臂",
                          st)

    # ---------- 表达式
    def _expr(self, n: Optional[Node], sc: Scope) -> Ty:
        if n is None:
            return TY_UNKNOWN
        k = n.kind

        if k == "lit-int":
            return TY_I64
        if k == "lit-string":
            return Ty("name", "Vec", [Ty("name", "u8")])

        if k == "lit-char":
            return TY_U64
        if k == "lit-bool":
            return TY_BOOL
        if k == "paren":
            return self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
        if k == "tuple":
            return Ty("tuple", args=[self._expr(c, sc) for c in n.children])
        if k == "struct-literal":
            ty_name = n.name or ""
            decl = self.types.get(ty_name)
            want: Dict[str, Ty] = {}
            if decl is not None and decl.kind == "struct":
                for f in decl.children:
                    if f.kind == "field" and f.children:
                        want[f.name] = ty_of_node(f.children[0])
            got = set()
            for fi in n.children:
                if fi.kind != "field-init" or not fi.children:
                    continue
                got.add(fi.name)
                vt = self._expr(fi.children[0], sc)
                if fi.name in want and not compatible(vt, want[fi.name]):
                    self._err("E-Type-FieldMismatch",
                              f"{ty_name}.{fi.name} 是 {want[fi.name].render()}，"
                              f"给的是 {vt.render()}", fi)
                elif want and fi.name not in want:
                    self._err("E-Type-NoField",
                              f"{ty_name} 没有字段 {fi.name}"
                              f"（有：{', '.join(sorted(want))}）", fi)
            missing = set(want) - got
            if missing:
                self._err("E-Type-MissingField",
                          f"{ty_name} 缺字段 {sorted(missing)}", n)
            return Ty("name", ty_name)

        if k == "path":
            name = n.name or ""
            if "." in name:
                ty_name, variant = name.rsplit(".", 1)
                if (ty_name in self.enums and ty_name not in self.open_errors
                        and variant not in self.enums[ty_name]):
                    self._err("E-Variant-Unknown",
                              f"枚举 {ty_name} 没有变体 {variant}", n)
                return self.enum_payload.get((ty_name, variant),
                                             Ty("name", ty_name))
            s = sc.lookup(name)
            if s is None:
                # **弃用内建**：名字还认（老代码能跑），但给出改写指引
                try:
                    import stdlib as _std
                    if _std.is_deprecated(name):
                        self._warn(
                            "W-Builtin-Deprecated",
                            f"`{name}` 已弃用（v0.73 移除）：改用 "
                            + _std.replacement_for(name), n)
                        return TY_UNKNOWN
                except Exception:                       # noqa: BLE001
                    pass
                self._err("E-Name-Undefined", f"未声明的名字 `{name}`", n)
                return TY_UNKNOWN
            return s.ty

        if k == "unary":
            t = self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
            return TY_BOOL if (n.value or "") == "not" else t

        if k == "binary":
            op = n.value or ""
            a = self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
            b = self._expr(n.children[1], sc) if len(n.children) > 1 else TY_UNKNOWN
            if op in ("and", "or"):
                return TY_BOOL
            if op in ("<", "<=", ">", ">=", "==", "!="):
                return TY_BOOL
            if op in ("/", "%"):
                return Ty("name", "Result", [a if a.kind != "unknown" else TY_U64,
                                             Ty("name", "Arithmetic")])
            return a if a.kind != "unknown" else b

        if k == "call":
            name = n.name or ""
            args = []
            for c in n.children:
                if c.kind == "args":
                    args = [self._expr(a, sc) for a in c.children
                            if a.kind != "named-arg"] + \
                           [self._expr(a.children[0], sc) for a in c.children
                            if a.kind == "named-arg" and a.children]
            if "." in name:
                ty_name, variant = name.rsplit(".", 1)
                if (ty_name in self.enums and ty_name not in self.open_errors
                        and variant not in self.enums[ty_name]):
                    self._err("E-Variant-Unknown",
                              f"枚举 {ty_name} 没有变体 {variant}", n)
                return Ty("name", ty_name)
            if name in ("Ok", "Err"):
                return Ty("name", "Result", args[:1] or [TY_UNKNOWN])
            if name in ("sort", "sort_by"):
                # 原地排序，返回元素个数（u64）。比较器是**名字**，不是值。
                return TY_U64
            if name == "for_each":
                # **高阶内建**：`for_each(容器, 具名函数)`（v0.73 起支持 Vec）。
                # 以前只在解释器里有实现、名字表里没有 → 合法程序被 check 拒。
                if len(args) != 2:
                    self._err("E-Type-ArgCount",
                              f"for_each 要 2 个实参（容器 + 具名函数），给了 {len(args)}", n)
                return TY_U64
            if stdlib.is_stdlib(name):
                # **弃用内建**：还能用，但要告诉作者该改写成什么（v0.73 移除）
                if stdlib.is_deprecated(name):
                    self._warn(
                        "W-Builtin-Deprecated",
                        f"`{name}` 已弃用（v0.73 移除）：改用 "
                        + stdlib.replacement_for(name), n)
                # 最小基本库：本批全部是 `(容器, …) -> u64`，只校验实参个数。
                if len(args) != stdlib.arity(name):
                    self._err("E-Type-ArgCount",
                              f"{name} 要 {stdlib.arity(name)} 个实参，给了 {len(args)}", n)
                    return TY_UNKNOWN
                # **高阶内建**（v0.71）：参数是具名函数，返回容器或 u64
                if name in ("map", "filter"):
                    return Ty("name", "Vec", [TY_U64])
                if name in ("fold", "count_if"):
                    return TY_U64
                _rk = stdlib.ret_kind(name)
                if _rk == "i64":
                    return TY_I64
                if _rk == "vec-vec-u8":
                    return Ty("name", "Vec", [Ty("name", "Vec", [Ty("name", "u8")])])
                if _rk == "vec-u8":
                    return Ty("name", "Vec", [Ty("name", "u8")])
                if _rk == "any":
                    return TY_UNKNOWN
                if _rk == "result-vec-u64":
                    return Ty("name", "Result", [Ty("name", "Vec", [TY_U64])])
                if _rk == "result-u64":
                    return Ty("name", "Result", [TY_U64])
                if _rk == "option-vec-u8":
                    return Ty("name", "Option", [Ty("name", "Vec", [Ty("name", "u8")])])
                if _rk == "result-vec-u8":
                    return Ty("name", "Result", [Ty("name", "Vec", [Ty("name", "u8")])])
                return TY_U64
            if name in ("map", "filter", "fold", "count_if"):
                # 这些是**解释器直接实现**的（不在 stdlib 注册表里），单独处理
                if name in ("map", "filter"):
                    return Ty("name", "Vec", [TY_U64])
                return TY_U64

            if name == "print":
                if not args:
                    self._err("E-Type-ArgCount", "print 至少要 1 个实参", n)
                    return TY_UNKNOWN
                return TY_U64
            if name in ("min", "max"):
                # 内建二参整数函数：`(T, T) -> T`。成本常数、不改变阶，
                # 所以它能替掉 `if` 串两支而不动成本契约。
                if len(args) != 2:
                    self._err("E-Type-ArgCount",
                              f"{name} 要 2 个实参，给了 {len(args)}", n)
                    return TY_UNKNOWN
                if not (compatible(args[0], args[1])):
                    self._err("E-Type-ArgMismatch",
                              f"{name} 的两个实参类型不同："
                              f"{args[0].render()} vs {args[1].render()}", n)
                return args[0] if compatible(args[0], args[1]) else TY_UNKNOWN
            if name == "":
                # `FixedVec<T,N>::make()` 这类静态构造：callee 是 field，不是名字
                return TY_UNKNOWN
            s = sc.lookup(name)
            if s is None:
                self._err("E-Name-Undefined",
                          f"调用了未声明的函数 `{name}`"
                          f"（若它是外部符号，请在 `.tu` 的 `extern` 块登记，"
                          f"否则无法只数次数）", n)
                return TY_UNKNOWN
            callee = self.funcs.get(name) or self._find_decl(name)
            if callee is not None:
                want = self._params_of(callee)
                if len(want) == len(args):
                    for (pn, pt), at in zip(want, args):
                        if not compatible(at, pt):
                            self._err("E-Type-ArgMismatch",
                                      f"调用 {name}：形参 `{pn}` 是 {pt.render()}，"
                                      f"实参是 {at.render()}", n)
            return self._ret_decl(name)

        if k == "method-call":
            recv = self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
            # `const Vec<u8>& text` 的类型是 ref；取元素类型要先拆引用
            while recv.kind == "ref" and recv.args:
                recv = recv.args[0]
            name = n.name or ""
            if name == "length":
                return TY_U64
            if name == "at":
                return recv.args[0] if recv.args else TY_UNKNOWN
            if name == "push":
                return TY_VOID
            if name == "pop":
                return recv.args[0] if recv.args else TY_UNKNOWN
            return TY_UNKNOWN

        if k == "field":
            base = self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
            nm = n.name if n.name is not None else str(n.value or "")
            if nm.isdigit():
                idx = int(nm)
                if base.kind == "tuple" and idx < len(base.args):
                    return base.args[idx]
                return TY_UNKNOWN
            return TY_UNKNOWN

        if k == "try":
            inner = self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
            if inner.kind != "unknown" and not (inner.kind == "name"
                                                and inner.name == "Result"):
                self._err("E-Try-NotResult",
                          f"`?` 只能用于 Result，实际是 {inner.render()}", n)
                return TY_UNKNOWN
            return inner.args[0] if inner.args else TY_UNKNOWN

        if k == "dispatch":
            label = n.name or ""
            for fn in self.funcs.values():
                for kern in fn.children:
                    if kern.kind == "kernels" and kern.name == label:
                        return self._ret_of(kern)
            return TY_UNKNOWN

        if k == "cast":
            return ty_of_node(n.children[1]) if len(n.children) > 1 else TY_UNKNOWN

        if k == "index":
            base = self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
            # `const Vec<u64>& xs` 的类型是 ref；取元素类型要先拆引用，
            # 否则 `xs[0] = 5` 会被当成「给 Vec<u64> 赋 i64」误报 E-Type-Assign。
            while base.kind == "ref" and base.args:
                base = base.args[0]
            return base.args[0] if base.args else TY_UNKNOWN

        if k == "ref":
            return self._expr(n.children[0], sc) if n.children else TY_UNKNOWN

        return TY_UNKNOWN

    def _find_decl(self, name: str) -> Optional[Node]:
        return self.specs.get(name)

    def _ret_decl(self, name: str) -> Ty:
        fn = self.funcs.get(name)
        if fn is not None:
            t = self._ret_of(fn)
            return t
        sp = self._find_decl(name)
        if sp is not None:
            return self._ret_of(sp)
        return TY_UNKNOWN


# ---------------------------------------------------------------- 便利入口

def typecheck(tu_mod: Optional[Node], ch_mod: Optional[Node]) -> List[Diag]:
    return TypeChecker(tu_mod, ch_mod).check()


if __name__ == "__main__":
    import stdlib
    import sys
    from parser import parse_source

    sys.stdout.reconfigure(encoding="utf-8")
    srcs = [open(p, encoding="utf-8").read() for p in sys.argv[1:]]
    tu = ch = None
    for p, s in zip(sys.argv[1:], srcs):
        m = parse_source(s, p).module
        if p.endswith(".tu"):
            tu = m
        else:
            ch = m
    diags = typecheck(tu, ch)
    for d in diags:
        print(f"  {d.code} @{d.line}:{d.col}  {d.message}")
    print(f"\n{len(diags)} 条诊断")
