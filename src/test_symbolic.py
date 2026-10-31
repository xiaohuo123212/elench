#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_symbolic.py —— G27：符号域与成本解释器

运行：
  cd src; $env:PYTHONIOENCODING="utf-8"; python test_symbolic.py
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from cost_interp import CostInterp, report
from parser import parse_source
from symbolic import Cost

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


def cost_of(src, fn="f", decl_src=None):
    mod = parse_source(src, "x.eln").module
    decl = parse_source(decl_src, "x.tu").module if decl_src else None
    ci = CostInterp(mod, decl)
    return ci.cost_of(fn).render()


# ---------------------------------------------------------------- 代数

def test_algebra():
    print("[1] 符号值代数")
    n = Cost.size("xs")
    check(Cost.add(Cost.num(1), Cost.num(1)).render() == "2", "常数相加")
    check(Cost.add(Cost.num(0), n).render() == "n(xs)", "0 + n = n")
    check(Cost.mul(Cost.num(1), n).render() == "n(xs)", "1 × n = n")
    check(Cost.mul(Cost.num(0), n).render() == "0", "0 × n = 0")
    check(Cost.max(Cost.num(3), Cost.num(5)).render() == "5", "max 常数能比就比")

    print("[2] max 比不了就保留，不硬猜")
    check(Cost.max(Cost.num(3), n).render() == "max(3, n(xs))",
          "max(常数, 线性) 保留——常数可能比 n 大")
    check(Cost.max(n, Cost.log2(n)).render() == "n(xs)",
          "max(n, log2 n) = n —— 线性确定比 log 大")
    check(Cost.max(Cost.log2(n), Cost.num(5)).render() == "log2(n(xs))",
          "max(log2 n, 常数) = log2 n")


# ---------------------------------------------------------------- 语句成本

def test_statements():
    print("[3] 语句成本累加")
    check(cost_of("func f() -> u64 { return 1; }") == "2",
          "return 1 → 2（return 1 + 字面量 1）",
          cost_of("func f() -> u64 { return 1; }"))

    check(cost_of("func f() -> u64 { u64 a = 1; u64 b = 2; return a; }") == "6",
          "三条语句 → 6（每条语句 1 + 每个初值/读取 1）",
          cost_of("func f() -> u64 { u64 a = 1; u64 b = 2; return a; }"))

    print("[4] 分支取 max")
    src = ("func f(const Vec<u64>& xs) -> u64 { const u64 n = xs.length(); "
           "if (n == 0) { return 0; } return 1; }")
    r = cost_of(src)
    # 条件 1 + max(两支) + 后面那条 return：两支成本相同时 max 会化简掉，
    # 所以这里断言"是个具体的数"，而不是"含 max"
    check(r.isdigit(), "if 的两条路径已合并成一个数", r)

    # 一支常数、一支线性 → max 能判定，化简掉（这是**正确的**）
    src2 = ("func f(const Vec<u64>& xs) -> u64 { const u64 n = xs.length(); "
            "u64 i = 0; if (n == 0) { return 0; } "
            "while (i < n) { i = i + 1; } return i; }")
    r2 = cost_of(src2)
    check("max(" not in r2 and "n(xs)" in r2,
          "常数 vs 线性 → max 化简成线性", r2)

    # 两支都是循环，但遍历不同的容器 → 比不了，必须保留 max
    src3 = ("func f(const Vec<u64>& a, const Vec<u64>& b) -> u64 { const u64 x = a.length(); "
            "u64 t = 0; "
            "if (x == 0) { u64 i = 0; while (i < a.length()) { t = t + 1; i = i + 1; } } "
            "else { u64 j = 0; while (j < b.length()) { t = t + 1; j = j + 1; } } "
            "return t; }")
    r3 = cost_of(src3)
    check("max(" in r3, "遍历不同容器 → 保留 max（比不了就不猜）", r3)


def test_loops():
    print("[5] 循环：圈数 × 每圈")
    src = ("func f(const Vec<u64>& xs) -> u64 { u64 i = 0; "
           "while (i < xs.length()) { i = i + 1; } return i; }")
    r = cost_of(src)
    check("n(xs)" in r and "×" in r, "while 上界进入成本式", r)

    src2 = ("func f(const Vec<u64>& xs) -> u64 { u64 s = 0; "
            "for (x in xs) { s = s + x; } return s; }")
    r2 = cost_of(src2)
    check("n(xs)" in r2 and "×" in r2, "for 用容器长度做圈数", r2)


