#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""otel_dashboard.py —— **本机可视化**：把 OTel 数据画成一张网页

## 为什么还要自己做这个

标准答案是 Grafana，但它要求你先装 Prometheus/Tempo、写数据源、配 provision——
**为了看自己刚推出去的两条线，先搭一套运维栈。**

这个工具只做一件事：**读 sink 落的 NDJSON，画一张自刷新的网页。**

```bat
python tools\\otel_dashboard.py --file otel.ndjson --port 8080
```

浏览器打开 <http://127.0.0.1:8080> —— **每 3 秒自动刷新**，边跑 `elench export --push` 边看。

## 画什么

| 区域 | 内容 |
|---|---|
| **成本曲线** | 每个指标一张 SVG（双对数）：按 `cost.semantics` 分成不同颜色的折线 |
| **最慢 span** | 按耗时排序的条形表（一眼看出哪个 n / 哪个候选最慢） |
| **原始计数** | 收到多少 metrics / traces / logs |

**零依赖、零前端框架、单文件**：HTML 里内联 SVG，服务端现算。
"""
import argparse
import json
import math
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ORDER = {"upper_bound": "#1f77b4", "candidate_bound": "#ff9d1f", "measured": "#d62728"}
LABEL = {"upper_bound": "κ承诺（最劣候选）", "candidate_bound": "κ分段（逐段最优）",
         "measured": "实测"}


def load(path):
    recs = []
    if not os.path.exists(path):
        return recs
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:                           # noqa: BLE001
                continue
    return recs


def collect(recs):
    """→ (series, spans, counts)。series: (指标名, semantics) → [(n, value)]"""
    series, spans = {}, []
    counts = {"metrics": 0, "traces": 0, "logs": 0}
    for r in recs:
        sig = r.get("signal")
        counts[sig] = counts.get(sig, 0) + 1
        body = r.get("body", {})
        if sig == "metrics":
            for rm in body.get("resourceMetrics", []):
                for sm in rm.get("scopeMetrics", []):
                    for m in sm.get("metrics", []):
                        for dp in m.get("gauge", {}).get("dataPoints", []):
                            attrs = {a["key"]: a["value"] for a in dp.get("attributes", [])}
                            n = int(attrs.get("benchmark.input_size", {}).get("intValue", 0))
                            sem = attrs.get("cost.semantics", {}).get("stringValue", "?")
                            v = dp.get("asDouble", dp.get("asInt"))
                            if v is None:
                                continue
                            series.setdefault((m.get("name", "?"), sem), []).append((n, float(v)))
        elif sig == "traces":
            for rs in body.get("resourceSpans", []):
                for ss in rs.get("scopeSpans", []):
                    for s in ss.get("spans", []):
                        try:
                            d = (int(s["endTimeUnixNano"]) - int(s["startTimeUnixNano"])) / 1e6
                        except Exception:               # noqa: BLE001
                            d = 0.0
                        attrs = {a["key"]: a["value"] for a in s.get("attributes", [])}
                        spans.append({
                            "name": s.get("name", "?"),
                            "ms": d,
                            "detail": " ".join(
                                f"{k}={attrs[k].get('stringValue', attrs[k].get('intValue', ''))}"
                                for k in ("code.module", "label", "candidate",
                                          "benchmark.input_size") if k in attrs),
                        })
    for k in series:
        series[k] = sorted(series[k])
    return series, spans, counts


def svg_line(points_by_series, title, w=720, h=340):
    """画一张双对数折线图（内联 SVG，不依赖任何前端库）。"""
    all_pts = [p for pts in points_by_series.values() for p in pts if p[0] > 0 and p[1] > 0]
    if len(all_pts) < 2:
        return (f'<div class="empty">（{title}：点太少，先跑 <code>elench export … --push'
                f'</code> 再刷新）</div>')
    xs = [math.log10(p[0]) for p in all_pts]
    ys = [math.log10(p[1]) for p in all_pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if x1 - x0 < 1e-9:
        x1 = x0 + 1
    if y1 - y0 < 1e-9:
        y1 = y0 + 1
    L, R, T, B = 70, 20, 34, 44
    pw, ph = w - L - R, h - T - B

    def sx(v):
        return L + (math.log10(v) - x0) / (x1 - x0) * pw

    def sy(v):
        return T + ph - (math.log10(v) - y0) / (y1 - y0) * ph

    out = [f'<svg viewBox="0 0 {w} {h}" class="chart">',
           f'<rect x="{L}" y="{T}" width="{pw}" height="{ph}" fill="#fafafa" stroke="#ddd"/>',
           f'<text x="{w/2}" y="20" text-anchor="middle" font-size="14" font-weight="600">'
           f'{title}（双对数）</text>']
    for i, (key, pts) in enumerate(sorted(points_by_series.items())):
        # 先按 semantics 上色；没有该属性时**按指标名兜底**
        # （nanoseconds 就是实测，operations 是上界）——否则会全画成灰色。
        sem = key[1]
        if sem not in ORDER:
            sem = "measured" if "nanoseconds" in key[0] else "upper_bound"
        color = ORDER.get(sem, "#666")
        d = " ".join(f"{sx(n):.1f},{sy(v):.1f}" for n, v in pts if n > 0 and v > 0)
        out.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="2"/>')
        out.append(f'<text x="{L + 6}" y="{T + 16 + i * 16}" font-size="11" fill="{color}">'
                   f'■ {LABEL.get(sem, sem)}</text>')
    out.append(f'<text x="{w/2}" y="{h-10}" text-anchor="middle" font-size="11" fill="#666">'
               f'n（输入规模，对数）</text>')
    out.append('</svg>')
    return "".join(out)


def render(recs, refresh=3):
    series, spans, counts = collect(recs)
    # 按指标名分组
    by_metric = {}
    for (name, sem), pts in series.items():
        by_metric.setdefault(name, {})[(name, sem)] = pts

    charts = "".join(svg_line(v, k) for k, v in sorted(by_metric.items()))

    top = sorted(spans, key=lambda s: s["ms"], reverse=True)[:12]
    rows = "".join(f"<tr><td>{s['name']}</td><td class='num'>{s['ms']:.2f}</td>"
                   f"<td class='dim'>{s['detail']}</td></tr>" for s in top) \
        or "<tr><td colspan='3' class='dim'>（还没有 span：设 ELENCH_OTEL_TRACES=1 后跑 elench build / measure）</td></tr>"

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="{refresh}">
<title>Elench · OTel 看板</title>
<style>
 body{{font-family:'Segoe UI',system-ui,sans-serif;margin:24px;color:#1a1a1a;background:#fff}}
 h1{{font-size:18px;margin:0 0 4px}} .sub{{color:#666;font-size:12px;margin-bottom:18px}}
 .chart{{width:100%;max-width:760px;background:#fff;border:1px solid #eee;border-radius:6px;margin:8px 0}}
 table{{border-collapse:collapse;font-size:13px}} td,th{{padding:4px 10px;border-bottom:1px solid #eee;text-align:left}}
 th{{color:#666;font-weight:600;font-size:12px}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
 .dim{{color:#888}} .empty{{color:#888;font-size:13px;padding:10px}}
 code{{background:#f4f4f4;padding:1px 4px;border-radius:3px}}
 .cards{{display:flex;gap:14px;margin:6px 0 16px}}
 .card{{border:1px solid #eee;border-radius:6px;padding:8px 14px;min-width:90px}}
 .card b{{display:block;font-size:20px}} .card span{{color:#666;font-size:11px}}
</style></head><body>
<h1>Elench · OTel 看板</h1>
<div class="sub">每 {refresh} 秒自动刷新 · 数据来自本地 sink 的 NDJSON · 单位：操作数 / 纳秒 / 毫秒</div>
<div class="cards">
  <div class="card"><b>{counts.get('metrics',0)}</b><span>metrics 批次</span></div>
  <div class="card"><b>{counts.get('traces',0)}</b><span>traces 批次</span></div>
  <div class="card"><b>{counts.get('logs',0)}</b><span>logs 批次</span></div>
</div>
{charts}
<h2 style="font-size:15px;margin-top:22px">最慢的 span</h2>
<table><tr><th>span</th><th class="num">耗时(ms)</th><th>属性</th></tr>{rows}</table>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Elench 的 OTel 本机看板")
    ap.add_argument("--file", default="otel.ndjson")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--refresh", type=int, default=3)
    a = ap.parse_args()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):                               # noqa: N802
            if self.path.split("?")[0] not in ("/", "/index.html"):
                self.send_response(404)
                self.end_headers()
                return
            html = render(load(a.file), a.refresh).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"看板：http://127.0.0.1:{a.port}    数据：{os.path.abspath(a.file)}")
    print("  （每 %d 秒自动刷新；Ctrl+C 退出）" % a.refresh)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
