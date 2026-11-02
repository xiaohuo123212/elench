#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""policy.py —— **成本口径的唯一出处**。

这个模块存在的理由只有一条：**"承诺是什么"只能有一个定义**。

之前 `cost`、`build`、`export` 各自实现了一遍"要不要分段 / 有没有表 / 保守界怎么算"，
改一处漏三处（用户已经抓过两次：默认值回退、缺表就失败、图里画的是最劣候选）。

现在统一成三件事：

1. `mode(argv, module_dir)` —— 这次该用哪个口径（segmented / promise），
   开关优先于 `elench.toml`；
2. `table_path(module_dir, module)` —— 实测分档表在哪；
3. `static_plan(ci, tu_mod)` —— 没有表时，**静态**算出分段表（`plan_dispatch`）；
4. `bounds(ci, tu_mod, fn, points)` —— 给出同一组规模点上的
   **承诺（逐段最优）** 与 **保守界（全候选取 max）**，供 SVG/CSV/表共用。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

CONFIG_NAME = "elench.toml"


# ---------------------------------------------------------------- 项目配置
def find_config(start_dir: str) -> Optional[str]:
    """从 start_dir 往上找 elench.toml。"""
    import os

    d = os.path.abspath(start_dir or ".")
    for _ in range(64):
        cand = os.path.join(d, CONFIG_NAME)
        if os.path.exists(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return None


def load_config(start_dir: str) -> Dict[str, Dict[str, Any]]:
    """解析 elench.toml（只认 `[section]` + `key = 值`）。BOM 兼容。"""
    import io
    import os

    path = find_config(start_dir)
    if not path:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    section = "root"
    try:
        for raw in io.open(path, encoding="utf-8-sig"):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                out.setdefault(section, {})
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v.lower() in ("true", "false"):
                val: Any = v.lower() == "true"
            else:
                try:
                    val = int(v)
                except ValueError:
                    val = v
            out.setdefault(section, {})[k] = val
    except Exception:                                       # noqa: BLE001
        return {}
    return out


def option(start_dir: str, section: str, key: str, default: Any = None) -> Any:
    return load_config(start_dir).get(section, {}).get(key, default)


# ---------------------------------------------------------------- 口径
def mode(argv: List[str], module_dir: str) -> Tuple[str, str]:
    """返回 (`segmented` | `promise`, 一句说明)。

    优先级：命令行 `--promise` > `elench.toml` 的 `cost.promise` > 默认 `segmented`。
    """
    if "--promise" in argv:
        return "promise", "--promise"
    if option(module_dir, "cost", "promise", False):
        return "promise", "elench.toml: cost.promise = true"
    return "segmented", "默认口径"


def require_measured(argv: List[str], module_dir: str) -> bool:
    """缺分档表时是否必须失败：`--require-measured` > 配置 > 默认（放行）。"""
    if "--allow-missing-table" in argv:
        return False
    if any(a in argv for a in ("--require-measured", "--require-measure",
                              "--strict-measure")):
        return True
    return bool(option(module_dir, "build", "require_measured", False))


def table_path(module_dir: str, module: str) -> str:
    import os

    return os.path.join(module_dir, f"{module}.dispatch.yaml")


# ---------------------------------------------------------------- 静态分段
def static_plan(ci, tu_mod) -> Dict[str, List[Tuple[int, int, str]]]:
    """没有实测表时，用 `plan_dispatch` 算出静态分段：{label: [(lo, hi, 候选), ...]}。"""
    from bound import _descendants
    from dispatch import plan_dispatch

    # 让 dsh 里的实现可以被复用（避免两份代码算出不同分段）
    from dsh import _candidate_costs, _eval_at, _size_var

    out: Dict[str, List[Tuple[int, int, str]]] = {}
    for kern in _descendants(ci.mod, "kernels"):
        label = kern.name
        if not label:
            continue
        cands = _candidate_costs(ci, label)
        if len(cands) < 2:
            continue
        var = _size_var(cands)
        lo, hi = 1, 4096
        for bm in _descendants(tu_mod, "benchmodel"):
            for sc in _descendants(bm, "scale"):
                vals = [c.value for c in (sc.children or []) if isinstance(c.value, int)]
                if len(vals) >= 2:
                    lo, hi = min(vals), max(vals)
        plan = plan_dispatch(label, cands, var, lo, hi)
        segs = [(int(a), int(b), str(n)) for a, b, n in getattr(plan, "segments", [])]
        if not segs:
            from dispatch import order_of

            best = min(cands, key=lambda k: (order_of(cands[k]), _eval_at(cands[k], var, hi)))
            segs = [(lo, hi, best)]
        out[label] = segs
    return out


# ---------------------------------------------------------------- 两条界
def bounds(ci, tu_mod, fn_name: str, points: List[int]):
    """同一组规模点上的两条静态界。

    返回 `(segmented, max_over_candidates)`，都是 `{n: 值}`；算不出给 None。

    · `segmented` = **承诺**（逐段最优；没有分段时退化为候选自己的 κ）
    · `max_over_candidates` = **保守界**（全候选取 max）
    """
    from cost_check import evaluate

    var = None
    try:
        var = _size_var_from_fn(ci, fn_name)
    except Exception:                                       # noqa: BLE001
        var = "xs"
    promise = ci.cost_of(fn_name)
    seg: Dict[int, Optional[float]] = {}
    plan = static_plan(ci, tu_mod)
    # 找这个函数对应的 label（`return ?!label(...)`）
    label = _label_of(ci, fn_name)
    segs = plan.get(label or "", [])
    for n in points:
        v = None
        for lo, hi, who in segs:
            if lo <= n <= hi:
                v = _cand_value(ci, label, who, var, n)
                break
        if v is None:
            try:
                v = evaluate(promise, {var: n})
            except Exception:                               # noqa: BLE001
                v = None
        seg[n] = v
    mx: Dict[int, Optional[float]] = {}
    for n in points:
        try:
            mx[n] = evaluate(promise, {var: n})
        except Exception:                                   # noqa: BLE001
            mx[n] = None
    return seg, mx


def _size_var_from_fn(ci, fn_name: str) -> str:
    from dsh import _size_var

    cands = _candidate_costs_all(ci, fn_name)
    if cands:
        return _size_var(cands)
    return "xs"


def _candidate_costs_all(ci, fn_name: str):
    from dsh import _candidate_costs

    label = _label_of(ci, fn_name)
    return _candidate_costs(ci, label) if label else {}


def _label_of(ci, fn_name: str) -> Optional[str]:
    """函数体里 `return ?!label(...)` 的那个 label。"""
    from bound import _descendants

    fn = next((f for f in _descendants(ci.mod, "func") if f.name == fn_name), None)
    if fn is None:
        return None
    for d in _descendants(fn, "dispatch"):
        return d.name
    return None


def _cand_value(ci, label: Optional[str], who: str, var: str, n: int):
    from cost_check import evaluate
    from dsh import _candidate_costs

    if not label:
        return None
    cands = _candidate_costs(ci, label)
    cost = cands.get(who)
    if cost is None:
        return None
    try:
        return evaluate(cost, {var: n})
    except Exception:                                       # noqa: BLE001
        return None
