#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_tools_symbolic_exp.py —— 测量工具读**符号指数 κ** 的能力

钉住三件事（2026-11-12，符号指数进 `Cost` 之后工具侧的缺口）：

1. **解析**：`tools/cost_baseline.py::parse_kappa` 要认得 `Cost.render()`
   会印出的全部幂形状——常数指数（`n(xs)^8`、`n(xs)^(3/2)`、`sqrt(n(xs))`）、
   **符号指数**（`n(xs)^(n(xs))`、`2^(n(xs))`）、以及接收者字段名
   （`n(recv.n_)^8`，名字里有**点号**）。
   **符号指数必须读成符号指数**（`value=None` + `args[1]`），
   一旦被静默折成常数，基线的"变紧/变松"就是另一道题了。
2. **读不懂要报**：分段 `{…}`、负指数、缺操作数……一律抛 `KappaParseError`，
   上层输出里必须出现「**读不懂**」+ 原因。**静默降级 = 假绿/假警报**。
3. **饱和不判定**：`n(xs)^(n(xs))` 在 n=6 是 46656（能比），在 n=64 停在
   `symbolic.MAG_CAP=1e18`（**标记值，不是上界**）。`tools/kappa_vs_ops.py`
   必须把这一档标成 `kappa_saturated` 并**不作判定**——拿 1e18 比 ops 会假绿。

