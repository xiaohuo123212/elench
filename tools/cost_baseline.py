#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""cost_baseline.py —— **κ 基线**：漂移检测的唯一机制

## 为什么要有它（而不是"在 `.tu` 里记哈希"）

原则：**能重算的就不存；存了的必须能被校验**。

κ 是**生成物**——从 `.tu`（契约）+ `.eln`（实现）算出来的。所以它**不进 `.tu`**，
也不该"给分档表记机器/版本"。要盯的是**漂移**：这次改动让界变大了吗？

于是只有一条机制：**入库一份 κ 基线**，每次跑 `--check` 重算一遍再比。
基线里只有**规范化后的 κ 字符串**、target、verdict——都是人一眼能读的。

## 用法

    python tools/cost_baseline.py --write     # 扫语料，写 tests/baselines/cost.json
    python tools/cost_baseline.py --check     # 与基线比；有变化就 exit 1（默认）
    python tools/cost_baseline.py             # 同 --check

## 比对语义（**这就是工具输出的口径**）

κ 字符串变了 → 按**现有阶代数**（`cost_check.compare` 的 `_profile`：`n^d·log^l·loglog^m`）
判新旧谁大。**两个方向都判**，因为阶代数看不见常数因子（`n` 与 `2n` 同阶）：

| 判定 | 打印 | 退出码 |
|---|---|---|
| 新 κ 阶更低 | `更紧 ✓` | 0 |
| 同阶、常数没变大 | `更紧 ✓（同阶…）` | 0 |
| 同阶、但常数因子变大 | `**同阶变大**，请解释` | **1** |
| 新 κ 阶更高 | `**变松**，请解释` | **1** |
| ⊤ / 形状判不出 / 旧文本读不回来 | `无法判定` | **1** |
| verdict 从 ok 变成 debt / unknown | `**已经不在 target 之内**` | **1** |
| 模块 / 函数增删 | `+` / `-` | **1** |

**为什么"同阶变大"也算失败**：阶没变、但多了一轮同阶循环（常数翻倍）也是
"界变大"。只报"更紧"会把它说成好事——那正是最该有人看一眼的改动。
常数方向由 `compare()` 给的比值兜底（同规模假设下的参考值，**不是证据**）。

**target 也一起比**：把 target 改松（更宽松的界）不改变 κ，但它改了**承诺**——
同样打 `**变松**，请解释` 并 exit 1（"手动把一个样例的 target 改松"的验收就是这条）。

**为什么 target 与 κ 都要比**：κ 没变而 target 变松 = 契约偷偷放水；
κ 变松而 target 没变 = 实现变差。两件事都该有人看一眼。

## 语料与口径（两条硬约定）

1. **只扫 `文档/样例/*.eln` 里"有同名 `.tu`"的那些**（现在 17 个）——`tests/` 里的
   探针**不算语料**：它们本来就随测试改动。
2. **口径 = max（不读 `*.dispatch.yaml`）**。分档表是**测量产物**（`.gitignore`
   已排除），换台机器就没有或不一样；基线若依赖它，CI 会在别人机器上假红。
   `tests/golden/kappa.json` 用同一个口径，两者可以互相印证。

## 已知限制（照实说）

- **分段（piecewise）κ 判不了**：`{n<1: …; …}` 这种形状不在解析器覆盖范围里，
  会打 `无法判定` 并 exit 1。本工具的口径是 max，正常不会出现分段。
- **含符号指数的 κ 能读**（2026-11-12）：`n(xs)^(n(xs))` / `2^(n(xs))` 与常数
  指数 `n(xs)^8`、`n(xs)^(3/2)`、`sqrt(n(xs))` 一样读回 `Cost`，判阶走
  `cost_check.compare` 的 log 域。**读不回来时输出里写「读不懂」并带上原因**，
  不再只写一句"不是可解析的表达式"——否则读的人分不清"κ 变了"与"工具不认识"。
- 旧基线的 κ 字符串是**唯一**的旧值来源：本工具**不重算旧版本**（那要 git
  checkout，属于另一个工具 `cost_diff.py` 的职责）。字符串读不回来 = 无法判定。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

try:                                    # 中文 Windows 控制台默认 GBK
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
SAMPLE = os.path.join(ROOT, "文档", "样例")
BASELINE = os.path.join(ROOT, "tests", "baselines", "cost.json")

