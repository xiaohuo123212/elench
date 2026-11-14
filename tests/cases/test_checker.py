#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
test_checker.py —— M0 检查器测试

每个错误码至少一条阳性用例；样例文件必须通过。

运行：
  cd src; $env:PYTHONIOENCODING="utf-8"; python test_checker.py
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

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from checker import check_files, check_module
from parser import parse_source

SAMPLE = os.path.join(HERE, "..", "文档", "样例")

_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def codes(res):
    return [d.code for d in res.diagnostics]


def has(res, code):
    return code in codes(res)


# ---------------------------------------------------------------- 样例

def test_sample():
    print("[1] 真实样例")
    res = check_files(os.path.join(SAMPLE, "calculator.tu"),
                      os.path.join(SAMPLE, "calculator.eln"))
    check(res.ok, "calculator 通过",
          "; ".join(f"{d.code}@{d.line}" for d in res.diagnostics[:3]))
    check(len(res.functions) == 11, "11 个函数（第 4 步加了 more_digits）",
          f"实际 {len(res.functions)}")

    ev = next(f for f in res.functions if f.name == "evaluate")
    check(ev.state == "?", "evaluate 有体有 target → 状态待 cost 判定")
    check(ev.is_pub, "evaluate 是 pub")

    tk = next(f for f in res.functions if f.name == "tokenize")
    check(tk.state == "unchecked", "tokenize 有体无 target → unchecked",
          f"实际 {tk.state}")


# ---------------------------------------------------------------- F-1 / F-2

def test_signatures():
    print("[2] F-1 实现必须有 spec / F-2 签名一致")

    tu = "spec a(x: u64) -> bool { effects {}; }"
    ai = "bool a(x: u64) { return true; }"
    check(check_module(tu, ai).ok, "签名一致时通过")

    ai2 = "bool b(x: u64) { return true; }"
    r = check_module(tu, ai2)
    check(has(r, "E-Fn-NoSpec"), "实现无 spec → E-Fn-NoSpec", str(codes(r)))
    # **2026-11-10：`.tu` 可选**（规格 §8.9）——「没有 spec」=「没有契约声明」，
    # 默认档**不是错误**（不拦 run/build），`--strict` 才回到 error。
    check(all(d.severity == "warning" for d in r.diagnostics
              if d.code == "E-Fn-NoSpec"),
          "默认档 E-Fn-NoSpec 是 warning（不阻断）",
          str([(d.code, d.severity) for d in r.diagnostics]))
    check(r.ok, "默认档下无 spec 仍算通过（没有契约 ≠ 有错）", str(codes(r)))
    r_strict_no = check_module(tu, ai2, strict=True)
    check(any(d.code == "E-Fn-NoSpec" and d.severity == "error"
              for d in r_strict_no.diagnostics),
          "--strict 下 E-Fn-NoSpec 仍是 error（保持旧行为）",
          str([(d.code, d.severity) for d in r_strict_no.diagnostics]))
    # **完全没有 `.tu`**：不逐条报，只给一条汇总 info（check 仍 exit 0）。
    r_bare = check_module("", "u64 b(x: u64) { return x; }", has_tu=False)
    check(r_bare.ok, "没有 .tu：check 通过", str(codes(r_bare)))
    check(any(d.code == "I-Fn-NoSpec-Module" and d.severity == "info"
              for d in r_bare.diagnostics),
          "没有 .tu：汇总一条 info「N 个函数未声明契约」",
          str([(d.code, d.severity) for d in r_bare.diagnostics]))
    check(not has(r_bare, "E-Fn-NoSpec"), "没有 .tu：不逐条报 E-Fn-NoSpec")

    ai3 = "bool a(x: u64, y: u64) { return true; }"
    r = check_module(tu, ai3)
    check(has(r, "E-Spec-SigMismatch"), "参数个数不同 → E-Spec-SigMismatch", str(codes(r)))

    ai4 = "bool a(x: i64) { return true; }"
    r = check_module(tu, ai4)
    check(has(r, "E-Spec-SigMismatch"), "参数类型不同 → 报错")

    ai5 = "u64 a(x: u64) { return 1; }"
    r = check_module(tu, ai5)
    check(has(r, "E-Spec-SigMismatch"), "返回类型不同 → 报错")

    # 复杂类型也要比对
    tu2 = "spec f(v: vector<u8>) -> (i64! { effects {}; }"
    ai6 = "func f(v: vector<u8>) -> (i64! { return ??? ; }"
    # 上面故意用了不存在的记号，只为确认签名一致时不会误报签名错误
    r = check_module(tu2, ai6)
    check(not has(r, "E-Spec-SigMismatch"), "复杂类型一致时无签名错误", str(codes(r)))


