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
order: [other, spec]
specs:
  - name: average
    head: "spec average(vector<u64>& scores) -> u64"
    clauses:
      - "effects {};"
      - "target { time: { max: O(n) }, };"
others:
  - "error ParenError;"
```

**`order` 是什么**：顶层条目的原文顺序（`spec` / `other` 各一个词）。
`specs` / `others` 两张表各自保持表内顺序，但**跨表顺序**只有 `order` 能表达；
没有它，`tu→yaml→tu'` 会把「`error` 在 `spec` 之前」渲染成「`spec` 在 `error`
之前」，AST 比较立刻不等（顶层声明是有序的）。

**为什么 clause 存原文而不是拆成结构化字段**：`.tu` 里 clause 形态很多
（`effects` / `target` / `requires` / `bench-model` / `choose` / kernel 子句…），
逐条转录能**无损**；硬拆成字段则每加一种 clause 就要改格式。

## 用法

    python tools/contract_yaml.py 模块 --to-yaml=out.contract.yaml
    python tools/contract_yaml.py 模块 --to-tu=out.tu
    python tools/contract_yaml.py 模块 --selfcheck      # 跑三条验收
    python tools/contract_yaml.py --selfcheck           # 对全部样例跑三条验收
"""
import io
import json
import os
import re
import sys

#: 中文 Windows 的控制台默认 GBK，打印 `✓` / `✗` 会 `UnicodeEncodeError`
#: 直接死在自检的**第一行**（`tests/runner.py` 也为此配过同一次）。
#: 自检结果是要给人看的，所以这里自己配 UTF-8，不依赖调用方的环境变量。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                   # noqa: BLE001
    pass

#: **已知不支持**：`tu→yaml→tu'` 的 AST 等价未过的模块。
#: 宁可显式列出，也不要让自检永远红——**一个永远失败的检查会被忽略**。
#:
#: 2026-11-09 起为空：`calculator` 当年被记成"枚举体里的 variant/field 切分
#: 没覆盖"，实际是**体内注释被拼行后吞掉后续子句**（`_strip_comment` 修的
#: 就是这个）；注释按行剥掉后它真过，按本文件的规矩从名单里删掉。
KNOWN_UNSUPPORTED = set()

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: spec 头（**拼成一行之后**再匹配，见 `parse_tu`）：
#: `spec 名字[<类型参数>](形参...) [-> 返回类型]`，到 spec 体的 `{` 为止。
#:
#: 2026-11-09 修：旧正则 `^\s*spec\s+(?P<head>spec\s+…)` **多写了一个 `spec`**，
#: 于是要求源文本里出现两次 "spec"——`HEAD_RE.match("spec probe(u64 n) -> u64 {")`
#: 恒为 None，17 个样例的 spec **一个都没被识别**，全部被当成「其他顶层语句」
#: 拍平成一段 token 串。往返自检因此一直在自洽地处理一个错误的中间表示。
#:
#: `->` 之后不能再用 `\S+`：返回类型里有空格（`array<Token, MAX_TOKENS>!`、
#: `(i64, u64)!`），`\S+` 会把 `{` 之前的部分截断。
HEAD_RE = re.compile(
    r"^\s*(?P<head>spec\s+[A-Za-z_]\w*"
    r"(?:\s*<[^<>]*>)?\s*\(.*?\)\s*(?:->\s*[^{}]+?)?)\s*\{")


def _first_word(s: str) -> str:
    m = re.match(r"\s*([A-Za-z_]\w*)", _no_noise(s))
    return m.group(1) if m else ""


def _no_noise_map(s: str):
    """`_no_noise` 的带下标版本：返回 (结构性字符串, 每个字符在 `s` 中的下标)。

    下标让 clause 切分能**回到原文**取片段：既按 `_no_noise` 的规则忽略
    注释与字面量（`'('` / `'}'` 不干扰括号计数），又不会丢掉被扫描的文字。
    """
    out, pos = [], []
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
        pos.append(i)
        i += 1
    return "".join(out), pos


def _no_noise(s: str) -> str:
    """去掉 `//` 注释与 `'x'` / `"x"` 字面量——**只留结构性字符**。

    样例里会写 `'('`、`'}'`，不剥掉会把括号计数带偏。
    """
    return _no_noise_map(s)[0]


def _norm(s: str) -> str:
    """空白归一（只压空白，不动内容）。"""
    return re.sub(r"\s+", " ", s).strip()


def _strip_comment(line: str) -> str:
    """剥掉**一行**里的 `//` 行注释；引号里的 `//` 不算注释。

    **为什么必须在拼行之前按行剥**：这个工具把多行子句 `\\n` 拼成一行
    再写回 `.tu`。若注释留在拼好的行里，`//` 会把它**后面同一行**的子句
    全吞掉——`tu→yaml→tu'` 的 AST 比较就会失败（体内注释的经典症状）。
    注释本来就不进 AST、也不属于契约（见文件头），所以在**读入**时丢掉
    是唯一正确的层：既保住子句文本，又让导出的 YAML 干干净净。
    """
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c in ("'", '"'):
            q = c
            i += 1
            while i < n and line[i] != q:
                i += 2 if line[i] == "\\" else 1
            i += 1
            continue
        if c == "/" and i + 1 < n and line[i + 1] == "/":
            return line[:i]
        i += 1
    return line


def _spec_name(head: str) -> str:
    """从 head 取函数名；泛型 `spec pick<T>(…)` 取 `pick`（`<…>` 不是名字）。"""
    m = re.match(r"\s*spec\s+([A-Za-z_]\w*)", head)
    return m.group(1) if m else ""


def _collect_spec_head(lines, i: int):
    """从第 `i` 行起收集 spec 头（**可能多行**），直到签名的 `{`。

    返回 `(头行列表, 花括号所在行号, 该行结构性串里 `{` 之后的下标)`；
    找不到 `{` 时行号为 -1。

    样例里普遍是 `spec evaluate(` 换行再写形参。**签名里不会有花括号**
    （泛型用 `<>`），注释与字符串里的花括号已被 `_no_noise` 去掉，
    所以「第一个结构性 `{`」就是 spec 体的开口。
    """
    buf = []
    j = i
    while j < len(lines):
        buf.append(lines[j])
        nn = _no_noise(lines[j])
        if "{" in nn:
            return buf, j, nn.index("{") + 1
        j += 1
    return buf, -1, -1


def _split_spec_body(lines, start_line: int, start_off: int):
    """从 spec 体的 `{` 之后开始，按**顶层 `;`**（花括号深度 1）切 clause。

    返回 `(clauses, 收尾 `}` 所在行号, `}` 之后的下标)`。每条 clause 是压过
    空白的原文，**保留结尾的 `;`**。下标让调用方把同一行 `}` 之后的顶层语句
    （`spec f() { … } error E;`）接着解析，而不是**静默丢掉**。

    为什么按「深度 + 字符」而不是「行尾是不是 `;`」：

    * `benchmodel` / `choose` / `target` 都以 `;` 结束，但 `;` 可能**单独占
      一行**（多行 `target` 的 `}` 与 `;` 分在两行）——旧实现只看行尾，
      于是把整个 `target` 和后面的 clause 粘成一条，`tu→yaml→tu'` 的 AST
      就不等了（这是只修正则、9 个样例立刻红掉的根因）。
    * 体内的 `;`（`w = 32;`、`objective time;`）在深度 ≥ 2，绝不能切。
    * 同一行写多条 clause（`spec f() { effects {}; target {…}; }`）也要能切。

    注释已按行剥掉；字符串/字符字面量由 `_no_noise_map` 跳过，所以
    `'('` / `'}'` / `' '` 不影响深度与分号判定。
    """
    clauses = []
    parts = []
    depth = 1
    i = start_line
    off = start_off
    seg = 0
    while i < len(lines):
        line = lines[i]
        nn, pos = _no_noise_map(line)
        if i == start_line and off > 0:
            seg = pos[off - 1] + 1        # 从 `{` 之后取，头不进 clause
        while off < len(nn):
            c = nn[off]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    parts.append(line[seg:pos[off]])
                    txt = _norm(" ".join(parts))
                    if txt:
                        clauses.append(txt)
                    return clauses, i, pos[off] + 1
            elif c == ";" and depth == 1:
                parts.append(line[seg:pos[off] + 1])
                txt = _norm(" ".join(parts))
                if txt:
                    clauses.append(txt)
                parts = []
                seg = pos[off] + 1
            off += 1
        parts.append(line[seg:])
        i += 1
        off = 0
        seg = 0
    # 体没闭合：把已收集的原文交出去，AST 比较会报错（不静默吞掉）
    txt = _norm(" ".join(parts))
    if txt:
        clauses.append(txt)
    return clauses, i, None


def parse_tu(text: str):
    """把 `.tu` 拆成 specs（head + clauses）与 others（其余顶层语句原文）。

    **注释在切分前按行剥掉**（`_strip_comment`）：后面会把多行 `" ".join(...)`
    拼成一行，注释若留到那时就会吞掉同一行的后续子句。注释不进 AST，也不属于
    契约，所以剥掉不影响语义。

    **spec 头可以跨行**：先收集到第一个结构性 `{` 为止、拼成一行再喂 `HEAD_RE`，
    然后从那里按括号深度切 clause。旧实现用单行正则匹配，多行头全部落进
    「其他语句」分支——修正则后那 9 个样例仍然不等，就是因为头没接上。

    返回 `(specs, others, order)`；`order` 记顶层条目的**原文顺序**
    （`("spec", 下标)` / `("other", 下标)`），`emit_tu` 靠它还原交错顺序。
    """
    lines = [_strip_comment(raw) for raw in text.split("\n")]
    if lines and lines[0].startswith("\ufeff"):     # 带 BOM 的文件，先摘掉
        lines[0] = lines[0][1:]
    specs, others, order = [], [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        # **跳过"去噪后为空"的行**：`//` 注释、空行。
        if not s or not _no_noise(s).strip():
            i += 1
            continue
        if re.match(r"spec\b", s):
            head_lines, body_line, body_off = _collect_spec_head(lines, i)
            head_txt = _norm(" ".join(head_lines))
            m = HEAD_RE.match(head_txt)
            if m is None or body_line < 0:
                # **不静默拍平**：那正是这个 bug 藏了三天的原因。
                raise ValueError(f"认不出 spec 头（第 {i + 1} 行）：{s}")
            head = _norm(m.group("head"))
            clauses, end_line, after = _split_spec_body(lines, body_line, body_off)
            specs.append({"name": _spec_name(head), "head": head,
                          "clauses": clauses})
            order.append(("spec", len(specs) - 1))
            if after is None:
                i = end_line                       # 体没闭合/到文件尾
            else:
                # 同一行 `}` 之后还有顶层语句时，把它留给下一轮（不丢 token）
                lines[end_line] = lines[end_line][after:]
                i = end_line
            continue
        # 顶层非 spec 的 item：**先看头关键字，再决定怎么收**。
        # `enum` / `struct` 有花括号体，**体里没有分号**——"吃到分号"会把
        # 后面的 spec 一起吞掉（`merge` 的 `spec earlier` 就是这么消失的）。
        kw = (_first_word(s) or "")
        braced = kw in ("enum", "struct") and "{" in _no_noise(s)
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
            order.append(("other", len(others) - 1))
    return specs, others, order


def _default_order(specs, others):
    """没有 `order` 时的后退顺序：spec 在前、others 在后（旧格式的行为）。"""
    return ([("spec", i) for i in range(len(specs))]
            + [("other", i) for i in range(len(others))])


def _order_line(order) -> str:
    return "order: [" + ", ".join(k for k, _ in order) + "]"


def _complete_order(order, specs, others):
    """补全 `order`：手工编辑过的 YAML 漏了条目时，缺的按表内顺序接在后面。

    这样「少写一行 order」只会退化成旧行为，不会**静默丢声明**。
    越界与重复项一律忽略（防手改 YAML 引入 IndexError / 重复输出）。
    """
    out, seen = [], set()
    for k, i in order:
        n = len(specs) if k == "spec" else len(others) if k == "other" else 0
        if not (0 <= i < n) or (k, i) in seen:
            continue
        seen.add((k, i))
        out.append((k, i))
    out += [("spec", i) for i in range(len(specs)) if ("spec", i) not in seen]
    out += [("other", i) for i in range(len(others)) if ("other", i) not in seen]
    return out


def emit_yaml(module: str, specs, others, order=None) -> str:
    if order is None:
        order = _default_order(specs, others)
    out = ["# elench contract v1 —— 由 tools/contract_yaml.py 生成",
           "# 改契约请改 .tu；这个文件是**导出物**。",
           "contract: v1",
           f"module: {module}",
           _order_line(order),
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
    specs, others, order = [], [], []
    have_order = False
    n_spec = n_other = 0
    cur = None
    section = None
    for raw in text.split("\n"):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("module:"):
            module = s.split(":", 1)[1].strip()
        elif s.startswith("order:"):
            have_order = True
            body = s.split(":", 1)[1].strip()
            if body.startswith("[") and body.endswith("]"):
                body = body[1:-1]
            for part in body.split(","):
                kind = part.strip()
                if kind == "spec":
                    order.append(("spec", n_spec))
                    n_spec += 1
                elif kind == "other":
                    order.append(("other", n_other))
                    n_other += 1
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
    if not have_order:
        order = _default_order(specs, others)
    return module, specs, others, order


def emit_tu(specs, others, order=None) -> str:
    """规范形式：4 空格缩进，clause 一行一条，**不含注释**。

    顶层条目按 `order` 还原交错顺序；不传时退回「spec 在前、others 在后」。
    """
    if order is None:
        order = _default_order(specs, others)
    order = _complete_order(order, specs, others)
    out = []
    for kind, idx in order:
        if kind == "spec":
            sp = specs[idx]
            out.append(sp["head"] + " {")
            for c in sp["clauses"]:
                out.append("    " + c)
            out.append("}")
        else:
            out.append(others[idx])
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
    specs, others, order = resolve(tu_path)
    yaml1 = emit_yaml("m", specs, others, order)

    # `yaml → tu → yaml`：**真的经过 tu**（旧版这里只做了 yaml→yaml，
    # 名字与内容不符——tu 那一步从没被验过）。
    _, s2, o2, ord2 = parse_yaml(yaml1)
    tu2 = emit_tu(s2, o2, ord2)
    yaml2 = emit_yaml("m", *parse_tu(tu2))

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


def selfcheck_all() -> int:
    """没给模块时：对 `文档/样例/*.tu` **全部**跑三条验收。

    验收命令 `python -B tools/contract_yaml.py --selfcheck` 不带模块，指的就是
    「全部样例」；以前这里只打印用法（退出码 2），等于没有这个入口。
    """
    d = os.path.join(R, "文档", "样例")
    names = sorted(f[:-3] for f in os.listdir(d) if f.endswith(".tu"))
    if not names:
        print(f"  找不到任何样例：{d}")
        return 2
    bad = []
    for m in names:
        print(f"[{m}]")
        if selfcheck(os.path.join(d, m + ".tu")) != 0:
            bad.append(m)
    print()
    if bad:
        print(f"失败 {len(bad)} 个：" + "、".join(bad))
        return 1
    print(f"全部通过：{len(names)} 个样例")
    return 0


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
        if do_check:
            return selfcheck_all()
        print("用法: contract_yaml.py <模块> [--to-yaml=文件] [--to-tu=文件] [--selfcheck]")
        return 2
    tu_path = find_tu(module)
    if do_check:
        return selfcheck(tu_path)
    specs, others, order = resolve(tu_path)
    base = os.path.basename(tu_path).rsplit(".", 1)[0]
    if to_yaml:
        io.open(to_yaml, "w", encoding="utf-8", newline="\n").write(
            emit_yaml(base, specs, others, order))
        print(f"  已写 {to_yaml}（{len(specs)} 个 spec，{len(others)} 条其他语句）")
        return 0
    if to_tu:
        io.open(to_tu, "w", encoding="utf-8", newline="\n").write(
            emit_tu(specs, others, order))
        print(f"  已写 {to_tu}（规范形式，**不含注释**）")
        return 0
    sys.stdout.write(emit_yaml(base, specs, others, order))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
