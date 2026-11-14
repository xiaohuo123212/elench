#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_local_size.py —— **局部容器尺寸**的两类来源（2026-11-12）

## 为什么要有这份测试

C++ 里最常见的两种"建一个局部容器"的写法，κ 里都曾经只剩一个**局部名**
（`n(color)` / `n(masked)`）——target 引用不到，于是整条 κ = ⊤：

```cpp
std::vector<u64> color(g.size(), 0);   // ① 直接构造带尺寸（--compat 降级成 filled）
Buf masked = replace_all(frame, secret, stars);   // ② 局部容器 = 函数返回值
Buf head = slice(masked, 0, 11);                  //    链式
```

| # | 构造 | 上界 | 可证的理由 |
|---|---|---|---|
| ① | `vector<T>(n, v)` / `vector<T>::filled(v, n)` | `n`（字面量 / 标量形参 / `x.size()` / `x.f.size()`） | 构造时就确定了长度 |
| ② | `x = f(…)`（f 返回容器） | `返回尺寸(f)` 在**调用点**按实参实例化 | `return <容器>` 的长度上界 |
| ② | `y = slice(x, lo, hi)` | `hi` 的实参值 | 返回尺寸里的**标量形参**也要实例化 |

**① 的坑不在"怎么算 n"，在"算出来之后被抹掉"**：`has_cycle` 里的
`dfs_visit(g, v, color)` 把 `color` 整条传给非 const 引用形参，出参增长
分析一碰自递归就成环 ⇒ ⊤ ⇒ `|color|` 被抹成 None。但"这次调用不会写长"
是**安全性质**：体内没有增长点、递归调用把同一个实参传给同一个形参
（共归纳假设）⇒ 任何有限深度都不会变长。本文件钉住这条：界**保留**。

**② 的坑在"只换了容器尺寸，没换标量形参"**：`slice` 的返回尺寸是 `hi`，
`slice(masked, 0, 11)` 的 `hi=11` 不换掉，界里就留着 `hi`（调用方查不到）。
还有一条兜底：`append_range(out, src, i, at)` 的 `hi` 是 `find_from` 的
返回值（局部标量），换不出来；但它的循环体**无条件读** `src[i]`（`src` 是
const 容器形参）⇒ 良定义执行下圈数 ≤ n(src)（`i >= n(src)` 那一次读就越界，
与 `bound.py` 的"下标合法"口径同一条）⇒ `hi ≤ n(src)`。

## 硬约束（本文件钉的就是它）

1. **κ 是上界**：解释器真的跑一遍，`ops ≤ κ`；容器的**真实长度 ≤ 界**。
   每个形状都给 **3 个规模**，不是"看起来像符号"就算过。
2. **两种写法等价**：`std::vector<u64> color(g.size(), 0)`（`--compat`）
   与手写 `vector<u64>::filled(0, g.size())` 的 **κ 逐字节相同**、**run 输出相同**。
3. **推不出就不许猜**：`first_line(lines) = lines[0]` 的返回尺寸没有界 ⇒
   调用方的局部界必须是 `None`（⊤），MUST NOT 编一个 `n(lines)`。

跑法：

    cd tests/cases; $env:PYTHONIOENCODING="utf-8"; python test_kappa_local_size.py
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