# ---------------------------------------------------------------- F-4 / F-8

def test_ai_no_decl():
    print("[3] F-4 / F-8：.ai 不得有声明与 use")

    tu = "spec a() -> bool { effects {}; }"
    r = check_module(tu, "const X: u64 = 1;")
    check(has(r, "E-Ai-HasDecl"), ".ai 有 const → E-Ai-HasDecl", str(codes(r)))

    r = check_module(tu, "use core.x;")
    check(has(r, "E-Ai-HasUse"), ".ai 有 use → E-Ai-HasUse", str(codes(r)))

    r = check_module(tu, "enum E { A }")
    check(has(r, "E-Ai-HasDecl"), ".ai 有 enum → 报错")

    r = check_module(tu, "spec b() -> bool { effects {}; }")
    check(has(r, "E-Ai-HasDecl"), ".ai 有 spec → 报错")


# ---------------------------------------------------------------- choose

BASE_TU = """
spec host(const vector<u64>& xs) -> u64 {
    effects   {};
    target { time: { max: O(xs.length()) }, };
    benchmodel {
        data  { xs = random_u64s(); }
        scale { xs in range(1, 1024, *2); }
    };
    choose L(const vector<u64>& xs) -> u64 {
        over     {f};
        objective  time;
    };
}
"""

BASE_AI = """
u64 host(const vector<u64>& xs) {
    const u64 n = xs.length();
    if (n == 0) { return 0; }
    return ?!L(xs);
} kernels L(const vector<u64>& xs) -> u64 {
    f { return 1; }
}
"""


def test_choose():
    print("[4] choose 规则（B-3 / B-8 / B-10 / 宿主 / 实参）")

    r = check_module(BASE_TU, BASE_AI)
    check(r.ok, "合法 choose 通过", str(codes(r)))

    # B-3：?! 无对应 choose
    ai = BASE_AI.replace("?!L(xs)", "?!M(xs)")
    r = check_module(BASE_TU, ai)
    check(has(r, "E-Choose-Undeclared"), "?!M 无 choose → E-Choose-Undeclared",
          str(codes(r)))

    # B-8：over 为空
    tu = BASE_TU.replace("over     {f};", "over     {};")
    r = check_module(tu, BASE_AI)
    check(has(r, "E-Choose-Empty"), "over 为空 → E-Choose-Empty", str(codes(r)))

    # B-8：over 超过 4
    tu = BASE_TU.replace("over     {f};", "over     {k0, k1, k2, k3, k4};")
    r = check_module(tu, BASE_AI)
    check(has(r, "E-Choose-TooManyCandidates"), "over 5 个 → 超限", str(codes(r)))

    # B-10：实现里有 over 名单外的候选
    ai = BASE_AI.replace("    f { return 1; }", "    f { return 1; }\n    nope { return 2; }")
    r = check_module(BASE_TU, ai)
    check(has(r, "E-Choose-Undeclared"), "候选不在 over → E-Choose-Undeclared",
          str(codes(r)))

    # 宿主：?! 写到别的函数里
    tu2 = BASE_TU + """
spec other(const vector<u64>& xs) -> u64 { effects {}; }
"""
    ai2 = BASE_AI + """
u64 other(const vector<u64>& xs) {
    const u64 n = xs.length();
    return ?!L(n);
}
"""
    r = check_module(tu2, ai2)
    check(has(r, "E-Choose-WrongHost"), "?! 跑到别人函数里 → E-Choose-WrongHost",
          str(codes(r)))

    # 实参个数
    ai3 = BASE_AI.replace("?!L(xs)", "?!L(xs, xs)")
    r = check_module(BASE_TU, ai3)
    check(has(r, "E-Choose-ArityMismatch"), "实参多于参数 → E-Choose-ArityMismatch",
          str(codes(r)))

    # 签名不一致
    ai4 = BASE_AI.replace("kernels L(const vector<u64>& xs) -> u64",
                          "kernels L(const vector<u8>& xs) -> u64")
    r = check_module(BASE_TU, ai4)
    check(has(r, "E-Spec-SigMismatch"), "kernels 签名与 choose 不一致 → E-Spec-SigMismatch",
          str(codes(r)))

    # 旧的 pending 子句已下线（v0.69：usemodel 也一并删除）
    tu3 = BASE_TU.replace("over     {f};", "over     {f};\n        pending {f};")
    r = check_module(tu3, BASE_AI)
    check(has(r, "E-Choose-PendingClause"), "pending 子句 → E-Choose-PendingClause",
          str(codes(r)))


