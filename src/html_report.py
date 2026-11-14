#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""html_report.py —— `elench export <模块> --full --out=报告.html` 的**静态归档页**。

## 它是什么，不是什么

| | 是什么 | 不是什么 |
|---|---|---|
| **本文件（`.html`）** | **给人看的归档产物**：一页一模块、自包含、可贴 PR / 进 CI artefact | —— |
| `elench otel dashboard` | **副产品**：边推 OTel 边刷新，适合盯现场 | 不是归档件（自刷新、要跑服务端） |

**不写第二套渲染逻辑**：曲线仍然由 `src/chart.py` 的 `line_chart()` 画
（`otel.to_svg` 与 `export --full` 出 `.svg` 用的是同一个函数），
本文件只负责「把已有的数据排进 HTML 壳子」：数据全部来自 `dsh._full_report`
的同一份 `rows`（上界 / 实测 / 承诺上限），外加 `cost_interp` 的 κ 表达式与
`cost_check.compare` 的判决。

## 三条硬规则（否则这个报告会骗人）

1. **「证明」与「符合」分开写**：κ 是**证明**（任何 n 都成立）；实测点是
   **符合**（只覆盖测过的区间）。卡片上用**不同的词 + 不同的颜色**
   （κ 蓝、实测红，与 `chart.COLORS` 同色），并且**不许合成一个「通过」**。
2. **实测点数 < 4 时不画趋势线**：只画点，并写明「点数不足，不作趋势判断」。
   实现方式是给 `chart.line_chart` 传 `dots_only={实测系列名}`——
   `chart.py` 只多了一个默认空的可选参数，**它原来的 `.svg` 输出逐字节不变**。
3. **两个时间戳**：`κ / 判决` 是「刚才（本地静态）」，`实测` 是「哪份数据、
   什么时候」。没有实测数据时**明说「未测量」**，不留空。
4. **两处可读性打磨**（2026-11-10）：
   - **κ 的分段折叠**：分段主体**逐字符相同**时折成「分段：… / 主体（三段相同）：…」，
     原文仍留在 `<details>` 里（可复制）。**判据保守**：有任何一段不同就原样打印
     ——把两个不同的式子说成"相同"，比让卡片长三行坏得多。
   - **比值必须带方向**：`κ = O(n)` 对 `target = O(n²)` 的比值本来就趋近 0，
     只印 `比值 ≈ 0.00` 读起来像程序出错。现在 `< 0.01` 说「远低于（好）」，
     `0.01–1` 正常给数值，`> 1` 用**红色块**明写「κ 超过 target」。

## 自包含

单文件、**零外链**：CSS 内联、SVG 内联（`xmlns` 与 XML 声明在嵌入时去掉——
内联 `<svg>` 不需要它们，去掉才能保证整页 `grep -c "https\\?://"` 为 0）、
字体只用系统字体栈（`ui-monospace, Consolas` / `system-ui`）。
本机没有外网也能打开。

## 确定性

同一份输入两次生成**逐字节相同**；唯一的变量是时间戳，所以它有两个出口：
`--now=<字符串>`（原样写入，便于复现）与 `--no-timestamp`（写成固定标记）。
`tests/cases/test_cli_smoke.py` 里有一条把两次输出按字节比的断言。
"""
from __future__ import annotations

import math
import os
import re

#: 单文件体积期望上界（20 个函数左右的模块）。超了就在测试里红，别悄悄膨胀。
SIZE_BUDGET_KB = 200

#: `chart.line_chart()` 的整页 SVG 带 XML 声明与 `xmlns`；内联进 HTML 时两者都多余。
_XML_PROLOG = re.compile(r"^<\?xml[^>]*\?>\s*")
_XMLNS = re.compile(r'\s*xmlns="[^"]*"')
_SVG_WH = re.compile(r'width="(\d+)" height="(\d+)"')

#: 判决口径与 `elench build` 的**时间维度**一致（`cost_check.compare`）。
_VERDICT_LABEL = {"ok": "ok（达成）", "debt": "debt（超出）",
                  "unknown": "unknown（无法判定）"}

#: κ 系列 / 实测系列在图例里的名字。**带「证明」「符合」两个字**是故意的：
#: 图例本身就要说清这两条线不是同一类陈述。
KAPPA_SERIES = "κ（证明·任何 n 成立）"
MEAS_SERIES = "实测（符合·仅测过的区间）"


# ---------------------------------------------------------------- 转义

def _esc(s) -> str:
    """HTML 转义。**所有动态文本都必须过这里**（κ 表达式里有 `<` `>` `≤`，
    函数名/诊断消息里可能有 `&`）。裸字符串拼接是这份报告最容易骗人的地方。"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;")
            .replace("'", "&#39;"))


def _md(s) -> str:
    """转义后再把 `**…**` 变成 `<strong>`（`Verdict.render()` 的原文带它）。

    **先转义再替换**：反过来的话用户文本里的 `**` 会插进标签。
    """
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", _esc(s))


# ---------------------------------------------------------------- 小工具

