#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_void_as_value.py —— **`void` 当值用：`check` 期就拒**（三方一致）

## 这一批修的是什么

`void` 的语义是**没有值**。以前 `check` 只挡住了一部分（声明初值 / 再赋值 /
`return` / `if` 条件），其余位置一律放行，于是下游各自兜底——**同一个 `.eln`
三个命令三种结论**：

| # | 位置 | 改前 `check` | 改前 `run` | 改前 `dsh cpp` | 改后 |
|---|---|---|---|---|---|
| ① | `print(xs.assign(2, 0));` | ✓ 放行 | ✓ 打 `None` | ✗ `E-Cxx-Unsupported` | `E-Type-Void` ×3 |
| ② | `print(g());`（`g` 返回 `()`） | ✓ 放行 | ✓ 打 `None` | ✓ 出码，**g++ 编不过** | `E-Type-Void` ×3 |
| ③ | `xs[xs.assign(2, 0)]` | ✓ 放行 | ✗ `E-Index-Type` | ✗ `E-Cxx-Unsupported` | `E-Type-Void` ×3 |
| ④ | `xs.assign(2, 0).size()` | ✓ 放行 | ✗ `E-Type-NotContainer` | ✗ `E-Cxx-Unsupported` | `E-Type-Void` ×3 |
| ⑤ | `if (g() == 0)` | ✓ 放行 | ✓ 走 false 分支 | ✓ 出码，**g++ 编不过** | `E-Type-Void` ×3 |
| ⑥ | `xs.push_back(g())` | ✓ 放行 | ✓ 追加 `None` | ✓ 出码，**g++ 编不过** | `E-Type-Void` ×3 |
| ⑦ | `u64 x = 5; x.size()` | ✓ 放行 | ✗ `E-Type-NotContainer` | ✓ 出码，**g++ 编不过** | `E-Type-NotContainer` ×3 |

判据只有一条：**`void` 只能作表达式语句**（返回值被丢弃）。声明初值 / 再赋值 /
`return` / `if` 条件本来就有**更具体**的码（`E-Type-Assign` / `E-Type-Return` /
`E-Type-Cond`），**不放宽也不改码**——这里只钉"以前没人管"的位置。

## 验收口径（三条都要）

1. **三方一致**：上面每一行 `check` / `run` / `dsh cpp` 都 rc≠0，且报**同一个码**；
   `run` 的红必须是**检查挡下的**（输出里不许有 `InterpError`）——`run` / `cpp`
   都先跑 `check`，所以这条同时验证了"check 拒绝 ⇒ run 也拒"。
2. **一个根因一条诊断**：`print(xs.assign(2, 0));` 只报 **1 条错误**（不级联出
   `E-Type-Assign` 之类的第二个码）。
3. **合法形状一个都不许误伤**（rc=0 逐个贴）：语句位置的 `void` 调用
   （`xs.assign(2, 0);` / `g();`）、`() f() { return g(); }`、
   `for_each(xs, on)`（`on` 返回 `()`）、以及 `xs.push_back(1);`。
   另加一条：`dsh.cpp` 的**发射器护栏**（值位置 `assign` 报 `E-Cxx-Unsupported`）
   **仍在源码里**——`check` 变严之后它走不到，但**不许删**。

