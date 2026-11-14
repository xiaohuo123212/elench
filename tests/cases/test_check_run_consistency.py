#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_check_run_consistency.py —— **check 绿、run 崩** 的回归网。

## 这一批修的是什么

「名字写着能用、跑起来才炸」比缺功能严重得多。本轮（2026-11-12）逐个钉住：

| # | 形状 | 改前 | 改后 |
|---|---|---|---|
| 1 | `S x = {1, 2};`（C++ 源码，struct 与实现同一个 `.eln`，`--compat`） | check ✓ / run ✗ | **支持**：与 `S { a: 1, b: 2 }` 的 κ 与 run **逐字节相同** |
| 2 | `S x = {1, 2};`（struct 在 `.tu`，parser 看不见字段顺序） | check ✓ / run ✗ | **拒绝**：`E-Struct-PosInitUnsupported`（含字段顺序与两条改法） |
| 3 | `S x = {1};` / `{1,2,3}` / `{}` | check ✓ / run ✗ | **拒绝**：`E-Compat-AggregateArity`（个数必须等于字段数） |
| 4 | `u64 x = {1};` / `f64` / `bool` / 枚举 / 元组 / `map` / `set` / `stack` / `queue` / `T*` | check ✓ / run ✗ | **拒绝**：`E-Init-BraceUnsupported`（大括号只对三种容器成立） |
| 5 | `xs.a` / `n.a` / `"ab".a`（对非 struct/元组取字段） | check ✓ / run ✗ | **拒绝**：`E-Type-UnknownField` |
| 6 | `(nope) as u64`（`as` 的操作数以前完全不检查） | check ✓ / run ✗ | **拒绝**：`E-Name-Undefined` |
| 7 | `t.Num`（枚举**值**上用 `.` 取变体） | check ✓ / run 静默 0 | **拒绝**：`E-Member-WrongSep` |
| 8 | `fold(xs, 0, f)` 的 `f` 只有 1 个形参 | check ✓ / run ✗ | **拒绝**：`E-Type-HofCallback` |
| 9 | `map(xs, g)`（`g` 是 `func(…)` 形参）/ `map(xs, f(1))` | check ✓ / run ✗ | **拒绝**：`E-Type-HofCallback` |

## 验收口径（两条都要）

1. **等价性**（只对 #1）：`{1,2}` 与字段式的 `cost --signature` 整份 JSON 与
   `run` 输出**逐字节相同**。
2. **结论一致**：对上面**每一个形状**，`check` 与 `run` 的结论必须一致
   （要么都过、要么都在检查期被挡住）——**不许再出现 check rc=0 / run rc≠0**。
   被拒绝的形状还额外断言 `run` 的输出里**没有 `InterpError`**（说明它是在
   `_gate_checks` 被挡下的，不是跑起来才炸的）。

跑法：`python tests/cases/test_check_run_consistency.py`，退出码 0 即通过。
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DSH = os.path.join(ROOT, "src", "dsh.py")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name}  {detail}")


def _write(d: str, name: str, text: str) -> None:
    with io.open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _invoke(root: str, cmd: list) -> tuple:
    """在已写好 `m.eln` / 可选 `m.tu` 的目录里跑一次 dsh，返回 (rc, 合并输出)。"""
    r = subprocess.run([sys.executable, "-B", DSH] + cmd, cwd=root,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=ENV)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _module(eln: str, tu: str = None, compat: bool = False) -> tuple:
    """写一对模块并跑 `check` 与 `run`，返回 ((check_rc, out), (run_rc, out))。"""
    d = tempfile.mkdtemp(prefix="elench-consistency-")
    try:
        _write(d, "m.eln", eln)
        if tu is not None:
            _write(d, "m.tu", tu)
        flag = ["--compat"] if compat else []
        return _invoke(d, ["check", "m"] + flag), _invoke(d, ["run", "m"] + flag)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _both_green(name: str, eln: str, tu: str = None, compat: bool = False) -> str:
    """形状必须「两边都过」；返回 run 输出（便于再断言具体值）。"""
    (cr, cout), (rr, rout) = _module(eln, tu, compat)
    check(f"{name}：check rc=0", cr == 0, cout[-300:])
    check(f"{name}：run rc=0", rr == 0, rout[-300:])
    return rout


