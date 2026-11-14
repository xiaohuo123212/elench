# -*- coding: utf-8 -*-
"""`typedef struct { … } X;`（2026-11-13）—— 真实 C 库里 3/3 都撞到的第二个缺口。

## 为什么它值得一条专门的测试

inih.c 里**只有第 40 行一处** `typedef struct { … } ini_parse_string_ctx;`，
却挡掉了后面 **295 行**（整个文件 335 行）。支持之前那一段只报一条
`E-Keyword-Unsupported` + `E-Parse-SkippedBody`——**看起来错误很少**，其实
整段没被解析（正是 `recover_item` 最危险的假绿灯）。

## 支持什么（三种写法，产物都是**已有节点**）

| 写法 | 产物 |
|---|---|
| `typedef struct { … } X;` | `struct X { … }` |
| `typedef struct X { … } X;` | `struct X { … }`（标签名 = 类型名） |
| `typedef struct Tag { … } X;` | `struct X { … }`（`Tag` 只是 C 的标签拼写，本语言没有标签命名空间） |

**纪律**：剥掉糖之后必须还是同一个程序——所以三种写法的 AST 必须与手写
`struct X { … }` **同形**（去掉行列号后逐节点相等），κ / run 因此逐字节相同。

## 不支持什么（**明确诊断**，不是裸 parse 错）

函数指针 / 基本类型别名 / 数组 / 多声明符 / 只有标签没有体 / 写在函数体里。
每条都给 `E-Keyword-Unsupported` + 改法，且**一条声明只报一条**（不级联）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import compat                                     # noqa: E402
from checker import check_module                  # noqa: E402
from interp import Interp                         # noqa: E402
from parser import parse_source                   # noqa: E402

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


def parse(src, compat_on=False, name="t.eln"):
    """按 `compat_on` 解析；**总是**把开关恢复成 False（不给后续测试留状态）。"""
    compat.set_enabled(bool(compat_on))
    try:
        return parse_source(src, name)
    finally:
        compat.set_enabled(False)


def e_codes(res):
    return [d.code for d in res.diagnostics if d.code.startswith("E-")]


def shape(node):
    """去掉 line/col 的 AST 形状——比的是"剥糖之后是不是同一棵树"。"""
    if node is None:
        return None
    return (node.kind, node.name, node.value,
            tuple(shape(c) for c in node.children))


#: 三种写法 + 同名的手写 struct，**语义必须完全相同**。
FORMS = (
    "anon",
    "tag_is_name",
    "tag_differs",
    "plain",
)


def source_of(kind: str) -> str:
    body = ("u64 f() {\n"
            "    Point p = Point { x: 3, y: 4 };\n"
            "    u64 a = p.x;\n"
            "    u64 b = p.y;\n"
            "    return a + b;\n"
            "}\n")
    head = {
        "anon":         "typedef struct { u64 x; u64 y; } Point;\n",
        "tag_is_name":  "typedef struct Point { u64 x; u64 y; } Point;\n",
        "tag_differs":  "typedef struct Point_ { u64 x; u64 y; } Point;\n",
        "plain":        "struct Point { x: u64, y: u64, }\n",
    }[kind]
    return head + body


# ---------------------------------------------------------------- ① 三种写法

def test_three_forms():
    print("[1] 三种写法都认，且**零 E- 诊断**（`--compat` 开 / 关都一样）")
    for kind in ("anon", "tag_is_name", "tag_differs"):
        src = source_of(kind)
        for compat_on in (False, True):
            res = parse(src, compat_on)
            check(not e_codes(res),
                  f"`{kind}`（compat={compat_on}）零 E- 诊断", e_codes(res))


def test_ast_same_as_plain_struct():
    print("[2] 剥糖之后是**同一棵树**：三种写法的 AST == 手写 `struct Point`")
    want = shape(parse(source_of("plain"), False).module)
    for kind in ("anon", "tag_is_name", "tag_differs"):
        got = shape(parse(source_of(kind), False).module)
        check(got == want, f"`{kind}` 的 AST 与手写 struct 同形",
              "形状不同" if got != want else "")


# ---------------------------------------------------------------- ② check / run

def test_check_and_run():
    print("[3] `X v;` 之后能 **check**、能 **run**（三种写法合一个程序）")
    structs = ("typedef struct { u64 x; u64 y; } Point;\n"
               "typedef struct Tag { u64 v; } Aliased;\n"
               "typedef struct Same { u64 z; } Same;\n")
    main = ("""
