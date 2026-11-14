#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_requires_var_var.py —— `requires lo <= hi;`（**变量 vs 变量**）真的被检查

## 为什么要有这个测试

`01_sorting.merge_sort` 的 κ 用**尺寸差** `hi - lo`，所以它的 target 必须配
`requires lo <= hi;`。修前 `requires` 只认「变量 vs **整数字面量**」，
这句在两个自调用点和 `main` 的调用点上各报一条 `E-Require-NotCheckable`
（"我不会检查"），于是 `check` 退出码 1——而 `cost` 判 ✓。**两个命令结论不一致，
这条契约就写不出来。**

现在**同一函数内两个数值项之间的比较**（`lo <= hi`、`a < b`、`x != y`、
`hi <= xs.size()`…）会被**真的检查**：在调用点用**实参之间的关系**证明；
证不出仍然报 `E-Require-Unproven`。本文件把这件事钉死。

## 钉哪六类（只验"能证"等于没验）

1. **能证**：`merge_sort` 形状（两次自调用 + 入口调用 `ms(xs, 0, xs.size())`）
   ⇒ 零诊断。`lo ≤ mid` 靠 `requires lo <= hi` + `mid = lo + (hi - lo)/2`
   的**整数除法**规则（只在本工具能证出 `hi - lo ≥ 0` 时才给界）。
2. **两种写法一致**：`lo <= hi` 与 `hi >= lo`（同一契约的两种拼写）诊断**逐条相同**。
3. **证不出必须报**：方向写反（`requires hi <= lo;`）⇒ 每个调用点
   `E-Require-Unproven`；`requires n_probe <= ks.size();` 没有任何守卫 ⇒ 必须报。
4. **不许拿过期事实证明**：`mid` 的定义提到 `t`，之后 `t` 被改写 ⇒
   `t ≤ mid` 不再可证（定义式失效），必须报。
5. **除法不放宽**：没有 `requires lo <= hi` 时 `hi - lo ≥ 0` 证不出，
   `lo ≤ mid` 也就证不出 ⇒ 必须报（不许把 `(hi - lo)/2` 当"显然非负"）。
6. **`E-Require-NotCheckable` 不增加**：新形状里一条也没有；
   真正认不出的（`xs[0] > 0`、`&&`）仍然是 `E-Require-NotCheckable`——
   "认得"是把形状纳入检查，不是把诊断关掉。