# ---------------------------------------------------------------- ① 四种尺寸量
FILL_CPP = r"""
#include <cstdio>
#include <vector>

using u64 = unsigned long long;
using Graph = std::vector<std::vector<u64>>;

struct Wrap { std::vector<u64> v; };

static u64 fill_lit(u64 n) {
    std::vector<u64> v(8, 0);
    u64 i = 0;
    u64 s = 0;
    while (i < v.size()) { s = s + v[i]; i = i + 1; }
    return s + n;
}

static u64 fill_param(u64 n) {
    std::vector<u64> v(n, 0);
    u64 i = 0;
    u64 s = 0;
    while (i < v.size()) { s = s + v[i]; i = i + 1; }
    return s;
}

static void paint(std::vector<u64>& c, u64 v, u64 x) { c[v] = x; }

static u64 fill_size(Graph& g) {
    std::vector<u64> color(g.size(), 0);
    u64 v = 0;
    u64 s = 0;
    while (v < color.size()) { paint(color, v, v); s = s + color[v]; v = v + 1; }
    return s;
}

static u64 fill_field(Wrap& w) {
    std::vector<u64> c(w.v.size(), 0);
    u64 v = 0;
    u64 s = 0;
    while (v < c.size()) { paint(c, v, v); s = s + c[v]; v = v + 1; }
    return s;
}

// 返回**真实迭代次数**（= |color|）：用来对"实测 ≤ 上界"下断言。
static u64 count_color(const Graph& g) {
    std::vector<u64> color(g.size(), 0);
    u64 v = 0;
    while (v < color.size()) { paint(color, v, v); v = v + 1; }
    return v;
}

// 递归 + 三元色：**整条传给非 const 引用**（DFS 探针）。体内只有
// `color[v] = 1/2`（vector 下标赋值不改长度）与递归自调用 ⇒ 共归纳可证不变长。
static bool visit(const Graph& g, u64 v, std::vector<u64>& color) {
    color[v] = 1;
    std::size_t k = 0;
    while (k < g[v].size()) {
        u64 w = g[v][k];
        if (color[w] == 0) { if (visit(g, w, color)) return true; }
        k = k + 1;
    }
    color[v] = 2;
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
"""

#: 手写版：逐字节等于 `--compat` 降级后的形状。
FILL_HAND = FILL_CPP.replace(
    "std::vector<u64> color(g.size(), 0);",
    "std::vector<u64> color = std::vector<u64>::filled(0, g.size());")


# ---------------------------------------------------------------- ② 返回尺寸
RET_CPP = r"""
#include <cstdio>
#include <vector>

using u64 = unsigned long long;
using u8 = unsigned char;
using Buf = std::vector<u8>;

static void append_range(Buf& b, const Buf& src, std::size_t lo, std::size_t hi) {
    for (std::size_t i = lo; i < hi; ++i) b.push_back(src[i]);
}

static u64 find_from(const Buf& hay, const Buf& needle, std::size_t from) {
    if (needle.size() == 0) return from;
    for (std::size_t i = from; i + needle.size() <= hay.size(); ++i) {
        bool ok = true;
        for (std::size_t k = 0; k < needle.size(); ++k) {
            if (hay[i + k] != needle[k]) ok = false;
        }
        if (ok) return i;
    }
    return hay.size();
}

static Buf replace_all(const Buf& src, const Buf& from, const Buf& to) {
    Buf out;
    std::size_t i = 0;
    for (std::size_t step = 0; step < src.size(); ++step) {
        u64 at = find_from(src, from, i);
        if (at == src.size()) break;
        append_range(out, src, i, at);
        append_range(out, to, 0, to.size());
        i = at + from.size();
    }
    append_range(out, src, i, src.size());
    return out;
}

static Buf cut(const Buf& b, std::size_t lo, std::size_t hi) {
    Buf out;
    append_range(out, b, lo, hi);
    return out;
}

static u64 driver(const Buf& frame, const Buf& secret, const Buf& stars) {
    Buf masked = replace_all(frame, secret, stars);
    Buf head = cut(masked, 0, 4);
    Buf tail = cut(masked, 4, masked.size());
    return masked.size() * 1000 + head.size() * 100 + tail.size();
}

// **推不出**：返回的是 `lines[0]`（元素），长度与 `n(lines)` 无关。
static Buf first_line(const std::vector<Buf>& lines) {
    return lines[0];
}

static u64 unknown_ret(const std::vector<Buf>& lines) {
    Buf x = first_line(lines);
    Buf y = cut(x, 0, 4);
    return y.size();
}
"""


def _parse(src, name):
    prev = compat.enabled()
    compat.set_enabled(True)
    try:
        return parse_source(src, name).module
    finally:
        compat.set_enabled(prev)


def _bounds(ci, fn_name):
    """局部容器 → 尺寸上界（`None` = 推不出）。"""
    fn = ci.fns[fn_name]
    return {k: (None if v is None else v.render())
            for k, v in ci._container_bounds_full(fn)[0].items()}


def _run(ci, mod, fn_name, args):
    """解释器跑一次，返回 `(返回值, 该函数的 ops)`。"""
    ops_enable(True)
    try:
        ops_reset()
        val = Interp(mod, None).call(fn_name, args)
        return val, ops_of(fn_name)
    finally:
        ops_enable(False)


