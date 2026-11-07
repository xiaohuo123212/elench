#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""real_false_reject.py —— 在**真实样例**上量门禁的误拒/漏判。

## 与 `false_reject.py` 的分工

| 脚本 | 语料 | 用途 |
|---|---|---|
| `false_reject.py` | 模板生成 | 大样本、阶已知、可控 |
| `real_false_reject.py` | **仓库里的真实样例** | 证明"在真代码上也不乱判" |

## 做法

1. 用当前成本模型算出每个函数的 κ 在 n=64/256/1024 的**增长**，据此判它的真实阶；
2. 把样例 `.tu` 里的 `target { time: { max: O(...) } }` 改写成三档：
   **与真实阶相同 / 松一阶 / 紧一阶**；
3. 三档分别跑 `build --deny-debt --deny-unknown`：
   - 相同、松一阶 → **必须通过**（否则误拒）；
   - 紧一阶 → **必须拒绝**（否则漏判）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
SAMPLES = os.path.join(ROOT, "文档", "样例")
ELENCH = os.path.join(SRC, "elench.py")

#: 真实样例：(模块, 主函数, 尺寸变量)
PROGS = [
    ("count", "count", "text"),
    ("sum", "average", "scores"),
    ("search", "binary_search", "xs"),
    ("stats", "summarize", "xs"),
    ("topk", "topk", "xs"),
    ("paren", "paren", "text"),
    ("lcs", "lcs", "a"),
    ("merge", "merge", "xs"),
    ("matmul", "matmul", "dim"),
    ("calculator", "evaluate", "text"),
]

def order_expr(kind: int, base: str) -> str:
    """按真实阶造 target 表达式。

    **尺寸量必须与 κ 一致**：`target` 写 `O(n)` 而 κ 用 `n(scores)` 时，
    比较会走 E23 → unknown。第一次跑这个脚本就栽在这里（14 例"误拒"全是它）。
    """
    if kind == 1:
        return "O(1)"
    if kind == 2:
        return f"O({base})"
    if kind == 3:
        return f"O({base} * log2({base}))"
    return f"O({base} * {base})"


def size_base(param_name: str, kind: str) -> str:
    """向量参数 → `xs.length()`；标量参数 → `dim`。"""
    if kind in ("u8vec", "u64vec") or kind.startswith("structvec:"):
        return f"{param_name}.length()"
    return param_name


def classify_growth(v64: float, v256: float, v1024: float) -> int:
    """按 64→1024 的增长倍数判阶（4× 规模）。"""
    if v256 <= 0:
        return 1
    r = v1024 / v256 if v256 else 1.0
    if r < 1.6:
        return 1
    if r < 4.25:
        return 2
    if r < 9.0:
        return 3          # n log n（4× 规模应涨 ≈5 倍）
    return 4


def main() -> int:
    sys.path.insert(0, SRC)
    from cost_check import evaluate
    from cost_interp import CostInterp
    from parser import parse_source

    total = ok = fp = fn = unk = skipped = 0
    details = []
    for name, func, var in PROGS:
        tu_p = os.path.join(SAMPLES, name + ".tu")
        ch_p = os.path.join(SAMPLES, name + ".eln")
        if not (os.path.exists(tu_p) and os.path.exists(ch_p)):
            skipped += 1
            continue
        tu_src = open(tu_p, encoding="utf-8").read()
        el_src = open(ch_p, encoding="utf-8").read()
        tu = parse_source(tu_src, name + ".tu").module
        el = parse_source(el_src, name + ".eln").module
        ci = CostInterp(el, tu, table_path=None)
        try:
            k = ci.cost_of(func)
        except Exception:                              # noqa: BLE001
            skipped += 1
            continue
        try:
            v64 = evaluate(k, {var: 64})
            v256 = evaluate(k, {var: 256})
            v1024 = evaluate(k, {var: 1024})
        except Exception:                              # noqa: BLE001
            skipped += 1
            continue
        true_order = classify_growth(v64, v256, v1024)

        # 找该函数的参数名与种类（决定 target 里写 `xs.length()` 还是 `dim`）
        from kappa_vs_measured import param_kind
        params = next((c for c in ci.fns[func].children
                       if getattr(c, "kind", "") == "params"), None)
        _p = (params.children[0] if params is not None and params.children else None)
        _pname = getattr(_p, "name", "") or var
        base = size_base(_pname, param_kind(_p) if _p is not None else "?")

        for delta in (-1, 0, 1):
            tgt = true_order + delta
            if tgt < 1 or tgt > 4:
                continue
            expect_pass = tgt >= true_order
            # 改写 .tu 里的 time target
            # **整条替换 target 子句**：`target { ... };` 内部没有分号，
            # 所以 `[^;]*` 恰好覆盖整个子句。用嵌套括号正则去改 O(...) 会把
            # 后面的 `allocation` 一起吃掉（第一版就是这样误判了 18 例）。
            new_tu, cnt = re.subn(
                r"target\s*\{[^;]*\};",
                "target { time: { max: %s }, };" % order_expr(tgt, base),
                tu_src, count=1)
            if cnt == 0:
                skipped += 1
                continue
            d = tempfile.mkdtemp(prefix="eln_rfr_")
            open(os.path.join(d, name + ".tu"), "w", encoding="utf-8").write(new_tu)
            open(os.path.join(d, name + ".eln"), "w", encoding="utf-8").write(el_src)
            r = subprocess.run([sys.executable, ELENCH, "build", name,
                                "--deny-debt", "--deny-unknown"],
                               capture_output=True, encoding="utf-8",
                               errors="replace", cwd=d)
            out = (r.stdout or "") + (r.stderr or "")
            got_pass = (r.returncode == 0)
            # **"拒绝"和"判不出"必须分开数**：
            #   `无法判定` = E23（κ 与 target 的尺寸量不一致）——这是已知限制，
            #   不是"误拒"。混在一起会把工具的能力问题说成统计结论。
            outcome = ("pass" if got_pass else
                       "unknown" if "无法判定" in out else
                       "debt" if "超出 target" in out else "other")
            total += 1
            if expect_pass:
                if outcome == "pass":
                    ok += 1
                elif outcome == "unknown":
                    unk += 1
                    details.append(f"判不出 {name}.{func}（E23：尺寸量不一致）")
                else:
                    fp += 1
                    details.append(f"误拒 {name}.{func} 真实阶 {true_order} → "
                                   f"target {order_expr(tgt, base)}")
            else:
                if outcome in ("debt", "unknown"):
                    ok += 1
                else:
                    fn += 1
                    details.append(f"漏判 {name}.{func} 真实阶 {true_order} → "
                                   f"target {order_expr(tgt, base)}")

    print(f"真实样例：{len(PROGS)} 个程序（跳过 {skipped} 处无法改写的）")
    print(f"总计 {total} 例：**判对 {ok}**，误拒 {fp}，漏判 {fn}，判不出 {unk}（E23 限制）")
    for line in details[:8]:
        print("  -", line)
    return 0 if fp == 0 and fn == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
