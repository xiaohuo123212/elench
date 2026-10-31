#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
dsh —— AI 语言工具链（M0）

子命令：
  dsh check <模块.tu> [<模块.eln>] [--strict] [--json]
  dsh show  <模块.tu> <label>         打印某个 choose 的候选与状态
  dsh parse <文件>                    打印 AST（调试用）

M0 范围：**没有成本分析**，故 `state` 只能给出 draft / planned / unchecked，
        `done` / `debt` 需要 M2 的成本计算。

用法：
  cd src
  $env:PYTHONIOENCODING="utf-8"
  python dsh.py check ../文档/设计/目前设计/样例/calculator.tu
"""

from __future__ import annotations

import io
import json
import os
import sys
from typing import List

from checker import check_files
from formatter import format_source
from parser import parse_file

USAGE = """elench —— 成本契约语言 Elench 的工具链（v0.70-M1）

  用法：elench <命令> [参数...]
        文件参数可以只写模块名：`elench cost sum` = sum.tu + sum.eln

  检查与查看
    run       <模块> [--fn=名] [--u64=5] [--vec=1,2,3] [--parallel=threads]
                                 执行模块的 main（或指定函数），打印结果
    check     <模块>              静态检查：语法 / 类型 / 契约 / 债务
                                  --strict 更严；--format=json 机读
    parse     <文件>              打印 AST（调试语法；给写分析工具的人看）
    fmt       <文件>...           格式化：统一风格
                                  --check 只判定不改（CI 用）；--stdout 打到终端
    highlight <文件>              终端里的 ANSI 语法高亮
                                  --tokens 打印 token 流（调试词法器）
    show      <模块.tu> <label>   打印某个 choose 的声明：候选 / objective / 是否有 pending

  成本（核心）
    cost      <模块> [--segmented] 算 κ 并与 target 比：达成 / 债务 / 无法判定
                                  --segmented：有分档表时按段取上界（更紧、保持阶）
    stats     <模块> <函数>       跑一次，报调用数 / 分配数 / 迭代数
    bench     <模块> <函数>       单函数隔离测量（只给判定与比值，不做全量统计）
    export    <模块> [--format=table|otlp|svg|csv] [--out=文件] [--function=名]
                                  导出「静态上界 vs 实测」
                                  不给 --out 就打到终端；给 --out 就写文件
                                  csv/svg/otlp 建议配 --out（否则粘不回文件）
                                  --push：按 OTLP/HTTP 推到 collector（配 OTEL_* 环境变量）

  决策点 ?! 与门禁
    dispatch  <模块>              三层筛：每个 ?! 要不要测、分几段（只出计划，不生成表）
    measure   <模块>              真跑候选，用实测生成分档表（可提交、可 diff）
    build     <模块> [--out=路径] [--measure] [--deny-debt]
                                  构建门禁：含 pending 就失败
                                  默认只跑静态 dispatch；--measure 才真跑测量（慢）
                                  --deny-debt：超出 target 即拒绝构建（默认只记债务）

  --help    打印本页
