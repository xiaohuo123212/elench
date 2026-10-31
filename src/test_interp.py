#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_interp.py —— 具体值解释器 + bench

运行：
  cd src; $env:PYTHONIOENCODING="utf-8"; python test_interp.py
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from bench import measure, read_benchmodel
from interp import Err, Interp, Ok
from parser import parse_source

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "..", "文档", "设计", "目前设计", "样例")

_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def load(base):
    tu = parse_source(open(os.path.join(SAMPLE, base + ".tu"), encoding="utf-8").read(),
                      base + ".tu").module
    ch = parse_source(open(os.path.join(SAMPLE, base + ".eln"), encoding="utf-8").read(),
                      base + ".eln").module
    return tu, ch


def ok_of(v):
    return v.v if isinstance(v, Ok) else None


def err_variant(v):
    if not isinstance(v, Err):
        return None
    e = v.e
    return getattr(e, "variant", None)


# ---------------------------------------------------------------- search

def test_search():
    print("[1] search：二分查找")
    tu, ch = load("search")
    it = Interp(ch, tu)

    r = it.call("binary_search", [[1, 3, 5, 7], 5])
    check(isinstance(r, Ok) and r.v == 2, "binary_search([1,3,5,7],5) → 2", str(r))

    r = it.call("binary_search", [[1, 3, 5, 7], 4])
    check(err_variant(r) == "NotFound", "binary_search(…,4) → NotFound", str(r))

    r = it.call("binary_search", [[], 1])
    check(err_variant(r) == "NotFound", "binary_search([],1) → NotFound", str(r))

    # 两个候选都要能选（替换法）
    r = it.call("binary_search", [[1, 3, 5, 7], 7], force={"search_impl": "search_linear"})
    check(isinstance(r, Ok) and r.v == 3, "强制 search_linear 也能找到 7", str(r))

    r = it.call("binary_search", [[2], 2])
    check(isinstance(r, Ok) and r.v == 0, "单元素命中 → 0", str(r))

    r = it.call("binary_search", [[2], 3])
    check(err_variant(r) == "NotFound", "单元素未命中 → NotFound", str(r))


# ---------------------------------------------------------------- sum

def test_sum():
    print("[2] sum：平均")
    tu, ch = load("sum")
    it = Interp(ch, tu)

    check(ok_of(it.call("average", [[1, 2, 3]])) == 2, "average([1,2,3]) → 2")
    check(err_variant(it.call("average", [[]])) == "Empty", "average([]) → Empty")
    check(ok_of(it.call("average", [[10]])) == 10, "average([10]) → 10")
    check(ok_of(it.call("average", [[0, 0, 0, 0]])) == 0, "average([0,0,0,0]) → 0")

    # 两个候选结果必须一致（同阶，只差快慢）
    a = ok_of(it.call("average", [[3, 5, 7]], force={"sum_kernel": "sum_by_index"}))
    b = ok_of(it.call("average", [[3, 5, 7]], force={"sum_kernel": "sum_by_iter"}))
    check(a == b == 5, "两个候选结果一致（都是 5）", f"{a} vs {b}")


# ---------------------------------------------------------------- calculator

def test_calculator():
    print("[3] calculator：表达式求值")
    tu, ch = load("calculator")
    it = Interp(ch, tu)

    def ev(s: bytes):
        return it.call("evaluate", [list(s)])

    check(ok_of(ev(b"1+2*3")) == 7, "1+2*3 → 7", str(ev(b"1+2*3")))
    check(ok_of(ev(b"(1+2)*3")) == 9, "(1+2)*3 → 9", str(ev(b"(1+2)*3")))
    check(ok_of(ev(b"2*3+4")) == 10, "2*3+4 → 10（优先级）", str(ev(b"2*3+4")))
    check(ok_of(ev(b"10-3-2")) == 5, "10-3-2 → 5（左结合）", str(ev(b"10-3-2")))
    check(ok_of(ev(b"8/2")) == 4, "8/2 → 4", str(ev(b"8/2")))
    check(ok_of(ev(b"((1))")) == 1, "((1)) → 1（嵌套括号）", str(ev(b"((1))")))

    check(isinstance(ev(b""), Err), "空串 → Err", str(ev(b"")))
    check(isinstance(ev(b"1+"), Err), "残缺表达式 → Err", str(ev(b"1+")))
    check(isinstance(ev(b"1/0"), Err), "除零 → Err", str(ev(b"1/0")))
    check(isinstance(ev(b"1$a"), Err), "非法字符 → Err", str(ev(b"1$a")))


