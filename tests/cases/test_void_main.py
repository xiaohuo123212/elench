#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_void_main.py —— `main` 的返回类型 → C++ 入口 `int main()` 的三种发射

## 缺口是什么（2026-11-14 之前）

`dsh cpp` 曾经**无条件**发：

```cpp
int main() { return (int)elench_main(); }
```

而语言里 `() main()`（返回 `()`）的 `elench_main` 是 **void** ⇒ g++ 报
`void value not ignored as it ought to be`——生成物编不过。反过来，`main`
返回 `vector<u64>` / `f64` 时旧实现也照发那句：前者编不过，后者**静默截断**
（浮点返回值变成退出码）。

**实测记录**：在 HEAD `d4ec6b3` 上，void 分支已经在 `093eaae` 修好了
（`int main() { elench_main(); return 0; }`）；仍然没有的是「返回别的类型
⇒ 明确报 `E-Cxx-Unsupported`，别发编不过的东西」。这个文件把两半都钉住，
免得下一次改发射器时又退回去。

## 钉住什么

1. `()` / `void main()` ⇒ `int main() { elench_main(); return 0; }`；
2. `u64` / `i64` / `bool` ⇒ `int main() { return (int)elench_main(); }`（照旧）；
3. 别的返回类型（`vector<u64>` / `f64` / struct）⇒ **`E-Cxx-Unsupported`**、
   rc≠0、**不写生成物**（不发编不过的东西）；
4. `--compat` 下 C++ 的 `int main()` 照旧（入口仍然是 `int main()`）；
5. 有参数的语言 `main` 不生成 C++ 入口（既有行为，不能被这条改动碰到）；
6. 有 g++ 时真编真跑：`() main() { print(1); return; }` ⇒ 生成物输出 `1`
   （acceptance：出码 + g++ 编过 + 运行输出 1）。

## 关于 `return ();`

验收原文写的是 `() main() { print(1); return (); }`，但 HEAD 的 parser **不认**
`()` 当表达式字面量：

```
✗ E-Parse-ExpectedExpr @1:31 期望表达式，实际是 ')'
```

`parser.py` 不在本轮可改范围（文件所有权），所以这里用语言里合法的
`return;`（void 函数的裸返回）与"隐式落空"两种写法验证同一件事。

