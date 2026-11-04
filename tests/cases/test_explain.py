#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_explain.py —— 超界归因（`elench cost --explain`）

## 它守的是什么

1. **硬约束**：开 `--explain` 的 κ 必须与不开时**完全一致**——归因是只读的。
2. **指得准**：`sort_small`（插入排序）的元凶必须是**第 7 行那个外层 while**，
   而不是"某个 n² 项"这种含糊说法。
3. **不许沉默**：拿不到行号或没有单一元凶时，必须**显式说**，不能输出空段。
4. **确定性**：同一输入两次输出逐字节相同（报告要能进 CI）。
"""
import os
import subprocess
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
DSH = os.path.join(SRC, "dsh.py")

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(args):
    r = subprocess.run([sys.executable, DSH] + args, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def kappa_lines(text):
    return [l.strip() for l in text.splitlines() if "κ ≤" in l]


def main() -> int:
    sm = os.path.join(ROOT, "demo", "repair", "v1", "sort_small")
    ex = os.path.join(ROOT, "文档", "入门", "例子", "pick")

    print("[1] 硬约束：--explain 不改变 κ")
    for m in (sm, ex):
        _r1, a = run(["cost", m])
        _r2, b = run(["cost", m, "--explain"])
        check(f"{os.path.basename(m)}：κ 一致", kappa_lines(a) == kappa_lines(b),
              f"{kappa_lines(a)[:1]} vs {kappa_lines(b)[:1]}")

    print("[2] 指得准：元凶是外层 while 那一行")
    _rc, out = run(["cost", sm, "--explain"])
    check("有「支配项」", "支配项" in out, out[-200:])
    check("有「元凶」", "元凶" in out)
    check("指向第 7 行", "第 7 行" in out, [l for l in out.splitlines() if "元凶" in l])
    check("带出源码片段 while", "while (i < xs.length())" in out)
    check("标注为循环构造", "循环" in out)

    print("[3] 最小修复：说出拿掉它能不能达标")
    check("有「最小修复」", "最小修复" in out)
    check("这条能达标（或明说不能）",
          "即可达标" in out or "没有单一元凶" in out, out[-200:])

    print("[4] 确定性：两次输出逐字节相同")
    _rc, out2 = run(["cost", sm, "--explain"])
    check("两次一致", out == out2)

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
