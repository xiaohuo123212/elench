#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_out_param.py —— **出参尺寸传播**（`T&` 出参，P0-1，2026-11-12）

## 为什么要这份测试

真实 C++ 里"用出参把容器填满"是最常见的写法之一：

    static void count_words(const vector<string>& words,
                            unordered_map<string,u64>& freq) {
        for (const auto& w : words) { freq[w] += 1; }
    }
    int main() {
        vector<string> cmds;  … push_back ×6 …
        unordered_map<string,u64> freq;      // **空的**，声明处推不出尺寸
        count_words(cmds, freq);             // ← 这里才把 freq 填满
        for (auto it = freq.begin(); it != freq.end(); ++it) …
    }

`freq` 是**局部**容器，`target` 里引用不到它 ⇒ 遍历半径写不出 ⇒ κ = ⊤。
出参传播的口径（与 `_ret_size_of` 是**对偶**，同一份增长预扫）：

    |out| ≤ |out|₀ + Σ(插入点被执行次数)         （被调方算的是后一项）
    |freq| ≤ 0 + n(words) = n(cmds)             （调用点用实参的界实例化）

加法只会变大（成本对尺寸单调）⇒ κ 仍是上界。**证明不了就 ⊤**，三条边界都钉在下文：
  ① 增长依赖出参**自己的当前尺寸**（`while (i < out.size()) out.push(…)`）；
  ② 增长里还剩**被调方查不到的名字**（局部容器 / 被改写的标量）；
  ③ 出参被交给认不出的函数（界直接抹成 `None`）。

## 两类断言（只比表达式形状不算验证）

- **等价性守卫**：同一段逻辑的两种写法（出参填 vs 就地填）给**同一个尺寸上界**，
  且解释器跑出来的**返回值逐字节相同**；同一份代码算两遍 κ 也逐字节相同。
- **实测 ≤ 上界**：解释器真的跑一遍，`|out|` 的真实值 ≤ 上界、`ops ≤ κ`
  （每个规模都验，不是只看一眼表达式）。

跑法：

    cd tests/cases; $env:PYTHONIOENCODING="utf-8"; python test_kappa_out_param.py
"""

import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
for _p in (SRC, ROOT):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

from cost_check import compare, evaluate as kappa_at      # noqa: E402
from cost_interp import CostInterp                        # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset   # noqa: E402
from parser import parse_source                            # noqa: E402

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


#: 声明侧：只有一个真实契约（只为断言「判得出达成」），其余函数不参与判定。
TU = """spec sum_after_fill(const vector<u64>& xs) -> u64 {
    target { time: { max: O(xs.size()) }, allocation: { max: O(xs.size()) }, };
}
"""

#: 实现侧。**出参填容器**的五个形状 + 三条 ⊤ 边界。
CH = """void add_all(vector<u64>& out, const vector<u64>& xs) {
    for (auto& x : xs) { out.push(x); }
}

u64 sum_after_fill(const vector<u64>& xs) {
    vector<u64> out;
    add_all(out, xs);
    u64 s = 0;
    u64 i = 0;
    while (i < out.size()) { s = s + out.at(i); i = i + 1; }
    return s;
}

u64 sum_inline(const vector<u64>& xs) {
    vector<u64> out;
    for (auto& x : xs) { out.push(x); }
    u64 s = 0;
    u64 i = 0;
    while (i < out.size()) { s = s + out.at(i); i = i + 1; }
    return s;
}

u64 size_after_fill(const vector<u64>& xs) {
    vector<u64> out;
    add_all(out, xs);
    return out.size();
}

u64 fill_chain(const vector<u64>& xs) {
    vector<u64> a;
    add_all(a, xs);
    vector<u64> b;
    add_all(b, a);
    return b.size();
}

u64 grow_n(vector<u64>& out, u64 k) {
    u64 i = 0;
    while (i < k) { out.push(i); i = i + 1; }
}

