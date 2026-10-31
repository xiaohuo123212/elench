#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""v0.68 语法迁移：把测试里内嵌的旧写法转成 C++ 风格。

只改**字符串字面量里**的示例源码，不动测试自身的 Python 代码结构。
"""

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def migrate_lang(src: str) -> str:
    """把一段（本语言的）源码从 v0.67 写法迁移到 v0.68。"""
    s = src

    # 1) 关键字
    s = re.sub(r"\bfn\s+", "func ", s)
    s = re.sub(r"\.end\s+fn\b", "}", s)
    s = re.sub(r"\}\s*end\s+(if|while|for|match|fn|spec|choose)\b", "}", s)
    s = re.sub(r"\}\s*end\s+enum\b", "}", s)
    s = s.replace(" end ", " ")

    # 2) 容器类型
    s = re.sub(r"BoundedVector\[\s*([A-Za-z_0-9]+)\s*,\s*([A-Za-z_0-9]+)\s*\]",
               r"FixedVec<\2, \1>", s)
    s = re.sub(r"\[\]([A-Za-z_0-9]+)", r"Vec<\1>", s)

    # 3) 引用：&mut T → T& ；&T → T&
    s = re.sub(r"&mut\s+([A-Za-z_][A-Za-z_0-9<>,\[\] ]*)", r"\1&", s)

    # 4) 返回类型：-> Result[T, E] → -> T!
    def result_to_bang(m):
        inner = m.group(1)
        depth = 0
        for i, ch in enumerate(inner):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            elif ch == "," and depth == 0:
                return "-> " + inner[:i].strip() + "!"
        return m.group(0)

    s = re.sub(r"->\s*Result\[(.+?)\](?=\s*[{\n])", result_to_bang, s)

    # 5) 绑定：val x: T = → const T x = ；val x = → x = （类型交给推导）
    def fix_val(m):
        indent, name, typ = m.group(1), m.group(2), m.group(3)
        if typ:
            return f"{indent}const {typ.strip()} {name} ="
        return f"{indent}{name} ="

    s = re.sub(r"([ \t]*)val\s+([A-Za-z_][A-Za-z_0-9]*)\s*:\s*([A-Za-z_0-9<>,\[\] ]+?)\s*=",
               fix_val, s)

    def fix_var(m):
        indent, name, typ = m.group(1), m.group(2), m.group(3)
        if typ:
            return f"{indent}{typ.strip()} {name} ="
        return f"{indent}{name} ="

    s = re.sub(r"([ \t]*)var\s+([A-Za-z_][A-Za-z_0-9]*)\s*:\s*([A-Za-z_0-9<>,\[\] ]+?)\s*=",
               fix_var, s)

    # 6) 条件加括号
    def paren_if(m):
        kw, cond = m.group(1), m.group(2)
        return f"{kw} ({cond}) {{"

    s = re.sub(r"\b(if|while)\s+(?!\()([^{}\n;]+?)\s*\{", paren_if, s)

    def paren_for(m):
        v, it = m.group(1), m.group(2)
        return f"for ({v} in {it}) {{"

    s = re.sub(r"\bfor\s+(?!\()([A-Za-z_][A-Za-z_0-9]*)\s+in\s+(\S+)\s*\{", paren_for, s)

    # 7) at() 不再解引用
    s = re.sub(r"\*\s*([A-Za-z_][A-Za-z_0-9]*\.at\()", r"\1", s)

    # 8) Ok(x) 去掉（只在 return 后）
    s = re.sub(r"return\s+Ok\(([^()]*)\)\s*;", r"return \1;", s)

    return s


def main():
    files = ["test_parser.py", "test_checker.py", "test_formatter.py",
             "test_invalid.py", "test_highlight.py"]
    for name in files:
        p = os.path.join(HERE, name)
        t = io.open(p, encoding="utf-8").read()
        out_lines = []
        for line in t.split("\n"):
            # 只处理含引号的双引号/三引号串所在行
            if ('"' in line) and re.search(r"\b(fn|val|var|end|Result\[|\[\]|BoundedVector|&mut)\b", line):
                # 拆出字符串片段，仅对其内容迁移
                parts = re.split(r'("(?:[^"\\]|\\.)*")', line)
                for k in range(len(parts)):
                    if k % 2 == 1:
                        body = parts[k][1:-1]
                        parts[k] = '"' + migrate_lang(body) + '"'
                out_lines.append("".join(parts))
            else:
                out_lines.append(line)
        new = "\n".join(out_lines)
        if new != t:
            io.open(p, "w", encoding="utf-8", newline="\n").write(new)
            print(f"  {name}: 已迁移")
        else:
            print(f"  {name}: 无变化")


if __name__ == "__main__":
    sys.exit(main())
