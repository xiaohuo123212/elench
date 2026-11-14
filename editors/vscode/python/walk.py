# -*- coding: utf-8 -*-
"""**双值域 kind 覆盖的守门器**：对着 `kinds.py` 的注册表做六类检查。

## 它防什么

`interp.py`（具体值）与 `cost_domain/cost_interp.py`（符号值）以前各有一条
`if k == "..."` 长链。两条链**漂移过四次**（递归、内建实参、`min`/`max`、函数值），
每次都是"一侧补了、另一侧没补"——编译不报错、运行不崩，只是 κ 悄悄偏小。

现在分派改成查表（`kinds.py`），这个文件负责：

1. **两域对称**：表达式层与语句层，两个域登记的 kind 集合**必须完全相等**
   （比较的是"每个域真正处理过的集合"：查表分派 ∪ 前置分支）；
2. **登记即实现**：表里写的每个方法名，必须在对应类上**真的存在**；
3. **清空单不过期**：`IGNORED`（不产生成本）/ `UNIMPLEMENTED`（两侧都没做）
   的 kind，**不能**出现在任何一张表里——**也不能出现在前置分支里**；
4. **没有孤儿**：每个 kind 要么登记、要么写理由；
5. **前置分支也要登记**（这条是 2026-11-10 补的）：`_eval` / `_expr` 在查表
   **之前**就 `return` 的 kind 写在 `kinds.INTERP_PRE_EXPR` / `COST_PRE_EXPR`。
   以前的检查**只看那四张表**，于是 `deref`（`_eval` 里明明有分支）一直躺在
   `UNIMPLEMENTED` 里而 `stale()` 查不出来——"表说没实现、实际已实现"，
   而这张表是我们判断"某个 kind 到底被谁处理了"的唯一依据；
6. **跟 parser 对账**：`ALL_KINDS` = `parser.py` 真的能产出的 kind 全集；
   登记了却产不出的写进 `NOT_PRODUCED`。反向对账由
   `tests/cases/test_drift.py` 用 `ast` 扫 `parser.py` 来做。

以前这些靠"人记得看"，现在它们是 `tests/cases/test_drift.py` 的断言。

**注**：这里不再从源码正则抽 `if k == ...`。静态抽取会被注释骗
（把死分支改成注释后，检测器曾报出两个**不存在**的 kind），而且分派已经变成查表，
**表本身才是唯一的事实**。唯一还对源码做静态扫描的是 `test_drift.py` 里
"parser 能产出哪些 kind"这一条——它用 `ast` 而不是正则，注释里的字符串骗不过去。
"""

import sys

from kinds import (COST_EXPR, COST_STMT, INTERP_EXPR, INTERP_STMT,
                   PRE_EXPR)

#: 解析器能产出的 kind 全集（`parser.py` 里 `Node("...")` 的一级 kind）。
#:
#: **必须与 parser 同步**：缺少的（以前漏了 `lit-null` / `type-ptr` / `list-init` /
#: `break` / `continue` / `parallel` … 共 22 个）会让 `unaccounted()` **静默**漏检——
#: 不在 `ALL_KINDS` 里的 kind 永远不算"孤儿"。反向对照见 `NOT_PRODUCED`，
#: 双向由 `tests/cases/test_drift.py` 的 `[6]` 钉住。
ALL_KINDS = frozenset("""
args arm array assign assign-op bench-data bench-scale benchmodel binary binding block
break call candidate cast cast-static choose choose-clause clause const const-decl continue deref dim
dispatch effect-name enum error-decl expr-stmt ext-entry extern extern-effects external
field field-init fn for if index kernels link list-init lit-bool lit-char lit-float lit-int
lit-null lit-string match method-call module named-arg namespace parallel param params
paren path pattern pattern-literal pattern-wildcard ref return rewrite scale-binding seed
size-bind slot spec spec-body step struct struct-literal ternary try tuple type-args type-auto
type-const type-fn type-fn-params type-name type-param type-params type-ptr type-ref
type-slice type-tuple unary use using-namespace val-decl var-decl variant while iter-arg
""".split())

