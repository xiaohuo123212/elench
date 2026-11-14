#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_merge_log.py —— `merge_sort` 的 κ 是 **O(n log n)**（按层求和）

## 这份测试钉什么（2026-11-12 夜）

区间对半分的递归是

    T(s) = 2·T(s/2) + 单层(s)                （s = hi - lo）

⇒ `T(s) = Σ_{k<K} [2^k·A + B·s] = A·(2^K − 1) + B·s·K = Θ(s log s)`。

修前记的是「**激活数** × 单层」：激活数 `≈ 2s` 是对的，但单层用的是
**入口区间**的成本 `A + B·s`，每一层都乘同一个 `s` ⇒ `2s × (A + B·s) = O(s²)`。

**这不是"松一点"，是产品级缺陷**：作者写 `target O(n log n)`（完全正确）
会被判「阶不够」= **假债务**，比 ⊤ 更坏（⊤ 至少说"判不了"）。
同一个 `merge_sort` 写 `O(n)` 又必须判**债务**——所以本文件两头都钉。

## 六类断言（只比表达式形状不算验证）

1. **形状**：κ 里有 `log2(...)` 与 `(hi - lo)` 的**乘积**；且**不含** `mid`
   这类被调方形参名（那是"单层用绝对下标"的痕迹，也正是 O(n²) 的根因）。
2. **增长**：`κ(2n)/κ(n) < 2.6`（`n log n` ≈ 2.2，二次 ≈ 4.0）——
   逐规模求值，不看符号形状。
3. **等价性**：同一逻辑的三种写法 κ **逐字节相同**、解释器 ops 相同。
4. **上界**：解释器**真跑** `n = 4/8/16/32/64/128`，断 `ops ≤ κ`（不是估计值）。
   靶子是**真语料** `demo/corpus/01_sorting.cpp` 的 `merge_sort`，不是玩具函数。
5. **判定可推翻**：同一个函数的 `.tu` 写 `O(n log n)` → **达成**；
   写 `O(n)` → **债务（阶不够）**。两条都要有，只验其中一条等于没验。
6. **不越界**：`place` 那种 n 叉递归**不被**这条覆盖（仍 ⊤）——按层求和的
   前提是 `T(s) = 2·T(s/2) + f(s)`，n 叉是 `Σ n^k`（指数）。

跑法：

    cd tests/cases; $env:PYTHONIOENCODING="utf-8"; python test_kappa_merge_log.py
"""

import io as _io
import os as _os
import subprocess as _subprocess
import sys as _sys
import tempfile as _tempfile

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


def load(eln: str, tu: str = "", compat_mode: bool = False):
    """`(实现, 契约)` 两棵 AST。`compat_mode` 只对 C++ 语料开（与 `--compat` 同一开关）。"""
    old = compat.enabled()
    if compat_mode:
        compat.set_enabled(True)
    try:
        ch = parse_source(eln, "merge_log.eln").module
        decl = parse_source(tu, "merge_log.tu").module if tu else None
    finally:
        compat.set_enabled(old)
    return ch, decl


def ci_of(eln: str, tu: str = "", compat_mode: bool = False) -> CostInterp:
    ch, decl = load(eln, tu, compat_mode)
    return CostInterp(ch, decl)


def run_call(fn: str, args, eln: str, tu: str = "", compat_mode: bool = False):
    """`(返回值, ops, 每个函数的实际调用次数)`。"""
    ch, decl = load(eln, tu, compat_mode)
    it = Interp(ch, decl)
    ops_enable(True)
    try:
        ops_reset()
        v = it.call(fn, args)
        return v, ops_of(), dict(it.calls)
    finally:
        ops_enable(False)


def xs_desc(n: int):
    """`n` 个降序元素：归并排序在它上面**每次都真的两路都走**。"""
    return [i for i in range(n)][::-1]


# ---------------------------------------------------------------- 语料

CORPUS_CPP = _os.path.join(ROOT, "demo", "corpus", "01_sorting.cpp")


def corpus_eln() -> str:
    """真语料 + 一个入口包装（`sort_all(xs)` 就是 `merge_sort(xs, 0, xs.size())`）。"""
    with _io.open(CORPUS_CPP, encoding="utf-8") as fh:
        text = fh.read()
    return text + """
