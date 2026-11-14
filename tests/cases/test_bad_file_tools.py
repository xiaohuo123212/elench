#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_bad_file_tools.py —— **语法错的文件**不能让工具抛 traceback。

## 为什么要这条测试

2026-11-08 一个"扮演 C++ 程序员"的探针发现：`elench fmt` 遇到语法错的文件
直接 `AttributeError: 'Diagnostic' object has no attribute 'msg'` —— 因为
`Diagnostic` 的字段叫 `message`，而两处打印用了 `_d.msg`。

**根因不是打错字，是"错误路径"从来没被测过**：正常文件永远走不到那个分支。
所以这里专门喂坏文件。
"""
import os
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
EL = os.path.join(ROOT, "src", "elench.py")

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def main() -> int:
    d = tempfile.mkdtemp(prefix="eln_bad_")
    # 故意的语法错：C 风格强转 `(u64)true`（本语言只收 `as`）。
    # `!true` 从 2026-11-10 晚起是合法写法（`!` 与 `not` 同义），不能再用。
    io = __import__("io")
    io.open(os.path.join(d, "m.eln"), "w", encoding="utf-8").write(
        "pub u64 main() {\n"
        "    if ((u64)true) { return 1; }\n"
        "    return 0;\n"
        "}\n")
    io.open(os.path.join(d, "m.tu"), "w", encoding="utf-8").write(
        "spec main() -> u64 {\n    effects {};\n    target { time: { max: O(1) }, };\n}\n")

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for args, label in ((["fmt", "m"], "fmt"), (["check", "m"], "check")):
        r = subprocess.run([sys.executable, EL] + args, cwd=d, env=env,
                           capture_output=True, encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        check(f"{label} 非零退出", r.returncode != 0, str(r.returncode))
        check(f"{label} 没有 traceback", "Traceback" not in out, out[-200:])
        check(f"{label} 给出了诊断码", "E-" in out, out[-200:])
    if FAILED:
        print(f"\n失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
