#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""otel_export.py —— OTel 接入的**完整出口**（metrics / traces / logs，零第三方依赖）

## 为什么不用 opentelemetry-sdk

本项目承诺**零第三方依赖**（README 明写）。SDK 会拖进 protobuf、grpc、requests 一整串。
OTLP/HTTP 的 **JSON 编码**用标准库 `urllib` 就能发——**collector 不关心发送方用没用 SDK**。

代价：不发 protobuf/gRPC。**OTLP/HTTP+JSON 是规范内的合法编码。**

## 五个能力

| # | 能力 | 入口 |
|---|---|---|
| 1 | 推送 metrics | `emit_metrics(payload, cfg)` |
| 2 | traces：`build` / `measure` / 每个 `?!` 一个 span | `Tracer` + `span()` |
| 3 | exemplars：把「哪一次测量」挂到数据点上 | `attach_exemplars(payload, by_size)` |
| 4 | 批量 + 重试 | `post(..., cfg)`、`flush_all([...])` |
| 5 | logs：`E-*` 诊断走同一条链路 | `emit_logs(records, cfg)` |

## 语义（最容易错的一条）

**κ 是上界，不是观测值。** 所以：

- 指标名 `.operations`，属性 `cost.semantics=upper_bound | candidate_bound | measured`；
- 面板**必须**按这个属性分序列，否则两条线会被平均成一个没意义的数。
"""
import json
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from urllib import error as _urlerror
from urllib import request as _urlrequest
from urllib.parse import urlparse

SDK_NAME = "elench"
SDK_VERSION = "0.70.0"
SCHEMA_URL = "https://opentelemetry.io/schemas/1.24.0"


# ---------------------------------------------------------------- 配置

def _truthy(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def config() -> Dict[str, Any]:
    """**只认标准环境变量**（接入方不改我们代码）。

    | 变量 | 作用 | 默认 |
    |---|---|---|
    | `OTEL_EXPORTER_OTLP_ENDPOINT` | 通用端点（按信号加 `/v1/metrics` 等） | 空 = 不推 |
    | `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` / `_TRACES_` / `_LOGS_` | 分信号端点（优先） | 空 |
    | `OTEL_SERVICE_NAME` | `service.name` | `elench` |
    | `OTEL_SDK_DISABLED` | `true` 时**一个字节都不发** | `false` |
    | `ELENCH_OTEL_TRACES` | `1` 时打 span | `false` |
    | `ELENCH_OTEL_LOGS` | `1` 时推诊断 | `false` |
    | `ELENCH_OTEL_RETRIES` | 失败重试次数 | `3` |
    | `ELENCH_OTEL_TIMEOUT` | 秒 | `5` |
    """
    return {
        "disabled": _truthy(os.environ.get("OTEL_SDK_DISABLED", "")),
        "endpoint": os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
        "metrics_endpoint": os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", ""),
        "traces_endpoint": os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", ""),
        "logs_endpoint": os.environ.get("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT", ""),
        "service_name": os.environ.get("OTEL_SERVICE_NAME", SDK_NAME),
        "service_version": os.environ.get("OTEL_SERVICE_VERSION", SDK_VERSION),
        "traces": _truthy(os.environ.get("ELENCH_OTEL_TRACES", "")),
        "logs": _truthy(os.environ.get("ELENCH_OTEL_LOGS", "")),
        "retries": int(os.environ.get("ELENCH_OTEL_RETRIES", "3") or 3),
        "timeout": float(os.environ.get("ELENCH_OTEL_TIMEOUT", "5")),
    }


def signal_url(cfg: Dict[str, Any], signal: str) -> str:
    """拼 `<endpoint>/v1/<signal>`，三种写法都归一化。

    **端口是最常见的坑**：OTLP/HTTP 是 **4318**，gRPC 才是 4317。
    """
    ep = (cfg.get(signal + "_endpoint") or cfg.get("endpoint") or "").strip()
    if not ep:
        return ""
    p = urlparse(ep)
    path = (p.path or "").rstrip("/")
    if path.endswith("/v1/" + signal):
        return ep
    return ep.rstrip("/") + "/v1/" + signal


def metrics_url(endpoint: str) -> str:
    """兼容旧接口：只给一个 endpoint 时拼 metrics 路径。"""
    return signal_url({"endpoint": endpoint}, "metrics")


# ---------------------------------------------------------------- resource

def resource_attrs(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """身份 + 生成者。`telemetry.sdk.name` 写 `elench`——**没用 SDK 就不冒充**。"""
    return [
        {"key": "service.name", "value": {"stringValue": cfg["service_name"]}},
        {"key": "service.version", "value": {"stringValue": cfg["service_version"]}},
        {"key": "telemetry.sdk.name", "value": {"stringValue": SDK_NAME}},
        {"key": "telemetry.sdk.language", "value": {"stringValue": "python"}},
        {"key": "telemetry.sdk.version", "value": {"stringValue": SDK_VERSION}},
    ]


def _resource(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {"attributes": resource_attrs(cfg)}


def _merge_resource(node: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    """合并 resource：**同名属性用配置覆盖**（用户设了 service.name 就必须生效）。"""
    cur = node.setdefault("resource", {})
    merged = {a.get("key"): a for a in cur.get("attributes", [])}
    for a in resource_attrs(cfg):
        merged[a["key"]] = a
    cur["attributes"] = list(merged.values())


# ---------------------------------------------------------------- metrics

def wrap_metrics(payload: Dict[str, Any], cfg: Dict[str, Any],
                 now_ns: Optional[int] = None) -> Dict[str, Any]:
    """把 `otel.to_otlp()` 的产物包成 ExportMetricsServiceRequest。"""
    out = json.loads(json.dumps(payload))
    if "resourceMetrics" in out:
        for rm in out.get("resourceMetrics", []):
            _merge_resource(rm, cfg)
            rm.setdefault("schemaUrl", SCHEMA_URL)
        return out
    return {"resourceMetrics": [{
        "resource": _resource(cfg),
        "schemaUrl": SCHEMA_URL,
        "scopeMetrics": [{
            "scope": {"name": SDK_NAME, "version": SDK_VERSION},
            "metrics": out.get("metrics", []),
        }],
    }]}


def attach_exemplars(payload: Dict[str, Any],
                     by_size: Dict[int, Dict[str, Any]],
                     trace_id: str = "", span_id: str = "") -> Dict[str, Any]:
    """把「哪一次测量」挂到数据点上（OTLP 的 `exemplars`）。

    `by_size`：规模 n → {"value": 浮点, "traceId": 可选, "spanId": 可选, "timeUnixNano": 可选}

    **为什么值得做**：Gauge 只是一个数，图上点进去看不到「这是哪次跑出来的」。
    exemplar 就是把那张图**接到具体样本**上的钩子。
    """
    out = json.loads(json.dumps(payload))
    for rm in out.get("resourceMetrics", []):
        for sm in rm.get("scopeMetrics", []):
            for m in sm.get("metrics", []):
                for dp in m.get("gauge", {}).get("dataPoints", []):
                    n = None
                    for a in dp.get("attributes", []):
                        if a.get("key") == "benchmark.input_size":
                            v = a.get("value", {})
                            n = v.get("intValue", v.get("stringValue"))
                    try:
                        n = int(n) if n is not None else None
                    except (TypeError, ValueError):
                        n = None
                    ex = by_size.get(n) if n is not None else None
                    if not ex:
                        continue
                    dp.setdefault("exemplars", []).append({
                        "timeUnixNano": str(ex.get("timeUnixNano") or time.time_ns()),
                        "asDouble": float(ex["value"]),
                        "traceId": ex.get("traceId") or trace_id or uuid.uuid4().hex,
                        "spanId": ex.get("spanId") or span_id or uuid.uuid4().hex[:16],
                    })
    return out




def add_piecewise_metric(payload: Dict[str, Any], rows: List[Dict[str, Any]],
                         function_name: str) -> Dict[str, Any]:
    """把 **κ分段** 作为同名的 `elench.cost.operations` 再推一条，
    靠 `cost.semantics=candidate_bound` 区分。

    **为什么必须单独一条**：承诺（upper_bound）与分段最优（candidate_bound）
    是同一个量的两种口径。混在一条里，面板会把它俩平均——
    那正好抹掉我们最想让人看见的差别。
    """
    dps = []
    for r in rows:
        v = r.get("static_piecewise")
        if v is None:
            continue
        dps.append({
            "attributes": [
                {"key": "code.function.name", "value": {"stringValue": function_name}},
                {"key": "benchmark.input_size", "value": {"intValue": str(r["size"])}},
                {"key": "cost.semantics", "value": {"stringValue": "candidate_bound"}},
            ],
            "timeUnixNano": str(time.time_ns()),
            "asDouble": float(v),
        })
    if not dps:
        return payload
    out = json.loads(json.dumps(payload))
    for rm in out.get("resourceMetrics", []):
        for sm in rm.get("scopeMetrics", []):
            sm.setdefault("metrics", []).append({
                "name": "elench.cost.operations",
                "unit": "{operation}",
                "gauge": {"dataPoints": dps},
            })
    return out


# ---------------------------------------------------------------- traces

class Tracer:
    """极简 span 收集器：`with tracer.span("build", {"x": 1}):`。

    **不是通用 tracing 库**——只覆盖我们要的两三件事：`build`、`measure`、每个 `?!`。
    超过这个范围就该换 SDK 了，但那时项目也不再是零依赖。
    """

    def __init__(self, cfg: Optional[Dict[str, Any]] = None):
        self.cfg = cfg or config()
        self.spans: List[Dict[str, Any]] = []
        self.trace_id = uuid.uuid4().hex
        self._stack: List[Dict[str, Any]] = []
        self.enabled = bool(self.cfg.get("traces")) and bool(
            self.cfg.get("traces_endpoint") or self.cfg.get("endpoint"))

    def _attrs(self, attrs: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for k, v in (attrs or {}).items():
            if isinstance(v, bool):
                out.append({"key": k, "value": {"boolValue": v}})
            elif isinstance(v, int):
                out.append({"key": k, "value": {"intValue": str(v)}})
            elif isinstance(v, float):
                out.append({"key": k, "value": {"doubleValue": v}})
            else:
                out.append({"key": k, "value": {"stringValue": str(v)}})
        return out

    @contextmanager
    def span(self, name: str, attrs: Optional[Dict[str, Any]] = None,
             status_error: bool = False):
        if not self.enabled:
            yield None
            return
        sp = {
            "traceId": self.trace_id,
            "spanId": uuid.uuid4().hex[:16],
            "parentSpanId": self._stack[-1]["spanId"] if self._stack else "",
            "name": name,
            "kind": 1,                                     # SPAN_KIND_INTERNAL
            "startTimeUnixNano": str(time.time_ns()),
            "attributes": self._attrs(attrs or {}),
            "status": {"code": 2 if status_error else 1},  # ERROR / OK
        }
        self._stack.append(sp)
        try:
            yield sp
        finally:
            sp["endTimeUnixNano"] = str(time.time_ns())
            self._stack.pop()
            self.spans.append(sp)

    def payload(self) -> Dict[str, Any]:
        return {"resourceSpans": [{
            "resource": _resource(self.cfg),
            "schemaUrl": SCHEMA_URL,
            "scopeSpans": [{
                "scope": {"name": SDK_NAME, "version": SDK_VERSION},
                "spans": self.spans,
            }],
        }]}

    def flush(self) -> Dict[str, Any]:
        if not self.enabled or not self.spans:
            return {"sent": False, "reason": "no spans / disabled"}
        return post(self.payload(), self.cfg, "traces")


# ---------------------------------------------------------------- logs

def log_record(body: str, severity: str = "WARN",
               attrs: Optional[Dict[str, Any]] = None,
               now_ns: Optional[int] = None) -> Dict[str, Any]:
    sev = {"TRACE": 1, "DEBUG": 5, "INFO": 9, "WARN": 13, "ERROR": 17}.get(severity, 13)
    return {
        "timeUnixNano": str(now_ns or time.time_ns()),
        "severityNumber": sev,
        "severityText": severity,
        "body": {"stringValue": body},
        "attributes": [{"key": k, "value": {"stringValue": str(v)}}
                       for k, v in (attrs or {}).items()],
    }


def wrap_logs(records: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {"resourceLogs": [{
        "resource": _resource(cfg),
        "schemaUrl": SCHEMA_URL,
        "scopeLogs": [{
            "scope": {"name": SDK_NAME, "version": SDK_VERSION},
            "logRecords": records,
        }],
    }]}


# ---------------------------------------------------------------- 发送（批量 + 重试）

def post(payload: Dict[str, Any], cfg: Dict[str, Any],
         signal: str = "metrics") -> Dict[str, Any]:
    """POST 一个负载。**重试 5xx / 连接失败，不重试 4xx**（4xx 是负载错，重试没用）。"""
    if cfg.get("disabled"):
        return {"sent": False, "reason": "OTEL_SDK_DISABLED"}
    url = signal_url(cfg, signal)
    if not url:
        return {"sent": False, "reason": f"no {signal} endpoint"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    retries = max(0, int(cfg.get("retries", 3)))
    last = ""
    for attempt in range(retries + 1):
        req = _urlrequest.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "User-Agent": f"{SDK_NAME}/{SDK_VERSION}",
        })
        try:
            with _urlrequest.urlopen(req, timeout=cfg["timeout"]) as resp:
                return {"sent": True, "url": url, "status": resp.status,
                        "bytes": len(body), "attempts": attempt + 1}
        except _urlerror.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if 400 <= exc.code < 500:
                return {"sent": False, "url": url, "reason": last, "attempts": attempt + 1}
        except Exception as exc:                    # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(min(0.2 * (2 ** attempt), 2.0))
    return {"sent": False, "url": url, "reason": last, "attempts": retries + 1}


def emit_metrics(payload: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None,
                 now_ns: Optional[int] = None) -> Dict[str, Any]:
    cfg = cfg or config()
    return post(wrap_metrics(payload, cfg, now_ns), cfg, "metrics")


def emit_logs(records: List[Dict[str, Any]],
              cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or config()
    if not records:
        return {"sent": False, "reason": "no records"}
    return post(wrap_logs(records, cfg), cfg, "logs")


def flush_all(payloads: List[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None,
              signal: str = "metrics") -> List[Dict[str, Any]]:
    """依次发送一批负载（批量：一条命令只开一条连接序列，不在每条上加延迟）。"""
    cfg = cfg or config()
    return [post(p, cfg, signal) for p in payloads]


# 兼容旧名字
def emit(metrics_payload: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None,
         now_ns: Optional[int] = None) -> Dict[str, Any]:
    return emit_metrics(metrics_payload, cfg, now_ns)


def wrap(metrics_payload: Dict[str, Any], cfg: Dict[str, Any],
         now_ns: Optional[int] = None) -> Dict[str, Any]:
    return wrap_metrics(metrics_payload, cfg, now_ns)