def test_pending():
    print("[5] B-16 pending 双向校验（写在 over 里）")

    # 情形一：two candidates，g 未实现且没标 pending → 漏报
    tu = BASE_TU.replace("over     {f};", "over     {f, g};")
    r = check_module(tu, BASE_AI)
    check(has(r, "E-Choose-PendingMismatch"), "g 缺体未标 pending → 漏报", str(codes(r)))

    # 情形二：标上 pending，应当通过
    tu2 = BASE_TU.replace("over     {f};", "over     {f, g pending};")
    r2 = check_module(tu2, BASE_AI)
    check(r2.ok, "标 pending 后通过", str(codes(r2)))
    check(r2.choices["L"]["pending"] == ["g"], "pending 被记录",
          str(r2.choices.get("L")))

    # 情形三：已实现却仍标 pending → 声明过期
    tu3 = BASE_TU.replace("over     {f};", "over     {f pending};")
    r3 = check_module(tu3, BASE_AI)
    check(has(r3, "E-Choose-PendingMismatch"), "已实现却标 pending → 过期",
          str(codes(r3)))


# ---------------------------------------------------------------- B-15

def test_whole_body():
    print("[6] B-15：?! 不得构成整个函数体")

    ai_bad = """
fn host(xs: []u64) -> u64 {
    return ?!L(xs);
} kernels L(xs: []u64) -> u64 {
    f { return 1; }
} end fn
"""
    r = check_module(BASE_TU, ai_bad)
    check(has(r, "E-Choose-WholeBody"), "整函数体是 ?! → E-Choose-WholeBody",
          str(codes(r)))

    r2 = check_module(BASE_TU, BASE_AI)
    check(not has(r2, "E-Choose-WholeBody"), "有自己逻辑时不报", str(codes(r2)))


# ---------------------------------------------------------------- 嵌套

def test_nesting():
    print("[7] 嵌套 kernels：深度上限 + 自带标签")

    tu = BASE_TU.replace(
        "over     {f};",
        "over     {f};")
    ai_ok = """
fn host(xs: []u64) -> u64 {
    val n = xs.length();
    if n == 0 { return 0; } end if
    return ?!L(xs);
} kernels L(xs: []u64) -> u64 {
    f {
        val y = ?!M(xs);
        return y;
    } kernels M(xs: []u64) -> u64 {
        m1 { return 1; }
    }
} end fn
"""
    # 嵌套块不在 .tu 里声明 → 但它是词法内嵌的，checker 只按标签对账
    r = check_module(tu, ai_ok)
    check(not has(r, "E-Choose-NestTooDeep"), "一层嵌套不报深度超限", str(codes(r)))

    # 三层（depth=3）才超限
    ai_deep = """
fn host(xs: []u64) -> u64 {
    val n = xs.length();
    if n == 0 { return 0; } end if
    return ?!L(xs);
} kernels L(xs: []u64) -> u64 {
    f {
        val y = ?!M(xs);
        return y;
    } kernels M(xs: []u64) -> u64 {
        m1 {
            val z = ?!N(xs);
            return z;
        } kernels N(xs: []u64) -> u64 {
            n1 { return 1; }
        }
    }
} end fn
"""
    r2 = check_module(tu, ai_deep)
    check(has(r2, "E-Kernels-Nested"), "嵌套 kernels → E-Kernels-Nested",
          str(codes(r2)))


# ---------------------------------------------------------------- 状态

