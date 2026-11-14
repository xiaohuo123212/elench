#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_bound.py —— G29：`while` 上界提取测试

运行：
  cd src; $env:PYTHONIOENCODING="utf-8"; python test_bound.py
"""

import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但**这批测试原先住在 `src/`**，里面大量
#: `os.path.join(HERE, "..", "文档")` / `os.path.join(ROOT, "tools")` 都假设
#: 「本文件在 src/、上一级是仓库根」。搬家时改 200 处路径比让 `HERE` 继续指 src 贵得多，
#: 所以保留旧语义：`HERE` = src/，`CASEDIR` = 本文件真正所在的目录。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import sys

sys.stdout.reconfigure(encoding="utf-8")

from bound import Bound, bound_of_while, expr_to_bound
from parser import parse_source

_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def mod_of(src):
    r = parse_source(src, "t.eln")
    check(not r.diagnostics, "样例能解析",
          "; ".join(d.code for d in r.diagnostics))
    return r.module


def _desc(n, kind, acc=None):
    if acc is None:
        acc = []
    if n.kind == kind:
        acc.append(n)
    for c in n.children:
        _desc(c, kind, acc)
    return acc


def upper(src, label=None):
    """跑一个函数，返回每个 while 的 (上界文本 or '⊤', 原因)。"""
    mod = mod_of(src)

    def repeats_of(lb):
        if lb == "in_range":
            return Bound("log2", left=Bound("sub", left=Bound("var", value="hi"),
                                            right=Bound("var", value="lo")))
        return None

    fn = _desc(mod, "fn")[0]
    out = []
    for w in _desc(fn, "while"):
        b, why = bound_of_while(w, repeats_of, fn)
        out.append((b.render() if b else "⊤", why))
    return out


def test_size_compare():
    print("[1] 来源①：尺寸比较")
    r = upper("u64 f(const vector<u64>& xs) { u64 i = 0; "
              "while (i < xs.length()) { i = i + 1; } return i; }")
    check(r[0][0] == "xs.length()", "i < xs.length() → xs.length()", str(r))

    r = upper("u64 f(const vector<u64>& xs) { const u64 n = xs.length(); u64 i = 0; "
              "while (i < n) { i = i + 1; } return i; }")
    check(r[0][0] == "xs.length()",
          "i < n（const 绑定）→ 展开回 xs.length()（报告里不该出现局部变量名）", str(r))


def test_zero_init():
    print("[2] 计数器初值为 0 → 不再写 e - i")
    r = upper("u64 f(const vector<u64>& xs) { u64 i = 0; "
              "while (i < 10) { i = i + 1; } return i; }")
    check(r[0][0] == "10", "i 从 0 起 → 上界 10（不是 10 - i）", str(r))

    # 初值不是 0 时保留减法
    r = upper("u64 f(const vector<u64>& xs) { u64 i = 5; "
              "while (i < xs.length()) { i = i + 1; } return i; }")
    check(r[0][0] == "(xs.length() - i)", "i 从 5 起 → xs.length() - i", str(r))


def test_and():
    print("[3] `&&` 组合取 min")
    r = upper("u64 f(const vector<u64>& xs) { const u64 n = xs.length(); u64 i = 0; "
              "while (i < n && i < xs.length()) { i = i + 1; } return i; }")
    check("min(" in r[0][0], "`&&` → min", str(r))


def test_predicate():
    print("[4] 来源②：谓词的 repeats")
    r = upper("u64 g(const vector<u64>& xs) { u64 lo = 0; u64 hi = xs.length(); "
              "while (in_range(lo, hi)) { lo = lo + 1; } return lo; }")
    check(r[0][0] == "log2((hi - lo))", "谓词 repeats 代入", str(r))


def test_unprovable():
    print("[5] 来源③：抽不出时明确返回 None")
    r = upper("u64 f(const vector<u64>& xs) { u64 i = 0; "
              "while (i < unknown_pred(i)) { i = i + 1; } return i; }")
    check(r[0][0] == "⊤", "抽不出 → ⊤（绝不静默猜）", str(r))
    check("repeats" in r[0][1], "原因里指出 repeats 这条出路", str(r[0][1]))


def test_no_silence():
    print("[6] 抽不出时必须给出原因，不静默")
    cases = [
        "while (i < unknown_pred(i)) { i = i + 1; }",
        "while (a * b < 10) { a = a + 1; }",
    ]
    for c in cases:
        r = upper("u64 f(const vector<u64>& xs) { u64 i = 0; u64 a = 0; u64 b = 0; "
                  + c + " return i; }")
        b, why = r[0]
        check(b == "⊤" and bool(why), f"抽不出有原因：{c[:20]}", f"{b} / {why}")


def test_loop_progresses():
    """E21 回归：循环体必须能推进计数器，否则不许给上界。

    这是**健全性**回归，不是风格检查：`while (j > 0) { s = s + 1; }` 是死循环，
    真实成本 ∞，而修这个 bug 之前它拿到了 `O(n)`。
    """
    print("[7] E21：循环必须能推进")
    from bound import progresses, _descendants

    def prog(src):
        mod = parse_source(src, "t.eln").module
        ws = _descendants(mod, "while")
        return progresses(ws[0])

    def fn(body):
        return ("u64! f(u64 n, const vector<u64>& q) {\n"
                "    u64 s = 0;\n    " + body + "\n    return s;\n}")

    cases = [
        # (说明, 循环体, 期望能不能推进)
        ("计数式推进",            "u64 i = 0; while (i < n) { s = s + 1; i = i + 1; }", True),
        ("倒数式推进",            "u64 j = n; while (j > 0) { s = s + 1; j = j - 1; }", True),
        ("双指针都动",            "u64 a = 0; u64 b = n; while (a < b) { a = a + 1; b = b - 1; }", True),
        ("容器 pop",              "while (q.length() > 0) { q.pop(); }", True),
        ("按引用传给被调函数",     "while (q.length() > 0) { t(& q); }", True),
        ("死循环：计数器不动",     "u64 j = n; while (j > 0) { s = s + 1; }", False),
        ("死循环：计数式不动",     "u64 i = 0; while (i < n) { s = s + 1; }", False),
        ("死循环：容器只读",       "while (q.length() > 0) { s = s + 1; }", False),
        # **按值传不算推进**——这条区分是故意的：`f(i)` 改不了 i。
        ("按值传不算推进",         "u64 i = 0; while (i < n) { s = s + g(i); }", False),
        # **复合赋值也是推进**（2026-11-10）。以前只扫 `assign`，于是
        # `i += 1` 被判成「计数器在循环体里从未被修改」——写的和说的正好相反。
        ("复合赋值 += 1",         "u64 i = 0; while (i < n) { s = s + 1; i += 1; }", True),
        ("复合赋值 += 2（步长）",  "u64 i = 0; while (i < n) { s = s + 1; i += 2; }", True),
        ("复合赋值 -= 1",         "u64 j = n; while (j > 0) { s = s + 1; j -= 1; }", True),
        # 方向不对的复合赋值**不算推进**：`while (i < n) { i -= 1; }` 永远到不了 n。
        ("死循环：-= 配 <",        "u64 i = 0; while (i < n) { s = s + 1; i -= 1; }", False),
        ("死循环：+= 配 >",        "u64 j = n; while (j > 0) { s = s + 1; j += 1; }", False),
        ("死循环：+= 0",           "u64 i = 0; while (i < n) { s = s + 1; i += 0; }", False),
    ]
    for label, body, want in cases:
        ok, why = prog(fn(body))
        check(ok == want, f"{label} → 可推进={want}", f"得到 {ok}（{why}）")

    # **上界必须仍是上界**：`while (i < n) { …; i += 2; }` 的真圈数是
    # `ceil(n/2)`，我们只给 `n`。`n >= ceil(n/2)`，偏松但**不偏小**——
    # 要收紧到 ceil(n/2) 得再证明「步长恒为 2 且初值为 0」，这次不做。
    got = upper(fn("u64 i = 0; while (i < n) { s = s + 1; i += 2; }"))
    check(bool(got) and got[0][0] == "n",
          "`i += 2` 的上界是 n（保守；不缩成 ceil(n/2)）", str(got))
    # 文案不许说反话：判据只是"没找到对它的修改"，不是"你从来没改过"。
    _, why = prog(fn("u64 i = 0; while (i < n) { s = s + 1; }"))
    check("从未被修改" not in why, "「不推进」的文案不再说「从未被修改」", why)
    check("没有被推进" in why and "+=" in why,
          "「不推进」的文案列出认得的推进写法", why)

    # 死循环在**成本侧**必须变成 ⊤（不给有限上界）
    from cost_interp import CostInterp
    dead = fn("u64 j = n; while (j > 0) { s = s + 1; }")
    k = CostInterp(parse_source(dead, "t.eln").module, None).cost_of("f").render()
    check("⊤" in k, "死循环的 κ 含 ⊤", k)


def test_repeats_render():
    """repeats 里的裸名字是**圈数（标量）**，不许渲染成容器尺寸 `n(名字)`。

    2026-11-09：`repeats log2(k)` 曾经渲染成 `log2(n(n))`（成本域对未知名字
    的兜底是 `Cost.size`，那是 `xs.length()` 的写法）。`n(名字)` 在求值时
    查不到就按 1 算 ⇒ κ **不再是上界**，而且读报告的人会以为 k 是容器。
    """
    print("[8] repeats 的裸名字渲染成标量（不是 n(名字)）")
    from cost_interp import CostInterp

    tu = parse_source(
        "spec pred(const vector<u64>& xs, u64 k) -> bool {\n"
        "    effects {};\n    repeats log2(k);\n}\n"
        "spec g(const vector<u64>& xs, u64 n) -> u64 {\n"
        "    effects {};\n"
        "    target { time: { max: O(log(n)) }, allocation: { max: 0 }, };\n}\n",
        "t.tu").module
    ch = parse_source(
        "bool pred(const vector<u64>& xs, u64 k) { return k > 1; }\n"
        "u64 g(const vector<u64>& xs, u64 n) {\n"
        "    u64 j = n;\n"
        "    while (pred(xs, j)) { j = j / 2; }\n"
        "    return j;\n}\n", "t.eln").module
    got = CostInterp(ch, tu).cost_of("g").render()
    check("log2(k)" in got, "repeats log2(k) → κ 里是 log2(k)", got)
    check("n(k)" not in got, "不许渲染成 n(k)（容器尺寸的写法）", got)


def test_repeats_plus_const():
    """`repeats` 支持 `绑定项 + 整数字面量`（2026-11-10）——**只在这个入口**。

    为的是写得出**严格**圈数上界：二分的真圈数是 `floor(log2 m) + 1`，
    所以 `repeats log(hi - lo) + 1`；`repeats log(hi - lo)` 本身差一圈。

    放开范围**只有一种形状**（`expr_to_bound(allow_add=True)`）：
    一侧是整数字面量、另一侧是普通的界表达式（`-` / `log2` / 名字 / 尺寸）。
    默认入口（`while` 比较那条）**仍然拒收 `+`**——否则 `while (a * b < 10)`
    会被当成计数器比较，给出假上界。
    """
    print("[9] repeats 的 `+ 整数字面量`（窄入口）")
    from cost_domain.bound import expr_to_bound

    def expr_of(src):
        mod = parse_source(
            "spec f(u64 k) -> bool { effects {}; repeats %s; }\n" % src,
            "t.tu").module
        for c in _desc(mod, "clause"):
            if (c.name or "") == "repeats" and c.children:
                return c.children[0]
        return None

    def render(src, allow_add=False):
        b = expr_to_bound(expr_of(src), allow_add=allow_add)
        return b.render() if b is not None else "⊤"

    # ① 放开的那一种形状
    check(render("log2(k) + 1", True) == "log2(k) + 1",
          "log2(k) + 1 → 界 `log2(k) + 1`", render("log2(k) + 1", True))
    check(render("1 + log2(k)", True) == "log2(k) + 1",
          "字面量写在左侧也认（同一形状）", render("1 + log2(k)", True))
    check(render("log(hi - lo) + 1", True) == "log2((hi - lo)) + 1",
          "log(hi - lo) + 1 → `log2((hi - lo)) + 1`（样例写法）",
          render("log(hi - lo) + 1", True))

    # ② 默认入口（while 比较）**不许**借这个口子
    check(render("log2(k) + 1") == "⊤",
          "默认入口仍拒收 `+`（比较那里不许误当计数器）", render("log2(k) + 1"))

    # ③ 只放开一层：嵌套 `+` 不收（不做算术化简）
    check(render("log2(k) + 1 + 2", True) == "⊤",
          "嵌套 `+` 不收（只放开「绑定项 + 一个字面量」）",
          render("log2(k) + 1 + 2", True))
    check(render("log2(k + 1)", True) == "⊤",
          "`log` 的实参里不收 `+`（形状只到顶层）", render("log2(k + 1)", True))

    # ④ 比较里的 `+` 仍然推不出（形状没被全面放开）
    r = upper("u64 f(u64 n) { u64 i = 0; while (i < n + 1) { i = i + 1; } return i; }")
    check(r[0][0] == "⊤", "`while (i < n + 1)` 仍推不出（不是计数器比较）", str(r))

    # ⑤ 端到端：κ 里带上这个 +1，判阶不变（常数项）
    from cost_interp import CostInterp
    tu = parse_source(
        "spec pred(u64 k) -> bool { effects {}; repeats log2(k) + 1; }\n"
        "spec g(u64 n) -> u64 {\n"
        "    effects {};\n"
        "    target { time: { max: O(log(n)) }, allocation: { max: 0 }, };\n}\n",
        "t.tu").module
    ch = parse_source(
        "bool pred(u64 k) { return k > 0; }\n"
        "u64 g(u64 n) {\n"
        "    u64 j = n;\n"
        "    while (pred(j)) { j = j / 2; }\n"
        "    return j;\n}\n", "t.eln").module
    got = CostInterp(ch, tu).cost_of("g").render()
    check("log2(k)" in got and "+ 1" in got,
          "repeats log2(k) + 1 → κ 里带 +1（不再是 log2(k) 单独）", got)


def test_block_scan_guard():
    """分块 / 窗口扫描：`while (i + 3 <= n)`（2026-11-10）。

    C / C++ / Rust 里「每轮吃 k 个字节」的自然写法是 `i + k <= n`
    （base64 / CRC32 / hexdump 都这么写），以前一律记 ⊤——最自然的写法
    反而丢掉整条契约。放开范围**只有**「计数器 + 非负整数字面量」与
    「老规则能推出的界」比较这一种形状，`*` / `/` 仍拒。

    **2026-11-10（第二轮）**：证明得出「初值 0 + 每轮恰增 c」时收紧成
    `ceil(e / c)`（`Bound.kind == "ceildiv"`），证不出仍给 `e`。
    """
    print("[10] 分块扫描：`while (i + 3 <= n)`")
    PRE = ("u64 f(const vector<u64>& xs) { const u64 n = xs.length(); "
           "u64 i = 0; u64 cnt = 0; %s return cnt; }")

    def one(guard, body="cnt = cnt + xs.at(i); i = i + 3;"):
        return upper(PRE % ("while (%s) { %s }" % (guard, body)))

    # ① 收紧组：守卫的 `c` 与步长相等（默认 step 3）⇒ ceil(e / c)
    for guard in ("i + 3 <= n", "i + 3 < n", "n >= i + 3", "n > i + 3",
                  "3 + i <= n", "i + 3 <= xs.length()"):
        r = one(guard)
        check(r[0][0] == "ceil(xs.length() / 3)",
              f"{guard} → ceil(xs.length() / 3)（收紧 3 倍）", str(r))
        check("分块扫描" in r[0][1], f"{guard} 的原因说明是分块扫描", r[0][1])
    # 步长与守卫的 c **不等**（`i + 1 < n` 配 `i += 3`）⇒ 不收紧
    r = one("i + 1 < n")
    check(r[0][0] == "xs.length()",
          "`i + 1 < n` 配步长 3 → 仍是 xs.length()（c 不等不收紧）", str(r))

    # ② 行为必须逐字不变的老形状 —— **但 `<=` 不在此列**：`i <= n` 的真圈数
    #    比 `i < n` 多一整圈（界是 `e - i + 1`）。以前这里把它也钉成
    #    `xs.length()`，钉的其实是那个 bug（2026-11-10 修，见 [11]）。
    for guard, want in (("i < n", "xs.length()"), ("j < n", "xs.length()"),
                        ("i <= n", "xs.length() + 1")):
        var = "j" if guard.startswith("j") else "i"
        g = guard.replace(var, var)
        body = f"cnt = cnt + xs.at({var}); {var} = {var} + 2;"
        r = upper(PRE.replace("u64 i = 0;", "u64 i = 0; u64 j = 0;")
                  % ("while (%s) { %s }" % (g, body)))
        check(r[0][0] == want, f"{guard} → {want}", str(r))

    # ③ 反面清单：必须仍然 ⊤（不许因为放开 `+` 把算术当计数器）
    PRE2 = ("u64 f(const vector<u64>& xs) { const u64 n = xs.length(); "
            "u64 i = 0; u64 a = 0; u64 b = 0; u64 cnt = 0; %s return cnt; }")
    bad = [
        ("i * 3 <= n", "乘法不算计数器"),
        ("i / 3 <= n", "除法不算计数器"),
        ("a * b < 10", "乘法比较（老回归）"),
        ("i + 3 > n", "计数器在高侧，可能是死循环"),
        ("i + 3 >= n", "计数器在高侧，可能是死循环"),
        ("i + 3 <= n + 1", "字面量在上界侧（不是计数器+字面量）"),
        ("i + 3 <= b + 4", "两侧都是「名字 + 字面量」"),
        ("i + b <= n", "另一侧是变量不是字面量"),
    ]
    for guard, why in bad:
        r = upper(PRE2 % ("while (%s) { cnt = cnt + xs.at(0); i = i + 1; }" % guard))
        check(r[0][0] == "⊤", f"{guard} 仍 ⊤（{why}）", str(r))

    # ④ `<=` 且 c = 0：真实圈数是 n + 1，取 n 会**偏小** ⇒ 明确不收
    r = one("i + 0 <= n")
    check(r[0][0] == "⊤", "`i + 0 <= n` 仍 ⊤（取 n 会偏小一整圈）", str(r))
    check("偏小" in r[0][1] or "不收" in r[0][1], "原因说清为什么不收", r[0][1])
    # 而 `i + 0 < n` 等于既有的 `i < n`，收
    r = one("i + 0 < n")
    check(r[0][0] == "xs.length()", "`i + 0 < n` = `i < n` → 收", str(r))

    # ⑤ 初值不是 0：拿不到静态初值 ⇒ ⊤（宁可 ⊤ 不猜；i64 的负初值会让 n 偏小）
    r = upper("u64 f(const vector<u64>& xs) { const u64 n = xs.length(); "
              "u64 i = 1; u64 cnt = 0; "
              "while (i + 3 <= n) { cnt = cnt + xs.at(0); i = i + 3; } return cnt; }")
    check(r[0][0] == "⊤", "`i + 3 <= n` 但初值非 0 → ⊤", str(r))

    # ⑥ E21 仍然管得住这个形状：体内不推进 / 推错方向都不给上界
    from bound import progresses, _descendants

    def prog(guard, body):
        mod = parse_source("u64 f(u64 n) { u64 i = 0; u64 s = 0; "
                           "while (%s) { %s } return s; }" % (guard, body), "t.eln").module
        return progresses(_descendants(mod, "while")[0])

    for guard, body, want, why in [
        ("i + 3 <= n", "s = s + 1; i = i + 3;", True, "推进了计数器"),
        ("i + 3 <= n", "s = s + 1; i += 3;", True, "复合赋值也算推进"),
        ("i + 3 <= n", "s = s + 1;", False, "体内根本不改 i ⇒ 死循环"),
        ("i + 3 <= n", "s = s + 1; i -= 1;", False, "方向反了 ⇒ 到不了 n"),
        ("n > i + 3", "s = s + 1; i = i + 3;", True, "翻转写法也认推进量"),
        # 翻转写法 `e > i + c` 的推进方向仍是「i 变大」：直接返回 `>` 会把
        # 这条合法写法判成死循环（`_compound_never_advances` 会读反方向）。
        ("n > i + 3", "s = s + 1; i += 3;", True, "翻转写法 + 复合赋值"),
        ("n > i + 3", "s = s + 1; i -= 3;", False, "翻转写法方向反了"),
    ]:
        ok, msg = prog(guard, body)
        check(ok == want, f"E21：`{guard}` + `{body}` → 可推进={want}（{why}）", msg)

    # ⑦ 端到端：κ 必须是有限的，而且带着 n(data)
    from cost_interp import CostInterp
    src = ("u64 f(const vector<u64>& data) { const u64 n = data.length(); "
           "u64 i = 0; u64 cnt = 0; "
           "while (i + 3 <= n) { cnt = cnt + data.at(i); i = i + 3; } return cnt; }")
    k = CostInterp(parse_source(src, "t.eln").module, None).cost_of("f").render()
    check("⊤" not in k, "`i + 3 <= n` 的 κ 不再是 ⊤", k)
    check("n(data)" in k, "`i + 3 <= n` 的 κ 带 n(data)", k)
    check("/ 3" in k, "`i + 3 <= n` 的 κ 收紧成 n(data) / 3（步长 3）", k)

    # ⑧ **收紧的边界**（2026-11-10 第二轮）：证不出「初值 0 + 每轮恰增 c」
    #    就必须退回旧上界 `e`——**绝不给出比真实小的数**。
    print("   ⑧ 收紧的边界：证不出就退回 e")
    # (说明, 循环体, 期望的界)
    guards = [
        # 步长是变量：圈数取决于 k，用 n/3 只会偏小
        ("i += k", "cnt = cnt + xs.at(i); i += k;", "xs.length()"),
        # 步长比守卫的 c 小：真圈数 ≈ n/2 > n/3
        ("i += 2", "cnt = cnt + xs.at(i); i = i + 2;", "xs.length()"),
        # 步长比守卫的 c 大：n/3 仍是上界，但「每轮恰增 c」证不出 ⇒ 不收紧
        ("i += 6", "cnt = cnt + xs.at(i); i = i + 6;", "xs.length()"),
        # 推进写在 if 里：不保证每轮都推进，真圈数可以到 n
        ("if 里推进", "cnt = cnt + xs.at(0); if (n > 0) { i = i + 3; }",
         "xs.length()"),
        # 体内写两次：推进量不止 c
        ("写两次", "cnt = cnt + xs.at(0); i = i + 3; i = i + 3;", "xs.length()"),
        # 按引用传出：被调函数可能改小它（改小 ⇒ 圈数变多）。
        # **注**：`u64&` 目前过不了类型检查（E-Ref-Scalar，引用只对可变聚合成
        # 立），所以这一格今天还写不出可编译的程序——这里只验证 `bound.py` 的
        # 那条护栏本身；等标量引用（或"计数器是结构体字段"）落地时它就要紧。
        ("按引用传出", "cnt = cnt + xs.at(0); g2(& i); i = i + 3;", "xs.length()"),
    ]
    for label, body, want in guards:
        # `g2` 放在 `f` **后面**：`upper()` 取模块里第一个 `fn`，而第一个
        # 必须是带 while 的那个（解析不要求先声明后使用）。
        src2 = ("u64 f(const vector<u64>& xs, u64 k) { "
                "const u64 n = xs.length(); u64 i = 0; u64 cnt = 0; "
                "while (i + 3 <= n) { " + body + " } return cnt; } "
                "u64 g2(u64& z) { z = z + 0; return 0; }")
        r = upper(src2)
        check(r and r[0][0] == want, f"`{label}` → {want}（不收紧）", str(r))
    # 循环**外**还有一次赋值：进循环时的值不是 0 ⇒ 不收紧
    r = upper("u64 f(const vector<u64>& xs) { const u64 n = xs.length(); "
              "u64 i = 0; u64 cnt = 0; i = 4; "
              "while (i + 3 <= n) { cnt = cnt + xs.at(0); i = i + 3; } return cnt; }")
    check(r and r[0][0] == "xs.length()",
          "循环外另有一次赋值 → xs.length()（初值证不出 0）", str(r))

    # ⑨ **整除边界**：`e - i0 = 6`、`c = 3` ⇒ 2 圈，**不是 3**；
    #    `e - i0 = 7` ⇒ 7/3（向上取整真的向上，但不多算一整圈）。
    print("   ⑨ 整除边界：6/3 = 2 圈，不是 3")
    from bound import bound_of_while as _bow
    from cost_interp import bound_to_cost_inst as _btc
    from cost_check import evaluate as _eval

    def bound_cost(n_lit):
        src3 = ("u64 f() { const u64 n = " + n_lit + "; u64 i = 0; "
                "u64 cnt = 0; while (i + 3 <= n) { cnt = cnt + 1; i = i + 3; } "
                "return cnt; }")
        mod = parse_source(src3, "t.eln").module
        b, _why = _bow(_descendants(mod, "while")[0], None,
                       _descendants(mod, "fn")[0])
        return b, _btc(b)

    for n_lit, want_loops, want_render in (("6", 2, "2"), ("7", 7 / 3, "7/3")):
        b, c = bound_cost(n_lit)
        check(b.render() == f"ceil({n_lit} / 3)",
              f"n = {n_lit} 的界渲染成 ceil({n_lit} / 3)", b.render())
        check(c.render() == want_render,
              f"n = {n_lit} 的界在成本域里是 {want_render}（不是多算一圈）", c.render())
        v = _eval(c, {})
        check(abs(v - want_loops) < 1e-9,
              f"n = {n_lit}：圈数 {v:g} = {want_loops:g}", str(v))

    # ⑩ **κ 仍是上界**：跑一次，ops 必须 ≤ κ（n = 64 与 256 两档）
    print("   ⑩ κ 仍是上界：ops ≤ κ（n = 64 / 256）")
    from interp import Interp, ops_enable, ops_of, ops_reset
    import interp as _ip

    ch = parse_source(src, "t.eln").module
    kappa = CostInterp(ch, None).cost_of("f")
    old = _ip.ops_enable(True)
    try:
        for n in (64, 256):
            ops_reset()
            it = Interp(ch, None)
            it.call("f", [[1] * n])
            ops = ops_of("f")
            kv = _eval(kappa, {"data": float(n)})
            check(ops <= kv, f"n = {n}：ops {ops} ≤ κ {kv:g}（收紧后仍没破界）",
                  f"ops={ops} κ={kv}")
    finally:
        _ip.ops_enable(old)


def test_le_guard_extra_round():
    """`<=` / `>=` 比 `<` / `>` **多一整圈**（2026-11-10 修的真实破界）。

    破界长什么样（修复前实测）：

        u64 fl(u64 n) {
            u64 s = 0;
            for (u64 i = 0; i <= n; i = i + 1) { s = s + i; }
            return s;
        }

    界被记成 `n`（与 `i < n` 同一格），于是 **n = 0 时 κ = 20 < ops = 22**。
    `i <= n` 的真圈数是 `n + 1`：多出来的不只是"一次推进"，守卫被求值的次数
    也从 `n + 1` 变成 `n + 2`，而 κ 的常数项只预留了一份。

    这里钉三件事：
      ① 界：`i <= n` → `n + 1`；`i < n` / `i > 0` **逐字不变**；
      ② 端到端：`κ ≥ ops` 在 n = 0,1,2,3,8 **全部**成立（修前 n = 0 破界）；
      ③ 降序 `i >= 0`（`i = i - 1`）同样多一圈，`i > 0` 不变。
    """
    print("[11] `<=` / `>=` 多一圈：κ ≥ ops 在 n = 0,1,2,3,8")
    from cost_check import evaluate as _eval
    from cost_interp import CostInterp
    from interp import Interp, ops_enable, ops_of, ops_reset
    import interp as _ip

    # ---- ① 界的文本：`<=` 带 +1，`<` / `>` 不变
    PRE = ("u64 f(u64 n) { u64 i = 0; u64 cnt = 0; "
           "while (%s) { cnt = cnt + i; i = i + 1; } return cnt; }")
    for guard, want in (("i < n", "n"), ("i <= n", "n + 1")):
        r = upper(PRE % guard)
        check(r[0][0] == want, f"`{guard}` 的界 = {want}", str(r))
    PRE_DESC = ("i64 f(i64 n) { i64 j = n; i64 cnt = 0; "
                "while (%s) { cnt = cnt + j; j = j - 1; } return cnt; }")
    # 注意：`upper()` 直接调 `bound_of_while`，**没有**递减循环那套初值信息
    # （`dec_vars` / `init_of` 由 `cost_interp` 的 wrapper 提供），所以走的是
    # 普通的 `计数器 op 界` 那一支，界写成 `(j - 0)`。两条路的 `>=` 都 +1。
    for guard, want in (("j > 0", "(j - 0)"), ("j >= 0", "(j - 0) + 1")):
        r = upper(PRE_DESC % guard)
        check(r[0][0] == want, f"`{guard}`（降序）的界 = {want}", str(r))

    # ---- ② / ③ 端到端：κ 表达式 + ops 实测
    CH = (
        "u64 le_for(u64 n) {\n"
        "    u64 s = 0;\n"
        "    for (u64 i = 0; i <= n; i = i + 1) { s = s + i; }\n"
        "    return s;\n}\n"
        "u64 lt_for(u64 n) {\n"
        "    u64 s = 0;\n"
        "    for (u64 i = 0; i < n; i = i + 1) { s = s + i; }\n"
        "    return s;\n}\n"
        "u64 le_while(u64 n) {\n"
        "    u64 s = 0;\n"
        "    u64 i = 0;\n"
        "    while (i <= n) { s = s + i; i = i + 1; }\n"
        "    return s;\n}\n"
        "i64 ge_desc(i64 n) {\n"
        "    i64 s = 0;\n"
        "    i64 i = n;\n"
        "    while (i >= 0) { s = s + i; i = i - 1; }\n"
        "    return s;\n}\n"
        "i64 gt_desc(i64 n) {\n"
        "    i64 s = 0;\n"
        "    i64 i = n;\n"
        "    while (i > 0) { s = s + i; i = i - 1; }\n"
        "    return s;\n}\n")
    ch = mod_of(CH)
    ci = CostInterp(ch, None)
    kappa = {f: ci.cost_of(f) for f in ("le_for", "lt_for", "le_while",
                                        "ge_desc", "gt_desc")}
    # `<` / `>` 的 κ **必须逐字不变**（`n×14`，不是 `(n + 1)×14`）
    for f in ("lt_for", "gt_desc"):
        check("(n + 1)" not in kappa[f].render(),
              f"`{f}` 的 κ 不该多一圈（`<` / `>` 逐字不变）", kappa[f].render())
    for f in ("le_for", "le_while", "ge_desc"):
        check("(n + 1)" in kappa[f].render(),
              f"`{f}` 的 κ 必须多一圈（`<=` / `>=`）", kappa[f].render())

    old = _ip.ops_enable(True)
    try:
        for f, sizes in (("le_for", (0, 1, 2, 3, 8)),
                         ("le_while", (0, 1, 2, 3, 8)),
                         ("ge_desc", (0, 1, 2, 3, 8)),
                         ("lt_for", (0, 1, 2, 3, 8)),
                         ("gt_desc", (0, 1, 2, 3, 8))):
            for n in sizes:
                ops_reset()
                Interp(ch, None).call(f, [n])
                ops = ops_of(f)
                kv = _eval(kappa[f], {"n": float(n)})
                check(ops <= kv,
                      f"{f}(n={n})：ops {ops} ≤ κ {kv:g}（`<=` 不许破界）",
                      f"ops={ops} κ={kv} κ式={kappa[f].render()}")
    finally:
        _ip.ops_enable(old)


def main():
    test_size_compare()
    test_zero_init()
    test_and()
    test_predicate()
    test_unprovable()
    test_no_silence()
    test_loop_progresses()
    test_repeats_render()
    test_repeats_plus_const()
    test_block_scan_guard()
    test_le_guard_extra_round()


    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
