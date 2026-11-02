#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""kappa_vs_ops.py —— 让「κ 是上界」变成一条**可直接比大小**的命令

## 它和 `kappa_vs_measured.py` 的区别（先看这个，否则数字会读错）

| 工具 | 比的右边是什么 | 单位和 κ 同不同 | 能得出什么 |
|---|---|---|---|
| `kappa_vs_measured.py` | `evaluation_steps` / 纳秒 | **不同** | 只能看数量级 |
| **这个** | **ops（操作数）** | **相同** | **可以判 `ops ≤ κ`** |

κ 的单位是「操作」。拿它去比纳秒（或比另一套口径的步数），**比出来的大小没有意义**——
导出图里「静态比实测低」的误会就是这么来的（见计划 §8）。

## 判据只有一条

    ops(n) ≤ κ(n)   —— 对每个函数、每一档 n 都成立

`ops` 由 `src/interp.py` 的计数器给出（`_eval` / `_stmt` / 内建调用各计 1，
**粒度为整棵调用树**），`κ` 由 `src/cost_interp.py` 给出。两边口径的已知差异写在
`interp.py` 文件头的「操作数计数器」一节里，**不在这里偷偷抹平**。

## 用法

    python tools/kappa_vs_ops.py                            # 文档/样例，三档 64/512/4096
    python tools/kappa_vs_ops.py --sizes 64,256,1024
    python tools/kappa_vs_ops.py --dir 文档/入门/例子        # 换一套样例
    python tools/kappa_vs_ops.py --function topk --sizes 64,512
    python tools/kappa_vs_ops.py --json out.json            # 机器可读
    python tools/kappa_vs_ops.py --strict                   # 有破界 → 退出码 1

**反例演示**（验收 #4：工具必须抓得到破界）：

    python tools/kappa_vs_ops.py --kappa-scale 0.5 --sizes 64

`--kappa-scale` 把 κ 折小（0.5 = 砍一半），模拟「`cost_interp` 的系数被调小」。
`tests/cases/test_ops.py` 里用的是更狠的做法：**monkeypatch `cost_interp.OP_COST`**，
不改源文件也能证明工具会报破界。

## 规模轴怎么定的

- **容器实参**：长度 = n（这是规模轴）；
- **标量实参**：`max(2, n // 4)` —— 与 `kappa_vs_measured.py` 同一条规则，
  两个工具的口径不许分叉；
- **超预算**：单次调用超过 `--max-ops`（默认 300 万次求值步）就**中止并标「未测」**，
  既不算成立也不算破界——**不猜**。
