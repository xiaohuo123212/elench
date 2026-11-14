#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_method_return_unknown.py —— **任何容器方法都不许落到 `TY_UNKNOWN`**。

## 为什么要有这条测试

`typecheck.py` 的 `?`（`TY_UNKNOWN`）**与任何类型兼容**——这是"宁可漏报、
不许误报"的设计取舍。它的代价是：**方法分派里漏一个返回类型分支，就等于
那个方法"什么都收"**。这类漏法不会红任何东西，只会让错误的值类型一路
放行到运行期。

`vector.assign` 曾是最后一处缺口，而且前后踩了**两个方向**的坑：

| 形状 | 最初（`?`） | 上一轮（= 接收者类型） | 本轮（= `void`） |
|---|---|---|---|
| `vector<u64> ys = xs.assign(2, 0);` | ✓ 放行 | ✓ 放行（`run` 给 `[0, 0]`） | ✗ `E-Type-Assign`「初值是 void」 |
| `u64 n = xs.assign(2, 0);` | ✓ 放行 | ✗ `E-Type-Assign` | ✗ `E-Type-Assign` |
| `xs.assign(2, 0);` | ✓ | ✓ | ✓（语句用法**必须保持**） |

上一轮把静态类型定成**接收者类型**，解释器也 `return recv`——两边"一致"，
但 **C++ 的 `std::vector::assign` 返回 `void`**：`check` 绿、`dsh cpp` 发射
`std::vector<uint64_t> ys = xs.assign(2, 0);`、g++ 报
`conversion from 'void' to non-scalar type`。这就是"三方不一致"。

本轮的裁决是 **`void`**（`assign` 是"清空后填 n 个 x"的写操作，C++ 语义、
也是它本来的用途）：`interp.py` 的 `assign` 分支 `return None`，
`typecheck.py` 声明 `TY_VOID`，`dsh.py` 在值位置报 `E-Cxx-Unsupported`
（不发射编不过的表达式）。三方口径一致。

## 三层验收

1. **系统性排查（本测试的第 1 节）**：遍历 `typecheck.METHOD_TABLE` 的**每一个**
   (容器, 方法)，断言静态类型**不是** `?`。以后往表里加方法却忘了加分支，
   这里立刻红——而且新增方法必须先在这里补实参模板，不会静默漏测。
2. **`assign` 的正/反向**：静态类型是 `void`、解释器返回 `None`；
   `u64` / `bool` / 任意容器都不能接它的值（`E-Type-Assign`）；语句位置必须仍过。
3. **别的容器没有 `assign`**：`array` / `fixedvec` 定长，`check` 报
   `E-Call-UnknownMethod`（不是"未知所以放行"）。

