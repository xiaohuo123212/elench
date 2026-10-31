#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""demo_repair.py —— **「违反 → 修复」的现场演示**（3 分钟跑完）

## 讲的故事

1. **声明不变**：`v1` 与 `v2` 的 `.tu` **逐字节相同**（脚本会打印 sha256）；
2. **v1 违反**：手写插入排序，κ = O(n²)，声明要 O(n log n) → `cost` 报债务；
3. **门禁拦住**：`build --deny-debt` **非零退出**（不是"警告了事"）；
4. **v2 修复**：实现换成库里的 `sort`，声明一个字没改 → κ = n log n，达标；
5. **门禁放行**：`build` 零退出。

**这就是三个动词里的 `violate` 与 `repair`**——而且全过程**没有运行用户程序**。

用法：
    python tools/demo_repair.py
"""
import hashlib
import io
import os
import subprocess
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(R, "src")
V1 = os.path.join(R, "demo", "repair", "v1", "sort_small")
V2 = os.path.join(R, "demo", "repair", "v2", "sort_small")


def sha(path: str) -> str:
    with io.open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def run(args):
    """跑 dsh 命令，返回 (退出码, 输出)。"""
    proc = subprocess.run([sys.executable, "dsh.py"] + args, cwd=SRC,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def banner(text: str) -> None:
    print()
    print("=" * 78)
    print(text)
    print("=" * 78)


def main() -> int:
    banner("① 声明完全相同（只有实现不同）")
    h1 = sha(V1 + ".tu")
    h2 = sha(V2 + ".tu")
    print(f"  v1 .tu sha256[:16] = {h1}")
    print(f"  v2 .tu sha256[:16] = {h2}")
    print(f"  → {'相同 ✅（声明一个字没改）' if h1 == h2 else '不同 ❌'}")

    banner("② v1：静态检查通过（语法/类型/契约都没错）")
    rc, out = run(["check", V1 + ".tu", V1 + ".eln"])
    print(out.rstrip())
    print(f"  exit={rc}")

    banner("③ v1：cost —— κ 是 O(n²)，声明要 O(n log n) → **债务**")
    rc, out = run(["cost", V1])
    print(out.rstrip())

    banner("④ v1：build --deny-debt —— **门禁拦住**")
    rc, out = run(["build", V1, "--deny-debt"])
    print(out.rstrip()[:1200])
    print(f"  exit={rc}   ← 非零即「拒绝构建」")
    gate_failed = rc != 0

    banner("⑤ v2：换实现（用库里的 sort），声明不变 → κ = n log n")
    rc2, out2 = run(["cost", V2])
    print(out2.rstrip())

    banner("⑥ v2：build --deny-debt —— **门禁放行**")
    rc3, out3 = run(["build", V2, "--deny-debt"])
    print(out3.rstrip()[:1200])
    print(f"  exit={rc3}   ← 0 即「允许构建」")
    gate_passed = rc3 == 0

    banner("结论")
    ok = gate_failed and gate_passed
    print(f"  · 同一份声明，两种实现：v1 被拦、v2 放行 → {'✅ 演示成立' if ok else '❌ 演示不成立'}")
    print("  · 全过程**没有运行被检查的程序**；判定来自静态成本解释器与门禁。")
    print("  · 这就是 query（κ）→ violate（target 比对）→ repair（换实现）三步。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
