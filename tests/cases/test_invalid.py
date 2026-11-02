#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
test_invalid.py —— M0 验收 ②：100 个非法输入

每个用例要求：
  1. 至少产生 1 条诊断（不许静默通过）
  2. 每条诊断有**精确 span**：line ≥ 1、col ≥ 1、0 ≤ start ≤ end ≤ len(src)
  3. 有非空 code
  4. 诊断是纯函数输出：同一输入跑两次，JSON 逐字节相同
  5. 指定了期望错误码的用例，必须命中该码

运行：
  cd src; $env:PYTHONIOENCODING="utf-8"; python test_invalid.py
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

import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

from checker import check_module
from parser import parse_source

# ---------------------------------------------------------------- 收集

CASES = []          # list[dict]


def case(name, expect=None, strict=False, tu="", ai=None, code=None):
    CASES.append({"name": name, "expect": expect, "strict": strict,
                  "tu": tu, "ai": ai, "code": code})


# 一个合法的骨架，供变异使用
OK_TU = "spec a(x: u64) -> bool { effects {}; }"
OK_AI = "func a(x: u64) -> bool { return true; }"


def bare(spec_body="effects {};", ret="bool"):
    return f"spec a(x: u64) -> {ret} {{ {spec_body} }}"


# ================================================================ A. 词法（20）

case("A01 未闭合的字符字面量", "E-", code="const X: u64 = 'a;")
case("A02 空字符字面量", "E-", code="const X: u64 = '';")
case("A03 非法转义", "E-", code="const X: u64 = '\\q';")
case("A04 数字后跟字母", "E-", code="const X: u64 = 12ab;")
case("A05 缺少操作数", "E-", code="const X: u64 = 1 +;")
case("A06 未知字符 $", "E-", code="const X: u64 = $;")
case("A07 全角括号", "E-", code="spec a（） -> u64 { effects {}; }")
case("A08 全角冒号", "E-", code="const X： u64 = 1;")
case("A09 未闭合的字符串", "E-", code='const X: u64 = "abc;')
case("A10 ⊖ 单独出现", "E-", code="spec a() -> u64 { effects {}; } ⊖")
case("A11 数字后缀写法", "E-", code="const X: u64 = 42u64;")
case("A12 长写法关键字 function", "E-", code="function a() -> u64 { effects {}; }")
case("A13 长写法 public", "E-", code="public func a() -> u64 { return 1; }")
case("A14 两个连续字面量", "E-", code="const X: u64 = 1 2;")
case("A15 反引号字符串", "E-", code="const X: u64 = `abc`;")
case("A16 spec 后直接分号", "E-", code="spec ;")
case("A17 单独的右花括号", "E-", code="}")
case("A18 单独的 end", "E-", code="end")
case("A19 单独的箭头", "E-", code="->")
case("A20 单独的 ?!", "E-", code="?!")

# ================================================================ B. 语法结构（32）

case("B01 缺右花括号", "E-Parse", code="spec a() -> u64 { effects {}; spec")
case("B02 缺左花括号", "E-Parse", code="spec a() -> u64 effects {}; }")
case("B03 缺左圆括号", "E-Parse", code="spec a) -> u64 { effects {}; }")
case("B04 缺右圆括号", "E-Parse", code="spec a( -> u64 { effects {}; }")
case("B05 缺分号（effects 后）", "E-Parse", code="spec a() -> u64 { effects {} }")
case("B06 缺箭头", "E-Parse", code="spec a() u64 { effects {}; }")
case("B07 缺 spec 名", "E-Parse", code="spec () -> u64 { effects {}; }")
case("B08 tar7et 拼错", "E-Parse", code="spec a() -> u64 { effects {}; targat { time: { max: O(1) }, }; }")
case("B09 target 缺分号", "E-Parse", code="spec a() -> u64 { effects {}; target { time: { max: O(1) }, } }")
case("B10 benchmodel 缺 data 段", "E-Bench-MissingData", code="spec a(const Vec<u64>& x) -> u64 { effects {}; benchmodel { scale { x in range(1, 4, *2); } } }")
case("B11 顶层 kernel（v0.66 已下线）", "E-Kernel-KeywordRemoved",
     code="kernel k(x: u64) -> u64;\nspec a() -> u64 { effects {}; }")
