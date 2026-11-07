#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""doc_links.py —— 公开文档**死链**检查（CI 门）

## 为什么

2026-11-06 清目录时改了几十个文件的位置，`文档/待办.md` 里立刻多出一处死链。
**死链不会让任何测试变红，但会让第一个点开的人失去信任。**

## 范围

只查**公开**文档（跳过 `设计/`、`实验/` 这类内部目录），因为外部读者只会点这些。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 没有死链 |
| 1 | 有死链（打印位置 + 目标） |

用法：

    python tools/doc_links.py            # 有死链就 exit 1
    python tools/doc_links.py --list     # 打印全部链接（含正常的）
"""
import io
import os
import re
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = ("设计", "实验", ".git")
LINK_RE = re.compile(r"\]\(([^)\s]+\.md)(?:#[^)]*)?\)")


def public_files():
    for dp, dns, fns in os.walk(os.path.join(R, "文档")):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for f in fns:
            if f.endswith(".md"):
                yield os.path.join(dp, f)


def scan():
    dead = []
    total = 0
    for p in public_files():
        rel = os.path.relpath(p, R)
        for i, line in enumerate(io.open(p, encoding="utf-8", errors="ignore").read().split("\n"), 1):
            for m in LINK_RE.finditer(line):
                tgt = m.group(1)
                if tgt.startswith(("http://", "https://")):
                    continue
                total += 1
                cand = os.path.normpath(os.path.join(os.path.dirname(p), tgt))
                if not os.path.exists(cand):
                    dead.append((rel, i, tgt))
    return total, dead


def main(argv) -> int:
    total, dead = scan()
    if "--list" in argv:
        print(f"  公开文档相对链接 {total} 个")
    if not dead:
        print(f"  ✓ 无死链（检查了 {total} 个相对链接）")
        return 0
    print(f"✗ {len(dead)} 处死链（共 {total} 个链接）：")
    for p, i, t in dead[:20]:
        print(f"    {p}:{i}  → {t}")
    if len(dead) > 20:
        print(f"    …还有 {len(dead) - 20} 处")
    print("  修法：改链接，或把目标文件移到文档里指的位置。")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
