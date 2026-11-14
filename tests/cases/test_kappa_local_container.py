#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_local_container.py —— 局部容器尺寸**归约到形参**（2026-11-12）

## 为什么要有这份测试

真实 C++ 的内核几乎都要"**建一个局部容器再遍历**"：

    u64 walk(map<u64,u64>& m) {
        for (auto it = m.begin(); it != m.end(); ++it) s = s + it->second;
    }

`--compat` 把它降级成 `vector<u64> ks = m.keys(); while (idx < ks.size()) …`。
修前 κ 里是 `n(ks)`——`ks` 是局部名，`target` 引用不到它，于是：

    κ = ⊤，报错「局部容器 'ks' 的尺寸无法在 target 中引用」

**这不是算不出阶，是界里的名字写不进契约**。本次给局部容器五条上界来源：

| # | 构造 | 上界 | 可证的理由 |
|---|---|---|---|
| ① | `ks = m.keys()/values()/items()/entries()/iter()` | `|ks| ≤ |m|` | 逐元素对应 |
| ② | `out.push*` 在"遍历 xs、每轮 ≤1 次"的循环体里 | `|out| ≤ n(xs)` | 增长预扫按轮数累计 |
| ③ | `p = split(s, sep)` | `|p| ≤ n(s)+1` | 分隔符每处最多一刀 |
| ④ | `filled(v,n)` / 数组字面量 | `n` / 常量 | 构造时已知 |
| ⑤ | 其余 | **⊤** | 不猜 |

**归约一直做到形参或常量**：`|ks| ≤ |cnt|` 且 `|cnt| ≤ n(ws)` 时继续追，
最后 κ 里只剩 `n(ws)`；追不到就放弃（⊤），MUST NOT 留 `n(局部)` 冒充尺寸量。

## 硬约束（本文件钉的就是它）

1. **κ 仍是上界**：解释器真的跑一遍，`ops ≤ κ`（不是"看起来像符号"就算过）。
2. **替换只能是"≥ 原界"的方向**：①③ 都是"至多一样长"；归约后的界必须
   ≥ 局部容器的**实际**尺寸（用解释器量出来），见 [8]。
3. **追不到形参就 ⊤**：`split(lines[i], …)` 的源是元素、长度与 `n(lines)` 无关，
   κ 必须退 ⊤ 并给原因，MUST NOT 编一个 `n(lines)`。

跑法：

    cd tests/cases; $env:PYTHONIOENCODING="utf-8"; python test_kappa_local_container.py
"""

import os as _os
import sys as _sys
import json as _json

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
from cost_interp import CostError, CostInterp             # noqa: E402
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


#: 声明侧：两个真实形状的 target（都写得比 κ 松一档，只为断言"判得了"）。
TU = """spec walk(map<u64,u64>& m) -> u64 {
    target { time: { max: O(m.size() * m.size()) }, allocation: { max: 0 }, };
}

spec maxfreq(vector<vector<u8>>& ws) -> u64 {
    target { time: { max: O(ws.size() * ws.size()) }, allocation: { max: 0 }, };
}
"""

#: 实现侧：与 `--compat` 降级后**逐字节同形**的手写版本（`test_compat` 钉着这条），
#: 外加 push / split / filled / "推不出" 四个形状。
CH = """u64 walk(map<u64,u64>& m) {
    vector<u64> ks = m.keys();
    u64 i = 0;
    u64 s = 0;
    while (i < ks.size()) { s = s + m.get(ks[i]); i = i + 1; }
    return s;
}

u64 maxfreq(vector<vector<u8>>& ws) {
    map<vector<u8>,u64> cnt;
    for (auto& w : ws) { cnt[w] = cnt[w] + 1; }
    vector<vector<u8>> ks = cnt.keys();
    u64 i = 0;
    u64 best = 0;
    while (i < ks.size()) { best = max(best, cnt.get(ks[i])); i = i + 1; }
    return best;
}

u64 cnt_size(vector<vector<u8>>& ws) {
    map<vector<u8>,u64> cnt;
    for (auto& w : ws) { cnt[w] = cnt[w] + 1; }
    return cnt.size();
}

