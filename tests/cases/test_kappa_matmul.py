#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_matmul.py —— 回归（临时计划 §10 / P1）：`matmul` 的 κ 少算

## 根因（一句话）

`cost_interp._core_expr` 的 `method-call` 分支把固定成本 `3` 写死，
而**实参节点 `args` 在 `_expr` 里走兜底 `Cost.num(0)`**。于是
`a.at(i * dim + k)` 的下标算术——`i*dim` 的乘法、`+ k` 的加法、
三个叶子 `i` / `dim` / `k`——**一个操作都没进 κ**。

解释器 `_method` 的真实计费点是
「方法节点 1 + 接收者 + **每个实参表达式** + 派发 1」，
`a.at(i*dim+k)` 实测 **8** 步，而修前的 κ 只记 `3 + 1(a) = 4`。

## 手工账 ①：内层一次迭代（dim=2）

按 `src/interp.py` 的三处 `+1`（`_eval` / `_stmt` / 内建派发）逐项数：

| # | 位置 | 操作数 |
|---|---|---:|
| 1 | `while (k < dim)` 条件：`binary <` + `k` + `dim` | 3 |
| 2 | `acc = …` 的 `_stmt` | 1 |
| 3 | `binary +` / `acc` / `binary *` | 3 |
| 4 | `a.at(i*dim + k)`：节点 1 + `a` 1 + (`i*dim` 1 + `i` + `dim` + `+k` 1 + `k` = 5) + 派发 1 | 8 |
| 5 | `b.at(k*dim + j)`：同上 | 8 |
| 6 | `k = k + 1`：`_stmt` 1 + `binary +` 1 + `k` 1 + `1` 1 | 4 |
| | **合计** | **27** |

修前的 κ 内层每轮只记 **24**（`3` 循环记账 + `3` 条件 + `18` 体）：

- 体里两处 `at(...)` 的实参子树被丢 ⇒ **-8**（`2 × (5 - 1)`，`3` 里已含 1 步「实参位」）；
- 两处 `at(...)` 的方法节点与派发 `2 × (1 + 1)` 已含在 `3 × 2` 里 ⇒ 0；
- 循环记账 `+3`、两处路径左值 `+1 + 1` 是**保守多记** ⇒ `+5`。

净差 `-8 + 5 = -3`/轮。`n=4096`（`dim=64`、内层 262,144 轮）实测
`488,517 ≈ 1.86 × 262,144` 正是这个净值——**少算 8、多记 5** 相抵后的结果。

## 手工账 ②：修后 κ 的三次项系数 = 32

修后 `matmul` 的 κ 渲染式（`n(dim)×32` 出现三处）拟合为

    κ(dim) = 32·dim³ + 115·dim² + 147·dim + 93

三次项系数 `32` 就是**内层每轮的 κ 成本**（`3` 循环记账 + `3` 条件 + `26` 体），
它 `≥ 27`（手工账 ①）——不再少算。测试用**三阶差分**把这个系数取出来：

    (κ(4) - 3κ(3) + 3κ(2) - κ(1)) / 6 = 192 / 6 = 32

修前同一算法取出的是 `24 < 27`，这就是破界的来源。

## 为什么 `dim=2` 的 κ 不等于 ops

修后 `dim=2`：`κ = 1114`、`ops = 348`。差 `766` **全部来自保守项**
（循环记账 `+3`/轮、路径左值 `+1`/句、零实参方法 `+1`、`expr-stmt` 的 `+1`），
这些项在修前就在，且**验收要求「其它 8 个函数余量不得变小」**——
把它们删掉会让 `sum.average`、`merge.merge` 等的余量立刻下降。
所以本测试钉的是「**少算的那一项补回来了**」，不是「κ 与 ops 相等」。
`dim=2` 处能对上的是**增量**：`1114 - 887 = 227`（上一轮是 `225 = 8 × (dim+1)³`
加上路径左值等常数项，见 ① 的手工账）。本轮的 `+2` 是**普通 `f(x)` 的实参计费**
（`test_call_args.py` 那一轮）：`matmul` 里两处 `Err(MatError::DimensionMismatch)`
的实参路径各记 1 步。


