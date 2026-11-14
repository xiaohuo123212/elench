#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_include_diag.py —— `#include` **不再静默丢弃**（2026-11-13）

## 为什么这条测试必须存在

以前 `src/preproc.py` 对 `#include` 的规则是"整行丢弃、零诊断"。于是：

    #include "nonexistent_third_party.h"
    #include <nlohmann/json.hpp>
    u64 f() { return 1; }

`check` 打印 **✓ 通过**——用户以为"我的库接进来了、check 过了"，
而 `check` **从头到尾没看过库的任何一行**。报错至少你知道有问题，
这种"看起来通过"会让后面所有结论（κ、契约、对拍）**全部失效**。

现在的判据（见 `preproc.py` 模块文档）：

| `#include` | 处理 |
|---|---|
| 标准库头（`preproc.STANDARD_HEADERS`） | **静默丢弃**（本语言有内建类型与门面头，不影响语义） |
| 别的头（第三方 / 项目内 / 平台） | `E-Lex-IncludeUnresolved`（说清：这行被忽略、符号不可用、要用就翻译成 `.tu`） |
| `#ifdef` 死分支里的 | 不报（与 `#define` 一致） |

## 钉住什么

1. 标准头 **零诊断**（现状不能回退）；
2. 非标准头 **必须报**，且消息含三件事：被忽略 / 符号不可用 / `.tu`；
3. 白名单是**数据**（`frozenset`）且覆盖语料、探针库、测试里实际出现的头
   —— 漏一个，语料就会变红；
4. **不级联**：`parse_source` 之后只有这一条诊断；
5. 端到端 `check`：非标准头退出码非 0，标准头仍是 0。

