#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_parallel.py —— 规格 §13.6（B-23…B-27）的回归测试

覆盖四件事：
1. **语义**：`parallel` 先按顺序执行（v0.71 的顺序模拟）；
2. **成本**：κ = Σ（B-24），因此两个任务各 `n` 时 κ 里出现两个 `n(xs)`；
3. **B-25**：`parallel` 里不能有 `?!` → `E-Parallel-Dispatch`；
4. **B-26**：`?!` 候选里不能有 `parallel` → `E-Parallel-Measured`。
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
import io
import json
import os
import sys
import tempfile


import dsh                                              # noqa: E402
from checker import check_module                       # noqa: E402
from cost_check import compare                          # noqa: E402
from cost_domain import par as parv                     # noqa: E402
from cost_interp import CostInterp                      # noqa: E402
from interp import Interp                               # noqa: E402
from parser import parse_source                         # noqa: E402

FAILED = []

TU = """spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

TU_CHOOSE = """spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
    choose kernel(const vector<u64>& xs) -> u64 {
        over { by_a, by_b };
        objective time;
    };
}
"""


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(code, tu_src=TU, args=None):
    tu = parse_source(tu_src, "m.tu").module
    ch = parse_source(code, "m.eln").module
    return Interp(ch, tu).call("f", args if args is not None else [[1, 2, 3]])


def codes(code, tu_src=TU):
    return [d.code for d in check_module(tu_src, code, "m").diagnostics]


#: 4 个**均等**任务（2026-11-09：par 结构的回归样本）
TASKS4 = """u64 f(const vector<u64>& xs) {
    u64 a = 0;
    u64 b = 0;
    u64 c = 0;
    u64 d = 0;
    parallel {
        { for (u64 i = 0; i < xs.length(); i = i + 1) { a = a + 1; } }
        { for (u64 i = 0; i < xs.length(); i = i + 1) { b = b + 1; } }
        { for (u64 i = 0; i < xs.length(); i = i + 1) { c = c + 1; } }
        { for (u64 i = 0; i < xs.length(); i = i + 1) { d = d + 1; } }
    }
    return a + b + c + d;
}
"""

#: 4 个**不均衡**任务（3:1:1:1）
SKEW4 = """u64 f(const vector<u64>& xs) {
    u64 a = 0;
    u64 b = 0;
    u64 c = 0;
    u64 d = 0;
    parallel {
        { for (u64 i = 0; i < xs.length(); i = i + 1) { a = a + 1; }
          for (u64 i = 0; i < xs.length(); i = i + 1) { a = a + 2; }
          for (u64 i = 0; i < xs.length(); i = i + 1) { a = a + 3; } }
        { for (u64 i = 0; i < xs.length(); i = i + 1) { b = b + 1; } }
        { for (u64 i = 0; i < xs.length(); i = i + 1) { c = c + 1; } }
        { for (u64 i = 0; i < xs.length(); i = i + 1) { d = d + 1; } }
    }
    return a + b + c + d;
}
"""

#: 只有 1 个任务（退化情形）
SINGLE = """u64 f(const vector<u64>& xs) {
    u64 a = 0;
    parallel {
        { for (u64 i = 0; i < xs.length(); i = i + 1) { a = a + 1; } }
    }
    return a;
}
"""

#: 阶**不同**的任务：一个 O(n²) + 三个 O(n) → 整块 O(n²)
MIXED = """u64 f(const vector<u64>& xs) {
    u64 a = 0;
    u64 b = 0;
    parallel {
        { for (u64 i = 0; i < xs.length(); i = i + 1) {
              for (u64 j = 0; j < xs.length(); j = j + 1) { a = a + 1; }
          } }
        { for (u64 i = 0; i < xs.length(); i = i + 1) { b = b + 1; } }
    }
    return a + b;
}
"""

#: 嵌套 parallel
NESTED = """u64 f(const vector<u64>& xs) {
    u64 a = 0;
    u64 b = 0;
    parallel {
        { parallel {
            { for (u64 i = 0; i < xs.length(); i = i + 1) { a = a + 1; } }
            { for (u64 j = 0; j < xs.length(); j = j + 1) { a = a + 2; } }
        } }
        { for (u64 k = 0; k < xs.length(); k = k + 1) { b = b + 1; } }
    }
    return a + b;
}
"""


def test_par_view(tu) -> None:
    """**最大核计账**（2026-11-09）：κ 保留任务结构 / 判阶 max / 总量 Σ / 墙钟估计。

    守四件事：

    1. `par` 节点在 κ 里，**逐任务看得见**（不再压平成一个和）；
    2. 判阶按 **max**（B-24），总量 `evaluate` 按 **Σ**；
    3. 瓶颈 = 阶最大者；同阶取规模化值最大者（3:1:1:1 → 任务0）；
    4. **κ 与机器档无关，`wall_est` 有关**——这是大小核唯一的正确接缝。
    """
    print("=== 成本：par 保留任务结构（B-24 两条口径都直接可读）===")
    ci4 = CostInterp(parse_source(TASKS4, "m.eln").module, tu)
    k4 = ci4.cost_of("f")
    check("κ 里有 par(...)（不再是压平的和）", "par(" in k4.render(), k4.render())
    check("4 个任务的 n(xs) 都在", k4.render().count("n(xs)") == 4, k4.render())
    v4 = compare(k4, ci4.target_cost("f"))
    check("判阶取 max：4 个 O(n) 任务 vs O(n) → 达成", v4.kind == "ok", v4.render())

    blocks = parv.par_blocks(k4)
    check("识别出 1 个并行块", len(blocks) == 1, str(len(blocks)))
    tasks = blocks[0][1] if blocks else []
    check("块里有 4 个任务", len(tasks) == 4, str(len(tasks)))
    bi, how = parv.bottleneck(tasks, parv.primary_var(k4))
    check("均等任务：瓶颈取任务0（同值取下标小）", bi == 0 and how == "order", f"{bi}/{how}")

    # ---- 3:1:1:1：阶相同，取规模化值最大者
    ci_sk = CostInterp(parse_source(SKEW4, "m.eln").module, tu)
    k_sk = ci_sk.cost_of("f")
    t_sk = parv.par_blocks(k_sk)[0][1]
    bi2, how2 = parv.bottleneck(t_sk, parv.primary_var(k_sk))
    check("不均衡 3:1:1:1 → 瓶颈是任务0（系数最大）",
          bi2 == 0 and how2 == "order", f"{bi2}/{how2}")
    check("不均衡：每个任务阶都是 n",
          [parv.order_text(parv.order_of(t, "xs")) for t in t_sk] == ["n"] * 4,
          str([parv.order_text(parv.order_of(t, "xs")) for t in t_sk]))

    # ---- 总量 = Σ（数值）
    #
    # 每个任务在 xs=1024 处 = 17×1024 + 19 = 17427（三段式 `for` 在 parser 里
    # desugar 成 `while`，逐轮 17 步——旧 `..` 区间写法已删，见 CHANGELOG）。
    _PER = 17 * 1024 + 19
    vals = [parv._scale_value(t, "xs", 1024) for t in t_sk]
    check("总量 = Σ（1024 处：三份 17427 + 三份 17427）",
          abs(sum(vals) - _PER * 6) < 1e-6, str(vals))

    # ---- 机器档：κ 不变、wall_est 变（D.2 的核心验收）
    hom = parv.MachineProfile.from_obj(
        {"cores": [{"kind": "C", "weight": 1.0}] * 4})
    bl = parv.MachineProfile.from_obj({"cores": [
        {"kind": "P", "weight": 1.0}, {"kind": "P", "weight": 1.0},
        {"kind": "E", "weight": 0.45}, {"kind": "E", "weight": 0.45}]})
    s_hom = parv.summary(k4, hom, 1024)["blocks"][0]
    s_bl = parv.summary(k4, bl, 1024)["blocks"][0]
    check("κ 与 profile 无关：任务表达式与阶逐条相同",
          s_hom["tasks"] == s_bl["tasks"], "任务表被 profile 影响了")
    check("同构 4 核：wall_est = 17427（每任务一个核）",
          abs(s_hom["wall_estimate"] - 17427) < 1e-6, str(s_hom["wall_estimate"]))
    check("大小核：wall_est 变大 ≈ 17427 / 0.45",
          s_bl["wall_estimate"] > s_hom["wall_estimate"]
          and abs(s_bl["wall_estimate"] - 17427 / 0.45) < 1.0,
          f"{s_hom['wall_estimate']} → {s_bl['wall_estimate']}")
    check("同阶的四个任务：ties = 4（报告据此说「阶相同」）",
          s_hom["bottleneck_ties"] == 4, str(s_hom["bottleneck_ties"]))

    # ---- 阶不同：瓶颈由**阶**决定，不是由系数
    ci_mx = CostInterp(parse_source(MIXED, "m.eln").module, tu)
    k_mx = ci_mx.cost_of("f")
    s_mx = parv.summary(k_mx, None, 1024)["blocks"][0]
    check("阶不同：瓶颈 = 那个 O(n²) 的任务（ties = 1）",
          s_mx["bottleneck"] == 0 and s_mx["bottleneck_ties"] == 1,
          f"{s_mx['bottleneck']}/{s_mx['bottleneck_ties']}")
    check("阶不同：整块判阶 = n²（对 O(n) 是债务）",
          compare(k_mx, ci_mx.target_cost("f")).kind == "debt",
          compare(k_mx, ci_mx.target_cost("f")).render())
    _mt = parv.par_blocks(k_mx)[0][1] if parv.par_blocks(k_mx) else []
    check("阶不同：总量仍是 Σ（n² 那一项 + 其余），任务数不变",
          len(_mt) == 2
          and parv.order_of(_mt[0], "xs") == (2, 0, 0)
          and "n(xs)×(" in k_mx.render(),
          k_mx.render())

    # ---- 核数 < 任务数：LPT 会把活摊到每个核上
    two = parv.MachineProfile.from_obj({"cores": [
        {"kind": "P", "weight": 1.0}, {"kind": "E", "weight": 0.45}]})
    w2, loads, assign, _vals, why = parv.wall_estimate(k_tasks := tasks, two, 1024)
    check("2 核 < 4 任务：每核都分到活", all(x > 0 for x in loads), str(loads))
    check("2 核：wall 大于 4 核", w2 > s_hom["wall_estimate"], str(w2))
    check("每个任务都分到某个核", len(assign) == 4 and all(a in (0, 1) for a in assign),
          str(assign))
    check("核数 < 任务数 → 一定有核分到多个任务（任务不拆）",
          max(assign.count(0), assign.count(1)) > 1, str(assign))

    # ---- 退化：1 个任务 / 嵌套
    ci1 = CostInterp(parse_source(SINGLE, "m.eln").module, tu)
    k1 = ci1.cost_of("f")
    check("单任务：κ 仍是 par(...)（保留「这里写过 parallel」）",
          "par(" in k1.render() and len(parv.par_blocks(k1)) == 1, k1.render())
    check("单任务：判阶 = 那个任务的阶（对 O(n) 达成）",
          compare(k1, ci1.target_cost("f")).kind == "ok")
    ci_n = CostInterp(parse_source(NESTED, "m.eln").module, tu)
    k_n = ci_n.cost_of("f")
    check("嵌套：两个 par 块都被识别（外层 + 内层）",
          len(parv.par_blocks(k_n)) == 2, str(len(parv.par_blocks(k_n))))
    check("嵌套：外层左边那个任务本身是 par（阶仍是 n）",
          parv.order_of(parv.par_blocks(k_n)[0][1][0], "xs") == (1, 0, 0),
          str(parv.order_of(parv.par_blocks(k_n)[0][1][0], "xs")))

    # ---- allocation：并行块里的分配取 Σ（以前一律记 0）
    ci_a = CostInterp(parse_source(
        """u64 f(const vector<u64>& xs) {
    parallel {
        { const vector<u64> t = format_u64(42); }
        { const vector<u64> u = format_u64(43); }
    }
    return 0;
}
""", "m.eln").module, parse_source(
            "spec f(const vector<u64>& xs) -> u64 { effects {}; "
            "target { time: { max: O(xs.length()) }, allocation: { max: 0 } }; }",
            "m.tu").module)
    a = ci_a.alloc_of("f")
    check("并行块里两个分配 = Σ = 2（以前记 0 → 会假达成）",
          a.const_value() == 2.0, a.render())

    # ---- CLI：同一份程序，换机器档 → κ 不变、wall_est 变
    print("=== CLI：--cores 只改 wall_est（κ 逐字不变）===")
    tmp = tempfile.mkdtemp()
    tu_p = os.path.join(tmp, "pv.tu")
    ch_p = os.path.join(tmp, "pv.eln")
    prof_h = os.path.join(tmp, "hom.json")
    prof_b = os.path.join(tmp, "big.json")
    _write(tu_p, TU)
    _write(ch_p, TASKS4)
    _write(prof_h, '{"cores": [{"kind":"C","weight":1.0},'
                   ' {"kind":"C","weight":1.0},'
                   ' {"kind":"C","weight":1.0},'
                   ' {"kind":"C","weight":1.0}]}')
    _write(prof_b, '{"cores": [{"kind":"P","weight":1.0},'
                   ' {"kind":"P","weight":1.0},'
                   ' {"kind":"E","weight":0.45},'
                   ' {"kind":"E","weight":0.45}]}')

    rc_h, out_h = _run_cost([tu_p, "--cores=" + prof_h, "--at=1024"])
    rc_b, out_b = _run_cost([tu_p, "--cores=" + prof_b, "--at=1024"])
    check("两次 cost 都退出 0", rc_h == 0 and rc_b == 0, f"{rc_h}/{rc_b}")
    check("κ 行逐字相同", _pick(out_h, "κ ≤") == _pick(out_b, "κ ≤"),
          f"{_pick(out_h, 'κ ≤')} vs {_pick(out_b, 'κ ≤')}")
    check("视图里能看见 4 个任务",
          all(f"任务{i}" in out_h for i in range(4)), out_h[-600:])
    check("wall_est 变了", _pick(out_h, "wall_est") != _pick(out_b, "wall_est"),
          f"{_pick(out_h, 'wall_est')} vs {_pick(out_b, 'wall_est')}")
    rc_j, out_j = _run_cost([tu_p, "--json", "--cores=" + prof_b, "--at=1024"])
    d = json.loads(out_j)
    blk = d["functions"]["f"].get("parallel", {}).get("blocks", [{}])[0]
    check("--json 带 parallel 段", bool(blk), list(d["functions"]["f"]))
    check("--json 的 wall_estimate 有数", isinstance(blk.get("wall_estimate"), float),
          str(blk.get("wall_estimate")))
    check("--json 里 κ 表达式与 profile 无关",
          "par(" in d["functions"]["f"]["cost_expression"],
          d["functions"]["f"]["cost_expression"])

    # ---- 没给 --at：κ 含尺寸量 → wall_est 老实说"给不出"
    rc_n, out_n = _run_cost([tu_p, "--cores=" + prof_b])
    check("没给 --at 时 wall_est = —（不猜规模）",
          rc_n == 0 and "wall_est = —" in out_n, _pick(out_n, "wall_est"))
    # ---- 坏档：明确报错，不静默当同构
    bad = os.path.join(tmp, "bad.json")
    _write(bad, '{"cores": [{"kind": "P"}]}')
    rc_bad, _o = _run_cost([tu_p, "--cores=" + bad, "--at=1024"])
    check("坏机器档 → 退出码 2（不静默当同构）", rc_bad == 2, str(rc_bad))


def _write(path: str, text: str) -> None:
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _run_cost(argv):
    """跑一次 `cost`，返回 `(rc, stdout)`；stderr 也吞掉（坏档那条是**预期**的报错）。"""
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = dsh.cmd_cost(list(argv))
    return rc, buf.getvalue()


def _pick(out: str, needle: str) -> str:
    for ln in out.splitlines():
        if needle in ln:
            return ln.strip()
    return ""


def main() -> int:
    print("=== 语义：顺序模拟 ===")
    check("两个任务都执行（各写各的，回来合并）",
          run("""u64 f(const vector<u64>& xs) {
    u64 a = 0;
    u64 b = 0;
    parallel {
        { a = sum_of(xs); }
        { b = sum_of(xs); }
    }
    return a + b;
}""") == 12, "期望 12")

    check("嵌套 parallel 也能跑",
          run("""u64 f(const vector<u64>& xs) {
    u64 a = 0;
    u64 b = 0;
    parallel {
        { parallel { { a = 1; } } }
        { b = 2; }
    }
    return a + b;
}""") == 3, "期望 3")

    check("每个任务一个花括号（显式任务边界）",
          run("""u64 f(const vector<u64>& xs) {
    u64 s = 0;
    parallel {
        { s = s + sum_of(xs); }
        { s = s + sum_of(xs); }
    }
    return s;
}""") == 12, "期望 12")

    print("=== 成本：κ = Σ（B-24）===")
    tu = parse_source(TU, "m.tu").module
    ch = parse_source("""u64 f(const vector<u64>& xs) {
    u64 s = 0;
    parallel {
        s = s + sum_of(xs);
        s = s + sum_of(xs);
    }
    return s;
}
""", "m.eln").module
    k = CostInterp(ch, tu).cost_of("f").render()
    check("κ 里出现两个 n(xs)（Σ 而不是 max）", k.count("n(xs)") == 2, k)

    test_par_view(tu)

    print("=== ?! 可以进 parallel（成本仍按 Σ 记）===")
    c = codes("""u64! f(const vector<u64>& xs) {
    u64 s = 0;
    parallel {
        s = s + ?!kernel(xs);
    }
    return s;
}
""", TU_CHOOSE)
    check("不报 E-Parallel-Dispatch", "E-Parallel-Dispatch" not in c, str(c))

    print("=== 允许在任务里打印（改为「不可测量」，不再报错）===")
    c = codes("""u64 f(const vector<u64>& xs) {
    parallel {
        { print(1); }
        { print(2); }
    }
    return 0;
}
""")
    check("parallel 里 print 不再报 E-Parallel-Effectful",
          "E-Parallel-Effectful" not in c, str(c))

    print("=== 任务之间不许共享可变状态（E-Parallel-ShareMut）===")
    c = codes("""u64 f(const vector<u64>& xs) {
    u64 s = 0;
    parallel {
        { s = s + 1; }
        { s = s + 2; }
    }
    return s;
}
""")
    check("两个任务都写 s → 报 E-Parallel-ShareMut", "E-Parallel-ShareMut" in c, str(c))

    c = codes("""u64 f(const vector<u64>& xs) {
    u64 a = 0;
    u64 b = 0;
    parallel {
        { a = 1; }
        { b = 2; }
    }
    return a + b;
}
""")
    check("各写各的 → 通过", "E-Parallel-ShareMut" not in c, str(c))

    print("=== B-26：候选里不能有 parallel ===")
    c = codes("""u64! f(const vector<u64>& xs) {
    return ?!kernel(xs);
} kernels kernel(const vector<u64>& xs) -> u64! {
    by_a {
        parallel {
            print(1);
        }
        return 1;
    }
    by_b {
        return 2;
    }
}
""", TU_CHOOSE)
    check("报 E-Parallel-Measured", "E-Parallel-Measured" in c, str(c))

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
