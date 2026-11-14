#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_tree_depth.py —— **树递归的激活数 = 分支数^声明深度**（④，2026-11-12）

## 为什么要有这份测试

`demo/corpus/10_backtrack.cpp` 的 `QueenSolver::place` 是**树递归**：

    void place(u64 row) {
        if (row == n_) { count_ += 1; return; }
        for (u64 col = 0; col < n_; ++col) {      // ← 每层至多分叉 n 次
            if (cols_[col] != 0) continue;
            …
            place(row + 1);                        // ← 自调用落在 for 里
        }
    }

`.tu` 已经声明 `recursion depth <= 8`（`check` 的硬要求，运行期也真的会拦）。
修前 κ = ⊤：规则只认三种形状（声明深度单独 / visited 标记 / 区间对半分），
而 `place` 是"深度有界、分支数也不超过 n"——两个界都在，只是没人把它们乘起来。

## 判据（缺一即 ⊤，实现见 `cost_interp._tree_activation_bound`）

    ① `.tu` **显式**声明 `recursion depth <= D`，D 是**字面量**（不信 `repeats` 那条退路）；
    ② 每个自调用点都在若干层循环的**体**里，且每层循环的**轮数上界可推**
       （`_for_count` / `bound_of_condition_wrapper`，与 `_while_inner` 同一份）；
    ③ 分支数 B = 各层轮数之**积**；多个自调用点 ⇒ 各点的 B **求和**；
    ④ 每个自调用点把同一个标量形参按**恰好 +1** 推进（`row + 1`），体内不改写它。

    ⇒ 激活数 ≤ Σ_{k=0..D} B^k ≤ (D+1)·B^D
    ⇒ κ = (D+1)·B^D × 单层成本 + 尾部（与线性递归同一个合并公式，只是
       "深度"换成了"整棵树的激活数"）

`(D+1)` 那个余量就是等比数列的上界；有了它，指数里的 D 是**字面量常数**，
`Cost.pow(B, D)` 用整指数就够——不需要符号指数。

## 两类断言（用户定的口径）

- **等价性**：`place(row + 1)` 与 `place(1 + row)` 两种写法 ⇒ **κ 逐字节相同**、
  run 的返回值与 ops **逐字节相同**；同一份代码算两遍也逐字节相同。
- **实测 ≤ 上界**：解释器真跑 n=4/6/8（8 皇后的剪枝搜索树，激活数只有 2057，
  全分叉树在 n=8 会是 1,900 万次调用——测不了），返回的**真实激活数**
  ≤ `(D+1)·B^D`，且 `ops ≤ κ`。

## 拒绝形状（各一条）

没有 `recursion depth` / 自调用在轮数推不出的循环里（`while (c)`）/
分支数推不出（字段被体内改写）/ 深度用 `row + 2` 推进 /
`recursion depth` 不是字面量 —— 都必须"推不出"（⊤ 或"成环"）。

跑法：

    cd tests/cases; $env:PYTHONIOENCODING="utf-8"; python test_kappa_tree_depth.py