def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def _num(x, dash: str = "—") -> str:
    """整数千分位；不是有限数就写 `—`（**不许**把 `inf` / `nan` 印成数字）。"""
    return f"{float(x):,.0f}" if _finite(x) else dash


def _ratio_text(r) -> str:
    return f"{float(r):.2f}" if _finite(r) else "—"


def _frac(x) -> str:
    try:
        num, den = x.numerator, x.denominator
    except AttributeError:
        return str(x)
    return str(num) if den == 1 else f"({num}/{den})"


def _order_text(cost) -> str:
    """成本式 → `O(n^2)` 这样的**摘要**（只给人看，判决从不读它）。

    判不了（多尺寸量 / 家族外 / ⊤）就返回空串——**不猜**。
    """
    if cost is None:
        return ""
    try:
        from cost_check import order_profile
        vs = cost.variables()
        if len(vs) != 1:
            return ""
        prof = order_profile(cost, vs[0])
    except Exception:                                   # noqa: BLE001
        return ""
    if prof is None:
        return ""
    exp, logs, loglogs = prof
    bits = []
    if exp:
        bits.append("n" if exp == 1 else f"n^{_frac(exp)}")
    if logs:
        bits.append("log2(n)" if logs == 1 else f"log2(n)^{logs}")
    if loglogs:
        bits.append("loglog(n)" if loglogs == 1 else f"loglog(n)^{loglogs}")
    return "O(" + "·".join(bits) + ")" if bits else "O(1)"


# ---------------------------------------------------------------- κ 折叠

#: 段数 → 中文数字。**只为零点几行**：`主体（三段相同）` 比 `主体（3 段相同）`
#: 读着像中文；超出表就退回阿拉伯数字，不硬编。
_CN_NUM = {2: "两", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八",
           9: "九", 10: "十"}


def _seg_label(var, lo, hi, big) -> str:
    """一段 → 人读的分界。**与 `symbolic.render` 的哨兵口径一致**：
    哨兵段不印天文数字，印 `n(x) < k` / `n(x) > k`。"""
    if lo <= -big // 2:
        return f"n({var}) < {hi + 1}"
    if hi >= big // 2:
        return f"n({var}) > {lo - 1}"
    return f"{lo} ≤ n({var}) ≤ {hi}"


def _piece_parts(cost):
    """`piece` 节点 → (分界标签, 主体文本) 或 None。

    **只认「完整覆盖」的分段**：哨兵不足时 `symbolic.render` 会多印一段
    `；越界: <回退式>`，那说明分段之外还有东西，折叠起来会漏掉它。
    """
    if cost is None or getattr(cost, "kind", None) != "piece":
        return None
    try:
        from cost_domain.symbolic import PIECE_BIG
        var, segs = cost.value
        labels, bodies = [], []
        below = above = False
        for lo, hi, c in segs:
            labels.append(_seg_label(var, lo, hi, PIECE_BIG))
            bodies.append(c.render())
            below = below or lo <= -PIECE_BIG // 2
            above = above or hi >= PIECE_BIG // 2
    except Exception:                                   # noqa: BLE001
        return None
    if len(bodies) < 2 or not (below and above):
        return None
    return labels, bodies


def _fold_kappa(cost):
    """→ `{"labels": [...], "body": ..., "n": k}` 或 None（不折叠）。

    **判据：全部主体逐字符相同**（`len(set(bodies)) == 1`）。不许做任何
    "差不多""只差常数"的判断——把两个不同的式子折叠成同一个，是在报告里
    造一个不存在的等式，比让卡片长三行坏得多。
    """
    parts = _piece_parts(cost)
    if parts is None:
        return None
    labels, bodies = parts
    if len(set(bodies)) != 1:
        return None
    return {"labels": labels, "body": bodies[0], "n": len(bodies)}


# ---------------------------------------------------------------- 比值方向

#: 比值三档。**档位决定排版**：`over` 是异常（红色块，不能藏在小字里），
#: `far` 是好消息（绿色），`in` 正常给数值。
RATIO_FAR, RATIO_IN, RATIO_OVER = "far", "in", "over"

#: `compare` 自带的那半句：`，比值 ≈ 0.00` / `；参考比值 ≈ 2.3e+14`。
_RATIO_FRAGMENT = re.compile(r"[，；](?:参考)?比值 ≈ [0-9][0-9.eE+-]*")


def _ratio_shape(kappa, target) -> str:
    """κ/target 的**阶形状**（`1/n` 之类），判不出来就空串。

    只给「远低于」那一档当"约 …"用：那时数值已经小到 `0.00`，形状才是
    读者真正需要的信息。**不同尺寸量 / 有对数因子 / 判不出阶 → 空串**，
    绝不硬猜。
    """
    if kappa is None or target is None:
        return ""
    try:
        from cost_check import order_profile
        kv, tv = kappa.variables(), target.variables()
        if len(kv) != 1 or len(tv) != 1 or kv[0] != tv[0]:
            return ""
        kp, tp = order_profile(kappa, kv[0]), order_profile(target, tv[0])
    except Exception:                                   # noqa: BLE001
        return ""
    if kp is None or tp is None:
        return ""
    (ke, kl, kll), (te, tl, tll) = kp, tp
    if kl != tl or kll != tll:
        return ""                   # 对数因子不同，不是纯幂，别概括
    d = ke - te
    if d == 0:
        return "常数"
    a = abs(d)
    if a.denominator == 1:
        base = "n" if a == 1 else f"n^{a.numerator}"
    elif a.numerator == 1 and a.denominator == 2:
        base = "sqrt(n)"
    else:
        base = f"n^({a.numerator}/{a.denominator})"
    if d > 0:
        return base
    return "1/n" if base == "n" else f"1/{base}"


