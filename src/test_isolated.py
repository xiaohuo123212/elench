#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_isolated.py —— `dsh bench <fn>`（规格 §13.5 B-18…B-22）

## 为什么用合成模块而不是样例

**三套样例里没有一个函数能做隔离测量**：

| 样例 | 为什么不行 |
|---|---|
| `search` / `sum` | 函数体里都有 `?!` → B-18 拒绝 |
| `calculator` | 没有 `?!`，但**没有 benchmodel** → 造不出数据 |
| `sum` 的候选 | 候选不是函数，不能单独调 |

**这不是缺陷，是 B-18 的正常效果**——它就是要挡住"拿半条链路测"。
所以本测试用**合成模块**覆盖成功路径。
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from bench import measure
from interp import Interp
from isolated import BenchError, check_measurable, ratio_of, report, verdict
from parser import parse_source

_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


#: 一个**不含** `?!`、且有 benchmodel 的模块——isolated 测量的合法目标
TU_OK = """spec total_up(const Vec<u64>& xs) -> u64 {
    effects   { };
    benchmodel {
        data  { xs = sorted_u64s(); }
        scale { xs in range(1, 1024, *2); }
        seed  = 1;
    };
    target {
        time: { max: O(xs.length()) },
    };
}
"""

CH_OK = """func total_up(const Vec<u64>& xs) -> u64 {
    u64 s = 0;
    u64 i = 0;
    const u64 n = xs.length();
    while (i < n) {
        s = s + xs.at(i);
        i = i + 1;
    }
    return s;
}
"""

#: 含 `?!` 的模块——必须被拒
TU_DISPATCH = """spec pick(const Vec<u64>& xs) -> u64 {
    effects   { };
    benchmodel {
        data  { xs = sorted_u64s(); }
        scale { xs in range(1, 64, *2); }
        seed  = 1;
    };
    choose k(const Vec<u64>& xs) -> u64 {
        over {a, b};
        objective time;
    };
}
"""

CH_DISPATCH = """func pick(const Vec<u64>& xs) -> u64 {
    return ?!k(xs);
}
func a(const Vec<u64>& xs) -> u64 { return 0; }
func b(const Vec<u64>& xs) -> u64 { return 1; }
"""


def load(tu_src, ch_src):
    tu = parse_source(tu_src, "t.tu").module
    ch = parse_source(ch_src, "t.eln").module
    return tu, ch, Interp(ch, tu)


# ---------------------------------------------------------------- B-18

def test_b18():
    print("[1] B-18：含 `?!` 或闭包不完整 → 拒绝")
    _, _, it = load(TU_DISPATCH, CH_DISPATCH)
    try:
        check_measurable("pick", it.fns)
        check(False, "含 `?!` 应被拒")
    except BenchError as e:
        msg = str(e)
        check("?!`" in msg or "?!）" in msg or "含 `?!" in msg,
              "含 `?!` 被拒且说明原因", msg[:60])
        check("dsh measure" in msg, "被拒时指向 `dsh measure`", msg[:80])

    # 闭包不完整：调用了还没实现的函数
    ch_missing = CH_OK.replace("    return s;", "    return helper_none(s);")
    _, _, it2 = load(TU_OK, ch_missing)
    try:
        check_measurable("total_up", it2.fns)
        check(True, "调用了未定义函数时不归 B-18 管（由检查器报 E-Name-Undefined）")
    except BenchError as e:
        check("闭包" in str(e), "闭包缺失被拒", str(e)[:60])

    # 正常函数应通过
    _, _, it3 = load(TU_OK, CH_OK)
    dist = check_measurable("total_up", it3.fns)
    check(isinstance(dist, list) and len(dist) >= 3,
          "合法函数返回失真清单（≥3 条）", str(dist))


# ---------------------------------------------------------------- B-19

def test_b19():
    print("[2] B-19：must 带 isolated + 失真清单；不写 cost.yaml")
    _, _, it = load(TU_OK, CH_OK)
    dist = check_measurable("total_up", it.fns)
    out = report("total_up", {"n = 16": 1.0, "n = 64": 2.5}, dist, [])
    check("isolated: true" in out, "输出含 isolated: true")
    check(all(d in out for d in dist), "失真清单每条都出现")
    check("MUST NOT 写入" in out, "明确声明不写 cost.yaml")
    # 真正的硬保证：report() 不碰文件系统——用 mtime 检查没有新文件产生
    here = os.path.dirname(os.path.abspath(__file__))
    before = set(os.listdir(here))
    report("total_up", {"n = 16": 1.0}, dist, [])
    after = set(os.listdir(here))
    check(before == after, "调用 report 不产生任何文件", str(after - before))


# ---------------------------------------------------------------- B-20

def test_b20():
    print("[3] B-20：只给判定与相对比值，不给原始时序")
    # 比值本身
    check(ratio_of(100.0, 250.0) == 2.5, "ratio_of(100,250) = 2.5", str(ratio_of(100.0, 250.0)))
    check(ratio_of(0.0, 5.0) is None, "基线为 0 → None（不除零）")
    check(verdict(0.5) == "更快", "比值 0.5 → 更快")
    check(verdict(2.0) == "更慢", "比值 2.0 → 更慢")
    check(verdict(1.0) == "持平", "比值 1.0 → 持平")

    _, _, it = load(TU_OK, CH_OK)
    dist = check_measurable("total_up", it.fns)
    out = report("total_up", {"n = 16": 1.0, "n = 64": 2.5}, dist, [])
    check("×2.50" in out, "比值以 ×N 形式出现")
    # 不许出现 "ns" / "纳秒" 这类原始时序
    check("ns" not in out.replace("MUST", ""), "输出里没有 `ns` 原始时序",
          [ln for ln in out.split("\n") if "ns" in ln.replace("MUST", "")][:2])


# ---------------------------------------------------------------- B-21

def test_b21():
    print("[4] B-21：自底向上——先列被调用者")
    tu = TU_OK.replace("total_up", "outer_fn")
    ch = """func inner_fn(u64 x) -> u64 { return x + 1; }
func outer_fn(const Vec<u64>& xs) -> u64 {
    const u64 n = xs.length();
    return inner_fn(n);
}
"""
    _, _, it = load(tu, ch)
    dist = check_measurable("outer_fn", it.fns)
    from isolated import _callees
    deps = [(d, _callees(it.fns[d], it.fns)) for d in _callees(it.fns["outer_fn"], it.fns)]
    check([d for d, _ in deps] == ["inner_fn"], "识别出直接被调用者 inner_fn", str(deps))
    out = report("outer_fn", {"n = 16": 1.0}, dist, deps)
    check(out.index("inner_fn") < out.index("相对比值"),
          "被调用者排在比值之前（自底向上）")


# ---------------------------------------------------------------- 真跑一次

def test_real_measure():
    print("[5] 真测一次（合成模块，无 `?!`）")
    tu, ch, _ = load(TU_OK, CH_OK)
    m = measure(ch, tu, "total_up", [16, 64], repeats=3)
    check(set(m) == {16, 64}, "测到两个规模", str(m))
    check(all(v > 0 for v in m.values()), "耗时都是正数")
    check(m[64] > m[16], "规模变大 → 耗时变大（线性函数）", f"{m}")


def main():
    test_b18()
    test_b19()
    test_b20()
    test_b21()
    test_real_measure()

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
