#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""exp_s3.py —— 实验 S3：门禁判定对紧度阈值的翻转

## 对应哪一条

`实验-评价方案.md` §5 S3：横轴阈值，纵轴通过率，
目标"找到零误报且全拦截的区间"。

## 口径

复用 S2 的实测紧度（`κ(n) / max 实测`）：

- 门禁规则假设为"紧度 ≤ 阈值则放行"；
- **可疑行（频繁失败导致紧度虚高）排除**——用 S2 的 `suspect` 列；
- 通过率 = 放行数 / 可用样本数。

## 一个必须说清的局限

方案要的是"**零误报**且全拦截"，那需要**人工标注哪些函数真的超支**。
本机没有这份标注，所以这里只能报**通过率曲线**，
不能声称找到了"零误报"区间。**不假装有标注。**

## 怎么跑

    python tools/exp_s3.py            # 读 文档/设计/归档/实验-2026-11/s2.csv
"""
import csv
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SRC = os.path.join(ROOT, "文档", "设计", "归档", "实验-2026-11", "s2.csv")
OUT = os.path.join(ROOT, "文档", "设计", "归档", "实验-2026-11", "s3.csv")
THRESHOLDS = (1.0, 1.25, 1.5, 2.0, 3.0, 5.0, 10.0)


def main() -> int:
    if not os.path.exists(SRC):
        print(f"✗ 缺 {SRC}：先跑 tools/exp_s2.py", file=sys.stderr)
        return 2
    with open(SRC, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    usable = [r for r in rows
              if r.get("tightness") and r.get("suspect", "False") != "True"]
    print(f"  可用样本 {len(usable)} / {len(rows)}（排除可疑行）")
    print(f"  {'阈值':<8}{'放行':<6}{'通过率':<10}")
    out = []
    for t in THRESHOLDS:
        ok = sum(1 for r in usable if float(r["tightness"]) <= t)
        rate = ok / len(usable) if usable else 0
        out.append({"threshold": t, "passed": ok, "total": len(usable), "rate": round(rate, 3)})
        print(f"  {t:<8}{ok:<6}{rate:<10.1%}")
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["threshold", "passed", "total", "rate"])
        w.writeheader()
        w.writerows(out)
    print(f"\n  数据 → {os.path.relpath(OUT, ROOT)}（{len(out)} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
