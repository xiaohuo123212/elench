#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_env.py —— L2 收尾五个内建：`args` / `env_get` / `cwd` / `exit` / `write`

## 三个语义决定（都写进文档了）

| 内建 | 决定 | 为什么 |
|---|---|---|
| `args()` | 返回**容器**（`args_len` / `args_get`），不是 `vector<vector<u8>>` | 嵌套泛型还没做 |
| `env_get(name)` | **`optional`**（不存在 → `optional::None`），**不是空串** | 空串是**合法值**（环境变量可以是空的），拿空串当"没有"会撞 |
| `exit(code)` | **控制流**，不是失败；**不影响成本上界** | 没执行的代码本来就不贡献承诺 |
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


import stdlib                          # noqa: E402
from interp import ExitRequest, Interp  # noqa: E402
from parser import parse_source        # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(tu, ch, fn="f", args=None):
    tu_m = parse_source(tu, "m.tu").module
    ch_m = parse_source(ch, "m.eln").module
    return Interp(ch_m, tu_m).call(fn, args or [[1]])


TU = "spec f(const vector<u64>& xs) -> u64 { effects { env, io }; }\n"


def main() -> int:
    print("=== args ===")
    stdlib.set_prog_args(["foo", "bar"])
    got = run(TU, """func f(const vector<u64>& xs) -> u64 {
    Args a = args();
    return args_len(a);
}
""")
    check("args_len 读到 2", got == 2, str(got))
    first = run(TU, """func f(const vector<u64>& xs) -> u64 {
    Args a = args();
    return args_get(a, 0).length();
}
""")
    check("args_get(0) 长度 3（foo）", first == 3, str(first))
    stdlib.set_prog_args([])

    print("=== env_get：找不到是 None，不是空串 ===")
    os.environ["ELENCH_T"] = "hello"
    hit = run(TU, """func f(const vector<u64>& xs) -> u64 {
    match (env_get("ELENCH_T")) {
        optional::Some(v) => { return v.length(); }
        optional::None => { return 0; }
        _ => { return 0; }
    }
}
""")
    check("命中 → 长度 5", hit == 5, str(hit))
    miss = run(TU, """func f(const vector<u64>& xs) -> u64 {
    match (env_get("ELENCH_NOPE_XYZ")) {
        optional::Some(v) => { return 1; }
        optional::None => { return 2; }
        _ => { return 3; }
    }
}
""")
    check("未命中 → optional::None 分支（不是空串命中）", miss == 2, str(miss))

    print("=== cwd ===")
    n = run(TU, """func f(const vector<u64>& xs) -> u64 {
    return cwd().length();
}
""")
    check("cwd 非空", n > 0, str(n))

    print("=== exit：控制流 ===")
    try:
        run(TU, """func f(const vector<u64>& xs) -> u64 {
    exit(7);
    return 1;
}
""")
        check("exit 抛 ExitRequest", False, "没有抛出")
    except ExitRequest as e:
        check("exit(7) → 码 7", e.code == 7, str(e.code))

    print("=== write：不换行 ===")
    w = stdlib.FNS["write"]["impl"]([list(b"abc")])
    check("write 返回写入字节数 3", w == 3, str(w))

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
