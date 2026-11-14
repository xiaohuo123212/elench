#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_cpp_run_link.py —— `dsh cpp --run` 的**链接通道**与真对拍

## 改前是什么样（2026-11-14 之前）

```
dsh cpp --run  =  用 g++ 把生成的 .cpp 编成 exe
                 （模块没有 main 就 `-c`；没有编译器就"无法对拍"）
```

**它不跑、也不和解释器比**。`_cpp_compare` 的旧实现到"编译成功"就 return 0；
`tests/cases/test_ternary.py:479` 的注释把这件事写清楚过。文件名里的 "run"
是承诺，实现里没有。

于是接真库要手工三步：

```
gcc -c cJSON.c -o cJSON.o
g++ -std=c++17 gen.cpp cJSON.o -o gen.exe     # ← dsh 不做这一步
./gen.exe
```

## 现在钉住什么

1. **`--link=` / `--link-dir=` / `--lib=` 进命令行的位置与顺序**（纯函数
   `_cpp_link_cmd`，不需要编译器）：链接输入在源文件之后、`-l` 之前——链接器
   从左到右解析，库必须排在引用它的目标文件之后。
2. **参数校验是当场拒绝**（rc=2）：文件不存在 / 后缀不认识 / 目录不存在 /
   库名非法，各自给出能读懂的那一句——不让链接器的 `cannot find` 当唯一解释。
3. **`-static` 默认开（Windows + g++）**，`--no-static` 关：MinGW 的 exe 默认
   依赖 `libstdc++-6.dll`，离开编译器目录运行就 `STATUS_DLL_NOT_FOUND`
   （3221225781）——那会把"生成物没跑起来"误判成"后端错"。这里真跑一个
   **PATH 里没有 MinGW** 的 exe 来钉住它。
4. **对拍口径与 `tools/behavior_parity.py:strip_return_line` 逐字节一致**
   （同一批夹具断言两份实现相同）：`dsh cpp --run` 里有一份逐字等价实现，
   因为 `editors/vscode/python/dsh.py` 镜像不能 import `tools/`。
5. **真编真链真跑**：本地 C 库 + `.o` → `--run` 一条命令 rc=0，stdout 与
   `dsh run` 逐字节一致，且能看到生成物的输出（42）。
6. **链接失败 rc≠0**（未定义符号会提示 `--link=` / `--lib=`）——**不是跳过**；
   临时产物整个删掉。
7. **没有 main 的模块：`--link=` 明确"未使用"**，不当成验证通过。
8. 本机有 `build/real-libs/cJSON`（gitignored）时，再跑一遍 cJSON 1.7.19 的
   真实全链路；没有就打印「跳过（未验证）」，**不冒充通过**。
