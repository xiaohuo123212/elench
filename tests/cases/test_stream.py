#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_stream.py —— 流式读（第一批：`open_read` / `next_line` / `close`）

## 口径（**不需要 `assume L`**）

`κ(next_line) = 1 + n(ret)` —— **"这次读了多少字节"**，
和参数用**同一套 `n(...)` 写法**（`xs.length()` → `n(xs)`，`ret.length()` → `n(ret)`）。

循环的总上界**由调用方传参**（预算），不是库的假设。
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
import tempfile


from cost_interp import CostInterp    # noqa: E402
from interp import Interp             # noqa: E402
from parser import parse_source       # noqa: E402

FAILED = []
TU = """error Io;
spec f(const vector<u8>& path) -> u64! {
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
    ch = parse_source("u64! f(const vector<u8>& path) {\n" + body + "\n}\n", "m.eln").module
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
            optional::Some(line) => { n = n + 1; }
            optional::None => { more = 0; }
            _ => { more = 0; }
        }
    }
    close(r);
    return n;""", pb)
    check("读到 3 行", getattr(n, "v", n) == 3, repr(n))

    print("=== 内容与长度 ===")
    first = run("""    Reader r = open_read(path)?;
    match (next_line(r)) {
        optional::Some(line) => { close(r); return line.length(); }
        optional::None => { close(r); return 0; }
        _ => { close(r); return 0; }
    }""", pb)
    check("第一行长度 5（alpha）", getattr(first, "v", first) == 5, repr(first))

    print("=== EOF 是 optional::None，不是错误 ===")
    eof = run("""    Reader r = open_read(path)?;
    u64 i = 0;
    while (i < 3) {
        match (next_line(r)) { optional::Some(l) => { } optional::None => { } _ => { } }
        i = i + 1;
    }
    close(r);
    match (next_line(r)) {
        optional::Some(l) => { return 1; }
        optional::None => { return 2; }
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

    print("=== K6：成功值是 Ok 包装，与 Result 语义一致 ===")
    # 2026-11-10 之前成功值返回**裸** `ReaderVal`：`next_line` / `close` 照样能用，
    # 但文档推荐的 `match (open_read(p)) { Ok(r) => … }` **永远不命中**、被 `_`
    # 静默吃掉。这里把三条路都钉住：Ok 臂命中 / Err 臂命中 / 直接传句柄要报错。
    hit = run("""    match (open_read(path)) {
        Ok(r) => { close(r); return 1; }
        Err(e) => { return 2; }
        _ => { return 3; }
    }""", pb)
    check("`match (open_read(p)) { Ok(r) => … }` 命中 Ok 臂",
          getattr(hit, "v", hit) == 1, repr(hit))

    miss = run("""    match (open_read(path)) {
        Ok(r) => { close(r); return 1; }
        Err(e) => { return 2; }
        _ => { return 3; }
    }""", list(os.path.join(d, "no-such-file.txt").encode("utf-8")))
    check("打开不存在的文件 → Err 臂（2）", getattr(miss, "v", miss) == 2, repr(miss))

    try:
        run("""    close(open_read(path));
    return 0;""", pb)
        check("不 `?` 不 `match` 就把 `Reader!` 交给 close → 报错",
              False, "没有报错（会被静默忽略）")
    except Exception as exc:                        # noqa: BLE001
        check("不 `?` 不 `match` 就把 `Reader!` 交给 close → 报错",
              "Reader" in str(exc), str(exc)[:80])

    print("=== 成本：1 + n(ret)，不是常数 ===")
    ch = """u64! f(const vector<u8>& path) {
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
        # **2026-11-08 批次 2**：`map`/`set` 的 `insert` 现在按真实语义记 1 次分配
    # （C++ 里插一个节点就是一次 `new`）。流式读取内部用 `insert`，
    # 于是 alloc 从 1 变成 2——**这是修正后的正确值**，不是回归。
    check("alloc 里有 2（open + 一行插入）", "2" in a, a)

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
