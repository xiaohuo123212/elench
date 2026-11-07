#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_ops.py —— 解释器**操作数计数器**（计划 §8）

## 这份测试要钉住什么

`κ` 是「操作数上界」这句话，只有在**同一单位**下被实测压住才算数。
`src/interp.py` 的计数器就是这个"同一单位"。它有四件事必须成立：

| # | 判据 | 这里怎么测 |
|---|---|---|
| a | **计数不改语义** | 同一函数，关/开计数各跑一次，**stdout 与返回值逐字节比对** |
| b | **ops 随规模单调** | 同一函数在 n = 64 / 128 / 256 上 ops 严格递增 |
| c | **上界成立** | `tools/kappa_vs_ops.py` 的 64 / 512 / 4096 三档，**≥ 8 个函数**全成立 |
| d | **反例能抓** | **monkeypatch `cost_interp.OP_COST`**（不改源文件），工具必须报破界 |
| e | **开销可接受** | 开计数 / 关计数的单次耗时比 ≤ 3×（报告实测倍数） |

## 为什么 d 要用 monkeypatch

「工具报成立」本身不是证据——**只有它能报破界，成立才有意义**。
把 `OP_COST`（κ 的基本操作单价）从 1 调成 0，等于"把 κ 砍到贴近 0"，
如果工具还在说成立，那这个工具就是摆设。

**不改源文件**：patch 只活在这一次进程里，`finally` 里恢复。

跑法：

    cd src; $env:PYTHONIOENCODING="utf-8"; python test_ops.py
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

import contextlib
import io as _io
import os
import sys
import time

# **src 优先**：`tools/` 里也有 `.py`，不让它遮住 `interp` / `parser` 这些名字
sys.path.insert(0, os.path.join(ROOT, "tools"))

import interp                                          # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset   # noqa: E402

import kappa_vs_ops as kvo                             # noqa: E402

SAMPLES = os.path.join(ROOT, "文档", "样例")

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


#: (a) 用哪些函数做「开/关一致」——覆盖内建、方法调用、dispatch、结构体、早退路径
CONSISTENCY = [
    ("sum", "average"),
    ("count", "count"),
    ("stats", "summarize"),
    ("paren", "paren"),
    ("topk", "topk"),
    ("merge", "merge"),
    ("search", "binary_search"),
    ("matmul", "matmul"),
    ("calculator", "evaluate"),
    ("lcs", "lcs"),
]


def _run_capture(ch, tu, fn, args, counting, tbl=None):
    """跑一次，捕获 stdout；返回 (stdout, 返回值 repr, ops)。"""
    mod = interp
    old = mod._COUNT
    mod.ops_enable(counting)
    buf = _io.StringIO()
    try:
        ops_reset()
        it = Interp(ch, tu, table_path=tbl)
        with contextlib.redirect_stdout(buf):
            val = it.call(fn, args)
        return buf.getvalue(), repr(val), ops_of(fn)
    finally:
        mod.ops_enable(old)


# ---------------------------------------------------------------- (a)
def test_same_output():
    print("[a] 开/关计数：stdout 与返回值完全一致")
    for prog, fn in CONSISTENCY:
        loaded = kvo._load(SAMPLES, prog)
        if loaded is None:
            check(False, f"{prog}.{fn} 能加载", "找不到样例")
            continue
        tu, ch, tbl = loaded
        node = [f for f in ch.children if getattr(f, "name", "") == fn][0]
        args = kvo._fixture_args(prog, fn, tu, node, 64)
        if args is None:
            args = kvo._build_args(kvo._kinds_of(tu, node), tu, 64)
        try:
            out_off, val_off, _ = _run_capture(ch, tu, fn, args, False, tbl)
        except Exception as exc:                        # noqa: BLE001
            # `requires` 不成立是"输入不合"，不是计数的问题
            if "requires" not in str(exc):
                check(False, f"{prog}.{fn} 关计数能跑", f"{type(exc).__name__}: {exc}")
                continue
            args = [sorted(a) if isinstance(a, list) else a for a in args]
            out_off, val_off, _ = _run_capture(ch, tu, fn, args, False, tbl)
        out_on, val_on, ops = _run_capture(ch, tu, fn, args, True, tbl)
        same = (out_off == out_on) and (val_off == val_on)
        check(same, f"{prog}.{fn}：stdout + 返回值一致（ops={ops:,}）",
              f"stdout {out_off!r} vs {out_on!r}；值 {val_off} vs {val_on}")


# ---------------------------------------------------------------- (b)
def test_monotone():
    print("[b] ops 随规模单调（同一函数 n = 64 / 128 / 256）")
    for prog, fn in (("sum", "average"), ("stats", "summarize"),
                     ("count", "count"), ("merge", "merge"),
                     ("search", "binary_search")):
        loaded = kvo._load(SAMPLES, prog)
        if loaded is None:
            check(False, f"{prog}.{fn} 能加载")
            continue
        tu, ch, tbl = loaded
        node = [f for f in ch.children if getattr(f, "name", "") == fn][0]
        seq = []
        for n in (64, 128, 256):
            args = kvo._fixture_args(prog, fn, tu, node, n)
            if args is None:
                args = kvo._build_args(kvo._kinds_of(tu, node), tu, n)
            try:
                _, _, ops = _run_capture(ch, tu, fn, args, True, tbl)
            except Exception as exc:                    # noqa: BLE001
                if "requires" not in str(exc):
                    raise
                args = [sorted(a) if isinstance(a, list) else a for a in args]
                _, _, ops = _run_capture(ch, tu, fn, args, True, tbl)
            seq.append(ops)
        # **带分档表的模块不适用**：表会让不同规模走不同候选，
        # ops 于是可能"上一步 919、这一步 293"——那是换路，不是不增长。
        # （`search` 就是这么被排除的：n<=127 线性、n>127 二分。）
        if tbl:
            print(f"  skip {prog}.{fn}：有分档表，允许按规模换候选（{seq}）")
            continue
        check(seq[0] < seq[1] < seq[2],
              f"{prog}.{fn}：{seq} 严格递增", str(seq))