def _both_red(name: str, eln: str, tu: str = None, compat: bool = False,
              code: str = "") -> None:
    """形状必须「两边都红」——而且 run 的红必须是**检查挡下的**，不是 InterpError。"""
    (cr, cout), (rr, rout) = _module(eln, tu, compat)
    check(f"{name}：check rc≠0", cr != 0, cout[-300:])
    check(f"{name}：run rc≠0", rr != 0, rout[-300:])
    check(f"{name}：check 给出 {code or '诊断'}", (code in cout) if code else True,
          cout[-300:])
    check(f"{name}：run 不是“跑起来才炸”", "InterpError" not in rout, rout[-300:])


SPEC_MAIN = ("spec main() -> u64 {\n"
             "    effects {};\n"
             "    target { time: { max: O(1) }, allocation: { max: 0 }, };\n"
             "}\n")


def _sig(eln: str, tu: str = None, compat: bool = False) -> tuple:
    """`cost --signature` 的原始 JSON 文本（整份比较，不只看数字）。"""
    d = tempfile.mkdtemp(prefix="elench-consistency-")
    try:
        _write(d, "m.eln", eln)
        if tu is not None:
            _write(d, "m.tu", tu)
        flag = ["--compat"] if compat else []
        rc, out = _invoke(d, ["cost", "m", "--signature"] + flag)
        return rc, out
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 1. 支持 + 等价

PROBE_ELN = """struct S { u64 a; u64 b; };
u64 main() { S x = {1, 2}; return x.a + x.b; }
"""

PROBE_FIELD_ELN = """struct S { u64 a; u64 b; };
u64 main() { S x = S { a: 1, b: 2 }; return x.a + x.b; }
"""


def _last_payload_line(out: str) -> str:
    """`run` 合并输出里最后一条**程序输出**（跳过 `·` 开头的说明行）。"""
    lines = [l.strip() for l in out.splitlines()
             if l.strip() and not l.strip().startswith("·")]
    return lines[-1] if lines else ""


def test_supported_equivalent() -> None:
    print("[1] `S x = {1, 2};`（--compat，struct 同文件）：支持，且与字段式等价")
    out = _both_green("用户探针（check/run 都过）", PROBE_ELN, SPEC_MAIN,
                      compat=True)
    check("run 输出 = 3", _last_payload_line(out) == "3", out[-160:])

    # 等价性：κ 的整份 signature 与 run 输出逐字节相同
    r1, k1 = _sig(PROBE_ELN, SPEC_MAIN, compat=True)
    r2, k2 = _sig(PROBE_FIELD_ELN, SPEC_MAIN, compat=True)
    check("两种写法 cost --signature 都 rc=0", r1 == 0 and r2 == 0, f"{r1}/{r2}")
    check("两种写法 κ（整份 JSON）逐字节相同", k1 == k2,
          f"\n--- 位置式 ---\n{k1}\n--- 字段式 ---\n{k2}")
    try:
        _kv = json.loads(k1)["functions"]["main"]["cost"]["value"]
    except Exception as exc:                         # noqa: BLE001
        _kv = f"<JSON 解析失败：{exc}>"
    check("κ 不再是列表初始化的 12（= 字段式的 8）", _kv == 8, k1[-260:])

    _, o1 = _module(PROBE_ELN, SPEC_MAIN, compat=True)[1]
    _, o2 = _module(PROBE_FIELD_ELN, SPEC_MAIN, compat=True)[1]
    check("两种写法 run 输出逐字节相同", o1 == o2, f"{o1!r} vs {o2!r}")

    # 结构体字段类型也照常校验：`{1, 2}` 落进 bool 字段 → 检查期报错
    _both_red("类型不符 `struct S { u64 a; bool b; }` + `{1, 2}`",
              "struct S { u64 a; bool b; };\n"
              "u64 main() { S x = {1, 2}; return x.a; }\n",
              compat=True, code="E-Type-FieldMismatch")

    # 直接初始化 `S x{1, 2};` 仍是原来的支持路径（不许被本次改动带坏）
    _both_green("`S x{1, 2};`（原有路径不回归）",
                "struct S { u64 a; u64 b; };\n"
                "u64 main() { S x{1, 2}; return x.a + x.b; }\n",
                compat=True)