# ---------------------------------------------------------------- ① 四种尺寸量
def test_filled_sources():
    print("[1] ① 局部尺寸 = n：字面量 / 标量形参 / x.size() / x.f.size()")
    mod = _parse(FILL_CPP, "fill_cpp.eln")
    ci = CostInterp(mod, None)
    for fn, want in (("fill_lit", "8"), ("fill_param", "n"),
                     ("fill_size", "n(g)"), ("fill_field", "n(w.v)")):
        b = _bounds(ci, fn)
        got = b.get(list(b)[0]) if b else None
        check(got == want, f"{fn}: 唯一的局部容器界是 `{want}`", str(b))
    k = ci.cost_of("fill_lit")
    check(k.kind != "unknown", "fill_lit 的 κ 不是 ⊤", k.render())
    check("n(v)" not in k.render(), "κ 里没有局部名 `n(v)`", k.render())


# ---------------------------------------------------------------- ① 不抹界 + 等价
def test_call_does_not_erase():
    print("[2] ① 整条传给（非 const 引用）形参**不抹界**；两种写法 κ/run 相同")
    cpp = _parse(FILL_CPP, "fill_cpp2.eln")
    hand = _parse(FILL_HAND, "fill_hand2.eln")
    a, b = CostInterp(cpp, None), CostInterp(hand, None)

    # 两种写法的 κ **逐字节相同**（compat 降级后同形）。
    for fn in ("fill_lit", "fill_param", "fill_size", "fill_field", "has_cycle"):
        ka, kb = a.cost_of(fn).render(), b.cost_of(fn).render()
        check(ka == kb, f"{fn}: 两种写法的 κ 逐字节相同", f"{ka!r} != {kb!r}")
    for fn in ("fill_size", "has_cycle"):
        check(_bounds(a, fn) == _bounds(b, fn),
              f"{fn}: 两种写法的局部界相同", f"{_bounds(a, fn)} != {_bounds(b, fn)}")

    # **界被保住**：`paint(color, …)`（只写已有槽位）与 `visit(g, v, color)`
    # （自递归、体内也不增长）都不许把 `n(g)` 抹成 None。
    check(_bounds(a, "fill_size").get("color") == "n(g)",
          "fill_size：`paint` 之后 |color| 仍是 `n(g)`", str(_bounds(a, "fill_size")))
    check(_bounds(a, "has_cycle").get("color") == "n(g)",
          "has_cycle：`visit`（自递归）之后 |color| 仍是 `n(g)`",
          str(_bounds(a, "has_cycle")))

    # **等价性（run）**：同逻辑两种写法输出逐字节相同。
    g = [[1], [2], [0, 2]]                       # 有环
    for fn, args in (("fill_size", [g]), ("has_cycle", [g])):
        va, _ = _run(a, cpp, fn, args)
        vb, _ = _run(b, hand, fn, args)
        check(va == vb, f"{fn}: 两种写法的 run 结果相同（{va!r}）", f"{va!r} != {vb!r}")

    # **实测 ≤ 上界 + ops ≤ κ + 真实迭代次数**（3 个规模）。
    k = a.cost_of("fill_size")
    kc = a.cost_of("count_color")
    for n in (2, 5, 9):
        gg = [[(i + 1) % n] for i in range(n)]
        got, ops = _run(a, cpp, "fill_size", [gg])
        bnd = kappa_at(a._container_bounds_full(a.fns["fill_size"])[0]["color"],
                       {"g": n})
        check(len(gg) <= bnd, f"n={n}: 真实 |color| = {len(gg)} ≤ 界 {bnd:g}",
              f"{len(gg)}>{bnd}")
        kv = kappa_at(k, {"g": n})
        check(ops <= kv, f"n={n}: ops {ops} ≤ κ {kv:g}", f"ops={ops} κ={kv}")
        iters, iops = _run(a, cpp, "count_color", [gg])
        kb = kappa_at(a._container_bounds_full(a.fns["count_color"])[0]["color"],
                      {"g": n})
        check(iters <= kb, f"n={n}: 真实迭代 {iters} ≤ |color| 的上界 {kb:g}",
              f"{iters}>{kb}")
        kvc = kappa_at(kc, {"g": n})
        check(iops <= kvc, f"n={n}: count_color ops {iops} ≤ κ {kvc:g}",
              f"ops={iops} κ={kvc}")


