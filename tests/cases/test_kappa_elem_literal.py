#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_elem_literal.py —— **元素长度从字面量推**（`02_csv_stats` / `08_bits_hash`，2026-11-12 夜）

## 为什么要有这份测试

语料里有两个 ⊤ 曾被归成「元素长度与输入尺寸无关，不可判定」：

    std::vector<std::string> lines{"a,1", "b,2", "a,3"};   // L(lines) = 3，已知
    … split(lines[i], ',') …

    cmds.push_back("get"); …                                // L(cmds) = 3，已知
    unordered_map<std::string,u64> freq;  count_words(cmds, freq);
    … freq.get(ks[idx]) …                                   // 键长 ≤ 3

它们的数据源是**字面量**，长度写死在源码里 —— 不是"不可判定"，是工具缺口。
`L(x) := max_i n(x[i])` 本来就实现了（`test_kappa_elem_growth.py` 的四条来源），
缺的是「容器从字面量长出来」这一档。

## 本文件钉住的来源（**推不出就 ⊤**，方向只能是"上界"）

| # | 构造 | `L(x)` 的上界 | 为什么可证 |
|---|---|---|---|
| ① | `vector<string> xs{"a,1", "b,22"}`（含 `= {…}`） | `max(各字面量长度)` | 构造时每个元素就是写明的字面量 |
| ② | `xs.push_back("hello")`（含循环里） | `max(旧 L, 该字面量长度)` | 每轮至多入一个，长度就是字面量长度 |
| ③ | `ks = m.keys()`，且 `m` 的键来自 ①② | `L(ks) = L(m)` | 每个键是 `m` 的键的拷贝；`L(m)` 是键长最大值 |
| ④ | 来自**函数实参 / 外部输入**（非 const 形参、认不出的调用） | **⊤** | 不猜 |

## 两类断言（只比形状不算验证）

1. **等价性**：`T xs{…}` 与 `T xs = {…}` 的 κ **逐字节相同**、run 返回值逐字节
   相同；`push` 与 `push_back` 同理。
2. **实测 ≤ 上界**：解释器真的跑，取**真实的** `max_i n(x[i])` 与推出的界比
   （≥3 个规模）；`ops ≤ κ`（κ 仍是上界）。

跑法：

    cd tests/cases; $env:PYTHONIOENCODING="utf-8"; python test_kappa_elem_literal.py
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

import compat as _compat                                # noqa: E402
from cost_check import evaluate as kappa_at             # noqa: E402
from cost_interp import CostInterp                      # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset  # noqa: E402
from parser import parse_source                         # noqa: E402

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


