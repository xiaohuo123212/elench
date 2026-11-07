#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_borrow_br3.py —— 钉住 BR-3「方法返回引用默认借自接收者」的现状

## 为什么需要这个测试

`borrow.py` 用 `_base_name()`（取表达式里**第一个 path**）来判定"借自谁"。
这是个**启发式**，文档里却同时存在两种说法：

- `borrow.py` 的注释表写 BR-3 ✅；
- `设计缺口-梳理.md` 写"仅 BR-3 未做"。

**两边说的不是一件事**：代码说的是"调用点能猜出借的是谁"，
文档说的是"类型侧没有记录归属"。

这个测试钉住**当前实际行为**，好让将来改动有对照。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source        # noqa: E402
import borrow                          # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def diags(ch_src, tu_src="spec caller() -> u64 { effects { }; target { time: { max: O(1) }, allocation: { max: 0 }, }; }"):
    ch = parse_source(ch_src, "a.eln").module
    tu = parse_source(tu_src, "a.tu").module
    return borrow.check_borrows(tu, ch)      # [(code, msg, line, col), ...]


print("[1] 直接借容器元素，再改容器 → 报冲突")
SRC1 = """func caller() -> u64 {
    vector<u64> xs = [1,2,3];
    const u64& r = xs.at(0);
    xs.push(4);
    return r;
}
"""
try:
    ds = diags(SRC1)
    codes = [d[0] for d in ds]
    check("报 E-Borrow-Conflict", any("Borrow-Conflict" in str(c) for c in codes), codes)
except Exception as e:                                  # noqa: BLE001
    print(f"  (调用方式不同：{type(e).__name__}: {e})")

print("[2] 返回引用的函数只有一个引用参数 → 归属唯一（BR-4 不拦）")
SRC2 = """func head_ref(vector<u64>& xs) -> u64& {
    return xs.at(0);
}
func caller() -> u64 {
    vector<u64> v = [1,2,3];
    const u64& r = head_ref(v);
    v.push(9);
    return r;
}
"""
try:
    ds = diags(SRC2)
    codes = [d[0] for d in ds]
    check("报 E-Borrow-Conflict（借自 v）", any("Borrow-Conflict" in str(c) for c in codes), codes)
except Exception as e:                                  # noqa: BLE001
    print(f"  (调用方式不同：{type(e).__name__}: {e})")

print("[3] 两个引用参数且返回引用 → BR-4 直接拒绝")
SRC3 = """func pick_ref(vector<u64>& a, vector<u64>& b) -> u64& {
    return b.at(0);
}
func caller() -> u64 {
    vector<u64> x = [1];
    vector<u64> y = [2];
    const u64& r = pick_ref(x, y);
    return r;
}
"""
try:
    ds = diags(SRC3)
    codes = [d[0] for d in ds]
    check("报 E-Borrow-Ambiguous", any("Borrow-Ambiguous" in str(c) for c in codes), codes)
except Exception as e:                                  # noqa: BLE001
    print(f"  (调用方式不同：{type(e).__name__}: {e})")

print("[4] 结构体字段上的方法调用也算变异（`s.items.push` → `s` 被改）")
SRC4 = """func taker(Box& s) -> u64 {
    const u64& r = s.items.at(0);
    s.items.push(9);
    return r;
}
"""
TU4 = """struct Box {
    items: vector<u64>,
}
spec taker(Box& s) -> u64 { effects { }; target { time: { max: O(1) }, allocation: { max: 0 }, }; }
"""
try:
    ds = diags(SRC4, TU4)
    codes = [d[0] for d in ds]
    check("报 E-Borrow-Conflict", any("Borrow-Conflict" in c for c in codes), codes)
except Exception as e:                                  # noqa: BLE001
    print(f"  (调用方式不同：{type(e).__name__}: {e})")

print("[5] 所有会改容器的方法都必须被算作变异（漏一个就是漏报）")
for _m, _a in (("fill", "0"), ("extend", "xs"), ("resize", "5"), ("clear", ""),
               ("push", "9"), ("pop", ""), ("reverse", "")):
    _src = f"""func caller() -> u64 {{
    vector<u64> xs = [1,2,3];
    const u64& r = xs.at(0);
    xs.{_m}({_a});
    return r;
}}
"""
    _codes = [d[0] for d in diags(_src)]
    check(f"xs.{_m}() 报冲突", any("Borrow-Conflict" in c for c in _codes), _codes)

print("[6] 内建函数改第一个实参也算变异（swap(xs, i, j)）")
_codes = [d[0] for d in diags("""func caller() -> u64 {
    vector<u64> xs = [1,2,3];
    const u64& r = xs.at(0);
    swap(xs, 0, 1);
    return r;
}
""")]
check("swap(xs, 0, 1) 报冲突", any("Borrow-Conflict" in c for c in _codes), _codes)

print("[7] 可变引用（`u64& q`）也必须是借用——以前只扫 const-decl")
_codes = [d[0] for d in diags("""func caller() -> u64 {
    vector<u64> xs = [1,2,3];
    u64& q = xs.at(0);
    xs.push(9);
    return q;
}
""")]
check("u64& q 报冲突", any("Borrow-Conflict" in c for c in _codes), _codes)

print("[8] 拷贝不是借用（`u64 v = xs.at(0)` 不该报）")
_codes = [d[0] for d in diags("""func caller() -> u64 {
    vector<u64> xs = [1,2,3];
    u64 v = xs.at(0);
    xs.push(9);
    return v;
}
""")]
check("拷贝不报", not any("Borrow" in c for c in _codes), _codes)

print("[9] 传可变引用算修改；传只读引用不算（`const` 记在 param 上）")
_SRC9A = (
    "func take(vector<u64>& v) -> u64 { return 0; }\n"
    "func caller() -> u64 {\n"
    "    vector<u64> xs = [1,2,3];\n"
    "    const u64& r = xs.at(0);\n"
    "    take(xs);\n"
    "    return r;\n"
    "}\n")
_codes = [d[0] for d in diags(_SRC9A)]
check("take(xs) 报冲突", any("Borrow-Conflict" in c for c in _codes), _codes)

_SRC9B = (
    "func look(const vector<u64>& v) -> u64 { return 0; }\n"
    "func caller() -> u64 {\n"
    "    vector<u64> xs = [1,2,3];\n"
    "    const u64& r = xs.at(0);\n"
    "    look(xs);\n"
    "    return r;\n"
    "}\n")
_codes = [d[0] for d in diags(_SRC9B)]
check("look(xs) 不报", not any("Borrow" in c for c in _codes), _codes)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
