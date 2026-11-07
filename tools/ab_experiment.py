#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""ab_experiment.py —— 《AB实验》的**可执行外壳**（语法严格度对照）

## 为什么现在才做

`文档/设计/归档/实验-2026-11/AB实验.md` 与 `实测题目.md` 写着"设计完成，**未执行**"。
设计再细，没有执行器就永远是零。这个脚本把三件事接起来：

1. **题目**（五道，来自 `实测题目.md`）——已按**当前语法**重写（`func`/`const`/`var`/`{}`）；
2. **模型**——外部命令，`--model-cmd`，从 stdin 读提示、往 stdout 写代码（**不绑定任何厂商**）；
3. **判定**——机械可判：能不能通过解析（严格组还要过 `elench check`），**不靠人看**。

## 三种用法

```bash
# ① 自检：用内置 stub 模型跑通全流程（不需要任何 API Key）
python tools/ab_experiment.py --stub

# ② 真跑：接任意模型命令（示例，换成你自己的）
python tools/ab_experiment.py --model-cmd "my-llm-cli --stdin" --samples 5

# ③ 只跑某一组
python tools/ab_experiment.py --stub --arm strict
```

## 它**不**证明什么（必须和结果一起看）

> 这个实验测的是"**提示里带不带语法约束**"对生成代码**能否通过解析**的影响。
> 它**不**测"这门语言好不好"，也**不**测成本契约。**两件事不能混为一谈。**

## 结果去哪

