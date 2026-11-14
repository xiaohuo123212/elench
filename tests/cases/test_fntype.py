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
TU = "spec f(const vector<u64>& xs, func(u64, u64) -> u64 g) -> u64 { effects {}; }\n"
TU_CALL = ("spec f(const vector<u64>& xs, func(u64, u64) -> u64 g) -> u64 { effects {}; }\n"
           "spec h(const vector<u64>& xs) -> u64 { effects {}; }\n")
CH = """u64 add(u64 a, u64 b) { return a + b; }
u64 f(const vector<u64>& xs, func(u64, u64) -> u64 g) { return g(3, 4); }
"""
CH_CALL = """u64 add(u64 a, u64 b) { return a + b; }
u64 f(const vector<u64>& xs, func(u64, u64) -> u64 g) { return g(3, 4); }
u64 h(const vector<u64>& xs) { return f(xs, add); }
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
    TU_COST = """spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length() * k(visit)) }, allocation: { max: 0 }, };
}
"""
    # **探针用 `vector` 而不是 `HashMap`**（2026-11-11）：本项钉的是
    # 「`k(f)` 渲染成裸符号、不许写成 `n(k(f))`」，与容器长度无关。
    # HashMap 自 2026-11-11 起有尺寸上界（`map_put` 登记成 `log2(n(h))` 后
    # 必须能推出 `n(h)`），空 HashMap 的 `for_each` 轮数是 0 ⇒ κ 里
    # **根本不该出现 `k(visit)`**（0 次迭代是更紧的真上界，不是漏算）。
    # 用形参 `xs` 的 `n(xs)` 当轮数，才继续测到"裸符号"这件事。
    CH_COST = """u64 visit(u64 v) { return v; }
u64 f(const vector<u64>& xs) {
    for_each(xs, visit);
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
