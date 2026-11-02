#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_gaps.py —— 四类 κ 缺口的**微例钉子**（①递归 ②内建 ③min/max ④函数值）

## 这份测试钉什么

四类「κ 比实测少」的缺口，每类一个**最小复现**。修前数字是用
`src/cost_interp.py.bak`（本轮改动前的副本）实测的，修后在同一份样例上重测：

| # | 微例（n=16） | 实测 ops | 修前 κ | 修后 κ | 修前缺口 |
|---|---|---:|---:|---:|---:|
| ① | `down(xs, 8)`（无深度声明） | 104 | 33 | 33 | **+71**（未声明 → 修前行为） |
| ① | `down(xs, 8)`（`recursion depth <= 8`） | 104 | 33 | **153** | 0 |
| ① | `main8(xs)` 调 `down(xs, 8)` | 108 | 20 | **157** | 0 |
| ② | `print("x")` | 6 | 5 | **6** | 0 |
| ② | `map(xs, visit)` | 36 | 66 | 67 | 0（尺寸项本来就在） |
| ② | `filter(xs, keep)` | 68 | 98 | 99 | 0 |
| ② | `fold(xs, 0, addv)` | 69 | 98 | 100 | 0 |
| ② | `count_if(xs, keep)` | 68 | 98 | 99 | 0 |
| ② | `sort(xs)` | 4 | 65 | 68 | 0 |
| ② | `sort_by(xs, cmpv)`（比较器无 target） | 124 | 257 | 260 | 0 |
| ② | `for_each(m, visit2)` | 15 | inf | inf | 符号项（`k(visit2)`） |
| ③ | `max(b, 1)` / `min(b, 1)` | 5 | 4 | **5** | 0 |
| ④ | `host_u(xs)`（`g = countf` 静态可求） | 10 | 6 | **22** | 0 |
| ④ | `host_v(xs)`（`g = idf` 静态可求） | 8 | 6 | **8** | 0 |
| ④ | `host_vn(xs)`（`g(idf(xs))`） | 11 | 6 | **11** | 0 |
| ④ | `apply_u(xs, g)`（调用点不知道 g） | — | 3 | inf + `k(g)` | 符号项 |

**判据**：每个微例都断 `ops ≤ κ`（符号项那两条断 `κ` 里出现 `k(...)`、数值给
`inf`——`inf ≥ ops` 成立，但**不作为数值证据**）。「修前 κ」同时用来断
**κ 只增不减**：修后的 κ 必须 ≥ 上表左列的旧值。

## 为什么 ② 里有些微例修前就成立

`map/filter/fold/count_if` 的 `n × κ(f)` 尺寸项本来就在，缺的只是**实参表达式**
（`xs` 求值那 1 步）；所以修前缺口是负的（κ 有余量），修后仍然负——这不是
「没修」，是**尺寸项把实参那 1 步盖住了**。真正被实参卡住的是 `print`（κ 5、
ops 6）和 `sort_by`（比较器很小的时候）。③ 的 `min/max` 差的是**内建派发**
那 1 步，不是实参。

## ④ 的两条支路

- **静态可求**：`host_u(xs)` 里 `apply_u(xs, countf)`，实参是具名函数 ⇒
  被调体 `countf` 的 κ 计入调用方 ⇒ 缺口 **0**（修前缺口恰好 = κc）。
- **符号**：直接分析 `apply_u(xs, g)` 本身 ⇒ κ 里是 `k(g)`（契约项），
  `cost_check.evaluate` 给 `inf`。`tools/kappa_vs_ops.py` 必须**打印 inf 并
  跳过该点**，不许在 `int(inf)` 上崩——本文件最后一段用临时目录真跑一遍工具。

跑法：

    cd src; $env:PYTHONIOENCODING="utf-8"; python test_kappa_gaps.py
