#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""otel.py —— 把「静态成本上界」与「实测」导出成可画图的形式。

## 为什么要这张图

**一条线画不出对错，两条线才能。**

- **静态线**：`κ(n)` —— 编译期算出的**承诺**（换机器换输入都成立）
- **实测线**：`measured(n)` —— 解释器真跑出来的**观察**

**两条线放在同一张图上，就能看出三件事：**

| 看到什么 | 说明 |
|---|---|
| 两条线**平行**（对数坐标下） | **阶一致** → 静态模型抓住了增长规律（M2 验收③） |
| 两条线**越来越开** | 静态模型漏了某个随 n 增长的成本 |
| **实测跑到了静态上面** | **契约被打破**（这是最严重的情况） |

## 为什么不能直接画同一个 y 轴

**κ 的单位是「操作次数」，实测的单位是「纳秒」。同一根轴上是苹果和橘子。**

**所以本模块把两条线**各自归一化**（都除以自己第一个点的值）——
这样比的是**增长速度**，不是数值。**归一化之后单位就被消掉了。**

**两条线平行 ⟺ 同阶。** 这正是 M2 验收③要的东西。

## 与 OpenTelemetry 的关系

命名空间 `elench.*`。两条线用**同一个指标名下的不同属性**表达，
这样任何 OTel 面板都能把它们画在一起：

    elench.cost          （指标名）
      cost.source = static_analysis   ← 静态线
      cost.source = measurement       ← 实测线
      cost.semantics = upper_bound    ← 只有静态线有

**三条诚实属性 MUST 带**（缺一条就会被误读成"实测值"）：

    cost.source       static_analysis | measurement
    cost.unit         operations | nanoseconds
    cost.semantics    upper_bound | observation
