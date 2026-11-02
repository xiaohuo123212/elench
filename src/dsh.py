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
    grammar   <模块.tu>           从 .tu 导出**受限语法**：EBNF + 词汇表 + 打洞骨架
                                  --format=ebnf|json|both（默认 both）；--out=文件

  成本（核心）
    cost      <模块>              算 κ 并与 target 比：达成 / 债务 / 无法判定
                                  承诺 = **分段上界**：每段用运行期真跑的那个候选的符号式
    stats     <模块> <函数> [--ops] 跑一次：调用数 / 分配数 / 迭代数 / **操作数**
                                  --ops：打开操作数计数并输出 JSON（与 κ 同单位）
    bench     <模块> <函数>       单函数隔离测量（只给判定与比值，不做全量统计）
    export    <模块> [--format=table|otlp|svg|csv] [--out=文件] [--function=名] [--timing]
                                  导出「静态上界 vs 实测」（默认实测=**操作数**，与 κ 同单位）
                                  --timing：实测改成纳秒（不同单位，图会各自归一化）
                                  不给 --out 就打到终端；给 --out 就写文件
                                  csv/svg/otlp 建议配 --out（否则粘不回文件）
                                  --push：按 OTLP/HTTP 推到 collector（配 OTEL_* 环境变量）

  决策点 ?! 与门禁
    dispatch  <模块>              三层筛：每个 ?! 要不要测、分几段（只出计划，不生成表）
    measure   <模块>              真跑候选，用实测生成分档表（可提交、可 diff）
    build     <模块> [--out=路径] [--measure] [--deny-debt]
                                  构建门禁：含 pending 就失败
                                  默认只跑静态 dispatch；--measure 才真跑测量（慢）
                                  --deny-debt：超出 target 即拒绝构建（**默认就是宽松的**：只记债务、退出 0）

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
    return CostInterp(ch_mod, tu_mod, table_path=path,
                      diags=getattr(ch_mod, "_parse_diags", None)
                      or getattr(tu_mod, "_parse_diags", None))


def _elench_argv() -> List[str]:
    """怎么用**当前解释器**再调一次 elench（restart 里要跑的就是它）。"""
    return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "elench.py")]


def _cost_restarts(module: str):
    """`κ > target` 时的修法清单。

    **绝不含"放宽 target"**：契约是持久的、实现是一次性的。要给本地一个出口，
    只有 `build --lenient`（不记录、不写文件），而不是把线挪走。
    """
    from checker import Restart
    base = _elench_argv()
    return [
        Restart("command", "重测并生成分档表", base + ["measure", module]),
        Restart("command", "查看分档与静态交点", base + ["dispatch", module]),
        # **不新增开关**：`build` 默认就是宽松的（只记债务、退出 0），
        # 门禁那档是 `--deny-debt`。这里只是把"默认行为"明确写成一条可选动作。
        Restart("command", "以默认（宽松）模式编译：只记债务、不阻断",
                base + ["build", module]),
        Restart("info", "定位超支来源：看上面 κ 表达式里增长最快的那一项"),
        Restart("info", "契约与实现不匹配（target 写错阶 / 函数已删）→ 人工确认，"
                        "工具不会改 target"),
    ]


def _print_restarts(actions, indent: str = "      ") -> None:
    if not actions:
        return
    print(f"{indent}修法：")
    for i, a in enumerate(actions, 1):
        tail = ""
        if a.kind == "command" and a.argv:
            tail = "  " + " ".join(a.argv[1:])
        elif a.kind == "edit":
            tail = f"  （{a.patch.get('file')}:{a.patch.get('line')}）"
        print(f"{indent}  {i}) {a.label}{tail}")
    print(f"{indent}  选一条：--apply=N（改文件的动作只打印 diff）")


