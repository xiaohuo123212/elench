#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_cost.py —— 静态闭环（成本 vs target）与分档规划

运行：
  cd src; $env:PYTHONIOENCODING="utf-8"; python test_cost.py
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

from cost_check import compare, evaluate
from cost_interp import CostInterp
from dispatch import crossing, order_of, plan_dispatch, table_from_measure
from parser import parse_source
from symbolic import Cost

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


def n():
    return Cost.size("xs")


# ---------------------------------------------------------------- 比较

def test_compare_orders():
    print("[1] 阶比较（big-O 口径）")
    check(compare(n(), n()).kind == "ok", "n vs O(n)")
    check(compare(Cost.add(Cost.mul(Cost.num(3), n()), Cost.num(9)), n()).kind == "ok",
          "3n+9 vs O(n)：常数不影响")
    check(compare(Cost.mul(n(), n()), n()).kind == "debt", "n² vs O(n) 超标")
    check(compare(n(), Cost.log2(n())).kind == "debt", "n vs O(log n) 超标")
    check(compare(Cost.log2(n()), n()).kind == "ok", "log n vs O(n) 达成")
    check(compare(Cost.mul(n(), n()), Cost.mul(n(), n())).kind == "ok", "n² vs O(n²)")
    check(compare(Cost.num(100), Cost.num(1)).kind == "ok",
          "常数 100 vs O(1)：big-O 一律达成（标准定低是作者的自由）")


def test_compare_edges():
    print("[2] 边界：⊤、多尺寸、缺 target")
    check(compare(Cost.top(), n()).kind == "unknown", "κ=⊤ → unknown")
    check(compare(n(), Cost.top()).kind == "ok", "target=⊤ → 一切满足")

    # **E23**：κ 与 target 的尺寸量不一致时，比比值等于比两件不同的事 ——
    # 旧断言（ok/debt）编码的正是那个错行为。现在 MUST 是 unknown，并说明原因。
    two = Cost.add(Cost.size("xs"), Cost.size("ys"))
    v = compare(two, n())
    check(v.kind == "unknown" and "不一致" in v.note,
          "尺寸量不一致 → 不判债务（E23）", str(v.kind))

    # 尺寸量一致的多尺寸仍然要比得出来
    both = Cost.mul(Cost.size("xs"), Cost.size("ys"))
    v2 = compare(Cost.add(both, Cost.num(1)), both)
    check(v2.kind == "ok" and v2.multi,
          "尺寸量一致时多尺寸仍能判定", str(v2.kind))


# ---------------------------------------------------------------- 求值

def test_evaluate():
    print("[3] 成本式求值")
    c = Cost.add(Cost.mul(Cost.num(2), n()), Cost.num(3))
    check(evaluate(c, {"xs": 10}) == 23, "2n+3 在 n=10 → 23",
          str(evaluate(c, {"xs": 10})))
    check(evaluate(Cost.log2(n()), {"xs": 1024}) == 10, "log2(1024) → 10",
          str(evaluate(Cost.log2(n()), {"xs": 1024})))
    check(evaluate(Cost.max(Cost.num(3), n()), {"xs": 5}) == 5, "max 求值")
    check(evaluate(Cost.top(), {"xs": 1}) == float("inf"), "⊤ → inf")


# ---------------------------------------------------------------- 阶

def test_order():
    print("[4] 增长阶判定")
    check(order_of(Cost.num(1)) == 0, "常数 → 0")
    check(order_of(Cost.log2(n())) == 1, "log2 → 1")
    check(order_of(n()) == 2, "线性 → 2")
    check(order_of(Cost.mul(n(), Cost.log2(n()))) == 3, "n log n → 3")
    check(order_of(Cost.mul(n(), n())) == 4, "n² → 4")


# ---------------------------------------------------------------- 交叉点

def test_crossing():
    print("[5] 交叉点（整数二分）")
    a = Cost.add(Cost.mul(Cost.num(8), n()), Cost.num(1))
    b = Cost.add(Cost.mul(Cost.num(3), n()), Cost.num(100000))
    x = crossing(a, b, "xs", 1, 1 << 30)
    check(x is not None and 15000 <= x <= 25000,
          "8n+1 与 3n+100000 交点 ≈ 20000", str(x))

    # 不翻转 → None
    check(crossing(n(), Cost.mul(Cost.num(2), n()), "xs", 1, 1 << 20) is None,
          "n 与 2n 不翻转 → None")

    # log vs 线性：在小规模上可能翻转
    lg = Cost.add(Cost.mul(Cost.num(7), Cost.log2(n())), Cost.num(8))
    lin = Cost.add(Cost.mul(Cost.num(4), n()), Cost.num(5))
    x2 = crossing(lg, lin, "xs", 1, 1 << 20)
    check(x2 is not None, "log 与线性有交点", str(x2))


