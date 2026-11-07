#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_generics_ok.py —— 泛型最小切片（第 1 步）

## 之前为什么用不了

- `.eln` 的 `func f<T>` 能解析；
- 但 `.tu` 的 `spec f<T>` **报 E-Parse-Expected**——声明与实现配不上对；
- 即使 spec 用具体类型，调用时 `T` 也不匹配任何实参（`E-Type-ArgMismatch`）。

## 现在

1. `spec f<T>` 能解析（类型参数与 `func f<T>` 同形状）；
2. 调用时**类型参数当通配符**——任何实参都算匹配。

**这是最小可用切片**：能写、能检查、能跑。
完整的统一（`T` 在多个位置必须一致）与成本口径还没做。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source                       # noqa: E402
from interp import Interp                              # noqa: E402
from typecheck import typecheck
from cost_interp import CostInterp                        # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


TU = """spec pick<T>(T a, T b) -> T { effects {}; target { time: { max: O(1) }, allocation: { max: 0 }, }; }
spec caller() -> u64 { effects {}; target { time: { max: O(1) }, allocation: { max: 0 }, }; }
"""

CH = """func pick<T>(T a, T b) -> T {
    return a;
}
func caller() -> u64 {
    return pick(3, 4);
}
"""

print("[1] `spec f<T>` 能解析（以前报 E-Parse-Expected）")
mod = parse_source(TU, "a.tu").module
check("spec 有 type-params 子节点",
      any(getattr(c, "kind", "") == "type-params"
          for it in mod.children if getattr(it, "name", "") == "pick"
          for c in getattr(it, "children", [])), "")

print("[2] 能过检查、能跑")
diags = typecheck(parse_source(TU, "a.tu").module,
                  parse_source(CH, "a.eln").module)
check("没有诊断", not diags, [d.code for d in diags])
it = Interp(parse_source(CH, "a.eln").module, None)
check("pick(3, 4) == 3", it.call("caller", []) == 3, it.call("caller", []))

print("[3] 类型变量要统一：`T` 不能同时是两种类型")
CH_BAD = """func pick<T>(T a, T b) -> T {
    return a;
}
func caller() -> u64 {
    return pick(3, "abc");
}
"""
_bad = typecheck(parse_source(TU, "a.tu").module,
                 parse_source(CH_BAD, "b.eln").module)
_codes = [d.code for d in _bad]
check("不一致的实参报 E-Type-ArgMismatch", "E-Type-ArgMismatch" in _codes, _codes)

print("[4] `T` 不需要单独的成本口径：κ 与类型无关")
TU2 = """spec gsum<T>(const vector<T>& xs) -> u64 { effects {}; target { time: { max: O(xs.length()) }, allocation: { max: 0 }, }; }
spec msum(const vector<u64>& xs) -> u64 { effects {}; target { time: { max: O(xs.length()) }, allocation: { max: 0 }, }; }
"""
CH2 = """func gsum<T>(const vector<T>& xs) -> u64 {
    u64 s = 0;
    u64 i = 0;
    while (i < xs.length()) {
        s = s + xs.at(i);
        i = i + 1;
    }
    return s;
}
func msum(const vector<u64>& xs) -> u64 {
    u64 s = 0;
    u64 i = 0;
    while (i < xs.length()) {
        s = s + xs.at(i);
        i = i + 1;
    }
    return s;
}
"""
_ci = CostInterp(parse_source(CH2, "c.eln").module, parse_source(TU2, "c.tu").module)
_kg = _ci.cost_of("gsum").render()
_km = _ci.cost_of("msum").render()
check("泛型与单态的 κ 完全相同", _kg == _km, f"{_kg} vs {_km}")

print("[5] 后端**生成 C++ 模板**（2026-11-06 起不再拒绝）")
import dsh as _dsh                                       # noqa: E402
_cpp = _dsh._gen_cpp(parse_source(TU, "a.tu").module, parse_source(CH, "a.eln").module)
check("生成 template <typename T>", "template <typename T>" in _cpp, _cpp[:200])
check("T 没有被退化成 uint64_t", "T pick(T a, T b)" in _cpp, _cpp[:200])
check("调用者在模板之后", _cpp.index("T pick(") < _cpp.index("uint64_t caller("), "")

print("[6] 泛型调泛型也能生成（C++ 隐式实例化，不需要我们推导）")
_SRC6 = (
    "func id<T>(T a) -> T { return a; }\n"
    "func pick<T>(T a, T b) -> T { return a; }\n"
    "func caller() -> u64 { return pick(id(3), 4); }\n")
_TU6 = (
    "spec id<T>(T a) -> T { effects { }; target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n"
    "spec pick<T>(T a, T b) -> T { effects { }; target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n"
    "spec caller() -> u64 { effects { }; target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n")
_cpp6 = _dsh._gen_cpp(parse_source(_TU6, "b.tu").module,
                      parse_source(_SRC6, "b.eln").module)
check("两个函数都生成模板", _cpp6.count("template <typename T>") == 2, _cpp6[:160])
check("id 在 pick 之前", _cpp6.index("T id(") < _cpp6.index("T pick("), "")
check("调用原样保留", "pick(id(3), 4)" in _cpp6, "")

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
