#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_ternary.py —— 三目 `c ? a : b` 与 C 风格强转的诊断（2026-11-13）

## 为什么这两个是"闸门"

三个库风格的 C++ 程序（`demo/probe-libs` 的 `skiplist` / `ring` / `pool`）**每一个**
都同时用到两样东西：

| 形状 | 改前 | 后果 |
|---|---|---|
| `c ? a : b` | `E-Parse-Expected`（`?` 只被当成 `x?` 的 try 后缀） | 解析就停 ⇒ check / κ / `dsh cpp` / 对拍**全归零** |
| `(int)xs.size()` | `E-Cast-CStyle` | 同上 |

所以这一轮的验收不是"多了一种语法"，而是"整条链路能走通"：

    解析 → 类型 → 解释器 → κ（含 max 语义）→ `dsh cpp` 发射 → g++ 编译 → 行为对拍

## 断言（八组）

1. **解析**：优先级（在 `||` 之下、`+` 之下）、**右结合**、与 `x?`（try）的消歧；
2. **类型**：条件与 `if` 同一口径（bool / 指针；`--compat` 下整数）；
   两支必须**相容**，不相容报 `E-Type-CondBranches`（**不许猜**）；
3. **解释器：只求值一支**（副作用探针——两支各自 push 一个不同的值，
   跑完看 log 里到底有几个元素）；
4. **κ 取两支的 max**（**不是相加**）：用"两支都是 O(n)、系数不同"的探针，
   让 max 与 sum 的差随规模线性增长，再断言差值**是常数**；
5. **κ 仍是上界**：解释器实测 ops ≤ κ（4 个规模 × 两个分支）；
6. **等价性**：`c ? a : b` 与 `if/else` 的 κ 与 run 对比（差在哪就写清在哪）；
7. **C 风格强转**：仍然拒绝，但消息必须给出**这一处**可复制的 `as` 改法；
8. **后端**：`dsh cpp` 发射 `?:`；有编译器时真编、真跑、与解释器逐字节对拍
   （没有编译器就明确说"跳过"，绝不冒充通过）。

跑法：`python -B tests/cases/test_ternary.py`（退出码 0 即通过）。
"""

import contextlib
import io as _io
import os
import shutil
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
for _p in (SRC,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                            # noqa: BLE001
    pass

import dsh                                               # noqa: E402
import interp                                            # noqa: E402
from cost_check import evaluate                          # noqa: E402
from cost_interp import CostInterp                       # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset  # noqa: E402
from parser import parse_source                          # noqa: E402

DSH = os.path.join(SRC, "dsh.py")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

FAILED = []


def check(ok, name, detail=""):
    if ok:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


# ---------------------------------------------------------------- 工具
def parse(src, name="m.eln"):
    return parse_source(src, name).module


def kappas(ch, tu=None):
    ci = CostInterp(ch, tu, table_path=None)
    return ci, {nm: ci.cost_of(nm) for nm in ci.fns}


def first_expr(mod, fn="f"):
    """`fn` 体的第一条语句的第一个子表达式（探针里都是 `return <expr>;`）。"""
    node = next(c for c in mod.children if getattr(c, "name", "") == fn)
    blk = next(c for c in node.children if c.kind == "block")
    return blk.children[0].children[0]


def ops_of_call(ch, tu, fn, args):
    """跑一次：返回 `(stdout, repr(返回值), ops)`。`ops` 是整棵树的节点数。"""
    old = interp._COUNT
    interp.ops_enable(True)
    buf = _io.StringIO()
    try:
        ops_reset()
        it = Interp(ch, tu, table_path=None)
        with contextlib.redirect_stdout(buf):
            val = it.call(fn, args)
        return buf.getvalue(), repr(val), ops_of(None)
    finally:
        interp.ops_enable(old)


def _write(d, name, text):
    with _io.open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def dsh_run(src, tu=None, cmd="check", compat=False):
    """写一对模块跑一次 `dsh <cmd>`，返回 `(rc, 合并输出)`。"""
    d = tempfile.mkdtemp(prefix="elench-tern-")
    try:
        _write(d, "m.eln", src)
        if tu is not None:
            _write(d, "m.tu", tu)
        argv = [sys.executable, "-B", DSH, cmd, "m"] + (["--compat"] if compat else [])
        r = subprocess.run(argv, cwd=d, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=ENV)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    finally:
        shutil.rmtree(d, ignore_errors=True)


#: 探针模块：三目的四类用途各一个函数。
#:
#: `scan_a` / `scan_b` 都是 O(n(xs)) 但**系数不同**（7 vs 9）——这是第 4 组断言
#: 的关键：如果 κ 把两支**相加**，κ 的斜率就是 `7+9=16`；取 max 则是 `max(7,9)=9`。
#: 常数因子在这里是**可测的差**，不是四舍五入看不出来的东西。
PROBE = """
pub u64 scan_a(const vector<u64>& xs) {
    u64 s = 0;
    for (const auto& x : xs) { s = s + x; }
    return s;
}