"""
import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
if SRC not in _sys.path:
    _sys.path.insert(0, SRC)

try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                        # noqa: BLE001
    pass

from checker import check_module                          # noqa: E402

FAILED = []
T = "target { time: { max: O(1) }, allocation: { max: 0 }, };\n"


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def diags(tu, ch):
    return list(check_module(tu, ch, "m").diagnostics)


def codes(tu, ch):
    return [d.code for d in diags(tu, ch)]


def msgs(tu, ch):
    return [d.message for d in diags(tu, ch)]


# ---------------------------------------------------------------- merge_sort 形状

#: 真语料 `demo/corpus/01_sorting.cpp` 的 `merge_sort` 骨架（**不动 demo/**，
#: 所以这里抄一份最小的等价形状；`tests/cases/test_kappa_merge_log.py` 也是这么做的）。
MS_ELN = """
void ms(vector<u64>& xs, u64 lo, u64 hi) {
    if (hi - lo < 2) { return; }
    u64 mid = lo + (hi - lo) / 2;
    ms(xs, lo, mid);
    ms(xs, mid, hi);
}
void sort_all(vector<u64>& xs) {
    ms(xs, 0, xs.size());
}
"""


def ms_tu(req="requires lo <= hi;", tail=""):
    # `recursion depth <= 32` 只为躲开 `E-Recursion-Unbounded`（与 requires 无关；
    # 真语料也是这么写的）。`tail` 放额外的 spec。
    return (f"spec ms(vector<u64>& xs, u64 lo, u64 hi) -> void {{ effects {{}}; "
            f"recursion depth <= 32; {req} {T}}}\n"
            f"spec sort_all(vector<u64>& xs) -> void {{ effects {{}}; {T}}}\n" + tail)


# ---------------------------------------------------------------- [1]

def test_merge_sort_shape():
    print("[1] 认得了，而且真的在**每个调用点**证出来")
    got = codes(ms_tu(), MS_ELN)
    check("merge_sort 形状（2 个自调用 + `ms(xs, 0, xs.size())`）→ 零诊断", got == [], str(got))
    check("一个 E-Require-NotCheckable 都没有", "E-Require-NotCheckable" not in got, str(got))
    check("一个 E-Require-Unproven 都没有", "E-Require-Unproven" not in got, str(got))

    # `mid` 若换成**不含区间长度**的写法（`mid = lo`），右半还是整个父区间：
    # `mid ≤ hi` 仍可证，但这不是重点——重点是"证明真是按式子算的，不是看名字猜的"。
    bad_mid = MS_ELN.replace("u64 mid = lo + (hi - lo) / 2;", "u64 mid = hi;")
    got2 = codes(ms_tu(), bad_mid)
    # （`mid = hi` 会让右半不再变小，成本侧另报 `W-Exp-Risk`——那是 κ 的事，
    #  与 requires 无关。这里只看 requires 的诊断。）
    check("`mid = hi` ⇒ `lo <= mid` 由 `lo <= hi` 直接证出、`mid <= hi` 由相等证出",
          not [c for c in got2 if c.startswith("E-Require")], str(got2))

    bad_mid2 = MS_ELN.replace("u64 mid = lo + (hi - lo) / 2;", "u64 mid = hi + 1;")
    got3 = codes(ms_tu(), bad_mid2)
    check("`mid = hi + 1` ⇒ `mid <= hi` 证不出 ⇒ 必须报 E-Require-Unproven",
          got3.count("E-Require-Unproven") == 1, str(got3))


# ---------------------------------------------------------------- [2]

def test_two_spellings():
    print("[2] 同一契约的两种写法（`lo <= hi` / `hi >= lo`）结论一致")
    a = codes(ms_tu("requires lo <= hi;"), MS_ELN)
    b = codes(ms_tu("requires hi >= lo;"), MS_ELN)
    check("两种写法都零诊断", a == [] and b == [], f"a={a} b={b}")

    wa = codes(ms_tu("requires hi <= lo;"), MS_ELN)
    wb = codes(ms_tu("requires lo >= hi;"), MS_ELN)
    check("写反的两种写法**诊断码逐条相同**", wa == wb, f"wa={wa} wb={wb}")
    check("写反 ⇒ 每个调用点各报一条 E-Require-Unproven（3 个调用点）",
          wa.count("E-Require-Unproven") == 3, str(wa))

    # 守卫也等价：`if (lo <= hi)` vs `if (hi >= lo)` 都能证出同一条 requires。
    guard_tu = (f"spec leaf(vector<u64>& xs, u64 lo, u64 hi) -> void {{ effects {{}}; "
                f"requires lo <= hi; {T}}}\n"
                f"spec g(vector<u64>& xs, u64 lo, u64 hi) -> void {{ effects {{}}; {T}}}\n")
    leaf = "void leaf(vector<u64>& xs, u64 lo, u64 hi) { }\n"
    g1 = ("void g(vector<u64>& xs, u64 lo, u64 hi) {\n"
          "    if (lo <= hi) { leaf(xs, lo, hi); }\n}\n")
    g2 = ("void g(vector<u64>& xs, u64 lo, u64 hi) {\n"
          "    if (hi >= lo) { leaf(xs, lo, hi); }\n}\n")
    check("`if (lo <= hi)` 守卫 → 通过", codes(guard_tu, g1 + leaf) == [],
          str(codes(guard_tu, g1 + leaf)))
    check("`if (hi >= lo)` 守卫 → 也通过（跨写法等价）", codes(guard_tu, g2 + leaf) == [],
          str(codes(guard_tu, g2 + leaf)))
    check("没有守卫 → 两种写法都报 E-Require-Unproven",
          "E-Require-Unproven" in codes(
              guard_tu, "void g(vector<u64>& xs, u64 lo, u64 hi) {\n"
                        "    leaf(xs, lo, hi);\n}\n" + leaf))


# ---------------------------------------------------------------- [3]

def test_must_report():
    print("[3] 证不出必须报（不许静默放行）")
    PROBE_ELN = """