void sort_all(vector<u64>& xs) {
    merge_sort(xs, 0, xs.size());
}
"""


def corpus_tu(all_target: str, ms_target: str) -> str:
    """语料契约的**临时**版本（不改 `demo/**`）：只换两处 target。

    `ms_target` 用 `(hi - lo)` 表达——`merge_sort` 的尺寸量是**区间长度**，
    `requires lo <= hi;` 是让差值成立的那一条（写 target 时必须有）。
    `all_target` 用 `xs.size()`，所以那里可以字面写 `O(n log n)`。
    """
    return """
spec insertion_sort(vector<u64>& xs) -> () {
    effects {};
    target { time: { max: O(xs.size() * xs.size()) }, allocation: { max: 0 }, };
}
spec merge_halves(vector<u64>& xs, u64 lo, u64 mid, u64 hi) -> () {
    effects { alloc };
    target { time: { max: O(max(mid, hi)) }, allocation: { max: O(max(mid, hi)) }, };
}
spec sort_all(vector<u64>& xs) -> () {
    effects {};
    target { time: { max: %(ALL)s }, allocation: { max: 0 }, };
}
spec merge_sort(vector<u64>& xs, u64 lo, u64 hi) -> () {
    effects { alloc };
    recursion depth <= 32;
    requires lo <= hi;
    target { time: { max: %(MS)s }, allocation: { max: 0 }, };
}
""" % {"ALL": all_target, "MS": ms_target}


LOG_TARGET = "O((hi - lo) * log(hi - lo))"
LIN_TARGET = "O(hi - lo)"

#: 最小复现：**只有** `merge_sort` 的形状（区间的两半 + 一个线性的 merge）。
TINY_ELN = """
u64 merge_range(vector<u64>& xs, u64 lo, u64 mid, u64 hi) {
    u64 i = lo;
    u64 s = 0;
    while (i < mid) { s = s + xs[i]; i = i + 1; }
    while (i < hi) { s = s + xs[i]; i = i + 1; }
    return s;
}

void ms(vector<u64>& xs, u64 lo, u64 hi) {
    if (hi - lo < 2) { return; }
    u64 mid = lo + (hi - lo) / 2;
    ms(xs, lo, mid);
    ms(xs, mid, hi);
    merge_range(xs, lo, mid, hi);
}
"""


def tiny_tu(extra: str = "") -> str:
    return """
