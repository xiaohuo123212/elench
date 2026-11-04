#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""otel_view.py —— 读 `otel_local_sink.py` 落的 NDJSON，**按人看得懂的方式打印**

## 为什么要它

`sink` 只打印一行摘要（`traces spans=13 [?!sum_kernel, measure.point, ...]`），
**看不出"哪个 n 最慢"**——而那恰恰是分档表要回答的问题。

这个工具把 NDJSON 里攒下的 span **按耗时排序**打出来，并且把父子缩进成树：

```
trace 3f2a…
  build                     12.4 ms   module=sum
  ?!sum_kernel              88.1 ms   candidate=sum_by_iter
    measure.point            3.2 ms   n=1
    measure.point            4.9 ms   n=2
    ...
    measure.point           21.7 ms   n=4096      ← 最慢的那个一眼可见
```

用法：

    python tools/otel_view.py otel.ndjson            # 全部
    python tools/otel_view.py otel.ndjson --slowest  # 只按耗时排（不缩进）
    python tools/otel_view.py otel.ndjson --signal metrics
"""
import argparse
import json
import sys


def _ns(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _attr(sp, key, default=""):
    for a in sp.get("attributes", []):
        if a.get("key") == key:
            v = a.get("value", {})
            return v.get("stringValue", v.get("intValue", v.get("doubleValue", default)))
    return default


def load(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:                           # noqa: BLE001
                continue
    return out


def spans_of(records):
    spans = []
    for r in records:
        if r.get("signal") != "traces":
            continue
        for rs in r["body"].get("resourceSpans", []):
            for ss in rs.get("scopeSpans", []):
                spans += ss.get("spans", [])
    return spans


def show_spans(spans, slowest_only=False, limit=40):
    dur = {id(s): (_ns(s.get("endTimeUnixNano")) - _ns(s.get("startTimeUnixNano"))) / 1e6
           for s in spans}
    if slowest_only:
        print(f"  {'耗时(ms)':>10}  {'span':<22} 属性")
        print("  " + "-" * 68)
        for s in sorted(spans, key=lambda x: dur[id(x)], reverse=True)[:limit]:
            detail = " ".join(f"{k}={_attr(s, k)}" for k in
                              ("code.module", "label", "candidate", "benchmark.input_size")
                              if _attr(s, k) != "")
            print(f"  {dur[id(s)]:>10.2f}  {s.get('name', '?'):<22} {detail}")
        return

    by_parent = {}
    for s in spans:
        by_parent.setdefault(s.get("parentSpanId") or "", []).append(s)

    def walk(parent, depth):
        for s in by_parent.get(parent, []):
            detail = " ".join(f"{k}={_attr(s, k)}" for k in
                              ("code.module", "label", "candidate", "benchmark.input_size")
                              if _attr(s, k) != "")
            print(f"  {'  ' * depth}{s.get('name', '?'):<22} {dur[id(s)]:>9.2f} ms  {detail}")
            walk(s.get("spanId"), depth + 1)

    roots = {s.get("traceId") for s in spans}
    for tid in roots:
        print(f"  trace {tid[:12]}…")
        walk("", 1)
        for s in by_parent.get("", []):
            pass
    # 没有 parent 的（parentSpanId 为空但不在 by_parent[""] 里的情况）已覆盖


def show_metrics(records):
    for r in records:
        if r.get("signal") != "metrics":
            continue
        for rm in r["body"].get("resourceMetrics", []):
            svc = ""
            for a in rm.get("resource", {}).get("attributes", []):
                if a.get("key") == "service.name":
                    svc = a.get("value", {}).get("stringValue", "")
            for sm in rm.get("scopeMetrics", []):
                for m in sm.get("metrics", []):
                    dps = m.get("gauge", {}).get("dataPoints", [])
                    print(f"  service={svc}  {m.get('name')}  unit={m.get('unit')}  "
                          f"points={len(dps)}")
                    for dp in dps[:4]:
                        n = next((a["value"].get("intValue") for a in dp.get("attributes", [])
                                  if a.get("key") == "benchmark.input_size"), "?")
                        sem = next((a["value"].get("stringValue")
                                    for a in dp.get("attributes", [])
                                    if a.get("key") == "cost.semantics"), "")
                        print(f"      n={n:<6} value={dp.get('asDouble', dp.get('asInt'))}  "
                              f"semantics={sem}")


def main() -> int:
    ap = argparse.ArgumentParser(description="查看 elench 推出去的 OTel 数据")
    ap.add_argument("path")
    ap.add_argument("--signal", choices=["traces", "metrics", "all"], default="all")
    ap.add_argument("--slowest", action="store_true")
    a = ap.parse_args()

    recs = load(a.path)
    if not recs:
        print("  （文件为空：先跑 tools/otel_local_sink.py --out <这个文件>，再 push）")
        return 1
    if a.signal in ("traces", "all"):
        spans = spans_of(recs)
        print(f"  === traces（{len(spans)} 个 span）===")
        if spans:
            show_spans(spans, slowest_only=a.slowest)
        print()
    if a.signal in ("metrics", "all"):
        print("  === metrics ===")
        show_metrics(recs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
