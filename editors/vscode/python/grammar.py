#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""grammar.py —— 从 `.tu` 导出**受限语法**，供模型生成 `.eln` 时做受限解码。

## 为什么这件事值得做

`.tu` 已经把"这个模块允许出现什么"写完了：函数名、参数类型、返回类型、effects。
**它就是一份语法约束**——只是以前只能给人和检查器看，不能给解码器看。

把 `.tu` 翻成"语法 + 词汇表"之后，模型生成 `.eln` 时可以被**约束在合法空间里**：
写错签名、调用不存在的函数、返回错类型，在**生成那一刻就发生不了**，
而不是等 `check` 事后抓。

## 三件产物

| 产物 | 内容 | 给谁用 |
|---|---|---|
| **语言 EBNF** | `.eln` 的语句/表达式产生式（语言级，所有模块相同） | 受限解码的语法骨架 |
| **模块词汇表** | 本模块的函数/外部函数/类型/常量（含签名与 effects） | 解码时限制标识符与实参类型 |
| **打洞骨架** | 每个函数一个"签名齐全、body 是洞"的骨架 | 让模型只填 body，不重写签名 |

## 不做什么

**不生成完整 AST 的 JSON Schema。** 那等于把 `parser.py` 重写一遍，
两份语法必然漂移；而漂移的语法比没有语法更危险（解码器放行的，检查器会拒）。
所以这里只给"语言骨架 + 词汇表 + 洞"，洞里的具体形状交给 EBNF。

## 漂移怎么防

`tests/cases/test_grammar.py` 会拿 `lexer.KEYWORDS` 逐个比对：
**词法里有的关键字，EBNF 里必须出现**（除了纯 token 类：`ident`/`eof`/`int`/`string`）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: 纯 token 类关键字：它们不是"结构关键字"，EBNF 里以 `<ident>`/`<int>` 等出现。
TOKEN_KEYWORDS = {"ident", "eof", "int", "float", "string", "char"}


def _clause(spec, name: str):
    body = next((c for c in spec.children if c.kind == "spec-body"), None)
    return next((c for c in (body.children if body else []) if c.kind == "clause"
                 and (c.name or "") == name), None)


def _type_of(node) -> str:
    from checker import type_str
    return type_str(node)


def _params(spec) -> List[Dict[str, str]]:
    from checker import param_list
    return [{"name": n or "_", "type": t} for n, t in param_list(spec)]


def _returns(spec) -> str:
    for c in spec.children:
        if c.kind in ("type-name", "type-ref"):
            return _type_of(c)
    return ""


def _effects(spec) -> List[str]:
    cl = _clause(spec, "effects")
    if cl is None:
        return []
    v = cl.value
    if v == "pure" or v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [str(v)]


def _has(spec, name: str) -> bool:
    return _clause(spec, name) is not None


# ---------------------------------------------------------------- 语言 EBNF

