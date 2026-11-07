#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_effects_cost.py —— 成本表里的**副作用分列**（⑤ Quasar 改造版）。

## 统计什么、不统计什么

**统计**：函数声明了哪些 effect；每个 effect 有几处**调用点**、分别调了谁；
κ 里有哪些 `ext(…)` 外部项。

**不统计**：把解释器操作按 effect 分摊。一次解释器操作不属于任何副作用，
"40% 算 fs、60% 算 io"是编数——那会让报表看起来更精细，其实更假。
"""
import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import subprocess
import sys

from cost_interp import CostInterp
from parser import parse_source

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


TU = """
extern read_file(const vector<u8>& p) -> u64 from "ucrtbase" effects { fs, io };

spec load(const vector<u8>& p) -> u64 {
    effects { fs };
    target { time: { max: O(p.length()) } };
}

spec pure_one(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) } };
}
"""

ELN = """
func load(const vector<u8>& p) -> u64 {
    return read_file(p) + read_file(p);
}

func pure_one(const vector<u64>& xs) -> u64 {
    return xs.length();
}
"""


def make():
    tu = parse_source(TU, "t.tu").module
    eln = parse_source(ELN, "t.eln").module
    return CostInterp(eln, tu)


def test_declared():
    print("[1] 声明的 effects")
    ci = make()
    ea = ci.effect_attribution("load")
    check(ea["declared"] == ["fs"], "load 声明了 fs", str(ea["declared"]))
    ea2 = ci.effect_attribution("pure_one")
    check(ea2["declared"] == [], "pure_one 没有 effect", str(ea2["declared"]))


def test_call_sites():
    print("[2] 调用点归属（按 effect 统计次数）")
    ci = make()
    ea = ci.effect_attribution("load")
    fs = ea["effects"].get("fs")
    io_ = ea["effects"].get("io")
    check(fs is not None and fs["sites"] == 2,
          "两处 read_file → fs×2", str(fs))
    check(io_ is not None and io_["sites"] == 2,
          "同一个调用也记 io×2（一次调用命中多个 effect）", str(io_))
    check(fs and fs["callees"] == ["read_file"], "callees 记下被调名字", str(fs))
    check(ea["ext_terms"] == ["read_file"],
          "κ 里的 ext 项被收集出来", str(ea["ext_terms"]))

    ea2 = ci.effect_attribution("pure_one")
    check(ea2["effects"] == {}, "pure_one 没有调用点", str(ea2["effects"]))
    check(ea2["ext_terms"] == [], "pure_one 没有外部项", str(ea2["ext_terms"]))


def test_cli():
    print("[3] `elench cost` 里有副作用那一行")
    ex = os.path.join(ROOT, "文档", "样例")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, os.path.join(SRC, "elench.py"), "cost",
                        os.path.join(ex, "count.tu")],
                       capture_output=True, encoding="utf-8", errors="replace", env=env)
    out = (r.stdout or "") + (r.stderr or "")
    check(r.returncode == 0, "退出码 0", out[-200:])
    check("副作用：声明" in out, "打印了「副作用：声明」", out[:200])
    check("alloc" in out, "count 的 alloc 出现在那一行里", out[:300])


def main() -> int:
    test_declared()
    test_call_sites()
    test_cli()
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
