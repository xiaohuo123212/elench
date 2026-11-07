#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_alloc.py —— `allocation` 维度（E36，v0.71）

## 为什么这个维度必须单独判

`allocation: { max: 0 }` 是**数个数**，不是判阶。
如果用时间那套 big-O 比较，"0 次"和"1 次"都是常数、都会被判"达成"——
**那等于把 E36 变成装饰**。

所以分配维用 `compare_exact`：**常数之间按数值比**。

## 建模规则（初版，见 `cost_interp.alloc_of` 注释）

| 构造 | 分配 |
|---|---|
| 字面量（含字符串） | 0 |
| `slice` / `format_u64` / `text_trim` / `text_to_lower` / `read_file` … | 1 |
| 循环里分配 | **每轮 × 轮数** |
| `if` 两分支 | **max**（不假设走哪条） |
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


from cost_check import compare_exact      # noqa: E402
from cost_interp import CostInterp        # noqa: E402
from parser import parse_source           # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def build(tu_src, ch_src):
    tu = parse_source(tu_src, "m.tu").module
    ch = parse_source(ch_src, "m.eln").module
    return CostInterp(ch, tu)


def tu_for(alloc_max: str) -> str:
    return f"""spec f(const vector<u64>& xs) -> u64 {{
    effects {{}};
    target {{ time: {{ max: O(xs.length()) }}, allocation: {{ max: {alloc_max} }}, }};
}}
"""


def main() -> int:
    print("=== 字面量与纯运算：0 次分配 ===")
    ci = build(tu_for("0"), """func f(const vector<u64>& xs) -> u64 {
    const vector<u64> s = "hello";
    return s.length() + xs.length();
}
""")
    a = ci.alloc_of("f")
    check("字符串字面量 = 0 次", a.is_const() and a.value == 0, a.render())
    check("0 vs target 0 → 达成", compare_exact(a, ci.target_cost("f", "allocation")).kind == "ok")

    print("=== 弄成一个分配：1 次 ===")
    ci = build(tu_for("0"), """func f(const vector<u64>& xs) -> u64 {
    const vector<u64> t = format_u64(42);
    return t.length();
}
""")
    a = ci.alloc_of("f")
    check("format_u64 = 1 次", a.is_const() and a.value == 1, a.render())
    v = compare_exact(a, ci.target_cost("f", "allocation"))
    check("1 次 vs target 0 → **记债**（不是静默放过）", v.kind == "debt", v.render())

    print("=== 循环里分配：每轮 1 次 ===")
    ci = build(tu_for("O(xs.length())"), """func f(const vector<u64>& xs) -> u64 {
    u64 i = 0;
    while (i < xs.length()) {
        const vector<u64> t = slice(xs, i, i + 1);
        i = i + 1;
    }
    return i;
}
""")
    a = ci.alloc_of("f")
    check("循环内 slice = n(xs) 次", "n(xs)" in a.render(), a.render())
    check("n(xs) vs target O(n) → 达成",
          compare_exact(a, ci.target_cost("f", "allocation")).kind == "ok")

    print("=== 循环里不分配：与轮数无关 ===")
    ci = build(tu_for("0"), """func f(const vector<u64>& xs) -> u64 {
    u64 i = 0;
    u64 s = 0;
    while (i < xs.length()) {
        s = s + xs[i];
        i = i + 1;
    }
    return s;
}
""")
    a = ci.alloc_of("f")
    check("无分配的循环 = 0 次", a.is_const() and a.value == 0, a.render())

    print("=== 一次性 slice：1 次（不论长度） ===")
    ci = build(tu_for("0"), """func f(const vector<u64>& xs) -> u64 {
    const vector<u64> t = slice(xs, 0, xs.length());
    return t.length();
}
""")
    a = ci.alloc_of("f")
    v = compare_exact(a, ci.target_cost("f", "allocation"))
    check("slice 一次 = 1 次（不是 n 次）", a.is_const() and a.value == 1, a.render())
    check("1 次 vs target 0 → 记债", v.kind == "debt", f"{a.render()} → {v.kind}")

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
