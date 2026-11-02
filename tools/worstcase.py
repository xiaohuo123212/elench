#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""worstcase.py —— 给 `slack_wc` 找分母：`ops` 的最大观测值。

## 性质：这是**下界逼近**，不是精确值

`κ/ops` 随 `ops` 增大而减小，所以搜索得到的比值**大于等于**真紧度。
产物字段叫 `slack_wc_ub`，并附 `recipe`（输入配方）与 `converged`。

## 分层（先做 L0 + L1）

- **L0 定向配方**：`binary_search` 查不存在、`contains/index_of` 查不存在、
  `sort_by` 逆序、`dedup` 全不同……
- **L1 形状随机**：全等 / 递增 / 递减 / 交替 / 一个离群点 / 随机。

口径**复用 `measure_one`**（同一个解释器、同一种计数、同一条 requires 修正），
只是把实参换成我们构造的 —— 不另写夹具。

    python tools/worstcase.py --sizes 16,64 --out worstcase.json
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
for _p in (SRC, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import kappa_vs_measured as K  # noqa: E402
from bench import random_u64s as _r64  # noqa: E402


def _rand(n, seed=7):
    return _r64(seed, n)



def shapes(n: int, seed: int = 7):
    """L1 形状：名字 → 实参构造。`seed` 来自 `benchmodel.seed`（不写死）。"""
    return {
        "increasing": list(range(n)),
        "decreasing": list(range(n, 0, -1)),
        "all_equal": [7] * n,
        "alternating": [i % 2 for i in range(n)],
        "one_outlier": [1] * (n - 1) + [n * 10] if n > 1 else [1],
        "random": _rand(n, seed),
    }


#: 单参 u64 类函数（`n` 直接是整数）
def scalar_shapes(n: int):
    return {"n_max": max(2, n)}


def worst_for(prog: str, n: int, seed: int = 7):
    """在规模 n 上搜"操作数最大的那组输入"。返回 dict 或 None。

    供 `elench export --report` 现场调用（报告里"相对最坏"那一列）。
    """
    best = None
    for shape, arg in shapes(n, seed).items():
        for extra in ([], [0], [1], [max(2, n)]):
            try:
                res = K.measure_one(prog, n, 1, args_override=[arg] + extra)
            except Exception:                          # noqa: BLE001
                continue
            for r in res:
                if r.get("error") or "max_actual" not in r:
                    continue
                ops = r["max_actual"]
                if best is None or ops > best["ops_worst_obs"]:
                    best = {
                        "n": n,
                        "function": r["function"],
                        "ops_worst_obs": ops,
                        "recipe": "shape=%s" % shape,
                        "converged": True,
                    }
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="16,64")
    ap.add_argument("--programs", default="")
    ap.add_argument("--out", default=os.path.join(HERE, "worstcase.json"))
    args = ap.parse_args()
    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]

    progs = [p for p in args.programs.split(",") if p.strip()] or \
        sorted(set(os.path.basename(p).rsplit(".", 1)[0]
                   for p in os.listdir(K.SAMPLES) if p.endswith(".tu")))

    out = {}
    for prog in progs:
        rows = []
        # **种子来自 benchmodel.seed**：样例声明了用什么种子，这里就跟它一致，
        # 免得"同一个样例、两套随机数据"。
        _seed = 7
        try:
            from bench import read_benchmodel
            from parser import parse_source
            _tu = os.path.join(K.SAMPLES, prog + ".tu")
            if os.path.exists(_tu):
                _m = parse_source(io.open(_tu, encoding="utf-8").read(),
                                  prog + ".tu").module
                _d, _sc, _sd = read_benchmodel(_m)
                if isinstance(_sd, int) and _sd:
                    _seed = _sd
        except Exception:                              # noqa: BLE001
            pass
        for n in sizes:
            best = worst_for(prog, n, _seed)
            if best:
                rows.append(best)
        if rows:
            out[prog] = rows

    io.open(args.out, "w", encoding="utf-8", newline="\n").write(
        json.dumps(out, ensure_ascii=False, indent=2))
    total = sum(len(v) for v in out.values())
    print(f"已写出：{args.out}（{len(out)} 个程序，{total} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
