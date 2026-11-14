#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_guarantees.py —— `guarantees` 从"能写没人看"变成"可判定 + 真校验"。

## 这条测试守什么

1. **分类**：可检查的谓词（`is_sorted` / `contains` / 比较式）进检查器；
   其余**必须**报 `E-Guarantee-Uncheckable`，而不是被静默忽略。
2. **运行时真的校验**：`run` 返回后求值，违反抛 `E-Guarantee-Violated`。
   以前 `guarantees` 写了等于没写——那是最坏的一种"看起来有保证"。
"""
import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import sys

import guarantees as G
from checker import check_module
from interp import Err, Interp, InterpError, Ok
from parser import parse_source

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


def spec_of(tu_src):
    mod = parse_source(tu_src, "t.tu").module
    for it in mod.children:
        if it.kind == "spec":
            return it
    return None


TU_SORTED = """
spec f(const vector<u64>& xs) -> u64 {
    effects {};
    guarantees is_sorted(xs);
}
"""

TU_BAD = """
spec f(const vector<u64>& xs) -> u64 {
    effects {};
    guarantees looks_good(xs);
}
"""

TU_CMP = """
spec f(u64 n) -> u64 {
    effects {};
    guarantees ret >= 0;
}
"""

# `T!`（可失败）返回值上的 guarantees：`ret` 指 `Ok` 里的值，`Err` 时跳过。
TU_FALLIBLE = """
error E { Bad };

spec f(const vector<u64>& xs) -> vector<u64>! {
    effects {};
    guarantees is_sorted(ret);
}
"""

OK_F = """
vector<u64>! f(const vector<u64>& xs) {
    return xs;
}
"""

# `T!` 的实现体里声明了局部 `ys`：guarantees 引用它应当**合法**。
LOCAL_VAR_AI = """
vector<u64>! f(const vector<u64>& xs) {
    vector<u64> ys = {1, 2, 3};
    return ys;
}
"""

# ---- 用户同名函数（2026-11-09 的洞）
#
# `is_sorted` 恒返回 1：如果 guarantees 真的调它，未排序输入也会"通过"。
HIJACK_AI = """
u64 is_sorted(const vector<u64>& xs) {
    return 1;
}
u64 f(const vector<u64>& xs) {
    return 0;
}
"""

# 同名函数只在**别的调用点**用，guarantees 不用它 → 必须仍然合法。
TU_NO_PRED = """
spec is_sorted(const vector<u64>& xs) -> u64 {
    effects {};
}
spec f(const vector<u64>& xs) -> u64 {
    effects {};
    guarantees ret >= 0;
}
"""

LOCAL_PRED_AI = """
u64 is_sorted(const vector<u64>& xs) {
    return 1;
}
u64 f(const vector<u64>& xs) {
    return 0;
}
"""

# 谓词引用一个**不存在**的名字（既不是形参、实现体里也没声明）。
TU_UNDEF = """
error E;

