#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""probe_real_libs.py —— 用**同一份输入**重跑真库探针，产出可对比的结论。

## 它是什么 / 不是什么

`build/real-libs/` 下是 2026-11 早期 pull 下来的真实第三方库（cJSON / doctest / fmt /
inih / nlohmann-json / stb / tinyexpr / utf8h / xxHash），以及用它们写的探针
（`_probe/eln/*.eln` 37 个、`_cases/*.eln` 4 个）。

这个脚本**只做一件事**：对每个探针跑 `check --compat`（可选 `cost`），把结论记下来，
让"当时 vs 现在"能逐条比。**它不修改任何探针**——输入是证据，不许动。

**它不能替代人工判断**：`check` 通过不等于"这个库能用"，只等于"这一份探针的
语法 / 类型 / 契约检查过了"。所以输出里只有"通过 / 诊断码"，没有"支持了这个库"。

## 用法

```bash
python -B tools/probe_real_libs.py            # 人读
python -B tools/probe_real_libs.py --json     # 机读
python -B tools/probe_real_libs.py --cost     # 顺便跑 cost（慢一些）
```

`build/` 是 gitignored：材料不在时报**明确的缺失**并退出 2，不假装跑过。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DSH = os.path.join(ROOT, "src", "dsh.py")
BASE = os.path.join(ROOT, "build", "real-libs")
PROBE = os.path.join(BASE, "_probe", "eln")
CASES = os.path.join(BASE, "_cases")

#: 有历史日志的探针（`_cases/logs/*_check.log`）——对比时用得上
HAS_LOG = {"stb", "stb2", "utf8", "xxh"}


def probes():
    out = []
    if os.path.isdir(PROBE):
        out += [(n, os.path.join(PROBE, n)) for n in sorted(os.listdir(PROBE))
                if n.endswith(".eln")]
    if os.path.isdir(CASES):
        out += [(n, os.path.join(CASES, n)) for n in sorted(os.listdir(CASES))
                if n.endswith(".eln")]
    # **`_cases/eln/`**：有历史日志（`_cases/logs/*_check.log`）的那 4 个探针就在这里，
    # 名字是 `utf8.eln` / `xxh.eln` / `stb.eln` / `stb2.eln`。早期版本的脚本漏了这一层，
    # 于是"当时 vs 现在"拿不到真正同一份输入——这是 harness 的错，不是代码的错。
    sub = os.path.join(CASES, "eln")
    if os.path.isdir(sub):
        out += [("eln/" + n, os.path.join(sub, n)) for n in sorted(os.listdir(sub))
                if n.endswith(".eln")]
    return out


#: `--ffi` 的开关（进程内状态，避免全局到处传）
_FFI_MODE = [False]


def include_dirs():
    """把 9 个库的 `include/` 与库根目录当搜索路径。

    真库的头是 `#include <nlohmann/json.hpp>` 这种**尖括号**形式，等价于编译时
    的 `-I`。不设的话它们会报 `E-Lex-IncludeUnresolved`——那是**缺搜索路径**，
    不是"吃不下这个头"。两种口径都报出来，别把环境问题算成语言问题。
    """
    dirs = []
    if os.path.isdir(BASE):
        for lib in sorted(os.listdir(BASE)):
            d = os.path.join(BASE, lib)
            if not os.path.isdir(d):
                continue
            inc = os.path.join(d, "include")
            if os.path.isdir(inc):
                dirs.append(inc)
            dirs.append(d)
    return dirs


