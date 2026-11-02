# -*- coding: utf-8 -*-
"""**两值域 kind 覆盖表的守门测试**：漂移必须让测试变红，而不是靠人记得看。

## 背景

`interp.py`（具体值域）与 `cost_interp.py`（符号值域）各自用 if 链分派 AST 节点。
两份清单**已经漂移过**：某一侧多写一个分支、少写一个分支，编译器不会报错，
运行时也不会崩——它只是让 κ 悄悄偏小，或者让某个构造在解释器里**根本跑不了**。

`src/walk.py` 能从源码静态抽出「谁处理了哪些 kind」，但它以前**不是测试**：
跑一遍、看一眼、没人管。

## 这个文件钉住四件事

1. **漂移为零**：语句层与表达式层，两个值域处理的 kind 集合**完全相等**；
2. **清空单不许过期**：`IGNORED`（不产生成本）与 `UNIMPLEMENTED`（两侧都没做）
   里的 kind，**不能**在任何一侧出现分支；
3. **检测器本身有效**：喂一张人造的漂移表，`asymmetry()` 必须报出来；
   喂注释里的假 kind，`extract_kinds()` 必须忽略（这条真的踩过：
   把死分支改成注释解释之后，检测器报告了两个不存在的 kind）；
4. **修复要留行为证据**：`tuple` 的 κ ≥ ops、`lit-float` 不再抛、语句层没有 try/ref。
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
import subprocess
import sys

sys.path.insert(0, os.path.join(ROOT, "tools"))

import walk                                              # noqa: E402
from cost_check import evaluate as kappa_at              # noqa: E402
from cost_interp import CostInterp                       # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset  # noqa: E402
from parser import parse_source                          # noqa: E402

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


def kinds(dom: str, layer: str) -> frozenset:
    return walk.report()["table"][(dom, layer)]


def run_eln(src: str, fn: str, args):
    """返回 (κ, 实测 ops)。κ 在 `xs` 长度 = len(args[0]) 上取值。"""
    mod = parse_source(src, "t.eln").module
    k = kappa_at(CostInterp(mod, None).cost_of(fn),
                 {"xs": len(args[0]) if args else 0})
    ops_enable(True)
    try:
        ops_reset()
        Interp(mod, None).call(fn, args)
        return k, ops_of()
    finally:
        ops_enable(False)


# ---------------------------------------------------------------- 1. 漂移为零

def test_no_drift():
    print("[1] 漂移为零：两值域的 kind 集合必须逐个相等")
    p = walk.problems()
    check(p["asymmetry"] == {},
          "语句层 + 表达式层都没有「只在一侧」的 kind", str(p["asymmetry"]))
    check(p["unaccounted"] == [],
          "没有既没处理、也没写理由的 kind", str(p["unaccounted"]))
    check(p["stale"]["ignored_but_handled"] == [],
          "IGNORED 里没有「其实有分支」的条目",
          str(p["stale"]["ignored_but_handled"]))
    check(p["stale"]["unimplemented_but_handled"] == [],
          "UNIMPLEMENTED 里没有「其实有分支」的条目",
          str(p["stale"]["unimplemented_but_handled"]))

    ei, ec = kinds("interp", "expr"), kinds("cost", "expr")
    check(ei == ec, f"表达式层：interp == cost（各 {len(ei)} 个）",
          f"interp-cost={sorted(ei - ec)} cost-interp={sorted(ec - ei)}")
    si, sc = kinds("interp", "stmt"), kinds("cost", "stmt")
    check(si == sc, f"语句层：interp == cost（各 {len(si)} 个）",
          f"interp-cost={sorted(si - sc)} cost-interp={sorted(sc - si)}")

    check("lit-float" in ei and "lit-float" in ec,
          "lit-float 两侧都有分支（修前只有 cost 有）")
    check("tuple" in ec and "type-name" in ec,
          "tuple / type-name 在 cost 侧有分支（修前落兜底 ⇒ 少算）")
    check("try" not in sc and "ref" not in sc,
          "语句层没有 try / ref 分支（解析器从不产出，删掉死代码）")


# ---------------------------------------------------------------- 2. 反向控制

def test_detector_works():
    print("[2] 反向控制：检测器必须能抓到人造漂移")
    fake = {
        ("interp", "stmt"): frozenset({"if", "zzz"}),
        ("cost", "stmt"): frozenset({"if"}),
        ("interp", "expr"): frozenset({"lit-int"}),
        ("cost", "expr"): frozenset({"lit-int", "yyy"}),
    }
    a = walk.asymmetry(fake)
    check(a.get("stmt", {}).get("interp") == ["zzz"],
          "语句层单侧 kind 被抓到", str(a.get("stmt")))
    check(a.get("expr", {}).get("cost") == ["yyy"],
          "表达式层单侧 kind 被抓到", str(a.get("expr")))

    # 别名是**死代码**，不是"归一化"：解析器只产 struct-literal，
    # `struct-lit` / `record-literal` 曾经让覆盖表看起来对称，实际是假的。
    from kinds import COST_EXPR
    check("struct-lit" not in COST_EXPR and "record-literal" not in COST_EXPR
          and COST_EXPR.get("struct-literal") == "_k_expr_struct_literal",
          "结构体字面量只认 struct-literal 一个拼写", str(COST_EXPR.get("struct-literal")))

    # IGNORED 条目一旦有分支 ⇒ stale
    st = walk.stale({("interp", "stmt"): frozenset({"module"}),
                     ("cost", "stmt"): frozenset(),
                     ("interp", "expr"): frozenset(),
                     ("cost", "expr"): frozenset()})
    check(st["ignored_but_handled"] == ["module"],
          "IGNORED 里有分支 → stale 抓得到", str(st))

    # UNIMPLEMENTED 条目一旦有分支 ⇒ stale
    st = walk.stale({("interp", "stmt"): frozenset(),
                     ("cost", "stmt"): frozenset({"deref"}),
                     ("interp", "expr"): frozenset(),
                     ("cost", "expr"): frozenset()})
    check(st["unimplemented_but_handled"] == ["deref"],
          "UNIMPLEMENTED 里有分支 → stale 抓得到", str(st))


def test_registry_is_truth():
    print("[3] 注册表是唯一事实：**登记即实现**")
    check(walk.missing_handlers() == [],
          "表里写的每个方法都在类上真的存在", str(walk.missing_handlers()))

    # 反向控制：捏一个不存在的处理器名，必须被抓到
    fake = {("interp", "expr"): {"lit-int": "_k_expr_nope"},
            ("interp", "stmt"): {},
            ("cost", "expr"): {"lit-int": "_k_expr_lit_int"},
            ("cost", "stmt"): {}}
    check(walk.missing_handlers(fake) != [],
          "人造的假处理器名会被抓到", str(walk.missing_handlers(fake)))

    # 这一条是真实事故：把两条死分支改成注释解释之后，旧的静态抽取
    # 报出了 `ref` / `try` 两个**不存在**的 kind。现在分派是查表，
    # 注释不再参与判断——表里没有就是没有。
    from kinds import COST_STMT
    check("try" not in COST_STMT and "ref" not in COST_STMT,
          "语句层注册表里没有 try / ref（注释也造不出 phantom kind）")


# ---------------------------------------------------------------- 4. 修复的行为证据

def test_fix_evidence():
    print("[4] 修复的行为证据：κ ≥ ops，且不再抛「未实现」")

    src = """
