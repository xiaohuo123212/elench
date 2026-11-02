#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_fuzz_smoke.py —— 让检测器**每次 runner 都跑一点**，避免"写了没人跑"。

20 个随机程序，要求：
1. 编译通过率 ≥ 60%（生成器不能生成一堆废代码）；
2. **反例 = 0**——一旦出现就是真 bug（`ops > κ`），这条测试立刻红，
   并打印最小复现（缩小由 `tests/fuzz/shrink.py` 负责，CI 里不做以省时间）。
"""
import os as _os

_os.environ.setdefault("ELENCH_COUNT_OPS", "1")

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
FUZZ = _os.path.join(ROOT, "tests", "fuzz")
for _p in (SRC, FUZZ):
    if _p not in __import__("sys").path:
        __import__("sys").path.insert(0, _p)

import sys  # noqa: E402

import gen  # noqa: E402
from check import judge  # noqa: E402
from parser import parse_source  # noqa: E402

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


def test_shrinker_mechanics():
    """缩小器的机械行为：只要"还是反例"，无关行就被删掉。"""
    import shrink

    fake = ("func f(const Vec<u64>& xs, u64 n) -> u64 {\n"
            "    u64 s = 0;\n"
            "    u64 KEEP = 1;\n"
            "    s = s + KEEP;\n"
            "    return s;\n}\n")
    orig = shrink._is_ce
    shrink._is_ce = lambda tu, src, sizes: "KEEP = 1;" in src
    try:
        out = shrink.shrink("", fake, [2])
    finally:
        shrink._is_ce = orig
    check("KEEP = 1;" in out and "u64 s = 0;" not in out,
          "缩小器删掉无关行、保留关键行", out)


def main() -> int:
    n = 20
    sizes = [2, 4]
    compiled = 0
    ces = []
    unmeasured = 0
    for seed in range(n):
        tu_src, eln_src, family = gen.gen_program(seed)
        tu_r = parse_source(tu_src, "fuzz.tu")
        el_r = parse_source(eln_src, "fuzz.eln")
        if tu_r.diagnostics or el_r.diagnostics:
            continue
        compiled += 1
        bad, rows = judge(tu_src, eln_src, sizes, family)
        if not rows:
            unmeasured += 1
        if bad:
            ces.append((seed, rows, eln_src))

    test_shrinker_mechanics()
    check(compiled >= int(n * 0.6), f"编译通过率 ≥ 60%（{compiled}/{n}）", str(compiled))
    check(unmeasured <= int(n * 0.2),
          f"测不出来的不超过 20%（{unmeasured}/{n}）——它不算通过",
          "超过就说明有族根本没被覆盖")
    check(not ces, f"随机程序里没有 ops > κ（{n} 个）",
          f"反例 {len(ces)} 个")
    if ces:
        seed, rows, src = ces[0]
        print(f"    首个反例 seed={seed}：{rows}")
        print(src)

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
