# -*- coding: utf-8 -*-
"""静态分段建议（`src/segment_plan.py`）：**解方程**，不测量。

守护三件事：
1. 两条线性成本式的交点算得对（`gap` 的 2,715/2,716 就是这条）；
2. 常数 / 对数 / 多候选都能给出**连续、覆盖整个区间**的分段；
3. 带符号项（`⊤`、`k(f)`）的候选被**跳过**而不是被硬猜成某个数。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cost_domain.symbolic import Cost          # noqa: E402
from segment_plan import plan, render          # noqa: E402

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


def lin(a: int, b: int) -> Cost:
    """a·n + b"""
    return Cost.add(Cost.mul(Cost.num(a), Cost.size("xs")), Cost.num(b))


def test_linear():
    print("[1] 两条线性式：交点与分段")
    res = plan({"heavy": lin(104, 110), "setup": lin(23, 220042)}, "xs", 8, 65536)
    xs = res["crossings"]
    check(len(xs) == 1, "一个交点", str(xs))
    if xs:
        n, a, b = xs[0]
        # 104n+110 = 23n+220042 → n = 219932/81 = 2715.2 → 首个"乙更小"是 2716
        check(n == 2716, "交点 = 2716（解析解 2715.2 的上取整）", str(n))
        check((a, b) == ("heavy", "setup"), "交点两侧的赢家对", f"{a}/{b}")
    segs = res["segments"]
    check(segs[0][0] == 8 and segs[-1][1] == 65536,
          "分段覆盖整个声明区间", str(segs[:1] + segs[-1:]))
    check([s[2] for s in segs] == ["heavy", "setup"],
          "小 n 用 heavy、大 n 用 setup", str([s[2] for s in segs]))
    check(all(segs[i][1] + 1 == segs[i + 1][0] for i in range(len(segs) - 1)),
          "分段首尾相接（无空洞、无重叠）", str(segs))


def test_const_vs_linear():
    print("[2] 常数 vs 线性")
    res = plan({"fixed": Cost.num(1000), "grow": Cost.size("xs")}, "xs", 1, 4096)
    segs = res["segments"]
    # n=1 时 n < 1000（线性更小）；n=4096 时 1000 < 4096（常数更小）
    check(segs[0][2] == "grow" and segs[-1][2] == "fixed",
          "小 n 线性更小、大 n 常数更小", str([s[2] for s in segs]))
    if res["crossings"]:
        check(res["crossings"][0][0] == 1001,
              "交点 = 1001（n > 1000 时线性更大）", str(res["crossings"][0][0]))


def test_log():
    print("[3] 对数 vs 平方：n log n 永远 ≤ n²（不该报交点）")
    nlogn = Cost.mul(Cost.size("xs"), Cost.log2(Cost.size("xs")))
    res = plan({"nlogn": nlogn, "n2": Cost.mul(Cost.size("xs"), Cost.size("xs"))},
               "xs", 2, 4096)
    check(res["crossings"] == [], "没有交点时不硬造一个", str(res["crossings"]))
    check(len(res["segments"]) == 1 and res["segments"][0][2] == "nlogn",
          "整段都是 n log n", str(res["segments"]))

    # 真有一个交点：常数 100 vs n log n（n=2 时 100 大，n=4096 时 n log n 大）
    res2 = plan({"fixed": Cost.num(100), "nlogn": nlogn}, "xs", 2, 4096)
    check(len(res2["crossings"]) == 1, "常数 vs n log n 有一个交点",
          str(res2["crossings"]))
    if res2["crossings"]:
        n = res2["crossings"][0][0]
        check(2 < n < 4096, f"交点落在区间内（n={n}）", str(n))


def test_single_and_symbolic():
    print("[4] 单候选 / 符号项")
    res = plan({"only": lin(3, 5)}, "xs", 8, 1024)
    check(len(res["segments"]) == 1 and res["segments"][0][2] == "only",
          "只有一个候选 → 一整段", str(res["segments"]))

    res2 = plan({"ok": lin(3, 5), "sym": Cost("sym", "k(f)")}, "xs", 8, 1024)
    check(res2["skipped"] == ["sym"], "符号项候选被跳过（不硬猜）", str(res2["skipped"]))
    check(all(s[2] == "ok" for s in res2["segments"]),
          "跳过后只剩能算的那一个", str(res2["segments"]))

    text = render("kernel", res2)
    check("跳过" in text, "render 里明说跳过了谁", text)


def test_multi_candidates():
    print("[5] 多个候选（≥3）：argmin 分段、被支配的候选不出现、两次交叉")
    n = Cost.size("xs")
    # 三候选：常数 500 / 线性 n / 平方 n²
    res = plan({"const500": Cost.num(500), "linear": n,
                "quad": Cost.mul(n, n)}, "xs", 1, 4096)
    names = [s[2] for s in res["segments"]]
    check(names == ["linear", "const500"], "三候选切出正确的两段", str(names))
    check("quad" not in names, "被支配的候选不出现在任何段里", str(names))

    # 两次交叉：n² 先赢（小 n），再 50n，最后常数
    res2 = plan({"quad": Cost.mul(n, n), "lin50": Cost.mul(Cost.num(50), n),
                 "const": Cost.num(100000)}, "xs", 1, 4096)
    names2 = [s[2] for s in res2["segments"]]
    check(names2 == ["quad", "lin50", "const"],
          "三次换手（两个交点）顺序正确", str(names2))
    check(len(res2["crossings"]) == 2, "交点数量 = 2", str(res2["crossings"]))
    segs2 = res2["segments"]
    check(all(segs2[i][1] + 1 == segs2[i + 1][0] for i in range(len(segs2) - 1)),
          "多段仍然首尾相接", str(segs2))


def test_gap_integration():
    print("[5] 真实例子 gap：和 `elench cost` 打印的同一套数")
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from parser import parse_source
    from cost_interp import CostInterp

    base = os.path.join(ROOT, "文档", "入门", "例子")
    tu = parse_source(open(os.path.join(base, "gap.tu"), encoding="utf-8").read(),
                      "gap.tu").module
    eln = parse_source(open(os.path.join(base, "gap.eln"), encoding="utf-8").read(),
                       "gap.eln").module
    ci = CostInterp(eln, tu)
    cands = ci.candidate_costs("kernel", {})
    check(set(cands) == {"by_scan_heavy", "by_scan_setup"},
          "拿到两个候选各自的 κ", str(sorted(cands)))
    res = plan(cands, "xs", 8, 65536)
    check(res["crossings"] and res["crossings"][0][0] == 2222,
          "交点 2222", str(res["crossings"]))


def main() -> int:
    test_linear()
    test_const_vs_linear()
    test_log()
    test_single_and_symbolic()
    test_multi_candidates()
    test_gap_integration()
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
