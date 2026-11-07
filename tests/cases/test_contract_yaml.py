#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_contract_yaml.py —— L-11 的三条验收（对全部样例）

## 钉住什么

| 验收 | 要求 |
|---|---|
| `tu → yaml` 幂等 | **全部 15 个样例** |
| `yaml → tu → yaml` 逐字节稳定 | **全部 15 个样例** |
| `tu → yaml → tu'` AST 等价 | **14 个**；`calculator` 在 `KNOWN_UNSUPPORTED` 里 |

**为什么允许一个例外**：`calculator` 的 `enum` 体里嵌了 `variant` / `field`，
我的行级切分器还没覆盖到——**这是工具表达力的缺口，不是契约错误**。
但例外必须写在代码里（`KNOWN_UNSUPPORTED`）并被这里断言，
**不能靠"自检失败就没人跑"来掩盖**。

**回归保护**：如果哪天 `KNOWN_UNSUPPORTED` 里的样例**通过了**，本测试也会失败——
提醒把它从名单里删掉。反过来，名单外的样例掉了，立刻红。
"""
import os
import subprocess
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from contract_yaml import KNOWN_UNSUPPORTED      # noqa: E402

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

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print(f"全部通过：{len(samples)} 个样例（{len(KNOWN_UNSUPPORTED)} 个已知例外）")
