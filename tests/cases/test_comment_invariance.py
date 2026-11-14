# -*- coding: utf-8 -*-
"""**注释不影响任何结论**——同一程序加/不加噪声注释，κ、ops、诊断必须完全一致。

## 为什么它是一条测试，而不是一次性实验

这个项目里已经出现过一次"文本被当成代码"的事故：`walk.py` 用正则读
**Python 源码**、猜"哪个方法处理了哪些 kind"，而把死分支改成注释解释之后，
检测器立刻报出两个**不存在**的 kind。

Elench 自己的实现**结构上**不会有这个问题——词法器把注释收进 `trivia`，
永远不进 token 流（`src/lexer.py`）。但"结构上不会"这句话，如果没有测试，
就只是一句话。这条测试把它钉住：

| 量 | 要求 |
|---|---|
| `κ` 的符号式 | 逐字相同 |
| `κ(n)` 的数值 | 相同 |
| 实测 ops | 相同 |
| 诊断集合（按 code 排序） | 相同 |

注释里故意写**看起来像代码**的东西：`if k == "call"`、`target { time: O(n^3) }`、
`return 1;`、未闭合的括号与引号。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "src"))

from checker import check_module                      # noqa: E402
from cost_domain.cost_interp import CostInterp        # noqa: E402
from cost_check import evaluate                       # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset  # noqa: E402
from parser import parse_source                       # noqa: E402

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


# ---------------------------------------------------------------- 样本

SUM_TU = """
spec total(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""
SUM_PLAIN = """
u64 total(const vector<u64>& xs) {
    u64 s = 0;
    u64 i = 0;
    while (i < xs.length()) {
        s = s + xs.at(i);
        i = i + 1;
    }
    return s;
}
"""
SUM_NOISY = """
// 文件头注释：if k == "call" { return 1; }
u64 total(const vector<u64>& xs) {
    u64 s = 0;                      // 累加器
    /// 文档注释：target { time: O(n^3) }，κ = 999
    u64 i = 0;
    while (i < xs.length()) {       // while (true) { }
        s = s + xs.at(i);           // s = s * 1000000;
        // 括号与引号：{ ( [ " ' —— 都不该影响词法
        i = i + 1;
    }
    // return 0;
    return s;
}
"""

#: 块注释版：同一程序 + `/* … */` 噪声（含**跨行**与"看起来像代码"的正文）
SUM_NOISY_BLOCK = """
/* 文件头注释：if k == "call" { return 1; }
   这一行在注释里；跨行不影响行号 */
u64 total(const vector<u64>& xs) {
    u64 s = 0;                      /* 累加器 */
    /// 文档注释：target { time: O(n^3) }，κ = 999
    u64 i = 0;
    while (i < xs.length()) {       /* while (true) { }
                                       跨两行的注释正文 */
        s = s + xs.at(i);           /* s = s * 1000000; */
        i = i + 1;
    }
    /* return 0; */
    return s;
}
"""

DISPATCH_TU = """
choose kernel over { by_scan, by_rev };
spec host(const vector<u64>& xs) -> u64 {
    effects {};
    benchmodel { n = xs.length() };
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec by_scan(const vector<u64>& xs) -> u64 { effects {}; target { time: { max: O(xs.length()) }, allocation: { max: 0 }, }; }
spec by_rev(const vector<u64>& xs) -> u64 { effects {}; target { time: { max: O(xs.length()) }, allocation: { max: 0 }, }; }
"""
DISPATCH_PLAIN = """
u64 host(const vector<u64>& xs) {
    if (xs.length() == 0) { return 0; }
    return ?!kernel(xs);
}
kernels kernel(const vector<u64>& xs) -> u64 {
    by_scan {
        u64 s = 0;
        for (auto x : xs) { s = s + x; }
        return s;
    }
    by_rev {
        u64 s = 0;
        u64 i = 0;
        while (i < xs.length()) {
            s = s + xs.at(xs.length() - 1 - i);
            i = i + 1;
        }
        return s;
    }
}
"""
DISPATCH_NOISY = """
// choose kernel over { 假的, 候选 };
u64 host(const vector<u64>& xs) {
    // return ?!kernel(xs);   —— 注释掉的旧写法
    if (xs.length() == 0) { return 0; }   // if (true) { }
    return ?!kernel(xs);
}
kernels kernel(const vector<u64>& xs) -> u64 {
    by_scan {
        u64 s = 0;
        for (auto x : xs) {      // for (auto y : xs) { s = s + 1; }
            s = s + x;
        }
        // return 0;
        return s;
    }
    by_rev {
        /// 文档：这里曾经是 return xs.length() * 2;
        u64 s = 0;
        u64 i = 0;
        while (i < xs.length()) {   // while (i > 0) { }
            s = s + xs.at(xs.length() - 1 - i);
            i = i + 1;
        }
        return s;
    }
}
"""

STRUCT_TU = """
spec build(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""
STRUCT_PLAIN = """
struct Pair { key: u64, value: u64 }
u64 build(const vector<u64>& xs) {
    Pair p = Pair{ key: xs.length(), value: 1 };
    return p.key;
}
"""
STRUCT_NOISY = """
struct Pair { key: u64, value: u64 }   // struct Fake { a: u64 }
u64 build(const vector<u64>& xs) {
    // Pair p = Pair{ key: 0, value: 0 };
    Pair p = Pair{ key: xs.length(), value: 1 };
    return p.key;
}
"""


def observe(eln: str, tu: str, fn: str, n: int = 8):
    """返回 (κ 符号式, κ(n), ops, 诊断码列表)。"""
    ch = parse_source(eln, "t.eln").module
    decl = parse_source(tu, "t.tu").module
    cost = CostInterp(ch, decl).cost_of(fn)
    k_val = evaluate(cost, {"xs": n})
    ops_enable(True)
    try:
        ops_reset()
        Interp(ch, decl).call(fn, [[1, 2, 3, 4, 5, 6, 7, 8]])
        o = ops_of()
    finally:
        ops_enable(False)
    diags = sorted(d.code for d in check_module(tu, eln, "t").diagnostics)
    return cost.render(), k_val, o, diags


def compare(name, plain, noisy, tu, fn):
    a = observe(plain, tu, fn)
    b = observe(noisy, tu, fn)
    check(a[0] == b[0], f"{name}：κ 符号式逐字相同", f"{a[0]!r}\n vs {b[0]!r}")
    check(a[1] == b[1], f"{name}：κ(n) 数值相同", f"{a[1]} vs {b[1]}")
    check(a[2] == b[2], f"{name}：实测 ops 相同", f"{a[2]} vs {b[2]}")
    check(a[3] == b[3], f"{name}：诊断集合相同", f"{a[3]} vs {b[3]}")


def test_cases():
    print("[1] 同一程序 + 噪声注释：κ / ops / 诊断 三项都必须一致")
    compare("while 循环", SUM_PLAIN, SUM_NOISY, SUM_TU, "total")
    compare("while 循环（块注释）", SUM_PLAIN, SUM_NOISY_BLOCK, SUM_TU, "total")
    compare("?! 派发", DISPATCH_PLAIN, DISPATCH_NOISY, DISPATCH_TU, "host")
    compare("结构体字面量", STRUCT_PLAIN, STRUCT_NOISY, STRUCT_TU, "build")


def test_slash_star():
    print("[2] `/* */` 块注释（2026-11-13 起支持）：**跳过**，且行号照常推进")
    src = "u64 f() {\n    /* 块注释 */\n    return 1;\n}\n"
    r = parse_source(src, "f.eln")
    errs = [d.code for d in r.diagnostics if d.code.startswith("E-")]
    check(not errs, "`/* */` 零 E- 诊断（是注释，不是错误）", str(errs))

    # **跨行**：块注释占几行，后面的 token 就报第几行（诊断靠行号）
    src_multi = ("u64 f() {\n"
                 "    /* 第一行\n"
                 "       第二行\n"
                 "       第三行 */\n"
                 "    u64 y = ;\n"
                 "}\n")
    r2 = parse_source(src_multi, "f.eln")
    line = next((d.line for d in r2.diagnostics if d.code.startswith("E-")), None)
    check(line == 5, "跨行块注释之后，错误仍报**真实行号** 5", str(line))

    # 未闭合：必须**大声失败**，不许静默吞到文件尾
    src3 = "u64 f() {\n    /* 没有闭合\n    return 1;\n}\n"
    r3 = parse_source(src3, "f.eln")
    codes3 = [d.code for d in r3.diagnostics]
    check("E-Lex-BlockCommentUnterminated" in codes3,
          "未闭合的 `/*` 报 E-Lex-BlockCommentUnterminated", str(codes3))

    # 反过来：`//` 里的 `/*` 是普通文本
    src2 = "u64 f() {\n    // 这里写 /* 也不会开始块注释\n    return 1;\n}\n"
    r4 = parse_source(src2, "f.eln")
    errs4 = [d.code for d in r4.diagnostics if d.code.startswith("E-")]
    check(not errs4, "`//` 里的 `/*` 只是文本", str(errs4))


def test_lexer_trivia():
    print("[3] 词法层证据：注释进 trivia，不进 token 流")
    from lexer import lex
    src = "u64 f() {\n    // 噪声\n    return 1;\n}\n"
    r = lex(src)
    kinds = [t.kind for t in r.tokens]
    check(all("COMMENT" not in str(k).upper() for k in kinds),
          "token 流里没有注释 token", str(kinds[:8]))
    check(any("噪声" in (getattr(t, "text", "") or "") for t in r.trivia),
          "注释被收进 trivia（保留给格式化/文档用）",
          str([getattr(t, "text", "") for t in r.trivia])[:120])


def main() -> int:
    test_cases()
    test_slash_star()
    test_lexer_trivia()
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
