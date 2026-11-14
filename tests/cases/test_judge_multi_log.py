#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_judge_multi_log.py —— 判阶处理「**多尺寸量 + log 项**」

## 这份测试钉什么（2026-11-13）

`02_csv_stats.count_fields` 的 κ 是

    κ = 3 + 2 + n(lines)×(… + L(lines) + L(lines)×(…) + log₂(n(lines)) + …) + …

**两个尺寸量**（`n(lines)`、`L(lines)`）**加 log 因子**。改前：无论 target 怎么写都
判不了——`_profile` 一次只看一个尺寸量，`_monomials`（多尺寸量判据）一遇 `log2`
就返回 None，于是「含 log ⇒ 展不开 ⇒ unknown」，少写一个尺寸量又退 E23。

改后：多尺寸量判据用**多维 profile**——每个单项式记成

    (e , l)     eᵢ = xᵢ 的多项式次数（Fraction）
                lᵢ = log₂(xᵢ) 的因子次数（int，可负）

比较规则是**乘积序**（把「尺寸量」与「它的 log」当成两个独立维）：

    (C) ok    ∀ (e,l) ∈ κ  ∃ (f,m) ∈ target:  e ≤ f 且 l ≤ m（**都逐分量**）
    (A) debt  ∃ (e,l) ∈ κ  ∀ (f,m) ∈ target:  逐维「次数严格高」或「次数打平且
                                              log 次数不低」，且至少一维严格
    其余      → unknown

(C) 的证明：`x^e log^l x / (x^f log^m x) = x^(e−f) log^(l−m) x ≤ 1`（x ≥ 2）。
(A) 的证明：沿"所有尺寸一起变大"，每个因子都不衰减、至少一个到 ∞。

**这个判据故意保守的一点**：`l` **不跨维吸收**。单尺寸量时 `n log n ≺ n²` 靠
字典序（同一个变量，`x²` 真压得住 `x log x`）；多尺寸量时每一维是**独立变量**，
`xs·log₂(ys)` 对 `xs·ys` 数学上成立（`log m ≤ m`），但它要求先知道两个尺寸量的
关系——与「`n·m` 与 `n²` 不可比」同一条理由。所以判据**充分不必要**：
说 ok 就真 ok、说 debt 就真 debt，其余 unknown。

## 六类断言

1. **单尺寸量红线不变**：`n² vs n log n`、`n log n vs n²`、`n log n vs n` 等
   逐条与改前同结论（这条判据一个字节都不经过单尺寸量路径）。
2. **多维 profile 的规则单测**：(C)/(A)/unknown 各给正反例，含"log 不跨维吸收"、
   "`n·m` 与 `n²` 仍不可比"两条保守边界。
3. **真语料**：`demo/corpus/02_csv_stats.cpp` 的 `count_fields`，临时换 5 种
   target 写法（`demo/**` 不动），看 ✓ / ▲ / ? 各自出现。
4. **等价性**：同一判定的两种 target 写法 ⇒ κ **逐字节相同**、解释器 run
   逐字节相同（target 只是元数据，不许漏进 κ）。
5. **解释器实测**：`ops ≤ κ`（逐规模，不是估计值）。
6. **证不出 ⇒ unknown**：三个例子（log 跨维、多尺寸偏序、缺 log 项）。

跑法：
    cd tests/cases; $env:PYTHONIOENCODING="utf-8"; python test_judge_multi_log.py
