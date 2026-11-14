#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""ai_run.py —— KPI③「多模型 × 12 题」的**运行账本**：prepare / score / freeze / list

## 为什么有这个工具

KPI③（AI 一次通过率）现在卡住的**不是材料，是流程**：11-12 到 11-13 一共跑过 4 个
实例（实例1/2/3 各 4 题、实例4 全 12 题），**没有一次记下 provider / model / version /
temperature**。没有归因的数字不能算证据——它能说明"有人写出来了"，不能说明"某个模型
的一次通过率是多少"，也无法复现、无法对比、无法排除"作者读过参考答案"。

这个工具**不负责跑模型**（模型的会话在调用方那里，见 `prepare` 打印的提示词）。
它负责把「一次跑」变成**可复现、可归因、可打分**的一件事，4 条子命令：

| 子命令 | 干什么 | 为什么 |
|---|---|---|
| `freeze` | 打印 / 校验材料的 sha256 清单 | **尺子**：尺子一变，历史数字就不可比，而且必须**吵出来**是哪个文件变了 |
| `prepare` | 建一个**隔离运行目录**（材料 + 12 题，**没有** `参考.eln`）+ 记录元数据 | **隔离**：测的是"它会不会写"，不是"它会不会抄" |
| `score` | 调 `打分.py` 打三栏，把三栏 + 元数据写成一份 JSON，并追加到账本 | **归因**：数字必须带着"谁写的 / 什么时候 / 哪把尺子 / 干净不干净" |
| `list` | 从账本读回所有运行，算「≥3 模型 × 12 题」达成没有 | **判据**：KPI③ 要能一条命令回答，而不是翻四个目录数表格 |

## 它**不**做什么（重要，别指望它做）

- **不跑模型**。模型会话由调用方开。`prepare` 只能保证**目录**干净（只有材料 + 题目，
  没有 `参考.eln`、没有 `打分.py`、没有历史提交），**不能**保证**会话**干净——
  会话里读没读过仓库是模型的事。所以 `score` 让你用 `--contamination` **声明**，
  而不是替你猜：声明 `none` 才计入 KPI。
  另外：交给模型的是**那一个运行目录**，别把上级 `运行/` 一起给它——上级里还有
  别的运行的 `提交/`（答案）和结果 JSON。
- **不改三栏口径**。`score` 用 subprocess 跑 `python -B 打分.py --提交 … --json …`，
  和手工跑是**同一条代码路径**：记录里的 `rows` 就是 `打分.py` 自己写出来的 `rows`，
  原样搬运，一个字段都不重算。这里没有第二套判定，也就没有"两套口径对不上"。
- **不动题目与材料**。12 个 `.tu`、`材料/`、`打分.py` 一个字节都不写（`freeze` 只读，
  `prepare` 只拷）。材料的冻结值变了就报错，改不改由人决定（`freeze --write` 立新 epoch，
  旧 epoch 连同 diff 记进 `history`）。

## 用法

```powershell
$env:PYTHONIOENCODING = "utf-8"

# 0) 第一次：把当前材料冻成 epoch（之后材料一动就报错，并指出哪个文件）
python -B tools/ai_run.py freeze --write
python -B tools/ai_run.py freeze                    # 校验

# 1) 开一次运行：隔离目录 + 元数据（4 个字段缺一个就拒绝）
python -B tools/ai_run.py prepare --provider deepseek --model deepseek-v4.1-flash `
    --version "2026-11-13" --temperature 0.0

# 2) 把 <运行目录> 与打印出来的提示词交给被测模型（会话只读这个目录）

# 3) 交完打分：三栏 + 元数据 → 一份 JSON + 追加到账本
python -B tools/ai_run.py score --run "<运行目录>" --sub "<运行目录>\提交"

