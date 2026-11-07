#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""tightness_table.py —— 紧度表（T-05 的「表」这一半）

## 它做什么

调 `elench export --full --format=csv`，把结果转成 **Markdown 表**与 **CSV**。

## 为什么不用另写一套测量

`export` 已经给出 `函数,规模,κ,实测,相对(κ÷实测),承诺上限` —— **别再写第二个测量器**，
那会立刻出现两套口径（这正是这个项目一直在防的事）。

## 表与图的分工

| | 能被复算 | 能进 CI | 用途 |
|---|---|---|---|
| **表**（本工具） | ✅ 重跑即得同一组数 | ✅ | **证据** |
| **图**（`--out=x.svg`） | ❌ 没人核对像素 | ❌ | 展示 |

用法：

    python tools/tightness_table.py 模块          # 打印 Markdown
    python tools/tightness_table.py 模块 --out=t.md --csv=t.csv
"""
import csv
import io
import os
import subprocess
import sys
import tempfile

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_export(module: str):
    fd, csv_path = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    try:
        p = subprocess.run([sys.executable, "-B", os.path.join(R, "src", "dsh.py"),
                            "export", module, "--full", "--format=csv", "--out=" + csv_path],
                           cwd=R, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0:
            raise SystemExit("  ✗ export 没产出 CSV：\n" + (p.stdout + p.stderr)[-600:])
        with io.open(csv_path, encoding="utf-8") as f:
            return list(csv.reader(f)), (p.stdout or "")
    finally:
        try:
            os.remove(csv_path)
        except OSError:
            pass


def to_markdown(rows):
    if not rows:
        return "（空）"
    head, body = rows[0], rows[1:]
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---" for _ in head) + "|"]
    for r in body:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def main(argv) -> int:
    module = None
    out_md = out_csv = None
    for a in argv:
        if a.startswith("--out="):
            out_md = a.split("=", 1)[1]
        elif a.startswith("--csv="):
            out_csv = a.split("=", 1)[1]
        elif not a.startswith("--"):
            module = a
    if not module:
        print("用法: tightness_table.py <模块> [--out=表.md] [--csv=表.csv]")
        return 2
    rows, _log = run_export(module)
    md = to_markdown(rows)
    if out_csv:
        with io.open(out_csv, "w", encoding="utf-8", newline="\n") as f:
            w = csv.writer(f)
            w.writerows(rows)
    if out_md:
        io.open(out_md, "w", encoding="utf-8", newline="\n").write(
            f"# 紧度表：{os.path.basename(module)}\n\n"
            "> 由 `tools/tightness_table.py` 生成，数据来自 `elench export --format=csv`。\n"
            "> **相对(κ÷实测) 越接近 1 越紧**；远大于 1 说明上界的阶比真实成本高。\n\n"
            + md + "\n")
        print(f"  已写 {out_md}（{len(rows) - 1} 行）")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
