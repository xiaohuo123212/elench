# -*- coding: utf-8 -*-
"""**静态分段建议**：解出 `?!` 各候选 κ 的交叉点，给出"哪一段该用谁"。

## 为什么要它

分档表（`*.dispatch.yaml`）是**测**出来的，所以它只覆盖测过的规模
（`gap` 的表只到 4096）。一旦越界，运行期退回第一个候选，承诺却还停在旧数上——
实测 n=8192：承诺 426,001，实际 729,109。

而"两个候选在哪里交叉"**不需要测**：两条 κ 都是编译期算出来的表达式，

    甲：104n + 110        乙：23n + 220,042
    104n + 110 = 23n + 220,042  →  n = 2,715.2

所以交叉点可以**静态求出来**，且与表无关。

## 它不做什么

- 不决定"承诺"的形状：承诺 = **分段上界**，分段取自**运行期**的 `.dispatch.yaml`
  （见 `cost_interp._dispatch`），不是这里的静态交点——两套分段一旦不一致就会破界；
- 不替代实测：它给的是**模型里的**交叉点，真机常数不同，最优分段会偏移。
  表仍然负责"实际跑哪个"；这里给的是"该测哪些点"和"承诺的形状"。

## 边界

表达式里带符号项（`k(f)`）或 `⊤` 时，求值会得到 `inf`/异常——这类候选直接跳过，
并在结果里标出来（不硬猜）。
"""

import math
from typing import Dict, List, Optional, Tuple

from cost_domain.cost_check import evaluate
from cost_domain.symbolic import Cost


def _value(c: Cost, var: str, n: float) -> Optional[float]:
    """在 n 处求值；符号项 / ⊤ / 非有限值 → None（不参与比较）。"""
    try:
        v = evaluate(c, {var: n})
    except Exception:                                  # noqa: BLE001
        return None
    if v is None or math.isinf(v) or math.isnan(v):
        return None
    return v


def _points(lo: int, hi: int, samples: int) -> List[int]:
    """采样点：小范围逐点，大范围按 log 等距。"""
    lo = max(1, int(lo))
    hi = max(lo, int(hi))
    if hi - lo <= 4096:
        return list(range(lo, hi + 1)) if hi - lo <= 256 else \
            sorted({lo} | {int(round(lo * (hi / lo) ** (i / samples)))
                           for i in range(samples + 1)} | {hi})
    return sorted({lo, hi} | {int(round(lo * (hi / lo) ** (i / samples)))
                              for i in range(samples + 1)})


def _bisect(a: Cost, b: Cost, var: str, p: int, q: int) -> Optional[int]:
    """在 [p, q] 里找 a(n) 与 b(n) 的交叉点（a(p) ≤ b(p) 且 a(q) > b(q)）。"""
    lo, hi = p, q
    for _ in range(60):
        if hi - lo <= 1:
            break
        mid = (lo + hi) // 2
        va, vb = _value(a, var, mid), _value(b, var, mid)
        if va is None or vb is None:
            return None
        if va <= vb:
            lo = mid
        else:
            hi = mid
    return hi


def plan(cands: Dict[str, Cost], var: str, lo: int, hi: int,
         samples: int = 48) -> Dict[str, object]:
    """求出建议分段。

    返回 `{"segments": [(lo, hi, name)], "crossings": [(n, left, right)],
           "skipped": [name], "note": str}`。
    """
    usable = {k: v for k, v in cands.items() if _value(v, var, lo) is not None}
    skipped = sorted(set(cands) - set(usable))
    if len(usable) <= 1:
        name = next(iter(usable), None)
        return {
            "segments": [(lo, hi, name)] if name else [],
            "crossings": [],
            "skipped": skipped,
            "note": "只有一个可用候选" if name else "没有可用候选",
        }

    pts = _points(lo, hi, samples)

    def winner(n: int) -> Optional[str]:
        best, best_v = None, None
        for name, c in sorted(usable.items()):
            v = _value(c, var, n)
            if v is None:
                continue
            if best_v is None or v < best_v:
                best, best_v = name, v
        return best

    # 逐点找"赢家变化"的边界
    runs: List[Tuple[int, str]] = []
    prev_n, prev_w = pts[0], winner(pts[0])
    runs.append((prev_n, prev_w))
    crossings = []
    for n in pts[1:]:
        w = winner(n)
        if w != prev_w and prev_w is not None and w is not None:
            cut = _bisect(usable[prev_w], usable[w], var, prev_n, n)
            if cut is not None:
                crossings.append((cut, prev_w, w))
                runs.append((cut, w))
        prev_n, prev_w = n, w

    # 把"跑"的起点换成段起点
    segments: List[Tuple[int, int, str]] = []
    for i, (start, name) in enumerate(runs):
        end = (runs[i + 1][0] - 1) if i + 1 < len(runs) else hi
        start = max(start, lo)
        if start <= end:
            segments.append((start, end, name))

    return {
        "segments": segments,
        "crossings": crossings,
        "skipped": skipped,
        "note": "",
    }


def render(label: str, res: Dict[str, object]) -> str:
    """一行结论 + 逐段说明（给 `elench cost` 直接打印）。"""
    segs = res.get("segments") or []
    xs = res.get("crossings") or []
    if xs:
        head = "、".join(f"n={n}（{a} ↔ {b}）" for n, a, b in xs)
    else:
        head = "无交点"
    lines = [f"      ?!{label}：静态交点 {head}"]
    for a, b, name in segs:
        lines.append(f"         {a}–{b} → {name}")
    if res.get("skipped"):
        lines.append(f"         （跳过，含符号项/⊤：{', '.join(res['skipped'])}）")
    return "\n".join(lines)
