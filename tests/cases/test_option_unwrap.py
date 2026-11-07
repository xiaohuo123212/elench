#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_option_unwrap.py —— `option_unwrap(o)`（E2）

## 为什么要有它

以前只有 `option_unwrap_or(o, default)`。想"这个值一定存在"时，
作者只能写 `option_unwrap_or(o, 0)` —— **把漏判静默变成 0**。
那是这个语言最不该有的东西（`E18` 讲的就是这件事）。

`option_unwrap` 在 `None` 时**报错**，让漏判立刻可见。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source            # noqa: E402
from interp import Interp, InterpError      # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


SRC = """func ok() -> u64 {
    optional<u64> o = option_some(7);
    return option_unwrap(o);
}
func bad() -> u64 {
    optional<u64> n = option_none();
    return option_unwrap(n);
}
func with_default() -> u64 {
    optional<u64> n = option_none();
    return option_unwrap_or(n, 42);
}
"""

it = Interp(parse_source(SRC, "a.eln").module, None)

print("[1] Some(7) → 7")
check("option_unwrap(Some(7)) == 7", it.call("ok", []) == 7, it.call("ok", []))

print("[2] None → 报错（不静默变成 0）")
try:
    it.call("bad", [])
    check("None 抛错", False, "没有抛")
except InterpError as e:
    check("None 抛错且提到 Some", "Some" in str(e), str(e))

print("[3] unwrap_or 仍是默认值语义（不受影响）")
check("option_unwrap_or(None, 42) == 42", it.call("with_default", []) == 42, it.call("with_default", []))

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
