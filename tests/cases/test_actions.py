#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_actions.py —— 静态检查的 **fix-it**（`actions` / `--apply=N`）

## 它守的是什么

以前诊断只给散文建议（"请在 spec 里加 repeats"），读者还得自己翻译成补丁。
现在带 `actions` 的诊断会被编号列出，`--apply=N` 直接执行。

**三条硬规则**：

1. `patch.text` 里**不许有占位符**（曾经出现过 `<原来的条件>`，真应用就是往文件里
   写这行字）；
2. `--apply` 改的必须是**那一行**（曾经把 spec 行当成了 guarantees 行）；
3. `edit` 类**只打印 diff，不落盘**——改动要人确认。
"""
import json
import os
import re
import subprocess
import sys
import tempfile

#: **占位符**的形状：`<...>` / `<…>` / 尖括号里有中文（`<原来的条件>`）。
#: 不能简单判「含 `<`」——真实签名里的泛型（`Vec<u64>`）和比较式
#: （`while (i < xs.length())`）都有 `<`，那是**代码**不是占位符。
#: 2026-11-11 之前这条断言用 `"<" not in x or "->" in x`，把泛型签名
#: `u64 b(const Vec<u64>& xs) { … }` 误判成占位符（test_actions 红了）。
PLACEHOLDER = re.compile(r"<[^<>]*(?:\.\.\.|…|[\u4e00-\u9fff])[^<>]*>")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
DSH = os.path.join(SRC, "dsh.py")

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(args, cwd=None):
    r = subprocess.run([sys.executable, DSH] + args, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=cwd)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="elench_act_")
    tu = os.path.join(tmp, "a.tu")
    eln = os.path.join(tmp, "a.eln")
    with open(tu, "w", encoding="utf-8", newline="\n") as f:
        f.write("spec a(const Vec<u64>& xs) -> u64 {\n"
                "    effects {};\n"
                "    guarantees sorted(xs);\n"
                "}\n")
    with open(eln, "w", encoding="utf-8", newline="\n") as f:
        f.write("u64 a(const Vec<u64>& xs) { return 1; }\n"
                "u64 b(const Vec<u64>& xs) { return 2; }\n")

    print("[1] 诊断带上可执行的 actions")
    _rc, out = run(["check", os.path.join(tmp, "a"), "--format=json"])
    diags = json.loads(out).get("diagnostics") or []
    acts = [a for d in diags for a in (d.get("actions") or [])]
    check("有 actions", bool(acts), [(d["code"], d.get("actions")) for d in diags])
    check("都是 edit（能落到文件上）", all(a.get("kind") == "edit" for a in acts),
          [a.get("kind") for a in acts])
    texts = [a.get("patch", {}).get("text", "") for a in acts]
    check("patch.text 不为空", all(texts), texts)
    check("**没有占位符**（不许出现 `<...>` / `<原来的条件>`）",
          all(not PLACEHOLDER.search(x) for x in texts), texts)

    print("[2] 修法清单编号，--apply=N 能取到")
    _rc, out = run(["check", os.path.join(tmp, "a")])
    check("有修法清单", "修法" in out, out[:200])
    check("编号 1)", "1)" in out)

    print("[3] --apply=1 改的是**那一行**（不是 spec 行）")
    _rc, out = run(["check", os.path.join(tmp, "a"), "--apply=1"])
    check("指到第 3 行", "a.tu:3" in out, [l for l in out.splitlines() if "：" in l][-3:])
    check("内容是被注释掉的 guarantees 那一行", "已注释" in out and "sorted(xs)" in out, out[-200:])
    check("只打印不落盘", "未落盘" in out)

    print("[4] --apply 越界要报错，不静默")
    _rc, out = run(["check", os.path.join(tmp, "a"), "--apply=99"])
    check("报越界", "越界" in out, out[-120:])

    print("[5] 第二批：槽位 / while 条件 也能给出可应用的修法")
    d2 = os.path.join(tmp, "b")
    os.makedirs(d2, exist_ok=True)
    with open(os.path.join(d2, "b.tu"), "w", encoding="utf-8", newline="\n") as f:
        f.write("spec b(const Vec<u64>& xs) -> u64 {\n"
                "    effects {};\n"
                "    benchmodel {\n"
                "        data { xs = random_u64s(); }\n"
                "        scale { xs in range(1, 4096, *2); }\n"
                "    }\n"
                "    ;\n"
                "    target { time: { minimum: O(xs.length()) }, };\n"
                "}\n")
    with open(os.path.join(d2, "b.eln"), "w", encoding="utf-8", newline="\n") as f:
        f.write("u64 b(const Vec<u64>& xs) { u64 i = 0; "
                "while (i != 7) { i = i + 1; } return i; }\n")
    _rc, out = run(["check", os.path.join(d2, "b")])
    check("槽位有修法", "E-Target-BadSlot" in out and "改成 `max`" in out, out[:400])
    check("while 条件有修法", "E-While-NoBound" in out and "改成 `i < xs.length()`" in out,
          out[:400])

    print("[6] 修法指到 .eln 的**那一行**（不是 .tu）")
    _rc, out = run(["check", os.path.join(d2, "b"), "--apply=2"])
    # 第 1 条是 BadSlot（.tu:8），第 2 条是 while（.eln:1）
    check("最后一条指到 b.eln", "b.eln:1" in out, out[-300:])
    check("确实改了条件", "while (i < xs.length())" in out, out[-300:])

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
