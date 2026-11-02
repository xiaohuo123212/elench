#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

"""kappa_vs_measured.py —— 把「κ 是不是真上界」变成一条可复现的命令

**为什么要这个脚本（而不是一次性探针）**

论文里我们只主张两件事，其中一件是「成本签名是一个可测量量」。
这句话的**唯一证据**是这张表：**推导出的上界 κ，是否压得住最坏实测**。

之前它是临时写在会话里的探针——**跑完就没了，别人无法复现**。
这个脚本把它固定下来：一条命令、固定的种子、可 diff 的输出。

**一条硬规则**

> **κ 是「上界」，所以它必须 ≥ 最坏情况的实际步数，不是 ≥ 某一次随机输入。**
> 所以默认 `--runs 20`：**每个程序跑 20 组输入，取 max**。
> 只比一次会得出错误结论（`topk` 的 max/min 差 1.8 倍）。

**用法**

    python tools/kappa_vs_measured.py                       # 终端打表
    python tools/kappa_vs_measured.py --json out.json       # 存机器可读结果
    python tools/kappa_vs_measured.py --md out.md           # 存 markdown 表
    python tools/kappa_vs_measured.py --strict              # 有 κ < max 时退出码 1

**它测的是什么（看数字前先看懂这三个量）**

| 量 | 含义 |
|---|---|
| `κ(n)` | 编译器从实现推出来的渐近上界，在 n 处求值 |
| `max 实测` | 解释器真跑一遍，`evaluation_steps` 的**最大值**（20 组输入） |
| `κ ≥ max` | **唯一重要的判据**：上界是否压得住最坏情况 |
"""
import argparse
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
SAMPLES = os.path.join(ROOT, "文档", "设计", "目前设计", "样例")
sys.path.insert(0, SRC)

PROGRAMS = ["calculator", "search", "sum", "stats", "merge",
            "paren", "count", "matmul", "lcs", "topk"]


def param_kind(p):
    """参数是什么形状：u8vec / u64vec / structvec:<类型> / scalar。

    **以前这里只看"一层"类型实参，把 `Vec<u8>` 误判成 u64vec**——
    于是给 `count` 灌了 0..2^32 的整数，桶数组 `buckets[b]` 直接越界。
    现在把类型子树里**所有**类型名收集起来判断，并对 `Vec<结构体>` 单独分类
    （`merge` 要的是 `Vec<Interval>`，不是一串整数）。
    """
    ty = p.children[0] if p.children else None
    if ty is None:
        return "scalar"
    from bound import _descendants
    names = [n.name for n in _descendants(ty, "type-name") if n.name]
    if ty.name:
        names.insert(0, ty.name)
    joined = "/".join(names)
    if "Vec" not in joined and ty.kind != "type-ref":
        return "scalar"
    if "u8" in joined:
        return "u8vec"
    # Vec<结构体>：元素类型名不是标量
    for cand in ("Interval", "Stats", "Point", "Node", "Pair"):
        if cand in joined:
            return "structvec:" + cand
    return "u64vec"


def _mk_struct(fields, name, i):
    """按 `.tu` 里的字段顺序造一个结构体值（数值字段给 i / i+1）。"""
    from interp import StructVal
    vals = {}
    for idx, f in enumerate(fields):
        vals[f] = i + idx
    return StructVal(name, vals)


def _struct_fields(tu, type_name):
    from bound import _descendants
    for st in _descendants(tu, "struct"):
        if (st.name or "") == type_name:
            return [f.name for f in st.children if getattr(f, "kind", "") == "field"]
    return []