"""

import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但**这批测试原先住在 `src/`**，里面大量
#: `os.path.join(HERE, "..", "文档")` / `os.path.join(ROOT, "tools")` 都假设
#: 「本文件在 src/、上一级是仓库根」。搬家时改 200 处路径比让 `HERE` 继续指 src 贵得多，
#: 所以保留旧语义：`HERE` = src/，`CASEDIR` = 本文件真正所在的目录。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import io
import math
import os
import sys
import tempfile

# **src 优先**：`tools/` 里也有 `.py`，别让它遮住 `interp` / `parser`
sys.path.insert(0, os.path.join(ROOT, "tools"))

from cost_check import evaluate as kappa_at            # noqa: E402
from cost_interp import CostInterp                     # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset   # noqa: E402
from parser import parse_source                        # noqa: E402
from symbolic import Cost                              # noqa: E402

import kappa_vs_ops as kvo                             # noqa: E402

_passed = 0
_failed = []

#: 修前 κ（`cost_interp.py.bak` 实测）。用来断「κ 只增不减」。
PRE = {
    "down8": 33.0, "main8": 20.0,
    "print": 5.0, "map": 66.0, "filter": 98.0, "fold": 98.0,
    "count_if": 98.0, "sort": 65.0, "sort_by": 257.0,
    "max": 4.0, "min": 4.0,
    "host_u": 6.0, "host_v": 6.0, "host_vn": 6.0,
}


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def load(eln: str, tu: str = ""):
    ch = parse_source(eln, "gaps.eln").module
    decl = parse_source(tu, "gaps.tu").module if tu else None
    return ch, decl


def kappa(eln: str, tu: str, fn: str, env: dict) -> float:
    """κ(fn) 在给定尺寸赋值下的数值；含符号项时是 `inf`。"""
    ch, decl = load(eln, tu)
    k = CostInterp(ch, decl).cost_of(fn)
    return kappa_at(k, dict(env))


def kappa_cost(eln: str, tu: str, fn: str):
    ch, decl = load(eln, tu)
    return CostInterp(ch, decl).cost_of(fn)


def ops_of_call(fn: str, args, eln: str, tu: str = "") -> int:
    """整棵调用树的实测操作数。"""
    ch, decl = load(eln, tu)
    ops_enable(True)
    try:
        ops_reset()
        Interp(ch, decl).call(fn, args)
        return ops_of()
    finally:
        ops_enable(False)


def xs(n: int = 16):
    return [i for i in range(n)]


# ---------------------------------------------------------------- ① 递归深度
RECUR = """
func down(const Vec<u64>& xs, u64 i) -> u64 {
    if (i == 0) { return xs.length(); }
    return 1 + down(xs, i - 1);
}
func main8(const Vec<u64>& xs) -> u64 { return down(xs, 8); }
"""


def tu_rec(unbounded: bool = False) -> str:
    """`recursion depth <= N` 是**唯一**的深度声明（规格 C11-b）。"""
    return """
