#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""preview.py —— 起一个本地服务器，打开组件预览页。

## 为什么要起服务器，不能直接双击

`preview.html` 要读同目录的 `01-html.html` / `02-onMount.js` / `03-onUpdate.js` /
`04-onUnmount.js` —— 这样预览跑的就是**要贴到平台上去的那一份**，不会出现
"预览是对的、贴上去不是"。但浏览器在 `file://` 下不允许 `fetch` 同目录文件，
所以必须走 `http://`。

## 用法

```bash
python -B tools/trufar_component/preview.py          # 自动开浏览器
python -B tools/trufar_component/preview.py --no-open
```
"""
from __future__ import annotations

import argparse
import functools
import http.server
import os
import socket
import sys
import threading
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *a):
        # 预览页会反复请求几个小文件，日志刷屏没意义
        pass


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="preview", description="Elench 组件本地预览")
    ap.add_argument("--port", type=int, default=0, help="端口（默认 0 = 让系统分配）")
    ap.add_argument("--no-open", action="store_true", help="只起服务，不开浏览器")
    args = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    port = args.port or _free_port()
    handler = functools.partial(_Quiet, directory=HERE)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = "http://127.0.0.1:%d/preview.html" % srv.server_address[1]

    print("预览地址：%s" % url, file=sys.stderr)
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