def _ratio_line(ratio, shape: str = "", verdict=None):
    """κ/target → (档位, 一句话)。**方向必须说出来**。

    只有数没有方向，`0.00` 会被读成"算错了"；而 `> 1` 时只写一行小字，
    等于把债务藏起来。
    """
    if not _finite(ratio):
        return "", ""
    r = float(ratio)
    if r < 0.01:
        tail = f"（约 {shape}）" if shape else f"（比值 {r:.1e}）"
        return RATIO_FAR, f"κ 远低于 target{tail}：这是好消息——界比承诺紧得多"
    if r <= 1.0:
        return RATIO_IN, f"κ 不高于 target（比值 ≈ {r:.2f}）"
    note = f"κ 超过 target（比值 ≈ {r:.2f}）"
    if verdict == "debt":
        note += "：这是债务，必须处理"
    elif verdict == "ok":
        note += "：阶仍达成（big-O 吸收常数），但数值确实超了，不能当成没看见"
    else:
        note += "：这是异常，必须查清"
    return RATIO_OVER, note


def _strip_ratio(text: str) -> str:
    """去掉 `compare` 自带的那半句数字比值（**换成带方向的措辞**）。

    只删数字那一截，**保留后面可能跟着的同规模假设注**——那是判据口径，
    换措辞不能把它丢掉。匹配不到就原样返回，不猜。
    """
    return _RATIO_FRAGMENT.sub("", str(text or ""), count=1)


def _slug(name: str, idx: int) -> str:
    s = re.sub(r"[^0-9A-Za-z_.-]+", "-", str(name)).strip("-")
    return f"fn-{s}" if s else f"fn-{idx}"


def _inline_svg(svg: str) -> str:
    """整页 SVG → **可内联的片段**：去 XML 声明、去 `xmlns`、尺寸换 `viewBox`。

    为什么要换：卡片宽度是流式的，固定 `width="820" height="480"` 在窄屏上
    要么被裁、要么撑破版式；`viewBox` + CSS `width:100%` 才是等比缩放。
    """
    s = _XML_PROLOG.sub("", (svg or "").strip())
    m = _SVG_WH.search(s)
    if m:
        w, h = m.group(1), m.group(2)
        s = s[:m.start()] + f'viewBox="0 0 {w} {h}" width="100%"' + s[m.end():]
    return _XMLNS.sub("", s, count=1)


# ---------------------------------------------------------------- 数据

def _module_pair(prog: str):
    """`prog` → (tu 路径, eln 路径)。口径与 `kappa_vs_measured.measure_one` 一致。"""
    prog = str(prog)
    if prog.endswith(".tu") or os.path.sep in prog or "/" in prog:
        base = prog[:-3] if prog.endswith(".tu") else prog
        return base + ".tu", base + ".eln"
    from kappa_vs_measured import SAMPLES
    return (os.path.join(SAMPLES, prog + ".tu"),
            os.path.join(SAMPLES, prog + ".eln"))


def _load_interp(prog: str):
    """→ (CostInterp 或 None, 失败原因)。**分档表走同一个 `policy.table_path`**：
    报告里的 κ 必须与实测用的是同一份分段，否则两边量的不是同一个实现。"""
    try:
        import io

        from cost_interp import CostInterp
        from parser import parse_source
        from policy import table_path

        tu_p, ch_p = _module_pair(prog)
        if not (os.path.exists(tu_p) and os.path.exists(ch_p)):
            return None, f"找不到模块文件：{tu_p}"
        tu = parse_source(io.open(tu_p, encoding="utf-8").read(),
                          os.path.basename(tu_p)).module
        ch = parse_source(io.open(ch_p, encoding="utf-8").read(),
                          os.path.basename(ch_p)).module
        tbl = table_path(os.path.dirname(os.path.abspath(tu_p)),
                         os.path.basename(tu_p).rsplit(".", 1)[0])
        return CostInterp(ch, tu,
                          table_path=(tbl if os.path.exists(tbl) else None)), ""
    except Exception as exc:                            # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _points(rs) -> list:
    """同一函数的实测行 → 按 n 升序的点。**只用 rows**，不另算一遍（同源）。"""
    out = []
    for r in rs or []:
        n = r.get("size")
        if n is None:
            continue
        out.append({"n": int(n), "kappa": r.get("bound"), "ops": r.get("typical"),
                    "target": r.get("target")})
    out.sort(key=lambda p: p["n"])
    return out


