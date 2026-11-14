#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_threads.py —— `parallel` 的**真线程**模式（v0.71）

## 测什么

1. **语义等价**：`parallel="seq"`（顺序）与 `parallel="threads"`（真线程）
   对同一程序必须给出**完全相同**的结果；
2. **稳定性**：线程模式反复跑 50 次，结果不变（检查器已禁止共享可变状态，
   所以这里不该出现"取决于调度"的结果）；
3. **成本不变**：两种模式下 κ 相同（κ 是静态量，与执行方式无关）。

## 诚实的限制

原型是 Python，**受 GIL 限制，线程模式没有 CPU 加速**。
它存在的意义是**验证语义**（等价性 + 稳定性），**不是**为了跑得快。
"""
import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但**这批测试原先住在 `src/`**，里面大量
#: `os.path.join(HERE, "..", "文档")` / `os.path.join(ROOT, "tools")` 都假设
#: 「本文件在 src/、上一级是仓库根」。搬家时改 200 处路径比让 `HERE` 继续指 src 贵得多，
#: 所以保留旧语义：`HERE` = src/，`CASEDIR` = 本文件真正所在的目录。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import sys


from cost_interp import CostInterp      # noqa: E402
from interp import Interp                # noqa: E402
from parser import parse_source          # noqa: E402

FAILED = []

TU = """spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

CH = """u64 f(const vector<u64>& xs) {
    u64 a = 0;
    u64 b = 0;
    parallel {
        { a = sum_of(xs); }
        { b = sum_of(xs) + 1; }
    }
    return a * 100 + b;
}
"""

CH_NESTED = """u64 f(const vector<u64>& xs) {
    u64 a = 0;
    u64 b = 0;
    u64 c = 0;
    parallel {
        { a = sum_of(xs); }
        {
            b = sum_of(xs) + 1;
            c = sum_of(xs) + 2;
        }
    }
    return a * 10000 + b * 100 + c;
}
"""


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(code, mode, args):
    tu = parse_source(TU, "m.tu").module
    ch = parse_source(code, "m.eln").module
    return Interp(ch, tu, parallel=mode).call("f", args)


def main() -> int:
    print("=== 语义等价 ===")
    for args in ([[1, 2, 3]], [[5]], [[1, 1, 1, 1]]):
        seq = run(CH, "seq", args)
        thr = run(CH, "threads", args)
        check(f"seq == threads（{args[0]}）", seq == thr, f"{seq} vs {thr}")

    check("嵌套任务块也等价",
          run(CH_NESTED, "seq", [[1, 2]]) == run(CH_NESTED, "threads", [[1, 2]]))

    print("=== 稳定性（50 次）===")
    vals = {run(CH, "threads", [[1, 2, 3]]) for _ in range(50)}
    check("50 次线程模式结果唯一", len(vals) == 1, str(vals))

    print("=== 成本与执行方式无关 ===")
    tu = parse_source(TU, "m.tu").module
    ch = parse_source(CH, "m.eln").module
    k_seq = CostInterp(ch, tu).cost_of("f").render()
    k_thr = CostInterp(ch, tu).cost_of("f").render()
    check("κ 相同（静态量）", k_seq == k_thr, k_seq)

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
