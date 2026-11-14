#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""typenames.py —— 容器类型名的**别名层**（B1，2026-11-05）

## 为什么要有这个文件

改名 `Vec → vector` 失败过两次，原因不是漏改，而是：
**"显示用的名字"和"判断用的名字"是同一个字符串字面量。**

整词替换会同时动两者，只要有一处漏网（fixture、f-string 拼出来的名字、文档样例），
就变成"声明 `vector<u8>`、判据还认 `Vec`"——**不报错，成本结论悄悄变**（定长性丢失）。

## 做法

- `CANON`：旧名 → 新名。**判断**一律先过 `canon()`；
- `DISPLAY`：新名 → 写出去的名字。**构造/显示**一律过 `display()`。

于是新旧两套名字**能共存**：数据还没翻的时候行为完全不变，
翻数据时只需要动 `.eln` / `.tu` / 文档，不用碰判断逻辑。

**单向纪律**：`canon` 只往里收，`display` 只往外发；不要用 `canon` 去拼字符串。
"""
from typing import Any

#: 旧名 → 规范名。**只增不改**（历史数据里两个名字都可能出现）。
CANON = {
    "Vec": "vector",
    "FixedVec": "array",
    "Map": "map",
    "Set": "set",
    "Option": "optional",
    # **P4-0（2026-11-11）**：`std::expected<T, E>` 是本语言 `Result` 的
    # **唯一写法**。内部仍叫 `Result`（类型检查 / 解释器都按这个名字判），
    # 但用户写出来的是 `expected`；`Result` 本身成了旧名（见 `OLD`）。
    "expected": "Result",
}

#: **已下线的类型名 → 新名**（P4-0，2026-11-11）。
#:
#: 与 `CANON` 分开：`CANON` 是"内部两种写法等价"，这张表是"**外面不许再写**"。
#: `typecheck` 遇到它们报 `E-Type-OldName`（error），消息里教新名。
#: `Option` / `Result` 以前都能写，现在只剩 `optional` / `expected`。
OLD = {
    "Option": "optional",
    "Result": "expected",
}

#: 规范名 → 写出去的名字。将来若要改显示，只动这张表。
DISPLAY = {
    "vector": "vector",
    "array": "array",
    "map": "map",
    "set": "set",
    "optional": "optional",
    # 内部名 → 用户写法：诊断里不许出现 `Result<…>` / `Option<…>`
    # （文档与名字表说的是 `expected` / `optional`，两边必须同源）。
    "Result": "expected",
    "Option": "optional",
}


def canon(name: Any) -> str:
    """把任意名字变成规范名（未知的名字原样返回）。"""
    if not isinstance(name, str):
        return ""
    return CANON.get(name, name)


def display(name: Any) -> str:
    """把规范名变成对外的写法（未知的规范名原样返回）。"""
    if not isinstance(name, str):
        return ""
    return DISPLAY.get(name, name)
