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

## 四层（2026-11-03 补齐）

| 层 | 做什么 | 为什么必须 |
|---|---|---|
| **① 口径** | 两端都按**各自版本的分档表**切段 | 承诺已是**分段上界**；只取代码不取表，等于用今天的分段算昨天的承诺 |
| **② 判决** | 报「达标 → 超标」 | 「阶变了」不等于「变坏了」；L1 也可能变好 |
| **③ 表变化** | 段表改动单独报 | **表也是承诺**：段边界一移，承诺形状就变了，而 κ 在采样点上可能一模一样 |
| **④ 人话** | 具体规模上的倍数 | 符号表达式对非专家不可读 |

## ⚠ 已知的口径陷阱（2026-11-03 实测）

`*.dispatch.yaml` 被 `.gitignore` 排除（第 3 行），**历史里从来没有过表**。
后果：`--diff` 的旧端拿不到表 → 退回 `max`，而新端读得到表 → 分段。
同一份代码会显示「`gap.top` 从 201,553 变到 104,567，0.52×，L1」——
**那是口径差，不是代码变好了。**

所以本模块**显式检测两端表的有无**：不一致时把结论标成「口径不可比」，
并且**不参与 `--deny-regress`**，避免 CI 因为一个文件没提交而误报回归。

要真正消除这个陷阱，得让表进版本库（它本来就是承诺的一部分）。

## 它**不**比什么

两端都用**当前**的成本模型去算，所以它回答的是「**你的代码改了**，承诺的阶变了吗？」
而不是「成本模型自己改了，所有数字变了多少」——后者靠 `tools/golden_check.py`。

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
from typing import Any, Dict, List, Optional, Tuple

#: 用来判定"随规模变 vs 常数变"的规模点
SIZES = (64, 256, 1024)

#: `cost_check.Verdict.kind` → 人话
_VERDICT_ZH = {"ok": "达标", "debt": "超标", "unknown": "无法判定"}


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


def table_path_for(tu_path: str) -> str:
    """当前工作区该用哪份表——交给 `policy.table_path`（`[dispatch] source` 决定）。"""
    from policy import table_path as _tp
    return _tp(os.path.dirname(os.path.abspath(tu_path)),
               os.path.basename(tu_path).rsplit(".", 1)[0])


def _read_table(path: Optional[str]) -> Dict[str, List[Tuple[int, int, str]]]:
    if not path or not os.path.exists(path):
        return {}
    try:
        from interp import load_dispatch_table
        return load_dispatch_table(path) or {}
    except Exception:                                  # noqa: BLE001
        return {}


