#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_cost_diff.py —— 成本 diff 必须能分出「阶变了」与「只有常数变了」。

## 为什么这条测试重要

`--diff` 的价值全在**分类**上：把 `O(n)` → `O(n²)` 和 `4+n×7` → `4+n×9`
混成一句"变慢了 3 倍"，那这个功能就是装饰。
"""
import os
import shutil
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from cost_diff import classify  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


V1_TU = """spec f(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(n * n) } };
}
"""
V1_ELN = """func f(const Vec<u64>& xs) -> u64 {
    u64 i = 0;
    u64 s = 0;
    while (i < xs.length()) {
        s = s + 1;
        i = i + 1;
    }
    return s;
}
"""
# 加一层循环：O(n) → O(n²)
V2_ELN = """func f(const Vec<u64>& xs) -> u64 {
    u64 i = 0;
    u64 s = 0;
    while (i < xs.length()) {
        u64 j = 0;
        while (j < xs.length()) {
            s = s + 1;
            j = j + 1;
        }
        i = i + 1;
    }
    return s;
}
"""
# 只改常数：多一条加法
V3_ELN = """func f(const Vec<u64>& xs) -> u64 {
    u64 i = 0;
    u64 s = 0;
    while (i < xs.length()) {
        s = s + 1 + 1;
        i = i + 1;
    }
    return s;
}
"""


def git(d, *args):
    subprocess.run(["git"] + list(args), cwd=d, capture_output=True,
                   encoding="utf-8", errors="replace")


def test_classify():
    print("=== classify：阶变 vs 常数变 ===")
    check("同阶只改常数 → L3",
          classify([100.0, 400.0, 1600.0], [110.0, 440.0, 1760.0]) == "L3",
          classify([100.0, 400.0, 1600.0], [110.0, 440.0, 1760.0]))
    check("线性 → 平方 → L1",
          classify([100.0, 400.0, 1600.0], [100.0, 1600.0, 25600.0]) == "L1")
    check("完全一样 → 不变",
          classify([1.0, 2.0], [1.0, 2.0]) == "不变")


def test_cli():
    print("=== CLI：真在 git 仓库里 diff 两个版本 ===")
    d = tempfile.mkdtemp(prefix="eln_diff_t_")
    git(d, "init", "-q")
    git(d, "config", "user.email", "t@example.com")
    git(d, "config", "user.name", "t")
    for name, content in (("m.tu", V1_TU), ("m.eln", V1_ELN)):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(content)
    git(d, "add", ".")
    git(d, "commit", "-q", "-m", "v1")
    with open(os.path.join(d, "m.eln"), "w", encoding="utf-8") as f:
        f.write(V3_ELN)
    git(d, "add", ".")
    git(d, "commit", "-q", "-m", "v3-const")

    r = subprocess.run([sys.executable, os.path.join(SRC, "elench.py"),
                        "cost", "m", "--diff=HEAD~1"],
                       capture_output=True, encoding="utf-8",
                       errors="replace", cwd=d)
    out = (r.stdout or "") + (r.stderr or "")
    check("常数变化报 L3", "L3" in out, out[-300:])
    check("不误报 L1", "L1" not in out, out[-300:])

    with open(os.path.join(d, "m.eln"), "w", encoding="utf-8") as f:
        f.write(V2_ELN)
    git(d, "add", ".")
    git(d, "commit", "-q", "-m", "v2-order")
    r = subprocess.run([sys.executable, os.path.join(SRC, "elench.py"),
                        "cost", "m", "--diff=HEAD~1", "--deny-regress"],
                       capture_output=True, encoding="utf-8",
                       errors="replace", cwd=d)
    out = (r.stdout or "") + (r.stderr or "")
    check("阶变报 L1", "L1" in out, out[-300:])
    check("--deny-regress 退出码 1", r.returncode == 1, str(r.returncode))


def main() -> int:
    test_classify()
    test_cli()
    if FAILED:
        print(f"\n失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
