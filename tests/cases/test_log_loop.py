#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_log_loop.py —— L-01：每轮 ×2 / ÷2 的循环要给出 log2 界

## 修之前（实测）

    while (i < n) { i = i * 2; }   →  κ ≤ 2 + n(n)×9 + …     O(n)，**误拒**
    while (i > 1) { i = i / 2; }   →  κ ≤ 2 + n(i)×9 + …     与 target 的 n 不一致，**判不了**

## 修之后

两者都是 `log2(...)`，带 `O(log(n))` target 的程序判 **达成**。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source        # noqa: E402
from cost_interp import CostInterp      # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


CH = """u64 dbl(u64 n) {
    u64 i = 1;
    while (i < n) {
        i = i * 2;
    }
    return i;
}
u64 half(u64 n) {
    u64 i = n;
    while (i > 1) {
        i = i / 2;
    }
    return i;
}
"""
TU = """spec dbl(u64 n) -> u64 { effects { }; target { time: { max: O(log(n)) }, allocation: { max: 0 }, }; }
spec half(u64 n) -> u64 { effects { }; target { time: { max: O(log(n)) }, allocation: { max: 0 }, }; }
"""

ci = CostInterp(parse_source(CH, "a.eln").module, parse_source(TU, "a.tu").module)
k_dbl = ci.cost_of("dbl").render()
k_half = ci.cost_of("half").render()

print("[1] 两个循环的 κ 都含 log2")
check("倍增循环含 log2", "log2(" in k_dbl, k_dbl)
check("减半循环含 log2", "log2(" in k_half, k_half)

print("[2] 不再退化成 O(n)")
check("倍增不含 n(n)×9 那种线性项", "n(n)×9" not in k_dbl, k_dbl)
check("减半不再用 n(i)", "n(i)" not in k_half, k_half)

# ---------------------------------------------------------------- `<=` 多一圈
# **2026-11-10**：`while (i <= n)` 的圈数是 `floor(log2 n) + 1`，不是 `log2 n`。
# 修前 `u64 i = 1; while (i <= n) { i = i * 2; }` 在 **n = 1 时 κ = 13 < ops = 15**。
# `<` 那一侧（上面的 `dbl`）**必须逐字不变**：`log2(n)`，不许变成 `log2(n + 1)`。
print("[3] `<=` 比 `<` 多一圈，`<` 不变")
CH_LE = """u64 dbl_le(u64 n) {
    u64 i = 1;
    while (i <= n) {
        i = i * 2;
    }
    return i;
}
u64 dbl(u64 n) {
    u64 i = 1;
    while (i < n) {
        i = i * 2;
    }
    return i;
}
"""
TU_LE = """spec dbl_le(u64 n) -> u64 { effects { }; target { time: { max: O(log(n)) }, allocation: { max: 0 }, }; }
spec dbl(u64 n) -> u64 { effects { }; target { time: { max: O(log(n)) }, allocation: { max: 0 }, }; }
"""
_ci_le = CostInterp(parse_source(CH_LE, "b.eln").module,
                    parse_source(TU_LE, "b.tu").module)
k_le = _ci_le.cost_of("dbl_le").render()
k_lt = _ci_le.cost_of("dbl").render()
check("`i <= n` 的界是 log2(n + 1)（多一圈）", "log2(n + 1)" in k_le, k_le)
check("`i < n` 的界仍是 log2(n)（逐字不变）", "log2(n + 1)" not in k_lt and "log2(n)" in k_lt, k_lt)

from cost_check import evaluate as _eval                       # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset       # noqa: E402
import interp as _ip                                          # noqa: E402

_mod_le = parse_source(CH_LE, "b.eln").module
_old = _ip.ops_enable(True)
try:
    for _n in (1, 2, 3, 8, 16):
        ops_reset()
        Interp(_mod_le, None).call("dbl_le", [_n])
        _ops = ops_of("dbl_le")
        _kv = _eval(_ci_le.cost_of("dbl_le"), {"n": float(_n)})
        check(f"dbl_le(n={_n})：ops {_ops} ≤ κ {_kv:g}", _ops <= _kv,
              f"ops={_ops} κ={_kv} κ式={k_le}")
finally:
    _ip.ops_enable(_old)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