void probe(vector<u64>& ks, u64 n_probe) { }
void caller(vector<u64>& ks, u64 n_probe) {
    probe(ks, n_probe);
}
"""
    tu = (f"spec probe(vector<u64>& ks, u64 n_probe) -> void {{ effects {{}}; "
          f"requires n_probe <= ks.size(); {T}}}\n"
          f"spec caller(vector<u64>& ks, u64 n_probe) -> void {{ effects {{}}; {T}}}\n")
    got = codes(tu, PROBE_ELN)
    check("`requires n_probe <= ks.size();`（无任何守卫）⇒ E-Require-Unproven",
          got.count("E-Require-Unproven") == 1, str(got))
    check("它是**认得出**的形状（不是 E-Require-NotCheckable）",
          "E-Require-NotCheckable" not in got, str(got))
    m = msgs(tu, PROBE_ELN)
    check("消息带出式子与实参", any("n_probe <= ks.size()" in x and "n_probe" in x for x in m), str(m))

    # 有守卫就能证：两种写法都行。
    g1 = ("void caller(vector<u64>& ks, u64 n_probe) {\n"
          "    if (n_probe <= ks.size()) { probe(ks, n_probe); }\n}\n")
    g2 = ("void caller(vector<u64>& ks, u64 n_probe) {\n"
          "    if (ks.size() >= n_probe) { probe(ks, n_probe); }\n}\n")
    check("`if (n_probe <= ks.size())` 守卫 ⇒ 通过",
          codes(tu, g1 + "void probe(vector<u64>& ks, u64 n_probe) { }\n") == [],
          str(codes(tu, g1 + "void probe(vector<u64>& ks, u64 n_probe) { }\n")))
    check("`if (ks.size() >= n_probe)` 守卫 ⇒ 也通过",
          codes(tu, g2 + "void probe(vector<u64>& ks, u64 n_probe) { }\n") == [],
          str(codes(tu, g2 + "void probe(vector<u64>& ks, u64 n_probe) { }\n")))

    # **不等于**：`<` 能推出 `!=`，没有事实就推不出。
    ne_tu = (f"spec neq(u64 a, u64 b) -> void {{ effects {{}}; requires a != b; {T}}}\n"
             f"spec g(u64 a, u64 b) -> void {{ effects {{}}; {T}}}\n")
    neq = "void neq(u64 a, u64 b) { }\n"
    check("`requires a != b;` + `if (a < b)` ⇒ 通过（`a < b` 蕴含 `a != b`）",
          codes(ne_tu, "void g(u64 a, u64 b) {\n"
                       "    if (a < b) { neq(a, b); }\n}\n" + neq) == [],
          str(codes(ne_tu, "void g(u64 a, u64 b) {\n"
                           "    if (a < b) { neq(a, b); }\n}\n" + neq)))
    check("`requires a != b;` 没有守卫 ⇒ E-Require-Unproven",
          "E-Require-Unproven" in codes(
              ne_tu, "void g(u64 a, u64 b) {\n    neq(a, b);\n}\n" + neq))
    check("`requires a != b;` + 反向守卫（`if (a >= b)`）⇒ 证不出，仍报",
          "E-Require-Unproven" in codes(
              ne_tu, "void g(u64 a, u64 b) {\n"
                     "    if (a >= b) { neq(a, b); }\n}\n" + neq))


# ---------------------------------------------------------------- [4]

def test_stale_facts():
    print("[4] 不许拿过期事实证明（赋值让定义式失效）")
    STALE = """
void leaf(vector<u64>& xs, u64 lo, u64 hi) { }
void caller(vector<u64>& xs, u64 lo, u64 hi) {
    u64 t = lo;
    u64 mid = t;
    t = t + 10;
    leaf(xs, t, mid);
}
"""
    tu_stale = (f"spec leaf(vector<u64>& xs, u64 lo, u64 hi) -> void {{ effects {{}}; "
                f"requires lo <= hi; {T}}}\n"
                f"spec caller(vector<u64>& xs, u64 lo, u64 hi) -> void {{ effects {{}}; {T}}}\n")
    got = codes(tu_stale, STALE)
    check("`mid = t` 之后 `t` 被改写 ⇒ `t <= mid` 证不出（定义式已失效）",
          got.count("E-Require-Unproven") == 1, str(got))

    FRESH = STALE.replace("    t = t + 10;\n", "")
    check("不改写 `t` ⇒ `t <= mid` 可证（`mid = t`）",
          codes(tu_stale, FRESH) == [], str(codes(tu_stale, FRESH)))


# ---------------------------------------------------------------- [5]

#: 调用点要证的是 `lo <= mid` / `mid <= hi`（`mid` 由调用方算出来）。
#: **`hi - lo ≥ 0` 证不出时，(hi-lo)/2 的界不给 ⇒ 两条都证不出。**
DIV_ELN = """
void leaf(vector<u64>& xs, u64 lo, u64 hi) { }
void caller(vector<u64>& xs, u64 lo, u64 hi) {
    u64 mid = lo + (hi - lo) / 2;
    leaf(xs, lo, mid);
    leaf(xs, mid, hi);
}
"""

DIV_TU_HEAD = (f"spec leaf(vector<u64>& xs, u64 lo, u64 hi) -> void {{ effects {{}}; "
               f"requires lo <= mid; requires mid <= hi; {T}}}\n")


def div_tu(caller_req=""):
    return (DIV_TU_HEAD +
            f"spec caller(vector<u64>& xs, u64 lo, u64 hi) -> void {{ effects {{}}; "
            f"{caller_req} {T}}}\n")


def test_division_needs_nonneg():
    print("[5] 除法只在能证 `被除数 ≥ 0` 时给界（不许当'显然非负'）")
    got = codes(div_tu(""), DIV_ELN)
    check("调用方没有 `requires lo <= hi` ⇒ `lo <= mid` / `mid <= hi` 都证不出，各报一条",
          got.count("E-Require-Unproven") == 2, str(got))

    got2 = codes(div_tu("requires lo <= hi;"), DIV_ELN)
    check("有 `requires lo <= hi` ⇒ 两条都证得出（`0 ≤ (hi-lo)/2 ≤ hi-lo`）",
          got2 == [], str(got2))

    # 早退守卫 `if (hi - lo < 2) { return; }` **不**做提前返回推理：
    # 它只给"真分支"事实，对后面的语句没有贡献——所以只有 requires 能救。
    eln_guard = """