sys.path.insert(0, SRC)

NOTE = ("κ 基线（口径：max，不读 *.dispatch.yaml）。"
        "变了要人看一眼：变松（界变大）要解释，变紧是好事")

#: 判定归类（**打印什么**与**退出码**都由它决定）
TIGHTER = "更紧"          # 新 κ 严格更小（阶降了）
SAME_UP = "同阶变大"       # 阶没变，但常数因子变大了（`_profile` 只看阶，用比值兜底）
LOOSER = "变松"            # 阶升了
UNKNOWN = "无法判定"       # ⊤ / 形状判不了 / 旧文本读不回来


# --------------------------------------------------------------- 语料
def corpus():
    """`[(key, tu_path, eln_path)]`：只收"有同名 `.tu`"的样例（现在 17 个）。"""
    out = []
    if not os.path.isdir(SAMPLE):
        return out
    for name in sorted(os.listdir(SAMPLE)):
        if not name.endswith(".eln"):
            continue
        eln = os.path.join(SAMPLE, name)
        tu = os.path.join(SAMPLE, name[:-4] + ".tu")
        if not os.path.isfile(tu):
            continue
        key = os.path.relpath(tu, ROOT)[:-3].replace(os.sep, "/")
        out.append((key, tu, eln))
    return out


def _facts(ci, name):
    """与 `dsh cost` 同一份 requires 事实（判定口径必须一致）。"""
    try:
        from cost_check import facts_of_spec
        sp = (getattr(ci, "specs", None) or {}).get(name)
        return facts_of_spec(sp) if sp is not None else {}
    except Exception:                                   # noqa: BLE001
        return {}


def collect():
    """重算整份基线：`{key: {fn: {kappa, target, verdict}}}`。"""
    from parser import parse_source
    from cost_interp import CostInterp
    from cost_check import compare

    out = {}
    for key, tu_p, eln_p in corpus():
        tu = parse_source(io.open(tu_p, encoding="utf-8").read(),
                          os.path.basename(tu_p)).module
        ch = parse_source(io.open(eln_p, encoding="utf-8").read(),
                          os.path.basename(eln_p)).module
        # **max 口径**：不读分档表（见文件头「语料与口径」第 2 条）
        ci = CostInterp(ch, tu, table_path=None)
        fns = {}
        for fn in sorted(ci.fns):
            try:
                c = ci.cost_of(fn)
                t = ci.target_cost(fn)
                v = compare(c, t, _facts(ci, fn))
                fns[fn] = {"kappa": _norm(c.render()),
                           "target": _norm(t.render()) if t is not None else None,
                           "verdict": v.kind}
            except Exception as exc:                    # noqa: BLE001
                fns[fn] = {"kappa": "⊤",
                           "target": None,
                           "verdict": "unknown",
                           "error": "%s: %s" % (type(exc).__name__, exc)}
        out[key] = fns
    return out


def _norm(s):
    """规范化：只压空白。**不做别的化简**——κ 的语义比较交给阶代数。"""
    return " ".join(str(s).split())


# ------------------------------------------------- κ 文本 → Cost（render 的逆）
#
# 为什么要这个：基线里存的是**字符串**（人读、可 diff），而"变紧 / 变松"
# 必须按**现行阶代数**判。判的入口是 `cost_check.compare(新, 旧)`，它要
# `Cost` 对象——所以这里把 `Cost.render()` 的文本读回 `Cost`。
#
# **只覆盖 render 会产出的形状**（`src/cost_domain/symbolic.py::Cost.render`）：
# 常数（含分数）、`n(x)`、裸符号、`ext(x)`、`⊤`、`log2/sqrt/pow`、`+ × /`、
# `max/min`、`par(...)`。**分段 `{…}` 不支持**——解析失败一律判「无法判定」，
# 绝不猜一个方向（猜错会让"变松"静默通过）。
#
# **指数**（2026-11-12 起）：`Cost.render` 会印三种幂——
# `n(xs)^8`、`n(xs)^(3/2)`、`sqrt(n(xs))`（常数指数）与
# `n(xs)^(n(xs))`、`2^(n(xs))`（**符号指数**，`value=None` + `args[1]`）。
# 后两种以前被这里判"指数只认常数"，于是整条 κ 读不回来 → 基线门只能打
# 「无法判定」（工具读不懂被误报成 κ 变了）。现在**符号指数原样构造**，
# 交给 `Cost.pow`/`cost_check.compare` 的 log 域判阶。
#
# **名字里允许点号**（`n(recv.n_)`、`n(field.vec)`）：`Cost.size` 的接收者
# 字段链就是这样渲染的。以前点号是"不认识的字符"，`n(recv.n_)^8` 连
# 常数指数的形状都读不进来。
_TOKEN_RE = re.compile(
    r"\s*(?:(?P<top>⊤)"
    r"|(?P<num>\d+(?:/\d+)?)"
    r"|(?P<name>[^\W\d]\w*(?:\.\w+)*)"
    r"|(?P<op>[+×*/^(),\-]))")


