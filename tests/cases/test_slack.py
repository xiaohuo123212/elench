#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_slack.py —— L-08：`cost --explain` 的余量视图

## 口径

- 两边都是**数** → 给数字（`余量 N` / `超支 N`）；
- 任一边是**阶** → `余量 不适用`，**不编数字**：
  `O(n)` 对 `14n` 的余量是无穷，不是某个值。

## 硬约束

`--explain` 只加输出，**不改变 κ**。
"""
import os
import subprocess
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def cost_out(extra):
    p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "src", "dsh.py"),
                        "cost", "文档/样例/sum"] + extra,
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.stdout


print("[1] --explain 输出余量行")
out = cost_out(["--explain"])
check("有'余量'行", "余量" in out, out[-300:])

print("[2] 阶比较不给数字（time 维度）")
check("time 标'不适用'", "不适用" in out and "time" in out, out[-400:])

print("[3] --explain 不改变 κ")
a = [l for l in cost_out([]).splitlines() if "κ ≤" in l]
b = [l for l in out.splitlines() if "κ ≤" in l]
check("κ 表达式相同", a and b and a[0].strip() == b[0].strip(),
      (a[:1], b[:1]))

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