#: **登记了但 parser 产不出**——漂移的另一个方向（表里有、语法里没有）。
#:
#: 它们是**声明事实**，不是"实现"：不进 `ALL_KINDS`、不进任何分发表/前置分支。
#: `problems()["not_produced"]` 保证有人把"产不出"的东西写回 `ALL_KINDS` 时立刻红。
NOT_PRODUCED = {
    "assume": "**已移除的语法**（2026-11-04）：解析阶段直接报 E-Assume-Removed，不产出节点",
    "impl": "`impl` 块：parser 只在 `END_KIND` 里认得这个词作块终结符，没有产生节点的分支（2026-11-10 核实）",
    "trait": "trait 声明：同 `impl`——parser 没有产生点",
    "trait-method": "trait 方法声明：依赖 trait，parser 没有产生点",
}

#: **故意忽略**的 kind（不携带成本、不包裹可执行子节点）。每条都要写理由。
IGNORED = {
    # -- 类型节点：`typecheck` 读，两个值域都**不进** --
    "type-ref": "纯类型引用",
    "type-args": "泛型实参，编译期擦除",
    "type-const": "泛型常量实参，编译期",
    "type-param": "泛型形参（`Vec<T>` 里的 `T`），编译期擦除",
    "type-params": "泛型形参表，编译期擦除",
    "type-slice": "类型修饰",
    "type-tuple": "类型修饰",
    "type-ptr": "类型修饰（`T*`）：只由 `typecheck._type`（`typecheck.py:660`）与拷贝分类读，不进表达式分派",
    "type-fn": "函数类型 `func(...) -> T`：只由类型检查与拷贝分类读，不进表达式分派",
    "type-fn-params": "函数类型的形参表，编译期",
    # -- P0 兼容层（`--compat`）的两个**编译期** kind：都不进值域 --
    "type-auto": ("`auto x = …;` 的**待推断**类型：由 `typecheck._compat_auto_ty` "
                  "在编译期换成具体类型节点（`ty_to_node`）；换不掉时类型检查报 "
                  "`E-Compat-AutoNoType`，**不会**带着它进 interp / 成本域"),
    "iter-arg": ("`f(xs.begin(), xs.end())` 里的**临时**节点：`parser._compat_fold_iter_args` "
                 "在实参表解析完的那一刻就折成 `xs`（或报「本语言没有迭代器」），"
                 "**从不进入模块 AST**"),
    # -- 容器/绑定容器：由所属的语句或表达式逐个处理 --
    "params": "形参列表容器，由 `_body` 按名绑定",
    "param": "单个形参，由 `_body` 处理",
    "args": "实参容器，由 `_call` 逐个求值",
    "named-arg": "具名实参，由 `_call` 处理",
    "binding": "pattern 绑定名",
    "pattern": "match 臂的模式，由 `_match` 处理",
    "pattern-literal": "match 臂里的字面量模式（`0 => …`），由 `_match` / `ast_util` 的模式识别读",
    "pattern-wildcard": "`_` 臂，由 `_match` 处理",
    "arm": "match 的一个臂，由 `_match` 处理",
    "field-init": "结构体字面量的字段，由 struct-literal 处理",
    "clause": "spec 子句，不在函数体内",
    "choose-clause": "choose 子句，不在函数体内",
    # -- 模块级声明：由 checker / cost 的声明扫描按名读，不在函数体内 --
    "spec": "声明文件节点",
    "spec-body": "声明文件节点",
    "choose": "候选声明，由 `_dispatch` 读",
    "candidate": "候选声明",
    "kernels": "候选实现块，由 `_dispatch` 读",
    "module": "模块根",
    "fn": "函数定义",
    "use": "use 声明（当前不加载）",
    "using-namespace": "`using namespace std;`：只置 `using_std` 标记（`parser.py:843`），无线成本",
    "namespace": "`namespace` 声明：解析期展平（`parser.py:924`），不包裹可执行语义",
    "extern": "外部声明",
    "external": "`external { 名字: O(…) }` 表：成本域按名读复杂度上限（`cost_interp.py:520`）",
    "ext-entry": "`external` 表的一条登记（名字 + 复杂度），声明期元数据",
    "extern-effects": "`extern … effects { … }`：extern 声明的 effect 清单",
    "effect-name": "effect 名字，声明期元数据（`cost_interp.py:4601` 按名读）",
    "link": "`link \"lib\"` 指令：只记库名（`interp.py:2066`），无线成本",
    "enum": "枚举声明",
    "variant": "枚举变体",
    "struct": "结构体声明",
    "error-decl": "错误类型声明",
    "const": "模块常量声明（值由 const_values 读）",
    "seed": "benchmodel 字段",
    "step": "bench-scale 字段",
    "dim": "bench-scale 维度",
    "slot": "benchmodel 槽位",
    "scale-binding": "bench-scale 绑定",
    "size-bind": "`sizes n = …` 的尺寸绑定：由 cost 的分档扫描读（`cost_interp.py:1053`）",
    "bench-data": "benchmodel 字段",
    "bench-scale": "benchmodel 字段",
    "benchmodel": "benchmodel 声明块，不在函数体内",
    "rewrite": "benchmodel 重写规则",
}

