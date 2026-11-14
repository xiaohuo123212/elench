#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_no_free_symbol.py —— 内联**必须**把被调方的形参换成调用点的实参

## 病是什么（2026-11-13，`13_numeric.stddev_milli`）

    static u64 isqrt(u64 x) { for (step = 0; step <= x; ++step) … }
    static u64 stddev_milli(const vector<u64>& xs) { … return isqrt(acc / xs.size()); }

修前 κ(stddev_milli) 里带着 `(x + 1)×33`：`x` 是 **isqrt 的形参**，调用点的实参
`acc / xs.size()` 没把它代换掉。`evaluate` 对查不到的名字给 **inf**（保守、不破界），
可 κ **渲染成一条像模像样的具体式**，判定侧只会说「尺寸量不一致（E23）」，把真凶
指向 target。这正是「用别人的形参名写自己这帧的成本」——本文件钉住它不再出现。

## 三级替换（`cost_interp._subst_callee_scalars`）

| 级 | 依据 | 例 |
|---|---|---|
| ① `_scalar_arg_bound` | 字面量 / 模块常量 / 调用方标量形参 / 不可变局部 | `isqrt(144)` → `145×33` |
| ② `_count_of_expr` | 能写成尺寸量的上界（只对非裸名表达式） | `f(to.size())` → `n(to)` |
| ③ `_scalar_value_bound` | 把**调用方本帧的名字**写进 κ（`a / b ≤ a`） | `isqrt(acc / n(xs))` → `(acc + 1)×33` |

三级都换不出（连名字都给不出来）⇒ **整条 ⊤**，绝不把别人的形参名留在调用方 κ 里。

## 断言（六条）

1. **语料 115 个函数**：κ 里没有**别的函数的形参名**（严格，一个都不许）；
2. 已知的「**局部**名」残留**必须为空**——曾经恰好两条（`13_numeric.main: acc` /
   `17_sortkey.main: hi`），2026-11-13 的「区间减半循环」规则把两条都消掉了
   （`isqrt` 的界成了常数 `65×33`，实参表达式不再进 κ；`lower_bound_by_key` 的
   界成了 `log2(n(xs))+1`，`hi` 不再是上界）。多一条就红。
3. `13_numeric`：`x` 消失，`stddev_milli` 的尺寸量 ⊆ `{xs}`（`acc` 也**不再**出现）；
4. **等价性**：同逻辑两种写法 → κ 逐字节相同、stdout/返回值/ops 逐字节相同；
5. **实测**：解释器计数器给的 ops ≤ κ（同单位，`evaluate` 代入真实的 `acc`）；
6. **反例**：实参连名字都给不出时 → ⊤（不是留符号，也不是瞎猜）。

跑法：

    python -B tests/cases/test_kappa_no_free_symbol.py
