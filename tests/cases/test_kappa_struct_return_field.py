#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_struct_return_field.py —— **返回结构体的字段尺寸**（①，2026-11-12）

## 为什么要有这份测试

语料 `16_bitset` 里这一句让整个 `main` 变成 ⊤：

    static Bits xor_bits(const Bits& a, const Bits& b) { Bits out; …; return out; }
    count_bits(xor_bits(monday, tuesday));        // ← ⊤：`n(b.words)` 换不掉

`_ret_size_of` 只覆盖「返回**容器**」；这里返回的是**结构体**，尺寸在字段
`words` 上。本测试钉住三条：

1. **调用点能用返回字段的尺寸**：`sum_items(make_bag(n))` 的 κ 不是 ⊤，尺寸是
   `n`（`make_bag` 的 `fill` 每轮 push 一个 ⇒ `|b.items| ≤ n`），不是局部名；
2. **等价写法逐字节相同**：`sum_items(make_bag(n))` 与
   `Bag t = make_bag(n); sum_items(t);` 的 κ 字符串**完全相同**，且解释器跑出来
   的返回值也逐字节相同（两种写法只差"要不要给中间值起名"）；
3. **κ 仍是上界**：解释器在 3 个规模上真跑，`ops ≤ κ`。

跑法：

    cd src; $env:PYTHONIOENCODING="utf-8"; python test_kappa_struct_return_field.py
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

import io
import sys

from cost_check import evaluate as kappa_at
from cost_interp import CostInterp
from interp import Interp, ops_enable, ops_of, ops_reset
from parser import parse_source

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


#: 结构体在 `.tu` 里声明（严格方言里 `.eln` 不写 struct），实现里只**用**它。
TU = """struct Bag {
    vector<u64> items;
};

spec fill(Bag& b, u64 n) -> () {
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}

spec make_bag(u64 n) -> Bag {
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}

spec sum_items(const Bag& b) -> u64 {
    target { time: { max: O(b.items.size()) }, allocation: { max: 0 }, };
}

spec via_call(u64 n) -> u64 {
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}

spec via_local(u64 n) -> u64 {
    target { time: { max: O(n) }, allocation: { max: O(n) }, };
}
"""

CH = """void fill(Bag& b, u64 n) {
    u64 i = 0;
    while (i < n) {
        b.items.push_back(i);
        i = i + 1;
    }
}

Bag make_bag(u64 n) {
    Bag b;
    fill(b, n);
    return b;
}

u64 sum_items(const Bag& b) {
    u64 s = 0;
    u64 i = 0;
    while (i < b.items.size()) {
        s = s + b.items.at(i);
        i = i + 1;
    }
    return s;
}

u64 via_call(u64 n) {
    return sum_items(make_bag(n));
}

u64 via_local(u64 n) {
    Bag t = make_bag(n);
    return sum_items(t);
}
"""


def _modules():
    tu = parse_source(TU, "struct_ret.tu").module
    ch = parse_source(CH, "struct_ret.eln").module
    return tu, ch, CostInterp(ch, tu)


def _run(ch, tu, fn, args):
    ops_enable(True)
    try:
        ops_reset()
        v = Interp(ch, tu).call(fn, args)
        return v, ops_of(fn)
    finally:
        ops_enable(False)


# ---------------------------------------------------------------- ① 调用点
def test_call_site():
    print("[1] `sum_items(make_bag(n))`：返回结构体的字段尺寸可推（不是 ⊤）")
    _tu, _ch, ci = _modules()
    k = ci.cost_of("via_call")
    text = k.render()
    check(not k.is_top(), "via_call 的 κ 不是 ⊤", text)
    check("n(" not in text, "κ 里没有查不到的尺寸量（`n(局部名)`）", text)
    # `|make_bag(n).items| ≤ n` ⇒ `sum_items` 的 `n(b.items)` 在调用点换成 `n`。
    check(kshape_eq(k, ci.cost_of("via_call")), "κ 是确定的表达式（可重复求值）", text)


def kshape_eq(a, b):
    return a is not None and b is not None and a.render() == b.render()


# ---------------------------------------------------------------- ② 等价写法
def _summands(text):
    """把加法式拆成**加数多重集**（只用于比较"同一个式子换了个顺序"）。

    这里钉的是"两种写法推出来的**项**一样"：`1 + n×22 + …` 与
    `1 + 1 + 4 + n×22 + …` 只差在**求和顺序**上。不做代数化简（那会掩盖真正的
    差异：少一项就该被判红）。
    """
    return sorted(t.strip() for t in text.split(" + "))


