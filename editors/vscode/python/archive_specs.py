#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""归档 v0.68 之前的规格版本（从 git 历史取全文）。"""

import io
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
ARC = os.path.join(ROOT, "文档", "设计", "归档", "规格历史")
SPEC = "文档/设计/目前设计/AI语言设计规格.md"

# 每个版本归档「宣布该版本的那次提交」，即该版本自身的规格
VERSIONS = [
    ("v0.62", "95aa063", "语法风格统一：kernel 去 fn / effects 强制花括号 / 块子句带分号 / choose 用 usemodel / ?! 必须括号"),
    ("v0.63", "bf3543d", "补 T11-b：let 不可赋值（含复合赋值与字段路径）"),
    ("v0.64", "849c1fa", "新增 C2：条件不得被冗余圆括号整条包住"),
    ("v0.65", "eb2bea5", "let 改为 val（与 var 真对举）；objective 改短名 time/p99/mem/size"),
    ("v0.66", "9e56991", "候选块 kernels：choose 进 spec、over 内 pending、kernel 关键字下线、允许嵌套"),
    ("v0.67", "edfc2d8", "算术全函数化（回绕）；BoundedVector.push 全函数；新增 L6 溢出 lint"),
]

HEADER = """<!-- 归档副本，请勿修改 -->
<!-- 版本：{ver}  提交：{sha}  归档于 2026-10-29 -->
<!-- 最新版见 文档/设计/目前设计/AI语言设计规格.md -->

> **⚠️ 本文是 {ver} 的冻结副本，仅供对照，不作为当前规范。**
> **该版本的关键变更**：{note}
> **为什么归档**：v0.68 把语法整体改成 C++ 风格（`func` / 类型前置 / 块无 `end` / `Vec<T>` / `T!`），
> 与前几个版本的写法差异较大，保留全文便于对照与论文引用。
>
> ---

"""


def main():
    os.makedirs(ARC, exist_ok=True)
    made = 0
    for ver, sha, note in VERSIONS:
        r = subprocess.run(["git", "show", f"{sha}:{SPEC}"],
                           cwd=ROOT, capture_output=True)
        if r.returncode != 0:
            print(f"  ✗ {ver}: 取不到 {sha}")
            continue
        body = r.stdout.decode("utf-8")
        # 版本行可能没跟上提交（历史遗留），这里以归档标头为准
        out = HEADER.format(ver=ver, sha=sha[:7], note=note) + body
        path = os.path.join(ARC, f"AI语言设计规格-{ver}.md")
        io.open(path, "w", encoding="utf-8", newline="\n").write(out)
        made += 1
        print(f"  ✓ {ver}  ←  {sha[:7]}  ({len(body.splitlines())} 行)")
    print(f"共归档 {made} 份 → {os.path.relpath(ARC, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
