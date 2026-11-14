#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
sync_docs.py —— 文档示例注入（杜绝"同一段代码抄 7 遍"）

问题
----
同一个示例在 7 份文档里各存一份，于是 `u8` 与 `u64` 并存、宿主名有
`average` / `is_pass` / `sum` 三种。**改一次要改 7 处，漏一处就是自相矛盾。**

做法
----
文档里不存副本，只写引用；内容由本脚本从**权威样例**注入：

    <!-- @include 文档/样例/search.tu#spec -->
    <!-- @end -->

样例文件里对应位置写区域标记：

    // #region spec
    spec binary_search(...) { ... } end spec
    // #endregion spec

用法
----
  python sync_docs.py             # 就地同步
  python sync_docs.py --check     # 只检查是否过期（过期返回 1）
  python sync_docs.py --list      # 列出文档里的所有 include 与对应区域

设计约束
--------
1. **只改两条标记之间的内容**，标记之外一个字都不动。
2. **围栏自动补**：标记写在围栏外时，注入内容自动包进 ``` 围栏；写在围栏内则原样注入。
3. **嵌套区域标记不注入**：`#region spec` 里套的 `#region choose` 两行会被跳过。
4. **未闭合 / 找不到区域 → 报错并跳过该文件**（不猜、不半改）。
5. **幂等**：同步两次结果完全相同。
6. **`_` 开头的文件不处理**（标准文件里会举 include 的写法，那是文档内容不是指令）。
   **`_` 开头的目录同样不处理**：`_probe/`、`_incoming/`、`_tier_a/` 是代理留下的
   临时树，里面的 `.md` 是旧快照，不是仓库内容；扫它们只会把别人的脚手架当成文档。
7. **仓库根目录也算**（README.md 在根上，不在 `文档/` 里）。
8. **源缺失 = 失败**：`--check` 下「过期」与「include 解析失败」都返回 1。
   以前只在「有过期」时返回 1，源文件被删后错误只打到 stderr、退出码仍是 0，
   于是生成器明明知道文档坏了，CI 却看不见（2026-11-10 的 `Vec<u64>` 事故）。
"""

import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))

INCLUDE_RE = re.compile(r"^\s*<!--\s*@include\s+(\S+?)#([\w.-]+)\s*-->\s*$", re.MULTILINE)
END_RE = re.compile(r"^\s*<!--\s*@end\s*-->\s*$", re.MULTILINE)
REGION_LINE = re.compile(r"#\s*(?:end)?region\b")
SKIP_DIRS = {".git", "node_modules", ".perf", "_internal"}


def read(p):
    return io.open(p, encoding="utf-8").read()


def extract_region(path: str, region: str):
    """取出 #region <id> 与 #endregion <id> 之间的行；嵌套的 region 标记行跳过。"""
    if not os.path.exists(path):
        return None, f"文件不存在：{path}"
    lines = read(path).split("\n")
    start = end = -1
    for i, line in enumerate(lines):
        if re.search(r"#region\s+" + re.escape(region) + r"\b", line):
            start = i
        elif re.search(r"#endregion\s+" + re.escape(region) + r"\b", line):
            if start >= 0:
                end = i
                break
    if start < 0 or end < 0:
        return None, f"区域未找到：{region}"
    body = [l for l in lines[start + 1:end] if not REGION_LINE.search(l)]
    return "\n".join(_narrow(body)), None


def _narrow(body):
    """**只影响显示**：把「上一行以 `(` 结尾」的续行改成 4 空格悬挂缩进。

    动机：签名按括号对齐（`spec average(` 后面缩进十几格）在**窄页面**里很难看、
    也显得代码很长。悬挂缩进同样是合法风格（`dsh fmt` 两种都接受），
    但读起来更紧凑。**只改注入到文档里的文本，样例文件不动。**
    """
    out = []
    prev_open = False
    for ln in body:
        if prev_open and ln.strip() and ln[:1] in (" ", "\t"):
            out.append("    " + ln.strip())
        else:
            out.append(ln)
        prev_open = ln.rstrip().endswith("(")
    return out


