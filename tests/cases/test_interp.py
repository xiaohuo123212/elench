#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_interp.py —— 具体值解释器 + bench

运行：
  cd src; $env:PYTHONIOENCODING="utf-8"; python test_interp.py
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

from bench import measure, read_benchmodel
from interp import Err, Interp, Ok
from parser import parse_source

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
    src = """u64 f(u64 a, u64 b) {
    return a + b;
}
u64 g(u64 a, u64 b) {
    return a * b;
}
u64 h(u64 a) {
    return 0 - a;
}"""
    r = parse_source(src, "w.eln")
    check(not r.diagnostics, "回绕用例能解析",
          "; ".join(d.code for d in r.diagnostics))
    it = Interp(r.module, None)
    M = 1 << 64
    check(it.call("f", [M - 1, 1]) == 0, "u64 最大值 + 1 → 回绕到 0",
          str(it.call("f", [M - 1, 1])))
    # 值层是**二进制补码**：同一个位模式按有符号读是 -2。
    # `u64` 的转回无符号发生在**打印**（`_print_typed`），不在值层。
    check(it.call("g", [M - 1, 2]) == -2, "u64 最大值 × 2 → 补码 -2",
          str(it.call("g", [M - 1, 2])))
    check(it.call("h", [3]) == -3, "0 - 3 → 补码 -3", str(it.call("h", [3])))


# ---------------------------------------------------------------- 死循环保护

