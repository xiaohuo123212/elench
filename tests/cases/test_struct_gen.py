#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_struct_gen.py —— `structs(T)` 生成器（E14）

## 为什么需要

内置生成器原先只有整数序列（`random_u64s` / `make_u8s` / `make_texts` /
`sorted_u64s` / `any_u64`），于是**参数是 `vector<结构体>` 的函数根本测不了**：
`benchmodel.data` 里写什么名字都报「不在内置集里」。

现在 `pts = structs(Point);` 能造 `vector<Point>`。

## 边界（故意的）

**只填整数字段**。字段类型是 `vector`/字符串/结构体时**直接报错，不猜**——
猜出来的数据会让测量结论失去意义。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source      # noqa: E402
import bench                          # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


TU = """struct Point { x: u64, y: u64, }

spec total(const vector<Point>& pts) -> u64 {
    effects {};
    target { time: { max: O(1) }, allocation: { max: 0 }, };
    benchmodel {
        data { pts = structs(Point); }
        scale { pts in range(8, 64, *2); }
        seed = 0;
    }
}
"""

TU_BAD = """struct Bad { name: vector<u8>, }

spec total(const vector<Bad>& xs) -> u64 {
    effects {};
    target { time: { max: O(1) }, allocation: { max: 0 }, };
    benchmodel {
        data { xs = structs(Bad); }
        scale { xs in range(8, 64, *2); }
        seed = 0;
    }
}
"""

print("[1] 整数字段：能造出 vector<Point>，长度随 scale 变")
tu = parse_source(TU, "p.tu").module
data, scale, seed = bench.read_benchmodel(tu)
check("读到生成器", data.get("pts", "").startswith("structs:Point"), data)
args = bench.make_args(data, {p: 0 for p in scale} or {"pts": 0}, 8, seed)
check("造出 8 个元素", len(args[0]) == 8, len(args[0]) if args else None)
first = args[0][0]
check("元素是 StructVal", type(first).__name__ == "StructVal", first)
check("字段齐全", set(first.fields) == {"x", "y"}, first.fields)

print("[2] 非整数字段：明确报错，不猜")
tu_bad = parse_source(TU_BAD, "b.tu").module
data2, scale2, seed2 = bench.read_benchmodel(tu_bad)
try:
    bench.make_args(data2, {"xs": 0}, 8, seed2)
    check("应当报错", False, "没有报错")
except RuntimeError as e:
    check("报错信息说清是哪个字段", "字段 name" in str(e), str(e))

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
