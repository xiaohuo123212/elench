#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""cost_interp.py —— G27：符号域解释器（只累加成本，不执行值）。

**它做什么**：遍历函数体，按 §7.3 / T11 / T13 的组合规则，算出这个函数的成本式。

**它不做什么**：
  · 不算真实数值（那是具体域的事）
  · 不验证 `target` 是否达成（那是上一层的事）

**单位约定（G26 挂起期间的临时口径）**：
  一次"基本操作"记 1。哪些算基本操作见 OP_COST。

**跨函数（用户定的第 4 条）**：
  ① 有 `target` → 直接用 `target` 的表达式（**不展开**，那是它的承诺）
  ② 没有 `target` → 尝试内联，把被调函数的成本算一遍
  ③ 内联转不起来（递归/成环）→ 报错
"""

import os
import sys
from typing import Dict, List, Optional

from bound import Bound, bound_of_while, expr_to_bound, _descendants
from parser import Node
from symbolic import Cost
from ast_util import descendants as _descendants
from ast_util import const_int_value as _const_int_value

# 基本操作的单位成本。G26 定了就换表，这里只放一个口径。
OP_COST = 1


class CostError(Exception):
    pass


# ---------------------------------------------------------------- 表达式 → 尺寸

SIZE_FNS = ("length", "size", "capacity", "len")






def _substitute_capacity(b, caps: Dict[str, int]):
    """把界里的 `size(定容局部)` 换成容量常数（E22）。"""
    if b is None or not caps:
        return b
    if b.kind == "size" and b.value in caps:
        return Bound("const", value=caps[b.value])
    if b.kind in ("sub", "min", "max"):
        return Bound(b.kind, left=_substitute_capacity(b.left, caps),
                     right=_substitute_capacity(b.right, caps))
    if b.kind == "log2":
        return Bound("log2", left=_substitute_capacity(b.left, caps))
    return b
def as_cost(n: Optional[Node], const_sizes: Dict[str, Cost],
            capacities: Optional[Dict[str, int]] = None) -> Cost:
    """把表达式变成成本意义上的量；认不出的一律记 0（值不参与上界）。

    `const u64 n = scores.length();` 记成 `Cost.size("scores")`——
    **这一步很重要**：后面的 `while (i < n)` 要靠它把上界还原成 `scores.length()×3`，
    而不是认不出 `n` 然后退回成不带系数。
    """
    if n is None:
        return Cost.num(0)
    if n.kind == "dispatch":
        return Cost.num(0)      # 具体成本由 _dispatch 记，这里只是绑定值
    if n.kind == "path" and n.name in const_sizes:
        return const_sizes[n.name]
    if n.kind in ("method-call", "call"):
        fn = (n.name or "").split(".")[-1]
        if fn in SIZE_FNS:
            tgt = None
            for c in n.children:
                if c.kind == "path":
                    tgt = c.name
                    break
            if tgt is None:
                # `xs.at(i)` 之类：args 里第一个是容器
                for c in n.children:
                    if c.kind == "path":
                        tgt = c.name
            # E22：定容器 → 容量常数，不留局部尺寸名
            if tgt is not None and capacities and tgt in capacities:
                return Cost.num(capacities[tgt])
            return Cost.size(tgt or "?")
    return Cost.num(0)


# ---------------------------------------------------------------- 主解释器

class CostInterp:
    """一个模块的成本解释器。

    **`repeats` 声明在 `.tu`，实现写在 `.eln`**——所以构造时要把两边都传进来，
    否则 `.eln` 里的 `while (in_range(...))` 会因为"找不到 in_range 的 repeats"而记 ⊤。
    """

    def __init__(self, mod: Node, decl: Optional[Node] = None):
        self.mod = mod
        self.decl = decl
        self.fns: Dict[str, Node] = {}
        self.specs: Dict[str, Node] = {}
        self.repeats: Dict[str, Optional[Cost]] = {}
        self.inlining: List[str] = []      # 内联栈，用于检出环
        self.cur_fn: Optional[Node] = None  # 当前正在分析的函数（递减循环分析要用）
        # 局部行别名：`const Vec<u64> row = m.at(i);` → {"row": "m"}
        # 用途：让 `row.length()` 记成 `n(m)`，而不是记成查不到的 `n(row)`。
        self.row_of: Dict[str, str] = {}
        # **E22**：定容局部 → 容量常数（`FixedVec<T, N>` 的 N）
        self.capacities: Dict[str, int] = {}
        # **只有 `const` 声明才是不可变的**：把 `u64 i = 0` 的当前值也当常数
        # 会把内层循环的籍算成 0 次（成本消失，方向是低估）。
        self.const_names: set = set()
        for fn in _descendants(mod, "fn"):
            self.fns[fn.name] = fn
        for sp in _descendants(mod, "spec"):
            self.specs[sp.name] = sp
        if decl is not None:
            for sp in _descendants(decl, "spec"):
                self.specs.setdefault(sp.name, sp)
        # 模块级常量（`const BUCKET_COUNT: u64 = 256;`）——成本式里要当常数，不是尺寸变量
        self.const_values: Dict[str, int] = {}
        for scope in (decl, mod):
            if scope is None:
                continue
            for it in _descendants(scope, "const"):
                v = _const_int_value(it)
                if v is not None:
                    self.const_values.setdefault(it.name, v)
        # **批次 S**：模块级 `assume` 声明（环境参数的**上界**）。
        # 形如 `assume n <= xs.length();` → {n: Bound(size, xs)}
        # 用途：让界里的环境变量归约成尺寸表达式（否则 `while (i < n)` 只能给 `n(n)`）。
        # **批次 S**：外部符号（`extern`）只**数次数**，不**估价格**——
        # 因为它的内部成本我们看不见；「调了几次」可静态算、可验证，
        # 「每次多贵」属环境，写进 `.tu` 就是又一处可撒谎位置。
        self.externs: Dict[str, object] = {}
        self.external_calls: Dict[str, int] = {}
        for e in (_descendants(decl, "extern") if decl is not None else []):
            if getattr(e, "name", None):
                self.externs[e.name] = e
        self.assumptions: Dict[str, object] = {}
        for a in _descendants(decl, "assume") if decl is not None else []:
            for c in a.children:
                if getattr(c, "kind", None) != "binary":
                    continue
                if (c.value or "") not in ("<", "<=") or len(c.children) < 2:
                    continue
                lhs, rhs = c.children[0], c.children[1]
                if getattr(lhs, "kind", None) != "path" or not lhs.name:
                    continue
                b = expr_to_bound(rhs)
                if b is not None:
                    self.assumptions.setdefault(lhs.name, b)
        # **A–D**：不再写模块级全局（旧写法会清掉别的实例的常量表）。
        # 旧全局与旧函数暂时保留给 `_baseline` 对拍用，新代码一律走实例级版本。
        self._collect_repeats()

    # ---------- 初始化
    def _collect_repeats(self) -> None:
        for name, sp in self.specs.items():
            body = next((c for c in sp.children if c.kind == "spec-body"), None)
            for c in (body.children if body else []):
                if c.kind == "clause" and (c.name or "") == "repeats":
                    if c.children:
                        self.repeats[name] = expr_to_bound(c.children[0])
                        break

    def target_cost(self, name: str) -> Optional[Cost]:
        """取函数的 `target` 里 `time` 的表达式；没有则 None。"""
        sp = self.specs.get(name)
        if sp is None:
            return None
        body = next((c for c in sp.children if c.kind == "spec-body"), None)
        for c in (body.children if body else []):
            if c.kind == "clause" and (c.name or "") == "target":
                got = self._find_time_slot(c)
                if got is not None:
                    return got
        return None

    def _find_time_slot(self, target: Node) -> Optional[Cost]:
        for d in _descendants(target, "dim"):
            if (d.name or "") != "time":
                continue
            for slot in _descendants(d, "slot"):
                if slot.children:
                    got = self._target_cost(slot.children[0])
                    if got is not None:
                        return got
        return None

    def _size_of_path(self, name: str) -> Cost:
        """把变量名翻成尺寸量。

        `row` 是局部行别名时，尺寸取**外层容器**的名字（`n(m)`）——
        因为 `n(row)` 在求值时查不到，会退化成 1，整个二维成本式失真。
        """
        if name in self.row_of:
            return Cost.size(self.row_of[name])
        return Cost.size(name)

    def _row_alias_of(self, decl) -> Optional[str]:
        """`const Vec<u64> row = m.at(i);` → 返回 `m`；否则 None。"""
        if decl.kind != "const-decl":
            # 只有 const 才绑：`var` 可能被重新赋值，绑了就不健全
            return None
        if len(decl.children) < 2:
            return None
        val = decl.children[-1]
        if val is None or val.kind not in ("method-call", "call"):
            return None
        if (val.name or "").split(".")[-1] not in ("at", "index"):
            return None
        # **只有容器类型才算行别名**。`const Token token = tokens.at(i);`
        # 里 token 是枚举值，不是一行——把它当行别名虽无害（标量没有 .length()），
        # 但会让 row_of 里塞满假条目，日后误导人。
        ty = decl.children[0] if decl.children else None
        if not _is_container_type(ty):
            return None
        recv = val.children[0] if val.children and val.children[0].kind == "path" else None
        return recv.name if recv is not None else None

    def _target_cost(self, n: Optional[Node]) -> Optional[Cost]:
        """把 target 槽位里的表达式直接翻成 `Cost`。

        **为什么不复用 `expr_to_bound`**：`expr_to_bound` 服务的是 `while` 上界，
        它必须保守（`a * b` 当不了计数器）。而 target 里 `O(a.length() * b.length())`
        就是**乘积界**，`Cost.mul` 本来就能表达——两条路径的语义不同，不能共用。
        """
        if n is None:
            return None
        n = _unwrap_obig(n)

        if n.kind == "lit-int":
            try:
                return Cost.num(int(n.value))
            except (TypeError, ValueError):
                return None

        if n.kind == "path":
            if n.name in self.const_values:
                return Cost.num(self.const_values[n.name])
            return self._size_of_path(n.name)

        if n.kind in ("method-call", "call"):
            fn = (n.name or "").split(".")[-1]
            if fn in ("length", "size", "capacity", "len"):
                obj = n.children[0] if n.children and n.children[0].kind == "path" else None
                return self._size_of_path(obj.name) if obj else Cost.size("?")
            if fn in ("log", "log2"):
                for c in n.children:
                    if c.kind == "args":
                        for a in c.children:
                            inner = self._target_cost(a)
                            return Cost.log2(inner) if inner is not None else None
                return None
            return None

        if n.kind == "binary":
            a, b = (n.children + [None, None])[:2]
            la, lb = self._target_cost(a), self._target_cost(b)
            if la is None or lb is None:
                return None
            if n.value == "*":
                return Cost.mul(la, lb)
            if n.value == "+":
                return Cost.add(la, lb)
            return None

        # 其余形状退回旧路径（保守界）
        b = expr_to_bound(n)
        return bound_to_cost_inst(b, self.const_values) if b is not None else None

    # ---------- 入口
    def cost_of(self, fn_name: str) -> Cost:
        self.cur_fn = self.fns.get(fn_name)
        self.row_of = {}
        self.capacities: Dict[str, int] = {}   # E22：定容局部 → 容量常数
        self._reassigned = set()
        fn = self.fns.get(fn_name)
        if fn is None:
            raise CostError(f"找不到函数 {fn_name}")
        return self._body(fn)

    def _body(self, fn: Node) -> Cost:
        blocks = [c for c in fn.children if c.kind == "block"]
        if not blocks:
            return Cost.num(0)
        env: Dict[str, Cost] = {}
        # 形参容器登记尺寸（`xs.length()` 可直接用）
        for pm in _descendants(fn, "param"):
            pass
        return self._block(blocks[0], env)

    # ---------- 块 / 语句
    def _block(self, blk: Node, env: Dict[str, Cost]) -> Cost:
        total = Cost.num(0)
        for st in blk.children:
            total = Cost.add(total, self._stmt(st, env))
        return total

    def _stmt(self, st: Node, env: Dict[str, Cost]) -> Cost:
        k = st.kind

        if k in ("const-decl", "var-decl"):
            # **E22**：`const FixedVec<Token, MAX_TOKENS> tokens = ...;`
            # 这个局部的长度上界是 MAX_TOKENS（常数），不是新的尺寸变量。
            _tnode = st.children[0] if st.children else None
            if st.kind == "const-decl" and st.name:
                self.const_names.add(st.name)
            _cap = type_capacity_inst(_tnode, self.const_values)
            if _cap is not None and st.name:
                self.capacities[st.name] = _cap
            # children[0] 是类型，最后一个是初值
            val_node = st.children[-1] if len(st.children) >= 2 else None
            # **初值里的成本必须记上**（E17）：`const u64 t = f(xs);` 原先只记 1，
            # 被调函数的成本完全消失，导致 κ 不再是上界。
            init_cost = self._expr(val_node, env) if val_node is not None else Cost.num(0)
            # 局部行别名：`const Vec<u64> row = m.at(i);` → row 的尺寸记成 n(m)
            alias = self._row_alias_of(st)
            if alias is not None and alias in env:
                self.row_of[st.name] = alias
            elif alias is not None:
                self.row_of[st.name] = alias
            v = as_cost(val_node, env, self.capacities)
            # `const m = n;` / `u64 hi = n;` —— 别名沿着 env 走到底
            if val_node is not None and val_node.kind == "path" \
                    and val_node.name in env:
                v = env[val_node.name]
            env[st.name] = v
            # `?!L(...)` 的成本取所有候选的最大值，必须**递归找**——
            # 它可能被包在 `(?!L(x))?` 的 try/paren 里，不能只看顶层节点。
            extra = Cost.num(0)
            for d in _descendants(val_node, "dispatch"):
                extra = Cost.add(extra, self._dispatch(d, env))
            # **E29**：容器销毁也要计入（RAII 落地的成本真相）
            drop = drop_cost_of(_tnode, st.name or "")
            return Cost.add(Cost.add(Cost.add(Cost.num(OP_COST), init_cost), extra), drop)

        if k == "assign":
            # 被重新赋值 → 不再等于那一行，撤销别名（宁可不认，不可算错）
            lhs = st.children[0] if st.children else None
            if lhs is not None and lhs.kind == "path":
                self.row_of.pop(lhs.name, None)
            # **右侧的成本必须记上**（E17）：`t = inner(xs);` 的调用成本原先整个丢了
            rhs = st.children[1] if len(st.children) > 1 else None
            rhs_cost = self._expr(rhs, env) if rhs is not None else Cost.num(0)
            # **甲.3**：左值也要算——`dp[(i+1)*width + (j+1)] = ...` 里
            # 下标表达式是实打实的求值，原先**完全没计入**（`_expr(lhs)` 从没被调用）。
            # 实测 `lcs` 因此每轮少算约 6 步，n=16 累计少 1509 步。
            lhs_cost = self._expr(lhs, env) if lhs is not None else Cost.num(0)
            return Cost.add(Cost.add(Cost.num(OP_COST), lhs_cost), rhs_cost)

        if k == "expr-stmt":
            if st.children:
                return self._expr(st.children[0], env)
            return Cost.num(OP_COST)

        if k == "return":
            c = Cost.num(OP_COST)
            if st.children:
                c = Cost.add(c, self._expr(st.children[0], env))
            return c

        if k == "if":
            cond = st.children[0] if st.children else None
            branches = [c for c in st.children[1:] if c.kind == "block"]
            # **甲.2**：条件要用 `_expr` 求整条表达式的成本。
            # 原先这里只算 1 个操作（`cond` 读了没用），而解释器会把条件整个求值一遍。
            cc = Cost.add(Cost.num(OP_COST),
                          self._expr(cond, env) if cond is not None
                          else Cost.num(0))
            costs = [self._block(b, dict(env)) for b in branches]
            if not costs:
                return cc
            worst = costs[0]
            for x in costs[1:]:
                worst = Cost.max(worst, x)
            return Cost.add(cc, worst)

        if k == "while":
            return self._while(st, env)

        if k == "for":
            return self._for(st, env)

        if k == "match":
            return self._match(st, env)

        if k == "block":
            return self._block(st, dict(env))

        if k == "try":
            # `?` 处：两条路径取较大者（用户定的第 5 条）
            inner = Cost.num(0)
            for c in st.children:
                if c.kind == "block":
                    inner = Cost.add(inner, self._block(c, dict(env)))
                elif c.kind not in ("block",):
                    inner = Cost.add(inner, self._expr(c, env))
            # 失败路径也要花掉已走的成本；`?` 本身算一次
            return Cost.add(inner, Cost.num(OP_COST))

        if k == "dispatch":
            # `?!L(...)` 单独成句（`?!L(x);`）
            return self._dispatch(st, env)

        if k == "ref":
            return Cost.num(0)

        # 其它语句保守记 1
        return Cost.num(OP_COST)

    # ---------- 循环
    def _while(self, st: Node, env: Dict[str, Cost]) -> Cost:
        cond = st.children[0] if st.children else None
        body = next((c for c in st.children[1:] if c.kind == "block"), None)

        # 用 G29 的提取器拿圈数。计数器初值从 env 里取（不做数据流分析）。
        # 同时把 env 里已知的尺寸名交给它，避免 `n` 认不出来。
        b, _why = bound_of_condition_wrapper(
            st, self.repeats.get, env, self.row_of, self.capacities,
            self.const_names, self.cur_fn, self.assumptions)
        if b is None:
            return Cost.top()

        n = bound_to_cost_inst(b, self.const_values)
        # **甲.2**：每轮的成本是「迭代开销 1 + 条件**整条表达式** + 体」。
        # 原先只算 1 个操作代表条件，而解释器每轮都把条件求值一遍
        # （`i < 10` 是 3 个节点）——这是残余差距的主要来源。
        cond_cost = self._expr(cond, env) if cond is not None else Cost.num(OP_COST)
        per = Cost.add(Cost.num(OP_COST), cond_cost)
        if body is not None:
            body_cost = self._block(body, dict(env))
            # **R0：守卫事实传播**（E20）。
            # `while (v < E)` 的**体内**必有 `v ≤ E`，所以体内的 `size(v)`
            # 可以换成 `E`。不换，嵌套循环的内层界会留下局部名（`n(i)`），
            # 求值时按 1 算 → 低估 → 上界不成立。
            #
            # 只处理**单条**比较（`and`/`or` 不组合），推不出就不换（不猜）。
            if (cond is not None and cond.kind == "binary"
                    and (cond.value or "") in ("<", "<=")
                    and len(cond.children) >= 2
                    and cond.children[0].kind == "path"
                    and (cond.children[0].name or "")):
                env_sizes = {k: Bound("size", value=v.value)
                             for k, v in env.items() if v.kind == "size"}
                rhs = clean_bound_inst(expr_to_bound(cond.children[1]),
                                       self.cur_fn, env_sizes,
                                       self.row_of, self.capacities,
                                       self.assumptions)
                if rhs is not None:
                    body_cost = body_cost.substitute_sizes(
                        {cond.children[0].name: bound_to_cost_inst(rhs, self.const_values)})
            per = Cost.add(per, body_cost)
        # 每圈取最坏那圈，再乘圈数（用户定的第 3 条）；
        # 末尾那次「条件为假」的检查也要算进去。
        return Cost.add(Cost.mul(n, per), per)

    def _for(self, st: Node, env: Dict[str, Cost]) -> Cost:
        body = next((c for c in st.children if c.kind == "block"), None)
        base = next((c for c in st.children if c.kind == "path"), None)
        name = base.name if base is not None else None
        if name is None:
            for pm in _descendants(st, "path"):
                name = pm.name
                break
        if name is None:
            return Cost.top()
        per = Cost.num(OP_COST * 2)  # 甲.2：+1 绑定
        if body is not None:
            per = Cost.add(per, self._block(body, dict(env)))
        return Cost.mul(Cost.size(name), per)

    def _match(self, st: Node, env: Dict[str, Cost]) -> Cost:
        arms = [c for c in st.children if c.kind == "arm"]
        worst: Optional[Cost] = None
        for a in arms:
            blocks = [c for c in a.children if c.kind == "block"]
            c = Cost.num(OP_COST)
            for b in blocks:
                c = Cost.add(c, self._block(b, dict(env)))
            worst = c if worst is None else Cost.max(worst, c)
        return worst if worst is not None else Cost.num(OP_COST)

    # ---------- 调用
    def _expr(self, n: Node, env: Dict[str, Cost]) -> Cost:
        k = n.kind
        if k == "call":
            return self._call(n.name, n, env)
        if k == "dispatch":
            return self._dispatch(n, env)
        if k in ("binary", "unary", "cast"):
            # **必须递归进子表达式**（E17）：`t = t + inner(xs)` 里 inner 的成本
            # 原先是整块丢掉的——这会让 κ 小于真实成本，**上界就不成立**。
            total = Cost.num(OP_COST)
            for c in n.children:
                total = Cost.add(total, self._expr(c, env))
            return total
        if k in ("method-call",):
            # 同理：`xs.at(inner(xs))` 的实参成本、`m.at(i)` 的接收者成本都要算。
            # **甲.2**：再加 1 个操作代表**方法本身的派发/执行**——
            # 实测 `xs.at(i)` 在解释器里比「节点 + 接收者 + 实参」多 1 步。
            total = Cost.num(OP_COST * 2)
            for c in n.children:
                total = Cost.add(total, self._expr(c, env))
            return total
        if k in ("paren",):
            if n.children:
                return self._expr(n.children[0], env)
            return Cost.num(0)
        # **甲（E24）**：叶子也要计数。
        # `operations` 的单位是「语句 + 表达式节点（含叶子）+ 循环轮数」（见 `test_units.py`）；
        # 这里原先走兜底 `Cost.num(0)`，叶子免费 ⇒ κ 比实际步数**小**
        # （实测 `paren` 差 2.00 倍、`lcs` 差 4.96 倍）。
        if k in ("lit-int", "lit-char", "lit-bool", "lit-float", "lit-string",
                 "path", "ref"):
            return Cost.num(OP_COST)
        if k in ("index", "field", "not"):
            total = Cost.num(OP_COST)
            for c in n.children:
                total = Cost.add(total, self._expr(c, env))
            return total
        if k == "try":
            # **`(...)?` 的成本不能消失**：`return (inner(xs))?;` 原先走到这里
            # 直接 return 0，被调函数的成本整个丢了（与 E17 同一类，方向是低估）。
            # `dispatch` 子节点跳过——它在声明分支里已经单独记过，重复计费会把上界撑大。
            total = Cost.num(OP_COST)
            for c in n.children:
                if c.kind == "dispatch":
                    continue
                total = Cost.add(total, self._expr(c, env))
            return total
        return Cost.num(0)

    def _param_size_table(self, fn: Node, call: Node) -> Dict[str, Cost]:
        """E19：被调函数 κ 里的 `size(形参名)` → **调用者实参的尺寸**。

        不这么做，κ 里会留下被调函数的形参名（`n(a)`），
        在调用者作用域里查不到，求值时按 1 算 → **低估 → 上界不成立**。
        """
        pnode = next((c for c in fn.children if c.kind == "params"), None)
        anode = next((c for c in call.children if c.kind == "args"), None)
        if pnode is None or anode is None:
            return {}
        table: Dict[str, Cost] = {}
        for p, a in zip(pnode.children, anode.children):
            ty = p.children[0] if p.children else None
            if ty is None:
                continue
            is_container = (ty.kind == "type-ref"
                            or (ty.name or "") in ("Vec", "FixedVec"))
            if not is_container or not p.name:
                continue
            tgt = a
            while tgt is not None and tgt.kind in ("ref", "paren"):
                tgt = tgt.children[0] if tgt.children else None
            if tgt is None or tgt.kind != "path" or not tgt.name:
                # 实参是表达式（`xs.at(i)` / 另一个调用）→ 给不出尺寸，**不猜**
                continue
            nm = tgt.name
            if nm in self.capacities:            # 定容局部（E22）
                table[p.name] = Cost.num(self.capacities[nm])
            elif nm in self.row_of:              # 行别名归一到外层容器
                table[p.name] = Cost.size(self.row_of[nm])
            else:
                table[p.name] = Cost.size(nm)
        return table

    def _call(self, name: Optional[str], node: Node, env: Dict[str, Cost]) -> Cost:
        # `min(a,b)` / `max(a,b)` —— **常数成本，不改变阶**。
        # 这是它们能进内建集的唯一理由：作者拿它替掉 `if` 串两支，成本式不变。
        if (name or "") in ("min", "max"):
            return Cost.num(1)

        # `sort(xs)` → O(n log n)（**语言给定，作者不用声明也不用证明**）
        # `sort_by(xs, cmp)` → O(n log n × κ(cmp))：因为 cmp 是**具名函数**，
        # 它的成本可静态分析，所以乘积仍可写出来。
        if (name or "") in ("sort", "sort_by"):
            args_node = next((c for c in node.children if c.kind == "args"), None)
            items = args_node.children if args_node is not None else []
            xs = items[0].name if items and items[0].kind == "path" else "?"
            n = Cost.size(xs)
            core = Cost.mul(n, Cost.log2(n))
            if name == "sort_by" and len(items) > 1 and items[1].kind == "path":
                cmp_name = items[1].name or ""
                kc = self.target_cost(cmp_name)
                if kc is None and cmp_name in self.fns:
                    kc = self.cost_of(cmp_name)
                core = Cost.mul(core, kc if kc is not None else Cost.top())
            return core
        if not name:
            return Cost.num(OP_COST)
        short = name.split(".")[-1]

        # **批次 S**：已声明的外部符号 → **只数次数**，返回「调用本身」的成本。
        if short in getattr(self, "externs", {}):
            self.external_calls[short] = self.external_calls.get(short, 0) + 1
            total = Cost.num(OP_COST)
            args_node = next((c for c in node.children if c.kind == "args"), None)
            for a in (args_node.children if args_node is not None else []):
                total = Cost.add(total, self._expr(a, env))
            return total

        # ① 有 target → 用 target（用户的第 4 条）
        t = self.target_cost(short)
        if t is not None:
            # target 也是用**被调函数的形参名**写的，同样要按实参实例化。
            # 注意：此处 `fn` 还没赋值（内联分支才赋值），要自己取一次。
            _fn = self.fns.get(short)
            _tab = self._param_size_table(_fn, node) if _fn is not None else {}
            return Cost.add(Cost.num(OP_COST), t.substitute_sizes(_tab))

        # ② 没有 target → 内联
        fn = self.fns.get(short)
        if fn is None:
            # 内置/外部函数：记 1，不猜
            return Cost.num(OP_COST)

        if short in self.inlining:
            raise CostError(
                f"内联 {short} 时成环（{ ' → '.join(self.inlining + [short]) }）；"
                f"给它补一个 target，或检查是否递归")
        self.inlining.append(short)
        try:
            return Cost.add(Cost.num(OP_COST),
                            self._body(fn).substitute_sizes(
                                self._param_size_table(fn, node)))
        finally:
            self.inlining.pop()

    def _dispatch(self, node: Node, env: Dict[str, Cost]) -> Cost:
        """`?!L(...)`：候选的成本上界 = 所有候选里最大的那个。"""
        label = node.name or ""
        best: Optional[Cost] = None
        for kern in _descendants(self.mod, "kernels"):
            if (kern.name or "") != label:
                continue
            for cand in kern.children:
                if cand.kind != "candidate":
                    continue
                blocks = [c for c in cand.children if c.kind == "block"]
                c = Cost.num(0)
                for b in blocks:
                    c = Cost.add(c, self._block(b, dict(env)))
                best = c if best is None else Cost.max(best, c)
        if best is None:
            # 没有实现的候选：按 target
            sp = self.specs.get((node.name or "").split(".")[-1])
            if sp is not None:
                t = self.target_cost((node.name or "").split(".")[-1])
                if t is not None:
                    return t
            return Cost.top()
        return Cost.add(Cost.num(OP_COST), best)


# ---------------------------------------------------------------- 界 → Cost

def _is_container_type(ty: Optional[Node]) -> bool:
    """声明类型是不是容器（`Vec<...>` / `FixedVec<...>`，可嵌套）。"""
    if ty is None:
        return False
    if ty.kind == "type-ref" and ty.children:
        ty = ty.children[0]
    if ty.kind != "type-name":
        return False
    return (ty.name or "") in ("Vec", "FixedVec")


def _substitute_size(b, row_of: Dict[str, str]):
    """把界表达式里的 `size(局部行名)` 换成 `size(外层容器)`。"""
    if b is None or not row_of:
        return b
    if b.kind == "size" and b.value in row_of:
        return Bound("size", value=row_of[b.value])
    if b.kind in ("sub", "min", "max"):
        return Bound(b.kind, left=_substitute_size(b.left, row_of),
                     right=_substitute_size(b.right, row_of))
    if b.kind == "log2":
        return Bound("log2", left=_substitute_size(b.left, row_of))
    return b


def bound_of_condition_wrapper(st: Node, repeats_of, env, row_of=None,
                              capacities=None, const_names=None, cur_fn=None,
                              assumptions=None):
    """把 env 里的两件事告诉 G29 的提取器：

      · 初值为 0 的变量（决定上界写成 `e` 还是 `e - i`）
      · 尺寸别名（`const n = scores.length();` → 上界里出现 n 时换成 scores.length()）
    """
    from bound import bound_of_condition, monotone_decreasing, substitute, progresses
    # **E21**：循环体不推进计数器 ⇒ 这是死循环，**不许给上界**。
    # 不给上界会变成 ⊤，而 ⊤ 会让"达成"判定变成"无法判定"——这正是我们要的诚实。
    ok, prog_why = progresses(st)
    if not ok:
        return None, f"{prog_why}（循环不会推进 ⇒ 不给上界，E21）"
    zv = {v for v, c in env.items() if c.is_const() and c.value == 0}
    cond = st.children[0] if st.children else None
    # 递减循环（甲）：`while (j > 0)` + 体内 `j = j - 1` → 上界 = j 的初值。
    # 上界取初值只是**上界**（j 只减不增），松一点不违健全性。
    dec = monotone_decreasing(cur_fn) if cur_fn is not None else {}
    zv_all = zv | set(dec)
    b, why = bound_of_condition(cond, repeats_of, zv_all, set(dec), dec)
    if b is not None:
        table = {k: Bound("size", value=v.value)
                 for k, v in env.items() if v.kind == "size"}
        # **E22**：环境里已知是**常数**的局部（`const u64 n = tmp.length();` →
        # tmp 定容 ⇒ n 是常数），也要进替换表；否则守卫里的 `i < n`
        # 会退化成 `n(n)` 这种局部尺寸名。
        table.update({k: Bound("const", value=v.value)
                      for k, v in env.items()
                      if v.is_const() and k in (const_names or ())})
        b = substitute(b, table)
        # 局部行别名：`row` 的尺寸归一到外层容器（`n(m)`）。
        # **单独一遍**，因为 `substitute` 只换 `var` 节点，而 `row.length()`
        # 已经变成 `size(row)` 了。直接改 substitute 会波及所有尺寸替换，风险更大。
        b = _substitute_size(b, row_of or {})
        b = _substitute_capacity(b, capacities or {})
    if assumptions:
        b = substitute(b, assumptions)
    return b, why
def _unwrap_obig(n: Node) -> Node:
    """`O(x)` 去掉 O，取里面的表达式。"""
    while n is not None and n.kind in ("call", "method-call") and \
            (n.name or "").split(".")[-1] in ("O", "BigO"):
        inner = None
        for c in n.children:
            if c.kind == "args" and c.children:
                inner = c.children[0]
                break
        if inner is None:
            return n
        n = inner
    return n
def bound_to_cost_inst(b, consts=None) -> Cost:
    """`_bound_to_cost` 的实例级版本：常量表由参数传入，不读模块全局。"""
    consts = consts or {}

    def go(x) -> Cost:
        k = x.kind
        if k == "const":
            try:
                return Cost.num(int(x.value))
            except (TypeError, ValueError):
                return Cost.num(1)
        if k == "size":
            return Cost.size(x.value)
        if k == "var":
            if x.value in consts:
                return Cost.num(consts[x.value])
            return Cost.size(x.value)
        if k == "sub":
            return go(x.left)                     # a - b <= a
        if k == "add":
            return Cost.add(go(x.left), go(x.right))
        if k == "mul":
            return Cost.mul(go(x.left), go(x.right))
        if k == "min":
            return _min_cost(go(x.left), go(x.right))
        if k == "max":
            return Cost.max(go(x.left), go(x.right))
        if k == "log2":
            return Cost.log2(go(x.left))
        return Cost.top()

    return go(b)


def type_capacity_inst(tn, consts=None):
    """`_type_capacity` 的实例级版本（常量表由参数传入）。"""
    consts = consts or {}
    if tn is None or (tn.name or "") != "FixedVec":
        return None
    for c in tn.children:
        if c.kind != "type-args":
            continue
        for a in c.children[1:]:
            raw = a.value if a.value is not None else a.name
            if raw is None:
                continue
            try:
                return int(raw)
            except (TypeError, ValueError):
                got = consts.get(str(raw))
                if got is not None:
                    return int(got)
    return None



def drop_cost_of(ty_node, name: str) -> Cost:
    """**E29**：析构成本。

    RAII 落地后（决定清单 §10.47），每个容器出作用域都要析构：
    - `FixedVec<T, N>` → **栈上，0**
    - `Vec<POD>` → **一次 free，记 1**
    - `Vec<Vec<..>>` → **n 次元素析构 + 一次 free**，记 `1 + n(名字)`

    记在**声明处**而不是作用域出口：一个变量只会被析构一次，
    所以总和与"在出口记"相同，但实现上不需要跟踪作用域。
    """
    if ty_node is None:
        return Cost.num(0)
    names = [getattr(d, "name", None) for d in _descendants(ty_node, "type-name")]
    names += [getattr(d, "name", None) for d in _descendants(ty_node, "type-ref")]
    names = [n for n in names if n]
    if names and names[0] == "FixedVec":
        return Cost.num(0)
    vecs = sum(1 for n in names if n == "Vec")
    if vecs == 0:
        return Cost.num(0)
    if vecs >= 2:
        return Cost.add(Cost.num(1), Cost.size(name))
    return Cost.num(1)

def clean_bound_inst(b, cur_fn=None, env_sizes=None, row_of=None,
                     capacities=None, assumptions=None):
    """`_clean_bound` 的实例级版本：当前函数由参数传入，不读 `_CUR_FN`。"""
    from bound import substitute, const_bindings
    if b is None:
        return None
    if cur_fn is not None:
        b = substitute(b, const_bindings(cur_fn))
    b = _substitute_size(b, row_of or {})
    b = _substitute_capacity(b, capacities or {})
    if assumptions:
        b = substitute(b, assumptions)
    if env_sizes:
        b = substitute(b, env_sizes)
    return b


def _min_cost(a: Cost, b: Cost) -> Cost:
    """`min` 在成本上界里取较**松**的那侧（因为它是上界的上界）。"""
    return Cost.max(a, b)


# ---------------------------------------------------------------- 报告

def report(mod: Node, decl: Optional[Node] = None,
           names: Optional[List[str]] = None) -> List[dict]:
    ci = CostInterp(mod, decl)
    out = []
    for name in (names or [n for n in ci.fns]):
        fn = ci.fns.get(name)
        if fn is None:
            continue
        try:
            c = ci.cost_of(name)
        except CostError as e:
            out.append({"fn": name, "cost": "⊤", "error": str(e)})
            continue
        t = ci.target_cost(name)
        out.append({
            "fn": name,
            "cost": c.render(),
            "target": t.render() if t is not None else None,
        })
    return out


if __name__ == "__main__":
    from parser import parse_source

    if len(sys.argv) < 2:
        print("用法：python cost_interp.py <模块.tu> <模块.eln>", file=sys.stderr)
        raise SystemExit(2)
    srcs = [open(p, encoding="utf-8").read() for p in sys.argv[1:]]
    decl = impl = None
    for p, s in zip(sys.argv[1:], srcs):
        m = parse_source(s, os.path.basename(p)).module
        if p.endswith(".tu"):
            decl = m
        else:
            impl = m
    rows = report(impl or decl, decl)
    w = max((len(r["fn"]) for r in rows), default=8)
    cw = max((len(r["cost"]) for r in rows), default=8)
    for r in rows:
        tgt = r["target"] if r.get("target") else "—"
        err = f"   ⚠ {r['error']}" if r.get("error") else ""
        print(f"  {r['fn'].ljust(w)}  ≤ {r['cost'].ljust(cw)}   目标 {tgt}{err}")