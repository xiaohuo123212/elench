#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_unknown_container.py —— 未知容器必须**报错**，不能建成空壳

## 为什么

以前任何类型名都能过 check（类型名不是封闭集合），静态构造落到
`return []`，于是：

    deque<u64> q = deque<u64>::new();
    return q.length();        // 检查说没问题，运行返回 0

**"检查说没问题、运行给假答案"比报错危险得多。**

现在未实现的容器名在静态构造处直接抛 InterpError，并列出已实现的容器。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source              # noqa: E402
from interp import Interp, InterpError        # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(src, fn="f"):
    mod = parse_source(src, "a.eln").module
    return Interp(mod, None).call(fn, [])


print("[1] 未实现的容器名 → 报错（不是静默 0）")
for _ty in ("deque", "vectr", "list"):
    src = (f"u64 f() {{\n    {_ty}<u64> q = {_ty}<u64>::new();\n"
           f"    return q.length();\n}}\n")
    try:
        v = run(src)
        check(f"{_ty} 报错", False, f"返回了 {v}")
    except InterpError as ex:
        check(f"{_ty} 报错", "未知容器" in str(ex), str(ex)[:60])

print("[2] 已实现的容器仍然可用")
_v = run("u64 f() {\n    vector<u64> v = vector<u64>::make();\n"
         "    v.push(1);\n    return v.length();\n}\n")
check("vector 仍可用", _v == 1, _v)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
