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
          run("""u64 f(const vector<u64>& xs) {
    return reverse(xs) * 1000 + xs.at(0) * 10 + xs.at(1) * 1;
}""", [[1, 2]]) == 2021, "期望 2021")

    check("fill 全部置值",
          run("""u64 f(const vector<u64>& xs) {
    fill(xs, 9);
    return xs.at(0) * 10 + xs.at(1);
}""", [[1, 2]]) == 99, "期望 99")

    check("dedup 去掉相邻重复（返回新长度）",
          run("""u64 f(const vector<u64>& xs) {
    return dedup(xs);
}""", [[1, 1, 2, 2, 3]]) == 3, "期望 3")

    check("dedup 不跨位置去重",
          run("""u64 f(const vector<u64>& xs) {
    return dedup(xs);
}""", [[1, 2, 1]]) == 3, "期望 3（1,2,1 相邻都不等）")

    check("contains 命中/未命中",
          run("""u64 f(const vector<u64>& xs) {
    return contains(xs, 2) * 10 + contains(xs, 7);
}""", [[1, 2, 3]]) == 10, "期望 10")

    check("count_of 计数",
          run("""u64 f(const vector<u64>& xs) {
    return count_of(xs, 1);
}""", [[1, 1, 2]]) == 2, "期望 2")

    print("=== P1：只读查询 ===")
    check("index_of 命中/未命中（哨兵 = n）",
          run("""u64 f(const vector<u64>& xs) {
    return index_of(xs, 2) * 10 + index_of(xs, 9);
}""", [[1, 2, 3]]) == 13, "期望 13（1*10+3）")

    check("binary_search 命中",
          run("""u64 f(const vector<u64>& xs) {
    return binary_search(xs, 3);
}""", [[1, 2, 3, 4]]) == 2, "期望 2")

    check("binary_search 未命中 → n",
          run("""u64 f(const vector<u64>& xs) {
    return binary_search(xs, 5);
}""", [[1, 2, 3, 4]]) == 4, "期望 4")

    check("sum_of 求和",
          run("""u64 f(const vector<u64>& xs) {
    return sum_of(xs);
}""", [[1, 2, 3]]) == 6, "期望 6")

    check("is_sorted 升序/乱序",
          run("""u64 f(const vector<u64>& xs) {
    return is_sorted(xs);
}""", [[1, 2, 3]]) == 1, "期望 1")
    check("is_sorted 乱序 → 0",
          run("""u64 f(const vector<u64>& xs) {
    return is_sorted(xs);
}""", [[1, 3, 2]]) == 0, "期望 0")

    print("=== P2：变换 ===")
    check("slice 保留区间并返回长度",
          run("""u64 f(const vector<u64>& xs) {
    const u64 k = slice(xs, 1, 3);
    return k * 10 + xs.at(0);
}""", [[10, 20, 30, 40]]) == 40, "期望 40（长度 2、首元素 20）")

    check("rotate 左旋 1",
          run("""u64 f(const vector<u64>& xs) {
    rotate(xs, 1);
    return xs.at(0) * 10 + xs.at(1);
}""", [[1, 2, 3]]) == 23, "期望 23")

    check("rotate k=n 不变",
          run("""u64 f(const vector<u64>& xs) {
    rotate(xs, 3);
    return xs.at(0);
}""", [[1, 2, 3]]) == 1, "期望 1")

    check("fill_range 只改区间",
          run("""u64 f(const vector<u64>& xs) {
    fill_range(xs, 1, 3, 9);
    return xs.at(0) * 1000 + xs.at(1) * 10 + xs.at(2);
}""", [[1, 2, 2, 4]]) == 1099, "期望 1099")

    print("=== 哈希表（有界探测）===")
    check("hmap 写入后能读出",
          run("""u64 f(const vector<u64>& xs) {
    array<u64, 16> t = array<u64, 16>::filled(0);
    hmap_put(t, 7, 70);
    hmap_put(t, 23, 230);
    return hmap_get(t, 7) + hmap_get(t, 23);
}""", [[1]]) == 300, "期望 300")

    check("hmap 覆盖写而非追加",
          run("""u64 f(const vector<u64>& xs) {
    array<u64, 16> t = array<u64, 16>::filled(0);
    hmap_put(t, 7, 70);
    hmap_put(t, 7, 99);
    return hmap_get(t, 7);
}""", [[1]]) == 99, "期望 99")

    check("hmap 未命中 → -1（i64）/ has → 0",
          run("""i64 f(const vector<u64>& xs) {
    array<u64, 16> t = array<u64, 16>::filled(0);
    return hmap_get(t, 5);
}""", [[1]]) == -1, "期望 -1")

    check("hmap 存 0 也能读回 0（与缺失可区分）",
          run("""i64 f(const vector<u64>& xs) {
    array<u64, 16> t = array<u64, 16>::filled(0);
    hmap_put(t, 7, 0);
    return hmap_get(t, 7) * 10 + hmap_has(t, 7);
}""", [[1]]) == 1, "期望 1（值 0、存在）")

    check("hmap 缺失时 has = 0",
          run("""u64 f(const vector<u64>& xs) {
    array<u64, 16> t = array<u64, 16>::filled(0);
    return hmap_has(t, 5);
}""", [[1]]) == 0, "期望 0")

    print("=== 类型检查 ===")
    tu = parse_source(TU, "m.tu").module
    bad = parse_source("""u64 f(const vector<u64>& xs) {
    return reverse(xs, xs);
}
""", "m.eln").module
    codes = [d.code for d in TypeChecker(tu, bad).check()]
    check("实参个数错 → E-Type-ArgCount", "E-Type-ArgCount" in codes, str(codes))

    print("=== 内建返回类型登记（K5） ===")
    # `ret=` 是内建返回类型的**唯一登记处**：`interp` 用它打标记、`typecheck`
    # 用它推类型、`gen_builtins_doc.py` 用它生成文档。2026-11-10 之前
    # `vec_pop` / `map_get` / `open_read` 等 17 个内建登记的是 `ret="any"`，
    # 而 `typecheck` 把 `any` 当 `UNKNOWN`——`u64 x = vec_pop(h)` 静默通过。
    # 下面三条把"登记是否齐全、有没有消费分支、有没有人又用 any 偷懒"钉住。
    import stdlib as _stdlib
    from typecheck import _builtin_ret_ty, TY_ANY
    ret_outside = sorted({v["ret"] for v in _stdlib.FNS.values()} - _stdlib.RET_KINDS)
    check("每个内建的 ret 都在 RET_KINDS 里", not ret_outside, str(ret_outside))
    unhandled = sorted(k for k in _stdlib.RET_KINDS
                       if k != "any" and _builtin_ret_ty(k, []) is TY_ANY)
    check("RET_KINDS 的每个词都有 typecheck 分支", not unhandled, str(unhandled))
    any_left = sorted(n for n, v in _stdlib.FNS.items() if v["ret"] == "any")
    check("没有内建再用 ret=\"any\" 偷懒", not any_left, str(any_left))

    print("=== 成本 ===")
    for fn, want_has_n in (("reverse(xs)", True), ("contains(xs, 1)", True)):
        code = f"""u64 f(const vector<u64>& xs) {{
    return {fn};
}}
"""
        ch = parse_source(code, "m.eln").module
        k = CostInterp(ch, tu).cost_of("f")
        has_n = "n(xs)" in k.render()
        check(f"{fn} 的 κ 含 n", has_n == want_has_n, k.render())

    k = CostInterp(parse_source("""u64 f(const vector<u64>& xs) {
    return reverse(xs);
}
""", "m.eln").module, tu).cost_of("f")
    n16, n64 = evaluate(k, {"xs": 16}), evaluate(k, {"xs": 64})
    check("成本随 n 线性增长", n64 > n16 * 2, f"{n16} → {n64}")

    kb = CostInterp(parse_source("""u64 f(const vector<u64>& xs) {
    return binary_search(xs, 1);
}
""", "m.eln").module, tu).cost_of("f")
    check("binary_search 是 log 阶（16→256 不止翻倍）",
          evaluate(kb, {"xs": 256}) < evaluate(kb, {"xs": 16}) * 20,
          f"{evaluate(kb, {'xs': 16})} → {evaluate(kb, {'xs': 256})}")

    print("=== ③ 数学内建：值域按 C，成本 O(1)、分配 0 ===")
    # 这批以前**一个都没有**（`E-Call-Undeclared`），而 FFI 也不行
    # （C ABI 后端不支持 `f64` 参数）——`_cppmatrix/misc/results/24*`。
    import math as _math
    from stdlib import call as _c
    import stdlib as _std

    check("sqrt(9.0) = 3.0", _c("sqrt", [9.0]) == 3.0, repr(_c("sqrt", [9.0])))
    check("sqrt(-1.0) 是 NaN（C 语义，不是报错、不是 0）",
          _math.isnan(_c("sqrt", [-1.0])), repr(_c("sqrt", [-1.0])))
    check("floor(3.7) = 3.0 且是 float（不是 u64）",
          _c("floor", [3.7]) == 3.0 and isinstance(_c("floor", [3.7]), float),
          repr(_c("floor", [3.7])))
    check("floor(-3.7) = -4.0", _c("floor", [-3.7]) == -4.0, repr(_c("floor", [-3.7])))
    check("ceil(3.2) = 4.0 / ceil(-3.2) = -3.0",
          _c("ceil", [3.2]) == 4.0 and _c("ceil", [-3.2]) == -3.0)
    check("round(2.5) = 3.0（.5 远离零，不是银行家舍入）",
          _c("round", [2.5]) == 3.0, repr(_c("round", [2.5])))
    check("round(-2.5) = -3.0", _c("round", [-2.5]) == -3.0, repr(_c("round", [-2.5])))
    check("round(2.4) = 2.0 / round(2.6) = 3.0",
          _c("round", [2.4]) == 2.0 and _c("round", [2.6]) == 3.0)
    check("fabs(-1.5) = 1.5", _c("fabs", [-1.5]) == 1.5)
    check("fabs(-0.0) = 0.0 且符号为正",
          _c("fabs", [-0.0]) == 0.0 and _math.copysign(1.0, _c("fabs", [-0.0])) > 0)
    check("abs：i64 -5 → 5、u64 5 → 5、f64 -1.5 → 1.5",
          _c("abs", [-5]) == 5 and _c("abs", [5]) == 5 and _c("abs", [-1.5]) == 1.5,
          repr([_c("abs", [-5]), _c("abs", [5]), _c("abs", [-1.5])]))
    check("log2(8.0) = 3.0 / log2(1.0) = 0.0",
          _c("log2", [8.0]) == 3.0 and _c("log2", [1.0]) == 0.0)
    check("log2(0.0) = -inf（C 语义）", _c("log2", [0.0]) == float("-inf"))
    check("log2(-1.0) 是 NaN", _math.isnan(_c("log2", [-1.0])))
    check("popcount(255) = 8 / popcount(0) = 0 / popcount(1) = 1",
          (_c("popcount", [255]), _c("popcount", [0]), _c("popcount", [1])) == (8, 0, 1))
    check("clz(1) = 63 / clz(255) = 56",
          (_c("clz", [1]), _c("clz", [255])) == (63, 56))
    check("ctz(8) = 3 / ctz(1) = 0", (_c("ctz", [8]), _c("ctz", [1])) == (3, 0))

    # **`clz(0)` / `ctz(0)` 报错**（C 是 UB；本项目不编值——见"数学内建"一节）
    for nm in ("clz", "ctz"):
        try:
            _c(nm, [0])
            check(f"{nm}(0) → 报错", False, "没有报错（返回了编出来的值？）")
        except Exception as exc:                    # noqa: BLE001
            check(f"{nm}(0) → 报错", "未定义" in str(exc), str(exc)[:60])

    # 登记：`ret` / `cost` / `alloc` 三处都在
    for nm, rk in (("sqrt", "f64"), ("floor", "f64"), ("ceil", "f64"),
                   ("round", "f64"), ("fabs", "f64"), ("log2", "f64"),
                   ("abs", "arg0"), ("popcount", "u64"), ("clz", "u64"),
                   ("ctz", "u64")):
        check(f"{nm}：ret={rk} / cost=1 / alloc=0",
              _std.ret_kind(nm) == rk and _std.cost_kind(nm) == "1"
              and _std.alloc_kind(nm) == "0",
              f"ret={_std.ret_kind(nm)} cost={_std.cost_kind(nm)} "
              f"alloc={_std.alloc_kind(nm)}")
        check(f"{nm}：声明了失败语义", bool(_std.failure_mode(nm)), "")

    # 类型：`f64` / `arg0` 两个 ret 词都要有消费分支（否则 K5 的洞又开一个）
    from typecheck import TY_F64, TY_I64 as _TY_I64
    check("ret=f64 → 类型 f64", _builtin_ret_ty("f64", []) is TY_F64)
    check("ret=arg0 → 跟实参（i64 进 i64 出）",
          _builtin_ret_ty("arg0", [_TY_I64]) is _TY_I64)

    # 端到端：`f64 r = sqrt(...)` 静态通过；`clz(0)` 是运行期错（不是静态）
    ok = [d.code for d in TypeChecker(tu, parse_source("""u64 f(const vector<u64>& xs) {
    f64 r = sqrt(16.0);
    u64 p = popcount(7);
    i64 a = abs(xs.at(0));
    return p + (r as u64) + (a as u64);
}
""", "m.eln").module).check()]
    check("数学内建的调用点静态通过", not ok, str(ok))

    # 成本：放在循环里是**线性**、常数因子不随数值大小走
    kc = CostInterp(parse_source("""u64 f(const vector<u64>& xs) {
    u64 c = 0;
    for (auto x : xs) {
        c = c + popcount(x) + clz(x) + ctz(x);
    }
    return c;
}
""", "m.eln").module, tu).cost_of("f")
    c16, c64 = evaluate(kc, {"xs": 16}), evaluate(kc, {"xs": 64})
    check("数学内建在循环里是线性（16→64 涨 4 倍）",
          c16 > 0 and 3.0 < c64 / c16 < 5.0, f"{c16} → {c64}")

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
