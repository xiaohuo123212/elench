# -*- coding: utf-8 -*-
"""**文档里的代码必须能编译**——手写的片段没有机器验，就会慢慢变成谎话。

## 起因（真实事故，两次）

1. `文档/语法清单.md` 的遍历那行写的是 `for (u64 x : xs)`——**解析器只认 `for (x in xs)`**，
   这行从来编不过，而它是对外文档里被抄得最多的一行。
2. `文档/项目介绍.md` 的示例里有 `val idx = …`——`val` 是**退役关键字**（`E-Decl-OldKeyword`）。

两条都不是"写错了某个字"，是**文档与实现之间没有任何机器对账**。

## 这条测试检查什么

| 对象 | 要求 |
|---|---|
| ```` ```elench ```` 代码块 | 逐块丢给 `parse_source`，**不允许出现 `E-` 诊断** |
| 行内语句片段（`` `for (...)` `` / `` `while (...)` `` / `` `if (...)` ``） | 去掉 `…` 占位符后包进一个函数体，同样不允许 `E-` |

## 例外只有一个，而且必须写在代码里

纯语法模板（含 `name(params) -> RetType` 这类**占位符**）不参与编译检查。
标记方式：块的第一行写注释

    // doc-template: <为什么它不是真代码>

理由跟"清空单要写原因"一样：**例外必须可见、可审计**，不能靠测试里的一张手写清单
（那种清单会和文档一起腐烂）。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from parser import parse_source  # noqa: E402

_passed = 0
_failed = []


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


#: 文档目录（公开的，进仓库的那几份）
DOC_DIRS = [os.path.join(ROOT, "文档"), os.path.join(ROOT, "文档", "入门")]

BLOCK = re.compile(r"```elench[^\n]*\n(.*?)```", re.S)
SPAN = re.compile(r"`([^`\n]+)`")
TEMPLATE = re.compile(r"^\s*//\s*doc-template:\s*(\S.*)$", re.M)
STMT_START = re.compile(r"^(for|while|if)\s*\(")


def docs():
    out = []
    for d in DOC_DIRS:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith(".md"):
                out.append(os.path.join(d, f))
    return out


def errs_of(src: str):
    r = parse_source(src, "snippet.eln")
    return [d for d in r.diagnostics if d.code.startswith("E-")]


def test_blocks():
    print("[1] ```elench 代码块：逐块编译，不允许 E-")
    total = skipped = 0
    bad = []
    for p in docs():
        s = open(p, encoding="utf-8").read()
        for i, m in enumerate(BLOCK.finditer(s), 1):
            code = m.group(1)
            tm = TEMPLATE.search(code)
            if tm:
                skipped += 1
                print(f"  -- 跳过（模板）：{os.path.basename(p)} 第{i}块 —— {tm.group(1)}")
                continue
            total += 1
            errs = errs_of(code)
            if errs:
                first = code.strip().split("\n")[0][:50]
                bad.append(f"{os.path.basename(p)} 第{i}块 `{first}` → "
                           f"{errs[0].code}@{errs[0].line}:{errs[0].col}")
    check(not bad, f"{total} 个代码块全部通过（{skipped} 个模板跳过）", "; ".join(bad[:4]))
    check(total >= 5, "真的检查到了东西（不是空跑）", str(total))


def test_inline_statements():
    print("[2] 行内语句片段：`for (...)` / `while (...)` / `if (...)` 必须能编译")
    total = 0
    bad = []
    for p in docs():
        s = open(p, encoding="utf-8").read()
        for m in SPAN.finditer(s):
            t = m.group(1).strip()
            if not STMT_START.match(t):
                continue
            total += 1
            body = t.replace("…", "").replace("...", "")
            src = ("func _t(const vector<u64>& xs) -> u64 {\n    "
                   + body + "\n    return 0;\n}\n")
            errs = errs_of(src)
            if errs:
                bad.append(f"{os.path.basename(p)} `{t}` → {errs[0].code}")
    check(not bad, f"{total} 个语句片段全部通过", "; ".join(bad[:4]))
    check(total >= 2, "真的检查到了东西（不是空跑）", str(total))


def test_regressions_are_named():
    print("[3] 把踩过的两个坑写成断言（这两个写法**必须**编译失败）")
    for label, src in (
        ("`for (u64 x : xs)` 不是合法遍历", "func f(const vector<u64>& xs) -> u64 {\n"
                                            "    for (u64 x : xs) { }\n    return 0;\n}\n"),
        ("`val` 是退役关键字", "func f() -> u64 {\n    val x = 1;\n    return x;\n}\n"),
    ):
        errs = errs_of(src)
        check(bool(errs), f"{label} → 必须报 E-", str([d.code for d in errs]))

    ok_src = "func f(const vector<u64>& xs) -> u64 {\n    for (x in xs) { }\n    return 0;\n}\n"
    check(not errs_of(ok_src), "`for (x in xs)` 是合法写法（对照）")


def main() -> int:
    test_blocks()
    test_inline_statements()
    test_regressions_are_named()
    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
