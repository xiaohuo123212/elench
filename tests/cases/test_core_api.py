#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_core_api.py —— `src/core_api.py` 必须与 CLI **逐字段相同**，且在 WASM 里能跑。

## 为什么要有这条测试

`core_api` 现在是三条路共用的核心：MCP 工具、平台组件背后的工作流、以及（计划中的）
浏览器里的 Pyodide 网页。它一旦和 CLI 漂了，**三个壳会同时给出错的数字**，
而且不会有任何一处报错——只会"看起来能跑"。

所以钉两件事：

1. **等价**：同一段源码，`core_api` 在进程内算出来的 JSON，必须与
   `python -B src/dsh.py <op> … --json` 子进程那条路逐字段相同（这条是硬线）。
2. **可在浏览器里跑**：把 `subprocess` / `ctypes` / `socket` / `ssl` 等**禁止 import**
   再跑一遍完整的 check + cost。这比真实 Pyodide 更严（Pyodide 其实有 ctypes 的壳），
   能过就说明核心路径不依赖任何宿主能力。

> 第 2 条是 2026-11-18 加的：当时发现 `cost` 会因为
> `cost_interp → interp → ffi_c → import ctypes` 在 WASM 里直接 ImportError。
> 修法是把 `ctypes` 变成"可用就用、没有就退化"，并在真调 FFI 时给一条清楚的话。
"""
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")

ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

TU = """spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(n) }, allocation: { max: 0 }, };
}
"""
ELN_OK = """u64 f(const vector<u64>& xs) {
    u64 s = 0;
    for (auto x : xs) { s = s + x; }
    return s;
}
"""
ELN_DEBT = """u64 f(const vector<u64>& xs) {
    u64 s = 0;
    for (auto x : xs) { for (auto y : xs) { s = s + y; } }
    return s;
}
"""
ELN_SYNTAX = "u64 f( {\n    return\n}\n"


def main() -> int:
    fails = []

    def check(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    # ---------- 1. 等价：core_api vs CLI 子进程 ----------
    import tempfile
    import shutil

    sys.path.insert(0, SRC)
    import core_api

    cases = [("cost", TU, ELN_OK), ("cost", TU, ELN_DEBT),
             ("check", TU, ELN_SYNTAX), ("check", "", ELN_OK)]
    for op, tu, eln in cases:
        got = core_api.cost(tu, eln) if op == "cost" else core_api.check(tu, eln)
        got.pop("_rc", None)
        got.pop("_stderr", None)

        tmp = tempfile.mkdtemp(prefix="elench_t_")
        try:
            args = [sys.executable, "-B", os.path.join(SRC, "dsh.py"), op]
            if tu.strip():
                p = os.path.join(tmp, "probe.tu")
                open(p, "w", encoding="utf-8", newline="").write(tu)
                args.append(p)
            if eln.strip():
                p = os.path.join(tmp, "probe.eln")
                open(p, "w", encoding="utf-8", newline="").write(eln)
                args.append(p)
            args.append("--json")
            r = subprocess.run(args, capture_output=True, text=True,
                               encoding="utf-8", env=ENV, timeout=180)
            want = json.loads(r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        same = got == want
        check(same, "%s（tu=%s eln=%s）进程内 == 子进程"
              % (op, "有" if tu.strip() else "无", "有" if eln.strip() else "无"))

    # ---------- 2. 能在"没有宿主能力"的环境里跑 ----------
    # 子进程里装一个 import 拦截器，再跑完整的 check + cost。
    probe = r'''
import sys, os, json, importlib.abc
BLOCK = {"subprocess", "ctypes", "_ctypes", "socket", "_socket",
         "multiprocessing", "ssl", "signal", "fcntl"}
class B(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCK:
            raise ImportError("blocked: " + name)
        return None
sys.meta_path.insert(0, B())
sys.path.insert(0, %r)
import core_api
TU = %r
OK = %r
BAD = %r
c = core_api.cost(TU, OK)
d = core_api.cost(TU, BAD)
k = core_api.check(TU, "u64 f( {\n}\n")
print(json.dumps({
    "ok_verdict": c["functions"]["f"]["verdict"],
    "ok_kappa": c["functions"]["f"]["cost_expression"],
    "debt_verdict": d["functions"]["f"]["verdict"],
    "syntax_ok": k.get("ok"),
    "diag": [x["code"] for x in (k.get("diagnostics") or [])][:1],
}, ensure_ascii=False))
''' % (SRC, TU, ELN_OK, ELN_DEBT)

    r = subprocess.run([sys.executable, "-B", "-c", probe], capture_output=True,
                       text=True, encoding="utf-8", env=ENV, timeout=300)
    if r.returncode != 0:
        tail = (r.stderr or "") .strip().splitlines()[-3:]
        check(False, "欢迎在无 subprocess/ctypes 的环境里跑（%s）" % " / ".join(tail))
    else:
        d = json.loads(r.stdout)
        check(d["ok_verdict"] == "ok", "无宿主能力时：合规代码仍判 ok")
        check(d["ok_kappa"] == "2 + n(xs)×7 + 2", "无宿主能力时：κ 仍算得对（%s）" % d["ok_kappa"])
        check(d["debt_verdict"] == "debt", "无宿主能力时：违约代码仍判 debt")
        check(d["syntax_ok"] is False and d["diag"] and d["diag"][0].startswith("E-Parse"),
              "无宿主能力时：语法错仍给出诊断码")

    if fails:
        print("")
        print("%d 项失败" % len(fails))
        return 1
    print("")
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
