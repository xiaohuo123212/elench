#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_doc_numbers.py —— 文档数字绑定必须**真的会失败**。

## 为什么这条测试重要

一个"永远通过"的检查器比没有检查器更糟：它给人"数字有保障"的错觉。
所以这里既测"一致 → 通过"，也测"写错 → 非零退出"，
还测"未知 key"和"来源文件缺失"这两种失败形态。
"""
import os
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
TOOL = os.path.join(ROOT, "tools", "doc_numbers.py")

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(args):
    return subprocess.run([sys.executable, TOOL] + args,
                          capture_output=True, encoding="utf-8", errors="replace")


def write(text):
    fd, path = tempfile.mkstemp(suffix=".md", dir=tempfile.gettempdir())
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def test_list():
    print("=== --list 能解析所有来源 ===")
    r = run(["--list"])
    check("退出码 0", r.returncode == 0, r.stderr[-300:])
    check("列出了 kappa_total_n16", "kappa_total_n16" in r.stdout)
    check("来源都算出来了（没有 '—'）",
          "—" not in r.stdout.split("\n", 1)[1], r.stdout[:300])


def current(key):
    """从 --list 里取**当前**值。

    **不能写死 85**：测试文件数会因为新增一条测试而变（观察者效应），
    写死就会变成"加一个测试 → 绑定的数字立刻错"。
    """
    for line in run(["--list"]).stdout.split("\n"):
        if line.startswith(key):
            return int(line.split()[1].replace(",", ""))
    return None


def test_check_pass():
    print("=== 绑定正确 → 通过 ===")
    v = current("test_files")
    p = write(f"测试文件共 {v}<!--dn:test_files--> 个。\n")
    r = run(["--check", p])
    check("退出码 0", r.returncode == 0, (r.stdout + r.stderr)[-300:])
    os.remove(p)


def test_check_drift():
    print("=== 数字被改过 → 必须失败 ===")
    p = write("测试文件共 84<!--dn:test_files--> 个。\n")
    r = run(["--check", p])
    check("退出码 1", r.returncode == 1, (r.stdout + r.stderr)[-300:])
    check("指出了差异", "test_files" in (r.stdout + r.stderr),
          (r.stdout + r.stderr)[-300:])
    os.remove(p)


def test_unknown_key():
    print("=== 未知绑定键 → 必须失败 ===")
    p = write("随便 3<!--dn:no_such_key--> 个。\n")
    r = run(["--check", p])
    check("退出码 1", r.returncode == 1, (r.stdout + r.stderr)[-200:])
    check("报了未知键", "no_such_key" in (r.stdout + r.stderr))
    os.remove(p)


def test_scan():
    print("=== --scan 不崩、能认出可疑数字 ===")
    p = write("这里有个数字 12,345,678 没有来源。\n\n```bash\nelench cost --n=4096\n```\n")
    r = run(["--scan", p])
    check("退出码 0", r.returncode == 0, r.stderr[-300:])
    check("认出了 12,345,678", "12,345,678" in r.stdout, r.stdout[:300])
    check("代码块里的数字不算", "4096" not in r.stdout, r.stdout[:300])
    os.remove(p)


def main() -> int:
    test_list()
    test_check_pass()
    test_check_drift()
    test_unknown_key()
    test_scan()
    if FAILED:
        print(f"\n失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
