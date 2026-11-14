#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""cfg.py —— 控制流图 + 活跃性分析（分支敏感借用分析的基础）

**为什么需要它**

`borrow.py` 早先的近似把函数体压成"一条时间线"：按源码顺序取引用的最后一次使用。
它不懂分支，所以两类判定都不准：

| 类型 | 例子 | 后果 |
|---|---|---|
| **误报** | `if (c) { const u64& r = xs.at(0); xs.push(1); }` | 被迫改写法 |
| **漏报** | `const u64& r = xs.at(0); if (c) { xs.push(1); } return r;` | **交付 UB** |

**漏报更严重**，而时间线天然会漏——因为它不知道"这支改、那支不改"。

**这里实现的东西**

1. **CFG**：节点=语句，边=顺序 / `if` 两支 / `while` 与 `for` 的回边 / `match` 各 arm /
   `return` 与 `break`/`continue` 的跳转；
2. **def / use / mut**：每个节点写什么、读什么、改谁；
3. **活跃性不动点**：`live_out[n] = ∪ live_in[succ]`，`live_in[n] = use[n] ∪ (live_out[n] − def[n])`。

**这就是 Rust MIR borrowck 的路子（基于 liveness 的 NLL），只是我们做在 AST 上。**
"""
from typing import Dict, List, Optional, Set

from ast_util import descendants


class CfgNode:
    """CFG 节点。`st` 是它对应的 AST 语句（合成节点为 None）。"""

    __slots__ = ("id", "st", "kind", "succ", "uses", "defs", "muts")

    def __init__(self, nid: int, st=None, kind: str = "stmt"):
        self.id = nid
        self.st = st
        self.kind = kind
        self.succ: List[int] = []
        self.uses: Set[str] = set()
        self.defs: Set[str] = set()
        self.muts: Set[str] = set()

    def __repr__(self) -> str:
        return f"<{self.id} {self.kind} {getattr(self.st, 'kind', '')}>"


#: 会**改容器内容**的方法。漏一个，借用检查就在那个方法上失效
#: （2026-11-06 补：`fill` / `extend` / `enqueue` / `dequeue` 都曾漏掉）。
MUT_METHODS = {"push", "push_back", "pop", "clear", "set", "sort", "sort_by",
               "reverse", "resize", "insert", "remove", "erase",
               "fill", "extend", "enqueue", "dequeue"}

#: 会改**第一个实参**的内建函数（不是方法，所以方法扫描抓不到）。
MUT_FUNCS = {"swap"}


def _paths(node) -> Set[str]:
    return {p.name for p in descendants(node, "path") if p.name}


def _base_of(node) -> Optional[str]:
    for p in descendants(node, "path"):
        if p.name:
            return p.name
    return None


class Cfg:
    def __init__(self):
        self.nodes: Dict[int, CfgNode] = {}
        self.entry: Optional[int] = None

    def new(self, st=None, kind: str = "stmt") -> CfgNode:
        n = CfgNode(len(self.nodes), st, kind)
        self.nodes[n.id] = n
        return n

    def link(self, a: int, b: Optional[int]) -> None:
        if b is None:
            return
        if b not in self.nodes[a].succ:
            self.nodes[a].succ.append(b)

    # ---------------- 构建 ----------------

    def build_block(self, block, succ: Optional[int], loop_ctx=None) -> Optional[int]:
        """从后往前建，返回入口节点 id。`loop_ctx` = (cond_id, exit_node)。"""
        cur = succ
        stmts = list(block.children)
        for st in reversed(stmts):
            cur = self.build_stmt(st, cur, loop_ctx)
        return cur

    def build_stmt(self, st, succ: Optional[int], loop_ctx=None) -> Optional[int]:
        k = getattr(st, "kind", None)

        if k == "block":
            return self.build_block(st, succ, loop_ctx)

        if k == "if":
            cond = self.new(st, "cond")
            cond.uses |= _paths(st.children[0]) if st.children else set()
            branches = [c for c in st.children[1:] if getattr(c, "kind", None) == "block"]
            for b in branches:
                entry = self.build_block(b, succ, loop_ctx)
                self.link(cond.id, entry if entry is not None else succ)
            if not branches:
                self.link(cond.id, succ)
            return cond.id

        if k in ("while", "for"):
            cond = self.new(st, "cond")
            cond.uses |= _paths(st.children[0]) if (k == "while" and st.children) else set()
            body = next((c for c in st.children if getattr(c, "kind", None) == "block"), None)
            exit_node = None
            ctx = (cond.id, exit_node)
            entry = self.build_block(body, cond.id, ctx) if body is not None else None
            self.link(cond.id, entry if entry is not None else succ)
            self.link(cond.id, succ)           # 条件为假 → 退出
            return cond.id

        if k == "return":
            n = self.new(st, "return")
            n.uses |= _paths(st)
            return n.id                        # 无后继

        if k in ("break", "continue"):
            n = self.new(st, "jump")
            if loop_ctx is not None:
                target = loop_ctx[0] if k == "continue" else loop_ctx[1]
                self.link(n.id, target)
            return n.id

        if k == "match":
            n = self.new(st, "match")
            n.uses |= _paths(st.children[0]) if st.children else set()
            arms = [c for c in st.children if getattr(c, "kind", None) == "arm"]
            for a in arms:
                for b in a.children:
                    if getattr(b, "kind", None) == "block":
                        entry = self.build_block(b, succ, loop_ctx)
                        self.link(n.id, entry if entry is not None else succ)
            if not arms:
                self.link(n.id, succ)
            return n.id

        # 普通语句
        n = self.new(st, "stmt")
        if k in ("const-decl", "var-decl") and st.children:
            n.uses |= _paths(st.children[-1])
            if st.name:
                n.defs.add(st.name)
        elif k in ("assign", "assign-op") and st.children:
            lhs = st.children[0]
            n.uses |= _paths(st)
            base = _base_of(lhs)
            if base:
                n.defs.add(base)
                n.muts.add(base)
        else:
            n.uses |= _paths(st)
            for mc in descendants(st, "method-call"):
                if (mc.name or "") not in MUT_METHODS or not mc.children:
                    continue
                # **变异归到根变量**（2026-11-06 修）：以前只认 `children[0]` 是 path，
                # 于是 `s.items.push(9)` 记不上——接收者是 `field` 节点，条件不成立，
                # **整个字段上的写入都被漏掉**。用 `_base_of` 取根（`s.items` → `s`），
                # 方向保守：改字段就算改结构体。
                base = _base_of(mc.children[0])
                if base:
                    n.muts.add(base)
                    n.uses.add(base)
            # **内建函数也会改第一个实参**（`swap(xs, i, j)`）——方法扫描抓不到它，
            # 于是"借用还活着就换元素"以前是漏报的。
            for call in descendants(st, "call"):
                if (call.name or "") not in MUT_FUNCS:
                    continue
                args = next((c for c in (call.children or []) if c.kind == "args"), None)
                if args is None or not args.children:
                    continue
                base = _base_of(args.children[0])
                if base:
                    n.muts.add(base)
                    n.uses.add(base)
        self.link(n.id, succ)
        return n.id


def build(block) -> Cfg:
    c = Cfg()
    entry = c.build_block(block, None)
    c.entry = entry
    return c


def liveness(cfg: Cfg) -> Dict[int, Set[str]]:
    """返回 live_in：节点 id → 该点"之后还会用到"的变量集合（不动点）。"""
    live_in: Dict[int, Set[str]] = {i: set() for i in cfg.nodes}
    live_out: Dict[int, Set[str]] = {i: set() for i in cfg.nodes}
    changed = True
    rounds = 0
    while changed and rounds < 200:          # 上界防死循环：图是有限的
        changed = False
        rounds += 1
        for i, n in cfg.nodes.items():
            out = set()
            for s in n.succ:
                out |= live_in.get(s, set())
            if out != live_out[i]:
                live_out[i] = out
                changed = True
            new_in = n.uses | (out - n.defs)
            if new_in != live_in[i]:
                live_in[i] = new_in
                changed = True
    return live_in