class KappaParseError(ValueError):
    pass


def _tokens(text):
    pos, out = 0, []
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise KappaParseError("不认识的字符：%r（位置 %d）" % (text[pos], pos))
        pos = m.end()
        for kind in ("top", "num", "name", "op"):
            if m.group(kind) is not None:
                out.append((kind, m.group(kind)))
                break
    return out


class _Parser:
    def __init__(self, text):
        self.toks = _tokens(text)
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, expect=None):
        tok = self.peek()
        if tok[0] is None:
            raise KappaParseError("表达式在这里就结束了")
        if expect is not None and tok[1] != expect:
            raise KappaParseError("期望 %r，实际 %r" % (expect, tok[1]))
        self.i += 1
        return tok

    def parse(self):
        c = self.expr()
        if self.i != len(self.toks):
            raise KappaParseError("末尾还有没读完的记号：%r"
                                  % (self.toks[self.i:],))
        return c

    # expr := mul (('+' ) mul)*
    def expr(self):
        from cost_domain.symbolic import Cost
        c = self.mul()
        while self.peek() == ("op", "+"):
            self.take("+")
            c = Cost.add(c, self.mul())
        return c

    # mul := unary (('×'|'*'|'/') unary)*
    def mul(self):
        from cost_domain.symbolic import Cost
        c = self.unary()
        while True:
            tok = self.peek()
            if tok in (("op", "×"), ("op", "*")):
                self.take(tok[1])
                c = Cost.mul(c, self.unary())
            elif tok == ("op", "/"):
                self.take("/")
                c = Cost.div(c, self.unary())
            else:
                return c

    def unary(self):
        from cost_domain.symbolic import Cost
        if self.peek() == ("op", "-"):
            self.take("-")
            return Cost.mul(Cost.num(-1), self.unary())
        return self.power()

    def power(self):
        from cost_domain.symbolic import Cost
        from fractions import Fraction
        base = self.atom()
        if self.peek() != ("op", "^"):
            return base
        self.take("^")
        exp = self.exponent()
        if exp.kind == "num":
            # `Cost.num` 存的是 int 或 `Fraction`（`3/2` 不截断）——两种都要认。
            e = exp.value if isinstance(exp.value, Fraction) else Fraction(exp.value)
            if e < 0:
                # 与 `Cost.pow` 同一条规则：负指数随尺寸变小，不是成本上界。
                # 这里**显式报错**而不是交给 `Cost.pow` 静默退 ⊤。
                raise KappaParseError(
                    "指数是负数（%s）：成本随尺寸单调不减，负指数不是任何"
                    "成本的上界" % e)
            return Cost.pow(base, e)
        if exp.is_top():
            raise KappaParseError("指数是 ⊤：上界未知，不能当成指数")
        # **符号指数**：`n(xs)^(n(xs))`、`2^(n(xs))`。形状交给 `Cost.pow`
        # （`value=None` + `args[1]`），判阶走 log 域，**不在这里折算成常数**。
        return Cost.pow(base, exp)

    # exponent := '(' expr ')' | unary
    #
    # `render` 对**符号指数一律加括号**（`n(xs)^(n(xs))`，见 symbolic.py 的
    # `render`），对常数指数可能不加（`n(xs)^8`）——两种都要认。
    #
    # 不带括号时只吃一个 unary（`n^2×3` 的指数是 2，不是 2×3；`n^2^3` 按
    # 右结合读成 `n^(2^3)`，与数学惯例一致）。
    def exponent(self):
        if self.peek() != ("op", "("):
            return self.unary()
        self.take("(")
        e = self.expr()
        self.take(")")
        return e

    def atom(self):
        from cost_domain.symbolic import Cost
        kind, val = self.peek()
        if kind == "top":
            self.take()
            return Cost.top()
        if kind == "num":
            self.take()
            return Cost.num(_frac(val))
        if kind == "op" and val == "(":
            self.take("(")
            c = self.expr()
            self.take(")")
            return c
        if kind == "name":
            self.take()
            if self.peek() != ("op", "("):
                return Cost("sym", val)
            self.take("(")
            if val == "n":                       # 尺寸量：`n(text)`
                k2, v2 = self.take()
                if k2 != "name":
                    raise KappaParseError("n(...) 里只认一个尺寸量名字")
                self.take(")")
                return Cost.size(v2)
            if val == "ext":                     # 外部项：`ext(read_file)`
                k2, v2 = self.take()
                if k2 != "name":
                    raise KappaParseError("ext(...) 里只认一个名字")
                self.take(")")
                return Cost("ext", v2)
            args = [self.expr()]
            while self.peek() == ("op", ","):
                self.take(",")
                args.append(self.expr())
            self.take(")")
            if val == "log2":
                return Cost.log2(args[0])
            if val == "sqrt":
                return Cost.sqrt(args[0])
            if val == "max":
                c = args[0]
                for a in args[1:]:
                    c = Cost.max(c, a)
                return c
            if val == "min":
                c = args[0]
                for a in args[1:]:
                    c = Cost("min", None, (c, a))
                return c
            if val == "par":
                return Cost.par(args)
            raise KappaParseError("不认识的函数：%s(...)" % val)
        if kind is None:
            raise KappaParseError("表达式在这里就结束了（缺一个操作数）")
        if kind == "op":
            raise KappaParseError("这里不该出现运算符 %r（缺一个操作数）" % (val,))
        raise KappaParseError("这里不该出现 %r" % (val,))


