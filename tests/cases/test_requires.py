#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_requires.py —— `requires` 的**调用点验证**（v0.71 第一步；2026-11-12 补 `.size()`）

## 为什么要有这个测试

`requires` 如果只在运行期断言，它和函数体里的 `if` 没本质区别。
**只有"编译器在调用点能证明"，`requires` 才值钱**——这个测试锁住这件事。

## 2026-11-12：**现行拼写 `size()` 必须和旧拼写 `length()` 一样被检查**

事故：`fact_of` 只匹配 `chain[1] == "length"`，于是
`requires xs.size() > 0;`（`.size()` 才是 v0.73 之后的现行拼写）被**静默丢掉**——
契约看起来被检查了，其实没有（假保护）。

本测试现在锁三件事：

1. **两种拼写行为一致**：都检查，能证都过、证不出都报 `E-Require-Unproven`；
   旧拼写额外报 `W-Builtin-Deprecated`（兼容但提醒改名）。
2. **认不出的形状必须报 `E-Require-NotCheckable`**（不许静默）：
   `xs[0] > 0`、`&&` 复合条件、非比较谓词。
   **2026-11-13 起**「两个数值项比较」（`hi <= xs.size()`、`lo <= hi`）不再属于
   认不出——它**认得并且真的检查**，证不出时报 `E-Require-Unproven`
   （见 `test_requires_var_var.py`；这里钉住"仍然不放行"）。
3. **不许漏掉调用点**：`if` 的条件认不出时，体内/`else` 里的调用**也要**检查
   （以前认不出就整段跳过）。
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


from checker import check_module          # noqa: E402

FAILED = []
T = "target { time: { max: O(1) }, allocation: { max: 0 }, };\n"

#: 同一份契约的两种拼写：`size`（现行）与 `length`（v0.73 弃用，仍兼容）。
HEAD_TU_SIZE = (f"spec head(const vector<u64>& xs) -> u64 {{ effects {{}}; "
                f"requires xs.size() > 0; {T}}}\n")
HEAD_TU_LEN = (f"spec head(const vector<u64>& xs) -> u64 {{ effects {{}}; "
               f"requires xs.length() > 0; {T}}}\n")
HEAD_CH = "u64 head(const vector<u64>& xs) { return xs[0]; }\n"

SORTED_TU = (f"spec find(const vector<u64>& xs, u64 key) -> u64 {{ effects {{}}; "
             f"requires is_sorted(xs); {T}}}\n")
SORTED_CH = "u64 find(const vector<u64>& xs, u64 key) { return xs[0]; }\n"

SCALAR_TU = (f"spec ith(const vector<u64>& xs, u64 k) -> u64 {{ effects {{}}; "
             f"requires k > 0; {T}}}\n")
SCALAR_CH = "u64 ith(const vector<u64>& xs, u64 k) { return xs[0]; }\n"


def codes(tu, ch):
    return [d.code for d in check_module(tu, ch, "m").diagnostics]


def msgs(tu, ch):
    return [d.message for d in check_module(tu, ch, "m").diagnostics]


