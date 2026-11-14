#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_cpp_extern.py —— `dsh cpp` 发 **extern 声明** / **第三方 include**；自检失败不留文件

## 为什么这条测试必须存在

真库（xxHash / cJSON）接入的最后一道门在这里断过：

    extern XXH64(const vector<u8>&, u64, u64) -> u64 from "xxhash" effects {};
    # check ✓ 通过
    $ dsh cpp xxh.eln > xxh.cpp
    # 生成的 C++ 里**没有**那句声明 ⇒ g++ 报 'XXH64' was not declared

`extern "C"` 以前只在 `dsh header` 里发（`cmd_header`），`_gen_cpp` 一条都不发；
第三方 include 也没有任何通道（18 个写死的标准头，0 条第三方头）。
外加一个危险行为：**编译自检失败仍然写出 .cpp 并打印路径**，构建脚本会当成成功。

## 钉住什么

1. `.eln` 顶层的 `extern …;` → 生成物顶部有 `extern "C" <ffi_c 映射> …;`；
2. `.tu` 的 `spec … { external "lib"; }` 是**同一件事**，也发（且按名字去重）；
3. 容器形参在调用点取 `.data()`（`std::vector` 不会隐式转成 `const char*`）；
4. `--include=` 可重复、在 18 个标准头之后 / `using namespace std;` 之前；
   头名里的引号 / 尖括号 / 换行被拒绝（那是唯一能往生成物里塞任意 C++ 的通道）；
5. **自检失败 ⇒ 不写 / 删掉输出文件**，stderr 说清（假编译器验证，不依赖 g++）；
6. 有编译器时**真编真链真跑**：本地 C 库（`extern "C"` + 头文件）→ 生成物 → exe → 42。

跑法：`python -B tests/cases/test_cpp_extern.py`
"""
import os
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
#: 与 `test_cpp.py` 同一条旧约定：`HERE` 指 `src/`（`dsh.py` 在那）。
HERE = SRC
for _p in (SRC, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dsh                                                   # noqa: E402
from parser import parse_source                              # noqa: E402

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


def run_cpp(files, name="m", args=(), env=None, want_output=False, d=None):
    """把 `files` 写进临时目录，跑一次 `dsh cpp <name>.eln`；返回 `(proc, out_path, dir)`。"""
    d = d or tempfile.mkdtemp(prefix="eln_cpp_ext_")
    for fn, text in files.items():
        with open(os.path.join(d, fn), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    out = os.path.join(d, name + ".cpp") if want_output else None
    cmd = [sys.executable, "-B", os.path.join(HERE, "dsh.py"), "cpp",
           os.path.join(d, name + ".eln")]
    if out:
        cmd.append("--output=" + out)
    cmd += list(args)
    e = dict(ENV_BASE)
    e.update(env or {})
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=e)
    return r, out, d


def both(r):
    return (r.stdout or "") + (r.stderr or "")


# ---------------------------------------------------------------- ① extern 发射
ELN_ELN = """extern XXH64(const vector<u8>& data, u64 len, u64 seed) -> u64 from "xxhash"
    effects {};

