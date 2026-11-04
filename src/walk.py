# -*- coding: utf-8 -*-
"""**双值域 kind 覆盖的守门器**：对着 `kinds.py` 的注册表做四类检查。

## 它防什么

`interp.py`（具体值）与 `cost_domain/cost_interp.py`（符号值）以前各有一条
`if k == "..."` 长链。两条链**漂移过四次**（递归、内建实参、`min`/`max`、函数值），
每次都是"一侧补了、另一侧没补"——编译不报错、运行不崩，只是 κ 悄悄偏小。

现在分派改成查表（`kinds.py`），这个文件负责：

1. **两域对称**：表达式层与语句层，两个域登记的 kind 集合**必须完全相等**；
2. **登记即实现**：表里写的每个方法名，必须在对应类上**真的存在**；
3. **清空单不过期**：`IGNORED`（不产生成本）/ `UNIMPLEMENTED`（两侧都没做）
   的 kind，**不能**出现在任何一张表里；
4. **没有孤儿**：每个 kind 要么登记、要么写理由。

以前这四条靠"人记得看"，现在它们是 `tests/cases/test_drift.py` 的断言。

**注**：这里不再从源码正则抽 `if k == ...`。静态抽取会被注释骗
（把死分支改成注释后，检测器曾报出两个**不存在**的 kind），而且分派已经变成查表，
**表本身才是唯一的事实**。
"""

import sys

from kinds import COST_EXPR, COST_STMT, INTERP_EXPR, INTERP_STMT

#: 解析器能产出的 kind 全集（`parser.py` 里 `Node("...")` 的一级 kind）
ALL_KINDS = frozenset("""
args arm array assign assign-op assume bench-data benchmodel bench-scale binary binding block
call candidate cast choose choose-clause clause const const-decl deref dim dispatch enum
error-decl expr-stmt extern field field-init fn for if impl index kernels lit-bool lit-char
lit-float lit-int lit-string match method-call module named-arg param params paren path
pattern pattern-wildcard ref return rewrite scale-binding seed slot spec spec-body step
struct struct-literal trait trait-method try tuple type-args type-const type-name type-ref
type-slice type-tuple unary use var-decl variant while
""".split())

#: **故意忽略**的 kind（不携带成本、不包裹可执行子节点）。每条都要写理由。
IGNORED = {
    "type-ref": "纯类型引用",
    "type-args": "泛型实参，编译期擦除",
    "type-const": "泛型常量实参，编译期",
    "type-slice": "类型修饰",
    "type-tuple": "类型修饰",
    "params": "形参列表容器，由 `_body` 按名绑定",
    "param": "单个形参，由 `_body` 处理",
    "args": "实参容器，由 `_call` 逐个求值",
    "named-arg": "具名实参，由 `_call` 处理",
    "binding": "pattern 绑定名",
    "pattern": "match 臂的模式，由 `_match` 处理",
    "pattern-wildcard": "`_` 臂，由 `_match` 处理",
    "arm": "match 的一个臂，由 `_match` 处理",
    "field-init": "结构体字面量的字段，由 struct-literal 处理",
    "clause": "spec 子句，不在函数体内",
    "choose-clause": "choose 子句，不在函数体内",
    "spec": "声明文件节点",
    "spec-body": "声明文件节点",
    "choose": "候选声明，由 `_dispatch` 读",
    "candidate": "候选声明",
    "kernels": "候选实现块，由 `_dispatch` 读",
    "module": "模块根",
    "fn": "函数定义",
    "use": "use 声明（当前不加载）",
    "extern": "外部声明",
    "assume": "**已移除的语法**（2026-11-04）：解析阶段直接报 E-Assume-Removed",
    "enum": "枚举声明",
    "variant": "枚举变体",
    "struct": "结构体声明",
    "trait": "trait 声明",
    "trait-method": "trait 方法声明",
    "impl": "impl 块",
    "error-decl": "错误类型声明",
    "const": "模块常量声明（值由 const_values 读）",
    "seed": "benchmodel 字段",
    "step": "bench-scale 字段",
    "dim": "bench-scale 维度",
    "slot": "benchmodel 槽位",
    "scale-binding": "bench-scale 绑定",
    "bench-data": "benchmodel 字段",
    "bench-scale": "benchmodel 字段",
    "benchmodel": "benchmodel 声明块，不在函数体内",
    "rewrite": "benchmodel 重写规则",
}

