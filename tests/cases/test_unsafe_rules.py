#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_unsafe_rules.py —— `unsafe` 从装饰变成约束

## 背景

`unsafe { }` 以前能解析，但**关键字被丢弃**（当普通块），既不影响 effects
也不影响成本——写了等于没写。

而且它当时**根本没法被声明**：
`EFFECT_VOCAB` 里没有 `unsafe`，而 `unsafe` 在词法层是关键字，
`effects { unsafe }` 直接报「期望效果名」。**一个要求你做不到的事的检查，
比不检查更糟**，所以先修这个前置。

## 现在守的三件事

1. `effects { unsafe }` 能解析（前置）；
2. 有 `unsafe` 块但没声明 → `E-Unsafe-Undeclared`；
3. 声明了 `unsafe` 但没有块 → `E-Unsafe-Overdeclared`（虚报）。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source      # noqa: E402
from typecheck import typecheck      # noqa: E402
from checker import check_module     # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def _tu(effects: str) -> str:
    return ("spec f() -> u64 {\n"
            "    effects {%s};\n"
            "    target { time: { max: O(1) }, allocation: { max: 0 }, };\n"
            "}\n") % effects


def codes(tu_text, ch_text):
    # 类型检查（AST 入口）与整体检查（源码入口）都要过一遍
    tu = parse_source(tu_text, "f.tu").module
    ch = parse_source(ch_text, "f.eln").module
    out = [d.code for d in typecheck(tu, ch)]
    res = check_module(tu_text, ch_text, "f")
    out += [d.code for d in res.diagnostics]
    return out


print("[1] `effects { unsafe }` 能解析（前置：词表 + 关键字当效果名）")
tu = parse_source(_tu(" unsafe "), "f.tu")
check("无解析错误", not [d for d in tu.diagnostics if d.code.startswith("E-Parse")],
      [d.code for d in tu.diagnostics])

print("[2] 有 unsafe 块但没声明 → E-Unsafe-Undeclared")
c = codes(_tu(""), "func f() -> u64 { unsafe { return 1; } }")
check("报 E-Unsafe-Undeclared", "E-Unsafe-Undeclared" in c, c)

print("[3] 声明了但没有块 → E-Unsafe-Overdeclared")
c = codes(_tu(" unsafe "), "func f() -> u64 { return 1; }")
check("报 E-Unsafe-Overdeclared", "E-Unsafe-Overdeclared" in c, c)

print("[4] 声明了且确实有块 → 不报这两条")
c = codes(_tu(" unsafe "), "func f() -> u64 { unsafe { return 1; } }")
check("两条都不出现",
      "E-Unsafe-Undeclared" not in c and "E-Unsafe-Overdeclared" not in c, c)


FFI_TU = """spec f(const vector<u8>& s) -> u64 {
    effects { unsafe };
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}
"""

FFI_CH = """extern c_len(const vector<u8>& s) -> u64 from "ucrtbase" effects { io };

func f(const vector<u8>& s) -> u64 { return c_len(s); }
"""

FFI_CH_OK = """extern c_len(const vector<u8>& s) -> u64 from "ucrtbase" effects { io };

func f(const vector<u8>& s) -> u64 { unsafe { return c_len(s); } }
"""

print("[5] FFI 调用必须在 unsafe 块里 → E-Unsafe-Required")
c = codes(FFI_TU, FFI_CH)
check("报 E-Unsafe-Required", "E-Unsafe-Required" in c, c)
c = codes(FFI_TU, FFI_CH_OK)
check("包进 unsafe 后不报", "E-Unsafe-Required" not in c, c)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
