#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""release_check.py —— 版本号一致性（CI 门）

## 现状（2026-11-07 实测）

| 文件 | 版本 |
|---|---|
| `pyproject.toml` | **1.0.0** |
| `editors/vscode/package.json` | **0.6.50** |
| `CHANGELOG.md` 最新版本标题 | 从 `## [x.y.z]` 读 |

**三套不一致**——这不是 bug，是**还没做发布统一**。所以本工具用**基线**：

- 首次 `--update` 记下当前差异；
- 之后**只在出现新差异时 exit 1**（防止"又改了版本号但漏了某一处"）。

**目的不是"立刻统一"，是"不再悄悄分叉"。**

用法：

    python tools/release_check.py            # 有新分叉就 exit 1
    python tools/release_check.py --update   # 记基线
    python tools/release_check.py --report   # 只看现状
"""
import io
import json
import os
import re
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(R, "tools", "release_versions_baseline.json")


def read_versions():
    out = {}
    p1 = os.path.join(R, "pyproject.toml")
    if os.path.exists(p1):
        m = re.search(r'^version\s*=\s*"([^"]+)"', io.open(p1, encoding="utf-8").read(), re.M)
        if m:
            out["pyproject.toml"] = m.group(1)
    p2 = os.path.join(R, "editors", "vscode", "package.json")
    if os.path.exists(p2):
        try:
            out["editors/vscode/package.json"] = json.load(
                io.open(p2, encoding="utf-8")).get("version", "")
        except Exception:                              # noqa: BLE001
            pass
    p3 = os.path.join(R, "CHANGELOG.md")
    if os.path.exists(p3):
        m = re.search(r"^##\s*\[(\d+\.\d+(?:\.\d+)?)\]", io.open(p3, encoding="utf-8").read(), re.M)
        if m:
            out["CHANGELOG.md"] = m.group(1)
    return out


def main(argv) -> int:
    cur = read_versions()
    if "--update" in argv:                      # **先处理 --update**：否则没有基线时会被下面的分支挡掉
        io.open(BASE, "w", encoding="utf-8", newline="\n").write(
            json.dumps(cur, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        print(f"  已写基线：{BASE}")
        return 0
    if "--report" in argv or not os.path.exists(BASE):
        print("  当前版本：")
        for k, v in sorted(cur.items()):
            print(f"    {k:<32} {v}")
        distinct = sorted(set(cur.values()))
        if len(distinct) > 1:
            print(f"  ⚠ 三套里有 {len(distinct)} 个不同值：{distinct}")
        if not os.path.exists(BASE):
            print("  （还没有基线：跑 --update 记录现状）")
        return 0
    base = json.load(io.open(BASE, encoding="utf-8"))
    changed = {k: (base.get(k), v) for k, v in cur.items() if base.get(k) != v}
    if changed:
        print("✗ 版本号出现**基线之外**的变化：")
        for k, (old, new) in sorted(changed.items()):
            print(f"    {k}: {old} → {new}")
        print("  发布时请四套一起改；确认无误后跑 --update。")
        return 1
    print(f"  ✓ 与基线一致（{len(cur)} 处版本号；已知分叉不再报警）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
