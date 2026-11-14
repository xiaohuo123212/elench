#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_method_types.py —— 容器方法的**返回类型**：静态声明 == 解释器实际返回。

## 为什么要有这条测试

2026-11-13 的审计：`check` 全绿、`run` 出来的值却不对——不是解释器错，是
`typecheck.py` 把方法的返回类型**声明错了**（或干脆声明成 `?`）。三个实测：

| # | 形状 | 修前 check | 修前 run | 后果 |
|---|---|---|---|---|
| 1 | `bool b = m.insert(1, 3);` | ✓（声明 bool） | `0` / `1`（u64） | 生成物按 C++ `bool` 打 `False`/`True`，解释器打 `0`/`1` |
| 2 | `bool a = s.insert(1);` | ✗ `E-Type-Assign`「初值是 void」 | `0`（u64） | 能跑的值被静态拒掉 |
| 3 | `bool b = m.contains(1);` | ✓（`TY_UNKNOWN`） | `1`（u64） | **最宽的一处**：`u64 n = …` 与 `bool b = …` 都放行 |

**2026-11-13 第二轮（本轮的改动）**：第 3 行按"对齐 interp"改成 `u64` 之后，
`if (m.contains(k))` 报 `E-Type-Cond`——**类型更精确了，用户的自然写法却编不过**，
方向反了。`contains` 的唯一用途是谓词，`if` 只收 bool / 指针，所以：

- `contains` → **`bool`**（`interp.py` 返回 `True/False`，发射器头文件也是
  `bool contains(const K&) const`）；
- `insert` / `put` / `remove` / `erase` **仍返回 u64 计数**（1/0，没人写进 `if`）。

**判据只有一个**：`src/interp.py` 的 `_method` 是语言语义的定义。本测试把它和
`typecheck.py` 的声明**逐条钉住**，再对每一类配一条「必须被拒绝」的负例，
防止修法变成"把类型放宽成什么都收"。

