#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""tightness_compare.py —— 「κ ÷ 实测 ops」的**前后对照尺子**

## 为什么需要它

解释器正在改成**值语义拷贝**（`T a = b;` / 按值形参 / 入容器三条路径会真的拷贝），
而 `src/interp.py` 的 `ops` 计数器把拷贝也记进操作数。于是**同一份程序、
同一个 κ、同一个 n**，改前改后的 `κ ÷ 实测` 会变——这是预期的，但必须能**指出来变了多少**，
而不是靠眼睛看两张表。

这个工具就是那把尺子：**同一份代码、同一条命令，改前跑一次存 baseline，
改后再跑一次，自动列出「哪个函数的比值变了、变了多少」**。

## 三个量，别读错

| 量 | 从哪来 | 单位 |
|---|---|---|
| `κ` | `src/kappa_vs_measured.py` 的 `measure_one`（`CostInterp.cost_of` + `cost_check.evaluate`，与 `tools/kappa_vs_ops.py` 同一个成本域） | 操作 |
| `实测ops` | 同一个 `measure_one` 调用里，被 `interp.ops_*` 记下的**整棵调用树**操作数 | 操作 |
| `κ÷ops` | 两者相除；**越接近 1 越紧**，`< 1` 就是**破界** | 无量纲 |

**为什么不自己再写一个测量器**：那会立刻出现第二套口径（这个项目一直在防的事）。
所以实测值**只**从 `measure_one` 里拿——但 `measure_one` 只返回 `evaluation_steps`，
**不返回 ops**，于是这里用一种不改 `src/` 的办法拿到它：在调用前把 `interp.Interp`
换成一个只多记一笔的子类，`call()` 返回时读 `counters()["operations"]`。
**实参构造、benchmodel、`requires` 修正、分档表全部还是 `measure_one` 那一套**，
没有一个字节的测量逻辑被重写。

## 用法

    python -B tools/tightness_compare.py                          # 默认 n = 16,64,256
    python -B tools/tightness_compare.py --sizes 16,64
    python -B tools/tightness_compare.py --save-baseline=base.json   # 改前存一把尺子
    python -B tools/tightness_compare.py --baseline=base.json        # 改后对比，出差分表
    python -B tools/tightness_compare.py --module sum,topk --runs 3
    python -B tools/tightness_compare.py --function average,bsearch  # 只看这些函数（表会变短）

## 判据（只有一条是硬判据）

    κ ≥ ops   —— 否则**破界**（🔴 单独列在表尾的一节里，退出码 1）

`κ÷ops` 的档位只是**描述**，不是判据：`≤1.05` 贴紧、`≤2` 成立、`≤10` 偏松、`>10` 过松。
破界行**永远**单独列一遍并在退出码里体现——不许混在 12 倍的行里被忽略。

## 多出来的一节：`(b) 阶错了` vs `(a) 常数项松`（2026-11-10）

`κ÷ops` 大**有两种原因**，读者的下一步动作完全不同：

- **(a) 常数项松**：比值**收敛**（只是数值大）。阶对得上 ⇒ 写真实阶的 target 仍能过
  （big-O 吸收常数），**用户没有被逼**。改 κ 的常数项即可。
- **(b) 阶错了**：κ 的阶**真的高于**真实阶，比值**随 n 发散**。这时任何能过的 target
  都必须 ≥ κ 的阶 ⇒ **真实的紧界反被判债务**，这就是"用户被迫写松的 target"。

`calculator` 的 39 万倍是 (a)（2752 万常数项），`bsearch` 的 33 倍才是 (b)。
**不看"比值随 n 怎么走"就会把这两件事说反**，所以主表之后单列一节
（`render_loose`）。这一节**不参与判定**：主表的「判定」列与退出码与它无关。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 没有破界（`κ < ops`） |
| 1 | **有破界**：表尾「破界」一节列出，并说明是哪几条 |
| 2 | 用法错误（参数不认、`--sizes` 解析不出） |
| 3 | 环境不可用：`src/` 导入失败（例如 `interp.py` 正被改到一半） |

## 已知限制（读数字前先看这一节）

- **`ops` 计数默认是关的**（`interp._COUNT`）。本工具在 import 前设
  `ELENCH_COUNT_OPS=1` 并调 `ops_enable()`；若这一版 `interp.py` 里没有 ops 计数，
  会明确报「ops 计数不可用」，而不是拿 0 去算比值。
- **步数预算是真的会挡**：`ELENCH_STEP_BUDGET`（默认 200 万，`--max-steps=` 可调）
  超了就记「未测(超预算)」——**未测 ≠ 成立**，也不算破界。
- **需要专门夹具的程序**（`calculator` / `paren` / `matmul` / `topk`）在本工具里走
  `measure_one` 自己的实参构造（benchmodel 优先），**不是** `kappa_vs_ops.FIXTURES`。
  于是 `calculator` 这类「随机字节早退」的函数比值会**大得离谱**（κ 是按 token 预算的上界），
  那是**上界松**，不是测量错。要看夹具口径请用 `tools/kappa_vs_ops.py`。
