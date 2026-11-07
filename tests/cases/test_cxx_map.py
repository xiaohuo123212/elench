#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_cxx_map.py —— C++ 后端的第一块砖：**映射表必须闭合**

## 为什么先测"表"而不是"生成器"

仓库里**没有 C++ 后端**（`elench export` 导的是成本数据，不是代码）。
在后端写出来之前，唯一能保证的是：**每个内建都有交代**——

- 要么有映射（带**成本注记**，因为 C++ 的 `unordered_map` 平均 `O(1)` **不等于**我们的上界承诺）；
- 要么在 `UNMAPPED` 里写明"为什么不做"。

**漏一个就红。** 这样后端作者不会遇到"这个内建怎么办"的空白。
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


import cxx_map      # noqa: E402
import stdlib       # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def main() -> int:
    print("=== 每个内建都有交代 ===")
    missing = [n for n in stdlib.FNS if n not in cxx_map.BUILTIN_MAP and n not in cxx_map.UNMAPPED]
    check("没有任何内建被漏掉（共 %d 个）" % len(stdlib.FNS), not missing, str(missing))

    print("=== 每条映射都带成本注记 ===")
    no_note = [n for n, v in cxx_map.BUILTIN_MAP.items() if not (len(v) == 2 and v[1].strip())]
    check("映射都带 cost_note", not no_note, str(no_note))

    print("=== 关键类型映射 ===")
    for elench, cxx in (("map<K,V>", "std::map"),
                        ("set<T>", "std::set"),
                        ("vector<u8>", "std::vector")):
        check(f"{elench} → {cxx}", cxx in cxx_map.cxx_type(elench), cxx_map.cxx_type(elench))

    print("=== 平均 O(1) 与上界不同的地方必须写明 ===")
    note = cxx_map.cxx_builtin("map_put")[1]
    check("map_put 的注记提到 rehash / 摊还", "rehash" in note or "摊还" in note, note)

    print("=== 明确不映射的有理由 ===")
    check("exec / now_ms 都有理由",
          all(cxx_map.unmapped_reason(k).strip() for k in cxx_map.UNMAPPED))

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
