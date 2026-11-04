#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_borrow_br3.py —— 方法返回的引用默认借自接收者（BR-3）

## 这条为什么以前标着 🔴

`borrow.py` 的规则表写着「BR-3 方法返回引用默认借自 `self` —— 需类型侧记归属，未做」。
实测发现**它其实已经成立**：借用事件的"被借对象"由 `_base_name` 取，
而它找的是表达式里**第一个 `path`**——对 `xs.at(0)` 就是接收者 `xs`。

所以下面这段必须报 `E-Borrow-Conflict`：

    const u64& r = xs.at(0);
    xs.push(1);        // ← r 还活着，而 xs 被可变借用
    return r;

方向是**保守**的（`s.field.at(0)` 记成借自 `s`），符合"宁可多报，不可漏报"——
漏报就是 UB。

这条测试的作用：把"已经成立"变成"**钉住**"。规则表可以写错，行为不能悄悄退化。
"""
import os
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from dsh import _diagnose_all       # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


TU = """spec g(Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

BAD = """func g(Vec<u64>& xs) -> u64 {
    const u64& r = xs.at(0);
    xs.push(1);
    return r;
}
"""

GOOD = """func g(Vec<u64>& xs) -> u64 {
    const u64& r = xs.at(0);
    return r;
}
"""


def run(eln_src):
    d = tempfile.mkdtemp()
    tu_p, eln_p = os.path.join(d, "m.tu"), os.path.join(d, "m.eln")
    with open(tu_p, "w", encoding="utf-8") as fh:
        fh.write(TU)
    with open(eln_p, "w", encoding="utf-8") as fh:
        fh.write(eln_src)
    return _diagnose_all(tu_p, None, strict=False, overflow="off", relax=[])


print("[1] 方法返回的引用借自接收者：期间改接收者 → E-Borrow-Conflict")
codes = [d.code for d in run(BAD).diagnostics]
check("报了 E-Borrow-Conflict", "E-Borrow-Conflict" in codes, codes[:5])

print("[2] 不改接收者 → 不报")
codes = [d.code for d in run(GOOD).diagnostics]
check("没有 E-Borrow-Conflict", "E-Borrow-Conflict" not in codes, codes[:5])

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
