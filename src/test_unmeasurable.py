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
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

TU = """spec f(const Vec<u64>& xs) -> u64 {
    effects { io };
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

CH = """func f(const Vec<u64>& xs) -> u64 {
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
