#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""sync_public.py —— 把当前 HEAD 的**树**同步到对外仓库 `clean`

**为什么不能直接 `git push clean main`**：
`clean` 的历史是**故意独立**的（单提交起点，历史里没有内部材料）。
直接推 `main` 要么被拒（unrelated histories），要么把开发历史（含
`文档/设计/` 的旧提交）一起带过去——后者正是我们要避免的。

**做法**：用 `git commit-tree` 在 `clean/main` 上造一个**新提交**，
树取自当前 HEAD，父提交是 `clean/main`。这样：
  · 内容 = 当前 HEAD 的全部文件（已由 .gitignore 保证不含内部材料）
  · 历史 = clean 自己的线性历史，永远不含开发提交

用法：
    python tools/sync_public.py            # 只造提交，不推
    python tools/sync_public.py --push     # 造完就推
"""
import subprocess
import sys


def git(*args, check=True):
    p = subprocess.run(("git",) + args, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{p.stderr.strip()}")
    return p.stdout.strip()


def main() -> int:
    git("fetch", "clean", check=False)
    tree = git("rev-parse", "HEAD^{tree}")
    parent = git("rev-parse", "clean/main")
    parent_tree = git("rev-parse", "clean/main^{tree}")

    if tree == parent_tree:
        print("  已同步：clean/main 的树与 HEAD 相同，无需新提交")
        return 0

    dev = git("rev-parse", "--short", "HEAD")
    msg = f"Sync from main (dev {dev})"
    new = git("commit-tree", tree, "-p", parent, "-m", msg)
    print(f"  新公开提交 {new[:8]}（父 {parent[:8]}，树 {tree[:8]}）")

    if "--push" in sys.argv:
        git("push", "clean", f"{new}:main")
        print("  已推送到 clean/main")
    else:
        print("  未推送；加 --push 推送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