case("B12 effects pure 裸写（v0.62 禁止）", "E-Effects-BarePure", code="spec a() -> u64 { effects pure; }")
case("B13 effects 里 pure 混用", "E-Effects-PureMixed", code="spec a() -> u64 { effects {pure, alloc}; }")
case("B14 choose 用 model（已下线）", "E-Clause-OldModel",
     tu=("spec a(const Vec<u64>& x) -> u64 { effects {};\n"
         "    choose L(const Vec<u64>& x) -> u64 { over {k}; model {s}; objective time; }\n"
         "}"),
     ai="func a(const Vec<u64>& x) -> u64 { const u64 n = x.length(); return ?!L(x); }")
case("B15 枚举变体缺逗号", "E-Parse", code="enum E { A B }")
case("B16 结构体字段缺冒号", "E-Parse", code="struct S { x u64, } struct")
case("B17 trait 方法缺分号", "E-Parse", code="trait T { func m(self) -> u64 } trait")
case("B18 use 缺分号", "E-Parse", code="use core.x")
case("B19 const 缺等号", "E-Parse", code="const X: u64 1;")
case("B20 const 缺分号", "E-Parse", code="const X: u64 = 1")
case("B21 func 缺体", "E-Parse", code="func a() -> u64 fn")
case("B22 用旧关键字 fn", "E-Func-KeywordRemoved", code="fn a() -> u64 { return 1; }")
case("B23 用旧关键字 val", "E-Decl-OldKeyword", code="func a() -> u64 { val x = 1; return x; }")
case("B24 用旧关键字 var", "E-Decl-OldKeyword", code="func a() -> u64 { var x = 1; return x; }")
case("B25 用旧关键字 end", "E-End-Removed", code="func a() -> u64 { return 1; } end fn")
case("B26 match 臂缺 =>", "E-Parse", code="func a() -> u64 { match 1 { _ { return 1; }, } }")
case("B27 let 缺等号", "E-Parse", code="func a() -> u64 { val x: u64 1; return x; }")
case("B28 return 缺分号", "E-Parse", code="func a() -> u64 { return 1 }")
case("B29 元组类型缺右括号", "E-Parse", code="spec a() -> (u64, u64 { effects {}; }")
case("B30 切片类型缺右方括号", "E-Parse", code="spec a(x: [] -> u64 { effects {}; }")
case("B31 泛型缺右方括号", "E-Parse", code="spec a(x: V[u64) -> u64 { effects {}; }")
case("B32 旧写法切片 []T", "E-Type-OldSlice", code="spec a(x: []u8) -> u64 { effects {}; }")

# ================================================================ v0.68 专项（12）

case("V01 用旧关键字 fn", "E-Func-KeywordRemoved", code="fn a() -> u64 { return 1; }")
case("V02 用旧关键字 val", "E-Decl-OldKeyword", code="func a() -> u64 { val x = 1; return x; }")
case("V03 用旧关键字 var", "E-Decl-OldKeyword", code="func a() -> u64 { var x = 1; return x; }")
case("V04 用旧写法 end fn", "E-End-Removed", code="func a() -> u64 { return 1; } end fn")
case("V05 旧写法 &mut T", "E-Ref-OldMut", code="spec a(x: &mut Vec<u8>) -> u64 { effects {}; }")
case("V06 旧写法 []T", "E-Type-OldSlice", code="spec a(x: []u8) -> u64 { effects {}; }")
case("V07 旧写法 BoundedVector", "E-Type-OldBounded",
     code="spec a(x: BoundedVector[X, Y]) -> u64 { effects {}; }")
case("V08 条件缺括号（if）", "E-Cond-ParenRequired", tu=OK_TU,
     ai="func a(x: u64) -> bool { if x == 0 { return true; } return false; }")
case("V09 条件缺括号（while）", "E-Cond-ParenRequired", tu=OK_TU,
     ai="func a(x: u64) -> bool { while x > 0 { x = x - 1; } return true; }")
case("V10 T! 里写 return Ok(x)", "E-Return-OkInFallible",
     tu="error E;\nspec a() -> u64! { effects {}; }",
     ai="func a() -> u64! { return Ok(1); }")
case("V11 有 ? 但返回类型无 !", "E-Fallible-Missing",
     tu="error E;\nspec a() -> u64 { effects {}; }\nspec b() -> u64! { effects {}; }",
     ai="func a() -> u64 { return b()?; }\nfunc b() -> u64! { return 1; }")
case("V12 用了 T! 但没 error 声明", "E-Error-DeclMissing",
     tu="spec a() -> u64! { effects {}; }", ai="func a() -> u64! { return 1; }")

# ================================================================ v0.68 嵌套撤回（3）