def _parse_apply(argv) -> Optional[int]:
    for a in argv:
        if a.startswith("--apply="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                return None
    return None


def _run_restart(action, write: bool = False) -> int:
    """执行一条 restart。**只有 command 会真的跑**；edit 只打印建议。"""
    if action.kind == "command" and action.argv:
        print(f"  → 执行：{' '.join(action.argv[1:])}")
        try:
            r = subprocess.run(action.argv)
        except OSError as exc:
            print(f"  ✗ 起不来：{exc}", file=sys.stderr)
            return 1
        return r.returncode
    if action.kind == "edit":
        print(f"  → 建议改动：{action.patch.get('file')}:{action.patch.get('line')}")
        print(f"     {action.patch.get('text')}")
        print("     （未落盘：这是给你确认的 diff）")
        return 0
    print(f"  → {action.label}")
    return 0


def _table_covers_declared(tu_mod, tu_p, strict: bool = False) -> bool:
    """分档表必须覆盖 `benchmodel.scale` 的全区间；不覆盖就**报错**。

    为什么这不是"警告"：表外的规模，运行期会退回第一个候选，而承诺如果按表算，
    就会在表外**破界**（实测 gap n=8192：承诺 426,001 / 实测 729,109）。

    解决办法只有两个：① 重跑 `elench measure`（现在默认测满声明区间）；
    ② 改 `.tu` 里的 `scale`，把范围缩到真能测的规模。**不许**截断后继续。
    """
    try:
        sizes, lo, hi = _scale_range(tu_mod)
    except Exception:                                  # noqa: BLE001
        return True
    if not sizes or lo is None or hi is None:
        return True                                    # 没声明 → 不检查
    tbl_path = _tbl_for(tu_p)
    if not os.path.exists(tbl_path):
        return True                                    # 没有表：走 max，不在这里拦
    from interp import load_dispatch_table
    try:
        tbl = load_dispatch_table(tbl_path)
    except Exception as exc:                           # noqa: BLE001
        print(f"✗ 分档表读不了：{tbl_path}（{exc}）", file=sys.stderr)
        return False
    segs = [seg for v in tbl.values() for seg in v]
    if not segs:
        print(f"✗ 分档表是空的：{tbl_path}", file=sys.stderr)
        return False
    tlo = min(seg[0] for seg in segs)
    thi = max(seg[1] for seg in segs)
    if tlo <= lo and thi >= hi:
        return True
    # **为什么默认只是警告、不是硬报错**：
    # 表外并不是"错"——承诺在表外会退回"第一个候选"的 κ，而运行期也退回同一个
    # 候选，所以**仍然成立**（实测 gap n=8192/16384 都 OK）。缺的只是**紧度**。
    # 而真把 2^20 声明区间测满是分钟级（甚至小时级），硬报错会挡住所有样例。
    # 想要"必须覆盖"就当严格门：`--strict-coverage`。
    mark = "✗" if strict else "⚠"
    out = sys.stderr if strict else sys.stdout
    print(f"{mark} 分档表没覆盖声明的规模区间：", file=out)
    print(f"    声明 {lo:,}..{hi:,}（benchmodel.scale），表 {tlo:,}..{thi:,}", file=out)
    print(f"    未覆盖的规模：{thi + 1:,}..{hi:,}（这些规模上承诺按回退候选计，"
          f"仍然成立，只是不紧）", file=out)
    print("    修法：① 重跑 `elench measure`（现在默认测满声明区间）；"
          "② 或改 `.tu` 的 scale 把范围缩小。", file=out)
    return not strict


def _recursion_gate(tu_mod, ch_mod) -> bool:
    """C11-b 门禁：**有递归却没写 `recursion depth`** ⇒ 拒绝算 κ。

    `check` 里那条 `E-Recursion-Unbounded` 是编译期报错；这里是**同一道闸**的
    命令级落实。原因很简单：继续算的话 κ 只含一层，`cost` 会打印
    "✓ 所有函数都在 target 之内"，而实测是它的 3 倍——**假结论比报错更糟**。
    """
    if tu_mod is None or ch_mod is None:
        return False
    import checker as _ck
    impls = {it.name: it for it in ch_mod.children if it.kind == "fn"}
    specs = {it.name: it for it in tu_mod.children if it.kind == "spec"}
    diags = _ck.check_recursion_depth(impls, specs)
    if not diags:
        return False
    print(f"✗ 拒绝算 κ：{len(diags)} 个递归函数没有深度声明（C11-b）",
          file=sys.stderr)
    for d in diags:
        print(f"  ✗ {d.code} @{d.line}:{d.col}  {d.message}", file=sys.stderr)
    print("  （κ 只能按一层计，会比真实成本小；在 `.tu` 里补 "
          "`recursion depth <= N;` 后再算）", file=sys.stderr)
    return True



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


def _diagnose_all(tu, ai, strict: bool = False, overflow: str = "off"):
    """**唯一的检查入口**：`check` / `run` / `build` 都走这里。

    以前 `check` 与 `build` 各写一套：`build` 只调 `check_module`、**不跑类型检查**，
    于是 `func f() -> u64 { return true; }` 能"✓ 构建通过"。
    两套门禁必然漂移——只留一套。
    """
    from checker import Diag, check_files
    res = check_files(tu, ai, strict=strict, overflow=overflow)
    if not any(d.severity == "error" for d in res.diagnostics):
        try:
            from parser import parse_source as _ps
            from typecheck import typecheck
            _tu = _ps(io_read(tu), os.path.basename(tu)).module
            _ch = _ps(io_read(ai), os.path.basename(ai)).module if ai else None
            for d in typecheck(_tu, _ch):
                res.diagnostics.append(Diag(d.code, d.message, d.line, d.col,
                                             severity=getattr(d, "severity", "error")))
        except Exception as e:                      # noqa: BLE001
            # **绝不静默跳过**（E10）：检查器崩了就等于"没查"，而没查 MUST NOT 显示为通过。
            res.diagnostics.append(Diag(
                "E-Internal-TypeCheck",
                f"类型检查器内部异常，本次类型检查未完成：{type(e).__name__}: {e}"
                f"（这不代表代码正确——它代表**没检查**）",
                0, 0, severity="error"))
    return res


def cmd_check(argv: List[str]) -> int:
    argv = _expand_module_args(argv)
    if not argv:
        print("用法：elench check <模块.tu> [<模块.eln>]", file=sys.stderr)
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
    tu = _resolve_module_path(paths[0])
    ai = paths[1] if len(paths) > 1 else None
    if ai:
        ai = _resolve_module_path(ai, ".eln")

    if tu is None:
        print(f"✗ 找不到 {paths[0]}（试过：原样、+.tu、+.eln、仓库样例目录）", file=sys.stderr)
        return 2

    res = _diagnose_all(tu, ai, strict=strict, overflow=overflow)

    if as_json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0 if res.ok else 1

    # ---- 人读输出：**先给结论，再给细节** ----
    # 以前是"模块表 → 诊断（stderr）"，两个流交错时看起来像两段无关的东西。
    errs = [d for d in res.diagnostics if d.severity == "error"]
    warns = [d for d in res.diagnostics if d.severity != "error"]
    if errs:
        extra = f"，{len(warns)} 条警告" if warns else ""
        print(f"✗ {len(errs)} 条错误{extra}")
        for d in res.diagnostics:
            loc = f"@{d.line}:{d.col}" if d.line else ""
            tag = "✗" if d.severity == "error" else "⚠"
            print(f"  {tag} {d.code} {loc}  {d.message}")
        print()
    elif warns:
        print(f"⚠ {len(warns)} 条警告（不阻断构建）")
        for d in warns:
            loc = f"@{d.line}:{d.col}" if d.line else ""
            print(f"  ⚠ {d.code} {loc}  {d.message}")
        print()
    else:
        print("✓ 没有诊断")
        print()

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
    planned = sum(1 for f in res.functions if not f.has_impl)
    pending = sum(len(v["pending"]) for v in res.choices.values())
    if errs:
        # **退出码与 --format=json 一致**：有 error 就 1（以前人读模式恒 0，CI 会误绿）
        print(f"✗ 未通过（{len(res.functions)} 个函数，{len(errs)} 条错误）")
        return 1
    print(f"✓ 通过（{len(res.functions)} 个函数，其中 {planned} 个无体）")
    if pending:
        print(f"  ⚠ 有 {pending} 个 pending 候选：dsh check 通过，但 dsh build 必失败（B-17）")
    print("  注：check 只做静态检查；done / debt 由 elench cost 与 elench build 判定")
    return 0


def cmd_fmt(argv: List[str]) -> int:
    check_only = "--check" in argv
    to_stdout = "--stdout" in argv
    files = [a for a in argv if not a.startswith("--")]
    if not files:
        print("用法：elench fmt <文件或模块名>... [--check] [--stdout]", file=sys.stderr)
        return 2
    resolved_files = []
    for a in files:
        r = _resolve_module_path(a, ".eln") or _resolve_module_path(a, ".tu")
        if r is None:
            print(f"✗ 找不到 {a}（试过：原样、+.tu、+.eln、仓库样例目录）", file=sys.stderr)
            return 2
        resolved_files.append(r)
    files = resolved_files

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
        print("用法：elench highlight <文件> [--tokens]", file=sys.stderr)
        return 2
    from highlight import highlight, tokens_of

    for raw_path in files:
        path = _resolve_module_path(raw_path, ".eln") or _resolve_module_path(raw_path, ".tu")
        if path is None:
            print(f"✗ 找不到 {raw_path}（试过：原样、+.tu、+.eln、仓库样例目录）", file=sys.stderr)
            return 2
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
        print("用法：elench show <模块.tu> <label>", file=sys.stderr)
        return 2
    _resolved = _resolve_module_path(argv[0])
    if _resolved is None:
        print(f"✗ 找不到 {argv[0]}（试过：原样、+.tu、+.eln、仓库样例目录）", file=sys.stderr)
        print("  用法：elench show <模块或文件> <label>   例：elench show pick kernel", file=sys.stderr)
        return 2
    tu, label = _resolved, argv[1]
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
        print("用法：elench parse <文件>", file=sys.stderr)
        return 2
    target = _resolve_module_path(argv[0])
    if target is None:
        print(f"✗ 找不到 {argv[0]}（试过：原样、+.tu、+.eln、仓库样例目录）", file=sys.stderr)
        print("  用法：elench parse <文件或模块名>   例：elench parse pick", file=sys.stderr)
        return 2
    res = parse_file(target)
    print(res.module.dump() if res.module else "(空)")
    if res.diagnostics:
        print(f"-- {len(res.diagnostics)} 条诊断 --")
        for d in res.diagnostics:
            print(f"  {d.code} @{d.line}:{d.col}  {d.message}")
        return 1
    return 0



_SAMPLE_DIRS = ("文档/样例", "文档/入门/例子", "docs/样例", "examples")


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_module_path(arg: str, ext: str = ".tu"):
    """把命令行给的参数解析成**真实存在的文件**。

    顺序（这也是"不能只看文件名"的意思）：
      1. 原样存在（`a.tu` / `a.eln` / 任意路径）
      2. 补扩展名：`pick` → `pick.tu` / `pick.eln`
      3. 仓库自带的样例目录：`pick` → `文档/样例/pick.tu`、`文档/入门/例子/pick.tu`
      4. 都不行 → None
    """
    cands = []
    if os.path.exists(arg):
        return arg
    base, e = os.path.splitext(arg)
    if e:
        cands.append(arg)
    else:
        cands.extend([arg + ext, arg + (".eln" if ext == ".tu" else ".tu")])
    for c in cands:
        if os.path.exists(c):
            return c
    # 样例目录（相对仓库根；`elench parse pick` 在哪个目录跑都能找到）
    root = _repo_root()
    name = os.path.basename(base if e else arg)
    for d in _SAMPLE_DIRS:
        for suffix in (".tu", ".eln"):
            c = os.path.join(root, d, name + suffix)
            if os.path.exists(c):
                return c
    return None


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
    # **单参数写法**：`elench header count` → `count.tu` / `count.eln`，也支持模块名
    if not os.path.splitext(tu)[1]:
        resolved = _resolve_module_path(tu)
        if resolved:
            tu = resolved
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


def cmd_grammar(argv: List[str]) -> int:
    """`elench grammar <模块.tu>` —— 导出**受限语法**（给 LLM 的受限解码用）。

    产物三件（`--format=both` 时一起打印）：

    1. **语言 EBNF**：`.eln` 的语句/表达式产生式，所有模块相同；
    2. **模块词汇表（JSON）**：本模块允许的函数 / 外部函数 / 类型 / 常量，含签名与 effects；
    3. **打洞骨架**：每个函数一个"签名齐全、body 是洞"的骨架。

    **为什么不做 JSON Schema**：那等于把 `parser.py` 重写一遍，两份语法必然漂移，
    而漂移的语法比没有语法更危险（解码器放行、检查器拒绝）。
    """
    from parser import parse_source
    import grammar as _g

    fmt = "both"
    out_path = None
    rest: List[str] = []
    for a in _expand_module_args(argv):
        if a.startswith("--format="):
            fmt = a.split("=", 1)[1]
        elif a.startswith("--out="):
            out_path = a.split("=", 1)[1]
        else:
            rest.append(a)

    tu_p, _ch = _load_pair(rest)
    if tu_p is None:
        print("用法：elench grammar <模块.tu> [--format=ebnf|json|both] [--out=文件]",
              file=sys.stderr)
        return 2
    tu_mod = parse_source(io_read(tu_p), os.path.basename(tu_p)).module
    try:
        text = _g.render(tu_mod, fmt)
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2

    if out_path:
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text if text.endswith("\n") else text + "\n")
        print(f"已写出 {out_path}")
    else:
        print(text)
    return 0


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
    if _recursion_gate(tu_mod, ch_mod):
        return 1
    if not _table_covers_declared(tu_mod, tu_p,
                                  strict=("--strict-coverage" in argv)):
        return 1
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


