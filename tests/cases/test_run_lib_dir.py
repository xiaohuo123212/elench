#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_run_lib_dir.py —— `dsh run --lib-dir=`：**裸名外部库**的解析顺序 / 透传 / 负例

## 这一批修的是什么（2026-11-14）

同一个 `.eln`，两条命令两种结论：

```
extern cJSON_Version() -> vector<u8> from "cjson" effects {};

dsh cpp v.eln --run --link=…/cJSON.o   ✓ 生成物能链接、能跑（"cjson" 只是注释）
dsh run v.eln                          ✗ 加载外部库失败（cjson）：
                                          Could not find module 'cjson'
```

根因不是"库没编"，是**名字怎么解析**：Python 3.8+ 的 `ctypes.CDLL` 对裸名
**既不搜 cwd 也不搜 PATH**（Windows 走受限的 `LoadLibraryEx` 口径），所以写裸名
永远加载不了；同一个 `.eln` 换成绝对路径就能跑。于是 `dsh cpp --run` 能编译、
解释器侧却只能报「无法对拍」。

## 这里钉住什么

1. **搜索顺序**（纯函数 `ffi_c.library_candidates`，不需要编译器）：
   绝对/带路径的写法 → 每个 `--lib-dir` 下 `<名字><平台后缀>` → `<名字>`
   → 最后把裸名交给平台加载器。**不额外搜 cwd、不搜 PATH**。
2. **CLI 形状**：`--lib-dir=` 可重复；空串 / 不是目录当场 rc=2；usage 里有它。
3. **负例**：找不到库时消息**列出试过的每个路径**（按顺序），并区分
   「找不到该文件」与「文件在、加载失败」。
4. **透传**：`dsh cpp --run --lib-dir=…` 一条命令 rc=0 且**对拍一致**
   （不是"跳过"）；同一份 `.eln` **不给** `--lib-dir` 就必须 rc≠0 + 明说
   「无法对拍」——不许把跳过冒充成通过。
5. 本机有 gcc 时，再跑一遍**裸名 `cjson`** 的真全链路（本机没有
   `build/real-libs/cJSON` 就明确打印「未验证」，不冒充通过）。

