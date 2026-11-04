#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_contract_diff.py —— 契约 diff（`elench cost --diff`）

## 它守的是什么

`cost --diff` 只比 **κ**（承诺的松紧）。但"这次改动有没有把契约削弱"是另一件事，
而且**完全可判定**：

| 改动 | κ 会动吗 | 现在能看见吗 |
|---|---|---|
| 删掉 `guarantees is_sorted(ret)` | **不动** | ✅ 契约 diff |
| `effects` 从 pure 变成 io | **不动** | ✅ 契约 diff |
| `scale` 从 1..4096 缩到 1..1024 | **不动** | ✅ 契约 diff |
| `target` 从 O(n) 放松成 O(n²) | **不动** | ✅ 契约 diff，且标 **[放松]** |

**放松/削弱是最危险的方向**：κ 不报警，可观察行为却少了一层保证。
"""
import os
import shutil
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
DSH = os.path.join(SRC, "dsh.py")
FORMER = os.path.join(ROOT, "文档", "入门", "例子")

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def git(d, *args):
    return subprocess.run(["git"] + list(args), cwd=d, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def main() -> int:
    if not shutil.which("git"):
        print("跳过：没有 git")
        return 0
    tmp = tempfile.mkdtemp(prefix="elench_ctr_")
    try:
        os.makedirs(os.path.join(tmp, "mod"))
        git(tmp, "init", "-q")
        git(tmp, "config", "user.email", "t@example.com")
        git(tmp, "config", "user.name", "t")
        for f in ("pick.tu", "pick.eln"):
            shutil.copy(os.path.join(FORMER, f), os.path.join(tmp, "mod", "a." + f.split(".")[-1]))
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "base")

        tu_p = os.path.join(tmp, "mod", "a.tu")
        src = open(tu_p, encoding="utf-8").read()
        # 一口气做四种"κ 不动但契约变"的改动
        src = src.replace("effects { };", "effects { io };")
        src = src.replace("O(xs.length())", "O(xs.length() * xs.length())")
        src = src.replace("range(1, 4096, *2)", "range(1, 1024, *2)")
        open(tu_p, "w", encoding="utf-8").write(src)

        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, DSH, "cost", "mod/a", "--diff=HEAD"],
                           cwd=tmp, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        out = (r.stdout or "") + (r.stderr or "")

        print("[1] κ 没变，但契约变了")
        check("有「契约变了」小标题", "契约变了" in out, out[-300:])
        check("effects 变化", "effects" in out and "pure" in out and "io" in out)
        check("scale 缩小", "10024" not in out and "1..1024" in out)
        check("target 变化被识别", "target.time" in out)
        check("target **放松**（不是笼统的『变化』）", "[放松]" in out,
              [l for l in out.splitlines() if "target.time" in l])

        print("[2] 削弱必须显式警告（κ 不会替你报警）")
        check("有削弱/放松警告", "削弱或放松" in out, out[-200:])
        check("说明 κ 不报警", "κ 不会报警" in out)

        print("[3] κ 本身确实没变（只比契约）")
        kline = [l for l in out.splitlines() if "不变" in l]
        check("κ 判为不变", bool(kline), kline[:1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
