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

import math
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
    | `log2(s)`（含 `log2(sym)`） | 0（低于 size，排序时靠前） |
    | `size(x)` | 1 |
    | `sym(x)`（标量形参 `h` / `w` / `hi`，以及 `k(f)`） | 1（与尺寸量同阶） |
    | 加 / max | `max(两棵)` |
    | 乘 | `两棵之和` |
    | `par`（并行块） | `max(各任务)`——与判阶同口径（B-24） |
    | 分段 | 取所有段（含哨兵）里最大的 |

    **`sym` 必须算 1，不能当常数**（2026-11-10 修）：标量形参（`blur` 的 `h` / `w`）
    在 `Cost` 里是 `sym`，不是 `size`。以前这里只认 `size`，于是 `h×39` 的 `deg`
    落到最后的 `return 0` —— 支配项被指成一个常数 `4`，而杠杆却报 99.9% 在
    `h×(…)` 上，**三个数字互相打脸**。`k(f)`（函数参数的成本）同样不能当常数：
    它至少和一次调用同阶；`deg` 只用于排序，不参与判定。

    > 它只是**排序用的粗指标**，不是判定依据。判定永远是 `cost_check.compare`。
    """
    k = c.kind
    if k == "num":
        return 0
    if k in ("size", "sym"):
        return 1
    if k == "log2":
        # 包括 `log2(sym)`：与 `log2(size)` 同一档（低于尺寸量，排序时靠前）。
        return 0
    if k in ("add", "max"):
        return max(deg(c.args[0]), deg(c.args[1])) if len(c.args) >= 2 else 0
    if k == "mul":
        return deg(c.args[0]) + deg(c.args[1]) if len(c.args) >= 2 else 0
    if k == "par":
        # **不认 par 会把支配项指错**（2026-11-09）：`deg` 落到最后的 `return 0`，
        # 于是 `8 + par(n(xs)×7, …) + 8` 的支配项被指成常数 `8`，
        # 而杠杆却报 par 占 99.9% —— 两个数字互相打脸。
        return max([deg(t) for t in c.args] or [0])
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


#: 求值环境：**所有尺寸量都取 n**（与 `compare` 的"同规模假设"同一个口径）。
#
# 只把 `size_var` 塞进环境是不够的（2026-11-09 实测）：`evaluate` 对**没给的**
# `size` 按 1 算、对没给的 `sym` 按 ⊤（+inf）算。`blur` 的 κ 是
# `4 + h×(...) + w×16 + ...`，h / w 都是标量形参；只给 h 的话 w 求成 inf，
# 于是 `inf / inf = nan`，报告打出 `杠杆 nan%`（同一个 κ 的另一处却是
# `比值 ≈ 16.00`，因为 `compare` 的参考比值把所有尺寸量都设成了 n）。
# 两处必须用同一个口径，否则就是两个数字互相打脸。
def _env_for(c: Optional[Cost], size_var: str, n: int = 0) -> Dict[str, float]:
    if not n:
        n = LEVER_N                    # 常量在本函数之后定义，故此处解析
    env = {v: float(n) for v in (c.variables() if c is not None else [])}
    env[str(size_var)] = float(n)
    return env


def _finite_value(c: Optional[Cost], env: Dict[str, float]) -> Optional[float]:
    """求出一个**有限**的值；⊤ / inf / nan / 抛异常都返回 `None`。

    **这是"能不能给出数字"的唯一闸门**：杠杆百分比、剩余比例、阈值都过它。
    只要它返回 `None`，报告里就不许出现任何数值化的归因。
    """
    from cost_check import evaluate
    try:
        v = evaluate(c, env)
    except Exception:                                  # noqa: BLE001
        return None
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _why_no_share(actual: Optional[Cost], finite: Optional[float]) -> str:
    """杠杆算不出来时，说清**为什么**（而不是给一个数）。"""
    if actual is not None and actual.is_top():
        return "κ = ⊤，没有有限占比可言"
    if finite is None:
        return "κ 在该 n 处求不出有限值（⊤ / 分母为 0 / 未解释符号 k(f)）"
    if finite <= 0:
        return f"κ 在该 n 处为 {finite:g}（不是正的，占比无意义）"
    return "没有可分的子项"


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
    #: 求值环境（**所有尺寸量 = n**）。`finite` 是后面所有数字的闸门：
    #: 它是 `None` 时报告里一个数值归因都不许出现（⊤ / 除零 / 未解释符号）。
    env = _env_for(actual, size_var, size_n)
    finite = _finite_value(actual, env)
    lines.append(f"  实际      {actual.render()}      [proved：来自 κ 树结构；维度 {dim_zh}]")
    if finite is None:
        # **⊤ 没有"尺寸指数"**：报 0 等于说"它是常数"，正好与事实相反。
        lines.append("  支配项    —（κ 求不出有限值，不排支配项）")
    else:
        lines.append(f"  支配项    {dom.render()}      ← 尺寸指数 {deg(dom)}")
    # **杠杆定价**（借自 Majdoddin §3.9）：改哪里有用、哪里没用
    _lv = lever_shares(actual, size_var) if finite is not None else []
    if _lv:
        for _txt, _share in _lv:
            lines.append(f"  杠杆      {_share:6.1%}  {_txt[:56]}")
        _scale = ("（多尺寸量按同规模假设：所有尺寸量 = n）"
                  if len(env) > 1 else "")
        lines.append("            ↑ 占比是该子项在 n=%d 处占 κ 的比例；改占比大的才有效%s"
                     % (LEVER_N, _scale))
    else:
        lines.append(f"  杠杆      —    无法归因：{_why_no_share(actual, finite)}")

    # **硬闸门（2026-11-09）**：κ 求不出有限值时，**不许指认元凶、不许给建议**。
    # 以前这里会拿 `⊤` 和 `⊤` 相除打出 `nan%`，还会做 ablation —— 把 `⊤`
    # 换成常数 1 之后 `compare` 当然判"达标"，于是建议「删掉这一项即可达标」，
    # 元凶还被文本回找指到某个 `^` 分支（那个分支在 κ=⊤ 时根本没有意义）。
    # **不是沉默，是主动给错建议**——用户会照着删代码。所以这里直接返回。
    if finite is None:
        lines.append("  元凶      —（κ 整体求不出有限值，不指认单一构造）")
        _why = (getattr(ci, "top_reasons", None) or {}).get(fn)
        if _why:
            # 原因由 `CostInterp._note_tree_rec` 侧记（只读）：转发，不要编。
            lines.append(f"  原因      {_why}")
        return lines

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
            _left = _pct(cand, actual, env)
            if _left is None:
                # **不许把"求不出"印成 1.0（=100%）**：那正是"编一个数"。
                lines.append(f"  最小修复  {how}（剩余比例求不出："
                             f"原式在这个 n 上不是有限值）")
            else:
                lines.append(f"  最小修复  {how}（剩余是原来的 {_left:.0%}）")
            fixed = True
            break
        tried.append((t.render(), v2.kind))
    if not fixed:
        lines.append(f"  最小修复  **没有单一元凶**（试了 {len(tried)} 项都不够）"
                     f" —— 超界是累加导致的（`E-Cost-NoSingleBlame`）")
    return lines


# ---------------------------------------------------------------- 被迫的信任
#
# `ci.target_trusted`（`cost_domain/cost_interp.py::_note_target_trusted`）记的是
# **调用点没有推导值、只能按被调函数声明的 `target` 记账**的次数。两种情形：
#
#   · 被调函数**只有 spec、没有实现**（`no_impl`）；
#   · 被调函数递归、却没有 `recursion depth`（`recursion_no_depth`）——
#     展开自己会无穷递归，只能按它声明的那一层 target 记账。
#
# **这不是判定，也不改 κ**：它只是把「别人的承诺」从「算出来的上界」里分出来。
# 不说出来的后果很具体：用户看到 `κ ≤ 7n + 2`，会以为这整条都是工具推的；
# 其实其中一段是作者自己写的 target——**上界的一半没有被验证过**。
#
# 表格是按名字累计的（同一个被调函数被信任多次就是 2、3…），而且跨函数累计。
# 所以 CLI 侧用 `trusted_delta` 取「算这一个函数期间新增的部分」，逐函数标注。

#: 理由码 → 人读说明。JSON 用左边的码（稳定、机读），人读输出用右边。
TRUST_REASONS = {
    "no_impl": "只有 spec、没有实现",
    "recursion_no_depth": "递归但没有 `recursion depth`",
}


def trusted_delta(before: Optional[Dict[str, int]],
                  after: Optional[Dict[str, int]]) -> Dict[str, int]:
    """`after − before`（只留正增量）：这一次 `cost_of` 期间新增的信任项。

    `ci.target_trusted` 是**整个 CostInterp 累计**的（嵌套调用会往里加），
    直接拿它逐函数打印会把别人的账算到这个函数头上。CLI 在每次
    `cost_of(name)` 前后各取一次快照，差值才是「这个函数的 κ 里含的信任」。
    """
    out: Dict[str, int] = {}
    for nm, n in (after or {}).items():
        d = int(n) - int((before or {}).get(nm, 0))
        if d > 0:
            out[nm] = d
    return out


def trust_reason(ci: Any, callee: str) -> str:
    """这个被调函数为什么只能按 target 记账：`no_impl` / `recursion_no_depth`。

    **判据只有一条**：`ci.fns` 里有没有它的实现（那是 `cost_of` 能算的集合）。
    `_note_target_trusted` 只在「没有实现」或「递归但没有深度」两处登记，
    有实现且非递归的被调函数走的是 κ（见 `_call_inner` ①），不会进这张表。
    """
    return "no_impl" if callee not in (getattr(ci, "fns", None) or {}) \
        else "recursion_no_depth"


def target_trusted_entries(ci: Any, trusted: Optional[Dict[str, int]]
                           ) -> List[Dict[str, str]]:
    """机读版：`[{"callee": "inner", "reason": "no_impl"}]`（按名字排序，输出确定）。

    **只在非空时由调用方加键**——绝大多数模块没有这种调用点，JSON 形状不变。
    """
    return [{"callee": nm, "reason": trust_reason(ci, nm)}
            for nm in sorted(trusted or {})]


def target_trusted_lines(ci: Any, trusted: Optional[Dict[str, int]]) -> List[str]:
    """人读版：一行说清「κ 里有几项其实来自别人的声明」。没有就不打。"""
    trusted = {nm: n for nm, n in (trusted or {}).items() if n > 0}
    if not trusted:
        return []
    total = sum(trusted.values())
    parts = []
    for nm in sorted(trusted):
        why = TRUST_REASONS.get(trust_reason(ci, nm), "没有推导值")
        times = f" ×{trusted[nm]}" if trusted[nm] > 1 else ""
        parts.append(f"`{nm}` {why}{times}")
    return [f"其中 {total} 项按**声明的 target** 计（{'；'.join(parts)}）——不是推导值"]


def _pct(part: Cost, whole: Cost, env: Dict[str, float]) -> Optional[float]:
    """拿掉某一项后，剩下的占原来的多少（**估计值**；求不出返回 `None`）。

    **不再退回 `1.0`**：以前求值失败时返回 1.0，报告就印成
    「剩余是原来的 100%」——那是一个**看起来像结论的编造值**。
    """
    a = _finite_value(whole, env)
    b = _finite_value(part, env)
    if a is None or b is None or a <= 0:
        return None
    return max(0.0, min(1.0, b / a))


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
            # **高阶内建把函数名当实参**（`sort_by(xs, earlier)`）：
            # 那不是 call 节点，以前从"被调方"里漏掉——实测 merge 样例的
            # `earlier` 就这样消失了。这里把它补成一条调用。
            _an = next((c for c in (getattr(cur, "children", None) or [])
                        if getattr(c, "kind", "") == "args"), None)
            for _a in (getattr(_an, "children", None) or []):
                if getattr(_a, "kind", "") == "path":
                    calls.append(_a)
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
        # **所有尺寸量 = n**（与 `compare` 的参考比值同一口径）：只设 `size_var`
        # 会让含标量形参（`sym`）的被调方在这一步求成 inf，再被印成 `κ=inf`。
        v = _finite_value(ck, _env_for(ck, size_var, n))
        if v is None:
            # 求不出有限值 → 与 ⊤ 同等对待：**不印 inf**，也不参与"最贵"排序。
            rows.append((0.0, name, None, getattr(c, "line", None)))
            continue
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


def valued_terms(actual: Cost, size_var: str,
                 n: int = LEVER_N) -> Optional[List[Tuple[Cost, float, float]]]:
    """`[(子项, 在 n 处的值, 占 κ 的比例)]`，按值降序；算不出返回 `None`。

    **杠杆定价、界松判据、`--explain` 的占比共用这一个口径**（2026-11-10）：
    以前 `lever_shares` 自己摊平一遍、界松判据又要摊平一遍，两边迟早各算各的
    —— 那正是"两个数字互相打脸"的来源。

    **求不出有限值时返回 `None`，绝不返回 nan**（2026-11-09）：
    以前 `whole` 与 `term` 都可能是 ⊤（求值成 +inf），`inf / inf = nan`，
    报告就打出 `杠杆 nan%`。现在的闸门是：整棵树、**每一个**子项都必须是
    有限正数；有一个不是，就整段不给占比（调用方显示「—」）。
    """
    env = _env_for(actual, size_var, n)
    whole = _finite_value(actual, env)
    if whole is None or whole <= 0:
        return None
    out = []
    for term in subterms(actual):
        v = _finite_value(term, env)
        if v is None:
            # 有一项求不出有限值 ⇒ 占比不是"部分/整体"，整段作废。
            return None
        if v > 0:
            out.append((term, v, v / whole))
    if not out:
        return None
    out.sort(key=lambda t: -t[2])
    return out


def lever_shares(actual: Cost, size_var: str,
                 n: int = LEVER_N) -> List[Tuple[str, float]]:
    """返回 `[(子项渲染文本, 占比)]`，按占比降序，取前三。

    占比 = 该子项在 n 处的值 ÷ 整棵 κ 在 n 处的值。
    **它是"这一项值多少"，不是"拿掉它能省多少"**——后者见 `explain_function` 的 ablation。
    口径见 `valued_terms`（求不出有限值就是空表，不是 nan）。
    """
    got = valued_terms(actual, size_var, n)
    if got is None:
        return []
    return [(t.render(), share) for t, _v, share in got[:3]]


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

    **⊤ / 非有限值不许变出数字**（2026-11-09）：以前 `target` 是 ⊤ 时
    `b = +inf`，`a / inf = 0`，于是打出「target 比实际松 100 倍以上」——
    那是拿"无穷"当 0 用；`impl` 与 `target` 都求不出有限值时还会打出 `nan×`。
    现在两边逐点过 `_finite_value`，任何一边不是有限值就直接说"算不出来"。

    **多尺寸量**取**同规模假设**（所有尺寸量 = n），与 `compare` 的参考比值
    同一口径；返回值里带 `multi` 供调用方标注。
    """
    if target.is_top():
        return {"n0": None, "ratio": None, "ok": False, "skipped": 0, "multi": False,
                "reason": "target 是 ⊤（任何成本都满足），比值与生效阈值没有意义"}
    if impl.is_top():
        return {"n0": None, "ratio": None, "ok": False, "skipped": 0, "multi": False,
                "reason": "κ 是 ⊤，没有可比的有限值"}
    names = sorted(set(impl.variables()) | set(target.variables())
                   | {size_var})
    multi = len(names) > 1
    ratios, skipped = [], 0
    for n in THRESHOLD_GRID:
        env = {v: float(n) for v in names}
        a = _finite_value(impl, env)
        if a is None:
            return {"n0": None, "ratio": None, "ok": False, "skipped": skipped,
                    "multi": multi,
                    "reason": f"κ 在 n={n} 处求不出有限值（⊤ / 分母为 0）"}
        b = _finite_value(target, env)
        if b is None:
            return {"n0": None, "ratio": None, "ok": False, "skipped": skipped,
                    "multi": multi,
                    "reason": f"target 在 n={n} 处求不出有限值（⊤ / 分母为 0）"}
        if b <= 0:
            skipped += 1          # target 在这点上是 0（如 log2(1)），跳过而不是放弃
            continue
        r = a / b
        if math.isnan(r) or math.isinf(r):
            return {"n0": None, "ratio": None, "ok": False, "skipped": skipped,
                    "multi": multi,
                    "reason": f"κ/target 在 n={n} 处求不出有限值"}
        ratios.append((n, r))
    if not ratios:
        return {"n0": None, "ratio": None, "ok": False, "skipped": skipped,
                "multi": multi,
                "reason": "所有采样点上 target 都是 0"}
    final = ratios[-1][1]
    start_n, stable = ratios[-1][0], final
    for n, r in reversed(ratios):
        if abs(r - final) <= 0.01 * abs(final):
            start_n, stable = n, r
        else:
            break
    return {"n0": start_n, "ratio": stable, "ok": True, "skipped": skipped,
            "multi": multi, "reason": ""}


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
    if info.get("multi"):
        skip_note += "；多尺寸量按同规模假设（所有尺寸量 = n）"
    if ratio < 0.01:
        return [f"阈值      从 n ≥ {n0} 起 κ/target ≈ {ratio:.1e} —— "
                f"**target 比实际松 100 倍以上，契约几乎不约束**（estimated{skip_note}）"]
    if ratio <= 1.0:
        return [f"阈值      从 n ≥ {n0} 起 κ 不超过 target，比值稳定在 {ratio:.2f}×"
                f"（estimated{skip_note}）"]
    return [f"阈值      从 n ≥ {n0} 起比值稳定在 **{ratio:.1f}×**"
            f"（big-O 吸收常数，故仍判达标；n 很小时比值可能更大{skip_note}）"]


