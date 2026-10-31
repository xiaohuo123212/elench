#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""v0.68 文档迁移：把六份文档正文里的旧语法就地转成 C++ 风格。

只处理**代码围栏之外**与**散文中的行内代码**，注入块（@include）不动——
那些由 sync_docs.py 从样例同步。
"""

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

FILES = [
    "文档/设计/目前设计/简明语法.md",
    "文档/设计/目前设计/语法总览.md",
    "文档/设计/目前设计/语法设计.md",
    "文档/设计/目前设计/样例/示例-三个构造.md",
    "文档/一屏对比.md",
    "文档/语言介绍.md",
]

PAIRS = [
    # 关键字
    (r"\bfn(\s+)(?=[A-Za-z_])", r"func\1"),
    (r"`fn`", "`func`"),
    (r"\bval(\s+)(?=[A-Za-z_])", r"const\1"),
    (r"\bvar(\s+)(?=[A-Za-z_])", r"\1"),
    (r"`val`", "`const`"),
    (r"`var`", "`const`"),
    # 旧的结束子句
    (r"\}\s*end\s+(if|while|for|match|fn|spec|choose|struct|enum|trait|impl)\b", "}"),
    # 容器
    (r"BoundedVector\[\s*([A-Za-z_0-9]+)\s*,\s*([A-Za-z_0-9]+)\s*\]", r"FixedVec<\2, \1>"),
    (r"\[\]\s*([A-Za-z_][A-Za-z_0-9]*)", r"Vec<\1>"),
    # 返回类型
    (r"->\s*Result\[", "-> !Result_SENTINEL["),
    # 引用
    (r"&mut\s+", ""),
    # at() 不再解引用
    (r"\*\s*([A-Za-z_][A-Za-z_0-9]*\.at\()", r"\1"),
]


def convert(text: str) -> str:
    s = text
    for pat, rep in PAIRS:
        s = re.sub(pat, rep, s)

    # `-> !Result_SENTINEL[T, E]` → `-> T!`
    def fix_result(m):
        inner = m.group(1)
        depth, cut = 0, -1
        for i, ch in enumerate(inner):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            elif ch == "," and depth == 0:
                cut = i
                break
        if cut < 0:
            return m.group(0)
        return "-> " + inner[:cut].strip() + "!"

    s = re.sub(r"->\s*!Result_SENTINEL\[(.+?)\]", fix_result, s)

    # 条件补括号
    def paren(m):
        kw, cond = m.group(1), m.group(2).strip()
        if cond.startswith("(") and cond.endswith(")"):
            return f"{kw} {cond} {{"
        return f"{kw} ({cond}) {{"

    s = re.sub(r"\b(if|while)\s+(?!\()([^{};\n]+?)\s*\{", paren, s)
    s = re.sub(r"\bfor\s+(?!\()([A-Za-z_][A-Za-z_0-9]*)\s+in\s+([^\s{}]+)\s*\{",
               r"for (\1 in \2) {", s)
    return s


def main():
    total = 0
    for rel in FILES:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            print(f"  跳过（不存在）{rel}")
            continue
        raw = io.open(p, encoding="utf-8").read()
        lines = raw.split("\n")
        out = []
        fence = False
        for ln in lines:
            if ln.strip().startswith("```"):
                fence = not fence
                out.append(ln)
                continue
            out.append(convert(ln) if not fence else ln)
        new = "\n".join(out)
        if new != raw:
            io.open(p, "w", encoding="utf-8", newline="\n").write(new)
            total += 1
            print(f"  已迁移 {rel}")
        else:
            print(f"  无变化 {rel}")
    print(f"共 {total} 份")


if __name__ == "__main__":
    sys.exit(main())
