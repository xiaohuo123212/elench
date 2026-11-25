#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_compat_cpp.py —— `--compat` 到底吃下多少真实 C++（逐条钉住）。

## 为什么要有这条（2026-11-22）

文档里曾写着「类成员函数 / 运算符重载 / 类模板 / 宏」都不支持。逐条实测之后发现
**一半是错的**：成员函数（读/写字段、带参、互调）和宏（带参、嵌套、条件编译）
早就通了。文档写的和代码实际能做到的对不上，比做不到更糟——用户会绕远路。

所以这张表把"到底行不行"钉死：每条都给代码，`--compat` 与严格模式各跑一遍。

**这一轮新做的两条**（本轮之前确实不行）：
- **C 风格标量强转 `(int)x`**（第 18 条）：只认内建标量拼写（`CXX_SCALAR_TYPES` 与
  `NATIVE_SCALARS`），因为它们不可能是变量名，解析期**不需要猜**；语义严格等于
  `x as T`（同一个 `cast` 节点），所以没有第二套语义。别名 / struct 名仍拒绝。
- **`#include "x.h"`**（第 19 条）：真的读进来——递归内联 + 把函数原型翻成
  `extern name(…) -> T from "<头>";`。解析不到的头仍然报 `E-Lex-IncludeUnresolved`，
  **不假装读过**。

