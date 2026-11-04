#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""otel_local_sink.py —— **不用 Docker 的 OTLP 接收端**（本机调试用）

## 为什么要它

配 OTel 的现实门槛不是"发数据"，是"**先得有个东西收**"：
装 collector、写 yaml、开端口——**为了看一眼自己发的 JSON 长什么样，要装一套基础设施。**

这个小工具把门槛降到一条命令：

```powershell
python tools/otel_local_sink.py            # 监听 4318，收 metrics/traces/logs
```

然后用标准环境变量指过来即可：

```powershell
$env:OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4318"
$env:OTEL_SERVICE_NAME           = "elench"
$env:ELENCH_OTEL_TRACES          = "1"
elench export count --push
```

它做三件事：**收 → 打印摘要 → 追加写 NDJSON 文件**（可 diff、可进 CI 工件）。

**它不是生产 collector**：不存储、不转发、不做背压。**只用于本机确认"我们发出的东西对不对"。**
"""
import argparse
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SIGNALS = {"/v1/metrics": "metrics", "/v1/traces": "traces", "/v1/logs": "logs"}


def summarize(signal: str, body: dict) -> str:
    """把负载压成一行摘要，**不打印整个 JSON**（那会淹没终端）。"""
    try:
        if signal == "metrics":
            rms = body.get("resourceMetrics", [])
            svc = ""
            names, points = [], 0
            for rm in rms:
                for a in rm.get("resource", {}).get("attributes", []):
                    if a.get("key") == "service.name":
                        svc = a.get("value", {}).get("stringValue", "")
                for sm in rm.get("scopeMetrics", []):
                    for m in sm.get("metrics", []):
                        names.append(m.get("name", "?"))
                        points += len(m.get("gauge", {}).get("dataPoints", []))
            return f"service={svc} metrics={len(names)} points={points} [{', '.join(sorted(set(names)))[:70]}]"
        if signal == "traces":
            spans = []
            for rs in body.get("resourceSpans", []):
                for ss in rs.get("scopeSpans", []):
                    spans += ss.get("spans", [])
            return (f"spans={len(spans)} [" +
                    ", ".join(s.get("name", "?") for s in spans[:6]) + "]")
        recs = []
        for rl in body.get("resourceLogs", []):
            for sl in rl.get("scopeLogs", []):
                recs += sl.get("logRecords", [])
        sev = [r.get("severityText", "?") for r in recs]
        return f"logs={len(recs)} severity={sorted(set(sev))}"
    except Exception as exc:                            # noqa: BLE001
        return f"（摘要失败：{type(exc).__name__}: {exc}）"


def make_handler(out_path: str, quiet: bool):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):                              # noqa: N802
            signal = SIGNALS.get(self.path.split("?")[0])
            n = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(n)
            if signal is None:
                self.send_response(404)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
                print(f"  ! 未知路径 {self.path}（只收 /v1/metrics|traces|logs）", file=sys.stderr)
                return
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception as exc:                    # noqa: BLE001
                self.send_response(400)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
                print(f"  ! {signal} 不是合法 JSON：{exc}", file=sys.stderr)
                return

            line = summarize(signal, body)
            ts = time.strftime("%H:%M:%S")
            print(f"  [{ts}] {signal:8} {line}")
            if out_path:
                with open(out_path, "a", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps({"signal": signal, "received_at": time.time(),
                                        "body": body}, ensure_ascii=False) + "\n")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, fmt, *args):              # 静音访问日志
            if not quiet:
                pass

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="本机 OTLP/HTTP 接收端（调试用）")
    ap.add_argument("--port", type=int, default=4318)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--out", default="", help="NDJSON 落盘路径（留空则只打印）")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    srv = ThreadingHTTPServer((a.host, a.port), make_handler(a.out, a.quiet))
    print(f"OTLP sink 监听 http://{a.host}:{a.port}")
    print("  /v1/metrics  /v1/traces  /v1/logs")
    if a.out:
        print(f"  NDJSON → {os.path.abspath(a.out)}")
    print("  Ctrl+C 退出")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