def load_at_ref(tu_p: str, ch_p: str, ref: str,
                root: str) -> Tuple[Any, Dict[str, Any], Optional[str]]:
    """把 `<ref>` 版本的 `.tu` / `.eln` / **`.dispatch.yaml`** 取到临时目录并构造 CostInterp。

    **表必须一起取**：承诺是分段上界，分段来自表。只取代码不取表，
    两边就不是同一个量（实测 `gap`：max 口径 201,553，分段口径 104,567，差 1.93 倍）。
    """
    from cost_interp import CostInterp
    from parser import parse_source

    _root = git_root(tu_p)
    if not _root:
        return None, {}, f"{tu_p} 不在任何 git 仓库里（--diff 需要版本历史）"
    rel_tu = os.path.relpath(os.path.abspath(tu_p), _root).replace("\\", "/")
    rel_ch = os.path.relpath(os.path.abspath(ch_p), _root).replace("\\", "/")
    old_tu = _git_show(ref, rel_tu)
    old_ch = _git_show(ref, rel_ch)
    if old_tu is None or old_ch is None:
        return None, {}, f"`{ref}` 里没有 {rel_tu} / {rel_ch}"

    d = tempfile.mkdtemp(prefix="eln_diff_")
    tu = parse_source(old_tu, os.path.basename(rel_tu)).module
    ch = parse_source(old_ch, os.path.basename(rel_ch)).module

    # **表可能有两份**：本机实测（`<模块>.dispatch.yaml`，通常被 gitignore）
    # 与入库参考表（`tables/<模块>.table.yaml`）。历史里能取到哪份就用哪份，
    # 优先取下当前配置选中的那份，取不到再退另一份。
    _base = os.path.splitext(rel_tu)[0]
    _dir = os.path.dirname(_base)
    _dir = "" if _dir in ("", ".") else _dir + "/"
    _mod = os.path.basename(_base)
    _cands = []
    try:
        from policy import (local_table_path as _ltp, repo_table_path as _rtp)
        _abs_dir = os.path.dirname(os.path.abspath(tu_p))
        for _p in (_ltp(_abs_dir, _mod), _rtp(_abs_dir, _mod)):
            _cands.append(os.path.relpath(_p, _root).replace("\\", "/"))
    except Exception:                                  # noqa: BLE001
        _cands = []
    _cands += [f"{_dir}{_mod}.dispatch.yaml",
               f"{_dir}tables/{_mod}.table.yaml"]
    old_tbl_txt = None
    rel_tbl = None
    for _c in _cands:
        if _c in [x for x in _cands[:_cands.index(_c)]]:
            continue
        old_tbl_txt = _git_show(ref, _c)
        if old_tbl_txt:
            rel_tbl = _c
            break
    tbl_path = None
    if old_tbl_txt:
        tbl_path = os.path.join(d, os.path.basename(rel_tbl))
        with open(tbl_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(old_tbl_txt)
    ci = CostInterp(ch, tu, table_path=tbl_path)
    return ci, _read_table(tbl_path), None


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
    if abs(hi - lo) < 1e-6 and abs(hi - 1) < 1e-9:
        return "不变"
    if abs(hi - lo) < 1e-3:
        return "L3"                     # 比值恒定：整体乘了一个常数
    # **L2（L-09，2026-11-06）**：比值**不是完全恒定**但漂移很小（≤5%），
    # 例如 2n+3 改成 2n+5、或 n 改成 1.02n。以前这类直接落进 L1（"阶变了"）——
    # **那是误报**：把"系数动了"说成"复杂度变了"。
    if lo > 1e-9 and (hi / lo) <= 1.05:
        return "L2"
    return "L1"


def _verdict(ci, fn: str) -> str:
    """用**和 `elench cost` 同一套**判定（`cost_check.compare`）。"""
    try:
        from cost_check import compare
        return _VERDICT_ZH.get(compare(ci.cost_of(fn), ci.target_cost(fn)).kind,
                               "无法判定")
    except Exception:                                  # noqa: BLE001
        return "无法判定"


def _fmt_segs(segs: List[Any]) -> str:
    return "; ".join(f"{a}..{b}:{nm}" for a, b, nm in segs) or "（空）"


def diff_tables(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, str]]:
    """分档表的差异：**表也是承诺，段边界一移承诺形状就变了**。"""
    out: List[Dict[str, str]] = []
    for label in sorted(set(old) | set(new)):
        o = [tuple(x) for x in (old.get(label) or [])]
        n = [tuple(x) for x in (new.get(label) or [])]
        if o == n:
            continue
        if not o:
            out.append({"label": label, "kind": "新增表", "detail": _fmt_segs(n)})
        elif not n:
            out.append({"label": label, "kind": "删除了表", "detail": _fmt_segs(o)})
        else:
            same_names = [x[2] for x in o] == [x[2] for x in n]
            out.append({"label": label,
                        "kind": "段边界变了" if same_names else "候选变了",
                        "detail": f"{_fmt_segs(o)}  →  {_fmt_segs(n)}"})
    return out


# ---------------------------------------------------------------- 契约 diff
#
# `cost --diff` 只比 **κ**（承诺的松紧）。但"这次改动有没有把契约削弱"
# 是另一件事，而且**完全可判定**（就是结构比较）：
#   删掉一条 `guarantees is_sorted(ret)` → 可观察行为变了，κ 一点不动；
#   `effects` 从 pure 变成 io        → κ 不动（外部调用只数次数，不估价）；
#   `scale` 从 8..65536 缩到 8..4096 → 承诺的**适用范围**变小了。
# 这三样现在都看不见。所以单独比一遍契约本身。

_CLAUSE_KEYS = ("effects", "guarantees", "assume", "repeats", "recursion")


def _txt(n, depth: int = 0) -> str:
    """把一段 AST **稳定地**渲染成文本（两端同一套规则，才能比）。"""
    if n is None:
        return ""
    if depth > 6:
        return "…"
    kids = getattr(n, "children", None) or []
    if kids:
        return " ".join(x for x in (_txt(c, depth + 1) for c in kids) if x)
    v = getattr(n, "value", None)
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}={v[k]}" for k in sorted(v) if v[k]) + "}"
    if v is not None:
        return str(v)
    return getattr(n, "name", None) or ""


