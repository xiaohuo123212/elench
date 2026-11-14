#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_repeats_neg.py —— `repeats` 认**否定形态**的守卫（2026-11-13）

## 病

`while (done(xs))` 能在 `done` 的 spec 里写 `repeats …` 拿到 κ，但同一个谓词
换三种等价写法就掉回 `⊤`：

    while (!done(xs))              // 条件形态 unary 不可推
    while (done(xs) == false)      // 条件形态 binary 不可推
    while (done(xs) != true)       // 同上

同一个谓词、三种等价写法，只有一种能算出界——正是本项目最不想要的失真。

## 判据只有一份

`bound.predicate_call_of(cond)`：条件若**整个就是**一个谓词调用（可带布尔
包装 / 括号），返回那个 `call`；`&&` / `||` / 比较式一律不收。`cost_interp`
与 `bound_of_condition` 都走它，所以：

1. **等价性**：`p(x)`、`!p(x)`、`p(x) == false`、`p(x) != true`
   （外加字面量在前的 `false == p(x)` / `true != p(x)`、`!!`）
   **κ 逐字节相同**——七条 κ 字符串互比，不是"看起来差不多"。
   括号包裹的写法单列：解释器与成本域都按 `paren` **节点**记 1，所以
   `((p(x) == false))` 的 κ 比裸写法大（2 层括号 ⇒ 每轮 +2）——仍然认得出界，
   但**不假装逐字节相同**（把括号剥掉会让 κ < ops，上界就不成立）。
2. **条件成本也按裸谓词计**（`cost_interp._while_inner`）：多出来的 `!` /
   `==` 是**解释器 ops 那边的记账**，κ 这边不重复计。本文件把这条记账差
   **精确钉住**（`!` 每轮 +1、`== false` 每轮 +2、2 层括号再每轮 +2）,
   避免以后有人把 ops 差当成"界变了"。**括号不外推**：κ 里保留 `paren`
   节点的成本（2 层括号 ⇒ κ 每轮 +2），否则 `((p(x) == false))` 会 κ < ops。
3. **实测**：三档规模（64 / 512 / 4096）下四种写法的返回值 / 真实圈数相同，
   且 `真实圈数 ≤ 声明上界`、`ops ≤ κ`——界是真上界，不是形状像。
4. **反向验收**：target 写成 κ 的阶 ⇒ ✓，收紧一档 ⇒ 债务（▲）；
   把 repeats 从 `n` 收紧成 `1`，κ 从线性变成常数（界确实被用上），
   而**运行期断言**会抓住这个假声明（`E-Repeats-Exceeded`）。
5. **拒绝形状**：谓词没有 repeats / `!(i < n)` / `p(i) == 0` / `p(i) < 2` /
   `p(i) != p(j)` 仍是 ⊤——推不出就不猜。

## 已知缺口（照实写在这里）

`src/checker.py::_pred_of` 还是旧口径（只认裸 `call`），所以 `elench check`
对否定形态仍报 `E-While-NoBound`，而 `cost` 已经能给出界——**check 与 cost
给出相反结论**。修法是一行：让 `_pred_of` 改调 `bound.predicate_call_of`。
本轮 `checker.py` 由另一个代理改（文件所有权），所以没动它。

运行期那半 `interp._repeats_limit` 同样只认裸 `call`：否定形态的假 repeats
**不会被** `E-Repeats-Exceeded` 抓住（`interp.py` 也不在本轮可改文件里）。
所以本文件的"反向验收"用**静态判定 + 正写法的运行期断言**两条腿站住。

跑法：

    python -B tests/cases/test_kappa_repeats_neg.py
"""

import os
import sys

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

import compat                                            # noqa: E402,F401
from cost_check import compare, evaluate as kappa_at      # noqa: E402
from cost_interp import CostInterp                       # noqa: E402
from interp import (Interp, InterpError, ops_enable,     # noqa: E402
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


def mods(tu_src, eln_src):
    return (parse_source(tu_src, "neg.tu").module,
            parse_source(eln_src, "neg.eln").module)


# ---------------------------------------------------------------- 受试模块
#
# 同一个二分查找体，只有**守卫的写法**不同。`in_range` / `done` 是同一个
# 条件的两个极性（`lo < hi` / `lo >= hi`），两条 spec 的 repeats 逐字相同
# （`log(hi - lo) + 1` 是二分真圈数 `floor(log2 m) + 1`）。
TU_EQUIV = """
spec in_range(u64 lo, u64 hi) -> bool {
    effects { };
    repeats log(hi - lo) + 1;
}

