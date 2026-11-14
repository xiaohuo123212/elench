#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_spec_draft.py —— `elench spec` 生成声明草案

## 设计要点

- **effects 能推**（读实现）；
- **target 不能推**——从实现推出来的上界会让「实现满足 spec」变成自证。
  所以草案里 target **只以注释形式出现**（`???`），并给出静态 κ 作参考，
  明确写"这是现状，不是承诺"。

空 target 的 spec 是**合法的**（check 会说「有实现但无 target」），
所以草案能编译、能被后续工具处理，只是 cost 会提示还没承诺。
"""
import io
import os
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


def _run(module_dir):
    r = subprocess.run([sys.executable, "-B", os.path.join(SRC, "dsh.py"), "spec", module_dir],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "") + (r.stderr or "")


tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_spec")
os.makedirs(tmp, exist_ok=True)
io.open(os.path.join(tmp, "t.eln"), "w", encoding="utf-8").write(
    "u64 foo(vector<u64>& xs) {\n    return xs.length();\n}\n")
io.open(os.path.join(tmp, "t.tu"), "w", encoding="utf-8").write("")

out = _run(os.path.join(tmp, "t"))

print("[1] 生成草案，且 target 只以注释出现")
check("有 spec 骨架", "spec foo(" in out, out[:120])
check("类型带泛型参数", "vector<u64>& xs" in out, out[:160])
check("target 是注释（???）", "// target" in out and "???" in out, "")
check("给了静态 κ 作参考", "静态 κ" in out, "")
check("明说 target 要自己定", "自证" in out or "要你自己定" in out, "")

print("[2] 每个函数都有 spec 时不生成")
io.open(os.path.join(tmp, "t.tu"), "w", encoding="utf-8").write(
    "spec foo(vector<u64>& xs) -> u64 { effects { }; }\n")
out2 = _run(os.path.join(tmp, "t"))
check("提示都有了", "每个函数都有 spec" in out2, out2[:120])

import shutil
shutil.rmtree(tmp, ignore_errors=True)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