def _scale_text(node) -> str:
    """`bench-scale` → `xs: 1..4096（×2）`。直接 `_txt` 会得到 `range 1 4096 2`。"""
    name = getattr(node, "name", "") or ""
    nums = []

    def walk(n, depth=0):
        if n is None or depth > 4:
            return
        v = getattr(n, "value", None)
        if isinstance(v, bool):
            pass
        elif isinstance(v, int):
            nums.append(v)
        elif isinstance(v, str) and v.strip().isdigit():
            nums.append(int(v.strip()))
        for c in (getattr(n, "children", None) or []):
            walk(c, depth + 1)

    walk(node)
    body = _txt(node)
    if len(nums) >= 2:
        step = f"（×{nums[2]}）" if len(nums) >= 3 else ""
        return f"{name + ': ' if name else ''}{nums[0]}..{nums[1]}{step}"
    return body


def _specs_of(tu_mod) -> Dict[str, Any]:
    out = {}
    for it in (getattr(tu_mod, "children", None) or []):
        if getattr(it, "kind", "") == "spec":
            out[it.name or ""] = it
    return out


def contract_snapshot(ci, tu_mod) -> Dict[str, Dict[str, str]]:
    """每个函数的**契约事实**（键 → 文本）。只收可比较、可在报告里说清的。"""
    from symbolic import Cost as _C

    snap: Dict[str, Dict[str, str]] = {}
    for fn, spec in _specs_of(tu_mod).items():
        facts: Dict[str, str] = {}
        body = next((c for c in (spec.children or []) if c.kind == "spec-body"), None)
        for cl in (body.children if body else []):
            k = getattr(cl, "kind", "")
            nm = getattr(cl, "name", "") or ""
            if k == "clause" and nm in _CLAUSE_KEYS:
                if nm == "effects":
                    v = getattr(cl, "value", None)
                    if isinstance(v, list):
                        facts[nm] = "、".join(v) if v else "pure"
                    else:
                        facts[nm] = str(v or "pure")
                else:
                    facts[nm] = _txt(cl)
            elif k == "clause" and nm == "target":
                for d in (cl.children or []):
                    if getattr(d, "kind", "") == "dim":
                        dn = d.name or ""
                        try:
                            cost = ci.target_cost(fn, dn)
                        except Exception:                  # noqa: BLE001
                            cost = None
                        facts[f"target.{dn}"] = cost.render() if cost is not None else _txt(d)
            elif k == "benchmodel":
                for sub in (cl.children or []):
                    if getattr(sub, "kind", "") == "bench-scale":
                        # `scale { xs in range(1, 4096, *2); }` → `xs: 1..4096 ×2`
                        facts["scale"] = _scale_text(sub)
            elif k == "choose":
                for cc in (cl.children or []):
                    if getattr(cc, "kind", "") != "choose-clause":
                        continue
                    if (cc.name or "") == "over":
                        facts[f"{nm}.over"] = "、".join((cc.value or {}).get("names", []))
                    elif (cc.name or "") == "objective":
                        facts[f"{nm}.objective"] = str(cc.value)
        snap[fn] = facts
    return snap


def _looseness(ci_old, ci_new, fn: str, dim: str) -> str:
    """target 变化是**放松**还是**收紧**——用数值判，不猜。

    放松是最危险的方向：承诺变宽了，κ 不报警，可观察行为却少保证了一层。
    """
    from cost_check import evaluate
    try:
        ko, kn = ci_old.target_cost(fn, dim), ci_new.target_cost(fn, dim)
        if ko is None or kn is None:
            return "变化"
        bigger = smaller = False
        for n in SIZES:
            env = {v: n for v in set(ko.variables()) | set(kn.variables())}
            vo, vn = evaluate(ko, env), evaluate(kn, env)
            if vn > vo:
                bigger = True
            elif vn < vo:
                smaller = True
        if bigger and not smaller:
            return "放松"
        if smaller and not bigger:
            return "收紧"
        return "变化"
    except Exception:                                  # noqa: BLE001
        return "变化"