def indent_of(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]


def sync_file(path: str, check_only: bool):
    """返回 (改动数, 错误列表)。"""
    lines = read(path).split("\n")
    out = []
    changed = 0
    errors = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            in_fence = not in_fence

        m = INCLUDE_RE.match(line)
        if not m:
            out.append(line)
            i += 1
            continue

        target, region = m.group(1), m.group(2)
        src = os.path.normpath(os.path.join(os.path.dirname(path), target))
        body, err = extract_region(src, region)
        indent = indent_of(line)

        j = i + 1
        while j < len(lines) and not END_RE.match(lines[j]):
            if INCLUDE_RE.match(lines[j]):
                errors.append(f"{os.path.basename(path)}:{i+1} include 未闭合就遇到下一个 include")
                break
            j += 1
        if j >= len(lines):
            errors.append(f"{os.path.basename(path)}:{i+1} include 没有对应的 @end")
            out.extend(lines[i:])
            break
        if err:
            errors.append(f"{os.path.basename(path)}:{i+1} {err}")
            out.extend(lines[i:j + 1])
            i = j + 1
            continue

        rendered = [indent + l if l.strip() else "" for l in body.split("\n")]
        if not in_fence:
            # 标记在围栏外 → 自动包一层，保证渲染成代码块
            rendered = [indent + "```"] + rendered + [indent + "```"]

        out.append(line)
        current = lines[i + 1:j]
        if current != rendered:
            changed += 1
        out.extend(rendered)
        out.append(lines[j])
        i = j + 1

    if changed and not check_only:
        io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out))
    return changed, errors


#: 归档目录：**历史冻结区，不参与"示例同步"检查**。
#: 理由：归档件被移动过，它们里面的相对链接（`样例/search.eln`）
#: 在原目录下是对的、在归档目录下就断了；而归档件的价值在于"当时的原样"，
#: 不是"链接现在还能不能点"。要让链接仍可用，就在件内改成绝对相对路径。
ARCHIVE_DIRS = {"归档"}


def md_files():
    for dirpath, dirnames, files in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and d not in ARCHIVE_DIRS
                       and not d.startswith("_")]
        for f in files:
            if f.endswith(".md") and not f.startswith("_"):
                yield os.path.join(dirpath, f)


def main(argv):
    check_only = "--check" in argv
    listing = "--list" in argv

    total_changed = 0
    all_errors = []
    touched = []

    for p in sorted(md_files()):
        text = read(p)
        if "@include" not in text:
            continue
        if listing:
            for m in INCLUDE_RE.finditer(text):
                print(f"  {os.path.relpath(p, ROOT)}  →  {m.group(1)}#{m.group(2)}")
            continue
        changed, errors = sync_file(p, check_only)
        total_changed += changed
        all_errors.extend(errors)
        if changed:
            touched.append(os.path.relpath(p, ROOT))

    if listing:
        return 0

    for e in all_errors:
        print("  ✗ " + e, file=sys.stderr)

    if check_only:
        # 解析失败（源文件/区域不存在）与「内容过期」一样是红：否则生成器
        # 报错、退出码 0，CI 永远看不见文档已经撒谎。
        if touched or all_errors:
            if touched:
                print(f"✗ {len(touched)} 份文档的示例已过期：")
                for t in touched:
                    print("    " + t)
                print("  修：python src/sync_docs.py")
            if all_errors:
                print(f"✗ {len(all_errors)} 处 @include 解析失败（逐条见上）")
            return 1
        print("✓ 文档示例与权威样例一致")
        return 0

    if total_changed:
        print(f"✓ 已同步 {total_changed} 个代码块，涉及 {len(touched)} 份文档")
    else:
        print("✓ 无需改动")
    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
