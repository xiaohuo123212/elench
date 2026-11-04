#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""cost_explain.py —— **超界归因**：把「超标了」变成「是哪个构造抬的」。

## 一句话

> **κ 本来是一棵树，报告把它压成了一行字符串——归因 80% 的工作是「别压」。**

现在 `cost` 只说「超标，比值 ≈ 1072023837083.90」。读的人知道超了，
但不知道**是哪一段、哪个构造、删了它能不能达标**。

## 三步（都是确定的，不用启发式）

| 步 | 做什么 |
|---|---|
| **① 维度** | 逐个维度（time / allocation）判，报出**哪几个**超 |
| **② 支配项** | 按**尺寸指数** `deg` 找抬得最高的子树 |
| **③ 最小修复** | 把支配项**换成常数 1**，用同一套引擎重判；达标就是元凶 |

第 ③ 步的关键性质：**它不是猜，是"再算一遍"**。所以归因结论**能被工具自己验证**。

## 溯源从哪来

`Cost` 是 `__slots__` 的不可变结构，**不给它加字段**（会污染所有构造点）。
改在构造点旁边挂一张**并行侧表** `id(Cost) → Origin`，只在 `--explain` 时维护。

**陷阱**：`Cost.add/mul/max` 会新建节点，`id()` 不稳定。所以侧表在
`_while` / `_call` / `_dispatch` **拿到返回值的那一刻**绑定——这三个是"元凶"的粒度。
（第一版就这个粒度：**构造级**，不做逐 atom。）

## 硬约束

> 开 `--explain` 的 κ 必须与不开时**逐字节相同**。
> 归因是**只读**的：它只读侧表，不参与任何算术。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from symbolic import Cost

#: 无单一元凶时最多再试几个候选（按 deg 递减）
MAX_TRIES = 5

_KIND_ZH = {
    "loop": "循环",
    "call": "调用",
    "dispatch": "?! 候选选择",
    "branch": "分支",
    "builtin": "内建",
    "literal": "字面量",
}


# ---------------------------------------------------------------- 尺寸指数

def deg(c: Cost) -> int:
    """**尺寸指数**：这个子树随规模长得多快（用于比大小，不用于判定）。

    | 形状 | deg |
    |---|---|
    | 常数 | 0 |
    | `log2(s)` | 0（低于 size，排序时靠前） |
    | `size(x)` | 1 |
    | 加 / max | `max(两棵)` |
    | 乘 | `两棵之和` |
    | 分段 | 取所有段（含哨兵）里最大的 |

    > 它只是**排序用的粗指标**，不是判定依据。判定永远是 `cost_check.compare`。
    """
    k = c.kind
    if k == "num":
        return 0
    if k == "size":
        return 1
    if k == "log2":
        return 0
    if k in ("add", "max"):
        return max(deg(c.args[0]), deg(c.args[1])) if len(c.args) >= 2 else 0
    if k == "mul":
        return deg(c.args[0]) + deg(c.args[1]) if len(c.args) >= 2 else 0
    if k == "piece":
        _var, segs = c.value
        return max([deg(x) for _, _, x in segs] + [deg(c.args[0])] if segs
                   else [deg(c.args[0])])
    return 0


# ---------------------------------------------------------------- 支配项

def subterms(c: Cost, origins: Optional[Dict[int, Dict[str, Any]]] = None,
             limit: int = 64) -> List[Cost]:
    """把加法 / max 链**摊平**成并列子项（左到右，保证确定性）。

    **遇到已登记的构造就停下**：`_while` 返回的是一个 `add`（"n×每轮 + 末尾判空"），
    继续往下拆会把"这个循环"拆成两个无主碎片，元凶就永远指不出来。
    归因的粒度是**构造级**，不是 atom 级——这里守住这条。

    不加 `set` 去重：同样的子项出现两次时**两边都要算**，否则 ablation 会低估。
    """
    out: List[Cost] = []
    org = origins or {}

    def rec(x: Cost) -> None:
        if len(out) >= limit:
            return
        if id(x) in org:
            out.append(x)          # 已登记的构造：整块作为一个子项
            return
        if x.kind in ("add", "max") and len(x.args) >= 2:
            rec(x.args[0])
            rec(x.args[1])
            return
        out.append(x)

    rec(c)
    return out