"""

import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from symbolic import Cost

NAMESPACE = "elench"


# ---------------------------------------------------------------- 尺寸变量归一

def sizes_to(cost: Optional[Cost], var: str) -> Optional[Cost]:
    """把成本式里**所有**尺寸变量都换成 `var`。

    **为什么需要**：候选体里的成本是用**内部变量**写的——

        search_binary  → 2 + log2(n(hi))×7 + 2
        search_linear  → 1 + n(n)×4 + 2

    而 scale 段的规模变量是 `xs`。`n(hi)`、`n(n)` 都是 `n(xs)` 的单调函数
    （`n = xs.length()`，`hi = n - 1`），所以**换成同一个变量再比增长阶是成立的**，
    但**原样拿去求值会得到错的常数**（`n(hi)` 在环境里查不到，退化成 1）。

    **这是一处必须写在报告里的近似**：本函数假设候选的所有尺寸变量都与输入规模同阶。
    """
    if cost is None:
        return None
    k = cost.kind
    if k == "size":
        return Cost.size(var)
    if k in ("num", "top"):
        return cost
    return Cost(k, args=[sizes_to(a, var) for a in cost.args])


# ---------------------------------------------------------------- 数据采集

def series(impl_cost: Cost, sizes: List[int], measured: Dict[int, float],
           var: str = "xs",
           candidate_cost: Optional[Cost] = None,
           piecewise: Optional[Dict[int, float]] = None) -> List[Dict[str, Any]]:
    """把静态与实测对齐到同一组规模点上。

    **两条静态线，口径不同，必须分开**：

    | 线 | 是什么 | 和实测比有意义吗 |
    |---|---|---|
    | `static_bound` | **承诺**：`?!` 取所有候选的最大值 | **不能直接比斜率**——承诺按最坏候选给，实测是单个候选 |
    | `static_candidate` | **被测那个候选自己的 κ** | **能比**——同口径，比值该平稳 |

    **这是本轮发现的一个易错点**：拿"承诺"和"单候选实测"画一起，
    斜率必然不同，看起来像模型错了，其实是口径不同。
    """
    from cost_check import evaluate

    pts = [n for n in sizes if (measured is None or n in measured)]
    pts.sort()
    out = []
    for n in pts:
        k = evaluate(impl_cost, {var: n})
        c = evaluate(candidate_cost, {var: n}) if candidate_cost is not None else None
        m = measured.get(n) if measured else None
        out.append({
            "size": n,
            "static_bound": k,
            "static_candidate": c,
            "measured": m,
            "measured_over_bound": (m / k) if (m and k) else None,
            # 同口径比值——**这一列平稳才说明静态模型对**
            "measured_over_candidate": (m / c) if (m and c) else None,
            # **κ分段**（丙）：按表逐段最优；没有表时由 dsh 传 None，渲染退化为「候选」口径
            "static_piecewise": (piecewise or {}).get(n),
        })
    return out


# ---------------------------------------------------------------- OTel（OTLP JSON）

def to_otlp(rows: List[Dict[str, Any]], function_name: str,
            sizes: List[int], measured: Dict[int, float],
            target: Optional[Cost] = None,
            now_ns: Optional[int] = None) -> Dict[str, Any]:
    """导出成 OTLP JSON（metrics）。

    ## 两条合规约束（v0.69 修）

    1. **单位 MUST 用 UCUM**。以前写 `unit: "mixed"`——**一个指标混两种单位不合规**。
       现在拆成两个指标：`elench.cost.operations`（`{operation}`）与
       `elench.cost.nanoseconds`（`ns`）。

    2. **`timeUnixNano` MUST 是真实时间戳**。以前填 `"0"`，那不是合法时刻。
       静态上界**没有真实时刻**，所以填导出时刻，并靠 `cost.semantics=upper_bound` 区分——
       **不靠时间戳，靠属性。**

    ## 面板怎么画两条线

    两个指标同名前缀不同后缀，按 `code.function.name` + `benchmark.input_size` 对齐；
    或者用 `cost.source` 过滤同一张图上的两条序列。
    """
    now = now_ns if now_ns is not None else time.time_ns()

    bound_pts: List[Dict[str, Any]] = []
    cand_pts: List[Dict[str, Any]] = []
    meas_pts: List[Dict[str, Any]] = []
    target_pts: List[Dict[str, Any]] = []

    for r in rows:
        n = r["size"]
        common = {"code.function.name": function_name, "benchmark.input_size": n}

        bound_pts.append(_point(r["static_bound"], now, dict(
            common, **{
                "cost.source": "static_analysis",
                "cost.semantics": "upper_bound",
                "cost.scope": "contract_bound",
            })))

        if r.get("static_candidate") is not None:
            cand_pts.append(_point(r["static_candidate"], now, dict(
                common, **{
                    "cost.source": "static_analysis",
                    "cost.semantics": "upper_bound",
                    "cost.scope": "single_candidate",
                })))

        if r.get("measured") is not None:
            meas_pts.append(_point(r["measured"], now, dict(
                common, **{
                    "cost.source": "measurement",
                    "cost.semantics": "observation",
                })))

    if target is not None:
        from cost_check import evaluate
        var = "xs"
        for n in sizes:
            tv = evaluate(target, {var: n})
            if tv > 0:
                target_pts.append(_point(tv, now, {
                    "code.function.name": function_name,
                    "benchmark.input_size": n,
                    "cost.source": "declared_target",
                    "cost.semantics": "budget",
                }))

    operations = {
        "name": f"{NAMESPACE}.cost.operations",
        "description": "静态成本上界与声明目标（单位：操作次数）",
        "unit": "{operation}",
        "gauge": {"dataPoints": bound_pts + cand_pts + target_pts},
    }
    metrics = [operations]
    if meas_pts:
        metrics.append({
            "name": f"{NAMESPACE}.cost.nanoseconds",
            "description": "实测耗时（单位：纳秒；cost.semantics=observation）",
            "unit": "ns",
            "gauge": {"dataPoints": meas_pts},
        })

    return {
        "resourceMetrics": [{
            "resource": {"attributes": [
                _attr("service.name", NAMESPACE),
                _attr("telemetry.sdk.name", "dsh"),
                _attr("telemetry.sdk.language", "python"),
            ]},
            "scopeMetrics": [{
                "scope": {"name": NAMESPACE, "version": "0.69"},
                "metrics": metrics,
            }],
        }],
    }


def _attr(key: str, value: Any) -> Dict[str, Any]:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, str):
        return {"key": key, "value": {"stringValue": value}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"intValue": str(value)}}


def _point(value: float, time_unix_nano: int, attrs: Dict[str, Any]) -> Dict[str, Any]:
    """一个 gauge 数据点。`timeUnixNano` MUST 是真实时刻（OTLP 要求）。"""
    return {
        "asDouble": float(value),
        "timeUnixNano": str(int(time_unix_nano)),
        "attributes": [_attr(k, v) for k, v in attrs.items()],
    }


# ---------------------------------------------------------------- CSV（通用）

def to_csv(rows: List[Dict[str, Any]]) -> str:
    """CSV：列名用完整词，能直接丢进任何画图工具。"""
    lines = ["input_size,"
             "static_bound_operations,"
             "static_candidate_operations,"
             "measured_nanoseconds,"
             "measured_over_candidate"]
    for r in rows:
        cand = r.get("static_candidate")
        ratio = r.get("measured_over_candidate")
        lines.append(",".join([
            str(r["size"]),
            f"{r['static_bound']:.0f}",
            "" if cand is None else f"{cand:.0f}",
            f"{r['measured']:.0f}",
            "" if ratio is None else f"{ratio:.6g}",
        ]))
    return "\n".join(lines)


# ---------------------------------------------------------------- SVG（双线图）

def to_svg(rows: List[Dict[str, Any]], function_name: str,
           width: int = 760, height: int = 460) -> str:
    """画「静态 vs 实测」双线图（双对数，各自归一化）。

    **为什么归一化**：κ 是操作次数、实测是纳秒，同一根 y 轴上不可比。
    归一化之后比的是**增长速度**——**两条线平行 ⟺ 同阶**。
    """
    W, H = width, height
    L, R, T, B = 78, 24, 56, 62
    pw, ph = W - L - R, H - T - B

    has_measure = any(r.get("measured") for r in rows)
    pts = [r for r in rows if r["static_bound"] > 0
           and (r.get("measured") or not has_measure)]
    if len(pts) < 2:
        return _svg_message(W, H, "点太少，画不出趋势（至少需要 2 个规模点）")

    # 归一化：各自除以自己第一个点的值
    k0 = pts[0]["static_bound"]
    ks = [r["static_bound"] / k0 for r in pts]
    if has_measure:
        m0 = pts[0]["measured"]
        ms = [r["measured"] / m0 for r in pts]
    else:
        ms = []
    # **第三条线：被测那个候选自己的 κ**（与被测同口径，比值才该平稳）
    cs = []
    _key = "static_piecewise" if any(r.get("static_piecewise") for r in pts) else "static_candidate"
    if any(r.get(_key) for r in pts):
        c0 = next((r[_key] for r in pts if r.get(_key)), None)
        if c0:
            cs = [(r[_key] / c0) if r.get(_key) else None for r in pts]
    xs = [r["size"] for r in pts]

    # 双对数坐标
    lx = [math.log10(v) for v in xs]
    ly_all = ([math.log10(v) for v in ks]
              + [math.log10(v) for v in ms]
              + [math.log10(v) for v in cs if v])
    x0, x1 = min(lx), max(lx)
    y0, y1 = min(ly_all), max(ly_all)
    if x1 - x0 < 1e-9:
        x1 = x0 + 1
    if y1 - y0 < 1e-9:
        y1 = y0 + 1
    y0 -= 0.15
    y1 += 0.15

    def sx(v: float) -> float:
        return L + (math.log10(v) - x0) / (x1 - x0) * pw

    def sy(v: float) -> float:
        return T + ph - (math.log10(v) - y0) / (y1 - y0) * ph

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="Segoe UI, sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        f'<text x="{W // 2}" y="28" text-anchor="middle" font-size="16" '
        f'font-weight="600" fill="#1a1a1a">'
        f'{_esc(function_name)}：静态上界 vs 实测（各自归一化，双对数）</text>',
        f'<text x="{W // 2}" y="46" text-anchor="middle" font-size="11" '
        f'fill="#666">'
        + ("虚线=承诺(最劣候选)、点线=κ分段(逐段最优)、实线=实测；"
           "同口径看**点线 vs 实线**是否平行"
           if has_measure else
           "该函数没有 benchmodel，只能画静态线（没有实测）")
        + '</text>',
        f'<rect x="{L}" y="{T}" width="{pw}" height="{ph}" fill="#fafafa" '
        f'stroke="#cccccc"/>',
    ]

    # 网格与 y 刻度（1x, 2x, 4x, 8x …）
    for k in range(-2, 5):
        v = 2 ** k
        if y0 <= math.log10(v) <= y1:
            y = sy(v)
            out.append(f'<line x1="{L}" y1="{y:.1f}" x2="{L + pw}" y2="{y:.1f}" '
                       f'stroke="#eeeeee"/>')
            out.append(f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end" '
                       f'font-size="10" fill="#888">{v}x</text>')

    # x 刻度
    for r in pts:
        x = sx(r["size"])
        out.append(f'<line x1="{x:.1f}" y1="{T}" x2="{x:.1f}" y2="{T + ph}" '
                   f'stroke="#f2f2f2"/>')
        out.append(f'<text x="{x:.1f}" y="{T + ph + 16}" text-anchor="middle" '
                   f'font-size="9" fill="#888">{r["size"]:,}</text>')

    # 两条线
    if cs:
        out.append(_polyline([(sx(x), sy(v)) for x, v in zip(xs, ks)], "#1f77b4",
                             dash="2 3"))       # 承诺=虚线：最劣候选，数学上就该"高一截"
    else:
        out.append(_polyline([(sx(x), sy(v)) for x, v in zip(xs, ks)], "#1f77b4"))
    for x, v in zip(xs, ks):
        out.append(f'<circle cx="{sx(x):.1f}" cy="{sy(v):.1f}" r="3" fill="#1f77b4"/>')
    if has_measure:
        out.append(_polyline([(sx(x), sy(v)) for x, v in zip(xs, ms)], "#d62728"))
    # 第三条：被测候选自己的 κ（同口径，与被测比斜率才有意义）
    if cs:
        pts_c = [(sx(x), sy(v)) for x, v in zip(xs, cs) if v]
        out.append(_polyline(pts_c, "#ff9d1f", dash="6 4"))
        for x, v in zip(xs, ms):
            out.append(f'<circle cx="{sx(x):.1f}" cy="{sy(v):.1f}" r="3" fill="#d62728"/>')

    # 轴标签
    out.append(f'<text x="{L + pw // 2}" y="{H - 18}" text-anchor="middle" '
               f'font-size="11" fill="#444">输入规模 n（对数）</text>')
    out.append(f'<text x="16" y="{T + ph // 2}" text-anchor="middle" font-size="11" '
               f'fill="#444" transform="rotate(-90 16 {T + ph // 2})">'
               f'相对成本（各自除以第一个点，对数）</text>')

    # 图例
    out.append(f'<rect x="{L + pw - 178}" y="{T + 10}" width="168" height="64" '
               f'fill="#ffffff" stroke="#dddddd" rx="4"/>')
    out.append(f'<line x1="{L + pw - 156}" y1="{T + 26}" x2="{L + pw - 132}" '
               f'y2="{T + 26}" stroke="#1f77b4" stroke-width="2"/>')
    out.append(f'<text x="{L + pw - 126}" y="{T + 30}" font-size="10" fill="#333">'
               f'静态上界 κ（操作数）</text>')
    out.append(f'<line x1="{L + pw - 156}" y1="{T + 44}" x2="{L + pw - 132}" '
               f'y2="{T + 44}" stroke="#d62728" stroke-width="2"/>')
    out.append(f'<text x="{L + pw - 126}" y="{T + 48}" font-size="10" fill="#333">'
               f'实测（纳秒）</text>')
    # 第三条：被测候选自己的 κ（同口径）
    out.append(f'<line x1="{L + pw - 156}" y1="{T + 62}" x2="{L + pw - 132}" '
               f'y2="{T + 62}" stroke="#ff9d1f" stroke-width="2" '
               f'stroke-dasharray="6 4"/>')
    out.append(f'<text x="{L + pw - 126}" y="{T + 66}" font-size="10" fill="#333">'
               f'κ分段（逐段最优）</text>')

    out.append("</svg>")
    return "\n".join(out)


def _polyline(pts: List[Tuple[float, float]], color: str, dash: str = "") -> str:
    d = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="2"/>'


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _svg_message(W: int, H: int, msg: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}">'
            f'<rect width="{W}" height="{H}" fill="#fff"/>'
            f'<text x="{W // 2}" y="{H // 2}" text-anchor="middle" font-size="13" '
            f'fill="#666">{_esc(msg)}</text></svg>')


# ---------------------------------------------------------------- 人读报告

def report(rows: List[Dict[str, Any]], function_name: str) -> str:
    out = [f"module {function_name}：静态上界 vs 实测", ""]
    out.append(f"  {'规模 n':>10}  {'κ承诺':>12}  {'κ分段':>12}  {'实测 ns':>12}  "
               f"{'实测/κ分段':>13}")
    out.append("  " + "-" * 66)
    for r in rows:
        cand = r.get("static_piecewise") or r["static_candidate"]
        ratio = r["measured_over_candidate"]
        out.append(f"  {r['size']:>10,}  {r['static_bound']:>12,.0f}  "
                   + (f"{cand:>12,.0f}" if cand is not None else f"{'—':>12}")
                   + f"  {r['measured']:>12,.0f}  "
                   + (f"{ratio:>13.3e}" if ratio is not None else f"{'—':>13}"))
    out.append("")
    out.append("  **怎么读这张表**：")
    out.append("  - 两个数的**单位不同**（操作数 vs 纳秒），所以**不能直接比大小**")
    out.append("  - 要看的是「实测/κ」这一列**是否平稳**：")
    out.append("      平稳   → 静态模型抓住了增长规律（阶一致）")
    out.append("      递减   → 静态模型高估了（保守，不违反契约）")
    out.append("      递增   → **静态模型漏了某项随 n 增长的成本**")
    out.append("  - **实测绝对值超过 κ 不代表违约**——单位不同；违约只在"
               "「同一单位下」才谈得上")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    from parser import parse_source
    from cost_interp import CostInterp
    from bench import measure

    sys.stdout.reconfigure(encoding="utf-8")
    HERE = os.path.dirname(os.path.abspath(__file__))
    S = os.path.join(HERE, "..", "文档", "设计", "目前设计", "样例")
    tu = parse_source(open(os.path.join(S, "search.tu"), encoding="utf-8").read(), "t").module
    ch = parse_source(open(os.path.join(S, "search.eln"), encoding="utf-8").read(), "c").module
    ci = CostInterp(ch, tu)
    sizes = [1, 2, 4, 8, 16, 32, 64, 128, 256]
    m = measure(ch, tu, "binary_search", sizes,
                force={"search_impl": "search_binary"})
    rows = series(ci.cost_of("binary_search"), sizes, m)
    print(report(rows, "binary_search"))
