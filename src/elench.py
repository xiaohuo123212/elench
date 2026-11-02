#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""elench —— Elench 工具链的**正式入口**。

历史原因实现写在 `dsh.py` 里；这里只做转接，保证：
  · `python src/elench.py …` 与安装后的 `elench …` 是同一条命令；
  · 老脚本里的 `python src/dsh.py …` 仍然可用（不破坏测试与工具）。
"""
import sys

from dsh import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
