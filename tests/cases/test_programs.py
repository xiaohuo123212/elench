#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_programs.py —— M1 十个手写程序（批次 0）

**判据来自计划 §四**：每个程序 MUST 给出**手算期望值**，且解释器跑出来一致。

**当前进度**：
  1  calculator  ✅（已有，在 test_interp 里）
  2  search      ✅（已有，在 test_interp 里）
  3  sum         ✅（已有，在 test_interp 里）
  4  stats       ✅ 本文件
  5-10           ⏳ 未写

运行：
  cd src; $env:PYTHONIOENCODING="utf-8"; python test_programs.py
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

sys.stdout.reconfigure(encoding="utf-8")

from interp import Err, Interp, Ok
from parser import parse_source

SAMPLE = os.path.join(HERE, "..", "文档", "样例")

_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def load(base):
    tu = parse_source(open(os.path.join(SAMPLE, base + ".tu"), encoding="utf-8").read(),
                      base + ".tu").module
    ch = parse_source(open(os.path.join(SAMPLE, base + ".eln"), encoding="utf-8").read(),
                      base + ".eln").module
    return Interp(ch, tu)


def unwrap(v):
    """`T!` 函数返回 Ok(x)，取出 x。"""
    return v.v if isinstance(v, Ok) else v


# ---------------------------------------------------------------- 程序 4：stats

def test_stats():
    print("[4] stats —— struct 聚合 + 两遍扫描 + 同阶两候选")
    it = load("stats")

    # 手算期望值（与 stats.eln 头部注释一致）
    CASES = [
        ([1, 2, 3, 4],    (2, 1, 1, 4)),
        ([5],             (5, 0, 5, 5)),
        ([4, 1, 3, 2, 5], (3, 1, 1, 5)),  # 偏差和 6 / 5 = 1（整除）
        ([10, 10, 10],    (10, 0, 10, 10)),
    ]
    for xs, want in CASES:
        got_val = unwrap(it.call("summarize", [xs]))
        f = getattr(got_val, "fields", None)
        got = ((f["mean"], f["variance"], f["min_value"], f["max_value"])
               if f else None)
        check(got == want, f"summarize({xs})", f"实际 {got}，期望 {want}")

    # 空数组 → Err（requires + 显式分支）
    r = it.call("summarize", [[]])
    check(isinstance(r, Err), "summarize([]) → Err", str(r))

    # 两个候选必须给出同一个和（同阶、只差常数）
    s1 = it.call("summarize", [[7, 8, 9]], force={"sum_kernel": "sum_forward"})
    s2 = it.call("summarize", [[7, 8, 9]], force={"sum_kernel": "sum_backward"})
    v1 = unwrap(s1).fields["mean"]
    v2 = unwrap(s2).fields["mean"]
    check(v1 == v2 == 8, "两个候选算出同一个均值", f"{v1} vs {v2}")


# ---------------------------------------------------------------- 程序 8：matmul

def test_matmul():
    print("[8] matmul —— 二维扁平 + 三层嵌套")
    it = load("matmul")

    CASES = [
        ([1, 2, 3, 4], [5, 6, 7, 8], 2, [19, 22, 43, 50]),
        ([1], [7], 1, [7]),
        ([1, 0, 0, 1], [1, 2, 3, 4], 2, [1, 2, 3, 4]),
        ([1, 2, 3, 4, 5, 6, 7, 8, 9],
         [1, 0, 0, 0, 1, 0, 0, 0, 1], 3, [1, 2, 3, 4, 5, 6, 7, 8, 9]),
    ]
    for a, b, dim, want in CASES:
        got = unwrap(it.call("matmul", [a, b, dim]))
        check(got == want, f"matmul(dim={dim})", f"实际 {got}，期望 {want}")

    # 维度不符 → Err
    r = it.call("matmul", [[1, 2, 3], [1, 2, 3], 2])
    check(isinstance(r, Err), "长度 ≠ dim² → Err", str(r))


# ---------------------------------------------------------------- 程序 5：merge

def test_merge():
    print("[5] merge —— vector<struct> + 原地写 + 边界")
    from interp import StructVal
    it = load("merge")

    def iv(s, f):
        return StructVal("Interval", {"start": s, "finish": f})

    def run(pairs):
        r = it.call("merge", [[iv(a, b) for a, b in pairs]])
        out = unwrap(r)
        if not isinstance(out, list):
            return out
        return [(e.fields["start"], e.fields["finish"]) for e in out]

    CASES = [
        ([(1, 3), (2, 6), (8, 10), (15, 18)], [(1, 6), (8, 10), (15, 18)]),
        ([(1, 4), (4, 5)], [(1, 5)]),            # 相邻（端点接触）
        ([(1, 10), (2, 3)], [(1, 10)]),          # 完全包含
        ([(5, 7)], [(5, 7)]),                    # 单元素
        ([], []),                                # 空
        ([(3, 4), (1, 2)], [(1, 2), (3, 4)]),    # 乱序 → 先排序
    ]
    for pairs, want in CASES:
        got = run(pairs)
        check(got == want, f"merge({pairs})", f"实际 {got}，期望 {want}")


# ---------------------------------------------------------------- 程序 6：paren

