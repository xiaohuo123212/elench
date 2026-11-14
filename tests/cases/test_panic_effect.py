#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_panic_effect.py —— L-05：`o.unwrap()` 必须要求声明 `panic`

## 修之前

`stdlib.EFFECTS` 里没有 `panic`/`unwrap`，`EFFECT_VOCAB` 里也没有 `panic`——
于是**双重失效**：

1. `effects { }` 的函数里调 `o.unwrap()` **一声不响**（后果没声明）；
2. 想老实声明时，`effects { panic }` 反被 `E-Effect-Unknown` 拒绝。

## 现在

- `panic` 进了效果词表；
- `unwrap`/`expect`/`panic`/`assert` 映射到 `panic` 效果；
- 未声明 → `E-Effect-Missing`，声明了 → 通过。

## 零误报

样例与测试里这些调用出现 **0 次**（实测），所以这条规则加上去**一个样例都没误伤**。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from checker import check_module      # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


CH = """u64 risky(optional<u64> o) {
    return o.unwrap();
}
"""
TU_EMPTY = """spec risky(optional<u64> o) -> u64 { effects { }; target { time: { max: O(1) }, allocation: { max: 0 }, }; }
"""
TU_PANIC = """spec risky(optional<u64> o) -> u64 { effects { panic }; target { time: { max: O(1) }, allocation: { max: 0 }, }; }
"""

print("[1] 用了 unwrap 但没声明 panic → 报错，且带行号")
res = check_module(TU_EMPTY, CH, "case")
codes = [d.code for d in res.diagnostics]
miss = [d for d in res.diagnostics if d.code == "E-Effect-Missing"]
check("报 E-Effect-Missing", bool(miss), codes)
check("带行号", bool(miss) and getattr(miss[0], "line", 0) > 0,
      getattr(miss[0], "line", None) if miss else "")

print("[2] 声明了 panic → 通过")
res2 = check_module(TU_PANIC, CH, "case2")
check("无诊断", not res2.diagnostics, [d.code for d in res2.diagnostics])

print("[3] 不用 panic 的程序不误报")
CH3 = "u64 safe(vector<u64>& xs) {\n    return xs.length();\n}\n"
TU3 = "spec safe(vector<u64>& xs) -> u64 { effects { }; target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n"
res3 = check_module(TU3, CH3, "case3")
check("无 Effect 类诊断",
      not any("Effect" in d.code for d in res3.diagnostics),
      [d.code for d in res3.diagnostics])

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
