#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""doc_numbers.py —— 文档里的数字**绑定到来源**，来源一变就报错。

## 为什么需要它（今天的教训）

2026-11-02 一天之内，预印本的数字**过时了三次**：κ 收紧、分段口径、
Table 7 与生成器不一致。每次都是靠人肉 grep 发现的。

> 一个数字只要能被**手抄**，它就会**过期**。

## 零标注起步，一处标注可升级

| 级别 | 做法 | 工具行为 |
|---|---|---|
| **零标注** | 什么都不写 | `--scan` 把文档里 ≥4 位的数字抽出来，与**来源清单**比对；找不到来源的列为"可疑数字" |
| **一处标注** | 在数字后加 `<!--dn:key-->` | `--check` 逐处比对；不一致就非零退出（CI 用） |

标注是**行内 HTML 注释**——在 Markdown 渲染里不可见，authoring 时也不碍眼。

## 用法

```bash
python tools/doc_numbers.py --list                  # 有哪些绑定、值是多少
python tools/doc_numbers.py --check                 # 绑定值 vs 来源（CI）
python tools/doc_numbers.py --scan 文档/其他/预印本/预印本-v0.1.md   # 找可疑数字
```

## 绑定清单在哪

`doc_numbers.json`（与本脚本同目录）：`key → {type, ...}`。
支持四种来源：

| type | 含义 |
|---|---|
| `sum` | `file` 的 `path[*].field` 求和 |
| `field` | `file` 的 `path` 指向的值（点号路径） |
| `count` | `file` 的 `path` 列表长度 |
| `audit` | 调 `tools/audit_numbers.py --json` 取字段 |
| `const` | 字面量（少用；用来说明"这个数就是要固定"） |

**零依赖**：只用标准库；不 import 本项目任何模块（所以它也能给别的仓库用）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MANIFEST = os.path.join(HERE, "doc_numbers.json")

#: 数字 + 可选千分位；后面紧跟一个绑定注释
BOUND = re.compile(r"(?P<num>\d[\d,]*)\s*<!--dn:(?P<key>[A-Za-z0-9_.-]+)-->")
#: 可疑数字：≥4 位（或带千分位），排除年份、版本号、命令行里的参数
SUSPECT = re.compile(r"(?<![\w.\\/-])(\d{1,3}(?:,\d{3})+|\d{4,})(?![\w.\\/-])")


def load_manifest() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(MANIFEST):
        print(f"✗ 找不到绑定清单：{MANIFEST}", file=sys.stderr)
        return {}
    return json.load(open(MANIFEST, encoding="utf-8"))


def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in [p for p in path.split(".") if p]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def resolve(spec: Dict[str, Any]) -> Tuple[Optional[float], str]:
    """算出一个绑定键的当前值 → (值, 说明)。"""
    t = spec.get("type")
    if t == "const":
        return float(spec["value"]), "常量"
    if t == "audit":
        r = subprocess.run([sys.executable, os.path.join(HERE, "audit_numbers.py"), "--json"],
                           capture_output=True, encoding="utf-8")
        if r.returncode != 0:
            return None, "audit_numbers.py 失败"
        data = json.loads(r.stdout)
        return float(data[spec["field"]]), f"audit.{spec['field']}"
    f = os.path.join(ROOT, spec["file"].replace("/", os.sep))
    if not os.path.exists(f):
        return None, f"缺少来源文件 {spec['file']}"
    data = json.load(open(f, encoding="utf-8"))
    if t == "field":
        return float(_dig(data, spec["path"])), f"{spec['file']}:{spec['path']}"
    if t == "count":
        return float(len(_dig(data, spec["path"]))), f"{spec['file']}:{spec['path']}.len"
    if t == "sum":
        rows = _dig(data, spec["path"])
        field = spec["field"]
        return float(sum(r.get(field) or 0 for r in rows)), f"Σ {spec['file']}:{spec['path']}[].{field}"
    return None, f"未知的 type={t}"


def fmt(v: float) -> str:
    return f"{int(v):,}" if abs(v - int(v)) < 1e-9 else f"{v:,.2f}"


def cmd_list(man: Dict[str, Dict[str, Any]]) -> int:
    print(f"{'key':<24}{'当前值':>16}   来源")
    for k, spec in man.items():
        v, why = resolve(spec)
        print(f"{k:<24}{(fmt(v) if v is not None else '—'):>16}   {why}")
    return 0


def cmd_check(man: Dict[str, Dict[str, Any]], docs: List[str]) -> int:
    bad = 0
    total = 0
    for doc in docs:
        p = doc if os.path.isabs(doc) else os.path.join(ROOT, doc)
        if not os.path.exists(p):
            print(f"✗ 文档不存在：{doc}", file=sys.stderr)
            bad += 1
            continue
        text = open(p, encoding="utf-8").read()
        for m in BOUND.finditer(text):
            total += 1
            key = m.group("key")
            if key not in man:
                print(f"  ? {os.path.basename(doc)}: 未知绑定键 {key}")
                bad += 1
                continue
            want, why = resolve(man[key])
            got = int(m.group("num").replace(",", ""))
            if want is None:
                print(f"  ✗ {key}: 来源不可用（{why}）")
                bad += 1
            elif int(round(want)) != got:
                print(f"  ✗ {key}: 文档写 {got:,}，来源 {fmt(want)}（{why}）")
                bad += 1
    print(f"\n绑定检查：{total} 处，{'全部一致' if not bad else f'{bad} 处不一致'}")
    return 0 if bad == 0 else 1


def cmd_scan(docs: List[str]) -> int:
    """零标注模式：把"看起来像主张"的数字列出来，标出哪些能在来源里找到。"""
    man = load_manifest()
    known = set()
    for key, spec in man.items():
        if key.startswith("_"):
            continue
        v, _ = resolve(spec)
        if v is not None:
            known.add(int(round(v)))
    for doc in docs:
        p = doc if os.path.isabs(doc) else os.path.join(ROOT, doc)
        if not os.path.exists(p):
            continue
        text = open(p, encoding="utf-8").read()
        # 去掉代码块（命令里的数字不算主张）
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        suspects = []
        for m in SUSPECT.finditer(text):
            raw = m.group(1)
            val = int(raw.replace(",", ""))
            tag = "known" if val in known else "??"
            if tag == "??":
                line = text.count("\n", 0, m.start()) + 1
                suspects.append((line, raw))
        print(f"=== {os.path.basename(doc)}：{len(suspects)} 个没有来源的数字")
        for line, raw in suspects[:40]:
            print(f"  行 {line:>5}  {raw}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="文档数字绑定检查（零依赖）")
    ap.add_argument("--check", action="store_true", help="校验绑定（CI）")
    ap.add_argument("--list", action="store_true", help="列出绑定与当前值")
    ap.add_argument("--scan", action="store_true", help="零标注模式：找可疑数字")
    ap.add_argument("docs", nargs="*", help="要检查的文档（默认：清单里的 docs）")
    args = ap.parse_args()

    man = load_manifest()
    docs = args.docs or man.get("_docs", []) if isinstance(man.get("_docs"), list) else args.docs
    if not docs:
        docs = ["文档/其他/预印本/预印本-v0.1.md"]

    if args.list:
        return cmd_list({k: v for k, v in man.items() if not k.startswith("_")})
    if args.scan:
        return cmd_scan(docs)
    return cmd_check({k: v for k, v in man.items() if not k.startswith("_")}, docs)


if __name__ == "__main__":
    sys.exit(main())
