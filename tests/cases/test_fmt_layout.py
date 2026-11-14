#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_fmt_layout.py —— fmt 的**排版黑名单**（批次 4 第 13 / 14 / 16 条）

## 为什么需要一条独立的排版测试

`fmt --check` 只回答一个很窄的问题：**"再格式化一次有没有变化"**。
只要格式化器本身错得稳定（把 `{1, 2, 3}` 炸成一行一个元素、把 38 字符的签名
折成两行），它的产物就是"合规"的——`--check` 永远抓不到排版缺陷。

所以这里绕开 `--check`，直接对**格式化产物**跑黑名单：

| 编号 | 坏形态 | 断言 |
|---|---|---|
| 13 | 装得下的签名在 `(` 后断行、续行对齐左括号 | 一行放下；真超宽时续行恰好 4 空格 |
| 14 | 短初始化列表 `{1, 2, 3}` 被炸成多行 | 保持单行（长列表仍允许折行） |
| 16 | `0..4` 变成 `0 .. 4`；`effects {}` 变成 `effects { }` | 两侧紧贴、内部不留空格 |

另外钉住：幂等、token 序列不变、样例文件无超宽行。

跑法：`python -B tests/cases/test_fmt_layout.py`，退出码 0 即通过。
"""

import glob
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from formatter import MAX_LINE, format_source        # noqa: E402
from lexer import Kind, Lexer                        # noqa: E402

SAMPLE = os.path.join(ROOT, "文档", "样例")

_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def toks(src):
    return [t.text for t in Lexer(src).run().tokens if t.kind != Kind.EOF]


# ---------------------------------------------------------------- 黑名单

#: 每条都是"修复前真实出现过的排版"。命中任何一条就是回潮。
BLACKLIST = (
    # 13：签名在 `(` 后无条件断行，续行刚好接在左括号那一列
    (re.compile(r"\(\n[ \t]*const\b"), "13 签名在 `(` 后断行（const 形参）"),
    # 14：`= {` 后面直接换行 → 短列表被当成块展开
    (re.compile(r"=[ \t]*\{\n"), "14 初始化列表被炸成一行一个元素"),
    # 16：`..` 两侧有空格（排除三连点 `...`）
    (re.compile(r"\s(?<!\.)\.\.(?!\.)|(?<!\.)\.\.(?!\.)\s"), "16 `..` 两侧出现空格"),
    # 16：空花括号内部有空格（只查同一行，别把空块 `{\n}` 误判）
    (re.compile(r"\{[ \t]+\}"), "16 空 `{}` 内部出现空格"),
)

#: 已知坏产物（修复前的真实输出）。黑名单必须先命中它自己，否则测试是空跑。
BAD_BEFORE = """pub u64 g(
           const vector<u8>& xs) {
    vector<u64> v = {
        1,
        2,
        3
    };
    for (i in 0 .. 4) {
    }
    return xs.length() + v.length();
}
"""

BAD_EFFECTS = "spec f() -> u64 { effects { }; target { time: { max: O(1) } }; }\n"


def violations(text, only=None):
    """返回命中项 `(编号, 说明)`；`only` 可限定只查某几条。"""
    hits = []
    for rx, label in BLACKLIST:
        if only is not None and not any(label.startswith(p) for p in only):
            continue
        m = rx.search(text)
        if m:
            hits.append((label, m.group(0)))
    return hits


def code_only(text):
    """抹掉注释与字符串字面量（它们不是排版，黑名单不该管内容）。"""
    lex = Lexer(text).run()
    chars = list(text)
    spans = [(t.start, t.end) for t in lex.trivia]
    spans += [(t.start, t.end) for t in lex.tokens if t.kind == Kind.STRING]
    for start, end in spans:
        for i in range(start, min(end, len(chars))):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


# ---------------------------------------------------------------- 样本

PROBE = (
    "pub u64 g(const vector<u8>& xs) {\n"
    "    vector<u64> v = {1, 2, 3};\n"
    "    for (i in 0..4) {\n"
    "    }\n"
    "    return xs.length() + v.length();\n"
    "}\n"
)

#: 同样的 token，故意写成挤在一起 / 带多余空格的形态。
MESSY = (
    "pub u64 g(const   vector<u8>& xs) {vector<u64> v={1,2,3};"
    "for(i in 0 .. 4){}return xs.length()+v.length();}\n"
)

LONG_SIG = ("pub func a_very_long_function_name_here(u64 alpha, u64 beta) -> "
            "SomeVeryLongReturnTypeNameThatGoesOnAndOn { return 0; }")


# ---------------------------------------------------------------- 1. 探针

def test_probe():
    print("[1] 探针：13 / 14 / 16 三条一次覆盖，且产物就是规范形")
    out = format_source(PROBE)
    check(out == PROBE, "规范输入格式化后逐字不变（幂等）",
          "\n" + out.replace(" ", "·"))
    check(not violations(out), "规范产物不命中任何排版黑名单", str(violations(out)))

    messy = format_source(MESSY)
    check(messy == PROBE, "挤成一行的同一 token 序列被收敛到规范形",
          "\n" + messy.replace(" ", "·"))
    check(toks(MESSY) == toks(messy), "格式化不增删 token")
    check(format_source(messy) == messy, "规范形再格式化仍不变")


# ---------------------------------------------------------------- 2. 黑名单自检

def test_blacklist_selfcheck():
    print("[2] 黑名单自检：修复前的真实坏产物必须被抓住")
    labels = [lab for lab, _ in violations(BAD_BEFORE)]
    for want in ("13", "14", "16 `..`"):
        check(any(want in lab for lab in labels), f"坏产物命中「{want}」",
              str(labels))
    check(bool(violations(BAD_EFFECTS)), "坏产物命中「16 空 `{}`」",
          BAD_EFFECTS.strip())
    fixed_effects = "spec f() -> u64 { effects {}; target { time: { max: O(1) } }; }\n"
    check(violations(PROBE) == [] and violations(fixed_effects) == [],
          "（反例）修复后的形状不命中", "黑名单写反了")


# ---------------------------------------------------------------- 3. 签名

def test_signature():
    print("[3] 签名：装得下不折行；真超宽时续行 4 空格（不对齐括号）")

    # 13 的最小复现：38 字符的签名 MUST 保持一行
    src = "pub u64 g(const vector<u8>& xs) { return 0; }"
    out = format_source(src)
    first = out.splitlines()[0]
    check(first == "pub u64 g(const vector<u8>& xs) {",
          "38 字符签名保持一行", first)
    check(len(first) <= MAX_LINE, "签名行不超宽", f"{len(first)} 列")
    check(not violations(out), "签名产物不命中黑名单", str(violations(out)))

    # 真超宽：续行必须比上一行多一级（4 空格），不是对齐左括号
    out2 = format_source(LONG_SIG)
    lines = out2.splitlines()
    check(all(len(ln) <= MAX_LINE for ln in lines), "超宽签名折行后无超宽行",
          f"{[len(ln) for ln in lines]}")
    check(len(lines) >= 2 and lines[0].endswith(","), "在逗号后折行",
          lines[0] if lines else "")
    if len(lines) >= 2:
        indent = len(lines[1]) - len(lines[1].lstrip())
        check(indent == 4, "续行缩进恰好 4 空格", f"{indent} 空格：{lines[1]!r}")
        check(not lines[1].startswith(" " * 8), "续行不对齐左括号", lines[1])
    check(format_source(out2) == out2, "折行后仍幂等")


# ---------------------------------------------------------------- 4. 初始化列表

def test_initializer():
    print("[4] 初始化列表：短的保持单行，长的不超宽")

    short = "u64 t() { vector<u64> v = {1, 2, 3}; return v.length(); }"
    out = format_source(short)
    check("vector<u64> v = {1, 2, 3};" in out, "短列表保持单行 `{1, 2, 3}`",
          out.replace("\n", " | "))
    check("= {\n" not in out, "短列表没有元素级折行", out.replace("\n", " | "))
    check(format_source(out) == out, "短列表幂等")

    expr = "u64 t() { vector<u64> v = {a + 1, twice(a), a}; return v[0]; }"
    check("= {a + 1, twice(a), a};" in format_source(expr),
          "元素是表达式时也保持单行", format_source(expr).replace("\n", " | "))

    empty = "u64 t() { vector<u64> v = {}; return v.length(); }"
    check("vector<u64> v = {};" in format_source(empty), "空列表 `{}` 内部无空格",
          format_source(empty).replace("\n", " | "))

    # 长列表 >= 120 字符：允许按块展开，但必须守住行宽
    long = ("u64 t() { vector<u64> v = {"
            + ", ".join(str(i) for i in range(40)) + "}; return v.length(); }")
    out_long = format_source(long)
    check(all(len(ln) <= MAX_LINE for ln in out_long.splitlines()),
          "长列表折行后无超宽行",
          f"{[len(ln) for ln in out_long.splitlines() if len(ln) > MAX_LINE]}")
    check("= {" in out_long, "长列表仍会被展开（不是一律塞成一行）",
          out_long.replace("\n", " | ")[:120])
    check(format_source(out_long) == out_long, "长列表幂等")

    # 结构体 / 成本槽位的内联花括号仍保留内部空格（别被 14 一并压紧）
    slot = "spec a() -> u64 { effects pure; target { time: { max: O(1) }, } }"
    out_slot = format_source(slot)
    check("{ max: O(1) }" in out_slot, "结构体/成本槽位仍为 `{ max: … }`",
          out_slot.replace("\n", " | "))


# ---------------------------------------------------------------- 5. 范围与空花括号

def test_range_and_empty_braces():
    print("[5] `..` 紧贴、空 `{}` 内部无空格")

    out = format_source(PROBE)
    check("for (i in 0..4) {" in out, "`0..4` 两侧无空格", out.replace("\n", " | "))
    check(" .. " not in out, "没有 ` .. `", out.replace("\n", " | "))

    messy = "u64 t() { for (i in 0 .. 4) { } return 0; }"
    out2 = format_source(messy)
    check("for (i in 0..4) {" in out2, "带空格的输入被收紧成 `0..4`",
          out2.replace("\n", " | "))
    check(".. " not in out2 and " .." not in out2, "`..` 前后都不留空格",
          out2.replace("\n", " | "))

    spec = "spec f() -> u64 { effects { }; target { time: { max: O(1) } }; }"
    out3 = format_source(spec)
    check("effects {};" in out3, "`effects {}` 内部无空格",
          out3.replace("\n", " | "))
    check("{ }" not in out3, "没有 `{ }`", out3.replace("\n", " | "))
    check(format_source(out3) == out3, "spec 产物幂等")


# ---------------------------------------------------------------- 6. 样例回归

def test_samples():
    print("[6] 全部样例：无黑名单命中、无超宽行、幂等")

    files = sorted(glob.glob(os.path.join(SAMPLE, "*.eln")) +
                   glob.glob(os.path.join(SAMPLE, "*.tu")))
    check(bool(files), "样例存在（不是空跑）", str(len(files)))
    bad_hits, bad_width, bad_idem = [], [], []
    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        out = format_source(src)
        # 注释/字符串里的 `...` 不是排版，先抹掉再查。
        # 这里不查 14：长列表本来就应该按块展开（`= {\n`），
        # 第 14 条由探针与 test_initializer 的短列表断言单独钉住。
        hits = violations(code_only(out), only=("13", "16"))
        if hits:
            bad_hits.append((os.path.basename(path), hits))
        over = [len(ln) for ln in out.splitlines() if len(ln) > MAX_LINE]
        if over:
            bad_width.append((os.path.basename(path), over))
        if format_source(out) != out:
            bad_idem.append(os.path.basename(path))
    check(not bad_hits, f"{len(files)} 个样例不命中排版黑名单", str(bad_hits[:2]))
    check(not bad_width, f"{len(files)} 个样例无超宽行", str(bad_width[:2]))
    check(not bad_idem, f"{len(files)} 个样例格式化幂等", str(bad_idem[:4]))


# ---------------------------------------------------------------- main

def main():
    test_probe()
    test_blacklist_selfcheck()
    test_signature()
    test_initializer()
    test_range_and_empty_braces()
    test_samples()

    print()
    total = _passed + len(_failed)
    if _failed:
        print(f"失败 {len(_failed)} / {total}")
        for f in _failed:
            print("  - " + f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