case("V13 kernels 嵌在候选体里", "E-Kernels-Nested",
     tu="spec a(const Vec<u64>& x) -> u64 { effects {}; "
        "choose L(const Vec<u64>& x) -> u64 { over {k}; objective time; } }",
     ai="""func a(const Vec<u64>& x) -> u64 {
    return ?!L(x);
} kernels L(const Vec<u64>& x) -> u64 {
    k {
        return 1;
    } kernels M(const Vec<u64>& x) -> u64 {
        m { return 1; }
    }
}""")

# ================================================================ T13 循环上界（4）

_T13_TU = """spec pred(u64 lo, u64 hi) -> bool {
    effects {};
    repeats log(hi - lo);
}
spec pred2(u64 lo, u64 hi) -> bool { effects {}; }
spec a(const Vec<u64>& xs) -> u64 { effects {}; }"""

case("V14 条件无尺寸比较 → 宽松 warn", "E-While-NoBound", tu=_T13_TU,
     ai="func a(const Vec<u64>& xs) -> u64 { u64 i = 0; while (i < 10) { i = i + 1; } return i; }")

case("V15 谓词没写 repeats → 宽松 warn", "E-While-NoBound", tu=_T13_TU,
     ai="func a(const Vec<u64>& xs) -> u64 { u64 i = 0; "
        "while (pred2(i, xs.length())) { i = i + 1; } return i; }")

case("V16 枚举成员用 `.`", "E-Member-WrongSep",
     tu="enum Token { Plus }\nspec a(u64 x) -> u64 { effects {}; }",
     ai="func a(u64 x) -> u64 { const Token t = Token.Plus; return 1; }")

case("V17 scale 旧写法 n(xs) =", "E-Bench-OldScale",
     tu="spec f(const Vec<u64>& xs) -> u64 {\n  effects {};\n  benchmodel {\n"
        "    data { xs = sorted_u64s(); }\n    scale { n(xs) = range(1, 64, *2); }\n"
        "    seed = 1;\n  }\n}\n",
     ai="func f(const Vec<u64>& xs) -> u64 { return 0; }")

# ================================================================ C. 语义检查（38）

case("C01 实现无 spec", "E-Fn-NoSpec", tu="", ai="func b() -> u64 { return 1; }")
case("C02 参数个数不同", "E-Spec-SigMismatch", tu=OK_TU,
     ai="func a(x: u64, y: u64) -> bool { return true; }")
case("C03 参数类型不同", "E-Spec-SigMismatch", tu=OK_TU,
     ai="func a(x: i64) -> bool { return true; }")
case("C04 返回类型不同", "E-Spec-SigMismatch", tu=OK_TU,
     ai="func a(x: u64) -> u64 { return 1; }")
case("C05 .ai 有 const", "E-Ai-HasDecl", tu=OK_TU, ai=OK_AI + "\nconst X: u64 = 1;")
case("C06 .ai 有 use", "E-Ai-HasUse", tu=OK_TU, ai="use core.x;\n" + OK_AI)
case("C07 .ai 有 enum", "E-Ai-HasDecl", tu=OK_TU, ai="enum E { A }\n" + OK_AI)
case("C08 .ai 有 spec", "E-Ai-HasDecl", tu=OK_TU, ai="spec z() -> u64 { effects {}; }\n" + OK_AI)
case("C09 .ai 里写 choose", "E-Choose-NotInSpec", tu=OK_TU,
     ai=OK_AI + "\nchoose L(x: u64) -> u64 { over {k}; objective time; }")
case("C10 顶层 kernel 声明（已下线）", "E-Kernel-KeywordRemoved", tu=OK_TU,
     ai=OK_AI + "\nkernel k(x: u64) -> u64 { return 1; } kernel")

CHOOSE_TU = """spec a(const Vec<u64>& x) -> u64 {
    effects {};
    choose L(const Vec<u64>& x) -> u64 {
        over {%s};
        objective time;
    };
}"""
CHOOSE_AI = """func a(const Vec<u64>& x) -> u64 {
    const u64 n = x.length();
    if (n == 0) { return 0; }
    return ?!L(x);
} kernels L(const Vec<u64>& x) -> u64 {
    k { return 1; }
}"""

CHOOSE_AI_TWO = CHOOSE_AI.replace("    k { return 1; }",
                                  "    k { return 1; }\n    j { return 2; }")

case("C11 over 为空", "E-Choose-Empty", tu=CHOOSE_TU % "", ai=CHOOSE_AI)
case("C12 over 超 4 个", "E-Choose-TooManyCandidates",
     tu=CHOOSE_TU % "k1, k2, k3, k4, k5", ai=CHOOSE_AI)
