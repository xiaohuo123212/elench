#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""core_api.py —— 「检查」与「算 κ」的**进程内**入口（不再 fork 一个 python）。

## 为什么要有这一层（2026-11-18）

以前要把 Elench 包成工具，只有一条路：`subprocess` 起一个 `dsh.py` 子进程。
这条路有两个问题：

1. **慢**：每次调用都要付一次解释器启动（约 100 ms），做网页/工具时不能接受；
2. **在浏览器里根本不存在**：Pyodide（WASM 里的 CPython）没有 `subprocess`。

而这两件事都不需要子进程——`dsh.py` 的 `subprocess` 全部是**函数内惰性 import**
（3813 / 3888 / 5055 / 5311 行），模块级只用到 `io/json/os/re/threading`。
所以只要把"调 CLI"换成"在同一个进程里调 CLI 函数"，浏览器里也能跑。

## 这一层刻意**不重写**任何逻辑

`check` / `cost` 的 JSON 结构是 `cmd_check` / `cmd_cost` 里四十多行拼出来的
（判决、分配、explanation、可选的告警键……）。照抄一份出来，两份迟早不一样。
所以这里就**调那两个函数**，把它们打到 stdout 的 JSON 截下来：

    with redirect_stdout(buf):
        rc = dsh.main(["dsh", "check", <tu>, <eln>, "--json"])

`--json` 是机读模式，`cmd_cost` 里有一句注释明确保证"机读模式下不许有人的话"，
所以截下来的 stdout 就是纯 JSON，不用做任何清洗。

**代价**：依赖"`--json` 只输出 JSON"这条约定。所以下面有 `selftest()`：
一旦哪天有人在机读模式下加了半句话，这里会立刻炸，而不是悄悄返回半截数据。

## 用法

```python
import core_api
r = core_api.check(tu_text, eln_text)      # → dict，与 `elench check --json` 完全相同
c = core_api.cost(tu_text, eln_text)       # → dict，与 `elench cost --json` 完全相同
```
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Dict, Optional

#: 临时文件名固定成 probe：平台侧不关心模块名，固定住能让输出稳定可比。
MODULE = "probe"

#: 字段上限（与 tools/elench_tool.py 一致；这一层不重复做限额判断，只做兜底）。
MAX_TEXT_BYTES = 256 * 1024


class CoreError(Exception):
    """调用方该看到的错误（不是 traceback）。"""


def _write(tmp: str, name: str, text: str) -> Optional[str]:
    if not (text or "").strip():
        return None
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise CoreError("%s 超过上限 %d 字节" % (name, MAX_TEXT_BYTES))
    p = os.path.join(tmp, name)
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return p


def _call(argv) -> Dict[str, Any]:
    """在**本进程**里调 `dsh.main(argv)`，把 stdout 的 JSON 解析出来。"""
    import dsh  # 延迟 import：让本模块在只需要类型检查的场景下也能被 import

    buf = io.StringIO()
    err = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(err):
            rc = dsh.main(list(argv))
    except SystemExit as e:                      # dsh.main 正常是 return，防御性兜一下
        rc = int(getattr(e, "code", 1) or 0)
    out = buf.getvalue()
    if not out.strip():
        raise CoreError("%s 没有输出（rc=%d）" % (" ".join(argv[1:2]), rc))
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        # 这正是"机读模式被塞了人的话"的那种回归——必须当成错误大声报出来
        raise CoreError("机读输出不是纯 JSON（rc=%d）：%r" % (rc, out.strip()[:200]))
    payload["_rc"] = rc
    # stderr 只在非空时带出去：调用方（工具/网页）要能说清"查出来哪里有问题"，
    # 但不该让 JSON 形状因为多一个空字段而变化。
    tail = (err.getvalue() or "").strip()
    if tail:
        payload["_stderr"] = tail
    return payload


def _pair(tu_text: str, eln_text: str):
    """把两段源码落到临时目录，返回 (tu_path, eln_path, tmpdir)。"""
    tmp = tempfile.mkdtemp(prefix="elench_core_")
    try:
        tu_p = _write(tmp, MODULE + ".tu", tu_text)
        eln_p = _write(tmp, MODULE + ".eln", eln_text)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if tu_p is None and eln_p is None:
        shutil.rmtree(tmp, ignore_errors=True)
        raise CoreError("tu 与 eln 至少要给一个")
    return tu_p, eln_p, tmp


