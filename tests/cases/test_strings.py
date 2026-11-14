#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_strings.py —— 字符串字面量 + 变参 `print`（v0.71）

设计见 `文档/设计/目前设计/临时计划-字符串与print.md`：字面量是**编译期常量**
（类型不可变 `vector<u8>`、零分配、成本 1 个操作），`print` 变参、**不引入格式串**。
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

import io                            # noqa: F401  (保留：子测试可能读文件)
import os
import sys


from cost_check import evaluate                       # noqa: E402
from cost_interp import CostInterp                    # noqa: E402
from interp import Interp, Text                       # noqa: E402
from lexer import lex                                 # noqa: E402
from parser import parse_source                       # noqa: E402

FAILED = []

TU = """spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(code):
    tu = parse_source(TU, "m.tu").module
    ch = parse_source(code, "m.eln").module
    return Interp(ch, tu).call("f", [[1]])


def _ret(eln: str, fn: str = "mk"):
    """跑一个无参函数，拿它的**返回值本身**（看值层形态）。"""
    tu = parse_source(TU, "m.tu").module
    ch = parse_source(eln, "m.eln").module
    return Interp(ch, tu).call(fn, [])


def _printed(eln: str) -> str:
    """跑 `f`，把 `print` 的输出收成字符串（断言"打出来的是什么"）。"""
    import contextlib
    import io
    tu = parse_source(TU, "m.tu").module
    ch = parse_source(eln, "m.eln").module
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        Interp(ch, tu).call("f", [[1]])
    return buf.getvalue()


def main() -> int:
    print("=== 词法：解码 ===")
    r = lex('"hi" "A\\n" "B\\x41"')
    toks = [t for t in r.tokens if t.kind.value == "string"]
    check("三个字面量", len(toks) == 3, str(len(toks)))
    check("普通文本", bytes(toks[0].value) == b"hi", repr(toks[0].value))
    check("\\n 转义", bytes(toks[1].value) == b"A\n", repr(toks[1].value))
    check("\\xNN 转义", bytes(toks[2].value) == b"BA", repr(toks[2].value))
    check("UTF-8 字面量", bytes(lex('"中"').tokens[0].value) == "中".encode(), "UTF-8")

    print("=== 语义：字面量是 vector<u8> 常量 ===")
    v = run("""u64 f(const vector<u64>& xs) {
    const vector<u8> t = "abc";
    return t[0];
}""")
    check("可以用下标读字节（a = 97）", v == 97, str(v))

    print("=== 成本：字面量 1 个操作、零分配 ===")
    tu = parse_source(TU, "m.tu").module
    ch = parse_source("""u64 f(const vector<u64>& xs) {
    const vector<u8> t = "hello";
    return 0;
}""", "m.eln").module
    k = CostInterp(ch, tu).cost_of("f")
    check("κ 是常数（不随字面量长度增长）",
          "n(" not in k.render(), k.render())
    check("κ 值很小（5 字节的串不产生 5 次操作）",
          evaluate(k, {"xs": 1}) <= 6, str(evaluate(k, {"xs": 1})))

    print("=== print：变参 + 文本渲染 ===")
    from interp import _builtin_print
    check("两个文本参数", _builtin_print([Text(b"n ="), 42]) == 0, "返回 0")
    check("纯文本", _builtin_print([Text(b"done"), Text(b"ok")]) == 0, "返回 0")

    print("=== vector<u8> 的每条产生路径都是 Text（同一值同一打印） ===")
    # 2026-11-09：以前只有**字面量**是 `Text`，函数返回/内建结果/大括号初始化
    # 落到普通 list，于是 `print(v)` 打数字、`print("{}", v)` 打文本。
    check("字面量", isinstance(_ret("vector<u8> mk() { return \"hi\"; }"), Text),
          "字面量应是 Text")
    check("函数返回（大括号初始化）",
          isinstance(_ret("vector<u8> mk() { vector<u8> b = {104, 105}; return b; }"),
                     Text),
          "大括号初始化后返回也应是 Text")
    check("内建结果（text_concat）",
          isinstance(_ret('vector<u8> mk() { return text_concat("h", "i"); }'), Text),
          "text_concat 结果应是 Text")
    check("vector<u64> 不是 Text（打印仍是数字）",
          not isinstance(_ret("vector<u64> mk() { vector<u64> v = {104, 105}; return v; }"),
                         Text),
          "u64 向量不能被当成文本")
    check("vector<u8> 与 vector<u64> 打印不同",
          _printed("u64 f(const vector<u64>& xs) { vector<u8> b = {104, 105}; "
                   "vector<u64> n = {104, 105}; print(b); print(n); return 0; }")
          == "hi\n104 105\n",
          "u8 打文本、u64 打数字")
    check("print(v) 与 print(\"{}\", v) 一致",
          _printed('u64 f(const vector<u64>& xs) { '
                   'vector<u8> b = {104, 105}; print(b); print("{}", b); return 0; }')
          == "hi\nhi\n",
          "格式路径不能退回数字")
    check("print(\"{}\", vector<u64>) 仍是数字",
          _printed('u64 f(const vector<u64>& xs) { '
                   'vector<u64> n = {104, 105}; print(n); print("{}", n); return 0; }')
          == "104 105\n104 105\n",
          "格式路径不能把 u64 向量当字节串")

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