case("C13 实现了 over 名单外的候选", "E-Choose-Undeclared",
     tu=CHOOSE_TU % "k", ai=CHOOSE_AI.replace("    k { return 1; }",
                                              "    k { return 1; }\n    nope { return 0; }"))
case("C14 缺体且没标 pending", "E-Choose-PendingMismatch",
     tu=CHOOSE_TU % "k, j", ai=CHOOSE_AI)
case("C15 标了 pending 却已实现", "E-Choose-PendingMismatch",
     tu=CHOOSE_TU % "k pending", ai=CHOOSE_AI)
case("C16 旧的 pending 子句", "E-Choose-PendingClause",
     tu=(CHOOSE_TU % "k").replace("objective time", "pending {k};\n        objective time"),
     ai=CHOOSE_AI)
case("C17 ?! 无对应 choose", "E-Choose-Undeclared", tu=OK_TU,
     ai="func a(x: u64) -> bool { val n = x; return ?!Nope(x); }")
case("C18 ?! 跑到别的函数里", "E-Choose-WrongHost",
     tu=CHOOSE_TU % "k" + """
spec other(x: []u64) -> u64 { effects {}; } end spec""",
     ai=CHOOSE_AI + """
fn other(x: []u64) -> u64 {
    val n = x.length();
    return ?!L(n);
} end fn""")
case("C19 实参个数不符", "E-Choose-ArityMismatch",
     tu=CHOOSE_TU % "k",
     ai=CHOOSE_AI.replace("?!L(x)", "?!L(x, x)"))
case("C20 ?! 构成整个函数体", "E-Choose-WholeBody",
     tu=CHOOSE_TU % "k",
     ai="""fn a(x: []u64) -> u64 {
    return ?!L(x);
} kernels L(x: []u64) -> u64 {
    k { return 1; }
} end fn""")
case("C21 kernels 嵌套（v0.68 已撤回）", "E-Kernels-Nested",
     tu=CHOOSE_TU % "k",
     ai="""func a(const Vec<u64>& x) -> u64 {
    const u64 n = x.length();
    if (n == 0) { return 0; }
    return ?!L(x);
} kernels L(const Vec<u64>& x) -> u64 {
    k {
        return ?!M(x);
    } kernels M(const Vec<u64>& x) -> u64 {
        m1 { return 1; }
    }
}""")

# 严格模式
case("C22 条件缺括号", "E-Cond-ParenRequired", strict=True, tu=OK_TU,
     ai="func a(x: u64) -> bool { if x == 0 { return true; } return false; }")
case("C23 L4 元组 4 元", "L4-TupleTooLong", strict=True,
     tu="spec a() -> (u64, u64, u64, u64) { effects {}; }", ai="")
case("C24 L5 行宽超限", "L5-LineTooLong", strict=True,
     tu="// " + "x" * 120 + "\n" + OK_TU, ai="")
case("C25 L4 表达式元组", "L4-TupleTooLong", strict=True, tu=OK_TU,
     ai="func a(x: u64) -> bool { val t = (1, 2, 3, 4); return true; }")

# 其余语义/结构
case("C26 spec 重复定义", "E-", code=OK_TU + "\n" + OK_TU)
case("C27 func 重复实现", "E-", tu=OK_TU, ai=OK_AI + "\n" + OK_AI)
case("C28 choose 重复", "E-Duplicate-Choose", tu=(CHOOSE_TU % "k").replace("        objective time;\n    }", "        objective time;\n    }\n    choose L(const Vec<u64>& x) -> u64 { over {k}; objective time; }", 1), ai=CHOOSE_AI)
case("C29 未知子句名", "E-", code="spec a() -> u64 { effects {}; unknownclause 1; }")
case("C30 target 槽位写错", "E-", code="spec a() -> u64 { effects {}; target { time: { minimum: O(1) }, }; }")
case("C31 target 缺冒号", "E-Parse", code="spec a() -> u64 { effects {}; target { time { max: O(1) }, }; }")
case("C32 benchmodel 缺 scale 段", "E-Bench-MissingScale", code="spec a(const Vec<u64>& x) -> u64 { effects {}; benchmodel { data { x = gen(); } } }")
case("C33 requires 缺表达式", "E-Clause-EmptyExpr", code="spec a() -> u64 { effects {}; requires; }")
case("C34 objective 未知", "E-",
     tu=(CHOOSE_TU % "k").replace("objective time", "objective maximize_fun"), ai=CHOOSE_AI)
case("C35 ?! 缺括号", "E-Choose-NoLabel", tu=OK_TU,
     ai="func a(x: u64) -> bool { val n = x; return ?!; }")