spec f(const vector<u64>& xs) -> vector<u64>! {
    effects {};
    guarantees is_sorted(ys);
}
"""


def test_classify():
    print("[1] 分类：哪些能检查、哪些不能")
    ok, bad = G.classify(spec_of(TU_SORTED))
    check(len(ok) == 1 and not bad, "is_sorted(xs) 可检查", str(bad))
    ok2, bad2 = G.classify(spec_of(TU_BAD))
    check(not ok2 and len(bad2) == 1, "未知谓词 → 不可检查", str(bad2))
    check("未知谓词" in bad2[0], "原因里指名道姓", bad2[0] if bad2 else "")

    ok3, bad3 = G.classify(spec_of(TU_CMP))
    check(len(ok3) == 1 and not bad3, "ret >= 0 可检查", str(bad3))

    # 实参个数错
    ok4, bad4 = G.classify(spec_of(
        "spec f(u64 n) -> u64 { effects {}; guarantees is_sorted(n, n); }"))
    check(bool(bad4) and "需要" in bad4[0], "实参个数错 → 不可检查", str(bad4))

    # 实参不是变量名
    ok5, bad5 = G.classify(spec_of(
        "spec f(u64 n) -> u64 { effects {}; guarantees is_sorted(n + 1); }"))
    check(bool(bad5), "实参是表达式 → 不可检查", str(bad5))


def test_checker():
    print("[2] check：可检查报 info；不可检查 / 未定义名字 / 同名侵占 都报 error")
    r = check_module(TU_SORTED, None, "t.tu")
    codes = [d.code for d in r.diagnostics]
    check("I-Guarantee-Runtime" in codes, "可检查 → I-Guarantee-Runtime", str(codes))
    check(r.ok, "info 不影响整体通过")

    r2 = check_module(TU_BAD, None, "t.tu")
    d2 = [d for d in r2.diagnostics if d.code == "E-Guarantee-Uncheckable"]
    check(len(d2) == 1, "不可检查 → E-Guarantee-Uncheckable", str([d.code for d in r2.diagnostics]))
    # 2026-11-09 加严：以前是 warning，`run` 会静默跳过——承诺位等于没有。
    check(d2 and d2[0].severity == "error", "是不可检查 → error（不再是 warning）",
          d2[0].severity if d2 else "")
    check(not r2.ok, "不可检查 → check 不通过（承诺不能永远不生效）")

    # ---- 用户的同名函数不能把 guarantees 架空（2026-11-09）
    #
    # 根因：`_call` 是「用户函数优先于同名内建」，于是 `guarantees is_sorted(xs)`
    # 实际调用户写的恒真版本。现在求值只走内置谓词；同名函数留在模块里
    # **且被 guarantees 用到** → check 直接报 error，要求改名。
    r4 = check_module(TU_SORTED, HIJACK_AI, "t.tu")
    d4 = [d for d in r4.diagnostics if d.code == "E-Guarantee-Uncheckable"]
    check(len(d4) == 1, "用户同名 is_sorted 侵占用到的谓词 → error",
          str([(d.code, d.severity) for d in r4.diagnostics]))
    check(d4 and d4[0].severity == "error", "侵占是 error", d4[0].severity if d4 else "")
    check(d4 and "内置谓词名" in d4[0].message and "改名" in d4[0].message,
          "消息说清「只能改名」", d4[0].message if d4 else "")
    check(not r4.ok, "侵占 → check 不通过")

    # 同名函数**没被 guarantees 用到**时仍合法（这就是选 (a) 而不是一刀切 (b) 的原因）
    r5 = check_module(TU_NO_PRED, LOCAL_PRED_AI, "t.tu")
    check(r5.ok, "同名函数只在别的调用点用 → 仍然合法",
          str([(d.code, d.severity) for d in r5.diagnostics]))

    # ---- 谓词引用未定义的名字：静态就能报（以前只到 run 才炸）
    r6 = check_module(TU_UNDEF, OK_F, "t.tu")
    d6 = [d for d in r6.diagnostics if d.code == "E-Guarantee-Uncheckable"]
    check(len(d6) == 1 and "未定义的名字" in d6[0].message and "ys" in d6[0].message,
          "未定义名字 `ys` → error", str([d.message for d in r6.diagnostics]))
    check(not r6.ok, "未定义名字 → check 不通过")

    # 局部变量是**合法**的（guarantees 在函数帧里求值），不许误杀。
    r7 = check_module(TU_UNDEF, LOCAL_VAR_AI, "t.tu")
    check(r7.ok and not [d for d in r7.diagnostics
                         if d.code == "E-Guarantee-Uncheckable"],
          "实现体里声明过的局部名 → 不误报",
          str([(d.code, d.severity) for d in r7.diagnostics]))

    # `T!` 上的 guarantees：info 里要写明「Ok 解包 / Err 跳过」，
    # 否则作者会以为 `ret` 是 `Ok(...)` 包装壳（2026-11-09 修的那个崩）。
    r3 = check_module(TU_FALLIBLE, None, "t.tu")
    d3 = [d for d in r3.diagnostics if d.code == "I-Guarantee-Runtime"]
    check(len(d3) == 1 and "解包" in d3[0].message,
          "T! 的 info 说明解包/跳过语义",
          d3[0].message if d3 else str([d.code for d in r3.diagnostics]))


def test_runtime():
    print("[3] run：满足则通过，违反则 E-Guarantee-Violated")

    def run(tu_src, ai_src, args):
        tu = parse_source(tu_src, "t.tu").module
        eln = parse_source(ai_src, "t.eln").module
        it = Interp(eln, tu)
        return it.call("f", args)

    TU = """
