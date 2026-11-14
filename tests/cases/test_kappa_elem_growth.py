#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_elem_growth.py —— 局部嵌套容器的**元素长度上界** `L(x)`（2026-11-12 晚）

## 为什么要这份测试

语料 `demo/corpus/12_graph.cpp` 里唯一剩下一个「缺分析」的 ⊤：

    Graph g = make_graph(8);      // n(g)=8，每行空 ⇒ L(g)=0
    add_edge(g, 0, 1);            // g[u].push_back(v) ⇒ 某个内层 +1
    … 共 7 次 add_edge …
    count_components(g);          // κ 里有 L(g)，调用点上换不出来 ⇒ main ⊤

`L(x) := max_i n(x[i])`（内层长度的上界）。`L(g)` 与 `n(g)` **无关**：
`vector<vector<u64>>` 里每行可以比行数长。所以「`n(g)` 就是 `g[v].size()` 的
上界」是错的（反证在 `test_kappa_elem_and_offset.py` [2]），缺的是在调用点把
`L(g)` 推成一个**具体的上界**。

## 四条来源（本文件逐个钉住）

| # | 构造 | `L(x)` 的上界 | 为什么可证 |
|---|---|---|---|
| ① | `x = make_graph(n)` / `Graph x(n, empty)` | 被调函数返回值的 L / `n(empty)` | 每行是同一个空行的拷贝 ⇒ L=0 |
| ② | 循环里的 `x[u].push_back(v)` | 初始 L + 轮数 × 每次 +1 | 每轮至多让某一内层 +1 |
| ③ | `x[i] = row` / `x.push_back(row)` | `max(L(x), n(row))` | 整行赋值/入行：该行长度 ≤ n(row) |
| ④ | `x[i] = src[j]`（`src` 是 **const** 形参） | `max(L(x), L(src))` | 元素来自另一个容器的元素 ⇒ 长度 ≤ 该容器的 `L`（传递；2026-11-12 夜） |
| ⑤ | 其余（`src` 非 const / 别名改写 / 认不出的调用…） | **⊤（None）** | 不猜 |

## 判据（本文件钉的就是它）

1. **κ 是上界**：解释器真的跑，`ops ≤ κ`（不是"形状对就算过"）。
2. **实测真实值 ≤ 上界**：解释器返回真实的 `g`，取 `max_i n(g[i])`，在 **≥3 个
   规模** 上与推出的界比。只比形状不算验证。
3. **等价性**：同一逻辑的两种写法 κ **逐字节相同**、`run` 输出**逐字节相同**。
4. **推不出就 ⊤**：`g[0] = src[0]`（元素的行长没有名字）⇒ 界是 None、κ 是 ⊤。

跑法：

    cd tests/cases; $env:PYTHONIOENCODING="utf-8"; python test_kappa_elem_growth.py
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
from cost_interp import CostInterp                       # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset  # noqa: E402
from parser import parse_source                          # noqa: E402

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


# ---------------------------------------------------------------- 声明侧
TU = """spec scan_from(const vector<vector<u64>>& g, u64 v) -> u64 {
    target { time: { max: O(L(g)) }, allocation: { max: 0 }, };
}
"""

#: 实现侧：与 `12_graph.cpp` 的 `make_graph` / `add_edge` / `main` 同形。
#: `main_like` 走**被调函数**加边（增长从调用点记），`main_direct` 直接在
#: `main` 里 `g[u].push_back(v)`（增长从方法调用记）——两种写法应当推出同一个
#: `L(g)` 上界、跑出同一个返回值。
CH = """vector<vector<u64>> make_graph(u64 n) {
    vector<u64> empty;
    vector<vector<u64>> g = vector<vector<u64>>::filled(empty, n);
    return g;
}

void add_edge(vector<vector<u64>>& g, u64 u, u64 v) {
    g[u].push_back(v);
}

u64 scan_from(const vector<vector<u64>>& g, u64 v) {
    u64 k = 0;
    u64 s = 0;
    while (k < g[v].size()) { s = s + g[v][k]; k = k + 1; }
    return s;
}

u64 scan_from_alt(const vector<vector<u64>>& g, u64 v) {
    u64 k = 0;
    u64 s = 0;
    while (g[v].size() > k) { s = s + g[v][k]; k = k + 1; }
    return s;
}

u64 main_like() {
    vector<vector<u64>> g = make_graph(8);
    add_edge(g, 0, 1);
    add_edge(g, 0, 2);
    add_edge(g, 1, 3);
    add_edge(g, 2, 3);
    add_edge(g, 3, 4);
    add_edge(g, 5, 6);
    add_edge(g, 6, 5);
    return scan_from(g, 0) + scan_from(g, 5);
}

u64 main_direct() {
    vector<vector<u64>> g = make_graph(8);
    g[0].push_back(1);
    g[0].push_back(2);
    g[1].push_back(3);
    g[2].push_back(3);
    g[3].push_back(4);
    g[5].push_back(6);
    g[6].push_back(5);
    return scan_from(g, 0) + scan_from(g, 5);
}

vector<vector<u64>> build_star(u64 n) {
    vector<vector<u64>> g = make_graph(n);
    u64 i = 1;
    while (i < n) { add_edge(g, 0, i); i = i + 1; }
    return g;
}

vector<vector<u64>> build_assign(u64 n) {
    vector<vector<u64>> g = make_graph(n);
    vector<u64> row;
    u64 i = 0;
    while (i < n) { row.push_back(i); i = i + 1; }
    g[0] = row;
    return g;
}

u64 from_elem_rows(const vector<vector<u64>>& src) {
    vector<vector<u64>> g = make_graph(2);
    g[0] = src[0];
    return g.size();
}

u64 unknown_rows(vector<vector<u64>>& src) {
    vector<vector<u64>> g = make_graph(2);
    g[0] = src[0];
    return g.size();
}
"""