"""

import io as _io
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

import compat as _compat                                  # noqa: E402
from cost_interp import CostError, CostInterp              # noqa: E402
from cost_check import evaluate as kappa_at                # noqa: E402
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


def _ci(tu_src, ch_src):
    tu = parse_source(tu_src, "tree.tu").module
    ch = parse_source(ch_src, "tree.eln").module
    return tu, CostInterp(ch, tu)


def _run(tu_src, ch_src, fn, args):
    """跑一次 → `(返回值, ops)`。"""
    tu = parse_source(tu_src, "tree.tu").module
    ch = parse_source(ch_src, "tree.eln").module
    ops_enable(True)
    try:
        ops_reset()
        val = Interp(ch, tu).call(fn, args)
        return val, ops_of(fn)
    finally:
        ops_enable(False)


# ---------------------------------------------------------------- 语料：place
CORPUS = _os.path.join(ROOT, "demo", "corpus")


def _corpus():
    """`demo/corpus/10_backtrack` 的 `(tu, ch)`（`.cpp` 走 `--compat`）。"""
    _compat.set_enabled(True)
    try:
        tu = parse_source(_io.open(_os.path.join(CORPUS, "10_backtrack.tu"),
                                   encoding="utf-8").read(), "10_backtrack.tu").module
        ch = parse_source(_io.open(_os.path.join(CORPUS, "10_backtrack.cpp"),
                                   encoding="utf-8").read(),
                          "10_backtrack.cpp").module
    finally:
        _compat.set_enabled(False)
    return tu, ch


def test_corpus_place():
    print("[1] 语料 `10_backtrack`：`place` 从 ⊤ 变成 `9×n(recv.n_)^8×…`（solve/main 跟着下来）")
    tu, ch = _corpus()
    ci = CostInterp(ch, tu)
    k = ci.cost_of("QueenSolver_place")
    print(f"       κ(place) = {k.render()}")
    check(not k.is_top(), "κ(place) 不是 ⊤", k.render())
    check("^8" in k.render(), "κ(place) 含 8 次幂（D=8 走 `Cost.pow` 的整指数）",
          k.render())
    check("9×" in k.render(), "κ(place) 含 `(D+1) = 9` 那个等比数列余量",
          k.render())
    check(set(k.variables()) == {"recv.n_"},
          "尺寸量是 `recv.n_`（分支数 B = 循环 `col < recv.n_` 的轮数）",
          str(k.variables()))
    # **调用方**：`solve` 调 `place(0)`，`main` 调 `solver.solve()`。
    ks = ci.cost_of("QueenSolver_solve")
    print(f"       κ(solve) = {ks.render()}")
    check(not ks.is_top(), "κ(solve) 不是 ⊤（跟着 place 下来）", ks.render())
    km = ci.cost_of("main")
    print(f"       κ(main)  = {km.render()[:120]}…")
    check(not km.is_top(), "κ(main) 不是 ⊤（接收者换名 `recv.n_` → `solver.n_`）",
          km.render())
    check(set(km.variables()) == {"solver.n_"},
          "κ(main) 的尺寸量是 `solver.n_`（没留下被调方的 `recv.n_`）",
          str(km.variables()))
    # **单调性**：κ 只会因为这一条变紧，但仍是上界 ⇒ 至少不能比修前更小。
    # 修前是 ⊤（无穷），这里就只断言"有限"。
    for n in (6, 8):
        cap = kappa_at(k, {"recv.n_": n})
        check(cap == cap and cap != float("inf"),
              f"n={n}: κ(place) = {cap:g}（有限）", str(cap))
    print()


# ---------------------------------------------------------------- 合成 tree 递归
#: 判据 ①：**显式**的 `recursion depth <= 8`（字面量）。
TU_TREE = """
spec qwalk(u64 n, u64 row) -> u64 {
    effects {};
    recursion depth <= 8;
}
spec qwalk_zz(u64 m, u64 row) -> u64 {
    effects {};
    recursion depth <= 8;
}
"""

#: 8 皇后剪枝版树递归：**返回真实激活数**（`s = 1` 记自己，`+ 子树的激活数`）。
#: 形状与 `place` 一致：每层 `while (col < n)` 分叉 n 次、`row + 1` 推进一层。
#: 剪枝（`!= 0` + 不标记入口）保证既跑得动 n=8，又**不**会被 ② 的 visited
#: 判据误认（那条要求 `== 0` 守卫 + 入口即标记）。
CH_TREE = """
u64 qwalk(u64 n, u64 row) {
    if (row == n) { return 1; }
    u64 s = 1;
    u64 col = 0;
    while (col < n) {
        s = s + qwalk(n, row + 1);
        col = col + 1;
    }
    return s;
}

