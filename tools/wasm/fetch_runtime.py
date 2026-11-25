#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""fetch_runtime.py —— 把 Pyodide 运行时**下载到仓库里**，让网页不再依赖 CDN。

## 为什么要把运行时放进来（2026-11-19）

`probe.html` 一开始从 jsdelivr 加载 Pyodide。这有三个现实问题：

1. **离线不可用**：校园网 / 公司代理经常拦 jsdelivr；
2. **CSP**：被平台内嵌时，外部域名可能直接不允许；
3. **不确定**：一个诊断工具不该因为"今天 CDN 慢"而看起来像坏了。

而它其实不大 —— 实测 **13.2 MB**（未压缩）：

    pyodide.asm.wasm   9866 KB   ← 大头，CPython 本体
    python_stdlib.zip  2303 KB
    pyodide.asm.js     1224 KB
    pyodide-lock.json   106 KB
    pyodide.js           14 KB

放进仓库之后：**页面只从本地加载，不联网**。

## 用法

```bash
python -B tools/wasm/fetch_runtime.py            # 缺什么补什么（幂等）
python -B tools/wasm/fetch_runtime.py --check    # 只报告，不下载
python -B tools/wasm/fetch_runtime.py --force    # 全部重下
```

下载目标目录是 `tools/wasm/pyodide/`（默认**入库**；`--gitignore` 会把它写进
`.gitignore` 并跳过下载，适合"只用不提交"的场合）。
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "pyodide")
ROOT = os.path.dirname(os.path.dirname(HERE))

VERSION = "0.27.2"
BASE = "https://cdn.jsdelivr.net/pyodide/v%s/full/" % VERSION

#: (文件名, 期望字节数)。期望值用于"下到半截"的检测——对不上就重下。
FILES = [
    ("pyodide.js", 14 * 1024),
    ("pyodide.asm.js", 1224 * 1024),
    ("pyodide.asm.wasm", 9866 * 1024),
    ("python_stdlib.zip", 2303 * 1024),
    ("pyodide-lock.json", 106 * 1024),
]
#: 允许的误差（KB）：CDN 版本微调不该让脚本误报，但差太多一定要重下。
TOL_KB = 64


def _human(n: int) -> str:
    return "%.1f MB" % (n / 1048576.0) if n >= 1048576 else "%d KB" % round(n / 1024)


def _ok(name: str, size: int) -> bool:
    want = dict(FILES)[name]
    return abs(size - want) // 1024 <= TOL_KB


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fetch_runtime", description="下载 Pyodide 运行时到仓库")
    ap.add_argument("--check", action="store_true", help="只检查，不下载")
    ap.add_argument("--force", action="store_true", help="全部重新下载")
    ap.add_argument("--version", default=VERSION, help="Pyodide 版本（默认 %s）" % VERSION)
    ap.add_argument("--gitignore", action="store_true",
                    help="不下载，改为把 tools/wasm/pyodide/ 写进 .gitignore")
    args = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if args.gitignore:
        gi = os.path.join(ROOT, ".gitignore")
        line = "/tools/wasm/pyodide/"
        txt = open(gi, encoding="utf-8").read() if os.path.isfile(gi) else ""
        if line in txt:
            print("已在 .gitignore：%s" % line)
            return 0
        with open(gi, "a", encoding="utf-8", newline="") as f:
            f.write("\n# Pyodide 运行时：`tools/wasm/fetch_runtime.py` 现取，不占仓库体积\n"
                    + line + "\n")
        print("已写入 .gitignore：%s" % line)
        return 0

    base = "https://cdn.jsdelivr.net/pyodide/v%s/full/" % args.version
    os.makedirs(DEST, exist_ok=True)

    total = 0
    need = []
    for name, want in FILES:
        p = os.path.join(DEST, name)
        have = os.path.getsize(p) if os.path.isfile(p) else -1
        if args.force:
            need.append(name)
            print("  %-20s 强制重下" % name)
        elif have >= 0 and _ok(name, have):
            total += have
            print("  %-20s ✓ 已有 %s" % (name, _human(have)))
        elif have >= 0:
            need.append(name)
            print("  %-20s 大小不对（%s，期望约 %s），重下"
                  % (name, _human(have), _human(want)))
        else:
            need.append(name)
            print("  %-20s 缺，需要下载（约 %s）" % (name, _human(want)))

    if args.check:
        print("")
        print("检查结果：%d 个文件齐（%s），%d 个待下载"
              % (len(FILES) - len(need), _human(total), len(need)))
        return 0 if not need else 1

    for name in need:
        url = base + name
        p = os.path.join(DEST, name)
        tmp = p + ".part"
        print("  下载 %-20s → %s" % (name, os.path.relpath(p, ROOT)))
        try:
            with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, p)
        except Exception as e:                        # noqa: BLE001
            if os.path.exists(tmp):
                os.unlink(tmp)
            print("  ✗ %s 下载失败：%s" % (name, e), file=sys.stderr)
            print("    常见原因：没有网络 / 代理拦了 jsdelivr / 版本号不对（--version）",
                  file=sys.stderr)
            return 1
        sz = os.path.getsize(p)
        total += sz
        if not _ok(name, sz):
            print("  ! %s 下到 %s，与预期差得较多（CDN 版本可能变了）" % (name, _human(sz)),
                  file=sys.stderr)

    # 写一份来源说明，免得以后有人以为这些是手写的
    with open(os.path.join(DEST, "SOURCE.txt"), "w", encoding="utf-8", newline="") as f:
        f.write("Pyodide %s\n来源：%s\n由 tools/wasm/fetch_runtime.py 下载，不要手改。\n"
                % (args.version, base))
    print("")
    print("完成：%d 个文件，共 %s，位置 %s"
          % (len(FILES), _human(total), os.path.relpath(DEST, ROOT)))
    print("页面会优先从这里加载（不再走 CDN）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
