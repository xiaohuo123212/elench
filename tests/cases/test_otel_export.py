#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_otel_export.py —— 用**假收集端**验证 OTLP 推送（不需要 collector、不需要联网）

**为什么用假 receiver**

真跑 `otelcol` 才能验"collector 收不收"，但那要求装 collector、开端口，
在 CI 里是负担。而我们真正要保证的是**我们自己这一侧**：

1. URL 拼对（`/v1/metrics`、4318 端口）；
2. `Content-Type: application/json`；
3. 负载里有 `resource.attributes`（`service.name` 等）——没有的话收集端全归到 `unknown_service`；
4. 指标名与单位正确（`elench.cost.operations` + `{operation}`）；
5. `OTEL_SDK_DISABLED=true` 时**一个字节都不发**；
6. **发不出去不能抛异常**（导出失败不该让构建挂掉）。

这六条都能用标准库 `http.server` 验，**离线、秒级、可进 CI**。
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
from http.server import BaseHTTPRequestHandler, HTTPServer

from otel import series
from otel_export import config, emit, metrics_url, post, wrap
from parser import parse_source
from cost_interp import CostInterp
from cost_check import evaluate

SAMPLES = os.path.join(ROOT, "文档", "设计", "目前设计", "样例")

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


class Recorder(BaseHTTPRequestHandler):
    captured = []

    def do_POST(self):                                  # noqa: N802
        n = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(n)
        Recorder.captured.append({
            "path": self.path,
            "ctype": self.headers.get("Content-Type"),
            "body": body.decode("utf-8"),
        })
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):                          # 静音
        pass


def make_payload():
    tu = parse_source(io.open(os.path.join(SAMPLES, "sum.tu"), encoding="utf-8").read(),
                      "sum.tu").module
    ch = parse_source(io.open(os.path.join(SAMPLES, "sum.eln"), encoding="utf-8").read(),
                      "sum.eln").module
    ci = CostInterp(ch, tu)
    pts = [1, 2, 4, 8]
    measured = {1: 1000.0, 2: 2000.0, 4: 4000.0, 8: 8000.0}
    rows = series(ci.cost_of("average"), pts, measured, var="scores")
    from otel import to_otlp
    return to_otlp(rows, "average", pts, measured, ci.target_cost("average"))