def _mods():
    tu = parse_source(TU, "elem_growth.tu").module
    ch = parse_source(CH, "elem_growth.eln").module
    return tu, ch, CostInterp(ch, tu)


def _elem_bound(ci, fn_name, name="g"):
    """局部嵌套容器 → `L` 上界（`None` = 推不出）。"""
    eb, _ln = ci._elem_bounds_full(ci.fns[fn_name])
    v = eb.get(name)
    return None if v is None else v.render()


def _run(fn_name, args):
    """解释器跑一次，返回 `(返回值, 该函数的 ops)`。"""
    tu, ch, _ci = _mods()
    ops_enable(True)
    try:
        ops_reset()
        val = Interp(ch, tu).call(fn_name, args)
        return val, ops_of(fn_name)
    finally:
        ops_enable(False)


# ---------------------------------------------------------------- ① 返回空行
def test_make_graph_zero():
    print("[1] ① `make_graph(n)`：n 行空行 ⇒ L(g) = 0（不是 n(g)）")
    _tu, _ch, ci = _mods()
    check(_elem_bound(ci, "make_graph") == "0",
          "make_graph 的局部 `g`：L(g) = 0", str(_elem_bound(ci, "make_graph")))
    # `Graph g(8, empty)` 与 `make_graph(8)` 是同一个构造（前者在 main 里直接写）。
    check(_elem_bound(ci, "main_like") == "7",
          "main_like 的 `g`：L(g) = 7（0 + 7 次 push）",
          str(_elem_bound(ci, "main_like")))
    check(_elem_bound(ci, "main_direct") == "7",
          "main_direct 的 `g`：L(g) = 7（同一份界）",
          str(_elem_bound(ci, "main_direct")))


# ---------------------------------------------------------------- ② 12_graph.main
def test_main_becomes_concrete():
    print("[2] ② `12_graph.main` 同形：κ 从 ⊤ 变成具体式，且不含 L(g) / n(局部)")
    _tu, _ch, ci = _mods()
    k = ci.cost_of("main_like")
    text = k.render()
    check(k.kind != "unknown", "main_like 的 κ 不是 ⊤", text)
    check("L(" not in text, "κ 里没有 `L(...)`（已在调用点换成 7）", text)
    check("n(g)" not in text, "κ 里没有局部名 `n(g)`（析构项也换成 8）", text)
    check(k.variables() == [], "κ 的尺寸量集合为空（全是常数）", str(k.variables()))
    check(k.const_value() is not None,
          f"κ 折成一个数、可求值（{text}）", text)
    _out, ops = _run("main_like", [])
    check(ops <= kappa_at(k, {}),
          f"ops {ops} ≤ κ {kappa_at(k, {}):g}（κ 仍是上界）",
          f"ops={ops} κ={kappa_at(k, {})}")

    # 分配维度：`L(g)` 也要换出来（以前是 `n(g)×L(g)×1` 这种别人的形参名）。
    a = ci.alloc_of("main_like")
    check(a.variables() == [] and a.const_value() is not None,
          f"alloc 折成一个数、可求值（{a.render()}）", a.render())


