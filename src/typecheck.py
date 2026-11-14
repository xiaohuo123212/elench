#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""typecheck.py —— 名字解析 + 类型检查（M1 的第一项交付）。

**为什么两件事放一个文件**：**名字解析的产物就是类型的载体**——
不知道 `Token` 是类型还是变量，就没法判断 `Token::Plus` 是什么类型。
分两个 pass 只是徒增一次遍历。

## 设计取舍：**未知类型与任何类型兼容**

一个只做"确凿错误"的检查器，比一个"到处误报"的检查器有用得多。

所以本检查器有 `UNKNOWN` 类型，它与任何类型都可兼容。**只有当两边都确定且不兼容时才报错。**

这样：
- **样例全部能过**（泛型、闭包等处一律 UNKNOWN）
  **E12 的精度边界就在这里**：UNKNOWN 保证「不误报」，代价是「会漏报」。
- **确凿的错误照样抓住**（返回 `u64` 的函数 `return true;`）

## 已实现的检查

| # | 规则 | 错误码 |
|---|---|---|
| T-1 | 未声明的名字 | `E-Name-Undefined` |
| T-2 | 同一作用域重复声明 | `E-Name-Duplicate` |
| T-3 | **遮蔽外层名字**（用户定：禁止） | `E-Name-Shadowed` |
| T-4 | 返回类型与签名不符 | `E-Type-Return` |
| T-5 | 实参类型与形参不符 | `E-Type-ArgMismatch` |
| T-6 | `?` 用在非 `Result` 上 | `E-Try-NotResult` |
| T-7 | `match` 臂不穷尽且无 `_` | `E-Match-NonExhaustive` |
| T-8 | 枚举变体不存在 | `E-Variant-Unknown` |
| T-9 | 赋值类型不符 | `E-Type-Assign` |
| T-10 | 条件不是 `bool` | `E-Type-Cond` |
| T-11 | struct / 元组当标量做运算、排序（无运算符重载） | `E-Type-StructOp` |
| T-12 | struct 字段不存在 / 元组没有 `first`/`second` | `E-Type-UnknownField` |
| T-13 | 成员函数不存在（本语言 struct 只有字段） | `E-Type-StructMethod` |
| T-14 | struct 字段是函数类型 / 调用函数类型字段 | `E-Type-FuncValueField` |
| T-15 | 模块常量初值不是常量表达式（装载时求不出） | `E-Const-NotConstant` |
| T-16 | 局部变量声明成 `T&`（本语言没有局部引用） | `E-Ref-Local` |
| T-17 | 标量 / 枚举 / 元组 / 函数类型的 `T&` 形参（写不回调用方） | `E-Ref-Scalar` |
| T-18 | `X::f(...)` / `X::V` 的 `X` 不是已知类型（`foo::print(…)` 被当成全局内建） | `E-Type-Unknown` |

