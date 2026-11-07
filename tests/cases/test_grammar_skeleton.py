#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_grammar_skeleton.py —— `elench grammar --format=skeleton`

Code4AI ①（借 Pel）的缺口：`.tu` 本身就是语法约束，
所以能直接生成"签名齐全、body 待填"的 `.eln` 空实现。

验收标准：生成的骨架**能过 check**——只报"没实现"一类，
不报语法/签名错。解析器都不认的骨架是没用的。
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


tmp = os.path.join(CASEDIR, "_tmp_skel")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp, exist_ok=True)
shutil.copy(os.path.join(ROOT, "文档", "样例", "sum.tu"), os.path.join(tmp, "sum.tu"))

r = subprocess.run([sys.executable, "-B", os.path.join(SRC, "dsh.py"), "grammar",
                    os.path.join(tmp, "sum.tu"), "--format=skeleton"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
out = (r.stdout or "") + (r.stderr or "")

print("[1] 骨架带签名、body 是 TODO")
check("有 func 签名", "func average(" in out, out[:120])
check("body 是 TODO", "TODO" in out, "")
check("没有实现代码", "return" not in out, "")

print("[2] 生成的骨架能过 check（只报没实现一类）")
io.open(os.path.join(tmp, "sum.eln"), "w", encoding="utf-8").write(out)
r2 = subprocess.run([sys.executable, "-B", os.path.join(SRC, "dsh.py"), "check", tmp],
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
o2 = (r2.stdout or "") + (r2.stderr or "")
check("没有解析错误", "E-Parse" not in o2, o2[:200])
check("没有签名错", "E-Spec-SigMismatch" not in o2, o2[:200])

shutil.rmtree(tmp, ignore_errors=True)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
