"""Step 1 of the routing idea: can the *spec* (`.tu`) predict implementation size (`.eln`)?

No model needed. We extract structural features from each function's spec, measure how
much code the implementation actually took, and look at rank correlation.

n is tiny (order 20), so this is an exploratory gate, not a result:
  - if spec features do NOT track implementation size, token-level routing has no signal
    and the idea is dead before the model arrives;
  - if they do, the features are worth testing against completion tokens later.
"""
import os
import sys

sys.path.insert(0, "src")

from parser import parse_source          # noqa: E402
from cost_interp import CostInterp       # noqa: E402
from bound import _descendants           # noqa: E402
import cost_explain as CE                # noqa: E402

DIRS = ("文档/设计/目前设计/样例", "文档/入门/例子", "demo/repair/v1", "demo/repair/v2")

# 阶的数值化：n 的幂次（log 记半阶）
def order_of(cost):
    if cost is None:
        return None
    return CE.deg(cost)


def spec_features(tu_mod, name):
    """从 `.tu` 的一个 spec 里抽结构特征。"""
    spec = next((c for c in (tu_mod.children or [])
                 if getattr(c, "kind", "") == "spec" and (c.name or "") == name), None)
    if spec is None:
        return None
    body = next((c for c in (spec.children or []) if c.kind == "spec-body"), None)
    feats = {"n_params": 0, "n_vec_params": 0, "has_effects": 0,
             "has_choose": 0, "has_recursion_decl": 0, "n_size_vars": 0}
    params = next((c for c in (spec.children or []) if c.kind == "params"), None)
    ps = params.children if params is not None else []
    feats["n_params"] = len(ps)
    for p in ps:
        vals, stack = [], [p]
        while stack:
            n = stack.pop()
            for attr in ("value", "name"):
                v = getattr(n, attr, None)
                if isinstance(v, str):
                    vals.append(v)
            stack.extend(getattr(n, "children", None) or [])
        if any("Vec" in x for x in vals):
            feats["n_vec_params"] += 1
    for cl in (body.children if body else []):
        k, nm = getattr(cl, "kind", ""), getattr(cl, "name", "") or ""
        if k == "clause" and nm == "effects":
            v = getattr(cl, "value", None)
            feats["has_effects"] = 1 if (v and v != "pure") else 0
        if k == "choose":
            feats["has_choose"] = 1
        if k == "clause" and nm == "recursion":
            feats["has_recursion_decl"] = 1
    return feats


def impl_metrics(eln_mod, name):
    """从 `.eln` 的实现里量“写了多少”。"""
    fn = next((c for c in (eln_mod.children or [])
               if getattr(c, "kind", "") in ("func", "fn", "kernel") and (c.name or "") == name),
              None)
    if fn is None:
        return None
    nodes = 0
    stack = [fn]
    while stack:
        n = stack.pop()
        nodes += 1
        stack.extend(getattr(n, "children", None) or [])
    calls = [c for c in _descendants(fn, "call")]
    loops = [c for c in _descendants(fn, "while")] + [c for c in _descendants(fn, "for")]
    stmts = [c for c in _descendants(fn, "let")] + [c for c in _descendants(fn, "assign")]
    recursive = any((c.name or "") == name for c in calls)
    return {"n_nodes": nodes, "n_calls": len(calls), "n_loops": len(loops),
            "n_stmts": len(stmts), "recursive": int(recursive)}


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


rows = []
_seen = set()   # v1/v2 是同一模块的两版实现，去重
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
            feats = spec_features(tu, name)
            impl = impl_metrics(eln, name)
            if feats is None or impl is None:
                continue
            tgt = ci.target_cost(name, "time")
            if tgt is None:
                continue
            feats["order"] = order_of(tgt)
            feats["n_size_vars"] = len(cost_vars) if False else len(set(tgt.variables()))
            _key = (os.path.basename(base), name)
            if _key in _seen:
                continue
            _seen.add(_key)
            rows.append((os.path.basename(base), name, feats, impl))

print(f"样本：{len(rows)} 个「有 target 且有实现」的函数\n")
print(f"{'模块':<11}{'函数':<15}{'阶':>3}{'参数':>4}{'容器':>4}{'尺寸':>4}{'?!':>3}"
      f"{'副作用':>5}{'节点':>6}{'调用':>5}{'循环':>5}")
print("-" * 78)
for mod, name, f, im in rows:
    print(f"{mod:<11}{name:<15}{f['order'] if f['order'] is not None else '-':>3}"
          f"{f['n_params']:>4}{f['n_vec_params']:>4}{f['n_size_vars']:>4}"
          f"{f['has_choose']:>3}{f['has_effects']:>5}"
          f"{im['n_nodes']:>6}{im['n_calls']:>5}{im['n_loops']:>5}")

FEATS = ["order", "n_params", "n_vec_params", "n_size_vars",
         "has_choose", "has_effects", "has_recursion_decl"]
TARGETS = ["n_nodes", "n_calls", "n_loops", "n_stmts"]

print("\nSpearman ρ（特征 × 实现规模）")
print(f"{'':<20}" + "".join(f"{t:>10}" for t in TARGETS))
for ft in FEATS:
    a = [float(r[2].get(ft) or 0) for r in rows]
    line = f"{ft:<20}"
    for tg in TARGETS:
        b = [float(r[3][tg]) for r in rows]
        rho = spearman(a, b)
        line += f"{rho:>10.2f}" if rho is not None else f"{'-':>10}"
    print(line)

n_ok = len(rows)
print(f"\n注意：n = {n_ok}，这是门槛检查，不是结论。|ρ| ≥ 0.4 才值得等模型来验证。")