# ---------------------------------------------------------------- 分档规划

def test_plan():
    print("[6] 三层筛：分档规划")
    lin = Cost.add(Cost.mul(Cost.num(4), n()), Cost.num(5))
    lg = Cost.add(Cost.mul(Cost.num(7), Cost.log2(n())), Cost.num(8))

    p = plan_dispatch("search_impl", {"lin": lin, "lg": lg}, "xs", 1, 1 << 20)
    check(not p.need_measure, "线性 vs 对数：范围内 log 全程赢，不用测", p.reason)
    check(any("lg" in w for _, _, w in p.segments), "赢家是 log 那个", str(p.segments))

    quad = Cost.mul(n(), n())
    p2 = plan_dispatch("x", {"lin": lin, "quad": quad}, "xs", 1, 1 << 20)
    check(p2.need_measure and len(p2.segments) >= 2,
          "线性 vs 平方：有交叉点 → 需要测，分 ≥2 段", str(p2.segments))

    # 差价太小 → 提示不值得分档
    a = Cost.add(Cost.mul(Cost.num(3), n()), Cost.num(5))
    b = Cost.add(Cost.mul(Cost.num(3), n()), Cost.num(6))
    p3 = plan_dispatch("y", {"a": a, "b": b}, "xs", 1, 1 << 20)
    check(not p3.need_measure, "两候选几乎一样 → 不分档", p3.reason)


def test_table_from_measure():
    print("[7] 从实测生成表")
    measured = {
        "A": {1: 10.0, 10: 100.0, 100: 1000.0},
        "B": {1: 50.0, 10: 30.0, 100: 200.0},
    }
    segs = table_from_measure("L", [1, 10, 100], measured)
    check(len(segs) >= 1, "生成至少一段", str(segs))
    check(segs[0][2] == "A", "n=1 时 A 赢（10 < 50）", str(segs[0]))
    check(segs[-1][2] == "B", "n=100 时 B 赢（200 < 1000）", str(segs[-1]))

    # 差价 < 查表成本 → 沿用上一个赢家
    close = {"A": {1: 10.0}, "B": {1: 11.0}}
    segs2 = table_from_measure("M", [1], close)
    check(segs2 and segs2[0][2] == "A", "差价 1ns < 3ns → 不翻转", str(segs2))


# ---------------------------------------------------------------- 真实样例

def test_samples():
    print("[8] 三套真实样例")
    tu = parse_source(open(os.path.join(SAMPLE, "search.tu"), encoding="utf-8").read(),
                      "search.tu").module
    ch = parse_source(open(os.path.join(SAMPLE, "search.eln"), encoding="utf-8").read(),
                      "search.eln").module
    ci = CostInterp(ch, tu)
    c = ci.cost_of("binary_search")
    t = ci.target_cost("binary_search")
    v = compare(c, t)
    # 这个样例**故意**演示"取最慢候选 → 债务"（search_linear 是 O(n)，target 是 O(log n)）
    check(v.kind == "debt",
          "binary_search 因含线性候选而超标（样例有意演示债务）", v.render())

    tu2 = parse_source(open(os.path.join(SAMPLE, "sum.tu"), encoding="utf-8").read(),
                       "sum.tu").module
    ch2 = parse_source(open(os.path.join(SAMPLE, "sum.eln"), encoding="utf-8").read(),
                       "sum.eln").module
    ci2 = CostInterp(ch2, tu2)
    v2 = compare(ci2.cost_of("average"), ci2.target_cost("average"))
    check(v2.kind == "ok", "average 在 target 之内（两个候选同阶）", v2.render())


# ---------------------------------------------------------------- 签名输出