# ---------------------------------------------------------------- 回绕

def test_wrapping():
    print("[4] 算术回绕（v0.67 全函数化）")
    src = """func f(u64 a, u64 b) -> u64 {
    return a + b;
}
func g(u64 a, u64 b) -> u64 {
    return a * b;
}
func h(u64 a) -> u64 {
    return 0 - a;
}"""
    r = parse_source(src, "w.eln")
    check(not r.diagnostics, "回绕用例能解析",
          "; ".join(d.code for d in r.diagnostics))
    it = Interp(r.module, None)
    M = 1 << 64
    check(it.call("f", [M - 1, 1]) == 0, "u64 最大值 + 1 → 回绕到 0",
          str(it.call("f", [M - 1, 1])))
    check(it.call("g", [M - 1, 2]) == M - 2, "u64 最大值 × 2 → 回绕",
          str(it.call("g", [M - 1, 2])))
    check(it.call("h", [3]) == M - 3, "0 - 3 → 回绕", str(it.call("h", [3])))


# ---------------------------------------------------------------- 死循环保护

def test_budget():
    print("[5] 死循环必须有上限（不许把工具挂住）")
    src = """func f(u64 n) -> u64 {
    u64 i = 0;
    while (i < n) {
        i = i;
    }
    return i;
}"""
    from interp import InterpBudgetExceeded
    it = Interp(parse_source(src, "loop.eln").module, None, budget=10_000)
    try:
        it.call("f", [5])
        check(False, "死循环应当抛 InterpBudgetExceeded")
    except InterpBudgetExceeded:
        check(True, "死循环抛 InterpBudgetExceeded")


# ---------------------------------------------------------------- bench

def test_bench():
    print("[6] 真实计时（bench）")
    tu, ch = load("search")
    data, scale, seed = read_benchmodel(tu)
    check(bool(data), "读到 benchmodel.data", str(data))
    check(bool(scale), "读到 benchmodel.scale", str(scale))
    check(isinstance(seed, int) and seed >= 0, "读到 benchmodel.seed", str(seed))

    SIZES = [16, 256]        # 跨度够大，阶差才盖过解释器的固定开销
    lin = measure(ch, tu, "binary_search", SIZES, force={"search_impl": "search_linear"})
    lg = measure(ch, tu, "binary_search", SIZES, force={"search_impl": "search_binary"})
    check(set(lin) == set(SIZES) and set(lg) == set(SIZES),
          "两个候选都测到两个规模", f"{lin} / {lg}")
    check(all(v > 0 for v in lin.values()) and all(v > 0 for v in lg.values()),
          "耗时都是正数")

    # 阶差证据：**线性相对对数越来越吃亏**
    #   （n=16 时对数因初始化开销反而更慢；n=256 时线性已明显更慢）
    r_small = lin[SIZES[0]] / lg[SIZES[0]]
    r_big = lin[SIZES[1]] / lg[SIZES[1]]
    check(r_big > r_small * 2.0,
          "线性候选相对对数候选越来越慢（实测出阶差）",
          f"lin/log: {r_small:.2f} → {r_big:.2f}")


# ---------------------------------------------------------------- 每函数计数

def test_per_function_calls():
    print("[6.5] 每函数调用数（M1 验收③）")
    tu, ch = load("calculator")
    it = Interp(ch, tu)
    it.call("evaluate", [list(b"1+2*3")])
    c = it.counters()
    fc = c["function_calls"]
    check(isinstance(fc, dict), "function_calls 是字典", str(type(fc)))
    check(fc.get("evaluate") == 1, "evaluate 调用了 1 次", str(fc))
    check(fc.get("is_digit", 0) > 0, "is_digit 被调用过", str(fc))
    check(sum(fc.values()) == c["function_calls_total"],
          "合计等于各项之和", f"{fc} vs {c['function_calls_total']}")
    check(len(fc) >= 5, "至少记录 5 个函数", str(sorted(fc)))


