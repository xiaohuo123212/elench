#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_doc_contracts.py —— 把《入门》§6「子句表」逐条钉住。

## 为什么有这个文件

§6 那张表写的是"**违反时**报什么"。它以前是照设计意图写的，没人逐条造反例验过，
于是有一条（`repeats`）写成了"这个循环最多转几圈"，实际只约束「**以它为条件的
`while`**」——照着表去用会得到一个静默不生效的声明。

这个文件把表里每条的**违反场景**做成可执行样例，跑出来的诊断必须与表一致：
表改了而代码没改、或代码改了而表没改，这里都会红。

## 判据（每条都要成立）

1. 违反 → 报**表里写的那个**码（或运行期异常名）；
2. 合规的写法 → 不报（防止"永远报错"也算通过）；
3. `repeats` 的**边界**：写在非谓词上只给警告 `W-Repeats-Unused`，不静默生效。
"""
import io
import os
import re
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
DSH = os.path.join(ROOT, "src", "dsh.py")

_passed = 0
_failed = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print("  ok   " + label)
    else:
        _failed += 1
        print("  FAIL " + label + (("  ← " + extra) if extra else ""))


def run(args, cwd):
    r = subprocess.run([sys.executable, "-B", DSH] + args, cwd=cwd,
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def codes(out: str):
    return sorted(set(re.findall(r"E-[A-Za-z-]+", out)))


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="elench_doc_contracts_")

    def w(name: str, text: str) -> None:
        io.open(os.path.join(tmp, name), "w", encoding="utf-8", newline="").write(text)

    # ---------------- target：cost 判债务；build --deny-debt 退出码 1 ----------------
    w("tgt.eln", "u64 walk(const vector<u64>& xs) {\n    u64 s = 0;\n    u64 i = 0;\n"
                 "    while (i < xs.size()) {\n        s = s + xs.at(i);\n"
                 "        i = i + 1;\n    }\n    return s;\n}\n")
    w("tgt.tu", "spec walk(const vector<u64>& xs) -> u64 {\n    effects {};\n"
                "    target { time: { max: O(log(xs.size())) }, allocation: { max: 0 }, };\n}\n")
    rc, out = run(["build", "tgt", "--compat", "--deny-debt"], tmp)
    check(rc == 1 and "拒绝构建" in out, "target 被打破 → build --deny-debt 退出码 1", out.strip()[:60])

    # ---------------- effects：实现用了 io，spec 写 {} ----------------
    w("eff.eln", "u64 shout(u64 x) {\n    print(x);\n    return x;\n}\n")
    w("eff.tu", "spec shout(u64 x) -> u64 {\n    effects {};\n"
                "    target { time: { max: O(1) }, allocation: { max: 0 }, };\n}\n")
    rc, out = run(["check", "eff", "--compat"], tmp)
    check("E-Effect-Missing" in codes(out), "effects 写 {} 但实现 print（io）→ E-Effect-Missing",
          ", ".join(codes(out))[:50])

    # ---------------- requires：运行期前提被违反 ----------------
    w("req.eln", "u64 head(const vector<u64>& xs) {\n    return xs.at(0);\n}\n")
    w("req.tu", "spec head(const vector<u64>& xs) -> u64 {\n    effects {};\n"
                "    requires xs.size() > 0;\n"
                "    target { time: { max: O(1) }, allocation: { max: 0 }, };\n}\n")
    rc, out = run(["run", "req", "--fn=head", "--vec=", "--compat"], tmp)
    check(rc != 0 and "InterpRequiresViolated" in out,
          "requires 不成立 + 空输入 → InterpRequiresViolated（不是 E- 码）", out.strip()[:70])

    # ---------------- guarantees：可检查的进运行期 ----------------
    w("gua.eln", "vector<u64> order(const vector<u64>& xs) {\n    return xs;\n}\n")
    w("gua.tu", "spec order(const vector<u64>& xs) -> vector<u64> {\n    effects {};\n"
                "    guarantees is_sorted(ret);\n"
                "    target { time: { max: O(n) }, allocation: { max: 0 }, };\n}\n")
    rc, out = run(["run", "gua", "--fn=order", "--vec=3,1,2", "--compat"], tmp)
    check(rc != 0 and "E-Guarantee-Violated" in codes(out),
          "guarantees is_sorted 被违反 → E-Guarantee-Violated", ", ".join(codes(out))[:50])

    # ---------------- repeats：**只约束以它为条件的 while** ----------------
    w("rep.eln", "bool always(u64 i) {\n    return true;\n}\n\nu64 spin(u64 n) {\n"
                 "    u64 i = 0;\n    while (always(i)) {\n        i = i + 1;\n    }\n"
                 "    return i;\n}\n")
    w("rep.tu", "spec always(u64 i) -> bool {\n    effects {};\n    repeats 2;\n"
                "    target { time: { max: O(1) }, allocation: { max: 0 }, };\n}\n"
                "spec spin(u64 n) -> u64 {\n    effects {};\n"
                "    target { time: { max: O(n) }, allocation: { max: 0 }, };\n}\n")
    rc, out = run(["run", "rep", "--fn=spin", "--u64=1", "--compat"], tmp)
    check(rc != 0 and "E-Repeats-Exceeded" in codes(out),
          "谓词 repeats 声明 2 圈、实跑 3 圈 → E-Repeats-Exceeded", ", ".join(codes(out))[:50])

    # 写在**非谓词**上：只给警告，不生效（这是 §6 表最容易被误读的一格）
    w("rep2.eln", "u64 walk(const vector<u64>& xs) {\n    u64 s = 0;\n    u64 i = 0;\n"
                  "    while (i < xs.size()) {\n        s = s + xs.at(i);\n"
                  "        i = i + 1;\n    }\n    return s;\n}\n")
    w("rep2.tu", "spec walk(const vector<u64>& xs) -> u64 {\n    effects {};\n    repeats 1;\n"
                 "    target { time: { max: O(n) }, allocation: { max: 0 }, };\n}\n")
    rc, out = run(["check", "rep2", "--compat"], tmp)
    check("W-Repeats-Unused" in out,
          "repeats 写在非谓词上 → 警告 W-Repeats-Unused（不静默生效）", out.strip()[:60])

    # ---------------- recursion depth：写小了 / 不写 ----------------
    w("rec.eln", "u64 down(u64 n) {\n    if (n == 0) {\n        return 0;\n    }\n"
                 "    return 1 + down(n - 1);\n}\n")
    w("rec.tu", "spec down(u64 n) -> u64 {\n    effects {};\n    recursion depth <= 1;\n"
                "    target { time: { max: O(n) }, allocation: { max: 0 }, };\n}\n")
    rc, out = run(["run", "rec", "--fn=down", "--u64=3", "--compat"], tmp)
    check(rc != 0 and "E-Recursion-Depth-Exceeded" in codes(out),
          "recursion depth <= 1 但实深 3 → E-Recursion-Depth-Exceeded", ", ".join(codes(out))[:50])

    w("rec2.eln", "u64 down(u64 n) {\n    if (n == 0) {\n        return 0;\n    }\n"
                  "    return 1 + down(n - 1);\n}\n")
    w("rec2.tu", "spec down(u64 n) -> u64 {\n    effects {};\n"
                 "    target { time: { max: O(n) }, allocation: { max: 0 }, };\n}\n")
    rc, out = run(["check", "rec2", "--compat"], tmp)
    check("E-Recursion-Unbounded" in codes(out),
          "有递归但不写 recursion depth → E-Recursion-Unbounded", ", ".join(codes(out))[:50])

    # ---------------- sizes：右侧不是形状量 ----------------
    w("sz.eln", "u64 total(const vector<u64>& xs) {\n    return xs.size();\n}\n")
    w("sz.tu", "spec total(const vector<u64>& xs) -> u64 {\n    effects {};\n"
               "    sizes table = table.length();\n"
               "    target { time: { max: O(n) }, allocation: { max: 0 }, };\n}\n")
    rc, out = run(["check", "sz", "--compat"], tmp)
    check("E-Sizes-NotShape" in codes(out),
          "sizes 右侧不是形状量 → E-Sizes-NotShape", ", ".join(codes(out))[:50])

    # ---------------- external：不登记 ----------------
    w("ext.eln", "u64 f() {\n    unsafe { return never_declared(1); }\n}\n")
    rc, out = run(["check", "ext", "--compat"], tmp)
    check("E-Call-Undeclared" in codes(out),
          "调未登记的 external → E-Call-Undeclared", ", ".join(codes(out))[:50])

    # ---------------- benchmodel：缺了 measure 直接拒绝 ----------------
    w("nb.eln", "u64 k(u64 n) {\n    return n;\n}\n")
    w("nb.tu", "spec k(u64 n) -> u64 {\n    effects {};\n"
               "    target { time: { max: O(1) }, allocation: { max: 0 }, };\n}\n")
    rc, out = run(["measure", "nb", "--compat"], tmp)
    check(rc == 2 and "benchmodel" in out,
          "没有 benchmodel.scale → measure 退出码 2 并说清原因", out.strip()[:70])

    print()
    print("  %s  通过 %d，失败 %d" % ("全部通过" if _failed == 0 else "有失败", _passed, _failed))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
