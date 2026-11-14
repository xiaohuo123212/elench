#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_kappa_insert_count.py —— 三参 `insert(pos, count, value)` 的 κ 必须**随 count 增长**

## 病是什么（2026-11-13，**上界被破**）

`src/interp.py` 这一轮才把三参 `insert` 的**语义**实现出来（以前把 `count`
静默丢掉、把位置当值）。但成本域仍按「一次 `insert` = +1 个元素」记：

    vector<u64> xs;
    xs.insert(0, n, 7);        // 真的插了 n 个 7（n=8→size 8，n=256→size 256）
    return xs.size();

    κ(修前) = 16（常数，与 n 无关）      ← n=256 时真实工作量 256 > 16

这违反本项目唯一一条底线：**κ 必须是上界**。同一个「+1」还出现在**增长预扫**
里，所以 `xs.insert(0,n,7)` 之后 `for (auto x : xs)` 的轮数也只按 1 圈算——
**第二次低估**（本次一并修）。

## 修法（`src/cost_domain/cost_interp.py`，三处消费同一个判据）

| 消费点 | 修前 | 修后 |
|---|---|---|
| `_k_expr_method_call`（**成本**） | 一次调用记常数 | `+ count`（每个元素构造 1 个操作） |
| `_scan_expr`（**容器尺寸上界**） | `|xs| += 1` | `|xs| += count` |
| `_grow_elem` / `_fg_call`（内层行 / 字段） | `+= 1` | `+= count` |

`count` 走 `_count_of_expr`（与 `for` 轮数、`assign` 的增长预扫**同一份**推导）：
字面量 / 形参 / `xs.size()` / `+ - * / % >>` 都认；**推不出 ⇒ ⊤**（MUST NOT 当 1）。
`assign(n, v)` / `resize(n)` 是同一族（带数量参数），一起修。

## 断言（九条）

1. `insert(0, n, 7)` 的 κ **随 n 增长**（修前恒为 16）；
2. n = 8 / 64 / 256：解释器 `ops ≤ κ`，且 `κ ≥ ops + n`（真的插了 n 个元素）；
3. `insert(0, 3, 7)` 的 κ 是常数，但 `≥ ops + 3`（常数 count 也不能少算元素）；
4. 两参 `insert(0, 7)` 的 κ **不引入数量项**（仍是常数、无尺寸符号）；
5. `assign(n, 7)` 同样随 n 增长（同类低估，语料 `10_backtrack` 的构造用了它）；
6. 数量推不出（`insert(0, f(), 7)`）⇒ 该项退 ⊤；
7. 增长上界也吃 count：`insert(0,n,7)` 之后 `for (x : xs)` 的 κ 随 n 增长；
8. 等价性：只改接收者名字（`xs` ↔ `ys`）→ κ 与 run（返回值 / ops）逐字节相同；
9. 元素是 owning 容器：`insert` 的 n 个元素是 **n 份深拷贝**（修前 κ 与 n 无关，
   真实 ops 已破界）。

跑法：

    python -B tests/cases/test_kappa_insert_count.py