spec done(u64 lo, u64 hi) -> bool {
    effects { };
    repeats log(hi - lo) + 1;
}
"""

_BODY = """        const u64 mid = lo + ((hi - lo) / 2);
        const u64 v = xs.at(mid);
        if (v == key) { return mid; }
        if (v < key) { lo = mid + 1; } else { hi = mid; }
"""

#: (函数名, 守卫的写法, 解释器每轮比 f_pos 多记的操作数, 是否与肯定写法**逐字节同 κ**)
#:   —— 第三列是**实测的事实**（解释器把布尔包装也当操作），与 κ 无关：
#:   前七条 κ 按裸谓词计，必须逐字节相同。
#:   `f_paren` 是**第八条**：括号在成本域与解释器里都要记 1 个操作
#:   （`_k_expr_paren`），剥掉会让 κ < ops ⇒ 它的 κ 比肯定写法大 2/轮；
#:   仍然认得出界，只是**不假装逐字节相同**（上界比"好看"重要）。
GUARDS = [
    ("f_pos",      "in_range(lo, hi)",          0, True),
    ("f_not",      "!done(lo, hi)",             1, True),
    ("f_eqf",      "done(lo, hi) == false",     2, True),
    ("f_net",      "done(lo, hi) != true",      2, True),
    ("f_lit_first", "false == done(lo, hi)",    2, True),
    ("f_lit_last",  "true != done(lo, hi)",     2, True),
    ("f_dblnot",   "!!in_range(lo, hi)",        2, True),
    ("f_paren",    "((done(lo, hi) == false))", 4, False),
]

ELN_EQUIV = "\n".join(
    ["bool in_range(u64 lo, u64 hi) { return lo < hi; }",
     "bool done(u64 lo, u64 hi) { return lo >= hi; }"] +
    ["u64 %s(const vector<u64>& xs, u64 key) {\n"
     "    u64 lo = 0;\n"
     "    u64 hi = xs.size();\n"
     "    while (%s) {\n%s    }\n"
     "    return hi;\n}" % (name, guard, _BODY)
     for name, guard, _extra, _same in GUARDS])


# ---------------------------------------------------------------- ① 等价性
def test_equivalence():
    print("[1] 等价写法：七条 κ **逐字节**相同；括号写法 κ 更大但仍是上界")
    tu, ch = mods(TU_EQUIV, ELN_EQUIV)
    ci = CostInterp(ch, tu)
    kappas = {}
    for name, guard, _extra, _same in GUARDS:
        kappas[name] = ci.cost_of(name).render()
        check(not ci.cost_of(name).is_top(),
              f"`while ({guard})` 不再是 ⊤", kappas[name])
    base = kappas["f_pos"]
    for name, guard, _extra, same in GUARDS[1:]:
        if not same:
            continue
        check(kappas[name] == base,
              f"`while ({guard})` 的 κ 与肯定写法逐字节相同",
              f"{kappas[name]!r} != {base!r}")
    check("log2(" in base, "κ 里用的是 done 的 repeats（`log2(hi - lo) + 1`）", base)
    # 同一个界也意味着**不含**否定符号：κ 的文本里没有 `!` / `== false`
    check(all("!" not in k and "false" not in k and "true" not in k
              for k in kappas.values()),
          "κ 文本里没有 `!` / `== false` / `!= true` 的残留", str(kappas))

    # 括号不是免费的：解释器与成本域都按**节点**记，2 层括号 ⇒ κ 每轮 +2。
    # 这里钉住"更大"这个方向（更大仍可能是上界；更小就一定不是）。
    import math
    n = 4096
    kb = kappa_at(ci.cost_of("f_pos"), {"hi": n})
    kp = kappa_at(ci.cost_of("f_paren"), {"hi": n})
    want = 2 * math.floor(math.log2(n)) + 4
    check(kp - kb == want,
          f"`((… == false))` 的 2 层括号在 κ 里按节点计：+{want}（n = {n}）",
          f"{kp - kb} != {want}")
    return tu, ch, ci, kappas


# ---------------------------------------------------------------- ② 解释器实测
def test_measured():
    print("[2] 解释器实测：三档规模，返回值 / 圈数相同，且 ops ≤ κ、圈数 ≤ 界")
    tu, ch = mods(TU_EQUIV, ELN_EQUIV)
    ci = CostInterp(ch, tu)
    ops_enable()
    import math

    for n in (64, 512, 4096):
        xs = list(range(n))
        key = xs[-1]                      # 每档都走「找不到 → 区间减半到底」
        seen = {}
        for name, guard, extra, _same in GUARDS:
            it = Interp(ch, tu)
            ops_reset()
            ret = it.call(name, [xs, key])
            got = ops_of()
            iters = it.iters
            seen[name] = (ret, iters, got)
            # κ ≥ ops（界的定义）
            k = ci.cost_of(name)
            kv = kappa_at(k, {v: n for v in k.variables()})
            check(kv >= got,
                  f"n = {n} `{guard}`：ops {got} ≤ κ {kv:g}", f"{got} > {kv}")
        # 八条写法的返回值与真实圈数必须一致
        rets = {v[0] for v in seen.values()}
        iters = {v[1] for v in seen.values()}
        check(rets == {n - 1}, f"n = {n}：八条写法返回同一结果 {rets}", str(seen))
        check(len(iters) == 1, f"n = {n}：八条写法真实圈数相同 {iters}", str(iters))
        it0 = iters.pop()
        # 声明上界 ≥ 真实圈数（log2(n) + 1）
        bound = math.floor(math.log2(n)) + 1
        check(it0 <= bound,
              f"n = {n}：真实圈数 {it0} ≤ 声明上界 `log2({n}) + 1` = {bound}", "")
        base_ops = seen["f_pos"][2]
        for name, guard, extra, _same in GUARDS:
            check(seen[name][2] == base_ops + extra * it0,
                  f"n = {n} `{guard}`：ops = f_pos + {extra}×{it0} 圈"
                  f"（解释器对布尔包装的记账，κ 不计）",
                  f"{seen[name][2]} != {base_ops} + {extra}×{it0}")
        print(f"       n = {n}: 圈数 {it0}，κ = "
              f"{kappa_at(ci.cost_of('f_pos'), {'hi': n}):g}，ops(f_pos) = {base_ops}")


# ---------------------------------------------------------------- ③ 反向验收
TU_REV = """
spec more(u64 i, u64 n) -> bool {
    effects { };
    repeats REPEATS;
}

