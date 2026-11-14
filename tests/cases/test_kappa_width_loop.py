#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_width_loop.py —— **位宽有界**的循环（2026-11-12）

## 靶子

    static u64 count_bits(const Bits& b) {
        for (u64 w = 0; w < b.words.size(); ++w) {
            u64 x = b.words[w];
            while (x != 0) {          // ← 旧口径：条件数据相关 ⇒ κ = ⊤
                x = x & (x - 1);      //   Kernighan：每轮清掉最低位的 1
                n += 1;
            }
        }
        return n;
    }

`while (x != 0)` 不是尺寸比较，旧规则抽不出轮数，于是 `count_bits` 整条 κ
退成 ⊤。但下面三种形状的圈数**与数据无关**地 ≤ 类型位宽：

| 更新 | 每轮严格减少 | 圈数上界 |
|---|---|---|
| `x & (x - 1)` | popcount 减 **1**（清掉最低位的 1） | `popcount(x₀) ≤ W` |
| `x >> k`（k ≥ 1） | 位长至少减 **1** | `bitlen(x₀) ≤ W` |
| `x / k`（k ≥ 2，**无符号**） | 位长至少减 **1**（`x/k ≤ x/2`） | `bitlen(x₀) ≤ W` |

`W` 取**类型位宽**：`u8`→8 / `u16`→16 / `u32`→32 / `u64`→64。

条件认 `x != 0`（以及 `0 != x`）与**无符号**的 `x > 0`（它与 `x != 0` 等价）；
`x != c` / `x > c`（c ≠ 0）不是这个形状（前者退 ⊤，后者仍由旧的 `x - c` 兜底）。

## 硬约束（本文件钉的就是它）

1. **κ 必须是上界**：解释器真的跑 `0` / `1` / `0xFFFF…` / `0xAAAA…` 四种输入，
   `真实轮数 ≤ W` **且** `ops ≤ κ`。只比表达式形状不算验证。
2. **同逻辑两种写法必须逐字节相同**：`x = x & (x - 1)` / `(x - 1) & x` /
   `x &= (x - 1)` 的 κ 与返回值、ops 必须完全一样；`>> 1` / `>>= 1` 同理。
   拼写不该改成本（`&=` / `>>=` 在 `--compat` 下被 parser 降级成同一个 assign）。
3. **必须拒绝的形状一律 ⊤**（不许猜）：有符号 `i64`、体内多处写 `x`、
   只有 `x = x - 1`、条件 `x != 1`、更新写在 `if` 里、`break` / `continue`、
   `x >> 0`、`&x` 传出、`y != 0` 却写 `x`。
4. **语料靶子**：`demo/corpus/16_bitset.cpp` 的 `count_bits` 从 ⊤ 变成常数系数，
   并且逐字跑它（4 组位图）`ops ≤ κ`、返回值 = 真实位数。

跑法：

    cd src; $env:PYTHONIOENCODING="utf-8"; python test_kappa_width_loop.py
"""

import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，这批 κ 测试的惯例是 `HERE = src/`
#: （见 `test_kappa_field_size.py` 同一段），路径按仓库根推。
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
import shutil
import subprocess
import tempfile
import compat                                            # noqa: E402
from bound import width_bounded_loop                     # noqa: E402
from cost_check import evaluate as kappa_at              # noqa: E402
from cost_interp import CostInterp                       # noqa: E402
from interp import (Interp, StructVal, ops_enable,       # noqa: E402
                    ops_of, ops_reset)
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


# ---------------------------------------------------------------- 受试模块
#
# 三种形状 + 三种等价拼写 + 三种宽度；它们在**同一份模块**里，这样
# `Interp` 只建一次、每个函数的输入都一样。
SRC_ALL = r"""
u64 pop_assign(u64 x) {
    u64 n = 0;
    while (x != 0) { x = x & (x - 1); n += 1; }
    return n;
}

