#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_parse_before_measure.py —— 测量类命令先看解析诊断

## 为什么

`benchmodel` 写在 **spec 外面**（模块级）时，解析器会老老实实报
`E-Parse-UnexpectedTop`；但 `measure` / `bench` / `stats` **不看诊断就往下跑**，
最后报的是一句误导性的二次错误：

    没有 benchmodel.scale，无法确定测量点

作者照这句话去改，改不对——它指的不是病因。这套检查守的就是"先报真错误"。
"""
import os
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
DSH = os.path.join(ROOT, "src", "dsh.py")

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


BAD_TU = """struct Point { x: u64, y: u64, }

spec total(const vector<Point>& pts) -> u64 {
    effects {};
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}

benchmodel {
    data { pts = structs(Point); }
    scale { pts in range(8, 64, *2); }
    seed = 0;
}
"""

BAD_ELN = """u64 total(const vector<Point>& pts) {
    return pts.length();
}
"""

tmp = tempfile.mkdtemp()
tu = os.path.join(tmp, "top.tu")
eln = os.path.join(tmp, "top.eln")
open(tu, "w", encoding="utf-8").write(BAD_TU)
open(eln, "w", encoding="utf-8").write(BAD_ELN)

env = dict(os.environ, PYTHONIOENCODING="utf-8", ELENCH_STEP_BUDGET="20000000")

print("[1] measure：报解析错误，不报「没有 benchmodel.scale」")
r = subprocess.run([sys.executable, "-B", DSH, "measure", tu],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", env=env, timeout=120)
out = (r.stdout or "") + (r.stderr or "")
check("提到 E-Parse-UnexpectedTop", "E-Parse-UnexpectedTop" in out, out[-200:])
check("不再说「没有 benchmodel.scale」", "没有 benchmodel.scale" not in out, out[-200:])
check("退出码非 0", r.returncode != 0, r.returncode)

print("[2] bench：同样先报解析错误")
r = subprocess.run([sys.executable, "-B", DSH, "bench", tu, "total"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", env=env, timeout=120)
out = (r.stdout or "") + (r.stderr or "")
check("提到 E-Parse-UnexpectedTop", "E-Parse-UnexpectedTop" in out, out[-200:])

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
