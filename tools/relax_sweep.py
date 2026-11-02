#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""relax_sweep.py —— 「放开一条限制，会多出多少不可证的放行？」

## 这是什么实验

`设计-自由与严谨` 的核心主张：**限制不是风格偏好，是"不撒谎"的代价**。
要证明它，只需量一件事：

> 把某条限制关掉，有多少个**本来会被拒**的程序变成"通过"，而它们的 κ 其实是 ⊤
> （= 工具已经不知道上界，却还是放行了）。

那才是真正的**假阳性**（false acceptance）。

## 六种配置

| 配置 | 含义 |
|---|---|
| `baseline` | 全部限制打开（默认） |
| `R1` | while 圈数可以推不出 |
| `R2` | 递归可以不声明 depth |
| `R3` | 可以不声明 benchmodel/scale |
| `R4` | （v0 无独立检查，开关存在但**不改变任何判定**） |
| `R5` | 分配可以不被建模 |

## 用法

```bash
python tools/relax_sweep.py            # 打印矩阵
python tools/relax_sweep.py --json     # 机读
```
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Dict, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
ELENCH = os.path.join(ROOT, "src", "elench.py")

#: 语料：每个都**故意违反一条限制**，并且**都带 target**（所以"放行"意味着承诺了一个上界）
CORPORA: Dict[str, Tuple[str, str]] = {
    # R1：while 条件不是尺寸比较 ⇒ 圈数推不出
    "C1-while": (
        "spec f(const Vec<u64>& xs) -> u64 {\n"
        "    effects {};\n"
        "    target { time: { max: O(xs.length()) }, };\n"
        "}\n",
        "func f(const Vec<u64>& xs) -> u64 {\n"
        "    u64 s = 0;\n"
        "    bool go = true;\n"
        "    while (go) {\n"
        "        s = s + 1;\n"
        "        if (s >= 4) { go = false; }\n"
        "    }\n"
        "    return s;\n"
        "}\n",
    ),
    # R2：递归但 .tu 没写 recursion depth
    "C2-recursion": (
        "spec fact(u64 n) -> u64 {\n"
        "    effects {};\n"
        "    target { time: { max: O(n) }, };\n"
        "}\n",
        "func fact(u64 n) -> u64 {\n"
        "    if (n <= 1) { return 1; }\n"
        "    return n * fact(n - 1);\n"
        "}\n",
    ),
    # R5：承诺 0 次分配，实际每轮分配
    "C3-allocation": (
        "spec f(const Vec<u8>& xs) -> u64 {\n"
        "    effects {};\n"
        "    target { time: { max: O(xs.length() * xs.length()) },\n"
        "             allocation: { max: 0 }, };\n"
        "}\n",
        "func f(const Vec<u8>& xs) -> u64 {\n"
        "    u64 i = 0;\n"
        "    u64 s = 0;\n"
        "    while (i < xs.length()) {\n"
        "        Vec<u8> parts = text_join(xs, 44);\n"
        "        s = s + parts.length();\n"
        "        i = i + 1;\n"
        "    }\n"
        "    return s;\n"
        "}\n",
    ),
}

CONFIGS = ["baseline", "R1", "R2", "R3", "R4", "R5"]
FLAGS = ["--deny-debt"]


def run(d: str, extra) -> Tuple[int, str]:
    r = subprocess.run([sys.executable, ELENCH, "build", "m"] + FLAGS + list(extra),
                       capture_output=True, encoding="utf-8",
                       errors="replace", cwd=d)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def kappa_is_top(d: str) -> bool:
    r = subprocess.run([sys.executable, ELENCH, "cost", "m"],
                       capture_output=True, encoding="utf-8",
                       errors="replace", cwd=d)
    return "⊤" in ((r.stdout or "") + (r.stderr or ""))


def main() -> int:
    global FLAGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--deny-unknown", action="store_true",
                    help="再叠 --deny-unknown（判不出也拒绝）")
    args = ap.parse_args()
    if args.deny_unknown:
        FLAGS = FLAGS + ["--deny-unknown"]

    table = {}
    for cname, (tu, eln) in CORPORA.items():
        d = tempfile.mkdtemp(prefix="eln_sw_")
        with open(os.path.join(d, "m.tu"), "w", encoding="utf-8") as f:
            f.write(tu)
        with open(os.path.join(d, "m.eln"), "w", encoding="utf-8") as f:
            f.write(eln)
        row = {}
        for cfg in CONFIGS:
            extra = [] if cfg == "baseline" else [f"--relax={cfg}"]
            rc, _out = run(d, extra)
            accepted = (rc == 0)
            row[cfg] = {
                "accepted": accepted,
                "kappa_top": kappa_is_top(d) if accepted else None,
            }
        table[cname] = row

    if args.json:
        print(json.dumps(table, ensure_ascii=False, indent=2))
        return 0

    print("行 = 故意违规的语料；列 = 配置（baseline + 单条放宽）")
    print("单元 = 通过 / 拒绝（通过时标出 κ 是否为 ⊤）\n")
    head = f"{'语料':<16}" + "".join(f"{c:>16}" for c in CONFIGS)
    print(head)
    print("-" * len(head))
    for cname, row in table.items():
        cells = []
        for cfg in CONFIGS:
            v = row[cfg]
            if not v["accepted"]:
                cells.append("拒绝")
            else:
                cells.append("**通过(⊤)**" if v["kappa_top"] else "通过")
        print(f"{cname:<16}" + "".join(f"{c:>16}" for c in cells))

    bad = sum(1 for cname, row in table.items() for cfg, v in row.items()
              if cfg != "baseline" and v["accepted"] and v["kappa_top"])
    print(f"\n**放开一条限制后「通过但 κ=⊤（不可证）」的格数：{bad}**")
    print("（baseline 一行全拒绝，说明限制确实在挡；放宽后的通过格才是代价）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