// 交集可交换：`(x - 1) & x` 与上面**逐字节同成本**。
u64 pop_swap(u64 x) {
    u64 n = 0;
    while (x != 0) { x = (x - 1) & x; n += 1; }
    return n;
}

// `&=` 在 `--compat` 下被 parser 降级成 `x = x & (x - 1)`（同一个 assign）。
u64 pop_compound(u64 x) {
    u64 n = 0;
    while (x != 0) { x &= (x - 1); n += 1; }
    return n;
}

u64 shift_assign(u64 x) {
    u64 n = 0;
    while (x != 0) { x = x >> 1; n += 1; }
    return n;
}

// `>>=` 同样降级成 `x = x >> 1`。
u64 shift_compound(u64 x) {
    u64 n = 0;
    while (x != 0) { x >>= 1; n += 1; }
    return n;
}

u64 div_assign(u64 x) {
    u64 n = 0;
    while (x != 0) { x = x / 2; n += 1; }
    return n;
}

// `x > 0`：**无符号**下与 `x != 0` 等价，同样给位宽上界（有符号会落到
// 旧的 `x - c` 上界，见 [5]）。
u64 gt0_shift(u64 x) {
    u64 n = 0;
    while (x > 0) { x = x >> 1; n += 1; }
    return n;
}

u64 w8_pop(u8 x) {
    u64 n = 0;
    while (x != 0) { x = x & (x - 1); n += 1; }
    return n;
}

u64 w16_shift(u16 x) {
    u64 n = 0;
    while (x != 0) { x = x >> 1; n += 1; }
    return n;
}

u64 w32_div(u32 x) {
    u64 n = 0;
    while (x != 0) { x = x / 2; n += 1; }
    return n;
}

// ---------------- 以下全部必须 ⊤ ----------------
// 有符号：右移实现定义、除法收敛性不等价。
u64 r_signed_shift(i64 x) {
    u64 n = 0;
    while (x != 0) { x = x >> 1; n += 1; }
    return n;
}

u64 r_signed_div(i64 x) {
    u64 n = 0;
    while (x != 0) { x = x / 2; n += 1; }
    return n;
}

// 体内还有第二处写 x ⇒「每轮至少少一位」被打断。
u64 r_extra_write(u64 x) {
    u64 n = 0;
    while (x != 0) { x = x & (x - 1); x = x - 1; n += 1; }
    return n;
}

// 只有 `x = x - 1`：单独论证过才收，默认不认。
u64 r_minus_one(u64 x) {
    u64 n = 0;
    while (x != 0) { x = x - 1; n += 1; }
    return n;
}

// 条件 `x != 1`：不是「减到 0 为止」，圈数与位宽无关。
u64 r_ne_one(u64 x) {
    u64 n = 0;
    while (x != 1) { x = x >> 1; n += 1; }
    return n;
}

// `x > 5` 不是本规则的形状（c ≠ 0）：落入旧的 `x - c` 上界，见 [5]。
u64 r_gt(u64 x) {
    u64 n = 0;
    while (x > 5) { x = x >> 1; n += 1; }
    return n;
}

// 循环变量是 y，体内改的却是 x ⇒ 不推进 y（形状判据之外的另一层）。
u64 r_other_var(u64 x, u64 y) {
    u64 n = 0;
    while (y != 0) { x = x >> 1; n += 1; }
    return n;
}

// 更新写在 if 里：可能不执行 ⇒ 不成立。
u64 r_in_if(u64 x, u64 c) {
    u64 n = 0;
    while (x != 0) { if (c > 0) { x = x >> 1; } n += 1; }
    return n;
}

u64 r_break(u64 x) {
    u64 n = 0;
    while (x != 0) { x = x >> 1; if (n > 3) { break; } n += 1; }
    return n;
}

u64 r_continue(u64 x) {
    u64 n = 0;
    while (x != 0) { if (n > 3) { n += 1; continue; } x = x >> 1; n += 1; }
    return n;
}

// `x >> 0` 原地不动 = 死循环。
u64 r_shift0(u64 x) {
    u64 n = 0;
    while (x != 0) { x = x >> 0; n += 1; }
    return n;
}

