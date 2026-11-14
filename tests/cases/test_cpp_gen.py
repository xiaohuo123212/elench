#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_cpp_gen.py —— L0 止血：`elench cpp` 的 5 个静默错误回归测试

## 为什么单独一个文件

`test_cpp.py` 只验「最小切片能生成、能编译」。这 5 个 bug 的共同特征是
**exit 0 + 生成物看起来像 C++**，所以旧测试一个都没抓到：

1. `u64 j = i;` → `uint64_t j = 0;`（初值被丢掉）
2. `vector<u64>& xs` → `const std::vector<uint64_t>&`（写不进去）
3. `if (...) {...} else {...}` → `else` 整个消失（**最危险：能编、能跑、结果错**）
4. `f64 x = 1;` → `uint64_t x = ...`（未知类型静默退化）
5. 不跑 checker：`return nope;` 也 rc=0 并写出代码

本文件只做**文本断言**（不依赖 g++）：生成出来的 C++ 里必须出现/不出现什么。
编译自检由 `cmd_cpp` 自己负责，这里只验「它要么真编、要么明确说跳过」。
"""
import os
import shutil
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
DSH = os.path.join(SRC, "dsh.py")

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def _spec(sig: str) -> str:
    return (f"spec {sig} {{ effects {{}}; "
            f"target {{ time: {{ max: O(1) }}, allocation: {{ max: 0 }}, }}; }}\n")


def run_cpp(name: str, tu: str, eln: str, extra=None, env=None):
    """写一对 .tu/.eln，跑 `dsh cpp --output=<tmp>.cpp`，返回 (proc, 生成文本, 输出路径)。"""
    d, tu_p, ch_p, out_p = _write_pair(name, tu, eln)
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(
        [sys.executable, DSH, "cpp", tu_p, "--output=" + out_p] + list(extra or []),
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=e)
    gen = ""
    if os.path.exists(out_p):
        with open(out_p, encoding="utf-8", errors="replace") as fh:
            gen = fh.read()
    return r, gen, out_p


def _write_pair(name: str, tu: str, eln: str):
    """只负责落盘：返回 (临时目录, .tu 路径, .eln 路径, 期望的 .cpp 路径)。

    语义对拍那一节需要 `.tu`/`.eln` 的**路径**去跑 `dsh run`，所以把它拆出来
    复用，而不是让 `run_cpp` 返回五元组（会动到所有既有调用点）。
    """
    d = tempfile.mkdtemp(prefix="eln_cppgen_" + name + "_")
    tu_p = os.path.join(d, name + ".tu")
    ch_p = os.path.join(d, name + ".eln")
    out_p = os.path.join(d, name + ".cpp")
    open(tu_p, "w", encoding="utf-8", newline="\n").write(tu)
    open(ch_p, "w", encoding="utf-8", newline="\n").write(eln)
    return d, tu_p, ch_p, out_p


def both(r):
    return (r.stdout or "") + (r.stderr or "")


# ---------------------------------------------------------------- ① 初值
def test_path_init():
    print("[1] `u64 j = i;` 初值不能丢；`vector<u64> tmp;` 不能补 `= 0`")
    r, gen, _ = run_cpp(
        "path_init",
        _spec("get(u64 i) -> u64"),
        "u64 get(u64 i) {\n"
        "    u64 j = i;\n"
        "    vector<u64> tmp;\n"
        "    return j;\n"
        "}\n")
    check("rc=0", r.returncode == 0, both(r)[-300:])
    check("初值是 path → `uint64_t j = i;`", "uint64_t j = i;" in gen, gen[:300])
    check("不再被兜底成 `= 0`", "uint64_t j = 0;" not in gen, gen[:300])
    check("无初值 vector → `std::vector<uint64_t> tmp;`",
          "std::vector<uint64_t> tmp;" in gen, gen[:300])
    check("无初值 vector 不再被写成 `= 0`",
          "std::vector<uint64_t> tmp = 0;" not in gen, gen[:300])


# ---------------------------------------------------------------- ② 形参 const
def test_param_const():
    print("[2] 形参的 const 要按 `.eln` 给，不能给所有 vector 都加 `const&`")
    r, gen, _ = run_cpp(
        "param_const",
        _spec("f(vector<u64>& xs) -> u64") + _spec("g(const vector<u64>& ys) -> u64"),
        "u64 f(vector<u64>& xs) {\n"
        "    xs[0] = 1;\n"
        "    return xs[0];\n"
        "}\n"
        "u64 g(const vector<u64>& ys) {\n"
        "    return ys[0];\n"
        "}\n")
    check("rc=0", r.returncode == 0, both(r)[-300:])
    check("可变引用 → `std::vector<uint64_t>& xs`",
          "uint64_t f(std::vector<uint64_t>& xs)" in gen, gen[:400])
    check("可变引用**不**带 const",
          "const std::vector<uint64_t>& xs" not in gen, gen[:400])
    check("源里写 const 的仍是 `const std::vector<uint64_t>& ys`",
          "uint64_t g(const std::vector<uint64_t>& ys)" in gen, gen[:400])


# ---------------------------------------------------------------- ③ else
def test_else():
    print("[3] `else` / `else if` 必须发射，block 要取全部子语句")
    r, gen, _ = run_cpp(
        "else_branch",
        _spec("pick(u64 a, u64 b) -> u64"),
        "u64 pick(u64 a, u64 b) {\n"
        "    u64 r = 0;\n"
        "    if (a > b) { r = a; } else { r = b; }\n"
        "    if (a == 0) { r = 1; } else if (b == 0) { r = 2; } else { r = 3; }\n"
        "    return r;\n"
        "}\n")
    check("rc=0", r.returncode == 0, both(r)[-300:])
    check("有 else 分支", "else {" in gen, gen[:500])
    check("else 里的 `r = b;` 在", "r = b;" in gen, gen[:500])
    check("有 else if", "else if ((" in gen, gen[:500])
    check("else-if 链末尾的 `r = 3;` 在", "r = 3;" in gen, gen[:500])
    # 三个分支都在：r=a / r=b / r=3
    for stmt in ("r = a;", "r = b;", "r = 3;"):
        check(f"分支语句 `{stmt}` 没有被吞掉", stmt in gen, gen[:500])


# ---------------------------------------------------------------- ④ 未知类型
def test_unknown_type():
    print("[4] 认不出的类型不许静默变成 uint64_t")
    r, gen, out_p = run_cpp(
        "unknown_ty",
        _spec("f() -> u64"),
        "u64 f() {\n"
        "    f64 x = 1;\n"
        "    return 1;\n"
        "}\n")
    # 两种可接受的修法：① 生成器报 E-Cxx-Unsupported（rc≠0、不写文件）；
    #                  ② cxx_map 后来补了 f64 → 生成 `double`，但**绝不能**是 uint64_t。
    if r.returncode != 0:
        check("认不出 → rc≠0", True)
        check("报 E-Cxx-Unsupported", "E-Cxx-Unsupported" in both(r), both(r)[-300:])
        check("不写文件", not os.path.exists(out_p), out_p)
    else:
        check("若 cxx_map 已映射 f64，则生成 double 而不是 uint64_t",
              "double x" in gen and "uint64_t x" not in gen, gen[:300])
    check("绝不出现 `uint64_t x`", "uint64_t x" not in gen, gen[:300])

    # f64 是**语言合法类型**（checker 不拦），所以生成器必须自己拒绝/正确映射。
    # 再补一个 checker 也放行、但 cxx_map 必然没有映射的类型：`.tu` 里声明的 struct。
    # 这一条锁死「认不出 ⇒ 报错」，即使以后 cxx_map 补了 f64 也不会退化。
    print("[4b] `.tu` 里声明的 struct：生成器没有映射 ⇒ 明确报错，不写文件")
    struct_tu = ("struct Point { x: u64, y: u64 }\n" + _spec("f() -> u64"))
    r2, gen2, out2 = run_cpp(
        "unknown_struct",
        struct_tu,
        "u64 f() {\n"
        "    Point p;\n"
        "    return 1;\n"
        "}\n")
    check("rc≠0", r2.returncode != 0, both(r2)[-300:])
    check("报 E-Cxx-Unsupported", "E-Cxx-Unsupported" in both(r2), both(r2)[-300:])
    check("不写文件", not os.path.exists(out2), out2)
    check("不生成 `uint64_t p`", "uint64_t p" not in gen2, gen2[:300])


# ---------------------------------------------------------------- ⑤ checker
def test_checker_gate():
    print("[5] `dsh cpp` 必须先跑 checker：有 error ⇒ rc≠0、不写文件")
    r, gen, out_p = run_cpp(
        "no_check",
        _spec("main() -> u64"),
        "u64 main() {\n"
        "    return nope;\n"
        "}\n")
    check("rc≠0", r.returncode != 0, both(r)[-300:])
    check("诊断是 E-Name-Undefined", "E-Name-Undefined" in both(r), both(r)[-300:])
    check("错误时**不写文件**", not os.path.exists(out_p), out_p)


# ---------------------------------------------------------------- 编译自检
def test_compile_selfcheck():
    print("[6] 编译自检：有编译器就真编，没有就明确说「跳过」——不许静默")
    r, gen, _ = run_cpp(
        "selfcheck",
        _spec("get(u64 i) -> u64"),
        "u64 get(u64 i) {\n"
        "    u64 j = i;\n"
        "    return j;\n"
        "}\n")
    msg = both(r)
    has_compiler = bool(os.environ.get("ELENCH_CXX") or os.environ.get("ELENCH_GXX")) \
        or any(__import__("shutil").which(c) for c in ("g++", "clang++", "cl"))
    if has_compiler:
        check("有编译器 → 编译自检通过", "编译自检通过" in msg, msg[-400:])
    else:
        check("没编译器 → 明确说跳过，且不冒充通过",
              "跳过编译自检" in msg and "编译自检通过" not in msg, msg[-400:])

    print("[6b] 编译自检失败 ⇒ rc≠0（用假编译器验证失败路径，不依赖 g++）")
    d = tempfile.mkdtemp(prefix="eln_fakecc_")
    fake = os.path.join(d, "fake_cc.py")
    open(fake, "w", encoding="utf-8", newline="\n").write(
        "import sys\n"
        "sys.stderr.write('fake-compiler: 我拒绝这段代码\\n')\n"
        "sys.exit(3)\n")
    # ELENCH_CXX 是测试/CI 用的编译器覆盖点；这里塞「解释器 + 假编译器脚本」。
    env = {"ELENCH_CXX": f'"{sys.executable}" "{fake}"'}
    r2, gen2, out2 = run_cpp(
        "selfcheck_fail",
        _spec("get(u64 i) -> u64"),
        "u64 get(u64 i) {\n"
        "    u64 j = i;\n"
        "    return j;\n"
        "}\n",
        env=env)
    check("假编译器拒绝 → rc≠0", r2.returncode != 0, both(r2)[-400:])
    check("说清楚是编译自检失败", "编译自检失败" in both(r2), both(r2)[-400:])


# ---------------------------------------------------------------- ⑦ `?` 按位置
def test_question_type_by_position():
    """同一份"空类型"，只有**返回位置**能解释成 void。

    `.eln` 的 `-> ()` 与"类型推断失败"在 AST 里同形（都是 `_type_str == ""`），
    所以判据只能是位置：返回位置 → `void`；形参/变量位置 → `E-Cxx-Unsupported`。
    变量/形参的空类型在**源码里写不出来**（parse 先拒绝），所以这两条用
    `_cpp_stmt` / `_gen_cpp` 直接构造节点，验证生成器自己的护栏。
    """
    print("[7] 空类型按位置：返回 ⇒ void；形参/变量 ⇒ 报错")
    r, gen, _ = run_cpp(
        "void_pos",
        _spec("f() -> ()"),
        "void f() {\n    return;\n}\n",
        ["--compat"])
    check("rc=0", r.returncode == 0, both(r)[-300:])
    check("返回位置发射 `void f()`", "void f()" in gen, gen[:300])
    check("void 函数体里的 `return;` 原样发射", "    return;" in gen, gen[:300])

    if SRC not in sys.path:
        sys.path.insert(0, SRC)
    import dsh as _dsh

    class _N:
        """最小 Node 替身：只带生成器会读的四个字段。"""

        def __init__(self, kind, name=None, value=None, children=()):
            self.kind, self.name, self.value = kind, name, value
            self.children = list(children)

    _ctx = _dsh._CppCtx()
    try:
        _dsh._cpp_stmt(_N("var-decl", "x", {"mut": True}, []), "", _ctx)
        check("变量位置的空类型 ⇒ 报错", False, "没有抛 CppUnsupported")
    except _dsh.CppUnsupported:
        check("变量位置的空类型 ⇒ 报错", True)

    _prm = _N("param", "p")                     # 没有类型子节点 ⇒ 空类型
    _fn = _N("fn", "f", {"pub": False},
             [_N("params", children=[_prm]), _N("type-name", "u64")])
    try:
        _dsh._gen_cpp(_N("module"), _N("module", children=[_fn]))
        check("形参位置的空类型 ⇒ 报错", False, "没有抛 CppUnsupported")
    except _dsh.CppUnsupported:
        check("形参位置的空类型 ⇒ 报错", True)


# ---------------------------------------------------------------- ⑧ 语句
def test_statements():
    print("[8] 语句：block / for / break / continue / assign-op 都要发射")
    r, gen, _ = run_cpp(
        "stmts",
        _spec("g(vector<u64>& xs) -> u64"),
        "u64 g(vector<u64>& xs) {\n"
        "    u64 s = 0;\n"
        "    for (auto x : xs) {\n"
        "        if (x == 0) { continue; }\n"
        "        if (x > 100) { break; }\n"
        "        s += x;\n"
        "        s -= 1;\n"
        "    }\n"
        "    {\n"
        "        u64 t = 1;\n"
        "        s += t;\n"
        "    }\n"
        "    return s;\n"
        "}\n",
        ["--compat"])
    check("rc=0", r.returncode == 0, both(r)[-300:])
    check("range-for 发射", "for (auto x : xs) {" in gen, gen[:600])
    check("continue 发射", "continue;" in gen, gen[:600])
    check("break 发射", "break;" in gen, gen[:600])
    check("`+=` 复合赋值", "s += x;" in gen, gen[:600])
    check("`-=` 复合赋值", "s -= 1;" in gen, gen[:600])
    # block 作为**语句**（裸 `{}` 作用域）要发成成对的 `{` / `}`，且取全部子语句
    check("裸 block 发射成 `{`", "\n    {\n" in gen, gen[:800])
    check("裸 block 的子语句都发射",
          "        uint64_t t = 1;\n        s += t;" in gen, gen[:800])


# ---------------------------------------------------------------- ⑨ 方法调用
def test_method_calls():
    print("[9] method-call 通用化：size/push_back/empty/clear/keys/get/assign/at/insert/c_str")
    r, gen, _ = run_cpp(
        "methods",
        _spec("h(map<string, u64>& m, vector<u64>& v) -> u64"),
        "u64 h(map<string, u64>& m, vector<u64>& v) {\n"
        "    vector<string> ks = m.keys();\n"
        "    u64 a = m.get(ks[0]);\n"
        "    u64 n = v.size();\n"
        "    v.push_back(a);\n"
        "    v.clear();\n"
        "    bool e = v.empty();\n"
        "    v.assign(2, 7);\n"
        "    u64 z = v.at(0);\n"
        "    v.insert(0, z);\n"
        "    string t = ks[0].c_str();\n"
        "    return n;\n"
        "}\n",
        ["--compat"])
    check("rc=0", r.returncode == 0, both(r)[-300:])
    check("size → `.size()`", "v.size()" in gen, gen[:700])
    check("push_back → `.push_back(a)`", "v.push_back(a);" in gen, gen[:700])
    check("clear → `.clear()`", "v.clear();" in gen, gen[:700])
    check("empty → `.empty()`", "v.empty();" in gen, gen[:700])
    check("assign → `.assign(2, 7)`", "v.assign(2, 7);" in gen, gen[:700])
    check("at → `.at(0)`", "v.at(0);" in gen, gen[:700])
    check("insert → `insert(begin()+pos, value)`",
          "v.insert(v.begin() + (0), z);" in gen, gen[:700])
    check("keys → `elench_keys(m)`", "elench_keys(m)" in gen, gen[:700])
    check("get → `m.at(…)`（键不存在就报错，与解释器同口径）",
          "m.at(ks[0])" in gen, gen[:700])
    check("c_str 是同一个值（本语言没有指针字符串）",
          "std::vector<uint8_t> t = ks[0];" in gen, gen[:900])


# ---------------------------------------------------------------- ⑩ struct
def test_struct():
    print("[10] struct 定义 / struct-literal / field 都要发射")
    r, gen, _ = run_cpp(
        "structs",
        "struct Point { x: u64, y: u64 }\n"
        + _spec("mk(u64 a, u64 b) -> Point")
        + _spec("ox(Point p) -> u64"),
        "struct Point { u64 x; u64 y; };\n"
        "Point mk(u64 a, u64 b) {\n"
        "    return Point{a, b};\n"
        "}\n"
        "u64 ox(Point p) {\n"
        "    return p.x;\n"
        "}\n",
        ["--compat"])
    check("rc=0", r.returncode == 0, both(r)[-300:])
    check("struct 定义发射", "struct Point {" in gen, gen[:900])
    check("字段类型映射", "    uint64_t x;" in gen, gen[:900])
    check("struct-literal 发射（按声明顺序）", "return Point{a, b};" in gen, gen[:900])
    check("field 取值发射", "return p.x;" in gen, gen[:900])


# ---------------------------------------------------------------- ⑪ 字面量与 printf 族
def test_literals_and_printf():
    print("[11] 字面量 / list-init / 空名构造 / write・format・print")
    r, gen, _ = run_cpp(
        "lits",
        "spec lits(u64 n, f64 x) -> () { effects { io }; "
        "target { time: { max: O(1) }, }; }\n",
        "void lits(u64 n, f64 x) {\n"
        "    bool ok = true;\n"
        "    u8 c = 'A';\n"
        "    f64 y = 1e16;\n"
        "    string s = \"hi\\n\";\n"
        "    vector<u64> xs{5, 3, 9};\n"
        "    vector<string> ss{\"a\", \"b\"};\n"
        "    vector<u64> filled = vector<u64>(3, 7);\n"
        "    write(format(\"{:u} \", n));\n"
        "    print(\"x = {:f}\", x);\n"
        "    print(\"plain\");\n"
        "    print(x);\n"
        "}\n",
        ["--compat"])
    check("rc=0", r.returncode == 0, both(r)[-300:])
    check("lit-bool → `true`", "bool ok = true;" in gen, gen[:900])
    check("lit-char → `'A'`", "uint8_t c = 'A';" in gen, gen[:900])
    check("lit-float 保留词法值", "double y = (1e16);" in gen, gen[:900])
    check("lit-string → `elench_str(…（含转义）)`",
          'elench_str("hi\\n")' in gen, gen[:900])
    check("list-init（数字）", "= {5, 3, 9};" in gen, gen[:900])
    check("list-init（文本）",
          '= {elench_str("a"), elench_str("b")};' in gen, gen[:900])
    check("空名构造 `vector<T>::filled(v, n)` → `vector<T>(n, v)`",
          "std::vector<uint64_t>(3, 7)" in gen, gen[:900])
    check("write 不换行、format 折成 elench_join",
          'elench_out(elench_join({elench_u(n), elench_str(" ")}));' in gen, gen[:900])
    check("print 的 `{:f}` 走定点渲染",
          'elench_outln(elench_join({elench_str("x = "), elench_f(x)}));' in gen,
          gen[:900])
    check("纯字面量 print", 'elench_outln(elench_str("plain"));' in gen, gen[:900])
    check("标量单参 print 走泛型输出（浮点按解释器的 repr 口径）",
          "elench_outln(x);" in gen, gen[:900])


# ---------------------------------------------------------------- ⑫ 位运算
def test_bitwise():
    print("[12] 位运算：>> & ^ | << ~")
    r, gen, _ = run_cpp(
        "bits",
        _spec("bit(u64 a, u64 b) -> u64"),
        "u64 bit(u64 a, u64 b) {\n"
        "    u64 x = (a >> 3) & b;\n"
        "    x = x ^ 1;\n"
        "    x = x | 2;\n"
        "    x = x << 1;\n"
        "    x = ~x;\n"
        "    return x;\n"
        "}\n",
        ["--compat"])
    check("rc=0", r.returncode == 0, both(r)[-300:])
    check("`>>`", "(a >> 3)" in gen, gen[:500])
    check("`&`", "& b)" in gen, gen[:500])
    check("`^`", "(x ^ 1)" in gen, gen[:500])
    check("`|`", "(x | 2)" in gen, gen[:500])
    check("`<<`", "(x << 1)" in gen, gen[:500])
    check("`~`", "(~x)" in gen, gen[:500])


# ---------------------------------------------------------------- ⑬ 头文件
def test_headers():
    print("[13] 头文件一次性发全（自检要能过）")
    r, gen, _ = run_cpp(
        "headers",
        _spec("f(vector<u64>& xs) -> u64"),
        "u64 f(vector<u64>& xs) {\n    return sum_of(xs);\n}\n",
        ["--compat"])
    need = ("<cstdint>", "<vector>", "<iostream>", "<map>", "<set>", "<string>",
            "<numeric>", "<algorithm>", "<deque>", "<optional>", "<array>",
            "<cmath>", "<cstdio>")
    for h in need:
        check(f"有 `#include {h}`", f"#include {h}" in gen, gen[:400])
    check("sum_of → std::accumulate",
          "std::accumulate(xs.begin(), xs.end(), uint64_t(0))" in gen, gen[:1200])


# ---------------------------------------------------------------- ⑭ 哈希容器
def _test_gxx():
    """找编译器：**只认显式覆盖点**（`ELENCH_GXX` / `ELENCH_CXX` 的单个路径）。

    为什么不跟 `PATH` 走：本机 MinGW 在系统 `%TEMP%` 下写不了临时文件
    （`Cannot create temporary file … Permission denied`），而测试的源文件
    默认就建在 `%TEMP%` 里。所以下面那段端到端验证只在**人明确配了编译器**
    时跑，并把 `TMP`/`TEMP` 指到工作区内；没配就明说「跳过」——不冒充通过。
    """
    for key in ("ELENCH_GXX", "ELENCH_CXX"):
        raw = (os.environ.get(key) or "").strip().strip('"')
        if raw and os.path.isfile(raw):
            return raw
    return None


def test_unordered_map():
    """`unordered_map<K,V>` → **插入序**适配器（不是 std::map，也不是 std::unordered_map）。

    为什么必须是插入序：解释器 `UnorderedMapVal.keys()` 就是插入序
    （`_HashMapData` 底层是 Python dict）。`std::map` 按键排序 ⇒ 行为对拍必红；
    `std::unordered_map` 的桶序是 libstdc++ 内部实现、不可复现 ⇒ 同样对拍不了。
    适配器只要求键有 `==`（`vector<uint8_t>` 天然有），**不需要 hash**。
    """
    print("[14] unordered_map：发射插入序适配器，而不是 std::map / std::unordered_map")
    tu = ("spec main() -> u64 {\n"
          "    effects { io, alloc };\n"
          "    target { time: { max: O(1) }, };\n"
          "}\n")
    eln = ("u64 main() {\n"
           "    vector<vector<u8>> words;\n"
           '    words.push_back("b");\n'
           '    words.push_back("a");\n'
           '    words.push_back("b");\n'
           '    words.push_back("c");\n'
           "    unordered_map<vector<u8>, u64> m;\n"
           "    for (const auto& w : words) { m[w] += 1; }\n"
           "    vector<vector<u8>> ks = m.keys();\n"
           "    for (u64 i = 0; i < ks.size(); i += 1) { print(ks[i]); }\n"
           "    return 0;\n"
           "}\n")
    r, gen, gen_p = run_cpp("umap", tu, eln, ["--compat"])
    check("rc=0", r.returncode == 0, both(r)[-300:])
    check("类型映射成适配器",
          "elench_umap<std::vector<uint8_t>, uint64_t>" in gen, gen[-1500:])
    check("**不**发射 std::unordered_map",
          "std::unordered_map<" not in gen, gen[:600])
    check("**不**静默换成 std::map",
          "std::map<std::vector<uint8_t>" not in gen, gen[-1500:])
    check("适配器本体发出来了", "class elench_umap" in gen, gen[:3000])
    check("存储是插入序的 deque（不是按键排序的 map）",
          "using storage_type = std::deque<value_type>;" in gen, gen[:3000])
    check("`m[w] += 1` 走 operator[]", "m[w] += 1;" in gen, gen[-1500:])

    # ---- 真的编、真的跑：插入序 b/a/c（若是 std::map 会变成 a/b/c）----------
    gxx = _test_gxx()
    if gxx is None:
        print("  --   没配 ELENCH_GXX：跳过端到端行为验证（不冒充通过）")
    else:
        d = os.path.join(ROOT, "build", "_cpp_gen_umap_%d" % os.getpid())
        try:
            os.makedirs(d, exist_ok=True)
            env = dict(os.environ)
            env["TMP"] = env["TEMP"] = d        # 系统 %TEMP% 会被沙箱拒绝
            # 编译器目录进 PATH：MinGW 的 exe 运行时要 `libstdc++-6.dll` 等 DLL，
            # 只配 `ELENCH_GXX` 的绝对路径并不会把它们放进 PATH（旧写法漏了这一步，
            # 于是 exe 启动失败、stdout 为空，检查会误报"不是插入序"）。
            env["PATH"] = os.path.dirname(gxx) + os.pathsep + env.get("PATH", "")
            exe = os.path.join(d, "m.exe")
            rc = subprocess.run([gxx, "-std=c++17", gen_p, "-o", exe],
                                env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
            check("生成的 C++ 编得过", rc.returncode == 0, (rc.stderr or "")[:400])
            if rc.returncode == 0:
                p = subprocess.run([exe], env=env, capture_output=True)
                # MinGW 的 stdout 是**文本模式**（`\n` → `\r\n`），与门里的
                # `text=True` 同口径：比之前先把 CRLF 归一。
                out = p.stdout.replace(b"\r\n", b"\n")
                check("跑出来是插入序 b/a/c（std::map 会是 a/b/c）",
                      out == b"b\na\nc\n", repr(p.stdout))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # ---- 类型层：越界形状必须**明确报错**，不许静默退化 ---------------------
    sys.path.insert(0, SRC)
    import dsh
    check("类型层：unordered_map<u64,u64> → 适配器",
          dsh._cxx_type_of("unordered_map<u64, u64>")
          == "elench_umap<uint64_t, uint64_t>", "")
    for bad, why in (
        ("unordered_set<u64>", "unordered_set 本轮没做适配器"),
        ("vector<unordered_map<u64, u64>>", "哈希容器嵌在别的容器里"),
        ("unordered_map<Ring, u64>", "struct 键没有 `==` 契约"),
    ):
        try:
            got = dsh._cxx_type_of(bad, {"Ring", "Point"})
        except dsh.CppUnsupported:
            check(f"{why} ⇒ 明确报错", True)
        else:
            check(f"{why} ⇒ 明确报错", False, f"静默生成了 `{got}`")


# ---------------------------------------------------------------- ⑮ insert 按容器分派
#
# 2026-11-13（L0）：`insert` 曾经**不看接收者类型**，一律按 `vector` 的位置插入发射：
#     map<u64,u64> m;  m.insert(97, 11);  →  m.insert(m.begin() + (97), 11);
# g++ 报 `cannot convert std::_Deque_iterator to const unsigned char&`。
# 同一个 `.insert` 在本语言里有多套语义（`interp.py` 是定义）：
#   map.insert(k,v)           不覆盖已有键，返回 1/0
#   unordered_map.insert(k,v) 覆盖，返回 0
#   set.insert(v)             插元素，返回 0
#   vector.insert(pos,v)      位置插入
# 这一节锁四件事：① 生成物文本按容器分派；② 不再出现 `m.begin()+`；
# ③ 配了编译器时**真编**；④ 生成物运行结果 == `dsh run`（解释器）。
_INSERT_TU = (
    "spec main() -> u64 {\n"
    "    effects { io, alloc };\n"
    "    target { time: { max: O(1) }, allocation: { max: O(1) }, };\n"
    "}\n")

_INSERT_ELN = """u64 main() {
    map<u64, u64> m;
    print(m.insert(1, 10));
    print(m.insert(1, 20));
    print(m.get(1));
    print(m.put(1, 99));
    print(m.put(7, 77));
    print(m.get(1));
    unordered_map<u64, u64> h;
    print(h.insert(2, 30));
    print(h.insert(2, 40));
    print(h.get(2));
    print(h.size());
    set<u64> s;
    print(s.insert(5));
    print(s.insert(5));
    print(s.size());
    vector<u64> sv = s.values();
    for (u64 i = 0; i < sv.size(); i += 1) { print(sv[i]); }
    vector<u64> v;
    v.push_back(1);
    v.push_back(3);
    v.insert(1, 2);
    for (u64 i = 0; i < v.size(); i += 1) { print(v[i]); }
    return 0;
}
"""


def _is_int_text(s: str) -> bool:
    t = s[1:] if s[:1] in ("+", "-") else s
    return bool(t) and t.isdigit()


def _strip_run_ret(out: str) -> str:
    """`dsh run` 会把 `main` 的**返回值**打在最后一行——那不是程序 stdout。

    口径与 `tools/behavior_parity.py:strip_return_line` 一致：只剥"最后一行且是
    整数"的情况（程序自己打印的 `0` 在返回值之前，不会被误删）。
    """
    lines = (out or "").splitlines()
    if lines and _is_int_text(lines[-1].strip()):
        lines = lines[:-1]
    return "\n".join(lines)


def test_insert_semantics_by_container():
    print("[15] `insert` 按接收者类型分派：map / unordered_map / set / vector 四套语义")
    r, gen, gen_p = run_cpp("insert_dispatch", _INSERT_TU, _INSERT_ELN, ["--compat"])
    check("rc=0", r.returncode == 0, both(r)[-400:])
    check("map.insert → `elench_map_insert`（不覆盖、返回 1/0）",
          "elench_outln(elench_map_insert(m, 1, 10));" in gen, gen[-1200:])
    check("map.insert **不再**按向量发射（无位置插入）",
          "m.insert(m.begin() + (1), 10)" not in gen and
          "m.insert(m.begin() + (1), 20)" not in gen, gen[-1200:])
    check("map.put → `elench_map_put`（覆盖、返回是否新插入）",
          "elench_outln(elench_map_put(m, 1, 99));" in gen, gen[-1200:])
    check("unordered_map.insert → 适配器的 `insert(k, v)`（覆盖、返回 0）",
          "elench_outln(h.insert(2, 30));" in gen, gen[-1200:])
    check("unordered_map.insert **不再**按向量发射（无位置插入）",
          "h.insert(h.begin() + (2), 30)" not in gen, gen[-1200:])
    check("set.insert → `elench_set_insert`（插元素、返回 0）",
          "elench_outln(elench_set_insert(s, 5));" in gen, gen[-1200:])
    check("vector.insert(pos, v) → 位置版 `insert(begin() + pos, v)`",
          "v.insert(v.begin() + (1), 2);" in gen, gen[-1200:])
    check("set.values() → `elench_elems`（元素本身；不是 pair.first/mapped_type）",
          "elench_elems(s)" in gen, gen[-1200:])
    check("set.values() 不用 `elench_keys` / `elench_values`",
          "elench_keys(s)" not in gen and "elench_values(s)" not in gen, gen[-1200:])

    # ---- 语义：生成物 stdout == 解释器 `dsh run`（剥掉末行返回值）----------
    d, tu_p, ch_p, _out = _write_pair("insert_sem", _INSERT_TU, _INSERT_ELN)
    ir = subprocess.run([sys.executable, DSH, "run", "--compat", tu_p, ch_p],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env=dict(os.environ))
    check("`dsh run` 能跑出基准", ir.returncode == 0,
          (ir.stdout or "")[-300:] + (ir.stderr or "")[-300:])
    want = _strip_run_ret(ir.stdout or "")

    gxx = _test_gxx()
    if gxx is None:
        print("  --   没配 ELENCH_GXX：跳过真实编译与运行对拍（不冒充通过）")
    else:
        dd = os.path.join(ROOT, "build", "_cpp_gen_insert_%d" % os.getpid())
        try:
            os.makedirs(dd, exist_ok=True)
            env = dict(os.environ)
            env["TMP"] = env["TEMP"] = dd      # 系统 %TEMP% 会被沙箱拒绝
            env["PATH"] = os.path.dirname(gxx) + os.pathsep + env.get("PATH", "")
            exe = os.path.join(dd, "insert.exe")
            rc = subprocess.run([gxx, "-std=c++17", "-O0", gen_p, "-o", exe],
                                env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
            check("生成的 C++ 编得过（g++ 真编）", rc.returncode == 0,
                  (rc.stderr or "")[:500])
            if rc.returncode == 0:
                p = subprocess.run([exe], env=env, capture_output=True)
                got = p.stdout.replace(b"\r\n", b"\n").decode("utf-8", "replace")
                check("生成物运行结果 == 解释器 `dsh run`（同逻辑逐行一致）",
                      got.rstrip("\n") == want.rstrip("\n"),
                      "got=%r / want=%r" % (got, want))
        finally:
            shutil.rmtree(dd, ignore_errors=True)


# ---------------------------------------------------------------- ⑯ 分派护栏
def test_container_dispatch_guards():
    """判不出接收者类型 / 定长数组：必须**明确报错**，不许按向量硬套。"""
    print("[16] 分派护栏：判不出类型 / std::array 没有的方法 ⇒ E-Cxx-Unsupported")
    # (a) 接收者是调用结果：静态类型判不出 ⇒ 不知道是"位置插入"还是"插键值对"
    r, _gen, out_p = run_cpp(
        "guard_unknown",
        _spec("f() -> vector<u64>") + _spec("g() -> u64"),
        "vector<u64> f() {\n"
        "    vector<u64> v;\n"
        "    return v;\n"
        "}\n"
        "u64 g() {\n"
        "    f().insert(0, 1);\n"
        "    return 0;\n"
        "}\n",
        ["--compat"])
    check("判不出接收者 ⇒ rc≠0", r.returncode != 0, both(r)[-400:])
    check("判不出接收者 ⇒ 报 E-Cxx-Unsupported",
          "E-Cxx-Unsupported" in both(r), both(r)[-400:])
    check("判不出接收者 ⇒ 不写文件", not os.path.exists(out_p), out_p)

    # (b) `array<T,N>` 定长：std::array 没有 push_back / clear（也没有 insert）。
    #     解释器对 FixedList 同样拒绝改长度 ⇒ 生成期报错是对的。
    #     （`array.assign` 到不了这里：METHOD_TABLE 里 array 本来就没有 assign，
    #       check 期先报 E-Call-UnknownMethod。）
    for meth, call in (("push_back", "a.push_back(2);"),
                       ("clear", "a.clear();")):
        r2, _g2, out2 = run_cpp(
            "guard_array_" + meth,
            _spec("h() -> u64"),
            "u64 h() {\n"
            "    array<u64, 4> a;\n"
            "    " + call + "\n"
            "    return 0;\n"
            "}\n",
            ["--compat"])
        check(f"array.{meth} ⇒ rc≠0", r2.returncode != 0, both(r2)[-400:])
        check(f"array.{meth} ⇒ 报 E-Cxx-Unsupported",
              "E-Cxx-Unsupported" in both(r2), both(r2)[-400:])
        check(f"array.{meth} ⇒ 不写文件", not os.path.exists(out2), out2)


# ---------------------------------------------------------------- ⑰ assign 是 void
def test_assign_is_void():
    """`assign(n, x)` 返回 void（C++ 的 `std::vector::assign` 就是 void）。

    **三方一致**的目标形状（2026-11-13）：

    | 写法 | check | run | dsh cpp |
    |---|---|---|---|
    | `xs.assign(2, 0);` | ✓ | ✓ | ✓（发射 `.assign(2, 0);`） |
    | `vector<u64> ys = xs.assign(2, 0);` | ✗ E-Type-Assign | ✗ | ✗（check 先挡） |
    | `print(xs.assign(2, 0));` | ✗ E-Type-Void | ✗ | ✗（check 先挡） |

    第三行**改过一次口径**（2026-11-13 晚）：`void` 当值用是语言级规则，现在由
    `check` 的 `E-Type-Void` 统一挡（`void` 只能作表达式语句），所以 `dsh cpp`
    在 `_gate_checks` 就返回，**走不到**发射器那句 `E-Cxx-Unsupported`。
    发射器护栏**没有删**（`src/dsh.py` 的 `_cpp_method_call` 里仍在，值位置
    照样明确报错，不许发编不过的表达式）；"护栏还在"由
    `tests/cases/test_void_as_value.py` 的源码断言钉住，不靠这条消息文本。
    """
    print("[17] assign 返回 void：值位置必须被拒，语句位置必须能编")
    # (a) 值位置：check 先挡（rc≠0、不写文件）。
    r, _gen, out_p = run_cpp(
        "assign_value",
        _spec("f() -> u64"),
        "u64 f() {\n"
        "    vector<u64> xs;\n"
        "    vector<u64> ys = xs.assign(2, 0);\n"
        "    return ys.size();\n"
        "}\n",
        ["--compat"])
    blob = both(r)
    check("值位置 assign ⇒ rc≠0", r.returncode != 0, blob[-500:])
    check("值位置 assign ⇒ 报 E-Type-Assign（初值是 void）",
          "E-Type-Assign" in blob and "void" in blob, blob[-500:])
    check("值位置 assign ⇒ 不写文件", not os.path.exists(out_p), out_p)

    # (b) 语句位置：返回值被丢弃，必须照旧发射、照旧能过编译自检。
    r2, gen2, _ = run_cpp(
        "assign_stmt",
        _spec("f() -> u64"),
        "u64 f() {\n"
        "    vector<u64> xs;\n"
        "    xs.assign(2, 0);\n"
        "    return xs.size();\n"
        "}\n",
        ["--compat"])
    check("语句位置 assign ⇒ rc=0", r2.returncode == 0, both(r2)[-500:])
    check("语句位置 assign ⇒ 发射 `.assign(2, 0);`",
          "xs.assign(2, 0);" in gen2, gen2[:600])

    # (c) check 没拦住的"void 当值用"（`print` 的实参）：**发射器**必须报错，
    #     不许把编不过的表达式写到文件里。
    _spec_io = ("spec f() -> u64 { effects { io }; "
                "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n")
    r3, _g3, out3 = run_cpp(
        "assign_print",
        _spec_io,
        "u64 f() {\n"
        "    vector<u64> xs;\n"
        "    print(xs.assign(2, 0));\n"
        "    return xs.size();\n"
        "}\n",
        ["--compat"])
    blob3 = both(r3)
    check("print(assign(...)) ⇒ rc≠0", r3.returncode != 0, blob3[-500:])
    check("print(assign(...)) ⇒ 明确报错（E-Type-Void 或发射器的 E-Cxx-Unsupported）",
          ("E-Type-Void" in blob3) or ("E-Cxx-Unsupported" in blob3), blob3[-500:])
    check("print(assign(...)) ⇒ 不写文件", not os.path.exists(out3), out3)


def main() -> int:
    test_path_init()
    test_param_const()
    test_else()
    test_unknown_type()
    test_checker_gate()
    test_compile_selfcheck()
    test_question_type_by_position()
    test_statements()
    test_method_calls()
    test_struct()
    test_literals_and_printf()
    test_bitwise()
    test_headers()
    test_unordered_map()
    test_insert_semantics_by_container()
    test_container_dispatch_guards()
    test_assign_is_void()
    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
