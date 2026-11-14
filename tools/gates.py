#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""gates.py —— 12 道门**并发**跑：把「验证一轮 2–3 分钟」压到最短

## 为什么

每轮改完要串行跑 12 个门，**2–3 分钟**全花在等上面。这 12 个门**互相独立**
（没有一个是另一个的前置），串行纯粹是历史写法，不是依赖关系。
并行跑它们不改变任何判据——**只会更快拿到同一个结论**。

**唯一的例外速度**：`cpp_parity`（第 12 道，2026-11-13 加）要编 19×2 次 C++
（外加 `dsh cpp` 自己的编译自检和 19 次解释器 `run`），本机 4 路并发实测约 7 秒，
仍是秒级；嫌慢就 `--only=cpp_parity` 单独跑，或 `--only=` 跳过它。

## 门与并发的关系（别把 CPU 抢爆）

`tests/runner.py` 自己已经 `-j 4`（它内部再开 4 个测试进程）。所以这里
**默认最多 4 个门同时跑**，不是 9 个：9 × 4 = 36 个 Python 进程同时抢核，
上下文切换反而让总时间变长。用 `--jobs=` 调（机器核多可以试 6–8）。

**Windows 怎么并发**：这里用 `concurrent.futures.ThreadPoolExecutor` + `subprocess.run`，
**不是** `multiprocessing`。原因是每个门本来就是**独立进程**（`subprocess` 会释放 GIL），
线程池只负责「同时等几个子进程」，所以不存在 `spawn` 的 pickle 问题，
也不需要 `if __name__ == "__main__"` 之外的重入保护。

## 用法

    python -B tools/gates.py                 # 并发跑 12 道门（默认 --jobs=4）
    python -B tools/gates.py --serial        # 串行跑一遍，做耗时基准
    python -B tools/gates.py --both          # 先串行、再并发，**同一次调用里给出加速比**
    python -B tools/gates.py --jobs=6        # 核多就开大
    python -B tools/gates.py --only=doc_links,kappa_vs_ops
    python -B tools/gates.py --list          # 只列门，不跑
    python -B tools/gates.py --json=out.json # 机器可读（含失败门的输出尾部）
    python -B tools/gates.py --check-env     # 本机依赖自检：哪些门能跑、缺哪些 gitignored 路径
    python -B tools/gates.py --strict-env    # 跳过即失败（CI 用，见下）

## 本地依赖与"跳过"（`GATE_NEEDS`）

12 道门里有 4 道读的是**本机才有的内部材料**——`文档/设计/`、`文档/其他/`、以及测量产物
`*.dispatch.yaml`（都在 `.gitignore` 里）。干净 checkout 上这 4 道**必然红**，但红得没有
信息量（全是"文件不存在"）。`GATE_NEEDS` 把依赖**写成声明**（证据是逐个读门源码得到的，
见那张表的注释）。

正常跑门时，依赖缺失的门**不执行**，打一行 `− 名称 跳过（缺本地 fixtures：路径）`，
汇总里单列"跳过 N"；**跳过不算失败，退出码仍是 0**。跳过只在声明依赖确实缺失时发生——
本机依赖齐全时 12 道门全跑，红就是红。要"环境不完整即失败"（CI 上不该静默跳过）：
加 `--strict-env`（= `--strict`，同一个开关，两个写法）。`--only=` **点名的门不会跳过**：
它缺依赖就直接 `✗ 门 X 需要 …，本机没有`、退出码 `1`。

## 输出

每条一行：`✓/✗/− 名称 耗时 末行`；末尾给**总耗时**、**串行估算（Σ 各门耗时）**、
**加速比**、**跳过原因**与**失败列表**。退出码：全绿（含跳过）`0`，否则 `1`
（`--list` 恒 `0`；`--check-env` 也是**自检**、有缺失仍 `0`，除非再加 `--strict`）。

## 已知限制

- 「串行估算 = Σ 各门耗时」在**并发跑时**是偏高估计：并发下各门的墙钟被 CPU 竞争拉长，
  所以估算出的加速比**偏保守**。要精确对比请用 `--both`（真的各跑一遍）。
