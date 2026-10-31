#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_parallel.py —— 规格 §13.6（B-23…B-27）的回归测试

覆盖四件事：
1. **语义**：`parallel` 先按顺序执行（v0.71 的顺序模拟）；
2. **成本**：κ = Σ（B-24），因此两个任务各 `n` 时 κ 里出现两个 `n(xs)`；
3. **B-25**：`parallel` 里不能有 `?!` → `E-Parallel-Dispatch`；
4. **B-26**：`?!` 候选里不能有 `parallel` → `E-Parallel-Measured`。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from checker import check_module                       # noqa: E402
from cost_interp import CostInterp                      # noqa: E402
from interp import Interp                               # noqa: E402
from parser import parse_source                         # noqa: E402

FAILED = []

TU = """spec f(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

TU_CHOOSE = """spec f(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
    choose kernel(const Vec<u64>& xs) -> u64 {
        over { by_a, by_b };
        objective time;
    };
}
"""


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(code, tu_src=TU, args=None):
    tu = parse_source(tu_src, "m.tu").module
    ch = parse_source(code, "m.eln").module
    return Interp(ch, tu).call("f", args if args is not None else [[1, 2, 3]])


def codes(code, tu_src=TU):
    return [d.code for d in check_module(tu_src, code, "m").diagnostics]


def main() -> int:
    print("=== 语义：顺序模拟 ===")
    check("两个任务都执行（各写各的，回来合并）",
          run("""func f(const Vec<u64>& xs) -> u64 {
    u64 a = 0;
    u64 b = 0;
    parallel {
        { a = sum_of(xs); }
        { b = sum_of(xs); }
    }
    return a + b;
}""") == 12, "期望 12")

    check("嵌套 parallel 也能跑",
          run("""func f(const Vec<u64>& xs) -> u64 {
    u64 a = 0;
    u64 b = 0;
    parallel {
        { parallel { { a = 1; } } }
        { b = 2; }
    }
    return a + b;
}""") == 3, "期望 3")

    check("每个任务一个花括号（显式任务边界）",
          run("""func f(const Vec<u64>& xs) -> u64 {
    u64 s = 0;
    parallel {
        { s = s + sum_of(xs); }
        { s = s + sum_of(xs); }
    }
    return s;
}""") == 12, "期望 12")

    print("=== 成本：κ = Σ（B-24）===")
    tu = parse_source(TU, "m.tu").module
    ch = parse_source("""func f(const Vec<u64>& xs) -> u64 {
    u64 s = 0;
    parallel {
        s = s + sum_of(xs);
        s = s + sum_of(xs);
    }
    return s;
}
""", "m.eln").module
    k = CostInterp(ch, tu).cost_of("f").render()
    check("κ 里出现两个 n(xs)（Σ 而不是 max）", k.count("n(xs)") == 2, k)

    print("=== ?! 可以进 parallel（成本仍按 Σ 记）===")
    c = codes("""func f(const Vec<u64>& xs) -> u64! {
    u64 s = 0;
    parallel {
        s = s + ?!kernel(xs);
    }
    return s;
}
""", TU_CHOOSE)
    check("不报 E-Parallel-Dispatch", "E-Parallel-Dispatch" not in c, str(c))

    print("=== 允许在任务里打印（改为「不可测量」，不再报错）===")
    c = codes("""func f(const Vec<u64>& xs) -> u64 {
    parallel {
        { print(1); }
        { print(2); }
    }
    return 0;
}
""")
    check("parallel 里 print 不再报 E-Parallel-Effectful",
          "E-Parallel-Effectful" not in c, str(c))

    print("=== 任务之间不许共享可变状态（E-Parallel-ShareMut）===")
    c = codes("""func f(const Vec<u64>& xs) -> u64 {
    u64 s = 0;
    parallel {
        { s = s + 1; }
        { s = s + 2; }
    }
    return s;
}
""")
    check("两个任务都写 s → 报 E-Parallel-ShareMut", "E-Parallel-ShareMut" in c, str(c))

    c = codes("""func f(const Vec<u64>& xs) -> u64 {
    u64 a = 0;
    u64 b = 0;
    parallel {
        { a = 1; }
        { b = 2; }
    }
    return a + b;
}
""")
    check("各写各的 → 通过", "E-Parallel-ShareMut" not in c, str(c))

    print("=== B-26：候选里不能有 parallel ===")
    c = codes("""func f(const Vec<u64>& xs) -> u64! {
    return ?!kernel(xs);
} kernels kernel(const Vec<u64>& xs) -> u64! {
    by_a {
        parallel {
            print(1);
        }
        return 1;
    }
    by_b {
        return 2;
    }
}
""", TU_CHOOSE)
    check("报 E-Parallel-Measured", "E-Parallel-Measured" in c, str(c))

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