def test_arity() -> None:
    print("[2] 位置式个数不符：检查期就必须报（不能等到 run）")
    for vals in ("{1}", "{1, 2, 3}", "{}"):
        _both_red(f"`S x = {vals};`", (
            "struct S { u64 a; u64 b; };\n"
            f"u64 main() {{ S x = {vals}; return x.a + x.b; }}\n"),
            compat=True, code="E-Compat-AggregateArity")


# ---------------------------------------------------------------- 2. 明确拒绝

def test_struct_cross_file() -> None:
    print("[3] struct 在 `.tu`（字段顺序 parser 看不见）：明确拒绝，绝不猜顺序")
    tu = "struct S { a: u64, b: u64, }\n" + SPEC_MAIN
    eln = "u64 main() { S x = {1, 2}; return x.a + x.b; }\n"
    _both_red("非 --compat + struct 在 .tu", eln, tu,
              code="E-Struct-PosInitUnsupported")
    _both_red("--compat + struct 在 .tu", eln, tu, compat=True,
              code="E-Struct-PosInitUnsupported")


def test_other_brace_targets() -> None:
    print("[4] 其它的 `T x = {…}`：大括号只对 vector/array/FixedVec 成立")
    cases = (
        ("u64 `{1}`", "u64 x = {1};", "u64", ""),
        ("f64 `{1.5}`", "f64 x = {1.5};", "u64", ""),
        ("bool `{1}`", "bool b = {1};", "u64", ""),
        ("枚举 `{1}`", "E e = {1};", "u64",
         "enum E { A, B }\n"),
        ("元组 `{1,2}`", "(u64, u64) t = {1, 2};", "u64", ""),
        ("map `{}`", "map<u64, u64> m = {};", "u64", ""),
        ("set `{}`", "set<u64> s = {};", "u64", ""),
        ("stack `{}`", "stack<u64> st = {};", "u64", ""),
        ("queue `{}`", "queue<u64> q = {};", "u64", ""),
        ("指针 `{}`", "u64* p = {};", "u64", ""),
    )
    for label, decl, ret, pre in cases:
        tu = pre + SPEC_MAIN
        eln = f"u64 main() {{ {decl} return 0; }}\n"
        _both_red(f"`{label}`", eln, tu, code="E-Init-BraceUnsupported")

    # 三种容器仍然照常支持（不能因为收紧而误伤）
    _both_green("`vector<u64> xs = {1,2}` 仍支持",
                "u64 main() { vector<u64> xs = {1, 2}; return xs[0] + xs[1]; }\n",
                SPEC_MAIN)
    _both_green("`array<u64,2> a = {1,2}` 仍支持",
                "u64 main() { array<u64, 2> a = {1, 2}; return a[0] + a[1]; }\n",
                SPEC_MAIN)
    _both_green("`vector<u64> e = {}` 仍支持",
                "u64 main() { vector<u64> e = {}; return e.size(); }\n", SPEC_MAIN)


def test_field_on_non_aggregate() -> None:
    print("[5] 对非 struct / 元组取字段：检查期拒绝（以前 run 抛“未实现的字段访问”）")
    for label, body in (
        ("vector 取字段", "vector<u64> xs = {1}; return xs.a;"),
        ("标量取字段", "u64 n = 5; return n.a;"),
        ("字符串取字段", 'return "ab".a;'),
    ):
        _both_red(label, f"u64 main() {{ {body} }}\n", SPEC_MAIN,
                  code="E-Type-UnknownField")