def language_ebnf() -> str:
    """`.eln` 的语言级 EBNF（所有模块共用）。"""
    return r"""(* Elench .eln —— 语言级 EBNF（受限解码骨架）
   只写"结构"，不写语义：类型合法性由 .tu 词汇表约束，成本由 κ 判定。 *)

module        = { item } ;
item          = use-decl | func-decl | struct-decl | enum-decl | trait-decl
              | impl-decl ;
impl-decl     = "impl" path [ "for" path ] "{" { func-decl } "}" ;

use-decl      = "use" path [ "as" IDENT ] ";" ;
func-decl     = { "pub" } "func" IDENT "(" [ params ] ")" [ "->" type ] block
                [ kernels-block ] ;
kernels-block = "kernels" IDENT "(" [ params ] ")" [ "->" type ]
                "{" { candidate } "}" ;
candidate     = IDENT block ;
params        = param { "," param } ;
param         = [ "const" ] type [ IDENT ] ;
struct-decl   = "struct" IDENT "{" { field } "}" ;
field         = IDENT ":" type ";" ;
enum-decl     = "enum" IDENT "{" variant { "," variant } "}" ;
variant       = IDENT [ "(" type { "," type } ")" ] ;
trait-decl    = "trait" IDENT "{" { func-sig } "}" ;
func-sig      = "func" IDENT "(" [ params ] ")" [ "->" type ] ";" ;

(* ---------------- 类型 ---------------- *)
type          = prim-type | path [ "<" type { "," type } ">" ] [ "&" ]
              | "[" type "]" | "(" type { "," type } ")" | func-type | "dyn" IDENT ;
prim-type     = "u64" | "i64" | "bool" | "char" | "float" ;
func-type     = "func" "(" [ type { "," type } ] ")" [ "->" type ] ;

(* ---------------- 语句 ---------------- *)
block         = "{" { stmt } "}" ;
stmt          = const-stmt | var-stmt | assign-stmt | if-stmt | while-stmt
              | for-stmt | match-stmt | parallel-stmt | return-stmt
              | break-stmt | continue-stmt | expr-stmt | block
              | unsafe-block | asm-stmt | spawn-stmt | scope-stmt
              | rewrite-stmt | assume-stmt ;
const-stmt    = "const" type IDENT "=" expr ";" ;
var-stmt      = type IDENT "=" expr ";" ;
assign-stmt   = lvalue "=" expr ";" ;
if-stmt       = "if" "(" expr ")" block [ "else" ( if-stmt | block ) ] ;
while-stmt    = "while" "(" expr ")" block ;
for-stmt      = "for" "(" IDENT "in" expr ")" block ;
match-stmt    = "match" "(" expr ")" "{" arm { "," arm } "}" ;
arm           = pattern "=>" ( expr | block ) ;
pattern       = IDENT | IDENT "(" [ IDENT { "," IDENT } ] ")" | "self" ;
parallel-stmt = "parallel" block ;
return-stmt   = "return" [ expr ] ";" ;
break-stmt    = "break" ";" ;
continue-stmt = "continue" ";" ;
expr-stmt     = expr ";" ;
unsafe-block  = "unsafe" block ;
asm-stmt      = "asm" "(" STRING { "," STRING } ")" ";" ;
spawn-stmt    = "spawn" IDENT "(" [ args ] ")" ";" ;
scope-stmt    = "scope" block ;
rewrite-stmt  = "rewrite" IDENT block ;
assume-stmt   = "assume" expr ";" ;

(* ---------------- 表达式 ---------------- *)
expr          = or-expr ;
or-expr       = and-expr { ( "||" | "or" ) and-expr } ;
and-expr      = not-expr { ( "&&" | "and" ) not-expr } ;
not-expr      = [ "!" | "not" ] cmp-expr ;
cmp-expr      = add-expr { ( "==" | "!=" | "<" | "<=" | ">" | ">=" ) add-expr } ;
add-expr      = mul-expr { ( "+" | "-" ) mul-expr } ;
mul-expr      = unary { ( "*" | "/" | "%" ) unary } ;
unary         = [ "-" ] postfix ;
postfix       = primary { call-suffix | index-suffix | field-suffix } ;
call-suffix   = "(" [ args ] ")" ;
index-suffix  = "[" expr "]" ;
field-suffix  = "." IDENT [ "(" [ args ] ")" ] ;
primary       = INT | STRING | "true" | "false" | path | "(" expr ")" | array-lit ;
array-lit     = "[" [ expr { "," expr } ] "]" ;
args          = expr { "," expr } ;
path          = IDENT { "." IDENT } ;
lvalue        = path | path "[" expr "]" ;

(* ---------------- 声明文件 .tu 的形态（词汇表来源） ---------------- *)
tu-module     = { tu-item } ;
tu-item       = spec-decl | extern-decl | const-decl | struct-decl
              | enum-decl | error-decl | use-decl ;
spec-decl     = "spec" IDENT "(" [ params ] ")" [ "->" type ] "{" { tu-clause } "}" ;
extern-decl   = "extern" IDENT "(" [ params ] ")" [ "->" type ]
                "from" STRING [ "effects" "{" [ IDENT { "," IDENT } ] "}" ] ";" ;
error-decl    = "error" IDENT "{" IDENT { "," IDENT } "}" ";" ;
tu-clause     = "effects" "{" [ IDENT { "," IDENT } ] "}" ";"
              | "guarantees" expr ";"
              | "assume" expr ";"
              | "repeats" expr ";"
              | "recursion" "depth" "<=" INT ";"
              | "target" "{" { dim ":" "{" slot ":" cost-expr "}" } "}" ";"
              | "external" "{" IDENT ":" cost-expr { "," IDENT ":" cost-expr } "}" ";"
              | "benchmodel" "{" "data" "{" binding "}" "scale" "{" scale-binding "}" "}" ;
choose-decl   = "choose" IDENT "(" [ params ] ")" [ "->" type ] "{"
                "over" "{" IDENT { "," IDENT } "}" ";"
                "objective" IDENT ";" "}" [ ";" ] ;
cost-expr     = "O" "(" expr ")" ;
"""