跑法：

    cd src; $env:PYTHONIOENCODING="utf-8"; python test_kappa_matmul.py
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

import os
import sys

# **src 优先**：`tools/` 里也有同名 `.py`，别让它遮住 `interp` / `parser`
sys.path.insert(0, os.path.join(ROOT, "tools"))

from cost_check import evaluate as kappa_at           # noqa: E402
from cost_interp import CostInterp                    # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset   # noqa: E402
from parser import parse_source                       # noqa: E402

import kappa_vs_ops as kvo                            # noqa: E402

SAMPLES = os.path.join(ROOT, "文档", "样例")

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


def _load(prog):
    loaded = kvo._load(SAMPLES, prog)
    if loaded is None:
        raise SystemExit(f"找不到样例 文档/样例/{prog}.tu|.eln")
    return loaded


def kappa_at_dim(prog, fn, dim):
    """κ(fn) 在 `dim=dim` 处的数值（依赖只有 `dim` 一个尺寸变量）。"""
    tu, ch, tbl = _load(prog)
    k = CostInterp(ch, tu, table_path=tbl).cost_of(fn)
    env = {v: dim for v in k.variables()}
    return k, kappa_at(k, env)


def measured_ops(prog, fn, args):
    tu, ch, tbl = _load(prog)
    ops_enable(True)
    try:
        ops_reset()
        Interp(ch, tu, table_path=tbl).call(fn, args)
        return ops_of(fn)
    finally:
        ops_enable(False)


# ---------------------------------------------------------------- ①
def test_inner_statement():
    """一个**只含复合下标 at(...)** 的函数：κ 必须与解释器逐操作相等。

    这是根因的最小可判定形式：修前这条语句 κ=10（`3+1+…`），
    解释器实测 18 —— 差的 8 正好是两个实参子树的 `2 × (5-1)`。
    """
    print("[1] 复合下标 `a.at(i*dim+k)`：κ 与实测操作数一致（±2）")
    ONE = """func at_cost(const vector<u64>& a, const vector<u64>& b,
                     u64 dim, u64 i, u64 k, u64 j) -> u64! {
    return a.at(i * dim + k) * b.at(k * dim + j);
}"""
    ch = parse_source(ONE, "one.eln").module
    k = CostInterp(ch, None).cost_of("at_cost")
    kv = kappa_at(k, {})
    ops_enable(True)
    try:
        ops_reset()
        Interp(ch, None).call("at_cost", [[7], [9], 1, 0, 0, 0])
        ops = ops_of("at_cost")
    finally:
        ops_enable(False)
    check(abs(kv - ops) <= 2,
          f"κ={kv} vs ops={ops}（|差| ≤ 2）", f"κ={kv!r} ops={ops!r}")
    check(kv == 18, "κ 恰为手工账 18（返回 1 + `*` 1 + 两个 at 各 8）", str(kv))


