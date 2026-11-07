#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_measure_incremental.py —— 分档表能装多个 label，且能只改其中一段

## 守的两件事

1. **一份表装多个 label**：以前写入器在每个 label 的循环里 `open(..., "w")`，
   第二个 label 会把第一个覆盖掉，而且**不报错**。
2. **按段合并**：`measure <模块> <choose名>` 只替换那一段，其余段逐字不变。
   不然 `git diff` 会糊成一片，增量重测就失去意义。
"""
import io
import os
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import dsh                                     # noqa: E402
from interp import load_dispatch_table         # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


tmp = tempfile.mkdtemp()
p = os.path.join(tmp, "demo.dispatch.yaml")

print("[1] 写入器：两个 label 都留下")
dsh._write_dispatch_table(p, "demo",
                          {"a": [(8, 4095, "by_scan")],
                           "b": [(1, 7, "by_iter")]}, False)
txt = io.open(p, encoding="utf-8").read()
check("a 段在", "label: a" in txt, txt)
check("b 段在", "label: b" in txt, txt)
tbl = load_dispatch_table(p)
check("读回两个 label", set(tbl) == {"a", "b"}, tbl)

print("[2] 按段合并：只换目标段，其余逐字不变")
before = io.open(p, encoding="utf-8").read()
dsh._merge_dispatch_table(p, "demo", "a", [(8, 999, "by_better")], False)
after = io.open(p, encoding="utf-8").read()
b_before = [l for l in before.split("\n") if "by_iter" in l]
b_after = [l for l in after.split("\n") if "by_iter" in l]
check("b 段逐字相同", b_before == b_after, (b_before, b_after))
check("a 段换成新候选", "by_better" in after, after)
check("module 行只有一条", after.count("module:") == 1, after)
check("合并后仍能读回", set(load_dispatch_table(p)) == {"a", "b"})

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
