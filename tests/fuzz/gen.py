#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""gen.py —— 机械化生成能编译的随机程序（soundness 检测器用）。

## 四个**程序族**（v3）

| 族 | 打的是哪个历史漏算 | 形状 |
|---|---|---|
| `loop` | —（对照组） | `while` + 随机语句，v2 词汇表 |
| `minmax` | **`min`/`max`** | 循环体里 `max(i,2)` / `min(n,3)` |
| `hof` | **函数值传参** | `for_each(xs, visit)`，`visit` 有非平凡成本 |
| `rec` | **递归** | `f(n-1)+f(n-2)`，`.tu` 声明 `recursion depth <= 4;` |

`dispatch`（`?!` 分档表）暂缺：候选集要 `kernels` 语法 + `measure` 生成的
`.dispatch.yaml`，仓库里没有现成样例可抄，**没有硬编一个猜的语法**。

## 为什么生成源码而不是 AST

编译与否由**真实 parser** 判定；反例源码本身就能当最小复现（`shrink.py` 删行即可）。
"""
from __future__ import annotations

import random
from typing import List, Tuple

XS_LEN = 3
FAMILIES = ("loop", "minmax", "hof", "rec", "dispatch")

#: `loop` / `minmax`：两个形参
TU_TWO = """spec g(u64 x) -> u64 {
    effects {};
    target { time: { max: O(1) } };
}

spec f(const Vec<u64>& xs, u64 n) -> u64 {
    effects {};
    target { time: { max: O(n * n * xs.length()) }, allocation: { max: 0 } };
}
"""

ELN_HELPER = """func g(u64 x) -> u64 {
    return x + 1;
}
"""

#: `hof`：一个具名回调
TU_HOF = """spec visit(u64 x) -> u64 {
    effects {};
    target { time: { max: O(1) } };
}

spec f(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) } };
}
"""

ELN_VISIT = """func visit(u64 x) -> u64 {
    u64 s = x;
    s = s + 1;
    s = s + 2;
    s = s + 3;
    return s;
}
"""

#: `dispatch`：`?!` + choose + kernels（三件套；照 `样例/count` 的最小形态）
TU_DISPATCH = """spec f(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length() * xs.length()) } };

    choose pick(const Vec<u64>& xs) -> u64 {
        over { fast, slow };
    }
}
"""

ELN_DISPATCH = """func f(const Vec<u64>& xs) -> u64 {
    if (xs.length() == 0) {
        return 0;
    }
    return ?!pick(xs);
} kernels pick(const Vec<u64>& xs) -> u64 {
    fast {
        return xs.length();
    }
    slow {
        u64 s = 0;
        for (x in xs) {
            s = s + x;
        }
        return s;
    }
}
"""


#: `rec`：递归深度必须声明，否则 E-Recursion-Unbounded
TU_REC = """spec f(u64 n) -> u64 {
    effects {};
    recursion depth <= 4;
    target { time: { max: O(n * n * n) } };
}
"""

ELN_REC = """func f(u64 n) -> u64 {
    if (n < 2) {
        return n;
    }
    return f(n - 1) + f(n - 2);
}
"""


def _loop_stmt(rng: random.Random, depth: int) -> str:
    choices = ["add", "add2", "assign", "compound", "if", "call", "array",
               "cast", "div", "minmax"]
    if depth < 2:
        choices += ["while_small", "for_xs"]
    w = rng.choice(choices)
    if w == "add":
        return "        s = s + 1;"
    if w == "add2":
        return f"        s = s + {rng.randint(2, 5)};"
    if w == "assign":
        return "        s = s + i;"
    if w == "compound":
        return "        s += 3;"
    if w == "if":
        return ("        if (i % 2 == 0) {\n"
                "            s = s + 1;\n"
                "        } else {\n"
                "            s = s + 2;\n"
                "        }")
    if w == "call":
        return "        s = s + g(i);"
    if w == "array":
        return "        Vec<u64> a = [1, 2, 3];\n        s = s + a.length();"
    if w == "cast":
        return "        s = s + (i as u8);"
    if w == "div":
        return "        s = s + (i / 2);"
    if w == "minmax":
        return "        s = s + max(i, 2) + min(n, 3);"
    if w == "for_xs":
        inner = "\n".join(_loop_stmt(rng, depth + 1)
                          for _ in range(rng.randint(1, 2)))
        return ("        for (x in xs) {\n"
                f"{inner}\n"
                "            s = s + x;\n"
                "        }")
    k = rng.randint(1, 4)
    inner = "\n".join(_loop_stmt(rng, depth + 1)
                      for _ in range(rng.randint(1, 2)))
    return (f"        u64 j = 0;\n"
            f"        while (j < {k}) {{\n"
            f"{inner}\n"
            f"            j = j + 1;\n"
            f"        }}")


def gen_program(seed: int) -> Tuple[str, str, str]:
    """返回 (tu 源码, eln 源码, 族名)。"""
    rng = random.Random(seed)
    family = FAMILIES[seed % len(FAMILIES)]

    if family == "rec":
        return TU_REC, ELN_REC, "rec"

    if family == "dispatch":
        # 候选里有快有慢：κ 取 max（两候选上界），实测跑其中一个
        return TU_DISPATCH, ELN_DISPATCH, "dispatch"

    if family == "hof":
        # 回调成本越高、κ 越容易漏；`for_each` 的 κ 必须含 κ(visit)
        return TU_HOF, ELN_VISIT + "\n" + (
            "func f(const Vec<u64>& xs) -> u64 {\n"
            "    for_each(xs, visit);\n"
            "    return 0;\n}\n"), "hof"

    body = "\n".join(_loop_stmt(rng, 0) for _ in range(rng.randint(1, 2)))
    eln = (ELN_HELPER + "\n"
           "func f(const Vec<u64>& xs, u64 n) -> u64 {\n"
           "    u64 s = 0;\n"
           "    u64 i = 0;\n"
           "    while (i < n) {\n"
           f"{body}\n"
           "        i = i + 1;\n"
           "    }\n"
           "    return s;\n"
           "}\n")
    return TU_TWO, eln, family


#: 覆盖率词表
def used(eln: str) -> List[str]:
    out = []
    if "while" in eln:
        out.append("while")
    if "for (x in xs)" in eln:
        out.append("for_in")
    if "if (" in eln:
        out.append("if")
    if "}" in eln and "} else {" in eln:
        out.append("if_else")
    if "max(" in eln or "min(" in eln:
        out.append("minmax")
    if "g(i)" in eln:
        out.append("call")
    if "Vec<u64> a = [" in eln:
        out.append("array")
    if " as u8" in eln:
        out.append("cast")
    if " / 2)" in eln:
        out.append("div")
    if "+= " in eln:
        out.append("compound")
    if "for_each" in eln:
        out.append("hof")
    if "return f(n - 1)" in eln:
        out.append("rec")
    if "?!" in eln and "kernels" in eln:
        out.append("dispatch")
    return out


VOCAB = ["while", "for_in", "if", "if_else", "minmax", "call", "array",
         "cast", "div", "compound", "hof", "rec", "dispatch"]


def main() -> int:
    import sys

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    for s in range(n):
        tu, eln, fam = gen_program(s)
        print(f"===== seed {s} [{fam}] =====")
        print(eln)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
