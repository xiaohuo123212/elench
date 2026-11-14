#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_log_domain.py —— **符号指数 + log 域判阶**（设计文档的第 1–4 步）。

设计依据：`文档/设计/目前设计/设计-log域判阶-2026-11-12.md`
（`n^n` 要能进 κ，而"能写出来"只是一半：**判阶必须跟着换成 log 域**，
否则 `n^n`、`2^n`、`n^10` 会被判成同一档）。

本文件钉住五件事，一条都不许少：

  [1] **表示与化简**：`Cost("pow", None, (base, exp))` 的构造 / render /
      to_dict / from_dict / variables / const_value；`x^0`、`x^1`、
      `(x^a)^b`、`0^e`、`1^e`、`⊤^e` 六条化简；折叠上限（不构造 4096^4096）。
  [2] **§6 的 15 条对照表**——其中 #1–#4、#10–#11 是**既有能力**，改完必须
      仍判对（回归钉子）；#5–#9 是本次新增，`n^n` 必须与 `2^n`、`n^10` 分开。
  [3] **§4 的每一条拒绝**：除法（log 形式出现减法）、`piece`、底数 ≤ 1 的
      符号幂、`loglog` 及以上嵌套、`ext` / `k(f)` / 陌生节点。
      **`to_log` 失败时判定必须是 unknown**，不许退"达成"或"债务"。
  [4] **饱和**：`pow(4096, 4096)` 的 `evaluate` 不崩、**< 10ms**，
      返回"极大"标记（`MAG_CAP = 1e18`），并且**不构造** 15,000 位的整数。
  [5] **两类断言**：
      ① 等价性——两种写法（同语义、不同局部名）的 κ 与 run **逐字节相同**；
      ② **解释器实测真实值 ≤ 上界**——`ops ≤ evaluate(κ, {n})`，
         既有路径（线性循环）与新形状（`n^(n+1)`，符号指数）各一条。

跑法：

    cd src; $env:PYTHONIOENCODING="utf-8"; python ../tests/cases/test_kappa_log_domain.py
    # 或者仓库根： python tests/runner.py --tag kappa
