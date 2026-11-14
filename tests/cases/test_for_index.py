#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_for_index.py —— `for (… in …)` **已删除**（2026-11-10 夜）

## 为什么这份文件还在、但内容反了

它原来测的是"带下标的 `for (i, x in xs)`（批次 C）"。2026-11-10 夜用户定：
`for (x in xs)` / `for (i, x in xs)` / `for (i in 0..n)` 三种写法**与 C++
range-for 重合**，整个删除，`..` 区间一并删。

**删除功能的唯一补偿是报错教替代写法**。所以这份文件现在守三件事：

1. 三种写法**都必须**报 `E-For-InRemoved`（不是普通 `E-Parse-Expected`）；
2. 一条源码只留**一条**诊断（头与体先吃掉，不级联）；
3. 消息里**三种替代写法都在**：`for (auto& x : xs)` / `for (auto x : xs)` /
   `for (u64 i = 0; i < n; i = i + 1)`；
4. `..` 不再是运算符（`1..3` 不能被解析成区间表达式）；
5. 替代写法本身通过：range-for 与三段式都能解析、能跑、κ 仍是 `n(xs)`。

**没有删断言**：原来的 4 条验收（能跑 / κ / i 类型 / 区间）改成了"对应写法
现在必须报什么"——同一批输入，判据从"语义正确"换成"删除后的诊断"。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source          # noqa: E402
from interp import Interp                 # noqa: E402
from cost_interp import CostInterp        # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


#: 三种被删写法 + 各自的"旧语义"。输入不变，判据变成"必须报 E-For-InRemoved"。
REMOVED = [
    ("for (x in xs)",
     "u64 f(const vector<u64>& xs) {\n"
     "    u64 s = 0;\n"
     "    for (x in xs) { s = s + x; }\n"
     "    return s;\n}\n"),
    ("for (i, x in xs)",
     "u64 f(const vector<u64>& xs) {\n"
     "    u64 s = 0;\n"
     "    for (i, x in xs) { s = s + x * i; }\n"
     "    return s;\n}\n"),
    ("for (i in 0..n)",
     "u64 f(u64 n) {\n"
     "    u64 s = 0;\n"
     "    for (i in 0..n) { s = s + i; }\n"
     "    return s;\n}\n"),
]

print("[1] 三种 `in` 写法都必须报 E-For-InRemoved（而不是普通解析错误）")
for label, src in REMOVED:
    r = parse_source(src, "a.eln")
    codes = [d.code for d in r.diagnostics]
    errs = [c for c in codes if c.startswith("E-")]
    check(f"`{label}` → E-For-InRemoved", "E-For-InRemoved" in codes, codes)
    check(f"`{label}` 只留一条 E-（不级联）", len(errs) == 1, codes)

print("[2] 消息里三种替代写法都在（删除的唯一补偿是教怎么写）")
msg = " ".join(d.message for d in parse_source(REMOVED[0][1], "a.eln").diagnostics)
check("消息含 `for (auto& x : xs)`", "for (auto& x : xs)" in msg, msg[:240])
check("消息含 `for (auto x : xs)`", "for (auto x : xs)" in msg, msg[:240])
check("消息含三段式下标写法", "for (u64 i = 0; i < n; i = i + 1)" in msg, msg[:240])
check("消息含 `xs.at(i)`", "xs.at(i)" in msg, msg[:240])
check("消息说清「与 C++ range-for 重合」", "C++ range-for" in msg, msg[:240])

print("[3] `..` 不再是运算符")
r_dotdot = parse_source("u64 f(u64 n) { u64 x = 1..3; return x; }\n", "d.eln")
check("`1..3` 不再解析成区间（报 E-）",
      any(d.code.startswith("E-") for d in r_dotdot.diagnostics),
      [d.code for d in r_dotdot.diagnostics])
check("没有 `binary(..)` 节点",
      not any(getattr(c, "kind", "") == "binary" and (c.value or "") == ".."
              for c in (r_dotdot.module.children if r_dotdot.module else [])),
      "")

print("[4] 替代写法本身能通过：range-for 能跑、κ 仍是 n(xs)")
TU = """spec g(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec h(u64 n) -> u64 {
    effects {};
    target { time: { max: O(n) }, allocation: { max: 0 }, };
}
"""
PLAIN = """u64 g(const vector<u64>& xs) {
    u64 s = 0;
    for (auto x : xs) {
        s = s + x;
    }
    return s;
}
"""
THREE = """u64 h(u64 n) {
    u64 s = 0;
    for (u64 i = 0; i < n; i = i + 1) {
        s = s + i;
    }
    return s;
}
"""
r_plain = parse_source(PLAIN, "p.eln")
check("`for (auto x : xs)` 零诊断", not [d for d in r_plain.diagnostics
                                          if d.code.startswith("E-")],
      [d.code for d in r_plain.diagnostics])
it = Interp(r_plain.module, None)
check("`for (auto x : xs)` 结果 = 6", it.call("g", [[1, 2, 3]]) == 6, it.call("g", [[1, 2, 3]]))

r_three = parse_source(THREE, "t.eln")
check("三段式零诊断", not [d for d in r_three.diagnostics if d.code.startswith("E-")],
      [d.code for d in r_three.diagnostics])
it3 = Interp(r_three.module, None)
check("三段式 0+1+2+3 = 6", it3.call("h", [4]) == 6, it3.call("h", [4]))

ci = CostInterp(parse_source(PLAIN + THREE, "a.eln").module,
                parse_source(TU, "a.tu").module)
k_plain = ci.cost_of("g").render()
check("range-for 的 κ 含 n(xs)", "n(xs)" in k_plain, k_plain)
check("range-for 判 ok", ci.verdict_of("g", "time").kind == "ok"
      if hasattr(ci, "verdict_of") else True, "")

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
