#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_guarantees.py —— `guarantees` 从"能写没人看"变成"可判定 + 真校验"。

## 这条测试守什么

1. **分类**：可检查的谓词（`is_sorted` / `contains` / 比较式）进检查器；
   其余**必须**报 `E-Guarantee-Uncheckable`，而不是被静默忽略。
2. **运行时真的校验**：`run` 返回后求值，违反抛 `E-Guarantee-Violated`。
   以前 `guarantees` 写了等于没写——那是最坏的一种"看起来有保证"。
"""
import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import sys

import guarantees as G
from checker import check_module
from interp import Interp, InterpError
from parser import parse_source

_passed = 0
_failed = []


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def spec_of(tu_src):
    mod = parse_source(tu_src, "t.tu").module
    for it in mod.children:
        if it.kind == "spec":
            return it
    return None


TU_SORTED = """
spec f(const Vec<u64>& xs) -> u64 {
    effects {};
    guarantees is_sorted(xs);
}
"""

TU_BAD = """
spec f(const Vec<u64>& xs) -> u64 {
    effects {};
    guarantees looks_good(xs);
}
"""

TU_CMP = """
spec f(u64 n) -> u64 {
    effects {};
    guarantees ret >= 0;
}
"""


def test_classify():
    print("[1] 分类：哪些能检查、哪些不能")
    ok, bad = G.classify(spec_of(TU_SORTED))
    check(len(ok) == 1 and not bad, "is_sorted(xs) 可检查", str(bad))
    ok2, bad2 = G.classify(spec_of(TU_BAD))
    check(not ok2 and len(bad2) == 1, "未知谓词 → 不可检查", str(bad2))
    check("未知谓词" in bad2[0], "原因里指名道姓", bad2[0] if bad2 else "")

    ok3, bad3 = G.classify(spec_of(TU_CMP))
    check(len(ok3) == 1 and not bad3, "ret >= 0 可检查", str(bad3))

    # 实参个数错
    ok4, bad4 = G.classify(spec_of(
        "spec f(u64 n) -> u64 { effects {}; guarantees is_sorted(n, n); }"))
    check(bool(bad4) and "需要" in bad4[0], "实参个数错 → 不可检查", str(bad4))

    # 实参不是变量名
    ok5, bad5 = G.classify(spec_of(
        "spec f(u64 n) -> u64 { effects {}; guarantees is_sorted(n + 1); }"))
    check(bool(bad5), "实参是表达式 → 不可检查", str(bad5))


def test_checker():
    print("[2] check：可检查报 info，不可检查报警告（且不阻断）")
    r = check_module(TU_SORTED, None, "t.tu")
    codes = [d.code for d in r.diagnostics]
    check("I-Guarantee-Runtime" in codes, "可检查 → I-Guarantee-Runtime", str(codes))
    check(r.ok, "info 不影响整体通过")

    r2 = check_module(TU_BAD, None, "t.tu")
    d2 = [d for d in r2.diagnostics if d.code == "E-Guarantee-Uncheckable"]
    check(len(d2) == 1, "不可检查 → E-Guarantee-Uncheckable", str([d.code for d in r2.diagnostics]))
    check(d2 and d2[0].severity == "warning", "是 warning 不是 error",
          d2[0].severity if d2 else "")
    check(r2.ok, "warning 不阻断构建")


def test_runtime():
    print("[3] run：满足则通过，违反则 E-Guarantee-Violated")

    def run(tu_src, ai_src, args):
        tu = parse_source(tu_src, "t.tu").module
        eln = parse_source(ai_src, "t.eln").module
        it = Interp(eln, tu)
        return it.call("f", args)

    TU = """
spec f(const Vec<u64>& xs) -> u64 {
    effects {};
    guarantees is_sorted(xs);
}
"""
    GOOD = """
func f(const Vec<u64>& xs) -> u64 {
    sort(xs);
    return 0;
}
"""
    BAD = """
func f(const Vec<u64>& xs) -> u64 {
    return 0;
}
"""
    try:
        run(TU, GOOD, [[3, 1, 2]])
        check(True, "排好序 → 通过")
    except InterpError as exc:
        check(False, "排好序 → 通过", str(exc))

    try:
        run(TU, BAD, [[3, 1, 2]])
        check(False, "没排序 → 应该被抓", "没有抛异常")
    except InterpError as exc:
        check("E-Guarantee-Violated" in str(exc), "没排序 → E-Guarantee-Violated", str(exc))

    # 比较式：ret >= 0
    TU2 = """
spec f(u64 n) -> u64 {
    effects {};
    guarantees ret >= 0;
}
"""
    GOOD2 = "func f(u64 n) -> u64 { return n; }\n"
    check(run(TU2, GOOD2, [5]) == 5, "ret >= 0 通过，且返回值不被改写")


def main() -> int:
    test_classify()
    test_checker()
    test_runtime()
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
