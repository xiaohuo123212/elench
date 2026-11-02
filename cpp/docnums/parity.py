#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""parity.py —— C++ 版 docnums 与 Python 版 `tools/doc_numbers.py` 逐字节对照。

## 为什么要有"两个实现"

不是炫技：**检查器自己也得被检查**。语义只有一份（同一份 `doc_numbers.json`、
同一套 `<!--dn:key-->` 标记），两个独立实现跑同一批文档，输出必须一致。
不一致就说明"其中一个是错的"，而这正是这个工具要防的那类问题。

## 用法

    python parity.py path/to/docnums            # 只比 --list / --check
    python parity.py path/to/docnums --full     # 再比 --scan（噪声大，可选）

退出码：0 一致 / 1 有差异。
"""
import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
PY_TOOL = os.path.join(ROOT, "tools", "doc_numbers.py")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", cwd=ROOT)
    return r.returncode, (r.stdout or "").strip()


def norm(text):
    """去掉路径分隔符差异与空白尾巴，方便比较。"""
    return "\n".join(line.rstrip().replace("\\", "/") for line in text.split("\n")).strip()


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python parity.py <docnums 可执行文件> [--full]")
        return 2
    exe = sys.argv[1]
    full = "--full" in sys.argv

    cases = [
        ["--list"],
        ["--check"],
    ]
    bad = 0
    for args in cases:
        rc_c, out_c = run([exe] + args)
        rc_p, out_p = run([sys.executable, PY_TOOL] + args)
        same_rc = rc_c == rc_p
        same_out = norm(out_c) == norm(out_p)
        flag = "ok  " if (same_rc and same_out) else "DIFF"
        print(f"  {flag} {' '.join(args)}  rc: cpp={rc_c} py={rc_p}")
        if not same_out:
            bad += 1
            c_lines = norm(out_c).split("\n")
            p_lines = norm(out_p).split("\n")
            for i in range(max(len(c_lines), len(p_lines))):
                c = c_lines[i] if i < len(c_lines) else "<缺>"
                p = p_lines[i] if i < len(p_lines) else "<缺>"
                if c != p:
                    print(f"        cpp: {c}")
                    print(f"        py : {p}")

    if full:
        for doc in ("文档/其他/预印本/预印本-v0.1.md",):
            rc_c, out_c = run([exe, "--scan", doc])
            rc_p, out_p = run([sys.executable, PY_TOOL, "--scan", doc])
            print(f"  {'ok  ' if norm(out_c) == norm(out_p) else 'DIFF'} --scan {doc}")
            if norm(out_c) != norm(out_p):
                bad += 1

    print(f"\n{'一致' if bad == 0 else f'{bad} 处差异'}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
