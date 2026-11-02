#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_fntype.py —— 函数类型 `func(A, B) -> R`（slice 1：**只在参数位置**）

## 只开一个口子

| 允许 | 暂不支持 |
|---|---|
| `spec f(..., func(u64, u64) -> u64 g)` | 返回值是函数 |
| 传**具名函数** `f(xs, add)` | 局部变量是函数、函数存进容器 |
| 在函数体里调用 `g(1, 2)` | 闭包 / lambda |

## 成本

`for_each(m, f)` 的 κ = `n(m) × (1 + k(f)) + n(m)`；target 里写 `O(m.length() * k(f))`。
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
from typecheck import typecheck       # noqa: E402

FAILED = []
TU = "spec f(const Vec<u64>& xs, func(u64, u64) -> u64 g) -> u64 { effects {}; }\n"
TU_CALL = ("spec f(const Vec<u64>& xs, func(u64, u64) -> u64 g) -> u64 { effects {}; }\n"
           "spec h(const Vec<u64>& xs) -> u64 { effects {}; }\n")
CH = """func add(u64 a, u64 b) -> u64 { return a + b; }
func f(const Vec<u64>& xs, func(u64, u64) -> u64 g) -> u64 { return g(3, 4); }
"""
CH_CALL = """func add(u64 a, u64 b) -> u64 { return a + b; }
func f(const Vec<u64>& xs, func(u64, u64) -> u64 g) -> u64 { return g(3, 4); }
func h(const Vec<u64>& xs) -> u64 { return f(xs, add); }
"""


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def main() -> int:
    print("=== 解析 ===")
    p = parse_source(CH, "m.eln")
    check("func(...)->R 能解析", [d.code for d in p.diagnostics] == [],
          str([d.code for d in p.diagnostics][:2]))

    print("=== 类型检查 ===")
    tu = parse_source(TU_CALL, "m.tu")
    tc = typecheck(tu.module, parse_source(CH_CALL, "m.eln").module)
    check("签名一致的函数能传", [d.code for d in tc] == [], str([d.code for d in tc][:3]))

    print("=== 运行 ===")
    got = Interp(parse_source(CH_CALL, "m.eln").module, tu.module).call("h", [[1]])
    check("h → g(3,4) = 7", got == 7, str(got))

    print("=== 成本：k(f) 是裸符号 ===")
    TU_COST = """spec f(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length() * k(visit)) }, allocation: { max: 0 }, };
}
"""
    CH_COST = """func visit(const Vec<u8>& k, u64 v) -> u64 { return v; }
func f(const Vec<u64>& xs) -> u64 {
    HashMap<Vec<u8>, u64> m = map_new();
    for_each(m, visit);
    return 0;
}
"""
    ci = CostInterp(parse_source(CH_COST, "m.eln").module,
                    parse_source(TU_COST, "m.tu").module)
    k = ci.cost_of("f").render()
    check("κ 含 k(visit)（不是 n(k(visit))）", "k(visit)" in k and "n(k(visit))" not in k, k)
    t = ci.target_cost("f").render()
    check("target 含 k(visit)", "k(visit)" in t, t)

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