# ---------------------------------------------------------------- 预期失败（xfail）

# **为什么要留红着的测试**：
#   i64 负数现在算错（E8），真正的修法是「值携带类型」，那件事并入了 E4。
#   **把盲点写成一条红着的记录，比留在脑子里可靠**——它会在每次回归时提醒我们。
#
# **规矩**：
#   1. xfail 不计入通过数，也不阻断回归（退出码仍是 0）
#   2. 若某条 xfail **意外通过**（XPASS），必须打印警告并提示删除标记
#      —— 因为那意味着 E4 已经修好了，标记该拆
#   3. 每条 xfail MUST 写明「记录时间」与「等谁修」

_XFAIL = []
_XPASS = []


def xcheck(actual, expected, name, why, since, blocker):
    """预期失败的断言。

    actual/expected：实际值与应有的值
    why            ：为什么现在错
    since          ：记录时间（YYYY-MM-DD HH:MM）
    blocker        ：等谁修（如 "E4"）
    """
    if actual == expected:
        _XPASS.append(f"{name}  （{blocker} 似乎已修好，请删掉这条 xfail）")
        print(f"  XPASS  {name}  → 意外通过，{blocker} 可能已经修了")
        return
    _XFAIL.append(f"{name}  期望 {expected}，实际 {actual}  记录于 {since}，等 {blocker}")
    print(f"  xfail  {name}  → 实际 {actual}（期望 {expected}），等 {blocker}")


def test_negative_numbers_xfail():
    print("[7] 负数：预期失败（E8，等 E4）")

    # ① 减法结果是负数
    src1 = """func sub(i64 a, i64 b) -> i64 {
    return a - b;
}"""
    it1 = Interp(parse_source(src1, "neg.eln").module, None)
    xcheck(it1.call("sub", [3, 10]), -7,
           "i64 减法 3-10",
           "解释器按 u64 掩码回绕，负数变成巨大正数",
           "2026-10-29 21:42", "E4")

    # ② 一元取负
    src2 = """func neg(i64 a) -> i64 {
    return 0 - a;
}"""
    it2 = Interp(parse_source(src2, "neg2.eln").module, None)
    xcheck(it2.call("neg", [5]), -5,
           "i64 取负 0-5",
           "同上",
           "2026-10-29 21:42", "E4")

    # ③ 全负数组求和
    src3 = """func sum3(i64 a, i64 b, i64 c) -> i64 {
    return a + b + c;
}"""
    it3 = Interp(parse_source(src3, "neg3.eln").module, None)
    xcheck(it3.call("sum3", [-1, -2, -3]), -6,
           "i64 全负数相加",
           "同上；注意正数相加是对的，只有结果跨过 0 才暴露",
           "2026-10-29 21:42", "E4")

    # ④ calculator 的负数结果（真样例）
    tu, ch = load("calculator")
    it4 = Interp(ch, tu)
    got = it4.call("evaluate", [list(b"1-3")])
    got_v = got.v if isinstance(got, Ok) else got
    xcheck(got_v, -2,
           "calculator 求值 1-3",
           "端到端：样例产出错误答案",
           "2026-10-29 21:42", "E4")


def main():
    test_search()
    test_sum()
    test_calculator()
    test_wrapping()
    test_budget()
    test_bench()
    test_per_function_calls()
    test_negative_numbers_xfail()

    print()
    if _XFAIL:
        print(f"预期失败 {len(_XFAIL)} 项（不阻断回归，等 E4 修好）：")
        for f in _XFAIL:
            print("  xfail -", f)
        print()
    if _XPASS:
        print(f"⚠ 意外通过 {len(_XPASS)} 项 —— E4 可能已修好，请删掉对应 xfail：")
        for f in _XPASS:
            print("  XPASS -", f)
        print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项" + (f"（另有 {len(_XFAIL)} 项预期失败）" if _XFAIL else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