def diff_contracts(old: Dict[str, Dict[str, str]],
                   new: Dict[str, Dict[str, str]],
                   ci_old=None, ci_new=None) -> List[Dict[str, str]]:
    """比契约**本身**。`kind` 取值：削弱 / 增强 / 变化 / 扩大 / 缩小。"""
    out: List[Dict[str, str]] = []
    for fn in sorted(set(old) | set(new)):
        o, n = old.get(fn), new.get(fn)
        if o is None or n is None:
            out.append({"function": fn, "fact": "（函数）",
                        "old": "有" if o else "无", "new": "有" if n else "无",
                        "kind": "新增函数" if o is None else "删除函数"})
            continue
        for key in sorted(set(o) | set(n)):
            ov, nv = o.get(key), n.get(key)
            if ov == nv:
                continue
            if ov is None:
                kind = "增强"
            elif nv is None:
                # 承诺/副作用**消失**是最容易滑过去的一类：κ 完全不动
                kind = "削弱" if key.startswith(("guarantees", "assume")) else "变化"
            elif key.startswith("target."):
                kind = (_looseness(ci_old, ci_new, fn, key.split(".", 1)[1])
                        if (ci_old is not None and ci_new is not None) else "变化")
            else:
                kind = "变化"
            out.append({"function": fn, "fact": key,
                        "old": ov if ov is not None else "（无）",
                        "new": nv if nv is not None else "（删除）",
                        "kind": kind})
    return out


def diff(tu_p: str, ch_p: str, ref: str, root: str) -> Tuple[
        List[Dict[str, Any]], List[Dict[str, str]], Dict[str, Any],
        Optional[str], List[Dict[str, str]]]:
    """返回 `(每个函数的行, 分档表的变化, 上下文, 错误)`。

    上下文里的 `comparable` 说明**两端是否同一口径**：两端表的有无不一致时
    （典型情形是表被 `.gitignore` 排除、历史里根本没有），
    任何"变大/变小"的结论都是口径差，不是代码变化。
    """
    from cost_check import evaluate

    old_ci, old_tbl, err = load_at_ref(tu_p, ch_p, ref, root)
    if err:
        return [], [], {}, err, []

    from cost_interp import CostInterp
    from parser import parse_source

    tu = parse_source(open(tu_p, encoding="utf-8").read(),
                      os.path.basename(tu_p)).module
    ch = parse_source(open(ch_p, encoding="utf-8").read(),
                      os.path.basename(ch_p)).module
    cur_tbl_path = table_path_for(tu_p)
    new_ci = CostInterp(ch, tu,
                        table_path=(cur_tbl_path if os.path.exists(cur_tbl_path) else None))
    new_tbl = _read_table(cur_tbl_path)

    ctx = {"old_has_table": bool(old_tbl), "new_has_table": bool(new_tbl)}
    ctx["comparable"] = (ctx["old_has_table"] == ctx["new_has_table"])

    # 契约本身也要比：κ 不动但承诺被削弱的情形，现在只有这里能看见
    contract = diff_contracts(contract_snapshot(old_ci, old_ci.decl),
                              contract_snapshot(new_ci, tu),
                              ci_old=old_ci, ci_new=new_ci)

    rows: List[Dict[str, Any]] = []
    names = sorted(set(old_ci.fns) | set(new_ci.fns))
    for fn in names:
        in_old, in_new = fn in old_ci.fns, fn in new_ci.fns
        if not in_new:
            rows.append({"function": fn, "kind": "删除", "old": None, "new": None,
                         "ratio": None, "comparable": True})
            continue
        if not in_old:
            rows.append({"function": fn, "kind": "新增", "old": None, "new": None,
                         "ratio": None, "comparable": True})
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
                         "new": None, "ratio": None, "comparable": ctx["comparable"]})
            continue
        kind = classify(old_vals, new_vals)
        # 口径不可比时，任何"变了"都不是代码变化，标出来免得被当成结论
        if not ctx["comparable"] and kind != "不变":
            kind = "口径差"
        ratio = (new_vals[-1] / old_vals[-1]) if old_vals[-1] else None
        rows.append({"function": fn, "kind": kind,
                     "old": old_vals[-1], "new": new_vals[-1], "ratio": ratio,
                     "old_expr": old_ci.cost_of(fn).render(),
                     "new_expr": new_ci.cost_of(fn).render(),
                     "verdict_old": _verdict(old_ci, fn),
                     "verdict_new": _verdict(new_ci, fn),
                     "comparable": ctx["comparable"]})
    return rows, diff_tables(old_tbl, new_tbl), ctx, None, contract