"""

import contextlib
import io as _io
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
#: 这批测试原先住在 `src/`，旧语义保留（与 test_bound / test_ops 同）
HERE = SRC
for _p in (SRC, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                        # noqa: BLE001
    pass

import compat                                            # noqa: E402
from parser import parse_source                          # noqa: E402
from cost_interp import CostInterp                       # noqa: E402
from cost_check import evaluate                          # noqa: E402
import interp                                            # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset  # noqa: E402

CORPUS = os.path.join(ROOT, "demo", "corpus")
SAMPLES = os.path.join(ROOT, "文档", "样例")

FAILED = []


def check(cond, name, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


# ---------------------------------------------------------------- 读语料
def load_cpp(base, cdir=CORPUS):
    """`<base>.cpp`（**当 `.eln` 读**，compat 打开）+ `<base>.tu` → `(tu, ch)`。"""
    tu_p = os.path.join(cdir, base + ".tu")
    ch_p = os.path.join(cdir, base + ".cpp")
    if not os.path.isfile(tu_p) or not os.path.isfile(ch_p):
        return None
    compat.set_enabled(True)
    try:
        tu = parse_source(_io.open(tu_p, encoding="utf-8").read(),
                          base + ".tu").module
        ch = parse_source(_io.open(ch_p, encoding="utf-8").read(),
                          base + ".cpp").module
    finally:
        compat.set_enabled(False)
    return tu, ch


def load_eln(base, cdir=SAMPLES):
    tu_p = os.path.join(cdir, base + ".tu")
    ch_p = os.path.join(cdir, base + ".eln")
    if not os.path.isfile(tu_p) or not os.path.isfile(ch_p):
        return None
    tu = parse_source(_io.open(tu_p, encoding="utf-8").read(), base + ".tu").module
    ch = parse_source(_io.open(ch_p, encoding="utf-8").read(), base + ".eln").module
    return tu, ch


def param_names(fn):
    out = set()
    for c in (fn.children or []):
        if c.kind == "params":
            for p in (c.children or []):
                if getattr(p, "name", ""):
                    out.add(p.name)
    return out


def kappa_of_all(tu, ch, table_path=None):
    """按**声明序**算全部 κ（= `dsh cost` 的口径），返回 `(ci, {fn: Cost})`。"""
    ci = CostInterp(ch, tu, table_path=table_path)
    return ci, {nm: ci.cost_of(nm) for nm in ci.fns}


# ---------------------------------------------------------------- [1][2]
# 已知的「局部名」残留（**不是形参**）。
# 2026-11-13：「区间减半循环」规则（`bound.halving_interval_loop`）把原来的两条
# （`13_numeric.main: acc` / `17_sortkey.main: hi`）都消掉了——`isqrt` 的界成了
# 常数，`acc` 不再被内联；`lower_bound_by_key` 的界成了 `log2(n(xs))+1`，`hi`
# 不再是上界。**从今往后这条必须为空**（再出现一条就是新的、没建模的名字）。
UNKNOWN_LOCAL = {}


def test_corpus_no_param_leak():
    print("[1] 语料 115 个函数：κ 里没有**别的函数的形参名**")
    total = 0
    param_leaks = {}
    local_leaks = {}
    for fnf in sorted(os.listdir(CORPUS)):
        if not fnf.endswith(".cpp"):
            continue
        base = fnf[:-4]
        loaded = load_cpp(base)
        if loaded is None:
            continue
        tu, ch = loaded
        ci, kappas = kappa_of_all(tu, ch)
        own = {nm: param_names(ci.fns[nm]) for nm in ci.fns}
        for nm in sorted(ci.fns):
            total += 1
            alien = list((getattr(ci, "kappa_leaks", {}) or {}).get(nm) or [])
            other = set()
            for nm2, ps in own.items():
                if nm2 != nm:
                    other |= ps
            # 交集里排除「本帧自己的形参」（同名不等于泄漏）
            pl = sorted({a for a in alien
                         if a.split(".", 1)[0] in other
                         and a.split(".", 1)[0] not in own[nm]})
            if pl:
                param_leaks[f"{base}.{nm}"] = pl
            rest = sorted(set(alien) - set(pl))
            if rest:
                local_leaks[f"{base}.{nm}"] = rest
    print(f"       函数总数 {total}")
    check(total == 115, f"语料函数数 = 115（实测 {total}）", total)
    check(not param_leaks, "没有一个 κ 留着别人的**形参**名", param_leaks)
    check(local_leaks == UNKNOWN_LOCAL,
          "「局部名」残留为空（`acc` / `hi` 已被区间减半规则消掉）",
          f"实测 {local_leaks}")


def test_samples_clean():
    print("[1b] `文档/样例` 的 29 个函数：κ 里一个外来名字都没有")
    bad = {}
    total = 0
    for fnf in sorted(os.listdir(SAMPLES)):
        if not fnf.endswith(".eln"):
            continue
        base = fnf[:-4]
        loaded = load_eln(base)
        if loaded is None:
            continue
        tu, ch = loaded
        ci, _ = kappa_of_all(tu, ch)
        for nm in ci.fns:
            total += 1
        if getattr(ci, "kappa_leaks", None):
            bad.update({f"{base}.{k}": v
                        for k, v in ci.kappa_leaks.items()})
    check(not bad, f"{total} 个函数里没有外来符号", bad)


# ---------------------------------------------------------------- [3]
def test_13_numeric_no_x():
    print("[3] `13_numeric`：`x`（isqrt 的形参）必须消失")
    tu, ch = load_cpp("13_numeric")
    ci, kappas = kappa_of_all(tu, ch)
    for fn in ("stddev_milli", "main"):
        k = kappas[fn]
        print(f"       κ({fn}) = {k.render()}")
        check("x" not in set(k.variables()), f"{fn} 的 κ 不含 `x`", k.render())
    sd = kappas["stddev_milli"]
    check(set(sd.variables()) <= {"xs"},
          "`stddev_milli` 的尺寸量 ⊆ {xs}", sd.variables())
    # 2026-11-13 起 `acc` **不该**再出现：`isqrt` 的界成了常数（区间减半 ⇒
    # 轮数 ≤ 类型位宽 65），实参表达式 `acc / xs.size()` 不需要进 κ 了。
    # 这条钉的是**结果**：κ 里既不缺信息（仍是上界，见 [5]），也不带本帧标量。
    check("acc" not in set(sd.variables()),
          "`acc` 不进 κ（区间减半让 isqrt 的界与实参无关）", sd.variables())
    # `isqrt` 自己的 κ 现在是**常数**：`hi = x` 是形参、取不到尺寸量 ⇒ 用类型位宽
    # 兜底（u64 ⇒ 65 轮），所以 `x` 也不该出现在它自己的 κ 里。
    check(set(kappas["isqrt"].variables()) == set(),
          "`isqrt` 的 κ 是常数（`x` 不再出现：上界取类型位宽，不是 `x + 1`）",
          kappas["isqrt"].variables())


# ---------------------------------------------------------------- [4]
#: 同一个调用方 + 同一个被调方体，只改**被调方的形参名**（`x` / `z`）或**函数名**
#: （`isqrt` / `root_of`）。这三种写法的 κ 必须**逐字节相同**——被调方的形参名
#: 根本不该出现在调用方的 κ 里（修前会：`(x + 1)×33` vs `(z + 1)×33`）。
_PROBE_EQ = """
#include <vector>
using u64 = unsigned long long;