- 子进程统一 `PYTHONIOENCODING=utf-8`、`PYTHONPATH=src`、`cwd=仓库根`；
  否则 `tests/cases/*` 与 `tools/*` 在 GBK 控制台下会死在打印 `✓` 上（`tests/runner.py` 踩过）。
- 门之间有**文件级**耦合时并发会假红（例如一个门生成、另一个门校验同一份产物）。
  现在这 12 个门互不写同一份产物；将来加门若出现假红，先查这一点再怀疑代码。
- **同一个门被跑两遍**也算并发：`cpp_parity` 曾经因为两个实例共用一个临时目录而
  互删文件（报「与原 .cpp 一致 11/19」「文件不存在 …06_heap.tu」，单独重跑就恢复）。
  2026-11-13 改成每次运行一个唯一目录后，门禁和手工同时跑不会再假红；
  回归自检：`python -B tests/cases/test_cpp_parity.py --concurrent`（要 g++，约 10–30s）。
- `cpp_parity` 需要本机有 g++：找不到时它**明确报「跳过」并 rc=0**（用户口径：
  "不许把跳过当通过"，所以它在汇总里单列，而不是冒充测过）。要把它变成硬失败，
  设 `ELENCH_GXX` 指到编译器即可；这不算 `GATE_NEEDS`（那是"仓库内路径"表，
  编译器在系统里，不在仓库里）。
- 另一个代理正在改 `src/` 时，`runner` / `kappa_vs_ops` 可能因为半成品而红——**如实红**，
  这不是门禁的问题，也不该用重跑掩盖。