u64 collect_sum(vector<u64>& xs) {
    vector<u64> out;
    for (auto& x : xs) { out.push(x); }
    u64 i = 0;
    u64 s = 0;
    while (i < out.size()) { s = s + out.at(i); i = i + 1; }
    return s;
}

u64 out_count(vector<u64>& xs) {
    vector<u64> out;
    for (auto& x : xs) { out.push(x); }
    return out.size();
}

u64 count_pieces(vector<u8>& s, vector<u8> sep) {
    vector<vector<u8>> p = text_split_vec(s, sep);
    u64 i = 0;
    u64 c = 0;
    while (i < p.size()) { c = c + 1; i = i + 1; }
    return c;
}

u64 fill_walk(u64 n) {
    vector<u64> v = vector<u64>::filled(0, n);
    u64 i = 0;
    u64 s = 0;
    while (i < v.size()) { s = s + v.at(i); i = i + 1; }
    return s;
}

u64 unprovable(vector<vector<u8>>& lines, u8 sep) {
    u64 total = 0;
    for (auto& ln : lines) {
        vector<vector<u8>> p = text_split_vec(ln, sep);
        u64 i = 0;
        while (i < p.size()) { total = total + 1; i = i + 1; }
    }
    return total;
}

u64 build_text(u64 n) {
    vector<u8> text;
    u64 i = 0;
    while (i < n) {
        text = text + "ab";
        i = i + 1;
    }
    return text.size();
}

vector<Interval> passthrough(vector<Interval>& xs) {
    vector<Interval> out = vector<Interval>::make();
    for (auto& x : xs) { out.push(x); }
    return out;
}
"""

#: `Interval` 只需要一个标量字段：这条只为钉「局部容器被 return」的形状。
TU = TU.replace(
    'spec walk(map<u64,u64>& m) -> u64 {',
    'struct Interval { start: u64, }\n\n'
    'spec passthrough(vector<Interval>& xs) -> vector<Interval> {\n'
    '    target { time: { max: O(xs.size()) }, allocation: { max: O(xs.size()) }, };\n'
    '}\n\n'
    'spec build_text(u64 n) -> u64 {\n'
    '    target { time: { max: O(n) }, allocation: { max: O(n) }, };\n'
    '}\n\n'
    'spec walk(map<u64,u64>& m) -> u64 {')


def _modules():
    tu = parse_source(TU, "local.tu").module
    ch = parse_source(CH, "local.eln").module
    return tu, ch, CostInterp(ch, tu)


def _bounds(ci, fn_name):
    """局部容器 → 尺寸上界（`None` = 推不出）。"""
    fn = ci.fns[fn_name]
    b = ci._container_bounds_full(fn)[0]
    return {k: (None if v is None else v.render()) for k, v in b.items()}


def _run_ops(fn_name, args):
    """解释器跑一次，返回 `(返回值, 该函数的 ops)`。"""
    _tu, ch, _ci = _modules()
    ops_enable(True)
    try:
        ops_reset()
        val = Interp(ch, _tu).call(fn_name, args)
        return val, ops_of(fn_name)
    finally:
        ops_enable(False)


# ---------------------------------------------------------------- ① keys
def test_keys_reduce():
    print("[1] `ks = m.keys()`：|ks| ≤ |m| ⇒ κ 里是 `n(m)`，不是 `n(ks)`")
    _tu, _ch, ci = _modules()
    k = ci.cost_of("walk")
    text = k.render()
    check(k.kind != "unknown", "walk 的 κ 不是 ⊤", text)
    check("n(m)" in text, "κ 含 `n(m)`（尺寸量归约到了形参）", text)
    check("n(ks)" not in text, "κ 里没有局部名 `n(ks)`", text)
    b = _bounds(ci, "walk")
    check(b.get("ks") == "n(m)", "|ks| 的上界正是 `n(m)`", str(b.get("ks")))
    check(k.variables() == ["m"], "κ 的尺寸量只剩形参 `m`", str(k.variables()))
    v = compare(k, ci.target_cost("walk"))
    check(v.kind == "ok", f"target `O(m.size()*m.size())` 判达成：{v.kind}", v.render())


# ---------------------------------------------------------------- ① 真实 C++ 探针
def test_cpp_iterator_probe():
    print("[2] 真实 C++：`for (auto it = m.begin(); …)`（--compat 降级）的 κ 与 ops")
    import compat
    cpp = """#include <map>
