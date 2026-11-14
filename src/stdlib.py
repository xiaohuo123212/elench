#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""stdlib.py —— **最小基本库（L1）**：语言给定的容器/算法内建

## 为什么集中在一个文件里

每个内建要同时被三处认出来：**解释器**（怎么跑）、**类型检查**（什么类型）、
**成本分析**（花多少操作）。三处各写一遍，早晚会不一致（我们已经在 `sort` 上
见过一次：解释器和成本分析对"比较器是名字"的处理必须对齐）。

所以：**一个注册表，三处查同一张表。**

## 收编标准（每加一个都要过）

1. **成本必须能用输入尺寸静态定界**（`O(n)` / `O(log n)` / `O(1)`）；
2. **不引入新的不可知成本源**（不做 I/O、不做时钟、不做随机）；
3. **不引入任意代码**（比较器只能传**具名函数**，不能传闭包——见 `_builtin_sort`）。

## 这一批只做"返回 u64 的容器操作"

| 名字 | 签名 | 成本 |
|---|---|---|
| `reverse(xs)` | 原地反转，返回元素个数 | `O(n)` |
| `fill(xs, v)` | 全部置为 `v`，返回元素个数 | `O(n)` |
| `dedup(xs)` | 去掉**相邻**重复，返回新长度 | `O(n)` |
| `contains(xs, v)` | 有则 1，否则 0 | `O(n)` |
| `count_of(xs, v)` | 出现次数 | `O(n)` |
"""
import math
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

#: 名字 → (实参个数, 解释器实现, 成本阶)
#: 成本阶用字符串：`"n"` 表示 `O(n(args[0]))`，`"1"` 表示 `O(1)`。
FNS: Dict[str, Dict[str, Any]] = {}


def _register(name: str, arity: int, impl: Callable[..., Any], cost: str,
              ret: str = "u64", empty: str = "",
              varargs: bool = False) -> None:
    """登记一个内建。

    `arity` 对普通内建是**精确**实参个数；`varargs=True` 时它只是**下限**
    （今天只有 `format`：格式串 + 每个洞一个实参）。类型检查器必须按
    「至少 arity 个」判，否则多洞写法会被固定 arity 误杀。

    `ret` **不是文档字段**：它必须在 `RET_KINDS` 里，并且
    `typecheck._builtin_ret_ty` 有对应分支。写成词表外的值不会静默通过——
    类型检查器会把它当不透明 `Any`（`u64 x = f()` 会报 `E-Type-Assign`）。
    **别再用 `ret="any"` 偷懒**：返回 `Option` / `Result` / 容器就必须写出来，
    否则调用点会失去类型（K5 就是这么来的）。
    """
    FNS[name] = {"arity": arity, "impl": impl, "cost": cost,
                 "ret": ret, "empty": empty, "varargs": varargs}
#: **已弃用的内建**：名字 → 手写等价代码（v0.73 真正移除）。
#:
#: 判据：能用一个 `for` 循环写出来、且 κ 照样数得出来 → 不值得做成内建。
#: 保留实现只为**不打断现有代码**；调用会得到 `W-Builtin-Deprecated`，
#: 消息里直接给出该改写成什么。
REPLACEMENTS: Dict[str, str] = {
    "sum_of": "for (auto x : xs) { s = s + x; }",
    "count_of": "for (auto x : xs) { if (x == v) { c = c + 1; } }",
    "contains": "for (auto x : xs) { if (x == v) { return 1; } }",
    "index_of": "for (u64 i = 0; i < xs.length(); i = i + 1) { if (xs.at(i) == v) { return i; } }",
    "is_sorted": "for (u64 i = 0; i + 1 < xs.length(); i = i + 1) { if (xs.at(i) > xs.at(i + 1)) { return 0; } }",
    "reverse": "for (u64 i = 0; i < xs.length() / 2; i = i + 1) { u64 t = xs.at(i); xs.at(i) = xs.at(xs.length() - 1 - i); xs.at(xs.length() - 1 - i) = t; }",
    "fill": "for (u64 i = 0; i < xs.length(); i = i + 1) { xs.at(i) = v; }",
    "fill_range": "for (u64 i = lo; i < hi; i = i + 1) { xs.at(i) = v; }",
    "rotate": "先 reverse 整体、再分两段 reverse（或按 k 取模搬移）",
    "copy": "vector<u64> out; for (auto x : xs) { out.push(x); }",
    "for_each": "for (auto& kv : m) { visit(kv.first); }",
}

#: 弃用内建的注册表（**不在 `FNS` 里**，`is_stdlib` 仍认它们，以便老代码可跑）
DEPRECATED_BUILTINS: Dict[str, Dict[str, Any]] = {}


def _register_deprecated(name: str, arity: int, impl: Callable[..., Any],
                         cost: str, ret: str = "u64", empty: str = "") -> None:
    DEPRECATED_BUILTINS[name] = {
        "arity": arity, "impl": impl, "cost": cost, "ret": ret,
        "empty": empty, "deprecated": True,
        "replacement": REPLACEMENTS.get(name, ""),
    }


def _n_of(xs: Any) -> int:
    try:
        return len(xs)
    except TypeError:
        return 0


def _reverse(args: List[Any]) -> int:
    xs = args[0]
    xs.reverse()
    return len(xs)


def _fill(args: List[Any]) -> int:
    xs, v = args[0], args[1]
    for i in range(len(xs)):
        xs[i] = v
    return len(xs)


def _dedup(args: List[Any]) -> int:
    """去掉**相邻**重复（与 C++ `unique` 同义，不是"全局去重"）。

    返回新长度；尾部残留元素**不清理**（调用方用返回长度截断）。
    """
    xs = args[0]
    if not xs:
        return 0
    k = 1
    for i in range(1, len(xs)):
        if xs[i] != xs[k - 1]:
            xs[k] = xs[i]
            k += 1
    return k


def _contains(args: List[Any]) -> int:
    xs, v = args[0], args[1]
    return 1 if v in xs else 0


def _count_of(args: List[Any]) -> int:
    xs, v = args[0], args[1]
    return sum(1 for x in xs if x == v)


_register_deprecated("reverse", 1, _reverse, "n")
_register_deprecated("fill", 2, _fill, "n")
#: `empty` 在 `dedup` 上记的不是"空输入"，而是**这条内建的语义陷阱**——
#: 只去相邻重复、容器不变短（`std::unique` 语义）。实测见 `文档/内建函数.md` 文末。
_register("dedup", 1, _dedup, "n",
          empty="只去**相邻**重复（`std::unique` 语义）：返回新长度，**尾部残留不清理**")
_register_deprecated("contains", 2, _contains, "n", empty="空 → 0（不含）")
_register_deprecated("count_of", 2, _count_of, "n", empty="空 → 0 次")


def _index_of(args: List[Any]) -> int:
    """第一个等于 `v` 的下标；**没有则返回 `len(xs)`**（哨兵，与"未找到"同义）。"""
    xs, v = args[0], args[1]
    for i, x in enumerate(xs):
        if x == v:
            return i
    return len(xs)


def _binary_search(args: List[Any]) -> int:
    """升序表上的二分；**没有则返回 `len(xs)`**。表**必须已排序**（不检查）。"""
    xs, v = args[0], args[1]
    lo, hi = 0, len(xs)
    while lo < hi:
        mid = (lo + hi) // 2
        if xs[mid] < v:
            lo = mid + 1
        else:
            hi = mid
    if lo < len(xs) and xs[lo] == v:
        return lo
    return len(xs)


def _sum_of(args: List[Any]) -> int:
    """求和（按 u64 回绕语义取模）。空 → 0。"""
    return sum(args[0]) & ((1 << 64) - 1)


def _is_sorted(args: List[Any]) -> int:
    """非降序 → 1，否则 0。空/单元素 → 1。"""
    xs = args[0]
    for i in range(1, len(xs)):
        if xs[i - 1] > xs[i]:
            return 0
    return 1


_register_deprecated("index_of", 2, _index_of, "n", empty="未找到 → len(xs)")
_register("binary_search", 2, _binary_search, "log",
          empty="未找到 → `len(xs)`（**不是 -1**）；表须已升序")
_register_deprecated("sum_of", 1, _sum_of, "n", empty="空 → 0")
_register_deprecated("is_sorted", 1, _is_sorted, "n", empty="空 → 1")


def _slice(args: List[Any]) -> int:
    """原地保留 `[lo, hi)`，返回新长度。越界按夹取（clamp）处理。"""
    xs, lo, hi = args[0], int(args[1]), int(args[2])
    lo = max(0, min(lo, len(xs)))
    hi = max(lo, min(hi, len(xs)))
    keep = list(xs[lo:hi])
    xs[:] = keep
    return len(xs)


def _rotate(args: List[Any]) -> int:
    """原地**左旋** `k` 位（`k` 按 n 取模），返回 n。"""
    xs, k = args[0], int(args[1])
    n = len(xs)
    if n == 0:
        return 0
    k %= n
    if k:
        xs[:] = list(xs[k:]) + list(xs[:k])
    return n


def _fill_range(args: List[Any]) -> int:
    """把 `[lo, hi)` 置为 `v`，返回被赋值的元素个数。越界按夹取。"""
    xs, lo, hi, v = args[0], int(args[1]), int(args[2]), args[3]
    lo = max(0, min(lo, len(xs)))
    hi = max(lo, min(hi, len(xs)))
    for i in range(lo, hi):
        xs[i] = v
    return hi - lo


_register("slice", 3, _slice, "n", empty="lo==hi → 清空")
_register_deprecated("rotate", 2, _rotate, "n", empty="空 → 0")
_register_deprecated("fill_range", 4, _fill_range, "n", empty="lo==hi → 0 个")


# ------------------------------------------------------------------ 哈希表（有界探测）
#
# **为什么是"有界探测"而不是"期望 O(1)"**：
# 期望复杂度进不了契约——它依赖数据。所以这里**硬性限制探测次数**：
# 最多 `HMAP_PROBES` 次，放不下就**返回 0（失败）**。
# 于是单次操作的工作量是**常数上界**，`O(1)` 是**真的上界**，不是"平均"。
#
# **键长度问题**：标量键是常数时间；**容器键不是**。`vector<T>` / `array<T,N>`
# 的哈希必须**读完整个键**，所以单次哈希是 `O(len(key))`，整个 `get/insert`
# 是 `O(len(key))`（不是 O(1)）。`hash_key` 的实现口径见下；κ 侧由
# `cost_domain/cost_interp.py` 对 `unordered_*` 的容器键加 `n(键表达式)` 项。
#
# **结构体不做键**：字段的哈希没有稳定契约（字段可能含容器 / 浮点），C++ 的
# `std::hash<struct>` 也不存在。`check` 继续报 `E-Type-UnhashableKey`。

HMAP_PROBES = 8
HMAP_HASH_MUL = 2654435761

#: FNV-1a 的 32 位参数——**与 `MapVal._h` 原来那份完全相同**。
#: 容器键（`vector` / `array`）逐元素喂进这个混合器；标量键仍走
#: `HMAP_HASH_MUL`（与 `_hmap_index` 同一套）。**只有一份实现**：
#: `MapVal._h` 与语言 `unordered_*` 都调用 `hash_key()`。
_FNV_OFFSET = 2166136261
_FNV_PRIME = 16777619
_HASH32 = 0xFFFFFFFF
_HASH31 = 0x7FFFFFFF


def _hash_mix(acc: int, h: int) -> int:
    """把 32 位 `h` 按 FNV-1a 喂进累加器（4 字节，低字节在前）。"""
    for shift in (0, 8, 16, 24):
        acc = ((acc ^ ((h >> shift) & 0xFF)) * _FNV_PRIME) & _HASH32
    return acc


def hash_key(key) -> int:
    """键的哈希（**标量与容器共用**，31 位非负）。

    - 标量（整数 / 浮点 / bool / enum）：`(hash(k) * HMAP_HASH_MUL) & 0x7fffffff`
      —— 与旧的 `MapVal._h` 标量分支**逐位相同**，也与 `_hmap_index` 同一套乘法。
    - 容器（`list` / `tuple`，即 `vector<T>` / `array<T,N>` / `Text`）：FNV-1a
      逐元素混入，**复杂度 O(len(key))**；嵌套容器递归，长度也混进去。
    - 容器里的浮点用 Python 的 `hash(float)`（相等浮点 → 相等哈希；`-0.0 == 0.0`
      也成立），`+inf/-inf/nan` 不会抛异常。

    **相等性不在这里**：哈希只决定桶；冲突后由 `interp._HashKey.__eq__` 做
    **逐元素真比较**（`vector<u8>` 按字节/元素相等，不是对象身份）。
    """
    if isinstance(key, (list, tuple)):
        acc = _FNV_OFFSET
        for e in key:
            if isinstance(e, (list, tuple)):
                h = hash_key(e)
            else:
                try:
                    h = hash(e) & _HASH32
                except TypeError:           # pragma: no cover - check 已拦
                    h = id(e) & _HASH32
            acc = _hash_mix(acc, h)
        return _hash_mix(acc, len(key)) & _HASH31
    return (hash(key) * HMAP_HASH_MUL) & _HASH31


def _hmap_index(k: int, slots: int) -> int:
    return ((k & 0xFFFFFFFFFFFFFFFF) * HMAP_HASH_MUL) % slots


def _hmap_put(args: List[Any]) -> int:
    """写入/更新，**最多探测 8 次**；放不下返回 0（表满或探测耗尽）。"""
    buf, k, v = args[0], int(args[1]), int(args[2])
    slots = len(buf) // 2
    if slots == 0 or k == 0:
        return 0
    i = _hmap_index(k, slots)
    for _ in range(HMAP_PROBES):
        key = buf[2 * i]
        if key == k:
            buf[2 * i + 1] = v
            return 1
        if key == 0:
            buf[2 * i] = k
            buf[2 * i + 1] = v
            return 1
        i = (i + 1) % slots
    return 0


def _hmap_get(args: List[Any]) -> int:
    """查值；**最多探测 8 次**，未命中返回 **-1**（返回类型 `i64`）。

    为什么用 -1 而不是 0：**值 0 是合法的**（`hmap_put(t, 7, 0)`），
    用 0 当"缺失"会分不清"存了 0"和"没这个键"。
    代价：值的有效范围变成 `i64`（63 位有符号）。
    """
    buf, k = args[0], int(args[1])
    slots = len(buf) // 2
    if slots == 0 or k == 0:
        return -1
    i = _hmap_index(k, slots)
    for _ in range(HMAP_PROBES):
        key = buf[2 * i]
        if key == k:
            return buf[2 * i + 1]
        if key == 0:
            return -1
        i = (i + 1) % slots
    return -1


def _hmap_has(args: List[Any]) -> int:
    return 1 if _hmap_get([args[0], args[1]]) >= 0 else 0


_register("hmap_put", 3, _hmap_put, "1", empty="满/探测耗尽 → 0")
_register("hmap_get", 2, _hmap_get, "1", ret="i64", empty="未命中 → -1")
_register("hmap_has", 2, _hmap_has, "1", empty="未命中 → 0")


# ------------------------------------------------------------------ 随机
#
# **设计（2026-10-31 定）**：
#   · `rand_u64()` 无参数；**熵源内部读时钟**，不暴露 `now_ms`；
#   · 环境变量 `ELENCH_SEED` 可覆盖熵源 → **完全可复现**；
#   · 成本 **O(1) 静态上界**（splitmix64 一步）；
#   · 效果 **`nondet`**：调用方 MUST 声明 `effects { nondet }`；
#   · 并行：每个任务一条**独立且确定**的流，由 `(基种子, 任务下标)` 导出，
#     于是同种子同程序 → 同结果，**与调度无关**。

_MASK64 = (1 << 64) - 1


def _splitmix64(x: int):
    x = (x + 0x9E3779B97F4A7C15) & _MASK64
    z = x
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
    return (z ^ (z >> 31)) & _MASK64, x


class _Rand:
    def __init__(self, seed: int):
        self.seed = seed & _MASK64

    def next(self) -> int:
        v, self.seed = _splitmix64(self.seed)
        return v


#: I/O 用量与时耗：`{name: [次数, 秒]}`——`bench` 用它把 I/O 从总时间里**摘出来**。
IO_STATS: Dict[str, list] = {}


def reset_io_stats() -> None:
    IO_STATS.clear()


def io_report() -> Dict[str, list]:
    return {k: list(v) for k, v in IO_STATS.items()}


def _stats_add(name: str, seconds: float) -> None:
    rec = IO_STATS.setdefault(name, [0, 0.0])
    rec[0] += 1
    rec[1] += seconds


_tls = threading.local()
_base: Optional[_Rand] = None


def base_seed() -> int:
    """基种子：优先 `ELENCH_SEED`，否则时钟。"""
    raw = os.environ.get("ELENCH_SEED", "").strip()
    if raw:
        try:
            return int(raw) & _MASK64
        except ValueError:
            return hash(raw) & _MASK64
    # **默认熵源 = 时钟 ⊕ 进程熵**：只用 `time_ns` 在同一纳秒内会撞种子，
    # 混入 `os.urandom(8)` 之后每次运行都不可能撞。
    return (time.time_ns() ^ int.from_bytes(os.urandom(8), "little")) & _MASK64


def reset_base(seed: Optional[int] = None) -> int:
    global _base
    _base = _Rand(base_seed() if seed is None else seed)
    return _base.seed


def set_task_stream(seed: int, idx: int) -> None:
    """给并行任务一条独立流：`seed` 与任务下标混合。"""
    _tls.rng = _Rand((seed ^ (idx * 0x9E3779B97F4A7C15)) & _MASK64)


def clear_task_stream() -> None:
    if hasattr(_tls, "rng"):
        del _tls.rng


def _rand_u64(args: List[Any]) -> int:
    rng = getattr(_tls, "rng", None)
    if rng is None:
        global _base
        if _base is None:
            _base = _Rand(base_seed())
        rng = _base
    return rng.next()


#: 内建 → 效果集。**这是"可撒谎位置"之外唯一能自动验证的契约维度。**
EFFECTS = {
    # **会 panic 的内建必须声明出来**（L-05，2026-11-06）：
    # 以前 `EFFECTS` 里没有它们，于是 `effects { }` 的函数里调 `unwrap`
    # **一声不响**——而 panic 是能让程序中止的效果，藏起来等于契约在撒谎。
    # 现有样例与测试里这些调用出现 0 次（实测），所以这条加上去**零误报**。
    "panic": {"panic"},
    "unwrap": {"panic"},
    "expect": {"panic"},
    "assert": {"panic"},
    "assert_eq": {"panic"},
    "print": {"io"},
    "rand_u64": {"nondet"},
    "read_file": {"fs"},
    "read_stdin": {"io"},
    "write": {"io"},
    # **stderr 是另一条流**（2026-11-11）：`print` 固定写 stdout，
    # 语料里的 `std::cerr << …` 没有等价的"换一条流"写法。
    # 效果与 `write` 同一口径：`io`（不是 `fs`——没碰文件系统）。
    "eprint": {"io"},
    "eprintln": {"io"},
    "exit": {"io"},
    "args": {"env"},
    "env_get": {"env"},
    "cwd": {"env"},
    "open_read": {"fs"},
    "next_line": {"fs"},
    "close": {"fs"},
    "open_write": {"fs"},
    "write_chunk": {"fs"},
    "close_write": {"fs"},
    "fs_rename": {"fs"},
    "fs_remove": {"fs"},
    "env_set": {"env"},
    "write_file": {"fs"},
    "file_exists": {"fs"},
    "try_read_file": {"fs"},
    "append_file": {"fs"},
    "file_size": {"fs"},
}


def effects_of(name: str) -> set:
    return EFFECTS.get(name, set())


def is_stdlib(name: str) -> bool:
    return name in FNS or name in DEPRECATED_BUILTINS


def arity(name: str) -> int:
    return _entry(name)["arity"]


def is_varargs(name: str) -> bool:
    """这个内建的 `arity` 是**下限**（可变参数）还是精确值？"""
    return bool(_entry(name).get("varargs"))


def arity_ok(name: str, given: int) -> bool:
    """实参个数是否满足内建签名：变参只看下限，其余要求精确相等。"""
    want = arity(name)
    return given >= want if is_varargs(name) else given == want


def call(name: str, args: List[Any]) -> Any:
    return _entry(name)["impl"](args)


def ret_kind(name: str) -> str:
    """内建的**返回类型标记**（`ret=` 字段）。

    这个词表有**三个消费者**，缺一条消费分支就是静默降级：

    | 消费者 | 用途 |
    |---|---|
    | `interp._tag_stdlib_result` | 把值打上语言类型的标记（`u64` / `vector<u8>` → `Text`） |
    | `typecheck._builtin_ret_ty` | 推出调用表达式的 `Ty`（赋值 / `return` / 比较都靠它） |
    | `tools/gen_builtins_doc.py` | 生成 `文档/内建函数.md` 的「返回」列 |

    2026-11-10 之前 `vec_pop` / `map_get` / `open_read` 等 17 个内建写的是
    `ret="any"`，`typecheck` 只有一条 `any → UNKNOWN` 的分支——`UNKNOWN`
    与任何类型兼容，于是 `u64 x = vec_pop(h)` 静默通过，`x == 9` 静默为
    `false`（K5）。现在每个内建都登记**能推出具体类型**的词；`any` 只作为
    「确实推不出来」的兜底，类型检查器会把它当**不透明值**（不能赋给标量），
    不再静默放行。

    新增内建时：先在 `RET_KINDS` 里加词、在 `typecheck._builtin_ret_ty`
    里加对应的 `Ty`、在 `tools/gen_builtins_doc.py` 的 `RET_LABEL` 里加
    人读标签（三处齐全才算登记完）。
    """
    return _entry(name).get("ret", "u64")


#: `ret=` 的**全部取值**（`tests/cases/test_stdlib.py` 会断言没有表外的值）。
#:
#: 分三组：**标量**（`u64` / `i64`）、**容器**（`vec-*` / `map` / `set` /
#: `deque` / `args` / `split`）、**包装**（`Option` 一族、`Result` 一族）。
#: 包装类型是这次 K5 修复的重点：`Option` / `Result` **不能**直接当标量用，
#: 必须先 `match` / `option_unwrap` / `?` 解包。
RET_KINDS = frozenset({
    # ---- 标量
    "u64", "i64", "f64",
    # ---- `bool`：字符分类谓词（`isdigit` 一族）返回它。
    # 本语言的 `if` / `while` 条件**只收 `bool` 或指针**（`E-Type-Cond`），
    # C++ 那套"非零即真"在这里不存在；所以 `if (isdigit(c))` 要真的能写，
    # `is*` 就必须返回 `bool`，而不是 C 的 `int`。
    "bool",
    # ---- `arg0`：返回类型**跟第一个实参走**（`abs`：整数进整数出、f64 进 f64 出）
    "arg0",
    # ---- 容器 / 不透明容器
    "vec-u8", "vec-u64", "vec-vec-u8",
    "map", "set", "deque", "args", "split",
    # ---- stdlib 自己的哈希表（**与语言的 `map<K,V>` 不是同一个类型**，见 `map_new`）
    "hashmap",
    # ---- Result
    "result-u64", "result-vec-u8", "result-vec-u64",
    "result-reader", "result-writer", "result-json",
    # ---- Option
    "option-vec-u8", "option-elem", "option-arg0", "option-map-val",
    "option-unknown",
    # ---- Option 的**载荷**（`unwrap` / `unwrap_or` 的返回值，不是 Option）
    "option-payload",
    # ---- 兜底：类型推不出来。**不许用它偷懒**，但留一条安全出口
    "any",
})


#: 内建 → **分配次数**（`0` / `1` / `n`）。E36 用。
#: 未列出的内建默认 `0`（原地或纯计算）。
_ALLOC = {
    "open_read": "1",
    "next_line": "1",
    "close": "0",
    "open_write": "1",
    "write_chunk": "0",
    "close_write": "0",
    "vec_push": "1",
    "vec_pop": "0",
    "json_array": "1",
    "json_object": "1",
    "env_set": "0",
    "fs_rename": "0",
    "fs_remove": "0",
    "utf8_chars_vec": "1",
    "format": "1",
    "json_parse": "nodes",
    "text_split_vec": "1n",   # 外层 1 次 + 每行 1 次
    "text_join_vec": "1",
    "base64_encode": "1",
    "base64_decode": "1",
    "json_emit_str": "1",
    "json_emit_u64": "1",
    "json_emit_bool": "1",
    "text_concat": "1",
    "text_split": "1",
    "split_get": "1",
    "text_join": "1",
    "text_replace": "1",
    "utf8_encode": "1",
    "map_new": "1",
    # **每个新元素算一次分配**（2026-11-11）：以前 `map_put` / `set_add` 没登记
    # ⇒ 分配上界 0，而真插入一个元素至少要一个槽位/节点（HashMap 还会 rehash）。
    # 1 次/调用是**上界**（覆盖 rehash 那次 O(n) 里摊到每一次的份）。
    "map_put": "1",
    "set_new": "1",
    "set_add": "1",
    "deque_new": "1",
    "copy": "1",           # 返回新 Vec（E36）
    "slice": "1",          # 原地截断（内部复制一份），返回新长度
    "copy_slice": "1",
    "args": "1",
    "args_get": "1",
    "env_get": "1",
    "cwd": "1",
    "write": "0",
    "format_u64": "1",
    "text_trim": "1",
    "text_to_lower": "1",
    "read_file": "1",
    "try_read_file": "1",
    "read_stdin": "1",
}


#: **失败语义**（决策文档：`文档/设计/目前设计/决策-失败语义.md`）
#:
#: 五个取值，**每个内建必须选一个**（`test_failure_modes.py` 会查）：
#:   - `never`     不会失败
#:   - `sentinel`  用特殊返回值表示（**必须写明哨兵值**，且值域里不能出现）
#:   - `option`    返回 `Option`（`Some`/`None`）
#:   - `err`       返回 `Result` 的 `Err(枚举::变体)`
#:   - `abort`     直接中止（**只允许用在"I/O 失败即无可继续"的地方**）
#:
#: 哨兵值写在 `SENTINEL` 里；`err` 的枚举写在 `ERR_ENUM` 里。
FAIL_MODE: Dict[str, str] = {
    # ---- never
    **{k: "never" for k in (
        "min", "max", "print", "sort", "sort_by", "reverse", "fill", "dedup",
        "contains", "count_of", "is_sorted", "sum_of", "rotate", "fill_range",
        "hmap_put", "hmap_has", "rand_u64", "format_u64", "utf8_len", "text_trim",
        "text_to_lower", "utf8_encode", "utf8_codepoint_at", "text_split",
        "split_len", "text_join", "text_replace", "base64_encode", "json_emit_str",
        "json_emit_u64", "json_emit_bool", "text_concat", "copy", "map_new",
        "map_put", "map_has", "map_len", "set_new", "set_add", "set_has",
        "set_len", "deque_new", "deque_push_back", "deque_push_front", "deque_len",
        "utf8_char_at", "utf8_byte_of_char", "file_exists", "slice",
        "args", "args_len", "cwd", "exit", "option_some", "option_none", "option_is_some",
        "text_split_vec", "text_join_vec", "close", "close_write", "write_chunk",
        "env_set", "json_array", "json_object", "utf8_chars_vec", "vec_push", "option_unwrap_or", "option_unwrap",
        "json_parse",
        "format",
        # ---- 数学（全部 O(1)、不分配、不抛异常）
        "sqrt", "floor", "ceil", "round", "fabs", "abs", "log2",
        "popcount", "clz", "ctz",
        # ---- 字符分类（ASCII 语义，全部 O(1)、不分配、不抛异常）
        "isdigit", "isspace", "isalpha", "isalnum",
        "toupper", "tolower", "isupper", "islower",
    )},
    # ---- never（stderr 输出）：与 `print` 同一口径——I/O 失败在宿主层中止，
    # 但语言层面**不把它建模成 `Result`/`Option`**，所以不是 `abort` 模式。
    "eprint": "never",
    "eprintln": "never",
    # ---- sentinel
    "hmap_get": "sentinel",
    "index_of": "sentinel",
    "text_find": "sentinel",
    "binary_search": "sentinel",
    "split_get": "sentinel",
    "copy_slice": "err",
    # ---- option
    "map_get": "option",
    "env_get": "option",
    "next_line": "option",
    "vec_pop": "option",
    "args_get": "sentinel",
    "deque_pop_front": "option",
    "deque_pop_back": "option",
    # ---- err
    "parse_u64": "err",
    "parse_hex": "err",
    "file_size": "err",
    "open_read": "err",
    "open_write": "err",
    "fs_rename": "err",
    "fs_remove": "err",
    "json_parse": "err",
    "try_read_file": "err",
    "base64_decode": "err",
    # ---- abort
    "read_file": "abort",
    "read_stdin": "abort",
    "write_file": "abort",
    "append_file": "abort",
    "write": "abort",
    "write_chunk": "abort",
}

#: 哨兵值（`sentinel` 模式必须有）。
SENTINEL: Dict[str, str] = {
    "hmap_get": "-1（i64）",
    "index_of": "len(xs)（等于长度＝没找到）",
    "text_find": "-1（i64）",
    "binary_search": "-1（i64）",
    "split_get": "空 `Vec<u8>`（越界）",
    "args_get": "空 `Vec<u8>`（越界）",
}

#: `err` 模式对应的枚举。
ERR_ENUM: Dict[str, str] = {
    "open_read": "Io::NotFound / Io::Denied / Io::Failed",
    "open_write": "Io::Denied / Io::Failed",
    "fs_rename": "Io::NotFound / Io::Denied / Io::Failed",
    "fs_remove": "Io::NotFound / Io::Denied / Io::Failed",
    "json_parse": "Json::BadToken / UnexpectedEof / Trailing / BadEscape / Unsupported",
    "copy_slice": "Range::OutOfBounds",
    "parse_u64": "Parse::BadNumber / Parse::Overflow",
    "parse_hex": "Parse::BadNumber / Parse::Overflow",
    "file_size": "Io::NotFound / Io::Failed",
    "try_read_file": "Io::NotFound / Io::Denied / Io::Failed",
    "base64_decode": "Parse::BadNumber",
}


#: **不在这张注册表里、但语言自带的**（由解释器/成本器直接实现）。
#: 它们同样要声明失败语义——**"不在表里"不等于"不用交代"**。
NATIVE: Dict[str, str] = {
    "min": "never",
    "max": "never",
    "print": "never",          # I/O 失败按 abort 处理，但语言层面不暴露
    "sort": "never",
    "sort_by": "never",
    "reverse": "never",
    "fill": "never",
    "map": "never",
    "filter": "never",
    "fold": "never",
    "count_if": "never",
}


def failure_mode(name: str) -> str:
    """内建的失败语义；未登记返回空串（测试会把它当错）。"""
    n = name or ""
    return FAIL_MODE.get(n) or NATIVE.get(n, "")


def _entry(name: str) -> Dict[str, Any]:
    """统一查表：现役 + 已弃用（弃用的仍能跑，只是要给警告）。"""
    if name in FNS:
        return FNS[name]
    return DEPRECATED_BUILTINS[name]


def is_deprecated(name: str) -> bool:
    return name in DEPRECATED_BUILTINS


def replacement_for(name: str) -> str:
    return DEPRECATED_BUILTINS.get(name, {}).get("replacement", "")


def all_builtins() -> list:
    """注册表里的 + 解释器直接实现的（测试用它查"有没有漏"。）"""
    return sorted(set(FNS) | set(NATIVE) | set(DEPRECATED_BUILTINS))


def alloc_kind(name: str) -> str:
    """内建的分配次数：`0` / `1` / `n`。"""
    fn = FNS.get(name or "") or DEPRECATED_BUILTINS.get(name or "")
    if fn is None:
        return "0"
    return _ALLOC.get(name or "", "0")


def cost_kind(name: str) -> str:
    return _entry(name)["cost"]


def n_of(xs: Any) -> int:
    """给成本分析算规模用。"""
    return _n_of(xs)

_register("rand_u64", 0, _rand_u64, "1", empty="—")

# ------------------------------------------------------------------ L2：读文件 / 读标准输入
#
# **成本口径（规格附录 B 规则 2）**：I/O 的时间成本**静态不可知**，
# 所以这两个内建的成本是 **⊤（未知）**——调用它们的函数 `elench cost` 会判"无法判定"。
# 想让它可判，需要 `external { read_file: O(1); }` 登记，**该登记机制尚未实现**。
#
# **错误模型（v0.71 简化）**：I/O 失败直接中止（`InterpError`），
# 不做 `Result`——`Result` 版本留到 L2 第二轮（要先有内置错误枚举）。


def _decode_path(raw: Any) -> str:
    """`Vec<u8>`（或字符串字面量）→ 文件系统路径。"""
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8")
    if isinstance(raw, list):
        return bytes(int(x) & 0xFF for x in raw).decode("utf-8")
    return str(raw)


def _read_file(args: List[Any]) -> list:
    """读整个文件 → `Vec<u8>`。失败直接抛。"""
    from interp import InterpError
    path = _decode_path(args[0])
    t0 = time.perf_counter()
    try:
        with open(path, "rb") as f:
            data = list(f.read())
    except OSError as exc:
        raise InterpError(f"read_file({path!r}) 失败：{exc}")
    _stats_add("read_file", time.perf_counter() - t0)
    return data


def _read_stdin(args: List[Any]) -> list:
    """读全部标准输入 → `Vec<u8>`。"""
    import sys as _sys
    t0 = time.perf_counter()
    data = _sys.stdin.buffer.read() if hasattr(_sys.stdin, "buffer") else _sys.stdin.read().encode()
    _stats_add("read_stdin", time.perf_counter() - t0)
    return list(data)


_register("read_file", 1, _read_file, "ext", ret="vec-u8",
          empty="**读整个文件**成 `Vec<u8>`（本语言里 `Vec<u8>` 就是文本，"
                "按 UTF-8 解释）；失败 → 直接中止")
_register("read_stdin", 0, _read_stdin, "ext", ret="vec-u8", empty="—")


def _write_file(args: List[Any]) -> int:
    """把 `Vec<u8>` 写进文件（覆盖），返回写入字节数。失败直接抛。"""
    from interp import InterpError
    path = _decode_path(args[0])
    data = args[1]
    raw = bytes(int(x) & 0xFF for x in data) if isinstance(data, list) else bytes(data)
    t0 = time.perf_counter()
    try:
        with open(path, "wb") as f:
            f.write(raw)
    except OSError as exc:
        raise InterpError(f"write_file({path!r}) 失败：{exc}")
    _stats_add("write_file", time.perf_counter() - t0)
    return len(raw)


_register("write_file", 2, _write_file, "ext",
          empty="覆盖写整个文件（文本就是 `Vec<u8>`）；失败 → 直接中止；返回写入字节数")


def _file_exists(args: List[Any]) -> int:
    """文件存在吗 → 1/0（用于"读失败就换条路"的分支）。"""
    return 1 if os.path.exists(_decode_path(args[0])) else 0


def _try_read_file(args: List[Any]) -> Any:
    """`try_read_file(path) -> Vec<u8>!`：**失败是值，不是中止**。

    `Err(Io::NotFound)` / `Err(Io::Denied)` / `Err(Io::Failed)`——
    `Io` 是**内置枚举**，用户不必声明。
    """
    from interp import EnumVal, Err
    path = _decode_path(args[0])
    t0 = time.perf_counter()
    try:
        with open(path, "rb") as f:
            data = list(f.read())
    except FileNotFoundError:
        _stats_add("try_read_file", time.perf_counter() - t0)
        return Err(EnumVal("Io", "NotFound"))
    except PermissionError:
        _stats_add("try_read_file", time.perf_counter() - t0)
        return Err(EnumVal("Io", "Denied"))
    except OSError:
        _stats_add("try_read_file", time.perf_counter() - t0)
        return Err(EnumVal("Io", "Failed"))
    _stats_add("try_read_file", time.perf_counter() - t0)
    from interp import Ok
    return Ok(data)


_register("file_exists", 1, _file_exists, "ext", empty="—")
_register("try_read_file", 1, _try_read_file, "ext", ret="result-vec-u8",
          empty="**失败是值**：`Err(Io::*)`；成功 → `Ok(Vec<u8>)`（文本）")


# ------------------------------------------------------------------ L3：文本与数字
#
# 这一层**没有效果**（纯函数），成本全部可静态定界。
# 文本在本语言里就是 `Vec<u8>`；UTF-8 只在需要"字符数"时才解释。

def _parse_u64(args: List[Any]) -> Any:
    """`parse_u64(data) -> u64!`：十进制解析；空/非法 → `Err(Parse::BadNumber)`，
    溢出 → `Err(Parse::Overflow)`。"""
    from interp import Err, Ok, EnumVal
    raw = args[0]
    data = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    s = data.decode("ascii", "replace").strip()
    if not s or not s.isdigit():
        return Err(EnumVal("Parse", "BadNumber"))
    try:
        v = int(s)
    except ValueError:
        return Err(EnumVal("Parse", "BadNumber"))
    if v > 0xFFFFFFFFFFFFFFFF:
        return Err(EnumVal("Parse", "Overflow"))
    return Ok(v)


def _format_u64(args: List[Any]) -> list:
    """`format_u64(n) -> Vec<u8>`：十进制文本（**长度 ≤ 20**，所以成本是常数上界）。"""
    return list(str(int(args[0]) & 0xFFFFFFFFFFFFFFFF).encode("ascii"))


def _utf8_len(args: List[Any]) -> int:
    """`utf8_len(data) -> u64`：**字符数**（不是字节数）。非法序列按 U+FFFD 计。"""
    raw = args[0]
    data = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    return len(data.decode("utf-8", "replace"))


def _text_find(args: List[Any]) -> int:
    """`text_find(hay, needle) -> i64`：首次出现的字节下标；**没有则 -1**。"""
    hay, nee = args[0], args[1]
    h = bytes(int(x) & 0xFF for x in hay) if isinstance(hay, list) else bytes(hay)
    n = bytes(int(x) & 0xFF for x in nee) if isinstance(nee, list) else bytes(nee)
    return h.find(n)


def _text_trim(args: List[Any]) -> list:
    """`text_trim(data) -> Vec<u8>`：去掉两端空白。"""
    raw = args[0]
    data = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    return list(data.strip())


def _text_to_lower(args: List[Any]) -> list:
    """`text_to_lower(data) -> Vec<u8>`：**ASCII 小写**（不做 Unicode 大小写折叠）。"""
    raw = args[0]
    data = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    return list(bytes(c + 32 if 65 <= c <= 90 else c for c in data))


_register("parse_u64", 1, _parse_u64, "n", ret="result-u64", empty="空/非法 → Err(Parse::BadNumber)")
_register("format_u64", 1, _format_u64, "1", ret="vec-u8", empty="—")
_register("utf8_len", 1, _utf8_len, "n", empty="空 → 0")
_register("text_find", 2, _text_find, "nm", ret="i64", empty="未找到 → -1")
_register("text_trim", 1, _text_trim, "n", ret="vec-u8", empty="空 → 空")
_register("text_to_lower", 1, _text_to_lower, "n", ret="vec-u8", empty="空 → 空")


def _append_file(args: List[Any]) -> int:
    """追加写入，返回**写入字节数**。"""
    from interp import InterpError
    path = _decode_path(args[0])
    data = args[1]
    raw = bytes(int(x) & 0xFF for x in data) if isinstance(data, list) else bytes(data)
    t0 = time.perf_counter()
    try:
        with open(path, "ab") as f:
            f.write(raw)
    except OSError as exc:
        raise InterpError(f"append_file({path!r}) 失败：{exc}")
    _stats_add("append_file", time.perf_counter() - t0)
    return len(raw)


def _file_size(args: List[Any]) -> Any:
    """`file_size(path) -> u64!`：字节数；不存在 → `Err(Io::NotFound)`。"""
    from interp import Err, Ok, EnumVal
    path = _decode_path(args[0])
    t0 = time.perf_counter()
    try:
        n = os.path.getsize(path)
    except FileNotFoundError:
        _stats_add("file_size", time.perf_counter() - t0)
        return Err(EnumVal("Io", "NotFound"))
    except OSError:
        _stats_add("file_size", time.perf_counter() - t0)
        return Err(EnumVal("Io", "Failed"))
    _stats_add("file_size", time.perf_counter() - t0)
    return Ok(n)


def _parse_hex(args: List[Any]) -> Any:
    """`parse_hex(data) -> u64!`：十六进制解析（可带 0x 前缀）。"""
    from interp import Err, Ok, EnumVal
    raw = args[0]
    data = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    s = data.decode("ascii", "replace").strip()
    if s[:2].lower() == "0x":
        s = s[2:]
    if not s:
        return Err(EnumVal("Parse", "BadNumber"))
    try:
        v = int(s, 16)
    except ValueError:
        return Err(EnumVal("Parse", "BadNumber"))
    if v > 0xFFFFFFFFFFFFFFFF:
        return Err(EnumVal("Parse", "Overflow"))
    return Ok(v)


_register("append_file", 2, _append_file, "ext", empty="—")
_register("file_size", 1, _file_size, "ext", ret="result-u64", empty="不存在 → Err(Io::NotFound)")
_register("parse_hex", 1, _parse_hex, "n", ret="result-u64", empty="空/非法 → Err(Parse::BadNumber)")


def _copy(args: List[Any]) -> list:
    """`copy(xs) -> Vec<u64>`：返回**新** Vec（分配 1 次）。"""
    return list(args[0]) if isinstance(args[0], list) else []


_register_deprecated("copy", 1, _copy, "n", ret="vec-u64", empty="空 → 空")


def _copy_slice(args: List[Any]) -> Any:
    """`copy_slice(xs, lo, hi) -> Vec<u64>!`：**复制**一段，越界是**错误**不是夹取。

    `lo`/`hi` 必须在 `[0, len]` 内且 `lo <= hi`，否则 `Err(Range::OutOfBounds)`。
    与 `slice` 的区别：**`slice` 原地截断且静默夹取**（已知脚枪），
    这个**不改原向量、也不静默**。
    """
    from interp import Err, Ok, EnumVal
    xs = args[0]
    lo, hi = int(args[1]), int(args[2])
    n = len(xs)
    if lo < 0 or hi < lo or hi > n:
        return Err(EnumVal("Range", "OutOfBounds"))
    return Ok(list(xs[lo:hi]))


_register("copy_slice", 3, _copy_slice, "n", ret="result-vec-u64", empty="越界 → Err(Range::OutOfBounds)")


# ------------------------------------------------------------------ L2 收尾：环境与标准输出
#
# **`args` 没有嵌套泛型**（`Vec<Vec<u8>>` 还没做），所以照 `Split` 的样子给容器：
# `args_len` / `args_get`。
#
# **`env_get` 按决策文档用 `option`**：不存在就是 `Option::None`——
# 空串是**合法值**（环境变量可以是空的），用空串当"没有"会撞。

class ArgsVal:
    """命令行参数（每个元素是 `Vec<u8>`）。"""

    __slots__ = ("items",)

    def __init__(self, items):
        self.items = items


#: 程序参数（由 CLI 显式设置；`args()` 只读这里，**不去翻 sys.argv**——
#: 否则 `dsh run` 自己的命令行参数会漏进去）。
_PROG_ARGS: List[str] = []


def set_prog_args(items) -> None:
    global _PROG_ARGS
    _PROG_ARGS = list(items or [])


def _args(args: List[Any]) -> ArgsVal:
    """`args() -> Args`：程序参数（不含程序名）。"""
    import sys as _sys
    return ArgsVal([list(a.encode("utf-8")) for a in _PROG_ARGS])


def _args_len(args: List[Any]) -> int:
    return len(args[0].items)


def _args_get(args: List[Any]) -> list:
    """取第 i 个参数（**返回新 Vec**，分配 1 次）；越界 → 空。"""
    it = args[0].items
    i = int(args[1])
    return list(it[i]) if 0 <= i < len(it) else []


def _env_get(args: List[Any]) -> Any:
    """`env_get(name) -> Option<Vec<u8>>`：不存在 → `Option::None`（**不是空串**）。"""
    from interp import EnumVal
    raw = args[0]
    name = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    try:
        val = os.environ.get(name.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return none()
    if val is None:
        return none()
    return some(list(val.encode("utf-8")))


def _cwd(args: List[Any]) -> list:
    """`cwd() -> Vec<u8>`：当前工作目录。"""
    return list(os.getcwd().encode("utf-8"))


def _exit(args: List[Any]) -> Any:
    """`exit(code)`：**立即以 code 结束程序**（不再执行后面的语句）。

    语义上有两件事要说清（已写进文档）：
      · 它**不是** `Result` 失败，是**控制流**——所以后面就算有 `?` / `match` 也不会执行；
      · 它对 **成本上界没有影响**：`κ = max(exit 之前的成本, exit 之后的成本)` 仍然成立，
        因为"不被执行"的代码本来就只贡献上界不影响承诺。
    """
    from interp import ExitRequest
    raise ExitRequest(int(args[0]))


def _write(args: List[Any]) -> int:
    """`write(data) -> u64`：写 stdout（**不加换行**），返回写入字节数。"""
    import sys as _sys
    from interp import InterpError
    raw = args[0]
    data = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    try:
        buf = getattr(_sys.stdout, "buffer", None)
        if buf is not None:
            buf.write(data)
            buf.flush()
        else:
            _sys.stdout.write(data.decode("utf-8", "replace"))
            _sys.stdout.flush()
    except (BrokenPipeError, OSError) as exc:
        raise InterpError(f"输出失败（{type(exc).__name__}）：{exc}") from None
    return len(data)


# ------------------------------------------------------------------ stderr 输出（2026-11-11）
#
# **缺口来自语料**：`demo/corpus/00_docnums.cpp` 的 3 处 `std::cerr << … << "\n"`
# 在语言里无处可写——`print` 固定写 stdout，没有"第二条流"。
#
# **为什么叫 eprint / eprintln 而不是 cerr**：本语言没有流对象，`cerr` 那种
# `<<` 链式写入也不存在；`eprint(…, …)` 直接收多个实参，等价于
# `cerr << a << b << …`（`<<` 是**无分隔符拼接**，所以这里也不加分隔符——
# `print` 用空格，两者刻意不同，文档与测试都钉住这条）。
#
# **不做 `{}` 插值**：插值是 `format` 的职责（`print` 的插值只是历史行为）。
# 要插值就写 `eprintln(format("…{}…", x))`——一个名字只做一件事，
# 也避免了"`eprintln` 的洞数没人校验"这类静默错。
#
# **成本口径与 `print` / `write` 一致**：记 1 次调用、不建模 IO 延迟；
# 效果是 `{ io }`（不是 `{ fs }`——不碰文件系统）。


def _stderr_write(text: str) -> int:
    """把文本写到 stderr，返回写入的**字节数**（UTF-8）。

    **走 `.buffer` 而不是文本层**：文本层会把 `\\n` 按平台转成 CRLF，
    而 `eprintln` 承诺的是**一个 LF**（与 C++ `"\\n"` 一致）。
    宿主没有 `.buffer`（测试里的 `StringIO` 捕获）时退回文本层。
    """
    import sys as _sys
    from interp import InterpError
    data = text.encode("utf-8")
    try:
        buf = getattr(_sys.stderr, "buffer", None)
        if buf is not None:
            buf.write(data)
            buf.flush()
        else:
            _sys.stderr.write(text)
            _sys.stderr.flush()
    except (BrokenPipeError, OSError) as exc:
        raise InterpError(f"写 stderr 失败（{type(exc).__name__}）：{exc}") from None
    return len(data)


def _echo(args: List[Any], newline: bool) -> int:
    """`eprint` / `eprintln` 的共同实现：`<<` 式**无分隔符**拼接 + 可选 LF。

    **渲染规则复用 `interp._text_of`**（`print` / 格式串 / `format` 的唯一一套）：
    `vector<u8>`（`Text`）按 UTF-8 文本、其它容器按空格分隔数字、标量按十进制。
    非法 UTF-8 字节按 U+FFFD 输出（`_text_of` 的 `replace` 口径）——
    **要原样字节请用 `write`（stdout）**，别指望 `eprint` 当字节管道。
    """
    from interp import _text_of
    text = "".join(_text_of(a) for a in args)
    if newline:
        text += "\n"
    return _stderr_write(text)


_register("args", 0, _args, "1", ret="args", empty="—")
_register("args_len", 1, _args_len, "1", empty="—")
_register("args_get", 2, _args_get, "1", ret="vec-u8", empty="越界 → 空")
_register("env_get", 1, _env_get, "1", ret="option-vec-u8",
          empty="不存在 → Option::None")


def some(v):
    """`Option::Some(v)`（统一构造，避免各处自己拼 `EnumVal`）。"""
    from interp import EnumVal
    return EnumVal("Option", "Some", v)


def none():
    """`Option::None`。"""
    from interp import EnumVal
    return EnumVal("Option", "None")


_register("option_some", 1, lambda a: some(a[0]), "1", ret="option-arg0", empty="—")
_register("option_none", 0, lambda a: none(), "1", ret="option-unknown", empty="—")
_register("option_is_some", 1, lambda a: 1 if getattr(a[0], "variant", "") == "Some" else 0,
          "1", empty="Some → 1")
_register("cwd", 0, _cwd, "1", ret="vec-u8", empty="—")
_register("exit", 1, _exit, "1", empty="立即结束程序")
_register("write", 1, _write, "1", empty="写 stdout（不换行）")
# `arity=0` + `varargs=True`：**零个实参也合法**（`eprintln()` 就是一个空行，
# 对应 C++ 的 `cerr << "\\n"`）；`arity` 对变长内建只是**下限**。
_register("eprint", 0, lambda a: _echo(a, False), "1", varargs=True,
          empty="写 stderr（不换行）；实参**无分隔符**拼接、**不做 `{}` 插值**；"
                "返回写入字节数（UTF-8）")
_register("eprintln", 0, lambda a: _echo(a, True), "1", varargs=True,
          empty="写 stderr + **一个 LF**；其余同 `eprint`")


# ------------------------------------------------------------------ C：HashMap<K,V>
#
# **和 `hmap_*` 一样的口径**：哈希 + **有界 8 次探测**，所以 `O(1)` 是**真上界**
# （不是"平均 O(1)"）。用 Python dict 会得到一个**最坏无界**的结构，那就是撒谎。
#
# 值语义：`HashMap` 是**可变对象**（放进 env 的是引用），`map_put` 直接改它。
# 这打破了"值语义"的直觉，所以文档里写清：**它是引用类型**。

_PROBES = 8


class MapVal:
    """开放寻址 + 有界探测的哈希表（键：`Vec<u8>` / 整数 / 其它容器）。

    哈希统一走 `hash_key()`：容器键 **O(len(key))**，标量键 O(1)。
    """

    __slots__ = ("cap", "ks", "vs", "n")

    def __init__(self, cap: int = 16):
        self.cap = 16
        while self.cap < cap:
            self.cap *= 2
        self.ks = [None] * self.cap
        self.vs = [None] * self.cap
        self.n = 0

    def _h(self, key) -> int:
        return hash_key(key)

    def _slot(self, key):
        """返回 (找到的下标) 或 (可插入的下标) 或 None（8 次探测满）。"""
        i = self._h(key) & (self.cap - 1)
        for _ in range(_PROBES):
            k = self.ks[i]
            if k is None:
                return ("empty", i)
            if k == key:
                return ("found", i)
            i = (i + 1) & (self.cap - 1)
        return None

    def _rehash(self) -> None:
        """扩容一倍并重排（**摊还 O(1)** 的那一次 O(n)）。"""
        old_ks, old_vs = self.ks, self.vs
        self.cap *= 2
        self.ks = [None] * self.cap
        self.vs = [None] * self.cap
        self.n = 0
        for k, v in zip(old_ks, old_vs):
            if k is not None:
                self.put(k, v)

    def put(self, key, val) -> int:
        """插入/覆盖。**1 = 新插入，0 = 覆盖已有键**（不是"失败"）。

        探测满（8 次都占着）→ **扩容后重试**，而不是放弃插入：
        **静默丢数据比慢更糟**。代价是扩容那一次 O(n)，所以口径是**摊还 O(1)**，
        与 `Deque` 一致的诚实写法（C++ 的 `unordered_map` 也是这样）。
        """
        got = self._slot(key)
        if got is None:
            self._rehash()
            got = self._slot(key)
            if got is None:                # 极端情况：重排后仍探测满 → 再扩一次
                self._rehash()
                got = self._slot(key)
            if got is None:
                return 0
        kind, i = got
        if kind == "empty":
            self.n += 1
            self.ks[i] = key
            self.vs[i] = val
            return 1
        self.vs[i] = val                   # 覆盖
        return 0

    def get(self, key):
        got = self._slot(key)
        if got is None or got[0] != "found":
            return None
        return self.vs[got[1]]

    def has(self, key) -> int:
        got = self._slot(key)
        return 1 if (got is not None and got[0] == "found") else 0

    def items(self):
        """**遍历用**：返回 [(key, value)]。

        **顺序不承诺**（哈希表的内部排列），但**同一次运行内是稳定的**。
        调用方要复制成列表再用——**遍历中修改容器不会影响这一份快照**。
        """
        out = []
        for i in range(self.cap):
            if self.ks[i] is not None:
                out.append((self.ks[i], self.vs[i]))
        return out


def _map_new(args: List[Any]) -> MapVal:
    return MapVal()


#: `map_*` 自由函数**只接受 stdlib 的 HashMap** 的统一说明。
#:
#: 为什么不把它们扩到语言的 `map<K,V>`：**成本口径不同**。HashMap 是
#: 「哈希 + 有界 8 次探测」⇒ `O(1)` 是**真上界**；语言的 `map` 是平衡二叉树 ⇒
#: `O(log n)`。把两者塞进同一个 `cost="1"` 的登记里，`map_put(m, k, v)` 的 κ
#: 就会**低估**（少算比多算危险）。所以：自由函数只给 HashMap，
#: 语言的 map 用方法式 API（`m.insert` / `m.get` / `m.contains` / `m.size`），
#: 那条路走容器方法的 `log2(n)` 记账。
_MAP_FREE_FN_HINT = (
    "第一个实参不是 stdlib 的 `HashMap`（`map_new()` 的返回类型）。"
    "语言里的 `map<K,V>` / `unordered_map` 用方法式 API："
    "`m.insert(k, v)` / `m.get(k)` / `m.contains(k)` / `m.size()`"
    "（语言的 `map` 是 O(log n)，`map_put` 登记的是 O(1) 有界探测——"
    "两者不能共用一个成本登记，否则 κ 会低估）")


def _map_put(args: List[Any]) -> int:
    m = args[0]
    if not isinstance(m, MapVal):
        raise TypeError("map_put 的" + _MAP_FREE_FN_HINT)
    return m.put(args[1], args[2])


def _map_get(args: List[Any]) -> Any:
    from interp import EnumVal
    m = args[0]
    if not isinstance(m, MapVal):
        raise TypeError("map_get 的" + _MAP_FREE_FN_HINT)
    got = m.get(args[1])
    # 未命中返回 `Option::None`（不用 -1 哨兵：值域任意，-1 会和真实值撞）。
    return some(got) if got is not None else none()


def _map_has(args: List[Any]) -> int:
    m = args[0]
    if not isinstance(m, MapVal):
        raise TypeError("map_has 的" + _MAP_FREE_FN_HINT)
    return m.has(args[1])


def _map_len(args: List[Any]) -> int:
    m = args[0]
    if not isinstance(m, MapVal):
        raise TypeError("map_len 的" + _MAP_FREE_FN_HINT)
    return m.n


_register("map_new", 0, _map_new, "1", ret="hashmap",
          empty="—（`HashMap` 是**引用类型**：`map_put` 原地改它）")
# **成本登记跟着实现走**（2026-11-11，κ 低估审计 ②）：这五个是**每次一个
# 元素**的读写，登记 `"log"`（`log2(n(容器))`）而不是常数 `"1"`。
#
# 为什么不是 `"1"`：`MapVal.put` 在 8 次探测耗尽时会 `_rehash`（扫全表）；
# 单次最坏 O(n)、摊还 O(1)。按**摊还**记 `"1"` 曾经是安全的，但它与语言
# `map` 的 `insert`（`log2(n)`）走的是**同名不同阶**的两套口径——审计 ②
# 要的是「同一类操作同一个形状」，`log2` 是现成节点（`map.insert` 已在用），
# 且 n ≥ 2 时 `log2(n) ≥ 1`，**只会把界放大、不会变成低估**。
_register("map_put", 3, _map_put, "log",
          empty="插入成功 1 / 覆盖 0；**只接受 `HashMap`**（语言的 `map` 用 `m.insert`）")
_register("map_get", 2, _map_get, "log", ret="option-map-val",
          empty="未命中 → Option::None；**只接受 `HashMap`**（语言的 `map` 用 `m.get`）")
_register("map_has", 2, _map_has, "log",
          empty="未命中 → 0；**只接受 `HashMap`**（语言的 `map` 用 `m.contains`）")
_register("map_len", 1, _map_len, "1",
          empty="空 → 0；**只接受 `HashMap`**（语言的 `map` 用 `m.size()`）")


# ------------------------------------------------------------------ Set / Deque
#
# **接口照 C++ 的 std::unordered_set / std::deque 抄**（名字都对齐），
# 但**实现口径不放宽**：Set 复用 MapVal 的**有界 8 次探测**（所以 `O(1)` 是真上界）；
# Deque 是**环形缓冲**，两端 push/pop 都是 `O(1)` **摊还**（扩容那一次不算）。

class DequeVal:
    """环形缓冲：`std::deque` 的接口（push_front/back、pop_front/back）。"""

    __slots__ = ("buf", "cap", "head", "n")

    def __init__(self, cap: int = 8):
        self.cap = cap
        self.buf = [None] * cap
        self.head = 0
        self.n = 0

    def _grow(self) -> None:
        self.cap *= 2
        self.buf = [self.buf[(self.head + i) % (self.cap // 2)] for i in range(self.n)] + \
                   [None] * (self.cap - self.n)
        self.head = 0

    def _at(self, i: int):
        return self.buf[(self.head + i) % self.cap]

    def push_back(self, v) -> int:
        if self.n == self.cap:
            self._grow()
        self.buf[(self.head + self.n) % self.cap] = v
        self.n += 1
        return 1

    def push_front(self, v) -> int:
        if self.n == self.cap:
            self._grow()
        self.head = (self.head - 1) % self.cap
        self.buf[self.head] = v
        self.n += 1
        return 1

    def pop_front(self):
        if self.n == 0:
            return None
        v = self.buf[self.head]
        self.head = (self.head + 1) % self.cap
        self.n -= 1
        return v

    def pop_back(self):
        if self.n == 0:
            return None
        i = (self.head + self.n - 1) % self.cap
        v = self.buf[i]
        self.n -= 1
        return v


def _set_new(args: List[Any]) -> MapVal:
    """`Set<T>` 就是一个"值恒为 1"的 HashMap —— 复用同一套有界探测。"""
    return MapVal()


def _set_add(args: List[Any]) -> int:
    m = args[0]
    if not isinstance(m, MapVal):
        raise TypeError("set_add 的第一个实参不是 Set")
    return m.put(args[1], 1)


def _set_has(args: List[Any]) -> int:
    return args[0].has(args[1])


def _set_len(args: List[Any]) -> int:
    return args[0].n


def _deque_new(args: List[Any]) -> DequeVal:
    return DequeVal()


def _deque_push_back(args: List[Any]) -> int:
    return args[0].push_back(args[1])


def _deque_push_front(args: List[Any]) -> int:
    return args[0].push_front(args[1])


def _deque_pop_front(args: List[Any]) -> Any:
    v = args[0].pop_front()
    return some(v) if v is not None else none()


def _deque_pop_back(args: List[Any]) -> Any:
    v = args[0].pop_back()
    return some(v) if v is not None else none()


def _deque_len(args: List[Any]) -> int:
    return args[0].n


_register("set_new", 0, _set_new, "1", ret="set", empty="—")
# 与 `map_put` 同一口径（2026-11-11）：每次写一个元素 → `log2(n(s))`。
_register("set_add", 2, _set_add, "log", empty="插入成功 1 / 探测满 0")
_register("set_has", 2, _set_has, "log", empty="未命中 → 0")
_register("set_len", 1, _set_len, "1", empty="空 → 0")
_register("deque_new", 0, _deque_new, "1", ret="deque", empty="—")
_register("deque_push_back", 2, _deque_push_back, "1", empty="—")
_register("deque_push_front", 2, _deque_push_front, "1", empty="—")
_register("deque_pop_front", 1, _deque_pop_front, "1", ret="option-elem",
          empty="空 → Option::None")
_register("deque_pop_back", 1, _deque_pop_back, "1", ret="option-elem",
          empty="空 → Option::None")
_register("deque_len", 1, _deque_len, "1", empty="空 → 0")


# ------------------------------------------------------------------ L3 续：切分 / 拼接 / 替换 / UTF-8 码点
#
# **为什么 `text_split` 返回一个"容器"而不是 `Vec<Vec<u8>>`**：
# 嵌套泛型类型（`Vec<Vec<u8>>`）还没做，但"切分后逐段处理"是真实需求——
# 所以先给一个**不透明容器**：`split_len` / `split_get`，和 HashMap/Deque 一个风格。

class SplitVal:
    """按分隔符切出来的段（每段是 `Vec<u8>`）。"""

    __slots__ = ("parts",)

    def __init__(self, parts):
        self.parts = parts


def _text_split(args: List[Any]) -> SplitVal:
    """`text_split(data, sep) -> Split`：`sep` 为空 → 按字节切。"""
    raw, sep = args[0], args[1]
    d = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    s = bytes(int(x) & 0xFF for x in sep) if isinstance(sep, list) else bytes(sep)
    if s:
        parts = d.split(s)
    else:
        parts = [bytes([b]) for b in d]
    return SplitVal([list(x) for x in parts])


def _split_len(args: List[Any]) -> int:
    return len(args[0].parts)


def _split_get(args: List[Any]) -> list:
    """取第 i 段（**返回新 Vec**，分配 1 次）。越界 → 空。"""
    parts = args[0].parts
    i = int(args[1])
    return list(parts[i]) if 0 <= i < len(parts) else []


def _text_join(args: List[Any]) -> list:
    """`text_join(split, sep) -> Vec<u8>`：把段用 `sep` 拼回去。"""
    parts = args[0].parts
    sep = args[1]
    s = bytes(int(x) & 0xFF for x in sep) if isinstance(sep, list) else bytes(sep)
    return list(s.join(bytes(x) for x in parts))


def _text_replace(args: List[Any]) -> list:
    """`text_replace(data, from, to) -> Vec<u8>`：全部替换；`from` 为空 → 原样返回。"""
    raw, a, b = args[0], args[1], args[2]
    d = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    fa = bytes(int(x) & 0xFF for x in a) if isinstance(a, list) else bytes(a)
    fb = bytes(int(x) & 0xFF for x in b) if isinstance(b, list) else bytes(b)
    if not fa:
        return list(d)
    return list(d.replace(fa, fb))


def _utf8_encode(args: List[Any]) -> list:
    """`utf8_encode(cp) -> Vec<u8>`：码点 → UTF-8 字节（非法码点 → U+FFFD）。"""
    cp = int(args[0])
    try:
        return list(chr(cp).encode("utf-8"))
    except (ValueError, OverflowError):
        return list("\ufffd".encode("utf-8"))


def _utf8_codepoint_at(args: List[Any]) -> int:
    """`utf8_codepoint_at(data, byte_index) -> u64`：**从该字节起**解一个码点。"""
    raw = args[0]
    d = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    i = int(args[1])
    if not (0 <= i < len(d)):
        return 0xFFFD
    for width in (1, 2, 3, 4):
        try:
            ch = d[i:i + width].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if len(ch) == 1:
            return ord(ch)
    return 0xFFFD


_register("text_split", 2, _text_split, "n", ret="split", empty="空 → 一个空段")
_register("split_len", 1, _split_len, "1", empty="—")
_register("split_get", 2, _split_get, "n", ret="vec-u8", empty="越界 → 空")
_register("text_join", 2, _text_join, "n", ret="vec-u8", empty="空 → 空")
_register("text_replace", 3, _text_replace, "nm", ret="vec-u8", empty="from 为空 → 原样")
_register("utf8_encode", 1, _utf8_encode, "1", ret="vec-u8", empty="非法 → U+FFFD")
_register("utf8_codepoint_at", 2, _utf8_codepoint_at, "1", empty="越界 → U+FFFD")


# ------------------------------------------------------------------ A：base64 / JSON 输出
#
# **JSON 只做"输出"**：`json_emit_*` 生成字节串，用户用 `text_concat` 拼。
# 不解析 JSON——**解析要嵌套类型（对象/数组），那还没做**。

_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _b64_encode(args: List[Any]) -> list:
    """`base64_encode(data) -> Vec<u8>`：标准 base64（带 `=` 补齐）。"""
    raw = args[0]
    d = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    out = bytearray()
    i = 0
    while i + 2 < len(d):
        n = (d[i] << 16) | (d[i + 1] << 8) | d[i + 2]
        out += bytes((ord(_B64[(n >> 18) & 63]), ord(_B64[(n >> 12) & 63]),
                      ord(_B64[(n >> 6) & 63]), ord(_B64[n & 63])))
        i += 3
    rest = len(d) - i
    if rest == 1:
        n = d[i] << 16
        out += bytes((ord(_B64[(n >> 18) & 63]), ord(_B64[(n >> 12) & 63]), ord("="), ord("=")))
    elif rest == 2:
        n = (d[i] << 16) | (d[i + 1] << 8)
        out += bytes((ord(_B64[(n >> 18) & 63]), ord(_B64[(n >> 12) & 63]),
                      ord(_B64[(n >> 6) & 63]), ord("=")))
    return list(bytes(out))


def _b64_decode(args: List[Any]) -> Any:
    """`base64_decode(data) -> Vec<u8>!`：非法字符/长度 → `Err(Parse::BadNumber)`。"""
    from interp import Err, Ok, EnumVal
    raw = args[0]
    d = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    try:
        s = d.decode("ascii").strip()
    except UnicodeDecodeError:
        return Err(EnumVal("Parse", "BadNumber"))
    idx = {c: i for i, c in enumerate(_B64)}
    s = s.rstrip("=")
    if len(s) % 4 == 1:
        return Err(EnumVal("Parse", "BadNumber"))
    acc, bits = 0, 0
    out = bytearray()
    for ch in s:
        if ch not in idx:
            return Err(EnumVal("Parse", "BadNumber"))
        acc = (acc << 6) | idx[ch]
        bits += 6
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return Ok(list(bytes(out)))


def _json_emit_str(args: List[Any]) -> list:
    """`json_emit_str(data) -> Vec<u8>`：带引号并转义（`"` `\\` 控制字符）。"""
    raw = args[0]
    d = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    out = bytearray(b'"')
    for b in d:
        if b == 0x22:
            out += b'\\"'
        elif b == 0x5C:
            out += b'\\\\'
        elif b == 0x0A:
            out += b'\\n'
        elif b == 0x09:
            out += b'\\t'
        elif b == 0x0D:
            out += b'\\r'
        elif b < 0x20:
            out += ("\\u%04x" % b).encode("ascii")
        else:
            out.append(b)
    out += b'"'
    return list(bytes(out))


def _json_emit_u64(args: List[Any]) -> list:
    """`json_emit_u64(n) -> Vec<u8>`：数字（JSON 里不带引号）。"""
    return list(str(int(args[0]) & 0xFFFFFFFFFFFFFFFF).encode("ascii"))


def _json_emit_bool(args: List[Any]) -> list:
    """`json_emit_bool(n) -> Vec<u8>`：`true` / `false`。"""
    return list(b"true" if int(args[0]) != 0 else b"false")


def _text_concat(args: List[Any]) -> list:
    """`text_concat(a, b) -> Vec<u8>`：拼接（**分配 1 次**）。"""
    def to_bytes(x):
        return bytes(int(v) & 0xFF for v in x) if isinstance(x, list) else bytes(x)
    return list(to_bytes(args[0]) + to_bytes(args[1]))


# ------------------------------------------------------------------ B：按字符取值
def _utf8_char_at(args: List[Any]) -> int:
    """`utf8_char_at(data, char_index) -> u64`：**第 i 个字符**的码点（不是字节）。"""
    raw = args[0]
    d = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    s = d.decode("utf-8", "replace")
    i = int(args[1])
    return ord(s[i]) if 0 <= i < len(s) else 0xFFFD


def _utf8_byte_of_char(args: List[Any]) -> int:
    """`utf8_byte_of_char(data, char_index) -> u64`：第 i 个字符的**字节偏移**。"""
    raw = args[0]
    d = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    s = d.decode("utf-8", "replace")
    i = int(args[1])
    if not (0 <= i < len(s)):
        return len(d)
    return len(s[:i].encode("utf-8"))


_register("base64_encode", 1, _b64_encode, "n", ret="vec-u8", empty="空 → 空")
_register("base64_decode", 1, _b64_decode, "n", ret="result-vec-u8", empty="非法 → Err(Parse::BadNumber)")
_register("json_emit_str", 1, _json_emit_str, "n", ret="vec-u8", empty="空 → \"\"")
_register("json_emit_u64", 1, _json_emit_u64, "1", ret="vec-u8", empty="—")
_register("json_emit_bool", 1, _json_emit_bool, "1", ret="vec-u8", empty="—")
_register("text_concat", 2, _text_concat, "n", ret="vec-u8", empty="空 + 空 → 空")
# **这两个是"扫一遍文本"却登记成常数**（2026-11-11，κ 低估审计 ① 的同族）：
# `utf8_char_at` / `utf8_byte_of_char` 的实现都先 `d.decode("utf-8")` 把**整串**
# 解一遍（`utf8_byte_of_char` 还要再 `encode` 前缀）⇒ 真实 O(n)。记 `"1"`
# 是低估，改成 `"n"`（以第一个实参 `n(data)` 为尺寸量）。
_register("utf8_char_at", 2, _utf8_char_at, "n", empty="越界 → U+FFFD")
_register("utf8_byte_of_char", 2, _utf8_byte_of_char, "n", empty="越界 → 长度")


# ------------------------------------------------------------------ 一层嵌套（v0.71）
#
# **只支持一层**（`Vec<Vec<u8>>` / `Vec<Vec<u64>>`），理由与记账口径见
# `文档/设计/目前设计/三个前置-做什么用.md` §2：
#   · 分配 = `1 + n(外层)`（外层 1 次 + 每行各 1 次）
#   · 内层长度不靠 assume：`n(外层) ≤ n(text)`、`Σ n(内层) ≤ n(text)`

def _text_split_vec(args: List[Any]) -> list:
    """`text_split_vec(data, sep) -> Vec<Vec<u8>>`：**一层的表**（真正可下标）。"""
    raw, sep = args[0], args[1]
    d = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    s = bytes(int(x) & 0xFF for x in sep) if isinstance(sep, list) else bytes(sep)
    parts = d.split(s) if s else [d]
    return [list(x) for x in parts]


def _text_join_vec(args: List[Any]) -> list:
    """`text_join_vec(rows, sep) -> Vec<u8>`：把一层的表拼回去。"""
    rows, sep = args[0], args[1]
    s = bytes(int(x) & 0xFF for x in sep) if isinstance(sep, list) else bytes(sep)
    return list(s.join(bytes(int(v) & 0xFF for v in r) for r in rows))


_register("text_split_vec", 2, _text_split_vec, "n", ret="vec-vec-u8", empty="空 → 一个空段")
_register("text_join_vec", 2, _text_join_vec, "n", ret="vec-u8", empty="空 → 空")


# ------------------------------------------------------------------ 流式（v0.71 第一批）
#
# **口径（`设计-流式输入.md` §2）**：`next_line` 的成本 = **`1 + n(ret)`**——
# 即"这次读了多少字节"，**和参数用同一套 `n(...)` 写法**，不需要 assume。
# 循环的总上界由**调用方传参**（预算）表达，与库无关。

class ReaderVal:
    """带缓冲的读句柄（不透明）。"""

    __slots__ = ("fh", "buf", "pos", "eof", "closed")

    def __init__(self, fh, buf_size: int = 8192):
        self.fh = fh
        self.buf = b""
        self.pos = 0
        self.eof = False
        self.closed = False

    def fill(self) -> bool:
        """从文件再读一块；返回是否读到数据。"""
        if self.eof or self.closed:
            # **已关闭的句柄按 EOF 处理**：`close` 之后再读不崩、也不静默返回数据。
            self.eof = True
            return False
        chunk = self.fh.read(self._buf_size)
        if not chunk:
            self.eof = True
            return False
        self.buf = self.buf[self.pos:] + chunk
        self.pos = 0
        return True

    _buf_size = 8192

    def next_line(self):
        """返回 `(行, 消耗字节数)`；EOF → `(None, 0)`。

        成本就是**返回值的长度**——所以口径写 `1 + n(ret)`。
        """
        while True:
            nl = self.buf.find(b"\n", self.pos)
            if nl >= 0:
                line = self.buf[self.pos:nl]
                self.pos = nl + 1
                return line, len(line) + 1
            if self.eof:
                if self.pos >= len(self.buf):
                    return None, 0
                line = self.buf[self.pos:]
                self.pos = len(self.buf)
                return line, len(line)
            if not self.fill():
                continue


def _open_read(args: List[Any]) -> Any:
    """`open_read(path) -> Reader!`：**成功也返回 `Ok(Reader)`**（K6）。

    2026-11-10 之前成功值返回**裸** `ReaderVal`：指针能传给 `next_line` / `close`，
    所以表面上"能用"，但文档推荐的
    `match (open_read(p)) { Ok(r) => … }` **永远不命中**、被 `_` 静默吃掉。
    现在成功值也是 `Result` 的 `Ok`，与 `FAIL_MODE=err` / `ret=result-reader`
    的声明一致；`?` 仍然解包（`const Reader r = open_read(p)?;`），
    直接传给 `next_line` / `close` 则**明确报错**，不再静默给个错分支。
    """
    from interp import Err, EnumVal, Ok
    path = _decode_path(args[0])
    try:
        fh = open(path, "rb")
    except FileNotFoundError:
        return Err(EnumVal("Io", "NotFound"))
    except PermissionError:
        return Err(EnumVal("Io", "Denied"))
    except OSError:
        return Err(EnumVal("Io", "Failed"))
    return Ok(ReaderVal(fh))


def _next_line(args: List[Any]) -> Any:
    r = args[0]
    if not isinstance(r, ReaderVal):
        raise TypeError(
            "next_line 的实参不是 Reader：`open_read` 的返回值是 `Reader!`，"
            "要先 `?` 解包（`const Reader r = open_read(p)?;`）"
            "或 `match` 出 `Ok(r)`")
    line, _used = r.next_line()
    if line is None:
        return none()
    return some(list(line))


def _close(args: List[Any]) -> int:
    r = args[0]
    if not isinstance(r, ReaderVal):
        raise TypeError(
            "close 的实参不是 Reader：`open_read` 的返回值是 `Reader!`，"
            "要先 `?` 解包（`const Reader r = open_read(p)?;`）"
            "或 `match` 出 `Ok(r)`")
    if not r.closed:
        try:
            r.fh.close()
        except OSError:
            pass
        r.closed = True
    return 1


_register("open_read", 1, _open_read, "ext", ret="result-reader",
          empty="成功 → Ok(Reader)（用 `?` 或 `match` 的 `Ok(r)` 臂取出）；"
                "失败 → Err(Io::*)")
_register("next_line", 1, _next_line, "nret", ret="option-vec-u8",
          empty="EOF → Option::None")
_register("close", 1, _close, "1", empty="幂等")


# ------------------------------------------------------------------ 流式写（第二批）
def _open_write(args: List[Any]) -> Any:
    """`open_write(path) -> Writer!`：**成功也返回 `Ok(Writer)`**（同 `open_read`）。"""
    from interp import Err, EnumVal, Ok
    path = _decode_path(args[0])
    try:
        fh = open(path, "wb")
    except PermissionError:
        return Err(EnumVal("Io", "Denied"))
    except OSError:
        return Err(EnumVal("Io", "Failed"))
    return Ok(WriterVal(fh))


class WriterVal:
    """带缓冲的写句柄（不透明）。"""

    __slots__ = ("fh", "closed")

    def __init__(self, fh):
        self.fh = fh
        self.closed = False


def _write_chunk(args: List[Any]) -> int:
    from interp import InterpError      # 局部导入：错误路径不能自己 `NameError`（同本文件其它函数）
    w = args[0]
    if not isinstance(w, WriterVal):
        raise TypeError(
            "write_chunk 的第一个实参不是 Writer：`open_write` 的返回值是 `Writer!`，"
            "要先 `?` 解包或 `match` 出 `Ok(w)`")
    if w.closed:
        raise InterpError("write_chunk 作用在已关闭的 Writer 上")
    raw = args[1]
    data = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    try:
        w.fh.write(data)
    except OSError as exc:
        raise InterpError(f"写失败：{exc}") from None
    return len(data)


def _close_write(args: List[Any]) -> int:
    w = args[0]
    if not isinstance(w, WriterVal):
        raise TypeError(
            "close_write 的实参不是 Writer：`open_write` 的返回值是 `Writer!`，"
            "要先 `?` 解包或 `match` 出 `Ok(w)`")
    if not w.closed:
        try:
            w.fh.flush()
            w.fh.close()
        except OSError:
            pass
        w.closed = True
    return 1


_register("open_write", 1, _open_write, "ext", ret="result-writer",
          empty="成功 → Ok(Writer)（用 `?` 或 `match` 的 `Ok(w)` 臂取出）；"
                "失败 → Err(Io::*)")
_register("write_chunk", 2, _write_chunk, "n", empty="返回写入字节数")
_register("close_write", 1, _close_write, "1", empty="幂等")


# ------------------------------------------------------------------ 解释器补完（v0.71）
#
# **先不做编译器**：把解释器这边能做的先做齐（用户定的顺序）。

def _vec_push(args: List[Any]) -> int:
    """`vec_push(xs, v) -> u64`：末尾追加（**原地**），返回新长度。

    分配口径：**摊还 1 次**（扩容那一次不算；总分配 ≤ push 次数）。
    """
    xs = args[0]
    if not isinstance(xs, list):
        raise TypeError("vec_push 的实参不是 Vec")
    xs.append(args[1])
    return len(xs)


def _vec_pop(args: List[Any]) -> Any:
    """`vec_pop(xs) -> Option<u64>`：弹出末尾；空 → `Option::None`。"""
    xs = args[0]
    if not isinstance(xs, list):
        raise TypeError("vec_pop 的实参不是 Vec")
    return some(xs.pop()) if xs else none()


def _json_array(args: List[Any]) -> list:
    """`json_array(items) -> Vec<u8>`：`items` 是**已序列化好的**一层的表。

    例：`json_array(["1", "2"])` → `[1,2]`（成员原样拼，不再加引号——
    要字符串请先过 `json_emit_str`）。
    """
    items = args[0]
    parts = []
    for it in items:
        parts.append(bytes(int(x) & 0xFF for x in it) if isinstance(it, list) else bytes(it))
    return list(b"[" + b",".join(parts) + b"]")


def _json_object(args: List[Any]) -> list:
    """`json_object(flat) -> Vec<u8>`：`flat` 是**键值交替**的一层表。

    例：`json_object(["\"a\"", "1", "\"b\"", "2"])` → `{"a":1,"b":2}`。
    键要自己先 `json_emit_str`（这样 `"a"` 的转义由它负责）。
    """
    flat = args[0]
    seen = {}
    parts = []
    i = 0
    while i + 1 < len(flat):
        k = flat[i]
        v = flat[i + 1]
        kb = bytes(int(x) & 0xFF for x in k) if isinstance(k, list) else bytes(k)
        vb = bytes(int(x) & 0xFF for x in v) if isinstance(v, list) else bytes(v)
        parts.append(kb + b":" + vb)
        i += 2
    return list(b"{" + b",".join(parts) + b"}")


def _env_set(args: List[Any]) -> int:
    """`env_set(name, value) -> u64`：设置环境变量（**进程内有效**）。"""
    name = _decode_path(args[0])
    val = _decode_path(args[1])
    os.environ[name] = val
    return 1


def _fs_rename(args: List[Any]) -> Any:
    """`fs_rename(from, to) -> u64!`：改名/移动。"""
    from interp import Err, Ok, EnumVal
    a, b = _decode_path(args[0]), _decode_path(args[1])
    try:
        os.replace(a, b)
    except FileNotFoundError:
        return Err(EnumVal("Io", "NotFound"))
    except PermissionError:
        return Err(EnumVal("Io", "Denied"))
    except OSError:
        return Err(EnumVal("Io", "Failed"))
    return Ok(1)


def _fs_remove(args: List[Any]) -> Any:
    """`fs_remove(path) -> u64!`：删除文件。"""
    from interp import Err, Ok, EnumVal
    p = _decode_path(args[0])
    try:
        os.remove(p)
    except FileNotFoundError:
        return Err(EnumVal("Io", "NotFound"))
    except PermissionError:
        return Err(EnumVal("Io", "Denied"))
    except OSError:
        return Err(EnumVal("Io", "Failed"))
    return Ok(1)


def _utf8_chars_vec(args: List[Any]) -> list:
    """`utf8_chars_vec(data) -> Vec<u64>`：整串 → **码点数组**（用现有 `vec-u64`，
    不必等 `Vec<u32>`）。"""
    raw = args[0]
    d = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    return [ord(ch) for ch in d.decode("utf-8", "replace")]


_register("vec_push", 2, _vec_push, "1", empty="返回新长度")
_register("vec_pop", 1, _vec_pop, "1", ret="option-elem",
          empty="空 → Option::None")
_register("json_array", 1, _json_array, "n", ret="vec-u8", empty="空 → []")
_register("json_object", 1, _json_object, "n", ret="vec-u8", empty="空 → {}")
_register("env_set", 2, _env_set, "1", empty="—")
_register("fs_rename", 2, _fs_rename, "ext", ret="result-u64", empty="失败 → Err(Io::*)")
_register("fs_remove", 1, _fs_remove, "ext", ret="result-u64", empty="失败 → Err(Io::*)")
_register("utf8_chars_vec", 1, _utf8_chars_vec, "n", ret="vec-u64", empty="空 → 空")


def _option_unwrap(args: List[Any]) -> Any:
    """`option_unwrap(o) -> T`：`Some(v) → v`；`None` 是**错误**。

    以前只有 `option_unwrap_or(o, default)`，想"一定要有值"只能写
    `option_unwrap_or(o, 0)`——**把漏判静默变成 0**，那是这个语言最不该有的东西。
    """
    v = args[0]
    if getattr(v, "variant", "") != "Some":
        from interp import InterpError as _IE      # 延迟导入，避免成环
        raise _IE("option_unwrap：值不是 Some（要么漏判，要么该用 option_unwrap_or）")
    return getattr(v, "payload", None)


def _option_unwrap_or(args: List[Any]) -> Any:
    """`option_unwrap_or(o, default) -> T`：`Some(v) → v`，`None → default`。

    **为什么需要它**：`?` 只对 `Result` 生效，`Option` 要显式解包——
    这比"假装 `?` 也能解 Option"更清楚。
    """
    o = args[0]
    return o.payload if getattr(o, "variant", "") == "Some" else args[1]


_register("option_unwrap_or", 2, _option_unwrap_or, "1", ret="option-payload",
          empty="None → default")
_register("option_unwrap", 1, _option_unwrap, "1", ret="option-payload",
          empty="None → 报错")


def _to_bytes(x) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, list):
        return bytes(int(v) & 0xFF for v in x)
    if hasattr(x, "text"):                      # interp.Text
        return str(x.text()).encode("utf-8")
    return str(x).encode("utf-8")


def _format(args: List[Any]) -> list:
    """`format("x = {}, y = {:>2}", x, y)` —— **位置参数**，第一个参数是格式串。

    解析规则与检查器**共用 `fmt.py`**（两边各写一份迟早漂移）。

    **错误路径必须真的能抛**：`InterpError` 是解释器的语言级异常，本模块没有
    模块级导入它（其他函数都是函数内 `from interp import InterpError`）。
    这里以前漏了这行——于是任何运行期格式错误都变成宿主
    `NameError: name 'InterpError' is not defined`，用户看到的是 Python
    traceback 而不是 `E-Format-*`（2026-11-10 修）。
    """
    import fmt as _fmt
    from interp import InterpError
    if not args:
        raise InterpError("format 至少要 1 个实参（格式串）")
    text = _to_bytes(args[0]).decode("utf-8", "replace")
    values = list(args[1:])
    try:
        parts = _fmt.parse(text)
    except _fmt.FmtError as exc:
        raise InterpError(f"{exc.code}: {exc.msg}") from None
    out = []
    vi = 0
    for kind, payload in parts:
        if kind == "lit":
            out.append(payload)
            continue
        if vi >= len(values):
            raise InterpError(f"E-Format-Arity: 格式串里的洞比实参多（第 {vi + 1} 个洞没值）")
        try:
            out.append(_fmt.apply(payload, values[vi]))
        except _fmt.FmtError as exc:
            raise InterpError(f"{exc.code}: {exc.msg}") from None
        vi += 1
    if vi != len(values):
        raise InterpError(f"E-Format-Arity: 给了 {len(values)} 个实参，格式串只有 {vi} 个洞")
    return list("".join(out).encode("utf-8"))


_register("format", 1, _format, "n", ret="vec-u8", varargs=True,
          # `empty` 记的是"这条内建的用法要点"：洞数与实参数**必须相等**
          #（多一个少一个都报 `E-Format-Arity`），所以它是变长的、但变长也有约束。
          # **spec 全集必须写在这里**：`文档/内建函数.md` 是从这条登记生成的，
          # 少写一种写法，用户就只能靠试——而试出来的"静默降级"正是 2026-11-10
          # 修掉的那批 bug（`{:08x}` 曾经补空格、`{:.6f}` 曾经报"没有浮点类型"）。
          empty="变长：每个 `{}` 按位置取一个实参；**洞数与实参数必须相等**"
                "（多/少都报 `E-Format-Arity`）。**spec 全集**：`{}`（默认，与 `print(x)` 同结果）、"
                "`{:d}`/`{:u}`（十进制；`{:u}` 只收非负）、`{:x}`/`{:X}`（十六进制，只收非负整数）、"
                "`{:f}`/`{:.Nf}`（定点小数，N=0..100，默认 6 位）、`{:s}`（文本）；"
                "可叠加宽度 `N`、零填充 `0N`、对齐 `<`/`>`（如 `{:08x}` → `000000ff`、"
                "`{:8x}` → `      ff`、`{:10.2f}` → `      3.14`）；"
                "其余写法（`{:b}`/`{:o}`/`{:e}`/`{:g}`/`{:^8}`/`{:+}`/`{:.2}` 等）"
                "一律 `E-Format-BadSpec` 并列出支持集，**不静默降级**；"
                "详见「花括号格式串」一节")


# ------------------------------------------------------------------ JSON 解析（递归类型）
#
# **产物是递归枚举**：Json { Null, Bool(u64), Num(u64), Str(Vec<u8>), Arr(Vec<Json>), Obj(Vec<Entry>) }。
# 口径见 jsonval.py：时间 O(n(text))，分配 1 + 节点数 ≤ 1 + n(text)。

def _json_parse(args: List[Any]) -> Any:
    from interp import Err, Ok, EnumVal
    import jsonval
    raw = args[0]
    data = bytes(int(x) & 0xFF for x in raw) if isinstance(raw, list) else bytes(raw)
    try:
        value, _nodes = jsonval.parse(data)
    except jsonval.JsonError as exc:
        return Err(EnumVal("Json", exc.code, exc.pos))
    return Ok(value)


_register("json_parse", 1, _json_parse, "n", ret="result-json",
          empty="失败 -> Err(Json::*)，payload 是字节位置")


# ------------------------------------------------------------------ 数学内建（O(1)）
#
# **为什么必须有**（`_cppmatrix/misc/results/24*`）：`sqrt` / `floor` / `ceil` /
# `abs` / `log2` / `popcount` 一个都没有，而 **FFI 也补不上**——C ABI 后端不支持
# `f64` 参数（`E-FFI-Unsupported`）。于是"算个平方根"只能牛顿迭代 / 二分，
# 契约上还得写 `O(log n)`；那不是在写程序，是在给语言擦屁股。
#
# **收编口径（三条全部满足才收）**：
#   1. 成本 **O(1)**、与输入规模无关（`cost="1"`，κ 里记常数）；
#   2. **不引入新的失败模式**：不 I/O、不分配、不随机、不抛异常（`clz(0)` /
#      `ctz(0)` 这种前置条件违反另算，见下）；
#   3. 语义与 C 的 `math.h` / `__builtin_*` **逐条对齐**，不做"聪明"的偏离。
#
# **返回类型分两类**（`ret=` 两处都要登记，`typecheck` 才认）：
#   - `sqrt` / `floor` / `ceil` / `round` / `fabs` / `log2` → `f64`（同 C 的 `double`）；
#   - `popcount` / `clz` / `ctz` → `u64`；
#   - `abs` → **跟实参走**（`ret="arg0"`）：整数进、整数出；`f64` 进、`f64` 出。
#
# **没有收的**：`sin`/`cos`/`tan`/`exp`/`pow`/`log`（`pow` 用运算符 `**`，见语法清单）、
# 128 位整数、`sqrt` 的整数版（用二分，见能力边界 §4.2）。

_U64_BITS = 64
_U64_MASK = (1 << _U64_BITS) - 1


def _as_f64(x: Any, who: str) -> float:
    """把实参变成 `f64`。整数按值转（C 也允许隐式转换），其它类型报错。"""
    if isinstance(x, bool):
        raise TypeError(f"{who} 的实参不能是 bool")
    if isinstance(x, (int, float)):
        return float(x)
    raise TypeError(f"{who} 的实参必须是 f64（或整数），给的是 {type(x).__name__}")


def _as_u64(x: Any, who: str) -> int:
    """把实参按 **64 位补码** 取整数值（`popcount` / `clz` / `ctz` 的共用入口）。"""
    if isinstance(x, bool) or not isinstance(x, int):
        raise TypeError(f"{who} 的实参必须是 u64 整数（`i64` 先 `as u64`），"
                        f"给的是 {type(x).__name__}")
    return int(x) & _U64_MASK


def _sqrt(args: List[Any]) -> float:
    x = _as_f64(args[0], "sqrt")
    # C 的 `sqrt(-1)` 是 **NaN**（不是错误、不是 0）：这里原样照抄。
    return math.sqrt(x) if x >= 0.0 else float("nan")


def _log2(args: List[Any]) -> float:
    x = _as_f64(args[0], "log2")
    if x > 0.0:
        return math.log2(x)
    if x == 0.0:
        return float("-inf")          # 同 C：log2(±0) = -inf（并置 FE_DIVBYZERO）
    return float("nan")               # 负数 → NaN


def _floor(args: List[Any]) -> float:
    x = _as_f64(args[0], "floor")
    # `math.floor` 对 nan/inf 抛 ValueError；C 是原样返回，先挡掉。
    return x if not math.isfinite(x) else float(math.floor(x))


def _ceil(args: List[Any]) -> float:
    x = _as_f64(args[0], "ceil")
    return x if not math.isfinite(x) else float(math.ceil(x))


def _round(args: List[Any]) -> float:
    """**四舍五入，遇 .5 远离零**（C 的 `round`，不是 banker's rounding）。

    Python 的 `round()` 是银行家舍入（`round(2.5) == 2`），直接用会让
    `round(2.5)` 给 `2.0`——和 C / 数学课本都不一致，所以手写：
    `floor(x + 0.5)`（非负）/ `ceil(x - 0.5)`（负）。
    """
    x = _as_f64(args[0], "round")
    if not math.isfinite(x):
        return x
    return float(math.floor(x + 0.5)) if x >= 0.0 else float(math.ceil(x - 0.5))


def _fabs(args: List[Any]) -> float:
    """`fabs(x: f64) -> f64`：**浮点绝对值**（`-0.0 → 0.0`、NaN 保持 NaN）。"""
    return math.fabs(_as_f64(args[0], "fabs"))


def _abs(args: List[Any]) -> Any:
    """`abs(x) -> 同类型`：整数按符号、`f64` 同 `fabs`。

    **符号怎么处理**：
      - 无符号（`u8`…`u64`）：值域非负，**恒等**（`abs(5) == 5`）；
      - 有符号（`i8`…`i64`）：`x < 0` 取 `-x`。`abs(i64 最小值)` 会**超出 i64**，
        这里不特殊处理——它按语言里 `-x` 的同一套溢出语义走（与 C 的
        `abs(INT64_MIN)` 是 UB 不同：我们不 UB，给的是回绕后的确定值）；
      - `f64`：走 `fabs`（`-0.0 → 0.0`、NaN → NaN）。
    """
    x = args[0]
    if isinstance(x, float):
        return math.fabs(x)
    if isinstance(x, bool) or not isinstance(x, int):
        raise TypeError(f"abs 的实参必须是整数或 f64，给的是 {type(x).__name__}")
    return -x if x < 0 else x


def _popcount(args: List[Any]) -> int:
    """`popcount(x: u64) -> u64`：二进制里 1 的个数（`popcount(0) == 0`）。"""
    return _as_u64(args[0], "popcount").bit_count()


def _clz(args: List[Any]) -> int:
    """`clz(x: u64) -> u64`：前导零个数（64 位）。`clz(0)` **报错**。

    C 的 `__builtin_clz(0)` 是 **UB**（gcc 甚至给"操作数位数 − 1"这种没意义的值）。
    本项目里"未定义"不是一个可以留着的状态——所以**报错**，并给出改法。
    选"返回 64"的写法看起来更"顺手"，但那等于**编了一个值**：`clz(0)` 之后的
    `64 - clz(x)` 会静默变成 `0`（一个看起来合理的答案），正是这个项目最反对的
    "静默错值"。
    """
    v = _as_u64(args[0], "clz")
    if v == 0:
        from interp import InterpError        # 局部导入：避免与 interp 循环 import
        raise InterpError(
            "clz(0) 未定义（C 的 `__builtin_clz(0)` 是 UB）：先判空再取，"
            "`if (x == 0) { … }`；`u64` 的位宽是 64，`clz(1) = 63`")
    return _U64_BITS - v.bit_length()


def _ctz(args: List[Any]) -> int:
    """`ctz(x: u64) -> u64`：末尾零个数。`ctz(0)` **报错**（理由同 `clz`）。"""
    v = _as_u64(args[0], "ctz")
    if v == 0:
        from interp import InterpError        # 局部导入：避免与 interp 循环 import
        raise InterpError(
            "ctz(0) 未定义（C 的 `__builtin_ctz(0)` 是 UB）：先判空再取，"
            "`if (x == 0) { … }`；`ctz(1) = 0`")
    return (v & -v).bit_length() - 1


_register("sqrt", 1, _sqrt, "1", ret="f64",
          empty="负数 -> NaN（同 C，不是错误）")
_register("log2", 1, _log2, "1", ret="f64",
          empty="0.0 -> -inf、负数 -> NaN（同 C）")
_register("floor", 1, _floor, "1", ret="f64",
          empty="返回 **f64**（不是 u64）；inf/nan 原样返回")
_register("ceil", 1, _ceil, "1", ret="f64",
          empty="返回 **f64**（不是 u64）；inf/nan 原样返回")
_register("round", 1, _round, "1", ret="f64",
          empty="四舍五入、**.5 远离零**（C 的 round：`2.5 -> 3.0`、`-2.5 -> -3.0`；"
                "不是银行家舍入）")
_register("fabs", 1, _fabs, "1", ret="f64",
          empty="-0.0 -> 0.0；NaN 保持 NaN（同 C 的 fabs）")
_register("abs", 1, _abs, "1", ret="arg0",
          empty="**跟实参类型走**：无符号恒等、有符号取负、f64 同 `fabs`")
_register("popcount", 1, _popcount, "1",
          empty="按 64 位；`popcount(0) = 0`")
_register("clz", 1, _clz, "1",
          empty="按 64 位；**`clz(0)` 报错**（C 是 UB，这里不编值）；`clz(1) = 63`")
_register("ctz", 1, _ctz, "1",
          empty="按 64 位；**`ctz(0)` 报错**（C 是 UB，这里不编值）；`ctz(1) = 0`")


# ------------------------------------------------------------------ 字符分类（ASCII，O(1)）
#
# 八个纯函数：`isdigit` / `isspace` / `isalpha` / `isalnum` /
# `toupper` / `tolower` / `isupper` / `islower`。
# 全 `O(1)`、不分配、`effects {}`——放进循环里，κ 只随圈数走。
#
# **语义按 ASCII 定死，与 C++ 的 `std::isdigit` 不同**：C++ 那套跟着
# locale 走（同一个字节在不同 locale 下可能给出不同答案，负数还是 UB）。
# 本语言**没有 locale**，所以：
#
#   - `c < 128`  → 按 ASCII 判：`'0'..'9'` / 空格与 `\t \n \v \f \r` /
#                  `'A'..'Z'` / `'a'..'z'`；
#   - `c >= 128` → 分类函数一律 **0**；`toupper` / `tolower` **原样返回**。
#
# **实参按 u64 解释**（C 里负数/超范围是 UB，这里定义好）：先取 64 位补码，
# 再按上面两条判——所以 `isdigit(-1)` 是 `isdigit(2^64-1)` → 0，
# `toupper(-1)` 原样返回 `2^64-1`（`u64` 值）。
#
# 字符范围用**集合**表示，而不是 `str.isdigit()` 之类的 Python 判定——
# Python 的 `'²'.isdigit()` 是 True、`'é'.isalpha()` 是 True，那是 Unicode
# 语义，与本文件的 ASCII 口径不同。显式列出字节值，边界就在源码里。

_ASCII_DIGIT = frozenset(range(0x30, 0x3A))            # '0'..'9'
_ASCII_SPACE = frozenset({0x20, 0x09, 0x0A, 0x0B, 0x0C, 0x0D})
_ASCII_UPPER = frozenset(range(0x41, 0x5B))            # 'A'..'Z'
_ASCII_LOWER = frozenset(range(0x61, 0x7B))            # 'a'..'z'


def _char_byte(args: List[Any], who: str) -> int:
    """把实参变成 **u64**（`_as_u64` 的字符分类专用别名，只为可读）。

    **负数/超范围按无符号处理**（见本节文件头）：`i64 -1` 先变成
    `2^64-1`，再按 ASCII 判——不是 UB、也不是报错。
    """
    return _as_u64(args[0], who)


def _isdigit(args: List[Any]) -> bool:
    """`isdigit(c) -> bool`：ASCII 十进制数字（含 `c >= 128` 一律 false）。"""
    return _char_byte(args, "isdigit") in _ASCII_DIGIT


def _isspace(args: List[Any]) -> bool:
    """`isspace(c) -> bool`：ASCII 空白（空格 / `\\t` / `\\n` / `\\v` / `\\f` / `\\r`）。"""
    return _char_byte(args, "isspace") in _ASCII_SPACE


def _isalpha(args: List[Any]) -> bool:
    """`isalpha(c) -> bool`：ASCII 字母（含 `c >= 128` 一律 false）。"""
    v = _char_byte(args, "isalpha")
    return v in _ASCII_UPPER or v in _ASCII_LOWER


def _isalnum(args: List[Any]) -> bool:
    """`isalnum(c) -> bool`：ASCII 字母或数字（不是 Unicode 的"字母数字"）。"""
    v = _char_byte(args, "isalnum")
    return v in _ASCII_UPPER or v in _ASCII_LOWER or v in _ASCII_DIGIT


def _isupper(args: List[Any]) -> bool:
    """`isupper(c) -> bool`：ASCII 大写字母。"""
    return _char_byte(args, "isupper") in _ASCII_UPPER


def _islower(args: List[Any]) -> bool:
    """`islower(c) -> bool`：ASCII 小写字母。"""
    return _char_byte(args, "islower") in _ASCII_LOWER


def _toupper(args: List[Any]) -> int:
    """`toupper(c) -> u64`：`'a'..'z'` → `'A'..'Z'`，**其它原样返回**（含 ≥128）。"""
    v = _char_byte(args, "toupper")
    return v - 0x20 if v in _ASCII_LOWER else v


def _tolower(args: List[Any]) -> int:
    """`tolower(c) -> u64`：`'A'..'Z'` → `'a'..'z'`，**其它原样返回**（含 ≥128）。"""
    v = _char_byte(args, "tolower")
    return v + 0x20 if v in _ASCII_UPPER else v


_register("isdigit", 1, _isdigit, "1", ret="bool",
          empty="ASCII `'0'`..`'9'` -> true；其它（含 ≥128）-> false")
_register("isspace", 1, _isspace, "1", ret="bool",
          empty="ASCII 空格 / `\\t` / `\\n` / `\\v` / `\\f` / `\\r` -> true；其它 -> false")
_register("isalpha", 1, _isalpha, "1", ret="bool",
          empty="ASCII 字母 -> true；其它（含 ≥128）-> false")
_register("isalnum", 1, _isalnum, "1", ret="bool",
          empty="ASCII 字母或数字 -> true；其它（含 ≥128）-> false")
_register("isupper", 1, _isupper, "1", ret="bool",
          empty="ASCII `'A'`..`'Z'` -> true；其它 -> false")
_register("islower", 1, _islower, "1", ret="bool",
          empty="ASCII `'a'`..`'z'` -> true；其它 -> false")
_register("toupper", 1, _toupper, "1",
          empty="`'a'`..`'z'` → 大写；其它**原样返回**（含 ≥128）")
_register("tolower", 1, _tolower, "1",
          empty="`'A'`..`'Z'` → 小写；其它**原样返回**（含 ≥128）")
