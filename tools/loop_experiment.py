#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""loop_experiment.py —— **闭环实验**：几次编译错误反馈能写出可编译的程序

## 它回答什么问题

上一轮试点（`结果-语法严格度试点.md`）证明：**冷启动下模型写不出 Elench**（0/20）。
那条结论把问题从"提示怎么写"改成了：

> **如果编译器把错误码回灌给模型，它几次能改对？**

这个脚本就是测这个的。

## 实验设计

| 组 | 做法 |
|---|---|
| 每一轮 | 生成 → 落成 `.tu`/`.eln` → `elench check` → 通过则停 |
| 失败时 | 把**错误码 + 行号 + 消息**原样回灌，要求只改这些地方 |
| 上限 | `--max-retries 3`（即最多 4 次生成） |

**记录**：第几次通过、每次的错误码序列、最终是否通过。

## 判定为什么可信

判定不是"模型说它对"，而是 **`elench check` 的退出码**——与 CI 用的同一个门禁。

## 用法

```bash
python tools/loop_experiment.py \
  --base-url https://api.deepseek.com --model deepseek-flash \
  --tasks 5 --max-retries 3
```

Key 从 `OPENAI_API_KEY` 或 `DEEPSEEK_API_KEY` 读（**不写进任何文件**）。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
DSH = os.path.join(ROOT, "src", "dsh.py")
OUT_DIR = os.path.join(ROOT, "文档", "设计", "归档", "实验-2026-11", "raw")

sys.path.insert(0, os.path.join(ROOT, "src"))

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122 Safari/537.36"

# ---------------------------------------------------------------- 题目

# 每题给一个**固定的声明文件骨架**：模型只写实现，避免它在 .tu 上跑偏。
TASKS = [
    {
        "id": "t1",
        "tu": """error Empty;

spec avg(const Vec<u64>& xs) -> u64! {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
""",
        "ask": "实现 spec 里的 `avg`：返回整数平均值；空输入返回 `Err(Empty::NoInput)`。",
    },
    {
        "id": "t2",
        "tu": """error Empty;

spec count(const Vec<u8>& text) -> u64! {
    effects {};
    target { time: { max: O(text.length()) }, allocation: { max: 0 }, };
}
""",
        "ask": "实现 spec 里的 `count`：返回出现次数最多的字节的出现次数；空输入返回 `Err(Empty::NoInput)`。",
    },
    {
        "id": "t3",
        "tu": """error Bad;

spec paren(const Vec<u8>& text) -> u64! {
    effects {};
    target { time: { max: O(text.length()) }, allocation: { max: 0 }, };
}
""",
        "ask": "实现 spec 里的 `paren`：文本只含 ( ) [ ] 三种字节，括号配对正确返回 1，否则 0。",
    },
    {
        "id": "t4",
        "tu": """error Bad;

spec maxof(const Vec<u64>& xs) -> u64! {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
""",
        "ask": "实现 spec 里的 `maxof`：返回最大值；空输入返回 `Err(Bad::Empty)`。",
    },
    {
        "id": "t5",
        "tu": """error Bad;

spec is_sorted(const Vec<u64>& xs) -> u64! {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
""",
        "ask": "实现 spec 里的 `is_sorted`：升序返回 1，否则 0。",
    },
]

RULES = """Elench 语法要点（严格遵守）：
- 函数：`func name(const Vec<u64>& xs) -> u64! { ... }`
- 常量：`const u64 n = xs.length();`；可变：`u64 i = 0;` 之后直接赋值
- 语句必须以 `;` 结尾；块用 `{ }`，**没有 `end`**
- 只有 `while (cond) { ... }` 与 `if (cond) { ... }`，**没有 `for ... in`**
- 取元素：`xs.at(i)`；长度：`xs.length()`；**没有字符串字面量**
- 返回错误：`return Err(Empty::NoInput);`；成功：`return value;`
"""


def build_prompt(task, prev=None, diags=None):
    p = (RULES + "\n声明文件（不要改它）：\n```elench\n" + task["tu"] + "```\n\n"
         + "任务：" + task["ask"] + "\n\n"
         + "只输出**实现文件的完整内容**（不要解释、不要围栏）。\n")
    if prev and diags:
        p += ("\n---\n你上一次的实现有这些错误（`elench check` 的原样输出）：\n"
              + diags + "\n请只修这些地方，重新输出**完整实现文件**。\n")
    return p


# ---------------------------------------------------------------- 模型