def dominating(c: Cost,
               origins: Optional[Dict[int, Dict[str, Any]]] = None) -> Optional[Cost]:
    """**支配项** = `deg` 最大的那个子项；并列时取**最靠左**的（输出确定）。"""
    terms = subterms(c, origins)
    if not terms:
        return None
    best = terms[0]
    for t in terms[1:]:
        if deg(t) > deg(best):
            best = t
    return best


def _replace(c: Cost, target: Cost, repl: Cost) -> Cost:
    """把树里**第一个**等于 `target` 的子树换成 `repl`（按对象同一性）。"""
    if c is target:
        return repl
    if not c.args:
        return c
    new_args = []
    done = False
    for a in c.args:
        if not done and a is target:
            new_args.append(repl)
            done = True
        else:
            new_args.append(_replace(a, target, repl))
    if c.kind == "piece":
        var, segs = c.value
        return Cost("piece", (var, tuple((lo, hi, _replace(x, target, repl))
                                         for lo, hi, x in segs)), tuple(new_args))
    return Cost(c.kind, c.value, tuple(new_args))


# ---------------------------------------------------------------- 报告

def _origin_of(origins: Dict[int, Dict[str, Any]], c: Cost) -> Optional[Dict[str, Any]]:
    """先按对象同一性找；找不到就按**渲染文本**回找。

    为什么要回找：`substitute_sizes`（把局部尺寸换成形参尺寸）会**重建节点**，
    `id()` 随之失效。文本匹配在多处同形时只能取第一个——**确定性优先**，
    且同形的子项本来就该指向同类构造。
    """
    hit = origins.get(id(c))
    if hit is not None:
        return hit
    txt = c.render()
    for o in origins.values():
        if o.get("render") == txt:
            return o
    return None


def _fmt_origin(o: Optional[Dict[str, Any]],
                src_lines: Optional[List[str]] = None) -> str:
    if not o:
        return ""
    ln = o.get("line", 0)
    where = f"第 {ln} 行" if ln else "（无行号）"
    kind = _KIND_ZH.get(o.get("kind", ""), o.get("kind", ""))
    note = f"  `{o['note']}`" if o.get("note") else ""
    txt = ""
    if src_lines and 0 < ln <= len(src_lines):
        txt = f"  `{src_lines[ln - 1].strip()[:72]}`"
    return f"{where}{txt}{note}  （{kind}）"