**T-11…T-16 的共同点（2026-11-10）**：这些都是"`check` 全绿、`run` 才炸"
或"`run` 也不炸但结果是静默错的"那一类。判据全部是**静态已知**的类型，
没有理由留到运行期——所以一律在 `check` 阶段报，且消息里写清"能怎么做"。
"""
import stdlib

from typing import Any, Dict, List, Optional, Set, Tuple

import compat as _compat
from parser import Node
from typenames import canon, display, OLD as OLD_TYPE_NAMES

# ---------------------------------------------------------------- 类型

UNKNOWN = "?"          # 与任何类型兼容
NUMERIC = {"u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64"}
UNSIGNED = {"u8", "u16", "u32", "u64"}
FLOATS = {"f32", "f64"}
INT_WIDTH = {"u8": 8, "u16": 16, "u32": 32, "u64": 64,
             "i8": 8, "i16": 16, "i32": 32, "i64": 64}

#: **语言里真实存在的类型头名字**（判据表；`canon()` 之后再比）。
#:
#: 为什么需要这张表：`UNKNOWN`（`?`）的设计本意是"**推不出**的类型不误报"，
#: 但类型名是**写出来的**，不是推出来的——`u128 x = 1;` 里的 `u128` 不是
#: "推不出"，是"**根本没这个类型**"。以前它走进了 `ty_of_node` → `Ty("u128")`，
#: 于是只在赋值那一刻报一句 `E-Type-Assign 声明为 u128，初值是 i64`
#: （误导：读者会以为类型是真存在的、只是初值类型不对）；而不带初值的
#: `u128 x;` / `foo_t x;` 则**一句都不报**。2026-11-10 起一律报
#: `E-Type-Unknown`（见 `_check_type_names`）。
#:
#: 进这张表的条件：**解释器真的认识它**（`interp._default_value_of` /
#: 容器分派 / `typenames.CANON` 的旧名）。表外的名字要么是用户 `struct` /
#: `enum` / `error`（`_index_names` 收集），要么是泛型类型参数，要么就是
#: **不存在的类型**。
BUILTIN_TYPE_NAMES = frozenset({
    # 标量
    "u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64", "f32", "f64",
    "bool", "void",
    # 容器 / 结果（含 `typenames.CANON` 的旧名：Vec / FixedVec / Map / Set / Option）
    # `expected` 是 `Result` 的**唯一用户写法**（P4-0）；内部仍按 `Result` 判。
    "vector", "array", "fixedvec", "map", "set", "optional", "expected",
    "Result",
    "unordered_map", "unordered_set", "stack", "queue",
    # `deque` 是**已登记但未接线**的容器：放它进来，让
    # `E-Type-UnimplementedContainer`（checker）去报那一条，
    # 这里别再报"未知类型名"（一个名字只该有一条诊断）。
    "deque",
    # **stdlib 的 HashMap**：`map_new()` 的返回类型，**不是**语言的 `map<K,V>`。
    # 两者成本口径不同（哈希有界探测 O(1) vs 平衡树 O(log n)），所以要两个
    # 名义类型；`map_put` / `map_get` / `map_has` / `map_len` 只吃这一个。
    "HashMap",
})

#: `X::f(...)` / `X::V` 里**可以当限定词**的已登记命名空间。
#:
#: 本语言没有**用户**命名空间：`namespace X { … }` 在 `parser.parse_namespace`
#: 里被**展平**，里面的名字直接进全局作用域，`X::` 限定名不是合法形状。
#: 唯一留下的一层是 `std`——标准库（`std::printf` / `std::sort` / `std::move` …）
#: 登记在它下面。所以 `X` 的判据是「已知**类型名**」∪ 这张表。
QUALIFIED_NAMESPACES = frozenset({"std"})


class Ty:
    """一个类型。`kind` ∈ name / tuple / ref / ptr / addrof / null / fn / unknown。

    `ptr`：`T*`（`args[0]` 是**指向的类型**）。两个 const 维度分开记：

    | 写法 | `mut`（指向可变） | `pconst`（指针本身 const） |
    |---|---|---|
    | `T*` | True | False |
    | `const T*` | False | False |
    | `T* const` | True | True |
    | `const T* const` | False | True |

    `addrof`：`&x`（取地址）的**中间类型**。它不是"最终的指针"——`&x` 能
    交给 `T*` 形参 / `T&` 形参（显式借用标记），但**不能**直接赋给标量
    （`u64 y = &x;` 仍然报错）。`compatible` 里对这两个方向各给一条规则。
    `null`：`nullptr` 字面量，只与 `ptr` 兼容。
    """

    __slots__ = ("kind", "name", "args", "mut", "pconst")

    def __init__(self, kind: str, name: str = "", args=(), mut: bool = False,
                 pconst: bool = False):
        self.kind = kind
        self.name = name
        self.args = tuple(args)
        self.mut = mut
        self.pconst = pconst

    def __repr__(self) -> str:
        return self.render()

    def render(self) -> str:
        if self.kind == "unknown":
            return UNKNOWN
        if self.kind == "null":
            return "nullptr"
        if self.kind == "tuple":
            return "(" + ", ".join(a.render() for a in self.args) + ")"
        if self.kind == "ref":
            return ("const " if not self.mut else "") + self.args[0].render() + "&"
        if self.kind == "ptr":
            inner = self.args[0].render() if self.args else "?"
            return (("const " if not self.mut else "") + inner + "*"
                    + (" const" if self.pconst else ""))
        if self.kind == "addrof":
            return "&" + (self.args[0].render() if self.args else "?")
        if self.kind == "name":
            # **诊断里写用户的名字**：内部 `Result` / `Option` 对外是
            # `expected` / `optional`（P4-0）。不这么映射的话，读者按文档
            # 写了 `expected<…>` 却在错误里看到 `Result<…>`。
            _nm = display(self.name)
            if self.args:
                return f"{_nm}<" + ", ".join(a.render() for a in self.args) + ">"
            return _nm
        if self.kind == "fn":
            return "fn"
        return "?"


TY_UNKNOWN = Ty("unknown")
TY_BOOL = Ty("name", "bool")
TY_U64 = Ty("name", "u64")
TY_I64 = Ty("name", "i64")
TY_F64 = Ty("name", "f64")
TY_VOID = Ty("name", "void")

#: **不透明类型**：内建的返回类型真的推不出来时的兜底（`stdlib.ret="any"`，
#: 或 `ret` 写了词表外的值）。它**不是** `UNKNOWN`：`UNKNOWN` 与任何类型兼容，
#: 于是 `u64 x = f()` 会静默通过；`Any` 只与 `Any` 兼容，赋值 / 传参 / 比较
#: 都会报错。方向是安全的——**宁可让作者补一条 `ret=`，也不许静默变成标量**。
TY_ANY = Ty("name", "Any")

#: `nullptr` 字面量的类型：只与 `T*` 兼容（`u64 x = nullptr;` 必须报错）。
TY_NULL = Ty("null")

#: 内建返回的**不透明容器**：语言里没有可声明的类型名（`deque<T>` 未接线、
#: `Reader` / `Writer` / `Split` / `Args` / `Json` 只在值上存在），但它们是
#: **真值形状**，不能当标量。给它们一个名义类型，赋值 / 比较就能静态挡住。
OPAQUE_BUILTIN_TYPES = frozenset({
    "Any", "Args", "Reader", "Writer", "Split", "Json",
})

#: 不透明值只能交给哪些内建（诊断消息里直接给出出路）。
_OPAQUE_CONSUMERS = {
    "Args": "`args_len` / `args_get`",
    "Split": "`split_len` / `split_get`",
    "Reader": "`next_line` / `close`",
    "Writer": "`write_chunk` / `close_write`",
    "Json": "`match` 它的 `Json::*` 变体（`Json::Num(n)` / `Json::Arr(xs)` …）",
}

#: 「包装值」的族：它们**必须解包**（`match` / `option_unwrap` / `?`）才能当标量。
#: `Option` 的规范名是 `optional`（`typenames.CANON`）；`Result` 没有别名。
WRAPPED_TYPE_HEADS = frozenset({"optional", "Result"})

#: **算法类内建的精确实参个数**——表里每一条都从 `src/interp.py` 的**实际实现**
#: 读出来（不是凭印象、不是抄文档）：
#:
#: | 名字 | arity | 代码依据（`src/interp.py`） |
#: |---|---|---|
#: | `min` / `max` | 2 | `:745` / `:752`：`len(args) != 2` 直接报错 |
#: | `sort` | 1 | `:2976`：`if not items` → 「至少要 1 个实参」；**多给的被忽略** |
#: | `sort_by` | 2 | `:2992`：`len(items) < 2` 报错；比较器是第 2 个（`:2994`） |
#: | `map` / `filter` / `count_if` | 2 | `:2917`：`_builtin_higher` 的 `len(items) < 2`；回调取 `items[1]`（`:2924`） |
#: | `fold` | 3 | `:2935`：`len(items) < 3` 报错；回调取 `items[2]`（`:2924`） |
#: | `for_each` | 2 | `:2742`：`len(_items) < 2` 报错 |
#:
#: **为什么必须在这里查**：这 9 个名字**不在** `stdlib.FNS` 注册表里（只在
#: `stdlib.NATIVE`，见 `stdlib.py:593`），所以上面 `stdlib.arity_ok(...)` 那条路
#: **结构上覆盖不到**它们。以前 `sort`/`sort_by` 靠 `checker.py` 的
#: `_check_sort_calls` 兜着，`map`/`filter`/`fold`/`count_if` 则**两边都没人管**：
#: `map(xs)`、`fold(xs, 0, add, 1)`、`count_if(xs, p, 9)` 一律 check 通过，
#: 运行期才炸——或者更糟：解释器只查下限（`< 2` / `< 3`），**多给的实参被静默忽略**，
#: 「参数写错了却有个看起来合理的结果」。
#:
#: 判据来自实现而不是文档，这条也修掉了「文档曾把 `min`/`max` 写成 1 参而没人发现」——
#: 文档写错能活下来，是因为**没有任何一处按文档去校验**。
BUILTIN_ARITY: Dict[str, int] = {
    "sort": 1,
    "sort_by": 2,
    "map": 2,
    "filter": 2,
    "fold": 3,
    "count_if": 2,
    "for_each": 2,
    "min": 2,
    "max": 2,
}

#: 高阶内建的**实参说明**：只报「要 2 个」不够，读者还得知道第 2 个是**具名函数**
#: （匿名函数是任意代码，成本不可静态界定——`interp._builtin_higher` 的同一套理由）。
BUILTIN_ARITY_HINT: Dict[str, str] = {
    "sort_by": "（容器 + 具名比较器）",
    "map": "（容器 + 具名函数）",
    "filter": "（容器 + 具名函数）",
    "count_if": "（容器 + 具名函数）",
    "fold": "（容器 + 初值 + 具名函数）",
    "for_each": "（容器 + 具名函数）",
}

#: **高阶内建的回调形参个数**——判据来自 `interp._builtin_higher` 实际怎么调：
#:
#: | 内建 | 解释器调用 | 回调形参个数 |
#: |---|---|---|
#: | `map` / `filter` / `count_if` | `cb(x)` | 1 |
#: | `fold` | `cb(acc, x)` | 2 |
#: | `for_each` | `cb(x)`（list-like / set）/ `cb(k, v)`（map 类） | 1 或 2 |
#:
#: **为什么必须检查**：解释器 `_run_fn` 在形参个数对不上时直接抛
#: `InterpError: <fn> 需要 N 个实参，给了 M`。`fold(xs, 0, onearg)`（`onearg`
#: 只有 1 个形参）以前 `check` rc=0、`run` rc=1——又是一条"名字写着能用、
#: 跑起来才炸"。回调是**静态可见的具名函数**，个数可以在检查期比。
#:
#: `sort_by` 不在这里：它的比较器个数由 `checker._check_sort_calls` 报
#: `E-Sort-BadComparator`，不要两处各报一遍。
HOF_CALLBACK_ARITY: Dict[str, int] = {
    "map": 1,
    "filter": 1,
    "count_if": 1,
    "for_each": 1,       # 容器是 map / HashMap 时是 2（见 `_check_hof_callback`）
    "fold": 2,
}

#: 高阶内建的**第一个实参**必须是容器。`map` / `filter` / `fold` /
#: `count_if` / `sort` / `sort_by` 只认 list-like（解释器要求
#: `isinstance(recv, list)`）；`for_each` 还认 map / set / HashMap。
#: 以前 `map(42, twice)` / `sort(n)` 一律 check 绿，`run` 才抛
#: `InterpError: … 的第一个实参必须是容器`。
HOF_LIST_LIKE_HEADS = frozenset({"vector", "array", "fixedvec"})
HOF_MAP_HEADS = frozenset({"map", "unordered_map", "HashMap"})
HOF_SET_HEADS = frozenset({"set", "unordered_set"})

#: **只看 stdlib 的 `HashMap`** 的自由函数（`map_new()` 的返回类型）。
#:
#: 语言的 `map<K,V>` / `unordered_map` 有同名能力，但**成本口径不同**：
#: HashMap = 哈希 + 有界 8 次探测 ⇒ `O(1)` 真上界；语言 map = 平衡二叉树 ⇒
#: `O(log n)`。共用一个 `cost="1"` 的登记会让 `map_put(lang_map, k, v)` 的 κ
#: 低估（少算比多算危险），所以这几个函数在 `check` 阶段就**明确拒绝**语言
#: 容器，并给出方法式写法（`m.insert` / `m.get` / `m.contains` / `m.length()`）。
#:
#: 这条以前是「`check` 全绿、`run` 抛 `TypeError`」（`_tier_b/structs` K2）。
MAP_FREE_FNS = frozenset({"map_put", "map_get", "map_has", "map_len"})

#: 语言里的**有序/哈希容器**头名（`canon` 之后）。它们走方法式 API。
LANG_MAP_HEADS = frozenset({"map", "unordered_map", "set", "unordered_set"})


#: **容器方法表**——与解释器**逐条对齐**（`src/interp.py` 的 method-call 分支）。
#:
#: 用途：`vector<u64> v; v.size();` 以前 check 全过、run 才炸
#: `InterpError: 未实现的方法`。**假绿灯比直接报错更难查**：读者以为类型系统认了。
#:
#: **2026-11-11（P4-0）：名字对齐 C++。** 这张表只收**现在的名字**；
#: 旧名（`push` / `length` / `is_empty`）不进表，由 `DEPRECATED_METHODS`
#: 报 `W-Builtin-Deprecated` 并教新名——旧名仍然能跑（解释器两侧都认）。
#:
#:   vector/array/fixedvec → size empty at push_back pop find insert erase
#:                           clear back front
#:   map/set/unord           → size empty insert get/contains erase/remove
#:                           clear keys values
#:   stack/queue             → push pop top size empty clear…
#:
#: 表里**故意不含** `unordered_set.get`：解释器那条分支没有按容器类型分开，
#: 对 set 调用会 `TypeError`。宁可静态拒绝，也不对外宣称一个会崩的方法。
METHOD_TABLE = {
    "vector": {"size", "empty", "at", "push_back", "assign", "pop", "find", "insert",
               "erase", "clear", "back", "front"},
    "array": {"size", "empty", "at", "push_back", "pop", "find", "insert",
              "erase", "clear", "back", "front"},
    "fixedvec": {"size", "empty", "at", "push_back", "pop", "find", "insert",
                 "erase", "clear", "back", "front"},
    "map": {"size", "empty", "insert", "put", "get", "contains", "remove",
            "erase", "clear", "keys"},
    "set": {"size", "empty", "insert", "contains", "remove", "erase", "clear",
            "values"},
    "unordered_map": {"size", "empty", "insert", "get", "contains", "remove",
                      "erase", "clear", "keys", "values"},
    "unordered_set": {"size", "empty", "insert", "contains", "remove", "erase",
                      "clear", "keys", "values"},
    "stack": {"size", "empty", "push", "enqueue", "pop", "dequeue", "top",
              "peek", "front", "clear"},
    "queue": {"size", "empty", "push", "enqueue", "pop", "dequeue", "top",
              "peek", "front", "clear"},
}

#: **旧名 → 新名**（P4-0，2026-11-11）。旧名还认，但报
#: `W-Builtin-Deprecated`：消息教新名，样例与文档来得及迁移。
#:
#: 为什么用 warning 而不是 error：样例 / 文档 / 下游代码**大量在用**旧名；
#: 一次性改成 error 会让所有存量代码在同一版里同时红。
DEPRECATED_METHODS = {
    "push": "push_back",
    "length": "size",
    "is_empty": "empty",
}

#: `push` 只对**顺序容器**（vector / array / fixedvec）是旧名；
#: `stack` / `queue` 的 `push` 本来就是 C++ 的名字，不报弃用。
_PUSH_DEPRECATED_HEADS = frozenset({"vector", "array", "fixedvec"})



def _is_float(t) -> bool:
    """是不是浮点类型（`f32`/`f64`）。"""
    return getattr(t, "kind", "") == "name" and t.name in FLOATS


def _is_int_ty(t) -> bool:
    """是不是整数标量类型（位运算只接受这一组）。"""
    return getattr(t, "kind", "") == "name" and canon(t.name) in NUMERIC


def _pow_const_int(node):
    """把指数位置的**字面量常量表达式**折成 `int`；折不出返回 `None`。

    只认 `lit-int` / `paren` / `unary -` / `**` 四种。**不认变量和 `const`
    名字**：`const u64 k = 10; 2 ** k` 也算折不出（那条路要求写 `2.0 ** k`），
    因为"常量"和"运行期值"在类型层要分得开——这一层是**语义闸门**，不是
    优化器。

    认 `**` 是为了让 `2 ** 3 ** 2` 能过：右结合下指数是 `3 ** 2`，它本身
    就是一个幂表达式。折出来的值只用来判「是否 ≥ 0 / 是否存在」，不参与类型。
    """
    n = node
    while getattr(n, "kind", "") == "paren" and getattr(n, "children", None):
        n = n.children[0]
    if n is None:
        return None
    k = getattr(n, "kind", "")
    if k == "lit-int":
        try:
            return int(n.value)
        except (TypeError, ValueError):
            return None
    if k == "unary" and (getattr(n, "value", "") or "") == "-" and n.children:
        v = _pow_const_int(n.children[0])
        return None if v is None else -v
    if k == "binary" and (n.value or "") == "**" and len(n.children) >= 2:
        l = _pow_const_int(n.children[0])
        r = _pow_const_int(n.children[1])
        # 指数上限 64：只为挡住 `2 ** 1000000` 这种"折常量本身要几十万位"的写法
        if l is None or r is None or r < 0 or r > 64:
            return None
        return l ** r
    return None


def _pow_exponent_literal(node) -> tuple:
    """整数 `**` 的指数是不是**非负的字面量常量**？返回 `(状态, 值)`。

    状态三档：`"ok"`（折出来且 ≥ 0）/ `"negative"`（折出来是负数）/
    `"notliteral"`（变量、调用、任何运行期才定得下来的东西）。
    """
    v = _pow_const_int(node)
    if v is None:
        return "notliteral", None
    return ("ok", v) if v >= 0 else ("negative", v)


def _bitwise_result_ty(a: Ty, b: Ty) -> Ty:
    """位运算的结果类型（与运行期 `_ubin` 的位宽规则一致）。

    · 任一边是无符号 → 结果是无符号，位宽取**无符号操作数里最宽的**
      （`u64 & i64` → `u64`，`u8 & i64` → `u8`）；
    · 两边都是有符号 → 取较宽的有符号类型（`i8 & i64` → `i64`）；
    · 未知类型不参与推断。
    """
    if a.kind == "unknown":
        return b
    if b.kind == "unknown":
        return a
    aw = INT_WIDTH.get(canon(a.name), 0)
    bw = INT_WIDTH.get(canon(b.name), 0)
    au = canon(a.name) in UNSIGNED
    bu = canon(b.name) in UNSIGNED
    if au and bu:
        return a if aw >= bw else b
    if au:
        return a
    if bu:
        return b
    return a if aw >= bw else b


def _shift_result_ty(a: Ty, b: Ty) -> Ty:
    """移位的结果类型：**只看左操作数**（与运行期 `_shift` 一致）。

    C / Rust 的规则：移位的类型由左操作数（整型提升后）决定，移位量只提供
    数值、不参与类型。所以 `u32 << u64` 是 `u32`（不是 `u64`）、
    `i64 << u64` 是 `i64`。旧实现按「两边最宽」推导成 `u64`，运行期也真的
    在 64 位上算——于是 `u32 x; u64 n; x << (32 - n)` 得 `2^32`，
    `u32 m` 版本得 0（同一个式子，只差形参类型）。
    左操作数类型未知时退回右操作数（不制造误报）；两侧都未知 → `?`。
    """
    if a.kind != "unknown":
        return a
    return b if b.kind != "unknown" else TY_UNKNOWN

# ---------------------------------------------------------------- 内建返回类型
#
# `stdlib.RET_KINDS` 是内建返回类型的**登记处**，这里是为它写的**唯一消费分支**。
# 2026-11-10（K5）之前 `vec_pop` / `map_get` / `open_read` 等 17 个内建登记的是
# `ret="any"`，而这里只有一条 `any → UNKNOWN`——`UNKNOWN` 与**任何类型兼容**，
# 于是 `u64 x = vec_pop(h)` 静默通过，`x == 9` 运行期静默为 `false`。
#
# 两条纪律：
#   1. 每个 `ret` 词都必须在这里有分支（`tests/cases/test_stdlib.py` 对账）；
#   2. **词表外的值 / `any` 一律退回 `TY_ANY`（不透明），绝不退回 `UNKNOWN`**。

def _unref(t: Ty) -> Ty:
    while t.kind == "ref" and t.args:
        t = t.args[0]
    return t


def _elem_ty_of(container: Ty) -> Ty:
    """容器表达式的**元素类型**：`map<K,V>` → `V`；`array<T,N>` → `T`；其余 → 末参。

    `vec_pop` 的返回是 `Option<元素类型>`——元素类型推不出来时退回 `UNKNOWN`
    （于是 `Option<?>`），**不会**退回"整个值就当标量"。
    """
    c = _unref(container)
    if c.kind != "name" or not c.args:
        return TY_UNKNOWN
    head = canon(c.name)
    if head in ("map", "unordered_map"):
        return c.args[1] if len(c.args) > 1 else TY_UNKNOWN
    if head in ("array", "fixedvec"):
        return c.args[0]
    return c.args[-1]


def _map_key_ty_of(container: Ty) -> Ty:
    """`map<K,V>` / `unordered_map<K,V>` → `K`；`set<T>` / `unordered_set<T>` → `T`。

    `keys()` 的**元素类型**（2026-11-13，P4）：有序与哈希同一口径——
    `map` / `unordered_map` 的 `keys()` 是键 `K`；`unordered_set` 的
    `keys()` 与 `set` / `unordered_set` 的 `values()` 都是元素本身
    （`interp.py` 的 `SetVal.values()` / `UnorderedSetVal.keys()` 实测如此）。
    推不出来时退回 `UNKNOWN`（不猜成标量）。
    """
    c = _unref(container)
    if c.kind != "name" or not c.args:
        return TY_UNKNOWN
    if canon(c.name) in ("map", "unordered_map"):
        return c.args[0]
    return _elem_ty_of(c)


def _vector_of(elem: Ty) -> Ty:
    """`vector<elem>`：`keys()` / `values()` 的返回类型（快照，不是视图）。"""
    return Ty("name", "vector", [elem])


def _option_payload_ty(t: Ty) -> Ty:
    """`Option<T>` → `T`；不是 `Option`（或载荷未知）→ `UNKNOWN`。"""
    t = _unref(t)
    if t.kind == "name" and canon(t.name) == "optional" and t.args:
        return t.args[0]
    return TY_UNKNOWN


def _wrapped_head(t: Ty) -> str:
    """`Option` / `Result` 的族名；不是包装值 → `""`。"""
    t = _unref(t)
    if t.kind != "name":
        return ""
    if t.name == "Result":
        return "Result"
    if canon(t.name) == "optional":
        return "optional"
    return ""


def _opaque_head(t: Ty) -> str:
    """内建返回的**不透明值**（`Args` / `Reader` / `Split` / `Json` / `deque`…）。"""
    t = _unref(t)
    if t.kind == "name" and t.name in OPAQUE_BUILTIN_TYPES:
        return t.name
    return ""


def _needs_unwrap(t: Ty) -> bool:
    """这个值**不能直接当标量用**（是 `Option` / `Result` / 内建不透明值）。"""
    return bool(_wrapped_head(t) or _opaque_head(t))


def _unwrap_hint(t: Ty) -> str:
    """给"用了包装值当标量"的作者一条**能照抄**的出路。"""
    head = _wrapped_head(t)
    if head == "optional":
        return ("`Option` 要先解包：`match (x) { optional::Some(v) => { … }, "
                "optional::None => { … }, _ => { … } }`，或 "
                "`option_unwrap(x)` / `option_unwrap_or(x, 默认值)`")
    if head == "Result":
        return ("`Result` 要先解包：在 `-> T!` 函数里写 `x?`，或 "
                "`match (x) { Ok(v) => { … }, Err(e) => { … }, _ => { … } }`")
    if _opaque_head(t):
        use = _OPAQUE_CONSUMERS.get(t.name, "配套的内建")
        return (f"它是不透明值（{t.render()}），只能交给 {use}，不能当标量")
    return ("这个内建的返回类型推不出来（不透明值），不能直接当标量用；"
            "先 `match` / 解包，或给它补一条具体的 `ret=` 登记")


def _is_void_ty(t: Optional[Ty]) -> bool:
    """这个类型是不是 **`void`**（没有值）。

    `void` 在本实现里有**两种拼写**，必须都认：

    | 来源 | 类型 |
    |---|---|
    | `()` / `void` 写在**返回类型位置**（`parser` 第 7 条：`void` 归一成 `()`） | `Ty("tuple", args=[])`（`render()` 就是 `()`） |
    | 方法表里声明成 void 的返回值（`assign` / `push_back` / `clear` …） | `Ty("name", "void")` |

    **空的元组不可能是值**：本语言没有零元素元组字面量（写不出来），
    所以 `Ty("tuple", [])` 只可能来自上面那一列。

    `void` 的意思是**没有值**，不是"零个元素的值"。所以它只能出现在
    **表达式语句**里（返回值被丢弃）；任何"需要值"的位置都是错的
    （`E-Type-Void`，见 `TypeChecker._err_void_value`）。
    """
    if t is None:
        return False
    while t.kind == "ref" and t.args:
        t = t.args[0]
    if t.kind == "tuple":
        return not t.args
    return t.kind == "name" and canon(t.name) == "void"


def _builtin_ret_ty(rk: str, args: List[Ty]) -> Ty:
    """`stdlib.ret` → 调用表达式的 `Ty`。

    每个分支对应 `stdlib.RET_KINDS` 里的一个词。**词表外的值退回 `TY_ANY`**
    （不透明），而不是旧实现里的 `TY_U64` / `TY_UNKNOWN`——前者会把非标量
    内建当成 `u64`，后者与任何类型兼容。
    """
    a0 = args[0] if args else TY_UNKNOWN
    # ---- 标量
    if rk == "u64":
        return TY_U64
    if rk == "i64":
        return TY_I64
    if rk == "bool":
        # 字符分类谓词（`isdigit` 一族）返回 `bool`。**不能返回 `u64`**：
        # 本语言的 `if` / `while` 条件只收 `bool` 或指针（`E-Type-Cond`），
        # 返回整数会让 C++ 的 `if (std::isdigit(c))` 原样搬不过来。
        return TY_BOOL
    if rk == "f64":
        # 数学内建（`sqrt` / `floor` / `ceil` / `round` / `fabs` / `log2`）返回
        # **f64**，与 C 的 `double` 一致。以前没有这个 ret 词，这些函数根本
        # 不存在（`E-Call-Undeclared`，`_cppmatrix/misc/24b`）。
        return TY_F64
    if rk == "arg0":
        # **返回类型跟第一个实参走**（`abs`）：`i64 → i64`、`u64 → u64`、
        # `f64 → f64`。推不出（实参类型未知）就退 `UNKNOWN`，不猜成 `u64`。
        return _unref(a0)
    # ---- 容器
    if rk == "vec-u8":
        return Ty("name", "vector", [Ty("name", "u8")])
    if rk == "vec-u64":
        return Ty("name", "vector", [TY_U64])
    if rk == "vec-vec-u8":
        return Ty("name", "vector", [Ty("name", "vector", [Ty("name", "u8")])])
    if rk == "map":
        # 键 / 值类型由**声明侧**给出（`map<u64, u64> m = map_new();` 是**旧写法**，
        # 2026-11-10 起 `map_new()` 返回 `HashMap`，见下一条）；这里给 `map<?, ?>`：
        # 与任何 `map<K,V>` 兼容，但**不与标量兼容**。
        return Ty("name", "map", [TY_UNKNOWN, TY_UNKNOWN])
    if rk == "set":
        return Ty("name", "set", [TY_UNKNOWN])
    if rk == "deque":
        return Ty("name", "deque", [TY_UNKNOWN])
    if rk == "hashmap":
        # `map_new()`：**stdlib 的 HashMap**，与语言的 `map<K,V>` 是两个名义类型
        # （成本口径不同：O(1) 有界探测 vs O(log n)）。键/值类型由声明侧给出
        # （`HashMap<u64, u64> m = map_new();`）。
        return Ty("name", "HashMap", [TY_UNKNOWN, TY_UNKNOWN])
    if rk == "args":
        return Ty("name", "Args")
    if rk == "split":
        return Ty("name", "Split")
    # ---- Result
    if rk == "result-u64":
        return Ty("name", "Result", [TY_U64])
    if rk == "result-vec-u8":
        return Ty("name", "Result", [Ty("name", "vector", [Ty("name", "u8")])])
    if rk == "result-vec-u64":
        return Ty("name", "Result", [Ty("name", "vector", [TY_U64])])
    if rk == "result-reader":
        return Ty("name", "Result", [Ty("name", "Reader"), Ty("name", "Io")])
    if rk == "result-writer":
        return Ty("name", "Result", [Ty("name", "Writer"), Ty("name", "Io")])
    if rk == "result-json":
        # `json_parse` 的 payload 类型与错误枚举都叫 `Json`（语言里就这么命名）：
        # 第一个参数是**值**类型，第二个是**错误枚举**。
        return Ty("name", "Result", [Ty("name", "Json"), Ty("name", "Json")])
    # ---- Option
    if rk == "option-vec-u8":
        return Ty("name", "Option", [Ty("name", "vector", [Ty("name", "u8")])])
    if rk == "option-elem":
        return Ty("name", "Option", [_elem_ty_of(a0)])
    if rk == "option-arg0":
        t = _unref(a0)
        return Ty("name", "Option", [t if t.kind != "unknown" else TY_UNKNOWN])
    if rk == "option-map-val":
        return Ty("name", "Option", [_elem_ty_of(a0)])
    if rk == "option-unknown":
        return Ty("name", "Option", [TY_UNKNOWN])
    # ---- Option 的载荷（`option_unwrap` / `option_unwrap_or`）：
    #      `Some(v)` 给 `v` 的类型；`Option<?>` 时 `unwrap_or` 的第 2 个实参兜底。
    if rk == "option-payload":
        payload = _option_payload_ty(a0)
        if payload.kind != "unknown":
            return payload
        return _unref(args[1]) if len(args) > 1 else TY_UNKNOWN
    # ---- 兜底：`any` 或词表外的值 → 不透明（不能赋给标量）
    return TY_ANY


# ---------------------------------------------------------------- 第 11 条：static_cast
#
# `static_cast<T>(x)` 在 `--compat` 下**只认两种目标**（parser 已经把其它目标
# 挡掉），且只有当"转换后值与转换前**逐位相同**"时才折成 `x`：
#
# | 目标 | 可折的实参类型 | 为什么 |
# |---|---|---|
# | `unsigned char`（`u8`） | **只有 `u8`** | `u64`/`i64`/… → `u8` 是**窄化**，会丢高位 |
# | `u64` | `u8` / `u16` / `u32` / `u64` | 无符号→无符号、位宽不变或加宽，值域不变 |
#
# **不折**的（一律 `E-Cast-Unsupported`，消息里给改法）：
#   - 窄化（`u64` → `u8`）、有符号 → `u64`（负数会变成补码大数）；
#   - 推不出静态类型（**不猜**：宁可多报一条错，也不许把真转换折成 no-op）；
#   - 非整数（`f64` / 容器 / 结构体）。
#
# 这条判据**只有一份实现**：`parse_source` 的尽力折叠与 `check` 的权威判定
# 都调它（前者见 `parser._has_cast_static`，后者见 `_expr` 的 `cast-static`）。

#: `cast-static` 节点里 `name` 的取值 → 用户看到的 C++ 目标写法。
_CAST_TARGET_SPELLING = {"u8": "unsigned char", "u64": "u64"}


def _static_cast_fold_ok(target: str, src: Ty) -> Optional[str]:
    """`static_cast<target>(x)` 能否**无损**折成 `x`。

    返回 `None` = 可以折（运行时逐位相同）；返回字符串 = **不能折的理由**
    （直接进诊断正文）。判据只看静态类型，不做常量折叠：
    `static_cast<u64>(-1)` 的实参类型是 `i64`，即便它的值恰好是正数也不折——
    "这个值恰好没溢出"不是类型事实。
    """
    s = _unref(src)
    if s.kind == "unknown":
        return ("操作数的静态类型推不出来 ⇒ 无法证明转换前后逐位相同"
                "（本语言不猜类型）")
    if s.kind != "name":
        return f"操作数的类型是 {s.render()}，不是整数标量"
    head = canon(s.name)
    if head not in INT_WIDTH:
        return f"操作数的类型是 {s.render()}，不是整数标量"
    if target == "u8":
        if head == "u8":
            return None
        return (f"目标 `u8` 比操作数的 `{s.render()}` 窄（或符号不同）："
                f"高位会被丢掉（如 `u64 300` → `u8 44`），这是**真转换**，"
                f"不是拼写")
    if target == "u64":
        if head in UNSIGNED:
            return None
        return (f"操作数的 `{s.render()}` 是有符号整数：负值转 `u64` 会变成"
                f"补码大数（`-1` → `2^64-1`），值变了")
    return f"目标 `{target}` 不在兼容层的两种形状里"


def _ptr_accepts(elem: Ty, ptr: Ty) -> bool:
    """`&x` 指向的 `elem` 能否作为指针类型 `ptr` 的指向。"""
    target = ptr.args[0] if ptr.args else TY_UNKNOWN
    return compatible(elem, target)


def compatible(a: Ty, b: Ty) -> bool:
    """a 能不能赋给 b。UNKNOWN 与任何类型兼容。

    ## 指针的兼容规则（①–③ 的地基）

    | 方向 | 结论 | 依据 |
    |---|---|---|
    | `T*` → `T*` | ✅（逐层比指向） | 同一类型 |
    | `T*` → `const T*` | ✅（加 const） | 只读化是安全的 |
    | `const T*` → `T*` | ❌ | 会**丢掉** const，之后能经指针改 const 对象 |
    | `nullptr` → `T*` | ✅ | 空指针是任何指针的合法值 |
    | `nullptr` → 标量 / 容器 | ❌ | `u64 x = nullptr;` 必须报错 |
    | `&x` → `T*` / `const T*` | ✅（指向类型逐个比） | 取地址 |
    | `&x` → `T&` / `const T&` | ✅（显式借用标记，calculator.eln 依赖） | 值域相同 |
    | `&x` → 标量 | ❌ | `u64 y = &x;` 仍然报错 |
    | `T*` → `T&` / `T` | ❌ | 指针不是它指向的值 |

    **`&x` 用 `addrof` 中间类型**，而不是直接给 `T*`：否则
    `u64 y = &x;` 会与 `u64` 兼容（把地址当整数，静默通过）。
    """
    if a.kind == "unknown" or b.kind == "unknown":
        return True
    # ---- `nullptr`：只与指针（以及 nullptr 自己）兼容
    if a.kind == "null" or b.kind == "null":
        other = b if a.kind == "null" else a
        return other.kind in ("ptr", "null", "unknown")
    # ---- `&x`：可以落到指针或引用上，不能落到标量上
    if a.kind == "addrof":
        inner = a.args[0] if a.args else TY_UNKNOWN
        if b.kind == "ptr":
            return _ptr_accepts(inner, b)
        if b.kind == "ref":
            return compatible(inner, b.args[0] if b.args else TY_UNKNOWN)
        return False
    # ---- 引用先拆（沿用既有口径：ref 与它指向的类型在赋值上等价）
    if a.kind == "ref":
        a = a.args[0]
    if b.kind == "ref":
        b = b.args[0]
    # ---- 指针：逐层比，且只允许「加 const」
    if a.kind == "ptr" or b.kind == "ptr":
        if a.kind != "ptr" or b.kind != "ptr":
            return False
        ea = a.args[0] if a.args else TY_UNKNOWN
        eb = b.args[0] if b.args else TY_UNKNOWN
        if not compatible(ea, eb):
            return False
        # `const T*` → `T*`：去 const，拒绝（未知类型不猜）
        if (not a.mut) and b.mut and ea.kind != "unknown" and eb.kind != "unknown":
            return False
        return True
    if a.kind != b.kind:
        return False
    if a.kind == "fn":
        # **函数类型逐项比对**（L-06，2026-11-06）：以前只比参数个数，
        # 于是 `fn(u64) -> u64` 与 `fn(bool) -> u64` 被判成兼容——
        # 调用点会把 `n` 传进一个要 `bool` 的函数，静态检查却放行。
        # `Ty("fn").args` 是「参数…, 返回」的平铺列表，所以逐项比对
        # 顺带把返回值也比了。`compatible` 对嵌套 `fn` 递归，结构有限必终止。
        return (len(a.args) == len(b.args)
                and all(compatible(x, y) for x, y in zip(a.args, b.args)))
    if a.kind == "tuple":
        return (len(a.args) == len(b.args)
                and all(compatible(x, y) for x, y in zip(a.args, b.args)))
    if a.kind == "name":
        # **过 canon**：`Vec` 与 `vector` 是同一个类型（改名期两套写法都要能用）。
        # 只改构造侧（内建返回类型）而不改比较侧，会让 read_stdin 这类
        # "内建返回 vs 声明"的比较失败。
        if canon(a.name) == canon(b.name):
            return (len(a.args) == len(b.args)
                    and all(compatible(x, y) for x, y in zip(a.args, b.args)))
        # 数值类型之间一律按 UNKNOWN 处理（N2 禁止隐式转换，这里不代替 N2，
        # 只是不制造误报；真正的 N2 检查由 checker 的字面量规则负责）
        if a.name in NUMERIC and b.name in NUMERIC:
            return True
        # **浮点之间可赋值**：`f32 → f64` 无损；反方向按 C++ 允许（收窄是 N2 的事）。
        if a.name in FLOATS and b.name in FLOATS:
            return True
        # 整数 ↔ 浮点（`f64 x = 3;`）也放行，理由同上。
        if (a.name in NUMERIC and b.name in FLOATS) or (a.name in FLOATS and b.name in NUMERIC):
            return True
        return False
    return False


def ty_of_node(n: Optional[Node]) -> Ty:
    """把 AST 的类型节点变成 Ty。"""
    if n is None:
        return TY_UNKNOWN
    k = n.kind
    if k == "type-name":
        args = []
        for c in n.children:
            if c.kind == "type-args":
                args = [ty_of_node(x) for x in c.children]
        return Ty("name", n.name or "", args)
    if k == "type-fn":
        # **函数类型**：`func(u64, Vec<u8>) -> u64` → Ty("fn", "", [参数..., 返回])
        ps = next((c for c in n.children if c.kind == "type-fn-params"), None)
        ret = n.children[-1] if n.children else None
        args = [ty_of_node(c) for c in (ps.children if ps else [])]
        if ret is not None and ret.kind != "type-fn-params":
            args.append(ty_of_node(ret))
        return Ty("fn", "", args)
    if k == "type-ref":
        return Ty("ref", args=[ty_of_node(n.children[0])] if n.children else [TY_UNKNOWN],
                  mut=(n.value == "mut"))
    if k == "type-ptr":
        v = n.value if isinstance(n.value, dict) else {}
        return Ty("ptr",
                  args=[ty_of_node(n.children[0])] if n.children else [TY_UNKNOWN],
                  mut=not bool(v.get("pointee_const")),
                  pconst=bool(v.get("ptr_const")))
    if k == "type-tuple":
        return Ty("tuple", args=[ty_of_node(c) for c in n.children])
    if k == "type-slice":
        return Ty("name", "vector", [ty_of_node(n.children[0])] if n.children else [TY_UNKNOWN])
    return TY_UNKNOWN


def ty_to_node(ty: Optional[Ty]) -> Optional[Node]:
    """`Ty` → **类型节点**（只给 `--compat` 的 `auto` 用，第 3 条）。

    推断出具体类型之后，`typecheck` 会把 `type-auto` **当场换成**它，
    于是 `auto n = 5;` 的 AST 与 `i64 n = 5;` **同形**——下游
    （interp / 成本域 / 格式检查）一行都不用改，κ 与 run 输出必然相同。

    **推不出可写的类型就返回 None**（`unknown` / 无名 / 函数类型）：
    调用方据此报 `E-Compat-AutoNoType`，**不猜**。
    """
    if ty is None:
        return None
    k = ty.kind
    if k == "name":
        if not ty.name:
            return None
        node = Node("type-name", 0, 0, name=ty.name)
        if ty.args:
            args = Node("type-args", 0, 0)
            for a in ty.args:
                sub = ty_to_node(a)
                if sub is None:
                    return None
                args.add(sub)
            node.add(args)
        return node
    if k in ("ref", "ptr"):
        base = ty_to_node(ty.args[0]) if ty.args else None
        if base is None:
            return None
        if k == "ref":
            return Node("type-ref", 0, 0,
                        value=("mut" if ty.mut else "const")).add(base)
        return Node("type-ptr", 0, 0,
                    value={"pointee_const": not ty.mut,
                           "ptr_const": bool(ty.pconst)}).add(base)
    if k == "tuple":
        node = Node("type-tuple", 0, 0)
        for a in ty.args:
            sub = ty_to_node(a)
            if sub is None:
                return None
            node.add(sub)
        return node
    return None


# ---------------------------------------------------------------- 符号表

class Sym:
    __slots__ = ("name", "kind", "ty", "node", "origin")

    def __init__(self, name: str, kind: str, ty: Ty, node: Optional[Node] = None,
                 origin: str = ""):
        self.name = name
        self.kind = kind          # type / func / var / const / variant / error
        self.ty = ty
        self.node = node
        #: **这个符号是哪来的**（2026-11-12）：空 = 普通声明；
        #: `"map-pair"` = `for (auto& kv : m)` / `for (const auto& kv : m)`
        #: 的**只读 `(键, 值)` 视图**。它只用于**写位置**的诊断（见
        #: `_place`）：这种 `kv` 不能被重新绑定、字段也写不回去。
        self.origin = origin


class Diag:
    __slots__ = ("code", "message", "line", "col", "severity")

    def __init__(self, code: str, message: str, line: int = 0, col: int = 0,
                 severity: str = "error"):
        self.code = code
        self.message = message
        self.line = line
        self.col = col
        self.severity = severity

    def __repr__(self) -> str:
        return f"{self.code}@{self.line}:{self.col} {self.message}"


class Scope:
    def __init__(self, parent: Optional["Scope"] = None):
        self.parent = parent
        self.names: Dict[str, Sym] = {}

    def declare(self, s: Sym) -> Optional[Sym]:
        """声明；返回被遮蔽的外层同名符号（有的话）。"""
        if s.name in self.names:
            return self.names[s.name]       # 同作用域重复
        self.names[s.name] = s
        outer = self.parent
        while outer is not None:
            if s.name in outer.names:
                return outer.names[s.name]
            outer = outer.parent
        return None

    def lookup(self, name: str) -> Optional[Sym]:
        sc: Optional[Scope] = self
        while sc is not None:
            if name in sc.names:
                return sc.names[name]
            sc = sc.parent
        return None


# ---------------------------------------------------------------- 检查器

class TypeChecker:
    def __init__(self, tu_mod: Optional[Node], ch_mod: Optional[Node]):
        self.tu = tu_mod
        self.eln = ch_mod
        self.diags: List[Diag] = []
        self.globals = Scope()
        self.types: Dict[str, Node] = {}        # 类型名 → 声明节点
        # 枚举名 → 变体名集合。**内置 `Io`** 是语言提供的错误枚举，
        # 用户不必在每个 .tu 里重写（I/O 失败需要它）。
        self.enums: Dict[str, Set[str]] = {
            # **内置错误枚举**：每个程序都会遇到，让每个 .tu 重写一遍不合理。
            # 变体由编译器给，**不增加可撒谎位置**。
            "Io": {"NotFound", "Denied", "Failed"},
            "Arithmetic": {"DivisionByZero", "Overflow"},
            "Parse": {"BadNumber", "Overflow"},
            "Range": {"OutOfBounds"},
            "Option": {"Some", "None"},
        }
        self.enum_payload: Dict[Tuple[str, str], Ty] = {}
        self.open_errors: Set[str] = set()
        self.error_types: Set[str] = set()
        self.funcs: Dict[str, Node] = {}
        self.specs: Dict[str, Node] = {}
        #: 模块级常量名（`array<u64, BUCKET_COUNT>` 的第二个实参在语法树上也是
        #: `type-name`——它其实是**常量**，不是类型名，见 `_parse_type_args`）。
        self.const_names: Set[str] = set()
        #: 泛型类型参数（`func f<T>` 的 `T`）——出现处一律当"已知"，不报未知类型。
        self.type_params: Set[str] = set()
        #: `X::f(...)` 的 `X` 已经报过未知的位置（`(line, col)`）。
        #: 同一个调用节点在某些路径上会被 `_expr` 走两次（与
        #: `_map_silent_warned` 同一条去重理由），不按位置去重就会报两条。
        self._unknown_qualifier_at: Set[Tuple[int, int]] = set()
        #: 已经报过未知类型名的位置（`(line, col)`），避免同一处报两遍
        #: （`_params_of` 会被 `_collect` 与 `_check_fn` 各调一次）。
        self._unknown_type_at: Set[Tuple[int, int]] = set()
        #: 旧类型名（`Option` / `Result`）已经报过的位置——同一条声明会被
        #: `_params_of` / `_ret_of` 各走一次，不按位置去重就会重复报。
        self._old_type_at: Set[Tuple[int, int]] = set()
        #: **指针的空状态**（③ 的静态那一半）：`{名字: True}` 表示"这一句之前
        #: 最后一次赋值是 `nullptr`"。只做**直线 + 守卫**的保守流分析：
        #:   · 声明 `T* p = nullptr;` → True；`= &x` / `= 别的指针` → False；
        #:   · `p = nullptr;` → True；`p = …` → False；
        #:   · `if (p) { … }` / `if (p != nullptr) { … }` 的**体内** → False；
        #:   · 块结束时，块内被改过的名字在块外一律**忘掉**（不猜）。
        #: 判据只用于**报** `E-Pointer-NullDeref`（可证的空解引用），不参与兼容性。
        self._nulls: Dict[str, bool] = {}
        #: **`W-Map-SilentInsert` 的两个状态**（2026-11-11，审计 A3 / 日程 D9）：
        #:
        #: - `_write_lvalue` > 0 表示"当前正在给一个左值求类型"（`m[k] = v` 的
        #:   目标）。那时 `m[k]` 的插入是**写者的本意**，不该告警。
        #: - `_map_silent_warned` 按 `(line, col)` 去重：同一个 `m[k]` 可能被
        #:   `_expr` 走两次（`auto` 推断 + 常规检查），不该刷两条。
        self._write_lvalue = 0
        self._map_silent_warned: Set[Tuple[int, int]] = set()
        #: 当前语句里**正在被按下标写**的变量名（`freq[w] = …` 里的 `freq`）。
        #: RHS 的 `freq[w]` 是这次写的一部分，不报 `W-Map-SilentInsert`。
        self._map_written_in_stmt: Set[str] = set()
        self._collect()

    def _novariant(self, ty_name: str, variant: str) -> str:
        """变体不存在的提示：错误类型和枚举说清楚是哪一个（E3）。"""
        kind = "错误类型" if ty_name in self.error_types else "枚举"
        return "%s %s 没有变体 %s" % (kind, ty_name, variant)

    # ---------- 收集
    def _index_names(self) -> None:
        """**先**把两个模块里"能出现在类型位置的名字"登记齐。

        `_collect` 是按源码顺序走的，而函数可以写在 `struct Entry` 前面：
        `func f(Entry e)` 如果一边收集一边校验，就会在 `Entry` 还没登记时
        报"未知类型名 Entry"——那是**误报**。所以先扫一遍名字，再做符号收集。
        """
        for mod in (self.tu, self.eln):
            if mod is None:
                continue
            for it in mod.children:
                if it.kind in ("struct", "enum"):
                    self.types[it.name] = it
                    if it.kind == "enum":
                        self.enums[it.name] = {v.name for v in it.children
                                               if getattr(v, "kind", "") == "variant"}
                elif it.kind == "error-decl":
                    _vs = {v.name for v in (it.children or [])
                           if getattr(v, "name", None)}
                    if _vs:
                        self.enums[it.name] = _vs
                        self.error_types.add(it.name)
                    else:
                        self.open_errors.add(it.name)
                elif it.kind == "const":
                    self.const_names.add(it.name)
                elif it.kind in ("fn", "spec"):
                    for c in it.children:
                        if getattr(c, "kind", "") != "type-params":
                            continue
                        for tp in c.children:
                            if getattr(tp, "kind", "") == "type-param" and tp.name:
                                self.type_params.add(tp.name)

    def _collect(self) -> None:
        self._index_names()
        # **`npos` 是语言预置常量**（`u64` 最大值，P4-0）：`xs.find(v)` 找不到
        # 时返回它，AI 会写 `if (xs.find(v) != npos)`（与 C++ 的
        # `std::string::npos` 同形）。用户自己声明同名常量时以用户的为准。
        if "npos" not in self.const_names:
            self.globals.declare(Sym("npos", "const", TY_U64))
        for mod in (self.tu, self.eln):
            if mod is None:
                continue
            for it in mod.children:
                if it.kind in ("struct", "enum"):
                    self.types[it.name] = it
                    self.globals.declare(Sym(it.name, "type", Ty("name", it.name), it))
                    if it.kind == "struct":
                        # 字段类型也要查未知类型名：`struct S { x: u128 }` 以前
                        # 一句都不报（字段类型没人看），和局部声明是同一个洞。
                        for f in it.children:
                            if getattr(f, "kind", "") == "field" and f.children:
                                _ft = self._decl_ty(f.children[0])
                                # **函数类型只能出现在形参位置**（文档 §3）。
                                # `struct Op { f: func(u64) -> u64 }` 能过 check，
                                # 但 `Op { f: inc }` 在 run 期报「未绑定的名字 inc」——
                                # 语言里函数值只包装名字，不进 struct 字段。
                                if _ft.kind == "fn":
                                    self._err(
                                        "E-Type-FuncValueField",
                                        f"struct 字段 `{f.name}` 不能是函数类型："
                                        f"本语言的函数类型只在**形参位置**"
                                        f"（`func(u64) -> u64 f`），函数值不存进字段。"
                                        f"函数是**具名**的：直接调用 `f_impl(x)`，"
                                        f"或把它交给 `map` / `filter` / `fold` / "
                                        f"`sort_by` / `for_each` 这些内建消费者。",
                                        f)
                    if it.kind == "enum":
                        vs = set()
                        for v in it.children:
                            if v.kind != "variant":
                                continue
                            vs.add(v.name)
                            payload = (self._decl_ty(v.children[0])
                                       if v.children else TY_UNKNOWN)
                            self.enum_payload[(it.name, v.name)] = payload
                        self.enums[it.name] = vs
                elif it.kind == "error-decl":
                    self.globals.declare(Sym(it.name, "error", TY_UNKNOWN, it))
                    # **E3**：写了变体就是封闭（可校验），没写仍然开放（老行为）。
                    _vs = {v.name for v in (it.children or []) if getattr(v, 'name', None)}
                    if _vs:
                        self.enums[it.name] = _vs
                        self.error_types.add(it.name)
                    else:
                        self.open_errors.add(it.name)
                    continue
                elif it.kind == "const":
                    ty = self._decl_ty(it.children[0]) if it.children else TY_UNKNOWN
                    self.globals.declare(Sym(it.name, "const", ty, it))
                elif it.kind == "fn":
                    self.funcs[it.name] = it
                    self.globals.declare(Sym(it.name, "func", self._fn_type(it), it))
                elif it.kind == "spec":
                    self.specs[it.name] = it
                    self.globals.declare(Sym(it.name, "func", self._spec_type(it), it))
                elif it.kind == "extern":
                    # **`extern` 也是可调用的函数**，但以前**根本没进符号表**——
                    # 于是调外部函数的程序一律报 `E-Name-Undefined`，
                    # 而消息还在说"请在 .tu 的 extern 块登记"（已经登记了）。
                    # 现在按 spec 的方式收进来：签名一样，只是没有实现体。
                    self.globals.declare(Sym(it.name, "func", self._spec_type(it), it))

    # ---------- 类型名：已知 / 未知
    def _type_head_known(self, name: str) -> bool:
        """这个名字（类型的头）是不是一个**真实存在**的类型。"""
        raw = name or ""
        if not raw:
            return False
        head = canon(raw)
        if head in BUILTIN_TYPE_NAMES or canon(head) in BUILTIN_TYPE_NAMES:
            return True
        if raw in self.type_params or head in self.type_params:
            return True
        if raw in self.types or head in self.types:          # struct / enum
            return True
        if raw in self.enums or head in self.enums:          # 错误枚举 / 内置 Io…
            return True
        if raw in self.error_types or raw in self.open_errors:
            return True
        # `array<u64, BUCKET_COUNT>`：`BUCKET_COUNT` 在语法树上也是 type-name，
        # 但它指向模块级常量，不是类型。（同理 `FixedVec<u64, STACK_CAP>`。）
        if raw in self.const_names or head in self.const_names:
            return True
        return False

    def _qualified_head_known(self, head: str) -> bool:
        """`X::f(...)` 的 `X` 是不是一个**已知的限定词**。

        判据 = 已知类型名（`_type_head_known`：内置 / struct / enum / error /
        类型参数 / 模块常量）**或**已登记命名空间（`std`，见
        `QUALIFIED_NAMESPACES`）。空串不算。
        """
        if not head:
            return False
        return head in QUALIFIED_NAMESPACES or self._type_head_known(head)

    def _check_qualified_head(self, name: str, node: Optional[Node]) -> bool:
        """`X::f(...)` / `X::V`：`X` 不是已知类型就报 `E-Type-Unknown`。

        返回 True = **前缀未知**（调用方应短路成 `UNKNOWN`，别再拿 `X` 当
        类型去级联出第二个码）。

        为什么判据必须在这一层：lexer 把 `::` 当 PATHSEP，parser 把它落成
        `X.member` 的**限定名**（`call.name` / `path.name`，见
        `parser._expr` 的 PATHSEP 分支）；于是下游只认**最后一段**
        （本文件的 `.` 分支、`interp` 的 `split(".")[-1]`）。`foo::print(...)`
        里的 `foo` **从来没有被问过"你存在吗"**——成员名 `print` 撞上内建，
        就静默放行、`run` 照样输出。真实 C++ 库 `fmt::print(…)` 会因此
        "通过且输出正确"，而 `#include` 早被丢弃、库根本没进来：这是最坏的
        一类假绿。类型名表只有这里认识，所以判据放这里。
        """
        head, _, member = (name or "").rpartition(".")
        if self._qualified_head_known(head):
            return False
        at = node
        if (node is not None and node.children
                and getattr(node.children[0], "kind", "") == "path"):
            # 指到 `foo` 本身：call 节点的位置是成员名 `print` 的列。
            at = node.children[0]
        key = (getattr(at, "line", 0), getattr(at, "col", 0))
        if key not in self._unknown_qualifier_at:
            self._unknown_qualifier_at.add(key)
            self._err(
                "E-Type-Unknown",
                f"未知类型名 `{head}`：`{head}::{member}` 里的 `::` 左边必须是"
                f"**已知类型**（内置类型，或本模块 / `.tu` 里用 struct / enum / "
                f"error 声明的类型）。`{head}` 没有声明过——所以这**不是**"
                f"静态成员调用。\n"
                f"如果你要调的是全局函数 / 内建，就直接写 `{member}(…)`——"
                f"**不要带 `::`**。（限定名只取**最后一段**当被调名：成员名"
                f"撞上内建时（例如 `foo::print` 的 `print`），以前会 `check` "
                f"零诊断、`run` 照样输出，让人以为外部库已经接进来了。）\n"
                f"本语言没有命名空间：`namespace {head} {{ … }}` 会被**展平**，"
                f"里面的名字直接进全局作用域，不能再用 `{head}::` 限定。",
                at)
        return True

    def _check_type_names(self, n: Optional[Node]) -> bool:
        """递归检查一个**类型节点**里出现的类型名；返回是否发现未知名字。

        只走"类型位置"的节点：`type-name` / `type-ref` / `type-tuple` / `type-fn`；
        `path` / `type-const` / `lit-int`（容器容量等常量表达式）不看——
        它们不是类型名。`array<T, N>` / `fixedvec<T, N>` 的**第二个**实参
        是常量（`_parse_type_args` 里裸标识符会变成 `type-name`），
        靠 `const_names` 认，不靠位置猜。
        """
        if n is None:
            return False
        k = getattr(n, "kind", "")
        unknown = False
        if k == "type-name":
            _raw = n.name or ""
            # **旧类型名（P4-0，2026-11-11）**：`Option` / `Result` 报
            # `E-Type-OldName`（error），消息教新名。只留 `optional` /
            # `expected` —— 两套名字并存时，AI 会按文档写一个、按实现写另一个，
            # 而"哪个对"没有静态答案。
            _new = OLD_TYPE_NAMES.get(_raw)
            if _new is not None:
                _key = (n.line, n.col)
                if _key not in self._old_type_at:
                    self._old_type_at.add(_key)
                    self._err(
                        "E-Type-OldName",
                        f"类型名 `{_raw}` 已下线：改写成 `{_new}`"
                        f"（P4-0 名字对齐 C++：只留 `optional` / `expected` 两个写法；"
                        f"`std::{_new}<…>` 也认）。旧名不再接受，"
                        f"免得同一种类型有两套名字。", n)
                return True
            if not self._type_head_known(n.name or ""):
                unknown = True
                key = (n.line, n.col)
                if key not in self._unknown_type_at:
                    self._unknown_type_at.add(key)
                    self._err(
                        "E-Type-Unknown",
                        f"未知类型名 `{n.name}`：本语言没有这个类型。"
                        f"标量写 u8/u16/u32/u64/i8/i16/i32/i64/f32/f64/bool；"
                        f"容器写 vector/array/map/set/optional；"
                        f"自定义类型要在 .tu 里用 struct / enum / error 声明。\n"
                        f"若你写的是**指针声明**（`T * p` / `{n.name} * q`）："
                        f"请确认类型名 `{n.name}` 已声明在**这一行之前**——"
                        f"前向引用 C++ 也解析不了（`struct {n.name}` 要写在"
                        f"用它的函数前面；跨文件则写在 `.tu` 里）。",
                        n)
            for c in n.children:
                if getattr(c, "kind", "") == "type-args":
                    for a in c.children:
                        if self._check_type_names(a):
                            unknown = True
        elif k in ("type-ref", "type-slice", "type-tuple", "type-ptr"):
            for c in n.children:
                if self._check_type_names(c):
                    unknown = True
        elif k == "type-fn":
            # children = [type-fn-params, 返回类型]
            for c in n.children:
                if getattr(c, "kind", "") == "type-fn-params":
                    for a in c.children:
                        if self._check_type_names(a):
                            unknown = True
                elif self._check_type_names(c):
                    unknown = True
        return unknown

    def _decl_ty(self, n: Optional[Node]) -> Ty:
        """声明位置的类型：先查未知类型名；未知就退成 `UNKNOWN`。

        **为什么要退成 `UNKNOWN`**：`u128 x = 1;` 以前在赋值那一刻报
        `E-Type-Assign 声明为 u128，初值是 i64`——类型名本身没人管，
        消息还把读者引到"初值类型不对"上。现在类型名先报一条
        `E-Type-Unknown`，然后按 `UNKNOWN` 处理：**不再**用这个不存在的
        类型去和初值比、也别再拿它去级联出别的类型错误。
        """
        if self._check_type_names(n):
            return TY_UNKNOWN
        return ty_of_node(n)


    def _params_of(self, n: Node) -> List[Tuple[str, Ty]]:
        p = next((c for c in n.children if c.kind == "params"), None)
        out = []
        for c in (p.children if p else []):
            if c.kind == "param":
                out.append((c.name or "", self._decl_ty(c.children[0]) if c.children else TY_UNKNOWN))
            elif c.children:
                out.append(("", self._decl_ty(c.children[0])))
        return out

    def _ret_of(self, n: Node) -> Ty:
        node = next((c for c in n.children if c.kind.startswith("type")), None)
        base = self._decl_ty(node) if node is not None else TY_VOID
        if (n.value or {}).get("fallible"):
            return Ty("name", "Result", [base, Ty("name", "CalcError")])
        return base

    def _fn_type(self, n: Node) -> Ty:
        # **带返回类型**：与 `func(...)->R` 的 Ty 形状一致（末尾是返回类型）
        ret = self._decl_ty(next((c for c in n.children
                                  if c.kind in ("type-name", "type-ref", "type-fn")), None))
        return Ty("fn", name=n.name or "",
                  args=[t for _, t in self._params_of(n)] + [ret])

    def _spec_type(self, n: Node) -> Ty:
        ret = self._decl_ty(next((c for c in n.children
                                  if c.kind in ("type-name", "type-ref", "type-fn")), None))
        return Ty("fn", name=n.name or "",
                  args=[t for _, t in self._params_of(n)] + [ret])

    # ---------- 引用形参：只对**可变聚合**成立
    #: 解释器里"引用"能用 = 值本身是**可变对象**（`list` / `MapVal` / `StructVal`…）。
    #: 标量（`u64` / `f64` / `bool`）、枚举、元组都是不可变的：`u64& x` 即使
    #: 绑上，`x = 42` 也只改到形参那一份，**写不回调用方**——以前静默如此。
    _REF_CONTAINER_HEADS = frozenset((
        "vector", "array", "fixedvec", "map", "set",
        "unordered_map", "unordered_set", "stack", "queue", "deque",
    ))

    def _ref_target_ok(self, t: Ty) -> bool:
        t = self._deref_ty(t)
        if t.kind == "name":
            h = canon(t.name)
            return h in self._REF_CONTAINER_HEADS or self._is_struct_ty(t)
        return False

    def _check_ref_params(self) -> None:
        """`u64& x` 这类**值类型引用形参**必须静态拒绝。

        为什么不是实现写回：解释器的环境里放的是**值**（Python `int` 不可变），
        真写回要给每个标量绑定一个"引用单元"，并改掉读 / 写 / 下标 / 字段 /
        内核实参 / 返回值等全部路径——而值语义的拷贝记账（七个拷贝点）正好
        建立在这些值是值的前提上。代价与风险都不成比例。

        所以口径与**局部 `T&` 一律拒绝**保持一致：
        **引用只用于可变聚合（容器 / struct）的形参**。标量要写回就返回新值，
        或用 `vector<u64>& out` 承载输出。
        """
        impl_names = set()
        for mod in (self.tu, self.eln):
            for it in (mod.children if mod is not None else []):
                if it.kind == "fn" and it.name:
                    impl_names.add(it.name)
        for mod in (self.tu, self.eln):
            if mod is None:
                continue
            for it in mod.children:
                if it.kind not in ("fn", "spec") or not it.name:
                    continue
                # 有实现时只在实现上报（签名一致由 checker 保证），避免同一
                # 形参在 .tu / .eln 各报一遍。
                if it.kind == "spec" and it.name in impl_names:
                    continue
                ps = next((c for c in it.children if c.kind == "params"), None)
                for p in (ps.children if ps is not None else []):
                    if p.kind != "param" or not p.children:
                        continue
                    # **`const T&` 不是写回契约**（R1-A，2026-11-10）：
                    # C++ 里 `const u64& x` 是最普通的**只读**形参写法（省一次拷贝），
                    # 它**不承诺**写回；解释器按值传、函数体里也改不了，语义完全一致。
                    # 以前这里不区分 const，于是每一个照 C++ 写的 `const u64& x`
                    # 都被报 `E-Ref-Scalar`——`rename-and-go` 在第一个形参就断。
                    # `T&`（无 const）仍然拒：那才是真的写回。
                    if (p.value or {}).get("const"):
                        continue
                    tyn = p.children[0]
                    if getattr(tyn, "kind", "") != "type-ref":
                        continue
                    inner = ty_of_node(tyn.children[0]) if tyn.children else TY_UNKNOWN
                    # 泛型参数 / 推不出的类型：不猜（避免误报）。
                    if inner.kind == "unknown":
                        continue
                    if inner.kind == "name" and (
                            inner.name in self.type_params
                            or canon(inner.name) in self.type_params):
                        continue
                    if self._ref_target_ok(inner):
                        continue
                    self._err(
                        "E-Ref-Scalar",
                        f"`{p.name}` 的 `{inner.render()}&` 形参**写不回调用方**："
                        f"本语言的引用只对**可变聚合**成立（`vector<u64>&` / "
                        f"`Point&` 形参改元素 / 字段能传出去）；"
                        f"标量、枚举、元组是不可变值，`x = …` 只改到形参副本"
                        f"（以前静默如此）。要写回请改三选一："
                        f"① 返回值（` f({inner.render()} x) {inner.render()}`，"
                        f"调用方写 `x = f(x);`）；"
                        f"② 返回 `Result` 表达失败；"
                        f"③ 用容器承载输出（`func f(vector<{inner.render()}>& out)`，"
                        f"里面写 `out[0] = …`）。",
                        p)

    # ---------- 入口
    def check(self) -> List[Diag]:
        # **第 11 条**（P0 兼容层）：先把**可无损折叠**的 `static_cast<…>(x)`
        # 就地换成 `x`（`.tu` 里的类型也看得见）。折不动的保持原节点，
        # 由 `_expr` 的 `cast-static` 分支报 `E-Cast-Unsupported`。
        #
        # 放在最前面而不是在 `_expr` 里现场折：折叠要**换掉父节点的 child**，
        # 而 `_expr` 只有值、没有父指针。`_TypeRecorder` 自己跑的那遍必须跳过
        # 这一步（否则它折不了时又会进来一遍，无限递归）。
        if (getattr(self, "_fold_casts_before_check", True)
                and self.eln is not None
                and _tree_has_kind(self.eln, "cast-static")):
            resolve_static_cast(self.eln, self.tu)
        # **模块常量先查**：它们的初值在装载时求值，不是常量表达式就只能是
        # "静默 None"（`const N: u64 = 10 * 2;` 的 run 曾打印 None）。
        self._check_const_initializers()
        # **值类型引用形参**（`u64&`）：写不回调用方，静态拒绝。
        self._check_ref_params()
        for mod in (self.tu, self.eln):
            if mod is None:
                continue
            for it in mod.children:
                if it.kind == "fn":
                    self._check_fn(it)
        # 声明之外的两个节点：`&x` / `*x`（不在这条路上查就没人查——它们
        # 以前都是"运行期恒等"的静默节点）。
        for mod in (self.tu, self.eln):
            if mod is None:
                continue
            self._check_unary_usage(mod)
        return self.diags

    # ---------- 模块常量：初值必须是装载时可求的常量表达式
    #: 常量初值里允许的二元运算。**与 `interp._const_value` 同一组**：
    #: 静态这一层只判"形状能不能在装载时算"，运行期那一层负责真的算。
    #: 两边一旦漂移，就会出现"check 绿、run 给 None"——这正是本条要堵的洞，
    #: 所以新增运算符必须同时改两处（`tests/cases/test_typecheck.py` 钉了一组
    #: 正反例）。
    _CONST_BINOPS = frozenset((
        "+", "-", "*", "/", "%", "**", "&", "|", "^", "<<", ">>",
        "<", "<=", ">", ">=", "==", "!=",
    ))

    def _const_init_shape_ok(self, n: Optional[Node], seen: Set[str]) -> bool:
        """初值是不是**装载时可求值**的常量表达式（只看形状，不算值）。

        允许：字面量（整数 / 布尔 / 字符 / 浮点）、**前面已声明**的模块常量、
        括号、一元 `- + ~ not`、`_CONST_BINOPS` 的二元式、标量 `as`。
        不允许：函数调用、容器/下标、未声明的名字、形参——这些求不出值，
        以前会被 `interp._literal` 静默变成 `None`。
        """
        if n is None:
            return False
        k = getattr(n, "kind", "")
        if k in ("lit-int", "lit-bool", "lit-char", "lit-float"):
            return True
        if k == "path":
            return (n.name or "") in seen
        if k == "paren" and n.children:
            return self._const_init_shape_ok(n.children[0], seen)
        if k == "unary" and n.children:
            return ((n.value or "") in ("-", "+", "~", "!")
                    and self._const_init_shape_ok(n.children[0], seen))
        if k == "binary" and len(n.children) >= 2:
            return ((n.value or "") in self._CONST_BINOPS
                    and self._const_init_shape_ok(n.children[0], seen)
                    and self._const_init_shape_ok(n.children[1], seen))
        if k == "cast" and len(n.children) >= 2:
            want = ty_of_node(n.children[1])
            return (want.kind == "name"
                    and (canon(want.name) in NUMERIC or canon(want.name) in FLOATS)
                    and self._const_init_shape_ok(n.children[0], seen))
        return False

    def _check_const_initializers(self) -> None:
        seen: Set[str] = set()
        for mod in (self.tu, self.eln):
            if mod is None:
                continue
            for it in mod.children:
                if it.kind != "const" or not it.name:
                    continue
                init = it.children[-1] if len(it.children) >= 2 else None
                if init is None or not self._const_init_shape_ok(init, seen):
                    self._err(
                        "E-Const-NotConstant",
                        f"模块常量 `{it.name}` 的初值不是**常量表达式**："
                        f"常量在装载时求值，只认字面量、**前面已声明**的模块常量、"
                        f"括号、算术 / 位运算 / 比较，以及标量 `as`。"
                        f"函数调用、形参、容器的下标 / 字段都求不出来——"
                        f"以前这类初值会静默变成 `None`。"
                        f"把初值改成一个能直接算出来的式子，"
                        f"或改在函数体里用 `const` 局部变量算。",
                        init or it)
                    continue
                # **初值类型也要和声明对得上**：`const X: u64 = true;` 以前
                # 连这句都不查（常量初值从不进 `_expr`），run 得到 `True`——
                # 声明写着 u64、值是 bool。与局部 `const` 同一条规则。
                _decl = (self._decl_ty(it.children[0])
                         if it.children else TY_UNKNOWN)
                _vt = self._expr(init, Scope(self.globals))
                if not compatible(_vt, _decl):
                    self._err(
                        "E-Type-Assign",
                        f"模块常量 `{it.name}` 声明为 {_decl.render()}，"
                        f"初值是 {_vt.render()}", init)
                seen.add(it.name)

    # ---------- `&x` / `*x`：位置判据（`*` 的类型判据在 `_deref_expr`）
    def _check_unary_usage(self, mod: Node) -> None:
        """`&x`（取地址）的**位置**判据；`*x` 由 `_deref_expr` 按类型判。

        ## 为什么 `&x` 不是"一律允许"

        样例 `calculator.eln` 里有 `apply_top(& values, & operators)`，而同一个
        函数在 `pop_until_open` 里又写成 `apply_top(values, operators)`
        ——`&` 是**冗余的显式借用标记**，被调函数的形参本来就是
        `array<i64, MAX_DEPTH>&`。样例内容是冻结的。

        所以判据卡在**语义成立**的位置：

        | 写法 | 结论 |
        |---|---|
        | `f(&x)`，第 i 个形参是 `T&` / `const T&` | ✅ 允许（显式借用标记） |
        | `f(&x)`，第 i 个形参是 `T*` / `const T*` | ✅ 允许（指针实参） |
        | `T* p = &x;`（声明类型是指针） | ✅ 允许（取地址） |
        | `p = &x;`（赋值右边，类型层再比 `T*`） | ✅ 允许 |
        | `return &x;`（返回类型是指针） | ✅ 允许 |
        | `print(&x)` / `g(&x)`（形参不是引用/指针） | ❌ `E-Unary-Unsupported` |
        | `u64 y = &x;`（声明不是指针） | ❌ `E-Unary-Unsupported` |
        | `&(1 + 2)` | ❌（取地址只能作用于变量 / 下标 / 字段） |

        **位置判据为什么还要留**：`print` 的形参是内建可变参数，类型层看不出
        问题；而运行期 `&` 目前仍是恒等（运行期未接，见 `文档/能力边界.md`），
        静默通过会把"地址"当普通值传出去。
        """
        def params_of_callee(name: str):
            node = self.funcs.get(name) or self.specs.get(name)
            if node is None:
                return None
            return self._params_of(node)

        def is_ref_param(params, idx) -> bool:
            if not params or idx >= len(params):
                return False
            return params[idx][1].kind == "ref"

        def is_ptr_param(params, idx) -> bool:
            if not params or idx >= len(params):
                return False
            return params[idx][1].kind == "ptr"

        def walk(n, ref_ctx, ptr_ok=False, ret_ptr=False) -> None:
            if n is None:
                return
            k = getattr(n, "kind", "")
            if k == "ref":
                operand = n.children[0] if n.children else None
                # `&` 只能作用于**位置**（`&(1 + 2)` / `&f()` 没有意义）
                if operand is not None and getattr(operand, "kind", "") not in (
                        "path", "index", "field", "deref"):
                    self._err(
                        "E-Unary-Unsupported",
                        "`&` 后面必须是一个变量 / 下标 / 字段（要取地址的那个位置），"
                        "不能是一个临时表达式。", n)
                    return
                if ref_ctx is not None:
                    callee, idx, params = ref_ctx
                    if not (is_ref_param(params, idx) or is_ptr_param(params, idx)):
                        self._err(
                            "E-Unary-Unsupported",
                            f"`{callee}` 的这个形参既不是引用（`T&` / `const T&`）"
                            f"也不是指针（`T*`）：`&` 只在把实参传给引用 / 指针形参时"
                            f"成立，这里直接写实参即可（`{callee}(x)`）。", n)
                        return
                    walk(operand, None)
                    return
                if ptr_ok:
                    walk(operand, None)
                    return
                self._err(
                    "E-Unary-Unsupported",
                    "`&x` 的结果是**指针**（`T*`），这里不是指针位置："
                    "取地址只能用在 `T* p = &x;` / `p = &x;` / 指针形参实参 / "
                    "指针返回位置。\n"
                    "本语言没有从指针到标量的隐式转换，"
                    "要用 `x` 的值就直接写 `x`。", n)
                return
            if k in ("var-decl", "const-decl", "val-decl"):
                ty = n.children[0] if n.children else None
                is_ptr = getattr(ty, "kind", "") == "type-ptr"
                for c in n.children[1:]:
                    walk(c, None, is_ptr, ret_ptr)
                return
            if k == "assign" and len(n.children) >= 2:
                walk(n.children[0], None, False, ret_ptr)
                walk(n.children[1], None, True, ret_ptr)
                return
            if k == "return":
                for c in n.children:
                    walk(c, None, ret_ptr, ret_ptr)
                return
            if k == "fn":
                ret = self._ret_of(n)
                rp = _unref(ret).kind == "ptr"
                for c in n.children:
                    walk(c, None, False, rp)
                return
            if k in ("call", "method-call"):
                args = next((c for c in n.children if getattr(c, "kind", "") == "args"), None)
                callee = n.name if k == "call" else None
                params = params_of_callee(callee) if callee else None
                for c in n.children:
                    if c is args:
                        continue
                    walk(c, None, False, ret_ptr)
                if args is not None:
                    for i, a in enumerate(args.children):
                        walk(a, (callee or n.name or "?", i, params), False, ret_ptr)
                return
            for c in n.children:
                walk(c, None, ptr_ok, ret_ptr)

        walk(mod, None)


    def _err(self, code: str, msg: str, n: Optional[Node]) -> None:
        self.diags.append(Diag(code, msg, getattr(n, "line", 0) if n else 0,
                               getattr(n, "col", 0) if n else 0))

    def _warn(self, code: str, msg: str, n: Optional[Node]) -> None:
        self.diags.append(Diag(code, msg, getattr(n, "line", 0) if n else 0,
                               getattr(n, "col", 0) if n else 0,
                               severity="warning"))

    def _check_fn(self, fn: Node) -> None:
        sc = Scope(self.globals)
        for name, ty in self._params_of(fn):
            if name:
                self._decl(sc, Sym(name, "var", ty, fn))
        block = next((c for c in fn.children if c.kind == "block"), None)
        body_scope = sc
        if block is not None:
            ret = self._ret_of(fn)
            fallible = bool((fn.value or {}).get("fallible"))
            body_scope = self._block(block, sc, ret, fallible)
        # 内核候选体也要查（它们共享宿主的作用域）
        for kern in fn.children:
            if kern.kind != "kernels":
                continue
            for cand in kern.children:
                if cand.kind != "candidate":
                    continue
                b = next((c for c in cand.children if c.kind == "block"), None)
                if b is not None:
                    # 候选默认 inline：能读宿主局部变量（`n`），所以用宿主的整块作用域
                    self._block(b, body_scope, self._ret_of(kern), True)

    def _decl(self, sc: Scope, s: Sym) -> None:
        # 同一作用域重复
        if s.name in sc.names:
            self._err("E-Name-Duplicate",
                      f"`{s.name}` 在同一作用域内重复声明", s.node)
            return
        # 遮蔽外层（用户定：禁止）
        outer = sc.parent
        while outer is not None:
            if s.name in outer.names:
                self._err("E-Name-Shadowed",
                          f"`{s.name}` 遮蔽了外层同名符号；本语言禁止遮蔽",
                          s.node)
                return
            outer = outer.parent
        sc.names[s.name] = s

    # ---------- 语句
    def _block(self, blk: Node, outer: Scope, ret: Ty, fallible: bool) -> Scope:
        sc = Scope(outer)
        # **块内的空指针状态不外泄**：离开这个块时回到进入时的状态。
        # 保守方向是"少报"（宁可漏掉一条可证的 NullDeref，也不误报）。
        saved = dict(self._nulls)
        try:
            for st in blk.children:
                self._stmt(st, sc, ret, fallible)
        finally:
            self._nulls = saved
        return sc

    def _check_list_init_elems(self, st: Node, ty: Ty, init: Node, sc: Scope) -> None:
        """`array<T,N> a = {…}` / `vector<T> v = {…}`：逐元素比类型。

        **规则与普通赋值完全一致**（都走 `compatible`）：

        | 元素类型 → 声明类型 T | 结果 | 依据 |
        |---|---|---|
        | ① 同为整型（`u8`/`i64`/…） | ✅ 兼容 | `compatible` 对 `NUMERIC` 内部一律放行 |
        | ② 整型 ↔ 浮点（`f32`/`f64`） | ✅ 兼容 | 同上（N2 的隐式转换检查在 checker，不在这里重复） |
        | ③ 整型 → `bool` | ❌ `E-Init-ElemType` | `bool b = 1;` 今天已经报 `E-Type-Assign`（实测 `@2:5 初值是 i64`） |
        | ④ `bool` → 整型 | ❌ | 同上，`bool` 不在 `NUMERIC` 里 |
        | ⑤ 字符串/其它非数值 → 整型 | ❌ | 同上 |
        | ⑥ 元素类型推不出（`?`） | ✅ 不报 | `compatible` 对 UNKNOWN 一律放行，避免误报 |
        | ⑦ 整数字面量超出目标宽度（`u8` 放 300） | ✅ **不报** | `u8 x = 300;` 今天也不报（无溢出检查），保持一致 |
        | ⑧ 空 `{}` | ✅ 不报 | 没有元素可比 |

        **非容器类型的大括号初始化在这里明确拒绝**（2026-11-12，见
        `_reject_brace_init`）：解释器 `_build_from_list_init` **只认**
        `vector` / `array` / `FixedVec`，别的类型一律在运行期抛
        `InterpError: 不支持大括号初始化`。以前这里对非容器直接 `return`，
        于是 `u64 x = {1};` / `map<K,V> m = {};` 全是「check 绿、run 崩」。
        **宁可检查期报错，也不许放行一个只会崩的写法。**
        """
        head = canon(ty.name) if ty.kind == "name" else ""
        if head not in ("array", "vector", "fixedvec"):
            self._reject_brace_init(st, ty, init)
            return
        if not ty.args:
            return
        elem_ty = ty.args[0]
        for idx, elem in enumerate(init.children, 1):
            et = self._expr(elem, sc)
            if not compatible(et, elem_ty):
                self._err("E-Init-ElemType",
                          f"`{st.name}` 第 {idx} 个元素类型不符："
                          f"期望 {elem_ty.render()}，实际是 {et.render()}",
                          elem)

    def _reject_brace_init(self, st: Node, ty: Ty, init: Node) -> None:
        """`T x = {…}` 且 `T` **不是** vector/array/FixedVec → 检查期拒绝。

        ## 为什么必须在这里挡

        解释器只给三种容器实现大括号初始化（`interp._build_from_list_init`），
        别的类型在运行期直接抛 `InterpError: … 不支持大括号初始化`。而
        `_check_list_init_elems` 以前对非容器 `return`，`_expr(list-init)` 又
        返回 UNKNOWN（`compatible` 对 UNKNOWN 放行）——于是下面这些**全是
        `check` rc=0、`run` rc=1**：

        | 写法 | 运行期 |
        |---|---|
        | `u64 x = {1};` / `f64 x = {1.5};` / `bool b = {1};` | `InterpError` |
        | `E x = {1};`（枚举）/ 元组 `{…}` | `InterpError` |
        | `map<K,V> m = {};` / `set` / `stack` / `queue` | `InterpError` |
        | `u64* p = {};` | `InterpError` |
        | `S x = {…};`（字段顺序看不见） | `InterpError` |

        ## 为什么 struct 单独一条码

        struct 的位置式初始化在 **C++ 里是合法且常见**的，只是本语言的
        parser 只能在**同一文件、使用之前**看见字段顺序（`.tu` 里的 struct
        对 parser 不可见）。所以它给 `E-Struct-PosInitUnsupported` 并明确
        指出两条可走的路（字段式 / 把 struct 写在使用之前），而不是一句
        "不支持"。

        其余类型没有"补个声明就能成立"的等价物，给 `E-Init-BraceUnsupported`。
        """
        # 初值类型还推不出（`auto` 已单独报 E-Compat-AutoNoType） /
        # 局部引用已单独报 E-Ref-Local：不在这里叠第二条。
        if ty.kind in ("unknown", "ref"):
            return
        decl = self.types.get(ty.name) if ty.kind == "name" else None
        if decl is not None and decl.kind == "struct":
            fields = [f.name for f in (decl.children or [])
                      if getattr(f, "kind", "") == "field" and f.name]
            _fl = ", ".join(fields) if fields else "…"
            _first = fields[0] if fields else "…"
            self._err(
                "E-Struct-PosInitUnsupported",
                f"`{st.name}` 的类型是 struct {ty.name}，初值却是**位置式**大括号 "
                f"`{{…}}`。这个文件里看不见 `struct {ty.name}` 的字段顺序"
                f"（它声明在 `.tu`，或在本函数之后），无法把第 i 个值按声明顺序"
                f"落到第 i 个字段上——**猜顺序会静默错位**，所以这里直接拒绝。\n"
                f"改法：① 写字段式 `{ty.name} {st.name} = {ty.name} {{ {_first}: …, "
                f"… }};`（字段顺序：{_fl}；这种写法跨文件也成立）；"
                f"② 若这是 C++ 源码（`--compat`），把 `struct {ty.name} {{ … }}` "
                f"写在**使用它的函数之前**、且与实现同一个 `.eln`，就可以写 "
                f"`{ty.name} {st.name} = {{…}};`。",
                init)
            return
        _head = canon(ty.name) if ty.kind == "name" else ""
        if ty.kind == "ptr":
            _hint = ("指针的默认值是空指针：写 `T* p = nullptr;`，"
                     "指向对象写 `T* p = &x;`。")
        elif ty.kind == "tuple":
            _hint = f"元组直接写圆括号：`{ty.render()} {st.name} = (a, b);`。"
        elif _head in ("map", "unordered_map", "set", "unordered_set",
                       "stack", "queue", "deque"):
            _hint = (f"这种容器的空初始化写 `{ty.render()} {st.name};`"
                     f"（默认构造就是空的），不要写 `= {{}};`。")
        elif ty.kind == "name" and ty.name in self.enums:
            _hint = (f"枚举值不是容器：写成 `{ty.name} {st.name} = "
                     f"{ty.name}::<变体>;`（变体用 `::`，不是 `{{…}}`）。")
        else:
            _hint = (f"把大括号去掉、直接写表达式：`{ty.render()} {st.name} = "
                     f"<表达式>;`。")
        self._err(
            "E-Init-BraceUnsupported",
            f"`{st.name}` 的类型 {ty.render()} 不支持大括号初始化 `{{…}}`："
            f"本语言（以及运行期解释器）只对 `vector<T>` / `array<T, N>` / "
            f"`FixedVec<T, N>` 这三种容器成立。\n改法：{_hint}", init)

    def _compat_auto_ty(self, st: Node, sc: Scope) -> Ty:
        """声明位的类型；`--compat` 的 `auto` 在**这里**推断（第 3 条）。

        **推断不出来就报 `E-Compat-AutoNoType`，不猜**：`auto` 的类型是
        **编译期**确定的（契约与 κ 都依赖静态类型），猜一个"看起来合理"的
        类型会让 `check` 绿、`cost` 的数字却是按错类型算的。

        推断成功时把 `st.children[0]`（`type-auto`）**当场换成**具体类型节点：
        此后整条流水线（interp / 成本域 / 后续语句的类型比较）看到的 AST
        与用户手写该类型**逐字节同形**。
        """
        first = st.children[0] if st.children else None
        if getattr(first, "kind", "") != "type-auto":
            return self._decl_ty(first) if st.children else TY_UNKNOWN
        # 只在 `--compat` 下会产出 `type-auto`；这里不重复判开关——
        # 不判反而更安全：万一有人手搓一个 `type-auto` 节点，也不会漏掉推断。
        init = st.children[1] if len(st.children) >= 2 else None
        _hint = (f"改法：① 写全类型（`u64 {st.name or 'x'} = …;`）；"
                 f"② 让初值有一个**静态可判**的类型（字面量 / 已声明变量 / "
                 f"有 spec 或实现的函数调用 / 容器的 `.size()`）。")
        if init is None:
            self._err("E-Compat-AutoNoType",
                      f"`auto {st.name or '?'};` 推不出类型：`auto` 只能从**初值**推断。\n"
                      + _hint, st)
            return TY_UNKNOWN
        if getattr(init, "kind", "") == "list-init":
            self._err(
                "E-Compat-AutoNoType",
                f"`auto {st.name or '?'} = {{…}};` 推不出类型：大括号初始化本身"
                f"没有类型（与 C++ 的 `auto x = {{1, 2}}` 同样不成立）。\n"
                f"改法：① 写全容器类型 `vector<u64> {st.name or 'x'} = {{…}};`；"
                f"② 结构化绑定写 `const (u64, u64) t = f();` 再取 `t.0` / `t.1`。",
                init)
            return TY_UNKNOWN
        vt = self._expr(init, sc)
        node = ty_to_node(vt)
        if node is None:
            self._err(
                "E-Compat-AutoNoType",
                f"`auto {st.name or '?'}` 的类型推不出来：初值的类型是 "
                f"{vt.render()}，它不是**可写出来的**类型。\n" + _hint, st)
            return TY_UNKNOWN
        # 位置沿用 `auto` 那个 token：诊断指向用户写的那一处。
        node.line = getattr(first, "line", 0)
        node.col = getattr(first, "col", 0)
        st.children[0] = node
        return vt

    def _stmt(self, st: Node, sc: Scope, ret: Ty, fallible: bool) -> None:
        k = st.kind

        if k in ("const-decl", "var-decl"):
            # **第 3 条**：`auto x = …;` 是"类型待定"的声明。必须先判：推断会把
            # `children[0]` 换成具体类型，之后就看不出它原本是 `auto` 了。
            _was_auto = bool(st.children) and \
                getattr(st.children[0], "kind", "") == "type-auto"
            ty = self._compat_auto_ty(st, sc)
            # **本语言没有局部引用**（文档 §3：引用只在**形参位置**，§`T&` 的
            # "别名"只列了形参与 `-> T&` 返回）。`vector<u64>& r = ys;` 以前
            # check 绿、run 把 r 当**拷贝**：`r[0] = 9` 改不到 `ys`。这比报错
            # 更坏——名字写着引用、行为是拷贝。静态可判，就在 check 里挡。
            if ty.kind == "ref":
                _base = ty.args[0].render() if ty.args else "T"
                # **`const T& x = expr;` 在 `--compat` 下放行**（2026-11-12）：
                # C++ 里它只用于"只读绑定"，而本语言把它当拷贝——**读的结果一样**，
                # 而且 C++ 侧根本改不了它（const），所以不会出现"写着引用、行为是拷贝"。
                # **非 const 的 `T&` 局部仍拒绝**：那种写法 C++ 能改到原对象，我们改不到，
                # 放行就是静默错值。
                # AST 里 `const` 不挂在类型上，而是**声明种类**：`const T& x` 是
                # `const-decl` + `type-ref`，`T& x` 是 `var-decl` + `type-ref`。
                _const_ref = (k == "const-decl") or ("const" in _base) or \
                    (getattr(ty, "value", "") == "const")
                if _compat.enabled() and _const_ref:
                    self._decl(sc, Sym(st.name, "var", ty.args[0] if ty.args else ty, st))
                    return
                self._err(
                    "E-Ref-Local",
                    f"局部变量 `{st.name}` 不能声明成引用 `{_base}&`："
                    f"本语言没有局部引用 / 别名（引用只出现在**形参位置**，"
                    f"`T&` / `const T&`）。这样写只会得到一份**拷贝**，"
                    f"改 `{st.name}` 改不到原对象。"
                    f"要共享请把容器提成形参传 `T&`："
                    f"`void f(vector<u64>& {st.name}) {{ … }}`；"
                    f"只改本函数里的这个容器就直接写 `{st.name}[0] = …`。",
                    st)
            self._decl(sc, Sym(st.name, "var", ty, st))
            init = st.children[-1] if len(st.children) >= 2 else None
            # **大括号初始化：逐个元素比类型**（`E-Init-ElemType`）。
            # 用与普通赋值**同一套** `compatible`，不新造更松/更严的规则：
            #   `bool b = 1;` 今天报 E-Type-Assign，所以 `array<bool,3> = {1,2,3}`
            #   也必须报；`u8 x = 300;` 今天不报溢出，这里也不报。
            if init is not None and getattr(init, "kind", "") == "list-init":
                self._check_list_init_elems(st, ty, init, sc)
            # `auto` 的初值**已经在上面的推断里求过类型**：再求一遍会让初值里的
            # 每条诊断都出现两次（`_expr` 有副作用：它报诊断）。推断出的 `ty`
            # 本来就等于初值类型，所以这里也不存在"要比较"的东西。
            if init is not None and not _was_auto:
                vt = self._expr(init, sc)
                if not compatible(vt, ty):
                    self._err_scalar_mismatch("decl", st.name, ty, vt, st)
            # **③ 空状态的起点**：`T* p = nullptr;` → 确定为空；
            # `T* p = &x;` → 确定非空；无初值 / 其它 → 不知道（不记）。
            if ty.kind == "ptr":
                if init is None:
                    self._nulls.pop(st.name, None)
                else:
                    self._nulls[st.name] = (getattr(init, "kind", "") == "lit-null")
            return

        if k == "assign":
            self._mark_incdec_rhs(st.children[0], st.children[1])
            # **同一条语句里"写 m[k]"时，RHS 里的 `m[k]` 不再报 W-Map-SilentInsert**：
            # `freq[w] = freq[w] + 1` 是词频的标准写法，插入是写者本意。
            # 只按**变量名**闭包（`m`），所以 `xs[i] = m[i]` 里的 `m[i]` 照样报。
            _saved = self._map_written_in_stmt
            self._map_written_in_stmt = set(_saved)
            _lb = (st.children[0].children[0]
                   if getattr(st.children[0], "children", None) else None)
            if (getattr(st.children[0], "kind", "") == "index"
                    and getattr(_lb, "kind", "") == "path"):
                self._map_written_in_stmt.add(_lb.name or "")
            try:
                vt = self._expr(st.children[1], sc)
                tt = self._place(st.children[0], sc)
            finally:
                self._map_written_in_stmt = _saved
            if not compatible(vt, tt):
                self._err_scalar_mismatch(
                    "assign", getattr(st.children[0], "name", "") or "?", tt, vt, st)
            # **③ 空状态推进**：指针变量的重新赋值直接更新（直线路径）。
            lhs = st.children[0]
            if lhs.kind == "path" and _unref(tt).kind == "ptr":
                self._nulls[lhs.name or ""] = (
                    getattr(st.children[1], "kind", "") == "lit-null")
            return

        if k == "return":
            if st.children:
                vt = self._expr(st.children[0], sc)
                want = ret
                if fallible:
                    # `T!` 里 `return x` 自动包 Ok；也允许直接返回 Result
                    if vt.kind == "name" and vt.name == "Result":
                        return
                    want = ret.args[0] if (ret.kind == "name" and ret.args) else TY_UNKNOWN
                if not compatible(vt, want):
                    msg = (f"返回类型不符：签名 {want.render()}，"
                           f"实际 {vt.render()}")
                    # **包装值直接返回**：只说"不符"等于把作者丢在原地——他看不出
                    # `Result` 是哪来的、也不知道该改签名还是该解包。
                    # （2026-11-11 起整数 `/` `%` 返回普通整数，这条不再为除法服务；
                    #   显式 `Ok(...)` 等确实产生 `Result` 的表达式仍会走到这里。）
                    if vt.kind == "name" and vt.name == "Result" \
                            and want.name != "Result":
                        msg += (
                            f"。{vt.render()} 是包装值，不能直接当 "
                            f"{want.render()} 返回：要么把签名改成可失败的 "
                            f"`-> {want.render()}!`（`return x` 自动包 Ok），"
                            f"要么先解包（在 `-> T!` 函数里写 `x?`，或 "
                            f"`match (x) {{ Ok(v) => …, Err(e) => … }}`）再返回")
                    elif _needs_unwrap(vt) and not _needs_unwrap(want):
                        # K5 的返回位置：`-> u64 { return vec_pop(h); }` 以前
                        # 静默通过（内建类型是 UNKNOWN），运行期把 Option 当 u64。
                        msg += (f"。{vt.render()} 是包装值/不透明值，"
                                f"不能直接当 {want.render()} 返回。{_unwrap_hint(vt)}")
                    self._err("E-Type-Return", msg, st)
            return

        if k == "if":
            cond = st.children[0] if st.children else None
            ct = self._expr(cond, sc) if cond is not None else TY_BOOL
            # **`if (p)`**：指针可以直接当条件（C++ 同义：非空为真）。
            # **`--compat` 下整数可作条件**（2026-11-12）：判据见 `_cond_ok`。
            if not self._cond_ok(ct):
                self._err("E-Type-Cond",
                          f"if 的条件应是 bool 或指针，实际 {ct.render()}", st)
            guard = self._null_guard(cond)
            saved = dict(self._nulls)
            if guard and guard[0]:
                self._nulls[guard[0]] = bool(guard[1])
            try:
                for c in st.children[1:]:
                    if c.kind == "block":
                        self._block(c, sc, ret, fallible)
                    elif c.kind == "if":
                        self._stmt(c, sc, ret, fallible)
            finally:
                self._nulls = saved
            return

        if k == "while":
            cond = st.children[0] if st.children else None
            ct = self._expr(cond, sc) if cond is not None else TY_BOOL
            if not self._cond_ok(ct):
                self._err("E-Type-Cond",
                          f"while 的条件应是 bool 或指针，实际 {ct.render()}", st)
            # **① 的遍历上界**：条件由指针决定（`while (p)` /
            # `while (p != nullptr)`）时，迭代次数 = **链长**，而链长不是
            # 语言的尺寸量——除非条件里同时有尺寸比较（`i < xs.length()`）。
            # 推不出就**明确报无法给出上界**，绝不给一个数（κ 不许被估小）。
            if (self._cond_mentions_ptr(cond, sc)
                    and not self._cond_is_size_bound(cond)):
                self._err(
                    "E-Pointer-NoBound",
                    "指针遍历的**上界无法给出**：这个 while 的迭代次数由指针"
                    "（链长）决定，而链长不是声明尺寸量。\n"
                    "三种写法能给出上界：① 改成遍历容器 "
                    "`for (const auto& x : p->arr) { … }`（上界 = `arr` 的 "
                    "`length()`，这是现有尺寸量）；② 在条件里加一个**与尺寸量比较**的计数器"
                    "（`i < n` / `i < xs.length()`，并每轮推进）；"
                    "③ 在 `.tu` 里把上界写成 `repeats`/`target` 的一部分。\n"
                    "**没有上界时不能给数字**：κ 记 ⊤ 并注明原因，"
                    "不许写一个看起来合理的常数。", st)
            guard = self._null_guard(cond)
            saved = dict(self._nulls)
            if guard and guard[0]:
                self._nulls[guard[0]] = bool(guard[1])
            try:
                for c in st.children[1:]:
                    if c.kind == "block":
                        self._block(c, sc, ret, fallible)
            finally:
                self._nulls = saved
            return

        if k == "for":
            # 遍历源的类型（决定循环变量类型）：`for (const auto& k : m)` 里 k 是
            # **键**类型，`for (const auto& x : xs)` 里 x 是元素类型。以前一律
            # UNKNOWN，于是 `s = s + k`（k 是 u64 键）这种写法拿不到类型信息。
            # **绑定类别（引用/按值）不改静态类型**：`auto&` 与 `auto` 的元素类型
            # 相同；拷贝与否是成本/运行期口径（见 `interp._k_stmt_for`）。
            _base = next((c for c in st.children if c.kind not in ("block",)), None)
            _bt = self._expr(_base, sc) if _base is not None else TY_UNKNOWN
            while _bt.kind == "ref" and _bt.args:
                _bt = _bt.args[0]
            # **遍历源是 `void`**（2026-11-13）：`for (auto& x : g())` 以前
            # 拿到 UNKNOWN 元素类型、check 绿，运行期在 `None` 上迭代才炸。
            if _is_void_ty(_bt):
                self._err_void_value("`for` 的遍历源", st)
                return
            _elem = TY_UNKNOWN
            #: **这一轮的元素是不是 `map` 的 `(键, 值)`**（2026-11-12）：
            #: C++ 的 `for (const auto& kv : m)` 里 `kv` 是
            #: `pair<const K, V>`，**不是键**。以前这里给的是 `K`，于是
            #: `kv.first` 拿不到类型、`kv.second` 更谈不上——运行期直接报
            #: 「未实现的字段访问」。现在静态类型就是本语言已有的二元组
            #: `(K, V)`，与运行期同形（`interp._k_stmt_for` 给的就是元组）。
            _is_pair = False
            if _bt.kind == "name" and _bt.args:
                _h = canon(_bt.name)
                if _h in ("map", "unordered_map"):
                    if len(_bt.args) >= 2:
                        _elem = Ty("tuple", args=[_bt.args[0], _bt.args[-1]])
                        _is_pair = True
                    else:
                        _elem = _bt.args[0]      # 类型实参不全：不猜，保持旧口径
                else:
                    _elem = _bt.args[-1]         # vector<T> / set<T> 的元素
            _origin = ""
            if _is_pair:
                # **绑定类别决定能不能写回去**（见 `interp._k_stmt_for`）：
                # 元素是**每轮现造**的 `(键, 值)`，整体重新绑定写不回表；
                # 字段 `.0` / `.1` 也没有可写的存储。写位置一律在 `_place`
                # 报清楚（否则就是 check 绿、run 崩）。
                _origin = {"mut": "map-pair-ref",
                           "const": "map-pair-constref"}.get(
                               getattr(st, "ref", None), "map-pair-val")
            for c in st.children:
                if c.kind == "block":
                    inner = Scope(sc)
                    # `st.value` 以前存 `for (i, x in xs)` 的下标名；那种写法
                    # 已删除（2026-11-10 夜），现在恒为空。
                    _ix = getattr(st, "value", None)
                    if _ix:
                        self._decl(inner, Sym(_ix, "var", TY_U64, st))
                    if st.name:
                        self._decl(inner, Sym(st.name, "var", _elem, st, _origin))
                    self._block(c, inner, ret, fallible)
            return

        if k == "match":
            self._match(st, sc, ret, fallible)
            return

        if k == "block":
            self._block(st, sc, ret, fallible)
            return

        if k == "expr-stmt" and st.children:
            self._expr(st.children[0], sc)
            return

        if k == "dispatch":
            self._expr(st, sc)
            return

    def _place(self, n: Node, sc: Scope) -> Ty:
        if n.kind == "path":
            s = sc.lookup(n.name or "")
            if s is None:
                self._err("E-Name-Undefined", f"未声明的名字 `{n.name}`", n)
                return TY_UNKNOWN
            # **`map` 迭代出的 `(键, 值)` 不能整体重新绑定**（2026-11-12）：
            # `for (auto& kv : m) { kv = (k, v); }` 在 C++ 里改的是表里那一项，
            # 我们绑的是每轮现造的一份，写回去只会丢写。引用绑定在运行期
            # 也会拒绝（`interp._k_stmt_for` 的 finally）——check 必须同一条口径。
            if str(getattr(s, "origin", "")) in ("map-pair-ref",
                                                 "map-pair-constref"):
                self._err(
                    "E-Assign-Immutable",
                    f"`{n.name}` 是 `map` 迭代出的 `(键, 值)`，**不能整体重新"
                    f"绑定**（它是每轮现造的一份，写回去只会丢写）。\n"
                    f"改表请写 `m[键] = 新值` / `m.put(键, 新值)`；"
                    f"按值取一份就在循环里声明别的局部变量。", n)
                return TY_UNKNOWN
            return s.ty
        # **这是"要被写的那块存储"**：`m[k] = v` 里的 `m[k]` 插入是写者本意，
        # 不该报 `W-Map-SilentInsert`（见 `_expr` 的 index 分支）。
        self._write_lvalue += 1
        try:
            return self._expr(n, sc)
        finally:
            self._write_lvalue -= 1

    def _mark_incdec_rhs(self, lhs: Node, rhs: Node) -> None:
        """`m[k]++` 的**回读**不是读：它是同一次写的一部分。

        `i++;` 由 parser desugar 成 `i = i + 1`（`_desugar_incdec`），RHS 的
        左操作数是 LHS 的**深拷贝**——它带着**同一个 `(line, col)`**。于是
        `m[k]++` 在类型侧看起来像"写一次 + 读一次 `m[k]`"，会误报
        `W-Map-SilentInsert`（实测：`cnt[100]++` 报在 `++` 那一行）。

        这里按**形状**认这个 desugar（parser 不给标记，`parser.py` 不在本次
        改动范围）：RHS 是 `binary`，左操作数与 LHS 同类且位置完全相同。
        认出来就给那个拷贝打 `_write_place`，`_expr` 的 index 分支看见它
        就当作写路径，不告警。

        为什么不担心误挡真读：**手写**的 `m[k] = m[k] + 1` 里两个 `m[k]` 的
        `col` 一定不同（不同起点），认不出来；只有深拷贝会同位。
        """
        if getattr(rhs, "kind", "") != "binary" or not rhs.children:
            return
        a = rhs.children[0]
        if getattr(a, "kind", "") not in ("index", "field", "deref"):
            return
        if (getattr(a, "kind", "") == getattr(lhs, "kind", "")
                and getattr(a, "line", 0) == getattr(lhs, "line", 0)
                and getattr(a, "col", 0) == getattr(lhs, "col", 0)):
            try:
                a._write_place = True
            except Exception:                            # noqa: BLE001
                pass

    def _in_map_write_stmt(self, n: Node) -> bool:
        """这条 `m[k]` 是不是**同一条语句里正在写同一个容器**的回读。

        `freq[w] = freq[w] + 1`（词频）的 RHS 也会插入，但那正是写者的本意；
        为它报 `W-Map-SilentInsert` 只会让标准写法戴着噪音跑。
        判据只看**变量名**，由 `_stmt` 的 `assign` 分支按语句临时登记。
        """
        if not self._map_written_in_stmt:
            return False
        recv = n.children[0] if n.children else None
        return (getattr(recv, "kind", "") == "path"
                and (recv.name or "") in self._map_written_in_stmt)

    def _warn_map_silent_insert(self, n: Node) -> None:
        """`W-Map-SilentInsert`：`m[k]` 在**只读**位置会静默插入默认值（D9 第三类）。

        判据（全部静态、不需要跑）：出现 `m[k]` 这个**读**表达式，且 `m` 的
        静态类型是 `map<K,V>` / `unordered_map<K,V>`。赋值目标（`m[k] = v`）
        不算——那是写者明确的写。

        **为什么必须报**：C++ 里 `m[k]` 同样是"缺键就插入"，也同样是经典陷阱
        （`if (m[k] == 0)` 一句只想读的判断，悄悄把表撑大；`size()` 变了、
        后面的 `keys()` / 迭代多出一项）。本语言的语义既然对齐了 C++，
        这个陷阱就要在 `check` 里看得见，而不是只写在文档里。

        **为什么是 warning**：这是**合法的 C++ 语义**，不是错误；`--deny-warnings`
        之类"把警告当错误"的开关才能升级它。
        """
        pos = (getattr(n, "line", 0), getattr(n, "col", 0))
        if pos in self._map_silent_warned:
            return
        self._map_silent_warned.add(pos)
        _recv = n.children[0] if n.children else None
        _name = (_recv.name if _recv is not None
                 and getattr(_recv, "kind", "") == "path" else "")
        self._warn(
            "W-Map-SilentInsert",
            f"`{_name or 'm'}[k]` 是按 C++ 的 `map::operator[]` 语义求值的："
            f"**键不存在时会先插入一个默认值**（`u64`→0、容器→空、`struct`→零值），"
            f"再把这个默认值交给你。这正是 C++ 的经典陷阱——"
            f"**只想读，却把表改大了**（`size()` 变了，之后 `keys()` / 遍历多出一项）。"
            f"只读请用 `{_name or 'm'}.get(k)`（缺键直接报错，可先 `{_name or 'm'}.contains(k)` 判断）；"
            f"确要插入就写 `{_name or 'm'}[k] = v` 或 `{_name or 'm'}.insert(k, v)`"
            f"（`insert` 不覆盖已有键）。这是 warning，不阻断构建。",
            n)

    def _match(self, st: Node, sc: Scope, ret: Ty, fallible: bool) -> None:
        scrut = self._expr(st.children[0], sc) if st.children else TY_UNKNOWN
        # **`match` 的判别式是值位置**（2026-11-13）：`match (g())` 里 void
        # 以前一声不响，运行期拿 `None` 去比变体（永远落到 `_` 臂）。
        if _is_void_ty(scrut):
            self._err_void_value("`match` 的判别式", st)
        arms = [c for c in st.children[1:] if c.kind == "arm"]
        covered: Set[str] = set()
        has_wild = False
        for arm in arms:
            pat = next((c for c in arm.children
                        if c.kind in ("pattern", "pattern-wildcard")), None)
            if pat is not None and pat.kind == "pattern-wildcard":
                has_wild = True
            elif pat is not None and pat.name:
                parts = pat.name.split(".")
                if len(parts) == 2:
                    ty_name, variant = parts
                    covered.add(variant)
                    if (ty_name in self.enums and ty_name not in self.open_errors
                            and variant not in self.enums[ty_name]):
                        self._err("E-Variant-Unknown",
                                  self._novariant(ty_name, variant), pat)
            inner = Scope(sc)
            # `Token::Number(n)` —— 绑定名是 **pattern 的子节点**，不是 arm 的直接子节点。
            # 不在这里声明，`return n` 就会报 `E-Name-Undefined`（速查里最常用的
            # enum + match 写法都过不了）。
            if pat is not None:
                for b in pat.children:
                    if b.kind == "binding":
                        # 载荷类型未跟踪（E12 的一部分），先给 UNKNOWN——它对任何类型都兼容
                        self._decl(inner, Sym(b.name or "", "var", TY_UNKNOWN, b))
            if arm is not None:
                for b in arm.children:
                    if b.kind == "binding":
                        self._decl(inner, Sym(b.name or "", "var", TY_UNKNOWN, b))
                    elif b.kind == "block":
                        self._block(b, inner, ret, fallible)
                    elif b.kind not in ("pattern", "pattern-wildcard"):
                        self._stmt(b, inner, ret, fallible)

        # 穷尽性：scrutinee 是已知枚举，且没有 `_`
        if (not has_wild and scrut.kind == "name"
                and scrut.name in self.enums):
            missing = self.enums[scrut.name] - covered
            if missing:
                self._err("E-Match-NonExhaustive",
                          f"match 未穷尽 {scrut.name}：缺 {sorted(missing)}，也没有 `_` 臂",
                          st)

    # ---------- 表达式
    # ---------- 结构体 / 元组：它们是**聚合值**，不是标量
    #
    # 以前这一层对 struct 只记名字、对字段一律返回 UNKNOWN，于是
    # `a + b`、`a < b`、`sort(ps)`、`format("{}", p)`、`p.sum()` 全部
    # 静态放行，运行期落到宿主 `TypeError` / `InterpError`。这些判据都在
    # 类型里现成，没有理由留到运行期——本语言**没有运算符重载**，
    # struct 更没有成员函数，写法本身就是静态可判的"不存在"。
    def _deref_ty(self, t: Ty) -> Ty:
        while t.kind == "ref" and t.args:
            t = t.args[0]
        return t

    # ---------- 指针：`*p`（解引用）/ 空指针
    def _deref_expr(self, n: Node, sc: Scope) -> Ty:
        """`*p`：**按操作数类型判定**。

        | 操作数类型 | 结论 |
        |---|---|
        | `T*` / `const T*` | ✅ 结果是 `T`（成本 O(1)，见 `文档/能力边界.md`） |
        | `nullptr` 已证 / `p` 确定为空 | ❌ `E-Pointer-NullDeref` |
        | 非指针（`u64` / 容器 / struct / `T&`） | ❌ `E-Unary-Unsupported`（原来的诊断） |
        | 推不出（`?`） | 不报（避免级联） |

        **与乘法的分工**：`a * b` 走 `parse_expr` 的二元分支，产出 `binary`，
        永远到不了这里；只有**前缀** `*` 才产出 `deref`。所以"按类型判定"不会
        把乘法吞成解引用——它只决定前缀 `*` 是"合法解引用"还是"明确拒绝"。
        """
        operand = n.children[0] if n.children else None
        t = self._expr(operand, sc) if operand is not None else TY_UNKNOWN
        t = _unref(t)
        if t.kind == "ptr":
            # **③ 的空指针静态检查**：能证出来的就当场报，不留到运行期。
            if (operand is not None and operand.kind == "path"
                    and self._nulls.get(operand.name or "") is True):
                self._err(
                    "E-Pointer-NullDeref",
                    f"`*{operand.name}` 解引用一个**确定为 nullptr** 的指针"
                    f"（`{operand.name}` 上一次赋值是 `nullptr`）：运行期这里是"
                    f"空指针解引用。\n"
                    f"改法：先判空 `if ({operand.name} != nullptr) {{ … }}`，"
                    f"或把指针赋成 `&x` 之后再用。", n)
            return t.args[0] if t.args else TY_UNKNOWN
        if t.kind == "unknown":
            return TY_UNKNOWN
        if t.kind == "addrof":
            # `*&x` 就是 x（C++ 同义）；接受，不额外要求。
            return t.args[0] if t.args else TY_UNKNOWN
        self._err(
            "E-Unary-Unsupported",
            f"`*` 是**解引用**，操作数必须是指针（`T*`）；这里操作数是 "
            f"{t.render()}，不是指针。\n"
            f"要相乘写 `a * b`（`*` 在两个操作数中间）；要取元素写下标 "
            f"（`xs.at(i)`）或用 `const T&` 形参。", n)
        return TY_UNKNOWN

    def _null_guard(self, cond: Optional[Node]):
        """`if (p)` / `if (p != nullptr)` / `if (p == nullptr)` → `(名字, 体内是否非空)`。

        只认这几种**直接**形状（`and` / `or` 组合不猜）：
          · `p` / `p != nullptr` / `nullptr != p` → 体内 `p` 非空；
          · `p == nullptr` / `nullptr == p` → 体内 `p` 为空（`*p` 必须报）。
        """
        if cond is None:
            return None
        if cond.kind == "path":
            return (cond.name or None, True)
        if cond.kind == "unary" and (cond.value or "") == "!":
            return None                      # `if (!p)` → 体内 p 为空，不当守卫
        if cond.kind == "binary" and (cond.value or "") in ("!=", "=="):
            a, b = (cond.children + [None, None])[:2]
            if a is None or b is None:
                return None
            name = None
            if a.kind == "path" and b.kind == "lit-null":
                name = a.name or None
            elif b.kind == "path" and a.kind == "lit-null":
                name = b.name or None
            if name is None:
                return None
            return (name, (cond.value or "") == "!=")
        return None

    def _cond_mentions_ptr(self, cond: Optional[Node], sc: Scope) -> bool:
        """循环条件里有没有**指针相关**的东西（指针变量 / 解引用 / 指针字段）。"""
        if cond is None:
            return False
        if cond.kind == "deref":
            return True
        if cond.kind == "path":
            s = sc.lookup(cond.name or "")
            return bool(s is not None and _unref(s.ty).kind == "ptr")
        return any(self._cond_mentions_ptr(c, sc) for c in (cond.children or []))

    def _cond_is_size_bound(self, cond: Optional[Node]) -> bool:
        """条件是不是**尺寸比较**（`i < n` / `i < xs.length()`）——即"上界来自尺寸量"。

        与 `checker._cond_can_bound` **同一口径的窄版**：只认比较的两侧是
        `.length()` 调用、标量/尺寸名或整数字面量；`p != nullptr` 不算
        （指针自己给不出上界）。不认的地方不报错——交给下面的兜底。
        """
        if cond is None:
            return False
        if cond.kind == "binary":
            op = cond.value or ""
            if op in ("&&", "||"):
                return any(self._cond_is_size_bound(c) for c in cond.children)
            if op in ("<", "<=", ">", ">="):
                return all(self._is_size_side(c) for c in (cond.children or []))
        return False

    def _is_size_side(self, n: Optional[Node]) -> bool:
        if n is None:
            return False
        if n.kind in ("lit-int", "lit-float"):
            return True
        if n.kind == "method-call" and (n.name or "") == "length":
            return True
        if n.kind == "call" and (n.name or "").split(".")[-1] in ("len", "size"):
            return True
        if n.kind == "path":
            return True          # 标量/尺寸名：`i < n`（形参 n 就是声明尺寸量）
        if n.kind == "paren":
            return self._is_size_side(n.children[0] if n.children else None)
        if n.kind == "binary" and (n.value or "") in ("+", "-", "*", "/", "%"):
            return all(self._is_size_side(c) for c in (n.children or []))
        return False

    def _struct_fields(self, name: str) -> Dict[str, Ty]:
        decl = self.types.get(name)
        out: Dict[str, Ty] = {}
        if decl is not None and decl.kind == "struct":
            for f in decl.children:
                if f.kind == "field" and f.children:
                    out[f.name] = ty_of_node(f.children[0])
        return out

    def _is_struct_ty(self, t: Ty) -> bool:
        t = self._deref_ty(t)
        return (t.kind == "name" and t.name in self.types
                and self.types[t.name].kind == "struct")

    def _is_aggregate(self, t: Ty) -> bool:
        """struct 或元组：两种都**没有**运算符、也没有字段以外的东西。"""
        t = self._deref_ty(t)
        return t.kind == "tuple" or self._is_struct_ty(t)

    def _aggregate_desc(self, t: Ty) -> str:
        t = self._deref_ty(t)
        if t.kind == "tuple":
            return f"元组 {t.render()}"
        return f"struct {t.name}"

    def _aggregate_fields_hint(self, t: Ty) -> str:
        t = self._deref_ty(t)
        if t.kind == "tuple":
            return "位置字段 " + "、".join("." + str(i) for i in range(len(t.args)))
        fs = list(self._struct_fields(t.name))
        return "字段 " + ("、".join(fs) if fs else "（无）")

    # ---------- 包装值当标量用（K5：`x == 9` 曾静默为 false）
    def _err_scalar_mismatch(self, site: str, name: str, want: Ty, got: Ty,
                             node: Node) -> None:
        """赋值 / 初始化：右边是 `Option` / `Result` / 不透明值 → `E-Type-OptionToScalar`。

        K5 之前这里根本不报：`ret="any"` 让 `vec_pop(h)` 的类型是 `UNKNOWN`，
        而 `UNKNOWN` 与任何类型兼容。现在内建返回类型是真的，`Option<u64>` 与
        `u64` 天然不兼容；这里额外把**解包写法**写进消息（只说"不符"帮不到作者）。
        """
        if _needs_unwrap(got) and not _needs_unwrap(want):
            if site == "decl":
                head = (f"`{name}` 声明为 {want.render()}，但初值 "
                        f"{got.render()} 是包装值/不透明值")
            else:
                head = (f"赋值类型不符：左边（`{name}`）是 {want.render()}，"
                        f"右边 {got.render()} 是包装值/不透明值")
            self._err("E-Type-OptionToScalar",
                      f"{head}——不能直接当 {want.render()} 用。{_unwrap_hint(got)}",
                      node)
            return
        if site == "decl":
            self._err("E-Type-Assign",
                      f"`{name}` 声明为 {want.render()}，初值是 {got.render()}", node)
        else:
            self._err("E-Type-Assign",
                      f"赋值类型不符：左边 {want.render()}，右边 {got.render()}", node)

    def _err_void_value(self, site: str, node: Node, note: str = "") -> None:
        """`void` 出现在**需要值**的位置 ⇒ `E-Type-Void`（2026-11-13）。

        ## 判据（只有一条）

        > **`void` 只能作表达式语句**（返回值被丢弃）。别处一律拒绝。

        依据是 `void` 的语义：它表示"这个表达式没有值"，不是"值是空"。
        把它放进声明 / 实参 / 运算 / 下标 / 条件 / 方法接收者里，下游只能
        各自兜底，于是同一个 `.eln` 三个命令三种结论：

        | 写法 | 改前 check | 改前 run | 改前 `dsh cpp` |
        |---|---|---|---|
        | `print(xs.assign(2, 0));` | ✓ 放行 | ✓ 打 `None` | ✗ `E-Cxx-Unsupported` |
        | `print(g());`（`g` 返回 `()`） | ✓ 放行 | ✓ 打 `None` | ✓ 出码，**g++ 编不过** |
        | `xs[xs.assign(2, 0)]` | ✓ 放行 | ✗ `E-Index-Type` | ✗ |
        | `xs.assign(2, 0).size()` | ✓ 放行 | ✗ `E-Type-NotContainer` | ✗ |
        | `if (g() == 0)` | ✓ 放行 | ✓ 走 false 分支 | ✓ 出码，**g++ 编不过** |

        `run` / `build` / `dsh cpp` 都先跑 `check`，所以这一条报出来之后
        三方结论自动一致（`run` 打「先修完再 run/build」）。

        ## 不误伤的形状

        | 写法 | 为什么仍然合法 |
        |---|---|
        | `xs.assign(2, 0);` / `f();`（`f` 返回 `()`） | 表达式语句：返回值本来就丢弃 |
        | `() f() { return g(); }` | 返回类型就是 `()`，`return` 不消费值（C++ 也合法） |
        | `for_each(xs, on)`（`on` 返回 `()`） | 回调是**具名函数**（类型 `fn(…)->void`），不是 void 值 |
        | `vector<u64> ys = xs.assign(2, 0);` | 已有更具体的诊断 `E-Type-Assign`「初值是 void」，**不放宽也不改码** |
        """
        _msg = (f"{site}需要**值**，但这里是 void（没有值）。\n"
                f"`void` 只能作**表达式语句**：`xs.assign(2, 0);` / `f();` "
                f"（返回值被丢弃，合法）。\n"
                f"要拿到结果，就在**语句**里改/读同一个变量，再把变量放这里；"
                f"要丢返回值就单独写一行。")
        if note:
            _msg += "\n" + note
        self._err("E-Type-Void", _msg, node)

    def _err_wrapped_operand(self, op: str, a: Ty, b: Ty, n: Node) -> bool:
        """`+` / `&&` / `!` … 的操作数是包装值 → 静态拒绝（别落到宿主异常/错值）。

        返回是否报了错——调用方据此退回 `UNKNOWN`，免得再级联一条误导性的
        `E-Type-Assign`（根因已经说清楚了）。
        """
        for t in (a, b):
            if not _needs_unwrap(t):
                continue
            self._err("E-Type-OptionToScalar",
                      f"`{op}` 的操作数是 {t.render()}（包装值/不透明值），不是标量："
                      f"本语言没有运算符重载。{_unwrap_hint(t)}", n)
            return True
        return False

    def _err_compare_wrapped(self, op: str, a: Ty, b: Ty, n: Node) -> None:
        """**比较两边必须是同一类值**（K5 的"静默不等于"就出在这里）。

        `Option` / `Result` / 不透明值与标量比较时，运行期**不会报错**：
        `EnumVal.__eq__` 直接返回 `False`（`Option.Some(9) == 9` → `false`），
        `ReaderVal` 之类按身份比。所以必须在 `check` 就挡。
        """
        ha = _wrapped_head(a) or _opaque_head(a)
        hb = _wrapped_head(b) or _opaque_head(b)
        if not ha and not hb:
            return
        if ha and hb and ha == hb:
            return                       # 同类包装值之间比：语义有定义
        bad, other = (a, b) if ha else (b, a)
        if op in ("==", "!="):
            _tail = ("运行期不会报错，会**安静地给 false**（错值，不是错类型）")
        else:
            _tail = "运行期会落到宿主 `TypeError`（比较不支持这两个值）"
        self._err(
            "E-Type-OptionToScalar",
            f"`{op}` 不能把 {bad.render()} 与 {other.render()} 直接比较："
            f"{_tail}。{_unwrap_hint(bad)}",
            n)

    def _err_aggregate_op(self, op: str, a: Ty, b: Ty, n: Node) -> None:
        """`+` / `<` / … 操作数是 struct / 元组 → `E-Type-StructOp`。"""
        for t in (a, b):
            if not self._is_aggregate(t):
                continue
            desc = self._aggregate_desc(t)
            hint = self._aggregate_fields_hint(t)
            if op in ("<", "<=", ">", ">="):
                msg = (f"`{op}` 不能比较 {desc}：本语言没有运算符重载，"
                       f"struct / 元组没有顺序。它的{hint}——要比就比字段"
                       f"（`a.x {op} b.x`）；要排序用 `sort_by(xs, by_x)` + "
                       f"**具名比较器**（成本 `O(n log n × κ(cmp))` 才写得出来）。")
            else:
                msg = (f"`{op}` 不能用于 {desc}：本语言没有运算符重载，"
                       f"struct / 元组不是标量。它的{hint}——要用字段逐个算"
                       f"（`Point {{ x: a.x {op} b.x, y: a.y {op} b.y }}`），"
                       f"或写具名自由函数（`func add(Point a, Point b) -> Point`）再调用。")
            self._err("E-Type-StructOp", msg, n)
            return

    def _check_sort_element(self, recv: Ty, n: Node) -> None:
        """`sort(xs)`：元素必须是**可比较的标量**（`<` 成立）。"""
        recv = self._deref_ty(recv)
        if not (recv.kind == "name" and canon(recv.name) in
                ("vector", "array", "fixedvec") and recv.args):
            return
        elem = self._deref_ty(recv.args[0])
        if not self._is_aggregate(elem):
            return
        desc = self._aggregate_desc(elem)
        self._err(
            "E-Type-StructOp",
            f"`sort(...)` 要比较容器里的元素，但元素是 {desc}："
            f"本语言没有运算符重载，struct / 元组没有 `<`。它的"
            f"{self._aggregate_fields_hint(elem)}——要排序用 "
            f"`sort_by(xs, by_x)` + 具名比较器 "
            f"`func by_x({elem.render()} a, {elem.render()} b) -> bool`"
            f"（成本 `O(n log n × κ(cmp))` 才写得出来）。",
            n)

    def _check_format_aggregate(self, name: str, args: List[Ty],
                                args_node: Optional[Node], n: Node) -> None:
        """`format` / `print("…{}…", …)` 的洞不能塞 struct / 元组。

        `_text_of` 只给 list / 标量渲染，`stdlib.format` 对 struct 会
        `int(StructVal)` → 宿主 `TypeError`。`print(p)`（不插值）仍可用——
        它走 `repr`，是调试输出；这里只拦**格式化**那条路。
        """
        if not args:
            return
        if name == "print":
            items = args_node.children if args_node is not None else []
            if not items or getattr(items[0], "kind", "") != "lit-string":
                return
            txt = items[0].value
            if isinstance(txt, bytes):
                txt = txt.decode("utf-8", "replace")
            if "{" not in txt:
                return
        for t in args[1:]:
            if not self._is_aggregate(t):
                continue
            desc = self._aggregate_desc(t)
            self._err(
                "E-Format-ArgType",
                f"`{name}` 的格式化实参不能是 {desc}：`{{}}` 只渲染数字 / 文本，"
                f"struct / 元组没有标量形态。它的"
                f"{self._aggregate_fields_hint(t)}——要字符串就逐个字段格式化"
                f"（`format(\"p=({{}}, {{}})\", p.x, p.y)`），"
                f"或写具名函数把它转成 `vector<u8>`；"
                f"只想调试可以直接 `print(p)`（按 `repr` 打印，不插值）。",
                n)
            return

    def _err_struct_method(self, recv: Ty, name: str, n: Node) -> None:
        recv = self._deref_ty(recv)
        if recv.kind == "tuple":
            self._err(
                "E-Type-StructMethod",
                f"元组 {recv.render()} 没有方法 `{name}`：本语言只有内建容器有方法。"
                f"位置字段写 `.0` / `.1`；逻辑写成自由函数并显式传参。",
                n)
            return
        fields = self._struct_fields(recv.name)
        hint = "、".join(fields) if fields else "（无）"
        if name in fields and fields[name].kind == "fn":
            self._err(
                "E-Type-FuncValueField",
                f"struct {recv.name} 的 `{name}` 是**函数类型字段**，不是方法："
                f"本语言的函数值是具名的，不存进 struct 字段，也不能这样调用。"
                f"直接调用函数本身（`inc(x)`），或把它交给 `map` / `filter` / "
                f"`fold` / `sort_by` / `for_each` 这些内建消费者。",
                n)
            return
        if name in fields:
            self._err(
                "E-Type-StructMethod",
                f"struct {recv.name} 的 `{name}` 是**字段**，不是方法："
                f"读它写 `p.{name}`。本语言的 struct 只有字段（{hint}），"
                f"没有成员函数。",
                n)
            return
        # **`--compat` 的成员函数**（第 13 条）：只在接收者是"本文件里类型的
        # 具名变量"时才降级成 `R_push(r, …)`；走到这里说明接收者不是那种形状
        # （函数返回值 / `auto` / 跨 `.tu` 的 struct …）。此时按本语言原语义
        # 拒绝是对的——但要把"为什么没降级、该怎么写"说清楚。
        try:
            from compat import enabled as _compat_on
            _compat_hint = _compat_on()
        except Exception:                                   # noqa: BLE001
            _compat_hint = False
        extra = ""
        if _compat_hint:
            extra = (
                f"\n（`--compat` 下的成员函数会降级成 "
                f"`{recv.name}_{name}(接收者, …)`，但只对**本文件里类型的具名变量**"
                f"生效；这里的接收者不是那种形状，所以没有降级。改法：先把接收者"
                f"存进变量 `{recv.name} tmp = …;` 再 `tmp.{name}(…)`，"
                f"或直接写 `{recv.name}_{name}(…)`。）")
        self._err(
            "E-Type-StructMethod",
            f"struct {recv.name} 没有方法 `{name}`：本语言的 struct 只有字段"
            f"（{hint}），没有成员函数。把逻辑写成自由函数并显式传参："
            f"`func point_{name}(const {recv.name}& p) -> u64 {{ … }}`，"
            f"然后 `point_{name}(p)`。" + extra,
            n)

    def _pow_ty(self, n: Node, a: Ty, b: Ty) -> Ty:
        """`a ** b` 的类型（2026-11-10）。

        规则（**两条，不重叠**）：

        · **任一边是浮点** → 浮点幂（运行期 `powf` / `math.pow`，IEEE 语义：
          负底数配非整数指数给 `nan`、`0.0 ** -1` 给 `inf`，不报错、不崩）。
          结果类型取浮点那一边。
        · **两边都是整数** → **指数必须是非负整数字面量**，结果随左操作数。
          为什么不用运行期值：`2 ** 64` 在 `u64` 里回绕成 `0`，而"算错了"和
          "按 u64 回绕"在运行期值上分不开——与其猜，不如要求你把意图写下来
          （字面量指数，或 `2.0 ** n` 走浮点）。这条**不是**性能保守，是
          语义保守：不给"静默回绕"留通道。
        """
        if _is_float(a) or _is_float(b):
            return a if _is_float(a) else b
        state, val = _pow_exponent_literal(n.children[1] if len(n.children) > 1 else None)
        if state == "negative":
            self._err("E-Type-Pow-NegativeExponent",
                      "整数 `**` 不能有负指数（`2 ** -1` = 0.5，不是整数）："
                      "写浮点 `2.0 ** -1`，或改成 `1 / 2` 的写法", n)
            return TY_UNKNOWN
        if state != "ok":
            self._err("E-Type-Pow-ExponentLiteral",
                      "整数 `**` 的指数必须是**非负的字面量常量**"
                      "（`2 ** 10`、`2 ** 3 ** 2` 可以，`2 ** n` 不行）："
                      "运行期指数在 `u64` 里说不清溢出语义（`2 ** 64` 回绕成 0）。"
                      "要么把指数写成字面量，要么改用浮点 `2.0 ** n`（IEEE，不溢出）", n)
            return TY_UNKNOWN
        return a if a.kind != "unknown" else (b if b.kind != "unknown" else TY_UNKNOWN)

    def _cond_ok(self, ct: Ty) -> bool:
        """**条件类型判据**——`if` / `while` / 三目 `?:` 共用的**唯一**一份。

        以前这条判断在 `if` 与 `while` 里各写了一遍（复制粘贴），三目再加一份
        就是第三份；同一个语义三处代码，改一处忘两处只是时间问题（见
        `kinds.py` 文件头记的"两条链漂移过四次"）。所以抽成判据，消息仍由
        各调用点自己给（三处措辞本就不同，读者要知道是哪一种条件）。

        三条：
          ① 指针可以（C++ 同义：非空为真）；
          ② `bool` 可以；
          ③ **`--compat` 下整数可以**（2026-11-12）：C++ 里 `if (x)` /
             `if (m & 1)` 的语义就是"非零为真"，这是一条**无歧义的转换**，
             与"隐式转换"里那些靠猜的情形不是一回事。`compat=False` 时照旧拒绝。
        """
        _int_cond = ct.render().strip() in (
            "u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64")
        return (ct.kind == "ptr" or compatible(ct, TY_BOOL)
                or (_compat.enabled() and _int_cond))

    def _expr(self, n: Optional[Node], sc: Scope) -> Ty:
        if n is None:
            return TY_UNKNOWN
        k = n.kind

        if k == "lit-float":
            # **浮点字面量默认 `f64`**（C++ 里 `1.5` 是 `double`）。
            # 以前没有这个分支，`1.0 / 3.0` 被当整数除 → `Result<u64, Arithmetic>`。
            return TY_F64
        if k == "lit-int":
            return TY_I64
        if k == "list-init":
            # 大括号初始化本身没有"单一类型"：元素逐个由
            # `_check_list_init_elems` 与声明的 T 比较，这里返回 UNKNOWN
            # 以免外层 `compatible(vt, ty)` 再报一次重复的错误。
            return TY_UNKNOWN
        if k == "lit-string":
            return Ty("name", "vector", [Ty("name", "u8")])

        if k == "lit-char":
            return TY_U64
        if k == "lit-bool":
            return TY_BOOL
        if k == "lit-null":
            # `nullptr`：只与指针兼容（见 `compatible`）。它不是 `u64 0`——
            # 把空指针当整数会让 `u64 x = nullptr;` 静默通过。
            return TY_NULL
        if k == "paren":
            return self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
        if k == "cast-static":
            # **第 11 条**（P0 兼容层）：`static_cast<unsigned char|u64>(x)`。
            #
            # 折叠的判据只有一条：**转换后值与转换前逐位相同**（见
            # `_static_cast_fold_ok`）。能折叠时这个节点在 `parse_source` /
            # `resolve_static_cast` 里**已经被换成 `x` 本身**，正常情况下
            # 走不到这个分支；走得到说明折叠没做成（类型推不出 / 窄化），
            # 那就**报错**——绝不在这里"顺手"当恒等。
            _src = self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
            _why = _static_cast_fold_ok(n.name or "", _src)
            if _why is None:
                return _unref(_src)
            _tgt = _CAST_TARGET_SPELLING.get(n.name or "", n.name or "?")
            self._err(
                "E-Cast-Unsupported",
                f"`static_cast<{_tgt}>(x)` 不能折成 `x`：{_why}。\n"
                f"改法：真要做转换就写 `x as {n.name or 'T'}`（那是有定义的"
                f"转换，不是拼写）；值本来就不该变，就把整段去掉写成 `x`。", n)
            return TY_UNKNOWN
        if k == "tuple":
            return Ty("tuple", args=[self._expr(c, sc) for c in n.children])
        if k == "struct-literal":
            ty_name = n.name or ""
            decl = self.types.get(ty_name)
            want: Dict[str, Ty] = {}
            if decl is not None and decl.kind == "struct":
                for f in decl.children:
                    if f.kind == "field" and f.children:
                        want[f.name] = ty_of_node(f.children[0])
            got = set()
            for fi in n.children:
                if fi.kind != "field-init" or not fi.children:
                    continue
                got.add(fi.name)
                vt = self._expr(fi.children[0], sc)
                if fi.name in want and not compatible(vt, want[fi.name]):
                    self._err("E-Type-FieldMismatch",
                              f"{ty_name}.{fi.name} 是 {want[fi.name].render()}，"
                              f"给的是 {vt.render()}", fi)
                elif want and fi.name not in want:
                    self._err("E-Type-NoField",
                              f"{ty_name} 没有字段 {fi.name}"
                              f"（有：{', '.join(sorted(want))}）", fi)
            missing = set(want) - got
            if missing:
                self._err("E-Type-MissingField",
                          f"{ty_name} 缺字段 {sorted(missing)}", n)
            return Ty("name", ty_name)

        if k == "path":
            name = n.name or ""
            if "." in name:
                # 与 `call` 分支同一个判据：`::` 左边必须是已知类型 /
                # 已登记命名空间（`Color::Red`、`Token::Plus`、`std::…` 照旧）。
                if self._check_qualified_head(name, n):
                    return TY_UNKNOWN
                ty_name, variant = name.rsplit(".", 1)
                if (ty_name in self.enums and ty_name not in self.open_errors
                        and variant not in self.enums[ty_name]):
                    self._err("E-Variant-Unknown",
                              self._novariant(ty_name, variant), n)
                return self.enum_payload.get((ty_name, variant),
                                             Ty("name", ty_name))
            s = sc.lookup(name)
            if s is None:
                # **弃用内建**：名字还认（老代码能跑），但给出改写指引
                try:
                    import stdlib as _std
                    if _std.is_deprecated(name):
                        self._warn(
                            "W-Builtin-Deprecated",
                            f"`{name}` 已弃用（v0.73 移除）：改用 "
                            + _std.replacement_for(name), n)
                        return TY_UNKNOWN
                except Exception:                       # noqa: BLE001
                    pass
                self._err("E-Name-Undefined", f"未声明的名字 `{name}`", n)
                return TY_UNKNOWN
            return s.ty

        if k == "deref":
            # **`*x` 的判据是类型，不是形状**：操作数是指针 → 解引用；
            # 否则仍然报原来的 `E-Unary-Unsupported`（乘法 `a * b` 在
            # `parse_expr` 的二元分支，根本不会产出 `deref` 节点）。
            return self._deref_expr(n, sc)

        if k == "unary":
            t = self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
            # **`void` 不能当操作数**（2026-11-13）：`!g()` / `-g()`（`g` 返回
            # `()`）以前 check 绿、`run` 也绿，发射出来 `!g()` 才被 g++ 拒绝
            # （`invalid use of void expression`）。一元运算要的是**值**。
            if _is_void_ty(t):
                self._err_void_value(f"一元 `{n.value or '?'}` 的操作数", n)
                return TY_UNKNOWN
            if (n.value or "") == "~":
                if t.kind != "unknown" and not _is_int_ty(t):
                    self._err("E-Type-Bitwise",
                              f"`~` 只接受整数，收到 {t.render()}", n)
                return t
            if (n.value or "") == "-" and self._is_aggregate(t):
                self._err(
                    "E-Type-StructOp",
                    f"`-` 不能用于 {self._aggregate_desc(t)}：本语言没有运算符重载，"
                    f"struct / 元组不是标量。它的{self._aggregate_fields_hint(t)}——"
                    f"要对每个字段取负（`Point {{ x: -p.x, y: -p.y }}`），"
                    f"或写具名自由函数。", n)
                return t
            # `-` / `+` / `!` 同样不接受包装值（K5）：`-vec_pop(h)` 运行期
            # 落到 `TypeError`，`!vec_pop(h)` 会把 `Option` 的真值当 bool。
            if (n.value or "") in ("-", "+", "!"):
                self._err_wrapped_operand(n.value or "", t, TY_UNKNOWN, n)
            return TY_BOOL if (n.value or "") == "!" else t

        if k == "ternary":
            # **三目 `c ? a : b`**（2026-11-13，`parser.TERNARY_BP`）
            #
            # 三条判据，缺一条就会"猜"：
            #   ① 条件与 `if` **同一口径**（`_cond_ok`：bool / 指针，`--compat`
            #      下整数）——`?:` 是表达式位置的条件，规则不能两样；
            #   ② 两支必须是**值**（void 不行，同 `as` 的操作数）；
            #   ③ 两支必须**相容**（`compatible`，与赋值同一条规则）。
            #
            # 结果类型**不是两边随便挑一个**：`compatible` 是**方向性**的
            # （`T* → const T*` 行、反过来不行），所以先看哪个方向成立——
            # 只成立一个方向时，结果就是**收得下两支的那个类型**（这正是
            # C++ conditional-expression 的口径）；两个方向都成立（数值之间）
            # 时按算术口径：浮点优先，否则取左支（与 `/` `%` 同一规则）。
            cond = n.children[0] if n.children else None
            then = n.children[1] if len(n.children) > 1 else None
            els = n.children[2] if len(n.children) > 2 else None
            ct = self._expr(cond, sc) if cond is not None else TY_UNKNOWN
            if not self._cond_ok(ct):
                self._err("E-Type-Cond",
                          f"`?:` 的条件应是 bool 或指针，实际 {ct.render()}："
                          f"三目的条件与 `if` **同一条规则**（这是**值**的选择，"
                          f"不是索引）", n)
            ta = self._expr(then, sc) if then is not None else TY_UNKNOWN
            tb = self._expr(els, sc) if els is not None else TY_UNKNOWN
            if _is_void_ty(ta) or _is_void_ty(tb):
                self._err_void_value("三目 `?:` 的一支", n)
                return TY_UNKNOWN
            _ab, _ba = compatible(ta, tb), compatible(tb, ta)
            if not (_ab or _ba):
                # **不许猜**：两支落不到同一个类型上时，三目没有"哪个优先"
                # 的默认规则可依——那会让 `c ? 1 : "x"` 静默取一支、另一支
                # 永远跑不到，且类型随条件的**值**变。要求显式转换。
                self._err(
                    "E-Type-CondBranches",
                    f"`?:` 的两支类型不相容：`{ta.render()}` 与 {tb.render()}。\n"
                    f"三目是**一个值**，两支必须能落到同一个类型上（与赋值"
                    f"用的是同一条相容规则）。\n"
                    f"改法：把其中一支显式转换（`x as {tb.render()}` / "
                    f"`x as {ta.render()}`），或让两支写成同一个类型。",
                    n)
                return TY_UNKNOWN
            if ta.kind == "unknown":
                return tb
            if tb.kind == "unknown":
                return ta
            if _ab and _ba:
                if _is_float(ta) or _is_float(tb):
                    return ta if _is_float(ta) else tb
                return ta
            return tb if _ab else ta

        if k == "binary":
            op = n.value or ""
            a = self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
            b = self._expr(n.children[1], sc) if len(n.children) > 1 else TY_UNKNOWN
            # **`void` 操作数**（2026-11-13）：`g() == 0` / `g() && b`（`g` 返回
            # `()`）以前 check 绿——`==` 只比"包装值"，而布尔运算直接返回
            # `bool`、把 void 抹掉了。发射出来是 `g() == 0`，g++ 报
            # `invalid use of void expression`。**运算的两边都要值**。
            if _is_void_ty(a) or _is_void_ty(b):
                self._err_void_value(f"二元 `{op}` 的操作数", n)
                return TY_UNKNOWN
            # **包装值不是标量**（K5）：`vec_pop(h) == 9` 会静默为 false、
            # `vec_pop(h) + 1` 会落到宿主 `TypeError`。两条都在这里静态挡。
            if op in ("&&", "||"):
                self._err_wrapped_operand(op, a, b, n)
                return TY_BOOL
            # **算术 / 比较不接受 struct / 元组**（`E-Type-StructOp`）：
            # 它们运行期落到宿主 `TypeError`（`StructVal + StructVal` /
            # `'<' not supported`）。`==` / `!=` **放行**——结构化相等是
            # 语言已有的语义（`StructVal.__eq__` / 元组比较）。
            if op in ("+", "-", "*", "/", "%", "**", "<", "<=", ">", ">="):
                self._err_aggregate_op(op, a, b, n)
                # **包装值不参与算术**：`vec_pop(h) + 1` 以前落到宿主
                # `TypeError: unsupported operand type(s) for +: 'EnumVal' and 'int'`
                # （K5 实测）。比较另走 `_err_compare_wrapped`（消息不同）。
                if op not in ("<", "<=", ">", ">="):
                    if self._err_wrapped_operand(op, a, b, n):
                        return TY_UNKNOWN
            if op in ("&", "|", "^", "<<", ">>"):
                # **位运算不可失败**：不引入 `Result`（与 `+ - *` 同一口径）。
                # 浮点 / bool / 容器都没有按位语义，静态拒绝。
                for t in (a, b):
                    if t.kind != "unknown" and not _is_int_ty(t):
                        self._err("E-Type-Bitwise",
                                  f"位运算 `{op}` 只接受整数，收到 {t.render()}", n)
                        return TY_UNKNOWN
                # **移位的结果类型只看左操作数**（C/Rust 规则）：移位量只提供
                # 值。`& | ^` 仍是「两边最宽」——那是算术的正确规则，别一起改。
                if op in ("<<", ">>"):
                    return _shift_result_ty(a, b)
                return _bitwise_result_ty(a, b)
            if op in ("<", "<=", ">", ">=", "==", "!="):
                # K5 的**静默不等于**：两边类型不同时运行期不报错，`==` 给
                # `false`、`<` 落到宿主 `TypeError`——两种都不该活到运行期。
                self._err_compare_wrapped(op, a, b, n)
                return TY_BOOL
            if op in ("/", "%"):
                # **浮点除法不失败**（2026-11-05）：IEEE 754 里 `1.0/0.0 = inf`、
                # `0.0/0.0 = nan`，是**有定义的值**，不是错误。所以浮点走原类型。
                # **整数除/模同样返回普通整数**（2026-11-11）：以前返回
                # `Result<_, Arithmetic>`，于是 `(lo + hi) / 2`、`n / 2`、
                # `mask / 2` 这些算法代码里最常见的写法全被类型系统挡住
                # （AI 一次通过率实验：3 题里 2 题语义全对、却卡在这里）。
                # 现在除零是**运行期陷阱** `E-Div-ByZero`（像 Rust 的 panic），
                # 不是 `Err` 变体。结果类型与 `+`/`-`/`*` 同一规则：取左操作数，
                # 未知时退回已知的那一边（别引入一套新的位宽推断）。
                if _is_float(a) or _is_float(b):
                    return a if _is_float(a) else b
                return a if a.kind != "unknown" else b
            if op == "**":
                return self._pow_ty(n, a, b)
            return a if a.kind != "unknown" else b

        if k == "call":
            name = n.name or ""
            args = []
            for c in n.children:
                if c.kind == "args":
                    args = [self._expr(a, sc) for a in c.children
                            if a.kind != "named-arg"] + \
                           [self._expr(a.children[0], sc) for a in c.children
                            if a.kind == "named-arg" and a.children]
            # **实参里有 `void`**（2026-11-13）：`print(xs.assign(2, 0))` /
            # `print(g())`（`g` 返回 `()`）以前 check 绿、`run` 打 `None`，
            # 发射出来 `print(g())` 连 g++ 都过不了。
            #
            # 用户函数 / spec 的实参本来由 `_check_callee` 的
            # `E-Type-ArgMismatch` 挡住；但 `print` / `format` 这类变参内建、
            # 以及没有形参类型的路径一直是"收什么都行"。判据只有一条：
            # **值位置不收 void**——所以在这里统一判一次并**短路**返回
            # `UNKNOWN`（免得同一个 void 再级联出 `E-Type-Assign` 之类的
            # 第二个码，一个根因只报一条）。
            for _at in args:
                if _is_void_ty(_at):
                    self._err_void_value(
                        f"调用 `{name or '?'}` 的实参", n,
                        note=("（这条路径以前是**下游各自兜底**：`run` 对 `print` "
                              "打 `None`，`dsh cpp` 对 `assign` 这类值位置报 "
                              "`E-Cxx-Unsupported`。那些兜底**都还在**，但"
                              "**兜底不是许可**——判据只应该有 check 这一条。）"))
                    return TY_UNKNOWN
            # **struct / 元组被当成标量或可比较元素**：见用户函数分支之后的
            # `_check_sort_element` / `_check_format_aggregate`。
            if "." in name:
                # **`X::f(...)` 的 `X` 必须是已知类型**（2026-11-13）：限定名
                # 到这一层只剩「最后一段」被当成被调名，前缀从没人查过。
                # `foo::print(…)` 因此静默落到全局内建 `print`——check 零
                # 诊断、run 照样输出（假绿）。前缀未知就短路成 UNKNOWN。
                if self._check_qualified_head(name, n):
                    return TY_UNKNOWN
                ty_name, variant = name.rsplit(".", 1)
                if (ty_name in self.enums and ty_name not in self.open_errors
                        and variant not in self.enums[ty_name]):
                    self._err("E-Variant-Unknown",
                              self._novariant(ty_name, variant), n)
                return Ty("name", ty_name)
            if name in ("Ok", "Err"):
                return Ty("name", "Result", args[:1] or [TY_UNKNOWN])
            # **用户定义优先于同名内建**（批次 5 第 23 条）。
            # 以前先查 stdlib，于是 `pub func sum_of(...)` 被静默忽略：
            # 调用落到已弃用内建，还捎一条 `W-Builtin-Deprecated`。
            # 语言里"我定义的函数"比"库里的名字"更具体，按最具体的解析。
            if name in self.funcs:
                self._check_callee(name, self.funcs[name], args, n)
                return self._ret_decl(name)
            # **struct / 元组被当成标量或可比较元素**：`sort(ps)` 与
            # `format("{}", p)` 都属这一类（运行期宿主 `TypeError`）。
            # 放在用户函数分支之后：用户可以定义同名函数，那时走他的签名。
            _args_node = next((c for c in n.children if c.kind == "args"), None)
            if name == "sort" and len(args) == 1:
                self._check_sort_element(args[0], n)
            if name in ("format", "print"):
                self._check_format_aggregate(name, args, _args_node, n)
            # **算法类内建的实参个数**（表与代码依据见 `BUILTIN_ARITY`）。
            # 放在用户函数之后：`func map(...)` 已经在上面截走，不会被当成内建。
            # 用户函数自己还有 `checker._check_callee` / spec 的 `E-Call-Arity` 管。
            _want = BUILTIN_ARITY.get(name)
            if _want is not None and len(args) != _want:
                self._err("E-Type-ArgCount",
                          f"{name} 要 {_want} 个实参"
                          f"{BUILTIN_ARITY_HINT.get(name, '')}，给了 {len(args)}", n)
            # **高阶内建的第一个实参**（2026-11-12）：解释器要求它真的是
            # 容器（`map/filter/fold/count_if/sort/sort_by` 只要 list-like；
            # `for_each` 还认 map / set / HashMap）。以前 `map(42, twice)` /
            # `sort(n)` 一律 check 绿，`run` 才抛
            # 「… 的第一个实参必须是容器」。
            _hof_ok = (self._check_hof_container(name, args[0] if args else None, n)
                       if name in HOF_CALLBACK_ARITY or name in ("sort", "sort_by")
                       else None)
            # **高阶内建的回调**（2026-11-12）：解释器只接受**本模块的具名
            # 函数**，并按固定元数调用它（`map/filter/count_if` → 1、
            # `fold` → 2、`for_each` 看容器：list-like/set → 1、map → 2）。
            # 以前这里不查，于是
            #   · `fold(xs, 0, onearg)`（onearg 只有 1 个形参）→ `check` rc=0，
            #     `run` 抛「onearg 需要 1 个实参，给了 2」；
            #   · `map(xs, g)`（`g` 是 `func(u64)->u64` 形参）→ `run` 抛
            #     「map 的函数实参 g 不是本模块的函数」；
            #   · `map(xs, twice(1))`（调用表达式）→ `run` 抛「MUST 是具名函数」。
            # 三条都是「名字写着能用、跑起来才炸」，且**静态可判**。
            _cb_want = HOF_CALLBACK_ARITY.get(name)
            if name == "for_each" and args:
                # 二元回调只对 map 类成立；set / list-like 是一元。
                _ft = self._deref_ty(args[0])
                _fh = canon(_ft.name) if _ft.kind == "name" else ""
                if _fh in HOF_MAP_HEADS:
                    _cb_want = 2
                elif _fh in HOF_SET_HEADS or _fh in HOF_LIST_LIKE_HEADS:
                    _cb_want = 1
                else:
                    _cb_want = None        # 容器形状已报，不猜回调元数
            if (_cb_want is not None and len(args) == _want
                    and _hof_ok is not False and _args_node is not None):
                _cb_i = 2 if name == "fold" else 1
                _kids = [c for c in _args_node.children
                         if getattr(c, "kind", "") != "named-arg"]
                if len(_kids) > _cb_i:
                    self._check_hof_callback(name, _kids[_cb_i], _cb_want, sc)
            if name in ("push_heap", "pop_heap"):
                # 堆原语（对齐 std::push_heap / std::pop_heap）：返回 u64。
                return TY_U64
            if name == "swap":
                # 原地交换（B3）：3 个实参，返回 u64（0）。
                if len(args) != 3:
                    self._err("E-Type-ArgCount",
                              f"swap 要 3 个实参（容器, i, j），给了 {len(args)}", n)
                return TY_U64
            if name in ("sort", "sort_by"):
                # 原地排序，返回元素个数（u64）。比较器是**名字**，不是值。
                # 实参个数由 `BUILTIN_ARITY` 统一校验（sort=1、sort_by=2）。
                return TY_U64
            if name == "for_each":
                # **高阶内建**：`for_each(容器, 具名函数)`（v0.73 起支持 Vec）。
                # 以前只在解释器里有实现、名字表里没有 → 合法程序被 check 拒。
                # 实参个数由 `BUILTIN_ARITY` 统一校验。
                return TY_U64
            if stdlib.is_stdlib(name):
                # **弃用内建**：还能用，但要告诉作者该改写成什么（v0.73 移除）
                if stdlib.is_deprecated(name):
                    self._warn(
                        "W-Builtin-Deprecated",
                        f"`{name}` 已弃用（v0.73 移除）：改用 "
                        + stdlib.replacement_for(name), n)
                # 最小基本库：本批全部是 `(容器, …) -> u64`，只校验实参个数。
                # **变参内建**（`format`）的 `arity` 是下限：洞数 vs 实参数由
                # `checker._check_format_calls` 在编译期校验（`E-Format-Arity`）。
                # 以前这里一律按精确个数比，"format 要 1 个实参"把多洞写法误杀。
                if not stdlib.arity_ok(name, len(args)):
                    _want = stdlib.arity(name)
                    if stdlib.is_varargs(name):
                        self._err("E-Type-ArgCount",
                                  f"{name} 至少要 {_want} 个实参，给了 {len(args)}", n)
                    else:
                        self._err("E-Type-ArgCount",
                                  f"{name} 要 {_want} 个实参，给了 {len(args)}", n)
                    return TY_UNKNOWN
                # **高阶内建**（v0.71）：参数是具名函数，返回容器或 u64
                if name in ("map", "filter"):
                    return Ty("name", "vector", [TY_U64])
                if name in ("fold", "count_if"):
                    return TY_U64
                # **`map_put` / `map_get` / `map_has` / `map_len` 只吃
                # stdlib 的 `HashMap`**（`map_new()` 的返回类型）。语言里的
                # `map<K,V>` / `unordered_map` 在这里**静态拒绝**并给出方法式
                # 写法——否则就是"`check` 全绿、`run` 抛 TypeError"
                # （`_tier_b/structs` K2 的原始形态）。
                if name in MAP_FREE_FNS and args:
                    _a0 = _unref(args[0])
                    if _a0.kind == "name" and canon(_a0.name) in LANG_MAP_HEADS:
                        self._err(
                            "E-Type-ArgMismatch",
                            f"{name} 只接受 stdlib 的 `HashMap`"
                            f"（`map_new()` 的返回类型，O(1) 有界探测）；"
                            f"给的是语言里的 `{_a0.render()}`。语言 "
                            f"`map`/`unordered_map` 用方法式 API："
                            f"`m.insert(k, v)` / `m.get(k)` / `m.contains(k)` / "
                            f"`m.length()`（`map` 是 O(log n)，比 `map_put` 的 "
                            f"O(1) 慢——不把内建扩到它是为了不让 κ 低估）", n)
                        return TY_UNKNOWN
                _rk = stdlib.ret_kind(name)
                # **唯一消费点**：`ret` → `Ty`。旧实现是一串 if，缺了
                # `vec-u64`（`utf8_chars_vec` 被当成 `u64`）和 `any`
                # （`vec_pop` 被当成"任何类型"）。现在整张词表在
                # `_builtin_ret_ty` 里逐条落地，缺词退回不透明 `Any`。
                return _builtin_ret_ty(_rk, args)
            if name in ("map", "filter", "fold", "count_if"):
                # 这些是**解释器直接实现**的（不在 stdlib 注册表里），单独处理
                if name in ("map", "filter"):
                    return Ty("name", "vector", [TY_U64])
                return TY_U64

            if name == "print":
                if not args:
                    self._err("E-Type-ArgCount", "print 至少要 1 个实参", n)
                    return TY_UNKNOWN
                return TY_U64
            if name in ("min", "max"):
                # 内建二参整数函数：`(T, T) -> T`。成本常数、不改变阶，
                # 所以它能替掉 `if` 串两支而不动成本契约。
                if len(args) != 2:
                    # 个数错已经由 `BUILTIN_ARITY` 报过；这里只防止越界取 args。
                    return TY_UNKNOWN
                if not (compatible(args[0], args[1])):
                    self._err("E-Type-ArgMismatch",
                              f"{name} 的两个实参类型不同："
                              f"{args[0].render()} vs {args[1].render()}", n)
                return args[0] if compatible(args[0], args[1]) else TY_UNKNOWN
            if name == "":
                # `FixedVec<T,N>::make()` 这类静态构造：callee 是 field，不是名字。
                # **`array<T, N>(v)`**（P4-0）：callee 直接是 `type-name`，
                # 表达式的类型就是它自己（不是"推不出"）。
                _callee = n.children[0] if n.children else None
                if getattr(_callee, "kind", "") == "type-name":
                    return self._decl_ty(_callee)
                # **`array<T,N>::filled(v)` 是弃用别名**（P4-0）：`filled`
                # 多余——C++ 的值初始化就是 `array<T,N>(v)`。报 warning
                # 而不是 error：样例与文档还在用（与内建弃用同一口径）。
                if (getattr(_callee, "kind", "") == "field"
                        and (_callee.name or "") == "filled"
                        and _callee.children
                        and getattr(_callee.children[0], "kind", "") == "type-name"
                        and canon(_callee.children[0].name) == "array"):
                    self._warn(
                        "W-Builtin-Deprecated",
                        "`array<T, N>::filled(v)` 已弃用（v0.73 移除）："
                        "改用 `array<T, N>(v)`"
                        "（C++ 的值初始化写法；旧名仍能跑，新代码请写新名）", n)
                return TY_UNKNOWN
            s = sc.lookup(name)
            if s is None:
                self._err("E-Name-Undefined",
                          f"调用了未声明的函数 `{name}`"
                          f"（若它是外部符号，请在 `.tu` 的 `extern` 块登记，"
                          f"否则无法只数次数）", n)
                return TY_UNKNOWN
            callee = self._find_decl(name)
            if callee is not None:
                self._check_callee(name, callee, args, n)
            return self._ret_decl(name)

        if k == "method-call":
            recv = self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
            # `const Vec<u8>& text` 的类型是 ref；取元素类型要先拆引用
            while recv.kind == "ref" and recv.args:
                recv = recv.args[0]
            name = n.name or ""
            # **接收者是 `void`**（2026-11-13）：`xs.assign(2, 0).size()` /
            # `xs.push_back(1).size()` / `xs.clear().size()` 以前 check 绿，
            # `run` 报 `E-Type-NotContainer`、`dsh cpp` 报 `E-Cxx-Unsupported`，
            # 而 `xs.push_back(1).size()` 连 `dsh cpp` 都放行——生成物才被
            # g++ 拒。链式调用要求前一步**有值**：`void` 只能作语句。
            if _is_void_ty(recv):
                self._err_void_value(
                    f"方法 `.{name}()` 的接收者", n,
                    note=("（改前 `run` 报 `E-Type-NotContainer`、`dsh cpp` 报 "
                          "`E-Cxx-Unsupported`——两道兜底都还在，只是链式调用"
                          "本来就该在 check 期被挡。）"))
                return TY_UNKNOWN
            # **标量 / 枚举上调容器方法**（2026-11-13，缺口 ③）：
            # `u64 x = 5; x.size()` 以前 check 绿——下面 `size`/`length`
            # 分支不看接收者，一律返回 `u64`；`run` 才报
            # `E-Type-NotContainer`，而 `dsh cpp` **照样出码**（`x.size()`
            # 连 g++ 都过不了）。容器方法只对**容器**成立。
            #
            # 判据只认**已知的标量 / 枚举**：`unknown`（推不出）与类型参数
            # （泛型 `T`）**不猜**，保持原来的宽松——否则泛型代码会被误杀。
            _rv = recv.name if recv.kind == "name" else ""
            if _rv and (canon(_rv) in NUMERIC or canon(_rv) in FLOATS
                        or canon(_rv) == "bool" or _rv in self.enums):
                self._err(
                    "E-Type-NotContainer",
                    f"对 {recv.render()} 调方法 `.{name}()`：**标量 / 枚举没有"
                    f"方法**，只有容器有（`.size()` / `.at()` / `.push_back()` …）。"
                    f"`u64` 这类值没有「长度」或「元素」；要长度先放进容器，"
                    f"要算就写成算式。", n)
                return TY_UNKNOWN
            # **方法实参**（2026-11-13）：这里以前**完全不进实参**，于是
            # `xs.push_back(g())` / `xs.at(g())` / `m.insert(g(), 1)` 的
            # void 逐层漏到运行期（`run` 抛 `E-Index-Type` / `InterpError`）
            # 或到 g++（`push_back(g())`）。方法实参也是值位置。
            _margs = next((c for c in n.children
                           if getattr(c, "kind", "") == "args"), None)
            if _margs is not None:
                for _c in _margs.children:
                    _a = (_c.children[0] if getattr(_c, "kind", "") == "named-arg"
                          and _c.children else _c)
                    if getattr(_a, "kind", "") == "named-arg":
                        continue
                    if _is_void_ty(self._expr(_a, sc)):
                        self._err_void_value(f"方法 `.{name}()` 的实参", n)
                        return TY_UNKNOWN
            # **指针上调方法写 `->`**（`p->length()`），不是 `p.length()`。
            # `p->x()` 在 parser 里已经 desugar 成 `method-call(deref(p), …)`，
            # 走不到这里；能到这里说明写的是 `.`。
            if recv.kind == "ptr":
                self._err(
                    "E-Pointer-UseArrow",
                    f"对 {recv.render()}（指针）调方法 `{name}`：写 `p->{name}()`，"
                    f"不是 `p.{name}()`（`->` 会自动解引用）。", n)
                return TY_UNKNOWN
            # **包装值没有方法**（K5 的旁支）：`vec_pop(h).length()` 以前
            # 被当成 `u64`（`length` 一律返回 u64），运行期才炸。`Option` /
            # `Result` / 不透明值不支持任何方法，先解包。
            if _needs_unwrap(recv):
                self._err("E-Type-OptionToScalar",
                          f"对 {recv.render()} 调方法 `{name}`：它是包装值/不透明值，"
                          f"不是容器，没有方法。{_unwrap_hint(recv)}", n)
                return TY_UNKNOWN
            # **struct / 元组没有成员函数**（`E-Type-StructMethod`）：
            # 以前 `p.sum()` check 绿、run 报 `E-Type-NotContainer`。
            # 字段是函数类型时另给 `E-Type-FuncValueField`（`o.f(1)`）。
            if self._is_struct_ty(recv) or recv.kind == "tuple":
                self._err_struct_method(recv, name, n)
                return TY_UNKNOWN
            # **未知方法在 check 阶段就报**（`E-Call-UnknownMethod`）。
            # 只对**已知容器**判定：自定义类型/未知类型不猜（否则误报）。
            #
            # **旧名（P4-0）**：`push` / `length` / `is_empty` 仍然能跑，
            # 但这里报 `W-Builtin-Deprecated`（warning，不阻断构建）并教新名。
            # 判据是「新名在这一类容器上真的存在」——`stack.push` 是 C++ 的
            # 正确名字，不能报弃用。
            _recv_head = canon(recv.name) if recv.kind == "name" else ""
            if name == "c_str":
                # **C++ 的 `s.c_str()`**（2026-11-12，兼容层拼写，只在
                # `--compat` 下认）：`std::string` 在本语言里是
                # `vector<u8>`（运行时形态 `Text`），`c_str()` 就是**同一段
                # 文本**（C++ 返回 `const char*`，本语言不加指针包装）。
                # `compat=False` 时照旧报 `E-Call-UnknownMethod`——默认路径
                # 一个字节都不动。
                if _compat.enabled() and _recv_head == "vector":
                    return recv
            if _recv_head in METHOD_TABLE:
                table = METHOD_TABLE[_recv_head]
                if name not in table:
                    _new = DEPRECATED_METHODS.get(name)
                    if _new is not None and (_new in table or
                                             (name == "push"
                                              and _recv_head in _PUSH_DEPRECATED_HEADS)):
                        self._warn(
                            "W-Builtin-Deprecated",
                            f"`{name}` 已弃用（v0.73 移除）：改用 `.{_new}()`"
                            f"（C++ 的名字；旧名仍能跑，但新代码请写新名）", n)
                    else:
                        self._err(
                            "E-Call-UnknownMethod",
                            f"`{recv.name}` 没有方法 `{name}`。"
                            f"支持：{'、'.join(sorted(table))}"
                            + (f"（旧名 `{name}` 不再接受）"
                               if _new is not None else ""),
                            n)
            if name in ("size", "length"):
                return TY_U64
            if name in ("empty", "is_empty"):
                return TY_BOOL
            if name in ("at", "get"):
                # `at(i)` / `map.get(k)` 都返回**元素**（`map<K,V>` 是 `V`）。
                return _elem_ty_of(recv)
            if name in ("push_back", "push", "enqueue"):
                # **`stack.push` / `queue.push` / `enqueue` 返回 u64(0)**：
                # `interp.py` 的 `StackVal` / `QueueVal` 分支写死 `_u64(0)`
                # （以前这里一律按 `void`，`u64 n = s.push(1);` 会被误拒）。
                # 序列容器的 `push_back` 在 interp 里返回**接收者本身**
                # （实现细节），但 C++/发射器都按 `void` 处理，所以保持 void。
                if _recv_head in ("stack", "queue"):
                    return TY_U64
                return TY_VOID
            if name == "assign":
                # **`v.assign(n, x)` 返回 `void`**（2026-11-13，按 C++ 语义）。
                #
                # 上一轮这里按 `interp.py` 当时的 `return recv` 声明成**接收者
                # 类型**（`vector<T>`），于是 `vector<u64> ys = xs.assign(2, 0);`
                # 过了 check——但 `std::vector::assign` 返回 void，发射器直译
                # 出的 C++ 编不过（`conversion from 'void' to non-scalar type`）。
                # 这就是"check 绿、`dsh cpp` 红"的三方不一致。
                #
                # 本轮的裁决：**统一成 void**（`assign` 是"清空后填 n 个 x"的
                # 写操作，本来就不产出值）：
                #   · `interp.py` 的 `assign` 分支 `return None`；
                #   · 这里 `TY_VOID` ⇒ 值位置（`T ys = xs.assign(...)` / `return
                #     xs.assign(...)` / 当实参）报 `E-Type-Assign`「初值是 void」；
                #   · 语句位置 `xs.assign(n, x);` 丢弃返回值，照旧通过；
                #   · `dsh.py` 在值位置报 `E-Cxx-Unsupported`（绝不发射编不过的
                #     表达式）。三方口径一致。
                #
                # 只有 `vector` 有 `assign`（`METHOD_TABLE`）；`array` / `fixedvec`
                # 定长、改不了长度，在上面就报 `E-Call-UnknownMethod`。
                return TY_VOID
            if name == "pop":
                return _elem_ty_of(recv)
            if name in ("top", "peek", "dequeue"):
                # 只有 stack/queue 有这三个（见 `METHOD_TABLE`）；都是**元素**。
                if _recv_head in ("stack", "queue"):
                    return _elem_ty_of(recv)
                return TY_UNKNOWN
            if name == "find":
                # **返回下标**（u64）；找不到返回 `npos`（u64 最大值）。
                return TY_U64
            if name == "insert":
                # **返回类型以 `interp.py` 的 `_method` 为准**（2026-11-13，
                # 第 5 条不一致：以前 `map.insert` / `put` 声明成 bool，
                # 而 interp 返回 `_u64(...)`——`bool b = m.insert(k, v);`
                # 于是 check 绿、值却在两边不同：解释器打 `0/1`，生成物
                # 经过 C++ 的 `bool` 转换打 `False/True`）。
                #   · `map.insert(k,v)`      → u64：1 = 插进去了，0 = 键已存在
                #     （**不覆盖**，C++ 的 `pair.second` 数字版）；
                #   · `unordered_map.insert` → u64：恒 0（它**覆盖**）；
                #   · `set/unordered_set.insert` → u64：恒 0（插元素）；
                #   · `vector/array.insert(pos,v)` → void（interp 返回 None）。
                if _recv_head in ("vector", "array", "fixedvec"):
                    return TY_VOID
                if _recv_head in LANG_MAP_HEADS:
                    return TY_U64
                return TY_VOID
            if name == "contains" and _recv_head in LANG_MAP_HEADS:
                # **`contains(k)` 是谓词，返回 bool**（2026-11-13 修）：
                # 与 `empty()` 同一口径。`interp.py` 的 `MapVal` / `SetVal` /
                # `Unordered*` 分支返回 `True/False`，发射器头文件的
                # `bool contains(const K&) const` 也是 bool，两边一致。
                #
                # 上一轮"按 interp 对齐"把它声明成 u64（interp 当时返回
                # `_u64(1/0)`），类型更精确了，却让**唯一用途**编不过：
                # `if (m.contains(k))` 报 E-Type-Cond（`if` 只收 bool / 指针）——
                # 文档（`语法速查.md` §7.4、`语法清单.md`、`C++惯用法对照.md`）
                # 和 AI 实验 05 题（`if (cnt.contains(x))`）全是这个写法。
                # **返回 0/1 计数的是 insert/put/remove/erase**，不是 contains：
                # 那四个没人写进 `if`，继续按 u64 对齐 interp。
                return TY_BOOL
            if name == "put":
                # `m.put(k, v)`：**覆盖写**（本语言的扩展名），interp 返回
                # u64：1 = 键原本不存在（新插入），0 = 覆盖。
                return TY_U64 if _recv_head == "map" else TY_BOOL
            if name in ("erase", "remove"):
                # interp：map/set/unordered 的 `erase/remove` 返回 u64
                # （删掉了 = 1，本来没有 = 0）；vector/array 的 `erase(pos)`
                # 返回 void（interp 返回 None）。
                if _recv_head in ("vector", "array", "fixedvec"):
                    return TY_VOID
                if _recv_head in LANG_MAP_HEADS:
                    return TY_U64
                return TY_VOID
            if name == "clear":
                return TY_VOID
            if name == "keys" and _recv_head in ("map", "unordered_map",
                                                 "unordered_set"):
                # **`keys()` 是快照 `vector<K>`**（以前落到 `TY_UNKNOWN`，
                # 于是 `u64 n = m.keys();` 也放行——"不知道"等于什么都收）。
                return _vector_of(_map_key_ty_of(recv))
            if name == "values" and _recv_head in ("set", "unordered_set",
                                                   "unordered_map"):
                # `values()` 是快照：map 是 `vector<V>`，set 是 `vector<T>`。
                return _vector_of(_elem_ty_of(recv))
            if name in ("back", "front"):
                return _elem_ty_of(recv)
            return TY_UNKNOWN

        if k == "field":
            base = self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
            nm = n.name if n.name is not None else str(n.value or "")
            # **基址是 `void`**（2026-11-13）：`g().a` 以前落到下面的
            # "已知的非聚合值没有字段"（`E-Type-UnknownField`），三方都红但
            # 根因说不清。字段访问要的是**一个存在的值**，void 先拦。
            if _is_void_ty(base):
                self._err_void_value(f"字段访问 `.{nm}` 的基址", n)
                return TY_UNKNOWN
            # **指针上的 `.` 是 C++ 会拒绝的写法**（要写 `->`）。以前指针类型
            # 不存在，这里只会落到下面「未知字段」；现在明确说清改法。
            if base.kind == "ptr":
                self._err(
                    "E-Pointer-UseArrow",
                    f"`{nm}` 的基址是 {base.render()}（指针）：成员访问写 "
                    f"`p->{nm}`，不是 `p.{nm}`（`->` 会自动解引用；"
                    f"`p.{nm}` 在 C++ 里也不成立）。", n)
                return TY_UNKNOWN
            bt = self._deref_ty(base)
            # **枚举值上的 `.`**（2026-11-12）：枚举变体是**类型命名空间**的
            # 成员，MUST 用 `::`。`checker` 只查基址是**枚举类型名**的形状
            # （`Token.Plus`）；基址是**枚举值**时（`Token t = …; t.Plus`）
            # 以前完全不报——`check` 绿、`run` 在 `EnumVal` 上取字段**静默
            # 返回 0**。静默错值比崩更坏，所以在检查期挡。
            if bt.kind == "name" and bt.name in self.enums:
                _b = getattr(n.children[0], "name", "") if n.children else "?"
                self._err(
                    "E-Member-WrongSep",
                    f"`{_b}.{nm}` 的基址是 {bt.name} **枚举值**，不是结构体："
                    f"枚举变体与静态成员 MUST 用 `::`（例如 "
                    f"`{bt.name}::{nm}`）。`.变体` 在运行期读不到任何东西——"
                    f"以前会静默得到 0。", n)
                return TY_UNKNOWN
            # **写位置的元组字段**（2026-11-12）：`kv.1 = 5` / `kv.second = 5`
            # 在 C++ 里一个改得动（`pair` 的 second 是普通成员）、一个改不动
            # （`const pair&`），但本语言把两件事都简化成**拒绝**：
            #   · `map` 迭代出的 `(键, 值)` 是每轮现造的**只读视图**，写回去
            #     也只会丢写（宁可报，不许静默）；
            #   · 元组本身是值类型，没有可写的存储。
            # 报在 check 期（与运行期同一条口径），否则就是 check 绿、run 崩。
            if self._write_lvalue > 0 and bt.kind == "tuple":
                _s = None
                if n.children and getattr(n.children[0], "kind", "") == "path":
                    _s = sc.lookup(n.children[0].name or "")
                _view = bool(_s is not None and
                             str(getattr(_s, "origin", "")).startswith("map-pair"))
                _pair_nm = (_compat.enabled()
                            and nm in _compat.PAIR_FIELD_INDEX)
                if _view or _pair_nm:
                    if _view:
                        self._err(
                            "E-Assign-Immutable",
                            f"`{nm}` 是 `for (auto& kv : m)` 里**只读视图**的字段："
                            f"`map` 迭代出的是每轮现造的 `(键, 值)` 二元组，"
                            f"字段写不回去（写回只会丢写）。\n"
                            f"改表请写 `m[键] = 新值` / `m.put(键, 新值)`；"
                            f"要读就保持 `for (const auto& kv : m)`。", n)
                    else:
                        self._err(
                            "E-Assign-Immutable",
                            f"`pair.{nm}` 在 `--compat` 里映射到位置字段 "
                            f"`.{_compat.PAIR_FIELD_INDEX[nm]}`，而**元组的字段"
                            f"不能赋值**（元组是值类型，没有可写的存储）。\n"
                            f"要换元素就整体重建：`t = (新值0, t.1);`；"
                            f"`map` 迭代出的 `kv` 要改表请写 `m[键] = 新值`。", n)
                    return TY_UNKNOWN
            if nm.isdigit():
                idx = int(nm)
                if bt.kind == "tuple":
                    if idx < len(bt.args):
                        return bt.args[idx]
                    self._err(
                        "E-Type-UnknownField",
                        f"元组 {bt.render()} 只有位置字段 "
                        f"{'、'.join('.' + str(i) for i in range(len(bt.args)))}，"
                        f"没有 `.{idx}`", n)
                    return TY_UNKNOWN
                return TY_UNKNOWN
            if bt.kind == "tuple":
                # `p.first` / `p.second`（C++ `std::pair` 的字段名）：
                # **`--compat` 下按静态类型改写成位置字段**（2026-11-12，
                # 第 14 条的接线点之一）：只有基址**确实是二元组**才映射，
                # 所以 `struct Pair { first: …, second: … }` 里叫 `first`
                # 的字段**不会被改**（它走上面的 struct 分支）。
                # 本语言自己的写法仍是 `.0` / `.1`；`compat=False` 时
                # 一个字节都不动，照旧报 `E-Type-UnknownField`。
                _pi = (_compat.PAIR_FIELD_INDEX.get(nm)
                       if _compat.enabled() else None)
                if _pi is not None and int(_pi) < len(bt.args):
                    return bt.args[int(_pi)]
                self._err(
                    "E-Type-UnknownField",
                    f"元组 {bt.render()} 没有字段 `{nm}`：本语言的元组只有位置字段 "
                    f"{'、'.join('.' + str(i) for i in range(len(bt.args)))}"
                    f"（不是 C++ 的 `std::pair`，没有 `first` / `second`）。"
                    f"要读第一个元素写 `.0`；要名字可读就改用 struct "
                    f"（`struct Pair {{ first: u64, second: u64, }}`）。"
                    f"照抄 C++ 的 `pair.first` / `pair.second` 请加 `--compat`。", n)
                return TY_UNKNOWN
            if self._is_struct_ty(bt):
                fields = self._struct_fields(bt.name)
                if nm in fields:
                    # **返回真实字段类型**：以前一律 UNKNOWN，于是字段上的
                    # 类型错误要到下游才显形（或永不显形）。
                    return fields[nm]
                hint = "、".join(fields) if fields else "（无）"
                self._err(
                    "E-Type-UnknownField",
                    f"struct {bt.name} 没有字段 `{nm}`：它有字段 {hint}。"
                    f"字段名拼写要对得上（本语言没有 `first` / `second` 这类惯用名，"
                    f"以 `struct` 声明为准）。", n)
                return TY_UNKNOWN
            # **已知的非聚合值没有字段**（2026-11-12）：`xs.a` / `n.a` /
            # `"ab".a` 以前落到这句 `return UNKNOWN`——`check` rc=0，`run` 才抛
            # `InterpError: 未实现的字段访问 a`。字段访问只对 struct / 元组成立；
            # 类型**推不出**时（`bt == UNKNOWN`）不猜，避免级联误报。
            if bt.kind != "unknown" and not (
                    bt.kind == "name" and bt.name in self.type_params):
                self._err(
                    "E-Type-UnknownField",
                    f"{bt.render()} 没有字段 `{nm}`：只有 struct 和元组有字段"
                    f"（元组写 `.0` / `.1`）。容器的长度用 `.size()`，"
                    f"元素用 `[i]`；枚举变体用 `::{nm}`，不是 `.`。", n)
            return TY_UNKNOWN

        if k == "try":
            inner = self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
            if inner.kind != "unknown" and not (inner.kind == "name"
                                                and inner.name == "Result"):
                self._err("E-Try-NotResult",
                          f"`?` 只能用于 Result，实际是 {inner.render()}", n)
                return TY_UNKNOWN
            return inner.args[0] if inner.args else TY_UNKNOWN

        if k == "dispatch":
            label = n.name or ""
            for fn in self.funcs.values():
                for kern in fn.children:
                    if kern.kind == "kernels" and kern.name == label:
                        return self._ret_of(kern)
            return TY_UNKNOWN

        if k == "cast":
            # `as u128` 也是**类型位置**：以前这里直接 `ty_of_node`，于是
            # `u64 y = 0 as u128;` 报「初值是 u128」——把不存在的类型名
            # 当成真类型带进后面的比较。现在先查未知类型名（报 `E-Type-Unknown`），
            # 再退成 `UNKNOWN`（不再级联出误导的 E-Type-Assign）。
            #
            # **操作数也要检查**（2026-11-12）：以前这个分支**根本不进
            # `n.children[0]`**，于是 `(nope) as u64` / `(t.Variant) as u64`
            # 里的 `nope` / `t.Variant` 一条诊断都没有——`check` 绿、`run`
            # 才抛「未绑定的名字 nope」（或静默取到 0）。转换的目标类型不改变
            # "操作数必须先是一个合法表达式" 这条。
            if n.children:
                _src_t = self._expr(n.children[0], sc)
                # **`void as T`**（2026-11-13）：`(g()) as u64` 以前 check 绿、
                # `run` 绿（`None` 转成 0 之类的静默值），`dsh cpp` 报
                # `E-Cxx-Unsupported`。`as` 要的是**操作数的值**。
                if _is_void_ty(_src_t):
                    self._err_void_value("`as` 的操作数", n)
                    return TY_UNKNOWN
            return self._decl_ty(n.children[1]) if len(n.children) > 1 else TY_UNKNOWN

        if k == "index":
            base = self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
            # `const Vec<u64>& xs` 的类型是 ref；取元素类型要先拆引用，
            # 否则 `xs[0] = 5` 会被当成「给 Vec<u64> 赋 i64」误报 E-Type-Assign。
            while base.kind == "ref" and base.args:
                base = base.args[0]
            # **`void` 不能作下标基址**（2026-11-13）：`g()[0]`。
            if _is_void_ty(base):
                self._err_void_value("下标的基址", n)
                return TY_UNKNOWN
            # **下标表达式本身**（2026-11-13）：这个分支以前**根本不进
            # `children[1]`**——`xs[g()]` / `xs[xs.assign(2, 0)]` 里的 void
            # 一条诊断都没有：`run` 抛 `E-Index-Type`、`dsh cpp` 出码给
            # g++ 才拒（`array subscript is not an integer`）。
            # 这里只补"必须是有值的表达式"；下标是不是整数由运行期/发射器
            # 各自的既有口径管（不在这里新增更严的整数判据）。
            if len(n.children) > 1:
                if _is_void_ty(self._expr(n.children[1], sc)):
                    self._err_void_value("下标", n)
                    return TY_UNKNOWN
            # **包装值不能下标**（K5 的旁支）：`vec_pop(h)[0]` 以前拿到
            # `Option` 载荷的类型（`args[0]`），运行期却在 `EnumVal` 上取下标。
            if _needs_unwrap(base):
                self._err("E-Type-OptionToScalar",
                          f"下标 / `.at()` 不能用于 {base.render()}：它是包装值/"
                          f"不透明值，不是容器。{_unwrap_hint(base)}", n)
                return TY_UNKNOWN
            # **`p[i]` 是指针算术**：`char** argv` 的 `argv[i]` 是 `char*`
            # （等价于 `*(argv + i)`）。结果类型 = **指向的类型**。
            if base.kind == "ptr":
                return base.args[0] if base.args else TY_UNKNOWN
            # **标量 / 枚举不能下标**（2026-11-13，缺口 ③ 的旁支）：
            # `u64 x = 5; x[0]` 以前 check 绿——这个分支只按容器取元素类型，
            # 非容器一律落到最后的 `TY_UNKNOWN`；`run` 报 `E-Type-NotIndexable`
            # （`interp.py` 的容器分支），`dsh cpp` 还照样出码（`x[0]` 连 g++
            # 都过不了）。与 `.size()` 同一条判据：**只有容器能下标**。
            # 只认**已知的标量 / 枚举**：`unknown` / 类型参数（泛型 `T`）不猜。
            _bh = base.name if base.kind == "name" else ""
            if _bh and (canon(_bh) in NUMERIC or canon(_bh) in FLOATS
                        or canon(_bh) == "bool" or _bh in self.enums):
                self._err(
                    "E-Type-NotIndexable",
                    f"对 {base.render()} 取下标：**标量 / 枚举没有元素**，"
                    f"只有容器（vector / array / map …）能用 `[i]`。"
                    f"标量按位取值请写位运算（`x & 1` / `x >> 3`），"
                    f"按位置取东西先放进容器。", n)
                return TY_UNKNOWN
            # **`m[k]` 的值类型是 V，不是 K**（map<K,V> 的 args = [K, V]）。
            # 以前一律取 `args[0]`，于是 `m[1]` 的类型被当成键类型——
            # 恰好都是 u64 时看不出来，`map<u64, string>` 就错了。
            if base.kind == "name" and canon(base.name) in ("map", "unordered_map"):
                # **读位置的 `m[k]` 会静默插入默认值**（2026-11-11，审计 A3）：
                # 语义对齐 C++ 的 `operator[]`，陷阱也跟着来 ⇒ 发
                # `W-Map-SilentInsert`（`_place` 里的写目标不算，那里是写者本意）。
                if not self._write_lvalue and not getattr(n, "_write_place", False) \
                        and not self._in_map_write_stmt(n):
                    self._warn_map_silent_insert(n)
                return base.args[1] if len(base.args) >= 2 else TY_UNKNOWN
            return base.args[0] if base.args else TY_UNKNOWN

        if k == "ref":
            # `&x`：取地址。返回 `addrof`（**中间类型**，见 `Ty` 的说明）：
            #   · 落到 `T*` / `const T*` → 指针（`u64* p = &x;`）；
            #   · 落到 `T&` / `const T&` 形参 → 显式借用标记（calculator.eln）；
            #   · 落到标量 → 不兼容（`u64 y = &x;` 仍然报错）。
            inner = self._expr(n.children[0], sc) if n.children else TY_UNKNOWN
            return Ty("addrof", args=[inner])

        return TY_UNKNOWN

    def _check_hof_container(self, name: str, ty: Optional[Ty],
                             n: Node) -> Optional[bool]:
        """高阶内建的**第一个实参**是不是解释器认的容器。

        返回 `True`（已知是容器）/ `False`（已知不是，已报错）/ `None`
        （类型推不出：不猜，避免级联误报）。

        判据来自 `interp._builtin_higher` / `_builtin_sort` / `for_each`：
        前三者都先判 `isinstance(recv, list)`；`for_each` 另认
        `MapVal` / `SetVal` / stdlib `HashMap`。`vector<u8>`（`Text`）是
        `list` 的子类，所以照样算 list-like。
        """
        if ty is None:
            return None
        bt = self._deref_ty(ty)
        if bt.kind == "unknown":
            return None
        head = canon(bt.name) if bt.kind == "name" else ""
        ok = head in HOF_LIST_LIKE_HEADS
        if name == "for_each":
            ok = ok or head in HOF_MAP_HEADS or head in HOF_SET_HEADS
        if ok:
            return True
        if name == "for_each":
            _hint = ("`for_each` 的第一个实参必须是 `Vec` / `array`（一元回调）"
                     "或 `map` / `set` / `unordered_map` / `unordered_set` / "
                     "`HashMap`（map 类给二元回调）")
        else:
            _hint = (f"`{name}` 的第一个实参必须是 `vector` / `array` / "
                     f"`FixedVec`（map / set 请用 `for_each`）")
        self._err("E-Type-HofContainer",
                  f"{_hint}，收到 {bt.render()}。", n)
        return False

    def _check_hof_callback(self, name: str, cb: Node, want: int,
                            sc: Scope) -> None:
        """`map` / `filter` / `fold` / `count_if` / `for_each` 的函数实参。

        解释器（`interp._builtin_higher`）的判据只有一条：**实参必须是
        本模块的实现函数名**，且按固定元数调用（`map/filter/count_if/for_each`
        调 1 个、`fold` 调 2 个）。这里照抄同一条判据，把三类运行期错误
        挪到检查期：

        | 写法 | 运行期（以前 check 全绿） |
        |---|---|
        | `fold(xs, 0, onearg)` | `InterpError: onearg 需要 1 个实参，给了 2` |
        | `map(xs, g)`（`g` 是函数值形参） | `InterpError: map 的函数实参 g 不是本模块的函数` |
        | `map(xs, twice(1))` | `InterpError: … MUST 是具名函数` |

        **只对能静态判定的形状报**：名字查不到（拼错）由 `_expr(path)` 的
        `E-Name-Undefined` 管，这里不重复；`self.funcs` 里有实现才比元数。
        """
        if name == "fold":
            _where = "(acc, x)"
        elif name == "for_each" and want == 2:
            _where = "(k, v)"
        else:
            _where = "(x)"
        if getattr(cb, "kind", "") != "path":
            self._err(
                "E-Type-HofCallback",
                f"{name} 的函数实参 MUST 是**具名函数**（写函数名），"
                f"不是 {cb.kind} 表达式：匿名函数 / 调用结果都是任意代码，"
                f"κ 无法静态界定。", cb)
            return
        cname = cb.name or ""
        cdecl = self.funcs.get(cname)
        if cdecl is not None:
            got = len(self._params_of(cdecl))
            if got != want:
                self._err(
                    "E-Type-HofCallback",
                    f"{name} 的回调 `{cname}` 要 {want} 个形参"
                    f"（解释器按 {_where} 调用），它声明了 {got} 个。"
                    f"改法：把 `{cname}` 改成 {want} 个形参，或换一个签名匹配的函数。",
                    cb)
            return
        sym = sc.lookup(cname)
        if sym is None:
            return                       # 拼错 → `_expr(path)` 已报 E-Name-Undefined
        if getattr(sym, "kind", "") == "func":
            self._err(
                "E-Type-HofCallback",
                f"{name} 的函数实参 `{cname}` 只有声明（`.tu` 的 spec / extern），"
                f"没有本模块的实现体：解释器按**实现函数**查找回调，找不到就会"
                f"在运行期抛「不是本模块的函数」。\n"
                f"改法：给 `{cname}` 补实现，或换一个已实现的具名函数。", cb)
            return
        _sig = ", ".join(["u64 acc", "u64 x"][:want]) if name == "fold" \
            else ", ".join(["u64 x"][:want])
        self._err(
            "E-Type-HofCallback",
            f"{name} 的函数实参 `{cname}` 不是**模块函数**，而是函数值"
            f"（形参 / 局部变量）：本语言没有一等函数，高阶内建只接受"
            f"本模块的具名实现函数。\n"
            f"改法：把回调写成模块级函数 `u64 {cname}({_sig}) {{ … }}`，"
            f"直接传函数名。", cb)

    def _check_callee(self, name: str, callee: Node, args: List[Ty],
                      n: Node) -> None:
        """按被调函数的声明校验实参（用户函数 / spec / extern 共用一套）。"""
        want = self._params_of(callee)
        # **类型参数当通配符**（泛型最小切片）：形参类型若是被调函数的
        # 类型参数（`func pick<T>(T a, ...)`），任何实参都算匹配。
        # 完整做法要做统一（unification），这里只到"能写、能检查、能跑"。
        _tps = {getattr(c, "name", "") for c in
                next((x for x in getattr(callee, "children", [])
                      if getattr(x, "kind", "") == "type-params"), Node("x")).children}
        if len(want) == len(args):
            _bound = {}     # 类型参数 → 首次绑定的类型（统一）
            for (pn, pt), at in zip(want, args):
                if getattr(pt, "kind", "") == "name" and pt.name in _tps:
                    _prev = _bound.get(pt.name)
                    if _prev is None:
                        _bound[pt.name] = at
                    elif not compatible(at, _prev):
                        # **统一失败**：`T` 不能同时是两种类型。
                        self._err(
                            "E-Type-ArgMismatch",
                            f"调用 {name}：类型参数 {pt.name} 同时被绑成 "
                            f"{_prev.render()} 和 {at.render()}", n)
                    continue
                if not compatible(at, pt):
                    self._err("E-Type-ArgMismatch",
                              f"调用 {name}：形参 `{pn}` 是 {pt.render()}，"
                              f"实参是 {at.render()}", n)

    def _find_decl(self, name: str) -> Optional[Node]:
        return self.specs.get(name)

    def _ret_decl(self, name: str) -> Ty:
        fn = self.funcs.get(name)
        if fn is not None:
            t = self._ret_of(fn)
            return t
        sp = self._find_decl(name)
        if sp is not None:
            return self._ret_of(sp)
        return TY_UNKNOWN


# ---------------------------------------------------------------- 便利入口

def typecheck(tu_mod: Optional[Node], ch_mod: Optional[Node]) -> List[Diag]:
    return TypeChecker(tu_mod, ch_mod).check()


def resolve_static_cast(eln_mod: Optional[Node],
                        tu_mod: Optional[Node] = None) -> None:
    """**把可无损折叠的 `static_cast<…>` 就地换成实参**（P0 兼容层第 11 条）。

    判据**只有一份**：`_static_cast_fold_ok`（检查期 `_expr` 的 `cast-static`
    分支用的是同一个函数）。折不了的一律**保持原节点**，由 `check` 那遍报
    `E-Cast-Unsupported`——所以这里**吞掉所有诊断**，只做 AST 改写。

    ## 怎么拿到"实参的静态类型"

    `static_cast<unsigned char>(c)` 里的 `c` 可能来自 `for (auto c : s)`，
    解析器不知道它是 `u8`——类型只在类型检查器里有。所以这里借
    `_TypeRecorder` 跑一遍**同一套**类型检查，顺手把每个表达式节点的类型
    记进 `{id(node): Ty}`，再按这张表折。

    **类型推不出 → 不折**（记的是 `TY_UNKNOWN`，`_static_cast_fold_ok` 会给
    出"推不出来"的理由）。这正是"宁可多报错，也不许把真转换折成 no-op"。

    调用点两处（与 `resolve_auto` 同构）：
      - `parser.parse_source`：**尽力而为**（只看本文件），折叠成功下游
        （`cost` / `run` / `build`）看到的 AST 与手写 `x` **逐字节同形**；
      - `dsh._compat_resolve_auto`：成对的**权威**一遍（`.tu` 也看得见）。
    """
    if eln_mod is None:
        return
    if not _tree_has_kind(eln_mod, "cast-static"):
        return
    rec = _TypeRecorder(tu_mod, eln_mod)
    try:
        rec.check()
    except Exception:                                    # noqa: BLE001
        return
    _fold_casts_in_place(eln_mod, rec.ty_of)


def _tree_has_kind(node: Optional[Node], kind: str) -> bool:
    """子树里有没有 `kind` 节点（`cast-static` 的快速判据）。"""
    if node is None:
        return False
    if getattr(node, "kind", "") == kind:
        return True
    return any(_tree_has_kind(c, kind) for c in (node.children or []))


def _fold_casts_in_place(node: Optional[Node],
                         ty_of: Dict[int, Ty]) -> Optional[Node]:
    """递归折叠：**每个子节点先折**，再看自己是不是 `cast-static`。

    先折子节点是为了 `static_cast<u64>(static_cast<u64>(x))` 这类嵌套：
    内层折完变成 `x` 后，外层看到的实参就是 `x`（它的类型也在 `ty_of` 里，
    因为记录那一遍对两层都求过类型）。
    """
    if node is None:
        return None
    for i, c in enumerate(list(node.children or [])):
        node.children[i] = _fold_casts_in_place(c, ty_of)
    if getattr(node, "kind", "") == "cast-static" and node.children:
        src = node.children[0]
        ty = ty_of.get(id(src))
        if ty is not None and _static_cast_fold_ok(node.name or "", ty) is None:
            return src
    return node


class _TypeRecorder(TypeChecker):
    """跑一遍类型检查，同时记下 `{id(表达式节点): Ty}`（`resolve_static_cast` 用）。

    **只记录、不改判定**：所有诊断照样产出，只是调用方不要。
    用 `id()` 当键：AST 节点在树的存活期内不会被复制/重建，折叠期拿到的是
    同一批对象。
    """

    def __init__(self, tu_mod: Optional[Node], ch_mod: Optional[Node]):
        super().__init__(tu_mod, ch_mod)
        self.ty_of: Dict[int, Ty] = {}
        #: **不许在 `check()` 开头再折一遍**：这一遍本身就是折叠用的
        #: （折不动时若再进来会无限递归）。
        self._fold_casts_before_check = False

    def _expr(self, n, sc):                              # noqa: D102
        ty = super()._expr(n, sc)
        if n is not None:
            self.ty_of[id(n)] = ty
        return ty


def resolve_auto(eln_mod: Optional[Node],
                 tu_mod: Optional[Node] = None) -> List[Diag]:
    """**把 `type-auto` 就地推断成具体类型**（P0 兼容层第 3 条）。

    `--compat` 下 `auto x = expr;` 先被 parser 解析成「类型待定」的声明；
    真正的推断只有**一份实现**（`TypeChecker._compat_auto_ty`），这里只是
    把整棵树交给它跑一遍。返回诊断，**从不抛异常**（推断失败不是崩溃）。

    两个调用点，职责不同：

    - `parser.parse_source`：**尽力而为**的一遍（只有本文件可见）。成功就把
      `type-auto` 换掉——这样 `cost` / `build` / `stats` 看到的 AST 已经是
      具体类型，不会拿"未知类型"去算 κ。失败就**保持原样**、不留诊断，
      交给下面那遍权威判定；
    - `dsh._diagnose_all` 里成对的 `typecheck`：**权威**的一遍（`.tu` 的
      `spec` 也能看见），失败就报 `E-Compat-AutoNoType`。
    """
    if eln_mod is None:
        return []
    try:
        return TypeChecker(tu_mod, eln_mod).check()
    except Exception:                                    # noqa: BLE001
        return []



if __name__ == "__main__":
    import stdlib
    import sys
    from parser import parse_source

    sys.stdout.reconfigure(encoding="utf-8")
    srcs = [open(p, encoding="utf-8").read() for p in sys.argv[1:]]
    tu = ch = None
    for p, s in zip(sys.argv[1:], srcs):
        m = parse_source(s, p).module
        if p.endswith(".tu"):
            tu = m
        else:
            ch = m
    diags = typecheck(tu, ch)
    for d in diags:
        print(f"  {d.code} @{d.line}:{d.col}  {d.message}")
    print(f"\n{len(diags)} 条诊断")