def call_model(base_url, model, prompt, key, timeout=120):
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0.3, "max_tokens": 2000,
                       # **关掉思考**：`deepseek-flash` 默认会先写一大段 reasoning，
                       # 3000 token 全被它吃掉，content 为空 → 判定成"没写实现"。
                       "thinking": {"type": "disabled"}}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}",
                                          "User-Agent": UA,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return d["choices"][0]["message"]["content"]


def strip_fences(code: str) -> str:
    s = code.strip()
    if "```" in s:
        parts = s.split("```")
        cand = max(parts, key=len)
        if cand.lstrip().startswith(("elench", "eln", "rust", "text")):
            cand = cand.split("\n", 1)[1] if "\n" in cand else cand
        s = cand
    return s.strip()


# ---------------------------------------------------------------- 判定

def check(tu_src, eln_src):
    """返回 (是否通过, 诊断文本)。用真实 CLI，与 CI 同一门禁。"""
    d = tempfile.mkdtemp()
    tu = os.path.join(d, "m.tu")
    eln = os.path.join(d, "m.eln")
    open(tu, "w", encoding="utf-8", newline="\n").write(tu_src)
    open(eln, "w", encoding="utf-8", newline="\n").write(eln_src)
    r = subprocess.run([sys.executable, DSH, "check", tu, eln],
                       capture_output=True, text=True, encoding="utf-8")
    out = (r.stdout or "") + (r.stderr or "")
    codes = re.findall(r"\b(E-[A-Za-z0-9-]+)\b", out)

    # **空实现不算通过**：`check` 对"没写体"只报 planned 状态，不报错。
    # 只靠 E- 码判定会把"什么都没写"当成通过——第一次跑就是这样 5/5 假通过。
    if not codes:
        try:
            from parser import parse_source
            from bound import _descendants
            mod = parse_source(eln_src, "m.eln").module
            fns = [f for f in _descendants(mod, "fn")]
            if not any(any(c.kind == "block" for c in f.children) for f in fns):
                codes = ["E-NoImpl"]
        except Exception:                              # noqa: BLE001
            codes = ["E-Parse"]
    return (r.returncode == 0 and not codes), out.strip(), codes


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="闭环：错误反馈能让模型几次改对")
    ap.add_argument("--base-url", default="https://api.deepseek.com")
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--tasks", type=int, default=5)
    ap.add_argument("--max-retries", type=int, default=3)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    key = (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        print("需要 DEEPSEEK_API_KEY 或 OPENAI_API_KEY", file=sys.stderr)
        return 2

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = a.out or os.path.join(OUT_DIR, f"loop-{stamp}.md")

    rows = []
    for task in TASKS[:a.tasks]:
        tu = task["tu"]
        prompt = build_prompt(task)
        history = []
        passed_at = None
        eln = ""
        for attempt in range(a.max_retries + 1):
            t0 = time.time()
            try:
                eln = strip_fences(call_model(a.base_url, a.model, prompt, key))
            except Exception as exc:                   # noqa: BLE001
                history.append({"attempt": attempt + 1, "error": f"{type(exc).__name__}: {exc}"})
                break
            ok, diags, codes = check(tu, eln)
            history.append({"attempt": attempt + 1, "ok": ok, "codes": codes,
                            "s": round(time.time() - t0, 1)})
            print(f"  {task['id']} 第 {attempt + 1} 次: "
                  f"{'PASS' if ok else 'FAIL ' + ','.join(codes[:3])}  ({history[-1]['s']}s)",
                  flush=True)
            if ok:
                passed_at = attempt + 1
                break
            prompt = build_prompt(task, prev=eln, diags=diags[:1200])
        rows.append({"task": task["id"], "passed_at": passed_at, "history": history})
        print(f"    → {task['id']}: {('第 %d 次通过' % passed_at) if passed_at else '未通过'}")

    # 汇总
    ok_tasks = [r for r in rows if r["passed_at"]]
    lines = [f"# 闭环实验：错误反馈几次能改对 · {stamp}", "",
             f"- 模型：`{a.model}` @ `{a.base_url}`",
             f"- 题目：{len(rows)} 道，每题最多 {a.max_retries + 1} 次生成", "",
             f"**最终通过：{len(ok_tasks)} / {len(rows)}**", "",
             "| 题 | 第几次通过 | 每轮错误码 |", "|---|---|---|"]
    for r in rows:
        seq = " → ".join(
            ("PASS" if h.get("ok") else ",".join(h.get("codes", [])[:2]) or "ERR")
            for h in r["history"] if "attempt" in h)
        lines.append(f"| {r['task']} | {r['passed_at'] or '—'} | {seq} |")
    lines += ["", "> 判定用的是 `elench check` 的退出码（与 CI 同一门禁），不是模型自评。"]
    open(path, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    print(f"\n  结果：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