"""

import concurrent.futures as cf
import io
import json
import os
import subprocess
import threading
import sys
import time

#: 中文 Windows 控制台默认 GBK：`✓` / `✗` 会 `UnicodeEncodeError`，
#: 而且**死在打印时**——一行结果都看不到。tests/runner.py 里踩过同一个坑。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")

#: 12 道门。**(名称, 脚本相对路径, 额外参数)** —— 顺序就是输出顺序。
#: 名称只用于显示；改脚本路径时这里跟着改，`--list` 能自查。
GATES = [
    ("runner",         "tests/runner.py",                  ["-j", "4"]),
    ("golden",         "tools/golden_check.py",            []),
    # **κ 基线**（2026-11-11）：`golden` 只看"κ 字符串变没变"，这道门看"变松了没有"。
    # 判据在 `tools/cost_baseline.py` 的文件头：更紧 ✓ / 变松 ✗ / 无法判定 ✗。
    # 它只读**入库**的 `文档/样例` + `tests/baselines/cost.json`，干净 checkout 也能跑。
    ("cost_baseline",  "tools/cost_baseline.py",           ["--check"]),
    ("doc_numbers",    "tools/doc_numbers.py",             ["--check"]),
    ("doc_links",      "tools/doc_links.py",               []),
    ("doc_sync",       "src/sync_docs.py",                 ["--check"]),
    ("mirror_in_sync", "tests/cases/test_mirror_in_sync.py", []),
    ("doc_snippets",   "tests/cases/test_doc_snippets.py", []),
    ("diag_codes",     "tools/check_diag_codes.py",        []),
    ("kappa_vs_ops",   "tools/kappa_vs_ops.py",            ["--strict"]),
    # **判阶矩阵**（2026-11-12）：把"必须判对"的 27 对对照表变成一道闸。
    # 判错 ⇒ 退出码 1；"构造不出来"记 unsupported 不算判错（防冤枉实现）。
    ("order_matrix",   "tools/order_matrix.py",            []),
    # **两个维度**（2026-11-13 补齐，第 12 道）：`dsh cpp` 生成的 C++ 编译运行后
    #   ① 语义：与解释器 `dsh run --compat` 的 stdout 逐字节一致（**硬 19/19**，
    #      解释器是语言的语义定义；末行 main 返回值按 behavior_parity 的口径剥掉）；
    #   ② 行为：与原 `.cpp` 各自 g++ -O0 编出来、跑出来，逐字节比 stdout（软 ≥17；
    #      05 实参求值顺序、08 unordered_map 迭代顺序是 C++ 未指定，清单里断言
    #      「必须仍不同」，变相同就说明清单过期并 rc=1，以免掩盖回归）。
    # 找不到 g++ 时工具自己报"跳过"并 rc=0，不冒充通过。
    # **并发安全**（2026-11-13）：临时目录每次运行唯一（`build/cpp_parity_tmp/<pid>-…/`），
    # 所以"门禁跑着的同时手工再跑一次"不会再互删临时文件、不会假红。
    ("cpp_parity",     "tools/cpp_parity.py",              []),
]

#: 每道门**在本机**额外需要哪些 gitignored 路径（缺了这道门只能在本机跑，
#: 干净 checkout 上会红）。**旁挂一张表**而不是塞进 GATES 的第 4 个元素：
#: 后者要改 5 处解包（`for (n, s, e) in gates` / `--list` / `--only` 等），
#: 这里只加一个 dict。
#:
#: 每条都是**读源码核实**的（2026-11-10），不是按印象列的：
#:
#: - `runner`：`tests/cases/test_doc_numbers.py:47-51` 跑 `doc_numbers.py --list`
#:   并断言输出里没有「—」；缺 `table7.json` 时 `resolve()` 返回 `None`
#:   （`doc_numbers.py:94-96`）→ `cmd_list` 打「—」（`:116-117`）→ 该用例红。
#:   `tests/cases/test_piecewise.py:123-135` 要求 `count` 的 κ(16) < 6000；
#:   实测无 `count.dispatch.yaml` 时是 **6,502**（有表 5,046）→ 红。
#:   `tests/cases/test_policy.py:61-69` 要 `cost gap` 打印**分段** κ（`n(xs)×102` /
#:   `n(xs)×21`），这只能来自 `文档/入门/例子/gap.dispatch.yaml`；无表时是单条 max → 红。
#:   （在干净 checkout 上实测：runner 恰好红这 3 个用例。）
#: - `doc_numbers`：`--check` 默认查 `_docs` 里的预印本（`doc_numbers.py:192`，
#:   缺文件直接 `✗ 文档不存在` 计 bad，`:126-129`），三个绑定又都读 `table7.json`
#:   （`doc_numbers.json:4-19` → `:94-96`）。
#: - `doc_links`：公开文档里有 2 个相对链接指向 `文档/设计/…`
#:   （`文档/内建函数.md:300,302`）；缺 `文档/设计/` 就是死链（`doc_links.py:63-65`）。
#: - `diag_codes`：`DOCS` 含 `文档/设计/目前设计/AI语言设计规格.md`
#:   （`check_diag_codes.py:39`）；缺它 `doc_codes()` 静默少 87 个码 → 基线里多出
#:   31 个 `only_in_code` → 红（实测：全量 155 码，缺规格 68 码）。
#:
#: 其余 5 道只读**入库**文件（`文档/样例`、`文档/入门`、`文档/` 顶层、`src/`、
#: `editors/vscode/python/`），干净 checkout 也能跑；`kappa_vs_ops` 会**可选**读
#: `*.dispatch.yaml`，但缺表时实测仍 `--strict` 通过（12 个函数三档成立 ≥ 8），
#: 所以不登记为依赖。`doc_sync` 也会扫 `文档/设计/归档/`，但**文件缺失只是少查**，
#: 不会红——同样不登记。
GATE_NEEDS = {
    "runner": ["文档/其他/预印本/table7.json",
               "文档/样例/count.dispatch.yaml",
               "文档/入门/例子/gap.dispatch.yaml"],
    "doc_numbers": ["文档/其他/预印本/预印本-v0.1.md",
                    "文档/其他/预印本/table7.json"],
    "doc_links": ["文档/设计/"],
    "diag_codes": ["文档/设计/目前设计/AI语言设计规格.md"],
}

DEFAULT_JOBS = 4

#: **重编译 / 重 CPU 的门，彼此互斥**（2026-11-14 实测出来的）：
#: `runner`（内部还有 `-j 4`）、`cpp_parity`（4 路并发调 g++）、`kappa_vs_ops`（跑三档规模）
#: 三者同跑会互相抢 CPU，**runner 会打出 3–4 个假红**（单独重跑 192/192 全绿，
#: 而 gates 里报 189/192）。假红比红更坏：它让人"重跑一次就好了"，把真红也一起习惯掉。
#: 所以这三道门持同一把锁串行执行；其余轻门照旧并行。
EXCLUSIVE = frozenset({"runner", "cpp_parity", "kappa_vs_ops"})
_EXCLUSIVE_LOCK = threading.Lock()
DEFAULT_TIMEOUT = 1800

USAGE = """用法: gates.py [选项]

  --jobs=N        最多几个门同时跑（默认 4；runner 自己还有 -j 4）
  --serial        串行跑（耗时基准；退出码判据与并发完全一致）
  --both          先串行、再并发，同一次调用里给出真实加速比（耗时约翻倍）
  --only=a,b      只跑这些门（名字见 --list）
  --timeout=N     单道门超时秒数（默认 1800）
  --json=PATH     把结果写成 JSON（含失败门的输出尾部）
  --list          只列门，不跑
  --check-env     只做本地依赖自检：哪些门在本机能跑、缺哪些 gitignored 路径（不跑门）
  --strict        有门因缺本地 fixtures 被跳过就返回 1；也用于 --check-env
  --strict-env    与 --strict 同义（CI 里写明"环境不完整就算失败"）
  -h, --help      显示本帮助