def _chart_svg(fn: str, kappa_pts, meas_pts):
    """→ (内联 SVG 片段 或 None, 说明 或 None)。

    `kappa_pts` / `meas_pts` 都接受同一种点字典（含 `n` / `kappa` / `ops`），
    这样调用方传同一份 points 两次即可；分开两个参数只是为了测试能单独造数据。

    **规则 2 就落在这里**：实测点 < 4 时给 `chart.line_chart` 传 `dots_only`，
    κ 仍是线（它是证明，不是拟合），实测只有孤立的点 + 一句「不作趋势判断」。
    """
    from chart import line_chart

    k = [(p["n"], p["kappa"]) for p in kappa_pts if _finite(p["kappa"])
         and float(p["kappa"]) > 0]
    m = [(p["n"], p["ops"]) for p in meas_pts if _finite(p["ops"])
         and float(p["ops"]) > 0]
    notes = []
    if not k and not m:
        return None, ("没有可画的数据：κ 与实测都为空"
                      "（κ = ⊤ 时没有曲线，未测量时没有点）。")
    series = []
    if k:
        series.append((KAPPA_SERIES, k))
    elif kappa_pts:
        notes.append("κ = ⊤（上界判不出来），所以没有 κ 线。")
    dots = set()
    if m:
        series.append((MEAS_SERIES, m))
        if len(m) < 4:
            dots.add(MEAS_SERIES)
            notes.append(f"实测只有 {len(m)} 个点（< 4）：只画点、不画连线——"
                         f"点数不足，不作趋势判断。")
    else:
        notes.append("**未测量**：这张卡片没有任何实测点，"
                     "图上只有 κ（证明），没有「符合」这一半。")
    svg = line_chart(series, f"{fn}：κ 与实测（双对数）", "操作数",
                     log_y=True, dots_only=dots)
    return _inline_svg(svg), "".join(notes) if notes else None


def make_card(fn: str, kappa, target, points, runs: int, note: str = "") -> dict:
    """一条函数的卡片数据（纯数据，渲染在 `render()` 里）。"""
    verdict, vtext, kratio = None, "", None
    if kappa is not None and target is not None:
        try:
            from cost_check import compare
            v = compare(kappa, target)
            verdict, vtext = v.kind, v.render()
            kratio = getattr(v, "ratio", None)
        except Exception as exc:                        # noqa: BLE001
            verdict, vtext = "unknown", f"无法判定：比较失败（{type(exc).__name__}）"
    elif target is None:
        vtext = ("无法判定：这个函数没有 target（未声明契约），"
                 "或者模块解析失败——没有可比的承诺。")
    if note:
        vtext = (vtext + "；" if vtext else "") + note

    # **比值换成带方向的措辞**：原来 `compare.render()` 只给 `比值 ≈ 0.00`，
    # 读者无法分辨"紧得多"和"算错了"。数字仍来自 `compare` 的 `ratio`，
    # 这里只换说法，不重算。
    ratio_level, ratio_note = _ratio_line(kratio, _ratio_shape(kappa, target),
                                          verdict)
    if ratio_note:
        vtext = _strip_ratio(vtext)

    for p in points:
        kap, ops = p.get("kappa"), p.get("ops")
        p["ratio"] = ((float(kap) / float(ops))
                      if (_finite(kap) and _finite(ops) and float(ops) > 0
                          and float(kap) < 1e18) else None)

    svg, chart_note = _chart_svg(fn, points, points)
    return {
        "fn": str(fn),
        "target_expr": target.render() if target is not None else "",
        "target_order": _order_text(target),
        "kappa_expr": kappa.render() if kappa is not None else "",
        "kappa_order": _order_text(kappa),
        "kappa_fold": _fold_kappa(kappa),
        "verdict": verdict,
        "verdict_text": vtext,
        "ratio_level": ratio_level,
        "ratio_note": ratio_note,
        "points": points,
        "measured": any(p.get("ops") is not None for p in points),
        "svg": svg,
        "chart_note": chart_note,
        "has_target": target is not None,
    }


def collect(prog: str, rows, runs: int = 20):
    """→ (cards, undeclared, load_note)。

    `undeclared` 是**解析成功但没写 target** 的函数——它们照样有 κ（一个上界），
    只是「符不符合」无从谈起。把它们列出来，比让它们消失诚实。
    """
    by_fn = {}
    for r in rows or []:
        by_fn.setdefault(str(r.get("function") or "?"), []).append(r)
    for v in by_fn.values():
        v.sort(key=lambda r: r.get("size") or 0)

    ci, err = _load_interp(prog)
    if ci is None:
        note = f"模块没能解析（{err}）：本页只剩实测数字，没有 κ 表达式与判决。"
        cards = [make_card(fn, None, None, _points(rs), runs)
                 for fn, rs in sorted(by_fn.items())]
        return cards, [], note

    cards, undeclared = [], []
    for fn in sorted(ci.fns):
        try:
            kappa = ci.cost_of(fn)
        except Exception:                               # noqa: BLE001
            kappa = None
        try:
            target = ci.target_cost(fn)
        except Exception:                               # noqa: BLE001
            target = None
        pts = _points(by_fn.get(fn, []))
        if target is None:
            undeclared.append({
                "fn": str(fn),
                "kappa_expr": kappa.render() if kappa is not None else "",
                "kappa_order": _order_text(kappa),
                "points": len(pts),
            })
            continue
        cards.append(make_card(fn, kappa, target, pts, runs))
    return cards, undeclared, ""


