#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_contract_yaml.py —— L-11 的三条验收（对全部样例）

## 钉住什么

| 验收 | 要求 |
|---|---|
| `tu → yaml` 幂等 | **全部 17 个样例** |
| `yaml → tu → yaml` 逐字节稳定 | **全部 17 个样例** |
| `tu → yaml → tu'` AST 等价 | **全部 17 个样例**（`KNOWN_UNSUPPORTED` 为空） |

**2026-11-09 补的回归断言**：`HEAD_RE` 曾经多写了一个 `spec`（正则要求源文本
里出现两次 "spec"），于是 17 个样例的 spec **一个都没被识别**，全部被当成
普通语句拍平成一段 token 串——而三条验收**照样全绿**，因为它们测的只是
"拍平后的 token 串"，错误从比较里溜过去了。

所以这里**独立**数一遍源文件里的 `spec` 头行数（只看行首，不经过
`parse_tu`），要求与解析出的 spec 数一致；再要求 `others` 里没有任何一项
以 `spec` 开头。这条断言直接钉住"静默拍平"这个失败模式，不依赖被测
解析器自己的判断。

**回归保护**：如果哪天 `KNOWN_UNSUPPORTED` 里的样例**通过了**，本测试也会失败——
提醒把它从名单里删掉。反过来，名单外的样例掉了，立刻红。
"""
import io
import os
import re
import subprocess
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from contract_yaml import KNOWN_UNSUPPORTED, parse_tu      # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


samples = [f[:-3] for f in sorted(os.listdir(os.path.join(ROOT, "文档", "样例")))
           if f.endswith(".tu")]
check("样例数 >= 15", len(samples) >= 15, len(samples))

for m in samples:
    r = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tools", "contract_yaml.py"),
                        m, "--selfcheck"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    known = "已知不支持" in (r.stdout or "")
    if m in KNOWN_UNSUPPORTED:
        check(f"{m}：已知不支持且**明确打印**", r.returncode == 0 and known,
              f"exit={r.returncode}")
    else:
        check(f"{m}：三条验收全过", r.returncode == 0 and not known,
              (r.stdout or r.stderr).strip().split("\n")[-1][:60])

# ---- 结构断言：spec 必须**真的被识别**，不能被拍平成 others --------------
# 旧 bug 下这三条全都会失败，而上面三条验收照样全绿。
count_bad, flat_left, thin = [], [], []
total = 0
for m in samples:
    text = io.open(os.path.join(ROOT, "文档", "样例", m + ".tu"),
                   encoding="utf-8").read()
    # 独立计数：只看**.tu 源码里行首的 `spec` 头**，不经过 parse_tu
    naive = sum(1 for ln in text.split("\n") if re.match(r"spec\b", ln.strip()))
    specs, others, _order = parse_tu(text)
    total += len(specs)
    if naive == 0 or len(specs) != naive:
        count_bad.append(f"{m}({len(specs)}/{naive})")
    flat_left += [f"{m}:{o[:30]}" for o in others if o.startswith("spec")]
    thin += [f"{m}:{s['name']}" for s in specs
             if not s["name"] or not s["head"].startswith("spec ")
             or not s["clauses"]]

check("每个样例的 spec 数与源文件 `spec` 头行数一致（没有拍平）",
      not count_bad, count_bad)
check("others 里没有残留的 spec", not flat_left, flat_left)
check("每个 spec 都有 name / head / 至少一条 clause", not thin, thin)
check(f"{len(samples)} 个样例共识别 {total} 个 spec（>= 每个样例 1 个）",
      total >= len(samples), total)

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print(f"全部通过：{len(samples)} 个样例（{len(KNOWN_UNSUPPORTED)} 个已知例外）")
