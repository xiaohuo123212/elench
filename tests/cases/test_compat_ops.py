#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_compat_ops.py —— 运算符重载（兼容层第 21 条）。

## 为什么要**改写**而不是"允许通过"

κ 是按语法结构数出来的。只放行 `a < b` 的话，它会被当成一次比较（常数成本），
而用户写的 `operator<` 里可能有一整个循环——那样 κ 就不再是上界，整套东西的
意义就没了。所以这里把 `a < b` **改写成** `P__op_lt(a, b)`，κ 自然把被调函数的
成本算进去。

判据（硬要求）：`a < b` 的 κ 与显式写 `less(a, b)` 的 κ **逐字符相同**。

## 两种声明形态都要支持

- 自由函数：`bool operator<(const P& x, const P& y) { … }`
- 成员函数：`bool operator<(const P& o) const { … }`（降级成 `P__op_lt(recv, o)`）

两者落到**同一个名字**（`P__op_lt`），所以等价性才好比。
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


def kappa_of(out: str, fn: str) -> str:
    for line in out.split("\n"):
        m = re.search(r"·\s*" + re.escape(fn) + r"\s+κ ≤ (.+?)\s*$", line.strip())
        if m:
            return m.group(1).strip()
    return ""


def main() -> int:
    BODY = ("bool operator<(const P& x, const P& y) {\n"
            "    u64 i = 0;\n"
            "    u64 s = 0;\n"
            "    while (i < x.a) { s = s + 1; i = i + 1; }\n"
            "    return s > y.a;\n}\n")
    tmp = tempfile.mkdtemp(prefix="elench_ops_")

    def write(name: str, text: str) -> str:
        p = os.path.join(tmp, name)
        io.open(p, "w", encoding="utf-8", newline="").write(text)
        return p

    # ① 自由函数形态：`p < q` 与 `less(p, q)` 的 κ 必须逐字符相同
    # 重载体里放一个**循环**：这样"κ 里含被调函数成本"是看得见的（`n(x.a)`），
    # 而不是"恰好等于某个常数"——后者证明不了改写真的发生了。
    write("free.eln", "struct P { u64 a; };\n\n" + BODY +
                      "\nu64 f(const P& p, const P& q) {\n    if (p < q) {\n        return 1;\n    }\n    return 0;\n}\n")
    write("hand.eln", "struct P { u64 a; };\n\n" + BODY.replace("operator<", "less") +
                      "\nu64 f(const P& p, const P& q) {\n    if (less(p, q)) {\n        return 1;\n    }\n    return 0;\n}\n")
    rc, out = run(["check", "free.eln", "--compat"], tmp)
    check(rc == 0, "自由函数 operator< 能过", out.strip().split("\n")[0][:70])
    _, o_op = run(["cost", "free.eln", "--compat"], tmp)
    _, o_hd = run(["cost", "hand.eln", "--compat"], tmp)
    k_op, k_hd = kappa_of(o_op, "f"), kappa_of(o_hd, "f")
    check(k_op == k_hd and bool(k_op),
          "`p < q` 的 κ == `less(p, q)` 的 κ（逐字符）", "%r vs %r" % (k_op, k_hd))
    check("n(" in k_op, "κ 里确实含被调函数的成本（不是常数 1）", k_op[:60])

    # ② 成员函数形态（`const` 成员 → 接收者是 `const P&`）
    # 成员形态用**同一个循环体**，否则两个 κ 不可比（那是"测了两件事"）。
    write("mem.eln", "class P {\n    u64 a;\n"
                     "    bool operator<(const P& o) const {\n"
                     "        u64 i = 0;\n        u64 s = 0;\n"
                     "        while (i < a) { s = s + 1; i = i + 1; }\n"
                     "        return s > o.a;\n    }\n};\n\n"
                     "u64 f(const P& p, const P& q) {\n    if (p < q) {\n        return 1;\n    }\n    return 0;\n}\n")
    rc, out = run(["check", "mem.eln", "--compat"], tmp)
    check(rc == 0, "成员 operator<（const）能过", out.strip().split("\n")[0][:70])
    _, o_mem = run(["cost", "mem.eln", "--compat"], tmp)
    check(kappa_of(o_mem, "f") == k_op,
          "成员形态与自由形态的 κ 相同（落到同一个 `P__op_lt`）",
          "%r vs %r" % (kappa_of(o_mem, "f"), k_op))

    # ③ 其它运算符
    CASES = [
        ("operator==", "struct P { u64 a; };\nbool operator==(const P& x, const P& y) {\n    return x.a == y.a;\n}\n"
                       "u64 f(const P& p, const P& q) {\n    if (p == q) { return 1; }\n    return 0;\n}\n"),
        ("operator[]", "struct V { u64 a; };\nu64 operator[](const V& v, u64 i) {\n    return v.a + i;\n}\n"
                       "u64 f(const V& v) {\n    return v[2];\n}\n"),
        ("operator+", "struct V { u64 a; };\nV operator+(const V& x, const V& y) {\n    return V{x.a + y.a};\n}\n"
                      "u64 f(const V& p, const V& q) {\n    V r = p + q;\n    return r.a;\n}\n"),
        ("operator!=", "struct P { u64 a; };\nbool operator!=(const P& x, const P& y) {\n    return x.a != y.a;\n}\n"
                       "u64 f(const P& p, const P& q) {\n    if (p != q) { return 1; }\n    return 0;\n}\n"),
    ]
    for label, src in CASES:
        write("c.eln", src)
        rc, out = run(["check", "c.eln", "--compat"], tmp)
        check(rc == 0, "compat 通过：" + label, out.strip().split("\n")[0][:70])

    # ④ 严格模式：一字不改地拒绝（默认行为不变）
    rc, out = run(["check", "free.eln"], tmp)
    check(rc != 0 and "E-Parse-UnexpectedTop" in out,
          "严格模式仍然拒绝 `operator<`（不放宽默认）", out.strip().split("\n")[0][:60])

    # ⑤ 标量比较不受影响（没有对应重载就原样留着）
    write("scalar.eln", "u64 f(u64 a, u64 b) {\n    if (a < b) {\n        return 1;\n    }\n    return 0;\n}\n")
    rc, out = run(["check", "scalar.eln", "--compat"], tmp)
    check(rc == 0, "标量 `a < b` 不被改写、照常通过", out.strip().split("\n")[0][:60])

    print()
    print("  %s  通过 %d，失败 %d" % ("全部通过" if _failed == 0 else "有失败", _passed, _failed))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