u64 via_n_const(u64 unused) {
    vector<u64> out;
    grow_n(out, 5);
    return out.size();
}

u64 via_n_param(u64 k) {
    vector<u64> out;
    grow_n(out, k);
    return out.size();
}

// **在循环里调出参**：每层轮数必须乘进去（漏乘 = 低估）。
u64 via_loop(const vector<u64>& xs, u64 k) {
    vector<u64> out;
    u64 i = 0;
    while (i < k) {
        add_all(out, xs);
        i = i + 1;
    }
    return out.size();
}

// **靶子的形状**（08_bits_hash 的内核）：map 的键是序列（`vector<u8>` = string），
// 计数由出参完成，之后遍历它。
void count_words(const vector<vector<u8>>& words, map<vector<u8>,u64>& freq) {
    for (auto& w : words) { freq[w] = freq[w] + 1; }
}

u64 count_words_size(u64 unused) {
    vector<vector<u8>> cmds;
    cmds.push("get");
    cmds.push("put");
    cmds.push("get");
    cmds.push("del");
    cmds.push("get");
    cmds.push("put");
    map<vector<u8>,u64> freq;
    count_words(cmds, freq);
    return freq.size();
}

void fill_self(vector<u64>& out) {
    u64 i = 0;
    while (i < out.size()) { out.push(1); i = i + 1; }
}