spec f(const vector<u64>& xs) -> u64 {
    effects {};
    guarantees is_sorted(xs);
}
"""
    GOOD = """
u64 f(const vector<u64>& xs) {
    sort(xs);
    return 0;
}
"""
    BAD = """
u64 f(const vector<u64>& xs) {
    return 0;
}
"""
    try:
        run(TU, GOOD, [[3, 1, 2]])
        check(True, "排好序 → 通过")
    except InterpError as exc:
        check(False, "排好序 → 通过", str(exc))

    try:
        run(TU, BAD, [[3, 1, 2]])
        check(False, "没排序 → 应该被抓", "没有抛异常")
    except InterpError as exc:
        check("E-Guarantee-Violated" in str(exc), "没排序 → E-Guarantee-Violated", str(exc))

    # 比较式：ret >= 0
    TU2 = """
spec f(u64 n) -> u64 {
    effects {};
    guarantees ret >= 0;
}
"""
    GOOD2 = "u64 f(u64 n) { return n; }\n"
    check(run(TU2, GOOD2, [5]) == 5, "ret >= 0 通过，且返回值不被改写")

    # ---- **用户同名函数不能架空 guarantees**（2026-11-09 的洞）
    #
    # 以前 `_call` 是「用户函数优先于同名内建」，于是 `guarantees is_sorted(xs)`
    # 实际调用 `HIJACK_AI` 里那个恒返回 1 的版本——未排序输入也"通过"。
    # 现在谓词只走内置实现：同一个模块、同一份输入，必须判违反。
    try:
        run(TU, HIJACK_AI, [[3, 1, 2]])
        check(False, "同名恒真函数 → 不能架空承诺", "没有抛异常（洞还在）")
    except InterpError as exc:
        check("E-Guarantee-Violated" in str(exc),
              "同名恒真函数 → 用内置谓词判违反", str(exc))
    # 合法输入仍然通过——证明不是靠"一律失败"糊弄过去。
    check(run(TU, HIJACK_AI, [[1, 2, 3]]) == 0,
          "同名函数存在时，内置谓词对合法输入仍放行")
    # 用户函数本身还能正常调用：(a) 不禁用它，只是 guarantees 不用它。
    tu_h = parse_source(TU, "t.tu").module
    ai_h = parse_source(HIJACK_AI, "t.eln").module
    check(Interp(ai_h, tu_h).call("is_sorted", [[3, 1, 2]]) == 1,
          "用户版 is_sorted 在普通调用点仍生效")

    # ---- 不可检查的谓词：run 也不许静默跳过
    #
    # 以前 `_check_guarantees` 对分类失败的表达式直接 `return`——承诺位
    # 写了等于没写。现在 `check` 报 error；绕过 check 直接调 `Interp` 也一样。
    TU_UNC = """
