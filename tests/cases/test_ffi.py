#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_ffi.py —— 接其他语言的接口（第一批，v0.71）

## 这一批做了什么

| 能力 | 状态 |
|---|---|
| **声明** C ABI 函数：`extern strlen(...) -> u64 from "ucrtbase";` | ✅ |
| **调用**（解释器经 ctypes 真正调进去） | ✅ |
| **成本**：默认 `ext(名字)`；`external { strlen: O(n) }` 登记后替换 | ✅ |
| **效果**：每个 `extern` MUST 声明 `effects { … }` | ✅ `E-Extern-NoEffects` |
| **被其他语言调用**（导出） | ❌ 还没做 |

## 为什么成本口径天然合适

FFI 正是 `external` 登记存在的原因：**别人的代码有多贵，我们只能签字**。
所以 FFI 的成本**默认就是 `ext(名字)`**——看得见"这一项是外部的"，
`external { … }` 一登记就变成可判定。
"""
import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但**这批测试原先住在 `src/`**，里面大量
#: `os.path.join(HERE, "..", "文档")` / `os.path.join(ROOT, "tools")` 都假设
#: 「本文件在 src/、上一级是仓库根」。搬家时改 200 处路径比让 `HERE` 继续指 src 贵得多，
#: 所以保留旧语义：`HERE` = src/，`CASEDIR` = 本文件真正所在的目录。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import sys


from checker import check_module      # noqa: E402
from cost_interp import CostInterp    # noqa: E402
from interp import Interp             # noqa: E402
from parser import parse_source       # noqa: E402

FAILED = []
LIB = "ucrtbase" if sys.platform == "win32" else "libc"

TU = """spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""


def ch(effects="effects {}", extra_effects=""):
    return f'''extern strlen(const vector<u8>& s) -> u64 from "{LIB}" {effects};

func f(const vector<u64>& xs) -> u64 {{
    return strlen("hello"){extra_effects};
}}
'''


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def main() -> int:
    print(f"=== 调用 C 函数（库：{LIB}） ===")
    tu = parse_source(TU, "m.tu").module
    eln = parse_source(ch(), "m.eln").module
    check('strlen("hello") == 5', Interp(eln, tu).call("f", [[1]]) == 5,
          str(Interp(eln, tu).call("f", [[1]])))

    print("=== 写法二：extern 写成 spec（推荐） ===")
    tu_ext = f"""spec strlen(const vector<u8>& s) -> u64 {{
    external "{LIB}";
    effects {{}};
    target {{ time: {{ max: O(1) }}, allocation: {{ max: 0 }}, }};
}}
spec f(const vector<u64>& xs) -> u64 {{
    effects {{}};
    target {{ time: {{ max: O(xs.length()) }}, allocation: {{ max: 0 }}, }};
}}
"""
    r2 = parse_source(tu_ext, "m.tu")
    check("spec 里的 external \"lib\" 能解析", [d.code for d in r2.diagnostics] == [],
          str([d.code for d in r2.diagnostics][:2]))
    ch2 = """func f(const vector<u64>& xs) -> u64 {
    return strlen("hello");
}
"""
    got = Interp(parse_source(ch2, "m.eln").module, r2.module).call("f", [[1]])
    check("spec 形式能真调进 C（5）", got == 5, str(got))
    k2 = CostInterp(parse_source(ch2, "m.eln").module, r2.module).cost_of("f").render()
    check("spec 形式的成本仍是 ext(strlen)", "ext(strlen)" in k2, k2)
    check("spec 形式的 check 通过",
          [d.code for d in check_module(tu_ext, ch2, "m").diagnostics] == [])

    print("=== 写法三：module link + 无体 spec（推荐） ===")
    tu_link = f"""link "{LIB}";

spec strlen(const vector<u8>& s) -> u64 {{
    effects {{}};
    target {{ time: {{ max: O(1) }}, allocation: {{ max: 0 }}, }};
}}
spec f(const vector<u64>& xs) -> u64 {{
    effects {{}};
    target {{ time: {{ max: O(xs.length()) }}, allocation: {{ max: 0 }}, }};
}}
"""
    r3 = parse_source(tu_link, "m.tu")
    check("link \"lib\" 能解析", [d.code for d in r3.diagnostics] == [],
          str([d.code for d in r3.diagnostics][:2]))
    ch3 = 'func f(const vector<u64>& xs) -> u64 { return strlen("hello"); }\n'
    got3 = Interp(parse_source(ch3, "m.eln").module, r3.module).call("f", [[1]])
    check("无体 spec 自动当外部函数（5）", got3 == 5, str(got3))

    print("=== 成本：默认 ext，登记后替换 ===")
    k = CostInterp(eln, tu).cost_of("f").render()
    check("没登记 → ext(strlen)", "ext(strlen)" in k, k)

    tu2 = "external { strlen: O(n); }\n" + TU
    k2 = CostInterp(eln, parse_source(tu2, "m.tu").module).cost_of("f").render()
    check("登记 O(n) → 替换掉 ext", "ext(strlen)" not in k2 and "n" in k2, k2)

    print("=== 效果必须声明 ===")
    codes = [d.code for d in check_module(TU, ch(""), "m").diagnostics]
    check("extern 不写 effects → E-Extern-NoEffects", "E-Extern-NoEffects" in codes, str(codes))

    codes = [d.code for d in check_module(TU, ch("effects { io }"), "m").diagnostics]
    check("extern 声明了 io，调用方没声明 → E-Effect-Missing",
          "E-Effect-Missing" in codes, str(codes))

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
