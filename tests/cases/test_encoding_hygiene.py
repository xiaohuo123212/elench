#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_encoding_hygiene.py —— 入库的文本文件必须是 **UTF-8 无 BOM**。

## 为什么必须有这条测试（2026-11-18 审计发现）

`AGENTS.md` 写了"UTF-8 no BOM"，但仓库里**已经攒了 51 个违规文件**：

- **50 个 UTF-8 BOM**：`tools/` 14 个、`tests/cases/` 29 个、`demo/corpus/` 6 个、
  `corpus/adversarial/` 2 个。来源基本是 PowerShell 5 的 `Set-Content -Encoding utf8`
  （它会偷偷加 BOM）——也就是说**不是有意为之，是工具习惯的沉淀**。
- **1 个 UTF-16**：`audit-numbers.json`（`ff fe` 开头）。它连 `json.load` 都读不了，
  而且内容是 33 文件 / 58 测试（当年的快照），早就过期。已删除。

BOM 的危害不在于"读不了"（词法层会剥掉它，见 `src/lexer.py`），而在于：
`git diff` 第一行永远显示被改动、`grep` 命中行首多一个不可见字符、
不同工具链对 BOM 的处理不一致（`json.load` 直接报错）。

## 这条测试怎么判定

只扫 **git 跟踪的**文本文件（`git ls-files`），按扩展名白名单过滤，
然后逐字节看前 3 字节有没有 `EF BB BF`，以及能不能按 UTF-8 解码。
二进制产物（`.pyc` / `.whl` / 图片）不在白名单里，不会误报。
"""
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))

#: 只有这些扩展名按文本检查（其余可能是二进制，不当文本读）
TEXT_EXT = (".py", ".md", ".json", ".toml", ".cff", ".yml", ".yaml", ".txt",
            ".sh", ".js", ".ts", ".cpp", ".h", ".tu", ".eln", ".cfg", ".ini")


def tracked() -> list:
    r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8")
    if r.returncode != 0:
        return []
    return [f.strip() for f in r.stdout.split("\n") if f.strip()]


def main() -> int:
    files = [f for f in tracked() if f.lower().endswith(TEXT_EXT)]
    bad = []
    for f in files:
        p = os.path.join(ROOT, f)
        if not os.path.isfile(p):
            continue
        with open(p, "rb") as fh:
            b = fh.read()
        if b.startswith(b"\xef\xbb\xbf"):
            bad.append((f, "UTF-8 BOM"))
        elif b.startswith(b"\xff\xfe") or b.startswith(b"\xfe\xff"):
            bad.append((f, "UTF-16 BOM（应存成 UTF-8）"))
        else:
            try:
                b.decode("utf-8")
            except UnicodeDecodeError as e:
                bad.append((f, "非 UTF-8：%s" % str(e)[:40]))

    if bad:
        print("FAIL  %d / %d 个入库文本文件不是「UTF-8 无 BOM」：" % (len(bad), len(files)))
        for f, why in bad[:12]:
            print("      %-46s %s" % (f, why))
        if len(bad) > 12:
            print("      … 还有 %d 个" % (len(bad) - 12))
        print("      修法：剥掉前 3 字节 BOM；UTF-16 的另存为 UTF-8。")
        print("      PowerShell 里别用 `Set-Content -Encoding utf8`（PS 5 会加 BOM）。")
        return 1

    print("check  ok    %d 个入库文本文件全部 UTF-8 无 BOM" % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