# ---------------------------------------------------------------- 渲染

_CSS = """
:root{color-scheme:light}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:#f6f7f9;color:#1b1f24;font-size:14px;line-height:1.55;
 font-family:"Segoe UI",system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
code,.mono{font-family:ui-monospace,Consolas,"Courier New",monospace;
 font-size:12.5px;background:#eef1f5;border-radius:4px;padding:1px 5px;word-break:break-word}
.wrap{max-width:1180px;margin:0 auto;padding:26px 18px 60px}
h1{font-size:21px;margin:0 0 4px;font-weight:650}
h1 .mod{font-family:ui-monospace,Consolas,"Courier New",monospace;color:#1f5fa8}
.sub{color:#5b6572;font-size:12.5px;margin:0 0 16px}
.stamps{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 18px}
.stamp{flex:1 1 300px;background:#fff;border:1px solid #e3e7ec;border-left-width:4px;
 border-radius:6px;padding:8px 12px}
.stamp.s1{border-left-color:#1f5fa8}
.stamp.s2{border-left-color:#c0392b}
.stamp .k{color:#5b6572;font-size:12px;letter-spacing:.02em}
.stamp .v{display:block;font-family:ui-monospace,Consolas,"Courier New",monospace;
 font-size:14px;color:#1b1f24;margin:1px 0}
.stamp .n{display:block;color:#5b6572;font-size:12px}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 18px}
.stat{flex:1 1 120px;background:#fff;border:1px solid #e3e7ec;border-radius:6px;
 padding:8px 12px;text-align:center}
.stat b{display:block;font-size:20px;font-weight:650}
.stat.ok b{color:#2e7d32}.stat.debt b{color:#b45309}
.stat.unknown b{color:#6b7280}.stat.none b{color:#8a8f98}
.stat span{color:#5b6572;font-size:11.5px}
.rules{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px;
 margin:0 0 22px}
.rule{background:#fff;border:1px solid #e3e7ec;border-radius:6px;padding:9px 12px;
 font-size:12.5px;color:#3d4650}
.rule b{font-weight:650}
.rule.proof{border-left:4px solid #1f5fa8}
.rule.fit{border-left:4px solid #c0392b}
.rule.proof b{color:#1f5fa8}.rule.fit b{color:#c0392b}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:14px}
.fn{background:#fff;border:1px solid #e3e7ec;border-radius:8px;padding:12px 14px;
 display:flex;flex-direction:column;gap:8px}
.fn h2{font-size:15px;margin:0;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.fn h2 code{background:#eef1f5;font-size:14px}
.badge{font-family:ui-monospace,Consolas,"Courier New",monospace;font-size:11.5px;
 border-radius:999px;padding:1px 9px;border:1px solid transparent}
.badge.ok{background:#e8f5e9;color:#2e7d32;border-color:#bfe0c2}
.badge.debt{background:#fdf1e3;color:#b45309;border-color:#f0d3ae}
.badge.unknown{background:#eef0f3;color:#5b6572;border-color:#dde1e6}
.badge.none{background:#f2f3f5;color:#8a8f98;border-color:#e3e7ec}
dl.kv{display:grid;grid-template-columns:5.6em 1fr;gap:4px 10px;margin:0;align-items:start}
dl.kv dt{color:#5b6572;font-size:12.5px}
dl.kv dd{margin:0;min-width:0}
.tag{font-size:10.5px;border-radius:3px;padding:0 4px;margin-left:4px;border:1px solid}
.tag.proof{color:#1f5fa8;border-color:#bcd0e6;background:#eef4fa}
.tag.fit{color:#c0392b;border-color:#eec4c0;background:#fbeeed}
.order{color:#5b6572;font-size:12px;margin-left:6px}
.verdict.ok{color:#2e7d32}.verdict.debt{color:#b45309}.verdict.unknown{color:#6b7280}
.verdict b{font-family:ui-monospace,Consolas,"Courier New",monospace}
.ratio.far{display:block;color:#2e7d32}
.ratio.in{color:#3d4650}
.ratio.over{display:block;margin-top:4px;background:#fdecea;color:#b3261e;
 border:1px solid #f3b6b0;border-radius:4px;padding:3px 8px;font-weight:650}
.kap-fold{display:flex;flex-direction:column;gap:2px}
.kap-fold .seg,.kap-fold .body{line-height:1.8}
.kap-fold .lbl{color:#5b6572}
details.raw{margin-top:2px}
details.raw summary{color:#5b6572;font-size:11.5px;cursor:pointer}
details.raw code{display:block;margin-top:2px}
.muted{color:#8a8f98}
table.pts{border-collapse:collapse;width:100%;font-size:12.5px;
 font-family:ui-monospace,Consolas,"Courier New",monospace}
table.pts th{color:#5b6572;font-weight:600;font-size:11.5px;text-align:right;
 border-bottom:1px solid #e3e7ec;padding:2px 6px}
table.pts th:first-child{text-align:left}
table.pts td{border-bottom:1px solid #f0f2f5;padding:2px 6px;text-align:right;
 font-variant-numeric:tabular-nums}
table.pts td:first-child{text-align:left;color:#3d4650}
th.proof,td.proof{color:#1f5fa8}
th.fit,td.fit{color:#c0392b}
.fn svg{width:100%;max-width:820px;height:auto;display:block;margin:0 auto;
 background:#fff;border:1px solid #f0f2f5;border-radius:6px}
.cap{color:#5b6572;font-size:11.5px;margin:2px 0 3px}
.cap b{font-weight:650}
.note{color:#5b6572;font-size:12px;margin:4px 0 0}
.note.warn{color:#b45309}
.banner{background:#fff8e6;border:1px solid #f0d3ae;border-radius:6px;padding:8px 12px;
 color:#8a5a1f;font-size:12.5px;margin:0 0 16px}
section.block{margin:26px 0 8px}
section.block h2{font-size:15px;margin:0 0 8px;font-weight:650}
table.undeclared{border-collapse:collapse;width:100%;font-size:12.5px;background:#fff;
 border:1px solid #e3e7ec;border-radius:6px;overflow:hidden}
table.undeclared th,table.undeclared td{border-bottom:1px solid #f0f2f5;
 padding:5px 10px;text-align:left}
table.undeclared th{color:#5b6572;font-weight:600;font-size:11.5px;background:#fafbfc}
footer{margin-top:30px;padding-top:12px;border-top:1px solid #e3e7ec;color:#8a8f98;
 font-size:11.5px}
footer code{background:#f0f2f5}
"""


