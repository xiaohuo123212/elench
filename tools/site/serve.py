#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""serve.py —— Elench 工作台的本地服务器，以及**一键导出可部署的静态目录**。

## 为什么还需要一个服务器

页面本身是纯静态的，`κ` 是在**浏览器里**算的（Pyodide + `core_api`），没有服务端计算。
但浏览器有两件事必须走 `http://`：

1. 读 `.wasm`（`file://` 下 `fetch` 被禁止）；
2. 读 `src/**/*.py` 再灌进 Pyodide 的文件系统。

所以本地要么 `python -m http.server`，要么用这个脚本——后者顺手把**路由**理清了：

| 路径 | 来自 |
|---|---|
| `/` | `tools/site/index.html` |
| `/modules.json` | 现扫 `src/**/*.py`（不缓存） |
| `/src/...` | 仓库 `src/` |
| `/pyodide/...` | `tools/wasm/pyodide/`（13.2 MB 运行时） |

**开发和部署用的是同一套 URL**，所以 `index.html` 里不需要写死任何环境差异。

## 用法

```bash
python -B tools/site/serve.py                  # http://127.0.0.1:<随机端口>/ 并开浏览器
python -B tools/site/serve.py --port 8000 --no-open
python -B tools/site/serve.py --export dist    # 导出静态目录（可直接托管）
```

导出出来的 `dist/` 里就是 `index.html` + `src/` + `pyodide/` + `modules.json`，
丢到任何静态托管（GitHub Pages / 学校服务器 / nginx）就能用，**不需要 Python**。
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import socket
import sys
import threading
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "src")
PYODIDE = os.path.join(ROOT, "tools", "wasm", "pyodide")
INDEX = os.path.join(HERE, "index.html")

#: 导出时要复制的目录：(仓库内路径, 导出后的相对路径)
EXPORT_TREES = [(SRC, "src"), (PYODIDE, "pyodide")]


def modules() -> list:
    """`src/**/*.py`，返回形如 `src/parser.py` 的相对路径（用 `/`）。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in sorted(filenames):
            if f.endswith(".py"):
                p = os.path.join(dirpath, f)
                out.append(os.path.relpath(p, ROOT).replace(os.sep, "/"))
    return sorted(out)


def _modules_payload() -> bytes:
    return json.dumps({"modules": modules()}, ensure_ascii=False).encode("utf-8")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "elench-site/1.0"

    #: 显式写死要发的 MIME。
    #:   · `.wasm` **必须**是 application/wasm，否则 Pyodide 会退化到 arrayBuffer 那条慢路；
    #:   · `.py` 用 text/plain（浏览器不给它当脚本执行，页面是 fetch 成文本再灌进 FS）。
    #: 注意别从 `BaseHTTPRequestHandler` 上抄 `extensions_map`——那是
    #: `SimpleHTTPRequestHandler` 才有的属性（2026-11-20 就踩了这个 AttributeError）。
    extensions_map = {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".wasm": "application/wasm",
        ".py": "text/plain; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
        ".zip": "application/zip",
        ".map": "application/json; charset=utf-8",
    }

    def log_message(self, fmt, *a):
        pass

    def _send_file(self, path: str) -> None:
        if not os.path.isfile(path):
            self.send_error(404, "not found")
            return
        ctype = "application/octet-stream"
        for ext, t in self.extensions_map.items():
            if path.endswith(ext):
                ctype = t
                break
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send_file(INDEX)
        if path == "/modules.json":
            body = _modules_payload()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/src/"):
            rel = path[len("/src/"):]
            target = os.path.normpath(os.path.join(SRC, rel))
            if not target.startswith(os.path.normpath(SRC) + os.sep) and target != os.path.normpath(SRC):
                return self.send_error(403, "outside src")
            return self._send_file(target)
        if path.startswith("/pyodide/"):
            rel = path[len("/pyodide/"):]
            target = os.path.normpath(os.path.join(PYODIDE, rel))
            if not target.startswith(os.path.normpath(PYODIDE) + os.sep):
                return self.send_error(403, "outside pyodide")
            return self._send_file(target)
        self.send_error(404, "not found")


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def export(dest: str) -> int:
    """把站点materialize 成一个可部署的静态目录。"""
    dest = os.path.abspath(dest)
    if not os.path.isfile(INDEX):
        print("✗ 找不到 %s" % INDEX, file=sys.stderr)
        return 1
    if not os.path.isdir(PYODIDE):
        print("✗ 找不到运行时 %s\n  先跑：python -B tools/wasm/fetch_runtime.py" % PYODIDE,
              file=sys.stderr)
        return 1

    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)

    shutil.copy2(INDEX, os.path.join(dest, "index.html"))
    with open(os.path.join(dest, "modules.json"), "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps({"modules": modules()}, ensure_ascii=False, indent=2) + "\n")
    for src_dir, rel in EXPORT_TREES:
        shutil.copytree(src_dir, os.path.join(dest, rel),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    total = 0
    for dp, _, fs in os.walk(dest):
        for f in fs:
            total += os.path.getsize(os.path.join(dp, f))
    print("已导出：%s（%d 个文件，%.1f MB）" % (dest, sum(len(fs) for _, _, fs in os.walk(dest)),
                                          total / 1048576.0))
    print("部署：把这个目录整个放到任意静态托管即可；不需要 Python。")
    print("验证：cd %s && python -m http.server 8000 → 打开 http://127.0.0.1:8000/" % dest)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="serve", description="Elench 工作台（浏览器里算 κ）")
    ap.add_argument("--port", type=int, default=0, help="端口（默认 0 = 系统分配）")
    ap.add_argument("--no-open", action="store_true", help="不自动开浏览器")
    ap.add_argument("--export", metavar="DIR", help="导出可部署的静态目录，然后退出")
    args = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if args.export:
        return export(args.export)

    if not os.path.isdir(PYODIDE):
        print("提示：运行时不在 %s\n      页面会加载失败；先跑 "
              "python -B tools/wasm/fetch_runtime.py" % PYODIDE, file=sys.stderr)

    port = args.port or _free_port()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    print("工作台：%s" % url, file=sys.stderr)
    print("（κ 在浏览器里算，没有服务端计算；Ctrl+C 结束）", file=sys.stderr)
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。", file=sys.stderr)
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
