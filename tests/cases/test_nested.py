#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_nested.py —— 一层嵌套泛型 `Vec<Vec<u8>>`（v0.71）

## 只做一层

| 允许 | 不做 |
|---|---|
| `Vec<Vec<u8>>` | `Vec<Vec<Vec<u8>>>` |

**记账口径**（`三个前置-做什么用.md` §2）：

- **分配** = `1 + n(外层)`（外层 1 次 + 每行各 1 次）；
- **内层长度不需要 `assume`**：`n(外层) ≤ n(输入)`、`Σ n(内层) ≤ n(输入)`；
- **`assume` 只在"流式输入"或"成本依赖数据结构形状"时才需要**。
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


from cost_interp import CostInterp      # noqa: E402
from interp import Interp                # noqa: E402
from parser import parse_source          # noqa: E402

FAILED = []
TU = ("spec f(const Vec<u8>& t) -> u64 { effects {}; "
      "target { time: { max: O(t.length()) }, allocation: { max: O(t.length()) }, }; }\n")


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(body, data: bytes):
    tu = parse_source(TU, "m.tu").module
    ch = parse_source("func f(const Vec<u8>& t) -> u64 {\n" + body + "\n}\n", "m.eln").module
    return Interp(ch, tu).call("f", [list(data)])


def main() -> int:
    print("=== 能下标、能取长度 ===")
    check("切出 3 段", run("""    Vec<Vec<u8>> rows = text_split_vec(t, ",");
    return rows.length();""", b"a,b,cd") == 3)
    check("取第 0 段（长度 1）", run("""    Vec<Vec<u8>> rows = text_split_vec(t, ",");
    return rows[0].length();""", b"a,b,cd") == 1)
    check("取第 2 段（长度 2）", run("""    Vec<Vec<u8>> rows = text_split_vec(t, ",");
    return rows[2].length();""", b"a,b,cd") == 2)

    print("=== 能拼回去 ===")
    check("join 回原长 6", run("""    Vec<Vec<u8>> rows = text_split_vec(t, ",");
    return text_join_vec(rows, ",").length();""", b"a,b,cd") == 6)

    print("=== 逐行求和（Σ 记账） ===")
    total = run("""    Vec<Vec<u8>> rows = text_split_vec(t, ",");
    u64 i = 0;
    u64 s = 0;
    while (i < rows.length()) {
        s = s + rows[i].length();
        i = i + 1;
    }
    return s;""", b"a,b,cd")
    check("Σ 行长 = 4（a + b + cd）", total == 4, str(total))

    print("=== 分配 = 1 + n(外层) ===")
    ch = """func f(const Vec<u8>& t) -> u64 {
    Vec<Vec<u8>> rows = text_split_vec(t, ",");
    return rows.length();
}
"""
    a = CostInterp(parse_source(ch, "m.eln").module, parse_source(TU, "m.tu").module).alloc_of("f").render()
    check("alloc 含 `1 + n(rows)`", "1 + n(rows)" in a, a)

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
