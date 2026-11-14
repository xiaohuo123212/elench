#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_cpp_parity.py —— 只测 `tools/cpp_parity.py` 的**纯逻辑**，不真编译。

端到端（19 个模块 × 3 次 g++ + 1 次解释器 run）是 `python -B tools/cpp_parity.py`
的事，约 10–20 秒；放进 `tests/runner.py` 会把整套拖慢，而且**没有 g++ 的机器必然红**。
所以这里钉的是七件**容易悄悄错**的事：

  1. **语义口径复用了 `behavior_parity.strip_return_line`**（不另写一份），
     并且「只剥最后一行整数」——程序自己的 `0` 不会被误删。
  2. **已知未指定清单是断言**：`KNOWN_UNSPECIFIED` 里的模块允许与原 `.cpp` 不同，
     但**必须仍然不同**；哪天变相同 ⇒ 清单过期 ⇒ rc=1（否则会掩盖回归）。
     清单外差异仍然算真实差异，且语义维度永不豁免。
  3. **两个维度的汇总与退出码**：出码/编译/语义 = 19（硬），与原 .cpp ≥ 17（软）。
  4. **`--only` 子集口径**：单模块「语义一致但与原 .cpp 不同」且在清单里 ⇒ rc=0。
  5. **找不到 g++ ⇒ 明确「跳过」且 rc=0**：不许把"没测"说成"通过"。
  6. **临时目录每次运行唯一、只删自己的**（并发假红的根治）：两个 `make_run_dir()`
     不能同名；`clean_run_dir()` 只动传进去的那一个；父目录非空时
     `release_stage()` **不会**删（否则又回到互删）；自检能把「我的没删掉」
     和「别人的还在跑」分开——前者红，后者不红。
  7. **并发实测（默认不跑）**：`python -B tests/cases/test_cpp_parity.py --concurrent`
     真的同时起两个 `cpp_parity.py`，断言两边 rc 与四维数字一致、输出里没有
     「文件不存在」、并且跑完 `build/` 下没有 `cpp_parity_tmp` 残留。
     约 10–30 秒、需要 g++，所以**不进默认套件**（默认套件 < 1 秒、不起子进程）。

