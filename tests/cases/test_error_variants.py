#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_error_variants.py —— `error X { A, B };` 的变体真的会被校验（E3）

## 这条守的是什么

`error X;` 是**开放**的：任何变体都接受（老行为，保持兼容）。
`error X { A, B };` 是**封闭**的：写了变体清单，用错的变体必须报错。

E3 之前：EBNF 里一直写着变体列表，但解析器只吃 `error X;`，
文档与实现不一致，于是**变体从来不校验**——写错变体名要到运行期才炸。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source          # noqa: E402
from typecheck import typecheck          # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


TU = """error CalcError { BadToken, Empty };

spec f() -> u64! {
    effects {};
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}
"""

TU_OPEN = """error CalcError;

spec f() -> u64! {
    effects {};
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}
"""


def diag_of(tu_text, ch_text):
    tu = parse_source(tu_text, "e.tu").module
    ch = parse_source(ch_text, "e.eln").module
    return [d.message for d in typecheck(tu, ch)], None


print("[1] 封闭：写对变体 → 不报")
msgs, tc = diag_of(TU, 'func f() -> u64! {\n    return Err(CalcError::BadToken);\n}\n')
check("无 E-Variant-Unknown", not any("变体" in m for m in msgs), msgs)

print("[2] 封闭：写错变体 → 报，且说清是「错误类型」")
msgs, _ = diag_of(TU, 'func f() -> u64! {\n    return Err(CalcError::Nope);\n}\n')
hit = [m for m in msgs if "变体" in m]
check("报出了变体错误", bool(hit), msgs)
check("措辞是「错误类型」", any("错误类型" in m for m in hit), hit)

print("[3] 开放：`error X;` 不校验变体（老行为不变）")
msgs, _ = diag_of(TU_OPEN, 'func f() -> u64! {\n    return Err(CalcError::Whatever);\n}\n')
check("不因变体报警", not any("变体" in m for m in msgs), msgs)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