def with_g(tu, ch, callee, gsig="vector<u64>& xs"):
    # **`g` 必须是可变借用**（2026-11-08）："先 sort 再 find" 意味着 g 会改 xs，
    # 而 `sort(xs)` 是**原地**的——标成 `const vector<u64>&` 就是让契约撒谎
    # （现在 `check` 会报 `E-Const-Mutate`）。requires 的语义不受影响。
    g = (f"spec g({gsig}) -> u64 {{ effects {{}}; {T}}}\n")
    return codes(tu + g, "u64 g(" + gsig + ") {\n" + ch + "\n}\n" + callee)


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def main() -> int:
    print("=== [1] 两种拼写：一样检查、一样结论 ===")
    # 守卫内调用 → 通过（`size` 版不许有任何诊断；`length` 版只多一条弃用警告）
    check("size 契约 + size 守卫 → 通过，零诊断",
          with_g(HEAD_TU_SIZE, "    if (xs.size() > 0) {\n        return head(xs);\n    }\n"
                  "    return 0;", HEAD_CH) == [])
    check("length 契约 + length 守卫 → 通过（只多 W-Builtin-Deprecated）",
          with_g(HEAD_TU_LEN, "    if (xs.length() > 0) {\n        return head(xs);\n    }\n"
                  "    return 0;", HEAD_CH) == ["W-Builtin-Deprecated"],
          str(with_g(HEAD_TU_LEN, "    if (xs.length() > 0) {\n        return head(xs);\n"
                     "    }\n    return 0;", HEAD_CH)))
    check("length 契约 + size 守卫 → 也通过（**跨拼写等价**）",
          "E-Require-Unproven" not in with_g(
              HEAD_TU_LEN,
              "    if (xs.size() > 0) {\n        return head(xs);\n    }\n"
              "    return 0;", HEAD_CH))
    check("size 契约 + length 守卫 → 也通过（**跨拼写等价**）",
          "E-Require-Unproven" not in with_g(
              HEAD_TU_SIZE,
              "    if (xs.length() > 0) {\n        return head(xs);\n    }\n"
              "    return 0;", HEAD_CH))

    # 没有守卫 → 两种拼写**都**必须报 E-Require-Unproven（旧拼写多一条警告）
    size_no = with_g(HEAD_TU_SIZE, "    return head(xs);", HEAD_CH)
    len_no = with_g(HEAD_TU_LEN, "    return head(xs);", HEAD_CH)
    check("size 契约、没有守卫 → E-Require-Unproven（不再静默！）",
          "E-Require-Unproven" in size_no, str(size_no))
    check("length 契约、没有守卫 → E-Require-Unproven",
          "E-Require-Unproven" in len_no, str(len_no))
    check("两种拼写的结论一致（只差一条弃用警告）",
          set(size_no) == set(len_no) - {"W-Builtin-Deprecated"},
          f"size={size_no} length={len_no}")
    check("size 版消息按**原拼写**渲染 `xs.size() > 0`",
          any("xs.size() > 0" in m for m in
              msgs(HEAD_TU_SIZE + (f"spec g(vector<u64>& xs) -> u64 {{ effects {{}}; {T}}}\n"),
                   "u64 g(vector<u64>& xs) {\n    return head(xs);\n}\n" + HEAD_CH)))
    check("守卫方向反了（size: == 0）→ 仍报错",
          "E-Require-Unproven" in with_g(HEAD_TU_SIZE,
                                         "    if (xs.size() == 0) {\n        return head(xs);\n"
                                         "    }\n    return 0;", HEAD_CH))

    print("=== [2] 同一谓词的等价写法：反向 / 括号 / != 0 ===")
    REV_TU = (f"spec head(const vector<u64>& xs) -> u64 {{ effects {{}}; "
              f"requires 0 < xs.size(); {T}}}\n")
    check("反向书写 `0 < xs.size()` + 反向守卫 → 通过",
          with_g(REV_TU, "    if (0 < xs.size()) {\n        return head(xs);\n    }\n"
                  "    return 0;", HEAD_CH) == [])
    check("反向书写、没有守卫 → E-Require-Unproven",
          "E-Require-Unproven" in with_g(REV_TU, "    return head(xs);", HEAD_CH))
    PAREN_TU = (f"spec head(const vector<u64>& xs) -> u64 {{ effects {{}}; "
                f"requires (xs.size() > 0); {T}}}\n")
    check("括号 `requires (xs.size() > 0)` + 括号守卫 → 通过",
          with_g(PAREN_TU, "    if ((xs.size() > 0)) {\n        return head(xs);\n    }\n"
                  "    return 0;", HEAD_CH) == [])
    NEQ_TU = (f"spec head(const vector<u64>& xs) -> u64 {{ effects {{}}; "
              f"requires xs.size() != 0; {T}}}\n")
    check("`xs.size() != 0` 由 `size() > 0` 守卫证明",
          with_g(NEQ_TU, "    if (xs.size() > 0) {\n        return head(xs);\n    }\n"
                  "    return 0;", HEAD_CH) == [])

    print("=== [3] is_sorted：sort 的后置条件 ===")
    check("先 sort 再 find → 通过",
          with_g(SORTED_TU, "    sort(xs);\n    return find(xs, 1);", SORTED_CH) == [])
    check("没 sort → 报错",
          "E-Require-Unproven" in with_g(SORTED_TU, "    return find(xs, 1);", SORTED_CH))

    print("=== [4] 标量守卫 ===")
    check("k > 0 守卫 → 通过",
          with_g(SCALAR_TU, "    if (k > 0) {\n        return ith(xs, k);\n    }\n    return 0;",
                 SCALAR_CH) == [])
    check("反向 `0 < k` 守卫 → 通过",
          with_g(SCALAR_TU, "    if (0 < k) {\n        return ith(xs, k);\n    }\n    return 0;",
                 SCALAR_CH) == [])
    check("没有守卫 → 仍报 E-Require-Unproven（不许放松）",
          "E-Require-Unproven" in with_g(SCALAR_TU, "    return ith(xs, k);", SCALAR_CH))
    SCALAR_NEQ = (f"spec ith(const vector<u64>& xs, u64 k) -> u64 {{ effects {{}}; "
                  f"requires k != 0; {T}}}\n")
    # **2026-11-13 变了**：以前标量的 `!=` 一律保守拒绝（`_provable` 只给长度开
    # `!= 0` 的口子）。现在判定是整数线性算术：`if (k > 0)` 已经给出 `k ≥ 1`，
    # 于是 `k ≠ 0` 是**真能证出来**的——比"保守拒绝"更准，而且方向没松
    # （没有 `k > 0` 这个守卫时仍然证不出，见下一条）。
    check("标量 `k != 0` 由 `k > 0` 守卫证出（线性算术，不是猜）",
          "E-Require-Unproven" not in with_g(
              SCALAR_NEQ, "    if (k > 0) {\n        return ith(xs, k);\n    }\n    return 0;",
              SCALAR_CH))
    check("标量 `k != 0` 没有任何守卫 → 仍报 E-Require-Unproven（不静默、也不假装能证）",
          "E-Require-Unproven" in with_g(SCALAR_NEQ, "    return ith(xs, k);", SCALAR_CH))
    # `k != 0` 与 `k == 0` 方向相反：`if (k == 0)` 的**真分支**证不出 `k != 0`。
    check("标量 `k != 0`、守卫方向相反（`if (k == 0)`）→ 仍报",
          "E-Require-Unproven" in with_g(
              SCALAR_NEQ, "    if (k == 0) {\n        return ith(xs, k);\n    }\n    return 0;",
              SCALAR_CH))

    print("=== [5] 认不出的前提 MUST 报 E-Require-NotCheckable（不许静默） ===")
    IDX_TU = (f"spec head(const vector<u64>& xs) -> u64 {{ effects {{}}; "
              f"requires xs[0] > 0; {T}}}\n")
    idx_codes = with_g(IDX_TU, "    return head(xs);", HEAD_CH)
    check("`requires xs[0] > 0` → E-Require-NotCheckable",
          "E-Require-NotCheckable" in idx_codes, str(idx_codes))
    check("认不出时**不**报 E-Require-Unproven（换成说清「不会检查」）",
          "E-Require-Unproven" not in idx_codes, str(idx_codes))
    idx_msgs = msgs(IDX_TU + (f"spec g(vector<u64>& xs) -> u64 {{ effects {{}}; {T}}}\n"),
                    "u64 g(vector<u64>& xs) {\n    return head(xs);\n}\n" + HEAD_CH)
    check("诊断原文包含式子、原因与改法",
          any("xs[0] > 0" in m and "认不出来" in m and "没有被检查" in m
              and "xs.size()" in m for m in idx_msgs), str(idx_msgs))
    check("就算有守卫，认不出的前提**也不会**被当成已检查",
          "E-Require-NotCheckable" in with_g(
              IDX_TU,
              "    if (xs.size() > 0) {\n        return head(xs);\n    }\n    return 0;",
              HEAD_CH))
    RHS_TU = (f"spec head(const vector<u64>& xs, u64 hi) -> u64 {{ effects {{}}; "
              f"requires hi <= xs.size(); {T}}}\n")
    # **2026-11-13**：`hi <= xs.size()`（变量 vs 长度）以前报「认不出」；
    # 现在它是**认得出**的形状（两个数值项比较），只是没有事实时证不出。
    # 这里钉住"仍然不放行"：换成 `E-Require-Unproven`，不是 `NotCheckable`、更不是通过。
    rhs_codes = codes(
        RHS_TU + (f"spec g(vector<u64>& xs, u64 hi) -> u64 {{ effects {{}}; {T}}}\n"),
        "u64 g(vector<u64>& xs, u64 hi) {\n    return head(xs, hi);\n}\n"
        + HEAD_CH)
    check("`hi <= xs.size()` 现在**认得**：没有守卫 ⇒ E-Require-Unproven（不再说认不出）",
          "E-Require-Unproven" in rhs_codes and "E-Require-NotCheckable" not in rhs_codes,
          str(rhs_codes))
    check("`hi <= xs.size()`：实参就是长度 ⇒ 可证（`head(xs, xs.size())`）",
          "E-Require-Unproven" not in codes(
              RHS_TU + (f"spec g(vector<u64>& xs) -> u64 {{ effects {{}}; {T}}}\n"),
              "u64 g(vector<u64>& xs) {\n    return head(xs, xs.size());\n}\n"
              + "u64 head(const vector<u64>& xs, u64 hi) { return xs[0]; }\n"))
    CONJ_TU = (f"spec head(const vector<u64>& xs, u64 k) -> u64 {{ effects {{}}; "
               f"requires xs.size() > 0 && k > 0; {T}}}\n")
    check("复合条件 `&&` → E-Require-NotCheckable（提示拆成多条）",
          "E-Require-NotCheckable" in codes(
              CONJ_TU + (f"spec g(vector<u64>& xs, u64 k) -> u64 {{ effects {{}}; {T}}}\n"),
              "u64 g(vector<u64>& xs, u64 k) {\n    return head(xs, k);\n}\n"
              + HEAD_CH))
    CALL_TU = (f"spec head(const vector<u64>& xs) -> u64 {{ effects {{}}; "
               f"requires contains(xs, 1); {T}}}\n")
    check("非比较谓词（`contains(xs, 1)`）→ E-Require-NotCheckable",
          "E-Require-NotCheckable" in with_g(CALL_TU, "    return head(xs);", HEAD_CH))

    print("=== [6] 不许漏掉调用点：if 条件认不出时，体内/else 也要检查 ===")
    check("`if (flag)` 认不出 → 体内的调用**照样**检查（以前整段跳过）",
          "E-Require-Unproven" in with_g(HEAD_TU_SIZE,
                                         "    if (k_flag) {\n        return head(xs);\n    }\n"
                                         "    return 0;", HEAD_CH,
                                         gsig="vector<u64>& xs, u64 k_flag"))
    check("调用出现在 `if` 条件里 → 也要检查",
          "E-Require-Unproven" in with_g(HEAD_TU_SIZE,
                                         "    if (head(xs) > 0) {\n        return 1;\n    }\n"
                                         "    return 0;", HEAD_CH))
    check("`else` 分支用否定事实证明（`if (size == 0) … else …`）",
          with_g(HEAD_TU_SIZE,
                 "    if (xs.size() == 0) {\n        return 0;\n    } else {\n"
                 "        return head(xs);\n    }", HEAD_CH) == [])
    check("`else` 分支方向不对 → 仍报 E-Require-Unproven",
          "E-Require-Unproven" in with_g(HEAD_TU_SIZE,
                                         "    if (xs.size() > 0) {\n        return 0;\n"
                                         "    } else {\n        return head(xs);\n    }",
                                         HEAD_CH))

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