spec done(u64 i, u64 n) -> bool {
    effects { };
    repeats REPEATS;
}

spec walk(u64 n) -> u64 {
    effects { };
    target { time: { max: TARGET }, allocation: { max: 0 } };
}

spec walk_neg(u64 n) -> u64 {
    effects { };
    target { time: { max: TARGET }, allocation: { max: 0 } };
}
"""

ELN_REV = """
bool more(u64 i, u64 n) { return i < n; }
bool done(u64 i, u64 n) { return i >= n; }

u64 walk(u64 n) {
    u64 i = 0;
    while (more(i, n)) { i = i + 1; }
    return i;
}

u64 walk_neg(u64 n) {
    u64 i = 0;
    while (!done(i, n)) { i = i + 1; }
    return i;
}
"""


def _rev(repeats, target):
    return mods(TU_REV.replace("REPEATS", repeats).replace("TARGET", target),
                ELN_REV)


def test_reverse():
    print("[3] 反向验收：target 收紧一档 ⇒ ▲；repeats 收紧 ⇒ κ 跟着变（界被用上）")

    # ① target 写成 κ 的阶 ⇒ ✓；收紧到 O(1) ⇒ ▲（两个极性都要判得出）
    tu, ch = _rev("n", "O(n)")
    ci = CostInterp(ch, tu)
    for fn in ("walk", "walk_neg"):
        v = compare(ci.cost_of(fn), ci.target_cost(fn, "time"))
        check(v.kind == "ok",
              f"repeats n，target O(n)：{fn} 判达成 ✓", getattr(v, "note", v))
    tu2, ch2 = _rev("n", "O(1)")
    ci2 = CostInterp(ch2, tu2)
    for fn in ("walk", "walk_neg"):
        v = compare(ci2.cost_of(fn), ci2.target_cost(fn, "time"))
        check(v.kind == "debt",
              f"target 收紧一档（O(n) → O(1)）：{fn} 判债务 ▲",
              getattr(v, "note", v))

    # ② repeats 收紧一档（n → 1）：κ 从线性变常数——证明界**真的**来自 repeats，
    #    而且两个极性同步变化（否定形态不是靠别的分支蒙到的）。
    lin = ci.cost_of("walk").render()
    lin_neg = ci.cost_of("walk_neg").render()
    check(lin == lin_neg, "repeats n：肯定 / 否定写法的 κ 逐字节相同", f"{lin} vs {lin_neg}")
    tu3, ch3 = _rev("1", "O(n)")
    ci3 = CostInterp(ch3, tu3)
    tight = ci3.cost_of("walk").render()
    tight_neg = ci3.cost_of("walk_neg").render()
    check(tight == tight_neg,
          "repeats 收紧成 1：肯定 / 否定写法仍逐字节相同", f"{tight} vs {tight_neg}")
    check(tight != lin and "n×" not in tight,
          "repeats n → 1：κ 从线性收紧成常数（界确实被用上）", f"{lin} → {tight}")

    # ③ 假声明：repeats 1 但真跑 n 圈 —— 正写法被**运行期断言**抓住
    #    （否定形态的运行期缺口见文件头；这里不假装它已被覆盖）。
    for fn in ("walk",):
        try:
            Interp(ch3, tu3).call(fn, [8])
            check(False, f"{fn}：repeats 1 却真跑 8 圈 → 必须抛 E-Repeats-Exceeded",
                  "没抛")
        except InterpError as exc:
            check("E-Repeats-Exceeded" in str(exc),
                  f"{fn}：repeats 1 却真跑 8 圈 → E-Repeats-Exceeded", str(exc))
    got = Interp(ch3, tu3).call("walk_neg", [8])
    print(f"       已知缺口：walk_neg（`!done`）repeats 1 真跑 8 圈 → 运行期未校验"
          f"（返回 {got}；`interp.py` 不在本轮可改文件）")


# ---------------------------------------------------------------- ④ 拒绝形状
TU_REJ = """
spec p(u64 i) -> bool { effects { }; repeats 4; }
spec q(u64 i) -> bool { effects { }; repeats 4; }
spec unknown(u64 i) -> bool { effects { }; }
"""

ELN_REJ = """
bool p(u64 i) { return i < 100; }
bool q(u64 i) { return i < 100; }
bool unknown(u64 i) { return i < 100; }