跑法：`python tests/cases/test_method_return_unknown.py`，退出码 0 即通过。
"""
import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
if SRC not in _sys.path:
    _sys.path.insert(0, SRC)

import sys                                                    # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                             # noqa: BLE001
    pass

from interp import Interp                                     # noqa: E402
from parser import parse_source                               # noqa: E402
import typecheck as _tc                                       # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


# ---------------------------------------------------------------- 工具

def _parse(body_lines, ret="u64"):
    """一段函数体 → `.eln` 模块（不带 `.tu`，与 `dsh check xx.eln` 同口径）。"""
    body = "\n".join("    " + ln for ln in body_lines)
    return parse_source(f"{ret} f() {{\n{body}\n    return 0;\n}}\n",
                        "t.eln").module


def _walk(n, out):
    out.append(n)
    for c in (n.children or []):
        _walk(c, out)
    return out


def static_ty(decls, call):
    """`call` 的**静态类型**（用 typecheck 自己的记录器，不另写推断）。"""
    ch = _parse([decls, f"{call};"])
    rec = _tc._TypeRecorder(None, ch)
    rec.check()
    mname = call.split(".")[1].split("(")[0]
    nodes = [x for x in _walk(ch, []) if x.kind == "method-call" and x.name == mname]
    if not nodes:
        return "（找不到 method-call）"
    return rec.ty_of.get(id(nodes[0]), _tc.TY_UNKNOWN).render()


def codes(decls, stmt):
    """`decls` + 一条语句跑一遍 typecheck，返回诊断码列表。"""
    return [d.code for d in _tc.typecheck(None, _parse([decls, f"{stmt};"]))]


def run_ret(decls, call, ret):
    """解释器**实际**返回：把 `call` 放在 `ret f()` 里返回，取值。"""
    return Interp(_parse([decls, f"return {call};"], ret=ret), None).call("f", [])


def run_after(decls, stmt, tail, ret="u64"):
    """跑 `decls; stmt; return tail;`，取 `tail` 的返回值。

    `assign` 的值是 `void`，不能 `return xs.assign(...)`；要证明它的**副作用**
    只能这样写：先把它当语句执行，再读同一个容器。
    """
    return Interp(_parse([decls, stmt, f"return {tail};"], ret=ret),
                  None).call("f", [])


# ---------------------------------------------------------------- 1. 全表排查

#: 每个容器 → 一节声明 + **方法 → 实参表**。
#:
#: 这是"排查"的可执行版本：`METHOD_TABLE` 里有的名字，这里**必须有**实参模板，
#: 否则第 1 节直接红（新增方法不会被静默漏测）。
RECEIVERS = [
    ("vector", "vector<u64> xs;", "xs", {
        "size": "", "empty": "", "at": "0", "push_back": "1",
        "assign": "2, 0", "pop": "", "find": "0", "insert": "0, 1",
        "erase": "0", "clear": "", "back": "", "front": ""}),
    ("array", "array<u64, 4> xs;", "xs", {
        "size": "", "empty": "", "at": "0", "push_back": "1",
        "pop": "", "find": "0", "insert": "0, 1", "erase": "0",
        "clear": "", "back": "", "front": ""}),
    ("fixedvec", "fixedvec<u64, 4> xs;", "xs", {
        "size": "", "empty": "", "at": "0", "push_back": "1",
        "pop": "", "find": "0", "insert": "0, 1", "erase": "0",
        "clear": "", "back": "", "front": ""}),
    ("map", "map<u64, u64> xs;", "xs", {
        "size": "", "empty": "", "insert": "1, 10", "put": "1, 10",
        "get": "1", "contains": "1", "remove": "1", "erase": "1",
        "clear": "", "keys": ""}),
    ("set", "set<u64> xs;", "xs", {
        "size": "", "empty": "", "insert": "1", "contains": "1",
        "remove": "1", "erase": "1", "clear": "", "values": ""}),
    ("unordered_map", "unordered_map<u64, u64> xs;", "xs", {
        "size": "", "empty": "", "insert": "1, 10", "get": "1",
        "contains": "1", "remove": "1", "erase": "1", "clear": "",
        "keys": "", "values": ""}),
    ("unordered_set", "unordered_set<u64> xs;", "xs", {
        "size": "", "empty": "", "insert": "1", "contains": "1",
        "remove": "1", "erase": "1", "clear": "", "keys": "", "values": ""}),
    ("stack", "stack<u64> xs;", "xs", {
        "size": "", "empty": "", "push": "1", "enqueue": "1", "pop": "",
        "dequeue": "", "top": "", "peek": "", "front": "", "clear": ""}),
    ("queue", "queue<u64> xs;", "xs", {
        "size": "", "empty": "", "push": "1", "enqueue": "1", "pop": "",
        "dequeue": "", "top": "", "peek": "", "front": "", "clear": ""}),
]


def test_no_method_falls_to_unknown():
    print("[1] 全表排查：METHOD_TABLE 里每个 (容器, 方法) 的静态类型都不是 `?`")
    for head, decls, var, args_of in RECEIVERS:
        table = _tc.METHOD_TABLE.get(head, set())
        for meth in sorted(table):
            if meth not in args_of:
                check(f"{head}.{meth} 有实参模板", False,
                      f"METHOD_TABLE 新增了 `{head}.{meth}`，本测试没有实参模板"
                      f"——补 RECEIVERS 再跑（否则这一对没被排查）")
                continue
            ty = static_ty(decls, f"{var}.{meth}({args_of[meth]})")
            check(f"{head}.{meth}() → {ty}", ty not in ("?", "（找不到 method-call）"),
                  f"静态类型是 `{ty}`：方法分派里没有返回类型分支")
    # 反向：METHOD_TABLE 里有、RECEIVERS 里没有的容器（新增容器时会走到）。
    for head in _tc.METHOD_TABLE:
        if head not in [r[0] for r in RECEIVERS]:
            check(f"容器 {head} 有接收者模板", False,
                  "METHOD_TABLE 新增了容器，本测试没有覆盖")


# ---------------------------------------------------------------- 2. assign

def test_assign_static():
    print("[2] `assign` 的静态类型是 void（C++ 的 std::vector::assign 就是 void）")
    for decls in (
        "vector<u64> xs;",
        "vector<u8> xs;",
        'vector<vector<u8>> xs;',
        "vector<bool> xs;",
    ):
        got = static_ty(decls, "xs.assign(2, 0)")
        check(f"{decls} xs.assign(2, 0) → void", got == "void", f"实际 {got}")


def test_assign_runtime():
    print("[2b] 解释器实际返回：**void（None）**，不再是接收者")
    v = run_ret("vector<u64> xs;", "xs.assign(2, 0)", "()")
    check("xs.assign(2, 0) → void（None）", v is None, repr(v))
    check("返回值不是容器（不再 `return recv`）", not isinstance(v, list), repr(v))
    # 副作用仍然是"清空后填 n 个 x"（这是 assign 的用途，不能被本轮改坏）。
    check("assign 真的改了长度（清空后填 2 个）",
          run_after("vector<u64> xs; xs.push_back(9); xs.push_back(8);",
                    "xs.assign(2, 7);", "xs.size()") == 2)
    check("assign(2, 7) 的元素是 7",
          run_after("vector<u64> xs;", "xs.assign(2, 7);", "xs.at(0)") == 7)
    check('vector<vector<u8>>: assign(2, "a") 后每个元素是 [97]',
          run_after('vector<vector<u8>> xs;', 'xs.assign(2, "a");',
                    "xs.at(0).at(0)") == 97)


# ---------------------------------------------------------------- 3. 负例

#: 每条都**必须**报 `E-Type-Assign`（把 void 塞进声明位置）。
NEGATIVE = [
    # **本轮的靶子**：`assign` 返回 void，任何"接它的值"的写法都必须被拒绝。
    ("vector<u64> ys = xs.assign(2, 0);", "vector<u64> xs;",
     "assign 返回 void，不能初始化容器（C++ 的 std::vector::assign 也是 void）"),
    ('vector<u8> ys = xs.assign(2, 0);', "vector<u8> xs;",
     "接收者类型对得上也不行：void 不是 vector"),
    ("u64 n = xs.assign(2, 0);", "vector<u64> xs;",
     "assign 返回 void，不是 u64"),
    ("bool b = xs.assign(2, 0);", "vector<u64> xs;",
     "assign 返回 void，不是 bool"),
    ("i64 n = xs.assign(2, 0);", "vector<u64> xs;",
     "assign 返回 void，不是 i64"),
    ("f64 x = xs.assign(2, 0);", "vector<u64> xs;",
     "assign 返回 void，不是标量"),
    ("vector<vector<u8>> ys = xs.assign(2, 0);", "vector<u64> xs;",
     "即使元素类型改成容器的容器，void 也接不上"),
    ("u64 n = xs.clear();", "vector<u64> xs;",
     "clear 返回 void"),
    ("bool b = xs.clear();", "vector<u64> xs;",
     "clear 返回 void"),
    ("u64 n = xs.push_back(1);", "vector<u64> xs;",
     "push_back 返回 void（发射器/.tu 口径）"),
    ("u64 n = xs.insert(0, 1);", "vector<u64> xs;",
     "序列的 insert 返回 void"),
    ("u64 n = xs.erase(0);", "vector<u64> xs;",
     "序列的 erase 返回 void"),
]


def test_negative():
    print("[3] 负例：这些**必须**被拒绝（E-Type-Assign）")
    for stmt, decls, why in NEGATIVE:
        c = codes(decls, stmt)
        errs = [x for x in c if x.startswith("E-")]
        check(f"拒绝：{stmt}  （{why}）", errs == ["E-Type-Assign"], str(c))


def test_assign_only_vector():
    print("[3b] `assign` 只有 `vector` 有：定长容器报 E-Call-UnknownMethod")
    for decls in ("array<u64, 4> a;", "fixedvec<u64, 4> a;"):
        var = decls.split()[-1].rstrip(";")
        c = codes(decls, f"{var}.assign(2, 0)")
        check(f"{decls} .assign 被拒", "E-Call-UnknownMethod" in c, str(c))
        # 连"当语句用"也必须拒——不能因为"返回值未知"就放行。
        c2 = codes(decls, f"{var}.assign(2, 0)")
        check(f"{decls} .assign 不是 `?` 放行", c2 != [], str(c2))


# ---------------------------------------------------------------- 4. 正例

POSITIVE = [
    # **必须保持能过**：语句位置丢掉返回值。
    ("xs.assign(2, 0);", "vector<u64> xs;"),
    ('xs.assign(2, 0);', 'vector<vector<u8>> xs;'),
    ("xs.clear();", "vector<u64> xs;"),
    ("xs.push_back(1);", "vector<u64> xs;"),
    ("xs.insert(0, 1);", "vector<u64> xs;"),
    ("xs.erase(0);", "vector<u64> xs;"),
    # 相邻方法的类型没被这次改动带歪。
    ("u64 n = xs.size();", "vector<u64> xs;"),
    ("bool e = xs.empty();", "vector<u64> xs;"),
    ("u64 p = xs.pop();", "vector<u64> xs; xs.push_back(1);"),
    ("u64 b = xs.back();", "vector<u64> xs; xs.push_back(1);"),
    ("u64 i = xs.find(1);", "vector<u64> xs; xs.push_back(1);"),
]


def test_positive():
    print("[4] 正例：语句位置 / 相邻方法**不许**被误拒")
    for stmt, decls in POSITIVE:
        c = codes(decls, stmt)
        errs = [x for x in c if x.startswith("E-")]
        check(f"接受：{stmt}", not errs, str(c))


# ---------------------------------------------------------------- 5. 没放宽

def test_not_widened():
    print("[5] `void` 既不兼容标量、也不兼容容器（没有变成「什么都收」）")
    # 上一轮这里还能靠"接收者类型"接住 `vector<u64> ys = xs.assign(...)`；
    # 本轮是 void，所以**任何**声明位置都必须报同一条 `E-Type-Assign`，
    # 而且错误消息里要能看出根因（"初值是 void"），不是含糊的"类型不符"。
    for decls, stmt in (
        ("vector<u64> xs;", "u64 n = xs.assign(2, 0)"),
        ("vector<u64> xs;", "vector<u64> ys = xs.assign(2, 0)"),
        ("vector<u64> xs;", "vector<vector<u8>> ys = xs.assign(2, 0)"),
    ):
        c = codes(decls, stmt)
        errs = [x for x in c if x.startswith("E-")]
        check(f"{stmt} → 只报 E-Type-Assign", errs == ["E-Type-Assign"], str(c))
    # 语句位置的 `lhs(2, 0);` 不是"放宽"：返回值本来就被丢弃。
    check("语句位置不报错", codes("vector<u64> xs;", "xs.assign(2, 0)") == [])


# ---------------------------------------------------------------- main

def main() -> int:
    test_no_method_falls_to_unknown()
    test_assign_static()
    test_assign_runtime()
    test_negative()
    test_assign_only_vector()
    test_positive()
    test_not_widened()
    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: " + "; ".join(FAILED))
        return 1
    print("全部通过（全表排查 / assign 静态+运行时 / 负例 / 定长容器 / 正例 / 未放宽）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
