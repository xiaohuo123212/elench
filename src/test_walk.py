#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

"""test_walk.py —— 双值域 kind 覆盖（主批 A–D Step 2）

**这个测试要防的事**：某一边漏了一个 AST kind，走兜底分支，
成本**静默**变成 0，而上界不成立。

三类"没有域处理"的 kind 必须各有归属，**否则测试失败**：

| 类别 | 含义 | 在哪里登记 |
|---|---|---|
| **故意忽略** | 不携带成本信息、不包裹可执行子节点 | `walk.IGNORED[kind] = 理由` |
| **解析器支持但实现未支持** | 语言里有这个语法，但两个域都没实现 | `walk.UNIMPLEMENTED[kind] = 缺口号` |
| **单侧实现** | 只在一个域里处理 | `walk.EXPECTED_ONLY` 锁定（改动即报警） |
"""
import sys

import walk

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
    check(not u["stmt"], "语句层无孤儿", str(u["stmt"]))
    check(not u["expr"], "表达式层无孤儿", str(u["expr"]))


def test_asymmetry_locked():
    print("[4] 单侧实现的清单**锁定**（变了就报警——这正是漂移）")
    r = walk.report()
    for layer in ("stmt", "expr"):
        got_i = set(r["only"][layer]["interp"])
        got_c = set(r["only"][layer]["cost"])
        exp = walk.EXPECTED_ONLY[layer]
        check(got_i == set(exp["interp"]),
              f"{layer}: 只在 interp 的清单未变",
              f"新增 {sorted(got_i - set(exp['interp']))} 消失 {sorted(set(exp['interp']) - got_i)}")
        check(got_c == set(exp["cost"]),
              f"{layer}: 只在 cost 的清单未变",
              f"新增 {sorted(got_c - set(exp['cost']))} 消失 {sorted(set(exp['cost']) - got_c)}")


def test_both_nonempty():
    print("[5] 两个域都真的处理了东西（防空转）")
    r = walk.report()
    for (dom, layer), ks in r["table"].items():
        check(len(ks) >= 5, f"{dom}/{layer} 处理的 kind 数 ≥ 5", f"{len(ks)}")


def main():
    test_reasons()
    test_unimplemented()
    test_no_orphan()
    test_asymmetry_locked()
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
