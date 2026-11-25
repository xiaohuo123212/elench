#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""probe.py —— 起一个静态服务器，用来试「Elench 能不能在浏览器里跑」（Pyodide/WASM）。

## 这是在验证什么

如果 Pyodide 里能 `import core_api` 并算出正确的 κ，那么**整个网站可以是一个纯静态页**：
不需要服务器、不需要公网地址、不需要内网穿透，丢到任何静态托管（甚至本地双击）就能用。
这对"平台访问不到 127.0.0.1"这个死结是根治。

依据（本机实测的 import 闭包）：

    import core_api  → 新增 56 个模块，**浏览器里没有的：无**
    import dsh       → 只多一个 threading（且只用于 threading.local()）
    subprocess       → 在 dsh.py 里是**函数内惰性 import**，模块级不碰

## 为什么需要这个服务器

`probe.html` 要 `fetch` 仓库里的 `src/**/*.py` 再喂给 Pyodide 的文件系统。
浏览器不允许 `file://` 下 fetch 同目录文件，所以必须走 http://。
服务器同时提供 `/modules.json`（要加载的模块清单）。

## 用法

```bash
python -B tools/wasm/probe.py            # 自动开浏览器
python -B tools/wasm/probe.py --no-open --port 8000
```
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import socket
import sys
import threading
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(ROOT, "src")


def modules() -> list:
    """src/ 下所有 .py（相对路径，用 `/` 分隔）。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in sorted(filenames):
            if f.endswith(".py"):
                p = os.path.join(dirpath, f)
                out.append(os.path.relpath(p, ROOT).replace(os.sep, "/"))
    return sorted(out)


class Handler(http.server.SimpleHTTPRequestHandler):
    #: `.wasm` 必须是 `application/wasm`，否则 `WebAssembly.instantiateStreaming`
    #: 会拒绝（Pyodide 会退化成 arrayBuffer 那条路，慢且容易看着像卡住）。
    extensions_map = dict(http.server.SimpleHTTPRequestHandler.extensions_map)
    extensions_map[".wasm"] = "application/wasm"

    def log_message(self, fmt, *a):
        pass

    def do_GET(self):
        if self.path.split("?")[0] == "/modules.json":
            body = json.dumps({"modules": modules(),
                               "root": os.path.basename(ROOT)},
                              ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="probe", description="Pyodide 可行性探针")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    port = args.port or _free_port()
    handler = functools.partial(Handler, directory=ROOT)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = "http://127.0.0.1:%d/tools/wasm/probe.html" % srv.server_address[1]
    mods = modules()

    print("探针地址：%s" % url, file=sys.stderr)
    print("要喂给浏览器的模块：%d 个（%d KB 源码）"
          % (len(mods), sum(os.path.getsize(os.path.join(ROOT, m)) for m in mods) // 1024),
          file=sys.stderr)
    print("（Ctrl+C 结束）", file=sys.stderr)

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
