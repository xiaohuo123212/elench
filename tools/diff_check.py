#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""tools/diff_check.py —— 新旧实现**逐位对拍**（主批 B 的唯一验收手段）

## 为什么需要它

主批 B 要把两套遍历（`interp.py` / `cost_interp.py`）合成一套 `walk.py`
+ 两个值域。**合并之后，"行为没变"这句话凭什么成立？**

| 手段 | 能证明什么 |
|---|---|
| 跑测试 | 「这 N 条断言还过」——覆盖不到的地方变了也发现不了 |
| **对拍** | **「同一个输入，新旧输出逐字相同」**——这是可数、可复现的标准 |

**对拍的价值不在"全过"，在于"不过的时候你知道差在哪"**：
它同时打印 `旧: X` 与 `新: Y`，于是分歧点直接摆在眼前，
而不是「结果不对，不知道错在哪」。

## 比什么

1. **具体值**：`Interp.call()` 的返回值（`repr`）
2. **计数**：`counters()` 的四个数（调用/方法/分配/迭代）
3. **成本式**：`CostInterp.cost_of(fn).render()`
4. **目标式**：`CostInterp.target_cost(fn).render()`

## 怎么跑

    cd src
    $env:PYTHONIOENCODING="utf-8"
    python ..\\tools\\diff_check.py            # 全量对拍
    python ..\\tools\\diff_check.py --sample merge   # 只对拍一套

**退出码 0 = 全等；1 = 有差异（差异逐条打印）。**

## 注意

**基线是 `src/_baseline/` 里的只读副本**（L0 冻结）。
主批 D 收口时才删。**在那之前，谁都不许改基线文件**——
改了就等于把「行为没变」的基准挪走了。
"""

import importlib.util
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src")
SAMPLE = os.path.join(HERE, "..", "文档", "设计", "目前设计", "样例")

sys.path.insert(0, os.path.abspath(SRC))


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


#: 每个样例的「入口函数 + 固定实参」。
#:
#: **不用 benchmodel 造参**：规模会随 seed 变，对拍需要**确定的**输入。
#: 结构体实参用 `mod.StructVal`——两个解释器各有自己的 `StructVal` 类，
#: 必须用各自模块里的那个，否则 `isinstance` 判定会失败。
def args_of(sample, mod):
    if sample == "calculator":
        return [("evaluate", [list(b"1+2*3")]),
                ("evaluate", [list(b"(1+2)*(3+4)")])]
    if sample == "search":
        return [("binary_search", [[1, 3, 5, 7, 9, 11], 7]),
                ("binary_search", [[1, 3, 5, 7, 9, 11], 4])]
    if sample == "sum":
        return [("average", [[10, 20, 30]]),
                ("average", [[7]])]
    if sample == "stats":
        return [("summarize", [[1, 2, 3, 4]]),
                ("summarize", [[4, 1, 3, 2, 5]])]
    if sample == "merge":
        def ivs(pairs):
            return [mod.StructVal("Interval", {"start": s, "finish": f})
                    for s, f in pairs]
        return [("merge", [ivs([(1, 3), (2, 6), (8, 10)])]),
                ("merge", [ivs([(1, 4), (4, 5)])])]
    if sample == "paren":
        return [("paren", [list(b"(())")]),
                ("paren", [list(b"(()")])]
    if sample == "count":
        return [("count", [list(b"aabbb")]),
                ("count", [list(b"1231")])]
    if sample == "matmul":
        return [("matmul", [[1, 2, 3, 4], [5, 6, 7, 8], 2])]
    if sample == "lcs":
        return [("lcs", [list(b"ABCBDAB"), list(b"BDCABA")]),
                ("lcs", [list(b"ABC"), list(b"ACB")])]
    if sample == "topk":
        return [("topk", [[5, 1, 9, 3], 2]),
                ("topk", [[5, 1, 9, 3], 1])]
    return []


SAMPLES = ["calculator", "search", "sum", "stats", "merge",
           "paren", "count", "matmul", "lcs", "topk"]


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


class Diff:
    def __init__(self):
        self.same = 0
        self.diffs = []

    def eq(self, label, old, new):
        if old == new:
            self.same += 1
        else:
            self.diffs.append((label, old, new))

    def report(self):
        total = self.same + len(self.diffs)
        if not self.diffs:
            print(f"  对拍全等：{self.same} / {total} 例")
            return 0
        print(f"  对拍有差异：{len(self.diffs)} / {total} 例")
        for label, old, new in self.diffs[:40]:
            print(f"    ✗ {label}")
            print(f"        旧: {old}")
            print(f"        新: {new}")
        if len(self.diffs) > 40:
            print(f"    … 还有 {len(self.diffs) - 40} 条")
        return 1


def main(argv):
    only = None
    for a in argv[1:]:
        if a.startswith("--sample="):
            only = a.split("=", 1)[1]

    from parser import parse_source

    old_interp_mod = load_module(os.path.join(SRC, "_baseline", "interp_old.py"),
                                 "interp_old")
    old_cost_mod = load_module(os.path.join(SRC, "_baseline", "cost_interp_old.py"),
                               "cost_interp_old")
    from cost_interp import CostInterp
    from interp import Interp

    d = Diff()
    targets = [only] if only else SAMPLES
    for sample in targets:
        tu_src = read(os.path.join(SAMPLE, sample + ".tu"))
        ch_src = read(os.path.join(SAMPLE, sample + ".eln"))

        # ---- 成本式：所有函数都能比（不需要实参）
        tu = parse_source(tu_src, sample + ".tu").module
        ch = parse_source(ch_src, sample + ".eln").module
        old_ci = old_cost_mod.CostInterp(ch, tu)
        new_ci = CostInterp(ch, tu)
        for fn_name in sorted(old_ci.fns):
            if fn_name not in new_ci.fns:
                d.eq(f"{sample}.{fn_name} 存在性", True, False)
                continue
            o = old_ci.cost_of(fn_name).render()
            n = new_ci.cost_of(fn_name).render()
            d.eq(f"{sample}.{fn_name} κ", o, n)
            ot = old_ci.target_cost(fn_name)
            nt = new_ci.target_cost(fn_name)
            d.eq(f"{sample}.{fn_name} target",
                 ot.render() if ot else None, nt.render() if nt else None)

        # ---- 具体值：入口函数 × 固定输入
        # **实参要按模块各造一份**：结构体值的类定义在两个解释器模块里是
        # 不同的类，跨模块传会 `isinstance` 失败（这本身就是对拍的价值之一：
        # 它逼你想清"同一个值"在两个实现里到底是什么）。
        for fn_name, _ in args_of(sample, old_interp_mod):
            old_it = old_interp_mod.Interp(ch, tu)
            new_it = Interp(ch, tu)
            args_old = dict(args_of(sample, old_interp_mod))[fn_name]
            args_new = dict(args_of(sample, Interp.__module__ and sys.modules[
                Interp.__module__]))[fn_name]
            try:
                ov = repr(old_it.call(fn_name, args_old))
            except Exception as e:                  # noqa: BLE001
                ov = f"<{type(e).__name__}: {e}>"
            try:
                nv = repr(new_it.call(fn_name, args_new))
            except Exception as e:                  # noqa: BLE001
                nv = f"<{type(e).__name__}: {e}>"
            d.eq(f"{sample}.{fn_name}({len(args_old)} 实参) 返回值", ov, nv)

            oc = old_it.counters()
            nc = new_it.counters()
            for key in ("function_calls_total", "method_calls",
                        "allocations", "iterations"):
                d.eq(f"{sample}.{fn_name} {key}", oc.get(key), nc.get(key))

    print()
    return d.report()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