def _eval_at(cost, var, n):
    """把成本式在 n 处的值算出来（用于同阶候选比常数）；算不出给 0。"""
    try:
        from symbolic import evaluate
        return float(evaluate(cost, {var: n}))
    except Exception:                                       # noqa: BLE001
        return 0.0

def _static_plan_table(ci, tu_mod):
    """用**静态分档规划**造一张表：{label: [(lo, hi, 候选), ...]}。

    这是"承诺取每段最优"的静态版本：不需要先 measure，
    只要 `benchmodel.scale` 给出输入范围，`plan_dispatch` 就能算出交叉点。
    """
    from dispatch import plan_dispatch
    from bound import _descendants

    out = {}
    # kernels 在**实现模块**里（ci.mod），不在 .tu 里
    for kern in _descendants(ci.mod, "kernels"):
        label = kern.name
        if not label:
            continue
        cands = _candidate_costs(ci, label)
        if len(cands) < 2:
            continue
        var = _size_var(cands)
        lo, hi = 1, 4096
        # 从 benchmodel.scale 里取范围（拿不到就用默认）
        for bm in _descendants(tu_mod, "benchmodel"):
            for sc in _descendants(bm, "scale"):
                vals = [c.value for c in (sc.children or []) if isinstance(c.value, int)]
                if len(vals) >= 2:
                    lo, hi = min(vals), max(vals)
        plan = plan_dispatch(label, cands, var, lo, hi)
        segs = [(int(a), int(b), str(n)) for a, b, n in getattr(plan, "segments", [])]
        if not segs:
            # 没有交叉点 ⇒ 全程用阶最低（同阶再比常数）的那个候选，
            # 这仍然是「逐段最优」：只有一段，段内最优。
            from dispatch import order_of
            best = min(cands, key=lambda k: (order_of(cands[k]), _eval_at(cands[k], var, hi)))
            segs = [(lo, hi, best)]
        out[label] = segs
    return out

def _print_static_segments(ci, ch_mod, fn_name: str, w: int = 8) -> None:
    """打印某个函数里 `?!` 的**静态分段建议**（解 κ 的交叉点，不测量）。

    为什么放在 `cost` 里：承诺（= max over candidates）与"哪一段该用谁"是两件事。
    把两张表混着用，才会出现"表只到 4096、承诺在表外破界"
    （`gap` n=8192：承诺 426,001，实测 729,109）。
    """
    try:
        from checker import walk as _walk
        from segment_plan import plan, render
    except Exception:                                  # noqa: BLE001
        return
    fn = ci.fns.get(fn_name)
    if fn is None:
        return
    nodes = _walk(fn, "dispatch")
    if not nodes:
        return
    sizes, lo, hi = _scale_range(ci.decl)
    var = next(iter(sizes), "xs")
    for node in nodes:
        label = node.name or ""
        try:
            cands = ci.candidate_costs(label, {})
        except Exception:                              # noqa: BLE001
            continue
        if not cands:
            continue
        res = plan(cands, var, lo if lo else 8, hi if hi else 65536)
        print(render(label, res))


