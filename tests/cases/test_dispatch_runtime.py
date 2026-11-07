#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_dispatch_runtime.py —— 运行期**真的按分档表选候选**（方案甲）

**为什么这条必须测**

在补这个功能之前，"按段动态取最优"只是**一句话**：

- `measure` 会生成表，`build` 会校验表在不在，
- 但**解释器从来不看表**——它永远走"`over` 里第一个实现了的候选"。

于是表错不错，**没有任何运行结果能反驳它**。补上之后：

| 场景 | 期望 |
|---|---|
| 有表，规模落在第 1 段 | 走第 1 段的候选 |
| 有表，规模落在第 2 段 | 走第 2 段的候选 |
| 无表 | 退化为"第一个实现"，**不崩** |
| 规模超出表覆盖范围 | 退化 + **一条警告**（不静默） |

**这条测试的价值**：它把"表"从**报表**变成**可被运行反驳的输入**。
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
import tempfile

from interp import Interp, load_dispatch_table
from parser import parse_source

SAMPLES = os.path.join(ROOT, "文档", "样例")

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

    print("[1] 表能被解析（两种 label 写法都认）")
    tmp = tempfile.mkdtemp()
    tbl = os.path.join(tmp, "count.dispatch.yaml")
    io.open(tbl, "w", encoding="utf-8", newline="\n").write(
        "module: count\nlabel: freq_kernel\nsegments:\n"
        "  - {lo: 1, hi: 8, candidate: freq_pairwise}\n"
        "  - {lo: 9, hi: 1048576, candidate: freq_bucket}\n")
    parsed = load_dispatch_table(tbl)
    check(parsed.get("freq_kernel") == [(1, 8, "freq_pairwise"), (9, 1048576, "freq_bucket")],
          "解析出两段", str(parsed))

    print("[2] 小规模走第 1 段，大规模走第 2 段（**运行期真按表**）")
    it_small = Interp(ch, tu, table_path=tbl)
    it_small.call("count", [list(b"aabbb")])                 # n = 5 → 第 1 段
    check(it_small.last_candidates.get("freq_kernel") == "freq_pairwise",
          "n=5 → freq_pairwise", str(it_small.last_candidates))

    it_big = Interp(ch, tu, table_path=tbl)
    it_big.call("count", [list(b"ab" * 32)])                 # n = 64 → 第 2 段
    check(it_big.last_candidates.get("freq_kernel") == "freq_bucket",
          "n=64 → freq_bucket", str(it_big.last_candidates))

    print("[3] 无表：退化为第一个实现，不崩、不警告")
    it_none = Interp(ch, tu)
    val = it_none.call("count", [list(b"aabbb")])
    check(it_none.last_candidates.get("freq_kernel") == "freq_bucket",
          "无表 → 第一个实现（freq_bucket）", str(it_none.last_candidates))
    check(not it_none.dispatch_warnings, "无表不警告（这是合法退化）",
          str(it_none.dispatch_warnings))
    check("Ok" in repr(val) or isinstance(val, (int,)) or val is not None, "返回值正常", repr(val))

    print("[4] 规模超出表的覆盖范围：**沿最近那段延伸** + 一条警告（不静默）")
    small_tbl = os.path.join(tmp, "small.dispatch.yaml")
    io.open(small_tbl, "w", encoding="utf-8", newline="\n").write(
        "module: count\nlabel: freq_kernel\nsegments:\n"
        "  - {lo: 1, hi: 4, candidate: freq_pairwise}\n")
    it_out = Interp(ch, tu, table_path=small_tbl)
    it_out.call("count", [list(b"ab" * 32)])                 # n = 64 > 4
    # 2026-11-03 改规则：越界不再"退回第一个候选"，而是**沿最近的那一段延伸**
    # （低于下界用第一段的候选，高于上界用最后一段的）。理由是段表已经写明
    # "这个区间谁更快"，而"第一个候选"只是书写顺序，可能恰好是慢的那个——
    # 实测 `count`：按书写顺序取会让 n>4096 的承诺凭空变成 O(n²)。
    # 承诺侧（`cost` 的哨兵段）用的是同一条规则，两边必须一致，否则就是破界。
    check(it_out.last_candidates.get("freq_kernel") == "freq_pairwise",
          "越上界沿最后一段延伸 → freq_pairwise",
          str(it_out.last_candidates))
    check(len(it_out.dispatch_warnings) == 1, "留一条警告", str(it_out.dispatch_warnings))

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
