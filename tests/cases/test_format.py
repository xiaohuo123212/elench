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
| `{}` | 默认（**与 `print(x)` 同一结果**：整数十进制、浮点不截断、bool 打 `True`/`False`） |
| `{:d}` / `{:u}` | 十进制整数 |
| `{:x}` / `{:X}` | 十六进制（**只收非负整数**） |
| `{:f}` / `{:.Nf}` | 定点小数（N=0..100，默认 6 位） |
| `{:s}` | 文本 |
| `{:>N}` / `{:<N}` | 右/左对齐宽 N |
| `{:0N}` | **零填充**宽 N（`{:08x}` → `000000ff`，不是空格） |
| `{{` / `}}` | 字面量 `{` / `}` |

**编译期查**：Arity / ArgType / BadSpec；动态格式串给 `W-Format-Dynamic` 警告。
**不支持的 spec 必须报错并列出支持集**——静默降级成"另一种填充 / 进制 / 精度"
比报错糟得多（2026-11-10：`{}` 把 `f64` 截成整数、`{:08x}` 静默补空格、
`{:.6f}` 报「本语言还没有浮点类型」——三条都是「check 绿、run 绿、结果错」）。
"""
import contextlib
import ast
import io as _io
import os as _os
import re as _re
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


import fmt as _fmtmod             # 同一份解析器：断言错误文案里带支持集
from checker import check_module      # noqa: E402
from interp import EnumVal, Interp, InterpError, Ok   # noqa: E402
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


def fmt_err(text: str, *vals):
    """跑一次格式化，返回它抛的语言级错误（`InterpError` 的文本）；没抛返回 `None`。

    这里**必须**拿到 `InterpError`：`stdlib._format` 的错误路径以前漏了
    `from interp import InterpError`，于是运行期格式错误变成宿主
    `NameError`（见 `test_runtime_errors_are_language_errors`）。
    """
    try:
        fmt(text, *vals)
        return None
    except InterpError as exc:
        return str(exc)


def run_lines(ch: str, tu: str = TU) -> list:
    """跑一个 `.eln` 片段，返回它 `print` 出来的行（截获 stdout）。"""
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        Interp(parse_source(ch, "m.eln").module,
               parse_source(tu, "m.tu").module).call("f", [[1]])
    return buf.getvalue().splitlines()


#: 内建函数文档里「花括号格式串」那张表的一行：`| 写法 | 收的值 | `format(…)` | `输出` |`
#: 第一列可能含多个代码片段（`{:d}` / `{:u}`）或前缀词（宽度 `{:8}`），所以不锚第一列。
_DOC_ROW = _re.compile(r"^\|.*\| `format\((.+)\)` \| `(.*)` \|$")


def test_doc_spec_table() -> None:
    """文档的每一行 `format(…)` → 输出，必须**真的**跑出同样的结果。

    这张表是 `tools/gen_builtins_doc.py` 里手写的常量（`FORMAT_SPECS`），
    生成器 --check 只保证"文档 == 生成结果"，**两边一起错它看不出来**。
    所以这里直接对着实现跑：参数是 Python 字面量，文档写什么就喂什么。
    """
    path = _os.path.join(ROOT, "文档", "内建函数.md")
    rows, bad = 0, []
    for line in _io.open(path, encoding="utf-8").read().split("\n"):
        m = _DOC_ROW.match(line)
        if not m:
            continue
        call = "format(" + m.group(1) + ")"
        try:
            node = ast.parse(call, mode="eval").body
            vals = [ast.literal_eval(a) for a in node.args]
        except (SyntaxError, ValueError) as exc:
            bad.append(f"{call} 里的字面量解析不了：{exc}")
            continue
        text, rest = vals[0], vals[1:]
        try:
            got = fmt(text, *rest).decode("utf-8")
        except InterpError as exc:
            bad.append(f"{call} → 报错 {exc}")
            continue
        rows += 1
        if got != m.group(2):
            bad.append(f"{call} → {got!r}，文档写 {m.group(2)!r}")
    check(f"文档 spec 表 {rows} 行与实现一致", not bad, "; ".join(bad[:4]))
    check("文档 spec 表真的被读到了（≥9 行）", rows >= 9, str(rows))


def main() -> int:
    print("=== 运行时格式化 ===")
    check("默认 {}", fmt("x = {}", 7) == b"x = 7", fmt("x = {}", 7))
    check("右对齐 {:>3}", fmt("[{:>3}]", 7) == b"[  7]")
    check("左对齐 {:<3}", fmt("[{:<3}]", 7) == b"[7  ]")
    check("十六进制 {:x}", fmt("{:x}", 255) == b"ff")
    check("字面量花括号 {{}}", fmt("{{{}}}", 1) == b"{1}")
    check("字节串默认按文本", fmt("{}", list(b"ab")) == b"ab")
    check("多洞按顺序", fmt("{}-{}-{}", 1, 2, 3) == b"1-2-3")

    print("=== 浮点：`{}` 不许截断成整数（2026-11-10 修） ===")
    check("{} 打浮点 = 最短往返", fmt("{}", 3.14159265) == b"3.14159265", fmt("{}", 3.14159265))
    check("混在文本里同样不截断", fmt("x = {}", 3.14159265) == b"x = 3.14159265")
    check("{:>10} 打浮点是补空格，不是取整", fmt("[{:>10}]", 3.5) == b"[       3.5]",
          fmt("[{:>10}]", 3.5))
    check("{:f} 默认 6 位小数", fmt("[{:f}]", 3.14159265) == b"[3.141593]")
    check("{:.6f}", fmt("{:.6f}", 3.14159265) == b"3.141593", fmt("{:.6f}", 3.14159265))
    check("{:.2f}", fmt("{:.2f}", 3.14159265) == b"3.14")
    check("{:.0f}（不写小数点后）", fmt("{:.0f}", 3.14159265) == b"3")
    check("{:10.2f} 宽度 + 精度", fmt("[{:10.2f}]", 3.14159265) == b"[      3.14]")
    check("{:010.2f} 零填充 + 精度", fmt("[{:010.2f}]", 3.14159) == b"[0000003.14]")
    check("{:08.2f} 负数的 0 在符号后", fmt("[{:08.2f}]", -3.14159) == b"[-0003.14]")
    check("{:.2f} 整值浮点保留两位", fmt("{:.2f}", 2.0) == b"2.00")

    print("=== `0` 是零填充标志，不是宽度的前导字符（2026-11-10 修） ===")
    check("{:08x} → 000000ff", fmt("[{:08x}]", 255) == b"[000000ff]", fmt("[{:08x}]", 255))
    check("{:8x} → 空格填充", fmt("[{:8x}]", 255) == b"[      ff]")
    check("{:08} → 十进制零填充", fmt("[{:08}]", 42) == b"[00000042]")
    check("{:08d} → 十进制零填充（`d` 是显式形态）", fmt("[{:08d}]", 42) == b"[00000042]")
    check("{:08} 负数：0 补在符号后", fmt("[{:08}]", -42) == b"[-0000042]")
    check("{:X} 大写十六进制", fmt("{:X}", 255) == b"FF")
    check("{:d} / {:u} 十进制", fmt("{:d}|{:u}", 42, 42) == b"42|42")
    check("{:d} 收负值（有符号十进制）", fmt("{:d}", -42) == b"-42")
    check("{:u} 收负值 → E-Format-ArgType（以前会打 `-42`）",
          "E-Format-ArgType" in (fmt_err("{:u}", -42) or ""), fmt_err("{:u}", -42))
    check("u64 全 1 的十六进制", fmt("{:x}", 18446744073709551615) == b"ffffffffffffffff")
    check("{:<8} 左对齐（数字）", fmt("[{:<8}]", 42) == b"[42      ]")
    check("{:>8} 右对齐（数字）", fmt("[{:>8}]", 42) == b"[      42]")
    check("{:<8} 左对齐（文本）", fmt("[{:<8}]", "hi") == b"[hi      ]")
    check("{:>8} 右对齐（文本）", fmt("[{:>8}]", "hi") == b"[      hi]")

    print("=== bool：`{}` 必须是 True/False（以前静默变 1/0） ===")
    check("{} true", fmt("{}", True) == b"True", fmt("{}", True))
    check("{} false", fmt("{}", False) == b"False")
    check("{} true 带宽度", fmt("[{:>8}]", True) == b"[    True]")

    print("=== 非数字进数字 spec / 数字进文本 spec：报 E-Format-ArgType ===")
    bad_cases = [
        ("{:x} 给负值", ("{:x}", -1), "E-Format-ArgType"),
        ("{:08x} 给负值", ("{:08x}", -1), "E-Format-ArgType"),
        ("{:x} 给浮点（以前静默截成 int）", ("{:x}", 3.7), "E-Format-ArgType"),
        ("{:f} 给整数", ("{:f}", 42), "E-Format-ArgType"),
        ("{:s} 给整数", ("{:s}", 42), "E-Format-ArgType"),
        ("{:08} 给 bool", ("{:08}", True), "E-Format-ArgType"),
        ("{:x} 给文本", ("{:x}", "hi"), "E-Format-ArgType"),
        ("{:08x} 给文本", ("{:08x}", "hi"), "E-Format-ArgType"),
    ]
    for label, args, code in bad_cases:
        msg = fmt_err(*args)
        check(f"{label} → {code}", msg is not None and code in msg, msg)

    print("=== 默认洞与 print(x) 同一套渲染（枚举 / Result 不再漏宿主 TypeError） ===")
    ev = EnumVal("Arithmetic", "DivisionByZero")
    check("{} 枚举 = repr", fmt("{}", ev) == b"Arithmetic.DivisionByZero", fmt("{}", ev))
    check("{} Ok(7) = repr", fmt("{}", Ok(7)) == b"Ok(7)")
    check("{:s} 枚举 → E-Format-ArgType", "E-Format-ArgType" in (fmt_err("{:s}", ev) or ""))

    print("=== 不支持 spec：报 E-Format-BadSpec，文案列出支持集 ===")
    unsupported = ["{:b}", "{:o}", "{:e}", "{:g}", "{:^8}", "{:+}", "{:*<8}",
                   "{:08s}", "{:<08}", "{:.2}", "{:0x}", "{:999999999}", "{:.999f}"]
    for spec in unsupported:
        try:
            _fmtmod.parse(spec)
            check(f"{spec} → E-Format-BadSpec", False, "竟然被接受了")
        except _fmtmod.FmtError as exc:
            ok = (exc.code == "E-Format-BadSpec"
                  and "支持的完整写法" in exc.msg and spec in exc.msg)
            check(f"{spec} → E-Format-BadSpec 且列出支持集", ok, exc.msg[:70])

    try:
        _fmtmod.parse("{:.6}")
        bad_msg = ""
    except _fmtmod.FmtError as exc:
        bad_msg = exc.msg
    check("文案回显用户真实写的 spec（以前硬编码成 `{:.2}`）",
          "`{:.6}`" in bad_msg and "`{:.2}`" not in bad_msg, bad_msg)

    print("=== print 插值 ===")
    ch = 'u64 f(const vector<u64>& xs) { print("a = {}, b = {:>2}", 1, 2); return 0; }'
    Interp(parse_source(ch, "m.eln").module, parse_source(TU, "m.tu").module).call("f", [[1]])
    check("print 用格式串（输出见上一行）", True)

    print("=== 端到端：`print(v)` 与 `print(\"{}\", v)` 必须逐字一致 ===")
    e2e = run_lines(
        'u64 f(const vector<u64>& xs) {'
        '  f64 x = 3.14159265;'
        '  print(x); print("{}", x);'
        '  print("{:.2f}", x);'
        '  bool b = true;'
        '  print(b); print("{}", b);'
        '  vector<bool> bs = vector<bool>::new();'
        '  bs.push(true); bs.push(false);'
        '  print(bs); print("{}", bs);'
        '  print("{}", bs.at(0));'
        '  print("{}", Arithmetic::DivisionByZero);'
        '  return 0;'
        '}')
    want = ["3.14159265", "3.14159265", "3.14",
            "True", "True",
            "True False", "True False",
            "True",
            "Arithmetic.DivisionByZero"]
    check("f64 / bool / vector<bool> / 枚举 都走同一套渲染", e2e == want,
          f"got={e2e!r}")

    print("=== 编译期检查 ===")
    cases = {
        "洞多": ('print("x = {}, y = {}", 1);', "E-Format-Arity"),
        "洞少": ('print("x = {}", 1, 2);', "E-Format-Arity"),
        "非法说明符": ('print("{:.2}", 1);', "E-Format-BadSpec"),
        "十六进制给字节串": ('const vector<u8> s = "a"; print("{:x}", s);', "E-Format-ArgType"),
        "二进制不支持": ('print("{:b}", 5);', "E-Format-BadSpec"),
        "八进制不支持": ('print("{:o}", 8);', "E-Format-BadSpec"),
        "科学计数不支持": ('print("{:e}", 3.5);', "E-Format-BadSpec"),
        "通用浮点不支持": ('print("{:g}", 3.5);', "E-Format-BadSpec"),
    }
    for name, (body, code) in cases.items():
        ch = "u64 f(const vector<u64>& xs) { " + body + " return 0; }"
        got = [d.code for d in check_module(TU, ch, "m").diagnostics]
        check(f"{name} → {code}", code in got, str(got))

    print("=== 正常的不报错 ===")
    ok = ('u64 f(const vector<u64>& xs) {'
          ' print("x = {}, y = {:>2}, z = {:08x}, f = {:.2f}", 1, 2, 255, 3.14159);'
          ' return 0; }')
    check("合法格式串无诊断", [d.code for d in check_module(TU, ok, "m").diagnostics] == [])

    print("=== 文档的 spec 表 = 实现的输出（表格是手写常量，靠这条盯着它不腐烂） ===")
    test_doc_spec_table()

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
