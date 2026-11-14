#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_qualified_undeclared.py —— `X::f(...)` 里 `X` 必须是**已知类型**。

## 这是哪一种假绿

lexer 把 `::` 当 PATHSEP，parser 把它落成 `X.member` 的限定名；下游
（typecheck 的 `.` 分支、interp 的 `split(".")[-1]`）只认**最后一段**。
于是

    u64 f() {
        foo::print("hi {}\\n", 1);   // `foo` 从没声明过
        return 0;
    }

改前：`check` **零诊断**、`run` 照样打出 `hi 1`。原因不是"库接进来了"，
而是成员名 `print` 撞上语言内建，`foo` 被静默忽略。真实 C++ 库
`fmt::print(…)` 会因此"通过且输出正确"，而 `#include` 早被丢弃——
**最坏的一类假绿**。

## 判据

`X::f(...)` / `X::V` 里的 `X` 必须是：已知类型名（内置 / struct / enum /
error / 泛型参数）或已登记命名空间（`std`）。未知 → `E-Type-Unknown`，
消息里必须给出两条出路：不带 `::` 写全局名；或先把类型声明出来。

## 反向验收（比正向更重要）

- `foo::print(...)` **被拒**；
- `foo::bar(...)` **也拒**——不能留下"只要成员名撞内建就放行"的后门；
- `std::printf(...)` / `std::vector<u64>` / 已声明枚举的 `Color::Red` /
  `Token::Number(1)` / `vector<u64>::make()` / 泛型 `T::something` **照旧通过**。

跑法：`python tests/cases/test_qualified_undeclared.py`，退出码 0 即通过。
"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DSH = os.path.join(ROOT, "src", "dsh.py")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

FAILED = []

SPEC = ("spec f() -> u64 { effects {}; target { time: { max: O(1) }, "
        "allocation: { max: 0 }, }; }\n")
SPEC_IO = ("spec f() -> u64 { effects { io }; target { time: { max: O(1) }, "
           "allocation: { max: 0 }, }; }\n")


def _cli(cmd: str, eln: str, tu: str = SPEC, extra=()):
    """在临时目录里写 `m.eln` / `m.tu` 跑一次 CLI，返回 `(rc, stdout+stderr)`。"""
    d = tempfile.mkdtemp(prefix="elench-qual-")
    try:
        with open(os.path.join(d, "m.eln"), "w", encoding="utf-8",
                  newline="\n") as f:
            f.write(eln)
        with open(os.path.join(d, "m.tu"), "w", encoding="utf-8",
                  newline="\n") as f:
            f.write(tu)
        r = subprocess.run([sys.executable, "-B", DSH, cmd, "m", *extra], cwd=d,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=ENV)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name}  {detail[:300]}")