# ---------------------------------------------------------------- ③ 等价性
def test_equivalence():
    print("[3] 等价性：`k < g[v].size()` vs `g[v].size() > k` —— κ 与 run 逐字节相同")
    _tu, _ch, ci = _mods()
    t1 = ci.cost_of("scan_from").render()
    t2 = ci.cost_of("scan_from_alt").render()
    check(t1 == t2, "两种写法的 κ **逐字节相同**", f"{t1!r}\n         {t2!r}")
    g = [[3, 1, 4], [1, 5], [], [9, 2, 6, 5]]
    o1, _ = _run("scan_from", [g, 0])
    o2, _ = _run("scan_from_alt", [g, 0])
    check(o1 == o2, "两种写法的 run 输出逐字节相同", f"{o1!r} vs {o2!r}")

    # 再加一条实测：`L(g)` 的 target 仍判「达成」（不是只能'看着像'）。
    t = ci.target_cost("scan_from")
    check(t is not None and t.render() == "L(g)",
          "target `O(L(g))` 解析成同一个尺寸量",
          t.render() if t else None)
    check(compare(ci.cost_of("scan_from"), t).kind == "ok",
          "target 判达成", compare(ci.cost_of("scan_from"), t).render())


# ---------------------------------------------------------------- ② 循环 push
def test_loop_push_rule():
    print("[4] ② 循环里的 `g[u].push_back(v)`：L(g) ≤ 初始 L + 轮数（实测 ≥3 个规模）")
    _tu, _ch, ci = _mods()
    k = ci.cost_of("build_star")
    check(k.kind != "unknown", "build_star 的 κ 不是 ⊤", k.render())
    check(_elem_bound(ci, "build_star") == "n×1",
          "L(g) 的上界是 `n`（每轮 +1）", str(_elem_bound(ci, "build_star")))
    bound = ci._elem_bounds_full(ci.fns["build_star"])[0]["g"]
    for n in (2, 4, 8):
        rows, ops = _run("build_star", [n])
        real = max((len(r) for r in rows), default=0)
        bnd = kappa_at(bound, {"n": n})
        check(real <= bnd, f"n={n}: 真实 max_i n(g[i]) = {real} ≤ 界 {bnd:g}",
              f"{real}>{bnd}")
        check(ops <= kappa_at(k, {"n": n}),
              f"n={n}: ops {ops} ≤ κ {kappa_at(k, {'n': n}):g}",
              f"ops={ops} κ={kappa_at(k, {'n': n})}")
    # 紧度：这条界在"星形图"上取到等号（n-1 条边全在第 0 行，界给 n）。
    rows, _ops = _run("build_star", [8])
    check(max(len(r) for r in rows) == 7,
          "n=8：真实 max 行 = 7（界 8，方向正确）", str(rows))


# ---------------------------------------------------------------- ③ 整行赋值
def test_row_assign_rule():
    print("[5] ③ `g[0] = row`：L(g) ≤ max(初始 L, n(row))（实测 ≥3 个规模）")
    _tu, _ch, ci = _mods()
    check(_elem_bound(ci, "build_assign") == "n",
          "L(g) 的上界是 `n`（= n(row)）", str(_elem_bound(ci, "build_assign")))
    bound = ci._elem_bounds_full(ci.fns["build_assign"])[0]["g"]
    for n in (2, 4, 8):
        rows, _ops = _run("build_assign", [n])
        real = max((len(r) for r in rows), default=0)
        bnd = kappa_at(bound, {"n": n})
        check(real <= bnd, f"n={n}: 真实 max_i n(g[i]) = {real} ≤ 界 {bnd:g}",
              f"{real}>{bnd}")
        check(len(rows) == n, f"n={n}: 行数仍是 n（赋值没有偷偷多建行）",
              str(rows))


# ---------------------------------------------------------------- ④ 传递 / 不猜
def test_unprovable_is_top():
    print("[6] ④ 元素来自另一个容器的元素：const 源 ⇒ `L(src)`；非 const 源 ⇒ 界 None、κ = ⊤")
    _tu, _ch, ci = _mods()
    check(_elem_bound(ci, "from_elem_rows") == "L(src)",
          "`g[0] = src[0]`（const `src`）⇒ L(g) ≤ L(src)（传递）",
          str(_elem_bound(ci, "from_elem_rows")))
    check(_elem_bound(ci, "unknown_rows") is None,
          "`g[0] = src[0]`（非 const `src`）⇒ 界是 None（没猜）",
          str(_elem_bound(ci, "unknown_rows")))
    k = ci.cost_of("unknown_rows")
    check(k.is_top(), "κ 是 ⊤（不用 n(src) 冒充行长）", k.render())


def main() -> int:
    test_make_graph_zero()
    test_main_becomes_concrete()
    test_equivalence()
    test_loop_push_rule()
    test_row_assign_rule()
    test_unprovable_is_top()

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    _sys.exit(main())