**仍然不支持的**：`operator<` 等运算符重载、`template`。见文件末尾的已知缺口。
"""
import io
import os
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


def code_contains(out: str, needle: str) -> bool:
    return needle in out


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="elench_compat_")

    def write(name: str, text: str) -> str:
        p = os.path.join(tmp, name)
        io.open(p, "w", encoding="utf-8", newline="").write(text)
        return p

    # ---------------- 能过（--compat）----------------
    OK_CASES = [
        ("C 强转 (int)x", "u64 f(u64 x) {\n    return (int)x;\n}\n"),
        ("C 强转 (u64)x", "u64 f(int x) {\n    return (u64)x;\n}\n"),
        ("C 强转 (int)xs.size()", "i64 f(const vector<u64>& xs) {\n    return (int)xs.size();\n}\n"),
        ("C 强转 (unsigned long)x", "u64 f(u64 x) {\n    return (unsigned long)x;\n}\n"),
        ("C 强转 (u64)(x+1)*2 结合性", "u64 f(u64 x) {\n    return (u64)(x + 1) * 2;\n}\n"),
        ("成员函数：读字段", "class P {\n    u64 a;\n    u64 get() { return a; }\n};\nu64 f() {\n    P p{1};\n    return p.get();\n}\n"),
        ("成员函数：写字段 + 带参", "class P {\n    u64 a;\n    void set(u64 v) { a = v; }\n    u64 add(u64 v) { return a + v; }\n};\nu64 f() {\n    P p{1};\n    p.set(2);\n    return p.add(3);\n}\n"),
        ("宏：对象 / 带参 / 嵌套 / 条件编译",
         "#define N 8\n#define M (N * 2)\n#define MAX2(a, b) ((a) > (b) ? (a) : (b))\n"
         "#ifdef _WIN32\nu64 f() { return 1; }\n#else\nu64 f() { return MAX2(M, 3); }\n#endif\n"),
    ]
    for label, src in OK_CASES:
        p = write("a.eln", src)
        rc, out = run(["check", p, "--compat"], tmp)
        check(rc == 0, "compat 通过：" + label, out.strip().split("\n")[0][:70])

    # ---------------- 严格模式必须行为不变 ----------------
    p = write("s.eln", "u64 f(u64 x) {\n    return (int)x;\n}\n")
    rc, out = run(["check", p], tmp)
    check(rc != 0 and code_contains(out, "E-Cast-CStyle"),
          "严格模式仍然拒绝 (int)x（不放宽默认行为）", out.strip().split("\n")[0][:60])

    # ---------------- `(T)` 与 `as T` 语义相同 ----------------
    write("c1.eln", "u64 f(u64 x) {\n    return (int)x;\n}\n")
    write("c2.eln", "u64 f(u64 x) {\n    return x as int;\n}\n")
    _, o1 = run(["cost", os.path.join(tmp, "c1.eln"), "--compat"], tmp)
    _, o2 = run(["cost", os.path.join(tmp, "c2.eln"), "--compat"], tmp)
    k1 = o1.split("κ ≤ ")[1].split("\n")[0] if "κ ≤ " in o1 else "?"
    k2 = o2.split("κ ≤ ")[1].split("\n")[0] if "κ ≤ " in o2 else "?"
    check(k1 == k2 and k1 != "?", "(int)x 与 x as int 的 κ 相同（同一个 cast 节点）", k1 + " vs " + k2)

    # ---------------- 头文件 ----------------
    write("mylib.h",
          "#ifndef MYLIB_H\n#define MYLIB_H\n#include <cstdint>\n\n"
          "u64 helper(u64 n);\nu64 twice(u64 n);\n#endif\n")
    write("use.eln", '#include "mylib.h"\n\nu64 f(u64 n) {\n    return helper(n) + twice(n);\n}\n')
    rc, out = run(["check", "use.eln", "--compat"], tmp)
    check(rc == 0, "compat 读进 #include \"mylib.h\" 并翻成 extern", out.strip().split("\n")[0][:70])
    _, out = run(["cost", "use.eln", "--compat"], tmp)
    check(code_contains(out, "ext(helper)") and code_contains(out, "ext(twice)"),
          "头文件里的外部函数在 κ 里显示为 ext(名字)", out.strip()[:60])

    rc, out = run(["check", "use.eln"], tmp)
    check(rc != 0 and code_contains(out, "E-Lex-IncludeUnresolved"),
          "严格模式仍然拒绝 #include \"…\"（不静默）", out.strip().split("\n")[0][:60])

    write("miss.eln", '#include "nope.h"\nu64 f() {\n    return 1;\n}\n')
    rc, out = run(["check", "miss.eln", "--compat"], tmp)
    check(rc != 0 and code_contains(out, "E-Lex-IncludeUnresolved"),
          "找不到的头仍然明确报错（不假装读过）", out.strip().split("\n")[0][:60])

    # 头文件里的**函数定义**不该被翻成 extern（它有体，是普通函数）
    write("withimpl.h", "u64 sq(u64 n) {\n    return n * n;\n}\n")
    write("use2.eln", '#include "withimpl.h"\n\nu64 g(u64 n) {\n    return sq(n);\n}\n')
    rc, out = run(["check", "use2.eln", "--compat"], tmp)
    check(rc == 0 and not code_contains(out, "E-Parse"), "头文件里带函数定义也能用", out.strip().split("\n")[0][:60])

    # ---------------- 模板单态化（第 20 条，细节在 test_compat_tmpl.py）----------------
    write("tmpl.eln", "template <typename T>\nu64 len(const vector<T>& xs) {\n    return xs.size();\n}\n"
                      "u64 f(const vector<u64>& xs) {\n    return len(xs);\n}\n")
    rc, out = run(["check", "tmpl.eln", "--compat"], tmp)
    check(rc == 0, "compat 通过：template<typename T> 函数模板（单态化）",
          out.strip().split("\n")[0][:70])
    rc, out = run(["check", "tmpl.eln"], tmp)
    check(rc != 0 and code_contains(out, "E-Keyword-Unsupported"),
          "严格模式仍然拒绝 template", out.strip().split("\n")[0][:60])

    # ---------------- 尖括号 include + 搜索路径（第 19 条扩展，2026-11-23）----------------
    write("inc.h", "u64 from_header(u64 n);\n")
    write("angle.eln", '#include <inc.h>\n\nu64 f() {\n    return from_header(1);\n}\n')
    env_keep = os.environ.get("ELENCH_INCLUDE_DIRS")
    os.environ["ELENCH_INCLUDE_DIRS"] = tmp
    rc, out = run(["check", "angle.eln", "--compat"], tmp)
    if env_keep is None:
        os.environ.pop("ELENCH_INCLUDE_DIRS", None)
    else:
        os.environ["ELENCH_INCLUDE_DIRS"] = env_keep
    check(rc == 0, "`#include <inc.h>` + ELENCH_INCLUDE_DIRS 能内联",
          out.strip().split("\n")[0][:70])

    # **头文件用基名再包含自己**：`fmt/core.h` 里就有 `#include "core.h"`。
    # 以前"已经内联过"的分支把这一行**留着**，下游于是报
    # `E-Lex-IncludeUnresolved`——17 个真库探针就是被这一行挡住的。
    os.makedirs(os.path.join(tmp, "sub"), exist_ok=True)
    io.open(os.path.join(tmp, "sub", "self.h"), "w", encoding="utf-8", newline="").write(
        '#include "self.h"\nu64 from_self(u64 n);\n')
    write("useself.eln", '#include <sub/self.h>\n\nu64 f() {\n    return from_self(1);\n}\n')
    os.environ["ELENCH_INCLUDE_DIRS"] = tmp
    rc, out = run(["check", "useself.eln", "--compat"], tmp)
    if env_keep is None:
        os.environ.pop("ELENCH_INCLUDE_DIRS", None)
    else:
        os.environ["ELENCH_INCLUDE_DIRS"] = env_keep
    check(rc == 0 and "E-Lex-IncludeUnresolved" not in out,
          "头文件用基名再包含自己不再报 IncludeUnresolved",
          out.strip().split("\n")[0][:70])

    # 标准头必须**保持**被静默丢弃（不因为上面那条就变成"到处找文件"）
    write("std.eln", "#include <vector>\n\nu64 f(const vector<u64>& xs) {\n    return xs.size();\n}\n")
    rc, out = run(["check", "std.eln", "--compat"], tmp)
    check(rc == 0, "标准头 `<vector>` 仍然照常通过", out.strip().split("\n")[0][:70])

    # `#warning` 在 compat 下跳过（真库单头大量使用），严格模式仍报不支持
    write("warn.eln", "#warning \"deprecated\"\nu64 f() {\n    return 1;\n}\n")
    rc, out = run(["check", "warn.eln", "--compat"], tmp)
    check(rc == 0, "`#warning` 在 --compat 下不挡路", out.strip().split("\n")[0][:70])

    # ---------------- 成员的纯拼写限定词（第 22 条）----------------
    # `mutable` / `inline` / `volatile` / `constexpr` 在成员位置上是纯拼写：
    # 本语言字段本来就都能改、没有内联声明、没有编译期常量。真库头里成片出现
    # （`fmt/core.h` 的 `mutable` 一处就挡下 13 个探针）。
    write("ms.eln", "struct P {\n    mutable u64 a;\n    inline u64 get() { return a; }\n};\n\n"
                    "u64 f() {\n    P p{1};\n    return p.get();\n}\n")
    rc, out = run(["check", "ms.eln", "--compat"], tmp)
    check(rc == 0, "compat 通过：`mutable` 字段 + `inline` 成员函数",
          out.strip().split("\n")[0][:70])
    rc, out = run(["check", "ms.eln"], tmp)
    check(rc != 0 and "E-Keyword-LongForm" in out,
          "严格模式仍然拒绝 `mutable`（不放宽默认）", out.strip().split("\n")[0][:60])

    # ---------------- FFI 风格：`ELENCH_NO_INCLUDE_INLINE=1` 回到老口径 ----------------
    # FFI 的 `.eln`（`extern … from "lib"` + `link`）里那个 `#include` 只给 C 侧 shim 用。
    # 内联进来只会去啃整个真库头。开关打开时整行丢掉、不报错。
    write("ffi.eln", '#include "nope_does_not_exist.h"\n\nu64 f() {\n    return 1;\n}\n')
    rc, _ = run(["check", "ffi.eln", "--compat"], tmp)
    check(rc != 0, "默认：找不到的头仍然报错（不假装读过）")
    os.environ["ELENCH_NO_INCLUDE_INLINE"] = "1"
    rc, out = run(["check", "ffi.eln", "--compat"], tmp)
    os.environ.pop("ELENCH_NO_INCLUDE_INLINE", None)
    check(rc == 0, "`ELENCH_NO_INCLUDE_INLINE=1`：include 整行忽略（老口径）",
          out.strip().split("\n")[0][:70])

    # ---------------- 已知缺口（写下来，别让文档再吹）----------------
    # `template` 与 `operator<` 都已经从这张表搬走了（2026-11-23：单态化 +
    # 运算符重载，见 `test_compat_tmpl.py` / `test_compat_ops.py`）。
    # 这张表留着——下次再加"已知缺口"就往里加，做完了它自己会红。
    GAPS = []
    write("op.eln", "struct P { u64 a; };\nbool operator<(const P& x, const P& y) { return x.a < y.a; }\n"
                    "u64 f(const P& p, const P& q) {\n    if (p < q) { return 1; }\n    return 0;\n}\n")
    rc, out = run(["check", "op.eln", "--compat"], tmp)
    check(rc == 0, "compat 通过：operator< 重载（第 21 条）", out.strip().split("\n")[0][:70])
    for label, src in GAPS:
        p = write("g.eln", src)
        rc, _ = run(["check", p, "--compat"], tmp)
        check(rc != 0, "已知缺口仍是缺口：" + label + "（真支持了就把这条改掉）")

    print()
    print("  %s  通过 %d，失败 %d" % ("全部通过" if _failed == 0 else "有失败", _passed, _failed))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
