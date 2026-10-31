#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""defect_report.py —— **注入缺陷的检出率报告**（workshop 的 RQ1b 用）

## 它回答什么

> 静态检查能在**执行之前**拦住多少类缺陷？

取 `test_invalid.py` 里的用例（**A 词法 / B 语法 / C 契约与检查 / D 语义 / V 版本** 五类），
按类各取若干条，逐条给出：**期望诊断码 → 实际诊断码 → 是否命中**，
并统计**每类检出率**。

## 用法

    python tools/defect_report.py            # 打印 markdown 表
    python tools/defect_report.py --json     # 机器可读

**为什么不是"随便挑 20 个"**：按类分层抽样，每类都覆盖，
读者能看到"哪一类缺陷我们抓得住、哪一类抓不住"。
"""
import json
import os
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(R, "src"))

import test_invalid                        # noqa: E402
from checker import check_module           # noqa: E402

CLASSES = {
    "A": "词法（未闭合、非法转义…）",
    "B": "语法（缺括号、旧关键字…）",
    "C": "契约与检查（effects / target / 借用…）",
    "D": "语义（类型、返回、调用…）",
    "V": "版本与兼容（已下线写法…）",
}
PER_CLASS = 4          # 每类取几条（5 类 × 4 = 20 条）


def pick() -> list:
    """按类分层，每类取前 PER_CLASS 条（顺序稳定，可复现）。"""
    buckets = {k: [] for k in CLASSES}
    for c in test_invalid.CASES:
        name = c["name"]
        prefix = name[:1]
        if prefix in buckets and len(buckets[prefix]) < PER_CLASS:
            buckets[prefix].append(c)
    out = []
    for k in CLASSES:
        out.extend(buckets[k])
    return out


def run_one(c) -> dict:
    tu = c["tu"] or (c.get("code") or "")
    ai = c["ai"]
    res = check_module(tu, ai, "case", strict=c["strict"])
    got = [d.code for d in res.diagnostics]
    exp = c["expect"] or ""
    hit = bool(exp) and any(g == exp or g.startswith(exp) for g in got)
    return {
        "class": c["name"][:1],
        "name": c["name"],
        "expect": exp or "(任意 E-)",
        "got": got[0] if got else "-",
        "hit": hit or (not exp and bool(got)),
    }


def main() -> int:
    rows = [run_one(c) for c in pick()]
    total = len(rows)
    hits = sum(1 for r in rows if r["hit"])

    if "--json" in sys.argv:
        print(json.dumps({"total": total, "hits": hits, "rows": rows},
                         ensure_ascii=False, indent=2))
        return 0 if hits == total else 1

    print(f"### 注入缺陷检出率（{hits}/{total}）")
    print()
    print("| 类 | 缺陷 | 期望诊断码 | 实际诊断码 | 拦截 |")
    print("|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['class']} | {r['name'][4:] if len(r['name']) > 4 else r['name']} "
              f"| `{r['expect']}` | `{r['got']}` | {'✅' if r['hit'] else '❌'} |")
    print()
    by_class = {}
    for r in rows:
        k = r["class"]
        a, b = by_class.get(k, (0, 0))
        by_class[k] = (a + (1 if r["hit"] else 0), b + 1)
    print("| 类 | 含义 | 检出 |")
    print("|---|---|---|")
    for k, desc in CLASSES.items():
        if k in by_class:
            a, b = by_class[k]
            print(f"| {k} | {desc} | {a}/{b} |")
    print()
    print(f"**合计：{hits}/{total}**——全部在**执行之前**被拦下（没有运行被检查的程序）。")
    return 0 if hits == total else 1


if __name__ == "__main__":
    sys.exit(main())
