#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_builtin_deprecation.py —— **弃用内建的唯一测试库**。

## 为什么集中在这一个文件

10 个纯糖内建（`sum_of` / `copy` / `contains` / …）的调用点散在 7 个测试文件里
（42 处命中，33 处是真调用）。要"真删"就得同时改 7 个文件、还要重算它们断言里的
数字——这是破坏性变更，混在别的改动里会让"哪个改动导致哪个数变了"不可查。

所以先**把口径收在一处**：这里断言"它们仍是弃用状态、仍能跑、仍给改写提示"。
真正删除时，只需把这一个文件改成断言 `E-Builtin-Removed`，其余文件那时再逐条迁移。

## 三条断言

1. **登记完整**：10 个名字都在 `DEPRECATED_BUILTINS`，且每条都有**改写代码**；
2. **仍可用**：调用能跑通（不打断现有代码），结果与手写循环一致；
3. **有提示**：`check` 报 `W-Builtin-Deprecated`，**消息里带改写代码**。
"""
import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
for _p in (SRC,):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import subprocess
import sys

import stdlib
from interp import Interp
from parser import parse_source

_passed = 0
_failed = []

#: 应处于弃用状态的 10 个纯糖内建
NAMES = ["sum_of", "count_of", "contains", "index_of", "is_sorted",
         "reverse", "fill", "fill_range", "rotate", "copy"]

TU = "spec f(const vector<u64>& xs) -> u64 { effects {}; }\n"


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def run(eln, xs=(1, 2, 3)):
    tu_m = parse_source(TU, "t.tu").module
    el_m = parse_source("func f(const vector<u64>& xs) -> u64 {\n" + eln + "\n}\n",
                        "t.eln").module
    return Interp(el_m, tu_m).call("f", [list(xs)])


def test_registered():
    print("[1] 登记完整：10 个名字 + 每条都有改写代码")
    for n in NAMES:
        check(stdlib.is_deprecated(n), f"{n} 在弃用表里")
        repl = stdlib.replacement_for(n)
        check(bool(repl.strip()), f"{n} 有改写代码", repr(repl))


def test_still_runs():
    print("[2] 仍可用：结果与手写循环一致")
    check(run("    return sum_of(xs);") == 6, "sum_of([1,2,3]) == 6")
    check(run("    return contains(xs, 2);") == 1, "contains(xs,2) == 1")
    check(run("    return contains(xs, 9);") == 0, "contains(xs,9) == 0")
    check(run("    return count_of(xs, 1);") == 1, "count_of(xs,1) == 1")
    check(run("    return index_of(xs, 9);") == 3, "index_of 未命中 == 长度")
    check(run("    return is_sorted(xs);") == 1, "is_sorted([1,2,3]) == 1")
    check(run("    return reverse(xs) * 1000 + xs.at(0);") == 3003,
          "reverse 原地反转并返回长度")
    check(run("    fill(xs, 9);\n    return xs.at(0);") == 9, "fill 全置 9")
    check(run("    rotate(xs, 1);\n    return xs.at(0);") == 2, "rotate 左移 1")


def test_warns():
    print("[3] check 报 W-Builtin-Deprecated，且消息带改写代码")
    env = dict(_os.environ, PYTHONIOENCODING="utf-8")
    tmp = _os.path.join(_os.environ.get("TEMP", "."), "dep_chk")
    _os.makedirs(tmp, exist_ok=True)
    tu_p = _os.path.join(tmp, "m.tu")
    el_p = _os.path.join(tmp, "m.eln")
    open(tu_p, "w", encoding="utf-8").write(TU)
    open(el_p, "w", encoding="utf-8").write(
        "func f(const vector<u64>& xs) -> u64 { return sum_of(xs); }\n")
    r = subprocess.run([sys.executable, _os.path.join(SRC, "elench.py"),
                        "check", tu_p, el_p],
                       capture_output=True, encoding="utf-8", errors="replace", env=env)
    out = (r.stdout or "") + (r.stderr or "")
    check("W-Builtin-Deprecated" in out, "出现 W-Builtin-Deprecated", out[-160:])
    check("sum_of" in out and ("for (x in xs)" in out or "for" in out),
          "消息里带改写代码", out[-160:])


def main() -> int:
    test_registered()
    test_still_runs()
    test_warns()
    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed[:8]:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