"""
import os
import shutil
import subprocess
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
#: 与 `test_cpp.py` / `test_cpp_cstr_return.py` 同一条旧约定：`HERE` 指 `src/`。
HERE = SRC
#: **放在 `src` 之后**（append）：`tools/walk.py` 等与 `src/` 同名模块重名。
TOOLS = os.path.join(ROOT, "tools")
for _p in (SRC, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
if TOOLS not in sys.path:
    sys.path.append(TOOLS)

import dsh                                                   # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                            # noqa: BLE001
    pass

FAILED = []
ENV_BASE = dict(os.environ, PYTHONIOENCODING="utf-8")


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def both(r):
    return (r.stdout or "") + (r.stderr or "")


def run_dsh(args, env_extra=None, cwd=None):
    e = dict(ENV_BASE)
    e.update(env_extra or {})
    return subprocess.run([sys.executable, "-B", os.path.join(HERE, "dsh.py")] + list(args),
                          cwd=cwd or ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=e)


def _find_cxx():
    """找 C++ 编译器：`ELENCH_CXX` / PATH → 本机 MinGW 固定位置。

    **不是**为了让 CI 假装有编译器：两处都找不到就明确打印「跳过（未验证）」。
    本机（Windows + MinGW）的 `g++` 不在 PATH 上，固定位置这条 fallback 让
    端到端用例在**这台机器**真的跑起来（与 `test_cpp_cstr_return.py` 的
    `_find_gcc` 同一个理由）。
    """
    argv = dsh._cxx_compiler_argv()
    if argv:
        return argv
    for c in (r"C:\Users\haoll\Documents\shake\toolchain\mingw64\bin\g++.exe",):
        if os.path.isfile(c):
            return [c]
    return None


def _find_gcc(cxx):
    """找 `gcc`：先看 g++ 旁边（同一套工具链），再 PATH，最后本机固定位置。"""
    cands = []
    if cxx:
        base = os.path.dirname(cxx)
        cands += [os.path.join(base, "gcc.exe"), os.path.join(base, "gcc")]
    w = shutil.which("gcc")
    if w:
        cands.append(w)
    cands.append(r"C:\Users\haoll\Documents\shake\toolchain\mingw64\bin\gcc.exe")
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def _find_ar(cxx):
    cands = []
    if cxx:
        base = os.path.dirname(cxx)
        cands += [os.path.join(base, "ar.exe"), os.path.join(base, "ar")]
    w = shutil.which("ar")
    if w:
        cands.append(w)
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


# ---------------------------------------------------------------- ① 命令形状
def test_cmd_shape():
    print("[1] 链接参数进命令行的位置（纯函数，不需要编译器）")
    g = ["g++.exe"]
    cmd = dsh._cpp_link_cmd(g, "m.cpp", "m.exe", include_dirs=["inc"],
                            link_files=["a.o"], link_dirs=["libs"], libs=["m"])
    if os.name == "nt":
        check("Windows + g++：默认带 `-static`", "-static" in cmd, str(cmd))
    else:
        check("非 Windows：默认**不**加 `-static`（不需要）", "-static" not in cmd, str(cmd))
    check("`--no-static`（static=False）⇒ 没有 `-static`",
          "-static" not in dsh._cpp_link_cmd(g, "m.cpp", "m.exe", static=False))
    check("static=True 强制加（任何平台）",
          "-static" in dsh._cpp_link_cmd(g, "m.cpp", "m.exe", static=True))
    check("`-std=c++17` 在", "-std=c++17" in cmd, str(cmd))
    check("`-Iinc` 在", "-Iinc" in cmd, str(cmd))
    i_src = cmd.index("m.cpp")
    i_obj, i_L, i_l = cmd.index("a.o"), cmd.index("-Llibs"), cmd.index("-lm")
    i_o = cmd.index("-o")
    check("顺序：源文件 → `--link` 文件 → `-L` → `-l` → `-o`",
          i_src < i_obj < i_L < i_l < i_o, str(cmd))
    check("`-o` 后面就是 exe", cmd[i_o + 1] == "m.exe", str(cmd))
    check("没有链接输入时也照常出命令（只是少了那几个参数）",
          dsh._cpp_link_cmd(g, "m.cpp", "m.exe")[-2:] == ["-o", "m.exe"])

    cl = dsh._cpp_link_cmd(["cl.exe"], "m.cpp", "m.exe", include_dirs=["inc"],
                           link_files=["a.lib"], link_dirs=["libs"], libs=["ws2_32"])
    check("cl：链接选项跟在 `/link` 后", "/link" in cl and "/LIBPATH:libs" in cl, str(cl))
    check("cl：`--lib=x` → `x.lib`（没有 `-l`）", "ws2_32.lib" in cl, str(cl))
    check("cl：`/OUT:m.exe`", "/OUT:m.exe" in cl, str(cl))
    check("cl：不加 g++ 的 `-static`", "-static" not in cl, str(cl))

    print("       -- `--link` 的文件在生成物里**不出现**（本机路径不该烘焙进源码）")


# ---------------------------------------------------------------- ② 对拍口径
FIXTURES = [
    b"",
    b"1.7.19\r\n0\r\n",
    b"1.7.19\n0\n",
    b"hello\n-3\n",
    b"hello\n",
    b"42",
    b"0",
    b"5\n6\n",
    b"-7\r\n",
    b"\n0\n",
    b"42\nabc\n",
]


def test_strip_parity():
    print("[2] 剥 main 返回值末行：与 `tools/behavior_parity.py` **逐字节一致**")
    from behavior_parity import strip_return_line
    bad = []
    for fx in FIXTURES:
        a = dsh._cpp_strip_run_return_line(fx)
        b = strip_return_line(fx)
        if a != b:
            bad.append((fx, a, b))
    check(f"{len(FIXTURES)} 个夹具两边完全相同", not bad, str(bad[:3]))
    check("`1.7.19\\r\\n0\\r\\n` → 只剥末行",
          dsh._cpp_strip_run_return_line(b"1.7.19\r\n0\r\n") == b"1.7.19\r\n")
    check("程序自己打印的 `0` 不被误删（`0\\n-3\\n` 末行是 -3）",
          dsh._cpp_strip_run_return_line(b"0\n-3\n") == b"0\n")
    check("末行不是整数 ⇒ 原样返回",
          dsh._cpp_strip_run_return_line(b"42\nabc\n") == b"42\nabc\n")

    print("[2b] 退出码只在 0..255 时判（更大值会被 OS 截断，口径不唯一）")
    check("42 vs 42 ⇒ True", dsh._cpp_exit_code_matches(42, b"42\n")[0] is True)
    check("0 vs 0（cJSON 那种：末行 0）⇒ True",
          dsh._cpp_exit_code_matches(0, b"1.7.19\r\n0\r\n")[0] is True)
    check("42 vs 43 ⇒ False", dsh._cpp_exit_code_matches(42, b"43\n")[0] is False)
    check("300 ⇒ None（说清「不参与」，不硬判）",
          dsh._cpp_exit_code_matches(44, b"300\n")[0] is None)
    check("末行非整数 ⇒ None", dsh._cpp_exit_code_matches(0, b"done\n")[0] is None)


# ---------------------------------------------------------------- ③ 参数校验
def test_cli_validation(tmp):
    print("[3] 参数校验：当场拒绝（rc=2），不让链接器当唯一解释")
    d = os.path.join(tmp, "cli")
    os.makedirs(d, exist_ok=True)
    eln = os.path.join(d, "m.eln")
    with open(eln, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("u64 main() { return 0; }\n")
    env = {"TMP": d, "TEMP": d}

    def bad(args, want, label):
        r = run_dsh(["cpp", eln] + args, env_extra=env)
        msg = both(r)
        check(label, r.returncode == 2 and want in msg,
              f"rc={r.returncode} / {msg[-300:]}")

    bad(["--link="], "要一个 .o", "`--link=` 空串 ⇒ rc=2")
    bad(["--link=" + os.path.join(d, "nope.o")], "不存在",
        "`--link=` 文件不存在 ⇒ rc=2（链接必然失败）")
    bad(["--link=" + os.path.join(d, "quiet.txt")], "不认识",
        "`--link=` 后缀不认识 ⇒ rc=2")
    bad(["--link-dir=" + os.path.join(d, "nope_dir")], "不是目录",
        "`--link-dir=` 不是目录 ⇒ rc=2")
    bad(["--link-dir="], "要一个目录", "`--link-dir=` 空串 ⇒ rc=2")
    bad(["--lib="], "要一个库名", "`--lib=` 空串 ⇒ rc=2")
    bad(["--lib=bad name"], "不是合法库名", "`--lib=` 含空格 ⇒ rc=2")

    r = run_dsh(["cpp"])
    check("没有模块时 usage 里有新开关",
          "--link=" in both(r) and "--link-dir=" in both(r) and "--lib=" in both(r),
          both(r)[-300:])
    ok = run_dsh(["cpp", eln], env_extra=env)
    check("合法调用仍然 rc=0（新参数没把原路径弄坏）", ok.returncode == 0,
          both(ok)[-300:])


# ---------------------------------------------------------------- ④ 真编真链真跑
SHIM_H = """#include <stdint.h>
uint64_t el_add(uint64_t a, uint64_t b);
"""
SHIM_C = """#include "shim.h"
uint64_t el_add(uint64_t a, uint64_t b) { return a + b; }
"""

ELN_ADD = """extern el_add(u64 a, u64 b) -> u64 from "{lib}" effects {{}};

