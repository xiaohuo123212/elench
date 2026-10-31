#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

"""walk.py —— 双值域的 kind 覆盖表（主批 A–D Step 2）

**为什么需要这个文件**

`interp.py`（具体值域）与 `cost_interp.py`（符号值域）**各自**用 if 链分派 AST 节点。
两份清单**已经漂移过**：

    只在 interp 里处理：index / field / lit-bool / lit-char / tuple / struct-literal
    只在 cost_interp 里处理：const / size（Bound 的内部 kind）

漂移的后果是**静默的**：某一边漏了一个 kind，就走兜底 `return 0`，
成本变成 0，**上界不成立而没人知道**。

**这个文件做的事**：把"谁处理了哪些 kind"变成**可执行、可断言的表**——
从源码里**静态抽取**（不用插桩、不改运行时），然后与解析器能产出的 kind 全集对账。
对不上的必须进 `IGNORED` 并**写明理由**，否则测试失败。

**注意**：这是"让漂移变成响亮的失败"，不是"统一分派"。
统一分派（表驱动 / 装饰器注册）留到以后——**先有能报警的表，再谈重构**。
"""
import io
import os
import re

SRC = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# 解析器能产出的 kind 全集
#
# 来源（可复现）：在 `src/` 下跑
#   Select-String -Path parser.py -Pattern 'Node\("([a-z-]+)"' -AllMatches
# 取第一组并去重。
#
# **为什么不从源码动态抽**：`Node(...)` 有一部分是通过辅助函数构造的（kind 是变量），
# 静态正则**会漏**。宁可"少算"（漏掉新 kind 时测试不误报），也不要"算错"。
# ---------------------------------------------------------------------------
ALL_KINDS = frozenset("""
args arm array assign assign-op assume bench-data benchmodel bench-scale binary binding block
call candidate cast choose choose-clause clause const const-decl deref dim dispatch enum
error-decl expr-stmt extern field field-init fn for if impl index kernels lit-bool lit-char
lit-float lit-int lit-string match method-call module named-arg param params paren path
pattern pattern-wildcard ref return rewrite scale-binding seed slot spec spec-body step
struct struct-literal trait trait-method try tuple type-args type-const type-name type-ref
type-slice type-tuple unary use var-decl variant while
""".split())

# ---------------------------------------------------------------------------
# 故意忽略的 kind（**每条都要写理由**）
#
# 判据：忽略安全 ⇔ **它不携带任何成本信息**，且**不会包裹可执行的子表达式**。
# 若不满足（比如它下面挂着一个 `call`），忽略就会漏算成本。
# ---------------------------------------------------------------------------
IGNORED = {
    # 纯语法包装，不产生成本
    "type-name": "纯类型名，无运行时成本",
    "type-ref": "纯类型引用",
    "type-args": "泛型实参，编译期擦除",
    "type-const": "泛型常量实参，编译期",
    "type-slice": "类型修饰",
    "type-tuple": "类型修饰",
    "params": "形参列表容器，由 _body 按名绑定",
    "param": "单个形参，由 _body 处理",
    "args": "实参容器，由 _call 逐个求值",
    "named-arg": "具名实参，由 _call 处理",
    "binding": "pattern 绑定名",
    "pattern": "match 臂的模式，由 _match 处理",
    "pattern-wildcard": "`_` 臂，由 _match 处理",
    "arm": "match 的一个臂，由 _match 处理",
    "field-init": "结构体字面量的字段，由 struct-literal 处理",
    "clause": "spec 子句，不在函数体内",
    "choose-clause": "choose 子句，不在函数体内",
    # spec / 声明层（不是可执行函数体，成本分析不走这里）
    "spec": "声明文件节点",
    "spec-body": "声明文件节点",
    "choose": "候选声明，由 _dispatch 读",
    "candidate": "候选声明",
    "kernels": "候选实现块，由 _dispatch 读",
    "module": "模块根",
    "fn": "函数定义",
    "use": "use 声明（当前不加载）",
    "extern": "外部声明",
    "assume": "模块级环境假设（上界声明），不在函数体内",
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

#: **解析器支持，但两个值域都还没实现**（不是"可以忽略"，是"还没做"）
UNIMPLEMENTED = {
    "assign-op": "E25：`x += 1` 能解析；interp 抛「未实现的语句」，cost 给 ⊤（保守但无真实语义）",
    "array": "E25：数组字面量能解析，两个域都未实现",
    "deref": "E25：解引用能解析，两个域都未实现",
    "lit-float": "E25：浮点字面量能解析，两个域都未实现",
}

#: **单侧实现**的锁定清单（变了就报警——这就是"漂移"的信号）
#:
#: `interp` 侧多出来的那些（`lit-*` / `path` / `index` / `field` ...）是**真实的不对称**：
#: 具体值域要逐个求值，符号值域在 `_expr` 的兜底里**返回 0**——
#: 也就是**不计数常量与变量读取**。这是已知缺口 **E24**（实测 `f(){return 1+2;}` κ=2 而实际 4 步）。
EXPECTED_ONLY = {
    "stmt": {"interp": [], "cost": ["ref", "try"]},
    # 甲（E24）之后：cost 侧补上了 lit-*/path/ref/index/field/not，
    # 非对称从 **11 个缩到 3 个**，剩下的都能解释：
    #   struct-literal / tuple / type-name —— 类型/容器构造，不产生操作
    #   lit-float / lit-string —— **E25：两个域都没实现**，cost 侧先按 1 计（保守）
    "expr": {"interp": ["struct-literal", "tuple", "type-name"],
             "cost": ["lit-float"]},
}

# ---------------------------------------------------------------------------
# 从函数源码里抽"它处理了哪些 kind"
# ---------------------------------------------------------------------------
_CMP = re.compile(
    r"if\s+\w+\s*(?:==\s*\"([a-z][a-z0-9-]*)\"|in\s*\(([^)]*)\))")
_LIT = re.compile(r"\"([a-z][a-z0-9-]*)\"")


def _func_body(path: str, name: str) -> str:
    """取 `name` 这个方法的源码（从 `def name(` 到下一个同级 def）。"""
    lines = io.open(path, encoding="utf-8").read().split("\n")
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"\s*def " + re.escape(name) + r"\(", ln):
            start = i
            break
    if start is None:
        return ""
    indent = len(lines[start]) - len(lines[start].lstrip())
    out = [lines[start]]
    for ln in lines[start + 1:]:
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent \
                and re.match(r"\s*(def|class) ", ln):
            break
        out.append(ln)
    return "\n".join(out)


