#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_otel.py —— OTLP 导出与双线图数据（`dsh export`）

**范围**：只校验**本地结构**（字段名、单位、必填属性、数值）。
**不联网、不对照官方 proto**——所以本文件**不主张"完全符合 OTLP 规范"**，
只主张"符合本文件列出的那几条约束"。
"""

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from otel import NAMESPACE, series, sizes_to, to_csv, to_otlp, to_svg
from parser import parse_source
from symbolic import Cost

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "..", "文档", "设计", "目前设计", "样例")

_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def sample_rows():
    n = Cost.size("xs")
    impl = Cost.add(Cost.mul(Cost.num(4), n), Cost.num(9))
    cand = Cost.add(Cost.log2(n), Cost.num(2))
    sizes = [1, 2, 4, 8]
    measured = {1: 100.0, 2: 150.0, 4: 220.0, 8: 310.0}
    return series(impl, sizes, measured, "xs", candidate_cost=cand), sizes, measured, impl


# ---------------------------------------------------------------- 命名空间

def test_namespace():
    print("[1] 命名空间是 `elench.*`（不是 csharness）")
    check(NAMESPACE == "elench", "NAMESPACE == elench", NAMESPACE)
    rows, sizes, measured, impl = sample_rows()
    d = to_otlp(rows, "f", sizes, measured, None, now_ns=1_700_000_000_000_000_000)
    names = [m["name"] for m in d["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]]
    check(all(x.startswith("elench.") for x in names),
          "所有指标名以 elench. 开头", str(names))


# ---------------------------------------------------------------- C-3 单位

def test_units():
    print("[2] 单位 MUST 用 UCUM，且一个指标只有一种单位")
    rows, sizes, measured, impl = sample_rows()
    d = to_otlp(rows, "f", sizes, measured, None, now_ns=1)
    metrics = d["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    by_name = {m["name"]: m for m in metrics}

    check("elench.cost.operations" in by_name, "有 operations 指标", str(list(by_name)))
    check("elench.cost.nanoseconds" in by_name, "有 nanoseconds 指标", str(list(by_name)))
    check(by_name["elench.cost.operations"]["unit"] == "{operation}",
          "operations 的单位是 {operation}", by_name["elench.cost.operations"]["unit"])
    check(by_name["elench.cost.nanoseconds"]["unit"] == "ns",
          "nanoseconds 的单位是 ns", by_name["elench.cost.nanoseconds"]["unit"])
    check(all(m["unit"] != "mixed" for m in metrics), "不再出现 `mixed` 单位")


# ---------------------------------------------------------------- C-3 时间戳

def test_timestamp():
    print("[3] timeUnixNano MUST 是真实时间戳（不是 \"0\"）")
    rows, sizes, measured, impl = sample_rows()
    d = to_otlp(rows, "f", sizes, measured, None, now_ns=1_700_000_000_123_456_789)
    metrics = d["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    pts = [p for m in metrics for p in m["gauge"]["dataPoints"]]
    check(len(pts) > 0, "有数据点")
    check(all(p["timeUnixNano"] != "0" for p in pts), "没有 \"0\" 时间戳")
    check(all(p["timeUnixNano"] == "1700000000123456789" for p in pts),
          "时间戳等于传入的时刻")
    check(all(isinstance(p["timeUnixNano"], str) for p in pts),
          "OTLP JSON 里 int64 用字符串表示")


# ---------------------------------------------------------------- 诚实属性

def test_honesty_attrs():
    print("[4] 三条诚实属性 MUST 齐全")
    rows, sizes, measured, impl = sample_rows()
    d = to_otlp(rows, "f", sizes, measured, None, now_ns=1)
    metrics = d["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]

    def attrs_of(pt):
        return {a["key"]: list(a["value"].values())[0] for a in pt["attributes"]}

    all_attrs = []
    for m in metrics:
        for p in m["gauge"]["dataPoints"]:
            all_attrs.append(attrs_of(p))

    for a in all_attrs:
        check("cost.source" in a, "每个点都有 cost.source", str(a.keys()))
        check("cost.semantics" in a, "每个点都有 cost.semantics", str(a.keys()))
    check(any(a["cost.source"] == "static_analysis" for a in all_attrs), "有静态来源点")
    check(any(a["cost.source"] == "measurement" for a in all_attrs), "有实测来源点")
    check(any(a["cost.semantics"] == "upper_bound" for a in all_attrs), "静态点标 upper_bound")
    check(any(a["cost.semantics"] == "observation" for a in all_attrs), "实测点标 observation")
    # 静态点与实测点分开在不同的指标里（单位不同，不能混）
    ops = [a for m in metrics if m["unit"] == "{operation}"
           for p in m["gauge"]["dataPoints"] for a in [attrs_of(p)]]
    ns = [a for m in metrics if m["unit"] == "ns"
          for p in m["gauge"]["dataPoints"] for a in [attrs_of(p)]]
    check(all(a["cost.source"] != "measurement" for a in ops),
          "operations 指标里没有实测点（单位不同不混放）")
    check(all(a["cost.source"] == "measurement" for a in ns),
          "nanoseconds 指标里只有实测点")


# ---------------------------------------------------------------- 尺寸归一

def test_sizes_to():
    print("[5] 尺寸变量归一（MB-8 的落地方式）")
    n = Cost.size("hi")
    c = Cost.add(Cost.log2(n), Cost.num(2))
    r = sizes_to(c, "xs")
    check("n(xs)" in r.render() and "hi" not in r.render(),
          "n(hi) 归一成 n(xs)", r.render())
    check(sizes_to(None, "xs") is None, "None 进 None 出")


# ---------------------------------------------------------------- SVG / CSV

def test_svg_csv():
    print("[6] SVG 与 CSV")
    rows, sizes, measured, impl = sample_rows()
    svg = to_svg(rows, "f")
    check(svg.startswith("<svg"), "SVG 以 <svg 开头")
    check("静态上界" in svg and "实测" in svg, "图例含两条线")
    check("归一化" in svg, "标题写明归一化（否则会误读成同一单位）")

    # 没有实测点 → 只画静态线，且副标题换掉
    only_static = series(impl, sizes, None, "xs")
    svg2 = to_svg(only_static, "f")
    check("没有 benchmodel" in svg2, "无实测时副标题说明原因")

    csv = to_csv(rows)
    head = csv.split("\n")[0]
    check(head.startswith("input_size,"), "CSV 表头", head)
    check("static_candidate_operations" in head, "CSV 列名用完整词", head)
    check(len(csv.split("\n")) == len(sizes) + 1, "CSV 行数 = 点数 + 表头")


# ---------------------------------------------------------------- 真样例

def test_real_sample():
    print("[7] 真样例：search 的 OTLP 能出两个指标")
    from bench import measure
    from cost_interp import CostInterp

    tu = parse_source(open(os.path.join(SAMPLE, "search.tu"), encoding="utf-8").read(),
                      "t.tu").module
    ch = parse_source(open(os.path.join(SAMPLE, "search.eln"), encoding="utf-8").read(),
                      "t.eln").module
    ci = CostInterp(ch, tu)
    sizes = [1, 2, 4, 8]
    m = measure(ch, tu, "binary_search", sizes, force={"search_impl": "search_binary"})
    rows = series(ci.cost_of("binary_search"), sizes, m, "xs")
    d = to_otlp(rows, "binary_search", sizes, m, ci.target_cost("binary_search"),
                now_ns=1)
    metrics = d["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    check(len(metrics) == 2, "两个指标（operations + nanoseconds）", str(len(metrics)))
    ops = [p for p in metrics[0]["gauge"]["dataPoints"]]
    check(len(ops) >= len(sizes), "operations 至少每个规模一个点", str(len(ops)))
    # 输出的 JSON 必须可序列化
    s = json.dumps(d, ensure_ascii=False)
    check(len(s) > 500, "整个结构能 JSON 序列化")


def main():
    test_namespace()
    test_units()
    test_timestamp()
    test_honesty_attrs()
    test_sizes_to()
    test_svg_csv()
    test_real_sample()

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
