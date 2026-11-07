#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_stack_queue.py —— `stack<T>` / `queue<T>` 薄封装

C++ 的 `std::stack` / `std::queue` 也是**容器适配器**（默认包 `std::deque`），
不是容器。我们同样只做薄封装：底层就是普通向量，成本全部 O(1)，没有新内存语义。
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


SRC = """func s() -> u64 {
    stack<u64> st = stack<u64>::new();
    st.push(1);
    st.push(2);
    u64 a = st.top();
    u64 b = st.pop();
    return a * 10 + b;
}
func q() -> u64 {
    queue<u64> qq = queue<u64>::new();
    qq.push(1);
    qq.push(2);
    u64 a = qq.front();
    u64 b = qq.pop();
    return a * 10 + b;
}
func empty_stack() -> u64 {
    stack<u64> st = stack<u64>::new();
    return st.pop();
}
"""

it = Interp(parse_source(SRC, "a.eln").module, None)

print("[1] stack 是 LIFO：top = 2、pop = 2 → 22")
check("stack LIFO", it.call("s", []) == 22, it.call("s", []))

print("[2] queue 是 FIFO：front = 1、pop = 1 → 11")
check("queue FIFO", it.call("q", []) == 11, it.call("q", []))

print("[3] 空容器 pop 要报错（不静默）")
try:
    it.call("empty_stack", [])
    check("空 stack 报错", False, "没有抛")
except InterpError as e:
    check("空 stack 报错", "空" in str(e), str(e))

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
