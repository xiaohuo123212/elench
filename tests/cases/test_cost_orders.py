#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_cost_orders.py —— 阶判定用**结构判据**，不是数值斜率。

复现的旧洞（2026-11-08）：`n log n` 对 `O(n)` 的判定取决于采样区间。
旧实现把两边代进 `LADDER = 2^8 … 2^40` 拟合斜率：

    · 默认区间（8…40）：slope = 0.0674 → 债务（侥幸判对）
    · 换到 2^20…2^40 ：slope = 0.0493 < 0.05 → **判「达成」**（同一份代码）
    · κ = 100000 + n log n：常数把前段压平，slope = -0.0251 → 也判「达成」

结构判据直接读 `Cost` 的 `kind`/`args`，把阶归约成
`(多项式次数, log 因子数)`，**与采样点无关**。
"""

import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
if SRC not in _sys.path:
    _sys.path.insert(0, SRC)

import sys
from fractions import Fraction

sys.stdout.reconfigure(encoding="utf-8")

import cost_check as cc
from cost_check import compare, evaluate, _order_le, _profile
from symbolic import PIECE_BIG, Cost

_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def n(name="xs"):
    return Cost.size(name)


# ---------------------------------------------------------------- 必测用例

def test_required_cases():
    print("[1] 六个必测用例")
    v = compare(Cost.mul(n(), Cost.log2(n())), n())
    check(v.kind == "debt", "κ = n log n，target O(n) → 债务", v.render())
    check("结构判据" in v.note, "判定来自结构判据（不是斜率）", v.note)

    check(compare(n(), n()).kind == "ok", "κ = n，target O(n) → 达成")
    check(compare(Cost.num(5), n()).kind == "ok", "κ = 5，target O(n) → 达成")
    check(compare(Cost.mul(n(), n()), n()).kind == "debt",
          "κ = n×n，target O(n) → 债务")
    check(compare(n(), Cost.mul(n(), Cost.log2(n()))).kind == "ok",
          "κ = n，target O(n log n) → 达成（低阶满足高承诺）")

    vt = compare(Cost.top(), n())
    check(vt.kind == "unknown", "κ = ⊤ → 不判（unknown）", vt.render())
    # 构造函数会把 ⊤ 吸收进 add/max；复合式同样不判
    check(compare(Cost.add(n(), Cost.top()), n()).kind == "unknown",
          "κ 含 ⊤ 的复合式 → 不判")


def test_range_independence():
    """**旧洞的正面复现**：换采样区间不许改变判定。"""
    print("[2] 结构判据与采样区间无关")
    old = cc.LADDER
    try:
        cc.LADDER = [2 ** k for k in range(20, 41)]     # 旧实现这里 slope=0.0493
        v = compare(Cost.mul(n(), Cost.log2(n())), n())
        check(v.kind == "debt", "LADDER 换成 2^20…2^40 仍判债务", v.render())
        v2 = compare(Cost.add(Cost.num(100000), Cost.mul(n(), Cost.log2(n()))), n())
        check(v2.kind == "debt", "常数 100000 压不平常数项，仍判债务", v2.render())
    finally:
        cc.LADDER = old


# ---------------------------------------------------------------- 归约规则

def test_profile_rules():
    print("[3] 归约规则逐条")
    check(_profile(Cost.num(5), "xs") == (0, 0, 0), "num → (0,0,0)")
    check(_profile(n(), "xs") == (1, 0, 0), "size(var) → (1,0,0)")
    check(_profile(Cost.log2(n()), "xs") == (0, 1, 0),
          "log(size) → (0,1,0)，不抬次数")
    check(_profile(Cost.mul(n(), Cost.log2(n())), "xs") == (1, 1, 0),
          "mul → 次数/ log 相加")
    check(_profile(Cost.add(Cost.mul(n(), n()), n()), "xs") == (2, 0, 0),
          "add → 取支配项")
    check(_profile(Cost.max(Cost.log2(n()), n()), "xs") == (1, 0, 0),
          "max → 取支配项")

    # 常数项不影响阶：2 + n×7 + 2 → n
    messy = Cost.add(Cost.num(2),
                     Cost.add(Cost.mul(Cost.num(7), n()), Cost.num(2)))
    check(_profile(messy, "xs") == (1, 0, 0), "2 + n×7 + 2 归约成 n", str(messy))
    check(_order_le(messy, n(), "xs") is True, "常数项不影响「阶 ≤」")

    # log 是低于任何正次数的因子（数学事实 log n = o(n)）
    check(_order_le(Cost.log2(n()), n(), "xs") is True, "log n ≤ n")
    check(_order_le(n(), Cost.log2(n()), "xs") is False, "n ≰ log n")
    check(_order_le(Cost.mul(Cost.log2(n()), Cost.log2(n())), n(), "xs") is True,
          "log² n ≤ n")
    check(_order_le(Cost.mul(n(), Cost.log2(n())),
                    Cost.mul(n(), n()), "xs") is True, "n log n ≤ n²")

    # 标量形参在 Cost 里是 `sym`，与 `size` 同等当尺寸量
    check(_order_le(Cost("sym", "dim"), n("dim"), "dim") is True,
          "sym(dim) 与 size(dim) 同阶")

    # 判不了的边界
    check(_order_le(n(), n("ys"), "xs") is None, "别的尺寸量 → None")
    check(_profile(Cost("sym", "k(f)"), "xs") is None, "k(f) → None")
    check(_profile(Cost.top(), "xs") is None, "⊤ → None")
    check(_profile(Cost("ext", "read_file"), "xs") is None, "ext → None")


# ---------------------------------------------------------------- 判不了 = unknown

def test_fallback():
    print("[4] 结构判不了 → unknown（**不再**退回数值拟合）")
    # **piece 现在能判**（2026-11-10）：分段 = 各段阶的 max，任一段判不了才
    # unknown。旧行为是"分段一律 None → 退回数值拟合"，换个采样区间结论就翻。
    p = Cost.piece("xs", [(1, PIECE_BIG, n())], n())
    v = compare(p, n())
    check(v.kind == "ok", "piece（单段 n）vs O(n) → 达成", v.render())
    check("结构判据" in (v.note or ""), "piece 的结论来自结构判据", v.note)

    # ---- **取最严的那段**：有任意一段超阶 → 整体债务（不能只挑已知/达标段）。
    mixed = Cost.piece("xs", [(1, 10, Cost.log2(n())), (11, PIECE_BIG, n())], n())
    check(_profile(mixed, "xs") == (1, 0, 0),
          "piece(log, n) 的 profile = 各段 max = n", str(_profile(mixed, "xs")))
    check(compare(mixed, Cost.log2(n())).kind == "debt",
          "piece(log, n) vs O(log n) → 债务（最严的那段是 n）",
          compare(mixed, Cost.log2(n())).render())
    check(compare(mixed, n()).kind == "ok", "同一个 piece vs O(n) → 达成")

    # ---- **越界回退也进 max**：它是 n 超出声明区间时真的会取到的成本。
    fb = Cost.piece("xs", [(1, 10, Cost.log2(n()))], n())
    check(_profile(fb, "xs") == (1, 0, 0),
          "piece 的越界回退也参与 max（否则声明区间外不是上界）",
          str(_profile(fb, "xs")))
    check(compare(fb, Cost.log2(n())).kind == "debt",
          "回退是 n → 对 O(log n) 判债务")

    # ---- **任一段认不出 → 整段 None**（保守：未知段可能才是最大的）。
    fam = Cost.piece("xs", [(1, 10, n()), (11, PIECE_BIG, Cost("ext", "read_file"))], n())
    check(_profile(fam, "xs") is None, "piece 里有一段是 ext → None", str(fam))
    vf = compare(fam, n())
    check(vf.kind == "unknown", "piece 里有家族外的段 → unknown", vf.render())
    check("超出可判定的族" in vf.note and "ext(read_file)" in vf.note,
          "note 报到**那一段**的家族外原因（piece 本身不再报 unimplemented）", vf.note)

    p4 = Cost.piece("xs", [(1, 10, n()), (11, PIECE_BIG, Cost("weird"))], n())
    v4 = compare(p4, n())
    check(v4.kind == "unknown", "piece 里有陌生节点 → unknown", v4.render())
    check("结构判据还没实现" in v4.note,
          "陌生节点仍报「没实现」（只有 piece 本身不再是这一类）", v4.note)

    # ext：求值给 inf，数值也判不了 → unknown；归入「超出可判定的族」
    e = Cost.add(n(), Cost("ext", "read_file"))
    ve = compare(e, n())
    check(ve.kind == "unknown", "含 ext → unknown", ve.render())
    check("超出可判定的族" in ve.note and "ext(read_file)" in ve.note,
          "unknown 的 note 指出是外部项", ve.note)

    # **复现原始现场**：κ = 7n+2 对 target = O(log log log n) 以前给「超标」，
    # 因为结构判不了就退回数值拟合（斜率为正 → debt）。现在必须 unknown。
    lll3 = Cost.log2(Cost.log2(Cost.log2(n())))
    v3 = compare(Cost.add(Cost.mul(Cost.num(7), n()), Cost.num(2)), lll3)
    check(v3.kind == "unknown", "κ=7n+2 vs O(三层 log) → unknown（不给假债务）",
          v3.render())
    check("三层 log" in v3.note and "不用数值拟合" in v3.note,
          "note 指出三层 log 且明确不猜", v3.note)

    # **判定不再随 LADDER 变**：结构判据与采样区间无关。
    old = cc.LADDER
    try:
        cc.LADDER = [2 ** k for k in range(20, 41)]
        check(compare(p, n()).kind == "ok", "piece 的结论与 LADDER 无关")
        check(compare(mixed, Cost.log2(n())).kind == "debt",
              "piece 取 max 的结论也与 LADDER 无关")
        check(compare(Cost.add(Cost.mul(Cost.num(7), n()), Cost.num(2)),
                      lll3).kind == "unknown",
              "三层 log 的结论与 LADDER 无关（以前会从 ok 翻成 debt）")
    finally:
        cc.LADDER = old

    # 多尺寸量：只有**逐项支配**才判达成；这里是 target = κ 本身，支配成立
    both = Cost.mul(n("xs"), n("ys"))
    vm = compare(both, both)
    check(vm.kind == "ok" and vm.multi, "多尺寸量逐项支配 → 达成",
          vm.render())
    check("所有尺寸量 = n" in vm.render(),
          "多尺寸的输出标注「所有尺寸量 = n」", vm.render())


# ---------------------------------------------------------------- 多尺寸量

def test_multi_size_orders():
    """**多尺寸量三分法**（2026-11-09 起，2026-11-10 细化）。

    旧洞 1（2026-11-09）：多尺寸量跳过结构判据、退回 `_ratios`，而 `_ratios`
    只把字母序第一个尺寸设成 n、其余当 1 —— 于是 `κ = n×m` 对 `target = n+m`
    的比值恒定，**二次实现过线性契约**。两条最小复现（KMP / 并查集）都撞到它。

    旧洞 2（2026-11-10）：修洞 1 时把"判不了"写成了"一律 unknown"，于是
    **可证超出**（`κ = n×m` 对 `target = n+m`）与**不可比**（`κ = n×m` 对
    `target = n²`）拿到同一句「两个尺寸量的关系未知」——前者用户该去优化，
    后者才是"不知道"。三分法：

        (C) 逐项支配              → ok
        (A) ∃e 严格领先 target 每一项 → **debt**（可证，文案写明哪维高几次）
        (B) 有的维大有的维小        → unknown（**只有这一种**是判不了）
    """
    print("[4b] 多尺寸量：可证超出给债务，只有不可比才 unknown")
    t, p = n("text"), n("pattern")
    xs, ys = n("xs"), n("ys")

    # ---- 复现 A（KMP）：κ = text×(28·pattern + …)，target = text + pattern
    # **2026-11-10 翻转（unknown → debt）**：κ 的 `text×pattern` 对 target 的
    # **每一项**都严格领先（`text` 项缺 `pattern`、`pattern` 项缺 `text`），
    # 所以 κ/target = text×pattern/(text+pattern) 沿"两个尺寸一起大"的**所有**
    # 方向都无界——这是**可证**超出（新旧判定都"不是 ok"，但旧的说不知道）。
    kmp = Cost.add(Cost.mul(t, Cost.add(Cost.mul(Cost.num(28), p), Cost.num(7))),
                   Cost.add(t, p))
    va = compare(kmp, Cost.add(t, p))
    check(va.kind != "ok", "A：κ=text×pattern vs target=text+pattern 不再「达成」",
          va.render())
    check(va.kind == "debt",
          "A：可证超出 → **debt**（2026-11-10 前是 unknown）", va.render())
    check("text" in va.render() and "pattern" in va.render(),
          "A：债务文案说清**哪个尺寸量**高（不是一句「关系未知」）", va.render())
    check("两个尺寸量的关系未知" not in va.render(),
          "A：可证超出时 MUST NOT 再说「关系未知」", va.render())

    # ---- 复现 B（并查集）：κ = n + xs²，target = n×xs
    # 逐项看：κ 的 xs² 在 xs 上 2 > 1、在 n 上 0 < 1 —— **一维大、一维小**
    # （xs 大时 κ 大、n 大时 target 大），所以 (B) 不可比，保持 unknown。
    vb2 = compare(Cost.add(Cost.size("n"), Cost.mul(xs, xs)),
                  Cost.mul(Cost.size("n"), xs))
    check(vb2.kind != "ok", "B：κ=n+|xs|² vs target=O(n×|xs|) 不再「达成」",
          vb2.render())
    check(vb2.kind == "unknown", "B：一维大、一维小 → 仍 unknown（不许假债务）",
          vb2.note)
    check("不可比" in vb2.note and "谁增长快不确定" in vb2.note,
          "B：unknown 文案点明「不可比」+「谁增长快不确定」", vb2.note)

    # ---- (B) 的**最小反例**（任务口径）：κ = xs×ys，target = xs² + ys
    # ys ≫ xs 时 κ 大；ys ≪ xs 时 target 大 —— 谁大取决于两个尺寸量的关系。
    # 为什么不是 debt：e=(1,1) 在 xs 上 1 < 2，连 `e ≥ f` 都不成立。
    vb3 = compare(Cost.mul(xs, ys), Cost.add(Cost.mul(xs, xs), ys))
    check(vb3.kind == "unknown",
          "B：κ=xs×ys vs target=xs²+ys → unknown（关系未知，不是可证超出）",
          vb3.render())

    # ---- (A) 的正例：κ = xs×ys，target = xs + ys
    # 每一项都缺一维 ⇒ **没有任何关系假设能救**，判债务。
    va2 = compare(Cost.mul(xs, ys), Cost.add(xs, ys))
    check(va2.kind == "debt", "A：κ=xs×ys vs target=xs+ys → debt", va2.render())

    # ---- (A) 里"某一维明确高几次"的那种：κ = xs²·ys²，target = xs²·ys
    # 文案 MUST 说出「在 ys 上的次数 2 高于 target 的最高次数 1」。
    va3 = compare(Cost.mul(Cost.mul(xs, xs), Cost.mul(ys, ys)),
                  Cost.mul(Cost.mul(xs, xs), ys))
    check(va3.kind == "debt", "A：κ=xs²×ys² vs target=xs²×ys → debt", va3.render())
    check("在 `ys` 上的次数 2 高于 target 的最高次数 1" in va3.render(),
          "A：债务文案写明**哪个尺寸量高了几次**", va3.render())

    # ---- 支配成立时仍判达成：κ = a×b×63 + a + b，target = a×b
    a, b = n("a"), n("b")
    lcs = Cost.add(Cost.mul(Cost.num(63), Cost.mul(a, b)), Cost.add(a, b))
    vc = compare(lcs, Cost.mul(a, b))
    check(vc.kind == "ok", "支配成立：κ=a×b×63+a+b vs target=a×b → 达成",
          vc.render())

    # ---- 乘积 target 对二次 κ：真的被支配（这是对的，不是假达成）
    vd = compare(kmp, Cost.mul(t, p))
    check(vd.kind == "ok", "κ=text×pattern+… vs target=text×pattern → 达成",
          vd.render())

    # ---- 参考比值 MUST 按**同规模**算：ratio(n·m / (n+m)) = n/2 → 斜率 ≈ 1
    rs = cc._ratios(Cost.mul(xs, ys), Cost.add(xs, ys), ["xs", "ys"])
    check(len(rs) == len(cc.LADDER), "两尺寸都取同一个 n 时求值成功", str(len(rs)))
    check(abs(rs[-1] - cc.LADDER[-1] / 2.0) < 1.0,
          "同规模比值 = n/2（证明第二个尺寸没被当成 1）", str(rs[-1]))
    check(cc._slope(rs) > 0.5,
          "同规模下二次/线性比值随 n 增长（旧实现恒定 → 判达成）",
          str(cc._slope(rs)))

    # ---- 展不开的形状（log）→ unknown，不猜
    vlog = compare(Cost.mul(xs, Cost.log2(ys)), Cost.mul(xs, ys))
    check(vlog.kind == "unknown",
          "含 log 的多尺寸 → unknown（家族外，不猜）", vlog.render())
    check("不是本判据能展开" in vlog.note,
          "展不开的 unknown 说的是「展不开」，不是「不可比」", vlog.note)

    # ---- **分段的护栏**：`piece` 的某一支只在**小区间**生效，不是 κ 的全局下界。
    # κ = piece{1..10: xs×ys; 越界: xs} 对 target = xs + ys：xs 一大走 fallback
    # κ = xs = O(target)，**达标**；拿 1..10 那一支的 xs×ys 判债务就是假债务。
    # 所以 `_multi_exceed` 用 `allow_piece=False`（判达成用并集是上界，
    # 判超出用并集不是下界——两条方向 sound 性不同）。
    vp = compare(Cost.piece("xs", [(1, 10, Cost.mul(xs, ys))], xs),
                 Cost.add(xs, ys))
    check(vp.kind != "debt",
          "分段 κ 的高次支只在小区间生效 → MUST NOT 判债务", vp.render())


# ---------------------------------------------------------------- 分数次幂

def test_fractional_orders():
    """**有理次数**（2026-11-09）：`√n`、`n^(2/3)`、`n^(3/2)` 能写、能比。

    旧实现只认整数次数，target 里写 `O(sqrt(n))` 会在解析层"认不出"——
    契约整个不生效（`cost` 说"target 写了但解析失败"）。这里逐条钉住：
    归约出的次数是 `Fraction`，比较仍走同一个字典序。
    """
    print("[5] 有理次幂：归约 / 化简 / 逐对比较")
    xs = n()

    # ---- 归约
    check(_profile(Cost.sqrt(xs), "xs") == (Fraction(1, 2), 0, 0),
          "sqrt(n) → (1/2, 0, 0)", str(_profile(Cost.sqrt(xs), "xs")))
    check(_profile(Cost.pow(xs, Fraction(2, 3)), "xs") == (Fraction(2, 3), 0, 0),
          "n^(2/3) → (2/3, 0, 0)", str(_profile(Cost.pow(xs, Fraction(2, 3)), "xs")))
    check(_profile(Cost.pow(xs, Fraction(3, 2)), "xs") == (Fraction(3, 2), 0, 0),
          "n^(3/2) → (3/2, 0, 0)", str(_profile(Cost.pow(xs, Fraction(3, 2)), "xs")))

    # ---- 化简
    z = Cost.pow(xs, 0)
    check(z.kind == "num" and z.value == 1, "n^0 化简成 num(1)", z.render())
    sq = Cost.sqrt(xs)
    check(Cost.mul(sq, sq).render() == "n(xs)", "sqrt(n) × sqrt(n) = n",
          Cost.mul(sq, sq).render())
    check(Cost.mul(sq, xs).render() == "n(xs)^(3/2)",
          "sqrt(n) × n = n^(3/2)", Cost.mul(sq, xs).render())
    check(Cost.mul(xs, sq).render() == "n(xs)^(3/2)",
          "n × sqrt(n) = n^(3/2)", Cost.mul(xs, sq).render())
    check(Cost.mul(Cost.pow(xs, Fraction(2, 3)),
                   Cost.pow(xs, Fraction(1, 3))).render() == "n(xs)",
          "n^(2/3) × n^(1/3) = n")

    # ---- 逐对：1 ≺ log n ≺ n^(1/3) ≺ sqrt(n) ≺ n ≺ n log n ≺ n^(3/2) ≺ n² ≺ n³
    ladder = [
        ("1", Cost.num(1)),
        ("log n", Cost.log2(xs)),
        ("n^(1/3)", Cost.pow(xs, Fraction(1, 3))),
        ("sqrt(n)", Cost.sqrt(xs)),
        ("n", xs),
        ("n log n", Cost.mul(xs, Cost.log2(xs))),
        ("n^(3/2)", Cost.pow(xs, Fraction(3, 2))),
        ("n²", Cost.mul(xs, xs)),
        ("n³", Cost.mul(Cost.mul(xs, xs), xs)),
    ]
    for (lo_name, lo), (hi_name, hi) in zip(ladder, ladder[1:]):
        check(_profile(lo, "xs") < _profile(hi, "xs"),
              f"profile：{lo_name} ≺ {hi_name}",
              f"{_profile(lo, 'xs')} vs {_profile(hi, 'xs')}")
        v = compare(lo, hi)
        check(v.kind == "ok", f"κ={lo_name} vs target O({hi_name}) → ok", v.render())
        v2 = compare(hi, lo)
        check(v2.kind == "debt",
              f"κ={hi_name} vs target O({lo_name}) → debt", v2.render())
    # 两侧都含尺寸量时，结论必须来自**结构判据**（不是数值斜率）
    v_struct = compare(Cost.sqrt(xs), xs)
    check(v_struct.kind == "ok" and "结构判据" in (v_struct.note or ""),
          "sqrt(n) vs O(n) 由结构判据判定", v_struct.note)

    # ---- 边界：n^(1/2) log n 比 n^(1/2) 大（同次数，比 log 因子）
    sl = Cost.mul(Cost.sqrt(xs), Cost.log2(xs))
    check(_profile(sl, "xs") == (Fraction(1, 2), 1, 0),
          "sqrt(n) log n → (1/2, 1, 0)", str(_profile(sl, "xs")))
    check(compare(Cost.sqrt(xs), sl).kind == "ok", "sqrt(n) ≺ sqrt(n) log n（ok）")
    check(compare(sl, Cost.sqrt(xs)).kind == "debt", "sqrt(n) log n ≻ sqrt(n)（debt）")

    # ---- 家族外一律 None（不猜）；⊤ 吸收不许回归
    check(_profile(Cost.sqrt(Cost.log2(xs)), "xs") is None,
          "sqrt(log n) 家族外 → None（log^(1/2) 不在 (d,l) 里）")
    check(_profile(Cost.sqrt(Cost.mul(xs, Cost.log2(xs))), "xs") is None,
          "sqrt(n log n) 家族外 → None")
    check(_profile(Cost.sqrt(Cost("ext", "read_file")), "xs") is None,
          "sqrt(ext) → None")
    check(_profile(Cost.pow(Cost.size("ys"), Fraction(1, 2)), "xs") is None,
          "sqrt(别的尺寸量) → None")
    check(Cost.sqrt(Cost.top()).is_top(), "sqrt(⊤) = ⊤")
    check(Cost.pow(Cost.top(), Fraction(1, 2)).is_top(), "⊤^(1/2) = ⊤")
    junk = Cost.max(Cost.top(), Cost.sqrt(xs))
    check(junk.is_top(), "max(⊤, sqrt(n)) = ⊤（历史 bug 不许回归）", junk.render())
    check(Cost.add(Cost.top(), Cost.sqrt(xs)).is_top(), "⊤ + sqrt(n) = ⊤")
    check(compare(junk, Cost.sqrt(xs)).kind == "unknown", "max(⊤, sqrt(n)) → unknown")


def test_log_divisor_orders():
    """**负 log 因子**（2026-11-09）：`n / log n` 能写、能比、能判。

    没这一步时 `target { time: { max: O(n / log n) } }` 是"能 parse、语义层不认"：
    `_target_cost` 的 binary 分支只认 `+ - *`，`/` 直接 `return None`，
    于是 `_find_slot` 登记 `target_errors`、`cost` 说"target 写了但解析失败"——
    契约**整个不生效**（与 `sqrt` 修复前是同一种病）。

    归约家族仍是 `n^d · log^l n`，只是 **`l` 现在可以为负**：
    `n / log n → (1, -1)`。比较照旧字典序，同次数下 `-2 < -1 < 0 < 1`。
    """
    print("[6] 负 log 因子：n / log n 的归约与逐对比较")
    xs = n()
    lg = Cost.log2(xs)
    d1 = Cost.div(xs, lg)                       # n / log n
    d2 = Cost.div(xs, Cost.mul(lg, lg))         # n / log² n

    # ---- 归约：每一条都印出实际值
    check(_profile(d1, "xs") == (Fraction(1), -1, 0),
          "n/log n → (1, -1, 0)", str(_profile(d1, "xs")))
    check(_profile(d2, "xs") == (Fraction(1), -2, 0),
          "n/log²n → (1, -2, 0)", str(_profile(d2, "xs")))
    check(_profile(Cost.div(xs, Cost.num(2)), "xs") == (Fraction(1), 0, 0),
          "n/2 → (1, 0, 0)（常数分母不改量级）",
          str(_profile(Cost.div(xs, Cost.num(2)), "xs")))
    check(_profile(Cost.div(Cost.num(1), lg), "xs") == (Fraction(0), -1, 0),
          "1/log n → (0, -1, 0)", str(_profile(Cost.div(Cost.num(1), lg), "xs")))
    check(_profile(Cost.div(Cost.sqrt(xs), lg), "xs") == (Fraction(1, 2), -1, 0),
          "√n/log n → (1/2, -1, 0)",
          str(_profile(Cost.div(Cost.sqrt(xs), lg), "xs")))

    # ---- n/log²n ≺ n/log n ≺ n ≺ n log n（字典序自动）
    ladder = [
        ("n/log²n", d2),
        ("n/log n", d1),
        ("n", xs),
        ("n log n", Cost.mul(xs, lg)),
    ]
    for (lo_name, lo), (hi_name, hi) in zip(ladder, ladder[1:]):
        check(_profile(lo, "xs") < _profile(hi, "xs"),
              f"profile：{lo_name} ≺ {hi_name}",
              f"{_profile(lo, 'xs')} vs {_profile(hi, 'xs')}")
        v_ok = compare(lo, hi)
        check(v_ok.kind == "ok", f"κ={lo_name} vs target O({hi_name}) → ok",
              v_ok.render())
        v_debt = compare(hi, lo)
        check(v_debt.kind == "debt", f"κ={hi_name} vs target O({lo_name}) → debt",
              v_debt.render())

    # ---- 结论来自**结构判据**，不是采样斜率
    vs_ = compare(xs, d1)
    check(vs_.kind == "debt" and "结构判据" in (vs_.note or ""),
          "n vs O(n/log n) 由结构判据判债务", vs_.note)

    # ---- √n·log n  vs  n/log n：**谁大**？
    #   n/log n  = n^1 · (log n)^-1        → (1, -1)
    #   √n·log n = n^(1/2) · (log n)^1     → (1/2, 1)
    #   次数 1 > 1/2 ⇒ n/log n 大（正 log 因子救不了半个次数）。
    sl = Cost.mul(Cost.sqrt(xs), lg)
    check(_profile(sl, "xs") == (Fraction(1, 2), 1, 0),
          "√n·log n → (1/2, 1, 0)", str(_profile(sl, "xs")))
    check(_profile(d1, "xs") > _profile(sl, "xs"),
          "profile：√n·log n ≺ n/log n",
          f"{_profile(sl, 'xs')} vs {_profile(d1, 'xs')}")
    check(compare(sl, d1).kind == "ok", "κ=√n·log n vs target O(n/log n) → ok")
    check(compare(d1, sl).kind == "debt", "κ=n/log n vs target O(√n·log n) → debt")

    # ---- 边界①：分母是**另一个尺寸量** → 一律 ⊤（不猜两变量关系）
    m = n("ys")
    nm = Cost.div(xs, m)
    check(_profile(nm, "xs") is None,
          "n/m（m 是另一个尺寸量）→ None（退 ⊤）", str(_profile(nm, "xs")))
    check(_profile(Cost.div(m, xs), "xs") is None, "m/n → None")
    check(compare(Cost.num(9), nm).kind == "unknown",
          "常数 κ vs target O(n/m) → unknown（target 会掉到 O(1) 以下，不给假 ✓）",
          compare(Cost.num(9), nm).note)
    check(compare(xs, nm).kind == "unknown",
          "κ=n vs target O(n/m)：尺寸量不一致 → unknown",
          compare(xs, nm).note)

    # ---- 边界②：分母为 0 → 求值 inf，不崩；判阶 None
    z = Cost.div(xs, Cost.num(0))
    check(evaluate(z, {"xs": 8}) == float("inf"),
          "div(n, 0) 求值 → inf（不崩）", str(evaluate(z, {"xs": 8})))
    check(_profile(z, "xs") is None, "div(n, 0) → None（没有量级）",
          str(_profile(z, "xs")))
    check(Cost.div(xs, Cost.num(0)).kind == "div",
          "除零**不**偷偷折成 ⊤：节点留着，看得到是除零")

    # ---- 边界③：⊤ 吸收（历史 bug 不许回归）
    check(Cost.div(Cost.top(), xs).is_top(), "div(⊤, n) = ⊤")
    check(Cost.div(xs, Cost.top()).is_top(), "div(n, ⊤) = ⊤")
    check(Cost.max(Cost.top(), d1).is_top(), "max(⊤, n/log n) = ⊤")
    check(compare(Cost.max(Cost.top(), d1), d1).kind == "unknown",
          "max(⊤, n/log n) → unknown")
    check(_profile(Cost.div(xs, Cost("ext", "read_file")), "xs") is None,
          "分母是 ext → None")
    check(_profile(Cost.div(Cost("ext", "read_file"), lg), "xs") is None,
          "分子是 ext → None")

    # ---- 分母是 1/log n（负 log 分母）→ 家族外，不猜
    check(_profile(Cost.div(xs, Cost.div(Cost.num(1), lg)), "xs") is None,
          "n / (1/log n) → None（分母是负 log 因子，不在规则里）")

    # ---- 化简（与 profile 独立：表达式层）
    check(Cost.div(xs, Cost.num(1)).render() == "n(xs)", "div(a, 1) = a")
    check(Cost.div(Cost.num(6), Cost.num(3)).render() == "2", "div(6, 3) = 2")
    check(Cost.div(Cost.num(3), Cost.num(2)).const_value() == 1.5,
          "div(3, 2) = 1.5（**不许被 int() 截断成 1**）",
          str(Cost.div(Cost.num(3), Cost.num(2)).const_value()))
    check(Cost.div(xs, xs).render() == "1", "n / n = 1")
    check(Cost.mul(d1, lg).render() == "n(xs)",
          "(n / log n) × log n = n", Cost.mul(d1, lg).render())
    check(Cost.div(Cost.mul(xs, lg), lg).render() == "n(xs)",
          "(n × log n) / log n = n", Cost.div(Cost.mul(xs, lg), lg).render())

    # ---- 打印形式
    check(Cost.div(xs, lg).render() == "n(xs) / log2(n(xs))",
          "render：n(xs) / log2(n(xs))（log 节点一直印成 log2）",
          Cost.div(xs, lg).render())
    check(Cost.div(xs, Cost.mul(lg, lg)).render() == "n(xs) / (log2(n(xs))×log2(n(xs)))",
          "分母是乘积时加括号", Cost.div(xs, Cost.mul(lg, lg)).render())

    # ---- 除以 log 的**幂**：分母是 log² 也认（`k = 2`）
    check(_profile(Cost.div(xs, Cost.mul(lg, lg)), "xs") == (Fraction(1), -2, 0),
          "分母 log² → log 因子减 2", str(_profile(d2, "xs")))


def test_loglog_orders():
    """**loglog**（2026-11-09）：`O(n log log n)` 能写、能判（埃氏筛那一档）。

    复现的旧洞：`target { time: { max: O(n * log(log(n))) } }` **能 parse**
    （`_target_cost` 的 `log` 分支本来就会递归），但 `_profile` 把**任何**
    log 都记成 `(0, 1)`——那是 `log(n log n) = O(log n)` 那条规则的副作用，
    它没有区分"log 的里面还有没有 log"。于是 `n log n` 对
    `O(n log log n)` 判**达成**：target 写的是 `n loglog n`，判阶却按
    `n log n` 读，承诺被**悄悄放宽一档**。

    表示上**不新增节点**：`loglog n` 就是 `Cost.loglog(n)` =
    `log2(log2(n))`——`render` 印成 `log2(log2(n(xs)))`，`evaluate` 递归
    进 `args` 就能算，判阶把这一层嵌套认成三元组的第三位。
    """
    print("[7] loglog：n log log n 的归约与逐对比较")
    xs = n()
    lg = Cost.log2(xs)
    ll = Cost.loglog(xs)                  # log2(log2(n))

    # ---- 表示：两层 log2，打印 / 求值都现成
    check(ll.kind == "log2" and ll.args[0].kind == "log2",
          "loglog 是两层 log2 节点（不新增 kind）", str(ll))
    check(ll.render() == "log2(log2(n(xs)))",
          "render：log2(log2(n(xs)))", ll.render())
    # 2^16 = 65536 → log2 = 16 → log2 = 4
    check(abs(evaluate(ll, {"xs": 65536}) - 4.0) < 1e-9,
          "evaluate：n=2^16 时 loglog n = 4", str(evaluate(ll, {"xs": 65536})))

    # ---- 归约：三元组（次数, log, loglog）
    check(_profile(Cost.num(1), "xs") == (0, 0, 0), "1 → (0,0,0)")
    check(_profile(ll, "xs") == (0, 0, 1),
          "loglog n → (0,0,1)", str(_profile(ll, "xs")))
    check(_profile(lg, "xs") == (0, 1, 0),
          "log n → (0,1,0)", str(_profile(lg, "xs")))
    check(_profile(xs, "xs") == (1, 0, 0),
          "n → (1,0,0)", str(_profile(xs, "xs")))
    check(_profile(Cost.mul(lg, ll), "xs") == (0, 1, 1),
          "log n · loglog n → (0,1,1)", str(_profile(Cost.mul(lg, ll), "xs")))
    check(_profile(Cost.mul(xs, ll), "xs") == (1, 0, 1),
          "n loglog n → (1,0,1)", str(_profile(Cost.mul(xs, ll), "xs")))
    check(_profile(Cost.mul(xs, lg), "xs") == (1, 1, 0),
          "n log n → (1,1,0)", str(_profile(Cost.mul(xs, lg), "xs")))
    check(_profile(Cost.mul(xs, xs), "xs") == (2, 0, 0),
          "n² → (2,0,0)", str(_profile(Cost.mul(xs, xs), "xs")))

    # ---- mul 分量相加 / add、max 取支配项（都走同一个字典序）
    check(_profile(Cost.mul(ll, ll), "xs") == (0, 0, 2),
          "loglog n · loglog n → (0,0,2)", str(_profile(Cost.mul(ll, ll), "xs")))
    check(_profile(Cost.mul(ll, ll), "xs") < _profile(lg, "xs"),
          "loglog² n ≺ log n（第三位再大也压不过第二位）",
          f"{_profile(Cost.mul(ll, ll), 'xs')} vs {_profile(lg, 'xs')}")
    check(_profile(Cost.add(ll, lg), "xs") == (0, 1, 0),
          "add(loglog, log) → 支配项 log", str(_profile(Cost.add(ll, lg), "xs")))
    check(_profile(Cost.max(Cost.mul(xs, ll), xs), "xs") == (1, 0, 1),
          "max(n loglog n, n) → n loglog n",
          str(_profile(Cost.max(Cost.mul(xs, ll), xs), "xs")))

    # ---- 总梯子：1 ≺ loglog n ≺ log n ≺ log n·loglog n ≺ √n ≺ n
    #            ≺ n loglog n ≺ n log n ≺ n²
    ladder = [
        ("1", Cost.num(1)),
        ("loglog n", ll),
        ("log n", lg),
        ("log n·loglog n", Cost.mul(lg, ll)),
        ("√n", Cost.sqrt(xs)),
        ("n", xs),
        ("n loglog n", Cost.mul(xs, ll)),
        ("n log n", Cost.mul(xs, lg)),
        ("n²", Cost.mul(xs, xs)),
    ]
    for (lo_name, lo), (hi_name, hi) in zip(ladder, ladder[1:]):
        plo, phi = _profile(lo, "xs"), _profile(hi, "xs")
        check(plo < phi, f"profile：{lo_name} ≺ {hi_name}",
              f"{plo} vs {phi}")
        v_ok = compare(lo, hi)
        check(v_ok.kind == "ok", f"κ={lo_name} vs target O({hi_name}) → ok",
              v_ok.render())
        v_debt = compare(hi, lo)
        check(v_debt.kind == "debt", f"κ={hi_name} vs target O({lo_name}) → debt",
              v_debt.render())
        # **逐对印出实际值**：这条梯子是这一步的核心口径，别只印"通过"。
        print(f"    {lo_name:16} {str(plo):20} ≺ {hi_name:16} {str(phi):20}"
              f"  ok={v_ok.kind:5} / debt={v_debt.kind:5}")

    # ---- 边界①：n loglog n **小于** n log n（双向）
    nll = Cost.mul(xs, ll)
    nlogn = Cost.mul(xs, lg)
    check(_profile(nll, "xs") < _profile(nlogn, "xs"),
          "profile：n loglog n ≺ n log n",
          f"{_profile(nll, 'xs')} vs {_profile(nlogn, 'xs')}")
    check(compare(nll, nlogn).kind == "ok",
          "κ=n loglog n vs target O(n log n) → ok")
    v = compare(nlogn, nll)
    check(v.kind == "debt", "κ=n log n vs target O(n loglog n) → debt", v.render())
    check("结构判据" in (v.note or ""), "这一条由结构判据判（不是数值斜率）", v.note)

    # ---- 边界②：n loglog n **大于** √n（次数 1 > 1/2，不只是靠 log 因子）
    sq = Cost.sqrt(xs)
    check(_profile(sq, "xs") < _profile(nll, "xs"),
          "profile：√n ≺ n loglog n",
          f"{_profile(sq, 'xs')} vs {_profile(nll, 'xs')}")
    check(compare(sq, nll).kind == "ok", "κ=√n vs target O(n loglog n) → ok")
    check(compare(nll, sq).kind == "debt", "κ=n loglog n vs target O(√n) → debt")

    # ---- `log(n log n)`：化简成 log n（**写断言**）
    #   log(n log n) = log n + loglog n = Θ(log n)（低阶项不改变阶）。
    lnn = Cost.log2(Cost.mul(xs, lg))
    check(_profile(lnn, "xs") == (0, 1, 0),
          "log(n log n) → (0,1,0)（= log n + loglog n，仍是 log n）",
          str(_profile(lnn, "xs")))
    check(_profile(Cost.log2(Cost.mul(lg, ll)), "xs") == (0, 0, 1),
          "log(log n · loglog n) → (0,0,1)（= loglog n + 低阶 logloglog）",
          str(_profile(Cost.log2(Cost.mul(lg, ll)), "xs")))
    check(_profile(Cost.log2(Cost.mul(xs, ll)), "xs") == (0, 1, 0),
          "log(n loglog n) → (0,1,0)（次数 > 0，log 回来仍是 log n）",
          str(_profile(Cost.log2(Cost.mul(xs, ll)), "xs")))

    # ---- 三层 log：**家族到此为止** → None（不折算到 loglog）
    lll = Cost.log2(ll)                   # log(loglog n)
    check(_profile(lll, "xs") is None,
          "log(loglog n) → None（三层 log 超出家族，不猜）",
          str(_profile(lll, "xs")))
    check(_order_le(lll, lg, "xs") is None,
          "_order_le 对三层 log 也给 None（结构判不了）")
    # compare 也**不猜**：三层 log 是家族外 → unknown（旧行为是退回数值拟合，
    # 斜率正负决定 ok/debt，同一对式子换个采样区间就翻）。
    v3 = compare(ll, lll)
    check(v3.kind == "unknown",
          "三层 log 作为 target → unknown（不给数值拟合结论）", v3.render())
    check("超出可判定的族" in v3.note and "三层 log" in v3.note,
          "note 指出「超出可判定的族」且点名三层 log", v3.note)
    check("不用数值拟合" in v3.note,
          "note 明确说明不用数值拟合给结论", v3.note)
    # 家族外同样不猜：sqrt(loglog n) / loglog² 取根都超出三元组
    check(_profile(Cost.sqrt(ll), "xs") is None,
          "sqrt(loglog n) → None（家族外）", str(_profile(Cost.sqrt(ll), "xs")))
    check(_profile(Cost.pow(ll, 2), "xs") is None,
          "loglog² 取幂 → None（loglog 因子的非整数次幂不在家族里）")

    # ---- 除法：loglog 因子也可以为负（`loglog n > 1` 之后）
    d = Cost.div(xs, ll)                  # n / loglog n（比 n **小**）
    check(_profile(d, "xs") == (Fraction(1), 0, -1),
          "n/loglog n → (1,0,-1)", str(_profile(d, "xs")))
    check(_profile(d, "xs") < _profile(xs, "xs"),
          "profile：n/loglog n ≺ n（第三位 -1 < 0）",
          f"{_profile(d, 'xs')} vs {_profile(xs, 'xs')}")
    check(compare(d, xs).kind == "ok", "κ=n/loglog n vs target O(n) → ok")
    vd = compare(xs, d)
    check(vd.kind == "debt", "κ=n vs target O(n/loglog n) → debt", vd.render())
    mix = Cost.div(xs, Cost.mul(lg, ll))  # n / (log n · loglog n)
    check(_profile(mix, "xs") == (Fraction(1), -1, -1),
          "n/(log n·loglog n) → (1,-1,-1)", str(_profile(mix, "xs")))
    check(_profile(mix, "xs") < _profile(d, "xs"),
          "profile：n/(log n·loglog n) ≺ n/loglog n")

    # ---- ⊤ 吸收 / 判不了不许回归
    check(Cost.max(Cost.top(), ll).is_top(), "max(⊤, loglog n) = ⊤")
    check(compare(Cost.max(Cost.top(), ll), ll).kind == "unknown",
          "max(⊤, loglog n) → unknown")
    check(Cost.loglog(Cost.top()).is_top(),
          "loglog(⊤) = ⊤（上界未知**不许**被数值兜底当成常数）",
          Cost.loglog(Cost.top()).render())
    check(compare(Cost.loglog(Cost.top()), Cost.num(1)).kind == "unknown",
          "κ=loglog(⊤) vs O(1) → unknown（不假达成）")
    check(_profile(Cost.loglog(n("ys")), "xs") is None,
          "loglog(别的尺寸量) → None", str(_profile(Cost.loglog(n("ys")), "xs")))


def test_range_diff_orders():
    """**尺寸差 `hi - lo`**（2026-11-10）：区间长度是尺寸量。

    ## 复现的旧洞

    递归分治最自然的上界就是「区间长度的平方 / 带 log」：

        qsort(lo, hi)   →  O((hi - lo) * (hi - lo))
        mergesort       →  O((hi - lo) * log(hi - lo))
        二分            →  O(log(hi - lo))

    旧 `_target_cost` 一个都写不出来：`-` 只回 `max(a, b)`（**把 `hi - lo`
    当成 `hi`**——契约悄悄放宽），带括号时更直接认不出（没有 `paren` 分支），
    整条 target 报 `E-Target-BadForm`。

    ## 现在的口径（两条，没有第三条）

      · 能证明 `lo <= hi`（spec 的 `requires`）→ 它是**尺寸量**，符号名
        `hi - lo`（`Cost("sym", …)`，**不是** `n(hi - lo)`：差值不是容器）；
        κ 侧的 `while (j < hi)`（`j` 从 `lo` 起只增）走**同一个名字**；
      · 证明不了 → **退 ⊤ 并说清原因**，不当常数、也不当正数猜。
    """
    print("[8] 尺寸差 hi - lo：区间长度作为尺寸量")
    from parser import parse_source
    from cost_interp import CostInterp
    from cost_check import facts_of_spec

    CH = """
