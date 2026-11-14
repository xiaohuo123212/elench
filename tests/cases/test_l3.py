#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_l3.py —— L3：文本与数字（v0.71）

## 这一层的特点

- **纯函数**（没有 `effects`），成本全部可静态定界；
- 文本就是 `vector<u8>`；**UTF-8 只在"字符数"时才解释**；
- 失败是值：`parse_u64` 返回 `Err(Parse::BadNumber | Overflow)`（内置枚举）。
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


from cost_interp import CostInterp      # noqa: E402
from interp import Interp                # noqa: E402
from parser import parse_source          # noqa: E402

FAILED = []
T = "target { time: { max: O(a.length()) }, allocation: { max: 0 }, };\n"
TU = f"""error Parse;
spec f(const vector<u8>& a, const vector<u8>& b) -> u64 {{ effects {{}}; {T} }}
"""


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(body, a, b=None):
    tu = parse_source(TU, "m.tu").module
    ch = parse_source("u64 f(const vector<u8>& a, const vector<u8>& b) {\n" + body + "\n}\n",
                      "m.eln").module
    return Interp(ch, tu).call("f", [list(a), list(b if b is not None else b"")])


def variant(r):
    return getattr(getattr(r, "e", None), "variant", None)


def main() -> int:
    print("=== parse_u64 ===")
    r = run("    return parse_u64(a)?;", b"123")
    check("123 → 123", getattr(r, "v", r) == 123, repr(r))
    check("空串 → Err(Parse::BadNumber)",
          variant(run("    return parse_u64(a)?;", b"")) == "BadNumber")
    check("非数字 → Err(Parse::BadNumber)",
          variant(run("    return parse_u64(a)?;", b"12x")) == "BadNumber")
    check("溢出 → Err(Parse::Overflow)",
          variant(run("    return parse_u64(a)?;", b"9" * 25)) == "Overflow")

    print("=== parse_hex ===")
    r2 = run("    return parse_hex(a)?;", b"0x1f")
    check("0x1f → 31", getattr(r2, "v", r2) == 31, repr(r2))
    check("非法十六进制 → Err(Parse::BadNumber)",
          variant(run("    return parse_hex(a)?;", b"zz")) == "BadNumber")

    print("=== format_u64 ===")
    check("123 → 3 个字节",
          run("    return format_u64(123).length();", b"") == 3)

    print("=== UTF-8 ===")
    check("utf8_len(中文) = 2（字节是 6）",
          run("    return utf8_len(a);", "中文".encode()) == 2)

    print("=== 文本操作 ===")
    check("text_find 命中",
          run("    return text_find(a, b);", b"hello", b"ll") == 2)
    check("text_find 未命中 → -1",
          run("    return text_find(a, b);", b"hello", b"zz") == -1)
    check("text_trim 去两端空白",
          run("    return text_trim(a).length();", b"  ab  ") == 2)
    check("text_to_lower 只做 ASCII",
          run("    return text_to_lower(a)[0];", b"AZ") == ord("a"))

    print("=== 切分 / 拼接 / 替换 ===")
    check("text_split 段数", run("    Split<vector<u8>> sp = text_split(a, b);\n    return split_len(sp);", b"a,b,c", b",") == 3)
    check("split_get 取第一段", run("    Split<vector<u8>> sp = text_split(a, b);\n    return split_get(sp, 0).length();", b"a,b,c", b",") == 1)
    check("text_join 拼回去", run("    Split<vector<u8>> sp = text_split(a, b);\n    return text_join(sp, b).length();", b"a,b,c", b",") == 5)
    check("text_replace 全部替换",
          run("    return text_replace(a, b, \"X\").length();", b"a,b,c", b",") == 5)

    print("=== UTF-8 码点 ===")
    check("utf8_encode(20013) 是 3 字节", run("    return utf8_encode(20013).length();", b"") == 3)
    check("utf8_codepoint_at 解出 20013",
          run("    return utf8_codepoint_at(a, 0);", "中".encode()) == 20013)

    print("=== base64 ===")
    check("base64_encode 中文a（7 字节 → 12 字符）",
          run("    return base64_encode(a).length();", "中文a".encode()) == 12)
    check("base64 往返还原",
          run("    const vector<u8> e = base64_encode(a);\n    const vector<u8> d = base64_decode(e)?;\n    return d.length();",
              "中文a".encode()) == 7)

    print("=== JSON 输出（只输出，不解析）===")
    check("json_emit_str 带引号并转义",
          run("    return json_emit_str(a).length();", b'a"b') == 6, "")
    check("json_emit_u64 是数字", run("    return json_emit_u64(42).length();", b"") == 2)
    check("text_concat 拼接",
          run("    return text_concat(a, b).length();", b"ab", b"cd") == 4)

    print("=== 按字符遍历（B）===")
    check("utf8_char_at 取第 0 个字符", run("    return utf8_char_at(a, 0);", "中文".encode()) == 20013)
    check("utf8_char_at 取第 1 个字符", run("    return utf8_char_at(a, 1);", "中文".encode()) == 25991)
    check("utf8_byte_of_char 第 1 个字符在字节 3",
          run("    return utf8_byte_of_char(a, 1);", "中文".encode()) == 3)

    print("=== 成本 ===")
    tu = parse_source(TU, "m.tu").module
    ch = parse_source("""u64 f(const vector<u8>& a, const vector<u8>& b) {
    return text_find(a, b);
}
""", "m.eln").module
    k = CostInterp(ch, tu).cost_of("f").render()
    check("text_find 的成本含两个规模（n(a) × n(b)）",
          "n(a)" in k and "n(b)" in k, k)

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
