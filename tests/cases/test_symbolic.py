#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_symbolic.py —— G27：符号域与成本解释器

运行：
  cd src; $env:PYTHONIOENCODING="utf-8"; python test_symbolic.py
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
from fractions import Fraction

sys.stdout.reconfigure(encoding="utf-8")

from cost_check import evaluate
from cost_interp import CostInterp, report
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
    check(cost_of("u64 f() { return 1; }") == "2",
          "return 1 → 2（return 1 + 字面量 1）",
          cost_of("u64 f() { return 1; }"))

    check(cost_of("u64 f() { u64 a = 1; u64 b = 2; return a; }") == "6",
          "三条语句 → 6（每条语句 1 + 每个初值/读取 1）",
          cost_of("u64 f() { u64 a = 1; u64 b = 2; return a; }"))

    print("[4] 分支取 max")
    src = ("u64 f(const vector<u64>& xs) { const u64 n = xs.length(); "
           "if (n == 0) { return 0; } return 1; }")
    r = cost_of(src)
    # 条件 1 + max(两支) + 后面那条 return：两支成本相同时 max 会化简掉，
    # 所以这里断言"是个具体的数"，而不是"含 max"
    check(r.isdigit(), "if 的两条路径已合并成一个数", r)

    # 一支常数、一支线性 → max 能判定，化简掉（这是**正确的**）
    src2 = ("u64 f(const vector<u64>& xs) { const u64 n = xs.length(); "
            "u64 i = 0; if (n == 0) { return 0; } "
            "while (i < n) { i = i + 1; } return i; }")
    r2 = cost_of(src2)
    check("max(" not in r2 and "n(xs)" in r2,
          "常数 vs 线性 → max 化简成线性", r2)

    # 两支都是循环，但遍历不同的容器 → 比不了，必须保留 max
    src3 = ("u64 f(const vector<u64>& a, const vector<u64>& b) { const u64 x = a.length(); "
            "u64 t = 0; "
            "if (x == 0) { u64 i = 0; while (i < a.length()) { t = t + 1; i = i + 1; } } "
            "else { u64 j = 0; while (j < b.length()) { t = t + 1; j = j + 1; } } "
            "return t; }")
    r3 = cost_of(src3)
    check("max(" in r3, "遍历不同容器 → 保留 max（比不了就不猜）", r3)


def test_loops():
    print("[5] 循环：圈数 × 每圈")
    src = ("u64 f(const vector<u64>& xs) { u64 i = 0; "
           "while (i < xs.length()) { i = i + 1; } return i; }")
    r = cost_of(src)
    check("n(xs)" in r and "×" in r, "while 上界进入成本式", r)

    src2 = ("u64 f(const vector<u64>& xs) { u64 s = 0; "
            "for (auto x : xs) { s = s + x; } return s; }")
    r2 = cost_of(src2)
    check("n(xs)" in r2 and "×" in r2, "for 用容器长度做圈数", r2)


def test_target_and_inline():
    print("[6] 跨函数：有 target 用 target，没有就内联")
    decl = """spec g(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, };
}
spec h(const vector<u64>& xs) -> u64 { effects {}; }"""
    impl = """u64 f(const vector<u64>& xs) { return g(xs); }
u64 g(const vector<u64>& xs) { u64 i = 0; while (i < xs.length()) { i = i + 1; } return i; }
u64 h(const vector<u64>& xs) { return 1; }"""
    mod = parse_source(impl, "x.eln").module
    dmod = parse_source(decl, "x.tu").module
    ci = CostInterp(mod, dmod)
    f_cost = ci.cost_of("f").render()
    check("n(xs)" in f_cost, "f 调 g：用了 g 的 target（n(xs)）", f_cost)

    # h 没有 target → 内联，得到它的实际成本 2
    impl2 = "u64 f(const vector<u64>& xs) { return h(xs); }\nfunc h(const vector<u64>& xs) -> u64 { return 1; }"
    mod2 = parse_source(impl2, "x.eln").module
    ci2 = CostInterp(mod2, None)
    check(ci2.cost_of("f").render() == "5",
          "f 调 h（无 target）：内联 2 + 调用 1 + 实参 xs 1 + 语句 1 = 5",
          ci2.cost_of("f").render())


