#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""fetch_incoming.py —— 把某个 ref（PR / fork 分支 / 某个提交）**改动的文件**取到本地。

## 为什么需要它

看 GitHub 上的 PR 或别人的 fork 时，想做的通常不是"合进来"，而是：

> **只想看看他改了哪几个文件，把它们拿到本地对比一下。**

`git merge` 会动工作树，`git checkout branch -- .` 会覆盖一堆不想动的文件。
这个脚本只做三件事：**读**、**比**、**导出到独立目录**——不修改当前分支、不动工作树。

## 三种来源

| 来源 | `--ref` 怎么写 |
|---|---|
| **PR**（未合并） | `--pr 12`（等价于 `pull/12/head`） |
| **别人的 fork 分支** | 先 `git remote add alice https://github.com/alice/repo.git`，再 `--remote alice --ref main` |
| **某个提交 / 已合并的 PR** | `--ref <sha>`（比 `--base` 与它之间的差异） |

## 用法

```bash
# PR #12 相对 main 改了哪些文件 → 导出到 _incoming/pr12/
python tools/fetch_incoming.py --pr 12

# 别人的 fork（先加 remote）
python tools/fetch_incoming.py --remote alice --ref main

# 只导出某个目录里的改动
python tools/fetch_incoming.py --pr 12 --path src

# 只看不导出
python tools/fetch_incoming.py --pr 12 --dry-run
```

导出目录里会有：

- `files/…`：改动后的文件（保持原路径；删除的文件不会出现在这里）
- `MANIFEST.md`：每个文件的状态（新增/修改/删除/重命名）+ 行数增减

