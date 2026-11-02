#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_dispatch_correct.py —— **"?! 报告说跑哪个"必须等于"实际跑哪个"**。

背景（临时计划 §9，P0）：`_dispatch` 先按分档表选中候选，
紧跟的 `over` 顺序循环却**无条件覆盖**它 —— 于是：
    last_candidates 记 "sum_by_iter"，实际执行 "sum_by_index"。
表现是 `sum.average` 的 κ 与 ops 余量恰好为 0（两个错误相抵）。

本测试用"同一候选跑两遍"的等价性来验证：
  ① 默认跑一次 → 记下 last_candidates 说的候选 L 与 ops；
  ② 用 `force={label: L}` 再跑一次 → ops 必须**完全相等**。
若 ① 实际跑的不是 L，两次 ops 必然不同（本仓库里 139 vs 55）。
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

import io
import os
import sys


from interp import Interp, ops_enable, ops_reset, ops_of  # noqa: E402
from parser import parse_source  # noqa: E402

ROOT = os.path.dirname(HERE)
_ok, _bad = 0, []


def check(cond, name, detail=""):
    global _ok
    if cond:
        _ok += 1
        print(f"  OK   {name}")
    else:
        _bad.append(name)
        print(f"  FAIL {name}  {detail}")


def load(name):
    d = os.path.join(ROOT, "文档", "样例")
    tu = parse_source(io.open(os.path.join(d, name + ".tu"), encoding="utf-8").read(),
                      name + ".tu").module
    ch = parse_source(io.open(os.path.join(d, name + ".eln"), encoding="utf-8").read(),
                      name + ".eln").module
    return tu, ch


def run(tu, ch, fn, args, force=None):
    ops_enable(True)
    ops_reset()
    it = Interp(ch, tu)
    val = it.call(fn, args, force)
    return val, ops_of(), dict(it.last_candidates)


def main() -> int:
    print("[1] sum.average：默认路径 == 它自己报告的候选")
    tu, ch = load("sum")
    data = [list(range(1, 9))]
    v1, o1, lc1 = run(tu, ch, "average", data)
    label = next(iter(lc1))
    reported = lc1[label]
    v2, o2, lc2 = run(tu, ch, "average", data, {label: reported})
    check(v1 == v2, "两次结果相同", f"{v1} vs {v2}")
    check(o1 == o2, "两次 ops 相同（说明报告的候选就是实际跑的）",
          f"默认={o1} 强制{reported}={o2}；报告={lc1}")
    check(lc2.get(label) == reported, "强制的候选被如实记录", str(lc2))

    print("[2] 强制另一个候选：ops 应当不同（证明 ops 对候选敏感）")
    all_c = [k for k in ("sum_by_iter", "sum_by_index")]
    other = next((c for c in all_c if c != reported), None)
    if other:
        v3, o3, lc3 = run(tu, ch, "average", data, {label: other})
        check(v3 == v1, "换候选结果不变（这是契约，不是 bug）", f"{v3}")
        check(o3 != o1, "换候选 ops 不同（计数确实跟着实际执行走）",
              f"{other}={o3} vs {reported}={o1}")
    else:
        check(True, "只有一个候选，跳过")

    print()
    if _bad:
        print(f"失败 {len(_bad)} / {_ok + len(_bad)}")
        for b in _bad:
            print("  -", b)
        return 1
    print(f"全部通过：{_ok} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