跑法：`python tests/cases/test_void_as_value.py`，退出码 0 即通过。
"""

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
DSH = os.path.join(ROOT, "src", "dsh.py")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

FAILED = []


def check(ok: bool, label: str, detail: str = "") -> None:
    if ok:
        print(f"  ok   {label}")
    else:
        FAILED.append(label)
        print(f"  FAIL {label}   {detail[-600:]}")


def _write(d: str, name: str, text: str) -> None:
    with io.open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _invoke(d: str, cmd: list) -> tuple:
    r = subprocess.run([sys.executable, "-B", DSH] + cmd, cwd=d,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=ENV)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


#: 诊断码：只从**错误**行取（`check` 的错误行以 `✗` 开头；`run` / `cpp` 的
#: 走 stderr）。`⚠` 开头的警告行不算——`E-Fn-NoSpec` 是 warning（无 spec 不阻断），
#: 拿它当结论会把「没写 spec」误判成「void 被拒」。
_CODE_RE = re.compile(r"(E-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)")


def _err_code(out: str) -> str:
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("✗"):
            m = _CODE_RE.search(s)
            if m:
                return m.group(1)
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("⚠"):
            continue
        m = _CODE_RE.search(s)
        if m:
            return m.group(1)
    if "InterpError" in out:
        return "InterpError"
    return ""


def _module(eln: str, tu: str = "") -> dict:
    """写一对 `m.eln` / `m.tu`，跑三命令，返回 {cmd: (rc, 合并输出)}。"""
    d = tempfile.mkdtemp(prefix="elench-void-")
    try:
        _write(d, "m.eln", eln)
        _write(d, "m.tu", tu)
        return {
            "check": _invoke(d, ["check", "m"]),
            "run": _invoke(d, ["run", "m"]),
            "cpp": _invoke(d, ["cpp", "m.tu"]),
        }
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- 造形状

PRELUDE = "() g() { }\nu64 use_u64(u64 x) { return x; }\n"


def _main(bad: str) -> str:
    return PRELUDE + (
        "u64 main() {\n"
        "    vector<u64> xs = vector<u64>::filled(0, 3);\n"
        "    %s\n"
        "    return 0;\n"
        "}\n" % bad)


#: `(标签, 程序, 期望码, .tu 文本)` —— **7 类以前 check 全绿的位置**
LEAKS = [
    ("① print 的实参 `assign`", _main("print(xs.assign(2, 0));"),
     "E-Type-Void", ""),
    ("② print 的实参（返回 `()` 的函数）", _main("print(g());"),
     "E-Type-Void", ""),
    ("③ 下标 `xs[<void>]`", _main("u64 z = xs[xs.assign(2, 0)];"),
     "E-Type-Void", ""),
    ("④ 方法接收者 `xs.assign(…).size()`",
     _main("u64 z = xs.assign(2, 0).size();"), "E-Type-Void", ""),
    ("⑤ 比较操作数 `g() == 0`", _main("if (g() == 0) { return 1; }"),
     "E-Type-Void", ""),
    ("⑥ 方法实参 `xs.push_back(g())`", _main("xs.push_back(g());"),
     "E-Type-Void", ""),
    ("⑦ 标量当容器 `u64 x; x.size()`",
     PRELUDE + "u64 main() {\n    u64 x = 5;\n    return x.size();\n}\n",
     "E-Type-NotContainer", ""),
    # 同类里的其它位置（一并钉住，免得将来只修一半）
    ("⑧ 用户函数实参 `use_u64(g())`", _main("u64 z = use_u64(g());"),
     "E-Type-Void", ""),
    ("⑨ 内建实参 `max(g(), 1)`", _main("u64 z = max(g(), 1);"),
     "E-Type-Void", ""),
    ("⑩ 一元操作数 `!g()`", _main("if (!g()) { return 1; }"),
     "E-Type-Void", ""),
    ("⑪ `as` 的操作数 `(g()) as u64`", _main("u64 z = (g()) as u64;"),
     "E-Type-Void", ""),
    ("⑫ 链式 `xs.push_back(1).size()`",
     _main("u64 z = xs.push_back(1).size();"), "E-Type-Void", ""),
    ("⑬ 字段基址 `g().a`", PRELUDE + (
        "u64 main() {\n    return g().a;\n}\n"),
     "E-Type-Void", "struct S { a: u64, }\n"),
    ("⑭ `for` 的遍历源 `for (… : g())`",
     _main("for (auto& v : g()) { return v; }"), "E-Type-Void", ""),
    ("⑮ `match` 的判别式", PRELUDE + (
        "u64 main() {\n"
        "    match (g()) {\n"
        "        E::A => { return 1; }\n"
        "        _ => { return 2; }\n"
        "    }\n"
        "    return 0;\n}\n"),
     "E-Type-Void", "enum E { A, B }\n"),
    # 缺口 ③ 的旁支：标量当容器（与 `.size()` 同一条判据）
    ("⑯ 标量当容器下标 `u64 x; x[0]`",
     PRELUDE + "u64 main() {\n    u64 x = 5;\n    return x[0];\n}\n",
     "E-Type-NotIndexable", ""),
]

#: `(标签, 程序)` —— **必须仍然合法**（rc=0）
LEGAL = [
    ("语句位置 `xs.assign(2, 0);`", _main("xs.assign(2, 0);")),
    ("语句位置 `g();`（返回 `()`）", _main("g();")),
    ("语句位置 `xs.push_back(1);`", _main("xs.push_back(1);")),
    ("语句位置 `xs.clear();`（void 返回值）", _main("xs.clear();")),
    ("`() f() { return g(); }`（返回位置就是 `()`）", PRELUDE + (
        "() f() { return g(); }\n"
        "u64 main() { f(); return 0; }\n")),
    ("`for_each(xs, on)`，`on` 返回 `()`（HOF 回调）", PRELUDE + (
        "() on(u64 x) { print(x); }\n"
        "u64 main() {\n"
        "    vector<u64> xs = vector<u64>::filled(0, 3);\n"
        "    for_each(xs, on);\n"
        "    return 0;\n}\n")),
    ("`vector<u64> ys = filled(0, 3); ys.size();`（容器方法照常）", PRELUDE + (
        "u64 main() {\n"
        "    vector<u64> ys = vector<u64>::filled(0, 3);\n"
        "    return ys.size();\n}\n")),
]


def test_leaks() -> None:
    print("[1] void 当值：check / run / cpp 三边同码，且 run 不是跑起来才炸")
    for label, eln, code, tu in LEAKS:
        res = _module(eln, tu)
        c_rc, c_out = res["check"]
        r_rc, r_out = res["run"]
        p_rc, p_out = res["cpp"]
        check(c_rc != 0, f"{label}：check rc≠0", c_out)
        check(_err_code(c_out) == code, f"{label}：check 报 {code}", c_out)
        check(r_rc != 0, f"{label}：run rc≠0", r_out)
        check(_err_code(r_out) == code, f"{label}：run 同一个码 {code}", r_out)
        check("InterpError" not in r_out,
              f"{label}：run 是检查挡下的（无 InterpError）", r_out)
        check("先修完再 run/build" in r_out,
              f"{label}：run 打「先修完再 run/build」", r_out)
        check(p_rc != 0, f"{label}：cpp rc≠0", p_out)
        check(_err_code(p_out) == code, f"{label}：cpp 同一个码 {code}", p_out)


def test_one_root_cause_one_diag() -> None:
    print("[2] 一个根因一条诊断（不级联出第二个码）")
    _, out = _module(_main("print(xs.assign(2, 0));"))["check"]
    check("1 条错误" in out, "只在 1 条错误里报", out)
    codes = [c for c in _CODE_RE.findall(out.split("修法")[0])
             if not c.startswith("E-Fn-NoSpec")]
    check(codes.count("E-Type-Void") == 1, "错误码只有一个 E-Type-Void", str(codes))
    check("E-Type-Assign" not in out, "没有 E-Type-Assign 级的级联", str(codes))


def test_legal() -> None:
    print("[3] 合法形状不许误伤（check / run 都 rc=0）")
    for label, eln in LEGAL:
        res = _module(eln)
        c_rc, c_out = res["check"]
        r_rc, r_out = res["run"]
        check(c_rc == 0, f"{label}：check rc=0", c_out)
        check(r_rc == 0, f"{label}：run rc=0", r_out)


def test_legal_cpp() -> None:
    print("[4] `dsh cpp` 照旧发射语句位置的 void 调用（不过度拒绝）")
    for label, eln in LEGAL[:4]:
        p_rc, p_out = _module(eln)["cpp"]
        check(p_rc == 0, f"{label}：cpp rc=0", p_out)
    # `assign` 的语句形态必须原样发射（发射器那条分支还在）
    _, p_out = _module(_main("xs.assign(2, 0);"))["cpp"]
    check("xs.assign(2, 0);" in p_out, "cpp 发射 `xs.assign(2, 0);`", p_out)


def test_emitter_guard_kept() -> None:
    print("[5] 发射器护栏不许删（check 变严 ≠ 拆兜底）")
    src = io.open(DSH, encoding="utf-8").read()
    check("E-Cxx-Unsupported" in src, "dsh.py 仍认 `E-Cxx-Unsupported`", "dsh.py")
    check('if name == "assign" and not stmt_void:' in src,
          "dsh.py 仍在值位置拒绝 `assign`", "dsh.py 的 _cpp_method_call")


def main() -> int:
    test_leaks()
    test_one_root_cause_one_diag()
    test_legal()
    test_legal_cpp()
    test_emitter_guard_kept()
    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{' / '.join(FAILED[:6])}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