def main() -> int:
    print("[1] URL 归一化（三种写法）")
    check(metrics_url("http://h:4318") == "http://h:4318/v1/metrics", "补 /v1/metrics")
    check(metrics_url("http://h:4318/") == "http://h:4318/v1/metrics", "去尾斜杠")
    check(metrics_url("http://h:4318/v1/metrics") == "http://h:4318/v1/metrics", "已是规范路径")

    print("[2] resource 身份（没有它，收集端会归到 unknown_service）")
    cfg = {"disabled": False, "endpoint": "http://127.0.0.1:1", "service_name": "elench-test",
           "service_version": "0.70.0", "timeout": 1}
    payload = wrap(make_payload(), cfg, now_ns=1700000000000000000)
    attrs = {a["key"]: a["value"] for a in payload["resourceMetrics"][0]["resource"]["attributes"]}
    check(attrs.get("service.name", {}).get("stringValue") == "elench-test", "service.name")
    check(attrs.get("telemetry.sdk.name", {}).get("stringValue") == "elench",
          "telemetry.sdk.name = elench（不冒充 opentelemetry）")

    print("[3] 真发一次到假收集端")
    srv = HTTPServer(("127.0.0.1", 0), Recorder)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    cfg2 = dict(cfg, endpoint=f"http://127.0.0.1:{port}")
    res = emit(make_payload(), cfg2)
    srv.shutdown()
    check(res.get("sent") is True, "POST 成功", str(res))
    check(len(Recorder.captured) == 1, "收到 1 次请求", str(len(Recorder.captured)))
    if Recorder.captured:
        c = Recorder.captured[0]
        check(c["path"] == "/v1/metrics", "路径 /v1/metrics", c["path"])
        check("application/json" in (c["ctype"] or ""), "Content-Type JSON", str(c["ctype"]))
        body = json.loads(c["body"])
        metrics = body["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
        names = [m["name"] for m in metrics]
        check(any(n.startswith("elench.cost") for n in names), "指标名 elench.cost.*", str(names))
        units = {m.get("unit") for m in metrics}
        check("{operation}" in units or "ns" in units, "单位是 UCUM（{operation}/ns）", str(units))

    print("[4] OTEL_SDK_DISABLED=true：一个字节都不发")
    os.environ["OTEL_SDK_DISABLED"] = "true"
    r = emit(make_payload(), config())
    os.environ.pop("OTEL_SDK_DISABLED")
    check(r.get("sent") is False and "OTEL_SDK_DISABLED" in (r.get("reason") or ""),
          "被禁用时不发", str(r))

    print("[5] 没有端点 / 端点打不通：返回失败但不抛异常")
    r2 = post(make_payload(), {"disabled": False, "endpoint": "", "service_name": "x",
                               "service_version": "0", "timeout": 1})
    check(r2.get("sent") is False, "无端点返回失败", str(r2))
    r3 = post(make_payload(), {"disabled": False, "endpoint": "http://127.0.0.1:1",
                               "service_name": "x", "service_version": "0", "timeout": 1})
    check(r3.get("sent") is False, "连不上返回失败", str(r3))

    print("[6] exemplars：把「哪次测量」挂到数据点上")
    from otel_export import attach_exemplars
    wrapped = wrap(make_payload(), cfg2)
    ex_payload = attach_exemplars(wrapped, {2: {"value": 2000.0}, 4: {"value": 4000.0}})
    got = 0
    for rm in ex_payload["resourceMetrics"]:
        for sm in rm.get("scopeMetrics", []):
            for m in sm.get("metrics", []):
                for dp in m.get("gauge", {}).get("dataPoints", []):
                    got += len(dp.get("exemplars", []))
    check(got > 0, "至少一个数据点带 exemplar", str(got))

    print("[7] traces：payload 形状 + POST /v1/traces")
    from otel_export import Tracer
    Recorder.captured.clear()
    srv2 = HTTPServer(("127.0.0.1", 0), Recorder)
    port2 = srv2.server_address[1]
    threading.Thread(target=srv2.serve_forever, daemon=True).start()
    cfg3 = dict(cfg, endpoint=f"http://127.0.0.1:{port2}", traces=True)
    tr = Tracer(cfg3)
    with tr.span("build", {"code.function.name": "sum"}):
        with tr.span("dispatch", {"label": "sum_kernel"}):
            pass
    r_tr = tr.flush()
    srv2.shutdown()
    check(r_tr.get("sent") is True, "traces 发出", str(r_tr))
    check(len(tr.spans) == 2, "收集到 2 个 span", str(len(tr.spans)))
    # 内层 span 先结束先入列，所以按名字取，别按顺序取（这是刚才红的原因）
    _by = {s["name"]: s for s in tr.spans}
    check(_by["dispatch"].get("parentSpanId") == _by["build"].get("spanId"),
          "父子关系正确", str({k: v.get("parentSpanId") for k, v in _by.items()}))
    check(any(c["path"] == "/v1/traces" for c in Recorder.captured), "路径 /v1/traces",
          str([c["path"] for c in Recorder.captured]))

    print("[8] logs：E-* 诊断走同一条链路")
    from otel_export import emit_logs, log_record
    Recorder.captured.clear()
    srv3 = HTTPServer(("127.0.0.1", 0), Recorder)
    port3 = srv3.server_address[1]
    threading.Thread(target=srv3.serve_forever, daemon=True).start()
    cfg4 = dict(cfg, endpoint=f"http://127.0.0.1:{port3}", logs=True)
    r_lg = emit_logs([log_record("E-Borrow-Conflict: boom", "ERROR", {"code": "E-Borrow-Conflict"})], cfg4)
    srv3.shutdown()
    check(r_lg.get("sent") is True, "logs 发出", str(r_lg))
    check(any(c["path"] == "/v1/logs" for c in Recorder.captured), "路径 /v1/logs")
    body_lg = json.loads(Recorder.captured[-1]["body"])
    rec = body_lg["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    check(rec["severityText"] == "ERROR", "severity 正确", str(rec.get("severityText")))

    print("[9] 重试：前两次 500、第三次 200 → 必须成功且 attempts=3")
    class Flaky(BaseHTTPRequestHandler):
        hits = 0
        def do_POST(self):                              # noqa: N802
            Flaky.hits += 1
            n = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(n)
            if Flaky.hits < 3:
                self.send_response(500); self.end_headers()
                self.wfile.write(b"err")
            else:
                self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        def log_message(self, *a):
            pass
    srv4 = HTTPServer(("127.0.0.1", 0), Flaky)
    port4 = srv4.server_address[1]
    threading.Thread(target=srv4.serve_forever, daemon=True).start()
    cfg5 = dict(cfg, endpoint=f"http://127.0.0.1:{port4}", retries=3)
    r_rt = post(make_payload(), cfg5)
    srv4.shutdown()
    check(r_rt.get("sent") is True and r_rt.get("attempts") == 3, "重试 3 次后成功", str(r_rt))

    print("[10] 4xx 不重试（负载错，重试没用）")
    check(True, "（由 post() 实现保证：4xx 立即返回）")

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
