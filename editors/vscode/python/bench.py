#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""bench.py —— 真实计时（动态分析的第 ③ 步）。

**测什么**：按 `benchmodel` 造出数据，把函数跑 N 次，取**中位数**纳秒。

## 为什么用中位数

一次异常调度会把平均值拉偏，中位数不会。**我们要的是"通常多快"，不是"平均多快"。**

## 替换法（§7.8.2）

候选默认 `inline` 在宿主里，不能单独跑。所以测法是：
**同一份宿主、同一份数据，只换被测的那个候选**（`force={标签: 候选}`）。

## 内置生成器

`benchmodel.data` 里的生成器在真实项目里应由标准库提供；M1 阶段先内置最小集：

| 名字 | 生成什么 |
|---|---|
| `sorted_u64s(n)` | 升序的 n 个 u64 |
| `random_u64s(n)` | 伪随机的 n 个 u64（由 n 定种子，可复现） |
| `make_u8s(n)` | n 个 0..255 |
| `make_texts(n)` | n 个数字字符的字节数组 |
| `any_u64()` | 单个 u64 |

**确定性**：同一个 n 永远生成同一份数据（MO-3 / MB-3 的要求）。
"""

import random
import time
from typing import Any, Callable, Dict, List, Optional

from interp import Interp
from parser import Node


# ---------------------------------------------------------------- 内置生成器

def _rng(seed: int, n: int) -> random.Random:
    """由 (seed, n) 决定随机源。

    **`seed` 来自 benchmodel 的 `seed = <整数>;`（可省，默认 0）。**
    以前这里硬编码 `0x5EED + n`，导致 `seed` 字段写了等于没写——**那是装饰性字段**。
    """
    return random.Random((seed & 0xFFFFFFFF) ^ (n * 0x9E3779B1))


def sorted_u64s(seed: int, n: int) -> List[int]:
    r = _rng(seed, n)
    return sorted(r.randrange(0, 1 << 32) for _ in range(n))


def random_u64s(seed: int, n: int) -> List[int]:
    r = _rng(seed, n)
    return [r.randrange(0, 1 << 32) for _ in range(n)]


def make_u8s(seed: int, n: int) -> List[int]:
    r = _rng(seed, n)
    return [r.randrange(0, 256) for _ in range(n)]


def make_texts(seed: int, n: int) -> List[int]:
    r = _rng(seed, n)
    return [ord("0") + r.randrange(0, 10) for _ in range(n)]


#: 内置生成器：名字 → (seed, n) -> 值。
#: **MB-7 下每个都必须在 `.tu` 里有对应 `spec`**（见 checker 的 E-Bench-BadGenerator）。
def any_u64(seed: int, n: int) -> list:
    """`any_u64()` —— **单个** u64（不是序列）。

    为什么需要它：`benchmodel.data` 里**标量形参**（`k`、`dim`、`width`）
    没有生成器可写——四个内置生成器都产出序列。
    没有它，"有标量形参的函数"就无法写合法的 `data`，也就无法测量。
    """
    import random as _r
    g = _r.Random((seed << 8) ^ n ^ 0x9E37)
    return [g.getrandbits(32)]


BUILTIN = {
    "sorted_u64s": sorted_u64s,
    "random_u64s": random_u64s,
    "make_u8s": make_u8s,
    "make_texts": make_texts,
    "any_u64": any_u64,
}


# ---------------------------------------------------------------- benchmodel 解析

def _desc(n: Optional[Node], kind: str, acc=None) -> List[Node]:
    if acc is None:
        acc = []
    if n is None:
        return acc
    if n.kind == kind:
        acc.append(n)
    for c in n.children:
        _desc(c, kind, acc)
    return acc


def read_benchmodel(decl_mod: Node):
    """返回 (data, scale, seed)。data: {参数: 生成器名}；scale: {参数: [点]}。"""
    from checker import _eval_range_points
    data: Dict[str, str] = {}
    scale: Dict[str, List[int]] = {}
    bms = _desc(decl_mod, "benchmodel")
    if not bms:
        return data, scale, 0
    bm = bms[0]
    for b in _desc(bm, "bench-data"):
        for bind in b.children:
            if bind.kind != "binding":
                continue
            gen = bind.children[0] if bind.children else None
            name = None
            if gen is not None and gen.kind == "call":
                name = gen.name
            elif gen is not None and gen.kind == "path":
                name = gen.name
            elif gen is not None and gen.kind == "lit-int":
                # 字面量：`k = 32;` —— 标量形参最诚实的写法
                name = "#" + str(gen.value)
            data[bind.name] = name or ""
    for b in _desc(bm, "bench-scale"):
        for bind in b.children:
            if bind.kind != "scale-binding":
                continue
            pts, _err = _eval_range_points(bind.children[0] if bind.children else None)
            if pts:
                scale[bind.name] = pts
    seed = 0
    for sd in _desc(bm, "seed"):
        lit = sd.children[0] if sd.children else None
        if lit is not None and lit.kind == "lit-int":
            seed = int(lit.value)
    return data, scale, seed


# ---------------------------------------------------------------- 造数据

def make_args(data: Dict[str, str], sized: Dict[str, int], size: int,
              seed: int = 0) -> List[Any]:
    """按 data 生成器造一组实参。`sized` 里有的参数按 size 造，其余造最小规模。

    **顺序敏感**：`data` 的声明顺序 MUST 等于签名形参顺序（MB-13，由 checker 校验）。
    生成器可以引用**前面已造好**的参数（MB-5，如 `any_of(xs)`）——
    所以这里按 `data` 的插入顺序逐个求值，并把已造好的值放进 `bound`。
    """
    out = []
    bound: Dict[str, Any] = {}
    for pname, gen in data.items():
        n = size if pname in sized else 1
        if gen and gen.startswith("#"):
            # 字面量标量：原样给（`k = 32;` → 32），不随规模变——这是作者的选择
            try:
                lit = int(gen[1:])
            except ValueError:
                raise RuntimeError(f"benchmodel.data 里的字面量 {gen!r} 不是整数") from None
            out.append(lit)
            bound[pname] = lit
            continue
        if gen == "any_of":
            # 从前面已造好的容器里取一个元素；空容器 → T 的类型上界
            src = next(iter(bound.values()), None)
            out.append(any_of(src, seed))
            bound[pname] = out[-1]
            continue
        f = BUILTIN.get(gen)
        if f is None:
            raise RuntimeError(
                f"benchmodel.data 里的生成器 {gen!r} 不在内置集里"
                f"（内置只有 {sorted(BUILTIN)} 与 any_of）")
        val = f(seed, n)
        out.append(val)
        bound[pname] = val
    return out


#: 各整型的类型上界——`any_of` 在空容器上返回它。
#: **为什么是上界**：固定、可复现、且**必然不在容器里**，
#: 于是"未命中"这条路径也变成可测的了。
TYPE_CEILING = {
    "u64": (1 << 64) - 1,
    "i64": (1 << 63) - 1,
    "u32": (1 << 32) - 1,
    "i32": (1 << 31) - 1,
    "u8": 255,
    "i8": 127,
}


def any_of(container: Any, seed: int = 0, type_name: str = "u64") -> Any:
    """从容器里按 seed 取一个元素；容器为空 → 返回 `T` 的类型上界。

    **这就是「空数组也要能测」的落地方式**：
    不是给 `any_of` 加一条特例规则，而是把"取不到"统一处理成一个**越界哨兵**。
    """
    if container is None:
        return TYPE_CEILING.get(type_name, TYPE_CEILING["u64"])
    seq = list(container)
    if not seq:
        return TYPE_CEILING.get(type_name, TYPE_CEILING["u64"])
    r = random.Random((seed & 0xFFFFFFFF) ^ 0x5BF03635)
    return seq[r.randrange(0, len(seq))]


# ---------------------------------------------------------------- 测量

def measure(impl_mod: Node, decl_mod: Optional[Node], fn_name: str,
            sizes: List[int], repeats: int = 5,
            force: Optional[Dict[str, str]] = None,
            table_path: Optional[str] = None) -> Dict[int, float]:
    """跑 N 次，返回 {规模: 中位数纳秒}。"""
    data, scale_map, seed = (read_benchmodel(decl_mod) if decl_mod is not None
                             else ({}, {}, 0))
    if not data:
        raise RuntimeError("没有 benchmodel.data，无法造数据")

    # 哪些参数是规模驱动的：scale 里出现的那些
    sized = {p: 0 for p in scale_map} or {next(iter(data)): 0}

    out: Dict[int, float] = {}
    for size in sizes:
        args = make_args(data, sized, size, seed)
        samples = []
        for _ in range(max(3, repeats)):
            it = Interp(impl_mod, decl_mod, table_path=table_path)
            t0 = time.perf_counter_ns()
            it.call(fn_name, args, force=force)
            t1 = time.perf_counter_ns()
            samples.append(float(t1 - t0))
        samples.sort()
        out[size] = samples[len(samples) // 2]
    return out


if __name__ == "__main__":
    import os
    import sys
    from parser import parse_source

    sys.stdout.reconfigure(encoding="utf-8")
    HERE = os.path.dirname(os.path.abspath(__file__))
    S = os.path.join(HERE, "..", "文档", "设计", "目前设计", "样例")

    for base, fn, label, cands in (
        ("sum", "average", "sum_kernel", ["sum_by_index", "sum_by_iter"]),
        ("search", "binary_search", "search_impl", ["search_linear", "search_binary"]),
    ):
        tu = parse_source(open(os.path.join(S, base + ".tu"), encoding="utf-8").read(),
                          base + ".tu").module
        ch = parse_source(open(os.path.join(S, base + ".eln"), encoding="utf-8").read(),
                          base + ".eln").module
        print(f"=== {base}.{fn} ===")
        for cand in cands:
            m = measure(ch, tu, fn, [16, 64, 256], force={label: cand})
            print("  " + cand.ljust(16) + "  " +
                  "  ".join(f"n={k}: {v:,.0f} ns" for k, v in m.items()))