u64 qwalk_zz(u64 m, u64 row) {
    if (row == m) { return 1; }
    u64 s = 1;
    u64 col = 0;
    while (col < m) {
        s = s + qwalk_zz(m, row + 1);
        col = col + 1;
    }
    return s;
}
"""

#: 有剪枝的版本（实测用）：真跑 8 皇后，激活数 = 搜索树的结点数。
CH_QUEENS = """
u64 qwalk(u64 n, u64 row, vector<u64>& cols, vector<u64>& d1, vector<u64>& d2) {
    if (row == n) { return 1; }
    u64 s = 1;
    u64 col = 0;
    while (col < n) {
        u64 a = row + col;
        u64 b = row + n - 1 - col;
        if (cols[col] == 0) {
            if (d1[a] == 0) {
                if (d2[b] == 0) {
                    cols[col] = 1;
                    d1[a] = 1;
                    d2[b] = 1;
                    s = s + qwalk(n, row + 1, cols, d1, d2);
                    cols[col] = 0;
                    d1[a] = 0;
                    d2[b] = 0;
                }
            }
        }
        col = col + 1;
    }
    return s;
}
"""

TU_QUEENS = """
spec qwalk(u64 n, u64 row, vector<u64>& cols, vector<u64>& d1, vector<u64>& d2) -> u64 {
    effects {};
    recursion depth <= 8;
}
"""

#: 声明深度（`.tu`）与分支数（`while (col < n)` 的轮数 = n）
D_DECL = 8
B_OF = lambda n: n                                    # noqa: E731


def test_shape():
    print("[2] 合成 tree 递归：κ = (D+1)·B^D × 单层 + 尾，B = n、D = 8")
    _tu, ci = _ci(TU_TREE, CH_TREE)
    k = ci.cost_of("qwalk")
    print(f"       κ(qwalk) = {k.render()}")
    check(not k.is_top(), "κ 不是 ⊤", k.render())
    check("9×n^8×" in k.render(), "κ 含 `9×n^8×…`（(D+1)·B^D 的乘数）", k.render())
    check(set(k.variables()) == {"n"}, "尺寸量集合是 {n}（B 就是形参 n）",
          str(k.variables()))
    # 不同规模上都有限、且随 n 增长（8 次幂项在支配）
    vals = [kappa_at(k, {"n": n}) for n in (4, 6, 8)]
    print(f"       κ(4)/κ(6)/κ(8) = " + " / ".join(f"{v:.4g}" for v in vals))
    check(vals[0] < vals[1] < vals[2], "n 变大 ⇒ κ 严格增长（8 次幂在支配）",
          str(vals))
    # **等价性**：换个形参名（`n` → `m`）⇒ 同一个形状，只是符号名不同
    kz = ci.cost_of("qwalk_zz")
    check(not kz.is_top() and set(kz.variables()) == {"m"},
          "换个形参名 ⇒ 同一个形状，尺寸量换成 {m}", kz.render())
    check(k.render().replace("n", "m") == kz.render(),
          "两条 κ 逐字节同形（只差符号名 n→m）", kz.render())
    print()


def test_equivalence():
    print("[3] 等价性：`row + 1` vs `1 + row` ⇒ κ 与 run 逐字节相同")
    tu = TU_TREE
    src_a = CH_TREE
    src_b = CH_TREE.replace("qwalk(n, row + 1)", "qwalk(n, 1 + row)")
    check(src_a != src_b, "两种写法确实是两份不同的源码")
    _t, ca = _ci(tu, src_a)
    _t2, cb = _ci(tu, src_b)
    ra = ca.cost_of("qwalk").render()
    rb = cb.cost_of("qwalk").render()
    print(f"       κ(row + 1) = {ra}")
    print(f"       κ(1 + row) = {rb}")
    check(ra == rb, "两种写法 κ **逐字节相同**", f"{ra}  vs  {rb}")
    # **同一份代码算两遍**：缓存必须幂等
    _t3, cc = _ci(tu, src_a)
    check(cc.cost_of("qwalk").render() == ra, "同一份代码算两遍：κ 逐字节相同", ra)
    # run：返回值（真实激活数）与 ops 都逐字节相同
    for n in (3, 4):
        va, oa = _run(tu, src_a, "qwalk", [n, 0])
        vb, ob = _run(tu, src_b, "qwalk", [n, 0])
        check(va == vb and oa == ob,
              f"n={n}: 两种写法 run 逐字节相同（返回值 {va}、ops {oa}）",
              f"{va}/{oa} vs {vb}/{ob}")
    print()


def test_measured():
    print("[4] 实测 ≤ 上界：真实激活数 vs (D+1)·B^D，n = 4/6/8")
    _tu, ci = _ci(TU_QUEENS, CH_QUEENS)
    k = ci.cost_of("qwalk")
    print(f"       κ(qwalk) = {k.render()[:100]}…")
    check(not k.is_top(), "剪枝版的 κ 也不是 ⊤（形状与全分叉版一致）", k.render())
    check("9×n^8×" in k.render(), "κ 含 `9×n^8×…`（B = n、D = 8）", k.render())
    for n in (4, 6, 8):
        acts, ops = _run(TU_QUEENS, CH_QUEENS, "qwalk",
                         [n, 0, [0] * n, [0] * (2 * n), [0] * (2 * n)])
        cap = (D_DECL + 1) * (B_OF(n) ** D_DECL)       # (D+1)·B^D
        kaps = kappa_at(k, {"n": n})
        print(f"       n={n}: 真实激活数 {acts:>6} ≤ (D+1)·B^D {cap:>12}   "
              f"ops {ops:>9} ≤ κ {kaps:.4g}")
        check(acts <= cap, f"n={n}: 真实激活数 {acts} ≤ (D+1)·B^D = {cap}",
              f"{acts} > {cap}")
        check(ops <= kaps, f"n={n}: ops {ops} ≤ κ {kaps:.4g}", f"{ops} > {kaps}")
    print()


# ---------------------------------------------------------------- 拒绝形状
def _rejected(tu_src, ch_src, fn):
    """`(是否推不出, 证据)`：推不出 = κ 是 ⊤ **或** `cost_of` 报"成环"。"""
    try:
        _tu, ci = _ci(tu_src, ch_src)
        k = ci.cost_of(fn)
        return k.is_top(), k.render()
    except CostError as exc:
        return ("成环" in str(exc)), str(exc)


#: ① 没有 `recursion depth`：不许猜深度（修前的保守处理：内联成环 / ⊤）。
NO_DEPTH_TU = """
spec nodepth(u64 n, u64 row) -> u64 { effects {}; }
"""
NO_DEPTH_CH = """
u64 nodepth(u64 n, u64 row) {
    if (row == n) { return 1; }
    u64 s = 1;
    u64 col = 0;
    while (col < n) { s = s + nodepth(n, row + 1); col = col + 1; }
    return s;
}
"""

#: ② 自调用在**轮数推不出**的循环里：`while (c)` 的体不推进任何计数器（E21）。
BAD_LOOP_TU = """
spec wloop(u64 n, u64 row, u64 c) -> u64 {
    effects {};
    recursion depth <= 8;
}
"""
BAD_LOOP_CH = """
u64 wloop(u64 n, u64 row, u64 c) {
    if (row == n) { return 1; }
    u64 s = 1;
    while (c) { s = s + wloop(n, row + 1, c); }
    return s;
}
"""

#: ③ **分支数推不出**：循环半径是成员 `b.n`，而它在体内被改写
#: （条件每轮重读它 ⇒ 进入时的值不是上界）。
BAD_BRANCH_TU = """
struct Bag { n: u64, v: vector<u64>, }
spec bagwalk(Bag& b, u64 row) -> u64 {
    effects {};
    recursion depth <= 8;
}
"""
BAD_BRANCH_CH = """
u64 bagwalk(Bag& b, u64 row) {
    if (row == b.n) { return 1; }
    u64 s = 1;
    u64 col = 0;
    while (col < b.n) {
        b.n = b.n - 1;
        s = s + bagwalk(b, row + 1);
        col = col + 1;
    }
    return s;
}
"""

#: ④ 深度用 `row + 2` 推进：层数与声明深度 D 的对应关系说不清。
STEP2_TU = """
spec step2(u64 n, u64 row) -> u64 {
    effects {};
    recursion depth <= 8;
}
"""
STEP2_CH = """
u64 step2(u64 n, u64 row) {
    if (row >= n) { return 1; }
    u64 s = 1;
    u64 col = 0;
    while (col < n) { s = s + step2(n, row + 2); col = col + 1; }
    return s;
}
"""

#: ⑤ `recursion depth` **不是字面量**（`<= n` 是形参）：深度无法静态定值。
SYM_DEPTH_TU = """
spec symdepth(u64 n, u64 row) -> u64 {
    effects {};
    recursion depth <= n;
}
"""
SYM_DEPTH_CH = """
u64 symdepth(u64 n, u64 row) {
    if (row == n) { return 1; }
    u64 s = 1;
    u64 col = 0;
    while (col < n) { s = s + symdepth(n, row + 1); col = col + 1; }
    return s;
}
"""


def test_rejects():
    print("[5] 拒绝形状：判据缺一即「推不出」（各一条）")
    cases = [
        ("没有 `recursion depth` 声明", NO_DEPTH_TU, NO_DEPTH_CH, "nodepth"),
        ("自调用在**轮数推不出**的循环里（`while (c)`）", BAD_LOOP_TU,
         BAD_LOOP_CH, "wloop"),
        ("分支数推不出（循环半径 `b.n` 在体内被改写）", BAD_BRANCH_TU,
         BAD_BRANCH_CH, "bagwalk"),
        ("深度用 `row + 2` 推进（不是 +1）", STEP2_TU, STEP2_CH, "step2"),
        ("`recursion depth <= n` 不是字面量", SYM_DEPTH_TU, SYM_DEPTH_CH,
         "symdepth"),
    ]
    for label, tu_s, ch_s, fn in cases:
        bad, why = _rejected(tu_s, ch_s, fn)
        check(bad, f"{label} ⇒ 推不出", why)
        check("^8" not in str(why), f"{label} 的结论里没有 ④ 的 8 次幂", str(why))
    print()


# ---------------------------------------------------------------- 语料 ⊤ 数
def _corpus_tops():
    """`demo/corpus` 里**有 `.tu`**的模块：`(文件数, 函数数, ⊤ 的清单)`。"""
    names = sorted(x for x in _os.listdir(CORPUS) if x.endswith(".cpp"))
    nf = 0
    tops = []
    for x in names:
        cp = _os.path.join(CORPUS, x)
        tp = cp[:-4] + ".tu"
        if not _os.path.isfile(tp):
            continue
        _compat.set_enabled(True)
        try:
            tu = parse_source(_io.open(tp, encoding="utf-8").read(),
                              _os.path.basename(tp)).module
            ch = parse_source(_io.open(cp, encoding="utf-8").read(), x).module
        finally:
            _compat.set_enabled(False)
        ci = CostInterp(ch, tu)
        for fn in sorted(ci.fns):
            nf += 1
            try:
                if ci.cost_of(fn).is_top():
                    tops.append(f"{x}:{fn}")
            except CostError:
                tops.append(f"{x}:{fn}")
    return nf, tops


def test_corpus_tops():
    print("[6] KPI：语料里 κ 还是 ⊤ 的函数（修前 3 个，全在 10_backtrack）")
    nf, tops = _corpus_tops()
    print(f"       函数总数 {nf}，⊤ 的 {len(tops)} 个：{tops}")
    check(not tops, f"语料 {nf} 个函数里 ⊤ 的个数 = 0（修前 3）", str(tops))
    print()


def main():
    test_corpus_place()
    test_shape()
    test_equivalence()
    test_measured()
    test_rejects()
    test_corpus_tops()

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