func t(const Vec<u64>& xs) -> (u64, u64) { return (xs.length(), 1); }
"""
    k, o = run_eln(src, "t", [[0, 1, 2, 3]])
    check(o <= k, f"元组构造：ops {o} ≤ κ {k:g}（修前 tuple 落兜底）", f"{o}/{k}")

    src = """
func t(const Vec<u64>& xs) -> u64 { return xs.length(); }
"""
    k, o = run_eln(src, "t", [[0, 1, 2, 3]])
    check(o <= k, f"对照组（无元组）：ops {o} ≤ κ {k:g}", f"{o}/{k}")

    # lit-float：具体值域以前抛「未实现的表达式：lit-float」
    mod = parse_source("""
func f() -> u64 { return 1.5; }
""", "f.eln").module
    try:
        v = Interp(mod, None).call("f", [])
        ok = isinstance(v, float) and abs(v - 1.5) < 1e-9
        check(ok, "lit-float 在解释器里现在有值（不再抛未实现）", repr(v))
    except Exception as exc:                              # noqa: BLE001
        check(False, "lit-float 在解释器里现在有值（不再抛未实现）",
              f"{type(exc).__name__}: {exc}")


def test_cli():
    print("[5] `python src/walk.py` 作为闸：干净时退出 0")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, os.path.join(HERE, "walk.py")],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    check(p.returncode == 0, "退出码 0（有漂移时是 1）", str(p.returncode))
    check("无" in (p.stdout or ""), "输出里写明「无」漂移", (p.stdout or "")[-120:])


def main() -> int:
    test_no_drift()
    test_detector_works()
    test_registry_is_truth()
    test_fix_evidence()
    test_cli()

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
