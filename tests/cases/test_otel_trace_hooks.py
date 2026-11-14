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
| `elench measure` | `?!<标签>` span（每个 `?!`×候选一个）+ 逐点 `measure.point` span |
| `elench measure` 撞步数预算（失败） | `?!<标签>` span **仍然**要有（span 包着 try/except） |

**它抓的是"接线"，不是"库"**——两类 bug 不会互相冒充。

**为什么把测点截到 4096**

`measure` 默认测满 `benchmodel.scale` 声明的**每一个**点（`sum` 是 1..1048576）。
n=2^20 时一个候选就要跑 ~5M 解释器步——正好撞上「疑似死循环」的 5,000,000 步预算，
两个候选合计 ~59s，**而且两个都失败、一张表都不产出**。这条测试要的只是
「span 有没有接上」，一米规模都不需要；工具本来就为这种场景留了 `--max-size`
（见 `dsh.py cmd_measure` 的注释）。截到 4096 后 13 个测点、~0.9s，
接线一条不少，反而**多**了逐点 span 可断言。

**失败路径照样覆盖**

旧版因为测满 2^20，`measure` 其实**是失败的**（两个候选都撞步数预算），
所以它顺带覆盖了「测量抛异常、span 还在不在」。现在成功路径归 [2]，
失败路径用 `ELENCH_STEP_BUDGET=100` 单独钉在 [3]：预算小到必失败，
但不用烧 59s——覆盖的是同一条 `try/except`。

**为什么不直接跑 `文档/样例/`**

`build` / `measure` 会把 `<模块>.cost.yaml` / `<模块>.dispatch.yaml` 写在模块旁边。
`文档/样例/sum.dispatch.yaml` 是别的测试在读的实测分档表，直接写回会**改变它们的输入**，
也会在跑测试时弄脏工作区。所以两份源码先拷进临时目录再跑。
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
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

DSH = os.path.join(HERE, "dsh.py")
SAMPLES = os.path.join(ROOT, "文档", "样例")

#: `measure` 的测点上限（工具自带的 `--max-size`）。不设上限就是测满样例声明的
#: 1..1048576：两个候选合计 ~59s，且都会撞 5M 步预算而失败。
#: 这里只要接线，4096（13 个测点）已足够覆盖 while / for 两条候选路径。
MEASURE_MAX_SIZE = 4096

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


def attrs_of(span):
    """OTLP/JSON 的 attributes：`[{"key":…, "value":{"stringValue":…}}, …]`。"""
    return {a.get("key"): a.get("value") for a in span.get("attributes", [])}


def attr_text(span, key):
    """从 OTLP 的 `{"stringValue": …}` / `{"intValue": …}` 里取纯值。"""
    val = attrs_of(span).get(key) or {}
    if not isinstance(val, dict):
        return ""
    return val.get("stringValue") or val.get("intValue") or ""


def tail(proc, n=200):
    """失败时贴一小段子进程输出，别把整段日志塞进断言消息。"""
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return out[-n:]


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
    # **子进程输出显式钉成 UTF-8**：中文 Windows 上孩子默认按 GBK 写，
    # 父进程按 utf-8 解会 `UnicodeDecodeError`（reader 线程里静默炸掉）。
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **env_extra)
    return subprocess.run([sys.executable, DSH] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=180)


def stage(tmp):
    """把 `sum` 的两份源码拷进临时目录（产物写在那里，不动 `文档/样例/`）。"""
    for name in ("sum.tu", "sum.eln"):
        shutil.copyfile(os.path.join(SAMPLES, name), os.path.join(tmp, name))
    return os.path.join(tmp, "sum.tu"), os.path.join(tmp, "sum.eln")


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

    try:
        with tempfile.TemporaryDirectory(prefix="elench-otel-") as tmp:
            tu, eln = stage(tmp)

            print("[1] build → 必须有 `build` span")
            Recorder.spans.clear()
            proc = run(["build", tu, eln, "--allow-missing-table"], env)
            check(proc.returncode == 0, "build 退出码 0", tail(proc))
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
            proc = run(["measure", tu, eln, f"--max-size={MEASURE_MAX_SIZE}"], env)
            check(proc.returncode == 0, "measure 退出码 0（测点没撞步数预算）", tail(proc))
            names2 = [s.get("name") for s in Recorder.spans]
            check(any((n or "").startswith("?!") for n in names2),
                  "span 名包含 ?!标签", str(names2[:6]))
            label_spans = [s for s in Recorder.spans
                           if (s.get("name") or "").startswith("?!")]
            # 文档说的「每个 ?! × 候选一个 span」：sum_kernel 有两个候选。
            check(len(label_spans) == 2, "?!×候选 各一个 span（共 2 个）",
                  str([s.get("name") for s in label_spans]))
            cands = sorted(attr_text(s, "candidate") for s in label_spans)
            check(cands == ["sum_by_index", "sum_by_iter"], "两个候选各一个 span", str(cands))
            check(all(attr_text(s, "label") == "sum_kernel" for s in label_spans),
                  "每个 ?! span 带 label=sum_kernel",
                  str([attrs_of(s) for s in label_spans]))
            check(all(s.get("traceId") and s.get("spanId") for s in label_spans),
                  "?! span 有 traceId / spanId")
            # 开着 tracing 时是**逐点测**，每个规模点一个 `measure.point`——
            # 这正是"哪个 n 最慢"的数据来源，接线漏了这里也看得见。
            point_cands = sorted({attr_text(s, "candidate") for s in Recorder.spans
                                  if s.get("name") == "measure.point"})
            check(point_cands == ["sum_by_index", "sum_by_iter"],
                  "逐点 measure.point span 两个候选都有", str(point_cands))

            # [2] 是成功路径。旧版测满 2^20 时其实是**失败**路径（撞 5M 步预算），
            # 这一段用极小的预算把它单独钉住：失败也要有 `?!` span、也要带 candidate。
            print("[3] measure 撞步数预算（失败）→ `?!` span 仍然要有")
            Recorder.spans.clear()
            tight = dict(env, ELENCH_STEP_BUDGET="100")
            proc = run(["measure", tu, eln, "--max-size=16"], tight)
            check(proc.returncode != 0, "撞预算时 measure 退出码非 0", tail(proc))
            failed_spans = [s for s in Recorder.spans
                            if (s.get("name") or "").startswith("?!")]
            check(len(failed_spans) == 2, "失败时每个候选仍有 ?! span",
                  str([attrs_of(s) for s in failed_spans]))
            check(sorted(attr_text(s, "candidate") for s in failed_spans)
                  == ["sum_by_index", "sum_by_iter"],
                  "失败时的 ?! span 也带 candidate",
                  str(sorted(attr_text(s, "candidate") for s in failed_spans)))
    finally:
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
