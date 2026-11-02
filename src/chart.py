#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""chart.py —— 极简折线图（纯 SVG，无依赖）。

**为什么自己写**：`otel.to_svg` 是"承诺 vs 实测"那张固定双线图的渲染器，
它假设了 `rows` 的字段结构。紧度图要画的是**比值**（y 轴线性、两条线），
复用会逼着我去迁就它的数据结构；画图这件事本来就只有 80 行。

约定：
- x 用对数刻度（规模天然是 2 的幂）；
- y 可选线性（比值）或对数（操作数）；
- 每个系列一条折线 + 图例，**不叠在一张图上讲两件事**——调用方负责拆图。
"""
from __future__ import annotations

import math
from typing import List, Sequence, Tuple

W, H = 820, 480
L, R, T, B = 92, 210, 58, 64
PW, PH = W - L - R, H - T - B

COLORS = ["#1f5fa8", "#c0392b", "#2e7d32", "#8e44ad", "#d35400"]


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def line_chart(series: Sequence[Tuple[str, Sequence[Tuple[float, float]]]],
               title: str, ylabel: str, log_x: bool = True,
               log_y: bool = False, hline: float = None,
               annotate: bool = False, vline: float = None) -> str:
    """series = [(名字, [(x, y), ...]), ...] → SVG 文本。"""
    pts = [(x, y) for _n, xs in series for x, y in xs if y == y and y > 0]
    if not pts:
        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"/>'
    xs_all = [p[0] for p in pts]
    ys_all = [p[1] for p in pts]
    x0, x1 = min(xs_all), max(xs_all)
    if x0 == x1:
        x0, x1 = x0 / 2, x1 * 2
    y0, y1 = min(ys_all), max(ys_all)
    if hline is not None and hline > 0:
        y0, y1 = min(y0, hline), max(y1, hline)
    y0 = y0 * 0.9 if y0 > 0 else 0
    y1 = y1 * 1.1

    def sx(x):
        if log_x and x > 0:
            return L + PW * (math.log(x / x0) / math.log(x1 / x0))
        return L + PW * ((x - x0) / (x1 - x0) if x1 > x0 else 0.5)

    def sy(y):
        if log_y:
            a, b = math.log(y0), math.log(y1)
            return T + PH * (1 - (math.log(y) - a) / (b - a if b > a else 1))
        return T + PH * (1 - (y - y0) / (y1 - y0 if y1 > y0 else 1))

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'font-family="system-ui,Segoe UI,sans-serif" font-size="13">',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
           f'<text x="{L}" y="30" font-size="17" font-weight="600" fill="#1a1a1a">'
           f'{_esc(title)}</text>',
           f'<text x="18" y="{T + PH / 2}" fill="#444" transform="rotate(-90 18 '
           f'{T + PH / 2})" text-anchor="middle">{_esc(ylabel)}</text>',
           f'<rect x="{L}" y="{T}" width="{PW}" height="{PH}" fill="none" '
           f'stroke="#bbb"/>']

    # x 轴刻度（点很多时抽稀标签，避免叠字；折线仍然逐点画）
    _xs_sorted = sorted(set(xs_all))
    _step = max(1, len(_xs_sorted) // 8) if len(_xs_sorted) > 10 else 1
    for _i, x in enumerate(_xs_sorted):
        px = sx(x)
        out.append(f'<line x1="{px:.1f}" y1="{T}" x2="{px:.1f}" y2="{T + PH}" '
                   f'stroke="#eee"/>')
        if _i % _step == 0 or _i == len(_xs_sorted) - 1:
            out.append(f'<text x="{px:.1f}" y="{T + PH + 20}" text-anchor="middle" '
                       f'fill="#555">{x:g}</text>')
    # y 轴刻度：对数轴用 1/10/100/… 这样的整十点；线性轴平均分 4 段
    if log_y:
        _ticks = []
        p = 1.0
        while p < y1 * 10:
            if y0 * 0.9 <= p <= y1 * 1.1:
                _ticks.append(p)
            p *= 10
        yticks = _ticks or [y0, y1]
    else:
        yticks = [y0 + (y1 - y0) * i / 4 for i in range(5)]
    for y in yticks:
        py = sy(y)
        out.append(f'<line x1="{L}" y1="{py:.1f}" x2="{L + PW}" y2="{py:.1f}" '
                   f'stroke="#f3f3f3"/>')
        _lab = f"{y:.0f}" if y >= 1 else f"{y:.2f}"
        out.append(f'<text x="{L - 10}" y="{py + 4:.1f}" text-anchor="end" '
                   f'fill="#555">{_lab}</text>')
    # 竖线（例如"常数项不再主导"的 n0）
    if vline is not None and vline > 0:
        _vx = sx(vline)
        out.append(f'<line x1="{_vx:.1f}" y1="{T}" x2="{_vx:.1f}" y2="{T + PH}" '
                   f'stroke="#999" stroke-dasharray="6 5"/>')
        out.append(f'<text x="{_vx + 4:.1f}" y="{T + 14}" fill="#777">n0={vline:.0f}</text>')

    # 参照线（例如比值 = 1）
    if hline is not None and hline > 0:
        py = sy(hline)
        out.append(f'<line x1="{L}" y1="{py:.1f}" x2="{L + PW}" y2="{py:.1f}" '
                   f'stroke="#999" stroke-dasharray="6 5"/>')
        out.append(f'<text x="{L + PW - 6}" y="{py - 6:.1f}" text-anchor="end" '
                   f'fill="#777">{hline:g}</text>')

    _seen = set()
    _dup = []
    for i, (name, data) in enumerate(series):
        _sig = tuple((round(x, 6), round(y, 6)) for x, y in data if y == y)
        if _sig in _seen:
            _dup.append(name)
            continue
        _seen.add(_sig)
        color = COLORS[i % len(COLORS)]
        d = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in data if y == y and y > 0)
        if not d:
            continue
        out.append(f'<polyline points="{d}" fill="none" stroke="{color}" '
                   f'stroke-width="2.4"/>')
        for x, y in data:
            if y == y and y > 0:
                out.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3" '
                           f'fill="{color}"/>')
                if annotate:
                    out.append(f'<text x="{sx(x) + 6:.1f}" y="{sy(y) - 6:.1f}" '
                               f'font-size="11" fill="{color}">{y:.2f}</text>')
        ly = T + 18 + i * 24
        out.append(f'<line x1="{L + PW + 18}" y1="{ly}" x2="{L + PW + 52}" '
                   f'y2="{ly}" stroke="{color}" stroke-width="2.4"/>')
        out.append(f'<text x="{L + PW + 60}" y="{ly + 5}" fill="#333">'
                   f'{_esc(name)}</text>')

    if _dup:
        out.append(f'<text x="{L + PW + 60}" y="{T + 18 + len(series) * 24}" '
                   f'fill="#777">（{_esc("、".join(_dup))}与上线重合）</text>')
    out.append(f'<text x="{L}" y="{H - 18}" fill="#777">x 轴：输入规模'
               f'　y 轴：{_esc(ylabel)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def stack_svgs(svgs, titles=None) -> str:
    """把多张图**垂直堆进同一个文件**（每张仍是独立坐标系）。

    SVG 允许嵌套 `<svg>`，所以不需要把各图的坐标系揉在一起——
    「一个文件」和「一张图」是两件事：这个函数只合并文件，不合并语义。
    """
    if not svgs:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
    import re as _re
    total_h = 0
    parts = []
    for svg in svgs:
        body = _re.sub(r"^<\?xml[^>]*\?>\s*", "", svg)
        m = _re.search(r'width="(\d+)" height="(\d+)"', body)
        w, h = (int(m.group(1)), int(m.group(2))) if m else (W, H)
        body = body.replace('<svg xmlns="http://www.w3.org/2000/svg"', '<svg', 1)
        parts.append((w, h, body))
        total_h += h
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg xmlns="http://www.w3.org/2000/svg" width="{parts[0][0]}" '
           f'height="{total_h}">',
           f'<rect width="{parts[0][0]}" height="{total_h}" fill="#fff"/>']
    y = 0
    for w, h, body in parts:
        out.append(f'<g transform="translate(0,{y})">{body}</g>')
        y += h
    out.append("</svg>")
    return "\n".join(out)
