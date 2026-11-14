#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_cxx_map.py —— C++ 后端的第一块砖：**映射表必须闭合**

## 为什么先测"表"而不是"生成器"

仓库里**没有 C++ 后端**（`elench export` 导的是成本数据，不是代码）。
在后端写出来之前，唯一能保证的是：**每个内建都有交代**——

- 要么有映射（带**成本注记**，因为 C++ 的 `unordered_map` 平均 `O(1)` **不等于**我们的上界承诺）；
- 要么在 `UNMAPPED` 里写明"为什么不做"。

**漏一个就红。** 这样后端作者不会遇到"这个内建怎么办"的空白。

## L8（2026-11-13）：类型层从"标量 + 一层 vector"扩到**递归**

语料测绘：`vector<vector<u8>>` / `vector<vector<u64>>` / `map<vector<u8>, i64>` /
用户 struct（`Ring` / `JobHeap` / `Entry` …）/ `f64` 在 `.tu` 里大量出现，
而旧表只有 22 条字面量，认不出的会被 `dsh.py` 的 `or "uint64_t"` 静默兜底。

这个文件现在同时守住两件事：
1. **旧映射一个都不许变**（下面 `LEGACY_TYPE_MAP` 逐条对比）；
2. **新映射必须递归、必须给具体理由**（认不出返回 None + `unmapped_reason`）。
"""
import glob
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


import cxx_map      # noqa: E402
import stdlib       # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


#: 2026-11-13 改动**之前**的 `TYPE_MAP` 全量快照（22 条，逐字抄自 HEAD `bf0ab71`）。
#: 本任务只允许**新增**；任何一条值变了都算回归。
LEGACY_TYPE_MAP = {
    "u64": "uint64_t",
    "u32": "uint32_t",
    "i64": "int64_t",
    "u8": "uint8_t",
    "bool": "bool",
    "vector<u8>": "std::vector<uint8_t>",
    "vector<u64>": "std::vector<uint64_t>",
    "vector<i64>": "std::vector<int64_t>",
    "vector<u32>": "std::vector<uint32_t>",
    "Vec<u8>": "std::vector<uint8_t>",
    "Vec<u64>": "std::vector<uint64_t>",
    "Vec<i64>": "std::vector<int64_t>",
    "Vec<u32>": "std::vector<uint32_t>",
    "map<K,V>": "std::map<K, V>",
    "set<T>": "std::set<T>",
    "Map<K,V>": "std::map<K, V>",
    "Map<u64,u64>": "std::map<uint64_t, uint64_t>",
    "Set<T>": "std::set<T>",
    "Set<u64>": "std::set<uint64_t>",
    "Split": "std::vector<std::vector<uint8_t>>",
    "Option": "std::optional<T>",
    "Result": "tl::expected<T, E>",
}


def _type_text(n):
    """把类型节点还原成字符串（与 `dsh._type_str` 同口径）。

    **故意不 import dsh**：`dsh.py` 有独立的代理在改，测试不该因为它半写成
    而红。这里只依赖 parser 的 AST 形状（`type-ref` / `type-name` / `type-args`）。
    """
    if n is None:
        return ""
    kind = getattr(n, "kind", "")
    if kind == "type-ref":
        inner = next((c for c in n.children if c.kind in ("type-name", "type-ref")), None)
        return _type_text(inner)
    if kind == "type-name":
        args = next((c for c in n.children if c.kind == "type-args"), None)
        if args is None:
            return n.name or ""
        return (n.name or "") + "<" + ", ".join(_type_text(a) for a in args.children) + ">"
    return getattr(n, "name", "") or ""


def _corpus_type_strings():
    """扫 `demo/corpus/*.tu`：spec 形参 / 返回 + struct 字段的类型串。"""
    from parser import parse_source
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, "demo", "corpus", "*.tu"))):
        src = open(path, encoding="utf-8").read()
        res = parse_source(src, os.path.basename(path))
        if not res.ok:
            out.append((os.path.basename(path), "<parse-fail>"))
            continue
        mod = res.module

        def walk(n):
            k = getattr(n, "kind", "")
            if k == "spec":
                ps = next((c for c in (n.children or []) if c.kind == "params"), None)
                for prm in (ps.children if ps else []):
                    ty = next((c for c in (prm.children or [])
                               if c.kind in ("type-ref", "type-name")), None)
                    out.append((os.path.basename(path), _type_text(ty)))
                for c in (n.children or []):
                    if c.kind in ("type-ref", "type-name"):
                        out.append((os.path.basename(path), _type_text(c)))
                        break
            elif k == "struct":
                for f in (n.children or []):
                    if getattr(f, "kind", "") == "field":
                        ty = next((c for c in (f.children or [])
                                   if c.kind in ("type-ref", "type-name")), None)
                        out.append((os.path.basename(path), _type_text(ty)))
            for c in (n.children or []):
                walk(c)

        walk(mod)
    return out


def main() -> int:
    print("=== 每个内建都有交代 ===")
    missing = [n for n in stdlib.FNS if n not in cxx_map.BUILTIN_MAP and n not in cxx_map.UNMAPPED]
    check("没有任何内建被漏掉（共 %d 个）" % len(stdlib.FNS), not missing, str(missing))

    print("=== 每条映射都带成本注记 ===")
    no_note = [n for n, v in cxx_map.BUILTIN_MAP.items() if not (len(v) == 2 and v[1].strip())]
    check("映射都带 cost_note", not no_note, str(no_note))

    print("=== 既有映射 22 条逐条不变（改前快照对比）===")
    regress = [(k, v, cxx_map.cxx_type(k)) for k, v in LEGACY_TYPE_MAP.items()
               if cxx_map.cxx_type(k) != v]
    check("旧 TYPE_MAP 22/22 值不变", not regress, str(regress))
    # 本任务只动类型层：内建表 / 不映射名单的条数也钉住（新增要显式改这条）。
    check("BUILTIN_MAP 仍是 124 条", len(cxx_map.BUILTIN_MAP) == 124,
          str(len(cxx_map.BUILTIN_MAP)))
    check("UNMAPPED 仍只有 exec / now_ms",
          set(cxx_map.UNMAPPED) == {"exec", "now_ms"}, str(set(cxx_map.UNMAPPED)))

    print("=== 关键类型映射（旧的三条仍然成立）===")
    for elench, cxx in (("map<K,V>", "std::map"),
                        ("set<T>", "std::set"),
                        ("vector<u8>", "std::vector")):
        check(f"{elench} → {cxx}", cxx in cxx_map.cxx_type(elench), cxx_map.cxx_type(elench))

    print("=== 标量补全：u16/i8/i16/i32/f32/f64/void/string ===")
    for elench, cxx in (
            ("u16", "uint16_t"), ("i8", "int8_t"), ("i16", "int16_t"),
            ("i32", "int32_t"), ("f32", "float"), ("f64", "double"),
            ("void", "void"),
            # `string` 是 `Text` 的别名；parser 归一成 vector<u8>，所以这里
            # 必须是字节序列而不是 std::string（"不猜字符串语义"）。
            ("string", "std::vector<uint8_t>"),
    ):
        got = cxx_map.cxx_type(elench)
        check(f"{elench} → {cxx}", got == cxx, repr(got))

    print("=== 嵌套容器：任意深度（不是写死两层）===")
    for elench, cxx in (
            ("vector<vector<u8>>", "std::vector<std::vector<uint8_t>>"),
            ("vector<vector<u64>>", "std::vector<std::vector<uint64_t>>"),
            ("vector<vector<f64>>", "std::vector<std::vector<double>>"),
            ("vector<vector<vector<u8>>>",
             "std::vector<std::vector<std::vector<uint8_t>>>"),
            ("vector<vector<vector<vector<Entry>>>>",
             "std::vector<std::vector<std::vector<std::vector<Entry>>>>"),
    ):
        got = cxx_map.cxx_type(elench)
        check(f"{elench} → {cxx}", got == cxx, repr(got))

    print("=== map / set：键值类型递归映射 ===")
    for elench, cxx in (
            ("map<vector<u8>, i64>", "std::map<std::vector<uint8_t>, int64_t>"),
            ("map<u64, u64>", "std::map<uint64_t, uint64_t>"),
            ("map<u64, vector<u64>>", "std::map<uint64_t, std::vector<uint64_t>>"),
            ("map<string, u64>", "std::map<std::vector<uint8_t>, uint64_t>"),
            ("set<vector<u64>>", "std::set<std::vector<uint64_t>>"),
            ("set<u64>", "std::set<uint64_t>"),
            # 旧名（Vec / Map / Set）带参数时也要递归，不只是字面量表里那几条。
            ("Map<u64, vector<u8>>", "std::map<uint64_t, std::vector<uint8_t>>"),
            ("Vec<vector<u8>>", "std::vector<std::vector<uint8_t>>"),
    ):
        got = cxx_map.cxx_type(elench)
        check(f"{elench} → {cxx}", got == cxx, repr(got))

    print("=== 用户 struct / enum 名：原样透传（登记过的才透传）===")
    for elench, cxx in (
            ("Ring", "Ring"), ("Job", "Job"), ("JobHeap", "JobHeap"),
            ("QueenSolver", "QueenSolver"), ("Dsu", "Dsu"), ("Bits", "Bits"),
            ("Entry", "Entry"), ("Group", "Group"),
            ("Interval", "Interval"), ("Summary", "Summary"), ("Token", "Token"),
            ("vector<Entry>", "std::vector<Entry>"),
            ("vector<Group>", "std::vector<Group>"),
            ("vector<Job>", "std::vector<Job>"),
            ("vector<Interval>", "std::vector<Interval>"),
    ):
        got = cxx_map.cxx_type(elench)
        check(f"{elench} → {cxx}", got == cxx, repr(got))

    print("=== 新用户类型：登记后透传，没登记不猜 ===")
    check("未登记的 Widget → None", cxx_map.cxx_type("Widget") is None,
          repr(cxx_map.cxx_type("Widget")))
    cxx_map.register_user_types("Widget")
    try:
        check("register_user_types('Widget') 后 → Widget",
              cxx_map.cxx_type("Widget") == "Widget", repr(cxx_map.cxx_type("Widget")))
        check("按模块传名字（不改全局表）",
              cxx_map.cxx_type("Gadget", user_types={"Gadget"}) == "Gadget",
              repr(cxx_map.cxx_type("Gadget", user_types={"Gadget"})))
    finally:
        cxx_map.USER_TYPES.discard("Widget")

    print("=== 定长数组 array<T, N> ===")
    for elench, cxx in (
            ("array<u64, LCS_CAP>", "std::array<uint64_t, LCS_CAP>"),
            ("array<Token, MAX_TOKENS>", "std::array<Token, MAX_TOKENS>"),
            ("array<i64, MAX_DEPTH>", "std::array<int64_t, MAX_DEPTH>"),
            ("array<u64, 4>", "std::array<uint64_t, 4>"),
            ("array<vector<u8>, 2>", "std::array<std::vector<uint8_t>, 2>"),
            ("FixedVec<u8, 8>", "std::array<uint8_t, 8>"),
    ):
        got = cxx_map.cxx_type(elench)
        check(f"{elench} → {cxx}", got == cxx, repr(got))

    print("=== optional / expected（参数化写法）===")
    for elench, cxx in (
            ("optional<u64>", "std::optional<uint64_t>"),
            ("optional<vector<u8>>", "std::optional<std::vector<uint8_t>>"),
            ("expected<u64, i64>", "tl::expected<uint64_t, int64_t>"),
            ("Result<u64, i64>", "tl::expected<uint64_t, int64_t>"),
    ):
        got = cxx_map.cxx_type(elench)
        check(f"{elench} → {cxx}", got == cxx, repr(got))

    print("=== 认不出来：返回 None，且 unmapped_reason 有具体原因 ===")
    for elench, needle in (
            ("Foo", "未知类型名"),
            ("U64", "未知类型名"),
            ("vectr<u8>", "未知类型构造"),
            ("vector<u8, u64>", "需要 1 个类型参数"),
            ("map<u64>", "需要 2 个类型参数"),
            ("vector<vector<u8>", "解析失败"),
            ("vector<>", "没有类型实参"),
            ("array<u64, T[]>", "解析失败"),
            ("unordered_map<vector<u8>, u64>", "故意不映射"),
            ("unordered_set<u64>", "故意不映射"),
            ("deque<u64>", "故意不映射"),
            ("pair<u64, u64>", "故意不映射"),
            ("", "空类型串"),
    ):
        got = cxx_map.cxx_type(elench)
        reason = cxx_map.unmapped_reason(elench)
        check(f"{elench!r} → None", got is None, repr(got))
        check(f"{elench!r} 的理由含「{needle}」", needle in reason, reason[:160])

    print("=== 语料回归：demo/corpus/*.tu 的类型全部可映射（18/19；08 的 unordered_map 是故意的）===")
    bad = []
    for mod, t in _corpus_type_strings():
        if t == "":
            continue                      # `()` 返回（unit）：类型层故意不猜
        if t == "<parse-fail>":
            bad.append((mod, t))
            continue
        if cxx_map.cxx_type(t) is None and "unordered_map" not in t:
            bad.append((mod, t, cxx_map.unmapped_reason(t)[:80]))
    check("语料里除 unordered_map 外没有认不出的类型", not bad, str(bad[:8]))

    print("=== 平均 O(1) 与上界不同的地方必须写明 ===")
    note = cxx_map.cxx_builtin("map_put")[1]
    check("map_put 的注记提到 rehash / 摊还", "rehash" in note or "摊还" in note, note)

    print("=== 明确不映射的有理由 ===")
    check("exec / now_ms 都有理由",
          all(cxx_map.unmapped_reason(k).strip() for k in cxx_map.UNMAPPED))

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
