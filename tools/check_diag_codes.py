#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""check_diag_codes.py —— 诊断码双向核对（T-07）

## 它回答什么

> **文档里写的诊断码，代码里真的抛吗？代码里抛的，文档里有吗？**

## 为什么要有 baseline

第一次跑就有 **139 处不一致**（66 只在文档、73 只在代码）。
一次性全修不现实，但**也不能就这样算了**——所以：

- `--update`：把当前差异写进 `tools/diag_codes_baseline.json`（**一次性的、要有人负责**）；
- 默认：只在**出现新差异**时 exit 1。

**这条的目的不是"立刻一致"，是"不再悄悄变差"。**

## 已知的假阳性

文档里的 `E-1`…`E-9` 是**编号**不是错误码（表格行号），会被误当成码。
baseline 里会有它们，别去"修"。

用法：

    python tools/check_diag_codes.py            # 有新差异就 exit 1
    python tools/check_diag_codes.py --update   # 把当前状态记为基线
    python tools/check_diag_codes.py --report   # 打印全部差异（不判失败）
"""
import io
import json
import os
import re
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(R, "tools", "diag_codes_baseline.json")
DOCS = ("文档/语法清单.md", "文档/设计/目前设计/AI语言设计规格.md")

CODE_RE = re.compile(r'"(E-[A-Za-z0-9-]+)"')
DOC_RE = re.compile(r"\b(E-[A-Za-z0-9-]+)\b")


def code_codes():
    out = set()
    for f in sorted(os.listdir(os.path.join(R, "src"))):
        if f.endswith(".py"):
            out |= set(CODE_RE.findall(
                io.open(os.path.join(R, "src", f), encoding="utf-8").read()))
    return out


def doc_codes():
    out = set()
    for rel in DOCS:
        p = os.path.join(R, rel)
        if os.path.exists(p):
            out |= set(DOC_RE.findall(io.open(p, encoding="utf-8").read()))
    return out


def diff():
    c, d = code_codes(), doc_codes()
    return {"only_in_docs": sorted(d - c), "only_in_code": sorted(c - d)}


def main(argv) -> int:
    cur = diff()
    if "--update" in argv:
        io.open(BASE, "w", encoding="utf-8", newline="\n").write(
            json.dumps(cur, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        print(f"  已写基线：{BASE}")
        print(f"   只在文档 {len(cur['only_in_docs'])}；只在代码 {len(cur['only_in_code'])}")
        return 0

    if "--report" in argv or not os.path.exists(BASE):
        print(f"  只在文档里 {len(cur['only_in_docs'])}：{', '.join(cur['only_in_docs'][:12])}…")
        print(f"  只在代码里 {len(cur['only_in_code'])}：{', '.join(cur['only_in_code'][:12])}…")
        if not os.path.exists(BASE):
            print("  （还没有基线：先跑 --update）")
            return 0
        return 0

    base = json.load(io.open(BASE, encoding="utf-8"))
    new_doc = sorted(set(cur["only_in_docs"]) - set(base.get("only_in_docs", [])))
    new_code = sorted(set(cur["only_in_code"]) - set(base.get("only_in_code", [])))
    fixed = (len(base.get("only_in_docs", [])) - len(cur["only_in_docs"])
             + len(base.get("only_in_code", [])) - len(cur["only_in_code"]))
    if new_doc or new_code:
        print("✗ 出现**新的**诊断码差异：")
        if new_doc:
            print(f"    只在文档里：{', '.join(new_doc)}")
        if new_code:
            print(f"    只在代码里：{', '.join(new_code)}")
        print("  修法：补文档 / 补实现 / 确认是有意为之则跑 --update")
        return 1
    print(f"  ✓ 无新差异（基线内：文档多 {len(cur['only_in_docs'])}、代码多 {len(cur['only_in_code'])}"
          + (f"；比基线少了 {fixed}" if fixed else "") + "）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