spec f(const vector<u64>& xs) -> u64 {
    effects {};
    guarantees looks_good(xs);
}
"""
    try:
        run(TU_UNC, BAD, [[3, 1, 2]])
        check(False, "不可检查的谓词 → run 不静默", "没有抛异常")
    except InterpError as exc:
        check("E-Guarantee-Uncheckable" in str(exc),
              "不可检查的谓词 → run 报 E-Guarantee-Uncheckable", str(exc))

    # ---- `T!`（可失败）返回值：先解包再判谓词；`Err` 跳过（2026-11-09）
    #
    # 以前 `ret` 直接绑 `Ok(...)`：`is_sorted(ret)` 对 `Ok` 调 `len()` →
    # `TypeError: object of type 'Ok' has no len()`（check 却只报 info）。
    # 现在的语义：`Ok(v)` → 谓词看 `v`；`Err` → 跳过（没有正常返回）。
    _res = run(TU_FALLIBLE, OK_F, [[1, 2, 3]])
    check(isinstance(_res, Ok) and _res.v == [1, 2, 3],
          "T! + Ok：对解包值校验通过，返回值仍是 Ok(...)", repr(_res))

    try:
        run(TU_FALLIBLE, OK_F, [[3, 1, 2]])
        check(False, "T! + Ok 未排序 → E-Guarantee-Violated", "没有抛异常")
    except InterpError as exc:
        check("E-Guarantee-Violated" in str(exc) and "TypeError" not in str(exc),
              "T! + Ok 未排序 → E-Guarantee-Violated（无宿主异常）", str(exc))

    ERR_F = """
vector<u64>! f(const vector<u64>& xs) {
    return Err(E::Bad);
}
"""
    try:
        _res = run(TU_FALLIBLE, ERR_F, [[3, 1, 2]])
        check(isinstance(_res, Err), "T! + Err：跳过 guarantees，不误报", repr(_res))
    except InterpError as exc:
        check(False, "T! + Err：跳过 guarantees，不误报", str(exc))

    # 比较式也要解包：`Ok(0) > 0` 不应抛宿主异常；`0 > 0` 要真的失败。
    TU_POS = """
error E;
spec f(u64 n) -> u64! {
    effects {};
    guarantees ret > 0;
}
"""
    POS_F = "u64! f(u64 n) { return n; }\n"
    _res = run(TU_POS, POS_F, [5])
    check(isinstance(_res, Ok) and _res.v == 5, "T! + ret > 0：5 通过", repr(_res))
    try:
        run(TU_POS, POS_F, [0])
        check(False, "T! + ret > 0：0 违反", "没有抛异常")
    except InterpError as exc:
        check("E-Guarantee-Violated" in str(exc), "T! + ret > 0：0 违反", str(exc))

    # **宿主异常不许泄漏**：`is_sorted` 收到标量 → 只能是 Elench 诊断。
    TU_HOST = """
