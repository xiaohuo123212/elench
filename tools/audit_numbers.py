#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""audit_numbers.py —— **论文里的每个数字从哪来**（可复算，防漂移）

论文写「实现 N 行 / 测试 M 套 / K 处断言」这类数字时，
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


def _py_files(root):
    """`root` 下**递归**所有 .py（跳过 `__pycache__`）。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in sorted(filenames):
            if f.endswith(".py"):
                out.append(os.path.join(dirpath, f))
    return sorted(out)


def agg(root, pattern, recursive=True):
    """统计 `root` 下匹配 `pattern` 的 .py。

    **必须递归**：实现有 `src/cost_domain/`、测试在 `tests/cases/`。
    只扫顶层会得出"测试 0 文件"这种结论（2026-11-03 修）。
    """
    phys = nonempty = code = 0
    files = 0
    paths = _py_files(root) if recursive else [
        os.path.join(root, f) for f in sorted(os.listdir(root))]
    for path in paths:
        if not re.match(pattern, os.path.basename(path)):
            continue
        p, n, c = count_lines(path)
        phys += p
        nonempty += n
        code += c
        files += 1
    return {"files": files, "physical": phys, "nonempty": nonempty, "code": code}


def keywords():
    """真正被识别的关键字个数。

    **不要用正则去数 `KEYWORDS = {...}` 的字面量**：v0.68 之后它是
    `{k.value: k for k in Kind if ...}`（从枚举生成），正则只能数到 4 行。
    直接 import 拿真值，同时把"已下线的关键字"也报出来。
    """
    if SRC not in sys.path:
        sys.path.insert(0, SRC)
    try:
        import lexer                                    # noqa: E402
        live = sorted(lexer.KEYWORDS)
        dead = sorted(getattr(lexer, "REMOVED_KEYWORDS", {}) or {})
        return len(live), live, dead
    except Exception:                                   # noqa: BLE001
        return 0, [], []


def subcommands():
    """dsh 的分派表里认的子命令（不是 USAGE 里印出来的）。"""
    t = open(os.path.join(SRC, "dsh.py"), encoding="utf-8").read()
    names = sorted(set(re.findall(r'cmd == "([a-z]+)"', t)))
    return names


def assertions():
    """`check(` 调用点：**数测试目录**，不是数实现。

    实现里的 `check(` 是函数调用（`check_files` 之类），测试里的才是断言。
    """
    total = 0
    for path in _py_files(os.path.join(R, "tests")):
        with open(path, encoding="utf-8", errors="replace") as fh:
            total += fh.read().count("check(")
    return total


def _sample_dirs():
    """样例目录，**按优先级**。

    `文档/样例/` 是**公开的规范位置**（`tools/kappa_vs_ops.py` 读的就是它）；
    `文档/设计/目前设计/样例/` 是内部镜像。以前只读后者，导致公开目录里的
    新增样例在审计里看不见（2026-11-04 修）。
    """
    return [os.path.join(R, "文档", "样例"),
            os.path.join(R, "文档", "设计", "目前设计", "样例")]


def sample_lines():
    d = next((x for x in _sample_dirs() if os.path.isdir(x)), None)
    out = {}
    if d is None:
        return out
    for f in sorted(os.listdir(d)):
        if f.endswith((".tu", ".eln")):
            out[f] = count_lines(os.path.join(d, f))
    return out


def sample_programs():
    """`.tu` 样例个数（= 可对拍的算法数，每个样例一个 target）。"""
    d = next((x for x in _sample_dirs() if os.path.isdir(x)), None)
    if d is None:
        return 0
    return sum(1 for f in os.listdir(d) if f.endswith(".tu"))


def main():
    impl = agg(SRC, r"^(?!test_).*\.py$")
    tests = agg(os.path.join(R, "tests"), r"^test_.*\.py$")
    kw_n, kw, kw_dead = keywords()
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
        "keywords_removed": kw_dead,
        "check_call_sites": assertions(),
        "subcommands": subs,
        "sample_lines": sample_lines(),
        "sample_programs": sample_programs(),
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
    print(f"  关键字：{kw_n} 个（已下线仍需词法识别的：{len(kw_dead)} 个 → "
          f"{' '.join(kw_dead) if kw_dead else '无'}）")
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