u64 main() {
    Point p = Point { x: 3, y: 4 };
    Aliased a = Aliased { v: 5 };
    Same s = Same { z: 6 };
    return p.x + p.y + a.v + s.z;
}
""")
    # `--compat`：struct 与实现在同一个 .eln（就是 .cpp 改名的样子）
    res = check_with("", structs + main, "td", True, has_tu=False)
    errs = [(d.code, d.line) for d in res.diagnostics if d.severity == "error"]
    check(not errs, "check 通过（--compat：单个 .eln）", errs)
    # 默认模式：声明在 `.tu`、实现在 `.eln`（本语言的分工）
    res2 = check_with(structs, main, "td", False)
    errs2 = [(d.code, d.line) for d in res2.diagnostics if d.severity == "error"]
    check(not errs2, "check 通过（默认模式：struct 在 .tu、main 在 .eln）", errs2)
    # run：两种分工各跑一次
    ch = parse(structs + main, True).module
    check(Interp(ch, None).call("main", []) == 18,
          "run（--compat 单文件）返回 18（3+4+5+6）",
          Interp(ch, None).call("main", []))
    tu = parse(structs, True, "td.tu").module
    eln = parse(main, False, "td.eln").module
    check(Interp(eln, tu).call("main", []) == 18,
          "run（.tu + .eln）返回 18", Interp(eln, tu).call("main", []))


def test_alias_target_also_usable():
    print("[4] `typedef struct Tag { … } X;`：**只有 `X` 是类型名**（没有标签命名空间）")
    src = ("typedef struct Node_ { u64 v; } Node;\n"
           "u64 f() {\n"
           "    Node a = Node { v: 1 };\n"
           "    Node b = Node { v: 2 };\n"
           "    return a.v + b.v;\n"
           "}\n")
    res = parse(src, False)
    check(not e_codes(res), "`X v;` / `X { … }` 都能用", e_codes(res))
    ch = parse(src, False).module
    check(Interp(ch, None).call("f", []) == 3, "返回 1+2=3", Interp(ch, None).call("f", []))

    # 结构体**真名**是 typedef 名（`Tag` 只消费、不登记）
    names = []

    def walk(n):
        if n is None:
            return
        if n.kind == "struct":
            names.append(n.name)
        for c in n.children:
            walk(c)

    walk(ch)
    check(names == ["Node"], "结构体节点名是 `Node`（不是 `Node_`）", names)
    # `Node_ v;` 不是合法类型（C 里也只有 `struct Node_`，而本语言没有那个写法）
    bad = parse("typedef struct Node_ { u64 v; } Node;\n"
                "u64 g() { Node_ x; return 0; }\n", False)
    check("E-Parse-Expected" not in e_codes(bad),
          "`Node_ v;` 不落成裸 parse 错（它就是「未知类型名」，交给类型检查）",
          e_codes(bad))


# ---------------------------------------------------------------- ③ 明确诊断

def test_unsupported_forms():
    print("[5] 其余 typedef 形式：**明确诊断**（一条声明一条，带改法）")
    # (源码, 这是什么, `--compat` 下是否**已支持**)
    cases = [
        ("typedef u64 M;", "基本类型别名", True),
        ("typedef unsigned long long ULL;", "宽类型别名", True),
        ("typedef u64 (*Fn)(u64);", "函数指针", False),
        ("typedef u64 Arr[4];", "数组别名", False),
        ("typedef struct { u64 x; } A, *PA;", "多声明符", False),
        ("typedef struct X X;", "只有标签没有体（前向声明）", False),
        ("typedef struct X* PX;", "只有标签的指针", False),
        ("typedef enum { A } E;", "枚举别名", False),
    ]
    for src, what, ok_in_compat in cases:
        for compat_on in (False, True):
            res = parse(src + "\nu64 f() { return 0; }\n", compat_on)
            codes = [d.code for d in res.diagnostics]
            e = [c for c in codes if c.startswith("E-")]
            msgs = "; ".join(d.message for d in res.diagnostics)
            if compat_on and ok_in_compat:
                # 既有兼容层行为：`typedef Y X;` 走别名表，**不是**新缺口
                check(not e, f"{what}（compat=True）走别名表，零 E-", codes)
                continue
            check(e == ["E-Keyword-Unsupported"],
                  f"{what}（compat={compat_on}）**只报一条** E-Keyword-Unsupported",
                  codes)
            check("改法" in msgs,
                  f"{what} 的诊断带「改法」", msgs[:120])
            check(not any(c.startswith("E-Parse-") for c in codes),
                  f"{what} 不落成裸 parse 错（无 E-Parse-*）", codes)


def test_statement_level():
    print("[6] 写在函数体里的 `typedef struct`：说清**只能放模块顶层**")
    src = ("u64 f() {\n"
           "    typedef struct { u64 x; } L;\n"
           "    return 0;\n"
           "}\n")
    for compat_on in (False, True):
        res = parse(src, compat_on)
        codes = [d.code for d in res.diagnostics]
        msgs = "; ".join(d.message for d in res.diagnostics)
        check(codes == ["E-Keyword-Unsupported"],
              f"函数体内 typedef struct（compat={compat_on}）只报一条", codes)
        check("模块顶层" in msgs, "诊断指出应放到模块顶层", msgs[:160])


def test_no_cascade():
    print("[7] 一条坏声明**不级联**（不报跳过整段 / 不在同一位置报两条）")
    src = "typedef struct X X;\nu64 f() { return 0; }\n"
    res = parse(src, False)
    codes = [d.code for d in res.diagnostics]
    check(not any(c == "E-Parse-SkippedBody" for c in codes),
          "不报 E-Parse-SkippedBody（没有整段被跳过）", codes)
    check(len(codes) == len(set(codes)) == 1,
          "同一声明只留一条诊断", codes)


def check_with(tu_src, ai_src, module, compat_on, **kw):
    """在指定的 `compat` 状态下调 `check_module`（结束后恢复 False）。"""
    compat.set_enabled(bool(compat_on))
    try:
        return check_module(tu_src, ai_src, module, **kw)
    finally:
        compat.set_enabled(False)


def main() -> int:
    test_three_forms()
    test_ast_same_as_plain_struct()
    test_check_and_run()
    test_alias_target_also_usable()
    test_unsupported_forms()
    test_statement_level()
    test_no_cascade()
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
