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
import sys


from interp import Interp              # noqa: E402
from parser import parse_source        # noqa: E402

FAILED = []
TU = ("spec f(const vector<u64>& xs) -> u64 { effects {}; }\n"
      "spec visit(const vector<u8>& k, u64 v) -> u64 { effects {}; }\n"
      "spec touch_all(const vector<u64>& xs) -> u64 { effects {}; }\n")


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(body: str, xs=(1,)):
    ch = """u64 visit(const vector<u8>& k, u64 v) { return v; }
u64 touch_all(const vector<u64>& xs) { return 0; }
// 键/值**类型不同**的回调：回调按 `(k, v)` 顺序绑定（k: u64、v: vector<u8>），
// 顺序或类型错位会在这里直接崩，不需要 assert。
u64 pair_ty(u64 k, const vector<u8>& v) { return k + v.length(); }
// 二元回调（`map` / `unordered_map` / `HashMap`）。
u64 visit2(u64 k, u64 v) { return v; }
// 一元回调（`set` / `unordered_set`）。
u64 touch_elem(u64 x) { return x; }
u64 f(const vector<u64>& xs) {
""" + body + """
}
"""
    return Interp(parse_source(ch, "m.eln").module,
                  parse_source(TU, "m.tu").module).call("f", [list(xs)])


def main() -> int:
    print("=== 遍历 ===")
    n = run("""    HashMap<vector<u8>, u64> m = map_new();
    map_put(m, "a", 1);
    map_put(m, "b", 2);
    map_put(m, "c", 3);
    return for_each(m, visit);""")
    check("3 个键 → 返回 3", n == 3, str(n))

    n0 = run("""    HashMap<vector<u8>, u64> m = map_new();
    return for_each(m, visit);""")
    check("空 map → 返回 0", n0 == 0, str(n0))

    print("=== 快照语义：回调里改容器 ===")
    n = run("""    HashMap<vector<u8>, u64> m = map_new();
    map_put(m, "a", 1);
    map_put(m, "b", 2);
    // 回调里再塞一个键：本次遍历不受影响
    for_each(m, visit);
    map_put(m, "c", 3);
    return map_len(m);""")
    check("遍历后新增的键计数正常（3）", n == 3, str(n))

    print("=== vector 也支持（v0.73：一元回调） ===")
    nv = run("""    vector<u64> v = copy(xs);
    return for_each(v, touch_all);""", xs=(1, 2, 3))
    check("vector 遍历 → 返回元素个数", nv == len([1, 2, 3]), str(nv))

    print("=== ② 语言 map / set / unordered_map 也认（2026-11-10） ===")
    # 以前只认 `Vec` 与 stdlib 的 `HashMap`：文档《语法清单》写着
    # 「遍历映射表 | `for_each(m, f);`」，而 `m` 是语言 `map<K,V>` 时**运行期崩**
    # （`for_each 的第一个实参必须是 Vec 或 HashMap`）——典型的 D 类。
    lm = run("""    map<u64, u64> m;
    m.insert(3, 30);
    m.insert(1, 10);
    m.insert(2, 20);
    return for_each(m, visit2);""")
    check("语言 map：遍历 3 对", lm == 3, str(lm))

    # 键/值顺序：回调形参类型不同（`u64 k` / `const vector<u8>& v`），
    # 传错位置会在 `_convert` 处崩，所以这一条同时钉住「v 在第二位」。
    lt = run("""    map<u64, vector<u8>> m;
    m.insert(7, "seven");
    m.insert(9, "nine");
    return for_each(m, pair_ty);""")
    check("语言 map：`(k, v)` 按位置绑定（键 u64 / 值 Vec<u8>）", lt == 2, str(lt))

    ls = run("""    set<u64> s;
    s.insert(9);
    s.insert(7);
    s.insert(9);
    return for_each(s, touch_elem);""")
    check("语言 set：返回元素个数 2（一元回调，重复插入只留一个）",
          ls == 2, str(ls))

    lu = run("""    unordered_map<u64, u64> um;
    um.insert(5, 50);
    um.insert(6, 60);
    return for_each(um, visit2);""")
    check("语言 unordered_map：遍历 2 对", lu == 2, str(lu))

    print("=== 错误路径 ===")
    try:
        run("""    return for_each(42, visit);""")
        check("实参既不是 vector 也不是 map → 报错", False, "没有报错")
    except Exception as exc:                        # noqa: BLE001
        check("实参既不是 vector 也不是 map → 报错",
              "vector" in str(exc) or "HashMap" in str(exc), str(exc)[:60])

    try:
        run("""    HashMap<vector<u8>, u64> m = map_new();
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