def test_states():
    print("[9] 状态派生（C-COST-3-a）")

    # draft：无体无 target
    tu = "spec a() -> bool { effects {}; }"
    r = check_module(tu, "")
    f = next(x for x in r.functions if x.name == "a")
    check(f.state == "draft", "无体无 target → draft", f.state)

    # planned：有 target 无体
    tu2 = "spec a() -> bool { effects {}; target { time: { max: O(1) }, }; }"
    r2 = check_module(tu2, "")
    f2 = next(x for x in r2.functions if x.name == "a")
    check(f2.state == "planned", "有 target 无体 → planned", f2.state)

    # unchecked：有体无 target
    tu3 = "spec a() -> bool { effects {}; }"
    r3 = check_module(tu3, "bool a() { return true; }")
    f3 = next(x for x in r3.functions if x.name == "a")
    check(f3.state == "unchecked", "有体无 target → unchecked", f3.state)

    # ?：有体有 target（待 cost）
    tu4 = "spec a() -> bool { effects {}; target { time: { max: O(1) }, }; }"
    r4 = check_module(tu4, "bool a() { return true; }")
    f4 = next(x for x in r4.functions if x.name == "a")
    check(f4.state == "?", "有体有 target → 待 cost 判定", f4.state)


# ---------------------------------------------------------------- 完整性

def test_integration():
    print("[10] 组合：多错误同时出现仍能报全")

    tu = """
spec a(x: u64) -> bool {
    effects {};
    choose L(x: u64) -> bool { over {f, missing}; objective time; } end choose
} end spec
"""
    ai = """
const Z: u64 = 1;
fn b(x: u64) -> bool { return true; } end fn
"""
    r = check_module(tu, ai)
    check(has(r, "E-Ai-HasDecl"), "报 .ai 声明")
    check(has(r, "E-Fn-NoSpec"), "报无 spec")
    # **档位**（2026-11-10，规格 §8.9）：默认档是 warning，不是 error。
    check(any(d.code == "E-Fn-NoSpec" and d.severity == "warning"
              for d in r.diagnostics),
          "无 spec 在默认档是 warning（不阻断）",
          str([(d.code, d.severity) for d in r.diagnostics]))
    check(has(r, "E-Choose-PendingMismatch"), "报缺实现又没标 pending")
    check(len(r.diagnostics) >= 3, "多错误同时报出", f"实际 {len(r.diagnostics)}")


# ---------------------------------------------------------------- 严格模式

def test_strict():
    print("[11] 严格模式 L3 / L4 / L5")

    clean_tu = "spec a(x: u64) -> bool { effects {}; }"
    clean_ai = "bool a(x: u64) { return true; }"
    check(check_module(clean_tu, clean_ai, strict=True).ok,
          "规范代码在严格模式通过",
          str(codes(check_module(clean_tu, clean_ai, strict=True))))

    # L3：省略 end if
    lax_ai = "bool a(x: u64) { if (x == 0) { return true; } return false; }"
    r_lax = check_module(clean_tu, lax_ai)
    check(not has(r_lax, "L3-EndOmitted"), "v0.68 下 L3 已下线", str(codes(r_lax)))
    r_strict = check_module(clean_tu, lax_ai, strict=True)
    check(not has(r_strict, "L3-EndOmitted"), "严格模式也不再报 L3-EndOmitted",
          str(codes(r_strict)))

    # L4：元组 4 个元素
    tu4 = "spec b() -> (u64, u64, u64, u64) { effects {}; }"
    r = check_module(tu4, "", strict=True)
    check(has(r, "L4-TupleTooLong"), "4 元组 → L4-TupleTooLong", str(codes(r)))

    tu3 = "spec b() -> (u64, u64, u64) { effects {}; }"
    r3 = check_module(tu3, "", strict=True)
    check(not has(r3, "L4-TupleTooLong"), "3 元组合法", str(codes(r3)))

    # L4：表达式元组
    ai4 = "u64 c() { val t = (1, 2, 3, 4); return 1; }"
    tu4b = "spec c() -> u64 { effects {}; }"
    r4 = check_module(tu4b, ai4, strict=True)
    check(has(r4, "L4-TupleTooLong"), "表达式 4 元组 → L4", str(codes(r4)))

    # L5：行宽
    long_line = "// " + "x" * 120
    r5 = check_module(long_line + "\n" + clean_tu, "", strict=True)
    check(has(r5, "L5-LineTooLong"), "超长行 → L5-LineTooLong", str(codes(r5)))
    r5b = check_module(long_line + "\n" + clean_tu, "", strict=False)
    check(not has(r5b, "L5-LineTooLong"), "非严格模式不报 L5", str(codes(r5b)))


