#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""exp_s4.py —— 实验 S4：输入分布对 ops 的放大倍数

## 对应哪一条

`实验-评价方案.md` §5 S4：`ops(Di) / ops(D1)`，用来量化
"最坏输入构造能不能显著改变结论"（对应 A3 消融）。

## 口径

- 输入族：D1 均匀随机 / D2 已排序（等差）/ D3 重复密集
  （原方案的 D4–D6 需要按算法定制：哈希同桶、重复前缀、对抗分支，
  这里先做前三个通用族）；
- 规模：n ∈ {16, 64, 256, 1024}；
- 指标：`Interp.counters()["operations"]`（**必须** `ELENCH_COUNT_OPS=1`，
  否则恒为 0——这是踩过的坑）；
- 放大倍数以同规模的 D1 为 1.0。

## 怎么跑

    cd src
    ELENCH_COUNT_OPS=1 python ..\\tools\\exp_s4.py

Windows PowerShell:
    $env:ELENCH_COUNT_OPS="1"; python tools/exp_s4.py
"""
import csv
import os
import random
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source       # noqa: E402
from interp import Interp             # noqa: E402

CASES = os.path.join(ROOT, "文档", "样例")
OUT = os.path.join(ROOT, "文档", "设计", "归档", "实验-2026-11", "s4.csv")
SIZES = (16, 64, 256, 1024)


def d1(n):
    rnd = random.Random(42)
    return sorted(rnd.randrange(0, 10 ** 6) for _ in range(n))


def d2(n):
    return list(range(n))


def d3(n):
    return sorted(i // 8 for i in range(n))


FAMILIES = {"D1": d1, "D2": d2, "D3": d3}


def run_case(name, fn, make_args):
    tu = parse_source(open(os.path.join(CASES, name + ".tu"), encoding="utf-8").read(),
                      name + ".tu").module
    ch = parse_source(open(os.path.join(CASES, name + ".eln"), encoding="utf-8").read(),
                      name + ".eln").module
    rows = []
    for n in SIZES:
        for fam, gen in FAMILIES.items():
            xs = gen(n)
            it = Interp(ch, tu)
            try:
                r = it.call(fn, make_args(xs))
                ops = it.counters().get("operations", 0)
                iters = it.counters().get("iterations", 0)
                # **`Err(...)` 也是"没跑成"**：快速失败的 ops 没有比较意义
                err = "" if not str(r).startswith("Err(") else str(r)[:28]
            except Exception as ex:                      # noqa: BLE001
                ops, iters, err = 0, 0, type(ex).__name__
            rows.append({"case": name, "fn": fn, "family": fam, "n": n,
                         "ops": ops, "iterations": iters, "error": err})
    return rows



def run_lcs():
    """`lcs(const vector<u8>& a, const vector<u8>& b)`：相同串 vs 全异串。

    这是本批**唯一**测出真实分布效应的用例：全异的 ops 约为相同的 1.39 倍。
    """
    name = "lcs"
    tu = parse_source(open(os.path.join(CASES, name + ".tu"), encoding="utf-8").read(),
                      name + ".tu").module
    ch = parse_source(open(os.path.join(CASES, name + ".eln"), encoding="utf-8").read(),
                      name + ".eln").module
    rows = []
    for n in (8, 16, 32, 64):
        for fam, mk in (("D1", lambda k: (b"a" * k, b"a" * k)),      # 完全相同
                        ("D3", lambda k: (b"a" * k, b"b" * k))):     # 全异
            a, b = mk(n)
            it = Interp(ch, tu)
            r = it.call("lcs", [list(a), list(b)])
            c = it.counters()
            rows.append({"case": name, "fn": "lcs", "family": fam, "n": n,
                         "ops": c.get("operations", 0), "iterations": c.get("iterations", 0),
                         "error": "" if not str(r).startswith("Err(") else str(r)[:28]})
    return rows


def main() -> int:
    if not os.environ.get("ELENCH_COUNT_OPS"):
        print("✗ 先设 ELENCH_COUNT_OPS=1（否则 operations 恒为 0）", file=sys.stderr)
        return 2
    all_rows = []
    all_rows += run_case("search", "binary_search",
                         lambda xs: [list(xs), xs[len(xs) // 2]])
    # **堆类**：分布可能真改变 sift 次数（方案预期 ≥5× 的那一类）
    # **k 必须 ≤ HEAP_CAP**（topk.eln 里 k 超容量直接返回 Err(RankTooLarge)，
    # 而且**快速失败**：ops 只有 19——上一轮把它当成"数据"是错的）
    all_rows += run_case("topk", "topk",
                         lambda xs: [list(xs), max(1, min(len(xs) // 10, 32))])
    # **线性统计**：对照组，预期与 binary_search 一样 ≈1×
    all_rows += run_case("stats", "summarize", lambda xs: [list(xs)])

    # **LCS**：分布真的改变分支走向（相同 vs 全异），是这批里唯一测出差异的
    all_rows += run_lcs()

    for r in all_rows:
        base = next(x for x in all_rows
                    if x["family"] == "D1" and x["n"] == r["n"] and x["fn"] == r["fn"])
        r["amplification"] = round(r["ops"] / base["ops"], 3) if base["ops"] else None
        print(f"  {r['fn']:<14} {r['family']}  n={r['n']:<5} ops={r['ops']:<7} "
              f"iters={r['iterations']:<6} 放大={r['amplification']} {r['error']}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    print(f"\n  数据 → {os.path.relpath(OUT, ROOT)}（{len(all_rows)} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
