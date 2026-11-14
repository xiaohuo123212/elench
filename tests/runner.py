# -*- coding: utf-8 -*-
"""统一测试入口：**先集中，再谈覆盖**。

## 为什么要有这个文件

测试原来散在 `src/` 里（67 个文件、**65 份各写一份的 `check()`**、**5 组重名函数**，
还有 4 个长期失败但没人知道是哪 4 个）。现在它们都在 `tests/cases/`，
由这一条命令给出**一个**结论。原来那种状态意味着：

- 说「全套通过」时，**没有任何一个地方**能给出这个结论；
- κ 的 soundness 检测器要报「覆盖了哪些构造子」时，**没有统一的词汇表**可用；
- 机器抓到的反例**没有固定的落点**，下次就跑不到了。

**已完成**：① 物理搬家到 `tests/cases/`；② 一个入口、一个退出码、已知失败显式登记。
**下一步**：共用 `check()`（现在有 4 种不同签名）、`tests/corpus/`（反例语料）、
`tests/fuzz/`（生成器，见 `文档/待办.md` G 节）。

## 用法

    python tests/runner.py                 # 全跑，并行
    python tests/runner.py --tag kappa     # 只跑 κ 相关
    python tests/runner.py --list          # 列出来，不跑
    python tests/runner.py --json out.json # 机器可读
    python tests/runner.py -j 1            # 串行（调试用）

退出码：0 = 没有**意外**失败；1 = 有意外失败（`xfail` 不算，除非 `--strict-xfail`）。
"""

import argparse
import concurrent.futures as cf
import io
import json
import os
import re
import subprocess
import sys
import time

#: 中文 Windows 的控制台默认 GBK，打印 `✓` / `✗` 会 `UnicodeEncodeError`
#: 直接死在 **汇总之前**（一行结果都看不到）。测试子进程已设 UTF-8，
#: 父进程也必须自己配一次。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
CASES = os.path.join(HERE, "cases")

#: **已知失败**：不是「应该失败」，是「目前失败、且与本次改动无关」。
#: 每次跑都会重新验证：如果它**通过**了，会报 `xpass`，提醒把它从这里删掉。
KNOWN_FAIL = {
    "test_assume": "报告里「假设：n ≤ …」那一行没打出来（历史遗留，未修）",
}
# 2026-11-05：`test_dispatch_consistency` 与 `test_invalid` 已通过（xpass 连续两次），
# 按规矩从上面删掉——**登记表只放"现在真的红"的**。

#: 文件名 → 标签。**这就是「统一词汇表」的第一版**：检测器的覆盖表将来用同一套标签。
TAGS = {
    "test_cost": ["kappa"], "test_ops": ["kappa", "ops"],
    "test_kappa_gaps": ["kappa"], "test_kappa_matmul": ["kappa"],
    "test_call_args": ["kappa"], "test_recursion_bound": ["kappa", "checker"],
    "test_symbolic": ["kappa"], "test_units": ["kappa"],
    "test_alloc": ["kappa"], "test_assume": ["kappa"],
    "test_otel": ["otel"], "test_otel_export": ["otel"],
    "test_otel_dashboard": ["otel"], "test_otel_trace_hooks": ["otel"],
    "test_parser": ["parser"], "test_lexer": ["parser"],
    "test_formatter": ["parser"], "test_fmt_layout": ["parser"],
    "test_highlight": ["parser"],
    "test_checker": ["checker"], "test_typecheck": ["checker"],
    "test_borrow": ["checker"], "test_borrow_diff": ["checker"],
    "test_effects": ["checker"], "test_invalid": ["checker"],
    "test_interp": ["interp"], "test_interp_extra": ["interp"],
    "test_programs": ["interp"], "test_isolated": ["interp"],
    "test_cli_exit": ["cli"], "test_build": ["cli"], "test_header": ["cli"],
    "test_policy": ["cli"], "test_env": ["cli"],
    "test_dispatch_correct": ["dispatch"], "test_dispatch_runtime": ["dispatch"],
    "test_dispatch_closure": ["dispatch"], "test_dispatch_consistency": ["dispatch"],
    "test_stdlib": ["builtins"], "test_builtins": ["builtins"],
    "test_hof": ["builtins"], "test_foreach": ["builtins"],
    "test_sort": ["builtins"], "test_strings": ["builtins"],
    "test_json": ["builtins"], "test_containers": ["builtins"],
    "test_ffi": ["ffi"], "test_parallel": ["parallel"], "test_threads": ["parallel"],
    "test_editors": ["editors"], "test_golden": ["kappa"],
}

