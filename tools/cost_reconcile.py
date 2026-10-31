#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""cost_reconcile.py —— **逐节点对账**：κ 的每一项 vs 解释器的 operations

## 为什么需要它

E35 是这么发现的：把 `?!` 的上界从"全候选取 max"改成"按分档表逐段取 max"之后，
`count` 的上界从 335,790,142 掉到 3,484，而实测操作数是 3,936 —— **上界不成立**。

要修，必须先回答：**是哪一个构造少算了？**

这个脚本把语言拆成最小的构造（循环 / 条件 / 下标读写 / `at` / 常量循环 / 嵌套循环），
对每个构造同时算 **κ** 和 **实测 operations**，打印差值：

- `差 > 0`（ops > κ）→ **κ 少算**，这是 bug；
- `差 < 0`（κ > ops）→ κ 保守，可以接受。

## 用法

```bash
python tools/cost_reconcile.py            # 全部构造
python tools/cost_reconcile.py --n 64
```
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

TU = "spec f(const Vec<u64>& xs) -> u64 { effects {}; }\n"

CASES = {
    "单循环 + at 读": """func f(const Vec<u64>& xs) -> u64 {
    u64 s = 0;
    u64 i = 0;
    while (i < xs.length()) {
        s = s + xs.at(i);
        i = i + 1;
    }
    return s;
}""",
    "单循环 + 下标读": """func f(const Vec<u64>& xs) -> u64 {
    u64 s = 0;
    u64 i = 0;
    while (i < xs.length()) {
        s = s + xs[i];
        i = i + 1;
    }
    return s;
}""",
    "单循环 + 下标写读": """func f(const Vec<u64>& xs) -> u64 {
    u64 i = 0;
    while (i < xs.length()) {
        xs[i] = xs[i] + 1;
        i = i + 1;
    }
    return 0;
}""",
    "单循环 + if": """func f(const Vec<u64>& xs) -> u64 {
    u64 s = 0;
    u64 i = 0;
    while (i < xs.length()) {
        if (xs.at(i) == 0) {
            s = s + 1;
        }
        i = i + 1;
    }
    return s;
}""",
    "常量循环 256": """func f(const Vec<u64>& xs) -> u64 {
    u64 s = 0;
    u64 j = 0;
    while (j < 256) {
        s = s + j;
        j = j + 1;
    }
    return s;
}""",
    "嵌套循环": """func f(const Vec<u64>& xs) -> u64 {
    u64 s = 0;
    u64 i = 0;
    while (i < xs.length()) {
        u64 j = 0;
        while (j < xs.length()) {
            s = s + xs.at(j);
            j = j + 1;
        }
        i = i + 1;
    }
    return s;
}""",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="κ 与 operations 逐构造对账")
    ap.add_argument("--n", type=int, default=16)
    a = ap.parse_args()

    from cost_check import evaluate
    from cost_interp import CostInterp
    from interp import Interp
    from parser import parse_source

    tu = parse_source(TU, "m.tu").module
    print(f"  {'构造':18} {'κ':>8} {'ops':>8} {'差(ops-κ)':>10}  结论")
    print("  " + "-" * 62)
    bad = 0
    for name, code in CASES.items():
        ch = parse_source(code, "m.eln").module
        ci = CostInterp(ch, tu)
        k = ci.cost_of("f")
        kv = evaluate(k, {v: a.n for v in k.variables()}) if k.variables() else evaluate(k, {})
        it = Interp(ch, tu)
        it.call("f", [[1] * a.n])
        # **口径**：κ 的单位是「语句 + 表达式节点 + 循环轮数」，
        # 对应解释器的 `evaluation_steps`（`operations` 少了轮数，别拿它比）。
        ops = it.counters()["evaluation_steps"]
        diff = ops - kv
        verdict = "κ 少算（bug）" if diff > 0 else "保守"
        bad += 1 if diff > 0 else 0
        print(f"  {name:18} {kv:>8,.0f} {ops:>8,} {diff:>10,.0f}  {verdict}")
    print()
    print(f"  n={a.n}；少算的构造数：{bad}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