第 6 组会建/删 `build/cpp_parity_selftest_<pid>/`（**毫秒级、跑完即删**，build/ 是
gitignored）；第 7 组只在 `--concurrent` 时跑。其余测试不起子进程、不编译。
"""
import contextlib
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
TOOL = os.path.join(ROOT, "tools", "cpp_parity.py")

spec = importlib.util.spec_from_file_location("cpp_parity", TOOL)
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)

FAILED = []
SKIPPED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def skip(name):
    """记一次**跳过**。跳过不算失败，也**绝不算通过**（末尾会单列）。"""
    SKIPPED.append(name)
    print(f"  skip {name}")


# ---------------------------------------------------------------- 假结果表

def corpus_names():
    """19 个模块名；05 / 08 用真名（它们在已知清单里）。"""
    names = [f"{i:02d}_mod" for i in range(1, 20)]
    names[4] = "05_ring_buffer"
    names[7] = "08_bits_hash"
    return names


#: 真实形状：05 与 08 都与原 .cpp 不同（C++ 未指定），其余一致。
REAL_ORIG_DIFFS = {"05_ring_buffer", "08_bits_hash"}


def plain_names():
    """19 个模块名，**一个都不在已知清单里**（测"全好"的上界用）。"""
    return [f"{i:02d}_mod" for i in range(1, 20)]


def make_rows(names, sem_diffs=(), orig_diffs=(), no_emit=(),
              no_compile=(), no_orig=()):
    """按「出码 ⇒ 编译 ⇒ 语义/原 .cpp」的正常链造结果行。"""
    rows = []
    for m in names:
        emit = m not in no_emit
        comp = emit and m not in no_compile
        sem = comp and m not in sem_diffs
        measured = comp and m not in no_orig
        same_orig = measured and m not in orig_diffs
        rows.append({"module": m, "emit": emit, "compile": comp, "sem": sem,
                     "orig_measured": measured, "same_orig": same_orig})
    return rows


# ---------------------------------------------------------------- 1. 语义口径

def test_semantic_judge():
    print("[语义口径：复用 behavior_parity 的剥末行，不另写一份]")
    src = cp.strip_return_line.__code__.co_filename.replace("\\", "/")
    check("strip_return_line 来自 behavior_parity.py（不是另写一份）",
          src.endswith("/tools/behavior_parity.py"), src)

    # CRLF（Windows 下两侧都是 \r\n）也要能剥。
    check("剥末行：LF", cp.strip_return_line(b"a\nb\n0\n") == b"a\nb\n")
    check("剥末行：CRLF", cp.strip_return_line(b"a\r\n0\r\n") == b"a\r\n")
    check("末行不是整数就不剥", cp.strip_return_line(b"a\nNone\n") == b"a\nNone\n")
    check("空输出不炸", cp.strip_return_line(b"") == b"")
    # 程序自己最后打印的 `0` 在 main 返回值之前：只剥一行。
    check("程序自己的 0 不被误删",
          cp.strip_return_line(b"42\n0\n0\n") == b"42\n0\n")

    same, _ = cp.judge_semantic(b"x\r\n", 0, b"x\r\n0\r\n")
    check("生成物 == 解释器（剥末行）⇒ 语义一致", same is True)
    same, why = cp.judge_semantic(b"y\r\n", 0, b"x\r\n0\r\n")
    check("生成物 != 解释器 ⇒ 语义不一致", same is False and "语义不同" in why, why)
    same, why = cp.judge_semantic(b"x\r\n", 1, b"x\r\n0\r\n")
    check("解释器非零退出 ⇒ 不能算一致", same is False and "退出码" in why, why)
    same, why = cp.judge_semantic(b"x\r\n", None, b"")
    check("解释器超时/起不来 ⇒ 不能算一致", same is False and "跑不起来" in why, why)


# ---------------------------------------------------------------- 2. 已知差异

def test_known_unspecified():
    print("[已知差异清单：单列、必须仍不同、不是豁免]")
    check("05_ring_buffer 在清单里", "05_ring_buffer" in cp.KNOWN_UNSPECIFIED,
          str(list(cp.KNOWN_UNSPECIFIED)))
    check("08_bits_hash 在清单里", "08_bits_hash" in cp.KNOWN_UNSPECIFIED,
          str(list(cp.KNOWN_UNSPECIFIED)))
    for name in ("05_ring_buffer", "08_bits_hash"):
        check(f"{name} 写明了未指定原因",
              bool(cp.KNOWN_UNSPECIFIED.get(name, "").strip()))

    # 真实形状：05/08 与原 .cpp 不同，其余全一致，语义 19/19 ⇒ 通过。
    rows = make_rows(corpus_names(), orig_diffs=REAL_ORIG_DIFFS)
    s = cp.summarize(rows)
    check("05/08 的差异不进 orig_diffs", s["orig_diffs"] == [], str(s["orig_diffs"]))
    check("05/08 被单列为 known", s["known"] == ["05_ring_buffer", "08_bits_hash"],
          str(s["known"]))
    check("known 不计入 same_orig", s["same_orig"] == 17, str(s["same_orig"]))
    check("语义仍然 19/19", s["semantic"] == 19, str(s["semantic"]))
    check("真实形状判 ok", s["ok"] is True and cp.exit_code(s) == 0,
          str(s["problems"]))
    check("清单过期列表为空", s["stale_known"] == [], str(s["stale_known"]))

    # **断言**：05 变得与原 .cpp 相同 ⇒ 清单过期，必须红（否则会掩盖回归）。
    rows = make_rows(corpus_names(), orig_diffs={"08_bits_hash"})
    s = cp.summarize(rows)
    check("05 变相同 ⇒ stale_known", s["stale_known"] == ["05_ring_buffer"],
          str(s["stale_known"]))
    check("05 变相同 ⇒ rc=1", cp.exit_code(s) == 1, str(s["problems"]))
    check("失败项点名「清单过期」",
          any("过期" in p for p in s["problems"]), str(s["problems"]))

    # 08 也一样。
    rows = make_rows(corpus_names(), orig_diffs={"05_ring_buffer"})
    s = cp.summarize(rows)
    check("08 变相同 ⇒ stale_known", s["stale_known"] == ["08_bits_hash"],
          str(s["stale_known"]))
    check("08 变相同 ⇒ rc=1", cp.exit_code(s) == 1, str(s["problems"]))

    # 已知模块的**语义**也不豁免：解释器对不上照样红。
    rows = make_rows(corpus_names(), orig_diffs=REAL_ORIG_DIFFS,
                     sem_diffs={"05_ring_buffer"})
    s = cp.summarize(rows)
    check("已知模块语义不一致 ⇒ 语义 18", s["semantic"] == 18, str(s["semantic"]))
    check("已知模块语义不一致 ⇒ rc=1", cp.exit_code(s) == 1, str(s["problems"]))

    # 清单外的差异**必须**被抓住：known 机制不能顺手把真实回归也吞掉。
    rows = make_rows(corpus_names(), orig_diffs=REAL_ORIG_DIFFS | {"07_mod"})
    s = cp.summarize(rows)
    check("清单外差异进 orig_diffs", s["orig_diffs"] == ["07_mod"],
          str(s["orig_diffs"]))
    check("清单外差异让 same_orig 掉到 16", s["same_orig"] == 16,
          str(s["same_orig"]))
    check("清单外差异 ⇒ rc=1", cp.exit_code(s) == 1, str(s["problems"]))

    # 原 .cpp 没跑起来 ⇒ 不能算「相同」，也不能验证已知项的断言。
    rows = make_rows(corpus_names(), orig_diffs=REAL_ORIG_DIFFS,
                     no_orig={"05_ring_buffer"})
    s = cp.summarize(rows)
    check("已知项原 .cpp 未测 ⇒ 断言未验证 + rc=1",
          s["known_unverified"] == ["05_ring_buffer"] and cp.exit_code(s) == 1,
          str(s["problems"]))


# ---------------------------------------------------------------- 3. 汇总 / rc

def test_summary_and_rc():
    print("[汇总与退出码：两个维度各有阈值]")
    s = cp.summarize(make_rows(plain_names()))
    check("全好：出码 19", s["emitted"] == 19, str(s["emitted"]))
    check("全好：编译 19", s["compiled"] == 19, str(s["compiled"]))
    check("全好：语义 19", s["semantic"] == 19, str(s["semantic"]))
    check("全好：与原 .cpp 19", s["same_orig"] == 19, str(s["same_orig"]))
    check("全好：rc=0", cp.exit_code(s) == 0, str(s["problems"]))

    # 真实形状：05/08 未指定 + 0 个真实差异 ⇒ 语义 19、与原 .cpp 17，rc=0。
    s = cp.summarize(make_rows(corpus_names(), orig_diffs=REAL_ORIG_DIFFS))
    check("真实形状：语义 19", s["semantic"] == 19, str(s["semantic"]))
    check("真实形状：与原 .cpp 17", s["same_orig"] == 17, str(s["same_orig"]))
    check("真实形状：rc=0", cp.exit_code(s) == 0, str(s["problems"]))

    # 语义是硬阈值：17 个与原 .cpp 一致但语义 18 ⇒ 红（不能拿软维度换硬维度）。
    rows = make_rows(corpus_names(), orig_diffs=REAL_ORIG_DIFFS,
                     sem_diffs={"07_mod"})
    s = cp.summarize(rows)
    check("语义 18 ⇒ rc=1（硬阈值）", s["semantic"] == 18 and cp.exit_code(s) == 1,
          str(s["semantic"]))
    check("语义失败项点名模块", any("语义" in p for p in s["problems"]),
          str(s["problems"]))

    # 硬：出码少 1 个就失败（MIN_EMITTED = 19 = 全部出码）。
    s = cp.summarize(make_rows(corpus_names(), no_emit={"09_mod"}))
    check("出码 18 ⇒ 编译 18", s["compiled"] == 18, str(s["compiled"]))
    check("出码 18 ⇒ rc=1", cp.exit_code(s) == 1, str(s["problems"]))
    check("未出码单列", s["not_emitted"] == ["09_mod"], str(s["not_emitted"]))

    # 硬：出了码但编不过 ⇒ 编译 < 19 就红（不靠语义/原 .cpp 抵消）。
    rows = make_rows(corpus_names(), orig_diffs=REAL_ORIG_DIFFS,
                     no_compile={"06_mod"})
    s = cp.summarize(rows)
    check("编不过：编译 18", s["compiled"] == 18, str(s["compiled"]))
    check("编不过：not_compiled 单列", s["not_compiled"] == ["06_mod"],
          str(s["not_compiled"]))
    check("编不过：rc=1", cp.exit_code(s) == 1, str(s["problems"]))

    # 软阈值边界：与原 .cpp 17 = 刚好卡线；16 = 失败。
    rows = make_rows(corpus_names(), orig_diffs=REAL_ORIG_DIFFS | {"07_mod"})
    s = cp.summarize(rows)
    check("与原 .cpp 16 ⇒ rc=1", s["same_orig"] == 16 and cp.exit_code(s) == 1,
          str(s["same_orig"]))

    # 语料口径：不是 19 个模块时**不许**用旧阈值放过。
    s = cp.summarize(make_rows(corpus_names()[:18]))
    check("18 个模块 ⇒ rc=1（口径不符）",
          cp.exit_code(s) == 1 and any("语料" in p for p in s["problems"]),
          str(s["problems"]))


# ---------------------------------------------------------------- 4. --only

def test_only_subset():
    print("[--only 子集口径：单模块「语义对、原 .cpp 不同」⇒ rc=0]")
    lim = cp.subset_limits([{"module": "08_bits_hash"}])
    check("子集阈值：语义 = 1", lim["min_semantic"] == 1, str(lim))
    check("子集阈值：与原 .cpp = 0（已知项允许不同）",
          lim["min_same_orig"] == 0, str(lim))

    rows = make_rows(["08_bits_hash"], orig_diffs={"08_bits_hash"})
    s = cp.summarize(rows, **lim)
    check("--only=08：语义 1/1", s["semantic"] == 1, str(s["semantic"]))
    check("--only=08：与原 .cpp 0/1 但已知 ⇒ rc=0",
          s["same_orig"] == 0 and cp.exit_code(s) == 0, str(s["problems"]))

    rows = make_rows(["05_ring_buffer"], orig_diffs={"05_ring_buffer"})
    s = cp.summarize(rows, **cp.subset_limits(rows))
    check("--only=05：rc=0", cp.exit_code(s) == 0, str(s["problems"]))

    # 单模块清单外出现差异 ⇒ 真实回归 ⇒ 红。
    rows = make_rows(["07_dp"], orig_diffs={"07_dp"})
    s = cp.summarize(rows, **cp.subset_limits(rows))
    check("--only 非已知模块有差异 ⇒ rc=1", cp.exit_code(s) == 1,
          str(s["problems"]))

    # 单模块语义不对 ⇒ 红（不因已知而豁免）。
    rows = make_rows(["08_bits_hash"], orig_diffs={"08_bits_hash"},
                     sem_diffs={"08_bits_hash"})
    s = cp.summarize(rows, **cp.subset_limits(rows))
    check("--only 已知模块语义不一致 ⇒ rc=1", cp.exit_code(s) == 1,
          str(s["problems"]))

    # 单模块变成与原 .cpp 相同 ⇒ 清单过期 ⇒ 红。
    rows = make_rows(["08_bits_hash"])
    s = cp.summarize(rows, **cp.subset_limits(rows))
    check("--only 已知模块变相同 ⇒ rc=1", cp.exit_code(s) == 1,
          str(s["problems"]))


# ---------------------------------------------------------------- 5. 没编译器

def test_missing_gxx_skips():
    print("[找不到 g++：明确跳过、rc=0，不许假装通过]")
    orig = cp.find_gxx
    cp.find_gxx = lambda environ=None: None
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = cp.main([])
    finally:
        cp.find_gxx = orig
    out = buf.getvalue()
    check("rc=0", rc == 0, str(rc))
    check("说了「跳过」", "跳过" in out, out[-400:])
    check("说了「未测量」", "未测量" in out, out[-400:])
    check("没冒充「通过」", "✓ 通过" not in out, out[-400:])


# ---------------------------------------------------------------- 6. 语料与阈值

def test_corpus_and_thresholds():
    print("[语料与阈值口径]")
    mods = cp.discover_modules()
    check("语料 19 个模块", len(mods) == 19, str(len(mods)))
    check("00_docnums 被排除", "00_docnums" not in mods, str(mods[:3]))
    check("05_ring_buffer 在语料里", "05_ring_buffer" in mods)
    check("08_bits_hash 在语料里", "08_bits_hash" in mods)
    check("EXPECTED_MODULES 与语料一致", cp.EXPECTED_MODULES == 19,
          str(cp.EXPECTED_MODULES))
    check("出码阈值 = 全部出码（硬）",
          cp.MIN_EMITTED == cp.EXPECTED_MODULES, str(cp.MIN_EMITTED))
    check("编译阈值 = 全部编过（硬）",
          cp.MIN_COMPILED == cp.EXPECTED_MODULES, str(cp.MIN_COMPILED))
    check("语义阈值 = 全部语义一致（硬）",
          cp.MIN_SEMANTIC == cp.EXPECTED_MODULES, str(cp.MIN_SEMANTIC))
    check("与原 .cpp 阈值 ≥ 已知未指定之外的余量",
          cp.MIN_SAME_ORIG < cp.EXPECTED_MODULES, str(cp.MIN_SAME_ORIG))
    check("与原 .cpp 阈值 = 语料数 − 已知未指定数",
          cp.MIN_SAME_ORIG == cp.EXPECTED_MODULES - len(cp.KNOWN_UNSPECIFIED),
          f"{cp.MIN_SAME_ORIG} vs {cp.EXPECTED_MODULES} - "
          f"{len(cp.KNOWN_UNSPECIFIED)}")
    for name, value in (("MIN_EMITTED", cp.MIN_EMITTED),
                        ("MIN_COMPILED", cp.MIN_COMPILED),
                        ("MIN_SEMANTIC", cp.MIN_SEMANTIC),
                        ("MIN_SAME_ORIG", cp.MIN_SAME_ORIG)):
        check(f"{name} 在 (0, 语料数] 内", 0 < value <= cp.EXPECTED_MODULES,
              str(value))


# ---------------------------------------------------------------- 6. 临时目录并发安全

def test_run_dir_isolation():
    """并发假红的**根治点**：唯一目录 + 只删自己的 + 父目录空了才删。

    这组测试是"快的"：只建/删 `build/` 下几个空目录（毫秒级），不编译、
    不起子进程。真正起两个实例的实测在 `test_concurrent_instances()`（`--concurrent`）。
    """
    print("[临时目录：唯一目录、只删自己的、父目录空了才删]")
    stage = os.path.join(ROOT, "build", f"cpp_parity_selftest_{os.getpid()}")
    try:
        a = cp.make_run_dir(stage)
        b = cp.make_run_dir(stage)
        check("两次运行拿到**不同**目录", a != b and a.exists() and b.exists(),
              f"{a} / {b}")
        check("目录名带 pid 前缀（看得出来是谁的）",
              a.name.startswith(f"{os.getpid()}-")
              and b.name.startswith(f"{os.getpid()}-"),
              f"{a.name} / {b.name}")
        check("都在传入的父目录下",
              a.parent == b.parent
              and os.path.normcase(str(a.parent)) == os.path.normcase(stage),
              f"{a.parent} / {b.parent}")

        # **关键**：删自己那份，不能顺手删别人的（旧实现删的是共用的父目录）。
        check("删自己那份返回 True", cp.clean_run_dir(a) is True)
        check("自己的没了", not a.exists())
        check("**别人的没被删**", b.exists(), str(b))
        check("残留报告只剩别人的那份",
              cp.stage_residue(stage) == [b.name], str(cp.stage_residue(stage)))

        # 父目录非空 ⇒ release_stage 必须**拒绝删**（非空 rmdir 必然失败，这里钉住
        # 它没有偷偷用 rmtree 把别人的目录也带走）。
        check("父目录非空 ⇒ 不删（不会误删别人的）",
              cp.release_stage(stage) is False and os.path.isdir(stage))

        # 自检要能分开「我的没删掉」和「别人的还在跑」：前者红，后者**不红**。
        h = cp.hygiene_report(b, True, False, stage=stage)
        check("自检：自己的目录还在 ⇒ 判失败（真的残留）", h["ok"] is False, str(h))
        check("自检：失败时点名自己的目录", "没删掉" in h["note"], h["note"])
        h = cp.hygiene_report(a, True, False, stage=stage)
        check("自检：只剩别人的 ⇒ **不**判失败（并发不是错误）",
              h["ok"] is True and h["mine"] is False and h["others"] == [b.name],
              str(h))
        check("自检：并发时说明是别的实例", "并发" in h["note"], h["note"])

        # --keep 是用户明确要求保留现场：不算残留。
        h = cp.hygiene_report(b, True, True, stage=stage)
        check("自检：--keep 时保留现场不算失败", h["ok"] is True and h.get("kept"),
              str(h))

        # 自己的目录删掉、父目录空了 ⇒ 父目录跟着收掉，build/ 不留空壳。
        check("删掉最后一份返回 True", cp.clean_run_dir(b) is True)
        check("父目录空了 ⇒ 收掉它", cp.release_stage(stage) is True)
        check("**跑完 build/ 下不残留 cpp_parity_tmp**",
              cp.stage_residue(stage) == [] and not os.path.isdir(stage), stage)
        h = cp.hygiene_report(a, True, False, stage=stage)
        check("自检：干净时明说「不存在」", h["ok"] is True and "不存在" in h["note"],
              h["note"])
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    # 崩溃兜底：唯一目录的代价是"崩了的实例没人收尸"，靠 TTL 清（并发实例都新鲜）。
    stage2 = os.path.join(ROOT, "build", f"cpp_parity_selftest_gc_{os.getpid()}")
    try:
        fresh = cp.make_run_dir(stage2)
        stale = cp.make_run_dir(stage2)
        os.utime(stale, (0, 0))                       # 1970：模拟崩溃留下的
        gone = cp.gc_stale_run_dirs(stage2)           # 只调一次：它会把超期的清掉
        check("TTL 兜底：超期的清掉", gone == [stale.name] and not stale.exists(),
              str(gone))
        check("TTL 兜底：新鲜的不清（并发实例不会被误杀）", fresh.exists(), str(fresh))
        check("TTL 兜底：没东西可清时返回空", cp.gc_stale_run_dirs(stage2) == [])
    finally:
        shutil.rmtree(stage2, ignore_errors=True)


# ---------------------------------------------------------------- 7. 并发实测（慢）

#: `汇总：出码 19/19 · 编译 19/19 · 语义一致（vs 解释器）19/19 · 与原 .cpp 一致 17/19`
_SUMMARY_RE = re.compile(
    r"汇总：出码 (\d+)/(\d+) · 编译 (\d+)/(\d+) · "
    r"语义一致（vs 解释器）(\d+)/(\d+) · 与原 \.cpp 一致 (\d+)/(\d+)")


def parse_summary(text):
    """从一次运行的输出里抠出四维（分子/分母共 8 个数）；抠不到返回 None。"""
    m = _SUMMARY_RE.search(text or "")
    return tuple(int(g) for g in m.groups()) if m else None


def test_concurrent_instances(jobs=4, instances=2):
    """**真跑**：同一时刻起两个实例，两边 rc 与四维数字必须一致，跑完不许有残留。

    这就是 2026-11-13 那个假红的回归测试：旧实现下这里会红（一边
    「文件不存在 …\\build\\cpp_parity_tmp\\06_heap.tu」，数字掉到 8/19 或 11/19）。
    **不进默认套件**：要 g++、约 10–30 秒。
    """
    print(f"[并发实测：同时起 {instances} 个实例（--jobs={jobs}），数字必须一致]")
    if cp.find_gxx() is None:
        skip("并发实测（本机没有 g++：并发维度**未测量**，不等于通过）")
        return "skipped"

    stage = os.path.join(ROOT, "build", "cpp_parity_tmp")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    procs = []
    try:
        for _ in range(instances):
            procs.append(subprocess.Popen(
                [sys.executable, "-B", TOOL, f"--jobs={jobs}"],
                cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE))
        outs = []
        for p in procs:
            raw, err = p.communicate(timeout=900)
            outs.append((p.returncode, raw.decode("utf-8", "replace"),
                         err.decode("utf-8", "replace")))
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()

    numbers = []
    for i, (rc, out, err) in enumerate(outs, 1):
        nums = parse_summary(out)
        numbers.append(nums)
        shown = "/".join(str(n) for n in nums) if nums else "**解析失败**"
        print(f"  实例{i}：rc={rc} 四维={shown}")
        if nums is None:
            print("    输出尾部：" + "\n    ".join(out.strip().splitlines()[-6:]))
            if err.strip():
                print("    stderr：" + err.strip().splitlines()[-1])
        # 旧假红的签名：不是数字不对，是**文件被对面删了**。这里直接钉住。
        check(f"实例{i} 输出里没有「文件不存在」", "文件不存在" not in out)
        check(f"实例{i} 输出里没有「出码失败」", "出码失败" not in out)
        check(f"实例{i} 自检没说「没删掉」", "没删掉" not in out)

    check("两个实例都 rc=0", {rc for rc, _, _ in outs} == {0},
          str([rc for rc, _, _ in outs]))
    check("两个实例四维数字**完全一致**",
          len(numbers) == instances and None not in numbers
          and len(set(numbers)) == 1, str(numbers))
    check("数字仍是 19/19 · 19/19 · 19/19 · 17/19",
          (19, 19, 19, 19, 19, 19, 17, 19) in numbers, str(numbers))
    check("跑完 build/ 下不残留 cpp_parity_tmp", not os.path.exists(stage), stage)
    if os.path.exists(stage):                       # 万一残留，别把垃圾留给门禁
        shutil.rmtree(stage, ignore_errors=True)
    return "ran"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--concurrent" in argv:
        # **慢测**：真起两个实例（要 g++，约 10–30 秒）。默认套件不跑它。
        # `--jobs=N` 传给实例（两个实例各开 N 路；本机 24 核，N=8 也不会抢爆）。
        jobs = 4
        for a in argv:
            if a.startswith("--jobs="):
                jobs = int(a.split("=", 1)[1])
        test_concurrent_instances(jobs=jobs)
    else:
        for fn in (test_semantic_judge, test_known_unspecified,
                   test_summary_and_rc, test_only_subset,
                   test_missing_gxx_skips, test_corpus_and_thresholds,
                   test_run_dir_isolation):
            fn()
    if FAILED:
        print(f"\n✗ 失败 {len(FAILED)} 项：{', '.join(FAILED)}")
        return 1
    if SKIPPED:
        # **跳过不是通过**：单列出来，别让"全部通过"盖住"这一组根本没测"。
        print(f"\n通过（但有 {len(SKIPPED)} 组**跳过**，未测量不等于通过）："
              f"{', '.join(SKIPPED)}")
        return 0
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
