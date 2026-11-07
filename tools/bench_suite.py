#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""bench_suite.py —— 三条复核合并成**一个入口**（T-16）

## 它解决什么

复现论文里的数字以前要记三条命令、三个口径：

| 脚本 | 管什么 |
|---|---|
| `audit_numbers.py` | 每个数字**从哪来**（可复算） |
| `doc_numbers.py --check` | 文档里的数字**绑定到来源**，来源一变就报 |
| `artifact_check.py` | 样例**一键复现**（check / cost / build） |

**现在一条命令出表**，任一条红就 exit 1。

## 为什么不是"再做一张新表"

那三件事**已经在做**。缺的不是新数据，是**一个不会漏跑的入口**——
人记三条命令，漏一条就等于没跑。

## 用法

    python tools/bench_suite.py            # 三条全跑，出汇总表
    python tools/bench_suite.py --fast     # 跳过 artifact（最慢的一条）
"""
import os
import subprocess
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def run(cmd, timeout=600):
    try:
        p = subprocess.run(cmd, cwd=R, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -9, "超时"


def brief(out, n=2):
    lines = [l.strip() for l in out.split("\n") if l.strip()]
    return " / ".join(lines[-n:])[:96]


def main(argv) -> int:
    fast = "--fast" in argv
    checks = [
        ("数字可复算", [PY, "-B", "tools/audit_numbers.py"], False),
        ("数字绑定", [PY, "-B", "tools/doc_numbers.py", "--check"], False),
    ]
    if not fast:
        checks.append(("样例复现", [PY, "-B", "tools/artifact_check.py"], True))

    rows = []
    for name, cmd, optional in checks:
        rc, out = run(cmd)
        ok = rc == 0
        rows.append((name, ok, brief(out)))
        print(f"  {'✓' if ok else '✗'} {name:<10} exit={rc:<4} {brief(out)}")

    bad = [r for r in rows if not r[1]]
    print()
    if not bad:
        print(f"  **{len(rows)} 条全绿**（数字可复算 / 绑定 / 样例复现）")
        return 0
    print(f"  **{len(bad)} 条红**：" + "、".join(r[0] for r in bad))
    print("  逐条查：把对应的脚本单独跑一遍，它自己会打印原因。")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
