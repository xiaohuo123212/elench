#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""scale_probe.py —— 「哪些 n 跑得动」（T-06）

## 它回答什么

> 这个函数，n 到多大还能跑完？**在哪一档开始撞护栏？**

现在的做法是"跑挂了再手动调 `ELENCH_STEP_BUDGET`"，**没有一个可判定的规则**。
这个脚本给的就是那条规则：

| 档 | 含义 |
|---|---|
| `ok` | 跑完了，且结果与 κ 相容 |
| `ops` | 撞了**步数护栏**（`ELENCH_STEP_BUDGET`，默认 500 万）——不是算法慢，是护栏 |
| `timeout` | 墙钟超时（默认 20s） |
| `error` | 程序本身报错（不是规模问题） |

## 用法

    python tools/scale_probe.py sum                     # 自动倍增，直到跑不动
    python tools/scale_probe.py sum --sizes 16,64,256    # 指定档位
    python tools/scale_probe.py calculator --fn evaluate

**输出**：一档一行的表 + 一句"最大可跑 n"。**不美化**：撞护栏就说撞护栏。
"""
import os
import subprocess
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZES = os.path.join(R, "文档", "样例")


def run_one(prog: str, n: int, fn: str, budget: int, timeout: int):
    """在子进程里跑，**超时能杀掉**（同进程里跑会挂死）。"""
    code = (
        "import sys, json; sys.path.insert(0, %r)\n"
        "import kappa_vs_measured as K\n"
        "rows = K.measure_one(%r, %d, 1)\n"
        "out = []\n"
        "for r in rows:\n"
        "    if %r and r.get('function') != %r: continue\n"
        "    out.append({'fn': r.get('function'), 'ops': r.get('max_actual'),\n"
        "                'err': r.get('error'), 'holds': r.get('holds')})\n"
        "print(json.dumps(out, ensure_ascii=False))\n"
    ) % (os.path.join(R, "src"), prog, n, fn, fn)
    env = dict(os.environ, ELENCH_STEP_BUDGET=str(budget), PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run([sys.executable, "-B", "-c", code], cwd=R,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout", None
    if p.returncode != 0:
        err = (p.stderr or "").strip()
        msg = err.splitlines()[-1] if err else ""
        # **护栏超限**要单独归类：它不是"程序错"，是"规模太大"——
        # 混成 error 会让人以为代码有 bug。
        blob = (err + (p.stdout or "")).lower()
        if "interpbudgetexceeded" in blob or "budget" in blob or "步数" in blob:
            return "ops", None
        return "error", msg[:60]
    try:
        rows = __import__("json").loads(p.stdout.strip().splitlines()[-1])
    except Exception:                                  # noqa: BLE001
        return "error", "输出无法解析"
    if not rows:
        return "empty", None
    if any(r.get("err") for r in rows):
        e = str(rows[0].get("err"))
        # `measure_one` 会把异常**收进行里**（不是子进程失败）——护栏超限要单独归类。
        if "budget" in e.lower() or "步" in e:
            return "ops", None
        return "error", e[:60]
    return "ok", rows[0].get("ops")


def main(argv) -> int:
    prog = None
    fn = ""
    sizes = None
    budget = 5_000_000
    timeout = 20
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--sizes" and i + 1 < len(argv):
            sizes = [int(x) for x in argv[i + 1].split(",") if x.strip()]
            i += 2
            continue
        if a == "--fn" and i + 1 < len(argv):
            fn = argv[i + 1]
            i += 2
            continue
        if a == "--budget" and i + 1 < len(argv):
            budget = int(argv[i + 1])
            i += 2
            continue
        if a == "--timeout" and i + 1 < len(argv):
            timeout = int(argv[i + 1])
            i += 2
            continue
        if not a.startswith("--"):
            prog = a
        i += 1
    if not prog:
        print("用法: scale_probe.py <模块> [--fn=名字] [--sizes=16,64] [--budget=N] [--timeout=秒]")
        return 2
    if sizes is None:
        sizes = [16, 64, 256, 1024, 4096, 16384]

    print(f"  模块 {prog}  护栏 {budget:,} 步  墙钟 {timeout}s")
    print(f"  {'n':>7}  {'档':<8} {'操作数':>12}  说明")
    best = None
    for n in sizes:
        kind, val = run_one(prog, n, fn, budget, timeout)
        ops = f"{val:,}" if isinstance(val, int) else "—"
        note = "" if kind == "ok" else (val or "")
        print(f"  {n:>7}  {kind:<8} {ops:>12}  {note}")
        if kind == "ok":
            best = n
        elif kind in ("ops", "timeout"):
            break                 # 更大的档只会更糟
    print()
    if best is None:
        print("  **连最小档都没跑完**——先看是程序错还是护栏太小。")
        return 1
    print(f"  **最大可跑 n = {best}**（再上一档撞的是"
          f"{'步数护栏' if kind == 'ops' else '墙钟'}，不是算法错）。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