spec ms(vector<u64>& xs, u64 lo, u64 hi) -> () {
    effects {};
    recursion depth <= 32;
    requires lo <= hi;
    %s
}
spec merge_range(vector<u64>& xs, u64 lo, u64 mid, u64 hi) -> u64 {
    effects {};
    requires lo <= mid;
    requires mid <= hi;
}
""" % extra


# ---------------------------------------------------------------- [1] 形状

def _squash(text: str) -> str:
    return "".join(text.split())


def _is_level_sum(render: str) -> bool:
    """`×(log2(`：log 因子是**乘在区间长度上**的（不是加在别处的噪声）。"""
    return ")×(log2(" in _squash(render)


def test_shape():
    print("[1] κ 形状：按层求和 ⇒ `B×s×(log2(s)+1)`，单层不再用绝对下标")
    k = ci_of(TINY_ELN, tiny_tu()).cost_of("ms")
    r = k.render()
    print("       κ(ms) =", r)
    check(not k.is_top(), "ms：κ 不是 ⊤", r)
    check("log2(" in r, "κ 里有 `log2(...)`（层数是 log）", r)
    check(_is_level_sum(r), "`log2` 因子**乘在区间长度上**（`… ×s× (log2(…)+1)`）", r)
    check("hi - lo" in r, "区间长度用 `hi - lo` 表达（不是 `hi` / `mid` 这类绝对量）", r)
    # **绝对下标的痕迹**：修前单层成本是 `max(mid, hi)`（`while (i < mid && j < hi)`
    # 的轮数被记成绝对下标），乘上激活数就是 O(n²)。
    check("mid" not in r,
          "κ 里**没有** `mid`（被调方形参名 / 绝对下标都不许留下）", r)
    print()

    print("      —— 真语料 `demo/corpus/01_sorting.cpp`（同一个形状）")
    ci = ci_of(corpus_eln(), corpus_tu("O(xs.size() * log(xs.size()))", LOG_TARGET),
               compat_mode=True)
    k2 = ci.cost_of("merge_sort")
    r2 = k2.render()
    print("       κ(merge_sort) =", r2[:200], "…" if len(r2) > 200 else "")
    check(not k2.is_top(), "语料 merge_sort：κ 不是 ⊤")
    check("log2(" in r2 and _is_level_sum(r2),
          "语料 merge_sort：同样是 `s × log2(s)` 的乘积", r2[:300])
    check("mid" not in r2, "语料 merge_sort：κ 里没有 `mid`", r2[:300])
    print()


# ---------------------------------------------------------------- [2] 增长

def test_growth():
    print("[2] 增长：κ(2n)/κ(n) < 2.6（n log n ≈ 2.2；二次 ≈ 4.0）")
    ci = ci_of(corpus_eln(), corpus_tu("O(xs.size() * log(xs.size()))", LOG_TARGET),
               compat_mode=True)
    k = ci.cost_of("merge_sort")
    print("         n→2n         κ(2n)    κ(2n)/κ(n)   二次会是")
    prev = None
    for n in (16, 32, 64, 128, 256, 512):
        kv = kappa_at(k, {"xs": n, "lo": 0, "hi": n})
        if prev is not None:
            ratio = kv / prev[1]
            print(f"      {prev[0]:4d}→{n:<4d} {kv:12.0f}  {ratio:9.3f}    4.000")
            check(ratio < 2.6,
                  f"n={prev[0]}→{n}：κ 的增长 {ratio:.3f} < 2.6（对数级，不是二次）",
                  f"{prev[1]:g} → {kv:g}")
        prev = (n, kv)
    print()


# ---------------------------------------------------------------- [3] 等价写法

_MERGE_TAIL = """
u64 merge_range(vector<u64>& xs, u64 lo, u64 mid, u64 hi) {
    u64 i = lo;
    u64 s = 0;
    while (i < mid) { s = s + xs[i]; i = i + 1; }
    while (i < hi) { s = s + xs[i]; i = i + 1; }
    return s;
}
"""


def _variant(name: str, guard: str, mid: str, order: str) -> str:
    """同一逻辑的四种拼写（只换写法，不换语义）。"""
    if order == "fwd":
        calls = f"    {name}(xs, lo, mid);\n    {name}(xs, mid, hi);"
    else:
        calls = f"    {name}(xs, mid, hi);\n    {name}(xs, lo, mid);"
    return (f"void {name}(vector<u64>& xs, u64 lo, u64 hi) {{\n"
            f"    if (hi - lo {guard}) {{ return; }}\n"
            f"    u64 mid = {mid};\n"
            f"{calls}\n"
            f"    merge_range(xs, lo, mid, hi);\n"
            f"}}\n") + _MERGE_TAIL


def _variant_tu(name: str) -> str:
    return f"""
