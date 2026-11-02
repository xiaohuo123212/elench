# -*- coding: utf-8 -*-
"""**kind 注册表**：每个 AST kind 在两个值域里分别由哪个方法处理。

## 它为什么存在

`interp.py`（具体值）与 `cost_domain/cost_interp.py`（符号值）以前各自用
一条 `if k == "..."` 长链分派。两条链**已经漂移过四次**（递归、内建实参、
`min`/`max`、函数值），每次都是"某一侧补了一个分支，另一侧没有"——
编译器不报错，运行时也不崩，只是 κ 悄悄偏小。

现在分派是**查表**：`_core_expr` / `_core_eval` 只做
`_name = COST_EXPR.get(k)` + `getattr(self, _name)`，分支体各自是一个 `_k_*` 方法。
于是"这个 kind 在两个域里各做什么"变成**一张能读的表**，而不是两条要人工对照的链。

## 改名约定

方法名带层前缀：`_k_expr_call` / `_k_stmt_assign`。
理由：同一个类里 `_core_expr` 和 `_core_stmt` 都会处理 `dispatch`，
不带前缀会**互相覆盖**（第二份定义悄悄顶掉第一份）。

## 不要做的事

- 不要在这里加"某个 kind 只有一侧"的条目——`tests/cases/test_drift.py` 会红；
- 真要留一侧，就写进 `walk.py` 的 `IGNORED`（不产生成本）或
  `UNIMPLEMENTED`（两侧都没做），那两张表有反向校验。
"""

#: 成本域（符号值）：表达式 kind → 方法名
#:
#: 一个方法可以服务多个 kind（例如 `lit-int` / `path` / `ref` 都记 1 个操作），
#: 但**每一侧都必须在表里出现**，不允许"落到兜底"。

COST_EXPR = {
    "binary": '_k_expr_binary',
    "call": '_k_expr_call',
    "cast": '_k_expr_binary',
    "dispatch": '_k_expr_dispatch',
    "field": '_k_expr_index',
    "index": '_k_expr_index',
    "lit-bool": '_k_expr_lit_int',
    "lit-char": '_k_expr_lit_int',
    "lit-float": '_k_expr_lit_int',
    "lit-int": '_k_expr_lit_int',
    "lit-string": '_k_expr_lit_int',
    "method-call": '_k_expr_method_call',
    "paren": '_k_expr_paren',
    "path": '_k_expr_lit_int',
    "ref": '_k_expr_lit_int',
    "struct-literal": '_k_expr_struct_literal',
    "try": '_k_expr_try',
    "tuple": '_k_expr_tuple',
    "type-name": '_k_expr_type_name',
    "unary": '_k_expr_binary',
}

COST_STMT = {
    "break": '_k_stmt_break',
    "continue": '_k_stmt_continue',
    "assign": '_k_stmt_assign',
    "block": '_k_stmt_block',
    "const-decl": '_k_stmt_const_decl',
    "dispatch": '_k_stmt_dispatch',
    "expr-stmt": '_k_stmt_expr_stmt',
    "for": '_k_stmt_for',
    "if": '_k_stmt_if',
    "match": '_k_stmt_match',
    "parallel": '_k_stmt_parallel',
    "return": '_k_stmt_return',
    "var-decl": '_k_stmt_const_decl',
    "while": '_k_stmt_while',
}

INTERP_EXPR = {
    "binary": '_k_expr_binary',
    "call": '_k_expr_call',
    "cast": '_k_expr_cast',
    "dispatch": '_k_expr_dispatch',
    "field": '_k_expr_field',
    "index": '_k_expr_index',
    "lit-bool": '_k_expr_lit_bool',
    "lit-char": '_k_expr_lit_char',
    "lit-float": '_k_expr_lit_float',
    "lit-int": '_k_expr_lit_int',
    "lit-string": '_k_expr_lit_string',
    "method-call": '_k_expr_method_call',
    "paren": '_k_expr_paren',
    "path": '_k_expr_path',
    "ref": '_k_expr_ref',
    "struct-literal": '_k_expr_struct_literal',
    "try": '_k_expr_try',
    "tuple": '_k_expr_tuple',
    "type-name": '_k_expr_type_name',
    "unary": '_k_expr_unary',
}

INTERP_STMT = {
    "assign": '_k_stmt_assign',
    "break": '_k_stmt_break',
    "continue": '_k_stmt_continue',
    "block": '_k_stmt_block',
    "const-decl": '_k_stmt_const_decl',
    "dispatch": '_k_stmt_dispatch',
    "expr-stmt": '_k_stmt_expr_stmt',
    "for": '_k_stmt_for',
    "if": '_k_stmt_if',
    "match": '_k_stmt_match',
    "parallel": '_k_stmt_parallel',
    "return": '_k_stmt_return',
    "var-decl": '_k_stmt_const_decl',
    "while": '_k_stmt_while',
}


#: 合并视图：`(层, kind) -> {"interp": 方法名|None, "cost": 方法名|None}`
#:
#: **键必须带层**：`dispatch` 既是表达式节点也是语句节点，只用 kind 做键会把两张表
#: 混成一条（看起来"两侧都有"，实际是语句那一侧顶掉了表达式那一侧）。
KINDS = {}
for _layer, _i, _c in (("expr", INTERP_EXPR, COST_EXPR),
                       ("stmt", INTERP_STMT, COST_STMT)):
    for _k in set(_i) | set(_c):
        KINDS[(_layer, _k)] = {"interp": _i.get(_k), "cost": _c.get(_k)}
