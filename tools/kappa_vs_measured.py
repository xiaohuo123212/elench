#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""kappa_vs_measured —— **兼容入口**，实现已移到 `src/kappa_vs_measured.py`。

为什么搬：`src/dsh.py` 的 `export --full` 在**运行期** import 这个模块。
它原本在 `tools/` 下，而 `tools/` 不进任何安装产物（wheel / .pyz / vsix / 便携包），
于是那些命令在装好的包里一跑就 `ModuleNotFoundError: No module named 'kappa_vs_measured'`。
模块本身只依赖 `src/` 里的东西（`bound` / `interp` / `bench` / `cost_interp` / `parser`），
所以它本来就该在 `src/`。

**文档里的 `python tools/kappa_vs_measured.py ...` 仍然可用**——本文件会转去执行 src 那份。
"""
import os
import runpy
import sys

_SRC = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
_TARGET = os.path.join(_SRC, "kappa_vs_measured.py")


def main() -> int:
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)
    runpy.run_path(_TARGET, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())