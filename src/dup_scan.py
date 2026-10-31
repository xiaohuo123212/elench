#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
dup_scan.py —— 文档重复度扫描

为什么先量再删
--------------
"感觉重复很多"不能作为删的依据。这个脚本给出三类可核对的数字：

  1. **代码块重复**：同一段代码块出现在几个文件里
  2. **长句重复**：同一行（≥ 40 字符）出现在几个文件里
  3. **数字/事实重复**：版本号、测试数、"7 处"这类事实被几处各自维护

用法
----
  python dup_scan.py            # 全部
  python dup_scan.py --blocks   # 只看代码块
  python dup_scan.py --lines    # 只看长行
  python dup_scan.py --facts    # 只看事实性数字
"""

import io
import os
import re
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

SKIP_DIRS = {".git", "node_modules", ".perf", "_internal"}


def md_files():
    for dirpath, dirnames, files in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".md"):
                yield os.path.join(dirpath, f)


def rel(p):
    return os.path.relpath(p, ROOT)


# ---------------------------------------------------------------- 代码块

def scan_blocks():
    """返回 {normalized_block: [(文件, 起始行, 行数)]}"""
    index = defaultdict(list)
    for p in md_files():
        lines = io.open(p, encoding="utf-8").read().split("\n")
        i = 0
        while i < len(lines):
            if lines[i].strip().startswith("```"):
                start = i
                # 注入生成的块不算重复：它的副本由脚本维护，不会漂移
                generated = (start > 0 and "@include" in lines[start - 1])
                i += 1
                body = []
                while i < len(lines) and not lines[i].strip().startswith("```"):
                    body.append(lines[i])
                    i += 1
                if any("@include" in b for b in body):
                    generated = True          # include 写在围栏内的情况
                norm = "\n".join(l.strip() for l in body if l.strip())
                if not generated and len(norm) >= 60:   # 太短或生成的块不算
                    index[norm].append((rel(p), start + 1, len(body)))
            i += 1
    return index


def report_blocks(index):
    dups = [(k, v) for k, v in index.items() if len(v) >= 2]
    dups.sort(key=lambda kv: -len(kv[0]) * (len(kv[1]) - 1))
    print(f"\n{'='*70}")
    print(f"代码块重复：{len(dups)} 组（共 {sum(len(v) for _, v in dups)} 处拷贝）")
    print("=" * 70)
    for norm, occ in dups[:15]:
        first = norm.split("\n")[0][:60]
        print(f"\n  [{len(norm):4} 字符 × {len(occ)} 处]  {first}")
        for f, ln, n in occ:
            print(f"      {f}:{ln}")

    # 按文件统计"被复制的量"
    waste = defaultdict(int)
    for norm, occ in dups:
        for f, ln, n in occ:
            waste[f] += len(norm)
    print("\n  按文件：参与重复的字符量（越多越该收）")
    for f, n in sorted(waste.items(), key=lambda kv: -kv[1])[:12]:
        print(f"      {n:7}  {f}")


# ---------------------------------------------------------------- 长行

def scan_lines():
    index = defaultdict(list)
    for p in md_files():
        for no, line in enumerate(io.open(p, encoding="utf-8").read().split("\n"), 1):
            s = line.strip()
            if len(s) < 40 or s.startswith("```") or s.startswith("|--"):
                continue
            if s.startswith("|") and set(s) <= set("|- :"):
                continue
            index[s].append((rel(p), no))
    return index


def report_lines(index):
    dups = [(k, v) for k, v in index.items() if len(v) >= 2]
    dups.sort(key=lambda kv: -len(kv[0]) * (len(kv[1]) - 1))
    print(f"\n{'='*70}")
    print(f"长行重复：{len(dups)} 组")
    print("=" * 70)
    for s, occ in dups[:20]:
        files = sorted({f for f, _ in occ})
        print(f"\n  [{len(s):3} 字符 × {len(occ)} 处 / {len(files)} 文件]")
        print(f"      {s[:100]}")
        for f, no in occ[:6]:
            print(f"      {f}:{no}")


# ---------------------------------------------------------------- 事实数字

FACT_PATTERNS = [
    ("规格版本号", re.compile(r"v0\.\d+")),
    ("测试项数", re.compile(r"(\d+)\s*项测试")),
    ("可撒谎位置数", re.compile(r"(\d+)\s*处(可撒谎|谎言)")),
    ("测试总数", re.compile(r"测试\s*(\d+)\s*项|(\d+)\s*项测试全")),
]


def report_facts():
    print(f"\n{'='*70}")
    print("事实性数字：同一事实被几处各自维护")
    print("=" * 70)
    for name, rx in FACT_PATTERNS:
        hits = defaultdict(list)
        for p in md_files():
            for no, line in enumerate(io.open(p, encoding="utf-8").read().split("\n"), 1):
                for m in rx.finditer(line):
                    hits[m.group(0)].append((rel(p), no))
        if not hits:
            continue
        print(f"\n  【{name}】")
        for val, occ in sorted(hits.items(), key=lambda kv: -len(kv[1]))[:8]:
            files = sorted({f for f, _ in occ})
            if len(files) >= 2:
                print(f"      {val:28} {len(occ)} 处 / {len(files)} 文件")
                for f in files[:5]:
                    print(f"          - {f}")


def main(argv):
    only_blocks = "--blocks" in argv
    only_lines = "--lines" in argv
    only_facts = "--facts" in argv
    anything = only_blocks or only_lines or only_facts

    print(f"扫描目录：{rel(ROOT) or ROOT}")
    files = list(md_files())
    print(f"Markdown 文件：{len(files)} 个")

    if not anything or only_blocks:
        report_blocks(scan_blocks())
    if not anything or only_lines:
        report_lines(scan_lines())
    if not anything or only_facts:
        report_facts()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
