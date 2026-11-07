#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

"""golden_check.py —— 把 κ 冻结成 golden 文件，之后只与 golden 比对（A–D Step 4）

**为什么要 golden**

现在的对拍基准是 `src/_baseline/cost_interp_old.py`（833 行只读副本）。
它的缺点是**它自己也占 833 行代码、而且会过期**——真正要保住的其实只有**结果**：
137 个「样例 × 函数」的 κ 字符串。

**golden 文件只保结果**：一个 JSON，改 κ 时**必须显式重新冻结**，
diff 里能看到"哪几个 κ 变了"，而不是"某个 833 行的副本变了"。

**用法**

    python tools/golden_check.py --freeze     # 重新冻结（改 κ 之后必须做，并在提交信息里写理由）
    python tools/golden_check.py              # 与 golden 比对（CI / 每次改动都跑）
"""
import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
SAMPLE = os.path.join(ROOT, "文档", "样例")
GOLDEN = os.path.join(ROOT, "tests", "golden", "kappa.json")

sys.path.insert(0, SRC)

PROGRAMS = ["calculator", "search", "sum", "stats", "merge",
            "paren", "count", "matmul", "lcs", "topk"]


def collect():
    """当前实现下，每个样例每个函数的 κ。"""
    from parser import parse_source
    from cost_interp import CostInterp

    out = {}
    for b in PROGRAMS:
        tu_p = os.path.join(SAMPLE, b + ".tu")
        ch_p = os.path.join(SAMPLE, b + ".eln")
        if not (os.path.exists(tu_p) and os.path.exists(ch_p)):
            continue
        tu = parse_source(io.open(tu_p, encoding="utf-8").read(), b + ".tu").module
        ch = parse_source(io.open(ch_p, encoding="utf-8").read(), b + ".eln").module
        # **不读 `.dispatch.yaml`**（2026-11-07）：那份表是测量产物、gitignore，
        # 换台机器就没有或不一样 —— golden 若依赖它，就等于"只有这台机器能复现"。
        # 这里固定 **max 口径**；分段口径由 `test_dispatch_*` 单独覆盖。
        ci = CostInterp(ch, tu, table_path=None)
        out[b] = {fn: ci.cost_of(fn).render() for fn in sorted(ci.fns)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--freeze", action="store_true", help="重新冻结 golden")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    cur = collect()
    total = sum(len(v) for v in cur.values())

    if args.freeze:
        os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
        payload = {"note": "κ 的 golden 基准。改 κ 后必须 --freeze 并在提交信息里写理由。",
                   "programs": cur}
        with open(GOLDEN, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"已冻结 {total} 个 κ → {os.path.relpath(GOLDEN, ROOT)}")
        return 0

    if not os.path.exists(GOLDEN):
        print(f"没有 golden 文件：{GOLDEN}\n先跑 --freeze", file=sys.stderr)
        return 2

    old = json.load(open(GOLDEN, encoding="utf-8"))["programs"]
    diffs, missing, added = [], [], []
    for b, fns in cur.items():
        for fn, kappa in fns.items():
            if fn not in old.get(b, {}):
                added.append(f"{b}.{fn}")
            elif old[b][fn] != kappa:
                diffs.append((f"{b}.{fn}", old[b][fn], kappa))
    for b, fns in old.items():
        for fn in fns:
            if fn not in cur.get(b, {}):
                missing.append(f"{b}.{fn}")

    if not (diffs or missing or added):
        print(f"golden 全等：{total} / {total} 例")
        return 0

    print(f"golden 有差异：{len(diffs)} 变 / {len(missing)} 缺 / {len(added)} 新增")
    for name, o, n in diffs:
        print(f"  ✗ {name}\n     旧: {o}\n     新: {n}")
    for m in missing[:10]:
        print(f"  - 缺: {m}")
    for a in added[:10]:
        print(f"  + 新: {a}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