def explain_function(ci: Any, fn: str, verdict_kind: str,
                     target: Optional[Cost], actual: Optional[Cost],
                     size_var: str, size_n: int = 1024,
                     dimension: str = "time",
                     src_lines: Optional[List[str]] = None) -> List[str]:
    """给一个超界的函数出归因，返回要打印的行（**只读，不改任何东西**）。

    `verdict_kind` 是调用方已经判好的结论（`debt` / `unknown`）——归因**不重判**，
    它只回答「如果拿掉这一项，会不会好」。
    """
    from cost_check import compare, evaluate

    lines: List[str] = []
    if actual is None:
        return ["  归因不可用：这个函数没有算出 κ（`E-Cost-ExplainUnavailable`）"]
    origins = getattr(ci, "origins", {}) or {}

    # ② 支配项
    dom = dominating(actual, origins)
    if dom is None:
        return ["  归因不可用：κ 是单节点，没有可比的子项"]

    dim_zh = {"time": "时间", "allocation": "分配"}.get(dimension, dimension)
    lines.append(f"  实际      {actual.render()}      [proved：来自 κ 树结构；维度 {dim_zh}]")
    lines.append(f"  支配项    {dom.render()}      ← 尺寸指数 {deg(dom)}")
    # **杠杆定价**（借自 Majdoddin §3.9）：改哪里有用、哪里没用
    _lv = lever_shares(actual, size_var)
    if _lv:
        for _txt, _share in _lv:
            lines.append(f"  杠杆      {_share:6.1%}  {_txt[:56]}")
        lines.append("            ↑ 占比是该子项在 n=%d 处占 κ 的比例；改占比大的才有效"
                     % LEVER_N)
    if dimension == "time":
        o = _origin_of(origins, dom)
        lines.append(f"  元凶      {_fmt_origin(o, src_lines) or '（这个构造没登记到行号）'}")
    else:
        # **构造级溯源只对 time 建立**（侧表在 `cost_of` 里登记）。
        # 分配维度只做到表达式级——这一点必须说出来，不能装作有行号。
        lines.append("  元凶      **分配维度只到表达式级**：构造级溯源目前只对 time 建立")

    # `?!` 的逐候选分解：我们最独特的一块
    cands = _dispatch_cands_of(ci, dom)
    if cands:
        best = None
        for nm, cst in cands.items():
            d = deg(cst)
            mark = ""
            if best is None or d > best[1]:
                best = (nm, d)
        for nm, cst in cands.items():
            star = "  ★ 支配" if best and nm == best[0] else ""
            lines.append(f"  候选分解  {nm:<20}{cst.render()}{star}")

    # ③ 最小修复：拿掉它，用**同一套引擎**再判一次
    env = {size_var: size_n}

    # **`?!` 的修复不是"删掉"，是"换候选"**：拿掉整个 dispatch 没有意义，
    # 换成排第二的候选才是有意义的建议。所以先试这条，再退回通用 ablation。
    if cands:
        for alt, acst in sorted(cands.items(), key=lambda kv: deg(kv[1])):
            if acst is dom or acst.render() == dom.render():
                continue
            try:
                v3 = compare(_replace(actual, dom, acst), target)
            except Exception:                          # noqa: BLE001
                continue
            if v3.kind == "ok":
                lines.append(f"  最小修复  **换成候选 `{alt}` 即可达标**")
                return lines
    tried: List[Tuple[str, str]] = []
    terms = sorted(subterms(actual, origins), key=lambda x: -deg(x))[:MAX_TRIES]
    fixed = False
    for i, t in enumerate(terms):
        cand = _replace(actual, t, Cost.num(1))
        try:
            v2 = compare(cand, target)
        except Exception:                              # noqa: BLE001
            continue
        if v2.kind == "ok":
            how = ("**删掉这一项即可达标**" if i == 0 else
                   f"删掉第 {i + 1} 大的项（{t.render()}）可达标")
            lines.append(f"  最小修复  {how}（剩余是原来的 "
                         f"{_pct(cand, actual, env):.0%}）")
            fixed = True
            break
        tried.append((t.render(), v2.kind))
    if not fixed:
        lines.append(f"  最小修复  **没有单一元凶**（试了 {len(tried)} 项都不够）"
                     f" —— 超界是累加导致的（`E-Cost-NoSingleBlame`）")
    return lines


def _pct(part: Cost, whole: Cost, env: Dict[str, float]) -> float:
    """拿掉某一项后，剩下的占原来的多少（**估计值**，取不到就返回 1.0）。"""
    from cost_check import evaluate
    try:
        a = evaluate(whole, env)
        b = evaluate(part, env)
        if a and a > 0:
            return max(0.0, min(1.0, b / a))
    except Exception:                                  # noqa: BLE001
        pass
    return 1.0


def _dispatch_cands_of(ci: Any, dom: Cost) -> Optional[Dict[str, Cost]]:
    """支配项若来自 `?!`，返回它的逐候选成本。"""
    table = getattr(ci, "dispatch_cands", None) or {}
    if not table:
        return None
    # 按 render 文本匹配：支配项常常就是"某个候选 + 常数"的外层，
    # 所以退一步用包含关系找（确定性：按 label 排序取第一个命中）。
    txt = dom.render()
    for label in sorted(table):
        for nm, cst in (table[label] or {}).items():
            if cst.render() and cst.render() in txt:
                return dict(table[label])
    return None


# ---------------------------------------------------------------- 借用 ①：杠杆定价
#
# 思路来自 Majdoddin（arXiv 2610.41622）§3.9 "The levers, priced"：
# 不只说"超了多少"，而是把超出的量**分解到每个子项上**，
# 让人一眼看出**改哪里有用、改哪里没用**。
# 我们的版本比它弱：它按 2 的幂分解常数，我们按 κ 树的子项在某个 n 上的占比分解。

