#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_recursion_visited.py —— **visited 守卫的递归**（②，2026-11-12）

## 为什么要有这份测试

语料 `12_graph` 的 `dfs_visit` 自调用落在 `for (k < g[v].size())` 里，按
「树递归」判 ⊤：

    color[v] = 1;                                   // 进入即标记
    for (k < g[v].size()) { w = g[v][k];
        if (color[w] == 0) { if (dfs_visit(g, w, color)) return true; }   // 守卫
    }

但它**不是**树递归：每个顶点至多进入一次 ⇒ 一次调用的激活数 ≤ `n(color)`
⇒ κ ≤ `n(color) × 单层 + 尾部`。本测试钉住：

1. **能算**：κ(`visit`) 不是 ⊤，且含 `n(color)`（激活数上界）与 `L(g)`
   （内层循环的元素长度上界）；
2. **等价性逐字节**：把标记值从 `1/2` 换成 `7/9`（语义不变、语句数不变）——
   κ 字符串**逐字节相同**、run 返回值与 ops 也相同；
3. **上界**：解释器在 3 个规模上真跑，`ops ≤ κ`；且**激活数 ≤ n(color)**
   （用"访问到的顶点数"实测，不是只看形状）；
4. **不许乱用**：把进入处的标记写成 `0`、或把守卫改成 `!= 0 + continue`
   ⇒ 仍然 ⊤（**只认这一种形状**，证不出就不给界）。

跑法：

    cd src; $env:PYTHONIOENCODING="utf-8"; python test_kappa_recursion_visited.py
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

import compat                                            # noqa: E402
from cost_check import evaluate as kappa_at              # noqa: E402
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


#: `MARK_ON` / `MARK_DONE` 是**语义无关**的标记值：换成正数里的任何值，程序
#: 的可观测行为都不变（只有 "是否为 0" 有意义）。
CPP = r"""
#include <cstdio>
#include <vector>

using u64 = unsigned long long;
using Graph = std::vector<std::vector<u64>>;

static bool visit(const Graph& g, u64 v, std::vector<u64>& color) {
    color[v] = __MARK_ON__;
    std::size_t k = 0;
    while (k < g[v].size()) {
        u64 w = g[v][k];
        if (color[w] == 0) { if (visit(g, w, color)) return true; }
        k = k + 1;
    }
    color[v] = __MARK_DONE__;
    return false;
}

static bool has_cycle(const Graph& g) {
    std::vector<u64> color(g.size(), 0);
    std::size_t v = 0;
    while (v < g.size()) {
        if (color[v] == 0) { if (visit(g, v, color)) return true; }
        v = v + 1;
    }
    return false;
}

static u64 reach(const Graph& g) {
    std::vector<u64> color(g.size(), 0);
    std::size_t v = 0;
    while (v < g.size()) {
        if (color[v] == 0) { visit(g, v, color); }
        v = v + 1;
    }
    u64 seen = 0;
    std::size_t i = 0;
    while (i < color.size()) {
        if (color[i] != 0) seen += 1;
        i = i + 1;
    }
    return seen;
}
"""

#: 标记值 1/2 与 7/9：**同逻辑、同语句数**（可观测行为完全一样）。
CPP_A = CPP.replace("__MARK_ON__", "1").replace("__MARK_DONE__", "2")
CPP_B = CPP.replace("__MARK_ON__", "7").replace("__MARK_DONE__", "9")

#: 反例①：进入处写 0（标记根本不成立）。
CPP_ZERO = CPP_A.replace("color[v] = 1;", "color[v] = 0;")

#: 反例②：守卫写成 `!= 0` + continue（逻辑等价，但**不是**本规则认的形状）。
CPP_NE = r"""
using u64 = unsigned long long;
using Graph = std::vector<std::vector<u64>>;

static void visit2(const Graph& g, u64 v, std::vector<u64>& color) {
    color[v] = 1;
    std::size_t k = 0;
    while (k < g[v].size()) {
        u64 w = g[v][k];
        if (color[w] != 0) { k = k + 1; continue; }
        visit2(g, w, color);
        k = k + 1;
    }
    color[v] = 2;
}
"""