def test_signature_yaml():
    print("[9] 机器可读签名（JSON / YAML）")
    from dsh import to_yaml
    from symbolic import Cost

    c = Cost.add(Cost.num(4), Cost.mul(Cost.size("xs"), Cost.num(3)))
    d = c.to_dict()
    check(d["operator"] == "addition" and len(d["operands"]) == 2,
          "Cost.to_dict 结构（完整词）", str(d))

    # 真实的 cost_rendered 里含逗号与括号 → 必须加引号，否则 YAML 会被读坏
    rendered = "4 + 1 + 1 + max(1 + n(xs)×4 + 2, 2 + log2(n(xs))×7 + 2) + 1"
    obj = {"module": "m", "functions": {"f": {
        "cost": d,
        "cost_expression": rendered,
        "target": None,
        "verdict": "ok",
        "verdict_explanation": "达成：阶够",
    }}}
    y = to_yaml(obj)
    check("module: m" in y, "YAML 顶层标量")
    check("f:" in y and "cost:" in y, "YAML 嵌套键")
    check("- operator: constant" in y, "列表里的 dict → `- operator: constant`", y)
    check("'" + rendered + "'" in y, "含逗号的字符串被单引号包住")

    # 不含特殊字符的字符串**不该**加引号（否则可读性变差）
    check("plain_value: 4" in to_yaml({"plain_value": "4"}),
          "纯数字串不加引号")
    check("target: null" in y, "None → null")
    check("verdict: ok" in y, "普通标量不加引号")

    # 缩进必须是偶数（YAML 不用 tab，也不接受奇数缩进）
    for ln in y.split("\n"):
        ind = len(ln) - len(ln.lstrip(" "))
        check(ind % 2 == 0, f"缩进是偶数：{ln!r}")


def test_nested_row_alias():
    """L2（甲）：局部行别名的尺寸要归一到外层容器，否则二维成本式失真。"""
    print("[10] 嵌套二维：行别名归一")

    CH = """func f(Vec<Vec<u64>>& m) -> u64! {
    u64 s = 0;
    u64 i = 0;
    while (i < m.length()) {
        const Vec<u64> row = m.at(i);
        u64 j = 0;
        while (j < row.length()) {
            s = s + row.at(j);
            j = j + 1;
        }
        i = i + 1;
    }
    return s;
}"""
    ch = parse_source(CH, "t.eln").module
    r = CostInterp(ch, None).cost_of("f").render()
    check("n(row)" not in r, "不出现查不到的 n(row)", r)
    check(r.count("n(m)") >= 2, "n(m) 出现两次（外层 × 内层）", r)

    # 非容器别名不绑（`const Token t = tokens.at(i);` 里 t 不是一行）
    CH2 = """func g(Vec<u64>& xs) -> u64! {
    const u64 v = xs.at(0);
    u64 i = 0;
    while (i < xs.length()) {
        i = i + 1;
    }
    return v;
}"""
    ci2 = CostInterp(parse_source(CH2, "t2.eln").module, None)
    ci2.cost_of("g")
    check(ci2.row_of == {}, "标量别名不进 row_of", str(ci2.row_of))

    # 别名被重新赋值 → 撤销（宁可不认，不可算错）
    CH3 = """func h(Vec<Vec<u64>>& m) -> u64! {
    Vec<u64> row = m.at(0);
    row = m.at(1);
    u64 j = 0;
    while (j < row.length()) {
        j = j + 1;
    }
    return j;
}"""
    r3 = CostInterp(parse_source(CH3, "t3.eln").module, None).cost_of("h").render()
    check("n(row)" in r3, "`var` 重新赋值后不绑（保守）", r3)


def test_call_composition():
    """E17 回归：调用写在赋值/二元/method 实参里，成本 MUST 传播。

    这是一个**上界正确性**的回归——它不测"数字对不对"，测"阶传不传"。
    漏算时 κ 会小于真实成本，**上界就不成立**，而那是整个项目的立身之本。
    """
    print("[11] 调用链成本传播（E17 回归）")

    CH = """func inner(const Vec<u64>& xs) -> u64! {
    u64 s = 0;
    u64 i = 0;
    while (i < xs.length()) {
        s = s + xs.at(i);
        i = i + 1;
    }
    return s;
}
func in_assign(const Vec<u64>& xs) -> u64! {
    u64 t = inner(xs);
    return t;
}
func middle(const Vec<u64>& xs) -> u64! {
    u64 t = 0;
    u64 j = 0;
    while (j < xs.length()) {
        t = t + inner(xs);
        j = j + 1;
    }
    return t;
}
func nested(const Vec<u64>& xs) -> u64! {
    u64 u = 0;
    u64 k = 0;
    while (k < xs.length()) {
        u = u + middle(xs);
        k = k + 1;
    }
    return u;
}"""
    ci = CostInterp(parse_source(CH, "t.eln").module, None)

    def deg(fn):
        """粗略定阶：n(xs) 出现几次（1=线性，2=平方，3=立方）。"""
        return ci.cost_of(fn).render().count("n(xs)")

    check(deg("inner") >= 1, "inner 至少线性", ci.cost_of("inner").render())
    # in_assign 的 κ 必须包含 inner 的成本（原来只有常数 2）
    check(deg("in_assign") >= deg("inner"),
          "声明初值里的调用被计入", ci.cost_of("in_assign").render())
    check(deg("middle") > deg("inner"),
          "一层的调用链 MUST 让阶上升（O(n) → O(n²)）",
          f"inner={deg('inner')} middle={deg('middle')}")
    check(deg("nested") > deg("middle"),
          "两层调用链 MUST 再升一阶（O(n²) → O(n³)）",
          f"middle={deg('middle')} nested={deg('nested')}")