void leaf(vector<u64>& xs, u64 lo, u64 hi) { }
void caller(vector<u64>& xs, u64 lo, u64 hi) {
    if (hi - lo < 2) { return; }
    u64 mid = lo + (hi - lo) / 2;
    leaf(xs, lo, mid);
}
"""
    check("只有早退守卫、没有 `requires lo <= hi` ⇒ 仍然证不出（不做事后否定推理）",
          codes(div_tu(""), eln_guard).count("E-Require-Unproven") == 1,
          str(codes(div_tu(""), eln_guard)))


# ---------------------------------------------------------------- [6]

def test_notcheckable_not_increased():
    print("[6] E-Require-NotCheckable 不增加：认得的不报，认不出的照旧报")
    for req, ok in (("requires lo <= hi;", True),
                    ("requires hi >= lo;", True),
                    ("requires a != b;", True),
                    ("requires xs.size() >= hi;", True),
                    ("requires lo + 1 <= hi;", True),
                    ("requires (lo) <= (hi);", True)):
        got = codes(ms_tu(req), MS_ELN)
        check(f"{req} → 没有 E-Require-NotCheckable",
              "E-Require-NotCheckable" not in got, str(got))

    # 真认不出的：`xs[0]` 不是数值项、`&&` 不拆。
    idx = (f"spec leaf(vector<u64>& xs) -> void {{ effects {{}}; "
           f"requires xs[0] > 0; {T}}}\n"
           f"spec g(vector<u64>& xs) -> void {{ effects {{}}; {T}}}\n")
    leaf1 = "void leaf(vector<u64>& xs) { }\n"
    check("`requires xs[0] > 0;` → 仍报 E-Require-NotCheckable",
          "E-Require-NotCheckable" in codes(
              idx, "void g(vector<u64>& xs) {\n    leaf(xs);\n}\n" + leaf1))
    conj = (f"spec leaf(vector<u64>& xs, u64 lo, u64 hi) -> void {{ effects {{}}; "
            f"requires lo <= hi && lo > 0; {T}}}\n"
            f"spec g(vector<u64>& xs, u64 lo, u64 hi) -> void {{ effects {{}}; {T}}}\n")
    leaf2 = "void leaf(vector<u64>& xs, u64 lo, u64 hi) { }\n"
    check("`requires lo <= hi && lo > 0;` → 仍报 E-Require-NotCheckable（拆成两条就都能查）",
          "E-Require-NotCheckable" in codes(
              conj, "void g(vector<u64>& xs, u64 lo, u64 hi) {\n"
                    "    leaf(xs, lo, hi);\n}\n" + leaf2))

    # 「乘法两边都含变量」不是数值项：认不出（**不是**"证不出"）。
    mul = (f"spec leaf(u64 a, u64 b) -> void {{ effects {{}}; "
           f"requires a * b <= 10; {T}}}\n"
           f"spec g(u64 a, u64 b) -> void {{ effects {{}}; {T}}}\n")
    leaf3 = "void leaf(u64 a, u64 b) { }\n"
    check("`requires a * b <= 10;` → E-Require-NotCheckable（非线性不在表里）",
          "E-Require-NotCheckable" in codes(
              mul, "void g(u64 a, u64 b) {\n    leaf(a, b);\n}\n" + leaf3))


def main() -> int:
    test_merge_sort_shape()
    test_two_spellings()
    test_must_report()
    test_stale_facts()
    test_division_needs_nonneg()
    test_notcheckable_not_increased()
    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    _sys.exit(main())
