#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_dispatch_closure.py —— 闭环：**按表跑** 与 **逐候选强跑** 必须一致

**为什么这条是"闭环"而不是"又一个测试"**

`measure` 说：n ≥ 12 走 `freq_bucket`，n < 12 走 `freq_pairwise`，表就这么落盘。
如果运行期不读表，这句话**没有任何东西能验证**——表错了也不会有人知道。

现在运行期读表了，于是可以做一件之前做不到的事：

1. 造一张**故意分成两段**的表；
2. 让解释器**按表跑**，记录它实际走了谁（`last_candidates`）；
3. 再**强跑**（`force={label: 候选}`）同一个候选，比较**返回值**；
4. 两者必须一致 —— 否则"按表选"就把程序语义改了。

**这条测试一旦红，说明分档表不是"优化"而是"改变了行为"**，那是绝对不能发生的事。
"""
import io
import os
import sys
import tempfile

from interp import Interp
from parser import parse_source

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SAMPLES = os.path.join(ROOT, "文档", "设计", "目前设计", "样例")

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


def load(name):
    tu = parse_source(io.open(os.path.join(SAMPLES, name + ".tu"), encoding="utf-8").read(),
                      name + ".tu").module
    ch = parse_source(io.open(os.path.join(SAMPLES, name + ".eln"), encoding="utf-8").read(),
                      name + ".eln").module
    return tu, ch


def main() -> int:
    tu, ch = load("count")
    tmp = tempfile.mkdtemp()
    tbl = os.path.join(tmp, "count.dispatch.yaml")
    io.open(tbl, "w", encoding="utf-8", newline="\n").write(
        "module: count\nlabel: freq_kernel\nsegments:\n"
        "  - {lo: 1, hi: 8, candidate: freq_pairwise}\n"
        "  - {lo: 9, hi: 1048576, candidate: freq_bucket}\n")

    cases = [(5, [ord(c) for c in "aabbb"]), (64, [ord(c) for c in "ab" * 32])]

    print("[1] 按表跑：实际走的候选 = 表里那一段")
    for n, data in cases:
        it = Interp(ch, tu, table_path=tbl)
        it.call("count", [data])
        want = "freq_pairwise" if n <= 8 else "freq_bucket"
        check(it.last_candidates.get("freq_kernel") == want,
              f"n={n} → {want}", str(it.last_candidates))

    print("[2] 闭环：按表跑的结果 == 强跑同一候选的结果（否则改的是语义，不是优化）")
    for n, data in cases:
        it_tbl = Interp(ch, tu, table_path=tbl)
        v_tbl = it_tbl.call("count", [data])
        who = it_tbl.last_candidates.get("freq_kernel")
        it_force = Interp(ch, tu)
        v_force = it_force.call("count", [data], force={"freq_kernel": who})
        check(str(v_tbl) == str(v_force), f"n={n} 结果一致（{who}）",
              f"{v_tbl!r} vs {v_force!r}")

    print("[3] 两个候选在这两个规模上确实都返回同一个答案（表分段不影响语义）")
    for n, data in cases:
        vals = set()
        for who in ("freq_pairwise", "freq_bucket"):
            it = Interp(ch, tu)
            vals.add(str(it.call("count", [data], force={"freq_kernel": who})))
        check(len(vals) == 1, f"n={n} 两候选结果相同", str(vals))

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
