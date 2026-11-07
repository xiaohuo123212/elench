#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""grammar_export.py —— 从实际代码**导出用到的语法子集**（T-14）

## 它回答什么

> 要读懂/复刻这个模块，**必须实现哪些构造**？——而不是"这门语言一共有多少构造"。

这对"把 elench 嵌进别的仓库"有用：对方只要实现**实际用到的 47 种节点**，
不用一次实现整门语言。

## 闭环在哪里

导出的语法文件里，每个构造写成一行 `productions:`。
`--check` 会**重新扫一遍代码**，把出现的构造与文件里声明的比：

| 情形 | 结果 |
|---|---|
| 代码里出现、语法文件里没声明 | **exit 1**（闭环被打破：语法描述过期了） |
| 语法文件里声明、代码里没出现 | 只提示（多声明无害） |

**为什么是"构造级"而不是"token 级"**：真做 token 级语法校验等于**重写一个解析器**，
收益不抵成本。构造级已经能回答"要复刻什么"，而且**能被自动检查**——
一个不能自动检查的语法描述，过两周就过期了。

## 用法

    python tools/grammar_export.py sum                  # 打印语法
    python tools/grammar_export.py sum --out=sum.ebnf   # 写文件
    python tools/grammar_export.py sum --check=sum.ebnf # 校验闭环
"""
import os
import sys

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(R, "src"))

from parser import parse_source        # noqa: E402
from ast_util import descendants       # noqa: E402

#: 节点分类（只用于输出分组，**不影响检查**）
GROUPS = (
    ("item", ("fn", "spec", "struct", "enum", "const", "use", "error-decl", "extern",
              "benchmodel", "bench-data", "bench-scale")),
    ("decl", ("params", "param", "type-name", "type-ref", "type-fn", "type-fn-params",
              "type-params", "type-param", "type-slice")),
    ("clause", ("spec-body", "clause", "dim", "slot", "binding", "candidate",
                "choose", "choose-clause", "contract")),
    ("stmt", ("block", "var-decl", "const-decl", "assign", "assign-op", "if", "while",
              "for", "match", "arm", "return", "break", "continue", "expr-stmt",
              "unsafe", "dispatch", "scope-stmt")),
    ("expr", ("lit-int", "lit-float", "lit-string", "lit-char", "lit-bool", "path",
              "binary", "unary", "call", "method-call", "field", "index", "args",
              "struct-literal", "field-init", "tuple", "paren", "try", "cast",
              "slice", "range")),
)


def load(module: str):
    """读 `<模块>.tu` + `<模块>.eln`（有就一起）。找不到就抛。"""
    outs = []
    for ext in (".tu", ".eln"):
        for base in (module, f"文档/样例/{module}"):
            p = base + ext if base.endswith(module) else base
            p = (base if base.endswith(ext) else base + ext)
            if os.path.exists(p):
                outs.append(io_read(p))
                break
    if not outs:
        raise SystemExit(f"  找不到模块 {module}（试过 .tu / .eln 与 文档/样例/）")
    return outs


def io_read(p: str) -> str:
    import io
    return io.open(p, encoding="utf-8").read()


def used_kinds(sources):
    out = set()
    for src in sources:
        mod = parse_source(src, "m").module
        if mod is None:
            continue
        for n in descendants(mod, None) if False else _walk(mod):
            out.add(n)
    return out


def _walk(node):
    stack = [node]
    while stack:
        x = stack.pop()
        yield getattr(x, "kind", "?")
        for c in (getattr(x, "children", None) or []):
            if c is not None:
                stack.append(c)


def render(kinds) -> str:
    """按 GROUPS 分组输出；**没归类的构造单独列一节**（而不是悄悄丢掉）。"""
    owner = {}
    for name, members in GROUPS:
        for m in members:
            owner[m] = name
    lines = ["# 由 tools/grammar_export.py 生成 —— **构造级**语法子集",
             "# 每一行声明一个实际用到的节点；`--check` 会验证它没过期。",
             "",
             f"productions: {len(kinds)}",
             ""]
    for name, _ in GROUPS:
        got = sorted(k for k in kinds if owner.get(k) == name)
        if not got:
            continue
        lines.append(f"# {name}")
        lines.extend("  " + k for k in got)
        lines.append("")
    unknown = sorted(k for k in kinds if k not in owner)
    if unknown:
        lines.append("# 未归类（新构造：请补进 GROUPS，否则每次导出都会落到这里）")
        lines.extend("  " + k for k in unknown)
        lines.append("")
    return "\n".join(lines)


def declared_in(text: str):
    out = set()
    for line in text.split("\n"):
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("productions:"):
            out.add(s)
    return out


def main(argv) -> int:
    module = None
    out_path = None
    check_path = None
    for a in argv:
        if a.startswith("--out="):
            out_path = a.split("=", 1)[1]
        elif a.startswith("--check="):
            check_path = a.split("=", 1)[1]
        elif not a.startswith("--"):
            module = a
    if not module:
        print("用法: grammar_export.py <模块> [--out=文件] [--check=文件]")
        return 2
    sources = load(module)
    kinds = used_kinds(sources)
    text = render(kinds)

    if check_path:
        if not os.path.exists(check_path):
            print(f"  ✗ 语法文件不存在：{check_path}")
            return 1
        old = declared_in(io_read(check_path))
        new = set(kinds) - old
        gone = old - set(kinds)
        if new:
            print(f"✗ 代码里出现了语法文件**没声明**的构造：{', '.join(sorted(new))}")
            print("  修法：重跑 --out 覆盖语法文件（并确认新构造确实该被用）")
            return 1
        print(f"  ✓ 闭环成立：{len(kinds)} 种构造全部有声明"
              + (f"（多声明 {len(gone)} 种，无害）" if gone else ""))
        return 0

    if out_path:
        import io
        io.open(out_path, "w", encoding="utf-8", newline="\n").write(text)
        print(f"  已写 {out_path}（{len(kinds)} 种构造）")
        return 0

    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
