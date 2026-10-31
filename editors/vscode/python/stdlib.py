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
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

#: 名字 → (实参个数, 解释器实现, 成本阶)
#: 成本阶用字符串：`"n"` 表示 `O(n(args[0]))`，`"1"` 表示 `O(1)`。
FNS: Dict[str, Dict[str, Any]] = {}


def _register(name: str, arity: int, impl: Callable[..., Any], cost: str,
              ret: str = "u64", empty: str = "") -> None:
    FNS[name] = {"arity": arity, "impl": impl, "cost": cost,
                 "ret": ret, "empty": empty}


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


_register("reverse", 1, _reverse, "n")
_register("fill", 2, _fill, "n")
_register("dedup", 1, _dedup, "n")
_register("contains", 2, _contains, "n", empty="空 → 0（不含）")
_register("count_of", 2, _count_of, "n", empty="空 → 0 次")


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


_register("index_of", 2, _index_of, "n", empty="未找到 → len(xs)")
_register("binary_search", 2, _binary_search, "log", empty="未找到 → len(xs)；表须已排序")
_register("sum_of", 1, _sum_of, "n", empty="空 → 0")
_register("is_sorted", 1, _is_sorted, "n", empty="空 → 1")


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
_register("rotate", 2, _rotate, "n", empty="空 → 0")
_register("fill_range", 4, _fill_range, "n", empty="lo==hi → 0 个")


# ------------------------------------------------------------------ 哈希表（有界探测）
#
# **为什么是"有界探测"而不是"期望 O(1)"**：
# 期望复杂度进不了契约——它依赖数据。所以这里**硬性限制探测次数**：
# 最多 `HMAP_PROBES` 次，放不下就**返回 0（失败）**。
# 于是单次操作的工作量是**常数上界**，`O(1)` 是**真的上界**，不是"平均"。
#
# **键长度问题**：本批键是 `u64`，哈希是常数时间。
# 若将来键是 `Vec<u8>`（char[]），哈希必须**读完整个键**，
# 单次成本就得写成 `O(1 + len(key))` —— 见文档 §3.1。

HMAP_PROBES = 8
HMAP_HASH_MUL = 2654435761


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
    "print": {"io"},
    "rand_u64": {"nondet"},
    "read_file": {"fs"},
    "read_stdin": {"io"},
    "write": {"io"},
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
    return name in FNS


def arity(name: str) -> int:
    return FNS[name]["arity"]


def call(name: str, args: List[Any]) -> Any:
    return FNS[name]["impl"](args)


def ret_kind(name: str) -> str:
    return FNS[name].get("ret", "u64")


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
    "set_new": "1",
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
        "env_set", "json_array", "json_object", "utf8_chars_vec", "vec_push", "option_unwrap_or",
        "json_parse",
        "format",
    )},
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


def all_builtins() -> list:
    """注册表里的 + 解释器直接实现的（测试用它查"有没有漏"。）"""
    return sorted(set(FNS) | set(NATIVE))


def alloc_kind(name: str) -> str:
    """内建的分配次数：`0` / `1` / `n`。"""
    fn = FNS.get(name or "")
    if fn is None:
        return "0"
    return _ALLOC.get(name or "", "0")


def cost_kind(name: str) -> str:
    return FNS[name]["cost"]


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


_register("read_file", 1, _read_file, "ext", ret="vec-u8", empty="—")
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


_register("write_file", 2, _write_file, "ext", empty="—")


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
_register("try_read_file", 1, _try_read_file, "ext", ret="result-vec-u8", empty="—")


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


_register("copy", 1, _copy, "n", ret="vec-u64", empty="空 → 空")


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


_register("args", 0, _args, "1", ret="any", empty="—")
_register("args_len", 1, _args_len, "1", empty="—")
_register("args_get", 2, _args_get, "1", ret="vec-u8", empty="越界 → 空")
_register("env_get", 1, _env_get, "1", ret="any", empty="不存在 → Option::None")