def check(tu_text: str = "", eln_text: str = "", strict: bool = False,
          compat: bool = False) -> Dict[str, Any]:
    """静态检查。返回的 dict 与 `elench check <tu> <eln> --json` 逐字段相同。

    `compat=True` 走 C++ 兼容层（`--compat`）：`.cpp` **原样改名成 `.eln`** 就能查，
    `using` / `typedef` / `auto` / `std::` / `void` / 多声明符等十种写法会被接受。
    """
    tu_p, eln_p, tmp = _pair(tu_text, eln_text)
    try:
        argv = ["dsh", "check"] + [p for p in (tu_p, eln_p) if p] + ["--json"]
        if strict:
            argv.append("--strict")
        if compat:
            argv.append("--compat")
        return _call(argv)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cost(tu_text: str = "", eln_text: str = "", compat: bool = False) -> Dict[str, Any]:
    """算 κ 并与 target 比。返回的 dict 与 `elench cost <tu> <eln> --json` 逐字段相同。

    **没有 `.tu` 也能算**（只是判决会是"未声明契约"）——所以"把 .cpp 拷过来看它多贵"
    不需要先写契约：`cost("", cpp_source, compat=True)`。
    """
    tu_p, eln_p, tmp = _pair(tu_text, eln_text)
    try:
        argv = ["dsh", "cost"] + [p for p in (tu_p, eln_p) if p] + ["--json"]
        if compat:
            argv.append("--compat")
        return _call(argv)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def handle(req: Dict[str, Any]) -> Dict[str, Any]:
    """与 `tools/elench_tool.py` 的 `op` 约定对齐：{"op": "check"|"cost", "tu", "eln"}。"""
    op = (req or {}).get("op") or "check"
    compat = bool(req.get("compat"))
    if op == "check":
        return check(req.get("tu") or "", req.get("eln") or "",
                     strict=bool(req.get("strict")), compat=compat)
    if op == "cost":
        return cost(req.get("tu") or "", req.get("eln") or "", compat=compat)
    raise CoreError("不支持的 op=%r（只有 check / cost）" % op)


# ---------------------------------------------------------------- 自检


def selftest() -> int:
    """跟子进程那条老路对一遍：同样的输入必须得到同样的 JSON。

    这是本模块唯一的正确性依据 —— 它变了就说明 `--json` 的输出形状变了，
    或者我们在机读模式里加了人的话。
    """
    import subprocess
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)

    TU = ("spec f(const vector<u64>& xs) -> u64 {\n    effects {};\n"
          "    target { time: { max: O(n) }, allocation: { max: 0 }, };\n}\n")
    ELN_OK = ("u64 f(const vector<u64>& xs) {\n    u64 s = 0;\n"
              "    for (auto x : xs) { s = s + x; }\n    return s;\n}\n")
    ELN_DEBT = ("u64 f(const vector<u64>& xs) {\n    u64 s = 0;\n"
                "    for (auto x : xs) { for (auto y : xs) { s = s + y; } }\n"
                "    return s;\n}\n")

    cases = [("cost", TU, ELN_OK), ("cost", TU, ELN_DEBT),
             ("check", TU, "u64 f( {\n}\n"), ("check", "", ELN_OK)]
    fails = 0
    for op, tu, eln in cases:
        got = (cost(tu, eln) if op == "cost" else check(tu, eln))
        got.pop("_rc", None)
        # 子进程那条路
        tmp = tempfile.mkdtemp(prefix="elench_diff_")
        try:
            args = [sys.executable, "-B", os.path.join(root, "src", "dsh.py"), op]
            if tu.strip():
                p = os.path.join(tmp, MODULE + ".tu")
                io.open(p, "w", encoding="utf-8", newline="").write(tu)
                args.append(p)
            if eln.strip():
                p = os.path.join(tmp, MODULE + ".eln")
                io.open(p, "w", encoding="utf-8", newline="").write(eln)
                args.append(p)
            args.append("--json")
            r = subprocess.run(args, capture_output=True, text=True,
                               encoding="utf-8", timeout=120)
            want = json.loads(r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if got != want:
            fails += 1
            print("  FAIL %s（tu=%s eln=%s）与子进程输出不一致"
                  % (op, "有" if tu.strip() else "无", "有" if eln.strip() else "无"))

    total = len(cases)
    if fails:
        print("  %d / %d 不一致" % (fails, total))
        return 1
    print("  ok  %d 个用例：进程内输出与子进程**逐字段相同**" % total)
    return 0


if __name__ == "__main__":
    import sys

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(selftest())
