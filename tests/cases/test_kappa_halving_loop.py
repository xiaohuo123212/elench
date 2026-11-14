#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_halving_loop.py —— **区间减半循环**的迭代上界（2026-11-13）

## 靶子

```cpp
while (lo < hi) {                        // 或 for (step = 0; step <= x; ++step) {
    mid = lo + (hi - lo) / 2;            //        if (lo >= hi) break;   ← 同一个守卫
    if (…) lo = mid + 1; else hi = mid;  //        mid = lo + (hi - lo) / 2; …
}                                        //    }
```

旧口径只认 `for` 外壳的**线性**上界（`x + 1`）或把 `hi` 记成局部标量（`n(hi)`），
于是 `13_numeric.isqrt` 的 κ 里带着 `x`、`17_sortkey.lower_bound_by_key` 的 κ 里
带着 `hi`——两条 target 都判不了（E23）。真实轮数是 `log2(s₀) + 1`。

## 本规则给的界

`s = hi - lo`，`mid = lo + ⌊s/2⌋`：

| 分支 | 新区间长度 | 与 `s` 的关系（`s ≥ 1`） |
|---|---|---|
| `hi = mid` | `⌊s/2⌋` | `< s` 且 `≤ ⌈s/2⌉` |
| `lo = mid + 1` | `⌈s/2⌉ - 1` | `< s` 且 `≤ ⌈s/2⌉` |

⇒ `s_k ≤ ⌈s₀/2^k⌉`，`s < 1` 时退出 ⇒ **迭代 ≤ ⌈log2(s₀)⌉ + 1**。
`s₀` 能定位成尺寸量（`hi = xs.size()`）⇒ `log2(n(xs)) + 1`；否则取**类型位宽**
（`u32` → 33、`u64` → 65）。

## 五类断言（只比表达式形状不算验证）

1. **判据本体**：五种**接受**的写法各给预期界（`/2` / `>>1`、`<` / `!=` / 镜像
   `>`、`for` 外壳 + `break`、类型位宽）；
2. **解释器实测**：真实轮数 ≤ 推出的界，**四个规模以上**（`n = 8 / 64 / 1024 /
   65536`、`x = 1e3 / 1e6 / 1e12`），且 `ops ≤ κ`（κ 是上界，不是估计值）；
3. **等价性**：同逻辑两种写法 → κ **逐字节相同**、返回值 / ops / 轮数逐项相同
   （`/2` vs `>>1`、`<` vs `!=`、`hi > lo` 镜像）；
4. **§4 的七种拒绝形状各一条**，且**都必须仍给旧的线性界**（不许给新界）：
   `lo = mid`、`hi = mid + 1`、`mid` 两次赋值、`(lo + hi) / 2`、有符号类型、
   复合条件、写在内层循环里；
5. **语料靶子**：`13_numeric.isqrt` / `stddev_milli` / `17_sortkey.lower_bound_by_key`
   / `17_sortkey.main` 的 κ 形状；并做**反向验收**（target 收紧一档必须判债务）。

跑法：

    python -B tests/cases/test_kappa_halving_loop.py
"""

import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
for _p in (SRC, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

import compat                                            # noqa: E402
from parser import parse_source                          # noqa: E402
from cost_interp import CostInterp                       # noqa: E402
from cost_check import evaluate as kappa_at              # noqa: E402
from bound import halving_interval_loop                  # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset  # noqa: E402

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
# 所有函数在**同一份模块**里（`CostInterp` 只建一次）。
# 名字前缀：`a_` = 接受、`r_` = 拒绝（§4）、`w_` = 位宽路径。
SRC_ALL = r"""
// ===== 接受：`while (lo < hi)` + `lo + (hi - lo) / 2` =====
u64 a_slash(const vector<u64>& xs, u64 key) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (lo < hi) {
        u64 mid = lo + (hi - lo) / 2;
        if (xs[mid] < key) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}

// 同逻辑：`>> 1` 代替 `/ 2`（被除数非负，两者等价）。
u64 a_shift(const vector<u64>& xs, u64 key) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (lo < hi) {
        u64 mid = lo + ((hi - lo) >> 1);
        if (xs[mid] < key) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}