error E;
spec f(u64 n) -> u64! {
    effects {};
    guarantees is_sorted(ret);
}
"""
    try:
        run(TU_HOST, POS_F, [5])
        check(False, "谓词类型不匹配 → Elench 诊断", "没有抛异常")
    except InterpError as exc:
        check("E-Guarantee-Violated" in str(exc) and "TypeError" not in str(exc)
              and "object of type" not in str(exc),
              "谓词类型不匹配 → Elench 诊断（无 TypeError）", str(exc))

    # **谓词引用未定义名字**：clean 的 Elench 诊断，不是 `InterpError: ...` 嵌套。
    try:
        run(TU_UNDEF, OK_F, [[1, 2, 3]])
        check(False, "谓词引用未定义名字 → 诊断", "没有抛异常")
    except InterpError as exc:
        check("E-Guarantee-Violated" in str(exc) and "未绑定的名字 ys" in str(exc)
              and "TypeError" not in str(exc),
              "谓词引用未定义名字 → Elench 诊断", str(exc))


# ---- `guarantees { … }` 块形（2026-11-11）：花括号只是作用域
TU_BLOCK = """
spec f(const vector<u64>& xs, u64 n) -> u64 {
    effects {};
    guarantees {
        is_sorted(xs);
        ret > n;
    };
}
"""

# `;` 是两条独立断言：第 2 条必须**真被求值**（`ys` 未绑定 → 指名第 2 条）。
TU_TWO = """
spec f(const vector<u64>& xs) -> u64 {
    effects {};
    guarantees {
        true;
        is_sorted(ys);
    };
}
"""

# `||` 是一条短路析取：左边为真 → 右边根本不求值，`ys` 未绑定也不炸。
TU_SHORT = """
spec f(const vector<u64>& xs) -> u64 {
    effects {};
    guarantees {
        true || is_sorted(ys);
    };
}
"""


# `&&` 也是一条短路合取：左边为假 → 右边不求值（`ys` 未绑定也不会炸出
# 「无法判定」）；整条断言本身当然为假，所以报的是「第 1/1 条不满足」。
TU_SHORT_AND = """
spec f(const vector<u64>& xs) -> u64 {
    effects {};
    guarantees {
        false && is_sorted(ys);
    };
}
"""


def test_blocks():
    """`guarantees { e1; e2; };` / `{ a && b }` / `{ e };`（2026-11-11）。

    **用户定的语义**：花括号只是作用域；`;` = 两条独立断言（逐条求值、
    逐条报错，能指名第 k 条）；`&&` = 一条短路合取。**两者不能互相转换**：
    把 `&&` 拆成 `;` 会改变求值（`n > 0 && xs.at(0) == 1` 在 n==0 时越界）。
    """
    print("[4] `guarantees { … }` 块形：逐条独立 + `&&` 短路 + 只收表达式")

    # ---- 解析：块里每条各自一个 clause（行/列取该表达式自己的位置）
    mod = parse_source(TU_BLOCK, "t.tu").module
    spec = next(it for it in mod.children if it.kind == "spec")
    body = next(c for c in spec.children if c.kind == "spec-body")
    cl = [c for c in body.children if c.kind == "clause" and c.name == "guarantees"]
    check(len(cl) == 2, "块里两条断言 → AST 里两个 clause", str(len(cl)))
    check(cl[0].line == 5 and cl[1].line == 6,
          "每条保留自己的行号（诊断能指到具体一条）",
          f"{cl[0].line},{cl[1].line}" if cl else "")
    check([len(c.children) for c in cl] == [1, 1],
          "每个 clause 只挂一条表达式（不合并）", str([len(c.children) for c in cl]))

    # ---- 分类：两条都可检查
    ok, bad = G.classify(spec)
    check(len(ok) == 2 and not bad, "块里两条都进可检查集合", str(bad))

    # ---- check：块形也报 I-Guarantee-Runtime（不是被忽略）
    r = check_module(TU_BLOCK, None, "t.tu")
    codes = [d.code for d in r.diagnostics]
    check("I-Guarantee-Runtime" in codes and r.ok,
          "块形 guarantees 也进检查器并报 info", str(codes))

    # ---- 运行期：两条都要求值；第 2 条失败必须指名「第 2/2 条」
    def run(tu_src, ai_src, args):
        tu = parse_source(tu_src, "t.tu").module
        eln = parse_source(ai_src, "t.eln").module
        return Interp(eln, tu).call("f", args)

    AI_OK = """
u64 f(const vector<u64>& xs, u64 n) {
    sort(xs);
    return n + 1;
}
"""
    check(run(TU_BLOCK, AI_OK, [[3, 1, 2], 5]) == 6,
          "两条都满足 → 通过")

    AI_SECOND_BAD = """
u64 f(const vector<u64>& xs, u64 n) {
    sort(xs);
    return n;
}
"""
    try:
        run(TU_BLOCK, AI_SECOND_BAD, [[3, 1, 2], 5])
        check(False, "第 2 条失败 → 报错", "没有抛异常")
    except InterpError as exc:
        msg = str(exc)
        check("第 2/2 条" in msg and "E-Guarantee-Violated" in msg
              and "ret > n" in msg,
              "第 2 条失败 → 诊断指名「第 2/2 条」并带出断言", msg)

    AI_FIRST_BAD = """
u64 f(const vector<u64>& xs, u64 n) {
    return n + 1;
}
"""
    try:
        run(TU_BLOCK, AI_FIRST_BAD, [[3, 1, 2], 5])
        check(False, "第 1 条失败 → 报错", "没有抛异常")
    except InterpError as exc:
        msg = str(exc)
        check("第 1/2 条" in msg and "is_sorted" in msg,
              "第 1 条失败 → 诊断指名「第 1/2 条」", msg)

    # ---- `;` 与 `&&` 不可互换：同一条「右边引用未绑定名字」
    #   · `{ true; is_sorted(ys) }` → **第 2 条独立求值** → 报错（第 2/2 条）
    #   · `{ true || is_sorted(ys) }` → **短路** → 右边不求值 → 通过
    AI_ONE = """