`文档/设计/归档/实验-2026-11/raw/ab-<时间>.csv`（逐条）+ `ab-<时间>.md`（汇总）。
"""
import argparse
import csv
import datetime
import io
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
OUT_DIR = os.path.join(ROOT, "文档", "设计", "归档", "实验-2026-11", "raw")

sys.path.insert(0, os.path.join(ROOT, "src"))

# ---------------------------------------------------------------- 题目（当前语法）

TASKS = [
    ("t1", "写一个函数，接收一串整数，返回它们的和与平均值（平均值用浮点）。空输入时返回错误。",
     "覆盖 func 声明 / const 与 var / while 循环 / 算术 / error 与 `!`"),
    ("t2", "统计一段文本中每个字节出现的次数，返回出现最多的那个的次数。",
     "覆盖 Vec<u8>、FixedVec 桶、嵌套循环、max"),
    ("t3", "判断一段文本里的三种括号是否配对正确。",
     "覆盖 enum、穷尽 match、定容容器当栈"),
    ("t4", "解析形如 `key = value` 的配置文本，跳过空行与以 `#` 开头的注释行，遇到格式错误返回错误。",
     "覆盖字节扫描、error 枚举、`?` 传播、match"),
    ("t5", "写一个函数，把一串整数原地按升序排好（不调用库排序）。",
     "覆盖嵌套循环、下标读写、swap 用临时变量"),
]

PROMPT_HEAD = "你是 Elench 语言的代码生成器。只输出代码，不要解释。\n\n"

STRICT_RULES = """严格遵守以下语法约束（违反即判失败）：
- 函数用 `func name(...) -> T { ... }`，**不要**写 `function`、不要 `fn`
- 局部常量用 `const`，可变变量用 `var`，**不要**写 `val` / `variable`
- 块用 `{ }` 结束，**不要**写 `end`
- 每条语句以 `;` 结尾（`func`/`spec`/`struct` 等块声明除外）
- 只有 `while (cond) { ... }` 与 `if (cond) { ... }`，**没有** `for ... in ...`
- 类型写在名字后：`const u64 n = ...;`
"""

LOOSE_RULES = "按你习惯的风格写即可。\n"


def build_prompt(task_desc: str, arm: str) -> str:
    rules = STRICT_RULES if arm == "strict" else LOOSE_RULES
    return PROMPT_HEAD + rules + "\n任务：" + task_desc + "\n"


# ---------------------------------------------------------------- 模型

STUB_GOOD = """func avg(const Vec<u64>& xs) -> u64! {
    const u64 n = xs.length();
    if (n == 0) {
        return Err(Empty::NoInput);
    }
    u64 s = 0;
    u64 i = 0;
    while (i < n) {
        s = s + xs.at(i);
        i = i + 1;
    }
    if not s:
        raise RuntimeError("模型返回空内容")
    return s / n;
}
"""

STUB_OLD_SYNTAX = """function avg(xs) {
    val n = xs.length()
    variable s = 0
    for i in 0..n {
        s = s + xs.at(i)
    end
    return s / n
}
"""

STUB_MISSING_SEMI = """func avg(const Vec<u64>& xs) -> u64! {
    const u64 n = xs.length()
    return n
}
"""


def call_model(cmd: str, prompt: str, timeout: int = 120) -> str:
    """外部模型命令：**stdin 喂提示，stdout 收代码**。"""
    r = subprocess.run(cmd, shell=True, input=prompt, capture_output=True,
                       text=True, encoding="utf-8", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"模型命令退出码 {r.returncode}: {(r.stderr or '')[:200]}")
    return r.stdout or ""


def stub_model(prompt: str, task_id: str, sample: int) -> str:
    """假模型：**按组给不同输出**，只为验证流水线能分辨对错。

    - 严格组（提示里有约束）：多数给合规代码，偶尔缺分号；
    - 宽松组（无约束）：混入旧语法（`function`/`val`/`end`）与缺分号。

    这样两组通过率会出现差异——**但那是我们编出来的**，不是实验结果。
    """
    strict = "严格遵守" in prompt
    seq = ([STUB_GOOD, STUB_GOOD, STUB_MISSING_SEMI] if strict
           else [STUB_OLD_SYNTAX, STUB_MISSING_SEMI, STUB_GOOD])
    return seq[(hash(task_id) + sample) % 3]


# ---------------------------------------------------------------- 判定

def judge(code: str, arm: str) -> dict:
    """机械判定：先解析，严格组再过检查器。"""
    from parser import parse_source

    code = code.strip()
    if not code:
        return {"ok": False, "code": "E-Empty", "lines": 0}
    lines = len([x for x in code.splitlines() if x.strip()])
    try:
        res = parse_source(code, "gen.eln")
    except Exception as exc:                        # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        m = re.search(r"\b(E-[A-Za-z0-9-]+)\b", msg)
        return {"ok": False, "code": m.group(1) if m else "E-Parse", "lines": lines}

    # **解析器不抛异常也会报错**（诊断放在 diagnostics 里）——只 catch 异常等于
    # 把语法错误当通过。这是写这个脚本时踩到的第一个坑。
    errs = [d for d in res.diagnostics if d.code.startswith("E-")]
    if errs:
        return {"ok": False, "code": errs[0].code, "lines": lines}
    mod = res.module

    # **必须有 `func` 节点**：只"能解析"太弱——旧语法的 `function x() { }`
    # 会被当成表达式语句解析过去，看起来"通过"，其实是垃圾。
    from bound import _descendants
    if not _descendants(mod, "fn"):
        return {"ok": False, "code": "E-NoFunc", "lines": lines}

    if arm == "strict":
        # 严格组额外要求：所有块声明外语句带分号（解析器已覆盖），这里补一条
        # "不许出现已下线的关键字"——它们在新语法里本就是普通标识符，能解析但语义错。
        legacy = [w for w in ("function ", "val ", "variable ", " end\n", "end;")
                  if w in code]
        if legacy:
            return {"ok": False, "code": "E-Legacy-Keyword", "lines": lines}
    return {"ok": True, "code": "", "lines": lines}




# ---------------------------------------------------------------- OpenAI 兼容端点

def openai_generate(base_url: str, model: str, prompt: str,
                    temperature: float = 0.7, timeout: int = 300,
                    max_tokens: int = 700) -> str:
    """调 `/v1/chat/completions`（LM Studio / Ollama / vLLM 都兼容）。

    **为什么用这个而不是各家 SDK**：本地模型服务器几乎都实现了 OpenAI 兼容接口，
    一条 urllib 请求就能通吃；装 SDK 反而制造依赖。
    """
    import json as _json
    import urllib.request as _req

    url = base_url.rstrip("/") + "/v1/chat/completions"
    body = _json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stop": ["\n\n\n"],
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        # 线上 API 几乎都要 Bearer；本地 LM Studio / Ollama 不需要（有也不影响）
        headers["Authorization"] = f"Bearer {key}"
    req = _req.Request(url, data=body, method="POST", headers=headers)
    with _req.urlopen(req, timeout=timeout) as r:
        data = _json.loads(r.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def list_models(base_url: str) -> list:
    import json as _json
    import urllib.request as _req
    with _req.urlopen(base_url.rstrip("/") + "/v1/models", timeout=10) as r:
        data = _json.loads(r.read().decode("utf-8"))
    return [m.get("id") for m in data.get("data", [])]


def strip_fences(code: str) -> str:
    """模型爱把代码包在 ``` 里——剥掉再判，否则 100% 解析失败。"""
    s = code.strip()
    if "```" in s:
        parts = s.split("```")
        # 取最长的一段（通常是代码块内容）
        cand = max(parts, key=len)
        if cand.lstrip().startswith(("elench", "eln", "rust", "text")):
            cand = cand.split("\n", 1)[1] if "\n" in cand else cand
        s = cand.strip()
    return s


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="语法严格度 A/B 实验（可执行外壳）")
    ap.add_argument("--model-cmd", default="", help="外部模型命令（stdin→stdout）")
    ap.add_argument("--stub", action="store_true", help="用内置假模型自检流水线")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--arm", choices=["strict", "loose", "both"], default="both")
    ap.add_argument("--task", default="", help="只跑某一个题号，如 t3")
    ap.add_argument("--export-prompts", default="",
                    help="把 10 个提示词写成文件（手动粘贴到网页对话用），给目录")
    ap.add_argument("--openai-url", default="", help="OpenAI 兼容端点，如 http://127.0.0.1:1234")
    ap.add_argument("--openai-model", default="", help="模型 id（--list-models 可查）")
    ap.add_argument("--list-models", action="store_true", help="列出端点上的模型")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--max-tokens", type=int, default=700)
    ap.add_argument("--no-think", choices=["auto", "on", "off"], default="auto",
                    help="Qwen3 等模型的思考模式：auto=按模型名判断")
    ap.add_argument("--from-dir", default="",
                    help="不调用模型，直接判定目录里已有的 <arm>-<task>.eln")
    a = ap.parse_args()

    if a.list_models:
        try:
            for m in list_models(a.openai_url or "http://127.0.0.1:1234"):
                print("  " + m)
            return 0
        except Exception as exc:                    # noqa: BLE001
            print(f"  连不上：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

    # ---- 模式 A：导出提示词（给没有 API 的人用浏览器手动跑）
    if a.export_prompts:
        d = a.export_prompts
        os.makedirs(d, exist_ok=True)
        n = 0
        for arm in ("strict", "loose"):
            for tid, desc, cov in TASKS:
                fn = os.path.join(d, f"{arm}-{tid}.txt")
                io.open(fn, "w", encoding="utf-8", newline="\n").write(
                    build_prompt(desc, arm)
                    + "\n===== 把模型输出另存为 =====\n"
                    + os.path.join(d, f"{arm}-{tid}.eln") + "\n")
                n += 1
        print(f"  已导出 {n} 个提示词到 {d}")
        print("  用法：逐个粘进模型对话 → 把回答存成同名的 .eln → 再跑 --from-dir")
        return 0

    # ---- 模式 B：判定已有文件（手动跑或别的工具生成）
    if a.from_dir:
        d = a.from_dir
        os.makedirs(OUT_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        rows = []
        for arm in ("strict", "loose"):
            for tid, desc, cov in TASKS:
                fn = os.path.join(d, f"{arm}-{tid}.eln")
                if not os.path.exists(fn):
                    rows.append({"arm": arm, "task": tid, "sample": 0, "ok": False,
                                 "code": "E-MissingFile", "lines": 0, "ms": 0,
                                 "error": "", "model": d})
                    continue
                code = io.open(fn, encoding="utf-8").read()
                v = judge(code, arm)
                rows.append({"arm": arm, "task": tid, "sample": 0, "ok": v["ok"],
                             "code": v["code"], "lines": v["lines"], "ms": 0,
                             "error": "", "model": d})
                print(f"  {arm:6} {tid}  {'PASS' if v['ok'] else 'FAIL ' + v['code']}")
        csv_path = os.path.join(OUT_DIR, f"ab-{stamp}.csv")
        with io.open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
        ok_s = sum(1 for r in rows if r["arm"] == "strict" and r["ok"])
        ok_l = sum(1 for r in rows if r["arm"] == "loose" and r["ok"])
        md = os.path.join(OUT_DIR, f"ab-{stamp}.md")
        io.open(md, "w", encoding="utf-8", newline="\n").write(
            f"# A/B（手工采样）· {stamp}\n\n"
            f"- 来源目录：`{d}`\n- 每题 1 个样本（手动粘贴）\n\n"
            f"| 组 | 通过 / 总数 |\n|---|---|\n| strict | {ok_s} / 5 |\n| loose | {ok_l} / 5 |\n")
        print(f"\n  strict {ok_s}/5   loose {ok_l}/5\n  结果：{md}")
        return 0

    if not a.stub and not a.model_cmd and not a.openai_url:
        print("要么 --stub，要么给 --model-cmd，或用 --export-prompts / --from-dir", file=sys.stderr)
        return 2

    arms = ["strict", "loose"] if a.arm == "both" else [a.arm]
    tasks = [t for t in TASKS if not a.task or t[0] == a.task]
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = os.path.join(OUT_DIR, f"ab-{stamp}.csv")
    md_path = os.path.join(OUT_DIR, f"ab-{stamp}.md")

    rows = []
    for arm in arms:
        for tid, desc, cov in tasks:
            for s in range(a.samples):
                prompt = build_prompt(desc, arm)
                t0 = time.time()
                try:
                    if a.stub:
                        code = stub_model(prompt, tid, s)
                    elif a.openai_url:
                        _p = prompt
                        _think_off = (a.no_think == "on" or
                                      (a.no_think == "auto" and "qwen3" in a.openai_model.lower()))
                        if _think_off:
                            # **Qwen3 默认会先"想"一遍**：400 token 的推理 + 400 token 的代码
                            # = 45–78 秒/次。加 `/no_think` 后降到 ~10 秒，且输出更贴题目。
                            _p = prompt + "\n/no_think\n"
                        code = strip_fences(openai_generate(
                            a.openai_url, a.openai_model, _p, a.temperature,
                            max_tokens=a.max_tokens))
                    else:
                        code = call_model(a.model_cmd, prompt)
                    err = ""
                except Exception as exc:                # noqa: BLE001
                    code, err = "", f"{type(exc).__name__}: {exc}"
                v = judge(code, arm) if code else {"ok": False, "code": "E-Model", "lines": 0}
                rows.append({
                    "arm": arm, "task": tid, "sample": s,
                    "ok": v["ok"], "code": v["code"], "lines": v["lines"],
                    "ms": round((time.time() - t0) * 1000), "error": err,
                    "model": "stub" if a.stub else a.model_cmd,
                })
                print(f"  {arm:6} {tid} #{s}  {'PASS' if v['ok'] else 'FAIL ' + v['code']:12} "
                      f"{v['lines']:>3} 行", flush=True)

    with io.open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # 汇总
    def rate(arm):
        sub = [r for r in rows if r["arm"] == arm]
        return (sum(1 for r in sub if r["ok"]), len(sub))

    lines = [f"# A/B 实验（语法严格度）· {stamp}", "",
             f"- 模型：`{'stub（自检，非真实结果）' if a.stub else a.model_cmd}`",
             f"- 每题样本数：{a.samples}", "",
             "| 组 | 通过 / 总数 | 通过率 |", "|---|---|---|"]
    for arm in arms:
        ok, n = rate(arm)
        lines.append(f"| {arm} | {ok} / {n} | {(ok / n * 100 if n else 0):.0f}% |")
    lines += ["", "## 失败明细", "", "| 组 | 题 | 样本 | 错误码 |", "|---|---|---|---|"]
    for r in rows:
        if not r["ok"]:
            lines.append(f"| {r['arm']} | {r['task']} | {r['sample']} | `{r['code']}` |")
    lines += ["", "> **边界**：本实验测的是「提示里带不带语法约束」对**能否通过解析**的影响，",
              "> 不测「这门语言好不好」，也不测成本契约。**两件事不能混为一谈。**"]
    io.open(md_path, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")

    print()
    for arm in arms:
        ok, n = rate(arm)
        print(f"  {arm:6} 通过 {ok}/{n}  ({(ok / n * 100 if n else 0):.0f}%)")
    print(f"  结果：{md_path}")
    if a.stub:
        print("  ！ --stub 只验证流水线能分辨对错，**不是实验结论**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
