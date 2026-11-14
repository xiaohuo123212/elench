#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_json.py —— `json_parse`（**递归类型**的第一个用户）

## 类型

```elench
struct Entry { key: vector<u8>, value: Json, }
enum Json { Null, Bool(u64), Num(u64), Str(vector<u8>), Arr(vector<Json>), Obj(vector<Entry>), }
```

**递归**由 `Arr(vector<Json>)` 表达；运行期就是嵌套 `EnumVal`，值有限 → 不需要新机制。

## 口径

| 项 | 值 |
|---|---|
| 时间 | `O(n(text))` |
| 分配 | `1 + 节点数 ≤ 1 + n(text)` |
| 失败 | `Err(Json::*)`，payload 是**字节位置** |
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


from cost_interp import CostInterp     # noqa: E402
from interp import Interp              # noqa: E402
from parser import parse_source        # noqa: E402

FAILED = []
TU = """struct Entry { key: vector<u8>, value: Json, }
enum Json { Null, Bool(u64), Num(u64), Str(vector<u8>), Arr(vector<Json>), Obj(vector<Entry>), }
spec f(const vector<u8>& text) -> u64 {
    effects {};
    target { time: { max: O(text.length()) }, allocation: { max: O(text.length()) }, };
}
"""
CH = """u64 f(const vector<u8>& text) {
    Json j = json_parse(text)?;
    match (j) {
        Json::Null => { return 0; }
        Json::Bool(b) => { return b; }
        Json::Num(n) => { return n; }
        Json::Str(s) => { return s.length(); }
        Json::Arr(items) => { return items.length(); }
        Json::Obj(entries) => { return entries.length(); }
        _ => { return 999; }
    }
}
"""


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(text: bytes) -> int:
    return Interp(parse_source(CH, "m.eln").module,
                  parse_source(TU, "m.tu").module).call("f", [list(text)])


def main() -> int:
    print("=== 解析 + match 解构 ===")
    check("42 → Num(42)", run(b"42") == 42, str(run(b"42")))
    check("[1,2,3] → 3 个元素", run(b"[1,2,3]") == 3)
    check('{"a":1,"b":2} → 2 个成员', run(b'{"a":1,"b":2}') == 2)
    check('"hello" → 长度 5', run(b'"hello"') == 5)
    check("null → 0", run(b"null") == 0)
    check("true → 1", run(b"true") == 1)
    check("嵌套 [[1],[2,3]] → 外层 2", run(b"[[1],[2,3]]") == 2)

    print("=== 转义 ===")
    check('"a\\nb" 转义后长度 3', run(b'"a\\nb"') == 3)
    check('"\\u4e2d" → 3 字节 UTF-8', run(b'"\\u4e2d"') == 3)

    print("=== 错误：都带位置 ===")
    import jsonval
    cases = {
        b"": ("UnexpectedEof", 0),
        b'{"a":}': ("BadToken", 5),
        b"1.5": ("Unsupported", 1),
        b"[1]x": ("Trailing", 3),
        b'"\\q"': ("BadEscape", 3),
        b"-1": ("Unsupported", 0),
    }
    for data, (code, pos) in cases.items():
        try:
            jsonval.parse(data)
            check(f"{data!r} → {code}", False, "没有报错")
        except jsonval.JsonError as exc:
            check(f"{data!r} → {code}@{pos}", exc.code == code and exc.pos == pos,
                  f"{exc.code}@{exc.pos}")

    print("=== 深度上限 ===")
    deep = b"[" * 300 + b"]" * 300
    try:
        jsonval.parse(deep)
        check("300 层 → Unsupported", False, "没有报错")
    except jsonval.JsonError as exc:
        check("300 层 → Unsupported", exc.code == "Unsupported", exc.code)

    print("=== 成本/分配 ===")
    ci = CostInterp(parse_source(CH, "m.eln").module, parse_source(TU, "m.tu").module)
    check("κ 含 n(text)", "n(text)" in ci.cost_of("f").render(), ci.cost_of("f").render())
    a = ci.alloc_of("f").render()
    check("alloc = 1 + n(text)", a == "1 + n(text)", a)

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