# ---------------------------------------------------------------- ② 返回尺寸
def test_return_size_and_chain():
    print("[3] ② `x = f(…)` 用返回尺寸；`slice(x,lo,hi)` 链式（标量实参也实例化）")
    mod = _parse(RET_CPP, "ret_cpp.eln")
    ci = CostInterp(mod, None)

    b_ra = _bounds(ci, "replace_all").get("out")
    check(b_ra is not None and "n(src)" in (b_ra or ""),
          "replace_all：|out| 是 `n(src)` 写出来的表达式", str(b_ra))
    b_cut = _bounds(ci, "cut").get("out")
    check(b_cut == "hi", "cut：|out| = `hi`（返回尺寸里的标量形参）", str(b_cut))

    bd = _bounds(ci, "driver")
    check(bd.get("masked") is not None and "n(frame)" in (bd.get("masked") or ""),
          "driver：|masked| 已按 frame/secret/stars 的尺寸实例化",
          str(bd.get("masked")))
    check(bd.get("head") == "4", "driver：`cut(masked, 0, 4)` 的界是 4", str(bd.get("head")))
    check(bd.get("tail") == bd.get("masked"),
          "driver：`cut(masked, 4, masked.size())` 的界 = |masked| 的界",
          f"{bd.get('tail')} vs {bd.get('masked')}")

    k = ci.cost_of("driver")
    check(k.kind != "unknown", "driver 的 κ 不是 ⊤", k.render()[:80])
    # 链式（`n(masked)`）是局部尺寸：它的界在上面钉过；求值时用界（不是 1），
    # 保证 κ 仍是**上界**方向。
    _m = ci._container_bounds_full(ci.fns["driver"])[0]["masked"]

    sizes = [
        (b"GET /health key=SECRET123 ua=curl/8", b"SECRET123", b"*********"),
        (b"aaabbbaaa", b"a", b"z"),
        (b"abcde", b"zz", b"yy"),          # 找不到：masked = frame（换长度也不怕）
    ]
    for frame, secret, stars in sizes:
        L, f, t = len(frame), len(secret), len(stars)
        got, ops = _run(ci, mod, "driver", [list(frame), list(secret), list(stars)])
        masked_len = got // 1000
        head_len = (got // 100) % 10
        tail_len = got % 100
        env = {"frame": L, "secret": f, "stars": t,
               "masked": kappa_at(_m, {"frame": L, "secret": f, "stars": t})}
        mb = kappa_at(_m, {"frame": L, "secret": f, "stars": t})
        check(masked_len <= mb, f"|frame|={L}: 真实 |masked| = {masked_len} ≤ 界 {mb:g}",
              f"{masked_len}>{mb}")
        check(head_len <= 4, f"|frame|={L}: 真实 |head| = {head_len} ≤ 界 4", str(head_len))
        check(tail_len <= mb, f"|frame|={L}: 真实 |tail| = {tail_len} ≤ 界 {mb:g}",
              f"{tail_len}>{mb}")
        kv = kappa_at(k, env)
        check(ops <= kv, f"|frame|={L}: ops {ops} ≤ κ {kv:g}", f"ops={ops} κ={kv}")


# ---------------------------------------------------------------- ⑤ 不猜
def test_unprovable_stays_top():
    print("[4] ② 返回尺寸推不出 ⇒ 局部界 ⊤（不许编 `n(lines)`）")
    mod = _parse(RET_CPP, "ret_cpp2.eln")
    ci = CostInterp(mod, None)
    check(ci._ret_size_of("first_line", None) is None,
          "first_line 的返回尺寸是 None（返回元素，长度与 n(lines) 无关）",
          str(ci._ret_size_of("first_line", None)))
    b = _bounds(ci, "unknown_ret")
    check(b.get("x") is None, "调用方的 `x` 界是 None（没猜）", str(b.get("x")))
    check("n(lines)" not in str(b), "界里没有编造 `n(lines)`", str(b))
    try:
        k = ci.cost_of("unknown_ret")
        check(k.is_top(), "unknown_ret 的 κ 是 ⊤", k.render()[:80])
    except Exception as e:                                   # noqa: BLE001
        check("局部容器" in str(e), "CostError 指名局部容器（CLI 显示成 κ=⊤ 的原因）", str(e))


def main() -> int:
    test_filled_sources()
    test_call_does_not_erase()
    test_return_size_and_chain()
    test_unprovable_stays_top()

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
