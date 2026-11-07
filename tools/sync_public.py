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
    python tools/sync_public.py --dry-run  # 只看会公开哪些文件（只读，不动任何 ref）
    python tools/sync_public.py            # 只造提交，不推
    python tools/sync_public.py --push     # 造完就推
"""
import subprocess
import sys

# 内部材料：只允许留本地，任何发布路径都不得包含
INTERNAL_PREFIXES = ("文档/设计/", "文档/设计/归档/实验-2026-11/", "文档/其他/", "文档/待办")

# 明显的脏文件（审稿/打包误入）
def is_junk(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return (
        "~$" in path
        or base.startswith("-")
        or base == "_tensor2.py"
        or base.lower().endswith((".docx", ".doc", ".pdf"))
    )


def git(*args, check=True):
    p = subprocess.run(
        ("git", "-c", "core.quotepath=false") + args,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and p.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败：{p.stderr.strip()}")
    return p.stdout.strip()


def tree_files(rev):
    return [ln for ln in git("ls-tree", "-r", "--name-only", rev).splitlines() if ln.strip()]


def dry_run() -> int:
    """只读检查：打印"对外发布应该包含哪些文件"，并核对没有内部材料。"""
    files = tree_files("HEAD")
    leaks = [f for f in files if f.startswith(INTERNAL_PREFIXES)]
    junk = [f for f in files if is_junk(f)]

    print(f"  候选发布树：HEAD（{git('rev-parse', '--short', 'HEAD')}）共 {len(files)} 个文件")
    print(f"  内部材料条目：{len(leaks)}")
    for f in leaks:
        print("    LEAK:", f)
    print(f"  可疑脏文件：{len(junk)}")
    for f in junk:
        print("    JUNK:", f)

    print("\n  —— 对外发布应包含的文件 ——")
    for f in files:
        print("   ", f)

    parent = git("rev-parse", "--verify", "clean/main", check=False)
    if parent:
        same = git("rev-parse", "HEAD^{tree}") == git("rev-parse", "clean/main^{tree}")
        print(f"\n  与 clean/main 的树相同：{'是（无需新提交）' if same else '否（会造 1 个新提交）'}")
    else:
        print("\n  未找到 clean/main（先 `git fetch clean`），跳过同步状态比较")

    ok = not leaks and not junk
    print(f"\n  结论：{'干净，可以发布' if ok else '不干净，禁止发布'}")
    print("  （--dry-run 只读：没有 fetch、没有 commit-tree、没有 push）")
    return 0 if ok else 1


def _last_synced_dev(parent: str) -> str:
    """上一次同步对应的 dev 提交：从父提交的说明里找 `dev <sha>`。

    公开库的提交说明是**唯一**能记录"上次同步到哪"的地方——
    不能在别处存状态文件（那是又一份会漂移的真相）。
    """
    body = git("log", "-1", "--format=%B", parent, check=False) or ""
    for line in body.splitlines():
        if "dev " in line:
            tail = line.split("dev ", 1)[1]
            sha = "".join(c for c in tail if c.isalnum())[:40]
            if len(sha) >= 7:
                return sha
    return ""


def _public_message(dev: str, parent: str) -> str:
    """公开库的提交说明：**给外面的人看**，不是给自己看。

    以前只写 `Sync from main (dev xxxxxxx)` —— 读者看到的是"同步了一下"，
    既不知道改了什么，也不知道 `dev` 是什么（那是私有仓库的哈希）。

    现在：标题说清楚版本/范围，正文列出**自上次同步以来的真实提交**。
    """
    last = _last_synced_dev(parent)
    rng = f"{last}..HEAD" if last else "HEAD"
    subjects = []
    try:
        out = git("log", "--no-merges", "--reverse", "--format=%s", rng, check=False) or ""
        subjects = [ln.strip() for ln in out.splitlines() if ln.strip()]
    except Exception:                                   # noqa: BLE001
        subjects = []
    # 隐私：dev 的提交说明是内部用语，只取**数量**与**前若干条**做摘要，
    # 且不暴露私有哈希（`dev` 哈希只留在标题的括号里，便于对照调试）。
    title = f"Release {dev}: {len(subjects)} change(s) since last sync" if subjects \
        else f"Release {dev}"
    body = "\n".join(f"- {s}" for s in subjects[:40])
    if len(subjects) > 40:
        body += f"\n- … and {len(subjects) - 40} more"
    return title + "\n\n" + body if body else title


def main() -> int:
    if "--dry-run" in sys.argv:
        return dry_run()

    git("fetch", "clean", check=False)
    tree = git("rev-parse", "HEAD^{tree}")
    parent = git("rev-parse", "clean/main")
    parent_tree = git("rev-parse", "clean/main^{tree}")

    if tree == parent_tree:
        print("  已同步：clean/main 的树与 HEAD 相同，无需新提交")
        return 0

    dev = git("rev-parse", "--short", "HEAD")
    msg = _public_message(dev, parent)
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