"""

import contextlib
import io as _io
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
for _p in (SRC, CASEDIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                        # noqa: BLE001
    pass

from parser import parse_source                          # noqa: E402
from cost_interp import CostInterp                       # noqa: E402
from cost_check import evaluate                          # noqa: E402
import interp                                            # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset  # noqa: E402

FAILED = []


def check(cond, name, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


# ---------------------------------------------------------------- 工具

def _mod(src, name="probe.eln"):
    return parse_source(src, name).module


def kappa_of(src, fn, name="probe.eln"):
    ch = _mod(src, name)
    ci = CostInterp(ch, None, table_path=None)
    return ci.cost_of(fn), ch


def run_of(ch, fn, args):
    """跑一次，返回 `(stdout, 返回值 repr, ops 总数)`（`ops_of(None)` = 整棵树）。"""
    old = interp._COUNT
    interp.ops_enable(True)
    buf = _io.StringIO()
    try:
        ops_reset()
        it = Interp(ch, None, table_path=None)
        with contextlib.redirect_stdout(buf):
            val = it.call(fn, args)
        return buf.getvalue(), repr(val), ops_of(None)
    finally:
        interp.ops_enable(old)


def kappa_at(k, env):
    return evaluate(k, {v: env.get(v, 0) for v in k.variables()})


# ---------------------------------------------------------------- 探针程序

#: `count` 是**符号形参**：与父任务给的复现例逐字相同。
INSERT_SYM = """u64 f(u64 n) {
    vector<u64> xs;
    xs.insert(0, n, 7);
    return xs.size();
}
"""

#: 常数 count：κ 可以是常数，但不能少算这 3 个元素。
INSERT_CONST = """u64 f(u64 n) {
    vector<u64> xs;
    xs.insert(0, 3, 7);
    return xs.size();
}
"""

#: 两参重载：**没有**数量参数，κ 必须保持常数（守卫"数量项只对三参生效"）。
INSERT_TWO = """u64 f(u64 n) {
    vector<u64> xs;
    xs.insert(0, 7);
    return xs.size();
}
"""

#: `assign(n, v)`：同族（清空后填 n 个）——修前 κ 也是常数 14。
ASSIGN_SYM = """u64 f(u64 n) {
    vector<u64> xs;
    xs.assign(n, 7);
    return xs.size();
}
"""

#: 数量推不出：实参是**返回标量的调用**（不是尺寸量）⇒ 该项退 ⊤。
INSERT_UNKNOWN = """u64 c() {
    return 3;
}
u64 f(u64 n) {
    vector<u64> xs;
    xs.insert(0, c(), 7);
    return xs.size();
}
"""

#: **第二次低估**：`insert` 之后遍历同一个容器。增长预扫若按 +1 记，
#: `|xs| ≤ 1` ⇒ 循环轮数按 1 算 ⇒ κ 与 n 无关（实测修前 holds=False）。
INSERT_THEN_LOOP = """u64 f(u64 n) {
    vector<u64> xs;
    xs.insert(0, n, 7);
    u64 s = 0;
    for (auto x : xs) {
        s = s + x;
    }
    return s;
}
"""

#: 等价性：同逻辑、只改**接收者名字**（`xs` → `ys`）。
EQ_A = INSERT_SYM
EQ_B = """u64 f(u64 n) {
    vector<u64> ys;
    ys.insert(0, n, 7);
    return ys.size();
}
"""

#: 元素本身是 owning 容器：插 n 个元素 = **n 份深拷贝**，不是 1 份。
#: 修前 κ = `n(v) + 1 + 17`（与 n 无关）——n=8、n(v)=4 时真实 ops 已 52 > 22。
INSERT_NESTED = """u64 f(u64 n, vector<u64> v) {
    vector<vector<u64> > xs;
    xs.insert(0, n, v);
    return xs.size();
}
"""

SIZES = (8, 64, 256)


# ---------------------------------------------------------------- [1][2]

def test_symbolic_count():
    print("[1] `insert(0, n, 7)`：κ 必须**随 n 增长**（修前是常数 16）")
    k, ch = kappa_of(INSERT_SYM, "f")
    print(f"       κ(f) = {k.render()}")
    check("n" in set(k.variables()),
          "κ 的尺寸量里有 `n`（不再是一条常数式）", k.variables())
    vals = {n: kappa_at(k, {"n": n}) for n in SIZES}
    for n in SIZES:
        print(f"       n={n:3d}: κ = {vals[n]:,.0f}")
    check(vals[8] < vals[64] < vals[256],
          "κ(8) < κ(64) < κ(256)（严格增长）", vals)
    check(all(vals[n] >= n for n in SIZES),
          "每个 n 上 κ ≥ n（真的插了 n 个元素，不是 1 个）", vals)

    print("[2] 三个规模：解释器 ops ≤ κ，且 κ ≥ ops + n")
    for n in SIZES:
        _out, val, ops = run_of(ch, "f", [n])
        check(val == str(n), f"n={n}: 返回 size() = n（语义真的插了 n 个）", val)
        check(ops <= vals[n], f"n={n}: ops {ops} ≤ κ {vals[n]:,.0f}",
              f"ops={ops} κ={vals[n]}")
        check(vals[n] >= ops + n,
              f"n={n}: κ {vals[n]:,.0f} ≥ ops {ops} + n（n 次元素构造）",
              f"κ={vals[n]} ops={ops} n={n}")
    print()


# ---------------------------------------------------------------- [3][4]

def test_constant_count():
    print("[3] `insert(0, 3, 7)`：κ 可以是常数，但 ≥ ops + 3")
    k, ch = kappa_of(INSERT_CONST, "f")
    print(f"       κ(f) = {k.render()}")
    check(set(k.variables()) == set(), "常数 count 的 κ 没有尺寸符号", k.variables())
    vals = {n: kappa_at(k, {"n": n}) for n in SIZES}
    check(len(set(vals.values())) == 1, "三个规模 κ 相同（常数）", vals)
    _out, val, ops = run_of(ch, "f", [64])
    check(val == "3", "返回 3（真的插了 3 个）", val)
    kv = vals[64]
    check(ops <= kv, f"ops {ops} ≤ κ {kv:,.0f}", f"ops={ops} κ={kv}")
    check(kv >= ops + 3, f"κ {kv:,.0f} ≥ ops {ops} + 3", f"ops={ops} κ={kv}")
    print()


def test_two_arg_unchanged():
    print("[4] 两参 `insert(0, 7)`：κ 不引入数量项（仍是常数、无尺寸符号）")
    k, ch = kappa_of(INSERT_TWO, "f")
    print(f"       κ(f) = {k.render()}")
    check(set(k.variables()) == set(), "κ 没有尺寸符号", k.variables())
    vals = {n: kappa_at(k, {"n": n}) for n in SIZES}
    check(len(set(vals.values())) == 1, "三个规模 κ 相同", vals)
    _out, val, ops = run_of(ch, "f", [64])
    check(val == "1", "返回 1（两参只插 1 个）", val)
    check(ops <= vals[64], f"ops {ops} ≤ κ {vals[64]:,.0f}", ops)
    print()


# ---------------------------------------------------------------- [5][6][7]

def test_assign():
    print("[5] `assign(n, 7)`：同类低估，κ 也必须随 n 增长（修前常数 14）")
    k, ch = kappa_of(ASSIGN_SYM, "f")
    print(f"       κ(f) = {k.render()}")
    check("n" in set(k.variables()),
          "κ 的尺寸量里有 `n`", k.variables())
    vals = {n: kappa_at(k, {"n": n}) for n in SIZES}
    for n in SIZES:
        _out, val, ops = run_of(ch, "f", [n])
        check(val == str(n), f"n={n}: 返回 n", val)
        check(ops <= vals[n], f"n={n}: ops {ops} ≤ κ {vals[n]:,.0f}", ops)
        check(vals[n] >= ops + n,
              f"n={n}: κ {vals[n]:,.0f} ≥ ops {ops} + n", f"κ={vals[n]}")
    check(vals[8] < vals[64] < vals[256], "κ 严格随 n 增长", vals)
    print()


def test_unknown_count_is_top():
    print("[6] 数量推不出（`insert(0, c(), 7)`）⇒ 该项退 ⊤，不许当 1")
    k, _ch = kappa_of(INSERT_UNKNOWN, "f")
    print(f"       κ(f) = {k.render()}")
    check(k.is_top(), "κ 是 ⊤", k.render())
    print()


def test_growth_bound():
    print("[7] 增长上界也吃 count：`insert(0,n,7)` 之后 `for (x : xs)` 的 κ 随 n 增长")
    k, ch = kappa_of(INSERT_THEN_LOOP, "f")
    print(f"       κ(f) = {k.render()}")
    vals = {n: kappa_at(k, {"n": n}) for n in SIZES}
    for n in SIZES:
        _out, val, ops = run_of(ch, "f", [n])
        check(val == str(7 * n), f"n={n}: 返回值 = 7n（真的遍历了 n 个元素）", val)
        check(ops <= vals[n], f"n={n}: ops {ops} ≤ κ {vals[n]:,.0f}",
              f"ops={ops} κ={vals[n]}")
    check(vals[8] < vals[64] < vals[256],
          "κ 严格随 n 增长（修前这条是常数、且 ops 破界）", vals)
    print()


# ---------------------------------------------------------------- [8]

def test_equivalence():
    print("[8] 等价性：只改接收者名字（`xs` ↔ `ys`）→ κ 与 run 逐字节相同")
    ka, cha = kappa_of(EQ_A, "f", "a.eln")
    kb, chb = kappa_of(EQ_B, "f", "b.eln")
    print(f"       κ(xs) = {ka.render()}")
    print(f"       κ(ys) = {kb.render()}")
    check(ka.render() == kb.render(), "两条 κ 逐字节相同",
          f"{ka.render()}  vs  {kb.render()}")
    ra = run_of(cha, "f", [64])
    rb = run_of(chb, "f", [64])
    check(ra == rb, "run（返回值 / ops / stdout）逐字节相同", f"{ra} vs {rb}")
    print()


def test_nested_elements():
    print("[9] 元素是 owning 容器：n 个元素 = **n 份深拷贝**（修前 κ 与 n 无关）")
    k, ch = kappa_of(INSERT_NESTED, "f")
    print(f"       κ(f) = {k.render()}")
    vals = {}
    for n in SIZES:
        vals[n] = kappa_at(k, {"n": n, "v": 4})
        _out, val, ops = run_of(ch, "f", [n, [0] * 4])
        check(val == str(n), f"n={n}: 返回 n（插了 n 个元素）", val)
        check(ops <= vals[n], f"n={n}: ops {ops} ≤ κ {vals[n]:,.0f}",
              f"ops={ops} κ={vals[n]}")
        check(vals[n] >= ops + n,
              f"n={n}: κ {vals[n]:,.0f} ≥ ops {ops} + n", f"κ={vals[n]}")
    check(vals[8] < vals[64] < vals[256], "κ 严格随 n 增长", vals)
    print()


if __name__ == "__main__":
    test_symbolic_count()
    test_constant_count()
    test_two_arg_unchanged()
    test_assign()
    test_unknown_count_is_top()
    test_growth_bound()
    test_equivalence()
    test_nested_elements()
    print()
    if FAILED:
        print(f"> 失败 {len(FAILED)} 项")
        for x in FAILED:
            print(f"  - {x}")
        sys.exit(1)
    print("> 全部通过")