u64 main() {{
    unsafe {{ print(el_add(19, 23)); }}
    return 0;
}}
"""

ELN_NO_MAIN = """extern el_add(u64 a, u64 b) -> u64 from "{lib}" effects {{}};

u64 add2(u64 a, u64 b) {{
    unsafe {{ return el_add(a, b); }}
}}
"""

#: `main` 返回**非整数**：`dsh run` 末行是 `True`，不是能剥掉的整数返回值。
#: 这时硬比会把"方法判不了"误报成"生成物错"——必须明确跳过，且不许说对拍一致。
ELN_BOOL_MAIN = """u64 helper() { return 1; }

bool main() {
    print(7);
    return true;
}
"""


ELN_CJSON = """extern cJSON_Version() -> vector<u8> from "{lib}" effects {{}};
u64 main() {{
    unsafe {{
        vector<u8> v = cJSON_Version();
        print(v);
    }}
    return 0;
}}
"""


def _build_lib(d, gcc, cxx):
    """编 shim.h / shim.c → `.o`、`.a`、`.dll`；返回 (obj, libdir, dll, ar|None)。"""
    with open(os.path.join(d, "shim.h"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(SHIM_H)
    with open(os.path.join(d, "shim.c"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(SHIM_C)
    # TMP/TEMP 指到工作区（本机 MinGW 在系统 %TEMP% 下会 Permission denied）。
    env = dict(os.environ, TMP=d, TEMP=d)
    obj = os.path.join(d, "shim.o")
    r = subprocess.run([gcc, "-c", "-O2", os.path.join(d, "shim.c"), "-o", obj],
                       cwd=d, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    check("C 库编成 .o（链接用）", r.returncode == 0, (r.stderr or "")[-300:])
    dll = os.path.join(d, "shim.dll" if os.name == "nt" else "shim.so")
    share = [gcc, "-shared", "-O2", "-o", dll, os.path.join(d, "shim.c")]
    share.append("-Wl,--export-all-symbols" if os.name == "nt" else "-fPIC")
    r = subprocess.run(share, cwd=d, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    check("C 库编成动态库（解释器侧要用）", r.returncode == 0, (r.stderr or "")[-300:])
    ar = _find_ar(cxx)
    if ar:
        r = subprocess.run([ar, "rcs", os.path.join(d, "libshim.a"), obj],
                           cwd=d, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        check("`.a` 打出来了（--link-dir + --lib 那条路）", r.returncode == 0,
              (r.stderr or "")[-300:])
    return obj, d, dll, ar


def _write_eln(path, tmpl, dll):
    lib = dll.replace("\\", "/")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(tmpl.format(lib=lib))


def test_real_run(tmp):
    print("[4] 真编真链真跑：`.o` 进链接 → 生成物 stdout 与 `dsh run` 逐字节一致")
    cxx_argv = _find_cxx()
    gcc = _find_gcc(cxx_argv[0] if cxx_argv else None)
    if not cxx_argv or not gcc:
        print("  --   本机没有 g++ / gcc：端到端**这一项没有验证**（不是通过）")
        return
    cxx = cxx_argv[0]
    if os.path.basename(cxx).lower() in ("cl", "cl.exe"):
        print("  --   编译器是 cl：用例只写了 g++/clang++ 的参数，**没有验证**")
        return
    d = os.path.join(tmp, "link")
    os.makedirs(d, exist_ok=True)
    env = dict(ENV_BASE, TMP=d, TEMP=d, ELENCH_CXX=cxx)
    env["PATH"] = os.path.dirname(cxx) + os.pathsep + env.get("PATH", "")
    obj, libdir, dll, ar = _build_lib(d, gcc, cxx)
    eln = os.path.join(d, "m.eln")
    cpp = os.path.join(d, "m.cpp")
    _write_eln(eln, ELN_ADD, dll)

    r = run_dsh(["cpp", eln, "--output=" + cpp, "--run", "--link=" + obj],
                env_extra=env, cwd=d)
    msg = both(r)
    check("`--run --link=x.o` rc=0", r.returncode == 0, msg[-500:])
    check("真的**对拍**过（不是只编译）", "对拍一致" in msg, msg[-400:])
    check("生成物的 stdout 显示出来了（42）",
          "生成物 stdout" in msg and "42" in msg, msg[-400:])
    text = open(cpp, encoding="utf-8").read() if os.path.exists(cpp) else ""
    check("生成物里有 `extern \"C\"` 声明",
          'extern "C" uint64_t el_add(uint64_t a, uint64_t b);' in text, text[:300])
    check("链接输入**没有**被写进生成物（本机路径不烘焙）",
          obj not in text and obj.replace("\\", "/") not in text, "生成物里出现了 .o 路径")

    # 独立再跑一遍解释器：--run 内部比的是它，这里把口径本身也钉住。
    ir = subprocess.run([sys.executable, "-B", os.path.join(HERE, "dsh.py"),
                         "run", eln],
                        cwd=d, capture_output=True, env=env)
    interp = dsh._cpp_strip_run_return_line(ir.stdout or b"")
    check("`dsh run` 自己跑得起来", ir.returncode == 0,
          (ir.stderr or b"")[-300:].decode("utf-8", "replace"))
    check("解释器 stdout（剥末行）是 `42`",
          interp.strip() == b"42", repr(interp))

    if ar:
        r = run_dsh(["cpp", eln, "--output=" + cpp, "--run",
                     "--link-dir=" + libdir, "--lib=shim"], env_extra=env, cwd=d)
        check("`--link-dir=` + `--lib=` rc=0 且对拍一致",
              r.returncode == 0 and "对拍一致" in both(r), both(r)[-400:])
    else:
        print("  --   没有 `ar`：`--link-dir=` + `--lib=` 那条路**没有验证**（不是通过）")

    r = run_dsh(["cpp", eln, "--output=" + cpp, "--run", "--link=" + obj, "--no-static"],
                env_extra=env, cwd=d)
    check("`--no-static` 也能跑通（PATH 里有 MinGW 的运行时 DLL）",
          r.returncode == 0 and "对拍一致" in both(r), both(r)[-400:])

    # `-static` 的**意义**：PATH 里没有 MinGW 时，静态链接的 exe 仍然跑得起来。
    exe2 = os.path.join(d, "static_probe" + (".exe" if os.name == "nt" else ""))
    cmd = dsh._cpp_link_cmd([cxx], cpp, exe2, include_dirs=[d],
                            link_files=[obj], static=True)
    r = subprocess.run(cmd, cwd=d, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    check("按 `_cpp_link_cmd(static=True)` 链接成功", r.returncode == 0,
          (r.stderr or "")[-400:])
    bare = dict(env)
    bare["PATH"] = os.pathsep.join(
        p for p in env.get("PATH", "").split(os.pathsep)
        if p and os.path.normcase(os.path.abspath(p)) !=
        os.path.normcase(os.path.abspath(os.path.dirname(cxx))))
    got = subprocess.run([exe2], cwd=d, capture_output=True, env=bare)
    check("**PATH 里没有 MinGW 也能跑**（-static 挡住 STATUS_DLL_NOT_FOUND）",
          got.returncode == 0 and (got.stdout or b"").strip() == b"42",
          f"rc={got.returncode} out={got.stdout!r} err={(got.stderr or b'')[-200:]!r}")

    # ---- 不给 --output：stdout 必须只有源码（状态行 / 程序输出都在 stderr）
    r = run_dsh(["cpp", eln, "--run", "--link=" + obj], env_extra=env, cwd=d)
    check("不给 `--output` 时 rc=0 且对拍一致",
          r.returncode == 0 and "对拍一致" in (r.stderr or ""), both(r)[-400:])
    check("stdout **只有源码**：没有状态行、也没有程序输出 42",
          (r.stdout or "").startswith("// 由 elench cpp 生成")
          and "对拍一致" not in (r.stdout or "")
          and "42" not in (r.stdout or ""), (r.stdout or "")[-300:])
    check("程序输出走 stderr（`dsh cpp m --run > m.cpp` 不会被污染）",
          "生成物 stdout" in (r.stderr or "") and "42" in (r.stderr or ""),
          (r.stderr or "")[-300:])

    # ---- 链接失败：rc≠0，说清缺什么（不是跳过）
    r = run_dsh(["cpp", eln, "--output=" + cpp, "--run"], env_extra=env, cwd=d)
    msg = both(r)
    check("不递 .o ⇒ rc≠0（链接失败不是跳过）", r.returncode != 0, msg[-400:])
    check("报错里有未定义符号 / 缺库的提示",
          ("undefined reference" in msg or "unresolved external symbol" in msg)
          and ("--link=" in msg or "--lib=" in msg), msg[-500:])
    leftovers = [n for n in os.listdir(d) if n.startswith("elench_cpprun_")]
    check("失败后不留临时目录（跑完即删）", not leftovers, str(leftovers))

    # ---- 没有 main：--link 明确“未使用”，不算通过
    eln2 = os.path.join(d, "nom.eln")
    _write_eln(eln2, ELN_NO_MAIN, dll)
    r = run_dsh(["cpp", eln2, "--output=" + os.path.join(d, "nom.cpp"),
                 "--run", "--link=" + obj], env_extra=env, cwd=d)
    msg = both(r)
    check("没有 main ⇒ rc=0 但明说只验到编译",
          r.returncode == 0 and "未生成可执行文件" in msg, msg[-400:])
    check("没有 main ⇒ `--link=` 被标成**未使用**（不是验证通过）",
          "未使用" in msg, msg[-400:])

    # ---- main 返回非整数：分不清返回值与程序输出 ⇒ 跳过，不许冒充对拍一致
    eln3 = os.path.join(d, "boolmain.eln")
    with open(eln3, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(ELN_BOOL_MAIN)
    r = run_dsh(["cpp", eln3, "--output=" + os.path.join(d, "boolmain.cpp"),
                 "--run"], env_extra=env, cwd=d)
    msg = both(r)
    check("`bool main()` ⇒ rc=0 且明说「对拍跳过，未测量」",
          r.returncode == 0 and "对拍跳过，未测量" in msg, msg[-500:])
    check("`bool main()` ⇒ **不**说「对拍一致」（没比过就不许说比过）",
          "对拍一致" not in msg, msg[-400:])

    # ---- `--compat` 要递给解释器：否则对拍的两边读的不是同一个口径
    eln4 = os.path.join(d, "compat.eln")
    with open(eln4, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("using Num = u64;\n\nu64 main() {\n    Num x = 5;\n"
                 "    print(x);\n    return 0;\n}\n")
    r = run_dsh(["cpp", eln4, "--output=" + os.path.join(d, "compat.cpp"),
                 "--compat", "--run"], env_extra=env, cwd=d)
    msg = both(r)
    check("`--compat --run` rc=0（`--compat` 被递给 `dsh run`，解释器侧同一个口径）",
          r.returncode == 0 and "对拍一致" in msg, msg[-500:])
    check("生成物 stdout 里有 5（是真的跑到了）",
          "生成物 stdout" in msg and "5" in msg, msg[-300:])


# ---------------------------------------------------------------- ⑤ cJSON
def test_cjson(tmp):
    print("[5] cJSON 1.7.19 全链路（本机有 build/real-libs/cJSON 才跑）")
    libdir = os.path.join(ROOT, "build", "real-libs", "cJSON")
    if not (os.path.isfile(os.path.join(libdir, "cJSON.c"))
            and os.path.isfile(os.path.join(libdir, "cJSON.h"))):
        print("  --   build/real-libs/cJSON 不在（gitignored）：**跳过（未验证）**，"
              "不冒充通过")
        return
    cxx_argv = _find_cxx()
    gcc = _find_gcc(cxx_argv[0] if cxx_argv else None)
    if not cxx_argv or not gcc:
        print("  --   本机没有 g++ / gcc：**跳过（未验证）**")
        return
    if os.path.basename(cxx_argv[0]).lower() in ("cl", "cl.exe"):
        print("  --   编译器是 cl：**跳过（未验证）**")
        return
    d = os.path.join(tmp, "cjson")
    os.makedirs(d, exist_ok=True)
    env = dict(ENV_BASE, TMP=d, TEMP=d, ELENCH_CXX=cxx_argv[0])
    env["PATH"] = os.path.dirname(cxx_argv[0]) + os.pathsep + env.get("PATH", "")
    cjson_c = os.path.join(libdir, "cJSON.c")
    obj = os.path.join(d, "cJSON.o")
    r = subprocess.run([gcc, "-c", "-O2", cjson_c, "-o", obj], cwd=d,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    check("cJSON.c → cJSON.o", r.returncode == 0, (r.stderr or "")[-300:])
    dll = os.path.join(d, "cjson.dll" if os.name == "nt" else "libcjson.so")
    share = [gcc, "-shared", "-O2", "-o", dll, cjson_c,
             "-Wl,--export-all-symbols"]
    r = subprocess.run(share, cwd=d, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    check("cJSON.c → 动态库（解释器侧）", r.returncode == 0, (r.stderr or "")[-300:])

    eln = os.path.join(d, "ver.eln")
    _write_eln(eln, ELN_CJSON, dll)
    out = os.path.join(d, "ver.cpp")
    r = run_dsh(["cpp", eln, "--output=" + out, "--include=cJSON.h",
                 "--include-dir=" + libdir, "--run", "--link=" + obj],
                env_extra=env, cwd=d)
    msg = both(r)
    check("一条命令 rc=0（编译 + 链接 + 跑 + 对拍）", r.returncode == 0, msg[-500:])
    check("输出 `1.7.19`", "1.7.19" in msg, msg[-400:])
    check("与 `dsh run` 逐字节一致", "对拍一致" in msg, msg[-300:])


def main() -> int:
    tmp = os.path.join(ROOT, "build", "run_link_test")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        test_cmd_shape()
        test_strip_parity()
        test_cli_validation(tmp)
        test_real_run(tmp)
        test_cjson(tmp)
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