def _frac(text):
    from fractions import Fraction
    if "/" in text:
        a, b = text.split("/", 1)
        return Fraction(int(a), int(b))
    return Fraction(int(text), 1)


def parse_kappa(text):
    """κ 文本 → `Cost`。**读不懂就抛 `KappaParseError`**（上层判「无法判定」）。

    **支持的幂**（`Cost.render` 会印出来的全部形状）：

      · 常数指数：`n(xs)^8`、`n(xs)^(3/2)`、`sqrt(n(xs))`
      · 符号指数：`n(xs)^(n(xs))`、`2^(n(xs))`、`n(xs)^(1/2×n(xs))`
        （`value=None` + `args[1]`，判阶走 `cost_check` 的 log 域）

    **不支持就报错，绝不降级**：分段 `{…}`、负指数、缺失指数都会抛
    `KappaParseError`。**静默降级是本项目反复出问题的地方**——把符号指数
    当成常数（或当成"没有 κ"）会让基线门打出假绿/假警报。
    """
    return _Parser(_norm(text)).parse()


# --------------------------------------------------------- 变紧 / 变松 / 无法判定
def classify(new_cost, old_cost):
    """两个 `Cost` 的**阶**关系 → `(归类, 说明)`。

    **就是现行阶代数**：`compare()` 的 `_order_le` 比的是
    `n^d · log^l · loglog^m` 的指数——**常数因子它看不见**（`n` 与 `2n` 同阶，
    两个方向都 `ok`）。所以这里判三次：

    | 对称判据 | 含义 | 归类 |
    |---|---|---|
    | `compare(新, 旧)` = debt | 新 > 旧 | `变松` |
    | `compare(旧, 新)` = debt | 新 < 旧 | `更紧` |
    | 两边都 ok | 同阶 | 用 `ratio` 分：> 1 → `同阶变大`；否则 `更紧`（同阶） |
    | 其它 | ⊤ / 形状判不了 | `无法判定` |

    **为什么同阶还要看比值**：阶不变但常数翻倍（多一轮同阶循环）也是"界变大"，
    只报"更紧"会把它说成好事——那正是最该有人看一眼的改动。
    比值由 `compare()` 给（同规模假设下的参考值，**不是证据**），所以只在同阶时用。
    """
    from cost_check import compare
    try:
        fwd = compare(new_cost, old_cost)       # 新 ≤ 旧 ?
        bwd = compare(old_cost, new_cost)       # 旧 ≤ 新 ?
    except Exception as exc:                    # noqa: BLE001
        return UNKNOWN, "阶代数判不了：%s: %s" % (type(exc).__name__, exc)
    if fwd.kind == "debt" and bwd.kind == "debt":
        return UNKNOWN, "两个方向都判成超界（阶代数自相矛盾）"
    if fwd.kind == "debt":
        return LOOSER, ""
    if bwd.kind == "debt":
        return TIGHTER, ""
    if fwd.kind == "ok" and bwd.kind == "ok":
        r = fwd.ratio if fwd.ratio is not None else bwd.ratio
        if r is None:
            return TIGHTER, "同阶（常数因子判不了：比值 None）"
        if r > 1.0:
            return SAME_UP, "阶没变，但常数因子 ≈ %.2f×" % r
        return TIGHTER, "同阶（常数因子 ≈ %.2f×，没变大）" % r
    return UNKNOWN, "阶代数给的是 unknown（⊤ / 尺寸量对不上 / 形状在族外）"