spec {name}(vector<u64>& xs, u64 lo, u64 hi) -> () {{
    effects {{}};
    recursion depth <= 32;
    requires lo <= hi;
}}
spec merge_range(vector<u64>& xs, u64 lo, u64 mid, u64 hi) -> u64 {{
    effects {{}};
    requires lo <= mid;
    requires mid <= hi;
}}
"""


def test_equivalence():
    print("[3] 等价性：同逻辑的四种写法 κ **逐字节相同**、ops 相同")
    variants = (
        ("va", "< 2", "lo + (hi - lo) / 2", "fwd"),
        ("vb", "<= 1", "lo + (hi - lo) / 2", "fwd"),      # 早退换写法
        ("vc", "< 2", "lo + (hi - lo) / 2", "rev"),       # 两次调用换顺序
        ("vd", "< 2", "(lo + hi) / 2", "fwd"),            # 中点换写法
    )
    renders, opss = {}, {}
    for name, guard, mid, order in variants:
        eln = _variant(name, guard, mid, order)
        k = ci_of(eln, _variant_tu(name)).cost_of(name)
        check(not k.is_top(), f"{name}（{guard} / {mid} / {order}）：κ 有限",
              k.render()[:200])
        renders[name] = k.render()
        _v, o, _c = run_call(name, [xs_desc(16), 0, 16], eln, _variant_tu(name))
        opss[name] = o
    check(renders["va"] == renders["vb"] == renders["vc"],
          "早退 `<2` / `<=1`、两次调用换序：κ **逐字节相同**",
          f"{renders['va'][:120]} vs {renders['vb'][:120]}")
    check(renders["va"] != renders["vd"],
          "中点 `lo+(hi-lo)/2` 与 `(lo+hi)/2` 的常数项**不同**（该不同：多一个 `-`）")
    check(renders["va"].count("log2(") == renders["vd"].count("log2("),
          "但两种中点的 `log2` 项个数相同（各 2 个自调用点）")
    check(opss["va"] == opss["vb"] == opss["vc"], "解释器 ops **相同**", str(opss))
    print()


# ---------------------------------------------------------------- [4] ops ≤ κ

def test_ops_le_kappa():
    print("[4] 实测 ops ≤ κ（真语料，逐规模）")
    eln = corpus_eln()
    tu = corpus_tu("O(xs.size() * log(xs.size()))", LOG_TARGET)
    ci = ci_of(eln, tu, compat_mode=True)
    k_ms = ci.cost_of("merge_sort")
    k_all = ci.cost_of("sort_all")
    print("         n  激活数       ops      κ(merge_sort)      κ(sort_all)  ops≤κ")
    for n in (4, 8, 16, 32, 64, 128):
        _v, o, calls = run_call("merge_sort", [xs_desc(n), 0, n], eln, tu,
                                compat_mode=True)
        act = calls.get("merge_sort", 0)
        kv = kappa_at(k_ms, {"xs": n, "lo": 0, "hi": n})
        ka = kappa_at(k_all, {"xs": n})
        print(f"      {n:4d} {act:7d} {o:9d} {kv:16.0f} {ka:16.0f}  "
              f"{str(o <= kv and o <= ka):5s}")
        check(o <= kv, f"n={n}：ops {o} ≤ κ(merge_sort) {kv:.0f}", f"{o} > {kv}")
        check(o <= ka, f"n={n}：ops {o} ≤ κ(sort_all) {ka:.0f}", f"{o} > {ka}")
        # 激活数**恰好** 2n-1（叶子 n + 内部 n-1）：层数结构没被改动
        check(act == 2 * n - 1, f"n={n}：激活数 {act} = 2n-1", str(act))
    print()


# ---------------------------------------------------------------- [5] 判定

def _run_cost(tu_text: str) -> str:
    """真 cli：`dsh cost <临时 .tu> <临时 .eln> --compat`，返回合并输出。"""
    tmp = _tempfile.mkdtemp(prefix="merge_log_")
    try:
        eln_p = _os.path.join(tmp, "01_sorting.eln")
        tu_p = _os.path.join(tmp, "01_sorting.tu")
        for path, text in ((eln_p, corpus_eln()), (tu_p, tu_text)):
            with _io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        env = dict(_os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = SRC
        p = _subprocess.run(
            [_sys.executable, "-B", _os.path.join(ROOT, "src", "dsh.py"),
             "cost", tu_p, eln_p, "--compat"],
            cwd=tmp, capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env)
        return (p.stdout or "") + (p.stderr or "")
    finally:
        for nm in _os.listdir(tmp):
            try:
                _os.remove(_os.path.join(tmp, nm))
            except OSError:
                pass
        try:
            _os.rmdir(tmp)
        except OSError:
            pass


def _verdict_line(out: str, fn: str) -> str:
    """那个函数的**判定行**：`✓ fn`（达成）或 `▲ fn`（债务）。"""
    for ln in out.splitlines():
        s = ln.strip()
        if (s.startswith("✓ ") or s.startswith("▲ ")) and fn in s.split():
            return s
    for ln in out.splitlines():
        if fn in ln and ("✓" in ln or "▲" in ln):
            return ln.strip()
    return ""


def test_fake_debt_gone():
    print("[5] 假债务消失：`O(n log n)` 判**达成**，同一函数 `O(n)` 判**债务**")
    out_ok = _run_cost(corpus_tu("O(xs.size() * log(xs.size()))", LOG_TARGET))
    check(_verdict_line(out_ok, "sort_all").startswith("✓"),
          "`sort_all` 写 `O(xs.size() * log(xs.size()))` ⇒ **达成**",
          _verdict_line(out_ok, "sort_all")[:200] or out_ok[-300:])
    check(_verdict_line(out_ok, "merge_sort").startswith("✓"),
          "`merge_sort` 写 `O((hi - lo) * log(hi - lo))` ⇒ **达成**",
          _verdict_line(out_ok, "merge_sort")[:200])
    check("阶不够" not in out_ok,
          "整份输出里**没有**「阶不够」（假债务已经消失）",
          [ln.strip()[:160] for ln in out_ok.splitlines() if "阶不够" in ln][:2])
    print()
    print("      —— 同一函数写 `O(n)`：判定必须**还能推翻**")
    out_bad = _run_cost(corpus_tu("O(xs.size())", LOG_TARGET))
    v = _verdict_line(out_bad, "sort_all")
    check(v.startswith("▲"), "`sort_all` 写 `O(xs.size())` ⇒ 判**债务**（`▲`）", v[:200])
    check("阶不够" in out_bad, "理由写「阶不够」",
          [ln.strip()[:160] for ln in out_bad.splitlines() if "阶不够" in ln][:2])
    print()


# ---------------------------------------------------------------- [6] 不覆盖 n 叉

#: N 皇后：自调用在 `for col` 里 ⇒ 每层可能 n 次（**不是**两半）。
PLACE_ELN = """
u64 place(u64 row, u64 n) {
    if (row == n) { return 1; }
    u64 s = 0;
    for (u64 col = 0; col < n; col = col + 1) {
        s = s + place(row + 1, n);
    }
    return s;
}
"""


def test_place_not_covered():
    print("[6] **n 叉递归不被「按层求和」覆盖**：`place` 不是两半（④ 给的是幂界）")
    ci = ci_of(PLACE_ELN,
               "spec place(u64 row, u64 n) -> u64 { effects {}; recursion depth <= 8; }")
    k = ci.cost_of("place")
    # **2026-11-12 夜（判据 ④）**：n 叉递归由 `_tree_activation_bound` 给
    # `(D+1)·B^D`，不再是 ⊤。这一节要钉的是「**按层求和**只对
    # `T(s) = 2·T(s/2) + f(s)` 成立」——`_halving_span` 对它仍然返回 None。
    check(not k.is_top(), "place：κ 是具体式（④ 的 (D+1)·B^D，不是 ⊤）",
          k.render())
    check("9×n^8" in k.render(), "place：κ 含 `9×n^8`（D = 8、B = n）", k.render())
    check("place" not in ci.top_reasons, "place：top_reasons 里没有它",
          str(ci.top_reasons)[:200])
    check(ci._halving_span("place") is None,
          "place：`_halving_span` **不认**（只认「恰好 2 个自调用点 + 互不相交的两半 + 早退」）")
    print("       ↑ 按层求和只对 `T(s) = 2·T(s/2) + f(s)` 成立。n 叉是")
    print("         `Σ_{k} n^k`（指数）；④ 用的是**幂界** `(D+1)·B^D`，")
    print("         不是把指数记成多项式——两个口子分开，谁也替不了谁。")
    print()


def main() -> int:
    test_shape()
    test_growth()
    test_equivalence()
    test_ops_le_kappa()
    test_fake_debt_gone()
    test_place_not_covered()

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
