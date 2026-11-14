#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
test_parser.py —— M0 解析器测试

覆盖：
  · 两份样例完整解析（M0 验收 ⑦）
  · 关键构造的形状（target / model / effects / repeats / choose / kernel）
  · 字符字面量、元组类型、&mut、命名实参、match 臂、`?`、`as`
  · 错误恢复与终止符不匹配

运行：
  cd src; $env:PYTHONIOENCODING="utf-8"; python test_parser.py
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

from lexer import Kind
from parser import Node, parse_source

SAMPLE = os.path.join(HERE, "..", "文档", "样例")


# ---------------------------------------------------------------- 工具

_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def items(mod, kind):
    return [i for i in mod.children if i.kind == kind]


def spec_by_name(mod, name):
    for it in items(mod, "spec"):
        if it.name == name:
            return it
    return None


def clauses(spec):
    """取出 spec 下的所有子句（在 spec-body 里）。"""
    out = []
    for c in spec.children:
        if c.kind == "spec-body":
            out.extend(x for x in c.children if x.kind == "clause")
        elif c.kind == "clause":
            out.append(c)
    return out


def clause(spec, name):
    for c in clauses(spec):
        if c.name == name:
            return c
    return None


def models(spec):
    out = []
    for c in spec.children:
        if c.kind == "spec-body":
            out.extend(x for x in c.children if x.kind == "benchmodel")
    return out


def walk(node, kind, acc=None):
    if acc is None:
        acc = []
    if node.kind == kind:
        acc.append(node)
    for c in node.children:
        walk(c, kind, acc)
    return acc