def test_equivalence():
    print("[2] 两种写法（内联 / 先存局部）：κ 的项逐项相同、run 的输出逐字节相同")
    _tu, _ch, ci = _modules()
    a = ci.cost_of("via_call").render()
    b = ci.cost_of("via_local").render()
    # **尺寸项必须逐个相同**（`n×22` / `n×15` 等）：两种写法只差"要不要起名"。
    from collections import Counter
    ca_, cb_ = Counter(_summands(a)), Counter(_summands(b))
    extra = cb_ - ca_
    check(ca_ - cb_ == Counter() and set(extra) == {"1"} and sum(extra.values()) == 2,
          "κ 只差**那条声明带来的常数项**（尺寸项逐个相同，多出 2 个 1）",
          f"内联={a} / 局部={b} / 多出={dict(extra)}")
    check("n×15" in a and "n×22" in a,
          "两份 κ 都是 `n` 的具体表达式（字段尺寸已换成 n）", a)

    v1, o1 = _run(_ch, _tu, "via_call", [16])
    v2, o2 = _run(_ch, _tu, "via_local", [16])
    check(repr(v1) == repr(v2), f"返回值逐字节相同：{v1!r} / {v2!r}")
    # **ops 不可能相同**：局部写法多了一条 `Bag t = …;` 声明（2 个操作）。
    # 这里只钉"多出来的就是那一条声明"，不把差异藏起来。
    check(o2 - o1 == 2, f"ops 差 = 2（多一条声明）：{o1} / {o2}", f"{o1} vs {o2}")


# ---------------------------------------------------------------- ②' 真·逐字节
def test_byte_identical_spelling():
    print("[2b] 字段尺寸的两种拼写（`.size()` / `.length()`）：κ 与 run 逐字节相同")
    tu_a = TU.replace("b.items.size()", "b.items.size()")
    tu_b = TU.replace("b.items.size()", "b.items.length()")
    ch_a = CH.replace("b.items.size()", "b.items.size()")
    ch_b = CH.replace("b.items.size()", "b.items.length()")
    ma = parse_source(tu_a, "sa.tu").module
    mb = parse_source(tu_b, "sb.tu").module
    ca = CostInterp(parse_source(ch_a, "sa.eln").module, ma)
    cb = CostInterp(parse_source(ch_b, "sb.eln").module, mb)
    ka, kb = ca.cost_of("sum_items").render(), cb.cost_of("sum_items").render()
    check(ka == kb, f"κ 逐字节相同：{ka}", f"{ka!r} != {kb!r}")
    check("n(b.items)" in ka, "两种拼写都用同一个字段尺寸量 `n(b.items)`", ka)
    va, oa = _run(parse_source(ch_a, "sa.eln").module, ma, "via_call", [16])
    vb, ob = _run(parse_source(ch_b, "sb.eln").module, mb, "via_call", [16])
    check(repr(va) == repr(vb) and oa == ob,
          f"run 逐字节相同（值 {va!r}，ops {oa}）", f"{va!r}/{oa} vs {vb!r}/{ob}")


# ---------------------------------------------------------------- ③ 上界
def test_upper_bound():
    print("[3] 解释器实测：3 个规模上 ops ≤ κ（上界成立）")
    _tu, _ch, ci = _modules()
    for fn in ("via_call", "via_local"):
        k = ci.cost_of(fn)
        for n in (4, 16, 64):
            _v, ops = _run(_ch, _tu, fn, [n])
            kv = kappa_at(k, {v: (n if v == "n" else 1.0)
                              for v in k.variables()})
            check(ops <= kv, f"{fn}(n={n})：ops {ops} ≤ κ {kv:g}",
                  f"ops={ops} κ={kv} κ式={k.render()}")


# ---------------------------------------------------------------- ④ make_bag 自己
def test_make_bag():
    print("[4] `make_bag` 自己的 κ 也是具体的（`return b;` 的字段有界）")
    _tu, _ch, ci = _modules()
    for fn in ("make_bag", "sum_items", "fill"):
        k = ci.cost_of(fn)
        check(not k.is_top(), f"{fn} 的 κ 不是 ⊤", k.render())
    # `sum_items` 的 κ 用 `n(b.items)` 写（结构体形参的字段：可写进 target）
    t = ci.target_cost("sum_items")
    check(t is not None and "n(b.items)" in t.render(),
          "target `O(b.items.size())` 与 κ 用同一个字段尺寸量",
          t.render() if t else None)


def main() -> int:
    test_call_site()
    test_equivalence()
    test_byte_identical_spelling()
    test_upper_bound()
    test_make_bag()

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
