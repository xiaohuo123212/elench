#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""exp_plots.py —— 把 S1–S4 的 CSV 画成 SVG（四张敏感性图）

数据来自 tools/exp_s1.py … exp_s4.py；图写进 文档/设计/归档/实验-2026-11/。
用仓库自己的 `chart.line_chart`，不引入新依赖。
"""
import csv
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from chart import line_chart        # noqa: E402

D = os.path.join(ROOT, "文档", "设计", "归档", "实验-2026-11")


def load(name):
    p = os.path.join(D, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def save(name, svg):
    p = os.path.join(D, name)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(svg)
    print(f"  → {name}  ({len(svg) // 1024} KB)")


def s1():
    rows = load("s1.csv")
    if not rows:
        return print("  S1: 无数据，跳过")
    names = sorted({r["function"] for r in rows})
    series = []
    for nm in names:
        pts = [(float(r["op_cost"]), float(r["elasticity"]))
               for r in rows if r["function"] == nm and r["elasticity"]]
        if pts:
            series.append((nm, pts))
    save("s1-弹性.svg", line_chart(series, "S1　κ 对 OP_COST 的弹性",
                                   "κ(k) / κ(1)", log_x=False, log_y=False))


def s2():
    rows = load("s2.csv")
    if not rows:
        return print("  S2: 无数据，跳过")
    names = sorted({r["function"] for r in rows})
    series = []
    for nm in names:
        pts = [(float(r["n"]), float(r["tightness"]))
               for r in rows
               if r["function"] == nm and r["tightness"] and r.get("suspect") != "True"]
        if pts:
            series.append((nm, pts))
    save("s2-紧度对规模.svg", line_chart(series, "S2　紧度对规模的敏感（已排除可疑行）",
                                         "κ(n) / max 实测", log_x=True, log_y=False))


def s3():
    rows = load("s3.csv")
    if not rows:
        return print("  S3: 无数据，跳过")
    pts = [(float(r["threshold"]), float(r["rate"])) for r in rows]
    save("s3-阈值通过率.svg", line_chart([("通过率", pts)], "S3　门禁通过率随阈值变化",
                                        "通过率", log_x=False, log_y=False))


def s4():
    rows = load("s4.csv")
    if not rows:
        return print("  S4: 无数据，跳过")
    series = {}
    for r in rows:
        if r["family"] == "D1" or not r["amplification"]:
            continue
        key = f"{r['fn']}/{r['family']}"
        series.setdefault(key, []).append((float(r["n"]), float(r["amplification"])))
    save("s4-输入分布放大.svg", line_chart(sorted(series.items()),
                                          "S4　输入分布对 ops 的放大倍数",
                                          "ops(Di) / ops(D1)", log_x=True, log_y=False))


def main() -> int:
    os.makedirs(D, exist_ok=True)
    for f in (s1, s2, s3, s4):
        f()
    return 0


if __name__ == "__main__":
    sys.exit(main())
