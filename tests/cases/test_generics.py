#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_generics.py —— 泛型第一批（v0.71）

## 这一批**只做三件事**

1. **解析** `func id<T>(const vector<T>& xs) -> vector<T>`（类型参数列表）；
2. **签名相容**：`vector<T>` 能实现 `vector<u64>` 的 spec（**类型参数当通配符**）；
3. **`copy(xs)`** 内建（分配 1 次，E36 记得到）。

## 明确**没做**的（写在文档里，别当成有）

- **没有单态化**：`T` 不会在编译期被替换成具体类型（解释器本来就是动态的）；
- **没有泛型容器**：`HashMap<K,V>`、`set`、`deque` 都还没有；
- **`T` 上的元素运算不允许**：只能 `xs.length()`、传参、返回——
  因为"`T` 能不能比较/相加"需要 trait，而 trait 还没做。
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


from checker import check_module      # noqa: E402
from interp import Interp             # noqa: E402
from parser import parse_source       # noqa: E402

FAILED = []
T = "target { time: { max: O(xs.size()) }, allocation: { max: 0 }, };\n"


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def main() -> int:
    print("=== 解析 ===")
    m = parse_source("vector<T> id<T>(const vector<T>& xs) { return xs; }\n", "m.eln")
    fn = next((c for c in m.module.children if c.kind == "fn"), None)
    tp = next((c for c in fn.children if c.kind == "type-params"), None) if fn else None
    check("函数签名里有 type-params", tp is not None)
    check("类型参数名是 T", tp is not None and tp.children[0].name == "T", "")

    print("=== 签名相容：vector<T> 实现 vector<u64> ===")
    tu = f"""spec id(const vector<u64>& xs) -> vector<u64> {{ effects {{}}; {T} }}
spec consume(const vector<u64>& xs) -> u64 {{ effects {{}}; {T} }}
"""
    ch = """vector<T> id<T>(const vector<T>& xs) {
    return xs;
}
u64 consume(const vector<u64>& xs) {
    const vector<u64> ys = copy(xs);
    const vector<u64> zs = id(ys);
    return zs.length();
}
"""
    codes = [d.code for d in check_module(tu, ch, "m").diagnostics]
    check("check 通过（无诊断）", codes == [], str(codes))

    print("=== 运行 ===")
    tu_m = parse_source(tu, "m.tu").module
    ch_m = parse_source(ch, "m.eln").module
    got = Interp(ch_m, tu_m).call("consume", [[1, 2, 3]])
    check("泛型函数跑得通（返回长度 3）", got == 3, str(got))

    print("=== 类型参数推断 ===")
    tu2 = f"""spec onlyret(const vector<u64>& xs) -> u64 {{ effects {{}}; {T} }}
spec id(const vector<u64>& xs) -> vector<u64> {{ effects {{}}; {T} }}
"""
    ch_ok = """vector<T> id<T>(const vector<T>& xs) { return xs; }
u64 onlyret(const vector<u64>& xs) {
    const vector<u64> ys = id(xs);
    return ys.length();
}
"""
    check("能从实参推出 T（无诊断）",
          [d.code for d in check_module(tu2, ch_ok, "m").diagnostics] == [])

    ch_bad = """vector<T> id<T>(const vector<T>& xs) { return xs; }
u64 onlyret(const vector<u64>& xs) {
    const u64 n = id(1);
    return n;
}
"""
    check("推不出 T → E-Type-InferFailed",
          "E-Type-InferFailed" in [d.code for d in check_module(tu2, ch_bad, "m").diagnostics])

    print("=== HashMap<K,V>（有界探测） ===")
    tu3 = f"""spec m(const vector<u64>& xs) -> u64 {{ effects {{}}; {T} }}
"""
    ch3 = """u64 m(const vector<u64>& xs) {
    HashMap<vector<u8>, u64> h = map_new();
    map_put(h, "a", 1);
    map_put(h, "b", 2);
    print(map_len(h), map_get(h, "a"), map_has(h, "b"), map_has(h, "zzz"));
    return map_len(h);
}
"""
    tu3m = parse_source(tu3, "m.tu").module
    ch3m = parse_source(ch3, "m.eln").module
    check("HashMap 能存能取（长度 2）", Interp(ch3m, tu3m).call("m", [[1]]) == 2)

    print("=== 非泛型函数仍然严格 ===")
    bad = """vector<u64> id(const vector<u64>& xs) {
    return xs;
}
"""
    bad_tu = f"""spec id(const vector<u8>& xs) -> vector<u8> {{ effects {{}}; {T} }}
"""
    codes = [d.code for d in check_module(bad_tu, bad, "m").diagnostics]
    check("vector<u64> 实现 vector<u8> 的 spec → 报错",
          "E-Spec-SigMismatch" in codes, str(codes))

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
