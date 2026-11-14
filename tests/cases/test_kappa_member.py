#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_member.py —— **成员容器的尺寸符号 + 标量成员当循环半径**（P0-1 ②，2026-11-12）

## 为什么要这份测试

`--compat` 把 `class` 降级成「第一个形参是对象」的自由函数：

    class QueenSolver { u64 n_; std::vector<u64> cols_; … };
    std::vector<std::string> render() const {
        for (u64 row = 0; row < n_; ++row) …            // ← n_ 是**成员**
    }
    ⇒  fn QueenSolver_render(recv: QueenSolver) {
           while (row < recv.n_) { … recv.first_[row] … }
       }

两件事以前都推不出、整条 κ 变 ⊤：

  ① **成员容器的尺寸**：`recv.first_.size()` / `recv.v[i]` 的尺寸量写不出来。
     现在渲染成 `n(recv.first_)`（字段链就是尺寸符号）。**对象相对**是关键：
     调用点 `solver.render()` 上那个对象叫 `solver`，符号必须是
     `n(solver.first_)` —— 留 `n(recv.first_)` 就是把**被调方的形参名**留在
     调用方的 κ 里，求值查不到 ⇒ 按 1 算 ⇒ 低估。`_receiver_size_renames`
     负责这一步换名（与 `_param_size_table` 的 E19 是同一件事，粒度到字段）。
  ② **标量成员当循环半径**：`while (row < recv.n_)` 的轮数写成 `n(recv.n_)`
     （"这个成员的取值"）。它比容器长度多**一条前提**：该字段在函数执行期间
     **不变**（条件每轮重读它）。两种"会变"都退回 ⊤：
       · 函数体里对该基对象的字段有写（`recv.n_ = …`）；
       · 基对象被**当实参/接收者传出去**（非 const 引用可能改它）。

## 与 `.tu` 的关系（"给了 sizes 之后能算"这条路）

`n(recv.n_)` 是**普通尺寸量**，所以 `.tu` 的 target 可以直接写 `O(s.n)`，
判定走既有的尺寸量配对（本文件 [3] 钉住这条：判 **达成**）。

## 两类断言

- **等价性**：`bsum(zz)` 与 `bsum(s)` 给同一个**形状**的界，只是对象名不同
  （`n(zz.v)` / `n(s.v)`）；同一份代码算两遍 κ 逐字节相同。
- **实测 ≤ 上界**：真的构造一个 `Bag` 跑一遍，返回值的解析式证明循环确实跑了
  `n` 轮，`ops(bn) ≤ κ(bn)`。

跑法：

    cd tests/cases; $env:PYTHONIOENCODING="utf-8"; python test_kappa_member.py
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


#: 声明侧：`bn` 的 target 用**成员尺寸量** `s.n` 写 —— 这正是"给了 .tu 之后能算"
#: 这条路（`n(s.n)` 与实现对得上，判达成）。
TU = """spec bn(Bag& s) -> u64 {
    target { time: { max: O(s.n) }, allocation: { max: O(1) }, };
}
"""

#: 实现侧。成员容器读 + 标量成员当半径 + 三个 ⊤ 边界。
CH = """struct Bag {
    n: u64,
    v: vector<u64>,
}

u64 bsum(Bag& s) {
    u64 t = 0;
    u64 i = 0;
    while (i < s.v.size()) { t = t + s.v[i]; i = i + 1; }
    return t;
}

u64 bsum_call(Bag& zz) { return bsum(zz); }

u64 bn(Bag& s) {
    u64 t = 0;
    u64 row = 0;
    while (row < s.n) { t = t + s.v[row]; row = row + 1; }
    return t;
}

u64 bn_call(Bag& zz) { return bn(zz); }

// **⊤ 边界 ①**：字段在本函数里被改写（条件每轮重读它 ⇒ 初值不是上界）。
u64 bn_mutated(Bag& s) {
    u64 t = 0;
    u64 row = 0;
    while (row < s.n) { s.n = s.n - 1; t = t + row; row = row + 1; }
    return t;
}

// **⊤ 边界 ②**：对象被当实参传出去（非 const 引用可能改它）。
u64 bn_passed(Bag& s) {
    u64 t = 0;
    u64 row = 0;
    while (row < s.n) { t = t + bsum(s); row = row + 1; }
    return t;
}

// 实测用：真的建一个 Bag 再调 bn（成员值 = n，v 长 n）。
u64 bag_run(u64 n) {
    Bag b;
    b.n = n;
    u64 i = 0;
    while (i < n) { b.v.push(i); i = i + 1; }
    return bn(b);
}
"""


def _parse():
    return parse_source(CH, "member.eln").module


def _ci():
    tu = parse_source(TU, "member.tu").module
    return tu, CostInterp(_parse(), tu)


def _run(fn_name, args, inner=None):
    """跑一次 → `(返回值, inner 的 ops)`（`inner=None` 时取 `fn_name` 自己）。"""
    tu = parse_source(TU, "member.tu").module
    ch = _parse()
    ops_enable(True)
    try:
        ops_reset()
        val = Interp(ch, tu).call(fn_name, args)
        return val, ops_of(inner or fn_name)
    finally:
        ops_enable(False)


