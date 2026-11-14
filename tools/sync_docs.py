#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""tools/sync_docs.py —— 兼容入口（真身：`src/sync_docs.py`）。

文档、CONTRIBUTING 与 CI 习惯写 `python tools/sync_docs.py --check`，而实现住在
`src/`（跟其它 `src/*.py` 一起被打进 pyz 与编辑器镜像）。这里**只转发、不复制逻辑**：
两份副本早晚会漂移，而"副本漂移"正是这个工具本身要防的事。

用法与退出码同 `src/sync_docs.py`：

    python tools/sync_docs.py            # 就地同步
    python tools/sync_docs.py --check    # 过期或 include 解析失败 → exit 1
    python tools/sync_docs.py --list     # 列出所有 include
"""
import os
import runpy

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(os.path.dirname(HERE), "src", "sync_docs.py")

# `run_name="__main__"` 让目标文件执行它自己的 `if __name__ == "__main__"`
# 分支（含 `sys.exit(main(...))`），所以参数与退出码原样穿透。
runpy.run_path(TARGET, run_name="__main__")