def test_cast_operand() -> None:
    print("[6] `as` 的操作数也必须检查（以前整个操作数被跳过）")
    tu = "enum Token { Plus, Minus }\n" + SPEC_MAIN
    _both_red("`(nope) as u64` 里的未定义名字",
              "u64 main() { return (nope) as u64; }\n", tu,
              code="E-Name-Undefined")
    _both_red("`(v.Plus) as u64` 里的枚举值 `.`",
              "u64 main() { Token v = Token::Plus; return (v.Plus) as u64; }\n",
              tu, code="E-Member-WrongSep")


def test_enum_value_dot() -> None:
    print("[7] 枚举**值**上的 `.`：以前静默 0，现在检查期拒绝")
    tu = "enum Token { Plus, Minus }\n" + SPEC_MAIN
    _both_red("`u64 r = v.Plus;`",
              "u64 main() { Token v = Token::Plus; u64 r = v.Plus; return r; }\n",
              tu, code="E-Member-WrongSep")
    _both_red("`return v.Plus;`",
              "u64 main() { Token v = Token::Plus; return v.Plus; }\n",
              tu, code="E-Member-WrongSep")
    # `Token.Plus`（类型名基址）仍由 checker 的 E-Member-WrongSep 管，不回归
    _both_red("`Token.Plus`（类型名基址）仍报",
              "u64 main() { Token v = Token.Plus; return 0; }\n", tu,
              code="E-Member-WrongSep")
    # `Token::Plus` 合法
    _both_green("`Token::Plus` 合法",
                "u64 main() { Token v = Token::Plus; return 0; }\n", tu)


def test_hof_callback() -> None:
    print("[8] 高阶内建的回调：元数 / 具名函数 / 有无实现，全部检查期判")
    # 回调的**实现**必须在 `.eln`（解释器只查本模块实现函数）；
    # `.tu` 只放 main 的 spec。这样「合法形状」是真的能跑的。
    pre = ("u64 twice(u64 x) { return x + x; }\n"
           "u64 add(u64 a, u64 b) { return a + b; }\n"
           "u64 onearg(u64 a) { return a; }\n")
    tu = SPEC_MAIN

    def _eln(stmt: str) -> str:
        return pre + "u64 main() { vector<u64> xs = {1, 2, 3}; " + stmt + " }\n"

    _both_red("fold 回调只有 1 个形参",
              _eln("return fold(xs, 0, onearg);"), tu,
              code="E-Type-HofCallback")
    _both_red("map 回调有 2 个形参",
              _eln("const vector<u64> m = map(xs, add); return m[0];"), tu,
              code="E-Type-HofCallback")
    _both_red("map 的函数实参是调用表达式",
              _eln("const vector<u64> m = map(xs, twice(1)); return m[0];"), tu,
              code="E-Type-HofCallback")

    # 第一个实参不是容器：解释器 `isinstance(recv, list)` 才继续
    _both_red("map 的第一个实参是标量",
              _eln("u64 n = 5; return map(n, twice)[0];"), tu,
              code="E-Type-HofContainer")
    _both_red("sort 的第一个实参是标量",
              _eln("u64 n = 5; return sort(n);"), tu,
              code="E-Type-HofContainer")
    _both_red("for_each 的第一个实参是标量",
              _eln("u64 n = 5; return for_each(n, onearg);"), tu,
              code="E-Type-HofContainer")

    # 函数值形参：解释器只查模块实现函数，形参传进去必然 run 崩
    _both_red("map 的函数实参是 `func(...)` 形参",
              pre + "u64 apply(vector<u64> xs, func(u64)->u64 g) {\n"
              "    const vector<u64> m = map(xs, g);\n    return m[0];\n}\n"
              "u64 main() { vector<u64> xs = {1,2,3}; return apply(xs, twice); }\n",
              tu, code="E-Type-HofCallback")

    # 正确签名仍然两边都过
    _both_green("fold(xs, 0, add) 合法", _eln("return fold(xs, 0, add);"), tu)
    _both_green("map(xs, twice) 合法",
                _eln("const vector<u64> m = map(xs, twice); return m[0];"), tu)
    # `for_each` 对 map 是**二元**回调、对 set 是**一元**回调——不能一刀切
    _both_green("for_each(map, 二元回调) 合法",
                pre + "u64 visit2(u64 k, u64 v) { return v; }\n"
                "u64 main() { map<u64, u64> m; m.insert(1, 10);\n"
                "    return for_each(m, visit2); }\n", tu)
    _both_green("for_each(set, 一元回调) 合法",
                pre + "u64 main() { set<u64> s; s.insert(3);\n"
                "    return for_each(s, onearg); }\n", tu)