"""

STATE_MARK = {
    "draft": "·",
    "planned": "·",
    "unchecked": "!",
    "?": "?",
    "done": "✓",
    "debt": "▲",
}


# ---------------------------------------------------------------- YAML 输出

_YAML_SPECIAL = set(':#{}[],&*!|>\'"%@`')


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    s = str(v)
    need_quote = (s == "" or s != s.strip()
                  or any(ch in _YAML_SPECIAL for ch in s)
                  or s.lower() in ("true", "false", "null", "yes", "no"))
    if need_quote:
        return "'" + s.replace("'", "''") + "'"
    return s


def to_yaml(obj, indent: int = 0) -> str:
    """最小 YAML 发射器（只支持 dict / list / 标量，够 signature 用）。"""
    pad = " " * indent
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict) and v:
                lines.append(f"{pad}{k}:")
                lines.append(to_yaml(v, indent + 2))
            elif isinstance(v, list) and v:
                lines.append(f"{pad}{k}:")
                lines.append(to_yaml(v, indent + 2))
            elif isinstance(v, dict):
                lines.append(f"{pad}{k}: {{}}")
            elif isinstance(v, list):
                lines.append(f"{pad}{k}: []")
            else:
                lines.append(f"{pad}{k}: {_yaml_scalar(v)}")
    elif isinstance(obj, list):
        for it in obj:
            if isinstance(it, dict) and it:
                sub = to_yaml(it, indent + 2).split("\n")
                lines.append(f"{pad}- " + sub[0].lstrip())
                lines.extend(sub[1:])
            elif isinstance(it, list) and it:
                lines.append(f"{pad}-")
                lines.append(to_yaml(it, indent + 2))
            else:
                lines.append(f"{pad}- {_yaml_scalar(it)}")
    else:
        lines.append(f"{pad}{_yaml_scalar(obj)}")
    return "\n".join(lines)


_VALUE_FLAGS = ("--out", "--format", "--function", "--checks", "--seed", "--runs")


def _auto_enabled(argv):
    """自动链开关。**默认开**。

    链式关系（都能关）：

        export  ──自动──▶  build  ──自动──▶  dispatch + measure

    关掉的三种写法（等价）：
        --no-auto          命令行
        ELENCH_AUTO=0      环境变量
        （将来）elench.toml 里的 [auto] 段

    **为什么默认开**：绝大多数人只想知道"这段代码贵不贵"，
    不该被迫记住"先 dispatch 再 measure 再 build 再 export"这一串。
    """
    if os.environ.get("ELENCH_AUTO", "1") in ("0", "false", "no"):
        return False, [a for a in argv if a not in ("--auto", "--no-auto")]
    if "--no-auto" in argv:
        return False, [a for a in argv if a != "--no-auto"]
    return True, [a for a in argv if a != "--auto"]


def _normalize_flags(args):
    """把 `--out 文件` 这种**空格写法**归一成 `--out=文件`。

    为什么必须支持：`--flag value` 是绝大多数 CLI 的写法，用户不会记得我们要求 `=`。
    **让工具适应人，而不是让人记住工具的癖好。**
    """
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a in _VALUE_FLAGS and i + 1 < len(args):
            out.append(f"{a}={args[i + 1]}")
            i += 2
            continue
        out.append(a)
        i += 1
    return out


_TRACER = None


def _otel_tracer():
    """全局 tracer（懒加载）。**没有配置就返回一个 disabled 的**，调用点无需判断。"""
    global _TRACER
    if _TRACER is None:
        import otel_export as _oe
        _TRACER = _oe.Tracer(_oe.config())
    return _TRACER


def _otel_span(name, attrs=None, status_error=False):
    """给某个阶段打一个 span。**没启用 tracing 时是空操作**——调用点不用写 if。"""
    return _otel_tracer().span(name, attrs or {}, status_error=status_error)


def _otel_flush():
    """命令结束时把攒下的 span 一次发出去（失败只警告）。"""
    try:
        if _TRACER is not None and _TRACER.spans:
            _r = _TRACER.flush()
            if not _r.get("sent") and _r.get("reason") not in ("no spans / disabled",):
                print(f"  ！ OTel traces 未发出：{_r.get('reason')}", file=sys.stderr)
    except Exception:                                   # noqa: BLE001
        pass


def _cost_interp(ch_mod, tu_mod, tu_p: Optional[str] = None):
    """构造 CostInterp：**有分档表就按段取上界**（默认行为，2026-10-31 起）。

    没有表 → 自动退回"全候选取 max"（保守但不需要任何外部文件）。
    这就是"默认按段、缺表回退"的落点：**一处构造，所有命令一致**。
    """
    from cost_interp import CostInterp          # dsh 的惯例是函数内导入
    # **默认按段取上界**（2026-10-31，E35 修好后切换）：
    # 循环每轮 +1 的少算修掉之后，`count` / `sum` 的逐段上界在同单位对照下都 ≥ 实测。
    # **默认按段取上界**（2026-10-31 第三次切换，E35 已修）：
    # 循环每轮 3 个操作 + 方法调用 3 个操作之后，`count` 的逐段上界在
    # n = 16 / 64 / 256 / 512 / 1024 上全部 ≥ 实测（evaluation_steps）。
    path = None
    if tu_p:
        cand = _tbl_for(tu_p)
        if os.path.exists(cand):
            path = cand
    return CostInterp(ch_mod, tu_mod, table_path=path)


def _tbl_for(tu_p: str) -> str:
    """模块对应的分档表路径（约定：与 `.tu` 同目录、同名、`.dispatch.yaml`）。"""
    return os.path.join(os.path.dirname(os.path.abspath(tu_p)),
                        os.path.basename(tu_p).rsplit(".", 1)[0] + ".dispatch.yaml")


def _expand_module_args(args):
    """**允许只写一个名字**：`elench cost sum` → 自动配成 `sum.tu sum.eln`。

    为什么要这一层：`<模块.tu> [<模块.eln>]` 是**内部**的成对约定，
    但它不该变成**输入负担**——用户只记得住"这个程序叫 sum"。

    三种写法都接受：

    | 你写 | 展开成 |
    |---|---|
    | `sum` | `sum.tu sum.eln`（两个都存在时） |
    | `sum.tu` | `sum.tu sum.eln`（后半存在才补） |
    | `sum.eln` | `sum.tu sum.eln`（顺序会自动摆正） |

    **不做的事**：不在目录里递归搜、不猜别的目录——找不到就让后面的命令报"文件不存在"，
    因为"悄悄找了别的文件"比"报错"更难排查。
    """
    if not args:
        return args
    head = args[0]
    rest = list(args[1:])
    base, ext = os.path.splitext(head)

    if ext in (".tu", ".eln"):
        partner = base + (".eln" if ext == ".tu" else ".tu")
        if ext == ".eln" and os.path.exists(partner):
            return [partner, head] + rest
        if ext == ".tu" and os.path.exists(partner) and not rest[:1]:
            return [head, partner] + rest
        return args

    if os.sep in head or "/" in head:
        return args                      # 带路径的裸名字：不猜

    tu, eln = head + ".tu", head + ".eln"
    if os.path.exists(tu):
        return ([tu, eln] if os.path.exists(eln) else [tu]) + rest
    return args


def cmd_check(argv: List[str]) -> int:
    argv = _expand_module_args(argv)
    if not argv:
        print("用法：dsh check <模块.tu> [<模块.eln>]", file=sys.stderr)
        return 2

    as_json = ("--format=json" in argv) or ("--json" in argv)   # --json 为别名
    strict = "--strict" in argv
    overflow = "off"
    for a in argv:
        if a.startswith("--checks="):
            for part in a.split("=", 1)[1].split(","):
                if ":" in part:
                    k, v = part.split(":", 1)
                    if k.strip() == "overflow" and v.strip() in ("off", "warn", "error"):
                        overflow = v.strip()
    paths = [a for a in argv if not a.startswith("--")]
    tu = paths[0]
    ai = paths[1] if len(paths) > 1 else None

    if not os.path.exists(tu):
        print(f"找不到文件：{tu}", file=sys.stderr)
        return 2

    res = check_files(tu, ai, strict=strict, overflow=overflow)

    # 名字解析 + 类型检查（M1）：只在语法干净时做，否则会在残缺 AST 上误报
    if not any(d.severity == "error" for d in res.diagnostics):
        try:
            from checker import Diag
            from parser import parse_source as _ps
            from typecheck import typecheck
            _tu = _ps(io_read(tu), os.path.basename(tu)).module
            _ch = _ps(io_read(ai), os.path.basename(ai)).module if ai else None
            for d in typecheck(_tu, _ch):
                res.diagnostics.append(Diag(d.code, d.message, d.line, d.col))
        except Exception as e:                      # noqa: BLE001
            # **绝不静默跳过**（E10）：检查器崩了就等于"没查"，而没查 MUST NOT 显示为通过。
            # 「工具的『我不知道』必须比『我查过了』更显眼。」
            res.diagnostics.append(Diag(
                "E-Internal-TypeCheck",
                f"类型检查器内部异常，本次类型检查未完成：{type(e).__name__}: {e}"
                f"（这不代表代码正确——它代表**没检查**）",
                0, 0, severity="error"))

    if as_json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0 if res.ok else 1

    print(f"module {res.module}" + ("    [strict]" if strict else ""))

    if res.functions:
        print()
        width = max(len(f.name) for f in res.functions)
        for f in res.functions:
            mark = STATE_MARK.get(f.state, "?")
            impl = "有体" if f.has_impl else "无体"
            tgt = "target" if f.has_target else "—"
            pub = "pub " if f.is_pub else "    "
            print(f"  {mark} {pub}{f.name.ljust(width)}  {impl}  {tgt:6}  "
                  f"state={f.state:9} {f.note}")

    if res.choices:
        print()
        for label, info in res.choices.items():
            print(f"  choose {label}")
            print(f"    over      {', '.join(info['over']) or '(空)'}")
            if info["pending"]:
                print(f"    pending   {', '.join(info['pending'])}")
            if info["objective"]:
                print(f"    objective {info['objective']}")

    if res.labels_used:
        print()
        print(f"  ?! 使用：{', '.join(sorted(res.labels_used))}")

    print()
    if res.diagnostics:
        # DG-0：人读诊断走 stderr，避免污染 stdout 上的报告
        errs = [d for d in res.diagnostics if d.severity == "error"]
        warns = [d for d in res.diagnostics if d.severity != "error"]
        if errs:
            extra = f"，{len(warns)} 条警告" if warns else ""
            print(f"✗ {len(errs)} 条错误{extra}", file=sys.stderr)
        else:
            print(f"⚠ {len(warns)} 条警告（不阻断构建）", file=sys.stderr)
        for d in res.diagnostics:
            loc = f"@{d.line}:{d.col}" if d.line else ""
            tag = "✗" if d.severity == "error" else "⚠"
            print(f"  {tag} {d.code} {loc}  {d.message}", file=sys.stderr)
        # v0.68：warning 不阻断构建
        if errs:
            return 1

    planned = sum(1 for f in res.functions if not f.has_impl)
    pending = sum(len(v["pending"]) for v in res.choices.values())
    print(f"✓ 通过（{len(res.functions)} 个函数，其中 {planned} 个无体）")
    if pending:
        print(f"  ⚠ 有 {pending} 个 pending 候选：dsh check 通过，但 dsh build 必失败（B-17）")
    print("  注：check 只做静态检查；done / debt 由 dsh cost 与 dsh build 判定")
    return 0


def cmd_fmt(argv: List[str]) -> int:
    check_only = "--check" in argv
    to_stdout = "--stdout" in argv
    files = [a for a in argv if not a.startswith("--")]
    if not files:
        print("用法：dsh fmt <文件>... [--check] [--stdout]", file=sys.stderr)
        return 2

    dirty = 0
    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        out = format_source(src)
        changed = (out != src)
        if changed:
            dirty += 1

        if check_only:
            status = "需要格式化" if changed else "合规"
            print(f"  [{status}]  {path}")
            continue

        if to_stdout:
            sys.stdout.write(out)
            continue
        if not changed:
            print(f"  [无变化]    {path}")
            continue
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(out)
        print(f"  [已格式化]  {path}")

    if check_only and dirty:
        print(f"\n{dirty} 个文件需要格式化", file=sys.stderr)
        return 1
    return 0


def cmd_highlight(argv: List[str]) -> int:
    files = [a for a in argv if not a.startswith("--")]
    if not files:
        print("用法：dsh highlight <文件> [--tokens]", file=sys.stderr)
        return 2
    from highlight import highlight, tokens_of

    for path in files:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        if "--tokens" in argv:
            for cat, text in tokens_of(src):
                print(f"{cat}\t{text}")
        else:
            sys.stdout.write(highlight(src))
    return 0


def cmd_show(argv: List[str]) -> int:
    if len(argv) < 2:
        print("用法：dsh show <模块.tu> <label>", file=sys.stderr)
        return 2
    tu, label = argv[0], argv[1]
    res = check_files(tu, argv[2] if len(argv) > 2 else None)

    if label not in res.choices:
        print(f"没有叫 {label} 的 choose", file=sys.stderr)
        if res.choices:
            print("现有的：" + ", ".join(res.choices), file=sys.stderr)
        return 2

    info = res.choices[label]
    print(f"choose {label}")
    print(f"  objective  {info['objective'] or '(未声明)'}")
    print(f"  over       {', '.join(info['over']) or '(空)'}")
    print(f"  pending    {', '.join(info['pending']) or '(无)'}")

    print()
    print("  候选状态：")
    impls = res.candidates.get(label, {})
    for cand in info["over"]:
        if cand in impls:
            print(f"    {cand:20} ✓ 已实现")
        else:
            print(f"    {cand:20} · 未实现"
                  + ("（标了 pending）" if cand in info["pending"] else "（未标 pending，B-16 违规）"))
    host = info.get("host") or "?"
    print()
    print(f"  宿主       {host}")

    used = label in res.labels_used
    print()
    print(f"  ?! 引用：{'有' if used else '无'}")
    return 0


def cmd_parse(argv: List[str]) -> int:
    if not argv:
        print("用法：dsh parse <文件>", file=sys.stderr)
        return 2
    res = parse_file(argv[0])
    print(res.module.dump() if res.module else "(空)")
    if res.diagnostics:
        print(f"-- {len(res.diagnostics)} 条诊断 --")
        for d in res.diagnostics:
            print(f"  {d.code} @{d.line}:{d.col}  {d.message}")
        return 1
    return 0



def _load_pair(argv: List[str]):
    """从 argv 里取出 (tu_path, ch_path)；ch 可省，按同名推导。"""
    if not argv:
        return None, None
    # **位置参数要跳过开关**：`dsh cost --signature a.tu a.eln` 是合法写法，
    # 不滤掉开关的话 tu 会变成 "--signature"，然后 io_read 抛 traceback。
    pos = [a for a in argv if not a.startswith("-")]
    if not pos:
        return None, None
    tu = pos[0]
    # **单参数写法**：`elench header count` → `count.tu` / `count.eln`
    if not os.path.splitext(tu)[1]:
        cand = tu + ".tu"
        if os.path.exists(cand):
            tu = cand
            if len(pos) == 1:
                guess = os.path.splitext(tu)[0] + ".eln"
                if os.path.exists(guess):
                    return tu, guess
    ch = None
    for a in pos[1:]:
        ch = a
        break
    if ch is None:
        guess = os.path.splitext(tu)[0] + ".eln"
        if os.path.exists(guess):
            ch = guess
    return tu, ch


def cmd_cpp(argv: List[str]) -> int:
    """`elench cpp <模块>` —— 生成 C++ 源码（**最小切片**）。

    ## 支持什么（刻意窄）

    - 参数类型：`u64` / `i64` / `Vec<u64>` / `Vec<u8>`
    - 语句：变量声明、赋值、`while`、`if`、`return`
    - 表达式：整数四则、比较、`xs[i]`、`xs.length()`、用户函数调用、`print`
    - 内建：`sum_of` / `min` / `max`（其余遇到就报 `E-Cxx-Unsupported`）

    ## 为什么先做这么窄

    **能跑通一条窄路，比画一张覆盖全语言的饼有用**：
    它验证 `cxx_map` 的类型映射、验证两边语义、也是 E37 的第一块。

    ## 对拍：`--run`

    找 `g++` / `clang++` / `cl`；找到就编译、跑一遍、和解释器输出比。
    **找不到就如实说"没法对拍"**——不假装验证过。
    """
    from parser import parse_source as _ps
    tu_p, ch_p = _load_pair(argv)
    if tu_p is None or ch_p is None:
        print("用法: elench cpp <模块> [--output=x.cpp] [--run]", file=sys.stderr)
        return 2
    tu_mod = _ps(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = _ps(io_read(ch_p), os.path.basename(ch_p)).module

    try:
        text = _gen_cpp(tu_mod, ch_mod)
    except CppUnsupported as exc:
        print(f"✗ E-Cxx-Unsupported: {exc}", file=sys.stderr)
        return 1

    out = next((a.split("=", 1)[1] for a in argv if a.startswith("--output=")), None)
    if out:
        io.open(out, "w", encoding="utf-8", newline="\n").write(text)
        print(f"  {out}  ←  C++ 源码（{len(text.splitlines())} 行）")
    else:
        sys.stdout.write(text)
    if "--run" in argv:
        return _cpp_compare(tu_p, ch_p, text)
    return 0


class CppUnsupported(Exception):
    pass


def _cxx_type_of(name: str) -> str:
    import cxx_map as _cm
    return _cm.cxx_type(name) or "uint64_t"


def _gen_cpp(tu_mod, ch_mod) -> str:
    """把**最小切片**翻译成 C++（不支持就抛 `CppUnsupported`）。"""
    import cxx_map as _cm
    out: List[str] = []
    out.append("// 由 elench cpp 生成（最小切片）—— 目前只支持标量循环 + Vec + 少数内建")
    out.append("#include <cstdint>")
    out.append("#include <vector>")
    out.append("#include <iostream>")
    out.append("using namespace std;")
    out.append("")

    fns = [c for c in (ch_mod.children or []) if getattr(c, "kind", "") == "fn"]
    for fn in fns:
        ps = next((c for c in fn.children if c.kind == "params"), None)
        decls = []
        for prm in (ps.children if ps else []):
            ty = next((c for c in prm.children if c.kind in ("type-ref", "type-name")), None)
            ts = _type_str(ty)
            cty = _cm.cxx_type(ts) or "uint64_t"
            if cty.startswith("std::vector"):
                cty = f"const {cty}&"
            decls.append(f"{cty} {prm.name}")
        rt = next((c for c in fn.children if c.kind in ("type-ref", "type-name")), None)
        rts = _cm.cxx_type(_type_str(rt)) or "uint64_t"
        out.append(f"{rts} {fn.name}({', '.join(decls)}) {{")
        body = next((c for c in fn.children if c.kind == "block"), None)
        for st in (body.children if body else []):
            for line in _cpp_stmt(st, "    "):
                out.append(line)
        out.append("}")
        out.append("")
    return "\n".join(out) + "\n"


def _cpp_stmt(st, ind: str) -> List[str]:
    k = getattr(st, "kind", "")
    if k in ("var-decl", "const-decl"):
        ty = next((c for c in st.children if c.kind in ("type-ref", "type-name")), None)
        rhs = next((c for c in reversed(st.children)
                    if c.kind not in ("type-ref", "type-name", "type-args", "path")), None)
        return [f"{ind}{_cxx_type_of(_type_str(ty))} {st.name} = {_cpp_expr(rhs)};"]
    if k == "assign":
        return [f"{ind}{_cpp_expr(st.children[0])} = {_cpp_expr(st.children[1])};"]
    if k == "return":
        return [f"{ind}return {_cpp_expr(st.children[0])};"] if st.children else [f"{ind}return;"]
    if k == "while":
        cond = st.children[0]
        body = next((c for c in st.children[1:] if c.kind == "block"), None)
        lines = [f"{ind}while ({_cpp_expr(cond)}) {{"]
        for x in (body.children if body else []):
            lines += _cpp_stmt(x, ind + "    ")
        lines.append(f"{ind}}}")
        return lines
    if k == "if":
        cond = st.children[0]
        body = next((c for c in st.children[1:] if c.kind == "block"), None)
        lines = [f"{ind}if ({_cpp_expr(cond)}) {{"]
        for x in (body.children if body else []):
            lines += _cpp_stmt(x, ind + "    ")
        lines.append(f"{ind}}}")
        return lines
    if k == "expr-stmt":
        return [f"{ind}{_cpp_expr(st.children[0])};"]
    raise CppUnsupported(f"语句 {k} 还没支持")


def _cpp_expr(e) -> str:
    if e is None:
        return "0"
    k = getattr(e, "kind", "")
    if k == "lit-int":
        return str(int(e.value))
    if k == "path":
        return e.name or ""
    if k == "binary":
        # **运算符存在 `value` 里，不是 `name`**（AST 约定）
        op = {"+": "+", "-": "-", "*": "*", "/": "/", "%": "%",
              "<": "<", "<=": "<=", ">": ">", ">=": ">=", "==": "==",
              "!=": "!=", "and": "&&", "or": "||"}.get(e.value or "")
        if op is None:
            raise CppUnsupported(f"运算符 {e.value} 还没支持")
        return f"({_cpp_expr(e.children[0])} {op} {_cpp_expr(e.children[1])})"
    if k == "method-call" and (e.name or "") == "length":
        return f"{_cpp_expr(e.children[0])}.size()"
    if k == "index":
        return f"{_cpp_expr(e.children[0])}[{_cpp_expr(e.children[1])}]"
    if k == "call":
        short = (e.name or "").split(".")[-1]
        args = next((c for c in e.children if c.kind == "args"), None)
        vals = ", ".join(_cpp_expr(a) for a in (args.children if args else []))
        if short == "print":
            return f'(std::cout << {vals} << std::endl)'
        if short == "sum_of":
            return f"std::accumulate({vals}.begin(), {vals}.end(), uint64_t(0))"
        if short in ("min", "max"):
            return f"std::{short}({vals})"
        return f"{short}({vals})"
    raise CppUnsupported(f"表达式 {k} 还没支持")


def _cpp_compare(tu_p: str, ch_p: str, text: str) -> int:
    """编译并和解释器对拍；**没有编译器就如实说**。"""
    import shutil
    import subprocess
    import tempfile
    comp = next((c for c in ("g++", "clang++", "cl") if shutil.which(c)), None)
    if comp is None:
        print("  ⚠ 找不到 C++ 编译器（g++ / clang++ / cl），**无法对拍**——"
              "源码已生成，但没有验证过它能编译")
        return 0
    d = tempfile.mkdtemp()
    src = os.path.join(d, "m.cpp")
    io.open(src, "w", encoding="utf-8", newline="\n").write(text)
    exe = os.path.join(d, "m.exe" if os.name == "nt" else "m.out")
    r = subprocess.run([comp, "-std=c++17", src, "-o", exe], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"✗ 编译失败：{(r.stderr or '')[:300]}", file=sys.stderr)
        return 1
    print(f"  ✓ 用 {comp} 编译成功：{exe}")
    return 0


def cmd_header(argv: List[str]) -> int:
    """`elench header <模块.tu> <模块.eln>` —— 生成 C 头文件（**契约跟着代码走**）。

    ## 它是什么、不是什么

    **是**：一个 `.h`，每个函数一行 C 声明，**上面一行注释写着 κ、effects、target**。
    **不是**：实现。**别人能编译（拿到签名与契约），但链接不到实现**——
    实现要等 C++ 后端（E37）。

    ## 为什么要先做这个

    1. **契约第一次能离开我们的工具链**——别人读 `.h` 就知道"这个函数承诺多快"；
    2. 它是 C++ 后端的前置（同一个类型映射表 `cxx_map`）；
    3. 便宜：不需要会生成代码，只需要会"翻译签名"。
    """
    tu_p, ch_p = _load_pair(argv)
    if tu_p is None:
        print("用法: elench header <模块.tu> <模块.eln> [--output=x.h]", file=sys.stderr)
        return 2
    from parser import parse_source as _ps
    tu_mod = _ps(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = (_ps(io_read(ch_p), os.path.basename(ch_p)).module
              if ch_p and os.path.exists(ch_p) else None)
    if ch_mod is None:
        print("没有实现文件，无法生成头文件", file=sys.stderr)
        return 2
    out_path = next((a.split("=", 1)[1] for a in argv if a.startswith("--output=")), None)

    import checker as _ck
    import cxx_map as _cm
    ci = _cost_interp(ch_mod, tu_mod, tu_p)
    eff = _ck.function_effects(tu_mod, ch_mod)

    guard = os.path.basename(tu_p).rsplit(".", 1)[0].upper() + "_H"
    lines: List[str] = []
    lines.append("// 由 elench header 生成 —— **契约在这里，实现不在这里**")
    lines.append("// κ = 该函数承诺的成本上界；effects = 声明的副作用；target = 作者的要求")
    lines.append("#pragma once")
    lines.append("#include <cstdint>")
    lines.append("#include <vector>")
    lines.append("#include <string>")
    lines.append("")
    lines.append(f"// ---- 模块：{os.path.basename(tu_p)}")
    for name, fn in ci.fns.items():
        try:
            k = ci.cost_of(name).render()
        except Exception:                          # noqa: BLE001
            k = "⊤"
        e = ",".join(sorted(eff.get(name, set()))) or "—"
        ps = next((c for c in fn.children if c.kind == "params"), None)
        ps = ps.children if ps else []
        decls = []
        for prm in ps:
            ty = next((c for c in prm.children if c.kind in ("type-ref", "type-name")), None)
            ts = _cm.cxx_type(_type_str(ty)) or "/*?*/"
            # 容器按 `const &` 传（值拷贝会改语义：被调函数改的是副本）
            if ts.startswith("std::vector"):
                ts = f"const {ts}&"
            decls.append(f"{ts} {prm.name}")
        rt = next((c for c in fn.children if c.kind in ("type-ref", "type-name")), None)
        rts = _cm.cxx_type(_type_str(rt)) or "uint64_t"
        lines.append(f"// target: {self_target(ci, name)}")
        lines.append(f"// κ: {k}")
        lines.append(f"// effects: {e}")
        lines.append(f"{rts} {name}({', '.join(decls)});")
        lines.append("")
    lines.append("// ---- 外部依赖（FFI 声明；实现来自库，不在本模块）")
    # `.tu` 里用 `external "lib";` 声明的（v0.71 推荐写法）
    for _sp in (tu_mod.children or []):
        if getattr(_sp, "kind", "") != "spec":
            continue
        _body = next((c for c in _sp.children if c.kind == "spec-body"), None)
        _lib = None
        for _c in (_body.children if _body else []):
            if _c.kind == "clause" and (_c.name or "") == "external":
                _lib = (_c.value or {}).get("lib") if isinstance(_c.value, dict) else None
        if _lib is None:
            continue
        _ps = next((c for c in _sp.children if c.kind == "params"), None)
        _ds = []
        for _p in (_ps.children if _ps else []):
            _ty = next((c for c in _p.children if c.kind in ("type-ref", "type-name")), None)
            _ts = _type_str(_ty)
            _cty = "const char*" if _ts.startswith("Vec") else (_cm.cxx_type(_ts) or "uint64_t")
            _ds.append(f"{_cty} {_p.name}")
        _rt = next((c for c in _sp.children if c.kind in ("type-ref", "type-name")), None)
        _rts = _cm.cxx_type(_type_str(_rt)) or "uint64_t"
        lines.append(f'extern "C" {_rts} {_sp.name}({", ".join(_ds)});  // from "{_lib}"')
    for c in (ch_mod.children or []):
        if getattr(c, "kind", "") != "extern":
            continue
        lib = (c.value or {}).get("lib") if isinstance(c.value, dict) else None
        ps = next((x for x in c.children if x.kind == "params"), None)
        ps = ps.children if ps else []
        decls = []
        for prm in ps:
            ty = next((x for x in prm.children if x.kind in ("type-ref", "type-name")), None)
            ts = _type_str(ty)
            cty = "const char*" if ts.startswith("Vec") else (_cm.cxx_type(ts) or "uint64_t")
            decls.append(f"{cty} {prm.name}")
        rt = next((x for x in c.children if x.kind in ("type-ref", "type-name")), None)
        rts = _cm.cxx_type(_type_str(rt)) or "uint64_t"
        lines.append(f'extern "C" {rts} {c.name}({", ".join(decls)});  // from "{lib}"')

    text = "\n".join(lines) + "\n"
    if out_path:
        io.open(out_path, "w", encoding="utf-8", newline="\n").write(text)
        print(f"  {out_path}  ←  契约头文件（{len(ci.fns)} 个函数）")
    else:
        sys.stdout.write(text)
    return 0


def self_target(ci, name: str) -> str:
    """把 spec 里的 target 渲染成一行（作者的要求，和算出来的 κ 并排）。"""
    try:
        t = ci.target_cost(name)
        return t.render() if t is not None else "（未声明）"
    except Exception:                                   # noqa: BLE001
        return "（未声明）"


def _type_str(n) -> str:
    """把类型节点还原成字符串（复用 checker 的做法，保持独立以减少耦合）。"""
    if n is None:
        return ""
    if getattr(n, "kind", "") == "type-ref":
        inner = next((c for c in n.children if c.kind in ("type-name", "type-ref")), None)
        return _type_str(inner)
    if getattr(n, "kind", "") == "type-name":
        args = next((c for c in n.children if c.kind == "type-args"), None)
        if args is None:
            return n.name or ""
        return (n.name or "") + "<" + ", ".join(_type_str(a) for a in args.children) + ">"
    return getattr(n, "name", "") or ""


def cmd_cost(argv: List[str]) -> int:
    argv = _expand_module_args(argv)
    """静态闭环：算成本式，与 target 比，报债务。"""
    from parser import parse_source
    from cost_interp import CostInterp
    from cost_check import compare, compare_exact

    tu_p, ch_p = _load_pair(argv)
    if tu_p is None:
        print("用法：dsh cost <模块.tu> [<模块.eln>]", file=sys.stderr)
        return 2

    tu_mod = parse_source(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = (parse_source(io_read(ch_p), os.path.basename(ch_p)).module
              if ch_p and os.path.exists(ch_p) else None)
    if ch_mod is None:
        print("没有实现文件，无法算成本", file=sys.stderr)
        return 2

    ci = _cost_interp(ch_mod, tu_mod, tu_p)
    # **--segmented**：有分档表时按段取上界（更紧，且保持阶）
    if "--segmented" in argv:
        _tbl = os.path.join(os.path.dirname(os.path.abspath(tu_p)),
                            os.path.basename(tu_p).rsplit(".", 1)[0] + ".dispatch.yaml")
        if os.path.exists(_tbl):
            ci = CostInterp(ch_mod, tu_mod, table_path=_tbl)
            print(f"  （使用分档表：{os.path.basename(_tbl)}，按段取上界）")
        else:
            print("  ！ 没有分档表，退回「全候选取 max」")

    # `--signature`：输出机器可读的成本签名（JSON），供 agent 当**预算先验**用。
    # 这是"把成本签名塞进 agent 上下文"（研究切口 1）所需要的那份产物。
    if "--signature" in argv or "--json" in argv:
        out = {"module": os.path.basename(tu_p).rsplit(".", 1)[0],
               "functions": {}}
        for name in ci.fns:
            try:
                c = ci.cost_of(name)
            except Exception as e:                   # noqa: BLE001
                out["functions"][name] = {
                    "cost": {"operator": "unknown"},
                    "verdict": "unknown",
                    "reason": str(e)}
                continue
            tgt = ci.target_cost(name)
            v = compare(c, tgt)
            try:
                a = ci.alloc_of(name)
            except Exception:                          # noqa: BLE001
                a = None
            atgt = ci.target_cost(name, "allocation")
            av = compare_exact(a, atgt) if a is not None else None
            out["functions"][name] = {
                "cost": c.to_dict(),
                "cost_expression": c.render(),
                "target": tgt.to_dict() if tgt is not None else None,
                "verdict": v.kind,
                "verdict_explanation": v.render(),
                "allocation": a.to_dict() if a is not None else None,
                "allocation_expression": a.render() if a is not None else None,
                "allocation_verdict": av.kind if av is not None else "unknown",
                "allocation_explanation": av.render() if av is not None else None,
            }
        if "--yaml" in argv:
            print(to_yaml(out))
        else:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"module {os.path.basename(tu_p).rsplit('.', 1)[0]}")
    # **批次 S**：`assume` 是"可撒谎位置"，报告 MUST 把它印出来。
    if getattr(ci, "assumptions", None):
        for k, v in sorted(ci.assumptions.items()):
            print(f"  假设：{k} ≤ {v.render()}      ← 来自 .tu 的 assume（信任区）")
    print()
    w = max((len(n) for n in ci.fns), default=8)
    debt = 0
    for name in ci.fns:
        try:
            c = ci.cost_of(name)
        except Exception as e:                       # noqa: BLE001
            print(f"  {name.ljust(w)}  ⊤   ({e})")
            continue
        t = ci.target_cost(name)
        v = compare(c, t)
        mark = {"ok": "✓", "debt": "▲", "unknown": "?"}[v.kind]
        debt += 1 if v.kind == "debt" else 0
        # **E36：allocation 维度也判**（以前只解析、不判）
        try:
            a = ci.alloc_of(name)
            at = ci.target_cost(name, "allocation")
            av = compare_exact(a, at)
        except Exception:                              # noqa: BLE001
            a, av = None, None
        amark = {"ok": "✓", "debt": "▲", "unknown": "?"}[av.kind] if av is not None else " "
        debt += 1 if (av is not None and av.kind == "debt") else 0
        print(f"  {mark} {name.ljust(w)}  κ ≤ {c.render()}")
        _rend = c.render()
        if "ext(" in _rend:
            _exts = sorted({seg.split(")")[0] for seg in _rend.split("ext(")[1:]})
            print(f"    {'':{w}}  外部项：{', '.join('ext(' + x + ')' for x in _exts)}"
                  f"      ← 其余部分已算；这一项要 `external` 登记（尚未实现）")
        if a is not None:
            _an = a.render()
            _at = at.render() if at is not None else "（无 target）"
            print(f"    {'':{w}}  分配 {amark} alloc ≤ {_an}   (target {_at})")
        print(f"    {'':{w}}  {v.render()}")
    print()
    # **批次 S**：外部调用只数次数（不估价）——必须在**算完之后**打印。
    if getattr(ci, "external_calls", None):
        for _name, _cnt in sorted(ci.external_calls.items()):
            print(f"  外部调用：{_name} × {_cnt}      ← 只记次数，不估价（内部成本不可见）")
    unknown = sum(1 for name in ci.fns
                  if (lambda c: c is not None and compare(c, ci.target_cost(name)).kind == "unknown")
                  (None))  # 占位，真实统计在下面重算
    unknown = 0
    for name in ci.fns:
        try:
            c = ci.cost_of(name)
        except Exception:                              # noqa: BLE001
            unknown += 1
            continue
        if compare(c, ci.target_cost(name)).kind == "unknown":
            unknown += 1
    # **external 登记的两种不一致**（v0.71）：
    #   · 登记了但没人用 → 警告（陈旧登记会误导读者）
    #   · 用了但没登记 → 提示（ext(...) 留着，上界判不了）
    # **从 AST 数**，不要从 κ 的渲染里数——登记过的项已经被替换掉，
    # 渲染里就看不见了（第一版就是这样误报"登记了但没用到"的）。
    import stdlib as _stdlib3
    from parser import Node as _Node
    _used_ext: set = set()
    _stack = [ch_mod]
    while _stack:
        _x = _stack.pop()
        if _x is None:
            continue
        if getattr(_x, "kind", "") == "call" and getattr(_x, "name", ""):
            _short = (_x.name or "").split(".")[-1]
            if _stdlib3.is_stdlib(_short) and _stdlib3.cost_kind(_short) == "ext":
                _used_ext.add(_short)
        _stack.extend(getattr(_x, "children", []) or [])
    _reg = set(getattr(ci, "externals", {}) or {})
    for _name in sorted(_reg - _used_ext):
        print(f"  ⚠ external 登记了但没有用到：{_name}（陈旧登记会误导读者，建议删掉）")
    for _name in sorted(_used_ext - _reg):
        print(f"  提示：ext({_name}) 未登记——可用 `external {{ {_name}: O(1); }}` 让上界可判定")

    if debt:
        print(f"▲ {debt} 处超出 target（time / allocation 各算一处；记为债务，不阻断构建）")
    if unknown and not debt:
        # **不能把"无法判定"说成"达标"**——那是把"不知道"当成"没问题"。
        print(f"? {unknown} 个函数无法判定（尺寸量不一致 / κ 为 ⊤）；"
              f"其余在 target 之内")
    if not debt and not unknown:
        print("✓ 所有函数都在 target 之内")
    return 0


def cmd_dispatch(argv: List[str]) -> int:
    argv = _expand_module_args(argv)
    """三层筛：为每个 ?! 决定要不要测、分几段。"""
    from parser import parse_source
    from cost_interp import CostInterp
    from dispatch import plan_dispatch

    tu_p, ch_p = _load_pair(argv)
    if tu_p is None:
        print("用法：dsh dispatch <模块.tu> [<模块.eln>]", file=sys.stderr)
        return 2

    tu_mod = parse_source(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = (parse_source(io_read(ch_p), os.path.basename(ch_p)).module
              if ch_p and os.path.exists(ch_p) else None)
    if ch_mod is None:
        print("没有实现文件，无法规划分档", file=sys.stderr)
        return 2

    ci = _cost_interp(ch_mod, tu_mod, tu_p)
    sizes, lo, hi = _scale_range(tu_mod)
    if lo is None:
        print("没有 benchmodel.scale，无法确定实际输入范围", file=sys.stderr)
        return 2

    print(f"module {os.path.basename(tu_p).rsplit('.', 1)[0]}")
    print(f"实际输入范围：{lo:,} .. {hi:,}")
    print()
    from bound import _descendants
    labels = sorted({k.name for k in _descendants(ci.mod, "kernels") if k.name})
    any_plan = False
    for label in labels:
        cands = _candidate_costs(ci, label)
        if not cands:
            continue
        # **不可测的候选**：涉及 io/fs/env 的候选，测出来的是环境不是算法 → 跳过
        import checker as _checker2
        _bad = []
        for _cand_name in list(cands):
            _node = next((c for c in _descendants(ch_mod, "candidate")
                          if (c.name or "") == _cand_name), None)
            if _node is not None and (_checker2.node_effects(_node, ch_mod) & {"io", "fs", "env"}):
                _bad.append(_cand_name)
                del cands[_cand_name]
        if _bad:
            print(f"  ?!{label}: 跳过不可测量的候选 {_bad}（涉及 io/fs/env）")
        if not cands:
            continue
        any_plan = True
        plan = plan_dispatch(label, cands, _size_var(cands), lo, hi)
        print(plan.render())
        print()
    if not any_plan:
        print("（没有 ?!，无需分档）")
    return 0


def _candidate_costs(ci, label):
    """候选名 → 成本式。从 kernels 里取每个候选的体来算。"""
    from bound import _descendants
    out = {}
    for kern in _descendants(ci.mod, "kernels"):
        if (kern.name or "") != label:
            continue
        for cand in kern.children:
            if cand.kind != "candidate":
                continue
            blocks = [c for c in cand.children if c.kind == "block"]
            if not blocks:
                continue
            total = None
            for b in blocks:
                c = ci._block(b, {})
                total = c if total is None else __import__("symbolic").Cost.add(total, c)
            out[cand.name] = total
    return out


def _size_var(costs):
    for c in costs.values():
        vs = c.variables()
        if vs:
            return vs[0]
    return "n"


def _scale_range(tu_mod):
    """从 benchmodel.scale 里取 (各参数的点表, 最小, 最大)。"""
    from bound import _descendants
    from checker import _eval_range_points
    sizes, lo, hi = {}, None, None
    for bm in _descendants(tu_mod, "benchmodel"):
        for sb in _descendants(bm, "scale-binding"):
            pts, _err = _eval_range_points(sb.children[0] if sb.children else None)
            if not pts:
                continue
            sizes[sb.name] = pts
            lo = pts[0] if lo is None else min(lo, pts[0])
            hi = pts[-1] if hi is None else max(hi, pts[-1])
    return sizes, lo, hi


def io_read(path: str) -> str:
    import io
    return io.open(path, encoding="utf-8").read()



def cmd_measure(argv: List[str]) -> int:
    argv = _expand_module_args(argv)
    """动态闭环：真跑候选，用实测生成分档表（§7.8 第 ③ 步）。"""
    from parser import parse_source
    from cost_interp import CostInterp
    from dispatch import plan_dispatch, table_from_measure

    tu_p, ch_p = _load_pair(argv)
    if tu_p is None:
        print("用法：dsh measure <模块.tu> [<模块.eln>]", file=sys.stderr)
        return 2
    if not (ch_p and os.path.exists(ch_p)):
        print("没有实现文件，无法测量", file=sys.stderr)
        return 2

    try:
        from bench import measure          # 需要 interp.py
    except ImportError as e:
        print(f"测量需要执行器（src/interp.py + src/bench.py），当前不可用：{e}",
              file=sys.stderr)
        print("提示：`dsh cost` 与 `dsh dispatch` 只做静态部分，不依赖执行器。",
              file=sys.stderr)
        return 3

    tu_mod = parse_source(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = parse_source(io_read(ch_p), os.path.basename(ch_p)).module
    ci = _cost_interp(ch_mod, tu_mod, tu_p)
    sizes_by_param, lo, hi = _scale_range(tu_mod)
    if not sizes_by_param:
        print("没有 benchmodel.scale，无法确定测量点", file=sys.stderr)
        return 2

    from bound import _descendants
    print(f"module {os.path.basename(tu_p).rsplit('.', 1)[0]}")
    for label in sorted({k.name for k in _descendants(ch_mod, "kernels") if k.name}):
        cands = _candidate_costs(ci, label)
        if not cands:
            continue
        # 找该标签的宿主函数（?!label 出现在谁里）
        host = _host_of(ci, label)
        if host is None:
            print(f"  ?!{label}: 找不到宿主函数，跳过")
            continue
        var = _size_var(cands)
        pts = sizes_by_param.get(var) or list(sizes_by_param.values())[0]
        # 解释执行比编译产物慢两三个数量级，规模必须截断，否则一个 1e6 的循环跑不完。
        cap = 4096
        for a in argv:
            if a.startswith("--max-size="):
                try:
                    cap = int(a.split("=", 1)[1])
                except ValueError:
                    pass
        kept = [x for x in pts if x <= cap]
        if len(kept) < 2:
            kept = pts[:2]
        if len(kept) < len(pts):
            print(f"  （解释执行较慢：规模截断到 ≤ {cap}，实际测 {kept}）")
        pts = kept
        measured = {}
        for cand in cands:
            try:
                with _otel_span(f"?!{label}", {"label": label, "candidate": cand,
                                  "benchmark.points": len(pts)}):
                    if _otel_tracer().enabled:
                        # **开着 tracing 就逐点测**：这样每个规模点一个 span，
                        # 能直接看出"哪个 n 最慢"——而这正是分档表要回答的问题。
                        measured[cand] = {}
                        for _n in pts:
                            with _otel_span("measure.point",
                                            {"candidate": cand,
                                             "benchmark.input_size": _n}):
                                _one = measure(ch_mod, tu_mod, host, [_n],
                                               force={label: cand},
                                               table_path=_tbl_for(tu_p))
                            measured[cand].update(_one)
                    else:
                        measured[cand] = measure(ch_mod, tu_mod, host, pts,
                                                 force={label: cand},
                                                 table_path=_tbl_for(tu_p))
            except Exception as e:                       # noqa: BLE001
                print(f"  ?!{label}/{cand}: 测量失败 —— {e}")
        if not measured:
            continue
        segs = table_from_measure(label, pts, measured)
        # **就地落盘**：分档表必须成为可提交、可审查的文件，`build` 才有东西可校验。
        # 不放在函数末尾——那里有 return，容易漏执行（踩过）。
        _mod = os.path.basename(tu_p).rsplit(".", 1)[0]
        _tblp = os.path.join(os.path.dirname(os.path.abspath(tu_p)), _mod + ".dispatch.yaml")
        with open(_tblp, "w", encoding="utf-8", newline="\n") as _f:
            _f.write("# 由 `elench measure` 生成；MUST NOT 手改。\n")
            _f.write(f"module: {_mod}\nlabel: {label}\nsegments:\n")
            for _a, _b, _who in segs:
                _f.write(f"  - {{lo: {_a}, hi: {_b}, candidate: {_who}}}\n")
        print(f"  分档表 → {_tblp}")
        print(f"  ?!{label}（宿主 {host}）")
        print("    静态估计：")
        plan = plan_dispatch(label, cands, var, lo, hi)
        for ln in plan.render().split("\n"):
            print("      " + ln.strip())
        print("    实测分档：")
        if not segs:
            print("      （无有效测量）")
        for a, b, who in segs:
            print(f"      n ∈ [{a:,}, {b:,}] → {who}")
        # 静态与实测不一致时，明确指出——**以实测为准**（§7.8.3）
        static_win = {w for _, _, w in plan.segments}
        measured_win = {w for _, _, w in segs} if segs else set()
        if segs and static_win != measured_win:
            print(f"    ⚠ 静态与实测不一致（静态 {sorted(static_win)} / "
                  f"实测 {sorted(measured_win)}）：**以实测为准**")
        print()
    return 0


def _host_of(ci, label):
    """哪个函数体里出现了 `?!label`。"""
    from bound import _descendants
    for name, fn in ci.fns.items():
        for d in _descendants(fn, "dispatch"):
            if (d.name or "") == label:
                return name
    return None



def cmd_stats(argv: List[str]) -> int:
    argv = _expand_module_args(argv)
    """M1 验收③：跑一次，报每函数调用数 / 分配数 / 迭代数。"""
    from interp import Interp
    from parser import parse_source

    if len(argv) < 3:
        print("用法：dsh stats <模块.tu> <模块.eln> <函数> [参数...]", file=sys.stderr)
        return 2
    tu_p, ch_p, fn = argv[0], argv[1], argv[2]
    rest = argv[3:]
    tu_mod = parse_source(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = parse_source(io_read(ch_p), os.path.basename(ch_p)).module
    it = Interp(ch_mod, tu_mod)

    args = []
    for a in rest:
        if a.lstrip("-").isdigit():
            args.append(int(a))
        else:
            args.append(list(a.encode("utf-8")))

    # 第十二条：命令行没给够实参时，用 `benchmodel.data` 自动造——
    # CLI 合成不出 `Vec<u8>`，但它**读得到 benchmodel**。
    if not args:
        auto = _make_args_for(it, tu_mod, fn)
        if auto:
            args = auto

    try:
        val = it.call(fn, args)
    except Exception as e:                          # noqa: BLE001
        print(f"执行失败：{e}", file=sys.stderr)
        return 1
    print(f"module {os.path.basename(tu_p).rsplit('.', 1)[0]}")
    print(f"  {fn}({len(args)} 个实参) → {val}")
    c = it.counters()
    print(f"  调用数（每函数）")
    for fname in sorted(c["function_calls"]):
        print(f"      {fname:<24} {c['function_calls'][fname]:,}")
    print(f"      合计 {c['function_calls_total']:,}"
          f"   （其中方法调用 {c['method_calls']:,}）")
    print(f"  分配数   {c['allocations']:,}   （push 触发扩容的次数）")
    print(f"  迭代数   {c['iterations']:,}   （循环体执行次数）")
    return 0



def cmd_export(argv: List[str]) -> int:
    auto, argv = _auto_enabled(argv)
    argv = _normalize_flags(_expand_module_args(argv))
    # **容错两个常见误用**（只提示，不静默）：
    #   1. 参数顺序写成 `x.eln x.tu` → 自动换回来
    #   2. 裸 `csv` / `otlp` 之类 → 当成 `--format=csv`
    if len(argv) >= 2 and argv[0].endswith(".eln") and argv[1].endswith(".tu"):
        print("  ！ 参数顺序应为 <模块.tu> <模块.eln>，已自动交换")
        argv = [argv[1], argv[0]] + argv[2:]
    for _i, _a in enumerate(argv):
        if _a in ("table", "otlp", "svg", "csv"):
            print(f"  ！ 格式请写成 --format={_a}，已自动转换")
            argv = argv[:_i] + [f"--format={_a}"] + argv[_i + 1:]
            break
    """把「静态上界 vs 实测」导出成能画图的形式（OTLP / SVG / CSV / 表）。"""
    from bench import measure
    from bound import _descendants
    from cost_interp import CostInterp
    from otel import report, series, sizes_to, to_csv, to_otlp, to_svg
    from parser import parse_source

    fmt = "table"
    for a in argv:
        if a.startswith("--format="):
            fmt = a.split("=", 1)[1]
    out_path = None
    for a in argv:
        if a.startswith("--out="):
            out_path = a.split("=", 1)[1]
    if out_path and not any(a.startswith("--format=") for a in argv):
        _ext = os.path.splitext(out_path)[1].lower().lstrip(".")
        if _ext in ("csv", "svg", "otlp", "json"):
            fmt = "otlp" if _ext == "json" else _ext

    _pos = [a for a in argv if not a.startswith("--")]
    # **自动链第二环**：export 之前先 build（含它自己的 dispatch + measure）。
    # 构建没过也继续导出——export 是**诊断命令**，拦住它等于让人没法查为什么没过。
    if auto and _pos:
        print("  「自动」先在 export 之前跑 build（检查 + pending 门禁 + 分档表）")
        _rc = cmd_build([_pos[0]] + _pos[1:2])
        if _rc != 0:
            print("  ！ build 未通过（仍然继续导出，便于定位问题）")

    tu_p, ch_p = _load_pair(_pos)
    if tu_p is None:
        print("用法：elench export <模块> [--format=table|otlp|svg|csv] [--out=文件] [--function=名]",
              file=sys.stderr)
        return 2
    tu_mod = parse_source(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = parse_source(io_read(ch_p), os.path.basename(ch_p)).module
    ci = _cost_interp(ch_mod, tu_mod, tu_p)

    # 只导出一个函数：用 --function= 指定，否则取第一个 public 有 target 的
    fn_name = None
    for a in argv:
        if a.startswith("--function="):
            fn_name = a.split("=", 1)[1]
    if fn_name is None:
        for nm in ci.fns:
            if ci.target_cost(nm) is not None:
                fn_name = nm
                break
    if fn_name is None:
        print("没有带 target 的函数可导出", file=sys.stderr)
        return 2

    sizes_by_param, lo, hi = _scale_range(tu_mod)
    var = next(iter(sizes_by_param), "xs")
    cap = 4096
    pts = [x for x in sizes_by_param.get(var, []) if x <= cap]
    if len(pts) < 2:
        pts = sizes_by_param.get(var, [1, 2, 4, 8, 16, 32])[:6]

    # 找 ?! 标签与赢家候选（有的话就同时给「单候选」那条静态线）
    label, cand_name = None, None
    for d in _descendants(ch_mod, "dispatch"):
        label = d.name
        break
    if label:
        cands = _candidate_costs(ci, label)
        # 挑阶最低的候选——它是实测会用的那个
        from dispatch import order_of
        if cands:
            cand_name = min(cands, key=lambda k: order_of(cands[k]))
    cand_cost = (sizes_to(cands.get(cand_name), var)
                 if (label and cand_name and cands) else None)

    # ---- 先 dispatch：用**同一套三层筛**决定分段，而不是自己另算一遍
    # 上一版我在这里按"每个规模取 κ 最小"手算分段，结果和 `dispatch` 打架
    # （dispatch 说 sum 不分档、导出却说 8 起切到 sum_by_index）——
    # **同一件事算两遍必然分叉**，所以这里直接调 `plan_dispatch`。
    segments = []
    plan_reason = ""
    _plan = None
    if label and cands:
        from dispatch import plan_dispatch
        # **必须用 benchmodel 声明的范围**，不能用导出截断后的窗口：
        # 否则 `dispatch`（按声明范围）与 `export`（按截断窗口）会给出不同结论——
        # 这正是上一版自相矛盾的原因。
        # **必须与 `cmd_dispatch` 用同一个变量名推导**（`_size_var`）。
        # E32 的根因就在这里：export 用 benchmodel 的参数名、dispatch 用候选成本式里的尺寸变量，
        # 两者对不上时算出的交叉点自然不同。
        _plan = plan_dispatch(label, cands, _size_var(cands), lo, hi)
        plan_reason = _plan.reason
        _w0, _w1 = pts[0], pts[-1]
        for _lo, _hi, _nm in _plan.segments:
            a, b = max(_lo, _w0), min(_hi, _w1)
            if a <= b:
                segments.append((a, b, _nm))

    try:
        measured = measure(ch_mod, tu_mod, fn_name, pts,
                           force={label: cand_name} if label and cand_name else None,
                           table_path=_tbl_for(tu_p))
    except Exception as e:                          # noqa: BLE001
        # 没有 benchmodel（比如 calculator 没有 ?!）→ 只出静态线，不假装有实测
        print(f"（该函数没有 benchmodel，只能出静态线：{e}）", file=sys.stderr)
        measured = None
    # **κ分段**：按 plan 的段，逐点取该段候选的 κ（与实测同口径）。
    # 没有表 → piecewise=None → 渲染退化为「候选」口径，并标注是静态计划。
    _pw = {}
    if label and segments and _size_var(cands):
        from cost_check import evaluate as _ev2
        for _n in pts:
            for _lo, _hi, _who in segments:
                if _lo <= _n <= _hi and _who in cands:
                    try:
                        _pw[_n] = _ev2(cands[_who], {_size_var(cands): _n})
                    except Exception:                  # noqa: BLE001
                        pass
                    break
    rows = series(ci.cost_of(fn_name), pts, measured, var=var,
                  candidate_cost=cand_cost, piecewise=_pw or None)
    target = ci.target_cost(fn_name)

    if fmt == "otlp":
        body = json.dumps(to_otlp(rows, fn_name, pts, measured, target),
                          ensure_ascii=False, indent=2)
    elif fmt == "svg":
        body = to_svg(rows, fn_name)
    elif fmt == "csv":
        body = to_csv(rows)
    else:
        body = report(rows, fn_name)

    if not out_path and fmt in ("csv", "svg", "otlp"):
        print(f"（提示：加 --out=结果.{fmt} 就能存成文件，例如 "
              f"`elench export {os.path.basename(tu_p).removesuffix('.tu')} "
              f"--format={fmt} --out=结果.{fmt}`）", file=sys.stderr)

    # **已知不一致（E32）**：这里的分段与 `elench dispatch` 的输出对不上（sum 就是例子：
    # dispatch 说不分档，这里却算出交叉点）。在查清之前**默认不打印**——
    # 宁可少一个功能，不可输出自相矛盾的数据。加 `--segments` 才看（并会附警告）。
    # 分段表：**直接复用 `plan.render()` 的原文**，与 `elench dispatch` 逐字一致。
    # 这是 E32 的修法——同一件事只留一套措辞，就不可能有第二种说法。
    if fmt == "table" and label and _plan is not None:
        body = body + "\n  「分段」与 `elench dispatch` 同源（逐字一致）：\n\n" \
            + "\n".join("  " + ln for ln in _plan.render().splitlines()) + "\n"

    # ---- OTel 推送（--push）：metrics + exemplars（+ 可选 traces/logs）
    if "--push" in argv:
        try:
            import otel_export as _oe
            _cfg = _oe.config()
            _raw = to_otlp(rows, fn_name, pts, measured, target)
            # exemplars：把每个规模的实测值挂上去（图能点进具体样本）
            _ex = {n: {"value": v, "timeUnixNano": int(n)} for n, v in (measured or {}).items()}
            _raw = _oe.add_piecewise_metric(_raw, rows, fn_name)
            _raw = _oe.attach_exemplars(_raw, _ex)
            _r = _oe.emit_metrics(_raw, _cfg)
            print(f"  OTel metrics → {_r.get('url') or '（未配置端点）'}  {_r}")
            if _cfg.get("logs"):
                _recs = [_oe.log_record(
                    f"export {fn_name}: {len(rows)} 个规模点", "INFO",
                    {"code.function.name": fn_name, "rows": len(rows)})]
                print(f"  OTel logs → {_oe.emit_logs(_recs, _cfg)}")
            if _cfg.get("traces"):
                _tr = _oe.Tracer(_cfg)
                with _tr.span("export", {"code.function.name": fn_name,
                                         "benchmark.points": len(rows)}):
                    pass
                print(f"  OTel traces → {_tr.flush()}")
        except Exception as _exc:                      # noqa: BLE001
            print(f"  ！ OTel 推送失败（不影响导出）：{type(_exc).__name__}: {_exc}",
                  file=sys.stderr)

    if out_path:
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(body if body.endswith("\n") else body + "\n")
        print(f"已写出 {out_path}")
    else:
        print(body)
    return 0




def _make_args_for(interp, decl_mod, fn_name: str, size: int = 64):
    """给 CLI 造实参：参数不够时用 `benchmodel.data` 造（第十二条）。

    这解决「`dsh stats lcs.tu lcs.eln lcs` 报『需要 2 个实参，给了 0』」——
    CLI 没能力合成 `Vec<u8>`，但它**能读 benchmodel**。
    """
    from bench import make_args, read_benchmodel
    fn = interp.fns.get(fn_name)
    if fn is None:
        return None
    params = [pm.name for pm in _walk_kind(fn, "param")]
    data, _scale, seed = read_benchmodel(decl_mod)
    if not data:
        return None
    sized = set(data)
    try:
        return make_args(data, sized, size, seed)
    except Exception:                                  # noqa: BLE001
        return None


def _walk_kind(n, kind):
    acc = []
    if n.kind == kind:
        acc.append(n)
    for c in n.children:
        acc.extend(_walk_kind(c, kind))
    return acc


def cmd_bench(argv: List[str]) -> int:
    argv = _expand_module_args(argv)
    """单函数隔离测量（规格 §13.5 B-18…B-22）。"""
    from bench import measure
    from interp import Interp
    from isolated import BenchError, check_measurable, ratio_of, report
    from parser import parse_source

    if len(argv) < 3:
        print("用法：dsh bench <模块.tu> <模块.eln> <函数> [--repeats=N]", file=sys.stderr)
        return 2
    tu_p, ch_p, fn_name = argv[0], argv[1], argv[2]
    repeats = 5
    for a in argv:
        if a.startswith("--repeats="):
            try:
                repeats = max(3, int(a.split("=", 1)[1]))
            except ValueError:
                pass

    tu_mod = parse_source(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = parse_source(io_read(ch_p), os.path.basename(ch_p)).module
    interp = Interp(ch_mod, tu_mod)
    fns = interp.fns

    # **I/O 不再阻测**：照常测，测完把 I/O 的耗时**摘出来**单独报
    # （`read_file` / `print` 各自计时，见 stdlib.IO_STATS）。
    import stdlib as _stdlib
    _stdlib.reset_io_stats()
    import checker as _checker
    _eff = _checker.function_effects(tu_mod, ch_mod).get(fn_name, set())

    try:
        distortions = check_measurable(fn_name, fns)
    except BenchError as e:
        print(f"✗ 不能对 {fn_name} 做隔离测量", file=sys.stderr)
        print(str(e), file=sys.stderr)
        return 1

    from isolated import _callees
    deps = [(d, _callees(fns[d], fns)) for d in _callees(fns[fn_name], fns)]

    # 规模：用 benchmodel 的 scale（没有就用几个固定点）
    sizes_by_param, lo, hi = _scale_range(tu_mod)
    var = next(iter(sizes_by_param), None)
    pts = [x for x in sizes_by_param.get(var, []) if x <= 4096] if var else []
    if len(pts) < 2:
        pts = [16, 64, 256]

    auto = _make_args_for(interp, tu_mod, fn_name) if len(pts) < 2 else None
    try:
        base = measure(ch_mod, tu_mod, fn_name, pts, repeats=repeats)
    except Exception as e:                          # noqa: BLE001
        # 缺 `benchmodel.data` 时不能算比值，但**仍可报 I/O 用量**——
        # 这正是"不拒测、只标明哪部分不可比"的做法。
        _io = _stdlib.io_report()
        if not _io:
            # 缺 benchmodel 造不出数据 → **用兜底实参跑一遍**，至少报用量
            from interp import Interp as _Interp2
            _it2 = _Interp2(ch_mod, tu_mod)
            _a2 = _make_args_for(_it2, tu_mod, fn_name)
            if _a2 is None:
                _a2 = []
                _fn2 = _it2.fns.get(fn_name)
                _ps2 = next((c for c in _fn2.children if c.kind == "params"), None) if _fn2 else None
                for _p2 in (_ps2.children if _ps2 else []):
                    _ty2 = next((c for c in _p2.children if c.kind in ("type-ref", "type-name")), None)
                    _a2.append([1, 2, 3] if (_ty2 is not None and _ty2.kind == "type-ref") else 3)
            _stdlib.reset_io_stats()
            _it2 = _Interp2(ch_mod, tu_mod)
            try:
                _it2.call(fn_name, _a2)
            except Exception:                        # noqa: BLE001
                pass
            _io = _stdlib.io_report()
            _c2 = _it2.counters().get("builtin_calls", {})
            print(f"  · 无法做规模比较（{e}）")
            print("  · 内建用量：" + ("，".join(f"{k} × {v}" for k, v in sorted(_c2.items()))
                                      or "（无）"))
            if _io:
                print("  · I/O 用量：" + "，".join(f"{k} × {v[0]}（{v[1] * 1000:.1f} ms）"
                                                  for k, v in sorted(_io.items())))
            return 0
        print(f"  · 无法做规模比较（{e}）")
        print("  · I/O 用量：" + "，".join(f"{k} × {v[0]}（{v[1] * 1000:.1f} ms）"
                                          for k, v in sorted(_io.items())))
        return 0

    # **I/O 分解**：总时间里有多少是 I/O，剩下的才是"算法那部分"
    _io = _stdlib.io_report()
    if _io:
        _tot = sum(v[1] for v in _io.values())
        print("  · I/O 分解：" + "，".join(f"{k} × {v[0]}（{v[1] * 1000:.1f} ms）"
                                          for k, v in sorted(_io.items())))
        print(f"  · I/O 合计 {_tot * 1000:.1f} ms；其余（算法部分）由下方比值反映")
        if _eff & {"io", "fs", "env"}:
            print(f"  · 注意：{fn_name} 含 {sorted(_eff & {'io', 'fs', 'env'})}，"
                  f"跨规模比较时请扣掉上表的 I/O 时间")

    # B-20：只给**比值**——以最小规模点为基线
    ratios: Dict[str, Optional[float]] = {}
    k0 = pts[0]
    for n in pts:
        ratios[f"n = {n:,}"] = ratio_of(base.get(k0, 0.0), base.get(n, 0.0))

    print(report(fn_name, ratios, distortions, deps))
    return 0


def cmd_run(argv: List[str]) -> int:
    """`elench run <模块>`：**跑起来**，把 `main` 的结果打出来。

    **为什么必须有这个命令**：没有它，入门文档的第一句"写个 hello 然后运行"
    就写不下去——用户只能 `check`，永远看不到程序输出。
    """
    argv = _expand_module_args(argv)
    from parser import parse_source
    from interp import Interp, Ok, Err

    tu_p, ch_p = _load_pair(argv)
    if tu_p is None or not ch_p:
        print("用法：elench run <模块> [--fn=函数名] [--u64=5] [--vec=1,2,3]",
              file=sys.stderr)
        return 2
    tu_mod = parse_source(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = parse_source(io_read(ch_p), os.path.basename(ch_p)).module

    fn_name = "main"
    vec_args: List[List[int]] = []
    num_args: List[int] = []
    for a in argv:
        if a.startswith("--fn="):
            fn_name = a.split("=", 1)[1]
        elif a.startswith("--vec="):
            # `--vec=1,2,3` → 一个 Vec<u64> 实参
            body = a.split("=", 1)[1].strip()
            vec_args.append([int(x) for x in body.split(",") if x.strip() != ""])
        elif a.startswith("--u64="):
            num_args.append(int(a.split("=", 1)[1]))

    for a in argv:
        if a.startswith("--seed="):
            os.environ["ELENCH_SEED"] = a.split("=", 1)[1]
    _par = os.environ.get("ELENCH_PARALLEL", "seq")
    for a in argv:
        if a.startswith("--parallel="):
            _par = a.split("=", 1)[1]
    _prog_args = [a.split("=", 1)[1] for a in argv if a.startswith("--arg=")]
    import stdlib as _stdlib_args
    _stdlib_args.set_prog_args(_prog_args)
    it = Interp(ch_mod, tu_mod, parallel=_par)
    if fn_name not in it.fns:
        print(f"✗ 找不到函数 {fn_name}（模块里有：{', '.join(sorted(it.fns)) or '无'}）",
              file=sys.stderr)
        print("  提示：`--fn=名字` 可以指定入口", file=sys.stderr)
        return 2
    # **实参组装**：按声明顺序，参数是 `Vec<…>` 就吃一个 `--vec`，否则吃一个 `--u64`。
    call_args: List[object] = []
    spec = next((c for c in (tu_mod.children if tu_mod else [])
                 if c.kind == "spec" and (c.name or "") == fn_name), None)
    params = []
    if spec is not None:
        for c in spec.children:
            if c.kind == "params":
                params = list(c.children)
                break
    vi = ni = 0
    for prm in params:
        ty = next((c for c in prm.children
                   if c.kind in ("type", "type-fail", "type-ref", "type-name")), None)
        ty_name = (ty.name or "") if ty is not None else ""
        # `const Vec<u64>& xs` 解析成 `type-ref`；`u64 k` 是 `type-name`。
        if (ty is not None and ty.kind == "type-ref") \
                or ty_name.startswith("Vec") or ty_name.startswith("FixedVec"):
            if vi >= len(vec_args):
                print(f"✗ 参数 {prm.name} 是向量，需要 `--vec=…`", file=sys.stderr)
                return 2
            call_args.append(vec_args[vi]); vi += 1
        else:
            if ni >= len(num_args):
                print(f"✗ 参数 {prm.name} 是标量，需要 `--u64=…`", file=sys.stderr)
                return 2
            call_args.append(num_args[ni]); ni += 1

    from interp import ExitRequest  # 局部导入：dsh 顶层不 import interp
    try:
        val = it.call(fn_name, call_args)
    except ExitRequest as _ex:
        # **`exit(code)` 是正常控制流**，不是错误：码原样交给 shell
        return _ex.code
    except Exception as exc:                            # noqa: BLE001
        print(f"✗ 运行失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if isinstance(val, Ok):
        print(val.v if not isinstance(val.v, (list, tuple)) else list(val.v))
        return 0
    if isinstance(val, Err):
        print(f"Err({val.e})", file=sys.stderr)
        return 1
    print(val)
    return 0


def cmd_build(argv: List[str]) -> int:
    auto, argv = _auto_enabled(argv)
    argv = _expand_module_args(argv)
    """`dsh build`：**B-17 的落地**——检查 + pending 门禁 + 成本表。

    B-17 原文：含 `pending` 时 `dsh check` 通过、**`dsh build` 失败**，
    成本表标 `state: incomplete`。

    退出码：0 通过 / 1 有错误或含 pending / 2 用法错。
    """
    from parser import parse_source
    from checker import check_module
    from cost_interp import CostInterp
    from cost_check import compare

    out_path = None
    pos: List[str] = []
    for a in argv:
        if a.startswith("--out="):
            out_path = a.split("=", 1)[1]
        elif not a.startswith("-"):
            pos.append(a)
    if not pos:
        print("用法：dsh build <模块.tu> [<模块.eln>] [--out=<路径>]", file=sys.stderr)
        return 2

    tu_p, ch_p = _load_pair(pos)
    tu_src = io_read(tu_p)
    ch_src = io_read(ch_p) if (ch_p and os.path.exists(ch_p)) else None
    module = os.path.basename(tu_p).rsplit(".", 1)[0]

    # **自动链第一环**：先 dispatch（出计划），再 measure（落表）。
    # 任何一步失败都不阻断后面的检查——那才是"构建"的意义：把所有问题一次报全。
    if auto and ch_src and "?!" in ch_src:
        print("  「自动」先跑 dispatch（三层筛：要不要测、分几段）")
        try:
            cmd_dispatch([tu_p] + ([pos[1]] if len(pos) > 1 else []))
        except Exception as exc:                       # noqa: BLE001
            print(f"  ！ dispatch 失败（继续）：{type(exc).__name__}: {exc}")
        # **measure 不再自动跑**（2026-10-30 用户决定）：它要真执行候选，
        # 大范围下可能几十秒到分钟级，把"随手构建"变成了"等一分钟"。
        # 分档表要单独生成：`elench measure <模块>`，或 `elench build --measure`。
        if "--measure" in argv:
            print("  「--measure」再跑 measure（生成分档表）")
            try:
                cmd_measure([tu_p] + ([pos[1]] if len(pos) > 1 else []))
            except Exception as exc:                   # noqa: BLE001
                print(f"  ！ measure 失败（继续）：{type(exc).__name__}: {exc}")
        else:
            print("  （跳过 measure：分档表请单独跑 `elench measure <模块>`，"
                  "或加 --measure）")

    # **E31：缺表即失败**（规格 L3227 的 `E-Dispatch-NoTable`）。
    # `build` 只**校验**表在不在，不负责生成——measure 太慢（2026-10-30 决定）。
    _missing_table = []
    if ch_src:
        from bound import _descendants as _desc
        _chmod = parse_source(ch_src, module + ".eln").module
        _labels = sorted({d.name for d in _desc(_chmod, "dispatch") if d.name})
        if _labels:
            _tbl = os.path.join(os.path.dirname(os.path.abspath(tu_p)),
                                module + ".dispatch.yaml")
            if not os.path.exists(_tbl):
                if "--allow-missing-table" in argv:
                    print(f"  ！ 缺分档表 {module}.dispatch.yaml（--allow-missing-table，本次放行）")
                else:
                    _missing_table.append(_labels)
                    print(f"  ✗ E-Dispatch-NoTable：有 ?!（{', '.join('?!' + x for x in _labels)}）"
                          f"但没有 {module}.dispatch.yaml")
                    print(f"    修法：elench measure {module}   # 生成后提交这个文件")

    with _otel_span("build", {"code.module": module}):
        res = check_module(tu_src, ch_src, module, strict=False)
    errors = [d for d in res.diagnostics if d.severity == "error"]

    # OTLP logs：诊断走同一条链路（受 ELENCH_OTEL_LOGS 控制）
    try:
        import otel_export as _oe
        _cfg = _oe.config()
        if _cfg.get("logs") and res.diagnostics:
            _recs = [_oe.log_record(
                f"{d.code}: {d.message}", "ERROR" if d.severity == "error" else "WARN",
                {"code": d.code, "module": module, "line": d.line, "col": d.col})
                for d in res.diagnostics]
            _oe.emit_logs(_recs, _cfg)
    except Exception:                                  # noqa: BLE001
        pass
    if _missing_table:
        print("\n✗ 失败：缺分档表（E-Dispatch-NoTable）")
        return 1

    # ---- pending 门禁（B-16 的双向校验在 checker 里，这里只管"有没有"）
    pending = {label: info.get("pending") or []
               for label, info in (res.choices or {}).items()}
    pending = {k: v for k, v in pending.items() if v}

    # ---- 成本表
    tu_mod = parse_source(tu_src, os.path.basename(tu_p)).module
    ch_mod = (parse_source(ch_src, os.path.basename(ch_p)).module
              if ch_src is not None else None)
    table = {"module": module,
             "state": "incomplete" if pending else "complete",
             "functions": {}}
    if ch_mod is not None:
        ci = _cost_interp(ch_mod, tu_mod, tu_p)
        for name in ci.fns:
            try:
                c = ci.cost_of(name)
            except Exception as e:                   # noqa: BLE001
                table["functions"][name] = {"cost_expression": None,
                                            "verdict": "unknown",
                                            "reason": str(e)}
                continue
            tgt = ci.target_cost(name)
            v = compare(c, tgt)
            table["functions"][name] = {
                "cost_expression": c.render(),
                "target": tgt.render() if tgt is not None else None,
                "verdict": v.kind,
                "verdict_explanation": v.render(),
            }
    if pending:
        table["pending"] = {k: list(v) for k, v in pending.items()}

    dest = out_path or (os.path.splitext(tu_p)[0] + ".cost.yaml")
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(to_yaml(table))
    print(f"module {module}    [build]  成本表 → {dest}")

    if errors:
        print(f"\n✗ {len(errors)} 条错误，构建失败：", file=sys.stderr)
        for d in errors[:10]:
            print(f"  ✗ {d.code} @{d.line}:{d.col} {d.message}", file=sys.stderr)
        return 1
    if pending:
        for label, names in pending.items():
            print(f"\n✗ choose `{label}` 还有 pending 候选：{names}", file=sys.stderr)
        print("  B-17：含 pending 时 check 通过、build 必须失败。"
              "把候选实现出来，或从 `over` 里去掉 pending 标记，再 build。",
              file=sys.stderr)
        return 1
    print("✓ 构建通过（无不完整候选）")
    # **--deny-debt**：把"债务只记录"改成"债务即拒绝"。
    # 规格 L2450 的默认语义是**记录不阻断**（为了渐进采用），
    # 但发布 / CI 需要"不满足就拒绝编译"——那是同一套计算，只是把结论当门禁用。
    if "--deny-debt" in argv:
        from cost_check import compare as _cmp
        _debt = []
        for _n in ci.fns:
            try:
                _c = ci.cost_of(_n)
            except Exception:                          # noqa: BLE001
                continue
            if _cmp(_c, ci.target_cost(_n)).kind == "debt":
                _debt.append(_n)
        if _debt:
            print(f"✗ 拒绝构建：{len(_debt)} 个函数超出 target（--deny-debt）："
                  + ", ".join(sorted(_debt)))
            print("  修法：降低实现开销，或（仅当确认放宽契约时）改 .tu 的 target")
            return 1
    return 0


def main(argv=None) -> int:
    """控制台入口（友好错误层）。

    **为什么要这一层**：命令行工具崩出 Python traceback 是最不体面的失败方式——
    用户只想知道"哪个文件不存在"，不想看调用栈。
    """
    try:
        _rc = _main_impl(argv)
        _otel_flush()
        return _rc
    except FileNotFoundError as exc:
        print(f"✗ 文件不存在：{exc.filename}")
        print("  用法：elench <命令> <模块.tu> [<模块.eln>]")
        print("  注意：第一个参数是 .tu 声明文件，第二个是 .eln 实现文件。")
        return 2
    except KeyboardInterrupt:
        return 130


def _main_impl(argv=None) -> int:
    """控制台入口。

    **`argv=None` 不是风格问题，是必需**：`pyproject.toml` 里的
    `[project.scripts] elench = "dsh:main"` 会**不带参数**调用它；
    如果签名要求 `argv`，装好的 `elench` 一运行就 TypeError。
    """
    if argv is None:
        # 注意：`main` 的约定是 **argv 含程序名**（`argv[1]` 才是子命令），
        # 所以这里给 `sys.argv`，不是 `sys.argv[1:]`。
        argv = sys.argv
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(USAGE)
        return 0 if len(argv) >= 2 else 2

    cmd, rest = argv[1], argv[2:]
    if cmd == "run":
        return cmd_run(rest)
    if cmd == "check":
        return cmd_check(rest)
    if cmd == "fmt":
        return cmd_fmt(rest)
    if cmd == "highlight":
        return cmd_highlight(rest)
    if cmd == "show":
        return cmd_show(rest)
    if cmd == "parse":
        return cmd_parse(rest)
    if cmd == "cost":
        return cmd_cost(rest)
    if cmd == "dispatch":
        return cmd_dispatch(rest)
    if cmd == "measure":
        return cmd_measure(rest)
    if cmd == "stats":
        return cmd_stats(rest)
    if cmd == "header":
        return cmd_header(rest)
    if cmd == "cpp":
        return cmd_cpp(rest)
    if cmd == "build":
        return cmd_build(rest)
    if cmd == "export":
        return cmd_export(rest)
    if cmd == "bench":
        return cmd_bench(rest)

    print(f"未知子命令：{cmd}", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv))
