#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

"""test_walk.py —— 双值域 kind 覆盖的**登记簿**检查（漂移检查在 `test_drift.py`）

**这个测试要防的事**：某一边漏了一个 AST kind，走兜底分支，
成本**静默**变成 0，而上界不成立。

两类"没有域处理"的 kind 必须各有归属，**否则测试失败**：

| 类别 | 含义 | 在哪里登记 |
|---|---|---|
| **故意忽略** | 不携带成本信息、不包裹可执行子节点 | `walk.IGNORED[kind] = 理由` |
| **解析器支持但实现未支持** | 语言里有这个语法，但两个域都没实现 | `walk.UNIMPLEMENTED[kind] = 缺口号` |

**单侧实现（漂移）不再挂白名单**：`walk.EXPECTED_ONLY` 已删除，
现在要求两侧 kind 集合**完全相等**，由 `test_drift.py` 把关。
一张只会加条的白名单，早晚会变成谎话——所以它被删掉了，而不是被维护。
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
import sys


import walk  # noqa: E402

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


def test_reasons():
    print("[1] 每个「故意忽略」都要有理由")
    for k, why in walk.IGNORED.items():
        check(bool(why and why.strip()), f"IGNORED[{k}] 有理由", repr(why))


def test_unimplemented():
    print("[2] 「解析器支持但实现未支持」要登记到缺口")
    for k, why in walk.UNIMPLEMENTED.items():
        check(bool(why and why.strip()), f"UNIMPLEMENTED[{k}] 有缺口号", repr(why))


def test_no_orphan():
    print("[3] 没有「既没处理也没登记」的 kind")
    u = walk.unaccounted()
    check(not u, "无孤儿 kind", str(u))


def test_no_whitelist():
    print("[4] 白名单已删除（漂移必须为零，不能靠登记簿放行）")
    check(not hasattr(walk, "EXPECTED_ONLY"),
          "walk.EXPECTED_ONLY 不存在（不允许恢复白名单机制）")
    check(walk.asymmetry() == {}, "两值域没有单侧 kind", str(walk.asymmetry()))


def test_both_nonempty():
    print("[5] 两个域都真的处理了东西（防空转）")
    r = walk.report()
    for (dom, layer), ks in r["table"].items():
        check(len(ks) >= 5, f"{dom}/{layer} 处理的 kind 数 ≥ 5", f"{len(ks)}")


def main():
    test_reasons()
    test_unimplemented()
    test_no_orphan()
    test_no_whitelist()
    test_both_nonempty()

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
