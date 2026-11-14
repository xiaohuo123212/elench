#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""sync_mirror.py —— 把 `src/` 镜像到 `editors/vscode/python/`（含 `cost_domain` 包）。

## 为什么需要它

VS Code 扩展把解释器**打进 vsix**（`editors/vscode/python/`），所以这份代码是
`src/` 的**副本**。副本没有同步工具的下场，2026-11-07 审计已经看到了：
**38 个字节相同、13 个同名但内容不同**——扩展里的解释器比 `src/` 旧。

## 用法

```bash
python tools/sync_mirror.py --check    # CI：副本必须与 src 完全一致（不一致 exit 1）
python tools/sync_mirror.py            # 同步（覆盖副本）
```

## 同步什么

| 来源 | 目标 |
|---|---|
| `src/*.py`（不含 `test_*`） | `editors/vscode/python/*.py` |
| `src/cost_domain/*.py` | `editors/vscode/python/cost_domain/*.py` |

**不**同步：`__pycache__`、`src/_baseline/`（历史副本）、测试文件。
"""
from __future__ import annotations

import os
import shutil
import sys

# **Windows GBK 控制台可能编码不了 `✓`/`✗`**：以前这里一 print 就
# UnicodeEncodeError，`test_mirror_in_sync.py` 只拿到 None，报
# `AttributeError: 'NoneType' object has no attribute 'strip'`，
# **把真正的不一致原因盖住了**。改成 UTF-8 + replace，永不因输出崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                      # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
DST = os.path.join(ROOT, "editors", "vscode", "python")

SKIP = {"_baseline", "__pycache__"}



def same_text(a: str, b: str) -> bool:
    """按**文本**比，忽略 CRLF/LF 差异。

    Windows 上 git 会把检出的文件转成 CRLF，而新写的文件是 LF——
    用字节比会把"只差换行"误报成"内容漂移"（2026-11-07 实际踩到）。
    """
    try:
        ta = open(a, "rb").read().replace(b"\r\n", b"\n")
        tb = open(b, "rb").read().replace(b"\r\n", b"\n")
    except OSError:
        return False
    return ta == tb


def collect(root: str) -> dict:
    """{相对路径: 绝对路径}，只收 `.py`，跳过测试与历史副本。"""
    out = {}
    for dp, dn, fs in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP]
        for f in fs:
            if not f.endswith(".py") or f.startswith("test_"):
                continue
            p = os.path.join(dp, f)
            out[os.path.relpath(p, root)] = p
    return out


def main() -> int:
    check = "--check" in sys.argv
    files = collect(SRC)
    missing, diff = [], []
    for rel, src in sorted(files.items()):
        dst = os.path.join(DST, rel)
        if not os.path.exists(dst):
            missing.append(rel)
        elif not same_text(src, dst):
            diff.append(rel)
        else:
            continue
        if not check:
            os.makedirs(os.path.dirname(dst) or DST, exist_ok=True)
            shutil.copyfile(src, dst)

    if check:
        if not missing and not diff:
            print(f"镜像一致：{len(files)} 个文件")
            return 0
        print(f"✗ 镜像与 src 不一致：缺 {len(missing)} 个、内容不同 {len(diff)} 个")
        for f in missing:
            print("   缺 ", f)
        for f in diff:
            print("   不同", f)
        print("\n修法：python tools/sync_mirror.py")
        return 1

    print(f"已同步 {len(missing) + len(diff)} 个文件（共 {len(files)} 个）")
    for f in missing:
        print("   新建", f)
    for f in diff:
        print("   更新", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
