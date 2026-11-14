#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_explain.py —— 超界归因（`elench cost --explain`）

## 它守的是什么

1. **硬约束**：开 `--explain` 的 κ 必须与不开时**完全一致**——归因是只读的。
2. **指得准**：`sort_small`（插入排序）的元凶必须是**第 7 行那个外层 while**，
   而不是"某个 n² 项"这种含糊说法。
3. **不许沉默**：拿不到行号或没有单一元凶时，必须**显式说**，不能输出空段。
4. **确定性**：同一输入两次输出逐字节相同（报告要能进 CI）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
DSH = os.path.join(SRC, "dsh.py")

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(args):
    r = subprocess.run([sys.executable, DSH] + args, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def kappa_lines(text):
    return [l.strip() for l in text.splitlines() if "κ ≤" in l]


def main() -> int:
    sm = os.path.join(ROOT, "demo", "repair", "v1", "sort_small")
    ex = os.path.join(ROOT, "文档", "入门", "例子", "pick")

    print("[1] 硬约束：--explain 不改变 κ")
    for m in (sm, ex):
        _r1, a = run(["cost", m])
        _r2, b = run(["cost", m, "--explain"])
        check(f"{os.path.basename(m)}：κ 一致", kappa_lines(a) == kappa_lines(b),
              f"{kappa_lines(a)[:1]} vs {kappa_lines(b)[:1]}")

    print("[2] 指得准：元凶是外层 while 那一行")
    _rc, out = run(["cost", sm, "--explain"])
    check("有「支配项」", "支配项" in out, out[-200:])
    check("有「元凶」", "元凶" in out)
    check("指向第 7 行", "第 7 行" in out, [l for l in out.splitlines() if "元凶" in l])
    check("带出源码片段 while", "while (i < xs.size())" in out)
    check("标注为循环构造", "循环" in out)

    print("[3] 最小修复：说出拿掉它能不能达标")
    check("有「最小修复」", "最小修复" in out)
    check("这条能达标（或明说不能）",
          "即可达标" in out or "没有单一元凶" in out, out[-200:])

    print("[4] 确定性：两次输出逐字节相同")
    _rc, out2 = run(["cost", sm, "--explain"])
    check("两次一致", out == out2)

    # ---------------------------------------------------------------- 缺陷 1
    print("[5] deg() 认 sym（标量形参）：支配项不再被指成常数")
    sys.path.insert(0, SRC)
    from symbolic import Cost                       # noqa: E402
    import cost_explain as ce                       # noqa: E402
    hi = Cost("sym", "hi")
    sz = Cost.size("xs")
    check("deg(sym) = 1（与尺寸量同阶）", ce.deg(hi) == 1, str(ce.deg(hi)))
    check("deg(log2(sym)) = 0（与 log2(size) 同档）",
          ce.deg(Cost.log2(hi)) == 0, str(ce.deg(Cost.log2(hi))))
    check("deg(sym×39) = 1（不再当成常数）",
          ce.deg(Cost.mul(hi, Cost.num(39))) == 1)
    check("deg(n×sym) = 2", ce.deg(Cost.mul(sz, hi)) == 2)
    check("deg(n) = 1（原有口径不变）", ce.deg(sz) == 1)

    blur = os.path.join(ROOT, "文档", "样例", "blur")
    _rc, bout = run(["cost", blur, "--explain"])
    dom = next((l.strip() for l in bout.splitlines() if "支配项" in l), "")
    lever = [l.strip() for l in bout.splitlines()
             if "杠杆" in l and "%" in l]
    check("blur 支配项不是常数 4", dom.split()[1:2] != ["4"], dom)
    check("blur 支配项含 h×(…)", "h×" in dom, dom)
    check("blur 支配项尺寸指数 2", "尺寸指数 2" in dom, dom)
    check("blur 支配项与最大杠杆同向（都指 h×…）",
          bool(lever) and "h×" in lever[0], str(lever[:1]))

    # ---------------------------------------------------------------- 缺陷 2
    print("[6] target_trusted 接到 CLI：只有 spec 的被调函数要标注")
    tmp = tempfile.mkdtemp(prefix="elench-trust-")
    try:
        with open(os.path.join(tmp, "outer.tu"), "w", encoding="utf-8") as f:
            f.write(
                "spec inner(u64 n) -> u64 {\n"
                "    effects {};\n"
                "    target { time: { max: O(n) }, allocation: { max: 0 }, };\n"
                "}\n"
                "spec outer(u64 n) -> u64 {\n"
                "    effects {};\n"
                "    target { time: { max: O(n) }, allocation: { max: 0 }, };\n"
                "}\n")
        with open(os.path.join(tmp, "outer.eln"), "w", encoding="utf-8") as f:
            f.write("pub u64 outer(u64 n) { return 1 + inner(n); }\n")
        mod = os.path.join(tmp, "outer")
        _rc, tout = run(["cost", mod, "--explain"])
        check("人读：多一行「按声明的 target 计」",
              "按**声明的 target** 计" in tout, tout[-400:])
        check("人读：点名 inner 只有 spec、没有实现",
              "`inner` 只有 spec、没有实现" in tout)
        _rc, tjs = run(["cost", mod, "--json"])
        try:
            tdata = json.loads(tjs)
        except Exception:                          # noqa: BLE001
            tdata = {}
        fo = (tdata.get("functions", {}) or {}).get("outer", {}) or {}
        check("JSON：target_trusted = [{callee, reason}]",
              fo.get("target_trusted") == [{"callee": "inner",
                                            "reason": "no_impl"}],
              str(fo.get("target_trusted")))
        check("标注不改变判定（仍 ok）", fo.get("verdict") == "ok",
              str(fo.get("verdict")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------------------------------------------------------------- 回归红线
    print("[7] 回归红线：没有信任项时 JSON 形状不变，merge 的 explain 逐字不变")
    merge = os.path.join(ROOT, "文档", "样例", "merge")
    _rc, mjs = run(["cost", merge, "--json"])
    try:
        mdata = json.loads(mjs)
    except Exception:                              # noqa: BLE001
        mdata = {}
    check("merge JSON 不带 target_trusted 键",
          bool(mdata.get("functions")) and all(
              "target_trusted" not in f for f in mdata["functions"].values()))
    _rc, mout = run(["cost", merge, "--explain"])
    mdoms = [l.strip() for l in mout.splitlines() if "支配项" in l]
    check("merge 支配项逐字不变",
          "支配项    2 + n(xs)×log2(n(xs))×10 + 1      ← 尺寸指数 1" in mdoms,
          str(mdoms))

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