def test_guard_fact():
    """R0 / E20：守卫事实传播。

    `while (i < n)` 的体内必有 `i <= n`，所以内层 `while (j < i)` 的界
    可以换成 `n(xs)`。不换，κ 里会留下查不到局部名 `n(i)`——
    **求值时按 1 算 → 低估 → 上界不成立**。
    """
    print("[12] 守卫事实传播（R0 / E20）")
    from parser import parse_source as _ps
    from cost_interp import CostInterp as _CI

    tri = """func tri(const Vec<u64>& xs) -> u64! {
        u64 s = 0; const u64 n = xs.length(); u64 i = 0;
        while (i < n) { u64 j = 0; while (j < i) { s = s + xs.at(j); j = j + 1; } i = i + 1; }
        return s;
    }"""
    k = _CI(_ps(tri, "a.eln").module, None).cost_of("tri").render()
    check("n(i)" not in k, "嵌套依赖界：不留局部名 n(i)", k)
    check(k.count("n(xs)") >= 2, "嵌套依赖界：出现两次 n(xs)（O(n²)）", k)

    # 单层循环不受影响
    one = """func one(const Vec<u64>& xs) -> u64! {
        u64 s = 0; u64 i = 0;
        while (i < xs.length()) { s = s + 1; i = i + 1; }
        return s;
    }"""
    k1 = _CI(_ps(one, "a.eln").module, None).cost_of("one").render()
    check(k1.count("n(xs)") == 1, "单层循环：n(xs) 只出现一次", k1)

    # 复合条件（and/or）不组合事实，但也不能崩
    comp = """func comp(const Vec<u64>& xs) -> u64! {
        u64 s = 0; u64 i = 0;
        while (i < xs.length() && i < 10) { s = s + 1; i = i + 1; }
        return s;
    }"""
    try:
        kc = _CI(_ps(comp, "a.eln").module, None).cost_of("comp").render()
        check("⊤" not in kc or True, "复合条件不崩", kc)
    except Exception as e:                       # noqa: BLE001
        check(False, "复合条件不崩", str(e))


def test_instance_isolation():
    """A–D：`CostInterp` 的常量表必须是**实例级**的。

    修前的 bug：`_CONST_VALUES` 是模块级全局，`__init__` 里会 `clear()`——
    **同进程建第二个实例会把第一个实例的常量表清空**，
    于是前一个实例之后再算，模块常量会被当成尺寸变量（**静默的错误上界**）。
    """
    print("[13] 实例隔离（A–D：常量表不能是模块级全局）")
    from parser import parse_source as _ps
    from cost_interp import CostInterp as _CI

    A = """const u64 CAP = 256;
    func f(const Vec<u64>& xs) -> u64! {
        const FixedVec<u64, CAP> t = xs;
        const u64 m = t.length();
        u64 s = 0; u64 i = 0;
        while (i < m) { s = s + 1; i = i + 1; }
        return s;
    }"""
    B = """const u64 CAP = 8;
    func f(const Vec<u64>& xs) -> u64! {
        const FixedVec<u64, CAP> t = xs;
        const u64 m = t.length();
        u64 s = 0; u64 i = 0;
        while (i < m) { s = s + 1; i = i + 1; }
        return s;
    }"""
    ca, cb = _CI(_ps(A, "a.eln").module, None), _CI(_ps(B, "b.eln").module, None)
    ka1 = ca.cost_of("f").render()
    kb1 = cb.cost_of("f").render()
    ka2 = ca.cost_of("f").render()
    kb2 = cb.cost_of("f").render()
    check(ka1 == ka2, "A 实例连算两次结果相同（不被 B 污染）", f"{ka1} vs {ka2}")
    check(kb1 == kb2, "B 实例连算两次结果相同", f"{kb1} vs {kb2}")
    check(ka1 != kb1, "两个实例的答案不同（各自用各自的 CAP）", f"{ka1} vs {kb1}")
    check("n(" not in ka1 and "n(" not in kb1, "两个都是常数（定容生效）", f"{ka1} / {kb1}")


def main():
    test_instance_isolation()
    test_guard_fact()
    test_compare_orders()
    test_compare_edges()
    test_evaluate()
    test_order()
    test_crossing()
    test_plan()
    test_table_from_measure()
    test_samples()
    test_signature_yaml()
    test_nested_row_alias()
    test_call_composition()

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
