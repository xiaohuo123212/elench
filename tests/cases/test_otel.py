#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_otel.py —— OTLP 导出与双线图数据（`dsh export`）

**范围**：只校验**本地结构**（字段名、单位、必填属性、数值）。
**不联网、不对照官方 proto**——所以本文件**不主张"完全符合 OTLP 规范"**，
只主张"符合本文件列出的那几条约束"。
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
import sys

sys.stdout.reconfigure(encoding="utf-8")

from otel import NAMESPACE, series, sizes_to, to_csv, to_otlp, to_svg
from parser import parse_source
from symbolic import Cost

SAMPLE = os.path.join(HERE, "..", "文档", "样例")

_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def sample_rows(unit="nanoseconds"):
    """合成一组数据。**单位要显式说**：

    - `nanoseconds`（默认）：老口径，100..310 ns，和图上的κ不同单位；
    - `operations`：实测必须压在 κ 下面（κ = 4n+9 → 13/17/25/41）。
    """
    n = Cost.size("xs")
    impl = Cost.add(Cost.mul(Cost.num(4), n), Cost.num(9))
    cand = Cost.add(Cost.log2(n), Cost.num(2))
    sizes = [1, 2, 4, 8]
    if unit == "operations":
        measured = {1: 5, 2: 9, 4: 17, 8: 33}
    else:
        measured = {1: 100.0, 2: 150.0, 4: 220.0, 8: 310.0}
    return (series(impl, sizes, measured, "xs",
                   candidate_cost=cand, unit=unit),
            sizes, measured, impl)


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

    # **默认（操作数）模式**：实测与静态线同单位 → 同一个指标里靠属性区分
    rows_ops, sizes_ops, meas_ops, _ = sample_rows("operations")
    d2 = to_otlp(rows_ops, "f", sizes_ops, meas_ops, None, now_ns=1)
    m2 = d2["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    check(len(m2) == 1 and m2[0]["unit"] == "{operation}",
          "操作数模式只有一个 {operation} 指标", str([m["name"] for m in m2]))
    a2 = [{x["key"]: list(x["value"].values())[0] for x in p["attributes"]}
          for p in m2[0]["gauge"]["dataPoints"]]
    check(any(a["cost.source"] == "measurement" for a in a2),
          "操作数模式：实测点与静态线同指标")
    check(any(a["cost.source"] == "measurement" and a["cost.semantics"] == "observation"
              for a in a2), "操作数模式的实测点仍标 observation")


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
    check("承诺" in svg and ("实测" in svg or "保守界" in svg), "图例含两条线")
    check("归一化" in svg, "标题写明归一化（否则会误读成同一单位）")

    # 没有实测点 → 只画静态线，且副标题换掉
    only_static = series(impl, sizes, None, "xs")
    svg2 = to_svg(only_static, "f")

    # **XML 合法性**：标签必须成对——图例按有无实测切换时最容易漏开标签
    import xml.etree.ElementTree as _ET
    for _name, _doc in (("有实测", svg), ("无实测", svg2)):
        try:
            _ET.fromstring(_doc)
            check(True, f"{_name} SVG 是合法 XML")
        except Exception as _e:                    # noqa: BLE001
            check(False, f"{_name} SVG 是合法 XML", str(_e))
    check("未实测" in svg2, "无实测时副标题说明原因")

    csv = to_csv(rows)
    head = csv.split("\n")[0]
    check(head.startswith("input_size,"), "CSV 表头", head)
    check("static_candidate_operations" in head, "CSV 列名用完整词", head)
    check("measured_nanoseconds" in head, "--timing 的实测列仍是纳秒", head)
    check(len(csv.split("\n")) == len(sizes) + 1, "CSV 行数 = 点数 + 表头")


# ---------------------------------------------------------------- 默认口径（操作数）

def test_ops_mode():
    print("[7] 默认口径：操作数，**不归一化**")
    rows = sample_rows("operations")[0]
    svg = to_svg(rows, "f")
    check("操作数（对数）" in svg, "y 轴写『操作数（对数）』")
    check("归一化" not in svg, "默认**不做**归一化（图里不出现『归一化』）",
          "still normalized")
    check("承诺 vs 实测（操作数，对数；κ 必须在上方）" in svg,
          "标题写明操作数与『κ 必须在上方』")
    check("实测线在承诺线下方才合法" in svg, "图注写明判据", svg[svg.find("y=\"46\""):][:160])

    head = to_csv(rows).split("\n")[0]
    check("measured_operations" in head, "CSV 列名 measured_operations", head)
    check("measured_nanoseconds" not in head, "默认不出现纳秒列", head)

    # `--timing`（纳秒）仍是老行为：归一化 + 相对成本
    rows_t = sample_rows("nanoseconds")[0]
    svg_t = to_svg(rows_t, "f")
    check("相对成本（各自除以第一个点，对数）" in svg_t,
          "--timing 的 y 轴仍是相对成本")
    check("归一化" in svg_t, "--timing 仍归一化")
    check("measured_nanoseconds" in to_csv(rows_t).split("\n")[0],
          "--timing 的 CSV 列名仍是纳秒")


