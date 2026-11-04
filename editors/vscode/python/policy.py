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


#: 仓库级参考表的默认目录名（`[dispatch] repo_dir` 可改）
REPO_TABLE_DIR = "tables"


def local_table_path(module_dir: str, module: str) -> str:
    """本机实测出来的表：会被 `.gitignore` 的 `*.dispatch.yaml` 排除。"""
    import os

    return os.path.join(module_dir, f"{module}.dispatch.yaml")


def repo_table_path(module_dir: str, module: str) -> str:
    """入库的参考表：换后缀 `.table.yaml`，免得被 `*.dispatch.yaml` 的规则一起吞掉。"""
    import os

    d = str(option(module_dir, "dispatch", "repo_dir", REPO_TABLE_DIR) or REPO_TABLE_DIR)
    return os.path.join(module_dir, d, f"{module}.table.yaml")


def table_path(module_dir: str, module: str) -> str:
    """分档表用哪一份，由 `[dispatch] source` 决定。

    | source | 用哪份 | 适合 |
    |---|---|---|
    | `auto`（默认） | 有本机表用本机，否则用仓库表 | 日常开发 |
    | `local` | 只用本机实测 | 想要"本机最优" |
    | `repo` | 只用仓库表 | CI / 新人 clone（不用等 `measure`） |

    **为什么要有这个开关**：表里存的是**实测出来的交叉点**，那是**机器相关**的。
    入库能让人少跑一次 `measure`、可复现、可 diff；但别人机器上它可能次优。
    两件事没法同时最优，只能让用户选。
    """
    import os

    src = str(option(module_dir, "dispatch", "source", "auto") or "auto").lower()
    local = local_table_path(module_dir, module)
    repo = repo_table_path(module_dir, module)
    if src == "local":
        return local
    if src == "repo":
        return repo
    return local if os.path.exists(local) else repo


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
