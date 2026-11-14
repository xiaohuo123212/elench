#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_out_map.py —— **关联容器的出参增长**（`map` / `unordered_map` / `set`，P0-1 ①，2026-11-12）

## 为什么要这份测试

真实 C++ 里「用出参把**计数表**填满」和「把 vector 填满」一样常见：

    static void count_words(const vector<string>& words,
                            unordered_map<string,u64>& freq) {
        for (const auto& w : words) { freq[w] += 1; }      // ← 每轮至多插一个键
    }
    int main() {
        vector<string> cmds;  … push ×6 …
        unordered_map<string,u64> freq;   // **空的**：声明处推不出尺寸
        count_words(cmds, freq);          // ← 这里才把 freq 填满
        for (auto it = freq.begin(); it != freq.end(); ++it) …   // 半径来自 freq
    }

上一轮只做了 `vector`：`out.push(x)` 能推出「增长 = n(xs)」，但
`m[k] += 1` / `m.insert(k,v)` / `s.insert(x)` 一律 ⊤ —— 语料 `08_bits_hash`
（`count_words(cmds, freq)`）正是被这一点挡住的。

## 口径（与 vector 那条**同一套**内核，不是新造一条）

    |m| ≤ |m|₀ + Σ(插入点被执行次数)        （`_out_growth` 算的是后一项）

键插入的两个形状都算**一次**增长（每轮至多一个新键）：
  · 下标写 `m[k] = v` / `m[k] += 1`（C++ 语义：键不存在就插入）；
  · 方法 `m.insert(k, v)` / `s.insert(x)`（`_GROWTH_METHODS`）。

**第二遍扫描（本轮新增）**：`fill_map(keys, m)` 里 `keys` 是**只读**的，
所以 `n(keys)` 是进入时就有、且全程不变的尺寸量，可以当循环半径用。
`_out_growth_impl` 先按「所有出参尺寸都不可用」扫一遍（防止把"增长"冒充成
"尺寸"），再**只解锁增长恰为 0 的出参**扫第二遍。被写过的出参（`fill_self_map`
的 `m`）不解锁 —— 那正是 ⊤ 边界。

## 两类断言（只比表达式形状不算验证）

- **等价性**：同一段逻辑的三种写法（`+=` / `= m[k]+1` / `insert`）给**同一个
  尺寸上界**，解释器跑出来的**返回值逐字节相同**；同一份代码算两遍 κ 逐字节相同。
- **实测 ≤ 上界**：解释器真的跑一遍，`|m|` 的真实值 ≤ 上界、`ops ≤ κ`。

跑法：

    cd tests/cases; $env:PYTHONIOENCODING="utf-8"; python test_kappa_out_map.py
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


#: 声明侧：只给 `c_map` 一个真实契约。
#:
#: **为什么是 `n log n` 而不是 `n`**：有序 `map` 的每次插入是 AVL 查找
#: `O(log n)`（κ 里那两处 `log2(n(keys))`），所以 κ 的阶是 `n log n`。
#: `O(n)` 会判成「阶不够，比值 ≈ 105」——那是**如实**的，不是工具的毛病。
TU = """spec c_map(vector<u64>& keys) -> u64 {
    target { time: { max: O(keys.size() * log2(keys.size())) },
             allocation: { max: O(keys.size()) }, };
}
"""

#: 实现侧。**三种键插入写法**（`+=` / `= m[k]+1` / `insert`）+ 三种容器。
CH = """void fill_plus(vector<u64>& keys, map<u64,u64>& m) {
    u64 i = 0;
    while (i < keys.size()) { m[keys[i]] += 1; i = i + 1; }
}

void fill_assign(vector<u64>& keys, map<u64,u64>& m) {
    u64 i = 0;
    while (i < keys.size()) { m[keys[i]] = m[keys[i]] + 1; i = i + 1; }
}

void fill_insert(vector<u64>& keys, map<u64,u64>& m) {
    u64 i = 0;
    while (i < keys.size()) { m.insert(keys[i], 1); i = i + 1; }
}

void fill_set(const vector<u64>& xs, set<u64>& s) {
    u64 i = 0;
    while (i < xs.size()) { s.insert(xs[i]); i = i + 1; }
}

void fill_um(const vector<u64>& xs, unordered_map<u64,u64>& um) {
    for (auto& x : xs) { um[x] += 1; }
}

u64 c_map(vector<u64>& keys) {
    map<u64,u64> m;
    fill_plus(keys, m);
    return m.size();
}

u64 c_map_assign(vector<u64>& keys) {
    map<u64,u64> m;
    fill_assign(keys, m);
    return m.size();
}

u64 c_map_insert(vector<u64>& keys) {
    map<u64,u64> m;
    fill_insert(keys, m);
    return m.size();
}

u64 c_set(const vector<u64>& xs) {
    set<u64> s;
    fill_set(xs, s);
    return s.size();
}

u64 c_um(const vector<u64>& xs) {
    unordered_map<u64,u64> um;
    fill_um(xs, um);
    return um.size();
}

// **⊤ 边界**：增长依赖出参**自己的当前尺寸**（每轮插入都可能让条件更久成立）。
void fill_self_map(map<u64,u64>& m) {
    u64 i = 0;
    while (i < m.size()) { m[i] = 1; i = i + 1; }
}

u64 c_self_local(u64 k) {
    map<u64,u64> m;
    fill_self_map(m);
    return m.size();
}
"""