#: 分解时看的规模点（取声明区间的上端更有意义，但这里固定，保证可复现）
def _brief(s: str, width: int = 60) -> str:
    return s if len(s) <= width else s[:width - 1] + "…"


def callee_lines(ci: Any, fn: str, size_var: str, n: int = 0,
                 max_n: int = 3, depth: int = 2,
                 _seen: Optional[set] = None,
                 max_depth: int = 2) -> List[str]:
    """被调方清单：**这一层贵，是因为调了谁**（跨函数链的第一级）。

    只回答「调了谁、它自己多贵、贵在哪一行」，**不假装知道调用次数**——
    精确的调用占比要沿调用点累计，侧表里没存，所以这里给的是
    `κ_callee(n)` 的排序，标签写 estimated。

    这一节的意义：现在 `元凶` 只能说"第 12 行有一次调用"，
    读者看不到被调方内部；补上这一节就能往下走一层。
    """
    from cost_check import evaluate
    if not n:
        n = LEVER_N                     # 常量在本函数之后定义，故此处解析
    if _seen is None:
        _seen = set()
    node = getattr(ci, "fns", {}).get(fn)
    if node is None:
        return []
    calls, stack = [], [node]
    while stack:
        cur = stack.pop()
        if getattr(cur, "kind", "") == "call":
            calls.append(cur)
        stack.extend(getattr(cur, "children", None) or [])
    seen, rows = set(), []
    for c in calls:
        name = getattr(c, "name", None) or ""
        if not name or name in seen or name == fn:
            continue
        if name not in getattr(ci, "fns", {}):
            continue                      # 内建函数不走这条链
        seen.add(name)
        ck = ci.cost_of(name)
        if ck is None or ck.is_top():
            rows.append((0.0, name, None, getattr(c, "line", None)))
            continue
        try:
            v = float(evaluate(ck, {size_var: n}))
        except Exception:                                  # noqa: BLE001
            v = 0.0
        rows.append((v, name, ck, getattr(c, "line", None)))
    if not rows:
        return []
    rows.sort(key=lambda r: -r[0])
    out = []
    if depth == max_depth:
        out.append(f"被调方    按 κ({n}) 排序（**不含调用次数**，estimated）")
    pad = "  " * (max_depth - depth)
    for index, (v, name, ck, line) in enumerate(rows[:max_n]):
        if ck is None:
            out.append(f"  {pad}{name}()   κ = ⊤（推不出来）")
            continue
        dom = dominating(ck)
        out.append(f"  {pad}{name}()   κ={v:,.0f}   支配项 {_brief(dom.render(), 48)}"
                   + (f"（第 {line} 行调用）" if line else ""))
        # **递归下降**：只沿最贵的那个被调方往下走，深度受 `depth` 限制。
        # 用 `_seen` 防环（a → b → a 会在第二次遇到 a 时停）。
        if depth > 1 and index == 0 and name not in _seen:
            _seen.add(name)
            out.extend(callee_lines(ci, name, size_var, n=n, max_n=1,
                                    depth=depth - 1, _seen=_seen,
                                    max_depth=max_depth))
    return out


LEVER_N = 1024


def lever_shares(actual: Cost, size_var: str,
                 n: int = LEVER_N) -> List[Tuple[str, float]]:
    """返回 `[(子项渲染文本, 占比)]`，按占比降序，取前三。

    占比 = 该子项在 n 处的值 ÷ 整棵 κ 在 n 处的值。
    **它是"这一项值多少"，不是"拿掉它能省多少"**——后者见 `explain_function` 的 ablation。
    """
    from cost_check import evaluate
    env = {size_var: n}
    try:
        whole = evaluate(actual, env)
    except Exception:                                  # noqa: BLE001
        return []
    if not whole or whole <= 0:
        return []
    out = []
    for term in subterms(actual):
        try:
            v = evaluate(term, env)
        except Exception:                              # noqa: BLE001
            continue
        if v > 0:
            out.append((term.render(), v / whole))
    out.sort(key=lambda kv: -kv[1])
    return out[:3]