def test_no_ai_file():
    print("[12] 没有 .ai 文件（纯计划态模块）")
    tu = ("spec a(const vector<u64>& xs) -> u64 { effects {}; target { time: { max: O(1) }, };\n"
          "    benchmodel { data { xs = random_u64s(); } "
          "scale { xs in range(1, 64, *2); } }\n"
          "    choose L(const vector<u64>& xs) -> u64 { over {k pending}; objective time; }\n"
          "}\n")
    r = check_module(tu, None)
    check(r.ok, "无 .ai 时通过", str(codes(r)))
    fa = next(f for f in r.functions if f.name == "a")
    check(fa.state == "planned", "有 target 无体 → planned", fa.state)
    check(r.choices["L"]["pending"] == ["k"], "pending 被记录")
    # B-16：k 未实现且已列 pending → 一致，不报
    check(not has(r, "E-Choose-PendingMismatch"), "pending 一致", str(codes(r)))


def test_immutability():
    print("[13] let / var 可变性")
    tu = "spec a() -> u64 { effects {}; }"
    r = check_module(tu, "u64 a() { const u64 x = 1; x = 2; return x; }")
    check(has(r, "E-Assign-Immutable"), "给 const 赋值 → E-Assign-Immutable", str(codes(r)))
    r2 = check_module(tu, "u64 a() { u64 x = 1; x = 2; return x; }")
    check(r2.ok, "给可变绑定赋值合法", str(codes(r2)))
    r3 = check_module(tu, "u64 a() { const (u64, u64) t = (1, 2); t.0 = 3; return 1; }")
    check(has(r3, "E-Assign-Immutable"), "给 const 的字段赋值也拦", str(codes(r3)))
    r4 = check_module(tu, "u64 a() { const u64 x = 1; x += 2; return x; }")
    check(has(r4, "E-Assign-Immutable"), "+= 也拦", str(codes(r4)))
    r5 = check_module(
        tu, "u64 a() { const u64 x = 1; if (true) { u64 x = 2; x = 3; } return x; }")
    check(r5.ok, "内层 var 遮蔽外层 let 时合法", str(codes(r5)))


# ---------------------------------------------------------------- T13

def test_while_bounds():
    print("[14] T13：while 的迭代上界")

    tu = """spec pred(u64 lo, u64 hi) -> bool {
    effects {};
    repeats log(hi - lo);
}
spec a(const vector<u64>& xs) -> u64 { effects {}; }"""

    # ① 条件含尺寸比较 → 无诊断
    ok = ("u64 a(const vector<u64>& xs) { u64 i = 0; "
          "while (i < xs.length()) { i = i + 1; } return i; }")
    r = check_module(tu, ok)
    check(not has(r, "E-While-NoBound"), "T13-① 尺寸比较：无诊断", str(codes(r)))

    # ① const 绑定的尺寸变量
    ok2 = ("u64 a(const vector<u64>& xs) { const u64 n = xs.length(); u64 i = 0; "
           "while (i < n) { i = i + 1; } return i; }")
    r = check_module(tu, ok2)
    check(not has(r, "E-While-NoBound"), "T13-① const 尺寸变量：无诊断", str(codes(r)))

    # ② 谓词 + repeats
    ok3 = ("u64 a(const vector<u64>& xs) { u64 i = 0; "
           "while (pred(i, xs.length())) { i = i + 1; } return i; }")
    r = check_module(tu, ok3)
    check(not has(r, "E-While-NoBound"), "T13-② 谓词 repeats：无诊断", str(codes(r)))

    # ③ 推不出：宽松是 warning，严格是 error
    bad = ("u64 a(const vector<u64>& xs) { u64 i = 0; "
           "while (i < bound_of(xs.length())) { i = i + 1; } return i; }")
    r_lax = check_module(tu, bad)
    w = [d for d in r_lax.diagnostics if d.code == "E-While-NoBound"]
    check(len(w) == 1 and w[0].severity == "warning",
          "T13-③ 宽松模式：warning", str([(d.code, d.severity) for d in r_lax.diagnostics]))

    # ④ 常量上界：`while (i < 10)` 可推，不该报警
    ok4 = ("u64 a(const vector<u64>& xs) { u64 i = 0; "
           "while (i < 10) { i = i + 1; } return i; }")
    r = check_module(tu, ok4)
    check(not has(r, "E-While-NoBound"), "T13-④ 常量上界：无诊断", str(codes(r)))

    r_strict = check_module(tu, bad, strict=True)
    e = [d for d in r_strict.diagnostics if d.code == "E-While-NoBound"]
    check(len(e) == 1 and e[0].severity == "error",
          "T13-③ 严格模式：error", str([(d.code, d.severity) for d in r_strict.diagnostics]))

    # hint 里要给出改法
    check(any("repeats" in d.message for d in w),
          "T13-③ hint 指出 repeats 这条路", str([d.message[:30] for d in w]))