def test_budget():
    print("[5] 死循环必须有上限（不许把工具挂住）")
    src = """u64 f(u64 n) {
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


# ---------------------------------------------------------------- 预算文案分类

def _budget_msg(eln, tu_src=None, budget=2_000, entry="f", args=None,
                advisor=None):
    """跑一次注定超预算的调用，返回 `InterpBudgetExceeded` 的文案。"""
    from interp import InterpBudgetExceeded
    ch = parse_source(eln, "b.eln").module
    tu = (parse_source(tu_src, "b.tu").module
          if tu_src is not None else None)
    it = Interp(ch, tu, budget=budget)
    if advisor is not None:
        it.budget_advisor = advisor
    try:
        it.call(entry, args or [])
    except InterpBudgetExceeded as exc:
        return str(exc)
    raise AssertionError("这次调用没有超预算——测试本身写错了")


def test_budget_messages():
    print("[5b] 预算耗尽的文案要区分：死循环 / 算法复杂度 / 输入太大")
    # ① 真死循环（条件不是数值上界）：仍说"疑似死循环"，
    #    但必须同时提醒"也可能是算法复杂度"——不许把人只往循环变量上引。
    _dead = _budget_msg(
        "u64 f(u64 n) {\n"
        "    u64 i = 0;\n"
        "    while (n != 0) { i = i + 1; }\n"
        "    return i;\n}\n", budget=20_000, args=[1])
    check("疑似死循环" in _dead, "真死循环仍报「疑似死循环」", _dead)
    check("也可能是算法本身的复杂度" in _dead,
          "兜底文案带上「也可能是算法复杂度」", _dead)
    check("ELENCH_STEP_BUDGET" in _dead,
          "兜底文案给出 ELENCH_STEP_BUDGET", _dead)

    # ② 输入太大：`i < n` 有具体数值上界，超限时计数器确实在推进。
    _big = _budget_msg(
        "u64 f(u64 n) {\n"
        "    u64 i = 0;\n"
        "    while (i < n) { i = i + 1; }\n"
        "    return i;\n}\n", budget=1_000, args=[10 ** 9])
    check("输入规模" in _big, "有限大循环报「输入规模」", _big)
    check("具体数值上界" in _big, "给出具体上界", _big)
    check("ELENCH_STEP_BUDGET" in _big,
          "输入太大给出 ELENCH_STEP_BUDGET 与当前值", _big)

    # ③ 树递归：**复用 cost 侧的 `_note_tree_rec`**，不是解释器另写一份判定。
    _tu = ("spec main() -> u64 { effects {}; "
           "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n"
           "spec bt(u64 k) -> u64 { effects {}; recursion depth <= 64; "
           "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n")
    _eln = ("u64 main() { return bt(10); }\n"
            "u64 bt(u64 k) {\n"
            "    if (k == 0) { return 1; }\n"
            "    u64 a = bt(k - 1);\n"
            "    u64 b = bt(k - 1);\n"
            "    return a + b;\n}\n")
    import dsh
    _ch = parse_source(_eln, "b.eln").module
    _tu_mod = parse_source(_tu, "b.tu").module
    _adv = dsh._tree_rec_budget_advisor(_ch, _tu_mod, None, "main")
    _tree = _budget_msg(_eln, _tu, budget=2_000, entry="main",
                        args=[], advisor=_adv)
    check("算法本身的复杂度" in _tree,
          "树递归超预算指向「算法复杂度」", _tree)
    check("树递归" in _tree, "引用 cost 侧的「树递归」原因", _tree)
    check("不是死循环" in _tree, "明确说「不是死循环」", _tree)


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


def xcheck(actual, expected, name, why=None, since=None, blocker=None):
    """**2026-11-05 起这些不再是"预期失败"**：`_wrap` 改成二进制补码后，
    `i64` 的减法/取负/全负数相加/calculator 求值全部正确。
    保留函数名与参数，是为了让调用点一行都不用改。"""
    check(actual, expected, name)

def test_negative_numbers_xfail():
    print("[7] 负数：预期失败（E8，等 E4）")

    # ① 减法结果是负数
    src1 = """i64 sub(i64 a, i64 b) {
    return a - b;
}"""
    it1 = Interp(parse_source(src1, "neg.eln").module, None)
    xcheck(it1.call("sub", [3, 10]), -7,
           "i64 减法 3-10",
           "解释器按 u64 掩码回绕，负数变成巨大正数",
           "2026-10-29 21:42", "E4")

    # ② 一元取负
    src2 = """i64 neg(i64 a) {
    return 0 - a;
}"""
    it2 = Interp(parse_source(src2, "neg2.eln").module, None)
    xcheck(it2.call("neg", [5]), -5,
           "i64 取负 0-5",
           "同上",
           "2026-10-29 21:42", "E4")

    # ③ 全负数组求和
    src3 = """i64 sum3(i64 a, i64 b, i64 c) {
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


# ---------------------------------------------------------------- 值语义拷贝

#: **值语义（2026-11-09，A-full 第 2 步）**：解释器的拷贝面与成本域 κ 的
#: 七个拷贝位置对齐（`cost_interp` 的 `_copy_cost` / 设计 §4.1）：
#:
#:   1. `T a = b;`（b 是**左值**）→ 拷贝；b 是 prvalue（`mk()` / `{}` / `T{…}`）→ 不拷贝
#:   2. `a = b;`（赋值，b 左值）→ 拷贝赋值
#:   3. 按值形参 `f(T v)`（实参是左值）→ 拷贝；实参是 prvalue → 不拷贝（移动）
#:   4. `return <借用值>`（`const&` 形参 / 元素 / 字段 / const 局部）→ **拷贝**
#:   5. `push` / `insert` / `vec_push` / `map_put` 入容器、`m[k] = v`（左值）→ 拷贝
#:   6. `for (auto x : xs)` 的元素绑定 → 每轮一次拷贝（标量 → 0）
#:   7. `S{a}` 聚合初始化的成员（成员是容器且值是左值）→ 拷贝
#:
#: **不该变的四条**：`return 局部变量`（移动）、`return T{…}`（省略）、
#: `T&` / `const T&` 形参（别名）、`-> T&` 返回（别名）。
#: 改之前这 24 个格子全是"改一边、另一边也变"（`_alias/results_before3.json`）。
_VALUE_SEM_SRC = """
struct Entry { xs: vector<u64>, }
u64 byval(vector<u64> p) { p.push(99); return p.length(); }
u64 byref(vector<u64>& p) { p.push(99); return p.length(); }
vector<u64> mk() { vector<u64> v = {1, 2, 3}; return v; }
u64 assign_copy() {
    vector<u64> src = {1, 2, 3};
    vector<u64> dst = src;
    src.push(99);
    return dst.length();
}
u64 assign_elem() {
    vector<u64> src = {1, 2, 3};
    vector<u64> dst = src;
    src[0] = 77;
    return dst[0];
}
u64 assign_stmt_copy() {
    vector<u64> src = {1, 2, 3};
    vector<u64> dst;
    dst = src;
    src.push(99);
    return dst.length();
}
u64 assign_bytes() {
    vector<u8> s = "abc";
    vector<u8> t = s;
    t.push(122);
    return s.length();
}
u64 assign_array() {
    array<u64, 4> a = {1, 2, 3, 4};
    array<u64, 4> b = a;
    b.pop();
    return a.length();
}
u64 assign_nested() {
    vector<u64> x = {1, 2};
    vector<vector<u64>> src;
    src.push(x);
    vector<vector<u64>> dst = src;
    dst[0].push(9);
    return src[0].length();
}
u64 store_copy() {
    map<u64, vector<u64>> box;
    vector<u64> src = {1, 2, 3};
    box.insert(1, src);
    src.push(99);
    return box.get(1).length();
}
u64 push_copy() {
    vector<vector<u64>> vv;
    vector<u64> x = {1, 2};
    vv.push(x);
    x.push(3);
    return vv[0].length();
}
u64 push_prvalue() {
    vector<vector<u64>> vv;
    vv.push(vector<u64>::filled(7, 3));
    return vv[0].length();
}
u64 ref_alias() {
    vector<u64> src = {1, 2, 3};
    byref(src);
    return src.length();
}
u64 byval_copy() {
    vector<u64> src = {1, 2, 3};
    byval(src);
    return src.length();
}
u64 byval_prvalue() {
    return byval(mk());
}
u64 ret_move() {
    vector<u64> r = mk();
    r.push(9);
    return r.length();
}
u64 set_copy() {
    set<u64> s = set_new();
    set_add(s, 1);
    set<u64> t = s;
    set_add(t, 2);
    return set_len(s);
}
vector<u64> pick_elem(map<u64, vector<u64>>& box) {
    return box.get(1);
}
u64 ret_elem_copy() {
    vector<u64> src = {1, 2, 3};
    map<u64, vector<u64>> box;
    box.insert(1, src);
    vector<u64> r = pick_elem(box);
    r.push(99);
    return box.get(1).length();
}
vector<u64> leak_ref(const vector<u64>& p) {
    return p;
}
u64 ret_ref_param_copy() {
    vector<u64> src = {1, 2, 3};
    vector<u64> r = leak_ref(src);
    r.push(99);
    return src.length();
}
u64 ret_index_copy() {
    vector<vector<u64>> vv;
    vector<u64> x = {1, 2, 3};
    vv.push(x);
    vector<u64> r = vv[0];
    r.push(99);
    return vv[0].length();
}
u64 for_elem_copy() {
    vector<vector<u64>> vv;
    vector<u64> x = {1, 2, 3};
    vv.push(x);
    for (auto e : vv) { e.push(99); }
    return vv[0].length();
}
u64 for_scalar_untouched() {
    vector<u64> xs = {1, 2, 3};
    u64 s = 0;
    for (auto x : xs) { s = s + x; }
    return s;
}
u64 vec_push_copy() {
    vector<vector<u64>> vv;
    vector<u64> x = {1, 2, 3};
    vec_push(vv, x);
    x.push(99);
    return vv[0].length();
}
u64 map_index_write_copy() {
    map<u64, vector<u64>> m;
    vector<u64> x = {1, 2, 3};
    m[1] = x;
    x.push(99);
    return m.get(1).length();
}
u64 map_index_key_copy() {
    map<vector<u8>, u64> m;
    vector<u8> k = "ab";
    m[k] = 7;
    k.push(99);
    return m.contains("ab");
}
u64 struct_member_copy() {
    vector<u64> v = {1, 2};
    Entry e = Entry { xs: v };
    v.push(99);
    return e.xs.length();
}
u64 for_ref_mutates() {
    vector<vector<u64>> vv;
    vector<u64> x = {1, 2, 3};
    vv.push(x);
    for (auto& e : vv) { e.push(99); }
    return vv[0].length();
}
u64 for_ref_scalar_writeback() {
    vector<u64> xs = {1, 2, 3};
    for (auto& x : xs) { x = x + 10; }
    return xs.at(0) + xs.at(1) + xs.at(2);
}
u64 for_const_ref_readonly() {
    vector<vector<u64>> vv;
    vector<u64> x = {1, 2, 3};
    vv.push(x);
    u64 s = 0;
    for (const auto& e : vv) { s = s + e.length(); }
    return s;
}
"""


def test_value_semantics():
    print("[8] 值语义：七个拷贝位置与 C++ / κ 对齐（其余仍是移动/别名）")
    it = Interp(parse_source(_VALUE_SEM_SRC, "valuesem.eln").module, None)

    # ① `T a = b;`（b 左值）→ 拷贝。容器级 / 元素级 / 字节串 / 定长 / 嵌套
    check(it.call("assign_copy", []) == 3, "`T dst = src;` 拷贝（容器级）")
    check(it.call("assign_elem", []) == 1, "`T dst = src;` 拷贝（元素级）")
    check(it.call("assign_bytes", []) == 3, "`vector<u8>` 拷贝后仍是独立字节串")
    check(it.call("assign_array", []) == 4, "`array` 拷贝保留原长度")
    check(it.call("assign_nested", []) == 2, "嵌套容器是**深**拷贝（内层不共享）")

    # ② 赋值 `a = b;`（不是声明初始化）→ 拷贝赋值
    check(it.call("assign_stmt_copy", []) == 3, "`dst = src;` 赋值也是拷贝（第 2 步新补）")

    # ③ 按值形参（实参左值）→ 拷贝；实参 prvalue → 不拷贝也能正常跑
    check(it.call("byval_copy", []) == 3, "按值形参没有改到调用方（长度仍是 3）")
    check(it.call("byval_prvalue", []) == 4, "按值形参收 prvalue 实参也正常")

    # ④ 入容器（push/insert/vec_push/map 下标写）→ 拷贝
    check(it.call("store_copy", []) == 3, "`box.insert(1, src)` 拷贝了 src")
    check(it.call("push_copy", []) == 2, "`vv.push(x)` 拷贝了 x")
    check(it.call("push_prvalue", []) == 3, "`push(prvalue)` 不拷贝也能用")
    check(it.call("set_copy", []) == 1, "`Set<T> t = s;`（`set_new()` 的值）也拷贝")
    check(it.call("vec_push_copy", []) == 3, "`vec_push(vv, x)` 拷贝了 x")
    check(it.call("map_index_write_copy", []) == 3, "`m[1] = x` 拷贝了值")
    check(it.call("map_index_key_copy", []) == 1, "`m[k] = v` 拷贝了键（改 k 不影响）")

    # ⑤ `return <借用值>` → 拷贝（第 2 步新补）；`return 局部变量` 仍是移动
    check(it.call("ret_elem_copy", []) == 3, "`return box.get(1);` 是拷贝（不再泄漏）")
    check(it.call("ret_ref_param_copy", []) == 3, "`return <const& 形参>` 是拷贝")
    check(it.call("ret_index_copy", []) == 3, "`return xs[0]`（元素）是拷贝")

    # ⑥ `for (auto x : xs)` 的元素绑定 → 每轮拷贝；标量元素照旧
    check(it.call("for_elem_copy", []) == 3, "`for (auto x : vv)` 改 x 不动原元素")
    check(it.call("for_scalar_untouched", []) == 6, "标量元素遍历语义不变")

    # ⑥-b **引用迭代不拷贝**（2026-11-10 夜，C++ 语义）：
    #   `auto&` 改的是容器元素（修前被当成 `in` 的按值拷贝 → 容器纹丝不动）；
    #   `const auto&` 只读、不拷贝；引用迭代标量元素要能写回。
    check(it.call("for_ref_mutates", []) == 4,
          "`for (auto& e : vv)` 真的改到容器（length 3 → 4）")
    check(it.call("for_ref_scalar_writeback", []) == 36,
          "`for (auto& x : xs) { x = x + 10; }` 写回元素（11+12+13）")
    check(it.call("for_const_ref_readonly", []) == 3,
          "`for (const auto& e : vv)` 只读、不拷贝")

    # `const auto&` 的循环变量不能重新绑定（C++ 里是编译错误；这里运行期报）
    _const_src = ("u64 f(const vector<u64>& xs) {\n"
                  "    for (const auto& x : xs) { x = x + 1; }\n"
                  "    return 0;\n}\n")
    try:
        Interp(parse_source(_const_src, "c.eln").module, None).call("f", [[1, 2]])
        _cmsg = None
    except Exception as _e:                       # noqa: BLE001
        _cmsg = str(_e)
    check(_cmsg is not None and "E-Assign-Immutable" in _cmsg
          and "只读引用" in _cmsg,
          "`const auto&` 循环变量重新绑定 → E-Assign-Immutable（教写 auto&）",
          str(_cmsg)[:160])

    # ⑦ `S{a}` 聚合初始化的成员拷贝
    check(it.call("struct_member_copy", []) == 2, "`Entry{xs: v}` 拷贝了 v")

    # **不该变**的位置：引用形参仍是别名；`return 局部变量` 仍是移动
    check(it.call("ref_alias", []) == 4, "`T&` 形参仍是别名（写回调用方）")
    check(it.call("ret_move", []) == 4, "`return 局部变量` 仍是移动（不重复拷贝）")


#: **K1（2026-11-10，数据丢失）**：`struct` 的容器字段读出来必须是**字段里
#: 那一份存储**。以前 `_field` 走 `_convert_to`，元素要从裸 `int` 重标成 `U64`
#: 时它 `[_convert_to(x) for x in v]` 重建列表 → `s.v.push(...)` / `s.v[0] = 9`
#: 都写在副本上，**静默**只剩第一次 push。
#:
#: 覆盖面按实测清单逐个钉住：`vector<u64>` / `vector<u32>` / `array<u64,N>` /
#: 嵌套 struct 里的 `vector<u64>` / 通过 `S&` 写；以及**不许回归**的
#: `vector<u8>` / `map` / `vector<i64>` / `vector<vector<u64>>`。
#: `value_semantics` 那一项同时钉住「修的是假别名，不是取消拷贝」。
_K1_FIELD_SRC = """
struct Inner { z: u64, w: vector<u64>, }
struct S {
    v: vector<u64>,
    b: vector<u8>,
    m: map<u64, u64>,
    a: array<u64, 3>,
    u: vector<u32>,
    n: u64,
    inner: Inner,
}
struct I { w: vector<i64>, }
struct V { vv: vector<vector<u64>>, }

S make_s() {
    return S { v: vector<u64>::make(), b: vector<u8>::make(),
               m: map<u64, u64>::make(), a: array<u64, 3>::make(),
               u: vector<u32>::make(), n: 0,
               inner: Inner { z: 1, w: vector<u64>::make() } };
}
u64 push_literal() {
    S s = make_s();
    s.v.push(3); s.v.push(1); s.v.push(7);
    return s.v.length() * 1000 + s.v.at(0) * 100 + s.v.at(1) * 10 + s.v.at(2);
}
u64 push_from_var() {
    S s = make_s();
    u64 a = 3; u64 b = 1; u64 c = 7;
    s.v.push(a); s.v.push(b); s.v.push(c);
    return s.v.length() * 1000 + s.v.at(0) * 100 + s.v.at(1) * 10 + s.v.at(2);
}
u64 index_write() {
    S s = make_s();
    s.v.push(3);
    s.v[0] = 9;
    return s.v.at(0);
}
u64 field_copy_value_semantics() {
    S a = make_s();
    a.v.push(1); a.v.push(2);
    S b = a;
    b.v.push(3);
    b.v[0] = 99;
    return a.v.length() * 100000 + b.v.length() * 10000
         + a.v.at(0) * 100 + b.v.at(0);
}
u64 u8_field() {
    S s = make_s();
    s.b.push(65); s.b.push(66); s.b.push(67);
    return s.b.length();
}
u64 map_field() {
    S s = make_s();
    s.m.insert(1, 10); s.m.insert(2, 20); s.m[3] = 30;
    return s.m.length() * 100 + s.m.get(3);
}
u64 i64_field() {
    I s = I { w: vector<i64>::make() };
    s.w.push(3); s.w.push(1); s.w.push(7);
    return s.w.length() * 1000 + s.w.at(0) * 100 + s.w.at(1) * 10 + s.w.at(2);
}
u64 nested_struct_field() {
    S s = make_s();
    s.inner.z = 5;
    s.inner.w.push(4);
    s.inner.w.push(5);
    return s.inner.w.length() * 10 + s.inner.z;
}
u64 nested_vector_field() {
    V s = V { vv: vector<vector<u64>>::make() };
    vector<u64> inner;
    inner.push(3);
    s.vv.push(inner);
    s.vv[0].push(9);
    return s.vv[0].length() * 10 + s.vv[0].at(1);
}
u64 array_field() {
    S s = make_s();
    s.a.push(3); s.a.push(1); s.a.push(7);
    return s.a.length() * 1000 + s.a.at(2);
}
u64 u32_field() {
    S s = make_s();
    s.u.push(3); s.u.push(1); s.u.push(7);
    return s.u.length() * 1000 + s.u.at(2);
}
u64 fill_by_ref(S& s) {
    s.v.push(3); s.v.push(1); s.v.push(7);
    return s.v.length();
}
u64 byref_field() {
    S s = make_s();
    u64 n = fill_by_ref(&s);
    return n * 1000 + s.v.length() * 100 + s.v.at(2);
}
"""


def test_struct_container_field_shared_storage():
    print("[8b] struct 容器字段：读出来就是字段本身（K1 数据丢失）")
    it = Interp(parse_source(_K1_FIELD_SRC, "k1field.eln").module, None)

    check(it.call("push_literal", []) == 3317,
          "字段 vector<u64> 三次字面量 push 全生效（3 1 7）",
          str(it.call("push_literal", [])))
    check(it.call("push_from_var", []) == 3317,
          "字段 vector<u64> 用 u64 变量 push 仍全生效（决定性反例）",
          str(it.call("push_from_var", [])))
    check(it.call("index_write", []) == 9,
          "字段下标写 s.v[0] = 9 落到字段上（at(0) == 9）",
          str(it.call("index_write", [])))
    check(it.call("field_copy_value_semantics", []) == 230199,
          "S b = a 后改 b.v 不影响 a.v（值语义/深拷贝没被取消）",
          str(it.call("field_copy_value_semantics", [])))

    check(it.call("u8_field", []) == 3, "vector<u8> 字段不回归（ABC）",
          str(it.call("u8_field", [])))
    check(it.call("map_field", []) == 330, "map 字段不回归（3 个键，m[3] == 30）",
          str(it.call("map_field", [])))
    check(it.call("i64_field", []) == 3317, "vector<i64> 字段不回归（3 1 7）",
          str(it.call("i64_field", [])))
    check(it.call("nested_struct_field", []) == 25,
          "嵌套 struct 里的 vector<u64> 字段不回归（w == 4 5，z == 5）",
          str(it.call("nested_struct_field", [])))
    check(it.call("nested_vector_field", []) == 29,
          "vector<vector<u64>> 字段不回归（[3, 9]）",
          str(it.call("nested_vector_field", [])))
    check(it.call("array_field", []) == 3007, "array<u64,3> 字段全生效（3 1 7）",
          str(it.call("array_field", [])))
    check(it.call("u32_field", []) == 3007, "vector<u32> 字段全生效（3 1 7）",
          str(it.call("u32_field", [])))
    check(it.call("byref_field", []) == 3307, "通过 S& 形参写字段也生效（3 1 7）",
          str(it.call("byref_field", [])))


def test_bitwise():
    """位运算的运行期语义（2026-11-09）：按位宽回绕、移位越界给 0、无符号不退化。"""
    src = """
u64 band(u64 a, u64 b) { return a & b; }
u64 bor(u64 a, u64 b) { return a | b; }
u64 bxor(u64 a, u64 b) { return a ^ b; }
u64 shl(u64 a, u64 b) { return a << b; }
u64 shr(u64 a, u64 b) { return a >> b; }
u8 bnot8() { return ~0; }
u16 bnot16() { return ~0; }
u32 bnot32() { return ~0; }
u64 bnot64() { return ~0; }
i64 bnot_signed() { return ~0; }
i64 shr_signed() { i64 x = -8; return x >> 1; }
u64 shr_unsigned() { u64 x = 0; return x - 1; }
u8 shl8_oob() { u8 x = 1; return x << 8; }
u8 shl8_in() { u8 x = 1; return x << 7; }
u64 shr64_oob() { u64 x = 1; return x >> 64; }
u64 mixed_and() { u64 a = 12; i64 b = -1; return a & b; }
u64 mixed_or(u64 a, i64 b) { return a | b; }
"""
    it = Interp(parse_source(src, "bw.eln").module, None)

    check(it.call("band", [12, 10]) == 8, "12 & 10 → 8")
    check(it.call("bor", [12, 10]) == 14, "12 | 10 → 14")
    check(it.call("bxor", [12, 10]) == 6, "12 ^ 10 → 6")
    check(it.call("shl", [12, 4]) == 192, "12 << 4 → 192")
    check(it.call("shr", [12, 2]) == 3, "12 >> 2 → 3")

    check(it.call("bnot8", []) == 255, "u8 `~0` → 255")
    check(it.call("bnot16", []) == 65535, "u16 `~0` → 65535")
    check(it.call("bnot32", []) == 4294967295, "u32 `~0` → 4294967295")
    check(it.call("bnot64", []) == 18446744073709551615, "u64 `~0` → 2^64-1")
    check(it.call("bnot_signed", []) == -1, "i64 `~0` → -1（补码）")

    check(it.call("shr_signed", []) == -4, "i64 右移是算术右移（-8 >> 1 → -4）")
    check(it.call("shr_unsigned", []) == 18446744073709551615,
          "u64 `0 - 1` 仍是 2^64-1（无符号不退化）")

    check(it.call("shl8_oob", []) == 0, "u8 `1 << 8`（移位量 = 位宽）→ 0")
    check(it.call("shl8_in", []) == 128, "u8 `1 << 7` → 128")
    check(it.call("shr64_oob", []) == 0, "u64 `1 >> 64` → 0")

    check(it.call("mixed_and", []) == 12, "u64 & i64：按无符号（-1 视为全 1）")
    check(it.call("mixed_or", [12, -1]) == 18446744073709551615,
          "u64 | i64(负数)：结果仍是 u64 语义")


def test_repeats_runtime():
    """`repeats` 是**运行期可校验的断言**（2026-11-09）。

    修之前：`spec pending(...) { repeats 4; }` 配一个跑 999 圈的循环，
    `check` / `cost` / `run` 全绿——假命题能让契约通过（实测
    `_tier_a/union_find/probe_repeats`）。现在循环跑过声明上界就抛
    `E-Repeats-Exceeded`；真实上界必须照常通过（不误报）。
    """
    from interp import InterpError

    print("[9] repeats：运行期断言（假声明必须被抓）")
    decl = parse_source(
        "spec pending(u64 x) -> bool { effects {}; repeats 4; }\n"
        "spec walk(u64 n) -> u64 { effects {}; }\n", "r.tu").module
    impl = parse_source(
        "bool pending(u64 x) { return x < 1000; }\n"
        "u64 walk(u64 n) {\n"
        "    u64 i = 0;\n"
        "    while (pending(i)) { i = i + 1; }\n"
        "    return i;\n}\n", "r.eln").module
    it = Interp(impl, decl)
    try:
        got = it.call("walk", [0])
        check(False, "repeats 4 但真跑 1000 圈 → 必须抛诊断", f"没抛，返回 {got}")
    except InterpError as exc:
        check("E-Repeats-Exceeded" in str(exc),
              "repeats 4 但真跑 1000 圈 → E-Repeats-Exceeded", str(exc))

    # **真实的声明必须通过**：同一段实现，repeats 1000 → 跑满 1000 圈不报。
    decl_ok = parse_source(
        "spec pending(u64 x) -> bool { effects {}; repeats 1000; }\n"
        "spec walk(u64 n) -> u64 { effects {}; }\n", "r.tu").module
    it_ok = Interp(impl, decl_ok)
    check(it_ok.call("walk", [0]) == 1000,
          "repeats 1000 → 跑满 1000 圈照常通过（不误报）",
          str(it_ok.call("walk", [0])))

    # **带尺寸的表达式**按运行期实际尺寸求值：`repeats log2(k)` 的 k 从谓词
    # 形参按本次实参绑定（不是从调用方同名变量碰运气）。
    decl_lg = parse_source(
        "spec pred(u64 k) -> bool { effects {}; repeats log2(k); }\n"
        "spec halve(u64 n) -> u64 { effects {}; }\n", "r.tu").module
    impl_lg = parse_source(
        "bool pred(u64 k) { return k > 1; }\n"
        "u64 halve(u64 n) {\n"
        "    u64 j = n;\n"
        "    u64 c = 0;\n"
        "    while (pred(j)) { j = j / 2; c = c + 1; }\n"
        "    return c;\n}\n", "r.eln").module
    it_lg = Interp(impl_lg, decl_lg)
    check(it_lg.call("halve", [1024]) == 10,
          "repeats log2(k)：1024 减半真跑 10 圈 = log2(1024) → 通过",
          str(it_lg.call("halve", [1024])))
    try:
        it_lg.call("halve", [1 << 20])      # log2 = 20，真跑 20 圈 → 仍在上界内
        check(True, "repeats log2(k)：2^20 真跑 20 圈 → 通过")
    except Exception as exc:               # noqa: BLE001
        check(False, "repeats log2(k)：2^20 真跑 20 圈 → 通过", str(exc))

    # **严格上界（2026-11-10）**：repeats 支持 `绑定项 + 整数字面量`，
    # 于是 `log2(k) + 1` 写得出来，**「log 型额外 +1 圈」那条特例已删除**。
    # 二分的真圈数是 `floor(log2 m) + 1`（m=1024 → 11 圈；m=1 是 1 圈，
    # 而 log2(1)=0），所以严格写法是 `log2(k) + 1`，不是 `log2(k)`。
    decl_lg1 = parse_source(
        "spec pred1(u64 k) -> bool { effects {}; repeats log2(k); }\n"
        "spec down(u64 n) -> u64 { effects {}; }\n", "r.tu").module
    impl_lg1 = parse_source(
        "bool pred1(u64 k) { return k > 0; }\n"
        "u64 down(u64 n) {\n"
        "    u64 j = n;\n"
        "    u64 c = 0;\n"
        "    while (pred1(j)) { j = j / 2; c = c + 1; }\n"
        "    return c;\n}\n", "r.eln").module

    # `log2(k)` 单独写**不再是上界**（以前靠特例放行）：11 > 10 → 必须抓。
    # 修前这里是「放行」，正是 ① 要复现的那个洞。
    try:
        got = Interp(impl_lg1, decl_lg1).call("down", [1024])
        check(False, "repeats log2(k) 真跑 11 圈 → 必须抓（不再允许 +1）",
              f"没抛，返回 {got}")
    except InterpError as exc:
        check("E-Repeats-Exceeded" in str(exc),
              "repeats log2(k) 真跑 11 圈 → E-Repeats-Exceeded（不再允许 +1）",
              str(exc))

    # `repeats log2(k) + 1` 才是严格上界：同一段实现照常通过。
    decl_plus = parse_source(
        "spec pred1(u64 k) -> bool { effects {}; repeats log2(k) + 1; }\n"
        "spec down(u64 n) -> u64 { effects {}; }\n", "r.tu").module
    try:
        got = Interp(impl_lg1, decl_plus).call("down", [1024])
        check(got == 11, "repeats log2(k) + 1：11 圈 = 严格上界 → 通过", str(got))
    except Exception as exc:               # noqa: BLE001
        check(False, "repeats log2(k) + 1：11 圈 = 严格上界 → 通过", str(exc))

    # ---- 常数 / 尺寸上界仍是**严格**比（不许借 +1 的旧特例混过去）
    decl5 = parse_source(
        "spec five(u64 x) -> bool { effects {}; repeats 4; }\n"
        "spec walk5(u64 n) -> u64 { effects {}; }\n", "r.tu").module
    impl5 = parse_source(
        "bool five(u64 x) { return x < 5; }\n"
        "u64 walk5(u64 n) {\n"
        "    u64 i = 0;\n"
        "    while (five(i)) { i = i + 1; }\n"
        "    return i;\n}\n", "r.eln").module
    try:
        got = Interp(impl5, decl5).call("walk5", [0])
        check(False, "repeats 4 真跑 5 圈 → 必须抓", f"没抛，返回 {got}")
    except InterpError as exc:
        check("E-Repeats-Exceeded" in str(exc), "repeats 4 真跑 5 圈 → E-Repeats-Exceeded",
              str(exc))

    # `repeats log2(8)` = 3，跑 4 圈 → 抓（以前含 log2 的界被额外 +1，放行）
    decl8 = parse_source(
        "spec eight(u64 k) -> bool { effects {}; repeats log2(8); }\n"
        "spec half4(u64 n) -> u64 { effects {}; }\n", "r.tu").module
    impl8 = parse_source(
        "bool eight(u64 k) { return k > 0; }\n"
        "u64 half4(u64 n) {\n"
        "    u64 j = n;\n"
        "    u64 c = 0;\n"
        "    while (eight(j)) { j = j / 2; c = c + 1; }\n"
        "    return c;\n}\n", "r.eln").module
    try:
        got = Interp(impl8, decl8).call("half4", [8])
        check(False, "repeats log2(8) 真跑 4 圈 → 必须抓（不再允许 +1）",
              f"没抛，返回 {got}")
    except InterpError as exc:
        check("E-Repeats-Exceeded" in str(exc),
              "repeats log2(8) 真跑 4 圈 → E-Repeats-Exceeded", str(exc))

    # ---- **真样例（严格声明）**：`search.tu` 的 `log(hi - lo) + 1`
    # 在 m = 1 / 2 / 128 都是真上界（m=1 是最关键的一档：log2(1)=0，旧写法差 1）。
    tu_s, ch_s = load("search")
    it_s = Interp(ch_s, tu_s)
    for m in (1, 2, 128):
        xs = [2 * i for i in range(m)]
        try:
            r = it_s.call("binary_search", [xs, 1],
                          force={"search_impl": "search_binary"})
            ok = err_variant(r) == "NotFound"
            check(ok, f"search：m={m} 的二分跑满真圈数 ≤ log2(m)+1（不抛）", str(r))
        except Exception as exc:           # noqa: BLE001
            check(False, f"search：m={m} 的二分跑满真圈数 ≤ log2(m)+1（不抛）", str(exc))


def test_recursion_depth_runtime():
    """`recursion depth <= N` 是**运行期断言**（2026-11-10）。

    修前（`_verify/report.md` K3）：这条声明**只进 κ**。三份逐字节相同的
    `.eln` 只改 `recursion depth`（8 / 64 / 256），最大安全深度**都是 75**
    （宿主 `sys.setrecursionlimit` 决定），唯一差别是 κ 里那个常数
    `4 + 13N`。于是「声明 8、实跑 75 层」时 **κ=117 < ops=831** ——
    **上界被越过**，而这正是整轮在追的那条病。

    现在：超过声明 → `E-Recursion-Depth-Exceeded`（fail-fast）；不超 → 照常
    通过；连声明都没有而撞上宿主栈 → `E-Recursion-HostLimit`（宿主异常
    **不许外泄**，必须给函数名 + 层数）。
    """
    from interp import (E_RECURSION_DEPTH_EXCEEDED, E_RECURSION_HOST_LIMIT,
                        InterpError)

    print("[9-b] recursion depth：运行期断言（声明越小越容易被抓）")
    _chain_eln = (
        "u64 chain(u64 n) {\n"
        "    if (n == 0) { return 0; }\n"
        "    return 1 + chain(n - 1);\n"
        "}\n")

    def mod(depth):
        tu = "spec chain(u64 n) -> u64 {\n    effects {};\n"
        if depth is not None:
            tu += f"    recursion depth <= {depth};\n"
        tu += "    target { time: { max: O(n) }, allocation: { max: 0 }, };\n}\n"
        return (parse_source(tu, "d.tu").module,
                parse_source(_chain_eln, "d.eln").module)

    # ① 声明 8、实跑 20 层 → **必须抓**（修前静默跑完，κ 只有 117）。
    tu8, el8 = mod(8)
    try:
        got = Interp(el8, tu8).call("chain", [20])
        check(False, "声明 8、实跑 20 层 → 必须抛 E-Recursion-Depth-Exceeded",
              f"没抛，返回 {got}")
    except InterpError as exc:
        check(E_RECURSION_DEPTH_EXCEEDED in str(exc),
              "声明 8、实跑 20 层 → E-Recursion-Depth-Exceeded", str(exc))
    # 边界不误伤：8 层递归 = 9 帧，正好在上界内（与 cost 的 `(N+1)×体一次`
    # 同一口径；`test_kappa_gaps.py` 的 `down(xs, d)` + `<= d` 靠这条）。
    check(Interp(el8, tu8).call("chain", [8]) == 8,
          "声明 8、实跑 8 层（9 帧）→ 通过（边界不误报）")

    # ② 声明 256、实跑 75 层 → 通过。**宿主默认上限只有 ~75 层**，
    #    能通过说明引擎按声明抬了 `sys.setrecursionlimit`（只作开闸用）。
    tu256, el256 = mod(256)
    check(Interp(el256, tu256).call("chain", [75]) == 75,
          "声明 256、实跑 75 层 → 通过")

    # ③ 声明 256、实跑 300 层 → 抓的是**语言断言**，不是宿主 RecursionError。
    try:
        got = Interp(el256, tu256).call("chain", [300])
        check(False, "声明 256、实跑 300 层 → 必须抛 E-Recursion-Depth-Exceeded",
              f"没抛，返回 {got}")
    except RecursionError as exc:
        check(False, "声明 256、实跑 300 层 → 不许漏宿主 RecursionError", str(exc))
    except InterpError as exc:
        check(E_RECURSION_DEPTH_EXCEEDED in str(exc),
              "声明 256、实跑 300 层 → E-Recursion-Depth-Exceeded（不是 RecursionError）",
              str(exc))

    # ④ **没有声明** + 撞上宿主栈 → 仍必须是语言诊断（第 ④ 条硬约束）。
    tu0, el0 = mod(None)
    _old = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(1000)
        try:
            got = Interp(el0, tu0).call("chain", [200])
            check(False, "没声明、实跑 200 层（宿主 1000 帧）→ "
                         "必须抛 E-Recursion-HostLimit", f"没抛，返回 {got}")
        except RecursionError as exc:
            check(False, "没声明、实跑 200 层 → 不许漏宿主 RecursionError", str(exc))
        except InterpError as exc:
            check(E_RECURSION_HOST_LIMIT in str(exc),
                  "没声明、实跑 200 层 → E-Recursion-HostLimit", str(exc))
            check("chain" in str(exc),
                  "E-Recursion-HostLimit 文案里有函数名（可定位）", str(exc))
    finally:
        sys.setrecursionlimit(_old)

    # ⑤ 读不出正整数的声明**不许静默**：`<= 0` 会让 cost 当成没声明
    #    （只算一层 κ），只剩运行期这条兜底。
    tu_bad = parse_source(
        "spec chain(u64 n) -> u64 { effects {}; recursion depth <= 0;\n"
        "    target { time: { max: O(n) } }; }\n", "b.tu").module
    try:
        got = Interp(el8, tu_bad).call("chain", [3])
        check(False, "`recursion depth <= 0` → 运行期必须抛 E-Recursion-Uncheckable",
              f"没抛，返回 {got}")
    except InterpError as exc:
        check("E-Recursion-Uncheckable" in str(exc),
              "`recursion depth <= 0` → E-Recursion-Uncheckable", str(exc))

    # ⑥ 线程模式（`parallel { … } threads` 里多个任务共用一个 `Interp`）：
    #    层数表必须 **per-thread**。共享一个 dict 会把两个线程的层数相加
    #    （各 5 层 → 误判 10 层），声明 8 就会假报。
    import threading as _th
    _it = Interp(el8, tu8)
    _seen = {}

    def _peek():
        _seen[_th.get_ident()] = _it._depth_counts()

    _ts = [_th.Thread(target=_peek) for _ in range(4)]
    for _t in _ts:
        _t.start()
    for _t in _ts:
        _t.join()
    check(len(_seen) == 4 and len({id(v) for v in _seen.values()}) == 4,
          "4 个线程各拿到一份独立的层数表（per-thread，不串味）", str(len(_seen)))


def test_pow():
    """`**` 的**值**（2026-11-10）。

    这条测试存在的理由就是"错值"：在 2026-11-10 之前，`a ** b` 会被解析成
    `a * (*b)`（前缀 `*` 是恒等解引用），于是 `2 ** 10` = 20、`3 ** 4` = 12、
    `16.0 ** 0.5` = 8.0——`check` 全绿、`run` 跑得动、结果全错。所以这里
    断言的是**具体数字**，不是"没崩"。
    """
    print("[?] `**` 幂：整数/浮点/结合性/回绕")
    src = """u64 ipow(u64 a) {
    return a ** 10;
}
u64 ipow_small(u64 a) {
    return a ** 4;
}
u64 ipow0(u64 a) {
    return a ** 0;
}
u64 ipow_wrap_64(u64 a) {
    return a ** 64;
}
u64 right_assoc() {
    return 2 ** 3 ** 2;
}
i64 unary_tighter() {
    return -2 ** 2;
}
f64 fpow(f64 a, f64 b) {
    return a ** b;
}
f64 fsqrt(f64 a) {
    return a ** 0.5;
}
f64 int_float_pow(u64 a, f64 b) {
    return a ** b;
}
f64 fneg_exp() {
    return 2.0 ** -1;
}
f64 fnan() {
    return (-8.0) ** 0.5;
}
f64 finf() {
    return 1e308 ** 2;
}
f64 fmul(f64 a, f64 b) {
    return a * b;
}
u64 imul(u64 a, u64 b) {
    return a * b;
}"""
    r = parse_source(src, "pow.eln")
    check(not r.diagnostics, "幂用例能解析",
          "; ".join(d.code for d in r.diagnostics))
    it = Interp(r.module, None)

    # ---- 用户报的那三行：必须是 1024 / 81 / 4.0，不是 20 / 12 / 8.0
    check(it.call("ipow", [2]) == 1024, "2 ** 10 → 1024", str(it.call("ipow", [2])))
    check(it.call("ipow_small", [3]) == 81, "3 ** 4 → 81", str(it.call("ipow_small", [3])))
    check(it.call("fsqrt", [16.0]) == 4.0, "16.0 ** 0.5 → 4.0",
          str(it.call("fsqrt", [16.0])))

    # ---- 平方根通路 + 精度（用户点名的那一位）
    check(it.call("fsqrt", [2.0]) == 1.4142135623730951,
          "2.0 ** 0.5 → 1.4142135623730951（与 C pow 同一位）",
          repr(it.call("fsqrt", [2.0])))
    check(it.call("int_float_pow", [2, 0.5]) == 1.4142135623730951,
          "2 ** 0.5（整数底数）也提升成浮点幂、同一位",
          repr(it.call("int_float_pow", [2, 0.5])))

    # ---- 结合性：右结合。左结合会得 (2**3)**2 = 64
    check(it.call("right_assoc", []) == 512, "2 ** 3 ** 2 → 512（右结合）",
          str(it.call("right_assoc", [])))

    # ---- 一元前缀比 `**` 紧：-2 ** 2 = (-2)**2 = 4（Python 是 -4，这里不同）
    check(it.call("unary_tighter", []) == 4, "-2 ** 2 → 4（一元先绑定）",
          str(it.call("unary_tighter", [])))

    # ---- 整数幂的溢出：**与 `*` 同一口径**（u64 回绕），不是报错、也不是
    #      静默给个"看着合理"的数。`2 ** 64` 在 u64 里就是 0。
    check(it.call("ipow0", [5]) == 1, "5 ** 0 → 1", str(it.call("ipow0", [5])))
    check(it.call("ipow0", [0]) == 1, "0 ** 0 → 1（与 C pow/Math.pow 同口径）",
          str(it.call("ipow0", [0])))
    check(it.call("ipow_wrap_64", [2]) == 0, "2 ** 64 → 0（u64 回绕，与 2*…*2 一致）",
          str(it.call("ipow_wrap_64", [2])))

    # ---- 浮点：IEEE 语义（不抛宿主异常、不崩）
    check(it.call("fpow", [-2.0, 3.0]) == -8.0, "(-2.0) ** 3.0 → -8.0",
          str(it.call("fpow", [-2.0, 3.0])))
    check(it.call("fneg_exp", []) == 0.5, "2.0 ** -1 → 0.5",
          str(it.call("fneg_exp", [])))
    import math as _math
    nan_v = it.call("fnan", [])
    check(isinstance(nan_v, float) and _math.isnan(nan_v),
          "(-8.0) ** 0.5 → nan（IEEE，不是宿主 ValueError）", str(nan_v))
    inf_v = it.call("finf", [])
    check(isinstance(inf_v, float) and _math.isinf(inf_v),
          "1e308 ** 2 → inf（IEEE，不抛 OverflowError）", str(inf_v))

    # ---- **不许破坏二元 `*`**（`**` 的旧实现就是靠 `*` 蒙混过去的）
    check(it.call("imul", [3, 4]) == 12, "3 * 4 → 12（二元乘法不变）",
          str(it.call("imul", [3, 4])))
    check(it.call("fmul", [1.5, 2.0]) == 3.0, "1.5 * 2.0 → 3.0（浮点乘法不变）",
          str(it.call("fmul", [1.5, 2.0])))


def test_const_and_enum_ordinal():
    """模块常量的**表达式初值**与枚举 `as` 的**序号**（2026-11-10）。

    两条以前都是**静默错值**：

    · `const N: u64 = 10 * 2;` → `check` 绿、`run` 打印 `None`（常量只取字面量）；
    · `Color::Green as u64` → 原样返回 `EnumVal`（`run` 打印 `Color.Green`）。

    所以断言的是**具体数字**，不是"没崩"。
    """
    print("[?] 模块常量表达式初值 / 枚举序号")
    tu_src = """const N: u64 = 10 * 2;
const M: u64 = N + 1;
const P: u64 = 2 ** 10;
const SH: u64 = 1 << 4;
const C: u8 = 250 + 10;
enum Color { Red, Green, Blue }
enum Shape { Circle(r: u64), Square(s: u64), }
spec f() -> u64 { effects {}; }
spec g() -> u64 { effects {}; }
spec h() -> u64 { effects {}; }
"""
    ch_src = """u64 f() {
    return N + M + P + SH + C as u64;
}
u64 g() {
    const Color c = Color::Green;
    return c as u64;
}
u64 h() {
    return Shape::Square(9) as u64;
}
"""
    tu = parse_source(tu_src, "c.tu").module
    ch = parse_source(ch_src, "c.eln").module
    it = Interp(ch, tu)
    # 20 + 21 + 1024 + 16 + 4 = 1085
    check(it.call("f", []) == 1085,
          "`10*2` / `N+1` / `2**10` / `1<<4` / `u8` 截断 → 1085",
          str(it.call("f", [])))
    check(it.call("g", []) == 1, "Color::Green as u64 → 1（变体位置）",
          str(it.call("g", [])))
    check(it.call("h", []) == 1, "带载荷的 Shape::Square as u64 → 1（载荷不影响序号）",
          str(it.call("h", [])))


def _ptr_env(tu_src, ch_src):
    """指针用例的 `Interp`：**不经过 `typecheck` 闸**。

    这一条是**故意**的：本组测的是**运行期**语义，闸在 `typecheck.py`
    （`E-Pointer-RuntimeUnsupported`，另一路在删），这里直接拿解释器跑，
    才能证明"运行期真的接线了"，而不是"闸放行了但跑出来是错的"。
    """
    tu = parse_source(tu_src, "ptr.tu").module
    ch = parse_source(ch_src, "ptr.eln").module
    return Interp(ch, tu)


def _ptr_err(tu_src, ch_src, fn="main", args=None):
    """跑一次，把**运行期语言诊断**的文本返回；没抛返回 `None`。

    只捕 `InterpError`——别的异常（`TypeError` / `AttributeError` …）
    是**宿主异常**，必须让它冒出来把用例打红：那正是"不许崩宿主"这条
    承诺的判据（空指针解引用曾经就是 `None.fields` 的 `AttributeError`）。
    """
    from interp import InterpError
    try:
        _ptr_env(tu_src, ch_src).call(fn, args or [])
    except InterpError as exc:
        return str(exc)
    return None


def test_pointer_runtime():
    """指针的**运行期**：`&x` 取地址 / `*p` 读写 / `p->f` / `nullptr`（2026-11-10）。

    ## 这一组在钉什么

    指针是**唯一允许别名的地方**：`q->f = 9` 改的必须就是 `s.f`。
    接线前解释器里 `*p` / `&x` 是"值就是操作数本身"，所以 `u64 y = *p;`
    会**静默拿到 p 自己的值**——比崩更糟。这组用例断言的是**具体数字**
    （`9` / `7` / `20`），不是"没崩"：

    | 语义 | 断言 |
    |---|---|
    | `&x` 指向存储，不是拷贝 | `*p = 7` 之后 `x == 7` |
    | `p->f` 与 `(*p).f` 同形（AST 同构） | 两者读到同一个字段，写也写同一处 |
    | `S* r = q;` 拷的是地址 | `r->f = 42` 之后 `s.f == 42` |
    | `nullptr` 比较 / `if (p)` 判空 | `&x != nullptr` 真、`nullptr == nullptr` 真、`if (nullptr)` 假 |
    | 空指针解引用 | 抛 **`E-Pointer-NullDeref`**（运行期），**不是**宿主异常 |
    | 值语义未破 | `S b = a;` 仍是深拷贝（改 `b` 不动 `a`） |
    """
    print("[10] 指针运行期：&x / *p / p->f / nullptr / 别名 vs 值语义")
    from interp import E_POINTER_NULL_DEREF, InterpError, U64

    TU = "struct S { f: u64, };"

    # ---- ① `&x` 产出地址（指向那块存储，不是拷贝）+ `*p` 读
    it = _ptr_env(TU, """
u64 get() { u64 x = 3; u64* p = &x; u64 y = *p; return y; }
u64 main() { return get(); }
""")
    check(it.call("main", []) == 3, "`&x` 取地址 + `*p` 读到 x 的值（3）",
          str(it.call("main", [])))

    # ---- ② `*p = 7` 写到 x 上（别名生效）
    it = _ptr_env(TU, """
u64 f() { u64 x = 3; u64* p = &x; *p = 7; return x; }
""")
    check(it.call("f", []) == 7, "`*p = 7` 改到的就是 `x`（读回 7）",
          str(it.call("f", [])))

    # ---- ③ `q->f = 9` 写穿结构体；`q->f` 与 `(*q).f` 同值
    it = _ptr_env(TU, """
u64 f() {
    S s; s.f = 1;
    S* q = &s;
    q->f = 9;
    return s.f * 100 + (*q).f;
}
""")
    check(it.call("f", []) == 909,
          "`q->f = 9` 改到 `s.f`；`q->f` 与 `(*q).f` 同值（909）",
          str(it.call("f", [])))

    # ---- ④ `S* r = q;` 拷的是地址：两个指针指向同一对象
    it = _ptr_env(TU, """
u64 f() {
    S s; s.f = 1;
    S* q = &s;
    S* r = q;
    r->f = 42;
    return s.f;
}
""")
    check(it.call("f", []) == 42, "`S* r = q;` 拷地址：`r->f = 42` 改到 `s.f`",
          str(it.call("f", [])))

    # ---- ⑤ 指针当标量：赋值（②拷贝点）也是拷地址，不深拷被指对象
    it = _ptr_env(TU, """
u64 f() {
    S s; s.f = 5;
    S* q = &s;
    S* r = nullptr;
    r = q;
    r->f = 6;
    return s.f;
}
""")
    check(it.call("f", []) == 6, "`r = q;`（赋值）拷地址：`r->f = 6` 改到 `s.f`",
          str(it.call("f", [])))

    # ---- ⑥ `nullptr` 比较 / 真值（`if (p)` / `p != nullptr`）
    it = _ptr_env(TU, """
u64 f(u64* p) { if (p) { return 1; } return 0; }
u64 main() {
    u64 x = 3;
    u64* p = &x;
    u64* z = nullptr;
    if (p == nullptr) { return 90; }
    if (z != nullptr) { return 91; }
    if (z) { return 92; }
    if (p != nullptr) { return 1; }
    return 93;
}
""")
    check(it.call("main", []) == 1,
          "`p == nullptr` 假 / `z != nullptr` 假 / `if (z)` 假 / `if (p)` 真",
          str(it.call("main", [])))
    check(it.call("f", [None]) == 0, "形参收到 nullptr（None）时 `if (p)` 为假",
          str(it.call("f", [None])))

    # ---- ⑦ `u64* z;`（无初值）默认是**空指针**，不是 0
    it = _ptr_env(TU, """
u64 f() { u64* z; if (z == nullptr) { return 1; } return 0; }
""")
    check(it.call("f", []) == 1, "`u64* z;` 默认值 = 空指针（`z == nullptr`）",
          str(it.call("f", [])))

    # ---- ⑧ **空指针解引用**：抛 `E-Pointer-NullDeref`（运行期），不崩宿主
    msg = _ptr_err(TU, """
u64 g(u64* p) { return *p; }
u64 main() { u64* z = nullptr; return g(z); }
""")
    check(msg is not None and E_POINTER_NULL_DEREF in msg,
          "运行期 `*p` 解引用空指针 → E-Pointer-NullDeref（经形参，静态证不出来）",
          str(msg))
    # 读 `p->f` 也走 `deref`：空指针同样必须是语言诊断
    msg = _ptr_err(TU, """
u64 g(S* q) { return q->f; }
u64 main() { S* z = nullptr; return g(z); }
""")
    check(msg is not None and E_POINTER_NULL_DEREF in msg,
          "运行期 `q->f` 解引用空指针 → E-Pointer-NullDeref", str(msg))
    # **写**空指针同样是诊断（不是宿主 AttributeError）
    msg = _ptr_err(TU, """
u64 h(u64* p) { *p = 1; return 0; }
u64 main() { u64* z = nullptr; return h(z); }
""")
    check(msg is not None and E_POINTER_NULL_DEREF in msg,
          "运行期 `*p = …` 写空指针 → E-Pointer-NullDeref", str(msg))
    # 直线上的 `nullptr` 直接解引用也必须是诊断（哪怕静态先拦，运行期也要拦）
    msg = _ptr_err(TU, """
u64 main() { u64* z = nullptr; return *z; }
""")
    check(msg is not None and E_POINTER_NULL_DEREF in msg,
          "直线 `u64* z = nullptr; *z` → 运行期也报 E-Pointer-NullDeref", str(msg))
    # **非指针解引用**：语言诊断，不是宿主 TypeError
    msg = _ptr_err(TU, "u64 main() { u64 x = 1; return *x; }")
    check(msg is not None and "不是指针" in msg,
          "`*x`（x 是 u64）→ 语言诊断（不是宿主 TypeError）", str(msg))

    # ---- ⑨ **值语义未破**：`S b = a;` 仍是深拷贝（指针唯一的别名例外）
    it = _ptr_env(TU, """
u64 f() {
    S a; a.f = 1;
    S b = a;
    b.f = 2;
    return a.f * 10 + b.f;
}
""")
    check(it.call("f", []) == 12,
          "`S b = a;` 仍是值语义：改 b.f 不影响 a.f（12）", str(it.call("f", [])))

    # ---- ⑩ 指针**字段**：`S t = s;` 里指针字段只拷地址（浅），不深拷被指对象
    TU_NODE = "struct N { v: u64, }; struct S { f: u64, p: N*, };"
    it = _ptr_env(TU_NODE, """
u64 f() {
    N n; n.v = 1;
    S s; s.f = 7; s.p = &n;
    S t = s;
    t.p->v = 9;
    return n.v;
}
""")
    check(it.call("f", []) == 9,
          "结构体拷贝里指针字段只拷地址（`t.p->v` 改到同一个 n，9）",
          str(it.call("f", [])))

    # ---- ⑪ 别名与 `u64` 的宽度：`*p = 0 - 1;` 落进去的是 2^64-1
    it = _ptr_env(TU, """
u64 f() { u64 x = 0; u64* p = &x; *p = 0 - 1; return x; }
""")
    v = it.call("f", [])
    check(v == 18446744073709551615 and isinstance(v, U64),
          "`*p = 0 - 1` 按**指向类型**落值：x = 2^64-1（不是 -1）", repr(v))

    # ---- ⑫ 双层指针：`&p` / `**pp` 读写都到最里面那个存储
    it = _ptr_env(TU, """
u64 f() {
    u64 x = 1;
    u64* p = &x;
    u64** pp = &p;
    **pp = 5;
    return x * 10 + (*p);
}
""")
    check(it.call("f", []) == 55,
          "`u64** pp = &p; **pp = 5;` 写穿两层到 x（55）", str(it.call("f", [])))

    # ---- ⑬ 容器元素 / 结构体字段的地址（`_ref_of` 的另外两种存储）
    it = _ptr_env(TU, """
u64 f() {
    vector<u64> xs = {5, 6, 7};
    u64* p = &xs[1];
    *p = 60;
    S s; s.f = 1;
    u64* q = &s.f;
    *q = 11;
    return xs[1] * 100 + s.f;
}
""")
    check(it.call("f", []) == 6011,
          "`&xs[1]` / `&s.f` 也是真存储：`*p = 60` / `*q = 11` 都写穿",
          str(it.call("f", [])))

    # ---- ⑭ 引用形参的**显式借用标记** `&a` 没被"取地址"打坏（calculator 依赖）
    it = _ptr_env(TU, """
u64 take(array<u64, 2>& a) { a[0] = 1; return a[0]; }
u64 f() { array<u64, 2> a = {0, 0}; return take(&a); }
""")
    check(it.call("f", []) == 1,
          "`take(&a)`（`T&` 形参的显式借用标记）仍然按引用传、写回调用方",
          str(it.call("f", [])))

    # ---- ⑮ 指针**误用**一律是语言诊断，不是宿主异常（`TypeError`）
    #
    # 值域里指针是一个真对象，所以"拿它当容器 / 当数字 / 当可迭代"都会在宿主
    # 抛 `TypeError`。这些路径必须在解释器里翻成 `InterpError`——
    # 宿主异常泄漏到用户面前 = 没有码、没有修法的一句 traceback。
    for name, src in (
            ("`p + 1`（指针算术）", "u64 f() { u64 x = 1; u64* p = &x; return p + 1; }"),
            ("`p < q`（指针比大小）", "u64 f() { u64 x = 1; u64* p = &x; u64* q = &x; "
                                    "if (p < q) { return 1; } return 0; }"),
            ("`p[0]`（指针下标）", "u64 f() { u64 x = 1; u64* p = &x; return p[0]; }"),
            ("`p as u64`（指针转整数）", "u64 f() { u64 x = 1; u64* p = &x; return p as u64; }"),
            ("`-p`（指针取负）", "u64 f() { u64 x = 1; u64* p = &x; return 0 - 0 + (0 - p); }"),
            ("`for (auto y : p)`（遍历指针）",
             "u64 f() { u64 x = 1; u64* p = &x; for (auto y : p) { print(\"{}\", y); } return 0; }"),
    ):
        msg = _ptr_err(TU, src, fn="f")
        check(msg is not None,
              f"{name} → 语言诊断（不是宿主 TypeError）", str(msg))


def main():
    test_search()
    test_sum()
    test_calculator()
    test_wrapping()
    test_budget()
    test_budget_messages()
    test_bench()
    test_per_function_calls()
    test_negative_numbers_xfail()
    test_value_semantics()
    test_struct_container_field_shared_storage()
    test_bitwise()
    test_repeats_runtime()
    test_recursion_depth_runtime()
    test_pow()
    test_const_and_enum_ordinal()
    test_pointer_runtime()

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
