#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""shrink.py —— 把反例缩到最小：**整行删除，只要还是反例就留下**。

为什么只删行、不重写语句：反例的价值是"最小复现"，而**行**是作者能读的单位。
更聪明的缩小（换字面量、简化条件）等 v3；先把"20 行缩到 3 行"做出来。
"""
from __future__ import annotations

import os
import sys
from typing import List

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(ROOT, "src")
for _p in (SRC, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _is_ce(tu_src: str, eln_src: str, sizes: List[int]) -> bool:
    from check import judge
    bad, _rows = judge(tu_src, eln_src, sizes)
    return bad


def shrink(tu_src: str, eln_src: str, sizes: List[int]) -> str:
    """贪心删行：从后往前，删掉还能保持"是反例"的行。"""
    if not _is_ce(tu_src, eln_src, sizes):
        return eln_src
    lines = eln_src.split("\n")
    # 只尝试删"函数体内部"的行（保留签名与结尾）
    changed = True
    while changed:
        changed = False
        for i in range(len(lines) - 1, 0, -1):
            if not lines[i].strip():
                continue
            cand = lines[:i] + lines[i + 1:]
            cand_src = "\n".join(cand)
            if _is_ce(tu_src, cand_src, sizes):
                lines = cand
                changed = True
                break
    return "\n".join(lines)


def main() -> int:
    import argparse
    import io

    ap = argparse.ArgumentParser()
    ap.add_argument("tu")
    ap.add_argument("eln")
    ap.add_argument("--sizes", default="2,4")
    args = ap.parse_args()
    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
    tu_src = io.open(args.tu, encoding="utf-8").read()
    eln_src = io.open(args.eln, encoding="utf-8").read()
    out = shrink(tu_src, eln_src, sizes)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
