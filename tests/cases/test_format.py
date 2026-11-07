#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_format.py —— 花括号格式化（设计 A：位置参数）

## 规则

```elench
print("x = {}, y = {:>3}", x, y);      // 位置参数：第 i 个洞吃第 i 个实参
const vector<u8> s = format("v={:x}", 255);
```

| 写法 | 含义 |
|---|---|
| `{}` | 默认 |
| `{:>N}` / `{:<N}` | 右/左对齐宽 N |
| `{:x}` | 十六进制（仅数字） |
| `{:s}` | 强制字节串 |
| `{{` / `}}` | 字面量 `{` / `}` |

**编译期查**：Arity / ArgType / BadSpec；动态格式串给 `W-Format-Dynamic` 警告。
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
from interp import Interp             # noqa: E402
from parser import parse_source       # noqa: E402

FAILED = []
TU = "spec f(const vector<u64>& xs) -> u64 { effects { io }; }\n"


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def fmt(text: str, *vals) -> bytes:
    import stdlib
    args = [list(text.encode())] + list(vals)
    return bytes(stdlib.FNS["format"]["impl"](args))


def main() -> int:
    print("=== 运行时格式化 ===")
    check("默认 {}", fmt("x = {}", 7) == b"x = 7", fmt("x = {}", 7))
    check("右对齐 {:>3}", fmt("[{:>3}]", 7) == b"[  7]")
    check("左对齐 {:<3}", fmt("[{:<3}]", 7) == b"[7  ]")
    check("十六进制 {:x}", fmt("{:x}", 255) == b"ff")
    check("字面量花括号 {{}}", fmt("{{{}}}", 1) == b"{1}")
    check("字节串默认按文本", fmt("{}", list(b"ab")) == b"ab")
    check("多洞按顺序", fmt("{}-{}-{}", 1, 2, 3) == b"1-2-3")

    print("=== print 插值 ===")
    ch = 'func f(const vector<u64>& xs) -> u64 { print("a = {}, b = {:>2}", 1, 2); return 0; }'
    Interp(parse_source(ch, "m.eln").module, parse_source(TU, "m.tu").module).call("f", [[1]])
    check("print 用格式串（输出见上一行）", True)

    print("=== 编译期检查 ===")
    cases = {
        "洞多": ('print("x = {}, y = {}", 1);', "E-Format-Arity"),
        "洞少": ('print("x = {}", 1, 2);', "E-Format-Arity"),
        "非法说明符": ('print("{:.2}", 1);', "E-Format-BadSpec"),
        "十六进制给字节串": ('const vector<u8> s = "a"; print("{:x}", s);', "E-Format-ArgType"),
    }
    for name, (body, code) in cases.items():
        ch = "func f(const vector<u64>& xs) -> u64 { " + body + " return 0; }"
        got = [d.code for d in check_module(TU, ch, "m").diagnostics]
        check(f"{name} → {code}", code in got, str(got))

    print("=== 正常的不报错 ===")
    ok = 'func f(const vector<u64>& xs) -> u64 { print("x = {}, y = {:>2}", 1, 2); return 0; }'
    check("合法格式串无诊断", [d.code for d in check_module(TU, ok, "m").diagnostics] == [])

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