# ---------------------------------------------------------------- W-Target-Loose
#
# ## 它要抓的是什么（一句话）
#
# > **判据比的是「我的静态估计 κ 的阶 ≤ 你写的 target 的阶」，不是「真实成本 ≤ target」。**
# > 于是 κ 一旦比真实松，**用户想写真实的紧界反被判债务**，只能写一条和 κ 一样松的 target
# > 才过——"被迫写松的 target"是这么来的。
#
# ## 两种"松"，必须分开（这是本告警唯一的技术难点）
#
# | 因 | 现象 | 能不能静态证 | 本告警 |
# |---|---|---|---|
# | **(a) 常数项松** | `κ = n×1447 + 27790173`：比值 39 万倍，**阶没错**，是常数项撑的 | **能** | ✅ 报 |
# | **(b) 阶错了** | κ 的阶**真的高于**真实阶（模型粗：把 log 记成 n、把 n log n 记成 n²） | **不能**（"真实阶"要实测） | ❌ 不报，只在文案里区分并指路 |
#
# 为什么 (a) 能静态证：判据只看**阶**，所以 κ 里**任何不随 n 增长的部分都是纯粹的余额**——
# 它永远不会把判定推成债务，却能把"上界"撑到离真实十万八千里。判据就是找这种余额：
#
#   在参考规模 `LEVER_N` 处，κ 的**数值最大子项**如果本身**不随 n 增长**（`order_profile == 0`）
#   且占 κ 的一半以上，那么 target 要求的"随 n 增长"**完全是被这个常数满足的**——这个 O(…)
#   对"规模变大"什么都没说。
#
# 为什么 (b) 不能静态证：`κ` 和 `target` 都在静态侧，两边都可能是错的；
# "κ 的阶高于真实阶"这句话里的"真实"只有实测（`interp` 的 ops 计数）能给。
# 它的实测影子是**比值随 n 发散**（`κ÷ops` 一路上涨），工具是
# `tools/tightness_compare.py`。所以本告警不在静态侧冒充 (b)。
#
# ## 为什么不会误报
#
# - **债务不报**：`debt` 的 target 是**更紧**的那一侧（方向相反），不在本告警的范围。
# - **target 本来就承诺常数不报**：`O(1)` 的 target 没有"随 n 增长"可被常数冒充。
# - **支配项本来就随 n 增长不报**：`merge`（`n log n×10` 占 99%）/ `count`（`n×21`）都是这种。
# - **算不出有限值不报**：⊤ / nan / 除零一律沉默（`valued_terms` 返回 `None`）。
#
# ## 硬约束
#
# > **本告警不参与任何算术、不改任何判定**：它只读 `compare` 已经判好的结论、
# > 只读 `valued_terms` 的占比。`ok` / `debt` / `unknown` 一个都不许变。

