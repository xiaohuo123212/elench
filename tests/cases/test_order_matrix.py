#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_order_matrix.py —— **判阶矩阵工具**的纯逻辑单测（不跑 `dsh`、< 1 秒）。

被测对象：`tools/order_matrix.py`（§6「必须判对」的 15 对 + 自加钉子）。

## 钉三件事

  [1] **一份数据**：期望表**只有工具里那一份**。这个测试**不另抄** 15 行期望，
      而是直接读 `order_matrix.PAIRS` / `DOC_PAIRS` / `EXTRA_PAIRS`，
      再核对"工具自己跑出来的行"与这份数据**逐条对齐**（id、顺序、期望）。
      抄第二份的后果是：改了一边、另一边还绿着——那正是这张表要防的事。

  [2] `--json` 的**结构**：顶层字段齐全；`rows` 与 `total` 一致；
      分组小计之和 = 总计；`gate` / `exit_code` 与 `failed` 自洽；
      每行 `status` 与 `verdict` / `expected` 的关系成立
      （`pass` ⇒ 判决在期望内；`fail` ⇒ 判决不在期望内；`unsupported` ⇒ 没有判决）。

  [3] **退出码语义**（这条最关键）：
      · 判错 ⇒ **1**——用 monkeypatch 把一条期望写成反的（`n²` 对 `n log n` 期望
        `ok`），工具必须报 ✗、`failed == 1`、退出码 1；
      · 未支持 ⇒ **0**——用 monkeypatch 造一条"构造不出来"和一条"构造退化成 ⊤"
        的格子，工具必须记 `unsupported`、不计入失败、退出码 0
        （否则符号指数没做完时这道闸会红给别人看，等于把"还没实现"当成"判错了"）。

## 判定本身不在这里测

15 对/21 对的**判决**由工具自己跑（`python -B tools/order_matrix.py`，是真闸）。
这里只钉"数据只有一份""JSON 结构对""退出码语义对"——都 < 1 秒，不依赖语料。

跑法：

    python -B tests/cases/test_order_matrix.py
"""

import contextlib
import importlib.util
import io
import json
import os as _os
import sys as _sys
from fractions import Fraction

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
TOOL = _os.path.join(ROOT, "tools", "order_matrix.py")

try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                      # noqa: BLE001
    pass


def _load_tool():
    """按**文件路径**加载工具（不靠 sys.path，也不指望 `tools/` 里有 `__init__`）。"""
    spec = importlib.util.spec_from_file_location("order_matrix", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


om = _load_tool()

_PASSED = 0
_FAILED = []


def check(cond, name, detail=""):
    global _PASSED
    if cond:
        _PASSED += 1
    else:
        _FAILED.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


def run_cli(argv):
    """跑工具的 `main(argv)`，返回 `(退出码, stdout 文本)`。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = om.main(list(argv))
    return code, buf.getvalue()


def _patch_pairs(pairs):
    """把工具的 `PAIRS` 临时换掉（工具只从这一个全局读表）。"""
    saved = om.PAIRS
    om.PAIRS = tuple(pairs)
    return saved


# ================================================================ [1] 同一份数据

