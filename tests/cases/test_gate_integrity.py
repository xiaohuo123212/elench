#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_gate_integrity.py —— **门禁不可撒谎** + κ 的三条硬约束。

## 为什么单独一条文件

审计实测过：`u64 f() { return true; }` 能 `build` 出"✓ 构建通过"、
能 `run` 出 `True`。一个会说"通过"的门禁，比缺功能严重得多——所以它值得
一个**专门的**测试文件，而不是散在别处。

## 五条

1. `check` / `run` / `build` 对同一个类型错程序**都必须**非 0 退出；
2. `run --no-check` 才能跑（并明说"输出不可信"）；
3. 解析失败（坏 `.eln`）时 **κ 必须是 ⊤**，不许是 0；
4. `match` 单语句臂的 **κ > 0**；
5. 循环里的分配 **= 轮数 × 每轮**，不许是 0。
"""
import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import subprocess
import sys
import tempfile

from cost_interp import CostInterp
from parser import parse_source

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


TU = ('spec f() -> u64 {\n    effects {};\n'
      '    target { time: { max: O(1) } };\n}\n')
ELN_BAD = "u64 f() {\n    return true;\n}\n"


def write(tmp, tu=TU, eln=ELN_BAD):
    tp = os.path.join(tmp, "m.tu")
    ep = os.path.join(tmp, "m.eln")
    open(tp, "w", encoding="utf-8").write(tu)
    open(ep, "w", encoding="utf-8").write(eln)
    return tp, ep


def sh(args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, os.path.join(SRC, "elench.py")] + args,
                       capture_output=True, encoding="utf-8", errors="replace", env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def test_gate():
    print("[1] 类型错程序：check / build / run 都必须拒绝")
    tmp = tempfile.mkdtemp(prefix="gate_")
    tp, ep = write(tmp)
    for cmd in ("check", "build", "run"):
        rc, out = sh([cmd, tp, ep])
        check(rc != 0, f"{cmd} 退出码非 0", f"rc={rc} {out[-120:]}")
    rc, out = sh(["run", tp, ep, "--fn=f", "--no-check"])
    check(rc == 0, "run --no-check 才放行", f"rc={rc}")
    check("不可信" in out, "--no-check 明确提示输出不可信", out[-120:])


def test_kappa_top_on_parse_error():
    print("[2] 解析失败 → κ = ⊤（不许 0）")
    tu = parse_source(TU, "m.tu").module
    bad = parse_source("u64 f() {\n    return ???;\n}\n", "m.eln").module
    ci = CostInterp(bad, tu, diags=getattr(bad, "_parse_diags", None))
    check(ci.cost_of("f").is_top(), "坏 .eln 的 κ 是 ⊤", ci.cost_of("f").render())
    good = parse_source("u64 f() {\n    return 1;\n}\n", "m.eln").module
    ci2 = CostInterp(good, tu, diags=getattr(good, "_parse_diags", None))
    check(not ci2.cost_of("f").is_top(), "好 .eln 照常给数")


def test_match_cost():
    print("[3] match 单语句臂的 κ > 0")
    tu = parse_source('spec f(u64 n) -> u64 { effects {}; '
                      'target { time: { max: O(n) } }; }\n', "m.tu").module
    eln = parse_source("u64 f(u64 n) {\n"
                       "    match (n) {\n        0 => return 1,\n"
                       "        _ => return 2,\n    }\n}\n", "m.eln").module
    ci = CostInterp(eln, tu)
    k = ci.cost_of("f")
    check(not k.is_top() and k.render() != "0", "κ 不是 0", k.render())


def test_alloc_in_loop():
    print("[4] 循环内分配 = 轮数 × 每轮")
    tu = parse_source('spec f(const vector<u64>& xs) -> u64 { effects {}; '
                      'target { time: { max: O(n) }, allocation: { max: 0 } }; }\n',
                      "m.tu").module
    eln = parse_source("u64 f(const vector<u64>& xs) {\n"
                       "    u64 s = 0;\n"
                       "    for (auto x : xs) { vector<u64> t = [1, 2]; s = s + t.length(); }\n"
                       "    return s;\n}\n", "m.eln").module
    ci = CostInterp(eln, tu)
    a = ci.alloc_of("f").render()
    check("n(xs)" in a, "分配里出现 n(xs)", a)


def main() -> int:
    test_gate()
    test_kappa_top_on_parse_error()
    test_match_cost()
    test_alloc_in_loop()
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
