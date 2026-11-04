"""exp_metric.py —— 画出 κ/target 的分布，并给出三档阈值下的敏感性。

为什么先画分布再谈阈值：21 个函数压成一个二值判断会丢掉全部信息。
阈值只在**需要一个二值指标做组间对比**时才用（例如 E1 的四组）。
"""
import json
import os
import sys

sys.path.insert(0, "src")

from parser import parse_source                      # noqa: E402
from cost_interp import CostInterp                   # noqa: E402
import cost_explain as CE                            # noqa: E402
from contract_metric import (DEFAULT_FLOOR, is_vacuous,  # noqa: E402
                             sensitivity)
from cost_check import compare  # noqa: E402

DIRS = ("文档/设计/目前设计/样例", "文档/入门/例子", "demo/repair/v1", "demo/repair/v2")


def collect():
    rows, seen = [], set()
    for d in DIRS:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".tu"):
                continue
            base = os.path.join(d, f[:-3])
            try:
                tu = parse_source(open(base + ".tu", encoding="utf-8").read(), "a.tu").module
                eln = parse_source(open(base + ".eln", encoding="utf-8").read(), "a.eln").module
                ci = CostInterp(eln, tu)
            except Exception:
                continue
            for name in sorted(ci.fns):
                key = (os.path.basename(base), name)
                if key in seen:
                    continue
                tgt, cost = ci.target_cost(name, "time"), ci.cost_of(name)
                if tgt is None or cost is None or cost.is_top():
                    continue
                var = (cost.variables() or ["n"])[0]
                _info = CE.threshold_of(cost, tgt, var)
                n0, ratio = _info["n0"], _info["ratio"]
                seen.add(key)
                verdict = compare(cost, tgt).kind
                rows.append({"module": key[0], "function": name, "n0": n0,
                             "ratio": ratio, "verdict": verdict})
    return rows


def bar(ratio, lo=-5.0, hi=3.0, width=44):
    """把 log10(ratio) 映射成一条横条。"""
    import math
    if not ratio or ratio <= 0:
        return ""
    x = math.log10(ratio)
    t = (x - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    return "█" * max(1, int(round(t * width)))


rows = collect()
rows.sort(key=lambda r: (r["ratio"] if r["ratio"] else 0))

print("κ/target 分布（log10 横轴；▏=1e-5  ▏=1e-3  ▏=1  虚线附近=契约开始失去分辨力）")
print(f"{'模块':<11}{'函数':<15}{'n0':>7}{'κ/target':>12}  分布")
print("-" * 84)
for r in rows:
    rat = r["ratio"]
    txt = f"{rat:.3g}" if rat is not None else "—"
    print(f"{r['module']:<11}{r['function']:<15}{str(r['n0']):>7}{txt:>12}  {bar(rat)}")

print("\n三档阈值敏感性（换阈值，结论会不会翻）")
print(f"{'floor':>8}{'合规':>7}{'真空':>7}{'总数':>7}")
for s in sensitivity(rows):
    print(f"{s['floor']:>8}{s['compliant']:>7}{s['vacuous']:>7}{s['total']:>7}")

vac = [r for r in rows if is_vacuous(r["ratio"], DEFAULT_FLOOR)]
print(f"\n真空契约（κ/target < {DEFAULT_FLOOR}）：{len(vac)} 个")
for r in vac:
    print(f"  {r['module']}.{r['function']}  ratio={r['ratio']:.3g}")

out = os.path.join("文档", "实验", "契约指标分布.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as fh:
    json.dump({"rows": rows, "sensitivity": sensitivity(rows),
               "default_floor": DEFAULT_FLOOR}, fh, ensure_ascii=False, indent=2)
print(f"\n明细写入 {out}")
