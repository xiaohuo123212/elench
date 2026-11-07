#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""bench.py —— 真实测量（动态分析的第 ③ 步）。

**测什么**：按 `benchmodel` 造出数据，把函数跑 N 次，取**中位数**纳秒。

## 两把尺子（实参构造共用一份）

| 函数 | 量 | 单位 | 和 κ 的关系 |
|---|---|---|---|
| `measure` | 墙钟时间 | 纳秒 | **不同单位**：只能归一化后比斜率（`--timing`） |
| `measure_ops` | `interp` 的操作数计数器 | 操作 | **同单位**：可直接判 `实测 ≤ κ`（默认） |

两者的实参都来自 `_bench_plan` + `_build_args`——**夹具只有一套**，
否则两个口径量的不是同一个输入。

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
BUILTIN = {
    "sorted_u64s": sorted_u64s,
    "random_u64s": random_u64s,
    "make_u8s": make_u8s,
    "make_texts": make_texts,
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
            if gen is not None and gen.kind == "call" and (gen.name or "") == "structs":
                # **E14**：`pts = structs(Point);` —— 造 `Vec<Point>`。
                # 字段表在这里一次解析完并编码进名字里，`make_args` 就不用再拿 decl_mod。
                _sn = None
                _args = next((c for c in gen.children if c.kind == "args"), None)
                if _args is not None and _args.children:
                    _sn = _args.children[0].name
                _fields = []
                for _st in _desc(decl_mod, "struct"):
                    if _st.name != _sn:
                        continue
                    for _f in _st.children:
                        if _f.kind != "field":
                            continue
                        _ty = _f.children[0].name if _f.children else "?"
                        _fields.append("%s=%s" % (_f.name, _ty))
                data[bind.name] = "structs:%s:%s" % (_sn or "?", ",".join(_fields))
                continue
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
        # **逐参数尺寸**：`sized[pname]` 给了正数就用它，否则跟随 size。
        # 为什么需要：`lcs(a, b)` 的上界是 `a.len × b.len`，而原来只有**第一个**
        # 规模参数跟着 n 变，`b` 永远是 1 —— 于是 O(n²) 的样例被当成 O(n) 测，
        # 实测只有 23 次操作，比值 11614× 毫无意义。
        _explicit = sized.get(pname) if hasattr(sized, "get") else None
        n = _explicit if _explicit else (size if pname in sized else 1)
        if gen and gen.startswith("#"):
            # 字面量标量：原样给（`k = 32;` → 32），不随规模变——这是作者的选择
            try:
                lit = int(gen[1:])
            except ValueError:
                raise RuntimeError(f"benchmodel.data 里的字面量 {gen!r} 不是整数") from None
            # **标量形参也能被 `scale` 驱动**（2026-11-07 修）。
            # `data` 里的字面量是"默认值"；一旦这个参数也在 `scale` 里出现
            # （`k in range(1, 64, *2)`），就按当前规模取值。
            # 以前字面量永远赢，于是"参数是数字"的函数**规模列在变、参数不变**，
            # 表是假的（`blur` 实测 7 行 κ/实测 全相同）。
            val = size if pname in sized else lit
            out.append(val)
            bound[pname] = val
            continue
        if gen == "any_of":
            # 从前面已造好的容器里取一个元素；空容器 → T 的类型上界
            src = next(iter(bound.values()), None)
            out.append(any_of(src, seed))
            bound[pname] = out[-1]
            continue
        if gen.startswith("structs:"):
            # **E14**：造 `Vec<T>`，T 是 .tu 里声明的结构体。
            # **只填整数字段**：别的类型（Vec/字符串/结构体）不猜，直接报错。
            from interp import StructVal
            _parts = gen.split(":", 2)
            _sn = _parts[1]
            _fs = [x for x in (_parts[2] if len(_parts) > 2 else "").split(",") if x]
            if not _fs:
                raise RuntimeError(
                    "benchmodel.data 里的 structs(%s)：找不到这个结构体或它没有字段" % _sn)
            _rnd = random.Random((seed & 0xFFFFFFFF) ^ 0x71F3A0D1)
            _vals = []
            for _f in _fs:
                _fname, _, _fty = _f.partition("=")
                if _fty not in ("u64", "u32", "i64", "i32", "u8", "i8"):
                    raise RuntimeError(
                        "benchmodel.data 里的 structs(%s)：字段 %s 的类型 %r 不是整数，"
                        "生成器不会替你猜（E14）" % (_sn, _fname, _fty))
                _cap = 1000 if _fty in ("u64", "i64", "u32", "i32") else 200
                _vals.append((_fname, _rnd.randrange(0, _cap)))
            out.append([StructVal(_sn, dict(_vals)) for _ in range(max(1, n))])
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

def _bench_plan(decl_mod: Optional[Node]):
    """造实参所需的上下文：`(data, sized, seed)`。

    **`measure` 与 `measure_ops` 必须共用这一份**：一个测纳秒、一个测操作数，
    量的是同一个函数，输入也必须是同一组（否则两个口径对不上）。
    `sized` = 规模驱动的参数：`scale` 里出现的那些；没有 `scale` 就取第一个参数。
    """
    data, scale_map, seed = (read_benchmodel(decl_mod) if decl_mod is not None
                             else ({}, {}, 0))
    if not data:
        raise RuntimeError("没有 benchmodel.data，无法造数据")
    sized = {p: 0 for p in scale_map} or {next(iter(data)): 0}
    return data, sized, seed


def _build_args(data: Dict[str, str], sized: Dict[str, int], size: int,
                seed: int) -> List[Any]:
    """按 benchmodel 造**某一档**的实参。

    **两个测量口径唯一的实参来源**：`measure`（纳秒）与 `measure_ops`（操作数）
    都从这里取实参。换实现时**只许改这里**，不许各写一套夹具——
    夹具一分叉，两个口径比的就不是同一个输入了。
    """
    return make_args(data, sized, size, seed)


def measure(impl_mod: Node, decl_mod: Optional[Node], fn_name: str,
            sizes: List[int], repeats: int = 5,
            force: Optional[Dict[str, str]] = None,
            table_path: Optional[str] = None) -> Dict[int, float]:
    """跑 N 次，返回 {规模: 中位数纳秒}。"""
    data, sized, seed = _bench_plan(decl_mod)

    out: Dict[int, float] = {}
    for size in sizes:
        args = _build_args(data, sized, size, seed)
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


def measure_ops(impl_mod: Node, decl_mod: Optional[Node], fn_name: str,
                sizes: List[int],
                force: Optional[Dict[str, str]] = None,
                table_path: Optional[str] = None) -> Dict[int, int]:
    """跑一遍，返回 {规模: 操作数}。

    ## 为什么不再用纳秒

    κ 的单位是「操作」。拿纳秒去和 κ 比，只能靠**各自归一化**看斜率——
    那是"两条线长得像不像"，不是"上界成立不成立"。
    操作数和 κ **同单位**，所以可以直接判 `实测 ≤ κ`，**不需要归一化**。

    ## 口径必须与 `measure` 一致

    - 实参：同一个 `_bench_plan` + `_build_args`（**不是另写一套夹具**）；
    - 同一个 `Interp(impl_mod, decl_mod, table_path=...)`、同一份 `force`；
    - 每个规模点新建一个解释器、`ops_reset()` 后跑一次。

    **只跑一次**：操作数是确定性的（同一输入、同一分档表 → 同一个数），
    取中位数没有意义。函数是整棵调用树（含嵌套调用），与 κ(f) 的口径相同。
    """
    from interp import (ops_enable, ops_enabled, ops_of, ops_reset)

    data, sized, seed = _bench_plan(decl_mod)

    was_on = ops_enabled()
    out: Dict[int, int] = {}
    try:
        ops_enable(True)
        for size in sizes:
            args = _build_args(data, sized, size, seed)
            it = Interp(impl_mod, decl_mod, table_path=table_path)
            # **每次测量前清零**：计数器是进程级的，不清零就会把上一档累加进来
            ops_reset()
            it.call(fn_name, args, force=force)
            out[size] = ops_of(fn_name)
    finally:
        ops_enable(was_on)
    return out


if __name__ == "__main__":
    import os
    import sys
    from parser import parse_source

    sys.stdout.reconfigure(encoding="utf-8")
    HERE = os.path.dirname(os.path.abspath(__file__))
    S = os.path.join(HERE, "..", "文档", "样例")

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
