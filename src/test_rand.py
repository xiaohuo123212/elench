#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_rand.py —— `rand_u64()`（v0.71）

## 设计要点（规格附录 B.1）

| 项 | 规定 |
|---|---|
| 形式 | `rand_u64() -> u64`，**无参数** |
| 熵源 | **内部读时钟**，不暴露 `now_ms` |
| 复现 | **`ELENCH_SEED`** 可覆盖 → 完全可复现 |
| 成本 | **O(1) 静态上界**（splitmix64 一步） |
| 效果 | **`nondet`**：调用方 MUST 声明 |
| 并行 | 每任务一条**独立且确定**的流（`(基种子, 任务下标)` 导出） |

## 一句话

> **`rand_u64()` 承诺"花几个操作"，不承诺"返回什么数"。**
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stdlib                                     # noqa: E402
from checker import check_module                   # noqa: E402
from cost_check import evaluate                    # noqa: E402
from cost_interp import CostInterp                 # noqa: E402
from interp import Interp                          # noqa: E402
from parser import parse_source                    # noqa: E402

FAILED = []
T = "target { time: { max: O(1) }, allocation: { max: 0 }, };\n"


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def main() -> int:
    print("=== 可复现：同一 ELENCH_SEED ===")
    os.environ["ELENCH_SEED"] = "42"
    stdlib.reset_base()
    a = [stdlib._rand_u64([]) for _ in range(3)]
    stdlib.reset_base()
    b = [stdlib._rand_u64([]) for _ in range(3)]
    check("同种子 → 同序列", a == b, f"{a} vs {b}")
    check("序列确实在变（不是常数）", len(set(a)) == 3, str(a))

    print("=== 换种子就不同 ===")
    stdlib.reset_base(7)
    c = [stdlib._rand_u64([]) for _ in range(3)]
    check("不同种子 → 不同序列", a != c, f"{a} vs {c}")

    print("=== 并行：每任务独立且确定的流 ===")
    stdlib.set_task_stream(42, 0)
    t0 = [stdlib._rand_u64([]) for _ in range(2)]
    stdlib.clear_task_stream()
    stdlib.set_task_stream(42, 0)
    t0b = [stdlib._rand_u64([]) for _ in range(2)]
    stdlib.clear_task_stream()
    check("同 (种子, 下标) → 同序列", t0 == t0b, f"{t0} vs {t0b}")

    stdlib.set_task_stream(42, 1)
    t1 = [stdlib._rand_u64([]) for _ in range(2)]
    stdlib.clear_task_stream()
    check("不同任务下标 → 不同流", t0 != t1, f"{t0} vs {t1}")

    print("=== 效果：必须声明 nondet ===")
    tu_bad = f"spec f(const Vec<u64>& xs) -> u64 {{ effects {{}}; {T}}}\n"
    tu_ok = f"spec f(const Vec<u64>& xs) -> u64 {{ effects {{ nondet }}; {T}}}\n"
    body = "func f(const Vec<u64>& xs) -> u64 { return rand_u64(); }\n"
    check("不声明 → E-Effect-Missing",
          "E-Effect-Missing" in [d.code for d in check_module(tu_bad, body, "m").diagnostics])
    check("声明了 → 通过",
          [d.code for d in check_module(tu_ok, body, "m").diagnostics] == [])

    print("=== 成本：O(1) ===")
    tu = parse_source(tu_ok, "m.tu").module
    ch = parse_source(body, "m.eln").module
    k = CostInterp(ch, tu).cost_of("f")
    check("κ 与 n 无关", "n(" not in k.render(), k.render())
    check("κ 是常数", evaluate(k, {"xs": 16}) == evaluate(k, {"xs": 4096}), k.render())

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
