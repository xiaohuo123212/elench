#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""diag_hints.py —— 诊断的"下一步做什么"（T-15）

## 现状（2026-11-07 实测）

**130 个错误码，只有 9 个在消息里带改法**，121 个只说"哪里错了"，不说"怎么改"。

## 这个工具做什么 / 不做什么

| 做 | 不做 |
|---|---|
| 给**机械可改**的码配 1–3 条具体动作 | 不给 121 个码都硬写一句套话（那是填充，不是帮助） |
| `--apply` 只用于**纯 token 替换**的码 | 不自动重排结构、不猜意图 |

**`--apply` 只覆盖一个码**：`E-Func-KeywordRemoved`（`fn` → `func`）。
它是字面替换、**天然幂等**（再跑一次没有 `fn ` 可换，文件不变）。

**其余码只给建议。** 一个会改坏文件的 `--apply` 比没有更糟。

## 用法

    python tools/diag_hints.py 文档/样例/sum          # 跑 check 并附上动作
    python tools/diag_hints.py 模块 --apply=1         # 对第 1 条执行自动修复（若支持）
"""
import io
import json
import os
import re
import subprocess
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 码 → (动作列表, 是否支持自动修复)
HINTS = {
    "E-Func-KeywordRemoved": (["把 `fn` 改成 `func`（v0.68 起）"], True),
    "E-Spawn-Removed": (["删掉 `spawn`，改成顺序调用",
                         "并发成本不是确定上界，语言层不提供"], False),
    "E-Ref-OldMut": (["`&T` → `const T&`（只读）", "`&mut T` → `T&`（可写）"], False),
    "E-Type-OldSlice": (["`[]T` → `vector<T>`"], False),
    "E-Ai-HasDecl": (["`struct`/`enum`/`const` 只能写在 `.tu` 里",
                      "把声明移过去，`.eln` 里只放实现"], False),
    "E-Ai-HasUse": (["`use` 只能写在 `.tu` 里"], False),
    "E-Effect-Missing": (["在 spec 的 `effects { }` 里补上这个效果",
                          "若不该有：把触发它的调用改掉"], False),
    "E-Borrow-Conflict": (["把借用限制在修改之前的作用域里",
                           "或先拷一份值再改容器"], False),
    "E-Borrow-Ambiguous": (["只借用一个参数的元素",
                            "或把两个参数的借用拆成两次调用"], False),
    "E-Cost-UpperOnly": (["给 target 补下界，或改用 `max` 单边（本语言允许单边）"], False),
    "E-Name-Undefined": (["检查拼写", "外部符号请在 `.tu` 的 `extern` 块登记"], False),
    "E-Extern-NoEffects": (["给 extern 声明补 `effects { }`",
                            "纯函数也要显式写空效果集"], False),
    "E-Require-Unproven": (["在调用点加 `if` 守卫，让编译器看得见前提",
                            "或把这项检查放回函数体"], False),
}

FN_RE = re.compile(r"\bfn\b")


def run_check(module):
    p = subprocess.run([sys.executable, "-B", os.path.join(R, "src", "dsh.py"),
                        "check", module, "--json"],
                       cwd=R, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    try:
        return json.loads(p.stdout)
    except Exception:                                  # noqa: BLE001
        return {"diagnostics": [], "raw": p.stdout + p.stderr}


def diags_of(data):
    out = []
    for d in data.get("diagnostics", []) or []:
        out.append((d.get("code", ""), d.get("line", 0), d.get("col", 0), d.get("msg", "")))
    for f in data.get("functions", []) or []:
        for d in f.get("diagnostics", []) or []:
            out.append((d.get("code", ""), d.get("line", 0), d.get("col", 0), d.get("msg", "")))
    return out


def apply_one(module, idx, ds):
    """只对**字面替换**类做自动修复；其余返回 False。"""
    if idx < 1 or idx > len(ds):
        print(f"  ✗ 没有第 {idx} 条诊断")
        return False
    code, line, col, _ = ds[idx - 1]
    hint = HINTS.get(code)
    if not hint or not hint[1]:
        print(f"  {code} 不支持自动修复——请按上面的动作手工改。")
        return False
    path = module if module.endswith((".tu", ".eln")) else module + ".eln"
    if not os.path.exists(path):
        print(f"  ✗ 改不了：找不到 {path}")
        return False
    src = io.open(path, encoding="utf-8").read()
    new = FN_RE.sub("func", src)
    if new == src:
        print(f"  ✓ 已经是改好的状态（幂等：这次没有改动）")
        return True
    io.open(path, "w", encoding="utf-8", newline="\n").write(new)
    print(f"  ✓ 已把 {path} 里的 `fn` 换成 `func`")
    return True


def main(argv) -> int:
    module = None
    apply_idx = None
    for a in argv:
        if a.startswith("--apply="):
            apply_idx = int(a.split("=", 1)[1])
        elif not a.startswith("--"):
            module = a
    if not module:
        print("用法: diag_hints.py <模块> [--apply=N]")
        return 2
    data = run_check(module)
    ds = diags_of(data)
    if not ds:
        print("  ✓ 没有诊断")
        return 0
    if apply_idx is not None:
        return 0 if apply_one(module, apply_idx, ds) else 1
    print(f"  {len(ds)} 条诊断：")
    covered = 0
    for i, (code, line, col, msg) in enumerate(ds, 1):
        hint = HINTS.get(code)
        print(f"  [{i}] {code} @{line}:{col}  {msg[:60]}")
        if hint:
            covered += 1
            for act in hint[0]:
                print(f"        → {act}")
            if hint[1]:
                print(f"        （可自动修复：--apply={i}）")
    print(f"\n  {covered}/{len(ds)} 条有具体动作；其余见诊断原文。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
