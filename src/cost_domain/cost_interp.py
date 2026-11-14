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
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from .bound import (Bound, bound_of_while, expr_to_bound, is_count_var,
                    predicate_call_of, predicate_cost_node,
                    field_chain_name, size_receiver_name, elem_receiver_name,
                    _descendants, FIELD_TAG, ELEM_TAG, nonnegative_vars)
from parser import Node
from typenames import canon
from .symbolic import Cost, _compound
from ast_util import descendants as _descendants
from ast_util import arms_of, match_scrutinee, arm_pattern, arm_block, arm_single_stmts
from ast_util import stmt_body, while_cond, for_iterable
from ast_util import const_int_value as _const_int_value
from kinds import COST_EXPR, COST_STMT

# 基本操作的单位成本。G26 定了就换表，这里只放一个口径。
OP_COST = 1

# **局部容器的尺寸归并**（E23 续，2026-11-08）。
# 只有这些类型才在成本域里带 `n(名字)`；名字必须是**输入尺寸量**才允许进 target。
#: **`HashMap` 也在里面**（2026-11-11，κ 低估审计 ②）：它以前只被当成
#: 「外来名字」，于是 `map_put(h, …)` 的尺寸项写成了 `n(h)` 却查不到界——
#: 有 target 的函数会从「达成」掉成 E23「尺寸量对不上」（**判定变坏**）。
#: 把它登记成容器后，增长预扫能按 `map_put` 的轮数推出 `n(h)`，
#: 与语言 `map` 的 `n(m)` 走同一条路。
_CONTAINER_TYPES = ("vector", "array", "map", "set",
                    "unordered_map", "unordered_set", "deque",
                    "HashMap", "hashmap")

#: **返回空容器的构造调用**（`map<…>::new()` / `map_new()` / `set_new()` …）。
#:
#: 这张表以前只装"裸名构造"（`new` / `make` / …），而 `map_new` / `set_new`
#: 被写在**内层**的第二个判断里——永远进不去 ⇒ `HashMap h = map_new();`
#: 的初始长度上界是 `None`（推不出）。以前没影响（`map_put` 记常数，不查
#: 尺寸）；2026-11-11 把它登记成 `log2(n(h))` 之后，这条就变成 E23/⊤ 了。
#: 两处判断合并成一张表，避免第三次漂移。
_EMPTY_CTOR_SHORTS = ("new", "make", "empty", "create", "with_capacity",
                      "map_new", "set_new", "vec_new", "deque_new")

# 会**增长**容器的方法名（每个调用最多写入 1 个元素）。
# 与 E36 的分配口径同一张表，避免两处漂移。
_GROWTH_METHODS = ("push", "insert", "push_back", "push_front",
                   "append", "enqueue", "add", "put",
                   "emplace", "emplace_back")

# 已知**不增长**的方法。局部容器的尺寸上界只有在这张表里才敢继续用；
# 其余方法（可能 `merge` / `swap` / 自定义扩展）保守判「推不出」。
_SAFE_METHODS = ("get", "contains", "has", "contains_key", "keys", "values",
                 "items", "entries", "iter", "begin", "end",
                 "length", "size", "len", "capacity", "empty", "is_empty",
                 "at", "index", "front", "back", "first", "last",
                 "find", "count", "count_of", "exists", "peek", "top",
                 "pop", "pop_back", "pop_front", "remove", "erase", "clear",
                 "sort", "sort_by", "reverse", "fill", "fill_range", "swap",
                 "data", "as_slice", "clone", "copy", "to_vec", "slice",
                 "is_sorted", "sum_of", "min_of", "max_of")

#: **元素长度** `L(x)` 预扫专用（2026-11-12 晚）：往**内层容器**里追加一个元素的方法。
#: 与 `_GROWTH_METHODS` 的差别是这里只收「一次调用加 **1** 个元素」的那几个；
#: `append` 可能加一批，单独处理（按 `n(实参)` 累加）。
_ELEM_PUSH_METHODS = ("push_back", "push", "emplace_back", "emplace", "add",
                      "enqueue", "push_front")

#: **元素长度**预扫里允许的「不改行长度」的方法。相对 `_SAFE_METHODS` 去掉了
#: `swap`（两个容器的行可以互换，长度会变）与 `fill`（可能把某行换成长度未知的
#: 值），其余只读 / 变短 / 重排的操作都保留（`pop` / `clear` / `sort` 只会让 L 变小）。
#: **另补 `c_str` / `cdata`**（2026-11-12 夜）：`ks[idx].c_str()` 是**只读**的
#: 取指针操作，不改元素长度。不补的话 `02_csv_stats` / `08_bits_hash` 的
#: `printf("%s", ks[idx].c_str())` 会把刚推出来的 `L(ks)` 抹成 ⊤。
_ELEM_SAFE_METHODS = tuple(m for m in _SAFE_METHODS
                           if m not in ("swap", "fill", "fill_range")) + (
    "c_str", "cdata")

# 内建的增长调用：第一个实参是容器（`map_put(cnt, k, v)`）。
_GROWTH_BUILTINS = ("map_put", "hmap_put", "set_add", "vec_push",
                    "deque_push_back", "deque_push_front")

#: **计数表**容器（规则④）：`map` / `set` 及它们的 hash 版、内建 `HashMap`。
#:
#: 为什么不复用 `_ASSOC_HEADS` / `_SET_HEADS`：那两张表是**值语义分类**
#: （拷贝/借用口径），这里是"能不能把 `n(局部)` 归约到**键源**的尺寸"的判定集合
#: ——两件事，混用会让其中一条口径漂。
_COUNTING_TABLE_KINDS = ("map", "unordered_map", "set", "unordered_set",
                         "HashMap", "hashmap")

#: **派生容器**（2026-11-12）：结果容器的长度**至多与源容器一样长**。
#:
#: `ks = m.keys()` 每个键一条、`vs = m.values()` 每个值一条、
#: `items()/entries()/iter()` 每条目一条。这是**逐元素对应**，不是"名字像"：
#: 源容器有 n 个元素，派生结果不可能超过 n 条——所以 `|派生| ≤ |源|` 是可证的，
#: 把 κ 里的 `n(ks)` 换成 `n(m)` 是**放大**（仍是上界）。
_DERIVED_SIZE_SHORTS = ("keys", "values", "items", "entries", "iter",
                        "clone", "copy", "to_vec")

#: **切分族**：`|pieces| ≤ n(源) + 1`（每个分隔符处最多切一刀，段数 ≤ 分隔符数+1，
#: 而分隔符数 ≤ 源长度）。源可以是 `vector<u8>` / `string`（本语言里都是
#: 可数长度的序列）。用户自定义同名函数优先（见 `self.fns` 判断）。
_SPLIT_SIZE_SHORTS = ("split", "text_split", "text_split_vec",
                      "split_lines", "text_lines")

#: **`_call_inner` 里特判的内建**（不在 `stdlib.FNS` 里）：`print` / `min` /
#: `max` / `swap` 不改实参的**长度**；`sort`/`sort_by`/`push_heap`/`pop_heap`
#: 只重排。长度上界只在"可能变长"时才失效，所以这些都算"不变长"。
#:
#: **派发顺序的规则（2026-11-10，D2 连带）**：**用户函数优先于内建**——
#: 与解释器 `_call` 一致（interp.py:3914-3919 的注释记着这条修过的顺序）。
#: 成本域里凡是"按内建名字记账"的地方（`_call_inner` 的内建分支、
#: `_alloc_expr` 的内建分配表、`_call_may_grow_arg`）都必须先看
#: `self.fns`；否则用户写的 `dedup` 会被当成内建 `dedup`，得到**假上界**
#: （`_diff/dedup::main`：κ=107 < 实测 ops=1005）。
#: **例外**：`print` / `swap` 是解释器在用户函数**之前**特判的，用户盖不住。
#: `_probe/d2d4/shadow_builtins.py` 对 105 个内建名逐个验证过这条规则。
_SPECIAL_BUILTINS = frozenset({
    "print", "min", "max", "swap", "sort", "sort_by", "push_heap", "pop_heap",
    "for_each", "map", "filter", "fold", "count_if",
})

#: **真的会改长度**的特别内建（2026-11-13，规则⑥的筛子）。
#:
#: `_SPECIAL_BUILTINS` 的注释说"sort / push_heap / pop_heap 只重排"——那是
#: **符号口径**下的记账选择（长度上界只在"可能变长"时才失效，而这三者不进入
#: 增长预扫的账）。但 `push_heap(h, x)` 在解释器里**真的**往 h 里加一个元素、
#: `pop_heap(h)` 真的减一个，`swap(a, b)` 真的把两个容器的长度互换。
#:
#: 这个区别只在**换数**时才要命：把 `n(h)` 换成预扫给的界（对 h 是 0），
#: 换完就是**下界**了。所以"有界就代"必须是"界真的覆盖了所有写入才代"，
#: 这三者（以及 `_UNMODELLED_SIZE_METHODS` 里的方法）一律挡在门外。
_LENGTH_CHANGING_SPECIALS = frozenset({"push_heap", "pop_heap", "swap"})

#: **不改实参长度**的特别内建：`_SPECIAL_BUILTINS` 里除上面三个之外的（`print`
#: 打完就返回、`sort`/`sort_by` 重排、HOF 只读源容器）。规则⑥只放行这些。
_LENGTH_PRESERVING_SPECIALS = frozenset(
    set(_SPECIAL_BUILTINS) - set(_LENGTH_CHANGING_SPECIALS))

#: **不在 `_SAFE_METHODS` 里信任**的方法（规则⑥的筛子）。
#:
#: `_SAFE_METHODS` 是为"长度上界不失效"选的口径，`swap` / `fill` 在里面；
#: 但代入 κ 需要的是"**写入次数被预扫算进了界**"，这两条不满足：
#:   · `swap` 把两个容器的长度互换（可能变长）；
#:   · `fill` / `fill_range` 可能把容器换成另一个长度。
#: 另补几个显然会改长度的（`resize` / `assign` / `merge` / `splice` / `reserve`），
#: 它们本来就不在 `_SAFE_METHODS` 里，列在这里只是让判据读起来完整。
_UNMODELLED_SIZE_METHODS = frozenset({
    "swap", "fill", "fill_range", "resize", "assign", "merge", "splice",
    "reserve", "shrink_to_fit",
})


def _container_type_name(ty: Optional[Node]) -> Optional[str]:
    """声明/形参类型是不是容器；是则返回 canon 后的类型名。"""
    if ty is None:
        return None
    if getattr(ty, "kind", "") == "type-ref" and getattr(ty, "children", None):
        ty = ty.children[0]
    if getattr(ty, "kind", "") != "type-name":
        return None
    nm = canon(getattr(ty, "name", "") or "")
    return nm if nm in _CONTAINER_TYPES else None


def _callee_short(n: Optional[Node]) -> str:
    """调用的短名；`map<...>::new()` 的短名在 `field` 子节点上。"""
    if n is None:
        return ""
    nm = (getattr(n, "name", "") or "").split(".")[-1]
    if nm:
        return nm
    for c in (getattr(n, "children", None) or []):
        if getattr(c, "kind", "") == "field" and getattr(c, "name", ""):
            return (c.name or "").split(".")[-1]
    return ""


def _is_qualified_call(n: Optional[Node]) -> bool:
    """`Type<...>::make()` 这种**限定名构造**（不是用户函数 `make(xs)`）。"""
    return any(getattr(c, "kind", "") == "field"
               for c in (getattr(n, "children", None) or []))


def _has_top(c: Optional["Cost"]) -> bool:
    """**任意深度**上有 ⊤ 吗（`Cost.is_top()` 只看根节点）。

    为什么需要它：`Cost.mul(Cost.top(), Cost.num(84))` 的 `kind` 是 `mul`，
    `is_top()` 为 False。而 `compare()` 判断"κ 是 ⊤"用的正是 `is_top()`；
    漏掉之后它会以为 κ 是个普通式子，可能在"κ 不含尺寸量 ⇒ 常数"那一支
    用 `evaluate`（⊤ → inf）判出一个**假的达成**。
    """
    if c is None:
        return False
    if c.kind == "top":
        return True
    if c.kind == "piece":
        _var, segs = c.value
        if any(_has_top(s) for _lo, _hi, s in segs):
            return True
    return any(_has_top(a) for a in getattr(c, "args", ()))


# ---------------------------------------------------------------- 值语义（A-full）
#
# 口径来自 `文档/设计/值语义-A-full-设计.md` §3–§6（C++17 值语义）。一句话：
# **只有真会产生新对象的地方才记拷贝**——
#   · prvalue（`{…}` / `S{…}` / `T::make()` / 用户调用返回的新对象）→ 强制省略，0
#   · `return` 非 const 局部 / 按值形参 → 隐式移动（容器 O(1)、array/struct 逐成员）
#   · 其余消费（初始化 / 赋值 / 按值传参 / 入容器 / 返回借用值 / `for` 元素）→ 拷贝
#
# **判定只写这一份**（`_copy_class` / `_value_class` / `_type_of_expr`）：
# 将来的 C++ 后端与 checker 要复用时从这里搬，别各写一套（`kinds.py` 记了 4 次漂移史）。
#
# **上界口径（硬约束）**：只加不减；尺寸写不出来 → ⊤（不猜）。
# **标量/枚举的拷贝记 0**：这是设计 §9.2 影子模型的口径，也是"golden 只变 4 个"的前提。
# 记 1 会让**每个有标量形参/局部赋值的函数**都变——那是把"表达式节点各记 1"
# 再乘一遍。这是一处**登记在案的常数级低估**（与 §10.8 的 array-N 解析失败同类）。
_SEQ_HEADS = ("vector", "stack", "queue", "deque")
_ASSOC_HEADS = ("map", "unordered_map")
_SET_HEADS = ("set", "unordered_set")
_ARR_HEADS = ("array",)
_OWNING_HEADS = _SEQ_HEADS + _ASSOC_HEADS + _SET_HEADS

#: 把元素**移出**容器、结果归调用方独占的方法（prvalue）。
_MOVE_METHODS = ("pop", "dequeue", "remove")
#: 返回容器**内部元素**（别名）的方法。
_BORROW_METHODS = ("at", "get", "front", "back", "top", "peek", "first", "last")
#: 返回**新容器**的方法（视图物化 → prvalue）。
_VIEW_METHODS = ("keys", "values", "items", "entries", "iter")

#: 返回**新容器**的内建（`stdlib.ret_kind` 判定，不是手抄名单）。
_FRESH_RET_KINDS = frozenset({"vec-u8", "vec-u64", "vec-vec-u8",
                              "result-vec-u8", "result-vec-u64",
                              "option-vec-u8"})

#: 会**增长**容器的方法（入容器 = 一个元素拷贝点）。
#: **`assign` 也在里面**（2026-11-13）：`v.assign(n, x)` 是 n 次拷贝构造。
#: 以前不在表里 ⇒ 元素是 owning 容器时那 n 份拷贝一个都没进 κ；`insert` 的
#: 数量项（`_method_count_cost` × 单次拷贝）要靠这里才乘得起来。
_STORE_METHODS = ("push", "insert", "push_back", "push_front",
                  "append", "enqueue", "add", "emplace", "emplace_back",
                  "assign")


def _ret_type_of(fn: Optional[Node]) -> Optional[Node]:
    """函数的返回类型节点（`-> T`；`T!` 的失败标记在 `fn.value`，不在类型里）。"""
    if fn is None:
        return None
    for c in (fn.children or []):
        k = getattr(c, "kind", "")
        if k in ("type-name", "type-ref", "type-fail"):
            return c
        if k == "block":
            break
    return None


def _is_ref_type(ty: Optional[Node]) -> bool:
    """`T&` / `const T&` → 引用（别名，零拷贝）。判据见设计 §3.4。"""
    return getattr(ty, "kind", "") == "type-ref"


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


def _paren_inner(n: Optional[Node]) -> Optional[Node]:
    """剥掉 `( … )`（可嵌套）：判**形状**时括号不改变含义。

    `mid = (lo + hi) / 2` 与 `mid = lo + ((hi - lo) / 2)` 在 AST 里差的只是
    几个 `paren` 节点；不剥的话「等价写法 κ 逐字节相同」这条断言会假失败。
    """
    while n is not None and getattr(n, "kind", "") == "paren" and n.children:
        n = n.children[0]
    return n


def _walk_all(n: Optional[Node]):
    """深度优先遍历**所有**节点（含 `n` 自己）。判「这个名字还出现过吗」用。"""
    if n is None:
        return
    yield n
    for c in (getattr(n, "children", None) or []):
        for x in _walk_all(c):
            yield x


# ---------------------------------------------------------------- 索引追逐循环
#
# 「沿容器里的指针一路走」是并查集 / 链表 / 置换的**标准写法**：
#
#     while (C[x] != x) { x = C[x]; }          // 找根 / 走链
#
# 它没有计数器，`bound_of_condition` 的 `i < e` 形状一个都不沾，以前一律 ⊤。
# 但仓库**早就有**同一条口径（`文档/语法清单.md`）：
#
#   · `κ` 界的是**会终止的那些执行**（「`κ` 的口径：对终止执行的上界」一节）；
#   · 「良定义执行下圈数 ≤ n(C)——`i >= n(C)` 的那一次读就越界」也是同一条。
#
# 在「执行会终止」这个前提下，追逐走过的下标**互不相同**：
# 若 `x_i == x_j`（`i < j`），因为 `C` 在体里不变（或只被写回成守卫的另一侧），
# 第 `i` 轮与第 `j` 轮的守卫会给出**同一个结论**，于是轨迹进入周期、永不退出——
# 与「会终止」矛盾。走过的下标都在 `[0, n(C))`（守卫每轮都读 `C[x]`，良定义执行
# 不许越界），所以**圈数 ≤ n(C)**。
#
# 这不是新假设，是项目已写明的口径；代价是这条上界**只在终止执行上成立**，
# 与「纯结构推导」不同——`_while` 的 `--explain` 注记会把它标出来。

#: 允许「被追逐的量」与容器元素的整数类型头。**只收无符号**：
#: `i64` 可能为负，`C[-1]` 的越界不是「良定义」，不能拿 n(C) 当上界。
_CHASE_UNSIGNED = frozenset({"u8", "u16", "u32", "u64", "u128", "usize"})

#: 追逐体里**一出现就拒**的节点（只认「直着走一步」）：控制流会打断
#: 「每轮都推进 x」的天真推理，宁可 ⊤。
_CHASE_FORBIDDEN = ("break", "continue", "return", "while", "for", "match",
                    "parallel", "if")


def _chase_same_expr(a: Optional[Node], b: Optional[Node]) -> bool:
    """两棵表达式树**结构相同**（括号不算）。判「守卫另一侧 == 写回的值」用。"""
    a, b = _paren_inner(a), _paren_inner(b)
    if a is None or b is None:
        return a is b
    if getattr(a, "kind", "") != getattr(b, "kind", ""):
        return False
    if (getattr(a, "name", None) or "") != (getattr(b, "name", None) or ""):
        return False
    if str(getattr(a, "value", "")) != str(getattr(b, "value", "")):
        return False
    ca = list(getattr(a, "children", None) or [])
    cb = list(getattr(b, "children", None) or [])
    return len(ca) == len(cb) and all(_chase_same_expr(x, y)
                                      for x, y in zip(ca, cb))


def _chase_lvalue_chain(lhs: Optional[Node]) -> Optional[str]:
    """左值的**纯字段链名**（先把下标剥掉）：`d.parent[x]` → `d.parent`。"""
    while lhs is not None and getattr(lhs, "kind", "") in (
            "index", "paren", "try", "deref", "ref"):
        lhs = lhs.children[0] if lhs.children else None
    return field_chain_name(lhs)


def _chase_names(n: Optional[Node]) -> set:
    """表达式里出现的**裸变量名**。"""
    return {(p.name or "") for p in _descendants(n, "path")}




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
    if b.kind == "ceildiv":
        # 定容局部出现在分子里（`while (i + 3 <= N)`，`N` 是 `array<u64, 16>`
        # 的容量）时也要换成常数，否则界里留着 `N.length()` 这种查不到的尺寸量。
        return Bound("ceildiv", value=b.value,
                     left=_substitute_capacity(b.left, caps))
    return b
def as_cost(n: Optional[Node], const_sizes: Dict[str, Cost],
            capacities: Optional[Dict[str, int]] = None) -> Cost:
    """表达式在成本域里的**值**（这个名字等于什么）；认不出 → **⊤，绝不是 0**。

    返回值只有一处用途：**这个局部名字的值**（`env`），随后被
    `bound_of_condition_wrapper` 的替换表 / 增长预扫当成已知量用。
    初始化表达式本身的**成本**由 `self._expr` 另算，不经过这里。

    由此推出两条硬约束：

    ① **绝不折成常数**（2026-11-09 修）。`const u64 total = a.length() +
       b.length(); while (x < total)` 曾经记成 `num(0)` —— 上界被替换成 0，
       κ 变成 23 那种小常数，而真实轮数是 `n(a)+n(b)`。**写了 const 反而比
       不写 const 更不保守**（不写时 `expr_to_bound` 拒收 `+`，退回 ⊤）。
       现在 `+` / `*` 按结构造 `Cost`，`-` / `/` / `%` 取左操作数（无符号，
       左操作数就是上界）。

    ② **认不出就 ⊤**。`const u64 k = xs.at(0);` 这种形状与尺寸无关，
       记 0 会让 `while (i < k)` 的轮数变成 0；记 ⊤ 只让判定退化成"无法判定"，
       方向是**保守**的。宁可 ⊤，不许 0。

    历史：整数字面量曾经也记 0（同一条"值不参与上界"的旧口径），
    `const u64 n = 1000` 的轮数因此变成 0 —— κ=22 / ops=45。
    """
    if n is None:
        return Cost.num(0)
    if n.kind == "dispatch":
        return Cost.num(0)      # 具体成本由 _dispatch 记，这里只是绑定值
    if n.kind == "lit-int":
        v = _int_literal(n)
        return Cost.num(0 if v is None else v)
    if n.kind == "paren" and getattr(n, "children", None):
        return as_cost(n.children[0], const_sizes, capacities)
    if n.kind == "path" and n.name in const_sizes:
        return const_sizes[n.name]
    if n.kind == "binary":
        op = n.value or ""
        a = n.children[0] if (n.children or []) else None
        b = n.children[1] if len(n.children or []) > 1 else None
        la = as_cost(a, const_sizes, capacities)
        lb = as_cost(b, const_sizes, capacities)
        if la.is_top() or lb.is_top():
            return Cost.top()
        if op == "+":
            return Cost.add(la, lb)
        if op == "*":
            return Cost.mul(la, lb)
        if op in ("-", "/", "%"):
            # u64 无符号：`a - b ≤ a`、`a / k ≤ a`、`a % b ≤ a`。
            # 拿左操作数当这个**名字的值**，它一定是真实值的上界。
            return la
        return Cost.top()
    if n.kind in ("method-call", "call"):
        fn = (n.name or "").split(".")[-1]
        if fn in SIZE_FNS:
            # **被量对象可以是字段链**（`s.v.size()` / `a.b.length()`）：
            # 它的名字就是可渲染的尺寸符号 `n(s.v)`（2026-11-12）。
            # 推不出（`f().size()` / `xs.at(i).size()`）→ **⊤**，不写 `n(?)`
            # ——`n(?)` 会被 `cost_vars()` 当成一个普通尺寸量，是把"不知道"
            # 伪装成"知道"（D4 的同一口径）。
            tgt = size_receiver_name(n)
            if tgt is None:
                return Cost.top()
            # E22：定容器 → 容量常数，不留局部尺寸名
            if capacities and tgt in capacities:
                return Cost.num(capacities[tgt])
            return Cost.size(tgt)
    # **认不出 → ⊤**（不是 0）：这个返回值是这个**名字的值**，记 0 就是
    # 把它当成"上界 0"用（`while (i < k)` 的轮数会变成 0）。
    return Cost.top()


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


#: **按 `requires` 事实算出来的缓存**（键是函数节点 / 名字，值里含循环上界）。
#: 给被调方临时补事实（`_halving_callee_plan`）时必须**整体快照/还原**：
#: 只还原 `_facts_cache` 会留下"按绝对下标算的局部容器尺寸"（`n(tmp)`），
#: 按层求和那一步又把它乘回激活数 ⇒ O(n²)。
_FACT_SENSITIVE_CACHES = (
    "_facts_cache",
    "_rec_once_cache",
    "_rec_leaf_cache",
    "_bounds_cache",
    "_out_cache",
    "_elem_bounds_cache",
    "_elem_grow_cache",
)


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
        # **target 解析失败登记**（2026-11-09）：`target_cost` 以前对"写了但
        # 认不出"的槽位直接返回 None，调用方把它读成"没写 target"——
        # **契约凭空消失，check 一声不吭**。现在每个失败槽位都记下来：
        # `check` 出 `E-Target-BadForm`、`cost` 打印错误并且不再说"没有 target"。
        self.target_errors: List[Dict[str, object]] = []
        self._target_error_keys: set = set()
        # **`sizes` 绑定的名字**（规格 §7.2 G1-b）：spec 名 → {名字: 右侧表达式}。
        # 名字是 **spec 侧的量**，不是实现里的局部：`sizes table = n + 1;`
        # 只在 target/cost 里把 `table` 当 `n + 1` 的缩写（"替换即可"，见
        # `复查-集中谎言准则.md` §2.2 #14）。实现里叫 `table` 的容器多大，
        # 它既不知道也不检查——所以规格只允许绑**形状量**。
        self._size_binds_cache: Dict[str, Dict[str, Node]] = {}
        self._size_bind_stack: List[str] = []
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
        # `_self_call_shape` 的缓存：函数名 → `(每条路径最多几个自调用点,
        # 是否有自调用落在**轮数上界推不出 ≤ 1** 的循环里)`
        self._rec_shape_cache: Dict[str, tuple] = {}
        # `_tree_activation_bound` 的缓存：函数名 → **激活数上界** `(D+1)·B^D`
        # （④「树递归 + 每层分叉数可推」，2026-11-12）或 None（判据缺一）。
        self._tree_cache: Dict[str, Optional[Cost]] = {}
        # **正在算 ④ 的那一帧**（函数名 / 函数节点）。只在这一帧里，成员界
        # `n(recv.n_)` 走**逐字段**证明（`_tree_field_static`）而不是保守的
        # "对象动过 ⇒ 所有成员都不许当界"。其它路径置 None，行为不变。
        self._tree_frame: Optional[str] = None
        self._tree_fn: Optional[Node] = None
        # `_visited_depth` 的缓存：函数名 → **激活数上界**（`n(标记容器)`）或 None。
        self._visited_cache: Dict[str, Optional[Cost]] = {}
        # `_halving_span` 的缓存：函数名 → `(lo 形参名, hi 形参名)` 或 None。
        # **区间对半分的递归**（`merge_sort`）：一条路径上 2 个自调用点，但两次
        # 调用拿的是**互不相交、各自 ≤ 一半**的子区间 ⇒ 总激活数 ≤ `2 × (hi - lo)`
        # （证明见 `_halving_span` 的注释，不是"假设程序会停"）。
        self._halving_cache: Dict[str, Optional[tuple]] = {}
        # **区间对半分里 `mid` 的局部名**：函数名 → `mid`（或 None）。与
        # `_halving_cache` 同一份判据，只是单独缓存——**按层求和**那一步要认出
        # 「一次调用的实参是哪个区间端点」。
        self._halving_mid_cache: Dict[str, Optional[str]] = {}
        # **`requires` 的临时补充**：函数名 → `{小: {大, …}}`。只在「区间对半分
        # 的帧里调用一个只碰本区间的函数」时用（见 `_halving_callee_plan`）：
        # 那时调用点**证明了**实参是 `lo ≤ mid ≤ hi` 这三个端点，于是被调方
        # 内部 `mid - lo` 这类差值才成立。用完即撤，**不入 `_facts_cache`**。
        self._facts_extra: Dict[str, Dict[str, set]] = {}
        # **尺寸差的来源**：`mid - lo` 这类符号名 → `(被减式, 减式)` 两个 Cost。
        # 调用点要把被调方的差值**按实参实例化**；没有这张表，它只是一个
        # 不认识的名字（求值按 +∞、判定对不上）。
        self._diff_origin: Dict[str, tuple] = {}
        # `_rec_leaf` 的缓存：函数名 → **叶子层**（不做自调用那些路径）的 κ 上界
        self._rec_leaf_cache: Dict[str, Cost] = {}
        # 非 None 时表示正在算叶子层：**这个名字**的自调用点整条切成 0
        self._rec_leaf_root: Optional[str] = None
        # 叶子层的重入保护（间接环）：重入时退回「单层」，不许无限递归
        self._rec_leaf_busy: set = set()
        # **κ 为什么是 ⊤**（函数名 → 一句话）。只用于报告，不参与任何算术：
        # 没有它，`cost` 对树递归只会说「有循环上界推不出」——把递归说成循环，
        # 读的人找不到真正的原因。
        self.top_reasons: Dict[str, str] = {}
        # **索引追逐循环的注记**：`id(while 节点) → 一句话`。只给 `--explain`
        # 侧表用（与 `origins` 同一口径：**不参与任何算术**）。`_while` 取走即删，
        # 同一个节点被反复分析（内联两次）也不会串。
        self._chase_notes: Dict[int, str] = {}
        # 非 None 时表示正在算「函数体只算一次」：**只切这个名字**的自递归，
        # 其它环上函数一律报错（间接递归不猜，见 `_call` 的注释）。
        self._rec_cut_root: Optional[str] = None
        # **函数值形参的绑定栈**（④）：帧 → {形参名: 具名函数名 或 None(仍为符号)}
        self.fnval_stack: List[Dict[str, Optional[str]]] = []
        self.cur_fn: Optional[Node] = None  # 当前正在分析的函数（递减循环分析要用）
        # 局部行别名：`const Vec<u64> row = m.at(i);` → {"row": "m"}
        # 用途：让 `row.length()` 记成 `n(m)`，而不是记成查不到的 `n(row)`。
        self.row_of: Dict[str, str] = {}
        # **当前正在遍历的函数体栈**（`_body` / `alloc_of` 各压一层）。
        # `cur_fn` 只在 `cost_of` 里设一次，内联被调函数时它仍指向**调用者**——
        # 而 `for` 轮数的局部别名表必须按**正被遍历的那个函数**查
        # （同名局部在两个函数里可能指向不同容器，拿错表 = 低估）。
        self._fn_stack: List[Node] = []
        # **`for` 轮数的局部别名表缓存**：id(fn) → (fn, {局部名: 初值节点})。
        # 键里带 `fn` 引用（不只是 id），否则对象回收后 id 复用会指错表。
        self._alias_cache: Dict[int, tuple] = {}
        # **被写过的名字**缓存：id(fn) → (fn, {名字})。给「尺寸别名撤销」用：
        # `u64 m = xs.size(); m = m + 1;` 之后 MUST NOT 再拿 `n(xs)` 当 `i < m` 的界。
        self._written_cache: Dict[int, tuple] = {}
        # **撤销的尺寸别名**缓存：id(fn) → (fn, {名字})。只有「声明即 `.size()`、
        # 之后被改写」的名字才进来（见 `_revoked_size_aliases`）。
        self._revoked_cache: Dict[int, tuple] = {}
        # **成员当循环半径**的守卫缓存：id(fn) → (fn, {被改过的基对象名})。
        # `while (row < recv.n_)` 的上界 `n(recv.n_)` 只在 `n_` 不被改写时成立
        # （见 `_field_bound_blocked`）。
        self._field_block_cache: Dict[int, tuple] = {}
        # **`L(g)` 的前提缓存**：id(fn) → (fn, {被写/被传出去的外层容器名})。
        # `g[v].size()` 的上界 `L(g)` 只在 `g` 是 const 形参、且执行期间不变时
        # 成立（见 `_elem_bound_ok`）。
        self._elem_block_cache: Dict[int, tuple] = {}
        # **局部容器尺寸全表缓存**：id(fn) → (fn, bounds, local_names, params,
        # row_of, caps)。`_container_bounds_full` 用；与 `_alias_cache` 同样的键约定。
        self._bounds_cache: Dict[int, tuple] = {}
        # **正在推导界的函数**（id 集合）：`main` 扫到自己的调用时不再重入。
        self._bounds_busy: set = set()
        # **顺序扫描的偏界上下文**：fn → (bounds, env, caps, row_of, params)。
        self._scan_ctx: List[tuple] = []
        # **返回尺寸的推导链**（`_ret_size_of`）：成环就判推不出，不猜。
        self._size_chain: List[str] = []
        # **返回结构体的字段尺寸链**（`_ret_field_size_of`）：`(函数名, 字段)`，
        # 成环同样判推不出（①，2026-11-12）。
        self._field_size_chain: List[tuple] = []
        # **字段增长预扫的环检测**（①）：键里带函数节点引用（id 复用会指错）。
        self._field_busy: set = set()
        # **「尺寸推不出」的原因**（只用于报告）：按发生顺序记一句话。
        # 最后若某个函数的 κ 真的退成 ⊤（或残留 `n(?)`），把这些原因贴给它。
        self._size_unknown: List[str] = []
        # **κ 里本帧解释不了的符号**（`cost_of` 出口的回归网，2026-11-13）：
        # 函数名 → 名字清单。只登记、**不改变 κ**——理由见 `cost_of` 里那段注释。
        # `tests/cases/test_kappa_no_free_symbol.py` 用它做语料级断言。
        self.kappa_leaks: Dict[str, List[str]] = {}
        # **出参增长**（2026-11-12，P0-1）：`id(fn) → (fn, {形参名: 增长上界/None})`。
        # 与 `_bounds_cache` 同一套键约定（键里带 `fn` 引用，避免 id 复用指错表）。
        self._out_cache: Dict[int, tuple] = {}
        #: **正在推导增长**的函数（id 集合）：出参增长互相依赖成环 → 不猜。
        self._out_busy: set = set()
        #: **出参自己的当前尺寸不许当已知量**（增长口径，`_resolve_local_cost`）。
        self._out_size_block: set = set()
        # **局部容器的「元素长度上界」`L(x)`**（2026-11-12 晚）：
        # `id(fn) → (fn, {名字: 上界/None}, 局部名集合)`。与 `_bounds_cache` 同一套
        # 键约定（键里带 `fn` 引用，避免 id 复用指错表）。
        self._elem_bounds_cache: Dict[int, tuple] = {}
        #: **正在推导元素长度上界**的函数（id 集合）：成环 → 只能给偏界。
        self._elem_bounds_busy: set = set()
        #: 元素长度预扫的**偏界上下文**：`[(fn, eb)]`（重入时回答"到现在为止"）。
        self._elem_scan_ctx: List[tuple] = []
        #: **元素增长**（`g[u].push_back(v)` 对被调方形参的 L）：id(fn) → (fn, {名: 增长}).
        self._elem_grow_cache: Dict[int, tuple] = {}
        #: **正在推导元素增长**的函数（id 集合）：递归 / 互递归 → 不猜。
        self._elem_grow_busy: set = set()
        #: **返回元素长度上界**的推导链（`_ret_elem_of`）：成环判推不出。
        self._elem_ret_chain: List[str] = []
        #: `for (auto& x : <源>)` 的别名表：`id(fn) → (fn, {别名: 源名字})`。
        #: `n(x)`（元素长度）折回 `L(源)` 要用（2026-11-12 夜）。
        self._for_alias_cache: Dict[int, tuple] = {}
        #: 节点 → 它所属的函数（只有"当前帧推不出"时才用；懒建一次）。
        self._owner_index: Dict[int, Node] = {}
        #: **增长预扫里"到这一句为止已被改写的标量名"**（含形参）。
        #: `u64 m = n; m = m * 2; for (i < m) out.push(i);` 的轮数是 `2n`，
        #: 而 `_name_count` 只看**声明处初值** ⇒ 记成 `n`（低估）。预扫是顺序的，
        #: 所以按扫描顺序累积；查到一个被写过的名字就判「推不出」。
        #: **只在预扫期间有效**（`_name_count` 在 κ 遍历里也会被调，那时不设它）。
        self._scan_written: set = set()
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
        # **A-full（值语义拷贝成本）的三张表**：结构体定义 / 函数返回类型 / 帧栈。
        #
        # 之前**完全没有**结构体表 ⇒ `Interval a` 的拷贝成本会漏（设计 §8.1）。
        self.structs: Dict[str, Dict[str, Node]] = {}
        for _m in (decl, mod):
            if _m is None:
                continue
            for _st in _descendants(_m, "struct"):
                if not getattr(_st, "name", None):
                    continue
                self.structs.setdefault(_st.name, {
                    f.name: (f.children[0] if f.children else None)
                    for f in (_st.children or []) if getattr(f, "name", None)})
        # `call` 的返回值类别（prvalue / 借用）与返回类型都要问它。
        self.fn_ret: Dict[str, Optional[Node]] = {}
        for _m in (decl, mod):
            if _m is None:
                continue
            for _fn in _descendants(_m, "fn"):
                if getattr(_fn, "name", None):
                    self.fn_ret.setdefault(_fn.name, _ret_type_of(_fn))
        # **帧栈**：`_body` 压/弹。给「值类别 / 声明类型 / 元素类型」提供局部环境。
        # 只在算 κ / alloc 的遍历期间有效；候选体在 `candidate_costs` 里单独压一帧。
        self._frames: List[Dict[str, object]] = []
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
        # **`requires` 事实缓存**（2026-11-10）：spec 名 → `{小: {大, …}}`。
        # 尺寸差 `hi - lo` 的**非负性**只能靠它证明（见 `prove_le`）。
        # 缓存的是 `facts_of_spec` 的**同一份**结果，不另造一套顺序推理。
        self._facts_cache: Dict[str, Dict[str, set]] = {}
        # **target 认不出时的具体原因**（2026-11-10）：`_target_cost` 可能
        # 知道"为什么"（`lo - hi` 方向证明不了），而通用提示说不清。
        # 这里只转一句人话，不参与判定。
        self._target_hint: Optional[str] = None
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

    def _rec_leaf(self, name: str) -> Cost:
        """**叶子层成本**：函数体里**不做自调用**的那些路径的成本上界。

        为什么需要它（P0-R，2026-11-12）：线性递归的真成本是

            T(D) = D × (单层) + 叶子层

        其中「叶子层」是**最后一层**：它走的是 `if (n == 0) { return … }` 那一支，
        不再付一次自调用的开销。`_rec_once()` 给的单层是「自调用点切成
        `1 + Σ实参`」——它**含**每层的调用开销，但不含叶子层。以前合并写成

            κ = 1 + Σ实参 + D × 单层

        那个 `1 + Σ实参` 只有在「叶子层 ≤ 每层调用开销」时才盖得住叶子层；
        叶子分支更重时（`if (n == 0) { return xs.length() + …; }`）它就**小于**
        叶子层成本——方向是不安全的。现在改写成

            κ = D × 单层 + max(1 + Σ实参, 叶子层)

        两项都是上界，取大者。

        **怎么算**：把自调用点**整条切成 0**（连实参也不计——那条路径上根本
        没发生这次调用），其余照常走 `_body`。`_block` / `if` 都是单调的
        add / max 组合，所以结果 ≥ **任何**自调用-free 路径的成本（多算
        别的分支只会更大，不会更小）。
        """
        got = self._rec_leaf_cache.get(name)
        if got is not None:
            return got
        fn = self.fns.get(name)
        if fn is None:
            return Cost.num(0)
        # **重入**（间接环）：拿「单层」当上界——单层 ≥ 叶子层，且它自己会截断。
        if name in self._rec_leaf_busy:
            return self._rec_once(name)
        old = self._rec_leaf_root
        self._rec_leaf_root = name
        self._rec_leaf_busy.add(name)
        try:
            body = self._body(fn)
        finally:
            self._rec_leaf_busy.discard(name)
            self._rec_leaf_root = old
        self._rec_leaf_cache[name] = body
        return body

    # ---------------------------------------------------------------- 递归树的形状
    #
    # **2026-11-09 修（`_diff/fib_rec` 破界：κ=2,064 < 实测 31,926）**
    #
    # `_rec_once()` 把「函数体只算一次」里**每一个**自调用点都切成 `1 + Σ实参`，
    # 调用点再乘声明深度 `D`。这只对**一条链**成立：
    #
    #     T(D) = B + T(D-1)        ⇒ T ≤ (D+1) × B          （gcd / fact / 二分）
    #
    # 一旦一条执行路径上出现**两个**自调用点（`fib(n-1) + fib(n-2)`），真实形状是
    #
    #     T(D) = B + 2·T(D-1)      ⇒ T = Θ(2^D)
    #
    # 而旧写法仍然乘 `D` ⇒ 指数被当成**加性**：一个指数函数被判「对 O(n²) 达成」，
    # 这是假通过（不是松，是**破界**）。
    #
    # **为什么退 ⊤ 而不是给指数项**：`Cost` 文法的节点只有
    # `num/size/add/mul/max/log2/piece/par/top`——**没有幂**。硬拼 `sym("2^n")`
    # 会让 `evaluate` 认不出（逐点求值一律 ∞），下游还得当 ⊤ 处理：结果一样，
    # 只是把「不知道」伪装成一个算不出来的符号。所以这里**直接 ⊤**（上界未知），
    # 宁可判不出来，也不许把指数记成线性。
    #
    # 判据是**一条执行路径**上最多几个自调用点（不是全函数出现几次）：
    #
    #   · `if (c) { return f(lo, mid); } else { return f(mid, hi); }`
    #     ——每层只走**一个**分支 ⇒ 仍是一条链，保持原来的线性合并（"二分"）；
    #   · `f(n-1) + f(n-2)`、`f(n-1); f(n-2);` ⇒ 一条路径两个 ⇒ 非线性 ⇒ ⊤；
    #   · 自调用落在**循环体**里 ⇒ 每层可能跑 L 次（L 静态不可知）⇒ 非线性 ⇒ ⊤。
    #
    # 路径计数按「退出路径 / 穿透路径」两分量做（`_shape_walk` 的 `(exit, fall)`）：
    # 没有这一步，`if (...) { return f(...); } return f(...);` 这种**早退**二分
    # 会被当成「顺序两个自调用」而误判 ⊤——那是过头，但更重要的是别把
    # 「分支里 return 之后不可能再执行」漏掉、把真正的树递归算小。

    def _self_call_shape(self, name: str):
        """`(per_path, bad_loop)`：一条执行路径上最多几次自调用 / 是否有自调用
        落在**轮数上界推不出 ≤ 1** 的循环里。

        ## 循环乘数（P0-R，2026-11-12）

        题面判据是「P = 自调用点数 × 每个点所在循环的轮数上界；P 的乘积上界
        ≤ 1 ⇒ 线性」。修前这里是**布尔**的「在循环里就 ⊤」，于是
        `for (i = 0; i < 1; i = i + 1) { s = s + f(n - 1); }` 也被判树递归
        （它每层其实**至多一次**自调用，是一条链）。

        现在只把**轮数上界推不出 ≤ 1** 的循环记成 `bad_loop`：

          · `while (k < 1)` / 轮数是常数 0 或 1 → 乘数 ≤ 1，**不算坏循环**；
          · 轮数是符号量（`for (auto& x : xs)` 的 `n(xs)`、`while (i < n)`）
            或 ≥ 2 → 每个自调用点可能跑多次 ⇒ 非线性（保守，和修前一样）；
          · 嵌套循环：只要有一层是坏循环，整条路径就是坏循环。

        「常数 0」也按 1 算（过近似，不是低估）：轮数 0 的循环里自调用根本
        不执行，记 1 只会让判据更保守。
        """
        hit = self._rec_shape_cache.get(name)
        if hit is not None:
            return hit
        fn = self.fns.get(name)
        if fn is None:
            out = (0, False)
        else:
            # 轮数推导要按**被分析的那个函数**查形参/局部（`_bound_to_cost_scoped`
            # 读 `cur_fn`）：形状分析可能在调用者的帧里被触发（`_call_inner` ①）。
            old_fn = self.cur_fn
            self.cur_fn = fn
            try:
                shape = self._shape_walk(fn, name, fn, False)
            finally:
                self.cur_fn = old_fn
            out = (max(shape[0], shape[1]), shape[2])
        self._rec_shape_cache[name] = out
        return out

    def _shape_literal_env(self, fn) -> Dict[str, "Cost"]:
        """**只按字面量初值**建的小环境：给 `_loop_single` 判轮数用。

        为什么不复用 `_decl_inits`：它把「被写过」的名字排除在外，而循环
        计数器（`u64 k = 0; while (k < 1) { … k = k + 1; }`）**必然**被写。
        轮数上界看的是**进入循环时的初值**（计数器只增 ⇒ 之后只会更小），
        所以这里保留字面量初值。同名多处声明 → 不收（作用域可能不同，
        拿一个初值套到另一个作用域上会猜错）。
        """
        env: Dict[str, "Cost"] = {}
        if fn is None:
            return env
        dup = set()
        for d in (list(_descendants(fn, "const-decl"))
                  + list(_descendants(fn, "var-decl"))):
            nm = getattr(d, "name", None)
            if not nm or len(getattr(d, "children", None) or []) < 2:
                continue
            if not (getattr(d, "value", None) or {}).get("has_init", True):
                continue
            if nm in env:
                dup.add(nm)
                continue
            v = _int_literal(d.children[-1])
            if v is None:
                continue
            env[nm] = Cost.num(v)
        for nm in dup:
            env.pop(nm, None)
        return env

    def _loop_single(self, st, fn) -> bool:
        """这个循环的**轮数上界能否证明 ≤ 1**（自调用点乘数的判据）。

        **只认 `while`**：三段式 `for` 在 parser 里都 desugar 成
        `block + while`，走到这里的一定是容器遍历（`for (auto& x : xs)`），
        它的轮数是 `n(xs)` 这样的符号量——永远不是常数，直接判否。

        轮数推导走 `bound_of_condition_wrapper`（与 `_while_inner` **同一份**
        口径，不另写一套）；推不出、或结果不是常数 → 判否（保守）。
        """
        if getattr(st, "kind", "") != "while":
            return False
        env = self._shape_literal_env(fn)
        try:
            b, _why = bound_of_condition_wrapper(
                st, self.repeats.get, env, self.row_of, self.capacities,
                self.const_names, fn,
                resolve=lambda nm: self._name_count(nm, fn),
                size_alias_skip=self._revoked_size_aliases(fn))
            if b is None:
                return False
            c = self._bound_to_cost_scoped(b, fn)
        except Exception:                                  # noqa: BLE001
            return False
        if c is None or not c.is_const():
            return False
        try:
            return 0 <= int(c.value) <= 1
        except (TypeError, ValueError):
            return False

    def _shape_walk(self, n, name: str, fn, bad_loop: bool):
        """`(exit, fall, bad_loop)`：走到**退出** / **穿透**语句时各最多几次自调用。

        `exit` = 这一段的路径上「return / break / continue 结束」的最坏计数，
        `fall` = 「执行完继续往下」的最坏计数。两者都是**上界**（取 max、相加
        都是过近似），所以判「线性」只会偏保守。

        `bad_loop`：这一段是否处在「轮数上界推不出 ≤ 1」的循环里——是的话，
        里面每个自调用点都可能执行多次（P > 1）。嵌套循环逐层累积。
        """
        if n is None:
            return (0, 0, False)
        kind = getattr(n, "kind", "") or ""

        def _kids(k=bad_loop):
            e = f = 0
            flag = False
            for c in (getattr(n, "children", None) or []):
                ce, cf, cl = self._shape_walk(c, name, fn, k)
                e += max(ce, cf)      # 顺序求值：每个孩子都跑（过近似）
                f += max(ce, cf)
                flag = flag or cl
            return (e, f, flag)

        if kind in ("call", "method-call"):
            # 自调用点：本节点算 1 次；实参表达式同样在**这条路径**上求值。
            here = 1 if _callee_short(n) == name else 0
            e = f = here
            flag = bool(here) and bad_loop
            for c in (getattr(n, "children", None) or []):
                ce, cf, cl = self._shape_walk(c, name, fn, bad_loop)
                e += max(ce, cf)
                f += max(ce, cf)
                flag = flag or cl
            return (e, f, flag)

        if kind == "block":
            # 顺序组合：先退出就不会执行后面的语句；穿透的路径才继续累加。
            e_acc = f_acc = 0
            flag = False
            for c in (getattr(n, "children", None) or []):
                ce, cf, cl = self._shape_walk(c, name, fn, bad_loop)
                flag = flag or cl
                e_acc = max(e_acc, f_acc + ce)
                f_acc = f_acc + cf
            return (e_acc, f_acc, flag)

        if kind in ("return", "break", "continue"):
            # `break` / `continue` 也**结束当前语句序列**（后面的语句不执行）。
            e = f = 0
            flag = False
            for c in (getattr(n, "children", None) or []):
                ce, cf, cl = self._shape_walk(c, name, fn, bad_loop)
                e += max(ce, cf)
                flag = flag or cl
            return (e, 0, flag)

        if kind == "if":
            kids = list(getattr(n, "children", None) or [])
            cond = kids[0] if kids else None
            ce, cf, flag = self._shape_walk(cond, name, fn, bad_loop)
            base = max(ce, cf)
            e_best = f_best = 0        # 没有 `else` 时存在「整条 if 都不进」的路径
            for b in kids[1:]:
                be, bf, bl = self._shape_walk(b, name, fn, bad_loop)
                e_best = max(e_best, be)
                f_best = max(f_best, bf)
                flag = flag or bl
            return (base + e_best, base + f_best, flag)

        if kind == "match":
            se, sf, flag = self._shape_walk(match_scrutinee(n), name, fn, bad_loop)
            base = max(se, sf)
            worst = 0
            for arm in arms_of(n):
                ae, af, al = self._shape_walk(arm, name, fn, bad_loop)
                worst = max(worst, ae, af)         # 臂互斥：只跑一个
                flag = flag or al
            return (base + worst, base + worst, flag)

        if kind in ("kernels",):
            # `?!` 的候选互斥：每次只跑一个 ⇒ 取 max
            e = f = 0
            flag = False
            for c in (getattr(n, "children", None) or []):
                ce, cf, cl = self._shape_walk(c, name, fn, bad_loop)
                e = max(e, ce)
                f = max(f, cf)
                flag = flag or cl
            return (e, f, flag)

        if kind in ("for", "while"):
            # 循环体（以及每轮都要求值的条件）里的自调用**可能每轮一次**：
            # 只有轮数上界能证明 ≤ 1 时，这条路径才仍是一条链；否则记坏循环。
            inner_bad = bad_loop or not self._loop_single(n, fn)
            e = f = 0
            flag = False
            for c in (getattr(n, "children", None) or []):
                ce, cf, cl = self._shape_walk(c, name, fn, inner_bad)
                e += max(ce, cf)
                f += max(ce, cf)
                flag = flag or cl
            return (e, f, flag)

        return _kids()

    def _rec_linear(self, name: str) -> bool:
        """调用树是否**退化成一条链**：一条路径上恰好一个自调用点，且没有任何
        一层循环的轮数上界 > 1（P ≤ 1）。

        循环乘数按 P0-R 的判据：自调用在循环里 ⇒ 每个点乘该循环的轮数上界；
        上界 ≤ 1 的循环不破坏线性（见 `_self_call_shape` / `_loop_single`）。

        `per_path == 0` 也判「非线性」：`short in self.frames` 说明这条边在环上，
        但按名字数不到自调用点（互递归 / 经函数值转发），那说明这个「一次」的
        模型本身不贴形状 ⇒ 不猜。
        """
        per_path, bad_loop = self._self_call_shape(name)
        return per_path == 1 and not bad_loop

    def _rec_tree_like(self, name: str) -> bool:
        """**确实是树递归**：一条路径上 ≥2 个自调用点，或自调用落在轮数推不出
        ≤ 1 的循环里。

        与 `_rec_linear()` 的区别：这里 `per_path == 0` **不算**树递归——
        调用方的 ①-b 分支会因为常数 `repeats` 走进同一个 `depth_of()` 判断，
        把「没有自调用」当树递归会把普通函数误退成 ⊤。
        """
        per_path, bad_loop = self._self_call_shape(name)
        return per_path >= 2 or bad_loop

    # ---------------------------------------------------------------- 树递归的分支数（④）
    #
    # **问题**（2026-11-12，语料 `10_backtrack` / `place`）：
    #
    #     void place(u64 row) {
    #         if (row == n_) { count_ += 1; return; }
    #         for (u64 col = 0; col < n_; ++col) {
    #             if (cols_[col] != 0) continue;
    #             place(row + 1);              // ← 自调用落在 for 里 ⇒ 树递归
    #         }
    #     }
    #
    # 一条执行路径上只有一个自调用点，但 `_rec_linear` 判「不是链」（自调用在
    # 轮数 > 1 的循环里）⇒ 旧实现退 ⊤。可这里**深度有界**（`.tu` 声明
    # `recursion depth <= 8`，运行期也真的会拦），**分支数也有界**（每层 ≤ n）：
    #
    #     T(D) = Σ_{k=0..D} B^k × 单层  ≤  (D+1)·B^D × 单层
    #
    # **判据（缺一即 ⊤）**：
    #   ① `.tu` 显式声明了 `recursion depth <= D`，D 是**字面量**；
    #   ② 每个自调用点都被若干层循环包着，且每层循环的**轮数上界可推**
    #      （`_for_count` / `bound_of_condition_wrapper`，与 `_while_inner` 同一份
    #      口径；推不出就 ⊤，绝不猜）；
    #   ③ 分支数 B = 各层循环轮数之**积**；多个自调用点 ⇒ 各点的 B **求和**
    #      （一次激活最多派生 `Σ B_site` 个子激活）；
    #   ④ 每个自调用点都把**同一个标量形参**按**恰好 +1** 推进（`row + 1`），
    #      且函数体从不改写它（`row` 只增 ⇒ 推进量就是"层"）。
    #
    # `(D+1)` 那个余量来自等比数列 `Σ_{k=0..D} B^k ≤ (D+1)·B^D`（B ≥ 1）。
    # 用它就**不需要符号指数**：D 是字面量常数 ⇒ `Cost.pow(B, D)` 走整指数，
    # 现有 `Cost.pow` 就支持（`Cost("pow", Fraction(D), (B,))`）。
    #
    # **为什么要求 `+1`（而不是 `-1` / `+2`）**：`h(n) { … for (i<2) h(n-1); }` 这种
    # 递减递归在 `test_kappa_gaps` 里**钉住是 ⊤**（它的循环乘数判据与这里不同，
    # 见 `_self_call_shape` 的 P0-R 注释）；`row + 2` 那种"每层跳两格"的推进与
    # 声明深度 `D` 的对应关系说不清。判据只收「按 +1 推进」，行为与既有测试
    # 一致，不靠"深度声明"去猜别的推进方式。

    def _tree_activation_bound(self, name: str) -> Optional["Cost"]:
        """④：树递归的**激活数上界** `(D+1)·B^D`；判据缺一即 None。"""
        if name in self._tree_cache:
            return self._tree_cache[name]
        out = self._tree_activation_bound_impl(name)
        self._tree_cache[name] = out
        return out

    def _tree_activation_bound_impl(self, name: str) -> Optional["Cost"]:
        fn = self.fns.get(name)
        if fn is None:
            return None
        # ① **显式**的 `recursion depth <= D`（字面量）。`depth_of` 还有
        # `repeats` 那条常数退路，这里**不认**——④ 要的是"作者声明的层数"。
        depth = self.depths.get(name)
        if not isinstance(depth, int) or depth < 1:
            return None
        body = next((c for c in (fn.children or []) if c.kind == "block"), None)
        if body is None:
            return None
        # 轮数推导要按**被分析的那个函数**查形参/局部（与 `_self_call_shape`
        # 同一处理）；`_for_count` 还读 `_fn_stack[-1]`，一起压上这一帧。
        old_fn = self.cur_fn
        _tf_prev = (self._tree_frame, self._tree_fn)
        self.cur_fn = fn
        self._tree_frame = name
        self._tree_fn = fn
        self._fn_stack.append(fn)
        try:
            sites = self._tree_self_sites(fn, name)
            if not sites:
                return None
            for _call, loops in sites:
                # ② 每个自调用点都必须在循环**体**里：裸自调用（`fib`）与
                # 条件里的自调用都不在乘数里，直接拒绝。
                if not loops:
                    return None
            if not self._tree_depth_param_ok(fn, [c for c, _ in sites]):
                return None                 # ④
            total: Optional[Cost] = None
            for _call, loops in sites:
                prod = Cost.num(1)
                for st in loops:
                    b = self._tree_loop_bound(st, fn)
                    if b is None:
                        return None         # ②/③：轮数（分支数）推不出
                    prod = Cost.mul(prod, b)
                total = prod if total is None else Cost.add(total, prod)
            if total is None or total.is_top():
                return None
            return Cost.mul(Cost.num(depth + 1), Cost.pow(total, depth))
        finally:
            self._fn_stack.pop()
            self.cur_fn = old_fn
            self._tree_frame, self._tree_fn = _tf_prev

    def _tree_self_sites(self, fn, name: str):
        """收集 `name` 的自调用点 → `[(call, [外层循环…])]`；条件里的 → None。

        循环按**从内到外**排（先乘内层）。`for` / `while` 都算循环：三段式
        `for` 在 parser 里已经 desugar 成 `while`，这里剩下的是容器遍历。

        **条件里的自调用**（`while (f(x) > 0)`）直接判否：它每轮求值一次，
        一轮之外的**最后一次失败检查**也会调一次 ⇒ 乘数应是 `B+1` 而不是 `B`。
        用 B 就是低估，所以宁可 ⊤（不猜）。
        """
        parent = {}
        for n in _walk_all(fn):
            for c in (getattr(n, "children", None) or []):
                parent[id(c)] = n
        out = []
        for n in _walk_all(fn):
            if getattr(n, "kind", "") not in ("call", "method-call"):
                continue
            if _callee_short(n) != name:
                continue
            loops = []
            cur = n
            while id(cur) in parent:
                p = parent[id(cur)]
                if getattr(p, "kind", "") in ("while", "for"):
                    kids = list(getattr(p, "children", None) or [])
                    if kids and kids[0] is cur:
                        return None     # 自调用在循环**条件**里（见上）
                    loops.append(p)
                cur = p
            out.append((n, loops))
        return out

    def _tree_loop_bound(self, st, fn) -> Optional["Cost"]:
        """一层循环的**轮数上界**（树递归的分支因子）；推不出 → None。

        `while` 走 `bound_of_condition_wrapper` + `_bound_to_cost_scoped`
        （与 `_while_inner` 同一份口径）；`for` 走 `_for_count`。
        结果还要过 `_tree_bound_ok`：界里**不许出现非形参的名字**。
        """
        k = getattr(st, "kind", "")
        if k == "for":
            c = self._for_count(st)
        elif k == "while":
            env = self._shape_literal_env(fn)
            b, _why = bound_of_condition_wrapper(
                st, self.repeats.get, env, self.row_of, self.capacities,
                self.const_names, fn,
                resolve=lambda nm: self._name_count(nm, fn),
                size_alias_skip=self._revoked_size_aliases(fn))
            if b is None:
                return None
            c = self._bound_to_cost_scoped(b, fn)
            if c is None:
                # **标量成员当界**（`while (col < recv.n_)`）：`_bound_to_cost_scoped`
                # 对 FIELD_TAG 的要求是"基对象一次都没被动过"（`_field_bound_blocked`
                # 把**任何**字段写、以及**把对象传出去**都算动过）。递归方法必然
                # 把 `recv` 传给自调用，也常写别的字段（`recv.cols_[c] = 1`）——
                # 那条保守判据整条否掉，但这里可以**逐字段**证：
                # 只要 `recv.n_` 本身没被写、且 `recv` 只传给**自调用**，它就全程
                # 不变（见 `_tree_bound_ok` / `_tree_field_static`）。
                c = bound_to_cost_inst(b, self.const_values,
                                       self._scalar_params_of())
        else:
            return None
        if c is None or c.is_top():
            return None
        if not self._tree_bound_ok(c, fn):
            return None
        return c

    def _tree_bound_ok(self, c: "Cost", fn) -> bool:
        """界里的每个尺寸量/符号都必须**根在形参上**（调用点才换得掉）。

        `n(i)`（局部计数器）、`n(total)`（推不出初值的局部别名）这类名字在
        调用点上没有对应物，求值会按 1 算 ⇒ **低估**。所以宁可 ⊤。
        另外，名字所在的形参**不许被整体改写**（`n = n - 1`）。
        """
        if c is None or c.is_top():
            return False
        names = {p.name for p in self._tree_params(fn) if p.name}
        if not names:
            return False
        for v in c.variables():
            root, _dot, fld = str(v).partition(".")
            if root not in names:
                return False
            if not self._tree_field_static(fn, root, fld):
                return False
        return True

    def _tree_field_ok(self, fn, dotted: str) -> bool:
        """④ 的**精确版**成员界：`recv.n_` 在这个函数执行期间不变。

        与 `_field_bound_ok` 同一件事、不同粒度：那里"对象动过 ⇒ 所有成员都
        不许当界"；这里只挡**同一个字段链**（`recv.n_` 不受 `recv.cols_[c] = 1`
        影响），且**允许把对象传给自己的自调用**（同一个函数体已经逐字段证过）。
        只在 `_tree_frame` 那一帧里被用到（见 `_bound_to_cost_scoped`）。
        """
        if fn is None or not dotted or "." not in dotted:
            return False
        base, _dot, fld = dotted.partition(".")
        names = {p.name for p in self._tree_params(fn) if p.name}
        if base not in names:
            return False
        return self._tree_field_static(fn, base, fld)

    @staticmethod
    def _tree_params(fn):
        pnode = next((c for c in ((fn.children if fn is not None else []) or [])
                      if c.kind == "params"), None)
        return list(pnode.children if pnode is not None else [])

    def _tree_field_static(self, fn, root: str, fld: str) -> bool:
        """`root.fld`（`fld` 为空 = 整个形参）在**本函数执行期间**是否不变。

        与 `_field_bound_blocked` 的区别是**粒度**：那里"对象动过 ⇒ 所有成员都
        不许当界"；这里只挡**同一个字段链**（`recv.n_` 与 `recv.cols_[i] = 1`
        互不影响）。整个形参被赋值（`recv = …`）仍然挡。
        传出去只允许**传给自己的自调用**（同一个函数体，逐字段证过不改它）。
        """
        if fn is None:
            return False
        if fld:
            same = root + "." + fld
        else:
            same = root
        for a in (list(_descendants(fn, "assign"))
                  + list(_descendants(fn, "assign-op"))):
            lhs = a.children[0] if (a.children or []) else None
            while getattr(lhs, "kind", "") in ("index", "paren", "ref", "deref"):
                lhs = lhs.children[0] if getattr(lhs, "children", None) else None
            nm = field_chain_name(lhs) if lhs is not None else None
            if not nm:
                continue
            # `x = …` / `x.f = …` 都算；前缀关系两个方向都要挡
            # （写 `recv` 整体 ⇒ `recv.n_` 变了；写 `recv.n_.x` 也变了）。
            if (nm == same or nm.startswith(same + ".")
                    or same.startswith(nm + ".")):
                return False
        # `&root`（按引用传出）⇒ 被调方可能写它。
        for r in _descendants(fn, "ref"):
            if any(getattr(p, "name", "") == root for p in _descendants(r, "path")):
                return False
        # 当实参 / 方法接收者传出去：**只允许**本函数自己的自调用。
        own = getattr(fn, "name", "") or ""
        for c in (list(_descendants(fn, "call"))
                  + list(_descendants(fn, "method-call"))):
            kids = list(getattr(c, "children", None) or [])
            pairs = []
            if getattr(c, "kind", "") == "method-call" and kids:
                pairs.append(kids[0])
            for a in kids:
                if getattr(a, "kind", "") == "args":
                    pairs.extend(a.children or [])
            for x in pairs:
                nm = field_chain_name(x)
                if not nm or nm.split(".", 1)[0] != root:
                    continue
                if _callee_short(c) != own:
                    return False
        return True

    def _tree_depth_param_ok(self, fn, calls) -> bool:
        """④：存在一个标量形参，**每个**自调用点都把它按**恰好 +1** 推进，
        且函数体从不改写它（`row` 只增 ⇒ 推进量就是"层"）。"""
        ps = self._tree_params(fn)
        for i, p in enumerate(ps):
            nm = getattr(p, "name", "") or ""
            if not nm:
                continue
            ty = p.children[0] if (p.children or []) else None
            if ty is not None and _is_container_type(ty):
                continue                    # 容器/结构体：不是"层"计数器
            if _is_ref_type(ty):
                continue
            if self._tree_param_written(fn, nm):
                continue
            if all(self._tree_arg_advances(_call_args(call), i, nm)
                   for call in calls):
                return True
        return False

    @staticmethod
    def _tree_arg_advances(argv, i: int, pname: str) -> bool:
        """第 i 个实参是不是 `pname + 1` / `1 + pname`（**恰好 +1**）。

        **只收 +1**（2026-11-12 定，判据 ④ 的字面口径）：`row + 2` 那种"每层
        跳两格"的推进，层数与声明深度 `D` 的对应关系说不清（声明的是"递归
        深度"还是"格子数"?）——推不出就不猜，退 ⊤。递减（`n - 1`）同理：
        `test_kappa_gaps` 钉着 `h(n-1)` 在 `for (i<2)` 里仍是 ⊤。
        """
        if i >= len(argv):
            return False
        a = _paren_inner(argv[i])
        if getattr(a, "kind", "") != "binary" or (a.value or "") != "+":
            return False
        kids = list(getattr(a, "children", None) or [])
        if len(kids) < 2:
            return False
        for x, y in ((kids[0], kids[1]), (kids[1], kids[0])):
            if (getattr(x, "kind", "") == "path" and x.name == pname):
                k = _int_literal(y)
                if k == 1:
                    return True
        return False

    @staticmethod
    def _tree_param_written(fn, name: str) -> bool:
        """函数体里有没有 `name = …` / `name += …`（整体改写形参本身）。"""
        for a in (list(_descendants(fn, "assign"))
                  + list(_descendants(fn, "assign-op"))):
            lhs = a.children[0] if (a.children or []) else None
            while getattr(lhs, "kind", "") in ("paren", "ref", "deref"):
                lhs = lhs.children[0] if getattr(lhs, "children", None) else None
            if getattr(lhs, "kind", "") == "path" and lhs.name == name:
                return True
        return False

    # ---------------------------------------------------------------- visited 守卫的递归（②）
    #
    # **问题**（2026-11-12，语料 `12_graph`）：
    #
    #     static bool dfs_visit(const Graph& g, u64 v, std::vector<u64>& color) {
    #         color[v] = 1;                                   // ← 进入即标记
    #         for (k < g[v].size()) { w = g[v][k];
    #             if (color[w] == 0) { if (dfs_visit(g, w, color)) return true; }  // ← 守卫
    #         }
    #         color[v] = 2; return false;
    #     }
    #
    # 自调用在**轮数推不出 ≤1 的循环里**（`for k < L(g)`），按树递归判 ⊤。但这不是
    # 树递归：**每个顶点至多进入一次** ⇒ 一次调用的激活数 ≤ `n(color)`。
    #
    # **能证的前提（缺一即不给界）**：
    #
    #   ① 一条执行路径上**恰好一个**自调用点（`per_path == 1`）；
    #   ② 有一个**非 const 引用容器形参** `m`（"已访问标记"），每个自调用点都把它
    #      按同一个形参位置传下去（`dfs_visit(…, color)`）；
    #   ③ 每个自调用点都被 `m[<实参>] == 0` 这样的守卫**支配**——守卫里的下标与
    #      自调用传给标量形参的那个实参**是同一个表达式**（`color[w] == 0` 与
    #      `dfs_visit(g, w, color)`）；
    #   ④ 函数体在**无条件**位置（`body` 的直接子语句，且在第一个含自调用的语句
    #      之前）有一句 `m[<形参>] = <正整数字面量>`——进入即标记；
    #   ⑤ 函数体**从不把 `m` 改回 0**：对 `m` 的写只有"下标赋值且右值是正整数字面量"；
    #      `m` 不外传（除了上面那个自调用）、不整体赋值、不调 `clear/fill/…`。
    #
    # **结论**：任何一次激活的顶点值互不相同（一旦被标成正数就不再是 0，而每个
    # 自调用都要求当时为 0），而顶点值都在 `[0, n(m))` ⇒ **激活数 ≤ n(m)**。
    # 于是 `κ(rec) ≤ n(m) × 单层 + 尾部`，与 `recursion depth` 那条公式同形，
    # 只是深度换成**可证的尺寸量**（调用点上再由 `_param_size_table` 换成调用方
    # 的容器尺寸）。**这不是"程序会终止"的假设**：每条前提都是这条代码本身的形状。

    def _visited_depth(self, name: str) -> Optional["Cost"]:
        """visited 守卫成立的递归 → **激活数上界** `n(标记容器)`；否则 None。"""
        if name in self._visited_cache:
            return self._visited_cache[name]
        out = self._visited_depth_impl(name)
        self._visited_cache[name] = out
        return out

    def _visited_depth_impl(self, name: str) -> Optional["Cost"]:
        fn = self.fns.get(name)
        if fn is None:
            return None
        per_path, _bad_loop = self._self_call_shape(name)
        if per_path != 1:
            return None                     # 一条路径 0 个（互递归）或 ≥2 个（树）
        body = next((c for c in (fn.children or []) if c.kind == "block"), None)
        if body is None:
            return None
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        ps = pnode.children if pnode is not None else []
        calls: List[tuple] = []
        self._collect_self_calls(body, name, [], calls)
        if not calls:
            return None
        # **标记容器候选**：非 const 引用的容器形参（要能写才能标记）。
        markers = []
        for i, p in enumerate(ps):
            ty = p.children[0] if (p.children or []) else None
            if not p.name or not _is_ref_type(ty):
                continue
            if (getattr(p, "value", None) or {}).get("const"):
                continue
            if _container_type_name(ty) is None:
                continue
            markers.append((i, p.name))
        if not markers:
            return None
        for mi, mname in markers:
            vparam = self._marker_entry_param(fn, body, ps, mname, calls)
            if vparam is None:
                continue
            if not self._marker_untouched(fn, name, ps, mname, mi):
                continue
            if all(self._self_call_guarded(call, ps, mi, mname, guards, vparam)
                   for call, guards in calls):
                return Cost.size(mname)
        return None

    def _collect_self_calls(self, node, name: str, guards: List[Node],
                            out: List[tuple]) -> None:
        """收集 `name` 的自调用点，以及它**外层 if / 循环条件**的栈。"""
        if node is None:
            return
        k = getattr(node, "kind", "")
        if k in ("if", "while", "for"):
            cond = node.children[0] if node.children else None
            # **条件本身也要走**：`if (dfs_visit(…)) { … }` 的自调用就在条件里。
            # 条件不是它自己分支的"守卫"（守卫是外层那个 `if`），所以不加栈。
            self._collect_self_calls(cond, name, guards, out)
            g2 = guards + ([cond] if cond is not None else [])
            for b in (node.children or [])[1:]:
                self._collect_self_calls(b, name, g2, out)
            return
        if k in ("call", "method-call") and _callee_short(node) == name:
            out.append((node, list(guards)))
        for c in (getattr(node, "children", None) or []):
            self._collect_self_calls(c, name, guards, out)

    def _marker_entry_param(self, fn, body, ps, mname: str,
                            calls) -> Optional[int]:
        """④：入口处无条件 `m[<形参>] = <正整数>`。返回那个形参的下标。"""
        first_call = len(body.children)
        for i, st in enumerate(body.children or []):
            if any(True for _ in _descendants(st, "call")
                   if _callee_short(_) == (fn.name or "")):
                first_call = i
                break
        for i, st in enumerate(body.children or []):
            if i >= first_call:
                break
            if getattr(st, "kind", "") != "assign":
                continue
            lhs = st.children[0] if st.children else None
            rhs = st.children[1] if len(st.children or []) > 1 else None
            if (lhs is None or getattr(lhs, "kind", "") != "index"
                    or not lhs.children
                    or getattr(lhs.children[0], "kind", "") != "path"
                    or lhs.children[0].name != mname
                    or len(lhs.children) < 2):
                continue
            idx = lhs.children[1]
            if getattr(idx, "kind", "") != "path" or not idx.name:
                continue
            lit = _int_literal(rhs)
            if lit is None or lit <= 0:
                continue
            for pi, p in enumerate(ps):
                if p.name == idx.name:
                    ty = p.children[0] if (p.children or []) else None
                    if ty is None or _is_container_type(ty) or _is_ref_type(ty):
                        return None
                    return pi
            return None
        return None

    def _self_call_guarded(self, call, ps, mi: int, mname: str, guards,
                           vparam: int) -> bool:
        """②③：自调用把 `mname` 按原位置传下去，且被 `m[<本层实参>] == 0` 守卫。"""
        args = next((c for c in (call.children or []) if c.kind == "args"), None)
        argv = args.children if args is not None else []
        if mi >= len(argv) or vparam >= len(argv):
            return False
        a_m = argv[mi]
        if getattr(a_m, "kind", "") != "path" or a_m.name != mname:
            return False
        a_v = argv[vparam]
        w_v = _expr_text(a_v)
        for g in guards:
            if getattr(g, "kind", "") != "binary" or (g.value or "") != "==":
                continue
            if len(g.children or []) < 2:
                continue
            l, r = g.children[0], g.children[1]
            for x, y in ((l, r), (r, l)):
                if getattr(x, "kind", "") != "index" or not x.children:
                    continue
                if (getattr(x.children[0], "kind", "") != "path"
                        or x.children[0].name != mname
                        or len(x.children) < 2):
                    continue
                if _int_literal(y) != 0:
                    continue
                if _expr_text(x.children[1]) == w_v:
                    return True
        return False

    def _marker_untouched(self, fn, name: str, ps, mname: str, mi: int) -> bool:
        """⑤：`m` 从不被改回 0、不外传、不整体赋值。

        **别名也要挡**：本语言没有局部引用（`E-Ref-Local`），所以同一个容器只能
        通过"实参 / 接收者 / `&m`"三条路被另一个名字看到。三条都在这里拒掉——
        否则"标记永不回 0"可以被 `m2` 一侧的写推翻（两个形参指向同一个容器）。
        """
        for a in list(_descendants(fn, "assign")):
            lhs = a.children[0] if (a.children or []) else None
            if lhs is None:
                continue
            if getattr(lhs, "kind", "") == "path" and lhs.name == mname:
                return False                    # 整体赋值：内容可能被换掉
            if (getattr(lhs, "kind", "") == "index" and lhs.children
                    and getattr(lhs.children[0], "kind", "") == "path"
                    and lhs.children[0].name == mname):
                rhs = a.children[1] if len(a.children or []) > 1 else None
                lit = _int_literal(rhs)
                if lit is None or lit <= 0:
                    return False                # 写 0 / 非常量：标记可能被清
                continue
            # `x = m[i]` 之类只是读，没有别名赋值风险（引用形参已在外层排除）
        # **`m[v] += e` 不认**（`-=` 会把标记减回 0；`+= 0` 也不建立标记）。
        for a in list(_descendants(fn, "assign-op")):
            lhs = a.children[0] if (a.children or []) else None
            if (getattr(lhs, "kind", "") == "index" and lhs.children
                    and getattr(lhs.children[0], "kind", "") == "path"
                    and lhs.children[0].name == mname):
                return False
        # **`&m` 传出去**：被调方可能写它。
        for r in list(_descendants(fn, "ref")):
            for pth in _descendants(r, "path"):
                if getattr(pth, "name", "") == mname:
                    return False
        for c in list(_descendants(fn, "method-call")):
            recv = c.children[0] if (c.children or []) else None
            if getattr(recv, "kind", "") == "path" and recv.name == mname:
                if _callee_short(c) not in self._FIELD_READ_METHODS:
                    return False                # clear / fill / resize / push …
        for c in list(_descendants(fn, "call")):
            short = _callee_short(c)
            args = next((x for x in (c.children or []) if x.kind == "args"), None)
            for i, a in enumerate(args.children if args is not None else []):
                nm = field_chain_name(a)
                if nm != mname:
                    continue
                if short == name and i == mi:
                    continue                    # 自调用：正是被证明的那一处
                return False                    # 外传 / 别名位置：被调方可能清掉标记
        return True

    # ---------------------------------------------------------------- 区间对半分的递归
    #
    # **问题**（2026-11-12，语料 `01_sorting.merge_sort`）：
    #
    #     static void merge_sort(vector<u64>& xs, size_t lo, size_t hi) {
    #         if (hi - lo < 2) { return; }
    #         size_t mid = lo + (hi - lo) / 2;
    #         merge_sort(xs, lo, mid);            // 左半 [lo, mid)
    #         merge_sort(xs, mid, hi);            // 右半 [mid, hi)
    #         merge_halves(xs, lo, mid, hi);
    #     }
    #
    # 一条执行路径上 **2** 个自调用点 ⇒ `_rec_tree_like` 判「树递归 ⇒ ⊤」。
    # 但它**不是**指数：`fib(n-1) + fib(n-2)` 的两棵子树吃的是**同一组输入**，
    # 每层都要把整个区间再试一遍；这里两棵子树吃的是 `[lo, mid)` 与 `[mid, hi)`。
    #
    # **上界证明**（写进 κ 的那一条，不是"假设程序会停"）：
    #
    #   · 每个激活的区间 ⊆ 父区间的一半 ⇒ 第 k 层的区间长度 ≤ (hi-lo)/2^k；
    #   · 同一层的区间**互不相交**（左半 ∪ 右半 = 父区间，交集为空）
    #     ⇒ 第 k 层至多 2^k 个激活；
    #   · 叶子（不再自调用）的区间长度 ≥ 1、且互不相交 ⇒ **叶子数 ≤ hi - lo**；
    #   · 二叉树的内部激活数 = 叶子数 - 1 < 叶子数
    #   ⇒ **总激活数 ≤ 2 × (hi - lo)**。
    #
    # 于是 κ **不是**「激活数 × 单层」——那会把**每一层**都乘上入口区间的长度
    # （`2s × (A + B·s)`），拿到 `O(s²)`，把作者写对的 `O(n log n)` 判成假债务。
    # 正确的是**按层求和**（见 `_halving_level_sum`）：
    #
    #     T(s) = 2·T(s/2) + (A + B·s)
    #     ⇒ Σ_{k<K} [2^k·A + B·s] = A·(2^K − 1) + B·s·K = Θ(s log s)
    #     ⇒ κ ≤ (2s + 1)·A + B·s·(log2(s+1) + 1)      （s = hi - lo）
    #
    # 落到**某个**自调用点时，它拿到的是父区间的一半 ⇒ 那里用 `(s+1)/2`
    # （`ceil(s/2)`），两个自调用点合起来正好是整棵树的 `2s·A + B·s·log2(s)`。
    # 单层成本必须先变成**只随区间长度走**的（`_halving_callee_plan` /
    # `_resolve_halving_diffs`）：否则 `mid - lo` / `hi - mid` 会退化成绝对下标
    # `mid` / `hi`，按层求和又变回二次。
    #
    # **「区间互不相交」在代码里落成三条**（缺一即 None ⇒ 仍 ⊤）：
    #   ① 两次调用就是全部自调用（`len(calls) == 2`）且**顺序执行**
    #      （`per_path == 2`；互斥分支各一个时 `per_path` 会是 1）；
    #   ② `lo` / `hi` 是**按值**标量形参 ⇒ 子调用改不到父层的区间端点；
    #   ③ `mid` 是**局部**、初值就是 `(lo+hi)/2`（或 `lo+(hi-lo)/2` / 右移写法），
    #      且函数体里除"两次调用的那个实参位置"外**再没有第二处出现**
    #      ⇒ 两次调用读到的 mid 是同一个值 ⇒ 左半/右半真的是父区间的一个划分。
    # 三者合起来才让"第 k 层的 2^k 个区间互不相交"成立；少一条就只能 ⊤。

    def _halving_span(self, name: str) -> Optional[Tuple[str, str]]:
        """区间对半分的递归 → `(lo 形参名, hi 形参名)`；否则 `None`（不猜）。"""
        if name in self._halving_cache:
            return self._halving_cache[name]
        out = self._halving_span_impl(name)
        self._halving_cache[name] = out
        return out

    def _halving_span_impl(self, name: str) -> Optional[Tuple[str, str]]:
        fn = self.fns.get(name)
        if fn is None:
            return None
        per_path, bad_loop = self._self_call_shape(name)
        if per_path != 2 or bad_loop:
            return None
        body = next((c for c in (fn.children or []) if c.kind == "block"), None)
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        if body is None or pnode is None:
            return None
        ps = list(pnode.children or [])
        # ① 形参：`(容器, 标量 lo, 标量 hi)`——两个端点都**按值**
        #    （按引用时子调用能改父层的 lo/hi，"区间不变"就不再成立）。
        if len(ps) != 3:
            return None
        ty0 = ps[0].children[0] if ps[0].children else None
        if _container_type_name(ty0) is None:
            return None
        lo_nm = getattr(ps[1], "name", "") or ""
        hi_nm = getattr(ps[2], "name", "") or ""
        if not lo_nm or not hi_nm or lo_nm == hi_nm:
            return None
        for p in (ps[1], ps[2]):
            ty = p.children[0] if p.children else None
            if ty is None or _is_ref_type(ty):
                return None
            if _container_type_name(ty) is not None:
                return None
            if self._type_parts(ty)[0] in self.structs:
                return None
        # ② 恰好两个自调用点（**语句位置或表达式里都一样**）：
        #    `merge_sort(…); merge_sort(…);` 与
        #    `return f(lo, mid) + f(mid, hi);` 是同一种形状——激活数只取决于
        #    "两次调用各拿哪个区间"，与结果怎么用无关。
        calls: List[tuple] = []
        self._collect_self_calls(body, name, [], calls)
        if len(calls) != 2:
            return None
        nodes = [c for c, _g in calls]
        argv = []
        for c in nodes:
            an = next((x for x in (c.children or []) if x.kind == "args"), None)
            a = list(an.children if an is not None else [])
            if len(a) != 3:
                return None
            argv.append(a)
        # ③ 容器实参：两次都是**同一个**容器形参（裸名）。
        if (_expr_text(argv[0][0]) != (ps[0].name or "")
                or _expr_text(argv[1][0]) != (ps[0].name or "")):
            return None
        # ④ 两次调用的实参配对 + `lo` / `hi` 的只读别名。
        alias = self._halving_aliases(body, lo_nm, hi_nm, nodes)
        pair = self._halving_pair(argv[0], argv[1], lo_nm, hi_nm, alias)
        if pair is None:
            return None
        mid_a, mid_b = pair
        mid_nm = getattr(_paren_inner(mid_a), "name", "") or ""
        if not mid_nm or mid_nm in (lo_nm, hi_nm):
            return None
        if mid_nm in {getattr(p, "name", "") for p in ps}:
            return None
        # ⑤ `mid`：局部标量，初值 = `(lo + hi) / 2` 的等价形状之一，且只写这一次。
        decl = self._local_decl(body, mid_nm)
        if decl is None:
            return None
        if not (getattr(decl, "value", None) or {}).get("has_init", True):
            return None
        init = decl.children[-1] if len(decl.children or []) > 1 else None
        if not self._is_halving_mid(init, lo_nm, hi_nm):
            return None
        if not self._scalar_readonly(body, mid_nm, nodes):
            return None
        # ⑥ 早退：`hi - lo < C`（C ≤ 3）或 `<= C`（C ≤ 2），且那一支真的 `return`。
        if not self._has_halving_base(body, lo_nm, hi_nm):
            return None
        return (lo_nm, hi_nm)

    def _halving_aliases(self, body, lo_nm: str, hi_nm: str,
                         self_nodes) -> Dict[str, str]:
        """`u64 a = lo;` 这种**只读别名** → `{a: lo}`；其余一律不收。

        收的条件：初值就是 `lo` / `hi` 的裸名、**只有一处声明**，且这个名字
        从没被写过 / 取过地址 / 传给非 const 引用（`_scalar_readonly`）。
        """
        out: Dict[str, str] = {}
        for d in (list(_descendants(body, "var-decl"))
                  + list(_descendants(body, "const-decl"))):
            nm = getattr(d, "name", "") or ""
            if not nm or nm in (lo_nm, hi_nm) or nm in out:
                continue
            if not (getattr(d, "value", None) or {}).get("has_init", True):
                continue
            src = _paren_inner(d.children[-1] if len(d.children or []) > 1 else None)
            if getattr(src, "kind", "") != "path" or getattr(src, "name", "") not in (lo_nm, hi_nm):
                continue
            if self._local_decl(body, nm) is not d:
                continue                     # 同名多处声明（遮蔽）：不认
            if self._scalar_readonly(body, nm, self_nodes):
                out[nm] = src.name
        return out

    def _halving_pair(self, a1, a2, lo_nm: str, hi_nm: str,
                      alias: Dict[str, str]):
        """`(xs, lo, mid)` + `(xs, mid, hi)`（或对称顺序）→ 那两个 `mid` 节点。"""
        def _want(n, nm: str) -> bool:
            n = _paren_inner(n)
            got = getattr(n, "name", "") if getattr(n, "kind", "") == "path" else ""
            return bool(got) and (got == nm or alias.get(got) == nm)

        def _path(n):
            n = _paren_inner(n)
            return getattr(n, "name", "") if getattr(n, "kind", "") == "path" else ""

        for x, y in ((a1, a2), (a2, a1)):
            if not (_want(x[1], lo_nm) and _want(y[2], hi_nm)):
                continue
            mx, my = _path(x[2]), _path(y[1])
            if not mx or mx != my:
                continue
            return (x[2], y[1])
        return None

    def _local_decl(self, body, nm: str) -> Optional[Node]:
        """函数体里**唯一**一个叫 `nm` 的声明；没有或有多个（遮蔽）→ None。"""
        hits = [d for d in (list(_descendants(body, "var-decl"))
                            + list(_descendants(body, "const-decl")))
                if getattr(d, "name", "") == nm]
        return hits[0] if len(hits) == 1 else None

    def _scalar_readonly(self, body, nm: str, self_nodes) -> bool:
        """`nm` 在函数体里**从不被改动**（只读）。

        挡三件事，每一条都能让"两次调用读到同一个 mid"失效：

          · `nm = …;` / `nm += …`（`++nm` 在 parser 里也 desugar 成 `assign`）；
          · `&nm` 取地址（本语言没有局部引用，但 `&` 是实参语法的一种）；
          · 把 `nm` 传给**非 const 引用**形参的调用（被调方能改它）。
            认不出被调方（内建 / 外部）时，只有"位置在后"才安全——那已经过了
            两次自调用，改不改都无所谓了。
        """
        last_line = max([getattr(c, "line", 0) or 0 for c in self_nodes] or [0])
        for n in _walk_all(body):
            k = getattr(n, "kind", "")
            if k in ("assign", "assign-op"):
                lhs = (n.children or [None])[0]
                if (getattr(lhs, "kind", "") == "path"
                        and getattr(lhs, "name", "") == nm):
                    return False
            elif k == "ref":
                if any(getattr(x, "kind", "") == "path"
                       and getattr(x, "name", "") == nm for x in _walk_all(n)):
                    return False
            elif k in ("call", "method-call"):
                an = next((x for x in (n.children or [])
                           if getattr(x, "kind", "") == "args"), None)
                for i, a in enumerate(an.children if an is not None else []):
                    if not (getattr(a, "kind", "") == "path"
                            and getattr(a, "name", "") == nm):
                        continue
                    fn2 = self.fns.get(_callee_short(n)) if k == "call" else None
                    if fn2 is None:
                        # 内建 / 外部 / 方法调用：认不出形参的 const 性。
                        # 只有"已经过了两次自调用"才允许（那时改不改都不影响区间）。
                        if (getattr(n, "line", 0) or 0) <= last_line:
                            return False
                        continue
                    ps2 = next((c for c in (fn2.children or [])
                                if getattr(c, "kind", "") == "params"), None)
                    kids = list(ps2.children if ps2 is not None else [])
                    if i >= len(kids):
                        return False
                    ty2 = kids[i].children[0] if kids[i].children else None
                    if (_is_ref_type(ty2)
                            and not (getattr(kids[i], "value", None) or {}).get("const")):
                        return False
        return True

    def _is_halving_mid(self, init, lo_nm: str, hi_nm: str) -> bool:
        """初值是不是「区间中点」：`(lo+hi)/2` / `(lo+hi)>>1` / `lo+(hi-lo)/2` / `lo+((hi-lo)>>1)`。

        四种写法在**无符号整数**上都是 `lo + floor((hi-lo)/2)`（`lo ≤ hi` 时
        与 `floor((lo+hi)/2)` 相等），所以都可以当"左半 ≤ 右半、两半都 < 父"的
        依据。**只认这四个形状**：多边形求值、`(lo+hi+1)/2`、`mid = lo` 都不收。
        """
        e = _paren_inner(init)
        if e is None or getattr(e, "kind", "") != "binary":
            return False
        op = getattr(e, "value", "") or ""
        kids = list(e.children or [])
        if len(kids) < 2:
            return False
        if op in ("/", ">>"):
            return (self._is_param_sum(_paren_inner(kids[0]), lo_nm, hi_nm)
                    and _int_literal(_paren_inner(kids[1])) == (2 if op == "/" else 1))
        if op == "+":
            for base, half in ((kids[0], kids[1]), (kids[1], kids[0])):
                base = _paren_inner(base)
                if (getattr(base, "kind", "") == "path"
                        and getattr(base, "name", "") == lo_nm
                        and self._is_halving_half(_paren_inner(half), lo_nm, hi_nm)):
                    return True
        return False

    def _is_param_sum(self, e, lo_nm: str, hi_nm: str) -> bool:
        """`lo + hi`（任一顺序）。"""
        if e is None or getattr(e, "kind", "") != "binary" or (e.value or "") != "+":
            return False
        kids = [_paren_inner(k) for k in (e.children or [])]
        if len(kids) < 2:
            return False
        names = {getattr(k, "name", "") for k in kids
                 if getattr(k, "kind", "") == "path"}
        return len(kids) == 2 and names == {lo_nm, hi_nm} and all(
            getattr(k, "kind", "") == "path" for k in kids)

    def _is_halving_half(self, e, lo_nm: str, hi_nm: str) -> bool:
        """`(hi - lo) / 2` 或 `(hi - lo) >> 1`。"""
        if e is None or getattr(e, "kind", "") != "binary":
            return False
        op = getattr(e, "value", "") or ""
        if op not in ("/", ">>"):
            return False
        kids = list(e.children or [])
        if len(kids) < 2:
            return False
        if _int_literal(_paren_inner(kids[1])) != (2 if op == "/" else 1):
            return False
        d = _paren_inner(kids[0])
        if d is None or getattr(d, "kind", "") != "binary" or (d.value or "") != "-":
            return False
        dk = [_paren_inner(k) for k in (d.children or [])]
        return (len(dk) == 2
                and getattr(dk[0], "kind", "") == "path"
                and getattr(dk[1], "kind", "") == "path"
                and getattr(dk[0], "name", "") == hi_nm
                and getattr(dk[1], "name", "") == lo_nm)

    def _has_halving_base(self, body, lo_nm: str, hi_nm: str) -> bool:
        """有 `hi - lo < C`（C ≤ 3）/ `hi - lo <= C`（C ≤ 2）的**早退**。

        为什么 `C ≤ 2`（`<` 时 3）：叶子区间的长度必须 ≥ 1，才能保证
        "叶子数 ≤ hi - lo"；`< 3` 与 `<= 2` 都只让长度 0/1/2 停下。
        大于这个数也**不是**错，只是上界会变松——但那要多算一层常数，这里
        不猜，直接 ⊤。
        """
        for st in _descendants(body, "if"):
            cond = _paren_inner((st.children or [None])[0])
            if cond is None or getattr(cond, "kind", "") != "binary":
                continue
            op = getattr(cond, "value", "") or ""
            if op not in ("<", "<=", ">", ">="):
                continue
            kids = list(cond.children or [])
            if len(kids) < 2:
                continue
            for left, right, flip in ((kids[0], kids[1], False),
                                      (kids[1], kids[0], True)):
                if not self._is_halving_half_diff(_paren_inner(left), lo_nm, hi_nm):
                    continue
                c = _int_literal(_paren_inner(right))
                if c is None:
                    continue
                eff = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}[op] if flip else op
                if not ((eff == "<" and c <= 3) or (eff == "<=" and c <= 2)):
                    continue
                then = stmt_body(st)
                if then is not None and any(getattr(x, "kind", "") == "return"
                                            for x in (then.children or [])):
                    return True
        return False

    def _is_halving_half_diff(self, e, lo_nm: str, hi_nm: str) -> bool:
        """`hi - lo`（两操作数都是裸名）。"""
        if e is None or getattr(e, "kind", "") != "binary" or (e.value or "") != "-":
            return False
        kids = [_paren_inner(k) for k in (e.children or [])]
        return (len(kids) == 2
                and getattr(kids[0], "kind", "") == "path"
                and getattr(kids[1], "kind", "") == "path"
                and getattr(kids[0], "name", "") == hi_nm
                and getattr(kids[1], "name", "") == lo_nm)

    def _halving_span_at(self, fn: Node, call: Optional[Node]) -> Optional["Cost"]:
        """调用点上**区间长度** `hi - lo` 的 Cost（**调用方口径**）；推不出 → None。

        `(hi - lo)` 是被调函数**形参**上的尺寸量：κ 里留着它，调用方查不到
        （求值按 +∞，判定对不上），必须在调用点换成实参的区间
        （`merge_sort(xs, 0, xs.size())` → `n(xs)`）。
        """
        sp = self._halving_span(getattr(fn, "name", "") or "")
        if sp is None or call is None:
            return None
        lo_nm, hi_nm = sp
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        anode = next((c for c in (call.children or []) if c.kind == "args"), None)
        ps = list(pnode.children if pnode is not None else [])
        args = list(anode.children if anode is not None else [])
        pos = {getattr(p, "name", ""): i for i, p in enumerate(ps)}
        if lo_nm not in pos or hi_nm not in pos:
            return None
        i_lo, i_hi = pos[lo_nm], pos[hi_nm]
        if max(i_lo, i_hi) >= len(args):
            return None
        fn_here = self._cur_frame_fn()
        lo_c = self._span_endpoint(args[i_lo], fn_here)
        hi_c = self._span_endpoint(args[i_hi], fn_here)
        if lo_c is None or hi_c is None:
            return None
        return size_diff_cost(hi_c, lo_c,
                              self._requires_facts(getattr(fn_here, "name", "") or ""))

    def _span_endpoint(self, e, fn) -> Optional["Cost"]:
        """区间端点表达式 → 尺寸量；**只认精确形状**（`-` / `/` / `%` 一律 None）。

        为什么必须精确：区间长度是 `hi - lo`，**上界端点可以让它变大（安全），
        下界端点变大就是低估**（`lo` 记成 `max(a, 1)` 而真实是 `a-1` ⇒ κ 变小
        ⇒ 破界）。`_count_of_expr` 对 `-` / `/` / `%` 一律取左侧（过近似），
        所以那些形状在这里直接拒掉，只留字面量 / 裸名 / `.size()` / `+` / `*`。
        """
        e = _paren_inner(e)
        if e is None:
            return None
        k = getattr(e, "kind", "")
        if k == "lit-int":
            v = _int_literal(e)
            return None if v is None else Cost.num(v)
        if k == "binary":
            op = e.value or ""
            if op not in ("+", "*"):
                return None
            kids = list(e.children or [])
            if len(kids) < 2:
                return None
            l = self._span_endpoint(kids[0], fn)
            r = self._span_endpoint(kids[1], fn)
            if l is None or r is None:
                return None
            return Cost.add(l, r) if op == "+" else Cost.mul(l, r)
        return self._count_of_expr(e, fn)

    # ---------------------------------------------------------------- 按层求和
    #
    # **问题**（2026-11-12 夜，`01_sorting.merge_sort`）：区间对半分的递推是
    #
    #     T(s) = 2·T(s/2) + 单层(s)                （s = hi - lo）
    #     ⇒ T(s) = Σ_{k<K} [2^k·A + B·s] = A·(2^K - 1) + B·s·K
    #     ⇒ Θ(s log s)
    #
    # 而修前记的是「激活数 × 单层」，**每一层都乘入口区间的长度**：
    # `2s × (A + B·s) = O(s²)`。后果不是"松一点"，是**把正确的契约判成债务**：
    # 作者写 `O(n log n)`（正确）会得到「阶不够」——比 ⊤ 更坏（⊤ 至少说"判不了"）。
    #
    # **修法两步**：
    #   ① 单层成本必须是**只随区间长度走**的（不能含 `lo` / `hi` 这类绝对下标）：
    #      区间对半分的帧里调用一个"只碰本区间"的函数时，实参就是本帧的端点
    #      `lo ≤ mid ≤ hi`（这一步在调用点**证出来**，见 `_halving_callee_plan`），
    #      于是被调方内部的差值 `mid - lo` / `hi - mid` 成立，且每个差值 ≤ `hi - lo`；
    #   ② 把单层拆成 `A + B×s`（`_split_span_affine`）后**逐层求和**
    #      （`_halving_level_sum`）。
    #
    # **n 叉递归（`place` 那种）不被这条覆盖**：`for col in 0..n { place(row+1,n) }`
    # 每层的激活数是 n 而不是 2，`Σ_k n^k` 是指数；`_halving_span` 的判据要求
    # "恰好 2 个自调用点、拿互不相交的两半、有 `hi - lo < C` 的早退"，它一条都
    # 过不了 ⇒ 仍然 ⊤。这里不加任何"通用递归求和"的口子。

    def _note_diff_origin(self, d: Cost, hi_c: Cost, lo_c: Cost) -> None:
        """记下尺寸差符号的来源（`mid - lo` → 两个操作数），供调用点实例化。"""
        if getattr(d, "kind", "") != "sym" or not getattr(d, "value", ""):
            return
        self._diff_origin.setdefault(str(d.value), (hi_c, lo_c))

    def _halving_mid_name(self, name: str) -> Optional[str]:
        """区间对半分里 `mid` 的局部名（与 `_halving_span_impl` 同一份判据）。"""
        if name in self._halving_mid_cache:
            return self._halving_mid_cache[name]
        out = self._halving_mid_name_impl(name)
        self._halving_mid_cache[name] = out
        return out

    def _halving_mid_name_impl(self, name: str) -> Optional[str]:
        sp = self._halving_span(name)
        fn = self.fns.get(name)
        if sp is None or fn is None:
            return None
        lo_nm, hi_nm = sp
        body = next((c for c in (fn.children or []) if c.kind == "block"), None)
        if body is None:
            return None
        calls: List[tuple] = []
        self._collect_self_calls(body, name, [], calls)
        if len(calls) != 2:
            return None
        nodes = [c for c, _g in calls]
        argv = []
        for c in nodes:
            an = next((x for x in (c.children or []) if x.kind == "args"), None)
            a = list(an.children if an is not None else [])
            if len(a) != 3:
                return None
            argv.append(a)
        alias = self._halving_aliases(body, lo_nm, hi_nm, nodes)
        pair = self._halving_pair(argv[0], argv[1], lo_nm, hi_nm, alias)
        if pair is None:
            return None
        return getattr(_paren_inner(pair[0]), "name", "") or None

    def _halving_callee_plan(self, callee: str,
                             node: Optional[Node]) -> Optional[dict]:
        """区间对半分的帧里的一次调用 → `{facts, levels, span}`；条件不齐 → None。

        三条前提（缺一即 None，**不猜**）：

          · 当前帧确实是区间对半分的递归（`_halving_span`：两个端点只读、
            `mid` 是中点、有 `hi - lo < C` 的早退 ⇒ 递归路径上 `hi - lo ≥ 2 > 0`）；
          · 这次调用的**每一个标量实参**都是本帧的端点（`lo` / `mid` / `hi`
            或它们的只读别名），其余形参是容器；
          · 端点序列**非降**（`lo ≤ mid ≤ hi`）——所以这三个值真的构成区间前提。

        返回的 `facts` 交给被调方的 `requires` 用（`_facts_extra`）：形参 i → 所有
        级别更高的形参。**为什么需要它**：`merge_halves` 的
        `while (i < mid && j < hi)` 里 `i` 从 `lo` 起、`j` 从 `mid` 起，轮数是
        `mid - lo` / `hi - mid`；没有 `lo ≤ mid ≤ hi`，这两个差值退化成**绝对
        下标** `mid` / `hi`——把它们乘上激活数就是 O(n²)（见上面「按层求和」）。
        """
        caller = self._cur_frame_fn()
        if node is None or caller is None:
            return None
        cname = getattr(caller, "name", "") or ""
        sp = self._halving_span(cname)
        if sp is None:
            return None
        lo_nm, hi_nm = sp
        mid_nm = self._halving_mid_name(cname)
        if not mid_nm:
            return None
        lvl_of = {lo_nm: 0, mid_nm: 1, hi_nm: 2}
        fn = self.fns.get(callee)
        if fn is None:
            return None
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        anode = next((c for c in (node.children or []) if c.kind == "args"), None)
        ps = list(pnode.children if pnode is not None else [])
        args = list(anode.children if anode is not None else [])
        if not ps or len(args) != len(ps):
            return None
        body = next((c for c in (caller.children or []) if c.kind == "block"), None)
        if body is None:
            return None
        calls: List[tuple] = []
        self._collect_self_calls(body, cname, [], calls)
        _self_nodes = [c for c, _g in calls]
        # **三个端点必须只读**：被改写过的 `lo` / `hi` / `mid` 在调用点就不再是
        # "区间端点"，`lo ≤ mid ≤ hi` 这三条事实也就不成立。形状判据本来就默认
        # 它们不变（激活数上界也靠这一点），这里把它写成**显式**前提。
        for _nm in (lo_nm, mid_nm, hi_nm):
            if not self._scalar_readonly(body, _nm, _self_nodes):
                return None
        alias = self._halving_aliases(body, lo_nm, hi_nm, _self_nodes)
        levels: Dict[str, int] = {}
        seq: List[int] = []
        for p, a in zip(ps, args):
            ty = p.children[0] if p.children else None
            if ty is None:
                return None
            if _container_type_name(ty) is not None:
                continue                     # 容器形参不是区间端点
            if _is_ref_type(ty) or self._type_parts(ty)[0] in self.structs:
                return None                  # 按引用/结构体：改得到，不认
            a2 = _paren_inner(a)
            nm = (getattr(a2, "name", "") or ""
                  if getattr(a2, "kind", "") == "path" else "")
            nm = alias.get(nm, nm)
            if nm not in lvl_of:
                return None
            pn = getattr(p, "name", "") or ""
            if not pn:
                return None
            levels[pn] = lvl_of[nm]
            seq.append(lvl_of[nm])
        if len(seq) < 2:
            return None
        if any(seq[i] > seq[i + 1] for i in range(len(seq) - 1)):
            return None
        facts: Dict[str, set] = {}
        for pn, lv in levels.items():
            facts[pn] = {q for q, lq in levels.items() if lq > lv}
        span = Cost("sym", size_diff_name(Cost("sym", hi_nm), Cost("sym", lo_nm)))
        return {"facts": facts, "levels": levels, "span": span}

    def _resolve_halving_diffs(self, cost: Cost, fn: Node, plan: dict) -> Cost:
        """把被调方 κ 里的**形参差值**换成调用方的区间长度 `s = hi - lo`。

        `merge_halves` 在 `lo ≤ mid ≤ hi` 下的 κ 里有 `mid - lo` / `hi - mid`
        （`_diff_origin` 记着来源）。调用点是区间对半分的帧，两端点都落在
        `[lo, hi]` 里 ⇒ **任何两个端点的差 ≤ `hi - lo`**，所以逐个换成 `s`
        （每个都 ≤ s，是上界；比按 `s/2` 记松一倍常数，不影响阶）。
        """
        if not self._diff_origin:
            return cost
        params = self._scalar_params_of(fn)
        if not params:
            return cost
        table: Dict[str, Cost] = {}
        for nm in cost.variables():
            org = self._diff_origin.get(nm)
            if org is None:
                continue
            a = _cost_symbol_name(org[0])
            b = _cost_symbol_name(org[1])
            if not a or not b or a not in params or b not in params:
                continue
            table[nm] = plan["span"]
        return cost.substitute_syms(table) if table else cost

    def _split_span_affine(self, c: Optional[Cost],
                           span: str) -> Optional[Tuple[Cost, Cost]]:
        """单层成本 → `(A, B)`，使 `c ≤ A + B × s`（`s` = 区间长度符号名）。

        拆不出（`s` 出现在非线性位置：`s×s`、`log2(s+1)`、下标成 `max` 之外的
        未知 kind、⊤）→ None，调用方**退回旧口径**（宁可松，不许破界）。

        每一步都只做**保持上界**的变形：

          · `add` 逐项；`max(a,b)` → `(max(Aa,Ab), max(Ba,Bb))`
            （`max` 的两侧各自 ≤ 自己的 `A+B·s`，取大仍是上界）；
          · `mul` **至多一边含 `s`**（两边都含 = `s²`，那不是"每层线性"）；
          · `div` 只认**正常数分母**；
          · 其余：含 `s` → None；不含 `s` → 整项进 `A`（每帧都要付的常数）。
        """
        if c is None or c.is_top():
            return None
        zero, one = Cost.num(0), Cost.num(1)
        k = c.kind
        if k == "num":
            return (c, zero)
        if k in ("sym", "size"):
            return (zero, one) if str(c.value or "") == span else (c, zero)
        if k == "add":
            parts = [self._split_span_affine(a, span) for a in c.args]
            if any(p is None for p in parts):
                return None
            A = zero
            B = zero
            for pa, pb in parts:            # type: ignore[misc]
                A = Cost.add(A, pa)
                B = Cost.add(B, pb)
            return (A, B)
        if k == "max":
            parts = [self._split_span_affine(a, span) for a in c.args]
            if any(p is None for p in parts):
                return None
            A, B = parts[0]                 # type: ignore[misc]
            for pa, pb in parts[1:]:        # type: ignore[misc]
                A = Cost.max(A, pa)
                B = Cost.max(B, pb)
            return (A, B)
        if k == "mul":
            pa = self._split_span_affine(c.args[0], span)
            pb = self._split_span_affine(c.args[1], span)
            if pa is None or pb is None:
                return None
            if not _is_zero_cost(pa[1]) and not _is_zero_cost(pb[1]):
                return None                  # `s × s`：不是每层线性
            A = Cost.mul(pa[0], pb[0])
            B = Cost.add(Cost.mul(pa[0], pb[1]), Cost.mul(pa[1], pb[0]))
            return (A, B)
        if k == "div":
            den = c.args[1]
            if not (getattr(den, "kind", "") == "num" and den.value):
                return None
            pa = self._split_span_affine(c.args[0], span)
            if pa is None:
                return None
            return (Cost.div(pa[0], den), Cost.div(pa[1], den))
        if span in set(c.variables()):
            return None
        return (c, zero)

    def _halving_level_sum(self, once: Cost, s: Cost, span: str) -> Optional[Cost]:
        """**按层求和**：`Σ_{k<K} [2^k·A + B·s] ≤ (2s+1)·A + B·s·(log2(s+1)+1)`。

        推导（写进 κ 的那一条，不是"假设"）：

          · 第 k 层的激活拿的是**互不相交**的子区间，长度和 ≤ `s`
            ⇒ 第 k 层的单层成本 ≤ `2^k·A + B·s`（激活数 ≤ `2^k`）；
          · 递归到 `hi - lo < 2` 为止 ⇒ 层数 `K = ceil(log2 s)`；
          · `2^K ≤ 2(s+1)` ⇒ `Σ 2^k = 2^K − 1 ≤ 2s + 1`；
          · `K ≤ log2(s) + 1 ≤ log2(s+1) + 1`。

        **两个 `+1` 就是 `ceil` 的余量**：`(2s+1)` 盖住"层数是向上取整"，
        `log2(s+1) + 1` 盖住 `K ≤ log2(s)+1`。写成 `2s` / `log2(s)` 会在
        `s = 1` 少算一层（真值 1 层、`log2(1)=0`），并在 `s = 0` 求值成 −∞。
        """
        sp = self._split_span_affine(once, span)
        if sp is None:
            return None
        A, B = sp
        head = Cost.mul(Cost.add(Cost.mul(Cost.num(2), s), Cost.num(1)), A)
        body = Cost.mul(Cost.mul(B, s),
                        Cost.add(Cost.log2(Cost.add(s, Cost.num(1))),
                                 Cost.num(1)))
        return Cost.add(head, body)

    def _note_tree_rec(self, name: str) -> None:
        """记下「κ 为什么是 ⊤」——只给报告用，不参与算术。

        **只记被调函数自己**，不往调用者身上写：`_call` 里 G1 的
        `self.cost_of(short)` 探针会把 `cur_fn` 改成被调方（旧行为），
        按 `cur_fn` 归因会把原因贴到**别的函数**上。

        **两种 ⊤ 的原因不同，文案必须分开**（2026-11-10）：
          · `per_path >= 2` / 自调用在轮数推不出 ≤ 1 的循环里 → 真的是**树递归**：
            调用树规模不是「深度 × 单次成本」，指数被记成线性会让 κ 小于实测；
          · `per_path == 0` → **互递归**（`a↔b`，或经函数值转发）：这条边在
            调用环上，但按名字在 `name` 的函数体里数不到自调用点，
            「一次」的模型本身不贴形状。

        两种都退 ⊤（判定不变）。以前两种情况共用树递归那一句，读者会去
        `name` 的函数体里找一个不存在的「多个自调用点」。
        """
        per_path, bad_loop = self._self_call_shape(name)
        if per_path >= 2 or bad_loop:
            # **两种树形状分开写**：`place` 是一条路径 1 个自调用点、但在
            # `for col` 里（每层 n 次）；`merge_sort` 是一条路径 2 个自调用点。
            # 都写「多个自调用点」会让读者去 `place` 里找第二个调用点。
            if per_path >= 2:
                _why = f"一条执行路径上有 {per_path} 个自调用点"
            else:
                _why = "自调用落在轮数上界推不出 ≤ 1 的循环里（每层可能跑多次）"
            self.top_reasons[name] = (
                f"`{name}` 是树递归：{_why}，"
                f"调用树规模不是「深度 × 单次成本」（指数被记成线性会让 κ 小于实测）"
                f" ⇒ 退 ⊤，不猜")
            return
        # `per_path == 0`：环上的另一侧才是自调用点。`frames` 是当前遍历栈，
        # 去掉自己就是环上这一侧的函数（去重，栈里可能已经绕了一圈）。
        seen = set()
        others = []
        for f in self.frames:
            if f and f != name and f not in seen:
                seen.add(f)
                others.append(f)
        who = "、".join(f"`{f}`" for f in others) or "调用环上的其它函数"
        self.top_reasons[name] = (
            f"`{name}` 与 {who} 互递归：自调用点在环上（按名字在 `{name}` 的"
            f"函数体里数不到），规模推不出 ⇒ 退 ⊤，不猜")

    def _collect_repeats(self) -> None:
        for name, sp in self.specs.items():
            body = next((c for c in sp.children if c.kind == "spec-body"), None)
            for c in (body.children if body else []):
                if c.kind == "clause" and (c.name or "") == "repeats":
                    if c.children:
                        # **`allow_add=True` 只在这里**：repeats 要能写
                        # `log(hi - lo) + 1`（严格圈数上界）。比较/守卫那条
                        # 入口仍保持 `+` 不可推，见 `bound.expr_to_bound`。
                        self.repeats[name] = expr_to_bound(
                            c.children[0], allow_add=True)
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
        """取函数的 `target` 里某个维度（`time` / `allocation`）的 max 表达式；没有则 None。

        **"没有 target" 与 "写了但认不出" 是两件事**：后者会登记到
        `target_errors`（`check` 出 `E-Target-BadForm`、`cost` 打印错误），
        不再被上层当成"没写"而静默丢弃。
        """
        sp = self.specs.get(name)
        if sp is None:
            return None
        self._target_fn = name
        body = next((c for c in sp.children if c.kind == "spec-body"), None)
        for c in (body.children if body else []):
            if c.kind == "clause" and (c.name or "") == "target":
                got = self._find_slot(c, dim, name)
                if got is not None:
                    _m = self._len_alias(name)
                    if _m:
                        got = _rename_sizes(got, _m)
                    return got
        return None

    def target_failed(self, name: str, dim: Optional[str] = None) -> bool:
        """这个函数的 target 是不是**写了却认不出**（`target_errors` 里有它）。"""
        return any(e.get("fn") == name and (dim is None or e.get("dim") == dim)
                   for e in self.target_errors)

    def _find_slot(self, target: Node, dim: str, fn_name: Optional[str] = None) -> Optional[Cost]:
        """取某个维度里**第一个认得出的**槽位；认不出的逐个登记。

        **每个槽位都要看**：以前 `return` 在第一处非 None 就退出，
        于是 `min`/`max` 并存时后面那个写坏了也看不见——而它同样是承诺。
        """
        got: Optional[Cost] = None
        for d in _descendants(target, "dim"):
            if (d.name or "") != dim:
                continue
            for slot in _descendants(d, "slot"):
                if not slot.children:
                    continue
                # **每槽位清一次**（2026-11-10）：`_target_cost` 只在自己失败时
                # 留一句具体原因，不按槽位清就会把上一个槽位的理由报给下一个。
                self._target_hint = None
                one = self._target_cost(slot.children[0])
                if one is None:
                    self._note_target_error(fn_name, dim, slot)
                elif got is None:
                    got = one
        return got

    def _note_target_error(self, fn_name: Optional[str], dim: str, slot: Node) -> None:
        """登记一个"写了却认不出"的 target 槽位（按 line/col/slot 去重）。"""
        node = slot.children[0] if slot.children else None
        # **位置取槽位自己的**（不是表达式子节点的）：`check` 用 `slot.line/col`
        # 去配对诊断，两边对不上就会"登记了却报不出来"（踩过）。
        line = getattr(slot, "line", 0) or getattr(node, "line", 0)
        col = getattr(slot, "col", 0) or getattr(node, "col", 0)
        key = (fn_name, dim, slot.name, line, col)
        if key in self._target_error_keys:
            return
        self._target_error_keys.add(key)
        expr = _expr_text(node)
        # **具体原因优先**（2026-11-10）：`_target_cost` 在"尺寸差方向证明不了"
        # 这类情形下知道自己为什么失败，通用提示说不出这个。用掉即清，
        # 免得把上一槽位的原因贴到下一个上（两个槽位紧挨着）。
        _why = self._target_hint
        self._target_hint = None
        message = (f"target.{dim}.{slot.name or ''} 的成本式 `{expr}` 认不出：{_why}"
                   if _why else
                   self._target_error_message(dim, slot.name or "", node, expr))
        self.target_errors.append({
            "fn": fn_name or "", "dim": dim, "slot": slot.name or "",
            "line": line, "col": col, "expr": expr,
            "message": message,
        })

    def _target_error_message(self, dim: str, slot_name: str, node, expr: str) -> str:
        """给"认不出"的 target 一句**能照着改**的话。"""
        inner = _unwrap_obig(node)
        hint = ""
        if inner is not None and getattr(inner, "kind", "") == "path":
            nm = inner.name or ""
            if nm in self._size_bind_table():
                rhs = _expr_text(self._size_bind_table()[nm])
                return (f"target.{dim}.{slot_name} 引用了 `sizes` 绑定的 `{nm}`，"
                        f"但它的右侧 `{rhs}` 认不出。`sizes` 只能绑**形状量**："
                        f"形参名 / 容器形参的 `.length()` / 整数 / "
                        f"它们的 `+ * min max`（规格 §7.2 G1-b）")
            if nm == "n":
                conts = self._container_param_names()
                hint = ("；`n` 要能绑定：形参里**恰好一个容器**（指它的长度），"
                        "或 `n` 本身是形参")
                if len(conts) > 1:
                    hint += (f"（当前有 {len(conts)} 个容器形参："
                             + "、".join(conts) + "，`n` 指哪个不唯一）")
            elif nm:
                hint = f"；`{nm}` 既不是形参，也不是模块级常量"
        return (f"target.{dim}.{slot_name} 的成本式 `{expr}` 认不出{hint}。"
                f"支持：整数 / 形参名 / 容器尺寸（`xs.length()`，或恰好一个容器形参时的 `O(n)`）"
                f" / `log(...)` / `k(f)` / 四则运算（`+ - * /`）"
                f" / **尺寸差**（`hi - lo`，须能证明 `lo <= hi`）"
                f" / 幂（`n ** 2` / `n ^ 2`、`n ** 0.5`、`n ** (2/3)`，指数须是**常数**；"
                f"底数可以是字段链，如 `recv.n_ ^ 8`）"
                f" / 分数次幂（`sqrt(x)`、`root(x, q)`、`pow(x, p/q)`，如 `pow(n, 2/3)`）"
                f"。**除法只认 `n / log(n)`、`n / 2` 这类**：分母是另一个尺寸量"
                f"（`n / m`）或分母为 0 时判不了阶，会退 ⊤")

    def _spec_params(self) -> List[Node]:
        """当前正在求 target 的那个 spec 的形参节点列表。"""
        sp = self.specs.get(getattr(self, "_target_fn", "") or "")
        if sp is None:
            return []
        for c in sp.children:
            if getattr(c, "kind", "") == "params":
                return list(c.children)
        return []

    def _only_container_param(self) -> Optional[str]:
        """形参里**恰好一个容器**时返回它的名字（`O(n)` 的绑定目标）。"""
        conts = self._container_param_names()
        return conts[0] if len(conts) == 1 else None

    def _container_param_names(self) -> List[str]:
        """当前 spec 的**容器形参**名字（按声明顺序）。

        `O(xs)` 里的裸名字要认成 `n(xs)`；`n` 本身是容器形参时也只指它自己，
        不许被"唯一容器"规则顶掉（形参名优先于绑定魔法）。
        """
        out: List[str] = []
        for p in self._spec_params():
            ty = next((c for c in p.children if c.kind in ("type-ref", "type-name")), None)
            if p.name and _is_container_type(ty):
                out.append(p.name)
        return out

    def _scalar_param_names(self) -> set:
        out = set()
        for p in self._spec_params():
            ty = next((c for c in p.children if c.kind in ("type-ref", "type-name")), None)
            if p.name and not _is_container_type(ty):
                out.add(p.name)
        return out

    def _requires_facts(self, fn_name: Optional[str] = None) -> Dict[str, set]:
        """**某个** spec 的 `requires` 事实：`{小: {大, …}}`（`小 <= 大`）。

        **这是尺寸差 `hi - lo` 能成立的全部依据**：只有 `requires` 里声明的
        顺序才作数，**不从用法推断**（`xs[hi]` 推不出 `hi < len`）——
        与 A1 的 `facts_of_spec` 是同一份口径（直接调它，不另写一套）。

        **名字必须由调用方给**（2026-11-10）：`target` 侧传 `_target_fn`，
        κ 侧传**正在遍历的那个函数**。以前只按 `_target_fn` 取，而它被
        `target_cost()` 设过之后就一直留着——`cost` 先预解全部 target，
        轮到 κ 时它指向**别的函数**，事实表张冠李戴（实测：`scan_range`
        的圈数因此停在 `hi`，判成「尺寸量不一致」）。
        找不到 spec（只给实现、没给 `.tu`）→ 空表 → 差值一律退 ⊤。
        """
        nm = fn_name or ""
        if not nm:
            return {}
        _extra = self._facts_extra.get(nm) or {}
        hit = self._facts_cache.get(nm)
        if hit is not None and not _extra:
            return hit
        out: Dict[str, set] = {}
        sp = self.specs.get(nm)
        if sp is not None:
            try:
                from .cost_check import facts_of_spec
                out = facts_of_spec(sp)
            except Exception:                          # noqa: BLE001
                out = {}
        if _extra:
            # **临时补充的事实**（只来自「本帧区间端点」的调用点，见
            # `_halving_callee_plan`）：与 `requires` 合并，但**不缓存**——
            # 它只在这一次调用成立，缓存下来会污染这个函数独立的 κ。
            out = {k: set(v) for k, v in out.items()}
            for _k, _v in _extra.items():
                out.setdefault(_k, set()).update(_v)
            return out
        self._facts_cache[nm] = out
        return out

    def _target_facts(self) -> Dict[str, set]:
        """**正在求 target 的那个 spec** 的 `requires` 事实（尺寸差要用）。"""
        return self._requires_facts(getattr(self, "_target_fn", "") or "")

    def _size_bind_table(self) -> Dict[str, Node]:
        """当前 target 所属 spec 的 `sizes` 绑定：**名字 → 右侧表达式**（G1-b）。

        **这是 spec 侧的名字，不是实现里的局部**：`sizes table = n + 1;` 只是把
        `table` 定义成 `n + 1` 的缩写（规格 §7.2：可绑定量 MUST 是能在 O(1) 内
        由形参求出的**形状量**）。实现里那个叫 `table` 的容器有多大，这条绑定
        既不知道也不检查——名字相同纯属巧合，不是引用。

        以前 `sizes` 能解析但**没有任何消费者**：写了 `sizes table = n + 1;`
        再在 target 里写 `O(table)`，`table` 会被当成一个自由的尺寸量
        （渲染成 `n(table)`），判定退化成 ⊤——**契约看着更精确，其实一个字
        没生效**。现在这里把绑定喂给 `_target_cost`。
        """
        fn = getattr(self, "_target_fn", "") or ""
        cached = self._size_binds_cache.get(fn)
        if cached is not None:
            return cached
        out: Dict[str, Node] = {}
        sp = self.specs.get(fn)
        body = next((c for c in (getattr(sp, "children", None) or [])
                     if getattr(c, "kind", "") == "spec-body"), None)
        for c in (getattr(body, "children", None) or []):
            if (getattr(c, "kind", "") != "clause"
                    or (getattr(c, "name", "") or "") != "sizes"):
                continue
            for b in (getattr(c, "children", None) or []):
                if (getattr(b, "kind", "") != "size-bind"
                        or not getattr(b, "name", "")):
                    continue
                if getattr(b, "children", None):
                    # 重复绑定由 checker 报 E-Sizes-Dup；这里取第一条，
                    # 保证 cost 侧行为稳定（不随 dict 顺序漂）。
                    out.setdefault(b.name, b.children[0])
        self._size_binds_cache[fn] = out
        return out

    def _size_bind_cost(self, name: str) -> Optional["Cost"]:
        """把一条 `sizes` 绑定展开成 `Cost`；认不出 / 环形绑定 → None（不猜）。"""
        expr = self._size_bind_table().get(name)
        if expr is None:
            return None
        if name in self._size_bind_stack:
            return None              # `sizes a = b, b = a;`：环，不猜
        self._size_bind_stack.append(name)
        try:
            return self._target_cost(expr)
        finally:
            self._size_bind_stack.pop()

    def _scalar_params_of(self, fn: Optional[Node] = None) -> set:
        """**实现侧**的标量形参名（与 `_scalar_param_names` 同集，只是换来源）。

        `bound_to_cost_inst` 用它把「循环上界里的 `dim`」渲染成裸符号 `dim`，
        这样 κ 与 target 的尺寸量集合才对得上（P1）。
        """
        fn = fn if fn is not None else getattr(self, "cur_fn", None)
        if fn is None:
            return set()
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        out = set()
        for p in (pnode.children if pnode is not None else []):
            ty = next((c for c in (p.children or [])
                       if c.kind in ("type-ref", "type-name")), None)
            if p.name and not _is_container_type(ty):
                out.add(p.name)
        return out

    def _size_of_path(self, name: str) -> Cost:
        """把变量名翻成尺寸量。

        `row` 是局部行别名时，尺寸取**外层容器**的名字（`n(m)`）——
        因为 `n(row)` 在求值时查不到，会退化成 1，整个二维成本式失真。
        """
        if name in self.row_of:
            return Cost.size(self.row_of[name])
        return Cost.size(name)

    # ---------------------------------------------------------------- 值语义：分类与判定
    #
    # 这一段是 A-full 的**单一事实来源**（设计 §8.1）。四个入口：
    #   `_copy_class(ty)`    类型 → 拷贝代价的**类别**
    #   `_copy_cost / _move_cost`  类别 → (time, allocation) 两个维度
    #   `_value_class(expr)` 表达式 → prvalue / xvalue / lvalue / err
    #   `_type_of_expr(expr)` 表达式 → 声明类型节点（值类别与元素类型都要问它）
    #
    # **帧栈**：`_body` 与 `alloc_of` 各压一帧，给"这个名字声明的什么类型 /
    # 是不是 const / 是不是可隐式移动的局部"提供依据。

    def _unwrap_type(self, ty: Optional[Node]) -> Optional[Node]:
        """剥掉 `type` / `type-fail` / `type-ref` 外壳，拿到 `type-name`。"""
        while ty is not None:
            k = getattr(ty, "kind", "")
            if k in ("type", "type-fail", "type-ref") and ty.children:
                ty = ty.children[0]
                continue
            break
        return ty

    def _type_parts(self, ty: Optional[Node]):
        """类型节点 → `(head, 类型实参, 是否引用)`。

        `vector<u64>` → `("vector", [<u64>], False)`；
        `const vector<u64>&`（`param` 上的 `type-ref`）→ `(…, True)`。
        """
        ref = False
        while ty is not None:
            k = getattr(ty, "kind", "")
            if k == "type-ref":
                ref = True
                ty = ty.children[0] if ty.children else None
                continue
            if k in ("type", "type-fail") and ty.children:
                ty = ty.children[0]
                continue
            break
        if ty is None or getattr(ty, "kind", "") != "type-name":
            return "", [], ref
        args_node = next((c for c in (ty.children or [])
                          if getattr(c, "kind", "") == "type-args"), None)
        args = [c for c in ((args_node.children if args_node is not None else []) or [])
                if getattr(c, "kind", "") in ("type-name", "type-ref")]
        return canon(ty.name or ""), args, ref

    def _recv_head(self, recv: Optional[Node]) -> str:
        """接收者的**声明类型头名**（过 `canon`）；认不出 → `""`。

        **为什么不只看 `self.vty`**：那张表只装 `const-decl`/`var-decl` 的局部，
        形参（`const vector<u64>& xs`）根本不在里面。`find` / `insert` / `erase`
        的接收者**通常就是形参**，只查 `vty` 会让尺寸项整个消失——那正是
        这次要修的 κ 低估。`_type_of_expr` 走 `_frame_decls()`，形参与局部都收。
        """
        if recv is None:
            return ""
        h, _args, _ref = self._type_parts(self._type_of_expr(recv))
        if h:
            return h
        if getattr(recv, "kind", "") == "path" and getattr(recv, "name", None):
            return canon(self.vty.get(recv.name, ""))
        return ""

    def _size_term_of_recv(self, recv: Optional[Node], head: str):
        """容器操作的**尺寸项**（有序 map/set → `log2(n(recv))`，其余 → `n(recv)`）。

        接收者不是**裸名字**时不猜：`f().find(v)` / `s.xs.find(v)` 的长度写不出
        符号量，返回 `None`（调用方按原来的常数记，保守方向见报告"未做到"）。
        """
        if not (getattr(recv, "kind", "") == "path"
                and getattr(recv, "name", None)):
            return None
        nm = recv.name
        if head in ("map", "set"):
            return Cost.log2(Cost.size(nm))
        if head == "array":
            # 定长数组的尺寸是**容量 N**（编译期常量）：写 `n(a)` 会让判定
            # 去找一个根本不会出现的尺寸量（E23），而 N 是已知的。
            _cap = self._array_cap(self._type_of_expr(recv))
            if _cap is not None:
                return Cost.num(_cap)
        return Cost.size(nm)

    def _hash_len_term_hashmap(self, recv: Optional[Node], key: Optional[Node]):
        """stdlib `HashMap` 自由函数（`map_put/get/has`）的**容器键长度项**。

        与语言 `unordered_*` 的口径相同：键类型是 `vector` / `array` 时，
        哈希是 O(键长)，κ 加 `n(键表达式)`。标量键（`HashMap<u64,u64>`）→ None，
        既有 κ 逐字节不变（`test_map_cost` 的 `free_put` 仍含 `log2`）。
        """
        if recv is None or key is None:
            return None
        _h, args, _r = self._type_parts(self._type_of_expr(recv))
        if _h != "HashMap":
            return None
        _key_ty = args[0] if args else None
        _kh, _kargs, _kr = self._type_parts(_key_ty)
        if _kh not in ("vector", "array", "fixedvec"):
            return None
        _c = self._container_expr_size(key)
        return _c if _c is not None else Cost.top()

    def _hash_len_term(self, key: Optional[Node], recv: Optional[Node],
                       head: str):
        """**容器键的哈希长度项** `O(n(key))`；标量键 → `None`。

        2026-11-12：`unordered_map<vector<u8>, u64>` / `unordered_set<vector<T>>`
        的键可以做，但哈希要**读完整个键**——单次 `get/insert/remove` 是
        `O(n(表) + n(键))`，不是 O(1)。这里只给 `unordered_*` 加；有序
        `map` 的键比较也有 O(键长)，但那批 κ 已由 `log2(n(m))` 记录且不属本次
        变更（改它会让既有 golden 变动）。

        键表达式推不出长度时返回 ⊤（**宁可 ⊤，不许假装 O(1)**），并**记一句原因**
        ——报告里要能看出是哪一项的尺寸推不出，否则读者只看到 ⊤。
        """
        if head not in ("unordered_map", "unordered_set") or key is None:
            return None
        _h, args, _r = self._type_parts(self._type_of_expr(recv))
        _key_ty = args[0] if args else None
        _kh, _kargs, _kr = self._type_parts(_key_ty)
        if _kh not in ("vector", "array", "fixedvec"):
            return None
        _c = self._container_expr_size(key)
        return _c if _c is not None else self._unknown_size(
            f"容器键 `{_expr_text(key)}` 的长度推不出（`{head}` 的键是序列，"
            f"哈希要读完整个键）⇒ 这一项退 ⊤，不猜")

    def _assoc_write_target(self, lhs: Optional[Node]):
        """`m[k]`（接收者是 `map` / `unordered_map`）→ `(recv, key, head)`；否则 None。

        **`m[k] = v` 与 `m.insert(k, v)` 是同一个程序**（都"存在则覆盖"），
        D1 等价性守卫要求两者 κ **逐字节相同**。这里只负责认出目标，
        成本式由 `_assoc_index_write_cost` 与 method-call 的 insert 分支**共用**。
        """
        if lhs is None or getattr(lhs, "kind", "") != "index":
            return None
        recv = lhs.children[0] if lhs.children else None
        key = lhs.children[1] if len(lhs.children or []) > 1 else None
        if recv is None or key is None:
            return None
        head = self._recv_head(recv)
        if head not in ("map", "unordered_map"):
            return None
        return recv, key, head

    def _array_cap(self, ty: Optional[Node]) -> Optional[int]:
        """`array<T, N>` 的 N（模块级常量也能解）；解不出 → None。"""
        return type_capacity_inst(self._unwrap_type(ty), self.const_values)

    def _copy_class(self, ty: Optional[Node], depth: int = 0):
        """拷贝代价的**类别**（设计 §4.1 / §5.1）：

        | 返回 | 含义 |
        |---|---|
        | `("scalar", 0)` | 标量 / enum / 认不出的值类型 → 0（见文件头的登记口径） |
        | `("array", N)` | 定长数组：逐元素 N 次，0 分配 |
        | `("array-owning", N)` | 元素是 owning 的定长数组（嵌套） |
        | `("struct", k)` | 结构体：k 个成员逐个 |
        | `("struct-owning", k)` | 含 owning 成员的结构体 |
        | `("owning", head)` | vector / map / set / stack / queue …：n 个元素 + 分配 |
        """
        if ty is None or depth > 8:
            return ("scalar", 0)
        h, args, ref = self._type_parts(ty)
        if ref:
            return ("scalar", 0)          # 引用 = 别名：被引用方自己不是拷贝
        if h in _OWNING_HEADS:
            return ("owning", h)
        if h in _ARR_HEADS:
            inner = args[0] if args else None
            if self._copy_class(inner, depth + 1)[0] in (
                    "owning", "array-owning", "struct-owning"):
                return ("array-owning", self._array_cap(ty))
            return ("array", self._array_cap(ty))
        if h in self.structs:
            fields = self.structs[h]
            for _fty in fields.values():
                if self._copy_class(_fty, depth + 1)[0] in (
                        "owning", "array-owning", "struct-owning"):
                    return ("struct-owning", len(fields))
            return ("struct", len(fields))
        return ("scalar", 0)

    def _nested_owning(self, ty: Optional[Node]) -> bool:
        """容器/数组的元素本身是不是 owning（嵌套 → 内层尺寸写不出）。"""
        h, args, _r = self._type_parts(ty)
        if h in _ASSOC_HEADS:
            inner = args[1] if len(args) > 1 else None
        elif h in _SEQ_HEADS or h in _SET_HEADS or h in _ARR_HEADS:
            inner = args[0] if args else None
        else:
            return False
        return inner is not None and self._copy_class(inner)[0] in (
            "owning", "array-owning", "struct-owning")

    def _elem_type(self, ty: Optional[Node]) -> Optional[Node]:
        """容器的**元素类型节点**（map → V；set/vector/array → T）。"""
        h, args, _r = self._type_parts(ty)
        if h in _ASSOC_HEADS:
            return args[1] if len(args) > 1 else None
        if h in _SEQ_HEADS or h in _SET_HEADS or h in _ARR_HEADS:
            return args[0] if args else None
        return None

    def _copy_cost(self, ty: Optional[Node],
                   src: Optional[str] = None):
        """一次**拷贝**的 `(time, allocation)`（设计 §5.1 的 Cost 表达式）。

        `src` 是拷贝源的尺寸符号名（`_size_symbol_of` 给出）；**写不出就是 None**，
        此时容器拷贝的 time 记 ⊤——不猜内层长度（设计 §5.2 决定 D7）。
        """
        kind = self._copy_class(ty)
        c = kind[0]
        if c == "scalar":
            return Cost.num(0), Cost.num(0)
        if c == "struct":
            return Cost.num(kind[1]), Cost.num(0)
        if c == "array":
            n = kind[1]
            return (Cost.num(n) if isinstance(n, int) else Cost.num(1)), Cost.num(0)
        if c == "array-owning":
            n = kind[1]
            nn = Cost.num(n) if isinstance(n, int) else Cost.num(1)
            # 元素是 owning：内层长度写不出 → time 取 ⊤；alloc `1 + N` 仍是上界
            # （元素容器个数 ≤ N，每个 1 次）。
            return Cost.top(), Cost.add(Cost.num(1), nn)
        if c == "struct-owning":
            # 逐成员尺寸在成本域里**没有符号可写**（没有 `n(结构体)` 这个量）。
            # 宁可 ⊤ 不猜；语料里 0 例（设计 §9.1）。
            return Cost.top(), Cost.top()
        head = kind[1]
        n = Cost.size(src) if src else None
        if head in _ASSOC_HEADS:
            # **D6**：`map`/`set` 的拷贝 = **结构克隆**，O(n) + n 次节点分配
            # （后端 `std::map` 的拷贝算法是线性的；【实测】见设计 §5.3）。
            # **若实现改成"逐个 insert 重建"，这里必须改记 n log n**——否则低估。
            time = n if n is not None else Cost.top()
            return time, time
        if head in _SET_HEADS:
            time = n if n is not None else Cost.top()
            return time, time
        time = (Cost.add(n, Cost.num(1)) if n is not None else Cost.top())
        if self._nested_owning(ty):
            time = Cost.top()             # 内层长度写不出（§5.2）
        return time, Cost.num(1)

    def _move_cost(self, ty: Optional[Node]) -> Cost:
        """一次**隐式移动**的 time（分配恒 0，见设计 §6.2）。"""
        kind = self._copy_class(ty)
        c = kind[0]
        if c in ("array", "array-owning"):
            n = kind[1]
            return Cost.num(n) if isinstance(n, int) else Cost.num(1)
        if c in ("struct", "struct-owning"):
            return Cost.num(kind[1])
        return Cost.num(0)                # owning 容器 / 标量

    def _size_symbol_of(self, e: Optional[Node],
                        fallback: str = "") -> Optional[str]:
        """拷贝源的**尺寸符号名**；写不出 → None（调用方按 ⊤ 处理）。"""
        while e is not None and getattr(e, "kind", "") in ("paren", "try"):
            e = e.children[0] if e.children else None
        if e is None:
            return fallback or None
        k = getattr(e, "kind", "")
        if k == "path":
            nm = e.name or ""
            return self.row_of.get(nm, nm) or None
        if k == "field":
            # 结构体字段作为拷贝源：长度写成 `n(s.v)`（2026-11-12）。
            # 以前这里一律回退到目标变量名，`vector<u64> y = s.v;` 会记成
            # `n(y)` —— 一个"被拷贝方自己的尺寸"，不是可证明的上界来源。
            return field_chain_name(e) or fallback or None
        if k in ("index", "method-call"):
            # 元素/字段是容器时，内层长度没有符号（除非 row_of 绑过）。
            return fallback or None
        return fallback or None

    def _fresh_builtin(self, name: str) -> bool:
        """返回**新容器**的内建（`text_split_vec` 之类）→ prvalue。

        **用户函数优先**：同名用户函数在 `_value_class` 里由 `fn_ret` 先处理。
        走到这里还撞上同名用户函数（返回类型缺失这种边角）时，按"**不是**新容器"
        处理——保守方向：多记一次拷贝，不会低估。
        """
        if name in self.fns:
            return False
        try:
            return stdlib.ret_kind(name) in _FRESH_RET_KINDS
        except Exception:                                  # noqa: BLE001
            return False

    def _cur_frame(self) -> Dict[str, object]:
        return (self._frames[-1] if self._frames
                else {"decls": {}, "consts": set(), "locals": set(), "ret": None})

    def _frame_decls(self) -> Dict[str, Optional[Node]]:
        return self._cur_frame()["decls"]        # type: ignore[return-value]

    def _make_frame(self, fn: Optional[Node]) -> Dict[str, object]:
        """一个函数体（或候选体）的局部环境：名字 → 类型 / const / 可移动 / 返回类型。"""
        decls: Dict[str, Optional[Node]] = {}
        consts: set = set()
        locals_: set = set()
        if fn is not None:
            pnode = next((c for c in (fn.children or [])
                          if getattr(c, "kind", "") == "params"), None)
            for prm in (pnode.children if pnode is not None else []):
                ty = prm.children[0] if (prm.children or []) else None
                if getattr(prm, "name", None):
                    decls[prm.name] = ty
                    if not _is_ref_type(ty):
                        # 按值形参也是"可隐式移动的实体"（设计 §3.2 / §6.2）
                        locals_.add(prm.name)
            body = next((c for c in (fn.children or [])
                         if getattr(c, "kind", "") == "block"), None)
            for d in ((_descendants(body, "const-decl")
                       + _descendants(body, "var-decl")
                       + _descendants(body, "val-decl"))
                      if body is not None else []):
                if not getattr(d, "name", None) or not (d.children or []):
                    continue
                decls[d.name] = d.children[0]
                (consts if d.kind == "const-decl" else locals_).add(d.name)
        return {"decls": decls, "consts": consts, "locals": locals_,
                "ret": _ret_type_of(fn)}

    def _value_class(self, e: Optional[Node]) -> str:
        """表达式的值类别（设计 §3.2）：`prvalue` / `xvalue` / `lvalue` / `err`。

        `xvalue` **只在 `return` 语句里**换成移动；其它消费点一律按拷贝
        （C++ 的隐式移动只发生在 return / throw 上）。
        """
        if e is None:
            return "prvalue"
        k = getattr(e, "kind", "")
        if k in ("list-init", "array", "struct-literal", "lit-string", "lit-int",
                 "lit-char", "lit-float", "lit-bool"):
            return "prvalue"
        if k in ("paren", "try", "deref"):
            return self._value_class(e.children[0] if e.children else None)
        if k == "ref":
            return "lvalue"               # `&x` 只是"我知道这是借用"的标记
        if k == "path":
            nm = e.name or ""
            fr = self._cur_frame()
            if nm in fr["locals"] and nm not in fr["consts"]:   # type: ignore[operator]
                return "xvalue"           # 非 const 局部 / 按值形参 → 可隐式移动
            return "lvalue"               # 借用值（const&/& 形参、元素、const 局部…）
        if k == "call":
            name = (e.name or "").split(".")[-1]
            if name in ("Err", "Ok"):
                return "err"              # 失败分支：载荷不是返回值本体
            if not e.name:
                return "prvalue"          # `T::make()` / `T::filled()`
            ret = self.fn_ret.get(name)
            if ret is not None:
                return "lvalue" if _is_ref_type(ret) else "prvalue"
            fret = self._fnvalue_ret_type(name)      # 函数值形参 `g: func(..)->R`
            if fret is not None:
                return "lvalue" if _is_ref_type(fret) else "prvalue"
            return "prvalue" if self._fresh_builtin(name) else "lvalue"
        if k == "method-call":
            nm = (e.name or "").split(".")[-1]
            if nm in _VIEW_METHODS:
                return "prvalue"          # 新向量（元素由内建负责拷）
            return "prvalue" if nm in _MOVE_METHODS else "lvalue"
        return "lvalue"

    def _fnvalue_ret_type(self, name: Optional[str]) -> Optional[Node]:
        """`g(x)` 里 `g: func(A) -> R` 的 R 类型节点（认不出 → None）。

        没有它，`return g(xs)` 会被当成"借用值返回"而记一次容器拷贝，
        且尺寸写不出 → ⊤（`test_kappa_gaps` 的 host_v/host_vn 就是这样翻成 inf 的）。
        `type-fn` 的子节点是 `[type-fn-params, R]`。
        """
        ty = self._frame_decls().get(name or "")
        if getattr(ty, "kind", "") != "type-fn":
            return None
        kids = [c for c in (ty.children or [])
                if getattr(c, "kind", "") != "type-fn-params"]
        return kids[-1] if kids else None

    def _type_of_expr(self, e: Optional[Node]) -> Optional[Node]:
        """表达式的**声明类型节点**（只求类型，不求值；认不出 → None）。"""
        if e is None:
            return None
        k = getattr(e, "kind", "")
        if k == "path":
            return self._frame_decls().get(e.name or "")
        if k in ("paren", "try", "deref", "ref", "cast"):
            return self._type_of_expr(e.children[0] if e.children else None)
        if k == "index":
            return self._elem_type(
                self._type_of_expr(e.children[0] if e.children else None))
        if k == "field":
            base = self._type_of_expr(e.children[0] if e.children else None)
            h, args, _r = self._type_parts(base)
            if h in self.structs:
                return self.structs[h].get(e.name or "")
            if h in _SEQ_HEADS or h in _ARR_HEADS:
                return args[0] if args else None
            return None
        if k == "method-call":
            recv = self._type_of_expr(e.children[0] if e.children else None)
            nm = (e.name or "").split(".")[-1]
            if nm in _BORROW_METHODS or nm in _MOVE_METHODS:
                return self._elem_type(recv)
            return None
        if k == "call":
            _ret = self.fn_ret.get((e.name or "").split(".")[-1])
            if _ret is not None:
                return _ret
            return self._fnvalue_ret_type((e.name or "").split(".")[-1])
        return None

    def _iter_elem_type(self, it: Optional[Node]) -> Optional[Node]:
        """`for (x in <it>)` 的**元素类型节点**（`key`/`values` 也能解）。"""
        if it is None:
            return None
        if getattr(it, "kind", "") == "method-call":
            nm = (it.name or "").split(".")[-1]
            recv = self._type_of_expr(it.children[0] if it.children else None)
            h, args, _r = self._type_parts(recv)
            if nm == "keys" and h in _ASSOC_HEADS + _SET_HEADS:
                return args[0] if args else None
            if nm == "values" and h in _ASSOC_HEADS:
                return args[1] if len(args) > 1 else None
            if nm in ("items", "entries"):
                return None               # 元组：本项目语料里载荷都是标量
        return self._elem_type(self._type_of_expr(it))

    def _store_copy_cost(self, n: Optional[Node]):
        """`push/insert/…` 的**元素拷贝** `(time, allocation)`。

        只对**元素是 owning 容器**的入容器记（设计 §8.2 #6）；标量/array/struct
        元素不记（与影子模型一致，避免常数项大范围漂移）。
        `insert(k, v)` 在 map 上拷贝的是 V；键 K 的拷贝**没有建模**
        （语料里 0 例，记在设计报告的"未做到"里）。
        """
        if n is None:
            return Cost.num(0), Cost.num(0)
        nm = (n.name or "").split(".")[-1]
        if nm not in _STORE_METHODS:
            return Cost.num(0), Cost.num(0)
        recv = n.children[0] if (n.children or []) else None
        rty = self._type_of_expr(recv)
        args = []
        for c in (n.children or [])[1:]:
            if getattr(c, "kind", "") == "args":
                args.extend(c.children or [])
        if not args:
            return Cost.num(0), Cost.num(0)
        head = self._type_parts(rty)[0]
        if nm in ("insert", "put") and head in _ASSOC_HEADS and len(args) > 1:
            val = args[1]
        elif nm == "insert" and len(args) >= 3:
            # **三参 `insert(pos, count, value)`**（2026-11-13）：被拷贝的值是
            # **第 3 个**实参。旧实现取 `args[0]`（位置）——元素是 owning 容器时
            # 会把"位置表达式"当成拷贝源，尺寸项整个记错。语料里 0 例，但形状
            # 一经出现就是低估，所以在数量项一起修掉。
            val = args[2]
        elif nm == "assign" and len(args) >= 2:
            # `assign(n, v)`：值是第 2 个实参（同上，`args[0]` 是个数）。
            val = args[1]
        else:
            val = args[0]
        return self._store_copy_value(rty, val)

    def _store_copy_value(self, rty: Optional[Node], val: Optional[Node]):
        """**同一个存储位置**的值拷贝 `(time, allocation)`（`_store_copy_cost` 的核心）。

        单独抽出来，是因为 `m[k] = v` 必须与 `m.insert(k, v)` 走**同一份**
        判定（D1）：两边都调这一个函数，模板/尺寸符号就不可能漂。
        """
        ety = self._elem_type(rty)
        cc = self._copy_class(ety) if ety is not None else ("scalar", 0)
        if cc[0] not in ("owning", "array-owning", "struct-owning"):
            return Cost.num(0), Cost.num(0)
        if val is None or self._value_class(val) == "prvalue":
            return Cost.num(0), Cost.num(0)
        return self._copy_cost(ety, self._size_symbol_of(val))

    def _callee_entry_copy(self, fn: Optional[Node], call: Optional[Node]) -> "Cost":
        """调用点补记**被调函数按值形参**的拷贝（只在用 `target` 记账时需要）。

        为什么需要：`target` 是作者的承诺，写的时候是**解释器实测**口径
        （解释器不拷贝），所以它**不含**入口拷贝；而 C++ 里这次拷贝是调用方
        做的。用 κ 内联时入口已经记过（`_body`），这里只补 target 那条路。
        **宁可多算**：即使某个 target 已经把拷贝算进去，多记也不破界。
        """
        if fn is None:
            return Cost.num(0)
        T, _A = self._param_copy_cost(fn)
        if T.is_const() and T.value == 0:
            return T
        if call is not None:
            try:
                T = T.substitute_sizes(self._param_size_table(fn, call, derive=False))
            except Exception:                              # noqa: BLE001
                pass
        return T

    def _param_copy_cost(self, fn: Optional[Node]):
        """**按值形参**在函数入口的拷贝 `(time, allocation)`（每次调用一份）。

        记在**入口**而不是调用点：`_param_size_table` 会把 κ 里的 `n(形参)`
        换成实参尺寸，所以入口记一次就自动带上正确的尺寸符号；在调用点记
        要在 5 条调用路径各写一遍（设计 §8.2 #13）。
        """
        T = A = Cost.num(0)
        if fn is None:
            return T, A
        pnode = next((c for c in (fn.children or [])
                      if getattr(c, "kind", "") == "params"), None)
        for prm in (pnode.children if pnode is not None else []):
            ty = prm.children[0] if (prm.children or []) else None
            if ty is None or _is_ref_type(ty):
                continue
            t, a = self._copy_cost(ty, getattr(prm, "name", "") or "")
            T, A = Cost.add(T, t), Cost.add(A, a)
        return T, A

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

    # ---------------------------------------------------------------- 局部容器的尺寸归并
    #
    # **问题**（2026-11-08）：`map cnt; ... for (k in cnt.keys())` 让 κ 里出现
    # `n(cnt)`。`cnt` 是局部变量，spec 的 target 里**引用不到**它，于是
    # `compare()` 只能判 E23「尺寸量不一致」→ 永远「无法判定」。
    #
    # **解法（方案 1）**：给局部容器算一个**可证明的尺寸上界**——
    #
    #     规模 ≤ 初始规模 + Σ(每个写点被执行的次数上界)
    #
    # 写点（`insert`/`push`/…）在 k 层循环里就乘上每层轮数（轮数用与 κ 同一套
    # 推导，所以两边的尺寸量名字一致）。得到的上界只用**输入尺寸量**表达，
    # 最后把 κ 里的 `n(cnt)` 替换掉。**替换只会变大**（成本对尺寸单调），
    # 所以 κ 仍是上界——这条是硬约束，不是近似。
    #
    # 推不出上界时**不猜**：名字留在 κ 里，由 `cost_of` 明确报错，
    # 而不是静默退回「无法判定」。

    def _local_container_bounds(self, fn: Node):
        """返回 `({局部名: 尺寸上界 或 None}, {局部容器名})`。"""
        bounds, local_names, _params, _row_of, _caps = self._container_bounds_full(fn)
        # **归并两类局部容器**（2026-11-12）：
        #
        # ① **被当作循环轮数用的**（`for k in cnt.keys()` / `while (i < v.length())`）。
        #    这类 `n(局部)` 是真正的**尺寸维度**：κ 的阶由它决定，而 target
        #    里引用不到它，契约永远判不了。
        # ② **派生容器的源**（`ks = cnt.keys()` 里的 `cnt`，见 `_DERIVATION`）。
        #    源自己的 `n(cnt)` 会出现在两处：派生那一次调用是 O(n(cnt))，
        #    之后的 `cnt.get(...)` 是 O(log n(cnt))。**不把源归约掉**，
        #    `n(cnt)` 就留在 κ 里——"建局部容器再遍历"这一类永远证不出来。
        #    归约方向是 `n(源) → ≥ 它的界`（规则①是逐元素对应），仍是上界。
        #
        # **只被 `insert/push` 写、之后从不遍历/派生/返回**的局部容器，还要再分两族
        # （2026-11-12，规则④）：
        #   · 键取自**被遍历容器的元素**（计数表，`for (x : xs) cnt.insert(x, 1)`）
        #     → 归约（类 ④，见 `_counting_table_locals`）；
        #   · 键是**循环计数器**（`while (i < n) m[i] = i`，D1 形状）
        #     → 保持原样（那是"把 map 当稀疏数组用"，键集合与任何输入容器的
        #       **元素**无关；`log2(n(m))` 是既有渲染，`test_map_cost` 钉着它）。
        drivers = self._local_size_drivers(fn, local_names)
        drivers |= self._derivation_sources(fn, local_names)
        # ③ **被返回的局部容器**（`return freq;`）。函数自己的 κ 里也会出现
        #    `n(freq)`（写点/读点的尺寸项），而它在 target 里同样引用不到。
        #    它的界与 `_ret_size_of` 给调用点用的是**同一个**（增长预扫的
        #    `n(ws)`），所以这里归约不会让调用方与函数体两处口径分叉。
        drivers |= self._returned_container_names(fn, local_names)
        # ④ **计数表**（2026-11-12，规则②的 map/set 版）：**键取自被遍历容器的元素**
        #    的局部 `map`/`set`/`unordered_*`（`for (x : xs) cnt.insert(x, 1)`）。
        #    它的尺寸由**不同键的个数**决定，而键集合 ⊆ `xs` 的元素集合 ⇒
        #    `|cnt| ≤ n(xs)`。数目仍由增长预扫给（规则②那份），见
        #    `_counting_table_locals` 的说明。
        drivers |= self._counting_table_locals(fn, local_names, bounds)
        # ⑤ **结构体局部变量的容器字段**（①，2026-11-12）：`n(monday.words)` 与
        #    `n(seen.words)` 是**局部尺寸量**，target 里引用不到 ⇒ 必须在函数自己的
        #    κ 里就换成界（与 `return freq;` 同一条理由）。界已在
        #    `_container_bounds_impl` 里算好；这里只是让它**参与替换**。
        drivers |= {nm for nm in bounds if "." in nm}
        # ⑥ **写入全被增长预扫建模**的局部容器（2026-11-13）：把「有可证上界」的
        #    局部容器都代入，而不是只代前面五类 driver。
        #
        #    **为什么"有界"还不够**：增长预扫只在它**看得见**写入时加数。`push_heap(h, x)`
        #    在成本域里按"只重排"记账（长度不变），可解释器里它真的加一个元素；
        #    认不出的外部函数同理。漏掉这些写点，`bounds[h]` 就只是「声明初值 +
        #    看得见的 push」，**比真实规模小** —— 拿它替换 `n(h)` 会把 κ 变成下界。
        #    `test_heap` 的 `log2(n(h))` 钉的正是这一格（h 被 `push_heap` 写）。
        #    所以判据是"**每一处提到它的地方都被记过账**"，见 `_modelled_write_locals`。
        #
        #    **只代界非 None 的**：界推不出的名字保持原样（静默留在 κ 里、由判定
        #    侧判 E23），**不**因为"进了 driver 集合"就变成 `cost_of` 报错——
        #    后者是另一条口径（`test_hof` 钉着"无界 ⇒ 报错"那一格）。
        #    换句话说：这一条只**增加代入**，不增加报错。
        drivers |= {nm for nm in self._modelled_write_locals(fn, local_names)
                    if bounds.get(nm) is not None}
        # **归约的方向**：`n(局部) → 界`，而界是增长预扫给的**上界** ⇒ 代入后
        # κ 只会变大或不变 ⇒ 仍是上界（硬约束，见 `test_kappa_local_size_subst.py`）。
        #
        # **与 `_copied_sequence_locals`（曾试过、已回退）的差别**：那一条按
        # "被 `container.push(x)` 拷进去"选名字，既不筛写点也不筛 None，于是
        # `test_heap` 的 `n(h)`（h 被 `push_heap` 写）与 `test_hof` 的
        # "无界局部 ⇒ cost_of 报错"两处都被撞翻。这一条**只代能证明"界覆盖了
        # 全部写入"的名字**，两条既有契约都不动。
        return {nm: bounds.get(nm) for nm in drivers}, drivers

    def _modelled_write_locals(self, fn: Node, local_names) -> set:
        """规则⑥的筛子：**每一处写入都被增长预扫记进界里**的局部容器名。

        判据（全函数扫一遍，一处不满足就整个名字不算）：

        | 形状 | 判定 |
        |---|---|
        | `nm.push_back(…)` 等（`_GROWTH_METHODS`） | 预扫按"每轮 +1 × 倍数"记过 ⇒ 放行 |
        | `nm.size()` / `.at(i)` / `.clear()` 等（`_SAFE_METHODS`） | 只读 / 变短 ⇒ 放行 |
        | `nm.swap(…)` / `.fill(…)` / `.resize(…)` / 认不出的方法 | **挡**（见 `_UNMODELLED_SIZE_METHODS`） |
        | `map_put(nm, …)` 等（`_GROWTH_BUILTINS` 的第一个实参） | 预扫记过 ⇒ 放行 |
        | `other.push_back(nm)`（增长方法的实参） | 是**拷贝进去**，只读 ⇒ 放行 |
        | `print` / `sort` / `sort_by` / HOF（`_LENGTH_PRESERVING_SPECIALS`） | 不改长度 ⇒ 放行 |
        | `push_heap` / `pop_heap` / `swap`（`_LENGTH_CHANGING_SPECIALS`） | **挡** |
        | `f(nm)`：`_call_may_grow_arg` 为真（非 const 引用 / 认不出） | **挡** |
        | `f(nm)`：按值或 `const&` 的用户函数 | 只读 ⇒ 放行 |

        **为什么用"屏蔽"而不是"证明覆盖"**：预扫的加法是**完备**的（每个
        `_GROWTH_METHODS` / `_GROWTH_BUILTINS` 写点都加），缺的只是"它当作
        不变长"的那几格。所以只要没有那几格，`bounds[nm]` 就是覆盖了全部写入的
        上界——判据是"没有漏账的写点"，不是"每个写点都数对"。
        """
        out: set = set()
        if not local_names:
            return out
        body = next((c for c in (fn.children or []) if c.kind == "block"), None)
        if body is None:
            return out
        # **关联容器（`map`/`set`）不在这一条里**：它们由规则④按"键是否取自
        # 被遍历容器的元素"决定，而"键是循环技术器"那一格（`while (i < n)
        # { m[i] = i; }`，D1 形状）是**登记在案的不归约**——`test_map_cost`
        # 的 `[5]` 钉着 `log2(n(m))`。规则⑥只管**序列**（vector/string/deque/
        # array）：它们的规模是"追加了多少个"，正是增长预扫数的那个量。
        kinds = self._container_kinds_of(fn)
        cand = {nm for nm in local_names
                if kinds.get(nm) is not None
                and kinds.get(nm) not in _COUNTING_TABLE_KINDS}
        if not cand:
            return out
        bad: set = set()
        # ① 接收者：方法必须属于"记过账的写入"或"不动长度"。
        for mc in _descendants(body, "method-call"):
            recv = (mc.children[0]
                    if mc.children and getattr(mc.children[0], "kind", "") == "path"
                    else None)
            if recv is None or recv.name not in cand:
                continue
            short = _callee_short(mc)
            if short in _UNMODELLED_SIZE_METHODS:
                bad.add(recv.name)
            elif short in _GROWTH_METHODS or short in _SAFE_METHODS:
                continue
            else:
                bad.add(recv.name)
        # ② 实参：只有"拷贝进去 / 只读"的位置才放行。
        for call in _descendants(body, "call"):
            short = _callee_short(call)
            args = next((c for c in (call.children or []) if c.kind == "args"), None)
            for i, a in enumerate(args.children if args is not None else []):
                while getattr(a, "kind", "") in ("ref", "paren"):
                    a = a.children[0] if a.children else None
                if getattr(a, "kind", "") != "path" or a.name not in cand:
                    continue
                if short in _GROWTH_BUILTINS and i == 0:
                    continue                    # 预扫记过（map_put / set_add …）
                if short in _GROWTH_METHODS:
                    continue                    # 增长方法的实参是拷贝，只读
                if short in _LENGTH_PRESERVING_SPECIALS:
                    continue
                if short in _LENGTH_CHANGING_SPECIALS:
                    bad.add(a.name)
                    continue
                if self._call_may_grow_arg(short, i):
                    bad.add(a.name)             # 非 const 引用 / 认不出 ⇒ 可能改长
        return cand - bad

    def _returned_container_names(self, fn: Node, local_names) -> set:
        """`return <局部容器>;` 里的名字（返回尺寸的同一份上界）。"""
        out: set = set()
        if not local_names:
            return out
        for r in _descendants(fn, "return"):
            e = r.children[0] if r.children else None
            while e is not None and getattr(e, "kind", "") in ("ref", "paren"):
                e = e.children[0] if e.children else None
            if (getattr(e, "kind", "") == "path"
                    and getattr(e, "name", "") in local_names):
                out.add(e.name)
        return out

    # ---------------------------------------------------------------- 规则④：计数表
    #
    # **问题**（2026-11-12，KPI③ 唯一失败项 = AI 真实提交 05）：
    #
    #     u64 max_freq(const vector<u64>& xs) {
    #         map<u64, u64> cnt;                                  // 局部空表
    #         for (const auto& x : xs) {
    #             if (cnt.contains(x)) { cnt.put(x, cnt.get(x) + 1); }
    #             else                 { cnt.insert(x, 1); }      // 每轮至多插一个键
    #         }
    #         return best;
    #     }
    #
    # κ 里是 `log2(n(cnt))`，而 `cnt` 是**局部名**，target 只能写 `xs.size()`
    # ⇒ `compare` 判 E23「尺寸量 ['cnt','xs'] 与 ['xs'] 不一致」⇒ 永远判不了。
    # **语义与代码都正确，漏的只是成本域的一条归约。**
    #
    # **界本来就算得出来**：`_container_bounds_full` 对这个 `cnt` 给的正是
    # `n(xs)` —— `insert`/`put`/`add`/`emplace` 已在 `_GROWTH_METHODS` 里、
    # `m[k] = v` 已在 `_scan_growth_stmt` 里按 map 记增长，增长预扫按轮数累计。
    # 也就是说：**规则②（push 那条）的那一份预扫原封不动地覆盖了 map/set**，
    # 这里不新造第二份（新造一套就会和轮数推导漂，这正是历史上 `n(n)` 的来历）。
    # 缺的只是 `_local_container_bounds` 的 **driver 集合**：以前只有
    # ①轮数 ②派生源 ③被返回，这一类谁都不属于。
    #
    # ## 为什么"键注入"是这一族的判据（而不是"名字叫 cnt"）
    #
    # 规则②的界来自**轮数**：`out.push` 每轮最多长 1 个，所以 `|out| ≤ 轮数`。
    # map 的键会**重复**（05 里 `cnt.put(x, …)` 对同一个 `x` 反复写），
    # 尺寸是**不同键的个数**，不是轮数。这里要的是一条独立的理由：
    #
    #     每个插入点的键都取自容器 C 的元素
    #         ⇒ 键集合 ⊆ C 的元素集合
    #         ⇒ |cnt| ≤ |C|（不同键的个数 ≤ 元素个数）
    #
    # 它与插入次数**无关**：同键写十次，`|cnt|` 也不涨。C 的判据两条：
    #   · `for (x : C)` 的循环变量 `x`（`x` 就是 C 的元素）；
    #   · `C[j]` / `C.at(j)` / `C.get(j)`，且 `C` 是**序列**（取的是元素，
    #     不是 map 的"值"——`m2.get(k)` 的值可以是任何东西，不算元素）。
    # 并且 `|C|` 自己写得成尺寸符号（形参，或可继续归约的局部容器）。
    #
    # **数目用增长预扫的界，不用"|C|"**：循环外的前置插入（`cnt.insert(k, 0)`）
    # 只执行一次，预扫按 +1 计入 ⇒ 界是 `n(xs) + 1`，仍是上界，也仍是 O(n(xs))。
    # 这些**不在循环里**的写点不要求键注入（它们只贡献常数个键），
    # 所以"循环外再插一个键"这一格不需要另写规则。
    #
    # ## 不在此列（保持原样：`n(局部)` 留在 κ 里，判 E23）
    #
    # · 键是**循环计数器**（`while (i < n) { m[i] = i; }`，D1 形状）：键集合是
    #   `0..n-1`，与任何输入容器的**元素**无关，那等于把 map 当稀疏数组用。
    #   它的界虽然也能由轮数证出来，但 `log2(n(m))` 是既有渲染，
    #   `test_map_cost` 的 `[5]` 钉着它 —— 本轮**只扩不缩**，不动这一格。
    # · 键是字面量 / 别的容器的**值** / 算术表达式：没有"键 ⊆ 元素"的对应。
    # · 有一个**循环里**的写点说不出键 ⇒ 整个容器不归约（宁可不判，不许猜）。
    #
    # 方向：`n(cnt) → bounds[cnt]`，而 bounds 是增长预扫算的**上界** ⇒
    # 代入后 κ 只会变大 ⇒ 仍是上界（硬约束）。

    def _counting_table_locals(self, fn: Node, local_names, bounds) -> set:
        """规则④：**被循环填充**的局部 `map`/`set`，键取自某容器的元素 ⇒ 可归约。

        三条件（缺一条就留在原样，判 E23）：
          ① 至少有一个**循环里**的写点（"被循环填充"；`unordered_map t; t.get(1)`
             这种空表不算——归约成 0 会抹掉"这次查找 O(n(t))"的形状）；
          ② 每个**循环里**的写点的键都取自某个尺寸可写的容器的元素（键注入）；
          ③ 增长预扫给出了界（`bounds[nm] is not None`）——数目就用它。
        """
        out: set = set()
        if not local_names:
            return out
        kinds = self._container_kinds_of(fn)
        cand = {nm for nm in local_names
                if kinds.get(nm) in _COUNTING_TABLE_KINDS
                and bounds.get(nm) is not None}
        if not cand:
            return out
        # **尺寸符号写得出来**的容器：形参，或自带界的局部容器（可继续归约）。
        params = self._container_params_of(fn)
        sized = {nm for nm in kinds
                 if nm in params or (nm in local_names and bounds.get(nm) is not None)}
        ok = {nm: True for nm in cand}
        looped: set = set()
        self._walk_loops(
            fn,
            lambda st, loops: self._counting_table_stmt(
                fn, st, loops, cand, kinds, sized, ok, looped))
        # **至少要有一个"循环里的写点"**（任务标题那半句：**被循环填充**）：
        # 一个写点都没有的容器（`unordered_map t; return t.get(1);`）尺寸恒为 0，
        # 把它归约成 0 会**抹掉**"这次查找是 O(n(t))"这个形状（`test_unordered`
        # 的 `[3]` 钉的正是"unordered 的 κ 含 n(t) 线性项"）。那不是本规则要覆盖
        # 的族——"被循环填充的计数表"才是——所以这一格退回原样。
        return {nm for nm in cand if ok.get(nm) and nm in looped}

    def _container_kinds_of(self, fn: Node) -> Dict[str, str]:
        """`{名字: 容器种类}`（局部声明 + 容器形参）；种类的 canon 名。"""
        out: Dict[str, str] = {}
        for d in (list(_descendants(fn, "const-decl"))
                  + list(_descendants(fn, "var-decl"))):
            nm = getattr(d, "name", "")
            kd = _container_type_name(d.children[0] if d.children else None)
            if nm and kd:
                out[nm] = kd
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        for p in (pnode.children if pnode is not None else []):
            ty = next((c for c in (p.children or [])
                       if c.kind in ("type-ref", "type-name")), None)
            kd = _container_type_name(ty)
            if getattr(p, "name", "") and kd:
                out[p.name] = kd
        return out

    def _walk_loops(self, fn: Node, visit) -> None:
        """按语句走函数体，`visit(语句, 外层循环栈)` 每个语句一次。

        循环栈里每一项是 `_loop_ctx` 的结果（`for` 带循环变量与被遍历容器）。
        **漏看一个语句只会让规则④少归约（保守）**，不会失真：尺寸的数值一律
        取自增长预扫（那一份是完整的），这里只判"键是不是容器元素"。
        """
        body = next((c for c in (fn.children or []) if c.kind == "block"), None)
        self._walk_loops_stmts(body.children if body is not None else [], [], visit)

    def _walk_loops_stmts(self, stmts, loops, visit) -> None:
        for st in (stmts or []):
            visit(st, loops)
            k = getattr(st, "kind", "")
            if k == "block":
                self._walk_loops_stmts(st.children, loops, visit)
            elif k in ("for", "while"):
                body = stmt_body(st)
                inner = loops + [self._loop_ctx(st)]
                self._walk_loops_stmts(
                    body.children if body is not None else [], inner, visit)
            elif k == "if":
                # `if` 的子节点：[条件, block, block?] / else-if 也是 `if` 节点
                # （与 `_scan_growth_stmt` 同一套形状）。
                for c in (st.children or [])[1:]:
                    if c.kind == "block":
                        self._walk_loops_stmts(c.children, loops, visit)
                    elif c.kind in ("if", "else"):
                        self._walk_loops_stmts([c], loops, visit)
            elif k == "match":
                for a in arms_of(st):
                    blk = arm_block(a)
                    self._walk_loops_stmts(
                        blk.children if blk is not None else arm_single_stmts(a),
                        loops, visit)
            elif k == "parallel":
                body = stmt_body(st)
                for task in (body.children if body is not None else []):
                    self._walk_loops_stmts(
                        task.children if getattr(task, "kind", "") == "block"
                        else [task], loops, visit)
            else:
                for c in (st.children or []):
                    if getattr(c, "kind", "") == "block":
                        self._walk_loops_stmts(c.children, loops, visit)

    def _loop_ctx(self, st) -> dict:
        """一环循环：`for` 记循环变量与被遍历容器名（只认裸容器名）。"""
        k = getattr(st, "kind", "")
        ctx = {"kind": k, "var": None, "src": None}
        if k != "for":
            return ctx
        var = getattr(st, "name", "") or ""
        if not var:
            p = next((c for c in (st.children or []) if c.kind == "param"), None)
            var = getattr(p, "name", "") if p is not None else ""
        ctx["var"] = var or None
        it = for_iterable(st)
        if it is not None and getattr(it, "kind", "") == "path":
            ctx["src"] = getattr(it, "name", "") or None
        return ctx

    def _counting_table_stmt(self, fn, st, loops, cand, kinds, sized, ok,
                            looped) -> None:
        """检查 `st` 的**自己这一层**的写点（嵌套块由 `_walk_loops_stmts` 走）。"""
        k = getattr(st, "kind", "")
        # **整容器赋值**（`cnt = other`）：写点不是插入，键说不出来 ⇒ 不归约。
        if k in ("assign", "assign-op"):
            lhs = st.children[0] if st.children else None
            if (getattr(lhs, "kind", "") == "path"
                    and getattr(lhs, "name", "") in cand):
                ok[lhs.name] = False
            elif (getattr(lhs, "kind", "") == "index"
                    and lhs.children
                    and getattr(lhs.children[0], "kind", "") == "path"
                    and lhs.children[0].name in cand):
                base = lhs.children[0].name
                # 只对 map 的下标赋值是"插入"（与 `_scan_growth_stmt` 同口径）。
                if kinds.get(base) in ("map", "unordered_map"):
                    key = lhs.children[1] if len(lhs.children) > 1 else None
                    self._counting_table_write(
                        fn, base, key, loops, kinds, sized, ok, looped)
        # **写点写在表达式里**（`cnt.put(x, 1)` / `map_put(cnt, k, v)`）。
        for c in (st.children or []):
            if getattr(c, "kind", "") == "block":
                continue                       # 嵌套块交给递归（循环栈会变）
            self._counting_table_expr(fn, c, loops, cand, kinds, sized, ok,
                                      looped)

    def _counting_table_expr(self, fn, e, loops, cand, kinds, sized, ok,
                             looped) -> None:
        """表达式里的写点：增长方法的接收者 / 增长内建的第一个实参。"""
        for d in _descendants(e, "method-call"):
            short = _callee_short(d)
            if short not in _GROWTH_METHODS:
                continue
            recv = (d.children[0]
                    if d.children and getattr(d.children[0], "kind", "") == "path"
                    else None)
            if recv is None or recv.name not in cand:
                continue
            args = next((c for c in (d.children or []) if c.kind == "args"), None)
            key = args.children[0] if (args is not None and args.children) else None
            self._counting_table_write(
                fn, recv.name, key, loops, kinds, sized, ok, looped)
        for d in _descendants(e, "call"):
            short = _callee_short(d)
            if short not in _GROWTH_BUILTINS:
                continue
            args = next((c for c in (d.children or []) if c.kind == "args"), None)
            xs = args.children if args is not None else []
            first = xs[0] if xs else None
            while first is not None and getattr(first, "kind", "") in ("ref", "paren"):
                first = first.children[0] if first.children else None
            if first is None or getattr(first, "kind", "") != "path":
                continue
            if first.name not in cand:
                continue
            # `map_put(h, k, v)` / `set_add(s, x)`：第 2 个实参是键。
            key = xs[1] if len(xs) > 1 else None
            self._counting_table_write(
                fn, first.name, key, loops, kinds, sized, ok, looped)

    def _counting_table_write(self, fn, nm, key, loops, kinds, sized, ok,
                              looped) -> None:
        """一个写点：**循环里**的键必须取自某个尺寸可写的容器的元素。"""
        if not loops:
            # 循环外的前置插入：只执行一次，预扫已按 +1 计入 ⇒ 不要求键注入
            # （`cnt.insert(k, 0)` 这种"再插一个键"由此自动变成 +1 常数）。
            return
        looped.add(nm)
        if key is None or self._elem_source_of(key, loops, kinds, sized, fn) is None:
            ok[nm] = False

    def _elem_source_of(self, e, loops, kinds, sized, fn,
                        seen=None) -> Optional[str]:
        """这个表达式**取的是哪个容器的元素**？→ 容器名；说不清 → None。

        | 形状 | 元素来源 |
        |---|---|
        | `x`（某个外层 `for (x : C)` 的循环变量） | `C` |
        | `x`（`const`/从未重写的局部，初值是元素） | 初值的来源（一跳） |
        | `C[j]`（`C` 是序列） | `C` |
        | `C.at(j)` / `C.get(j)`（`C` 是序列） | `C` |
        | 其余（字面量 / 算术 / map 的值 / 嵌套元素） | None（不猜） |

        别名那一跳用的是 `_decl_inits`（**不可变**局部：`const`，或全函数没有
        第二次写的 `var`）——被重写过的局部不能当元素别名（可能变成别的值）。
        """
        while e is not None and getattr(e, "kind", "") in ("ref", "paren"):
            e = e.children[0] if e.children else None
        if e is None:
            return None
        seen = set(seen or ())
        k = getattr(e, "kind", "")
        if k == "path":
            nm = getattr(e, "name", "")
            for lp in reversed(loops):
                if (lp.get("kind") == "for" and lp.get("var") == nm
                        and lp.get("src")):
                    return lp["src"] if lp["src"] in sized else None
            if fn is None or not nm or nm in seen:
                return None
            init = self._decl_inits(fn).get(nm)
            if init is None:
                return None
            return self._elem_source_of(init, loops, kinds, sized, fn,
                                        seen | {nm})
        if k == "index":
            base = e.children[0] if (e.children or []) else None
            if (base is None or getattr(base, "kind", "") != "path"
                    or base.name not in sized):
                return None
            return base.name if kinds.get(base.name) in _SEQ_HEADS + _ARR_HEADS else None
        if k in ("method-call", "call"):
            if _callee_short(e) not in ("at", "get", "index"):
                return None
            recv = (e.children[0]
                    if (e.children
                        and getattr(e.children[0], "kind", "") == "path") else None)
            if recv is None or recv.name not in sized:
                return None
            return recv.name if kinds.get(recv.name) in _SEQ_HEADS + _ARR_HEADS else None
        return None

    def _derivation_sources(self, fn: Node, local_names) -> set:
        """派生容器（`keys/values/…` / `split`）的**局部源容器**名。"""
        out: set = set()
        if not local_names:
            return out
        for d in _descendants(fn, "method-call"):
            short = _callee_short(d)
            if short not in _DERIVED_SIZE_SHORTS and short not in _SPLIT_SIZE_SHORTS:
                continue
            recv = (d.children[0]
                    if (d.children and getattr(d.children[0], "kind", "") == "path")
                    else None)
            if recv is not None and recv.name in local_names:
                out.add(recv.name)
        for d in _descendants(fn, "call"):
            short = _callee_short(d)
            if short not in _SPLIT_SIZE_SHORTS:
                continue
            args = next((c for c in (d.children or [])
                         if getattr(c, "kind", "") == "args"), None)
            first = args.children[0] if (args is not None and args.children) else None
            if (getattr(first, "kind", "") == "path"
                    and first.name in local_names):
                out.add(first.name)
        return out

    # ---------------------------------------------------------------- 出参增长（P0-1）
    #
    # **问题**（2026-11-12）：`f(xs, out)` 用**出参**填容器，是 C++ 最常见的写法之一：
    #
    #     static void count_words(const vector<string>& words,
    #                             unordered_map<string,u64>& freq) {
    #         for (const auto& w : words) { freq[w] += 1; }
    #     }
    #     int main() { vector<string> cmds; …push…; unordered_map<…> freq;  // 空的
    #                  count_words(cmds, freq);        // ← 这里才把 freq 填满
    #                  for (auto it = freq.begin(); …) … }   // 半径来自 freq
    #
    # 调用点的 `freq` 是**局部容器**，它自己的声明推不出尺寸（`|freq| = 0`），
    # 真实尺寸是"进函数时的大小 + 被调函数插了多少"。以前 `_scan_expr` 对
    # "可能增长实参"的调用一律把界抹成 `None`（⊤）——保守，但把这一类全判死。
    #
    # **对偶关系**：`_ret_size_of` 推「`return <局部容器>` 的长度」（**返回**方向），
    # 这里推「`T&` 形参被写进去多少」（**出参**方向）。两者用的是**同一个**
    # 增长预扫（`_scan_*` + `_grow_local`），所以同一份内核在弹出的两处给出
    # 同一个界：`return out` 是 `n(ws)`，出参是 `n(words)`。
    #
    # **上界口径**（`|x|` = 长度，`|x|₀` = 进入被调函数时的大小）：
    #
    #     |out| ≤ |out|₀ + Σ(插入点被执行次数)        （增长预扫算的只是后一项）
    #     |freq| ≤ |freq|₀ + |words|                 （count_words(cmds, freq)）
    #
    # 调用点把「自己给实参的界」当 `|out|₀`，**加上**被调方的增长 —— 加法只会
    # 变大（成本对尺寸单调）⇒ κ 仍是上界。这就是硬约束，不是近似。
    #
    # **证明不了就 ⊤（三条）**：
    #   ① 增长的**因子**用到出参自己的当前尺寸（`for (i < out.size()) out.push(…)`）
    #      —— 每轮重算，两侧都不再是"进入时的尺寸"，增长与初始值纠缠不清；
    #   ② 增长里还剩**被调方的局部容器名 / 标量形参名**（调用方作用域查不到，
    #      求值会按 1 算 ⇒ 低估。`_scalar_arg_bound` 只肯换"不会被改写"的标量
    #      实参：字面量 / 模块常量 / 调用方没写过的形参或 `const` 局部；
    #      换不掉就 ⊤）；
    #   ③ 被调方把出参又交给认不出的函数（`_scan_expr` 已把界抹成 `None`）。

    def _ref_out_params(self, fn: Optional[Node]) -> Dict[str, str]:
        """非 `const` 引用的**容器**形参 → `{名字: 容器种类}`（出参候选）。

        按值是拷贝（改不到调用者）、`const T&` 只读借用（`_call_may_grow_arg`
        已经这么分）——只有这两类之外的非 const 引用才可能把调用者的容器写长。
        """
        out: Dict[str, str] = {}
        pnode = next((c for c in ((fn.children if fn is not None else []) or [])
                      if c.kind == "params"), None)
        for p in (pnode.children if pnode is not None else []):
            ty = p.children[0] if (p.children or []) else None
            if ty is None or not _is_ref_type(ty):
                continue
            if (getattr(p, "value", None) or {}).get("const"):
                continue
            kind = _container_type_name(ty)
            if kind is not None and p.name:
                out[p.name] = kind
        return out

    def _param_at(self, fn: Optional[Node], idx: int) -> Optional[Node]:
        """`fn` 的第 `idx` 个形参节点（越界 → None）。"""
        pnode = next((c for c in ((fn.children if fn is not None else []) or [])
                      if c.kind == "params"), None)
        ps = pnode.children if pnode is not None else []
        return ps[idx] if 0 <= idx < len(ps) else None

    def _out_growth(self, fn: Optional[Node], pname: str) -> Optional[Cost]:
        """被调函数 `fn` 对形参 `pname` 的**增长上界**（不含进入时的长度）。

        单位是**被调函数自己的符号**（形参尺寸 / 模块常量）；推不出 → `None`。
        结果按函数缓存：一次推导要扫全函数体，而每个调用点都会问。
        """
        if fn is None or not pname:
            return None
        hit = self._out_cache.get(id(fn))
        if hit is not None and hit[0] is fn:
            return hit[1].get(pname)
        if id(fn) in self._out_busy:
            # 增长互相依赖成环（递归 / 互递归）：**没有"最小不动点"的证明就不猜**
            return None
        self._out_busy.add(id(fn))
        try:
            tab = self._out_growth_impl(fn)
        finally:
            self._out_busy.discard(id(fn))
        self._out_cache[id(fn)] = (fn, tab)
        return tab.get(pname)

    def _out_growth_impl(self, fn: Node) -> Dict[str, Optional[Cost]]:
        """`_out_growth` 的实现体：对每个非 const 引用容器形参扫一遍增长。"""
        outs = self._ref_out_params(fn)
        if not outs:
            return {}
        body = next((c for c in (fn.children or []) if c.kind == "block"), None)
        if body is None:
            return {}
        # 增长从 **0** 起算："进入时多大"由调用点自己那一份界负责 —— 这里只数
        # 插入点被执行多少次。
        params = set(outs) | self._container_params_of(fn)
        const_names = {getattr(d, "name", "") for d in _descendants(fn, "const-decl")
                       if getattr(d, "name", "")}
        # `_scan_kinds`：`m[k] = v` 只对 **map/unordered_map** 是"插入"（vector
        # 的下标赋值要求元素已存在）。出参的种类必须在这里，否则 `freq[w] += 1`
        # 不会被记成增长；局部容器的种类一并带上，只影响判定、不改它们的界。
        kinds = dict(outs)
        for d in (list(_descendants(fn, "const-decl"))
                  + list(_descendants(fn, "var-decl"))):
            nm = getattr(d, "name", None)
            kd = _container_type_name(d.children[0] if d.children else None)
            if nm and kd:
                kinds.setdefault(nm, kd)

        def _pass(tracked, blocked) -> Dict[str, Optional[Cost]]:
            """扫一遍函数体。`tracked`＝要跟踪的名字（初值 0），`blocked`＝尺寸不可用。

            **`blocked` 里的出参尺寸不许当已知量**（否则 `bounds[p]` 会把"增长"
            冒充成"尺寸"：`for (i < out.size())` 的轮数会被算成 0）。
            """
            grow: Dict[str, Optional[Cost]] = dict(tracked)
            env: Dict[str, Cost] = {}
            caps: Dict[str, int] = {}
            row_of: Dict[str, str] = {}
            _prev_kinds = getattr(self, "_scan_kinds", None)
            _prev_block = self._out_size_block
            _prev_written = self._scan_written
            # **`cur_fn` 指向被扫的这个函数**（与 `cost_of` 一致）：
            # `_bound_to_cost_scoped` 认标量形参时看 `cur_fn`，不换的话
            # `while (i < k)` 的 `k` 会渲染成 `n(k)`（一个调用方查不到的名字），
            # 而这里要的是被调方的 `sym(k)`。
            _prev_cur = self.cur_fn
            self.cur_fn = fn
            self._scan_kinds = kinds
            self._out_size_block = set(blocked)
            self._scan_written = set()
            self._fn_stack.append(fn)      # `_for_count` 的局部别名表按这一帧查
            try:
                self._scan_growth_stmts(body.children, Cost.num(1), grow, env,
                                        caps, row_of, const_names, params, fn)
            finally:
                self._fn_stack.pop()
                self._scan_kinds = _prev_kinds
                self._out_size_block = _prev_block
                self._scan_written = _prev_written
                self.cur_fn = _prev_cur
            return grow

        # **第一遍：所有出参的尺寸都不可用**（见上：`bounds[p]` 是插入次数）。
        grow = _pass({p: Cost.num(0) for p in outs}, outs)
        # **第二遍：只解锁"从没被写过的出参"**（增长恰为 0）。
        #
        #   `fill_map(keys, m)` 里 `keys` 只被读（`keys[i]` / `keys.size()`），增长
        #   是 0 ⇒ 它在函数体内任何时刻的长度都等于**进入时的长度** ⇒ `n(keys)`
        #   是一个合法尺寸量，调用点会用实参的界实例化它（`_param_size_table`）。
        #   被写过的出参**不解锁**：那正是第一遍要挡的"增长冒充尺寸"。
        #
        #   **为什么仍是上界**：解锁只可能把第一遍里的 ⊤（`n(出参)` 被 `_resolve_
        #   local_cost` 折成 None）换成具体式。第一遍已经得出的**有限值不可能
        #   含被挡的尺寸名**（`_resolve_local_cost` 见到一个就整项 None），所以
        #   两遍里都定义的值逐字相同，只有 ⊤ → 具体式这一种变化（多算不改少）。
        stable = {p for p in outs
                  if grow.get(p) is not None and grow[p].kind == "num"
                  and grow[p].value == 0}
        unblocked = set(outs) - stable
        if stable and unblocked:
            grow.update(_pass({p: Cost.num(0) for p in unblocked}, unblocked))
        # **被调方的局部容器名要归约掉**：增长预扫只跟踪出参，局部容器的界在
        # 另一份表里（`_container_bounds_full`）。链断在局部容器上 → ⊤。
        lbounds, local_names, _p, _row, _caps = self._container_bounds_full(fn)
        out: Dict[str, Optional[Cost]] = {}
        for p in outs:
            g = grow.get(p)
            if g is None:
                out[p] = None
                continue
            g = self._resolve_local_cost(g, lbounds, {})
            if g is None:
                out[p] = None
                continue
            out[p] = self._only_roots(g, local_names, params)
        return out

    def _scalar_arg_bound(self, node, fn: Optional[Node]) -> Optional[Cost]:
        """调用点实参的**值**上界（严格版：只认**不会被改写**的那几种）。

        为什么不直接用 `_count_of_expr`：它对"局部 `var` 的初值"是按**声明处**
        展开的（`u64 k = 10; …; k = k * 100; f(out, k)` 会给出 10），拿它当
        增长上界就是**低估**。这里只收"值不会被改"的形状：

          · 整数字面量；
          · 模块常量（`const N: u64 = 256;`，不可写）；
          · **调用方的标量形参**（同一个值穿过去，且调用方没写过它）；
          · **`const` 局部 + 字面量/常量初值**，且调用方没写过它。

        其余 → `None`（增长里会留下被调方形参名，由 `_leaked_names` 判 ⊤）。
        """
        if node is None:
            return None
        while getattr(node, "kind", "") in ("ref", "paren"):
            node = node.children[0] if node.children else None
        if node is None:
            return None
        v = _int_literal(node)
        if v is not None:
            return Cost.num(v)
        if getattr(node, "kind", "") != "path":
            return None
        nm = getattr(node, "name", "") or ""
        if not nm:
            return None
        if nm in self.const_values:
            return Cost.num(self.const_values[nm])
        if fn is None:
            return None
        if nm in self._written_names(fn):
            return None
        if nm in self._scalar_params_of(fn):
            return Cost("sym", nm)
        if nm in self._const_names_of(fn):
            init = self._decl_inits(fn).get(nm)
            lit = _int_literal(init)
            if lit is not None:
                return Cost.num(lit)
            if init is not None and getattr(init, "kind", "") == "path" \
                    and getattr(init, "name", "") in self.const_values:
                return Cost.num(self.const_values[init.name])
        # **初值是算得出来的表达式**（2026-11-12）：`size_t mid = lo + (hi - lo) / 2;`
        # 这类**派生标量**也要能过调用点。`_decl_inits` 只收"全函数没被写过"的
        # 名字（初值就是全程的值），`_count_of_expr` 对 `-` / `/` / `%` 一律取
        # 左操作数——给的是**上界**，方向安全（`mid ≤ lo + hi`、`≤ hi`）。
        #
        # 为什么非它不可：`merge_sort` 里 `merge_halves(xs, lo, mid, hi)` 的
        # `mid` 换不出来时，被调方的形参名会**原样留在调用方的 κ 里**
        # （求值按 +∞ ⇒ 那条 κ 其实等于 ⊤，只是长得像具体式）。
        init = self._decl_inits(fn).get(nm)
        if init is not None:
            b = self._count_of_expr(init, fn)
            if b is not None and not b.is_top():
                return b
        return None

    def _subst_scalar_syms(self, c: Optional[Cost], fn: Node,
                           call: Node, loose: bool = False,
                           extra: Optional[Dict[str, "Cost"]] = None
                           ) -> Optional[Cost]:
        """把增长式里的**标量形参** `sym(k)` 换成调用点实参的值上界。

        出参增长最常见的形状是 `for (i = 0; i < k; ++i) out.push_back(…)`
        —— 增长是 `k`，而 `k` 在调用方作用域里查不到（求值按 1 算 ⇒ 低估）。
        换不出上界（有 `sym(标量形参)` 不在表里）→ `None`（⊤，不猜）。

        `loose=True`：**返回尺寸**专用（2026-11-12）。那里要的是"结果容器有多长"
        的**上界**，实参是 `masked.size()` 这种表达式时用 `_count_of_expr` 展开
        （`n(masked)`）；仍是**裸局部名**的不收——`_count_of_expr` 对局部名按
        声明处初值展开，被改写过的值会低估（与增长那条路的严格口径一致）。

        `extra`：`{标量形参名: 上界}` 的**兜底表**（`_scalar_index_ceiling`
        推出来的"圈数 ≤ n(C)"）。只在 `_scalar_arg_bound` 换不出来时用。
        """
        if c is None:
            return None
        pnode = next((x for x in (fn.children or []) if x.kind == "params"), None)
        anode = next((x for x in (call.children or []) if x.kind == "args"), None)
        ps = pnode.children if pnode is not None else []
        args = anode.children if anode is not None else []
        tab: Dict[str, Cost] = {}
        for i, p in enumerate(ps):
            if i >= len(args) or not p.name:
                continue
            ty = p.children[0] if (p.children or []) else None
            if _is_container_type(ty) or _is_ref_type(ty):
                continue                    # 容器实参走 `_param_size_table`
            v = self._scalar_arg_bound(args[i], self._cur_frame_fn())
            if v is None and loose:
                arg = args[i]
                while getattr(arg, "kind", "") in ("ref", "paren"):
                    arg = arg.children[0] if arg.children else None
                # 裸名字（可能被改写的局部）不收；其余表达式按上界展开。
                if arg is not None and getattr(arg, "kind", "") != "path":
                    v = self._count_of_expr(arg, self._cur_frame_fn())
            if v is None and extra:
                v = extra.get(p.name)
            if v is not None:
                tab[p.name] = v
        # **换不掉的标量形参 = 泄漏**：`sym(k)` 留着的话，调用方的 `k` 可能
        # 完全是另一回事（求值按 1 算 ⇒ 低估）。宁可 ⊤。
        scalars = {(p.name or "") for p in ps
                   if (p.children[0] if (p.children or []) else None) is not None
                   and not _is_container_type(p.children[0])}
        need: set = set()

        def _collect(x) -> None:
            if x is None:
                return
            if x.kind == "sym" and str(x.value) in scalars:
                need.add(str(x.value))
            if x.kind == "piece":
                for _lo, _hi, sub in x.value[1]:
                    _collect(sub)
            for a in getattr(x, "args", ()) or ():
                _collect(a)

        _collect(c)
        if need - set(tab):
            return None
        if not need:
            return c                   # 增长里没有标量形参：不用换

        def walk(x: Cost) -> Cost:
            if x is None:
                return None
            k = x.kind
            if k == "sym":
                return tab.get(str(x.value), x)
            if k in ("num", "top"):
                return x
            if k in ("add", "mul", "max"):
                l, r = walk(x.args[0]), walk(x.args[1])
                if l is None or r is None:
                    return None
                return Cost.add(l, r) if k == "add" else (
                    Cost.mul(l, r) if k == "mul" else Cost.max(l, r))
            if k == "div":
                l, r = walk(x.args[0]), walk(x.args[1])
                return None if l is None or r is None else Cost.div(l, r)
            if k == "log2":
                a = walk(x.args[0])
                return None if a is None else Cost.log2(a)
            if k == "piece":
                var, segs = x.value
                fallback = walk(x.args[0])
                if fallback is None:
                    return None
                return Cost.piece(var, [(lo, hi, walk(s)) for lo, hi, s in segs],
                                  fallback)
            return x

        return walk(c)

    def _leaked_names(self, c: Optional[Cost], fn: Node,
                      substituted: bool = False) -> List[str]:
        """增长式里**调用方查不到的名字** → 你要 ⊤。

        **替换前后分开判**（同名是个坑：调用方的局部恰好也叫 `k` 时，替换后的
        `sym(k)` 是**调用方的**标量，不能当"被调方形参泄漏"）：

          · `substituted=False`（替换前，增长式只用**被调方**的符号）：
            `size(名字)` 只有**容器形参**允许（马上被 `_param_size_table` 换掉）；
            `sym(名字)` 只有**标量形参**允许（马上被 `_subst_scalar_syms` 换掉）；
            其余 size/sym 名都是局部名 ⇒ 换不掉 ⇒ 泄漏。
          · `substituted=True`（替换后，尺寸量已经是调用方的）：只认 `⊤`
            ——推不出就该 ⊤，别的名字都是调用方自己作用域里的。
        """
        if c is None:
            return ["⊤"]
        bad: List[str] = []
        pnode = next((x for x in (fn.children or []) if x.kind == "params"), None)
        params = [(p.name or "") for p in (pnode.children if pnode is not None else [])]
        cparams = self._container_params_of(fn)
        scalars = {p for p in params if p and p not in cparams}

        def walk(x) -> None:
            if x is None:
                return
            if x.kind == "top":
                bad.append("⊤")
            elif not substituted:
                if x.kind == "sym" and str(x.value) not in scalars:
                    bad.append(str(x.value))
                elif x.kind == "size" and str(x.value) not in cparams:
                    bad.append(str(x.value))
            if x.kind == "piece":
                for _lo, _hi, sub in x.value[1]:
                    walk(sub)
            for a in getattr(x, "args", ()) or ():
                walk(a)

        walk(c)
        return bad

    def _out_growth_at_call(self, short: str, call: Node,
                            idx: int) -> Optional[Cost]:
        """调用点（**调用方符号**）的"这次调用让第 `idx` 个实参长多少"。

        推不出 → `None`（调用方照旧把界抹成 ⊤）。
        """
        fn = self.fns.get(short)
        if fn is None:
            return None
        p = self._param_at(fn, idx)
        if p is None or not p.name or p.name not in self._ref_out_params(fn):
            return None
        raw = self._out_growth(fn, p.name)
        if raw is None:
            return None
        g = self._subst_scalar_syms(raw, fn, call)
        if g is not None and self._leaked_names(g, fn):
            return None
        if g is None:
            # **②' 圈数兜底**（2026-11-12）：增长式里还有换不出的标量形参时，
            # 若被调函数的循环**无条件读**某个 const 容器形参 `C[i]`（`i` 是
            # 计数器、上界正是那个标量形参），则良定义执行下**圈数 ≤ n(C)**
            # （`i >= n(C)` 时那一次读就是越界；与 `bound.py` 的"下标合法"口径
            # 同一条）。把那个标量形参换成调用点实参的尺寸，增长才是有限界。
            # 兜底表里是**调用方**符号，所以替换前不再做"被调方形参泄漏"检查。
            extra: Dict[str, Cost] = {}
            ctab = self._param_size_table(fn, call, derive=True)
            pnode = next((x for x in (fn.children or []) if x.kind == "params"), None)
            for _p in (pnode.children if pnode is not None else []):
                if not _p.name:
                    continue
                _ty = _p.children[0] if (_p.children or []) else None
                if _ty is None or _is_container_type(_ty) or _is_ref_type(_ty):
                    continue
                _c = self._scalar_index_ceiling(fn, _p.name)
                _v = ctab.get(_c) if _c is not None else None
                if _v is not None:
                    extra[_p.name] = _v
            if not extra:
                return None
            g = self._subst_scalar_syms(raw, fn, call, extra=extra)
            if g is None:
                return None
        # **容器形参 → 实参尺寸**：与 `_callee_kappa` 用的是**同一张表**
        # （`_param_size_table`），所以调用点的 κ 与这里的界口径不会分叉。
        tab = self._param_size_table(fn, call, derive=True)
        g = g.substitute_sizes(tab)
        if self._leaked_names(g, fn, substituted=True):
            return None
        return g

    # ---------------------------------------------------------------- 圈数兜底（②'）
    #
    # **问题**（2026-11-12，局部容器尺寸 ②）：`replace_all` 里的
    # `append_range(out, src, i, at)`，增长式是 `hi`；`at` 是 `find_from(…)`
    # 的返回值（局部标量），调用点换不出上界 ⇒ `|out|` = ⊤ ⇒ `masked` = ⊤。
    #
    # **可证的兜底**：`append_range` 的循环体**无条件读** `src[i]`
    # （`for i in [lo, hi) { b.push(src[i]); }`），`src` 是 **const 容器形参**。
    # 良定义执行里 `i ≥ n(src)` 的那一次读就是越界 ⇒ **圈数 ≤ n(src)**，于是
    # `hi ≤ n(src)`（调用点用**实参**的尺寸，`append_range(out, to, 0, to.size())`
    # 得到的是 `n(to)`，不是 `n(src)` —— 位置对应关系必须按形参查表）。
    #
    # **只认这一种形状**（少一条就返回 None，不猜）：
    #   · `while (i < s)` / `while (i <= s)`，`s` 正是传入的标量形参名；
    #   · 体**最后一条**语句是 `i = i + 1`（for 降级后的标准步进）；
    #   · 体内没有对 `i` 的其它赋值；
    #   · 体**顶层**某条语句（不是 if/循环里）含 `C[i]`，C 是 const 容器形参。
    #     "顶层"是为了排除 `if (c) x = src[i]`——被跳过的那次读不能证明越界。

    def _scalar_index_ceiling(self, fn: Node, sname: str) -> Optional[str]:
        """`sname` 若被当作"const 容器下标循环"的圈数上界，返回那个容器形参名。"""
        if not sname:
            return None
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        const_containers: set = set()
        for p in (pnode.children if pnode is not None else []):
            if not p.name:
                continue
            ty = p.children[0] if (p.children or []) else None
            if not _is_container_type(ty):
                continue
            if _is_ref_type(ty) and not bool(
                    (getattr(p, "value", None) or {}).get("const")):
                continue                    # 非 const 引用：体内可能被写长
            const_containers.add(p.name)
        if not const_containers:
            return None
        for loop in _descendants(fn, "while"):
            cond = loop.children[0] if loop.children else None
            if cond is None or getattr(cond, "kind", "") != "binary":
                continue
            if (cond.value or "") not in ("<", "<="):
                continue
            a = cond.children[0] if cond.children else None
            b = cond.children[1] if len(cond.children or []) > 1 else None
            if not (getattr(a, "kind", "") == "path"
                    and getattr(b, "kind", "") == "path"
                    and getattr(b, "name", "") == sname):
                continue
            counter = getattr(a, "name", "") or ""
            body = next((c for c in (loop.children or [])
                         if c.kind == "block"), None)
            if not counter or body is None or not body.children:
                continue
            stmts = list(body.children)
            if not self._is_counter_step(stmts[-1], counter):
                continue
            rest = stmts[:-1]
            if any(self._writes_name(st, counter) for st in rest):
                continue
            hit = self._uncond_index_read(rest, counter, const_containers)
            if hit is not None:
                return hit
        return None

    def _is_counter_step(self, st, counter: str) -> bool:
        """`counter = counter + 1` / `counter += 1`（for 降级后的标准步进）。

        **只认"增大"**：递减的 `while (i < s) { …; i = i - 1 }` 不终止，
        "圈数 ≤ n(C)" 对它没有意义 ⇒ 返回 False（判不出界）。
        """
        if st is None:
            return False
        k = getattr(st, "kind", "")
        if k not in ("assign", "assign-op"):
            return False
        lhs = st.children[0] if st.children else None
        if not (getattr(lhs, "kind", "") == "path"
                and getattr(lhs, "name", "") == counter):
            return False
        rhs = st.children[1] if len(st.children or []) > 1 else None
        if k == "assign-op":
            return (st.value or "") == "+="
        if rhs is None or getattr(rhs, "kind", "") != "binary":
            return False
        if (rhs.value or "") != "+":
            return False
        a = rhs.children[0] if rhs.children else None
        b = rhs.children[1] if len(rhs.children or []) > 1 else None
        return (getattr(a, "kind", "") == "path"
                and getattr(a, "name", "") == counter
                and _int_literal(b) == 1)

    def _writes_name(self, node, name: str) -> bool:
        """子树里有没有对 `name` 的赋值（整名字或 `name[...]`）。"""
        for d in _descendants(node, "assign"):
            lhs = d.children[0] if d.children else None
            if self._lhs_names(lhs) & {name}:
                return True
        for d in _descendants(node, "assign-op"):
            lhs = d.children[0] if d.children else None
            if self._lhs_names(lhs) & {name}:
                return True
        return False

    def _lhs_names(self, lhs) -> set:
        """赋值目标的基名（`p` / `p[i]` / `p.v` 都算 `p`）。"""
        while lhs is not None:
            k = getattr(lhs, "kind", "")
            if k in ("index", "field") and lhs.children:
                lhs = lhs.children[0]
                continue
            if k == "path":
                return {getattr(lhs, "name", "") or ""}
            return set()
        return set()

    def _uncond_index_read(self, stmts, counter: str, containers) -> Optional[str]:
        """**顶层**语句里有没有无条件的 `C[counter]` 读；有 → 返回 C。"""
        for st in (stmts or []):
            k = getattr(st, "kind", "")
            if k in ("if", "while", "for", "match", "block", "parallel"):
                continue                    # 条件/循环里的读：被跳过时不能证明越界
            for d in _descendants(st, "index"):
                if len(d.children or []) < 2:
                    continue
                base, idx = d.children[0], d.children[1]
                if (getattr(base, "kind", "") == "path"
                        and getattr(base, "name", "") in containers
                        and getattr(idx, "kind", "") == "path"
                        and getattr(idx, "name", "") == counter):
                    return base.name
        return None

    def _container_bounds_full(self, fn: Node):
        """**全量**的局部容器尺寸上界：
        `(bounds, 局部容器名, 容器形参名, row_of, caps)`。

        与 `_local_container_bounds` 同一次推导，只是**不**按「被当作轮数」
        裁剪。调用点（`_param_size_table`）需要一个局部容器**自己的**长度上界：
        `decode(enc)` 里 `enc` 的长度不是输入尺寸量，但它由 `encode(data)` 的
        返回尺寸决定——推得出来就该用表达式，推不出来才是 ⊤。

        结果按函数缓存（`_bounds_cache`）：一次推导要扫全函数体，而每个调用点
        都会问。**缓存值只读**（本文件里没有任何地方改返回的 dict）。
        """
        hit = self._bounds_cache.get(id(fn))
        if hit is not None and hit[0] is fn:
            return hit[1], hit[2], hit[3], hit[4], hit[5]
        # **正在算这个函数的界**：不再递归进去（`main` 扫描自己体内的调用时
        # 会问「`main` 的形参尺寸」）。**但要把"扫到这一句为止"的偏界给出去**
        # ——增长预扫是顺序的，`tmp` 的界在它被使用之前就已经算出来了；
        # 给空界会让 `fresh(tmp)` 变成 ⊤，比真实情况保守得多。
        if id(fn) in self._bounds_busy:
            for _f, _b, _e, _c, _r, _p in reversed(self._scan_ctx):
                if _f is fn:
                    return _b, set(), _p, _r, _c
            return {}, set(), self._container_params_of(fn), {}, {}
        self._bounds_busy.add(id(fn))
        try:
            return self._container_bounds_impl(fn)
        finally:
            self._bounds_busy.discard(id(fn))

    def _container_bounds_impl(self, fn: Node):
        """`_container_bounds_full` 的实现体（缓存/重入由外层管）。"""
        decls = []
        counts: Dict[str, int] = {}
        local_names = set()
        rowlike = set()
        for d in (list(_descendants(fn, "const-decl"))
                  + list(_descendants(fn, "var-decl"))):
            nm = getattr(d, "name", None)
            if not nm:
                continue
            kind = _container_type_name(d.children[0] if d.children else None)
            if kind is None:
                continue
            decls.append((d, kind))
            local_names.add(nm)
            counts[nm] = counts.get(nm, 0) + 1
            # 行别名（`row = m.at(i)`）：尺寸归外层容器是既有机制（`row_of`），
            # 这里不把它当独立尺寸量——否则 `var` 重赋值那条保守测试会翻。
            if self._is_row_like(d):
                rowlike.add(nm)
        params = set()
        for p in _descendants(fn, "param"):
            ty = next((c for c in (p.children or [])
                       if c.kind in ("type-ref", "type-name")), None)
            if p.name and _container_type_name(ty):
                params.add(p.name)
        local_names -= rowlike
        # **结构体局部变量的容器字段**（①，2026-11-12）：`Bits out;` 的 `out.words`
        # 也是一个"局部容器"——κ 里写成 `n(out.words)`（点分名，与
        # `_container_size_symbol` / `_receiver_size_renames` 的写法一致）。
        # 它的界 = 声明处初值（默认构造 = 空）+ 函数体里的**插入次数**，
        # 由 `_struct_local_field_bound` 一次算完（**不进**顺序增长预扫：那会把
        # 同一批插入记两遍 ⇒ 只会变大，但会让本来能证的界变成"巨大"）。
        sf_decls = []
        for d in (list(_descendants(fn, "const-decl"))
                  + list(_descendants(fn, "var-decl"))):
            nm = getattr(d, "name", None)
            if not nm:
                continue
            sname = self._struct_of_type(d.children[0] if d.children else None)
            if sname is None:
                continue
            for f in self._struct_container_fields(sname):
                sf_decls.append((d, nm + "." + f, f, sname))
        if not decls and not sf_decls:
            self._bounds_cache[id(fn)] = (fn, {}, local_names, params, {}, {})
            return {}, local_names, params, {}, {}

        # **同名多处声明**：作用域不同，不能把一个界套到另一个上 → 不替换。
        bounds: Dict[str, Optional[Cost]] = {}
        for d, _kind in decls:
            nm = d.name
            if counts.get(nm, 0) > 1:
                bounds[nm] = None
            elif nm not in bounds:
                bounds[nm] = self._initial_container_bound(d, fn)
        for d, key, f, sname in sf_decls:
            counts[key] = counts.get(key, 0) + 1
            local_names.add(key)
            if counts[key] > 1:
                bounds[key] = None
            elif key not in bounds:
                bounds[key] = self._struct_local_field_bound(fn, d.name, f, sname)

        env: Dict[str, Cost] = {}
        caps: Dict[str, int] = {}
        row_of: Dict[str, str] = {}
        const_names = {getattr(d, "name", "") for d in _descendants(fn, "const-decl")
                       if getattr(d, "name", "")}
        body = next((c for c in (fn.children or []) if c.kind == "block"), None)
        if body is not None:
            # `_scan_*` 需要知道「这个局部是 map 还是 vector」——下标赋值
            # （`m[k] = v`）只对 map 是插入。放在实例字段上，省得给
            # 每个递归调用再传一个参数（单线程，下一次 prepass 会覆盖）。
            # **这个字段是共享的**：本方法可能在另一个函数的扫描进行中被调用
            # （`_param_size_table` → 返回尺寸 → 扫被调函数），必须存/还。
            _prev_kinds = getattr(self, "_scan_kinds", None)
            _prev_written = self._scan_written
            # **`cur_fn` 指向被扫的这个函数**（与 `cost_of` 一致）：`_bound_to_cost_scoped`
            # 认标量形参时看 `cur_fn`。不换的话，「谁先调用了
            # `_container_bounds_full(fn)`」会决定 `while (i < k)` 里的 `k` 渲染成
            # `sym(k)` 还是 `n(k)`——而 `n(k)` 是一个**查不到的局部名**（求值按 1
            # 算 ⇒ 低估），还会被缓存下来。
            _prev_cur = self.cur_fn
            self.cur_fn = fn
            self._scan_kinds = {d.name: kind for d, kind in decls}
            # 预扫是**顺序**的：`_scan_written` 每份预扫一份新的（见 `_name_count`）。
            self._scan_written = set()
            # `_for_count` 用 `_fn_stack[-1]` 认「这是哪一帧的局部别名表」；
            # 扫一个**别的**函数时不能拿调用者的帧。
            self._fn_stack.append(fn)
            # 偏界上下文：重入本函数时（`_container_bounds_full`）用它回答。
            self._scan_ctx.append((fn, bounds, env, caps, row_of, params))
            try:
                self._scan_growth_stmts(body.children, Cost.num(1), bounds, env,
                                        caps, row_of, const_names, params, fn)
            finally:
                self._scan_ctx.pop()
                self._fn_stack.pop()
                self._scan_kinds = _prev_kinds
                self._scan_written = _prev_written
                self.cur_fn = _prev_cur
        # 界里可能还残留另一个局部容器的尺寸（按声明顺序已经解析过一遍，
        # 这里再做一次不动点收尾；成环就判推不出）。
        for nm in list(bounds):
            if bounds[nm] is not None:
                _r = self._resolve_local_cost(bounds[nm], bounds, env, {nm})
                # **归约必须停在形参/常量**（规则⑤）：链断在某个没有界的
                # 局部容器上时，`_resolve_local_cost` 会原样留下 `n(它)`。
                # 那不是"算出了界"，是把局部名冒充成尺寸量 → 丢掉这个界。
                bounds[nm] = self._only_roots(_r, local_names, params)
        self._bounds_cache[id(fn)] = (fn, bounds, local_names, params, row_of, caps)
        return bounds, local_names, params, row_of, caps

    def _only_roots(self, c: Optional[Cost], local_names, params
                    ) -> Optional[Cost]:
        """界里只剩**形参/常量/模块值**才留下；还有局部容器尺寸名 → None（⊤）。

        **同名遮蔽也算局部**：局部容器把形参遮蔽时，`n(名字)` 指的到底是哪个
        说不清——这种界不能当"追到了形参"，宁可 ⊤。
        """
        if c is None:
            return None
        bad: List[str] = []

        def walk(x) -> None:
            if x is None:
                return
            if x.kind == "size" and x.value in local_names:
                bad.append(str(x.value))
            if x.kind == "piece":
                for _lo, _hi, sub in x.value[1]:
                    walk(sub)
            for a in getattr(x, "args", ()) or ():
                walk(a)

        walk(c)
        return None if bad else c

    def _container_params_of(self, fn: Optional[Node]) -> set:
        """`fn` 的**容器形参**名（不扫函数体，便宜；给重入时的保守回退用）。"""
        out: set = set()
        if fn is None:
            return out
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        for p in (pnode.children if pnode is not None else []):
            ty = next((c for c in (p.children or [])
                       if c.kind in ("type-ref", "type-name")), None)
            if p.name and _container_type_name(ty):
                out.add(p.name)
        return out

    def _is_row_like(self, d) -> bool:
        """`row = m.at(i)` 这种行别名声明。"""
        if len(d.children or []) < 2:
            return False
        val = d.children[-1]
        if getattr(val, "kind", "") not in ("method-call", "call"):
            return False
        return _callee_short(val) in ("at", "index")

    def _local_size_drivers(self, fn: Node, local_names) -> set:
        """找出「尺寸被当作循环轮数」的局部容器名。"""
        out = set()
        if not local_names:
            return out

        for f in _descendants(fn, "for"):
            it = next((c for c in (f.children or []) if c.kind != "block"), None)
            if it is None:
                continue
            if it.kind == "path" and it.name in local_names:
                out.add(it.name)
            elif it.kind in ("method-call", "call"):
                if _callee_short(it) in ("keys", "values", "items", "entries", "iter"):
                    recv = (it.children[0]
                            if it.children and it.children[0].kind == "path" else None)
                    if recv is not None and recv.name in local_names:
                        out.add(recv.name)
            out |= self._size_called_names(it, local_names)
        for w in _descendants(fn, "while"):
            out |= self._size_names_via_aliases(
                w.children[0] if w.children else None, fn, local_names)
        return out

    def _size_names_via_aliases(self, node, fn: Optional[Node], names,
                                seen=None) -> set:
        """条件里的 `n(局部容器)`，**含经尺寸别名的一跳**。

        `u64 m = ks.size(); while (i < m)` 的轮数推导会展开成 `n(ks)`
        （`_count_of_expr` 认局部别名），但条件里根本没有 `ks.size()` ——
        只扫条件会漏掉 `ks`，于是 `n(ks)` 留在 κ 里查不到界。
        这里顺着别名初值再找一跳（与轮数推导用的是**同一份** `_decl_inits`）。
        """
        out = self._size_called_names(node, names)
        if node is None or fn is None or not names:
            return out
        seen = set(seen or ())
        inits = self._decl_inits(fn)
        for p in _descendants(node, "path"):
            nm = getattr(p, "name", "")
            if not nm or nm in seen or nm not in inits:
                continue
            out |= self._size_names_via_aliases(
                inits[nm], fn, names, seen | {nm})
        return out

    def _size_called_names(self, node, names) -> set:
        """表达式里被 `.length()/.size()/...` 取尺寸的局部容器名。"""
        out = set()
        if node is None or not names:
            return out
        for d in _descendants(node, "method-call"):
            if _callee_short(d) in SIZE_FNS:
                recv = (d.children[0]
                        if d.children and d.children[0].kind == "path" else None)
                if recv is not None and recv.name in names:
                    out.add(recv.name)
        return out

    def _growth_targets(self, node, known) -> set:
        """子树里被写入（或保守地可能被改写）的局部容器名。"""
        out = set()
        if node is None or not known:
            return out
        for d in _descendants(node, "method-call"):
            short = _callee_short(d)
            recv = (d.children[0]
                    if d.children and d.children[0].kind == "path" else None)
            if recv is None or recv.name not in known:
                continue
            if short in _GROWTH_METHODS:
                out.add(recv.name)
            elif short not in _SAFE_METHODS:
                out.add(recv.name)
        for d in _descendants(node, "call"):
            args = next((c for c in (d.children or []) if c.kind == "args"), None)
            if args is None:
                continue
            short = _callee_short(d)
            for _i, a in enumerate(args.children or []):
                while getattr(a, "kind", "") in ("ref", "paren"):
                    a = a.children[0] if a.children else None
                if getattr(a, "kind", "") == "path" and a.name in known:
                    if short in _GROWTH_BUILTINS and _i == 0:
                        out.add(a.name)
                    elif short not in _SAFE_METHODS:
                        out.add(a.name)
        return out

    def _initial_container_bound(self, d, fn: Optional[Node] = None
                                 ) -> Optional[Cost]:
        """声明处的初始规模上界。"""
        ty = d.children[0] if d.children else None
        cap = type_capacity_inst(ty, self.const_values)
        if cap is not None:
            return Cost.num(cap)
        has_init = (getattr(d, "value", None) or {}).get(
            "has_init", len(d.children or []) > 1)
        if not has_init:
            return Cost.num(0)          # 默认构造的 map/vector：空
        val = d.children[-1] if len(d.children or []) >= 2 else None
        return self._static_container_bound(val, fn)

    def _static_container_bound(self, val, fn: Optional[Node] = None
                                ) -> Optional[Cost]:
        """只看字面量/构造器的初始规模；路径交给扫描阶段（按声明顺序）。"""
        return self._container_size_bound(val, None, None, None, fn)

    def _container_expr_bound(self, val, bounds, env, params) -> Optional[Cost]:
        """容器表达式（初值/赋值右值）的规模上界；推不出 → None。"""
        return self._container_size_bound(
            val, bounds, env, params, self._cur_frame_fn())

    # ---------------------------------------------------------------- 容器尺寸的五条来源
    #
    # **问题**（2026-11-12）：`ks = m.keys()` / `pieces = split(s, sep)` /
    # `out.push_back(...)` 这类"建一个局部容器再遍历"的内核，κ 里只剩
    # `n(ks)`。`ks` 是局部名，spec 的 target 引用不到 ⇒ E23 / ⊤。实测：
    #
    #     u64 walk(map<u64,u64>& m) { for (auto it = m.begin(); …) … }
    #     → `局部容器 'ks' 的尺寸无法在 target 中引用`
    #
    # **解法**：给每个局部容器一个**指向形参/常量的上界表达式**，规则五条：
    #
    # | # | 构造 | 上界 | 为什么可证 |
    # |---|---|---|---|
    # | ① | `ks = m.keys()` / `.values()` / `.items()` / … | `|ks| ≤ |m|` | 逐元素对应（键/值/条目各一条） |
    # | ② | `out.push*` 出现在"遍历 xs 的循环体、每轮 ≤1 次" | `|out| ≤ n(xs)` | 增长预扫按轮数累计（`_scan_*`，既有机制） |
    # | ③ | `pieces = split(s, sep)` | `|pieces| ≤ n(s)+1` | 分隔符每处最多切一刀 |
    # | ④ | `filled(v,n)` / 数组字面量 | `n` / 常量（`array<T,N>` 用容量） | 构造时就知道 |
    # | ⑤ | 其余 | **⊤**（`None`） | 不许猜 |
    #
    # **归约一直做到形参或常量**：`_resolve_local_cost` 递归查 `bounds`，
    # 最后在 `_container_bounds_impl` 的收尾里再用 `_only_roots` 检查一次——
    # 还有"局部容器尺寸名"就说明链断了（⊤），**绝不把局部名留在界里冒充尺寸量**。
    #
    # **方向**：替换只能是 `n(局部) → ≥ 它的表达式`。规则①③都是"至多一样长"，
    # 代入后 κ 变大或不变 ⇒ 仍是上界（硬约束，见 `test_kappa_local_container.py`）。

    def _container_size_symbol(self, e: Optional[Node]) -> Optional[Cost]:
        """容器表达式 → **可写进 target 的尺寸符号** `n(名字)`；写不出 → None。

        只认名字链：`xs` → `n(xs)`；`s.v`（结构体容器字段）→ `n(s.v)`。
        `xs[i]` / `f(x)` 这种"元素/临时"没有可引用的尺寸名，返回 None。
        """
        while e is not None and getattr(e, "kind", "") in ("ref", "paren"):
            e = e.children[0] if e.children else None
        if e is None:
            return None
        k = getattr(e, "kind", "")
        if k == "path" and getattr(e, "name", ""):
            return Cost.size(e.name)
        if k == "field":
            nm = field_chain_name(e)
            return Cost.size(nm) if nm else None
        return None

    def _self_concat_extra(self, rhs, name: str, bounds=None, env=None,
                           params=None) -> Optional[Cost]:
        """`<name> = <name> + e` 里**每执行一次增长多少**；不是自引用 → None。

        只看形状：`x = x + e` / `x = e + x` / `x = x + e1 + e2`（左结合链）。
        返回的是 `e` 的长度上界（推不出 → None，调用方退回 `max` 口径）。
        """
        if rhs is None or getattr(rhs, "kind", "") != "binary":
            return None
        if (rhs.value or "") != "+":
            return None

        def _len(e):
            return self._container_expr_bound(e, bounds or {}, env or {},
                                              params or set())

        a = rhs.children[0] if (rhs.children or []) else None
        b = rhs.children[1] if len(rhs.children or []) > 1 else None
        if getattr(a, "kind", "") == "path" and getattr(a, "name", "") == name:
            return _len(b)
        if getattr(b, "kind", "") == "path" and getattr(b, "name", "") == name:
            return _len(a)
        # `x = (x + e1) + e2`：左子树自引用，右边是新增量，两段都算增长。
        left = self._self_concat_extra(a, name, bounds, env, params)
        if left is not None:
            right = _len(b)
            return None if right is None else Cost.add(left, right)
        right = self._self_concat_extra(b, name, bounds, env, params)
        if right is not None:
            extra = _len(a)
            return None if extra is None else Cost.add(extra, right)
        return None

    def _container_size_bound(self, val, bounds, env, params,
                              fn: Optional[Node] = None) -> Optional[Cost]:
        """容器表达式规模上界的**唯一实现**（静态/动态两个入口共用）。

        `bounds is None` = 静态口径（只看构造器与名字，不做局部归约）。
        """
        _static = bounds is None
        if val is None:
            return Cost.num(0)
        while getattr(val, "kind", "") in ("ref", "paren"):
            val = val.children[0] if val.children else None
            if val is None:
                return Cost.num(0)

        def _size_of(e):
            """名字链 → `n(名)`；动态口径下先查局部界（递归归约）。"""
            while e is not None and getattr(e, "kind", "") in ("ref", "paren"):
                e = e.children[0] if e.children else None
            if e is None:
                return None
            if getattr(e, "kind", "") == "path" and getattr(e, "name", ""):
                if _static:
                    return Cost.size(e.name)
                return self._name_size_bound(e.name, bounds, env, params)
            return self._container_size_symbol(e)

        k = getattr(val, "kind", "")
        if k == "binary" and (val.value or "") == "+":
            # ④' **串接**：`text = text + "abc"` 的长度 = 两边长度之和。
            # 这是构造（不是"猜"）：拼接不会比两段加起来更长。
            # 文献里"边读边拼"的写法（`text += line`）全靠这一条才有界。
            a = val.children[0] if (val.children or []) else None
            b = val.children[1] if len(val.children or []) > 1 else None
            la = self._container_size_bound(a, bounds, env, params, fn)
            lb = self._container_size_bound(b, bounds, env, params, fn)
            if la is None or lb is None:
                return None
            return Cost.add(la, lb)
        if k in ("list-init", "list-lit", "array", "vec-lit"):
            return Cost.num(len(val.children or []))
        if k == "lit-string":
            # **`vector<u8> a = "(([]))"`**：字面量就是长度（逐字节），
            # 不需要扫函数体就知道。漏了这一支，`a` 的界是 None ⇒ 调用点
            # 只能判 ⊤（`_diff/bracket_match` 的 `main` 就是这样）。
            try:
                return Cost.num(len(val.value or b""))
            except TypeError:
                return None
        if k == "path":
            return _size_of(val)
        if k == "field":
            # 结构体容器字段：`const Vec<u64> v = s.v;` → `n(s.v)`（可写进 target）。
            return self._container_size_symbol(val)
        if k in ("method-call", "call"):
            short = _callee_short(val)
            if short in _EMPTY_CTOR_SHORTS:
                # **只有限定名构造才当空容器**：裸的 `make(xs)` 可能是用户函数，
                # 返回多大不知道 —— 当 0 会让上界偏小（`map_new` 一类别名是
                # 内建、没有歧义，`_EMPTY_CTOR_SHORTS` 已经把它们收进来）。
                if _is_qualified_call(val) or short in ("map_new", "set_new",
                                                        "vec_new", "deque_new"):
                    return Cost.num(0)
                return None
            # **用户函数优先**（与 `_call_inner` 的派发顺序一致）：用户写了
            # 同名 `split`，跑的是他那一份，不能用内建的"切分"规则。
            if k == "call" and short in self.fns:
                # **`enc = encode(data)`（D2）**：局部容器的长度由**被调函数的
                # 返回尺寸**决定。以前这里一律 None ⇒ 界是 None ⇒ 调用点只能写
                # `n(enc)`（拿局部名当尺寸量）⇒ E23。推得出来就用表达式。
                return self._ret_size_of(short, val)
            recv = (val.children[0]
                    if (getattr(val, "children", None)
                        and getattr(val.children[0], "kind", "") in
                        ("path", "field")) else None)
            if short in _DERIVED_SIZE_SHORTS:
                # ① 派生：`ks = m.keys()` / `vs = m.values()` / `.items()` /
                #    `.entries()` / `.iter()` / `clone/copy/to_vec`。
                #    逐元素对应 ⇒ 长度不超过源容器。源是局部时继续往上追
                #    （`_size_of` → `_name_size_bound` → `_resolve_local_cost`）。
                return _size_of(recv) if recv is not None else None
            if short in _SPLIT_SIZE_SHORTS:
                # ③ 切分：段数 ≤ 源长度 + 1（每个分隔符处最多一刀）。
                args = next((c for c in (val.children or [])
                             if getattr(c, "kind", "") == "args"), None)
                src = (args.children[0]
                       if args is not None and args.children else None)
                if src is None:
                    src = recv           # 方法写法 `s.split(sep)`
                base = _size_of(src)
                if base is None:
                    # 源是 `lines[i]`（元素）这种没有尺寸名的表达式：
                    # 元素长度与 `n(lines)` 无关，**不猜**。
                    return None
                return Cost.add(base, Cost.num(1))
            if short == "filled":
                # ④ `vector<T>::filled(v, n)`：长度就是 n（`array<T,N>::filled`
                #    的容量在 `type_capacity_inst` 那一支已经先返回了）。
                args = next((c for c in (val.children or [])
                             if getattr(c, "kind", "") == "args"), None)
                cnt = (args.children[-1] if args is not None
                       and len(args.children or []) >= 2 else None)
                if cnt is None:
                    return None
                return self._count_of_expr(cnt, fn)
            return None
        return None

    def _name_size_bound(self, name: str, bounds, env, params) -> Optional[Cost]:
        """名字的规模上界：局部容器 → 已知界；输入容器 → `n(名字)`。"""
        if not name:
            return None
        if name in bounds:
            b = bounds[name]
            return None if b is None else self._resolve_local_cost(b, bounds, env)
        if name in env:
            v = env.get(name)
            return v if v is not None and v.kind in ("size", "num") else None
        if name in params:
            return Cost.size(name)
        return None

    # ---------------------------------------------------------------- 返回尺寸（D2）
    #
    # **问题**：`n(?)` / `n(局部名)` / `n(被调形参名)` 都是"我不知道这个容器多大"
    # 的**伪装**。求值时按 1 算，判定时却当一个真正的尺寸量参与比较——
    # 于是要么误报债务（`fmt_scalar` 的比值 ≈ 1e12），要么 E23 判不了
    # （`roundtrip` 的 `n(enc)`）。
    #
    # **口径（两条）**：
    #   1. **能推就推**：用户函数返回容器的长度有上界——由函数体的
    #      `return <expr>` 决定（局部容器用增长预扫的界；形参用 `n(形参)`；
    #      嵌套调用递归推）。再用调用点的实参把形参名换掉，就是**调用方符号**
    #      写出来的表达式（`encode(data)` → `n(data)×4`）。
    #   2. **推不出就 ⊤ + 原因**：绝不写 `n(?)`，也绝不把被调函数的形参名
    #      （或局部名）留在调用方的 κ 里当尺寸量。

    def _cur_frame_fn(self) -> Optional[Node]:
        """当前正在遍历的函数体是哪个（`_fn_stack` 顶；没有则 `cur_fn`）。"""
        if self._fn_stack:
            return self._fn_stack[-1]
        return self.cur_fn

    def _unknown_size(self, why: str) -> "Cost":
        """记一句「尺寸推不出」的原因，并返回 ⊤。

        **为什么不是 `n(?)`**：`?` 会被 `cost_vars()` 当成一个普通尺寸量，
        参与 `compare()` 的尺寸量配对——那是把"不知道"冒充成"知道"。
        返回 ⊤ 让 `compare()` 走「κ 是 ⊤ → 无法判定」，并在 `cost` 里打印原因。
        """
        if why and why not in self._size_unknown:
            self._size_unknown.append(why)
        return Cost.top()

    def _ret_size_of(self, name: str, call_node: Optional[Node]) -> Optional["Cost"]:
        """用户函数 `name(...)` 返回的**容器长度上界**（调用方符号写）；推不出 → None。

        `call_node` 是调用点：用来把被调函数的形参名换成实参尺寸
        （`_param_size_table`）。没有调用点（例如从别处推导时）就保留形参名。
        """
        fn = self.fns.get(name)
        if fn is None:
            return None
        ret = _ret_type_of(fn)
        if ret is None or _container_type_name(ret) is None:
            return None                      # 返回的不是容器：没有"长度"可谈
        if name in self._size_chain:
            return None                      # 返回尺寸互相依赖成环：不猜
        self._size_chain.append(name)
        try:
            bounds, _ln, params, row_of, _caps = self._container_bounds_full(fn)
            body = next((c for c in (fn.children or []) if c.kind == "block"), None)
            sizes: List[Cost] = []
            for r in _descendants(body, "return"):
                e = r.children[0] if r.children else None
                sz = self._returned_size(e, bounds, params, row_of, fn)
                if sz is None:
                    return None              # 有一条出口推不出 ⇒ 整体推不出
                sizes.append(sz)
            if not sizes:
                return None
            total = sizes[0]
            for s in sizes[1:]:
                total = Cost.max(total, s)
            # **成员尺寸是对象相对的**：`return recv.first_;` 的尺寸是
            # `n(recv.first_)`，调用点上必须换成实参那个对象的名字
            # （`n(solver.first_)`）。换不掉就推不出（不许留被调方形参名）。
            _ren, _leak = self._receiver_size_renames(fn, call_node, total)
            if _leak:
                return None
            if _ren:
                total = total.substitute_sizes(_ren)
            # **返回尺寸里的局部名也要换掉**（2026-11-13，与内联同一条硬约束）：
            # `matmul` 的 `return c;` 尺寸是 `max(n, n(a))`，`n` 是它的局部标量。
            # 内联/取返回尺寸时把 `n` 原样交给调用方，调用方根本没有这个名字
            # （`03_matrix.main` 的 `max(n, 2)`）。能换成值上界就换（`n → n(a)`）；
            # 证不出的（被改写的局部）原样留着、由 `cost_of` 出口登记（不退 ⊤，
            # 理由见 `_resolve_callee_scalar_locals` 的末段）。
            # **要在 `tab` 之前做**：`n → n(a)` 里的 `a` 还是被调方的形参，
            # 紧接着的 `substitute_sizes(tab)` 才把它换成调用方的容器。
            total = self._resolve_callee_scalar_locals(total, fn)
            if total is None:
                return None
            tab = (self._param_size_table(fn, call_node)
                   if call_node is not None else {})
            total = total.substitute_sizes(tab)
            # **标量形参也要在调用点实例化**（2026-11-12，②）：返回尺寸常常是
            # `hi`（`slice` 的 `return out;` 的界）这种**标量形参**。只换容器尺寸
            # 会把 `hi` 原样留在调用方的界里 —— 调用方查不到这个名字（求值按 1 算
            # ⇒ 低估），于是 `head = slice(masked, 0, 11)` 的界只能写 `n(hi)` 或 ⊤。
            # `loose=True`：实参是 `masked.size()` 时按上界展开成 `n(masked)`。
            if call_node is not None:
                sub = self._subst_scalar_syms(total, fn, call_node, loose=True)
                # **换不出来就保留原式**，不要退 ⊤：`zeros(n)` 的返回尺寸是
                # `sym(n)`，调用点 `zeros(a.size())` 里 `n` 是**裸局部名**
                # （严格口径不收），可它在调用方作用域里也常常就叫 `n`
                # （03_matrix 的 `std::size_t n = a.size()`）——换掉反而把
                # 原来能判的 `c` 变成"局部容器尺寸无法在 target 中引用"。
                # 留下的 `sym(形参名)` 求值时是 ⊤（不是 1），**不会低估**。
                if sub is not None:
                    total = sub
            return total
        finally:
            self._size_chain.pop()

    def _returned_size(self, e, bounds, params, row_of,
                       fn: Optional[Node] = None) -> Optional["Cost"]:
        """`return <e>;` 里容器的长度上界（被调函数自己的尺寸符号）。"""
        while e is not None and getattr(e, "kind", "") in (
                "ref", "paren", "try", "cast", "deref"):
            e = e.children[0] if e.children else None
        if e is None:
            return None
        k = getattr(e, "kind", "")
        if k == "path":
            nm = e.name or ""
            if not nm:
                return None
            if nm in row_of:
                return Cost.size(row_of[nm])
            if nm in bounds:
                b = bounds[nm]
                return None if b is None else self._resolve_local_cost(b, bounds, {})
            if nm in params:
                return Cost.size(nm)
            return None
        if k == "field":
            # **返回成员容器**（2026-11-12，`10_backtrack` / `first_solution`）：
            #
            #     const std::vector<u64>& first_solution() const { return first_; }
            #
            # 返回的是这个成员的**引用**，长度就是 `n(recv.first_)` —— 与
            # `_container_size_symbol` 对 `const Vec<T> v = s.v;` 的写法同一条
            # 口径（那一条已经在这个仓库里用了很久）。调用点由
            # `_receiver_size_renames` 换成实参那个对象的名字（`n(solver.first_)`）。
            # **只认形参的字段**：局部结构体的容器字段已经在 `bounds` 里。
            nm = field_chain_name(e)
            if nm:
                if nm in bounds:
                    b = bounds[nm]
                    return (None if b is None
                            else self._resolve_local_cost(b, bounds, {}))
                pnode = next((c for c in ((fn.children if fn is not None else [])
                                          or []) if c.kind == "params"), None)
                pnames = {p.name for p in (pnode.children
                                           if pnode is not None else []) if p.name}
                if nm.split(".", 1)[0] in pnames:
                    return Cost.size(nm)
            return None
        if k == "lit-string":
            try:
                return Cost.num(len(e.value or b""))
            except TypeError:
                return None
        if k in ("list-init", "list-lit", "array", "vec-lit"):
            return Cost.num(len(e.children or []))
        if k in ("call", "method-call"):
            short = _callee_short(e)
            if short in _EMPTY_CTOR_SHORTS:
                if _is_qualified_call(e) or short in ("map_new", "set_new",
                                                      "vec_new", "deque_new"):
                    return Cost.num(0)
                return None
            if short in ("clone", "copy", "to_vec", "keys", "values",
                         "items", "entries", "iter"):
                recv = e.children[0] if e.children and e.children[0].kind == "path" else None
                return (self._returned_size(recv, bounds, params, row_of, fn)
                        if recv is not None else None)
            if k == "call" and short in self.fns:
                return self._ret_size_of(short, e)
        return None

    def _container_expr_size(self, e) -> Optional["Cost"]:
        """容器表达式 `e` 的长度上界（**用当前这一帧的尺寸符号写**）；推不出 → None。

        用在两处：调用点给容器形参换尺寸（`_param_size_table`），
        以及 `format(...)` 各实参的长度上界。**推不出就是 None**——
        调用方负责退 ⊤ 并记原因，不许写 `n(?)`。
        """
        while e is not None and getattr(e, "kind", "") in (
                "ref", "paren", "try", "cast", "deref"):
            e = e.children[0] if e.children else None
        if e is None:
            return None
        k = getattr(e, "kind", "")
        fn = self._cur_frame_fn()
        if fn is None:
            # **帧栈为空时的兜底**（`_initial_container_bound` 的预扫不压帧）：
            # 按 AST 归属找这个表达式所在的函数，否则 `lines[i]` 的类型/界查不到。
            fn = self._owner_fn_of(e)
        # **先认形参**：不需要扫函数体，也避开「扫自己正在扫的那个函数」的重入。
        params = self._container_params_of(fn)
        if k == "path" and (e.name or "") in params:
            return Cost.size(e.name)
        caps: Dict[str, int] = dict(self.capacities)
        bounds: Dict[str, Optional[Cost]] = {}
        row_of: Dict[str, str] = dict(self.row_of)
        if fn is not None:
            bounds, _ln, params, _row, caps = self._container_bounds_full(fn)
            row_of = _row
        if k == "path":
            nm = e.name or ""
            if not nm:
                return None
            if nm in caps:
                return Cost.num(caps[nm])
            if nm in row_of:
                return Cost.size(row_of[nm])
            if nm in bounds:
                b = bounds[nm]
                return None if b is None else self._resolve_local_cost(b, bounds, {})
            if nm in params:
                return Cost.size(nm)
            # **元素别名的长度**（`for (const auto& w : words)`）：`w` 是
            # `words` 的一个元素 ⇒ `n(w) ≤ L(words)`（2026-11-12 夜；
            # 判据与另外两处共用 `_elem_alias_size_bound`）。
            # 只在源容器"元素带长度"时才认；否则落回旧口径。
            _al = self._elem_alias_size_bound(fn, nm)
            if _al is not None:
                return _al
            ty = self._type_of_expr(e)
            if ty is not None and _is_container_type(ty):
                return None              # 是容器但界推不出：不猜
            return Cost.size(nm)         # 模块级 / 外来名字：沿用旧口径
        if k == "index":
            # **元素（内层容器）的长度**：`g[v]` / `parts[0]` / `ks[idx]` 的长度
            # ≤ `L(根容器)`（2026-11-12 夜；**不是** `n(根)`——每行可以比行数长）。
            # 元素是标量 ⇒ 没有"长度"这回事（`xs[i] + 1` 不是尺寸量）。
            root, depth = self._index_root(e)
            if not root or depth != 1:
                return None              # 更深的下标：长度量写不出来
            # **类型先按 `fn` 的声明查**（`_type_of_expr` 依赖当前帧，而
            # `_container_bounds_impl` 的预扫不压帧——只认帧会在这里漏掉）。
            ety = None
            _rty = self._type_of_name_in(fn, root)
            if _rty is not None:
                ety = self._elem_type(_rty)
            if ety is None:
                ety = self._type_of_expr(e)
            if _container_type_name(ety) is None:
                return None
            return self._elem_bound_by_name(root, fn)
        if k == "field":
            # **结构体字段是容器**：`s.v` 的长度写成 `n(s.v)`（2026-11-12）。
            # 只有类型能证明它是容器时才收——`s.count` 这种标量字段不是尺寸量。
            nm = field_chain_name(e)
            if not nm:
                return None
            ty = self._type_of_expr(e)
            h, _args, _ref = self._type_parts(ty)
            if h in _ARR_HEADS:
                cap = self._array_cap(ty)
                return Cost.num(cap) if cap is not None else None
            if h in _OWNING_HEADS or h in _SEQ_HEADS:
                return Cost.size(nm)
            return None
        if k == "lit-string":
            try:
                return Cost.num(len(e.value or b""))
            except TypeError:
                return None
        if k in ("list-init", "list-lit", "array", "vec-lit"):
            return Cost.num(len(e.children or []))
        if k in ("call", "method-call"):
            short = _callee_short(e)
            if short in _EMPTY_CTOR_SHORTS:
                if _is_qualified_call(e) or short in ("map_new", "set_new",
                                                      "vec_new", "deque_new"):
                    return Cost.num(0)
                return None
            if short in ("clone", "copy", "to_vec", "keys", "values",
                         "items", "entries", "iter"):
                recv = e.children[0] if e.children and e.children[0].kind == "path" else None
                return (self._container_expr_size(recv)
                        if recv is not None else None)
            if k == "call" and short in self.fns:
                return self._ret_size_of(short, e)
        return None

    def _resolve_local_cost(self, c: Optional[Cost], bounds, env,
                            seen=None) -> Optional[Cost]:
        """把成本式里的 `size(局部容器)` 换成它的界；推不出 → None。"""
        if c is None:
            return None
        seen = set(seen or ())
        if c.kind == "size":
            nm = c.value
            # **出参的"当前尺寸"不是已知量**（P0-1）：推增长时 `bounds[出参]`
            # 装的是"插入次数"，拿它当尺寸就等于把增长冒充成大小
            # （`for (i < out.size())` 的轮数会算成 0 —— 低估，上界不成立）。
            if nm in self._out_size_block:
                return None
            if nm in bounds:
                if nm in seen:
                    return None         # 循环依赖：不猜
                b = bounds[nm]
                if b is None:
                    return None
                return self._resolve_local_cost(b, bounds, env, seen | {nm})
            if nm in env:
                v = env.get(nm)
                if v is not None and v.kind in ("size", "num"):
                    return v
            return c
        if c.kind in ("num", "top", "sym", "ext"):
            return c
        if c.kind in ("add", "mul", "max"):
            l = self._resolve_local_cost(c.args[0], bounds, env, seen)
            r = self._resolve_local_cost(c.args[1], bounds, env, seen)
            if l is None or r is None:
                return None
            if c.kind == "add":
                return Cost.add(l, r)
            if c.kind == "mul":
                return Cost.mul(l, r)
            return Cost.max(l, r)
        if c.kind == "log2":
            a = self._resolve_local_cost(c.args[0], bounds, env, seen)
            return None if a is None else Cost.log2(a)
        if c.kind == "div":
            # **分子分母都要解析**：漏一边就等于留着一个查不到的名字
            # （求值按 1 算）。判不了的照旧返回 None（不猜）。
            l = self._resolve_local_cost(c.args[0], bounds, env, seen)
            r = self._resolve_local_cost(c.args[1], bounds, env, seen)
            return None if l is None or r is None else Cost.div(l, r)
        return c

    def _grow_local(self, bounds, name: str, factor: Optional[Cost]) -> None:
        """记一次写入：该容器最多 +1，随当前循环倍数放大。"""
        if name not in bounds:
            return
        if factor is None or bounds[name] is None:
            bounds[name] = None
            return
        bounds[name] = Cost.add(bounds[name], factor)

    def _scan_growth_stmts(self, stmts, factor, bounds, env, caps,
                           row_of, const_names, params, fn) -> None:
        for st in (stmts or []):
            self._scan_growth_stmt(st, factor, bounds, env, caps,
                                   row_of, const_names, params, fn)

    def _scan_growth_stmt(self, st, factor, bounds, env, caps,
                          row_of, const_names, params, fn) -> None:
        k = getattr(st, "kind", "")

        if k in ("const-decl", "var-decl"):
            self._scan_decl_growth(st, factor, bounds, env, caps, row_of, params)
            return

        if k in ("assign", "assign-op"):
            lhs = st.children[0] if st.children else None
            rhs = st.children[1] if len(st.children or []) > 1 else None
            # **被改写的标量名**（P0-1 连带）：`_name_count` 只认声明处初值，
            # 顺序预扫里"先赋过值"的名字不能再拿来当轮数（见 `_name_count`）。
            if (lhs is not None and getattr(lhs, "kind", "") == "path"
                    and getattr(lhs, "name", "")):
                self._scan_written.add(lhs.name)
            self._scan_expr(lhs, factor, bounds)
            # **`m[k] = v` 对 map 是插入**（C++ 下标语义）：记一次增长。
            if (lhs is not None and lhs.kind == "index"
                    and lhs.children and lhs.children[0].kind == "path"):
                _base = lhs.children[0].name
                if getattr(self, "_scan_kinds", {}).get(_base) in ("map", "unordered_map"):
                    self._grow_local(bounds, _base, factor)
            if lhs is not None and lhs.kind == "path" and lhs.name in bounds:
                # **`x = x + e` 是增长，不是"换一个值"**（2026-11-12）。
                # 只用 `max(旧界, 新界)` 会把它记成**一次**的长度：循环里
                # `text = text + "ab"` 重复 n 次，真长 2n，而 `max` 给出 2 ——
                # 那是低估（κ 不再是上界）。所以自引用串接走 `_grow_local`
                # 的同一口径：加上"每轮增量 × 当前倍数"。
                extra = self._self_concat_extra(rhs, lhs.name, bounds, env, params)
                if extra is not None:
                    cur = bounds[lhs.name]
                    if cur is None or factor is None:
                        bounds[lhs.name] = None
                    else:
                        bounds[lhs.name] = Cost.add(
                            cur, Cost.mul(factor, extra))
                else:
                    rb = self._container_expr_bound(rhs, bounds, env, params)
                    cur = bounds[lhs.name]
                    if rb is None or cur is None:
                        bounds[lhs.name] = None
                    else:
                        bounds[lhs.name] = Cost.max(cur, rb)
            self._scan_expr(rhs, factor, bounds)
            return

        if k == "if":
            cond = st.children[0] if st.children else None
            self._scan_expr(cond, factor, bounds)
            # **分支 = `block` 或 `else if`**（`if` 节点）。`else if` 也是 `if` 的
            # 子节点（`parser.parse_if`），只收 `block` 会把 else-if 里的
            # `push` / `m[k] = v` 全部漏掉 —— 容器上界会**偏小**（κ 不是上界）。
            runs = []
            for c in (st.children or [])[1:]:
                if c.kind == "block":
                    runs.append(c.children)
                elif c.kind == "if":
                    runs.append([c])
            if not runs:
                return
            self._scan_branches(runs, factor, bounds, env, caps,
                                row_of, const_names, params, fn)
            return

        if k == "while":
            cond = st.children[0] if st.children else None
            self._scan_expr(cond, factor, bounds)
            body = next((c for c in (st.children or [])[1:] if c.kind == "block"), None)
            f2 = None
            if factor is not None:
                b, _why = bound_of_condition_wrapper(
                    st, self.repeats.get, env, row_of, caps, const_names, fn,
                    resolve=lambda nm, _f=fn: self._name_count(nm, _f),
                    size_alias_skip=self._revoked_size_aliases(fn))
                if b is not None:
                    # **局部别名按初值结构展开**（`const total = a.length()+…`）：
                    # 推不出 → None → f2 = None（这一层的增长上界不猜）。
                    lb = self._bound_to_cost_scoped(b, fn)
                    lb = self._resolve_local_cost(lb, bounds, env)
                    # **界里还有"被改写过的标量名"就是没界**（P0-1 连带）：
                    # `_bound_to_cost_scoped` 对自由名沿用旧口径 `size(名字)`，
                    # 而 `u64 m = n; m = m * 2; while (i < m)` 的真实轮数是 2n。
                    # 留着 `n(m)` 在 κ 里 = 把查不到的局部标量冒充成尺寸量
                    # （求值按 1 算 ⇒ 低估）。宁可 ⊤。
                    if lb is not None and (set(lb.variables())
                                           & self._scan_written):
                        lb = None
                    f2 = None if lb is None else Cost.mul(factor, lb)
            # **`while (i < cnt.length())` 且体内又往 cnt 里写**：轮数不再由
            # 进入循环时的规模决定（条件每轮重算）→ 判推不出，不许拿旧界。
            if body is not None and f2 is not None:
                _cond_dep = self._size_called_names(cond, set(bounds))
                if _cond_dep & self._growth_targets(body, set(bounds)):
                    f2 = None
            if body is not None:
                self._scan_growth_stmts(body.children, f2, bounds, env, caps,
                                        row_of, const_names, params, fn)
            return

        if k == "for":
            it = next((c for c in (st.children or []) if c.kind != "block"), None)
            self._scan_expr(it, factor, bounds)
            body = next((c for c in (st.children or []) if c.kind == "block"), None)
            ib = None if factor is None else self._for_iter_bound(st, bounds, env, params)
            f2 = None if (factor is None or ib is None) else Cost.mul(factor, ib)
            if body is not None:
                self._scan_growth_stmts(body.children, f2, bounds, env, caps,
                                        row_of, const_names, params, fn)
            return

        if k == "match":
            scrut = match_scrutinee(st)
            self._scan_expr(scrut, factor, bounds)
            arms = arms_of(st)
            runs = []
            for a in arms:
                blk = arm_block(a)
                # `_scan_branches` 收的是**语句列表**（见它的说明）：臂是 block
                # 就传 `block.children`，单语句臂传语句列表。
                runs.append(blk.children if blk is not None else arm_single_stmts(a))
            if runs:
                self._scan_branches(runs, factor, bounds, env, caps,
                                    row_of, const_names, params, fn)
            return

        if k == "block":
            self._scan_growth_stmts(st.children, factor, bounds, env, caps,
                                    row_of, const_names, params, fn)
            return

        if k == "parallel":
            body = stmt_body(st)
            for task in (body.children if body is not None else []):
                if getattr(task, "kind", "") == "block":
                    self._scan_growth_stmts(task.children, factor, bounds, env, caps,
                                            row_of, const_names, params, fn)
                else:
                    self._scan_growth_stmt(task, factor, bounds, env, caps,
                                           row_of, const_names, params, fn)
            return

        # 其余语句：扫子表达式（块按语句继续扫）
        for c in (st.children or []):
            if getattr(c, "kind", "") == "block":
                self._scan_growth_stmts(c.children, factor, bounds, env, caps,
                                        row_of, const_names, params, fn)
            else:
                self._scan_expr(c, factor, bounds)

    def _scan_decl_growth(self, d, factor, bounds, env, caps, row_of, params) -> None:
        ty = d.children[0] if d.children else None
        cap = type_capacity_inst(ty, self.const_values)
        has_init = (getattr(d, "value", None) or {}).get(
            "has_init", len(d.children or []) > 1)
        if cap is not None and d.name:
            caps[d.name] = cap
        val = d.children[-1] if (has_init and len(d.children or []) >= 2) else None
        if val is not None:
            self._scan_expr(val, factor, bounds)
        alias = self._row_alias_of(d)
        if alias is not None and d.name:
            row_of[d.name] = alias
        if not d.name:
            return
        if val is None:
            env[d.name] = Cost.num(0)
            return
        v = as_cost(val, env, caps)
        if val.kind == "path" and val.name in env:
            v = env[val.name]
        env[d.name] = v
        if d.name in bounds:
            ib = self._container_expr_bound(val, bounds, env, params)
            cur = bounds[d.name]
            if ib is None or cur is None:
                bounds[d.name] = None
            else:
                bounds[d.name] = Cost.max(cur, ib)

    def _scan_branches(self, runs, factor, bounds, env, caps,
                       row_of, const_names, params, fn) -> None:
        """`runs`：每条分支的**语句列表**（`block.children`，或 `[if 节点]`）。

        match 臂传的是 `arm_block(...).children` / `arm_single_stmts(...)`，
        `if` 传的是各 `block` 的 children 与 `[else-if 节点]`——两种都是
        「语句列表」，所以这里统一按语句列表扫，不再假设它是 block。
        """
        base_bounds = dict(bounds)
        base_env = dict(env)
        results = []
        for stmts in runs:
            b2 = dict(base_bounds)
            e2 = dict(base_env)
            self._scan_growth_stmts(stmts, factor, b2, e2, caps,
                                    row_of, const_names, params, fn)
            results.append((b2, e2))
        self._merge_branch_states(base_bounds, base_env, results, bounds, env)

    def _merge_branch_states(self, base_bounds, base_env, results, bounds, env) -> None:
        names = set(base_bounds)
        for b2, _e2 in results:
            names.update(b2)
        for nm in names:
            vals = [base_bounds.get(nm)]
            for b2, _e2 in results:
                vals.append(b2.get(nm, base_bounds.get(nm)))
            if any(v is None for v in vals):
                bounds[nm] = None
            else:
                cur = vals[0]
                for v in vals[1:]:
                    cur = Cost.max(cur, v)
                bounds[nm] = cur
        keys = set(base_env)
        for _b2, e2 in results:
            keys.update(e2)
        for key in keys:
            vals = [base_env.get(key)]
            for _b2, e2 in results:
                vals.append(e2.get(key, base_env.get(key)))
            vals = [v for v in vals if v is not None]
            if not vals:
                continue
            cur = vals[0]
            for v in vals[1:]:
                cur = Cost.max(cur, v)
            env[key] = cur

    def _for_iter_bound(self, st, bounds, env, params=None) -> Optional[Cost]:
        """增长预扫用的轮数上界：`_for_count` 的界再按**局部容器的当前上界**归一。

        **2026-11-09：不再另写一套推导**。旧版本自己走 `expr_to_bound`，与
        `_for_count` 是两条路，正是它们漂出了 `n(n)` 这类错名字；而且它对
        「局部标量别名 = 字面量」记成了 `num(0)`（`as_cost` 的旧口径）——
        轮数会被算成 **0**，容器的增长上界跟着偏小（低估）。
        `params` 只为兼容调用点保留：参数解析已经在 `_for_count` 里做完。
        """
        return self._resolve_local_cost(self._for_count(st), bounds, env)

    def _scan_expr(self, e, factor, bounds) -> None:
        if e is None:
            return
        if getattr(e, "kind", "") in ("method-call", "call"):
            short = _callee_short(e)
            recv = (e.children[0]
                    if e.children and e.children[0].kind == "path" else None)
            args = next((c for c in (e.children or []) if c.kind == "args"), None)
            if recv is not None and recv.name in bounds:
                if short in _GROWTH_METHODS:
                    # **数量参数**（2026-11-13）：`insert(pos,count,v)` 一次加
                    # `count` 个，不是 1 个。按 +1 记会让 `|xs|` 的上界偏小，
                    # 随后 `for (x : xs)` 的轮数也跟着偏小 ⇒ **第二次低估**
                    # （实测：`xs.insert(0,n,7); for(x:xs)…` 的 κ 与 n 无关）。
                    _has, _amt = self._method_count_cost(
                        short, self._recv_head(recv), args)
                    if not _has:
                        self._grow_local(bounds, recv.name, factor)
                    elif factor is None or _amt is None or bounds[recv.name] is None:
                        bounds[recv.name] = None
                    else:
                        bounds[recv.name] = Cost.add(
                            bounds[recv.name], Cost.mul(factor, _amt))
                elif short not in _SAFE_METHODS:
                    # 不认识的方法可能合并/扩容/交换 → 上界推不出（不猜）。
                    bounds[recv.name] = None
            if short in _GROWTH_BUILTINS and args is not None and args.children:
                first = args.children[0]
                while getattr(first, "kind", "") in ("ref", "paren"):
                    first = first.children[0] if first.children else None
                if getattr(first, "kind", "") == "path":
                    self._grow_local(bounds, first.name, factor)
            # **把局部容器整个传给别的函数**：可能是按引用改大小，
            # 保守判未知。只认「裸容器实参」，避免 `cnt.get(k)` 这种误伤。
            #
            # **2026-11-10（D2）**：只有**非 const 引用**形参才可能改调用者的
            # 对象；按值是一份拷贝、`const T&` 是只读借用。以前一律判未知，
            # 于是 `roundtrip` 的 `enc` 在 `decode(enc)` 之后界就被抹成 None，
            # `n(enc)` 只能原样留在 κ 里（E23）。认不出被调函数时仍按"可能改"。
            # **`x.push(y)` / `m.insert(k, v)` 的实参改不到**：增长族的实参是
            # **拷贝进去**的（C++ 的 `push_back(const T&)` / `insert` 都只读实参），
            # 它不可能被写长。以前一律按"可能改"抹成 ⊤，于是
            # `board.push_back(line)` 之后 `line` 的界就是 None —— 渲染层的
            # `n(line)` 只能原样留在 κ 里（一个**局部容器名**，target 侧引用不到，
            # `cost_of` 甚至会报 E23）。这一格是"收紧但不失真"：只读实参的长度
            # 在调用前后**逐字不变**。
            if args is not None and short not in _GROWTH_METHODS:
                for _i, a in enumerate(args.children or []):
                    while getattr(a, "kind", "") in ("ref", "paren"):
                        a = a.children[0] if a.children else None
                    if (getattr(a, "kind", "") == "path"
                            and a.name in bounds
                            and not (short in _GROWTH_BUILTINS and _i == 0)):
                        # **`filled(fill, n)` 的 fill 是拷贝源，只读**（2026-11-12 晚）：
                        # `vector<vector<T>> g(n, empty)` 的每一行都是 `empty` 的拷贝，
                        # 构造器不会改 `empty`。旧口径按"认不出的调用可能改实参"
                        # 把 `empty` 的长度界抹成 None ⇒ `L(g)`（每行长度）也就推不出。
                        if short == "filled" and _i == 0:
                            continue
                        if (getattr(e, "kind", "") != "call"
                                or self._call_may_grow_arg(short, _i)):
                            # **出参增长**（P0-1）：只有非 const 引用容器形参能写长
                            # 调用者的容器。能推出"写进去多少"就**加上**（仍是上界），
                            # 推不出才把界抹成 ⊤。`∞ + k = ∞`、`有限 + k ≥ 有限`。
                            #
                            # **必须乘 `factor`**：这句调用可能在 k 层循环里
                            # （`while (i < k) { add_all(out, xs); }`），每层轮数
                            # 就是这一层的"插入点被执行次数"。漏乘 = 低估
                            # （`|out| ≤ n(xs)` 而真实是 `k×n(xs)`）。
                            _g = (self._out_growth_at_call(short, e, _i)
                                  if getattr(e, "kind", "") == "call" else None)
                            # **证明"这次调用根本不会把它写长"时，界原样保留**
                            # （2026-11-12，局部容器尺寸）：`dfs_visit(g, v, color)`
                            # 的自递归让 `_out_growth` 成环退 ⊤，于是 `|color|`
                            # 在循环里被抹掉。但只看**增长动作**就能证明它不长：
                            # 体内只有 `color[v] = 1/2`（vector 下标赋值不改长度），
                            # 递归调用把同一个实参传给同一个形参（共归纳假设）。
                            # 这是**安全性质**的证明：任何真实的"变长"都必须在
                            # 某个有限深度上落到某个增长点上，而增长点一个都没有。
                            if (_g is None
                                    and getattr(e, "kind", "") == "call"
                                    and self._arg_never_grows(short, _i)):
                                continue
                            if _g is not None and factor is not None:
                                _g = Cost.mul(factor, _g)
                            _cur = bounds[a.name]
                            if _g is None or _cur is None:
                                bounds[a.name] = None
                            else:
                                bounds[a.name] = Cost.add(_cur, _g)
        for c in (e.children or []):
            self._scan_expr(c, factor, bounds)

    def _call_may_grow_arg(self, short: str, idx: int) -> bool:
        """`f(…, <局部容器>, …)` 能不能让**调用者**那个容器**变长**。

        长度**上界**只在"调用后可能变长"时失效；重排 / 赋值 / 变短都不动上界。

        | 被调方 | 会变长吗 |
        |---|---|
        | 内建 | 只有 push 族（`_GROWTH_BUILTINS`） |
        | `_call_inner` 特判的内建（`print`/`sort`/…） | 不会 |
        | 本模块用户函数 | 只有**非 const 引用**形参能（按值是拷贝、`const T&` 只读） |
        | 认不出（FFI / 外来） | 按"可能"处理（修前的保守口径） |
        """
        # **用户函数优先**：与 `_call_inner` 的派发顺序一致（用户写了同名函数时，
        # 跑的是用户那份，内建分支不参与）。
        fn = self.fns.get(short)
        if fn is not None:
            pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
            params = pnode.children if pnode is not None else []
            if idx >= len(params):
                return True
            p = params[idx]
            ty = p.children[0] if p.children else None
            if _is_ref_type(ty):
                return not bool((getattr(p, "value", None) or {}).get("const"))
            return False
        try:
            if stdlib.is_stdlib(short):
                return short in _GROWTH_BUILTINS
        except Exception:                                   # noqa: BLE001
            pass
        if short in _SPECIAL_BUILTINS:
            return short in _GROWTH_BUILTINS
        return True

    # ---------------------------------------------------------------- 「这次调用不会写长」
    #
    # **问题**（2026-11-12，局部容器尺寸 ①）：`std::vector<u64> color(g.size(), 0)`
    # 的界本来是 `n(g)`（`filled` 规则），但循环体里一句 `dfs_visit(g, v, color)`
    # 就把它抹成了 ⊤ —— 因为 `_out_growth` 走**出参增长**那条路，而 `dfs_visit`
    # 自递归 ⇒ 增长互相依赖成环 ⇒ 推不出 ⇒ `_scan_expr` 按"可能变长"抹界。
    #
    # 但"**它不会变长**"是个**安全性质**，可以**共归纳**地证明（不需要不动点）：
    # 任何真实的变长都必须发生在某个**有限深度**的某个**增长动作**上。所以只要
    #   ① 体内没有增长动作（`push` 族 / map 下标赋值 / 整容器赋值 / 未知调用）；
    #   ② 每个把该实参传出去的调用，其对应形参也满足本条（递归/互递归处按
    #      **假设成立**处理，即 `visiting` 命中就当 True）；
    # 那么**任何深度**都不会变长。反过来只要有一个增长点或一个不认识的外部调用，
    # 就返回 False（调用方照旧抹 ⊤）。这是"收紧但不失真"：它只可能把 ⊤ 换成
    # 原来的界，**从不**产生比原来更小的界。
    #
    # 只给 `call`（自由函数）用；方法调用没有"形参位置"这层对应关系，不猜。

    def _arg_never_grows(self, short: str, idx: int) -> bool:
        """能否证明 `short(…, <容器>, …)` **不会把第 `idx` 个实参写长**。"""
        fn = self.fns.get(short)
        if fn is None:
            return False
        return self._param_never_grows(fn, idx, set())

    def _param_never_grows(self, fn: Node, idx: int, visiting: set) -> bool:
        """`fn` 的第 `idx` 个（引用）形参在体内**从不改变长度**。"""
        key = (id(fn), idx)
        if key in visiting:
            return True                     # 共归纳假设：成环的调用本身不增长
        p = self._param_at(fn, idx)
        if p is None or not getattr(p, "name", ""):
            return False
        body = next((c for c in (fn.children or []) if c.kind == "block"), None)
        if body is None:
            return False
        ty = p.children[0] if (p.children or []) else None
        head, _args, _ref = self._type_parts(ty)
        return self._body_never_grows(
            body, p.name, head, fn, key, visiting | {key})

    def _body_never_grows(self, node, name: str, head: Optional[str],
                          fn: Node, key, visiting: set) -> bool:
        """子树里有没有可能让容器 `name` **变长**的写入；没有 → True。

        `head` 是这个容器的种类（`map`/`unordered_map` 的下标赋值 = 插入）。
        """
        if node is None:
            return True
        k = getattr(node, "kind", "")

        # 整容器赋值：`p = …` 可能换成更大的内容 —— 不猜。
        if k in ("assign", "assign-op"):
            lhs = node.children[0] if node.children else None
            if getattr(lhs, "kind", "") == "path" and getattr(lhs, "name", "") == name:
                return False
            if (getattr(lhs, "kind", "") == "index"
                    and lhs.children
                    and getattr(lhs.children[0], "kind", "") == "path"
                    and getattr(lhs.children[0], "name", "") == name
                    and head in ("map", "unordered_map")):
                return False                # map 下标赋值 = 插入
            # 其它写入（`p[i] = v` 的 vector 情形、`q[i].f = v`）不改 `p` 的长度。

        if k == "method-call":
            recv = (node.children[0]
                    if (node.children
                        and getattr(node.children[0], "kind", "") == "path")
                    else None)
            short = _callee_short(node)
            if recv is not None and getattr(recv, "name", "") == name:
                # `p.push` 族会写长；不认识的方法同样可能（resize/assign/merge…）。
                if short not in _SAFE_METHODS:
                    return False
                return True
            # `other.push(p)` / `other.merge(p)`：实参是**拷贝/只读**时改不到 `p`。
            args = next((c for c in (node.children or [])
                         if getattr(c, "kind", "") == "args"), None)
            for a in (args.children if args is not None else []) or []:
                while getattr(a, "kind", "") in ("ref", "paren"):
                    a = a.children[0] if a.children else None
                if getattr(a, "kind", "") == "path" and getattr(a, "name", "") == name:
                    # 增长族的实参是**拷贝进去**的（`push_back(const T&)` 只读），
                    # 改不到 `p`；不认识的方法一律按"可能"处理。
                    if (short not in _SAFE_METHODS
                            and short not in _GROWTH_METHODS):
                        return False

        if k == "call":
            short = _callee_short(node)
            args = next((c for c in (node.children or [])
                         if getattr(c, "kind", "") == "args"), None)
            for _i, a in enumerate((args.children if args is not None else []) or []):
                while getattr(a, "kind", "") in ("ref", "paren"):
                    a = a.children[0] if a.children else None
                if not (getattr(a, "kind", "") == "path"
                        and getattr(a, "name", "") == name):
                    continue
                callee = self.fns.get(short)
                if callee is not None:
                    if not self._call_may_grow_arg(short, _i):
                        continue            # 按值 / const 引用：改不到调用者
                    if not self._param_never_grows(callee, _i, visiting):
                        return False
                    continue
                if short in _GROWTH_BUILTINS and _i == 0:
                    return False
                try:
                    if stdlib.is_stdlib(short):
                        continue            # 内建是值语义，不原地改写
                except Exception:           # noqa: BLE001
                    pass
                if short in _SPECIAL_BUILTINS:
                    continue                # sort/print/…：不改长度
                return False                # 认不出的外部调用：按"可能"处理

        # 其余节点：继续往下（`return p;` / `for (x : p)` / 条件…都只读长度）。
        for c in (getattr(node, "children", None) or []):
            if not self._body_never_grows(c, name, head, fn, key, visiting):
                return False
        return True

    def _target_cost(self, n: Optional[Node]) -> Optional[Cost]:
        """把 target 槽位里的表达式直接翻成 `Cost`。

        **为什么不复用 `expr_to_bound`**：`expr_to_bound` 服务的是 `while` 上界，
        它必须保守（`a * b` 当不了计数器）。而 target 里 `O(a.length() * b.length())`
        就是**乘积界**，`Cost.mul` 本来就能表达——两条路径的语义不同，不能共用。
        """
        if n is None:
            return None
        n = _unwrap_obig(n)
        # **先剥括号**（2026-11-10）：`_target_cost` 原来没有 `paren` 分支，括号
        # 一路落到底部的 `expr_to_bound`——而它同样不认括号 → None。
        # 于是 `O((hi - lo) * (hi - lo))` **整条**认不出（`E-Target-BadForm`），
        # 连里面本来能认的部分也一起丢。括号不改变值，剥掉不影响语义。
        while getattr(n, "kind", "") == "paren" and n.children:
            n = n.children[0]
            n = _unwrap_obig(n)

        if n.kind == "lit-int":
            try:
                return Cost.num(int(n.value))
            except (TypeError, ValueError):
                return None

        if n.kind == "path":
            nm = n.name or ""
            if nm in self.const_values:
                return Cost.num(self.const_values[n.name])
            # **先认形参，再认 `n` 的魔法**（2026-11-09 修）。
            # 旧顺序把 `nm == "n"` 放在最前面：形参名恰好叫 `n`（又不是容器）时，
            # "n = 唯一容器的长度"这条规则先命中；没有容器形参就返回 None，
            # 于是 `_find_slot` 静默跳过、`target_cost` 返回 None，上层把它
            # 读成"spec 没写 time 的 target"——**契约凭空消失**。
            #
            # **标量形参不是尺寸量**：`lo`/`hi`/`k` 是下标或计数，`n(hi)` 是错的。
            # 这里给 `sym`（量本身），于是 `O(hi - lo)` 能直接与循环轮数比较；
            # κ 侧的标量形参也是同一个渲染（`bound_to_cost_inst` 的 `scalars`），
            # 两侧尺寸量同名，判定与显示才一致。
            if nm in self._scalar_param_names():
                return Cost("sym", nm)
            # **容器形参的裸名字就是它的尺寸量**：`O(xs)` 与 `O(xs.length())` 同义。
            # 单独列出来，是因为容器形参也可能叫 `n`（此时 `n` 只指它自己）。
            if nm in self._container_param_names():
                return Cost.size(nm)
            # **`sizes` 绑定的名字**（G1-b）：`sizes table = n + 1;` 之后，
            # target 里的 `table` 就是 `n + 1`。放在 `n` 的魔法**之前**：
            # 显式绑定比"形参里恰好一个容器"的约定更具体。
            # 以前这里没有这一支 ⇒ 绑定写了等于没写（`table` 变成一个自由的
            # 尺寸量，判定退化成 ⊤）。
            if nm in self._size_bind_table():
                return self._size_bind_cost(nm)
            # **`n` 的绑定规则（E23）**：形参里**恰好一个容器**时，`n` 就是它的长度。
            # 以前 `O(n)` 被当成一个自由符号，于是 `f(xs)` 的 target `O(n)`
            # 与常数 κ 比较时判"达成"——**契约看着成立，其实什么都没约束**。
            # 绑不上时返回 None，但**不再静默**：`_find_slot` 会登记
            # `target_errors`，`check` 出诊断、`cost` 打印错误。
            if nm == "n":
                _only = self._only_container_param()
                if _only is not None:
                    return Cost.size(_only)
                return None
            return self._size_of_path(nm)

        if n.kind in ("method-call", "call"):
            fn = (n.name or "").split(".")[-1]
            # **有理次幂**（2026-11-09）：`.tu` 里没有 `^` 这个 token（`^`
            # 不是运算符），所以表面写法用三个内建式函数名：
            #
            #     sqrt(x)        = x^(1/2)
            #     root(x, q)     = x^(1/q)          q 是正整数
            #     pow(x, p/q)    = x^(p/q)          指数必须是**字面量比**
            #
            # 只认**靶表达式内部**：它们不是运行时内建，不进 `stdlib` 表——
            # 写进普通代码会照旧报"未声明的函数"，不会多出一个半实现的语义。
            if fn in ("sqrt", "root", "pow"):
                argv = _call_args(n)
                want = 1 if fn == "sqrt" else 2
                if len(argv) != want:
                    return None          # 参数个数不对：不猜缺省值
                base = argv[0]
                base_cost = self._target_cost(base) if base is not None else None
                if base_cost is None:
                    return None
                if fn == "sqrt":
                    return Cost.sqrt(base_cost)
                e = _target_literal_fraction(argv[1])
                if e is None:
                    return None
                if fn == "root":
                    if e.denominator != 1 or e < 2:
                        return None      # root 的第二个参数必须是 ≥2 的整数
                    return Cost.pow(base_cost, Fraction(1, int(e)))
                return Cost.pow(base_cost, e)
            if fn == "k":
                for c in n.children:
                    if c.kind == "args" and c.children and c.children[0].kind == "path":
                        return Cost("sym", f"k({c.children[0].name})")
                return None
            # **`min` / `max`**（2026-11-09，G1-b 的形状量）：规格 §7.2 允许尺寸名
            # 由 `min`/`max` 组合（`sizes m = min(xs.length(), limit);`）。
            # `min` 与 κ 侧同一口径：取较松的一侧（`_min_cost`），因为这里要的是
            # **上界**；`max` 就是 `Cost.max`。参数个数不对 → None（不猜）。
            if fn in ("min", "max"):
                argv = _call_args(n)
                if len(argv) != 2:
                    return None
                la = self._target_cost(argv[0])
                lb = self._target_cost(argv[1])
                if la is None or lb is None:
                    return None
                return _min_cost(la, lb) if fn == "min" else Cost.max(la, lb)
            if fn in ("length", "size", "capacity", "len"):
                # `s.v.size()`（字段链）→ 尺寸符号 `n(s.v)`：target 与 κ 用
                # **同一个名字**才对得上（2026-11-12）。认不出就不给（不写 `n(?)`）。
                nm = size_receiver_name(n)
                return Cost.size(nm) if nm else None
            if fn == "L":
                # **元素长度上界** `L(g)`（2026-11-12）：κ 侧 `g[v].size()` 的
                # 尺寸量就是这个符号（见 bound.py 模块头）。两边同名才对得上判定。
                # 只认 `L(<纯字段链>)`；`L(f())` / 多实参 → None（不猜）。
                argv = _call_args(n)
                if len(argv) != 1:
                    return None
                nm = field_chain_name(argv[0])
                return Cost("sym", f"L({nm})") if nm else None
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
            # **`**` / `^` 幂**（2026-11-10 / 2026-11-12）：`.eln` 里 `**` 已经是
            # 幂运算符了，但 target 里以前只认 `pow(x, p/q)` —— **同一个语言，
            # 两处对 `**` 的理解不同**：`O(n ** 2)` 直接报 `E-Target-BadForm`。
            #
            # 现在**复用 `pow` 的构造器**（`Cost.pow`，不新造节点、不新造阶
            # 规则）：`n ** 2` == `pow(n, 2)`、`n ** 1/2` == `sqrt(n)`。
            # 判阶仍走 `cost_check._profile` 已有的 `pow` 一条（本文件不参与判阶）。
            #
            # **`^` 也当幂**（2026-11-12）：成本契约是数学式子，`n ^ 8` 在
            # `.tu` 里写出来就是「n 的 8 次方」；`^` 在 `.eln` **代码**里仍是
            # 按位异或（那是运行期语义，这条分支只服务 target 槽位，不碰 `.eln`）。
            # 底数允许**字段链**（`recv.n_ ^ 8`）：`recv.n_` 经底下的 `field`
            # 兜底路径翻成 `Cost.size("recv.n_")`，与 κ 侧 `n(recv.n_)`
            # **是同一个尺寸量**——这是判阶能对上的前提。
            #
            # **硬约束：指数必须是编译期常数**（字面量或常数分数）。指数是
            # 变量（`n ** k`）时阶就成了"关于 k 的表达式"，`_profile` 的
            # `(次数, log 因子数, loglog 因子数)` 没有值可填 —— **不猜**，退 ⊤
            # 并在 `_target_hint` 里说清原因（含糊的通用提示说不出这句）。
            if (n.value or "") in ("**", "^"):
                if a is None or b is None:
                    return None               # 缺操作数：语法层已报错
                _base = self._target_cost(a)
                if _base is None:
                    return None               # 底数认不出：用通用提示
                _e, _why = _target_star_star_exponent(b, n.value or "**")
                if _e is None:
                    self._target_hint = f"`{_expr_text(n)}` 的指数不收：{_why}"
                    return None
                _p = Cost.pow(_base, _e)
                if _p.is_top():
                    # 与 `Cost.pow` 同一条规则：负底数 / 未知底数判不了 ⇒ ⊤。
                    self._target_hint = (
                        f"`{_expr_text(n)}` 退 ⊤：`Cost.pow` 判不了这个幂"
                        f"（负指数不是成本上界；负底数的分数幂不是实数）。不猜。")
                    return None
                return _p
            la, lb = self._target_cost(a), self._target_cost(b)
            if la is None or lb is None:
                return None
            if n.value == "*":
                return Cost.mul(la, lb)
            if n.value == "+":
                return Cost.add(la, lb)
            if n.value == "-":
                # **`hi - lo` 是尺寸差**（2026-11-10）。旧口径 `Cost.max(la, lb)`
                # 是**把契约悄悄放宽**：target 写 `O(hi - lo)`、判却按 `O(hi)`
                # ——用户以为承诺了区间长度，其实承诺了一个更松的上界，
                # 于是真实的债务会被判成「达成」。
                #
                # 现在的口径只有两条，没有第三条：
                #   · 能证明 `lo <= hi`（spec 的 `requires`）→ 它是**尺寸量**，
                #     符号名 `hi - lo`（`Cost("sym", …)`），与 κ 侧循环轮数
                #     **同一个名字**；
                #   · 证明不了 → **不当常数、不当正数猜**，退 ⊤ 并说清原因。
                _d = size_diff_cost(la, lb, self._target_facts())
                if _d is not None:
                    return _d
                self._target_hint = (
                    f"差值 `{_expr_text(n)}` 可能是负的：只有能证明"
                    f"「{lb.render()} ≤ {la.render()}」时它才是**非负的尺寸量**。"
                    f"这里证明不了（`requires` 里没有这条顺序）⇒ **退 ⊤**，"
                    f"不当常数、也不当正数猜。要启用它，请在 spec 里写前置条件，"
                    f"例如 `requires {_expr_text(b)} <= {_expr_text(a)};`")
                return None
            if n.value == "/":
                # **除法**（2026-11-09）：`O(n / log(n))` 要能写。
                # 判阶只认"分母是 log 的幂 / 常数"两种，别的在 `_profile`
                # 里退 ⊤——**这里照结构造节点，不在这里猜**。
                #
                # **语义提醒**：`.eln` 代码里的 `/` 是整数除法，而 target
                # 成本式里的 `/` 是**量级意义上的除法**（`n / log n` 不是
                # 整数商）。两者对本语言要判的东西（阶）给出同一档结论：
                # 除以正常数不改量级，`n / log n` 在两种读法下都低于 `n`。
                return Cost.div(la, lb)
            return None

        # 其余形状退回旧路径（保守界）
        b = expr_to_bound(n)
        return (bound_to_cost_inst(b, self.const_values, self._scalar_param_names(),
                                   self._target_facts())
                if b is not None else None)

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
        prev_fn = self.cur_fn
        try:
            fn = self.fns.get(fn_name)
            if fn is None:
                return Cost.num(0)
            # **区间二分递归的分配量不猜**（2026-11-12）：alloc 维度的自调用在
            # 环上按 0 记（上一行），那等于"只算一层"。对 `merge_sort` 就是
            # `O(n)`，而真实是 `O(n log n)`（每层都要一块 `hi - lo` 的缓冲）
            # ⇒ **低估**。时间维度有可证的激活数上界 `2 × (hi - lo)`（见
            # `_halving_span`），alloc 维度还没有这套倍乘，所以这里保持 ⊤——
            # 与修前一致，不拿"只算一层"冒充上界。
            if self._halving_span(fn_name) is not None:
                return Cost.top()
            # **`cur_fn` / 函数体栈也要设**（2026-11-09）：循环轮数要靠它们查
            # 「局部别名 → 尺寸量」表（`const u64 n = xs.length();`）。修前这里
            # 只能靠 `cost_of` 顺手留下的值；单独调 `alloc_of`（很多测试都这样）
            # 时 `cur_fn` 是 None，同一条 `while (i < n)` 在两个维度上会用到
            # **不同的**别名表 —— 口径一漂，分配上界就可能偏小。
            self.cur_fn = fn
            self._fn_stack.append(fn)
            body = stmt_body(fn)
            self._frames.append(self._make_frame(fn))
            try:
                # **A-full #2**：按值形参入口的拷贝也要分配（vector +1 / map +n）。
                # 分配与时间**各算各的**，但判据必须同一份（`_param_copy_cost`）。
                _t, _a = self._param_copy_cost(fn)
                _res = Cost.add(_a, self._alloc_stmts(body.children if body else []))
                # **局部容器的尺寸量在 alloc 维也要代入**（2026-11-13）。
                #
                # 为什么必须代入：`target` 里写不出局部容器的名字，所以只要 alloc
                # 的 κ 里留着 `n(局部)`，这条契约就永远判不了（E23 / H3 的假债务）。
                # 时间维度早就在 `_local_container_bounds` 里代掉了（`cost_of`），
                # alloc 维度以前没接——`11_text.main` 的
                # `109 + n(lines)×(n(text)×2 + 2 + n(fields)×…)` 正是这一格。
                #
                # 代入用的是**同一份** `_local_container_bounds`（不另造界），
                # 方向与时间维一致：`n(局部) → 它的上界`，只会变大 ⇒ 仍是上界。
                # 三条保护：
                #   · 表为空 → 一个字不动（既有 111 个函数的 alloc 逐字节不变）；
                #   · 某个名字推不出（None）→ 整项不改（不猜、不按 1 算）；
                #   · 换完带 ⊤ → 不改（`Cost.mul` 不吸收 ⊤，留着会变成
                #     "看着像常数"的假上界，`compare` 会误判达成）。
                _lb, _ln = self._local_container_bounds(fn)
                if _lb and not _res.is_top():
                    _r2 = self._resolve_local_cost(_res, _lb, {})
                    if _r2 is not None and not _has_top(_r2):
                        _res = _r2
                return _res
            finally:
                self._frames.pop()
                self._fn_stack.pop()
        finally:
            self.cur_fn = prev_fn
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
                self.const_names, self.cur_fn,
                resolve=lambda nm: self._name_count(nm, self.cur_fn),
                size_alias_skip=self._revoked_size_aliases(self.cur_fn))
            if b is None:
                return Cost.top()       # 推不出轮数：分配上界未知（不撒谎）
            # 与时间维度**同一份**展开（`const` 别名按结构走，不是常数）
            bound = self._bound_to_cost_scoped(b)
            if bound is None or bound.is_top():
                return Cost.top()
            return Cost.mul(bound, inner)
        if k == "for":
            # `for (auto x : xs) { vector<u64> t = [1,2]; … }` 每轮分配一次
            # → 上界 = 轮数 × 每轮。
            body = stmt_body(s)
            inner = self._alloc_stmts(body.children if body else [])
            # **按值迭代的元素拷贝也分配**（2026-11-10 夜）：元素是 owning
            # 容器时，每轮真拷一份 → 每轮 1 次分配；`auto&` / `const auto&`
            # 不拷贝 → 不记。数组/结构体元素拷贝不分配（`_copy_cost` 给 0）。
            _it = next((c for c in (s.children or [])
                        if getattr(c, "kind", "") != "block"), None)
            _ety = self._iter_elem_type(_it)
            _elem_a = Cost.num(0)
            if getattr(s, "ref", None) is None and _ety is not None:
                _et, _elem_a = self._copy_cost(_ety, None)
            if (inner.is_const() and inner.value == 0
                    and _elem_a.is_const() and _elem_a.value == 0):
                return Cost.num(0)
            # **轮数与时间成本用同一份推导**（`_for_count`）：容器 / 键视图 /
            # 区间（含**字面量**与**局部别名**上界）三种写法一起覆盖。
            # 修前这里区间分支自己调 `_for_count`，其余分支直接 `Cost.size(名字)`，
            # 两维在两套推导之间漂过（见 `_for_count` 的说明）；推不出 → ⊤。
            cnt = self._for_count(s)
            if cnt is None:
                return Cost.top()
            return Cost.mul(cnt, Cost.add(inner, _elem_a))
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
            worst = self._alloc_stmts(body.children if body else [])
            # **else / else-if 也是分支**：`stmt_else` 只认 `kind == "else"`，
            # 而这个 parser 把 `else` 直接挂成 `block`、`else if` 挂成 `if`
            # （`parser.parse_if`），于是 else 侧的分配原先一律记 0。
            # 取 max —— 一次执行只走一条路，与时间侧的 `Cost.max` 同口径。
            for c in (s.children or [])[1:]:
                if c is body:
                    continue
                if c.kind == "block":
                    worst = Cost.max(worst, self._alloc_stmts(c.children))
                elif c.kind == "if":
                    worst = Cost.max(worst, self._alloc_stmt(c))
            return worst
        if k == "block":
            return self._alloc_stmts(s.children)
        if k == "parallel":
            # **B-24：`allocation` 同样取 Σ**。
            #
            # 以前这里没有分支，`parallel { … }` 落到函数末尾的 `return Cost.num(0)`
            # ——**并行块里的分配一律记 0**。于是
            # `parallel { { v.push(x); } }` 对 `allocation: { max: 0 }` 会判"达成"，
            # 而真实分配是 1：这是既有的**低估**（不是引入 `par` 造成的，但同一类错误，
            # 顺手一起修掉）。
            #
            # 分配维度**不保留 `par` 结构**：它的判据是数值（`compare_exact`），
            # 不需要"瓶颈任务"；而且那里若拿 max 口径去判 Σ，就会**低估**。
            body = stmt_body(s)
            total = Cost.num(0)
            for task in (body.children if body is not None else []):
                total = Cost.add(
                    total,
                    self._alloc_stmts(task.children)
                    if getattr(task, "kind", "") == "block" else self._alloc_stmt(task))
            return total
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
            total = self._alloc_expr(rhs, hint=(s.name or ""))
            # **A-full #11**：声明初始化的**拷贝**也要分配（右值是 prvalue 时 0）。
            if (getattr(s, "value", None) or {}).get("has_init", len(s.children) > 1):
                total = Cost.add(total, self._copy_alloc_of_decl(s, rhs))
            return total
        if k == "assign":
            # **`m[k] = v` 的分配必须与 `m.insert(k, v)` 逐项同源**（D1）：
            # 插入一个节点/槽位算 1 次，key/value 的分配与值拷贝都按同一条
            # `_store_copy_value` 判。以前这里只加 rhs 的分配 ⇒ `m[k] = v`
            # 的 alloc 是 0，而 `m.insert` 是 n（审计 P4a）。
            _aw = self._assoc_write_target(s.children[0] if s.children else None)
            if _aw is not None:
                recv, key, _head = _aw
                val = s.children[1] if len(s.children) > 1 else None
                total = Cost.num(1)
                total = Cost.add(total, self._alloc_expr(recv))
                total = Cost.add(
                    total, Cost.add(self._alloc_expr(key),
                                    self._alloc_expr(val)))
                _st, _sa = self._store_copy_value(
                    self._type_of_expr(recv), val)
                return Cost.add(total, _sa)
            # **A-full #11**：`a = b` 的容器拷贝要分配（上界记 1 / n）。
            total = Cost.num(0)
            if len(s.children) > 1:
                total = Cost.add(total, self._alloc_expr(s.children[1]))
            return Cost.add(total, self._copy_alloc_of_assign(s))
        if k == "assign-op":
            # **`m[k] += v` 的分配**：读 `m[k]` 在缺键时插入 `V{}`（至多 1 次：
            # 写回 `put` 命中的是刚插入/已有的键，不再分配）。修前这里落到
            # 末尾的 `Cost.num(0)` ⇒ 分配上界 0（低估）。
            total = Cost.num(0)
            if len(s.children) > 1:
                total = Cost.add(total, self._alloc_expr(s.children[1]))
            _aw = self._assoc_write_target(s.children[0] if s.children else None)
            if _aw is not None:
                recv, key, _head = _aw
                total = Cost.add(total, self._alloc_expr(recv))
                total = Cost.add(total, self._alloc_expr(key))
                total = Cost.add(total, Cost.num(1))
            return total
        if k == "return":
            total = self._alloc_expr(s.children[0]) if s.children else Cost.num(0)
            # **A-full #11**：`return` 借用值 → 拷贝要分配；移动 → 0（最容易漏的一条）。
            return Cost.add(total, self._copy_alloc_of_return(s))
        if k == "expr-stmt":
            return self._alloc_expr(s.children[0]) if s.children else Cost.num(0)
        return Cost.num(0)

    # ---------- 拷贝的 allocation 侧（与 time 侧同一份判定）
    #
    # `compare_exact` 是**数值**比较：漏一处就是假达成。所以 decl / assign / return
    # 三个拷贝点的 time 与 alloc 必须成对加，而且共用 `_copy_cost` 的类别判定。

    def _copy_alloc_of_decl(self, s, rhs) -> "Cost":
        if rhs is None or self._value_class(rhs) == "prvalue":
            return Cost.num(0)
        ty = s.children[0] if s.children else None
        _t, a = self._copy_cost(ty, self._size_symbol_of(
            rhs, self.row_of.get(s.name, "") or (s.name or "")))
        return a

    def _copy_alloc_of_assign(self, s) -> "Cost":
        if len(s.children) < 2:
            return Cost.num(0)
        lhs, rhs = s.children[0], s.children[1]
        if self._value_class(rhs) == "prvalue":
            return Cost.num(0)
        ty = self._type_of_expr(lhs)
        _t, a = self._copy_cost(ty, self._size_symbol_of(
            rhs, getattr(lhs, "name", "") or ""))
        return a

    def _copy_alloc_of_return(self, s) -> "Cost":
        e = s.children[0] if s.children else None
        ret = self._cur_frame()["ret"]
        if e is None or ret is None or _is_ref_type(ret):
            return Cost.num(0)
        vc = self._value_class(e)
        if vc in ("prvalue", "err", "xvalue"):
            return Cost.num(0)            # 省略 / 失败载荷 / **移动**（0 分配）
        _t, a = self._copy_cost(ret, self._size_symbol_of(e))
        return a

    def _alloc_expr(self, e, hint: str = "") -> "Cost":
        if e is None:
            return Cost.num(0)
        k = getattr(e, "kind", "")
        if k == "call":
            short = (e.name or "").split(".")[-1]
            # **用户函数优先**（与 `_call_inner` / 解释器 `_call` 同一规则）：
            # 用户定义了同名函数时，分配也按**用户实现**记，不能套同名内建的表。
            _shadowed = "." not in (e.name or "") and short in self.fns
            if stdlib.is_stdlib(short) and not _shadowed:
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
            # **调用点的分配也要实例化**（2026-11-12 晚）：`alloc_of(short)` 用的是
            # **被调方的形参名**（`n(g)` / `L(g)`）。以前直接把它返回，调用方 κ 里
            # 就留着别人的名字；名字碰巧同名时（`12_graph.main` 的 `g`）还会把
            # 被调方形参的尺寸当成调用方局部量的尺寸。分配与时间维度一样，是
            # **调用方**的承诺 ⇒ 必须在调用点换掉。换不干净 → ⊤（不猜）。
            _a = self.alloc_of(short)
            _fn = self.fns.get(short)
            if _fn is not None:
                _inst = self._instantiate_callee_cost(_a, _fn, e)
                return Cost.top() if _inst is None else _inst
            return _a
        if k == "method-call":
            # **容器增长 = 分配**（2026-11-08）。
            #
            # 以前 `v.push(x)` 落进"把子节点分配相加"，子节点是字面量 → 0，
            # 于是 `vector<u64> v; while (i < n) { v.push(i); }` 的
            # `allocation: { max: 0 }` 会判**达成**——契约形同虚设。
            #
            # 上界取 **每次 push/insert 各 1 次**：
            #   - `vector` 的真实分配是翻倍增长（≈ log₂n 次），1 次/调用 ≥ 它；
            #   - `map`/`set` 的 `insert` 每插一个节点就分配一次，1 次/调用 = 精确。
            # **宁可多算**：这里是上界，不是估计值。
            _m = (e.name or "").split(".")[-1]
            if _m in ("keys", "values", "items", "entries"):
                # **建一个 n 元素的新列表 = 1 次分配**（2026-11-11）：
                # 以前落到"子节点分配相加"，而接收者是 path ⇒ 0。`m.keys()` 的
                # 分配上界因此是 0——那是**低估**（真实会分配一个新 list）。
                total = Cost.num(1)
                for c in getattr(e, "children", []) or []:
                    total = Cost.add(total, self._alloc_expr(c))
                return total
            if _m in ("push", "insert", "push_back", "append", "enqueue"):
                total = Cost.num(1)
                for c in getattr(e, "children", []) or []:
                    total = Cost.add(total, self._alloc_expr(c))
                # **A-full #12**：入容器的**元素拷贝**的分配（元素 owning 时）
                _st, _sa = self._store_copy_cost(e)
                return Cost.add(total, _sa)
            # **其它方法调用不能直接返回 0**：子节点里可能藏着真分配
            # （`f(...)`、`.lines()` 之类）。落到下面的"子节点相加"。
            if not getattr(e, "children", None):
                return Cost.num(0)
        if k in ("lit-int", "lit-str", "path", "lit-bool"):
            return Cost.num(0)
        if k == "index":
            # **`m[k]` 读缺键会插入 `V{}`**（`interp._map_index_read`，C++ 的
            # `operator[]` 语义）⇒ 至多 1 次节点分配。序列的 `xs[i]` 仍 0。
            # 修前这里落到"子节点相加"（下标是字面量 → 0）：是**低估**。
            _recv = e.children[0] if getattr(e, "children", None) else None
            if self._recv_head(_recv) in ("map", "unordered_map"):
                return Cost.num(1)
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
        _notes0 = len(self._size_unknown)
        # **④ 的那一帧**（2026-11-12）：树递归 + 每层分叉数可推时，成员界
        # `n(recv.n_)` 走**逐字段**证明（`_tree_field_ok`）而不是保守的
        # "对象动过 ⇒ 所有成员都不许当界"。递归方法必然把 `recv` 传给自调用、
        # 也常写别的字段，不换这一帧的话整个循环轮数推不出 ⇒ 函数体 ⊤。
        # 只在这个函数自己的帧里生效，其它函数/其它路径一个字都不变。
        _tf_prev = (self._tree_frame, self._tree_fn)
        if self._tree_activation_bound(fn_name) is not None:
            self._tree_frame, self._tree_fn = fn_name, fn
        try:
            bounds, local_names = self._local_container_bounds(fn)
            cost = self._body(fn)
        finally:
            self._tree_frame, self._tree_fn = _tf_prev
        if local_names:
            # **只替换真正出现在 κ 里的名字**（2026-11-12）：`substitute_sizes`
            # 会**重建**整棵成本树，`origins` 的 `id()` 随之失效，`--explain`
            # 的「元凶行号」会退化成"这个构造没登记到行号"。表里全是没出现过的
            # 名字时（例如 `out` 只被 `return`、κ 里没有 `n(out)`），调用它是
            # 纯粹的副作用——花一次重建换零收益。
            _vars = set(cost.variables())
            table = {nm: b for nm, b in bounds.items()
                     if b is not None and nm in _vars}
            if table:
                cost = cost.substitute_sizes(table)
            left = [nm for nm in sorted(local_names)
                    if nm in set(cost.variables())]
            if left:
                raise CostError(
                    "局部容器 " + "、".join(f"'{x}'" for x in left)
                    + " 的尺寸无法在 target 中引用"
                      "（请把容器提为形参，或用一维展平）")
        # **嵌套局部容器的 `n(名字)` 也是局部尺寸量**（2026-11-12 晚）：
        # `vector<vector<T>> g` 的析构项是 `1 + n(g)`（`drop_cost_of`），而
        # `g` 若既不是循环半径、也没有被返回，就**不在** `_local_container_bounds`
        # 的 driver 集合里 ⇒ `n(g)` 留在 κ 里。求值时 `size` 查不到会按 **1** 算
        # （不是 inf）——那是**低估**。这里用同一份长度上界把它换掉；推不出就 ⊤。
        # 只处理**嵌套**局部（语料里只有 `12_graph`），一维局部走原有的 driver 口径，
        # 不动既有形状（`test_heap` / `test_hof` 钉着的那些）。
        _eb_n, _nested_n = self._elem_bounds_full(fn)
        if _nested_n:
            _vars_n = set(cost.variables())
            _need_n = sorted(nm for nm in _nested_n if nm in _vars_n)
            if _need_n:
                _fb_n = self._container_bounds_full(fn)[0]
                _tab_n: Dict[str, Cost] = {}
                _bad_n: List[str] = []
                for nm in _need_n:
                    b = _fb_n.get(nm)
                    r = None if b is None else self._resolve_local_cost(b, _fb_n, {})
                    if r is None:
                        _bad_n.append(nm)
                    else:
                        _tab_n[nm] = r
                if _bad_n:
                    cost = Cost.top()
                    self._size_unknown.append(
                        "嵌套局部容器 "
                        + "、".join(f"'{x}'" for x in _bad_n)
                        + " 的长度上界推不出 ⇒ κ 退 ⊤")
                elif _tab_n:
                    cost = cost.substitute_sizes(_tab_n)
        # **`n(?)` 不许作为尺寸量参与判定**（D4 的兜底）：它是"我不知道"的
        # 标记，`compare()` 却会把它当成一个普通尺寸量去配对（错过 E23、
        # 或按"同规模假设"给出一个错误的比值）。构造点已经改成"推导或 ⊤"，
        # 这里再兜一层：真漏出来就退 ⊤，并把原因贴给这个函数。
        _notes = list(dict.fromkeys(self._size_unknown[_notes0:]))
        if "?" in set(cost.variables()):
            cost = Cost.top()
            _notes.append("κ 里有 `n(?)`（未知尺寸）：这一项在构造时就没推出来")
        # **⊤ 藏在子表达式里也要显式化**（D2 的兜底）：`Cost.mul(⊤, 84)` 的
        # `kind` 是 `mul`，`κ_impl.is_top()` 为 False —— `compare()` 于是会
        # 走到"κ 不含尺寸量 ⇒ 常数"那一支，`evaluate` 给出 inf，**判成 ok**。
        # κ 里只要有一处推不出，整条式子就是 ⊤（不是"84 个未知"）。
        if not cost.is_top() and _has_top(cost):
            cost = Cost.top()
            _notes.append("κ 的子表达式里有 ⊤（某处的尺寸/成本推不出）")
        # **内联泄漏的出口断言**（2026-11-13）：κ 的尺寸量里**不许有本帧解释
        # 不了的名字**。正常来源只有一个——内联时被调方的形参 / 局部没被实参
        # 代换掉（`13_numeric.stddev_milli` 的 `x`）。真正的修在**构造点**
        # （`_subst_callee_scalars` / `_resolve_callee_scalar_locals`）：那里换
        # 不掉就退 ⊤；这里只是**回归网**。
        #
        # **为什么这里只记一笔、不把 κ 退成 ⊤**（这是本函数这一层唯一的选择）：
        #
        #   · 有几处「外来名字」是**契约层面的写法**，动词是 spec 自己的：
        #     `repeats log2(k)` 的 `k`（`test_bound` 钉的就是它必须渲染成
        #     `log2(k)`）、FFI 注册成本 `n`（`test_ffi`）、资源读长度 `n(ret)`
        #     （`test_stream`）。在出口一律 ⊤ 会把它们全判成"推不出"——
        #     那是**把信息扔了**，不是变安全；
        #   · `evaluate` 对查不到的名字已经给 **inf**（保守、不破界），
        #     `compare` 给 unknown（不给假达成）。也就是说这层**没有安全性缺口**，
        #     只有可读性缺口——用 ⊤ 换可读性，代价是 `demo/corpus` 的
        #     「115/115 可算」直接掉到 112/115（`test_kappa_tree_depth` [6] 钉着）。
        #
        # 所以出口**只登记、不改 κ**：`self.kappa_leaks[函数] = [名字…]`。
        # 它不是"打一行日志然后没人看"——`tests/cases/test_kappa_no_free_symbol.py`
        # 拿它做**语料级断言**（115 个函数逐条查，出现一条新的就红），
        # 比终端输出强：CI 每次都会读。
        _alien = self._foreign_symbols(cost, fn)
        if _alien:
            self.kappa_leaks[fn_name] = _alien
        if cost.is_top() and _notes and fn_name not in self.top_reasons:
            # **最多列 3 条**：一个调用点一条，几十个实参时整行会没法读。
            _shown = _notes[:3]
            if len(_notes) > 3:
                _shown.append(f"……另有 {len(_notes) - 3} 处同类推不出")
            self.top_reasons[fn_name] = "；".join(_shown)
        return cost

    def _body(self, fn: Node) -> Cost:
        # **把函数名压栈**：`_call` 靠 `self.frames` 认出「递归调用点」。
        # 修前只有 `self.inlining`（分支③ 才压），有 target 的函数体从不压栈，
        # 于是递归那一跳被当成普通调用按 target 记**一层**——深度整个丢了。
        self.frames.append(fn.name or "")
        # **A-full**：函数体遍历期间压一帧，给值类别 / 声明类型 / 元素类型提供依据。
        self._frames.append(self._make_frame(fn))
        # **函数体栈**（2026-11-09）：内联被调函数时 `cur_fn` 仍指向调用者，
        # 而 `for` 轮数的局部别名表必须按**正被遍历的函数**查 —— 同名局部在
        # 两个函数里可能指向不同容器，拿错表会把轮数记小（低估）。
        self._fn_stack.append(fn)
        try:
            blocks = [c for c in fn.children if c.kind == "block"]
            if not blocks:
                return Cost.num(0)
            env: Dict[str, Cost] = {}
            # **A-full #1**：按值形参在**入口**记一次拷贝（设计 §8.2）。
            # 尺寸用形参名；调用点由 `_param_size_table` 实例化成实参尺寸。
            entry, _entry_alloc = self._param_copy_cost(fn)
            return Cost.add(entry, self._block(blocks[0], env))
        finally:
            self._fn_stack.pop()
            self._frames.pop()
            self.frames.pop()

    # ---------- 块 / 语句
    def _block(self, blk: Node, env: Dict[str, Cost]) -> Cost:
        total = Cost.num(0)
        for st in blk.children:
            total = Cost.add(total, self._stmt(st, env))
        return total

    def _stmt(self, st: Node, env: Dict[str, Cost]):
        if getattr(st, "kind", None) == "assign-op":
            lhs = st.children[0] if st.children else None
            rhs = st.children[1] if len(st.children) > 1 else None
            total = Cost.add(Cost.num(OP_COST),
                             self._expr(rhs, env) if rhs is not None else Cost.num(0))
            # **`m[k] += v` / `m[k]++` 是「读 + 写」两半，不是只有 rhs**。
            # 2026-11-11：`interp._map_index_read` 把 `m[k]` 的读对齐了 C++
            # （缺键插入 `V{}`），`assign-op` 的 index 左值也改走 map 写路径。
            # 于是这条语句的真实成本 = 读（一次 AVL 查找，可能 +1 次节点分配）
            # + 写（一次 AVL 插入）。修前只记 `1 + κ(rhs)`：**低估**。
            _aw = self._assoc_write_target(lhs)
            if _aw is not None:
                recv, key, head = _aw
                total = Cost.add(total, self._expr(lhs, env))       # 读
                total = Cost.add(
                    total,
                    self._assoc_index_write_cost(recv, key, None, head, env,
                                                 with_stmt=False))  # 写
            return total
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
        #
        # **只声明不初始化**（`array<u64, N> dp;`）没有初值子节点：此时
        # `children[-1]` 就是类型节点本身，当表达式求值只会得到 0 ——
        # 但定长数组的默认构造要**写满 N 个零**，那是 N 次操作。
        # 少数会低估（κ 必须是上界），所以这里按容量补计（2026-11-08）。
        _has_init = (st.value or {}).get("has_init", len(st.children) > 1)
        if not _has_init:
            _cap0 = type_capacity_inst(_tnode, self.const_values)
            _ic = Cost.num(OP_COST) if _cap0 is None else Cost.mul(
                Cost.num(_cap0), Cost.num(OP_COST))
            env[st.name] = Cost.num(0)
            return Cost.add(Cost.add(Cost.num(OP_COST), _ic),
                            drop_cost_of(_tnode, st.name or ""))
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
        # **A-full #3**：声明初始化的**拷贝**。右值是 prvalue（`{…}` / `T::make()` /
        # 返回新容器的调用）→ C++17 强制省略，记 0；否则按类型递归记（§5.1）。
        # **隐式移动不在这里**：`T a = b;` 里 `b` 是左值，C++ 也只能拷贝。
        copy = Cost.num(0)
        if val_node is not None and self._value_class(val_node) != "prvalue":
            # 尺寸符号：行别名（`const vector<u64> row = m.at(i)`）按**外层**容器
            # 记（设计 §5.2 的既有约定）；否则用右值自己的名字。
            copy, _ca = self._copy_cost(
                _tnode,
                self._size_symbol_of(val_node,
                                     self.row_of.get(st.name, "") or (st.name or "")))
        return Cost.add(
            Cost.add(Cost.add(Cost.add(Cost.num(OP_COST), init_cost), extra), drop),
            copy)
    def _k_stmt_assign(self, st, env):
        # 被重新赋值 → 不再等于那一行，撤销别名（宁可不认，不可算错）
        lhs = st.children[0] if st.children else None
        if lhs is not None and lhs.kind == "path":
            self.row_of.pop(lhs.name, None)
        # **`m[k] = v` 与 `m.insert(k, v)` 同一语义 ⇒ 同一个 κ**（D1 守卫）。
        # 走 `_assoc_index_write_cost`：它与 method-call 的 insert 分支**共用**
        # 同一个公式（基数 `max(3, 2+κ(k)+κ(v))` + 尺寸项 + 接收者 + 值拷贝），
        # 所以两边 `render()` 逐字节相同，不是靠事后把常数对齐。
        _aw = self._assoc_write_target(lhs)
        if _aw is not None:
            recv, key, head = _aw
            val = st.children[1] if len(st.children) > 1 else None
            return self._assoc_index_write_cost(recv, key, val, head, env)
        # **右侧的成本必须记上**（E17）：`t = inner(xs);` 的调用成本原先整个丢了
        rhs = st.children[1] if len(st.children) > 1 else None
        rhs_cost = self._expr(rhs, env) if rhs is not None else Cost.num(0)
        # **甲.3**：左值也要算——`dp[(i+1)*width + (j+1)] = ...` 里
        # 下标表达式是实打实的求值，原先**完全没计入**（`_expr(lhs)` 从没被调用）。
        # 实测 `lcs` 因此每轮少算约 6 步，n=16 累计少 1509 步。
        lhs_cost = self._expr(lhs, env) if lhs is not None else Cost.num(0)
        # **A-full #4**：拷贝赋值的**拷贝**。`a = b` 里 `b` 是左值 → C++ 走拷贝
        # 赋值（不是移动）；prvalue → 0。左值类型要解析（path / index / field）。
        copy = Cost.num(0)
        if rhs is not None and self._value_class(rhs) != "prvalue":
            copy, _ca = self._copy_cost(
                self._type_of_expr(lhs),
                self._size_symbol_of(rhs, getattr(lhs, "name", "") or ""))
        return Cost.add(Cost.add(Cost.add(Cost.num(OP_COST), lhs_cost), rhs_cost), copy)

    def _assoc_index_write_cost(self, recv, key, val, head, env,
                                with_stmt: bool = True) -> Cost:
        """`m[k] = v` 的成本式——**与 `m.insert(k, v)` 逐项同源**（D1）。

        与 `_k_expr_method_call` 的 insert 分支顺序**完全一致**：
          ① `max(3, 2 + κ(k) + κ(v))`（调用基数；key/value 的求值）
          ② 尺寸项（`map` → `log2(n(m))`；`unordered_map` → `n(m)`）
          ③ 接收者求值
          ④ 值拷贝（`_store_copy_value`，与 insert 同一个函数）
        再补语句本身的 1（`with_stmt=False` 给 `m[k] += v` 的**写回**半段用，
        那半段与读半段共处一条语句，语句的 1 只算一次）。
        **外加**：`_alloc_stmt` 里对同样的目标记 1 次分配，与 method-call
        insert 的 `_alloc_expr` 一致（含 key/value/拷贝的分配）。
        """
        _v = self._expr(val, env) if val is not None else Cost.num(0)
        _a = Cost.add(self._expr(key, env), _v)
        total = Cost.max(Cost.num(OP_COST * 3),
                         Cost.add(Cost.num(OP_COST * 2), _a))
        _sz = self._size_term_of_recv(recv, head)
        if _sz is not None:
            total = Cost.add(total, _sz)
        # **容器键的哈希长度项**：与 `m.insert(k,v)` 同源（D1 等价性守卫）。
        _kc = self._hash_len_term(key, recv, head)
        if _kc is not None:
            total = Cost.add(total, _kc)
        total = Cost.add(total, self._expr(recv, env))
        _st, _sa = self._store_copy_value(self._type_of_expr(recv), val)
        total = Cost.add(total, _st)
        return Cost.add(Cost.num(OP_COST), total) if with_stmt else total
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
        # **A-full #5**：返回处的 省略 / 移动 / 拷贝（设计 §3.2 / §6）。
        #
        #   prvalue（`S{…}` / `T::make()` / 调用返回新对象）→ 强制省略，0
        #   `Err(...)` → 失败载荷，不是返回值本体，0
        #   非 const 局部 / 按值形参 → **隐式移动**：容器 0、array/struct 逐成员
        #   借用值（const 局部 / `const&` 形参 / 元素 / 字段）→ **拷贝**（§5.1）
        #
        # 这一条**不是"能省的优化"**：C++11 起 return 语句先按右值做重载决议，
        # 后端关不掉（【实测】g++ 16.2 `V f(V p){return p;}` 走 move ctor）。
        e = st.children[0] if st.children else None
        ret = self._cur_frame()["ret"]
        if e is None or ret is None or _is_ref_type(ret):
            return c                          # `-> T&`：返回的是借用，0
        vc = self._value_class(e)
        if vc in ("prvalue", "err"):
            return c
        if vc == "xvalue":
            return Cost.add(c, self._move_cost(ret))
        ct, _ca = self._copy_cost(ret, self._size_symbol_of(e))
        return Cost.add(c, ct)
    def _k_stmt_if(self, st, env):
        cond = st.children[0] if st.children else None
        # **`else if` 不是 `block`，是 `if` 的第三个子节点**（`parser.parse_if`：
        # `else if` 直接 `node.add(self.parse_if())`，没有 `else` 包装节点）。
        # 以前这里只收 `kind == "block"`，于是**整条 `else if` 链从来没进过 κ**：
        # 它自己的 `_stmt`(+1)、条件、以及它下面所有分支的代价全部为 0。
        # 这是 `_diff/bracket_match.match_brackets` 破界（κ 576 < ops 609）的根因：
        # 实测每轮真正执行的是 `if (开括号) … **else if (闭括号) …**`，
        # κ 每轮只记了「开括号那个 if」。
        # 修法：`block` 走 `_block`，`if` 走 `_stmt`（它自己会 +1 并递归），
        # 两支都进 `Cost.max` —— 上界只增不减。
        branches = [c for c in st.children[1:]
                    if c.kind == "block" or c.kind == "if"]
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
            # `block`（then / 普通 else）与 `if`（else-if 链）都按语句定价：
            # else-if 必须走 `_stmt`，否则它自己的 +1 与条件会被吞掉。
            costs.append(self._block(b, e2) if b.kind == "block"
                         else self._stmt(b, e2))
            envs.append(e2)
        worst = costs[0]
        for x in costs[1:]:
            worst = Cost.max(worst, x)
        self._merge_env_max(env, before, envs)
        return Cost.add(cc, worst)

    def _merge_env_max(self, env, before, envs):
        """分支求值后的**绑定表合并**：被写过的键取 `Cost.max`，其余原样。

        `if` 语句与三目 `?:` 共用这一份：两者都是"几条路里**只走一条**"，
        所以环境里的界只能取 max（取最后一条是错的——那条路可能没执行，
        κ 会跟着变小 ⇒ 破界）。没被写过的键**不合并**，免得制造
        「两条路都不存在」的假状态。

        判"写过"用**渲染串**比（`Cost` 没有稳定的一等相等）：多认一点不算错
        （取 max 只会变大），漏认就会变小，所以这里宁可多认。
        """
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


    def _k_stmt_parallel(self, st, env):
        # **B-24**：κ = Σ_i κ_i（每个任务是 `parallel { {…} {…} }` 里的一个块），
        # 判阶 MUST 等价于 `max_i 阶(κ_i)`。
        #
        # **2026-11-09 起两条口径都直接可读**：以前这里返回 `Cost.add` 的连加，
        # 任务结构被压平——判阶只能靠"Σ 的阶 = max 的阶"这个**恒等式间接**成立。
        # 后果是：拿不到"最慢那个任务"的表达式（`cost` 只能打一串和），
        # 也没法给大小核算墙钟（Σ 里已经分不出谁是谁）。
        #
        # 现在返回 `Cost.par(tasks)`：
        #   value(par) = Σ   ← 与 B-24 的 κ 一字不差（数值口径、分配口径不变）
        #   order(par) = max ← 判阶口径（`cost_check._profile`）
        # **核数 / 核权重 MUST NOT 进 κ**：它们只在 `par.wall_estimate`（报告层）。
        body = stmt_body(st)
        if body is None:
            return Cost.num(0)
        # 求和顺序、env 共享方式都与改前**逐字相同**：任务按源码顺序定价，
        # 且共用同一个 env（顺序语义下前一个任务建立的界对后一个可见）。
        tasks = [self._block(t, env) if t.kind == "block" else self._stmt(t, env)
                 for t in body.children]
        return Cost.par(tasks)
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
        c = self._while_inner(st, env)
        # 索引追逐的上界**依赖「执行会终止」**（见模块头那段）。只把它记进
        # `--explain` 的注记：侧表，不参与任何算术 ⇒ 开关前后 κ 逐字节相同。
        note = self._chase_notes.pop(id(st), "")
        return self._mark(c, st, "loop", note)

    def _call(self, name: Optional[str], node: Node, env: Dict[str, Cost]) -> Cost:
        c = self._call_inner(name, node, env)
        return self._mark(c, node, "call", (name or "").split(".")[-1])

    def _dispatch(self, node: Node, env: Dict[str, Cost]) -> Cost:
        c = self._dispatch_inner(node, env)
        return self._mark(c, node, "dispatch", node.name or "")

    def _chase_loop_bound(self, st: Node, cond: Optional[Node],
                          body: Optional[Node]):
        """**索引追逐循环**：`while (C[x] != x) { x = C[x]; }` 的圈数 = `n(C)`。

        返回 `(Bound, why)`；形状不符 → `(None, why)`（调用方走回旧提取器）。

        ## 为什么 `n(C)` 是上界（在仓库口径下，不是新假设）

        `文档/语法清单.md` 的「`κ` 的口径：**对终止执行的**上界」写明：`κ` 界的是
        **会终止的那些执行**；`bound.py` 也在同一份文档里用「良定义执行下圈数
        ≤ n(C)——`i >= n(C)` 的那一次读就越界」。这里用的正是同一条：

          · 每轮都读 `C[x]` ⇒ 良定义执行里 `x ∈ [0, n(C))`；
          · 若某个下标被走过两次，轨迹进入**周期**（`C` 在体里不变），永不退出；
          · 与「会终止」矛盾 ⇒ 走过的下标互不相同 ⇒ 圈数 ≤ n(C)。

        ## 只认这几种形状（缺一条即 ⊤）

        | 判据 | 内容 |
        |---|---|
        | ① 条件 | `C[x] != x` / `x != C[x]` / `C[x] != <循环不变量>`，`C` 是**纯字段链** |
        | ② 体 | 对 `x` 的写**恰好一处**、是体的直接子语句、形如 `x = C[x]`（一步） |
        | ③ 容器 | 本帧可见（形参 / 局部）的**可下标序列**（vector / deque / array / string） |
        | ④ 体里不改 `C` | 不 push / erase / clear / 整赋值；`C` 不被传出（尺寸不能变） |
        | ⑤ 控制流 | 没有 `break` / `continue` / `return` / 嵌套 `if` / 循环 |

        另收**路径压缩**的一步形状（`find_root` 的第二个循环）：

            u64 next = C[x];   C[x] = V;   x = next;

        三个直接子语句、按序；`V` 是循环不变量且**与守卫另一侧逐字相同**——
        重复访问时 `C[x]` 已被写成 `V`，守卫立刻为假，所以更不会超 `n(C)`。
        `C[x] = V` 只改元素、不改尺寸，故 `n(C)` 仍是上界。

        `x` 与元素类型都必须是**无符号整数**：`i64` 可能为负，`C[-1]` 的越界
        不能靠 n(C) 兜住——那种情况仍然 ⊤。
        """
        if cond is None or body is None:
            return None, "没有循环条件 / 循环体"
        if getattr(cond, "kind", "") != "binary" or (cond.value or "") != "!=":
            return None, "条件不是 `!=`"

        left, right = (list(cond.children) + [None, None])[:2]

        def _is_index(n):
            return n is not None and getattr(n, "kind", "") == "index" \
                and len(n.children or []) >= 2

        if _is_index(left):
            idx, other = left, right
        elif _is_index(right):
            idx, other = right, left
        else:
            return None, "条件两侧都没有 `C[x]`"

        recv, key = idx.children[0], _paren_inner(idx.children[1])
        if getattr(key, "kind", "") != "path" or not getattr(key, "name", None):
            return None, "被追逐的量不是裸变量"
        x = key.name

        # ① 容器：本帧可见的纯字段链
        cname = field_chain_name(recv)
        if not cname:
            return None, "容器接收者不是纯字段链"
        root = cname.split(".", 1)[0]
        if root not in self._frame_decls():
            return None, f"`{root}` 不是本帧可见的形参 / 局部"
        # **局部引用别名**（`const vector<u64>& C = d.parent;`）不算：`n(C)` 不是
        # 一个稳定的输入尺寸量（求值侧查不到会按 1 算 ⇒ 低估）。形参是另一个
        # 问题——`const vector<u64>& xs` 的 `n(xs)` 就是调用方给的尺寸，收。
        _cur = self._cur_frame_fn()
        _pnode = next((c for c in (getattr(_cur, "children", None) or [])
                       if getattr(c, "kind", "") == "params"), None)
        _pnames = {p.name for p in (getattr(_pnode, "children", None) or [])
                   if getattr(p, "name", None)}
        if root not in _pnames and _is_ref_type(self._frame_decls().get(root)):
            return None, f"`{root}` 是局部引用别名（`n({root})` 不是稳定的尺寸量）"

        # ② 容器类型：可下标的序列（关联容器 / 结构体一律拒）
        rty = self._type_of_expr(recv)
        head, _targs, _tref = self._type_parts(rty)
        if not head:
            return None, f"`{cname}` 的类型认不出"
        if head in _ASSOC_HEADS or head in _SET_HEADS:
            return None, f"`{cname}` 是关联容器（下标不是序号）"
        if head in self.structs:
            return None, f"`{cname}` 是结构体"
        if head in _ARR_HEADS:
            cap = self._array_cap(rty)
            if cap is None:
                return None, "定长数组的容量认不出"
            bound = Bound("const", value=cap)
        elif head in _SEQ_HEADS or head == "string":
            bound = Bound("size", value=cname)
        else:
            return None, f"`{head}` 不是可下标的序列容器"

        # ③ 下标与元素都必须是无符号整数（有符号可能为负 ⇒ C[-1] 越界）
        xtype = self._type_parts(self._type_of_expr(key))[0]
        if xtype not in _CHASE_UNSIGNED:
            return None, f"`{x}` 的类型 `{xtype or '未知'}` 不是无符号整数"
        etype = self._type_parts(self._type_of_expr(idx))[0]
        if etype not in _CHASE_UNSIGNED:
            return None, f"`{cname}[…]` 的元素类型 `{etype or '未知'}` 不是无符号整数"

        direct = list(body.children or [])
        if not direct:
            return None, "循环体为空"
        for k in _CHASE_FORBIDDEN:
            if _descendants(body, k):
                return None, f"体内有 `{k}`（只认直着走一步的追逐）"

        assigns = (list(_descendants(body, "assign"))
                   + list(_descendants(body, "assign-op")))

        # ④ 对 x 的写：恰好一处、`x = …`、是体的直接子语句
        xw = [a for a in assigns
              if getattr((a.children or [None])[0], "kind", "") == "path"
              and getattr((a.children or [None])[0], "name", "") == x]
        if len(xw) != 1:
            return None, f"体内对 `{x}` 的写有 {len(xw)} 处（只认恰好一处）"
        xw = xw[0]
        if getattr(xw, "kind", "") != "assign":
            return None, f"对 `{x}` 的写不是 `{x} = …`"
        if xw not in direct:
            return None, f"对 `{x}` 的写嵌在别的语句里（只认体的直接子语句）"
        rhs = _paren_inner((xw.children + [None, None])[1])

        # ⑤ 对容器（含它的前缀 / 元素）的写；以及任何可能改尺寸的调用
        c_writes = []
        for a in assigns:
            ch = _chase_lvalue_chain((a.children or [None])[0])
            if ch and (ch == cname or ch.startswith(cname + ".")
                       or cname.startswith(ch + ".")):
                c_writes.append(a)
        for call in (list(_descendants(body, "call"))
                     + list(_descendants(body, "method-call"))):
            kids = list(call.children or [])
            if getattr(call, "kind", "") == "method-call" and kids:
                rch = _chase_lvalue_chain(kids[0])
                if rch and (rch == cname or rch.startswith(cname + ".")
                            or cname.startswith(rch + ".")):
                    return None, f"体内对 `{cname}` 调了方法（尺寸可能变）"
            for arg in kids:
                if root in _chase_names(arg):
                    return None, f"体内把 `{root}` 传给了调用（可能改尺寸）"

        # 守卫另一侧只收**裸变量 / 字面量**：复合量（`arr[0]` / `s.f + 1`）的
        # 「执行期间不变」还要证明子表达式没被写，超出这条形状的判据范围。
        other = _paren_inner(other)
        if getattr(other, "kind", "") not in ("path", "lit-int", "lit-bool",
                                              "lit-float", "lit-char",
                                              "lit-string"):
            return None, "守卫另一侧不是裸变量 / 字面量（可能是会变的复合量）"

        def _other_ok(expr) -> bool:
            """守卫另一侧 / 写回值必须是**循环不变量**（`x` 自己除外）。"""
            written = set()
            for a in assigns:
                lhs = _paren_inner((a.children or [None])[0])
                if getattr(lhs, "kind", "") == "path" and getattr(lhs, "name", ""):
                    written.add(lhs.name)
            for nm in _chase_names(expr):
                if nm == x:
                    continue
                if nm == root or nm.startswith(cname + "."):
                    return False
                if nm in written:
                    return False
            return True

        # ---- 形状 A：`x = C[x]`，体里不碰容器
        if (rhs is not None and getattr(rhs, "kind", "") == "index"
                and len(rhs.children or []) >= 2):
            rkey = _paren_inner(rhs.children[1])
            if (field_chain_name(rhs.children[0]) == cname
                    and getattr(rkey, "kind", "") == "path"
                    and (rkey.name or "") == x):
                if c_writes:
                    return None, f"追逐体里改了 `{cname}`（下标空间可能变）"
                if not _other_ok(other):
                    return None, "守卫另一侧不是循环不变量"
                return bound, ("索引追逐：终止执行下走过的下标互不相同 ⇒ 圈数 ≤ n(C)")

        # ---- 形状 B：路径压缩 `next = C[x]; C[x] = V; x = next;`
        if rhs is not None and getattr(rhs, "kind", "") == "path" and rhs.name:
            tmp = rhs.name
            defs = [d for d in direct
                    if getattr(d, "kind", "") in ("var-decl", "const-decl")
                    and getattr(d, "name", "") == tmp]
            if len(defs) != 1:
                return None, f"临时变量 `{tmp}` 的定义不唯一"
            init = _paren_inner((defs[0].children or [None])[-1])
            if not (init is not None and getattr(init, "kind", "") == "index"
                    and len(init.children or []) >= 2
                    and field_chain_name(init.children[0]) == cname):
                return None, f"`{tmp}` 的初值不是 `{cname}[…]`"
            ikey = _paren_inner(init.children[1])
            if getattr(ikey, "kind", "") != "path" or (ikey.name or "") != x:
                return None, f"`{tmp}` 的初值不是 `{cname}[{x}]`"
            # 临时变量只能被定义这一次
            for a in assigns:
                lhs = _paren_inner((a.children or [None])[0])
                if (getattr(lhs, "kind", "") == "path"
                        and (lhs.name or "") == tmp and a is not defs[0]):
                    return None, f"`{tmp}` 在体内被再次赋值"
            pos = {id(s): i for i, s in enumerate(direct)}
            if id(defs[0]) not in pos or pos[id(defs[0])] > pos[id(xw)]:
                return None, f"`{tmp}` 的定义不在 `{x}` 的写之前"
            # 恰好一处元素写 `C[x] = V`，在 x 的写之前
            cw = [a for a in c_writes
                  if getattr(a, "kind", "") == "assign"
                  and getattr((a.children or [None])[0], "kind", "") == "index"
                  and len((a.children or [None])[0].children or []) >= 2
                  and field_chain_name((a.children or [None])[0].children[0]) == cname
                  and getattr(_paren_inner((a.children or [None])[0].children[1]),
                              "kind", "") == "path"
                  and (_paren_inner((a.children or [None])[0].children[1]).name or "")
                  == x]
            if len(cw) != 1 or len(c_writes) != 1:
                return None, f"体内对 `{cname}` 的写不唯一 / 不是 `{cname}[{x}] = …`"
            cwa = cw[0]
            if cwa not in direct or pos[id(cwa)] > pos[id(xw)]:
                return None, f"`{cname}[{x}]` 的写不在 `{x}` 的写之前"
            val = _paren_inner((cwa.children + [None, None])[1])
            # **写回的值必须就是守卫的另一侧**：重复访问时守卫立刻为假。
            if not _chase_same_expr(val, other):
                return None, "写回的值与守卫另一侧不同（重复访问不会退出）"
            if not _other_ok(val):
                return None, "写回的值不是循环不变量"
            return bound, ("路径压缩式索引追逐：写回值 == 守卫另一侧 ⇒ "
                           "重复访问立刻退出 ⇒ 圈数 ≤ n(C)")

        return None, f"`{x}` 的写不是一步的 `{x} = {cname}[{x}]`"

    def _while_inner(self, st: Node, env: Dict[str, Cost]) -> Cost:
        cond = st.children[0] if st.children else None
        body = next((c for c in st.children[1:] if c.kind == "block"), None)

        # **先看索引追逐**（`while (C[x] != x) { x = C[x]; }`）：它不是计数器
        # 形状，旧提取器只会给 ⊤。判据 / 健全性论证见 `_chase_loop_bound`。
        chase, _chase_why = self._chase_loop_bound(st, cond, body)
        if chase is not None:
            b = chase
            self._chase_notes[id(st)] = ("索引追逐：圈数 = n(C)，"
                                         "依赖“κ 只界终止执行”的口径")
        else:
            # 用 G29 的提取器拿圈数。计数器初值从 env 里取（不做数据流分析）。
            # 同时把 env 里已知的尺寸名交给它，避免 `n` 认不出来。
            b, _why = bound_of_condition_wrapper(
                st, self.repeats.get, env, self.row_of, self.capacities,
                self.const_names, self.cur_fn,
                resolve=lambda nm: self._name_count(nm, self.cur_fn),
                size_alias_skip=self._revoked_size_aliases(self.cur_fn))
        if b is None:
            return Cost.top()

        n = self._bound_to_cost_scoped(b)
        if n is None:
            # 界里的名字展开不出来（`const u64 k = xs.at(0); while (i < k)`）：
            # **记 ⊤，不许退回常数**——推不出轮数时"达成"是假承诺。
            return Cost.top()
        # **甲.2**：每轮的成本是「迭代开销 1 + 条件**整条表达式** + 体」。
        # 原先只算 1 个操作代表条件，而解释器每轮都把条件求值一遍
        # （`i < 10` 是 3 个节点）——这是残余差距的主要来源。
        #
        # **等价写法必须同一个 κ**（2026-11-13）：`while (!p(x))` /
        # `while (p(x) == false)` / `while (p(x) != true)` 与 `while (p(x))`
        # 的圈数都来自**同一条** repeats，条件成本也按**裸谓词调用**计——
        # 否则同一个界会因为多一个 `!` / `==` 而变样，四种等价写法给出四个 κ。
        # 多出来的操作是解释器 ops 那边的记账（`!` 每轮 +1，`== false` 每轮
        # +2），由每轮固定的 1 个循环开销与条件成本里已有的余量覆盖；
        # `tests/cases/test_kappa_repeats_neg.py` 在三档规模上钉住 κ ≥ ops。
        # **括号不剥**（`predicate_cost_node`）：`((p(x) == false))` 多两层
        # 括号就多 2 个操作，剥掉会让 κ 低于 ops、上界不成立。
        _cond_node = predicate_cost_node(cond)
        cond_cost = (self._expr(_cond_node, env) if _cond_node is not None
                     else Cost.num(OP_COST))
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
                             for k, v in env.items()
                             if v.kind == "size"
                             and k not in self._revoked_size_aliases(self.cur_fn)}
                rhs = clean_bound_inst(expr_to_bound(cond.children[1]),
                                       self.cur_fn, env_sizes,
                                       self.row_of, self.capacities,)
                # **局部别名按结构展开**（`while (v < total)`，`total` 是
                # `const u64 total = a.length()+b.length()`）：不展开的话
                # 这里会把 `n(total)` 塞进体内——一个求值时按 1 算的假尺寸量。
                rhs_cost = (self._bound_to_cost_scoped(rhs, self.cur_fn)
                            if rhs is not None else None)
                if rhs_cost is None and rhs is not None:
                    # 展开不出（旧行为：`size(局部名)`）→ 保留旧路径，
                    # 不因为这一条把整段体的成本变成 ⊤。
                    rhs_cost = bound_to_cost_inst(
                        rhs, self.const_values, self._scalar_params_of())
                if rhs_cost is not None:
                    body_cost = body_cost.substitute_sizes(
                        {cond.children[0].name: rhs_cost})
            per = Cost.add(per, body_cost)
        # 每圈取最坏那圈，再乘圈数（用户定的第 3 条）；
        # 末尾那次「条件为假」的检查也要算进去。
        return Cost.add(Cost.mul(n, per), per)

    # ---------------------------------------------------------------- `for` 的轮数
    #
    # **2026-11-09 修（κ 低于实测的根因）**：旧 `_for_count` 是
    # 「在整棵 `for` 子树里抓**第一个** `path`，轮数 = `Cost.size(那个名字)`」。
    # 它只在 `for (x in xs)` / `for (i in 0..xs.length())` 这两种拼写下**碰巧**对：
    #
    #   · `for (i in 0..n)`（`n` 是 `xs.length()` 的局部别名）→ 记成 `n(n)`。
    #     `n` 不是输入尺寸量，`cost_check.evaluate` 的 `env.get(c.value, 1.0)`
    #     查不到就按 **1** 算 ⇒ κ 不再是上界；
    #   · `for (k in 0..1000)`（上界是**字面量**）→ 区间里根本没有 `path`，
    #     于是那个"第一个 path"落在了**循环体**里 —— 轮数变成循环体第一个
    #     变量的尺寸，与 1000 毫无关系 ⇒ 同样低估。
    #
    # 现在的口径：**从 `for_iterable(st)` 的形状推；推不出就 `None`（调用方记 ⊤）**，
    # 绝不退回某个默认值。时间成本（`_for`）、分配成本（`_alloc_stmt`）、
    # 增长预扫（`_for_iter_bound`）三处共用这一份。

    def _decl_inits(self, fn) -> Dict[str, Node]:
        """本函数里**不可变**局部声明的初值：`const` + 从未被重写的 `var`。

        · `const-decl`：语言保证不再赋值，初值就是它的值；
        · `var-decl`：只有**全函数都没有**第二次写它时才算（`assign` /
          `assign-op` 的左侧出现过即判"写过"）——写过的可能变大或变小，
          拿声明处的值当上界会低估；
        · 同名多处声明（不同作用域）：把一个初值套到另一个作用域上会猜错 → 不收；
        · 没有初值的声明（`array<u64, 4> dp;`）：没有可追的表达式 → 不收。

        结果按函数缓存（`_alias_cache`）。只做**语法**判定，不做数据流分析——
        判不出来的那些靠 `_name_count` 返回 `None` 兜底（⊤）。
        """
        hit = self._alias_cache.get(id(fn))
        if hit is not None and hit[0] is fn:
            return hit[1]
        inits: Dict[str, Node] = {}
        seen: Dict[str, int] = {}
        for d in (list(_descendants(fn, "const-decl"))
                  + list(_descendants(fn, "var-decl"))):
            nm = getattr(d, "name", None)
            if not nm or len(d.children or []) < 2:
                continue
            if not (getattr(d, "value", None) or {}).get("has_init", True):
                continue
            seen[nm] = seen.get(nm, 0) + 1
            inits.setdefault(nm, d.children[-1])
        written = self._written_names(fn)
        consts = {getattr(d, "name", "")
                  for d in _descendants(fn, "const-decl")}
        out: Dict[str, Node] = {}
        for nm, init in inits.items():
            if seen.get(nm, 0) > 1:
                continue
            if nm not in consts and nm in written:
                continue
            out[nm] = init
        # 第三项是**本函数**里的 `const-decl` 名（`_bound_to_cost_scoped` 用它区分
        # "const 别名但初值认不出（→ ⊤）"与"自由名 / 可变计数器（→ 旧口径 n(名)）"）。
        self._alias_cache[id(fn)] = (fn, out, consts)
        return out

    def _written_names(self, fn) -> set:
        """本函数里**被赋值过的名字**（`assign` / `assign-op` 的左侧）。

        只做语法判定：任一处写过就算写过。用途是**撤销尺寸别名**——
        `u64 m = s.v.size(); m = m + 1;` 之后 `while (i < m)` 的界 MUST NOT
        再用 `n(s.v)`：那是声明处的旧值，不是新值，拿它当上界会**低估**。
        与 `_decl_inits` 共用同一份判定，避免两处漂移。
        """
        if fn is None:
            return set()
        hit = self._written_cache.get(id(fn))
        if hit is not None and hit[0] is fn:
            return hit[1]
        out = set()
        for a in (list(_descendants(fn, "assign"))
                  + list(_descendants(fn, "assign-op"))):
            lhs = a.children[0] if (a.children or []) else None
            if getattr(lhs, "kind", "") == "path" and getattr(lhs, "name", ""):
                out.add(lhs.name)
        self._written_cache[id(fn)] = (fn, out)
        return out

    def _field_bound_blocked(self, fn) -> set:
        """**不许当循环半径**的成员基对象名（保守判定，按函数缓存）。

        `while (row < recv.n_)` 的轮数上界写成 `n(recv.n_)`（成员取值）——这只在
        该字段**在执行期间不变**时成立，因为循环条件每轮重读它。两种"会变"都算：

          ① 函数体里对**该基对象的字段**有写（`recv.n_ = …` / `recv.first_[i] = …`）；
          ② 基对象被**当实参／方法接收者传出去**——非 const 引用形参可能改它，
             与 `_ref_out_params` 是同一口径（那里也只认"非 const 引用"才会写长）。

        字段名不参与判定（保守到"这个对象动过 ⇒ 它所有成员都不许当界"）。
        **只影响"字段能不能当 Bound"**，不动任何已有的尺寸量。
        """
        if fn is None:
            return set()
        hit = self._field_block_cache.get(id(fn))
        if hit is not None and hit[0] is fn:
            return hit[1]
        out: set = set()
        for a in (list(_descendants(fn, "assign"))
                  + list(_descendants(fn, "assign-op"))):
            lhs = a.children[0] if (a.children or []) else None
            # `recv.f = v` / `recv.f[i] = v`：剥掉下标/括号，剩下的字段链根
            while getattr(lhs, "kind", "") in ("index", "paren", "ref", "deref"):
                lhs = lhs.children[0] if lhs.children else None
            nm = field_chain_name(lhs) if lhs is not None else None
            if nm and "." in nm:
                out.add(nm.split(".", 1)[0])
        for c in (list(_descendants(fn, "call"))
                  + list(_descendants(fn, "method-call"))):
            # 实参（`f(s)`）与方法接收者（`s.m()`）都可能是非 const 引用，
            # 于是都算"这个对象被传出去了"。**裸路径也算**（`f(s)` 的 `s` 就是
            # 一条单段字段链）：漏掉它正是最常见的那一种。
            kids = list(c.children or [])
            pairs = []
            if kids and getattr(c, "kind", "") == "method-call":
                pairs.append(kids[0])            # 接收者
            for a in kids:
                if getattr(a, "kind", "") == "args":
                    pairs.extend(a.children or [])
            for x in pairs:
                nm = field_chain_name(x)
                if nm:
                    out.add(nm.split(".", 1)[0])
        self._field_block_cache[id(fn)] = (fn, out)
        return out

    def _field_bound_ok(self, fn, dotted: str) -> bool:
        """`n(recv.n_)` 能不能当循环半径：根是**形参**、且没被改过。"""
        if fn is None or not dotted or "." not in dotted:
            return False
        base = dotted.split(".", 1)[0]
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        names = {(p.name or "") for p in (pnode.children if pnode is not None else [])}
        if base not in names:
            return False
        return base not in self._field_bound_blocked(fn)

    def _elem_bound_blocked(self, fn) -> set:
        """**不许当元素长度上界**的外层容器名（保守判定，按函数缓存）。

        `L(g) = max_i n(g[i])` 是"元素长度的最大值"。它要是全程上界，`g` 在函数
        执行期间就必须**不变**（行数、每行长度都不变）。两种"会变"都算：

          ① 函数体里对 `g` / `g[…]` 有写（`g[v] = row`、`g.push_back(…)`、
             `g[v].push_back(…)`——最后一处剥下标后剩下的根是 `g`）；
          ② `g` 被当实参／方法接收者传出去（非 const 引用形参可能改它），
             与 `_field_bound_blocked` 同一口径。

        与 `_field_bound_blocked` 的区别：**单段路径也要算**（`g[v] = …` 的
        根是 `g`，没有点号），因为改一行就会改 `L(g)`。
        """
        if fn is None:
            return set()
        hit = self._elem_block_cache.get(id(fn))
        if hit is not None and hit[0] is fn:
            return hit[1]
        out: set = set()
        for a in (list(_descendants(fn, "assign"))
                  + list(_descendants(fn, "assign-op"))):
            lhs = a.children[0] if (a.children or []) else None
            while getattr(lhs, "kind", "") in ("index", "paren", "ref", "deref",
                                               "field"):
                lhs = lhs.children[0] if lhs.children else None
            nm = field_chain_name(lhs) if lhs is not None else None
            if nm:
                out.add(nm.split(".", 1)[0])
        # **按引用传出**（`f(&g)`）：被调函数可能改它。
        for r in _descendants(fn, "ref"):
            for p in _descendants(r, "path"):
                if getattr(p, "name", ""):
                    out.add(p.name)
        for c in (list(_descendants(fn, "call"))
                  + list(_descendants(fn, "method-call"))):
            kids = list(c.children or [])
            pairs = []
            if kids and getattr(c, "kind", "") == "method-call":
                # **只读方法不算改**（`g[v].size()` / `g.at(i)` / `.find`…）。
                # 漏了这条会把 `g[v].size()` 自己当成"g 被动了"——正是要用的
                # 那一处把前提否掉。
                _m = (getattr(c, "name", "") or "").split(".")[-1]
                if _m not in _SAFE_METHODS:
                    pairs.append((kids[0], None))       # 接收者
            for a in kids:
                if getattr(a, "kind", "") == "args":
                    for _i, _a in enumerate(a.children or []):
                        pairs.append((_a, _i))
            for x, _i in pairs:
                nm = field_chain_name(x)
                if not nm:
                    continue
                # **传出去 ≠ 会被改**（2026-11-12，语料 `12_graph`）：只有
                # **非 const 引用**形参能写长实参；按值是拷贝、`const T&` 是只读
                # 借用（与 `_call_may_grow_arg` / `_out_growth` 同一口径）。
                # 不认这个区分，`dfs_visit(g, v, color)` 这句自递归会把 `g` 判成
                # "被传出去"⇒ `L(g)` 的前提被否 ⇒ 含 `g[v].size()` 的循环退回 ⊤。
                if _i is not None and getattr(c, "kind", "") == "call":
                    if not self._call_may_grow_arg(
                            (getattr(c, "name", "") or "").split(".")[-1], _i):
                        continue
                out.add(nm.split(".", 1)[0])
        # **增长方法只申请了接收者**（`g.push_back(x)` 的接收者是 `g`）；
        # 上面的 `pairs` 已经覆盖它。`g[v].push_back(x)` 的接收者是 `g[v]`，
        # 上面剥不出名字——这里再按"剥下标后的根"扫一遍**非只读**方法。
        for mc in _descendants(fn, "method-call"):
            _m = (getattr(mc, "name", "") or "").split(".")[-1]
            if _m in _SAFE_METHODS:
                continue
            recv = mc.children[0] if (mc.children or []) else None
            while getattr(recv, "kind", "") in ("index", "paren", "ref", "deref"):
                recv = recv.children[0] if recv.children else None
            nm = field_chain_name(recv) if recv is not None else None
            if nm:
                out.add(nm.split(".", 1)[0])
        self._elem_block_cache[id(fn)] = (fn, out)
        return out

    def _elem_bound_ok(self, fn, name: str) -> bool:
        """`L(g)` 能不能当元素长度的上界：`g` 是**本帧的 const 形参**、
        且没被写/传出去。

        为什么必须是 const 形参：`L(g)` 是对**实参**在当前时刻的元素长度取最大值；
        只有 const 保证函数执行期间不变。局部容器（哪怕是 `const` 局部）不在这里认
        ——它们可能被 `push_back` 扩容（本语言里 const 只约束重新赋值），保守起见
        一律 ⊤；真需要时再把它作为独立的一档补上。
        """
        if fn is None or not name:
            return False
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        if pnode is None:
            return False
        hit = None
        for p in (pnode.children or []):
            if getattr(p, "name", "") == name:
                hit = p
                break
        if hit is None:
            return False
        # **const 形参**：`param` 的 `value` 里带 `{'const': True}`（compat 的
        # `const Graph& g` 折出来就是这样）。
        if not (getattr(hit, "value", None) or {}).get("const"):
            return False
        return name not in self._elem_bound_blocked(fn)

    # ---------------------------------------------------------------- 元素长度上界 L(x)
    #
    # **问题**（2026-11-12 晚，`12_graph.main`）：`Graph g = make_graph(8);
    # add_edge(g,u,v)×7; count_components(g)` —— 被调方 κ 里的 `g[v].size()` 是
    # `L(g)`，调用点上只把**名字**换对不够，还要把**值**换出来，`main` 才不会
    # 是 ⊤（旧实现里这一步换不出来，见 `_rename_callee_elems` 的说明）。
    #
    # **定义**：`L(x) := max_i n(x[i])`——`x` 是容器套容器时，内层长度的上界。
    # 它与 `n(x)` **无关**（每行可以比行数长），所以 MUST NOT 用 `n(x)` 代替。
    #
    # 四条来源（**推不出就 `None` ⇒ 调用点退 ⊤，不猜**）：
    #
    # | # | 构造 | `L(x)` 的上界 | 为什么可证 |
    # |---|---|---|---|
    # | ① | `x = make_graph(n)` / `Graph x(n, empty)` | 被调函数返回值的 L / `n(empty)` | 每行是同一个空行的拷贝 ⇒ L=0 |
    # | ② | 循环里的 `x[u].push_back(v)`（含经被调方非 const 引用形参） | 初始 L + 轮数 × 每次 +1 | 每轮至多让某一内层 +1 |
    # | ③ | `x[i] = row` / `x.push_back(row)` | `max(L(x), n(row))` | 整行赋值/入行：该行长度 ≤ n(row) |
    # | ④ | 其余（`x[i].swap(…)`、别名改写、认不出的调用…） | **None（⊤）** | 不猜 |
    #
    # **方向（硬约束）**：替换只能是 `L(x) → ≥ 真实值`。`max` / `+` 都只会变大；
    # 行数与轮数复用既有的 `_container_bounds_full` / `_for_count` /
    # `_bound_to_cost_scoped`（同一份推导，不另造一套）。
    #
    # **为什么用「整个函数的最大值」而不是逐语句的当前值**：上界只要求 ≥ 任一
    # 执行点的真实值，全程最大值当然满足；逐点值还要处理分支合并，收益只是更紧。
    # 分支仍然按 `max` 合并（各分支要么都有界、要么该项判 None）。

    def _is_nested_container_type(self, ty: Optional[Node]) -> bool:
        """`ty` 是**容器套容器**吗（`vector<vector<T>>` / `map<K, vector<T>>` …）。"""
        elem = self._elem_type(ty)
        return elem is not None and _container_type_name(elem) is not None

    # ---------------------------------------------------------------- 元素长度：哪些容器有 L
    #
    # **2026-11-12 夜（本任务）**：`L(x) := max_i n(x[i])` 原先只对「容器套容器」
    # 有意义。语料 `02_csv_stats` / `08_bits_hash` 的元素是 **string**
    # （`vector<vector<u8>>`，按值语义就是"可数长度的序列"），它本来就是嵌套容器
    # ——真正缺的是「字面量初始化」那一条来源（见 `_elem_expr_bound` 的 ①）。
    # 这里再把**关联容器的容器键**也纳进来：
    #
    #     unordered_map<vector<u8>, u64> freq;      // 键是 string
    #     freq[w] += 1;                             // 插入：键长 ≤ n(w)
    #
    # 这种表的 `L(freq)` 只有一个含义——**键长度的最大值**（哈希/拷贝都要读完整
    # 个键）。它与「map 的值是容器」时的含义（行长）不能混：两者同时成立时
    # `_elem_len_kind` 返回 `""`（不猜，宁可 ⊤）。

    def _elem_len_kind(self, ty: Optional[Node]) -> str:
        """容器的 `L` 语义：`"value"`（元素是容器）/ `"key"`（关联容器的键是容器）。

        `""` = 没有可写的元素长度（标量元素 / 键值都是容器 —— 两种含义冲突）。
        """
        h, args, _r = self._type_parts(ty)
        if h in _ASSOC_HEADS:
            kty = args[0] if args else None
            vty = args[1] if len(args) > 1 else None
            has_k = _container_type_name(kty) is not None
            has_v = _container_type_name(vty) is not None
            if has_k and not has_v:
                return "key"
            if has_v and not has_k:
                return "value"
            return ""               # 键、值都是容器：不猜是哪一个
        if h in _SEQ_HEADS or h in _SET_HEADS or h in _ARR_HEADS:
            ety = args[0] if args else None
            if _container_type_name(ety) is not None:
                return "value"
        return ""

    def _elem_len_type(self, ty: Optional[Node]) -> bool:
        """`ty` 的元素长度 `L` 有意义吗（见 `_elem_len_kind`）。"""
        return bool(self._elem_len_kind(ty))

    def _elem_len_params(self, fn: Optional[Node]) -> List[Node]:
        """`fn` 的**带元素长度**的容器形参节点（按 `params` 顺序）。"""
        out: List[Node] = []
        if fn is None:
            return out
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        for p in (pnode.children if pnode is not None else []):
            ty = next((c for c in (p.children or [])
                       if c.kind in ("type-ref", "type-name")), None)
            if getattr(p, "name", "") and self._elem_len_type(ty):
                out.append(p)
        return out

    def _owner_fn_of(self, node: Optional[Node]) -> Optional[Node]:
        """`node` 属于哪个函数（**帧栈为空时**的兜底：`_initial_container_bound`）。

        为什么需要：`_container_bounds_impl` 先算"声明处初值"的界（不压帧、
        不压 `_fn_stack`），而那条路上会走到 `_param_size_table` → 实参表达式的
        尺寸。没有这一格，`split(lines[i], ',')` 的实参类型/界就查不到，
        局部容器的界会被缓存成 ⊤。
        """
        if node is None:
            return None
        if not self._owner_index:
            for fn in self.fns.values():
                stack = [fn]
                while stack:
                    x = stack.pop()
                    self._owner_index[id(x)] = fn
                    stack.extend(getattr(x, "children", ()) or ())
        return self._owner_index.get(id(node))

    def _type_of_name_in(self, fn: Optional[Node], name: str) -> Optional[Node]:
        """`fn` 里名字（形参 / 局部）的声明类型节点；认不出 → None。"""
        if fn is None or not name:
            return None
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        for p in (pnode.children if pnode is not None else []):
            if getattr(p, "name", "") == name:
                return p.children[0] if (p.children or []) else None
        for d in (list(_descendants(fn, "const-decl"))
                  + list(_descendants(fn, "var-decl"))):
            if getattr(d, "name", "") == name:
                return d.children[0] if (d.children or []) else None
        return None

    def _for_alias_of(self, fn: Optional[Node], name: str) -> Optional[str]:
        """`for (auto& name : <源名字>)` 的**源容器名**；认不出/同名多处 → None。

        只认"迭代的是**裸名字**"的形状（与本文件其它别名口径一致）。同名
        for 指向不同容器时判不出该用哪一个 ⇒ None（不猜）。
        """
        if fn is None or not name:
            return None
        hit = self._for_alias_cache.get(id(fn))
        if hit is not None and hit[0] is fn:
            mp = hit[1]
        else:
            mp: Dict[str, str] = {}
            dup: set = set()
            for f in _descendants(fn, "for"):
                nm = getattr(f, "name", "") or ""
                it = next((c for c in (f.children or []) if c.kind != "block"),
                          None)
                while it is not None and getattr(it, "kind", "") in (
                        "ref", "paren"):
                    it = it.children[0] if it.children else None
                if not nm or it is None or getattr(it, "kind", "") != "path" \
                        or not getattr(it, "name", ""):
                    continue
                if nm in mp and mp[nm] != it.name:
                    dup.add(nm)
                mp[nm] = it.name
            for nm in dup:
                mp.pop(nm, None)
            self._for_alias_cache[id(fn)] = (fn, mp)
        return mp.get(name)

    def _elem_bound_by_name(self, name: str, fn: Optional[Node]) -> Optional["Cost"]:
        """名字的元素长度上界 `L(name)`（用**本帧符号**写）；推不出 → None。

        局部容器查 `_elem_bounds_full`；**const 且带元素长度**的形参原样保留
        `L(形参)`（前提见 `_elem_bound_ok`）；其余（非 const 形参 / 外来名）→ None。
        """
        if not name:
            return None
        if fn is not None:
            eb, _ln = self._elem_bounds_full(fn)
            if name in eb:
                return eb[name]
        ty = self._type_of_name_in(fn, name)
        if self._elem_len_type(ty) and self._elem_bound_ok(fn, name):
            return Cost("sym", f"L({name})")
        return None

    def _elem_sym_params(self, fn: Optional[Node]) -> set:
        """`fn` 里可以**原样保留**的 `L(形参)`：只有 const 嵌套容器形参。"""
        return {getattr(p, "name", "") or "" for p in self._elem_len_params(fn)
                if (getattr(p, "value", None) or {}).get("const")}

    def _elem_alias_size_bound(self, fn: Optional[Node],
                               name: str) -> Optional["Cost"]:
        """`n(<范围 for 的元素>)` 的上界 = `L(所属容器)`；不适用 → None。

        ```cpp
        for (const auto& field : fields) {          // field 是 fields 的元素
            for (i = 0; i < field.size(); ++i) …    // 旧口径：n(field)
        }                                           // 新口径：L(fields)
        ```

        `n(field)` 这个名字**不属于任何形参、也不是可写的局部容器**
        ⇒ target 里写不出来 ⇒ `compare` 永远落 E23（2026-11-13，
        `11_text.main` 是语料最后一个判不了的函数）。真值是"某一个元素的
        长度"，而上界**已经算好了**：`L(fields) = max_i n(fields[i])`
        （`_elem_bounds_full`，四条来源）。

        **方向**：`n(field) ≤ L(fields)` ⇒ 只变大，κ 仍是上界（硬约束）。
        **只对"元素带长度"的源容器认**（`vector<string>` /
        `vector<vector<T>>` / map 的键是容器）：`for (const auto& x : xs)`
        的标量元素没有"长度"这回事，落回旧口径（不猜）。

        三处调用点共用这一份判据（**不另造界**），是因为 `n(元素)` 有三个
        出生地：循环上界（`_bound_to_cost_scoped`）、局部尺寸别名展开
        （`_count_of_expr`）、容器长度表达式（`_container_expr_size`）。
        """
        if fn is None or not name:
            return None
        src = self._for_alias_of(fn, name)
        if src is None:
            return None
        if not self._elem_len_type(self._type_of_name_in(fn, src)):
            return None
        return self._elem_bound_by_name(src, fn)

    def _elem_bounds_full(self, fn: Node):
        """局部容器的**元素长度上界**表：`(eb, 局部名集合)`。

        与 `_container_bounds_full` 平行：那个给 `n(x)`，这个给 `L(x)`。
        `eb[name] is None` = 推不出（**调用点 MUST 退 ⊤**）。
        """
        hit = self._elem_bounds_cache.get(id(fn))
        if hit is not None and hit[0] is fn:
            return hit[1], hit[2]
        # 重入（扫自己体内的调用）：给「扫到这一句为止」的偏界。
        if id(fn) in self._elem_bounds_busy:
            for _f, _e in reversed(self._elem_scan_ctx):
                if _f is fn:
                    return dict(_e), set(_e)
            return {}, set()
        self._elem_bounds_busy.add(id(fn))
        try:
            eb, local_names = self._elem_bounds_impl(fn)
        finally:
            self._elem_bounds_busy.discard(id(fn))
        self._elem_bounds_cache[id(fn)] = (fn, eb, local_names)
        return eb, local_names

    def _elem_bounds_impl(self, fn: Node):
        """`_elem_bounds_full` 的实现体（缓存/重入由外层管）。"""
        decls = []
        local_names: set = set()
        counts: Dict[str, int] = {}
        for d in (list(_descendants(fn, "const-decl"))
                  + list(_descendants(fn, "var-decl"))):
            nm = getattr(d, "name", None)
            if not nm:
                continue
            ty = d.children[0] if d.children else None
            if not self._elem_len_type(ty):
                continue
            decls.append(d)
            local_names.add(nm)
            counts[nm] = counts.get(nm, 0) + 1
        if not decls:
            return {}, set()
        eb: Dict[str, Optional[Cost]] = {}
        # 每个嵌套局部都先占位（`None` = 还没算出 / 推不出）：扫描时按声明顺序填。
        for d in decls:
            eb.setdefault(getattr(d, "name", ""), None)
        # 同名多处声明（作用域不同）：不能把一个界套到另一个上 → 不替换。
        for nm, c in counts.items():
            if c > 1:
                eb[nm] = None
        # **元素别名**（`row = g[i]` / `for (auto& row : g)`）能绕过名字直接改行。
        # 语料里没有这种写法；出现了就整项判推不出（保守，不猜）。
        #
        # **2026-11-13：把这组名字并进 `_elem_dup_names`**。以前只在这里把
        # `eb[nm]` 置 None，可紧接着的扫描是按**声明处初值**填的
        # （`_elem_scan_decl` 只看 `_elem_dup_names`）——于是这个 None 被覆盖，
        # 守卫等于没生效。别名的写点（`row.push_back(…)`）没有进增长预扫，
        # 所以覆盖后的 `L(容器)` 会比真实值**小**：一旦它被当作 `n(元素)` 的
        # 上界用（`_elem_alias_size_bound`），κ 就成了下界。并进
        # `_elem_dup_names` 之后扫描不再覆盖，这条守卫才真正挡得住。
        _alias_targets = self._elem_alias_targets(fn, local_names)
        for nm in _alias_targets:
            eb[nm] = None

        bounds, _ln, params, row_of, caps = self._container_bounds_full(fn)
        bounds = dict(bounds)
        env: Dict[str, Cost] = {}
        const_names = {getattr(d, "name", "") for d in _descendants(fn, "const-decl")
                       if getattr(d, "name", "")}
        body = next((c for c in (fn.children or []) if c.kind == "block"), None)
        if body is not None:
            _prev_kinds = getattr(self, "_scan_kinds", None)
            _prev_written = self._scan_written
            _prev_cur = self.cur_fn
            _prev_seen = getattr(self, "_elem_decl_seen", None)
            _prev_dup = getattr(self, "_elem_dup_names", None)
            self.cur_fn = fn
            self._scan_kinds = {}
            self._scan_written = set()
            self._elem_decl_seen = set()
            self._elem_dup_names = ({nm for nm, c in counts.items() if c > 1}
                                    | set(_alias_targets))
            _nested = [d for d in decls if getattr(d, "name", None)]
            self._scan_kinds = {d.name: (_container_type_name(
                d.children[0] if d.children else None) or "")
                for d in _nested}
            self._fn_stack.append(fn)
            self._elem_scan_ctx.append((fn, eb))
            try:
                self._elem_scan_stmts(body.children, Cost.num(1), eb, bounds, env,
                                      caps, row_of, const_names, params, fn)
            finally:
                self._elem_scan_ctx.pop()
                self._fn_stack.pop()
                self._scan_kinds = _prev_kinds
                self._scan_written = _prev_written
                self._elem_decl_seen = _prev_seen
                self._elem_dup_names = _prev_dup
                self.cur_fn = _prev_cur
        # 收尾：`L(局部)` 链展开到常量/形参；链断在局部上 → None（不猜）。
        sym_ok = self._elem_sym_params(fn)
        for nm in list(eb):
            if eb[nm] is None:
                continue
            v = self._resolve_elem_cost(eb[nm], eb, sym_ok, {nm})
            if v is not None and (set(v.elem_names()) & local_names):
                v = None
            eb[nm] = v
        return eb, local_names

    def _elem_alias_targets(self, fn: Node, local_names: set) -> set:
        """会被**元素别名**改到行的外层容器名（`row = g[i]` / `for (auto& row : g)`）。

        只做「有这种写法就算推不出」的保守判定：别名改名、`row.push_back`、
        `row = …`、把 `row` 传给别的函数都可能改 `g` 某一行的长度。
        行内写标量（`row[k] = 1`）不改长度，但这里也保守地算进去——语料 0 例，
        不值得为它多写一套区分。
        """
        out: set = set()
        if not local_names:
            return out
        alias: Dict[str, str] = {}
        for d in (list(_descendants(fn, "const-decl"))
                  + list(_descendants(fn, "var-decl"))):
            nm = getattr(d, "name", None)
            if not nm:
                continue
            ty = d.children[0] if d.children else None
            if _container_type_name(ty) is None or self._is_nested_container_type(ty):
                continue
            init = d.children[-1] if len(d.children or []) >= 2 else None
            root = self._base_path_name(init)
            if root in local_names:
                alias[nm] = root
        for f in _descendants(fn, "for"):
            it = next((c for c in (f.children or []) if c.kind != "block"), None)
            if it is None or getattr(it, "kind", "") != "path":
                continue
            if getattr(it, "name", "") in local_names:
                v = getattr(f, "name", "") or ""
                if v:
                    alias[v] = it.name
        if not alias:
            return out
        for a in (list(_descendants(fn, "assign"))
                  + list(_descendants(fn, "assign-op"))):
            lhs = a.children[0] if (a.children or []) else None
            root = self._base_path_name(lhs)
            if root in alias:
                out.add(alias[root])
        for mc in _descendants(fn, "method-call"):
            root = self._base_path_name(
                mc.children[0] if (mc.children or []) else None)
            if root in alias and _callee_short(mc) not in _SAFE_METHODS:
                out.add(alias[root])
        for c in _descendants(fn, "call"):
            # **按"能不能改长"筛实参位置**（2026-11-13）：以前把别名传出去
            # 一律算"可能改行"，于是
            #
            #     for (const auto& line : lines) { auto fs = split(line, ','); … }
            #
            # 里 `split` 的 `const vector<u8>&` 形参（**不可能改**）也把
            # `lines` 抹成"L 推不出"，四跳链在第一步就断了。
            # 判据用 `_call_may_grow_arg`（与 `_modelled_write_locals` 同一份）：
            # 用户函数的非 const 引用 / 认不出的被调方才算。
            _short = _callee_short(c)
            args = next((x for x in (c.children or []) if x.kind == "args"), None)
            for _i, a in enumerate(args.children if args is not None else []):
                root = self._base_path_name(a)
                if root in alias and self._call_may_grow_arg(_short, _i):
                    out.add(alias[root])
        return out

    def _base_path_name(self, e) -> str:
        """表达式 → **根名字**（剥 `ref`/`paren`/`deref`/`index`/`field` 链）。

        `g[v][k]` → `g`；`s.v[i]` → `s.v`；认不出 → `""`。
        """
        while e is not None:
            k = getattr(e, "kind", "")
            if k in ("ref", "paren", "deref", "try", "cast"):
                e = e.children[0] if e.children else None
                continue
            if k == "index":
                e = e.children[0] if e.children else None
                continue
            break
        if e is None:
            return ""
        if getattr(e, "kind", "") == "path":
            return getattr(e, "name", "") or ""
        if getattr(e, "kind", "") == "field":
            return field_chain_name(e) or ""
        return ""

    def _index_root(self, e):
        """`(根名字, 下标层数)`：`g[v]` → `("g", 1)`；`g` → `("g", 0)`。"""
        depth = 0
        while e is not None and getattr(e, "kind", "") in (
                "ref", "paren", "deref", "try", "cast"):
            e = e.children[0] if e.children else None
        while e is not None and getattr(e, "kind", "") == "index":
            depth += 1
            e = e.children[0] if e.children else None
            while e is not None and getattr(e, "kind", "") in (
                    "ref", "paren", "deref", "try", "cast"):
                e = e.children[0] if e.children else None
        if e is None:
            return "", 0
        if getattr(e, "kind", "") == "path":
            return getattr(e, "name", "") or "", depth
        if getattr(e, "kind", "") == "field":
            return field_chain_name(e) or "", depth
        return "", 0

    def _resolve_elem_cost(self, c: Optional["Cost"], eb, sym_ok,
                           seen=None) -> Optional["Cost"]:
        """把成本式里的 `L(局部)` 换成它的界；推不出 → None。"""
        if c is None:
            return None
        seen = set(seen or ())
        if c.kind == "sym":
            v = str(c.value or "")
            if v.startswith("L(") and v.endswith(")"):
                nm = v[2:-1]
                if nm in eb:
                    if nm in seen:
                        return None         # 循环依赖：不猜
                    b = eb[nm]
                    if b is None:
                        return None
                    return self._resolve_elem_cost(b, eb, sym_ok, seen | {nm})
                if nm in sym_ok:
                    return c                # 合法尺寸量（const 形参）
                return None                 # 认不出的名字：不猜
            return c
        if c.kind in ("num", "top", "size", "ext"):
            return c
        if c.kind in ("add", "mul", "max", "div"):
            l = self._resolve_elem_cost(c.args[0], eb, sym_ok, seen)
            r = self._resolve_elem_cost(c.args[1], eb, sym_ok, seen)
            if l is None or r is None:
                return None
            if c.kind == "add":
                return Cost.add(l, r)
            if c.kind == "mul":
                return Cost.mul(l, r)
            if c.kind == "div":
                return Cost.div(l, r)
            return Cost.max(l, r)
        if c.kind == "par":
            vals = [self._resolve_elem_cost(t, eb, sym_ok, seen) for t in c.args]
            return None if any(v is None for v in vals) else Cost.par(vals)
        if c.kind == "log2":
            a = self._resolve_elem_cost(c.args[0], eb, sym_ok, seen)
            return None if a is None else Cost.log2(a)
        if c.kind == "pow":
            a = self._resolve_elem_cost(c.args[0], eb, sym_ok, seen)
            return None if a is None else Cost.pow(a, c.value)
        if c.kind == "piece":
            var, segs = c.value
            out = []
            for lo, hi, sub in segs:
                s = self._resolve_elem_cost(sub, eb, sym_ok, seen)
                if s is None:
                    return None
                out.append((lo, hi, s))
            outside = self._resolve_elem_cost(c.args[0], eb, sym_ok, seen)
            return None if outside is None else Cost.piece(var, out, outside)
        return c

    # ---------------------------------------------------------------- 四条来源
    def _elem_filled_bound(self, val, eb, fn, sym_ok, depth=0) -> Optional["Cost"]:
        """`vector<vector<T>>::filled(fill, n)` / `(n)` 的 `L`。

        每行都是 `fill` 的拷贝（缺省 fill = 空行）⇒ `L = n(fill)`。`fill` 是
        标量字面量时只可能是 parser 补的默认值（`vector<vector<T>>(n)`），
        语义是**空行**，给 0。
        """
        args = next((c for c in (val.children or []) if c.kind == "args"), None)
        kids = list(args.children if args is not None else [])
        if not kids:
            return Cost.num(0)
        fill = kids[0]
        ln = self._container_expr_size(fill)
        if ln is not None:
            return ln
        if _int_literal(fill) is not None:
            return Cost.num(0)
        if self._elem_expr_bound(fill, eb, fn, sym_ok, depth + 1) is not None:
            # fill 本身是嵌套容器：每行是它的拷贝 —— 那种类型不叫"行"，
            # 但上界仍可写 L(fill)。
            return self._elem_expr_bound(fill, eb, fn, sym_ok, depth + 1)
        return None

    def _elem_expr_bound(self, val, eb, fn, sym_ok=None,
                         depth=0) -> Optional["Cost"]:
        """容器表达式（嵌套容器）的 `L` 上界；推不出 → None。"""
        if depth > 8 or val is None:
            return None
        while getattr(val, "kind", "") in ("ref", "paren", "try", "cast", "deref"):
            val = val.children[0] if val.children else None
        if val is None:
            return None
        sym_ok = sym_ok or set()
        k = getattr(val, "kind", "")
        if k == "path":
            nm = getattr(val, "name", "") or ""
            if nm in eb:
                return eb[nm]
            if nm in sym_ok:
                return Cost("sym", f"L({nm})")
            return None
        if k == "field":
            nm = field_chain_name(val)
            if nm and nm in eb:
                return eb[nm]
            return None
        if k in ("list-init", "list-lit", "array", "vec-lit"):
            # **① 初始化列表**（2026-11-12 夜，`02_csv_stats`）：容器在构造时
            # 每个元素就是写明的字面量/表达式，`L(x) = max(各元素长度)` 是**精确**的。
            # `vector<string> lines{"a,1", "b,2", "a,3"}` ⇒ `L(lines) = 3`
            # （`= {"…", …}` 在 AST 里也是同一个 `list-init`，不另写一条）。
            # 有一个元素长度推不出 ⇒ 整体 None（不猜）。
            if not (val.children or []):
                return Cost.num(0)
            total = None
            for ch in (val.children or []):
                ln = self._container_expr_size(ch)
                if ln is None:
                    return None
                total = ln if total is None else Cost.max(total, ln)
            return total
        if k in ("call", "method-call"):
            short = _callee_short(val)
            if k == "call" and short in self.fns:
                return self._ret_elem_of(short, val)
            if short in ("keys", "values", "items", "entries", "iter"):
                # **③ 派生容器的元素长度**：`ks = m.keys()` 的每个键都是 `m`
                # 的键的拷贝 ⇒ `L(ks) = L(m)`（键长的最大值）。`values()` 同理
                # 按"元素是容器"那一档；两种含义在同一张表上时 `_elem_len_kind`
                # 已经判过了（返回 `""`），这里再对一次口径。
                if short in ("items", "entries"):
                    return None         # 条目是元组：没有单一的 L
                recv = val.children[0] if (val.children or []) else None
                want = "key" if short in ("keys", "iter") else "value"
                _rty = None
                if recv is not None and getattr(recv, "kind", "") == "path":
                    _rty = self._type_of_name_in(fn, getattr(recv, "name", "") or "")
                if _rty is None:
                    _rty = self._type_of_expr(recv)
                if self._elem_len_kind(_rty) != want:
                    return None
                return self._elem_expr_bound(recv, eb, fn, sym_ok, depth + 1)
            if short == "filled":
                return self._elem_filled_bound(val, eb, fn, sym_ok, depth)
            if short in ("clone", "copy", "to_vec"):
                recv = val.children[0] if (val.children or []) else None
                return self._elem_expr_bound(recv, eb, fn, sym_ok, depth + 1)
            if short in _EMPTY_CTOR_SHORTS and (
                    _is_qualified_call(val)
                    or short in ("map_new", "set_new", "vec_new", "deque_new")):
                return Cost.num(0)
            return None
        return None

    def _ret_elem_of(self, name: str,
                     call_node: Optional[Node]) -> Optional["Cost"]:
        """用户函数 `name(...)` 返回值的 `L`（用**调用方**的符号写）；推不出 → None。"""
        fn = self.fns.get(name)
        if fn is None or call_node is None:
            return None
        ret = _ret_type_of(fn)
        if not self._elem_len_type(ret):
            return None
        if name in self._elem_ret_chain:
            return None                     # 返回 L 互相依赖成环：不猜
        self._elem_ret_chain.append(name)
        try:
            eb, _ln = self._elem_bounds_full(fn)
            body = next((c for c in (fn.children or []) if c.kind == "block"), None)
            sym_ok = self._elem_sym_params(fn)
            sizes: List[Cost] = []
            for r in _descendants(body, "return"):
                e = r.children[0] if r.children else None
                b = self._returned_elem(e, eb, fn, sym_ok)
                if b is None:
                    return None         # 有一条出口推不出 ⇒ 整体推不出
                sizes.append(b)
            if not sizes:
                return None
            total = sizes[0]
            for s in sizes[1:]:
                total = Cost.max(total, s)
            # **先换 `L(形参)` 的名字，再换尺寸**（2026-11-12 夜）：顺序不能反。
            # 反过来的话，`n(s) → L(调用方容器)` 先落地，`_rename_callee_elems`
            # 会把**调用方的** `L(lines)` 当成"被调方的 L(形参)"去查位置表，
            # 查不到 ⇒ 判泄漏 ⇒ 明明推得出来也退 ⊤（`02_csv_stats` 的
            # `parts = split(lines[i], ',')` 正是这一格）。
            total, leak = self._rename_callee_elems(fn, total, call_node)
            if leak:
                return None
            # 形参实例化：尺寸（`n(形参)`）+ 标量（`sym(形参)`）+ `L(形参)`。
            tab = self._param_size_table(fn, call_node)
            total = total.substitute_sizes(tab)
            sub = self._subst_scalar_syms(total, fn, call_node, loose=True)
            if sub is not None:
                total = sub
            return total
        finally:
            self._elem_ret_chain.pop()

    def _returned_elem(self, e, eb, fn, sym_ok) -> Optional["Cost"]:
        """`return <e>;` 里嵌套容器的 `L`（被调函数自己的符号）。"""
        while e is not None and getattr(e, "kind", "") in (
                "ref", "paren", "try", "cast", "deref"):
            e = e.children[0] if e.children else None
        if e is None:
            return None
        k = getattr(e, "kind", "")
        if k == "path":
            nm = getattr(e, "name", "") or ""
            if nm in eb:
                return eb[nm]
            if nm in sym_ok:
                return Cost("sym", f"L({nm})")
            return None
        if k in ("call", "method-call"):
            short = _callee_short(e)
            if short == "filled":
                return self._elem_filled_bound(e, eb, fn, sym_ok)
            if short in ("clone", "copy", "to_vec"):
                recv = e.children[0] if (e.children or []) else None
                return self._returned_elem(recv, eb, fn, sym_ok)
            if k == "call" and short in self.fns:
                return self._ret_elem_of(short, e)
            if short in _EMPTY_CTOR_SHORTS and (
                    _is_qualified_call(e)
                    or short in ("map_new", "set_new", "vec_new", "deque_new")):
                return Cost.num(0)
            return None
        return None

    # ---------------------------------------------------------------- 增长：被调方的 L
    def _elem_growth(self, fn: Optional[Node], pname: str) -> Optional[Cost]:
        """被调函数 `fn` 对形参 `pname` 的**元素长度增长**（不含进入时的 L）。

        单位是被调方自己的符号（标量形参 / 模块常量）；推不出 → None。
        """
        if fn is None or not pname:
            return None
        hit = self._elem_grow_cache.get(id(fn))
        if hit is not None and hit[0] is fn:
            return hit[1].get(pname)
        if id(fn) in self._elem_grow_busy:
            return None                     # 递归 / 互递归：不猜
        self._elem_grow_busy.add(id(fn))
        try:
            tab = self._elem_growth_impl(fn)
        finally:
            self._elem_grow_busy.discard(id(fn))
        self._elem_grow_cache[id(fn)] = (fn, tab)
        return tab.get(pname)

    def _elem_growth_impl(self, fn: Node) -> Dict[str, Optional[Cost]]:
        """`_elem_growth` 的实现体：对每个非 const 引用的嵌套容器形参扫一遍。"""
        outs: Dict[str, str] = {}
        for p in self._elem_len_params(fn):
            ty = next((c for c in (p.children or [])
                       if c.kind in ("type-ref", "type-name")), None)
            if not _is_ref_type(ty):
                continue
            if (getattr(p, "value", None) or {}).get("const"):
                continue
            outs[getattr(p, "name", "")] = getattr(p, "name", "")
        if not outs:
            return {}
        body = next((c for c in (fn.children or []) if c.kind == "block"), None)
        if body is None:
            return {p: Cost.num(0) for p in outs}
        grow: Dict[str, Optional[Cost]] = {p: Cost.num(0) for p in outs}
        # 体内的嵌套局部容器（`row = g[i]` 之类）也要占位，否则 `_elem_expr_bound`
        # 查不到它们 —— 但它们的名字**不是**出参，最后只交出 `outs`。
        _dup: Dict[str, int] = {}
        for d in (list(_descendants(fn, "const-decl"))
                  + list(_descendants(fn, "var-decl"))):
            nm = getattr(d, "name", None)
            if not nm:
                continue
            ty = d.children[0] if d.children else None
            if not self._elem_len_type(ty):
                continue
            grow.setdefault(nm, None)
            _dup[nm] = _dup.get(nm, 0) + 1
        for nm, c in _dup.items():
            if c > 1:
                grow[nm] = None
        bounds, _ln, params, row_of, caps = self._container_bounds_full(fn)
        bounds = dict(bounds)
        env: Dict[str, Cost] = {}
        const_names = {getattr(d, "name", "") for d in _descendants(fn, "const-decl")
                       if getattr(d, "name", "")}
        _prev_kinds = getattr(self, "_scan_kinds", None)
        _prev_written = self._scan_written
        _prev_cur = self.cur_fn
        _prev_seen = getattr(self, "_elem_decl_seen", None)
        _prev_dup = getattr(self, "_elem_dup_names", None)
        self.cur_fn = fn
        self._scan_kinds = {}
        self._scan_written = set()
        self._elem_decl_seen = set()
        self._elem_dup_names = {nm for nm, c in _dup.items() if c > 1}
        self._fn_stack.append(fn)
        self._elem_scan_ctx.append((fn, grow))
        try:
            self._elem_scan_stmts(body.children, Cost.num(1), grow, bounds, env,
                                  caps, row_of, const_names, params, fn)
        finally:
            self._elem_scan_ctx.pop()
            self._fn_stack.pop()
            self._scan_kinds = _prev_kinds
            self._scan_written = _prev_written
            self._elem_decl_seen = _prev_seen
            self._elem_dup_names = _prev_dup
            self.cur_fn = _prev_cur
        sym_ok = self._elem_sym_params(fn) - set(outs)
        out: Dict[str, Optional[Cost]] = {}
        for p in outs:
            g = grow.get(p)
            if g is None:
                out[p] = None
                continue
            g = self._resolve_elem_cost(g, grow, sym_ok, frozenset({p}))
            if g is not None and (set(g.elem_names()) & set(outs)):
                g = None                    # 残留别的出参的 L：不猜（进入值 ≠ 增长）
            out[p] = g
        return out

    def _elem_growth_at_call(self, short: str, call: Node,
                             idx: int) -> Optional[Cost]:
        """调用 `short(…, x, …)` 会让 `L(x)` 长多少（调用方符号）；推不出 → None。"""
        fn = self.fns.get(short)
        if fn is None:
            return None
        p = self._param_at(fn, idx)
        if p is None or not getattr(p, "name", ""):
            return None
        ty = p.children[0] if (p.children or []) else None
        if not _is_ref_type(ty) or (getattr(p, "value", None) or {}).get("const"):
            return Cost.num(0)              # 按值 / const&：改不到调用方的对象
        if not self._elem_len_type(ty):
            return Cost.num(0)
        g = self._elem_growth(fn, p.name)
        if g is None:
            return None
        return self._instantiate_callee_cost(g, fn, call,
                                             caller_fn=self._cur_frame_fn())

    # ---------------------------------------------------------------- 元素长度预扫
    def _grow_elem(self, eb, name: str, factor: Optional[Cost],
                   amount: Optional[Cost] = None) -> None:
        """`x[i].push_back(v)`：该内层最多 +1，随当前循环倍数放大。

        **`amount`**（2026-11-13）：一次调用加几个元素。`None` = 老口径的 1 个
        （push 族）；三参 `insert` 传 `count`，推不出时传 `Cost.top()`（界 ⊤）。
        """
        if name not in eb:
            return
        if factor is None or eb[name] is None:
            eb[name] = None
            return
        if amount is None:
            amount = Cost.num(1)
        eb[name] = Cost.add(eb[name], Cost.mul(factor, amount))

    def _elem_scan_stmts(self, stmts, factor, eb, bounds, env, caps,
                         row_of, const_names, params, fn) -> None:
        for st in (stmts or []):
            self._elem_scan_stmt(st, factor, eb, bounds, env, caps,
                                 row_of, const_names, params, fn)

    def _elem_scan_stmt(self, st, factor, eb, bounds, env, caps,
                        row_of, const_names, params, fn) -> None:
        k = getattr(st, "kind", "")
        if k in ("const-decl", "var-decl"):
            self._elem_scan_decl(st, factor, eb, bounds, env, caps, row_of,
                                 params, fn)
            return
        if k in ("assign", "assign-op"):
            lhs = st.children[0] if st.children else None
            rhs = st.children[1] if len(st.children or []) > 1 else None
            if (lhs is not None and getattr(lhs, "kind", "") == "path"
                    and getattr(lhs, "name", "")):
                self._scan_written.add(lhs.name)
            self._elem_scan_expr(lhs, factor, eb, bounds, env, caps, row_of,
                                 const_names, params, fn)
            self._elem_assign_effect(st, lhs, rhs, factor, eb, bounds, env,
                                     params, fn)
            self._elem_scan_expr(rhs, factor, eb, bounds, env, caps, row_of,
                                 const_names, params, fn)
            return
        if k == "if":
            cond = st.children[0] if st.children else None
            self._elem_scan_expr(cond, factor, eb, bounds, env, caps, row_of,
                                 const_names, params, fn)
            runs = []
            for c in (st.children or [])[1:]:
                if c.kind == "block":
                    runs.append(c.children)
                elif c.kind == "if":
                    runs.append([c])
            if runs:
                self._elem_scan_branches(runs, factor, eb, bounds, env, caps,
                                         row_of, const_names, params, fn)
            return
        if k == "while":
            cond = st.children[0] if st.children else None
            self._elem_scan_expr(cond, factor, eb, bounds, env, caps, row_of,
                                 const_names, params, fn)
            body = next((c for c in (st.children or [])[1:] if c.kind == "block"),
                        None)
            f2 = None
            if factor is not None:
                b, _why = bound_of_condition_wrapper(
                    st, self.repeats.get, env, row_of, caps, const_names, fn,
                    resolve=lambda nm, _f=fn: self._name_count(nm, _f),
                    size_alias_skip=self._revoked_size_aliases(fn))
                if b is not None:
                    lb = self._bound_to_cost_scoped(b, fn)
                    lb = self._resolve_local_cost(lb, bounds, env)
                    if lb is not None and (set(lb.variables())
                                           & self._scan_written):
                        lb = None
                    f2 = None if lb is None else Cost.mul(factor, lb)
            if body is not None:
                self._elem_scan_stmts(body.children, f2, eb, bounds, env, caps,
                                      row_of, const_names, params, fn)
            return
        if k == "for":
            it = next((c for c in (st.children or []) if c.kind != "block"), None)
            self._elem_scan_expr(it, factor, eb, bounds, env, caps, row_of,
                                 const_names, params, fn)
            body = next((c for c in (st.children or []) if c.kind == "block"), None)
            ib = None if factor is None else self._for_iter_bound(
                st, bounds, env, params)
            f2 = None if (factor is None or ib is None) else Cost.mul(factor, ib)
            if body is not None:
                self._elem_scan_stmts(body.children, f2, eb, bounds, env, caps,
                                      row_of, const_names, params, fn)
            return
        if k == "match":
            scrut = match_scrutinee(st)
            self._elem_scan_expr(scrut, factor, eb, bounds, env, caps, row_of,
                                 const_names, params, fn)
            runs = []
            for a in arms_of(st):
                blk = arm_block(a)
                runs.append(blk.children if blk is not None else arm_single_stmts(a))
            if runs:
                self._elem_scan_branches(runs, factor, eb, bounds, env, caps,
                                         row_of, const_names, params, fn)
            return
        if k == "block":
            self._elem_scan_stmts(st.children, factor, eb, bounds, env, caps,
                                  row_of, const_names, params, fn)
            return
        if k == "parallel":
            body = stmt_body(st)
            for task in (body.children if body is not None else []):
                if getattr(task, "kind", "") == "block":
                    self._elem_scan_stmts(task.children, factor, eb, bounds, env,
                                          caps, row_of, const_names, params, fn)
                else:
                    self._elem_scan_stmt(task, factor, eb, bounds, env, caps,
                                         row_of, const_names, params, fn)
            return
        for c in (st.children or []):
            if getattr(c, "kind", "") == "block":
                self._elem_scan_stmts(c.children, factor, eb, bounds, env, caps,
                                      row_of, const_names, params, fn)
            else:
                self._elem_scan_expr(c, factor, eb, bounds, env, caps, row_of,
                                     const_names, params, fn)

    def _elem_scan_decl(self, d, factor, eb, bounds, env, caps, row_of,
                        params, fn) -> None:
        nm = getattr(d, "name", None)
        has_init = (getattr(d, "value", None) or {}).get(
            "has_init", len(d.children or []) > 1)
        val = d.children[-1] if (has_init and len(d.children or []) >= 2) else None
        if not nm or nm not in eb:
            if val is not None:
                self._elem_scan_expr(val, factor, eb, bounds, env, caps, row_of,
                                     set(), params, fn)
            return
        if nm not in self._elem_decl_seen:
            self._elem_decl_seen.add(nm)
            if nm not in self._elem_dup_names:      # 同名多处声明：保持 None
                if has_init:
                    eb[nm] = self._elem_expr_bound(
                        val, eb, fn, self._elem_sym_params(fn))
                else:
                    eb[nm] = Cost.num(0)            # 默认构造：没有行 ⇒ L=0
        if val is not None:
            self._elem_scan_expr(val, factor, eb, bounds, env, caps, row_of,
                                 set(), params, fn)

    def _elem_assign_effect(self, st, lhs, rhs, factor, eb, bounds, env,
                            params, fn) -> None:
        """整行赋值 / 整容器赋值对 `L` 的影响；认不出 → None。"""
        if lhs is None:
            return
        op = getattr(st, "value", "") or ""
        if getattr(lhs, "kind", "") == "path":
            nm = getattr(lhs, "name", "") or ""
            if nm not in eb:
                return
            if op in ("+=",):
                # `text = text + e` 的容器版：每执行一次加 n(e)。
                extra = self._container_expr_size(rhs)
                if eb[nm] is None or factor is None or extra is None:
                    eb[nm] = None
                else:
                    eb[nm] = Cost.add(eb[nm], Cost.mul(factor, extra))
                return
            b = self._elem_expr_bound(rhs, eb, fn, self._elem_sym_params(fn))
            if b is None or eb[nm] is None:
                eb[nm] = None
            else:
                eb[nm] = Cost.max(eb[nm], b)
            return
        root, depth = self._index_root(lhs)
        if not root or root not in eb:
            return
        if depth != 1:
            # `x[i][k] = v`：写的是行里的标量，不改行长度。
            return
        if self._elem_len_kind(self._type_of_name_in(fn, root)) == "key":
            # **关联容器的下标写是插入**（`m[k] = v` / `m[k] += 1`，C++ 语义）。
            # 这种表的 `L(m)` 是**键长**的最大值 ⇒ 只与键有关，与 v 无关
            # （`08_bits_hash` 的 `freq[w] += 1` / `02_csv_stats` 的
            # `freq[parts[0]] += 1`）。键长推不出 ⇒ None（不猜）。
            if op not in ("", "=", "+="):
                eb[root] = None
                return
            key = lhs.children[1] if len(lhs.children or []) > 1 else None
            ln = self._container_expr_size(key)
            if ln is None or eb[root] is None:
                eb[root] = None
            else:
                eb[root] = Cost.max(eb[root], ln)
            return
        # `x[i] = row`：整行替换，长度 ≤ n(row)（行的长度上界）。
        ln = self._container_expr_size(rhs)
        if op not in ("", "=", "+="):
            eb[root] = None
            return
        if op == "+=":
            # 行内追加：`row += other` → 长度 = |row| + |other| ≤ L(x) + n(other)。
            extra = self._container_expr_size(rhs)
            if eb[root] is None or extra is None:
                eb[root] = None
            else:
                eb[root] = Cost.add(eb[root], extra)
            return
        if ln is None or eb[root] is None:
            eb[root] = None
        else:
            eb[root] = Cost.max(eb[root], ln)

    def _elem_scan_branches(self, runs, factor, eb, bounds, env, caps,
                            row_of, const_names, params, fn) -> None:
        base = dict(eb)
        results = []
        for stmts in runs:
            e2 = dict(base)
            self._elem_scan_stmts(stmts, factor, e2, bounds, env, caps,
                                  row_of, const_names, params, fn)
            results.append(e2)
        names = set(base)
        for e2 in results:
            names.update(e2)
        for nm in names:
            vals = [base.get(nm)]
            for e2 in results:
                vals.append(e2.get(nm, base.get(nm)))
            if any(v is None for v in vals):
                eb[nm] = None
            else:
                cur = vals[0]
                for v in vals[1:]:
                    cur = Cost.max(cur, v)
                eb[nm] = cur

    def _elem_scan_expr(self, e, factor, eb, bounds, env, caps, row_of,
                        const_names, params, fn) -> None:
        if e is None:
            return
        k = getattr(e, "kind", "")
        if k == "method-call":
            recv = e.children[0] if (e.children or []) else None
            self._elem_method_effect(e, recv, factor, eb, env, caps, params, fn)
        elif k == "call":
            self._elem_call_effect(e, factor, eb, fn)
        for c in (e.children or []):
            self._elem_scan_expr(c, factor, eb, bounds, env, caps, row_of,
                                 const_names, params, fn)

    def _elem_method_effect(self, e, recv, factor, eb, env, caps, params, fn):
        short = _callee_short(e)
        root, depth = self._index_root(recv)
        if not root or root not in eb:
            return
        args = next((c for c in (e.children or []) if c.kind == "args"), None)
        kids = list(args.children if args is not None else [])
        cur = eb[root]
        if depth >= 1:
            # 接收者是**内层容器**（`g[v]`）：push 让该行 +1。
            if short in _ELEM_PUSH_METHODS or short == "insert":
                _has, _amt = self._method_count_cost(
                    short, self._recv_head(recv), args)
                if not _has:
                    self._grow_elem(eb, root, factor)
                else:
                    # 数量推不出 ⇒ 该行的长度上界退 ⊤（不是 +1）。
                    self._grow_elem(eb, root, factor,
                                    _amt if _amt is not None else Cost.top())
            elif short == "append":
                extra = self._container_expr_size(kids[0]) if kids else None
                if cur is None or factor is None or extra is None:
                    eb[root] = None
                else:
                    eb[root] = Cost.add(cur, Cost.mul(factor, extra))
            elif short in _ELEM_SAFE_METHODS:
                pass
            else:
                eb[root] = None
            return
        # 接收者是**外层容器**（`g`）：入一行 ⇒ L 取 max(旧, 该行长度)。
        if self._elem_len_kind(self._type_of_name_in(fn, root)) == "key":
            # 关联容器的容器键（`m.insert(k, v)` / `m.put(k, v)`）：L 是键长上界。
            if short in ("insert", "put", "emplace", "emplace_hint"):
                ln = self._container_expr_size(kids[0]) if kids else None
                if ln is None or cur is None:
                    eb[root] = None
                else:
                    eb[root] = Cost.max(cur, ln)
            elif short in _ELEM_SAFE_METHODS or short in (
                    "get", "contains", "has", "begin", "end",
                    "keys", "values", "items", "entries"):
                pass                    # 只读 / 不变长
            else:
                eb[root] = None
            return
        if short in _ELEM_PUSH_METHODS or short == "insert":
            ln = self._container_expr_size(kids[0]) if kids else None
            if ln is None or cur is None:
                eb[root] = None
            else:
                eb[root] = Cost.max(cur, ln)
        elif short == "append":
            b = self._elem_expr_bound(kids[0], eb, fn,
                                      self._elem_sym_params(fn)) if kids else None
            if b is None or cur is None:
                eb[root] = None
            else:
                eb[root] = Cost.max(cur, b)
        elif short in _ELEM_SAFE_METHODS:
            pass
        else:
            eb[root] = None

    def _elem_call_effect(self, e, factor, eb, fn) -> None:
        short = _callee_short(e)
        args = next((c for c in (e.children or []) if c.kind == "args"), None)
        kids = list(args.children if args is not None else [])
        callee = self.fns.get(short)
        for _i, a in enumerate(kids):
            root, _d = self._index_root(a)
            if not root or root not in eb:
                continue
            if callee is not None:
                if not self._call_may_grow_arg(short, _i):
                    continue
                g = self._elem_growth_at_call(short, e, _i)
                if g is None or factor is None:
                    eb[root] = None
                elif eb[root] is not None:
                    eb[root] = Cost.add(eb[root], Cost.mul(factor, g))
            else:
                # 认不出的外部调用：可能把行改长 → 不猜。
                if self._call_may_grow_arg(short, _i):
                    eb[root] = None


    def _revoked_size_aliases(self, fn) -> set:
        """**声明即尺寸、之后被改写**的局部名：尺寸别名必须撤销。

        只收「初始值本身就是 `.size()` / `.length()` 调用」的声明——它们正是
        本功能引入的尺寸别名（`u64 m = s.v.size();`）。这类名字被赋值之后，
        声明处的旧尺寸 MUST NOT 再当 `while (i < m)` 的界（2026-11-12）。

        **链式别名不在这里撤销**（`const u64 n = xs.size(); u64 hi = n; hi = mid;`）：
        `hi` 的初值是**另一个别名**，不是尺寸调用。它是既有口径，且
        `hi` 只减不增时初值仍是合法上界（二分查找就靠这条）；把它一起撤掉会让
        `log2(n(xs))` 变成 `log2(hi)` —— 界没变松，但**尺寸量对不上 target**，
        判定从「达成」退成 ⊤。那是把既有的健全路径改坏，不在本次范围。
        """
        if fn is None:
            return set()
        hit = self._revoked_cache.get(id(fn))
        if hit is not None and hit[0] is fn:
            return hit[1]
        written = self._written_names(fn)
        out: set = set()
        for d in (list(_descendants(fn, "const-decl"))
                  + list(_descendants(fn, "var-decl"))):
            nm = getattr(d, "name", None)
            if not nm or nm not in written:
                continue
            if not (getattr(d, "value", None) or {}).get("has_init", True):
                continue
            init = d.children[-1] if len(d.children or []) >= 2 else None
            if init is None or getattr(init, "kind", "") not in (
                    "method-call", "call"):
                continue
            if (init.name or "").split(".")[-1] not in SIZE_FNS:
                continue
            out.add(nm)
        self._revoked_cache[id(fn)] = (fn, out)
        return out

    def _const_names_of(self, fn) -> set:
        """**本函数**里声明为 `const` 的名字（读 `_decl_inits` 的缓存）。"""
        if fn is None:
            return set()
        self._decl_inits(fn)                    # 填缓存
        hit = self._alias_cache.get(id(fn))
        if hit is None or hit[0] is not fn or len(hit) < 3:
            return set()
        return hit[2]

    def _name_count(self, name: str, fn, seen=frozenset()) -> Optional["Cost"]:
        """名字作为**轮数**时的上界；推不出 → `None`（**不猜**）。

        优先级：不可变局部别名（**先查局部**，局部可以遮蔽模块常量）→
        模块级常量 → 标量形参（裸符号，与 target 侧同口径）。
        其余一律 `None`：认不出的局部、被重写过的 `var`、容器名
        （拿容器当区间上界没有意义）。
        """
        if not name:
            return None
        if name in seen:                                # 别名成环：不猜
            return None
        # **被改写过 → 声明处初值不再是轮数**（2026-11-12，P0-1 连带）：
        # 增长预扫是顺序的，`_scan_written` 记着"到这里为止已经赋过值的标量名"。
        # `u64 m = n; m = m * 2; for (i < m) out.push(i)` 的真实轮数是 `2n`，
        # 只按初值推成 `n` 会让 `|out|` 的上界偏小（低估 = 破界）。
        if name in self._scan_written:
            return None
        if fn is not None:
            init = self._decl_inits(fn).get(name)
            if init is not None:
                return self._count_of_expr(init, fn, seen | {name})
        if name in self.const_values:                   # `const N: u64 = 256;`
            return Cost.num(self.const_values[name])
        if fn is not None and name in self._scalar_params_of(fn):
            return Cost("sym", name)
        return None

    def _count_of_expr(self, e, fn, seen=frozenset()) -> Optional["Cost"]:
        """区间上界表达式 → 轮数上界（**只给上界**；认不出 → `None`）。

        只认这几种形状，别的一律 `None`：
          · 整数字面量（`0..1000` 的 1000）；
          · 名字（`n` / `hi` / 模块常量），经 `_name_count` 归一；
          · `x.length()` / `.size()` / `.len()` / `.capacity()` —— **含局部别名**；
          · `log2(x)`；
          · `+` / `*`（两者都可推时相加/相乘）；
          · `-`：无符号减法，`a - b ≤ a`，取 `max(a, b)` 是合法上界；
          · `/`、`%`：`a / k ≤ a`、`a % b ≤ a`，取左操作数。
        """
        if e is None:
            return None
        k = getattr(e, "kind", "")
        while k == "paren" and getattr(e, "children", None):
            e = e.children[0]
            k = getattr(e, "kind", "")
        if k == "lit-int":
            v = _int_literal(e)
            return None if v is None else Cost.num(v)
        if k == "path":
            return self._name_count(e.name or "", fn, seen)
        if k in ("method-call", "call"):
            short = _callee_short(e)
            if short in SIZE_FNS:
                # **字段链也算**（`s.v.size()` → `n(s.v)`，2026-11-12）：
                # 这是"局部尺寸别名"（`u64 m = s.v.size();`）能展开的一步。
                nm = size_receiver_name(e)
                if not nm:
                    # **`g[v].size()`**：元素长度上界 `L(g)`（2026-11-12）。
                    # `u64 m = g[v].size(); while (k < m)` 与直接写
                    # `while (k < g[v].size())` 必须算出**同一个** κ。
                    en = elem_receiver_name(e)
                    if en and self._elem_bound_ok(fn, en):
                        return Cost("sym", f"L({en})")
                    return None
                # E22：定容局部的尺寸是常数（与 `while` 侧 `_substitute_capacity`
                # 同一口径；`for (i in 0..tokens.length())` 不该留局部尺寸名）。
                if nm in self.capacities:
                    return Cost.num(self.capacities[nm])
                # **范围 for 的元素**（`for (const auto& field : fields)`）：
                # `field.size()` 的尺寸量**不是** `n(field)`（`field` 不是这一帧
                # 能写的名字，target 里也写不出来），而是元素长度上界
                # `L(fields) = max_i n(fields[i])`（2026-11-13，判据见
                # `_elem_alias_size_bound`，与 `_container_expr_size` 同一份）。
                _al = self._elem_alias_size_bound(fn, nm)
                if _al is not None:
                    return _al
                return self._size_of_path(nm)
            if short in ("log", "log2"):
                arg = next((c.children[0] for c in (e.children or [])
                            if getattr(c, "kind", "") == "args"
                            and c.children), None)
                inner = self._count_of_expr(arg, fn, seen)
                return None if inner is None else Cost.log2(inner)
            return None
        if k == "binary":
            op = e.value or ""
            a = e.children[0] if (e.children or []) else None
            b = e.children[1] if len(e.children or []) > 1 else None
            if op in ("/", "%", ">>"):
                # **右移也是"只会更小"**（2026-11-12）：`a >> k ≤ a`（无符号）。
                # `<<` **不在此列**（左移会变大）。区间二分的另一种写法
                # `mid = (lo + hi) >> 1` 要靠这一条才和 `/ 2` 得到同一个界
                # （否则 `mid` 在上界里换不出来，被调方的形参名会留在 κ 里）。
                return self._count_of_expr(a, fn, seen)
            la = self._count_of_expr(a, fn, seen)
            lb = self._count_of_expr(b, fn, seen)
            if la is None or lb is None:
                return None
            if op == "+":
                return Cost.add(la, lb)
            if op == "*":
                return Cost.mul(la, lb)
            if op == "-":
                return Cost.max(la, lb)
            return None
        return None

    # ---------------------------------------------------------------- 数量参数
    #
    # **2026-11-13：`insert(pos, count, value)` / `assign(n, v)` / `resize(n)`**
    #
    # `src/interp.py` 这一轮把三参 `insert` 的语义实现了（以前静默丢掉 count），
    # 但成本域仍按「一次 `insert` = +1 个元素」记（`_GROWTH_METHODS` 的注释）——
    # 于是一个**符号化**的 count 在 κ 和"容器尺寸上界"里都变成常数：
    #
    #     vector<u64> xs;  xs.insert(0, n, 7);  return xs.size();   // 真 n 个
    #     κ(修前) = 16（常数，与 n 无关）          ← **上界被破**
    #     for (auto x : xs) …                       ← 循环也按 1 圈算 ⇒ 再破一次
    #
    # 三处消费同一个判据，避免再造一份推导（历史上 `n(n)` 就是这么漂出来的）：
    #   ① `_k_expr_method_call`：**成本**加 `count`（每个元素构造 1 个操作）；
    #   ② `_scan_expr` / `_grow_elem` / `_fg_call`：**尺寸上界**加 `count`；
    #   ③ 数量推不出 ⇒ ①记 ⊤、②界记 None（**都不许当 1**）。

    def _method_count_arg(self, mname: str, head: str,
                          args_node: Optional[Node]) -> Optional[Node]:
        """`insert(pos,count,v)` / `assign(n,v)` / `resize(n)` 的**数量实参**。

        返回 `None` = 这个方法没有独立的数量参数（`push_back` / map 的
        `insert(k,v)` / `put` / `add` / `emplace` …），调用方按「一次 +1」记。
        `insert` 用**实参个数**判重载：map/set 的 `insert` 只有两个实参，
        三参的只可能是序列的 `(pos, count, value)`。
        `assign` / `resize` 只有 `vector` 有；`head == ""`（类型认不出）时
        仍按保守口径收进来——多算一档比漏掉强。
        """
        items = list(args_node.children) if args_node is not None else []
        if mname == "insert" and len(items) >= 3:
            return items[1]
        if mname in ("assign", "resize") and items and head in ("vector", ""):
            return items[0]
        return None

    def _method_count_cost(self, mname: str, head: str,
                           args_node: Optional[Node]):
        """`(有没有数量参数, 数量上界)`；有但推不出 → `(True, None)`。

        `None` 由调用方决定怎么退：κ 那边记 ⊤（`_unknown_size`），
        尺寸上界那边记 `None`（界推不出）。**两处都不许当成 1。**
        """
        node = self._method_count_arg(mname, head, args_node)
        if node is None:
            return False, Cost.num(1)
        return True, self._count_of_expr(node, self._cur_frame_fn())

    def _bound_to_cost_scoped(self, b, fn: Optional[Node] = None
                              ) -> Optional["Cost"]:
        """`bound_to_cost_inst` 的**实例口径**：界里的局部名按初值**结构展开**。

        与模块级 `bound_to_cost_inst` 的唯一区别在 `var` 这一支：

          · **先按初值展开**（`_decl_inits` + `_count_of_expr`，与 `for` 轮数
            **同一份推导**）：`const u64 total = a.length() + b.length();`
            展开成 `n(a) + n(b)`，`const u64 t = x.length() * 2` 展开成
            `2·n(x)`。结构保留，名字不是输入尺寸量的情况一并解决；
          · 模块常量 → `num`；`self.cur_fn` 的标量形参 → `sym`（与旧口径一致，
            **不**因为内联换了帧就把形参改渲染成 `n(名)`）；
          · **`const` 声明了但初值认不出 → `None`（调用方记 ⊤）**。
            MUST NOT 退回 `Cost.size(名字)`：那是把一个查不到的局部当成
            输入尺寸量，求值侧按 1 算，与"折成常数"是同一类低估。
          · 其余（自由名、会被守卫事实 R0 改写的局部计数器）沿用旧口径
            `size(名字)`——`while (j < i)` 的内层界先记 `n(i)`，再由外层
            `while (i < E)` 换成 E。

        为什么不能只用 `bound_to_cost_inst`：界里的 `var` 是**局部别名**时，
        它只能查 `consts`（模块常量）和 `scalars`（形参），查不到就把名字当尺寸量。
        `const u64 total = <尺寸表达式>` 恰好落在这一格——而它的初值在
        `bound_of_condition_wrapper` 的替换表里已经被 `as_cost` 折过一次
        （2026-11-09 前折成 0），到这一步已经没救了。所以两条都要修：
        表里 `total` 不再是 `const 0` 之后，这里才有机会按结构展开。
        """
        if b is None:
            return None
        if fn is None:
            fn = self._fn_stack[-1] if self._fn_stack else self.cur_fn
        # **标量形参按 `cur_fn` 认**（不是 `fn`）：旧口径如此，且内联时
        # `cur_fn` 才是"κ 属于谁"——换成被内联函数的帧会把 `n(x)` 渲染成 `x`，
        # 调用点的尺寸量就对不上了。
        scalars = self._scalar_params_of()

        def _alias(nm: str) -> Optional["Cost"]:
            """局部别名的**初值**（结构展开）；不是不可变局部 → None。"""
            if fn is None:
                return None
            init = self._decl_inits(fn).get(nm)
            if init is None:
                return None
            return self._count_of_expr(init, fn, frozenset({nm}))

        def go(x) -> Optional["Cost"]:
            k = getattr(x, "kind", "")
            if k == "const":
                try:
                    return Cost.num(int(x.value))
                except (TypeError, ValueError):
                    return Cost.num(1)
            if k == "size":
                # **标量成员当循环半径**（`while (row < recv.n_)`）多一条前提：
                # 这个字段在本函数里不许被改写（条件每轮重读它）。不满足 → ⊤。
                # 容器成员（`s.v.size()`）没有这个标签，照旧。
                if FIELD_TAG in (getattr(x, "args", None) or ()) \
                        and not self._field_bound_ok(fn, str(x.value or "")):
                    # **④ 树递归的精确那条**（2026-11-12）：`_field_bound_ok` 的
                    # 判据是"对象动过 ⇒ 所有成员都不许当界"，而递归方法**必然**
                    # 把 `recv` 传给自调用、也常写别的字段（`recv.cols_[c] = 1`）。
                    # 只在「正在算 ④ 的那个函数」这一帧里换成**逐字段**证明
                    # （`_tree_field_ok`）；其它任何路径的行为一个字都不变。
                    if not (self._tree_frame is not None
                            and fn is self._tree_fn
                            and self._tree_field_ok(fn, str(x.value or ""))):
                        return None
                # **范围 for 的元素**（2026-11-13，`11_text.main`）：
                # `for (const auto& field : fields) { … i < field.size() … }`
                # 里 `field.size()` 的尺寸量**不是** `n(field)`——`field` 是
                # 这一帧的元素别名，target 里写不出来（E23 永远判不了），
                # 真值也只是"某一个元素的长度"。换成**元素长度上界**
                # `L(fields) = max_i n(fields[i])`：
                #
                #     n(field) ≤ L(fields)          ← 这一步只会变大
                #     L(fields) ≤ …（`_elem_bounds_full` 的既有推导）…
                #
                # 判据是 `_elem_alias_size_bound`（与 `_container_expr_size`
                # **同一份**，不另造界）：只对"元素带长度"的源容器认
                # （`vector<string>` / `vector<vector<T>>` / map 的键是容器），
                # 标量元素的 `for (const auto& x : xs)` 不认、落回旧口径。
                _al = self._elem_alias_size_bound(fn, str(x.value or ""))
                if _al is not None:
                    return _al
                return Cost.size(x.value)
            if k == "elem":
                # **元素长度上界 `L(g)`**（`g[v].size()`）：多一条前提——
                # 外层容器 `g` 在本帧是 const 形参、且没被写/传出去。
                # 不满足 → ⊤（`L(g)` 就不再是"全程上界"）。
                if ELEM_TAG in (getattr(x, "args", None) or ()) \
                        and not self._elem_bound_ok(fn, str(x.value or "")):
                    return None
                return Cost("sym", f"L({x.value})")
            if k == "var":
                nm = x.value
                if nm in scalars:                       # 标量形参：裸符号
                    return Cost("sym", nm)
                got = _alias(nm)
                if got is not None:
                    return got
                if nm in self.const_values:             # 模块级常量
                    return Cost.num(self.const_values[nm])
                # **`const` 声明的名字但初值认不出**（`const u64 k = xs.at(0);`）：
                # 这一步 MUST 是 ⊤。退回 `size(k)` 就是给一个查不到的局部尺寸量，
                # 求值按 1 算——与"折成常数"是同一类低估。
                if nm in self._const_names_of(fn):
                    return None
                # **repeats 里的裸名字是圈数（标量），不是容器尺寸**（2026-11-09）：
                # `repeats log2(k)` 必须渲染成 `log2(k)`；退回 `size(k)` 会写成
                # `n(k)`（容器尺寸的写法），求值时查不到 k 就按 1 算 ⇒ κ 不是上界。
                if is_count_var(x):
                    return Cost("sym", nm)
                # 其余（自由名 / 会被守卫事实 R0 改写掉的局部计数器）沿用旧口径：
                # `while (j < i)` 的内层界先记 `n(i)`，再由外层 `while (i < E)`
                # 把它换成 E（`_while_inner` 的守卫事实传播）。
                return Cost.size(nm)
            if k == "sub":
                # **`hi - lo`**（2026-11-10）：`while (j < hi)` + `j` 从 `lo` 起只增
                # 时，轮数就是 `hi - lo`。能证明 `lo <= hi`（spec 的 requires）
                # 才把它造成**尺寸量**（符号名 `hi - lo`，与 target 侧同一个）；
                # 证明不了照旧退回 `a - b <= a`（上界，多算）。
                _hi = go(x.left)
                _lo = go(x.right) if x.right is not None else None
                _fn_nm = getattr(fn, "name", "") or ""
                if _hi is not None and _lo is not None:
                    _d = size_diff_cost(_hi, _lo, self._requires_facts(_fn_nm))
                    if _d is not None:
                        self._note_diff_origin(_d, _hi, _lo)
                        return _d
                return go(x.left)          # a - b <= a（u64，b >= 0）
            if k == "add":
                l, r = go(x.left), go(x.right)
                return None if l is None or r is None else Cost.add(l, r)
            if k == "mul":
                l, r = go(x.left), go(x.right)
                return None if l is None or r is None else Cost.mul(l, r)
            if k == "min":
                l, r = go(x.left), go(x.right)
                return None if l is None or r is None else _min_cost(l, r)
            if k == "max":
                l, r = go(x.left), go(x.right)
                return None if l is None or r is None else Cost.max(l, r)
            if k == "log2":
                a = go(x.left)
                return None if a is None else Cost.log2(a)
            if k == "ceildiv":
                # `ceil(e / c)` → `Cost.div(e, c)`。**为什么不是 `mul(1/c)`**：
                # `div` 在 `_profile` / `_monomials` 里被明确支持（正常数分母
                # ⇒ 与分子同阶），而 `mul(1/3)` 会渲染成 `1/3×n(data)`，
                # 把"每轮吃 3 个"读成"系数 1/3"，还要靠 Fraction 兜底。
                # **为什么 div 仍然只是上界**：真圈数 = `floor((e - i0) / c)`
                # ≤ `e / c`（`_exact_step_increment` 已证明初值 0、每轮恰增 c），
                # 所以精确有理数 `e/c` 就是上界；`ceil` 是渲染层的写法。
                a = go(x.left)
                if a is None:
                    return None
                try:
                    _d = int(x.value)
                except (TypeError, ValueError):
                    return None
                if _d < 1:
                    return None
                return Cost.div(a, Cost.num(_d))
            return None

        return go(b)

    def _for_count(self, st: Node) -> Optional["Cost"]:
        """`for` 的**轮数上界**；`None` = 推不出（调用方 MUST 记 ⊤）。

        时间成本（`_for`）与分配成本（`_alloc_stmt`）都调这一份，两维口径就不会
        漂（E36）。`for` 节点的子节点是 `[iterable, block]`，循环变量在 `.name`，
        所以轮数只能从 `for_iterable(st)` 推：

        | 写法 | 轮数 |
        |---|---|
        | `for (auto& x : xs)` / `for (auto x : xs)` | `n(xs)` |
        | `for (const auto& k : m.keys()/values()/items()/entries()/iter())` | `n(m)` |
        | `for (auto x : range(a, b))` | `b - a`（能证明 `a <= b` 时，见 `_range_count`）；否则 `b` |
        | 其它 | `None`（⊤，不猜） |

        **`for (i in a..b)` 已删除**（`..` 区间一并删）：轮数只剩"容器类遍历"
        这一条主路。三段式 `for` 在 parser 里已经 desugar 成 `while`，不走这里。
        """
        it = for_iterable(st)
        if it is None:
            return None
        fn = self._fn_stack[-1] if self._fn_stack else self.cur_fn
        k = getattr(it, "kind", "")
        if k == "path":
            return self._size_of_path(it.name) if getattr(it, "name", "") else None
        if k in ("method-call", "call"):
            short = _callee_short(it)
            if short in ("keys", "values", "items", "entries", "iter"):
                recv = (it.children[0]
                        if (getattr(it, "children", None)
                            and it.children[0].kind == "path") else None)
                return (self._size_of_path(recv.name)
                        if recv is not None and getattr(recv, "name", "") else None)
            if short in ("range", "seq"):
                args = next((c for c in (it.children or [])
                             if getattr(c, "kind", "") == "args"), None)
                if args is not None and args.children:
                    lhs = args.children[0] if len(args.children) > 1 else None
                    return self._range_count(lhs, args.children[-1], fn)
            return None
        return None

    def _range_count(self, lo_node, hi_node, fn) -> Optional["Cost"]:
        """`for (i in a..b)` / `range(a, b)` 的轮数：**`b - a` 优先，退回 `b`**。

        旧口径是 `b`（"无符号 `a ≥ 0` ⇒ `b - a ≤ b`"，仍是上界）。能证明
        `a <= b` 时改用**区间长度** `b - a`——它是精确值，而且与 target 里的
        `hi - lo` **同一个尺寸量**（否则 `for` 写的区间循环配 `O(hi - lo)`
        的 target 会判「尺寸量不一致」）。证明不了照旧退回 `b`（上界，多算）。
        """
        hi_c = self._count_of_expr(hi_node, fn) if hi_node is not None else None
        if hi_c is None:
            return None
        lo_c = self._count_of_expr(lo_node, fn) if lo_node is not None else None
        if lo_c is not None:
            _d = size_diff_cost(hi_c, lo_c,
                                self._requires_facts(getattr(fn, "name", "") or ""))
            if _d is not None:
                self._note_diff_origin(_d, hi_c, lo_c)
                return _d
        return hi_c

    def _for(self, st: Node, env: Dict[str, Cost]) -> Cost:
        body = stmt_body(st)
        cnt = self._for_count(st)
        if cnt is None:
            return Cost.top()
        per = Cost.num(OP_COST * 2)  # 甲.2：+1 绑定
        if body is not None:
            per = Cost.add(per, self._block(body, dict(env)))
        # **A-full #9：按值迭代的循环变量是元素拷贝**（第 7 个拷贝位置，
        # 设计 §4.3 第 5 条）。标量 → 0；**其它一切非标量类别**（owning 容器 /
        # 定长数组 / 结构体）→ 每轮一次拷贝代价：
        #   · `vector<vector<u64>>` 上 `for (auto v : vv)` 每轮拷一个内层，
        #     内层长度**没有符号**（§5.2 D7）→ `_copy_cost(ety, None)` 给 ⊤；
        #   · `vector<array<u64,4>>` 上每轮 4；`vector<S>` 上每轮 |S|。
        # 以前只记 owning 一档，array/struct 的按值迭代**漏记**了元素拷贝——
        # 于是 `auto` 与 `const auto&` 的 κ 一模一样（实测 34n vs 34n）。
        #
        # **引用迭代不拷贝**（2026-11-10 夜）：`auto&` / `const auto&` 绑定的是
        # 元素本身，每轮 O(1)，**no** 元素拷贝项。这正是"引用是 O(n)、按值是
        # O(Σ 元素大小)"的差别——`node.ref` 是唯一判据（parser 写入）。
        it = next((c for c in (st.children or [])
                   if getattr(c, "kind", "") != "block"), None)
        ety = self._iter_elem_type(it)
        if getattr(st, "ref", None) is None and ety is not None \
                and self._copy_class(ety)[0] != "scalar":
            _t, _a = self._copy_cost(ety, None)
            per = Cost.add(per, _t)
        if per.is_top():
            return Cost.top()
        return Cost.mul(cnt, per)

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
        # **A-full #7：聚合初始化逐成员拷贝**（设计 §3.1 第 6 条）。
        # `S s{a}`：字段 owning 且值非 prvalue → 拷一次；`Interval{...}`（u64 字段）
        # 与 `Summary{...}` 都是标量字段 → 0，不影响既有 κ。
        fields = self.structs.get(n.name or "", {})
        for _ch in (n.children or []):
            if getattr(_ch, "kind", "") != "field-init":
                continue
            fty = fields.get(_ch.name or "")
            val = _ch.children[0] if (_ch.children or []) else None
            if fty is None or val is None:
                continue
            if self._copy_class(fty)[0] in ("scalar",):
                continue
            if self._value_class(val) == "prvalue":
                continue
            _t, _a = self._copy_cost(fty, self._size_symbol_of(val))
            _c = Cost.add(_c, _t)
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
    def _k_expr_list_init(self, n, env):
        """`{ e1, ..., en }` —— **n 个元素至少 n 次操作**（κ 必须仍是上界）。

        兜底 `Cost.num(0)` 在这里是**低估**：建一个 n 元素的容器要写 n 次，
        真实机器上不可能凭空出现。少算比多算危险，所以逐元素计满。
        """
        total = Cost.num(OP_COST)
        for c in n.children:
            total = Cost.add(total, self._expr(c, env))
            total = Cost.add(total, Cost.num(OP_COST))
        # **A-full #8**：列表初始化的**元素拷贝**。
        # 元素的静态类型从它自己的声明查（`{a, b}` 里 `a` 是容器 → 拷一份）。
        # 元素是 prvalue（`{1, 2}` / `{S{…}}`）→ 0。
        for c in n.children:
            ety = self._type_of_expr(c)
            if ety is None or self._copy_class(ety)[0] in ("scalar",):
                continue
            if self._value_class(c) == "prvalue":
                continue
            _t, _a = self._copy_cost(ety, self._size_symbol_of(c))
            total = Cost.add(total, _t)
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
        # **容器操作的尺寸项**（2026-11-11，κ 低估审计 ①②）。
        #
        # 修前只有一张「方法名 → log2」的表，而且只查 `self.vty`（局部声明）：
        #   · `find` / `erase` **根本不在表里** ⇒ 真实 O(n) 记成 O(1)（P1/P2）；
        #   · `insert` **不管接收者是谁**都加 log2 ⇒ vector 的 O(n) 搬移被记成
        #     O(log n)（P2，且型参接收者查 `vty` 也查不到）；
        #   · `keys` / `values` 只记 log2 ⇒ 建列表的 O(n) 被记成 O(log n)。
        # 现在按**接收者声明类型**分派：同一个名字在不同容器上有不同的真实阶。
        _mname = (n.name or "").split(".")[-1]
        _recv = n.children[0] if n.children else None
        _head = self._recv_head(_recv)
        _size_term = None
        if _head in ("map", "unordered_map"):
            if _mname in ("get", "contains", "has", "insert", "put",
                          "remove", "erase"):
                # 有序 map：AVL 查找/插入/删除都是最坏 O(log n)；
                # unordered_map：契约是最坏 O(n)。靠声明类型区分。
                _size_term = self._size_term_of_recv(_recv, _head)
            elif _mname in ("keys", "values", "items", "entries"):
                # **建一个 n 元素的新列表是 O(n)**，不是 O(log n)。
                _size_term = self._size_term_of_recv(_recv, "vector")
        elif _head in ("set", "unordered_set"):
            if _mname in ("contains", "has", "insert", "add", "remove", "erase"):
                _size_term = self._size_term_of_recv(_recv, _head)
            elif _mname in ("values", "items", "entries"):
                _size_term = self._size_term_of_recv(_recv, "vector")
        elif _head in ("vector", "array"):
            if _mname in ("find", "insert", "erase"):
                # `find` 扫一遍、`insert`/`erase` 搬后半段：都**是 O(n)**。
                # 序列的 `at` / `pop` / `back` / `front` 仍是 O(1)，不加尺寸项。
                _size_term = self._size_term_of_recv(_recv, _head)
        elif _head == "queue":
            if _mname in ("pop", "dequeue"):
                # 底层是 `list.pop(0)`（搬 n-1 个元素），不是 O(1)。
                _size_term = self._size_term_of_recv(_recv, _head)
        if _size_term is None and _head not in (
                "map", "unordered_map", "set", "unordered_set",
                "vector", "array", "queue"):
            # **接收者静态类型认不出**（`auto m = …` 之类，`--compat` 下真实存在）。
            #
            # 这里**不能**什么都不加：`auto` 的 map 上 `m.insert` 的旧实现记了
            # `log2(n(m))`，一分不加就是**新引入的低估**。所以按旧表的保守口径
            # 兜底（get/insert/contains/remove/keys/values → log2）；旧表里没有
            # 的 `find`/`erase`/`has`/`put`/`items`/`entries` 用 `n`（三者中最大
            # 的阶：序列 O(n)、有序 map O(log n)、unordered O(n)）。
            if _mname in ("get", "insert", "contains", "remove", "keys", "values"):
                _size_term = self._size_term_of_recv(_recv, "map")
            elif _mname in ("find", "erase", "has", "put", "items", "entries"):
                _size_term = self._size_term_of_recv(_recv, "vector")
        if _size_term is not None:
            total = Cost.add(total, _size_term)
        # **容器键的哈希长度项**（2026-11-12）：`unordered_*` 的 get/insert/…
        # 对 `vector` / `array` 键要读完整键，κ 加 `n(键表达式)`。
        if _mname in ("get", "contains", "has", "insert", "put",
                      "remove", "erase"):
            _first = (args_node.children[0]
                      if args_node is not None and args_node.children else None)
            _kc = self._hash_len_term(_first, _recv, _head)
            if _kc is not None:
                total = Cost.add(total, _kc)
        # **一次调用新增多少元素**（2026-11-13）——**带数量参数的增长方法**。
        #
        # 旧口径是「一次 `insert` = +1 个元素」（`_GROWTH_METHODS` 的注释），
        # 对三参 `insert(pos, count, value)` 只在**数量是常数**时碰巧成立：
        #   `xs.insert(0, n, 7)` 的 count 是符号量 ⇒ κ 恒为常数 16，
        #   而真实是 **n 次拷贝构造**（n=256 时 256 > 16）⇒ **上界被破**（低估）。
        # 这里把数量实参折成 κ 里的一项：**元素逐个构造 = 每个 1 个操作**。
        #
        # 判据（`_count_of_expr`，与 `for` 轮数 / `assign` 的增长预扫同一份）：
        #   · 字面量 / 形参 / `xs.size()` / `+ - * / % >>` 都认；
        #   · **推不出 ⇒ ⊤**：MUST NOT 当 1 —— "数量判不出就按 1 记"正是这次
        #     要堵的低估（理由与 `_unknown_size` 的其它用法一致）。
        # 三参 `insert` 是**序列**重载（map/set 的 insert 只有两个实参），
        # 所以不需要先看接收者类型；`assign` / `resize` 只在 vector 上有。
        _cnt = None
        _has_cnt, _cnt_val = self._method_count_cost(_mname, _head, args_node)
        if _has_cnt:
            _cnt_node = self._method_count_arg(_mname, _head, args_node)
            _cnt = _cnt_val
            if _cnt is None:
                _cnt = self._unknown_size(
                    f"`{_mname}(…)` 的元素个数 `{_expr_text(_cnt_node)}` 推不出"
                    f" ⇒ 这一项退 ⊤，不猜（按 1 记会**低估**）")
            total = Cost.add(total, _cnt)
        for c in n.children:
            if c.kind == "args":
                continue        # 实参节点本身不计费（解释器也不求值它），逐个实参上面已计
            total = Cost.add(total, self._expr(c, env))
        # **A-full #6**：入容器的**元素拷贝**（`push` / `insert` / …）。
        # 元素是 owning 容器且实参是左值 → 拷一次；prvalue → 移动（0）。
        # **数量参数（2026-11-13）**：`insert(pos, count, v)` / `assign(n, v)` 是
        # **count 次**拷贝，不是一次 ⇒ 单次拷贝要乘上数量（标量元素 `_st=0`，
        # 乘了也是 0，既有 κ 一个字节不变）。
        _st, _sa = self._store_copy_cost(n)
        if _cnt is not None and not (_st.is_const() and int(_st.value) == 0):
            total = Cost.add(total, Cost.mul(_cnt, _st))
        elif _cnt is None:
            total = Cost.add(total, _st)
        return total
    def _k_expr_cast_static(self, n, env):
        """**未折叠的 `static_cast`**（P0 兼容层第 11 条）→ **⊤**，不猜一个数。

        能证明"值逐位不变"的 `static_cast<unsigned char|u64>(x)` 在解析/类型
        检查期就折成 `x` 了（见 `typecheck.resolve_static_cast`），不会走到这里。
        走到这里说明它是一次**真转换**（窄化 / 有符号 / 类型推不出）：那种
        转换会改值，我们拒绝把它记成恒等，也拒绝编一个"看起来合理"的 κ。
        """
        return Cost.top()

    def _k_expr_paren(self, n, env):
        # **`(e)` 自己也是一个表达式节点，必须记 1**（2026-11-09）。
        #
        # 修前这里**只返回里面的成本**（`return self._expr(n.children[0], env)`），
        # 等于让括号免费。而解释器 `_eval` 对**每个非 None 节点**记 1，
        # 包括 `paren` 包裹（`interp.py` 的单位定义：
        # 「每个表达式节点求值一次，含**叶子**与 `paren` 包裹」）。
        # 于是每一层括号在 κ 里少 1 —— 差额**恰好等于括号节点数**：
        #   `rotr(x,n) = (x >> n) | (x << (32 - n))`   κ=10 / ops=13（3 个括号）
        #   `ch`   κ=9  / ops=11（2 个）
        #   `maj`  κ=12 / ops=15（3 个）
        #   `big_sigma0`  κ=42 / ops=51（3 次调用 × 每次 3 个）
        #   `small_sigma0` κ=32 / ops=39（2×3 + 1）
        # 大函数里被常数余量盖住，**只有把叶子单独测才看得见**——
        # 所以这条违反了「κ 是上界」，而且调用方会按被调函数的 κ 记账，
        # 少算会沿调用链一路传下去（`just_call` κ=14 / ops=17）。
        #
        # 修法：给括号节点补记 `OP_COST`，**κ 只增不减**。
        # 没有改解释器计数器——它数的就是 AST 节点，改它等于重新定义单位，
        # 还会让所有历史 ops 一起变（`test_units.py` 把单位钉死了）。
        if n.children:
            return Cost.add(Cost.num(OP_COST), self._expr(n.children[0], env))
        return Cost.num(OP_COST)
    def _k_expr_ternary(self, n, env):
        """**三目 `c ? a : b`**（2026-11-13）的 κ。

        ```
        κ = 1 + κ(cond) + **max**(κ(a), κ(b))
        ```

        ## 为什么是 max，不是 `+`

        三目**只执行一支**：两支是**择一**关系，不是先后关系。所以：

        · `max` 是**上界**（真实执行的那一支必定 ≤ 两支的较大者）；
        · 相加**也是**上界，但那个界在说"两支都跑过"——一句话就把成本翻倍，
          读者据此判断"三目很贵"是**错的**（`tool.md` 的紧度口径也要求
          κ 尽量贴住真实执行，不能靠多记来保安全）。
        · 条件本身照算：它**一定**求值，且 `_eval` 对每个非 None 节点记 1，
          所以节点自己那 1 个操作加上 `κ(cond)`。

        ## 两支的绑定表

        与 `if` 语句同一个口径（`_merge_env_max`）：分支里可能给局部容器
        记了尺寸上界（`xs.push_back(...)`），两条路只走一条，所以合并取 max。
        不合并会让"三目里 push 过"的界整个丢掉 ⇒ κ 变小 ⇒ 破界。
        """
        total = Cost.num(OP_COST)
        if n.children:
            total = Cost.add(total, self._expr(n.children[0], env))
        before = dict(env)
        envs, costs = [], []
        for c in (n.children[1:3] if len(n.children) >= 3
                  else list(n.children[1:])):
            e2 = dict(env)
            costs.append(self._expr(c, e2))
            envs.append(e2)
        worst = costs[0] if costs else Cost.num(0)
        for x in costs[1:]:
            worst = Cost.max(worst, x)
        self._merge_env_max(env, before, envs)
        return Cost.add(total, worst)
    def _k_expr_lit_int(self, n, env):
        # **甲（E24）**：叶子也要计数。
        # `operations` 的单位是「语句 + 表达式节点（含叶子）+ 循环轮数」（见 `test_units.py`）；
        # 这里原先走兜底 `Cost.num(0)`，叶子免费 ⇒ κ 比实际步数**小**
        # （实测 `paren` 差 2.00 倍、`lcs` 差 4.96 倍）。
        return Cost.num(OP_COST)
    def _k_expr_index(self, n, env):
        # **下标读不是常数**：`m[k]` 在有序 map/set 上是 AVL 查找
        # `O(log n)`，`unordered_*` 的契约是最坏 `O(n)`。
        # 以前这里只有「节点 + 接收者 + 下标」三个常数 ⇒ **低估**（审计 P4b）。
        # 序列的 `xs[i]` 仍是 O(1)，不加尺寸项。
        total = Cost.num(OP_COST)
        _recv = n.children[0] if n.children else None
        _head = self._recv_head(_recv)
        for c in n.children:
            total = Cost.add(total, self._expr(c, env))
        if _head in ("map", "set", "unordered_map", "unordered_set"):
            _sz = self._size_term_of_recv(_recv, _head)
            if _sz is not None:
                total = Cost.add(total, _sz)
            # **容器键的哈希长度项**（`unordered_*`；标量键 → None）。
            _key = n.children[1] if len(n.children) > 1 else None
            _kc = self._hash_len_term(_key, _recv, _head)
            if _kc is not None:
                total = Cost.add(total, _kc)
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


    def _receiver_size_renames(self, fn: Optional[Node], call: Optional[Node],
                               cost: Optional["Cost"]) -> Tuple[Dict[str, Cost], bool]:
        """**成员尺寸符号是对象相对的**：`n(recv.cols_)` → 调用点的 `n(solver.cols_)`。

        `--compat` 把 `class` 降级成"第一个形参是对象"的自由函数
        （`QueenSolver_render(recv)`）。它的 κ 天然用 **`recv`** 写成员尺寸
        （`n(recv.n_)` / `n(recv.first_)`）。调用点 `solver.render()` 上那个
        对象叫 `solver` —— 不换的话，调用方的 κ 里会留着一个**被调方的形参名**
        （`n(recv.first_)`），求值时查不到 ⇒ 按 1 算 ⇒ **低估**。
        这与 `_param_size_table` 是同一件事（E19），只是粒度到字段。

        返回 `(表, 是否有换不掉的名字)`：
          · 表：`"recv.n_" → size("solver.n_")`；
          · 实参不是**纯字段链**（`make().render()`）⇒ 换不掉，`True`；
            调用方必须记 ⊤ —— 留一个查不到的名字比 ⊤ 更糟。
        """
        if fn is None or call is None or cost is None:
            return {}, False
        vars_ = set(cost.variables())
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        anode = next((c for c in (call.children or []) if c.kind == "args"), None)
        ps = pnode.children if pnode is not None else []
        args = anode.children if anode is not None else []
        out: Dict[str, Cost] = {}
        leak = False
        for i, p in enumerate(ps):
            nm = getattr(p, "name", "") or ""
            if not nm:
                continue
            ty = p.children[0] if (p.children or []) else None
            # 容器形参的 `n(名字)` 由 `_param_size_table` 负责，这里只管**对象**
            # （struct / class）的成员。标量形参不会产出 `名字.` 前缀，跳过无妨。
            if _container_type_name(ty) is not None:
                continue
            pre = nm + "."
            hits = [v for v in vars_ if v.startswith(pre)]
            if not hits:
                continue
            base = (field_chain_name(args[i]) if i < len(args) else None)
            if base:
                for v in hits:
                    out[v] = Cost.size(base + v[len(nm):])
                continue
            # **实参是"刚由函数返回的结构体"**（①，2026-11-12）：`count_bits(xor_bits(…))`
            # 里 `b` 绑到的是 `xor_bits` 的**返回值**，没有调用方名字可写——用被调
            # 函数的**返回字段尺寸**（`_ret_field_size_of`，界来自 `filled(nbits/64)`
            # 或出参增长），它在构造时就已经用**本帧**的符号写好了。
            arg = args[i] if i < len(args) else None
            derived = False
            if arg is not None and getattr(arg, "kind", "") in ("call", "method-call"):
                short = _callee_short(arg)
                callee = self.fns.get(short)
                if (callee is not None
                        and self._struct_of_type(_ret_type_of(callee))
                        == self._struct_of_type(ty)):
                    derived = True
                    for v in hits:
                        fld = v[len(pre):]
                        if not fld or "." in fld:
                            leak = True
                            continue
                        b = self._ret_field_size_of(short, fld, arg)
                        if b is None:
                            leak = True
                        else:
                            out[v] = b
            if derived:
                continue
            leak = True
        return out, leak

    # ---------------------------------------------------------------- 结构体返回值的**字段**尺寸（①）
    #
    # **问题**（2026-11-12，语料 `16_bitset.main`）：
    #
    #     static Bits xor_bits(const Bits& a, const Bits& b) { Bits out; …; return out; }
    #     count_bits(xor_bits(monday, tuesday));      // ← κ = ⊤
    #
    # `count_bits` 的 κ 用 `n(b.words)` 写（`b` 是 **struct** 形参），因此
    # `_param_size_table` 不管它（那不是容器形参），只能靠 `_receiver_size_renames`
    # 在调用点换名字。而那里**只认实参是纯字段链**（`count_bits(seen)` →
    # `n(seen.words)`）；实参是**函数调用**（`xor_bits(…)`）就只能判 ⊤——整个
    # `main` 跟着 ⊤，哪怕 `16_bitset` 的另外 10 个函数都能算。
    #
    # **口径**：与 `_ret_size_of`（返回**容器**的长度）同一条思路，粒度到字段：
    #
    #   · `return out;`（局部结构体）⇒ 字段界 = **初始界**（声明处的初值，没写就是
    #     默认构造 = 空）+ 函数体里对 `out.<字段>` 的**插入次数上界**；
    #   · 插入只认能证明的形状（见 `_fg_stmt`）：`x.f.push_back(…)` 按**轮数**累加、
    #     下标赋值只对 map 算一次插入、把 `x` 传出给非 const 引用形参时按被调函数的
    #     同一份扫描再加；**有一个说不清就 ⊤**（`None`）；
    #   · 调用点上再按实参把形参名换掉：容器走 `_param_size_table`，标量走
    #     `_subst_scalar_syms`，结构体字段走 `_receiver_size_renames`。
    #
    # **方向**：只可能把 ⊤ 换成"具体但偏松"的界（例如 `(nbits+63)` 而不是精确的
    # `ceil((nbits+63)/64)`），**从不**产生比真实更小的上界。

    #: 只读的结构体字段容器方法（不动长度）。用一张**比 `_SAFE_METHODS` 更窄**的表：
    #: `pop/erase/remove/clear` 在 `_SAFE_METHODS` 里算"安全"，但它们会**变短**——
    #: 对"长度上界"来说变短也安全，可对**访问标记**（②的 visited 守卫）不成立。
    _FIELD_READ_METHODS = ("size", "length", "len", "capacity", "empty",
                           "is_empty", "at", "index", "front", "back", "get",
                           "find", "count", "contains", "has", "contains_key",
                           "peek", "top", "first", "last")

    def _struct_of_type(self, ty: Optional[Node]) -> Optional[str]:
        """类型节点 → `self.structs` 里的结构体名；不是结构体 → `None`。"""
        base = self._unwrap_type(ty)
        nm = getattr(base, "name", "") or ""
        return nm if nm in self.structs else None

    def _struct_field_head(self, sname: str, field: str) -> Optional[str]:
        """结构体字段的**容器种类**（`vector` / `map` / `array`…）；不是容器 → None。"""
        ty = (self.structs.get(sname) or {}).get(field)
        return _container_type_name(ty)

    def _struct_container_fields(self, sname: str) -> List[str]:
        """结构体里所有**容器类型**的字段名（排序后，输出稳定）。"""
        out: List[str] = []
        for f in sorted((self.structs.get(sname) or {})):
            if self._struct_field_head(sname, f) is not None:
                out.append(f)
        return out

    def _field_growth(self, fn: Node, base: str, field: str,
                      sname: str, head: str,
                      visiting: Optional[set] = None) -> Optional["Cost"]:
        """`fn` **一次执行**里往 `base.<field>` 插入的元素个数上界（从 0 起算）。

        返回 `Cost.num(0)` = **可证"从不写长"**（`_scan_expr` 靠这条保留原界）；
        `None` = 推不出（调用方必须把界抹成 ⊤）。**只许是上界**。
        """
        key = (id(fn), base, field)
        seen = set(visiting or ())
        if key in seen or key in self._field_busy:
            return None                     # 增长互相依赖成环：不猜
        seen.add(key)
        body = next((c for c in (fn.children or []) if c.kind == "block"), None)
        if body is None:
            return Cost.num(0)
        # 轮数推导要按**被扫的这个函数**查局部别名/标量形参（`_bound_to_cost_scoped`
        # 读 `cur_fn`）：本方法可能在调用者的帧里被触发。
        _prev = self.cur_fn
        self.cur_fn = fn
        self._field_busy.add(key)
        try:
            return self._fg_stmts(body.children, Cost.num(1), fn, base, field,
                                  sname, head, seen)
        finally:
            self._field_busy.discard(key)
            self.cur_fn = _prev

    def _fg_stmts(self, stmts, factor, fn, base, field, sname, head, seen):
        """语句序列的插入次数上界（顺序相加）。"""
        total = Cost.num(0)
        for st in (stmts or []):
            g = self._fg_stmt(st, factor, fn, base, field, sname, head, seen)
            if g is None:
                return None
            total = Cost.add(total, g)
        return total

    def _fg_expr(self, e, factor, fn, base, field, sname, head, seen):
        """表达式里有没有**插入动作**（只可能是调用/方法调用）。"""
        if e is None:
            return Cost.num(0)
        k = getattr(e, "kind", "")
        if k in ("call", "method-call"):
            return self._fg_call(e, factor, fn, base, field, sname, head, seen)
        total = Cost.num(0)
        for c in (getattr(e, "children", None) or []):
            g = self._fg_expr(c, factor, fn, base, field, sname, head, seen)
            if g is None:
                return None
            total = Cost.add(total, g)
        return total

    def _fg_call(self, e, factor, fn, base, field, sname, head, seen):
        """一个调用点对 `base.<field>` 的插入次数上界（**含被调函数**）。"""
        short = _callee_short(e)
        kids = list(getattr(e, "children", None) or [])
        recv = kids[0] if kids else None
        args = next((c for c in kids if getattr(c, "kind", "") == "args"), None)
        tgt = f"{base}.{field}"
        if getattr(e, "kind", "") == "method-call":
            rn = field_chain_name(recv) if recv is not None else None
            if rn == tgt:
                if short in _GROWTH_METHODS:
                    # **数量参数**（2026-11-13）：三参 `insert(pos,count,v)`
                    # 一次加 `count` 个（与 `_scan_expr` / `_grow_elem` 同一判据）。
                    _has, _amt = self._method_count_cost(short, head, args)
                    if not _has:
                        return factor if factor is not None else None
                    if factor is None or _amt is None or _amt.is_top():
                        return None
                    return Cost.mul(factor, _amt)
                if short in ("assign", "resize"):
                    # **`c.assign(n, v)` / `c.resize(n)`：长度被设成 n**（不是 +1）。
                    # 2026-11-12（`10_backtrack` 的构造 `first_.assign(n, 0)`）：
                    # 以前这一支落到 `return None` ⇒ `n(solver.first_)` 没有界
                    # ⇒ `first_solution()` 的返回尺寸推不出 ⇒ `main` 的 κ 只能报
                    # "局部容器无法在 target 中引用"。从 0 起算的"插入个数" ≤ n
                    # 就是它的上界；外层循环 factor 次（每轮重新 assign 成 n）
                    # 也只多算，方向安全。计数推不出 → None（不猜）。
                    _a0 = (args.children[0]
                           if args is not None and args.children else None)
                    cnt = (self._count_of_expr(_a0, fn)
                           if _a0 is not None else None)
                    if cnt is None or cnt.is_top():
                        return None
                    return cnt if factor is None else Cost.mul(factor, cnt)
                if short in self._FIELD_READ_METHODS:
                    return Cost.num(0)
                return None
            if rn == base:
                return None                 # 整个结构体上的方法：说不清
        total = Cost.num(0)
        for i, a in enumerate(getattr(args, "children", None) or []):
            an = field_chain_name(a)
            if not an or not (an == base or an.startswith(base + ".")):
                continue                    # 实参不涉及被跟踪的对象
            if getattr(e, "kind", "") != "call":
                return None
            if an == tgt:
                # 把**字段容器本身**传出去：非 const 引用才可能改长度。
                if self._call_may_grow_arg(short, i):
                    return None
                continue
            if an != base:
                # **只读同结构体的别的字段**（`printf(…, heap.size)`）改不到
                # `heap.data` 的长度；但那个字段本身是**容器**时，它可能被换掉
                # / 与目标共有底层存储 ⇒ 不猜。
                _leaf = an[len(base) + 1:].split(".", 1)[0]
                if self._struct_field_head(sname, _leaf) is None:
                    continue
                return None
            # 把**整个结构体**传出去：看被调函数对应形参能不能写它。
            callee = self.fns.get(short)
            p = self._param_at(callee, i) if callee is not None else None
            if p is None or not getattr(p, "name", ""):
                return None                 # 认不出被调方：按"可能写长"处理
            ty = p.children[0] if (p.children or []) else None
            if not _is_ref_type(ty) or (getattr(p, "value", None) or {}).get("const"):
                continue                    # 按值 = 一份拷贝；const& = 只读借用
            sn2 = self._struct_of_type(ty)
            if sn2 is None:
                return None
            hd2 = self._struct_field_head(sn2, field)
            if hd2 is None:
                return None                 # 那个形参没有这个字段 / 不是容器
            if callee is fn and self._field_length_invariant(
                    fn, p.name, field, hd2):
                # **自调用**：增长分析正在算同一个 `(函数, 形参, 字段)`，递归
                # 进去必然撞防环保护。若本函数对那个字段只有下标读/写，则
                # 自调用也不改长度 ⇒ 贡献 0（`10_backtrack` 的 `place`）。
                continue
            g = self._field_growth(callee, p.name, field, sn2, hd2, seen)
            if g is None:
                return None
            g = self._instantiate_callee_cost(g, callee, e, caller_fn=fn)
            if g is None:
                return None
            if factor is None:
                return Cost.num(0) if g.is_const() and int(g.value) == 0 else None
            total = Cost.add(total, Cost.mul(factor, g))
        return total

    def _field_length_invariant(self, fn, base: str, field: str, head: str) -> bool:
        """`base.field` 的长度在**本函数（含自调用）**里从不改变。

        用途只有一个（2026-11-12，`10_backtrack`）：`_field_growth` 扫到
        「把整个结构体传给自己」时，会递归进同一个 `(函数, 形参, 字段)` 并撞上
        防环保护 ⇒ None。可如果这个函数对那个字段**只有下标读/写**（vector 的
        `x.f[i] = v` 不改长度），那自调用也不会改它 —— 这一支就能给 0。

        判据（缺一即 False，调用方退回 None）：
          · 对 `base.field` / `base` 没有整体赋值（`x.f = …` / `x = …`）；
          · 对 `base.field[i]` 的写只有**序列**（`vector` / `array`）——map/set 的
            下标写是插入，长度会变；
          · 没有 `push*` / `insert` / `assign` / `resize` 这类会长的方法；
          · 不把 `base` / `base.field` 传给**别的函数**（自调用除外，那是归纳）。
        """
        tgt = base + "." + field
        own = getattr(fn, "name", "") or ""

        def _strip(x):
            while getattr(x, "kind", "") in ("paren", "ref", "deref"):
                x = x.children[0] if getattr(x, "children", None) else None
            return x

        for a in (list(_descendants(fn, "assign"))
                  + list(_descendants(fn, "assign-op"))):
            if not (a.children or []):
                continue
            lhs = _strip(a.children[0])
            if lhs is None:
                continue
            if getattr(lhs, "kind", "") == "index":
                cont = lhs.children[0] if lhs.children else None
                ch = field_chain_name(cont) if cont is not None else None
                if ch == tgt:
                    if head in ("map", "unordered_map", "set", "unordered_set"):
                        return False        # 下标写 = 插入
                    continue                # 序列：只改已有元素
                if ch is not None and ch.startswith(tgt + "."):
                    return False            # 写子字段：可能整体换掉
                continue
            ch = field_chain_name(lhs)
            if ch is None:
                continue
            if ch == tgt or ch == base or ch.startswith(tgt + "."):
                return False
            if tgt.startswith(ch + "."):
                return False                # 写上层字段也可能换掉它
        for c in _descendants(fn, "method-call"):
            recv = c.children[0] if (c.children or []) else None
            rn = field_chain_name(recv) if recv is not None else None
            if not rn:
                continue
            if rn == base:
                # **整个对象上的方法**（`x.reset()` / `x.clear()`）：说不清会不会
                # 换掉 `x.field`，按"可能长"处理（与 `_fg_call` 同一口径）。
                return False
            if not (rn == tgt or rn.startswith(tgt + ".")
                    or tgt.startswith(rn + ".")):
                continue
            short = _callee_short(c)
            if short in _GROWTH_METHODS or short in ("assign", "resize"):
                return False
        for c in (list(_descendants(fn, "call"))
                  + list(_descendants(fn, "method-call"))):
            args = next((x for x in (c.children or [])
                         if getattr(x, "kind", "") == "args"), None)
            for a in (args.children if args is not None else []):
                an = field_chain_name(a)
                if not an:
                    continue
                if not (an == base or an.startswith(base + ".")):
                    continue
                # 传给**别的**函数：那边可能长它（自调用按归纳假设放行）
                if getattr(c, "kind", "") == "call" and _callee_short(c) == own:
                    continue
                return False
        return True

    def _fg_stmt(self, st, factor, fn, base, field, sname, head, seen):
        """一条语句的插入次数上界；`None` = 推不出。"""
        k = getattr(st, "kind", "")
        tgt = f"{base}.{field}"

        if k in ("const-decl", "var-decl"):
            # 声明本身不插入；初值由**初始界**（`_struct_init_field_bound`）负责，
            # 这里只扫初值里的调用（`Bits x = f();` 的插入发生在 f 里）。
            if len(st.children or []) < 2:
                return Cost.num(0)
            return self._fg_expr(st.children[-1], factor, fn, base, field,
                                 sname, head, seen)

        if k in ("assign", "assign-op"):
            lhs = st.children[0] if st.children else None
            rhs = st.children[1] if len(st.children or []) > 1 else None
            chain = field_chain_name(lhs) if lhs is not None else None
            if chain == tgt:
                return None                 # 整个字段容器被换掉：长度不可知
            if chain == base:
                return None                 # 整个结构体被换掉
            if (lhs is not None and getattr(lhs, "kind", "") == "index"
                    and lhs.children
                    and field_chain_name(lhs.children[0]) == tgt):
                # `x.f[k] = v`：map 是插入，vector 只改已存在的元素。
                if head in ("map", "unordered_map"):
                    ins = factor if factor is not None else None
                else:
                    ins = Cost.num(0)
                if ins is None:
                    return None
                g = self._fg_expr(rhs, factor, fn, base, field, sname, head, seen)
                return None if g is None else Cost.add(ins, g)
            g = self._fg_expr(rhs, factor, fn, base, field, sname, head, seen)
            return g

        if k == "if":
            cond = st.children[0] if st.children else None
            cg = self._fg_expr(cond, factor, fn, base, field, sname, head, seen)
            if cg is None:
                return None
            worst = Cost.num(0)              # 存在"整条 if 都不进"的路径
            for b in (st.children or [])[1:]:
                g = self._fg_stmt(b, factor, fn, base, field, sname, head, seen)
                if g is None:
                    return None
                worst = Cost.max(worst, g)
            return Cost.add(cg, worst)

        if k in ("while", "for"):
            cond = st.children[0] if st.children else None
            cg = self._fg_expr(cond, factor, fn, base, field, sname, head, seen)
            if cg is None:
                return None
            lb = self._fg_loop_bound(st, fn)
            f2 = None if (factor is None or lb is None) else Cost.mul(factor, lb)
            body = next((c for c in (st.children or [])[1:]
                         if getattr(c, "kind", "") == "block"), None)
            if body is None:
                return cg
            g = self._fg_stmts(body.children, f2, fn, base, field, sname, head, seen)
            return None if g is None else Cost.add(cg, g)

        if k in ("return", "expr-stmt"):
            g = self._fg_expr(st.children[0] if st.children else None,
                              factor, fn, base, field, sname, head, seen)
            return g

        if k == "block":
            return self._fg_stmts(st.children, factor, fn, base, field,
                                  sname, head, seen)

        # 其余语句（`match` / `parallel` …）：保守地按子节点累加。
        total = Cost.num(0)
        for c in (st.children or []):
            g = self._fg_stmt(c, factor, fn, base, field, sname, head, seen)
            if g is None:
                return None
            total = Cost.add(total, g)
        return total

    def _fg_loop_bound(self, st, fn) -> Optional["Cost"]:
        """插入预扫用的**轮数**上界（与 `_scan_growth_stmt` 同一份推导）。"""
        k = getattr(st, "kind", "")
        if k == "for":
            return self._for_iter_bound(st, {}, {}, ())
        b, _why = bound_of_condition_wrapper(
            st, self.repeats.get, self._shape_literal_env(fn), self.row_of,
            self.capacities, self._const_names_of(fn), fn,
            resolve=lambda nm: self._name_count(nm, fn),
            size_alias_skip=self._revoked_size_aliases(fn))
        if b is None:
            return None
        return self._resolve_local_cost(
            self._bound_to_cost_scoped(b, fn), {}, {})

    def _struct_init_field_bound(self, e, field: str, fn: Node,
                                 sname: Optional[str] = None
                                 ) -> Optional["Cost"]:
        """结构体表达式 `e` 的字段容器长度上界（**当前帧**的符号）；推不出 → None。"""
        while e is not None and getattr(e, "kind", "") in ("ref", "paren",
                                                          "cast", "try"):
            e = e.children[0] if e.children else None
        if e is None:
            return None
        k = getattr(e, "kind", "")
        if k in ("call", "method-call"):
            short = _callee_short(e)
            callee = self.fns.get(short)
            if callee is not None:
                return self._ret_field_size_of(short, field, e)
            return None
        if k == "field" and e.children:
            # `bits.words` 这种：交给外层的尺寸推导（不在这里猜）。
            return None
        return None

    def _struct_local_field_bound(self, fn: Node, base: str, field: str,
                                  sname: str) -> Optional["Cost"]:
        """`n(base.<field>)` 在 `fn` 执行完时的上界（**fn 的符号**）。"""
        _prev = self.cur_fn
        self.cur_fn = fn
        self._fn_stack.append(fn)
        try:
            return self._struct_local_field_bound_inner(fn, base, field, sname)
        finally:
            self._fn_stack.pop()
            self.cur_fn = _prev

    def _struct_local_field_bound_inner(self, fn: Node, base: str, field: str,
                                        sname: str) -> Optional["Cost"]:
        decl = None
        for d in (list(_descendants(fn, "const-decl"))
                  + list(_descendants(fn, "var-decl"))):
            if getattr(d, "name", "") == base:
                decl = d
                break
        if decl is None:
            return None                     # 不是本帧的局部：不猜
        has_init = (getattr(decl, "value", None) or {}).get(
            "has_init", len(decl.children or []) > 1)
        base_cost = Cost.num(0)             # `Bits out;` = 默认构造 = 空
        if has_init and len(decl.children or []) >= 2:
            base_cost = self._struct_init_field_bound(decl.children[-1], field,
                                                      fn, sname)
            if base_cost is None:
                return None
        head = self._struct_field_head(sname, field)
        if head is None:
            return None
        growth = self._field_growth(fn, base, field, sname, head)
        if growth is None:
            return None
        return Cost.add(base_cost, growth)

    def _ret_field_size_of(self, name: str, field: str,
                           call_node: Optional[Node]) -> Optional["Cost"]:
        """用户函数 `name(...)` **返回的结构体**里字段 `field` 的长度上界（调用方符号）。

        与 `_ret_size_of`（返回容器）对偶：那边看 `return <容器>`，这边看
        `return <局部结构体>` 里那个**字段容器**。推不出 → `None`（调用方记 ⊤）。
        """
        fn = self.fns.get(name)
        if fn is None:
            return None
        sname = self._struct_of_type(_ret_type_of(fn))
        if sname is None or field not in (self.structs.get(sname) or {}):
            return None
        if self._struct_field_head(sname, field) is None:
            return None                     # 返回的不是结构体 / 没有这个容器字段
        key = (name, field)
        if key in self._field_size_chain:
            return None                     # 字段尺寸互相依赖成环：不猜
        self._field_size_chain.append(key)
        try:
            body = next((c for c in (fn.children or []) if c.kind == "block"), None)
            if body is None:
                return None
            # 所有出口都必须返回**同一个局部结构体**（`return out;`）。
            locals_ = set()
            for r in _descendants(body, "return"):
                e = r.children[0] if r.children else None
                while e is not None and getattr(e, "kind", "") in ("ref", "paren"):
                    e = e.children[0] if e.children else None
                if (e is None or getattr(e, "kind", "") != "path"
                        or not getattr(e, "name", "")):
                    return None
                locals_.add(e.name)
            if len(locals_) != 1:
                return None
            total = self._struct_local_field_bound(fn, locals_.pop(), field, sname)
            if total is None:
                return None
            return self._instantiate_callee_cost(total, fn, call_node)
        finally:
            self._field_size_chain.pop()

    def _instantiate_callee_cost(self, cost: "Cost", fn: Node,
                                 call: Optional[Node],
                                 caller_fn: Optional[Node] = None
                                 ) -> Optional["Cost"]:
        """把**被调函数符号**写成的成本式在调用点实例化；换不干净 → None。

        四张表依次过（与 `_callee_kappa` 里的顺序一致）：容器形参 → 结构体字段
        → `L(形参)` → 标量形参。任何一项换不掉都退 ⊤，绝不把被调方的名字留在
        调用方的 κ 里（`n(b.words)` 在 `main` 里求值会被当成 1 ⇒ 低估）。

        `caller_fn`：**调用方**那一帧（表里的实参尺寸要用它的符号写）。默认取
        `_cur_frame_fn()`；在增长预扫里由调用方显式给出，避免拿错帧。
        """
        if cost is None:
            return None
        if call is None:
            return cost                     # 没有调用点：保留形参名（被调函数自己的 κ）
        caller = caller_fn if caller_fn is not None else self._cur_frame_fn()
        _prev = self.cur_fn
        if caller is not None:
            self.cur_fn = caller
            self._fn_stack.append(caller)
        try:
            tab = self._param_size_table(fn, call)
            # **先换 L 的名字、再代尺寸**（2026-11-12 夜，与 `_callee_kappa` 同）：
            # 反过来的话 `n(形参) → L(调用方容器)` 会先落地，被当成"被调方的
            # L(形参)"去查位置表 ⇒ 假泄漏 ⇒ 推得出来也退 ⊤。
            cost, eleak = self._rename_callee_elems(fn, cost, call)
            if eleak:
                return None
            cost = cost.substitute_sizes(tab)
            ren, leak = self._receiver_size_renames(fn, call, cost)
            if leak:
                return None
            if ren:
                cost = cost.substitute_sizes(ren)
            cost, xleak = self._resolve_caller_elems(cost, caller)
            if xleak:
                return None
            sub = self._subst_scalar_syms(cost, fn, call, loose=True)
            if sub is None:
                return None
            return sub
        finally:
            if caller is not None:
                self._fn_stack.pop()
            self.cur_fn = _prev

    def _struct_growth_at_call(self, short: str, idx: int, field: str,
                               call: Node) -> Optional["Cost"]:
        """调用 `short(…, x, …)` 会让 `x.<field>` 最长多少个元素（调用方符号）。

        `None` = 推不出。按值 / `const&` 形参 → `0`（改不到调用方的对象）。
        """
        fn = self.fns.get(short)
        if fn is None:
            return None
        p = self._param_at(fn, idx)
        if p is None or not getattr(p, "name", ""):
            return None
        ty = p.children[0] if (p.children or []) else None
        if not _is_ref_type(ty) or (getattr(p, "value", None) or {}).get("const"):
            return Cost.num(0)
        sname = self._struct_of_type(ty)
        if sname is None:
            return None
        head = self._struct_field_head(sname, field)
        if head is None:
            return None
        g = self._field_growth(fn, p.name, field, sname, head)
        if g is None:
            return None
        return self._instantiate_callee_cost(g, fn, call)

    def _param_size_table(self, fn: Node, call: Node,
                          derive: bool = True) -> Dict[str, Cost]:
        """E19：被调函数 κ 里的 `size(形参名)` → **调用者实参的尺寸**。

        不这么做，κ 里会留下被调函数的形参名（`n(a)`），
        在调用者作用域里查不到，求值时按 1 算 → **低估 → 上界不成立**。

        `derive=False` 走**旧口径**（只认名字 / 定容局部 / 行别名，表达式一律跳过）：
        这条路只用于**按 target 记账**的调用点（`_call_inner` ②）。
        原因是 `target` 是 **big-O 承诺**，隐含"≤ C·n + C"里的常数项；
        把形参尺寸换成一个退化实参的表达式（`f(make())` → 0）会把那个常数项
        一起抹掉——实测 `down(compute(xs), i)` 的 κ 会掉到 ops 以下。
        推导出来的 κ 没有这个问题（它自己数了被调函数的每个操作），所以
        `derive=True` 只给「有推导值」的调用点（`_callee_kappa`）。
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
            # **按值形参也要换尺寸**（A-full 之后 κ 里有 `n(形参)` 的拷贝项）。
            # 修前这里只认 `type-ref` 与被改名前的 `Vec`/`FixedVec`，
            # 于是 `vector<u64> xs`（按值）的 `n(xs)` 在调用点换不掉，
            # 实参尺寸丢了会**低估**。现在过 canon 的类型头。
            _h, _args, _isref = self._type_parts(ty)
            is_container = _isref or _h in (_OWNING_HEADS + _ARR_HEADS)
            if not is_container or not p.name:
                continue
            tgt = a
            while tgt is not None and tgt.kind in ("ref", "paren"):
                tgt = tgt.children[0] if tgt.children else None
            if not derive:
                # **旧口径**：target 只能按"实参是个名字"实例化。
                if tgt is None or tgt.kind != "path" or not tgt.name:
                    continue
                nm = tgt.name
                if nm in self.capacities:            # 定容局部（E22）
                    table[p.name] = Cost.num(self.capacities[nm])
                elif nm in self.row_of:              # 行别名归一到外层容器
                    table[p.name] = Cost.size(self.row_of[nm])
                else:
                    table[p.name] = Cost.size(nm)
                continue
            # **实参的尺寸要**推导**，不是把实参名字抄成尺寸量**（D2）：
            #   · 输入容器形参 → `n(名字)`（旧口径，仍对）
            #   · 局部容器      → 它的**长度上界**（`enc = encode(data)` → `n(data)×4`）
            #   · 嵌套调用      → `g` 的**返回尺寸**（`f(g(x))` → 用 `x` 写出来）
            # 推不出 → ⊤ + 原因；**MUST NOT** 留 `n(局部名)` / `n(被调形参名)`
            # —— 那两个名字在调用者作用域里查不到，却会被 `compare()` 当成
            # 一个尺寸量参与配对（E23 的直接来源）。
            sz = self._container_expr_size(tgt)
            if sz is None:
                table[p.name] = self._unknown_size(
                    f"`{fn.name or '?'}(…)` 的容器实参 `{_expr_text(a)}` 长度推不出"
                    f"（局部容器没有可证明的界，或表达式结果的尺寸未知）"
                    f" ⇒ 这一项退 ⊤，不猜")
                continue
            table[p.name] = sz
        return table

    def _rename_callee_elems(self, fn: Node, cost: "Cost",
                             call: Optional[Node]) -> Tuple["Cost", bool]:
        """把被调函数 κ 里的 `L(形参)` 换成 `L(调用点容器)`。

        **为什么需要**：`g[v].size()` 的尺寸量是 `L(g)`（见 `bound.py` 模块头），
        它写在**被调函数自己的形参名**上。内联 / 按 κ 记账时，调用方没有这个
        名字——留着它，求值查不到、判定尺寸量也对不上（与 `size(形参)` 的 E19
        完全同一件事）。

        **换法直接看实参本身，不看尺寸表**（2026-11-12 晚）：
        `形参 → 调用点的容器名`（裸名 / 字段链）是**语法**关系，与那个容器有多长
        无关。旧实现要求尺寸表里是 `Cost.size(名字)`（= 长度界是个**符号**），
        于是 `main` 里 `Graph g = make_graph(8)` 之后 `g` 的长度界是常数 `8`
        ⇒ `L(g)` 换不掉 ⇒ `count_components(g)` / `bfs(g,…)` / `has_cycle(g)`
        全被判 ⊤（12_graph 唯一的缺口就是这么来的）。
        长度是常数不影响「`L` 指的是哪个容器」——那是两件事，别混。

        换完的名字是不是**真正有界**由 `_resolve_caller_elems` 判：调用方局部容器
        有推出来的元素长度上界就代进去，推不出才 ⊤。**参数不是纯容器名**
        （`f(g(x))`、`f(xs[0])`）⇒ 换不掉，返回 `(cost, True)`，调用方记 ⊤。
        """
        if cost is None:
            return cost, False
        names = set(cost.elem_names())
        if not names:
            return cost, False
        if call is None:
            return cost, True           # 没有调用点：无法知道实参是谁，不猜
        pnode = next((c for c in (getattr(fn, "children", None) or [])
                      if getattr(c, "kind", "") == "params"), None)
        pos = {getattr(p, "name", ""): _i
               for _i, p in enumerate(pnode.children if pnode is not None else [])}
        anode = next((c for c in (getattr(call, "children", None) or [])
                      if getattr(c, "kind", "") == "args"), None)
        args = list(anode.children if anode is not None else [])
        mapping: Dict[str, str] = {}
        for nm in names:
            if nm not in pos:
                # `L(局部容器)` 不应出现（`_elem_bound_ok` 只认 const 形参）；
                # 真出现了说明上游口径变了，**不猜**。
                return cost, True
            _i = pos[nm]
            if _i >= len(args):
                return cost, True
            a = args[_i]
            while a is not None and getattr(a, "kind", "") in ("ref", "paren", "try",
                                                              "cast", "deref"):
                a = a.children[0] if a.children else None
            if a is not None and getattr(a, "kind", "") == "path" \
                    and getattr(a, "name", ""):
                mapping[nm] = a.name
            elif a is not None and getattr(a, "kind", "") == "field":
                nm2 = field_chain_name(a)
                if not nm2:
                    return cost, True
                mapping[nm] = nm2
            else:
                return cost, True
        return cost.rename_elems(mapping), False

    def _frame_names(self, fn: Optional[Node]) -> set:
        """**这一帧解释得了的名字**：形参 + 局部声明（`var-decl` / `const-decl` /
        `for` 绑定）+ 模块级常量。

        `cost_of` 出口的泄漏断言用（`_foreign_symbols`）。成员链 `recv.n_` 与
        差值名 `hi - lo` 不在这里展开，由 `_foreign_symbols` 拆开逐段判。
        """
        if fn is None:
            return set()
        out = set()
        for c in (fn.children or []):
            if c.kind == "params":
                for p in (c.children or []):
                    if getattr(p, "name", ""):
                        out.add(p.name)
        for d in list(_descendants(fn, "var-decl")) \
                + list(_descendants(fn, "const-decl")) \
                + list(_descendants(fn, "for")):
            if getattr(d, "name", ""):
                out.add(d.name)
        out |= set(getattr(self, "const_values", {}) or {})
        # **模块级声明**（`const N: u64 = 256;`）：κ 里写 `N` 是合法的。
        for it in (getattr(self.mod, "children", None) or []):
            if getattr(it, "kind", "") in ("const-decl", "var-decl", "const") \
                    and getattr(it, "name", ""):
                out.add(it.name)
        return out

    def _foreign_symbols(self, cost: Optional["Cost"],
                         fn: Optional[Node]) -> List[str]:
        """κ 里**这一帧解释不了**的名字 = 内联没代换掉的外来符号。

        「解释得了」= 形参 / 局部声明 / 模块常量；成员链看根（`recv.n_` 的
        `recv` 是本帧的名字就够）；区间差值名 `hi - lo` 看两边。

        为什么要有它（2026-11-13）：内联把被调方的 `sym(形参)` 留进调用方 κ 时，
        `evaluate` 对查不到的名字返回 **inf**（保守，不破界），但**渲染出来是一条
        像模像样的具体式**——判定侧只会说「尺寸量不一致（E23）」，指向的是
        target 而不是真凶；`compare()` 甚至可能拿它当尺寸量配出假的阶
        （`03_matrix.main` 的 `n` 就配出了一条**假债务**）。所以出口处再兜一层。
        """
        if cost is None or fn is None:
            return []
        known = self._frame_names(fn) | set(self.capacities or {})

        def ok(name: str) -> bool:
            nm = str(name or "")
            if not nm or nm == "?" or nm.startswith("k("):
                return True                         # `?` 另有兜底；`k(f)` 是函数值
            for pre in ("n(", "L("):                # `n(xs)` / `L(g)` 看括号里的名字
                if nm.startswith(pre) and nm.endswith(")"):
                    return ok(nm[2:-1])
            if " - " in nm:                         # `sym("hi - lo")`
                return all(ok(p) for p in nm.split(" - "))
            if nm in known:
                return True
            return nm.split(".", 1)[0] in known     # 成员链看根

        return sorted({v for v in cost.variables() if not ok(v)})

    def _sym_names(self, cost: Optional["Cost"]) -> set:
        """κ 里**裸符号**（`sym`）出现的名字集合。

        与 `variables()` 的区别是**关键**：`variables()` 把 `size(名字)` 也当
        尺寸量收进来，而 `size(名字)` 里的名字在调用点已经换成**调用方**的了
        （`n(from)` 是调用方的容器 `from`，不是被调方那个同名的标量形参）。
        拿 `variables()` 判"被调方标量形参有没有换掉"会在**同名**时误判
        （`14_buffer.replace_all` 调 `find_from(…, from=…)`、调用方自己也有
        `from`）——把本来干净的内联判成泄漏，退成 ⊤。
        """
        out: set = set()

        def walk(x) -> None:
            if x is None:
                return
            if x.kind == "sym":
                out.add(str(x.value))
            if x.kind == "piece":
                for _lo, _hi, sub in x.value[1]:
                    walk(sub)
            for a in getattr(x, "args", ()) or ():
                walk(a)

        walk(cost)
        return out

    def _scalar_value_bound(self, node, fn: Optional[Node]) -> Optional["Cost"]:
        """实参表达式的**值**上界，用**调用方这一帧里查得到的名字**写。

        与 `_count_of_expr` 的分工：那个只认"能直接写成尺寸量"的名字（容器尺寸 /
        模块常量 / 标量形参），**普通的局部标量一律拒收**；这里允许把局部标量
        **本身**当符号写出来（`sym(acc)`）——它在这一帧里是**合法名字**（不是
        别人的形参），而 `a / b ≤ a`、`a - b ≤ max(a, b)`、`a % b ≤ a`、
        `a >> b ≤ a` 这些无符号关系保证换上去只会变大。

        为什么非它不可（2026-11-13，`13_numeric.stddev_milli` 的

            return isqrt(acc / xs.size());

        第一版在这里退 ⊤：`acc` 是**被改写的局部**（`acc += d * d`），严格口径
        不收。可 `x = acc / n(xs) ≤ acc` 本身是成立的——只要把 `acc` **这个名字**
        留在 κ 里（`(acc + 1)×33`）。这一帧认它，`evaluate` 对局部名给 inf
        （保守，不破界），判定侧仍会说"尺寸量不一致"（E23）——但**不再有别人的
        形参名**，`demo/corpus` 的「115/115 可算」也保住了。

        认不出的形状 → `None`（调用点退 ⊤，见 `_subst_callee_scalars`）。
        """
        if node is None:
            return None
        while getattr(node, "kind", "") in ("ref", "paren", "cast", "try", "deref"):
            node = node.children[0] if node.children else None
            if node is None:
                return None
        k = getattr(node, "kind", "")
        if k == "lit-int":
            v = _int_literal(node)
            return None if v is None else Cost.num(v)
        if k == "path":
            nm = getattr(node, "name", "") or ""
            if not nm:
                return None
            if nm in self.const_values:
                return Cost.num(self.const_values[nm])
            ty = self._type_of_expr(node)
            _iscont = (nm in self.row_of
                       or (ty is not None and _is_container_type(ty))
                       or (fn is not None and nm in self._container_params_of(fn))
                       or (fn is not None
                           and nm in set(self._container_bounds_full(fn)[1])))
            if _iscont:
                return Cost.size(nm)        # 容器：`n(名字)`（与 `_name_count` 同口径）
            if fn is not None and nm not in self._frame_names(fn):
                return None                 # 不是这一帧的名字：不猜
            return Cost("sym", nm)          # 标量（含被改写的局部）：值符号
        if k in ("method-call", "call"):
            short = _callee_short(node)
            if short in SIZE_FNS:
                return self._count_of_expr(node, fn)
            return None
        if k == "binary":
            op = node.value or ""
            a = node.children[0] if (node.children or []) else None
            b = node.children[1] if len(node.children or []) > 1 else None
            la = self._scalar_value_bound(a, fn)
            if la is None:
                return None
            if op in ("/", "%", ">>"):
                # 无符号：`a / b ≤ a`、`a % b ≤ a`、`a >> b ≤ a`
                return la
            lb = self._scalar_value_bound(b, fn)
            if lb is None:
                return None
            if op == "+":
                return Cost.add(la, lb)
            if op == "*":
                return Cost.mul(la, lb)
            if op == "-":
                return Cost.max(la, lb)     # 无符号减法：`a - b ≤ max(a, b)`
            return None
        return None

    def _subst_callee_scalars(self, cost: Optional["Cost"], fn: Node,
                              call: Optional[Node],
                              caller: Optional[Node] = None) -> Optional["Cost"]:
        """被调方 κ 里**剩下的标量形参**必须换成调用点的界；换不掉 → `None`（⊤）。

        **为什么还要有这一步**（2026-11-13，`13_numeric.stddev_milli`）：
        `_scalar_bound_renames` 是**逐名尽力**的——认不出就把那个名字原样跳过。
        于是 `isqrt(acc / xs.size())` 的 `sym(x)` 留在了调用方的 κ 里：

            κ(stddev_milli) = … + (x + 1)×33 + …     ← `x` 是 isqrt 的形参
            κ 的尺寸量集合 = {x, xs}                  ← target 里没有 `x` 可写

        三级替换（**只会换成"更大的上界"，方向安全**）：

          ① `_scalar_arg_bound`：字面量 / 模块常量 / 调用方标量形参 / 不可变局部；
          ② `_count_of_expr`（只对非裸名表达式）：能写成尺寸量的上界；
          ③ `_scalar_value_bound`：把**调用方本帧的名字**写进 κ（`acc / n ≤ acc`）。

        三级都换不出（连名字都给不出来）⇒ **整条 ⊤**，绝不把别人的形参名留在
        调用方的 κ 里。

        **用 `_sym_names` 而不是 `variables()` 判"还有没有"**：换完之后
        `sym(nbits)` 可能换成**调用方同名的** `sym(nbits)`（`16_bitset.bits_from`
        调 `bits_init(b, nbits)`），名字一样、但那个名字现在是调用方的——按名字
        复查会把它当成"没换掉"。
        """
        if cost is None or call is None:
            return cost
        need = self._sym_names(cost) & set(self._scalar_params_of(fn))
        if not need:
            return cost                         # 已经干净：这条路一个字节都不动
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        anode = next((c for c in (call.children or []) if c.kind == "args"), None)
        ps = pnode.children if pnode is not None else []
        args = anode.children if anode is not None else []
        _prev = (self.cur_fn, self.row_of, self.capacities)
        if caller is not None:
            # 实参的界要用**调用方**那一帧查（`_decl_inits` / `const_values` /
            # `_type_of_expr`）；`_callee_kappa` 里 `cost_of(被调方)` 已经把
            # `cur_fn` 换成了被调方。
            self.cur_fn = caller
        try:
            tab: Dict[str, Cost] = {}
            for i, p in enumerate(ps):
                nm = getattr(p, "name", "") or ""
                if nm not in need:
                    continue
                a = args[i] if i < len(args) else None
                v = self._scalar_arg_bound(a, self._cur_frame_fn())
                if v is None:
                    arg = a
                    while getattr(arg, "kind", "") in ("ref", "paren"):
                        arg = arg.children[0] if arg.children else None
                    if arg is not None and getattr(arg, "kind", "") != "path":
                        v = self._count_of_expr(arg, self._cur_frame_fn())
                if v is None:
                    v = self._scalar_value_bound(a, self._cur_frame_fn())
                if v is None:
                    return None                 # 连名字都给不出来：退 ⊤
                tab[nm] = v
        finally:
            self.cur_fn, self.row_of, self.capacities = _prev
        return cost.substitute_syms(tab)

    def _resolve_callee_scalar_locals(self, cost: Optional["Cost"],
                                      fn: Optional[Node]) -> Optional["Cost"]:
        """把被调方表达式里的**标量局部**换成它的值上界（能证就换）。

        **为什么需要它**（2026-11-13，`03_matrix.main`）：`matmul` 的

            std::size_t n = a.size();
            for (i = 0; i < n; ++i) …

        把循环半径记成 `sym(n)`（`zeros(n)` 的返回尺寸也是它）。内联进 `main`
        后 `main` 根本没有 `n`：`evaluate` 给 inf（不破界），但 `compare()`
        拿它当**尺寸量**配阶，配出一条 `O(n) vs O(1)` 的**假债务**
        （`03_matrix.main` 修前 verdict=debt，修后是常数 782 = ✓）。

        **只认「没被写过」的局部**（`_decl_inits` 的判据）：那种局部的值恒等于
        初值，`_count_of_expr(初值)` 就是它的上界（`n → n(a)`），只会变大。

        证不出的（被改写的 `while (lo < hi)` 的 `hi`）**原样留着**、返回 `None`
        不用——这是**已知的、有文档的工具缺口**（`17_sortkey.tu` 写着「hi 由
        `xs.size()` 初始化，理想形态能折成 `n(xs)`」）：它落在"局部尺寸名"这一
        类（与 `n(cur)` / `n(masked)` 同类，E23 而不是破界），今天语料里只有
        `17_sortkey.main` 一例。**不许**在这里退 ⊤：`demo/corpus` 的
        「115/115 可算」是钉住的 KPI（`test_kappa_tree_depth` [6]）。
        """
        if cost is None or fn is None:
            return cost
        used = set(cost.variables())
        # **局部容器不在这里管**：它们的界由 `_container_bounds_full` 给
        # （`n(cur) → n(s)`，见 `_resolve_callee_locals`）。名字进这张表说明它是
        # 容器局部，不是标量——排掉，否则会把它当"没有初值的标量"判推不出。
        _ln = set(self._container_bounds_full(fn)[1])
        _locals = (self._frame_names(fn)
                   - set(self._scalar_params_of(fn))
                   - set(self._container_params_of(fn))
                   - _ln)
        _inits = self._decl_inits(fn)
        stm: Dict[str, Cost] = {}
        for nm in sorted(used & _locals):
            if "." in nm or nm in stm:
                continue
            init = _inits.get(nm)
            b = self._count_of_expr(init, fn) if init is not None else None
            if b is None or b.is_top():
                continue                     # 证不出：原样留着（已知缺口，不退 ⊤）
            stm[nm] = b
        if not stm:
            return cost
        # 两种渲染都要换：循环半径记成裸符号 `sym(n)`（`matmul`），
        # `while (lo < hi)` 记成 `size(hi)`（`17_sortkey.lower_bound_by_key`）。
        return cost.substitute_syms(stm).substitute_sizes(stm)

    def _resolve_callee_locals(self, cost: Optional["Cost"],
                               fn: Optional[Node]) -> Optional["Cost"]:
        """把被调方 κ 里的**局部尺寸名**换成它的上界；推不出 → None。

        两类名字（都在调用方作用域里查不到）：

        ① **局部容器**（2026-11-12 夜，`02_csv_stats`）：`split` 的 κ 里有
           `n(cur)`（把正在组装的字段拷进 `out` 的那一项）。`cur` 是 split 的
           局部，在调用方作用域里查不到——`evaluate` 对查不到的尺寸按 **1** 算，
           那是**低估**（κ 不再是上界）。`bounds(cur) = n(s)` 是同一份
           `_container_bounds_full` 早就推出来的，代进去只会变大。
           局部名还在、又没有界 ⇒ 调用点退 ⊤（不猜、也不许按 1 算）。

        ② **标量局部**（2026-11-13）：见 `_resolve_callee_scalar_locals`。
        """
        if cost is None or fn is None:
            return cost
        lb, ln, _lp, _lr, _lc = self._container_bounds_full(fn)
        used = set(cost.variables())
        tab: Dict[str, Cost] = {}
        bad: List[str] = []
        for nm in sorted(used & set(ln)):
            b = lb.get(nm)
            r = None if b is None else self._resolve_local_cost(b, lb, {})
            if r is None:
                bad.append(nm)
            else:
                tab[nm] = r
        if bad:
            return None
        cost = self._resolve_callee_scalar_locals(cost, fn)
        if cost is None:
            return None
        return cost.substitute_sizes(tab)

    def _resolve_caller_elems(self, cost: Optional["Cost"],
                              caller: Optional[Node]) -> Tuple[Optional["Cost"], bool]:
        """把调用方 κ 里的 `L(调用方容器)` 换成**推出来的元素长度上界**。

        `_rename_callee_elems` 只把名字换对；这一步才决定那个名字**有没有界**：

          · 调用方的**局部容器**：用 `_elem_bounds_full(caller)` 的界
            （推不出 → 记泄漏，外层退 ⊤）；
          · 调用方的**const 容器形参**：`L(形参)` 本来就是一个合法尺寸量
            （`_elem_bound_ok` 保证它全程不变），原样保留；
          · 非 const 形参 / 字段链 / 认不出的名字：**原样保留**（求值时是 ⊤，
            不会低估）。这一格与旧口径一致，改成硬 ⊤ 只会把能判的判成判不了。
        """
        if cost is None:
            return cost, False
        names = set(cost.elem_names())
        if not names:
            return cost, False
        if caller is None:
            return cost, True
        eb, local_names = self._elem_bounds_full(caller)
        sym_ok = set()
        for p in self._elem_len_params(caller):
            if (getattr(p, "value", None) or {}).get("const"):
                sym_ok.add(getattr(p, "name", "") or "")
        table: Dict[str, Cost] = {}
        for nm in names:
            if nm in eb:
                v = eb[nm]
                if v is None:
                    return cost, True
                table[nm] = v
            elif nm in local_names:
                return cost, True       # 是局部容器但推不出界：不猜
            elif nm in sym_ok:
                continue                # 合法尺寸量，原样保留
            # 其余（非 const 形参 / 字段链 / 外来名）：原样保留（旧口径）
        if not table:
            return cost, False
        return cost.substitute_elems(table), False

    def _scalar_bound_renames(self, fn: Node, call: Optional[Node],
                             cost: "Cost") -> Dict[str, Cost]:
        """被调函数 κ 里的**标量形参** → 调用点实参表达式（能推成尺寸/常数时）。

        **为什么需要**：`append_range(b, src, lo, hi)` 的轮数是 `hi`（标量形参），
        调用点 `append_range(out, to, 0, to.size())` 把它绑到 `n(to)`。不换的话
        调用方 κ 里会留一个**别人的形参名** `hi`——它既不是调用方的尺寸量，
        也说不清是哪一次调用的值（同一个函数被调两次、两次 hi 不同）。
        判定会因 E23 退 unknown（不会假达成），但那是"信息丢了"，能补就补。

        **只认能结构化的实参**：`expr_to_bound(实参)` 给 `size` / `const` 才换。
        变量实参（`f(..., at)`）、复合表达式（`lo + 1`）一律不动——那些值不是
        这段代码能命名成尺寸量的东西，硬换会把名字搞乱。
        """
        if call is None:
            return {}
        pnode = next((c for c in (fn.children or []) if c.kind == "params"), None)
        anode = next((c for c in (call.children or []) if c.kind == "args"), None)
        if pnode is None or anode is None:
            return {}
        used = set(cost.variables())
        out: Dict[str, Cost] = {}
        for p, a in zip(pnode.children, anode.children):
            nm = getattr(p, "name", "") or ""
            if not nm or nm not in used:
                continue
            ty = p.children[0] if (p.children or []) else None
            if ty is not None and _container_type_name(ty) is not None:
                continue                        # 容器形参：`_param_size_table` 管
            b = expr_to_bound(a)
            if b is None or b.kind not in ("size", "const"):
                # **换不成界表达式时的两条兜底**（2026-11-12）：以前直接跳过，
                # 于是被调方的标量形参名留在调用方 κ 里（`replace_all` 的
                # `hi×16`）——求值时是 ⊤（不是低估），但 κ 从此只能按"未知"
                # 看，判阶也就无从谈起。
                #   ① 调用方**自己的**标量/常数（`_scalar_arg_bound` 的严格口径）；
                #   ② 圈数兜底：`hi` 是"读 const 容器 `C[i]` 的循环"的上界
                #      ⇒ `hi ≤ n(C)`（良定义执行，见 `_scalar_index_ceiling`）。
                v = self._scalar_arg_bound(a, self._cur_frame_fn())
                if v is not None:
                    out[nm] = v
                    continue
                _c = self._scalar_index_ceiling(fn, nm)
                _cv = (self._param_size_table(fn, call, derive=True).get(_c)
                       if _c is not None else None)
                if _cv is not None:
                    out[nm] = _cv
                continue
            if b.kind == "size":
                out[nm] = Cost.size(b.value)
            elif b.kind == "const":
                try:
                    out[nm] = Cost.num(int(b.value))
                except (TypeError, ValueError):
                    continue
        return out

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
        _caller = self._cur_frame_fn()
        self.inlining.append(fn.name or "")
        self.fnval_stack.append(_binds)
        try:
            # **实参也要算**（与①②同一修法）：`_body(fn)` 只含被调函数的块，
            # 调用方实参表达式**不在**其中——`_param_size_table` 只换尺寸、
            # 不产生操作数。所以 `1 + 被调体 + Σ实参` 里每一项互不重叠。
            # `L(形参)` 也要换成调用点的容器名（与 `size(形参)` 同一件事）。
            # **先换 L 的名字、再代尺寸**（2026-11-12 夜，见 `_callee_kappa`）。
            _body, _leak = self._rename_callee_elems(fn, self._body(fn), node)
            if not _leak:
                _body = _body.substitute_sizes(_tab)
                _body, _leak = self._resolve_caller_elems(
                    _body, self._cur_frame_fn())
            # 标量形参的**界**按实参实例化（`hi` → `n(to)`）。
            _sren = self._scalar_bound_renames(fn, node, _body)
            if _sren:
                _body = _body.substitute_syms(_sren)
            # **剩下来的标量形参 ⇒ ⊤**（2026-11-13，与 `_callee_kappa` 同一修法）：
            # 内联的硬约束是「被调方的形参必须被实参代换掉」，换不掉就不许把
            # 名字留在调用方 κ 里（`13_numeric.stddev_milli` 的 `x`）。
            if not _leak:
                _body = self._subst_callee_scalars(_body, fn, node, _caller)
                _leak = _body is None
            if _leak:
                return Cost.add(Cost.num(OP_COST),
                                Cost.add(Cost.top(), _args))
            return Cost.add(Cost.num(OP_COST), Cost.add(_body, _args))
        finally:
            self.fnval_stack.pop()
            self.inlining.pop()

    def _callee_kappa(self, name: str, node: Optional[Node],
                      env: Dict[str, Cost]) -> Optional[Cost]:
        """被调函数的 **κ（推导值）**，已按调用点实参换好尺寸；环上 → None。

        **调用点必须用这个，不许再用被调函数的 `target`**（2026-11-09）。

        `target` 是作者写的**承诺**，κ 是工具**算出来的上界**。拿承诺去合成
        调用方的上界，等于让「写松一个 target」把整条调用链一起拉低：

          · `_tier_b/sha256`：`compress` 声明 `O(1)`，实际每块 ~18k ops；
            `sha256(1KB)` 的 κ 只有 21,566，实测 **308,126**（少 14 倍）。
          · 最小复现 `_probe_calltarget`：`inner` 声明 `O(1)`、κ(inner)=16，
            调用方 `outer` 每轮只记 9 —— κ(outer)=184 < 实测 427。

        口径与 `cost_of()` **逐字一致**（含按值形参的入口拷贝、局部容器尺寸
        归并），区别只有两条，都是为了在**调用点**用：

          · 按 `node` 的实参把形参尺寸换掉（`_param_size_table`）；
          · 带上调用点的函数值绑定（`apply(xs, compute)` 里的 `g` → `compute`），
            否则 κ 里会凭空多出 `k(g)` 符号、把本来能判的判成 ⊤。

        **返回的 κ 已含被调函数入口的按值拷贝**，调用点不要再加
        `_callee_entry_copy`（那是 target 那条路的补丁，加了就是双计）。

        `None` 只表示「这是个环上的调用点，没有可以展开的推导值」——
        递归调用点由 `_rec_once` + `recursion depth` 负责（见 `_call_inner` ①）。
        """
        if name in self.frames:
            return None
        fn = self.fns.get(name)
        if fn is None:
            return None
        # **递归且没有声明深度**：它的 κ 自己也只是「一层 target」的混合体
        # （递归那一跳没有可以展开的推导值），拿它当调用成本并不能把深度补上。
        # 这与「只有 spec」同类 → 退回 target（被迫信任，见 `_call_inner` ②）。
        # 有 `recursion depth <= N` 的递归不在此列：`depth_of` 有值，递归那一跳
        # 走 `_rec_once` × N，是完整的推导值。**visited 守卫**（②，2026-11-12）
        # 也在此列：`_visited_depth` 给出的是**激活数上界**（`n(标记容器)`），
        # 与声明 depth 同形，递归那一跳同样走 `_rec_once × 深度`。
        # **区间对半分**（②'，2026-11-12）同理：`_halving_span` 给的是激活数
        # 上界 `2 × (hi - lo)`，递归那一跳走的是同一套 `× 单层 + 叶子层`。
        if (self.depth_of(name) is None
                and self._self_call_shape(name)[0] > 0
                and self._visited_depth(name) is None
                and self._halving_span(name) is None):
            return None
        # **尺寸表要在进被调帧之前取**：它读的是调用方那一帧的
        # `row_of` / `capacities`（局部行别名、定容局部）。
        # **调用方那一帧也要先记下来**（2026-11-13）：`cost_of(被调方)` 会把
        # `cur_fn` 换成被调方，之后 `_subst_callee_scalars` 要给实参求界，
        # 必须按**调用方**的名字查 `_decl_inits` / `const_values`。
        _caller = self._cur_frame_fn()
        _tab = self._param_size_table(fn, node) if node is not None else {}
        # **区间长度也要在进被调帧之前实例化**（同上：读调用方那一帧）。
        _span = (self._halving_span_at(fn, node)
                 if self._halving_span(name) is not None else None)
        _span_sym = None
        if self._halving_span(name) is not None:
            _lo_i, _hi_i = self._halving_span(name)     # type: ignore[misc]
            _span_sym = size_diff_name(Cost("sym", _hi_i), Cost("sym", _lo_i))
        _binds = (self._fnvalue_bindings(fn, node, env)
                  if node is not None else {})
        # `cost_of` 是「顶层分析一个函数」的入口：它会**重置** cur_fn / row_of /
        # capacities。嵌在调用点里算一次，必须把调用方那份状态原样还回去，
        # 否则调用点之后的行别名 / 定容局部会查错表（低估）。
        # **区间对半分帧里的调用**：实参就是本帧的区间端点（`lo ≤ mid ≤ hi`）。
        # 给被调方补上这三条 `requires` 事实，它的轮数才会是 `mid - lo` 这类
        # **差值**（只随区间长度走）而不是绝对下标 `mid` / `hi`。
        _plan = self._halving_callee_plan(name, node)
        _prev = (self.cur_fn, self.row_of, self.capacities, self._reassigned)
        self.inlining.append(name)
        self.fnval_stack.append(_binds)
        try:
            if _plan is None:
                _k = self.cost_of(name)
            else:
                # 临时事实**用完即撤**：被调方独立的 κ 不受影响。缓存也一起
                # 快照/还原——按补充事实算出来的（更紧的）结果不能漏进别处。
                _snap = {_a: dict(getattr(self, _a))
                         for _a in _FACT_SENSITIVE_CACHES}
                # **先清再算**：这些缓存里装的是"按循环上界推出来的界"，
                # 上一次（没有补充事实）算出来的版本会是绝对下标。
                for _a in _FACT_SENSITIVE_CACHES:
                    setattr(self, _a, {})
                self._facts_extra[name] = _plan["facts"]
                try:
                    _k = self.cost_of(name)
                finally:
                    self._facts_extra.pop(name, None)
                    for _a, _v in _snap.items():
                        setattr(self, _a, _v)
            # **`L(形参)` → `L(调用点容器)`**（2026-11-12）：与尺寸表同一件事，
            # 换不掉（实参不是纯容器名）⇒ ⊤，绝不把被调方形参名留在调用方 κ 里。
            # **先换 L 的名字、再代尺寸**（2026-11-12 夜）：反过来的话，
            # `n(s) → L(调用方容器)` 先落地，`_rename_callee_elems` 会把**调用方的**
            # `L(lines)` 当成"被调方的 L(形参)"去查位置表，查不到 ⇒ 判泄漏 ⇒
            # 明明推得出来也退 ⊤（`02_csv_stats` 的 `split(lines[i], ',')`）。
            _k, _eleak = self._rename_callee_elems(fn, _k, node)
            if _eleak:
                return Cost.top()
            _k = self._resolve_callee_locals(_k, fn)
            if _k is None:
                return Cost.top()
            _k = _k.substitute_sizes(_tab)
            _k, _xleak = self._resolve_caller_elems(_k, self._cur_frame_fn())
            if _xleak:
                return Cost.top()
            _sren = self._scalar_bound_renames(fn, node, _k)
            if _sren:
                _k = _k.substitute_syms(_sren)
            # **标量形参没代换干净 ⇒ ⊤**（2026-11-13，`13_numeric.stddev_milli`）：
            # `_scalar_bound_renames` 是逐名尽力的（认不出就跳过），于是
            # `isqrt(acc / xs.size())` 的 `sym(x)` 原样留在了调用方的 κ 里。
            # 内联的硬约束是「被调方的形参必须被实参代换掉」——换不掉就退 ⊤，
            # 绝不把别人的形参名留在调用方 κ 里（渲染出来像具体式，其实是 inf）。
            _k = self._subst_callee_scalars(_k, fn, node, _caller)
            if _k is None:
                return Cost.top()
            if _plan is not None:
                # 被调方的形参差值（`mid - lo`）→ 本帧的区间长度 `hi - lo`；
                # 换不掉的名字**不许**留在调用方的 κ 里（形参名泄漏）。
                _k = self._resolve_halving_diffs(_k, fn, _plan)
            # **区间长度 `hi - lo`**（区间二分递归，2026-11-12）：那是**被调函数
            # 形参**上的尺寸量（`sym("hi - lo")`，一个复合名），`_scalar_bound_renames`
            # 只认单个形参名，换不到它。调用点必须换成实参的区间长度
            # （`merge_sort(xs, 0, xs.size())` → `n(xs)`）；换不出来就 ⊤——
            # 绝不让被调方的形参名留在调用方的 κ 里（求值按 +∞，判定对不上）。
            if _span_sym is not None and _span_sym in set(_k.variables()):
                if _span is None:
                    return Cost.top()
                _k = _k.substitute_syms({_span_sym: _span})
            # **成员尺寸是对象相对的**（与 `_ret_size_of` 同一条修法）：
            # `n(recv.first_)` → 调用点实参的 `n(solver.first_)`。换不掉 ⇒ ⊤。
            _ren, _leak = self._receiver_size_renames(fn, node, _k)
            if _leak:
                return Cost.top()
            return _k.substitute_sizes(_ren) if _ren else _k
        finally:
            self.fnval_stack.pop()
            self.inlining.pop()
            (self.cur_fn, self.row_of, self.capacities,
             self._reassigned) = _prev

    def _note_target_trusted(self, name: str) -> None:
        """标注：「这个调用点没有实现、只有声明，只能按 `target` 记账」。

        这不是判定，也不会改变 κ —— 它是**被迫的信任**，要在报告里能指出来
        （`ci.target_trusted`：名字 → 次数）。规则见 `_call_inner` ②。
        """
        tbl = getattr(self, "target_trusted", None)
        if tbl is None:
            tbl = {}
            self.target_trusted = tbl
        tbl[name] = tbl.get(name, 0) + 1

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
        # **用 κ，不用 target**（与 `_call_inner` ① 同一条修法）：函数值调用也是
        # 普通调用，被调函数的 `target` 只是声明，拿它记账会让写松的 target
        # 污染调用方。`_inline_body` 就是这条路上**原本**的推导值口径
        # （`1 + 被调体 + Σ实参`，带函数值绑定）——修前只把它当作「没 target 时
        # 的退路」，现在它对**有 target 的被调函数**同样适用。
        return self._inline_body(fn, node, env)

    def _format_cost(self, node: Node, env: Dict[str, Cost]) -> Cost:
        """`format(fmt, …)` 的成本：**输出长度的上界** + 实参求值（D4）。

        `format` 是内建，它的成本不是"第一个实参（格式串）的长度"——
        那是 `stdlib` 表里 `cost_kind == "n"` 的缺省口径，对 `format` 是错的
        （格式串通常是个短字面量，而输出长度由**各实参**决定）。

        上界口径：

        | 格式串 | 输出长度上界 |
        |---|---|
        | **字面量** | 字面量字节数 + Σ各实参的渲染长度（标量 ≤ 常数；容器 = `n(容器)`） |
        | 非字面量 | `n(fmt) × (1 + Σ各实参的渲染长度)`——洞数 ≤ `n(fmt)`，每个洞的输出 ≤ (1 + 最大实参长度) |

        **标量的渲染长度是常数上界**（`u64` 十进制 ≤ 20 字节、十六进制 ≤ 16；
        宽度/精度写的是字面量，也是常数），所以在 big-O 里记 0。
        **容器的长度推不出 → ⊤ + 原因**；MUST NOT 写 `n(?)`。
        """
        args_node = next((c for c in node.children if c.kind == "args"), None)
        items = list(args_node.children) if args_node is not None else []
        base = Cost.num(OP_COST * 2)         # 调用 + 内建派发
        if not items:
            return base
        sum_sz = Cost.num(0)
        for a in items[1:]:
            sz = self._rendered_size(a)
            if sz is None:
                return self._unknown_size(
                    f"`format(…)` 的实参 `{_expr_text(a)}` 长度推不出"
                    f" ⇒ 输出长度未知，退 ⊤，不猜")
            sum_sz = Cost.add(sum_sz, sz)
        fmt = items[0]
        while fmt is not None and getattr(fmt, "kind", "") in (
                "ref", "paren", "try", "cast"):
            fmt = fmt.children[0] if fmt.children else None
        if fmt is not None and getattr(fmt, "kind", "") == "lit-string":
            try:
                _lit = len(fmt.value or b"")
            except TypeError:
                _lit = 0
            core = Cost.add(Cost.num(_lit), sum_sz)
        else:
            _nfmt = self._container_expr_size(items[0])
            if _nfmt is None:
                return self._unknown_size(
                    f"`format(…)` 的格式串 `{_expr_text(items[0])}` 不是字面量、"
                    f"长度又推不出 ⇒ 洞数未知，退 ⊤，不猜")
            core = Cost.mul(_nfmt, Cost.add(Cost.num(1), sum_sz))
        return Cost.add(base, Cost.add(core, self._args_cost(node, env)))

    def _rendered_size(self, e) -> Optional["Cost"]:
        """一个值**渲染成文本后的长度上界**；只有容器会随规模增长。

        类型认不出时按标量记 0：本语言 `format` 的洞只接受标量与
        `vector<u8>`/`Text`（struct / 元组由 check 的 E-Format-Aggregate 拦住），
        而容器在 `_type_of_expr` 里认得出来。
        """
        ty = self._type_of_expr(e)
        if ty is not None and _is_container_type(ty):
            return self._container_expr_size(e)
        return Cost.num(0)

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
        # **用户函数优先于同名内建**（2026-11-10，D2 连带）：解释器 `_call` 是
        # `if name in self.fns` **先于** `stdlib`/`for_each`/`sort`/`min`/`max`
        # （interp.py:3914-3919 的注释记着这条修过的顺序）。成本域以前把内建
        # 分支写在前头，于是用户写的 `dedup` / `sort` 被当成内建记账——
        # `_diff/dedup` 的 `main` 就是：κ=107 而实测 ops=1005（**假上界**）。
        # 这里只让**本模块的具名函数**（不带 `.` 的裸名）抢回自己的名字。
        _shadowed = bool(name) and "." not in (name or "") and (name or "") in self.fns
        _short_hp = (name or "").split(".")[-1]
        # **`swap(xs, i, j)` 与 `print` 是解释器在「用户函数」之前特判的**
        # （interp.py:3870 / 3882）⇒ 用户函数**盖不住**它们，成本域同序。
        if _short_hp == "swap":
            return Cost.add(Cost.num(OP_COST), self._args_cost(node, env))
        # **堆原语是 `O(log2 n)`，不是常数**（对齐 std::push_heap/pop_heap）。
        if _short_hp in ("push_heap", "pop_heap") and not _shadowed:
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
        if _short_hof == "for_each" and not _shadowed:
            _an = next((c for c in node.children if c.kind == "args"), None)
            _items = _an.children if _an is not None else []
            # **尺寸推导**，不写 `n(?)` / 不写编出来的 `n(m)`。
            _n = self._container_expr_size(_items[0]) if _items else None
            if _n is None:
                _what = (_expr_text(_items[0]) if _items else "（无实参）")
                return self._unknown_size(
                    f"`for_each(…)` 的第一个实参 `{_what}` 长度推不出"
                    f" ⇒ 这一项退 ⊤，不猜")
            _fname = (_items[1].name if len(_items) >= 2 and _items[1].kind == "path" else "f")
            # **`k(visit)` 是裸符号**（不是 `n(visit)`——那读作"visit 的规模"）。
            _per = Cost.add(Cost.num(OP_COST), Cost("sym", f"k({_fname})"))
            _base = Cost.add(Cost.mul(_n, _per), _n)
            # **实参表达式**（②）：`for_each(m, visit)` 里 `m` 是先求值的
            # （`visit` 是具名函数，解释器包成 `FnVal` 不求值 ⇒ `_args_cost` 记 0）。
            # 与上面的 `n × …` 尺寸项**不重叠**：那是「转几圈」，这是「求一次实参」。
            return Cost.add(_base, self._args_cost(node, env))
        if _short_hof in ("map", "filter", "fold", "count_if") and not _shadowed:
            _an = next((c for c in node.children if c.kind == "args"), None)
            _items = _an.children if _an is not None else []
            # **尺寸推导**，不写编出来的 `n(xs)`。
            _n = self._container_expr_size(_items[0]) if _items else None
            if _n is None:
                _what = (_expr_text(_items[0]) if _items else "（无实参）")
                return self._unknown_size(
                    f"`{_short_hof}(…)` 的第一个实参 `{_what}` 长度推不出"
                    f" ⇒ 这一项退 ⊤，不猜")
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
        if stdlib.is_stdlib(name or "") and not _shadowed:
            # 最小基本库：多数是 `O(n(第一个容器实参))`。
            args_node = next((c for c in node.children if c.kind == "args"), None)
            items = args_node.children if args_node is not None else []
            short = (name or "").split(".")[-1]
            kind = stdlib.cost_kind(name or "")
            if kind == "ext":
                # **外部项**：不是"不知道多少"，而是"**除了这一项都算得出**"。
                # 渲染成 `ext(read_file)`，让读者看见成本里哪一块是外部给的。
                if (name or "") in self.externals:
                    return self.externals[name or '']
                return Cost("ext", name or "")
            if kind == "top":
                return Cost.top()
            if short == "format":
                # **`format` 的尺寸不是"第一个实参（格式串）的长度"**（D4）：
                # 它是**输出长度**，由格式串 + 各实参的渲染长度决定。
                return self._format_cost(node, env)
            if kind == "1":
                core = Cost.num(0)       # 常数成本（有界探测的哈希表）
            elif kind == "nret":
                # 流式口径：成本 = 1 + n(ret)（这次读了多少字节）
                return Cost.add(Cost.num(OP_COST), Cost.size("ret"))
            else:
                # **尺寸要推导，不许写 `n(?)`**：`n(?)` 会被 `compare()` 当成
                # 一个普通尺寸量去配对（D4 的误判来源）。推不出 → ⊤ + 原因。
                _n = self._container_expr_size(items[0]) if items else None
                if _n is None:
                    _what = (_expr_text(items[0]) if items else "（无实参）")
                    return self._unknown_size(
                        f"`{short}(…)` 的第一个实参 `{_what}` 长度推不出"
                        f" ⇒ 这一项退 ⊤，不猜")
                if kind == "nm":
                    # `text_find(hay, needle)`：最坏 O(|hay| × |needle|)
                    _m = (self._container_expr_size(items[1])
                          if len(items) > 1 else Cost.num(1))
                    core = Cost.mul(_n, _m if _m is not None else Cost.num(1))
                elif kind == "log":
                    core = Cost.log2(_n)
                else:
                    core = _n
            total = Cost.add(Cost.num(OP_COST), Cost.add(core, Cost.num(OP_COST)))
            # **stdlib `HashMap` 的容器键长度项**（2026-11-12）：`map_put` 等
            # 登记的 `log` 只覆盖表侧；键是 `vector` / `array` 时再读一遍键。
            if short in ("map_put", "map_get", "map_has"):
                _kc = self._hash_len_term_hashmap(
                    items[0] if items else None,
                    items[1] if len(items) > 1 else None)
                if _kc is not None:
                    total = Cost.add(total, _kc)
            for c in items:
                total = Cost.add(total, self._expr(c, env))
            return total
        if (name or "") in ("min", "max") and not _shadowed:
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
        if (name or "") in ("sort", "sort_by") and not _shadowed:
            args_node = next((c for c in node.children if c.kind == "args"), None)
            items = args_node.children if args_node is not None else []
            # **尺寸要推导，不许写 `n(?)`**（与 stdlib 同一条）。
            n = self._container_expr_size(items[0]) if items else None
            if n is None:
                _what = (_expr_text(items[0]) if items else "（无实参）")
                return self._unknown_size(
                    f"`{name}(…)` 的第一个实参 `{_what}` 长度推不出"
                    f" ⇒ 这一项退 ⊤，不猜")
            core = Cost.mul(n, Cost.log2(n))
            if name == "sort_by" and len(items) > 1 and items[1].kind == "path":
                cmp_name = items[1].name or ""
                # **比较器也走 κ**（与普通调用同一条修法）：`target` 只是声明，
                # 拿它乘 `n log n` 会让排序的 κ 跟着比较器的声明一起虚低。
                # `_callee_kappa` 已含比较器入口的按值拷贝，别再补。
                kc = (self._callee_kappa(cmp_name, None, env)
                      if cmp_name in self.fns else None)
                if kc is None:
                    kc = self.target_cost(cmp_name)
                    if kc is not None:
                        self._note_target_trusted(cmp_name)
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
            # **正在算叶子层**（`_rec_leaf`，P0-R）：这条路径上没有发生这次调用
            # ⇒ 整条（含实参求值）记 0。返回 0 只会让叶子层**更小**，而 `_block`
            # / `if` 的 add / max 组合保证结果仍是「任何自调用-free 路径」的上界。
            if self._rec_leaf_root == short:
                return Cost.num(0)
            # 正在算 B0（函数体只算一次）：把**本函数**的递归切成 `1 + Σ实参`，
            # 只保留每层都要付的调用开销——这正是 B0 的定义。
            if self._rec_cut_root == short:
                # **必须"直接在它自己的函数体里"**（2026-11-12）：调用栈顶就是
                # 它自己 ⇒ 这是那个自调用点本身。若栈顶是别的函数
                # （`f → helper → f`），这条边是**绕回来的间接递归**，它的深度
                # 没有被 `D ×` 覆盖（乘数只乘了一遍），切掉就是**少算**。
                # 与下面那条"两个函数的深度不能只乘一个"同一个理由，只是这里
                # 绕回来的名字相同，旧实现会静默切掉。
                if self.frames and self.frames[-1] != short:
                    raise CostError(
                        f"间接递归（{self._rec_cut_root} 经 {self.frames[-1]} "
                        f"又回到自己）：这条边的深度不在「一层 × 深度」的乘数里，"
                        f"暂不建模")
                return Cost.add(Cost.num(OP_COST), self._args_cost(node, env))
            if self._rec_cut_root is not None:
                raise CostError(
                    f"间接递归（{self._rec_cut_root} 与 {short} 在同一个环上）："
                    f"两个函数的深度上界不能只乘一个，暂不建模")
            _d = self.depth_of(short)
            # **深度**有四条来源，优先级按"能不能用"来：
            #   ① `.tu` 的 `recursion depth <= N` **且**调用树退化成一条链
            #      （`_rec_linear`）→ `D × 单层`；
            #   ② visited 守卫（②，2026-11-12）→ **激活数上界** `n(标记容器)`，
            #      它把"自调用在循环里、每层跑多次"直接盖住，不要求链；
            #   ②' 区间对半分（`_halving_span`，2026-11-12）→ **激活数上界**
            #      `2 × (hi - lo)`：一条路径 2 个自调用点，但拿的是互不相交的
            #      两半（`merge_sort`）。这是**可证明的**上界，见 `_halving_span`
            #      小节头的推导；`fib` / `place` 的形状过不了那套判据。
            #   ③ 只剩 ①、但形状是树递归 ⇒ ⊤（`fib` 那类：指数记成线性会破界）。
            _dc = None
            if _d is not None and self._rec_linear(short):
                _dc = Cost.num(_d)
            if _dc is None:
                _dc = self._visited_depth(short)
            _hv = self._halving_span(short)
            if _dc is None:
                if _hv is not None:
                    # 这一处自调用拿到的子树区间 ≤ 父区间的一半
                    # ⇒ 子树激活数 ≤ 2×ceil((hi-lo)/2) ≤ (hi-lo) + 1。
                    # **只作 `_old` 那条退路的下限**：真正进 κ 的是下面
                    # `_halving_level_sum` 的按层求和（`_deep` 被它替换），
                    # 否则「激活数 × 单层」会把每层都乘入口长度 ⇒ O(n²)。
                    # `hi - lo` 是**本帧形参**上的尺寸量（`sym`），调用点
                    # 由 `_callee_kappa` 按实参换成调用方的区间长度。
                    _dc = Cost.add(
                        Cost("sym", size_diff_name(Cost("sym", _hv[1]),
                                                  Cost("sym", _hv[0]))),
                        Cost.num(OP_COST))
            _tree_act: Optional["Cost"] = None
            if _dc is None:
                # ④ **树递归 + 每层分叉数可推**（2026-11-12，`10_backtrack` /
                # `place`）：`recursion depth <= D` 是字面量、每个自调用点都在
                # 轮数可推的循环体里 ⇒ 激活数 ≤ `(D+1)·B^D`。见
                # `_tree_activation_bound` 的判据与推导。
                _tree_act = self._tree_activation_bound(short)
                _dc = _tree_act
            if _dc is not None:
                _fn = self.fns.get(short)
                _tab = self._param_size_table(_fn, node) if _fn is not None else {}
                # **这一支用推导口径**（`derive=True`）：它最终取
                # `max(旧口径一层, D × 体一次) + 叶子层`，而 `D × 体一次` 本来就是
                # **推出来的** κ（自己数了每个操作），不依赖 target 的常数项。
                # 用旧口径会把**局部容器名**（`n(out)`）留在 κ 里 → E23。
                _args = self._args_cost(node, env)
                # **修前那一层**（有 target 时就是 `1 + target + 实参`），
                # 取 max ⇒ 任何调用的 κ 都单调不减。
                _old = Cost.add(Cost.num(OP_COST), _args)
                # **直接自调用不看自己的 target**（2026-11-12）：`target` 是
                # **正在被验证的那条承诺**。把它当自调用的下界会形成
                # `κ = max(κ_target, 推导值) × 外层` 的循环——声明的阶越高，
                # 算出来的 κ 阶越高，永远判不成「达成」：
                #   `place` 写 `O(n(recv.n_)^10)`（κ 的真实阶也是 10）⇒
                #   自调用被抬到 n^10、外层再乘一次 n ⇒ κ = n^11 ⇒ 判**债务**，
                #   而这是**假债务**。承诺写多松都不该改变「量出来的成本」。
                # 只跳过「栈顶就是它自己」这种**直接**自调用；间接递归
                # （f→g→f）维持原口径——那条边的深度本来就没被 `D ×` 覆盖。
                _t = (self.target_cost(short)
                      if not (self.frames and self.frames[-1] == short) else None)
                if _t is not None:
                    _old = Cost.max(_old, Cost.add(
                        Cost.add(Cost.add(Cost.num(OP_COST), _t.substitute_sizes(_tab)),
                                 self._callee_entry_copy(_fn, node)), _args))
                # **④ 的那一帧**：`_rec_once` / `_rec_leaf` 会把函数体再走一遍，
                # 里面就包括 `while (col < recv.n_)` 的轮数推导。成员界只在
                # `_tree_frame` 这一帧里走**逐字段**证明（`_tree_field_ok`），
                # 否则它们会被保守规则判成 ⊤，整条 κ 跟着 ⊤。
                _tf_prev = (self._tree_frame, self._tree_fn)
                if _tree_act is not None:
                    self._tree_frame = short
                    self._tree_fn = _fn
                try:
                    _once = self._rec_once(short).substitute_sizes(_tab)
                    # **`κ(rec) ≤ D × 单层 + 叶子层`**（P0-R）。单层 = `_rec_once`
                    # （自调用切成 `1+Σ实参`，含每层真实要付的调用开销）；叶子层 =
                    # `_rec_leaf`（自调用整条切 0）。**两层都不能少**：
                    #   ① 每层的 `1+Σ实参` 在解释器里是真实操作，记 0 会破界；
                    #   ② 最后一层不再调用，但它走的分支（`return xs.length()` 之类）
                    #      要钱——修前用 `1+Σ实参` 顶它，叶子更重时就不够了。
                    # 两项取 max 作为尾部：`max(1+Σ实参, 叶子层)` 同时盖上两种情况。
                    _leaf = self._rec_leaf(short).substitute_sizes(_tab)
                finally:
                    self._tree_frame, self._tree_fn = _tf_prev
                # 深度里的**标记容器**是被调方的形参名：调用点要换成调用方的容器
                # （`n(color)` → `n(g)` 或常数），不然求值时查不到 ⇒ 低估。
                # 常数深度（`recursion depth <= 8`）不受替换影响。
                _dc = _dc.substitute_sizes(_tab)
                _tail = Cost.max(Cost.add(Cost.num(OP_COST), _args), _leaf)
                _deep = Cost.add(Cost.mul(_dc, _once), _tail)
                # **按层求和**（2026-11-12 夜）：`T(s) = 2·T(s/2) + 单层(s)`
                # 的真阶是 `Θ(s log s)`；`激活数 × 单层` 把**每一层**都乘上
                # 入口区间的长度 ⇒ `2s × (A + B·s) = O(s²)`。单层拆成
                # `A + B×s` 后逐层求和，得到 `(2s+1)·A + B·s·(log2(s+1)+1)`。
                # 拆不出（单层里有非线性 `s`）就**留旧口径**（宁松不破界）。
                if _hv is not None:
                    _s_cost = Cost("sym", size_diff_name(
                        Cost("sym", _hv[1]), Cost("sym", _hv[0])))
                    # **子树区间 ≤ 父区间的一半**（`mid` 是中点，见
                    # `_is_halving_mid`）：`ceil(s/2) = (s+1)/2`。两个自调用点
                    # 各按这个长度求和，加起来正好是整棵树的
                    # `2s·A + B·s·log2(s)`。用父区间长度记也成立，但白松一倍。
                    _s_half = Cost.div(Cost.add(_s_cost, Cost.num(1)),
                                       Cost.num(2))
                    _lv = self._halving_level_sum(_once, _s_half,
                                                  str(_s_cost.value))
                    if _lv is not None:
                        _deep = Cost.add(_lv, _tail)
                return Cost.max(_old, _deep)
            if _d is not None:
                # **树递归且没有 visited 守卫** ⇒ ⊤：`D × 体一次` 只对**链**成立。
                # `fib_rec` 的 `fib(n-1)+fib(n-2)` 被记成 `D × 体一次`（2,064）
                # 而实测 31,926 —— 指数当加性，κ 不再是上界。
                self._note_tree_rec(short)
                return Cost.top()

        # ① **有实现 → 用算出来的 κ**（不是它的 target）。
        #
        # 2026-11-09 修：以前这里是「有 target 就用 target」。`target` 是作者写的
        # **承诺**，κ 是工具**算出来的上界**；拿承诺去合成上界，等于让「写松一个
        # target」把整条调用链的 κ 一起拉低：
        #   · `_tier_b/sha256`：`compress` 声明 `O(1)`、实际每块 ~18k ops，
        #     `sha256(1KB)` 的 κ = 21,566，实测 308,126（**少 14 倍**）；
        #   · `_probe_calltarget`：`inner` 声明 `O(1)`、κ(inner)=16，
        #     调用方 `outer` 每轮只记 9 —— κ(outer)=184 < 实测 427。
        #
        # 现在调用方**只信推导结果**：`_callee_kappa` 返回的就是 `cost` 打印的
        # 那个 κ（已按实参换尺寸、已含入口拷贝）。**不要**再加
        # `_callee_entry_copy`——那是 target 那条路的补丁，加了就是双计。
        #
        # 递归调用点不在这里：有 `recursion depth` 的走上一个分支
        # （`_rec_once` × 深度，同样是推导值）；没有深度的落到 ②。
        fn = self.fns.get(short)
        if fn is not None and short not in self.frames:
            _k = self._callee_kappa(short, node, env)
            if _k is not None:
                return Cost.add(Cost.num(OP_COST),
                                Cost.add(_k, self._args_cost(node, env)))

        # ② **没有推导值可用**：只有 spec 没有实现 / 递归但没有 `recursion
        #    depth`（展开自己会无穷递归）。此时 `target` 是**唯一**信息 ——
        #    这是**被迫信任声明**，不是「算出来的上界」，所以记一笔
        #    `self.target_trusted`（标注），报告里能一眼指出哪些调用点靠声明。
        t = self.target_cost(short)
        if t is not None:
            self._note_target_trusted(short)
            # target 也是用**被调函数的形参名**写的，同样要按实参实例化。
            # **用旧口径 `derive=False`**：target 是 big-O 承诺，隐含常数项；
            # 把形参尺寸换成退化实参的表达式（如返回空容器的调用 → 0）会把
            # 常数一起抹掉，实测会让 κ 掉到 ops 以下（`down_nested`）。
            _fn = self.fns.get(short)
            _tab = (self._param_size_table(_fn, node, derive=False)
                    if _fn is not None else {})
            # **实参要算**：`f(compute(xs))` 的 `compute(xs)` 是**调用方**求值的。
            # target 描述的是**被调函数自己**的成本（用形参尺寸写），
            # 里面不含调用方的实参表达式 ⇒ 两者相加**不是**双重计费。
            return Cost.add(Cost.num(OP_COST),
                            Cost.add(Cost.add(t.substitute_sizes(_tab),
                                              self._callee_entry_copy(_fn, node)),
                                     self._args_cost(node, env)))

        # ③ 连 target 都没有 → 内联（真有环就报错，不猜）
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

        **坑①（设计 §8.4）**：`?!` 候选有**自己的签名**（`kernels L(...) -> T`），
        不能拿宿主的返回类型当候选的。以前 `_block` 直接在宿主帧里跑，A-full 的
        返回/形参拷贝会按**宿主**的签名判定（影子模型踩过：`stats.summarize`
        凭空 +4）。这里给候选压一帧：形参/返回类型取自 `kernels` 头，
        宿主帧的局部名作**兜底**（候选默认内联，能读宿主局部）。
        """
        out: Dict[str, Cost] = {}
        host = self._cur_frame()
        for kern in _descendants(self.mod, "kernels"):
            if (kern.name or "") != label:
                continue
            for cand in kern.children:
                if cand.kind != "candidate":
                    continue
                decls = dict(host["decls"])          # type: ignore[arg-type]
                consts = set(host["consts"])         # type: ignore[arg-type]
                locals_ = set(host["locals"])        # type: ignore[arg-type]
                # ---- 候选自己的签名（不是宿主的）
                pnode = next((c for c in (kern.children or [])
                              if getattr(c, "kind", "") == "params"), None)
                for prm in (pnode.children if pnode is not None else []):
                    ty = prm.children[0] if (prm.children or []) else None
                    if getattr(prm, "name", None):
                        decls[prm.name] = ty
                        if not _is_ref_type(ty):
                            locals_.add(prm.name)
                for b in [x for x in cand.children if x.kind == "block"]:
                    for d in (_descendants(b, "const-decl")
                              + _descendants(b, "var-decl")
                              + _descendants(b, "val-decl")):
                        if not getattr(d, "name", None) or not (d.children or []):
                            continue
                        decls[d.name] = d.children[0]
                        (consts if d.kind == "const-decl" else locals_).add(d.name)
                self._frames.append({"decls": decls, "consts": consts,
                                     "locals": locals_,
                                     "ret": _ret_type_of(kern)})
                try:
                    c = Cost.num(0)
                    for b in [x for x in cand.children if x.kind == "block"]:
                        c = Cost.add(c, self._block(b, dict(env)))
                finally:
                    self._frames.pop()
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
    if b.kind == "ceildiv":
        # `while (i + 3 <= row.length())` 的分子是局部行名时归一到外层容器：
        # `ceil(n(m) / 3)`。漏掉这一支就会留下 `n(row)`（求值按 1 算 ⇒ 偏小）。
        return Bound("ceildiv", value=b.value, left=_substitute_size(b.left, row_of))
    return b


def _only_grows_init(fn: Optional[Node], name: str) -> Optional[Node]:
    """`name` 的**声明初值**——只在能保证它「只增不减」时返回，否则 None（不猜）。

    用途只有一个：把 `while (j < hi)` 的圈数上界 `hi - j` 收紧成 `hi - 初值`
    （即 `hi - lo`），这样 target 里的 `hi - lo` 才有**同一个尺寸符号**可对。

    三条前提（与 `monotone_decreasing` 同一套保守口径，只是方向相反）：

      ① 全函数里**只有一处**声明，且声明处有初值（`u64 j;` 没有初值 → 不猜）；
      ② 对它的每一次写都是 `j = j + <e>` 或 `j += <e>`——**只增**；
         `j = j - 1` / `j = 0` / `j = 5` 一律不换（换过去就不再是上界）；
      ③ 没有按引用传出去（`f(&j)`）、也没有当方法接收者（`j.pop()`）：
         那些地方可能把它改小，而从函数体看不出来。

    任何一条不满足 → None。**松一点没关系（退回 `hi`），紧错一点就不是上界了。**
    """
    if fn is None or not name:
        return None
    inits: List[Node] = []
    for k in ("var-decl", "const-decl"):
        for d in _descendants(fn, k):
            if getattr(d, "name", "") != name:
                continue
            if len(d.children or []) < 2:
                return None                 # 声明了但没有初值
            inits.append(d.children[-1])
    if len(inits) != 1:
        return None
    for a in list(_descendants(fn, "assign")) + list(_descendants(fn, "assign-op")):
        kids = getattr(a, "children", None) or []
        if not kids or getattr(kids[0], "kind", "") != "path" or kids[0].name != name:
            continue
        if getattr(a, "kind", "") == "assign-op":
            if (getattr(a, "value", "") or "") != "+=":
                return None
            continue
        rhs = kids[1] if len(kids) > 1 else None
        ok = False
        if rhs is not None and getattr(rhs, "kind", "") == "binary" \
                and (getattr(rhs, "value", "") or "") == "+":
            for side in (rhs.children + [None, None])[:2]:
                if getattr(side, "kind", "") == "path" and side.name == name:
                    ok = True
        if not ok:
            return None
    for r in _descendants(fn, "ref"):
        for p in _descendants(r, "path"):
            if getattr(p, "name", "") == name:
                return None                 # 可能被调用的函数改掉
    for mc in _descendants(fn, "method-call"):
        recv = mc.children[0] if getattr(mc, "children", None) else None
        if getattr(recv, "kind", "") == "path" and getattr(recv, "name", "") == name:
            return None
    return inits[0]


def _tighten_sub_start(b, cond, fn: Optional[Node]):
    """`sub(E, 计数器)` → `sub(E, 计数器初值)`；判不出就原样返回。

    **为什么能收**：`while (j < E)` 且 `j` 从 `v0` 起只增 ⇒ 圈数 ≤ `E - v0`
    （`E - j` 里的 `j` 在循环入口就等于 `v0`）。收紧后是**同一个上界**，
    只是把"计数器"换成了它的静态初值——`hi - lo` 这个尺寸符号才可能成立。
    """
    if b is None or cond is None or fn is None:
        return b
    # **`&&` 的组合界**（2026-11-12 夜）：`min` 两侧各自对应条件的一侧，分别
    # 收紧。`while (i < mid && j < hi)` 的两个计数器都从形参起、只增 ⇒ 两侧
    # 分别变成 `mid - lo` / `hi - mid`；不递归就只剩 `max(mid, hi)`——那是
    # **绝对下标**，乘上激活数就是 O(n²)。
    #
    # **`||` 不做**：`A || B` 的轮数上界是 `|A| + |B|`（不是 `max`），这里没有
    # 那套加法；收紧单侧只会让那个本就偏小的界更小。方向安全优先。
    if (getattr(cond, "kind", "") == "binary"
            and (getattr(cond, "value", "") or "") == "&&"
            and getattr(b, "kind", "") == "min"):
        _kids = list(getattr(cond, "children", None) or [])
        if len(_kids) >= 2 and b.left is not None and b.right is not None:
            return Bound(b.kind,
                         left=_tighten_sub_start(b.left, _kids[0], fn),
                         right=_tighten_sub_start(b.right, _kids[1], fn))
    if getattr(b, "kind", "") != "sub" or getattr(b, "right", None) is None:
        return b
    low = b.right
    if getattr(low, "kind", "") != "var" or not getattr(low, "value", ""):
        return b
    name = low.value
    # 守卫必须就是 `计数器 < E` / `计数器 <= E`：`sub(E, 计数器)` 才是"走到 E
    # 还差多少"。`E > 计数器` 这类写法走 `_sub_or_plain` 的 `>=` 分支，
    # 左边不是计数器，这里不碰（保守）。
    if not (getattr(cond, "kind", "") == "binary"
            and (getattr(cond, "value", "") or "") in ("<", "<=")
            and (getattr(cond, "children", None) or [])
            and getattr(cond.children[0], "kind", "") == "path"
            and getattr(cond.children[0], "name", "") == name):
        return b
    init = _only_grows_init(fn, name)
    if init is None:
        return b
    ib = expr_to_bound(init)
    if ib is None:
        return b
    if getattr(ib, "kind", "") == "var" and ib.value == name:
        return b                            # `j = j` 之类：没有新信息
    return Bound("sub", left=b.left, right=ib)


def bound_of_condition_wrapper(st: Node, repeats_of, env, row_of=None,
                              capacities=None, const_names=None, cur_fn=None,
                              resolve=None, size_alias_skip=None):
    """把 env 里的两件事告诉 G29 的提取器：

      · 初值为 0 的变量（决定上界写成 `e` 还是 `e - i`）
      · 尺寸别名（`const n = scores.length();` → 上界里出现 n 时换成 scores.length()）

    `resolve(name) -> Cost|None`：**按初值 AST** 求这个名字的值（实例口径，
    调用方传 `CostInterp._name_count`）。给了它，"常数局部"就只认**初值真的是
    常数**的名字；不给则退回读 `env`（兼容旧调用点，但那正是 2026-11-09 的
    反向 bug 所在——`as_cost` 认不出的表达式曾被记成 `num(0)`，`env` 里
    于是有一个**假的常数 0**，`while (x < total)` 的轮数直接变成 0）。

    `size_alias_skip`：**写过就不能再当尺寸别名**（`u64 m = xs.size(); m = m+1;`）。
    它是"写过名字"的集合；表里这些名字不替换，落到旧口径（`n(m)`），
    **不会**继续拿声明处的 `n(xs)` 当新界（2026-11-12）。
    """
    from .bound import (bound_of_condition, monotone_decreasing, substitute,
                        progresses, _log_loop, exact_step_prover)
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
    # **位宽有界**（2026-11-12）：`while (x != 0) { x = x & (x - 1); }` /
    # `x = x >> 1` / `x = x / 2` 的圈数 ≤ 类型位宽（与数据无关的常数上界）。
    # 命中 `x != 0` 但形状证不出时，把具体拒因透出去（`!=` 在下游没有分支，
    # 否则只会得到「条件形态 binary 不可推」）。
    from .bound import width_bounded_loop as _width_bounded_loop
    _wb, _wb_why, _wb_hit = _width_bounded_loop(st, cur_fn)
    if _wb is not None:
        return _wb, _wb_why
    if _wb_hit:
        return None, _wb_why
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
    # **步长证明器**（2026-11-10）：`while (i + 3 <= n)` 里 `i` 初值 0、每轮恰增 3
    # 时，圈数是 `ceil(n / 3)` 而不是 `n`。证明要看循环体，所以在这里把 `st` 与
    # `cur_fn` 包进去；证不出就沿用 `n`（`step_of` 返回 False 那一支）。
    b, why = bound_of_condition(cond, repeats_of, zv_all, set(dec), dec,
                                exact_step_prover(st, cur_fn),
                                nonnegative_vars(cur_fn), cur_fn, st)
    # **起点收紧**（2026-11-10）：`while (j < hi)` + `j` 从 `lo` 起只增 ⇒ 圈数
    # 是 `hi - lo`（不是 `hi`）。能证明 `lo <= hi` 时它会变成**尺寸量**
    # （符号名 `hi - lo`，见 `size_diff_cost`），与 target 侧同名；证明不了就
    # 退回 `hi`——旧口径、仍是上界，只是松。
    if b is not None:
        b = _tighten_sub_start(b, cond, cur_fn)
    if b is not None:
        _skip = set(size_alias_skip or ())
        table = {k: Bound("size", value=v.value)
                 for k, v in env.items() if v.kind == "size" and k not in _skip}
        # **E22 / 常数别名**：只有**初值真的是常数**的局部才能进替换表，
        # 否则守卫里的 `i < total` 会退化成 `n(total)` 这种局部尺寸名。
        #
        # 2026-11-09 修：**旧写法读 `env`**（`v.is_const()`），而 `as_cost`
        # 对认不出的表达式曾经返回 `num(0)` —— `const u64 total =
        # a.length()+b.length()` 在 `env` 里就是"常数 0"，于是上界被替换成
        # **0**，κ 变成 23 那种小常数还判"达成"（真实轮数是 `n(a)+n(b)`）。
        # 现在有 `resolve` 就**按初值 AST 重新求**：`const u64 n = 1000` 仍是
        # `const 1000`，尺寸表达式展开成 Cost 表达式（`num` 以外 → 不进表，
        # 留给 `_bound_to_cost_scoped` 结构展开），认不出的 → 不进表（⊤）。
        if resolve is not None:
            _ctab = {}
            for _nm in (const_names or ()):
                _c = resolve(_nm)
                if _c is not None and _c.is_const():
                    _ctab[_nm] = Bound("const", value=_c.value)
            table.update(_ctab)
        else:
            table.update({k: Bound("const", value=v.value)
                          for k, v in env.items()
                          if v.is_const() and k in (const_names or ())})
        b = substitute(b, table)
        # 局部行别名：`row` 的尺寸归一到外层容器（`n(m)`）。
        # **单独一遍**，因为 `substitute` 只换 `var` 节点，而 `row.length()`
        # 已经变成 `size(row)` 了。直接改 substitute 会波及所有尺寸替换，风险更大。
        b = _substitute_size(b, row_of or {})
        b = _substitute_capacity(b, capacities or {})
    # **区间减半**（2026-11-13）：`while (lo < hi) { mid = lo + (hi-lo)/2; … }`
    # 的轮数是 `log2(s₀)+1`（`hi = xs.size()` 时就是 `log2(n(xs))+1`），不是旧
    # 的线性界。判据只此一份（`bound.halving_interval_loop`）：`check` 与 `cost`
    # MUST NOT 给出相反结论。**取 min，且只在证得出更小时生效**——旧界不可判定
    # （含裸标量 `x` / `hi`，求值侧按 ∞）时取新界；旧界是尺寸量界时保持旧界，
    # 绝不比改前松（`cost_baseline` 的判据）。
    from .bound import halving_interval_bound as _halving_bound
    b, why = _halving_bound(b, why, st, cur_fn)
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


def _call_args(n: Optional[Node]) -> list:
    """调用节点的实参列表（没有 `args` 就是空表）。"""
    if n is None:
        return []
    args = next((c for c in (getattr(n, "children", None) or [])
                 if getattr(c, "kind", "") == "args"), None)
    return list(getattr(args, "children", None) or [])


def _target_literal_fraction(n: Optional[Node]) -> Optional[Fraction]:
    """把 target 里的**指数**读成有理数：`3`、`2/3`、`0.5`。

    **只认字面量（以及对字面量的 `/`）**：指数必须是编译期常数。若允许
    `O(n^k)`（k 是形参），阶就成了"关于 k 的表达式"，`_profile` 的
    `(次数, log 因子数, loglog 因子数)` 就没有值可填——那需要另一套符号阶
    代数，不在这一步。

    负指数返回 `None`：成本恒非负、随尺寸单调不减，`n^-1` 不是任何成本的
    上界，收下它就是给一个假承诺。`x/0` 同样返回 `None`。
    """
    if n is None:
        return None
    while getattr(n, "kind", "") == "paren" and n.children:
        n = n.children[0]
    n = _unwrap_obig(n)
    while getattr(n, "kind", "") == "paren" and n.children:
        n = n.children[0]
    k = getattr(n, "kind", "")
    if k == "lit-int":
        try:
            v = Fraction(int(n.value))
        except (TypeError, ValueError):
            return None
        return v if v >= 0 else None
    if k == "lit-float":
        try:
            v = Fraction(str(n.value))
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        return v if v >= 0 else None
    if k == "binary" and (n.value or "") == "/" and len(n.children) >= 2:
        a = _target_literal_fraction(n.children[0])
        b = _target_literal_fraction(n.children[1])
        if a is None or b is None or b == 0:
            return None
        return a / b
    return None


def _target_const_fraction(n: Optional[Node]) -> Optional[Fraction]:
    """**只读字面量**（含一元负号、字面量的 `/`）→ `Fraction`；否则 `None`。

    与 `_target_literal_fraction` 的唯一区别：**不收负号、不判非负**——
    它只负责"这是不是一个数"，"这个数能不能当指数"由
    `_target_star_star_exponent` 按 `Cost.pow` 的规则判。两者分开，
    是为了让"不是常数"与"常数但非法（负 / 分母 0）"给出不同的说法。
    """
    if n is None:
        return None
    while getattr(n, "kind", "") == "paren" and n.children:
        n = n.children[0]
    n = _unwrap_obig(n)
    while getattr(n, "kind", "") == "paren" and n.children:
        n = n.children[0]
    k = getattr(n, "kind", "")
    if k == "lit-int":
        try:
            return Fraction(int(n.value))
        except (TypeError, ValueError):
            return None
    if k == "lit-float":
        try:
            return Fraction(str(n.value))
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    if k == "unary" and (getattr(n, "value", "") or "") == "-" and n.children:
        v = _target_const_fraction(n.children[0])
        return None if v is None else -v
    if k == "binary" and (getattr(n, "value", "") or "") == "/" and len(n.children) >= 2:
        a = _target_const_fraction(n.children[0])
        b = _target_const_fraction(n.children[1])
        if a is None or b is None or b == 0:
            return None
        return a / b
    return None


def _target_has_var(n) -> bool:
    """表达式里有没有**变量**（裸名字 / 调用 / 下标 / 字段）。

    只用来给"指数为什么不行"分档：`n ** k`（有变量 → "不是常数"）与
    `n ** 2 ** 3`（纯常数式、只是没折叠 → "常数式，请写折叠后的数"）
    是两件事，同一句"不是常数"会把后者说错。
    """
    if n is None:
        return False
    if getattr(n, "kind", "") in ("path", "method-call", "call",
                                  "index", "field", "ref", "deref"):
        return True
    return any(_target_has_var(c) for c in (getattr(n, "children", None) or []))


def _target_star_star_exponent(n: Optional[Node], op: str = "**"):
    """`target` 里 `a ** b` / `a ^ b` 的指数：`(Fraction, None)` 或 `(None, 原因)`。

    `op` 只影响**报错文案**（target 里两种写法都当幂，见 `_target_cost`）；
    判据与 `pow(x, p/q)` 完全一致：只收**编译期常数**——字面量（`2`、`0.5`）
    或字面量的分数（`2/3`）。指数是变量时阶就成了"关于该变量的表达式"，
    判阶表 `(次数, log 因子数, loglog 因子数)` 没有值可填，只能退 ⊤ ——
    **不许猜**（κ 必须是上界）。

    与 `pow` 的区别只有一条：**把"为什么"说出来**。`pow(n, k)` 走的是
    通用提示（"支持 …"），而新开的幂这条路要能回答"为什么 `n ** k`
    不行"；负指数 / 分母为 0 也要与"不是常数"分开，否则等于把
    "常数但非法"说成"不是常数"。
    """
    # 正路：与 `pow` 用同一个读法（`2`、`0.5`、`2/3` 逐字一致）。
    e = _target_literal_fraction(n)
    if e is not None:
        return e, None
    # 走到这里说明 `_target_literal_fraction` 拒了它 —— 分类说清原因。
    if n is None:
        return None, "指数为空"
    _n = n
    while getattr(_n, "kind", "") == "paren" and _n.children:
        _n = _n.children[0]
    _n = _unwrap_obig(_n)
    while getattr(_n, "kind", "") == "paren" and _n.children:
        _n = _n.children[0]
    k = getattr(_n, "kind", "")
    txt = _expr_text(_n)
    val = _target_const_fraction(_n)
    if val is not None and val < 0:
        # **负指数不是上界**：`x ** -1` 随尺寸变小，与 `Cost.pow` 同一条规则
        # （那里退 ⊤）。这里退 BadForm，但把原因写在诊断里。
        return None, (f"指数 `{txt}` 是**负数**：成本随尺寸单调不减，"
                      f"`x ** 负数` 反而随尺寸变小，不是任何成本的**上界**"
                      f" ⇒ 不猜，退 ⊤（与 `pow(x, -1)` 同一条规则）")
    if k == "binary" and (getattr(_n, "value", "") or "") == "/" \
            and len(_n.children) >= 2:
        den = _n.children[1]
        dv = _target_const_fraction(den)
        if dv is None:
            return None, (f"指数的**分母** `{_expr_text(den)}` 不是常数："
                          f"`x ** (a / b)` 里 `b` MUST 是常数，否则阶是"
                          f"关于 `b` 的表达式（判阶表没有值可填）⇒ 不猜，退 ⊤")
        if dv == 0:
            return None, (f"指数的分母是 0（`{txt}`）：不是数 ⇒ 不猜，退 ⊤")
    if not _target_has_var(_n):
        # **常数式但不折叠**：`n ** 2 ** 3`（`.eln` 里是 `n ** 8`）这类。
        # 说"不是常数"就是**错话**——它是常数，只是不在这条路的收取形状里。
        return None, (f"指数 `{txt}` 是**常数式**，但 `{op}` 的指数只收字面量或"
                      f"常数分数（`2`、`0.5`、`2/3`），不折叠复合常数式；"
                      f"请直接写折叠后的数（如 `n {op} 8`）⇒ 不猜，退 ⊤")
    return None, (f"指数 `{txt}` **不是常数**：`{op}` 的指数 MUST 是字面量或"
                  f"常数分数（如 `2`、`0.5`、`2/3`）；指数是变量时，阶是"
                  f"「关于该变量的表达式」，判阶表没有值可填"
                  f" ⇒ 不猜，退 ⊤")


def _expr_text(n: Optional[Node]) -> str:
    """把成本式节点印回**能贴进报错**的一行文本。

    节点没留原文，只留 kind/name/value/children，所以按结构重拼；
    认不出的 kind 退回 `<kind>`，**不为了好看而编一个式子**。
    """
    if n is None:
        return "?"
    k = getattr(n, "kind", "")
    if k in ("lit-int", "lit-float", "path"):
        v = getattr(n, "value", None)
        return str(v if v is not None else (getattr(n, "name", "") or "?"))
    if k in ("call", "method-call"):
        args = next((c for c in (getattr(n, "children", None) or [])
                     if getattr(c, "kind", "") == "args"), None)
        inner = ", ".join(_expr_text(a) for a in (getattr(args, "children", None) or []))
        nm = getattr(n, "name", "") or "?"
        # **方法调用的接收者要印出来**（2026-11-10）：只印 `length()` 会把
        # `xs.length() - 1` 报成 `length() - 1`，作者看不出是哪个容器。
        if (k == "method-call" and (getattr(n, "children", None) or [])
                and getattr(n.children[0], "kind", "") == "path"
                and getattr(n.children[0], "name", "")):
            return f"{n.children[0].name}.{nm.split('.')[-1]}({inner})"
        return f"{nm}({inner})"
    if k == "binary":
        kids = list(getattr(n, "children", None) or [])
        if len(kids) >= 2:
            return f"{_expr_text(kids[0])} {getattr(n, 'value', '?')} {_expr_text(kids[1])}"
        return "?"
    if k == "paren":
        kids = list(getattr(n, "children", None) or [])
        return f"({_expr_text(kids[0])})" if kids else "?"
    if k == "unary":
        kids = list(getattr(n, "children", None) or [])
        op = getattr(n, "value", "") or ""
        return f"{op}{_expr_text(kids[0])}" if kids else "?"
    if k == "ref":
        kids = list(getattr(n, "children", None) or [])
        return f"&{_expr_text(kids[0])}" if kids else "&"
    if k == "index":
        kids = list(getattr(n, "children", None) or [])
        return f"{_expr_text(kids[0])}[{_expr_text(kids[1])}]" if len(kids) >= 2 else "?"
    return f"<{k}>"


def target_form_errors(decl_mod) -> List[Dict[str, object]]:
    """`.tu` 里**所有** spec 的 target 槽位中"认不出"的那些。

    **给 `check` 用**：`target` 写了却解析不出来，MUST 报诊断——
    以前这种槽位被 `_find_slot` 静默跳过，`check` 说"没有诊断"、
    `cost` 说"spec 没有 time 的 target"，作者以为有界、其实什么都没约束。

    返回 `{fn, dim, slot, line, col, expr, message}` 的列表（已按槽位去重）。
    """
    if decl_mod is None:
        return []
    # **先做一次便宜的预扫**：没有 target 子句的模块直接返回，
    # 不为每个 `check` 都构造一遍解释器。
    _targets: List[tuple] = []
    for sp in _descendants(decl_mod, "spec"):
        name = getattr(sp, "name", None)
        if not name:
            continue
        body = next((c for c in (getattr(sp, "children", None) or [])
                     if getattr(c, "kind", "") == "spec-body"), None)
        for c in (getattr(body, "children", None) or []):
            if getattr(c, "kind", "") != "clause" or (getattr(c, "name", "") or "") != "target":
                continue
            for d in (getattr(c, "children", None) or []):
                if getattr(d, "kind", "") == "dim":
                    _targets.append((name, getattr(d, "name", "") or ""))
    if not _targets:
        return []
    try:
        ci = CostInterp(decl_mod, decl_mod)
    except Exception:                                  # noqa: BLE001
        # 构造失败时**不冒充"没有错误"**：交给调用方原有的形状检查兜底。
        return []
    for name, dim in _targets:
        try:
            ci.target_cost(name, dim)
        except Exception:                              # noqa: BLE001
            continue
    return [dict(e) for e in ci.target_errors]


# ---------------------------------------------------------------- 尺寸差（`hi - lo`）
#
# **递归函数最自然的上界就是「区间长度的平方 / 带 log」**：快排 `qsort(lo, hi)`
# 的 target 天然写成 `O((hi - lo) * (hi - lo))`，归并是
# `O((hi - lo) * log(hi - lo))`，二分是 `O(log(hi - lo))`。2026-11-10 之前
# 这些**一个都写不出来**：`-` 只回 `max(a, b)`（把 `hi - lo` 当成 `hi`——
# 那是**把契约悄悄放宽**），带括号时更直接认不出（`paren` 没有分支，
# 落到 `expr_to_bound` 也是 None），于是整条 target 被报 `E-Target-BadForm`。
#
# **语义（硬约束）**：`hi - lo` 只有在**能证明 `lo <= hi`** 时才是尺寸量——
# 否则它可能是负的，而负的"尺寸"不是任何成本的上界。证明来源只有一处：
# **spec 的 `requires`**（与 A1 的 `facts_of_spec` 同一份事实）。
# 证明不了 MUST 退 ⊤ 并说清原因：**不许当常数、不许当正数猜**。
#
# **怎么与 κ 对上**：同一个差值在两边渲染成**同一个尺寸符号**，名字取两个
# 操作数 `Cost.render()` 的拼接（`hi - lo`）。κ 侧的 `while (j < hi)`
# （`j` 从 `lo` 起只增）经 `bound_of_condition_wrapper` 把上界收紧成
# `hi - lo` 之后，两侧用的就是同一个名字（`Cost("sym", "hi - lo")`）。
# 名字对不上时的后果是「尺寸量不一致」退 ⊤（可见），**不是**假达成。

#: 差值尺寸量的**规范名分隔符**。两侧都从 `Cost.render()` 拼，保证同名。
DIFF_SEP = " - "


def _cost_symbol_name(c) -> Optional[str]:
    """`Cost` → `requires` 事实里的符号名（`size`/`sym` 取 value，整数取数字）。"""
    if c is None:
        return None
    k = getattr(c, "kind", "")
    if k in ("size", "sym"):
        return str(c.value or "") or None
    if k == "num":
        try:
            f = Fraction(getattr(c, "value", None))
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        # 只有整数才有"名字"（`Fraction(3,2)` 不是尺寸量）
        return str(f.numerator) if f.denominator == 1 else None
    return None


def _facts_reach(facts, small: str, big: str, limit: int = 256) -> bool:
    """`facts`（`{小: {大, …}}`，按传递闭包）里能从 `small` 走到 `big` 吗。"""
    if not facts or not small or not big:
        return False
    seen = {small}
    stack = [small]
    while stack:
        cur = stack.pop()
        for nxt in (facts.get(cur) or ()):
            if nxt == big:
                return True
            if nxt not in seen and len(seen) < limit:
                seen.add(nxt)
                stack.append(nxt)
    return False


def prove_le(c_lo, c_hi, facts) -> bool:
    """能证明 `c_lo <= c_hi` 吗？**只认结构恒真与 `requires` 事实**，不猜。

    认的四条：

      · 同一个式子（`render()` 相同）→ `x <= x`；
      · 两个常数 → 数值比；
      · 左边是常数 0 → 真（`u64` 上一切量都 ≥ 0，`x - 0 = x` 同一条）；
      · 右边是加法 → 任一项够大就行（成本各项非负）；
      · 两边都能翻成符号名（`size` / `sym` / 整数常量）且 `facts` 里有
        `小 → … → 大` 的链（`requires lo <= hi` ⇒ `lo → hi`）。
    """
    if c_lo is None or c_hi is None:
        return False
    if c_lo.render() == c_hi.render():
        return True
    if getattr(c_lo, "kind", "") == "num" and getattr(c_hi, "kind", "") == "num":
        try:
            return Fraction(c_lo.value) <= Fraction(c_hi.value)
        except (TypeError, ValueError):
            return False
    if getattr(c_lo, "kind", "") == "num" and c_lo.value == 0:
        return True
    # **右边是加法**：成本各项都非负，所以 `lo <= 某一项` 就够（不必证明
    # `lo <= 整个和`）。`O(xs.length() + hi - lo)` 的结构是
    # `(n(xs) + hi) - lo`——要证明 `lo <= n(xs) + hi`，用这一条就落到
    # `lo <= hi`（`requires`）上。不加这条，最常见的"和里带差值"写法会被拒。
    if getattr(c_hi, "kind", "") == "add":
        return any(prove_le(c_lo, part, facts) for part in c_hi.args)
    a, b = _cost_symbol_name(c_lo), _cost_symbol_name(c_hi)
    return bool(a and b and _facts_reach(facts, a, b))


#: 差值操作数里需要加括号的节点种类（`(n(xs) + hi) - lo` 才不会被读错）。
_DIFF_WRAP_KINDS = ("add", "mul", "div", "max")


def _diff_operand_text(c) -> str:
    """差值操作数的打印：复合式子加括号，单量原样。

    `xs.length() + hi - lo` 的结构是 `(n(xs) + hi) - lo`，符号名必须写成
    `(n(xs) + hi) - lo`——印成 `n(xs) + hi - lo` 会被读成另一个式子
    （而且与 κ 侧的名字对不上）。
    """
    t = c.render()
    if getattr(c, "kind", "") in _DIFF_WRAP_KINDS or _compound(c):
        return f"({t})"
    return t


def size_diff_name(c_hi, c_lo) -> str:
    """尺寸差的**规范符号名**：`hi - lo`。target 与 κ 两侧都从这里拼。"""
    return f"{_diff_operand_text(c_hi)}{DIFF_SEP}{_diff_operand_text(c_lo)}"


def size_diff_cost(c_hi, c_lo, facts) -> Optional["Cost"]:
    """把 `hi - lo` 造成**尺寸量**；证明不了 `lo <= hi` 就返回 None（不猜）。

    三条化简先走（它们都是等式，不是近似）：

      · `x - 0 = x`（u64 上 `x ≥ 0`）→ 直接还 `x`。**不造 `n(x - 0)`**：
        那是把"没有差值"写成差值，报告里会多出一层噪声（golden 门抓过）。
      · `x - x = 0`；
      · 两个常数 → 数值差（`Cost.num`）。

    真正要造符号时用 **`Cost("sym", 名字)`**，不是 `Cost.size(...)`：
    `n(...)` 的读法是"某个容器的尺寸"，而 `hi - lo` 是**派生出来的尺寸量**
    （两个量的差），写成 `n(hi - lo)` 会让读者以为 `hi - lo` 是个容器。
    `sym` 在 `_profile` / `_monomials` / `compare` 里与 `size` **完全等价**
    （都是"这个尺寸量的一次项"），只是渲染不同。
    """
    if c_hi is None or c_lo is None:
        return None
    # `x - 0 = x`：没有差值可表达，别造符号
    if getattr(c_lo, "kind", "") == "num" and c_lo.value == 0:
        return c_hi
    # `x - x = 0`
    if c_hi.render() == c_lo.render():
        return Cost.num(0)
    # 两个常数 → 直接算（负数不是尺寸量，但那是"证明"的一部分：由 prove_le 挡）
    if getattr(c_hi, "kind", "") == "num" and getattr(c_lo, "kind", "") == "num":
        try:
            d = Fraction(c_hi.value) - Fraction(c_lo.value)
        except (TypeError, ValueError):
            return None
        return Cost.num(d) if d >= 0 else None
    if not prove_le(c_lo, c_hi, facts):
        return None
    return Cost("sym", size_diff_name(c_hi, c_lo))


def bound_to_cost_inst(b, consts=None, scalars=frozenset(), facts=None) -> Cost:
    """`_bound_to_cost` 的实例级版本：常量表由参数传入，不读模块全局。

    `scalars` 是**标量形参**名（`dim` / `k` / `hi`）。它们在 target 里写成裸符号，
    在 κ 里也必须渲染成裸符号（`Cost("sym", name)`）——否则 `n(dim)` 与 `dim`
    被当成两个不同的尺寸量，正确的承诺会被判成超标（matmul / topk）。

    `facts` 是 spec `requires` 的事实（`{小: {大, …}}`）。给了它、且差值方向
    能证明时，`a - b` 才渲染成**尺寸量**（符号名 `a - b`）；否则沿用旧口径
    `a - b <= a`（只取左操作数）——那是**上界**（多算），不是猜。
    """
    consts = consts or {}
    scalars = set(scalars or ())
    _facts = facts or {}

    def go(x) -> Cost:
        k = x.kind
        if k == "const":
            try:
                return Cost.num(int(x.value))
            except (TypeError, ValueError):
                return Cost.num(1)
        if k == "size":
            return Cost.size(x.value)
        if k == "elem":
            # **元素长度上界**没有"实例级"的证明上下文（拿不到所在函数，
            # 无法核「外层容器是 const 形参、没被写/传出去」这条前提）⇒ ⊤。
            # 正常路径是 `_bound_to_cost_scoped`（它带 `fn`，会核前提）。
            return Cost.top()
        if k == "var":
            if x.value in consts:
                return Cost.num(consts[x.value])
            if x.value in scalars:
                return Cost("sym", x.value)
            # repeats 里的裸名字是圈数（标量），不是容器尺寸（见 bound.COUNT_TAG）。
            if is_count_var(x):
                return Cost("sym", x.value)
            return Cost.size(x.value)
        if k == "sub":
            # **`hi - lo`**：能证明 `lo <= hi` 时它是**非负尺寸量**，与 target
            # 用同一个名字；证明不了就退回 `a - b <= a`（上界，宁可多算）。
            if x.right is not None:
                _d = size_diff_cost(go(x.left), go(x.right), _facts)
                if _d is not None:
                    return _d
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
        if k == "ceildiv":
            # `ceil(e / c)` → `Cost.div(e, c)`（口径与 `_bound_to_cost_scoped`
            # 那一支逐字相同：见那里的注释——真圈数是 `floor(e/c) ≤ e/c`，
            # 所以精确有理数就是上界；`ceil` 只是渲染层的写法）。
            a = go(x.left)
            try:
                _d = int(x.value)
            except (TypeError, ValueError):
                return Cost.top()
            if _d < 1:
                return Cost.top()
            return Cost.div(a, Cost.num(_d))
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


def _is_zero_cost(c: Optional[Cost]) -> bool:
    """是不是常数 0（拆 `A + B×s` 时用来判"这一边不含 s"）。"""
    return (c is not None and getattr(c, "kind", "") == "num"
            and c.value == 0)


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
    """成本式里有没有**未解释的**符号。

    只有 `k(f)`（函数值参数）算未解释：它的成本要看调用点。
    `sym(hi)` / `sym(dim)` 是**尺寸类量**（标量形参），不是未知——
    把它们也当"未解释"会让 `_call_inner` 不敢信被调函数的 target，
    退回内联，κ 反而被撑大。
    """
    if c is None:
        return False
    if getattr(c, "kind", "") == "sym":
        return str(c.value or "").startswith("k(")
    for a in (getattr(c, "args", None) or []):
        if _has_unresolved_sym(a):
            return True
    return False