using u64 = unsigned long long;

u64 walk(std::map<u64,u64>& m) {
    u64 s = 0;
    for (auto it = m.begin(); it != m.end(); ++it) { s = s + it->second; }
    return s;
}

u64 main() {
    std::map<u64,u64> m;
    u64 i = 0;
    while (i < NNN) { m.insert(i, i * 2); i = i + 1; }
    return walk(m);
}
"""
    prev = compat.enabled()
    compat.set_enabled(True)
    try:
        for n in (1, 4, 8):
            mod = parse_source(cpp.replace("NNN", str(n)), "cpp_walk.eln").module
            ci = CostInterp(mod, None)
            k = ci.cost_of("walk")
            text = k.render()
            if n == 8:
                check(k.kind != "unknown", "C++ 迭代器探针：κ 不是 ⊤", text)
                check("n(ks)" not in text, "C++ 迭代器探针：κ 里没有局部快照名", text)
            ops_enable(True)
            try:
                ops_reset()
                got = Interp(mod, None).call("main", [])
                ops = ops_of("walk")
            finally:
                ops_enable(False)
            kv = kappa_at(k, {"m": n})
            check(ops <= kv, f"n={n}: ops {ops} ≤ κ {kv:g}（κ 仍是上界）",
                  f"ops={ops} κ={kv} 值={got}")
    finally:
        compat.set_enabled(prev)


# ---------------------------------------------------------------- ②+① 链式归约
def test_chain_to_param():
    print("[3] 链式归约：`cnt`（② push）→ `ks = cnt.keys()`（①）→ 形参 `ws`")
    _tu, _ch, ci = _modules()
    k = ci.cost_of("maxfreq")
    text = k.render()
    check(k.kind != "unknown", "maxfreq 的 κ 不是 ⊤", text)
    check("n(ws)" in text, "κ 含 `n(ws)`（两个局部都归约到了形参）", text)
    check("n(cnt)" not in text and "n(ks)" not in text,
          "κ 里没有 `n(cnt)` / `n(ks)`", text)
    for nm in ("cnt", "ks"):
        b = _bounds(ci, "maxfreq").get(nm)
        check(b == "n(ws)", f"|{nm}| 的上界是 `n(ws)`（链追到了形参）", str(b))
    check(k.variables() == ["ws"], "κ 的尺寸量只剩形参 `ws`", str(k.variables()))
    v = compare(k, ci.target_cost("maxfreq"))
    check(v.kind == "ok", f"target 判达成：{v.kind}", v.render())

    # **实际值 ≤ 界**：ws 里有重复词，cnt 的真实长度比 n(ws) 小（严格变松的方向）。
    ws = [[97], [98], [97], [97], [99], [98], [97]]
    got = _run_ops("cnt_size", [ws])[0]
    bnd = kappa_at(ci._container_bounds_full(ci.fns["maxfreq"])[0]["cnt"],
                   {"ws": len(ws)})
    check(got <= bnd, f"真实 |cnt| = {got} ≤ 界 n(ws) = {bnd:g}（方向正确）",
          f"got={got} bnd={bnd}")


# ---------------------------------------------------------------- ② push
def test_push_rule():
    print("[4] `out.push` 在遍历 xs 的循环体里（每轮 ≤1 次）⇒ |out| ≤ n(xs)")
    _tu, _ch, ci = _modules()
    k = ci.cost_of("collect_sum")
    text = k.render()
    check("n(xs)" in text and "n(out)" not in text,
          "κ 用 `n(xs)`，没有 `n(out)`", text)
    check(_bounds(ci, "collect_sum").get("out") == "n(xs)",
          "|out| 的上界是 `n(xs)`", str(_bounds(ci, "collect_sum").get("out")))
    for n in (1, 3, 7):
        got, ops = _run_ops("out_count", [list(range(n))])
        bnd = kappa_at(ci._container_bounds_full(ci.fns["collect_sum"])[0]["out"],
                       {"xs": n})
        check(got <= bnd, f"n={n}: 真实 |out| = {got} ≤ 上界 {bnd:g}", f"{got}>{bnd}")
        kv = kappa_at(k, {"xs": n})
        check(ops <= kv, f"n={n}: ops {ops} ≤ κ {kv:g}", f"ops={ops} κ={kv}")


# ---------------------------------------------------------------- ③ split
def test_split_rule():
    print("[5] `p = split(s, sep)` ⇒ |p| ≤ n(s)+1（每个分隔符最多切一刀）")
    _tu, _ch, ci = _modules()
    k = ci.cost_of("count_pieces")
    text = k.render()
    check("n(p)" not in text, "κ 里没有局部名 `n(p)`", text)
    check(_bounds(ci, "count_pieces").get("p") == "n(s) + 1",
          "|p| 的上界是 `n(s) + 1`", str(_bounds(ci, "count_pieces").get("p")))
    # `a,b,c`：n(s)=5、真实段数 3 ≤ 6。
    for data in ([97], [97, 44], [97, 44, 98], [97, 44, 98, 44, 99]):
        got = _run_ops("count_pieces", [list(data), [44]])[0]
        bnd = kappa_at(ci._container_bounds_full(ci.fns["count_pieces"])[0]["p"],
                       {"s": len(data)})
        check(got <= bnd, f"|s|={len(data)}: 真实段数 {got} ≤ 上界 {bnd:g}",
              f"{got}>{bnd}")


# ---------------------------------------------------------------- ④ filled
def test_filled_rule():
    print("[6] `vector<u64>::filled(0, n)` ⇒ 尺寸就是 `n`（标量形参，不是局部名）")
    _tu, _ch, ci = _modules()
    k = ci.cost_of("fill_walk")
    text = k.render()
    check("n(v)" not in text, "κ 里没有 `n(v)`", text)
    check(_bounds(ci, "fill_walk").get("v") == "n",
          "|v| 的上界是标量形参 `n`", str(_bounds(ci, "fill_walk").get("v")))
    check(k.variables() == ["n"], "κ 的尺寸量只剩标量形参 `n`", str(k.variables()))
    for n in (2, 5):
        _v, ops = _run_ops("fill_walk", [n])
        kv = kappa_at(k, {"n": n})
        check(ops <= kv, f"n={n}: ops {ops} ≤ κ {kv:g}", f"ops={ops} κ={kv}")


# ---------------------------------------------------------------- ⑤ 不猜
def test_unprovable_is_top():
    print("[7] 推不出（split 的源是元素 `lines[i]`）⇒ ⊤ + 原因，不许编 `n(lines)`")
    _tu, _ch, ci = _modules()
    try:
        k = ci.cost_of("unprovable")
        text = k.render()
        check(k.is_top(), "κ 是 ⊤", text)
    except CostError as e:
        msg = str(e)
        check("局部容器" in msg and "'p'" in msg,
              "CostError 指名局部容器 'p'（CLI 会把它显示成 κ=⊤ 的原因）", msg)
    b = _bounds(ci, "unprovable")
    check(b.get("p") is None, "|p| 的界是 None（没猜）", str(b.get("p")))
    check("n(lines)" not in str(b), "界里没有编造 `n(lines)`", str(b))


# ---------------------------------------------------------------- ④' 串接
def test_concat_growth():
    print("[8] `text = text + \"ab\"` ⇒ 每执行一次加 `|ab|`；`return out;` 的局部也归约")
    _tu, _ch, ci = _modules()

    k = ci.cost_of("build_text")
    text = k.render()
    check(k.kind != "unknown", "build_text 的 κ 不是 ⊤", text)
    check(_bounds(ci, "build_text").get("text") == "n×2",
          "|text| 的上界是 `n×2`（自引用串接按**执行次数**累加，不是只加一次）",
          str(_bounds(ci, "build_text").get("text")))
    # **累加必须真的累加**：只做 `max(旧, 新)` 会得到常数 2，那是低估。
    for n in (1, 4):
        got, ops = _run_ops("build_text", [n])
        check(got == 2 * n, f"n={n}: 真实长度 {got} = 2n", f"got={got}")
        bnd = kappa_at(ci._container_bounds_full(ci.fns["build_text"])[0]["text"],
                       {"n": n})
        check(got <= bnd, f"n={n}: 真实长度 {got} ≤ 上界 {bnd:g}", f"{got}>{bnd}")
        kv = kappa_at(k, {"n": n, "text": got})
        check(ops <= kv, f"n={n}: ops {ops} ≤ κ {kv:g}", f"ops={ops} κ={kv}")

    kp = ci.cost_of("passthrough")
    tp = kp.render()
    check(kp.kind != "unknown", "passthrough 的 κ 不是 ⊤", tp)
    check("n(out)" not in tp, "κ 里没有局部名 `n(out)`（被 return 的也算）", tp)
    check(_bounds(ci, "passthrough").get("out") == "n(xs)",
          "`out` 的上界是 `n(xs)`", str(_bounds(ci, "passthrough").get("out")))


# ---------------------------------------------------------------- ④ 计数表（map/set）
#
# **2026-11-12 KPI③ 的唯一失败项**（AI 真实提交 05）：
#
#     map<u64, u64> cnt;                       // 局部空表
#     for (const auto& x : xs) {
#         if (cnt.contains(x)) { cnt.put(x, cnt.get(x) + 1); }
#         else                 { cnt.insert(x, 1); }
#     }
#
# κ 里是 `log2(n(cnt))`，而 `cnt` 是**局部名**，target 只能写 `xs.size()`
# ⇒ E23「尺寸量 ['cnt','xs'] 与 ['xs'] 不一致」⇒ 永远判不了。
#
# **规则④与②是同一族、同一份增长预扫**：`insert/put/add/emplace` 已经在
# `_GROWTH_METHODS` 里、`m[k] = v` 已经按 map 记增长，所以 `|cnt| ≤ n(xs)`
# 这一条界**本来就由增长预扫算出来了**（见 [9] 的 `_bounds` 断言）；
# 新增的只是"敢不敢替换"的判据 —— **键注入**：
#
#     每个（循环里的）插入点的键都是被遍历容器 C 的元素
#         ⇒ 键集合 ⊆ C 的元素集合 ⇒ |cnt| ≤ |C|
#
# 它与**插入次数**无关（同键写 n 次，`|cnt|` 也不涨），这正是 map 与 vector
# 的差别；规则②靠"轮数"，规则④靠"键的集合"。
CT = """
u64 best_put(const vector<u64>& xs) {
    map<u64, u64> cnt;
    u64 best = 0;
    for (const auto& x : xs) {
        if (cnt.contains(x)) { cnt.put(x, cnt.get(x) + 1); }
        else { cnt.insert(x, 1); }
        u64 c = cnt.get(x);
        if (c > best) { best = c; }
    }
    return best;
}

