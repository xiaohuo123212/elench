#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_otel_dashboard.py —— 看板必须**真的画出东西**（不是返回一张空白页）

**为什么测这个**：可视化的失败很安静——页面打开是白的，
你以为是"还没数据"，其实是渲染逻辑坏了。

这里用 NDJSON 假数据直接调 `render()` 与 HTTP 端点，断言：

1. 页面里出现 `<svg`（画了图），不是空 div；
2. 三种 `cost.semantics` 各一种颜色（承诺/分段/实测没被合并）；
3. 最慢 span 排在第一行；
4. 没有数据时给**明确的提示语**，不是白屏；
5. HTTP 端点返回 200 + `text/html`。
"""
import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但**这批测试原先住在 `src/`**，里面大量
#: `os.path.join(HERE, "..", "文档")` / `os.path.join(ROOT, "tools")` 都假设
#: 「本文件在 src/、上一级是仓库根」。搬家时改 200 处路径比让 `HERE` 继续指 src 贵得多，
#: 所以保留旧语义：`HERE` = src/，`CASEDIR` = 本文件真正所在的目录。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import io
import json
import os
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

# `otel_dashboard` 2026-11-03 搬到了 `src/`（打包需要）；`tools/` 里只剩兼容入口，
# 那个入口只在 `__main__` 下 runpy 转发，`import` 拿不到 `render`。
sys.path.insert(0, SRC)
import otel_dashboard as D  # noqa: E402

_passed = 0
_failed = []


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def metric_record(name, sem, pairs):
    dps = []
    for n, v in pairs:
        dps.append({
            "attributes": [
                {"key": "benchmark.input_size", "value": {"intValue": str(n)}},
                {"key": "cost.semantics", "value": {"stringValue": sem}},
            ],
            "asDouble": float(v),
        })
    return {"signal": "metrics", "received_at": 0, "body": {"resourceMetrics": [{
        "resource": {"attributes": []},
        "scopeMetrics": [{"scope": {"name": "elench"}, "metrics": [
            {"name": name, "unit": "{operation}", "gauge": {"dataPoints": dps}}]}],
    }]}}


def span_record(name, ms, attrs=None):
    return {"signal": "traces", "received_at": 0, "body": {"resourceSpans": [{
        "resource": {"attributes": []},
        "scopeSpans": [{"scope": {"name": "elench"}, "spans": [{
            "traceId": "t" * 32, "spanId": "s" * 16, "parentSpanId": "",
            "name": name,
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": str(1000000000 + int(ms * 1e6)),
            "attributes": [{"key": k, "value": {"stringValue": v}}
                           for k, v in (attrs or {}).items()],
        }]}],
    }]}}


def main() -> int:
    recs = [
        metric_record("elench.cost.operations", "upper_bound", [(1, 3154), (64, 85822)]),
        metric_record("elench.cost.operations", "candidate_bound", [(1, 130), (64, 4528)]),
        metric_record("elench.cost.nanoseconds", "measured", [(1, 1.5e6), (64, 1.9e6)]),
        span_record("?!freq_kernel", 111.9, {"candidate": "freq_bucket", "benchmark.input_size": "4096"}),
        span_record("build", 12.4, {"code.module": "count"}),
        span_record("?!freq_kernel", 37.1, {"candidate": "freq_pairwise"}),
    ]
    html = D.render(recs, refresh=3)

    print("[1] 画了图（不是白屏）")
    check("<svg" in html, "页面含 svg")
    check(html.count("<svg") >= 2, "每个指标一张图", str(html.count("<svg")))

    print("[2] 三种 semantics 各一种颜色（没被合并成一条线）")
    check("#1f77b4" in html, "承诺色 #1f77b4")
    check("#ff9d1f" in html, "分段色 #ff9d1f")
    check("#d62728" in html, "实测色 #d62728")

    print("[3] 最慢 span 排第一")
    i1 = html.find("?!freq_kernel")
    check(i1 > 0 and "111.9" in html, "出现 111.9 ms 的 span")
    check(html.find("111.9") < html.find("37.1"), "111.9 在 37.1 之前（按耗时降序）")

    print("[4] 没有数据时给明确提示，而不是白屏")
    empty = D.render([], refresh=3)
    check("点太少" in empty or "还没有 span" in empty, "空数据有提示语")
    check("<svg" not in empty, "空数据不画假图")

    print("[5] HTTP 端点：200 + text/html")
    tmp = os.path.join(os.environ.get("TEMP", "."), "elench_dash_test.ndjson")
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    class H(D.BaseHTTPRequestHandler if False else __import__("http.server").server.BaseHTTPRequestHandler):
        def do_GET(self):                                # noqa: N802
            body = D.render(D.load(tmp), 3).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
        ctype = resp.headers.get("Content-Type", "")
        body = resp.read().decode("utf-8")
    srv.shutdown()
    check("text/html" in ctype, "Content-Type 是 html", ctype)
    check("<svg" in body, "HTTP 返回的页面也有图")

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
