#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_effects.py —— 效果检查 + `requires` 断言（v0.71）

## 查的是两件事

1. **效果**：函数实际用到的效果 MUST ⊆ spec 声明的效果。
   这是"七处可撒谎位置"之外**唯一能自动验证**的契约维度——
   `print` 用了 `io`，而 `.tu` 写 `effects {}`，就是撒谎。
2. **`requires`**：入口断言。**调用方必须保证**，不成立就立刻失败，
   而不是让函数带着坏输入继续算。

## 一个必须记住的教训

样例里原本有 5 处 `requires`（`length() > 0` / `k > 0` / 维度匹配）
**和函数体自相矛盾**：体里明明 `return Err(...)` 处理空输入，前置又要求非空。
断言一开，这些矛盾全暴露了——**删掉前置，保留体里的错误处理**。
`requires` 只该写"调用方必须保证、而函数体**不打算处理**"的前提。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from checker import check_module          # noqa: E402
from interp import Interp, InterpError    # noqa: E402
from parser import parse_source           # noqa: E402

FAILED = []
T = "target { time: { max: O(1) }, allocation: { max: 0 }, };\n"


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def codes(tu, ch):
    return [d.code for d in check_module(tu, ch, "m").diagnostics]


def spec(effects="{}", extra=""):
    return f"spec f(const Vec<u64>& xs) -> u64 {{ effects {effects}; {extra}{T}}}\n"


def main() -> int:
    print("=== 效果检查 ===")
    check("纯函数 → 无诊断",
          codes(spec(), "func f(const Vec<u64>& xs) -> u64 { return 1; }\n") == [])
    check("用了 print 却没声明 io → E-Effect-Missing",
          "E-Effect-Missing" in codes(spec(), "func f(const Vec<u64>& xs) -> u64 { print(1); return 1; }\n"))
    check("声明了 io → 通过",
          codes(spec("{ io }"), "func f(const Vec<u64>& xs) -> u64 { print(1); return 1; }\n") == [])
    check("未知效果名 → E-Effect-Unknown",
          "E-Effect-Unknown" in codes(spec("{ telepathy }"),
                                      "func f(const Vec<u64>& xs) -> u64 { return 1; }\n"))
    check("经调用传递也要报 → E-Effect-Missing",
          "E-Effect-Missing" in codes(
              spec() + spec(),
              "func f(const Vec<u64>& xs) -> u64 { return g(xs); }\n"
              "func g(const Vec<u64>& xs) -> u64 { print(1); return 1; }\n"))

    print("=== requires 断言 ===")
    tu = parse_source(spec(extra="requires xs.length() > 0; "), "m.tu").module
    ch = parse_source("func f(const Vec<u64>& xs) -> u64 { return xs.at(0); }\n", "m.eln").module
    check("满足前提 → 正常返回", Interp(ch, tu).call("f", [[7, 8]]) == 7)
    raised = False
    try:
        Interp(ch, tu).call("f", [[]])
    except InterpError as exc:
        raised = "requires" in str(exc)
    check("违反前提 → 立刻失败（不是带着坏输入继续算）", raised)

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
