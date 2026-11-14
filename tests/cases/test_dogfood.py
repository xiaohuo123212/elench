#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_dogfood.py —— **吃自己的狗粮**：10 个样例必须被本工具链检查通过。

论文里写"我们的样例全部由本工具链检查通过"——这句话以前只是**说法**，
没有机器证据。这条测试就是那个证据：遍历 `文档/样例/*.tu`，逐个
`check`（无 error）+ `cost`（无债务），任何一条不满足都失败。

## 为什么留一个例外

`search.binary_search` 目前有一条**已知债务**（target 是 `O(log n)`，而承诺里含
线性候选的项）。它是真的、不是回归——所以列进 `KNOWN_DEBT` 并写明原因，
**不允许**在别处悄悄新增。
"""
import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import glob
import os
import sys

from checker import check_module
from cost_check import compare
from cost_interp import CostInterp
from parser import parse_source

_passed = 0
_failed = []

#: 已知债务：程序名 → 允许有债务的函数。**只增不减地写在这里，不许在别处冒出来。**
KNOWN_DEBT = {
    "search": {"binary_search"},
    # **2026-11-08 批次 2**：`push`/`insert` 现在计入分配上界，
    # 于是 blur / matmul 的 `allocation: { max: 0 }` 从"达成"翻成"债务"。
    # 这是**契约本来该有的结论**（它们确实要 push 建表），不是回归。
    "blur": {"blur"},
    "matmul": {"matmul"},
    # **2026-11-09 `else if` 进 κ（上一轮修的是 κ 漏算）**：
    # `_k_stmt_if` 以前只收 `kind == "block"` 的分支，整条 `else if` 链
    # （自己的 +1、条件、分支）**从来没进过 κ** —— `_diff/bracket_match` 因此
    # 破界（κ 576 < ops 609）。补上之后 `tokenize` 的
    # `else if (is_digit(byte)) { read_number(text, index)? }` 把
    # `read_number` 里那个 `while (index < total and is_digit(...))`（O(n)）
    # 算进了外层 `while (index < total)` 的每轮成本 → κ(tokenize) 从 O(n) 变 O(n²)。
    #
    # **2026-11-09 第 4 步已解除**（所以 `calculator` 从 KNOWN_DEBT 里删掉）：
    # 真实实现是 O(n)（`index` 单调推进到 `scanned.1`，每个字节只读一次），
    # 而那个内层循环的圈数由 `digits >= MAX_DIGITS` 卡在 **MAX_DIGITS + 1 = 20**。
    # 修法不是给 `read_number` 写 repeats（它不当 while 条件，写了不生效），而是：
    #   ① 把 `index < total and is_digit(...)` 包成单一谓词 `more_digits`；
    #   ② 在 `more_digits` 的 spec 上写 `repeats 20;`。
    # 单一谓词是必须的：`and` 组合的界在成本域里取**较松**的一侧
    # （`_min_cost` → `Cost.max`），只给 `is_digit` 写 repeats 仍会得到 max(n, 20)=n。
    # 现在 κ(evaluate) = n(text)×1426 + 常数 ⇒ O(n)，与 target 同阶，
    # `test_dogfood` 的「没有超出已知清单的债务」把它钉住（见 test_calculator_linear）。
}


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def load(name):
    base = os.path.join(ROOT, "文档", "样例")
    tu_src = open(os.path.join(base, name + ".tu"), encoding="utf-8").read()
    eln_src = open(os.path.join(base, name + ".eln"), encoding="utf-8").read()
    return tu_src, eln_src


def _accept_fingerprint(tu_p):
    """`.tu` 的语义指纹——与 `dsh._tu_canon` 同一套（只认词法 token，注释/空白不算）。"""
    import hashlib
    from dsh import _tu_canon
    return hashlib.sha256(_tu_canon(tu_p).encode("utf-8")).hexdigest()


def _dirty_sample_tus():
    """工作区里**有未提交改动**的样例 `.tu`（basename 集合）。

    **不拿它们判基线**：别的代理可能正改着，此刻的 `--accept-tu-change`
    只能确认到一半的状态。真正的失配是"改完提交了、基线却没跟着确认"——
    那正是这条断言要抓的（`calculator` 2026-11-09 就是这样漏的）。
    """
    import subprocess
    try:
        r = subprocess.run(
            ["git", "-c", "core.quotepath=false", "-C", ROOT,
             "status", "--porcelain", "--", os.path.join("文档", "样例")],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return set()
    if r.returncode != 0:                       # 不是 git 仓库：无从判断，全查
        return set()
    return {os.path.basename(l[3:].strip())
            for l in r.stdout.splitlines() if l[3:].strip().endswith(".tu")}


def _stale_accept():
    """本地 accept 基线对不上当前 `.tu` 的清单（空 = 全对得上）。

    为什么查这个：`build / cost / measure` 的第一道闸就是它，基线过期 ⇒ 样例
    **永远** build 失败，而失败信息容易被读成"用户自己的实现有问题"。
    基线是机器本地的（`.gitignore` 里有 `*.accept.yaml`），所以这里**只查存在的那几份**：
    从没确认过的样例，`build` 首次会自动记基线，不算错。

    返回 `(过期清单, 跳过的清单)`；每条过期记录带**两个 sha**，方便一眼对照。
    """
    stale = []
    dirty = _dirty_sample_tus()
    skipped = []
    base = os.path.join(ROOT, "文档", "样例")
    for tu_p in sorted(glob.glob(os.path.join(base, "*.tu"))):
        ap = tu_p[:-3] + ".accept.yaml"
        if not os.path.exists(ap):
            continue
        name = os.path.basename(tu_p)
        if name in dirty:
            skipped.append(name)
            continue
        want = None
        for line in open(ap, encoding="utf-8"):
            if line.startswith("tu_sha256:"):
                want = line.split(":", 1)[1].strip()
                break
        cur = _accept_fingerprint(tu_p)
        if cur != want:
            stale.append((name, want or "<缺 tu_sha256>", cur))
    return stale, skipped


def test_calculator_linear() -> None:
    """`calculator.evaluate` 的 time κ 必须回到 **O(text.length())**。

    为什么单列：2026-11-09 `else if` 进 κ 之后，`tokenize` 的 `else if (is_digit)`
    把 `read_number` 里那个 O(n) 的 while 算进了每轮成本，κ(evaluate) 一度是
    **n²**（真实实现是 O(n)）。当时把它登记成 `KNOWN_DEBT`；第 4 步给
    `read_number` 的循环条件包了单一谓词 `more_digits` 并写 `repeats 20;`，
    κ 回到 n¹。这条断言钉住"不再变回 n²"，也钉住"evaluate 不再是债务"。
    """
    print("[2] calculator.evaluate：time κ 是 O(n)（第 4 步收尾）")
    from cost_explain import deg
    tu_src, eln_src = load("calculator")
    tu = parse_source(tu_src, "calculator.tu").module
    eln = parse_source(eln_src, "calculator.eln").module
    ci = CostInterp(eln, tu)
    c = ci.cost_of("evaluate")
    tgt = ci.target_cost("evaluate")
    check(deg(c) == 1, "κ(evaluate) 的尺寸指数 = 1（线性）", f"实际 deg={deg(c)}")
    check(deg(tgt) == 1, "target(evaluate) 的尺寸指数 = 1", f"实际 deg={deg(tgt)}")
    check(compare(c, tgt).kind != "debt", "evaluate 不再是债务",
          str(compare(c, tgt).kind))
    check("calculator" not in KNOWN_DEBT or "evaluate" not in KNOWN_DEBT["calculator"],
          "calculator.evaluate 已从 KNOWN_DEBT 删除")


def main() -> int:
    base = os.path.join(ROOT, "文档", "样例")
    names = sorted(os.path.basename(p)[:-3]
                   for p in glob.glob(os.path.join(base, "*.tu")))
    check(len(names) >= 10, f"样例数 ≥ 10（实际 {len(names)}）", str(len(names)))

    total_err = 0
    unexpected = []
    for name in names:
        tu_src, eln_src = load(name)
        r = check_module(tu_src, eln_src, name + ".tu")
        errs = [d for d in r.diagnostics if d.severity == "error"]
        total_err += len(errs)
        if errs:
            print(f"    {name}: {[d.code for d in errs]}")

        tu = parse_source(tu_src, name + ".tu").module
        eln = parse_source(eln_src, name + ".eln").module
        ci = CostInterp(eln, tu)
        debts = set()
        for fn in ci.fns:
            try:
                if compare(ci.cost_of(fn), ci.target_cost(fn)).kind == "debt":
                    debts.add(fn)
            except Exception:                            # noqa: BLE001
                debts.add(fn)
        extra = debts - KNOWN_DEBT.get(name, set())
        if extra:
            unexpected.append(f"{name}: {sorted(extra)}")

    check(total_err == 0, f"{len(names)} 个样例 check 无 error", str(total_err))
    check(not unexpected, "没有超出已知清单的债务", "; ".join(unexpected))

    # 本地 accept 基线（`.tu` 的语义指纹）必须对得上当前声明。
    # 过期时把**两个 sha** 和**怎么更新**都打出来——只说"变了"等于把问题丢回给用户。
    stale, skipped = _stale_accept()
    check(not stale, "本地 accept 基线都对得上当前 .tu",
          f"{len(stale)} 份过期")
    if skipped:
        print(f"  （工作区有未提交改动、暂不判基线：{', '.join(skipped)}）")
    for _name, _want, _cur in stale:
        print(f"    {_name}: 基线 {_want[:12]}… ≠ 当前 {_cur[:12]}…")
        print(f"      确认声明没被放宽后更新："
              f"node bin/elench.js build 文档/样例/{_name[:-3]} --accept-tu-change")
    print(f"  （已知债务：{ {k: sorted(v) for k, v in KNOWN_DEBT.items()} }）")
    test_calculator_linear()

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