def render(rows: List[Dict[str, Any]], ref: str,
           table_changes: Optional[List[Dict[str, str]]] = None,
           ctx: Optional[Dict[str, Any]] = None,
           contract_changes: Optional[List[Dict[str, str]]] = None) -> str:
    ctx = ctx or {}
    comparable = ctx.get("comparable", True)
    old_has, new_has = ctx.get("old_has_table"), ctx.get("new_has_table")

    out = [f"成本 diff（相对 {ref}；**只比承诺 κ，不比实测**）"]

    # ① 口径必须写出来：读不读表，同一个函数能差一倍
    if comparable:
        out.append("口径：**分段上界**（两端各按自己版本的分档表切段），与 `elench cost` 同源"
                   if old_has else
                   "口径：**两端都没有分档表 → 都退回 `max over candidates`**（与 `elench cost` 同源）")
    else:
        out.append(f"口径：**两端不一致** —— 旧端{'有' if old_has else '无'}表，"
                   f"新端{'有' if new_has else '无'}表")
        out.append("      ⚠ 下面的『变化』是**口径差**，不是代码变好或变坏；已排除在 `--deny-regress` 之外。")
        out.append("      ⚠ 常见成因：`*.dispatch.yaml` 被 `.gitignore` 排除，历史里没有表——"
                   "而表本身就是承诺的一部分。")

    out.append("")
    out.append(f"{'函数':<20}{'变化':<8}{'旧 κ':>14}{'新 κ':>14}{'比':>9}   判决")
    for r in rows:
        o = f"{r['old']:,.0f}" if r.get("old") is not None else "—"
        n = f"{r['new']:,.0f}" if r.get("new") is not None else "—"
        ratio = f"{r['ratio']:.2f}×" if r.get("ratio") else "—"
        vo, vn = r.get("verdict_old"), r.get("verdict_new")
        judge = f"{vo} → {vn}" if vo and vn else "—"
        out.append(f"{r['function']:<20}{r['kind']:<8}{o:>14}{n:>14}{ratio:>9}   {judge}")

    # ②b **契约变化**：κ 完全不动，但承诺本身变了——这是最容易滑过去的一类
    if contract_changes:
        out += ["", "⚠ 契约变了（target / guarantees / effects / scale；"
                    "**κ 可能一点不动**）："]
        for c in contract_changes:
            mark = "削弱" if c["kind"] == "削弱" else c["kind"]
            out.append(f"  {c['function']:<18}{c['fact']:<22}"
                       f"{str(c['old'])[:34]:<36}→ {str(c['new'])[:34]}   [{mark}]")
        weak = [c for c in contract_changes if c["kind"] in ("削弱", "放松")]
        if weak:
            out.append("  ⚠ 有承诺被**削弱或放松**（" + "、".join(
                f"{c['function']}.{c['fact']}" for c in weak[:5]) +
                "）：**κ 不会报警**，但你的保证变少了——请确认是有意为之。")

    # ③ 表变化：κ 在采样点上可能一动不动，但承诺形状已经变了
    if table_changes:
        out += ["", "⚠ 承诺形状变了（分档表改动；**表也是承诺**）："]
        for t in table_changes:
            out.append(f"  {t['label']}  [{t['kind']}]  {t['detail']}")

    # ② 判决变差：L1 不等于变坏，要分开说
    worse = [r for r in rows
             if r.get("verdict_old") and r.get("verdict_new")
             and r["verdict_old"] == "达标" and r["verdict_new"] == "超标"]
    if comparable and worse:
        out += ["", "✗ 判决变差（达标 → 超标）："
                + "、".join(r["function"] for r in worse)]

    # ④ 人话：符号之外给一个能感知的量
    l1 = [r for r in rows if r["kind"] == "L1"]
    if l1:
        out += ["", "⚠ 阶变了（L1）：" + "、".join(r["function"] for r in l1)]
        for r in l1:
            out.append(f"  {r['function']}: {r.get('old_expr', '?')}")
            out.append(f"       → {r.get('new_expr', '?')}")
            if r.get("ratio"):
                out.append(f"       在 n={SIZES[-1]} 上多花 {r['ratio']:.2f} 倍"
                           f"（{r['old']:,.0f} → {r['new']:,.0f} 次操作）")
    return "\n".join(out)