u64 f(const vector<u64>& xs) {
    return 0;
}
"""
    try:
        run(TU_TWO, AI_ONE, [[3, 1, 2]])
        check(False, "`;` 的两条都求值（右边未绑定 → 报错）", "没有抛异常")
    except InterpError as exc:
        msg = str(exc)
        check("第 2/2 条" in msg and "未绑定" in msg,
              "`;` 的两条都求值 → 第 2 条报「未绑定」", msg)

    check(run(TU_SHORT, AI_ONE, [[3, 1, 2]]) == 0,
          "`||` 短路：左边 true → 右边（未绑定名字）不求值")

    # `&&` 短路：左边 false → 右边不求值。整条断言为假 → 报「第 1/1 条」，
    # 但**不能**出现「未绑定」（那说明右边被求值了）。
    try:
        run(TU_SHORT_AND, AI_ONE, [[3, 1, 2]])
        check(False, "`&&` 短路：整条为假应报违反", "没有抛异常")
    except InterpError as exc:
        msg = str(exc)
        check("第 1/1 条" in msg and "未绑定" not in msg,
              "`&&` 短路：左边 false → 右边（未绑定名字）不求值", msg)

    # ---- 块里第 k 条不可检查：checker 也要指名第几条
    r_bad = check_module(
        "spec f(const vector<u64>& xs) -> u64 {\n"
        "    effects {};\n"
        "    guarantees {\n"
        "        is_sorted(xs);\n"
        "        looks_good(xs);\n"
        "    };\n"
        "}\n", None, "t.tu")
    d_bad = [d for d in r_bad.diagnostics if d.code == "E-Guarantee-Uncheckable"]
    check(len(d_bad) == 1 and "第 2 条" in d_bad[0].message,
          "块里第 2 条不可检查 → 消息指名「第 2 条」",
          d_bad[0].message if d_bad else str([d.code for d in r_bad.diagnostics]))

    # ---- 块内只收表达式：语句 → E-Clause-BlockStmt
    for bad_src, label in (
        ("guarantees { if (n > 0) { } };", "if 语句"),
        ("guarantees { while (n > 0) { } };", "while 语句"),
        ("guarantees { u64 x = 1; };", "声明语句"),
    ):
        rr = parse_source(
            "spec f(u64 n) -> u64 { effects {}; %s }" % bad_src, "t.tu")
        cc = [d.code for d in rr.diagnostics]
        mm = "; ".join(d.message for d in rr.diagnostics)
        check("E-Clause-BlockStmt" in cc and "只能是断言表达式" in mm,
              f"块内 {label} → E-Clause-BlockStmt", mm[:160])

    # ---- 空块：没有断言可承诺 → E-Clause-EmptyExpr
    rr = parse_source("spec f(u64 n) -> u64 { effects {}; guarantees { }; }", "t.tu")
    cc = [d.code for d in rr.diagnostics]
    check("E-Clause-EmptyExpr" in cc, "空块 → E-Clause-EmptyExpr", str(cc))

    # ---- `requires { … }` 同构：两条前提各自一条 clause
    mod_r = parse_source(
        "spec g(u64 lo, u64 hi) -> u64 {\n"
        "    effects {};\n"
        "    requires { lo <= hi; hi <= 100; };\n"
        "}\n", "t.tu").module
    sp_r = next(it for it in mod_r.children if it.kind == "spec")
    bd_r = next(c for c in sp_r.children if c.kind == "spec-body")
    cl_r = [c for c in bd_r.children if c.kind == "clause" and c.name == "requires"]
    check(len(cl_r) == 2, "`requires { … }` 也是两条独立前提", str(len(cl_r)))


def main() -> int:
    test_classify()
    test_checker()
    test_runtime()
    test_blocks()
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
