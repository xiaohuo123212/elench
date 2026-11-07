#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_mirror_in_sync.py —— `editors/vscode/python/` 必须与 `src/` 一致。

## 为什么必须有这条测试

VS Code 扩展把解释器打进 vsix，那份代码是 `src/` 的**副本**。
2026-11-07 审计发现：**13 个文件同名但内容不同**（其中 `dsh.py` 少 19 行、
`typenames.py` 多了已删的 `Deque`）——扩展里的解释器比 `src/` 旧，
而没有任何机制会提醒。这条测试就是那个提醒。

修法：`python tools/sync_mirror.py`
"""
import os
import subprocess
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
TOOL = os.path.join(ROOT, "tools", "sync_mirror.py")


def main() -> int:
    r = subprocess.run([sys.executable, TOOL, "--check"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode == 0:
        print("check  ok   ", r.stdout.strip().splitlines()[0])
        return 0
    print("check  FAIL  ", (r.stdout or r.stderr).strip()[:400])
    print("       修法：python tools/sync_mirror.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())
