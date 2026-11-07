#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""exp_s1.py —— 实验 S1：κ 对 `OP_COST` 的弹性

## 对应哪一条

`实验-评价方案.md` §5 S1：横轴 `OP_COST`，纵轴 `κ / κ(基准)`，
预期"线性，斜率 = 常数项占比"。

## 口径

- `OP_COST` 是 `cost_domain/cost_interp.py` 里的模块级常量（默认 1），
  本脚本**在进程内改它再重算**，不改源码；
- 每个函数在固定 n 处求 κ 的数值（`cost_check.evaluate`）；
- 弹性 = κ(OP_COST=k) / κ(OP_COST=1)。

**注意**：改的是全局常量，必须在同一进程里按顺序跑完；跨进程无效。
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import cost_domain.cost_interp as CI          # noqa: E402
from cost_check import evaluate                # noqa: E402
from parser import parse_source               # noqa: E402

CASES = os.path.join(ROOT, "文档", "样例")
N = 16
TARGETS = [("sum", "average"), ("stats", "summarize"), ("search", "binary_search"),
           ("paren", "paren"), ("merge", "merge"), ("count", "count")]


def kappa_at(fn, ch, tu, n):
    ci = CI.CostInterp(ch, tu)
    k = ci.cost_of(fn)
    env = {v: n for v in k.variables()}
    return float(evaluate(k, env))


def main() -> int:
    loaded = {}
    for prog, fn in TARGETS:
        tu = parse_source(open(os.path.join(CASES, prog + ".tu"), encoding="utf-8").read(),
                          prog + ".tu").module
        ch = parse_source(open(os.path.join(CASES, prog + ".eln"), encoding="utf-8").read(),
                          prog + ".eln").module
        loaded[(prog, fn)] = (ch, tu)

    base = {}
    grid = (1, 2, 3)
    CI.OP_COST = 1
    for key, (ch, tu) in loaded.items():
        base[key] = kappa_at(key[1], ch, tu, N)

    print(f"  OP_COST 网格 {grid}，n={N}；弹性 = κ(k)/κ(1)")
    print(f"  {'函数':<22} " + " ".join(f"k={g:<8}" for g in grid))
    rows = []
    for key, (ch, tu) in loaded.items():
        cells = []
        for g in grid:
            CI.OP_COST = g
            v = kappa_at(key[1], ch, tu, N)
            ratio = v / base[key] if base[key] else None
            cells.append(f"{ratio:.3f}" if ratio else "-")
            rows.append((key[0], key[1], g, v, ratio))
        print(f"  {key[0] + '.' + key[1]:<22} " + " ".join(f"{c:<9}" for c in cells))

    out = os.path.join(ROOT, "文档", "设计", "归档", "实验-2026-11", "s1.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write("program,function,op_cost,kappa,elasticity\n")
        for prog, fn, g, v, r in rows:
            fh.write(f"{prog},{fn},{g},{v},{'' if r is None else r}\n")
    CI.OP_COST = 1
    print(f"\n  数据 → {os.path.relpath(out, ROOT)}（{len(rows)} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