// 按引用传出：被调方可能改 x。
u64 take_ref(u64& y) { y = y >> 1; return y; }
u64 r_ref(u64 x) {
    u64 n = 0;
    while (x != 0) { u64 t = take_ref(&x); n += t; }
    return n;
}
"""

#: 四种极端输入（0 / 1 / 全 1 / 交替位）。
INPUTS = (0, 1, (1 << 64) - 1, 0xAAAAAAAAAAAAAAAA)

#: 三种形状（外加 `x > 0` 的等价条件）+ 每轮减少的位。
SHAPES = (("pop_assign", "clear-lowest-bit", 64),
          ("shift_assign", "shift-1", 64),
          ("div_assign", "div-2", 64),
          ("gt0_shift", "shift-1", 64))

#: 宽度：函数名 → (类型, 位宽, 满位输入)。
WIDTHS = (("w8_pop", "u8", 8, 255),
          ("w16_shift", "u16", 16, 65535),
          ("w32_div", "u32", 32, 4294967295))

#: 必须拒绝：函数名 → 拒因里应出现的关键词。
REJECTS = (
    ("r_signed_shift", "有符号整数"),
    ("r_signed_div", "有符号整数"),
    ("r_extra_write", "处写"),
    ("r_minus_one", "每轮至少少一位"),
    ("r_ne_one", "减到 0 为止"),
    ("r_other_var", "处写"),
    ("r_in_if", "直接子语句"),
    ("r_break", "break"),
    ("r_continue", "continue"),
    ("r_shift0", "每轮至少少一位"),
    ("r_ref", "取引用传出"),
)


def _parse(src, name):
    """`--compat` 打开的解析（`&=` / `>>=` 的降级在 parser 里）。"""
    prev = compat.enabled()
    compat.set_enabled(True)
    try:
        return parse_source(src, name).module
    finally:
        compat.set_enabled(prev)


def _mods():
    mod = _parse(SRC_ALL, "width_loop.eln")
    return mod, CostInterp(mod, None)


def _fn_node(mod, name):
    return next((c for c in mod.children
                 if getattr(c, "kind", "") == "fn" and getattr(c, "name", "") == name),
                None)


def _whiles_of(fn_node):
    """函数里**所有** while（`for` 在 parser 里会被降级成 while）。"""
    out = []
    stack = [fn_node]
    while stack:
        n = stack.pop(0)
        if getattr(n, "kind", "") == "while":
            out.append(n)
        stack.extend(getattr(n, "children", None) or [])
    return out


def _width_bound(mod, fn_name):
    """`(界, 原因, 是否命中 `x != 0`)`——直接问判据本体。

    **要扫完所有 while**：`for (u64 w = 0; …)` 在 parser 里降级成 while，
    只看"第一个"会落在那个循环上（它的条件不是 `!= 0`）。
    """
    fn = _fn_node(mod, fn_name)
    for wh in _whiles_of(fn):
        b, why, hit = width_bounded_loop(wh, fn)
        if hit:
            return b, why, hit
    return None, "", False


def _run(mod, fn_name, args):
    """跑一次，返回 `(返回值, ops, 全部循环迭代次数)`。"""
    ops_enable(True)
    try:
        ops_reset()
        it = Interp(mod, None)
        val = it.call(fn_name, args)
        return val, ops_of(fn_name), it.iters
    finally:
        ops_enable(False)


# ---------------------------------------------------------------- ① 三种形状
def test_three_shapes():
    print("[1] 三种「每轮至少少一位」的形状 + `x > 0`：上界 = 64（不是 ⊤）")
    mod, ci = _mods()
    for fn, shape, width in SHAPES:
        k = ci.cost_of(fn)
        b, why, hit = _width_bound(mod, fn)
        check(not k.is_top(), f"{fn}：κ 不是 ⊤", k.render())
        check(hit and b is not None and getattr(b, "kind", "") == "const"
              and int(b.value) == width,
              f"{fn}：判据给常数上界 {width}", f"bound={b.render() if b else None}")
        check(shape in why, f"{fn}：原因里点明形状 `{shape}`", why)
        check(k.variables() == [],
              f"{fn}：κ 里没有尺寸量（位宽上界与数据无关）", str(k.variables()))


# ---------------------------------------------------------------- ② 实测上界
def test_measured():
    print("[2] 解释器实测：真实轮数 ≤ 64，且 ops ≤ κ（0 / 1 / 全 1 / 交替位）")
    mod, ci = _mods()
    for fn, _shape, width in SHAPES:
        k = ci.cost_of(fn)
        kv = kappa_at(k, {})
        check(kv == kv and kv != float("inf"), f"{fn}：κ 可求值（有限）", f"{kv}")
        for x in INPUTS:
            val, ops, iters = _run(mod, fn, [x])
            check(iters <= width,
                  f"{fn} x={x:#018x}：真实轮数 {iters} ≤ {width}", f"iters={iters}")
            check(val == iters,
                  f"{fn} x={x:#018x}：返回值 = 真实轮数（{iters}）", f"val={val}")
            check(ops <= kv,
                  f"{fn} x={x:#018x}：ops {ops} ≤ κ {kv:g}", f"ops={ops} κ={kv}")


# ---------------------------------------------------------------- ③ 等价拼写
def test_equivalent_spellings():
    print("[3] 同逻辑两种写法：κ 与 run 逐字节相同（拼写不改成本）")
    mod, ci = _mods()
    pairs = (("pop_assign", "pop_swap"),      # 交集可交换
             ("pop_assign", "pop_compound"),  # `&=` 降级
             ("shift_assign", "shift_compound"))  # `>>=` 降级
    for a, b in pairs:
        ka, kb = ci.cost_of(a), ci.cost_of(b)
        check(ka.render() == kb.render(),
              f"{a} 与 {b} 的 κ 逐字相同", f"{ka.render()} vs {kb.render()}")
        same = True
        detail = ""
        for x in INPUTS:
            va, oa, ia = _run(mod, a, [x])
            vb, ob, ib = _run(mod, b, [x])
            if (va, oa, ia) != (vb, ob, ib):
                same = False
                detail = (f"x={x:#x}: ({va},{oa},{ia}) vs ({vb},{ob},{ib})")
                break
        check(same, f"{a} 与 {b}：返回值 / ops / 轮数逐项相同", detail)


# ---------------------------------------------------------------- ④ 类型位宽
def test_type_widths():
    print("[4] 上界取**类型位宽**：u8→8 / u16→16 / u32→32")
    mod, ci = _mods()
    for fn, ty, width, full in WIDTHS:
        k = ci.cost_of(fn)
        b, why, hit = _width_bound(mod, fn)
        check(not k.is_top(), f"{fn}：κ 不是 ⊤", k.render())
        check(hit and b is not None and int(b.value) == width,
              f"{fn}（{ty}）：上界 {width}", f"bound={b.render() if b else None}")
        val, ops, iters = _run(mod, fn, [full])
        kv = kappa_at(k, {})
        check(iters <= width and val == iters,
              f"{fn} 满位输入：真实轮数 {iters} ≤ {width}", f"iters={iters}")
        check(ops <= kv, f"{fn}：ops {ops} ≤ κ {kv:g}", f"ops={ops} κ={kv}")


# ---------------------------------------------------------------- ⑤ 拒绝形状
def test_rejects():
    print("[5] 推不出的形状一律 ⊤（有符号 / 多处写 / x-1 / x!=c / break…）")
    mod, ci = _mods()
    for fn, needle in REJECTS:
        k = ci.cost_of(fn)
        _b, why, hit = _width_bound(mod, fn)
        check(k.is_top(), f"{fn}：κ 是 ⊤（不猜）", k.render())
        check(hit is True, f"{fn}：条件确实是 `x != 0` 形状（拒因有意义）", str(hit))
        check(needle in (why or ""),
              f"{fn}：拒因里写清 `{needle}`", why)

    # `x > 5`（c ≠ 0）**不是**本规则的形状：`hit=False`，落回旧的 `x - c` 上界。
    # （那条也健全，只是更松；本规则 MUST NOT 假装认它。）
    b, why, hit = _width_bound(mod, "r_gt")
    check(hit is False and b is None,
          "r_gt（`x > 5`）：位宽规则不认（hit=False）", f"hit={hit} b={b}")
    k = ci.cost_of("r_gt")
    check(not k.is_top() and "64" not in k.render(),
          "r_gt：κ 来自旧的 `x - c` 上界，不是位宽常数 64", k.render())


# ---------------------------------------------------------------- ⑤-b 诊断
def test_check_diagnostic():
    print("[5-b] `check` 的 E-While-NoBound 诊断里必须带**具体拒因**")
    d = tempfile.mkdtemp(prefix="elnwidth-")
    try:
        p = _os.path.join(d, "m.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 f(i64 x) {\n"
            "    u64 n = 0;\n"
            "    while (x != 0) { x = x >> 1; n += 1; }\n"
            "    return n;\n"
            "}\n")
        r = subprocess.run(
            [_sys.executable, "-B", _os.path.join(SRC, "dsh.py"), "check", p],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=ROOT)
        out = (r.stdout or "") + (r.stderr or "")
        check(r.returncode == 0,
              "check 退出码 0（E-While-NoBound 是 warning）", out[-300:])
        check("E-While-NoBound" in out,
              "有符号的形状仍然报 E-While-NoBound（没被静默放过）", out[-300:])
        check("有符号整数" in out,
              "诊断里带具体拒因（`有符号整数`），不是一句「无法确定」",
              out[-300:])
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- ⑥ 语料靶子
def test_corpus_count_bits():
    print("[6] 语料靶子：`16_bitset.count_bits` 从 ⊤ 变成有限界，并实测")
    cpp = _os.path.join(ROOT, "demo", "corpus", "16_bitset.cpp")
    mod = _parse(io.open(cpp, encoding="utf-8").read(), "16_bitset.eln")
    ci = CostInterp(mod, None)
    k = ci.cost_of("count_bits")
    text = k.render()
    check(not k.is_top(), "count_bits：κ 不是 ⊤", text)
    check("n(b.words)" in text, "count_bits：外层仍按 `n(b.words)` 记", text)
    check(k.variables() == ["b.words"],
          "count_bits：κ 只依赖 `b.words`（内层是常数 64）", str(k.variables()))
    b, why, hit = _width_bound(mod, "count_bits")
    check(hit and b is not None and int(b.value) == 64,
          "count_bits 的内层 while：位宽上界 64", f"bound={b.render() if b else None}")

    # 4 组位图（含全 1 / 交替位）；真实位数 = popcount 之和。
    vectors = ([0], [1], [(1 << 64) - 1],
               [0xAAAAAAAAAAAAAAAA, 0x5555555555555555, (1 << 64) - 1])
    for i, words in enumerate(vectors):
        want = sum(bin(w).count("1") for w in words)
        val, ops, iters = _run(mod, "count_bits",
                               [StructVal("Bits", {"words": list(words)})])
        kv = kappa_at(k, {"b.words": len(words)})
        check(val == want, f"位图{i}（{len(words)} 个字）：返回值 = 真实位数 {want}",
              f"val={val}")
        check(all(bin(w).count("1") <= 64 for w in words)
              and iters <= 65 * len(words),
              f"位图{i}：每条 lane 的轮数 ≤ 64", f"iters={iters}")
        check(ops <= kv, f"位图{i}：ops {ops} ≤ κ {kv:g}",
              f"ops={ops} κ={kv}")


def main() -> int:
    test_three_shapes()
    test_measured()
    test_equivalent_spellings()
    test_type_widths()
    test_rejects()
    test_check_diagnostic()
    test_corpus_count_bits()

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
