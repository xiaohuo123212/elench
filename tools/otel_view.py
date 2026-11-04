#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""otel_view —— **兼容入口**，实现已移到 `src/otel_view.py`。

为什么搬：它要能被 `elench otel ...` 直接调用，而 `tools/` 不进任何安装产物
（wheel / .pyz / .vsix / 便携包）。模块本身只依赖标准库，所以放 `src/` 没有代价。

**文档里的 `python tools/otel_view.py ...` 仍然可用**——本文件会转去执行 src 那份。
"""
import os
import runpy
import sys

_SRC = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
_TARGET = os.path.join(_SRC, "otel_view.py")

if __name__ == "__main__":
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)
    runpy.run_path(_TARGET, run_name="__main__")