- `topk` 在 n=16 且样例里写死 `k = 32` 时是**输入无效**（早退），会如实标「未测(输入无效)」。
- κ 含契约项（`k(f)` / `ext(...)`）时数值上是 `inf`，记「未判定(κ=inf)」：**既不算成立也不算破界**。
- `--runs` 默认 5（每组输入取 max）。**κ 是上界，只该跟最坏实测比**；
  runs 太小会低估 ops，比值看起来更「紧」——这是假紧，改这个数要写进报告。
"""

import contextlib
import io
import json
import math
import os
import sys
import time

#: 中文 Windows 控制台默认 GBK：`✓` / `✗` / `κ` 会在**打印结果时**崩掉。
#: tests/runner.py 里踩过同一个坑（它死在汇总之前，一行结果都看不到）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")

#: 默认两个语料目录。**标签用目录名**（`文档/样例` → `样例`，`_diff` → `_diff`），
#: 因为两边都有 `matmul` / `merge`，光写模块名会撞。
CORPORA = [
    os.path.join(ROOT, "文档", "样例"),
    os.path.join(ROOT, "_diff"),
]

DEFAULT_SIZES = "16,64,256"
DEFAULT_RUNS = 5
#: 单次调用步预算。`measure_one` 里的 `Interp` 读这个环境变量，
#: 超了抛 `InterpBudgetExceeded` → 该行记「未测(超预算)」。
DEFAULT_MAX_STEPS = 2_000_000

USAGE = """用法: tightness_compare.py [选项]

  --sizes=16,64,256     规模档（默认 16,64,256）
  --runs=5              每组规模跑几组输入取 max（默认 5）
  --dir=路径            只跑这个目录（可重复；默认 文档/样例 与 _diff）
  --module=a,b          只跑这些模块（.tu 文件名，不含扩展名）
  --function=a,b        只打印这些函数的行（注意：表会变短，baseline 也只覆盖这部分）
  --max-steps=2000000   单次调用步预算（超了记「未测(超预算)」）
  --save-baseline=PATH  把本次结果存成 baseline JSON（含 κ / ops / 比值）
  --baseline=PATH       与已有 baseline 对比，输出**差异表**
  --diff-pct=1.0        差异表只列比值变化超过这个百分比的点（默认 1.0%）
  -h, --help            显示本帮助

