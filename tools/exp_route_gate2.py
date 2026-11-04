"""Step 1b of the routing idea: same gate, but every feature is a COUNT instead of a flag.

Why: with n≈19 and 7-8 positives, a 0/1 feature carries almost no statistical content —
its Spearman ρ is just a rank version of a two-group mean difference. Counts are
interpretable and degrade gracefully.

Changes vs exp_route_gate.py:
  has_effects (0/1)  -> n_effects (declared)  + n_effect_sites (call sites attributed)
  has_choose  (0/1)  -> n_dispatch (number of `?!` nodes in the implementation)
  added              -> n_candidates (candidates across kernels in the module)
"""
import os
import sys

sys.path.insert(0, "src")

from parser import parse_source          # noqa: E402
from cost_interp import CostInterp       # noqa: E402
from bound import _descendants           # noqa: E402
import cost_explain as CE                # noqa: E402

DIRS = ("文档/设计/目前设计/样例", "文档/入门/例子", "demo/repair/v1", "demo/repair/v2")


def _texts(node):
    out, stack = [], [node]
    while stack:
        n = stack.pop()
        for attr in ("value", "name"):
            v = getattr(n, attr, None)
            if isinstance(v, str):
                out.append(v)
        stack.extend(getattr(n, "children", None) or [])
    return out


def spec_features(tu_mod, name):
    spec = next((c for c in (tu_mod.children or [])
                 if getattr(c, "kind", "") == "spec" and (c.name or "") == name), None)
    if spec is None:
        return None
    body = next((c for c in (spec.children or []) if c.kind == "spec-body"), None)
    feats = {"n_params": 0, "n_vec_params": 0, "n_effects": 0,
             "n_candidates": 0, "has_recursion_decl": 0}
    params = next((c for c in (spec.children or []) if c.kind == "params"), None)
    ps = params.children if params is not None else []
    feats["n_params"] = len(ps)
    for p in ps:
        if any("Vec" in x for x in _texts(p)):
            feats["n_vec_params"] += 1
    for cl in (body.children if body else []):
        k, nm = getattr(cl, "kind", ""), getattr(cl, "name", "") or ""
        if k == "clause" and nm == "effects":
            v = getattr(cl, "value", None)
            if isinstance(v, list):
                feats["n_effects"] = len(v)
            elif isinstance(v, str) and v not in ("", "pure"):
                feats["n_effects"] = 1
        if k == "clause" and nm == "recursion":
            feats["has_recursion_decl"] = 1
        if k == "choose":
            feats["n_candidates"] += sum(1 for c in (cl.children or [])
                                         if getattr(c, "kind", "") == "choose-clause"
                                         and (c.name or "") == "over"
                                         for _ in ((c.value or {}).get("names") or []))
    return feats


def impl_metrics(eln_mod, name):
    fn = next((c for c in (eln_mod.children or [])
               if getattr(c, "kind", "") in ("func", "fn", "kernel") and (c.name or "") == name),
              None)
    if fn is None:
        return None
    nodes, stack = 0, [fn]
    while stack:
        n = stack.pop()
        nodes += 1
        stack.extend(getattr(n, "children", None) or [])
    calls = _descendants(fn, "call")
    loops = _descendants(fn, "while") + _descendants(fn, "for")
    stmts = _descendants(fn, "let") + _descendants(fn, "assign")
    dispatch = _descendants(fn, "dispatch")
    return {"n_nodes": nodes, "n_calls": len(calls), "n_loops": len(loops),
            "n_stmts": len(stmts), "recursive": int(any((c.name or "") == name for c in calls)),
            "n_dispatch": len(dispatch)}


def rank(vals):
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    r = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(a, b):
    if len(a) < 4:
        return None
    ra, rb = rank(a), rank(b)
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da and db else None


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
            feats, impl = spec_features(tu, name), impl_metrics(eln, name)
            if feats is None or impl is None or (os.path.basename(base), name) in seen:
                continue
            tgt = ci.target_cost(name, "time")
            if tgt is None:
                continue
            seen.add((os.path.basename(base), name))
            feats["order"] = CE.deg(tgt)
            feats["n_size_vars"] = len(set(tgt.variables()))
            try:
                ea = ci.effect_attribution(name) or {}
                feats["n_effect_sites"] = sum(int(s.get("sites", 0))
                                              for s in (ea.get("effects") or {}).values())
            except Exception:
                feats["n_effect_sites"] = 0
            rows.append((os.path.basename(base), name, feats, impl))

print(f"样本：{len(rows)} 个函数\n")
print(f"{'模块':<11}{'函数':<15}{'阶':>3}{'参':>3}{'容':>3}{'尺寸':>4}{'效果':>4}"
      f"{'效应点':>5}{'?!':>4}{'候选':>4}{'节点':>6}")
print("-" * 80)
for mod, name, f, im in rows:
    print(f"{mod:<11}{name:<15}{f['order']:>3}{f['n_params']:>3}{f['n_vec_params']:>3}"
          f"{f['n_size_vars']:>4}{f['n_effects']:>4}{f['n_effect_sites']:>5}"
          f"{im['n_dispatch']:>4}{f['n_candidates']:>4}{im['n_nodes']:>6}")

FEATS = ["order", "n_params", "n_vec_params", "n_size_vars",
         "n_effects", "n_effect_sites", "n_candidates", "n_dispatch"]
TARGETS = ["n_nodes", "n_calls", "n_loops", "n_stmts"]

print("\nSpearman ρ（计数特征 × 实现规模）")
print(f"{'':<20}" + "".join(f"{t:>10}" for t in TARGETS))
for ft in FEATS:
    a = [float(r[2].get(ft) or 0) for r in rows]
    if len(set(a)) < 2:
        print(f"{ft:<20}" + f"{'常量，无方差':>10}")
        continue
    line = f"{ft:<20}"
    for tg in TARGETS:
        b = [float(r[3][tg]) for r in rows]
        rho = spearman(a, b)
        line += f"{rho:>10.2f}" if rho is not None else f"{'-':>10}"
    print(line)

print(f"\nn = {len(rows)}；仍是门槛检查，不是结论。")