static u64 {CALLEE}(u64 {P}) {
    u64 lo = 0;
    u64 hi = {P};
    for (u64 step = 0; step <= {P}; ++step) {
        if (lo >= hi) break;
        u64 mid = lo + (hi - lo) / 2;
        if (mid * mid < {P}) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

static u64 f(const std::vector<u64>& xs) {
    u64 acc = 0;
    for (std::size_t i = 0; i < xs.size(); ++i) acc += xs[i] * xs[i];
    return {CALLEE}(acc / xs.size());
}
"""


def parse_cpp(src, name="probe.cpp"):
    """把一段 C++ 当 `.eln` 解析（compat 打开），与 `--compat` 同一条路。"""
    compat.set_enabled(True)
    try:
        return parse_source(src, name).module
    finally:
        compat.set_enabled(False)


def _ops_of_main(ch, tu, fn, args, table=None):
    """跑一次，返回 `(stdout, 返回值 repr, ops 总数)`（`ops_of(None)` = 整棵树）。"""
    old = interp._COUNT
    interp.ops_enable(True)
    buf = _io.StringIO()
    try:
        ops_reset()
        it = Interp(ch, tu, table_path=table)
        with contextlib.redirect_stdout(buf):
            val = it.call(fn, args)
        return buf.getvalue(), repr(val), ops_of(None)
    finally:
        interp.ops_enable(old)


def test_equivalence():
    print("[4] 等价性：同逻辑、只改被调方的**名字** → κ 与 run 逐字节相同")
    xs = [3, 14, 15, 92, 65, 35, 89, 79]
    variants = {
        "形参 x": ("isqrt", "x"),
        "形参 z": ("isqrt", "z"),
        "函数名 root_of": ("root_of", "x"),
    }
    base = None
    for label, (callee, p) in variants.items():
        ch = parse_cpp(_PROBE_EQ.replace("{CALLEE}", callee).replace("{P}", p),
                       "probe_%s.cpp" % callee)
        _ci, kappas = kappa_of_all(None, ch)
        k = kappas["f"].render()
        run = _ops_of_main(ch, None, "f", [xs])
        print(f"       {label}: κ(f) = {k}")
        if base is None:
            base = (k, run)
            continue
        check(k == base[0], f"{label} 的 κ(f) 与基准逐字节相同",
              f"{k}  vs  {base[0]}")
        check(run == base[1], f"{label} 的 stdout/返回值/ops 与基准全等",
              f"{run} vs {base[1]}")


# ---------------------------------------------------------------- [5]
CORPUS_XS = [120, 340, 355, 400, 420, 900, 1000, 1200]


def _acc_of(xs, kmilli=1000):
    """`stddev_milli` 里的 `acc`（定点算术，**与 C++ 逐位相同**）。"""
    n = len(xs)
    mean = (sum(xs) * kmilli) // n
    acc = 0
    for x in xs:
        v = x * kmilli
        d = v - mean if v > mean else mean - v
        acc += d * d
    return acc


def test_ops_le_kappa():
    print("[5] 实测：ops ≤ κ（同单位；κ 的 `acc` 代入**真实**累加值）")
    tu, ch = load_cpp("13_numeric")
    ci, kappas = kappa_of_all(tu, ch)
    n = len(CORPUS_XS)
    acc = _acc_of(CORPUS_XS)
    print(f"       n(xs) = {n}，acc = {acc:,}（定点千分位）")
    for fn, args, env in (
            ("isqrt", [144], {"x": 144}),
            ("stddev_milli", [CORPUS_XS], {"xs": n, "acc": acc}),
            ("main", [], {"acc": acc}),
    ):
        _out, val, ops = _ops_of_main(ch, tu, fn, args)
        kv = evaluate(kappas[fn], env)
        check(ops <= kv, f"{fn}：ops {ops:,} ≤ κ {kv:,.0f}",
              f"ops={ops} κ={kv}")
        print(f"       {fn}: 返回 {val}，ops={ops:,}，κ={kv:,.0f}")


# ---------------------------------------------------------------- [6]
_PROBE_TOP = """
#include <vector>
using u64 = unsigned long long;

static u64 seed_of(u64 a) { return a; }
// 被调方的界**依赖自己的形参** `x`——这是"实参换不出来 ⇒ ⊤"那条的**前提**。
// （2026-11-13：这里的被调方原来是 `isqrt`；区间减半规则把它的界变成了与实参
//   无关的常数 `65 轮`，于是"换不出实参"不再意味着 ⊤。反例本身没错，是**前提**
//   变了——所以换一个界真的依赖形参的被调方，判据一个字不动。）
static u64 sum_to(u64 x) {
    u64 s = 0;
    for (u64 i = 0; i < x; ++i) s += i;
    return s;
}
// 实参是**返回标量的调用**：值上界与尺寸上界都写不出来 ⇒ 整条 ⊤
static u64 f(u64 a) { return sum_to(seed_of(a)); }
"""


def test_unresolvable_is_top():
    print("[6] 反例：连名字都给不出的实参 ⇒ ⊤（不许留符号、也不许猜）")
    ch = parse_cpp(_PROBE_TOP, "probe2.cpp")
    ci, kappas = kappa_of_all(None, ch)
    k = kappas["f"]
    check(k.is_top(), "f 的 κ 是 ⊤", k.render())
    check("seed_of" not in set(k.variables()),
          "f 的 κ 里没有 `seed_of` 之类的符号（没有悄悄留个名字）",
          k.variables())


if __name__ == "__main__":
    test_corpus_no_param_leak()
    test_samples_clean()
    test_13_numeric_no_x()
    test_equivalence()
    test_ops_le_kappa()
    test_unresolvable_is_top()
    print()
    if FAILED:
        print(f"> 失败 {len(FAILED)} 项")
        for x in FAILED:
            print(f"  - {x}")
        sys.exit(1)
    print("> 全部通过")