#: 实现侧：与 `02_csv_stats` / `08_bits_hash` 同形（`--compat` 展开 `std::string`）。
CH = r"""
#include <string>
#include <vector>
#include <map>
#include <unordered_map>

using u64 = unsigned long long;

// ---------- ① 初始化列表（两种拼写）
std::vector<std::string> lit_brace() {
    std::vector<std::string> xs{"a,1", "b,22", "c,333"};
    return xs;
}

std::vector<std::string> lit_eq() {
    std::vector<std::string> xs = {"a,1", "b,22", "c,333"};
    return xs;
}

// ---------- ② 逐个 push_back（两种拼写） / 循环里 push
std::vector<std::string> lit_push() {
    std::vector<std::string> xs;
    xs.push_back("a,1");
    xs.push_back("b,22");
    xs.push_back("c,333");
    return xs;
}

std::vector<std::string> lit_push_alt() {
    std::vector<std::string> xs;
    xs.push("a,1");
    xs.push("b,22");
    xs.push("c,333");
    return xs;
}

std::vector<std::string> lit_loop(u64 n) {
    std::vector<std::string> xs;
    u64 i = 0;
    while (i < n) { xs.push_back("hello"); i = i + 1; }
    return xs;
}

// ---------- ③ 派生的键快照：ks = m.keys()
u64 map_literals() {
    std::map<std::string, u64> m;
    m.insert("ab", 1);
    m.insert("abcd", 2);
    std::vector<std::string> ks = m.keys();
    u64 i = 0;
    u64 s = 0;
    while (i < ks.size()) { s = s + m.get(ks[i]); i = i + 1; }
    return s;
}

u64 map_from_param(const std::vector<std::string>& words) {
    std::map<std::string, u64> m;
    for (const auto& w : words) { m[w] += 1; }
    std::vector<std::string> ks = m.keys();
    u64 i = 0;
    u64 s = 0;
    while (i < ks.size()) { s = s + m.get(ks[i]); i = i + 1; }
    return s;
}

// 等价性：`m[k] += 1` 与 `m[k] = m[k] + 1`（D1 守卫）
u64 map_from_param_assign(const std::vector<std::string>& words) {
    std::map<std::string, u64> m;
    for (const auto& w : words) { m[w] = m[w] + 1; }
    std::vector<std::string> ks = m.keys();
    u64 i = 0;
    u64 s = 0;
    while (i < ks.size()) { s = s + m.get(ks[i]); i = i + 1; }
    return s;
}

// `unordered_map<string,u64>` 的**容器键哈希长度项**：键长 = L(ks)
u64 hash_literals() {
    std::unordered_map<std::string, u64> freq;
    freq["get"] += 1;
    freq["remove"] += 1;
    freq["put"] += 1;
    std::vector<std::string> ks = freq.keys();
    u64 i = 0;
    u64 s = 0;
    while (i < ks.size()) { s = s + freq.get(ks[i]); i = i + 1; }
    return s;
}

// 从形参来的键 + 哈希长度项：κ 里出现 `L(words)`（合法尺寸量，不是 ⊤）
u64 hash_from_param(const std::vector<std::string>& words) {
    std::unordered_map<std::string, u64> freq;
    for (const auto& w : words) { freq[w] += 1; }
    std::vector<std::string> ks = freq.keys();
    u64 i = 0;
    u64 s = 0;
    while (i < ks.size()) { s = s + freq.get(ks[i]); i = i + 1; }
    return s;
}

// ---------- ④ 推不出就不猜：源是非 const 形参
std::vector<std::string> from_nonconst_param(std::vector<std::string>& src) {
    std::vector<std::string> xs;
    xs.push_back(src[0]);
    return xs;
}
"""


def _mod():
    """`--compat` 解析一次；调用方负责恢复开关。"""
    prev = _compat.enabled()
    _compat.set_enabled(True)
    try:
        mod = parse_source(CH, "elem_lit.eln").module
    finally:
        _compat.set_enabled(prev)
    return mod


def _ci():
    return CostInterp(_mod(), None)


def _elem_bound(ci, fn_name, name):
    eb, _ln = ci._elem_bounds_full(ci.fns[fn_name])
    v = eb.get(name)
    return None if v is None else v.render()


def _run(mod, fn_name, args):
    """解释器跑一次 → `(容器/返回值, 该函数的 ops)`。"""
    ops_enable(True)
    try:
        ops_reset()
        val = Interp(mod, None).call(fn_name, args)
        return val, ops_of(fn_name)
    finally:
        ops_enable(False)


def _real_elem_len(val):
    """真实的 `max_i n(x[i])`（空容器取 0）。"""
    if not isinstance(val, list):
        return 0
    return max((len(r) for r in val), default=0)


# ---------------------------------------------------------------- ① 初始化列表
def test_init_list():
    print("[1] ① 初始化列表：`{…}` 与 `= {…}` 的 L 相同、κ 逐字节相同")
    ci = _ci()
    b1 = _elem_bound(ci, "lit_brace", "xs")
    b2 = _elem_bound(ci, "lit_eq", "xs")
    check(b1 == "5", "`{\"a,1\",\"b,22\",\"c,333\"}` ⇒ L(xs) = 5（最长的字面量）", str(b1))
    check(b1 == b2, "`= {…}` 给出**同一个** L", f"{b1} vs {b2}")
    k1 = ci.cost_of("lit_brace").render()
    k2 = ci.cost_of("lit_eq").render()
    check(k1 == k2, "两种写法的 κ 逐字节相同", f"{k1!r}\n         {k2!r}")
    check("L(" not in k1, "κ 里没有残留 `L(...)`", k1)
    # 实测：真实 max 元素长度 ≤ 界
    mod = _mod()
    val, ops = _run(mod, "lit_brace", [])
    real = _real_elem_len(val)
    check(real <= 5 and real == 5, f"实测 max_i n(xs[i]) = {real} ≤ 界 5", str(real))
    cap = kappa_at(ci.cost_of("lit_brace"), {})
    check(ops <= cap, f"ops {ops} ≤ κ {cap:g}（κ 仍是上界）", f"ops={ops} κ={cap}")
    print()


