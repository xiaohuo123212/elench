#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

"""test_units.py —— `operations` 的**单位定义**与计数器的一致性（批次 丙）

# 单位定义（这就是全部内容）

**`operations`（操作数）的计数单位**：

| 计 1 的东西 | 说明 |
|---|---|
| **每条语句执行一次** | 声明、赋值、`return`、`if`/`while`/`for` 本身 |
| **每个表达式节点求值一次** | 含**叶子**（字面量、变量读、`xs.at(i)` 的接收者） |
| **每轮循环迭代一次** | 条件判断的迭代开销 |

**不计的东西**：函数入口/出口、形参绑定、类型相关的一切。

# 这份测试要防什么

**单位不定义清楚，就会出一件很糟的事**：
κ 用一套单位、解释器用另一套，然后我们拿两边比大小，得出"上界不成立"或"上界太松"的**假结论**。

**这个文件把定义钉成可执行的断言**：给定一个程序，步数**必须**等于按定义手算的数。

# 为什么现在才有

2026-10-30 的对比实测发现：`lcs` 的 κ = 3150 而实际 15621（**4.96 倍**），
`paren` 正好 **2.00 倍**——**不是常数项差异，是按"叶子读取数"放大**。
**根因就是这里没有定义过 `operations` 是什么。**
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

import sys

from parser import parse_source
from interp import Interp

_passed = 0
_failed = []


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def steps_of(src: str, fn: str, args=None):
    mod = parse_source(src, "u.eln").module
    it = Interp(mod, None)
    it.call(fn, args or [])
    return it.counters()["evaluation_steps"]


def test_leaf_and_op():
    print("[1] 叶子与操作：`return 1 + 2;` = 语句 1 + 节点 3")
    n = steps_of("func f() -> u64! { return 1 + 2; }", "f")
    check(n == 4, "步数 = 4（return 1 + 叶子 1 + 叶子 2 + 二元 1）", f"实际 {n}")


def test_more_leaves_more_steps():
    print("[2] 每加一个叶子读取，步数 +1（这是「比例放大」的来源）")
    a = steps_of("func f() -> u64! { return 1 + 2; }", "f")
    b = steps_of("func f() -> u64! { return 1 + 2 + 3; }", "f")
    check(b - a == 2, "多一个 `+ 3` = 二元 1 + 叶子 1 = +2", f"{a} → {b}")


def test_decl_and_return():
    print("[3] 声明 + 返回：`u64 s = 0; return s;` = 4")
    n = steps_of("func f() -> u64! { u64 s = 0; return s; }", "f")
    check(n == 4, "步数 = 4（声明 1 + 叶子 0 1 + return 1 + 叶子 s 1）", f"实际 {n}")


def test_loop_linear_in_rounds():
    print("[4] 循环：轮数翻倍，步数约翻倍（线性）")
    src = """func f() -> u64! {
        u64 s = 0; u64 i = 0;
        while (i < {N}) {{ s = s + i; i = i + 1; }}
        return s;
    }"""
    a = steps_of(src.replace("{N}", "2").replace("{{", "{").replace("}}", "}"), "f")
    b = steps_of(src.replace("{N}", "4").replace("{{", "{").replace("}}", "}"), "f")
    check(b > a, "4 轮比 2 轮多", f"{a} → {b}")
    # 每轮的成本固定 ⇒ 差值 / 轮数差 是整数且 > 0
    per = (b - a) / 2.0
    check(per > 0 and abs(per - round(per)) < 1e-9,
          "每轮成本是固定的整数", f"{a} → {b}, 每轮 {per}")


def main():
    test_leaf_and_op()
    test_more_leaves_more_steps()
    test_decl_and_return()
    test_loop_linear_in_rounds()

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
