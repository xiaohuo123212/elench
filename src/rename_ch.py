#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""把实现文件的扩展名 `.eln` 改成 `.eln`（Elench）。

处理范围：
  · src/*.py            —— 工具与测试（含自动推导 `.eln` 的地方）
  · 文档/**/*.md        —— 规格、语法文档、材料（**排除归档目录**）
  · editors/vscode/**   —— 语法高亮与语言关联
  · 样例文件改名        —— *.ai → *.eln

不处理：
  · 归档目录（历史快照应保留原样）
  · `.eln` 作为独立英文词（如 "AI 生成"），只替换带点或带引号的文件名形态
"""

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

SKIP_DIRS = {".git", "_internal", "node_modules", ".perf", "归档"}
TEXT_EXT = {".py", ".md", ".json"}

# 只替换"文件名形态"的 .ai：前面是标识符/引号/斜杠，后面不是字母数字
PATTERNS = [
    (re.compile(r"([A-Za-z_][A-Za-z_0-9]*)\.ai\b"), r"\1.eln"),   # calculator.eln
    (re.compile(r"`\.ai`"), "`.eln`"),                             # 行内代码 `.eln`
    (re.compile(r"\[\.ai\]"), "[.eln]"),                           # 诊断前缀 [.eln]
    (re.compile(r'"\.ai"'), '".eln"'),                             # 字符串 ".eln"
    (re.compile(r"<模块\.ai>"), "<模块.eln>"),                     # 用法说明
]


def convert(text: str) -> str:
    s = text
    for rx, rep in PATTERNS:
        s = rx.sub(rep, s)
    return s


def main():
    changed = []
    for dirpath, dirnames, files in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in files:
            ext = os.path.splitext(f)[1]
            if ext not in TEXT_EXT:
                continue
            p = os.path.join(dirpath, f)
            try:
                raw = io.open(p, encoding="utf-8").read()
            except UnicodeDecodeError:
                continue
            new = convert(raw)
            if new != raw:
                io.open(p, "w", encoding="utf-8", newline="\n").write(new)
                changed.append(os.path.relpath(p, ROOT))

    # 样例文件改名
    sample = os.path.join(ROOT, "文档", "样例")
    for f in sorted(os.listdir(sample)):
        if f.endswith(".eln"):
            src = os.path.join(sample, f)
            dst = os.path.join(sample, f[:-3] + ".eln")
            os.replace(src, dst)
            changed.append(f"（改名）{f} → {os.path.basename(dst)}")

    for c in changed:
        print("  ", c)
    print(f"共 {len(changed)} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
