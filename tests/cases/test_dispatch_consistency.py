#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_dispatch_consistency.py —— `dispatch` 与 `export` 对分段的说法必须**逐字一致**

**为什么要有这条测试**

E32 就是这么来的：`export` 自己算了一遍分段，结论和 `dispatch` 相反
（`dispatch` 说 `sum` 不分档，`export` 说 5 之后切到 `sum_by_index`）。

根因有两个，都值得记住：

1. **同一件事写了两遍**——两套代码，必然分叉；
2. **变量名来源不同**——`export` 用 `benchmodel` 的参数名，`dispatch` 用候选成本式里的尺寸变量
   （`_size_var`）。名字对不上，交叉点自然算不一样。

修法是让 `export` 直接复用 `plan.render()` 的原文——**同一件事只留一套措辞**。
这条测试就是防它退回去：**两边一旦有第二种说法，立刻红。**
"""
import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但**这批测试原先住在 `src/`**，里面大量
#: `os.path.join(HERE, "..", "文档")` / `os.path.join(ROOT, "tools")` 都假设
#: 「本文件在 src/、上一级是仓库根」。搬家时改 200 处路径比让 `HERE` 继续指 src 贵得多，
#: 所以保留旧语义：`HERE` = src/，`CASEDIR` = 本文件真正所在的目录。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import re
import subprocess
import sys

DSH = os.path.join(HERE, "dsh.py")
SAMPLES = os.path.join(ROOT, "文档", "样例")

PROGRAMS = ["count", "sum", "search", "topk", "lcs", "matmul",
            "stats", "paren", "calculator", "merge"]

_passed = 0
_failed = []


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def run(args):
    r = subprocess.run([sys.executable, DSH] + args,
                       capture_output=True, text=True, encoding="utf-8")
    return (r.stdout or "") + (r.stderr or "")


def plan_lines(text):
    """只留"分段结论"相关的行：`?!...` 与 `n ∈ [...]`。"""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        # 只比"计划"本身：measure 的诊断（测量失败/警告）不算分段说法
        if "测量失败" in s or "！" in s:
            continue
        if s.startswith("?!") or s.startswith("n ∈"):
            out.append(s)
    return out


def main() -> int:
    print("[1] 每个带 ?! 的样例：dispatch 与 export 的分段逐字一致")
    for b in PROGRAMS:
        tu = os.path.join(SAMPLES, b + ".tu")
        eln = os.path.join(SAMPLES, b + ".eln")
        if not (os.path.exists(tu) and os.path.exists(eln)):
            continue
        d = plan_lines(run(["dispatch", tu, eln]))
        e = plan_lines(run(["export", tu, eln, "--no-auto"]))
        # export 里可能包含 measure 的额外行，但 plan 那几行必须原样出现
        # 用**集合**比：export 的自动链会把同一段计划打印两次（build 一次、自己一次），
        # 我们关心的是"说法是否一致"，不是"打印了几遍"。
        same = sorted(set(d)) == sorted(set(e))
        check(same, f"{b}: 分段说法一致",
              f"dispatch={sorted(set(d))[:3]} / export={sorted(set(e))[:3]}")

    print("[2] sum：dispatch 说「不分档」时，export 也必须这么说（E32 的现场）")
    d = plan_lines(run(["dispatch", os.path.join(SAMPLES, "sum.tu"),
                        os.path.join(SAMPLES, "sum.eln")]))
    e = plan_lines(run(["export", os.path.join(SAMPLES, "sum.tu"),
                        os.path.join(SAMPLES, "sum.eln"), "--no-auto"]))
    if d:
        check(d[0] in e, "sum 的结论一致", f"{d[:1]} vs {e[:1]}")
    else:
        check(True, "sum 没有 ?! 规划（跳过）")

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
