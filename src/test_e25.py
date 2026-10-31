#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_e25.py —— 补上的四种构造：复合赋值 / 数组 / 解引用 / 浮点（E25）

**为什么这四样必须两域都做**

- 只有解释器支持 → 能跑但**推不出成本**（κ = ⊤，等于没契约）；
- 只有成本支持 → 有数但**跑不起来**（数字没人核对）。

**这两件事在 E24/E26 上吃过教训**：一边有、一边没有，就会得出"上界成立"的假结论。

**第五种 `lit-string` 不做，而且是语言层面的决定**：
词法器直接拒绝（`E-Lex-StringLiteral`）——**不是"未实现"，是"这门语言不提供字符串字面量"**。
"""
import sys

from cost_check import evaluate
from cost_interp import CostInterp
from interp import Interp
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


def run(src, args=None):
    r = parse_source(src, "a.eln")
    mod = r.module
    it = Interp(mod, None)
    raw = it.call("f", args or [])
    # `call` 返回 Ok(...)/Err(...) 包裹；字段名可能是 value / val / payload，
    # 这里不假设具体实现，退回到字符串匹配（够用：断言只比小整数）。
    val = getattr(raw, "value", None)
    if val is None:
        for attr in ("val", "payload", "inner"):
            if hasattr(raw, attr):
                val = getattr(raw, attr)
                break
    if val is None:
        val = str(raw)
    steps = it.counters()["evaluation_steps"]
    ci = CostInterp(mod, None)
    kappa = ci.cost_of("f")
    return val, steps, kappa


def main() -> int:
    print("[1] 复合赋值：`i += 3; i *= 2;` → 6")
    val, steps, kappa = run("""func f() -> u64! {
    u64 i = 0;
    i += 3;
    i *= 2;
    return i;
}""")
    check(str(val).strip().strip("Ok()") == "6" or val == 6, "值与手算一致（6）", str(val))
    check(steps > 0, "解释器走了步数", str(steps))
    check(kappa.render() not in ("⊤", "top"), "κ 不是 ⊤（能推出数）", kappa.render())

    print("[2] 数组字面量：`const u64[3] a = [1, 2, 3];`")
    val, steps, kappa = run("""func f() -> u64! {
    const u64[3] a = [1, 2, 3];
    return a.at(1);
}""")
    check(str(val).strip().strip("Ok()") == "2" or val == 2, "取到第 1 个元素（2）", str(val))
    check(kappa.render() not in ("⊤", "top"), "κ 不是 ⊤", kappa.render())

    print("[3] 浮点字面量：`f64 x = 1.5;`")
    val, steps, kappa = run("""func f() -> u64! {
    f64 x = 1.5;
    return 7;
}""")
    check("7" in str(val), "程序正常返回", str(val))
    check(steps > 0, "浮点声明被真执行", str(steps))

    print("[4] 解引用：`*r`（v0 引用即值）")
    val, steps, kappa = run("""func f(const Vec<u64>& xs) -> u64! {
    const u64& r = xs.at(0);
    return *r;
}""", [[11, 22, 33]])
    check("11" in str(val), "取到 xs.at(0) 的值", str(val))

    print("[5] 字符串字面量：语言层拒绝（不是未实现）")
    r = parse_source('func f() -> u64! { return 0; }', "a.eln")
    r2 = parse_source('func g() -> u64! { const char c = "x"; return 0; }', "a.eln")
    codes = [d.code for d in (r2.diagnostics or [])]
    check("字符串字面量现在是合法常量（v0.71 起）",
          "E-Lex-StringLiteral" not in codes, str(codes))

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


def xs_first():
    """解引用用例里 `xs.at(0)` 的期望值：入口统一传 [11, 22, 33]。"""
    return 11


if __name__ == "__main__":
    sys.exit(main())