u64 hash_of(const vector<u8>& s) {
    u64 h = 0;
    unsafe { h = XXH64(s, s.size(), 0); }
    return h;
}
"""


def test_extern_from_eln():
    print("[1] `.eln` 的 extern → `extern \"C\"` 声明（返回类型用 ffi_c 的映射）")
    r, _out, _d = run_cpp({"m.eln": ELN_ELN})
    text = r.stdout or ""
    check("rc=0（没有 .tu 只有警告，不阻断）", r.returncode == 0, both(r)[-300:])
    check("发 `extern \"C\" uint64_t XXH64(const char*, uint64_t, uint64_t);`",
          'extern "C" uint64_t XXH64(const char* data, uint64_t len, uint64_t seed);'
          in text, text[:400])
    check("带来源注释 `// from \"xxhash\"`", '// from "xxhash"' in text, text[:400])
    check("声明在 `using namespace std;` 之前",
          text.find('extern "C" uint64_t XXH64') < text.find("using namespace std;"),
          "声明不在顶部")
    check("调用点把容器取 `.data()`（vector 不会隐式转指针）",
          "XXH64(reinterpret_cast<const char*>(s.data()), s.size(), 0)" in text,
          text[-500:])

    print("[1b] `.tu` 的 `spec … { external \"lib\"; }` 走同一套（并且去重）")
    tu = parse_source(
        "spec ext_double(u64 x) -> u64 {\n"
        '    external "shim";\n'
        "    effects {};\n"
        "    target { time: { max: O(1) }, allocation: { max: 0 }, };\n"
        "}\n", "m.tu").module
    ch = parse_source(
        "extern ext_double(u64 x) -> u64 from \"shim\" effects {};\n", "m.eln").module
    lines, params = dsh._cpp_extern_decls(tu, ch)
    check("两种写法（`.tu` spec / `.eln` extern）都认、按名字只发一条",
          lines == ['extern "C" uint64_t ext_double(uint64_t x);  // from "shim"'],
          str(lines))
    check("形参表被记下来（不转换，纯标量）", params == {"ext_double": [False]},
          str(params))

    print("[1c] ffi_c 认不出的类型 ⇒ 报错，不发按错 ABI 的声明")
    ch2 = parse_source(
        "extern f(u32 x) -> u32 from \"x\" effects {};\n", "m.eln").module
    try:
        dsh._cpp_extern_decls(None, ch2)
        ok, why = False, "没有报错"
    except dsh.CppUnsupported as exc:
        ok, why = True, str(exc)
    check("`u32` 没有 C ABI 映射（`shape.py` 只认 u64/i64）⇒ CppUnsupported",
          ok, why)
    check("错误里点名 ffi_c（不是静默猜 uint64_t）",
          ok and "ffi_c" in why, why)

    print("[1d] `Vec<u8>` 返回走 ffi_c 的 `_CString` ⇒ 声明成 `const char*`")
    ch3 = parse_source(
        "extern cJSON_Version() -> vector<u8> from \"cjson\" effects {};\n",
        "m.eln").module
    lines3, _p3 = dsh._cpp_extern_decls(None, ch3)
    check("`vector<u8>` 返回 ⇒ `const char*`（ffi_c 的映射，不是 cxx_map 的 vector）",
          lines3 == ['extern "C" const char* cJSON_Version();  // from "cjson"'],
          str(lines3))


# ---------------------------------------------------------------- ② include
def test_include_channel():
    print("[2] `--include=`：在标准头之后、代码之前；可重复；注入被拒")
    # 三个头真实存在（自检会真编）：空头就够，验的是**位置与通道**，不是库内容。
    d = tempfile.mkdtemp(prefix="eln_cpp_inc_")
    for h in ("alpha.h", "beta.h", "gamma.h"):
        with open(os.path.join(d, h), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("// test header\n")
    r, _out, _d = run_cpp(
        {"m.eln": "u64 main() {\n    return 0;\n}\n"},
        args=["--include=alpha.h", "--include=beta.h", "--include=<gamma.h>",
              "--include-dir=" + d], d=d)
    text = r.stdout or ""
    check("rc=0", r.returncode == 0, both(r)[-300:])
    i_std = text.find("#include <vector>")
    i_a = text.find('#include "alpha.h"')
    i_b = text.find('#include "beta.h"')
    i_g = text.find("#include <gamma.h>")
    i_use = text.find("using namespace std;")
    check("三条 include 都发了", min(i_a, i_b, i_g) >= 0,
          f"alpha@{i_a} beta@{i_b} gamma@{i_g}")
    check("在 18 个标准头之后", i_std >= 0 and i_std < i_a, f"std@{i_std} alpha@{i_a}")
    check("在 `using namespace std;`（代码）之前", max(i_a, i_b, i_g) < i_use,
          f"last include@{max(i_a, i_b, i_g)} using@{i_use}")
    check("顺序就是命令行给的顺序", i_a < i_b < i_g, f"{i_a},{i_b},{i_g}")

    print("[2b] 头名里能塞任意 C++ 的字符被拒（rc=2，且不生成源码）")
    for bad in ('bad"name.h', "bad<name.h", "bad\nname.h"):
        r2, _o, _d2 = run_cpp({"m.eln": "u64 main() {\n    return 0;\n}\n"},
                              args=["--include=" + bad])
        check(f"`--include={bad!r}` ⇒ rc=2、`✗` 说明",
              r2.returncode == 2 and "✗" in (r2.stderr or "")
              and not (r2.stdout or "").strip(),
              both(r2)[-200:])
    r3, _o, _d3 = run_cpp({"m.eln": "u64 main() {\n    return 0;\n}\n"},
                          args=["--include="])
    check("`--include=`（空串）⇒ rc=2", r3.returncode == 2, both(r3)[-200:])


# ---------------------------------------------------------------- ③ 自检失败
def test_selfcheck_failure_leaves_no_file():
    print("[3] 编译自检失败 ⇒ **不写 / 删掉**输出文件（假编译器，不依赖 g++）")
    d = tempfile.mkdtemp(prefix="eln_fakecc_")
    fake = os.path.join(d, "fake_cc.py")
    with open(fake, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("import sys\n"
                 "sys.stderr.write('fake-compiler: 我拒绝这段代码\\n')\n"
                 "sys.exit(3)\n")
    env = {"ELENCH_CXX": f'"{sys.executable}" "{fake}"'}
    files = {"m.eln": "u64 main() {\n    return 0;\n}\n"}

    # 先造一个"上一轮的旧产物"，验证它**会被删掉**（留着会被当成这次成功）。
    r0, out_path, dd = run_cpp(files, env=env, want_output=True)
    check("自检失败时本来就没写文件（旧文件另造）", not os.path.exists(out_path),
          out_path)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("// STALE：上一轮成功留下的旧文件\n")
    r, out, _d = run_cpp(files, env=env, want_output=True, d=dd)
    check("rc≠0", r.returncode != 0, str(r.returncode))
    check("stderr 说清是「编译自检失败」", "编译自检失败" in (r.stderr or ""),
          both(r)[-400:])
    check("旧产物被删掉（构建脚本不会把它当成成功）", not os.path.exists(out), out)
    check("stderr 说明删了哪个文件", "已删除" in (r.stderr or ""), both(r)[-400:])

    r2, _o, _d2 = run_cpp(files, env=env)
    check("stdout 模式：编不过 ⇒ stdout 一个字节都不写",
          not (r2.stdout or ""), (r2.stdout or "")[:120])
    check("stdout 模式：rc≠0", r2.returncode != 0, str(r2.returncode))


# ---------------------------------------------------------------- ④ 真编真链
SHIM_C = """#include <stdint.h>
uint64_t el_ext_double(uint64_t x) { return x * 2u; }
"""
SHIM_H = """#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
uint64_t el_ext_double(uint64_t x);
#ifdef __cplusplus
}
#endif
"""
SHIM_ELN = """extern el_ext_double(u64 x) -> u64 from "shim" effects {};