// 同逻辑：`lo != hi`（`lo <= hi` 是全程不变式，两种守卫等价）。
u64 a_ne(const vector<u64>& xs, u64 key) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (lo != hi) {
        u64 mid = lo + (hi - lo) / 2;
        if (xs[mid] < key) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}

// 同逻辑：镜像写法 `hi > lo`。
u64 a_gt(const vector<u64>& xs, u64 key) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (hi > lo) {
        u64 mid = lo + (hi - lo) / 2;
        if (xs[mid] < key) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}

// 守卫在体内（`for` 外壳 + `if (lo >= hi) break;`），s₀ = 形参 `x` ⇒ 类型位宽 65。
u64 a_forshell(u64 x) {
    u64 lo = 0;
    u64 hi = x;
    for (u64 step = 0; step <= x; ++step) {
        if (lo >= hi) break;
        u64 mid = lo + (hi - lo) / 2;
        if (mid * mid < x) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}

// 同上，但循环条件换成 `step < x + 1`（同逻辑的另一种兜底写法）。
u64 a_forshell2(u64 x) {
    u64 lo = 0;
    u64 hi = x;
    for (u64 step = 0; step < x + 1; ++step) {
        if (lo >= hi) break;
        u64 mid = lo + ((hi - lo) >> 1);
        if (mid * mid < x) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}

// 位宽路径：`hi = x` 是 u32 形参 ⇒ 上界 = 32 + 1 = 33。
u64 w32(u32 x) {
    u32 lo = 0;
    u32 hi = x;
    while (lo < hi) {
        u32 mid = lo + (hi - lo) / 2;
        if (mid < x) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}

// ===== §4 必须拒绝的七种形状（都必须仍给旧的线性界）=====
// ① `lo = mid`（不是 `mid + 1`）：s = 1 时原地不动 = 死循环。
u64 r1_lo_mid(const vector<u64>& xs, u64 key) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (lo < hi) {
        u64 mid = lo + (hi - lo) / 2;
        if (xs[mid] < key) { lo = mid; } else { hi = mid; }
    }
    return lo;
}

// ② `hi = mid + 1`：同上，可能不缩。
u64 r2_hi_plus(const vector<u64>& xs, u64 key) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (lo < hi) {
        u64 mid = lo + (hi - lo) / 2;
        if (xs[mid] < key) { lo = mid + 1; } else { hi = mid + 1; }
    }
    return lo;
}

// ③ `mid` 被第二次赋值：两次调用的 mid 不同，划分不成立。
u64 r3_mid_twice(const vector<u64>& xs, u64 key) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (lo < hi) {
        u64 mid = lo + (hi - lo) / 2;
        mid = mid + 0;
        if (xs[mid] < key) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}

// ④ `(lo + hi) / 2`：u64 下 `lo + hi` 会溢出。
u64 r4_sum_half(const vector<u64>& xs, u64 key) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (lo < hi) {
        u64 mid = (lo + hi) / 2;
        if (xs[mid] < key) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}

// ⑤ 有符号：负数下 `s = hi - lo` 的语义不同。
i64 r5_signed(i64 x) {
    i64 lo = 0;
    i64 hi = x;
    while (lo < hi) {
        i64 mid = lo + (hi - lo) / 2;
        if (mid < x) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}

// ⑥ 复合条件：不在本规则里。
u64 r6_compound(const vector<u64>& xs, u64 key, u64 k) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (lo < hi && k > 0) {
        u64 mid = lo + (hi - lo) / 2;
        if (xs[mid] < key) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}

// ⑦ 写在内层循环里：每轮可能执行多次，划分不成立。
u64 r7_inner_loop(const vector<u64>& xs, u64 key, u64 r) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (lo < hi) {
        u64 mid = lo + (hi - lo) / 2;
        for (u64 j = 0; j < r; ++j) { lo = mid + 1; }
        if (xs[mid] < key) { hi = mid; }
    }
    return lo;
}

// ⑧ 对称守卫（`!=`）但 `lo` 初值不是 0：守卫本身证明不了 `lo <= hi`，
//    初值 `lo > hi` 时 `hi - lo` 会回绕 ⇒ 折半论证不成立。
u64 r8_ne_nonzero(const vector<u64>& xs, u64 key) {
    u64 lo = 5;
    u64 hi = xs.size();
    while (lo != hi) {
        u64 mid = lo + (hi - lo) / 2;
        if (xs[mid] < key) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}

// ⑨ 两处写不在同一个 if 的 then/else 里：可能同一轮都执行（`hi < lo`）或都不执行。
u64 r9_both_direct(const vector<u64>& xs, u64 key) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (lo < hi) {
        u64 mid = lo + (hi - lo) / 2;
        lo = mid + 1;
        hi = mid;
    }
    return lo;
}

// ⑩ 体内有 `continue`：可以跳过两处写 ⇒ 区间不缩 ⇒ 可能死循环。
u64 r10_continue(const vector<u64>& xs, u64 key, u64 c) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (lo < hi) {
        u64 mid = lo + (hi - lo) / 2;
        if (c > 0) { continue; }
        if (xs[mid] < key) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}
"""

CANON = "log2(n(xs)) + 1"
#: 同一个界在 `Bound` 域里的写法（`size` 节点渲染成 `xs.length()`；进 κ 之后
#: 才归一成 `n(xs)`）。
CANON_BOUND = "log2(xs.length()) + 1"

#: 接受形状：函数名 → (预期界（Bound 渲染）, 预期界（数值函数，`n = n(xs)`）)
ACCEPT = (
    ("a_slash", CANON_BOUND, lambda n: math.log2(n) + 1),
    ("a_shift", CANON_BOUND, lambda n: math.log2(n) + 1),
    ("a_ne", CANON_BOUND, lambda n: math.log2(n) + 1),
    ("a_gt", CANON_BOUND, lambda n: math.log2(n) + 1),
)

#: 键路径（`hi = x`）：上界是类型位宽 + 1。
WIDTH_PATH = (("a_forshell", 65), ("a_forshell2", 65), ("w32", 33))

#: §4 的七种拒绝形状（外加三条同族的加固判据）：函数名 → (拒因关键词, 旧界是否有限)。
#: 全部要求"仍然给出旧的线性界"（`!=` 家族例外：它本来就没有兜底界 ⇒ ⊤，见
#: `width_bounded_loop` 的 `ne_family`；那是**改前**的口径，与本次无关）。
REJECTS = (
    ("r1_lo_mid", "mid + 1", True),
    ("r2_hi_plus", "mid", True),
    ("r3_mid_twice", "恰好一次", True),
    ("r4_sum_half", "溢出", True),
    ("r5_signed", "有符号", True),
    ("r6_compound", None, True),      # 复合条件：本规则**完全不介入**（hit=False）
    ("r7_inner_loop", "内层循环", True),
    ("r8_ne_nonzero", "对称守卫", False),   # `!=` 家族被拒 ⇒ 旧口径就是 ⊤
    ("r9_both_direct", "同一个 `if`", True),
    ("r10_continue", "continue", True),
)


def _parse(src, name):
    prev = compat.enabled()
    compat.set_enabled(True)
    try:
        return parse_source(src, name).module
    finally:
        compat.set_enabled(prev)


def _mods():
    mod = _parse(SRC_ALL, "halving_loop.eln")
    return mod, CostInterp(mod, None)


def _fn_node(mod, name):
    return next((c for c in mod.children
                 if getattr(c, "kind", "") == "fn" and getattr(c, "name", "") == name),
                None)


def _whiles_of(fn_node):
    out = []
    stack = [fn_node]
    while stack:
        n = stack.pop(0)
        if getattr(n, "kind", "") == "while":
            out.append(n)
        stack.extend(getattr(n, "children", None) or [])
    return out


def _halving(mod, fn_name):
    """`(界, 原因, 是否命中)`——扫完函数里所有 while，返回命中的那个。"""
    fn = _fn_node(mod, fn_name)
    for wh in _whiles_of(fn):
        b, why, hit = halving_interval_loop(wh, fn)
        if hit:
            return b, why, hit
    return None, "", False


def _run(mod, fn_name, args):
    """跑一次 → `(返回值, ops, 循环轮数)`。"""
    ops_enable(True)
    try:
        ops_reset()
        it = Interp(mod, None)
        val = it.call(fn_name, args)
        return val, ops_of(fn_name), it.iters
    finally:
        ops_enable(False)


def _python_lower_bound(xs, key):
    """`a_*` 的**参考语义**：第一个 `xs[i] >= key` 的下标。"""
    for i, v in enumerate(xs):
        if v >= key:
            return i
    return len(xs)


def _python_lower_bound_trace(xs, key):
    """参考实现：与 `a_*` **同一条路径**，返回 `(真实轮数, 返回值)`。

    轮数不是"大约 log2"——逐轮算，拿它跟 κ 比才有意义。
    """
    lo, hi, iters = 0, len(xs), 0
    while lo < hi:
        iters += 1
        mid = lo + (hi - lo) // 2
        if xs[mid] < key:
            lo = mid + 1
        else:
            hi = mid
    return iters, lo


# ---------------------------------------------------------------- ① 判据本体
def test_accepted():
    print("[1] 五种**接受**的写法各给预期界（/2、>>1、!=、镜像 >、for 外壳 + break）")
    mod, ci = _mods()
    for fn, want, _f in ACCEPT:
        b, why, hit = _halving(mod, fn)
        k = ci.cost_of(fn)
        check(hit and b is not None and b.render() == want,
              f"{fn}：判据给 `{want}`（进 κ 后归一成 `{CANON}`）",
              f"bound={b.render() if b else None}")
        check("区间减半" in (why or ""), f"{fn}：原因里点明「区间减半」", why)
        check(CANON in k.render(), f"{fn}：κ 里出现 `{CANON}`", k.render())
        check(k.variables() == ["xs"],
              f"{fn}：κ 只依赖 `xs`（`hi` 这个局部标量不见了）", str(k.variables()))
    for fn, width in WIDTH_PATH:
        b, why, hit = _halving(mod, fn)
        k = ci.cost_of(fn)
        check(hit and b is not None and getattr(b, "kind", "") == "const"
              and int(b.value) == width,
              f"{fn}：`hi = <形参>` 取**类型位宽** {width}",
              f"bound={b.render() if b else None}")
        check(k.variables() == [],
              f"{fn}：κ 里没有尺寸量（位宽上界与数据无关）", str(k.variables()))


# ---------------------------------------------------------------- ② 实测
def test_measured_lower_bound():
    print("[2a] 实测 lower_bound 形状：n = 8 / 64 / 1024 / 65536，真实轮数 ≤ 界")
    mod, ci = _mods()
    k = ci.cost_of("a_slash")
    for n in (8, 64, 1024, 65536):
        xs = [i * 3 for i in range(n)]
        key = n + 1                       # 落在表外 ⇒ 走满整条路径
        val, ops, iters = _run(mod, "a_slash", [xs, key])
        bound = math.log2(n) + 1
        kv = kappa_at(k, {"xs": n})
        want_iters, want_val = _python_lower_bound_trace(xs, key)
        check(iters <= bound,
              f"n={n}：真实轮数 {iters} ≤ log2({n})+1 = {bound:.3f}", f"iters={iters}")
        check(iters <= math.ceil(math.log2(n)) + 1,
              f"n={n}：真实轮数 {iters} ≤ ⌈log2 {n}⌉+1 = {math.ceil(math.log2(n)) + 1}",
              f"iters={iters}")
        check(iters == want_iters,
              f"n={n}：轮数与参考实现逐轮一致（{want_iters}）", f"iters={iters}")
        check(val == want_val == _python_lower_bound(xs, key),
              f"n={n}：返回值 = 参考下界 {want_val}", f"val={val}")
        check(ops <= kv, f"n={n}：ops {ops} ≤ κ {kv:g}", f"ops={ops} κ={kv}")


def test_measured_for_shell():
    print("[2b] 实测 isqrt 形状（for 外壳 + break）：x = 1e3 / 1e6 / 1e12")
    mod, ci = _mods()
    k = ci.cost_of("a_forshell")
    kv = kappa_at(k, {})
    for x in (1000, 1000000, 1000000000000):
        val, ops, iters = _run(mod, "a_forshell", [x])
        # 参考语义：最小的 `v` 使 `v*v >= x`（= ⌈√x⌉），不是 `math.isqrt` 的 ⌊√x⌋。
        want = math.isqrt(x)
        if want * want < x:
            want += 1
        check(iters <= 65, f"x={x}：真实轮数 {iters} ≤ 65（u64 位宽 + 1）", f"iters={iters}")
        check(val == want, f"x={x}：返回值 = ⌈√x⌉ = {want}", f"val={val}")
        check(ops <= kv, f"x={x}：ops {ops} ≤ κ {kv:g}", f"ops={ops} κ={kv}")
    print(f"       κ(a_forshell) = {k.render()}")


# ---------------------------------------------------------------- ③ 等价性
def test_equivalence():
    print("[3] 同逻辑两种写法：镜像 / `!=` 的 κ 逐字节相同，返回值与轮数逐项相同")
    mod, ci = _mods()
    pairs = (("a_slash", "a_ne"),         # `lo < hi` vs `lo != hi`
             ("a_slash", "a_gt"))         # `lo < hi` vs `hi > lo`
    cases = ([list(range(0, 24, 3)), 9],
             [[5], 5],
             [[7, 7, 7, 7], 7],
             [[], 0])
    for a, b in pairs:
        ka, kb = ci.cost_of(a).render(), ci.cost_of(b).render()
        check(ka == kb, f"{a} 与 {b} 的 κ 逐字节相同", f"{ka} vs {kb}")
        same, detail = True, ""
        for xs, key in cases:
            ra = _run(mod, a, [list(xs), key])
            rb = _run(mod, b, [list(xs), key])
            if ra != rb:
                same = False
                detail = f"n={len(xs)} key={key}: {ra} vs {rb}"
                break
        check(same, f"{a} 与 {b}：返回值 / ops / 轮数逐项相同", detail)

    # `/ 2` 与 `>> 1` **是同一逻辑**，但操作数不同（除法 vs 移位）：κ 的常数系数
    # 差 1、ops 也差 —— 这是**诚实的 ops 计数**，不是建模偏差。所以这一对只钉
    # 「界相同 + 返回值相同 + **轮数**相同」，不钉 ops。
    ka, kb = ci.cost_of("a_slash").render(), ci.cost_of("a_shift").render()
    check("(log2(n(xs)) + 1)" in ka and "(log2(n(xs)) + 1)" in kb,
          "a_slash / a_shift：`/2` 与 `>>1` 给同一个**界** `log2(n(xs)) + 1`",
          f"{ka} vs {kb}")
    b1, _w, _h = _halving(mod, "a_slash")
    b2, _w, _h = _halving(mod, "a_shift")
    check(b1 is not None and b2 is not None and b1.render() == b2.render(),
          "a_slash / a_shift：判据给出的界**逐字节相同**",
          f"{b1.render() if b1 else None} vs {b2.render() if b2 else None}")
    same, detail = True, ""
    for xs, key in cases:
        va, _oa, ia = _run(mod, "a_slash", [list(xs), key])
        vb, _ob, ib = _run(mod, "a_shift", [list(xs), key])
        if (va, ia) != (vb, ib):
            same = False
            detail = f"n={len(xs)} key={key}: {(va, ia)} vs {(vb, ib)}"
            break
    check(same, "a_slash / a_shift：返回值与**轮数**逐项相同（成本差的是 ops 不是轮数）",
          detail)

    # for 外壳的两种兜底写法（`step <= x` / `step < x + 1`）界相同（都是 65）。
    b1, _w1, h1 = _halving(mod, "a_forshell")
    b2, _w2, h2 = _halving(mod, "a_forshell2")
    check(h1 and h2 and b1.render() == b2.render() == "65",
          "a_forshell / a_forshell2：两种兜底写法给同一个上界 65",
          f"{b1.render() if b1 else None} vs {b2.render() if b2 else None}")
    same, detail = True, ""
    for x in (0, 1, 1000):
        v1, _o1, i1 = _run(mod, "a_forshell", [x])
        v2, _o2, i2 = _run(mod, "a_forshell2", [x])
        if (v1, i1) != (v2, i2):
            same = False
            detail = f"x={x}: {(v1, i1)} vs {(v2, i2)}"
            break
    check(same, "x=0/1/1000：a_forshell / a_forshell2 的返回值与轮数相同", detail)


# ---------------------------------------------------------------- ④ 七种拒绝
def test_rejects():
    print("[4] §4 的七种拒绝形状：不给新界，且**仍给旧的线性界**")
    mod, ci = _mods()
    for fn, needle, finite in REJECTS:
        b, why, hit = _halving(mod, fn)
        k = ci.cost_of(fn)
        check(b is None, f"{fn}：判据**不给**新界", f"bound={b.render() if b else None}")
        if needle is None:
            check(hit is False,
                  f"{fn}：复合条件根本不在本规则里（hit=False，原样交给旧规则）",
                  str(hit))
        else:
            check(hit is True, f"{fn}：形状属本族，拒因有意义（hit=True）", str(hit))
            check(needle in (why or ""), f"{fn}：拒因里写清 `{needle}`", why)
        # **旧界**：拒绝 ≠ 新界；也不会因为"拒绝"就变成 ⊤（`!=` 家族例外）。
        check(k.is_top() != finite,
              f"{fn}：旧口径不变（{'有限界' if finite else '`!=` 家族本来就是 ⊤'}）",
              k.render())
        check("log2" not in k.render(),
              f"{fn}：κ 里没有 `log2(...)`（没给新界）", k.render())
    # `r6_compound` 的旧界是 `min(n(hi), k - 0)` 那一类；其余是 `n(hi)`。
    for fn in ("r1_lo_mid", "r2_hi_plus", "r4_sum_half", "r7_inner_loop",
               "r5_signed", "r9_both_direct", "r10_continue"):
        k = ci.cost_of(fn)
        check("n(hi)" in k.render() or "hi" in k.render(),
              f"{fn}：κ 仍是旧的 `n(hi)` 形状", k.render())


# ---------------------------------------------------------------- ⑤ 语料靶子
def _corpus(base):
    cpp = os.path.join(ROOT, "demo", "corpus", base + ".cpp")
    tu = os.path.join(ROOT, "demo", "corpus", base + ".tu")
    if not (os.path.isfile(cpp) and os.path.isfile(tu)):
        return None, None
    return (_parse(io.open(cpp, encoding="utf-8").read(), base + ".cpp"),
            _parse(io.open(tu, encoding="utf-8").read(), base + ".tu"))


def test_corpus():
    print("[5] 语料四个靶子的 κ 形状（`x` / `acc` / `hi` 都不许再出现）")
    mod, tu = _corpus("13_numeric")
    ci = CostInterp(mod, tu)
    isq = ci.cost_of("isqrt")
    check(isq.variables() == [], "isqrt：κ 里没有 `x`（取类型位宽 ⇒ 常数）", isq.render())
    check("2186" in isq.render(), "isqrt：κ = 2186（= 4+2+65×33+33+2）", isq.render())
    sd = ci.cost_of("stddev_milli")
    check(set(sd.variables()) == {"xs"},
          "stddev_milli：κ 的尺寸量恰好 {xs}（`acc` 消失）", str(sd.variables()))
    check("log2" not in sd.render(),
          "stddev_milli：内联进来的是常数，不需要 `log2`", sd.render())
    m = ci.cost_of("main")
    check(m.variables() == [], "13_numeric.main：κ 是常数", m.render())

    mod, tu = _corpus("17_sortkey")
    ci = CostInterp(mod, tu)
    lb = ci.cost_of("lower_bound_by_key")
    check(lb.render() == "7 + (log2(n(xs)) + 1)×25 + 25 + 2",
          "lower_bound_by_key：κ = 7 + (log2(n(xs)) + 1)×25 + 25 + 2", lb.render())
    check(lb.variables() == ["xs"], "lower_bound_by_key：κ 只依赖 `xs`（没有 `hi`）",
          str(lb.variables()))
    m = ci.cost_of("main")
    check("log2(7)" in m.render(),
          "17_sortkey.main：`n(xs)` 折成字面量 7 ⇒ `log2(7)`（没有 `hi`）", m.render())

    # 同族的另一个实例**不许**被动到：`09_search_window.lower_bound_index` 的旧界
    # 已经是尺寸量界（`n(xs)`），`min` 证不出更小 ⇒ 保持旧界（绝不比改前松）。
    mod, tu = _corpus("09_search_window")
    ci = CostInterp(mod, tu)
    k = ci.cost_of("lower_bound_index")
    check("log2" not in k.render(),
          "09_search_window.lower_bound_index：旧界是尺寸量界 ⇒ 保持旧界（不动）",
          k.render())


# ---------------------------------------------------------------- ⑥ 反向验收
_PROBE_ELN = """\
u64 lb(const vector<u64>& xs, u64 key) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (lo < hi) {
        u64 mid = lo + (hi - lo) / 2;
        if (xs.at(mid) < key) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}
"""

_PROBE_TU = """\
spec lb(const vector<u64>& xs, u64 key) -> u64 {
    effects {};
    target { time: { max: %s }, allocation: { max: 0 }, };
}
"""


def _verdict_of(target: str):
    """写一份临时模块，跑 `dsh cost --json`，返回 `(判决, κ 文本)`。"""
    d = tempfile.mkdtemp(prefix="elnhalving-")
    try:
        p_eln = os.path.join(d, "probe.eln")
        p_tu = os.path.join(d, "probe.tu")
        io.open(p_eln, "w", encoding="utf-8", newline="\n").write(_PROBE_ELN)
        io.open(p_tu, "w", encoding="utf-8", newline="\n").write(_PROBE_TU % target)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        r = subprocess.run(
            [sys.executable, "-B", os.path.join(SRC, "dsh.py"), "cost",
             "--json", p_tu, p_eln],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env)
        try:
            rec = json.loads(r.stdout)["functions"]["lb"]
        except Exception:                                  # noqa: BLE001
            return "PARSE-FAIL:" + (r.stdout or r.stderr or "")[-400:], ""
        return rec.get("verdict"), rec.get("cost_expression") or ""
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_reverse():
    print("[6] 反向验收：target 写 κ 的阶 ⇒ 达成；收紧一档 ⇒ 判决变差")
    v_ok, cost = _verdict_of("O(xs.size())")
    check(v_ok == "ok", "target O(xs.size())：判达成", v_ok)
    check("log2(n(xs))" in cost, "κ 用的是 `log2(n(xs)) + 1`", cost)
    v_bad, _c = _verdict_of("O(1)")
    check(v_bad in ("debt", "unknown"),
          "target 收紧到 O(1)：不再判达成（给出债务 / 无法判定）", v_bad)


# ---------------------------------------------------------------- ⑦ check 一致
_CHECK_PROBE = """\
u64 ok_lb(const vector<u64>& xs, u64 key) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (lo < hi) {
        u64 mid = lo + (hi - lo) / 2;
        if (xs.at(mid) < key) { lo = mid + 1; } else { hi = mid; }
    }
    return lo;
}
u64 bad_lb(const vector<u64>& xs, u64 key) {
    u64 lo = 0;
    u64 hi = xs.size();
    while (lo < hi) {
        u64 mid = lo + (hi - lo) / 2;
        if (xs.at(mid) < key) { lo = mid; } else { hi = mid; }
    }
    return lo;
}
"""


def test_check_consistency():
    print("[7] `check` 与 `cost` MUST NOT 给出相反结论（同一个判据）")
    d = tempfile.mkdtemp(prefix="elnhalvingchk-")
    try:
        p = os.path.join(d, "m.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(_CHECK_PROBE)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        r = subprocess.run(
            [sys.executable, "-B", os.path.join(SRC, "dsh.py"), "check", p],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env)
        out = (r.stdout or "") + (r.stderr or "")
        check(r.returncode == 0, "check 退出码 0（E-While-NoBound 是 warning）",
              out[-300:])
        # 只有**被拒**的那一个 warn：接受形状已经能给出有限界，不该再报"推不出"。
        n_warn = out.count("E-While-NoBound")
        check(n_warn == 1,
              "只有被拒形状报 E-While-NoBound（接受形状 0 条，与 `cost` 一致）",
              f"count={n_warn} / {out[-400:]}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    test_accepted()
    test_measured_lower_bound()
    test_measured_for_shell()
    test_equivalence()
    test_rejects()
    test_corpus()
    test_reverse()
    test_check_consistency()

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
