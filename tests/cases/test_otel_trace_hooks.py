#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_otel_trace_hooks.py —— traces 真的打在 **build / measure / ?!** 上

**为什么单独测**

`test_otel_export.py` 验的是"发送器"（Tracer 能攒、能发、父子关系对）。
但那只证明**库**是好的——**没人保证 `build` 真的开了 span**。

这条测试跑**真实的 CLI 子进程**，用一个假接收端抓 `/v1/traces`，断言：

| 命令 | 必须出现 |
|---|---|
| `elench build` | `build` span |
| `elench measure` | `?!<标签>` span（每个 `?!`×候选一个） |

**它抓的是"接线"，不是"库"**——两类 bug 不会互相冒充。
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

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

DSH = os.path.join(HERE, "dsh.py")
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
    spans = []

    def do_POST(self):                                  # noqa: N802
        n = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(n)
        if self.path.endswith("/v1/traces"):
            try:
                body = json.loads(raw.decode("utf-8"))
                for rs in body.get("resourceSpans", []):
                    for ss in rs.get("scopeSpans", []):
                        for sp in ss.get("spans", []):
                            Recorder.spans.append(sp)
            except Exception:                            # noqa: BLE001
                pass
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def run(args, env_extra):
    env = dict(os.environ, **env_extra)
    return subprocess.run([sys.executable, DSH] + args, capture_output=True,
                          text=True, encoding="utf-8", env=env, timeout=180)


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 0), Recorder)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    env = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{port}",
        "OTEL_SERVICE_NAME": "elench-test",
        "ELENCH_OTEL_TRACES": "1",
        "ELENCH_OTEL_RETRIES": "1",
    }

    print("[1] build → 必须有 `build` span")
    Recorder.spans.clear()
    run(["build", os.path.join(SAMPLES, "sum.tu"), os.path.join(SAMPLES, "sum.eln"),
         "--allow-missing-table"], env)
    names = [s.get("name") for s in Recorder.spans]
    check("build" in names, "span 名包含 build", str(names))
    if "build" in names:
        sp = next(s for s in Recorder.spans if s.get("name") == "build")
        attrs = {a["key"]: a["value"] for a in sp.get("attributes", [])}
        check("code.module" in attrs, "带 code.module 属性", str(list(attrs)))
        check(sp.get("endTimeUnixNano", "0") > sp.get("startTimeUnixNano", "0"),
              "起止时间有序")
        check(sp.get("traceId") and sp.get("spanId"), "有 traceId / spanId")

    print("[2] measure → 必须有 `?!<标签>` span")
    Recorder.spans.clear()
    run(["measure", os.path.join(SAMPLES, "sum.tu"), os.path.join(SAMPLES, "sum.eln")], env)
    names2 = [s.get("name") for s in Recorder.spans]
    check(any(n.startswith("?!") for n in names2), "span 名包含 ?!标签", str(names2[:6]))
    if names2:
        attrs2 = {a["key"]: a["value"]
                  for s in Recorder.spans[:1] for a in s.get("attributes", [])}
        check("candidate" in attrs2 or "label" in attrs2, "带 label / candidate 属性",
              str(list(attrs2)))

    srv.shutdown()
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