def order_of(new_cost, old_text):
    """`新 Cost` 相对 `旧 κ 文本` 的归类；旧文本读不回来 → `无法判定`（**不猜**）。"""
    try:
        old = parse_kappa(old_text)
    except Exception as exc:                            # noqa: BLE001
        # **把"读不懂"说出来**：以前只写"不是可解析的表达式"，读的人看不出
        # 是"κ 变了"还是"工具不认识这个形状"——后者是工具缺陷，不是漂移。
        return UNKNOWN, "旧 κ **读不懂**（%s: %s）：%s" % (
            type(exc).__name__, exc, _norm(old_text)[:60])
    return classify(new_cost, old)


def order_text(old_text, new_text):
    """同样的判定，但两边都只有字符串（target 用它）。"""
    try:
        new = parse_kappa(new_text)
    except Exception as exc:                            # noqa: BLE001
        return UNKNOWN, "新值 **读不懂**（%s: %s）：%s" % (
            type(exc).__name__, exc, _norm(new_text)[:60])
    return order_of(new, old_text)


# ------------------------------------------------------------------ 比对
def _row(what, verdict, why, old_s, new_s):
    """一行比对结果 → `(mark, text, is_bad)`（`text` 不含模块/函数名）。"""
    detail = "\n      旧: %s\n      新: %s" % (old_s, new_s)
    if verdict == LOOSER:
        return "!", "%s **变松**，请解释%s" % (what, detail), True
    if verdict == SAME_UP:
        return "!", "%s **同阶变大**（%s），请解释%s" % (what, why, detail), True
    if verdict == UNKNOWN:
        return "?", "%s 无法判定（%s）%s" % (what, why, detail), True
    note = ("（%s）" % why) if why else ""
    return "✓", "%s 更紧 ✓%s%s" % (what, note, detail), False


def check(base, cur):
    """返回 `(rows, bad)`：rows 是给人看的行，bad 是"必须 exit 1"的条数。"""
    rows, bad = [], 0
    for key, fns in sorted(cur.items()):
        if key not in base:
            rows.append(("+", key, "模块是新加的（基线里没有）"))
            bad += 1
            continue
        for fn, info in sorted(fns.items()):
            old = base[key].get(fn)
            if old is None:
                rows.append(("+", "%s.%s" % (os.path.basename(key), fn),
                             "函数是新加的（基线里没有）"))
                bad += 1
                continue
            tag = "%s.%s" % (os.path.basename(key), fn)
            # ---- κ
            old_k, new_k = _norm(old.get("kappa", "")), _norm(info["kappa"])
            if new_k != old_k:
                mark, text, is_bad = _row("κ", *order_text(old_k, new_k),
                                          old_k, new_k)
                rows.append((mark, tag, text))
                bad += 1 if is_bad else 0
            # ---- target（承诺本身；**改松也要解释**）
            old_t, new_t = old.get("target"), info["target"]
            if _norm(old_t or "") != _norm(new_t or ""):
                if old_t and new_t:
                    verdict, why = order_text(old_t, new_t)
                else:
                    verdict, why = UNKNOWN, "一边没有 target，没法比"
                mark, text, is_bad = _row("target", verdict, why,
                                          old_t, new_t)
                rows.append((mark, tag, text))
                bad += 1 if is_bad else 0
            # ---- verdict（**ok → debt/unknown 必须 exit 1**）
            ov, nv = old.get("verdict"), info["verdict"]
            if ov != nv:
                if ov == "ok":
                    rows.append(("!", tag, "verdict %s → %s：**已经不在 target 之内**，"
                                 "必须解释" % (ov, nv)))
                    bad += 1
                else:
                    rows.append(("~", tag, "verdict %s → %s" % (ov, nv)))
    for key, fns in sorted(base.items()):
        if key not in cur:
            rows.append(("-", os.path.basename(key), "基线里的模块不见了"))
            bad += 1
            continue
        for fn in sorted(set(fns) - set(cur.get(key, {}))):
            rows.append(("-", "%s.%s" % (os.path.basename(key), fn),
                         "基线里的函数不见了"))
            bad += 1
    return rows, bad


