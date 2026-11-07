#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""ablation.py —— 复现「分段承诺值多少」那张表（A1-b）。

## 为什么要有脚本，而不是把分档表提交进样例

样例目录**故意不带** `.dispatch.yaml`：表是**测量产物**，提交进仓库就等于
把"某台机器某次测量"钉成事实。所以这里每次**现场生成**表到临时目录：

```
样例 .tu/.eln  →  measure --max-size=1024  →  γ 分档表  →  κ(分段) vs κ(max)
```

跑出来的表应当与 `文档/设计/目前设计/评估/消融-2026-11-02.md` 的 A1-b 一致
（数字会随 κ 改动而变，**这正是要它可复现的原因**）。

```bash
python tools/ablation.py
python tools/ablation.py --n 1024 --md out.md
```
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
SAMPLES = os.path.join(ROOT, "文档", "样例")
ELENCH = os.path.join(SRC, "elench.py")

#: 带 `?!` 的样例：(模块, 函数, 尺寸变量)
PROGS = [("count", "count", "text"), ("sum", "average", "scores"),
         ("search", "binary_search", "xs"), ("stats", "summarize", "xs"),
         ("topk", "topk", "xs")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1024, help="分档表覆盖到多大")
    ap.add_argument("--md", default=None, help="写进 markdown 文件")
    args = ap.parse_args()

    sys.path.insert(0, SRC)
    from cost_check import evaluate
    from cost_interp import CostInterp
    from parser import parse_source

    env = dict(os.environ, ELENCH_STEP_BUDGET="30000000",
               PYTHONIOENCODING="utf-8")
    lines = ["| 程序 | n | 分段（默认） | max | 收紧 |", "|---|---|---|---|---|"]
    for name, fn, var in PROGS:
        d = tempfile.mkdtemp(prefix="eln_ab_")
        for ext in (".tu", ".eln"):
            src = os.path.join(SAMPLES, name + ext)
            if os.path.exists(src):
                shutil.copy(src, d)
        subprocess.run([sys.executable, ELENCH, "measure", name,
                        f"--max-size={args.n}"],
                       capture_output=True, cwd=d, env=env)
        tbl = os.path.join(d, name + ".dispatch.yaml")
        if not os.path.exists(tbl):
            lines.append(f"| `{name}.{fn}` | — | 表生成失败 | — | — |")
            continue
        tu = parse_source(open(os.path.join(d, name + ".tu"), encoding="utf-8").read(),
                          name + ".tu").module
        el = parse_source(open(os.path.join(d, name + ".eln"), encoding="utf-8").read(),
                          name + ".eln").module
        seg = CostInterp(el, tu, table_path=tbl)
        mx = CostInterp(el, tu, table_path=None)
        for n in (64, 256, args.n):
            try:
                ks = evaluate(seg.cost_of(fn), {var: n})
                km = evaluate(mx.cost_of(fn), {var: n})
            except Exception as exc:                   # noqa: BLE001
                lines.append(f"| `{name}.{fn}` | {n} | {type(exc).__name__} | — | — |")
                continue
            ratio = (km / ks) if ks else 0
            lines.append(f"| `{name}.{fn}` | {n} | {ks:,.0f} | {km:,.0f} | {ratio:,.2f}× |")

    md = "\n".join(lines)
    print(md)
    if args.md:
        open(args.md, "w", encoding="utf-8", newline="\n").write(md + "\n")
        print(f"\n已写出：{args.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