跑法：`python tests/cases/test_method_types.py`，退出码 0 即通过。
"""
import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
for _p in (SRC,):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os                                                    # noqa: E402
import sys                                                   # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                            # noqa: BLE001
    pass

import compat as _compat                                     # noqa: E402
from interp import Interp                                    # noqa: E402
from parser import parse_source                              # noqa: E402
import typecheck as _tc                                      # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


# ---------------------------------------------------------------- 工具

#: `f` 的 `.tu` 签名可以按需要的返回类型替换（只有 spec 一行）。
def _tu(ret="i64"):
    return f"spec f() -> {ret} {{ effects {{}}; }}\n"


def _module(decls, call, ret="i64"):
    """把一段函数体 + 一条表达式拼成可解析的模块。"""
    body = "    " + decls.replace("\n", "\n    ") + "\n"
    return (parse_source(_tu(ret), "t.tu").module,
            parse_source(f"{ret} f() {{\n{body}    print({call});\n    return 0;\n}}\n",
                         "t.eln").module)


def _walk(n, out):
    out.append(n)
    for c in (n.children or []):
        _walk(c, out)
    return out


def static_ty(decls, call):
    """`call` 表达式的**静态类型**（用 typecheck 自己的记录器，不另写推断）。"""
    tu, ch = _module(decls, call)
    rec = _tc._TypeRecorder(tu, ch)
    rec.check()
    mname = call.rsplit(".", 1)[1].split("(")[0]
    nodes = [x for x in _walk(ch, []) if x.kind == "method-call" and x.name == mname]
    if not nodes:
        return "（找不到 method-call）"
    return rec.ty_of.get(id(nodes[0]), _tc.TY_UNKNOWN).render()


def codes(decls, stmt, ret="i64"):
    """`decls` + 一条语句跑一遍 typecheck，返回诊断码列表（不经过 CLI）。"""
    body = "    " + decls.replace("\n", "\n    ") + "\n"
    tu = parse_source(_tu(ret), "t.tu").module
    ch = parse_source(f"{ret} f() {{\n{body}    {stmt};\n    return 0;\n}}\n",
                      "t.eln").module
    return [d.code for d in _tc.typecheck(tu, ch)]


def run_ret(decls, call, ret):
    """解释器**实际**返回：把 `call` 放在 `ret f()` 里返回，取值。

    只有 `call` 的运行时类型真的等于 `ret` 时才用它——这本身就是一条断言
    （声明与返回值不一致时 `_convert_to` 会报错）。
    """
    tu = parse_source(_tu(ret), "t.tu").module
    body = "    " + decls.replace("\n", "\n    ") + "\n"
    ch = parse_source(f"{ret} f() {{\n{body}    return {call};\n}}\n", "t.eln").module
    return Interp(ch, tu).call("f", [])


# ---------------------------------------------------------------- 1. 静态类型表

#: 容器 → 声明 → 方法 → **期望静态类型**（== interp `_method` 的实际返回）。
#: `map` / `set` 的 `keys` / `values` 是**快照 vector**，不是视图。
TABLE = [
    # ---- vector / array：序列语义（insert/erase 是位置操作，返回 void）
    ("vector<u64> v;", "size", "u64"),
    ("vector<u64> v;", "empty", "bool"),
    ("vector<u64> v;", "at", "u64"),
    ("vector<u64> v; v.push_back(1);", "back", "u64"),
    ("vector<u64> v; v.push_back(1);", "front", "u64"),
    ("vector<u64> v; v.push_back(1);", "pop", "u64"),
    ("vector<u64> v; v.push_back(1);", "find", "u64"),
    ("vector<u64> v;", "push_back", "void"),
    ("vector<u64> v;", "insert", "void"),
    ("vector<u64> v;", "erase", "void"),
    ("vector<u64> v;", "clear", "void"),
    # **`assign` 是 void**（2026-11-13，C++ 的 `std::vector::assign` 就是 void）：
    # 它只能作语句；`vector<u64> ys = v.assign(2, 0);` 报 E-Type-Assign
    # （见下面 NEGATIVE）。上一轮曾按"接收者类型"登记，`check` 绿而 `dsh cpp`
    # 发射的 C++ 编不过。
    ("vector<u64> v;", "assign", "void"),
    ("array<u64, 4> a;", "size", "u64"),
    ("array<u64, 4> a;", "empty", "bool"),
    ("array<u64, 4> a;", "at", "u64"),
    ("array<u64, 4> a; a[0] = 1;", "back", "u64"),
    ("array<u64, 4> a; a[0] = 1;", "front", "u64"),
    ("array<u64, 4> a; a[0] = 1;", "pop", "u64"),
    ("array<u64, 4> a; a[0] = 1;", "find", "u64"),
    ("array<u64, 4> a;", "insert", "void"),
    ("array<u64, 4> a;", "erase", "void"),
    ("array<u64, 4> a;", "clear", "void"),

    # ---- map（有序）
    ("map<u64, u64> m;", "size", "u64"),
    ("map<u64, u64> m;", "empty", "bool"),
    ("map<u64, u64> m;", "insert", "u64"),
    ("map<u64, u64> m;", "put", "u64"),
    ("map<u64, u64> m; m.insert(1, 10);", "get", "u64"),
    # `contains` 是**谓词**：返回 bool（`if (m.contains(k))` 才编得过）。
    # 返回 0/1 计数的是 insert / put / remove / erase（下面几行，仍是 u64）。
    ("map<u64, u64> m; m.insert(1, 10);", "contains", "bool"),
    ("map<u64, u64> m; m.insert(1, 10);", "remove", "u64"),
    ("map<u64, u64> m; m.insert(1, 10);", "erase", "u64"),
    ("map<u64, u64> m;", "clear", "void"),
    ("map<u64, u64> m; m.insert(1, 10);", "keys", "vector<u64>"),

    # ---- set（有序）
    ("set<u64> s;", "size", "u64"),
    ("set<u64> s;", "empty", "bool"),
    ("set<u64> s;", "insert", "u64"),
    ("set<u64> s; s.insert(1);", "contains", "bool"),
    ("set<u64> s; s.insert(1);", "remove", "u64"),
    ("set<u64> s; s.insert(1);", "erase", "u64"),
    ("set<u64> s;", "clear", "void"),
    ("set<u64> s; s.insert(1);", "values", "vector<u64>"),

    # ---- unordered_map
    ("unordered_map<u64, u64> h;", "size", "u64"),
    ("unordered_map<u64, u64> h;", "empty", "bool"),
    ("unordered_map<u64, u64> h;", "insert", "u64"),
    ("unordered_map<u64, u64> h; h.insert(1, 10);", "get", "u64"),
    ("unordered_map<u64, u64> h; h.insert(1, 10);", "contains", "bool"),
    ("unordered_map<u64, u64> h; h.insert(1, 10);", "remove", "u64"),
    ("unordered_map<u64, u64> h; h.insert(1, 10);", "erase", "u64"),
    ("unordered_map<u64, u64> h;", "clear", "void"),
    ("unordered_map<u64, u64> h; h.insert(1, 10);", "keys", "vector<u64>"),
    ("unordered_map<u64, u64> h; h.insert(1, 10);", "values", "vector<u64>"),

    # ---- unordered_set
    ("unordered_set<u64> hs;", "size", "u64"),
    ("unordered_set<u64> hs;", "empty", "bool"),
    ("unordered_set<u64> hs;", "insert", "u64"),
    ("unordered_set<u64> hs; hs.insert(1);", "contains", "bool"),
    ("unordered_set<u64> hs; hs.insert(1);", "remove", "u64"),
    ("unordered_set<u64> hs; hs.insert(1);", "erase", "u64"),
    ("unordered_set<u64> hs;", "clear", "void"),
    ("unordered_set<u64> hs; hs.insert(1);", "keys", "vector<u64>"),
    ("unordered_set<u64> hs; hs.insert(1);", "values", "vector<u64>"),

    # ---- stack / queue：push/enqueue 返回 u64(0)（interp），其余是元素
    ("stack<u64> st;", "size", "u64"),
    ("stack<u64> st;", "empty", "bool"),
    ("stack<u64> st;", "push", "u64"),
    ("stack<u64> st;", "enqueue", "u64"),
    ("stack<u64> st; st.push(1);", "pop", "u64"),
    ("stack<u64> st; st.push(1);", "dequeue", "u64"),
    ("stack<u64> st; st.push(1);", "top", "u64"),
    ("stack<u64> st; st.push(1);", "peek", "u64"),
    ("stack<u64> st; st.push(1);", "front", "u64"),
    ("stack<u64> st;", "clear", "void"),
    ("queue<u64> q;", "size", "u64"),
    ("queue<u64> q;", "empty", "bool"),
    ("queue<u64> q;", "push", "u64"),
    ("queue<u64> q;", "enqueue", "u64"),
    ("queue<u64> q; q.push(1);", "pop", "u64"),
    ("queue<u64> q; q.push(1);", "dequeue", "u64"),
    ("queue<u64> q; q.push(1);", "top", "u64"),
    ("queue<u64> q; q.push(1);", "peek", "u64"),
    ("queue<u64> q; q.push(1);", "front", "u64"),
    ("queue<u64> q;", "clear", "void"),

    # ---- 元素类型要**传下去**：keys 是 K 的 vector，values 是 V 的 vector
    ("map<vector<u8>, u64> m; m.insert(\"a\", 1);", "keys",
     "vector<vector<u8>>"),
    ("unordered_map<u64, vector<u8>> h; h.insert(1, \"a\");", "values",
     "vector<vector<u8>>"),
    ("set<vector<u8>> s; s.insert(\"a\");", "values", "vector<vector<u8>>"),
]


def test_static_table():
    print("[1] 静态类型表：容器 / 方法 / 期望（== interp 实际返回）")
    for decls, meth, want in TABLE:
        recv = decls.split(";")[0].strip()
        var = recv.split()[-1].rstrip(";")
        call = f"{var}.{meth}()"
        got = static_ty(decls, call)
        check(f"{recv} .{meth}() → {want}", got == want,
              f"实际 {got}（{decls}）")


# ---------------------------------------------------------------- 2. 运行时真值

def test_runtime():
    print("[2] 解释器实际返回（`src/interp.py` 是定义）")
    check("map.insert 首次插入 → u64 1",
          run_ret("map<u64, u64> m;", "m.insert(1, 3)", "u64") == 1)
    check("map.insert 键已存在 → u64 0（不覆盖）",
          run_ret("map<u64, u64> m; m.insert(1, 3);", "m.insert(1, 99)", "u64") == 0)
    check("map.put 覆盖 → u64 0（键本来就在）",
          run_ret("map<u64, u64> m; m.insert(1, 3);", "m.put(1, 99)", "u64") == 0)
    check("map.put 新键 → u64 1",
          run_ret("map<u64, u64> m;", "m.put(7, 9)", "u64") == 1)
    check("unordered_map.insert 恒 0（它覆盖）",
          run_ret("unordered_map<u64, u64> h;", "h.insert(1, 3)", "u64") == 0)
    check("set.insert 恒 0",
          run_ret("set<u64> s;", "s.insert(1)", "u64") == 0)
    check("unordered_set.insert 恒 0",
          run_ret("unordered_set<u64> hs;", "hs.insert(1)", "u64") == 0)
    # `contains` 是**谓词**：运行时也必须是 `True/False`（不是 u64 的 1/0），
    # 否则 `bool b = m.contains(k)` 在解释器里存的是 u64、静态却声明 bool。
    check("map.contains 命中 → True",
          run_ret("map<u64, u64> m; m.insert(1, 3);", "m.contains(1)", "bool") is True)
    check("map.contains 未命中 → False",
          run_ret("map<u64, u64> m;", "m.contains(1)", "bool") is False)
    check("map.keys() → vector<u64>（键序）",
          run_ret("map<u64, u64> m; m.insert(2, 20); m.insert(1, 10);",
                  "m.keys()", "vector<u64>") == [1, 2])
    check("unordered_map.values() → vector<u64>（值，不是键）",
          run_ret("unordered_map<u64, u64> h; h.insert(1, 10); h.insert(2, 20);",
                  "h.values()", "vector<u64>") == [10, 20])
    check("set.values() → vector<u64>（元素本身）",
          run_ret("set<u64> s; s.insert(7);", "s.values()", "vector<u64>") == [7])
    check("stack.push 返回 u64 0",
          run_ret("stack<u64> st;", "st.push(1)", "u64") == 0)
    check("queue.enqueue 返回 u64 0",
          run_ret("queue<u64> q;", "q.enqueue(1)", "u64") == 0)
    check("map.remove 删掉了 → u64 1",
          run_ret("map<u64, u64> m; m.insert(1, 3);", "m.remove(1)", "u64") == 1)
    check("set.erase 本来没有 → u64 0",
          run_ret("set<u64> s;", "s.erase(9)", "u64") == 0)
    # `assign` 的运行时返回值是 **void（None）**：上一轮是接收者本身，
    # 于是 `vector<u64> ys = xs.assign(2, 0);` 在 check 绿、run 有值、
    # dsh cpp 编不过之间三方分裂。现在三方都是 void。
    check("vector.assign 返回 void（None）",
          run_ret("vector<u64> v;", "v.assign(2, 0)", "()") is None)

    # `c_str` 只在 `--compat` 下认；返回同一段文本（`vector<u8>`）。
    _compat.set_enabled(True)
    try:
        check("compat: vector<u8>.c_str() → vector<u8>（同一段文本）",
              static_ty("vector<u8> t;", "t.c_str()") == "vector<u8>")
        check("compat: c_str() 返回原文本",
              run_ret("vector<u8> t; t.push_back(65);", "t.c_str()",
                      "vector<u8>") == [65])
    finally:
        _compat.set_enabled(False)


# ---------------------------------------------------------------- 3. 反向验收

#: 每条都**必须**报 `E-Type-Assign`（把错误的类型塞进声明位置）。
#: `before` 记录修前的行为，写报告时用；测试只断言"现在必须拒绝"。
NEGATIVE = [
    # **本轮靶子**：`assign` 返回 void（C++ 语义），不能拿它的值。
    ("vector<u64> ys = v.assign(2, 0);", "vector<u64> v;",
     "assign() 是 void，不是 vector（check 绿而 dsh cpp 编不过的那条）"),
    ("u64 n = v.assign(2, 0);", "vector<u64> v;",
     "assign() 是 void，不是 u64"),
    ("u64 x = m.keys();", "map<u64, u64> m; m.insert(1, 10);",
     "keys() 是 vector<u64>，不是标量"),
    ("u64 x = h.values();", "unordered_map<u64, u64> h; h.insert(1, 10);",
     "values() 是 vector<u64>，不是标量"),
    ("u64 x = s.values();", "set<u64> s; s.insert(1);",
     "set.values() 是 vector<u64>，不是标量"),
    ("vector<u64> v = m.contains(1);", "map<u64, u64> m; m.insert(1, 10);",
     "contains() 是 bool，不是 vector"),
    ("vector<u64> v = m.insert(1, 2);", "map<u64, u64> m;",
     "insert() 是 u64，不是 vector"),
    ("bool b = m.insert(1, 2);", "map<u64, u64> m;",
     "insert() 是 u64（修前声明成 bool，静默错值）"),
    ("bool b = m.put(1, 2);", "map<u64, u64> m;",
     "put() 是 u64（修前声明成 bool）"),
    # **反向验收（2026-11-13 本轮）**：contains 是谓词。把 bool 塞进 u64 必须报；
    # 反过来 `bool b = m.contains(1)` 是正例（见 POSITIVE）。
    ("u64 n = m.contains(1);", "map<u64, u64> m; m.insert(1, 10);",
     "contains() 是 bool（谓词），不是 u64 计数"),
    ("u64 n = s.contains(1);", "set<u64> s; s.insert(1);",
     "set.contains() 是 bool，不是 u64"),
    ("u64 n = h.contains(1);", "unordered_map<u64, u64> h; h.insert(1, 10);",
     "unordered_map.contains() 是 bool，不是 u64"),
    ("u64 n = hs.contains(1);", "unordered_set<u64> hs; hs.insert(1);",
     "unordered_set.contains() 是 bool，不是 u64"),
    ("bool b = s.insert(1);", "set<u64> s;",
     "set.insert() 是 u64（修前是 void，能跑却被误拒）"),
    ("bool b = h.values();", "unordered_map<u64, u64> h; h.insert(1, 10);",
     "values() 是 vector<u64>，不是 bool"),
    ("vector<vector<u8>> ks = m.keys();", "map<u64, u64> m; m.insert(1, 10);",
     "键是 u64，不是 vector<u8>"),
]


def test_negative():
    print("[3] 反向验收：这些**必须**被拒绝（E-Type-Assign）")
    for src, decls, why in NEGATIVE:
        # 语句以赋值形式给出：把 `T x = expr;` 直接拼进函数体。
        c = codes(decls, src)
        errs = [x for x in c if x.startswith("E-")]
        check(f"拒绝：{src}（{why}）", errs == ["E-Type-Assign"], str(c))


# ---------------------------------------------------------------- 4. 正例

POSITIVE = [
    # `assign` 当语句：返回值被丢弃，必须仍然能过（否则就是误拒）。
    ("v.assign(2, 0);", "vector<u64> v;"),
    # `contains` 是谓词：bool 变量 + `if` 条件（`if` 只收 bool / 指针）。
    ("bool b = m.contains(1);", "map<u64, u64> m; m.insert(1, 10);"),
    ("bool b = s.contains(1);", "set<u64> s; s.insert(1);"),
    ("bool b = h.contains(1);", "unordered_map<u64, u64> h; h.insert(1, 10);"),
    ("bool b = hs.contains(1);", "unordered_set<u64> hs; hs.insert(1);"),
    ("u64 r = m.insert(1, 2);", "map<u64, u64> m;"),
    ("u64 r = m.put(1, 2);", "map<u64, u64> m;"),
    ("u64 g = m.get(1);", "map<u64, u64> m; m.insert(1, 10);"),
    ("u64 rm = m.remove(1);", "map<u64, u64> m; m.insert(1, 10);"),
    ("u64 er = m.erase(1);", "map<u64, u64> m; m.insert(1, 10);"),
    ("vector<u64> ks = m.keys();", "map<u64, u64> m; m.insert(1, 10);"),
    ("vector<u64> vs = h.values();", "unordered_map<u64, u64> h; h.insert(1, 10);"),
    ("vector<u64> sv = s.values();", "set<u64> s; s.insert(1);"),
    ("u64 t = st.top();", "stack<u64> st; st.push(1);"),
    ("u64 p = st.push(1);", "stack<u64> st;"),
    ("u64 q = qq.enqueue(1);", "queue<u64> qq;"),
    # `set.insert` / `unordered_set.insert` 以前声明成 void（能跑的值被误拒）。
    ("u64 r = s.insert(1);", "set<u64> s;"),
    ("u64 r = hs.insert(1);", "unordered_set<u64> hs;"),
    ("vector<u64> kv = hs.keys();", "unordered_set<u64> hs; hs.insert(1);"),
]


def test_positive():
    print("[4] 正例：类型对得上的用法**不许**被误拒")
    for src, decls in POSITIVE:
        c = codes(decls, src)
        errs = [x for x in c if x.startswith("E-")]
        check(f"接受：{src}", not errs, str(c))


def _codes_body(lines, ret="u64"):
    """把 `lines` 原样拼进函数体后跑 typecheck，返回诊断码。

    `codes()` 只收一条 `T x = expr;`；`if` / `while` 这类语句形状用它会被拼成
    `if (...) { };`，所以另走这一条（不动 `codes()` 的既有口径）。
    """
    tu = parse_source(_tu(ret), "t.tu").module
    body = "\n".join("    " + ln for ln in lines)
    ch = parse_source(f"{ret} f() {{\n{body}\n    return 0;\n}}\n",
                      "t.eln").module
    return [d.code for d in _tc.typecheck(tu, ch)]


#: 四个容器的「已有一个元素」声明 + 变量名（`if` 条件必须真的能编译）。
CONTAINS_RECV = [
    ("map<u64, u64> m; m.insert(1, 10);", "m"),
    ("set<u64> s; s.insert(1);", "s"),
    ("unordered_map<u64, u64> h; h.insert(1, 10);", "h"),
    ("unordered_set<u64> hs; hs.insert(1);", "hs"),
]


def test_contains_is_predicate():
    print("[4b] `contains` 是谓词：`if` / `while` / `!` 位置都当 bool 收（本轮回归）")
    for decls, var in CONTAINS_RECV:
        for shape, stmt in (
                ("if", f"if ({var}.contains(1)) {{ }}"),
                ("while", f"while ({var}.contains(1)) {{ return 0; }}"),
                ("!", f"if (!{var}.contains(1)) {{ }}"),
        ):
            c = _codes_body([decls, stmt])
            errs = [x for x in c if x.startswith("E-")]
            check(f"接受：{stmt}（{shape}）", not errs, str(c))
    # **反向**：`if` 条件只收 bool / 指针——`contains` 若被声明回 u64 就会在这里红。
    c = _codes_body(["map<u64, u64> m; m.insert(1, 10);",
                     "u64 n = m.contains(1);"])
    check("拒绝：`u64 n = m.contains(1);` → E-Type-Assign",
          [x for x in c if x.startswith("E-")] == ["E-Type-Assign"], str(c))


# ---------------------------------------------------------------- 5. 未放宽

def test_not_widened():
    print("[5] 没有把「未知」换成「什么都收」：相邻容器类型仍然分开")
    # `map<u64,u64>` 的 keys 是 vector<u64>；塞进 vector<vector<u8>> 必须报。
    c = codes("map<u64, u64> m; m.insert(1, 1);",
              "vector<vector<u8>> ks = m.keys()")
    check("vector<u64> 不能当 vector<vector<u8>>", "E-Type-Assign" in c, str(c))
    # `unordered_map<u64, vector<u8>>` 的 values 是 vector<vector<u8>>；
    # 塞进 vector<u64> 必须报（元素类型没有被抹成"任意"）。
    c2 = codes("unordered_map<u64, vector<u8>> h; h.insert(1, \"a\");",
               "vector<u64> vs = h.values()")
    check("vector<vector<u8>> 不能当 vector<u64>", "E-Type-Assign" in c2, str(c2))
    # `map` 没有 `values()`（interp 也没有）：静默放行才是回归。
    c3 = codes("map<u64, u64> m; m.insert(1, 1);", "m.values()")
    check("map.values() 仍是 E-Call-UnknownMethod",
          "E-Call-UnknownMethod" in c3, str(c3))


# ---------------------------------------------------------------- main

def main() -> int:
    test_static_table()
    test_runtime()
    test_negative()
    test_positive()
    test_contains_is_predicate()
    test_not_widened()
    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: " + "; ".join(FAILED))
        return 1
    print("全部通过（静态类型表 / 运行时真值 / 反向验收 / 正例 / 谓词位置 / 未放宽）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

