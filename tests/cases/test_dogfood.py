#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_dogfood.py —— **吃自己的狗粮**：10 个样例必须被本工具链检查通过。

论文里写"我们的样例全部由本工具链检查通过"——这句话以前只是**说法**，
没有机器证据。这条测试就是那个证据：遍历 `文档/样例/*.tu`，逐个
`check`（无 error）+ `cost`（无债务），任何一条不满足都失败。

## 为什么留一个例外

`search.binary_search` 目前有一条**已知债务**（target 是 `O(log n)`，而承诺里含
线性候选的项）。它是真的、不是回归——所以列进 `KNOWN_DEBT` 并写明原因，
**不允许**在别处悄悄新增。
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

import glob
import os
import sys

from checker import check_module
from cost_check import compare
from cost_interp import CostInterp
from parser import parse_source

_passed = 0
_failed = []

#: 已知债务：程序名 → 允许有债务的函数。**只增不减地写在这里，不许在别处冒出来。**
KNOWN_DEBT = {
    "search": {"binary_search"},
}


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def load(name):
    base = os.path.join(ROOT, "文档", "样例")
    tu_src = open(os.path.join(base, name + ".tu"), encoding="utf-8").read()
    eln_src = open(os.path.join(base, name + ".eln"), encoding="utf-8").read()
    return tu_src, eln_src


def main() -> int:
    base = os.path.join(ROOT, "文档", "样例")
    names = sorted(os.path.basename(p)[:-3]
                   for p in glob.glob(os.path.join(base, "*.tu")))
    check(len(names) >= 10, f"样例数 ≥ 10（实际 {len(names)}）", str(len(names)))

    total_err = 0
    unexpected = []
    for name in names:
        tu_src, eln_src = load(name)
        r = check_module(tu_src, eln_src, name + ".tu")
        errs = [d for d in r.diagnostics if d.severity == "error"]
        total_err += len(errs)
        if errs:
            print(f"    {name}: {[d.code for d in errs]}")

        tu = parse_source(tu_src, name + ".tu").module
        eln = parse_source(eln_src, name + ".eln").module
        ci = CostInterp(eln, tu)
        debts = set()
        for fn in ci.fns:
            try:
                if compare(ci.cost_of(fn), ci.target_cost(fn)).kind == "debt":
                    debts.add(fn)
            except Exception:                            # noqa: BLE001
                debts.add(fn)
        extra = debts - KNOWN_DEBT.get(name, set())
        if extra:
            unexpected.append(f"{name}: {sorted(extra)}")

    check(total_err == 0, f"{len(names)} 个样例 check 无 error", str(total_err))
    check(not unexpected, "没有超出已知清单的债务", "; ".join(unexpected))
    print(f"  （已知债务：{ {k: sorted(v) for k, v in KNOWN_DEBT.items()} }）")

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