_OK_RE = re.compile(r"全部通过|全部过|✓ 通过|全部覆盖")


def discover():
    out = []
    for name in sorted(os.listdir(CASES)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        out.append(os.path.join(CASES, name))
    return out


def tags_of(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem in TAGS:
        return TAGS[stem]
    for prefix, tag in (("test_otel", "otel"), ("test_dispatch", "dispatch"),
                        ("test_kappa", "kappa"), ("test_cost", "kappa")):
        if stem.startswith(prefix):
            return [tag]
    return ["other"]


def run_one(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # 测试现在住在 tests/cases/，`import checker` 靠 PYTHONPATH 找到 src/
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    t0 = time.time()
    p = subprocess.run([sys.executable, path], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    dt = time.time() - t0
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    last = ""
    for line in reversed(out.splitlines()):
        if line.strip():
            last = line.strip()
            break
    if stem in KNOWN_FAIL:
        status = "xfail" if p.returncode != 0 else "xpass"
    else:
        status = "ok" if p.returncode == 0 else "red"
    return {
        "name": stem,
        "status": status,
        "rc": p.returncode,
        "secs": round(dt, 2),
        "last": last[:160],
        "tags": tags_of(path),
        "known": KNOWN_FAIL.get(stem),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--tag", default=None, help="只跑这个标签")
    ap.add_argument("--list", action="store_true", help="只列出来")
    ap.add_argument("--json", default=None, help="把结果写成 JSON")
    ap.add_argument("-j", type=int, default=8, help="并发数（默认 8）")
    ap.add_argument("--strict-xfail", action="store_true",
                    help="已知失败若通过，也判失败")
    args = ap.parse_args(argv)

    files = discover()
    if args.tag:
        files = [f for f in files if args.tag in tags_of(f)]

    if args.list:
        for f in files:
            print(f"  {'·':1} {os.path.basename(f):38} {','.join(tags_of(f))}")
        print(f"\n共 {len(files)} 个文件")
        return 0

    if not files:
        print(f"没有匹配的文件（--tag={args.tag}）", file=sys.stderr)
        return 2

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=max(1, args.j)) as ex:
        rows = list(ex.map(run_one, files))
    total = time.time() - t0

    icon = {"ok": "✓", "red": "✗", "xfail": "◦", "xpass": "!"}
    for r in sorted(rows, key=lambda x: (x["status"] != "red", x["name"])):
        print(f"  {icon.get(r['status'], '?')} {r['name']:34} {r['secs']:6.2f}s  {r['last']}")

    n_ok = sum(1 for r in rows if r["status"] == "ok")
    n_red = sum(1 for r in rows if r["status"] == "red")
    n_xf = sum(1 for r in rows if r["status"] == "xfail")
    n_xp = sum(1 for r in rows if r["status"] == "xpass")
    print(f"\n  {len(rows)} 个文件 / {total:.1f}s："
          f"通过 {n_ok}，**意外失败 {n_red}**，已知失败 {n_xf}，意外通过 {n_xp}")
    if n_xp:
        print("  ！ 下面这些「已知失败」其实通过了，请从 KNOWN_FAIL 里删掉：")
        for r in rows:
            if r["status"] == "xpass":
                print(f"      {r['name']}")

    if args.json:
        with io.open(args.json, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"rows": rows, "secs": round(total, 1)},
                                ensure_ascii=False, indent=2))
        print(f"  → {args.json}")

    if n_red:
        return 1
    if n_xp and args.strict_xfail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
