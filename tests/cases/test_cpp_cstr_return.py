#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_cpp_cstr_return.py —— C 函数返回 `const char*`：调用点包装 + NULL **明确失败**

## 为什么这条测试必须存在

上一轮把「指针返回 → NUL 结尾的 `Vec<u8>`」做到了 `ffi_c` 与**声明**：

    extern "C" const char* cJSON_Version();       ← 声明是对的
    vector<u8> v = cJSON_Version();               ← 调用点编不过

`std::vector<uint8_t>` **不会**从 `const char*` 隐式构造，于是 `dsh cpp` 的编译
自检把它拦下来、不留文件（安全，但功能没通）。功能于是停在一个"安全地没有"
的状态：能生成声明、不能真正调用。

这一轮补的是**最后一段**：运行时助手 `elench_cstr`（按 NUL 求长、逐字节拷）
+ 调用点包装。缺口本身很小，但它是 cJSON / 任何返回字符串的 C 库的**唯一入口**。

## 钉住什么

1. **运行时助手**：`elench_cstr(const char*, const char*)` 在 `_CPP_RUNTIME` 里，
   按 NUL 求长（`std::strlen`）后逐字节拷成 `std::vector<uint8_t>`；
2. **只对指针返回包**：判据来自 `ffi_c.restype_for`（同一张表），`u64` 返回的
   external **不**包——包错会把数字变成字节串（静默错值）；
3. **没有第二张类型表**：monkeypatch `ffi_c.restype_for` 直接改变发射决定
   （把它改成 `c_uint64` 后，同一个 extern 立刻不再被包）；
4. **两种声明写法同一条路**：`.eln` 的 `extern … from` 与 `.tu` 的
   `spec … { external …; }`；
5. **NULL 明确失败**：`Vec<u8>` 表示不了 NULL，所以既不能返回空 vector（那是把
   NULL 和 `""` 混成一个值 = 静默错值），也不能假装成功。生成物里 stderr 点名
   是哪个外部函数、然后 abort（退出码非 0）；解释器侧同样明确失败
   （`E-FFI-Unsupported`）。**两边都不许安静地成功**；
6. **真编真链真跑**：本地 C 库返回 `const char*` → 生成物 + `.o` 链接 → 运行，
   stdout 与该 C 库的真串逐字节一致，并且与 `dsh run`（真 dll，剥掉 main 返回值
   那一行后的同一口径）**逐字节相同**。

跑法：`python -B tests/cases/test_cpp_cstr_return.py`
"""
import ctypes
import os
import shutil
import subprocess
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
#: 与 `test_cpp.py` / `test_cpp_extern.py` 同一条旧约定：`HERE` 指 `src/`（`dsh.py` 在那）。
HERE = SRC
#: **放在 `src` 之后**（append，不是 insert）：`tools/walk.py` / `tools/bound.py` 与
#: `src/` 里的同名模块重名，抢在 `src` 前面会让 `import walk` 拿到另一个东西。
TOOLS = os.path.join(ROOT, "tools")
for _p in (SRC, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
if TOOLS not in sys.path:
    sys.path.append(TOOLS)

import dsh                                                   # noqa: E402
from parser import parse_source                              # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                            # noqa: BLE001
    pass

FAILED = []
ENV_BASE = dict(os.environ, PYTHONIOENCODING="utf-8")

#: `dsh run` 会把 main 的返回值打一行——那不是程序 stdout。**复用已有口径**
#: （`tools/behavior_parity.strip_return_line`），不在这里写第三份。
from behavior_parity import strip_return_line                 # noqa: E402


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


# ---------------------------------------------------------------- ① 生成物文本
ELN_VER = """extern cJSON_Version() -> vector<u8> from "cjson" effects {};

u64 main() {
    unsafe {
        vector<u8> v = cJSON_Version();
        print(v);
    }
    return 0;
}
"""

ELN_SCALAR = """extern XXH64(const vector<u8>& data, u64 len, u64 seed) -> u64 from "xxhash"
    effects {};

