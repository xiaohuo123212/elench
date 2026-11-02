#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_hof.py —— 高阶内建：`map` / `filter` / `fold` / `count_if`（v0.71）

## 关键决定：**函数实参只能是具名函数**

理由和 `sort_by` 完全一样：

> **具名函数是有限集合，成本可静态分析；匿名函数是任意代码，`κ` 无法界定。**

所以 `map(xs, |x| x*2)` 会被拒绝（`InterpError`），而 `map(xs, twice)` 可以——
**`twice` 有 `.tu` 声明、有 κ**，于是 `map` 的 κ = `n(xs) × κ(twice) + n(xs)` 是可算的。
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


from cost_interp import CostInterp    # noqa: E402
from interp import Interp             # noqa: E402
from parser import parse_source       # noqa: E402

FAILED = []

PRELUDE = """func twice(u64 x) -> u64 { return x * 2; }
func gt10(u64 x) -> u64 { if (x > 10) { return 1; } return 0; }
func add(u64 a, u64 b) -> u64 { return a + b; }
"""

TU = ("spec f(const Vec<u64>& xs) -> u64 { effects {}; "
      "target { time: { max: O(xs.length()) }, allocation: { max: 0 }, }; }\n")


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(body, xs):
    ch = PRELUDE + "func f(const Vec<u64>& xs) -> u64 {\n" + body + "\n}\n"
    tu = parse_source(TU, "m.tu").module
    mod = parse_source(ch, "m.eln").module
    return Interp(mod, tu).call("f", [list(xs)])


def main() -> int:
    print("=== map / filter / fold / count_if ===")
    check("map 逐元素变换", run("""    const Vec<u64> d = map(xs, twice);
    return d[0];""", [1, 2]) == 2)
    check("filter 只留满足的", run("""    const Vec<u64> b = filter(xs, gt10);
    return b.length();""", [1, 11, 3, 20]) == 2)
    check("fold 求和", run("""    return fold(xs, 0, add);""", [1, 2, 3]) == 6)
    check("count_if 计数", run("""    return count_if(xs, gt10);""", [1, 11, 3, 20]) == 2)

    print("=== 函数实参不是名字时被拒绝（有意为之） ===")
    try:
        run("""    const Vec<u64> d = map(xs, twice(1));
    return d.length();""", [1])
        check("给了调用表达式 → 报错", False, "居然通过了")
    except Exception as exc:                     # noqa: BLE001
        check("给了调用表达式 → 报错（成本不可界定）",
              "具名" in str(exc) or "成本" in str(exc), str(exc)[:60])

    print("=== 成本 = n(xs) × κ(回调) + n(xs) ===")
    ch = PRELUDE + """func f(const Vec<u64>& xs) -> u64 {
    const Vec<u64> d = map(xs, twice);
    return d.length();
}
"""
    tu = parse_source(TU, "m.tu").module
    mod = parse_source(ch, "m.eln").module
    k = CostInterp(mod, tu).cost_of("f").render()
    check("κ 里出现 n(xs)×…", "n(xs)×" in k, k)
    check("κ 与 n 成正比（不是常数）", "n(xs)×" in k, k)

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
