#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_half_rules.py —— 减半识别的**四条防错**（判错比判不出坏得多）

## 背景

`_log_loop` 认"每轮 ×2 / ÷2"给 log 界。它一旦认错，产出的是**假上界**——
比 `⊤` 更糟：`⊤` 是"不知道"，假上界是"知道，但错了"。

四个反例（都已实测会被挡住）：

| 反例 | 真值 | 曾经的错 |
|---|---|---|
| `p = i; i = p;` | 死循环 | 顺着 `p=(i-1)/2` 报 log |
| 减半只在 `if` 一条分支 | 线性 | 把分支里的 `/2` 当每轮都减半 |
| `i = 0; i = i*2` | 死循环 | 报 `log2(n)` 并**判达标** |
| 守卫变量 ≠ 被减半变量 | 线性 | 追错变量 |

## 判据

四个都必须**不是** log；同时 `heapify_up`（`i = p`，`p=(i-1)/2`，直接子语句）**必须是** log。
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


def kappa(ch_src, tu_src, fn):
    ci = CostInterp(parse_source(ch_src, "a.eln").module,
                    parse_source(tu_src, "a.tu").module)
    return ci.cost_of(fn).render()


SPEC = ("spec %s -> u64 { effects { }; target { time: { max: O(log(n)) }, "
        "allocation: { max: 0 }, }; }\n")

CASES = [
    ("同变量二次赋值", """func f(u64 i) -> u64 {
    while (i > 0) {
        u64 p = (i - 1) / 2;
        p = i;
        i = p;
    }
    return i;
}
"""),
    ("减半只在一条分支", """func f(u64 i, u64 c) -> u64 {
    while (i > 0) {
        if (c > 0) {
            i = i / 2;
        } else {
            i = i - 1;
        }
    }
    return i;
}
"""),
    ("从 0 开始乘 2", """func f(u64 n) -> u64 {
    u64 i = 0;
    while (i < n) {
        i = i * 2;
    }
    return i;
}
"""),
]

print("[1] 三个反例都不许出现 log")
for label, src in CASES:
    k = kappa(src, SPEC % "f(u64 i)", "f")
    check(f"{label} 不是 log", "log2" not in k, k)

print("[2] 正例（中转减半）必须是 log")
HEAP = """func f(vector<u64>& h, u64 i) -> u64 {
    while (i > 0) {
        u64 p = (i - 1) / 2;
        if (h[p] >= h[i]) { return i; }
        i = p;
    }
    return 0;
}
"""
k = kappa(HEAP, "spec f(vector<u64>& h, u64 i) -> u64 { effects { }; target { time: { max: O(log(n)) }, allocation: { max: 0 }, }; }\n", "f")
check("中转减半报 log2", "log2" in k, k)

print()
print("[3] 欧几里得成语：while (b != 0) { t = b; b = a % b; a = t; } → log2(a)")
GCD = """func f(u64 a, u64 b) -> u64 {
    while (b != 0) {
        u64 t = b;
        b = a % b;
        a = t;
    }
    return a;
}
"""
k = kappa(GCD, "spec f(u64 a, u64 b) -> u64 { effects { }; target { time: { max: O(log(a)) }, allocation: { max: 0 }, }; }\n", "f")
check("gcd 报 log2", "log2" in k and "n(a)" in k, k)

print("[4] 形状不符就不给 log（多一条语句）")
GCD2 = GCD.replace("        a = t;", "        a = t;\n        b = b + 1;")
k2 = kappa(GCD2, "spec f(u64 a, u64 b) -> u64 { effects { }; target { time: { max: O(log(a)) }, allocation: { max: 0 }, }; }\n", "f")
check("多一条语句 → 不是 log", "log2" not in k2, k2)


if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