# ---------------------------------------------------------------- E-6

def test_member_sep():
    print("[15] E-6：类型命名空间 MUST 用 `::`")
    tu = """enum Token { Number(i64), Plus }
spec f(u64 x) -> i64 { effects {}; }"""
    bad = "i64 f(u64 x) {\n    const Token t = Token.Plus;\n    return 1;\n}"
    r = check_module(tu, bad, "m")
    check(has(r, "E-Member-WrongSep"), "枚举成员用 `.` → E-Member-WrongSep",
          str(codes(r)))

    ok = "i64 f(u64 x) {\n    const Token t = Token::Plus;\n    return 1;\n}"
    r2 = check_module(tu, ok, "m")
    check(not has(r2, "E-Member-WrongSep"), "用 `::` 不报", str(codes(r2)))

    # 值上的方法调用不受影响
    tu2 = """enum Token { Plus }
spec g(const vector<u64>& xs) -> u64 { effects {}; }"""
    ok2 = ("u64 g(const vector<u64>& xs) {\n"
           "    return xs.length();\n}")
    r3 = check_module(tu2, ok2, "m")
    check(not has(r3, "E-Member-WrongSep"), "`xs.length()` 不报", str(codes(r3)))


# ---------------------------------------------------------------- F-2 形参名

def test_param_names():
    print("[16] F-2：形参名（E-Spec-ParamName）")

    # `target` 里的尺寸量按 spec 名写；实现改名 → check 阶段就该报
    tu = ("spec f(const vector<u64>& xs) -> u64 {\n"
          "    effects {};\n"
          "    target { time: { max: O(xs.length()) }, };\n}\n")
    ai = ("u64 f(const vector<u64>& ys) {\n"
          "    return ys.length();\n}\n")
    r = check_module(tu, ai, "m")
    check(has(r, "E-Spec-ParamName"),
          "target 引用 spec 名、实现改名 → E-Spec-ParamName", str(codes(r)))
    _d = [x for x in r.diagnostics if x.code == "E-Spec-ParamName"]
    check(bool(_d) and "`xs`" in _d[0].message and "`ys`" in _d[0].message,
          "消息里点名两边的名字", str(_d[0].message if _d else ""))

    # 名字一致 → 不报
    check(not has(check_module(tu, ai.replace("ys", "xs"), "m"),
                  "E-Spec-ParamName"), "名字一致 → 不报")

    # 纯 `effects {}`（没有 target/requires/guarantees）→ 名字无关紧要，不报
    tu_e = "spec g(const vector<u64>& xs) -> u64 { effects {}; }"
    ai_e = "u64 g(const vector<u64>& ys) { return 0; }"
    check(not has(check_module(tu_e, ai_e, "m"), "E-Spec-ParamName"),
          "纯 effects → 不报（没有子句按名字引用它）")

    # target 是常数 O(1)：名字没被引用 → 不报
    tu_c = ("spec h(const vector<u64>& xs) -> u64 { effects {}; "
            "target { time: { max: O(1) }, }; }")
    ai_c = "u64 h(const vector<u64>& ys) { return 0; }"
    check(not has(check_module(tu_c, ai_c, "m"), "E-Spec-ParamName"),
          "target O(1) 没引用形参名 → 不报")

    # `requires` 也按 spec 名写（cost 的 facts / 运行期断言都用它）
    tu_r = ("spec k(const vector<u64>& xs) -> u64 { effects {}; "
            "requires xs.length() > 0; }")
    ai_r = "u64 k(const vector<u64>& ys) { return ys.length(); }"
    check(has(check_module(tu_r, ai_r, "m"), "E-Spec-ParamName"),
          "requires 引用形参名 → 报")

    # `guarantees` 的普通改名已由 E-Guarantee-Uncheckable 报（不重复报）
    tu_g = ("spec q(const vector<u64>& xs) -> u64 { effects {}; "
            "guarantees is_sorted(xs); }")
    ai_g = "u64 q(const vector<u64>& ys) { return 0; }"
    r = check_module(tu_g, ai_g, "m")
    check(has(r, "E-Guarantee-Uncheckable") and not has(r, "E-Spec-ParamName"),
          "guarantees 普通改名：已由 E-Guarantee-Uncheckable 报，不重复",
          str(codes(r)))

    # ...但 spec 名被实现的**局部变量**顶掉时，guarantee 会静默绑到错的值
    ai_sh = ("u64 q(const vector<u64>& ys) {\n"
             "    u64 xs = 0;\n    return xs;\n}")
    check(has(check_module(tu_g, ai_sh, "m"), "E-Spec-ParamName"),
          "guarantees 名被实现局部变量顶掉 → E-Spec-ParamName")

    # `O(n)` 的魔法绑定（恰好一个容器形参）也算引用那个形参
    tu_n = ("spec p(const vector<u64>& xs) -> u64 { effects {}; "
            "target { time: { max: O(n) }, }; }")
    ai_n = "u64 p(const vector<u64>& ys) { return ys.length(); }"
    r = check_module(tu_n, ai_n, "m")
    check(has(r, "E-Spec-ParamName"), "O(n) 绑到唯一容器 → 改名也报", str(codes(r)))

    # 只有 `.tu`、没有实现 → 不报
    r = check_module(tu, None, "m")
    check(not has(r, "E-Spec-ParamName"),
          "只有 .tu、没有实现 → 不报", str(codes(r)))