def run(args):
    env = dict(os.environ)
    if not env.get("PROBE_NO_INCLUDE_DIRS"):
        env["ELENCH_INCLUDE_DIRS"] = os.pathsep.join(include_dirs())
    if _FFI_MODE[0]:
        # **FFI 口径**：`extern … from "lib"` + `link "lib"` 的 `.eln` 里，`#include`
        # 只给 C 侧 shim 用。当年那批探针的 exit=0 就是在这个口径下得到的（见
        # 文档/真库探针.md §4.11），所以要比"当时 vs 现在"就必须用同一个口径。
        env["ELENCH_NO_INCLUDE_INLINE"] = "1"
    else:
        env.pop("ELENCH_NO_INCLUDE_INLINE", None)
    r = subprocess.run([sys.executable, "-B", DSH] + args, cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def verdict(path: str, with_cost: bool) -> dict:
    rc, out = run(["check", path, "--compat"])
    rec = {
        "ok": rc == 0,
        "codes": sorted(set(re.findall(r"E-[A-Za-z-]+", out))),
        "warn_codes": sorted(set(re.findall(r"[IW]-[A-Za-z-]+", out))),
    }
    if with_cost and rc == 0:
        rc2, out2 = run(["cost", path, "--compat"])
        rec["cost_ok"] = rc2 == 0
        rec["cost_codes"] = sorted(set(re.findall(r"E-[A-Za-z-]+", out2)))
    return rec


def _main_path(args) -> int:
    """探任意目录下的 `*.cpp`：复制成 `.eln`（**原件一个字不动**）再跑。"""
    import shutil
    import tempfile
    src_dir = args.path if os.path.isabs(args.path) else os.path.join(ROOT, args.path)
    if not os.path.isdir(src_dir):
        print("✗ 目录不存在：%s" % src_dir, file=sys.stderr)
        return 2
    tmp = tempfile.mkdtemp(prefix="probe_path_")
    rows = []
    # 两种布局都认：`dir/x.cpp` 与 `dir/x/main.cpp`（`demo/probe-libs/` 是后者）。
    cand = []
    for n in sorted(os.listdir(src_dir)):
        full = os.path.join(src_dir, n)
        if n.endswith(".cpp"):
            cand.append((n[:-4], full))
        elif os.path.isdir(full) and os.path.isfile(os.path.join(full, "main.cpp")):
            cand.append((n, os.path.join(full, "main.cpp")))
    for name, f in cand:
        dst = os.path.join(tmp, name + ".eln")
        shutil.copy2(f, dst)
        rc, out = run_abs(["check", dst, "--compat"])
        rows.append({"name": name + ".eln", "ok": rc == 0,
                     "codes": sorted(set(re.findall(r"E-[A-Za-z-]+", out)))})
    ok = sum(1 for r in rows if r["ok"])
    print("  目录：%s（%d 个 .cpp）" % (os.path.relpath(src_dir, ROOT), len(rows)))
    print("  " + "-" * 74)
    for r in rows:
        print("  %s %-18s %s" % ("✓" if r["ok"] else "✗", r["name"], ", ".join(r["codes"])[:40]))
    print("  " + "-" * 74)
    print("  口径：%s" % ("FFI（不内联 include）" if _FFI_MODE[0] else "移植（内联 include）"))
    print("  check --compat 通过：%d/%d" % (ok, len(rows)))
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


def run_abs(args):
    env = dict(os.environ)
    if _FFI_MODE[0]:
        env["ELENCH_NO_INCLUDE_INLINE"] = "1"
    r = subprocess.run([sys.executable, "-B", DSH] + args, cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="probe_real_libs", description="重跑真库探针")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--cost", action="store_true", help="顺便跑 cost")
    ap.add_argument("--path", metavar="DIR",
                    help="改为探这个目录下的 *.cpp（如 demo/probe-libs），"
                         "复制成 .eln 后跑，原件不动")
    ap.add_argument("--ffi", action="store_true",
                    help="FFI 口径：不内联 include（与当年日志同一口径）")
    ap.add_argument("--no-include-dirs", action="store_true",
                    help="不设 ELENCH_INCLUDE_DIRS（看缺搜索路径时是什么样）")
    args = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    _FFI_MODE[0] = bool(args.ffi)
    if args.no_include_dirs:
        os.environ["PROBE_NO_INCLUDE_DIRS"] = "1"
    if args.path:
        return _main_path(args)
    if not os.path.isdir(BASE):
        print("✗ 找不到 %s" % BASE, file=sys.stderr)
        print("  build/ 是 gitignored：这份材料只存在于做过探针的那台机器上。",
              file=sys.stderr)
        print("  没有它就无法重跑——不假装跑过。", file=sys.stderr)
        return 2

    items = probes()
    if not items:
        print("✗ %s 下没有 .eln 探针" % BASE, file=sys.stderr)
        return 2

    rows = []
    for name, path in items:
        rec = verdict(path, args.cost)
        rec["name"] = name
        rows.append(rec)

    if args.json:
        print(json.dumps({"base": BASE, "count": len(rows), "rows": rows},
                         ensure_ascii=False, indent=1))
        return 0

    ok = [r for r in rows if r["ok"]]
    print("  真库探针：%d 个（%s）" % (len(rows), os.path.relpath(BASE, ROOT)))
    print("  " + "-" * 74)
    for r in rows:
        mark = "✓" if r["ok"] else "✗"
        codes = ", ".join(r["codes"])[:40]
        flag = "  ← 有历史日志" if r["name"].split("_")[0] in HAS_LOG or r["name"] in HAS_LOG else ""
        print("  %s %-28s %s%s" % (mark, r["name"], codes, flag))
    print("  " + "-" * 74)
    print("  口径：%s" % ("FFI（不内联 include）" if _FFI_MODE[0] else "移植（内联 include）"))
    print("  check --compat 通过：%d/%d" % (len(ok), len(rows)))
    if args.cost:
        cok = [r for r in rows if r.get("cost_ok")]
        print("  cost  --compat 通过：%d/%d（仅对 check 通过的跑）" % (len(cok), len(ok)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