跑法：`python tests/cases/test_run_lib_dir.py`（退出码 0 即通过）。
"""
import os
import shutil
import subprocess
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
#: 与 `test_cpp_run_link.py` 同一条约定：`HERE` 指 `src/`。
HERE = SRC
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import dsh                                                   # noqa: E402
import ffi_c                                                 # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                            # noqa: BLE001
    pass

FAILED = []
ENV_BASE = dict(os.environ, PYTHONIOENCODING="utf-8")

#: 系统库（平台加载器那条路）。`ffi_c` 的解析顺序最后一步必须保住它，
#: 否则 `test_ffi.py` 里 `strlen` 那批用例会全红。
SYS_LIB = "ucrtbase" if sys.platform == "win32" else "libc"


def check(label, cond, detail=""):
    if cond:
        print(f"  ok   {label}")
    else:
        FAILED.append(label)
        print(f"  FAIL {label}   {detail[-700:]}")


def run_dsh(args, env_extra=None, cwd=None):
    e = dict(ENV_BASE)
    e.update(env_extra or {})
    return subprocess.run([sys.executable, "-B", os.path.join(HERE, "dsh.py")]
                          + list(args), cwd=cwd or ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=e)


def both(r):
    return (r.stdout or "") + (r.stderr or "")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# ---------------------------------------------------------------- ① 搜索顺序
def test_candidates():
    print("[1] 搜索顺序（纯函数 `ffi_c.library_candidates`）")
    sfx = ffi_c.lib_suffixes()[0]
    check("平台后缀：" + " / ".join(ffi_c.lib_suffixes()),
          (".dll",) == ffi_c.lib_suffixes() if os.name == "nt" else True,
          str(ffi_c.lib_suffixes()))
    got = ffi_c.library_candidates("cjson", ["d1", "d2"])
    want = [os.path.join("d1", "cjson" + sfx), os.path.join("d1", "cjson"),
            os.path.join("d2", "cjson" + sfx), os.path.join("d2", "cjson"),
            "cjson"]
    check("裸名 + 两个目录：先后缀、再不补后缀，目录按给出顺序，最后平台加载器",
          got == want, f"got={got}")

    ab = os.path.abspath(os.path.join("libs", "cjson"))
    check("绝对路径：**原样**，不补后缀、不试 --lib-dir",
          ffi_c.library_candidates(ab, ["d1"]) == [ab],
          str(ffi_c.library_candidates(ab, ["d1"])))
    rel = "libs/sub/cjson"
    check("带分隔符的相对路径：原样（相对进程 cwd，不猜别的目录）",
          ffi_c.library_candidates(rel, ["d1"]) == [rel],
          str(ffi_c.library_candidates(rel, ["d1"])))
    check("名字已经带后缀：目录下不重复加 `.dll.dll`",
          ffi_c.library_candidates("cjson" + sfx, ["d1"])
          == [os.path.join("d1", "cjson" + sfx), "cjson" + sfx],
          str(ffi_c.library_candidates("cjson" + sfx, ["d1"])))
    check("没有 --lib-dir：只剩「原样交给平台加载器」（与加开关之前完全一样）",
          ffi_c.library_candidates("ucrtbase") == ["ucrtbase"],
          str(ffi_c.library_candidates("ucrtbase")))
    check("空名字 = 进程自身符号（`CDLL(None)`）：没有候选",
          ffi_c.library_candidates("", ["d1"]) == [],
          str(ffi_c.library_candidates("", ["d1"])))
    check("**不把 cwd / PATH 拼进候选**（候选表里只有名字/目录，没有环境路径）",
          all(p in ("cjson",) or p.startswith(("d1", "d2"))
              for p in ffi_c.library_candidates("cjson", ["d1", "d2"])),
          str(ffi_c.library_candidates("cjson", ["d1", "d2"])))


# ---------------------------------------------------------------- ② CLI 形状
SYS_ELN = f"""extern strlen(const vector<u8>& s) -> u64 from "{SYS_LIB}" effects {{}};

u64 main() {{
    return strlen("hello");
}}
"""

NOLIB_ELN = """extern elench_no_such_symbol() -> u64 from "elench_no_such_lib" effects {};

u64 main() {
    return elench_no_such_symbol();
}
"""


def test_cli(tmp):
    print("[2] CLI：`--lib-dir=` 可重复；空串 / 不是目录当场 rc=2")
    d = os.path.join(tmp, "cli")
    write(os.path.join(d, "syslib.eln"), SYS_ELN)
    write(os.path.join(d, "nolib.eln"), NOLIB_ELN)
    empty = os.path.join(d, "emptydir")
    os.makedirs(empty, exist_ok=True)

    r = run_dsh(["run", os.path.join(d, "syslib.eln"), "--lib-dir="])
    check("`--lib-dir=` 空串 ⇒ rc=2", r.returncode == 2 and "要一个目录" in both(r),
          f"rc={r.returncode} / {both(r)[-300:]}")

    nope = os.path.join(d, "nope_dir")
    r = run_dsh(["run", os.path.join(d, "syslib.eln"), "--lib-dir=" + nope])
    check("`--lib-dir=` 不是目录 ⇒ rc=2", r.returncode == 2 and "不是目录" in both(r),
          f"rc={r.returncode} / {both(r)[-300:]}")

    r = run_dsh(["run"])
    check("没有模块时 usage 里有 `--lib-dir=`",
          "--lib-dir=" in both(r), both(r)[-300:])

    ok = run_dsh(["run", os.path.join(d, "syslib.eln")])
    check("系统库（ucrtbase/libc）照旧能加载：不给 `--lib-dir` 也 rc=0",
          ok.returncode == 0, f"rc={ok.returncode} / {both(ok)[-300:]}")
    ok2 = run_dsh(["run", os.path.join(d, "syslib.eln"), "--lib-dir=" + empty])
    check("给了 `--lib-dir`（目录里没有同名库）也不影响系统库：rc=0 且输出 5",
          ok2.returncode == 0 and "5" in (ok2.stdout or ""),
          f"rc={ok2.returncode} / {both(ok2)[-300:]}")

    print("[2b] 负例：找不到库时**列出试过的每个路径**（按 --lib-dir 顺序）")
    first = os.path.join(d, "first")
    second = os.path.join(d, "second")
    os.makedirs(first, exist_ok=True)
    os.makedirs(second, exist_ok=True)
    r = run_dsh(["run", os.path.join(d, "nolib.eln"),
                 "--lib-dir=" + first,
                 "--lib-dir=" + second])
    msg = both(r)
    check("rc≠0（不是静默失败）", r.returncode != 0, msg[-300:])
    check("消息里有「按顺序试过」", "按顺序试过" in msg, msg[-500:])
    check("消息里有第一目录的候选", first in msg, msg[-600:])
    check("消息里有第二目录的候选", second in msg, msg[-600:])
    check("消息里有「找不到该文件」（区分不存在 vs 加载失败）",
          "找不到该文件" in msg, msg[-600:])
    check("消息里有最后一步（裸名交给平台加载器）",
          "平台加载器" in msg and "elench_no_such_lib" in msg, msg[-600:])
    check("消息里有修法（`--lib-dir=` / 绝对路径）",
          "--lib-dir=" in msg and "绝对路径" in msg, msg[-600:])


# ---------------------------------------------------------------- ③ 透传（真库）
SHIM_C = """#include <stdint.h>
uint64_t el_probe(void) { return 4242; }
"""

SHIM_ELN = """extern el_probe() -> u64 from "elprobe" effects {};