# ---------------------------------------------------------------- 借用 ②：生效阈值
#
# Majdoddin 的核心主张："an asymptotic bound does not say from which input size"。
# 我们的判决是渐近的（O），但契约的**定义域是有限的**（`benchmodel.scale`）。
# 所以"达标"这两个字应该附带"从哪个 n 起"。
#
# **注意方向**：κ 是上界，所以算出来的 n0 是**真实阈值的下界**（偏小）。
# 这一点必须写在输出里，不能让人误以为是精确值。

THRESHOLD_GRID = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048,
                  4096, 8192, 16384, 32768, 65536, 131072, 262144)


def threshold_of(impl: Cost, target: Cost, size_var: str) -> Dict[str, Any]:
    """返回 `{n0, ratio, ok, skipped, reason}`。

    **为什么不是"逐点 κ ≤ T"**：判决是 big-O，**常数会被吸收**。
    `28n ≤ O(n)` 成立，但逐点 `28n ≤ n` 永远不成立——按逐点算会把
    每一个常数因子 >1 的函数都判成"永不达标"，那是错的。

    所以算的是**比值稳定点**：从哪个 n 起，`κ(n)/T(n)` 不再下降。

    **`T(n) = 0` 的点要跳过，不能整条判死**（2026-11-04 修）：
    `target = O(log n)` 在 `n = 1` 处就是 0；以前一句
    `if not b or b <= 0: return None` 让 `binary_search` / `sort_small`
    **完全算不出阈值且不吭声**。现在跳过并在 `skipped` 里报出来。

    网格采样，不是精确值——状态标签写 `estimated`。
    """
    from cost_check import evaluate
    ratios, skipped = [], 0
    for n in THRESHOLD_GRID:
        try:
            a = evaluate(impl, {size_var: n})
            b = evaluate(target, {size_var: n})
        except Exception as exc:                       # noqa: BLE001
            return {"n0": None, "ratio": None, "ok": False, "skipped": skipped,
                    "reason": f"{type(exc).__name__}: {exc}"}
        if not b or b <= 0:
            skipped += 1          # target 在这点上是 0（如 log2(1)），跳过而不是放弃
            continue
        ratios.append((n, a / b))
    if not ratios:
        return {"n0": None, "ratio": None, "ok": False, "skipped": skipped,
                "reason": "所有采样点上 target 都是 0"}
    final = ratios[-1][1]
    start_n, stable = ratios[-1][0], final
    for n, r in reversed(ratios):
        if abs(r - final) <= 0.01 * abs(final):
            start_n, stable = n, r
        else:
            break
    return {"n0": start_n, "ratio": stable, "ok": True, "skipped": skipped,
            "reason": ""}


def threshold_lines(ci: Any, fn: str, target: Optional[Cost], actual: Optional[Cost],
                    size_var: str) -> List[str]:
    """给**判为达标**的函数补一句：从哪个 n 起，常数因子是多少。

    **算不出来时必须说出来**（2026-11-04）：以前静默少一行，读者以为没有阈值这回事。
    """
    if target is None or actual is None:
        return ["阈值      **算不出来**：没有 target 或没有算出 κ（estimated）"]
    info = threshold_of(actual, target, size_var)
    if not info["ok"]:
        return [f"阈值      **算不出来**：{info['reason']}（estimated）"]
    n0, ratio, skipped = info["n0"], info["ratio"], info["skipped"]
    skip_note = f"；跳过 {skipped} 个 target=0 的点（如 log2(1)）" if skipped else ""
    if ratio < 0.01:
        return [f"阈值      从 n ≥ {n0} 起 κ/target ≈ {ratio:.1e} —— "
                f"**target 比实际松 100 倍以上，契约几乎不约束**（estimated{skip_note}）"]
    if ratio <= 1.0:
        return [f"阈值      从 n ≥ {n0} 起 κ 不超过 target，比值稳定在 {ratio:.2f}×"
                f"（estimated{skip_note}）"]
    return [f"阈值      从 n ≥ {n0} 起比值稳定在 **{ratio:.1f}×**"
            f"（big-O 吸收常数，故仍判达标；n 很小时比值可能更大{skip_note}）"]
