#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_stream.py —— 流式读（第一批：`open_read` / `next_line` / `close`）

## 口径（**不需要 `assume L`**）

`κ(next_line) = 1 + n(ret)` —— **"这次读了多少字节"**，
和参数用**同一套 `n(...)` 写法**（`xs.length()` → `n(xs)`，`ret.length()` → `n(ret)`）。

循环的总上界**由调用方传参**（预算），不是库的假设。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cost_interp import CostInterp    # noqa: E402
from interp import Interp             # noqa: E402
from parser import parse_source       # noqa: E402

FAILED = []
TU = """error Io;
spec f(const Vec<u8>& path) -> u64! {
    effects { fs };
    target { time: { max: O(1) }, allocation: { max: O(1) }, };
}
"""


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(body, path: bytes):
    tu = parse_source(TU, "m.tu").module
    ch = parse_source("func f(const Vec<u8>& path) -> u64! {\n" + body + "\n}\n", "m.eln").module
    return Interp(ch, tu).call("f", [list(path)])


def main() -> int:
    d = tempfile.mkdtemp()
    p = os.path.join(d, "lines.txt")
    open(p, "wb").write(b"alpha\nbeta\ngamma\n")
    pb = list(p.encode("utf-8"))

    print("=== 逐行读 ===")
    n = run("""    Reader r = open_read(path)?;
    u64 n = 0;
    u64 more = 1;
    while (more == 1) {
        match (next_line(r)) {
            Option::Some(line) => { n = n + 1; }
            Option::None => { more = 0; }
            _ => { more = 0; }
        }
    }
    close(r);
    return n;""", pb)
    check("读到 3 行", getattr(n, "v", n) == 3, repr(n))

    print("=== 内容与长度 ===")
    first = run("""    Reader r = open_read(path)?;
    match (next_line(r)) {
        Option::Some(line) => { close(r); return line.length(); }
        Option::None => { close(r); return 0; }
        _ => { close(r); return 0; }
    }""", pb)
    check("第一行长度 5（alpha）", getattr(first, "v", first) == 5, repr(first))

    print("=== EOF 是 Option::None，不是错误 ===")
    eof = run("""    Reader r = open_read(path)?;
    u64 i = 0;
    while (i < 3) {
        match (next_line(r)) { Option::Some(l) => { } Option::None => { } _ => { } }
        i = i + 1;
    }
    close(r);
    match (next_line(r)) {
        Option::Some(l) => { return 1; }
        Option::None => { return 2; }
        _ => { return 3; }
    }""", pb)
    check("读完 3 行后再读 → None（返回 2）", getattr(eof, "v", eof) == 2, repr(eof))

    print("=== 流式写 ===")
    out = os.path.join(d, "out.txt")
    wrote = run("""    Writer w = open_write(path)?;
    write_chunk(w, "abc");
    write_chunk(w, "de");
    close_write(w);
    return 5;""", list(out.encode("utf-8")))
    check("写后文件内容是 abcde",
          open(out, "rb").read() == b"abcde", str(getattr(wrote, "v", wrote)))

    print("=== 成本：1 + n(ret)，不是常数 ===")
    ch = """func f(const Vec<u8>& path) -> u64! {
    Reader r = open_read(path)?;
    const u64 x = next_line(r).length();
    close(r);
    return x;
}
"""
    ci = CostInterp(parse_source(ch, "m.eln").module, parse_source(TU, "m.tu").module)
    k = ci.cost_of("f").render()
    check("κ 含 `1 + n(ret)`", "n(ret)" in k, k)

    print("=== 分配：句柄 1 次 + 每行 1 次 ===")
    a = ci.alloc_of("f").render()
    check("alloc 里有 2（open + 一行）", "2" in a, a)

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
