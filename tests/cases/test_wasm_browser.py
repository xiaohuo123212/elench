#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_wasm_browser.py —— 在**真浏览器**里跑一遍工作台，确认 κ 与命令行一致。

## 为什么这条测试值得花十几秒

「网站能不能做成纯静态页」这个问题，只有浏览器能回答。前面所有验证
（import 闭包、WASM 模拟、`test_core_api.py`）都只是**代理指标**：

- 2026-11-19 的实测证明代理指标**会漏**：模拟环境全绿，但 Pyodide（CPython 3.12）
  一 import 就 `NameError: name 'Any' is not defined` —— 因为
  `cost_domain/cost_interp.py` 的注解在 ≤3.13 上是**eager 求值**的，而本机是 3.14（惰性）。

所以这条测试直接起无头浏览器，打开 `tools/site/` 的**工作台页面**（就是用户用的那个）：

    __site.boot()        → 加载本地 Pyodide 运行时 + 灌 60 个 .py + import core_api
    __site.run(tu, eln)  → 在浏览器里算 κ

断言：合规 `2 + n(xs)×7 + 2` / ok，违约 `2 + n(xs)×(2 + n(xs)×7) + 2` / debt。

## 什么时候跳过

需要 Node（内置 WebSocket）与 Edge/Chrome。缺任一个就**明确说跳过**并返回 0，
不假装通过——因为"没跑"和"跑过了"必须能区分。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SERVE_PY = os.path.join(ROOT, "tools", "site", "serve.py")
HEADLESS = os.path.join(ROOT, "tools", "wasm", "headless_check.mjs")
RUNTIME = os.path.join(ROOT, "tools", "wasm", "pyodide", "pyodide.asm.wasm")

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _free_port() -> int:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("SKIP  没装 node：浏览器验证需要它（Node 内置 WebSocket 做 CDP）")
        return 0
    if not any(os.path.isfile(b) for b in BROWSERS):
        print("SKIP  没找到 Edge/Chrome：浏览器验证需要无头浏览器")
        return 0
    if not os.path.isfile(RUNTIME):
        print("SKIP  运行时不在（tools/wasm/pyodide/）：先跑 "
              "python -B tools/wasm/fetch_runtime.py")
        return 0

    port = _free_port()
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    srv = subprocess.Popen([sys.executable, "-B", SERVE_PY, "--no-open", "--port", str(port)],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=env)
    try:
        url = "http://127.0.0.1:%d/modules.json" % port
        for _ in range(60):
            try:
                with urllib.request.urlopen(url, timeout=2):
                    break
            except Exception:                      # noqa: BLE001
                time.sleep(0.2)
        else:
            print("FAIL  工作台服务器没起来")
            return 1

        page = "http://127.0.0.1:%d/" % port
        r = subprocess.run([node, HEADLESS, page, "__site", "--compat"], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=900)
        out = (r.stdout or "") + (r.stderr or "")
        ok = (r.returncode == 0 and "与命令行一致" in out)
        print(("  ok   " if ok else "  FAIL ") + "浏览器（Pyodide）里算出的 κ 与命令行一致")
        if not ok:
            for ln in out.strip().splitlines()[-16:]:
                print("      " + ln.strip()[:150])
            return 1
        for ln in out.strip().splitlines():
            if "boot 完成" in ln or "合规：" in ln or "违约：" in ln or "耗时" in ln:
                print("      " + ln.strip())
        return 0
    finally:
        srv.kill()
        try:
            srv.wait(timeout=15)
        except Exception:                          # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