def test_paren():
    print("[6] paren —— array 当栈 + 容量边界")
    it = load("paren")

    def run(b):
        r = it.call("paren", [list(b)])
        if isinstance(r, Ok):
            return ("ok", r.v)
        if isinstance(r, Err):
            return ("err", getattr(r.e, "variant", None))
        return ("?", r)

    CASES = [
        (b"()", ("ok", 1)),
        (b"(())", ("ok", 1)),
        (b"", ("ok", 1)),                        # 空串匹配
        (b"(()", ("err", "Unclosed")),
        (b")(", ("err", "Unmatched")),
        (b"(" * 64 + b")" * 64, ("ok", 1)),      # 正好 64 层：容量边界内
        (b"(" * 65, ("err", "TooDeep")),         # 65 层：超容量，程序自己挡住
    ]
    for b, want in CASES:
        got = run(b)
        label = b[:10].decode(errors="replace") + ("…" if len(b) > 10 else "")
        check(got == want, f"paren({label!r}, len={len(b)})",
              f"实际 {got}，期望 {want}")

    # 定长数组本身的边界：push 超过 N 必须抛（不是静默扩容）
    from interp import Interp
    src = """u64! f(u64 k) {
    array<u64, 3> s = array<u64, 3>::make();
    u64 i = 0;
    while (i < k) {
        s.push(i);
        i = i + 1;
    }
    return s.length();
}"""
    it2 = Interp(parse_source(src, "fx.eln").module, None)
    check(it2.call("f", [3]) == Ok(3), "array<u64,3> 装 3 个 → 正常")
    try:
        it2.call("f", [4])
        check(False, "array<u64,3> 装第 4 个应报错")
    except Exception as e:                      # noqa: BLE001
        check("容量溢出" in str(e), "超容量报「容量溢出」", str(e)[:50])


# ---------------------------------------------------------------- 程序 7：count

def test_count():
    print("[7] count —— 桶数组原地写 + 两候选（同结果、不同阶）")
    it = load("count")

    def run(b, cand=None):
        f = {"freq_kernel": cand} if cand else None
        r = it.call("count", [list(b)], force=f)
        if isinstance(r, Ok):
            return ("ok", r.v)
        if isinstance(r, Err):
            return ("err", getattr(r.e, "variant", None))
        return ("?", r)

    CASES = [(b"aabbb", 3), (b"abcdef", 1), (b"aaaa", 4), (b"1231", 2)]
    for b, want in CASES:
        for cand in (None, "freq_bucket", "freq_pairwise"):
            got = run(b, cand)
            check(got == ("ok", want), f"count({b!r}, {cand or '默认'})",
                  f"实际 {got}，期望 {('ok', want)}")

    check(run(b"") == ("err", "Empty"), "空串 → Err(Empty)", str(run(b"")))


# ---------------------------------------------------------------- 程序 10：lcs

def test_lcs():
    print("[10] lcs —— 二维 DP 表 + 定长容量边界")
    it = load("lcs")

    def run(a, b):
        r = it.call("lcs", [list(a), list(b)])
        if isinstance(r, Ok):
            return ("ok", r.v)
        if isinstance(r, Err):
            return ("err", getattr(r.e, "variant", None))
        return ("?", r)

    CASES = [
        (b"ABCBDAB", b"BDCABA", 4),
        (b"AGGTAB", b"GXTXAYB", 4),
        (b"ABC", b"ABC", 3),
        (b"", b"ABC", 0),                       # 空串 → 0
        (b"ABC", b"ACB", 2),
        (b"XMJYAUZ", b"MZJAWXU", 4),
    ]
    for a, b, want in CASES:
        got = run(a, b)
        check(got == ("ok", want), f"lcs({a!r}, {b!r})", f"实际 {got}，期望 {('ok', want)}")

    # 定长 DP 表的容量边界：63×63 正好 64×64=4096 格；64×64 需要 65×65=4225 → 超
    check(run(b"A" * 63, b"A" * 63) == ("ok", 63), "63×63 装得下（4096 格）")
    check(run(b"A" * 64, b"A" * 64) == ("err", "Capacity"), "64×64 超容量 → Err(Capacity)",
          str(run(b"A" * 64, b"A" * 64)))


# ---------------------------------------------------------------- 程序 9：topk

def test_topk():
    print("[9] topk —— 手工堆 + 两候选（结果必须一致）")
    it = load("topk")

    def run(xs, k, cand=None):
        f = {"topk_kernel": cand} if cand else None
        r = it.call("topk", [list(xs), k], force=f)
        if isinstance(r, Ok):
            return ("ok", r.v)
        if isinstance(r, Err):
            return ("err", getattr(r.e, "variant", None))
        return ("?", r)

    CASES = [
        ([5, 1, 9, 3], 2, 5),
        ([5, 1, 9, 3], 1, 9),
        ([5, 1, 9, 3], 4, 1),
        ([7, 7, 7], 2, 7),                    # 重复值按名次算，不跳过
        ([1, 2, 3, 4, 5], 3, 3),
        ([9, 8, 7], 3, 7),
    ]
    for xs, k, want in CASES:
        for cand in (None, "topk_heap", "topk_select"):
            got = run(xs, k, cand)
            check(got == ("ok", want), f"topk({xs}, k={k}, {cand or '默认'})",
                  f"实际 {got}，期望 {('ok', want)}")

    # 边界：k 越界
    check(run([1, 2], 3)[0] == "err", "k > n → Err", str(run([1, 2], 3)))
    check(run([1, 2], 0)[1] == "RankZero", "k = 0 → Err(RankZero)", str(run([1, 2], 0)))


def main():
    test_stats()
    test_matmul()
    test_merge()
    test_paren()
    test_count()
    test_lcs()
    test_topk()

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项（10 个程序全部覆盖）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