退出码：0 = 无破界；1 = 有破界（κ < ops）；2 = 用法错误；3 = 环境不可用（src/ 导入失败）
"""


# ---------------------------------------------------------------- 参数解析

class UsageError(Exception):
    pass


#: 需要取值的选项；其余（`--help`）是开关。
_VALUE_OPTS = {"sizes", "runs", "dir", "module", "function", "max-steps",
               "save-baseline", "baseline", "diff-pct"}
_FLAG_OPTS = {"help"}


def parse_args(argv):
    """手写解析：仓库里多数工具这么写，不引第三方依赖、不搞 argparse 的隐式前缀匹配。"""
    opts = {
        "sizes": DEFAULT_SIZES, "runs": DEFAULT_RUNS, "dirs": [],
        "modules": None, "functions": None,
        "max_steps": DEFAULT_MAX_STEPS,
        "save_baseline": None, "baseline": None, "diff_pct": 1.0,
        "help": False,
    }
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
        if key not in _VALUE_OPTS and key not in _FLAG_OPTS:
            raise UsageError(f"不认识的选项：--{key}")
        if key in _FLAG_OPTS:
            if eq:
                raise UsageError(f"--{key} 是开关，不取值")
            opts["help"] = True
            i += 1
            continue
        if not eq:
            if i + 1 >= len(argv):
                raise UsageError(f"--{key} 需要取值（写成 --{key}=值 更稳）")
            val, i = argv[i + 1], i + 2
        else:
            i += 1
        if key == "sizes":
            opts["sizes"] = val
        elif key == "runs":
            opts["runs"] = _int_opt(key, val)
        elif key == "dir":
            opts["dirs"].append(val)
        elif key == "module":
            opts["modules"] = [s for s in val.split(",") if s.strip()]
        elif key == "function":
            opts["functions"] = [s for s in val.split(",") if s.strip()]
        elif key == "max-steps":
            opts["max_steps"] = _int_opt(key, val)
        elif key == "save-baseline":
            opts["save_baseline"] = val
        elif key == "baseline":
            opts["baseline"] = val
        elif key == "diff-pct":
            try:
                opts["diff_pct"] = float(val)
            except ValueError:
                raise UsageError(f"--diff-pct 要一个数：{val!r}")
    return opts


def _int_opt(key, val):
    try:
        v = int(val)
    except ValueError:
        raise UsageError(f"--{key} 要一个整数：{val!r}")
    if v <= 0:
        raise UsageError(f"--{key} 必须为正：{v}")
    return v


def parse_sizes(s):
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = int(part)
        except ValueError:
            raise UsageError(f"--sizes 里有一档不是整数：{part!r}")
        if n <= 0:
            raise UsageError(f"--sizes 里有一档 ≤ 0：{n}")
        if n not in out:
            out.append(n)
    if not out:
        raise UsageError("--sizes 一档都没有")
    return out


# ---------------------------------------------------------------- ops 捕获

class OpsRecorder(object):
    """把 `interp.Interp` 换成一个**只多记一笔**的子类。

    为什么要这样，而不是重新实现一遍测量：

    - `measure_one` 内部 `from interp import Interp` 是**调用时**才解析的，
      所以替换模块属性即可生效，`src/` 一个字节都不改；
    - 实参构造 / benchmodel / `requires` 修正 / 分档表仍然全在 `measure_one` 里，
      **不存在第二套测量口径**；
    - `call()` 返回后 `counters()["operations"]` 就是这一次调用树的 ops
      （`Interp.call` 在 `finally` 里结算 `self.ops`，异常路径也不会漏）。

    万一这一版 `interp.py` 正被改成没有 ops 计数：`counters()` 里取不到
    `operations`，这里会记 0，主流程再对照 `evaluation_steps` 判「ops 计数不可用」——
    **不会把 0 当成真的 0**。
    """

    def __init__(self):
        self.records = []
        self._real = None

    def __enter__(self):
        import interp

        self._real = interp.Interp
        rec = self.records
        real = self._real

        class _RecordingInterp(real):
            def call(self, *a, **k):
                value = super().call(*a, **k)
                fn = a[0] if a else k.get("fn_name")
                try:
                    c = self.counters()
                except Exception:                       # noqa: BLE001
                    c = {}
                try:
                    ops = int(c.get("operations") or 0)
                except Exception:                       # noqa: BLE001
                    ops = 0
                try:
                    steps = int(c.get("evaluation_steps") or 0)
                except Exception:                       # noqa: BLE001
                    steps = 0
                rec.append({"fn": fn, "ops": ops, "steps": steps})
                return value

        interp.Interp = _RecordingInterp
        # **计数默认关**：不改这一下，ops 会全是 0。
        if hasattr(interp, "ops_enable"):
            interp.ops_enable(True)
        return self

    def __exit__(self, *exc):
        import interp

        if self._real is not None:
            interp.Interp = self._real
        return False

    def take(self):
        """取走本轮的记录并清空。

        **必须原地 `del`，不能 `self.records = []`**：`_RecordingInterp.call`
        那个闭包在 `__enter__` 时就抓住了这个列表对象；一旦换成新列表，
        记录还写进旧列表，而 `take()` 拿到的永远是空的新列表——
        症状是「每个函数都报没抓到 ops」。
        """
        got = list(self.records)
        del self.records[:]
        return got


# ---------------------------------------------------------------- 测量一行

def _row(corpus, module, fn, n, status, kappa=None, kappa_repr="—",
         ops=None, steps=None, detail=""):
    ratio = None
    judge = "未测"
    if status == "ok" and kappa is not None and ops:
        ratio = kappa / ops
        if kappa < ops:
            judge = "破界"
        elif ratio <= 1.05:
            judge = "贴紧"
        elif ratio <= 2.0:
            judge = "成立"
        elif ratio <= 10.0:
            judge = "偏松"
        else:
            judge = "过松"
    elif status == "kappa_inf":
        judge = "未判定"
    return {
        "label": f"{corpus}.{module}.{fn}",
        "corpus": corpus, "module": module, "function": fn, "n": n,
        "status": status, "kappa": kappa, "kappa_repr": kappa_repr,
        "ops": ops, "steps": steps, "ratio": ratio, "judge": judge,
        "detail": detail,
    }


#: `src/kappa_vs_measured.py` 的模块对象（见 `_load_kappa_vs_measured`）。
_KVM = None


def _load_kappa_vs_measured():
    """从 `src/` **按文件路径**加载，不靠 `sys.path` 顺序。

    为什么不能图省事 `import kappa_vs_measured`：`tools/kappa_vs_measured.py` 是个
    **转发垫片**（只有 `main()`，没有 `measure_one`）。按脚本运行时 `sys.path[0]`
    就是 `tools/`；本机又是 editable 安装（`src/` 挂在 `sys.path` 的**后面**），
    于是条件式 `if SRC not in sys.path: insert` 不会触发，`import` 先命中垫片，
    报 `cannot import name 'measure_one'`。按路径直取，和 sys.path 顺序彻底解耦。
    """
    import importlib.util

    path = os.path.join(SRC, "kappa_vs_measured.py")
    spec = importlib.util.spec_from_file_location("_elench_kappa_vs_measured", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"加载不了 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    if not hasattr(mod, "measure_one"):
        raise ImportError(f"{path} 里没有 measure_one")
    return mod


def measure_module(corpus, tu_path, n, runs, recorder):
    """对**一个模块的一档 n** 调一次 `measure_one`，把它拆成逐函数的行。"""
    measure_one = _KVM.measure_one

    module = os.path.basename(tu_path)
    if module.endswith(".tu"):
        module = module[:-3]

    recorder.take()
    buf = io.StringIO()
    t0 = time.perf_counter()
    try:
        # 程序自己会 `print`（样例里有），不挡住就会插进 Markdown 表里
        with contextlib.redirect_stdout(buf):
            rows = measure_one(tu_path, n, runs)
    except Exception as exc:                            # noqa: BLE001
        spent = time.perf_counter() - t0
        detail = f"{type(exc).__name__}: {exc}"
        low = detail.lower()
        status = "budget" if ("执行超过" in detail or "budget" in low) else "error"
        return [_row(corpus, module, "?", n, status, detail=detail,
                     steps=None)], spent, detail
    spent = time.perf_counter() - t0
    recs = recorder.take()

    out = []
    for r in rows:
        fn = r.get("function", "?")
        if "invalid" in r:
            out.append(_row(corpus, module, fn, n, "invalid",
                            kappa_repr=str(r.get("kappa", "—")),
                            detail=str(r["invalid"])))
            continue
        if "error" in r:
            det = str(r.get("error") or "")
            status = "budget" if ("执行超过" in det or "budget" in det.lower()) else "error"
            out.append(_row(corpus, module, fn, n, status,
                            kappa_repr=str(r.get("kappa", "—")), detail=det))
            continue

        kappa = r.get("kappa_at_n")
        kappa_repr = "inf" if (kappa is None or (isinstance(kappa, float)
                                                 and (math.isinf(kappa) or math.isnan(kappa)))) \
            else f"{int(kappa):,}"
        if kappa is None or (isinstance(kappa, float)
                             and (math.isinf(kappa) or math.isnan(kappa))):
            out.append(_row(corpus, module, fn, n, "kappa_inf",
                            steps=r.get("max_actual"), kappa_repr="inf",
                            detail="κ 含符号/外部项（inf），不可判定"))
            continue

        kappa = int(kappa)
        mine = [x for x in recs if x.get("fn") == fn]
        ops = max((x["ops"] for x in mine), default=None)
        steps = r.get("max_actual")
        if ops is None:
            out.append(_row(corpus, module, fn, n, "error", kappa=kappa,
                            kappa_repr=f"{kappa:,}", steps=steps,
                            detail="没抓到 ops 记录（Interp.call 未返回？）"))
            continue
        if ops == 0 and (steps or 0) > 0:
            # **不许把 0 当真的 0**：计数器没开 / 这一版 interp 还没接 ops
            out.append(_row(corpus, module, fn, n, "no_ops", kappa=kappa,
                            kappa_repr=f"{kappa:,}", steps=steps,
                            detail="ops 计数不可用（interp 未开计数或未接 ops）"))
            continue
        out.append(_row(corpus, module, fn, n, "ok", kappa=kappa,
                        kappa_repr=f"{kappa:,}", ops=ops, steps=steps))
    return out, spent, ""


# ---------------------------------------------------------------- 主流程

def discover(dirs, modules=None):
    """[(语料标签, .tu 绝对路径)]：只收**同时有 .tu 与 .eln** 的模块。"""
    out = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        label = os.path.basename(os.path.normpath(d))
        for name in sorted(os.listdir(d)):
            if not name.endswith(".tu"):
                continue
            mod = name[:-3]
            if modules and mod not in modules:
                continue
            tu = os.path.join(d, name)
            if not os.path.exists(os.path.join(d, mod + ".eln")):
                continue
            out.append((label, tu))
    return out


def _disp_dir(d):
    """显示用：仓库内给相对路径，仓库外（比如从 `git archive` 解出来的旧树）给绝对路径。"""
    rel = os.path.relpath(d, ROOT)
    return rel if not rel.startswith("..") else d


def render_table(rows):
    head = ("| 模块.函数 | n | κ | 实测ops | κ÷ops | 判定 |\n"
            "|---|---|---|---|---|---|")
    lines = [head]
    for r in rows:
        if r["status"] == "ok":
            kappa = f"{r['kappa']:,}"
            ops = f"{r['ops']:,}"
            ratio = f"{r['ratio']:.2f}×"
            judge = {"破界": "🔴 **破界**", "贴紧": "✅ 贴紧",
                     "成立": "✓ 成立", "偏松": "~ 偏松",
                     "过松": "≫ 过松"}.get(r["judge"], r["judge"])
        else:
            # **没测成的行不印 κ 表达式**：κ 是分段渲染出来的，能有几百字符，
            # 一印就把整张表撑破（`topk` 在 n=16 上就是）。数值也不存在——
            # 这些行的 κ 在 n 处**没有求值**，印 `—` 才是实话。
            kappa = "inf" if r["status"] == "kappa_inf" else "—"
            ops = "—"
            ratio = "—"
            judge = {"invalid": "未测(输入无效)", "budget": "未测(超预算)",
                     "error": "未测(跑不了)", "kappa_inf": "未判定(κ=inf)",
                     "no_ops": "未测(ops 不可用)"}.get(r["status"], r["status"])
        lines.append(f"| `{r['label']}` | {r['n']} | {kappa} | {ops} | {ratio} | {judge} |")
    return "\n".join(lines)


def render_breaches(rows):
    bad = [r for r in rows if r["judge"] == "破界"]
    lines = ["## 破界（κ < ops）—— **这是唯一的硬判据**", ""]
    if not bad:
        lines.append("没有任何一行 `κ < ops`。")
        return "\n".join(lines), bad
    lines.append(f"**{len(bad)} 行破界**（下表与主表无关，单独列出，避免被“过松”的大数字淹没）：")
    lines.append("")
    lines.append("| 模块.函数 | n | κ | 实测ops | κ÷ops | 差（ops−κ） |")
    lines.append("|---|---|---|---|---|---|")
    for r in bad:
        lines.append(f"| `{r['label']}` | {r['n']} | {r['kappa']:,} | {r['ops']:,} | "
                     f"{r['ratio']:.3f}× | {r['ops'] - r['kappa']:,} |")
    return "\n".join(lines), bad


def render_unmeasured(rows):
    bad = [r for r in rows if r["status"] in ("error", "budget", "no_ops", "invalid")]
    if not bad:
        return ""
    lines = ["## 未测 / 跑不了（**未测 ≠ 成立**，也不算破界）", ""]
    lines.append("| 模块.函数 | n | 原因 |")
    lines.append("|---|---|---|")
    for r in bad:
        why = r["detail"] or r["status"]
        why = " ".join(str(why).split())[:150]
        lines.append(f"| `{r['label']}` | {r['n']} | {why} |")
    return "\n".join(lines)


def render_flat(rows, sizes):
    """**ops 不随 n 增长 = 根本没跑到正题**（输入被拒 / 容量挡住 / 提前返回）。

    这种点的「比值」不是紧度：`lcs` 在 n=64 上 ops=25（输入随机、第一个不匹配就返回），
    κ÷ops 会显示 10685×——把它当「κ 太松」是**读错了**。
    `tools/kappa_vs_ops.py:summarize` 里有同一条检查，这里保持同一口径。
    """
    if len(sizes) < 2:
        return ""
    by_fn = {}
    for r in rows:
        if r["status"] == "ok":
            by_fn.setdefault(r["label"], {})[r["n"]] = r
    flagged = []
    for label, per in sorted(by_fn.items()):
        ns = sorted(per)
        if len(ns) < 2:
            continue
        seq = [per[n]["ops"] for n in ns]
        if seq[-1] <= seq[0]:
            flagged.append((label, ns, seq, per[ns[-1]].get("steps")))
    if not flagged:
        return ""
    lines = ["## 疑似早退（ops 不随 n 增长）—— **这些点的比值不能当紧度读**", "",
             "| 模块.函数 | 各档 ops | 说明 |", "|---|---|---|"]
    for label, ns, seq, _steps in flagged:
        lines.append(f"| `{label}` | " + " / ".join(f"{n}:{o:,}" for n, o in zip(ns, seq))
                     + " | 最大档 ops 没有超过最小档：测到的是**早退路径**，不是这个函数的成本 |")
    lines.append("")
    lines.append("（判据与 `tools/kappa_vs_ops.py` 的 `flat` 一节相同；"
                 "要测到真成本得换合法输入或专门的夹具。）")
    return "\n".join(lines)


# ---------------------------------------------------------------- 告警：界松
#
# 这一节只回答一个问题：**大比值里，哪些是「κ 的阶真的高于真实阶」（用户被迫写松），
# 哪些只是「常数项松」？** 两者在 `cost` 侧长得一样（都是"达成"），必须分开：
#
# | 因 | 实测长相 | 用户被迫写松？ | 该怎么改 |
# |---|---|---|---|
# | **(b) 阶错了** | `κ÷ops` **随 n 单调发散**（末档/首档 ≥ 2 倍） | **是**：任何能过的 target 都得 ≥ κ 的阶 | 改模型/κ，让阶对上 |
# | **(a) 常数项松** | `κ÷ops` **收敛**（末档/首档 < 1.5 倍），只是数值大 | 否：写真实阶的 target 就能过（big-O 吸收常数） | 改常数项 |
#
# **这一节不改任何判定**：它只读 `_row` 已经算好的 `ratio` / `ops`，
# 主表的「判定」列与退出码与有没有这一节**完全无关**。
#
# 单独拿出来的原因：`calculator` 的 39 万倍**不是 (b)**（它的阶没错，是 2752 万常数项撑的）；
# 而 `bsearch` 只有 33 倍却是**真的 (b)**（κ 写 O(n)、真实是 O(log n)）。
# 不看"比值随 n 怎么走"，就会把这两件事说反。

#: 大比值门槛：与主表「过松」的档位同一个数（>10 倍），不另立一套。
LOOSE_RATIO = 10.0
#: (b) 的判据：末档比首档涨了这么多倍 ⇒ 比值在发散 ⇒ κ 的阶高于真实阶。
DIVERGE_GROW = 2.0
#: (a) 的判据：末档比首档涨不到这么多倍 ⇒ 比值收敛 ⇒ 差的是常数，不是阶。
CONVERGE_GROW = 1.5


def render_loose(rows, sizes):
    """把「大比值」按 (a) 常数项松 / (b) 阶错了 分开列；没有就返回 `""`。

    **早退的点不参与分类**（`ops` 不随 n 增长时，比值是噪声，不是紧度）——
    它们单独列成「判不了」，与 `render_flat` 同一口径。
    """
    by_fn = {}
    for r in rows:
        if r["status"] == "ok":
            by_fn.setdefault(r["label"], []).append(r)

    b_rows, a_rows, unknown = [], [], []
    for label, per in sorted(by_fn.items()):
        per = sorted(per, key=lambda r: r["n"])
        seq = [r["ops"] for r in per]
        ratios = [r["ratio"] for r in per]
        ns = [r["n"] for r in per]
        if len(per) < 2:
            continue
        if seq[-1] <= seq[0]:
            if max(ratios) >= LOOSE_RATIO:
                unknown.append((label, ns, ratios,
                                "ops 不随 n 增长（早退）⇒ 比值不是紧度，(a)/(b) 都判不了"))
            continue
        mx = max(ratios)
        if mx < LOOSE_RATIO:
            continue
        grow = ratios[-1] / ratios[0] if ratios[0] > 0 else float("inf")
        if grow >= DIVERGE_GROW:
            b_rows.append((label, ns, ratios, grow))
        elif grow < CONVERGE_GROW:
            a_rows.append((label, ns, ratios, grow))
        else:
            unknown.append((label, ns, ratios,
                            f"比值涨了 {grow:.2f} 倍，落在 {CONVERGE_GROW}~{DIVERGE_GROW} "
                            f"之间：分不清是常数项还是阶，**不猜**"))

    if not (a_rows or b_rows or unknown):
        return ""

    def _seq(ns, ratios):
        return " / ".join(f"{n}:{r:,.2f}×" for n, r in zip(ns, ratios))

    lines = ["## 界松分类：**(b) 阶错了** 还是 **(a) 常数项松**", "",
             f"判据：大比值 ≥ {LOOSE_RATIO:g}×（与主表「过松」同档），再看比值**随 n 怎么走**："
             f"涨 ≥ {DIVERGE_GROW:g} 倍 ⇒ 发散 ⇒ **阶错了**；"
             f"涨 < {CONVERGE_GROW:g} 倍 ⇒ 收敛 ⇒ **只是常数项**。", "",
             "| 模块.函数 | 各档 κ÷ops | 末/首 | 分类 | 含义 |", "|---|---|---|---|---|"]
    for label, ns, ratios, grow in b_rows:
        lines.append(f"| `{label}` | {_seq(ns, ratios)} | {grow:,.2f}× | "
                     f"**(b) 阶错了** | κ 的阶**真的高于**实测阶 ⇒ "
                     f"**这条 target 是被迫写松的**：想写真实的阶就过不了 |")
    for label, ns, ratios, grow in a_rows:
        lines.append(f"| `{label}` | {_seq(ns, ratios)} | {grow:,.2f}× | "
                     f"(a) 常数项松 | 阶对得上，差的是常数项 ⇒ "
                     f"写真实阶的 target 仍能过（big-O 吸收常数），**不算被迫** |")
    for label, ns, ratios, why in unknown:
        lines.append(f"| `{label}` | {_seq(ns, ratios)} | — | 判不了 | {why} |")
    lines.append("")
    lines.append("**怎么用**：`(b)` 那几行才是「用户被迫写松」——它们的 κ 阶高于真实阶，"
                 "任何能过的 target 都必须 ≥ κ 的阶，于是**真实的紧界反被判债务**。"
                 "`(a)` 只是常数项大：改 κ 的常数即可，**不要**为了迁就它放宽 target。")
    if b_rows or a_rows:
        lines.append("")
        lines.append("（右侧的 `cost` 侧告警 `W-Target-Loose` 只报能静态证的 `(a)` 那半边；"
                     "`(b)` 必须靠这一节的实测比值——静态看不见「真实阶」。）")
    return "\n".join(lines)


# ---------------------------------------------------------------- baseline

def _key(r):
    return (r["label"], r["n"])


def save_baseline(path, rows, sizes, runs, max_steps):
    payload = {
        "tool": "tools/tightness_compare.py",
        "version": 1,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sizes": sizes, "runs": runs, "max_steps": max_steps,
        "rows": rows,
    }
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _fmt_ratio(v):
    return "—" if v is None else f"{v:.2f}×"


def render_diff(cur, base, path, diff_pct):
    """差异表：**比值变了多少** + ops 怎么变 + 破界有没有翻转。"""
    bmap = {_key(r): r for r in base.get("rows", [])}
    cmap = {_key(r): r for r in cur}
    lines = [f"## 与 baseline 对比（`{path}`）", ""]
    gen = base.get("generated") or "?"
    lines.append(f"baseline：{gen}  sizes={base.get('sizes')}  runs={base.get('runs')}  "
                 f"max_steps={base.get('max_steps')}")
    lines.append("")
    lines.append("| 模块.函数 | n | 旧 κ÷ops | 新 κ÷ops | 比值变化 | ops（旧→新） | 说明 |")
    lines.append("|---|---|---|---|---|---|---|")

    changed, added, removed, flips = 0, 0, 0, 0
    for key in sorted(cmap):
        c = cmap[key]
        b = bmap.get(key)
        if b is None:
            added += 1
            lines.append(f"| `{c['label']}` | {c['n']} | — | {_fmt_ratio(c['ratio'])} | "
                         f"新增 | — | 这次才出现的点 |")
            continue
        bo, bn = b.get("ratio"), c.get("ratio")
        # **两边都没测出来**（`—`）不是「变了」：没有可比的东西，直接跳过。
        # 早先这里把 delta=None 当成「变化」，于是 88 行里 19 行全是空对空的噪声。
        if bo is None and bn is None:
            continue
        if bo is None or bn is None:
            delta = None
        else:
            delta = (bn - bo) / bo * 100.0
        ops_txt = (f"{b.get('ops')}→{c.get('ops')}"
                   if b.get("ops") is not None or c.get("ops") is not None else "—")
        notes = []
        if b.get("ops") != c.get("ops"):
            notes.append("ops 变了")
        if b.get("kappa") != c.get("kappa"):
            notes.append("κ 变了")
        if bo is None and bn is not None:
            notes.append("由未测变为可测")
        elif bo is not None and bn is None:
            notes.append("变为未测")
        if b.get("judge") == "破界" and c.get("judge") != "破界":
            notes.append("破界→恢复正常")
        elif b.get("judge") != "破界" and c.get("judge") == "破界":
            notes.append("**新破界**")
            flips += 1
        big = delta is None or abs(delta) >= diff_pct
        if big or notes:
            changed += 1
            d_txt = "—" if delta is None else f"{delta:+.1f}%"
            lines.append(f"| `{c['label']}` | {c['n']} | {_fmt_ratio(bo)} | {_fmt_ratio(bn)} | "
                         f"{d_txt} | {ops_txt} | {'、'.join(notes) or '比值变化'} |")
    for key in sorted(bmap):
        if key in cmap:
            continue
        removed += 1
        b = bmap[key]
        lines.append(f"| `{b['label']}` | {b['n']} | {_fmt_ratio(b.get('ratio'))} | — | "
                     f"消失 | — | 这次没测到这个点 |")

    if changed + added + removed == 0:
        lines.append("| — | — | — | — | — | — | 没有超过阈值的点 |")
    lines += ["", f"**差异汇总**：比值/ops/κ 有变化或破界翻转 {changed} 个点"
                  f"（其中**新破界 {flips}**），新增 {added}，消失 {removed}"
                  f"（阈值 {diff_pct}%）。"]
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        opts = parse_args(argv)
        if opts["help"]:
            print(USAGE)
            return 0
        sizes = parse_sizes(opts["sizes"])
    except UsageError as exc:
        print(f"参数错误：{exc}\n", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    # **必须在 import interp 之前**设：`_COUNT` 只在 import 时读一次环境变量；
    # 步预算在 `Interp.__init__` 里读，也在这里设好。
    os.environ.setdefault("ELENCH_COUNT_OPS", "1")
    os.environ["ELENCH_STEP_BUDGET"] = str(opts["max_steps"])
    # **把 src/ 顶到最前**（不是「没有才插」）：editable 安装已把它挂在 sys.path
    # 后面，而 sys.path[0] 是脚本目录 tools/，同名文件会先命中（见 _load_kappa_vs_measured）。
    while SRC in sys.path:
        sys.path.remove(SRC)
    sys.path.insert(0, SRC)

    global _KVM
    try:
        import interp                                   # noqa: F401
        _KVM = _load_kappa_vs_measured()
    except Exception as exc:                            # noqa: BLE001
        print(f"✗ 环境不可用：`src/` 导入失败（interp 可能正被改到一半）。\n"
              f"  {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    if not hasattr(interp, "ops_enable") and "ELENCH_COUNT_OPS" not in os.environ:
        print("✗ 环境不可用：这一版 `interp.py` 没有 ops 计数器（`ops_enable`）。\n"
              "  本工具比的是「κ ÷ ops」，没有 ops 就**不给数**，也不拿 steps 冒充。",
              file=sys.stderr)
        return 3

    dirs = opts["dirs"] or CORPORA
    dirs = [d if os.path.isabs(d) else os.path.join(ROOT, d) for d in dirs]
    targets = discover(dirs, opts["modules"])
    if not targets:
        print("✗ 没有可测的模块（需要同名 `.tu` + `.eln` 成对存在）", file=sys.stderr)
        return 2

    runs = opts["runs"]
    t0 = time.perf_counter()
    rows, failures = [], []
    with OpsRecorder() as rec:
        for corpus, tu in targets:
            for n in sizes:
                got, spent, detail = measure_module(corpus, tu, n, runs, rec)
                if detail:
                    failures.append(f"{corpus}.{os.path.basename(tu)[:-3]} n={n}：{detail}")
                rows.extend(got)

    if opts["functions"]:
        want = set(opts["functions"])
        rows = [r for r in rows if r["function"] in want]
    # **稳定排序**：并发/未来并行测量也不会让表顺序漂，diff 才读得下去。
    # 语料按 `--dir` 给的顺序排（`样例` 在前），同一语料内按 模块 → 函数 → n。
    corder = {os.path.basename(os.path.normpath(d)): i for i, d in enumerate(dirs)}
    rows.sort(key=lambda r: (corder.get(r["corpus"], 99), r["module"],
                             r["function"], r["n"]))
    total = time.perf_counter() - t0

    print(f"# 紧度对照：κ ÷ 实测 ops")
    print()
    print(f"- 目录：{'、'.join(_disp_dir(d) for d in dirs)}")
    print(f"- 规模：{sizes}；每档 {runs} 组输入取最坏；步预算 {opts['max_steps']:,}")
    print(f"- 实测来源：`src/kappa_vs_measured.py:measure_one`（ops 由 `interp` 计数器捕获）")
    print(f"- 共 {len(targets)} 个模块 × {len(sizes)} 档 = {len(rows)} 行，用时 {total:.1f}s")
    print()
    print(render_table(rows))
    print()

    ok = [r for r in rows if r["status"] == "ok"]
    tight = [r for r in ok if r["judge"] in ("贴紧", "成立")]
    loose = [r for r in ok if r["judge"] in ("偏松", "过松")]
    print("## 汇总")
    print(f"  · 能判定的点：{len(ok)} 个 —— 紧/成立 {len(tight)}，偏松/过松 {len(loose)}")
    counts = {}
    for r in rows:
        if r["status"] != "ok":
            counts[r["status"]] = counts.get(r["status"], 0) + 1
    for k, label in (("invalid", "输入无效（早退）"), ("budget", "超预算未测"),
                     ("error", "跑不了"), ("kappa_inf", "κ=inf 不可判定"),
                     ("no_ops", "ops 计数不可用")):
        if counts.get(k):
            print(f"  · {label}：{counts[k]}")
    print()

    flat = render_flat(rows, sizes)
    if flat:
        print(flat)
        print()

    # **界松分类**（`W-Target-Loose` 的实测那一半）：**只加一节，不动主表与退出码**。
    loose = render_loose(rows, sizes)
    if loose:
        print(loose)
        print()

    md_breaches, bad = render_breaches(rows)
    print(md_breaches)
    print()
    tail = render_unmeasured(rows)
    if tail:
        print(tail)
        print()
    if failures:
        print("## 整个模块没测成")
        for f in failures:
            print(f"  · {f}")
        print()

    if opts["save_baseline"]:
        save_baseline(opts["save_baseline"], rows, sizes, runs, opts["max_steps"])
        print(f"→ baseline 已写 `{opts['save_baseline']}`（{len(rows)} 行）")
        print()

    if opts["baseline"]:
        try:
            with io.open(opts["baseline"], encoding="utf-8") as f:
                base = json.load(f)
        except Exception as exc:                        # noqa: BLE001
            print(f"✗ 读不了 baseline `{opts['baseline']}`：{type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 2
        print(render_diff(rows, base, opts["baseline"], opts["diff_pct"]))
        print()

    if bad:
        print(f"✗ 退出码 1：**{len(bad)} 行破界（κ < ops）** —— 见上面单独列出的那一节。")
        print("  破界 = 推导出来的上界压不住实测：要么 κ 少算（成本域有问题），"
              "要么 ops 多算（拷贝/内建口径变了）。")
        return 1
    print("✓ 退出码 0：没有破界（κ ≥ ops）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
