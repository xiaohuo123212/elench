#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_doc_examples_current.py —— 文档里"从文件生成/复算"的数字不许漂。

## 为什么单独有这个测试

`doc_sync` 门只检查 `<!-- @include -->` 生成块与源文件是否一致；`doc_numbers` 只检查带
`<!--dn:-->` 绑定的数字。而这两类之外还有两种会悄悄漂的东西：

1. **手写的样例代码块**——`示例-三个构造.md` 里的 `search.tu` / `search.eln` 曾被手改过，
   与真文件对不上，门禁一声不响。
2. **紧度表里的实测数字**——`紧度-上界哪里松-2026-11-07.md` 的 9 个格子曾是 11-07 的快照，
   复算全对不上（`sum` 写 1.00，实测 2.70）。

这个测试各钉一条：生成块必须与源文件的 region **逐字相同**；紧度表抽三格必须与现跑一致。
"""
import io
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))

_passed = 0
_failed = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print("  ok   " + label)
    else:
        _failed += 1
        print("  FAIL " + label + (("  ← " + extra) if extra else ""))


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def region(text: str, name: str):
    """文件里 `#region name` 与 `#endregion name` 之间的行（不含标记行）。"""
    lines = text.split("\n")
    a = next((k for k, l in enumerate(lines) if ("#region " + name) in l), None)
    b = next((k for k, l in enumerate(lines) if ("#endregion " + name) in l), None)
    if a is None or b is None:
        return None
    return lines[a + 1:b]


def block_after(doc_lines, marker: str):
    i = next((k for k, l in enumerate(doc_lines) if marker in l), None)
    if i is None:
        return None
    o = next((k for k in range(i, len(doc_lines)) if doc_lines[k].startswith("```")), None)
    c = next((k for k in range(o + 1, len(doc_lines)) if doc_lines[k].startswith("```")), None)
    if o is None or c is None:
        return None
    return doc_lines[o + 1:c]


def main() -> int:
    # ---------------- ① 生成的代码块与源文件 region 一致 ----------------
    doc = read("文档", "样例", "示例-三个构造.md").split("\n")
    tu = read("文档", "样例", "search.tu")
    eln = read("文档", "样例", "search.eln")

    # `choose` 段没有嵌套标记，应当**逐字相同**
    got = block_after(doc, "search.tu#choose")
    want = region(tu, "choose")
    check(got is not None and want is not None and got == want,
          "`示例-三个构造` 的 choose 块与 search.tu#choose 逐字相同",
          "%r vs %r" % ((got or [])[:1], (want or [])[:1]))

    # `spec` / `fn` 段：sync_docs 会把续行缩进规整成 4 空格、并丢掉嵌套 region 标记，
    # 所以比较时把这两类差异归一化掉——**内容必须一致**。
    def norm(lines):
        out = []
        for l in lines or []:
            s = l.strip()
            if s.startswith("// #region") or s.startswith("// #endregion"):
                continue
            out.append(re.sub(r"\s+", " ", s))
        return out

    for marker, src, name in (("search.tu#spec", tu, "spec"),
                              ("search.eln#fn", eln, "fn")):
        got = block_after(doc, marker)
        want = region(src, name)
        check(norm(got) == norm(want),
              "`示例-三个构造` 的 %s 块内容与源文件一致（缩进/嵌套标记已归一）" % name,
              "行数 %d vs %d" % (len(got or []), len(want or [])))

    # ---------------- ② 紧度文档的数字与现跑一致 ----------------
    doc_t = read("文档", "紧度-上界哪里松-2026-11-07.md")
    check("tools/tightness_table.py" in doc_t and "2026-11-25" in doc_t,
          "紧度文档写明了「何时、怎么测」", "文首缺少复测说明")

    def table(mod):
        r = subprocess.run([sys.executable, "-B",
                            os.path.join(ROOT, "tools", "tightness_table.py"),
                            os.path.join("文档", "样例", mod)],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        rows = {}
        for line in (r.stdout or "").split("\n"):
            m = re.match(r"\s*\|\s*([\w\-]+)\s*\|\s*(\d+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|", line)
            if m:
                rows[(m.group(1), int(m.group(2)))] = (float(m.group(3)), float(m.group(4)))
        return rows

    for mod, n, kap, ops in (("sum", 64, 925, 342), ("insertion", 64, 111159, 29486)):
        rows = table(mod)
        hit = [v for k, v in rows.items() if k[1] == n]
        if not hit:
            check(False, "紧度表 %s n=%d 有数据" % (mod, n), "表里没有这个规模")
            continue
        k2, o2 = hit[0]
        check(abs(k2 - kap) < 1 and abs(o2 - ops) < 1,
              "紧度表 %s n=%d 与现跑一致（κ=%d / 实测=%d）" % (mod, n, kap, ops),
              "现跑 %s/%s" % (k2, o2))

    print()
    print("  %s  通过 %d，失败 %d" % ("全部通过" if _failed == 0 else "有失败", _passed, _failed))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