"""

import io as _io
import os as _os
import re as _re
import subprocess as _subprocess
import sys as _sys
import tempfile as _tempfile
from fractions import Fraction

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
for _p in (SRC, ROOT):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

import compat                                                     # noqa: E402
import cost_check as cc                                          # noqa: E402
from cost_check import evaluate as kappa_at                      # noqa: E402
from cost_interp import CostInterp                               # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset         # noqa: E402
from parser import parse_source                                  # noqa: E402
from symbolic import Cost                                        # noqa: E402

_passed = 0
_failed = []


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def n(name="xs"):
    return Cost.size(name)


# ---------------------------------------------------------------- 语料

CORPUS_CPP = _os.path.join(ROOT, "demo", "corpus", "02_csv_stats.cpp")

#: 02 的契约里**只换 time target**（`demo/**` 一个字都不改）。
TU_TMPL = """
spec split(const vector<u8>& s, u8 sep) -> vector<vector<u8>> {
    effects { alloc };
}
spec count_fields(const vector<vector<u8>>& lines) -> map<vector<u8>, i64> {
    effects { alloc };
    target { %s allocation: { max: O(lines.size() * L(lines)) }, };
}
spec main() -> i64 { effects { io, alloc }; }
"""


def corpus_cpp() -> str:
    with _io.open(CORPUS_CPP, encoding="utf-8") as fh:
        return fh.read()


def corpus_tu(time_target):
    tail = ""
    if time_target:
        tail = "time: { max: %s }," % time_target
    return TU_TMPL % tail


def load(eln: str, tu: str = "", compat_mode: bool = False):
    old = compat.enabled()
    if compat_mode:
        compat.set_enabled(True)
    try:
        ch = parse_source(eln, "multi_log.eln").module
        decl = parse_source(tu, "multi_log.tu").module if tu else None
    finally:
        compat.set_enabled(old)
    return ch, decl


def ci_of(eln: str, tu: str = "", compat_mode: bool = False) -> CostInterp:
    ch, decl = load(eln, tu, compat_mode)
    return CostInterp(ch, decl)


def run_call(fn: str, args, eln: str, tu: str = "", compat_mode: bool = False):
    """`(返回值, ops)`。"""
    ch, decl = load(eln, tu, compat_mode)
    it = Interp(ch, decl)
    ops_enable(True)
    try:
        ops_reset()
        v = it.call(fn, args)
        return v, ops_of()
    finally:
        ops_enable(False)


def _run_cost(tu_text: str) -> str:
    """真 cli：`dsh cost <临时 .tu> <临时 .eln> --compat`，返回合并输出。"""
    tmp = _tempfile.mkdtemp(prefix="judge_multi_log_")
    try:
        eln_p = _os.path.join(tmp, "02_csv_stats.eln")
        tu_p = _os.path.join(tmp, "02_csv_stats.tu")
        for path, text in ((eln_p, corpus_cpp()), (tu_p, tu_text)):
            with _io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        env = dict(_os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = SRC
        p = _subprocess.run(
            [_sys.executable, "-B", _os.path.join(ROOT, "src", "dsh.py"),
             "cost", tu_p, eln_p, "--compat"],
            cwd=tmp, capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env)
        return (p.stdout or "") + (p.stderr or "")
    finally:
        for nm in _os.listdir(tmp):
            try:
                _os.remove(_os.path.join(tmp, nm))
            except OSError:
                pass
        try:
            _os.rmdir(tmp)
        except OSError:
            pass


def _verdict_line(out: str, fn: str) -> str:
    for ln in out.splitlines():
        s = ln.strip()
        if (s.startswith("✓ ") or s.startswith("▲ ") or s.startswith("? ")) \
                and fn in s.split():
            return s
    return ""


_REASON_PREFIX = ("达成：", "**超标**：", "无法判定：")


def _reason_line(out: str, fn: str) -> str:
    """那个函数的**理由行**（`达成：…` / `**超标**：…` / `无法判定：…`）。"""
    cur = None
    for ln in out.splitlines():
        s = ln.strip()
        if s[:2] in ("✓ ", "▲ ", "? ") and s[2:].split()[:1] == [fn]:
            cur = fn
            continue
        if cur == fn and s.startswith(_REASON_PREFIX):
            return s
        if s[:2] in ("✓ ", "▲ ", "? "):
            cur = None
    return ""


def _kappa_text(ci: CostInterp, fn: str) -> str:
    """`dsh cost` 里那一行 κ 的**正文**（用于"逐字节相同"的对比）。"""
    k = ci.cost_of(fn)
    return k.render()


# ---------------------------------------------------------------- [1] 单尺寸红线

def test_single_var_redlines():
    print("[1] 单尺寸量红线（这条判据不经过那条路径）：n² / n log n / n / log n")
    cases = [
        ("n² vs n log n → 债务", Cost.mul(n(), n()), Cost.mul(n(), Cost.log2(n())), "debt"),
        ("n log n vs n² → 达成", Cost.mul(n(), Cost.log2(n())), Cost.mul(n(), n()), "ok"),
        ("n log n vs n → 债务", Cost.mul(n(), Cost.log2(n())), n(), "debt"),
        ("n vs n log n → 达成", n(), Cost.mul(n(), Cost.log2(n())), "ok"),
        ("n/log n vs n → 达成",
         Cost.div(n(), Cost.log2(n())), n(), "ok"),
        ("n vs n/log n → 债务",
         n(), Cost.div(n(), Cost.log2(n())), "debt"),
        ("n loglog n vs n log n → 达成",
         Cost.mul(n(), Cost.loglog(n())),
         Cost.mul(n(), Cost.log2(n())), "ok"),
    ]
    for label, a, b, want in cases:
        v = cc.compare(a, b)
        check(v.kind == want, f"{label}（结论 {v.kind}）", v.render())
    print()


# ---------------------------------------------------------------- [2] 多维 profile

def test_multi_log_rules():
    print("[2] 多维 profile：次数 + 同维 log 次数，按乘积序比")
    a, b = n("a"), n("b")
    la, lb = Cost.log2(a), Cost.log2(b)

    # ---- (C) ok：两个尺寸量 + log 因子都被压住
    kappa = Cost.add(Cost.add(Cost.mul(a, b), Cost.mul(a, la)),
                     Cost.add(Cost.num(7), Cost.mul(b, b)))
    target = Cost.add(Cost.mul(a, b), Cost.mul(a, Cost.mul(b, b)))
    target = Cost.add(target, Cost.mul(a, la))
    v = cc.compare(kappa, target)
    check(v.kind == "ok",
          "C：κ=a·b+a·log a+7+b² vs target=a·b+a·b²+a·log a → 达成", v.render())
    check(v.multi, "多尺寸量的输出仍带 multi 标记（比值栏标注同规模假设）",
          v.render())

    # ---- (C) 少一个 log 项就**不**达成（这正是 02 里那一档）
    v2 = cc.compare(kappa, Cost.add(Cost.mul(a, b), Cost.mul(a, Cost.mul(b, b))))
    check(v2.kind != "ok",
          "C⁻：target 少写 `a·log a` 这一项 → 不再判达成（log 因子没被压住）",
          v2.render())
    check(v2.kind == "unknown", "C⁻：证不出 → unknown（不是债务）", v2.render())
    check("不是本判据能展开" in v2.note,
          "C⁻：文案说清是「log 这一维没被压住」这一档", v2.note)
    check("log₂" in v2.note or "log" in v2.note,
          "C⁻：文案点出 log 因子（不是一句泛泛的「展不开」）", v2.note)

    # ---- (A) debt：次数上就可证超出，文案说清哪维高几次
    v3 = cc.compare(Cost.mul(a, Cost.mul(b, b)), Cost.mul(a, b))
    check(v3.kind == "debt", "A：κ=a·b² vs target=a·b → 债务", v3.render())
    check("可证超出" in v3.render(), "A：债务文案是**证明**（「可证超出」）", v3.render())
    check("在 `b` 上的次数 2 高于 target 的最高次数 1" in v3.render(),
          "A：文案写明哪个尺寸量高了几次", v3.render())

    # ---- (A) debt：靠 **log 因子**领先也能证（次数打平、log 更高）
    v4 = cc.compare(Cost.mul(Cost.mul(a, b), la), Cost.mul(a, b))
    check(v4.kind == "debt",
          "A：κ=a·b·log₂a vs target=a·b → 债务（log 因子 1 > 0）", v4.render())
    check("log 因子 1 高于 target 的最高 log 因子 0" in v4.render(),
          "A：靠 log 因子领先的债务也说清是哪一维", v4.render())
    # ---- (B) unknown：一维大、一维小 —— **多尺寸量的偏序保守不许被打破**
    v5 = cc.compare(Cost.mul(a, Cost.mul(b, b)), Cost.mul(Cost.mul(a, a), b))
    check(v5.kind == "unknown", "B：κ=a·b² vs target=a²·b → unknown（不可比）",
          v5.render())
    check("不可比" in v5.note, "B：文案点明不可比", v5.note)

    # ---- (B) unknown：log 挂在**不同**的尺寸量上（log a vs log b）
    v6 = cc.compare(Cost.mul(Cost.mul(a, b), la), Cost.mul(Cost.mul(a, b), lb))
    check(v6.kind == "unknown",
          "B：κ=a·b·log₂a vs target=a·b·log₂b → unknown（区间/关系未知）",
          v6.render())
    check("跨维吸收" in v6.note,
          "B：文案点明 log 不跨维吸收", v6.note)

    # ---- 「log m ≤ m 成立但不做」：xs·log₂(ys) vs xs·ys 仍是 unknown
    xs, ys = n("xs"), n("ys")
    v7 = cc.compare(Cost.mul(xs, Cost.log2(ys)), Cost.mul(xs, ys))
    check(v7.kind == "unknown",
          "保守：κ=xs·log₂(ys) vs target=xs·ys → unknown（跨维吸收不做）",
          v7.render())
    check("不是本判据能展开" in v7.note,
          "保守：文案说「不是本判据能展开的方向」", v7.note)

    # ---- 分段 + log：判「达成」取并集（上界，sound）；判「超出」**不**取并集
    # （某一支只在小区间生效，拿它当全局下界就是假债务——与 `_multi_exceed` 同一条）。
    pc = Cost.piece("a", [(1, 10, Cost.mul(Cost.mul(a, b), la))], a)
    v12 = cc.compare(pc, Cost.mul(Cost.mul(a, b), la))
    check(v12.kind == "ok",
          "piece+log：并集是上界 ⇒ 被支配时判达成", v12.render())
    v13 = cc.compare(pc, Cost.mul(a, b))
    check(v13.kind != "debt",
          "piece+log：高次支只在 a∈[1,10] 生效 → MUST NOT 判债务", v13.render())

    # ---- 展不开的形状仍然 unknown（⊤ / ext / 符号指数）
    v8 = cc.compare(Cost.mul(a, Cost.top()), Cost.mul(a, b))
    check(v8.kind == "unknown", "⊤：κ 含 ⊤ → unknown（不猜）", v8.render())
    v9 = cc.compare(Cost.mul(a, Cost("ext", "read_file")), Cost.mul(a, b))
    check(v9.kind == "unknown", "ext：κ 含外部项 → unknown（不猜）", v9.render())

    # ---- 结论与 LADDER 无关（结构判据，不是拟合）
    old = cc.LADDER
    try:
        cc.LADDER = [2 ** k for k in range(20, 41)]
        v10 = cc.compare(kappa, target)
        check(v10.kind == "ok", "LADDER 换一组：同一个 ok 不翻转", v10.render())
        v11 = cc.compare(Cost.mul(a, Cost.mul(b, b)), Cost.mul(a, b))
        check(v11.kind == "debt", "LADDER 换一组：同一个 debt 不翻转", v11.render())
    finally:
        cc.LADDER = old
    print()


# ---------------------------------------------------------------- [3] 真语料 5 种写法

#: (标签, target 写法, 期望的判定符号)
WRITINGS = [
    ("W1 乘进去（推荐）",
     "O(lines.size() * (L(lines) * L(lines) + log2(lines.size())))", "✓"),
    ("W2 展开成和（同一个式子）",
     "O(lines.size() * L(lines) * L(lines) + lines.size() * log2(lines.size()))", "✓"),
    ("W3 少写 log 那一项（**故意写紧一档**）",
     "O(lines.size() * L(lines) * L(lines))", "?"),
    ("W4 只写 n·L（**紧两档**）",
     "O(lines.size() * L(lines))", "▲"),
    ("W5 写 n²·L（多尺寸偏序：不可比）",
     "O(lines.size() * lines.size() * L(lines))", "?"),
    # **W6 是"只删一项"的紧一档，但本判据证不出超出**：κ 的 `n·log₂ n` 对
    # target 的 `n·L²` 项，在 `L` 那一维上是 0 < 2（`L=1` 时 κ 确实超，但比值
    # `log n / L²` 取决于 `L` 是否有界）。要判债务就得允许"只让 n 涨、固定 L"
    # 的**子方向**证明——而那正是 `n·m` vs `n²` 会被判债务的那条路（多尺寸量的
    # 偏序保守）。所以这里**必须**是 unknown：宁可判不了，不许把保守拆掉。
    ("W6 只删 log 项、保留 n·L²（紧一档，但**证不出**）",
     "O(lines.size() * L(lines) * L(lines) + lines.size())", "?"),
]


def test_corpus_writings():
    print("[3] 真语料 `02_csv_stats.count_fields`：5 种 target 写法的判定")
    outs = {}
    for label, writing, want in WRITINGS:
        out = _run_cost(corpus_tu(writing))
        outs[writing] = out
        line = _verdict_line(out, "count_fields")
        mark = line[0] if line else "?"
        print(f"      {label}\n        target = {writing}\n        判定行 = "
              f"{line[:150] or '<无>'}")
        check(mark == want, f"{label} ⇒ 判 `{want}`（实际 `{mark}`）",
              line[:300] or out[-400:])
    print()
    print("      —— 判定理由（摘自真 cli 输出）")
    for label, writing, _w in WRITINGS:
        print(f"      {label.split()[0]}:", _reason_line(outs[writing],
                                                        "count_fields")[:300])
    print()
    check("可证超出" in outs[WRITINGS[3][1]],
          "W4 的债务带**证明**（可证超出）",
          _reason_line(outs[WRITINGS[3][1]], "count_fields")[:300])
    check("在 `L(lines)` 上的次数 2 高于 target 的最高次数 1"
          in outs[WRITINGS[3][1]],
          "W4 的证明说清**哪个尺寸量高了几次**",
          _reason_line(outs[WRITINGS[3][1]], "count_fields")[:300])
    # W6：**证不出 ⇒ unknown**（不许为了"看着该超"就判债务 —— 那会拆掉
    # 「n·m 与 n² 不可比」的偏序保守）
    check(_verdict_line(outs[WRITINGS[5][1]], "count_fields").startswith("?"),
          "W6 只删 log 项：**证不出**，判 unknown（不退化成猜债务）",
          _reason_line(outs[WRITINGS[5][1]], "count_fields")[:200])
    check(_reason_line(outs[WRITINGS[0][1]], "count_fields").startswith("达成："),
          "W1 判达成",
          _reason_line(outs[WRITINGS[0][1]], "count_fields")[:200])
    check(_reason_line(outs[WRITINGS[2][1]], "count_fields").startswith("无法判定：")
          and "不是本判据能展开" in _reason_line(outs[WRITINGS[2][1]], "count_fields"),
          "W3 的 unknown 说清是「log 那一维没被压住」（不是「不可比」）",
          _reason_line(outs[WRITINGS[2][1]], "count_fields")[:300])
    print()


# ---------------------------------------------------------------- [4] 等价性

def test_equivalence():
    print("[4] 等价性：两种 target 写法 ⇒ κ 逐字节相同、run 逐字节相同")
    k1 = _kappa_text(ci_of(corpus_cpp(), corpus_tu(WRITINGS[0][1]), True),
                     "count_fields")
    k2 = _kappa_text(ci_of(corpus_cpp(), corpus_tu(WRITINGS[1][1]), True),
                     "count_fields")
    print(f"      κ(W1) = {k1[:110]}…")
    print(f"      κ(W2) = {k2[:110]}…")
    check(k1 == k2, "两种写法的 **κ 逐字节相同**（target 不许漏进 κ）",
          f"{len(k1)} vs {len(k2)}")
    check(k1.count("log2(") == k2.count("log2("),
          "两边的 log 项个数相同（各 2 处 map 查询）", str(k1.count("log2(")))

    # **同一个 run**：解释器返回值与 ops 逐字节/逐位相同
    arg = ["a,1", "b,2", "a,3", "c,4"]
    v1, o1 = run_call("count_fields", [arg], corpus_cpp(), corpus_tu(WRITINGS[0][1]),
                      True)
    v2, o2 = run_call("count_fields", [arg], corpus_cpp(), corpus_tu(WRITINGS[1][1]),
                      True)
    check(repr(v1) == repr(v2), "两次 run 的返回值**逐字节相同**", f"{v1!r} vs {v2!r}")
    check(o1 == o2, "两次 run 的 ops **相同**（target 不改变执行）", f"{o1} vs {o2}")

    # **判定也相同**：都达成（W3 少一项就不达成——见 [3]）
    c1 = ci_of(corpus_cpp(), corpus_tu(WRITINGS[0][1]), True)
    c2 = ci_of(corpus_cpp(), corpus_tu(WRITINGS[1][1]), True)
    j1 = cc.compare(c1.cost_of("count_fields"), c1.target_cost("count_fields", "time"))
    j2 = cc.compare(c2.cost_of("count_fields"), c2.target_cost("count_fields", "time"))
    check(j1.kind == j2.kind == "ok", "两种写法判定相同且都是「达成」",
          f"{j1.kind} vs {j2.kind}")
    print()


# ---------------------------------------------------------------- [5] ops ≤ κ

def test_ops_le_kappa():
    print("[5] 解释器实测：ops ≤ κ（逐规模，不是估计）")
    tu = corpus_tu(WRITINGS[0][1])
    ci = ci_of(corpus_cpp(), tu, True)
    k = ci.cost_of("count_fields")
    print("        lines    L     ops          κ   ops≤κ")
    for nlines, width in ((1, 4), (3, 4), (8, 6), (16, 6), (32, 8), (64, 8)):
        arg = [("k%03d," % (i % 7)) + "x" * width for i in range(nlines)]
        _v, o = run_call("count_fields", [arg], corpus_cpp(), tu, True)
        L = max(len(s) for s in arg)
        kv = kappa_at(k, {"lines": nlines, "L(lines)": L})
        print(f"      {nlines:6d} {L:3d} {o:8d} {kv:10.0f}   {str(o <= kv):5s}")
        check(o <= kv, f"lines={nlines}：ops {o} ≤ κ {kv:.0f}", f"{o} > {kv}")
    print()


# ---------------------------------------------------------------- [6] 失败模式

def test_unknown_examples():
    print("[6] 证不出 ⇒ unknown（3 个例子 + 1 个反向债务）")
    a, b = n("a"), n("b")

    # 例子 1：log 挂在**另一个**尺寸量上 —— 关系未知
    v1 = cc.compare(Cost.mul(Cost.mul(a, b), Cost.log2(a)),
                    Cost.mul(Cost.mul(a, b), Cost.log2(b)))
    print("      例 1  κ = a·b·log₂a      target = a·b·log₂b")
    print("            →", v1.render()[:230])
    check(v1.kind == "unknown", "例 1：log 挂在不同尺寸量 → unknown", v1.render())

    # 例子 2：多尺寸量偏序（一维大、一维小）
    v2 = cc.compare(Cost.mul(a, Cost.mul(b, b)), Cost.mul(Cost.mul(a, a), b))
    print("      例 2  κ = a·b²           target = a²·b")
    print("            →", v2.render()[:230])
    check(v2.kind == "unknown", "例 2：一维大、一维小 → unknown", v2.render())

    # 例子 3：真语料少写 log 那一项（W3）
    out3 = _run_cost(corpus_tu(WRITINGS[2][1]))
    line3 = _reason_line(out3, "count_fields")
    print("      例 3  κ = 02.count_fields  target = O(n·L²)（少 log 项）")
    print("            →", line3[:230])
    check(_verdict_line(out3, "count_fields").startswith("?"),
          "例 3：真语料少写 log 项 → unknown（`?`）", line3[:200])
    check("不是本判据能展开" in line3,
          "例 3：文案说清是 log 那一维没被压住（不是「不可比」）", line3[:300])

    # 反向：写紧两档 → **必须**判债务（否则「判不出」就成了「随便过」）
    out4 = _run_cost(corpus_tu("O(lines.size() * L(lines))"))
    line4 = _reason_line(out4, "count_fields")
    print("      反向 κ = 02.count_fields  target = O(n·L)")
    print("            →", line4[:230])
    check(_verdict_line(out4, "count_fields").startswith("▲"),
          "反向：故意写紧两档 → 判债务（`▲`）", line4[:200])
    print()


def main() -> int:
    test_single_var_redlines()
    test_multi_log_rules()
    test_corpus_writings()
    test_equivalence()
    test_ops_le_kappa()
    test_unknown_examples()

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    _sys.exit(main())