def _parse():
    return parse_source(CH, "outm.eln").module


def _ci():
    tu = parse_source(TU, "outm.tu").module
    return tu, CostInterp(_parse(), tu)


def _bounds(ci, fn_name):
    """局部容器 → 尺寸上界（`None` = 推不出）。"""
    fn = ci.fns[fn_name]
    b = ci._container_bounds_full(fn)[0]
    return {k: (None if v is None else v.render()) for k, v in b.items()}


def _growth(ci, fn_name):
    """被调方对**出参**的增长上界（`None` = 推不出）。"""
    fn = ci.fns[fn_name]
    g = ci._out_growth_impl(fn) or {}
    return {k: (None if v is None else v.render()) for k, v in g.items()}


def _run(fn_name, args):
    """解释器跑一次 → `(返回值, 该函数的 ops)`。"""
    tu = parse_source(TU, "outm.tu").module
    ch = _parse()
    ops_enable(True)
    try:
        ops_reset()
        val = Interp(ch, tu).call(fn_name, args)
        return val, ops_of(fn_name)
    finally:
        ops_enable(False)


# ---------------------------------------------------------------- ① 增长上界
def test_growth_bound():
    print("[1] `m[k] += 1` / `m.insert(k,v)` / `s.insert(x)`：每轮至多插一个键")
    _tu, ci = _ci()
    for fn, p, want in (("fill_plus", "m", "n(keys)"),
                        ("fill_assign", "m", "n(keys)"),
                        ("fill_insert", "m", "n(keys)"),
                        ("fill_set", "s", "n(xs)"),
                        ("fill_um", "um", "n(xs)")):
        g = _growth(ci, fn)
        check(g.get(p) == want,
              f"{fn}：|{p}| 的增长是 `{want}`（不是 ⊤）", str(g.get(p)))
    print()


# ---------------------------------------------------------------- ② 调用点
def test_call_site_bound():
    print("[2] 调用点用**实参的界**实例化：`|m| ≤ 0 + n(keys)`")
    _tu, ci = _ci()
    for fn, p, want in (("c_map", "m", "n(keys)"),
                        ("c_map_assign", "m", "n(keys)"),
                        ("c_map_insert", "m", "n(keys)"),
                        ("c_set", "s", "n(xs)"),
                        ("c_um", "um", "n(xs)")):
        b = _bounds(ci, fn)
        check(b.get(p) == want,
              f"{fn}：|{p}| ≤ {want}", str(b.get(p)))
    # **`keys` 是只读的出参**：增长恰为 0（第二遍扫描解锁 `n(keys)` 的前提）
    check(_growth(ci, "fill_plus").get("keys") == "0",
          "fill_plus：`keys` 没有被写（增长 = 0 ⇒ 它的尺寸可以当已知量）",
          str(_growth(ci, "fill_plus").get("keys")))
    print()


# ---------------------------------------------------------------- ③ κ 形状
def test_kappa_shape():
    print("[3] κ 不再是 ⊤：尺寸量是**调用方的输入**（不是被调方形参名）")
    _tu, ci = _ci()
    k = ci.cost_of("c_map")
    text = k.render()
    print(f"       κ(c_map) = {text}")
    check(not k.is_top(), "κ(c_map) 不是 ⊤", text)
    check("n(keys)" in text, "κ 里出现 `n(keys)`（出参增长被实例化了）", text)
    check(set(k.variables()) == {"keys"},
          "κ 只含输入尺寸量 `keys`（没有 `n(m)` 这种被调方名）",
          str(k.variables()))
    check("n(m)" not in text, "κ 里没有 `n(m)`（被调方形的名字不外泄）", text)
    # **达成**：`O(n log n)` 是这条 κ 的阶（有序 map 每次插入 AVL 查找）
    v = compare(k, ci.target_cost("c_map"))
    check(v.kind == "ok",
          f"`O(keys.size() * log2(keys.size()))` 判达成：{v.kind}", v.render())
    print()