#: 反例③：把标记容器传给**别的函数**（它可以把标记清回 0）。
CPP_ALIAS = r"""
using u64 = unsigned long long;
using Graph = std::vector<std::vector<u64>>;

static void clobber(std::vector<u64>& c, u64 i) { c[i] = 0; }

static void visit3(const Graph& g, u64 v, std::vector<u64>& color) {
    color[v] = 1;
    std::size_t k = 0;
    while (k < g[v].size()) {
        u64 w = g[v][k];
        if (color[w] == 0) { clobber(color, w); visit3(g, w, color); }
        k = k + 1;
    }
    color[v] = 2;
}
"""


def _parse(src, name):
    prev = compat.enabled()
    compat.set_enabled(True)
    try:
        return parse_source(src, name).module
    finally:
        compat.set_enabled(prev)


def _run(mod, fn, args):
    ops_enable(True)
    try:
        ops_reset()
        val = Interp(mod, None).call(fn, args)
        return val, ops_of(fn)
    finally:
        ops_enable(False)


def _run_total(mod, fn, args):
    """**总操作数**（`ops_of(None)`）。

    ⚠ 对**自递归**函数不能用 `ops_of(fn)`：它的口径是「这个函数整棵调用树的
    操作数」，而同名递归的每一层都会把自己的子树再加到外层同名槽位上一次——
    实测链式图 n=9 时 `ops_of('visit')` = 1908 / `ops_of(None)` = 404
    （真实是线性的）。这里要的是"实打实花了多少操作"，所以取总数；`ops` 与
    κ 仍然同单位（操作）。
    """
    ops_enable(True)
    try:
        ops_reset()
        val = Interp(mod, None).call(fn, args)
        return val, ops_of(None)
    finally:
        ops_enable(False)


def _env(k, n, lmax):
    """把 κ 里的尺寸量赋成实测值：`n(color)` → n，`L(g)` → 最长一行。"""
    e = {}
    for v in k.variables():
        if v.startswith("L("):
            e[v] = lmax
        elif v in ("g", "color"):
            e[v] = n
        else:
            e[v] = n
    return e


def _chain(n):
    """链式图：0→1→…→n-1（无环，L(g) = 1）。"""
    return [[i + 1] if i + 1 < n else [] for i in range(n)]


# ---------------------------------------------------------------- ① 能算
def test_recognized():
    print("[1] visited 守卫的递归：κ 不是 ⊤，含 `n(color)` 与 `L(g)`")
    mod = _parse(CPP_A, "visited_a.eln")
    ci = CostInterp(mod, None)
    k = ci.cost_of("visit")
    text = k.render()
    check(not k.is_top(), "visit 的 κ 不是 ⊤", text)
    check("n(color)" in text, "κ 里有激活数上界 `n(color)`", text)
    check("L(g)" in text, "κ 里有内层循环界 `L(g)`（元素长度）", text)
    check("n(?)" not in text, "κ 里没有 `n(?)`", text)
    # 调用方（普通函数）也跟着能算
    kc = ci.cost_of("has_cycle")
    check(not kc.is_top(), "has_cycle 的 κ 不是 ⊤", kc.render())


# ---------------------------------------------------------------- ② 等价写法
def test_equivalence():
    print("[2] 标记值 1/2 vs 7/9（语义无关）：κ 与 run 逐字节相同")
    a = CostInterp(_parse(CPP_A, "visited_a2.eln"), None)
    b = CostInterp(_parse(CPP_B, "visited_b2.eln"), None)
    for fn in ("visit", "has_cycle", "reach"):
        ka, kb = a.cost_of(fn).render(), b.cost_of(fn).render()
        check(ka == kb, f"{fn}: 两种标记值的 κ 逐字节相同", f"{ka!r} != {kb!r}")
    g = [[1], [2], [0, 2]]
    for fn in ("has_cycle", "reach"):
        va, oa = _run(_parse(CPP_A, "visited_a3.eln"), fn, [g])
        vb, ob = _run(_parse(CPP_B, "visited_b3.eln"), fn, [g])
        check(repr(va) == repr(vb) and oa == ob,
              f"{fn}: run 逐字节相同（值 {va!r}，ops {oa}）",
              f"{va!r}/{oa} vs {vb!r}/{ob}")


