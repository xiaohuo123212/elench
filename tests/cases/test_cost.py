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
from interp import Interp, ops_enable, ops_reset
from parser import parse_source
from symbolic import Cost

SAMPLE = os.path.join(HERE, "..", "文档", "样例")

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

    # 尺寸量一致、且 target **逐项支配** κ 的多尺寸仍判得出（2026-11-09）
    both = Cost.mul(Cost.size("xs"), Cost.size("ys"))
    v2 = compare(Cost.add(both, Cost.num(1)), both)
    check(v2.kind == "ok" and v2.multi,
          "多尺寸量逐项支配 → 达成", str(v2.kind))


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

    CH = """u64! f(vector<vector<u64>>& m) {
    u64 s = 0;
    u64 i = 0;
    while (i < m.length()) {
        const vector<u64> row = m.at(i);
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
    CH2 = """u64! g(vector<u64>& xs) {
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
    CH3 = """u64! h(vector<vector<u64>>& m) {
    vector<u64> row = m.at(0);
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

    CH = """u64! inner(const vector<u64>& xs) {
    u64 s = 0;
    u64 i = 0;
    while (i < xs.length()) {
        s = s + xs.at(i);
        i = i + 1;
    }
    return s;
}
u64! in_assign(const vector<u64>& xs) {
    u64 t = inner(xs);
    return t;
}
u64! middle(const vector<u64>& xs) {
    u64 t = 0;
    u64 j = 0;
    while (j < xs.length()) {
        t = t + inner(xs);
        j = j + 1;
    }
    return t;
}
u64! nested(const vector<u64>& xs) {
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

    tri = """u64! tri(const vector<u64>& xs) {
        u64 s = 0; const u64 n = xs.length(); u64 i = 0;
        while (i < n) { u64 j = 0; while (j < i) { s = s + xs.at(j); j = j + 1; } i = i + 1; }
        return s;
    }"""
    k = _CI(_ps(tri, "a.eln").module, None).cost_of("tri").render()
    check("n(i)" not in k, "嵌套依赖界：不留局部名 n(i)", k)
    check(k.count("n(xs)") >= 2, "嵌套依赖界：出现两次 n(xs)（O(n²)）", k)

    # 单层循环不受影响
    one = """u64! one(const vector<u64>& xs) {
        u64 s = 0; u64 i = 0;
        while (i < xs.length()) { s = s + 1; i = i + 1; }
        return s;
    }"""
    k1 = _CI(_ps(one, "a.eln").module, None).cost_of("one").render()
    check(k1.count("n(xs)") == 1, "单层循环：n(xs) 只出现一次", k1)

    # 复合条件（and/or）不组合事实，但也不能崩
    comp = """u64! comp(const vector<u64>& xs) {
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
    u64! f(const vector<u64>& xs) {
        const array<u64, CAP> t = xs;
        const u64 m = t.length();
        u64 s = 0; u64 i = 0;
        while (i < m) { s = s + 1; i = i + 1; }
        return s;
    }"""
    B = """const u64 CAP = 8;
    u64! f(const vector<u64>& xs) {
        const array<u64, CAP> t = xs;
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


def test_value_semantics():
    """A-full：**值语义的拷贝成本**（设计《值语义-A-full-设计》§3–§6）。

    只钉"哪条路径记多少"，不做数值快照：
      · prvalue（`{…}` / `T::make()` / 调用返回新对象）→ **省略**，0
      · `return` 非 const 局部 / 按值形参 → **隐式移动**（容器 0、array N）
      · 其余消费（按值传参 / 初始化 / 赋值 / 返回借用值）→ **拷贝**
      · `map/set` 拷贝 = **O(n) + n 次分配**（D6：结构克隆；改逐插重建必须改记 n log n）
    """
    print("[14] 值语义拷贝成本（A-full）")
    from parser import parse_source as _ps
    from cost_interp import CostInterp as _CI

    def ci_of(src, tu=""):
        return _CI(_ps(src, "vs.eln").module,
                   _ps(tu, "vs.tu").module if tu else None)

    # ---- 1) 按值 owning 形参：入口拷一次（`n(xs) + 1`，1 次分配）
    ci = ci_of("""u64 f(vector<u64> xs) {
        return xs.length();
    }""")
    r = ci.cost_of("f").render()
    check("n(xs)" in r, "按值 vector 形参：κ 含 n(xs)（入口拷贝）", r)
    check("按值 vector 形参：alloc = 1", ci.alloc_of("f").is_const()
          and ci.alloc_of("f").value == 1, ci.alloc_of("f").render())

    # 引用形参：零拷贝
    ci = ci_of("""u64 f(const vector<u64>& xs) {
        return xs.length();
    }""")
    r = ci.cost_of("f").render()
    check("const& 形参：κ 不含 n(xs)，alloc = 0",
          "n(xs)" not in r and ci.alloc_of("f").is_const()
          and ci.alloc_of("f").value == 0, f"{r} / {ci.alloc_of('f').render()}")

    # ---- 2) 按值形参的尺寸在调用点实例化（`n(形参)` → 实参尺寸）
    ci = ci_of("""u64 g(vector<u64> xs) { return xs.length(); }
    u64 f(const vector<u64>& v) { return g(v); }""")
    rf = ci.cost_of("f").render()
    check("调用点把 n(形参) 换成实参尺寸（不留 n(xs)）",
          "n(xs)" not in rf and "n(v)" in rf, rf)

    # ---- 3) 返回借用值（const&）→ 拷贝；返回 const 局部 → 拷贝
    ci = ci_of("""vector<u64> f(const vector<u64>& xs) {
        return xs;
    }""")
    r = ci.cost_of("f").render()
    check("return const& 形参：记拷贝（n(xs) + 1）", "n(xs) + 1" in r, r)
    check("return 借用值：alloc = 1", ci.alloc_of("f").is_const()
          and ci.alloc_of("f").value == 1, ci.alloc_of("f").render())

    ci = ci_of("""vector<u64> f() {
        const vector<u64> t = vector<u64>::make();
        return t;
    }""")
    r, a = ci.cost_of("f").render(), ci.alloc_of("f")
    check("return const 局部：记拷贝（D3，NRVO 不保证）",
          "n(t) + 1" in r and a.is_const() and a.value == 1, f"{r} / {a.render()}")

    # ---- 4) 返回非 const 局部 → **移动**（0 分配，不起 n(t)）
    ci = ci_of("""vector<u64> f() {
        vector<u64> t = vector<u64>::make();
        return t;
    }""")
    r, a = ci.cost_of("f").render(), ci.alloc_of("f")
    check("return 非 const 局部：按**移动**记（无 n(t)、0 分配）",
          "n(t)" not in r and a.is_const() and a.value == 0, f"{r} / {a.render()}")

    # ---- 5) 声明初始化的拷贝 / prvalue 省略
    ci = ci_of("""u64 f(const vector<u64>& xs) {
        vector<u64> a = xs;
        return a.length();
    }""")
    r = ci.cost_of("f").render()
    check("`T a = b`（b 是左值）：记拷贝 n(xs) + 1", "n(xs) + 1" in r, r)

    ci = ci_of("""u64 f() {
        vector<u64> a = vector<u64>::make();
        return a.length();
    }""")
    r = ci.cost_of("f").render()
    check("`T a = <prvalue>`：强制省略（无 n(a)）", "n(a)" not in r, r)

    # ---- 6) `array<T, N>` 的移动 = N（0 分配）
    ci = ci_of("""const u64 CAP = 8;
    array<u64, CAP> f() {
        array<u64, CAP> t = array<u64, CAP>::make();
        return t;
    }""")
    r, a = ci.cost_of("f").render(), ci.alloc_of("f")
    check("array 返回：按移动记 N = 8（0 分配）",
          "8" in r and a.is_const() and a.value == 0, f"{r} / {a.render()}")

    # ---- 7) struct 按值形参：k 个成员（Interval ×2 → 每次 2）
    from cost_check import evaluate as _ev
    TU = "struct P { a: u64, b: u64, }"
    ci = ci_of("""u64 f(P p) { return p.a; }""", TU)
    byval = ci.cost_of("f")
    ci = ci_of("""u64 f(const P& p) { return p.a; }""", TU)
    byref = ci.cost_of("f")
    check("struct 按值形参：比引用多 2（两个成员各 1）",
          _ev(byval, {}) - _ev(byref, {}) == 2,
          f"{byval.render()} vs {byref.render()}")

    # ---- 8) D6：`map` 拷贝 = O(n) **+ n 次节点分配**（不是 1 次）
    ci = ci_of("""u64 f(map<u64, u64> m) {
        map<u64, u64> x = m;
        return x.length();
    }""")
    r, a = ci.cost_of("f").render(), ci.alloc_of("f")
    check("map 拷贝：time 记 O(n)（含 n(m)）", "n(m)" in r, r)
    check("map 拷贝：alloc 记 n(m)（每节点 1 次，结构克隆）", "n(m)" in a.render(), a.render())

    # ---- 9) `?!` 候选用自己的签名（坑①）：候选返回 u64，不按宿主的 vector 记拷贝
    ci = ci_of("""vector<u64> host(const vector<u64>& xs) {
        const u64 t = (?!K(xs))?;
        return vector<u64>::make();
    } kernels K(const vector<u64>& xs) -> u64 {
        c { return xs.at(0); }
    }""")
    cands = ci.candidate_costs("K", {})
    check("?! 候选按 kernels 头的返回类型判定（不凭空记容器拷贝）",
          bool(cands) and all("n(xs) + 1" not in c.render() for c in cands.values()),
          str({k: v.render() for k, v in cands.items()}))

    # ---- 10) 第 7 个拷贝位置：`for` 的元素是 owning 容器 → 尺寸写不出 = ⊤
    ci = ci_of("""u64 f(const vector<vector<u64>>& vv) {
        for (auto x : vv) { u64 q = x.length(); }
        return 0;
    }""")
    r = ci.cost_of("f").render()
    check("for 元素是容器：内层尺寸写不出 → ⊤（不猜，§5.2）", "⊤" in r, r)


#: `else if` 微例：链**必须**进 κ（`_k_stmt_if` 曾经只收 `kind == "block"`）。
ELSEIF_ELN = """
u64 pick(const vector<u64>& xs, u64 k) {
    if (k == 0) {
        return 1;
    } else if (k == 1) {
        return 2;
    } else if (k == 2) {
        return 3;
    }
    return xs.length();
}
"""

ELSEIF_TU = """
spec pick(const vector<u64>& xs, u64 k) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""


def test_else_if_chain():
    """`else if` 是 `if` 的**子节点**（`parser.parse_if`），不是 `block`/`else`。

    修前 `_k_stmt_if` 只收 `kind == "block"` 的分支，于是整条 `else if` 链
    （它自己的 `_stmt` +1、条件、以及它下面所有分支）**从来没进过 κ**：

    | | κ(pick) | k=0 | k=1 | k=2 | k=3 |
    |---|---|---|---|---|---|
    | 修前 | **11** | ops 6 | ops 10 | ops 14 | **ops 16（破界 5）** |
    | 修后 | **19** | 6 | 10 | 14 | 16 |

    这正是生产语料上的那条：`_diff/bracket_match.match_brackets` 的
    κ(16)=576 < ops=609（差额 33），n=64 差 179，n=256 差 303。
    """
    from interp import Interp, ops_enable, ops_of, ops_reset

    ch_ = parse_source(ELSEIF_ELN, "elseif.eln").module
    tu_ = parse_source(ELSEIF_TU, "elseif.tu").module
    kv = evaluate(CostInterp(ch_, tu_).cost_of("pick"), {"xs": 16, "k": 0})
    check(kv == 19, "else-if 链：κ = 19（修前 11 —— 整条链被吞掉）", str(kv))
    check(kv >= 11, "else-if 链：κ 只增不减（≥ 修前 11）", str(kv))
    ops_enable(True)
    try:
        got = []
        for kk in (0, 1, 2, 3):
            ops_reset()
            Interp(ch_, tu_).call("pick", [[1, 2, 3, 4], kk])
            o = ops_of()
            got.append(o)
            check(o <= kv, f"else-if 链：k={kk} 的 ops {o} ≤ κ {kv:g}")
        check(got == [6, 10, 14, 16], "else-if 链：ops（基准）6/10/14/16", str(got))
    finally:
        ops_enable(False)


#: **`for (i in 0..n)` 已删除**（2026-11-10 夜，与 C++ range-for 重合；`..` 一并删）。
#:
#: 这一段原来是 `_for_count`（2026-11-09 修复）的微例：别名要追到尺寸量、
#: 字面量要当常数、推不出要 ⊤。**输入不变、判据改成"被删写法必须报
#: `E-For-InRemoved`"**——删除功能的唯一补偿就是报错教替代写法，所以这些
#: 微例正好用来守那条诊断。
#:
#: 替代写法（三段式）的**上界**性质另用 `FOR_THREE_ELN` 守（κ ≥ 真实轮数）。
FOR_RANGE_ELN = """
u64 alias(const vector<u64>& xs) {
    const u64 n = xs.length();
    u64 a = 0;
    for (i in 0..n) { a = a + 1; }
    return a;
}
u64 direct(const vector<u64>& xs) {
    u64 a = 0;
    for (i in 0..xs.length()) { a = a + 1; }
    return a;
}
u64! literal(vector<u64>& xs) {
    for (k in 0..1000) { xs[0] = xs[0] + 1; }
    return xs[0];
}
u64 shrunk(const vector<u64>& xs) {
    u64 a = 0;
    for (i in 0..xs.length() - 1) { a = a + 1; }
    return a;
}
u64 halved(const vector<u64>& xs) {
    u64 a = 0;
    for (i in 0..xs.length() / 2) { a = a + 1; }
    return a;
}
u64 rewritten(const vector<u64>& xs) {
    u64 n = xs.length();
    n = n * 2;
    u64 a = 0;
    for (i in 0..n) { a = a + 1; }
    return a;
}
u64 not_a_size(const vector<u64>& xs) {
    const u64 n = xs.at(0);
    u64 a = 0;
    for (i in 0..n) { a = a + 1; }
    return a;
}
u64 grow() {
    const u64 n = 8;
    vector<u64> out;
    for (k in 0..n) { out.push(k); }
    u64 s = 0;
    for (auto x : out) { s = s + x; }
    return s;
}
"""

#: 三段式替代写法的**上界**微例（`..` 删除后的主线）：
#:
#: · `alias`：`i < n`，`n = xs.length()` 的局部别名 → κ 里必须是 `n(xs)`；
#: · `direct`：`i < xs.length()`；
#: · `literal`：`i < 1000` → 常数上界 ≥ 1000；
#: · `shrunk` / `halved`：上界带算术（`- 1` / `/ 2`）→ `n(xs)` 或 ⊤，
#:   **两个都是上界**（⊤ = 不猜，绝不低估）；
#: · `grow`：8 轮 push → alloc = 8。
FOR_THREE_ELN = """
u64 alias(const vector<u64>& xs) {
    const u64 n = xs.length();
    u64 a = 0;
    for (u64 i = 0; i < n; i = i + 1) { a = a + 1; }
    return a;
}
u64 direct(const vector<u64>& xs) {
    u64 a = 0;
    for (u64 i = 0; i < xs.length(); i = i + 1) { a = a + 1; }
    return a;
}
u64! literal(vector<u64>& xs) {
    for (u64 k = 0; k < 1000; k = k + 1) { xs[0] = xs[0] + 1; }
    return xs[0];
}
u64 shrunk(const vector<u64>& xs) {
    u64 a = 0;
    for (u64 i = 0; i < xs.length() - 1; i = i + 1) { a = a + 1; }
    return a;
}
u64 halved(const vector<u64>& xs) {
    u64 a = 0;
    for (u64 i = 0; i < xs.length() / 2; i = i + 1) { a = a + 1; }
    return a;
}
u64 grow() {
    const u64 n = 8;
    vector<u64> out;
    for (u64 k = 0; k < n; k = k + 1) { out.push(k); }
    u64 s = 0;
    for (auto x : out) { s = s + x; }
    return s;
}
"""


def test_for_count():
    """`for (… in …)` 删除后的两条守则：**报错教替代**、替代写法的 κ 仍是上界。

    硬约束（全项目的立身之本）：**κ 只能是上界**。所以三条断言的方向都是
    「不许比真实轮数小」——宁可 ⊤（判不了），不许猜一个数。
    """
    print("[12] for 的轮数：被删写法报 E-For-InRemoved；三段式 κ 仍是上界")

    # ① 旧的 8 个微例（输入不变）：每一个都必须报 E-For-InRemoved。
    r_old = parse_source(FOR_RANGE_ELN, "forcount.eln")
    codes = [d.code for d in r_old.diagnostics]
    check(codes.count("E-For-InRemoved") == 8,
          "8 处 `for (… in …)` 全部报 E-For-InRemoved",
          f"{codes.count('E-For-InRemoved')} / {len(codes)}")
    check(not any(c.startswith("E-Parse-Expected") for c in codes),
          "被删写法不级联普通解析错误（头与体先吃掉）", codes)

    # ② 替代写法（三段式）的上界性质。
    ci = CostInterp(parse_source(FOR_THREE_ELN, "forcount3.eln").module, None)

    r_alias = ci.cost_of("alias").render()
    r_direct = ci.cost_of("direct").render()
    check("n(n)" not in r_alias, "别名上界不留 n(n)（查不到的局部名）", r_alias)
    check("n(xs)" in r_alias, "别名上界追到 n(xs)", r_alias)
    check(r_alias.count("n(xs)") == r_direct.count("n(xs)"),
          "别名写法与直写法的阶相同（n(xs) 次数一致）",
          f"{r_alias}  vs  {r_direct}")

    # ③ 字面量上界：轮数是常数，且 MUST ≥ 1000（1000 次真迭代）
    k_lit = ci.cost_of("literal")
    check(k_lit.variables() == [], "字面量上界 → κ 里没有尺寸量",
          str(k_lit.variables()))
    v_lit = evaluate(k_lit, {})
    check(v_lit >= 1000, "1000 轮的 κ ≥ 1000（不许按 1/body 首变量算）", f"{v_lit:g}")

    # ④ 上界带算术：`- 1` 给出 n(xs)；`/ 2` 推不出就 ⊤——**两者都是上界**。
    r_shrunk = ci.cost_of("shrunk").render()
    check("n(xs)" in r_shrunk, "shrunk（`length() - 1`）：κ 保留 n(xs)", r_shrunk)
    k_half = ci.cost_of("halved")
    check(k_half.is_top() or "n(xs)" in k_half.render(),
          "halved（`length() / 2`）：κ 是 n(xs) 或 ⊤（不猜，绝不低估）",
          k_half.render())

    # ⑤ 分配维度共用同一份推导：8 次 push = 8 次分配
    a_grow = ci.alloc_of("grow")
    check(a_grow.is_const() and a_grow.value == 8,
          "三段式 8 轮：alloc = 8（每轮 push 一次）", a_grow.render())
    r_grow = ci.cost_of("grow").render()
    check("n(out)" not in r_grow, "局部容器 out 的尺寸被解出来（不留 n(out)）", r_grow)
    check(evaluate(ci.cost_of("grow"), {}) >= 16,
          "两段循环的 κ 至少 16 步（8 + 8 轮，不为 0）", r_grow)


#: **引用迭代 vs 按值迭代**（2026-11-10 夜）：同一段遍历，只有绑定类别不同。
#:
#: · `vector<vector<u64>>`：元素是 owning 容器 → 按值版每轮拷一个内层，
#:   内层长度写不出 ⇒ κ = ⊤；引用版每轮 O(1) ⇒ κ 有限（**引用 < 按值**）。
#: · `vector<array<u64,4>>`：元素是定长数组 → 两版 κ 都有限，
#:   按值版每轮多 4（元素拷贝项），差值 = 4×n。
FOR_REF_ELN = """
u64 ref_vv(u64 n) {
    vector<vector<u64>> xs;
    for (u64 i = 0; i < n; i = i + 1) {
        vector<u64> row;
        row.push(1);
        row.push(2);
        row.push(3);
        row.push(4);
        xs.push(row);
    }
    u64 s = 0;
    for (const auto& row : xs) {
        s = s + row.at(0);
    }
    return s;
}
u64 val_vv(u64 n) {
    vector<vector<u64>> xs;
    for (u64 i = 0; i < n; i = i + 1) {
        vector<u64> row;
        row.push(1);
        row.push(2);
        row.push(3);
        row.push(4);
        xs.push(row);
    }
    u64 s = 0;
    for (auto row : xs) {
        s = s + row.at(0);
    }
    return s;
}
u64 ref_av(u64 n) {
    vector<array<u64, 4>> xs;
    for (u64 i = 0; i < n; i = i + 1) {
        array<u64, 4> row = {1, 2, 3, 4};
        xs.push(row);
    }
    u64 s = 0;
    for (const auto& row : xs) {
        s = s + row.at(0);
    }
    return s;
}
u64 val_av(u64 n) {
    vector<array<u64, 4>> xs;
    for (u64 i = 0; i < n; i = i + 1) {
        array<u64, 4> row = {1, 2, 3, 4};
        xs.push(row);
    }
    u64 s = 0;
    for (auto row : xs) {
        s = s + row.at(0);
    }
    return s;
}
"""


def test_for_ref_cost():
    """引用迭代的 κ MUST 小于按值迭代；两版的 κ 都必须 ≥ 同一输入的实测 ops。"""
    print("[13] 引用迭代 vs 按值迭代：κ 更小，且两版都 ≥ ops")
    mod = parse_source(FOR_REF_ELN, "forref.eln").module
    ci = CostInterp(mod, None)

    # ① `vector<vector<u64>>`：引用有限、按值 ⊤（内层长度写不出，不猜）
    k_ref_vv = ci.cost_of("ref_vv")
    k_val_vv = ci.cost_of("val_vv")
    check(not k_ref_vv.is_top(), "`vector<vector<u64>>` 的 `const auto&` κ 有限",
          k_ref_vv.render())
    check(k_val_vv.is_top(), "`vector<vector<u64>>` 的按值迭代 κ = ⊤（元素拷贝推不出）",
          k_val_vv.render())

    # ② `vector<array<u64,4>>`：两版都有限，按值多 4×n
    k_ref_av = ci.cost_of("ref_av")
    k_val_av = ci.cost_of("val_av")
    v_ref = evaluate(k_ref_av, {"n": 200})
    v_val = evaluate(k_val_av, {"n": 200})
    check(v_ref < v_val, "`vector<array<u64,4>>`：引用 κ < 按值 κ",
          f"{v_ref:g} vs {v_val:g}")
    check(v_val - v_ref >= 4 * 200,
          "按值版每轮多一份元素拷贝（差值 ≥ 4×200）", f"{v_val - v_ref:g}")

    # ③ 实测 ops（同一输入 n=200）：两版都必须 κ ≥ ops
    it = Interp(mod, None)
    ops_enable()
    try:
        got = {}
        for fn in ("ref_vv", "val_vv", "ref_av", "val_av"):
            ops_reset()
            it.call(fn, [200])
            got[fn] = it.ops
    finally:
        ops_enable(False)
    env = {"n": 200, "xs": 200, "row": 4}
    for fn in ("ref_vv", "val_vv", "ref_av", "val_av"):
        kv = evaluate(ci.cost_of(fn), env)
        check(kv >= got[fn], f"{fn}：κ ≥ ops（{kv:g} ≥ {got[fn]}）",
              f"κ={kv:g} ops={got[fn]}")
    # 引用版的实测 ops 必须**显著小于**按值版（同一输入）
    check(got["ref_vv"] < got["val_vv"], "`vector<vector<u64>>`：引用 ops < 按值 ops",
          f"{got['ref_vv']} vs {got['val_vv']}")
    check(got["ref_av"] < got["val_av"], "`vector<array<u64,4>>`：引用 ops < 按值 ops",
          f"{got['ref_av']} vs {got['val_av']}")
    # 修前的静默错值：`auto&` 被当成按值拷贝 → 容器不变。ops 只差元素拷贝项，
    # 但**语义**差一整条（容器改没改）。运行期断言在 test_interp ⑥-b。
    check(got["val_vv"] - got["ref_vv"] >= 5 * 200,
          "按值版每轮多拷一个内层（n+1 ≥ 5 ops × 200）",
          f"差 {got['val_vv'] - got['ref_vv']}")


#: `const u64 n = <字面量>` 当循环上界：轮数就是那个字面量。
#:
#: `as_cost` 对整数字面量返回 `Cost.num(0)`（"字面量不产生成本"）——对**成本**
#: 是对的；但这个返回值同时被当成"这个名字的**值**"写进 `env`，再被
#: `bound_of_condition_wrapper` 的 E22 表替换进上界，于是 `while (i < n)` 的
#: 轮数变成 **0**。实测：κ = 22，同一段代码 ops = 45（上界被违反）。
LITERAL_BOUND_ELN = """
u64 f(const vector<u64>& xs) {
    const u64 n = 3;
    u64 i = 0;
    u64 a = 0;
    while (i < n) {
        a = a + 1;
        i = i + 1;
    }
    return a;
}
"""


def test_literal_alias_bound():
    """字面量常量当循环上界：轮数是它本身，**不许**替换成 0。"""
    from interp import Interp, ops_enable, ops_of, ops_reset

    print("[15] 字面量常量当循环上界：κ ≥ ops（修前轮数被替换成 0）")

    ch_ = parse_source(LITERAL_BOUND_ELN, "litbound.eln").module
    k = CostInterp(ch_, None).cost_of("f")
    kv = evaluate(k, {"xs": 3})
    check(kv >= 3, "字面量上界：κ 至少含 3 轮", f"{kv:g}（{k.render()}）")
    ops_enable(True)
    try:
        ops_reset()
        Interp(ch_, None).call("f", [[1, 2, 3]])
        o = ops_of()
    finally:
        ops_enable(False)
    check(o <= kv, f"字面量上界：ops {o} ≤ κ {kv:g}（修前 22 < 45）",
          k.render())


#: `const u64 x = <尺寸表达式>;` 当循环上界：**κ 必须是结构，不许折成常数**。
#:
#: 修前（2026-11-09）：`as_cost` 只认字面量 / 名字 / `.length()`，`+` / `*` 落到
#: 兜底 `Cost.num(0)` ⇒ `env[total] = 0` ⇒ `bound_of_condition_wrapper` 的
#: 常数替换表把 `while (steps < total)` 的**轮数换成 0** ⇒ κ = 23 那种小常数，
#: 还判「✓ 达成」。而**不写 const 时反而是 ⊤**（`bound.expr_to_bound` 拒收 `+`）——
#: 「先绑 const」比「直接写」更不保守，方向反了，这是最危险的一类。
SIZE_ALIAS_ELN = """
u64 direct(const vector<u8>& text, const vector<u8>& pattern) {
    u64 steps = 0;
    while (steps < text.length() + pattern.length()) {
        steps = steps + 1;
    }
    return steps;
}

// ① 两个尺寸量之和
u64 summed(const vector<u8>& text, const vector<u8>& pattern) {
    const u64 total = text.length() + pattern.length();
    u64 steps = 0;
    while (steps < total) {
        steps = steps + 1;
    }
    return steps;
}

// ② 常数倍
u64 doubled(const vector<u8>& pattern) {
    const u64 twice = pattern.length() * 2;
    u64 steps = 0;
    while (steps < twice) {
        steps = steps + 1;
    }
    return steps;
}

// ③ 除以常数
u64 halved(const vector<u8>& xs) {
    const u64 half = xs.length() / 2;
    u64 i = 0;
    while (i < half) {
        i = i + 1;
    }
    return i;
}

// ④ 自乘
u64 squared(const vector<u8>& xs) {
    const u64 sq = xs.length() * xs.length();
    u64 i = 0;
    while (i < sq) {
        i = i + 1;
    }
    return i;
}

// ⑤ 标量形参自乘
u64 n2(u64 n) {
    const u64 n2v = n * n;
    u64 i = 0;
    while (i < n2v) {
        i = i + 1;
    }
    return i;
}

// ⑥ 认不出的初值：**必须是 ⊤**（不是 0）
u64 opaque(const vector<u8>& xs) {
    const u64 k = xs.at(0);
    u64 i = 0;
    while (i < k) {
        i = i + 1;
    }
    return i;
}

// ⑦ 回归：字面量别名仍是字面量（test_literal_alias_bound 守的那条）
u64 literal() {
    const u64 n = 1000;
    u64 i = 0;
    while (i < n) {
        i = i + 1;
    }
    return i;
}
"""


def test_size_alias_bound():
    """`const` 别名是尺寸表达式：κ 保留结构；认不出 → ⊤。**绝不许常数化 / 0 化**。"""
    from interp import Interp, ops_enable, ops_of, ops_reset

    print("[17] const 别名是尺寸表达式：κ 保留结构（修前折成常数 23）")
    ch_ = parse_source(SIZE_ALIAS_ELN, "sizealias.eln").module
    ci = CostInterp(ch_, None)

    # ① 和：两个尺寸量都要出现，而且**不是常数**
    k_sum = ci.cost_of("summed")
    r_sum = k_sum.render()
    check(not k_sum.is_const(), "两个尺寸量之和：κ 不是常数", r_sum)
    check("n(text)" in r_sum and "n(pattern)" in r_sum,
          "两个尺寸量之和：κ 同时含 n(text) 与 n(pattern)", r_sum)
    kv_sum = evaluate(k_sum, {"text": 5, "pattern": 3})
    check(kv_sum >= 8, "两个尺寸量之和：κ(text=5, pattern=3) ≥ 8 轮",
          f"{kv_sum:g}（{r_sum}）")

    # ② 常数倍：κ 里有 n(pattern)，且按 2 倍算（≥ 2×3 轮）
    k_two = ci.cost_of("doubled")
    r_two = k_two.render()
    check(not k_two.is_const(), "常数倍：κ 不是常数", r_two)
    check("n(pattern)" in r_two, "常数倍：κ 含 n(pattern)", r_two)
    kv_two = evaluate(k_two, {"pattern": 3})
    check(kv_two >= 6, "常数倍：κ(pattern=3) ≥ 6 轮", f"{kv_two:g}（{r_two}）")

    # ③ 除以 2：上界可以更松（n(xs)），但**不许是常数**
    k_half = ci.cost_of("halved")
    check(not k_half.is_const(), "除以常数：κ 不是常数", k_half.render())
    check("n(xs)" in k_half.render(), "除以常数：κ 仍以 n(xs) 为上界", k_half.render())

    # ④ 自乘：κ 里是平方，不是常数
    k_sq = ci.cost_of("squared")
    r_sq = k_sq.render()
    check(not k_sq.is_const(), "自乘：κ 不是常数", r_sq)
    check(r_sq.count("n(xs)") >= 2, "自乘：κ 里出现两次 n(xs)（平方）", r_sq)

    # ⑤ 标量形参自乘：`n × n`，不是常数
    k_n2 = ci.cost_of("n2")
    check(not k_n2.is_const(), "形参自乘：κ 不是常数", k_n2.render())
    check(evaluate(k_n2, {"n": 3}) >= 9, "形参自乘：κ(n=3) ≥ 9 轮", k_n2.render())

    # ⑥ 初值认不出（`xs.at(0)`）→ ⊤（修前被折成 0，判「达成」）
    check(ci.cost_of("opaque").is_top(),
          "认不出的初值 → ⊤（不许折成 0 / 常数）",
          ci.cost_of("opaque").render())

    # ⑦ 字面量别名：仍是它本身（1000 轮）
    check(evaluate(ci.cost_of("literal"), {}) >= 1000,
          "字面量别名仍是 1000 轮", f"{evaluate(ci.cost_of('literal'), {}):g}")

    # ⑧ **对账**：解释器实跑的 ops ≤ κ —— 这是"上界"二字的唯一硬判据
    ops_enable(True)
    try:
        r = Interp(ch_, None)
        for fn, args, dims in (("summed", [[1, 2, 3, 4, 5], [1, 2, 3]],
                                           {"text": 5, "pattern": 3}),
                               ("doubled", [[1, 2, 3]], {"pattern": 3})):
            ops_reset()
            r.call(fn, args)
            o = ops_of()
            kv = evaluate(ci.cost_of(fn), dims)
            check(o <= kv, f"{fn}：ops {o} ≤ κ {kv:g}（上界成立）",
                  ci.cost_of(fn).render())
    finally:
        ops_enable(False)


def test_bitwise_cost():
    """位运算是 O(1)：`cost_interp` 对每个二元运算符记 1 op，与 `+` 一致。"""
    print("[16] 位运算成本：每个二元运算 1 op（与 + 一致）")
    src = """
u64 plus(u64 a) { return a + a; }
u64 band(u64 a) { return a & a; }
u64 bor(u64 a) { return a | a; }
u64 bxor(u64 a) { return a ^ a; }
u64 shl(u64 a) { return a << a; }
u64 shr(u64 a) { return a >> a; }
u64 bnot(u64 a) { return ~a; }
u64 neg(u64 a) { return -a; }
"""
    ci = CostInterp(parse_source(src, "bw.eln").module, None)
    base = evaluate(ci.cost_of("plus"), {})
    for fn in ("band", "bor", "bxor", "shl", "shr"):
        got = evaluate(ci.cost_of(fn), {})
        check(got == base, f"{fn} 的 κ 与 plus 相同（{base:g}）", f"{got:g}")
    un = evaluate(ci.cost_of("neg"), {})
    notc = evaluate(ci.cost_of("bnot"), {})
    check(notc == un, f"`~a` 的 κ 与 `-a` 相同（{un:g}）", f"{notc:g}")


def test_explain_no_fake_numbers():
    """`--explain`：**⊤ / 非有限值不许产生数值化归因，更不许给删除建议**。

    实测（2026-11-09，`_tier_a/regex`）：`κ = ⊤` 的行打出 `杠杆 nan%`
    （`inf / inf`），还建议「删掉这一项即可达标」——因为 ablation 把 `⊤`
    换成常数 1 之后当然判达标。元凶行号还被文本回找指到 `^` 分支上。
    **用户会照着删代码**，所以这里把闸门钉死。
    """
    print("[18] --explain：⊤ / 非有限值不给杠杆、占比、阈值或删除建议")
    import cost_explain as ce

    class _N:
        def __init__(self, kind="", name="", children=None, line=0):
            self.kind, self.name = kind, name
            self.children, self.line = children or [], line

    class _Ci:
        origins = {}
        top_reasons = {"f": "`f` 是树递归：测试原因"}
        dispatch_cands = {}
        fns = {"caller": _N(children=[_N("call", "callee", line=7)]),
               "callee": _N()}

        def cost_of(self, name):
            # **不是 top 节点、但求值是 inf**：走 `_finite_value` 那条闸门。
            return Cost.mul(Cost.num(5), Cost.top()) if name == "callee" else None

    ci = _Ci()
    n = Cost.size("xs")
    top = Cost.top()
    check(ce.lever_shares(top, "xs") == [], "⊤ 的杠杆占比为空（不是 nan）")
    txt = "\n".join(ce.explain_function(ci, "f", "unknown",
                                        Cost.mul(n, Cost.num(2)), top, "xs"))
    check("nan" not in txt.lower(), "⊤ 的归因段没有 nan", txt)
    check("—" in txt, "⊤ 的杠杆显示为 —（无法归因）", txt)
    check("删掉" not in txt, "⊤ 的归因段没有「删掉这一项」建议", txt)
    check("树递归" in txt, "⊤ 的已知原因被转发进归因段", txt)
    # **含 ⊤ 但不是纯 top 节点**的 κ（`n × ⊤` 求值为 inf）同样不许给数
    mixed = Cost.mul(n, top)
    txt2 = "\n".join(ce.explain_function(ci, "g", "unknown",
                                         Cost.mul(n, Cost.num(2)), mixed, "xs"))
    check("nan" not in txt2.lower() and "删掉" not in txt2,
          "n×⊤ 的 κ 也不给 nan / 删除建议", txt2)
    # target 是 ⊤：以前 `a / inf = 0`，印成「target 松 100 倍以上」
    info = ce.threshold_of(n, top, "xs")
    check(not info["ok"] and "⊤" in info["reason"],
          "target=⊤ 时阈值算不出来（不是比值 0 / nan×）", str(info))
    th = "\n".join(ce.threshold_lines(ci, "h", top, n, "xs"))
    check("nan" not in th.lower() and "算不出来" in th,
          "target=⊤ 的阈值行不给数字", th)
    # 被调方求不出有限值：印 ⊤，不印 `κ=inf`
    cl = "\n".join(ce.callee_lines(ci, "caller", "xs"))
    check("inf" not in cl.lower() and "⊤" in cl, "被调方非有限值印 ⊤ 不印 inf", cl)
    # **不许误伤**：有限 κ 仍给杠杆百分比与最小修复建议
    fin = Cost.add(Cost.mul(n, Cost.num(10)), Cost.num(2))
    txt3 = "\n".join(ce.explain_function(ci, "k", "ok", Cost.mul(n, Cost.num(100)),
                                         fin, "xs"))
    check("%" in txt3 and "最小修复" in txt3 and "删掉" in txt3,
          "有限 κ 仍有杠杆百分比与最小修复建议", txt3)


def test_target_loose_warning():
    """`W-Target-Loose`：**「界松」不是「实现慢」**，而且它一个判定都不许改。

    实测（2026-11-10）：`calculator.evaluate` 的 κ 是 `n×1447 + 27790173`——
    27,790,173 那个常数项在 n=1024 处占 **94.9%**，`κ÷ops` 因此是 39 万倍；
    但 `compare` 只比**阶**，常数项永远推不成债务，于是这条 target 的"随 n 增长"
    **其实是被常数满足的**：界对"规模"什么都没说。这就是要报的 (a) 类。
    **不报 (b) 类**（κ 的阶真的高于真实阶）——那要实测，静态证不了。

    四条硬要求都钉在这里：

      1. **抓得到**：`calculator.evaluate` 报，且数字对得上（占 94.9%、比 target 大 2.8 万倍）；
      2. **不误报**：`merge`（`n log n×10` 占 99%）/ `count`（`n×21`）/ `sum` 等**一个都不报**；
         整个 `文档/样例` 的命中集合恰好是 `{calculator.evaluate}`；
      3. **不改判定**：报与不报两条路径之后 `compare` 的 `kind` / `render()` 逐字相同；
      4. **不会让 `check` 变红**：`dsh` 那条诊断的 severity 是 `warning`，没有 `error`。
    """
    print("[19] W-Target-Loose：界松告警（warning，不改判定，不误报）")
    import cost_explain as ce

    def _load(b):
        tu = parse_source(open(os.path.join(SAMPLE, b + ".tu"), encoding="utf-8").read(),
                          b + ".tu").module
        ch = parse_source(open(os.path.join(SAMPLE, b + ".eln"), encoding="utf-8").read(),
                          b + ".eln").module
        return CostInterp(ch, tu)

    # ---- ① 抓得到：calculator.evaluate ----
    ci = _load("calculator")
    c = ci.cost_of("evaluate")
    t = ci.target_cost("evaluate")
    before = compare(c, t)
    rep = ce.target_loose_report(ci, "evaluate", t, c, (c.variables() or ["n"])[0])
    check(rep is not None, "calculator.evaluate 报 W-Target-Loose", str(rep))
    if rep is not None:
        check(rep["code"] == "W-Target-Loose", "码是 W-Target-Loose", rep["code"])
        check(rep["share"] >= 0.9, f"常数项占 κ 的 {rep['share']:.1%}（≥90%）")
        check(rep["ratio"] is not None and rep["ratio"] > 10000,
              f"κ÷target = {rep['ratio']:,.0f}（比 target 大 1 万倍以上）"
              if rep["ratio"] else "κ÷target 是空")
        # 文案必须说清"不是实现慢"，并给出改哪儿
        blob = " ".join([rep["headline"]] + rep["lines"])
        check("不是实现慢" in blob, "文案说清这是「界松」不是「实现慢」", blob[:80])
        check("改 κ" in blob, "文案给出去哪儿改", blob[:80])
        check("另一回事" in blob, "文案区分 (a) 常数项松 / (b) 阶错了", blob[:80])
        lines = ce.target_loose_lines(ci, "evaluate", t, c, (c.variables() or ["n"])[0])
        check(lines and lines[0].startswith("W-Target-Loose"),
              "`cost` 用的逐行版首行带码", lines[0] if lines else "空")

    # ---- ② 不误报：正常紧的一个都不许报 ----
    for b, fn in (("merge", "merge"), ("count", "count"), ("sum", "average"),
                  ("stats", "summarize"), ("max_pair_sum", "max_pair_sum"),
                  ("insertion", "insertion"), ("blur", "blur")):
        cx = _load(b)
        cc = cx.cost_of(fn)
        tt = cx.target_cost(fn)
        sv = (cc.variables() or ["n"])[0]
        check(ce.target_loose_report(cx, fn, tt, cc, sv) is None,
              f"{b}.{fn} 不报（界是紧的）")
    # **债务不报**：debt 的 target 是**更紧**的那一侧，方向正好相反
    cx = _load("search")
    check(ce.target_loose_report(cx, "binary_search", cx.target_cost("binary_search"),
                                 cx.cost_of("binary_search"), "xs") is None,
          "债务（target 比 κ 更紧）不报")

    # ---- ③ 整个 文档/样例 的命中集合恰好是 {calculator.evaluate} ----
    hits = set()
    for name in sorted(os.listdir(SAMPLE)):
        if not name.endswith(".tu"):
            continue
        b = name[:-3]
        if not os.path.exists(os.path.join(SAMPLE, b + ".eln")):
            continue
        try:
            cm = _load(b)
        except Exception:                              # noqa: BLE001
            continue
        for fn in sorted(cm.fns):
            try:
                cc = cm.cost_of(fn)
                tt = cm.target_cost(fn)
            except Exception:                          # noqa: BLE001
                continue
            if tt is None:
                continue
            sv = (cc.variables() or ["n"])[0]
            if ce.target_loose_report(cm, fn, tt, cc, sv) is not None:
                hits.add(b + "." + fn)
    check(hits == {"calculator.evaluate"},
          "文档/样例 里只有 calculator.evaluate 命中（零误报）", str(sorted(hits)))

    # ---- ④ 判定不变：报完之后 compare 还是同一句话 ----
    after = compare(c, t)
    check(before.kind == after.kind and before.render() == after.render(),
          "加了告警后 compare 的结论逐字相同", f"{before.render()} -> {after.render()}")

    # ---- ⑤ check 不会因此变红：severity 是 warning ----
    from dsh import _target_loose_diags
    diags = _target_loose_diags(os.path.join(SAMPLE, "calculator.tu"), None)
    check(len(diags) == 1, f"check 侧拿到 1 条诊断（{len(diags)}）")
    check(all(d.severity == "warning" for d in diags),
          "诊断 severity 是 warning（CheckResult.ok 只数 error ⇒ check 不变红）",
          str([d.severity for d in diags]))
    check(not any(d.severity == "error" for d in diags), "没有一条是 error")
    # 正常模块的 check 侧一条都不该多
    check(_target_loose_diags(os.path.join(SAMPLE, "merge.tu"), None) == [],
          "merge 的 check 侧零诊断")


def test_unknown_container_size():
    """D2 / D4 回归：`n(?)` 不是尺寸量；能推的容器尺寸要**推导**。

    两个缺陷的共同症状是 κ 里冒出 `n(?)`：
      · **D4**：任何 `format(...)` 的"尺寸"都被当成**格式串（第一个实参）**的
        长度——格式串通常是字面量 ⇒ 记 `n(?)`，连 `format("{}", x)` 吃标量都中；
      · **D2**：容器实参是局部容器 / 嵌套调用时，尺寸没有被展开成"它由什么
        决定" ⇒ κ 里留 `n(局部名)`、`n(被调形参名)`，与 target 的尺寸量对不上（E23）。

    这里钉三件事：
      1. `format` 的输出长度按**实参**推导（标量 → 常数，容器 → `n(容器)`）；
      2. 局部容器 / 嵌套调用的尺寸用**表达式**代替（`enc = encode(data)` → `n(data)×k`）；
      3. 推不出时退 **⊤ + 原因**，绝不用 `n(?)` 冒充尺寸量。
    """
    print("[22] 未知容器尺寸（D2 / D4）")
    CH = """
vector<u8> encode(const vector<u8>& data) {
    vector<u8> out;
    for (auto b : data) {
        out.push(b);
        out.push(b);
        out.push(b);
        out.push(b);
    }
    return out;
}
vector<u8> decode(const vector<u8>& data) {
    vector<u8> out;
    for (auto b : data) {
        out.push(b);
    }
    return out;
}
vector<u8> fresh(const vector<u8>& data) {
    vector<u8> out;
    for (auto b : data) {
        out.push(b);
    }
    return out;
}
u64 grow(vector<u8>& v) {
    v.push(1);
    return v.length();
}
pub u64 fmt_scalar(u64 x) {
    vector<u8> s = format("{}", x);
    return x;
}
pub u64 fmt_container(const vector<u8>& xs) {
    vector<u8> s = format("{:s}", xs);
    return xs.length();
}
pub u64 fmt_call(const vector<u8>& data) {
    vector<u8> s = format("{:s}", fresh(data));
    return data.length();
}
pub u64 roundtrip(const vector<u8>& data) {
    vector<u8> enc = encode(data);
    vector<u8> dec = decode(enc);
    return dec.length();
}
pub u64 nested_calls(const vector<u8>& xs) {
    return decode(encode(xs)).length();
}
pub u64 fmt_unknown(const vector<u8>& xs) {
    vector<u8> out;
    grow(out);
    vector<u8> s = format("{:s}", out);
    return xs.length();
}
pub u64 grow_self(vector<u8>& v) {
    u64 i = 0;
    while (i < v.length()) { v.push(1); i = i + 1; }
    return v.length();
}
pub u64 fmt_top(const vector<u8>& xs) {
    vector<u8> out;
    u64 k = grow_self(out);
    vector<u8> s = format("{:s}", out);
    return xs.length() + k;
}
"""
    TU = """
spec encode(const vector<u8>& data) -> vector<u8> {
    effects { alloc };
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}
spec decode(const vector<u8>& data) -> vector<u8> {
    effects { alloc };
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}
spec fresh(const vector<u8>& data) -> vector<u8> {
    effects { alloc };
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}
spec grow(vector<u8>& v) -> u64 {
    effects { alloc };
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}
spec fmt_scalar(u64 x) -> u64 {
    effects { alloc };
    target { time: { max: O(1) }, allocation: { max: O(1) }, };
}
spec fmt_container(const vector<u8>& xs) -> u64 {
    effects { alloc };
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}
spec fmt_call(const vector<u8>& data) -> u64 {
    effects { alloc };
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}
spec roundtrip(const vector<u8>& data) -> u64 {
    effects { alloc };
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}
spec nested_calls(const vector<u8>& xs) -> u64 {
    effects { alloc };
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}
spec fmt_unknown(const vector<u8>& xs) -> u64 {
    effects { alloc };
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}
"""
    ci = CostInterp(parse_source(CH, "d2d4.eln").module,
                    parse_source(TU, "d2d4.tu").module)

    def verdict(fn):
        return compare(ci.cost_of(fn), ci.target_cost(fn))

    # ① D4：`format` 吃标量 —— 输出长度是常数 ⇒ O(1) 判达成，且没有 `n(?)`
    k1 = ci.cost_of("fmt_scalar")
    check("?" not in set(k1.variables()),
          "fmt_scalar 的 κ 里没有 `n(?)`", k1.render())
    check(verdict("fmt_scalar").kind == "ok",
          "fmt_scalar 的 O(1) 判达成（不再被 n(?) 顶成超标）",
          verdict("fmt_scalar").render())

    # ② D4：`format` 吃容器形参 —— 输出长度 = n(xs)，尺寸量就是 xs
    k2 = ci.cost_of("fmt_container")
    check(set(k2.variables()) == {"xs"},
          "fmt_container 的 κ 只含 n(xs)（不是 n(?)）", k2.render())
    check(verdict("fmt_container").kind == "ok",
          "fmt_container 的 O(n) 判达成", verdict("fmt_container").render())

    # ③ D4+D2：`format` 吃**调用结果** —— 用被调函数的返回尺寸（n(data)）
    k3 = ci.cost_of("fmt_call")
    check(set(k3.variables()) == {"data"},
          "fmt_call 的 κ 只含 n(data)（调用结果的尺寸已展开）", k3.render())
    check(verdict("fmt_call").kind == "ok",
          "fmt_call 的 O(n) 判达成", verdict("fmt_call").render())

    # ④ D2：局部容器当实参 —— 不再留 n(enc) / n(dec)（E23 的直接来源）
    k4 = ci.cost_of("roundtrip")
    check(set(k4.variables()) == {"data"},
          "roundtrip 的 κ 只含 n(data)（enc/dec 的尺寸已展开）", k4.render())
    v4 = verdict("roundtrip")
    check(v4.kind == "ok",
          "roundtrip 的 O(data.length()) 判达成（不再 E23）", v4.render())

    # ⑤ D2：嵌套调用 `f(g(x))` —— 形参名不泄漏（g 的形参叫 data，调用方叫 xs）
    k5 = ci.cost_of("nested_calls")
    check(set(k5.variables()) == {"xs"},
          "nested_calls 的 κ 只含 n(xs)（没有 n(被调形参名)）", k5.render())
    check(verdict("nested_calls").kind == "ok",
          "nested_calls 的 O(n) 判达成", verdict("nested_calls").render())

    # ⑥ 推不出 → ⊤ + 原因（**不是** `n(?)`）
    #
    # **2026-11-12 更新**：原来这里用 `fmt_unknown` 当"推不出"的例子——
    # 它靠 `grow(out)` 做出参增长。出参尺寸传播落地后，**这个例子推得出了**
    # （`|out| ≤ 1` ⇒ κ = 32），所以断言改成"它有界"，并把「推不出」那三条
    # 断言挪到下面 `fmt_top`：**增长依赖出参自身尺寸**（`grow_self`），
    # 这才是真正的 ⊤ 边界。两类都留着，谁坏了都能测出来。
    k6 = ci.cost_of("fmt_unknown")
    check(not k6.is_top(),
          "出参增长推得出 ⇒ fmt_unknown 有界（不再是 ⊤）", k6.render())

    k6b = ci.cost_of("fmt_top")
    check(k6b.is_top(), "增长依赖出参自身尺寸 → κ 退 ⊤", k6b.render())
    why = ci.top_reasons.get("fmt_top") or ""
    check("长度推不出" in why,
          "⊤ 带原因行（说明是哪一项的尺寸推不出）", why)
    check("?" not in k6b.render(), "⊤ 的渲染里没有 `n(?)`", k6b.render())

    # ⑦ 兜底：就算外来的 Cost 里带了 `n(?)`，它也不当尺寸量参与判定
    v7 = compare(Cost.add(Cost.size("?"), Cost.num(1)), n())
    check(v7.kind == "unknown" and "?" in (v7.note or ""),
          "`n(?)` 出现 ⇒ 判定是 unknown（不配对、不算比值）", v7.render())


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
    test_value_semantics()
    test_else_if_chain()
    test_for_count()
    test_for_ref_cost()
    test_literal_alias_bound()
    test_size_alias_bound()
    test_bitwise_cost()
    test_explain_no_fake_numbers()
    test_target_loose_warning()
    test_unknown_container_size()

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