#: **解析器支持，但两个值域都还没实现**。判据双向：进这张表就**两侧都不能有分支**。
UNIMPLEMENTED = {
    "assign-op": "E25：`x += 1` 能解析；interp 抛「未实现的语句」，cost 给 ⊤（保守但无真实语义）",
    "array": "E25：数组字面量能解析，两个域都未实现",
    "deref": "E25：解引用能解析，两个域都未实现",
}

#: 四个入口：两个值域 × 语句/表达式
TABLES = {
    ("interp", "stmt"): INTERP_STMT,
    ("interp", "expr"): INTERP_EXPR,
    ("cost", "stmt"): COST_STMT,
    ("cost", "expr"): COST_EXPR,
}


def report() -> dict:
    """`{"table": {(域, 层): 该域登记的 kind 集合}}`。"""
    return {"table": {k: frozenset(v) for k, v in TABLES.items()}}


def _table(table=None) -> dict:
    return table if table is not None else report()["table"]


def asymmetry(table=None) -> dict:
    """**漂移**：只被一个域登记的 kind。必须为空。"""
    t = _table(table)
    out = {}
    for layer in ("stmt", "expr"):
        a = frozenset(t.get(("interp", layer), ()))
        b = frozenset(t.get(("cost", layer), ()))
        got = {"interp": sorted(a - b), "cost": sorted(b - a)}
        if got["interp"] or got["cost"]:
            out[layer] = got
    return out


def stale(table=None) -> dict:
    """反向校验：`IGNORED` / `UNIMPLEMENTED` 里有没有**已经有分支**的条目。"""
    t = _table(table)
    handled = set()
    for ks in t.values():
        handled |= set(ks)
    return {
        "ignored_but_handled": sorted(set(IGNORED) & handled),
        "unimplemented_but_handled": sorted(set(UNIMPLEMENTED) & handled),
    }


def unaccounted(table=None) -> list:
    """既没登记、也没写理由的 kind —— 真正的未知。"""
    t = _table(table)
    covered = set(IGNORED) | set(UNIMPLEMENTED)
    for ks in t.values():
        covered |= set(ks)
    return sorted(ALL_KINDS - covered)


def missing_handlers(tables=None) -> list:
    """**登记即实现**：表里写的方法名，必须在类上真的存在。

    没有这一条，删掉一个 `_k_*` 方法时表还是"写得很整齐"，跑到那个 kind 才崩。
    """
    from interp import Interp
    from cost_domain.cost_interp import CostInterp

    owner = {"interp": Interp, "cost": CostInterp}
    bad = []
    for (dom, layer), tab in (tables or TABLES).items():
        cls = owner[dom]
        for kind, name in tab.items():
            if not hasattr(cls, name):
                bad.append(f"{dom}/{layer}: {kind} → {name} 不存在")
    return sorted(bad)


def problems(table=None) -> dict:
    """四类问题合成一张单子；**全部为空**才算干净。"""
    return {
        "asymmetry": asymmetry(table),
        "stale": stale(table),
        "unaccounted": unaccounted(table),
        "missing": missing_handlers(),
    }


if __name__ == "__main__":
    r = report()
    print("== 两值域各自登记的 kind ==")
    for (dom, layer), ks in sorted(r["table"].items()):
        print(f"  {dom:6} {layer:4}  {len(ks):2} 个: {sorted(ks)}")
    p = problems(r["table"])
    print("\n== 漂移（只在一侧登记）==")
    if p["asymmetry"]:
        for layer, d in p["asymmetry"].items():
            print(f"  {layer}: 只在 interp = {d['interp']}")
            print(f"  {layer}: 只在 cost   = {d['cost']}")
    else:
        print("  无")
    print("\n== 清空单过期 ==")
    print(f"  IGNORED 里其实有分支 = {p['stale']['ignored_but_handled'] or '无'}")
    print(f"  UNIMPLEMENTED 里其实有分支 = {p['stale']['unimplemented_but_handled'] or '无'}")
    print("\n== 登记了但方法不存在 ==")
    print(f"  {p['missing'] or '无'}")
    print("\n== 既没登记也没写理由（真未知）==")
    print(f"  {p['unaccounted'] or '无'}")

    bad = (p["asymmetry"] or p["stale"]["ignored_but_handled"]
           or p["stale"]["unimplemented_but_handled"] or p["missing"] or p["unaccounted"])
    sys.exit(1 if bad else 0)