u64 best_put_same(const vector<u64>& xs) {
    map<u64, u64> cnt;
    u64 best = 0;
    for (const auto& x : xs) {
        if (cnt.contains(x)) { cnt.put(x, cnt.get(x) + 1); }
        else { cnt.put(x, 1); }
        u64 c = cnt.get(x);
        if (c > best) { best = c; }
    }
    return best;
}

u64 best_index(const vector<u64>& xs) {
    map<u64, u64> cnt;
    u64 best = 0;
    for (const auto& x : xs) {
        cnt[x] += 1;
        u64 c = cnt.get(x);
        if (c > best) { best = c; }
    }
    return best;
}

u64 best_while(const vector<u64>& xs) {
    map<u64, u64> cnt;
    u64 i = 0;
    u64 best = 0;
    while (i < xs.size()) {
        u64 x = xs.at(i);
        if (cnt.contains(x)) { cnt.put(x, cnt.get(x) + 1); }
        else { cnt.insert(x, 1); }
        u64 c = cnt.get(x);
        if (c > best) { best = c; }
        i = i + 1;
    }
    return best;
}

u64 best_preloop(const vector<u64>& xs) {
    map<u64, u64> cnt;
    cnt.insert(999, 0);
    u64 best = 0;
    for (const auto& x : xs) {
        cnt[x] += 1;
        u64 c = cnt.get(x);
        if (c > best) { best = c; }
    }
    return best;
}