def test_bound_ops():
    """**默认口径的判据**：每个规模点 `κ ≥ 实测`。任一违反 → 失败并打印该点。

    用 `gap`：它带分档表（`by_scan_heavy` / `by_scan_setup`），
    实测走表（不 force），与承诺线同源。
    """
    print("[8] 默认（操作数）：逐点断言 κ ≥ 实测（gap）")
    from bench import measure_ops
    from cost_interp import CostInterp

    ex = os.path.join(HERE, "..", "文档", "入门", "例子")
    tu_p = os.path.join(ex, "gap.tu")
    ch_p = os.path.join(ex, "gap.eln")
    tbl = os.path.join(ex, "gap.dispatch.yaml")
    tbl = tbl if os.path.exists(tbl) else None
    tu = parse_source(open(tu_p, encoding="utf-8").read(), "gap.tu").module
    ch = parse_source(open(ch_p, encoding="utf-8").read(), "gap.eln").module
    ci = CostInterp(ch, tu, table_path=tbl)

    sizes = [8, 64, 512, 4096]
    m = measure_ops(ch, tu, "top", sizes, table_path=tbl)
    rows = series(ci.cost_of("top"), sizes, m, "xs")
    check(len(rows) == len(sizes), "每个规模点都有一行", str(len(rows)))
    check(all(r["unit"] == "operations" for r in rows), "单位字段是 operations")
    bad = []
    for r in rows:
        n, k, got = r["size"], r["static_bound"], r["measured"]
        if got is None:
            continue
        if not (k >= got):
            bad.append((n, k, got))
        check(k >= got, f"n={n}: κ ≥ 实测",
              f"**破界** n={n}: κ={k:,.0f} < 实测={got:,.0f}")
    check(not bad, "没有任何规模点破界", str(bad))


# ---------------------------------------------------------------- 真样例

def test_real_sample():
    print("[9] 真样例：search 的 OTLP（纳秒两个指标 / 操作数一个指标）")
    from bench import measure, measure_ops
    from cost_interp import CostInterp

    tu = parse_source(open(os.path.join(SAMPLE, "search.tu"), encoding="utf-8").read(),
                      "t.tu").module
    ch = parse_source(open(os.path.join(SAMPLE, "search.eln"), encoding="utf-8").read(),
                      "t.eln").module
    ci = CostInterp(ch, tu)
    sizes = [1, 2, 4, 8]
    m = measure(ch, tu, "binary_search", sizes, force={"search_impl": "search_binary"})
    # **显式声明单位**：这组数是纳秒（--timing 口径），不能默认成操作数
    rows = series(ci.cost_of("binary_search"), sizes, m, "xs", unit="nanoseconds")
    d = to_otlp(rows, "binary_search", sizes, m, ci.target_cost("binary_search"),
                now_ns=1)
    metrics = d["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    check(len(metrics) == 2, "两个指标（operations + nanoseconds）", str(len(metrics)))
    ops = [p for p in metrics[0]["gauge"]["dataPoints"]]
    check(len(ops) >= len(sizes), "operations 至少每个规模一个点", str(len(ops)))
    # 输出的 JSON 必须可序列化
    s = json.dumps(d, ensure_ascii=False)
    check(len(s) > 500, "整个结构能 JSON 序列化")

    # **默认（操作数）**：单位相同 → 只出一个指标
    m_ops = measure_ops(ch, tu, "binary_search", sizes,
                        force={"search_impl": "search_binary"})
    rows_ops = series(ci.cost_of("binary_search"), sizes, m_ops, "xs")
    d_ops = to_otlp(rows_ops, "binary_search", sizes, m_ops,
                    ci.target_cost("binary_search"), now_ns=1)
    metrics_ops = d_ops["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    check(len(metrics_ops) == 1, "操作数模式只有一个指标（同单位不拆）",
          str([x["name"] for x in metrics_ops]))
    check(all(r["unit"] == "operations" for r in rows_ops), "rows 标了 operations")
    bad = [(r["size"], r["static_bound"], r["measured"]) for r in rows_ops
           if r["measured"] is not None and r["static_bound"] < r["measured"]]
    check(not bad, "search 每个规模点 κ ≥ 实测", str(bad))


def main():
    test_namespace()
    test_units()
    test_timestamp()
    test_honesty_attrs()
    test_sizes_to()
    test_svg_csv()
    test_ops_mode()
    test_bound_ops()
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