def test_single_source_table():
    print("[1] 期望表只有工具里那一份（测试直接读 PAIRS，不另抄）")

    check(len(om.DOC_PAIRS) == 15, "§6 的对照表正好 15 对",
          f"实际 {len(om.DOC_PAIRS)}")
    check(tuple(p.id for p in om.DOC_PAIRS) == tuple(range(1, 16)),
          "§6 的编号就是 1…15（与设计文档表格同序）",
          str([p.id for p in om.DOC_PAIRS]))
    check(om.PAIRS == om.DOC_PAIRS + om.EXTRA_PAIRS,
          "PAIRS = DOC_PAIRS + EXTRA_PAIRS（唯一一份数据，不是拼出来的第二份）")

    kinds = set(om.KIND_TEXT)
    ids = [p.id for p in om.PAIRS]
    check(len(ids) == len(set(ids)), "编号不重复", str(ids))

    for p in om.PAIRS:
        tag = f"#{p.id}"
        check(isinstance(p.impl, om.Cell) and isinstance(p.target, om.Cell),
              f"{tag}：impl / target 都是 Cell", repr(p))
        check(callable(p.impl.build) and callable(p.target.build),
              f"{tag}：Cell.build 可调用（现场构造，不缓存）")
        check(callable(p.impl.probe) and callable(p.target.probe),
              f"{tag}：Cell.probe 可调用（用来区分「构造退化」与「判错」）")
        check(bool(p.expected) and set(p.expected) <= kinds,
              f"{tag}：期望值是 Verdict.kind 的子集", str(p.expected))
        check(len(p.why.strip()) >= 8,
              f"{tag}：写了「为什么是这个方向」", repr(p.why[:20]))
        check(p.group in om.GROUP_TITLE,
              f"{tag}：分组 {p.group!r} 有表头文字")

    check(all(p.group == "doc15" for p in om.DOC_PAIRS),
          "§6 的 15 对全在 doc15 组（汇总要按 15 计数）")

    # ---- 工具跑出来的行必须与这份数据**逐条对齐**（同一份数据流到了结果里）
    report = om.collect()
    check([r["id"] for r in report["rows"]] == ids,
          "collect() 的行序 = PAIRS 的序（同一份数据，没有第二张表）",
          f"{[r['id'] for r in report['rows']]}")
    mismatch = [r["id"] for p, r in zip(om.PAIRS, report["rows"])
                if list(p.expected) != r["expected"]]
    check(not mismatch, "每行的 expected 就是 Pair.expected（没有另写一份）",
          str(mismatch))

    # ---- 判定必须走真正的 compare（工具里不许出现自写的比较逻辑）
    import inspect
    src = inspect.getsource(om.evaluate_pair)
    check("cc.compare(" in src,
          "evaluate_pair 调的是 cost_check.compare（判定不自己写）")
    check("<=" not in src and ">=" not in src,
          "evaluate_pair 里没有自写的阶比较（`<=` / `>=`）", src)


# ================================================================ [2] --json 结构

_JSON_REPORT = {}


def test_json_structure():
    print("[2] `--json` 的结构（自洽：小计之和 = 总计，gate/exit_code 跟着 failed）")
    code, out = run_cli(["--json"])
    try:
        report = json.loads(out)
    except ValueError as exc:
        check(False, "--json 输出是合法 JSON", f"{exc}: {out[:200]}")
        return
    _JSON_REPORT.update(report)

    top = {"tool", "doc", "var", "total", "passed", "failed", "unsupported",
           "groups", "gate", "exit_code", "rows"}
    check(top <= set(report), "顶层字段齐全", str(sorted(top - set(report))))
    check(report["total"] == len(report["rows"]),
          "total == len(rows)", f"{report['total']} vs {len(report['rows'])}")
    check(report["passed"] + report["failed"] + report["unsupported"]
          == report["total"], "passed + failed + unsupported == total")
    check(report["passed"] + report["failed"] + report["unsupported"] == len(om.PAIRS),
          "总计覆盖 PAIRS 的每一条（没有漏跑的行）")

    groups = report["groups"]
    check(set(groups) == {p.group for p in om.PAIRS},
          "分组就是 PAIRS 里出现过的分组", str(sorted(groups)))
    check(sum(g["total"] for g in groups.values()) == report["total"],
          "分组 total 之和 = 总计")
    for name, g in groups.items():
        check(g["passed"] + g["failed"] + g["unsupported"] == g["total"],
              f"分组 {name} 的小计自洽")
        check(g["total"] == len([r for r in report["rows"] if r["group"] == name]),
              f"分组 {name} 的行数与 rows 一致")
    check(groups["doc15"]["total"] == 15, "doc15 组是 15 条", str(groups["doc15"]))

    ok = report["failed"] == 0
    check(report["gate"] == ("pass" if ok else "fail"),
          "gate 只由 failed 决定（未支持不算失败）")
    check(report["exit_code"] == (0 if ok else 1), "exit_code 跟着 gate")
    check(code == report["exit_code"], "main() 的返回值 == report.exit_code",
          f"{code} vs {report['exit_code']}")

    row_keys = {"id", "group", "impl_label", "target_label", "expected",
                "expected_text", "why", "impl", "target",
                "impl_order", "impl_order_text", "impl_order_source",
                "target_order", "target_order_text", "target_order_source",
                "verdict", "verdict_text", "note",
                "status", "status_text", "detail"}
    for r in report["rows"]:
        tag = f"#{r['id']}"
        check(row_keys <= set(r), f"{tag}：行字段齐全",
              str(sorted(row_keys - set(r))))
        check(r["status"] in om.STATUS_MARK, f"{tag}：status 合法", str(r["status"]))
        check(isinstance(r["expected"], list) and r["expected"]
              and set(r["expected"]) <= set(om.KIND_TEXT),
              f"{tag}：expected 是 kind 的非空列表", str(r["expected"]))
        if r["status"] == "pass":
            check(r["verdict"] in r["expected"],
                  f"{tag}：pass ⇒ 判决落在期望内", f"{r['verdict']} vs {r['expected']}")
        elif r["status"] == "fail":
            check(r["verdict"] is not None and r["verdict"] not in r["expected"],
                  f"{tag}：fail ⇒ 判决不在期望内", f"{r['verdict']} vs {r['expected']}")
            check(bool(r["detail"]), f"{tag}：fail 必须说清原因")
        else:
            check(r["verdict"] is None, f"{tag}：unsupported ⇒ 没有判决",
                  str(r["verdict"]))
            check(bool(r["detail"]), f"{tag}：unsupported 必须说清原因")

    # ---- 表格模式与 JSON 是同一份结果（两种视图，一份数据）
    code2, table = run_cli([])
    check(code2 == code, "表格模式的退出码与 --json 相同")
    check(f"共 {report['total']} 对" in table, "表格模式印的是同一组数",
          table.splitlines()[3] if table.count("\n") > 3 else table[:80])