# ---------------------------------------------------------------- ① 成员容器
def test_member_container_size():
    print("[1] 成员容器的尺寸：`s.v.size()` → `n(s.v)`，调用点换成实参那个对象")
    _tu, ci = _ci()
    k = ci.cost_of("bsum")
    print(f"       κ(bsum) = {k.render()}")
    check(set(k.variables()) == {"s.v"},
          "κ(bsum) 的尺寸量是 `s.v`（成员容器，可渲染）", str(k.variables()))
    check("n(s.v)" in k.render(), "κ 里出现 `n(s.v)`", k.render())
    kc = ci.cost_of("bsum_call")
    check(set(kc.variables()) == {"zz.v"},
          "调用点：`bsum(zz)` 的尺寸量换成 `zz.v`（对象相对的符号在调用点换名）",
          str(kc.variables()))
    check("n(s.v)" not in kc.render(),
          "调用点**没有**留下被调方的 `n(s.v)`（否则求值按 1 算 ⇒ 低估）",
          kc.render())
    print()


# ---------------------------------------------------------------- ② 标量成员
def test_scalar_member_radius():
    print("[2] 标量成员当循环半径：`while (row < s.n)` → 轮数 `n(s.n)`")
    _tu, ci = _ci()
    k = ci.cost_of("bn")
    print(f"       κ(bn) = {k.render()}")
    check(not k.is_top(), "κ(bn) 不是 ⊤", k.render())
    check(set(k.variables()) == {"s.n"},
          "尺寸量是 `s.n`（成员取值就写成尺寸符号）", str(k.variables()))
    kc = ci.cost_of("bn_call")
    print(f"       κ(bn_call) = {kc.render()}")
    check(set(kc.variables()) == {"zz.n"},
          "调用点：换成 `zz.n`（对象相对的符号在调用点换名）", str(kc.variables()))
    check("n(s.n)" not in kc.render(), "调用点没有留下 `n(s.n)`", kc.render())
    print()


# ---------------------------------------------------------------- ③ .tu 判定
def test_target_judged():
    print("[3] `.tu` 的 target 用 `O(s.n)` 写 ⇒ 判**达成**（这条路存在）")
    _tu, ci = _ci()
    k = ci.cost_of("bn")
    v = compare(k, ci.target_cost("bn"))
    check(v.kind == "ok", f"`O(s.n)` 判达成：{v.kind}", v.render())
    print()


# ---------------------------------------------------------------- ④ 等价性
def test_equivalence_guard():
    print("[4] 等价性守卫：同一写法换个对象名 —— 同一个形状，只换对象前缀")
    _tu, ci = _ci()
    a = ci.cost_of("bn").render()
    b = ci.cost_of("bn_call").render()
    # 形状相同（都只有一个尺寸量、都不是 ⊤、都没有泄漏），只是对象前缀不同
    check(("n(s.n)" in a) and ("n(zz.n)" in b),
          "两条 κ 各自用**自己那帧的对象名**写同一个尺寸量",
          f"{a}  vs  {b}")
    check(a.count("n(") == b.count("n("),
          "两条 κ 的尺寸量**出现次数**相同（同一个形状）", f"{a.count('n(')} vs {b.count('n(')}")
    # **同一份代码重新算一遍：κ 逐字节相同**（缓存必须幂等）
    _tu2, ci2 = _ci()
    check(ci2.cost_of("bn").render() == a, "重新算一遍：κ 逐字节相同", a)
    check(ci2.cost_of("bsum").render() == ci.cost_of("bsum").render(),
          "重新算一遍：κ(bsum) 逐字节相同", ci.cost_of("bsum").render())
    print()


# ---------------------------------------------------------------- ⑤ 实测
def test_measured_le_bound():
    print("[5] 实测 ≤ 上界：真的建一个 Bag 跑一遍（不是只比表达式形状）")
    _tu, ci = _ci()
    k = ci.cost_of("bn")
    print(f"       κ(bn) = {k.render()}")
    for n in (0, 1, 4, 16):
        got, ops = _run("bag_run", [n], inner="bn")
        want = n * (n - 1) // 2
        cap = kappa_at(k, {"s.n": n})
        check(got == want,
              f"n={n}: 返回值 {got} = 0+…+{n - 1}（循环**确实**跑了 n 轮）"
              if n else f"n={n}: 返回值 {got}（空循环，0 轮）",
              f"{got} vs {want}")
        check(ops <= cap,
              f"n={n}: ops(bn) {ops} ≤ κ {cap:g}", f"ops={ops} κ={cap}")
    print()


# ---------------------------------------------------------------- ⑥ ⊤ 边界
def test_top_boundaries():
    print("[6] ⊤ 边界：字段被改写 / 对象被传出去 ⇒ 不许当循环半径")
    _tu, ci = _ci()
    for fn, why in (("bn_mutated", "函数体里写了 `s.n`"),
                    ("bn_passed", "`s` 被当实参传给 `bsum(s)`")):
        k = ci.cost_of(fn)
        check(k.is_top(), f"{fn} ⇒ ⊤（{why}）", k.render())
        check("n(?)" not in k.render(), f"{fn} 的 ⊤ 里没有 `n(?)`", k.render())
    print()


def main():
    test_member_container_size()
    test_scalar_member_radius()
    test_target_judged()
    test_equivalence_guard()
    test_measured_le_bound()
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