def _stamp_html(kind: str, label: str, value: str, note: str) -> str:
    return (f'<div class="stamp {kind}"><span class="k">{_esc(label)}</span>'
            f'<span class="v">{_esc(value)}</span>'
            f'<span class="n">{_md(note)}</span></div>')


def _points_table(points) -> str:
    if not points:
        return ('<p class="note warn">没有规模点：这个函数既没有实测，'
                '也没有可展示的 κ 数值。</p>')
    out = ['<table class="pts"><tr><th>n</th>'
           '<th class="proof">κ（证明）</th>'
           '<th class="fit">实测 ops（符合）</th>'
           '<th>κ/ops</th></tr>']
    for p in points:
        out.append(
            f'<tr><td>{_esc(p["n"])}</td>'
            f'<td class="proof">{_num(p.get("kappa"))}</td>'
            f'<td class="fit">{_num(p.get("ops"))}</td>'
            f'<td>{_ratio_text(p.get("ratio"))}</td></tr>')
    out.append("</table>")
    return "".join(out)


def _kappa_html(card: dict) -> str:
    """κ 一栏：能折叠就折，**原文永远留在 `<details>` 里**（要能复制）。

    折叠只换排版，不改任何一个字：分界照 `symbolic.render` 的口径印，
    主体是**逐字符相同的原文**。
    """
    expr = card.get("kappa_expr") or ""
    if not expr:
        return '<span class="muted">—（没有算出 κ）</span>'
    order = (f'<span class="order">{_esc(card["kappa_order"])}</span>'
             if card.get("kappa_order") else "")
    fold = card.get("kappa_fold")
    if not fold:
        return f'<code>{_esc(expr)}</code>{order}'
    n = fold.get("n") or len(fold.get("labels") or [])
    segs = " / ".join(f"<code>{_esc(s)}</code>"
                      for s in fold.get("labels") or [])
    return ('<div class="kap-fold">'
            f'<div class="seg"><span class="lbl">分段：</span>{segs}</div>'
            f'<div class="body"><span class="lbl">主体（{_CN_NUM.get(n, n)}段相同）：'
            f'</span><code>{_esc(fold.get("body", ""))}</code>{order}</div>'
            '<details class="raw"><summary>原始 κ 表达式（可复制）</summary>'
            f'<code>{_esc(expr)}</code></details>'
            '</div>')


def _ratio_html(card: dict) -> str:
    """比值那句话。`over` 是**红色块**，不是行内小字。

    `in` 档是行内的，所以要自带一个「，」——否则它跟判决正文黏成
    「阶够κ 不高于 target」；`far` / `over` 是独立块，行首不加标点。
    """
    note = card.get("ratio_note") or ""
    if not note:
        return ""
    lvl = card.get("ratio_level") or RATIO_IN
    if lvl == RATIO_IN:
        mark = "，"
    elif lvl == RATIO_OVER:
        mark = "⚠ "
    else:
        mark = ""
    return f'<span class="ratio {_esc(lvl)}">{_esc(mark + note)}</span>'


