#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

"""test_build.py —— `dsh build`（规格 §13.5 **B-17** 的落地）

**B-17 原文**：含 `pending` 时 —— `dsh check` **通过**、`dsh build` **失败**、
成本表标 `state: incomplete`。

本文件就测这三件事，外加退出码。
"""
import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但**这批测试原先住在 `src/`**，里面大量
#: `os.path.join(HERE, "..", "文档")` / `os.path.join(ROOT, "tools")` 都假设
#: 「本文件在 src/、上一级是仓库根」。搬家时改 200 处路径比让 `HERE` 继续指 src 贵得多，
#: 所以保留旧语义：`HERE` = src/，`CASEDIR` = 本文件真正所在的目录。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import re
import subprocess
import sys
import tempfile

SAMPLE = os.path.join(ROOT, "文档", "设计", "目前设计", "样例")
DSH = os.path.join(HERE, "dsh.py")

_passed = 0
_failed = []


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def run(*args):
    p = subprocess.run([sys.executable, DSH, *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def make_pending_copy(dst):
    """把 `sum` 样例改成"有一个 pending 候选"的一致形态。"""
    tu = open(os.path.join(SAMPLE, "sum.tu"), encoding="utf-8").read()
    ch = open(os.path.join(SAMPLE, "sum.eln"), encoding="utf-8").read()
    # over 里标 pending
    tu2 = tu.replace("sum_by_iter", "sum_by_iter pending", 1)
    # .eln 里删掉该候选的体（B-16 要求：列了 pending ⇔ 没有体）
    start = ch.index("    sum_by_iter {")
    end = ch.index("}", ch.index("return s;", start)) + 1
    ch2 = ch[:start] + ch[end:]
    open(os.path.join(dst, "sum.tu"), "w", encoding="utf-8",
         newline="\n").write(tu2)
    open(os.path.join(dst, "sum.eln"), "w", encoding="utf-8",
         newline="\n").write(ch2)


def test_no_pending():
    print("[1] 无 pending：build 通过，state = complete")
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "sum.cost.yaml")
        rc, txt = run("build", os.path.join(SAMPLE, "sum.tu"), "--allow-missing-table",
                      os.path.join(SAMPLE, "sum.eln"), f"--out={out}")
        check(rc == 0, "退出码 0", f"rc={rc} {txt[-200:]}")
        check(os.path.exists(out), "成本表已生成", out)
        y = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
        check("state: complete" in y, "state: complete", y[:80])
        check("pending:" not in y, "没有 pending 段", y[:80])


def test_with_pending():
    print("[2] 有 pending：check 通过、build 失败、state = incomplete（B-17）")
    with tempfile.TemporaryDirectory() as d:
        make_pending_copy(d)
        tu, ch = os.path.join(d, "sum.tu"), os.path.join(d, "sum.eln")

        rc_c, txt_c = run("check", tu, ch)
        check(rc_c == 0, "check 通过（B-17 要求）", f"rc={rc_c} {txt_c[-200:]}")

        out = os.path.join(d, "sum.cost.yaml")
        rc_b, txt_b = run("build", tu, ch, f"--out={out}", "--allow-missing-table")
        check(rc_b == 1, "build 失败（退出码 1）", f"rc={rc_b}")
        check("pending" in txt_b, "失败信息里点了 pending", txt_b[-160:])

        y = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
        check("state: incomplete" in y, "state: incomplete", y[:80])
        check("sum_by_iter" in y, "成本表里列出 pending 候选名", y[-200:])


def test_usage():
    print("[3] 用法错：退出码 2")
    rc, txt = run("build")
    check(rc == 2, "无参数 → 2", f"rc={rc}")
    check("用法" in txt, "打印用法", txt[:60])


def main():
    test_no_pending()
    test_with_pending()
    test_usage()

    print("[4] E31：有 ?! 但没有分档表 → build 必须失败，并给出修法")
    import shutil
    import tempfile
    _tmp = tempfile.mkdtemp()
    # 复制 sum 的两份源码到临时目录（那里没有 .dispatch.yaml）
    shutil.copyfile(os.path.join(SAMPLE, "sum.tu"), os.path.join(_tmp, "sum.tu"))
    shutil.copyfile(os.path.join(SAMPLE, "sum.eln"), os.path.join(_tmp, "sum.eln"))
    _rc, _txt = run("build", os.path.join(_tmp, "sum.tu"),
                    os.path.join(_tmp, "sum.eln"))
    # 默认口径：静态分段可用 ⇒ 缺表只警告、放行
    check(_rc == 0, "缺表默认放行（静态分段）", f"rc={_rc} {_txt[-120:]}")
    check("静态分段" in _txt, "提示按静态分段放行", _txt[-160:])

    # 强制实测：--require-measured 才失败
    _rc, _txt = run("build", os.path.join(_tmp, "sum.tu"),
                    os.path.join(_tmp, "sum.eln"), "--require-measured")
    check(_rc == 1, "--require-measured 缺表 → 退出码 1（E31）", f"rc={_rc}")
    check("E-Dispatch-NoTable" in _txt, "报出 E-Dispatch-NoTable", _txt[-160:])
    check("elench measure" in _txt, "给出修法提示", _txt[-160:])

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