u64 scan(vector<u64>& xs, u64 lo, u64 hi) {
    u64 j = lo;
    u64 s = 0;
    while (j < hi) { s = s + xs[j]; j = j + 1; }
    return s;
}
u64 pair_scan(vector<u64>& xs, u64 lo, u64 hi) {
    u64 j = lo;
    u64 t = 0;
    while (j < hi) {
        u64 k = lo;
        while (k < hi) { t = t + xs[k]; k = k + 1; }
        j = j + 1;
    }
    return t;
}
"""

    def spec_src(fn, target, req=""):
        return (f"spec {fn}(vector<u64>& xs, u64 lo, u64 hi) -> u64 {{ "
                f"effects {{ }}; {req} target {{ time: {{ max: {target} }}, "
                f"allocation: {{ max: 0 }}, }}; }}\n")

    def build(tu_src):
        tu = parse_source(tu_src, "a.tu").module
        return CostInterp(parse_source(CH, "a.eln").module, tu)

    REQ = "requires lo <= hi; "

    # ---- ① target 侧：`(hi - lo)` 认得出，且**是一个尺寸量**
    TU_BOTH = (spec_src("scan", "O((hi - lo) * (hi - lo))", REQ)
               + spec_src("pair_scan", "O((hi - lo) * log(hi - lo))", REQ))
    ci = build(TU_BOTH)
    tg = ci.target_cost("scan")
    check(tg is not None, "O((hi - lo) * (hi - lo)) 能认", str(ci.target_errors))
    check(tg is not None and tg.render() == "(hi - lo)×(hi - lo)",
          "render：差值是一个整体，印成 (hi - lo)×(hi - lo)",
          "" if tg is None else tg.render())
    check(tg is not None and tg.variables() == ["hi - lo"],
          "尺寸量集合里是 `hi - lo`（不是 `hi` / `lo` 两个）",
          "" if tg is None else str(tg.variables()))
    check(tg is not None and _profile(tg, "hi - lo") == (Fraction(2), 0, 0),
          "profile：O((hi - lo)²) → (2,0,0)",
          "" if tg is None else str(_profile(tg, "hi - lo")))
    # 带 log 的那一档：`(hi-lo)·log(hi-lo)` → (1,1,0)
    tg2 = ci.target_cost("pair_scan")
    check(tg2 is not None and _profile(tg2, "hi - lo") == (Fraction(1), 1, 0),
          "profile：O((hi-lo)·log(hi-lo)) → (1,1,0)",
          "" if tg2 is None else str(_profile(tg2, "hi - lo")))

    # ---- ② κ 侧：区间循环的轮数也用**同一个符号**
    k = ci.cost_of("scan")
    check("hi - lo" in k.variables(),
          "κ 的循环轮数是 `hi - lo`（与 target 同一个尺寸量）", k.render())
    check(compare(k, tg).kind == "ok",
          "κ=O(hi-lo) vs target O((hi-lo)²) → ok", compare(k, tg).render())
    kp = ci.cost_of("pair_scan")
    v_pair = compare(kp, tg2)              # 平方 vs n log n
    check(v_pair.kind == "debt",
          "κ=O((hi-lo)²) vs target O((hi-lo)·log(hi-lo)) → **超标**",
          v_pair.render())
    check(compare(kp, tg).kind == "ok",
          "κ=O((hi-lo)²) vs target O((hi-lo)²) → ok（同一个符号才对得上）")

    # ---- ③ 证明不了 `lo <= hi` → **退 ⊤**（不是当常数、也不是当正数）
    ci3 = build(spec_src("scan", "O((hi - lo) * n)"))
    check(ci3.target_cost("scan") is None, "没有 requires → target 认不出（退 ⊤）")
    errs = ci3.target_errors
    check(len(errs) == 1 and "非负的尺寸量" in errs[0]["message"],
          "诊断说清原因：差值可能为负、不是尺寸量",
          str(errs))
    check(bool(errs) and "requires lo <= hi" in errs[0]["message"],
          "诊断给出修法（写 requires lo <= hi）",
          "" if not errs else errs[0]["message"])

    # ---- ④ 方向反了：`O((lo - hi) * n)` 也一样拒（**不许当尺寸量**）
    ci4 = build(spec_src("scan", "O((lo - hi) * n)", REQ))
    check(ci4.target_cost("scan") is None,
          "方向反了的差值 `lo - hi` 被拒（requires 说的是 lo<=hi）")
    e4 = ci4.target_errors
    check(bool(e4) and "hi ≤ lo" in e4[0]["message"],
          "诊断指出要证明的是 `hi <= lo`（方向），当前证不了",
          "" if not e4 else e4[0]["message"])

    # ---- ⑤ `requires` 的**四个方向**都归一成事实（含 `>=` 与整数下界）
    sp = parse_source(spec_src("scan", "O(n)", "requires hi >= lo; "), "a.tu").module
    f = facts_of_spec(next(c for c in sp.children if c.kind == "spec"))
    check(f.get("lo") == {"hi"}, "requires hi >= lo → 事实 lo≤hi", str(f))
    sp2 = parse_source(spec_src("scan", "O(n)", "requires 1 <= xs.length(); "),
                       "a.tu").module
    f2 = facts_of_spec(next(c for c in sp2.children if c.kind == "spec"))
    check(f2.get("1") == {"xs"}, "requires 1 <= xs.length() → 事实 1≤xs", str(f2))

    # ---- ⑥ 化简：`x - 0 = x`（**不许**造出 `n(x - 0)`——golden 门抓过）
    ci5 = build(spec_src("scan", "O(xs.length() - 0)"))
    tg5 = ci5.target_cost("scan")
    check(tg5 is not None and tg5.render() == "n(xs)",
          "O(xs.length() - 0) 化简成 n(xs)（不造 `x - 0` 符号）",
          "" if tg5 is None else tg5.render())

    # ---- ⑦ 已支持的形状**实测**（回归锚点）：加法、常数倍
    ci6 = build("spec scan(vector<u64>& xs, vector<u64>& ys) -> u64 { "
                "effects { }; target { time: { max: O(xs.length() + ys.length()) }, "
                "allocation: { max: 0 }, }; }\n")
    check(ci6.target_cost("scan") is not None,
          "O(a.length() + b.length()) 仍能认（加法）",
          str(ci6.target_errors))
    ci7 = build("spec scan(vector<u64>& xs) -> u64 { effects { }; "
                "target { time: { max: O(3 * n) }, allocation: { max: 0 }, }; }\n")
    tg7 = ci7.target_cost("scan")
    check(tg7 is not None and tg7.render() == "3×n(xs)",
          "O(3 * n) 仍能认（常数倍）", "" if tg7 is None else tg7.render())

    # ---- ⑧ `for (i in lo..hi)` 已删除；三段式等价写法必须给**上界** ----
    #
    # 旧写法 `..` 区间整条删掉（2026-11-10 夜，与 C++ range-for 重合）。
    # 这里守两件事：① 旧写法报 `E-For-InRemoved`；② 三段式 `i = lo; i < hi`
    # 的 κ 是**保守上界**（轮数记成 `hi`，不会低估），所以用它配
    # `O(hi - lo)` 的 target 只会判"债务/无法判定"，**绝不会判达成**。
    CH_FOR_OLD = ("u64 for_range(vector<u64>& xs, u64 lo, u64 hi) {\n"
                  "    u64 s = 0;\n"
                  "    for (i in lo..hi) { s = s + xs[i]; }\n"
                  "    return s;\n}\n")
    r_old = parse_source(CH_FOR_OLD, "old.eln")
    check("E-For-InRemoved" in [d.code for d in r_old.diagnostics],
          "`for (i in lo..hi)` 报 E-For-InRemoved",
          [d.code for d in r_old.diagnostics])

    CH_FOR = ("u64 for_range(vector<u64>& xs, u64 lo, u64 hi) {\n"
              "    u64 s = 0;\n"
              "    for (u64 i = lo; i < hi; i = i + 1) { s = s + xs[i]; }\n"
              "    return s;\n}\n")
    tu_for = parse_source(spec_src("for_range", "O(hi - lo)", REQ), "a.tu").module
    ci_for = CostInterp(parse_source(CH_FOR, "a.eln").module, tu_for)
    tg_for = ci_for.target_cost("for_range")
    kf = ci_for.cost_of("for_range")
    check("hi - lo" in kf.variables(),
          "三段式 `i = lo; i < hi` 的轮数也是 `hi - lo`（与 while 同一把尺）",
          kf.render())
    check(compare(kf, tg_for).kind == "ok",
          "三段式区间循环 vs target O(hi - lo) → ok",
          compare(kf, tg_for).render())


def test_star_star_target():
    """**target 成本式里的 `**`**（2026-11-10）：与 `.eln` 同一个运算符。

    ## 复现的旧洞

    `**` 在 `.eln` 里已经是幂（`2 ** 10 = 1024`），但 `.tu` 的 target 里
    只认 `pow(x, p/q)`：`O(n ** 2)` 报 `E-Target-BadForm`，提示叫人改写成
    `pow(n, 2)` —— **同一个语言，两处对 `**` 的理解不同**。

    ## 现在的口径

    直接复用 `pow` 的构造器（`Cost.pow`），**不新造节点、不新造阶规则**：

        O(n ** 2)            == O(pow(n, 2))        → n^2
        O(n ** 0.5)          == O(sqrt(n))          → sqrt(n)
        O(n ** (2/3))        == O(pow(n, 2/3))      → n^(2/3)
        O((hi - lo) ** 2)                           → (hi - lo)^2

    **指数必须是常数**（字面量 / 常数分数）：`O(n ** k)` 的阶是"关于 k 的
    表达式"，判阶表没有值可填 ⇒ 退 ⊤ 并**说清原因**。负指数同理（成本随
    尺寸单调不减，`x ** 负数` 不是上界）。
    """
    print("[10] target 里的 `**`：复用 pow 的构造，指数须为常数")
    from parser import parse_source
    from cost_interp import CostInterp

    def build(tu_src, eln_src="pub u64 f(u64 n) { return n; }\n"):
        tu = parse_source(tu_src, "a.tu").module
        eln = parse_source(eln_src, "a.eln").module
        ci = CostInterp(eln, tu)
        return ci, ci.target_cost("f")

    def tu(body, params="u64 n", req=""):
        return (f"spec f({params}) -> u64 {{ effects {{ }}; {req}"
                f"target {{ time: {{ max: {body} }}, allocation: {{ max: 0 }}, }}; }}\n")

    # ---- ① 收：与 `pow` / `sqrt` **同一个 Cost**（render 逐字相同）
    for body, want in (("O(n ** 2)", "n^2"),
                       ("O(n ** 0.5)", "sqrt(n)"),
                       ("O(n ** (2/3))", "n^(2/3)"),
                       ("O(n ** 3)", "n^3")):
        ci, tg = build(tu(body))
        check(tg is not None and tg.render() == want,
              f"target 认 `{body}` → {want}",
              "" if tg is None else (tg.render() or "None"))

    # **不新造节点**：`n ** 0.5` 与 `sqrt(n)` 是同一个式子的两种写法
    _ci_a, _a = build(tu("O(n ** 0.5)"))
    _ci_b, _b = build(tu("O(sqrt(n))"))
    check(_a is not None and _b is not None and _a.render() == _b.render(),
          "`n ** 0.5` 与 `sqrt(n)` 落到同一个 Cost（render 一致）",
          f"{None if _a is None else _a.render()} vs {None if _b is None else _b.render()}")
    _ci_c, _c = build(tu("O(n ** (2/3))"))
    _ci_d, _d = build(tu("O(pow(n, 2/3))"))
    check(_c is not None and _d is not None and _c.render() == _d.render(),
          "`n ** (2/3)` 与 `pow(n, 2/3)` 落到同一个 Cost",
          f"{None if _c is None else _c.render()} vs {None if _d is None else _d.render()}")

    # ---- ② 容器尺寸能当底数：`O(xs.length() ** 0.5)`
    ci, tg = build(tu("O(xs.length() ** 0.5)", params="vector<u64>& xs"),
                   "pub u64 f(vector<u64>& xs) { return xs.length(); }\n")
    check(tg is not None and tg.render() == "sqrt(n(xs))",
          "O(xs.length() ** 0.5) → sqrt(n(xs))",
          "" if tg is None else tg.render())
    check(tg is not None and _profile(tg, "xs") == (Fraction(1, 2), 0, 0),
          "profile：sqrt → (1/2, 0, 0)",
          "" if tg is None else str(_profile(tg, "xs")))

    # ---- ③ 尺寸差能当底数：`O((hi - lo) ** 2)`（须能证明 lo <= hi）
    ci, tg = build(tu("O((hi - lo) ** 2)",
                      params="vector<u64>& xs, u64 lo, u64 hi",
                      req="requires lo <= hi; "))
    check(tg is not None and tg.render() == "(hi - lo)^2",
          "O((hi - lo) ** 2) → (hi - lo)^2",
          "" if tg is None else tg.render())
    check(tg is not None and tg.variables() == ["hi - lo"]
          and _profile(tg, "hi - lo") == (Fraction(2), 0, 0),
          "`**` 的底数是尺寸差时仍是同一个符号 `hi - lo`",
          "" if tg is None else f"{tg.variables()} / {_profile(tg, 'hi - lo')}")

    # ---- ④ 拒：指数是变量 / 负数 / 非常数分母 → BadForm，且**说清原因**
    cases = [
        (tu("O(n ** k)", params="u64 n, u64 k"), "不是常数", "指数是变量"),
        (tu("O(n ** -1)"), "负数", "负指数"),
        (tu("O(n ** (2 / k))", params="u64 n, u64 k"), "分母", "非常数分母"),
        (tu("O(n ** (1 / 0))"), "分母是 0", "分母为 0"),
        # 纯常数式但未折叠：`n ** 2 ** 3` 在 `.eln` 里是 `n ** 8`。
        # 诊断**不能说"不是常数"**（它确实是常数），要说清"请写折叠后的数"。
        (tu("O(n ** 2 ** 3)"), "常数式", "常数式指数未折叠"),
    ]
    for src, needle, label in cases:
        ci, tg = build(src)
        errs = ci.target_errors
        check(tg is None, f"{label} → target 认不出（退 ⊤，不猜）",
              "" if tg is None else tg.render())
        check(bool(errs) and needle in errs[0]["message"],
              f"{label} 的诊断点明「{needle}」",
              "" if not errs else errs[0]["message"])

    # ---- ⑤ 旧写法**逐字不变**（同一个 render，不是"差不多"）
    for body, want in (("O(pow(n, 2/3))", "n^(2/3)"),
                       ("O(n * n)", "n×n"),
                       ("O(sqrt(n))", "sqrt(n)"),
                       ("O(root(n, 3))", "n^(1/3)")):
        ci, tg = build(tu(body))
        check(tg is not None and tg.render() == want,
              f"旧写法 `{body}` 仍 → {want}",
              "" if tg is None else tg.render())


def test_pow_field_base():
    """**target 的幂允许「字段基」，且与 κ 的 `n(recv.n_)` 是同一个量**（2026-11-12）。

    ## 复现的旧洞

    `place` 的 κ 用尺寸量 `n(recv.n_)`（接收者字段），但 target 里：

        O(recv.n_)        → `cost` 能判（字段链走 `expr_to_bound` 的 FIELD_TAG），
                            可 `check` 报 `E-Target-BadForm`；
        O(recv.n_ ^ 8)    → `^` 没被幂那条路收，`_target_cost` 直接认不出
                            （打印「target 写了但解析失败」）。

    **两条命令对同一份 `.tu` 给出相反结论**，而且真正想写的「字段基的幂」
    写不出来。根因是两处形状表都只认 `path` 底数。

    ## 现在的口径

        O(recv.n_)            == n(recv.n_)      与 κ **同一个**尺寸量
        O(recv.n_ ^ 8)        == n(recv.n_)^8    `^` 在 target 成本式里是幂
        O((recv.n_) ^ 8)      == n(recv.n_)^8    括号不改变值
        O(recv.n_ ** 8)       == n(recv.n_)^8    与 `**` 同一条构造

    指数仍**只收常数**（字面量 / 常数分数）：`recv.n_ ^ recv.n_` 报错，
    并在诊断里点明 `^` 与原因。
    """
    print("[11] target 的幂：字段基（`recv.n_`）与 `^`")
    from checker import _target_cost_shape_ok
    from parser import parse_source
    from cost_interp import CostInterp

    eln_src = "pub u64 f(S& recv, u64 row) { return recv.n_ + row; }\n"
    tu_tpl = ("struct S {{ n_: u64, }}\n"
              "spec f(S& recv, u64 row) -> u64 {{ effects {{ }}; "
              "target {{ time: {{ max: {body} }}, allocation: {{ max: 0 }}, }}; }}\n")

    def build(body):
        tu_mod = parse_source(tu_tpl.format(body=body), "a.tu").module
        eln_mod = parse_source(eln_src, "a.eln").module
        ci = CostInterp(eln_mod, tu_mod)
        return tu_mod, ci, ci.target_cost("f")

    def slot_expr(tu_mod):
        """`target.time.max` 槽位里的那个表达式节点（形状层看的就是它）。"""
        for c in tu_mod.children:
            if c.kind != "spec":
                continue
            for b in c.children:
                if b.kind != "spec-body":
                    continue
                for cl in b.children:
                    if cl.kind == "clause" and cl.name == "target":
                        for d in cl.children:
                            if d.kind == "dim" and d.name == "time":
                                for s in d.children:
                                    if s.kind == "slot" and s.children:
                                        return s.children[0]
        return None

    # ---- ① 字段链单独写：与 κ 同名（旧版 check 拒、cost 收，两边不一致）
    _tu, _ci, tg = build("O(recv.n_)")
    check(tg is not None and tg.render() == "n(recv.n_)",
          "O(recv.n_) → n(recv.n_)（与 κ 同一个尺寸量）",
          "" if tg is None else tg.render())
    check(tg is not None and tg.variables() == ["recv.n_"],
          "字段链的尺寸量名字就是 `recv.n_`",
          "" if tg is None else str(tg.variables()))

    # ---- ② 幂的底数可以是字段链：`^` / `**` / 带括号，落成同一个 Cost
    for body, want, order in (("O(recv.n_ ^ 8)", "n(recv.n_)^8", 8),
                              ("O((recv.n_) ^ 8)", "n(recv.n_)^8", 8),
                              ("O(recv.n_ ^ 9)", "n(recv.n_)^9", 9),
                              ("O(recv.n_ ** 8)", "n(recv.n_)^8", 8)):
        _tu, _ci, tg = build(body)
        check(tg is not None and tg.render() == want,
              f"target 认 `{body}` → {want}",
              "" if tg is None else tg.render())
        check(tg is not None
              and _profile(tg, "recv.n_") == (Fraction(order), 0, 0),
              f"`{body}` 的阶 = 指数字面量",
              "" if tg is None else str(_profile(tg, "recv.n_")))

    # ---- ③ 真的参与判阶：κ = n(recv.n_)^8 对 target 同阶判达成、高一次判债务
    _tu, _ci, tg = build("O((recv.n_) ^ 8)")
    check(compare(Cost.pow(Cost.size("recv.n_"), 8), tg).kind == "ok",
          "κ = n(recv.n_)^8 vs target n(recv.n_)^8 → 达成")
    check(compare(Cost.pow(Cost.size("recv.n_"), 9), tg).kind == "debt",
          "κ = n(recv.n_)^9 vs target n(recv.n_)^8 → 债务")

    # ---- ④ 形状层与语义层**同判**：形状拒 ⇒ 语义必拒；形状收的两种例外要
    #         说清（`^` 的指数由语义层细判；`%` / `<` 两边都拒）。
    cases = [
        ("O(recv.n_)", True, True),
        ("O(recv.n_ ^ 8)", True, True),
        ("O((recv.n_) ^ 8)", True, True),
        ("O(recv.n_ ** 8)", True, True),
        ("O(recv.n_ ^ 9)", True, True),
        # 形状层**故意**只查底数，指数留给语义层（它才说得出「为什么不行」）
        ("O(recv.n_ ^ recv.n_)", True, False),
        ("O(4 % 2)", False, False),
        ("O(recv.n_ < recv.n_)", False, False),
    ]
    for body, shape_want, sem_want in cases:
        tu_mod, _ci, tg = build(body)
        shape = _target_cost_shape_ok(slot_expr(tu_mod))
        check(shape == shape_want,
              f"形状层对 `{body}` 的判定 = {shape_want}",
              f"shape={shape}")
        check((tg is not None) == sem_want,
              f"语义层对 `{body}` 的判定 = {sem_want}（与形状层同一份口径）",
              "" if tg is None else tg.render())

    # ---- ⑤ 变量指数：收不了，但要说清是 `^` 的指数、为什么
    _tu, _ci, tg = build("O(recv.n_ ^ recv.n_)")
    errs = _ci.target_errors
    check(tg is None, "`recv.n_ ^ recv.n_`（变量指数）→ 认不出，不猜",
          "" if tg is None else tg.render())
    check(bool(errs) and "不是常数" in errs[0]["message"]
          and "`^`" in errs[0]["message"],
          "变量指数的诊断点明「不是常数」且指明是 `^`",
          "" if not errs else errs[0]["message"])


def test_self_recursive_target_not_feedback():
    """**自递归函数声明的 target 不许反过来抬高自己的 κ**（2026-11-12）。

    ## 旧行为（假债务）

    递归调用点那段为了「κ 单调不减」，会把**被调函数声明的 target** 也并进
    自调用的下界：`_old = max(1+args, 1+target+args)`。对**自调用**这就成了
    循环定义——`place` 真实阶是 10，写 `O(n(recv.n_)^10)`（同阶）反而让
    自调用变成 n^10、外层再乘一次 n ⇒ κ = n^11 ⇒ 判**债务**。写多松都不行，
    于是自递归函数永远判不成「达成」。

    ## 现在的口径

    直接自调用（栈顶就是它自己）**不读自己的 target**：κ 只由推导决定。
    target 写同阶 → 达成；写紧一阶 → 真债务。下面的 `walk` 是同一形状
    （`for` 里的自调用，D = 4 ⇒ 激活数 `(D+1)·B^D`，再乘每层 n）。
    """
    print("[12] 自递归的 target 不反馈进 κ")
    from parser import parse_source
    from cost_interp import CostInterp

    eln_src = (
        "pub u64 walk(S& recv, u64 row) {\n"
        "    if (row == recv.n_) { return 1; }\n"
        "    u64 s = 0;\n"
        "    for (u64 col = 0; col < recv.n_; ++col) {\n"
        "        s = s + walk(recv, row + 1);\n"
        "    }\n"
        "    return s;\n"
        "}\n")

    def build(target=None):
        _t = (f"target {{ time: {{ max: {target} }}, "
              f"allocation: {{ max: 0 }}, }}; " if target else "")
        tu_src = ("struct S { n_: u64, }\n"
                  f"spec walk(S& recv, u64 row) -> u64 {{ effects {{ }}; "
                  f"recursion depth <= 4; {_t}}}\n")
        tu_mod = parse_source(tu_src, "a.tu").module
        eln_mod = parse_source(eln_src, "a.eln").module
        ci = CostInterp(eln_mod, tu_mod)
        return ci, ci.target_cost("walk")

    ci0, _ = build()
    k0 = ci0.cost_of("walk")
    _ci6, t6 = build("O((recv.n_) ^ 6)")
    k6 = _ci6.cost_of("walk")
    check(k6.render() == k0.render(),
          "声明 target 不改变自递归函数的 κ（逐字相同）",
          f"{k0.render()[:80]} vs {k6.render()[:80]}")
    check(compare(k6, t6).kind == "ok",
          "同阶 target（n^6）→ 达成（旧实现会判债务）",
          compare(k6, t6).render())
    _ci5, t5 = build("O((recv.n_) ^ 5)")
    k5 = _ci5.cost_of("walk")
    check(compare(k5, t5).kind == "debt",
          "紧一阶 target（n^5）→ 真债务（判定没被架空）",
          compare(k5, t5).render())


def main():
    test_required_cases()
    test_range_independence()
    test_profile_rules()
    test_fallback()
    test_multi_size_orders()
    test_fractional_orders()
    test_log_divisor_orders()
    test_loglog_orders()
    test_range_diff_orders()
    test_star_star_target()
    test_pow_field_base()
    test_self_recursive_target_not_feedback()

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
