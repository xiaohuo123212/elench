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

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from lexer import Kind
from parser import Node, parse_source

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "..", "文档", "设计", "目前设计", "样例")


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
    check(len(items(tu.module, "spec")) == 10, "tu: 10 个 spec")

    ai = parse_source(load("calculator.eln"), "calculator.eln")
    check(ai.ok, "calculator.eln 无诊断",
          f"实际 {len(ai.diagnostics)} 条: "
          + "; ".join(f"{d.code}@{d.line}:{d.col}" for d in ai.diagnostics[:3]))
    check(len(items(ai.module, "fn")) == 10, "ai: 10 个 fn")
    check(len(items(ai.module, "const")) == 0, "ai: 零声明（F-4）")
    check(len(items(ai.module, "use")) == 0, "ai: 零 use（F-8）")


# ---------------------------------------------------------------- 2. spec 子句

def test_benchmodel():
    print("[?] benchmodel 三段式（v0.69）")
    src = """
spec f(const Vec<u64>& xs, u64 key) -> u64 {
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
    r2 = parse_source("spec g(const Vec<u64>& xs) -> u64 { effects {}; "
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
    check("Vec" in names, "tokenize 参数是 Vec<u8>", f"实际 {names[:4]}")


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


# ---------------------------------------------------------------- 6.5 `::`

def test_pathsep():
    print("[6.5] 类型命名空间 `::`（v0.69）")
    src = """enum Token { Number(i64), Plus }
enum Err { Bad(u64) }
func f(Token t) -> i64 {
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
    src2 = "func g() -> Vec<u64> {\n    return FixedVec<u64, 8>::make();\n}"
    r2 = parse_source(src2, "s.eln")
    check(not r2.diagnostics, "`FixedVec<T,N>::make()` 仍能解析",
          "; ".join(d.code for d in r2.diagnostics))


# ---------------------------------------------------------------- 6.6 scale 的 in

def test_scale_in():
    print("[6.6] `scale` 用 `in`（v0.69）")
    ok = """spec f(const Vec<u64>& xs) -> u64 {
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

spec average(const Vec<u8>& scores) -> u64! {
    effects   {alloc};
    benchmodel {
        data  { scores = make_u8s(); }
        scale { scores in range(1, 1024, *2); }
    };
    target {
        time      : { max: O(scores.length()) },
        allocation: { max: 0 },
    };
    choose sum_kernel(const Vec<u8>& scores) -> u64 {
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
pub func average(const Vec<u8>& scores) -> u64! {
    const u64 n = scores.length();
    if (n == 0) { return Err(CalcError.Empty); }
    const u64 total = ?!sum_kernel(scores);
    return total / n;
} kernels sum_kernel(const Vec<u8>& scores) -> u64 {
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
    test_choose_kernel_dispatch()
    test_errors()
    test_determinism()

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