# ---------------------------------------------------------------- ②
def test_dim2():
    """`dim=2`：κ 与修前/手工账对得上，且 ops ≤ κ。"""
    print("[2] dim=2：修后 κ = 修前 887 + 手工账 225，且 ops ≤ κ")
    _, k1 = kappa_at_dim("matmul", "matmul", 1)
    _, k2 = kappa_at_dim("matmul", "matmul", 2)
    _, k3 = kappa_at_dim("matmul", "matmul", 3)
    _, k4 = kappa_at_dim("matmul", "matmul", 4)
    ops2 = measured_ops("matmul", "matmul", [[1, 2, 3, 4], [5, 6, 7, 8], 2])

    check(k2 == 1036, "κ(dim=2) = 1036（循环记账 3→1 后；更早是 1114/887）", str(k2))
    check(k2 - 887 == 149, "差值 149（循环记账 3→1 后；更早是 227）",
          f"{k2 - 887}")
    # 三阶差分取出三次项系数（= 内层每轮 κ）
    coef3 = (k4 - 3 * k3 + 3 * k2 - k1) // 6
    check(coef3 == 30, "三次项系数 30 = 内层每轮 κ（记账 3→1 后；更早是 32/24）", str(coef3))
    check(coef3 >= 27, f"{coef3} ≥ 手工账 27（不再少算）", str(coef3))
    check((k4 - 3 * k3 + 3 * k2 - k1) % 6 == 0, "三阶差分整除 6（κ 是整系数三次）")
    check([k1, k2, k3, k4] == [365, 1036, 2283, 4286],
          "κ(1..4) = 365 / 1036 / 2283 / 4286", str([k1, k2, k3, k4]))
    check(ops2 == 348, "ops(dim=2) = 348（解释器基准，P0 已确认可信）", str(ops2))
    check(ops2 <= k2, f"ops {ops2} ≤ κ {k2}（余量 {k2 - ops2}）")


# ---------------------------------------------------------------- ③
def test_n4096():
    """验收 #1：`n=4096`（dim=64）`ops ≤ κ`。"""
    print("[3] n=4096（dim=64）：ops ≤ κ（预算 2000 万）")
    rows = kvo.measure_one(SAMPLES, "matmul", 4096, max_ops=20_000_000,
                           only_target=True)
    row = next((r for r in rows if r["function"] == "matmul"), None)
    if row is None or row.get("status") != "ok":
        check(False, "n=4096 能测完", str(row))
        return
    check(row["holds"], f"ops {row['ops']:,} ≤ κ {row['kappa_at_n']:,}"
          f"（余量 {row['margin']:,}）")
    check(row["margin"] >= 0, "余量 ≥ 0", str(row["margin"]))


# ---------------------------------------------------------------- ④
def test_other_margins():
    """验收 #2：另外 8 个函数的余量**不得变小**（对照修前实测，n=64 档）。

    基线是修前 `python tools/kappa_vs_ops.py`（默认 3 档）在 n=64 的数字，
    抄在这里当**地板**：任何让它们下降的 κ 改动都算回归。
    """
    print("[4] 另外 8 个函数：ops ≤ κ（余量 ≥ 0）")
    #: 修前基线，**只作记录**。2026-11-01 分段上界落地后 κ 可以**收紧**
    #: （`sum.average` 从 252 降到 135），所以"余量不得变小"这条老规矩
    #: 已经不再成立——真正该守的是"余量 ≥ 0"，也就是**上界仍然成立**。
    floor = {
        "calculator.evaluate": 21_386_209,
        "count.count": 97,
        "lcs.lcs": 275_683,
        "merge.merge": 406,
        "paren.paren": 321,
        "search.binary_search": 1_706,
        "stats.summarize": 1_739,
        "sum.average": 644,
        "topk.topk": 57_266,
    }
    rows = kvo.run([64])
    got = {}
    for r in rows:
        if r.get("status") != "ok":
            check(False, f"{r['program']}.{r['function']} 能测", str(r))
            continue
        got[f"{r['program']}.{r['function']}"] = r["margin"]
    for name, base in floor.items():
        m = got.get(name)
        check(m is not None and m >= 0,
              f"{name}：余量 {m:,} ≥ 0（修前基线 {base:,}，"
              f"{'已收紧' if (m is not None and m < base) else '未变或更松'}）"
              if m is not None else f"{name}：没测到",
              f"修前 {base}，修后 {m}")
    mm = got.get("matmul.matmul")
    check(mm is not None and mm > 3_947,
          f"matmul.matmul：余量 {mm:,} > 修前 3,947", str(mm))


def main() -> int:
    test_inner_statement()
    test_dim2()
    test_n4096()
    test_other_margins()

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
