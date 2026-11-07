#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_requires.py —— `requires` 的**调用点验证**（v0.71 第一步）

## 为什么要有这个测试

`requires` 如果只在运行期断言，它和函数体里的 `if` 没本质区别。
**只有"编译器在调用点能证明"，`requires` 才值钱**——这个测试锁住这件事。

## 这一版支持的三种谓词

| 谓词 | 事实来源 |
|---|---|
| `xs.length() > N` | `if (xs.length() > N) { … }` 的真分支 |
| `is_sorted(xs)` | 前面调过 `sort(xs)`，或 `if (is_sorted(xs)) { … }` |
| `k > N`（标量） | `if (k > N) { … }` 的真分支 |

**证明不了 → `E-Require-Unproven`**，消息里给两条修法。
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


from checker import check_module          # noqa: E402

FAILED = []
T = "target { time: { max: O(1) }, allocation: { max: 0 }, };\n"

HEAD_TU = (f"spec head(const vector<u64>& xs) -> u64 {{ effects {{}}; "
           f"requires xs.length() > 0; {T}}}\n")
HEAD_CH = "func head(const vector<u64>& xs) -> u64 { return xs[0]; }\n"

SORTED_TU = (f"spec find(const vector<u64>& xs, u64 key) -> u64 {{ effects {{}}; "
             f"requires is_sorted(xs); {T}}}\n")
SORTED_CH = "func find(const vector<u64>& xs, u64 key) -> u64 { return xs[0]; }\n"

SCALAR_TU = (f"spec ith(const vector<u64>& xs, u64 k) -> u64 {{ effects {{}}; "
             f"requires k > 0; {T}}}\n")
SCALAR_CH = "func ith(const vector<u64>& xs, u64 k) -> u64 { return xs[0]; }\n"


def codes(tu, ch):
    return [d.code for d in check_module(tu, ch, "m").diagnostics]


def with_g(tu, ch, callee):
    g = (f"spec g(const vector<u64>& xs) -> u64 {{ effects {{}}; {T}}}\n")
    return codes(tu + g, "func g(const vector<u64>& xs) -> u64 {\n" + ch + "\n}\n" + callee)


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def main() -> int:
    print("=== length 守卫 ===")
    check("守卫内调用 → 通过",
          with_g(HEAD_TU, "    if (xs.length() > 0) {\n        return head(xs);\n    }\n"
                          "    return 0;", HEAD_CH) == [])
    check("没有守卫 → E-Require-Unproven",
          "E-Require-Unproven" in with_g(HEAD_TU, "    return head(xs);", HEAD_CH))
    check("守卫方向反了（== 0）→ 仍报错",
          "E-Require-Unproven" in with_g(HEAD_TU,
                                         "    if (xs.length() == 0) {\n        return head(xs);\n    }\n"
                                         "    return 0;", HEAD_CH))

    print("=== is_sorted：sort 的后置条件 ===")
    check("先 sort 再 find → 通过",
          with_g(SORTED_TU, "    sort(xs);\n    return find(xs, 1);", SORTED_CH) == [])
    check("没 sort → 报错",
          "E-Require-Unproven" in with_g(SORTED_TU, "    return find(xs, 1);", SORTED_CH))

    print("=== 标量守卫 ===")
    check("k > 0 守卫 → 通过",
          with_g(SCALAR_TU, "    if (k > 0) {\n        return ith(xs, k);\n    }\n    return 0;",
                 SCALAR_CH) == [])

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