def some(v):
    """`Option::Some(v)`（统一构造，避免各处自己拼 `EnumVal`）。"""
    from interp import EnumVal
    return EnumVal("Option", "Some", v)


def none():
    """`Option::None`。"""
    from interp import EnumVal
    return EnumVal("Option", "None")


_register("option_some", 1, lambda a: some(a[0]), "1", ret="any", empty="—")
_register("option_none", 0, lambda a: none(), "1", ret="any", empty="—")
_register("option_is_some", 1, lambda a: 1 if getattr(a[0], "variant", "") == "Some" else 0,
          "1", empty="Some → 1")
_register("cwd", 0, _cwd, "1", ret="vec-u8", empty="—")
_register("exit", 1, _exit, "1", empty="立即结束程序")
_register("write", 1, _write, "1", empty="写 stdout（不换行）")


# ------------------------------------------------------------------ C：HashMap<K,V>
#
# **和 `hmap_*` 一样的口径**：哈希 + **有界 8 次探测**，所以 `O(1)` 是**真上界**
# （不是"平均 O(1)"）。用 Python dict 会得到一个**最坏无界**的结构，那就是撒谎。
#
# 值语义：`HashMap` 是**可变对象**（放进 env 的是引用），`map_put` 直接改它。
# 这打破了"值语义"的直觉，所以文档里写清：**它是引用类型**。

_PROBES = 8


class MapVal:
    """开放寻址 + 有界探测的哈希表（键必须是 `Vec<u8>` / 整数）。"""

    __slots__ = ("cap", "ks", "vs", "n")

    def __init__(self, cap: int = 16):
        self.cap = 16
        while self.cap < cap:
            self.cap *= 2
        self.ks = [None] * self.cap
        self.vs = [None] * self.cap
        self.n = 0

    def _h(self, key) -> int:
        if isinstance(key, list):
            acc = 2166136261
            for b in key:
                acc = ((acc ^ (int(b) & 0xFF)) * 16777619) & 0xFFFFFFFF
            return acc & 0x7FFFFFFF
        return (int(key) * 2654435761) & 0x7FFFFFFF

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


def _map_put(args: List[Any]) -> int:
    m = args[0]
    if not isinstance(m, MapVal):
        raise TypeError("map_put 的第一个实参不是 HashMap")
    return m.put(args[1], args[2])


def _map_get(args: List[Any]) -> Any:
    from interp import EnumVal
    m = args[0]
    if not isinstance(m, MapVal):
        raise TypeError("map_get 的第一个实参不是 HashMap")
    got = m.get(args[1])
    # 未命中返回 `Option::None`（不用 -1 哨兵：值域任意，-1 会和真实值撞）。
    return some(got) if got is not None else none()


def _map_has(args: List[Any]) -> int:
    return args[0].has(args[1])


def _map_len(args: List[Any]) -> int:
    return args[0].n


_register("map_new", 0, _map_new, "1", ret="any", empty="—")
_register("map_put", 3, _map_put, "1", empty="插入成功 1 / 探测满 0")
_register("map_get", 2, _map_get, "1", ret="any", empty="未命中 → Option::None")
_register("map_has", 2, _map_has, "1", empty="未命中 → 0")
_register("map_len", 1, _map_len, "1", empty="空 → 0")


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


_register("set_new", 0, _set_new, "1", ret="any", empty="—")
_register("set_add", 2, _set_add, "1", empty="插入成功 1 / 探测满 0")
_register("set_has", 2, _set_has, "1", empty="未命中 → 0")
_register("set_len", 1, _set_len, "1", empty="空 → 0")
_register("deque_new", 0, _deque_new, "1", ret="any", empty="—")
_register("deque_push_back", 2, _deque_push_back, "1", empty="—")
_register("deque_push_front", 2, _deque_push_front, "1", empty="—")
_register("deque_pop_front", 1, _deque_pop_front, "1", ret="any", empty="空 → Option::None")
_register("deque_pop_back", 1, _deque_pop_back, "1", ret="any", empty="空 → Option::None")
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


