#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_builtins.py —— 内建函数（L1：`min` / `max`）

**为什么要内建**：十个手写程序里 **13 处** 用 `if` 串两支求极值
（topk 5、stats 3、merge 2、count 2、search 1）。
**每一处手写都是错的机会**，而极值本身不该由作者表达。

**为什么只收这两个**：内建函数每加一个，就多一个「可撒谎位置」。
收它的门槛是——**成本常数、不改变阶、不引入任意代码**。三条都满足的很少。

**已知边界**：`i64` 负数语义还没修（E8），所以负数用例先记为预期失败；
E8 落地后这些标记 MUST 删掉（`xcheck` 会提示）。
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

sys.stdout.reconfigure(encoding="utf-8")

from interp import Err, Interp, Ok
from parser import parse_source
from typecheck import typecheck


_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def xcheck(actual, expected, name, why, since, blocker):
    """预期失败：记录时间与阻塞项。XPASS 时**必须**提示删标记。"""
    if actual == expected:
        print(f"  XPASS {name} —— 请删掉 xfail 标记（记录于 {since}，阻塞项 {blocker} 已解）")
        return
    check(True, name)          # 仍按预期失败


def interp_of(ch_src, tu_src=None):
    ch = parse_source(ch_src, "t.eln").module
    tu = parse_source(tu_src, "t.tu").module if tu_src else None
    return Interp(ch, tu)


def unwrap(v):
    return v.v if isinstance(v, Ok) else v


# ---------------------------------------------------------------- 值语义

def test_values():
    print("[1] min / max 的值")
    it = interp_of("""u64! f(u64 a, u64 b) {
    return min(a, b) + max(a, b);
}""")
    CASES = [((3, 7), 10), ((7, 3), 10), ((5, 5), 10), ((0, 9), 9)]
    for (a, b), want in CASES:
        got = unwrap(it.call("f", [a, b]))
        check(got == want, f"min({a},{b}) + max({a},{b})", f"实际 {got}，期望 {want}")

    it2 = interp_of("""u64! f(u64 a, u64 b) {
    return min(a, b) * 100 + max(a, b);
}""")
    got = unwrap(it2.call("f", [2, 8]))
    check(got == 208, "min(2,8)*100 + max(2,8) = 208", str(got))


# ---------------------------------------------------------------- 边界

def test_edges():
    print("[2] 边界：相等、0、大数")
    it = interp_of("""u64! f(u64 a, u64 b) {
    return max(a, b) - min(a, b);
}""")
    check(unwrap(it.call("f", [7, 7])) == 0, "相等 → 差 0")
    check(unwrap(it.call("f", [0, 100])) == 100, "0 与 100 → 差 100")
    big = 2 ** 63
    check(unwrap(it.call("f", [big, 1])) == big - 1, "大数边界", str(unwrap(it.call("f", [big, 1]))))


# ---------------------------------------------------------------- 负数（等 E8）

def test_negative_xfail():
    print("[3] 负数（i64 语义未修，等 E4 的 C 批）")

    # `min(-3, 2)` 现在**能过**——负数字面量不走 `_wrap`，原样传给 min。
    # （这条原本标了 xfail，XPASS 提示后改成正常断言。）
    it0 = interp_of("""i64! f(i64 a, i64 b) {
    return min(a, b);
}""")
    check(unwrap(it0.call("f", [-3, 2])) == -3,
          "min(-3, 2) = -3（字面量路径不掩码）",
          str(unwrap(it0.call("f", [-3, 2]))))

    # 真正的缺口：**减法**会把负数掩成无符号大数，于是 min 拿到的已经是错的。
    it = interp_of("""i64! f(i64 a, i64 b) {
    return min(a - b, 0);
}""")
    # **2026-11-12：这条不再 xfail** —— 整数减法早就不把负数掩成无符号大数了
    # （`8f5ce9d` 起的整数语义），实测 `min(1-3, 0)` 就是 -2。
    # xfail 标记留着会 XPASS，反而让 runner 红。
    check(unwrap(it.call("f", [1, 3])) == -2,
          "min(1-3, 0) = -2（减法不再掩成无符号大数）",
          str(unwrap(it.call("f", [1, 3]))))


# ---------------------------------------------------------------- 类型检查

def test_typecheck():
    print("[4] 类型检查：参数个数、类型一致")
    TU = "spec f(u64 a, u64 b) -> u64 {\n    effects {};\n}\n"
    tu = parse_source(TU, "t.tu").module

    ok = typecheck(tu, parse_source("""u64 f(u64 a, u64 b) {
    return min(a, b) + max(a, b);
}""", "t.eln").module)
    check(not ok, "正确用法零诊断", str([d.code for d in ok]))

    one = typecheck(tu, parse_source("""u64 f(u64 a, u64 b) {
    return min(a);
}""", "t.eln").module)
    check(any(d.code == "E-Type-ArgCount" for d in one),
          "少给实参 → E-Type-ArgCount", str([d.code for d in one]))

    mixed = typecheck(tu, parse_source("""u64 f(u64 a, u64 b) {
    return min(a, true);
}""", "t.eln").module)
    check(any(d.code == "E-Type-ArgMismatch" for d in mixed),
          "两个实参类型不同 → E-Type-ArgMismatch", str([d.code for d in mixed]))


# ---------------------------------------------------------------- 成本

def test_cost():
    print("[5] 成本：常数，不改变阶")
    from cost_interp import CostInterp
    from symbolic import Cost

    ch = parse_source("""u64! f(vector<u64>& xs) {
    u64 best = 0;
    u64 i = 0;
    while (i < xs.length()) {
        best = max(best, xs.at(i));
        i = i + 1;
    }
    return best;
}""", "t.eln").module
    k = CostInterp(ch, None).cost_of("f")
    r = k.render()
    # 必须出现一个「乘尺寸」的项（线性），且不得因为 max 而多出阶
    check("n(xs)" in r, "成本式含尺寸变量", r)
    check("n(xs)×n(xs)" not in r.replace(" ", ""),
          "**没有**变成平方阶（max 是常数成本）", r)


def main():
    test_values()
    test_edges()
    test_negative_xfail()
    test_typecheck()
    test_cost()

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
