# -*- coding: utf-8 -*-
"""分段上界（`Cost("piece")`）：节点语义 + **S2 同源**。

## 它守的是什么

分段承诺 = "**这一段真正会跑的那个候选**的 κ"。所以它成立的前提是
**承诺用的分段与运行期用的分档表是同一次计算**（S2）。

这条测试既测节点本身（求值/渲染/序列化/代数），也测那条前提：
凡是比较 `ops ≤ κ` 的地方，**测量与承诺必须传同一份表、同一个尺寸变量**——
不这么做就会得到假破界（本轮就踩过：忘了传表，n=4096 报 364,565 vs 314,261）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from bench import measure_ops                        # noqa: E402
from cost_domain.cost_check import evaluate          # noqa: E402
from cost_domain.cost_interp import CostInterp       # noqa: E402
from cost_domain.symbolic import (Cost, fallback_count,  # noqa: E402
                                  reset_fallback_count)
from parser import parse_source                      # noqa: E402

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


def piece(var="xs"):
    a = Cost.add(Cost.mul(Cost.num(104), Cost.size(var)), Cost.num(110))
    b = Cost.add(Cost.mul(Cost.num(23), Cost.size(var)), Cost.num(220042))
    return Cost.piece(var, [(8, 2715, a), (2716, 65536, b)], a), a, b


def test_node():
    print("[1] 节点语义：选中段、越界回退、渲染、序列化")
    p, a, b = piece()
    check(evaluate(p, {"xs": 8}) == evaluate(a, {"xs": 8}), "n=8 用第一段")
    check(evaluate(p, {"xs": 4096}) == evaluate(b, {"xs": 4096}), "n=4096 用第二段")
    check(evaluate(p, {"xs": 70000}) == evaluate(a, {"xs": 70000}),
          "越界用 fallback（= 运行期的第一个候选）")
    check("≤n(xs)≤" in p.render(), "渲染里有分区", p.render()[:60])
    d = p.to_dict()
    check(d["operator"] == "piecewise" and len(d["segments"]) == 2,
          "序列化成 piecewise + 两段", str(d)[:80])


def test_invariants():
    print("[2] 构造不变量：区间必须连续、不许嵌套")
    a = Cost.num(1)
    for bad, why in (
        ([(8, 10, a), (12, 20, a)], "中间有空隙"),
        ([(8, 20, a), (10, 30, a)], "区间重叠"),
        ([(8, 10, a), (11, 20, Cost.piece("xs", [(1, 2, a)], a))], "段里套段"),
    ):
        try:
            Cost.piece("xs", bad, a)
            check(False, f"{why} → 应该被拒绝", "没有抛异常")
        except ValueError:
            check(True, f"{why} → 拒绝")


def test_algebra():
    print("[3] 代数：能保持分段就保持，保持不住就退回（并计数）")
    p, a, b = piece()
    q = Cost.add(p, Cost.num(7))
    check(q.kind == "piece", "分段 + 常数 → 仍是分段", q.kind)
    check(evaluate(q, {"xs": 8}) == evaluate(p, {"xs": 8}) + 7, "加法数值正确")

    r = Cost.mul(Cost.num(3), p)
    check(r.kind == "piece", "常数 × 分段 → 仍是分段", r.kind)
    check(evaluate(r, {"xs": 4096}) == 3 * evaluate(p, {"xs": 4096}), "乘法数值正确")

    m = Cost.max(p, Cost.num(1))
    check(m.kind == "piece", "max(分段, 常数) → 仍是分段", m.kind)

    reset_fallback_count()
    raw = Cost.mul(Cost.size("xs"), p)
    check(raw.kind != "piece", "变量 × 分段 → 不再保持分段（形状退回）", raw.kind)
    check(fallback_count() >= 1, "退回被计数了", str(fallback_count()))
    check(evaluate(raw, {"xs": 8}) == 8 * evaluate(p, {"xs": 8}),
          "形状退回但**数值仍精确**")


def test_end_to_end():
    print("[4] S2 同源：测量与承诺用同一份表，ops ≤ κ")
    cases = [("文档/入门/例子", "gap", "top", [8, 4095, 4096, 8192, 16384]),
             ("文档/样例", "count", "count", [16, 512, 4096]),
             ("文档/样例", "sum", "average", [16, 512])]
    bad = []
    for d, mod, fn, sizes in cases:
        tu = parse_source(open(os.path.join(ROOT, d, mod + ".tu"), encoding="utf-8").read(),
                          mod + ".tu").module
        eln = parse_source(open(os.path.join(ROOT, d, mod + ".eln"), encoding="utf-8").read(),
                           mod + ".eln").module
        tbl = os.path.join(ROOT, d, mod + ".dispatch.yaml")
        has = os.path.exists(tbl)
        ci = CostInterp(eln, tu, table_path=tbl if has else None)
        k = ci.cost_of(fn)
        var, _, _ = ci._scale_span()
        for n in sizes:
            r = measure_ops(eln, tu, fn, [n], table_path=tbl if has else None)
            ops = r[n] if isinstance(r, dict) else r
            v = float(evaluate(k, {var or "xs": n}))
            if ops > v:
                bad.append(f"{mod}.{fn} n={n}: ops={ops} > κ={v:.0f}")
    check(not bad, "逐档 ops ≤ κ（含表范围外）", "; ".join(bad[:3]))


def test_tightness():
    print("[5] 分区真的更紧：count 在 n=16 回到 5,594（max 时是 7,114）")
    d = os.path.join(ROOT, "文档", "样例")
    tu = parse_source(open(os.path.join(d, "count.tu"), encoding="utf-8").read(),
                      "count.tu").module
    eln = parse_source(open(os.path.join(d, "count.eln"), encoding="utf-8").read(),
                       "count.eln").module
    tbl = os.path.join(d, "count.dispatch.yaml")
    ci = CostInterp(eln, tu, table_path=tbl if os.path.exists(tbl) else None)
    k = ci.cost_of("count")
    var, _, _ = ci._scale_span()
    v = float(evaluate(k, {var or "xs": 16}))
    check(v < 6000, f"κ(n=16) = {v:,.0f} < 6,000（被 O(n²) 候选抬高前是 7,114）", str(v))


def main() -> int:
    test_node()
    test_invariants()
    test_algebra()
    test_end_to_end()
    test_tightness()
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
