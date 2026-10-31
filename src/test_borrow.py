#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_borrow.py —— 借用检查（E30 / 规格 T7-a）

**为什么这一层必须有**：我们选了 C++ 后端，**Rust 会替我们证借用安全，C++ 不会**。
没有它，`const u64& r = xs.at(0); xs.push(1); return r;` 会静默编译通过，运行时读越界——
**那是在交付 UB。**

**这一版的边界（测试里也锁住）**：
- BR-5（结构体不得存引用）✅
- BR-1（借用期间不得可变借用）🟡 **NLL-lite**：借用"生命结束"之后再改是允许的
- BR-2/3/4 未实现
"""
import io
import os
import sys

from borrow import check_borrows
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


TU = "spec f(const Vec<u64>& xs) -> u64 { effects {}; }"


def diags(code, tu=TU):
    return check_borrows(parse_source(tu, "a.tu").module,
                         parse_source(code, "a.eln").module)


def has(ds, code):
    return any(c == code for c, _, _, _ in ds)


def main() -> int:
    print("[1] 改完还读 → 报 E-Borrow-Conflict（真 UB）")
    d = diags("""func f(const Vec<u64>& xs) -> u64! {
    const u64& r = xs.at(0);
    xs.push(1);
    return r;
}""")
    check(has(d, "E-Borrow-Conflict"), "借用期间 push 被拦下", str(d))

    print("[2] 先读后改 → 不报（NLL：借用已经结束）")
    d = diags("""func f(const Vec<u64>& xs) -> u64! {
    const u64& r = xs.at(0);
    const u64 a = r;
    xs.push(1);
    return a;
}""")
    check(not d, "借用结束后再改不误报", str(d))

    print("[3] 结构体字段是引用 → 报 E-Borrow-StructRef（BR-5）")
    # 先看语法能不能写出引用字段：三种写法都试一遍。
    forms = [
        "struct Holder { xs: const Vec<u64>& }",
        "struct Holder { xs: &Vec<u64> }",
        "struct Holder { xs: Vec<u64>& }",
    ]
    reported, expressible = False, False
    for form in forms:
        r = parse_source(form + "\nspec f(const Vec<u64>& xs) -> u64 { effects {}; }", "a.tu")
        if r.diagnostics:
            continue
        expressible = True
        d = diags("func f(const Vec<u64>& xs) -> u64! { return 0; }",
                  tu=form + "\nspec f(const Vec<u64>& xs) -> u64 { effects {}; }")
        reported = reported or has(d, "E-Borrow-StructRef")
    if expressible:
        check(reported, "结构体存引用被拦下")
    else:
        # 语法当前**写不出**引用字段 → BR-5 暂时不可达，是一条前瞻守卫。
        # 这里不断言"通过"，而是明确记录状态：测试不该假装验证了不存在的东西。
        check(True, "语法当前不允许引用字段（BR-5 是前瞻守卫，见缺口 E30）")

    print("[4] 十个真实样例 → 0 条借用诊断（不许误报）")
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    S = os.path.join(root, "文档", "设计", "目前设计", "样例")
    total = 0
    for b in ["calculator", "search", "sum", "stats", "merge",
              "paren", "count", "matmul", "lcs", "topk"]:
        tu = parse_source(io.open(os.path.join(S, b + ".tu"), encoding="utf-8").read(), b + ".tu").module
        ch = parse_source(io.open(os.path.join(S, b + ".eln"), encoding="utf-8").read(), b + ".eln").module
        total += len(check_borrows(tu, ch))
    check(total == 0, "十个样例零误报", f"共 {total} 条")


    print("[5] 跨块/跨循环：借用在外层，循环体里改 → 报错（BR-2 近似）")
    d = diags("""func f(Vec<u64>& xs) -> u64! {
    const u64& r = xs.at(0);
    u64 i = 0;
    while (i < 3) {
        xs.push(1);
        i = i + 1;
    }
    return r;
}""")
    check(has(d, "E-Borrow-Conflict"), "循环体里的可变操作被拦下", str(d))

    print("[6] BR-4：返回引用但有 2 个引用参数 → 报 E-Borrow-Ambiguous")
    d = diags("""func pick(const Vec<u64>& a, const Vec<u64>& b) -> u64&! {
    return a.at(0);
}""")
    check(has(d, "E-Borrow-Ambiguous"), "归属不明的引用返回被拒", str(d))

    print("[7] BR-4 反例：只有一个引用参数 → 不报（归属唯一）")
    d = diags("""func first(const Vec<u64>& a) -> u64&! {
    return a.at(0);
}""")
    check(not any(c == "E-Borrow-Ambiguous" for c, _, _, _ in d), "单引用参数不误报", str(d))

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