#: **解析器支持，但两个值域都还没实现**。判据双向：进这张表就**两侧都不能有分支**——
#: 查表分支（`kinds.*_EXPR` / `*_STMT`）**和**前置分支（`*_PRE_EXPR`）都算。
#:
#: 2026-11-10 的欠账（本文件这次修的就是它）：`deref` / `lit-null` / `type-ptr` 三个
#: kind 都已实现，表里却没登记；其中 `deref` 还错在 `UNIMPLEMENTED` 里。
#: 现在 `deref` → `kinds.INTERP_PRE_EXPR` + `COST_PRE_EXPR`，`lit-null` →
#: `INTERP_PRE_EXPR` + `COST_EXPR`，`type-ptr` → `IGNORED`（类型节点）。
#: `array` 同样有前置分支（两侧都有）⇒ 一并移出，否则 `stale()` 会报它。
UNIMPLEMENTED = {
    "assign-op": "E25：`x += 1` 能解析；interp 抛「未实现的语句」，cost 给 ⊤（保守但无真实语义）",
    "val-decl": ("只有旧关键字 `val` / `let` 能产出（`parser.parse_let`），而它**同时**报 "
                 "E-Decl-OldKeyword；AST 保留只是为了不让下游级联报「未声明」。"
                 "interp 会抛「未实现的语句」，cost 只把它当声明扫描容器尺寸"
                 "（`cost_interp.py:1318`），没有语句分支"),
}

#: 四个入口：两个值域 × 语句/表达式
TABLES = {
    ("interp", "stmt"): INTERP_STMT,
    ("interp", "expr"): INTERP_EXPR,
    ("cost", "stmt"): COST_STMT,
    ("cost", "expr"): COST_EXPR,
}


def _consumers() -> dict:
    """`kind -> (登记去向, …)`：一个 kind 到底被谁处理了。"""
    out = {}

    def _add(kind, where):
        out.setdefault(kind, []).append(where)

    for (dom, layer), tab in TABLES.items():
        for kind, name in tab.items():
            _add(kind, f"{dom}/{layer}: {name}")
    for dom, pre in PRE_EXPR.items():
        for kind, why in pre.items():
            _add(kind, f"{dom}/expr(前置): {why}")
    for kind, why in IGNORED.items():
        _add(kind, f"ignored: {why}")
    for kind, why in UNIMPLEMENTED.items():
        _add(kind, f"unimplemented: {why}")
    return {k: tuple(v) for k, v in out.items()}


#: **每个 kind 的登记去向**：查表分派 / 前置分支 / 写清理由的忽略 / 写清理由的未实现。
#:
#: 这是"某个 kind 到底被谁处理了"的**唯一索引**。`ALL_KINDS` 里的每个 kind
#: 都必须在这里至少有一条，否则就是漂移（`test_drift.py` 断言）。
#: 用法：`[k for k in walk.ALL_KINDS if k not in walk.CONSUMERS]` 必须为空。
CONSUMERS = _consumers()


def report() -> dict:
    """`{"table": {(域, 层): 该域登记的 kind 集合}, "pre": {域: 前置分支的 kind 集合}}`。"""
    return {
        "table": {k: frozenset(v) for k, v in TABLES.items()},
        "pre": {dom: frozenset(t) for dom, t in PRE_EXPR.items()},
    }


def _table(table=None) -> dict:
    return table if table is not None else report()["table"]


def _handled(table, dom: str, layer: str) -> frozenset:
    """某个域在某一层**真正处理过**的 kind：查表分派 ∪ 前置分支。"""
    got = frozenset(table.get((dom, layer), ()))
    if layer == "expr":
        got |= frozenset(PRE_EXPR.get(dom, {}))
    return got


def handled(dom: str, layer: str, table=None) -> frozenset:
    """公开版 `_handled`：`(域, 层)` 处理过的 kind 集合。两域必须相等。"""
    return _handled(_table(table), dom, layer)