case("C36 ?! 后无名字", "E-Choose-NoLabel", tu=OK_TU,
     ai="func a(x: u64) -> bool { val n = x; return ?!(x); }")
case("C37 赋值给字面量", "E-", tu=OK_TU, ai="func a(x: u64) -> bool { 1 = 2; return true; }")
case("C38 双重 end", "E-Parse", code="spec a() -> u64 { effects {}; } spec")

# ================================================================ D. 边界（10）

case("D01 注释后跟垃圾", "E-", code="// only\n@@@")
case("D02 空白后跟半个关键字", "E-", code="   \n\tretur 1;\n")
case("D03 只有右方括号", "E-", code="]")
case("D04 注释未闭合", "E-", code="/* 未闭合")
case("D05 嵌套 block 注释", "E-", code="/* /* */ */")
case("D06 use 后缺路径", "E-", code="use ;")
case("D07 const 缺名字", "E-", code="const : u64 = 1;")
case("D08 只剩下 spec 头", "E-", code="spec a()")
case("D09 只剩 func 头", "E-", code="func a()")
case("D10 非法 utf8 转义", "E-", code="const X: u64 = '\\u{110000}';")

CASES.append({"name": "SQL 注入式引号", "expect": "E-", "strict": False,
              "tu": "", "ai": None, "code": "'; DROP TABLE --"})
CASES.append({"name": "超长单行", "expect": "L5-LineTooLong", "strict": True,
              "tu": "const X: u64 = 1;" + " " * 300, "ai": None, "code": None})


# ---------------------------------------------------------------- 补足到 100

def _top_up():
    """用 种类错配与缺分号两种变异补足到 100 条。"""
    kinds = ["fn", "spec", "kernel", "choose", "struct", "enum", "trait", "impl",
             "if", "while", "for", "match"]
    i = 0
    while len(CASES) < 100:
        k = kinds[i % len(kinds)]
        CASES.append({
            "name": f"E{len(CASES)+1:02d} {k} 用在 spec 上",
            "expect": "E-Parse-EndKindMismatch", "strict": False,
            "tu": f"spec a() -> u64 {{ effects {{}}; }} {k}",
            "ai": None, "code": None,
        })
        i += 1


_top_up()


# ---------------------------------------------------------------- 运行

_passed = 0
_failed = []
_codes_seen = set()


def _check_span(src, d):
    ok = True
    if not d.code:
        ok = False
    if d.line < 1 or d.col < 1:
        ok = False
    if not (0 <= d.start <= d.end <= len(src)):
        ok = False
    return ok


def run():
    global _passed
    for c in CASES:
        tu = c["tu"] or (c.get("code") or "")
        ai = c["ai"]
        strict = c["strict"]
        res = check_module(tu, ai, "case", strict=strict)
        srcs = [tu] + ([ai] if ai else [])
        total_len = max(len(s) for s in srcs) if srcs else 0

        diags = res.diagnostics
        if not diags:
            _failed.append(f"{c['name']}: 未产生任何诊断")
            continue

        bad = [d for d in diags if not _check_span(tu if not ai else max(srcs, key=len), d)]
        if bad:
            _failed.append(f"{c['name']}: span 不合法 {bad[0].code} "
                           f"@{getattr(bad[0],'line',0)}:{getattr(bad[0],'col',0)}")
            continue

        if c["expect"]:
            got = [d.code for d in diags]
            exp = c["expect"]
            hit = any(g == exp for g in got) or any(g.startswith(exp) for g in got)
            if not hit:
                _failed.append(f"{c['name']}: 期望 {c['expect']}，实际 {got[:3]}")
                continue

        # 确定性：同一输入两次 JSON 逐字节相同
        r2 = check_module(tu, ai, "case", strict=strict)
        j1 = json.dumps(res.to_dict(), ensure_ascii=False, sort_keys=True)
        j2 = json.dumps(r2.to_dict(), ensure_ascii=False, sort_keys=True)
        if j1 != j2:
            _failed.append(f"{c['name']}: 两次诊断不同（非纯函数）")
            continue

        _codes_seen.update(d.code for d in diags)
        _passed += 1


def main():
    print(f"用例数：{len(CASES)}")
    run()

    print()
    print(f"通过 {_passed} / {len(CASES)}")
    if _failed:
        print(f"失败 {len(_failed)}：")
        for f in _failed[:12]:
            print("  - " + f)
        return 1

    print(f"覆盖诊断码 {len(_codes_seen)} 种：")
    for code in sorted(_codes_seen):
        print("  " + code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
