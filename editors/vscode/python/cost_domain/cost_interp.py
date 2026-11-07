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
import stdlib
import sys
from typing import Dict, List, Optional

from .bound import Bound, bound_of_while, expr_to_bound, _descendants
from parser import Node
from typenames import canon
from .symbolic import Cost
from ast_util import descendants as _descendants
from ast_util import arms_of, match_scrutinee, arm_pattern, arm_block, arm_single_stmts
from ast_util import stmt_body, stmt_else, while_cond, for_iterable
from ast_util import const_int_value as _const_int_value
from kinds import COST_EXPR, COST_STMT

# 基本操作的单位成本。G26 定了就换表，这里只放一个口径。
OP_COST = 1


class CostError(Exception):
    pass


# ---------------------------------------------------------------- 表达式 → 尺寸

SIZE_FNS = ("length", "size", "capacity", "len")


def _int_literal(n: Optional[Node]) -> Optional[int]:
    """取一个整数字面量节点的值；不是字面量就返回 `None`（**不猜**）。"""
    if n is None or getattr(n, "kind", "") != "lit-int":
        return None
    try:
        return int(n.value)
    except (TypeError, ValueError):
        return None






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

def _rename_sizes(c, mapping: dict):
    """把 Cost 里的 `size(局部名)` 换成 `size(容器名)`；其余结构原样。"""
    if c is None:
        return c
    if getattr(c, "kind", "") == "size" and c.value in mapping:
        return type(c).size(mapping[c.value])
    args = getattr(c, "args", ())
    if args:
        return type(c)(c.kind, c.value, [_rename_sizes(a, mapping) for a in args])
    return c


