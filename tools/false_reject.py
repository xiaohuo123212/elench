#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""false_reject.py —— 门禁的**误拒率 / 漏判率**，用生成程序量出来。

## 为什么要它

`--deny-debt` 只有两种判错方式：

| 判错 | 名字 | 后果 |
|---|---|---|
| 该放行的被拒 | **误拒**（false rejection） | 作者被迫改真代码去迎合工具 |
| 该拒的放行 | **漏判**（false acceptance） | 契约变成装饰，主张失效 |

两种都能量：**生成程序时我们知道它的真实成本阶**，于是"正确 target"和"过紧 target"
都能自动造出来，不需要人肉标注。

## 语料

四族模板 × 随机变体（语句数、是否加 `if`、内层界是常数还是 n）：

| 族 | 真实阶 |
|---|---|
| 直线赋值 | O(1) |
| 单层循环（界 = n） | O(n) |
| 双层循环（内层常数界） | O(n) |
| 双层循环（两层都是 n） | O(n²) |

每族 × 3 档 target（**正好 / 松一阶 / 紧一阶**）⇒ 该放行的必须放行，该拒的必须拒。

## 用法

```bash
python tools/false_reject.py --n 20            # 默认 20 个变体
python tools/false_reject.py --n 20 --relax=R1 # 放开一条限制后再量
```
"""
from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import tempfile
from typing import List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
ELENCH = os.path.join(ROOT, "src", "elench.py")

#: 真实阶 → target 表达式（参数名统一用 xs）
ORDER = {1: "O(1)", 2: "O(xs.length())", 3: "O(xs.length() * xs.length())"}


def gen(seed: int) -> Tuple[str, str, int]:
    """生成一个程序 → (tu, eln, 真实阶)。阶用 1/2/3 表示 O(1)/O(n)/O(n²)。"""
    rnd = random.Random(seed)
    kind = seed % 4
    extra = rnd.choice(["", "if", "assign"])
    body: List[str] = ["    u64 s = 0;"]

    if kind == 0:                                  # 直线：O(1)
        order = 1
        for _ in range(rnd.randint(1, 3)):
            body.append("    s = s + 1;")
        if extra == "if":
            body.append("    if (s > 2) { s = s - 1; }")
    elif kind == 1:                                # 单层循环：O(n)
        order = 2
        body += ["    u64 i = 0;",
                 "    while (i < xs.length()) {",
                 "        s = s + 1;",
                 "        i = i + 1;",
                 "    }"]
    elif kind == 2:                                # 双层，内层常数界：O(n)
        order = 2
        body += ["    u64 i = 0;",
                 "    while (i < xs.length()) {",
                 "        u64 j = 0;",
                 "        while (j < 8) {",
                 "            s = s + 1;",
                 "            j = j + 1;",
                 "        }",
                 "        i = i + 1;",
                 "    }"]
    else:                                          # 双层都用 n：O(n²)
        order = 3
        body += ["    u64 i = 0;",
                 "    while (i < xs.length()) {",
                 "        u64 j = 0;",
                 "        while (j < xs.length()) {",
                 "            s = s + 1;",
                 "            j = j + 1;",
                 "        }",
                 "        i = i + 1;",
                 "    }"]

    body.append("    return s;")
    eln = ("func f(const Vec<u64>& xs) -> u64 {\n" + "\n".join(body) + "\n}\n")
    return "", eln, order


def tu_for(order: int) -> str:
    return ("spec f(const Vec<u64>& xs) -> u64 {\n"
            "    effects {};\n"
            f"    target {{ time: {{ max: {ORDER[order]} }}, }};\n"
            "}\n")


def run_build(d: str, flags: List[str]) -> int:
    r = subprocess.run([sys.executable, ELENCH, "build", "m"] + flags,
                       capture_output=True, encoding="utf-8",
                       errors="replace", cwd=d)
    return r.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="变体数（每个变体测 3 档 target）")
    ap.add_argument("--relax", default=None, help="传给 build 的 --relax（如 R1 或 R1,R3）")
    args = ap.parse_args()

    flags = ["--deny-debt"]
    if args.relax:
        flags.append(f"--relax={args.relax}")

    fp = fn = ok = 0
    fp_cases: List[str] = []
    fn_cases: List[str] = []

    for seed in range(args.n):
        _, eln, order = gen(seed)
        for delta, label in ((-1, "紧一阶"), (0, "正好"), (1, "松一阶")):
            tgt_order = max(1, min(3, order + delta))
            if delta == -1 and order == 1:
                continue                      # O(1) 再紧无处可紧
            expect_pass = (tgt_order >= order)
            d = tempfile.mkdtemp(prefix="eln_fr_")
            with open(os.path.join(d, "m.tu"), "w", encoding="utf-8") as f:
                f.write(tu_for(tgt_order))
            with open(os.path.join(d, "m.eln"), "w", encoding="utf-8") as f:
                f.write(eln)
            rc = run_build(d, flags)
            got_pass = (rc == 0)
            if got_pass == expect_pass:
                ok += 1
            elif expect_pass and not got_pass:
                fp += 1
                fp_cases.append(f"seed={seed} 真实 O(·)^{order} target {ORDER[tgt_order]}（{label}）")
            else:
                fn += 1
                fn_cases.append(f"seed={seed} 真实 O(·)^{order} target {ORDER[tgt_order]}（{label}）")

    total = ok + fp + fn
    print(f"语料：{args.n} 个变体 × 3 档 target（跳过 O(1) 的'紧一阶'）")
    print(f"开关：{' '.join(flags)}")
    print(f"总计 {total} 例：**判对 {ok}**，误拒 {fp}，漏判 {fn}")
    if fp_cases:
        print("\n误拒（该放行却拒绝）：")
        for c in fp_cases[:6]:
            print("  -", c)
    if fn_cases:
        print("\n漏判（该拒绝却放行）：")
        for c in fn_cases[:6]:
            print("  -", c)
    return 0 if fp == 0 and fn == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
