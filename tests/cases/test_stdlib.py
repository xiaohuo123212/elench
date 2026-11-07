#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_stdlib.py —— 最小基本库（L1）的回归测试

覆盖三件事：
1. **解释器**：五个内建真的做了它说的事；
2. **类型检查**：实参个数错要报 `E-Type-ArgCount`；
3. **成本**：`O(n)` 的内建让 κ 里出现 `n`，而不是常数。
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

sys.path.insert(0, os.path.join(SRC, "..", "tools"))

from cost_check import evaluate                      # noqa: E402
from cost_interp import CostInterp                   # noqa: E402
from interp import Interp                            # noqa: E402
from parser import parse_source                      # noqa: E402
from typecheck import TypeChecker                     # noqa: E402

TU = """spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(code, args):
    tu = parse_source(TU, "m.tu").module
    ch = parse_source(code, "m.eln").module
    return Interp(ch, tu).call("f", args)


def main() -> int:
    print("=== 解释器 ===")
    check("reverse 原地反转并返回 n",
          run("""func f(const vector<u64>& xs) -> u64 {
    return reverse(xs) * 1000 + xs.at(0) * 10 + xs.at(1) * 1;
}""", [[1, 2]]) == 2021, "期望 2021")

    check("fill 全部置值",
          run("""func f(const vector<u64>& xs) -> u64 {
    fill(xs, 9);
    return xs.at(0) * 10 + xs.at(1);
}""", [[1, 2]]) == 99, "期望 99")

    check("dedup 去掉相邻重复（返回新长度）",
          run("""func f(const vector<u64>& xs) -> u64 {
    return dedup(xs);
}""", [[1, 1, 2, 2, 3]]) == 3, "期望 3")

    check("dedup 不跨位置去重",
          run("""func f(const vector<u64>& xs) -> u64 {
    return dedup(xs);
}""", [[1, 2, 1]]) == 3, "期望 3（1,2,1 相邻都不等）")

    check("contains 命中/未命中",
          run("""func f(const vector<u64>& xs) -> u64 {
    return contains(xs, 2) * 10 + contains(xs, 7);
}""", [[1, 2, 3]]) == 10, "期望 10")

    check("count_of 计数",
          run("""func f(const vector<u64>& xs) -> u64 {
    return count_of(xs, 1);
}""", [[1, 1, 2]]) == 2, "期望 2")

    print("=== P1：只读查询 ===")
    check("index_of 命中/未命中（哨兵 = n）",
          run("""func f(const vector<u64>& xs) -> u64 {
    return index_of(xs, 2) * 10 + index_of(xs, 9);
}""", [[1, 2, 3]]) == 13, "期望 13（1*10+3）")

    check("binary_search 命中",
          run("""func f(const vector<u64>& xs) -> u64 {
    return binary_search(xs, 3);
}""", [[1, 2, 3, 4]]) == 2, "期望 2")

    check("binary_search 未命中 → n",
          run("""func f(const vector<u64>& xs) -> u64 {
    return binary_search(xs, 5);
}""", [[1, 2, 3, 4]]) == 4, "期望 4")

    check("sum_of 求和",
          run("""func f(const vector<u64>& xs) -> u64 {
    return sum_of(xs);
}""", [[1, 2, 3]]) == 6, "期望 6")

    check("is_sorted 升序/乱序",
          run("""func f(const vector<u64>& xs) -> u64 {
    return is_sorted(xs);
}""", [[1, 2, 3]]) == 1, "期望 1")
    check("is_sorted 乱序 → 0",
          run("""func f(const vector<u64>& xs) -> u64 {
    return is_sorted(xs);
}""", [[1, 3, 2]]) == 0, "期望 0")

    print("=== P2：变换 ===")
    check("slice 保留区间并返回长度",
          run("""func f(const vector<u64>& xs) -> u64 {
    const u64 k = slice(xs, 1, 3);
    return k * 10 + xs.at(0);
}""", [[10, 20, 30, 40]]) == 40, "期望 40（长度 2、首元素 20）")

    check("rotate 左旋 1",
          run("""func f(const vector<u64>& xs) -> u64 {
    rotate(xs, 1);
    return xs.at(0) * 10 + xs.at(1);
}""", [[1, 2, 3]]) == 23, "期望 23")

    check("rotate k=n 不变",
          run("""func f(const vector<u64>& xs) -> u64 {
    rotate(xs, 3);
    return xs.at(0);
}""", [[1, 2, 3]]) == 1, "期望 1")

    check("fill_range 只改区间",
          run("""func f(const vector<u64>& xs) -> u64 {
    fill_range(xs, 1, 3, 9);
    return xs.at(0) * 1000 + xs.at(1) * 10 + xs.at(2);
}""", [[1, 2, 2, 4]]) == 1099, "期望 1099")

    print("=== 哈希表（有界探测）===")
    check("hmap 写入后能读出",
          run("""func f(const vector<u64>& xs) -> u64 {
    array<u64, 16> t = array<u64, 16>::filled(0);
    hmap_put(t, 7, 70);
    hmap_put(t, 23, 230);
    return hmap_get(t, 7) + hmap_get(t, 23);
}""", [[1]]) == 300, "期望 300")

    check("hmap 覆盖写而非追加",
          run("""func f(const vector<u64>& xs) -> u64 {
    array<u64, 16> t = array<u64, 16>::filled(0);
    hmap_put(t, 7, 70);
    hmap_put(t, 7, 99);
    return hmap_get(t, 7);
}""", [[1]]) == 99, "期望 99")

    check("hmap 未命中 → -1（i64）/ has → 0",
          run("""func f(const vector<u64>& xs) -> i64 {
    array<u64, 16> t = array<u64, 16>::filled(0);
    return hmap_get(t, 5);
}""", [[1]]) == -1, "期望 -1")

    check("hmap 存 0 也能读回 0（与缺失可区分）",
          run("""func f(const vector<u64>& xs) -> i64 {
    array<u64, 16> t = array<u64, 16>::filled(0);
    hmap_put(t, 7, 0);
    return hmap_get(t, 7) * 10 + hmap_has(t, 7);
}""", [[1]]) == 1, "期望 1（值 0、存在）")

    check("hmap 缺失时 has = 0",
          run("""func f(const vector<u64>& xs) -> u64 {
    array<u64, 16> t = array<u64, 16>::filled(0);
    return hmap_has(t, 5);
}""", [[1]]) == 0, "期望 0")

    print("=== 类型检查 ===")
    tu = parse_source(TU, "m.tu").module
    bad = parse_source("""func f(const vector<u64>& xs) -> u64 {
    return reverse(xs, xs);
}
""", "m.eln").module
    codes = [d.code for d in TypeChecker(tu, bad).check()]
    check("实参个数错 → E-Type-ArgCount", "E-Type-ArgCount" in codes, str(codes))

    print("=== 成本 ===")
    for fn, want_has_n in (("reverse(xs)", True), ("contains(xs, 1)", True)):
        code = f"""func f(const vector<u64>& xs) -> u64 {{
    return {fn};
}}
"""
        ch = parse_source(code, "m.eln").module
        k = CostInterp(ch, tu).cost_of("f")
        has_n = "n(xs)" in k.render()
        check(f"{fn} 的 κ 含 n", has_n == want_has_n, k.render())

    k = CostInterp(parse_source("""func f(const vector<u64>& xs) -> u64 {
    return reverse(xs);
}
""", "m.eln").module, tu).cost_of("f")
    n16, n64 = evaluate(k, {"xs": 16}), evaluate(k, {"xs": 64})
    check("成本随 n 线性增长", n64 > n16 * 2, f"{n16} → {n64}")

    kb = CostInterp(parse_source("""func f(const vector<u64>& xs) -> u64 {
    return binary_search(xs, 1);
}
""", "m.eln").module, tu).cost_of("f")
    check("binary_search 是 log 阶（16→256 不止翻倍）",
          evaluate(kb, {"xs": 256}) < evaluate(kb, {"xs": 16}) * 20,
          f"{evaluate(kb, {'xs': 16})} → {evaluate(kb, {'xs': 256})}")

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
