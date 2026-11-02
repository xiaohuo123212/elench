#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""check.py —— soundness 检测器的**裁判**：`ops > κ` 就是反例。

    python tests/fuzz/check.py [--n 200] [--sizes 2,4] [--shrink]

输出：覆盖率 + 反例（源码写进 `tests/corpus/CE-####.eln`）。

已知边界（§G 里也记着）：共享盲点（计数与 κ 一起错）、只测小 n、
非终止靠预算、符号 κ 跳过。
"""
from __future__ import annotations

import os as _os

_os.environ.setdefault("ELENCH_COUNT_OPS", "1")

import argparse
import io
import os
import sys
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(ROOT, "src")
for _p in (SRC, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gen  # noqa: E402
from cost_check import evaluate  # noqa: E402
from cost_interp import CostInterp  # noqa: E402
from interp import Interp  # noqa: E402
from parser import parse_source  # noqa: E402

XS = list(range(1, gen.XS_LEN + 1))


def judge(tu_src: str, eln_src: str, sizes: List[int], family: str = "loop"):
    """返回 (是否反例, 明细[(n, ops, κ)])；解析失败返回 (False, [])。"""
    tu_r = parse_source(tu_src, "fuzz.tu")
    el_r = parse_source(eln_src, "fuzz.eln")
    if tu_r.diagnostics or el_r.diagnostics:
        return False, []
    try:
        ci = CostInterp(el_r.module, tu_r.module,
                        diags=getattr(el_r.module, "_parse_diags", None))
        k = ci.cost_of("f")
        if k.is_top():
            return False, []
    except Exception:                                       # noqa: BLE001
        return False, []
    rows = []
    for n in sizes:
        if family == "rec":
            args = [n]
            env = {"n": n}
        elif family == "hof":
            args = [list(XS)]
            env = {"xs": len(XS)}
        elif family == "dispatch":
            # **规模必须随 n 变**：否则每个 n 都在测同一份输入，表看不出斜率
            _xs = [(i % 7) + 1 for i in range(n)]
            args = [_xs]
            env = {"xs": n}
        else:
            args = [list(XS), n]
            env = {"xs": len(XS), "n": n}
        try:
            it = Interp(el_r.module, tu_r.module)
            it.call("f", args)
            ops = it.ops
        except Exception:                                   # noqa: BLE001
            continue
        # **`k(visit)` 要代入**：κ 里的函数参数成本是符号，`evaluate` 不认识它，
        # 直接求值会得 inf → 整族被判"测不出来"。这里把每个 `k(NAME)`
        # 换成 `κ(NAME)` 在该规模上的值。
        _env = dict(env)
        # `k(visit)` 不在 `variables()` 里（改动它会打断 `kappa_vs_ops` 的
        # "符号 κ" 判定），所以这里直接从 **render** 里抓。只认 `k(名字)`。
        for _m2 in __import__("re").finditer(r"k\(([A-Za-z_][A-Za-z0-9_]*)\)",
                                            k.render()):
            try:
                _sub2 = ci.cost_of(_m2.group(1))
                _sv2 = float(evaluate(_sub2, env))
                if _sv2 != float("inf"):
                    _env["k(%s)" % _m2.group(1)] = _sv2
            except Exception:                           # noqa: BLE001
                pass
        for _v in k.variables():
            _m = __import__("re").match(r"k\(([A-Za-z_][A-Za-z0-9_]*)\)$", str(_v))
            if not _m:
                continue
            try:
                _sub = ci.cost_of(_m.group(1))
                _sv = float(evaluate(_sub, env))
                if _sv != float("inf"):
                    _env[str(_v)] = _sv
            except Exception:                           # noqa: BLE001
                pass
        kv = float(evaluate(k, _env))
        if kv == float("inf"):
            continue
        rows.append((n, ops, kv))
    return any(o > kv for _n, o, kv in rows), rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--sizes", default="2,4")
    ap.add_argument("--shrink", action="store_true")
    ap.add_argument("--save-dir", default=os.path.join(ROOT, "tests", "corpus"))
    args = ap.parse_args()
    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]

    cover: Dict[str, int] = {k: 0 for k in gen.VOCAB}
    compiled = 0
    ce = []
    unmeasured = []
    for seed in range(args.n):
        tu_src, eln_src, family = gen.gen_program(seed)
        tu_r = parse_source(tu_src, "fuzz.tu")
        el_r = parse_source(eln_src, "fuzz.eln")
        if tu_r.diagnostics or el_r.diagnostics:
            continue
        compiled += 1
        for w in gen.used(eln_src):
            cover[w] = cover.get(w, 0) + 1
        bad, rows = judge(tu_src, eln_src, sizes, family)
        if not rows:
            unmeasured.append((seed, family))
        if bad:
            worst = max(rows, key=lambda r: r[1] - r[2])
            src = eln_src
            if args.shrink:
                from shrink import shrink
                src = shrink(tu_src, eln_src, sizes)
            ce.append((seed, worst, src))

    print(f"生成 {args.n}：编译通过 {compiled}，反例 {len(ce)}，"
          f"**测不出来 {len(unmeasured)}**（不算通过）")
    if unmeasured:
        fams = {}
        for _s, f in unmeasured:
            fams[f] = fams.get(f, 0) + 1
        print("  测不出来的族：" + "，".join(f"{k}×{v}" for k, v in fams.items()))
    print("覆盖率：" + "，".join(f"{k}×{v}" for k, v in cover.items()))
    if ce:
        os.makedirs(args.save_dir, exist_ok=True)
        for seed, (n, ops, kv), src in ce[:10]:
            path = os.path.join(args.save_dir, f"CE-{seed:04d}.eln")
            io.open(path, "w", encoding="utf-8", newline="\n").write(src)
            print(f"\n✗ seed={seed} n={n}: ops={ops} > κ={kv:.0f} → {path}")
            print(src)
    return 1 if ce else 0


if __name__ == "__main__":
    raise SystemExit(main())
