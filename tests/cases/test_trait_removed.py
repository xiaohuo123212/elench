#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_trait_removed.py —— `trait` / `impl` 已下线（E11）

## 为什么删

核对后发现它不是「约束没实现」，而是**三重不可用**：
  ① 参数里不许写 `self`；
  ② 解析器不认 `impl Show for P`（EBNF 写着 `impl path ["for" path]`，实现只有 `impl Path {`）；
  ③ trait 里的方法签名要求已下线的 `fn` 关键字。
而且样例与文档里 **0 处使用**。

**一个看起来有用、实则不存在的机制，比没有更糟**——和 `assume` 是同一条理由。

## 守什么

写了 `trait` / `impl` 必须**立刻**报迁移错误，而不是先报一堆不相干的语法错。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source        # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


print("[1] trait → E-Trait-Removed")
d = parse_source("trait T { }\n", "a.tu").diagnostics
codes = [x.code for x in d]
check("有 E-Trait-Removed", "E-Trait-Removed" in codes, codes)
check("提示里说了「已下线」", any("已下线" in x.message for x in d), [x.message for x in d])

print("[2] impl → E-Impl-Removed")
d = parse_source("impl Foo { }\n", "a.tu").diagnostics
codes = [x.code for x in d]
check("有 E-Impl-Removed", "E-Impl-Removed" in codes, codes)

print("[3] EBNF 里仍能查到这两个关键字（防止有人以为语法还在）")
sys.path.insert(0, os.path.join(ROOT, "src"))
import grammar  # noqa: E402
ebnf = grammar.language_ebnf()
check("EBNF 提到 trait", "trait" in ebnf, ebnf[:80])
check("EBNF 提到 impl", "impl" in ebnf, ebnf[:80])

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