def load(name):
    with open(os.path.join(SAMPLE, name), "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- 1. 样例

def test_samples():
    print("[1] 样例完整解析（M0 ⑦）")

    tu = parse_source(load("calculator.tu"), "calculator.tu")
    check(tu.ok, "calculator.tu 无诊断",
          f"实际 {len(tu.diagnostics)} 条: "
          + "; ".join(f"{d.code}@{d.line}:{d.col}" for d in tu.diagnostics[:3]))
    check(len(items(tu.module, "use")) == 1, "tu: 1 个 use")
    check(len(items(tu.module, "const")) == 12, "tu: 12 个 const",
          f"实际 {len(items(tu.module, 'const'))}")
    check(len(items(tu.module, "enum")) == 2, "tu: 2 个 enum")
    check(len(items(tu.module, "spec")) == 11,
          "tu: 11 个 spec（第 4 步加了 more_digits）")

    ai = parse_source(load("calculator.eln"), "calculator.eln")
    check(ai.ok, "calculator.eln 无诊断",
          f"实际 {len(ai.diagnostics)} 条: "
          + "; ".join(f"{d.code}@{d.line}:{d.col}" for d in ai.diagnostics[:3]))
    check(len(items(ai.module, "fn")) == 11,
          "ai: 11 个 fn（第 4 步加了 more_digits）")
    check(len(items(ai.module, "const")) == 0, "ai: 零声明（F-4）")
    check(len(items(ai.module, "use")) == 0, "ai: 零 use（F-8）")


# ---------------------------------------------------------------- 2. spec 子句

def test_benchmodel():
    print("[?] benchmodel 三段式（v0.69）")
    src = """
spec f(const vector<u64>& xs, u64 key) -> u64 {
    effects {};
    benchmodel {
        data {
            xs  = sorted_u64s();
            key = any_u64();
        }
        scale {
            xs in range(1, 1024, *2);
        }
        seed = 1;
    };
}
"""
    r = parse_source(src, "b.tu")
    check(not r.diagnostics, "benchmodel 能解析",
          "; ".join(d.code for d in r.diagnostics))
    sp = spec_by_name(r.module, "f")
    bms = models(sp)
    check(len(bms) == 1, "一个 spec 一个 benchmodel", f"实际 {len(bms)}")
    bm = bms[0]
    data = next((c for c in bm.children if c.kind == "bench-data"), None)
    scale = next((c for c in bm.children if c.kind == "bench-scale"), None)
    seed = next((c for c in bm.children if c.kind == "seed"), None)
    check([b.name for b in data.children] == ["xs", "key"], "data 两个绑定",
          str([b.name for b in data.children]))
    check([b.name for b in scale.children] == ["xs"], "scale 绑定 n(xs)",
          str([b.name for b in scale.children]))
    check(seed is not None, "seed 解析到")

    rng = scale.children[0].children[0]
    args = next(c for c in rng.children if c.kind == "args")
    check(len(args.children) == 3, "range 有三个参数（含步长）",
          str(len(args.children)))
    check(args.children[2].kind == "step" and args.children[2].value == "*",
          "第三个参数是步长 `*`")

    # 旧写法必须报错
    r2 = parse_source("spec g(const vector<u64>& xs) -> u64 { effects {}; "
                      "model small { xs: a(); } ; }", "o.tu")
    check(any(d.code == "E-Clause-OldModel" for d in r2.diagnostics),
          "旧 `model` 报 E-Clause-OldModel",
          str([d.code for d in r2.diagnostics]))


def test_spec_clauses():
    print("[2] spec 子句形状")
    tu = parse_source(load("calculator.tu"), "tu")
    ev = spec_by_name(tu.module, "evaluate")

    check(ev is not None, "找到 spec evaluate")
    eff = clause(ev, "effects")
    check(eff is not None and eff.value == ["alloc"], "evaluate.effects = {alloc}",
          f"实际 {getattr(eff, 'value', None)}")

    req = clause(ev, "requires")
    check(req is not None and len(req.children) == 1, "evaluate 有 requires")
    check(req.children[0].kind == "binary"
          and req.children[0].value == "<=", "requires 是 <= 比较")

    tgt = clause(ev, "target")
    check(tgt is not None, "evaluate 有 target")
    dims = [d.name for d in tgt.children]
    check(dims == ["time", "allocation"], "target 两个维度 time/allocation",
          f"实际 {dims}")

    time_dim = tgt.children[0]
    slots = [s.name for s in time_dim.children]
    check(slots == ["max"], "time 维度只有 max 槽位（target 只有上限）",
          f"实际 {slots}")

    # v0.69：model → benchmodel。evaluate 没有 `?!`，所以不该有 benchmodel
    check(models(ev) == [], "evaluate 没有 ?! → 没有 benchmodel",
          f"实际 {len(models(ev))}")

    sd = spec_by_name(tu.module, "is_digit")
    e2 = clause(sd, "effects")
    check(e2 is not None and e2.value == "pure", "is_digit.effects = pure",
          f"实际 {getattr(e2, 'value', None)}")

    sr = spec_by_name(tu.module, "should_reduce")
    rep = clause(sr, "repeats")
    check(rep is not None, "should_reduce 有 repeats（T13 情形二）")


# ---------------------------------------------------------------- 3. 类型

def test_types():
    print("[3] 类型形状")
    tu = parse_source(load("calculator.tu"), "tu")

    ev = spec_by_name(tu.module, "evaluate")
    # ev.children: params, type-name(i64), spec-body；`T!` 由节点 value 标记
    tys = [c for c in ev.children if c.kind.startswith("type")]
    check(tys and tys[0].name == "i64", "evaluate 返回 i64（i64!）")
    check((ev.value or {}).get("fallible") is True, "evaluate 标了可能失败")

    p = spec_by_name(tu.module, "pop_until_open")
    params = [c for c in p.children if c.kind == "params"][0]
    refs = walk(params, "type-ref")
    check(len(refs) == 2, "pop_until_open 两个引用参数", f"实际 {len(refs)}")
    generic_args = [c for c in walk(params, "type-args")]
    check(len(generic_args) >= 2, "两个参数都是泛型容器")

    rn = spec_by_name(tu.module, "read_number")
    tuples = walk(rn, "type-tuple")
    check(len(tuples) == 1, "read_number 返回元组类型")
    check([t.name for t in tuples[0].children] == ["i64", "u64"], "元组元素 i64, u64")

    tk = spec_by_name(tu.module, "tokenize")
    names = [c.name for c in walk(tk, "type-name")]
    check("vector" in names, "tokenize 参数是 vector<u8>", f"实际 {names[:4]}")


# ---------------------------------------------------------------- 4. enum

def test_enum():
    print("[4] enum 与载荷")
    tu = parse_source(load("calculator.tu"), "tu")
    enums = {e.name: e for e in items(tu.module, "enum")}
    check(set(enums) == {"Token", "CalcError"}, "两个 enum: Token / CalcError")

    tok = enums["Token"]
    names = [v.name for v in tok.children]
    check(names[:2] == ["Number", "Plus"], "Token 变体顺序",
          f"实际 {names[:2]}")

    arith = [v for v in enums["CalcError"].children if v.name == "Arith"][0]
    fields = [f.name for f in arith.children if f.kind == "field"]
    check(fields == ["cause"], "CalcError.Arith 有具名载荷 cause")
    ftype = [c for c in arith.children if c.kind == "field"][0].children[0]
    check(ftype.name == "Arithmetic", "载荷类型是 Arithmetic")


# ---------------------------------------------------------------- 5. 语句

def test_statements():
    print("[5] 语句形状")
    ai = parse_source(load("calculator.eln"), "ai")

    def fn(name):
        for it in items(ai.module, "fn"):
            if it.name == name:
                return it
        return None

    ev = fn("evaluate")
    check(ev.value["pub"] is True, "evaluate 是 pub")
    body = [c for c in ev.children if c.kind == "block"][0]
    kinds = [s.kind for s in body.children]
    check(kinds == ["const-decl", "return"], "evaluate 体是 const + return", f"实际 {kinds}")
    check(walk(body, "try"), "evaluate 用了 ?")

    tk = fn("tokenize")
    tbody = [c for c in tk.children if c.kind == "block"][0]
    check(any(s.kind == "while" for s in tbody.children), "tokenize 有 while")
    wh = [s for s in tbody.children if s.kind == "while"][0]
    check(walk(wh, "if"), "while 体内有 if 链")
    check(len(walk(wh, "method-call")) > 0, "有方法调用")

    rd = fn("reduce")
    rbody = [c for c in rd.children if c.kind == "block"][0]
    mts = walk(rbody, "match")
    check(len(mts) == 1, "reduce 体内有一个 match", f"实际 {len(mts)}")
    mt = mts[0]
    check(len(mt.children) == 5, "reduce 的 match 有 4 臂 + 判别式",
          f"实际 {len(mt.children)}")
    arms = [c for c in mt.children if c.kind == "arm"]
    check(len(arms) == 4, "4 个臂")
    check(any(walk(a, "pattern-wildcard") for a in arms), "有 _ 通配臂")

    at = fn("apply_top")
    abody = [c for c in at.children if c.kind == "block"][0]
    named = walk(abody, "named-arg")
    check(len(named) >= 1, "apply_top 有命名实参（cause = …）", f"实际 {len(named)}")
    # 标注一下：v0.68 起具名实参保持，但位置不固定，这里只要求至少一处

    dv = fn("digit_value")
    dbody = [c for c in dv.children if c.kind == "block"][0]
    check(walk(dbody, "cast"), "digit_value 用了 as 转换")

    sr = fn("should_reduce")
    sbody = [c for c in sr.children if c.kind == "block"][0]
    check(sbody.children[0].kind == "if", "should_reduce 以 if 开头")


# ---------------------------------------------------------------- 6. 字符字面量

def test_char_literals():
    print("[6] 字符字面量（L18）")
    tu = parse_source(load("calculator.tu"), "tu")
    consts = {c.name: c for c in items(tu.module, "const")}
    sp = consts["SPACE"]
    lit = walk(sp, "lit-char")[0]
    check(lit.value == "' '", "SPACE 的值是字符字面量 ' '", f"实际 {lit.value!r}")
    check(walk(consts["OPEN_PAREN"], "lit-char"), "OPEN_PAREN 是字符字面量")


# ---------------------------------------------------------------- 6.4 整数字面量

def test_int_literals():
    print("[6.4] 整数字面量：AST 里存**解析后的数值**，不是源码文本")

    def lit_of(expr):
        r = parse_source("u64 f() {\n    return %s;\n}" % expr, "lit.eln")
        lits = walk(r.module, "lit-int")
        return (lits[0].value if lits else None), r

    v, r = lit_of("0x10")
    check(v == 16, "0x10 → 16（不是文本 \"0x10\"）", repr(v))
    v, _ = lit_of("0X1F")
    check(v == 31, "0X1F → 31", repr(v))
    v, _ = lit_of("0b1010")
    check(v == 10, "0b1010 → 10", repr(v))
    v, _ = lit_of("1_000_000")
    check(v == 1000000, "1_000_000 → 1000000", repr(v))
    v, _ = lit_of("0xFFFF_FFFF_FFFF_FFFF")
    check(v == 18446744073709551615,
          "0xFFFF_FFFF_FFFF_FFFF → u64 最大值（保持无符号值，不截成 i64）", repr(v))
    v, _ = lit_of("42")
    check(v == 42 and isinstance(v, int), "42 仍是 42（十进制不回归）", repr(v))

    # **超界是词法诊断**：check 期就该拦住，不能等到 `run` 才崩。
    r = parse_source("u64 f() {\n    return 0x1_0000_0000_0000_0000;\n}",
                     "lit.eln")
    check(any(d.code == "E-Lex-IntOverflow" for d in r.diagnostics),
          "0x 溢出 u64 → E-Lex-IntOverflow（check 期拦住）",
          "; ".join(d.code for d in r.diagnostics))


# ---------------------------------------------------------------- 6.5 `::`

def test_pathsep():
    print("[6.5] 类型命名空间 `::`（v0.69）")
    src = """enum Token { Number(i64), Plus }
enum Err { Bad(u64) }
i64 f(Token t) {
    const Token a = Token::Plus;
    match t {
        Token::Number(v) => { return v; }
        Token::Plus => { return 1; }
    }
    return 0;
}"""
    r = parse_source(src, "p.eln")
    check(not r.diagnostics, "`::` 能解析",
          "; ".join(d.code for d in r.diagnostics))

    pats = walk(r.module, "pattern")
    names = [p.name for p in pats]
    check("Token.Number" in names, "模式 `Token::Number(v)` 记成 Token.Number",
          str(names))

    # `::` 不能产出 method-call（那才是它存在的理由）
    mcs = walk(r.module, "method-call")
    check(mcs == [], "枚举成员不再解析成 method-call",
          str([m.name for m in mcs]))

    # `const Token a = Token::Plus;` 的右侧是 path（带点）
    paths = [p.name for p in walk(r.module, "path") if p.name and "." in (p.name or "")]
    check("Token.Plus" in paths, "`Token::Plus` 解析成 path Token.Plus", str(paths))

    # 静态调用仍走 field
    src2 = "vector<u64> g() {\n    return array<u64, 8>::make();\n}"
    r2 = parse_source(src2, "s.eln")
    check(not r2.diagnostics, "`array<T,N>::make()` 仍能解析",
          "; ".join(d.code for d in r2.diagnostics))


# ---------------------------------------------------------------- 6.6 scale 的 in

def test_scale_in():
    print("[6.6] `scale` 用 `in`（v0.69）")
    ok = """spec f(const vector<u64>& xs) -> u64 {
  effects {};
  benchmodel {
    data { xs = sorted_u64s(); }
    scale { xs in range(1, 1024, *2); }
    seed = 1;
  };
}"""
    r = parse_source(ok, "s.tu")
    check(not r.diagnostics, "`xs in range(...)` 能解析",
          "; ".join(d.code for d in r.diagnostics))
    sb = walk(r.module, "scale-binding")
    check(sb and sb[0].name == "xs", "scale-binding 记下参数名 xs", str([x.name for x in sb]))

    old = ok.replace("xs in range(1, 1024, *2);", "n(xs) = range(1, 1024, *2);")
    r2 = parse_source(old, "s.tu")
    check(any(d.code == "E-Bench-OldScale" for d in r2.diagnostics),
          "旧写法 `n(xs) = range(...)` 报 E-Bench-OldScale",
          "; ".join(d.code for d in r2.diagnostics))


# ---------------------------------------------------------------- 7. choose / kernel / ?!

def test_choose_kernel_dispatch():
    print("[7] choose / kernels / ?!（B-16、F-9）")
    src = """
error CalcError;

spec average(const vector<u8>& scores) -> u64! {
    effects   {alloc};
    benchmodel {
        data  { scores = make_u8s(); }
        scale { scores in range(1, 1024, *2); }
    };
    target {
        time      : { max: O(scores.length()) },
        allocation: { max: 0 },
    };
    choose sum_kernel(const vector<u8>& scores) -> u64 {
        over     {sum_by_index, sum_by_iter pending};
        objective time;
    };
}
"""
    r = parse_source(src, "t.tu")
    check(r.ok, "tu 片段无诊断",
          "; ".join(f"{d.code}@{d.line}:{d.message}" for d in r.diagnostics[:2]))

    spec = items(r.module, "spec")[0]
    body = items(spec, "spec-body")[0]
    chs = items(body, "choose")
    check(len(chs) == 1, "choose 挂在 spec 里")
    ch = chs[0]
    check(ch.name == "sum_kernel", "choose 标签", ch.name)
    names = {c.name: c.value for c in ch.children}
    over = names.get("over") or {}
    check(over.get("names") == ["sum_by_index", "sum_by_iter"], "over 名单",
          f"实际 {over.get('names')}")
    check(over.get("pending") == ["sum_by_iter"], "over 里的 pending 标记",
          f"实际 {over.get('pending')}")
    check("usemodel" not in names, "usemodel 已删除（v0.69）")
    check(names.get("objective") == "time", "objective")

    ai_src = """
pub u64! average(const vector<u8>& scores) {
    const u64 n = scores.length();
    if (n == 0) { return Err(CalcError.Empty); }
    const u64 total = ?!sum_kernel(scores);
    return total / n;
} kernels sum_kernel(const vector<u8>& scores) -> u64 {
    sum_by_index {
        u64 s = 0; u64 i = 0;
        while (i < n) { s = s + scores.at(i); i = i + 1; }
        return s;
    }
    sum_by_iter {
        return 0;
    }
}
"""
    r2 = parse_source(ai_src, "t.eln")
    check(r2.ok, "ai 片段无诊断",
          "; ".join(f"{d.code}@{d.line}:{d.message}" for d in r2.diagnostics[:2]))

    disp = walk(r2.module, "dispatch")
    check(len(disp) == 1, "一个 ?! 决策点")
    check(disp[0].name == "sum_kernel", "标签名 sum_kernel")

    fn = items(r2.module, "fn")[0]
    ks = items(fn, "kernels")
    check(len(ks) == 1, "func 上一个 kernels 块")
    cands = items(ks[0], "candidate")
    check([c.name for c in cands] == ["sum_by_index", "sum_by_iter"], "两个候选",
          str([c.name for c in cands]))


# ---------------------------------------------------------------- 8. 错误与恢复

def test_errors():
    print("[8] 错误诊断与恢复")

    r = parse_source("spec a() -> u64 { effects {}; }", "x")
    check(r.ok, "最简 spec 可解析",
          "; ".join(f"{d.code}@{d.line}" for d in r.diagnostics))

    # v0.68：`end` 已下线，任何 `end X` 都报 E-End-Removed
    r = parse_source("spec a() -> u64 { effects {}; } end func", "x")
    check(not r.ok, "`end func` 应报错")
    check(any(d.code == "E-End-Removed" for d in r.diagnostics),
          "报 E-End-Removed",
          f"实际 {[d.code for d in r.diagnostics]}")

    r = parse_source("spec a() -> u64 { effects {};", "x")
    check(not r.ok, "缺 } 应报错")
    check(any(d.code.startswith("E-Parse") for d in r.diagnostics), "给出 E-Parse-* 诊断")

    r = parse_source("} } garbage", "x")
    check(not r.ok, "顶层垃圾应报错")
    check(len(r.diagnostics) >= 1, "至少一条诊断")

    # 恢复：坏一个 item 之后仍能解析后面的
    r = parse_source("enum E { A, B }\nspec ok() -> u64 { effects {}; }\n", "x")
    check(r.ok, "前面好后面也好时无诊断",
          "; ".join(f"{d.code}@{d.line}" for d in r.diagnostics))
    check(len(items(r.module, "spec")) == 1, "恢复后仍拿到 spec")

    # 不允许死循环：大量垃圾必须终止
    r = parse_source("§§§ " * 200, "x")
    check(not r.ok, "垃圾输入报错")
    check(len(r.diagnostics) > 0, "有诊断且未挂死")

    r = parse_source("", "x")
    check(r.ok, "空文件合法")
    check(r.module is not None and len(r.module.children) == 0, "空模块零 item")


# ---------------------------------------------------------------- 9. 确定性

def test_determinism():
    print("[9] 确定性（同输入同输出）")
    src = load("calculator.eln")
    a = parse_source(src, "ai")
    b = parse_source(src, "ai")
    check(a.ok and b.ok, "两次都成功")
    check(a.module.dump() == b.module.dump(), "AST 转储逐字节相同")
    da = [d.to_dict() for d in a.diagnostics]
    db = [d.to_dict() for d in b.diagnostics]
    check(da == db, "诊断相同")


# ---------------------------------------------------------------- 9. 位运算优先级
#
# 规格（2026-11-09）：`| < ^ < & < == != < 关系 < .. < << >> < + - < * / % < |>`
# 这张表必须能从 AST 直接读出来，不靠求值结果猜。

def _expr_shape(expr):
    """把 `return <expr>;` 的 AST 压成带括号的字符串。"""
    r = parse_source(
        "u64 f(u64 a, u64 b, u64 c) { return %s; }" % expr, "t.eln")
    if not r.ok:
        return "ERR:" + ",".join(d.code for d in r.diagnostics)

    def fmt(n):
        if n.kind == "binary":
            return "(%s %s %s)" % (fmt(n.children[0]), n.value, fmt(n.children[1]))
        if n.kind == "unary":
            return "(%s%s)" % (n.value, fmt(n.children[0]))
        if n.kind == "paren":
            return fmt(n.children[0])
        if n.kind == "path":
            return n.name or "?"
        if n.kind == "lit-int":
            return str(n.value)
        return n.kind

    fn = items(r.module, "fn")[0]
    block = next(c for c in fn.children if c.kind == "block")
    ret = next(s for s in block.children if s.kind == "return")
    return fmt(ret.children[0])


def test_bitwise_precedence():
    print("[10] 位运算：C 层次（| < ^ < & < == < 关系 < << >> < + - < * /）")

    cases = [
        ("a | b ^ c & d", "(a | (b ^ (c & d)))"),
        ("a & b | c",     "((a & b) | c)"),
        ("a & b == c",    "(a & (b == c))"),      # C 的经典坑：& 比 == 松
        ("a == b < c",    "(a == (b < c))"),      # == 比关系运算松
        ("a + b << c",    "((a + b) << c)"),      # 加减比移位紧
        ("a << b + c",    "(a << (b + c))"),
        ("a << b >> c",   "((a << b) >> c)"),     # 同级左结合
        ("~a + b",        "((~a) + b)"),          # 一元 ~ 最紧
        ("a >> b",        "(a >> b)"),
        ("a & b & c",     "((a & b) & c)"),
    ]
    for src, want in cases:
        got = _expr_shape(src)
        check(got == want, f"{src} → {want}", got)

    # 相邻的两个 `>` 才是右移；`> >` 仍是两个比较号，第二个 `>` 无操作数
    check(_expr_shape("a > > b").startswith("ERR:"), "`a > > b` 不是右移")
    check(_expr_shape("a >> b") == "(a >> b)", "`a >> b` 是右移")

    # 嵌套泛型 `Vec<Vec<u8>>` 不能被 `>>` 词法撕开
    r = parse_source(
        "vector<vector<u64>> g(vector<vector<u64>> xs) { return xs; }",
        "t.eln")
    check(r.ok, "嵌套泛型 `>>` 仍按两个 `>` 解析",
          "; ".join(f"{d.code}@{d.line}" for d in r.diagnostics))

    # **逻辑运算符只有符号写法**（2026-11-11 用户决定：同一个概念不给两套写法）。
    # 词形 `and` / `or` / `not` 已退役 → **必须**报 `E-Logic-WordOp`，且消息
    # 教符号替代（只说"不支持"不算）。这里把旧的正例翻转成负例。
    for expr, sym in (("a and b", "&&"), ("a or b", "||"), ("not a", "!")):
        r = parse_source(
            "bool f(bool a, bool b) { return %s; }" % expr, "t.eln")
        codes = [d.code for d in r.diagnostics]
        msg = "; ".join(d.message for d in r.diagnostics)
        check("E-Logic-WordOp" in codes, f"`{expr}` 报 E-Logic-WordOp", codes)
        check(sym in msg and "改法" in msg,
              f"`{expr}` 的消息教替代写法 `{sym}`", msg[:200])
        # 一条诊断就够：不许在 parser 再级联一条「期望表达式」。
        check(len(codes) == 1, f"`{expr}` 只有一条诊断（不级联）", codes)

    # 符号写法本身照常解析，AST 的 `binary.value` 就是源码记号（`&&` / `||`）。
    check(_expr_shape("a && b") == "(a && b)", "a && b", _expr_shape("a && b"))
    check(_expr_shape("a || b") == "(a || b)", "a || b", _expr_shape("a || b"))
    check(_expr_shape("!a") == "(!a)", "!a", _expr_shape("!a"))

    # 优先级：`&&` 比 `||` 紧（同 C）
    check(_expr_shape("a && b || c") == "((a && b) || c)",
          "`a && b || c` → `(a && b) || c`", _expr_shape("a && b || c"))
    check(_expr_shape("a || b && c") == "(a || (b && c))",
          "`a || b && c` → `a || (b && c)`", _expr_shape("a || b && c"))


def test_pow():
    print("[11] `**`：独立 token、右结合、比 `* / %` 紧")

    # **2026-11-10 之前这里不是幂**：`**` 是两个 `*`，右操作数被读成前缀
    # `*`（`deref`，运行期恒等），于是 `a ** b` 等价于 `a * b`——
    # `2 ** 10` 得 20，`check` 全绿。下面第一条断言就是钉住那个形状
    # **不会再出现**（`_expr_shape` 对 `deref` 会渲染成 `deref` 字样）。
    got = _expr_shape("a ** b")
    check(got == "(a ** b)", "a ** b → (a ** b)", got)
    check("deref" not in got, "a ** b 的右边不再是 deref（旧 bug 的形状）", got)

    cases = [
        ("2 ** 10",        "(2 ** 10)"),
        ("a ** 2",         "(a ** 2)"),
        # **右结合**：`l_bp > r_bp`。左结合会得 `((a ** b) ** c)`。
        ("a ** b ** c",    "(a ** (b ** c))"),
        ("2 ** 3 ** 2",    "(2 ** (3 ** 2))"),
        # 比 `* / %` 紧：`a * b ** c` 不是 `(a * b) ** c`
        ("a * b ** c",     "(a * (b ** c))"),
        ("a ** b * c",     "((a ** b) * c)"),
        ("a ** b / c",     "((a ** b) / c)"),
        ("a ** b + c",     "((a ** b) + c)"),
        ("a - b ** c",     "(a - (b ** c))"),
        # 一元前缀比 `**` 紧（PREFIX_BP=25 > 24）：`-a ** 2` = `(-a) ** 2`
        ("-a ** 2",        "((-a) ** 2)"),
        ("~a ** 2",        "((~a) ** 2)"),
        # 右操作数走 `parse_prefix`，所以负号指数能被收进去（类型层再判）
        ("a ** -1",        "(a ** (-1))"),
    ]
    for src, want in cases:
        got = _expr_shape(src)
        check(got == want, f"{src} → {want}", got)

    # **不许破坏二元乘法**：`a * b` 仍是单个 STAR、仍是左结合
    check(_expr_shape("a * b") == "(a * b)", "a * b → (a * b)", _expr_shape("a * b"))
    check(_expr_shape("a * b * c") == "((a * b) * c)", "a * b * c 仍左结合",
          _expr_shape("a * b * c"))

    # ---- `**=` 给**专属诊断**（2026-11-10）
    # 词法是 `STAR_STAR` + `EQ`（不会静默变成 `*=`），于是以前右操作数位置
    # 撞上 `=`，报 `E-Parse-ExpectedExpr 期望表达式，实际是 '='`——明确失败，
    # 但**没说"没有 `**=` 这个运算符"**。现在必须点明并给改法。
    for src in ("u64 f(u64 a) { a **= 3; return a; }",
                "u64 f(u64 a, u64 b) { a **= b; return a; }"):
        r = parse_source(src, "t.eln")
        codes = [d.code for d in r.diagnostics]
        msg = "; ".join(d.message for d in r.diagnostics)
        check("E-Op-NoCompoundPow" in codes,
              f"`**=` 报专属码 E-Op-NoCompoundPow（{src.split('{')[1].strip()[:12]}…）",
              codes)
        check("E-Parse-ExpectedExpr" not in codes,
              "`**=` 不再落成通用的「期望表达式」", codes)
        check("没有" in msg and "**=" in msg and "x = x ** n" in msg,
              "`**=` 的诊断说清「没有这个运算符」并给出改法", msg[:160])

    # 相邻的复合赋值不受影响（不能被 `**=` 的新分支吃掉）
    r = parse_source("u64 f(u64 a) { a *= 3; a += 1; a -= 2; a /= 4; return a; }",
                     "t.eln")
    check(r.ok, "`*=` / `+=` / `-=` / `/=` 仍能解析（只拒 `**=`）",
          "; ".join(f"{d.code}@{d.line}" for d in r.diagnostics))
    stmts = walk(r.module, "assign-op")
    check(len(stmts) == 4, "四条复合赋值仍是 assign-op 语句", str(len(stmts)))


# ---------------------------------------------------------------- 12. 循环写法

def _strip_pos(node):
    """去掉 line/col 的 AST 快照：比的是**形状**，不是源码位置。"""
    return (node.kind, node.name, node.value,
            tuple(_strip_pos(c) for c in node.children))


def test_loop_spellings():
    """C++ 循环写法里最绊人的三条：`do-while` / `++` / 迭代器成员（2026-11-10）。

    · `do { } while` 以前被当成「类型名 do + 变量名」，报一句「期望 ';'」；
    · `++i;` / `i++;` 以前是纯解析错误（`E-Parse-ExpectedExpr 实际是 '+'`）；
    · `xs.end()` 以前撞上「`end` 已下线」的退役关键字诊断（完全无关），
      `xs.rbegin()` 却报「期望 'in'」——同一个缺口两套说法。
    """
    print("[12] 循环写法：do-while / ++ / 迭代器成员")

    # ---- ② `do { } while`：专属诊断 + κ 影响
    r = parse_source("u64 f(u64 i) { do { i = i + 1; } while (i < 3); return i; }",
                     "t.eln")
    codes = [d.code for d in r.diagnostics]
    msg = "; ".join(d.message for d in r.diagnostics)
    check("E-Keyword-Unsupported" in codes, "do-while 报 E-Keyword-Unsupported", codes)
    check("E-Parse-Expected" not in codes,
          "do-while 不再落成通用的「期望 ';'，实际是 '{'」", codes)
    check("while (true)" in msg and "break" in msg,
          "do-while 诊断给出 `while (true) { …; if (!(cond)) { break; } }` 改法", msg[:200])
    check("κ" in msg and "E-While-NoBound" in msg,
          "do-while 诊断提醒 `while (true)` 的 κ=⊤ 代价", msg[:260])

    # ---- ③ `++i;` / `i++;` / `--i;` / `i--;`：语句位置可用，AST 与手写赋值同形
    for src, expect in (("++i;", "i = i + 1;"), ("i++;", "i = i + 1;"),
                        ("--i;", "i = i - 1;"), ("i--;", "i = i - 1;")):
        got = parse_source(f"u64 f(u64 i) {{ {src} return i; }}", "t.eln")
        want = parse_source(f"u64 f(u64 i) {{ {expect} return i; }}", "t.eln")
        check(got.ok, f"`{src}` 能解析", "; ".join(d.code for d in got.diagnostics))
        check(_strip_pos(got.module) == _strip_pos(want.module),
              f"`{src}` 的 AST == `{expect}` 的 AST",
              got.module.children[0].dump() if got.module else "无 module")

    # 下标/字段位置也走同一条 desugar
    for src, expect in (("xs[i]++;", "xs[i] = xs[i] + 1;"),
                        ("++xs[i];", "xs[i] = xs[i] + 1;")):
        got = parse_source(f"u64 f(vector<u64>& xs, u64 i) {{ {src} return 0; }}",
                           "t.eln")
        want = parse_source(f"u64 f(vector<u64>& xs, u64 i) {{ {expect} return 0; }}",
                            "t.eln")
        check(got.ok and _strip_pos(got.module) == _strip_pos(want.module),
              f"`{src}` 的 AST == `{expect}` 的 AST",
              "; ".join(d.code for d in got.diagnostics))

    # **不能当表达式用**
    for src in ("x = i++;", "x = ++i;", "return i++;", "f(i++);"):
        r = parse_source(f"u64 f(u64 i) {{ u64 x = 0; {src} return x; }}", "t.eln")
        codes = [d.code for d in r.diagnostics]
        msg = "; ".join(d.message for d in r.diagnostics)
        check("E-Unary-Unsupported" in codes, f"`{src}` 被明确拒绝", codes)
        check("只能作独立语句" in msg, f"`{src}` 的文案点明「只能作独立语句」", msg[:160])

    # ---- ④ 迭代器成员：`begin` / `end` / `rbegin` / `rend` 一个说法
    for expr in ("xs.end()", "xs.rbegin()", "xs.begin", "xs.rend()"):
        r = parse_source(f"u64 f(const vector<u64>& xs) {{ return {expr}; }}",
                         "t.eln")
        codes = [d.code for d in r.diagnostics]
        msg = "; ".join(d.message for d in r.diagnostics)
        check("E-Call-UnknownMethod" in codes, f"`{expr}` 报 E-Call-UnknownMethod", codes)
        check("E-End-Removed" not in codes, f"`{expr}` 不再撞「end 已下线」", codes)
        check("本语言没有迭代器" in msg and "for (auto& x : xs)" in msg
              and "for (auto x : xs)" in msg and "rbegin" in msg,
              f"`{expr}` 的诊断是统一的「没有迭代器」+ 三种替代写法", msg[:200])

    # **单独出现的 `end` 仍是退役关键字**（判据是成员访问位置，不是词本身）
    r = parse_source("u64 f() { return 1; } end func", "t.eln")
    codes = [d.code for d in r.diagnostics]
    msg = "; ".join(d.message for d in r.diagnostics)
    check("E-End-Removed" in codes, "单独出现的 `end` 仍报 E-End-Removed", codes)
    check("已下线" in msg, "单独出现的 `end` 仍说「已下线」", msg[:120])


# ---------------------------------------------------------------- 13. 诊断质量

def test_diag_quality():
    """语句起点的 C++ 构造 → 专属诊断，而不是「期望 ';'」（2026-11-10）。

    硬约束：**只加错误分支**——`T x;`（未知类型名）与 `new = 1;`（变量赋值）
    必须照旧解析，不能因为诊断表而变红。
    """
    print("[13] 诊断质量：C++ 构造的专属诊断 + 合法写法零误伤")

    def codes_of(src, name="t.eln"):
        r = parse_source(src, name)
        return [d.code for d in r.diagnostics], "; ".join(d.message for d in r.diagnostics), r

    # ---- ① 语句起点关键字：一个缺口一种说法，且带改法
    keyword_cases = [
        ("throw 1;", "throw"),
        ("try { return 0; }", "try"),
        ("switch (n) { }", "switch"),
        ("using T = vector<u64>;", "using"),
        ("template <typename T> T f(T a) { return a; }", "template"),
        ("static u64 counter = 0;", "static"),
        ("sizeof(n);", "sizeof"),
        ("decltype(n) x;", "decltype"),
        ("new Foo(1);", "new"),
        ("delete p;", "delete"),
        ("this->x = 1;", "this"),
        ("nullptr;", "nullptr"),
        ("auto x = 1;", "auto"),
    ]
    for body, word in keyword_cases:
        codes, msg, _r = codes_of(f"u64 f(u64 n) {{ {body} return n; }}")
        check("E-Keyword-Unsupported" in codes,
              f"语句起点 `{word}` 报 E-Keyword-Unsupported", codes)
        check("本语言没有" in msg and "改法" in msg,
              f"`{word}` 的文案有「本语言没有」+「改法」", msg[:200])
        check("E-Parse-Expected" not in codes,
              f"`{word}` 不再落成通用「期望 ';'」", codes)

    # 顶层同一张表：`typedef` / `using` 不再只是「这里不该出现 'ident'」
    codes, msg, _r = codes_of("typedef vector<int> T;\nspec g() -> u64 { effects {}; }",
                              "a.tu")
    check("E-Keyword-Unsupported" in codes and "本语言没有" in msg,
          "`.tu` 顶层 `typedef` 报 E-Keyword-Unsupported", (codes, msg[:120]))
    check("E-Parse-UnexpectedTop" not in codes,
          "`.tu` 顶层 `typedef` 不再报 E-Parse-UnexpectedTop", codes)

    # ---- 判据窄：合法写法不许被吞
    for body in ("u64 x = 1;", "Foo x;", "new = 1;", "this.x = 1;",
                 "new = new + 1;"):
        codes, msg, _r = codes_of(f"u64 f(Foo& this, u64 new) {{ {body} return 0; }}")
        check(not codes, f"合法写法 `{body}` 零诊断（诊断表不吞它）", (codes, msg[:160]))

    # ---- ② 三段式 for：**接受**，desugar 成 `init; while (cond) { body; step; }`
    # （2026-11-10 晚，审核文档 §2「两种都保留」；成本按 while 推导）
    tri = "for (u64 i = 0; i < n; i++) { u64 x = i; }"
    codes, msg, r = codes_of(f"u64 f(u64 n) {{ {tri} return 0; }}")
    check(not codes, f"`{tri}` 零诊断", (codes, msg[:200]))
    tri_want = ("{ u64 i = 0; while (i < n) { u64 x = i; i = i + 1; } }")
    want = parse_source(f"u64 f(u64 n) {{ {tri_want} return 0; }}", "t.eln")
    check(r.ok and want.ok and _strip_pos(r.module) == _strip_pos(want.module),
          "三段式 for 的 AST == `init; while (cond) { body; step; }`",
          r.module.children[0].dump() if r.module else "无 module")

    # `int i` / 外部已有的 `i` / 空 init·cond·step 都必须能吃
    for src in ("for (int i = 0; i < n; i++) { }",
                "for (i = 0; i < n; i = i + 1) { }",
                "for (; i < n; i++) { }",
                "for (;;) { break; }"):
        codes, msg, _r = codes_of(
            f"u64 f(u64 n) {{ u64 i = 0; {src} return 0; }}")
        check(not codes, f"三段式变体 `{src}` 零诊断", (codes, msg[:200]))
        check("E-Parse-Expected" not in codes,
              f"`{src}` 不再级联「期望 ';'」", codes)

    # `continue` 在 C++ 的 `for` 里仍执行第三段：desugar 后必须把 step 插到
    # `continue` 前面，否则 `for (…; …; i++)` 里的 `continue` 会卡住不推进。
    cont_src = ("u64 f(u64 n) { for (u64 i = 0; i < n; i = i + 1) "
                "{ continue; } return 0; }")
    cont_r = parse_source(cont_src, "t.eln")
    guarded = [b for b in walk(cont_r.module, "block")
               if any(c.kind == "continue" for c in b.children)]
    check(cont_r.ok and len(guarded) == 1
          and guarded[0].children[0].kind == "assign"
          and guarded[0].children[-1].kind == "continue",
          "三段式里的 `continue` 前插入了 step（保留 C++ 的 for 语义）",
          cont_r.module.children[0].dump() if cont_r.module else "无 module")

    # **迭代器仍然不支持**：`E-For-NoIterator` 保留，且只在这一种形状上报
    it_src = ("for (auto it = xs.begin(); it != xs.end(); ++it) { }")
    codes, msg, _r = codes_of(
        f"u64 f(const vector<u64>& xs) {{ {it_src} return 0; }}")
    check("E-For-NoIterator" in codes,
          "`for (auto it = xs.begin(); …)` 仍报 E-For-NoIterator", codes)
    check("三段式" in msg and "begin" in msg and "for (auto& x : xs)" in msg,
          "迭代器诊断说清「三段式语法接受、迭代器不存在」并给改法", msg[:260])
    check(not any(c.startswith("E-Parse-Expected") for c in codes)
          and "E-Call-UnknownMethod" not in codes,
          "迭代器形状不级联（头与体已被吞掉）", codes)

    # ---- `for (… in …)` 三种写法**已删除**（2026-11-10 夜）：必须报
    # `E-For-InRemoved`，且消息里三种替代写法都在。
    for src in ("for (i in 0..n) { }", "for (x in xs) { }",
                "for (i, x in xs) { }"):
        codes, msg, _r = codes_of(
            f"u64 f(vector<u64>& xs, u64 n) {{ {src} return 0; }}")
        check("E-For-InRemoved" in codes, f"`{src}` 报 E-For-InRemoved",
              (codes, msg[:160]))
        check("for (auto& x : xs)" in msg and "for (auto x : xs)" in msg
              and "for (u64 i = 0; i < n; i = i + 1)" in msg,
              f"`{src}` 的消息教三种替代写法", msg[:240])

    # ---- ③ C++ range-for：四种前缀都认，**绑定类别记在 `node.ref`**
    #（`auto&` → "mut"，`const auto&` / `const T&` → "const"，`T x` → None）
    for prefix, want in (("const auto&", "const"), ("auto&", "mut"),
                         ("const u64&", "const"), ("u64", None)):
        src = f"for ({prefix} x : xs) {{ n = n + x; }}"
        r = parse_source(
            f"u64 f(const vector<u64>& xs, u64 n) {{ {src} return n; }}",
            "t.eln")
        codes = [d.code for d in r.diagnostics]
        check(not codes, f"`{src}` 零诊断", codes)
        fnode = next(iter(walk(r.module, "for")), None) if r.ok else None
        check(fnode is not None and getattr(fnode, "ref", "MISSING") == want,
              f"`{src}` 的 node.ref = {want!r}",
              getattr(fnode, "ref", "MISSING"))

    # `match token {` 的 `{` 是 match 的体，不能被位置式初始化的判据误伤
    codes, msg, _r = codes_of(
        "u64 f(u64 t) { match t { _ => { return 0; } } return 1; }")
    check(not codes, "`match token {` 仍能解析（不是位置式初始化）", (codes, msg[:160]))

    # ---- ③ 退役写法的迁移提示（每条都指向「新写法」）
    codes, msg, _r = codes_of("u64 f(u64 x) { const u64 y = 0o17; return y; }")
    check("E-Lex-OctalUnsupported" in codes and "0x" in msg and "十进制" in msg,
          "`0o17` 报 E-Lex-OctalUnsupported + 十进制/0x 改法", (codes, msg[:200]))

    codes, msg, _r = codes_of("u64 f(u64 x) {\n/* 块注释 */\n return x; }")
    check(not codes,
          "`/* */` 是块注释（2026-11-13 起支持，零诊断）", (codes, msg[:200]))
    codes, msg, _r = codes_of("u64 f(u64 x) {\n/* 没闭合\n return x; }")
    check("E-Lex-BlockCommentUnterminated" in codes and "没有闭合" in msg,
          "未闭合的 `/*` 报 E-Lex-BlockCommentUnterminated（不静默吞到文件尾）",
          (codes, msg[:200]))
    codes, msg, _r = codes_of("u64 f(u64 x) { return x; } // 这里写 /* 不算")
    check(not codes, "`//` 里的 `/*` 仍是普通文本", (codes, msg[:160]))

    codes, msg, _r = codes_of("u64 f(u64 x) { u64[4] a; return x; }")
    check("E-Type-OldBounded" in codes and "vector<T>" in msg and "array<T, N>" in msg,
          "`T[...]` 报 E-Type-OldBounded + vector/array 改法", (codes, msg[:200]))

    # `Vec<u64>` 仍是**合法别名**（typenames.canon）：不能为了提示把它变成错误
    codes, msg, _r = codes_of("u64 f(u64 n) { Vec<u64> xs; return n; }")
    check(not codes, "`Vec<u64>` 仍是合法别名（只改错误建议，不改合法语法）",
          (codes, msg[:160]))

    # ---- const 不做类型推断：继续报错，且理由写进消息
    codes, msg, _r = codes_of("u64 f() { const idx = 5; return idx; }")
    check("E-Decl-ConstNoType" in codes, "`const idx = 5;` 仍报错（不推断）", codes)
    check("显式类型优于类型推断" in msg and "const u64 idx = 5;" in msg,
          "缺类型诊断给 `const u64 idx = 5;` + 设计理由", msg[:200])

    # ---- 其余专属分支（都在原先就是错误的位置，只换说法）
    extra = [
        ("struct D : B { y: u64, }", "E-Struct-NoInheritance", "a.tu"),
        ("struct Box<T> { v: T, }", "E-Struct-NoGeneric", "a.tu"),
        ("struct S { u64 sum() const { return 0; } }", "E-Struct-BodyForm", "a.tu"),
        ("struct S { x: u64 = 5, }", "E-Struct-DefaultMember", "a.tu"),
        ("struct S { struct T { v: u64, } x: u64, }", "E-Struct-NoNested", "a.tu"),
        ("enum class Color { Red }", "E-Enum-NoClass", "a.tu"),
        ("enum E : u8 { A }", "E-Enum-NoUnderlying", "a.tu"),
        ("enum E { A = 5 }", "E-Enum-ExplicitValue", "a.tu"),
        ("spec f(u64... xs) -> u64 { effects {}; }", "E-Param-Variadic", "a.tu"),
        ("spec f<u64 N>() -> u64 { effects {}; }", "E-Spec-NonTypeParam", "a.tu"),
        ("u64 counter = 0;\nspec f() -> u64 { effects {}; }", "E-Decl-ModuleVar", "a.tu"),
    ]
    for src, want, name in extra:
        codes, msg, _r = codes_of(src, name)
        check(want in codes, f"`{src[:38]}` 报 {want}", (codes, msg[:160]))

    body_extra = [
        ("u64 a[] = {1, 2};", "E-Type-NoCArray"),
        ("const Point p = Point { 1, 2 };", "E-Type-PositionalInit"),
        ("const u64 r = match (n) { _ => { 1 } };", "E-Match-NotExpr"),
        ("sort_by(xs, |a, b| { return a < b; });", "E-Lambda-Unsupported"),
        ("u64 y = (u8)n;", "E-Cast-CStyle"),
        ("u64 y = static_cast<u8>(n);", "E-Cast-Unsupported"),
    ]
    for body, want in body_extra:
        codes, msg, _r = codes_of(
            f"u64 f(vector<u64>& xs, u64 n) {{ {body} return 0; }}")
        check(want in codes, f"`{body[:40]}` 报 {want}", (codes, msg[:160]))

    # ---- R1-A（2026-11-10）：C++ 拼写不再报错（`char` 以前报 E-Type-NoChar）
    for body in ("char c = 65;", "int d = 40;", "size_t m = 10;",
                 "unsigned long long big = 20;", "std::vector<u64> v;",
                 'std::string s = "hi";', "using namespace std;"):
        codes, msg, _r = codes_of(
            f"u64 f(vector<u64>& xs, u64 n) {{ {body} return 0; }}")
        check(not codes, f"`{body[:34]}` 零诊断（C++ 拼写归一）", (codes, msg[:200]))


# ---------------------------------------------------------------- R1-A：C++ 形状

def _fn(mod, name):
    return next((c for c in mod.children if c.kind == "fn" and c.name == name), None)


def _ret_name(fn):
    """`fn` 的返回类型名（子节点顺序是「类型参数 → 形参 → 返回类型 → 体」）。"""
    for c in fn.children:
        if c.kind == "type-name":
            return c.name
        if c.kind == "type-ref":
            return c.children[0].name if c.children else None
    return None


def _first_param_type(fn):
    ps = next((c for c in fn.children if c.kind == "params"), None)
    if ps is None or not ps.children:
        return None
    p = ps.children[0]
    ty = p.children[0] if p.children else None
    return getattr(ty, "name", None)


def test_cxx_surface():
    """R1-A（2026-11-10）：`.eln` 吃 C++ 的四件事。

    ① 函数声明用 C++ 形状（`u64 f(…)` / `auto f(…) -> T`），`func` 仍接受；
    ② `std::vector` 与 `vector` **是同一个类型**（归一在 AST 上，不在下游）；
    ③ `#include` / `using namespace std;` 接受（词法层另有断言）；
    ④ `int` / `unsigned` / `size_t` / `char` … 映射表（方案甲：整数 64 位、
       定长名按位宽）。
    """
    print("[14] R1-A：C++ 形状（删 func / std:: 归一 / using / int 映射）")

    def parse(src, name="t.eln"):
        r = parse_source(src, name)
        return r, [d.code for d in r.diagnostics], "; ".join(d.message for d in r.diagnostics)

    # ---- ① C++ 前置 / 后置返回产出**同形**的 AST；旧 `func` 形已下线（P4-0）
    for src, label, removed in (
        ("u64 add_one(const u64& x) { return x + 1; }", "C++ 前置返回", False),
        ("auto add_one(const u64& x) -> u64 { return x + 1; }", "C++ 后置返回", False),
        ("func add_one(const u64& x) -> u64 { return x + 1; }", "旧 `func` 写法", True),
    ):
        r, codes, msg = parse(src)
        check(f"{label}：" + ("报 E-Func-KeywordRemoved（P4-0 只留 C++ 形）"
                             if removed else "零诊断"),
              ("E-Func-KeywordRemoved" in codes) if removed else (not codes),
              (codes, msg[:200]))
        fn = _fn(r.module, "add_one")
        check(f"{label}：名字与返回类型", fn is not None and _ret_name(fn) == "u64",
              (getattr(fn, "name", None), getattr(fn, "children", None)))
        check(f"{label}：`const u64&` 形参仍在（const 没被丢）",
              fn is not None and _first_param_type(fn) == "u64",
              _first_param_type(fn) if fn is not None else None)
        _p = (next((c for c in fn.children if c.kind == "params"), None) if fn else None)
        check(f"{label}：形参 const 标记保留",
              bool(_p and _p.children and (_p.children[0].value or {}).get("const")),
              getattr(_p.children[0], "value", None) if _p and _p.children else None)

    # `func` 之后不再必需，但**没写返回类型**的 `auto` 必须报错（没有类型推断）
    r, codes, msg = parse("auto f(const u64& x) { return x; }")
    check("`auto f(…)` 缺后置返回报 E-Fn-AutoNoReturn",
          "E-Fn-AutoNoReturn" in codes, (codes, msg[:200]))

    # 顶层变量仍是错（不能被「C++ 形状」这条吞掉）
    r, codes, msg = parse("u64 counter = 0;", "a.tu")
    check("顶层变量仍报 E-Decl-ModuleVar", "E-Decl-ModuleVar" in codes, (codes, msg[:160]))

    # ---- ② `std::` 与短名同一个类型
    r, codes, msg = parse("u64 total(const std::vector<u64>& xs) { return 0; }")
    check("`std::vector` 零诊断", not codes, (codes, msg[:200]))
    check("`std::vector<u64>` 归一成 `vector`（下游只看短名）",
          _first_param_type(_fn(r.module, "total")) == "vector",
          _first_param_type(_fn(r.module, "total")))

    r, codes, msg = parse("u64 total(const vector<u64>& xs) { return 0; }")
    check("短名 `vector` 仍可用（REQUIRE_USING_FOR_SHORT_NAMES=False）",
          not codes and _first_param_type(_fn(r.module, "total")) == "vector",
          (codes, msg[:160]))

    for src, want in (
        ("u64 f(const std::map<u64, u64>& m) { return 0; }", "map"),
        ("u64 f(const std::unordered_map<u64, u64>& m) { return 0; }", "unordered_map"),
        ("u64 f(const std::unordered_set<u64>& m) { return 0; }", "unordered_set"),
        ("u64 f(const std::set<u64>& m) { return 0; }", "set"),
        ("u64 f(const std::array<u64, 4>& a) { return 0; }", "array"),
        ("u64 f(const std::optional<u64>& o) { return 0; }", "optional"),
    ):
        r, codes, msg = parse(src)
        got = _first_param_type(_fn(r.module, "f"))
        check(f"`std::{want}` 归一成 `{want}`", not codes and got == want, (codes, got))

    # `string` 是 `Text` 的别名，运行时形态是 `vector<u8>`（§8.8）
    r, codes, msg = parse('std::string greet() { std::string s = "hi"; return s; }')
    check("`std::string` 归一成 `vector<u8>` 且零诊断",
          not codes and _ret_name(_fn(r.module, "greet")) == "vector",
          (codes, _ret_name(_fn(r.module, "greet")) if _fn(r.module, "greet") else None))

    # `std::expected<T,E>` 归一到 `Result`（§8.3：Result 只留名字，类型即 expected）
    r, codes, msg = parse("std::expected<u64, Io> f(const std::vector<u64>& xs) { return 0; }")
    check("`std::expected` 归一（零解析错误）", not codes, (codes, msg[:200]))

    # 表外的 `std::` 名字与 `pair` 给**专属**诊断（不是静默当用户类型名）
    r, codes, msg = parse("std::cout c;")
    check("表外 `std::cout` 报 E-Type-UnknownStdName",
          "E-Type-UnknownStdName" in codes, (codes, msg[:200]))
    r, codes, msg = parse("std::pair<u64, u64> p;")
    check("`std::pair` 报 E-Type-NoPair + 元组改法",
          "E-Type-NoPair" in codes and "(K, V)" in msg, (codes, msg[:200]))

    # ---- ③ `using namespace std;` 被接受，并记录在 parser 状态里
    r, codes, msg = parse("using namespace std;\nu64 f(vector<u64> xs) { return 0; }")
    check("`using namespace std;` 零诊断", not codes, (codes, msg[:200]))
    check("`using namespace std;` 产生 using-namespace 节点",
          any(c.kind == "using-namespace" and c.name == "std" for c in r.module.children))
    r, codes, msg = parse("using namespace chrono;")
    check("非 std 命名空间报 E-Use-UnknownNamespace",
          "E-Use-UnknownNamespace" in codes, (codes, msg[:200]))
    r, codes, msg = parse("using std::vector;")
    check("单名 `using std::vector;` 报 E-Use-StdSingleName（指出整行写法）",
          "E-Use-StdSingleName" in codes and "using namespace std;" in msg,
          (codes, msg[:200]))

    # ---- ④ 标量映射表（方案甲 + 定长按位宽）
    scalar_cases = [
        ("int x = 0;", "i64"),
        ("unsigned x = 0;", "u64"),
        ("unsigned int x = 0;", "u64"),
        ("long x = 0;", "i64"),
        ("long long x = 0;", "i64"),
        ("unsigned long long x = 0;", "u64"),
        ("short x = 0;", "i64"),
        ("size_t x = 0;", "u64"),
        ("ssize_t x = 0;", "i64"),
        ("char x = 'a';", "u8"),
        ("signed char x = 0;", "i8"),
        ("unsigned char x = 0;", "u8"),
        ("bool x = true;", "bool"),
        ("double x = 0.5;", "f64"),
        ("float x = 0.5;", "f32"),
        ("long double x = 0.5;", "f64"),
        ("uint64_t x = 0;", "u64"),
        ("int64_t x = 0;", "i64"),
        ("uint32_t x = 0;", "u32"),
        ("int32_t x = 0;", "i32"),
        ("int16_t x = 0;", "i16"),
        ("uint8_t x = 0;", "u8"),
    ]
    for decl, want in scalar_cases:
        r, codes, msg = parse(f"u64 f() {{ {decl} return 0; }}")
        node = r.module.children[0] if r.module.children else None
        blk = next((c for c in (node.children if node else []) if c.kind == "block"), None)
        got = None
        for st in (blk.children if blk else []):
            if st.kind == "var-decl":
                got = st.children[0].name if st.children else None
        check(f"`{decl}` → `{want}`", not codes and got == want, (codes, got))

    # `int main()` 必须能当入口（产品目标的第一屏）
    r, codes, msg = parse('int main() { print("{}", 1); return 0; }')
    check("`int main()` 零诊断且被认成函数 main",
          not codes and _fn(r.module, "main") is not None, (codes, msg[:200]))
    check("`int main()` 的返回类型是 i64", _ret_name(_fn(r.module, "main")) == "i64",
          getattr(_fn(r.module, "main"), "children", None))

    # 一整屏 C++（include + using + int + std::）
    screen = (
        "#include <vector>\n"
        "#include <cstdint>\n"
        "#include <cstddef>\n"
        "using namespace std;\n"
        "\n"
        "int main() {\n"
        "    std::vector<int> xs = std::vector<int>::make();\n"
        "    xs.push(1);\n"
        "    size_t n = xs.length();\n"
        "    print(\"{}\", n);\n"
        "    return 0;\n"
        "}\n"
    )
    r, codes, msg = parse(screen)
    check("C++ 第一屏整段零诊断（include/using/int/std::/size_t）",
          not codes, (codes, msg[:240]))


# ---------------------------------------------------------------- 15. rename-and-go

def test_cxx_rename_go():
    """真实 C++ 文件改名 `.eln` 后：**词形不再误伤**（2026-11-10）。

    钉住四件事（都是实测出来的误报）：
      ① `end` 作参数名 / 变量名 / 下标 / 成员名合法，只有**块终结符位置**报错；
      ② `val` 只在**语句起始 + 后跟标识符**的声明形状上报（`long long val` 合法）；
      ③ `public:` / `private:` / `protected:` 在 struct 体里**接受并忽略**；
      ④ `namespace X { … }` **接受并展平**（重名报 `E-Name-Duplicate`）。
    """
    print("[15] rename-and-go：词形不再误伤（end / val / 访问说明符 / namespace）")

    def parse(src, name="t.eln"):
        r = parse_source(src, name)
        return r, [d.code for d in r.diagnostics], "; ".join(
            d.message for d in r.diagnostics)

    # ---- ① `end` 的四个合法位置
    for src, label in (
        ("u64 f(u64 end) { return end; }", "形参名"),
        ("u64 f() { u64 end = 3; return end; }", "变量名"),
        ("u64 f(const vector<u64>& xs, u64 end) { return xs[end]; }", "下标"),
    ):
        r, codes, msg = parse(src)
        check(not codes and "E-End-Removed" not in codes,
              f"`end` 作{label}零诊断", (codes, msg[:160]))

    # 成员名：**仍报「没有迭代器」**（另一条真实缺口），但**不是**退役关键字
    r, codes, msg = parse("u64 f(const vector<u64>& xs) { return xs.end(); }")
    check("E-Call-UnknownMethod" in codes and "E-End-Removed" not in codes,
          "`xs.end()` 报「没有迭代器」，**不**报「end 已下线」", (codes, msg[:160]))

    # 只有块终结符位置才报：`} end fn` / `} end if`
    r, codes, msg = parse("u64 f() { return 1; } end fn")
    check("E-End-Removed" in codes and "已下线" in msg,
          "`} end fn` 仍报 E-End-Removed", (codes, msg[:160]))
    r, codes, msg = parse("u64 f(u64 x) { if (x > 0) { return 1; } end if return 0; }")
    check("E-End-Removed" in codes,
          "`} end if`（if 块收尾）也报 E-End-Removed", (codes, msg[:160]))
    check(not any(c.startswith("E-Parse") for c in codes),
          "`} end if` 不再级联出 E-Parse-*", codes)

    # ---- ② `val` 只在声明形状上报
    r, codes, msg = parse("u64 f() { val x = 1; return x; }")
    check("E-Decl-OldKeyword" in codes, "`val x = 1;` 仍报 E-Decl-OldKeyword", codes)
    r, codes, msg = parse("u64 f() { long long val = 3; return val; }")
    check(not codes and "E-Decl-OldKeyword" not in codes,
          "`long long val = 3;`（val 是变量名）零诊断", (codes, msg[:160]))
    r, codes, msg = parse("u64 f() { var v = 1; return v; }")
    check("E-Decl-OldKeyword" in codes, "`var v = 1;` 仍报 E-Decl-OldKeyword", codes)

    # ---- ③ 访问说明符：struct 体里接受并忽略（C++ 字段写法也接受）
    src = ("struct S {\n"
           "    public: u64 x;\n"
           "    private: u64 y;\n"
           "    protected: u64 z;\n"
           "}\n")
    r, codes, msg = parse(src, "a.tu")
    fields = [c.name for c in (r.module.children[0].children
                               if r.module.children else [])]
    check(not codes, "`public:` / `private:` / `protected:` 在 struct 体里零诊断",
          (codes, msg[:200]))
    check(fields == ["x", "y", "z"], "三个字段都被收下（忽略的是可见性，不是字段）",
          str(fields))
    # 顶层的 `public func` 仍是长写法错误（应写 `pub`）
    r, codes, msg = parse("public u64 f() -> u64 { return 1; }")
    check("E-Keyword-LongForm" in codes and "pub" in msg,
          "顶层 `public` 仍报 E-Keyword-LongForm（写 `pub`）", (codes, msg[:160]))

    # ---- ④ namespace：展平；`foo::add` 不支持；重名报 E-Name-Duplicate
    r, codes, msg = parse("namespace foo {\n"
                          "    u64 add(u64 a, u64 b) { return a + b; }\n"
                          "}\n")
    kinds = [c.kind for c in r.module.children]
    check(not codes and kinds == ["fn"] and r.module.children[0].name == "add",
          "`namespace foo { func add }` 展平成全局 `add`", (codes, kinds))
    r, codes, msg = parse("namespace { u64 anon_f() { return 0; } }")
    check(not codes and [c.kind for c in r.module.children] == ["fn"],
          "匿名 `namespace { … }` 也展平", (codes, msg[:160]))
    r, codes, msg = parse("u64 add() { return 1; }\n"
                          "namespace foo { u64 add() { return 2; } }\n")
    check("E-Name-Duplicate" in codes and "展平" in msg,
          "展平后重名报 E-Name-Duplicate（不静默覆盖）", (codes, msg[:200]))
    check(len([c for c in r.module.children if c.kind == "fn"]) == 1,
          "重名时**丢掉后来者**（不动已有定义）", str(len(r.module.children)))

    # ---- ⑤ 错误恢复跳过 > 20 行时，必须留下 E-Parse-SkippedBody
    r, codes, msg = parse("class Small { int x; }\n")
    check("E-Parse-SkippedBody" not in codes,
          "跳过很少几行时**不**报 E-Parse-SkippedBody（恢复是正常机制）", codes)
    big = "class Big {\n" + "    int x = 1;\n" * 25 + "}\n"
    r, codes, msg = parse(big)
    check("E-Parse-SkippedBody" in codes and "没有被解析" in msg,
          "跳过 > 20 行时报 E-Parse-SkippedBody（错误数少 ≠ 解析通过）",
          (codes, msg[:200]))


# ---------------------------------------------------------------- 指针（①–③ 语法面）

def test_pointers():
    """指针：`T*` / `&x` / `*p` / `p->f` / `nullptr`（2026-11-10）。

    **这一组钉住三件容易做错的事**（也是本次改动的验收点）：

    1. `a * b` 仍是**乘法**、`a ** b` 仍是**幂**——不许被前缀 `*` 吞掉；
    2. `u64 * p` / `MyStruct * q`（`*` 与类型名之间有空格）**是**指针声明：
       判据是「左边的名字是不是已知类型名」（C++ 的规则），不是空格；
    3. `p->f` 与 `(*p).f` **同 AST**（`field(deref(p), f)`），
       所以下游（interp / cost）不需要为 `->` 新增一个分支。
    """
    print("[P] 指针：类型 / 取地址 / 解引用 / `->` / `nullptr`")

    def parse(src):
        r = parse_source(src, "t.eln")
        return r, [d.code for d in r.diagnostics]

    # ---- ① 指针类型
    r, codes = parse("u64 f(int argc, char** argv) { return 0; }")
    check(not codes, "`char** argv` 能解析", codes)
    tps = walk(r.module, "type-ptr")
    check(len(tps) == 2 and tps[0].children[0].kind == "type-ptr",
          "`char**` 是两层 `type-ptr`（指向指针的指针）",
          [t.kind for t in tps])

    r, codes = parse("struct Json { kind: u64, }\n"
                     "u64 g(const Json* p) { return 0; }")
    check(not codes, "`const Json*` 能解析", codes)
    tp = walk(r.module, "type-ptr")[0]
    check((tp.value or {}).get("pointee_const") is True,
          "`const Json*` 的 const 记在**指向**上（不是绑定不可变）", str(tp.value))

    r, codes = parse("struct Json { kind: u64, }\n"
                     "u64 g(Json* const p) { return 0; }")
    check(not codes, "`Json* const p` 能解析", codes)
    tp = walk(r.module, "type-ptr")[0]
    check((tp.value or {}).get("ptr_const") is True,
          "`Json* const` 的 const 记在**指针本身**上", str(tp.value))

    r, codes = parse("u64 g() { u64* p = nullptr; return 0; }")
    check(not codes and len(walk(r.module, "lit-null")) == 1,
          "`nullptr` 是字面量节点（不是未声明的名字）", codes)

    # ---- ② 取地址 / 解引用 / 成员访问
    r, codes = parse("u64 g() { u64 x = 3; u64* p = &x; u64 y = *p; "
                     "return y; }")
    check(not codes and len(walk(r.module, "ref")) == 1
          and len(walk(r.module, "deref")) == 1,
          "`&x` 产出 ref、`*p` 产出 deref", (codes,))

    r, codes = parse("struct S { field: u64, }\n"
                     "u64 g(S* q) { return q->field; }")
    d = walk(r.module, "deref")
    fld = [f for f in walk(r.module, "field")
           if f.children and f.children[0].kind == "deref"]
    check(not codes and len(d) == 1 and len(fld) == 1
          and fld[0].children[0] is d[0],
          "`q->field` 与 `(*q).field` 同形（`field(deref(q), field)`）",
          (codes, len(d), len(fld)))

    r, codes = parse("u64 g(char** argv) { char c = **argv; return 0; }")
    check(not codes and len(walk(r.module, "deref")) == 2,
          "`**argv` 是两级 deref", (codes,))

    # ---- ③ 判空
    r, codes = parse("bool g(u64* p) { if (p != nullptr) { return true; } "
                     "if (p) { return true; } return false; }")
    check(not codes, "`p != nullptr` / `if (p)` 能解析", codes)

    # ---- 乘法 / 幂不许被前缀 `*` 吞掉
    r, codes = parse("u64 g(u64 a, u64 b) { return a * b; }")
    check(not codes and not walk(r.module, "deref"),
          "`a * b` 仍是二元乘法（不产出 deref）", codes)
    r, codes = parse("u64 g(u64 a) { return a ** 2; }")
    check(not codes and not walk(r.module, "deref")
          and walk(r.module, "binary")[0].value == "**",
          "`a ** 2` 仍是幂（不产出 deref）", codes)

    # ---- 消歧规则（2026-11-10②）：看**左边是不是已知类型名**，不看空格
    r, codes = parse("u64 g() { u64 x = 1; u64 * p = &x; return x; }")
    decls = walk(r.module, "var-decl")
    check(not codes and "E-Pointer-Spacing" not in codes,
          "`u64 * p`（有空格）**是声明**：u64 是已知类型名，不再报 E-Pointer-Spacing",
          codes)
    check(len(walk(r.module, "type-ptr")) == 1
          and any(d.children and d.children[0].kind == "type-ptr" for d in decls),
          "`u64 * p` 的 AST 是 `var-decl(type-ptr(u64))`",
          [d.kind for d in decls])

    r, codes = parse("u64 g(u64 a, u64 b) { a * b; return 0; }")
    bins = walk(r.module, "binary")
    check(not codes and len(bins) == 1 and bins[0].value == "*"
          and not walk(r.module, "type-ptr"),
          "`a * b;`（a 是形参）仍是乘法：左边不是类型名", codes)

    r, codes = parse("u64 g(u64 a) { u64 x = a * a; x * x; return x; }")
    check(not codes and len(walk(r.module, "type-ptr")) == 0
          and len(walk(r.module, "binary")) == 2,
          "`x * x;`（x 是局部变量）仍是乘法", codes)

    r, codes = parse("struct MyStruct { x: u64, }\n"
                     "u64 g() { MyStruct * q; return 0; }")
    check(not codes and len(walk(r.module, "type-ptr")) == 1,
          "`MyStruct * q`（struct 已声明）是声明", codes)

    r, codes = parse("u64 g() { MyStruct * q; return 0; }")
    check(not codes and len(walk(r.module, "type-ptr")) == 1,
          "前向引用的 `MyStruct * q` 先按声明解析（未知类型由 typecheck 报，"
          "消息里指出“确认类型名已声明在前面”）", codes)

    r, codes = parse("struct Json { kind: u64, }\n"
                     "u64 g(const Json * p) { return 0; }")
    tp = walk(r.module, "type-ptr")
    check(not codes and tp and (tp[0].value or {}).get("pointee_const") is True,
          "`const Json * p`（有空格）仍是「指向 const」的指针", codes)

    r, codes = parse("struct Json { kind: u64, }\n"
                     "u64 g(Json * const p) { return 0; }")
    tp = walk(r.module, "type-ptr")
    check(not codes and tp and (tp[0].value or {}).get("ptr_const") is True,
          "`Json * const p`（有空格）仍是 const 指针", codes)

    r, codes = parse("u64 g(int argc, char ** argv) { return 0; }")
    check(not codes and len(walk(r.module, "type-ptr")) == 2,
          "`char ** argv`（两个 `*` 都有空格）仍是两层指针", codes)

    r, codes = parse("u64 g(int argc, char * argv[]) { return 0; }")
    arg = [p for p in walk(r.module, "param") if p.name == "argv"]
    check(not codes and arg and arg[0].children[0].kind == "type-ptr"
          and arg[0].children[0].children[0].kind == "type-ptr",
          "`char * argv[]` 是声明（形参 `[]` ≡ 多一层指针，C++ 的数组退化）",
          codes)

    # ---- 单条语句体（C++ `if (…) return x;`）与块同形
    r, codes = parse("u64 g(u64 n) { if (n > 0) return 1; return 0; }")
    blk = walk(r.module, "if")[0].children[1]
    check(not codes and blk.kind == "block" and len(blk.children) == 1,
          "`if (…) return 1;` 的单条语句体 desugar 成 block", codes)


# ---------------------------------------------------------------- main

def main():
    test_samples()
    test_spec_clauses()
    test_benchmodel()
    test_pathsep()
    test_scale_in()
    test_types()
    test_enum()
    test_statements()
    test_char_literals()
    test_int_literals()
    test_choose_kernel_dispatch()
    test_errors()
    test_determinism()
    test_bitwise_precedence()
    test_pow()
    test_loop_spellings()
    test_diag_quality()
    test_cxx_surface()
    test_cxx_rename_go()
    test_pointers()

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