def total_fns(d):
    return sum(len(v) for v in d.values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="κ 基线：--write 写 tests/baselines/cost.json；--check 比对（默认）")
    ap.add_argument("--write", action="store_true", help="写基线（会覆盖）")
    ap.add_argument("--check", action="store_true", help="与基线比（默认）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    cur = collect()
    if not cur:
        print("✗ 语料是空的：%s 下没有「有 .tu 的 .eln」" % SAMPLE, file=sys.stderr)
        return 2

    if args.write:
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        payload = {"note": NOTE, "modules": cur}
        with io.open(BASELINE, "w", encoding="utf-8", newline="\n") as fh:
            # **`note` 排在最前**（人先读到"这份文件是干什么的"）；模块与函数
            # 在 `collect()` 里已经排好序，所以不用 `sort_keys` 也稳定。
            fh.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        rel = os.path.relpath(BASELINE, ROOT).replace(os.sep, "/")
        print("已写基线：%s（%d 模块 / %d 函数）"
              % (rel, len(cur), total_fns(cur)))
        # **自检**：每条 κ 都要能被解析回"同阶"的 Cost，否则将来变了判不了。
        bad = 0
        for key, fns in sorted(cur.items()):
            for fn, info in sorted(fns.items()):
                try:
                    c = parse_kappa(info["kappa"])
                except Exception as exc:                 # noqa: BLE001
                    bad += 1
                    print("  ⚠ %s.%s：κ **读不懂**（%s: %s）——将来它变了只能判"
                          "「无法判定」" % (os.path.basename(key), fn,
                                          type(exc).__name__, exc))
                    continue
                verdict, why = order_of(c, info["kappa"])
                if verdict != TIGHTER:
                    bad += 1
                    print("  ⚠ %s.%s：κ 解析回来不同阶（%s %s）"
                          % (os.path.basename(key), fn, verdict, why))
        if bad:
            print("  注意：%d 条 κ 无法用「阶代数」回读；它们变了会判「无法判定」。" % bad)
        return 0

    if not os.path.exists(BASELINE):
        print("没有基线文件：%s\n先跑：python tools/cost_baseline.py --write"
              % os.path.relpath(BASELINE, ROOT), file=sys.stderr)
        return 2
    base = json.load(io.open(BASELINE, encoding="utf-8")).get("modules") or {}

    rows, bad = check(base, cur)
    if not args.quiet:
        print("κ 基线：%d 模块 / %d 函数（口径 max：不读 *.dispatch.yaml，跨机器可复现）"
              % (len(cur), total_fns(cur)))
        print("  判据：两个方向都比（阶代数看不见常数因子）——阶降 / 同阶不变 → 更紧 ✓；"
              "阶升 → **变松**；同阶但常数变大 → **同阶变大**；判不出 → 无法判定；")
        print("        verdict ok → debt/unknown → **必须解释**；两者任一必须解释的都 exit 1")
    if not rows:
        if not args.quiet:
            print("✓ 与基线一致：%d / %d 条 κ 没变"
                  % (total_fns(cur), total_fns(base)))
        return 0

    mark = {"!": "✗", "?": "?", "✓": "✓", "+": "+", "-": "-", "~": "~"}
    for kind, name, text in rows:
        print("  %s %s  %s" % (mark.get(kind, kind), name, text))
    if bad:
        print("✗ %d 处需要人看一眼（变松 / 同阶变大 / 无法判定 / 退化 / 增删）："
              "确认是有意的，就 `--write` 重写基线并在提交信息里写理由。" % bad)
        return 1
    print("✓ 只有「更紧 / 同阶不变」的变化（%d 处），不算漂移" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