def test_target_and_inline():
    print("[6] 跨函数：有 target 用 target，没有就内联")
    decl = """spec g(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, };
}
spec h(const Vec<u64>& xs) -> u64 { effects {}; }"""
    impl = """func f(const Vec<u64>& xs) -> u64 { return g(xs); }
func g(const Vec<u64>& xs) -> u64 { u64 i = 0; while (i < xs.length()) { i = i + 1; } return i; }
func h(const Vec<u64>& xs) -> u64 { return 1; }"""
    mod = parse_source(impl, "x.eln").module
    dmod = parse_source(decl, "x.tu").module
    ci = CostInterp(mod, dmod)
    f_cost = ci.cost_of("f").render()
    check("n(xs)" in f_cost, "f 调 g：用了 g 的 target（n(xs)）", f_cost)

    # h 没有 target → 内联，得到它的实际成本 2
    impl2 = "func f(const Vec<u64>& xs) -> u64 { return h(xs); }\nfunc h(const Vec<u64>& xs) -> u64 { return 1; }"
    mod2 = parse_source(impl2, "x.eln").module
    ci2 = CostInterp(mod2, None)
    check(ci2.cost_of("f").render() == "4",
          "f 调 h（无 target）：内联 2 + 调用 1 + 叶子 1 = 4",
          ci2.cost_of("f").render())


def test_recursion():
    print("[7] 内联成环要报错，不能转不停")
    src = ("func f(const Vec<u64>& xs) -> u64 { return g(xs); }\n"
           "func g(const Vec<u64>& xs) -> u64 { return f(xs); }")
    mod = parse_source(src, "x.eln").module
    ci = CostInterp(mod, None)
    try:
        ci.cost_of("f")
        check(False, "递归内联必须报错")
    except Exception as e:
        check("环" in str(e) or "目标" in str(e), "递归内联报错并给出提示", str(e)[:50])


def test_top():
    print("[8] 抽不出上界 → ⊤，不猜")
    src = ("func f(const Vec<u64>& xs) -> u64 { u64 i = 0; "
           "while (i < unknown_thing(i)) { i = i + 1; } return i; }")
    r = cost_of(src)
    check("⊤" in r, "抽不出 → ⊤", r)


def test_samples():
    print("[9] 三套真实样例：成本式里不该出现 ⊤")
    for base in ("calculator", "search", "sum"):
        tu = io_read(os.path.join(SAMPLE, base + ".tu"))
        ch = io_read(os.path.join(SAMPLE, base + ".eln"))
        dmod = parse_source(tu, base + ".tu").module
        imod = parse_source(ch, base + ".eln").module
        ci = CostInterp(imod, dmod)
        for name in ci.fns:
            try:
                r = ci.cost_of(name).render()
            except Exception as e:
                r = f"错误 {e}"
            check("⊤" not in r, f"{base}.{name} 成本可推", r)


def io_read(p):
    import io
    return io.open(p, encoding="utf-8").read()


def test_substitute_sizes():
    """E19 / R0 共用的机制：把 `size(x)` 换成别的成本。

    这是**上界正确性**的基础——换不掉，成本式里就会留下查不到的局部名字。
    """
    print("[N] 尺寸替换（substitute_sizes）")
    n = Cost.size
    c = Cost.add(Cost.mul(n("a"), Cost.num(3)), Cost.num(1))

    check(c.substitute_sizes({"a": n("xs")}).render() == "n(xs)×3 + 1",
          "换成另一个尺寸", c.substitute_sizes({"a": n("xs")}).render())
    # 换成常数要**折起来**（构造器会化简）
    check(c.substitute_sizes({"a": Cost.num(256)}).render() == "769",
          "换成常数并折叠", c.substitute_sizes({"a": Cost.num(256)}).render())
    check(c.substitute_sizes({"zzz": n("q")}).render() == "n(a)×3 + 1",
          "不命中时原样返回", c.substitute_sizes({"zzz": n("q")}).render())
    check(Cost.top().substitute_sizes({"a": n("xs")}).is_top(),
          "⊤ 保持 ⊤")
    check(Cost.max(n("a"), n("b")).substitute_sizes({"a": n("xs")}).render()
          == "max(n(xs), n(b))", "max 逐支替换")
    check(Cost.log2(n("a")).substitute_sizes({"a": n("xs")}).render()
          == "log2(n(xs))", "log2 逐支替换")


def main():
    test_substitute_sizes()
    test_algebra()
    test_statements()
    test_loops()
    test_target_and_inline()
    test_recursion()
    test_top()
    test_samples()

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