# ---------------------------------------------------------------- 一致性总闸

#: 每个形状跑一遍，断言 `check 绿 ⇔ run 绿`。这条是**总闸**：以后再加形状，
#: 只要 check 放过而 run 崩，这里就红。
CONSISTENCY = (
    ("用户探针 `S x = {1,2}`（--compat）", PROBE_ELN, None, True),
    ("字段式 `S { a:1, b:2 }`（--compat）", PROBE_FIELD_ELN, None, True),
    ("`S x{1,2}`（--compat）",
     "struct S { u64 a; u64 b; };\n"
     "u64 main() { S x{1, 2}; return x.a + x.b; }\n", None, True),
    ("`S x = {1}`（--compat）",
     "struct S { u64 a; u64 b; };\n"
     "u64 main() { S x = {1}; return 0; }\n", None, True),
    ("cross-file `S x = {1,2}`",
     "u64 main() { S x = {1, 2}; return x.a + x.b; }\n",
     "struct S { a: u64, b: u64, }\n" + SPEC_MAIN, False),
    ("`u64 x = {1}`", "u64 main() { u64 x = {1}; return x; }\n", SPEC_MAIN, False),
    ("`map<u64,u64> m = {}`",
     "u64 main() { map<u64, u64> m = {}; return 0; }\n", SPEC_MAIN, False),
    ("`xs.a`（非 struct 取字段）",
     "u64 main() { vector<u64> xs = {1}; return xs.a; }\n", SPEC_MAIN, False),
    ("`(nope) as u64`",
     "u64 main() { return (nope) as u64; }\n", SPEC_MAIN, False),
    ("`v.Plus`（枚举值）",
     "u64 main() { Token v = Token::Plus; return v.Plus; }\n",
     "enum Token { Plus, Minus }\n" + SPEC_MAIN, False),
    ("fold 回调 1 参",
     "u64 onearg(u64 a) { return a; }\n"
     "u64 main() { vector<u64> xs = {1}; return fold(xs, 0, onearg); }\n",
     SPEC_MAIN, False),
    ("`map(42, twice)`（第一个实参不是容器）",
     "u64 twice(u64 x) { return x + x; }\n"
     "u64 main() { return map(42, twice)[0]; }\n", SPEC_MAIN, False),
    ("for_each(map, 二元回调)（合法，两边都要绿）",
     "u64 visit2(u64 k, u64 v) { return v; }\n"
     "u64 main() { map<u64, u64> m; m.insert(1, 10);"
     " return for_each(m, visit2); }\n", SPEC_MAIN, False),
)


def test_consistency_gate() -> None:
    print("[9] 总闸：check 与 run 的结论必须一致（不许 check 绿 run 崩）")
    for name, eln, tu, compat in CONSISTENCY:
        (cr, _cout), (rr, rout) = _module(eln, tu, compat)
        check(f"{name}：check 绿 ⇔ run 绿",
              (cr == 0) == (rr == 0),
              f"check rc={cr} / run rc={rr}；{rout[-160:]}")


def main() -> int:
    for fn in (test_supported_equivalent, test_arity, test_struct_cross_file,
               test_other_brace_targets, test_field_on_non_aggregate,
               test_cast_operand, test_enum_value_dot, test_hof_callback,
               test_consistency_gate):
        fn()
        print()
    if FAILED:
        print(f"✗ 失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