# ---------------------------------------------------------------- ② push_back
def test_push_literals():
    print("[2] ② `push_back(<字面量>)`：L = max(旧 L, 字面量长度)；push/push_back 同 κ")
    ci = _ci()
    b = _elem_bound(ci, "lit_push", "xs")
    check(b == "5", "逐个 push_back ⇒ L(xs) = 5", str(b))
    check(_elem_bound(ci, "lit_push_alt", "xs") == "5",
          "`push` 与 `push_back` 给同一个 L")
    k1 = ci.cost_of("lit_push").render()
    k2 = ci.cost_of("lit_push_alt").render()
    check(k1 == k2, "`push` / `push_back` 的 κ 逐字节相同", f"{k1!r}\n         {k2!r}")
    # 两种拼写的 run 输出逐字节相同
    mod = _mod()
    v1 = _run(mod, "lit_push", [])[0]
    v2 = _run(mod, "lit_push_alt", [])[0]
    check(v1 == v2, "两种拼写的 run 输出逐字节相同", f"{v1!r} vs {v2!r}")
    # **循环里 push 字面量**：L 仍是那个字面量的长度（不随轮数长大）
    check(_elem_bound(ci, "lit_loop", "xs") == "5",
          "`while (i < n) xs.push_back(\"hello\")` ⇒ L(xs) = 5（与 n 无关）",
          str(_elem_bound(ci, "lit_loop", "xs")))
    k = ci.cost_of("lit_loop")
    for n in (0, 1, 7):
        val, ops = _run(mod, "lit_loop", [n])
        real = _real_elem_len(val)
        cap = kappa_at(k, {"n": n})
        check(real <= 5, f"n={n}: 真实 max_i n(xs[i]) = {real} ≤ 界 5", f"{real}>5")
        check(ops <= cap, f"n={n}: ops {ops} ≤ κ {cap:g}", f"ops={ops} κ={cap}")
    print()


