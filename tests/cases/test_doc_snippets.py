# -*- coding: utf-8 -*-
"""**文档里的代码必须能编译**——手写的片段没有机器验，就会慢慢变成谎话。

## 起因（真实事故，两次）

1. `文档/语法清单.md` 的遍历那行曾写 `for (u64 x : xs)`，而当时**解析器只认
   `for (x in xs)`**，这行从来编不过。2026-11-10 晚起 C++ range-for 已接受，
   而 `for (… in …)` 三种写法在当晚**整个删除**（与 C++ range-for 重合）。
   所以现在文档里只允许 `:` 形状；`in` 形状必须报 `E-For-InRemoved`。
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
#: 内建表的一行：`| \`名字\` | 参数 | \`返回\` | 成本 | 备注 |`
ROW = re.compile(r"^\| `([a-z0-9_]+)` \| [^|]+ \| `[^|]+` \| [^|]+ \| (.*) \|$")


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
            # **只测完整语句**（以 `{…}` 块或 `;` 结尾）。
            #
            # 判据不能只"以 for/while/if 开头"：`文档/C++惯用法对照.md` 里
            # 有一批**故意的反例**——`while (n --> 0)`、`while (cin >> x)`、
            # `for (i, x in xs)`（没有 `{…}` 的裸头）——它们本来就该编不过，
            # 那是文档在说"C++ 这套写法这里没有"。以前这条测试把它们当
            # "必须能编译"，于是整道门对**文档说真话**的部分报红。
            # 裸头不是一个语句，没法"包进函数体"验证，跳过它才是对的。
            if not (t.endswith("}") or t.endswith(";")):
                continue
            total += 1
            body = t.replace("…", "").replace("...", "")
            src = ("u64 _t(const vector<u64>& xs) {\n    "
                   + body + "\n    return 0;\n}\n")
            errs = errs_of(src)
            if errs:
                bad.append(f"{os.path.basename(p)} `{t}` → {errs[0].code}")
    check(not bad, f"{total} 个语句片段全部通过", "; ".join(bad[:4]))
    check(total >= 2, "真的检查到了东西（不是空跑）", str(total))


def test_builtins_doc_notes():
    """内建表的备注栏必须来自 `stdlib.py` 的 `empty=`，不能是空壳。

    2026-11-09 的回归：生成器把备注硬编码成 `""`——`text_find` 的「未找到 → -1」、
    `map_get` 的「未命中 → Option::None」等**几十条**语义在文档里蒸发，
    而 `gen_builtins_doc.py --check` 照样绿（它比的是"文档 == 生成结果"，两边一起错）。
    所以这里**直接对登记表**：每条非 `—` 的 `empty=` 都必须原样出现在它那一行。
    """
    print("[3] 内建函数表：每条 `empty=` 都进了备注栏")
    import stdlib  # noqa: E402  （sys.path 已在文件顶部指向 src）

    path = os.path.join(ROOT, "文档", "内建函数.md")
    rows = {}
    for line in open(path, encoding="utf-8").read().split("\n"):
        m = ROW.match(line)
        if m:
            rows[m.group(1)] = m.group(2)
    missing = [n for n in sorted(stdlib.FNS) if n not in rows]
    check(not missing, f"`stdlib.FNS` 的 {len(stdlib.FNS)} 个名字都在表里",
          ", ".join(missing[:6]))

    bad, want = [], 0
    for name in sorted(stdlib.FNS):
        e = (stdlib.FNS[name].get("empty") or "").strip()
        if e in ("", "—"):
            continue
        want += 1
        want_note = e.replace("->", "→")
        got = rows.get(name, "")
        if got != want_note:
            bad.append(f"`{name}` 备注是 `{got[:24]}`，应为 `{want_note[:24]}`")
    check(not bad, f"{want} 条 `empty=` 全部进了备注栏", "; ".join(bad[:4]))
    check(want >= 50, "真的检查到了东西（不是空跑）", str(want))


def test_regressions_are_named():
    print("[4] 把踩过的坑写成断言（这些写法**必须**编译失败 / 成功）")
    for label, src in (
        ("`for (u64 x in xs)` 不是合法遍历（`in` 已整个删除）",
         "u64 f(const vector<u64>& xs) {\n"
         "    for (u64 x in xs) { }\n    return 0;\n}\n"),
        ("`for (x in xs)` 已删除（与 C++ range-for 重合）",
         "u64 f(const vector<u64>& xs) {\n"
         "    for (x in xs) { }\n    return 0;\n}\n"),
        ("`for (i, x in xs)` 已删除（带下标的旧写法）",
         "u64 f(const vector<u64>& xs) {\n"
         "    for (i, x in xs) { }\n    return 0;\n}\n"),
        ("`for (i in 0..n)` 已删除（`..` 区间一并删）",
         "u64 f(u64 n) {\n"
         "    for (i in 0..n) { }\n    return 0;\n}\n"),
        ("`val` 是退役关键字", "u64 f() {\n    val x = 1;\n    return x;\n}\n"),
    ):
        errs = errs_of(src)
        check(bool(errs), f"{label} → 必须报 E-", str([d.code for d in errs]))

    # 三种 `in` 形状必须报到**同一条**码，且消息教替代写法。
    for label, src in (
        ("`for (x in xs)`", "u64 f(const vector<u64>& xs) {\n"
         "    for (x in xs) { }\n    return 0;\n}\n"),
        ("`for (i, x in xs)`", "u64 f(const vector<u64>& xs) {\n"
         "    for (i, x in xs) { }\n    return 0;\n}\n"),
        ("`for (i in 0..n)`", "u64 f(u64 n) {\n"
         "    for (i in 0..n) { }\n    return 0;\n}\n"),
    ):
        errs = errs_of(src)
        msg = "; ".join(d.message for d in errs)
        check("E-For-InRemoved" in [d.code for d in errs],
              f"{label} 报 E-For-InRemoved", [d.code for d in errs])
        check("for (auto& x : xs)" in msg and "for (u64 i = 0; i < n; i = i + 1)" in msg,
              f"{label} 的消息教替代写法", msg[:200])

    cpp_src = ("u64 f(const vector<u64>& xs) {\n"
               "    for (u64 x : xs) { }\n    return 0;\n}\n")
    check(not errs_of(cpp_src), "`for (u64 x : xs)` 是合法写法（C++ range-for）")
    ref_src = ("u64 f(const vector<u64>& xs) {\n"
               "    for (const auto& x : xs) { }\n    return 0;\n}\n")
    check(not errs_of(ref_src), "`for (const auto& x : xs)` 是合法写法（引用迭代）")


def main() -> int:
    test_blocks()
    test_inline_statements()
    test_builtins_doc_notes()
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
