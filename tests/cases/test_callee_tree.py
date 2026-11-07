#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_callee_tree.py —— L-07：报告里每一处子调用都能追溯到

## 判据（可执行版）

对 `文档/样例/` 里**每个**样例：函数体里调用到的**用户函数**，
都必须在 `cost --explain` 的「被调方」里出现（形如 `name()`）。

## 修之前

`merge` 的 `earlier` 不在报告里——因为它是**作为参数**传给
`sort_by(xs, earlier)` 的，不是 `call` 节点，扫描时整类漏掉。
`callee_lines` 现在会把高阶内建的 path 实参也算作被调方。
"""
import os
import re
import subprocess
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SAMPLES = os.path.join(ROOT, "文档", "样例")

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


names = sorted(f[:-4] for f in os.listdir(SAMPLES) if f.endswith(".eln"))
missing_total = []
for s in names:
    src = open(os.path.join(SAMPLES, s + ".eln"), encoding="utf-8").read()
    fns = set(re.findall(r"^func (\w+)", src, re.M))
    calls = set(re.findall(r"\b(\w+)\s*\(", src)) & fns
    p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "src", "dsh.py"),
                        "cost", os.path.join(SAMPLES, s), "--explain"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    miss = [c for c in sorted(calls) if f"{c}()" not in p.stdout]
    if miss:
        missing_total.append((s, miss))

print(f"[1] {len(names)} 个样例，每处用户函数调用都能在「被调方」里找到")
check("无缺失", not missing_total, missing_total)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