u64 via_self(const vector<u64>& xs) {
    vector<u64> out;
    add_all(out, xs);
    fill_self(out);
    return out.size();
}
"""


def _ci():
    tu = parse_source(TU, "outp.tu").module
    ch = parse_source(CH, "outp.eln").module
    return tu, ch, CostInterp(ch, tu)


def _bounds(ci, fn_name):
    """局部容器 → 尺寸上界（`None` = 推不出）。"""
    fn = ci.fns[fn_name]
    b = ci._container_bounds_full(fn)[0]
    return {k: (None if v is None else v.render()) for k, v in b.items()}


def _run(fn_name, args):
    """解释器跑一次 → `(返回值, 该函数的 ops)`。"""
    tu = parse_source(TU, "outp.tu").module
    ch = parse_source(CH, "outp.eln").module
    ops_enable(True)
    try:
        ops_reset()
        val = Interp(ch, tu).call(fn_name, args)
        return val, ops_of(fn_name)
    finally:
        ops_enable(False)


def _growth(ci, fn_name):
    """被调方对**出参**的增长上界（`None` = 推不出）。"""
    fn = ci.fns[fn_name]
    g = ci._out_growth_impl(fn) or {}
    return {k: (None if v is None else v.render()) for k, v in g.items()}


# ---------------------------------------------------------------- ① 出参上界
def test_out_param_bound():
    print("[1] `add_all(out, xs)`：|out| ≤ n(xs)（调用点用实参的界实例化）")
    _tu, _ch, ci = _ci()
    b = _growth(ci, "add_all")
    check(b.get("out") == "n(xs)", "被调方：|out| 的增长是 `n(xs)`", str(b.get("out")))
    b2 = _bounds(ci, "sum_after_fill")
    check(b2.get("out") == "n(xs)", "调用点：|out| ≤ n(xs)（不是 ⊤）", str(b2.get("out")))
    k = ci.cost_of("sum_after_fill")
    text = k.render()
    check(k.kind != "unknown", "κ 不是 ⊤", text)
    check(set(k.variables()) == {"xs"},
          "κ 只含输入尺寸量 `xs`（没有 n(out) 这种局部名）", text)
    v = compare(k, ci.target_cost("sum_after_fill"))
    check(v.kind == "ok", f"`O(xs.size())` 判达成：{v.kind}", v.render())
    print()


# ---------------------------------------------------------------- ② 等价性守卫
def test_equivalence_guard():
    print("[2] 等价性守卫：出参填 vs 就地填 —— 同一个尺寸上界 + 同一份运行结果")
    _tu, _ch, ci = _ci()
    b_out = _bounds(ci, "sum_after_fill").get("out")
    b_in = _bounds(ci, "sum_inline").get("out")
    check(b_out == b_in == "n(xs)",
          "两种写法给**同一个**尺寸上界（与写法无关）", f"{b_out} vs {b_in}")
    # κ 的**尺寸量集合**也要一样（不能一种写法漏出局部名）
    k1 = ci.cost_of("sum_after_fill")
    k2 = ci.cost_of("sum_inline")
    check(set(k1.variables()) == set(k2.variables()),
          "两种写法的 κ 尺寸量集合相同", f"{k1.variables()} vs {k2.variables()}")
    # **同一份代码算两遍逐字节相同**（缓存必须幂等）
    _tu2, _ch2, ci2 = _ci()
    check(ci2.cost_of("sum_after_fill").render() == k1.render(),
          "同一份代码重新算一遍：κ 逐字节相同", k1.render())
    # **运行结果逐字节相同**（同逻辑，两种写法）
    for n in (1, 7, 64):
        xs = list(range(n))
        v1 = _run("sum_after_fill", [xs])[0]
        v2 = _run("sum_inline", [xs])[0]
        check(v1 == v2, f"n={n}: 两种写法返回值逐字节相同（{v1}）", f"{v1} vs {v2}")
    print()


# ---------------------------------------------------------------- ③ 实测 ≤ 上界
def test_measured_le_bound():
    print("[3] 实测 ≤ 上界：解释器真的跑一遍（不是只比表达式形状）")
    _tu, _ch, ci = _ci()
    k = ci.cost_of("size_after_fill")
    print(f"       κ(size_after_fill) = {k.render()}")
    for n in (1, 4, 64, 512):
        got, _ops = _run("size_after_fill", [list(range(n))])
        cap = kappa_at(k, {"xs": n})
        check(got <= cap,
              f"n={n}: 真实 |out| = {got} ≤ 上界 {cap:g}", f"|out|={got} κ={cap}")
    # **ops ≤ κ**：出参那一路的成本也要被 κ 覆盖
    for n in (1, 16, 256):
        got, ops = _run("sum_after_fill", [list(range(n))])
        cap = kappa_at(ci.cost_of("sum_after_fill"), {"xs": n})
        check(ops <= cap,
              f"n={n}: ops {ops} ≤ κ {cap:g}（出参那一路没漏记）",
              f"ops={ops} κ={cap} 返回 {got}")
    print()


# ---------------------------------------------------------------- ④ 可组合
def test_composable():
    print("[4] 可组合：`b` 的界由 `a` 决定，`a` 的界由形参决定（链到底）")
    _tu, _ch, ci = _ci()
    b = _bounds(ci, "fill_chain")
    check(b.get("a") == "n(xs)", "|a| ≤ n(xs)", str(b.get("a")))
    check(b.get("b") == "n(xs)", "|b| ≤ n(a) ≤ n(xs)", str(b.get("b")))
    k = ci.cost_of("fill_chain")
    check(set(k.variables()) == {"xs"}, "κ 只含 n(xs)", k.render())
    for n in (1, 9, 100):
        got, ops = _run("fill_chain", [list(range(n))])
        cap = kappa_at(k, {"xs": n})
        check(got <= cap and ops <= cap,
              f"n={n}: 真实 |b| = {got} ≤ {cap:g}，ops {ops} ≤ κ",
              f"|b|={got} ops={ops} κ={cap}")
    print()


# ---------------------------------------------------------------- ⑤ 标量形参增长
def test_scalar_growth():
    print("[5] 增长是标量形参：`grow_n(out, k)` 里 `out.push` 在 `while (i < k)` 里")
    _tu, _ch, ci = _ci()
    b = _growth(ci, "grow_n")
    check(b.get("out") == "k", "被调方：增长是 `k`（本函数自己的符号）",
          str(b.get("out")))
    check(_bounds(ci, "via_n_const").get("out") == "5",
          "调用点：字面量实参 ⇒ |out| ≤ 5（常数）",
          str(_bounds(ci, "via_n_const").get("out")))
    check(_bounds(ci, "via_n_param").get("out") == "k",
          "调用点：形参实参 ⇒ |out| ≤ k（同一帧的符号）",
          str(_bounds(ci, "via_n_param").get("out")))
    for k in (0, 5, 40):
        got, ops = _run("via_n_param", [k])
        cap = kappa_at(ci.cost_of("via_n_param"), {"k": k})
        check(got <= cap and ops <= cap,
              f"k={k}: 真实 |out| = {got} ≤ {cap:g}，ops {ops} ≤ κ",
              f"|out|={got} ops={ops} κ={cap}")
    print()


# ---------------------------------------------------------------- ⑦ 循环里的出参
def test_out_param_in_loop():
    print("[6] 在循环里调出参：轮数要乘进增长（`while (i < k) add_all(out, xs)`）")
    _tu, _ch, ci = _ci()
    b = _bounds(ci, "via_loop").get("out")
    check(b == "k×n(xs)", "|out| ≤ k×n(xs)（漏乘 factor 就是低估）", str(b))
    for k in (0, 1, 3, 20):
        got, ops = _run("via_loop", [list(range(4)), k])
        cap = kappa_at(ci.cost_of("via_loop"), {"xs": 4, "k": k})
        check(got <= cap and ops <= cap,
              f"k={k}: 真实 |out| = {got} ≤ {cap:g}，ops {ops} ≤ κ",
              f"|out|={got} ops={ops} κ={cap}")
    print()


# ---------------------------------------------------------------- ⑧ 靶子形状
def test_target_shape():
    print("[7] 靶子形状（`map<vector<u8>,u64>` 计数 + 出参填 + 遍历）：|freq| 与实测")
    _tu, _ch, ci = _ci()
    b = _bounds(ci, "count_words_size")
    check(b.get("freq") == "6",
          "|freq| ≤ 6（调用点用 |cmds| 实例化：出参增长 = n(words)）",
          str(b.get("freq")))
    check(b.get("cmds") == "6", "|cmds| ≤ 6（六个 push）", str(b.get("cmds")))
    got, ops = _run("count_words_size", [0])
    k = ci.cost_of("count_words_size")
    # **本地容器名要按真实值代入**：这条 κ 里还有一项 `n(cmds)`（既有 E29 的
    # 析构成本写法，不是本次引入），代入真实的 |cmds| = 6 才是这条式子的取值。
    cap = kappa_at(k, {"cmds": 6})
    check(got <= 6, f"实测真实 |freq| = {got} ≤ 上界 6", f"|freq|={got}")
    check(ops <= cap, f"ops {ops} ≤ κ {cap:g}", f"ops={ops} κ={cap}")
    print()


# ---------------------------------------------------------------- ⑥ ⊤ 边界
def test_top_boundaries():
    print("[8] ⊤ 边界：证明不了就 ⊤（三条，逐条钉住）")
    _tu, _ch, ci = _ci()
    # ① 增长依赖出参自己的当前尺寸
    check(_bounds(ci, "fill_self").get("out") is None,
          "① `while (i < out.size()) out.push(…)` ⇒ 增长 ⊤（两侧纠缠）",
          str(_bounds(ci, "fill_self").get("out")))
    k = ci.cost_of("via_self")
    check(k.is_top(), "调用点跟着 ⊤（不拿一个假的界顶上）", k.render())
    why = ci.top_reasons.get("via_self") or ""
    check("长度推不出" in why, "⊤ 带原因行", why)
    check("?" not in k.render(), "⊤ 的渲染里没有 `n(?)`", k.render())
    print()


def main():
    test_out_param_bound()
    test_equivalence_guard()
    test_measured_le_bound()
    test_composable()
    test_scalar_growth()
    test_out_param_in_loop()
    test_target_shape()
    test_top_boundaries()

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print(f"  - {f}")
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    _sys.exit(main())