pub u64 scan_b(const vector<u64>& xs) {
    u64 s = 1;
    for (const auto& x : xs) { s = s * 1 + x; }
    return s;
}

pub u64 only_a(const vector<u64>& xs) { return scan_a(xs); }
pub u64 only_b(const vector<u64>& xs) { return scan_b(xs); }

pub u64 tern_ab(bool c, const vector<u64>& xs) { return c ? scan_a(xs) : scan_b(xs); }

pub u64 tern_eq(bool c, u64 a, u64 b) { return c ? a : b; }
pub u64 if_eq(bool c, u64 a, u64 b) { if (c) { return a; } else { return b; } }

pub u64 left_tap(vector<u64>& log, u64 v) { log.push_back(v); return v; }
pub u64 right_tap(vector<u64>& log, u64 v) { log.push_back(v); return v; }

pub u64 side(bool c, vector<u64>& log) {
    u64 r = c ? left_tap(log, 1) : right_tap(log, 2);
    return r;
}

pub u64 main() {
    vector<u64> xs;
    xs.push_back(1); xs.push_back(2); xs.push_back(3);
    vector<u64> log;
    u64 a = side(true, log);
    u64 b = side(false, log);
    // **把副作用打印出来**：`side` 的两支各 push 一个不同的值，
    // 所以这两行 stdout 同时钉住"只求值一支"（log.size() 必须是 2）。
    print(log.size());
    print(log[0]);
    print(log[1]);
    return tern_ab(true, xs) + tern_eq(true, 7, 9) + if_eq(false, 7, 9) + a + b;
}
"""


# ---------------------------------------------------------------- [1] 解析
def test_parse():
    print("[1] 解析：优先级 / 右结合 / 与 `x?` 的消歧")
    r = parse_source("pub u64 f(bool c, u64 a, u64 b) { return c ? a : b; }", "p.eln")
    check(not r.diagnostics, "`c ? a : b` 解析通过", [d.code for d in r.diagnostics])
    t = first_expr(r.module)
    check(getattr(t, "kind", "") == "ternary",
          "AST 是 ternary 节点", getattr(t, "kind", ""))
    check([c.kind for c in (t.children if t is not None else [])]
          == ["path", "path", "path"],
          "三个子节点 = 条件 / 真支 / 假支", [c.kind for c in (t.children or [])])

    # 优先级：三目在 `||` 之下 —— `a || b ? a : c` 的条件是整条 `a || b`
    r = parse_source("pub bool f(bool a, bool b, bool c) "
                     "{ return a || b ? a : c; }", "p.eln")
    t = first_expr(r.module)
    cond = t.children[0] if t is not None and t.children else None
    check(getattr(cond, "kind", "") == "binary" and getattr(cond, "value", "") == "||",
          "`a || b ? a : c` 的条件是 `a || b`（三目比 `||` 松）",
          getattr(cond, "kind", ""))

    # 优先级：三目在 `+` 之下 —— `a + b ? c : d` 的条件是 `a + b`
    r = parse_source("pub u64 f(bool c, u64 a, u64 b, u64 d) "
                     "{ return a + b ? a : d; }", "p.eln")
    t = first_expr(r.module)
    cond = t.children[0] if t is not None and t.children else None
    check(getattr(cond, "kind", "") == "binary" and getattr(cond, "value", "") == "+",
          "`a + b ? a : d` 的条件是 `a + b`（三目比算术松）",
          getattr(cond, "kind", ""))

    # 右结合：`a ? b : c ? d : e` == `a ? b : (c ? d : e)`
    r = parse_source("pub u64 f(bool a, bool c, u64 b, u64 d, u64 e) "
                     "{ return a ? b : c ? d : e; }", "p.eln")
    t = first_expr(r.module)
    els = t.children[2] if t is not None and len(t.children) > 2 else None
    check(getattr(els, "kind", "") == "ternary",
          "`a ? b : c ? d : e` 的 else 支仍是 ternary（右结合）",
          getattr(els, "kind", ""))
    check(getattr(t.children[1], "kind", "") == "path",
          "中支不会被抢先吃掉", getattr(t.children[1], "kind", ""))

    # 与 try 的消歧：`x?` 仍然解析成 try（**回归**：这条不能被三目吃掉）
    r = parse_source("pub u64 f() -> u64! { return 1; }\n"
                     "pub u64 g() -> u64! { return f()?; }", "p.eln")
    check(not r.diagnostics, "`f()?`（try 后缀）仍解析通过",
          [d.code for d in r.diagnostics])
    g = [c for c in r.module.children if getattr(c, "name", "") == "g"][0]
    ret = first_expr(r.module, "g")
    check(getattr(ret, "kind", "") == "try", "`f()?` 的 AST 是 try 节点",
          getattr(ret, "kind", ""))
    _ = g

    # 与 try 的消歧：`f()? + 1` 里的 `?` 仍是 try（三目的试解必须回退干净）
    r = parse_source("pub u64 f() -> u64! { return 1; }\n"
                     "pub u64 g() -> u64! { return f()? + 1; }", "p.eln")
    check(not r.diagnostics, "`f()? + 1` 仍解析通过（试解回退）",
          [d.code for d in r.diagnostics])

    # 三目出现在**各种值位置**：实参 / 下标 / 括号 / 表达式语句 / 条件
    shapes = [
        ("pub u64 h(u64 x) { return x; }\n"
         "pub u64 f(bool c, u64 a, u64 b) { return h(c ? a : b); }", "实参位置"),
        ("pub u64 f(bool c, u64 a, u64 b, const vector<u64>& xs) "
         "{ return xs[c ? a : b]; }", "下标位置"),
        ("pub u64 f(bool c, u64 a, u64 b) { u64 x = (c ? a : b); return x; }",
         "括号里"),
        ("pub u64 h(u64 x) { return x; }\n"
         "pub u64 g(u64 x) { return x; }\n"
         "pub u64 f(bool c, u64 a) { c ? h(a) : g(a); return 0; }", "表达式语句"),
        ("pub u64 f(bool c, u64 a, u64 b) { if (c ? a > b : false) { return a; } "
         "return b; }", "if 的条件里"),
    ]
    for src, name in shapes:
        r = parse_source(src, "p.eln")
        check(not r.diagnostics, f"三目在{name}：解析通过",
              [d.code for d in r.diagnostics])


# ---------------------------------------------------------------- [2] 类型
def test_types():
    print("[2] 类型：条件口径与两支相容（不相容 ⇒ E-Type-CondBranches）")
    rc, out = dsh_run("pub u64 f(bool c, u64 a, u64 b) { return c ? a : b; }")
    check(rc == 0, "两支同为 u64：check 通过", out[-300:])
    rc, out = dsh_run("pub f64 f(bool c, u64 a) { return c ? a : 0.5; }")
    check(rc == 0, "两支 u64 / f64（相容的数值）：check 通过", out[-300:])

    rc, out = dsh_run('pub u64 f(bool c) { return c ? 1 : "x"; }')
    check(rc != 0 and "E-Type-CondBranches" in out,
          "`c ? 1 : \"x\"` 报 E-Type-CondBranches（不许猜一支）", out[-400:])
    check("as" in out, "消息给出 `as` 的改法", out[-400:])

    rc, out = dsh_run("pub u64 f(u64 n, u64 a) { return n ? a : 0; }")
    check(rc != 0 and "E-Type-Cond" in out,
          "条件不是 bool/指针：报 E-Type-Cond（与 if 同一条判据）", out[-300:])
    rc, out = dsh_run("pub u64 f(u64 n, u64 a) { return n ? a : 0; }", compat=True)
    check(rc == 0, "`--compat` 下整数条件与 `if (n)` 同一口径（通过）", out[-300:])

    rc, out = dsh_run("pub () g() { }\npub u64 f(bool c) { return c ? g() : 1; }")
    check(rc != 0 and "E-Type-Void" in out,
          "有一支是 void：报 E-Type-Void（值位置不收 void）", out[-300:])


# ---------------------------------------------------------------- [3] 只求值一支
def test_one_branch_only():
    print("[3] 解释器：**只求值走的那一支**（副作用探针）")
    ch = parse(PROBE)
    for c, want_val, want_tag in ((True, 1, "left_tap 的 1"), (False, 2, "right_tap 的 2")):
        log = []
        _out, val, _ops = ops_of_call(ch, None, "side", [c, log])
        check(val == str(want_val), f"c={c}：返回值来自 {want_tag}", f"{val} != {want_val}")
        check(len(log) == 1,
              f"c={c}：log 里**只有 1 个**元素（另一支没执行）", log)
        if len(log) == 1:
            check(int(log[0]) == want_val,
                  f"c={c}：被 push 的正是 {want_tag}", log)
    # 另一支**一个副作用都没有**：两支都 push，只有一支跑 ⇒ 两次调用共 2 个元素
    log = []
    ops_of_call(ch, None, "side", [True, log])
    ops_of_call(ch, None, "side", [False, log])
    check(len(log) == 2, "依次 true / false：log 恰好 2 个元素（各跑一支）", log)


# ---------------------------------------------------------------- [4] κ = max
def test_kappa_max():
    print("[4] κ = 1 + κ(cond) + **max**(两支)（不是相加）")
    ch = parse(PROBE)
    _ci, ks = kappas(ch)
    s_tern = ks["tern_ab"].render()
    s_a, s_b = ks["only_a"].render(), ks["only_b"].render()
    print(f"       κ(scan_a)  = {ks['scan_a'].render()}")
    print(f"       κ(scan_b)  = {ks['scan_b'].render()}")
    print(f"       κ(only_a)  = {s_a}")
    print(f"       κ(only_b)  = {s_b}")
    print(f"       κ(tern_ab) = {s_tern}")
    check("max(" in s_tern, "κ 里出现 `max(...)`（两支是择一）", s_tern)
    check(s_tern.count("n(xs)") == 2,
          "两支各自的 `n(xs)` 都在 max 里（两个规模项，不是一个）", s_tern)

    # **可测的判据**：`tern_ab` 与"只调最贵那一支"的 κ 之差必须是**常数**。
    # 取 max ⇒ 差 = 3 + B - (1 + B) = 2（节点 + 条件）；取相加 ⇒ 差 = 2 + A = Θ(n)，
    # 规模一变大就露馅。这里直接对 4 个规模求值，看差值是不是 2。
    pts = (1, 4, 16, 64)
    diffs = [evaluate(ks["tern_ab"], {"xs": n}) - evaluate(ks["only_b"], {"xs": n})
             for n in pts]
    print(f"       κ(tern_ab) - κ(only_b) 在 n = {pts} 上 = {diffs}")
    check(all(d == diffs[0] for d in diffs),
          "与「只调最贵那一支」的差**与规模无关**（= max，不是相加）", diffs)
    check(diffs[0] == 2, "差值恰好 2（三目节点 1 + 条件 1）", diffs[0])

    # 相加会是什么样：直接算出来给读者看，并断言它**不成立**
    sums = [evaluate(ks["only_a"], {"xs": n}) + evaluate(ks["only_b"], {"xs": n})
            - 2 - evaluate(ks["only_b"], {"xs": n}) for n in pts]
    check(not all(s == sums[0] for s in sums),
          "反证：把两支相加的话，差会随规模增长（这条**必须**不成立）", sums)

    # 两支成本不同时，κ 取的是**贵的那支**（不是便宜的那支）
    vals = {n: evaluate(ks["tern_ab"], {"xs": n}) for n in pts}
    cheap = {n: evaluate(ks["scan_a"], {"xs": n}) + 3 for n in pts}
    check(all(vals[n] > cheap[n] for n in pts),
          "κ(tern_ab) 用的是贵的 `scan_b`（取 max，不是取 min/左支）", (vals, cheap))


# ---------------------------------------------------------------- [5] ops ≤ κ
def test_ops_le_kappa():
    print("[5] κ 仍是上界：解释器实测 ops ≤ κ（4 个规模 × 两个分支）")
    ch = parse(PROBE)
    _ci, ks = kappas(ch)
    for n in (4, 16, 64, 256):
        xs = list(range(1, n + 1))
        for c in (True, False):
            _o, _v, ops = ops_of_call(ch, None, "tern_ab", [c, xs])
            kv = evaluate(ks["tern_ab"], {"xs": n})
            check(ops <= kv, f"n={n} c={c}：ops {ops} ≤ κ {kv:.0f}",
                  f"ops={ops} κ={kv}")


# ---------------------------------------------------------------- [6] 等价性
def test_equivalence():
    print("[6] 等价性：`c ? a : b` 与 `if/else`")
    ch = parse(PROBE)
    _ci, ks = kappas(ch)
    kt, ki = ks["tern_eq"].render(), ks["if_eq"].render()
    print(f"       κ(tern_eq) = {kt}")
    print(f"       κ(if_eq)   = {ki}")
    check(kt == ki, "κ 逐字节相同", f"{kt} vs {ki}")
    # run：返回值 + ops
    for c in (True, False):
        _o1, vt, ops_t = ops_of_call(ch, None, "tern_eq", [c, 7, 9])
        _o2, vi, ops_i = ops_of_call(ch, None, "if_eq", [c, 7, 9])
        print(f"       c={c}: tern_eq → {vt} (ops {ops_t}) / if_eq → {vi} (ops {ops_i})")
        check(vt == vi, f"c={c}：返回值逐字节相同", f"{vt} vs {vi}")
        # **ops 也相同**：解释器的计数单位是「语句 + 表达式节点」，`if` 的
        # 分支块（block）本身不计数——所以两种写法的节点数恰好一样。
        # 这条是实测结论（不是推测）：改前我以为 if 多一个 block，跑出来是 4 / 4。
        check(ops_t == ops_i, f"c={c}：ops 逐字节相同", f"{ops_t} vs {ops_i}")


# ---------------------------------------------------------------- [7] C 风格强转
def test_c_style_cast():
    print("[7] C 风格强转：仍拒绝，但消息给出**这一处**可复制的改法（三条路）")
    probe = "pub u64 f(const vector<u64>& xs) { return (int)xs.size(); }"
    r = parse_source(probe, "c.eln")
    codes = [d.code for d in r.diagnostics]
    check(codes == ["E-Cast-CStyle"], "报 E-Cast-CStyle（选择：继续拒绝）", codes)
    msg = r.diagnostics[0].message if r.diagnostics else ""
    print("       " + msg.replace("\n", "\n       "))
    check("`xs.size() as i32`" in msg,
          "改法 C：消息里有**照抄就能用**的一行：`xs.size() as i32`", msg)
    check("`xs.size()`" in msg and "改法 A" in msg,
          "改法 A：不需要转换时**删掉**强转（真实用例多数如此）", msg)
    check("`i32 n = xs.size();`" in msg,
          "改法 B：带类型的中间变量（全链路可用）", msg)
    check("dsh cpp" in msg and "不发射" in msg,
          "消息如实说 `dsh cpp` 不发 `as`（否则改完 C 还会撞第二次）", msg)
    check("i32" in msg, "C++ 的 `int` 映射到本语言 `i32`（宽度与符号不改）", msg)
    check("只有一种写法" in msg, "消息说清「为什么不是两套写法」", msg)

    # **改法 B 真的能过整条链路**（check / 发射）：不是"看起来像"+ 空口承诺。
    fix_b = ("pub u64 f(const vector<u64>& xs) {\n"
             "    i32 n = xs.size();\n"
             "    return n;\n"
             "}")
    rc, out = dsh_run(fix_b)
    check(rc == 0, "改法 B 的产物 check 通过", out[-300:])
    try:
        text = dsh._gen_cpp(None, parse(fix_b))
        check("int32_t n = xs.size();" in text,
              "改法 B 的产物能被 `dsh cpp` 发射（`int32_t n = xs.size();`）", "")
    except dsh.CppUnsupported as exc:                      # noqa: BLE001
        check(False, "改法 B 的产物能被 `dsh cpp` 发射", str(exc))

    # 改法 A：`(a) * b` 这一类**合法形状不受影响**
    r = parse_source("pub u64 f(u64 a, u64 b) { return (a) * b; }", "c.eln")
    check(not r.diagnostics, "`(a) * b` 仍是乘法（括号表达式，不是强转）",
          [d.code for d in r.diagnostics])


# ---------------------------------------------------------------- [8] 后端
def _compile_and_run(text):
    """编译生成物并执行：返回 `(stdout, rc, 错误说明)`。

    **TMP / TEMP 指到源文件所在目录**：本机实测 MinGW 在系统 `%TEMP%` 下会
    `Cannot create temporary file`（Permission denied）——`dsh` 自己的
    `_cpp_compile_text` 也是这么做的（`dsh.py` 的注释）。
    """
    comp = dsh._cxx_compiler_argv()
    if comp is None:
        return None, None, "没有编译器"
    d = tempfile.mkdtemp(prefix="elench-tern-exe-")
    try:
        src = os.path.join(d, "m.cpp")
        _write(d, "m.cpp", text)
        exe = os.path.join(d, "m.exe" if os.name == "nt" else "m.out")
        env = dict(os.environ)
        env["TMP"] = env["TEMP"] = d
        # **编译器目录前置进 PATH**（与 `tools/cpp_parity.py` 的 `child_env` 同一条）：
        # MinGW 的 exe 运行时要 `libstdc++-6.dll` / `libgcc_s_seh-1.dll`，
        # 它们就在 g++.exe 旁边；不加 PATH 时进程会被系统以
        # `0xC0000135`（DLL 找不到）杀掉——那是环境问题，不是生成物的问题。
        _bindir = os.path.dirname(comp[0])
        if _bindir:
            env["PATH"] = _bindir + os.pathsep + env.get("PATH", "")
        r = subprocess.run(comp + ["-std=c++17", src, "-o", exe],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env)
        if r.returncode != 0:
            return None, None, (r.stderr or r.stdout)[:400]
        run = subprocess.run([exe], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", env=env)
        return run.stdout, run.returncode, ""
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_cpp_backend():
    print("[8] 后端 `dsh cpp`：发射 `?:`，有编译器就真编真跑")
    ch = parse(PROBE)
    text = dsh._gen_cpp(None, ch)
    check("max(" not in text, "生成物里没有把 max 发射成 C++（那是 κ 的形状，不是源码）", "")
    check("(c ? scan_a(xs) : scan_b(xs))" in text,
          "`return c ? a : b;` 发射成 `(c ? scan_a(xs) : scan_b(xs))`", "")

    # 具体形状（含运算符优先级：外层自带括号，与 AST 一一对应）
    ch2 = parse("pub u64 classify(u64 n) { return n > 10 ? n * 2 : n + 1; }")
    text2 = dsh._gen_cpp(None, ch2)
    check("((n > 10) ? (n * 2) : (n + 1))" in text2,
          "`n > 10 ? n * 2 : n + 1` 发射成 `((n > 10) ? (n * 2) : (n + 1))`", "")

    if dsh._cxx_compiler_argv() is None:
        print("       -- 跳过编译/对拍：本机没有 g++ / clang++ / cl（ELENCH_GXX 可指定）")
        return

    # 1) 语法自检（`dsh cpp` 的最后一关，与 CLI 同一个函数）
    status, detail = dsh._cpp_compile_text(text)
    check(status == "ok", f"生成的 C++ 能编译（{detail}）", str(detail)[:300])

    # 2) **行为对拍**：真编译、真执行，与解释器比 stdout + 返回值。
    #    注：`dsh cpp --run` 目前只做到"编译"（`_cpp_compare` 没有跑对拍），
    #    所以这里自己做——这正是验收要的那一步。
    out_cpp, rc_cpp, err = _compile_and_run(text)
    if out_cpp is None:
        check(False, "生成物能编译并跑起来", str(err)[:400])
        return
    out_int, val_int, _ops = ops_of_call(ch, None, "main", [])
    print(f"       解释器：stdout={out_int!r} 返回={val_int}")
    print(f"       C++   ：stdout={out_cpp!r} exit={rc_cpp}")
    check(out_cpp == out_int, "stdout 逐字节相同", f"{out_cpp!r} vs {out_int!r}")
    check(str(rc_cpp) == val_int, "返回值（C++ 的 exit code）逐字节相同",
          f"{rc_cpp} vs {val_int}")


if __name__ == "__main__":
    test_parse()
    test_types()
    test_one_branch_only()
    test_kappa_max()
    test_ops_le_kappa()
    test_equivalence()
    test_c_style_cast()
    test_cpp_backend()
    print()
    if FAILED:
        print(f"> 失败 {len(FAILED)} 项")
        for x in FAILED:
            print(f"  - {x}")
        sys.exit(1)
    print("> 全部通过")
