#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_foreach.py —— `for_each(m, f)`：容器遍历（函数类型的第一个用户）

## 语义

- **回调只能是具名函数**（与 `map/filter` 一致）；
- **快照遍历**：回调里改容器**不影响本次遍历**；
- 返回值 = 遍历了多少个键；
- κ = `n(m) × (1 + k(f)) + n(m)`。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from interp import Interp              # noqa: E402
from parser import parse_source        # noqa: E402

FAILED = []
TU = ("spec f(const Vec<u64>& xs) -> u64 { effects {}; }\n"
      "spec visit(const Vec<u8>& k, u64 v) -> u64 { effects {}; }\n"
      "spec touch_all(const Vec<u64>& xs) -> u64 { effects {}; }\n")


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(body: str, xs=(1,)):
    ch = """func visit(const Vec<u8>& k, u64 v) -> u64 { return v; }
func touch_all(const Vec<u64>& xs) -> u64 { return 0; }
func f(const Vec<u64>& xs) -> u64 {
""" + body + """
}
"""
    return Interp(parse_source(ch, "m.eln").module,
                  parse_source(TU, "m.tu").module).call("f", [list(xs)])


def main() -> int:
    print("=== 遍历 ===")
    n = run("""    HashMap<Vec<u8>, u64> m = map_new();
    map_put(m, "a", 1);
    map_put(m, "b", 2);
    map_put(m, "c", 3);
    return for_each(m, visit);""")
    check("3 个键 → 返回 3", n == 3, str(n))

    n0 = run("""    HashMap<Vec<u8>, u64> m = map_new();
    return for_each(m, visit);""")
    check("空 map → 返回 0", n0 == 0, str(n0))

    print("=== 快照语义：回调里改容器 ===")
    n = run("""    HashMap<Vec<u8>, u64> m = map_new();
    map_put(m, "a", 1);
    map_put(m, "b", 2);
    // 回调里再塞一个键：本次遍历不受影响
    for_each(m, visit);
    map_put(m, "c", 3);
    return map_len(m);""")
    check("遍历后新增的键计数正常（3）", n == 3, str(n))

    print("=== 错误路径 ===")
    try:
        run("""    Vec<u64> v = copy(xs);
    return for_each(v, visit);""")
        check("第一个实参不是 HashMap → 报错", False, "没有报错")
    except Exception as exc:                        # noqa: BLE001
        check("第一个实参不是 HashMap → 报错",
              "HashMap" in str(exc), str(exc)[:60])

    try:
        run("""    HashMap<Vec<u8>, u64> m = map_new();
    return for_each(m, 42);""")
        check("第二个实参不是函数 → 报错", False, "没有报错")
    except Exception as exc:                        # noqa: BLE001
        check("第二个实参不是函数 → 报错", True, str(exc)[:40])

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
