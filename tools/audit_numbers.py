#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""audit_numbers.py —— **论文里的每个数字从哪来**（可复算，防漂移）

论文写 `12.3k SLOC`、`58 套`、`924 处 check(`、`119/137` 这些数字时，
读者凭什么信？凭这个脚本：**同一份仓库、同一条命令、同一个口径**。

用法：
    python tools/audit_numbers.py           # 打印
    python tools/audit_numbers.py --json    # 机器可读（写进 artifact 清单）
"""
import json
import os
import re
import subprocess
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(R, "src")


def count_lines(path):
    """(物理行, 非空行, 代码行) —— **代码行** = 去掉空行与整行注释。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    phys = len(lines)
    nonempty = sum(1 for s in lines if s.strip())
    code = 0
    for s in lines:
        t = s.strip()
        if not t or t.startswith("#"):
            continue
        code += 1
    return phys, nonempty, code


def agg(pattern):
    phys = nonempty = code = 0
    files = 0
    for f in sorted(os.listdir(SRC)):
        if not re.match(pattern, f):
            continue
        p, n, c = count_lines(os.path.join(SRC, f))
        phys += p
        nonempty += n
        code += c
        files += 1
    return {"files": files, "physical": phys, "nonempty": nonempty, "code": code}


def keywords():
    """KEYWORDS 表里真正被识别的关键字个数（扣掉 eof/ident 这类内部 token）。"""
    t = open(os.path.join(SRC, "lexer.py"), encoding="utf-8").read()
    m = re.search(r"KEYWORDS = \{(.*?)\n\}", t, re.S)
    body = m.group(1) if m else ""
    entries = re.findall(r"^\s+\"([a-z_]+)\":", body, re.M)
    return len(entries), entries


def subcommands():
    """dsh 的分派表里认的子命令（不是 USAGE 里印出来的）。"""
    t = open(os.path.join(SRC, "dsh.py"), encoding="utf-8").read()
    names = sorted(set(re.findall(r'cmd == "([a-z]+)"', t)))
    return names


def assertions():
    total = 0
    for f in os.listdir(SRC):
        if f.endswith(".py"):
            with open(os.path.join(SRC, f), encoding="utf-8", errors="replace") as fh:
                total += fh.read().count("check(")
    return total


def sample_lines():
    d = os.path.join(R, "文档", "设计", "目前设计", "样例")
    out = {}
    for f in sorted(os.listdir(d)):
        if f.endswith((".tu", ".eln")):
            out[f] = count_lines(os.path.join(d, f))
    return out


def main():
    impl = agg(r"^(?!test_).*\.py$")
    tests = agg(r"^test_.*\.py$")
    kw_n, kw = keywords()
    subs = subcommands()
    data = {
        "implementation_files": impl["files"],
        "implementation_physical": impl["physical"],
        "implementation_nonempty": impl["nonempty"],
        "implementation_code": impl["code"],
        "test_files": tests["files"],
        "test_physical": tests["physical"],
        "test_nonempty": tests["nonempty"],
        "test_code": tests["code"],
        "keywords_total": kw_n,
        "check_call_sites": assertions(),
        "subcommands": subs,
        "sample_lines": sample_lines(),
    }
    if "--json" in sys.argv:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    print("=== 口径（论文必须写清用的是哪个）===")
    print(f"  实现：{impl['files']:>3} 文件  "
          f"物理 {impl['physical']:>6}  非空 {impl['nonempty']:>6}  "
          f"代码行 {impl['code']:>6}  ≈ {impl['code']/1000:.1f}k SLOC")
    print(f"  测试：{tests['files']:>3} 文件  "
          f"物理 {tests['physical']:>6}  非空 {tests['nonempty']:>6}  "
          f"代码行 {tests['code']:>6}  ≈ {tests['code']/1000:.1f}k SLOC")
    print(f"  关键字：{kw_n} 个 token 条目（含内部 token；净关键字 {kw_n - 2}）")
    print(f"  check( 调用点：{data['check_call_sites']}")
    print(f"  子命令（分派表）：{len(subs)} 个 → {' '.join(subs)}")
    print()
    print("=== 样例规模（.tu vs .eln）===")
    for name, (p, n, c) in data["sample_lines"].items():
        if name.startswith("calculator"):
            print(f"  {name:18} 物理 {p:>4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
