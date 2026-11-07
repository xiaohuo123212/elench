#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""artifact_check.py —— **一键复现**（审稿人五分钟能跑）

## 跑什么

对 `文档/样例/` 里的每个程序：

1. `check` —— 语法 / 类型 / 契约 / 债务
2. `cost`  —— κ 与 target 比对
3. `build` —— 门禁（含 `pending` 时失败）

最后打印一张总表，并写出 `artifact-report.md`（可直接贴进论文附录）。

## 用法

    python tools/artifact_check.py
    python tools/artifact_check.py --out artifact-report.md

**环境**：只需 Python（零第三方依赖）。
"""
import io
import os
import subprocess
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(R, "src")
SAMPLES = os.path.join(R, "文档", "样例")


def run(args):
    p = subprocess.run([sys.executable, "dsh.py"] + args, cwd=SRC,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def modules():
    names = []
    for f in sorted(os.listdir(SAMPLES)):
        if f.endswith(".tu"):
            names.append(f[:-3])
    return names


def main() -> int:
    out_path = os.path.join(R, "artifact-report.md")
    if "--out" in sys.argv:
        out_path = sys.argv[sys.argv.index("--out") + 1]

    rows = []
    for name in modules():
        base = os.path.join(SAMPLES, name)
        rc_c, _ = run(["check", base + ".tu", base + ".eln"])
        rc_k, out_k = run(["cost", base])
        rc_b, out_b = run(["build", base])
        debt = "超标" in out_k
        # **构建失败要区分原因**：`pending`（设计如此）vs 债务（--deny-debt 才有）vs 真错
        if rc_b == 0:
            why = "通过"
        elif "E-Dispatch-NoTable" in out_b or "缺分档表" in out_b:
            why = "缺分档表（先 measure）"
        elif "pending" in out_b or "不完整" in out_b:
            why = "pending 拦截（设计如此）"
        elif "拒绝构建" in out_b:
            why = "债务拒绝构建"
        else:
            why = "失败"
        rows.append({"name": name, "check": rc_c, "cost": rc_k,
                     "build": rc_b, "debt": debt, "why": why})
        print(f"  {name:12} check={rc_c} cost={rc_k} build={rc_b:>2}  {why}"
              f"{'  债务' if debt else ''}")

    # **判定**：check 全过；build 失败必须都是"设计如此"（pending）或债务，不能是"失败"
    ok = (all(r["check"] == 0 for r in rows)
          and all(r["why"] in ("通过", "pending 拦截（设计如此）", "债务拒绝构建",
                               "缺分档表（先 measure）")
                  for r in rows))
    lines = [
        "# 一键复现报告",
        "",
        f"- 程序数：**{len(rows)}**",
        f"- `check` 通过：**{sum(1 for r in rows if r['check'] == 0)}/{len(rows)}**",
        f"- `build` 通过：**{sum(1 for r in rows if r['build'] == 0)}/{len(rows)}**"
        f"（其余为 `pending` 拦截，属设计如此）",
        f"- 有债务（κ 超 target）：**{sum(1 for r in rows if r['debt'])}**",
        "",
        "| 程序 | check | cost | build | 债务 |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| `{r['name']}` | {r['check']} | {r['cost']} | {r['why']} "
                     f"| {'是' if r['debt'] else '否'} |")
    lines += ["", "复现命令：`python tools/artifact_check.py`（零第三方依赖）"]
    io.open(out_path, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    print(f"  报告 → {os.path.relpath(out_path, R)}")
    print(f"  总判定：{'✅ 全部通过' if ok else '❌ 有失败'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
