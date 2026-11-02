#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
test_highlight.py —— 语法高亮测试

核心不变量：**高亮不改文本**——strip_ansi(highlight(src)) == src。
否则复制粘贴会坏，这是高亮器最容易犯的错。

运行：
  cd src; $env:PYTHONIOENCODING="utf-8"; python test_highlight.py
"""

import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但**这批测试原先住在 `src/`**，里面大量
#: `os.path.join(HERE, "..", "文档")` / `os.path.join(ROOT, "tools")` 都假设
#: 「本文件在 src/、上一级是仓库根」。搬家时改 200 处路径比让 `HERE` 继续指 src 贵得多，
#: 所以保留旧语义：`HERE` = src/，`CASEDIR` = 本文件真正所在的目录。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from highlight import highlight, strip_ansi, tokens_of

SAMPLE = os.path.join(HERE, "..", "文档", "设计", "目前设计", "样例")

_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def read(name):
    with open(os.path.join(SAMPLE, name), "r", encoding="utf-8") as f:
        return f.read()


def cats(src):
    return [c for c, _ in tokens_of(src)]


def pairs(src):
    return tokens_of(src)


# ---------------------------------------------------------------- 1. 不变量

def test_invariant():
    print("[1] 高亮不改文本（最重要）")
    for name in ("calculator.tu", "calculator.eln", "search.tu", "search.eln"):
        src = read(name)
        h = highlight(src)
        check(strip_ansi(h) == src, f"{name}: strip(highlight) == 原文",
              "高亮改变了正文")
        check(highlight(src, color=False) == src, f"{name}: 关闭颜色时原样返回")

    # 各种古怪输入也不许改字
    for s in ["", "\n\n", "// 注释", "const X: u64 = 'a';", "⊖", "?!L(x)",
              "αβγ 中文注释"]:
        check(strip_ansi(highlight(s)) == s, f"古怪输入不改字: {s!r}",
              repr(strip_ansi(highlight(s))))


# ---------------------------------------------------------------- 2. 分类

def test_categories():
    print("[2] token 分类")
    src = read("search.tu")
    ps = pairs(src)

    check(any(c == "keyword" and t == "spec" for c, t in ps), "spec 是关键字")
    check(any(c == "keyword" and t == "kernels" for c, t in pairs(read("search.eln"))), "kernels 是关键字")
    check(any(c == "ctx" and t == "effects" for c, t in ps), "effects 是上下文关键字")
    check(any(c == "ctx" and t == "target" for c, t in ps), "target 是上下文关键字")
    check(any(c == "ctx" and t == "benchmodel" for c, t in ps), "benchmodel 是上下文关键字")
    check(any(c == "ctx" and t == "over" for c, t in ps), "over 是上下文关键字")
    check(any(c == "comment" for c, _ in ps), "注释被识别")
    check(any(c == "number" for c, t in ps), "数字被识别")
    check(any(c == "operator" and t == "->" for c, t in ps), "箭头是运算符")
    check(any(c == "operator" and t == "=" for c, t in ps), "等号是运算符")

    ai = read("search.eln")
    ps2 = pairs(ai)
    check(any(c == "dispatch" and t == "?!" for c, t in ps2), "?! 被单独标为决策点")
    check(any(c == "keyword" and t == "return" for c, t in ps2), "return 是关键字")
    check(any(c == "ident" and t == "xs" for c, t in ps2), "变量名是 ident")

    tu = read("calculator.tu")
    ps3 = pairs(tu)
    check(any(c == "char" and t == "' '" for c, t in ps3), "字符字面量被识别")
    check(any(c == "punct" for c, _ in ps3), "标点归类为 punct（但不染色）")

    # 词法错误要高亮成 error，而不是当普通字符
    bad = pairs("const X: u64 = $;")
    check(any(c == "error" for c, _ in bad), "非法字符标为 error", str(bad))


# ---------------------------------------------------------------- 3. 覆盖

def test_coverage():
    print("[3] 切片连续覆盖全文")
    src = read("calculator.eln")
    total = sum(len(t) for _, t in tokens_of(src))
    nonblank = len([ch for ch in src if not ch.isspace()])
    check(total >= nonblank, "所有非空白字符都有归类",
          f"{total} vs {nonblank}")

    # 抽样：每个类别至少出现一次（在四个样例里）
    seen = set()
    for name in ("calculator.tu", "calculator.eln", "search.tu", "search.eln"):
        seen.update(c for c, _ in pairs(read(name)))
    for want in ("keyword", "ctx", "comment", "number", "operator",
                 "punct", "ident", "char", "dispatch"):
        check(want in seen, f"类别 {want} 在样例中出现", str(sorted(seen)))


def main():
    test_invariant()
    test_categories()
    test_coverage()

    print()
    total = _passed + len(_failed)
    if _failed:
        print(f"失败 {len(_failed)} / {total}")
        for f in _failed:
            print("  - " + f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