u64 main() {
    unsafe {
        print(el_probe());
    }
    return 0;
}
"""

CJSON_ELN = """extern cJSON_Version() -> vector<u8> from "cjson" effects {};

u64 main() {
    unsafe {
        print(cJSON_Version());
    }
    return 0;
}
"""


def _find_cxx():
    argv = dsh._cxx_compiler_argv()
    if argv:
        return argv
    for c in (r"C:\Users\haoll\Documents\shake\toolchain\mingw64\bin\g++.exe",):
        if os.path.isfile(c):
            return [c]
    return None


def _find_gcc(cxx):
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


def _build_dll(d, gcc, csrc, stem):
    """`csrc` → `.o` + 动态库（**同一目录**，裸名要能被 `--lib-dir` 找到）。"""
    os.makedirs(d, exist_ok=True)
    env = dict(ENV_BASE, TMP=d, TEMP=d)
    obj = os.path.join(d, stem + ".o")
    r = subprocess.run([gcc, "-c", "-O2", csrc, "-o", obj], cwd=d,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    check(f"{stem}.c → {stem}.o", r.returncode == 0, (r.stderr or "")[-400:])
    dll = os.path.join(d, stem + ffi_c.lib_suffixes()[0])
    share = [gcc, "-shared", "-O2", "-o", dll, csrc]
    share.append("-Wl,--export-all-symbols" if os.name == "nt" else "-fPIC")
    r = subprocess.run(share, cwd=d, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    check(f"{stem}.c → 动态库", r.returncode == 0, (r.stderr or "")[-400:])
    return obj, dll


def test_passthrough(tmp):
    print("[3] `dsh cpp --run --lib-dir=` 透传：裸名一条命令 rc=0 且**对拍一致**")
    cxx_argv = _find_cxx()
    gcc = _find_gcc(cxx_argv[0] if cxx_argv else None)
    if not cxx_argv or not gcc:
        print("  --   本机没有 g++ / gcc：端到端**未验证**（不是通过）")
        return
    if os.path.basename(cxx_argv[0]).lower() in ("cl", "cl.exe"):
        print("  --   编译器是 cl：本用例只写了 g++ 参数，**未验证**（不是通过）")
        return
    cxx = cxx_argv[0]
    d = os.path.join(tmp, "passthrough")
    libdir = os.path.join(d, "lib")
    write(os.path.join(d, "shim.c"), SHIM_C)
    env = dict(ENV_BASE, TMP=d, TEMP=d, ELENCH_CXX=cxx)
    env["PATH"] = os.path.dirname(cxx) + os.pathsep + env.get("PATH", "")
    obj, _dll = _build_dll(libdir, gcc, os.path.join(d, "shim.c"), "elprobe")

    eln = os.path.join(d, "probe.eln")
    write(eln, SHIM_ELN)
    out = os.path.join(d, "probe.cpp")
    r = run_dsh(["cpp", eln, "--output=" + out, "--run", "--link=" + obj,
                 "--lib-dir=" + libdir], env_extra=env, cwd=d)
    msg = both(r)
    check("`--lib-dir=<目录>` + 裸名 `elprobe`：一条命令 rc=0",
          r.returncode == 0, msg[-700:])
    check("**真的对拍过**（不是只编译、也不是跳过）", "对拍一致" in msg, msg[-500:])
    check("生成物 stdout 里有 4242（真调进了 C）",
          "生成物 stdout" in msg and "4242" in msg, msg[-500:])

    # 负对照：同一份 `.eln`、同一个 `.o`，**不给 `--lib-dir`** ⇒ 不能让对拍假绿。
    r = run_dsh(["cpp", eln, "--output=" + out, "--run", "--link=" + obj],
                env_extra=env, cwd=d)
    msg = both(r)
    check("不给 `--lib-dir` ⇒ rc≠0（裸名加载不了）", r.returncode != 0, msg[-700:])
    check("且明说「无法对拍」（不是冒充通过）", "无法对拍" in msg, msg[-600:])
    check("解释器侧消息里列出试过的路径",
          "按顺序试过" in msg or "Could not find module" in msg, msg[-600:])


def test_cjson_bare(tmp):
    print("[4] cJSON 1.7.19：**裸名** `cjson` + `--lib-dir` 全链路（本机有才跑）")
    real = os.path.join(ROOT, "build", "real-libs", "cJSON")
    if not (os.path.isfile(os.path.join(real, "cJSON.c"))
            and os.path.isfile(os.path.join(real, "cJSON.h"))):
        print("  --   build/real-libs/cJSON 不在（gitignored）：**未验证**，不冒充通过")
        return
    cxx_argv = _find_cxx()
    gcc = _find_gcc(cxx_argv[0] if cxx_argv else None)
    if not cxx_argv or not gcc:
        print("  --   本机没有 g++ / gcc：**未验证**（不是通过）")
        return
    if os.path.basename(cxx_argv[0]).lower() in ("cl", "cl.exe"):
        print("  --   编译器是 cl：**未验证**（不是通过）")
        return
    cxx = cxx_argv[0]
    d = os.path.join(tmp, "cjson")
    libdir = os.path.join(d, "lib")
    os.makedirs(libdir, exist_ok=True)
    env = dict(ENV_BASE, TMP=d, TEMP=d, ELENCH_CXX=cxx)
    env["PATH"] = os.path.dirname(cxx) + os.pathsep + env.get("PATH", "")
    obj, _dll = _build_dll(libdir, gcc, os.path.join(real, "cJSON.c"), "cjson")

    eln = os.path.join(d, "ver.eln")
    write(eln, CJSON_ELN)
    out = os.path.join(d, "ver.cpp")
    r = run_dsh(["cpp", eln, "--output=" + out, "--include=cJSON.h",
                 "--include-dir=" + real, "--run", "--link=" + obj,
                 "--lib-dir=" + libdir], env_extra=env, cwd=d)
    msg = both(r)
    check("一条命令 rc=0（编译 + 链接 + 跑 + 对拍）", r.returncode == 0, msg[-800:])
    check("输出 `1.7.19`（生成物真的调进了 cJSON）", "1.7.19" in msg, msg[-500:])
    check("与 `dsh run` **逐字节一致**（不是「对拍跳过」）",
          "对拍一致" in msg and "对拍跳过" not in msg, msg[-500:])


def main() -> int:
    tmp = os.path.join(ROOT, "build", "run_lib_dir_test")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        test_candidates()
        test_cli(tmp)
        test_passthrough(tmp)
        test_cjson_bare(tmp)
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
