#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_behavior_parity.py —— 只测「度量尺」的纯逻辑，**不跑 g++ / 不跑 run**。

`tools/behavior_parity.py` 的端到端要编译 6 个 C++ 文件、跑几十次
`python -B src/dsh.py run`（约 20 秒），放进 `tests/runner.py` 会把整套测试拖慢。
所以这里只钉**容易悄悄错、错了会让尺子说谎**的部分：

  · `strip_return_line`：`run` 多打的 main 返回值必须剥掉，否则每个 main 都假红；
  · `diff_report`：不一致时必须给出**两边的行内容**，不能只说「不一致」；
  · `printf_equivalent`：只有「去掉格式串后数值相同」才算已知语义差，
    否则真 bug 会被 printf 掩护过去；
  · 探针生成：两边的输入必须来自**同一份** `PARAMS`；
  · recipe 完整性：探针函数名唯一、原函数真的在语料里。

端到端看 `python -B tools/behavior_parity.py`（验收命令）。
"""
import importlib.util
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
TOOL = os.path.join(ROOT, "tools", "behavior_parity.py")

spec = importlib.util.spec_from_file_location("behavior_parity", TOOL)
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


# ---------------------------------------------------------------- 固定参数
def test_fixed_params():
    print("[固定参数表]")
    check("vec_literal", bp.vec_literal([5, 3, 9]) == "{5, 3, 9}",
          bp.vec_literal([5, 3, 9]))
    check("char_literal(44)", bp.char_literal(44) == "','", bp.char_literal(44))
    check("char_literal 转义单引号", bp.char_literal(39) == "'\\''",
          bp.char_literal(39))
    check("PARAMS.xs 与报告一致", bp.PARAMS["xs"] == [5, 3, 9, 1, 7, 3],
          bp.PARAMS["xs"])
    check("矩阵赋值逐元素", bp.mat_assignments("a", [[1, 2], [3, 4]]).splitlines() == [
        "    a[0][0] = 1;", "    a[0][1] = 2;",
        "    a[1][0] = 3;", "    a[1][1] = 4;"])


# ---------------------------------------------------------------- 返回值剥行
def test_strip_return_line():
    print("[strip_return_line：run 多打的 main 返回值必须剥掉]")
    check("程序有输出 + 返回值 0",
          bp.strip_return_line(b"1 2 3\r\n0\r\n") == b"1 2 3\r\n")
    check("程序没有输出", bp.strip_return_line(b"0\r\n") == b"")
    check("最后一行不是整数就不剥",
          bp.strip_return_line(b"done\r\n") == b"done\r\n")
    check("程序自己打的 0 保留（剥的是最后那行返回值）",
          bp.strip_return_line(b"0\r\n0\r\n") == b"0\r\n")
    check("负返回值也剥", bp.strip_return_line(b"x\r\n-1\r\n") == b"x\r\n")
    check("空输出", bp.strip_return_line(b"") == b"")


# ---------------------------------------------------------------- diff
def test_diff_report():
    print("[diff_report：必须给出两边的行内容]")
    check("相同 → 无差异", bp.diff_report(b"a\r\n", b"a\r\n") == [])
    got = bp.diff_report(b"3 2 7 4\r\n", b"14 14 14 14\r\n")
    blob = "\n".join(got)
    check("报出首个差异字节", "首个差异在字节 0" in blob, blob)
    check("给 g++ 的行", "'3 2 7 4'" in blob, blob)
    check("给 elench 的行", "'14 14 14 14'" in blob, blob)
    check("报出行号", "第 1 行" in blob, blob)
    got2 = bp.diff_report(b"a\r\nb\r\n", b"a\r\nb\r\n")
    check("行完全相同但字节不同时给出解释",
          "行内容相同" in "\n".join(bp.diff_report(b"a\r\n", b"a\n")), got2)


# ---------------------------------------------------------------- printf 语义
def test_printf_semantics():
    print("[printf_equivalent：别让已知语义差掩护真 bug]")
    src = 'std::printf("%d\\n", x);\n'
    # 纯格式串差：去掉 `%d` 后数值相同
    check("纯格式串差 → True",
          bp.printf_equivalent(src, b"4\r\n", b"%d\r\n 4\r\n") is True)
    # 数值也不同 → False（03_matrix::main 就是这种）
    check("数值也不同 → False",
          bp.printf_equivalent(src, b"3 2 7 4\r\n", b"%d\r\n 14 14\r\n") is False)
    check("源码没有 printf → False",
          bp.printf_equivalent("x = 1;\n", b"1\r\n", b"1\r\n") is False)
    check("printf_gap_hint 认得原样打出的格式串",
          bp.printf_gap_hint(src, b"%d\r\n 4\r\n") is True)
    check("printf_gap_hint 不误报",
          bp.printf_gap_hint(src, b"4\r\n") is False)


# ---------------------------------------------------------------- 探针生成
def test_probe_generation():
    print("[探针生成：两边输入来自同一份 PARAMS]")
    eln, cpp = bp._vec_probe("insertion_sort", "insertion_sort(xs)")
    lit = bp.vec_literal(bp.PARAMS["xs"])
    check("eln 侧含同一份字面量", lit in eln, eln)
    check("cpp 侧含同一份字面量", lit in cpp, cpp)
    check("eln 探针名", "probe_insertion_sort" in eln, eln)
    check("eln 侧返回向量", "return xs;" in eln, eln)
    check("cpp 侧打印向量", "print_vec_u64(xs)" in cpp, cpp)

    eln2, cpp2 = bp._str_vec_probe("split", "csv", with_sep=True)
    check("字符串实参两边一致", bp.cpp_string(bp.PARAMS["csv"]) in eln2
          and bp.cpp_string(bp.PARAMS["csv"]) in cpp2, eln2 + cpp2)
    check("分隔符两边一致", "','" in eln2 and "','" in cpp2, eln2 + cpp2)

    # 13 个 recipe 的探针名在各自文件内唯一
    seen = set()
    dup = []
    for r in bp.RECIPES:
        key = (r["file"], r["fn"])
        if key in seen:
            dup.append(key)
        seen.add(key)
    check("recipe 不重复", not dup, str(dup))

    # 每个 runnable recipe 都要有 eln + cpp
    bad = [r["fn"] for r in bp.RECIPES if "skip" not in r
           and ("eln" not in r or "cpp" not in r)]
    check("runnable recipe 两边代码齐全", not bad, str(bad))


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_recipes_cover_real_functions():
    print("[recipe 完整性：探针的函数真的在语料里]")
    corpus = os.path.join(ROOT, "demo", "corpus")
    missing_file = [r["file"] for r in bp.RECIPES
                    if not os.path.isfile(os.path.join(corpus, r["file"]))]
    check("语料文件都存在", not missing_file, str(missing_file))
    for name in sorted({r["file"] for r in bp.RECIPES}):
        text = _read(os.path.join(corpus, name))
        for r in bp.RECIPES:
            if r["file"] != name:
                continue
            check(f"{name} 里有 {r['fn']}", r["fn"] in text)

    # 生成的 .eln 保留原文 + 追加探针
    text = _read(os.path.join(corpus, "01_sorting.cpp"))
    rs = [r for r in bp.RECIPES if r["file"] == "01_sorting.cpp"]
    probe = bp.build_eln_probe(text, rs)
    check("探针文件保留原 main", "int main()" in probe)
    check("探针文件追加了函数", "probe_insertion_sort" in probe)
    check("探针文件不复制源码（原文只出现一次）",
          probe.count("static void insertion_sort") == 1)


def test_driver_generation():
    print("[C++ driver 生成]")
    rs = [r for r in bp.RECIPES
          if r["file"] == "03_matrix.cpp" and "cpp" in r]
    driver = bp.build_cpp_driver(os.path.join(ROOT, "demo", "corpus",
                                              "03_matrix.cpp"), rs)
    check("改名 main 避开冲突", "#define main elench_corpus_main" in driver, driver)
    check("include 源文件", '#include "' in driver, driver)
    check("按名字分派探针", 'which == "probe_matmul"' in driver, driver)
    check("有矩阵打印助手", "print_mat" in driver, driver)


def test_top_level_functions():
    print("[顶层函数清单（只用于报告，不参与判定）]")
    text = _read(os.path.join(ROOT, "demo", "corpus", "01_sorting.cpp"))
    names = bp.top_level_functions(text)
    for want in ("main", "insertion_sort", "merge_halves", "merge_sort"):
        check(f"01_sorting 找到 {want}", want in names, str(names))
    ring = _read(os.path.join(ROOT, "demo", "corpus", "05_ring_buffer.cpp"))
    rnames = bp.top_level_functions(ring)
    check("05 不把成员函数当顶层函数", "push" not in rnames and "pop" not in rnames,
          str(rnames))


def test_summarize():
    print("[汇总：一致率只算可比目标]")
    files = [{
        "file": "x.cpp", "file_reason": None,
        "targets": [
            {"status": "consistent", "cause": None, "fn": "a",
             "deterministic": True, "diff": [], "reason": None},
            {"status": "inconsistent", "cause": "behavior_diff", "fn": "b",
             "deterministic": True, "diff": ["d"], "reason": None},
            {"status": "inconsistent", "cause": "printf_semantics", "fn": "c",
             "deterministic": True, "diff": ["d"], "reason": None},
            {"status": "not_comparable", "cause": "unobservable", "fn": "d",
             "deterministic": None, "diff": [], "reason": "r"},
        ],
    }]
    s = bp.summarize(files)
    check("可比 = 3", s["comparable"] == 3, str(s))
    check("一致 = 1", s["consistent"] == 1, str(s))
    check("不一致 = 2", s["inconsistent"] == 2, str(s))
    check("一致率 = 1/3", abs(s["parity_rate"] - 1 / 3) < 1e-3, str(s))
    check("printf 语义差不算缺陷", len(s["defects"]) == 1, str(s["defects"]))
    check("确定性计数", s["deterministic"] == {"ok": 3, "total": 3}, str(s))


def main():
    for fn in (test_fixed_params, test_strip_return_line, test_diff_report,
               test_printf_semantics, test_probe_generation,
               test_recipes_cover_real_functions, test_driver_generation,
               test_top_level_functions, test_summarize):
        fn()
    if FAILED:
        print(f"\n✗ 失败 {len(FAILED)} 项：{', '.join(FAILED)}")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
