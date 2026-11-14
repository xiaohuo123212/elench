#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""cost_artifact —— `*.cost.yaml`：**成本结果 + 出处（provenance）**

## 它是什么

`elench cost <模块> --out=x.cost.yaml` 写出来的那份文件：

```yaml
module: count
generated:
  tool: "elench 1.0.0"
  python: "3.14.0"
  platform: "Windows"
  timestamp: "2026-11-11T00:52:00+08:00"
  eln_sha256: "…"
  tu_sha256: "…"
functions:
  - name: count
    kappa: "12 + 1 + …"
    target: "n(text)×n(text)"
    verdict: ok
    allocation: "alloc <= 0"
    ratio: 0.0
```

## 三条边界（用户 2026-11-11 定的）

1. **能重算的就不存；存了的必须能被校验**——所以这里**不**存"这台机器上
   κ 是否成立"这种结论，只存"这份结果是什么时候、用什么算出来的"。
2. **`.tu` 里不记 `.eln` 的哈希**（那是契约文件，一屏读完）；
   哈希只出现在这份**生成物**里，看一眼就知道结果对应哪两份源文件。
3. **provenance 只写不校验**：它**永不**导致任何 error / warning，
   也**不参与任何门禁**。κ 的漂移检测只有一条机制：
   `tools/cost_baseline.py`（入库的 JSON 基线，跑 `--check`）。

## 为什么不进 git

`*.cost.yaml` 是**结果文件**（`.gitignore` 已排除）：换一台机器、换一个
时间戳就变了。入库的应该是**能重算它的输入**（`.tu` / `.eln`），
以及**要人看一眼的基线**（`tests/baselines/cost.json`）。
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import sys
from datetime import datetime


def sha256_of(path):
    """文件内容的 sha256（大写十六进制）；读不到就 None（**不编一个哈希**）。"""
    if not path:
        return None
    try:
        h = hashlib.sha256()
        with io.open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def collect(module, tu_path, eln_path, rows, tool_version, now=None):
    """拼出要写的那棵树（纯数据，**没有时间/平台以外的环境依赖**）。

    `now` 可以显式给（测试用），不给就取本机当前时间（带时区偏移）。
    """
    if now is None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
    elif isinstance(now, datetime):
        now = now.astimezone().isoformat(timespec="seconds")
    return {
        "module": module,
        "generated": {
            "tool": "elench %s" % (tool_version or ""),
            "python": platform.python_version(),
            "platform": platform.system(),
            "timestamp": str(now),
            "eln_sha256": sha256_of(eln_path),
            "tu_sha256": sha256_of(tu_path),
        },
        "functions": list(rows),
    }


_HEADER = (
    "# 由 `elench cost <模块> --out=…` 生成；**结果文件**：不入库、不参与门禁。\n"
    "# provenance 只写不校验——它不会导致任何 error / warning。\n"
    "# κ 的漂移检测请用 `python tools/cost_baseline.py --check`（入库的 JSON 基线）。\n"
)


def render(module, tu_path, eln_path, rows, tool_version, now=None):
    """返回整份文件的文本（含文件头注释）。"""
    # `to_yaml` 与 `cost` 的 `--signature` 输出**同一个发射器**：
    # 不再写第二份 YAML 序列化（两份必然漂移，而且引号风格会不一致）。
    from dsh import to_yaml
    body = to_yaml(collect(module, tu_path, eln_path, rows,
                           tool_version, now=now))
    return _HEADER + body + "\n"


def write(path, module, tu_path, eln_path, rows, tool_version, now=None):
    """写文件（必要时建目录），返回**绝对路径**。"""
    dest = os.path.abspath(path)
    parent = os.path.dirname(dest)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with io.open(dest, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render(module, tu_path, eln_path, rows,
                        tool_version, now=now))
    return dest


if __name__ == "__main__":                              # 手工自检
    _here = os.path.dirname(os.path.abspath(__file__))
    print(render("demo", None, None,
                 [{"name": "f", "kappa": "1 + n(xs)×2", "target": "n(xs)",
                   "verdict": "ok", "allocation": None, "ratio": 0.5}],
                 "0.0.0-selftest", now=None))
    sys.exit(0)