spec down(const Vec<u64>& xs, u64 i) -> u64 {
    effects {};
""" + ("" if unbounded else "    recursion depth <= 8;\n") + """
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec main8(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""


def test_recursion_depth():
    print("[1] 递归：κ 按 `.tu` 的 `recursion depth <= N` 乘「函数体只算一次」")
    n = 16
    # ①-a 声明 D=8：`down(xs, 8)` 的 ops ≤ κ，且 κ 从 33 涨到 153
    k8 = kappa(RECUR, tu_rec(), "down", {"xs": n, "i": 8})
    o8 = ops_of_call("down", [xs(n), 8], RECUR, tu_rec())
    check(o8 == 104, f"ops(down, i=8) = 104（修前实测，作为基准）", str(o8))
    check(k8 >= PRE["down8"],
          f"κ(down, i=8) = {k8:g} ≥ 修前 {PRE['down8']:g}（只增不减）", str(k8))
    check(o8 <= k8, f"ops {o8} ≤ κ {k8:g}（修前 κ=33，缺口 71）")
    check(k8 == 153, f"κ(down, i=8) = 153 = (8+1) × κ(体一次 17)", str(k8))

    # ①-b 深度**声明**变大 → κ 严格增长（同一份实现、同一档 n）
    seq = []
    for d in (1, 2, 4, 8, 16):
        kd = kappa(RECUR, tu_rec_for(d), "down", {"xs": n, "i": d})
        od = ops_of_call("down", [xs(n), d], RECUR, tu_rec_for(d))
        seq.append(kd)
        check(od <= kd, f"D={d:2d}：ops {od} ≤ κ {kd:g}")
    check(all(a < b for a, b in zip(seq, seq[1:])),
          f"声明深度 1→16：κ 严格增长 {seq}", str(seq))

    # ①-c 调用方也覆盖：`main8` 一次调用会压 8 层
    km = kappa(RECUR, tu_rec(), "main8", {"xs": n})
    om = ops_of_call("main8", [xs(n)], RECUR, tu_rec())
    check(km >= PRE["main8"],
          f"κ(main8) = {km:g} ≥ 修前 {PRE['main8']:g}（只增不减）", str(km))
    check(om <= km, f"ops(main8) {om} ≤ κ {km:g}（修前 κ=20，缺口 88）")

    # ①-d **没有声明**：保持修前那一层（不假装覆盖），这是「诚实的不覆盖」
    k0 = kappa(RECUR, tu_rec(unbounded=True), "down", {"xs": n, "i": 8})
    o0 = ops_of_call("down", [xs(n), 8], RECUR, tu_rec(unbounded=True))
    check(k0 == PRE["down8"],
          f"无 `recursion depth` 时 κ 保持修前 {PRE['down8']:g}（不猜深度）",
          str(k0))
    check(o0 > k0, f"（已知未覆盖）无声明时 ops {o0} > κ {k0:g} —— 见报告")
    print()


def tu_rec_for(d: int) -> str:
    return """
spec down(const Vec<u64>& xs, u64 i) -> u64 {
    effects {};
    recursion depth <= %d;
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec main8(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
""" % d


# ---------------------------------------------------------------- ② 内建实参
BUILTINS = """
func visit(u64 v) -> u64 { return v; }
func visit2(const Vec<u8>& k, u64 v) -> u64 { return v; }
func keep(u64 v) -> bool { return v > 0; }
func addv(u64 a, u64 b) -> u64 { return a + b; }
func cmpv(u64 a, u64 b) -> bool { return a < b; }
func p1() -> u64 { print("x"); return 0; }
func p2() -> u64 {
    HashMap<Vec<u8>, u64> m = map_new();
    map_put(m, "a", 1);
    return for_each(m, visit2);
}
func map1(const Vec<u64>& xs) -> Vec<u64> { return map(xs, visit); }
func filt(const Vec<u64>& xs) -> Vec<u64> { return filter(xs, keep); }
func fold1(const Vec<u64>& xs) -> u64 { return fold(xs, 0, addv); }
func cnt(const Vec<u64>& xs) -> u64 { return count_if(xs, keep); }
func srt(const Vec<u64>& xs) -> Vec<u64> { return sort(xs); }
func srtb(const Vec<u64>& xs) -> Vec<u64> { return sort_by(xs, cmpv); }
"""

#: 比较器**不写 target**：`sort_by` 的 `n log n × κ(cmp)` 用**函数体** κ。
#: （若写 `target { max: O(1) }`，那个 1 是"至多常数倍"不是精确值，模型仍偏松——
#:  见报告「仍未覆盖」。）
BUILTINS_TU = ""


def test_builtin_args():
    print("[2] 内建分支：`print` / HOF / `for_each` / `sort(_by)` 的实参进 κ")
    n = 16
    cases = (
        ("print", "p1", [], 5.0, 6),
        ("map", "map1", [xs(n)], 66.0, 36),
        ("filter", "filt", [xs(n)], 98.0, 68),
        ("fold", "fold1", [xs(n)], 98.0, 69),
        ("count_if", "cnt", [xs(n)], 98.0, 68),
        ("sort", "srt", [xs(n)], 65.0, 4),
        ("sort_by", "srtb", [xs(n)], 257.0, 124),
    )
    for key, fn, args, pre, want_ops in cases:
        k = kappa(BUILTINS, BUILTINS_TU, fn, {"xs": n})
        o = ops_of_call(fn, args, BUILTINS, BUILTINS_TU)
        check(o == want_ops, f"{key}：ops = {want_ops}（基准）", str(o))
        check(k >= pre, f"{key}：κ {k:g} ≥ 修前 {pre:g}（只增不减）", str(k))
        check(o <= k, f"{key}：ops {o} ≤ κ {k:g}")
    # **真正被实参卡住的那一条**：`print("x")` 修前 κ=5 < ops=6
    kp = kappa(BUILTINS, BUILTINS_TU, "p1", {"xs": n})
    check(kp == 6 and PRE["print"] == 5,
          f"print：κ 5 → {kp:g}（补上实参那 1 步）", str(kp))
    # **符号项**：`for_each(m, visit2)` 的 k(visit2) 是契约项 ⇒ κ = inf。
    # `inf ≥ ops` 成立，但**不作为数值证据**——只钉「符号在、工具不崩」。
    kf = kappa(BUILTINS, BUILTINS_TU, "p2", {"m": 1})
    of = ops_of_call("p2", [], BUILTINS, BUILTINS_TU)
    check("k(visit2)" in kappa_cost(BUILTINS, BUILTINS_TU, "p2").render(),
          "for_each：κ 含 k(visit2)（契约项）",
          kappa_cost(BUILTINS, BUILTINS_TU, "p2").render())
    check(math.isinf(kf) and of <= kf,
          f"for_each：ops {of} ≤ κ inf（符号项，非数值证据）", str(kf))
    print()


# ---------------------------------------------------------------- ③ min/max
MINMAX = """
func mx(u64 b) -> u64 { return max(b, 1); }
func mn(u64 b) -> u64 { return min(b, 1); }
"""


def test_minmax_dispatch():
    print("[3] `min/max`：补上内建派发那 1 步（修前 ops 5 vs κ 4）")
    for fn, key in (("mx", "max"), ("mn", "min")):
        k = kappa(MINMAX, "", fn, {"b": 7})
        o = ops_of_call(fn, [7], MINMAX)
        check(o == 5, f"{key}：ops = 5（基准）", str(o))
        check(k >= PRE[key], f"{key}：κ {k:g} ≥ 修前 {PRE[key]:g}", str(k))
        check(o <= k, f"{key}：ops {o} ≤ κ {k:g}")
        check(o - k == 0, f"{key}：缺口 {o - k:g} = 0（修前 1）")
    print()


# ---------------------------------------------------------------- ④ 函数值
FNVAL = """
func countf(const Vec<u64>& xs) -> u64 { return xs.length(); }
func idf(const Vec<u64>& xs) -> Vec<u64> { return xs; }
func apply_u(const Vec<u64>& xs, func(Vec<u64>) -> u64 g) -> u64 { return g(xs); }
func apply_v(const Vec<u64>& xs, func(Vec<u64>) -> Vec<u64> g) -> Vec<u64> {
    return g(xs);
}
func apply_vn(const Vec<u64>& xs, func(Vec<u64>) -> Vec<u64> g) -> Vec<u64> {
    return g(idf(xs));
}
func host_u(const Vec<u64>& xs) -> u64 { return apply_u(xs, countf); }
func host_v(const Vec<u64>& xs) -> Vec<u64> { return apply_v(xs, idf); }
func host_vn(const Vec<u64>& xs) -> Vec<u64> { return apply_vn(xs, idf); }
"""

FNVAL_TU = """
spec countf(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec apply_u(const Vec<u64>& xs, func(Vec<u64>) -> u64 g) -> u64 { effects {}; }
spec apply_v(const Vec<u64>& xs, func(Vec<u64>) -> Vec<u64> g) -> Vec<u64> { effects {}; }
"""

#: `for_each` 的 κ 是**符号**（`k(visit2)`）：工具必须打印 inf、跳过该点。
FGAP = """
func visit2(const Vec<u8>& k, u64 v) -> u64 { return v; }
func f(const Vec<u64>& xs) -> u64 {
    HashMap<Vec<u8>, u64> m = map_new();
    map_put(m, "a", 1);
    return for_each(m, visit2);
}
"""

FGAP_TU = """
spec f(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}
"""


def test_fnvalue():
    print("[4] 函数值调用：具名函数实参 → 被调体进 κ；否则 k(g) 符号项")
    n = 16
    # `host_u` 的被调体 `countf` **有 target**（`O(xs.length())`），所以 κ 用的是
    # target，比函数体松 ⇒ 缺口是负的（有余量）。`host_v` / `host_vn` 的被调体
    # 没有 target（走内联），κ 正好等于 ops —— 那两条断**缺口 0**。
    for fn, pre, want_ops, exact in (("host_u", 6.0, 10, False),
                                     ("host_v", 6.0, 8, True),
                                     ("host_vn", 6.0, 11, True)):
        k = kappa(FNVAL, FNVAL_TU, fn, {"xs": n})
        o = ops_of_call(fn, [xs(n)], FNVAL, FNVAL_TU)
        check(o == want_ops, f"{fn}：ops = {want_ops}（基准）", str(o))
        check(k >= pre, f"{fn}：κ {k:g} ≥ 修前 {pre:g}（只增不减）", str(k))
        check(o <= k, f"{fn}：ops {o} ≤ κ {k:g}")
        if exact:
            check(o - k == 0, f"{fn}：缺口 {o - k:g} = 0（修前 = κ(被调体)）")
        else:
            check(o - k <= 0,
                  f"{fn}：缺口 {o - k:g} ≤ 0（被调体有 target，κ 偏松）")

    # 调用点**不知道** g 绑到谁：κ 里必须有 `k(g)`，数值是 inf（不是 0）
    ka = kappa_cost(FNVAL, FNVAL_TU, "apply_u")
    check("k(g)" in ka.render(),
          f"apply_u：κ 含符号项 k(g)（修前是 3，**低估**）", ka.render())
    check(math.isinf(kappa_at(ka, {})),
          "apply_u：evaluate 给 inf（不是数值）", str(kappa_at(ka, {})))
    check(kappa_at(Cost("sym", "k(g)"), {}) == float("inf"),
          "Cost(sym)：evaluate 给 inf（符号项的统一约定）")

    # 工具：**同一份符号 κ** 必须在 kappa_vs_ops 里「打印 inf + 跳过」，不许崩
    tmp = tempfile.mkdtemp(prefix="kgap_")
    try:
        for name, text in (("fgap.eln", FGAP), ("fgap.tu", FGAP_TU)):
            with io.open(os.path.join(tmp, name), "w",
                         encoding="utf-8", newline="\n") as fh:
                fh.write(text.lstrip("\n"))
        rows = kvo.measure_one(tmp, "fgap", 8)
        check(len(rows) == 1 and rows[0]["status"] == "kappa_inf",
              "kappa_vs_ops：κ 含符号 → status=kappa_inf（不崩）",
              str(rows[:1]))
        if rows:
            check("inf" in str(rows[0].get("detail", "")).lower()
                  or rows[0]["status"] == "kappa_inf",
                  "kappa_vs_ops：该点被**跳过**（不判成立、不判破界）",
                  str(rows[0]))
        summary = kvo.summarize(rows, [8])
        check(("fgap", "f") in summary.get("symbolic", []),
              "kappa_vs_ops：summarize 把它放进 symbolic 栏（不进 ok_full）",
              str(summary.get("symbolic")))
        check(not summary.get("broken"),
              "kappa_vs_ops：符号项**不**算破界", str(summary.get("broken")))
        table = kvo.render_table(rows, [8])
        check("inf" in table, "kappa_vs_ops：表格里照实打印 inf", table)
    finally:
        for name in ("fgap.eln", "fgap.tu"):
            p = os.path.join(tmp, name)
            if os.path.exists(p):
                os.remove(p)
        os.rmdir(tmp)
    print()


def main() -> int:
    test_recursion_depth()
    test_builtin_args()
    test_minmax_dispatch()
    test_fnvalue()

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
