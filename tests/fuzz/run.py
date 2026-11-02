#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""run.py —— 检测器的**唯一入口**：生成 → 判定 → 缩小 → 落盘。

    python tests/fuzz/run.py                 # 200 个程序
    python tests/fuzz/run.py --n 2000        # 大规模
    python tests/fuzz/run.py --n 200 --shrink

三个零件在同一个目录里：

    gen.py     生成器（四个程序族：loop / minmax / hof / rec）
    check.py   裁判（ops > κ 即反例）+ 覆盖率 + "测不出来"计数
    shrink.py  缩小器（整行删除，保留仍是反例的最小形态）

它们也可以单独跑；这个脚本只是把常用组合包成一条命令。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import check  # noqa: E402


def main() -> int:
    return check.main()


if __name__ == "__main__":
    raise SystemExit(main())