def _card_html(card: dict, idx: int) -> str:
    kind = card.get("verdict") or "none"
    label = _VERDICT_LABEL.get(kind, "未判定")
    if card.get("verdict") is None:
        label = "无契约"
    rows = [
        f'<h2><code>{_esc(card["fn"])}</code>'
        f'<span class="badge {kind}">{_esc(label)}</span></h2>',
        "<dl class=\"kv\">",
    ]
    tgt = (f'<code>{_esc(card["target_expr"])}</code>'
           + (f'<span class="order">{_esc(card["target_order"])}</span>'
              if card.get("target_order") else "")
           ) if card.get("target_expr") else '<span class="muted">—（没有 target）</span>'
    rows.append(f"<dt>target</dt><dd>{tgt}</dd>")
    rows.append(f'<dt>κ<span class="tag proof">证明</span></dt>'
                f'<dd>{_kappa_html(card)}</dd>')
    # 比值那句话如果是**独立块**（远低于 / 超过），判决正文先收个句号——
    # 否则复制出来会是「阶够κ 远低于 target」这种黏在一起的句子。
    vtxt = card.get("verdict_text") or ""
    if (vtxt and card.get("ratio_level") in (RATIO_FAR, RATIO_OVER)
            and vtxt[-1] not in "。；；.!！?？）"):
        vtxt += "。"
    rows.append(f'<dt>判决</dt><dd class="verdict {kind}">'
                f'<b>{_esc(kind if card.get("verdict") else "—")}</b>'
                f'{"　" + _md(vtxt) if vtxt else ""}'
                f'{_ratio_html(card)}'
                f'</dd>')
    meas = ('<span class="muted">未测量</span>' if not card.get("measured")
            else "见下表")
    rows.append(f'<dt>实测<span class="tag fit">符合</span></dt><dd>{meas}</dd>')
    ratio = " / ".join(_ratio_text(p.get("ratio")) for p in card["points"]) or "—"
    rows.append(f"<dt>κ/ops</dt><dd>{_esc(ratio)}</dd>")
    rows.append("</dl>")
    body = "".join(rows) + _points_table(card["points"])
    if card.get("svg"):
        body += ('<p class="cap">曲线：蓝线是 κ（<b>证明</b>），'
                 '红点是实测（<b>符合</b>）</p>' + card["svg"])
    if card.get("chart_note"):
        body += f'<p class="note warn">{_md(card["chart_note"])}</p>'
    return f'<article class="fn" id="{_esc(_slug(card["fn"], idx))}">{body}</article>'


def render(payload: dict) -> str:
    """→ 完整 HTML（**纯函数**：同样的 payload 一定给出同样的字节）。"""
    module = payload.get("module") or "?"
    tool = payload.get("tool_version") or ""
    at = payload.get("generated_at") or "（未记录）"
    meas = payload.get("measured") or {}
    sm = payload.get("summary") or {}
    cards = payload.get("cards") or []
    und = payload.get("undeclared") or []

    head = [
        "<!doctype html>",
        '<html lang="zh">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>成本报告 · {_esc(module)}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        '<div class="wrap">',
        "<header>",
        f'<h1>成本报告 <span class="mod">{_esc(module)}</span></h1>',
        f'<p class="sub">静态归档页 · 自包含单文件（零外链）· '
        f'生成工具 {_esc(tool or "elench")}　'
        f'规模点 {_esc(", ".join(str(s) for s in payload.get("sizes") or [])) or "—"}'
        f'　来源：{_esc(payload.get("sizes_src") or "—")}　'
        f'每点输入 {_esc(payload.get("runs", 0))} 组</p>',
        '<div class="stamps">',
        _stamp_html("s1", "κ / 判决", at,
                    "刚才（本地静态分析）：κ 是推导出来的上界，任何 n 都成立；"
                    "判决比较的是阶，与实测无关。"),
        _stamp_html("s2", "实测", meas.get("at") or "未测量",
                    meas.get("source") or "未测量"),
        "</div>",
        "</header>",
    ]

    stats = [
        '<div class="stats">',
        f'<div class="stat"><b>{_esc(sm.get("declared", 0))}</b><span>有契约的函数</span></div>',
        f'<div class="stat ok"><b>{_esc(sm.get("ok", 0))}</b><span>判决 ok（达成）</span></div>',
        f'<div class="stat debt"><b>{_esc(sm.get("debt", 0))}</b><span>判决 debt（超出）</span></div>',
        f'<div class="stat unknown"><b>{_esc(sm.get("unknown", 0))}</b>'
        f'<span>判决 unknown（无法判定）</span></div>',
        f'<div class="stat none"><b>{_esc(sm.get("undeclared", 0))}</b>'
        f'<span>未声明契约</span></div>',
        f'<div class="stat"><b>{_esc(sm.get("points", 0))}</b><span>实测点数</span></div>',
        "</div>",
    ]

    rules = [
        '<div class="rules">',
        '<div class="rule proof"><b>证明（κ）</b>：编译器从实现推出来的上界，'
        '<b>任何 n 都成立</b>——包括没测过的 n。</div>',
        '<div class="rule fit"><b>符合（实测）</b>：只说明「在这几个 n 上没超过 κ」，'
        '<b>只覆盖测过的区间</b>；没测过的 n 什么都没说。</div>',
        '<div class="rule"><b>不许合成一个「通过」</b>：判决只回答「阶对不对」，'
        '实测只回答「这几个点破没破界」。两件事分两栏写。</div>',
        "</div>",
    ]

    body = []
    if payload.get("load_note"):
        body.append(f'<div class="banner">{_md(payload["load_note"])}</div>')

    for i, c in enumerate(cards):
        body.append(_card_html(c, i))
    main = ['<main class="cards">'] + body + ["</main>"] if body else [
        '<main class="cards"><article class="fn">'
        '<h2>没有可展示的函数</h2>'
        '<p class="note warn">这个模块没有带 target 的函数，也没有实测点。</p>'
        '</article></main>']

    und_html = []
    if und:
        und_html = [
            '<section class="block"><h2>未声明契约（有 κ，但没有 target 可比）</h2>',
            '<table class="undeclared"><tr><th>函数</th><th>κ（证明）</th>'
            '<th>阶</th><th>实测点</th></tr>',
        ]
        for u in und:
            und_html.append(
                f'<tr><td><code>{_esc(u["fn"])}</code></td>'
                f'<td><code>{_esc(u.get("kappa_expr") or "—")}</code></td>'
                f'<td>{_esc(u.get("kappa_order") or "—")}</td>'
                f'<td>{_esc(u.get("points", 0))}</td></tr>')
        und_html.append("</table>")
        und_html.append('<p class="note">这些函数没有承诺，所以既不算达成也不算债务；'
                        '它们的 κ 仍然是一个上界。</p></section>')

    tail = [
        '<footer>',
        f'<p>由 <code>elench export &lt;模块&gt; --full --out=报告.html</code> 生成；'
        f'数字与终端里 <code>--full</code> 的三节报告同源（同一份 rows）。'
        f'页面无外部依赖，可离线打开。</p>',
        f'<p>曲线复用 <code>src/chart.py</code>；本文件不引任何 CDN、字体或脚本。'
        f'生成时间 {_esc(at)}。</p>',
        "</footer>",
        "</div>",
        "</body>",
        "</html>",
    ]
    return "\n".join(head + stats + rules + main + und_html + tail) + "\n"