# ================================================================ [3] 退出码语义

def test_fail_exit_code_is_one():
    print("[3] 有 ✗ ⇒ 退出码 1（monkeypatch 造一条假的判反）")
    fake = om.Pair(999, om.C_N2, om.C_NLOGN, "ok",
                   "**故意写反**：n² 对 n log n 期望达成（真实判决是债务）",
                   group="extra")
    saved = _patch_pairs(list(om.PAIRS) + [fake])
    try:
        code, out = run_cli(["--json"])
        report = json.loads(out)
        row = [r for r in report["rows"] if r["id"] == 999]
        check(len(row) == 1, "假的那条进了结果")
        row = row[0] if row else {}
        check(row.get("status") == "fail", "假的那条被标成 fail（不是 pass）",
              str(row.get("status")))
        check(row.get("verdict") == "debt", "假的那条的判决仍是真实判决 debt",
              str(row.get("verdict")))
        check("判反" in row.get("detail", ""), "原因写「判反」", row.get("detail", ""))
        check(report["failed"] == 1, "failed 恰好 1 条", str(report["failed"]))
        check(report["passed"] == report["total"] - 1,
              "其余各行仍全部判对（假失败不掩盖真通过）",
              f"{report['passed']} / {report['total'] - 1}")
        check(report["gate"] == "fail", "gate = fail", report["gate"])
        check(code == 1, "**退出码 1**", str(code))

        code2, table = run_cli([])
        check(code2 == 1, "表格模式也是退出码 1", str(code2))
        check("✗" in table, "表格里打出 ✗")
        check("闸结论：**失败**" in table, "汇总明说「失败」")
        check("退出码 1" in table, "汇总印出退出码 1")
    finally:
        om.PAIRS = saved

    # 恢复后必须回到绿（证明 patch 没留后遗症）
    check(om.collect()["failed"] == 0, "恢复 PAIRS 后 failed 回到 0")


