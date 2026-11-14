#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_field_size.py —— 真实 C++ 的两种**尺寸量来源**（2026-11-12）

## 为什么要有这份测试

以前 κ 只认「裸形参 / 裸局部」的 `.size()`：

    while (i < v.size())        → n(v)            ✅
    while (i < s.v.size())      → ⊤（写成 n(?)）  ❌
    u64 m = s.v.size(); while (i < m) → ⊤         ❌

真实 C++ 几乎总是把 size 放进**结构体字段**或**局部变量**，于是

    48 个语料函数里 16 个 κ = ⊤（能算的只有 67%）

本次把这两类都做成**可渲染的尺寸符号**（不是数字，也不是猜出来的常数）：

| 写法 | κ 里的尺寸量 | 说明 |
|---|---|---|
| `s.v.size()` | `n(s.v)` | 字段链的点分名。比 `n(s)` 精确：同一个结构体的不同容器字段是两个尺寸量 |
| `u64 m = s.v.size();` 之后的 `m` | `n(s.v)` | **声明即赋值**的局部尺寸别名；`m` 被改写后别名**撤销**（不许拿旧界当新界） |

## 硬约束（本文件钉的就是它）

1. **κ 仍是上界**：`ops ≤ κ`。这里用解释器真的跑一遍 `f_field(S{5,1,3})`
   （ops = 67 / 60，κ = 94 / 84），不是"看起来像符号"就算过。
2. **推不出的尺寸量不许写成 `n(?)`**：`n(?)` 会被 `cost_vars()` 当成一个普通
   尺寸量，是把"不知道"伪装成"知道"。
3. **`.tu` 侧要能给它赋值**：`target O(s.v.size())` 与
   `sizes nv = s.v.size(); target O(nv)` 都解析成**同一个** `n(s.v)`，
   `check` 也不再报 `E-Sizes-NotShape` / `E-While-NoBound`。

跑法：

    cd src; $env:PYTHONIOENCODING="utf-8"; python test_kappa_field_size.py
"""

import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但这批 κ 测试的惯例是 `HERE = src/`
#: （见 `test_kappa_matmul.py` 同一段），路径按仓库根推。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
for _p in (SRC, ROOT):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

import io
import os
import subprocess
import sys
import tempfile

from cost_check import compare, evaluate as kappa_at
from cost_interp import CostInterp
from interp import (Interp, StructVal, ops_enable, ops_of, ops_reset)
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


#: 声明侧：结构体 + 两种 target 写法（字段尺寸直接写 / 用 `sizes` 起名）。
TU = """struct S {
    vector<u64> v;
};

spec f_direct(vector<u64>& v) -> u64 {
    target { time: { max: O(v.size()) }, allocation: { max: 0 }, };
}

spec f_field(S& s) -> u64 {
    target { time: { max: O(s.v.size()) }, allocation: { max: 0 }, };
}

spec f_local(S& s) -> u64 {
    sizes nv = s.v.size();
    target { time: { max: O(nv) }, allocation: { max: 0 }, };
}

spec f_reassign(S& s) -> u64 {
    target { time: { max: O(s.v.size()) }, allocation: { max: 0 }, };
}
"""

CH = """u64 f_direct(vector<u64>& v) {
    u64 i = 0;
    u64 s = 0;
    while (i < v.size()) {
        s = s + v.at(i);
        i = i + 1;
    }
    return s;
}

u64 f_field(S& s) {
    u64 i = 0;
    u64 t = 0;
    while (i < s.v.size()) {
        t = t + s.v.at(i);
        i = i + 1;
    }
    return t;
}

u64 f_local(S& s) {
    u64 m = s.v.size();
    u64 i = 0;
    u64 t = 0;
    while (i < m) {
        t = t + s.v.at(i);
        i = i + 1;
    }
    return t;
}