def test_recursion():
    print("[7] 内联成环要报错，不能转不停")
    src = ("u64 f(const vector<u64>& xs) { return g(xs); }\n"
           "u64 g(const vector<u64>& xs) { return f(xs); }")
    mod = parse_source(src, "x.eln").module
    ci = CostInterp(mod, None)
    try:
        ci.cost_of("f")
        check(False, "递归内联必须报错")
    except Exception as e:
        check("环" in str(e) or "目标" in str(e), "递归内联报错并给出提示", str(e)[:50])


def test_top():
    print("[8] 抽不出上界 → ⊤，不猜")
    src = ("u64 f(const vector<u64>& xs) { u64 i = 0; "
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


def test_par_algebra():
    """`Cost.par`：**并行块保留任务结构**（2026-11-09）。

    守两条 B-24 原文口径：
        value(par) = Σ_i value(κ_i)      （工作量 / allocation）
        order(par) = max_i order(κ_i)    （判阶）

    以及"不能因为引入 par 而低估"：⊤ 吸收、add/mul/max 的对接、替换递归。
    """
    print("[P] par（并行块）的代数")
    from cost_check import compare, evaluate

    n = Cost.size("xs")
    p2 = Cost.par([n, n])

    check(p2.render() == "par(n(xs), n(xs))",
          "render 印结构，不退化成一个和", p2.render())
    check(p2.kind == "par" and len(p2.args) == 2,
          "kind = par，args = 各任务", str(p2))

    # 判阶 = max：四个 n 的任务，阶是 n，不是 n⁴
    p4 = Cost.par([n, n, n, n])
    check(compare(p4, n).kind == "ok",
          "4 个 O(n) 任务对 O(n)：达成（阶 = max，不是 Σ 的 n⁴）")
    check(compare(p4, Cost.mul(n, n)).kind == "ok",
          "4 个 O(n) 任务对 O(n²)：也达成（更大的 target）")
    check(compare(Cost.par([Cost.mul(n, n), n]), n).kind == "debt",
          "一个任务 O(n²) → 整块 O(n²)，对 O(n) 是债务")
    check(compare(Cost.par([n, n]), Cost.num(1)).kind == "debt",
          "有尺寸量的 par 对 O(1) 是债务")

    # value = Σ（常数）：2 + 3 = 5
    check(Cost.par([Cost.num(2), Cost.num(3)]).const_value() == 5.0,
          "const_value(par) = Σ")
    check(evaluate(Cost.par([Cost.num(2), Cost.num(3)]), {}) == 5.0,
          "evaluate(par) = Σ")

    # 空 / 单任务 / ⊤
    check(Cost.par([]).render() == "0", "空并行块 = 0", Cost.par([]).render())
    check(Cost.par([n]).render() == "par(n(xs))",
          "单任务保留 par（视图对退化情形也说得出一句话）")
    check(compare(Cost.par([n]), n).kind == "ok", "单任务：判阶 = 那一个任务的阶")
    check(Cost.par([n, Cost.top()]).is_top(),
          "**任一分量是 ⊤ → 整块 ⊤**（否则 add 的 ⊤ 吸收会漏判）")

    # 与 add / mul / max 对接：不特判，但三条都对
    check(Cost.add(p2, Cost.num(1)).render() == "par(n(xs), n(xs)) + 1",
          "add(par, 1)：保留 par，外层加")
    check(compare(Cost.add(p2, n), n).kind == "ok", "par + n 对 O(n)：达成")
    check(Cost.mul(Cost.num(2), p2).render() == "2×par(n(xs), n(xs))",
          "mul(2, par)：外层乘")
    check(compare(Cost.mul(n, p2), Cost.mul(n, n)).kind == "ok",
          "n × par(n, n) 的阶 = n（次数相加，不是 n²）")
    check("max(" in Cost.max(p2, n).render(),
          "max(par, n) 比不了就保留（不硬猜）")

    # 替换要**递归进每个任务**，否则调用点换尺寸会漏名字 → 低估
    check(Cost.par([Cost.size("a"), Cost.num(2)])
          .substitute_sizes({"a": n}).render() == "par(n(xs), 2)",
          "substitute_sizes 逐分量递归")

    # 机读签名要说清两条口径
    d = Cost.par([n, Cost.num(1)]).to_dict()
    check(d.get("operator") == "parallel", "to_dict 的 operator = parallel", str(d))
    check(d.get("order") == "maximum" and d.get("value") == "sum",
          "to_dict 写明 order=max / value=sum", str(d))


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


def test_pow_algebra():
    """**有理次幂节点**（2026-11-09）：打印 / 求值 / 化简 / JSON。"""
    print("[5b] 有理次幂：打印 / 求值 / 化简")
    n = Cost.size("xs")

    check(Cost.sqrt(n).render() == "sqrt(n(xs))",
          "sqrt 打印成 sqrt(...)", Cost.sqrt(n).render())
    check(Cost.pow(n, Fraction(2, 3)).render() == "n(xs)^(2/3)",
          "n^(2/3) 打印带括号", Cost.pow(n, Fraction(2, 3)).render())
    check(Cost.pow(n, Fraction(3, 2)).render() == "n(xs)^(3/2)",
          "n^(3/2) 打印带括号", Cost.pow(n, Fraction(3, 2)).render())
    check(Cost.pow(n, 2).render() == "n(xs)^2", "整数指数打印成 n^2",
          Cost.pow(n, 2).render())
    check(Cost.pow(Cost.add(n, Cost.num(1)), Fraction(1, 2)).render()
          == "sqrt(n(xs) + 1)", "底数是加法时 sqrt 带括号",
          Cost.pow(Cost.add(n, Cost.num(1)), Fraction(1, 2)).render())

    # 求值
    check(abs(evaluate(Cost.sqrt(n), {"xs": 16}) - 4.0) < 1e-9,
          "sqrt 在 n=16 处是 4", str(evaluate(Cost.sqrt(n), {"xs": 16})))
    check(abs(evaluate(Cost.pow(n, Fraction(2, 3)), {"xs": 8}) - 4.0) < 1e-6,
          "n^(2/3) 在 n=8 处是 4",
          str(evaluate(Cost.pow(n, Fraction(2, 3)), {"xs": 8})))
    check(abs(evaluate(Cost.pow(n, Fraction(3, 2)), {"xs": 4}) - 8.0) < 1e-9,
          "n^(3/2) 在 n=4 处是 8",
          str(evaluate(Cost.pow(n, Fraction(3, 2)), {"xs": 4})))

    # 化简
    check(Cost.pow(n, 0).render() == "1", "n^0 = 1")
    check(Cost.pow(n, 1).render() == "n(xs)", "n^1 = n")
    check(Cost.pow(Cost.num(4), Fraction(1, 2)).render() == "2",
          "4^(1/2) = 2（整数底能折就折）")
    check(Cost.pow(Cost.pow(n, Fraction(1, 2)), Fraction(1, 2)).render()
          == "n(xs)^(1/4)", "(n^(1/2))^(1/2) = n^(1/4)")
    sq = Cost.sqrt(n)
    check(Cost.mul(sq, sq).render() == "n(xs)", "sqrt(n) × sqrt(n) = n")
    check(Cost.mul(sq, n).render() == "n(xs)^(3/2)", "sqrt(n) × n = n^(3/2)")
    check(Cost.mul(Cost.num(2), sq).render() == "2×sqrt(n(xs))",
          "常数 × sqrt 保持乘积（不混进底数）",
          Cost.mul(Cost.num(2), sq).render())

    # 与 ⊤ / 负指数 / 替换
    check(Cost.pow(Cost.top(), Fraction(1, 2)).is_top(), "⊤^(1/2) = ⊤")
    check(Cost.pow(n, Fraction(-1)).is_top(), "n^(-1) → ⊤（不是成本上界，不猜）")
    check(Cost.sqrt(n).substitute_sizes({"xs": Cost.size("ys")}).render()
          == "sqrt(n(ys))", "替换底数里的尺寸名")
    check(Cost.sqrt(n).to_dict()["exponent"] == "1/2",
          "JSON 签名带上指数", str(Cost.sqrt(n).to_dict()))


def test_div_algebra():
    """**除法节点**（2026-11-09）：打印 / 求值 / 化简 / 替换 / JSON。

    它只为一件真实需求存在：`π(x) ≈ x / log x`。`Cost` 以前没有除法，
    于是 `target { time: { max: O(n / log(n)) } }` **写不出来**（语义层认不出）。
    """
    print("[5c] 除法：打印 / 求值 / 化简 / 替换")
    n = Cost.size("xs")
    lg = Cost.log2(n)
    d = Cost.div(n, lg)

    check(d.kind == "div" and len(d.args) == 2, "kind = div，args = (分子, 分母)",
          str(d))
    check(d.render() == "n(xs) / log2(n(xs))",
          "render：n(xs) / log2(n(xs))", d.render())
    check(d.to_dict().get("operator") == "division",
          "to_dict 的 operator = division", str(d.to_dict()))
    check(len(d.to_dict().get("operands") or []) == 2, "to_dict 带两个操作数")

    # 求值
    check(abs(evaluate(d, {"xs": 1024}) - 102.4) < 1e-9,
          "n/log2(n) 在 n=1024 处是 102.4", str(evaluate(d, {"xs": 1024})))
    check(evaluate(Cost.div(Cost.num(0), n), {"xs": 8}) == 0.0, "0 / n = 0")
    check(evaluate(Cost.div(n, Cost.num(0)), {"xs": 8}) == float("inf"),
          "n / 0 求值 → inf（不崩）", str(evaluate(Cost.div(n, Cost.num(0)), {"xs": 8})))

    # 化简
    check(Cost.div(n, Cost.num(1)).render() == "n(xs)", "a / 1 = a")
    check(Cost.div(Cost.num(6), Cost.num(3)).render() == "2", "6 / 3 = 2")
    check(Cost.div(Cost.num(3), Cost.num(2)).const_value() == 1.5,
          "3 / 2 = 1.5（Fraction 不被 int() 截断）",
          str(Cost.div(Cost.num(3), Cost.num(2)).render()))
    check(Cost.div(n, n).render() == "1", "n / n = 1")
    check(Cost.div(Cost.mul(n, lg), lg).render() == "n(xs)", "(n×log n) / log n = n")
    check(Cost.mul(d, lg).render() == "n(xs)", "(n / log n) × log n = n")
    check(Cost.mul(lg, d).render() == "n(xs)", "log n × (n / log n) = n")
    check(Cost.div(n, Cost.num(2)).render() == "n(xs) / 2",
          "n / 2 保留系数（判阶按常数分母处理）", Cost.div(n, Cost.num(2)).render())

    # ⊤ 吸收：分母未知可能是个小量（log n），按 0 算会低估 → 整式 ⊤
    check(Cost.div(Cost.top(), n).is_top(), "⊤ / n = ⊤")
    check(Cost.div(n, Cost.top()).is_top(), "n / ⊤ = ⊤")

    # 替换：**分子分母都要换**（漏掉分母 = 除一个查不到的名字）
    check(d.substitute_sizes({"xs": Cost.size("ys")}).render()
          == "n(ys) / log2(n(ys))", "substitute_sizes 换掉分母里的尺寸名",
          d.substitute_sizes({"xs": Cost.size("ys")}).render())


def test_target_div_parses():
    """**端到端**：`O(n / log(n))` 从 `.tu` 走进 `Cost.div`，不再"写了却认不出"。

    修复前（真实现场）：`cost` 打印
    `无法判定：target 写了但解析失败`，契约**整个不生效**——
    与 `sqrt(n)` 修复前（2026-11-09 第 1 步）是同一种病：
    语法能过、语义层不认。
    """
    print("[5d] target 里的除法：解析 / 判阶 / 端到端")
    from cost_check import _profile, compare

    tu_src = """spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(n / log(n)) }, };
}"""
    impl_src = ("u64 f(const vector<u64>& xs) { u64 i = 0; "
                "while (i < xs.length()) { i = i + 1; } return i; }")
    ci = CostInterp(parse_source(impl_src, "x.eln").module,
                    parse_source(tu_src, "x.tu").module)

    t = ci.target_cost("f")
    check(t is not None,
          "O(n / log(n)) 能解析（不再是『target 写了但解析失败』）",
          str(getattr(ci, "target_errors", None)))
    check(not ci.target_failed("f"), "不再登记 target 解析失败",
          str(getattr(ci, "target_errors", None)))
    check(t is not None and t.render() == "n(xs) / log2(n(xs))",
          "解析出的成本式：n(xs) / log2(n(xs))", t.render() if t else "None")
    check(_profile(t, "xs") == (Fraction(1), -1, 0),
          "target 的阶 → (1, -1, 0)", str(_profile(t, "xs")))
    # 线性 κ 对 n/log n 必须是**债务**（n/log n 比 n 小一档）
    check(compare(Cost.size("xs"), t).kind == "debt",
          "κ = n vs O(n/log n) → debt（结构判据）",
          compare(Cost.size("xs"), t).render())
    check(abs(evaluate(t, {"xs": 1024}) - 102.4) < 1e-9,
          "target 可求值：1024 / log2(1024) = 102.4")

    # **分母是另一个尺寸量**：能解析（结构如实保留），但**判阶 ⊤**，不给数
    tu2 = """spec g(const vector<u64>& xs, const vector<u64>& ys) -> u64 {
    effects {};
    target { time: { max: O(xs.length() / ys.length()) }, };
}"""
    ci2 = CostInterp(parse_source("u64 g(const vector<u64>& xs, const vector<u64>& ys) { return 0; }",
                                  "x.eln").module,
                     parse_source(tu2, "x.tu").module)
    t2 = ci2.target_cost("g")
    check(t2 is not None, "O(xs.length() / ys.length()) 能解析（不报 BadForm）")
    check(t2 is not None and t2.kind == "div", "解析出 div 节点（结构如实保留）")
    check(_profile(t2, "xs") is None,
          "但判阶是 ⊤：n/m 的阶取决于两变量关系，不猜", str(_profile(t2, "xs")))
    check(compare(Cost.size("xs"), t2).kind == "unknown",
          "κ = n(xs) vs O(n(xs)/n(ys)) → unknown（不给一个数）",
          compare(Cost.size("xs"), t2).render())


def test_target_loglog_parses():
    """**端到端**：`O(n * log(log(n)))` 走到 `_profile` 的第三位（loglog）。

    修复前（真实现场）：`target { time: { max: O(n * log(log(n))) } }`
    **能 parse**（`_target_cost` 的 `log` 分支递归），但 `_profile` 把
    `log2(log2(n))` 一律折成 `log n`——于是 κ = `n log n` 对这个 target
    判**达成**。target 写的是 `n loglog n`，判阶却按 `n log n` 读，
    承诺被**悄悄放宽一档**。这条钉住"解析 → 归约 → 判定"整条链。
    """
    print("[5e] target 里的 loglog：解析 / 判阶 / 端到端")
    from cost_check import _profile, compare

    tu_src = """spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(n * log(log(n))) }, };
}"""
    ci = CostInterp(parse_source(
        "u64 f(const vector<u64>& xs) { return 0; }",
        "x.eln").module, parse_source(tu_src, "x.tu").module)
    t = ci.target_cost("f")
    check(t is not None, "O(n * log(log(n))) 能解析", str(ci.target_errors))
    check(not ci.target_failed("f"), "不登记 target 解析失败",
          str(getattr(ci, "target_errors", None)))
    check(t is not None and t.render() == "n(xs)×log2(log2(n(xs)))",
          "解析出的成本式：n(xs)×log2(log2(n(xs)))", t.render() if t else "None")
    check(_profile(t, "xs") == (Fraction(1), 0, 1),
          "target 的阶 → (1, 0, 1)（loglog 分量）", str(_profile(t, "xs")))
    check(abs(evaluate(t, {"xs": 65536}) - 65536 * 4) < 1e-9,
          "target 可求值：n=2^16 时 = n × 4",
          str(evaluate(t, {"xs": 65536})))
    # **关键回归**：n log n 不再被这个 target 放过（以前是假 ✓）
    k = Cost.mul(Cost.size("xs"), Cost.log2(Cost.size("xs")))
    v = compare(k, t)
    check(v.kind == "debt", "κ = n log n vs O(n log log n) → debt（不再假 ✓）",
          v.render())
    check("结构判据" in (v.note or ""), "由结构判据判（不是斜率）", v.note)
    check(compare(Cost.size("xs"), t).kind == "ok",
          "κ = n vs O(n log log n) → ok")


def main():
    test_par_algebra()
    test_substitute_sizes()
    test_algebra()
    test_pow_algebra()
    test_div_algebra()
    test_target_div_parses()
    test_target_loglog_parses()
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