def test_unsupported_exit_code_is_zero():
    print("[4] 未支持 ⇒ 退出码 0（构造不出来 / 构造退化 都不算判错）")

    def _boom():
        raise TypeError("Cost.pow 不支持符号指数（模拟实现未落地）")

    raised = om.Cell("n^n", _boom, om._probe_symbolic)
    degraded = om.Cell("2^n", lambda: om.Cost.top(), om._probe_symbolic)
    fake = [
        om.Pair(901, raised, om.C_N, "debt", "构造不出来（模拟符号指数未实现）",
                group="extra"),
        om.Pair(902, om.C_N, degraded, "ok", "构造退化成 ⊤（模拟静默降级）",
                group="extra"),
    ]
    saved = _patch_pairs(list(om.PAIRS) + fake)
    try:
        code, out = run_cli(["--json"])
        report = json.loads(out)
        rows = {r["id"]: r for r in report["rows"]}
        check(rows[901]["status"] == "unsupported", "#901 记成 unsupported",
              rows[901]["status"])
        check(rows[901]["verdict"] is None, "#901 没有判决（不是判错）")
        check("构造不出来" in rows[901]["detail"], "#901 说清是构造不出来",
              rows[901]["detail"])
        check(rows[902]["status"] == "unsupported", "#902 记成 unsupported",
              rows[902]["status"])
        check("⊤" in rows[902]["detail"], "#902 说清是构造退化（probe 抓的）",
              rows[902]["detail"])
        check(report["unsupported"] == 2, "unsupported 单独计数 2",
              str(report["unsupported"]))
        check(report["failed"] == 0, "未支持**不计入** failed",
              str(report["failed"]))
        check(report["passed"] == report["total"] - report["unsupported"],
              "原有各行仍全部判对", f"{report['passed']}")
        check(report["gate"] == "pass", "gate = pass（未支持不是失败）")
        check(code == 0, "**退出码 0**", str(code))

        code2, table = run_cli([])
        check(code2 == 0, "表格模式也是退出码 0", str(code2))
        check("未支持 2 条" in table, "汇总把未支持单列出来")
        check("不是判错" in table, "明说「不是判错」")
        check("退出码 0" in table, "汇总印出退出码 0")
    finally:
        om.PAIRS = saved

    check(om.collect()["unsupported"] == 0, "恢复 PAIRS 后 unsupported 回到 0")


# ================================================================ [5] --explain

def test_explain_and_display():
    print("[5] `--explain` 逐条说方向；阶那一栏只是展示（与判定分开）")
    code, out = run_cli(["--explain"])
    check(code == 0, "--explain 的退出码与判定一致", str(code))
    check(out.count("为什么这个方向：") == len(om.PAIRS),
          "每条都打印了「为什么这个方向」",
          f"{out.count('为什么这个方向：')} / {len(om.PAIRS)}")
    for p in om.PAIRS:
        if p.why[:10] not in out:
            check(False, f"#{p.id} 的 why 出现在 --explain 里", p.why[:20])
            break
    else:
        check(True, "每条 why 原文都出现在 --explain 里")

    # 阶：结构族走结构三元组，符号指数走 log 形式（并**标出来源**）
    xs = om.Cost.size(om.VAR)
    p_n2, src_n2 = om.order_of(om.Cost.mul(xs, xs))
    check(p_n2 == (2, 0, 0) and src_n2 == "结构", "n² 的阶 (2,0,0) / 结构",
          f"{p_n2} / {src_n2}")
    p_nn, src_nn = om.order_of(om.Cost.pow(xs, xs), use_log=True)
    check(p_nn == (1, 1, 0) and src_nn == "log域",
          "n^n 展示的是 **log 形式**的阶 (1,1,0) / log域", f"{p_nn} / {src_nn}")
    check(om._profile_text((1, 1, 0)) == "n·log n", "三元组印成人话",
          om._profile_text((1, 1, 0)))
    check(om._profile_text((Fraction(3, 2), 0, 0)) == "n^(3/2)",
          "分数次数带括号（不写成会被读错的 n^3/2）",
          om._profile_text((Fraction(3, 2), 0, 0)))


# ================================================================ 入口

def main():
    test_single_source_table()
    test_json_structure()
    test_fail_exit_code_is_one()
    test_unsupported_exit_code_is_zero()
    test_explain_and_display()

    print()
    if _FAILED:
        print(f"失败 {len(_FAILED)} / {_PASSED + len(_FAILED)}")
        for f in _FAILED:
            print("  -", f)
        return 1
    print(f"全部通过：{_PASSED} 项")
    return 0


if __name__ == "__main__":
    _sys.exit(main())