**为什么用 monkeypatch 造符号指数**：κ 由 `cost_interp` 从代码算出来，
当前语料里还没有"实现侧"符号指数（那条路在 `src` 侧刚开）。要测**工具**
的行为，就在 `CostInterp.cost_of` 上注入一个 `Cost.pow(n, n)`——这与
`tests/cases/test_ops.py` 注入 `OP_COST` 是同一种做法，不动 `src`。
"""
import os
import sys
from fractions import Fraction

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
TOOLS = os.path.join(ROOT, "tools")
SAMPLE = os.path.join(ROOT, "文档", "样例")

sys.path.insert(0, SRC)
sys.path.insert(0, TOOLS)

import cost_baseline as cb                               # noqa: E402
import kappa_vs_ops as kvo                               # noqa: E402

_ok = _bad = 0


def check(cond, label, detail=""):
    global _ok, _bad
    if cond:
        _ok += 1
        print("  ✓ %s" % label)
    else:
        _bad += 1
        print("  ✗ %s%s" % (label, ("\n      " + detail) if detail else ""))


# ------------------------------------------------------------ ① 解析
def test_constant_exponent():
    """常数指数的三种印法（`^8`、`^(3/2)`、`sqrt(...)`）都要读回来。"""
    c = cb.parse_kappa("n(xs)^8")
    check(c.kind == "pow" and c.value == 8,
          "`n(xs)^8` 读成 pow(value=8)", "%r %r" % (c.kind, c.value))
    check(c.render() == "n(xs)^8", "`n(xs)^8` 原样印回", c.render())

    c = cb.parse_kappa("n(xs)^(3/2)")
    check(c.kind == "pow" and c.value == Fraction(3, 2),
          "`n(xs)^(3/2)` 读成 pow(3/2)", "%r" % (c.value,))
    check(c.render() == "n(xs)^(3/2)", "`n(xs)^(3/2)` 原样印回", c.render())

    c = cb.parse_kappa("sqrt(n(xs))")
    check(c.kind == "pow" and c.value == Fraction(1, 2),
          "`sqrt(n(xs))` 读成 pow(1/2)", "%r" % (c.value,))
    check(c.render() == "sqrt(n(xs))", "`sqrt(n(xs))` 原样印回", c.render())

    # **接收者字段名带点号**（`n(recv.n_)^8`）：以前点号是"不认识的字符"。
    c = cb.parse_kappa("n(recv.n_)^8")
    check(c.kind == "pow" and c.args[0].kind == "size"
          and c.args[0].value == "recv.n_",
          "`n(recv.n_)^8` 读成 size(recv.n_) 的 8 次幂", c.render())
    check(c.render() == "n(recv.n_)^8", "`n(recv.n_)^8` 原样印回", c.render())


def test_symbolic_exponent():
    """**符号指数**：`value=None` + `args[1]`，不许折成常数。"""
    c = cb.parse_kappa("n(xs)^(n(xs))")
    check(c.kind == "pow" and c.value is None,
          "`n(xs)^(n(xs))` 保留成符号指数（value=None）", repr(c.value))
    check(len(c.args) == 2 and c.args[1].kind == "size"
          and c.args[1].value == "xs",
          "指数是 `size(xs)`（不是常数、不是被丢掉）", repr(c.args))
    check(c.render() == "n(xs)^(n(xs))", "`n(xs)^(n(xs))` 原样印回", c.render())

    c = cb.parse_kappa("2^(n(xs))")
    check(c.kind == "pow" and c.value is None and c.args[0].kind == "num",
          "`2^(n(xs))` 读成「常数底 + 符号指数」", c.render())
    check(c.render() == "2^(n(xs))", "`2^(n(xs))` 原样印回", c.render())

    # 复合指数（`(n^n)^(1/2)` 印出来的形状）
    c = cb.parse_kappa("n(xs)^(1/2×n(xs))")
    check(c.kind == "pow" and c.value is None,
          "`n(xs)^(1/2×n(xs))` 的复合指数不折成常数", c.render())
    check(c.render() == "n(xs)^(1/2×n(xs))", "复合指数原样印回", c.render())

    # **阶**：符号指数要按 log 域判，不能因为解析器不认识就退「无法判定」。
    n8 = cb.parse_kappa("n(xs)^8")
    nn = cb.parse_kappa("n(xs)^(n(xs))")
    v, why = cb.classify(nn, n8)
    check(v == cb.LOOSER, "`n^8 → n^n` 判「变松」（log 域：n·log n > 8·log n）",
          "%s %s" % (v, why))
    v, why = cb.classify(n8, nn)
    check(v == cb.TIGHTER, "`n^n → n^8` 判「更紧」", "%s %s" % (v, why))
    v, why = cb.classify(nn, cb.parse_kappa("n(xs)^(n(xs))"))
    check(v == cb.TIGHTER, "同一个符号指数式判「同阶」", "%s %s" % (v, why))


def test_roundtrip_render():
    """`parse_kappa(render)` 要把**同一个式子**印回来（逐字）。"""
    texts = [
        "n(xs)^8", "n(xs)^(3/2)", "sqrt(n(xs))",
        "n(xs)^(n(xs))", "2^(n(xs))", "n(recv.n_)^8",
        "n(xs)^(n(ys))", "log2(n(xs))×n(xs)^2",
        "n(xs)^(1/2×n(xs))",
    ]
    bad = []
    for t in texts:
        try:
            got = cb.parse_kappa(t).render()
        except Exception as exc:                     # noqa: BLE001
            bad.append("%s → %s: %s" % (t, type(exc).__name__, exc))
            continue
        if got != t:
            bad.append("%s → %s" % (t, got))
    check(not bad, "9 种幂形状 `parse(render)` 逐字一致", "\n      ".join(bad))


# ------------------------------------------------------------ ② 读不懂要报
def test_unreadable_is_loud():
    """读不懂 → 抛错（带原因），上层输出里必须出现「读不懂」。"""
    cases = [
        ("{n(xs)<1: 5; n(xs)>0: 7}", "分段（本工具不解析）"),
        ("n(xs)^", "缺指数"),
        ("n(xs)^(-1)", "负指数不是成本上界"),
        ("n(xs)^(⊤)", "指数是 ⊤"),
        ("", "空文本"),
        ("n(recv.n_^8", "括号不配对"),
    ]
    bad = []
    for text, why in cases:
        try:
            got = cb.parse_kappa(text)
        except cb.KappaParseError as exc:
            if not str(exc):
                bad.append("%s（%s）：异常没有原因" % (text, why))
            continue
        except Exception as exc:                     # noqa: BLE001
            bad.append("%s（%s）：抛的是 %s 不是 KappaParseError"
                       % (text, why, type(exc).__name__))
            continue
        bad.append("%s（%s）：**竟然读成了** %s" % (text, why, got.render()))
    check(not bad, "6 种读不懂的文本都**明确报错**（不是静默成常数/无 κ）",
          "\n      ".join(bad))

    # 上层：旧 κ 读不懂 → 「无法判定」+ **读不懂** + 原因（不猜方向）
    nn = cb.parse_kappa("n(xs)^(n(xs))")
    v, why = cb.order_of(nn, "{n(xs)<1: 5; n(xs)>0: 7}")
    check(v == cb.UNKNOWN and "读不懂" in why,
          "旧 κ 读不懂 → 无法判定，且说「读不懂」", "%s %s" % (v, why))
    check("不认识" in why or "KappaParseError" in why,
          "输出里带上了**原因**（不是只有一句无法判定）", why)

    v, why = cb.order_text("n(xs)^8", "3//2")
    check(v == cb.UNKNOWN and "读不懂" in why,
          "新值读不懂 → 无法判定，且说「读不懂」", "%s %s" % (v, why))

    # `check()` 的行：旧文本坏掉时**必须 exit 1**（bad ≥ 1），且文本里说读不懂
    base = {"文档/样例/demo": {"f": {"kappa": "{n(xs)<1: 5}",
                                     "target": None, "verdict": "ok"}}}
    cur = {"文档/样例/demo": {"f": {"kappa": "n(xs)", "target": None,
                                    "verdict": "ok"}}}
    rows, nbad = cb.check(base, cur)
    joined = "\n".join("%s %s" % (m, t) for m, _n, t in rows)
    check(nbad == 1 and "读不懂" in joined and "无法判定" in joined,
          "基线里那条坏 κ 变了 → 判失败并在行里写「读不懂」", joined)


# ------------------------------------------------------------ ③ 饱和不判定
def test_saturation_helper():
    """`kappa_value_kind`：唯一判据——有限可判 / inf / 饱和。"""
    cap = kvo.mag_cap()
    from cost_domain.symbolic import MAG_CAP
    check(cap == float(MAG_CAP), "`mag_cap()` 就是 `symbolic.MAG_CAP`（不另立数）",
          "%r vs %r" % (cap, MAG_CAP))
    check(kvo.kappa_value_kind(46656.0) is None, "n=6 的 46656 可判定")
    check(kvo.kappa_value_kind(0.0) is None, "0 可判定")
    check(kvo.kappa_value_kind(float(MAG_CAP)) == "saturated"
          and kvo.kappa_value_kind(float(MAG_CAP) * 2) == "saturated",
          "恰好 1e18 / 超过 1e18 都 → 饱和（标记值，不是上界）")
    check(kvo.kappa_value_kind(float("inf")) == "inf", "+∞ → inf（符号项）")
    check(kvo.kappa_value_kind(float("nan")) == "inf", "NaN → inf（不可判）")
    check(kvo.kappa_value_kind(None) == "inf", "None → inf（求不出）")


def _symbolic_kappa():
    from cost_domain.symbolic import Cost
    return Cost.pow(Cost.size("scores"), Cost.size("scores"))


def test_measure_one_symbolic_exponent():
    """`kappa_vs_ops` 对含符号指数的函数：小 n 能测、大 n 饱和不判定。"""
    import cost_interp

    orig = cost_interp.CostInterp.cost_of

    def fake(self, fn):
        if fn == "average":
            return _symbolic_kappa()
        return orig(self, fn)

    cost_interp.CostInterp.cost_of = fake
    try:
        small = kvo.measure_one(SAMPLE, "sum", 6, only_target=True)
        big = kvo.measure_one(SAMPLE, "sum", 64, only_target=True)
    finally:
        cost_interp.CostInterp.cost_of = orig

    small_avg = [r for r in small if r["function"] == "average"]
    big_avg = [r for r in big if r["function"] == "average"]
    check(len(small_avg) == 1 and len(big_avg) == 1,
          "两个档都拿到了 `sum.average` 一行",
          "small=%s big=%s" % (small_avg, big_avg))
    if not small_avg or not big_avg:
        return
    s, b = small_avg[0], big_avg[0]
    check(s["status"] == "ok", "n=6：κ 能算 → 正常判定", s.get("status"))
    check(s.get("kappa_at_n") == 46656, "n=6：κ=6^6=46656", repr(s.get("kappa_at_n")))
    check(s.get("holds") is True and s.get("ops", 0) <= 46656,
          "n=6：ops ≤ 46656 是真判定（不是饱和）",
          "ops=%r" % (s.get("ops"),))

    check(b["status"] == "kappa_saturated",
          "n=64：κ 饱和 → 状态单列 `kappa_saturated`", b.get("status"))
    check(b.get("holds") is None and b.get("ops") is None,
          "饱和时**没有**跑 ops、也**没有** holds（不许拿 1e18 比大小）",
          "holds=%r ops=%r" % (b.get("holds"), b.get("ops")))
    check("不作判定" in b.get("detail", "") and "标记值" in b.get("detail", ""),
          "饱和的 detail 说清「标记值 / 不作判定」", b.get("detail"))


def test_summary_and_table_saturated():
    """汇总：饱和单独一栏，**不进 ok_full**；表格里印「饱和」而不是 1e18。"""
    rows = [
        {"program": "p", "function": "f", "n": 6, "status": "ok",
         "holds": True, "ops": 47, "kappa_at_n": 46656, "value": "1"},
        {"program": "p", "function": "f", "n": 64,
         "status": "kappa_saturated", "kappa": "n(scores)^(n(scores))"},
    ]
    s = kvo.summarize(rows, [6, 64])
    check(("p", "f") in [tuple(x[:2]) for x in s["saturated"]],
          "饱和的函数进 `saturated` 栏", repr(s))
    check(("p", "f") not in s["ok_full"],
          "饱和**不**进 `ok_full`（1e18 不是证据）", repr(s["ok_full"]))
    check(("p", "f") not in [tuple(x[:2]) for x in s["broken"]],
          "饱和**不**算破界", repr(s["broken"]))
    tbl = kvo.render_table(rows, [6, 64])
    check("饱和" in tbl and "不判定" in tbl, "表格里这一档印「饱和 / 不判定」", tbl)
    check("1e18" not in tbl and "1,000,000,000,000,000,000" not in tbl,
          "表格里**不出现** 1e18（不把它当真实上界展示）", tbl)

    # **破界优先**：同一函数在 n=6 真破界、n=64 饱和 ⇒ 必须报破界，
    # 不能被「饱和」那一栏盖掉（否则真发现静默消失）。
    mixed = [
        {"program": "q", "function": "g", "n": 6, "status": "ok",
         "holds": False, "ops": 100000, "kappa_at_n": 10, "value": "1"},
        {"program": "q", "function": "g", "n": 64,
         "status": "kappa_saturated", "kappa": "n(xs)^(n(xs))"},
    ]
    s2 = kvo.summarize(mixed, [6, 64])
    check(("q", "g") in [tuple(x[:2]) for x in s2["broken"]]
          and ("q", "g") not in [tuple(x[:2]) for x in s2["saturated"]],
          "真破界 + 另一档饱和 → 报**破界**（不被饱和盖掉）", repr(s2))


def main() -> int:
    print("--- ① 解析：常数指数")
    test_constant_exponent()
    print("--- ① 解析：符号指数")
    test_symbolic_exponent()
    print("--- ① 解析：parse(render) 逐字一致")
    test_roundtrip_render()
    print("--- ② 读不懂要报")
    test_unreadable_is_loud()
    print("--- ③ 饱和：判据")
    test_saturation_helper()
    print("--- ③ 饱和：kappa_vs_ops 的实测行为")
    test_measure_one_symbolic_exponent()
    print("--- ③ 饱和：汇总与表格")
    test_summary_and_table_saturated()
    print()
    if _bad:
        print("✗ %d 项失败 / %d 项通过" % (_bad, _ok))
        return 1
    print("✓ 全部通过（%d 项）" % _ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
