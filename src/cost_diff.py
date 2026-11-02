#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""cost_diff.py —— 成本**承诺**的 diff：比类别，不比秒数。

## 为什么不是"性能回归测试"

CI 的性能门禁比的是**同一台机器上的毫秒数**，换个机器就失效；
而这里比的是**成本式**（`κ` 的符号表达式）：

| 级别 | 含义 | 例子 |
|---|---|---|
| **L1** | **阶变了** | `O(n)` → `O(n²)`（危险） |
| **L3** | 只有常数变了 | `4 + n×7` → `4 + n×9` |
| **不变** | 完全一样 | — |

**只 diff 承诺**（`cost_of`），不 diff 实测——实测本来就不该进版本对比。

## 它**不**比什么（重要）

两端都用**当前**的成本模型去算，所以它回答的是

> 「**你的代码改了**，承诺的阶变了吗？」

而不是「成本模型自己改了，所有数字变了多少」。后者要靠 `tools/golden_check.py`
（κ 冻结表）与重跑基线，不是这个工具的事。

## 用法

```bash
elench cost 文档/.../样例/gap --diff=HEAD~3
elench cost 文档/.../样例/gap --diff=main --deny-regress
```
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

#: 用来判定"随规模变 vs 常数变"的规模点
SIZES = (64, 256, 1024)


def _git_show(ref: str, path: str) -> Optional[str]:
    r = subprocess.run(["git", "show", f"{ref}:{path}"],
                       capture_output=True, encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else None


def git_root(path: str) -> Optional[str]:
    """模块所在目录的 git 根（**不假设**调用方在仓库根）。"""
    d = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
    r = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                       capture_output=True, encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 else None


def load_at_ref(tu_p: str, ch_p: str, ref: str, root: str):
    """把 `<ref>` 版本的 `.tu`/`.eln` 取到临时目录并构造 CostInterp。"""
    from cost_interp import CostInterp
    from parser import parse_source

    _root = git_root(tu_p)
    if not _root:
        return None, f"{tu_p} 不在任何 git 仓库里（--diff 需要版本历史）"
    rel_tu = os.path.relpath(os.path.abspath(tu_p), _root).replace("\\", "/")
    rel_ch = os.path.relpath(os.path.abspath(ch_p), _root).replace("\\", "/")
    old_tu = _git_show(ref, rel_tu)
    old_ch = _git_show(ref, rel_ch)
    if old_tu is None or old_ch is None:
        return None, f"`{ref}` 里没有 {rel_tu} / {rel_ch}"
    d = tempfile.mkdtemp(prefix="eln_diff_")
    tu = parse_source(old_tu, os.path.basename(rel_tu)).module
    ch = parse_source(old_ch, os.path.basename(rel_ch)).module
    # 分档表也可能随版本变；这里**不带表**：diff 比的是"模型给的承诺"，
    # 表是运行期选择，另属一类变化。
    return CostInterp(ch, tu, table_path=None), None


def classify(old_vals: List[float], new_vals: List[float]) -> str:
    """L1（阶变）/ L3（常数变）/ 不变 —— 只看数值曲线，不看表达式文本。"""
    if old_vals == new_vals:
        return "不变"
    ratios = []
    for o, n in zip(old_vals, new_vals):
        if o <= 0:
            return "L1" if n != o else "不变"
        ratios.append(n / o)
    if not ratios:
        return "不变"
    lo, hi = min(ratios), max(ratios)
    # 比值随规模漂移 ⇒ 增长阶不同；否则只是整体乘了一个常数
    return "不变" if abs(hi - lo) < 1e-6 and abs(hi - 1) < 1e-9 else (
        "L3" if abs(hi - lo) < 1e-3 else "L1")


def diff(tu_p: str, ch_p: str, ref: str, root: str) -> Tuple[List[Dict], Optional[str]]:
    from cost_check import evaluate

    old_ci, err = load_at_ref(tu_p, ch_p, ref, root)
    if err:
        return [], err

    from cost_interp import CostInterp
    from parser import parse_source

    tu = parse_source(open(tu_p, encoding="utf-8").read(),
                      os.path.basename(tu_p)).module
    ch = parse_source(open(ch_p, encoding="utf-8").read(),
                      os.path.basename(ch_p)).module
    new_ci = CostInterp(ch, tu, table_path=None)

    rows: List[Dict] = []
    names = sorted(set(old_ci.fns) | set(new_ci.fns))
    for fn in names:
        in_old, in_new = fn in old_ci.fns, fn in new_ci.fns
        if not in_new:
            rows.append({"function": fn, "kind": "删除", "old": None, "new": None,
                         "ratio": None})
            continue
        if not in_old:
            rows.append({"function": fn, "kind": "新增", "old": None, "new": None,
                         "ratio": None})
            continue
        old_vals, new_vals = [], []
        ok = True
        for n in SIZES:
            try:
                ko = old_ci.cost_of(fn)
                kn = new_ci.cost_of(fn)
                vo = evaluate(ko, {v: n for v in ko.variables()})
                vn = evaluate(kn, {v: n for v in kn.variables()})
            except Exception:                          # noqa: BLE001
                ok = False
                break
            old_vals.append(float(vo))
            new_vals.append(float(vn))
        if not ok:
            rows.append({"function": fn, "kind": "无法比较", "old": None,
                         "new": None, "ratio": None})
            continue
        kind = classify(old_vals, new_vals)
        ratio = (new_vals[-1] / old_vals[-1]) if old_vals[-1] else None
        rows.append({"function": fn, "kind": kind,
                     "old": old_vals[-1], "new": new_vals[-1], "ratio": ratio,
                     "old_expr": old_ci.cost_of(fn).render(),
                     "new_expr": new_ci.cost_of(fn).render()})
    return rows, None


def render(rows: List[Dict], ref: str) -> str:
    out = [f"成本 diff（相对 {ref}；**只比承诺 κ，不比实测**）", ""]
    out.append(f"{'函数':<22}{'变化':<8}{'旧 κ':>16}{'新 κ':>16}{'比':>8}")
    for r in rows:
        o = f"{r['old']:,.0f}" if r.get("old") is not None else "—"
        n = f"{r['new']:,.0f}" if r.get("new") is not None else "—"
        ratio = f"{r['ratio']:.2f}×" if r.get("ratio") else "—"
        out.append(f"{r['function']:<22}{r['kind']:<8}{o:>16}{n:>16}{ratio:>8}")
    l1 = [r for r in rows if r["kind"] == "L1"]
    if l1:
        out += ["", "⚠ 阶变了（L1）：" + "、".join(r["function"] for r in l1)]
        for r in l1:
            out.append(f"  {r['function']}: {r.get('old_expr', '?')}")
            out.append(f"       → {r.get('new_expr', '?')}")
    return "\n".join(out)
