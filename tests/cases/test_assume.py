#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

"""test_assume.py —— 批次 S：`assume` 与外部调用（2026-10-30）

`assume` 是"环境参数的**上界**"声明，属于**可撒谎位置**（信任区）。
所以有三条硬要求：

1. **只能写上界**（`<=` / `<`）——写 `=` 或 `>=` 一律报 `E-Assume-NotUpperBound`；
2. **报告 MUST 打印它**（读者要知道自己离真相多远）；
3. **它必须真的起作用**：`assume n <= xs.length();` 应当让 `while (i < n)` 的界
   从 `n(n)` 归约成 `n(xs)`。
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

import sys

from parser import parse_source, ParseError
from cost_interp import CostInterp

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


CH = """func f(const Vec<u64>& xs) -> u64! {
    u64 s = 0; u64 i = 0;
    while (i < n) { s = s + 1; i = i + 1; }
    return s;
}"""


def test_assume_works():
    print("[1] assume 让环境变量归约成尺寸")
    tu = "assume n <= xs.length();\nspec f(const Vec<u64>& xs) -> u64 { effects { }; }\n"
    ci = CostInterp(parse_source(CH, "a.eln").module,
                    parse_source(tu, "a.tu").module)
    k = ci.cost_of("f").render()
    check(ci.assumptions.get("n") is not None, "假设被收集", str(ci.assumptions))
    check("n(n)" not in k, "κ 里不再有 n(n)", k)
    check("n(xs)" in k, "κ 里出现 n(xs)", k)


def test_without_assume():
    print("[2] 没有 assume 时仍是 n(n)（不能凭空猜）")
    ci = CostInterp(parse_source(CH, "a.eln").module, None)
    k = ci.cost_of("f").render()
    check("n(n)" in k, "无假设 → 保留 n(n)", k)


def test_reject_not_upper_bound():
    print("[3] 非上界写法一律拒绝（E-Assume-NotUpperBound）")
    for src, why in (("assume n = 5;", "等号"),
                     ("assume n >= 5;", "下界"),
                     ("assume n > 5;", "下界（严格）")):
        # 解析器把错误**记成诊断**（不抛），所以要查 diagnostics
        try:
            r = parse_source(src, "a.tu")
            codes = [getattr(d, "code", "") for d in (getattr(r, "diagnostics", None) or [])]
            check("E-Assume-NotUpperBound" in codes, f"{why}被拒绝", str(codes))
        except ParseError as e:
            check("Assume" in str(getattr(e, "code", "")),
                  f"{why}被拒绝（异常）", str(e)[:60])


def test_report_prints():
    print("[4] 报告打印假设（信任区）")
    import subprocess, tempfile, os
    tu = "assume n <= xs.length();\nspec f(const Vec<u64>& xs) -> u64 { effects { }; }\n"
    with tempfile.TemporaryDirectory() as d:
        p1, p2 = os.path.join(d, "a.tu"), os.path.join(d, "a.eln")
        open(p1, "w", encoding="utf-8").write(tu)
        open(p2, "w", encoding="utf-8").write(CH)
        out = subprocess.run([sys.executable, "dsh.py", "cost", p1, p2],
                             capture_output=True, text=True, encoding="utf-8").stdout
    check("假设" in out and "xs.length()" in out, "报告里有「假设：n ≤ xs.length()」", out[:120])


def test_extern_counted_not_priced():
    """批次 S：外部符号**只数次数，不估价格**。

    理由：「调了几次」可静态算、可验证；「每次多贵」属环境，
    写进 `.tu` 就是又一处**可撒谎位置**。
    """
    print("[5] 外部调用只数次数")
    tu = "extern ext(const Vec<u64>& xs) -> u64;\nspec f(const Vec<u64>& xs) -> u64 { effects { }; }\n"
    ch = """func f(const Vec<u64>& xs) -> u64! {
    u64 s = 0; u64 i = 0;
    while (i < xs.length()) { s = s + ext(xs); i = i + 1; }
    return s;
}"""
    ci = CostInterp(parse_source(ch, "a.eln").module, parse_source(tu, "a.tu").module)
    k = ci.cost_of("f")
    check("ext" in ci.externs, "extern 被收集", str(list(ci.externs)))
    check(ci.external_calls.get("ext", 0) >= 1, "外部调用被计数", str(ci.external_calls))
    check("n(xs)" in k.render(), "κ 里没有被外部符号污染", k.render())


def test_undefined_call_hint():
    """未声明的调用要提示去 `extern` 登记（否则无法只数次数）。"""
    print("[6] 未声明函数 → 提示 extern 登记")
    from typecheck import typecheck
    tu = "spec f(const Vec<u64>& xs) -> u64 { effects { }; }\n"
    ch = "func f(const Vec<u64>& xs) -> u64! { return no_such_fn(xs); }\n"
    ds = typecheck(parse_source(tu, "a.tu").module, parse_source(ch, "a.eln").module)
    hit = [d for d in ds if "Undefined" in getattr(d, "code", "")]
    check(bool(hit), "报了未声明函数", str([getattr(d, "code", "?") for d in ds])[:80])
    check(any("extern" in str(d) for d in hit), "提示里有 extern", str(hit[:1])[:100])


def main():
    test_extern_counted_not_priced()
    test_undefined_call_hint()
    test_assume_works()
    test_without_assume()
    test_reject_not_upper_bound()
    test_report_prints()
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
