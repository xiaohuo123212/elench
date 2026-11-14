# -*- coding: utf-8 -*-
"""`E-Recursion-Unbounded`（C11-b）：**有递归就必须声明 `recursion depth`**。

## 这条诊断要解决什么

修四类 κ 缺口时，递归那一类是这么修的：`.tu` 写了 `recursion depth <= N`
就按 `(N+1) × 一层成本` 算。问题是——**不写**的时候，κ 只按一层计：

    down(xs, 8)：κ = 33，ops = 104   →  承诺的上界**小于**真实成本

而 `elench cost` 当时会打印「✓ 所有函数都在 target 之内」。**假结论比报错更糟**，
所以这里把它变成**编译期错误**：要么写深度，要么编不过。

## 判据

- 普通调用图上的**环**（直接递归 `a→a`、互递归 `a→b→a`）算递归；
- `?!` 到候选的边**不算**（那是派发，不是递归；候选调宿主另有 B-9 管）；
- 环上**每个**成员各自需要声明——互递归时不能只声明一个。

## 四道闸

`check` / `build`（走 checker）/ `cost` / `export`（走 `_recursion_gate`）
对同一个模块给出**一致**的拒绝。
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

import io
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(ROOT, "tools"))

from checker import check_module                      # noqa: E402

_passed = 0
_failed = []

CODE = "E-Recursion-Unbounded"


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


# ---------------------------------------------------------------- 源码样本

DIRECT_ELN = """
u64 down(const vector<u64>& xs, u64 i) {
    if (i == 0) { return xs.length(); }
    return 1 + down(xs, i - 1);
}
"""

DIRECT_TU = """
spec down(const vector<u64>& xs, u64 i) -> u64 {
    effects {};
%s
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

MUTUAL_ELN = """
u64 ping(const vector<u64>& xs, u64 i) {
    if (i == 0) { return xs.length(); }
    return pong(xs, i - 1);
}
u64 pong(const vector<u64>& xs, u64 i) {
    if (i == 0) { return xs.length(); }
    return ping(xs, i - 1);
}
"""

MUTUAL_TU = """
spec ping(const vector<u64>& xs, u64 i) -> u64 {
    effects {};
%s
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec pong(const vector<u64>& xs, u64 i) -> u64 {
    effects {};
%s
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

DEPTH = "    recursion depth <= 8;"
NONE = ""

# ---------------------------------------------------------------- W-Exp-Risk 样本

WCODE = "W-Exp-Risk"

#: 树递归：一条执行路径上**两个**自调用点（朴素 fib）。
TREE_ELN = """
u64 fib(u64 n) {
    if (n < 2) { return n; }
    return fib(n - 1) + fib(n - 2);
}
"""

TREE_TU = """
spec fib(u64 n) -> u64 {
    effects {};
%s
    target { time: { max: O(n * n) }, allocation: { max: 0 }, };
}
"""

#: 自调用落在**循环体**里（每层可能跑 L 次，L 静态不可知）。
LOOP_ELN = """
u64 walk(u64 n, u64 acc) {
    for (u64 i = 0; i < 3; i = i + 1) {
        if (n > 0) { acc = acc + walk(n - 1, acc); }
    }
    return acc;
}
"""

LOOP_TU = """
spec walk(u64 n, u64 acc) -> u64 {
    effects {};
%s
    target { time: { max: O(n * n) }, allocation: { max: 0 }, };
}
"""

#: 单链：一条路径上**恰好一个**自调用点（含「早退二分」——两个调用点互斥）。
CHAIN_ELN = """
u64 fact(u64 n) {
    if (n <= 1) { return 1; }
    return n * fact(n - 1);
}
u64 down(u64 n, u64 acc) {
    if (n == 0) { return acc; }
    if (n < 3) { return down(n - 1, acc + 1); }
    return down(n - 1, acc);
}
"""

CHAIN_TU = """
spec fact(u64 n) -> u64 {
    effects {};
%s
    target { time: { max: O(n) }, allocation: { max: 0 }, };
}
spec down(u64 n, u64 acc) -> u64 {
    effects {};
%s
    target { time: { max: O(n) }, allocation: { max: 0 }, };
}
"""


def exp_risk_of(tu: str, eln: str):
    """只取 `W-Exp-Risk`（`severity` 必须是 warning，见下）。"""
    return [d for d in check_module(tu, eln, "t").diagnostics if d.code == WCODE]


def diags_of(tu: str, eln: str):
    res = check_module(tu, eln, "t")
    return [d for d in res.diagnostics if d.code == CODE]


# ---------------------------------------------------------------- 检查器

def test_direct():
    print("[1] 直接递归")
    ds = diags_of(DIRECT_TU % NONE, DIRECT_ELN)
    check(len(ds) == 1, "无 depth → 1 条 E-Recursion-Unbounded", str(len(ds)))
    if ds:
        check(ds[0].severity == "error", "严重级是 error（不是 warning）", ds[0].severity)
        check("down → down" in ds[0].message,
              "消息里给出环：`down → down`", ds[0].message)
        check("recursion depth" in ds[0].message,
              "消息里给出出路：写 `recursion depth <= N;`", ds[0].message)
        check("C11-b" in ds[0].message, "消息里引用规格条款 C11-b", ds[0].message)
        check(ds[0].line == 2, "位置指向 spec 声明行", str(ds[0].line))

    check(diags_of(DIRECT_TU % DEPTH, DIRECT_ELN) == [],
          "写了 `recursion depth <= 8;` → 无诊断")


def test_mutual():
    print("[2] 互递归")
    ds = diags_of(MUTUAL_TU % (NONE, NONE), MUTUAL_ELN)
    names = sorted(d.message.split("`")[1] for d in ds if "`" in d.message)
    check(len(ds) == 2, "两个都不声明 → 2 条（环上每个成员各一条）", str(len(ds)))
    check(names == ["ping", "pong"], "两条分别指向 ping / pong", str(names))

    ds = diags_of(MUTUAL_TU % (DEPTH, NONE), MUTUAL_ELN)
    names = sorted(d.message.split("`")[1] for d in ds if "`" in d.message)
    check(len(ds) == 1 and names == ["pong"],
          "只声明 ping → 仍报 pong（不能只声明一个）", str(names))

    check(diags_of(MUTUAL_TU % (DEPTH, DEPTH), MUTUAL_ELN) == [],
          "两个都声明 → 无诊断")

    # ---- **⊤ 的原因文案**（2026-11-10）：互递归不是树递归
    #
    # 判定一直是 ⊤（不变），错的是措辞：`_note_tree_rec` 以前对两种情况都写
    # 「是树递归…多个自调用点」，可互递归 `a↔b` 的 `per_path` 是 **0**——
    # `ping` 的函数体里根本没有自调用点，自调用点在环的**另一侧**。
    from parser import parse_source
    from cost_interp import CostInterp

    ci = CostInterp(parse_source(MUTUAL_ELN, "m.eln").module,
                    parse_source(MUTUAL_TU % (DEPTH, DEPTH), "m.tu").module)
    for _fn in ("ping", "pong"):
        ci.cost_of(_fn)
    why = ci.top_reasons.get("ping", "")
    check("互递归" in why and "树递归" not in why,
          "互递归的 ⊤ 原因写「互递归」，不再写「树递归」", why)
    check("`pong`" in why, "原因里点名环上的另一个函数 pong", why)
    check("自调用点在环上" in why and "规模推不出" in why,
          "原因说清「自调用点在环上、规模推不出」", why)
    check(all(ci.cost_of(n).kind == "top" for n in ("ping", "pong")),
          "互递归仍是 κ=⊤（只是文案，判定没变）")

    # 树递归的原因文案保持不变（同一条分支要能区分两种原因）
    ci_tree = CostInterp(parse_source(TREE_ELN, "t.eln").module,
                         parse_source(TREE_TU % DEPTH, "t.tu").module)
    ci_tree.cost_of("fib")
    check("是树递归" in ci_tree.top_reasons.get("fib", ""),
          "真树递归的原因仍写「是树递归」", ci_tree.top_reasons.get("fib", ""))


def test_no_false_positive():
    print("[3] 不误报")
    chain = """
u64 a(const vector<u64>& xs) { return b(xs); }
u64 b(const vector<u64>& xs) { return xs.length(); }
"""
    tu = """
spec a(const vector<u64>& xs) -> u64 { effects {}; }
spec b(const vector<u64>& xs) -> u64 { effects {}; }
"""
    check(diags_of(tu, chain) == [], "无环的调用链 a→b → 无诊断")

    disp = """
u64 host(const vector<u64>& xs) { return ?!pick(xs); }
u64 by_scan(const vector<u64>& xs) { return xs.length(); }
u64 by_rev(const vector<u64>& xs) { return xs.length(); }
"""
    tu = """
choose pick over { by_scan, by_rev };
spec host(const vector<u64>& xs) -> u64 { effects {}; benchmodel { n = xs.length() }; }
spec by_scan(const vector<u64>& xs) -> u64 { effects {}; }
spec by_rev(const vector<u64>& xs) -> u64 { effects {}; }
"""
    check(diags_of(tu, disp) == [],
          "`?!` 到候选的边不算递归（宿主与候选之间没有环）")

    # 没有 spec 的函数：不归这条诊断管（交给 E-Spec-Missing 一类），不能崩
    check(diags_of("", DIRECT_ELN) == [],
          "没有 spec 时不崩、也不报这条（不越界）")

    # 非递归函数写了 depth：无害
    tu = """
spec down(const vector<u64>& xs, u64 i) -> u64 {
    effects {};
    recursion depth <= 8;
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec plain(const vector<u64>& xs) -> u64 {
    effects {};
    recursion depth <= 4;
}
"""
    check(diags_of(tu, DIRECT_ELN + """
u64 plain(const vector<u64>& xs) { return xs.length(); }
""") == [], "非递归函数写了 depth → 无害（不报错）")


def test_exp_risk():
    print("[4] W-Exp-Risk：树递归 = 「最坏可能指数级」的风险提示（warning，不阻断）")
    ds = exp_risk_of(TREE_TU % DEPTH, TREE_ELN)
    check(len(ds) == 1, "树递归 fib → 1 条 W-Exp-Risk", str(len(ds)))
    if ds:
        d = ds[0]
        check(d.severity == "warning", "严重级是 warning（不是 error）", d.severity)
        check("最坏可能指数级" in d.message,
              "文案说清「最坏可能指数级，除非你能证明不是」", d.message[:80])
        check(all(_w in d.message for _w in ("cost", "repeats", "recursion depth")),
              "文案给出「去哪儿看」：cost 原因行 / repeats / recursion depth",
              d.message[:80])
        check("`fib`" in d.message, "点名是哪个函数", d.message[:40])

    # **不许报成 error**：树递归合法（分治就是树形），只是提示
    res = check_module(TREE_TU % DEPTH, TREE_ELN, "t")
    check(res.ok, "有 W-Exp-Risk 仍然 check 通过（warning 不阻断）")
    check(not any(d.code.startswith("E-Exp") for d in res.diagnostics),
          "没有 E-Exp-*（这条只允许是 warning）")

    ds = exp_risk_of(LOOP_TU % DEPTH, LOOP_ELN)
    check(len(ds) == 1 and "循环体" in ds[0].message,
          "自调用落在循环体里 → 也报（同一条判定）",
          str([d.message[:40] for d in ds]))

    # ---- 不许误报
    check(exp_risk_of(CHAIN_TU % (DEPTH, DEPTH), CHAIN_ELN) == [],
          "单链递归 fact / 早退二分 → 0 条")
    check(exp_risk_of(DIRECT_TU % DEPTH, DIRECT_ELN) == [],
          "单链递归 down（`1 + down(...)`）→ 0 条")
    check(exp_risk_of(MUTUAL_TU % (DEPTH, DEPTH), MUTUAL_ELN) == [],
          "互递归 a↔b → 0 条（形状是链不是树；报「指数」会是猜）")
    check(exp_risk_of(TREE_TU % NONE, TREE_ELN) == [],
          "没写 recursion depth → 不报 W（另有 E-Recursion-Unbounded）")
    check(exp_risk_of("""
spec plain(u64 n) -> u64 { effects {}; recursion depth <= 4; }
""", """
u64 plain(u64 n) { return n; }
""") == [], "非递归函数写了 depth → 不报")

    # ---- **与 cost 的 ⊤ 原因同源**：报的集合 == cost 里「树递归退 ⊤」的集合
    from parser import parse_source
    from cost_interp import CostInterp
    ci = CostInterp(parse_source(TREE_ELN, "t.eln").module,
                    parse_source(TREE_TU % DEPTH, "t.tu").module)
    for _fn in sorted(ci.fns):
        ci.cost_of(_fn)
    tree_top = {n for n in ci.top_reasons if ci._rec_tree_like(n)}
    warned = {d.message.split("`")[1]
              for d in exp_risk_of(TREE_TU % DEPTH, TREE_ELN)}
    check(warned == tree_top,
          "报的集合与 `cost` 的「树递归退 ⊤」一致", f"{sorted(warned)} vs {sorted(tree_top)}")
    check(all(ci.cost_of(n).kind == "top" for n in warned),
          "被报的函数在 `cost` 里确实是 κ=⊤")

    # ---- CLI：exit 0 + 输出里有这条 warning
    tmp = tempfile.mkdtemp(prefix="exprisk_")
    try:
        tu_p = os.path.join(tmp, "r.tu")
        eln_p = os.path.join(tmp, "r.eln")
        for path, text in ((tu_p, TREE_TU % DEPTH), (eln_p, TREE_ELN)):
            with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        rc, out = run_cli(["check", tu_p], tmp)
        check(rc == 0, "CLI check：树递归退出码 0（warning 不阻断）", str(rc))
        check(WCODE in out and "最坏可能指数级" in out,
              "CLI check：输出里有 W-Exp-Risk", out[:200])
    finally:
        for name in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


# ---------------------------------------------------------------- 四条命令行闸

def run_cli(argv, cwd):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = os.path.join(ROOT, "src")
    p = subprocess.run([sys.executable, os.path.join(ROOT, "src", "dsh.py")] + argv,
                       cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def test_cli_gates():
    print("[5] 四道闸：check / build / cost / export 一致拒绝")
    tmp = tempfile.mkdtemp(prefix="recbound_")
    try:
        tu_p = os.path.join(tmp, "r.tu")
        eln_p = os.path.join(tmp, "r.eln")
        for path, text in ((tu_p, DIRECT_TU % NONE), (eln_p, eln_p and DIRECT_ELN)):
            with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)

        rc, out = run_cli(["check", tu_p], tmp)
        check(rc == 1, "check：退出码 1（编译不过）", str(rc))
        check(CODE in out, "check：输出里有 E-Recursion-Unbounded", out[:200])

        rc, out = run_cli(["build", tu_p], tmp)
        check(rc == 1, "build：退出码 1", str(rc))
        check(CODE in out, "build：输出里有 E-Recursion-Unbounded", out[:200])

        rc, out = run_cli(["cost", tu_p], tmp)
        check(rc == 1, "cost：**拒绝算 κ**（退出码 1，不再打印假结论）", str(rc))
        check("拒绝算 κ" in out, "cost：明说拒绝算 κ", out[:200])
        check("所有函数都在 target 之内" not in out,
              "cost：不再打印「✓ 都在 target 之内」", out[:200])

        rc, out = run_cli(["export", tu_p, "--format=csv"], tmp)
        check(rc == 1, "export：退出码 1", str(rc))
        check("拒绝算 κ" in out, "export：同样拒绝", out[:200])

        rc, out = run_cli(["header", tu_p], tmp)
        check(rc == 1, "header：退出码 1（不发出带假 κ 的 .h）", str(rc))

        # 补上 depth 之后四条都必须放行
        with io.open(tu_p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(DIRECT_TU % DEPTH)
        rc, out = run_cli(["check", tu_p], tmp)
        check(rc == 0, "补 depth 后 check：退出码 0", str(rc))
        rc, out = run_cli(["build", tu_p], tmp)
        check(rc == 0, "补 depth 后 build：退出码 0", str(rc))
        rc, out = run_cli(["cost", tu_p], tmp)
        check(rc == 0, "补 depth 后 cost：退出码 0", str(rc))
        rc, out = run_cli(["export", tu_p, "--format=csv"], tmp)
        check(rc == 0, "补 depth 后 export：退出码 0", str(rc))
        rc, out = run_cli(["header", tu_p], tmp)
        check(rc == 0, "补 depth 后 header：退出码 0", str(rc))
    finally:
        for name in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


def main() -> int:
    test_direct()
    test_mutual()
    test_no_false_positive()
    test_exp_risk()
    test_cli_gates()

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