def cmd_cost(argv: List[str]) -> int:
    argv = _expand_module_args(argv)
    _apply = _parse_apply(argv)
    """静态闭环：算成本式，与 target 比，报债务。"""
    from parser import parse_source
    from cost_interp import CostInterp
    from cost_check import compare, compare_exact

    tu_p, ch_p = _load_pair(argv)
    if tu_p is None:
        print("用法：elench cost <模块.tu> [<模块.eln>]", file=sys.stderr)
        return 2

    tu_mod = parse_source(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = (parse_source(io_read(ch_p), os.path.basename(ch_p)).module
              if ch_p and os.path.exists(ch_p) else None)
    if ch_mod is None:
        print("没有实现文件，无法算成本", file=sys.stderr)
        return 2

    if _recursion_gate(tu_mod, ch_mod):
        return 1
    if not _table_covers_declared(tu_mod, tu_p,
                                  strict=("--strict-coverage" in argv)):
        return 1

    ci = _cost_interp(ch_mod, tu_mod, tu_p)
    # **承诺口径（2026-11-01 定）**：承诺 κ = `max over candidates` 的**符号式**，
    # 与分档表无关——表只决定运行期跑哪个候选，不参与"承诺多少"。
    #
    # 曾经这里分两档（有表就按逐段上界），实测（`gap`，表只覆盖 8..4096）暴露三件事：
    #   ① 蓝线是一条水平线；② 真拐点（n≈2,715）被抹掉；
    #   ③ 表范围外破界（n=8192：承诺 426,001，实测 729,109）。
    # 原因只有一个：**表在替公式做决定**。
    print("  （口径：承诺 = 分段函数——每段用**运行期真正会跑的那个候选**的符号式；"
          "分段取自运行期同一份分档表，越界按运行期的回退；没有表时退回 max）")

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

        # **副作用分列**（⑤ Quasar 的"按 effects 记账"改造版）：
        # 只统计**可指认的**部分——每个 effect 有几处调用点、分别调了谁；
        # **不**把解释器操作按 effect 分摊（一次操作不属于任何副作用，硬分就是编数）。
        try:
            _ea = ci.effect_attribution(name)
        except Exception:                              # noqa: BLE001
            _ea = None
        if _ea and (_ea.get("declared") or _ea.get("effects") or _ea.get("ext_terms")):
            _decl = "、".join(_ea.get("declared") or []) or "无"
            _txt = f"    {'':{w}}  副作用：声明 {_decl}"
            if _ea.get("effects"):
                _bits = []
                for _e in sorted(_ea["effects"]):
                    _slot = _ea["effects"][_e]
                    _bits.append(f"{_e}×{_slot['sites']}（{'、'.join(_slot['callees'])}）")
                _txt += "；调用点 " + "；".join(_bits)
            if _ea.get("ext_terms"):
                _txt += "；外部项 " + "、".join(f"ext({x})" for x in _ea["ext_terms"])
            print(_txt)
        if v.kind == "debt":
            _acts = _cost_restarts(os.path.splitext(os.path.basename(tu_p))[0])
            _print_restarts(_acts, indent="    ")
            if _apply is not None:
                if not (1 <= _apply <= len(_acts)):
                    print(f"  ✗ --apply={_apply} 越界（可选 1..{len(_acts)}）",
                          file=sys.stderr)
                    return 2
                return _run_restart(_acts[_apply - 1])

        # **静态分段建议**（不依赖测量）：`?!` 的两个候选在哪里交叉，
        # 解两条 κ 的方程就能得到（`104n+110 = 23n+220042` → n≈2715）。
        _print_static_segments(ci, ch_mod, name, w)
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
        print("用法：elench dispatch <模块.tu> [<模块.eln>]", file=sys.stderr)
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
        # **候选不同阶是最常见的误用**：承诺取 max，混入一个高阶候选会把
        # 整个函数的 κ 抬到那个阶。这里明确警告，而不是等用户看到"债务"发懵。
        from dispatch import order_of
        orders = {name: order_of(c) for name, c in cands.items()}
        if len(set(orders.values())) > 1:
            detail = "、".join(f"{n}=阶{o}" for n, o in sorted(orders.items()))
            worst = max(orders.values())
            print(f"  ⚠ 候选不同阶（{detail}）：默认口径按段取上界（每段用该段最优），没问题；"
                  f"但 `cost --promise` 会取最差候选（阶{worst}），把 target 撑大。")
            print()
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
        print("用法：elench measure <模块.tu> [<模块.eln>]", file=sys.stderr)
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
        # **默认测满 `benchmodel.scale` 声明的每一个点。**
        #
        # 以前这里有个隐式的 `cap = 4096`：超出的点直接丢掉、只打一行提示、
        # 表照常产出。后果是 `.tu` 声明 8..65536、表只到 4096，
        # **benchmodel 形同虚设**——声明归声明，工具自己改。
        # 更糟的是运行期在表外"退回第一个候选"，承诺却按表算 → 表外破界
        # （实测 gap n=8192：承诺 426,001 / 实测 729,109）。
        #
        # 现在：测不动就**报错**，让人去改声明（缩小 scale）或改实现；
        # 真要用降级模式，必须显式 `--dev-cap=N`，产出的表会被标注
        # **不可用于承诺**（`degraded: true`）。
        dev_cap = None
        for a in argv:
            if a.startswith("--dev-cap="):
                try:
                    dev_cap = int(a.split("=", 1)[1])
                except ValueError:
                    print(f"  ！ --dev-cap 需要整数：{a}", file=sys.stderr)
                    return 2
        full_pts = list(pts)
        if dev_cap is not None:
            pts = [x for x in pts if x <= dev_cap] or full_pts[:2]
            print(f"  ！ 降级模式 --dev-cap={dev_cap}：只测 {pts}")
            print("  ！ 产出的表**不覆盖声明区间**，不可用于承诺（degraded: true）")
        measured = {}
        failed = []
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
                failed.append((label, cand, str(e)))
                print(f"  ?!{label}/{cand}: 测量失败 —— {e}")
        if failed:
            print(f"  ✗ {label}：{len(failed)} 个候选测量失败，**不产出分档表**")
            for _l, _c, _m in failed:
                print(f"      {_c}: {_m}")
            return 1
        if not measured:
            continue
        # **每个候选都必须测到每一个声明点**——缺一个就等于表有洞。
        thin = [c for c in cands if len(measured.get(c) or {}) < len(pts)]
        if thin:
            print(f"  ✗ {label}：这些候选没测满 {len(pts)} 个点：{thin}")
            return 1
        segs = table_from_measure(label, pts, measured)
        # **就地落盘**：分档表必须成为可提交、可审查的文件，`build` 才有东西可校验。
        # 不放在函数末尾——那里有 return，容易漏执行（踩过）。
        _mod = os.path.basename(tu_p).rsplit(".", 1)[0]
        _tblp = os.path.join(os.path.dirname(os.path.abspath(tu_p)), _mod + ".dispatch.yaml")
        with open(_tblp, "w", encoding="utf-8", newline="\n") as _f:
            _f.write("# 由 `elench measure` 生成；MUST NOT 手改。\n")
            if dev_cap is not None or len(pts) < len(full_pts):
                _f.write("# DEGRADED: 未覆盖 benchmodel.scale 声明的全部规模，"
                         "不可用于承诺\n")
                _f.write("degraded: true\n")
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



def _brief_repr(v, width: int = 120) -> str:
    """把返回值压成一行（JSON 里不放整个容器）。"""
    s = repr(v)
    return s if len(s) <= width else s[:width] + "…"


def cmd_stats(argv: List[str]) -> int:
    """M1 验收③：跑一次，报每函数调用数 / 分配数 / 迭代数 / **操作数**。

    `--ops`（或 `--json`）：打开操作数计数，输出**机器可读 JSON**。
    操作数与 `elench cost` 的 κ **同单位**（都是「操作」），可以并排比大小；
    `evaluation_steps` 与纳秒**不能**拿来和 κ 比（见计划 §8）。
    """
    from interp import Interp, ops_enable, ops_enabled, ops_reset
    from parser import parse_source

    argv = _expand_module_args(argv)
    as_json = ("--ops" in argv) or ("--json" in argv)
    argv = [a for a in argv if a not in ("--ops", "--json")]

    if len(argv) < 2:
        print("用法：elench stats <模块> <函数> [参数...] [--ops]",
              file=sys.stderr)
        return 2
    # 两种写法都收：`stats pick rank` 与 `stats pick.tu pick.eln rank`
    if len(argv) >= 3 and (argv[0].endswith(".tu") or argv[1].endswith(".eln")):
        tu_p, ch_p, fn = argv[0], argv[1], argv[2]
        rest = argv[3:]
    else:
        tu_p, ch_p = _load_pair([argv[0]])
        fn, rest = argv[1], argv[2:]
    tu_p = _resolve_module_path(tu_p) if tu_p else None
    if tu_p is None:
        print(f"✗ 找不到模块 {argv[0]}", file=sys.stderr)
        return 2
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
        if as_json and not ops_enabled():
            ops_enable()
        ops_reset()
        val = it.call(fn, args)
    except Exception as e:                          # noqa: BLE001
        print(f"执行失败：{e}", file=sys.stderr)
        return 1
    c = it.counters()
    if as_json:
        # **机器可读**：ops 与 κ 同单位，`tools/kappa_vs_ops.py` 拿它做对账。
        payload = {
            "module": os.path.basename(tu_p).rsplit(".", 1)[0],
            "function": fn,
            "args": [len(a) if isinstance(a, (list, tuple)) else a for a in args],
            "value": _brief_repr(val),
            "ops": c["operations"],
            "ops_by_function": c["operations_by_function"],
            "evaluation_steps": c["evaluation_steps"],
            "method_calls": c["method_calls"],
            "allocations": c["allocations"],
            "iterations": c["iterations"],
            "function_calls": c["function_calls"],
            "unit": "operation",
            "note": "ops 是操作数（表达式求值 + 语句执行 + 内建调用），不是纳秒；与 κ 同单位",
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    print(f"module {os.path.basename(tu_p).rsplit('.', 1)[0]}")
    print(f"  {fn}({len(args)} 个实参) → {val}")
    print(f"  调用数（每函数）")
    for fname in sorted(c["function_calls"]):
        print(f"      {fname:<24} {c['function_calls'][fname]:,}")
    print(f"      合计 {c['function_calls_total']:,}"
          f"   （其中方法调用 {c['method_calls']:,}）")
    print(f"  分配数   {c['allocations']:,}   （push 触发扩容的次数）")
    print(f"  迭代数   {c['iterations']:,}   （循环体执行次数）")
    if ops_enabled():
        print(f"  操作数   {c['operations']:,}   "
              f"（表达式求值 + 语句执行 + 内建调用；与 κ 同单位，不是纳秒）")
    else:
        print("  操作数   —（未计数；加 --ops 开启，或设 ELENCH_COUNT_OPS=1）")
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
    """把「静态上界 vs 实测」导出成能画图的形式（OTLP / SVG / CSV / 表）。

    ## 实测的两把尺子（`--timing` 选）

    | 模式 | 实测 | 单位 | 和 κ 的关系 |
    |---|---|---|---|
    | **默认** | `bench.measure_ops`（`interp` 操作数计数器） | 操作 | **同单位**：直接判 `实测 ≤ κ`，不归一化 |
    | `--timing` | `bench.measure`（中位数纳秒） | 纳秒 | 不同单位：只能归一化后比斜率 |

    两个模式**共用同一套实参**（`bench._build_args`），换的只是"量什么"。
    """
    if ("--full" in argv) or ("--report" in argv):
        _sizes = None            # None = 由模块的 benchmodel.scale 决定
        _runs = 20
        _pos = []
        _out = None
        _split = "--split" in argv
        for _a in argv:
            if _a.startswith("--sizes="):
                _sizes = [int(x) for x in _a.split("=", 1)[1].split(",") if x.strip()]
            elif _a.startswith("--runs="):
                _runs = int(_a.split("=", 1)[1])
            elif _a.startswith("--out="):
                _out = _a.split("=", 1)[1]
            elif not _a.startswith("--"):
                _pos.append(_a)
        if not _pos:
            print("用法：elench export <模块> --full [--sizes=…] [--runs=20] "
                  "[--out=图.svg] [--split]", file=sys.stderr)
            return 2
        _max = None
        _maxp = None
        for _a in argv:
            if _a.startswith("--max-size="):
                _max = int(_a.split("=", 1)[1])
            elif _a.startswith("--max-points="):
                _maxp = int(_a.split("=", 1)[1])
        return _full_report(_pos[0], _sizes, _runs, out_path=_out, split=_split,
                            max_size=_max, max_points=_maxp)

    from bench import measure, measure_ops
    from bound import _descendants
    from cost_interp import CostInterp
    from otel import report, series, sizes_to, to_csv, to_otlp, to_svg
    from parser import parse_source

    fmt = "table"
    for a in argv:
        if a.startswith("--format="):
            fmt = a.split("=", 1)[1]
    # **`--timing` = 退回纳秒**（默认是操作数，与 κ 同单位）
    timing = "--timing" in argv
    unit = "nanoseconds" if timing else "operations"
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
        print("用法：elench export <模块> [--format=table|otlp|svg|csv] [--out=文件] "
              "[--function=名] [--timing]",
              file=sys.stderr)
        return 2
    tu_mod = parse_source(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = parse_source(io_read(ch_p), os.path.basename(ch_p)).module
    if _recursion_gate(tu_mod, ch_mod):
        return 1
    if not _table_covers_declared(tu_mod, tu_p,
                                  strict=("--strict-coverage" in argv)):
        return 1
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
        _plan_segments = list(_plan.segments)
        # **有实测表就用实测表的分段**（2026-11-01 修）：
        # 承诺线必须与实际执行同源，否则"承诺"按静态规划画、实测按表跑，
        # 两条线根本不是同一套分段（matmul/gap 上表现为承诺与实测评行不了）。
        _tbl_segs = []
        try:
            _tbl_p = _tbl_for(tu_p)
            if os.path.exists(_tbl_p):
                from interp import load_dispatch_table
                _tbl_segs = load_dispatch_table(_tbl_p).get(label) or []
        except Exception:                                  # noqa: BLE001
            _tbl_segs = []
        if _tbl_segs:
            _plan_segments = _tbl_segs
            plan_reason = "实测分档表"
        for _lo, _hi, _nm in _plan_segments:
            a, b = max(_lo, _w0), min(_hi, _w1)
            if a <= b:
                segments.append((a, b, _nm))

    _has_tbl = os.path.exists(_tbl_for(tu_p))
    if not _has_tbl and "--measure" not in argv:
        # **不假装有实测**：没有分档表时，实测线只能测"某一个候选"，
        # 而承诺是按段最优——两者口径不同，画在一起就是错的。
        print("（未实测：只画静态线；要实测先 elench measure 或加 --measure）")
        measured = None
    else:
      try:
        # **有分档表就让表决定跑哪个候选**（承诺线与实测线同源）：
        # 以前无条件 `force=最低阶候选`，会在 n=4096 强行跑 heavy，
        # 而承诺按表选的是 setup —— 两条线量的不是同一个实现。
        _force = None
        if not _has_tbl and label and cand_name:
            _force = {label: cand_name}
        if timing:
            measured = measure(ch_mod, tu_mod, fn_name, pts,
                               force=_force, table_path=_tbl_for(tu_p))
        else:
            measured = measure_ops(ch_mod, tu_mod, fn_name, pts,
                                   force=_force, table_path=_tbl_for(tu_p))
      except Exception as e:                        # noqa: BLE001
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
                  candidate_cost=cand_cost, piecewise=_pw or None,
                  unit=unit)
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

    if len(argv) < 2:
        print("用法：elench bench <模块> <函数> [--repeats=N]", file=sys.stderr)
        return 2
    # 两种写法都收：`bench pick rank` 与 `bench pick.tu pick.eln rank`
    if len(argv) >= 3 and (argv[0].endswith(".tu") or argv[1].endswith(".eln")):
        tu_p, ch_p, fn_name = argv[0], argv[1], argv[2]
    else:
        tu_p, ch_p = _load_pair([argv[0]])
        fn_name = argv[1]
    tu_p = _resolve_module_path(tu_p) if tu_p else None
    if tu_p is None:
        print(f"✗ 找不到模块 {argv[0]}", file=sys.stderr)
        return 2
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


def _gate_checks(tu_p, ch_p, argv) -> bool:
    """跑 `check_module`；有 error 就拒绝继续（`run` / `build` 共用）。

    为什么必须在这里卡：审计实测过 —— `func f() -> u64 { return true; }`
    能 `build` 出"✓ 构建通过"，还能 `run` 出 `True`。
    **一个会说"通过"的门禁，比缺功能严重得多。**
    """
    if "--no-check" in argv:
        print("⚠ --no-check：跳过静态检查（仅供调试，输出**不可信**）")
        return True
    r = _diagnose_all(tu_p, ch_p if (ch_p and os.path.exists(ch_p)) else None)
    errs = [d for d in r.diagnostics if d.severity == "error"]
    if errs:
        print(f"✗ 未通过：{len(errs)} 条错误（先修完再 run/build）", file=sys.stderr)
        for d in errs[:5]:
            print(f"  {d.code} @{d.line}:{d.col} {d.message[:70]}", file=sys.stderr)
        return False
    return True


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
    if not _gate_checks(tu_p, ch_p, argv):
        return 1

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


# ---------------------------------------------------------------- 项目配置
# **唯一实现放在 `policy.py`**；这里只保留薄封装，避免两个模块各写一份。
from policy import (  # noqa: E402
    find_config as _policy_find_config,
    load_config as _policy_load_config,
    option as _policy_option,
    static_plan as _policy_static_plan,
    mode as _policy_mode,
    require_measured as _policy_require_measured,
    table_path as _policy_table_path,
)


def find_project_config(start_dir):
    return _policy_find_config(start_dir)


def load_project_config(start_dir):
    return _policy_load_config(start_dir)


def project_opt(start_dir, section, key, default=None):
    return _policy_option(start_dir, section, key, default)


def _static_plan_table(ci, tu_mod):
    """静态分段（实现见 policy.static_plan）。"""
    return _policy_static_plan(ci, tu_mod)


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

    # **门禁第一关**：静态检查不过，就不许"构建通过"（2026-11-01）
    if not _gate_checks(tu_p, ch_p, argv):
        return 1

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
                # **口径与 `cost` 对齐**：没有实测表时，静态分段仍然可用，
                # 所以 build 默认**警告放行**；要强制"必须实测"才加 --require-measured。
                _require = _policy_require_measured(
                    argv, os.path.dirname(os.path.abspath(tu_p)))
                if _require:
                    _missing_table.append(_labels)
                    print(f"  ✗ E-Dispatch-NoTable：有 ?!（{', '.join('?!' + x for x in _labels)}）"
                          f"但没有 {module}.dispatch.yaml（elench.toml 的 require_measured 或 --require-measured）")
                    print(f"    修法：elench measure {module}   # 生成后提交这个文件")
                elif "--allow-missing-table" in argv:
                    print(f"  ！ 缺分档表 {module}.dispatch.yaml（--allow-missing-table，本次放行）")
                else:
                    print(f"  ⚠ 缺分档表 {module}.dispatch.yaml：按**静态分段**放行（未实测）；"
                          f"要强制实测加 --require-measured")
                    print(f"    建议：elench measure {module}   # 生成后提交这个文件")

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
            _mod_b = os.path.splitext(os.path.basename(tu_p))[0]
            _print_restarts(_cost_restarts(_mod_b), indent="  ")
            return 1
    return 0


VERSION = "1.0.0"
ARGV_VERSION = ("--version", "-V", "version")


def _full_report(module_arg: str, sizes, runs, wc_path=None, out_path=None,
                 split: bool = False, max_size=None,
                 max_points=None) -> int:
    """**完整报告**：上界、实测、紧度**分三节**，每节一张表。

    刻意不用缩写：`typical / worst / slack / ops` 一律写全（典型、最坏、松度、操作数），
    也不把三件事挤进一张表——那会让人以为它们是同一层的量。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tools_dir = os.path.join(root, "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    try:
        from kappa_vs_measured import measure_one      # noqa: E402
    except Exception as exc:                           # noqa: BLE001
        print(f"无法加载测量器：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    # 模块名统一取 basename（去掉 .tu/.eln）：measure_one 按名字在样例目录里找
    # 既能给名字（在样例目录里找），也能给路径（任意模块）
    # 已带后缀 → 原样；不带 → 先试 `<名>.tu`（当前目录），再回退样例目录
    if module_arg.endswith((".tu", ".eln")):
        _cand = module_arg
    elif os.path.exists(module_arg + ".tu"):
        _cand = module_arg + ".tu"
    else:
        _cand = None
    # ⚠️ `prog` 要给 measure_one：有文件就用**路径**（它自己会加 .eln），
    # 否则退成裸名字（去样例目录找）。以前这里丢了路径，于是"人就在样例目录里"也找不到。
    prog = _cand if _cand else os.path.basename(module_arg).rsplit(".", 1)[0]

    # **规模从哪来**：优先 `benchmodel.scale`（模块自己声明的规模点），
    # 只有它没写时才退回默认 16/64/256。以前固定默认值，等于把
    # 作者声明的规模当不存在。
    _src = "默认（模块没写 benchmodel.scale）"
    if not sizes:
        try:
            from bench import read_benchmodel           # noqa: E402
            from parser import parse_source as _ps      # noqa: E402
            _samples = __import__("kappa_vs_measured").SAMPLES
            _tu = _cand if _cand else os.path.join(_samples, prog + ".tu")
            if os.path.exists(_tu):
                _m = _ps(io.open(_tu, encoding="utf-8").read(), prog + ".tu").module
                _data, _scale, _seed = read_benchmodel(_m)
                _pts = []
                for _v in (_scale or {}).values():
                    _pts.extend(_v)
                _pts = sorted(set(p for p in _pts if isinstance(p, int) and p > 0))
                if _pts:
                    # **作者写几个点就测几个点**。
                    # 以前这里有两道静默截断：先按 `≤4096` 砍掉大点，再"最多 6 个"
                    # 抽稀——`range(1, 65536, *2)` 的 17 个点只剩 6 个。
                    # 那是在替作者改他的 benchmodel，属于**不可接受的静默**。
                    # 真要限速/抽稀，用 `--max-size=` / `--max-points=` 显式说。
                    _u = sorted(set(_pts))
                    if max_size:
                        _cut = [p for p in _u if p <= max_size]
                        _u = _cut or _u[:1]
                    if max_points and len(_u) > max_points:
                        _idx = [round(i * (len(_u) - 1) / (max_points - 1))
                                for i in range(max_points)]
                        _u = [_u[i] for i in _idx]
                    sizes = _u
                    _src = "benchmodel.scale"
        except Exception:                              # noqa: BLE001
            sizes = None
    if not sizes:
        sizes = [16, 64, 256]

    rows = []
    for size in sizes:
        for r in measure_one(prog, size, runs):
            if r.get("error") or "max_actual" not in r:
                continue
            typical = float(r["max_actual"] or 0)
            if typical <= 0:
                continue
            rows.append({
                "function": r["function"],
                "size": size,
                "bound": float(r.get("kappa_at_n") or 0),
                "target": r.get("target_at_n"),
                "typical": typical,
            })
    if not rows:
        import glob as _glob
        _samples = __import__("kappa_vs_measured").SAMPLES
        _try = module_arg if module_arg.endswith(".tu") else module_arg + ".tu"
        _hits = _glob.glob(_try) + _glob.glob(os.path.join(_samples,
                                                           os.path.basename(_try)))
        if not _hits:
            print(f"找不到模块：{module_arg}", file=sys.stderr)
            print(f"  试过：{_try}、"
                  f"{os.path.join(_samples, os.path.basename(_try))}", file=sys.stderr)
            print("  提示：可以给路径，例如 "
                  "`elench export 文档/入门/例子/crossover --report`", file=sys.stderr)
            return 2
        print(f"模块找到了，但没有可对照的函数：{_hits[0]}", file=sys.stderr)
        print("  报告需要**带 `target` 的 spec**；请检查 .tu 是否写了 target。",
              file=sys.stderr)
        return 1

    # 最坏观测（若已有 worstcase.json）
    worst = {}
    path = wc_path or os.path.join(tools_dir, "worstcase.json")
    try:
        if path and os.path.exists(path):
            data = json.load(io.open(path, encoding="utf-8"))
            for row in data.get(prog, []) or []:
                worst[(row.get("function"), row.get("n"))] = row
    except Exception:                                  # noqa: BLE001
        worst = {}

    print("=" * 68)
    print(f"成本报告：{prog}")
    print(f"规模来源：{_src}　　规模点：{len(sizes)} 个——"
          f"{', '.join(str(x) for x in sizes)}")
    print(f"每点输入组数：{runs}（取其中操作数最大的一次）")
    print("=" * 68)

    print("\n第一节　上界（编译器算出的 κ，单位：操作数）")
    print("-" * 68)
    print(f"{'函数':<24}{'输入规模':>10}{'上界':>18}{'承诺上限(target)':>20}")
    for r in rows:
        _t = (f"{r['target']:,.0f}" if r.get("target") is not None else "—")
        print(f"{r['function']:<24}{r['size']:>10}{r['bound']:>18,.0f}{_t:>20}")

    # **现场补最坏搜索**：报告要么读 `worstcase.json`，要么自己搜——
    # 否则"相对最坏"那列永远是"（未搜索）"，等于把最有用的一列空着。
    _live = {}
    try:
        from worstcase import worst_for as _worst_for
        for _r in rows:
            _k = (_r["function"], _r["size"])
            if _k in worst:
                continue
            _w = _worst_for(prog, _r["size"], 7)
            if _w:
                worst[_k] = _w
                _live[_k] = True
    except Exception:                                  # noqa: BLE001
        _live = {}

    print("\n第二节　超额倍数（上界 ÷ 承诺上限；大于 1 = 常数意义上超了）")
    print("　（注意：阶的判定由 `elench cost` 负责；这里只看同阶下的常数倍）")
    print("-" * 68)
    print(f"{'函数':<18}{'规模':>8}{'上界':>14}{'承诺上限':>16}{'超额':>12}"
          f"{'典型':>12}{'最坏':>12}")
    for r in rows:
        w = worst.get((r["function"], r["size"]))
        wv = f"{w['ops_worst_obs']:,.0f}" if w else "—"
        tgt = r.get("target")
        if tgt:
            over = (r["bound"] / tgt) if tgt else float("inf")
            ostr = f"{over:,.1f}×"
        else:
            ostr = "—"
        tstr = f"{tgt:,.0f}" if tgt else "—"
        print(f"{r['function']:<18}{r['size']:>8}{r['bound']:>14,.0f}"
              f"{tstr:>16}{ostr:>12}{r['typical']:>12,.0f}{wv:>12}")

    print("\n第三节　紧度（上界 ÷ 实测，越接近 1 越紧）")
    print("-" * 68)
    print(f"{'函数':<24}{'输入规模':>10}{'相对典型':>12}{'相对最坏':>12}{'最坏输入配方':>22}")
    for r in rows:
        w = worst.get((r["function"], r["size"]))
        slack_typical = r["bound"] / r["typical"] if r["typical"] else float("inf")
        if w and w.get("ops_worst_obs"):
            tight_worst = r["bound"] / float(w["ops_worst_obs"])
            tstr = f"{tight_worst:.2f}"
            recipe = str(w.get("recipe", ""))[:20]
        else:
            tstr, recipe = "（未搜索）", "—"
        print(f"{r['function']:<24}{r['size']:>10}{slack_typical:>12.2f}{tstr:>12}{recipe:>22}")

    # **渐近紧度**：首尾两点的斜率比（κ 斜率 ÷ 最坏实测斜率）。
    # 点紧度在小 n 会被常数项主导，只有斜率比说明"是否同阶"。
    try:
        _by2 = {}
        for _r in rows:
            _by2.setdefault(_r["function"], []).append(_r)
        for _fn2, _rs2 in _by2.items():
            _rs2 = sorted(_rs2, key=lambda z: z["size"])
            if len(_rs2) < 2:
                continue
            _a0, _a1 = _rs2[0]["size"], _rs2[-1]["size"]
            _dk2 = (_rs2[-1]["bound"] - _rs2[0]["bound"]) / (_a1 - _a0)
            _w0 = worst.get((_rs2[0]["function"], _rs2[0]["size"]))
            _w1 = worst.get((_rs2[-1]["function"], _rs2[-1]["size"]))
            if _w0 and _w1:
                _do2 = ((_w1["ops_worst_obs"] - _w0["ops_worst_obs"])
                        / (_a1 - _a0))
                if _do2 > 0:
                    print(f"· 渐近紧度（斜率比）：{_dk2 / _do2:.2f}"
                          f"　（κ 斜率 {_dk2:.2f}/元素 ÷ 最坏实测 {_do2:.2f}/元素）")
    except Exception:                                  # noqa: BLE001
        pass

    # n0：常数项不再主导的起点（κ = a + b·n，取 b·n ≥ 10a）
    try:
        for _fn0, _rs0 in _by2.items():
            _rs0 = sorted(_rs0, key=lambda z: z["size"])
            if len(_rs0) < 2:
                continue
            _b0 = ((_rs0[-1]["bound"] - _rs0[-2]["bound"])
                   / (_rs0[-1]["size"] - _rs0[-2]["size"]))
            _a0 = _rs0[-1]["bound"] - _b0 * _rs0[-1]["size"]
            if _b0 > 0 and _a0 > 0:
                print(f"· 常数项 n0（常数 ≤ κ 的 10%）：n ≥ {10 * _a0 / _b0:.0f}"
                      f"　（κ ≈ {_a0:.0f} + {_b0:.2f}·n）")
    except Exception:                                  # noqa: BLE001
        pass

    print("\n判读")
    print("-" * 68)
    print("· 「相对典型」大而「相对最坏」小：界其实很紧，只是日常输入便宜。")
    print("· 「相对最坏」随规模上升：才说明上界的阶比真实成本高（危险）。")
    print("· 最坏输入是**搜索到的下界**，真实紧度不会比这更松，可能更紧。")

    # ---------- 图：默认**一个文件装多张图**（多面板，不是一张图里挤两条语义） ----------
    try:
        from chart import line_chart, stack_svgs
        _base = os.path.splitext(os.path.basename(module_arg.rstrip("/\\")))[0]
        _out_base = (os.path.splitext(out_path)[0] if out_path
                     else os.path.join(os.getcwd(), _base))
        _by_fn = {}
        for _r in rows:
            _by_fn.setdefault(_r["function"], []).append(_r)

        for _fn, _rs in _by_fn.items():
            _rs = sorted(_rs, key=lambda z: z["size"])
            _xs = [z["size"] for z in _rs]
            _bound = [(_xs[i], _rs[i]["bound"]) for i in range(len(_rs))]
            _typ = [(_xs[i], _rs[i]["typical"]) for i in range(len(_rs))]
            _worst = []
            for i in range(len(_rs)):
                _w = worst.get((_rs[i]["function"], _rs[i]["size"]))
                if _w:
                    _worst.append((_xs[i], _w["ops_worst_obs"]))

            _tgt = [(z["size"], z["target"]) for z in _rs
                    if z.get("target") is not None]
            _svg1 = line_chart(
                [("承诺（候选取最大）", _bound)] + ([("承诺上限 target", _tgt)] if _tgt else [])
                + [("典型实测", _typ), ("最坏实测", _worst)],
                f"{_fn}：上界与实测", "操作数", log_y=True)

            _st = [(_xs[i], _rs[i]["bound"] / _rs[i]["typical"])
                   for i in range(len(_rs))]
            _sw = []
            for i in range(len(_rs)):
                _w = worst.get((_rs[i]["function"], _rs[i]["size"]))
                if _w and _w.get("ops_worst_obs"):
                    _sw.append((_xs[i], _rs[i]["bound"] / float(_w["ops_worst_obs"])))
            _svg2 = line_chart(
                [("相对典型", _st)] + ([("相对最坏", _sw)] if _sw else []),
                f"{_fn}：紧度（越接近 1 越紧）", "上界 ÷ 实测",
                log_y=True, hline=1)

            _dir = os.path.dirname(_out_base) or "."
            _nm = os.path.basename(_out_base)
            if split:
                _f1 = os.path.join(_dir, f"{_nm}-上界与实测.svg")
                _f2 = os.path.join(_dir, f"{_nm}-紧度.svg")
                io.open(_f1, "w", encoding="utf-8", newline="\n").write(_svg1)
                io.open(_f2, "w", encoding="utf-8", newline="\n").write(_svg2)
                print(f"已写出图：{_f1}")
                print(f"已写出图：{_f2}")
            else:
                # 一个文件、三张面板（各自独立坐标系，不共享 y 轴）
                _v3 = []
                for _i in range(len(_rs)):
                    _w3 = worst.get((_rs[_i]["function"], _rs[_i]["size"]))
                    if _w3 and _w3.get("ops_worst_obs"):
                        _v3.append((_rs[_i]["size"],
                                    _rs[_i]["bound"] / float(_w3["ops_worst_obs"])))
                # n0：常数项占 κ 的 10% 以下的起点（用末两点估斜率与截距）
                _n0 = None
                if len(_rs) >= 2:
                    _b = ((_rs[-1]["bound"] - _rs[-2]["bound"])
                          / (_rs[-1]["size"] - _rs[-2]["size"]))
                    _a = _rs[-1]["bound"] - _b * _rs[-1]["size"]
                    if _b > 0 and _a > 0:
                        _n0 = 10 * _a / _b
                _svg3 = line_chart(
                    [("该点紧度（相对最坏）", _v3)],
                    f"{_fn}：各点紧度（数值标注）", "上界 ÷ 最坏实测",
                    log_y=True, hline=1, annotate=True, vline=_n0)
                _f3 = (out_path if out_path and out_path.lower().endswith(".svg")
                       else os.path.join(_dir, f"{_nm}-图.svg"))
                io.open(_f3, "w", encoding="utf-8", newline="\n").write(
                    stack_svgs([_svg1, _svg2, _svg3]))
                print(f"已写出图：{_f3}（一个文件，三张面板）")
    except Exception as _exc:                          # noqa: BLE001
        print(f"（画图失败：{type(_exc).__name__}: {_exc}）", file=sys.stderr)
    return 0


def cmd_tightness(argv: List[str]) -> int:
    """**紧度曲线**：κ(n) ÷ ops_typ(n)，对多个规模出表。

    ## 为什么单独一个动词、不挂到 `export`

    `export` 是纯静态的（不执行用户代码）；紧度要**把程序跑起来**才有 ops。
    把测量塞进 export 等于让"导出源码"顺带执行用户代码——副作用、非终止都会
    在导出时发生。跑代码必须**显式**。

    ## 指标名字必须准确

    随机输入测的是**典型成本**，所以这里出的是 `slack_typ = κ ÷ ops_typ`，
    **不是** `tightness_wc = κ ÷ ops_worst`。因为 `ops_worst ≥ ops_typ`，
    本表的数值是真实紧度的**上界**（偏乐观）。
    """
    argv = _expand_module_args(argv)
    if not argv:
        print("用法：elench tightness <模块> [--sizes 16,64,256] [--runs 20] "
              "[--out 文件.md|.json|.csv]", file=sys.stderr)
        return 2

    sizes = [16, 64, 256]
    runs = 20
    out = None
    fmt = None
    wc_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "tools", "worstcase.json")
    pos = []
    for a in argv:
        if a.startswith("--sizes="):
            sizes = [int(x) for x in a.split("=", 1)[1].split(",") if x.strip()]
        elif a.startswith("--runs="):
            runs = int(a.split("=", 1)[1])
        elif a.startswith("--out="):
            out = a.split("=", 1)[1]
        elif a.startswith("--format="):
            fmt = a.split("=", 1)[1]
        elif a.startswith("--worstcase="):
            wc_path = a.split("=", 1)[1]
        elif not a.startswith("--"):
            pos.append(a)
    if not pos:
        print("✗ 缺少模块名", file=sys.stderr)
        return 2

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tools = os.path.join(root, "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    try:
        from kappa_vs_measured import measure_one        # noqa: E402
    except Exception as exc:                             # noqa: BLE001
        print(f"✗ 无法加载测量器：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    prog = os.path.basename(pos[0]).rsplit(".", 1)[0]
    rows = []
    for n in sizes:
        for r in measure_one(prog, n, runs):
            if r.get("error") or "max_actual" not in r:
                continue
            ops = float(r["max_actual"] or 0)
            kv = float(r.get("kappa_at_n") or 0)
            if ops <= 0:
                continue
            rows.append({
                "program": r.get("program", prog),
                "function": r.get("function", "?"),
                "n": n,
                "kappa": kv,
                "ops_typ": ops,
                "slack_typ": kv / ops,
                "holds": bool(r.get("holds")),
            })

    if not rows:
        print("✗ 没有可对照的函数（需要带 target 的 spec）", file=sys.stderr)
        return 1

    # **真紧度的分母**：`tools/worstcase.py` 搜到的最坏观测值（下界逼近）
    wc = {}
    try:
        if wc_path and os.path.exists(wc_path):
            wc = json.load(io.open(wc_path, encoding="utf-8"))
    except Exception:                                    # noqa: BLE001
        wc = {}
    for r in rows:
        r["ops_worst_obs"] = None
        r["slack_wc_ub"] = None
        for w in wc.get(r["program"], []):
            if w.get("function") == r["function"] and w.get("n") == r["n"]:
                ops_w = float(w.get("ops_worst_obs") or 0)
                if ops_w > 0:
                    r["ops_worst_obs"] = ops_w
                    r["slack_wc_ub"] = r["kappa"] / ops_w
                break

    lines = ["# 紧度曲线（slack_typ = κ ÷ ops_typ）", "",
             f"> 规模 {sizes}，每点 {runs} 组输入取**最坏**（注意：仍不是对抗性输入）。",
             "> 随机输入测的是典型成本，故 `slack_typ` 是真实紧度 `tightness_wc` 的**上界**（偏乐观）。",
             "> `slack_wc_ub` 用 `tools/worstcase.py` 搜到的最坏观测值当分母——它仍是**下界逼近**，",
             "> 不是精确值；未收敛的条目要在 `worstcase.json` 里看 `converged`。",
             ""]
    lines.append("| 函数 | n | κ | ops_typ | slack_typ | ops_worst | slack_wc_ub | 界成立 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        _w = f"{r['ops_worst_obs']:,.0f}" if r["ops_worst_obs"] else "—"
        _wu = f"{r['slack_wc_ub']:.2f}" if r["slack_wc_ub"] else "—"
        lines.append(f"| {r['program']}.{r['function']} | {r['n']} | "
                     f"{r['kappa']:,.0f} | {r['ops_typ']:,.0f} | "
                     f"{r['slack_typ']:.2f} | {_w} | {_wu} | "
                     f"{'✓' if r['holds'] else '✗'} |")
    md = "\n".join(lines) + "\n"

    # 形状分类：按函数看 slack 随 n 的变化
    by_fn: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_fn.setdefault(f"{r['program']}.{r['function']}", []).append(r)
    cls = []
    for name, rs in by_fn.items():
        rs = sorted(rs, key=lambda x: x["n"])
        if len(rs) < 2:
            continue
        # **判形状要用真紧度**（有 worstcase 就用它）。
        # 用 `slack_typ` 会误报：早退函数的典型 ops 不随 n 涨，
        # 于是 `slack_typ` 一路上升，看起来像"主导项算松了"，其实紧度很稳。
        _key = ("slack_wc_ub" if all(r.get("slack_wc_ub") for r in rs)
                else "slack_typ")
        ratio = rs[-1][_key] / rs[0][_key] if rs[0][_key] else 0
        if ratio > 1.15:
            verdict = "随 n 上升 → **主导项可能算松了**（危险）"
        elif ratio < 0.87:
            verdict = "随 n 下降 → 常数项主导（安全）"
        else:
            verdict = "基本平 → 形状对得上"
        cls.append((name, rs[0][_key], rs[-1][_key], ratio, verdict))
    if cls:
        lines2 = ["", "## 形状分类（前 20% = slack 最大者；优先用 slack_wc_ub）", "",
                  "| 函数 | slack(n_min) | slack(n_max) | 比值 | 判读 |",
                  "|---|---|---|---|---|"]
        for name, a, b, ratio, verdict in sorted(cls, key=lambda x: -x[2]):
            lines2.append(f"| {name} | {a:.2f} | {b:.2f} | {ratio:.2f} | {verdict} |")
        md += "\n".join(lines2) + "\n"

    print(md)

    from emit import emit
    emit(md, rows, out=out, fmt=fmt, cols=["program", "function", "n", "kappa", "ops_typ", "slack_typ",
                                        "ops_worst_obs", "slack_wc_ub", "holds"],
         meta={"sizes": sizes, "runs": runs, "metric": "slack_typ"})
    return 0
    return 0


def main(argv=None) -> int:
    """控制台入口（友好错误层）。

    **为什么要这一层**：命令行工具崩出 Python traceback 是最不体面的失败方式——
    用户只想知道"哪个文件不存在"，不想看调用栈。
    """
    argv = list(sys.argv if argv is None else argv)
    if len(argv) > 1 and argv[1] in ("--version", "-V", "version"):
        print(f"elench {VERSION}")
        return 0

    try:
        _rc = _main_impl(argv)
        _otel_flush()
        return _rc
    except PermissionError as exc:
        print(f"✗ 无法读取：{exc.filename}")
        print("  可能原因：路径含空格/括号但**没有加引号**，或被别的程序占用。")
        print('  正确写法（PowerShell / bash 都是）：elench run "C:\\path with space\\hw.tu"')
        return 1
    except IsADirectoryError as exc:
        print(f"✗ 这是个目录，不是文件：{exc.filename}")
        return 1
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
        # `--out`：**JSON 走 `--signature`**（成本签名本来就是机器可读的），
        # 这样 csv/json 都是真数据，不是把终端文本存成文件。
        _outs = [a for a in rest if a.startswith("--out=")]
        if not _outs:
            return cmd_cost(rest)
        _out = _outs[0].split("=", 1)[1]
        _rest = [a for a in rest if not a.startswith("--out=")]
        import contextlib
        _buf = io.StringIO()
        with contextlib.redirect_stdout(_buf):
            _rc = cmd_cost(_rest + ["--json"])
        _txt = _buf.getvalue()
        _low = _out.lower()
        if _low.endswith(".json"):
            io.open(_out, "w", encoding="utf-8", newline="\n").write(_txt)
            print(f"已写出：{_out}")
            return _rc
        try:
            _data = json.loads(_txt[_txt.find("{"):])
        except Exception:                                # noqa: BLE001
            from emit import emit
            emit(_txt, [], out=_out)
            return _rc
        _rows = []
        for _fn, _v in (_data.get("functions") or {}).items():
            _rows.append({"module": _data.get("module", ""), "function": _fn,
                          "verdict": _v.get("verdict", "?"),
                          "reason": (_v.get("reason") or "").replace("\n", " ")})
        from emit import emit
        emit(_txt, _rows, out=_out, fmt=("csv" if _low.endswith(".csv") else None),
             cols=["module", "function", "verdict", "reason"],
             meta={"module": _data.get("module", "")})
        return _rc
    if cmd == "dispatch":
        return cmd_dispatch(rest)
    if cmd == "measure":
        return cmd_measure(rest)
    if cmd == "tightness":
        return cmd_tightness(rest)
    if cmd == "stats":
        return cmd_stats(rest)
    if cmd == "header":
        return cmd_header(rest)
    if cmd == "grammar":
        return cmd_grammar(rest)
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