# ---------------------------------------------------------------- 入口

def _now_stamp(now=None, no_timestamp: bool = False) -> str:
    """两个时间戳的唯一来源。`--no-timestamp` 时写成固定标记（逐字节可复现）。"""
    if no_timestamp:
        return "（未记录：--no-timestamp）"
    if now is not None and str(now).strip():
        return str(now).strip()
    import datetime
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def write_report(path: str, prog: str, rows, sizes=None, runs: int = 20,
                 sizes_src: str = "", now=None, no_timestamp: bool = False,
                 tool_version: str = "") -> int:
    """把 `_full_report` 的同一份 rows 写成一张自包含的 HTML。退出码 0/1。"""
    try:
        cards, undeclared, load_note = collect(prog, rows, runs)
    except Exception as exc:                            # noqa: BLE001
        print(f"✗ HTML 报告生成失败：{type(exc).__name__}: {exc}")
        return 1

    gen = _now_stamp(now, no_timestamp)
    n_meas = sum(len([p for p in c["points"] if p.get("ops") is not None])
                 for c in cards)
    if n_meas:
        measured = {
            "at": gen,
            "source": (f"本次导出时现场测量：interp 操作数计数器"
                       f"（bench.measure_ops 口径），每点 {runs} 组合成输入取最大；"
                       f"不是纳秒，也不是几小时前的服务器结果 · {n_meas} 个点"),
        }
    else:
        measured = {"at": "未测量",
                    "source": "未测量：没有跑出任何操作数，本页没有任何实测数字"}

    payload = {
        "module": os.path.basename(str(prog).rstrip("/\\")).removesuffix(".tu"),
        "tool_version": tool_version,
        "generated_at": gen,
        "sizes": list(sizes or []),
        "sizes_src": sizes_src,
        "runs": runs,
        "load_note": load_note,
        "summary": {
            "declared": sum(1 for c in cards if c.get("has_target")),
            "ok": sum(1 for c in cards if c.get("verdict") == "ok"),
            "debt": sum(1 for c in cards if c.get("verdict") == "debt"),
            # **每张卡片只能落进这三档之一**：判不出（含模块没解析出来）都算 unknown
            "unknown": sum(1 for c in cards
                           if c.get("verdict") not in ("ok", "debt")),
            "undeclared": len(undeclared),
            "points": n_meas,
        },
        "cards": cards,
        "undeclared": undeclared,
        "measured": measured,
    }
    text = render(payload)
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    import io
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)
    kb = len(text.encode("utf-8")) / 1024.0
    print(f"已写出 HTML 报告：{path}（{kb:.1f} KB，"
          f"{len(cards)} 个函数 / {n_meas} 个实测点）")
    if kb > SIZE_BUDGET_KB:
        print(f"  ！ 体积超过 {SIZE_BUDGET_KB} KB 的预算：{kb:.1f} KB")
    return 0
