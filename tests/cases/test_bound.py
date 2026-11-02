#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_bound.py —— G29：`while` 上界提取测试

运行：
  cd src; $env:PYTHONIOENCODING="utf-8"; python test_bound.py
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

import sys

sys.stdout.reconfigure(encoding="utf-8")

from bound import Bound, bound_of_while, expr_to_bound
from parser import parse_source

_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def mod_of(src):
    r = parse_source(src, "t.eln")
    check(not r.diagnostics, "样例能解析",
          "; ".join(d.code for d in r.diagnostics))
    return r.module


def _desc(n, kind, acc=None):
    if acc is None:
        acc = []
    if n.kind == kind:
        acc.append(n)
    for c in n.children:
        _desc(c, kind, acc)
    return acc


def upper(src, label=None):
    """跑一个函数，返回每个 while 的 (上界文本 or '⊤', 原因)。"""
    mod = mod_of(src)

    def repeats_of(lb):
        if lb == "in_range":
            return Bound("log2", left=Bound("sub", left=Bound("var", value="hi"),
                                            right=Bound("var", value="lo")))
        return None

    fn = _desc(mod, "fn")[0]
    out = []
    for w in _desc(fn, "while"):
        b, why = bound_of_while(w, repeats_of, fn)
        out.append((b.render() if b else "⊤", why))
    return out


def test_size_compare():
    print("[1] 来源①：尺寸比较")
    r = upper("func f(const Vec<u64>& xs) -> u64 { u64 i = 0; "
              "while (i < xs.length()) { i = i + 1; } return i; }")
    check(r[0][0] == "xs.length()", "i < xs.length() → xs.length()", str(r))

    r = upper("func f(const Vec<u64>& xs) -> u64 { const u64 n = xs.length(); u64 i = 0; "
              "while (i < n) { i = i + 1; } return i; }")
    check(r[0][0] == "xs.length()",
          "i < n（const 绑定）→ 展开回 xs.length()（报告里不该出现局部变量名）", str(r))


def test_zero_init():
    print("[2] 计数器初值为 0 → 不再写 e - i")
    r = upper("func f(const Vec<u64>& xs) -> u64 { u64 i = 0; "
              "while (i < 10) { i = i + 1; } return i; }")
    check(r[0][0] == "10", "i 从 0 起 → 上界 10（不是 10 - i）", str(r))

    # 初值不是 0 时保留减法
    r = upper("func f(const Vec<u64>& xs) -> u64 { u64 i = 5; "
              "while (i < xs.length()) { i = i + 1; } return i; }")
    check(r[0][0] == "(xs.length() - i)", "i 从 5 起 → xs.length() - i", str(r))


def test_and():
    print("[3] and 组合取 min")
    r = upper("func f(const Vec<u64>& xs) -> u64 { const u64 n = xs.length(); u64 i = 0; "
              "while (i < n and i < xs.length()) { i = i + 1; } return i; }")
    check("min(" in r[0][0], "and → min", str(r))


def test_predicate():
    print("[4] 来源②：谓词的 repeats")
    r = upper("func g(const Vec<u64>& xs) -> u64 { u64 lo = 0; u64 hi = xs.length(); "
              "while (in_range(lo, hi)) { lo = lo + 1; } return lo; }")
    check(r[0][0] == "log2((hi - lo))", "谓词 repeats 代入", str(r))


def test_unprovable():
    print("[5] 来源③：抽不出时明确返回 None")
    r = upper("func f(const Vec<u64>& xs) -> u64 { u64 i = 0; "
              "while (i < unknown_pred(i)) { i = i + 1; } return i; }")
    check(r[0][0] == "⊤", "抽不出 → ⊤（绝不静默猜）", str(r))
    check("repeats" in r[0][1], "原因里指出 repeats 这条出路", str(r[0][1]))


def test_no_silence():
    print("[6] 抽不出时必须给出原因，不静默")
    cases = [
        "while (i < unknown_pred(i)) { i = i + 1; }",
        "while (a * b < 10) { a = a + 1; }",
    ]
    for c in cases:
        r = upper("func f(const Vec<u64>& xs) -> u64 { u64 i = 0; u64 a = 0; u64 b = 0; "
                  + c + " return i; }")
        b, why = r[0]
        check(b == "⊤" and bool(why), f"抽不出有原因：{c[:20]}", f"{b} / {why}")


def test_loop_progresses():
    """E21 回归：循环体必须能推进计数器，否则不许给上界。

    这是**健全性**回归，不是风格检查：`while (j > 0) { s = s + 1; }` 是死循环，
    真实成本 ∞，而修这个 bug 之前它拿到了 `O(n)`。
    """
    print("[7] E21：循环必须能推进")
    from bound import progresses, _descendants

    def prog(src):
        mod = parse_source(src, "t.eln").module
        ws = _descendants(mod, "while")
        return progresses(ws[0])

    def fn(body):
        return ("func f(u64 n, const Vec<u64>& q) -> u64! {\n"
                "    u64 s = 0;\n    " + body + "\n    return s;\n}")

    cases = [
        # (说明, 循环体, 期望能不能推进)
        ("计数式推进",            "u64 i = 0; while (i < n) { s = s + 1; i = i + 1; }", True),
        ("倒数式推进",            "u64 j = n; while (j > 0) { s = s + 1; j = j - 1; }", True),
        ("双指针都动",            "u64 a = 0; u64 b = n; while (a < b) { a = a + 1; b = b - 1; }", True),
        ("容器 pop",              "while (q.length() > 0) { q.pop(); }", True),
        ("按引用传给被调函数",     "while (q.length() > 0) { t(& q); }", True),
        ("死循环：计数器不动",     "u64 j = n; while (j > 0) { s = s + 1; }", False),
        ("死循环：计数式不动",     "u64 i = 0; while (i < n) { s = s + 1; }", False),
        ("死循环：容器只读",       "while (q.length() > 0) { s = s + 1; }", False),
        # **按值传不算推进**——这条区分是故意的：`f(i)` 改不了 i。
        ("按值传不算推进",         "u64 i = 0; while (i < n) { s = s + g(i); }", False),
    ]
    for label, body, want in cases:
        ok, why = prog(fn(body))
        check(ok == want, f"{label} → 可推进={want}", f"得到 {ok}（{why}）")

    # 死循环在**成本侧**必须变成 ⊤（不给有限上界）
    from cost_interp import CostInterp
    dead = fn("u64 j = n; while (j > 0) { s = s + 1; }")
    k = CostInterp(parse_source(dead, "t.eln").module, None).cost_of("f").render()
    check("⊤" in k, "死循环的 κ 含 ⊤", k)


def main():
    test_size_compare()
    test_zero_init()
    test_and()
    test_predicate()
    test_unprovable()
    test_no_silence()
    test_loop_progresses()

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