"""

import contextlib
import io
import os as _os
import sys as _sys
import time

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
if SRC not in _sys.path:
    _sys.path.insert(0, SRC)

from fractions import Fraction  # noqa: E402

_sys.stdout.reconfigure(encoding="utf-8")

from cost_domain.cost_check import _profile, compare, evaluate  # noqa: E402
from cost_domain.cost_interp import CostInterp  # noqa: E402
from cost_domain.logdom import (  # noqa: E402
    has_symbolic_exp, to_log, to_log_with_reason)
from cost_domain.symbolic import MAG_CAP, Cost  # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset  # noqa: E402
from parser import parse_source  # noqa: E402

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


def nn():
    """`n(xs)^n(xs)`（符号指数）。"""
    return Cost.pow(n(), n())


# ================================================================ [1] 表示与化简

def test_representation():
    print("[1] 符号指数的表示 / render / to_dict / from_dict / variables")
    xs = n()

    # ---- 形状：`value=None` + `(base, exp)`，与常数指数（`value=Fraction` +
    #      `(base,)`）**不冲突**。选这个形状的理由写在 `symbolic.py` 的模块头。
    k = Cost.pow(xs, xs)
    check(k.kind == "pow" and k.value is None and len(k.args) == 2,
          "形状：Cost(\"pow\", None, (base, exp))", repr(k))
    check(isinstance(k.args[1], Cost) and k.args[1].kind == "size",
          "指数是**一个 Cost**（不是 Fraction、不是新 kind）", repr(k.args[1]))
    check(k.render() == "n(xs)^(n(xs))", "render：n(xs)^(n(xs))", k.render())
    check(k.variables() == ["xs"], "variables() 里同时有底数与指数（去重）",
          str(k.variables()))
    check(k.const_value() is None, "const_value()：符号指数不是常数")
    check(k.is_const() is False, "is_const()：不是常数节点")

    # ---- 旧形状**逐字不变**：常数指数的 render / to_dict 与 2026-11-09 一致
    old = Cost.pow(xs, Fraction(1, 2))
    check(old.kind == "pow" and old.value == Fraction(1, 2) and len(old.args) == 1,
          "常数指数仍是 Cost(\"pow\", Fraction, (base,))（旧形状不变）", repr(old))
    check(old.render() == "sqrt(n(xs))", "常数指数 render 不变：sqrt(n(xs))",
          old.render())
    check(old.to_dict() == {"operator": "power", "exponent": "1/2",
                            "operands": [{"operator": "size_variable",
                                          "variable": "xs"}]},
          "常数指数 to_dict 逐字不变", str(old.to_dict()))

    # ---- to_dict / from_dict（符号指数）
    d = k.to_dict()
    check(d["operator"] == "power", "to_dict：operator 仍是 power", str(d))
    check(isinstance(d.get("exponent"), dict),
          "to_dict：符号指数的 exponent 是**子树对象**（不是字符串）", str(d))
    check(d["exponent"] == {"operator": "size_variable", "variable": "xs"},
          "to_dict：exponent 就是指数子树的签名", str(d["exponent"]))
    back = Cost.from_dict(d)
    check(back.render() == k.render(),
          "from_dict(to_dict(x)) 还原成同一个式子（逐字）", back.render())

    # ---- 六个化简，逐条
    cases = [
        ("x^0 = 1", Cost.pow(xs, 0), "1"),
        ("x^1 = x", Cost.pow(xs, 1), "n(xs)"),
        ("Cost.num(0) 当指数也归一成常数",
         Cost.pow(xs, Cost.num(0)), "1"),
        ("(n^n)^(1/2) = n^(n·1/2)",
         Cost.pow(Cost.pow(xs, xs), Fraction(1, 2)), "n(xs)^(n(xs)×1/2)"),
        ("0^e = 0", Cost.pow(Cost.num(0), xs), "0"),
        ("1^e = 1", Cost.pow(Cost.num(1), xs), "1"),
        ("⊤^e = ⊤", Cost.pow(Cost.top(), xs), "⊤"),
        ("x^⊤ = ⊤", Cost.pow(xs, Cost.top()), "⊤"),
        ("2^n 留节点（不许退 ⊤）", Cost.pow(Cost.num(2), xs), "2^(n(xs))"),
        ("n^n 留节点（不许退 ⊤）", Cost.pow(xs, xs), "n(xs)^(n(xs))"),
    ]
    for label, got, want in cases:
        check(got.render() == want, f"化简：{label}", f"{got.render()} ≠ {want}")

    # ---- 幂**折叠有个上界**：4096^4096 有 15,000 位，不许真构造。
    t0 = time.perf_counter()
    big = Cost.pow(Cost.num(4096), 4096)
    dt = time.perf_counter() - t0
    check(big.kind == "pow" and big.value == Fraction(4096),
          "折叠上限：4096^4096 留节点（不折叠）", repr(big))
    check(big.render() == "4096^4096", "4096^4096 的 render（没有巨大整数尾数）",
          big.render()[:40])
    check(dt < 0.01, "4096^4096 的**构造**也 < 10ms（不构造大整数）", f"{dt*1000:.3f}ms")
    check(Cost.pow(Cost.num(2), 59).kind == "num",
          "2^59 = 5.76e17 < 1e18：仍然折叠（既有行为）")
    check(Cost.pow(Cost.num(2), 64).kind == "pow",
          "2^64 = 1.8e19 > 1e18：不折叠（饱和口径一致）")

    # ---- 等价写法：`2^n` 与 `sqrt(4)^n` 落到**同一个** Cost
    a = Cost.pow(Cost.num(2), xs)
    b = Cost.pow(Cost.pow(Cost.num(4), Fraction(1, 2)), xs)
    check(a.render() == b.render(), "`2^n` 与 `sqrt(4)^n` 是同一个式子（render 一致）",
          f"{a.render()} vs {b.render()}")
    check(Cost.from_dict(b.to_dict()).render() == a.render(),
          "两种写法读回 JSON 后仍是同一个式子")

    # ---- 替换：`substitute_sizes` **连指数一起换**（漏了就是低估）
    f = Cost.pow(Cost.size("arr"), Cost.size("arr"))
    g = f.substitute_sizes({"arr": Cost.size("xs")})
    check(g.render() == "n(xs)^(n(xs))",
          "substitute_sizes 换底数**也换指数**", g.render())
    check(g.variables() == ["xs"], "换完之后没有残留 arr", str(g.variables()))
    p = Cost("sym", "n")              # 标量形参 → 实参表达式
    h = Cost.pow(p, p).substitute_syms({"n": Cost.size("xs")})
    check(h.render() == "n(xs)^(n(xs))",
          "substitute_syms 换底数**也换指数**", h.render())
    check(Cost.pow(p, p).rename_elems({"g": "h"}).render() == "n^(n)",
          "rename_elems 对符号指数照常工作（不崩）")

    # ---- `_same`：符号指数是身份的一部分
    check(_profile(nn(), "xs") is None,
          "_profile(n^n) → None（三元组装不下它，必须靠 log 域）",
          str(_profile(nn(), "xs")))
    check(has_symbolic_exp(nn()) and not has_symbolic_exp(Cost.pow(xs, 10)),
          "has_symbolic_exp 只认符号指数（常数指数不算）")


# ================================================================ [2] 15 条对照表

def test_fifteen_rows():
    """设计文档 §6 的对照表。**逐条写、逐条打印实际结论。**"""
    print("[2] §6 的 15 条对照表（#1–#4、#10–#11 是既有能力的回归钉子）")
    xs = n()
    nlog = Cost.mul(xs, Cost.log2(xs))
    rows = [
        # (编号, κ, target, 期望, 是否走 log 域, 说明)
        (1, Cost.mul(xs, xs), nlog, "debt", False, "n² 增长更快"),
        (2, nlog, Cost.mul(xs, xs), "ok", False, "n log n ≤ n²"),
        (3, nlog, Cost.pow(xs, Fraction(3, 2)), "ok", False, "log n ≤ √n"),
        (4, Cost.pow(xs, Fraction(3, 2)), nlog, "debt", False, "√n 增长更快"),
        (5, nn(), Cost.pow(Cost.num(2), xs), "debt", True, "n log n > n"),
        (6, Cost.pow(Cost.num(2), xs), nn(), "ok", True, "n ≤ n log n"),
        (7, nn(), Cost.pow(xs, 10), "debt", True, "n log n > 10 log n"),
        (8, Cost.pow(xs, 10), nn(), "ok", True, "10 log n ≤ n log n"),
        (9, nn(), nn(), "ok", True, "同形（log 形式结构相同）"),
        (10, Cost.sqrt(xs), xs, "ok", False, "1/2 < 1"),
        (11, xs, Cost.sqrt(xs), "debt", False, "1 > 1/2"),
        (12, Cost.log2(xs), xs, "ok", False, "log ≪ 多项式"),
        (13, Cost.num(5), nn(), "ok", False, "κ 是常数"),
        (14, nlog, nlog, "ok", False, "同形"),
        (15, nlog, Cost.mul(xs, Cost.mul(Cost.log2(xs), Cost.log2(xs))),
         "ok", False, "一个 log 因子 ≤ 两个"),
    ]
    bad = 0
    for i, k, t, want, logdom, why in rows:
        v = compare(k, t)
        ok = v.kind == want
        bad += 0 if ok else 1
        text = (v.detail or v.note or "").replace("\n", " ")
        print(f"    {'✓' if ok else '✗'} #{i:<2} κ={k.render():<34} "
              f"target={t.render():<34} → {v.kind:<7} 期望 {want:<5} | {why}")
        check(ok, f"§6 #{i}：{k.render()} vs {t.render()} → {want}", v.kind)
        if logdom:
            check("log 域判据" in text,
                  f"§6 #{i} 的结论来自 **log 域**", text)
        elif i in (1, 2, 3, 4, 10, 11, 12, 14, 15):
            check("结构判据" in text,
                  f"§6 #{i}：仍是**既有结构判据**（log 域不插手）", text)

    check(bad == 0, "§6：15 条全对", f"有 {bad} 条判反")


# ================================================================ [3] §4 的拒绝

def test_rejections():
    print("[3] §4 的拒绝（**真的拒绝**：to_log 返回 None ⇒ compare 必须 unknown）")
    xs = n()
    lg = Cost.log2(xs)

    def rejected(label, c, needle):
        got, why = to_log_with_reason(c)
        check(got is None, f"拒绝：{label}", "没有拒绝：" + str(got))
        check(bool(why) and needle in why, f"拒绝原因点明「{needle}」", str(why))
        print(f"    ✓ {label:34} 拒绝原因：{why}")

    # ① 除法（log 形式出现减法）
    rejected("n / log n（分母不是常数）", Cost.div(xs, lg), "除法")
    rejected("n / m（分母是另一个尺寸量）", Cost.div(xs, n("ys")), "除法")
    check(to_log(Cost.div(xs, Cost.num(2))) is not None,
          "分母是常数 > 1：**接受**（丢掉 −log2(2)，上界方向）")
    half = Cost.div(xs, Cost.num(Fraction(1, 2)))
    check(to_log(half) is not None,
          "分母是常数 < 1：**接受**（a/(1/2) = 2a 变大，必须把常数加回去）",
          str(to_log(half)))
    check(to_log(half).render() == "log2(n(xs)) + 1",
          "分母 1/2 → log2(n) + log2(2) = log2(n) + 1", to_log(half).render())

    # ② 分段
    pc = Cost.piece("xs", [(1, 10, xs)], xs)
    rejected("piece（分段）", pc, "分段")
    v = compare(pc, nn())
    check(v.kind == "unknown", "κ=piece vs target=n^n → unknown（piece 被拒绝）",
          v.render())

    # ③ 底数 ≤ 1 的符号幂
    check(Cost.pow(Cost.num(Fraction(1, 2)), xs).is_top(),
          "(1/2)^n → ⊤（a<1 递减，不是成本上界）",
          Cost.pow(Cost.num(Fraction(1, 2)), xs).render())
    check(Cost.pow(Cost.num(-2), xs).is_top(),
          "(-2)^n → ⊤（负底数，无实数幂）")
    check(Cost.pow(Cost.num(1), xs).render() == "1", "1^e = 1（单独化简，不退 ⊤）")
    v = compare(Cost.top(), Cost.pow(Cost.num(Fraction(1, 2)), xs))
    check(v.kind == "unknown", "底数 ≤ 1 的幂参与比较 → unknown", v.render())

    # ⑤ loglog 及以上（变换后超过两层 log）
    rejected("loglog n（变换后三层 log）", Cost.loglog(xs), "两层 log")
    rejected("log2(n log n)（log 形式里有三层 log 子项，保守拒绝）",
             Cost.log2(Cost.mul(xs, lg)), "两层 log")

    # 其它"没有阶信息"的形状
    rejected("ext(read_file)", Cost("ext", "read_file"), "外部项")
    rejected("k(f)（函数参数的成本）", Cost("sym", "k(f)"), "k(")
    rejected("陌生节点", Cost("weird", None, ()), "陌生")
    rejected("par（§3 表里没有它）", Cost.par([xs, Cost.num(3)]), "parallel")

    # ⊤ 不是"拒绝"，是"未知仍是未知"
    check(to_log(Cost.top()) is not None and to_log(Cost.top()).is_top(),
          "⊤ → ⊤（未知仍是未知，不是拒绝）", str(to_log(Cost.top())))

    # ---- **to_log 失败 ⇒ compare 必须 unknown**（两侧各一条）
    vk = compare(Cost.div(xs, lg), nn())          # κ 侧被拒绝
    check(vk.kind == "unknown" and "κ 的 log 变换被拒绝" in vk.note,
          "κ 侧 to_log 被拒绝 → unknown（不许退达成/债务）", vk.render())
    vt = compare(nn(), Cost.div(xs, lg))          # target 侧被拒绝
    check(vt.kind == "unknown" and "target 的 log 变换被拒绝" in vt.note,
          "target 侧 to_log 被拒绝 → unknown（不许退达成/债务）", vt.render())
    check(vk.kind not in ("ok", "debt"), "被拒绝**不是**结论", vk.kind)

    # ---- 多尺寸量 + 符号指数：没有单尺寸量以外的口径 → unknown（不猜）
    #      （两边都必须用到 xs 与 ys，否则先撞 E23「尺寸量不一致」）
    multi_k = Cost.pow(xs, n("ys"))
    multi_t = Cost.pow(n("ys"), Cost.mul(xs, n("ys")))
    vm = compare(multi_k, multi_t)
    check(vm.kind == "unknown" and "单尺寸量" in vm.note,
          "符号指数 + 两个尺寸量 → unknown（log 域只有单尺寸量口径）",
          vm.render())

    # ---- **同阶但结构不同 ⇒ unknown**（常数因子在 log 域里是乘幂）
    half_exp = Cost.pow(xs, Cost.div(xs, Cost.num(2)))       # n^(n/2)
    vh = compare(nn(), half_exp)
    check(vh.kind == "unknown",
          "n^n vs n^(n/2)：log 形式同阶、结构不同 → unknown（**不许判达成**）",
          vh.render())
    check("乘幂" in vh.note, "note 说清「常数因子在 log 域里变成乘幂关系」", vh.note)
    v2 = compare(Cost.pow(xs, Cost.mul(Cost.num(2), xs)), nn())   # n^(2n) vs n^n
    check(v2.kind == "unknown", "n^(2n) vs n^n：同阶不同构 → unknown", v2.render())
    # 但**只差常数倍**的判达成（log 意义上的 +常数 = 原式的 ×常数）
    twice = Cost.mul(Cost.num(2), nn())
    vt2 = compare(twice, nn())
    check(vt2.kind == "ok", "2·n^n vs n^n：只差常数倍 → 达成", vt2.render())
    vp = compare(Cost.add(nn(), Cost.num(5)), nn())
    check(vp.kind == "ok", "n^n + 5 vs n^n：常数项不影响常数倍 → 达成", vp.render())


# ================================================================ [4] 饱和

def test_saturation():
    print("[4] 饱和：pow(4096,4096) 的 evaluate 不崩、< 10ms")
    xs = n()

    c = Cost.pow(Cost.num(4096), 4096)
    t0 = time.perf_counter()
    v = evaluate(c, {})
    dt = time.perf_counter() - t0
    print(f"    evaluate(4096^4096) = {v:.3e}  耗时 {dt*1000:.3f}ms")
    check(v == MAG_CAP, "pow(4096,4096) → 饱和标记 MAG_CAP（1e18）", str(v))
    check(dt < 0.01, "pow(4096,4096) 的 evaluate < 10ms", f"{dt*1000:.3f}ms")

    # n^n 在 4096 处同样是天文数字：走 log 空间判，一步乘法都不多做
    t0 = time.perf_counter()
    v2 = evaluate(Cost.pow(xs, xs), {"xs": 4096.0})
    dt2 = time.perf_counter() - t0
    print(f"    evaluate(n^n @ 4096) = {v2:.3e}  耗时 {dt2*1000:.3f}ms")
    check(v2 == MAG_CAP, "n^n @ n=4096 → 饱和标记", str(v2))
    check(dt2 < 0.01, "n^n @ n=4096 的 evaluate < 10ms", f"{dt2*1000:.3f}ms")

    # **阈值以下照常精确**：n^n 在 n=6 时是 46,656
    check(abs(evaluate(nn(), {"xs": 6}) - 46656.0) < 1e-6,
          "n^n @ n=6 = 46656（阈值以下不饱和）",
          str(evaluate(nn(), {"xs": 6})))
    check(abs(evaluate(Cost.pow(Cost.num(2), xs), {"xs": 40}) - 2 ** 40) < 1e-3,
          "2^n @ n=40 = 2^40（阈值以下不饱和）")

    # 直接构造的巨大整数常数也不许崩
    check(evaluate(Cost.num(10 ** 400), {}) == MAG_CAP,
          "Cost.num(10**400) 的 evaluate 不崩（→ MAG_CAP）")

    # ⊤ 参与的幂仍是 +inf（"未知"不许被饱和冒充成有限数）
    check(evaluate(Cost.pow(Cost.top(), xs), {"xs": 4}) == float("inf"),
          "⊤^n 的 evaluate = +inf（不是 1e18）")
    check(evaluate(Cost.pow(xs, Cost.top()), {"xs": 4}) == float("inf"),
          "n^⊤ 的 evaluate = +inf")


# ================================================================ [5] 等价性 + 实测

_TOTAL = """
pub u64 total(const vector<u64>& xs) {
    u64 %(acc)s = 0;
    u64 %(i)s = 0;
    while (%(i)s < xs.length()) {
        %(acc)s = %(acc)s + xs.at(%(i)s);
        %(i)s = %(i)s + 1;
    }
    return %(acc)s;
}
"""

_PLACE = """
pub u64 place(u64 n, u64 row) {
    if (row == n) {
        return 1;
    }
    u64 total = 0;
    u64 col = 0;
    while (col < n) {
        total = total + place(n, row + 1);
        col = col + 1;
    }
    return total;
}
"""

_EMPTY_TU = parse_source("", "a.tu").module


def _load(eln_src, name):
    return parse_source(eln_src, name + ".eln").module


def _run(eln_mod, fn, args):
    """跑一次并返回 `(返回值 repr, stdout, ops)`。"""
    buf = io.StringIO()
    ops_enable(True)
    ops_reset()
    try:
        it = Interp(eln_mod, _EMPTY_TU)
        with contextlib.redirect_stdout(buf):
            val = it.call(fn, args)
        return repr(val), buf.getvalue(), ops_of(fn)
    finally:
        ops_enable(False)


def test_equivalence_and_bound():
    print("[5] 等价性（两种写法 κ 与 run 逐字节相同）+ 实测 ≤ 上界")
    xs = n()

    # ---- ① 两种写法：同一算法的两个写法（局部变量名不同），
    #      κ 的 render **逐字节相同**、run 的返回值与 stdout **逐字节相同**。
    src_a = _TOTAL % {"acc": "s", "i": "i"}
    src_b = _TOTAL % {"acc": "acc", "i": "j"}
    ka = CostInterp(_load(src_a, "a"), _EMPTY_TU).cost_of("total")
    kb = CostInterp(_load(src_b, "b"), _EMPTY_TU).cost_of("total")
    print(f"    写法 A 的 κ = {ka.render()}")
    print(f"    写法 B 的 κ = {kb.render()}")
    check(ka.render() == kb.render(),
          "两种写法的 κ **逐字节相同**", f"{ka.render()} ≠ {kb.render()}")
    ra = _run(_load(src_a, "a"), "total", [[5, 3, 9, 1]])
    rb = _run(_load(src_b, "b"), "total", [[5, 3, 9, 1]])
    check(ra[0] == rb[0], "两种写法的 run 返回值逐字节相同", f"{ra[0]} vs {rb[0]}")
    check(ra[1] == rb[1], "两种写法的 stdout 逐字节相同", f"{ra[1]!r} vs {rb[1]!r}")

    # ---- ② 实测 ≤ 上界（既有路径）：线性循环
    for size in (3, 8, 64):
        val, _out, ops = _run(_load(src_a, "a"), "total", [list(range(size))])
        bound = evaluate(ka, {"xs": float(size)})
        print(f"    total(n={size:<3}) ops = {ops:<6} κ = {bound:<8.0f} "
              f"{'✓' if ops <= bound else '✗'}")
        check(ops <= bound, f"实测 ≤ 上界：total(n={size})",
              f"ops={ops} > κ={bound}")
    check(ra[0] == "18", "返回值是 18（5+3+9+1）", ra[0])

    # ---- ② 实测 ≤ 上界（**新形状**）：n 叉递归，激活数 n^n。
    #      κ = 48·n^(n+1)：激活数 n^n × 每层循环 n 次 × 每次迭代 ≤ 48 个操作
    #      （48 是"每层成本"的**声明常数**，与 κ 契约里的常数因子同一性质）。
    #      这里的点是：`n^(n+1)` 只有**符号指数**写得出来，log 域能判它的阶。
    sym_n = Cost("sym", "n")
    kappa_rec = Cost.mul(Cost.num(48),
                         Cost.pow(sym_n, Cost.add(sym_n, Cost.num(1))))
    check(has_symbolic_exp(kappa_rec),
          "递归探针的 κ 含符号指数（n^(n+1)）", kappa_rec.render())
    print(f"    递归探针 κ = {kappa_rec.render()}  "
          f"（log 形式 = {to_log(kappa_rec).render()}）")
    for size in range(1, 6):
        val, _out, ops = _run(_load(_PLACE, "place"), "place", [size, 0])
        bound = evaluate(kappa_rec, {"n": float(size)})
        print(f"    place(n={size}) 返回 {val:<6} ops = {ops:<8} κ = {bound:<10.0f} "
              f"{'✓' if ops <= bound else '✗'}")
        check(val == repr(size ** size), f"n 叉递归的返回值 = n^n（n={size}）", val)
        check(ops <= bound, f"实测 ≤ 上界：place(n={size})",
              f"ops={ops} > κ={bound}")


# ================================================================ main

def main():
    test_representation()
    test_fifteen_rows()
    test_rejections()
    test_saturation()
    test_equivalence_and_bound()

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