**不会**做的事：不切换分支、不 merge、不改工作树、不覆盖同名文件（默认拒绝，`--force` 才覆盖）。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def git(*args: str, cwd: str | None = None, check: bool = True) -> str:
    # `core.quotepath=false`：否则中文路径会被输出成 `"\346\226\207..."`，
    # 拿去当文件名会炸（WinError 123）。
    r = subprocess.run(["git", "-c", "core.quotepath=false"] + list(args),
                       capture_output=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        sys.stderr.write((r.stderr or "").strip() + "\n")
        raise SystemExit(2)
    return r.stdout


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-") or "ref"


def main() -> int:
    ap = argparse.ArgumentParser(description="把一个 ref 改动的文件导出到独立目录")
    ap.add_argument("--repo", default=ROOT, help="仓库路径（默认：本脚本所在仓库）")
    ap.add_argument("--remote", default="origin", help="远端名（默认 origin）")
    ap.add_argument("--pr", type=int, help="PR 编号（自动 fetch pull/<N>/head）")
    ap.add_argument("--ref", help="分支 / 标签 / 提交（与 --pr 二选一）")
    ap.add_argument("--base", default="main", help="比较基准（默认 main）")
    ap.add_argument("--path", action="append", default=[], help="只看这些子路径（可多次）")
    ap.add_argument("--out", help="导出目录（默认 _incoming/<slug>）")
    ap.add_argument("--dry-run", action="store_true", help="只列清单，不写文件")
    ap.add_argument("--force", action="store_true", help="允许覆盖已存在的导出目录")
    ap.add_argument("--no-fetch", action="store_true", help="跳过 git fetch（用本地已有 ref）")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    if not os.path.isdir(os.path.join(repo, ".git")):
        print(f"✗ 不是 git 仓库：{repo}", file=sys.stderr)
        return 2

    # ---- 1) 决定要比较的 ref，并按需 fetch
    if args.pr is not None:
        refspec = f"pull/{args.pr}/head"
        local = f"pr-{args.pr}"
        if not args.no_fetch:
            print(f"→ git fetch {args.remote} {refspec}:{local}")
            git("fetch", args.remote, f"{refspec}:{local}", cwd=repo, check=False)
        target = local
    else:
        if not args.ref:
            print("✗ 需要 --pr N 或 --ref <ref>", file=sys.stderr)
            return 2
        target = args.ref
        if not args.no_fetch:
            print(f"→ git fetch {args.remote}")
            git("fetch", args.remote, cwd=repo, check=False)

    # ---- 2) 确认两个 ref 都能解析（fetch 失败时给出可读的提示）
    for r in (args.base, target):
        if subprocess.run(["git", "rev-parse", "--verify", "--quiet", r + "^{commit}"],
                          cwd=repo, capture_output=True).returncode != 0:
            print(f"✗ 解析不了 `{r}`。\n"
                  f"  可能原因：① 网络不通（GitHub 连不上）→ 稍后重试；\n"
                  f"           ② 还没 fetch → 去掉 --no-fetch；\n"
                  f"           ③ 分支名不同（用 --base 指定）。", file=sys.stderr)
            return 2

    # ---- 3) 列改动（name-status + numstat 合并）
    ns = git("diff", "--name-status", f"{args.base}...{target}", cwd=repo).strip()
    numstat = {}
    for line in git("diff", "--numstat", f"{args.base}...{target}", cwd=repo).split("\n"):
        parts = line.split("\t")
        if len(parts) == 3:
            numstat[parts[2]] = (parts[0], parts[1])

    entries = []
    for line in ns.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:      # 重命名
            entries.append((status, parts[2], parts[1]))
        elif status.startswith("C") and len(parts) >= 3:    # 复制
            entries.append((status, parts[2], parts[1]))
        else:
            entries.append((status, parts[1], None))

    if args.path:
        prefixes = tuple(p.rstrip("/") + "/" for p in args.path)
        entries = [e for e in entries if e[1].startswith(prefixes)]

    if not entries:
        print(f"（{args.base} → {target} 之间没有改动）")
        return 0

    # ---- 4) 导出
    out = args.out or os.path.join(repo, "_incoming", slug(target))
    out = os.path.abspath(out)
    if os.path.isdir(out) and os.listdir(out) and not args.force:
        print(f"✗ 导出目录已存在且非空：{out}\n  加 --force 覆盖，或换 --out。", file=sys.stderr)
        return 2

    A, M, D = "A", "M", "D"
    added = sum(1 for e in entries if e[0].startswith(A))
    deleted = sum(1 for e in entries if e[0].startswith(D))
    modified = len(entries) - added - deleted
    files_dir = os.path.join(out, "files")

    print(f"\n{args.base} → {target}：{len(entries)} 个文件"
          f"（新增 {added} / 修改 {modified} / 删除 {deleted}）")

    manifest = [f"# 改动清单：`{args.base}` → `{target}`", "",
                f"- 新增 {added} / 修改 {modified} / 删除 {deleted}",
                f"- 导出时间：本地", "",
                "| 状态 | 文件 | +/− |", "|---|---|---|"]
    rows = []
    for status, path, old in entries:
        plus, minus = numstat.get(path, ("?", "?"))
        try:
            size = len(git("show", f"{target}:{path}", cwd=repo, check=False).encode("utf-8"))
        except Exception:                                   # noqa: BLE001
            size = 0
        rows.append(f"| {status} | `{path}`{f'（原 `{old}`）' if old else ''} | "
                    f"{plus}/{minus} |")
        print(f"  {status:<3} {path}  (+{plus}/-{minus})")
        if status.startswith(D):
            continue                                        # 删除的文件没有内容可导出
        if args.dry_run:
            continue
        dest = os.path.join(files_dir, path.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        content = subprocess.run(["git", "show", f"{target}:{path}"], cwd=repo,
                                 capture_output=True).stdout
        with open(dest, "wb") as fh:
            fh.write(content)

    if not args.dry_run:
        manifest += rows
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "MANIFEST.md"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(manifest) + "\n")
        print(f"\n→ 已导出到 {out}")
        print(f"  文件在 {os.path.join(out, 'files')}/，清单在 MANIFEST.md")
        print("  想只看差异：git diff --no-index <原文件> <导出文件>")
    else:
        print("\n（--dry-run：没有写任何文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
