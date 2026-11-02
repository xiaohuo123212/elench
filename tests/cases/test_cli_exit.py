#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_cli_exit.py —— CLI 退出码的护栏（§12）。

守两件事：
  1. `elench check` **有 error 就 exit 1**（以前人读模式恒 0，CI 会误绿）；
  2. `u64 y = 1.5;` 必须报 `E-Type-FloatToInt`（以前零诊断通过）。
"""
import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但**这批测试原先住在 `src/`**，里面大量
#: `os.path.join(HERE, "..", "文档")` / `os.path.join(ROOT, "tools")` 都假设
#: 「本文件在 src/、上一级是仓库根」。搬家时改 200 处路径比让 `HERE` 继续指 src 贵得多，
#: 所以保留旧语义：`HERE` = src/，`CASEDIR` = 本文件真正所在的目录。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import subprocess
import sys
import tempfile

_ok, _bad = 0, []


def check(cond, name, detail=""):
    global _ok
    if cond:
        _ok += 1
        print(f"  OK   {name}")
    else:
        _bad.append(name)
        print(f"  FAIL {name}  {detail}")


def run(args, cwd=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, os.path.join(HERE, "elench.py")] + args,
                       cwd=cwd or HERE, capture_output=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def write(d, tu, eln):
    with open(os.path.join(d, "m.tu"), "w", encoding="utf-8", newline="\n") as f:
        f.write(tu)
    with open(os.path.join(d, "m.eln"), "w", encoding="utf-8", newline="\n") as f:
        f.write(eln)


def main() -> int:
    TU = "spec f() -> u64 { effects {}; target { time: { max: 1 }, allocation: { max: 0 }, }; }\n"

    print("[1] check：有 error → exit 1，干净 → exit 0")
    d = tempfile.mkdtemp()
    write(d, TU, "func f() -> u64 {\n    u64 y = 1.5;\n    return y;\n}\n")
    rc, out = run(["check", os.path.join(d, "m.tu"), os.path.join(d, "m.eln")])
    check(rc == 1, "有 error 时 exit 1", f"rc={rc}")
    check("✗ 未通过" in out or "条错误" in out, "输出说明未通过", out[:120])

    print("[2] 浮点字面量赋给整数类型 → E-Type-FloatToInt")
    check("E-Type-FloatToInt" in out, "报出 E-Type-FloatToInt", out[:200])

    print("[3] 干净模块 → exit 0")
    write(d, TU, "func f() -> u64 {\n    return 7;\n}\n")
    rc, out = run(["check", os.path.join(d, "m.tu"), os.path.join(d, "m.eln")])
    check(rc == 0, "干净时 exit 0", f"rc={rc} {out[:120]}")
    check("✓ 通过" in out, "输出说通过", out[:120])

    print()
    if _bad:
        print(f"失败 {len(_bad)} / {_ok + len(_bad)}")
        for b in _bad:
            print("  -", b)
        return 1
    print(f"全部通过：{_ok} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
