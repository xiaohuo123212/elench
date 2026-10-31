#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_nested2.py —— 一层嵌套到底能干什么（把"现在支持的"钉住）

覆盖六件事（都是**今天就能用**的）：

1. 链式下标 `rows[i][j]`
2. 嵌套元素赋值 `rows[i][j] = x`
3. 把一行 `vec_push` 进外层
4. 传给 `const Vec<Vec<u8>>&` 形参
5. `text_join_vec` 拼回去
6. 逐行求和（Σ 记账）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from interp import Interp             # noqa: E402
from parser import parse_source       # noqa: E402

FAILED = []
TU = "spec f(const Vec<u8>& t) -> u64 { effects {}; }\n"


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(body, data=b"ab;cd"):
    ch = ("func take(const Vec<Vec<u8>>& rows) -> u64 { return rows.length(); }\n"
          "func f(const Vec<u8>& t) -> u64 {\n" + body + "\n}\n")
    mod = parse_source(ch, "m.eln").module
    tu = parse_source(TU, "m.tu").module
    return Interp(mod, tu).call("f", [list(data)])


def main() -> int:
    print("=== 一层嵌套的六种用法 ===")
    check("链式下标 rows[1][0] == 'c'",
          run("""    Vec<Vec<u8>> rows = text_split_vec(t, ";");
    return rows[1][0];""") == ord("c"))
    check("嵌套赋值 rows[0][0] = 65",
          run("""    Vec<Vec<u8>> rows = text_split_vec(t, ";");
    rows[0][0] = 65;
    return rows[0][0];""") == 65)
    check("push 一行进外层（3 行）",
          run("""    Vec<Vec<u8>> rows = text_split_vec(t, ";");
    const Vec<u8> row = text_split_vec(t, ";")[0];
    vec_push(rows, row);
    return rows.length();""") == 3)
    check("传给 const Vec<Vec<u8>>& 形参",
          run("""    Vec<Vec<u8>> rows = text_split_vec(t, ";");
    return take(rows);""") == 2)
    check("拼回去长度 5（ab;cd）",
          run("""    Vec<Vec<u8>> rows = text_split_vec(t, ";");
    return text_join_vec(rows, ";").length();""") == 5)
    check("逐行求和 = 4",
          run("""    Vec<Vec<u8>> rows = text_split_vec(t, ";");
    u64 i = 0;
    u64 s = 0;
    while (i < rows.length()) {
        s = s + rows[i].length();
        i = i + 1;
    }
    return s;""") == 4)

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
