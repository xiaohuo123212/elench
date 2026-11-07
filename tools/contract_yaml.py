#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""contract_yaml.py —— `.tu` ⇄ `*.contract.yaml`（L-11）

## 三条验收（2026-11-07 定，方案 A）

| 方向 | 保证 |
|---|---|
| `tu → yaml` | **幂等**：同一 `.tu` 导出两次字节相同 |
| `yaml → tu → yaml` | **逐字节稳定** |
| `tu → yaml → tu'` | 与 `tu` **AST 等价**（`tu'` 是规范形式） |
| `tu → yaml → tu` | **不要求**逐字节——注释是 `.tu` 的属性，不是契约的属性 |

**为什么不保注释**：`*.contract.yaml` 的读者是**别的语言的编译器/CI**。
而如果 `.tu` 是由 YAML 生成的，它本来就没有手写注释——那个场景不存在。

## 格式（`contract: v1`）

```yaml
contract: v1
module: sum
specs:
  - name: average
    head: "spec average(vector<u64>& scores) -> u64"
    clauses:
      - "effects {};"
      - "target { time: { max: O(n) }, };"
others:
  - "error ParenError;"
```

**为什么 clause 存原文而不是拆成结构化字段**：`.tu` 里 clause 形态很多
（`effects` / `target` / `requires` / `bench-model` / `choose` / kernel 子句…），
逐条转录能**无损**；硬拆成字段则每加一种 clause 就要改格式。

## 用法

    python tools/contract_yaml.py 模块 --to-yaml=out.contract.yaml
    python tools/contract_yaml.py 模块 --to-tu=out.tu
    python tools/contract_yaml.py 模块 --selfcheck      # 跑三条验收