# ---------------------------------------------------------------- (c)
def test_bound():
    print("[c] ≥ 8 个函数 × 3 档（64 / 512 / 4096）：ops ≤ κ")
    sizes = [64, 512, 4096]
    rows = kvo.run(sizes)
    s = kvo.summarize(rows, sizes)
    ok = [f"{p}.{f}" for p, f in s["ok_full"]]
    check(len(ok) >= 8, f"3 档全部成立的函数 ≥ 8 个（实际 {len(ok)}：{'、'.join(ok)}）")
    check(not s["broken"], "没有破界",
          str([f"{p}.{f}@{n}" for p, f, n in s["broken"]]))
    # 每个成立的行都得是"真跑到了"（ops 随规模增长由 summarize 的 flat 检查保证）。
    # **唯一允许的例外**：`lcs` 的 `LCS_CAP = 4096` 让 (n+1)² ≤ 4096，三档 64/512/4096
    # 全部超容量，测到的只能是 Err(Capacity) 的早退路径——这不叫"成立"，工具已经
    # 把它挪出 `ok_full`，这里只确认"没有别的函数也这样"。
    flat = [f"{p}.{f}" for p, f, *_ in s["flat"]]
    # 第二个例外：分档表会让「换候选」看起来像「不增长」。
    # search.binary_search 现在带 search.dispatch.yaml（n<=127 走线性、n>127 走二分），
    # 于是 n=64/128/256 的 ops 是 919/293/326：不是不增长，是换了更便宜的候选。
    # 这正是分档表该有的行为；把它当失败等于禁止按尺寸换路。
    check(set(flat) <= {"lcs.lcs", "search.binary_search"},
          "「ops 不随规模增长」只剩两类已知例外（lcs 的上限、search 的换候选）",
          str(flat))
    # 抽样核对：κ 与 ops 都是有限正数
    triples = [(r["program"], r["function"], r["n"], r["kappa_at_n"], r["ops"])
               for r in rows if r["status"] == "ok"]
    check(all(k > 0 and o > 0 for *_, k, o in triples),
          f"{len(triples)} 个格点的 κ / ops 都是正数")


# ---------------------------------------------------------------- (d)
def test_counterexample():
    print("[d] 反例：把 cost_interp.OP_COST 调小，工具必须报破界")
    import cost_interp
    old = cost_interp.OP_COST
    cost_interp.OP_COST = 0          # 基本操作单价 1 → 0：κ 被砍到近乎 0
    try:
        rows = kvo.run([64], max_ops=500_000)
        s = kvo.summarize(rows, [64])
    finally:
        cost_interp.OP_COST = old
    check(bool(s["broken"]),
          f"报了破界（{len(s['broken'])} 个函数）",
          "工具仍然说全部成立 —— 它抓不到反例，等于摆设")
    if s["broken"]:
        p, f, n = s["broken"][0]
        check(True, f"举例：{p}.{f} @ n={n} 破界")
    # 恢复后必须回到"成立"（证明 patch 没漏到别处）
    rows2 = kvo.run([64], max_ops=500_000)
    s2 = kvo.summarize(rows2, [64])
    check(not s2["broken"], "恢复 OP_COST 后重新成立", str(s2["broken"]))


# ---------------------------------------------------------------- (e)
def test_overhead():
    print("[e] 计数开销：开/关的单次耗时比 ≤ 3×")
    loaded = kvo._load(SAMPLES, "stats")
    tu, ch, tbl = loaded
    node = [f for f in ch.children if getattr(f, "name", "") == "summarize"][0]
    args = kvo._build_args(kvo._kinds_of(tu, node), tu, 4096)

    def best_of(counting, repeats=3):
        best = None
        for _ in range(repeats):
            old = interp._COUNT
            interp.ops_enable(counting)
            try:
                ops_reset()
                it = Interp(ch, tu, table_path=tbl)
                t0 = time.perf_counter()
                it.call("summarize", args)
                dt = time.perf_counter() - t0
            finally:
                interp.ops_enable(old)
            best = dt if best is None else min(best, dt)
        return best

    t_off = best_of(False)
    t_on = best_of(True)
    ratio = t_on / t_off if t_off else float("inf")
    check(ratio <= 3.0, f"开销 {ratio:.2f}×（关 {t_off * 1000:.1f}ms / "
                        f"开 {t_on * 1000:.1f}ms，stats.summarize n=4096）")


def test_per_thread():
    """**per-thread 计数器**：多线程各加 N 次，汇总必须**一个不少**。

    旧实现 `_OPS += 1` 是读—改—写三步，GIL 只保证单条字节码原子，
    两个线程交叉就会丢增量（只会少计 ⇒ ops ≤ κ 变成假阳性）。
    """
    import threading as _th
    import interp as _ip

    _ip.ops_reset()
    _ip.ops_enable(True)

    def worker(n):
        for _ in range(n):
            _ip._bump()

    N, T = 50000, 8
    ts = [_th.Thread(target=worker, args=(N,)) for _ in range(T)]
    for x in ts:
        x.start()
    for x in ts:
        x.join()

    got = _ip.ops_of()
    check(got == N * T, "per-thread：8 线程 × 50,000 次，汇总精确无丢失",
          f"got={got} want={N * T}")
    _ip.ops_enable(False)
    return got


def main() -> int:
    test_same_output()
    test_monotone()
    test_bound()
    test_counterexample()
    test_overhead()
    test_per_thread()

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