# 4) 「≥3 模型 × 12 题」达成没有？
python -B tools/ai_run.py list
```

旧数据（模型名没记录、作者读过参考答案）也能入账，但必须**如实标注**：
`--model unknown --legacy-reason "11-12 的会话没记模型"`、`--contamination
author-read-reference`。这样的运行 `kpi_countable` 是 `false`，`list` 会把它单列。

## 退出码

`0` = 成功（`score` 还要求 12/12 三列全过）；`1` = 跑完了但没全过（**不是**工具错，
是这一跑的分数）；`2` = 拒绝执行（元数据缺失 / 材料变了 / 运行目录被改过 / 用法错）。

**「没跑 / 未交」永远记成 `未交`**：`summary.not_submitted` 单列，
`full_pass` 与 `pass_rate` 只在真交了的题上成立——不会因为"没跑"而变成"通过"。
反过来也不许：**12 题全是 `未交`** 时默认**拒绝记录**（那是"没跑"，不是"0 分"），
确实要记成一次 0 分运行才加 `--allow-empty`。

## 与实验目录的关系

默认实验目录 = 仓库里那个 `文档/设计/…/实验-2026-11-12-AI一次通过率-12题/`
（`文档/设计/` 是 gitignored，所以这个工具在干净 checkout 上**需要 `--bench`**）。
它在实验目录里只写四样东西：`材料.sha256.json`（冻结清单）、`运行/`（隔离目录 +
提示词）、`运行-结果.jsonl`（账本）、`运行/结果-*.json`（每次的完整 JSON）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from typing import Dict, List, Optional, Tuple

#: 中文 Windows 控制台默认 GBK，`✓` / `✗` 会 UnicodeEncodeError 死在打印时
#: （`tests/runner.py` / `tools/gates.py` 都踩过）。父进程自己配一次。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

TOOL_VERSION = "1.0"
SCHEMA_META = "ai-run-meta/1"
SCHEMA_FREEZE = "ai-material-freeze/1"
SCHEMA_RECORD = "ai-run-record/1"

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

BENCH_ENV = "ELENCH_AI_BENCH"
BENCH_DEFAULT = os.path.join(ROOT, "文档", "设计", "目前设计",
                             "实验-2026-11-12-AI一次通过率-12题")
FREEZE_NAME = "材料.sha256.json"
RESULTS_NAME = "运行-结果.jsonl"
RUNS_DIR = "运行"
SUBMIT_DIR = "提交"
META_NAME = "运行-元数据.json"
SCORER = "打分.py"
MATERIAL_DIR = "材料"
PROMPT_NAME = "给AI的提示词.md"

#: 协议是 12 题。`kpi_countable` 要求题目数正好等于它——少一题就不是这个 KPI。
PROTOCOL_PROBLEMS = 12
META_FIELDS = ("provider", "model", "version", "temperature")
UNKNOWN = "unknown"
CONTAMINATIONS = ("none", "unknown", "author-read-reference")


# ===================================================================== 基础

class Refusal(Exception):
    """拒绝执行：不是崩溃，是"这一步现在不该做"（退出码 2）。"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, obj) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    # newline="\n"：Windows 文本模式会把 \n 翻成 \r\n，清单的 sha256 就会跟着变。
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def relpath(base: str, path: str) -> str:
    """相对路径统一用 `/`：清单的 key 不能随平台变。"""
    return os.path.relpath(path, base).replace("\\", "/")


def abspath_of(base: str, key: str) -> str:
    return os.path.join(base, *key.split("/"))


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def short(h: str, n: int = 12) -> str:
    """`sha256:abcdef…` 的显示形式：保留前缀和前 n 位。"""
    return h[:len("sha256:") + n] + "…"


def eprint(*a, **kw) -> None:
    """stderr 输出前先把 stdout 冲掉：`cmd /c … 2>&1` 合并两条流时顺序才不乱。"""
    sys.stdout.flush()
    print(*a, file=sys.stderr, **kw)


def bench_root(arg: Optional[str]) -> str:
    b = arg or os.environ.get(BENCH_ENV) or BENCH_DEFAULT
    b = os.path.abspath(b)
    if not os.path.isdir(b):
        raise Refusal(f"找不到实验目录：{b}\n"
                      f"  用 --bench 指定（或设 {BENCH_ENV}）；"
                      "它默认在 文档/设计/ 下，而那是 gitignored。")
    if not os.path.isfile(os.path.join(b, SCORER)):
        raise Refusal(f"{b} 里没有 {SCORER}——这不像 KPI③ 的实验目录")
    return b


# ===================================================================== 题目与材料

def problem_names(bench: str) -> List[str]:
    """12 个题目目录（`NN_名字` 且含 cases.json）。

    判定条件与 `打分.py:load_problems` **一致**（同样要求 cases.json）——
    两边必须认同一组题，否则"12 题"这个分母会悄悄变。
    """
    out = []
    for n in sorted(os.listdir(bench)):
        p = os.path.join(bench, n)
        if (os.path.isdir(p) and re.match(r"^\d\d_", n)
                and os.path.isfile(os.path.join(p, "cases.json"))):
            out.append(n)
    return out


#: 一个题目目录里，**发给 AI** 的东西。`参考.eln` 永远不在里面。
PROBLEM_SEND = ("题目.md", "spec.tu", "cases.json")


def material_files(bench: str) -> List[str]:
    """**冻结范围**：决定"这次测量是什么"的全部输入。

    = `材料/**` + 每个题目的 `题目.md/spec.tu/cases.json/参考.eln` + `打分.py`。
    连 `参考.eln` 与 `打分.py` 一起冻，是因为它们同样是测量的定义：
    `参考.eln` 是"12/12 自检"的基准，`打分.py` 是三栏口径本身。
    它们一变，历史数字就不再是同一个实验（哪怕 `材料/` 没动）。
    """
    files: List[str] = []
    mat = os.path.join(bench, MATERIAL_DIR)
    if os.path.isdir(mat):
        for root, dirs, names in os.walk(mat):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for n in sorted(names):
                if n.startswith(".") or n == "Thumbs.db":
                    continue
                files.append(relpath(bench, os.path.join(root, n)))
    for p in problem_names(bench):
        for n in ("题目.md", "spec.tu", "cases.json", "参考.eln"):
            if os.path.isfile(os.path.join(bench, p, n)):
                files.append(f"{p}/{n}")
    if os.path.isfile(os.path.join(bench, SCORER)):
        files.append(SCORER)
    return sorted(files)


def build_manifest(bench: str, files: List[str]) -> Dict[str, str]:
    return {k: sha256_file(abspath_of(bench, k)) for k in files}


def manifest_digest(files: Dict[str, str]) -> str:
    """清单本身的指纹：`(路径, 哈希)` 集合的规范化 JSON 的 sha256。"""
    text = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(text)


def diff_manifest(old: Dict[str, str], new: Dict[str, str]) -> Dict[str, List[str]]:
    return {
        "changed": sorted(k for k in old if k in new and old[k] != new[k]),
        "missing": sorted(k for k in old if k not in new),
        "added": sorted(k for k in new if k not in old),
    }


def freeze_path(bench: str) -> str:
    return os.path.join(bench, FREEZE_NAME)


def load_freeze(bench: str) -> Optional[dict]:
    p = freeze_path(bench)
    if not os.path.isfile(p):
        return None
    try:
        data = read_json(p)
    except (OSError, ValueError) as e:
        raise Refusal(f"冻结清单读不出来：{p}\n  {e}")
    if data.get("schema") != SCHEMA_FREEZE:
        raise Refusal(f"{p} 的 schema 是 {data.get('schema')!r}，不是 {SCHEMA_FREEZE}")
    if not isinstance(data.get("files"), dict):
        raise Refusal(f"{p} 里没有 files 表")
    return data


def verify_freeze(bench: str) -> dict:
    """把"现在盘上的材料"和"冻结值"对一遍。

    返回 dict（**不抛异常**，由调用方决定是拒绝还是只提示）：
      ok / freeze / current / changes / digest / tampered
    `tampered` = 清单文件自己的 digest 对不上（清单被手改过）。
    """
    fr = load_freeze(bench)
    cur = build_manifest(bench, material_files(bench))
    out = {"freeze": fr, "current": cur, "digest": manifest_digest(cur),
           "changes": None, "ok": False, "tampered": False}
    if fr is None:
        out["reason"] = "还没冻结（没有 %s）" % FREEZE_NAME
        return out
    out["tampered"] = manifest_digest(fr["files"]) != fr.get("digest")
    out["changes"] = diff_manifest(fr["files"], cur)
    out["ok"] = (not out["tampered"]) and not any(out["changes"].values())
    if not out["ok"]:
        out["reason"] = "清单被改过" if out["tampered"] else "材料与冻结值不符"
    return out


def render_freeze_problem(v: dict) -> str:
    fr = v.get("freeze")
    if fr is None:
        return (f"✗ {v['reason']}\n"
                "  先跑 `python -B tools/ai_run.py freeze --write` 把当前材料冻成一个 epoch——\n"
                "  没有冻结值就没有「这次的数字属于哪一版材料」这回事。")
    lines = [f"✗ {v['reason']}（冻结 epoch {fr.get('epoch')}，digest {short(fr.get('digest', ''))}）"]
    ch = v.get("changes") or {}
    for kind, label in (("changed", "改动"), ("added", "新增"), ("missing", "缺失")):
        for k in ch.get(kind, []):
            old = fr["files"].get(k)
            new = v["current"].get(k)
            if kind == "changed":
                lines.append(f"    {label}  {k}   {short(old)} → {short(new)}")
            elif kind == "added":
                lines.append(f"    {label}  {k}   {short(new or '')}")
            else:
                lines.append(f"    {label}  {k}   {short(old or '')}")
    if v.get("tampered"):
        lines.append("    （清单文件自己的 digest 也对不上：有人手改过它）")
    lines.append("  材料/题目/打分器一变，历史数字就不再是同一个实验。"
                 "确属有意改动就 `freeze --write` 立新 epoch（旧 epoch 连 diff 进 history），")
    lines.append("  然后**重新**跑——不要把新数字接在旧数字后面。")
    return "\n".join(lines)


# ===================================================================== 元数据

def parse_temperature(raw) -> object:
    """返回 float 或 `"unknown"`。0 是合法温度，所以"缺"只能用 None 表示。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.lower() == UNKNOWN:
        return UNKNOWN
    try:
        return float(s)
    except ValueError:
        raise Refusal(f"--temperature 要一个数（或 {UNKNOWN}），拿到的是 {raw!r}")


def make_attribution(provider, model, version, temperature,
                     legacy_reason: Optional[str] = None) -> dict:
    """四个字段**一个都不能少**；`unknown` 是显式的值，但必须给理由。

    这条不能松：11-12 那 4 个实例就是"数字在、归因没了"，于是每个数字都要重跑。
    """
    raw = {"provider": provider, "model": model,
           "version": version, "temperature": temperature}
    missing = [f for f, v in raw.items()
               if v is None or (isinstance(v, str) and not v.strip())]
    if missing:
        raise Refusal(
            "元数据缺失：" + " / ".join(missing) + "\n"
            "  provider / model / version / temperature **缺一个都不打分**——\n"
            "  没有归因能力的数字不算证据（11-12 那 4 个实例就是这么废掉的）。\n"
            "  真的没记录（旧数据）就把那几项写成 " + UNKNOWN + "，\n"
            '  并加 --legacy-reason "为什么没记录"；这样的运行不计入 KPI。')
    unknown = [f for f, v in raw.items()
               if isinstance(v, str) and v.strip().lower() == UNKNOWN]
    if unknown and not (legacy_reason and str(legacy_reason).strip()):
        raise Refusal(
            " / ".join(unknown) + f" 写成了 {UNKNOWN}，但没给理由。\n"
            "  `unknown` 是**记录下来的未知**（不是「跳过」）：加 "
            '--legacy-reason "为什么没记录"，这条运行会记成 kpi_countable=false。')
    temp = parse_temperature(temperature)
    return {
        "provider": str(provider).strip(),
        "model": str(model).strip(),
        "version": str(version).strip(),
        "temperature": None if temp == UNKNOWN else temp,
        "status": "recorded" if not unknown else "unknown",
        "unknown_fields": unknown,
        "note": (legacy_reason or "").strip(),
    }


def fmt_temperature(att: dict) -> str:
    t = att.get("temperature")
    if t is None:
        return UNKNOWN if "temperature" in (att.get("unknown_fields") or []) else "—"
    return repr(float(t))


def fmt_attribution(att: dict) -> str:
    return (f"provider={att['provider']}  model={att['model']}  "
            f"version={att['version']}  temperature={fmt_temperature(att)}")


# ===================================================================== 隔离计划

#: 实验目录顶层"不是材料也不是题目"的东西 → 为什么**不**拷进隔离目录。
def _why_excluded(name: str, isdir: bool) -> str:
    if name.startswith("提交-实例"):
        return "历史提交——里面就是答案"
    if name.startswith("负对照"):
        return "故意写错的实现 + 缺口探针（含正确写法）"
    if name.startswith("探针-"):
        return "语法探针（每个探针都带正确写法）"
    if name.startswith("归档-"):
        return "上一版材料（已过期，混进来会教错）"
    if name.startswith("材料自检"):
        return "材料里代码块的真实实现（答案）"
    if name.startswith("结果-"):
        return "历史结果（含上次的分数）"
    if name.startswith("自检-"):
        return "12 份参考实现的 check/cost 真实输出（答案）"
    if name in ("README.md",) or name.endswith(".md"):
        return "协议与历史记录（不是材料）"
    if name.endswith(".py"):
        return "评分器 / 脚本：被测环境里不该出现"
    return "不属于「材料 + 题目」"


def isolation_plan(bench: str, include_cases: bool = True) -> Tuple[List[str], List[dict]]:
    """返回 (要拷的相对路径, 不拷的条目及原因)。

    这是**隔离的口径本身**：只拷"材料 + 题目"。`参考.eln` 永远不拷
    （拷了就是测抄），`材料/给AI的提示词.md` 也不拷（材料里自己写明"不发给 AI"）。
    """
    inc: List[str] = []
    exc: List[dict] = []

    mat = os.path.join(bench, MATERIAL_DIR)
    if os.path.isdir(mat):
        for n in sorted(os.listdir(mat)):
            p = os.path.join(mat, n)
            if not os.path.isfile(p):
                continue
            key = f"{MATERIAL_DIR}/{n}"
            if n == PROMPT_NAME:
                exc.append({"path": key, "count": 1,
                            "why": "协议模板——材料里自己写明「这份文件不发给 AI」"})
            else:
                inc.append(key)

    n_ref = n_cases = n_other = 0
    for p in problem_names(bench):
        for n in sorted(os.listdir(os.path.join(bench, p))):
            key = f"{p}/{n}"
            if n == "参考.eln":
                n_ref += 1
                continue
            if n == "cases.json" and not include_cases:
                n_cases += 1
                continue
            if n in PROBLEM_SEND:
                inc.append(key)
            else:
                n_other += 1
    if n_ref:
        exc.append({"path": "<题目>/参考.eln", "count": n_ref,
                    "why": "参考答案——给了就是测抄（这是整个工具存在的第一个理由）"})
    if n_cases:
        exc.append({"path": "<题目>/cases.json", "count": n_cases,
                    "why": "--no-cases：去掉给 AI 自查的期望输出（协议变了，与历史不可比）"})
    if n_other:
        exc.append({"path": "<题目>/其它文件", "count": n_other,
                    "why": "不属于「题目.md + spec.tu + cases.json」"})

    keep = {MATERIAL_DIR, SCORER, FREEZE_NAME, RESULTS_NAME, RUNS_DIR} | set(problem_names(bench))
    for n in sorted(os.listdir(bench)):
        if n in keep or n.startswith("."):
            continue
        p = os.path.join(bench, n)
        exc.append({"path": n + ("/" if os.path.isdir(p) else ""), "count": 1,
                    "why": _why_excluded(n, os.path.isdir(p))})
    return sorted(inc), exc


#: 隔离目录里**不许出现**的文件名（大小写不敏感）。
FORBIDDEN_NAMES = {"参考.eln", SCORER}
FORBIDDEN_PREFIX = ("结果-", "自检-")


def scan_isolation(root: str) -> List[str]:
    """在隔离目录里找"不该在这儿"的文件，返回相对路径列表（空 = 干净）。"""
    hits: List[str] = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        for n in names:
            low = n.lower()
            if low in {x.lower() for x in FORBIDDEN_NAMES} or n.startswith(FORBIDDEN_PREFIX):
                hits.append(relpath(root, os.path.join(d, n)))
    return sorted(hits)


# ===================================================================== 输出小工具

def dw(s) -> int:
    """显示宽度：中文/全角算 2 列（表格对齐用）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in str(s))


def pad(s, width: int, align: str = "left") -> str:
    s = str(s)
    while dw(s) > width and len(s) > 1:
        s = s[:-1]
    gap = " " * max(0, width - dw(s))
    return gap + s if align == "right" else s + gap


def render_tree(root: str, annotate: Optional[Dict[str, str]] = None) -> List[str]:
    annotate = annotate or {}
    lines = [os.path.basename(os.path.normpath(root)) + "/"]
    #: 材料/ 与 提交/ 排在最前（读的人先看"给什么、往哪写"），其余按名字。
    rank = {MATERIAL_DIR: 0, SUBMIT_DIR: 1}

    def sort_key(d: str, n: str):
        isdir = os.path.isdir(os.path.join(d, n))
        return (not isdir, rank.get(n, 2) if isdir else 0, n)

    def walk(d: str, prefix: str) -> None:
        entries = sorted(os.listdir(d), key=lambda n: sort_key(d, n))
        for i, n in enumerate(entries):
            last = i == len(entries) - 1
            p = os.path.join(d, n)
            conn = "└── " if last else "├── "
            key = relpath(root, p)
            note = ("   " + annotate[key]) if key in annotate else ""
            if os.path.isdir(p):
                inner = [x for x in os.listdir(p) if x != "__pycache__"]
                lines.append(f"{prefix}{conn}{n}/" + (f"  （空）{note}" if not inner else note))
                walk(p, prefix + ("    " if last else "│   "))
            else:
                lines.append(f"{prefix}{conn}{n}  ({os.path.getsize(p)} B){note}")
    walk(root, "")
    return lines


def git_info() -> dict:
    """记一笔"当时的代码是哪一版"。失败不算错（干净 checkout 上也能跑）。

    `dirty_list` 也记下来：在一次多代理并行的工作里，"脏"往往是**别人**在改文档，
    光记一个 `dirty: true` 会让人以为工具链被人动过。
    """
    try:
        head = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=10).stdout.strip()
        st = subprocess.run(["git", "-C", ROOT, "-c", "core.quotepath=false",
                             "status", "--porcelain"],
                            capture_output=True, text=True, encoding="utf-8",
                            timeout=20).stdout
        dirty = [ln.strip() for ln in st.splitlines() if ln.strip()]
        return {"head": head or "unknown", "dirty": bool(dirty),
                "dirty_files": len(dirty), "dirty_list": dirty[:10]}
    except (OSError, subprocess.SubprocessError):
        return {"head": "unknown", "dirty": None, "dirty_files": None, "dirty_list": []}


def tool_info() -> dict:
    return {"name": "ai_run.py", "version": TOOL_VERSION, **git_info()}


# ===================================================================== freeze

def cmd_freeze(args) -> int:
    bench = bench_root(args.bench)
    files = material_files(bench)
    cur = build_manifest(bench, files)
    digest = manifest_digest(cur)
    fr = load_freeze(bench)

    if args.write:
        history = list(fr.get("history") or []) if fr else []
        changes = diff_manifest(fr["files"], cur) if fr else None
        if fr and fr.get("digest") == digest and not any((changes or {}).values()):
            print(f"材料没变（digest {short(digest)}，{len(cur)} 个文件），epoch 保持 "
                  f"{fr.get('epoch')}")
            _print_manifest(cur, fr.get("epoch") or "")
            return 0
        if fr:
            history.append({
                "epoch": fr.get("epoch"), "digest": fr.get("digest"),
                "count": fr.get("count"), "superseded_at": now_iso(),
                "changes": {k: v for k, v in (changes or {}).items() if v},
            })
        obj = {"schema": SCHEMA_FREEZE, "epoch": now_iso(), "stamp": stamp(),
               "digest": digest, "count": len(cur), "files": cur, "history": history}
        write_json(freeze_path(bench), obj)
        print(f"✓ 已冻结：{len(cur)} 个文件 → {FREEZE_NAME}")
        _print_manifest(cur, obj["epoch"])
        if changes and any(changes.values()):
            print("\n  ⚠ 这次 freeze 覆盖了一次**真实变化**（历史数字与之前不可比）：")
            for kind, label in (("changed", "改动"), ("added", "新增"), ("missing", "缺失")):
                for k in changes[kind]:
                    print(f"    {label}  {k}")
        return 0

    # 校验（默认）
    v = verify_freeze(bench)
    if not v["ok"]:
        eprint(render_freeze_problem(v))
        return 1
    print(f"✓ 材料校验通过：{v['freeze'].get('count')} 个文件与冻结值逐字节相同")
    print(f"  epoch  {v['freeze'].get('epoch')}")
    print(f"  digest {v['digest']}")
    _print_manifest(v["current"], v["freeze"].get("epoch") or "")
    return 0


def _print_manifest(files: Dict[str, str], epoch: str) -> None:
    print()
    print("  sha256 清单")
    width = max([len(k) for k in files] or [0])
    for k in sorted(files):
        print(f"    {k:<{width}}  {files[k]}")


# ===================================================================== prepare

def new_run_id(att: dict, ts: Optional[str] = None) -> str:
    slug = re.sub(r"[^0-9A-Za-z._-]+", "-", str(att.get("model") or "")).strip("-")[:40]
    return f"{ts or stamp()}-{slug or 'model'}"


def ensure_out_dir(out: str) -> None:
    """**不覆盖**已有运行：删不删由人决定，工具不替你删。"""
    if os.path.exists(out):
        if not os.path.isdir(out):
            raise Refusal(f"{out} 已存在且不是目录")
        if os.listdir(out):
            raise Refusal(f"{out} 已存在且非空——prepare 不覆盖已有运行"
                          "（换 --out / --run-id，或自己确认后删掉）")
        return
    os.makedirs(out)


def build_prompt(bench: str, out: str, include_cases: bool) -> Optional[str]:
    """从 `材料/给AI的提示词.md` 里抠出 ```text 块，把占位符换成真实路径。

    不自己写提示词：那段文字是**冻结材料**的一部分（也是历史实例用的同一段），
    改它就是改协议。
    """
    src = os.path.join(bench, MATERIAL_DIR, PROMPT_NAME)
    if not os.path.isfile(src):
        return None
    with open(src, "r", encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"```text\r?\n(.*?)```", text, re.S)
    if not m:
        return None
    t = m.group(1)
    t = t.replace("<材料目录>", os.path.join(out, MATERIAL_DIR))
    t = t.replace("<题目目录>", out)
    t = t.replace("<提交目录>", os.path.join(out, SUBMIT_DIR))
    t = t.replace("<语言仓库>", ROOT)
    if not include_cases:
        t += ("\n（本轮**不发** cases.json：只依据 题目.md 与 spec.tu 写，"
              "不要期望有期望输出可对。）\n")
    return t


def cmd_prepare(args) -> int:
    bench = bench_root(args.bench)
    att = make_attribution(args.provider, args.model, args.version, args.temperature,
                          args.legacy_reason)
    v = verify_freeze(bench)
    if not v["ok"]:
        eprint(render_freeze_problem(v))
        return 2

    include_cases = not args.no_cases
    inc, exc = isolation_plan(bench, include_cases=include_cases)
    run_id = args.run_id or new_run_id(att)
    out = os.path.abspath(args.out or os.path.join(bench, RUNS_DIR, run_id))
    ensure_out_dir(out)

    for key in inc:
        dst = abspath_of(out, key)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(abspath_of(bench, key), dst)
    os.makedirs(os.path.join(out, SUBMIT_DIR), exist_ok=True)

    copied = {relpath(out, abspath_of(out, k)): sha256_file(abspath_of(out, k)) for k in inc}
    bad = [k for k, h in copied.items() if v["freeze"]["files"].get(k) != h]
    if bad:
        raise Refusal("拷贝后校验失败（材料在拷贝过程中变了？）：\n  " + "\n  ".join(bad))

    leaks = scan_isolation(out)
    if leaks:
        raise Refusal("隔离自检失败：隔离目录里出现了不该有的文件\n  " + "\n  ".join(leaks))

    meta = {
        "schema": SCHEMA_META,
        "run_id": run_id,
        "created_at": now_iso(),
        "attribution": att,
        "clean": {"isolation": "prepared", "contamination": "none",
                  "note": "目录已隔离（无 参考.eln / 打分.py / 历史提交）；"
                          "会话是否干净由调用方声明（score --contamination）"},
        "protocol": {"problems": len(problem_names(bench)),
                     "cases_included": include_cases,
                     "prompt_source": f"{MATERIAL_DIR}/{PROMPT_NAME}"},
        "bench": {"root": bench, "freeze_epoch": v["freeze"].get("epoch"),
                  "material_digest": v["digest"],
                  "material_count": len(v["freeze"]["files"])},
        "materials": copied,
        "excluded": exc,
        "tool": tool_info(),
        "note": args.note or "",
    }
    write_json(os.path.join(out, META_NAME), meta)

    prompt_file = None
    prompt = build_prompt(bench, out, include_cases)
    if prompt and not args.no_prompt_file:
        runs_dir = os.path.join(bench, RUNS_DIR)
        os.makedirs(runs_dir, exist_ok=True)
        prompt_file = os.path.join(runs_dir, f"{run_id}.提示词.md")
        with open(prompt_file, "w", encoding="utf-8", newline="\n") as f:
            f.write("<!-- 由 tools/ai_run.py prepare 生成；交给被测模型的**整段文字**在下面 -->\n\n")
            f.write("```text\n" + prompt + "```\n")

    print("══ prepare ══")
    print(f"运行 id    {run_id}")
    print(f"目录       {out}")
    print(f"元数据     {fmt_attribution(att)}"
          + (f"   （归因 unknown：{att['note']}）" if att["status"] != "recorded" else ""))
    print(f"材料冻结   epoch {v['freeze'].get('epoch')}   digest {short(v['digest'])}"
          f"   {len(v['freeze']['files'])} 个文件逐一校验通过")
    print(f"协议       {meta['protocol']['problems']} 题   cases.json "
          f"{'已给（按冻结的提示词：' + '给 AI 自查' + '）' if include_cases else '不给（--no-cases）'}")
    print()
    print("目录树（这就是被测模型能看到的**全部**）：")
    for ln in render_tree(out, annotate={
            SUBMIT_DIR: "← 被测模型往这里写 12 个 .eln",
            META_NAME: "← 归因 + 冻结值（provenance，不是材料）",
            f"{MATERIAL_DIR}/{PROMPT_NAME}": ""}):
        print("  " + ln)
    print()
    print("不拷进去的东西（隔离的口径）：")
    for e in exc:
        cnt = f" ×{e['count']}" if e["count"] > 1 else ""
        print(f"  ✗ {e['path']}{cnt}   {e['why']}")
    print()
    print("隔离自检：")
    print(f"  参考.eln            {0 if not any('参考.eln' in x for x in leaks) else len([x for x in leaks if '参考.eln' in x])} 个（必须是 0）")
    print(f"  打分.py / 结果-*    {'0 个' if not leaks else str(len(leaks)) + ' 个'}")
    print(f"  cases.json          {len([k for k in inc if k.endswith('cases.json')])} 个")
    print(f"  {MATERIAL_DIR}/ 文件          {len([k for k in inc if k.startswith(MATERIAL_DIR + '/')])} 个")
    print(f"  提交/               {'空' if not os.listdir(os.path.join(out, SUBMIT_DIR)) else '非空'}")
    print()
    print("下一步")
    if prompt_file:
        print(f"  ① 把这段提示词交给被测模型（一个**新会话**，只给它这个目录）：")
        print(f"     {prompt_file}")
    else:
        print("  ① 提示词：--no-prompt-file，按 材料/给AI的提示词.md 的模板自己填路径")
    print(f"  ② 它把 12 个 .eln 写到 {os.path.join(out, SUBMIT_DIR)}")
    print(f"  ③ 打分：python -B tools/ai_run.py score --run \"{out}\"")
    return 0


# ===================================================================== 打分

def load_run_meta(run: str) -> dict:
    p = os.path.join(run, META_NAME)
    if not os.path.isfile(p):
        raise Refusal(f"{run} 里没有 {META_NAME}——它不是 prepare 出来的运行目录。\n"
                      "  手工提交请直接用 --sub + 4 个元数据参数（旧数据还要 --legacy-reason）。")
    meta = read_json(p)
    if meta.get("schema") != SCHEMA_META:
        raise Refusal(f"{p} 的 schema 是 {meta.get('schema')!r}，不是 {SCHEMA_META}")
    att = meta.get("attribution") or {}
    for f in META_FIELDS:
        if f not in att:
            raise Refusal(f"{p} 的 attribution 缺字段 {f}")
    return meta


def verify_run_dir(run: str, meta: dict) -> None:
    """运行目录里的材料/契约必须和 prepare 时一模一样。

    被测模型被明确要求"不许修改 spec.tu"，但**改了也照样能编译**——
    所以这里逐字节对一遍。这一条同时防住"改契约迁就自己的实现"。
    """
    bad: List[str] = []
    for key, want in (meta.get("materials") or {}).items():
        p = abspath_of(run, key)
        if not os.path.isfile(p):
            bad.append(f"缺失  {key}")
        elif sha256_file(p) != want:
            bad.append(f"改动  {key}")
    if bad:
        raise Refusal("运行目录被改过（材料与契约是给定的，不许改）：\n  "
                      + "\n  ".join(bad)
                      + "\n  这次提交作废；重新 prepare 一次再跑。")


def submission_manifest(sub: str) -> Dict[str, str]:
    """把提交的**字节**也记下来：数字指向哪一份文件，事后能复核。"""
    if os.path.isfile(sub):
        return {os.path.basename(sub): sha256_file(sub)}
    out: Dict[str, str] = {}
    for d, dirs, names in os.walk(sub):
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        for n in sorted(names):
            if n.endswith(".eln"):
                p = os.path.join(d, n)
                out[relpath(sub, p)] = sha256_file(p)
    return dict(sorted(out.items()))


def run_scorer(bench: str, sub: str) -> dict:
    """跑 `打分.py`（**子进程**，不是 import）。

    为什么用子进程而不是 `import` 它的 `judge_*`：这样"工具打的分"和"人手工打的分"
    是同一条代码路径、同一份输出；`rows` 原样搬，没有机会在中间重算一遍。
    """
    tmp = tempfile.mkdtemp(prefix="ai-run-")
    jp = os.path.join(tmp, "打分.json")
    argv = [sys.executable, "-B", SCORER, "--提交", sub, "--json", jp]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    t0 = time.time()
    try:
        p = subprocess.run(argv, cwd=bench, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=1800)
    except subprocess.TimeoutExpired:
        raise Refusal("打分.py 超过 1800 秒没跑完")
    dt = time.time() - t0
    text = (p.stdout or "") + (p.stderr or "")
    if not os.path.isfile(jp):
        raise Refusal(f"打分.py 没写出 JSON（退出码 {p.returncode}）：\n{text[-3000:]}")
    data = read_json(jp)
    shutil.rmtree(tmp, ignore_errors=True)
    if not isinstance(data.get("rows"), list):
        raise Refusal(f"打分.py 的 JSON 里没有 rows：{str(data)[:300]}")
    return {"returncode": p.returncode, "stdout": text, "data": data, "elapsed_s": dt}


SEM_RE = re.compile(r"^(\d+)/(\d+)$")


def summarize(rows: List[dict]) -> dict:
    """把 `打分.py` 的 rows 折成计数。**只数，不判**（判是打分.py 的事）。

    `full_pass` = check 通过 **且** 语义全对 **且** κ 达成——三列同时成立才叫通过。
    `未交` 只进 `not_submitted`，永远不进 `full_pass`（这是本仓库的老毛病，盯住它）。
    """
    total = len(rows)
    not_sub = [r for r in rows if r.get("check") == "未交"]
    sem_ok = sem_n = 0
    full = 0
    for r in rows:
        m = SEM_RE.match(str(r.get("sem", "")))
        a = b = 0
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            sem_ok += a
            sem_n += b
        if r.get("check") == "通过" and b > 0 and a == b and r.get("kappa") == "达成":
            full += 1
    return {
        "total": total,
        "submitted": total - len(not_sub),
        "not_submitted": len(not_sub),
        "check": sum(1 for r in rows if r.get("check") == "通过"),
        "sem_ok": sem_ok,
        "sem_n": sem_n,
        "kappa": sum(1 for r in rows if r.get("kappa") == "达成"),
        "full_pass": full,
        "pass_rate": round(full / total, 4) if total else 0.0,
    }


def kpi_blockers(att: dict, contamination: str, summary: dict) -> List[str]:
    b: List[str] = []
    if att.get("status") != "recorded":
        u = att.get("unknown_fields") or []
        b.append("元数据未记录（" + (" / ".join(u) if u else "unknown") + "）")
    if contamination != "none":
        b.append(f"不干净（contamination={contamination}）")
    if summary["total"] != PROTOCOL_PROBLEMS:
        b.append(f"题目数 {summary['total']} ≠ {PROTOCOL_PROBLEMS}")
    return b


def check_measured(summary: dict, allow_empty: bool = False) -> None:
    """一题都没交 ≠ 通过率 0%。

    "没跑"和"0 分"是两件事：前者没有信息量，后者是一个真实的数据点。
    `打分.py` 把认领不到的题记成 `未交`（正确），但**整次都是 `未交`** 时，
    它只是"这个目录里没人写东西"——不能悄悄变成 KPI 里的一个 0。
    真要记，就显式 `--allow-empty`。
    """
    if summary["submitted"] == 0 and not allow_empty:
        raise Refusal(
            f"提交里一题都认领不到（{summary['total']} 题全是「未交」）——\n"
            "  这不是「一次通过率 0%」，这是「没跑」。确认要把它记成一次 0 分的运行，\n"
            "  再加 --allow-empty；否则先检查提交路径/文件名（打分器按**函数名**认领）。")


def build_record(att: dict, contamination: str, isolation: str, summary: dict,
                 rows: List[dict], *, run_id: str, bench: str, material: dict,
                 protocol: dict, sub: str, sub_files: Dict[str, str],
                 elapsed_s: float, returncode: int, note: str = "") -> dict:
    blockers = kpi_blockers(att, contamination, summary)
    return {
        "schema": SCHEMA_RECORD,
        "recorded_at": now_iso(),
        "run_id": run_id,
        "attribution": att,
        "clean": {"isolation": isolation, "contamination": contamination},
        "material": material,
        "protocol": protocol,
        "submission": {"path": sub, "files": sub_files, "count": len(sub_files)},
        "tool": {**tool_info(), "scorer": SCORER,
                 "scorer_returncode": returncode, "elapsed_s": round(elapsed_s, 2)},
        "summary": summary,
        "kpi_countable": not blockers,
        "kpi_blockers": blockers,
        "note": note,
        "rows": rows,
    }


def append_record(path: str, rec: dict) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")


def load_records(path: str) -> List[dict]:
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for i, ln in enumerate(f, 1):
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except ValueError:
                raise Refusal(f"账本第 {i} 行不是合法 JSON：{path}")
    return out


def cmd_score(args) -> int:
    bench = bench_root(args.bench)

    # ① 元数据：只有**一个来源**（运行目录里的，或命令行给的），且必须齐。
    run = os.path.abspath(args.run) if args.run else None
    given = [f for f in META_FIELDS if getattr(args, f) is not None]
    if args.legacy_reason is not None:
        given.append("legacy-reason")
    if run and given:
        raise Refusal("元数据有两个来源（--run 里的 " + META_NAME + " 和 "
                      + " / ".join("--" + g for g in given) + "）；只能有一个。")
    if run:
        if not os.path.isdir(run):
            raise Refusal(f"运行目录不存在：{run}")
        meta = load_run_meta(run)
        att = meta["attribution"]
        isolation = (meta.get("clean") or {}).get("isolation", "prepared")
        contamination = (meta.get("clean") or {}).get("contamination", "unknown")
        protocol = meta.get("protocol") or {}
        run_id = args.run_id or meta.get("run_id") or os.path.basename(run)
        sub = os.path.abspath(args.sub or os.path.join(run, SUBMIT_DIR))
        verify_run_dir(run, meta)
        material = {"freeze_epoch": meta["bench"].get("freeze_epoch"),
                    "digest": meta["bench"].get("material_digest"),
                    "count": meta["bench"].get("material_count"), "verified": None}
    else:
        att = make_attribution(args.provider, args.model, args.version,
                              args.temperature, args.legacy_reason)
        isolation = "ad-hoc"
        contamination = args.contamination or "unknown"
        protocol = {"problems": PROTOCOL_PROBLEMS, "cases_included": None}
        run_id = args.run_id or f"ad-hoc-{stamp()}"
        if not args.sub:
            raise Refusal("没有 --run 就必须给 --sub（提交目录或单个 .eln）")
        sub = os.path.abspath(args.sub)
        material = {"freeze_epoch": None, "digest": None, "count": None, "verified": None}

    if args.contamination:
        if args.contamination not in CONTAMINATIONS:
            raise Refusal(f"--contamination 只能是 {' / '.join(CONTAMINATIONS)}")
        contamination = args.contamination

    # ② 材料冻结：尺子变了就不打分（不管是谁想打）。
    v = verify_freeze(bench)
    if not v["ok"]:
        eprint(render_freeze_problem(v))
        return 2
    material.update({"freeze_epoch": v["freeze"].get("epoch"), "digest": v["digest"],
                     "count": v["freeze"].get("count"), "verified": True})

    # ③ 提交必须存在；空目录要**明说**是 0 题已交，不能静默当成"没跑"混过去。
    if not os.path.exists(sub):
        raise Refusal(f"提交不存在：{sub}")

    print("══ score ══")
    print(f"运行       {run_id}")
    print(f"提交       {sub}")
    print(f"归因       {fmt_attribution(att)}   [{att['status']}]")
    if att["status"] != "recorded":
        print(f"           注意：{' / '.join(att['unknown_fields'])} = unknown"
              f"（{att['note'] or '未给理由'}）→ 不计入 KPI")
    print(f"干净度     isolation={isolation}  contamination={contamination}")
    print(f"材料       epoch {material['freeze_epoch']}  digest {short(material['digest'])}"
          f"  {material['count']} 个文件校验通过")

    res = run_scorer(bench, sub)
    rows = res["data"]["rows"]
    summary = summarize(rows)
    check_measured(summary, allow_empty=args.allow_empty)

    print()
    print("── 打分器原样输出（python -B 打分.py --提交 … --json …）──")
    for ln in res["stdout"].rstrip().splitlines():
        print("  " + ln)

    rec = build_record(att, contamination, isolation, summary, rows,
                       run_id=run_id, bench=bench, material=material,
                       protocol={**protocol, "problems": summary["total"]},
                       sub=sub, sub_files=submission_manifest(sub),
                       elapsed_s=res["elapsed_s"], returncode=res["returncode"],
                       note=args.note or "")

    json_out = os.path.abspath(args.json or os.path.join(
        bench, RUNS_DIR, f"结果-{run_id}.json"))
    write_json(json_out, rec)
    ledger = os.path.abspath(args.ledger or os.path.join(bench, RESULTS_NAME))
    if not args.no_record:
        append_record(ledger, rec)

    print()
    print("── 汇总（三列都来自打分.py，这里只做加法）──")
    s = summary
    print(f"  题目       {s['total']}（已交 {s['submitted']} / 未交 {s['not_submitted']}）")
    print(f"  check      {s['check']}/{s['total']}")
    print(f"  语义       {s['sem_ok']}/{s['sem_n']}")
    print(f"  κ 达成     {s['kappa']}/{s['total']}")
    print(f"  三列同时   {s['full_pass']}/{s['total']}"
          f"（一次通过率 {s['pass_rate'] * 100:.1f}%）")
    if s["not_submitted"]:
        print("  未交不算通过：这些题在 check 列就是 `未交`，"
              "既不是 0 也不是通过（见 JSON 的 rows）。")
    if rec["kpi_countable"]:
        print("  KPI 计入   ✓（元数据齐、干净、12 题）")
    else:
        print("  KPI 计入   ✗ " + "；".join(rec["kpi_blockers"]))
    print()
    print(f"JSON       {json_out}")
    if not args.no_record:
        print(f"账本       +1 行 → {ledger}")
    if args.print_json:
        print()
        print(json.dumps({k: rec[k] for k in ("schema", "run_id", "attribution",
                                              "clean", "material", "protocol",
                                              "summary", "kpi_countable",
                                              "kpi_blockers")},
                         ensure_ascii=False, indent=2))

    return 0 if s["full_pass"] == s["total"] and s["not_submitted"] == 0 else 1


# ===================================================================== list

def kpi_report(recs: List[dict]) -> dict:
    countable = [r for r in recs if r.get("kpi_countable")]
    models: Dict[Tuple[str, str, str], List[dict]] = {}
    for r in countable:
        a = r["attribution"]
        models.setdefault((a["provider"], a["model"], a["version"]), []).append(r)
    best = {}
    for key, rs in models.items():
        best[key] = max(rs, key=lambda r: (r["summary"]["full_pass"],
                                           r["summary"]["check"], r["recorded_at"]))
    return {"countable": countable, "models": models, "best": best,
            "reached": len(models) >= 3}


def cmd_list(args) -> int:
    bench = bench_root(args.bench)
    ledger = os.path.abspath(args.ledger or os.path.join(bench, RESULTS_NAME))
    recs = load_records(ledger)
    if args.json:
        print(json.dumps(recs, ensure_ascii=False, indent=2))
        return 0
    if not recs:
        print(f"账本还是空的：{ledger}")
        print("  先 prepare 一次、交给模型、再 score —— 每次 score 追加一行。")
        return 0
    show = recs[-args.limit:] if args.limit else recs
    print(f"账本 {ledger}（{len(recs)} 行，显示最近 {len(show)} 行）")
    print()
    #: 中文是双宽字符：不按显示宽度补空格，表格就会一路歪（`<` 只数**字符**）。
    widths = (16, 30, 26, 7, 9, 7, 7)
    head = ("  " + pad("时间", widths[0]) + " " + pad("运行", widths[1]) + " "
            + pad("模型", widths[2]) + " " + pad("check", widths[3], "right") + " "
            + pad("语义", widths[4], "right") + " " + pad("κ", widths[5], "right") + " "
            + pad("通过", widths[6], "right") + "   计入")
    print(head)
    print("  " + "-" * (sum(widths) + 6))
    for r in show:
        a, s = r["attribution"], r["summary"]
        sem = f"{s['sem_ok']}/{s['sem_n']}"
        flag = "✓" if r.get("kpi_countable") else "—"
        print("  " + pad(r["recorded_at"][:16].replace("T", " "), widths[0]) + " "
              + pad(str(r.get("run_id")), widths[1]) + " "
              + pad(str(a.get("model")), widths[2]) + " "
              + pad(f"{s['check']}/{s['total']}", widths[3], "right") + " "
              + pad(sem, widths[4], "right") + " "
              + pad(f"{s['kappa']}/{s['total']}", widths[5], "right") + " "
              + pad(f"{s['full_pass']}/{s['total']}", widths[6], "right")
              + "   " + flag)
    print()
    k = kpi_report(recs)
    print("── KPI③「≥3 模型 × 12 题」──")
    print(f"  可计入运行   {len(k['countable'])} 次")
    print(f"  去重模型     {len(k['models'])} 个（需要 ≥3）")
    if k["models"]:
        print("  模型（provider / model / version → 最好的一次三列同时通过）：")
        for key in sorted(k["models"]):
            b = k["best"][key]
            s = b["summary"]
            print(f"    {key[0]} / {key[1]} / {key[2]} → {s['full_pass']}/{s['total']}"
                  f"  [{b.get('run_id')}]")
    skipped: Dict[str, int] = {}
    for r in recs:
        if r.get("kpi_countable"):
            continue
        for b in (r.get("kpi_blockers") or ["未标注"]):
            key = b.split("（")[0]
            skipped[key] = skipped.get(key, 0) + 1
    if skipped:
        print("  未计入：" + "；".join(f"{k2} ×{v2}" for k2, v2 in sorted(skipped.items())))
    print()
    if k["reached"]:
        print(f"  判定   ✓ 达成（{len(k['models'])} 个模型有干净的 12 题运行）")
    else:
        need = 3 - len(k["models"])
        print(f"  判定   ✗ 未达成：还差 {need} 个模型（现 {len(k['models'])} 个可计入）")
        print("  还差的那一步不是代码：开新会话、只给 prepare 出来的目录、"
              "写完 score 一次（见 tools/ai_run.py prepare 打印的下一步）。")
    return 0


# ===================================================================== CLI

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="ai_run.py",
        description="KPI③「多模型 × 12 题」的运行账本：prepare / score / freeze / list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="元数据缺一个就不打分；材料变了就报错；未交永远不算通过。",
    )
    ap.add_argument("--bench", default=None,
                    help=f"实验目录（默认 {BENCH_DEFAULT}，或环境变量 {BENCH_ENV}）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def meta_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--provider", default=None, help="谁提供的模型，如 deepseek / openai")
        p.add_argument("--model", default=None, help="模型名，如 deepseek-v4.1-flash")
        p.add_argument("--version", default=None, help="模型版本/快照，如 2026-11-13")
        p.add_argument("--temperature", default=None,
                       help=f"温度（0 是合法值）；真没记录就写 {UNKNOWN}")
        p.add_argument("--legacy-reason", default=None,
                       help=f"把某项写成 {UNKNOWN} 的理由（必须，且该运行不计入 KPI）")

    f = sub.add_parser("freeze", help="打印/校验材料的 sha256 清单")
    f.add_argument("--write", action="store_true",
                   help="把当前材料冻成一个新 epoch（材料真的变了才用）")
    f.set_defaults(func=cmd_freeze)

    p = sub.add_parser("prepare", help="建隔离运行目录（材料 + 12 题，无 参考.eln）")
    meta_args(p)
    p.add_argument("--run-id", default=None, help="运行 id（默认 时间戳-模型名）")
    p.add_argument("--out", default=None, help=f"输出目录（默认 <bench>/{RUNS_DIR}/<run-id>）")
    p.add_argument("--no-cases", action="store_true",
                   help="不把 cases.json 发给 AI（协议变了：与历史不可比，会记进元数据）")
    p.add_argument("--no-prompt-file", action="store_true", help="不写提示词文件")
    p.add_argument("--note", default=None, help="备注（写进元数据）")
    p.set_defaults(func=cmd_prepare)

    s = sub.add_parser("score", help="给一个提交打分：三栏 + 元数据 → JSON + 账本")
    meta_args(s)
    s.add_argument("--run", default=None, help="prepare 出来的运行目录（元数据从里面读）")
    s.add_argument("--sub", default=None, help=f"提交目录/文件（默认 <run>/{SUBMIT_DIR}）")
    s.add_argument("--contamination", default=None, choices=CONTAMINATIONS,
                   help="干净度声明（默认：运行目录=none，手工提交=unknown）")
    s.add_argument("--run-id", default=None, help="这次运行的 id（默认取运行目录的）")
    s.add_argument("--json", default=None, help=f"完整记录写到哪（默认 <bench>/{RUNS_DIR}/结果-<id>.json）")
    s.add_argument("--ledger", default=None, help=f"账本路径（默认 <bench>/{RESULTS_NAME}）")
    s.add_argument("--no-record", action="store_true", help="不追加账本（只在屏幕上/JSON 里看）")
    s.add_argument("--print-json", action="store_true", help="把摘要 JSON 也打到屏幕上")
    s.add_argument("--allow-empty", action="store_true",
                   help="允许「一题都没交」的记录（默认拒绝：那是没跑，不是 0 分）")
    s.add_argument("--note", default=None, help="备注（写进记录）")
    s.set_defaults(func=cmd_score)

    l = sub.add_parser("list", help="列出账本里的所有运行 + KPI③ 判定")
    l.add_argument("--limit", type=int, default=0, help="只显示最近 N 条（默认全部）")
    l.add_argument("--ledger", default=None, help="账本路径")
    l.add_argument("--json", action="store_true", help="输出原始账本 JSON")
    l.set_defaults(func=cmd_list)
    return ap


def main(argv: List[str]) -> int:
    ap = build_parser()
    args = ap.parse_args(argv[1:])
    try:
        return args.func(args)
    except Refusal as e:
        eprint(f"✗ {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