_register("text_split", 2, _text_split, "n", ret="any", empty="空 → 一个空段")
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
_register("utf8_char_at", 2, _utf8_char_at, "1", empty="越界 → U+FFFD")
_register("utf8_byte_of_char", 2, _utf8_byte_of_char, "1", empty="越界 → 长度")


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
    from interp import Err, EnumVal
    path = _decode_path(args[0])
    try:
        fh = open(path, "rb")
    except FileNotFoundError:
        return Err(EnumVal("Io", "NotFound"))
    except PermissionError:
        return Err(EnumVal("Io", "Denied"))
    except OSError:
        return Err(EnumVal("Io", "Failed"))
    return ReaderVal(fh)


def _next_line(args: List[Any]) -> Any:
    r = args[0]
    if not isinstance(r, ReaderVal):
        raise TypeError("next_line 的实参不是 Reader")
    line, _used = r.next_line()
    if line is None:
        return none()
    return some(list(line))


def _close(args: List[Any]) -> int:
    r = args[0]
    if isinstance(r, ReaderVal) and not r.closed:
        try:
            r.fh.close()
        except OSError:
            pass
        r.closed = True
    return 1


_register("open_read", 1, _open_read, "ext", ret="any", empty="失败 → Err(Io::*)")
_register("next_line", 1, _next_line, "nret", ret="option-vec-u8",
          empty="EOF → Option::None")
_register("close", 1, _close, "1", empty="幂等")


# ------------------------------------------------------------------ 流式写（第二批）
def _open_write(args: List[Any]) -> Any:
    from interp import Err, EnumVal
    path = _decode_path(args[0])
    try:
        fh = open(path, "wb")
    except PermissionError:
        return Err(EnumVal("Io", "Denied"))
    except OSError:
        return Err(EnumVal("Io", "Failed"))
    return WriterVal(fh)


class WriterVal:
    """带缓冲的写句柄（不透明）。"""

    __slots__ = ("fh", "closed")

    def __init__(self, fh):
        self.fh = fh
        self.closed = False


def _write_chunk(args: List[Any]) -> int:
    w = args[0]
    if not isinstance(w, WriterVal):
        raise TypeError("write_chunk 的第一个实参不是 Writer")
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
    if isinstance(w, WriterVal) and not w.closed:
        try:
            w.fh.flush()
            w.fh.close()
        except OSError:
            pass
        w.closed = True
    return 1


_register("open_write", 1, _open_write, "ext", ret="any", empty="失败 → Err(Io::*)")
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
_register("vec_pop", 1, _vec_pop, "1", ret="any", empty="空 → Option::None")
_register("json_array", 1, _json_array, "n", ret="vec-u8", empty="空 → []")
_register("json_object", 1, _json_object, "n", ret="vec-u8", empty="空 → {}")
_register("env_set", 2, _env_set, "1", empty="—")
_register("fs_rename", 2, _fs_rename, "ext", ret="result-u64", empty="失败 → Err(Io::*)")
_register("fs_remove", 1, _fs_remove, "ext", ret="result-u64", empty="失败 → Err(Io::*)")
_register("utf8_chars_vec", 1, _utf8_chars_vec, "n", ret="vec-u64", empty="空 → 空")


def _option_unwrap_or(args: List[Any]) -> Any:
    """`option_unwrap_or(o, default) -> T`：`Some(v) → v`，`None → default`。

    **为什么需要它**：`?` 只对 `Result` 生效，`Option` 要显式解包——
    这比"假装 `?` 也能解 Option"更清楚。
    """
    o = args[0]
    return o.payload if getattr(o, "variant", "") == "Some" else args[1]


_register("option_unwrap_or", 2, _option_unwrap_or, "1", ret="any", empty="None → default")


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
    """
    import fmt as _fmt
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


_register("format", 1, _format, "n", ret="vec-u8", empty="—")


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


_register("json_parse", 1, _json_parse, "n", ret="any",
          empty="失败 -> Err(Json::*)，payload 是字节位置")
