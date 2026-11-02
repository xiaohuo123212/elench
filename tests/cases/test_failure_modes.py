#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_failure_modes.py —— 强制「失败语义」逐个声明（决策文档的执行者）

规则来自 `文档/设计/目前设计/决策-失败语义.md`：

| 规则 | 检查 |
|---|---|
| F-1 | `sentinel` 模式**必须**在 `SENTINEL` 里写明哨兵值 |
| F-2 | `abort` 只允许出现在 I/O 那四个函数上 |
| F-3 | `err` 模式**必须**在 `ERR_ENUM` 里写明枚举变体 |

外加一条本项目的老规矩：**"不在注册表里"不等于"不用交代"**——
解释器直接实现的内建（`NATIVE`）也要有语义。
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


import stdlib      # noqa: E402

FAILED = []
MODES = {"never", "sentinel", "option", "err", "abort"}
ABORT_ALLOWED = {"read_file", "write_file", "read_stdin", "append_file", "write", "write_chunk"}


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def main() -> int:
    names = stdlib.all_builtins()

    print(f"=== 每个内建都声明了失败语义（共 {len(names)} 个） ===")
    missing = [n for n in names if not stdlib.failure_mode(n)]
    check("没有漏声明的", not missing, str(missing))

    bad = [n for n in names if stdlib.failure_mode(n) not in MODES]
    check("取值只在这五种里", not bad, str(bad))

    print("=== F-1：sentinel 必须写明哨兵值 ===")
    sent = [n for n in names if stdlib.failure_mode(n) == "sentinel"]
    no_val = [n for n in sent if not stdlib.SENTINEL.get(n, "").strip()]
    check(f"{len(sent)} 个 sentinel 都有哨兵说明", not no_val, str(no_val))

    print("=== F-2：abort 只能是 I/O 那四个 ===")
    aborts = {n for n in names if stdlib.failure_mode(n) == "abort"}
    check("abort 集合与允许集合一致", aborts == ABORT_ALLOWED,
          f"多出 {sorted(aborts - ABORT_ALLOWED)}，缺 {sorted(ABORT_ALLOWED - aborts)}")

    print("=== F-3：err 必须写明枚举 ===")
    errs = [n for n in names if stdlib.failure_mode(n) == "err"]
    no_enum = [n for n in errs if not stdlib.ERR_ENUM.get(n, "").strip()]
    check(f"{len(errs)} 个 err 都有枚举说明", not no_enum, str(no_enum))

    print("=== 登记表里没有多余的名字 ===")
    extra = [n for n in list(stdlib.FAIL_MODE) + list(stdlib.SENTINEL) + list(stdlib.ERR_ENUM)
             if n not in names]
    check("没有登记不存在的内建", not extra, str(sorted(set(extra))))

    print("=== 分布（和对账文档用） ===")
    from collections import Counter
    dist = Counter(stdlib.failure_mode(n) for n in names)
    print("    " + "，".join(f"{k} = {dist.get(k, 0)}"
                           for k in ("never", "sentinel", "option", "err", "abort")))

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
