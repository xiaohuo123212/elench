#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_diag_hints.py —— T-15：诊断动作表 + 自动修复的**幂等性**

## 钉住三件事

1. 有动作表的码，`diag_hints.py` 能打出来；
2. `--apply=N` 对 `E-Func-KeywordRemoved` 真的改对（`fn` → `func`）；
3. **幂等**：改完再跑一次，**文件一个字节都不变**。

**第 3 条是重点**：一个不幂等的 `--apply` 会把文件越改越乱。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(args):
    return subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tools", "diag_hints.py")] + args,
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")


tmp = tempfile.mkdtemp()
try:
    eln = os.path.join(tmp, "a.eln")
    tu = os.path.join(tmp, "a.tu")
    io.open(eln, "w", encoding="utf-8", newline="\n").write("fn bad() -> u64 { return 1; }\n")
    io.open(tu, "w", encoding="utf-8", newline="\n").write(
        "spec bad() -> u64 { effects { }; target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n")

    print("[1] 有动作表的码会打出动作")
    r = run([eln])
    check("E-Func-KeywordRemoved 有动作", "把 `fn` 改成 `func`" in r.stdout, r.stdout[-200:])

    print("[2] --apply 真的改了")
    run([eln, "--apply=1"])
    after1 = io.open(eln, encoding="utf-8").read()
    check("已变成 func", after1.lstrip().startswith("func "), after1)

    print("[3] **幂等**：再跑一次不改变文件")
    run([eln, "--apply=1"])
    after2 = io.open(eln, encoding="utf-8").read()
    check("文件字节不变", after1 == after2, f"{after1!r} vs {after2!r}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
