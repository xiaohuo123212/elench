#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_cli_smoke.py —— **每个子命令都得有反应**。

## 为什么需要这条

2026-11-02 出过一次：`cmd_tightness` 被插进了 `_main_impl` 中间，父函数在那里就
结束了，后面 12 个 `if cmd == "...":` 全变成死代码。结果是：

- `py_compile` **通过**；
- 单元测试**全绿**；
- 但 `elench cost` / `build` / `export` … 全部**静默什么都不做**（rc=0、无输出）。

"没有输出"是这类 bug 唯一的共同特征。所以这条测试只认三件事：
**不崩（无 traceback）、有反应（有输出）、退出码合理。**
"""
import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
for _p in (SRC,):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import subprocess
import sys

_passed = 0
_failed = []

#: 所有子命令（跟 `_main_impl` 的分支链保持一致；新增命令要加进来）
COMMANDS = ["run", "check", "fmt", "highlight", "show", "parse", "cost",
            "dispatch", "measure", "stats", "header", "grammar", "cpp",
            "build", "export", "bench", "tightness"]


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def run(args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, os.path.join(SRC, "elench.py")] + args,
                       capture_output=True, encoding="utf-8", errors="replace",
                       env=env, timeout=180)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def test_every_command_reacts():
    print("[1] 每个子命令无参调用：不崩 + 有输出 + 退出码合理")
    for cmd in COMMANDS:
        rc, out, err = run([cmd])
        txt = out + err
        check("Traceback" not in txt, f"{cmd}：没有 traceback", txt[-120:])
        check(bool(txt.strip()), f"{cmd}：有输出（不是静默空转）", f"rc={rc}")
        check(rc in (0, 1, 2), f"{cmd}：退出码合理（{rc}）", str(rc))


def test_real_invocations():
    print("[2] 真实调用：tightness / cost 必须出内容")
    rc, out, err = run(["tightness", "samples/sum", "--sizes=16", "--runs=2"])
    check(rc == 0, "tightness 退出 0", f"rc={rc} {err[-100:]}")
    check("slack_typ" in (out + err), "tightness 输出含 slack_typ", (out + err)[:80])

    rc, out, err = run(["cost", "samples/sum"])
    check("κ" in (out + err) or "=" in (out + err), "cost 有实质输出",
          (out + err)[:80])


def main() -> int:
    test_every_command_reacts()
    test_real_invocations()
    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed[:10]:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