def handled(path: str, name: str) -> frozenset:
    """从源码静态抽取：这个方法按 kind 分派了哪些值。"""
    body = _func_body(os.path.join(SRC, path), name)
    got = set()
    for m in _CMP.finditer(body):
        if m.group(1):
            got.add(m.group(1))
        elif m.group(2):
            got.update(_LIT.findall(m.group(2)))
    return frozenset(got)


#: 四个入口：两个值域 × 语句/表达式
ENTRIES = {
    ("interp", "stmt"): ("interp.py", "_core_stmt"),
    ("interp", "expr"): ("interp.py", "_core_eval"),
    ("cost", "stmt"): ("cost_interp.py", "_core_stmt"),
    ("cost", "expr"): ("cost_interp.py", "_core_expr"),
}


def report() -> dict:
    """返回四个入口各自处理的 kind，以及**只在一侧出现**的 kind（漂移）。"""
    table = {k: handled(*v) for k, v in ENTRIES.items()}
    both, only = {}, {}
    for layer in ("stmt", "expr"):
        a = table[("interp", layer)]
        b = table[("cost", layer)]
        both[layer] = a & b
        only[layer] = {"interp": a - b, "cost": b - a}
    return {"table": table, "both": both, "only": only}


def unaccounted() -> dict:
    """没被任何一侧处理、也没进 `IGNORED` 的 kind —— 这些是真正的未知。"""
    r = report()
    covered = set(IGNORED) | set(UNIMPLEMENTED)
    for layer in ("stmt", "expr"):
        covered |= r["table"][("interp", layer)]
        covered |= r["table"][("cost", layer)]
    return {"stmt": sorted(ALL_KINDS - covered), "expr": sorted(ALL_KINDS - covered)}


if __name__ == "__main__":
    r = report()
    print("== 两值域各自处理的 kind ==")
    for (dom, layer), ks in sorted(r["table"].items()):
        print(f"  {dom:6} {layer:4}  {len(ks):2} 个: {sorted(ks)}")
    print("\n== 只在**一侧**出现（这是漂移）==")
    for layer in ("stmt", "expr"):
        i = sorted(r["only"][layer]["interp"])
        c = sorted(r["only"][layer]["cost"])
        print(f"  {layer}: 只在 interp = {i}")
        print(f"  {layer}: 只在 cost   = {c}")
    u = unaccounted()
    print("\n== 既没处理也没写理由（真未知）==")
    print(f"  {u['stmt']}")