# ---------------------------------------------------------------- ③ 上界
def test_upper_bound():
    print("[3] 3 个规模：ops ≤ κ，且真实激活数 ≤ n(color)")
    mod = _parse(CPP_A, "visited_u.eln")
    ci = CostInterp(mod, None)
    k = ci.cost_of("visit")
    kc = ci.cost_of("has_cycle")
    for n in (3, 5, 9):
        g = _chain(n)
        lmax = 1
        val, ops = _run_total(mod, "visit", [g, 0, [0] * n])
        kv = kappa_at(k, _env(k, n, lmax))
        check(ops <= kv, f"n={n}: visit 总 ops {ops} ≤ κ {kv:g}",
              f"ops={ops} κ={kv} κ式={k.render()}")
        # **激活数**（= 访问到的顶点数）≤ n(color)：上界的那个量真的成立。
        seen, sops = _run_total(mod, "reach", [g])
        check(seen <= n, f"n={n}: 真实激活数 {seen} ≤ n(color)={n}", f"{seen}>{n}")
        kv2 = kappa_at(kc, _env(kc, n, lmax))
        check(sops <= kv2, f"n={n}: reach 总 ops {sops} ≤ κ {kv2:g}",
              f"ops={sops} κ={kv2}")

    # 有环图：这个变体只标 1/2、没有 `color[w] == 1` 的背边判断 ⇒ 返回 false；
    # 测的是"提前 return 不会让 κ 小于实测"。
    g = [[1], [2], [0]]
    val, ops = _run_total(mod, "has_cycle", [g])
    kv = kappa_at(kc, _env(kc, 3, 1))
    check(val is False and ops <= kv,
          f"有环图：has_cycle 返回 {val!r}（该变体不判背边），总 ops {ops} ≤ κ {kv:g}",
          f"ops={ops} κ={kv}")


# ---------------------------------------------------------------- ④ 不许乱用
def test_not_over_applied():
    print("[4] 证不出就不给界：标记写 0 / 守卫写成 `!= 0` ⇒ 仍 ⊤")
    ci0 = CostInterp(_parse(CPP_ZERO, "visited_zero.eln"), None)
    try:
        k0 = ci0.cost_of("visit")
        ok0 = k0.is_top()
    except Exception:                                    # noqa: BLE001
        ok0 = True                                       # 成环报错 = 没给界
    check(ok0, "进入处标记写成 `0` ⇒ κ 仍是 ⊤（标记根本没建立）",
          str(getattr(ci0, "top_reasons", {})))

    ci1 = CostInterp(_parse(CPP_NE, "visited_ne.eln"), None)
    try:
        k1 = ci1.cost_of("visit2")
        ok1 = k1.is_top()
        text1 = k1.render()
    except Exception:                                    # noqa: BLE001
        ok1, text1 = True, "⊤(CostError)"
    check(ok1, "守卫写成 `!= 0` + continue ⇒ 仍 ⊤（只认 `== 0` 那一种形状）", text1)
    check("n(color)" not in text1, "没有凭空给出 `n(color)` 上界", text1)

    ci2 = CostInterp(_parse(CPP_ALIAS, "visited_alias.eln"), None)
    try:
        k2 = ci2.cost_of("visit3")
        ok2, text2 = k2.is_top(), k2.render()
    except Exception:                                    # noqa: BLE001
        ok2, text2 = True, "⊤(CostError)"
    check(ok2, "标记容器被传给别的函数（可能清回 0）⇒ 仍 ⊤", text2)
    check("n(color)" not in text2, "没有给出被别名破坏的 `n(color)` 上界", text2)


def main() -> int:
    test_recognized()
    test_equivalence()
    test_upper_bound()
    test_not_over_applied()

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