u64 main() {
    u64 h = 0;
    unsafe { h = XXH64("abc", 3, 0); }
    print(h);
    return 0;
}
"""


def test_runtime_helper():
    print("[1] 运行时助手：按 NUL 求长逐字节拷；NULL 分支是 abort，不是空 vector")
    rt = dsh._CPP_RUNTIME
    check("`elench_cstr(const char*, const char*)` 在 `_CPP_RUNTIME` 里",
          "static std::vector<uint8_t> elench_cstr(const char* p, const char* fn)" in rt,
          "助手不在运行时里")
    check("按 NUL 求长（`std::strlen(p)`）", "std::strlen(p)" in rt, "没有按 NUL 求长")
    check("逐字节拷进 `std::vector<uint8_t>`（不是 `std::string`）",
          "reinterpret_cast<const uint8_t*>(p)" in rt, "没看到逐字节拷贝")
    check("NULL 分支存在", "if (p == nullptr)" in rt, "没有判空")
    check("NULL 分支 **abort**（明确失败），不在值上兜底",
          rt.find("std::abort()") > rt.find("if (p == nullptr)") >= 0,
          "NULL 分支没有 abort")
    check("助手**不**返回 `std::vector<uint8_t>()`（不静默折叠成空串）",
          "return std::vector<uint8_t>();" not in rt, "有空的兜底返回值")
    check("stderr 里点出函数名（`%s` 用的是传进来的 `fn`）",
          "外部函数 %s" in rt or "外部函数" in rt, "消息里没有函数名")


def test_call_site_wrap():
    print("[2] 调用点：只有「返回类型在 ffi_c 里是 `_CString`」的 external 被包")
    ch = parse_source(ELN_VER, "m.eln").module
    gen = dsh._gen_cpp(None, ch)
    check("声明是 `extern \"C\" const char* cJSON_Version();`",
          'extern "C" const char* cJSON_Version();' in gen, gen[:400])
    check("调用点包成 `elench_cstr(cJSON_Version(), \"cJSON_Version\")`",
          'elench_cstr(cJSON_Version(), "cJSON_Version")' in gen, gen[-600:])
    check("直接初始化那个形状（`vector<u8> v = …`）也有包装",
          "std::vector<uint8_t> v = elench_cstr(cJSON_Version(), \"cJSON_Version\");" in gen,
          gen[-600:])

    ch2 = parse_source(ELN_SCALAR, "m.eln").module
    gen2 = dsh._gen_cpp(None, ch2)
    check("`u64` 返回的 external **不**被包（包错 = 把数字变字节串）",
          "elench_cstr(XXH64" not in gen2 and "XXH64(reinterpret_cast" in gen2,
          gen2[-500:])

    print("[2b] `_cpp_extern_vec_returns` 只收指针返回（同一份扫描）")
    check("`vector<u8>` 返回 → 在集合里",
          dsh._cpp_extern_vec_returns(None, ch) == {"cJSON_Version"},
          str(dsh._cpp_extern_vec_returns(None, ch)))
    check("`u64` 返回 → 不在集合里", dsh._cpp_extern_vec_returns(None, ch2) == set(),
          str(dsh._cpp_extern_vec_returns(None, ch2)))

    print("[2c] `.tu` 的 spec 写法走同一条路（`external \"cjson\";`）")
    tu = parse_source(
        "spec cJSON_Version() -> vector<u8> {\n"
        '    external "cjson";\n'
        "    effects {};\n"
        "    target { time: { max: O(1) }, allocation: { max: 0 }, };\n"
        "}\n", "m.tu").module
    check("spec 形态也被认出来", dsh._cpp_extern_vec_returns(tu, None) == {"cJSON_Version"},
          str(dsh._cpp_extern_vec_returns(tu, None)))
    lines, params = dsh._cpp_extern_decls(tu, None)
    check("声明只发一条、形参表为空",
          lines == ['extern "C" const char* cJSON_Version();  // from "cjson"']
          and params == {"cJSON_Version": []}, str((lines, params)))

    print("[2d] 两种写法同时出现 ⇒ 按名字去重，集合里仍然只有一条")
    both_mods = [parse_source(ELN_VER, "m.eln").module, tu]
    lines2, _p2 = dsh._cpp_extern_decls(both_mods[1], both_mods[0])
    check("只发一条声明", len(lines2) == 1, str(lines2))
    check("集合里也只有一条",
          dsh._cpp_extern_vec_returns(both_mods[1], both_mods[0]) == {"cJSON_Version"})


def test_same_table():
    print("[3] 判据同源：改 `ffi_c.restype_for`，发射决定跟着变（没有第二张表）")
    import ffi_c
    check("`vector<u8>` 在 ffi_c 里就是 `_CString`",
          ffi_c.restype_for("vector<u8>") is ffi_c._CString, "ffi_c 的映射变了")
    ch = parse_source(ELN_VER, "m.eln").module
    _old = ffi_c.restype_for
    try:
        ffi_c.restype_for = lambda ty: ctypes.c_uint64
        got = dsh._cpp_extern_vec_returns(None, ch)
    finally:
        ffi_c.restype_for = _old
    check("ffi_c 说它不是指针 ⇒ 集合空（生成器**没有**自己再判一遍 `vector<u8>`）",
          got == set(), str(got))
    check("恢复后仍然正常", dsh._cpp_extern_vec_returns(None, ch) == {"cJSON_Version"})


def test_dsh_cpp_selfcheck(tmp):
    print("[4] `dsh cpp`：这个形状以前过不了编译自检（现在 rc=0 且写出文件）")
    d = os.path.join(tmp, "selfcheck")
    os.makedirs(d, exist_ok=True)
    eln = os.path.join(d, "m.eln")
    out = os.path.join(d, "m.cpp")
    with open(eln, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(ELN_VER)
    r = run_dsh(["cpp", eln, "--output=" + out],
                env_extra={"TMP": d, "TEMP": d})
    check("rc=0", r.returncode == 0, both(r)[-500:])
    check("文件写出来了", os.path.exists(out), out)
    text = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
    check("生成物里有包装", 'elench_cstr(cJSON_Version(), "cJSON_Version")' in text,
          text[-400:])
    if dsh._cxx_compiler_argv():
        check("自检**真的**编过（不是跳过）", "编译自检通过" in (r.stderr or ""),
              both(r)[-300:])
    else:
        print("  --   本机没有 C++ 编译器：只验到「生成了正确文本」，没验编译")


# ---------------------------------------------------------------- ② 真编真链
SHIM_C = """#include <stddef.h>
const char* el_str(void) { return "hello from C"; }
const char* el_null(void) { return NULL; }
"""

ELN_LINK = """extern el_str() -> vector<u8> from "{lib}" effects {{}};