u64 cnt_len(const vector<u64>& xs) {
    map<u64, u64> cnt;
    for (const auto& x : xs) {
        if (cnt.contains(x)) { cnt.put(x, cnt.get(x) + 1); }
        else { cnt.insert(x, 1); }
    }
    return cnt.size();
}

u64 lit_key(const vector<u64>& xs) {
    map<u64, u64> cnt;
    for (const auto& x : xs) { cnt.insert(x, 1); cnt.insert(7, 1); }
    return cnt.size();
}

u64 counter_key(u64 n) {
    map<u64, u64> m = map<u64, u64>::new();
    u64 i = 0;
    while (i < n) { m[i] = i; i = i + 1; }
    return m.length();
}
"""

#: 每个函数都配 `O(n log n)` 的 target：判"达成"才算**归约到位**（不是"看着像"）。
CT_TU = """spec best_put(const vector<u64>& xs) -> u64 {
    target { time: { max: O(xs.size() * log2(xs.size())) }, allocation: { max: O(xs.size()) }, };
}
spec best_put_same(const vector<u64>& xs) -> u64 {
    target { time: { max: O(xs.size() * log2(xs.size())) }, allocation: { max: O(xs.size()) }, };
}
spec best_index(const vector<u64>& xs) -> u64 {
    target { time: { max: O(xs.size() * log2(xs.size())) }, allocation: { max: O(xs.size()) }, };
}
spec best_while(const vector<u64>& xs) -> u64 {
    target { time: { max: O(xs.size() * log2(xs.size())) }, allocation: { max: O(xs.size()) }, };
}
spec best_preloop(const vector<u64>& xs) -> u64 {
    target { time: { max: O(xs.size() * log2(xs.size())) }, allocation: { max: O(xs.size()) }, };
}
"""


def _ct_modules():
    return (parse_source(CT_TU, "ct.tu").module,
            parse_source(CT, "ct.eln").module)


def _ct_ci():
    tu, ch = _ct_modules()
    return CostInterp(ch, tu)


def _ct_run(fn_name, args):
    """解释器跑一次 `CT` 里的函数，返回 `(返回值, 该函数的 ops)`。"""
    tu, ch = _ct_modules()
    ops_enable(True)
    try:
        ops_reset()
        val = Interp(ch, tu).call(fn_name, args)
        return val, ops_of(fn_name)
    finally:
        ops_enable(False)


def test_counting_table_rule():
    print("[9] 局部 map/set 在遍历 xs 的循环里插入、键取自 xs 的元素 ⇒ |cnt| ≤ n(xs)")
    ci = _ct_ci()
    k = ci.cost_of("best_put")
    text = k.render()
    check(k.kind != "unknown", "κ 不是 ⊤", text)
    check("n(cnt)" not in text, "κ 里没有局部名 `n(cnt)`", text)
    check("log2(n(xs))" in text,
          "map 操作的 log2 参数已归约到形参 `n(xs)`", text)
    check(k.variables() == ["xs"], "κ 的尺寸量只剩形参 `xs`", str(k.variables()))
    # **界来自增长预扫**（规则②那一份），不是新造一套：`_bounds` 直接读它。
    check(_bounds(ci, "best_put").get("cnt") == "n(xs)",
          "|cnt| 的上界正是增长预扫给的 `n(xs)`",
          str(_bounds(ci, "best_put").get("cnt")))
    v = compare(k, ci.target_cost("best_put"))
    check(v.kind == "ok", f"target `O(n log n)` 判达成：{v.kind}", v.render())

    # **等价性 ①**：同逻辑只把 `insert` 写成 `put` ⇒ κ 逐字节相同。
    a = ci.cost_of("best_put")
    a2 = ci.cost_of("best_put_same")
    check(a.render() == a2.render(), "insert vs put：κ 文本逐字节相同",
          f"\n  {a.render()}\n  {a2.render()}")
    check(_json.dumps(a.to_dict(), sort_keys=True)
          == _json.dumps(a2.to_dict(), sort_keys=True),
          "insert vs put：κ 结构（to_dict）也相同")

    # **等价性 ②**：`contains/get/put` 与 `cnt[x] += 1`、`while` 三种写法是同一逻辑
    # ⇒ 解释器跑出来的值逐字节相同（含重复键：重复才压得到"键集合"这条理由）。
    for xs in ([7], [7, 7, 9, 9], [i % 3 for i in range(16)], list(range(9))):
        got = [_ct_run(fn, [list(xs)])[0]
               for fn in ("best_put", "best_index", "best_while")]
        check(got[0] == got[1] == got[2],
              f"n={len(xs)}: 三种写法 run 结果相同（{got[0]}）", str(got))

    # **实测：真实 |cnt| ≤ 推出的上界**（≥3 个规模）+ `ops ≤ κ`。
    bound = ci._container_bounds_full(ci.fns["best_put"])[0]["cnt"]
    for n in (1, 4, 16, 64):
        xs = [i % 3 for i in range(n)]
        got, _ops = _ct_run("cnt_len", [xs])
        bnd = kappa_at(bound, {"xs": n})
        check(got <= bnd, f"n={n}: 真实 |cnt| = {got} ≤ 上界 {bnd:g}", f"{got}>{bnd}")
        _v, ops = _ct_run("best_put", [xs])
        kv = kappa_at(k, {"xs": n})
        check(ops <= kv, f"n={n}: ops {ops} ≤ κ {kv:g}", f"ops={ops} κ={kv}")
    # 全不同键时上界**取到等号**（说明它是紧的，不是随手放大）。
    got, _ops = _ct_run("cnt_len", [list(range(16))])
    bnd = kappa_at(bound, {"xs": 16})
    check(got == bnd == 16, f"全不同键：真实 |cnt| = {got} = 上界 {bnd:g}", f"{got}/{bnd}")

    # **循环外的前置插入 ⇒ 界 +1**（常数，不影响阶）：`cnt.insert(999, 0)`。
    check(_bounds(ci, "best_preloop").get("cnt") == "1 + n(xs)",
          "前置插入：上界是 `1 + n(xs)`",
          str(_bounds(ci, "best_preloop").get("cnt")))
    kp = ci.cost_of("best_preloop")
    check("n(cnt)" not in kp.render(), "前置插入：κ 里仍没有局部名 `n(cnt)`",
          kp.render())
    vp = compare(kp, ci.target_cost("best_preloop"))
    check(vp.kind == "ok", f"前置插入：target 仍判达成：{vp.kind}", vp.render())

    # **不猜的两格**（形状对、理由不对 ⇒ 保持原样，由 CLI 判 E23）
    kl = ci.cost_of("lit_key")
    check("n(cnt)" in kl.render(),
          "循环里还有**字面量键**⇒ 键注入不成立 ⇒ `n(cnt)` 留在 κ 里（不猜）",
          kl.render())
    check("cnt" not in ci._local_container_bounds(ci.fns["lit_key"])[1],
          "lit_key 的 `cnt` 不在 driver 集合里",
          str(ci._local_container_bounds(ci.fns["lit_key"])[1]))
    kc = ci.cost_of("counter_key")
    check("log2(n(m))" in kc.render(),
          "D1 形状（键是**循环计数器**）仍保持 `log2(n(m))`（test_map_cost 钉着）",
          kc.render())
    check("m" not in ci._local_container_bounds(ci.fns["counter_key"])[1],
          "counter_key 的 `m` 不在 driver 集合里",
          str(ci._local_container_bounds(ci.fns["counter_key"])[1]))


def main() -> int:
    test_keys_reduce()
    test_cpp_iterator_probe()
    test_chain_to_param()
    test_push_rule()
    test_split_rule()
    test_filled_rule()
    test_unprovable_is_top()
    test_concat_growth()
    test_counting_table_rule()

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