# ---------------------------------------------------------------- ③ 传递
def test_derived_keys():
    print("[3] ③ `ks = m.keys()` 传递：L(ks) = L(m)；语料 08 的哈希长度项有界")
    ci = _ci()
    check(_elem_bound(ci, "map_literals", "m") == "4",
          "字面量键 ⇒ L(m) = 4（最长键 \"abcd\"）", str(_elem_bound(ci, "map_literals", "m")))
    check(_elem_bound(ci, "map_literals", "ks") == "4",
          "`ks = m.keys()` ⇒ L(ks) = L(m) = 4",
          str(_elem_bound(ci, "map_literals", "ks")))
    k = ci.cost_of("map_literals")
    check(k.variables() == [], "κ 全折成常数（没有局部名外泄）", str(k.variables()))
    check("L(" not in k.render(), "κ 里没有残留 `L(...)`", k.render())
    mod = _mod()
    val, ops = _run(mod, "map_literals", [])
    check(val == 3, f"run 输出 = 3（get 的和）", str(val))
    cap = kappa_at(k, {})
    check(ops <= cap, f"ops {ops} ≤ κ {cap:g}", f"ops={ops} κ={cap}")

    # **从形参来的键**：L(ks) = L(words)（合法尺寸量，不是 ⊤）
    check(_elem_bound(ci, "map_from_param", "ks") == "L(words)",
          "`for (w : words) m[w] += 1; ks = m.keys()` ⇒ L(ks) = L(words)",
          str(_elem_bound(ci, "map_from_param", "ks")))
    kp = ci.cost_of("map_from_param")
    check("n(ks)" not in kp.render() and "n(m)" not in kp.render(),
          "κ 里没有局部容器名 `ks` / `m`", kp.render())
    # 等价性：`m[w] += 1` 与 `m[w] = m[w] + 1` 的**尺寸量集合**相同、run 输出相同
    kp2 = ci.cost_of("map_from_param_assign")
    check(set(kp.variables()) == set(kp2.variables()),
          "两种计数写法的 κ 尺寸量集合相同",
          f"{kp.variables()} vs {kp2.variables()}")
    mod = _mod()
    for words in ([[97], [98], [97]], [[97, 98], [99, 100, 101], [97, 98]]):
        check(_run(mod, "map_from_param", [words])[0]
              == _run(mod, "map_from_param_assign", [words])[0],
              f"|words|={len(words)}：两种写法的 run 输出逐字节相同",
              "")
    # 实测：真实键长 ≤ L(words)，ops ≤ κ
    for words in ([[97], [98], [97]],
                  [[97, 98], [99, 100, 101], [97, 98]],
                  [[104, 101, 108, 108, 111]] * 4):
        real_len = max((len(w) for w in words), default=0)
        val, ops = _run(mod, "map_from_param", [words])
        cap = kappa_at(kp, {"words": len(words), "L(words)": real_len})
        check(ops <= cap,
              f"|words|={len(words)} max 键长 {real_len}：ops {ops} ≤ κ {cap:g}",
              f"ops={ops} κ={cap} 值={val}")

    # **哈希长度项 + 形参键**：κ 里必须出现 `L(words)`（08_bits_hash 的形状）
    check(_elem_bound(ci, "hash_from_param", "ks") == "L(words)",
          "`unordered_map` 的 `ks = freq.keys()` ⇒ L(ks) = L(words)",
          str(_elem_bound(ci, "hash_from_param", "ks")))
    khp = ci.cost_of("hash_from_param")
    check("L(words)" in khp.render(),
          "κ 里用的是 `L(words)`（哈希长度项没有留下 `n(ks[idx])`）", khp.render())
    check("n(ks)" not in khp.render() and "n(freq)" not in khp.render(),
          "κ 里没有局部容器名 `ks` / `freq`", khp.render())
    for words in ([[97], [98, 99]],
                  [[97, 98, 99, 100], [105]]):
        real_len = max((len(w) for w in words), default=0)
        _val, ops = _run(mod, "hash_from_param", [words])
        cap = kappa_at(khp, {"words": len(words), "L(words)": real_len})
        check(ops <= cap,
              f"|words|={len(words)} max 键长 {real_len}：ops {ops} ≤ κ {cap:g}",
              f"ops={ops} κ={cap}")

    # **哈希长度项**：`unordered_map<string,u64>` 的 get 必须按 4 记账（不是 ⊤）
    kh = ci.cost_of("hash_literals")
    check(kh.variables() == [], "hash_literals 的 κ 全折成常数", str(kh.variables()))
    text = kh.render()
    check("L(" not in text, "哈希长度项没有留下 `L(...)`", text)
    vh, opsh = _run(mod, "hash_literals", [])
    caph = kappa_at(kh, {})
    check(opsh <= caph, f"ops {opsh} ≤ κ {caph:g}（键长已计入）", f"ops={opsh} κ={caph}")
    print()


# ---------------------------------------------------------------- ④ ⊤
def test_top_boundary():
    print("[4] ④ 推不出就 ⊤：元素来自**非 const** 形参（可能被改长）")
    ci = _ci()
    check(_elem_bound(ci, "from_nonconst_param", "xs") is None,
          "`xs.push_back(src[0])`（src 非 const）⇒ 界 None（没猜）",
          str(_elem_bound(ci, "from_nonconst_param", "xs")))
    print()


def main():
    test_init_list()
    test_push_literals()
    test_derived_keys()
    test_top_boundary()

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