"""
import argparse
import io
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

#: 默认样例目录（10 个程序，10 个带 target 的函数）
DEFAULT_DIR = os.path.join(ROOT, "文档", "样例")

#: 默认三档规模（验收 §8.3 #3）
DEFAULT_SIZES = "64,512,4096"

#: 单次调用的求值步预算：超了就「未测」，不让一条命令跑成半小时
DEFAULT_MAX_OPS = 3_000_000


# ---------------------------------------------------------------- 实参构造

def _shape(v):
    """一个值在 κ 里的「尺寸」：容器 = 长度，标量 = 它自己。"""
    if isinstance(v, (list, tuple)):
        return len(v)
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    return 0


def _build_args(kinds, tu, n, seed=1):
    """通用夹具：容器长 n、标量 n//4（与 `kappa_vs_measured.py` 同一条规则）。"""
    from bench import make_u8s, random_u64s
    from kappa_vs_measured import _mk_struct, _struct_fields

    args = []
    for kind in kinds:
        if kind == "u8vec":
            args.append(make_u8s(seed, n))
        elif kind == "u64vec":
            args.append(random_u64s(seed, n))
        elif kind.startswith("structvec:"):
            _tn = kind.split(":", 1)[1]
            _fields = _struct_fields(tu, _tn)
            # 元素个数跟着 n 走：n//4（与 kappa_vs_measured 同口径）
            args.append([_mk_struct(_fields, _tn, i + seed)
                         for i in range(max(2, n // 4))])
        else:
            args.append(max(2, n // 4))
    return args


#: **程序特定的夹具**。通用规则（容器长 n、标量 n//4）造不出合法输入的函数，
#: 在这里写清楚"什么叫规模 n"——**不是为了凑数，是因为随机字节不是有效程序**。
#:
#: | 程序.函数 | 为什么需要 | n 的含义 |
#: |---|---|---|
#: | `calculator.evaluate` / `.tokenize` | 输入必须是**合法表达式字节流**（随机字节立刻 `Err(UnexpectedByte)`，测到的是早退路径） | 字节数 |
#: | `matmul.matmul` | `requires a.length() == dim²` | `dim²`（取 ⌊√n⌋ 的平方） |
#: | `paren.paren` | 随机字节几乎必然 `Err(Unmatched)`；要**配平**才能走完全程 | 字节数 |
#: | `topk.topk` | `k > HEAP_CAP(64)` 直接 `Err(RankTooLarge)`，`k = n//4` 在 n≥512 必然撞上 | 元素数；k = min(n//4, 64) |
FIXTURES = {
    ("calculator", "evaluate"): "expr_bytes",
    ("calculator", "tokenize"): "expr_bytes",
    ("matmul", "matmul"): "square_dim",
    ("paren", "paren"): "balanced_parens",
    ("topk", "topk"): "k_capped",
}


def _fixture_args(prog, fn, tu, fn_node, n):
    """按 `FIXTURES` 造实参；没登记 → 返回 None（走通用规则）。"""
    what = FIXTURES.get((prog, fn))
    if what is None:
        return None
    if what == "expr_bytes":
        # `1+1+…+1`，末尾用空格补齐到 n 字节：token 数 n/2，远小于 MAX_TOKENS=4096
        s = b"1+" * ((n - 1) // 2) + b"1"
        s = s + b" " * (n - len(s))
        return [list(s)]
    if what == "balanced_parens":
        s = b"()" * (n // 2)
        s = s + b" " * (n - len(s))
        return [list(s)]
    if what == "k_capped":
        from bench import random_u64s
        return [random_u64s(1, n), min(max(2, n // 4), 64)]
    if what == "square_dim":
        from math import isqrt
        dim = isqrt(n)
        return [[1] * (dim * dim), [1] * (dim * dim), dim]
    raise AssertionError(f"未知夹具 {what}")


def _kinds_of(tu, fn_node):
    from kappa_vs_measured import param_kind

    params = next((c for c in fn_node.children if c.kind == "params"), None)
    return [param_kind(p) for p in (params.children if params else [])]


# ---------------------------------------------------------------- 主流程

def _load(prog_dir, prog):
    from parser import parse_source

    tu_p = os.path.join(prog_dir, prog + ".tu")
    ch_p = os.path.join(prog_dir, prog + ".eln")
    if not (os.path.exists(tu_p) and os.path.exists(ch_p)):
        return None
    tu = parse_source(io.open(tu_p, encoding="utf-8").read(), prog + ".tu").module
    ch = parse_source(io.open(ch_p, encoding="utf-8").read(), prog + ".eln").module
    tbl = os.path.join(prog_dir, prog + ".dispatch.yaml")
    return tu, ch, (tbl if os.path.exists(tbl) else None)


def _kappa_env(fn_node, args):
    """κ 的求值环境：形参名 → 实参的尺寸。"""
    params = next((c for c in fn_node.children if c.kind == "params"), None)
    pnames = [p.name for p in (params.children if params else [])]
    return {nm: _shape(a) for nm, a in zip(pnames, args)}


def measure_one(prog_dir, prog, n, max_ops=DEFAULT_MAX_OPS,
                kappa_scale=1.0, only_target=True, verbose=False):
    """跑一个程序的一档 n：返回该程序**每个函数一行**的结果。"""
    from cost_check import evaluate
    from cost_interp import CostInterp
    from interp import Interp, ops_enabled, ops_enable, ops_of, ops_reset

    loaded = _load(prog_dir, prog)
    if loaded is None:
        return []
    tu, ch, tbl = loaded
    ci = CostInterp(ch, tu, table_path=tbl)

    if not ops_enabled():
        ops_enable()

    rows = []
    for fn in sorted(ci.fns):
        if only_target and ci.target_cost(fn) is None:
            continue
        fn_node = ci.fns[fn]
        try:
            kappa = ci.cost_of(fn)
        except Exception as exc:                            # noqa: BLE001
            rows.append({"program": prog, "function": fn, "n": n,
                         "status": "kappa_error",
                         "detail": f"{type(exc).__name__}: {exc}"})
            continue

        kinds = _kinds_of(tu, fn_node)
        args = _fixture_args(prog, fn, tu, fn_node, n)
        fixture = args is not None
        if args is None:
            args = _build_args(kinds, tu, n)
        env = _kappa_env(fn_node, args)
        if kappa.is_top():
            rows.append({"program": prog, "function": fn, "n": n,
                         "status": "kappa_top", "kappa": kappa.render(),
                         "detail": "κ = ⊤（无法判定）"})
            continue
        try:
            kv = evaluate(kappa,
                          {v: env.get(v, n) for v in kappa.variables()})
        except Exception as exc:                            # noqa: BLE001
            rows.append({"program": prog, "function": fn, "n": n,
                         "status": "kappa_error",
                         "detail": f"{type(exc).__name__}: {exc}"})
            continue
        if kv is None:
            rows.append({"program": prog, "function": fn, "n": n,
                         "status": "kappa_top", "kappa": kappa.render(),
                         "detail": "κ 求不出数值"})
            continue
        # **符号项 / 外部项 / ⊤**（`Cost("sym", …)`、`ext(...)`）求值给 **inf**。
        # 这不是「κ 少算」，而是「这个点上 κ 不可判定」：**打印 inf、跳过该点**，
        # 既不算成立也不算破界。修前这里直接 `int(inf)` → OverflowError，
        # 一个 `k(g)`（函数值调用）就能把整条命令崩掉。
        if math.isnan(kv) or math.isinf(kv):
            rows.append({"program": prog, "function": fn, "n": n,
                         "status": "kappa_inf", "kappa": kappa.render(),
                         "detail": "κ 含符号/外部项（∞），不可判定，跳过该点"})
            continue
        kv = int(kv * kappa_scale)

        t0 = time.perf_counter()
        try:
            ops_reset()
            # **必须用同一张分档表**：κ 是按表算的（`_dispatch_segmented`），
            # 解释器不传表就会跑另一个候选——两边比的就不是同一个程序了。
            it = Interp(ch, tu, budget=max_ops, table_path=tbl)
            try:
                value = it.call(fn, args)
            except Exception as exc:                        # noqa: BLE001
                # **`requires` 是"调用方必须保证"的前提**：随机数据不满足
                # `requires is_sorted(xs)` 是预期的——按前提修正数据再测（同 kappa_vs_measured）。
                if "requires" not in str(exc):
                    raise
                fixed = [sorted(a) if isinstance(a, list) else a for a in args]
                ops_reset()
                it = Interp(ch, tu, budget=max_ops, table_path=tbl)
                value = it.call(fn, fixed)
            ops = ops_of(fn)
        except Exception as exc:                            # noqa: BLE001
            from interp import InterpBudgetExceeded

            spent = time.perf_counter() - t0
            over = isinstance(exc, InterpBudgetExceeded) or "执行超过" in str(exc)
            rows.append({"program": prog, "function": fn, "n": n,
                         "status": ("budget" if over else "run_error"),
                         "kappa": kappa.render(), "kappa_at_n": kv,
                         "detail": (f"超出 --max-ops={max_ops}，未测"
                                    if over else f"{type(exc).__name__}: {exc}"),
                         "seconds": round(spent, 3)})
            continue
        spent = time.perf_counter() - t0

        rows.append({
            "program": prog, "function": fn, "n": n, "status": "ok",
            "kappa": kappa.render(), "kappa_at_n": kv, "ops": ops,
            "holds": ops <= kv,
            "margin": kv - ops,
            "value": _brief(value),
            "fixture": fixture,
            "seconds": round(spent, 3),
            "params": dict(env),
        })
        if verbose:
            mark = "✅" if ops <= kv else "🔴"
            print(f"    {prog}.{fn} n={n}: κ={kv:,} ops={ops:,} {mark}"
                  f"  ({spent:.2f}s)")
    return rows


def run(sizes, programs_dir=DEFAULT_DIR, max_ops=DEFAULT_MAX_OPS,
        kappa_scale=1.0, only_target=True, functions=None, verbose=False):
    progs = sorted(f[:-3] for f in os.listdir(programs_dir) if f.endswith(".tu"))
    rows = []
    for prog in progs:
        if functions and prog not in functions:
            continue
        for n in sizes:
            got = measure_one(programs_dir, prog, n, max_ops=max_ops,
                              kappa_scale=kappa_scale, only_target=only_target,
                              verbose=verbose)
            rows.extend(got)
    return rows


# ---------------------------------------------------------------- 输出

def _brief(v, width=72):
    s = repr(v)
    return s if len(s) <= width else s[:width] + "…"


def _index(rows):
    """{(程序, 函数): {n: row}}"""
    out = {}
    for r in rows:
        out.setdefault((r["program"], r["function"]), {})[r["n"]] = r
    return out


def summarize(rows, sizes):
    """把行汇总成结论。**只许如实报告**，不许把“未测”算成“成立”。"""
    by_fn = _index(rows)
    ok_full, broken, incomplete, flat, symbolic = [], [], [], [], []
    for (prog, fn), per_n in sorted(by_fn.items()):
        good = {n: r for n, r in per_n.items() if r["status"] == "ok"}
        bad = [n for n, r in good.items() if not r.get("holds", False)]
        # **κ 含符号/外部项（inf）：不可判定**。这不是「未测」，也不是破界——
        # 单独一栏报告，`--strict` 不因它失败（否则 `k(g)` 这类契约项会让
        # 整条命令永远红）。**它也不进 ok_full**：inf 不是证据。
        if any(r["status"] == "kappa_inf" for r in per_n.values()):
            symbolic.append((prog, fn))
            continue
        if bad:
            broken.append((prog, fn, sorted(bad)))
            continue
        if set(good) == set(sizes) and len(good) == len(sizes):
            seq = [good[n]["ops"] for n in sorted(sizes)]
            # **ops 不随规模增长 = 根本没跑到正题**（输入被拒 / 容量挡住）。
            # 这种"成立"是空的，不能算证据。**只有一档时判断不了**，跳过这条检查。
            if len(sizes) >= 2 and seq[-1] <= seq[0]:
                flat.append((prog, fn, seq, good[max(sizes)].get("value", "")))
            else:
                ok_full.append((prog, fn))
            continue
        missing = [n for n in sizes if n not in good]
        incomplete.append((prog, fn, len(good), len(sizes), missing))
    return {"ok_full": ok_full, "broken": broken, "incomplete": incomplete,
            "flat": flat, "symbolic": symbolic, "total_functions": len(by_fn)}


def render_table(rows, sizes):
    by_fn = _index(rows)
    lines = []
    head = f"  {'程序':<14} {'函数':<16} " + " ".join(
        f"{'n=' + str(n):>9} {'κ(n)':>12} {'ops(n)':>10} {'≤κ?':>4}" for n in sizes)
    lines.append(head)
    lines.append("  " + "-" * (len(head) - 2))
    for (prog, fn), per_n in sorted(by_fn.items()):
        cells = []
        for n in sizes:
            r = per_n.get(n)
            if r is None:
                cells.append(f"{'-':>9} {'-':>12} {'-':>10} {'-':>4}")
                continue
            if r["status"] != "ok":
                if r["status"] == "kappa_inf":
                    # 第 ④ 类：κ 含 `k(g)` / `ext` 符号 ⇒ 数值上是 inf。
                    # **照实打印 inf 并跳过**，不装成数字。
                    cells.append(f"{n:>9} {'inf':>12} {'跳过':>10} {'-':>4}")
                    continue
                why = {"budget": "未测(预算)", "kappa_top": "κ=⊤",
                       "kappa_error": "κ 出错", "run_error": "跑不了"}.get(
                           r["status"], r["status"])
                cells.append(f"{n:>9} {'-':>12} {why:>10} {'-':>4}")
                continue
            mark = "✅" if r["holds"] else "🔴"
            cells.append(f"{n:>9} {r['kappa_at_n']:>12,} {r['ops']:>10,} {mark:>4}")
        lines.append(f"  {prog:<14} {fn:<16} " + " ".join(cells))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="κ vs 实测操作数（同一单位：操作）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", default=DEFAULT_SIZES, help="规模档，逗号分隔")
    ap.add_argument("--dir", dest="prog_dir", default=DEFAULT_DIR, help="样例目录")
    ap.add_argument("--function", default=None, help="只测这些程序（逗号分隔）")
    ap.add_argument("--all-functions", action="store_true",
                    help="连没有 target 的函数也测")
    ap.add_argument("--max-ops", type=int, default=DEFAULT_MAX_OPS,
                    help=f"单次调用的步预算（默认 {DEFAULT_MAX_OPS:,}）")
    ap.add_argument("--kappa-scale", type=float, default=1.0,
                    help="把 κ 乘一个系数（< 1 用于演示“破界能抓到”）")
    ap.add_argument("--json", dest="json_out", help="把结果写成 JSON")
    ap.add_argument("--strict", action="store_true", help="有破界时退出码 1")
    ap.add_argument("--quiet", action="store_true", help="只打结论")
    args = ap.parse_args(argv)

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    funcs = [s for s in (args.function or "").split(",") if s] or None

    t0 = time.perf_counter()
    rows = run(sizes, programs_dir=args.prog_dir, max_ops=args.max_ops,
               kappa_scale=args.kappa_scale,
               only_target=not args.all_functions, functions=funcs)
    spent = time.perf_counter() - t0

    print(f"# κ vs 操作数（同一单位） 目录={os.path.relpath(args.prog_dir, ROOT)}"
          f"  档={sizes}")
    if args.kappa_scale != 1.0:
        print(f"# ⚠ 反例模式：κ 已被乘以 {args.kappa_scale}（不是真实上界）")
    print()
    if not args.quiet:
        print(render_table(rows, sizes))
        print()

    s = summarize(rows, sizes)
    print("## 结论")
    print(f"  · 有 target 的函数：{s['total_functions']} 个")
    if s["ok_full"]:
        names = "、".join(f"{p}.{f}" for p, f in s["ok_full"])
        print(f"  · **{len(sizes)} 档全部成立（ops ≤ κ）：{len(s['ok_full'])} 个** —— {names}")
    else:
        print(f"  · {len(sizes)} 档全部成立：0 个")
    if s["broken"]:
        print(f"  · **破界（ops > κ）：{len(s['broken'])} 个**")
        for prog, fn, bad in s["broken"]:
            print(f"      🔴 {prog}.{fn}  n={bad}")
    else:
        print("  · 破界：0 个")
    if s["flat"]:
        print(f"  · **ops 不随 n 增长（测到的是早退路径，不作为证据）：{len(s['flat'])} 个**")
        for prog, fn, seq, val in s["flat"]:
            print(f"      {prog}.{fn}：ops={seq} 末档返回 {val}")
    if s["incomplete"]:
        print(f"  · 未测满 {len(sizes)} 档：{len(s['incomplete'])} 个")
        for prog, fn, got, want, missing in s["incomplete"]:
            print(f"      {prog}.{fn}：测到 {got}/{want} 档（缺 n={missing}，"
                  f"规模过大或跑不动）")
        print("      未测 ≠ 成立：调大 --max-ops 才能给出判定"
              "（`matmul` 在 dim=64 就是这样一条：默认 300 万预算不够，"
              "调大后才有判定——E36 修完是成立，修前是破界）")
    if s["symbolic"]:
        names = "、".join(f"{p}.{f}" for p, f in s["symbolic"])
        print(f"  · **κ 含符号/外部项（inf，跳过该点，不作判定）："
              f"{len(s['symbolic'])} 个** —— {names}")
        print("      `k(g)` / `ext(...)` 是**契约项**：κ 本身不可数值化，"
              "所以这一栏既不算成立也不算破界（打印 inf，不崩）")
    print("  · 判据：`ops(n) ≤ κ(n)`；ops 单位是**操作**，不是纳秒。")
    print(f"  · 用时 {spent:.1f}s（预算 --max-ops={args.max_ops:,}）")

    if args.json_out:
        payload = {"sizes": sizes, "dir": os.path.relpath(args.prog_dir, ROOT),
                   "kappa_scale": args.kappa_scale, "max_ops": args.max_ops,
                   "rows": rows, "summary": s,
                   "seconds": round(spent, 3)}
        io.open(args.json_out, "w", encoding="utf-8", newline="\n").write(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    if args.strict and (s["broken"] or len(s["ok_full"]) < 8
                        or len(sizes) < 3):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