u64 r_unknown(u64 i) { while (!unknown(i)) { i = i + 1; } return i; }
u64 r_not_cmp(u64 i) { while (!(i < 3)) { i = i + 1; } return i; }
u64 r_eq_zero(u64 i) { while (p(i) == 0) { i = i + 1; } return i; }
u64 r_lt_two(u64 i) { while (p(i) < 2) { i = i + 1; } return i; }
u64 r_pred_pair(u64 i) { while (p(i) != p(i)) { i = i + 1; } return i; }
u64 r_wrapped_cmp(u64 i) { while (!p(i) == false) { i = i + 1; } return i; }
"""


def test_rejects():
    print("[4] 拒绝形状：推不出仍是 ⊤（不猜）")
    tu, ch = mods(TU_REJ, ELN_REJ)
    ci = CostInterp(ch, tu)
    for fn, why in [
        ("r_unknown",      "谓词没有 repeats"),
        ("r_not_cmp",      "`!(i < 3)` 不是谓词调用"),
        ("r_eq_zero",      "`p(i) == 0` 不是布尔比较"),
        ("r_lt_two",       "`p(i) < 2` 不是布尔比较"),
        ("r_pred_pair",    "`p(i) != p(i)` 两侧都不是布尔字面量"),
        ("r_wrapped_cmp",  "`!p(i) == false` 不是「整个条件」"),
    ]:
        check(ci.cost_of(fn).is_top(), f"{fn}：{why} ⇒ ⊤",
              ci.cost_of(fn).render())


def main() -> int:
    test_equivalence()
    test_measured()
    test_reverse()
    test_rejects()

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