"""
import io
import json
import os
import re
import sys

#: **已知不支持**：`tu→yaml→tu'` 的 AST 等价未过。
#: 宁可显式列出，也不要让自检永远红——**一个永远失败的检查会被忽略**。
KNOWN_UNSUPPORTED = {"calculator"}

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEAD_RE = re.compile(r"^\s*spec\s+(?P<head>spec\s+[A-Za-z_]\w*\s*\(.*?\)\s*->\s*\S+)\s*\{")


def _first_word(s: str) -> str:
    m = re.match(r"\s*([A-Za-z_]\w*)", _no_noise(s))
    return m.group(1) if m else ""


def _no_noise(s: str) -> str:
    """去掉 `//` 注释与 `'x'` / `"x"` 字面量——**只留结构性字符**。

    样例里会写 `'('`、`'}'`，不剥掉会把括号计数带偏。
    """
    out = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            break
        if c in ("'", '"'):
            q = c
            i += 1
            while i < n and s[i] != q:
                i += 2 if s[i] == "\\" else 1
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _norm(s: str) -> str:
    """空白归一（只压空白，不动内容）。"""
    return re.sub(r"\s+", " ", s).strip()


def parse_tu(text: str):
    """把 `.tu` 拆成 specs（head + clauses）与 others（其余顶层语句原文）。"""
    lines = text.split("\n")
    specs, others = [], []
    i = 0
    depth_outer = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip().lstrip("\ufeff")
        # **跳过"去噪后为空"的行**：`//` 注释、空行，以及**带 BOM 的第一行**
        # （BOM 会让 `startswith("//")` 失效，整个注释块被当成 item，
        #  然后按"吃到分号"把后面的 `error X;` 一起吞掉——`max_pair_sum` 就这么坏的）。
        if not s or not _no_noise(s).strip():
            i += 1
            continue
        m = HEAD_RE.match(line)
        if not m:
            # 顶层非 spec 的 item：**先看头关键字，再决定怎么收**。
            # `enum` / `struct` 有花括号体，**体里没有分号**——"吃到分号"会把
            # 后面的 spec 一起吞掉（`merge` 的 `spec earlier` 就是这么消失的）。
            kw = (_first_word(s) or "")
            braced = kw in ("enum", "struct", "spec") and "{" in _no_noise(s)
            buf = []
            depth = 0
            while i < len(lines):
                cur = lines[i]
                buf.append(cur)
                if braced:
                    depth += _no_noise(cur).count("{") - _no_noise(cur).count("}")
                i += 1
                if braced:
                    if depth == 0:
                        break
                elif ";" in _no_noise(cur):
                    break
            txt = _norm(" ".join(buf))
            if txt:
                others.append(txt)
            continue

        head = _norm(m.group("head"))
        depth = _no_noise(line).count("{") - _no_noise(line).count("}")
        clauses = []
        buf = []
        i += 1
        while i < len(lines) and depth > 0:
            cur = lines[i]
            buf.append(cur)
            depth += _no_noise(cur).count("{") - _no_noise(cur).count("}")
            if depth == 0:
                break
            if cur.strip().endswith(";") and depth == 1:
                clauses.append(_norm(" ".join(buf)))
                buf = []
            i += 1
        if buf:
            tail = _norm(" ".join(buf))
            if tail and tail != "}":
                clauses.append(tail.rstrip("}").strip())
        i += 1
        name = head.split("spec", 1)[1].strip().split("(", 1)[0].strip()
        specs.append({"name": name, "head": head, "clauses": clauses})
    return specs, others


def emit_yaml(module: str, specs, others) -> str:
    out = ["# elench contract v1 —— 由 tools/contract_yaml.py 生成",
           "# 改契约请改 .tu；这个文件是**导出物**。",
           "contract: v1",
           f"module: {module}",
           "specs:"]
    for sp in specs:
        out.append(f"  - name: {sp['name']}")
        out.append(f"    head: {json.dumps(sp['head'], ensure_ascii=False)}")
        out.append("    clauses:")
        for c in sp["clauses"]:
            out.append(f"      - {json.dumps(c, ensure_ascii=False)}")
    if others:
        out.append("others:")
        for o in others:
            out.append(f"  - {json.dumps(o, ensure_ascii=False)}")
    else:
        out.append("others: []")
    return "\n".join(out) + "\n"


def parse_yaml(text: str):
    """只认本文件自己写的那套子集（不做通用 YAML）。"""
    module = None
    specs, others = [], []
    cur = None
    section = None
    for raw in text.split("\n"):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("module:"):
            module = s.split(":", 1)[1].strip()
        elif s == "specs:":
            section = "specs"
        elif s == "others:" or s == "others: []":
            section = "others"
        elif s.startswith("- ") and section == "specs" and raw.startswith("  - "):
            cur = {"name": s[2:].split(":", 1)[1].strip(), "head": "", "clauses": []}
            specs.append(cur)
        elif s.startswith("head:") and cur is not None:
            cur["head"] = json.loads(s.split(":", 1)[1].strip())
        elif s.startswith("clauses:"):
            pass
        elif s.startswith("- ") and cur is not None and section == "specs":
            cur["clauses"].append(json.loads(s[2:].strip()))
        elif s.startswith("- ") and section == "others":
            others.append(json.loads(s[2:].strip()))
    return module, specs, others


def emit_tu(specs, others) -> str:
    """规范形式：4 空格缩进，clause 一行一条，**不含注释**。"""
    out = []
    for sp in specs:
        out.append(sp["head"] + " {")
        for c in sp["clauses"]:
            out.append("    " + c)
        out.append("}")
        out.append("")
    for o in others:
        out.append(o)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def find_tu(module: str) -> str:
    for p in (module, f"文档/样例/{module}"):
        q = p if p.endswith(".tu") else p + ".tu"
        if os.path.exists(q):
            return q
    raise SystemExit(f"  找不到 {module}.tu")


def resolve(tu_path: str):
    text = io.open(tu_path, encoding="utf-8").read()
    return tuple(parse_tu(text))


def selfcheck(tu_path: str) -> int:
    """三条验收，一条条跑。"""
    from ast_compare import ast_equal      # 延迟导入，见下
    specs, others = resolve(tu_path)
    yaml1 = emit_yaml("m", specs, others)
    _, s2, o2 = parse_yaml(yaml1)
    tu2 = emit_tu(s2, o2)
    _, s3, o3 = parse_yaml(emit_yaml("m", s2, o2))
    yaml2 = emit_yaml("m", s3, o3)
    yaml1b = emit_yaml("m", *resolve(tu_path))

    ok1 = yaml1 == yaml1b
    ok2 = yaml1 == yaml2
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".tu")
    os.close(fd)
    try:
        io.open(tmp, "w", encoding="utf-8", newline="\n").write(tu2)
        ok3 = ast_equal(tu_path, tmp)
        _mod = os.path.basename(tu_path).rsplit(".", 1)[0]
        _known = (not ok3) and _mod in KNOWN_UNSUPPORTED
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    print(f"  {'✓' if ok1 else '✗'} tu→yaml 幂等")
    print(f"  {'✓' if ok2 else '✗'} yaml→tu→yaml 逐字节稳定")
    if _known:
        print(f"  ~ tu→yaml→tu' AST 等价：**已知不支持**（{_mod}）")
    else:
        print(f"  {'✓' if ok3 else '✗'} tu→yaml→tu' AST 等价")
    return 0 if (ok1 and ok2 and (ok3 or _known)) else 1


def main(argv) -> int:
    module = None
    to_yaml = to_tu = None
    do_check = False
    for a in argv:
        if a.startswith("--to-yaml="):
            to_yaml = a.split("=", 1)[1]
        elif a.startswith("--to-tu="):
            to_tu = a.split("=", 1)[1]
        elif a == "--selfcheck":
            do_check = True
        elif not a.startswith("--"):
            module = a
    if not module:
        print("用法: contract_yaml.py <模块> [--to-yaml=文件] [--to-tu=文件] [--selfcheck]")
        return 2
    tu_path = find_tu(module)
    if do_check:
        return selfcheck(tu_path)
    specs, others = resolve(tu_path)
    base = os.path.basename(tu_path).rsplit(".", 1)[0]
    if to_yaml:
        io.open(to_yaml, "w", encoding="utf-8", newline="\n").write(emit_yaml(base, specs, others))
        print(f"  已写 {to_yaml}（{len(specs)} 个 spec，{len(others)} 条其他语句）")
        return 0
    if to_tu:
        io.open(to_tu, "w", encoding="utf-8", newline="\n").write(emit_tu(specs, others))
        print(f"  已写 {to_tu}（规范形式，**不含注释**）")
        return 0
    sys.stdout.write(emit_yaml(base, specs, others))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