退出码：0 = 全绿（跳过不计失败）；1 = 有门失败（含超时 / 脚本缺失）、
        --strict 下有门被跳过、或 --only 点名的门缺依赖；2 = 用法错误
        --check-env 有缺失也返回 0，加 --strict / --strict-env 才返回 1
"""


# ---------------------------------------------------------------- 参数

class UsageError(Exception):
    pass


_VALUE_OPTS = {"jobs", "only", "timeout", "json"}
_FLAG_OPTS = {"help", "serial", "both", "list", "check_env", "strict", "strict_env"}


def parse_args(argv):
    opts = {"jobs": DEFAULT_JOBS, "serial": False, "both": False, "list": False,
            "check_env": False, "strict": False, "strict_env": False,
            "only": None, "timeout": DEFAULT_TIMEOUT, "json": None, "help": False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            opts["help"] = True
            i += 1
            continue
        if not a.startswith("--"):
            raise UsageError(f"不认识的参数：{a}")
        key, eq, val = a[2:].partition("=")
        # `--check-env` 是唯一带连字符的选项；内部统一用下划线，免得 dict 键两种写法
        key = key.replace("-", "_")
        if key not in _VALUE_OPTS and key not in _FLAG_OPTS:
            raise UsageError(f"不认识的选项：--{key}")
        if key in _FLAG_OPTS:
            if eq:
                raise UsageError(f"--{key} 是开关，不取值")
            opts[key] = True
            i += 1
            continue
        if not eq:
            if i + 1 >= len(argv):
                raise UsageError(f"--{key} 需要取值（写成 --{key}=值 更稳）")
            val, i = argv[i + 1], i + 2
        else:
            i += 1
        if key == "jobs":
            opts["jobs"] = _int_opt(key, val)
        elif key == "timeout":
            opts["timeout"] = _int_opt(key, val)
        elif key == "only":
            opts["only"] = [s.strip() for s in val.split(",") if s.strip()]
        elif key == "json":
            opts["json"] = val
    if opts["serial"] and opts["both"]:
        raise UsageError("--serial 与 --both 二选一（--both 里已经包含一次串行）")
    # `--strict-env` 只是 `--strict` 在 CI 里更好念的写法：同一个开关，不做第二套语义。
    opts["strict"] = opts["strict"] or opts["strict_env"]
    return opts


def _int_opt(key, val):
    try:
        v = int(val)
    except ValueError:
        raise UsageError(f"--{key} 要一个整数：{val!r}")
    if v <= 0:
        raise UsageError(f"--{key} 必须为正：{v}")
    return v


# ---------------------------------------------------------------- 跑一道门

def child_env():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # 测试住在 tests/cases/，`import interp` 靠 PYTHONPATH 找到 src/
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONUTF8", "1")
    return env


def _tail(out, n=40, width=4000):
    lines = [ln for ln in (out or "").splitlines() if ln.strip()]
    return "\n".join(lines[-n:])[-width:]


def _last_line(out):
    for line in reversed((out or "").splitlines()):
        if line.strip():
            return " ".join(line.split())[:110]
    return "(无输出)"


def run_gate(name, script, extra, timeout):
    """跑一道门，返回结果 dict。**绝不抛异常**——一道门崩了不该拖垮其他门。"""
    path = os.path.join(ROOT, script)
    row = {"name": name, "script": script, "argv": extra, "rc": None,
           "ok": False, "secs": 0.0, "last": "", "output_tail": ""}
    if not os.path.exists(path):
        row["last"] = f"脚本缺失：{script}"
        row["output_tail"] = row["last"]
        return row

    argv = [sys.executable, "-B", path] + list(extra)
    t0 = time.perf_counter()
    # 重编译门互斥：见 EXCLUSIVE 的注释（并发会造出假红）。
    _held = name in EXCLUSIVE
    if _held:
        _EXCLUSIVE_LOCK.acquire()
    try:
        p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=child_env(), timeout=timeout)
        rc, out = p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired as exc:
        rc = None
        part = exc.stdout or ""
        if isinstance(part, bytes):
            part = part.decode("utf-8", "replace")
        out = part + f"\n[超时 {timeout}s，已中止]"
        row["last"] = f"超时（>{timeout}s）"
    except Exception as exc:                            # noqa: BLE001
        rc, out = None, f"{type(exc).__name__}: {exc}"
    finally:
        if _held:
            _EXCLUSIVE_LOCK.release()
    row["secs"] = round(time.perf_counter() - t0, 2)
    row["rc"] = rc
    row["ok"] = (rc == 0)
    if not row["last"]:
        row["last"] = _last_line(out)
    row["output_tail"] = "" if row["ok"] else _tail(out)
    return row


# ---------------------------------------------------------------- 执行一批

def execute(gates, jobs, timeout):
    """跑一批门。`jobs == 1` 即串行；否则用线程池同时等子进程。"""
    t0 = time.perf_counter()
    if jobs <= 1:
        rows = [run_gate(n, s, e, timeout) for (n, s, e) in gates]
    else:
        # **线程池而不是进程池**：每个门本身是独立子进程（subprocess 释放 GIL），
        # 线程只负责「同时等」，没有 pickle / spawn 的问题。
        rows = []
        with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(run_gate, n, s, e, timeout): (n, s, e)
                    for (n, s, e) in gates}
            for fut in cf.as_completed(futs):
                rows.append(fut.result())
    elapsed = time.perf_counter() - t0
    order = {name: i for i, (name, _s, _e) in enumerate(gates)}
    rows.sort(key=lambda r: order.get(r["name"], 99))
    return rows, elapsed


def skip_row(name, script, extra, miss):
    """被跳过的门：字段与 run_gate 一致，`skipped=True`，不耗时不产出。"""
    return {"name": name, "script": script, "argv": extra, "rc": None,
            "ok": True, "skipped": True, "missing": list(miss), "secs": 0.0,
            "last": "缺本地 fixtures：" + "、".join(miss), "output_tail": ""}


def run_batch(gates, skipped_rows, jobs, timeout):
    """只跑没被跳过的门，再把跳过行按 `gates` 的原顺序合并回去。

    `skipped_rows` 是**事先**由调用方按 `GATE_NEEDS` 的真实缺失算出来的，
    这里不做任何二次判断——所以依赖齐全时它为空，行为与没有跳过机制时完全一样。
    """
    skip_names = {r["name"] for r in skipped_rows}
    todo = [g for g in gates if g[0] not in skip_names]
    rows, elapsed = execute(todo, jobs, timeout) if todo else ([], 0.0)
    order = {name: i for i, (name, _s, _e) in enumerate(gates)}
    merged = rows + list(skipped_rows)
    merged.sort(key=lambda r: order.get(r["name"], 99))
    return merged, elapsed


def print_rows(rows):
    for r in rows:
        if r.get("skipped"):
            # `−`（U+2212）：不是通过、也不是失败——这道门本机根本跑不了
            print(f"  − {r['name']:<14} 跳过（缺本地 fixtures："
                  + "、".join(r["missing"]) + "）")
            continue
        icon = "✓" if r["ok"] else "✗"
        rc = "" if r["ok"] else f" [rc={r['rc']}]"
        print(f"  {icon} {r['name']:<14} {r['secs']:7.2f}s  {r['last']}{rc}")


def print_summary(rows, elapsed, jobs, mode, strict=False):
    total = sum(r["secs"] for r in rows)
    skipped = [r for r in rows if r.get("skipped")]
    bad = [r for r in rows if not r["ok"] and not r.get("skipped")]
    passed = len(rows) - len(skipped) - len(bad)
    skip_txt = f"跳过 {len(skipped)}" + ("（--strict 计为失败）"
                                         if skipped and strict else "")
    print()
    print(f"  {len(rows)} 道门 / {mode}"
          f"{'（--jobs=' + str(jobs) + '）' if jobs > 1 else ''}："
          f"通过 {passed}，{skip_txt}，失败 {len(bad)}")
    print(f"  总耗时（墙钟）：{elapsed:.1f}s")
    if jobs > 1:
        speed = (total / elapsed) if elapsed > 0 else 0.0
        print(f"  串行估算（Σ 各门耗时）：{total:.1f}s  → 加速 ≈ {speed:.1f}×")
        print("    （并发下各门互相抢 CPU，Σ 偏高，所以这个加速比是**保守**的；"
              "要精确对比用 --both）")
    if skipped:
        print()
        print("  跳过原因：这些门需要本地 fixtures"
              "（.gitignore 排除的内部文档/分档表），干净 checkout 上不可跑。")
        if strict:
            print("  --strict：上面这些跳过的门**计为失败**（CI 用；本机齐全时无影响）。")
    if bad:
        print()
        print("  失败列表：")
        for r in bad:
            why = r["last"] or "(无输出)"
            print(f"    ✗ {r['name']:<14} {r['secs']:7.2f}s  {why}")
    return bad, skipped


# ---------------------------------------------------------------- 本地依赖自检

def _missing(needs):
    """返回 needs 里本机不存在的路径（保持声明顺序）。"""
    return [p for p in needs
            if not os.path.exists(os.path.join(ROOT, p.replace("/", os.sep)))]


def check_env(gates, strict=False):
    """打印「本机哪些门能跑、缺什么」。**这是自检，不是断言**：有缺失也返回 0。

    判据只看 `GATE_NEEDS` 里声明的 gitignored 路径**存不存在**——不跑门、
    不改任何门的退出码语义。`--strict` 才把「有缺失」变成退出码 1。
    """
    print("本机门禁环境")
    unknown = sorted(set(GATE_NEEDS) - {n for (n, _s, _e) in GATES})
    if unknown:
        print(f"  ! GATE_NEEDS 里登记了不存在的门：{'、'.join(unknown)}",
              file=sys.stderr)
    runnable = 0
    for name, _script, _extra in gates:
        needs = GATE_NEEDS.get(name, [])
        miss = _missing(needs)
        if miss:
            print(f"  ✗ {name:<14} 缺 " + "、".join(miss) + "（gitignored）")
        else:
            runnable += 1
            print(f"  ✓ {name:<14} 依赖 " + ("、".join(needs) if needs else "—"))
    total = len(gates)
    bad = total - runnable
    print()
    if bad:
        print(f"可跑 {runnable} / {total}，缺 {bad} 道"
              "（这些门只能在本机跑，干净 checkout 会红）")
    else:
        print(f"可跑 {runnable} / {total}，缺 0 道（本机依赖齐全）")
    return 1 if (strict and bad) else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        opts = parse_args(argv)
    except UsageError as exc:
        print(f"参数错误：{exc}\n", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2
    if opts["help"]:
        print(USAGE)
        return 0

    gates = list(GATES)
    if opts["only"]:
        want = set(opts["only"])
        unknown = want - {n for (n, _s, _e) in gates}
        if unknown:
            print(f"参数错误：不认识的门的名字 {sorted(unknown)}（见 --list）",
                  file=sys.stderr)
            return 2
        gates = [g for g in gates if g[0] in want]

    if opts["check_env"]:
        return check_env(gates, opts["strict"])

    if opts["list"]:
        for name, script, extra in gates:
            exists = "✓" if os.path.exists(os.path.join(ROOT, script)) else "✗ 缺失"
            print(f"  {exists} {name:<14} {script} {' '.join(extra)}")
        print(f"\n共 {len(gates)} 道门")
        return 0

    # 跳过只发生在 `GATE_NEEDS` 声明的依赖**确实缺失**时；`--only` 点名的门绝不跳过。
    missing_by_gate = {}
    for name, _script, _extra in gates:
        miss = _missing(GATE_NEEDS.get(name, []))
        if miss:
            missing_by_gate[name] = miss

    if opts["only"] and missing_by_gate:
        for name, _script, _extra in gates:
            if name in missing_by_gate:
                print(f"✗ 门 {name} 需要 {'、'.join(missing_by_gate[name])}，"
                      f"本机没有（--only 点名的门不跳过）", file=sys.stderr)
        return 1

    skipped_rows = [skip_row(name, script, extra, missing_by_gate[name])
                    for (name, script, extra) in gates if name in missing_by_gate]

    print(f"# 并行门禁：{len(gates)} 道门，最多 {opts['jobs']} 个并发"
          f"（runner 自己还有 -j 4），单道超时 {opts['timeout']}s"
          + (f"；跳过 {len(skipped_rows)} 道（缺本地 fixtures）" if skipped_rows else ""))
    print()

    serial_elapsed = None
    if opts["both"]:
        print("## 第 1 轮：串行（基准）")
        rows_s, serial_elapsed = run_batch(gates, skipped_rows, 1, opts["timeout"])
        print_rows(rows_s)
        print_summary(rows_s, serial_elapsed, 1, "串行", opts["strict"])
        print()

    print(f"## {'第 2 轮：' if opts['both'] else ''}并发"
          f"（--jobs={opts['jobs']}）")
    rows, elapsed = run_batch(gates, skipped_rows, opts["jobs"], opts["timeout"])
    print_rows(rows)
    bad, skipped = print_summary(rows, elapsed, opts["jobs"], "并发", opts["strict"])

    if serial_elapsed:
        speed = serial_elapsed / elapsed if elapsed > 0 else 0
        print()
        print(f"## 串行 vs 并发（同一次调用，同样 {len(gates)} 道门）")
        print(f"  · 串行：{serial_elapsed:.1f}s")
        print(f"  · 并发：{elapsed:.1f}s（--jobs={opts['jobs']}）")
        print(f"  · **加速 {speed:.2f}×，省 {serial_elapsed - elapsed:.1f}s**")

    if opts["json"]:
        payload = {"gates": rows, "elapsed": round(elapsed, 2),
                   "jobs": opts["jobs"], "serial_elapsed":
                   None if serial_elapsed is None else round(serial_elapsed, 2),
                   "failed": [r["name"] for r in bad],
                   "skipped": [r["name"] for r in skipped],
                   "strict": opts["strict"]}
        d = os.path.dirname(os.path.abspath(opts["json"]))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with io.open(opts["json"], "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        print(f"\n  → {opts['json']}")

    print()
    if bad:
        print(f"✗ 退出码 1：{len(bad)} 道门失败 —— "
              + "、".join(r["name"] for r in bad))
        return 1
    if skipped and opts["strict"]:
        print(f"✗ 退出码 1（--strict）：{len(skipped)} 道门因缺本地 fixtures 被跳过 —— "
              + "、".join(r["name"] for r in skipped))
        return 1
    if skipped:
        print(f"✓ 退出码 0：{len(rows) - len(skipped)} 道门全绿，"
              f"{len(skipped)} 道跳过（缺本地 fixtures，不计失败）。")
        return 0
    print(f"✓ 退出码 0：{len(gates)} 道门全绿。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