def test_repeats_contract():
    """`repeats` 的静态那一半（2026-11-09）：名字可解析 + 声明有消费者。

    修之前：`repeats alpha(n)`（未定义的名字）**check 无诊断**、`cost` 静默给 ⊤；
    `repeats` 写在一个不当 while 条件的函数上永远不生效，也没有提示。
    """
    print("[17] repeats：名字可解析 + 声明有消费者")

    tu_bad = """spec pred(u64 n) -> bool {
    effects {};
    repeats alpha(n);
}
spec a(u64 n) -> u64 { effects {}; }"""
    ai_plain = "u64 a(u64 n) { return n; }"
    r = check_module(tu_bad, ai_plain, "m")
    check(has(r, "E-Repeats-UndefinedName"),
          "repeats 引用未定义的名字 → error（以前静默）", str(codes(r)))

    tu_ok = """const K: u64 = 8;
spec pred(u64 n) -> bool {
    effects {};
    repeats K;
}
spec a(u64 n) -> u64 { effects {}; }"""
    ai_used = ("bool pred(u64 n) { return n > 0; }\n"
               "u64 a(u64 n) { u64 j = n; "
               "while (pred(j)) { j = j - 1; } return j; }")
    r2 = check_module(tu_ok, ai_used, "m")
    check(not has(r2, "E-Repeats-UndefinedName"),
          "repeats 用模块常量 + 形参 → 不报", str(codes(r2)))
    check(not has(r2, "W-Repeats-Unused"),
          "repeats 有消费者（while 条件）→ 不提示", str(codes(r2)))

    # 声明在**不当 while 条件**的函数上 → 提示（这条上界永不生效）。
    ai_unused = ("bool pred(u64 n) { return n > 0; }\n"
                 "u64 a(u64 n) { return n; }")
    r3 = check_module(tu_ok, ai_unused, "m")
    check(has(r3, "W-Repeats-Unused"),
          "repeats 没被任何 while 当条件 → 提示", str(codes(r3)))

    # 只有 `.tu`（没有实现）→ 不提示：可能被别的模块当条件用。
    r4 = check_module(tu_ok, None, "m")
    check(not has(r4, "W-Repeats-Unused"),
          "没有实现文件 → 不提示（跨模块可能被用）", str(codes(r4)))


def main():
    test_sample()
    test_signatures()
    test_ai_no_decl()
    test_choose()
    test_pending()
    test_whole_body()
    test_nesting()
    test_states()
    test_integration()
    test_strict()
    test_no_ai_file()
    test_immutability()
    test_while_bounds()
    test_member_sep()
    test_param_names()
    test_repeats_contract()

    print()
    total = _passed + len(_failed)
    if _failed:
        print(f"失败 {len(_failed)} / {total}")
        for f in _failed:
            print("  - " + f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
