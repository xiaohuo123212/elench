#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_gate_total.py —— 门禁必须是**全函数**：`unknown` 不能等于放行

## 2026-11-06 修的两个洞

1. **`extern` 没进符号表**：调外部函数的程序一律报 `E-Name-Undefined`，
   而消息还在说"请在 .tu 的 extern 块登记"——已经登记了。
   修好后才能造出"能过 check、但 κ = ⊤"的模块。
2. **`build --deny-debt` 遇到 unknown 只提示、exit 0**：
   等于"不知道"被当成"没问题"。上界语言最不能出的错就是这个。

现在：`--deny-debt` 同样拒绝 unknown；不带开关仍然是**渐进采用**（exit 0）。
"""
import io
import os
import shutil
import subprocess
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


import tempfile
tmp = tempfile.mkdtemp(prefix="elench_gate_")

# κ = ⊤ 的模块：循环上界来自外部函数，推不出
io.open(os.path.join(tmp, "g.eln"), "w", encoding="utf-8").write(
    'extern mystery(u64 x) -> u64 from "kernel32" effects { env };\n'
    "\n"
    "func probe(u64 x) -> u64 {\n"
    "    unsafe {\n"
    "        u64 i = 0;\n"
    "        while (i < mystery(x)) {\n"
    "            i = i + 1;\n"
    "        }\n"
    "        return i;\n"
    "    }\n"
    "}\n")
io.open(os.path.join(tmp, "g.tu"), "w", encoding="utf-8").write(
    'extern mystery(u64 x) -> u64 from "kernel32" effects { env };\n'
    "spec probe(u64 x) -> u64 { effects { env, unsafe }; "
    "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n")


def _build(*flags):
    r = subprocess.run([sys.executable, "-B", os.path.join(SRC, "dsh.py"),
                        "build", os.path.join(tmp, "g"), *flags],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


print("[1] `extern` 能被检查器认到（以前报 E-Name-Undefined）")
rc_chk, out_chk = _build("--deny-debt")
check("没有 E-Name-Undefined", "E-Name-Undefined" not in out_chk, out_chk[:200])

print("[2] 门禁全函数：unknown 也要拒绝")
rc, out = _build("--deny-debt")
check("--deny-debt 遇到 unknown → exit 1", rc == 1, f"rc={rc}")
check("说清是「无法判定」", "无法判定" in out, out[:200])

print("[3] 不带开关仍然渐进采用")
rc0, _ = _build()
check("不带开关 → exit 0", rc0 == 0, f"rc={rc0}")

shutil.rmtree(tmp, ignore_errors=True)

print("[4] 门禁是**维度表**驱动的：加一维不用改门禁本体")
import io as _io
_src = _io.open(os.path.join(SRC, "dsh.py"), encoding="utf-8").read()
check("有 _GATE_DIMS 维度表", "_GATE_DIMS" in _src, "")
check("time 与 alloc 都在表里", '"time"' in _src and '"alloc"' in _src, "")

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
