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
- 分支在 `_eval` / `_expr` 的**前置判断**里（`if _k == "..."` 直接 return）的 kind，
  写进下面的 `INTERP_PRE_EXPR` / `COST_PRE_EXPR`——**不要**塞进 `INTERP_EXPR` /
  `COST_EXPR`（那两张表查不到前置分支），也**不要**留在 `UNIMPLEMENTED` 里
  （那会让"表说没实现、实际已实现"再次发生）。
"""

#: 成本域（符号值）：表达式 kind → 方法名
#:
#: 一个方法可以服务多个 kind（例如 `lit-int` / `path` / `ref` 都记 1 个操作），
#: 但**每一侧都必须在表里出现**，不允许"落到兜底"。

COST_EXPR = {
    "binary": '_k_expr_binary',
    "call": '_k_expr_call',
    "cast": '_k_expr_binary',
    # **P0 兼容层第 11 条的临时节点**：能无损折叠的 `static_cast` 在
    # `parser.parse_source` / `typecheck.resolve_static_cast` 里就换成实参了；
    # 只有**折不掉的**（窄化 / 有符号 / 类型推不出）才会留到值域。
    # 两个域都不许把它当恒等：interp 直接报错，成本域退 ⊤（见各自实现）。
    "cast-static": '_k_expr_cast_static',
    "dispatch": '_k_expr_dispatch',
    "field": '_k_expr_index',
    "index": '_k_expr_index',
    "list-init": '_k_expr_list_init',
    "lit-bool": '_k_expr_lit_int',
    "lit-char": '_k_expr_lit_int',
    "lit-float": '_k_expr_lit_int',
    "lit-int": '_k_expr_lit_int',
    # **`nullptr` 也是一个表达式节点**：解释器 `_eval` 对每个非 None 节点记 1，
    # 这里原先没有条目 ⇒ 落 `_core_expr` 兜底 `Cost.num(0)`，κ 每个 `nullptr` **少算 1**
    # （上界不成立的方向）。登记到 `_k_expr_lit_int` 与其它叶子同口径：记 1。
    "lit-null": '_k_expr_lit_int',
    "lit-string": '_k_expr_lit_int',
    "method-call": '_k_expr_method_call',
    "paren": '_k_expr_paren',
    "path": '_k_expr_lit_int',
    "ref": '_k_expr_lit_int',
    "struct-literal": '_k_expr_struct_literal',
    # 见 `INTERP_EXPR` 里同一条：成本域 **max**，不是相加。
    "ternary": '_k_expr_ternary',
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
    "cast-static": '_k_expr_cast_static',
    "dispatch": '_k_expr_dispatch',
    "field": '_k_expr_field',
    "index": '_k_expr_index',
    "list-init": '_k_expr_list_init',
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
    # **三目 `c ? a : b`**（2026-11-13）：两个域都是"条件照算 + 只取一支"。
    # 成本域取两支的 **max**（不是相加）——只执行一支，相加会把界放大一倍。
    "ternary": '_k_expr_ternary',
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


#: **查表之前的前置分支**：`Interp._eval` / `CostInterp._expr` 在 `_core_eval` /
#: `_core_expr` **之前**就 `return` 的 kind（`interp.py:3832-3849` /
#: `cost_interp.py:3644-3649`）。
#:
#: 为什么单列，而不是塞进 `INTERP_EXPR` / `COST_EXPR`：
#:
#: - 那两张表是 `_core_*` 里的 `getattr` **查表分派**；前置分支在查表之前就返回了，
#:   写进表里也不会被读到 —— 表就成了"没人读的注释"；
#: - 但**不登记**更糟：`walk.py` 只知道表，于是"代码里有分支、表里没有"的 kind
#:   会被判成"没实现"。2026-11-10 的欠账正是这样：`deref` 在 `_eval` 里有分支、
#:   却一直躺在 `walk.UNIMPLEMENTED` 里，而 `walk.stale()` 只比对那两张表、
#:   永远查不出来。
#:
#: 值 = **实际实现**：以 `_` 开头的写方法名（`walk.missing_handlers()` 会检查它
#: 真的存在于类上），其余写一句表达式说明。
#:
#: 注：`lit-float` 同时有前置分支（`interp.py:3832`）和 `INTERP_EXPR` 条目
#: （`_k_expr_lit_float`，等价备份）——前置分支先命中，那条表项现在不会被读到，
#: 但两份实现语义相同，所以不删；`lit-null` 没有可用的 `_k_*` 方法，只在前置分支。
INTERP_PRE_EXPR = {
    "array": "`_eval` 前置分支：逐元素求值成 Python 列表",
    "deref": "_deref_read",
    "lit-null": "`_eval` 前置分支：返回 None（`nullptr` 的运行期值）",
}

COST_PRE_EXPR = {
    "array": "`_expr` 前置分支：1 + Σ子节点",
    "deref": "`_expr` 前置分支：1 + Σ子节点（与 `_k_expr_index` 同形）",
}

#: `域 -> {kind: 前置分支实现}`。`walk.py` 把它并进"每个域处理过的集合"。
PRE_EXPR = {"interp": INTERP_PRE_EXPR, "cost": COST_PRE_EXPR}


#: 合并视图：`(层, kind) -> {"interp": 方法名|None, "cost": 方法名|None}`
#:
#: **键必须带层**：`dispatch` 既是表达式节点也是语句节点，只用 kind 做键会把两张表
#: 混成一条（看起来"两侧都有"，实际是语句那一侧顶掉了表达式那一侧）。
KINDS = {}
for _layer, _i, _c in (("expr", INTERP_EXPR, COST_EXPR),
                       ("stmt", INTERP_STMT, COST_STMT)):
    for _k in set(_i) | set(_c):
        KINDS[(_layer, _k)] = {"interp": _i.get(_k), "cost": _c.get(_k)}

# 前置分支并进合并视图：值是"前置: 实现说明"，**不是** `_k_*` 方法名。
for _dom, _pre in (("interp", INTERP_PRE_EXPR), ("cost", COST_PRE_EXPR)):
    for _k, _why in _pre.items():
        _slot = KINDS.setdefault(("expr", _k), {"interp": None, "cost": None})
        _slot[_dom] = f"前置: {_why}"
