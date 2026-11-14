#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_spawn_asm_scope_removed.py —— `spawn` / `asm` / `scope` 已下线

## 为什么删

三条都是「词法认得、EBNF 写着、语法分派没有」或「能解析但什么都不做」：

- `spawn` / `asm`：`parse_stmt` 里根本没有分支 → 报「期望表达式，实际是 'spawn'」，
  连一句人话都没有。
- `scope`：能解析，但 `advance()` 之后直接当普通 block —— **关键字被丢弃**，
  它承诺的「离开时 join」一个字都没实现。

## 各条的理由

- `spawn`：并发下的时间不是确定上界（取决于核数与调度）→ **κ 不再确定**；
- `asm`：内联汇编的操作数不可数（`rep movsb` 是几条取决于长度）→
  **「可验证」降级成「可估算」**；
- `scope`：存在的唯一意义是给 `spawn` 提供词法边界，`spawn` 没了它也没意义。

`parallel { }` 保留：它按顺序语义执行、成本保守，是真的能用的。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source        # noqa: E402
import grammar                          # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


CASES = [
    ("spawn", "E-Spawn-Removed", "u64 f() { spawn g(1); return 0; }"),
    ("asm", "E-Asm-Removed", 'u64 f() { asm("nop"); return 1; }'),
    ("scope", "E-Scope-Removed", "u64 f() { scope { return 1; } }"),
]

print("[1] 三条都报迁移错（不是「期望表达式」）")
for label, code, src in CASES:
    d = parse_source(src, "a.eln").diagnostics
    codes = [x.code for x in d]
    check(f"{label} → {code}", code in codes, codes)
    check(f"{label} 的说明含「已下线」", any("已下线" in x.message for x in d),
          [x.message for x in d][:1])

print("[2] EBNF 里仍能查到这三个关键字（防止有人以为语法还在）")
ebnf = grammar.language_ebnf()
for kw in ("spawn", "asm", "scope"):
    check(f"EBNF 提到 {kw}", kw in ebnf)

print("[3] parallel 仍然能解析（没被误删）")
d = parse_source("u64 f() { parallel { return 1; } }", "a.eln").diagnostics
check("parallel 无诊断", not [x for x in d if x.code.startswith("E-Parse")],
      [x.code for x in d])

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