class CostInterp:
    """一个模块的成本解释器。

    **`repeats` 声明在 `.tu`，实现写在 `.eln`**——所以构造时要把两边都传进来，
    否则 `.eln` 里的 `while (in_range(...))` 会因为"找不到 in_range 的 repeats"而记 ⊤。
    """

    def __init__(self, mod: Node, decl: Optional[Node] = None, table_path=None,
                 diags=None, explain: bool = False):
        # **解析失败就不许给数**（2026-11-01）：
        # 以前 `.eln` 里有语法错误时，坏掉的那段被丢掉，κ 照样算出一个数
        # （`match (n) { 0 => … }` 直接算出 **0**——一个"什么都不承诺"的上界）。
        # 有了诊断就返回 ⊤：不知道就是不知道。
        self.diags = list(diags or [])
        # **归因侧表（只在 --explain 下维护）**：`id(Cost) → Origin`。
        # 不给 `Cost` 加字段——它是 `__slots__` 不可变结构，改它会污染所有构造点。
        # `_keep` 持有强引用：否则中间节点被回收后 `id()` 会被复用，归因指错。
        self.explain = bool(explain)
        self.origins: Dict[int, Dict[str, Any]] = {}
        self.dispatch_cands: Dict[str, Dict[str, "Cost"]] = {}
        self._keep: list = []
        # **分档表不参与承诺**（2026-11-01 定）。
        #
        # 曾经这里是"有表就按逐段上界"：把每段候选在**段末端**折成一个常数再取 max。
        # 后果有三条，一次实测全部暴露（`gap`，表只覆盖 8..4096）：
        #   ① 蓝线变成一条**水平线**（常数压住了另一候选的线性项）；
        #   ② 真实的**拐点被抹掉**（甲 `104n+110` 与乙 `23n+220,042` 在 n≈2,715 交叉，
        #      本来在图里，折成常数后看不见了）；
        #   ③ **表范围外破界**：n=8192 承诺 426,001，实测 729,109。
        # 根因是"表在替公式做决定"。
        # **2026-11-02 定口径：承诺 = 分段上界（piecewise），分段取自运行期同一份表。**
        #   允许分段的**唯一前提**是 S2 同源：run / stats / bench / export 都读同一个
        #   `.dispatch.yaml`。不同源就会重演上面那三次破界（-341 / 402 vs 733 / 426,001 vs 729,109）。
        #   表外（超出声明区间）退回第一个候选 —— 与运行期"表覆盖不到就退化"对齐。
        #   无表 / 单候选 → 退回 max（sound，只是松）。
        #
        # 参数 `table_path` 保留：**分档表现在只用于"分段"这一件事**，
        # 而且必须与运行期读的是**同一份文件**（S2 同源）。
        # 没有表 → 不用分段、退回 max（运行期在无表时会"退化为第一个实现"，
        # 那个行为我们不敢假设，所以宁可松）。
        from interp import load_dispatch_table
        self.table_path = table_path
        self.table = load_dispatch_table(table_path) if table_path else {}
        self.mod = mod
        self.decl = decl
        self.fns: Dict[str, Node] = {}
        self.specs: Dict[str, Node] = {}
        self.repeats: Dict[str, Optional[Cost]] = {}
        # **外部项登记**：`external { read_file: O(1); }`
        self.externals: Dict[str, Cost] = {}
        self._alloc_visiting: List[str] = []
        # **FFI 名字**：`.eln` 里声明的 extern（成本走 ext/登记值）
        # **spec 里声明外部实现的名字**（成本同样走 ext/登记值）
        self.extern_spec_names = set()
        for _it in ((decl.children if decl is not None else []) or []):
            if getattr(_it, "kind", "") != "spec":
                continue
            _body = next((c for c in _it.children if c.kind == "spec-body"), None)
            for _c in (_body.children if _body else []):
                if _c.kind == "clause" and (_c.name or "") == "external":
                    self.extern_spec_names.add(_it.name)
        self.extern_names = {c.name for c in (getattr(mod, 'children', None) or [])
                             if getattr(c, 'kind', '') == 'extern'}
        self.inlining: List[str] = []      # 内联栈，用于检出环
        self.frames: List[str] = []        # **当前函数体的遍历栈**（递归检出用）
        # **递归深度声明**（`.tu` 的 `recursion depth <= N;`，C11-b）。
        # 找不到声明时 `depth_of()` 退回 `repeats`（仅当它是常数）。
        self.depths: Dict[str, int] = {}
        # `_rec_once` 的缓存：函数名 → **函数体只算一次**的 κ（递归调用点已切成 `1+Σ实参`）
        self._rec_once_cache: Dict[str, Cost] = {}
        # 非 None 时表示正在算「函数体只算一次」：**只切这个名字**的自递归，
        # 其它环上函数一律报错（间接递归不猜，见 `_call` 的注释）。
        self._rec_cut_root: Optional[str] = None
        # **函数值形参的绑定栈**（④）：帧 → {形参名: 具名函数名 或 None(仍为符号)}
        self.fnval_stack: List[Dict[str, Optional[str]]] = []
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
        self.vty: Dict[str, str] = {}
        for scope in (decl, mod):
            if scope is None:
                continue
            for it in _descendants(scope, "const"):
                v = _const_int_value(it)
                if v is not None:
                    self.const_values.setdefault(it.name, v)
        # **批次 S**：外部符号（`extern`）只**数次数**，不**估价格**——
        # 因为它的内部成本我们看不见；「调了几次」可静态算、可验证，
        # 「每次多贵」属环境，写进 `.tu` 就是又一处可撒谎位置。
        self.externs: Dict[str, object] = {}
        self.external_calls: Dict[str, int] = {}
        for e in (_descendants(decl, "extern") if decl is not None else []):
            if getattr(e, "name", None):
                self.externs[e.name] = e
        # **`assume` 已移除（2026-11-04）**：这个字典恒为空。
        # 它原来装「环境参数的上界」，但那是个**不可验证**的断言，
        # 而 κ 的卖点正是可验证——见 `C-决定与索引.md` N-13。
        # 形参仍往下传给 `bound`，但永远是空 dict（`if assumptions:` 恒假）。
        self.assumptions: Dict[str, object] = {}
        # **A–D**：不再写模块级全局（旧写法会清掉别的实例的常量表）。
        # 旧全局与旧函数暂时保留给 `_baseline` 对拍用，新代码一律走实例级版本。
        self._collect_repeats()
        self._collect_depths()
        self._collect_externals()

    # ---------- 初始化
    def _collect_externals(self) -> None:
        """读 `.tu` 里的 `external { name: O(1); }`，换成可用的成本常数。"""
        mod = self.decl
        if mod is None:
            return
        n_of = Cost.size("n")
        for item in mod.children:
            if getattr(item, "kind", "") != "external":
                continue
            for e in item.children:
                val = getattr(e, "value", None)
                if isinstance(val, tuple) and len(val) == 2:
                    kind, arg = val
                    self.externals[e.name or ""] = (
                        Cost.num(arg) if kind == "const" else Cost("sym", arg))

    def _collect_depths(self) -> None:
        """读 `.tu` 里的 `recursion depth <= N;`（规格 C11-b）。

        **这是「递归深度」的唯一声明来源**。格式（规格 §4.2）是

            recursion depth <= 32;

        解析器把它当普通子句：`clause(name="recursion")`，里面是一个
        `binary(<=, path(depth), lit-int(N))`。**没有这个声明就不猜深度**——
        保持修前的保守处理（有 target 按一层记；无 target 内联成环报错）。

        `repeats` 是**备用**：`repeats 16;` 这种常数声明在 `depth_of()` 里
        当深度用（递归也是一种「重复」，作者已经声明了次数）。符号形式的
        `repeats`（`repeats xs.length();`）**不用**：那是循环圈数，不是深度。
        """
        for name, sp in self.specs.items():
            body = next((c for c in sp.children if c.kind == "spec-body"), None)
            for c in (body.children if body else []):
                if c.kind != "clause" or (c.name or "") != "recursion":
                    continue
                for ch in c.children:
                    if getattr(ch, "kind", "") != "binary":
                        continue
                    if (ch.value or "") not in ("<=", "<"):
                        continue
                    if len(ch.children) < 2:
                        continue
                    lhs, rhs = ch.children[0], ch.children[1]
                    if getattr(lhs, "kind", "") != "path" or (lhs.name or "") != "depth":
                        continue
                    n = _int_literal(rhs)
                    if n is not None and n >= 1:
                        self.depths[name] = n
                    break
                break

    def depth_of(self, name: str) -> Optional[int]:
        """递归深度的**声明值**：`recursion depth <= N` 优先，其次常数 `repeats`。"""
        got = self.depths.get(name)
        if got is not None:
            return got
        b = self.repeats.get(name)
        if b is not None and getattr(b, "kind", "") == "const":
            try:
                n = int(b.value)
            except (TypeError, ValueError):
                return None
            return n if n >= 1 else None
        return None

    def _rec_once(self, name: str) -> Cost:
        """**κ(函数体只算一次)**：递归调用点切成 `1 + Σ实参`，不再往下展开。

        为什么需要它：`down(xs, 8)` 的真实成本是「一层体 × 9」，而修前 κ 只记
        一层。要在递归调用点写 `1 + Σargs + D × 函数体`，必须先把「函数体」
        算出来——但函数体里又有这个调用点。切成 `1 + Σargs` 就是那个截断：
        它保留了**每层都要付的调用开销与实参求值**，只去掉「再深一层」的部分。

        结果按函数名缓存（同一函数在不同调用点只需按 `_param_size_table` 换尺寸）。
        """
        got = self._rec_once_cache.get(name)
        if got is not None:
            return got
        fn = self.fns.get(name)
        if fn is None:
            return Cost.num(0)
        # **只在算 B0 时切**：期间若碰到环上的**其它**函数，说明是间接递归，
        # 那个函数体没有被 `D ×` 覆盖到 —— 宁可报错，不当它是 0（低估）。
        old = self._rec_cut_root
        self._rec_cut_root = name
        try:
            body = self._body(fn)
        finally:
            self._rec_cut_root = old
        self._rec_once_cache[name] = body
        return body

    def _collect_repeats(self) -> None:
        for name, sp in self.specs.items():
            body = next((c for c in sp.children if c.kind == "spec-body"), None)
            for c in (body.children if body else []):
                if c.kind == "clause" and (c.name or "") == "repeats":
                    if c.children:
                        self.repeats[name] = expr_to_bound(c.children[0])
                        break

    def _len_alias(self, fn_name: str) -> dict:
        """`const n = xs.length();` → {n: xs}（**E23 尺寸量统一**，2026-11-06）。

        以前 target 写 `O(n)` 会变成 `n(n)`，而 κ 侧写的是 `n(xs)`——
        两个符号对不上，判定直接落成"无法判定"，**尽管 κ 早就算出来了**。
        """
        out = {}
        fn = next((c for c in (getattr(self.mod, "children", []) or [])
                   if getattr(c, "kind", "") == "fn" and getattr(c, "name", "") == fn_name),
                  None)
        if fn is None:
            return out
        # **const 与 var 都算**：`u64 n = xs.length();` 是 var-decl。
        # 只扫 const 的话，最常见的写法反而漏掉（实测 two_pointer_dedup 就是这种）。
        for d in list(_descendants(fn, "const-decl")) + list(_descendants(fn, "var-decl")):
            if not getattr(d, "name", "") or len(d.children) < 2:
                continue
            v = d.children[-1]
            if getattr(v, "kind", "") not in ("method-call", "call"):
                continue
            if (v.name or "").split(".")[-1] not in ("length", "size", "len"):
                continue
            recv = v.children[0] if v.children else None
            if getattr(recv, "kind", "") == "path" and recv.name:
                out[d.name] = recv.name
        return out

    def target_cost(self, name: str, dim: str = "time") -> Optional[Cost]:
        """取函数的 `target` 里某个维度（`time` / `allocation`）的 max 表达式；没有则 None。"""
        sp = self.specs.get(name)
        if sp is None:
            return None
        body = next((c for c in sp.children if c.kind == "spec-body"), None)
        for c in (body.children if body else []):
            if c.kind == "clause" and (c.name or "") == "target":
                got = self._find_slot(c, dim)
                if got is not None:
                    _m = self._len_alias(name)
                    if _m:
                        got = _rename_sizes(got, _m)
                    return got
        return None

    def _find_slot(self, target: Node, dim: str) -> Optional[Cost]:
        for d in _descendants(target, "dim"):
            if (d.name or "") != dim:
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
            # **`k(f)`：函数参数的成本符号**（`for_each(m, f)` 用它写 target）。
            if fn == "k":
                for c in n.children:
                    if c.kind == "args" and c.children and c.children[0].kind == "path":
                        return Cost("sym", f"k({c.children[0].name})")
                return None
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
    # ---------------------------------------------------------------- 分配（E36）
    #
    # **这是"静态分配上界"的初版模型**，规则刻意保守（宁多不少）：
    #
    # | 构造 | 分配上界 |
    # |---|---|
    # | 字面量（数字 / 字符串） | 0 —— 字符串字面量是编译期常量 |
    # | 内建（查 `stdlib.alloc_kind`） | 0 / 1（`slice`、`format_u64`、`text_trim` … 各 1） |
    # | 循环 | **每轮分配 × 轮数上界**（轮数用与时间成本同一套推导） |
    # | `if` / 块 | 各分支取 **max**（保守：不假设走哪条） |
    # | 调用用户函数 | 被调函数的分配上界（带环检测） |
    # | 其它 | 0 |
    #
    # **已知保守点**：`sort` / `reverse` / `fill` / `dedup` 当作**原地**（0 次分配）；
    # 若将来引进"可能扩容"的 `push`，要在 `alloc_kind` 里登记成未知。

    def alloc_of(self, fn_name: str) -> "Cost":
        """函数的**分配上界**（`allocation` 维度）。"""
        if self.diags:
            return Cost.top()
        if fn_name in self._alloc_visiting:
            return Cost.num(0)          # 环：按 0 处理（时间成本那边会报环）
        self._alloc_visiting.append(fn_name)
        try:
            fn = self.fns.get(fn_name)
            if fn is None:
                return Cost.num(0)
            body = stmt_body(fn)
            return self._alloc_stmts(body.children if body else [])
        finally:
            self._alloc_visiting.pop()

    def _alloc_stmts(self, stmts) -> "Cost":
        total = Cost.num(0)
        for s in stmts:
            total = Cost.add(total, self._alloc_stmt(s))
        return total

    def _alloc_stmt(self, s) -> "Cost":
        k = getattr(s, "kind", "")
        if k == "while":
            cond = while_cond(s)
            body = stmt_body(s)
            inner = self._alloc_stmts(body.children if body else [])
            if inner.is_const() and inner.value == 0:
                return Cost.num(0)      # 循环里不分配 → 轮数无关
            # 复用**时间成本那套**循环上界推导（G29 提取器），保证两维口径一致
            b, _why = bound_of_condition_wrapper(
                s, self.repeats.get, {}, self.row_of, self.capacities,
                self.const_names, self.cur_fn)
            if b is None:
                return Cost.top()       # 推不出轮数：分配上界未知（不撒谎）
            bound = bound_to_cost_inst(b, self.const_values)
            if bound is None or bound.is_top():
                return Cost.top()
            return Cost.mul(bound, inner)
        if k == "for":
            # `for (x in xs) { Vec<u64> t = [1,2]; … }` 每轮分配一次 → 上界 = 轮数 × 每轮
            body = stmt_body(s)
            inner = self._alloc_stmts(body.children if body else [])
            if inner.is_const() and inner.value == 0:
                return Cost.num(0)
            iterable = for_iterable(s)
            if iterable is not None and iterable.kind == "binary" and iterable.value == "..":
                return Cost.mul(iterable, inner)      # 区间：轮数就是长度（保守用表达式本身）
            if iterable is not None and iterable.kind == "path":
                return Cost.mul(Cost.size(iterable.name or ""), inner)
            return Cost.top()                          # 推不出轮数就不撒谎
        if k == "match":
            worst = Cost.num(0)
            for a in [c for c in s.children if c.kind == "arm"]:
                acc = Cost.num(0)
                for b in a.children:
                    if b.kind == "block":
                        acc = Cost.add(acc, self._alloc_stmts(b.children))
                    elif b.kind in ("return", "expr-stmt", "assign", "assign-op",
                                    "var-decl", "const-decl"):
                        acc = Cost.add(acc, self._alloc_stmt(b))
                worst = Cost.max(worst, acc)
            return worst
        if k == "if":
            body = stmt_body(s)
            els = stmt_else(s)
            a = self._alloc_stmts(body.children if body else [])
            b = self._alloc_stmt(els.children[0]) if (els is not None and els.children) else Cost.num(0)
            return Cost.max(a, b)
        if k == "block":
            return self._alloc_stmts(s.children)
        if k in ("var-decl", "const-decl", "let", "decl"):
            # `const Vec<u64> v = <expr>;` —— 分配来自**右值**
            # 子节点顺序：[类型名…, 右值]；右值就是最后一个非类型/非参数节点。
            rhs = None
            for c in reversed(s.children):
                if c.kind in ("type-name", "type-ref", "type-args", "params", "path"):
                    continue
                rhs = c
                break
            if rhs is None:
                return Cost.num(0)
            return self._alloc_expr(rhs, hint=(s.name or ""))
        if k in ("return", "expr-stmt"):
            return self._alloc_expr(s.children[0]) if s.children else Cost.num(0)
        return Cost.num(0)

    def _alloc_expr(self, e, hint: str = "") -> "Cost":
        if e is None:
            return Cost.num(0)
        k = getattr(e, "kind", "")
        if k == "call":
            short = (e.name or "").split(".")[-1]
            if stdlib.is_stdlib(short):
                attr = stdlib.alloc_kind(short)
                if attr == "nodes":
                    # **JSON 解析**：分配 = 1 + 节点数，而节点数 <= n(text)
                    _nm = "text"
                    for _c in e.children:
                        if _c.kind == "args" and _c.children and _c.children[0].kind == "path":
                            _nm = _c.children[0].name
                            break
                    return Cost.add(Cost.num(1), Cost.size(_nm))
                if attr == "1n":
                    # 一层嵌套：外层 1 次 + **每行 1 次**。
                    # 行数用**绑定的名字**（`rows`）而不是调用名——`n(rows)` 才是
                    # 读者认得的尺寸量，而且它 ≤ n(输入)。
                    _nm = hint or "rows"
                    return Cost.add(Cost.num(1), Cost.size(_nm))
                if attr == "1":
                    return Cost.num(1)
                if attr == "n":
                    return Cost.size((e.children[0].name if e.children else "n") or "n")
                return Cost.num(0)
            return self.alloc_of(short)
        if k in ("lit-int", "lit-str", "path", "lit-bool"):
            return Cost.num(0)
        if k == "array":
            # `[1, 2]` 构造一个 Vec：**分配 1 次**。
            # 以前这里落到"把子节点分配相加"，而子节点全是字面量（0）——
            # 于是循环里 `Vec<u64> t = [1,2]` 的分配被算成 0。
            return Cost.num(1)
        total = Cost.num(0)
        for c in getattr(e, "children", []) or []:
            total = Cost.add(total, self._alloc_expr(c))
        return total

    def cost_of(self, fn_name: str) -> Cost:
        if self.diags:
            return Cost.top()
        self.cur_fn = self.fns.get(fn_name)
        self.row_of = {}
        self.capacities: Dict[str, int] = {}   # E22：定容局部 → 容量常数
        self._reassigned = set()
        fn = self.fns.get(fn_name)
        if fn is None:
            raise CostError(f"找不到函数 {fn_name}")
        return self._body(fn)

    def _body(self, fn: Node) -> Cost:
        # **把函数名压栈**：`_call` 靠 `self.frames` 认出「递归调用点」。
        # 修前只有 `self.inlining`（分支③ 才压），有 target 的函数体从不压栈，
        # 于是递归那一跳被当成普通调用按 target 记**一层**——深度整个丢了。
        self.frames.append(fn.name or "")
        try:
            blocks = [c for c in fn.children if c.kind == "block"]
            if not blocks:
                return Cost.num(0)
            env: Dict[str, Cost] = {}
            # 形参容器登记尺寸（`xs.length()` 可直接用）
            for pm in _descendants(fn, "param"):
                pass
            return self._block(blocks[0], env)
        finally:
            self.frames.pop()

    # ---------- 块 / 语句
    def _block(self, blk: Node, env: Dict[str, Cost]) -> Cost:
        total = Cost.num(0)
        for st in blk.children:
            total = Cost.add(total, self._stmt(st, env))
        return total

    def _stmt(self, st: Node, env: Dict[str, Cost]):
        if getattr(st, "kind", None) == "assign-op":
            rhs = st.children[1] if len(st.children) > 1 else None
            return Cost.add(Cost.num(OP_COST),
                            self._expr(rhs, env) if rhs is not None else Cost.num(0))
        return self._core_stmt(st, env)

    def _core_stmt(self, st: Node, env: Dict[str, Cost]) -> Cost:
        k = st.kind

        _name = COST_STMT.get(k)
        if _name is not None:
            return getattr(self, _name)(st, env)
        return Cost.num(OP_COST)

    # ---------- 循环


    def _k_stmt_const_decl(self, st, env):
        # **记下声明类型**：`unordered_map`/`unordered_set` 的成本是 O(n)，
        # `map`/`set` 是 O(log n)——同一个方法名，靠接收者的类型区分。
        _tyn = st.children[0] if st.children else None
        _head = getattr(_tyn, "name", None) if getattr(_tyn, "kind", "") == "type-name" else None
        if _head and getattr(st, "name", None):
            self.vty[st.name] = canon(_head)
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
    def _k_stmt_assign(self, st, env):
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
    def _k_stmt_expr_stmt(self, st, env):
        # **语句本身 +1**：解释器 `_stmt` 每条语句记 1，以前 κ 漏了这 1
        _base = Cost.num(OP_COST)
        if st.children:
            return Cost.add(_base, self._expr(st.children[0], env))
        return _base
    def _k_stmt_return(self, st, env):
        c = Cost.num(OP_COST)
        if st.children:
            c = Cost.add(c, self._expr(st.children[0], env))
        return c
    def _k_stmt_if(self, st, env):
        cond = st.children[0] if st.children else None
        branches = [c for c in st.children[1:] if c.kind == "block"]
        # 甲.2：条件要用 _expr 求整条表达式的成本。
        cc = Cost.add(Cost.num(OP_COST),
                      self._expr(cond, env) if cond is not None
                      else Cost.num(0))
        if not branches:
            return cc
        # E4：合并必须真的发生。以前每个分支拿 dict(env) 副本，改完就扔，
        # 于是分支里赋的界全丢——if (c) { i = xs.length() } 之后的 i
        # 被当成说不清的量（n(i)），κ 里出现 n(i)，判决变 unknown。
        # 现在只合并【被写过的键】，值取 Cost.max（上界语义）；
        # 没被写过的变量原样保留，不制造「两条路都不存在」的假状态。
        before = dict(env)
        costs, envs = [], []
        for b in branches:
            e2 = dict(env)
            costs.append(self._block(b, e2))
            envs.append(e2)
        worst = costs[0]
        for x in costs[1:]:
            worst = Cost.max(worst, x)
        changed = set()
        for e2 in envs:
            for k, v in e2.items():
                if k not in before or before[k].render() != v.render():
                    changed.add(k)
        for k in changed:
            vals = ([before[k]] if k in before else []) + [e2[k] for e2 in envs if k in e2]
            cur = vals[0]
            for v in vals[1:]:
                cur = Cost.max(cur, v)
            env[k] = cur
        return Cost.add(cc, worst)

    def _k_stmt_parallel(self, st, env):
        # **B-24**：κ = Σ_i κ_i（每个任务是 `parallel { {…} {…} }` 里的一个块）。
        # 判阶时等价于 max（阶相加取 max），因此 target 判定仍成立。
        body = stmt_body(st)
        if body is None:
            return Cost.num(0)
        total = Cost.num(0)
        for task in body.children:
            total = Cost.add(total, self._block(task, env) if task.kind == "block"
                             else self._stmt(task, env))
        return total
    def _k_stmt_break(self, st, env):
        # `break` **只会减少**执行 → 不增加上界
        return Cost.num(0)

    def _k_stmt_continue(self, st, env):
        return Cost.num(0)

    def _k_stmt_while(self, st, env):
        return self._while(st, env)
    def _k_stmt_for(self, st, env):
        return self._for(st, env)
    def _k_stmt_match(self, st, env):
        return self._match(st, env)
    def _k_stmt_block(self, st, env):
        return self._block(st, dict(env))
    def _k_stmt_dispatch(self, st, env):
        # `?!L(...)` 单独成句（`?!L(x);`）
        return self._dispatch(st, env)


    # ---------------------------------------------------------------- 归因钩子
    #
    # 三个"元凶粒度"的构造：循环 / 调用 / `?!` 选择。
    # **只在 explain 下登记**，而且只写侧表——**不参与任何算术**，
    # 所以开着它算出来的 κ 与不开**逐字节相同**（这是硬约束）。

    def _mark(self, c: "Cost", node: Node, kind: str, note: str = "") -> "Cost":
        if self.explain and c is not None:
            try:
                self._keep.append(c)
                self.origins[id(c)] = {
                    "line": getattr(node, "line", 0),
                    "col": getattr(node, "col", 0),
                    "kind": kind,
                    "note": note,
                    # `substitute_sizes` 会**重建**节点，`id()` 随之失效；
                    # 记一份 render 文本，报告侧可以按文本回找。
                    "render": c.render(),
                }
            except Exception:                          # noqa: BLE001
                pass
        return c

    def _while(self, st: Node, env: Dict[str, Cost]) -> Cost:
        return self._mark(self._while_inner(st, env), st, "loop")

    def _call(self, name: Optional[str], node: Node, env: Dict[str, Cost]) -> Cost:
        c = self._call_inner(name, node, env)
        return self._mark(c, node, "call", (name or "").split(".")[-1])

    def _dispatch(self, node: Node, env: Dict[str, Cost]) -> Cost:
        c = self._dispatch_inner(node, env)
        return self._mark(c, node, "dispatch", node.name or "")

    def _while_inner(self, st: Node, env: Dict[str, Cost]) -> Cost:
        cond = st.children[0] if st.children else None
        body = next((c for c in st.children[1:] if c.kind == "block"), None)

        # 用 G29 的提取器拿圈数。计数器初值从 env 里取（不做数据流分析）。
        # 同时把 env 里已知的尺寸名交给它，避免 `n` 认不出来。
        b, _why = bound_of_condition_wrapper(
            st, self.repeats.get, env, self.row_of, self.capacities,
            self.const_names, self.cur_fn)
        if b is None:
            return Cost.top()

        n = bound_to_cost_inst(b, self.const_values)
        # **甲.2**：每轮的成本是「迭代开销 1 + 条件**整条表达式** + 体」。
        # 原先只算 1 个操作代表条件，而解释器每轮都把条件求值一遍
        # （`i < 10` 是 3 个节点）——这是残余差距的主要来源。
        cond_cost = self._expr(cond, env) if cond is not None else Cost.num(OP_COST)
        # **每轮多记 1 个操作**：解释器在每轮里除了求值条件、执行体，还要做一次
        # 循环本身的记账（`_stmt` 包装层）。E35 对账实测：常量边界的循环
        # （`while (j < 256)`）每轮少记 1，累计 249 次；变量边界的循环因为边界
        # 估计偏大而被掩盖了。**只许变大，因此这里加而不是减。**
        # **每轮 1 个操作**（2026-11-02 从 3 改回 1）。
        #
        # 历史：E35 时条件只按 1 个操作记，实测每轮比 κ 多 1 → 加了 3 补偿。
        # 后来「甲.2」把条件改成**整条表达式**计（`cond_cost`），那次补偿就变成
        # **重复计数**：实测每轮 9 步，κ 记 14 步（1.46 倍），差距全在这 2 个
        # 多出来的常数上。实测（const_loop / scan_until，κ÷实测）：
        #   3 → 1.46 / 1.45    2 → 1.37 / 1.39
        #   1 → 1.28 / 1.32    0 → 1.18 / 1.26（余量只剩 18%，不留）
        # 取 1：仍留约 27% 余量，`kappa_vs_ops --strict` 破界 0、10/10 达标。

        per = Cost.add(Cost.num(OP_COST * 1), cond_cost)
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
                                       self.row_of, self.capacities,)
                if rhs is not None:
                    body_cost = body_cost.substitute_sizes(
                        {cond.children[0].name: bound_to_cost_inst(rhs, self.const_values)})
            per = Cost.add(per, body_cost)
        # 每圈取最坏那圈，再乘圈数（用户定的第 3 条）；
        # 末尾那次「条件为假」的检查也要算进去。
        return Cost.add(Cost.mul(n, per), per)

    def _for(self, st: Node, env: Dict[str, Cost]) -> Cost:
        body = stmt_body(st)
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
        """`match`：`1 + κ(被匹配表达式) + max(各臂 κ)`。

        **以前只收集 `kind=="block"` 的臂**，于是 `0 => return 1,` 这种单语句臂
        只贡献 `OP_COST`；如果整条 match 还因为模式解析失败被丢掉，κ 直接变 0。
        """
        # 被匹配的表达式（第一个非 arm 子节点）——助手与 interp 共用，防两边漂移
        scrut = match_scrutinee(st)
        base = Cost.num(OP_COST)
        if scrut is not None:
            base = Cost.add(base, self._expr(scrut, env))
        arms = arms_of(st)
        worst: Optional[Cost] = None
        for a in arms:
            c = Cost.num(OP_COST)
            for b in a.children:
                if b.kind in ("block",):
                    c = Cost.add(c, self._block(b, dict(env)))
                elif b.kind in ("return", "expr-stmt", "assign", "assign-op",
                                "var-decl", "const-decl"):
                    c = Cost.add(c, self._stmt(b, dict(env)))
            worst = c if worst is None else Cost.max(worst, c)
        return Cost.add(base, worst) if worst is not None else base

    # ---------- 调用
    def _expr(self, n: Node, env: Dict[str, Cost]):
        _k = getattr(n, "kind", None)
        if _k in ("array", "deref"):
            total = Cost.num(OP_COST)
            for c in n.children:
                total = Cost.add(total, self._expr(c, env))
            return total
        return self._core_expr(n, env)

    def _core_expr(self, n: Node, env: Dict[str, Cost]) -> Cost:
        k = n.kind
        _name = COST_EXPR.get(k)
        if _name is not None:
            return getattr(self, _name)(n, env)
        return Cost.num(0)


    def _k_expr_call(self, n, env):
        _c = self._call(n.name, n, env)
        if "::" in (n.name or ""):
            # **限定名调用（`Type<...>::method(...)`）多 1 步**：
            # 解释器会先求值类型实参路径再派发。E35 对账实测：
            # `FixedVec<u64,4>::filled(0)` 的 κ 比 ops 少 1。
            _c = Cost.add(_c, Cost.num(OP_COST))
        return _c
    def _k_expr_struct_literal(self, n, env):
        # **结构体字面量**：以前落到兜底 `Cost.num(0)`，字段表达式全丢。
        #
        # **只认这一个拼写**：解析器只产出 `struct-literal`（`parser.py:1616`），
        # 以前这里还列了 `struct-lit` / `record-literal` 两个**从不出现的别名**——
        # 那三个字符串让"两值域 kind 覆盖表"看起来对得上，实际是假的对称
        # （`walk.py` 的漂移检测就是靠这个报出来的）。
        _c = Cost.num(OP_COST)
        for _ch in (n.children or []):
            if getattr(_ch, "kind", "") == "args":
                for _f in _ch.children:
                    _c = Cost.add(_c, self._expr(_f, env))
            elif getattr(_ch, "kind", "") not in ("path", "type-ref", "type-name"):
                _c = Cost.add(_c, self._expr(_ch, env))
        return _c
    def _k_expr_dispatch(self, n, env):
        return self._dispatch(n, env)
    def _k_expr_tuple(self, n, env):
        # **元组构造**：解释器 `_core_eval` 在**每个节点**上 `_tick()` 一次，
        # 然后逐个求值元素 ⇒ 成本 = 1 + Σ元素。原先这里落到兜底，
        # 整个元组免费（`(a, b)` 的 κ 比 ops 少 2）。
        _c = Cost.num(OP_COST)
        for _ch in (n.children or []):
            _c = Cost.add(_c, self._expr(_ch, env))
        return _c
    def _k_expr_type_name(self, n, env):
        # 类型名（`Vec<u64>` 里的 `Vec`、`Enum::Variant` 的类型部分）：
        # 解释器只是造一个 `TypeRef`，**不进入子节点**，所以记 1 即可。
        # 原先落兜底 ⇒ 与 interp 侧不对称（walk 报的正是这一条）。
        return Cost.num(OP_COST)
    def _k_expr_binary(self, n, env):
        # **必须递归进子表达式**（E17）：`t = t + inner(xs)` 里 inner 的成本
        # 原先是整块丢掉的——这会让 κ 小于真实成本，**上界就不成立**。
        total = Cost.num(OP_COST)
        for c in n.children:
            total = Cost.add(total, self._expr(c, env))
        return total
    def _k_expr_method_call(self, n, env):
        # 同理：`xs.at(inner(xs))` 的实参成本、`m.at(i)` 的接收者成本都要算。
        # **甲.2**：再加 1 个操作代表**方法本身的派发/执行**——
        # 实测 `xs.at(i)` 在解释器里比「节点 + 接收者 + 实参」多 1 步。
        # **3 而不是 2**（E35 实测）：`x.at(i)` 在解释器里要走
        # 节点 → 接收者 → 实参 → 派发 四步，κ 记 2 会**少算 1**。
        #
        # **E36 修正：实参子树原先整块被丢掉——这是 `matmul` κ 少算的根因。**
        # `_expr` 对 `args` 节点走兜底 `Cost.num(0)`，所以
        # `a.at(i*dim + k)` 里 `i*dim` 的乘法、`+ k` 的加法、
        # 三个叶子（`i` / `dim` / `k`）**一个操作都没进 κ**。
        # 解释器 `_method` 的实际计费点是
        #   「方法节点 1 + 接收者 + **每个实参表达式** + 派发 1」，
        # 因此正确值是 `2 + 接收者 + Σκ(实参)`：
        #   `xs.at(i)`       → 2+1+1 = 4（与修前 4 相同）
        #   `a.at(i*dim+k)`  → 2+1+5 = 8（修前 4，补回 4；实测解释器正是 8）
        #   `xs.length()`    → 2+1+0 = 3（修前 4：修前对零实参多记 1）
        # 但**零实参的 `length()` 不能跟着降**：那会让 merge / search / stats
        # 的余量变小（验收要求「其它 8 个函数余量不得变小」）。所以与修前的
        # `3 + 接收者` 取 **max** ⇒ 任何调用的 κ 都单调不减：
        #   `out.push(Interval{...})`（实参是结构体字面量、κ 记 0）
        #   → max(3, 2+0) = 3，与修前一致，merge 的余量不动。
        args_node = next((c for c in n.children if c.kind == "args"), None)
        args_cost = Cost.num(0)
        for a in (args_node.children if args_node is not None else []):
            args_cost = Cost.add(args_cost, self._expr(a, env))
        total = Cost.max(Cost.num(OP_COST * 3),
                         Cost.add(Cost.num(OP_COST * 2), args_cost))
        # **有序 map/set 的操作是 `O(log2 n)`，不是常数**（B2）。
        # 以前按普通方法调用记常数 —— 那让 κ **偏小**：调用方会以为
        # `m.get(k)` 是 O(1)，而承诺是 O(log n)。**偏小的上界比没有更糟。**
        _mname = (n.name or "").split(".")[-1]
        if _mname in ("get", "insert", "contains", "remove", "keys", "values"):
            _recv = n.children[0] if n.children else None
            if getattr(_recv, "kind", "") == "path" and getattr(_recv, "name", None):
                _rt = canon(self.vty.get(_recv.name, ""))
                # **unordered：承诺最坏 O(n)**（哈希）。map/set：O(log n)。
                # 同一方法名靠接收者声明类型区分——不然就会给错的阶。
                if _rt in ("unordered_map", "unordered_set"):
                    total = Cost.add(total, Cost.size(_recv.name))
                else:
                    total = Cost.add(total, Cost.log2(Cost.size(_recv.name)))
        for c in n.children:
            if c.kind == "args":
                continue        # 实参节点本身不计费（解释器也不求值它），逐个实参上面已计
            total = Cost.add(total, self._expr(c, env))
        return total
    def _k_expr_paren(self, n, env):
        if n.children:
            return self._expr(n.children[0], env)
        return Cost.num(0)
    def _k_expr_lit_int(self, n, env):
        # **甲（E24）**：叶子也要计数。
        # `operations` 的单位是「语句 + 表达式节点（含叶子）+ 循环轮数」（见 `test_units.py`）；
        # 这里原先走兜底 `Cost.num(0)`，叶子免费 ⇒ κ 比实际步数**小**
        # （实测 `paren` 差 2.00 倍、`lcs` 差 4.96 倍）。
        return Cost.num(OP_COST)
    def _k_expr_index(self, n, env):
        total = Cost.num(OP_COST)
        for c in n.children:
            total = Cost.add(total, self._expr(c, env))
        return total
    def _k_expr_try(self, n, env):
        # **`(...)?` 的成本不能消失**：`return (inner(xs))?;` 原先走到这里
        # 直接 return 0，被调函数的成本整个丢了（与 E17 同一类，方向是低估）。
        # `dispatch` 子节点跳过——它在声明分支里已经单独记过，重复计费会把上界撑大。
        total = Cost.num(OP_COST)
        for c in n.children:
            if c.kind == "dispatch":
                continue
            total = Cost.add(total, self._expr(c, env))
        return total


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

    def _extern_cost(self, name: str) -> Cost:
        """FFI 调用的成本：登记过就用登记值，否则 `ext(name)`。"""
        if name in self.externals:
            return self.externals[name]
        return Cost("ext", name)

    def _frame_fn(self) -> Optional[Node]:
        """**当前正在分析的那一帧**是哪个函数（`_body` 的栈顶）。

        修前这里只看 `inlining` / `cur_fn`：有 target 的函数体遍历时两个都没记，
        于是「这个帧的形参是什么类型」问不出来。`self.frames` 在 `_body` 里
        统一压栈，两条路径（内联 / 走 target）都覆盖。
        """
        name = self.frames[-1] if self.frames else (
            self.cur_fn.name if self.cur_fn is not None else None)
        return self.fns.get(name) if name else None

    def _frame_param_names(self) -> set:
        """**当前正在分析的那一帧**的形参名。

        `_body` 的 `env` 里没有形参（见 `_body`），所以 `_args_cost` 判
        「这个实参是不是一个**被局部绑定的**函数名」时不能只看 `env`，
        还要看形参——否则 `func f(..., func(A)->R g) { h(g); }` 会把
        形参 `g` 误当模块函数、少记 1 步（**低估**是不安全的方向）。
        """
        fn = self._frame_fn()
        pnode = next((c for c in (fn.children if fn is not None else [])
                      if c.kind == "params"), None)
        return {(p.name or "") for p in (pnode.children if pnode is not None else [])}

    # ---------- ④ 函数值（`g: func(A)->R`）
    def _lookup_fnval(self, name: str):
        """在函数值绑定栈里查名字 → `(找到了?, 具名函数名 或 None)`。

        `None` 表示「这是函数值形参，但调用点不知道它绑到哪个函数」——
        那就是 `k(g)` 符号项，不是 0（低估）。
        """
        for b in reversed(self.fnval_stack):
            if name in b:
                return True, b[name]
        return False, None

    def _is_fnvalue_param(self, name: str) -> bool:
        """名字是不是**当前帧**声明成 `func(...) -> R` 的形参。"""
        if not name:
            return False
        fn = self._frame_fn()
        if fn is None:
            return False
        pnode = next((c for c in fn.children if c.kind == "params"), None)
        for p in (pnode.children if pnode is not None else []):
            if (p.name or "") != name:
                continue
            ty = p.children[0] if p.children else None
            return getattr(ty, "kind", "") == "type-fn"
        return False

    def _fnvalue_bindings(self, fn: Node, call: Node,
                          env: Dict[str, Cost]) -> Dict[str, Optional[str]]:
        """把「调用点的实参 → 形参」里**函数类型**的那几项记下来。

        | 实参形状 | 值 | 含义 |
        |---|---|---|
        | 本模块的具名函数（未被局部遮蔽） | 函数名 | `k(g)` **静态可求** |
        | 上一层帧的函数值形参 | 沿链上溯 | 继续往上找 |
        | 其它 | `None` | 仍是符号：调用点不知道 |

        **为什么要看 `env`**：解释器 `_args` 只有在「名字不在 `env` 里」时才把
        具名函数包成 `FnVal`；`const u64 f = 3;` 之后再用 `f` 就是普通变量。
        """
        pnode = next((c for c in fn.children if c.kind == "params"), None)
        anode = next((c for c in call.children if c.kind == "args"), None)
        if pnode is None or anode is None:
            return {}
        out: Dict[str, Optional[str]] = {}
        for p, a in zip(pnode.children, anode.children):
            ty = p.children[0] if p.children else None
            if ty is None or getattr(ty, "kind", "") != "type-fn":
                continue
            tgt = a
            while tgt is not None and getattr(tgt, "kind", "") in ("ref", "paren"):
                tgt = tgt.children[0] if tgt.children else None
            if tgt is None or getattr(tgt, "kind", "") != "path" or not tgt.name:
                # 实参是表达式（匿名函数之类）→ 本语言没有一等函数，不猜
                out[p.name] = None
                continue
            nm = tgt.name
            if nm in self.fns and nm not in env:
                out[p.name] = nm
                continue
            found, bound = self._lookup_fnval(nm)
            if found:
                out[p.name] = bound
            elif self._is_fnvalue_param(nm):
                out[p.name] = None
            else:
                # 本模块没有这个函数、也不是形参 ⇒ 外部符号（按 ext/符号记账）
                out[p.name] = None
        return out

    def _inline_body(self, fn: Node, node: Node, env: Dict[str, Cost]) -> Cost:
        """内联一个用户函数：`1 + 被调体 + Σ实参`，并带入函数值绑定（④）。"""
        # **实参在调用方那一帧里求值**：先算它，再进被调帧——
        # 否则 `_args_cost` 会把「被调函数的形参名」当成调用方的绑定名。
        _args = self._args_cost(node, env)
        _tab = self._param_size_table(fn, node)
        _binds = self._fnvalue_bindings(fn, node, env)
        self.inlining.append(fn.name or "")
        self.fnval_stack.append(_binds)
        try:
            # **实参也要算**（与①②同一修法）：`_body(fn)` 只含被调函数的块，
            # 调用方实参表达式**不在**其中——`_param_size_table` 只换尺寸、
            # 不产生操作数。所以 `1 + 被调体 + Σ实参` 里每一项互不重叠。
            return Cost.add(Cost.num(OP_COST),
                            Cost.add(self._body(fn).substitute_sizes(_tab), _args))
        finally:
            self.fnval_stack.pop()
            self.inlining.pop()

    def _fnvalue_call(self, fname: str, node: Node,
                      env: Dict[str, Cost]) -> Cost:
        """`g(x)` 且调用点**知道** `g` 是哪个具名函数 → 按普通调用的三条路径算。"""
        fn = self.fns.get(fname)
        if fn is None:
            # 只知道名字、不知道实现：记 `1 + Σ实参 + ext(name)`
            return Cost.add(Cost.num(OP_COST),
                            Cost.add(self._extern_cost(fname),
                                     self._args_cost(node, env)))
        if fname in self.frames:
            # 函数值构成的环（把递归函数当值传）：没有声明就**不猜**
            raise CostError(
                f"函数值 {fname} 成环（ {' → '.join(self.frames + [fname])} ）；"
                f"给它补 `recursion depth`，或不要把它当值传")
        t = self.target_cost(fname)
        _tab = self._param_size_table(fn, node)
        if t is not None:
            return Cost.add(Cost.num(OP_COST),
                            Cost.add(t.substitute_sizes(_tab),
                                     self._args_cost(node, env)))
        return self._inline_body(fn, node, env)

    def _args_cost(self, node: Node, env: Dict[str, Cost]) -> Cost:
        """`f(...)` 的**实参求值**成本：Σ κ(每个实参表达式)。

        ## 为什么必须逐个 `args.children` 走

        解释器 `_args`（`src/interp.py`）对**每个实参表达式**调用一次 `_eval`，
        每个 `_eval` 起点就 `_bump()` 记 1；**但 `args` 节点本身不求值**。
        而 `_expr(args)` 在 `_core_expr` 里走兜底 `Cost.num(0)`——
        所以「`for c in node.children` 里带上 args 节点」等于没算实参。

        这里逐个孩子走，与解释器的计费点一一对应，且**只加一次**：
        本函数只在确实没算过实参的分支里调用（见 `_call` 各处注释）。

        ## 两种**记 0** 的实参（与解释器同口径，不算就是不算）

        · `named-arg`：`Arith(cause = …)`——解释器 `_args` 只求值里面的表达式；
        · **具名函数实参**：`f(xs, add)` 里 `add` 被 `_args` 包成 `FnVal`，
          **不调 `_eval`** ⇒ 0 步。若不排除，每个这样的实参给 κ **虚加 1**。
        """
        an = next((c for c in node.children if c.kind == "args"), None)
        _params = self._frame_param_names()
        total = Cost.num(0)
        for a in (an.children if an is not None else []):
            if getattr(a, "kind", "") == "named-arg":
                # `Arith(cause = ...)`：解释器只求值里面的表达式（`_args` 同一处理）
                a = a.children[0] if a.children else None
                if a is None:
                    continue
            if (getattr(a, "kind", "") == "path" and (a.name or "") in self.fns
                    and (a.name or "") not in env and (a.name or "") not in _params):
                # **函数值实参**：解释器 `_args` 走 `FnVal` 分支，不 `_eval` ⇒ 0 步。
                # 判定与 `interp._args` 一致：本模块有同名函数、且没被局部/形参遮蔽。
                continue
            total = Cost.add(total, self._expr(a, env))
        return total

    def _call_inner(self, name: Optional[str], node: Node, env: Dict[str, Cost]) -> Cost:
        # **堆原语是 `O(log2 n)`，不是常数**（对齐 std::push_heap/pop_heap）。
        _short_hp = (name or "").split(".")[-1]
        if _short_hp in ("push_heap", "pop_heap"):
            _an = next((c for c in node.children if c.kind == "args"), None)
            _items = _an.children if _an is not None else []
            _recv = _items[0] if _items and getattr(_items[0], "kind", "") == "path" else None
            _base = Cost.add(Cost.num(OP_COST), self._args_cost(node, env))
            if _recv is not None and getattr(_recv, "name", None):
                return Cost.add(_base, Cost.log2(Cost.size(_recv.name)))
            return _base

        # **FFI**：`extern` 声明的名字 —— 成本是 `ext(name)`（或 `external` 登记值）
        # **高阶内建**：成本 = n(容器) × κ(回调) + n(容器)
        _short_hof = (name or "").split(".")[-1]
        # **`for_each(m, f)`**：成本 = `n(m) × k(f) + n(m)`。
        # `k(f)` 是**符号**——函数是参数，它的成本要由调用方保证（见计划 §2.5）。
        if _short_hof == "for_each":
            _an = next((c for c in node.children if c.kind == "args"), None)
            _items = _an.children if _an is not None else []
            _n = (Cost.size(_items[0].name) if _items and _items[0].kind == "path"
                  else Cost.size("m"))
            _fname = (_items[1].name if len(_items) >= 2 and _items[1].kind == "path" else "f")
            # **`k(visit)` 是裸符号**（不是 `n(visit)`——那读作"visit 的规模"）。
            _per = Cost.add(Cost.num(OP_COST), Cost("sym", f"k({_fname})"))
            _base = Cost.add(Cost.mul(_n, _per), _n)
            # **实参表达式**（②）：`for_each(m, visit)` 里 `m` 是先求值的
            # （`visit` 是具名函数，解释器包成 `FnVal` 不求值 ⇒ `_args_cost` 记 0）。
            # 与上面的 `n × …` 尺寸项**不重叠**：那是「转几圈」，这是「求一次实参」。
            return Cost.add(_base, self._args_cost(node, env))
        if _short_hof in ("map", "filter", "fold", "count_if"):
            _an = next((c for c in node.children if c.kind == "args"), None)
            _items = _an.children if _an is not None else []
            _n = Cost.size(_items[0].name) if _items and _items[0].kind == "path" else Cost.size("xs")
            _cb_node = _items[2] if (_short_hof == "fold" and len(_items) >= 3) else (
                _items[1] if len(_items) >= 2 else None)
            _per = Cost.num(OP_COST)
            if _cb_node is not None and _cb_node.kind == "path":
                _cb = (_cb_node.name or "")
                if _cb in self.fns:
                    # 把回调的 κ 求成常数（元素尺寸按 1）
                    from .cost_check import evaluate as _ev
                    _k = self.cost_of(_cb)
                    _per = Cost.num(OP_COST + int(_ev(_k, {}) or 0))
            _base = Cost.mul(_n, _per)
            _base = Cost.add(_base, _n)
            for _c in node.children:
                _base = Cost.add(_base, self._expr(_c, env))
            # **实参表达式**（②）：以前只走 `_expr(args)`（兜底 0），实参一个没算。
            # 这里只补 `args` 子树，**不碰** 上面的 `n × κ(f)` 尺寸项——两者不同源：
            # 尺寸项是「回调跑几遍」，这一项是「实参各求值一次」（解释器 `_eval` 记 1）。
            return Cost.add(_base, self._args_cost(node, env))
        _short_ffi = (name or "").split(".")[-1]
        if _short_ffi in getattr(self, "extern_names", set()) or \
                _short_ffi in getattr(self, "extern_spec_names", set()):
            _base = self._extern_cost(_short_ffi)
            for _c in node.children:
                _base = Cost.add(_base, self._expr(_c, env))
            return _base
        # `min(a,b)` / `max(a,b)` —— **常数成本，不改变阶**。
        # 这是它们能进内建集的唯一理由：作者拿它替掉 `if` 串两支，成本式不变。
        if (name or "") == "print":
            # 调试内建：1 个操作 + 实参成本（不建模 IO 延迟）。
            # 注意参数名是 `node`，不是 `n`——写错会 UnboundLocalError。
            # **实参以前整块丢了**（`args` 在 `_expr` 里走兜底 0）：`print("x")`
            # 解释器是 call 1 + 派发 1 + 实参 1 = 3，κ 只记了 2。
            total = Cost.num(OP_COST)
            for c in node.children:
                if c.kind == "args":
                    continue
                total = Cost.add(total, self._expr(c, env))
            return Cost.add(total, self._args_cost(node, env))
        if stdlib.is_stdlib(name or ""):
            # 最小基本库：本批全部 `O(n(第一个实参))`。
            args_node = next((c for c in node.children if c.kind == "args"), None)
            items = args_node.children if args_node is not None else []
            xs = items[0].name if items and items[0].kind == "path" else "?"
            n = Cost.size(xs)
            kind = stdlib.cost_kind(name or "")
            if kind == "ext":
                # **外部项**：不是"不知道多少"，而是"**除了这一项都算得出**"。
                # 渲染成 `ext(read_file)`，让读者看见成本里哪一块是外部给的。
                if (name or "") in self.externals:
                    return self.externals[name or '']
                return Cost("ext", name or "")
            if kind == "top":
                return Cost.top()
            if kind == "1":
                core = Cost.num(0)       # 常数成本（有界探测的哈希表）
            elif kind == "nret":
                # 流式口径：成本 = 1 + n(ret)（这次读了多少字节）
                return Cost.add(Cost.num(OP_COST), Cost.size("ret"))
            elif kind == "nm":
                # `text_find(hay, needle)`：最坏 O(|hay| × |needle|)
                _an = next((c for c in node.children if c.kind == "args"), None)
                _it = _an.children if _an is not None else []
                _m = Cost.size(_it[1].name) if len(_it) > 1 and _it[1].kind == "path" else Cost.num(1)
                core = Cost.mul(n, _m)
            elif kind == "log":
                core = Cost.log2(n)
            else:
                core = n
            total = Cost.add(Cost.num(OP_COST), Cost.add(core, Cost.num(OP_COST)))
            for c in items:
                total = Cost.add(total, self._expr(c, env))
            return total
        if (name or "") in ("min", "max"):
            # **内建派发 1 + 每个实参的求值**：以前只记 1、完全不计实参，
            # 于是 `max(b, xs.at(i))` 的 κ 比解释器实测少（实测 1+实参）。
            # **③**：解释器在 `_call` 里对 `BUILTINS_INT2` 命中时还会 `_bump()`
            # 一次（内建派发），修前那一步没进 κ —— `max(b, 1)` 实测 5、κ 记 4。
            # 所以是 `1（调用）+ 1（派发）+ Σ实参`，不再有「缺口 ≤ 1」。
            _an = next((c for c in node.children if c.kind == "args"), None)
            _args_cost = Cost.num(0)
            for _c in (_an.children if _an is not None else []):
                _args_cost = Cost.add(_args_cost, self._expr(_c, env))
            return Cost.add(Cost.num(OP_COST * 2), _args_cost)

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
            # **实参表达式**（②）：修前只返回 `core`，实参（`xs`、比较器名）
            # 与内建派发那一步**一个都没计**。解释器：call 1 + 派发 1 + 实参。
            # 比较器名是具名函数 ⇒ `_args_cost` 记 0（与解释器的 `FnVal` 一致）。
            return Cost.add(Cost.add(Cost.num(OP_COST * 2), core),
                            self._args_cost(node, env))
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

        # **④ 函数值调用 `g(x)`**（`g: func(A)->R`）：
        # `g` 不在 `self.fns` 里，但它可能是**某个形参**。两条路：
        #   · 调用点知道它绑到哪个具名函数 → `k(g)` 静态可求，按普通调用算；
        #   · 不知道 → 记 `1 + Σ实参 + k(g)`（符号项，**不是 0**：0 是低估）。
        # 语言契约是「调用方用 `k(g)` 保证」，所以符号项正是契约里那一项。
        _found, _bound = self._lookup_fnval(short)
        if _found:
            if _bound is not None:
                return self._fnvalue_call(_bound, node, env)
            return Cost.add(Cost.num(OP_COST),
                            Cost.add(self._args_cost(node, env),
                                     Cost("sym", f"k({short})")))
        if self._is_fnvalue_param(short):
            return Cost.add(Cost.num(OP_COST),
                            Cost.add(self._args_cost(node, env),
                                     Cost("sym", f"k({short})")))

        # **递归调用点（①）**：`short` 已在当前函数体的遍历栈里 ⇒ 这条边在环上。
        # 修前：有 target 就按**一层** target 记（深度整个丢掉，`down(xs,8)` 的
        # κ 只有一层，实测 104 > 33）；无 target 就在下面的内联分支报「成环」。
        # 修后：用 `.tu` 的 `recursion depth <= N` 乘上「函数体只算一次」的 κ。
        if short in self.frames:
            # 正在算 B0（函数体只算一次）：把**本函数**的递归切成 `1 + Σ实参`，
            # 只保留每层都要付的调用开销——这正是 B0 的定义。
            if self._rec_cut_root == short:
                return Cost.add(Cost.num(OP_COST), self._args_cost(node, env))
            if self._rec_cut_root is not None:
                raise CostError(
                    f"间接递归（{self._rec_cut_root} 与 {short} 在同一个环上）："
                    f"两个函数的深度上界不能只乘一个，暂不建模")
            _d = self.depth_of(short)
            if _d is not None:
                _fn = self.fns.get(short)
                _tab = self._param_size_table(_fn, node) if _fn is not None else {}
                _args = self._args_cost(node, env)
                # **修前那一层**（有 target 时就是 `1 + target + 实参`），
                # 取 max ⇒ 任何调用的 κ 都单调不减。
                _old = Cost.add(Cost.num(OP_COST), _args)
                _t = self.target_cost(short)
                if _t is not None:
                    _old = Cost.max(_old, Cost.add(
                        Cost.add(Cost.num(OP_COST), _t.substitute_sizes(_tab)), _args))
                _once = self._rec_once(short).substitute_sizes(_tab)
                _deep = Cost.add(Cost.add(Cost.num(OP_COST), _args),
                                 Cost.mul(Cost.num(_d), _once))
                return Cost.max(_old, _deep)

        # ① 有 target → 用 target（用户的第 4 条）
        t = self.target_cost(short)
        # **G1：承诺没建立就不能被信任。**
        # 被调函数的 κ 里若还留着未解释的符号（`k(f)` 这种），它的 target
        # 本身就是悬空的（实测 `apply` 的 target 是 O(n)，但传进 O(n²) 的函数就不成立）。
        # 这时调用方**不能**拿它的 target 当上界——退回 ⊤，让别人看到"算不出"。
        if t is not None:
            try:
                _ck = self.cost_of(short)
            except Exception:
                _ck = None
            if _ck is not None and _has_unresolved_sym(_ck):
                t = None
        if t is not None:
            # target 也是用**被调函数的形参名**写的，同样要按实参实例化。
            # 注意：此处 `fn` 还没赋值（内联分支才赋值），要自己取一次。
            _fn = self.fns.get(short)
            _tab = self._param_size_table(_fn, node) if _fn is not None else {}
            # **实参要算**：`f(compute(xs))` 的 `compute(xs)` 是**调用方**求值的。
            # target 描述的是**被调函数自己**的成本（用形参尺寸写），
            # 里面不含调用方的实参表达式 ⇒ 两者相加**不是**双重计费。
            _one = Cost.add(Cost.num(OP_COST),
                            Cost.add(t.substitute_sizes(_tab),
                                     self._args_cost(node, env)))
            # **①-b 递归函数的调用点**：target 是**一层**的承诺；声明的
            # `recursion depth <= N` 意味着这一次调用最多压 N+1 层。
            # 只乘 target 会留下「一层」的窟窿（调用方 ops > κ），所以乘
            # 「函数体只算一次」的 B0（比 target 更贴实现），并取 max 保持单调。
            _d = self.depth_of(short)
            if _d is not None and _fn is not None:
                _once = self._rec_once(short).substitute_sizes(_tab)
                _deep = Cost.add(Cost.num(OP_COST),
                                 Cost.add(Cost.mul(Cost.num(_d + 1), _once),
                                          self._args_cost(node, env)))
                return Cost.max(_one, _deep)
            return _one

        # ② 没有 target → 内联
        fn = self.fns.get(short)
        if fn is None:
            # **E18**：这里假定「被调函数体成本 = 0」，所以记的是 `1 + 实参`。
            # 对被调内部一无所知时，**这不是上界**——它可能跑 10⁶ 步。
            # 检测放在 `dsh.py::_unknown_calls`（静态扫一遍），因为这条路上的
            # 实例可能来自缓存，记账不一定落在报告用的那个对象上。
            # 内置/外部函数：记 `1 + 实参求值`，不猜被调内部。
            # **函数值调用 `g(x)` 也在这里**（`g: func(A)->R` 不在 `self.fns` 里）：
            # 解释器 `_call` 的 FnVal 分支照样先 `_args` 求值再派发，
            # 所以实参这一步是可静态算的、必须算；被调函数体的成本按语言约定
            # 由**调用方**用 `k(g)` 保证（见 `for_each` 的同一约定），不在这里猜。
            return Cost.add(Cost.num(OP_COST), self._args_cost(node, env))

        if short in self.inlining:
            raise CostError(
                f"内联 {short} 时成环（{ ' → '.join(self.inlining + [short]) }）；"
                f"给它补一个 target，或检查是否递归")
        # 内联：实参、尺寸替换、函数值绑定、`1 + 被调体 + Σ实参` 都在这里
        return self._inline_body(fn, node, env)

    def candidate_costs(self, label: str,
                        env: Dict[str, Cost]) -> Dict[str, Cost]:
        """`?!label` 的每个候选各自的 κ（不取 max）。

        **给静态分段建议用**（`elench cost` 报交点）：承诺要的是"最坏的那个"，
        而"挑哪个跑"要的是"每个候选各多大"——后者不能从 max 里还原出来。
        """
        out: Dict[str, Cost] = {}
        for kern in _descendants(self.mod, "kernels"):
            if (kern.name or "") != label:
                continue
            for cand in kern.children:
                if cand.kind != "candidate":
                    continue
                c = Cost.num(0)
                for b in [x for x in cand.children if x.kind == "block"]:
                    c = Cost.add(c, self._block(b, dict(env)))
                out[cand.name or ""] = c
        return out

    # ---------------------------------------------------------------- 副作用

    def _effects_of(self, name: str):
        """某个被调函数声明了哪些 effects。"""
        sp = self.specs.get(name)
        if sp is not None:
            body = next((c for c in sp.children if c.kind == "spec-body"), None)
            for cl in (body.children if body else []):
                if cl.kind == "clause" and (cl.name or "") == "effects":
                    v = cl.value
                    if v in (None, "pure"):
                        return []
                    return list(v) if isinstance(v, (list, tuple)) else [str(v)]
        ex = self.externs.get(name) if hasattr(self, "externs") else None
        if ex is not None:
            ee = next((c for c in ex.children if c.kind == "extern-effects"), None)
            return [c.name for c in (ee.children if ee else [])
                    if c.kind == "effect-name" and c.name]
        return []

    def effect_attribution(self, fn_name: str) -> Dict[str, Any]:
        """**按 effect 统计**：每个 effect 有几个调用点、分别调了谁；κ 里有哪些 `ext` 项。

        **不做的**：把解释器操作按 effect 分摊。一次解释器操作不属于任何副作用，
        硬分（"40% 算 fs、60% 算 io"）就是编数。这里只统计**可指认的**部分：
        调用点（谁声明了 effect）与外部项（`ext(名字)`）。
        """
        out: Dict[str, Any] = {"effects": {}, "ext_terms": [], "declared": []}
        out["declared"] = list(self._effects_of(fn_name))
        fn = self.fns.get(fn_name)
        if fn is None:
            return out
        for call in _descendants(fn, "call"):
            nm = (call.name or "").split(".")[-1]
            effs = self._effects_of(nm)
            for e in effs:
                slot = out["effects"].setdefault(e, {"sites": 0, "callees": []})
                slot["sites"] += 1
                if nm not in slot["callees"]:
                    slot["callees"].append(nm)
        try:
            k = self.cost_of(fn_name)
        except Exception:                                  # noqa: BLE001
            k = None
        if k is not None:
            _collect_ext(k, out["ext_terms"])
        out["ext_terms"] = sorted(set(out["ext_terms"]))
        return out

    def _scale_span(self):
        """从 `.tu` 的 `benchmodel.scale` 取 (尺寸变量名, 下限, 上限)。"""
        if self.decl is None:
            return None, None, None
        try:
            from checker import _eval_range_points
        except Exception:                              # noqa: BLE001
            return None, None, None
        for bm in _descendants(self.decl, "benchmodel"):
            for sb in _descendants(bm, "scale-binding"):
                pts, _err = _eval_range_points(sb.children[0] if sb.children else None)
                if pts:
                    return sb.name, pts[0], pts[-1]
        return None, None, None

    def _over_order(self, label: str) -> List[str]:
        """`over { ... }` 里的候选顺序——**与运行期退回候选时用的顺序必须一致**。

        以前承诺里的"越界"分支取的是 `cands` 的**定义顺序**（`.eln` 里谁先写），
        而运行期退的是 `over` 的顺序。两份顺序一旦不同，越界时承诺与运行期
        就不是同一个候选——那正是 S2 同源要防的事。
        """
        if self.decl is None:
            return []
        for ch in _descendants(self.decl, "choose"):
            if (ch.name or "") != label:
                continue
            for cl in ch.children:
                if (getattr(cl, "kind", "") == "choose-clause"
                        and getattr(cl, "name", "") == "over"):
                    return list((cl.value or {}).get("names", []))
        return []

    def _dispatch_inner(self, node: Node, env: Dict[str, Cost]) -> Cost:
        """`?!L(...)`：**承诺 = 分段函数**（每段用它自己那个候选的符号式）。

        ## 为什么是分段，而不是 max

        `max` 必须对**所有**候选成立，于是被最慢候选的阶绑住：`count` 的
        `O(n²)` 候选只在 n≤12 跑，却把整条线抬成 `O(n²)`。

        ## 成立的条件（不成立就别用）

        分段只等于"这一段真正会跑的那个候选"的 κ。所以它**要求运行期按同一份
        分段选候选**——这也是为什么分段由 `segment_plan.plan()`（静态解交点）生成，
        而不是读测量出来的分档表：两次不同的计算会给出两个不同的分区。

        越界（n 超出声明区间）用 `fallback`，那正是运行期"表覆盖不到、退回第一个
        候选"的行为；**所以它仍然是上界**，只是不紧。

        没有 `benchmodel.scale`（不知道区间）或只有 1 个候选 → 退回 `max`。
        """
        label = node.name or ""
        cands: Dict[str, Cost] = self.candidate_costs(label, env)

        # **分段只按"运行期真正会用的那份分档表"来切**（S2：同源）。
        #
        # 不能用 `plan()`（静态交点）来切：静态几何与运行期选择是**两次计算**，
        # 一旦不一致，承诺就按 A 段算、运行跑 B 候选 → 破界。实测过一次：
        # `search.binary_search` 用静态分段切出 [7,…] 的 log 候选，而运行期没有表、
        # 退回第一个候选（线性扫描），余量变成 **-341**（κ 小于实测）。
        #
        # 没有表 → 运行期"退化为第一个实现"，那是一个**单点行为**，我们不知道
        # 它一定跑哪个，所以退回 `max`（sound，松一点）。
        if self.explain and cands:
            # `?!` 的逐候选分解：我们最独特的一块。**只存不判**。
            self.dispatch_cands[label] = dict(cands)
        segs_tbl = self.table.get(label) or []
        if len(cands) >= 2 and segs_tbl:
            try:
                var, lo, hi = self._scale_span()
                segs = [(a, b, cands[nm]) for a, b, nm in segs_tbl if nm in cands]
                if var and segs:
                    # **越界时跑哪个，由 `over` 的顺序定，不是定义顺序**：
                    #   低于下界 → 第一个候选；高于上界 → 最后一个候选。
                    # 用两段哨兵表达，而不是给 `Cost.piece` 加第二个 fallback
                    # （加一个 Cost 形状要动九处，见 `能力索引` §0）。
                    # 运行期 `interp._dispatch` 按**同一条规则**退回，两处必须一致。
                    # **越界 = 沿最近的那一段延伸**，用的是**段上的候选**，
                    # 不是 `over` 的顺序。为什么：段表已经告诉我们"在区间顶端谁更快"，
                    # 而 `over` 的顺序只是书写顺序。反例（实测）：`count` 的 `over`
                    # 最后一个是 `count_by_scan`（O(n²)），但顶端赢的是线性的那个——
                    # 若按书写顺序取，n>4096 的承诺会被抬成 O(n²)，凭空变松。
                    lo_fb = segs[0][2]
                    hi_fb = segs[-1][2]
                    from symbolic import PIECE_BIG as _BIG
                    segs = ([(segs[0][0] - _BIG, segs[0][0] - 1, lo_fb)]
                            + segs
                            + [(segs[-1][1] + 1, segs[-1][1] + _BIG, hi_fb)])
                    return Cost.add(Cost.num(OP_COST),
                                    Cost.piece(var, segs, lo_fb))
            except Exception:                          # noqa: BLE001
                pass

        best: Optional[Cost] = None
        for c in cands.values():
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
    # **过 canon**：改名期 `Vec` 与 `vector` 必须判成同一个东西。
    # 写死旧名会让 `const vector<u64> r = ...` 不被当成容器，
    # κ 少算 1 个操作——**改名静默改成本**，正是这条路线的最大风险。
    return canon(ty.name) in ("vector", "array")


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
                              capacities=None, const_names=None, cur_fn=None):
    """把 env 里的两件事告诉 G29 的提取器：

      · 初值为 0 的变量（决定上界写成 `e` 还是 `e - i`）
      · 尺寸别名（`const n = scores.length();` → 上界里出现 n 时换成 scores.length()）
    """
    from .bound import (bound_of_condition, monotone_decreasing, substitute,
                        progresses, _log_loop)
    # **L-01**：每轮 ×2 / ÷2 的循环要走对数界，不能走一般推导（那会给出 O(n)）。
    _lg = _log_loop(st, cur_fn)
    if _lg is not None:
        return _lg
    from .bound import _cyclic_loop, _gcd_loop
    _cy = _cyclic_loop(st, cur_fn)
    if _cy is not None:
        return _cy
    _gd = _gcd_loop(st, cur_fn)
    if _gd is not None:
        return _gd
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
    # **过 canon**：`FixedVec` 与 `array` 必须同判。写死旧名会让改名后的
    # 定容局部丢掉容量（κ 从常数变回符号，`reduce` 实测 6717 → 一大串符号）。
    if tn is None or canon(tn.name) != "array":
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
    # **过 canon**（2026-11-05）：改名期 `Vec` 与 `vector` 必须同判。
    # 写死旧名会让 `vector<u64>` 的析构成本变 0 —— 那是**改名静默改成本**，
    # 也是此前翻数据时 κ 差 1 的真正来源（`Vec` 记 1、`vector` 记 0）。
    names = [canon(n) for n in names if n]
    if names and names[0] == "array":
        return Cost.num(0)
    vecs = sum(1 for n in names if n == "vector")
    if vecs == 0:
        return Cost.num(0)
    if vecs >= 2:
        return Cost.add(Cost.num(1), Cost.size(name))
    return Cost.num(1)

def clean_bound_inst(b, cur_fn=None, env_sizes=None, row_of=None,
                     capacities=None):
    """`_clean_bound` 的实例级版本：当前函数由参数传入，不读 `_CUR_FN`。"""
    from .bound import substitute, const_bindings
    if b is None:
        return None
    if cur_fn is not None:
        b = substitute(b, const_bindings(cur_fn))
    b = _substitute_size(b, row_of or {})
    b = _substitute_capacity(b, capacities or {})
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


def _collect_ext(c, acc) -> None:
    """把 κ 里所有 `ext(名字)` 项收集出来（含分段节点内部）。"""
    if c is None:
        return
    if c.kind == "ext":
        acc.append(str(c.value))
    if c.kind == "piece":
        for _lo, _hi, sub in c.value[1]:
            _collect_ext(sub, acc)
    for a in getattr(c, "args", ()):
        _collect_ext(a, acc)


def _has_unresolved_sym(c) -> bool:
    """成本式里有没有未解释的符号（`k(f)`）。"""
    if c is None:
        return False
    if getattr(c, "kind", "") == "sym":
        return True
    for a in (getattr(c, "args", None) or []):
        if _has_unresolved_sym(a):
            return True
    return False
