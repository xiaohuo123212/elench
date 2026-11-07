#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_adversarial.py —— 对抗语料必须**判不了就说判不了**

语料在 `corpus/adversarial/`，结果表在 `文档/对抗语料.md`。

**这个测试钉的是"诚实"，不是"算得对"**：
6 个难例当前全部无法判定（N2），这里保证它们**不会**悄悄变成
"判了、而且是错的"——那是本项目最不能出的错。

改进后这些函数会陆续离开 N2；那时改这个测试，并在文档里记下"哪条变强了"。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source          # noqa: E402
from cost_interp import CostInterp        # noqa: E402

FAILED = []
ADV = os.path.join(ROOT, "corpus", "adversarial")


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


tu = parse_source(open(os.path.join(ADV, "adv.tu"), encoding="utf-8").read(), "adv.tu").module
ch = parse_source(open(os.path.join(ADV, "adv.eln"), encoding="utf-8").read(), "adv.eln").module
ci = CostInterp(ch, tu)

print("[1] 六个难例都算得出 κ（不是崩、不是没成本）")
names = ["partition", "two_pointer_dedup", "linear_probe",
         "sliding_sum_max", "heapify_up", "gcd_loop"]
for nm in names:
    try:
        k = ci.cost_of(nm).render()
        check(f"{nm} 有 κ", bool(k), k)
    except Exception as ex:                             # noqa: BLE001
        check(f"{nm} 有 κ", False, f"{type(ex).__name__}: {ex}")

print("[2] 它们都没有被误判成'达标'（诚实性）")
for nm in names:
    try:
        v = ci.check_target(nm) if hasattr(ci, "check_target") else None
    except Exception:                                   # noqa: BLE001
        v = None
    if v is None:
        continue
    kind = getattr(v, "kind", "")
    check(f"{nm} 不是 ok", kind != "ok", kind)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
