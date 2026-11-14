#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_unmeasurable.py —— 「不可测量」策略（v0.71）

## 规则

**允许** `parallel` / 函数体里做 I/O（`io`/`fs`/`env`），
**但这样的函数不做时间测量**——`elench bench` 会拒绝跑，改成**报用量**：

    · f 不可测量：涉及 ['io']
    · 用量：print × 2, sum_of × 1

**理由**：这类效果的成本由外部环境决定，**测出来的是环境，不是算法**。
上界（κ）照样能算，所以 `check` / `cost` 不受影响。
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
import subprocess
import sys
import tempfile


TU = """spec f(const vector<u64>& xs) -> u64 {
    effects { io };
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

CH = """u64 f(const vector<u64>& xs) {
    const u64 s = sum_of(xs);
    print("s =", s);
    print("again");
    return s;
}
"""

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def main() -> int:
    d = tempfile.mkdtemp()
    tu = os.path.join(d, "m.tu")
    ch = os.path.join(d, "m.eln")
    open(tu, "w", encoding="utf-8", newline="\n").write(TU)
    open(ch, "w", encoding="utf-8", newline="\n").write(CH)

    r = subprocess.run([sys.executable, os.path.join(HERE, "dsh.py"), "check", tu, ch],
                       capture_output=True, text=True, encoding="utf-8")
    check("有 I/O 的函数 check 仍然通过（上界照样能算）", r.returncode == 0, r.stdout + r.stderr)

    r = subprocess.run([sys.executable, os.path.join(HERE, "dsh.py"), "bench", tu, ch, "f"],
                       capture_output=True, text=True, encoding="utf-8")
    out = (r.stdout or "") + (r.stderr or "")
    check("bench 不再拒测（改为报用量）", "内建用量" in out or "I/O 用量" in out, out[:200])
    check("bench 给出用量（print × 2）", "print × 2" in out, out[:200])

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