u64 f_reassign(S& s) {
    u64 m = s.v.size();
    m = m + 10;
    u64 i = 0;
    u64 t = 0;
    while (i < m) {
        t = t + s.v.at(i);
        i = i + 1;
    }
    return t;
}
"""


def _modules():
    tu = parse_source(TU, "field_size.tu").module
    ch = parse_source(CH, "field_size.eln").module
    return tu, ch, CostInterp(ch, tu)


# ---------------------------------------------------------------- ① 字段尺寸
def test_field_size():
    print("[1] 结构体字段的尺寸：`s.v.size()` → `n(s.v)`（不是 ⊤，也不是 n(?)）")
    _tu, _ch, ci = _modules()
    k = ci.cost_of("f_field")
    text = k.render()
    check(k.kind != "unknown", "f_field 的 κ 不是 ⊤", text)
    check("n(s.v)" in text, "κ 里是 `n(s.v)`（字段链的点分名）", text)
    check("?" not in text, "κ 里没有 `n(?)`（那是伪尺寸量）", text)
    t = ci.target_cost("f_field")
    check(t is not None and t.render() == "n(s.v)",
          "target `O(s.v.size())` 与 κ 用同一个尺寸量", t.render() if t else None)

    # **上界**：解释器真的跑一遍（3 个元素）。
    sv = StructVal("S", {"v": [5, 1, 3]})
    ops_enable(True)
    try:
        ops_reset()
        Interp(_ch, _tu).call("f_field", [sv])
        ops = ops_of("f_field")
    finally:
        ops_enable(False)
    kv = kappa_at(k, {v: 3 for v in k.variables()})
    check(ops <= kv, f"ops {ops} ≤ κ {kv:g}（κ 仍是上界）", f"ops={ops} κ={kv}")


# ---------------------------------------------------------------- ② 局部别名
def test_local_alias():
    print("[2] 局部尺寸别名：`u64 m = s.v.size();` → `m` 就是 `n(s.v)`")
    _tu, _ch, ci = _modules()
    k = ci.cost_of("f_local")
    text = k.render()
    check(k.kind != "unknown", "f_local 的 κ 不是 ⊤", text)
    check("n(s.v)" in text, "`while (i < m)` 的界展开成 `n(s.v)`", text)
    check("n(m)" not in text, "κ 里没有伪尺寸量 `n(m)`", text)
    t = ci.target_cost("f_local")
    check(t is not None and t.render() == "n(s.v)",
          "`sizes nv = s.v.size(); O(nv)` 展开成同一个 `n(s.v)`",
          t.render() if t else None)

    sv = StructVal("S", {"v": [5, 1, 3]})
    ops_enable(True)
    try:
        ops_reset()
        Interp(_ch, _tu).call("f_local", [sv])
        ops = ops_of("f_local")
    finally:
        ops_enable(False)
    kv = kappa_at(k, {v: 3 for v in k.variables()})
    check(ops <= kv, f"ops {ops} ≤ κ {kv:g}（κ 仍是上界）", f"ops={ops} κ={kv}")


# ---------------------------------------------------------------- ③ 改写撤销
def test_revoked_alias():
    print("[3] 别名被改写 ⇒ 撤销：`m = m + 10` 之后 MUST NOT 再用 `n(s.v)`")
    _tu, _ch, ci = _modules()
    k = ci.cost_of("f_reassign")
    text = k.render()
    check("n(s.v)" not in text,
          "被改写过的 `m` 不再展开成声明处的 `n(s.v)`", text)
    check("n(m)" in text, "退回旧口径的局部符号 `n(m)`（不是旧的 `n(s.v)`）", text)
    # 尺寸量与 target 对不上 ⇒ **不判**（不给假达成）
    v = compare(k, ci.target_cost("f_reassign"))
    check(v.kind == "unknown",
          f"κ 含 `n(m)`、target 是 `n(s.v)` ⇒ 判定 unknown（假达成不许出现）：{v.kind}",
          v.render())


# ---------------------------------------------------------------- ④ check 接受
def test_check_accepts():
    print("[4] `check` 接受字段尺寸的 `target` / `sizes` 写法（不再报形状错）")
    d = tempfile.mkdtemp(prefix="elnfield-")
    try:
        tu_p = os.path.join(d, "m.tu")
        ch_p = os.path.join(d, "m.eln")
        # **只放可推的两个**：`f_reassign` 的界本来就不该被证明
        # （`m` 被改写过），它会（也应该）继续报 `E-While-NoBound`。
        _tu = TU.split("spec f_reassign")[0]
        _ch = CH.split("u64 f_reassign")[0]
        io.open(tu_p, "w", encoding="utf-8", newline="\n").write(_tu)
        io.open(ch_p, "w", encoding="utf-8", newline="\n").write(_ch)
        p = subprocess.run([sys.executable, "-B",
                            os.path.join(SRC, "elench.py"), "check", tu_p, ch_p],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=ROOT)
        out = (p.stdout or "") + (p.stderr or "")
        check(p.returncode == 0, "check 退出码 0", out[-300:])
        check("E-Sizes-NotShape" not in out,
              "`sizes nv = s.v.size();` 不再报 `E-Sizes-NotShape`", out[-300:])
        check("E-While-NoBound" not in out,
              "`while (i < m)`（m 是未改写的尺寸别名）不再报 `E-While-NoBound`",
              out[-300:])
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    test_field_size()
    test_local_alias()
    test_revoked_alias()
    test_check_accepts()

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