# ---------------------------------------------------------------- ④ 等价性
def test_equivalence_guard():
    print("[4] 等价性守卫：三种键插入写法 —— 同一个尺寸上界 + 同一份运行结果")
    _tu, ci = _ci()
    bs = {fn: _bounds(ci, fn).get("m")
          for fn in ("c_map", "c_map_assign", "c_map_insert")}
    check(len(set(bs.values())) == 1 and set(bs.values()) == {"n(keys)"},
          "三种写法给**同一个** |m| 上界（与写法无关）", str(bs))
    # κ 里出现的**尺寸量集合**必须一样（不能漏出局部名 / 形参名）
    vs = {fn: set(ci.cost_of(fn).variables())
          for fn in ("c_map", "c_map_assign", "c_map_insert")}
    check(len({frozenset(v) for v in vs.values()}) == 1 and
          next(iter(vs.values())) == {"keys"},
          "三种写法的 κ 尺寸量集合相同（都只有 `keys`）", str(vs))
    # **同一份代码重新算一遍：κ 逐字节相同**（缓存必须幂等）
    _tu2, ci2 = _ci()
    check(ci2.cost_of("c_map").render() == ci.cost_of("c_map").render(),
          "同一份代码重新算一遍：κ 逐字节相同", ci.cost_of("c_map").render())
    # **运行结果逐字节相同**（同逻辑，三种写法）
    for n in (0, 1, 5, 12):
        keys = [i % 3 for i in range(n)]
        got = [_run(fn, [list(keys)])[0]
               for fn in ("c_map", "c_map_assign", "c_map_insert")]
        check(got[0] == got[1] == got[2],
              f"n={n}: 三种写法返回值逐字节相同（{got[0]}）", str(got))
    print()


# ---------------------------------------------------------------- ⑤ 实测
def test_measured_le_bound():
    print("[5] 实测 ≤ 上界：解释器真的跑一遍（不是只比表达式形状）")
    _tu, ci = _ci()
    k = ci.cost_of("c_map")
    print(f"       κ(c_map) = {k.render()}")
    for n in (0, 1, 5, 12):
        keys = [i % 3 for i in range(n)]        # 3 个不同的键
        got, _ops = _run("c_map", [list(keys)])
        cap = kappa_at(k, {"keys": n})
        check(got <= cap,
              f"n={n}: 真实 |m| = {got} ≤ 上界 {cap:g}", f"|m|={got} κ={cap}")
    for n in (1, 5, 12, 40):
        keys = [i % 3 for i in range(n)]
        _got, ops = _run("c_map", [list(keys)])
        cap = kappa_at(k, {"keys": n})
        check(ops <= cap,
              f"n={n}: ops {ops} ≤ κ {cap:g}（出参那一路没漏记）",
              f"ops={ops} κ={cap}")
    # 另外两种容器也跑一遍（形状不同，`set` 走 `insert`、`unordered_map` 走下标）
    k_set = ci.cost_of("c_set")
    for n in (1, 6):
        xs = [i % 4 for i in range(n)]
        got, ops = _run("c_set", [list(xs)])
        cap = kappa_at(k_set, {"xs": n})
        check(got <= cap and ops <= cap,
              f"c_set n={n}: 真实 |s| = {got} ≤ {cap:g}，ops {ops} ≤ κ",
              f"|s|={got} ops={ops} κ={cap}")
    k_um = ci.cost_of("c_um")
    for n in (1, 6):
        xs = [i % 4 for i in range(n)]
        got, ops = _run("c_um", [list(xs)])
        cap = kappa_at(k_um, {"xs": n})
        check(got <= cap and ops <= cap,
              f"c_um n={n}: 真实 |um| = {got} ≤ {cap:g}，ops {ops} ≤ κ",
              f"|um|={got} ops={ops} κ={cap}")
    print()


# ---------------------------------------------------------------- ⑥ ⊤ 边界
def test_top_boundaries():
    print("[6] ⊤ 边界：出参**被写过**就不再解锁它的尺寸（不许把增长冒充成尺寸）")
    _tu, ci = _ci()
    g = _growth(ci, "fill_self_map")
    check(g.get("m") is None,
          "`while (i < m.size()) m[i] = 1` ⇒ 增长 ⊤（轮数依赖出参自身）",
          str(g.get("m")))
    k = ci.cost_of("c_self_local")
    check(k.is_top(), "调用点跟着 ⊤（不拿一个假的界顶上）", k.render())
    check("?" not in k.render(), "⊤ 的渲染里没有 `n(?)`", k.render())
    print()


def main():
    test_growth_bound()
    test_call_site_bound()
    test_kappa_shape()
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