def measure_one(prog, n, runs, args_override=None):
    """返回该程序里有 target 的函数的对照行。"""
    from bench import make_u8s, random_u64s
    from cost_check import evaluate
    from cost_interp import CostInterp
    from interp import Interp
    from parser import parse_source

    # **`prog` 可以是名字（在样例目录里找），也可以是路径**（任意模块）。
    # 以前只认名字，于是 `文档/入门/例子/crossover.tu` 报"没有可对照的函数"。
    if prog.endswith(".tu") or os.path.sep in prog or "/" in prog:
        _base = prog[:-3] if prog.endswith(".tu") else prog
        tu_p = _base + ".tu"
        ch_p = _base + ".eln"
    else:
        tu_p = os.path.join(SAMPLES, prog + ".tu")
        ch_p = os.path.join(SAMPLES, prog + ".eln")
    if not (os.path.exists(tu_p) and os.path.exists(ch_p)):
        return []

    tu = parse_source(io.open(tu_p, encoding="utf-8").read(), prog + ".tu").module
    ch = parse_source(io.open(ch_p, encoding="utf-8").read(), prog + ".eln").module
    _tbl = os.path.join(os.path.dirname(os.path.abspath(tu_p)),
                          os.path.basename(tu_p).rsplit(".", 1)[0] + ".dispatch.yaml")
    ci = CostInterp(ch, tu, table_path=(_tbl if os.path.exists(_tbl) else None))

    rows = []
    for fn in sorted(ci.fns):
        if ci.target_cost(fn) is None:
            continue
        kappa = ci.cost_of(fn)

        params = next((c for c in ci.fns[fn].children if c.kind == "params"), None)
        kinds = [param_kind(p) for p in (params.children if params else [])]

        # **数据从哪来**：模块写了 `benchmodel.data` 就用它（与 `measure` 同一份），
        # 否则才退回「按形参类型造」。以前一律按类型造，于是同一个样例
        # （如 `count` 声明了 `make_texts`）在 `measure` 与这里吃两套数据。
        _bm = None
        try:
            from bench import _bench_plan, _build_args
            _bm = _bench_plan(tu)
        except Exception:                              # noqa: BLE001
            _bm = None

        acts, ops_seen, err = [], [], None
        for seed in range(1, runs + 1):
            args = []
            if _bm is not None and args_override is None:
                _data, _sized, _seed0 = _bm
                args = _build_args(_data, _sized, n, seed)
            if args_override is not None:
                # **对抗输入**（tools/worstcase.py）复用同一条测量口径：
                # 同一份解释器、同一种计数、同一条 requires 修正逻辑。
                import copy as _copy
                args = [_copy.deepcopy(a) for a in args_override]
            # 给了 args_override 就**不再**按 param_kind 构造（否则会多出实参）
            for kind in ([] if (args_override is not None or _bm is not None) else kinds):
                if kind == "u8vec":
                    args.append(make_u8s(seed, n))
                elif kind == "u64vec":
                    args.append(random_u64s(seed, n))
                elif kind.startswith("structvec:"):
                    _tn = kind.split(":", 1)[1]
                    _fields = _struct_fields(tu, _tn)
                    args.append([_mk_struct(_fields, _tn, i + seed)
                                 for i in range(max(2, n // 4))])
                else:
                    args.append(max(2, n // 4))
            try:
                it = Interp(ch, tu)
                try:
                    it.call(fn, args)
                except Exception as _exc:                 # noqa: BLE001
                    # **requires 是"调用方必须保证"的前提**：随机数据不满足
                    # `requires is_sorted(xs)` 是预期的——这里按前提修正数据再测。
                    if "requires" not in str(_exc):
                        raise
                    fixed = [sorted(a) if isinstance(a, list) else a for a in args]
                    it = Interp(ch, tu)
                    it.call(fn, fixed)
                acts.append(it.counters()["evaluation_steps"])
                ops_seen.append(it.counters().get("operations", 0))
            except Exception as exc:                       # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                break

        if not acts:
            rows.append({"program": prog, "function": fn, "error": err,
                         "kappa": kappa.render()})
            continue

        # **关键**：κ 里的每个变量都要用"真实实参"代入，不能一律填 n。
        # `topk(xs, k)` 的 k 在样例里是 n/4，若把 k 也填成 n，κ 会被算大；
        # 若只填第一个变量，k 会被当 1，κ 被算小——**E34 就是这么来的**。
        _env = {}

        def _shape(a):
            return len(a) if isinstance(a, (list, tuple)) else int(a)

        try:
            _pnames = [p.name for p in (params.children if params else [])]
            for _nm, _a in zip(_pnames, args):
                _env[_nm] = _shape(_a)
        except Exception:                                  # noqa: BLE001
            pass
        kv = evaluate(kappa, {v: _env.get(v, n) for v in kappa.variables()})
        _tgt = ci.target_cost(fn)
        try:
            _tv = float(evaluate(_tgt, {v: _env.get(v, n) for v in _tgt.variables()})) \
                if _tgt is not None else None
        except Exception:                                  # noqa: BLE001
            _tv = None
        rows.append({
            "program": prog, "function": fn,
            "kappa": kappa.render(), "kappa_at_n": kv,
            "target_at_n": _tv,
            "max_actual": max(acts), "min_actual": min(acts),
            "runs": len(acts),
            "holds": kv >= max(acts),
        })
    return rows


def render_md(rows, n, runs):
    out = [f"# κ 与实测对照（n = {n}，每函数 {runs} 组输入取最坏）", "",
           "> 由 `tools/kappa_vs_measured.py` 生成，不要手改。",
           "> 判据只有一条：**κ ≥ max(实测)**。", "",
           "| 程序 | 函数 | κ(n) | max 实测 | min 实测 | κ ≥ max |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            out.append(f"| `{r['program']}` | `{r['function']}` | — | 跑不了 | — | "
                       f"{r['error'][:40]} |")
            continue
        mark = "✅" if r["holds"] else f"🔴 差 {r['max_actual'] - int(r['kappa_at_n'])}"
        out.append(f"| `{r['program']}` | `{r['function']}` | {r['kappa_at_n']:.0f} | "
                   f"{r['max_actual']} | {r['min_actual']} | {mark} |")
    ok = sum(1 for r in rows if r.get("holds"))
    total = sum(1 for r in rows if "holds" in r)
    out += ["", f"**达标：{ok} / {total}**"]
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="κ 是否压得住最坏实测")
    ap.add_argument("--n", type=int, default=16, help="输入规模（默认 16）")
    ap.add_argument("--runs", type=int, default=20, help="每个程序的输入组数（默认 20）")
    ap.add_argument("--json", dest="json_out", help="把结果写成 JSON")
    ap.add_argument("--md", dest="md_out", help="把结果写成 markdown 表")
    ap.add_argument("--strict", action="store_true", help="有 κ < max 实测时退出码 1")
    args = ap.parse_args(argv)

    rows = []
    for prog in PROGRAMS:
        rows.extend(measure_one(prog, args.n, args.runs))

    if args.md_out:
        io.open(args.md_out, "w", encoding="utf-8", newline="\n").write(
            render_md(rows, args.n, args.runs))
    if args.json_out:
        io.open(args.json_out, "w", encoding="utf-8", newline="\n").write(
            json.dumps({"n": args.n, "runs": args.runs, "rows": rows},
                       ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    ok = sum(1 for r in rows if r.get("holds"))
    total = sum(1 for r in rows if "holds" in r)
    skipped = sum(1 for r in rows if "error" in r)
    print(render_md(rows, args.n, args.runs))
    print(f"（共 {len(rows)} 个函数：{ok}/{total} 达标，{skipped} 个构造实参失败）")
    if args.strict and ok != total:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