u64 main() {
    u64 v = 0;
    unsafe { v = el_ext_double(21); }
    print(v);
    return 0;
}
"""


def _compiler_env(cxx):
    """编译器所在目录进 PATH（MinGW 的 exe 运行时要 libstdc++-6.dll 等），
    TMP/TEMP 指到工作区内（MinGW 在系统 %TEMP% 下会 Permission denied）。"""
    d = tempfile.mkdtemp(prefix="eln_cc_tmp_")
    env = dict(ENV_BASE)
    env["TMP"] = env["TEMP"] = d
    env["PATH"] = os.path.dirname(cxx) + os.pathsep + env.get("PATH", "")
    return env, d


def test_real_link():
    print("[4] 真编真链真跑：本地 C 库（extern \"C\" + 头文件）→ 生成物 → 42")
    cxx_argv = dsh._cxx_compiler_argv()
    if not cxx_argv:
        print("  --   本机没有 C++ 编译器（ELENCH_CXX / g++ / clang++ / cl）："
              "**这一项没有验证**（不是通过）")
        return
    cxx = cxx_argv[0]
    if os.path.basename(cxx).lower() in ("cl", "cl.exe"):
        print("  --   编译器是 cl：这条用例只写了 g++/clang++ 的参数，"
              "**这一项没有验证**（不是通过）")
        return
    d = tempfile.mkdtemp(prefix="eln_cpp_link_")
    for fn, text in (("shim.c", SHIM_C), ("shim.h", SHIM_H),
                     ("shim.eln", SHIM_ELN)):
        with open(os.path.join(d, fn), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    cpp = os.path.join(d, "shim.cpp")
    env, tmpd = _compiler_env(cxx)
    gen = subprocess.run(
        [sys.executable, "-B", os.path.join(HERE, "dsh.py"), "cpp",
         os.path.join(d, "shim.eln"), "--output=" + cpp,
         "--include=shim.h", "--include-dir=" + d],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=dict(env, ELENCH_CXX=cxx))
    text = open(cpp, encoding="utf-8").read() if os.path.exists(cpp) else ""
    check("生成 rc=0（自检也过）", gen.returncode == 0,
          both(gen)[-400:])
    check("生成物里有 `#include \"shim.h\"`", '#include "shim.h"' in text,
          text[:300])
    check("生成物里有 `extern \"C\" … el_ext_double(…)`",
          "el_ext_double(uint64_t x)" in text, text[:500])

    obj = os.path.join(d, "shim.o")
    rc = subprocess.run([cxx, "-x", "c", "-c", os.path.join(d, "shim.c"),
                         "-o", obj],
                        cwd=d, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", env=env)
    check("C 库编译成功（-x c）", rc.returncode == 0, (rc.stderr or "")[-300:])
    exe = os.path.join(d, "shim.exe" if os.name == "nt" else "shim.out")
    cmd = [cxx, "-std=c++17"]
    if os.name == "nt":
        cmd.append("-static")      # 运行时不依赖 MinGW 的 DLL
    cmd += [cpp, obj, "-o", exe]
    lk = subprocess.run(cmd, cwd=d, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", env=env)
    check("生成物 + C 库链接成功", lk.returncode == 0, (lk.stderr or "")[-400:])
    if lk.returncode == 0:
        got = subprocess.run([exe], cwd=d, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", env=env)
        check("跑出 42（21*2，真调进了 C 库）", (got.stdout or "").strip() == "42",
              repr(got.stdout) + (got.stderr or "")[-200:])
    import shutil
    for p in (d, tmpd):
        shutil.rmtree(p, ignore_errors=True)


def main() -> int:
    test_extern_from_eln()
    test_include_channel()
    test_selfcheck_failure_leaves_no_file()
    test_real_link()
    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
