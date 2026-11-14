#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_cost_baseline.py —— κ 基线的**判定语义**（`tools/cost_baseline.py`）

要钉住的三件事：

1. **κ 文本能被读回 `Cost`**（`Cost.render()` 的逆）——否则"变了"只能判"无法判定"；
2. **四种归类**：更紧 / 同阶变大 / 变松 / 无法判定。**两个方向都判**，
   因为阶代数（`_profile`）看不见常数因子（`n` 与 `2n` 同阶）；
3. `verdict` 从 `ok` 退化（→ debt / unknown）**必须**算失败。

**为什么不在这里重算全部 17 个样例**：那是 `tools/gates.py` 的 `cost_baseline`
门在跑的事（`--check`）。这里只读**入库的基线文件**，验证"解析 + 归类"这两层，
所以几毫秒就跑完，不会和门禁重复算 29 条 κ。
"""
import io
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
TOOLS = os.path.join(ROOT, "tools")
BASELINE = os.path.join(ROOT, "tests", "baselines", "cost.json")

sys.path.insert(0, SRC)
sys.path.insert(0, TOOLS)

import cost_baseline as cb                               # noqa: E402

_ok = _bad = 0


def check(cond, label, detail=""):
    global _ok, _bad
    if cond:
        _ok += 1
        print("  ✓ %s" % label)
    else:
        _bad += 1
        print("  ✗ %s%s" % (label, ("\n      " + detail) if detail else ""))


def cost(text):
    return cb.parse_kappa(text)


def test_roundtrip_from_baseline():
    """基线里的每一条 κ 都要能被读回来（否则它变了只能判"无法判定"）。"""
    payload = json.load(io.open(BASELINE, encoding="utf-8"))
    check(isinstance(payload.get("note"), str) and payload["note"],
          "基线有 `note`（人先读到「这份文件是干什么的」）")
    mods = payload.get("modules") or {}
    check(len(mods) == 17, "语料是 17 个模块（文档/样例 里有 .tu 的那些）",
          "实际 %d" % len(mods))
    bad, total = [], 0
    for key, fns in mods.items():
        for fn, info in fns.items():
            total += 1
            try:
                c = cost(info["kappa"])
            except Exception as exc:                     # noqa: BLE001
                bad.append("%s.%s: %s" % (os.path.basename(key), fn, exc))
                continue
            verdict, why = cb.classify(c, c)
            if verdict != cb.TIGHTER:
                bad.append("%s.%s → %s %s" % (os.path.basename(key), fn,
                                              verdict, why))
    check(not bad, "29 条 κ 都能按 `Cost.render()` 读回并判成「同阶」",
          "\n      ".join(bad[:5]))
    check(total == 29, "基线里是 29 个函数", "实际 %d" % total)


def test_classify():
    n = cost("n(xs)")
    two_n = cost("n(xs) + n(xs)")
    n2 = cost("n(xs)×n(xs)")

    v, _ = cb.classify(n2, n)
    check(v == cb.LOOSER, "n → n² 判「变松」（阶升）", v)
    v, _ = cb.classify(n, n2)
    check(v == cb.TIGHTER, "n² → n 判「更紧」（阶降）", v)
    v, why = cb.classify(two_n, n)
    check(v == cb.SAME_UP, "n → 2n 判「同阶变大」（阶代数看不见常数）",
          "%s %s" % (v, why))
    v, why = cb.classify(n, two_n)
    check(v == cb.TIGHTER and "同阶" in why,
          "2n → n 判「更紧 / 同阶」（常数没变大）", "%s %s" % (v, why))
    v, why = cb.classify(cost("n(xs)×n(xs)"),
                         cost("n(text)×n(text)"))
    check(v == cb.UNKNOWN, "尺寸量对不上（xs vs text）判「无法判定」",
          "%s %s" % (v, why))
    v, why = cb.order_of(n, "⊤")
    check(v == cb.UNKNOWN, "旧 κ 是 ⊤ 判「无法判定」", "%s %s" % (v, why))
    v, why = cb.order_of(n, "{n(xs)<1: 5; n(xs)>0: 7}")
    check(v == cb.UNKNOWN, "旧 κ 是分段（本工具不解析）判「无法判定」",
          "%s %s" % (v, why))


def test_rows():
    """`check()` 的行与"必须失败"的条数。"""
    base = {"文档/样例/demo": {"f": {"kappa": "n(xs)", "target": "n(xs)",
                                     "verdict": "ok"}}}
    same = {"文档/样例/demo": {"f": {"kappa": "n(xs)", "target": "n(xs)",
                                     "verdict": "ok"}}}
    rows, bad = cb.check(base, same)
    check(not rows and not bad, "没变化 → 没有行、bad=0")

    worse = {"文档/样例/demo": {"f": {"kappa": "n(xs)×n(xs)",
                                      "target": "n(xs)", "verdict": "debt"}}}
    rows, bad = cb.check(base, worse)
    texts = ["%s %s" % (m, t) for m, _n, t in rows]
    joined = "\n".join(texts)
    check(bad >= 1, "κ 变松 + verdict 退化 → bad ≥ 1", "bad=%s" % bad)
    check("变松" in joined, "行里出现「变松」", joined)
    check("verdict ok → debt" in joined, "行里点明 verdict 退化", joined)

    looser_target = {"文档/样例/demo": {"f": {"kappa": "n(xs)",
                                              "target": "n(xs)×n(xs)",
                                              "verdict": "ok"}}}
    rows, bad = cb.check(base, looser_target)
    check(bad == 1 and any("target **变松**" in t for _m, _n, t in rows),
          "只改松 target（κ 不变）→ 也判失败", "%s" % (rows,))

    added = dict(same)
    added["文档/样例/new"] = {"g": {"kappa": "1", "target": None,
                                    "verdict": "unknown"}}
    rows, bad = cb.check(base, added)
    check(bad == 1 and any(m == "+" for m, _n, _t in rows),
          "模块增删 → bad=1（结构变化要人看一眼）", "%s" % (rows,))


def main() -> int:
    print("--- 基线与 κ 文本（round-trip）")
    test_roundtrip_from_baseline()
    print("--- 四种归类")
    test_classify()
    print("--- 比对行与退出判据")
    test_rows()
    print()
    if _bad:
        print("✗ %d 项失败 / %d 项通过" % (_bad, _ok))
        return 1
    print("✓ 全部通过（%d 项）" % _ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
