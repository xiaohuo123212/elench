#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_containers.py —— `Set<T>` / `Deque<T>`（C2，v0.71）

## 接口照 C++ 抄，口径不放宽

| 容器 | C++ 对应 | 我们的实现 | 成本口径 |
|---|---|---|---|
| `Set<T>` | `std::unordered_set` | **复用 `MapVal`**（值恒为 1） | `O(1)`：**有界 8 次探测** |
| `Deque<T>` | `std::deque` | 环形缓冲 | 两端 push/pop `O(1)` **摊还**（扩容那一次不算） |

**为什么不全交给 `std::unordered_set`/`std::deque`**：它们的最坏情况是 `O(n)`（rehash / 扩容），
而我们对外承诺的是**上界**——**"通常很快"不是上界**。接口可以抄，**承诺不能抄**。
"""
import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但**这批测试原先住在 `src/`**，里面大量
#: `os.path.join(HERE, "..", "文档")` / `os.path.join(ROOT, "tools")` 都假设
#: 「本文件在 src/、上一级是仓库根」。搬家时改 200 处路径比让 `HERE` 继续指 src 贵得多，
#: 所以保留旧语义：`HERE` = src/，`CASEDIR` = 本文件真正所在的目录。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import sys


from interp import Interp             # noqa: E402
from parser import parse_source       # noqa: E402

FAILED = []
TU = "spec f(const Vec<u64>& xs) -> u64 { effects {}; }\n"


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(body) -> int:
    tu = parse_source(TU, "m.tu").module
    ch = parse_source("func f(const Vec<u64>& xs) -> u64 {\n" + body + "\n}\n", "m.eln").module
    return Interp(ch, tu).call("f", [[1]])


def main() -> int:
    print("=== Set<T> ===")
    check("重复插入只算一个",
          run("""    Set<u64> s = set_new();
    set_add(s, 1);
    set_add(s, 1);
    set_add(s, 2);
    return set_len(s);""") == 2)
    check("set_has 命中", run("""    Set<u64> s = set_new();
    set_add(s, 7);
    return set_has(s, 7);""") == 1)
    check("set_has 未命中", run("""    Set<u64> s = set_new();
    return set_has(s, 7);""") == 0)

    print("=== Deque<T>（两端 O(1)）===")
    check("push_back + push_front 后长度为 2",
          run("""    Deque<u64> q = deque_new();
    deque_push_back(q, 10);
    deque_push_front(q, 5);
    return deque_len(q);""") == 2)
    check("pop_front 取到 5（先进先出那一端）",
          run("""    Deque<u64> q = deque_new();
    deque_push_back(q, 10);
    deque_push_front(q, 5);
    match (deque_pop_front(q)) {
        Option::Some(v) => { return v; }
        Option::None => { return 0; }
        _ => { return 0; }
    }""") == 5)
    check("pop_back 取到 10",
          run("""    Deque<u64> q = deque_new();
    deque_push_back(q, 10);
    deque_push_front(q, 5);
    match (deque_pop_back(q)) {
        Option::Some(v) => { return v; }
        Option::None => { return 0; }
        _ => { return 0; }
    }""") == 10)

    print("=== 扩容后仍然正确（超过初始容量 8）===")
    check("塞 20 个再取长度",
          run("""    Deque<u64> q = deque_new();
    u64 i = 0;
    while (i < 20) {
        deque_push_back(q, i);
        i = i + 1;
    }
    return deque_len(q);""") == 20)

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
