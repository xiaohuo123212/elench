#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_exhaustive.py —— `check --exhaustive`：**有限输入域上的真证明**

## 它守的是什么

`guarantees` 以前只在 `run` 时求值：强度 = 「跑过的那次输入成立」，
等于加了类型检查的 `assert`。`--exhaustive` 把参数域**有限且不大**的函数
枚举一遍，`check` 阶段就能说【证明】。这条测试钉住三件事：

1. **档位不许混**：证明 / 符合（无界，只做运行期断言）/ 未证（域太大、
   超时）是**三句不同的话**，不能合并成一个「通过」；
2. **反例要给人看**：报出`E-Guarantee-Counterexample`、行号、**最小**输入
   与返回值；
3. **不抽样冒充**：`u64`（无界）不枚举、不谎报；组合数超上限就跳过并说明。
"""
import json
import os
import subprocess
import sys
import tempfile

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


def write_module(tmp, name, tu_src, eln_src):
    d = os.path.join(tmp, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "m.tu"), "w", encoding="utf-8", newline="\n") as f:
        f.write(tu_src)
    with open(os.path.join(d, "m.eln"), "w", encoding="utf-8", newline="\n") as f:
        f.write(eln_src)
    return os.path.join(d, "m")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="elench_exh_")

    print("[1] u8 + 恒真 guarantees → 【证明】256/256")
    mod = write_module(
        tmp, "proved",
        "spec f(u8 a) -> u64 {\n"
        "    effects {};\n"
        "    guarantees ret >= 0;\n"
        "}\n",
        "u64 f(u8 a) { return a; }\n")
    rc, out = run(["check", mod, "--exhaustive"])
    check("退出码 0", rc == 0, out[-300:])
    check("明确说【证明】", "【证明】" in out, out[-300:])
    check("给出组合数 256 与「全部满足」",
          "256" in out and "全部满足 guarantees" in out, out[-300:])
    # **JSON 也带分档结论**（可选字段）：AI 不必解析人读输出。
    _rc, js = run(["check", mod, "--exhaustive", "--format=json"])
    try:
        data = json.loads(js)
        reps = data.get("exhaustive") or []
        ok_json = (data.get("ok") is True and len(reps) == 1
                   and reps[0].get("status") == "proved"
                   and reps[0].get("combos") == 256)
    except Exception:                                   # noqa: BLE001
        data, reps, ok_json = {}, [], False
    check("JSON 的 exhaustive.status = proved / combos = 256", ok_json, js[:300])

    print("[2] u8 + 会失败的 guarantees → 反例，且输入是**最小**的")
    mod = write_module(
        tmp, "cex",
        "spec f(u8 a) -> u64 {\n"
        "    effects {};\n"
        "    guarantees ret < 200;\n"
        "}\n",
        "u64 f(u8 a) { return a; }\n")
    rc, out = run(["check", mod, "--exhaustive"])
    check("退出码 1（反例是 error）", rc == 1, out[-400:])
    check("报 E-Guarantee-Counterexample", "E-Guarantee-Counterexample" in out,
          out[-400:])
    check("反例给出输入与返回值", "反例：a = 200" in out and "ret = 200" in out,
          out[-400:])
    check("反例是**最小**的（不是 199 或更小）",
          "a = 199" not in out and "a = 0" not in out, out[-400:])
    check("状态行是【反例】", "【反例】" in out, out[-300:])

    print("[3] u64 参数 → 【符合】（无界，只做运行期断言），不穷举")
    mod = write_module(
        tmp, "unbounded",
        "spec g(u64 n) -> u64 {\n"
        "    effects {};\n"
        "    guarantees ret >= 0;\n"
        "}\n",
        "u64 g(u64 n) { return n; }\n")
    rc, out = run(["check", mod, "--exhaustive"])
    check("退出码 0", rc == 0, out[-300:])
    check("明确说【符合】与 u64（无界）",
          "【符合】" in out and "u64（无界）" in out, out[-300:])
    check("**不谎报证明**", "【证明】" not in out and "种组合" not in out,
          out[-300:])

    print("[4] 组合数超上限 → 跳过穷举并说明")
    mod = write_module(
        tmp, "toolarge",
        "spec k(u8 a, u8 b) -> u64 {\n"
        "    effects {};\n"
        "    guarantees ret >= 0;\n"
        "}\n",
        "u64 k(u8 a, u8 b) { return a; }\n")
    rc, out = run(["check", mod, "--exhaustive", "--max-combos=1000"])
    check("退出码 0（跳过不是错误）", rc == 0, out[-300:])
    check("明确说【未证】且「域太大，跳过穷举」",
          "【未证】" in out and "域太大，跳过穷举" in out, out[-300:])
    check("说明总数 65,536 与上限 1,000",
          "65,536" in out and "1,000" in out, out[-300:])
    check("**不谎报证明**", "【证明】" not in out, out[-300:])

    print("[5] 超时 → 【未证】（不声称证明）")
    mod = write_module(
        tmp, "timeout",
        "spec t(u8 a, u8 b) -> u64 {\n"
        "    effects {};\n"
        "    guarantees ret >= 0;\n"
        "}\n",
        "u64 t(u8 a, u8 b) { return a; }\n")
    rc, out = run(["check", mod, "--exhaustive",
                   "--max-combos=100000", "--exhaustive-timeout=0.000001"])
    check("退出码 0（没跑完不是错误）", rc == 0, out[-300:])
    check("明确说【未证】与「穷举未完成」",
          "【未证】" in out and "穷举未完成" in out, out[-300:])
    check("**不谎报证明**", "【证明】" not in out, out[-300:])

    print("[6] requires 把输入排除在契约外：排除后仍是【证明】")
    mod = write_module(
        tmp, "requires",
        "spec q(u8 a) -> u64 {\n"
        "    effects {};\n"
        "    requires a > 0;\n"
        "    guarantees ret > 0;\n"
        "}\n",
        "u64 q(u8 a) { return a; }\n")
    rc, out = run(["check", mod, "--exhaustive"])
    check("退出码 0", rc == 0, out[-400:])
    check("仍是【证明】", "【证明】" in out, out[-400:])
    check("说明排除了 1 种（a = 0）不满足 requires 的输入",
          "1 种输入不满足 requires，已排除" in out, out[-400:])

    print("[7] 随机源 → 【未证】（随机结果不在参数域里）")
    mod = write_module(
        tmp, "nondet",
        "spec r(u8 a) -> u64 {\n"
        "    effects { nondet };\n"
        "    guarantees ret < 2;\n"
        "}\n",
        "u64 r(u8 a) { return rand_u64() % 2; }\n")
    rc, out = run(["check", mod, "--exhaustive"])
    check("退出码 0（不是错误）", rc == 0, out[-400:])
    check("明确说【未证】与 rand_u64",
          "【未证】" in out and "rand_u64" in out, out[-400:])
    check("**不谎报证明**", "【证明】" not in out, out[-400:])

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
