#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""exp_s2.py —— 实验 S2：紧度对规模的敏感（tightness vs n）

## 对应哪一条

`实验-评价方案.md` §5 S2：横轴 `n`，纵轴 `tightness_wc(n)`，
预期"单调下降，趋近渐近值"。

## 口径

- `κ(n)`：由 `tools/kappa_vs_measured.py --n=N --json` 给出（同一份成本模型）；
- `max 实测`：该工具跑 20 组输入取最坏（**不是随机一次**——这是它的硬规则）；
- **紧度 = κ(n) / max实测**。1.0 表示"恰好压住"，越大越松。

## 怎么跑

    python tools/exp_s2.py                # 默认 n ∈ {16, 64, 256}
    python tools/exp_s2.py --sizes=16,64,256,1024
"""
import csv
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TOOL = os.path.join(ROOT, "tools", "kappa_vs_measured.py")
OUT = os.path.join(ROOT, "文档", "设计", "归档", "实验-2026-11", "s2.csv")


def run_at(n):
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as fh:
        tmp = fh.name
    try:
        subprocess.run([sys.executable, "-B", TOOL, f"--n={n}", f"--json={tmp}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
        with open(tmp, encoding="utf-8") as fh:
            return json.load(fh)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main() -> int:
    sizes = [16, 64, 256]
    for a in sys.argv[1:]:
        if a.startswith("--sizes="):
            sizes = [int(x) for x in a.split("=", 1)[1].split(",") if x.strip()]

    rows = []
    for n in sizes:
        data = run_at(n)
        for r in data.get("rows", []):
            k, m = r.get("kappa_at_n"), r.get("max_actual")
            ok = isinstance(k, (int, float)) and isinstance(m, int) and m > 0
            tight = round(k / m, 2) if ok else None
            # **紧度 > 20 一律可疑**：容器有容量上限（HEAP_CAP / LcsError.Capacity），
            # 超过就返回 Err 快速失败，max 实测只剩几十，比值被放大成天文数字。
            # 这类行**不是"κ 太松"，是"根本没测成"**——必须标出来，不能当结论。
            suspect = tight is not None and tight > 20
            rows.append({"n": n, "function": r["function"], "kappa": k,
                         "max_measured": m, "tightness": tight,
                         "holds": r.get("holds"), "suspect": suspect})

    print(f"  {'函数':<16} " + " ".join(f"n={n:<8}" for n in sizes))
    names = sorted({r["function"] for r in rows})
    for nm in names:
        cells = []
        for n in sizes:
            r = next((x for x in rows if x["function"] == nm and x["n"] == n), None)
            if not r or r["tightness"] is None:
                cells.append("-")
            elif r["suspect"]:
                cells.append(f"({r['tightness']:.0f})?")     # 括号 = 疑似测量失效
            else:
                cells.append(f"{r['tightness']:.2f}")
        print(f"  {nm:<16} " + " ".join(f"{c:<9}" for c in cells))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\n  数据 → {os.path.relpath(OUT, ROOT)}（{len(rows)} 行）")
    print("  紧度 = κ(n) / max 实测；1.0 = 恰好压住，越大越松")
    return 0


if __name__ == "__main__":
    sys.exit(main())