def asymmetry(table=None) -> dict:
    """**漂移**：只被一个域登记的 kind。必须为空。

    比较的是"处理过的集合"，**包含前置分支**——只看那四张表的话，
    `deref`（两侧都在 `_eval` / `_expr` 的前置分支里）会被误判成"两侧都没有"。
    """
    t = _table(table)
    out = {}
    for layer in ("stmt", "expr"):
        a = _handled(t, "interp", layer)
        b = _handled(t, "cost", layer)
        got = {"interp": sorted(a - b), "cost": sorted(b - a)}
        if got["interp"] or got["cost"]:
            out[layer] = got
    return out


def stale(table=None) -> dict:
    """反向校验：`IGNORED` / `UNIMPLEMENTED` 里有没有**已经有分支**的条目。

    "有分支"包括**前置分支**——这正是 `deref` 漏掉的原因：它在 `_eval` 里
    `if _k == "deref"` 有分支，但那四张表里没有它，所以只比对表的旧版
    `stale()` 报"无"，测试照旧绿。

    传 `table=` 时只校验那张人造表（反向控制用），**不掺**前置分支；
    真实登记表（`table=None`）才把前置分支算进去。
    """
    t = _table(table)
    handled = set()
    for ks in t.values():
        handled |= set(ks)
    if table is None:
        for pre in PRE_EXPR.values():
            handled |= set(pre)
    return {
        "ignored_but_handled": sorted(set(IGNORED) & handled),
        "unimplemented_but_handled": sorted(set(UNIMPLEMENTED) & handled),
    }


def unaccounted(table=None) -> list:
    """既没登记、也没写理由的 kind —— 真正的未知。"""
    covered = set(CONSUMERS)
    for ks in _table(table).values():
        covered |= set(ks)
    return sorted(ALL_KINDS - covered)


def not_produced(table=None) -> list:
    """**漂移的另一个方向**：声明「parser 产不出」的 kind，不许出现在 `ALL_KINDS` / 分发表里。"""
    regd = set(ALL_KINDS)
    for ks in _table(table).values():
        regd |= set(ks)
    for pre in PRE_EXPR.values():
        regd |= set(pre)
    return sorted(set(NOT_PRODUCED) & regd)


def stray_reason() -> list:
    """写了 `IGNORED` / `UNIMPLEMENTED` 理由、却既不是 parser kind 也没声明「产不出」。"""
    known = set(ALL_KINDS) | set(NOT_PRODUCED)
    return sorted((set(IGNORED) | set(UNIMPLEMENTED)) - known)


def missing_handlers(tables=None, pre=None) -> list:
    """**登记即实现**：表里写的方法名，必须在类上真的存在。

    没有这一条，删掉一个 `_k_*` 方法时表还是"写得很整齐"，跑到那个 kind 才崩。
    前置分支里以 `_` 开头的值同样是方法名，一并检查。
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
    if tables is None:                        # 前置分支只在真实登记表上检查
        for dom, tab in (pre or PRE_EXPR).items():
            cls = owner[dom]
            for kind, name in tab.items():
                if name.startswith("_") and not hasattr(cls, name):
                    bad.append(f"{dom}/expr(前置): {kind} → {name} 不存在")
    return sorted(bad)


def problems(table=None) -> dict:
    """六类问题合成一张单子；**全部为空**才算干净。"""
    return {
        "asymmetry": asymmetry(table),
        "stale": stale(table),
        "unaccounted": unaccounted(table),
        "missing": missing_handlers(table),
        "not_produced": not_produced(table),
        "stray_reason": stray_reason(),
    }


if __name__ == "__main__":
    r = report()
    print("== 两值域各自登记的 kind ==")
    for (dom, layer), ks in sorted(r["table"].items()):
        print(f"  {dom:6} {layer:4}  {len(ks):2} 个: {sorted(ks)}")
    for dom, ks in sorted(r["pre"].items()):
        print(f"  {dom:6} expr(前置) {len(ks)} 个: {sorted(ks)}")

    p = problems()          # 真实登记表：`stale()` 会把前置分支一起算
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
    print("\n== 声明「parser 产不出」却出现在表里 ==")
    print(f"  {p['not_produced'] or '无'}")
    print("\n== 理由写了、却不是 parser kind ==")
    print(f"  {p['stray_reason'] or '无'}")
    print(f"\n== 每个 kind 的登记去向：{len(CONSUMERS)} 个 kind ==")

    bad = (p["asymmetry"] or p["stale"]["ignored_but_handled"]
           or p["stale"]["unimplemented_but_handled"] or p["missing"]
           or p["unaccounted"] or p["not_produced"] or p["stray_reason"])
    sys.exit(1 if bad else 0)