跑法：`python -B tests/cases/test_include_diag.py`
"""
import os
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import preproc                                              # noqa: E402
from parser import parse_source                             # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                           # noqa: BLE001
    pass

FAILED = []


def check(ok, name, detail=""):
    if ok:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def codes_of(src: str):
    return [(d.code, d.line) for d in preproc.preprocess(src, "t.eln").diagnostics]


#: 语料 / 探针库 / 测试里**实际出现过**的标准头（2026-11-13 清点）。
#: 白名单漏一个 → 这些模块会变红，所以单独钉一次。
REQUIRED_HEADERS = [
    "algorithm", "array", "cctype", "cmath", "cstddef", "cstdint", "cstdio",
    "cstdlib", "filesystem", "fstream", "iostream", "map", "memory_resource",
    "regex", "sstream", "string", "unordered_map", "utility", "vector",
]

#: 非标准头样本：真实第三方 / 项目内 / 平台 SDK。(写法, 实参)
NONSTANDARD = [
    ('#include "nonexistent_third_party.h"\n', "nonexistent_third_party.h"),
    ("#include <nlohmann/json.hpp>\n", "nlohmann/json.hpp"),
    ("#include <fmt/core.h>\n", "fmt/core.h"),
    ("#include <doctest/doctest.h>\n", "doctest/doctest.h"),
    ('#include "my_project/internal.h"\n', "my_project/internal.h"),
    ("#include <windows.h>\n", "windows.h"),
]


def main() -> int:
    print("[1] 白名单是数据，且覆盖语料实际用到的头")
    check(isinstance(preproc.STANDARD_HEADERS, frozenset),
          "`STANDARD_HEADERS` 是 frozenset（数据，不是一串 if）",
          type(preproc.STANDARD_HEADERS).__name__)
    check(len(preproc.STANDARD_HEADERS) >= 100,
          "白名单够宽（标准头全集，≥100 项）", str(len(preproc.STANDARD_HEADERS)))
    missing = [h for h in REQUIRED_HEADERS if h not in preproc.STANDARD_HEADERS]
    check(not missing, f"语料/探针/测试用到的 {len(REQUIRED_HEADERS)} 个标准头都在白名单里",
          str(missing))
    leaked = [h for _src, h in NONSTANDARD if h in preproc.STANDARD_HEADERS]
    check(not leaked, "非标准头没有混进白名单", str(leaked))

    print("[2] 标准头：零诊断（现状，不许回退）")
    for h in ("vector", "cstdio", "map", "string", "unordered_map", "cstdint",
              "algorithm", "array", "memory_resource"):
        got = codes_of(f"#include <{h}>\n")
        check(got == [], f"`#include <{h}>` 零诊断", str(got))
    check(codes_of('#include "vector"\n') == [],
          "引号形式的**标准头**（`\"vector\"`）也按名字判、零诊断",
          str(codes_of('#include "vector"\n')))

    print("[3] 非标准头：必须报 `E-Lex-IncludeUnresolved`，且消息说清三件事")
    for src, header in NONSTANDARD:
        got = codes_of(src)
        check(got == [("E-Lex-IncludeUnresolved", 1)],
              f"`{header}` ⇒ E-Lex-IncludeUnresolved@1", str(got))

    for label, src in (
        ('引号形式 `"nonexistent_third_party.h"`',
         '#include "nonexistent_third_party.h"\n'),
        ("尖括号形式 `<nlohmann/json.hpp>`",
         "#include <nlohmann/json.hpp>\n"),
    ):
        ds = preproc.preprocess(src, "t.eln").diagnostics
        msg = ds[0].message if ds else ""
        check(ds and ds[0].code == "E-Lex-IncludeUnresolved", f"{label} 有专属诊断码",
              str([d.code for d in ds]))
        check("整行忽略" in msg, f"{label} 消息说「这一行被忽略」", msg[:120])
        check("一个都不可用" in msg, f"{label} 消息说「库的符号不可用」", msg[:120])
        check(".tu" in msg and "extern" in msg and "link" in msg,
              f"{label} 消息给了 `.tu` 改法", msg[:160])

    print("[4] 死分支 / pragma / 语法错误")
    check(codes_of("#ifdef _WIN32\n#include <windows.h>\n#endif\n") == [],
          "`#ifdef _WIN32` 死分支里的 `<windows.h>` 不报（与 `#define` 一致）",
          str(codes_of("#ifdef _WIN32\n#include <windows.h>\n#endif\n")))
    check(codes_of("#define HAVE_X 1\n#if HAVE_X\n#include <fmt/core.h>\n#endif\n")
          == [("E-Lex-IncludeUnresolved", 3)],
          "活分支里的第三方头仍然报", "报错位置不对或漏报")
    check(codes_of("#pragma once\n") == [], "`#pragma once` 仍静默丢弃")
    check(codes_of("#include vector\n") == [("E-Lex-IncludeUnresolved", 1)],
          "`#include vector`（缺括号）报错，不再静默吞")

    print("[5] 不级联：`parse_source` 之后仍只有这一条诊断")
    src = '#include "nonexistent_third_party.h"\nu64 f() { return 1; }\n'
    r = parse_source(src, "t.eln")
    errs = [(d.code, d.line) for d in r.diagnostics if d.code.startswith("E-")]
    check(errs == [("E-Lex-IncludeUnresolved", 1)],
          "只剩 1 条错误（没有解析级联）", str(errs))
    fns = [c for c in (r.module.children if r.module else [])
           if c.kind == "fn" and c.name == "f"]
    check(len(fns) == 1, "被忽略的行不影响后面的函数解析", str([c.name for c in
                                                              (r.module.children if r.module else [])]))

    print("[6] 端到端 `check`：第三方头非 0 退出、标准头仍是 0")
    tmp = tempfile.mkdtemp()
    try:
        third = os.path.join(tmp, "third.eln")
        std = os.path.join(tmp, "std.eln")
        with open(third, "w", encoding="utf-8", newline="\n") as fh:
            fh.write('#include "nonexistent_third_party.h"\nu64 f() { return 1; }\n')
        with open(std, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("#include <vector>\nu64 f() { return 1; }\n")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        dsh = os.path.join(SRC, "dsh.py")

        def run(path):
            return subprocess.run([sys.executable, "-B", dsh, "check", path],
                                  cwd=ROOT, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=env)

        r3 = run(third)
        check(r3.returncode != 0, "第三方头：check 退出码非 0", str(r3.returncode))
        check("E-Lex-IncludeUnresolved" in r3.stdout,
              "第三方头：check 输出里有 E-Lex-IncludeUnresolved", r3.stdout[-300:])
        rs = run(std)
        check(rs.returncode == 0, "标准头：check 仍通过（退出码 0）",
              rs.stdout[-300:] + rs.stderr[-200:])
        check("E-Lex-IncludeUnresolved" not in rs.stdout,
              "标准头：输出里没有这个错误码", rs.stdout[-200:])
    finally:
        for name in ("third.eln", "std.eln"):
            p = os.path.join(tmp, name)
            if os.path.exists(p):
                os.remove(p)
        os.rmdir(tmp)

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项")
        for f in FAILED:
            print("  -", f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
