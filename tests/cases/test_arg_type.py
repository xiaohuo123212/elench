#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_arg_type.py —— 实参类型检查必须真的跑（E16）

## 这条守的是一个**接线** bug，不是缺功能

`typecheck.py` 里早就有 `E-Type-ArgMismatch`，但 `dsh._diagnose_all` 只在
用户**显式传了 `.eln`** 时把它喂给类型检查器。`elench check <模块>` 这种
最常见的用法（实现文件自动发现）传的是 `ai=None`，于是：

    `_ch = None` → 类型检查只看 `.tu` → **实参类型从来没检查过**

结果：`g("abc")` 传进 `u64` 形参，`check` 一声不吭。

所以这里**必须走 `_diagnose_all` 并传 `ai=None`**——直接调 `typecheck()` 是测不出来的
（它本来就好使）。
"""
import os
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from dsh import _diagnose_all       # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


TU = """spec g(u64 n) -> u64 {
    effects {};
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

BAD = """u64 g(u64 n) { return n + 1; }

u64 f(const vector<u64>& xs) {
    u64 a = g("abc");
    return a + xs.length();
}
"""

GOOD = """u64 g(u64 n) { return n + 1; }

u64 f(const vector<u64>& xs) {
    return g(xs.length());
}
"""


def run(eln_src):
    d = tempfile.mkdtemp()
    tu_p, eln_p = os.path.join(d, "m.tu"), os.path.join(d, "m.eln")
    with open(tu_p, "w", encoding="utf-8") as fh:
        fh.write(TU)
    with open(eln_p, "w", encoding="utf-8") as fh:
        fh.write(eln_src)
    # **关键：ai=None**，模拟 `elench check m.tu`（实现自动发现）
    return _diagnose_all(tu_p, None, strict=False, overflow="off", relax=[])


print("[1] 实参传错类型 → E-Type-ArgMismatch（走自动发现这条路）")
codes = [d.code for d in run(BAD).diagnostics]
check("报了 E-Type-ArgMismatch", "E-Type-ArgMismatch" in codes, codes[:5])

print("[2] 类型对 → 不报")
codes = [d.code for d in run(GOOD).diagnostics]
check("没有 E-Type-ArgMismatch", "E-Type-ArgMismatch" not in codes, codes[:5])

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