跑法：`python tests/cases/test_void_main.py`（退出码 0 即通过）。
"""
import os
import shutil
import subprocess
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import dsh                                                   # noqa: E402
from parser import parse_source                              # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                            # noqa: BLE001
    pass

FAILED = []
ENV_BASE = dict(os.environ, PYTHONIOENCODING="utf-8")

ENTRY_VOID = "int main() { elench_main(); return 0; }"
ENTRY_CAST = "int main() { return (int)elench_main(); }"


def check(label, cond, detail=""):
    if cond:
        print(f"  ok   {label}")
    else:
        FAILED.append(label)
        print(f"  FAIL {label}   {detail[-700:]}")


def gen(text, tu_text=""):
    """只走语法 + 发射（不碰编译器、不写磁盘）。"""
    tu = parse_source(tu_text, "m.tu").module
    ch = parse_source(text, "m.eln").module
    return dsh._gen_cpp(tu, ch)


def gen_error(text):
    try:
        gen(text)
    except dsh.CppUnsupported as exc:
        return str(exc)
    except Exception as exc:                                 # noqa: BLE001
        return f"__wrong_exc__ {type(exc).__name__}: {exc}"
    return ""


def run_dsh(args, env_extra=None, cwd=None):
    e = dict(ENV_BASE)
    e.update(env_extra or {})
    return subprocess.run([sys.executable, "-B", os.path.join(SRC, "dsh.py")]
                          + list(args), cwd=cwd or ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=e)


def both(r):
    return (r.stdout or "") + (r.stderr or "")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# ---------------------------------------------------------------- ① 三种发射
def test_emission():
    print("[1] 三种发射（纯发射器，不需要编译器）")

    text = gen("() main() {\n    print(1);\n    return;\n}\n")
    check("`() main()` ⇒ 入口调完就 return 0", ENTRY_VOID in text,
          "\n".join(text.splitlines()[-6:]))
    check("`() main()` ⇒ `void elench_main()`", "void elench_main()" in text,
          "\n".join(text.splitlines()[-8:]))
    check("`() main()` ⇒ **没有** `return (int)elench_main();`（那才是编不过的那句）",
          ENTRY_CAST not in text, "\n".join(text.splitlines()[-6:]))

    text = gen("() main() {\n    print(1);\n}\n")
    check("`() main()` 隐式落空（没有 `return;`）也发同一条入口",
          ENTRY_VOID in text, "\n".join(text.splitlines()[-6:]))

    for t, name in (("u64", "uint64_t"), ("i64", "int64_t"), ("bool", "bool")):
        text = gen(f"{t} main() {{\n    return 0;\n}}\n")
        check(f"`{t} main()` ⇒ `{name} elench_main()` + `(int)` 入口",
              ENTRY_CAST in text and f"{name} elench_main()" in text,
              "\n".join(text.splitlines()[-8:]))

    tu = "spec main() -> i64 {\n    effects {};\n    target { time: { max: 1 }, allocation: { max: 0 }, };\n}\n"
    text = gen("i64 main() {\n    print(1);\n    return 0;\n}\n", tu)
    check("有 `.tu` spec 也不影响入口发射", ENTRY_CAST in text,
          "\n".join(text.splitlines()[-6:]))


# ---------------------------------------------------------------- ② 不支持就报错
def test_unsupported():
    print("[2] 返回别的类型 ⇒ `E-Cxx-Unsupported`，**不发编不过的东西**")
    vec = "vector<u64> main() {\n    vector<u64> v = {1, 2, 3};\n    return v;\n}\n"
    msg = gen_error(vec)
    check("`vector<u64> main()` 抛 CppUnsupported", bool(msg) and "vector<u64>" in msg,
          msg)
    check("消息说明 C++ 入口只能返回 int", "int main()" in msg, msg)
    check("消息给了可执行的修法（u64/i64/bool 或 `()`）",
          "u64" in msg and "()" in msg, msg)

    fmsg = gen_error("f64 main() {\n    print(1);\n    return 1.5;\n}\n")
    check("`f64 main()` 也拒绝（`(int)double` 会**静默截断**）",
          bool(fmsg) and "f64" in fmsg, fmsg)

    # 有参数的 main 本来就不生成入口：这条改动不能把它变成报错。
    pmsg = gen_error("u64 main(u64 n) {\n    return n;\n}\n")
    check("带参数的 `main` 不生成入口、也不被新的拒绝逻辑误伤", pmsg == "", pmsg)
    ptext = gen("u64 main(u64 n) {\n    return n;\n}\n")
    check("带参数的 `main` 只留一行注释",
          "不生成 C++ 入口" in ptext and ENTRY_CAST not in ptext,
          "\n".join(ptext.splitlines()[-4:]))


# ---------------------------------------------------------------- ③ CLI 行为
def test_cli(tmp):
    print("[3] CLI：不支持时 rc≠0 且**不写生成物**；`--compat` 照旧")
    d = os.path.join(tmp, "cli")
    env = dict(ENV_BASE, TMP=d, TEMP=d)

    vec_eln = os.path.join(d, "vecmain.eln")
    write(vec_eln, "vector<u64> main() {\n    vector<u64> v = {1};\n    return v;\n}\n")
    out = os.path.join(d, "vecmain.cpp")
    r = run_dsh(["cpp", vec_eln, "--output=" + out], env_extra=env)
    check("`dsh cpp` 遇到 `vector<u64> main()` ⇒ rc≠0", r.returncode != 0,
          both(r)[-500:])
    check("stderr 有 `E-Cxx-Unsupported`", "E-Cxx-Unsupported" in both(r),
          both(r)[-500:])
    check("**不写生成物**（编不过的产物不落盘）", not os.path.exists(out), out)
    left = [n for n in os.listdir(d) if n.startswith("elench_cpprun_")]
    check("没留下临时目录", not left, str(left))

    compat_eln = os.path.join(d, "compat.eln")
    write(compat_eln, "int main() {\n    print(1);\n    return 0;\n}\n")
    cout = os.path.join(d, "compat.cpp")
    r = run_dsh(["cpp", compat_eln, "--compat", "--output=" + cout], env_extra=env)
    check("`--compat` 下 `int main()` 仍然 rc=0", r.returncode == 0, both(r)[-500:])
    text = open(cout, encoding="utf-8").read() if os.path.exists(cout) else ""
    check("`--compat` 的 C++ `int main()` 照旧：`(int)` 入口 + int64_t 实现",
          ENTRY_CAST in text and "int64_t elench_main()" in text,
          "\n".join(text.splitlines()[-8:]))


# ---------------------------------------------------------------- ④ 真编真跑
def _find_cxx():
    argv = dsh._cxx_compiler_argv()
    if argv:
        return argv
    for c in (r"C:\Users\haoll\Documents\shake\toolchain\mingw64\bin\g++.exe",):
        if os.path.isfile(c):
            return [c]
    return None


def test_real_run(tmp):
    print("[4] 真编真跑：`() main() { print(1); return; }` ⇒ 出码 + g++ 编过 + 输出 1")
    cxx_argv = _find_cxx()
    if not cxx_argv:
        print("  --   本机没有 g++ / clang++：**未验证**（不是通过）")
        return
    if os.path.basename(cxx_argv[0]).lower() in ("cl", "cl.exe"):
        print("  --   编译器是 cl：本用例只写 g++ 参数，**未验证**（不是通过）")
        return
    cxx = cxx_argv[0]
    d = os.path.join(tmp, "run")
    env = dict(ENV_BASE, TMP=d, TEMP=d, ELENCH_CXX=cxx)
    env["PATH"] = os.path.dirname(cxx) + os.pathsep + env.get("PATH", "")
    eln = os.path.join(d, "vm.eln")
    write(eln, "() main() {\n    print(1);\n    return;\n}\n")
    cpp = os.path.join(d, "vm.cpp")

    r = run_dsh(["cpp", eln, "--run", "--output=" + cpp], env_extra=env, cwd=d)
    msg = both(r)
    check("`dsh cpp --run` rc=0（编译自检 + 链接 + 运行都过了）",
          r.returncode == 0, msg[-700:])
    check("生成物 stdout 是 1", "生成物 stdout" in msg and "1" in msg, msg[-500:])
    text = open(cpp, encoding="utf-8").read() if os.path.exists(cpp) else ""
    check("生成物里是 void 入口", ENTRY_VOID in text and "void elench_main()" in text,
          "\n".join(text.splitlines()[-8:]))

    # 再**脱离 dsh 的 `-fsyntax-only` 自检**编一次、跑一次：
    # 这才是「g++ 编过 + 运行输出 1」的硬证据。
    exe = os.path.join(d, "vm" + (".exe" if os.name == "nt" else ""))
    cmd = dsh._cpp_link_cmd([cxx], cpp, exe)
    r2 = subprocess.run(cmd, cwd=d, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", env=env)
    check("生成的 .cpp 被 g++ **真正编译成 exe**（不是只看语法）",
          r2.returncode == 0, (r2.stderr or "")[-700:])
    if r2.returncode == 0:
        got = subprocess.run([exe], cwd=d, capture_output=True, env=env)
        check("exe 运行输出 `1`", (got.stdout or b"").strip() == b"1",
              f"rc={got.returncode} out={got.stdout!r} err={(got.stderr or b'')[-200:]!r}")
        check("exe 退出码是 0（void main 调完 return 0）", got.returncode == 0,
              str(got.returncode))
    left = [n for n in os.listdir(d) if n.startswith("elench_cpprun_")]
    check("没留下临时目录", not left, str(left))


def main() -> int:
    tmp = os.path.join(ROOT, "build", "void_main_test")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        test_emission()
        test_unsupported()
        test_cli(tmp)
        test_real_run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
