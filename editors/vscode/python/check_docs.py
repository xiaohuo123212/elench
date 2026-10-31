#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
check_docs.py —— 文档语法漂移检查

为什么需要它
------------
规格改了语法，代码和样例会立刻报错，**文档不会**——
文档里的代码示例只是文本，改错了没人知道。这个脚本把"文档里的语言"
当成也编译一遍：凡是出现旧写法的行，全部列出来。

用法
----
  python check_docs.py            # 全仓扫描，只报告
  python check_docs.py --fail     # 有命中就以非零退出（给 CI / 提交前跑）

豁免规则
--------
有些命中是**故意的**：语法变更历史里的对照、本文件的规则说明、标准文件里的反例。
这些行必须带下面的标记之一才放行，否则一律算漂移：

  〔旧〕  〔反例〕  〔已下线〕  〔历史〕
"""

import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

EXEMPT_MARKERS = ("〔旧〕", "〔反例〕", "〔已下线〕", "〔历史〕")

# 这些围栏标签里的代码不是本语言，跳过
OTHER_LANGS = {"rust", "cpp", "c++", "c", "python", "py", "dafny",
               "js", "javascript", "java", "go", "ts", "typescript", "sh"}

# (名字, 正则, 说明)
RULES = [
    ("旧 kernel 声明", re.compile(r"^\s*kernel\s+\w+\s*\("),
     "v0.66 起没有独立 kernel 声明；候选由 spec 里的 over 声明"),
    ("旧 pending 子句", re.compile(r"pending\s+\{"),
     "v0.66 起 pending 是 over 里的裸词标记"),
    ("choose 缺签名", re.compile(r"choose\s+\w+\s*\{"),
     "choose MUST 带参数表与返回类型：choose L(params) -> R {"),
    ("旧 let 绑定", re.compile(r"(?<![\w.`])let\s+[a-z_][a-z0-9_]*\s*[:=]"),
     "v0.65 起用 val"),
    ("旧 effects pure", re.compile(r"effects\s+pure\s*;"),
     "v0.62 起写 effects {};"),
    ("旧 objective 名", re.compile(r"minimize_(expected_time|p99|peak_mem|code_size)"),
     "v0.65 起用 time / p99 / mem / size"),
    ("旧 kernel fn", re.compile(r"kernel\s+fn\s"),
     "v0.62 起去掉 fn；v0.66 起 kernel 关键字整体下线"),
]

# 结构类检查：表格头后面必须紧跟分隔行
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP = re.compile(r"^\s*\|[:\s|-]+\|\s*$")

# 整份文件跳过
SKIP_FILES = {"_标准-语法变更-v0.66.md"}


def scan():
    hits = []
    for dirpath, _, files in os.walk(ROOT):
        # 归档目录是历史快照，本来就该保留旧写法，不参与"语法漂移"检查
        if any(seg in dirpath for seg in (".git", "_internal", "node_modules",
                                          ".perf", os.sep + "归档")):
            continue
        for f in files:
            if not f.endswith(".md") or f in SKIP_FILES:
                continue
            p = os.path.join(dirpath, f)
            try:
                text = io.open(p, encoding="utf-8").read()
            except UnicodeDecodeError:
                continue
            rel = os.path.relpath(p, ROOT)
            fence_tag = None
            for no, line in enumerate(text.split("\n"), 1):
                s = line.strip()
                if s.startswith("```"):
                    if fence_tag is None:
                        lang = s[3:].strip().lower()
                        fence_tag = "" if lang in OTHER_LANGS else None
                        if lang and lang not in OTHER_LANGS:
                            fence_tag = None
                        continue
                    else:
                        fence_tag = None
                        continue
                if fence_tag == "":
                    continue          # 外语代码块，不按本语言规则查
                if any(m in line for m in EXEMPT_MARKERS):
                    continue
                for name, rx, why in RULES:
                    if rx.search(line):
                        hits.append((rel, no, name, line.strip(), why))

            # 结构：表格头必须紧跟分隔行，否则表格会渲染错乱
            lines = text.split("\n")
            fence = False
            for no in range(len(lines) - 1):
                cur, nxt = lines[no], lines[no + 1]
                if cur.strip().startswith("```"):
                    fence = not fence
                    continue
                if fence:
                    continue
                if not TABLE_ROW.match(cur) or TABLE_SEP.match(cur):
                    continue
                # 只有"表头"才要求分隔行：表头 = 上一行不是表格行
                if no > 0 and TABLE_ROW.match(lines[no - 1]):
                    continue
                if TABLE_SEP.match(nxt):
                    continue
                if any(m in cur for m in EXEMPT_MARKERS):
                    continue
                hits.append((rel, no + 1, "表格头缺分隔行",
                             cur.strip()[:80],
                             "表格头后面必须紧跟 |---| 分隔行（否则表格会散架）"))
    return hits


def main(argv):
    hits = scan()
    if not hits:
        print("✓ 文档没有语法漂移")
        return 0

    by_file = {}
    for rel, no, name, line, why in hits:
        by_file.setdefault(rel, []).append((no, name, line, why))

    for rel in sorted(by_file):
        print(f"\n{rel}  ({len(by_file[rel])} 处)")
        for no, name, line, why in by_file[rel][:20]:
            shown = line if len(line) <= 90 else line[:87] + "..."
            print(f"  行{no:<5} [{name}] {shown}")
        print(f"    → {by_file[rel][0][3]}")

    total = len(hits)
    print(f"\n共 {total} 处，分布在 {len(by_file)} 个文件")
    print("若某处是故意的对照/反例，在该行加标记：" + " ".join(EXEMPT_MARKERS))
    return 1 if ("--fail" in argv and total) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