# ---------------------------------------------------------------- 模块词汇表

def module_vocab(tu_mod) -> Dict[str, Any]:
    """本模块允许出现的名字与签名（受限解码的白名单）。"""
    funcs: List[Dict[str, Any]] = []
    externs: List[Dict[str, Any]] = []
    consts: List[Dict[str, Any]] = []
    types: List[Dict[str, Any]] = []

    for node in (tu_mod.children if tu_mod is not None else []):
        k = node.kind
        if k == "spec":
            funcs.append({
                "name": node.name,
                "params": _params(node),
                "returns": _returns(node),
                "effects": _effects(node),
                "has_target": _has(node, "target"),
                "line": getattr(node, "line", 0),
            })
        elif k == "extern":
            effs = []
            ee = next((c for c in node.children if c.kind == "extern-effects"), None)
            for c in (ee.children if ee else []):
                if c.kind == "effect-name" and c.name:
                    effs.append(c.name)
            externs.append({
                "name": node.name,
                "params": _params(node),
                "returns": _returns(node),
                "effects": effs,
                "library": (node.value or {}).get("lib") if isinstance(node.value, dict) else None,
                "line": getattr(node, "line", 0),
            })
        elif k == "const":
            ty = next((c for c in node.children if c.kind.startswith("type")), None)
            consts.append({"name": node.name, "type": _type_of(ty) if ty else "",
                           "line": getattr(node, "line", 0)})
        elif k in ("struct", "enum", "error"):
            types.append({"name": node.name, "kind": k, "line": getattr(node, "line", 0)})

    return {"functions": funcs, "externs": externs, "constants": consts, "types": types}


def skeletons(tu_mod) -> List[Dict[str, Any]]:
    """每个函数一个"签名齐全、body 是洞"的骨架。"""
    out = []
    for f in module_vocab(tu_mod)["functions"]:
        ps = ", ".join(f"{p['type']} {p['name']}" for p in f["params"])
        ret = f" -> {f['returns']}" if f["returns"] else ""
        out.append({
            "id": f"{f['name']}.body",
            "signature": f"func {f['name']}({ps}){ret}",
            "returns": f["returns"],
            "hole_grammar": "block",
            "effects": f["effects"],
        })
    return out


# ---------------------------------------------------------------- 渲染

def render(tu_mod, fmt: str = "both") -> str:
    """`fmt ∈ {ebnf, json, both}`。"""
    import json

    ebnf = language_ebnf()
    payload = {
        "language": {"ebnf": ebnf},
        "vocabulary": module_vocab(tu_mod),
        "holes": skeletons(tu_mod),
    }
    if fmt == "ebnf":
        return ebnf
    if fmt == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)
    if fmt == "both":
        return ebnf + "\n\n(* ======== 词汇表（JSON） ======== *)\n" + \
            json.dumps({"vocabulary": payload["vocabulary"], "holes": payload["holes"]},
                       ensure_ascii=False, indent=2)
    raise ValueError(f"未知格式：{fmt}（可选 ebnf / json / both）")