u64 main() {{
    vector<u8> v = el_str();
    print(v);
    return 0;
}}
"""

ELN_NULL = """extern el_null() -> vector<u8> from "{lib}" effects {{}};

u64 main() {{
    vector<u8> v = el_null();
    print(v);
    return 0;
}}
"""


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


def test_real_link(tmp):
    print("[5] 真编真链真跑：本地 C 库返回 `const char*` → 生成物 → 与 dsh run 逐字节一致")
    cxx_argv = dsh._cxx_compiler_argv()
    gcc = _find_gcc(cxx_argv[0] if cxx_argv else None)
    if not cxx_argv or not gcc:
        print("  --   本机没有 g++ / gcc：端到端**这一项没有验证**（不是通过）")
        return
    cxx = cxx_argv[0]
    if os.path.basename(cxx).lower() in ("cl", "cl.exe"):
        print("  --   编译器是 cl：这条用例只写了 g++/clang++ 的参数，**没有验证**")
        return
    d = os.path.join(tmp, "link")
    os.makedirs(d, exist_ok=True)
    env = dict(ENV_BASE, TMP=d, TEMP=d, ELENCH_CXX=cxx)
    env["PATH"] = os.path.dirname(cxx) + os.pathsep + env.get("PATH", "")
    with open(os.path.join(d, "shim.c"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(SHIM_C)
    lib = os.path.join(d, "cstrshim.dll" if os.name == "nt" else "cstrshim.so")
    # Windows：`-Wl,--export-all-symbols` 必须传成一个 argv（单引号在 shell 里才安全，
    # 这里直接走 argv 数组，不经过 shell）。
    share = [gcc, "-shared", "-O2", "-o", lib, os.path.join(d, "shim.c")]
    if os.name == "nt":
        share.append("-Wl,--export-all-symbols")
    else:
        share.append("-fPIC")
    r = subprocess.run(share, cwd=d, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    check("C 库编成动态库（解释器侧要用它）", r.returncode == 0, (r.stderr or "")[-300:])
    obj = os.path.join(d, "shim.o")
    r = subprocess.run([gcc, "-c", "-O2", os.path.join(d, "shim.c"), "-o", obj],
                       cwd=d, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    check("C 库编成 .o（生成物链接用）", r.returncode == 0, (r.stderr or "")[-300:])

    lib_uri = lib.replace("\\", "/")

    def build(name, src_text):
        eln = os.path.join(d, name + ".eln")
        with open(eln, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src_text.format(lib=lib_uri))
        cpp = os.path.join(d, name + ".cpp")
        g = subprocess.run([sys.executable, "-B", os.path.join(HERE, "dsh.py"),
                            "cpp", eln, "--output=" + cpp],
                           cwd=d, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        exe = os.path.join(d, name + (".exe" if os.name == "nt" else ".out"))
        cmd = [cxx, "-std=c++17"]
        if os.name == "nt":
            cmd.append("-static")
        cmd += [cpp, obj, "-o", exe]
        lk = subprocess.run(cmd, cwd=d, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env)
        return eln, cpp, exe, g, lk

    eln, cpp, exe, g, lk = build("ok", ELN_LINK)
    check("`dsh cpp` rc=0（自检也过）", g.returncode == 0, both(g)[-400:])
    text = open(cpp, encoding="utf-8").read() if os.path.exists(cpp) else ""
    check("生成物里是 `elench_cstr(el_str(), \"el_str\")`",
          'elench_cstr(el_str(), "el_str")' in text, text[-400:])
    check("生成物 + C 库链接成功", lk.returncode == 0, (lk.stderr or "")[-400:])
    if lk.returncode == 0:
        got = subprocess.run([exe], cwd=d, capture_output=True, env=env)
        # Windows 的 stdout 是**文本模式**：`\n` 被 CRT 翻成 `\r\n`，解释器那侧
        # 的 Python stdout 也一样（所以两边对拍一致）。先归一 EOL 再比内容。
        check("跑出来的字节 == C 库真串",
              (got.stdout or b"").replace(b"\r\n", b"\n") == b"hello from C\n",
              repr(got.stdout) + repr(got.stderr[-200:]))
        runr = subprocess.run([sys.executable, "-B", os.path.join(HERE, "dsh.py"),
                               "run", eln],
                              cwd=d, capture_output=True, env=env)
        interp = strip_return_line(runr.stdout or b"")
        check("解释器侧也跑得起来（真 dll）", runr.returncode == 0,
              (runr.stderr or b"")[-400:].decode("utf-8", "replace"))
        check("**与 `dsh run` 逐字节相同**（剥掉 main 返回值那一行）",
              got.stdout == interp,
              f"g++={got.stdout!r} interp={interp!r}")

    # ---- NULL：明确失败，不许静默返回空 vector
    eln_n, cpp_n, exe_n, g_n, lk_n = build("null", ELN_NULL)
    check("NULL 版的生成物也编得过（NULL 是**运行期**的事）",
          g_n.returncode == 0 and lk_n.returncode == 0,
          (both(g_n) + (lk_n.stderr or ""))[-400:])
    if lk_n.returncode == 0:
        got_n = subprocess.run([exe_n], cwd=d, capture_output=True, env=env)
        err = (got_n.stderr or b"").decode("utf-8", "replace")
        check("生成物：**非零退出**（不是安静成功）", got_n.returncode != 0,
              str(got_n.returncode))
        check("生成物：stdout 里没有空 vector 的影子（不许把 NULL 当 `[]`）",
              got_n.stdout == b"", repr(got_n.stdout))
        check("生成物：stderr 点出是哪个外部函数、且说清是空指针",
              "el_null" in err and "空指针" in err, err[-300:])
        check("生成物：错误里教了出路（声明成 `u64` 判空）", "u64" in err, err[-300:])
        run_n = subprocess.run([sys.executable, "-B", os.path.join(HERE, "dsh.py"),
                                "run", eln_n],
                               cwd=d, capture_output=True, env=env)
        ierr = (run_n.stderr or b"").decode("utf-8", "replace")
        check("解释器侧：同样明确失败（`E-FFI-Unsupported`，不返回 None/空串）",
              run_n.returncode != 0 and "E-FFI-Unsupported" in ierr
              and "空指针" in ierr, ierr[-300:])


def main() -> int:
    tmp = os.path.join(ROOT, "build", "cstr_return_test")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        test_runtime_helper()
        test_call_site_wrap()
        test_same_table()
        test_dsh_cpp_selfcheck(tmp)
        test_real_link(tmp)
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