def main() -> int:
    # ---- 1. 正向：未声明的前缀必须被拒（成员撞内建也一样）----
    print("[1] 未声明的 `X::f(...)` → E-Type-Unknown（不再静默退化成全局调用）")
    rc, out = _cli("check", 'u64 f() {\n    foo::print("hi {}\\n", 1);\n    return 0;\n}\n',
                   SPEC_IO)
    check("1a `foo::print(...)` 被拒（rc≠0）", rc != 0, f"rc={rc} {out}")
    check("1b 报的是 E-Type-Unknown（未知类型名）", "E-Type-Unknown" in out, out)
    check("1c 诊断指出 `foo` 没声明", "foo" in out and "未知类型名" in out, out)
    check("1d **给出出路**：不带 `::` 写全局名",
          "print(…)" in out and "不要带 `::`" in out, out)

    # **改前这里 rc=0 且 stdout 有 `hi 1`**：这一条钉住"run 也不再放行"。
    rc, out = _cli("run", 'u64 f() {\n    foo::print("hi {}\\n", 1);\n    return 0;\n}\n',
                   SPEC_IO, ("--fn=f",))
    check("1e `run` 也拒绝（不再打出 `hi 1`）", rc != 0 and "hi 1" not in out,
          f"rc={rc} {out}")

    print("[2] 成员名不撞内建时**同样**被拒（堵后门）")
    rc, out = _cli("check", "u64 f() {\n    foo::bar(1);\n    return 0;\n}\n")
    check("2a `foo::bar(...)` 被拒", rc != 0 and "E-Type-Unknown" in out,
          f"rc={rc} {out}")
    # 不带括号的 `::` 也是同一个名字形状：`foo::bar;`
    rc, out = _cli("check", "u64 f() {\n    foo::bar;\n    return 0;\n}\n")
    check("2b `foo::bar`（无括号）也被拒", rc != 0 and "E-Type-Unknown" in out,
          f"rc={rc} {out}")

    # ---- 3. 不合格形状不许误伤 ----
    print("[3] 合法形状照旧（rc=0）")
    ok_cases = [
        ("3a `std::printf(...)`（已登记标准库名）",
         'u64 f() {\n    std::printf("v=%d\\n", 7);\n    return 0;\n}\n', SPEC),
        ("3b `std::vector<u64>`（标准库类型）",
         "u64 f() {\n    std::vector<u64> xs = {1, 2};\n    return xs[0];\n}\n",
         SPEC),
        ("3c `vector<u64>::make()`（静态构造）",
         "u64 f() {\n    vector<u64> v = vector<u64>::make();\n    return 0;\n}\n",
         SPEC),
        ("3d `array<u64, 3>::filled(0)`（静态构造）",
         "u64 f() {\n    array<u64, 3> a = array<u64, 3>::filled(0);\n"
         "    return a[0];\n}\n", SPEC),
        ("3e 泛型函数里的 `T::something`",
         "u64 f<T>(T x) {\n    return T::size();\n}\n",
         "spec f<T>(T x) -> u64 { effects {}; "
         "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n"),
    ]
    for label, eln, tu in ok_cases:
        rc, out = _cli("check", eln, tu)
        check(label, rc == 0 and "E-Type-Unknown" not in out, f"rc={rc} {out}")

    # ---- 4. 枚举：`Color::Red`（值）与 `Token::Number(1)`（带载荷构造）----
    print("[4] 枚举变体是合法限定名")
    enum_tu = ("enum Color { Red, Green, Blue, }\n"
               "enum Token { Plus, Number(u64), }\n" + SPEC)
    rc, out = _cli("check",
                   "u64 f() {\n    Color c = Color::Red;\n"
                   "    Token t = Token::Number(1);\n    return 0;\n}\n", enum_tu)
    check("4a `Color::Red` / `Token::Number(1)` 不误报",
          rc == 0 and "E-Type-Unknown" not in out, f"rc={rc} {out}")

    # `.tu` 里声明的类型同样算"已知"（类型名表把两个模块合在一起看）。
    rc, out = _cli("check", "u64 f() {\n    Err e = Err::One;\n    return 0;\n}\n",
                   "enum Err { One, Two, }\n" + SPEC)
    check("4b `.tu` 声明的枚举 `Err::One` 不误报",
          rc == 0 and "E-Type-Unknown" not in out, f"rc={rc} {out}")

    # ---- 5. 已声明 struct 的静态调用仍走**原来的**诊断（不是新码）----
    print("[5] 已知类型的静态成员调用：诊断码不变（E-Type-NoSuchMethod）")
    rc, out = _cli("check",
                   "u64 f() {\n    Point p = Point::new(1, 2);\n    return p.x;\n}\n",
                   "struct Point { x: u64, y: u64, }\n" + SPEC)
    check("5a `Point::new` 报 E-Type-NoSuchMethod，不报 E-Type-Unknown",
          rc != 0 and "E-Type-NoSuchMethod" in out and "E-Type-Unknown" not in out,
          f"rc={rc} {out}")

    # ---- 6. 对照：同一个调用去掉 `::` 就是合法的全局内建 ----
    print("[6] 对照：不带 `::` 的 `print(...)` 仍合法")
    rc, out = _cli("run", 'u64 f() {\n    print("hi {}\\n", 1);\n    return 0;\n}\n',
                   SPEC_IO, ("--fn=f",))
    check("6a `print(...)` rc=0 且输出 `hi 1`", rc == 0 and "hi 1" in out,
          f"rc={rc} {out}")

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