#: 告警码。`W-` 前缀 = 警告：`check` 只提示、不让构建变红（`CheckResult.ok` 只数 error）。
TARGET_LOOSE_CODE = "W-Target-Loose"

#: 常数项占 κ 的比例门槛。取 50%：**过半**才叫"被常数支配"；
#: 低于一半时 κ 的增长仍然主导，说"界松"就是夸大。
LOOSE_SHARE_GATE = 0.5


def target_loose_report(ci: Any, fn: str, target: Optional[Cost], actual: Optional[Cost],
                        size_var: str, n: int = 0,
                        src_lines: Optional[List[str]] = None
                        ) -> Optional[Dict[str, Any]]:
    """`W-Target-Loose` 的**唯一判据**：这条 target 是被一个常数项满足的（因 (a)）。

    **不该报时返回 `None`**（不是返回"没松"的一句话——沉默就是没松）。

    返回值（两个调用方共用同一份依据，不各写一套文案）：

    | 键 | 用途 |
    |---|---|
    | `headline` | 一句话结论（`cost` 印在码后面；`check` 的诊断正文） |
    | `detail`   | 一句"改哪儿"（`check` 用；`cost` 用下面更详细的 `lines`） |
    | `lines`    | 逐行明细（`判据 / 数字 / 改哪儿 / 另一回事`） |
    | `share`、`ratio` | 判据用到的两个数（测试要能核对，不许重新算） |

    `n` 默认 `LEVER_N`（与杠杆同一口径：所有尺寸量都取同一个 n）。
    `src_lines` 只在 `--explain` 时给（要读实现文件），没有就少一段行号文本。
    """
    if target is None or actual is None:
        return None
    if not n:
        n = LEVER_N                     # 常量在本函数之后定义，故此处解析

    from cost_check import compare, order_profile

    # ① **方向**：只有"判达标"才可能是界松。债务/未知的 target 是更紧的一侧。
    try:
        _v = compare(actual, target)
    except Exception:                                  # noqa: BLE001
        return None
    if _v.kind != "ok":
        return None

    # ② **target 必须承诺了"随 n 增长"**，否则没有东西能被常数冒充（`O(1)` 的 target 不报）。
    try:
        tp = order_profile(target, size_var)
    except Exception:                                  # noqa: BLE001
        return None
    if tp is None or (tp[0] == 0 and tp[1] == 0 and tp[2] == 0):
        return None

    # ③ **κ 在该规模上被一个不随 n 增长的常数项支配**（占了过半）。
    got = valued_terms(actual, size_var, n)
    if not got:
        return None
    term, term_val, share = got[0]
    if share < LOOSE_SHARE_GATE:
        return None
    try:
        op = order_profile(term, size_var)
    except Exception:                                  # noqa: BLE001
        return None
    if op is None or op[0] or op[1] or op[2]:
        return None            # 支配项自己随 n 增长 ⇒ 界不松（merge / count 都落在这里）

    env = _env_for(actual, size_var, n)
    whole = _finite_value(actual, env)
    tgt_val = _finite_value(target, env)
    ratio = None
    if whole is None or tgt_val is None or tgt_val <= 0:
        # 数字给不出就**不给**——宁少一行，不许编。
        num_txt = f"  数字      κ(n={n}) 或 target(n={n}) 求不出有限值，比值不给"
    else:
        ratio = whole / tgt_val
        ratio_txt = (f"是 target 的 **{ratio:,.0f} 倍**" if ratio >= 1.0 else
                     f"比 target 还小（target 是它的 {1.0 / ratio:,.0f} 倍，**两边都松**）")
        num_txt = (f"  数字      κ(n={n})={whole:,.0f}，{ratio_txt}；"
                   f"不随 n 增长的那一项自己占 {share:.1%}")

    kp_txt = "（判不出）"
    kp = order_profile(actual, size_var)
    if kp is not None:
        kp_txt = f"n^{kp[0]}"
        if kp[1]:
            kp_txt += f"·log^{kp[1]}"
        if kp[2]:
            kp_txt += f"·loglog^{kp[2]}"

    # 支配项（按**尺寸指数**）的溯源：这是"改 κ 从哪看"的那一句。
    where = ""
    try:
        dom = dominating(actual, getattr(ci, "origins", {}) or {})
        if dom is not None:
            o = _origin_of(getattr(ci, "origins", {}) or {}, dom)
            if o:
                where = "；支配项在 " + _fmt_origin(o, src_lines)
    except Exception:                                  # noqa: BLE001
        where = ""

    headline = (f"界松（**不是实现慢**）：n={n} 处 κ 有 {share:.1%} 来自"
                f"**不随 n 增长**的常数项 `{_brief(term.render(), 40)}`，"
                f"而 target 承诺的是随 n 增长的上界。")
    fix = ("  改哪儿    **改 κ 这一侧，不要为迁就它去放宽 target**——"
           "把 target 写得更松就等于把\"被迫写松\"落实成事实") + where
    lines = [
        f"  判据      κ 的阶是 {kp_txt}，判据只比阶 ⇒ 这个常数项**永远不会**被推成债务，"
        f"它只是把上界撑离真实；target 要求的\"随 n 增长\"其实是被常数满足的。",
        num_txt,
        fix,
        "  另一回事  「κ 的阶高于真实阶」是**另一个**因，静态证不了："
        "看 κ÷ops 是否随 n 发散（`python -B tools/tightness_compare.py`）",
    ]
    return {
        "code": TARGET_LOOSE_CODE,
        "headline": headline,
        "detail": fix.strip(),
        "lines": lines,
        "share": share,
        "ratio": ratio,
        "n": n,
    }


def target_loose_lines(ci: Any, fn: str, target: Optional[Cost], actual: Optional[Cost],
                       size_var: str, n: int = 0,
                       src_lines: Optional[List[str]] = None) -> List[str]:
    """`cost` 用的逐行形式：`[码 + 结论] + 明细`；不该报时 `[]`。

    只是 `target_loose_report` 的排版，**判据仍在那一处**（不复制条件）。
    """
    r = target_loose_report(ci, fn, target, actual, size_var, n, src_lines)
    if r is None:
        return []
    return [f"{r['code']}  {r['headline']}"] + r["lines"]
