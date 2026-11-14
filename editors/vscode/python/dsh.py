#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""
dsh —— AI 语言工具链（M0）

子命令：
  dsh check <模块.tu> [<模块.eln>] [--strict] [--json] [--exhaustive]
  dsh show  <模块.tu> <label>         打印某个 choose 的候选与状态
  dsh parse <文件>                    打印 AST（调试用）

M0 范围：**没有成本分析**，故 `state` 只能给出 draft / planned / unchecked，
        `done` / `debt` 需要 M2 的成本计算。

用法：
  cd src
  $env:PYTHONIOENCODING="utf-8"
  python dsh.py check ../文档/样例/calculator.tu
"""

from __future__ import annotations

from typenames import canon, display
import io
import json
import os
import re
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
                                  --exhaustive 有限输入域穷举 guarantees
                                  （--max-combos=N / --exhaustive-timeout=S）
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
                                  --explain：超界时给出**归因**（支配项 / 元凶行号 / 最小修复）
                                  --out=x.cost.yaml：写「结果 + 出处」（provenance；
                                  结果文件，不入库、不参与任何门禁）
                                  承诺 = **分段上界**：每段用运行期真跑的那个候选的符号式
                                  含 parallel 时打印**最大核视图**：逐任务 κ / 瓶颈 /
                                  判阶(max) / 总量(Σ) / wall_est
                                  --cores=<机器档.json>：大小核权重，**只影响 wall_est**（不进 κ）
                                  --at=N：wall_est 的规模（κ 含尺寸量时必须给）
    stats     <模块> <函数> [--ops] 跑一次：调用数 / 分配数 / 迭代数 / **操作数**
                                  --ops：打开操作数计数并输出 JSON（与 κ 同单位）
    bench     <模块> <函数>       单函数隔离测量（只给判定与比值，不做全量统计）

    export    <模块> [--format=table|otlp|svg|csv] [--out=文件] [--function=名] [--timing]
                                  导出「静态上界 vs 实测」（默认实测=**操作数**，与 κ 同单位）
                                  --timing：实测改成纳秒（不同单位，图会各自归一化）
                                  不给 --out 就打到终端；给 --out 就写文件
                                  csv/svg/otlp 建议配 --out（否则粘不回文件）
                                  --push：按 OTLP/HTTP 推到 collector（配 OTEL_* 环境变量）
    export    <模块> --full [--sizes=16,64,256] [--runs=20] [--out=文件]
                                  完整报告：三节（上界 / 超额倍数 / 紧度）；不给 --out 打终端
                                  --out 按后缀出：.svg 曲线 / .md 表 / .csv 数据 / .otel OTLP
                                  .html = 一页一模块的静态归档报告（自包含、无外链）
                                  --now=时间戳 / --no-timestamp：只影响 .html，便于逐字节复现
                                  --full 与 --timing 互斥（单位不同，会明确拒绝）

  观测（配 export --push）
    otel      <sink|view|dashboard>  本机 OTLP 接收端 / 查看 / 网页看板
                                   elench otel sink --out otel.ndjson
                                   elench otel view otel.ndjson
                                   elench otel dashboard --file otel.ndjson --port 8080

  决策点 ?! 与门禁
    dispatch  <模块> [<choose名>] 三层筛：每个 ?! 要不要测、分几段（只出计划，不生成表）
                                  给 <choose名> 就只看那一个 ?!（不收 --only）
    measure   <模块> [<choose名>] 真跑候选，用实测生成分档表（可提交、可 diff）
                                  给 <choose名> 只重测这一段并**按段合并**回表，
                                  其余 label 的段与注释逐字不变（git diff 只显示这一段）
                                  表必须先存在（不存在就报错，提示先跑一次全量）
    build     <模块> [--out=路径] [--measure] [--deny-debt] [--deny-unknown]
                                  构建门禁：含 pending 就失败；**check 有 error 也失败**
                                  默认只跑静态 dispatch；--measure 才真跑测量（慢）
                                  --deny-debt：超出 target 即拒绝构建（**默认是宽松的**：只记债务、退出 0）
                                  --deny-debt 也拒绝 κ=⊤；--deny-unknown：只拒绝 κ=⊤（无法判定）

  --help    打印本页

  --compat  （放在任意子命令后）**只加拼写、不改语义**：接受 C++ 的十种写法，
            关掉时行为与以前逐字节相同。逐条：
              struct 写在 .eln 里（--strict 仍拒绝）
              using X = Y; / typedef X Y;        → 别名表（不进 AST）
              auto x = expr;                     → 编译期推断（推不出报错）
              T{a, b} / vector<u64> v{1,2,3}     → 现有构造节点
              f(xs.begin(), xs.end())            → f(xs)（只在配平时）
              std::vector / std::sort / std::printf / std::min …
                                                 → 白名单；表外报 E-Compat-UnknownStd
              返回类型 void                       → ()
              函数声明开头的 static               → 跳过 token（局部 static 仍拒绝）
              多声明符 u64 a = 1, b = 2;          → 多条声明（AST 与手写多行同形）
              直接构造 u64 x(5) / vector<T> v(n, x)
                                                 → 单参拷贝初始化 / filled(x, n)；
                                                   没有等价物报 E-Compat-CtorUnsupported
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


def _cost_interp(ch_mod, tu_mod, tu_p: Optional[str] = None,
                 force_max: bool = False, explain: bool = False):
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
    if tu_p and not force_max:
        cand = _tbl_for(tu_p)
        if os.path.exists(cand):
            path = cand
    return CostInterp(ch_mod, tu_mod, table_path=path,
                      diags=getattr(ch_mod, "_parse_diags", None)
                      or getattr(tu_mod, "_parse_diags", None),
                      explain=explain)


def _tree_rec_budget_advisor(ch_mod, tu_mod, tu_p, entry: str):
    """给 `Interp.budget_advisor` 的闭包：超预算时用 **cost 侧的树递归判定**说话。

    **不重写判定**：构造 `CostInterp`，读 `top_reasons`（由
    `cost_domain.cost_interp::_note_tree_rec` 记下 κ=⊤ 的原因）。这里只做
    两件事：把当前调用栈/入口函数各算一次 `cost_of`（让原因被记下），
    再按「最内层优先」找一条原因。

    只在**真的超预算**时被调用一次，所以 `run` 的正常路径不付成本分析的代价；
    任何异常都返回 None——诊断失败不许盖掉原始错误。
    """
    def _advise(active):
        try:
            ci = _cost_interp(ch_mod, tu_mod, tu_p)
            _names = []
            for _nm in list(active or ()) + [entry]:
                if _nm and _nm not in _names:
                    _names.append(_nm)
            for _nm in reversed(_names):
                try:
                    ci.cost_of(_nm)
                except Exception:                      # noqa: BLE001
                    pass
            _reasons = getattr(ci, "top_reasons", None) or {}
            for _nm in reversed(_names):
                _why = _reasons.get(_nm)
                if _why:
                    return (
                        "**这更像是算法本身的复杂度（指数/超多项式），不是死循环。**\n"
                        f"  `cost` 侧的静态判定：{_why}\n"
                        "  修法：消掉重复展开（记忆化 / 换算法 / 剪枝），"
                        "而不是去改循环变量。\n"
                        "  如果只是想把这次跑完：提高 ELENCH_STEP_BUDGET（当前值见上）。")
        except Exception:                              # noqa: BLE001
            return None
        return None
    return _advise


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


#: `--allow-target-edit` 由命令层设置；默认 **False**（不许改 target）。
_allow_target_edit = False


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
        # **要碰 target 的改动必须显式放行**：契约是持久的，实现是一次性的。
        # 门槛不是"技术上能不能改"，是"要不要人再看一眼"。
        if getattr(action, "touches_target", False) and not _allow_target_edit:
            print(f"  ✗ 这条会改到 target：{action.label}", file=sys.stderr)
            print("     契约是持久的、实现是一次性的——请人工编辑，或显式加 --allow-target-edit",
                  file=sys.stderr)
            return 3
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
    print(f"✗ 拒绝算 κ：{len(diags)} 条递归深度问题（C11-b）",
          file=sys.stderr)
    for d in diags:
        print(f"  ✗ {d.code} @{d.line}:{d.col}  {d.message}", file=sys.stderr)
    print("  （κ 只能按一层计，会比真实成本小；在 `.tu` 里补 "
          "`recursion depth <= N;` 后再算）", file=sys.stderr)
    return True



def _merge_dispatch_table(path: str, mod: str, label: str, segs, degraded: bool) -> None:
    """**只替换 `label` 那一段**，其余段、注释、顺序原样保留。

    表是按 label 分块的纯文本（`interp.load_dispatch_table` 逐行正则解析），
    所以按块拼接就够了：`git diff` 里只会出现这一段的变化。
    """
    import re as _re
    lines = io.open(path, encoding="utf-8").read().split("\n") if False else \
        io.open(path, encoding="utf-8").read().split("\n")
    head, blocks = [], []
    for ln in lines:
        m = _re.match(r"^label:\s*(\w+)\s*$", ln)
        if m:
            blocks.append([m.group(1), [ln]])
        elif blocks:
            blocks[-1][1].append(ln)
        else:
            head.append(ln)
    new_block = ["label: %s" % label, "segments:"]
    for a, b, who in segs:
        new_block.append("  - {lo: %s, hi: %s, candidate: %s}" % (a, b, who))
    hit = False
    for blk in blocks:
        if blk[0] == label:
            blk[1] = new_block
            hit = True
    if not hit:
        blocks.append([label, new_block])
    out = [h for h in head if h.strip() and not h.startswith("module:")] + ["module: " + mod]
    for _, blk_lines in blocks:
        out.extend([x for x in blk_lines if x.strip() != ""])
    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")


def _write_dispatch_table(path: str, mod: str, labels: dict, degraded: bool) -> None:
    """写分档表：**一个文件装多个 label**（每个 label 一段 `label:` + `segments:`）。

    以前 `_body` 与 `open(..., "w")` 都在每个 label 的循环体内，第二个 label 直接
    覆盖第一个——多 label 静默丢数据且不报错。现在攒成 `{label: segs}` 一次写全。

    格式**故意不变**：`interp.load_dispatch_table` 是逐行正则解析的，
    它本来就按 `label:` 累积成 `{label: [(lo,hi,候选)]}`，多个 `label:` 段天然可读。
    """
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# 由 `elench measure` 生成；MUST NOT 手改。\n")
        if degraded:
            f.write("# DEGRADED: 未覆盖 benchmodel.scale 声明的全部规模，不可用于承诺\n")
            f.write("degraded: true\n")
        f.write("module: %s\n" % mod)
        for lab in sorted(labels):
            f.write("label: %s\n" % lab)
            f.write("segments:\n")
            for a, b, who in labels[lab]:
                f.write("  - {lo: %s, hi: %s, candidate: %s}\n" % (a, b, who))



def _tbl_for(tu_p: str) -> str:
    """模块对应的分档表路径。

    **不再是硬编码的 `<模块>.dispatch.yaml`**：`[dispatch] source` 决定用哪一份
    （`local` / `repo` / `auto`），见 `policy.table_path`。所有读表的地方都走这里，
    免得"承诺读这份、运行期读那份"。
    """
    from policy import table_path as _tp
    return _tp(os.path.dirname(os.path.abspath(tu_p)),
               os.path.basename(tu_p).rsplit(".", 1)[0])


# ------------------------------------------------ `.eln` 的契约指向（`// @spec`）
#
# **为什么要它**：`.tu` 与 `.eln` 一直靠"同名 sidecar"配对（`count.tu` ↔ `count.eln`）。
# 改名、移动目录、或者一个模块拆成多个 `.eln`，契约就会**悄悄丢掉**——工具照样跑，
# 只是不再判 target（`cost` 里那一行「未声明契约」很容易被当成"这个模块本来就没写"）。
#
# **为什么全在驱动层**：`//` 是注释，词法器本来就忽略它（`src/parser.py` 不需要知道
# 有这条指令），所以 `@spec` 只是"同名约定"的一个可显式覆盖，**不是新语法**。
#
# 规则（文档：`文档/设计/目前设计/决定-目录约定与spec指向.md`）：
#   ① 有 `@spec` → 用该路径（**相对 `.eln` 所在目录**解析）；
#   ② 没有 `@spec` → 回退同名 `.tu`（**完全向后兼容**：17 个既有样例一个字不用改）；
#   ③ 写了但解析不到 → `E-Spec-NotFound`（**不回退**：写了却悄悄忽略 = 假通过）；
#   ④ 多个 `.eln` 可以指向同一个 `.tu`（一个模块拆多文件）；
#   ⑤ 同一个函数不能在两处定义——由既有的 `E-Duplicate-Fn` 管，这里不管。
_SPEC_LINE_RE = re.compile(r"^//\s*@spec\s+(.+?)\s*$")


class SpecNotFound(Exception):
    """`// @spec` 指向的契约文件不存在（诊断码 `E-Spec-NotFound`）。"""

    code = "E-Spec-NotFound"

    def __init__(self, eln_path: str, raw: str, abs_path: str):
        self.eln_path = eln_path          #: 写下这条 @spec 的 `.eln`
        self.raw = raw                    #: `@spec` 后面原样写的路径
        self.abs_path = abs_path          #: 解析出来的绝对路径（消息里必须给）
        super().__init__(self.summary())

    def summary(self) -> str:
        return (f"@spec 指向的契约不存在：{self.raw}"
                f"（在 {os.path.basename(self.eln_path)} 里声明）")

    def lines(self) -> List[str]:
        """人读正文：**必须含解析到的绝对路径**，并给出"怎么修"。"""
        return [self.summary(),
                f"  解析到的绝对路径：{self.abs_path}",
                "  提示：这个文件没有契约就删掉 @spec 行（会回退到同名 .tu）"]


def _spec_directive(eln_path: str):
    """`.eln` **头部注释块**里的 `// @spec <路径>`；没写返回 None。

    "头部"的边界：空行与 `//` 注释都算头部，**遇到第一行真正的代码就停**。
    放在函数体中间的 `@spec` 不认——一份文件里不该有两个说法。
    路径可以用引号包住（`// @spec "specs/my mod.tu"`，路径里有空格时用）。
    """
    try:
        with io.open(eln_path, encoding="utf-8") as fh:
            for raw in fh:
                s = raw.strip().lstrip("\ufeff")
                if not s:
                    continue
                if not s.startswith("//"):
                    break                     # 头部结束：后面是代码
                m = _SPEC_LINE_RE.match(s)
                if m:
                    p = m.group(1).strip()
                    if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'":
                        p = p[1:-1]
                    return p
    except (OSError, UnicodeDecodeError):
        # 非 UTF-8 文件：**不在这里报**——交给正常路径报 `E-File-NotUtf8`。
        # 这里只是"扫一下有没有 @spec"，扫不了就当没写（绝不让 Python 异常冒给用户）。
        return None
    return None


def _spec_path(eln_path: str):
    """解析 `@spec`：没写 → None；写了且存在 → 绝对路径；写了但不存在 → 抛。

    **路径相对 `.eln` 所在目录**（不是当前工作目录）：`@spec` 说的是"这个文件
    的契约在哪"，它必须跟着文件走，否则换个目录运行就指向别处。
    """
    raw = _spec_directive(eln_path)
    if raw is None:
        return None
    base = os.path.dirname(os.path.abspath(eln_path))
    p = os.path.normpath(raw if os.path.isabs(raw)
                         else os.path.join(base, raw))
    if not os.path.isfile(p):
        raise SpecNotFound(eln_path, raw, p)
    return p


def _pair_from_eln(eln_path: str):
    """从 `.eln` 一侧凑出 `[.tu, .eln]`：**`@spec` 优先，同名回退**。

    返回 None = "既没有 `@spec`、也没有同名 `.tu`"（这是一份**没有契约**的实现）。
    **写完不存在的 `@spec` 在这里就抛**，不会静默回退到同名文件。
    """
    spec = _spec_path(eln_path)
    if spec:
        return [spec, eln_path]
    same = os.path.splitext(eln_path)[0] + ".tu"
    if os.path.exists(same):
        return [same, eln_path]
    return None


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
        if ext == ".eln":
            # **`@spec` 优先、同名回退**（顺序写死在 `_pair_from_eln` 里，只此一处）
            pair = _pair_from_eln(head)
            return (pair + rest) if pair else args
        if os.path.exists(partner) and not rest[:1]:
            return [head, partner] + rest
        return args

    if os.sep in head or "/" in head:
        return args                      # 带路径的裸名字：不猜

    tu, eln = head + ".tu", head + ".eln"
    if os.path.exists(eln):
        # 同名 `.eln` 在手：让它自己说契约在哪（`@spec` / 同名 `.tu`）
        pair = _pair_from_eln(eln)
        if pair:
            return pair + rest
    if os.path.exists(tu):
        return ([tu, eln] if os.path.exists(eln) else [tu]) + rest
    return args


#: **R1–R5 放宽开关** → 被降级为 warning 的诊断码。
#:
#: 这些是"设计-自由与严谨"里那 5 条限制的**单因素放开**，用来量
#: "放开一条限制会多出多少假阳性"。**默认全关**（一条都不放宽）。
#:
#: | 开关 | 放开的限制 | 被降级的诊断 |
#: |---|---|---|
#: | `R1` | while 圈数必须可推 | `E-While-NoBound` |
#: | `R2` | 递归必须声明 depth | `E-Recursion-Unbounded` |
#: | `R3` | 必须声明 benchmodel/scale | `E-Bench-NoModel`、`E-Bench-MissingScale` |
#: | `R4` | 无隐式转换/重载 | **没有对应诊断**（v0 由类型系统本身保证，无开关） |
#: | `R5` | 分配必须建模 | `alloc` 维度的 unknown（在门禁层处理） |
RELAX_CODES = {
    "R1": {"E-While-NoBound"},
    "R2": {"E-Recursion-Unbounded"},
    "R3": {"E-Bench-NoModel", "E-Bench-MissingScale"},
    # R4（隐式转换/重载）在 v0 里不是一条独立诊断——类型系统本身保证，无开关；
    # 保留空集只为让 `--relax=R4` 被"认得但无害"。
    "R4": set(),
    # R5（分配建模）不降级任何诊断，而是在**门禁层**跳过 allocation 维度。
    "R5": set(),
}


def _relax_names(relax) -> set:
    """`--relax=R1,R5` → {"R1","R5"}（原样保留，供门禁层判断）。"""
    out: set = set()
    for item in (relax or []):
        for part in str(item).split(","):
            _p = part.strip().upper()
            if _p:
                out.add(_p)
    return out


def _relax_codes(relax) -> set:
    """把 `--relax=R1,R3` 解析成"要降级的诊断码集合"。"""
    out: set = set()
    for item in (relax or []):
        for part in str(item).split(","):
            out |= RELAX_CODES.get(part.strip().upper(), set())
    return out


def _diagnose_all(tu, ai, strict: bool = False, overflow: str = "off",
                  relax=None):
    """**唯一的检查入口**：`check` / `run` / `build` 都走这里。

    以前 `check` 与 `build` 各写一套：`build` 只调 `check_module`、**不跑类型检查**，
    于是 `func f() -> u64 { return true; }` 能"✓ 构建通过"。
    两套门禁必然漂移——只留一套。

    **`.tu` 可选**（2026-11-10，规格 §8.9）：没有 `.tu` 时把 `tu` 置空再交给
    `check_files(None, …)`——不解析第二遍、不重复诊断，`E-Fn-NoSpec` 只汇总一条。
    """
    from checker import Diag, check_files
    _bare = _no_tu(tu, ai)
    res = check_files(None if _bare else tu, ai, strict=strict, overflow=overflow)
    # **放宽**：把指定诊断的 severity 从 error 降成 warning。
    # 注意这里只改**severity**，不改消息——读者仍能看见"这条限制被放开了"。
    _rc = _relax_codes(relax)
    if _rc:
        for _d in res.diagnostics:
            if _d.code in _rc and _d.severity == "error":
                _d.severity = "warning"
                _d.message = _d.message + "　（已按 --relax 放宽为警告）"
    if not any(d.severity == "error" for d in res.diagnostics):
        try:
            from parser import parse_source as _ps
            from typecheck import typecheck
            _tu = _ps("" if _bare else io_read(tu),
                      _module_name_of(tu, ai) + ".tu").module
            # **`.eln` 没显式传也要找**（E16）：以前这里 `ai is None` 就给 `_ch = None`，
            # 于是**实参类型检查从来没跑过**——`g("abc")` 传进 `u64` 形参一声不吭。
            _ai_path = ai
            if _ai_path is None and not _bare:
                _cand = os.path.splitext(tu)[0] + ".eln"
                if os.path.exists(_cand):
                    _ai_path = _cand
            _ch = (_ps(io_read(_ai_path), os.path.basename(_ai_path)).module
                   if _ai_path else None)
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
    # **宏展开来源**（2026-11-10）：预处理把宏展开成记号后行号仍是原文件行号，
    # 但读者只看到一行代码、不知道它是谁生成的。落在展开行上的诊断补一句
    # 「由宏 `N`（定义在 3:9）展开」——错误很可能就出在那个宏体里。
    from preproc import annotate as _pp_annotate
    _pp_annotate(res.diagnostics)
    return res


def _target_loose_diags(tu: str, ai: Optional[str]) -> List[Any]:
    """给 `check` 补 `W-Target-Loose`（**warning，永远不改变退出码**）。

    为什么这条落在 `dsh` 而不是 `checker.py`：判据要 κ，而 `checker` 不做成本推导；
    反过来 `cost_explain.target_loose_lines` 已经是**唯一**的判据，这里只转发它的结论
    ——**不重写一遍**（两份判据必然漂移）。

    `CheckResult.ok` 只数 `severity == "error"`，所以 `severity="warning"` 的这条
    **不会让 `check` 变红**；`--format=json` 里也一样（`ok` 不变）。

    整个函数对异常沉默（`[]`）：这条是**附加信息**，不许因为它让 `check` 崩。
    """
    # **没有 `.tu` 就没有契约**（规格 §8.9）：没有 target 可判，这个告警无从谈起。
    if _no_tu(tu, ai):
        return []
    try:
        from checker import Diag
        from parser import parse_source as _ps
    except Exception:                                  # noqa: BLE001
        return []
    try:
        _ai_p = ai
        if _ai_p is None:
            _cand = os.path.splitext(tu)[0] + ".eln"
            if os.path.exists(_cand):
                _ai_p = _cand
        if not _ai_p or not os.path.exists(_ai_p):
            return []
        _tu = _ps(io_read(tu), os.path.basename(tu)).module
        _ch = _ps(io_read(_ai_p), os.path.basename(_ai_p)).module
        # **与 `cost` 同一个构造**（分档表优先、缺表退回 max），否则同一个模块
        # `check` 和 `cost` 会给出两条不同的 κ。
        _ci = _cost_interp(_ch, _tu, tu)
        import cost_explain as _ce
        out = []
        for _n in _ci.fns:
            try:
                _c = _ci.cost_of(_n)
                _t = _ci.target_cost(_n)
            except Exception:                          # noqa: BLE001
                continue
            if _t is None:
                continue
            _sv = (_c.variables() or ["n"])[0]
            _rep = _ce.target_loose_report(_ci, _n, _t, _c, _sv)
            if _rep is not None:
                out.append(Diag("W-Target-Loose",
                                f"{_rep['headline']} {_rep['detail']}", 0, 0,
                                severity="warning"))
        return out
    except Exception:                                  # noqa: BLE001
        return []


def _run_exhaustive_check(tu: Optional[str], ai: Optional[str],
                          max_combos: int, timeout: float):
    """`check --exhaustive`：对**有限域**函数穷举求值 `guarantees`。

    返回 `(报告列表, 诊断列表)`；`报告列表` 为 None 表示没有实现文件可跑。
    **复用 `Interp.probe`**（不新写解释器）：`probe` 与 `run` 走同一套求值，
    只是把 guarantees 失败收集起来而不是抛异常（这样才能把返回值一起拿出来
    当反例）。
    """
    from checker import Diag
    from parser import parse_source
    from interp import Interp, exhaustive_module

    if not ai or not os.path.exists(ai):
        # 与 `_diagnose_all` 同一条猜测规则：只给 `.tu` 时，同名 `.eln` 就是实现。
        if tu and os.path.exists(os.path.splitext(tu)[0] + ".eln"):
            ai = os.path.splitext(tu)[0] + ".eln"
    if not ai or not os.path.exists(ai):
        return None, []
    try:
        _tu_mod = None
        if tu and not _no_tu(tu, ai):
            _tu_mod = parse_source(io_read(tu), os.path.basename(tu)).module
        _ai_mod = parse_source(io_read(ai), os.path.basename(ai)).module
        _it = Interp(_ai_mod, _tu_mod,
                     table_path=(_tbl_for(tu) if tu else None))
        reports, cexs = exhaustive_module(_it, max_combos=max_combos,
                                          timeout=timeout)
    except Exception as exc:                            # noqa: BLE001
        # 穷举器自己崩了 = **没证**，不是"证完了"。如实报【未证】。
        return ([{"fn": "", "status": "unproven",
                  "detail": f"穷举没能开始：{type(exc).__name__}: {exc}",
                  "domain": "", "combos": 0, "checked": 0,
                  "requires_skipped": 0, "counterexample": None}], [])
    diags = []
    for c in cexs:
        diags.append(Diag(c["code"], c["message"], c.get("line", 0),
                          c.get("col", 0), severity="error"))
    return reports, diags


def _print_exhaustive(reports) -> None:
    """分档打印穷举结论（**三档必须是三句话，不许合并成"通过"**）。"""
    PREFIX = {
        "proved": "【证明】",
        "counterexample": "【反例】",
        "runtime": "【符合】",
        "none": "【声明】",
        "too_large": "【未证】",
        "timeout": "【未证】",
        "unproven": "【未证】",
        "uncheckable": "【未证】",
    }
    print()
    print("穷举 guarantees（--exhaustive）：")
    if not reports:
        print("  【未证】没有可穷举的实现文件")
        return
    for r in reports:
        print(f"  {PREFIX.get(r.get('status'), '【未证】')} "
              f"{r.get('fn') or '?'}：{r.get('detail', '')}")


def cmd_check(argv: List[str]) -> int:
    argv = _expand_module_args(argv)
    if not argv:
        print("用法：elench check <模块.tu> [<模块.eln>]", file=sys.stderr)
        return 2

    as_json = ("--format=json" in argv) or ("--json" in argv)   # --json 为别名
    strict = "--strict" in argv
    # **有限域穷举**（2026-11-11）：默认关。语言里"贵的检查"一律显式开
    # （`--require-measured` / `--strict-coverage` 同一习惯），因为 65536 组合
    # 会真的跑一遍实现。
    exhaustive = "--exhaustive" in argv
    max_combos = None
    exhaustive_timeout = None
    for a in argv:
        if a.startswith("--max-combos="):
            try:
                max_combos = int(a.split("=", 1)[1].replace(",", ""))
            except ValueError:
                print(f"✗ --max-combos 需要整数：{a}", file=sys.stderr)
                return 2
            if max_combos < 1:
                print("✗ --max-combos 必须 ≥ 1", file=sys.stderr)
                return 2
        elif a.startswith("--exhaustive-timeout="):
            try:
                exhaustive_timeout = float(a.split("=", 1)[1])
            except ValueError:
                print(f"✗ --exhaustive-timeout 需要秒数：{a}", file=sys.stderr)
                return 2
            if exhaustive_timeout <= 0:
                print("✗ --exhaustive-timeout 必须 > 0", file=sys.stderr)
                return 2
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

    # **`@spec` 先把配对摆正**（2026-11-11）：第一个位置给的是 `.eln` 时，
    # 契约按**它自己声明的路径**找（`@spec` → 同名回退）；裸模块名也走同一条路。
    # 显式给了两个文件（`check a.tu b.eln`）就不猜——按给的来。
    if not ai and tu:
        _ext0 = os.path.splitext(tu)[1].lower()
        if _ext0 == ".eln":
            _pair = _pair_from_eln(tu)
            if _pair:
                tu, ai = _pair[0], _pair[1]
        elif _ext0 == ".tu":
            _eln = os.path.splitext(tu)[0] + ".eln"
            if os.path.exists(_eln):
                _pair = _pair_from_eln(_eln)
                if _pair:
                    tu, ai = _pair[0], _pair[1]

    # **`.tu` 可选**（2026-11-10，规格 §8.9）：第一个位置直接给了 `.eln`
    # 就把它认成实现文件（`tu=None`），不要再把同一份源当 `.tu` 解析一遍——
    # 那样诊断会带 `[.tu]` 前缀重复出现，而且 `E-Fn-NoSpec` 会连 `run` 都挡住。
    # 位置反着写（`check a.eln a.tu`）也认，按扩展名摆正顺序。
    if tu and os.path.splitext(tu)[1].lower() == ".eln":
        if ai and os.path.splitext(ai)[1].lower() == ".tu":
            tu, ai = ai, tu
        else:
            ai = ai or tu
            tu = None

    if tu is None and ai is None:
        print(f"✗ 找不到 {paths[0]}（试过：原样、+.tu、+.eln、仓库样例目录）", file=sys.stderr)
        return 2

    _relax = [a.split("=", 1)[1] for a in argv if a.startswith("--relax=")]
    # `--apply=N`：**把第 N 条修法真的执行**（`edit` 只打印 diff，`command` 才跑）。
    _apply = None
    for a in argv:
        if a.startswith("--apply="):
            try:
                _apply = int(a.split("=", 1)[1])
            except ValueError:
                print(f"✗ --apply 需要整数：{a}", file=sys.stderr)
                return 2
    res = _diagnose_all(tu, ai, strict=strict, overflow=overflow, relax=_relax)
    # **W-Target-Loose**（2026-11-10）：`check` 也提示「这条 target 是被常数项满足的」。
    # **warning**，`CheckResult.ok` 只数 error ⇒ 加它前后 `check` 的绿/红**完全一致**
    # （见 `_target_loose_diags`）。放在诊断组装之后、任何输出之前，人读与 JSON 都看得到。
    res.diagnostics.extend(_target_loose_diags(tu, ai))

    # **`--exhaustive`：有限输入域上的真证明**（2026-11-11）。默认关；
    # 开了才枚举。反例进 `res.diagnostics`（error ⇒ 退出码 1），分档结论
    # 挂到 `res.exhaustive`（人读输出与 `--format=json` 各取所需）。
    _exh_reports = None
    if exhaustive:
        from interp import EXHAUSTIVE_MAX_COMBOS, EXHAUSTIVE_TIMEOUT_S
        _exh_reports, _exh_diags = _run_exhaustive_check(
            tu, ai,
            max_combos if max_combos is not None else EXHAUSTIVE_MAX_COMBOS,
            (exhaustive_timeout if exhaustive_timeout is not None
             else EXHAUSTIVE_TIMEOUT_S))
        res.diagnostics.extend(_exh_diags)
        res.exhaustive = _exh_reports

    if as_json:
        # **结构化诊断（P6-2，schema version 1）**：stdout 只有 JSON，
        # `fix` 是可直接应用的替换（range 半开、1-based）。人类输出不走这里，
        # 所以不加 `--json` 时逐字节不变。判据与名字来源见 `diag_json.py`。
        from diag_json import payload as _json_payload
        print(json.dumps(_json_payload(res, tu, ai), ensure_ascii=False,
                         indent=2))
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

    # **修法清单**：静态检查以前只给散文建议（"请在 spec 里加 repeats"），
    # 读者还得自己翻译成补丁。带 `actions` 的诊断在这里编号列出，
    # `--apply=N` 直接执行——这就是"错误必须局部可修"的落地。
    _acts = [(d, a) for d in res.diagnostics for a in (d.actions or [])]
    if _acts:
        print(f"修法（{len(_acts)} 条，`--apply=N` 取一条）：")
        for i, (d, a) in enumerate(_acts, 1):
            kind = {"command": "可执行", "edit": "改文件", "info": "仅说明"}.get(a.kind, a.kind)
            print(f"  {i}) [{d.code}] {a.label}    （{kind}）")
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

    if _exh_reports is not None:
        _print_exhaustive(_exh_reports)

    if _apply is not None:
        global _allow_target_edit
        _allow_target_edit = "--allow-target-edit" in argv
        if not (1 <= _apply <= len(_acts)):
            print(f"  ✗ --apply={_apply} 越界（可选 1..{len(_acts)}）", file=sys.stderr)
            return 2
        return _run_restart(_acts[_apply - 1][1])

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
        src = io_read(path)
        # **语法错必须非零退出**（L-12 实测：以前 fmt 对语法错的文件报"无变化"并
        # exit 0——CI 会把它当成"格式没问题"）。
        from parser import parse_source as _psf
        _rf = _psf(src, os.path.basename(path))
        if not _rf.ok:
            for _d in _rf.diagnostics:
                print(f"✗ {_d.code} @{_d.line}:{_d.col}  [{os.path.basename(path)}] {_d.message}",
                      file=sys.stderr)
            return 1
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


def _facts_for(ci, name):
    """取该函数 spec 的 requires 事实（A1）。没有 spec / 没有 requires 就返回空。"""
    try:
        from cost_check import facts_of_spec
        sp = getattr(ci, "specs", {}).get(name)
        return facts_of_spec(sp) if sp is not None else {}
    except Exception:                                  # noqa: BLE001
        return {}


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
                if os.path.splitext(tu)[1].lower() == ".eln":
                    # 名字只解析到 `.eln`（没有同名 `.tu`）：契约由 `@spec`
                    # 或同名 `.tu` 决定；两者都没有就保持"没有契约"的写法。
                    pair = _pair_from_eln(tu)
                    return (pair[0], pair[1]) if pair else (tu, tu)
                guess = os.path.splitext(tu)[0] + ".eln"
                if os.path.exists(guess):
                    pair = _pair_from_eln(guess)     # @spec 优先 / 同名回退
                    if pair:
                        return pair[0], pair[1]
    ch = None
    for a in pos[1:]:
        ch = a
        break
    if ch is None:
        # **推断出来的 `.eln` 要听它自己的 `@spec`**；显式写了两个文件
        # （`cost a.tu b.eln`）就一律按给的来——那条路在上面 `pos[1:]`，不过这里。
        guess = os.path.splitext(tu)[0] + ".eln"
        if os.path.exists(guess):
            pair = _pair_from_eln(guess)
            if pair:
                return pair[0], pair[1]
            ch = guess
    return tu, ch


def _no_tu(tu_p: Optional[str], ch_p: Optional[str] = None) -> bool:
    """**这个模块没有 `.tu`**（`.tu` 可选，规格 §8.9 / R1-B）。

    判据是"`.tu` 的位置上其实是一个 `.eln`"：只给一个 `.eln` 时，
    `_load_pair` 会把它同时当成 `.tu` 与 `.eln`（`tu` 位置就是那个 `.eln`）。
    以前没人识别这种情形，于是同一个文件被解析两遍：
      · 诊断带 `[.tu]` 前缀**重复出现**；
      · `E-Fn-NoSpec` 把 `run` / `build` 全挡住——**没有契约 ≠ 有错**。

    返回 True 时上层要做三件事：`.tu` 源用空串、不解析第二遍、命令照常跑。
    """
    if not tu_p:
        return True
    return os.path.splitext(tu_p)[1].lower() == ".eln"


def _module_name_of(tu_p: Optional[str], ch_p: Optional[str] = None) -> str:
    """模块名：优先 `.tu` 的文件名，没有 `.tu` 就取实现文件名。"""
    p = tu_p or ch_p or "module"
    return os.path.splitext(os.path.basename(p))[0]


def _abort_on_parse_errors(*paths) -> bool:
    """解析有 error 就别往下跑，返回 False。

    **为什么要有**：`measure` / `bench` / `stats` 以前不看解析诊断就继续，
    于是把「这个块写错位置了」报成「没有 benchmodel.scale」这种二次错误。
    作者照那句话去改，改不对——因为它指的根本不是病因。
    """
    from parser import parse_source
    bad = []
    for pth in paths:
        if not pth or not os.path.exists(pth):
            continue
        try:
            src = io_read(pth)
        except OSError:
            continue
        except Exception as exc:                       # noqa: BLE001
            # 非 UTF-8 源文件：交给下面的命令层统一报诊断（不在这里 traceback）。
            from parser import SourceEncodingError
            if isinstance(exc, SourceEncodingError):
                raise
            continue
        res = parse_source(src, os.path.basename(pth))
        # **宏展开来源**（2026-11-10）：落在宏展开行上的诊断补一句「由宏 … 展开」，
        # 否则读者只看到一个位置、不知道这行代码是宏生成的。
        from preproc import annotate as _pp_annotate
        _pp_annotate(getattr(res, "diagnostics", None))
        for d in (getattr(res, "diagnostics", None) or []):
            if getattr(d, "severity", "error") == "error":
                bad.append((pth, d))
    if not bad:
        return True
    print("✗ 解析没通过，后面的测量没有意义：", file=sys.stderr)
    for pth, d in bad[:8]:
        print("  %s @%s:%s  [%s] %s" % (getattr(d, "code", "?"), getattr(d, "line", "?"),
                                        getattr(d, "col", "?"), os.path.basename(pth),
                                        getattr(d, "message", "")), file=sys.stderr)
    return False



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
        print("用法：elench grammar <模块.tu> [--format=ebnf|json|skeleton|both] [--out=文件]",
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


def cmd_effects(argv: List[str]) -> int:
    """`elench effects <模块>` —— **成本按 effect 分列**（Code4AI ⑤）。

    做法：按**语句**切分——每条语句的 κ 由成本模型算，它触及的 effect 由
    `checker.node_effects` 算，然后归到一个桶里。
    桶是**互斥**的：纯计算 / 单个 effect / 混合(io+fs)——所以各桶相加 = 函数总 κ，
    不会重复计数。**只读，不改任何 κ。**
    """
    from parser import parse_source as _ps
    from cost_interp import CostInterp as _CI
    import checker as _ck
    tu_p, ch_p = _load_pair(argv)
    if tu_p is None or ch_p is None:
        print("用法: elench effects <模块>", file=sys.stderr)
        return 2
    tu_mod = _ps(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = _ps(io_read(ch_p), os.path.basename(ch_p)).module
    fns = [c for c in (ch_mod.children or []) if getattr(c, "kind", "") == "fn"]
    # **必须先把 extern 的 effects 收集进表**，否则 `print_line(...)` 这类调用
    # 的 effect 查不到，会被算成"纯计算"——那会把 io/fs 的账记错。
    try:
        _ck._collect_extern_effects(ch_mod)
    except Exception:                              # noqa: BLE001
        pass
    print(f"  module {os.path.splitext(os.path.basename(ch_p))[0]}")
    print("  （按语句切分；桶互斥，各桶相加 = 该函数总 κ。纯计算不含任何 effect。）")
    print()

    for fn in fns:
        nm = getattr(fn, "name", "") or ""
        body = next((c for c in fn.children if c.kind == "block"), None)
        if body is None:
            continue
        ci = _CI(ch_mod, tu_mod)
        env = {}
        buckets = {}                       # 桶名 → Cost
        for st in body.children:
            try:
                c = ci._stmt(st, env)
            except Exception:              # noqa: BLE001
                continue
            ef = sorted(_ck.node_effects(st, ch_mod) or [])
            key = "纯计算" if not ef else ("混合(" + "+".join(ef) + ")" if len(ef) > 1 else ef[0])
            from cost_domain.symbolic import Cost as _Cost
            buckets[key] = _Cost.add(buckets[key], c) if key in buckets else c
        if not buckets:
            continue
        print(f"    {nm}")
        for k, c in sorted(buckets.items(), key=lambda kv: -len(kv[1].render())):
            print(f"      {k:<14} {c.render()}")
        print()
    return 0


def cmd_spec(argv: List[str]) -> int:
    """`elench spec <模块>` —— **生成 spec 草案**（声明草案生成的最小版）。

    设计要点（见 `设计-E-声明草案生成.md`）：
    - **effects 能推**（读实现）；
    - **target 不能推**——从实现推出来的 target 会让"实现满足 spec"变成自证。
      所以草案里 target **留空**，只给「静态 κ」当参考，并写明"这是现状，不是承诺"。

    空 target 的 spec 是**合法的**（`check` 会说 `有实现但无 target`），
    所以草案能编译、能被后续工具处理，只是 `cost` 会提示它还没承诺。
    """
    from parser import parse_source as _ps
    from cost_interp import CostInterp as _CI
    tu_p, ch_p = _load_pair(argv)
    if tu_p is None or ch_p is None:
        print("用法: elench spec <模块>", file=sys.stderr)
        return 2
    tu_mod = _ps(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = _ps(io_read(ch_p), os.path.basename(ch_p)).module
    have = {c.name for c in (tu_mod.children or []) if getattr(c, "kind", "") == "spec"}
    fns = [c for c in (ch_mod.children or []) if getattr(c, "kind", "") == "fn"]

    def _type_str(n):
        """把类型节点还原成源码写法（含 `vector<u64>&` 这类泛型与引用）。"""
        if n is None:
            return ""
        k = getattr(n, "kind", "")
        if k == "type-ref" and n.children:
            return _type_str(n.children[0]) + "&"
        if k == "type-name":
            base = n.name or ""
            for c in (n.children or []):
                if getattr(c, "kind", "") == "type-args":
                    inner = ", ".join(_type_str(x) for x in (c.children or []))
                    return f"{base}<{inner}>"
            return base
        if getattr(n, "name", None):
            return n.name
        return "?"

    ci = None
    try:
        ci = _CI(ch_mod, tu_mod)
    except Exception:                                  # noqa: BLE001
        ci = None

    missing = 0
    for fn in fns:
        nm = getattr(fn, "name", "") or ""
        if not nm or nm in have:
            continue
        missing += 1
        ps = next((c for c in fn.children if c.kind == "params"), None)
        parts = []
        for prm in (ps.children if ps else []):
            ty = next((c for c in prm.children if c.kind in ("type-ref", "type-name")), None)
            parts.append(f"{_type_str(ty)} {prm.name}")
        ret = next((c for c in fn.children if c.kind in ("type-ref", "type-name")), None)
        k = None
        if ci is not None:
            try:
                k = ci.cost_of(nm).render()
            except Exception:                          # noqa: BLE001
                k = None
        print(f"// {nm}：target **要你自己定**——从实现推出来的上界等于自证。")
        if k:
            print(f"// 静态 κ（成本模型算的**上界**，仅供参考）：{k}")
        print(f"spec {nm}({', '.join(parts)}) -> {_type_str(ret)} {{")
        print("    effects { };")
        print("    // target { time: { max: ??? } };   // ← 填这里；不写也能过 check，但 cost 会报「无 target」")
        print("}")
        print()
    if missing == 0:
        print("  每个函数都有 spec 了。")
    else:
        print(f"  共 {missing} 个函数缺 spec（草案如上，复制进 .tu 后填 target）")
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

    ## 门前两关（2026-11-13 起）

    1. **静态检查**：与 `check` / `build` 同一条路（`_gate_checks`）。有 error ⇒
       rc≠0、**不写文件**。以前 `u64 main() { return nope; }` 能 rc=0 写出代码。
    2. **编译自检**：生成后用 g++ / clang++ / cl 真编一遍（`-fsyntax-only`），
       编不过 ⇒ rc≠0。找不到编译器就**明确说「跳过」**，绝不冒充通过。
       可用 `ELENCH_CXX`（或 `ELENCH_GXX`）指定编译器。

       **2026-11-14 起：自检先跑、文件后写**。以前是"先写 .cpp、再自检"，
       编不过的产物留在磁盘上，构建脚本会把它当成成功；现在**自检失败就
       不写**（有旧文件就删掉），stderr 说清是编不过、还是没写。

    ## 第三方库：`--include=` / `--include-dir=`

    生成物顶部以前只有 18 个写死的标准头，**0 条第三方 include、0 条 extern**。
    （标准头现在是 19 个：加 `<cstdlib>` 给 `elench_cstr` 的 NULL 分支用。）
    现在两条通道：

    | 开关 | 作用 |
    |---|---|
    | `--include=xxhash.h`（可重复） | 发 `#include "xxhash.h"`（`--include=<xxhash.h>` 发尖括号形式） |
    | `--include-dir=build/real-libs/xxHash`（可重复） | 自检 / `--run` 编译时加 `-I`；生成物本身不写路径 |

    `.eln` / `.tu` 里声明的 external 会自动发射 `extern "C" …;`（见 `_gen_cpp`），
    所以 `extern XXH64(...) -> u64 from "xxhash" effects {};` 不再"check 过了、
    生成物里却没有那句声明"。

    ## 链接真库：`--link=` / `--link-dir=` / `--lib=`

    `--include=` 只让生成物**编得过**；调用 `extern` 声明的真函数还要**链得上**。
    2026-11-14 之前 `dsh cpp --run` **只编译生成物、不链接任何外部库**，
    接 cJSON 得手工 `gcc -c cJSON.c` → `g++ gen.cpp cJSON.o` → `./a.exe`。
    现在三条通道（都可重复）：

    | 开关 | 进命令行的位置 |
    |---|---|
    | `--link=<.o/.obj/.a/.lib>` | 源文件之后、`-l` 之前（链接器从左到右解析，库必须在引用它的目标文件之后） |
    | `--link-dir=<目录>` | `-L<目录>`（`cl` 是 `/link /LIBPATH:<目录>`） |
    | `--lib=<名字>` | `-l<名字>`（`cl` 是 `<名字>.lib`） |
    | `--no-static` | 关掉 Windows/MinGW 上默认的 `-static` |

    校验是**当场**的：`--link=` 文件不存在、后缀不认识、`--link-dir=` 不是目录、
    `--lib=` 名字非法 ⇒ rc=2 并说清哪一条不对。**不把"没链上"当跳过**：
    链接失败 rc≠0，未定义符号会提示用 `--link=` / `--lib=` 补。

    `-static` 默认开（仅 Windows + g++）：MinGW 的 exe 默认动态依赖
    `libstdc++-6.dll` / `libgcc_s_seh-1.dll`，离开编译器目录运行就
    `STATUS_DLL_NOT_FOUND`（3221225781）——对拍会**假红**。静态链接让生成物
    「拷到哪里都能跑」，也让对拍不受 PATH 影响。

    **`--link*= ` 只影响 `--run` 的编译/链接**：自检仍然是 `-fsyntax-only`，
    生成物文本里不写任何本机路径（同一个 `.cpp` 换台机器仍然可编）。

    ## 解释器侧的库搜索：`--lib-dir=`（可重复）

    `--link*` 解决的是「**g++ 链得上**」，`--lib-dir=` 解决的是「**ctypes 找得到**」。
    2026-11-14 之前这两件事只有一件有开关：

    ```
    extern cJSON_Version() -> vector<u8> from "cjson" effects {};
    dsh cpp v.eln --run --link=…/cJSON.o     ✓ 生成物能链接、能跑
    dsh run v.eln                            ✗ 加载外部库失败（cjson）：
                                                Could not find module 'cjson'
    ```

    根因不是"库没编"，是**名字怎么解析**：Python 3.8+ 的 `ctypes.CDLL` 对裸名
    既不搜 cwd 也不搜 PATH（Windows 走受限的 `LoadLibraryEx` 口径），所以
    `"cjson"` 永远找不到；而同一个 `.eln` 换成绝对路径就能跑。于是 `--run`
    虽然能编译，解释器侧却总是「无法对拍」。

    `--lib-dir=<目录>` 的解析顺序（`ffi_c.library_candidates`，**写进文档，
    别让人猜**）：

    | # | 候选 | 说明 |
    |---|---|---|
    | 1 | `from "…"` 里写的是**绝对路径 / 带路径分隔符** | 原样用，不补后缀 |
    | 2 | 每个 `--lib-dir`（按给出顺序）下 `<名字><平台后缀>` | Windows `.dll` / Linux `.so` / macOS `.dylib` |
    | 3 | 同一目录下 `<名字>`（不补后缀） | `from "libcjson.so"` 这种已经把后缀写全的 |
    | 4 | 最后把裸名**原样交给平台加载器** | `ucrtbase` / `libc` 这类系统库只能这样找；这是加 `--lib-dir` 之前的唯一旧行为 |

    **不额外搜 cwd、不额外搜 PATH**：候选表里没有这两项（第 4 步是平台加载器
    自己的规则，所以它排在所有 `--lib-dir` 之后——给了 `--lib-dir` 就一定优先，
    "跑的是哪个库"可控）。加载失败时错误消息**列出试过的每个路径**，并区分
    「找不到该文件」和「文件在、加载失败」（位数 / 依赖不对）。

    `--lib-dir=` 只递解释器侧（`_cpp_run_passthrough`），不进 g++ 命令行——
    链接找库仍然用 `--link=` / `--link-dir=` / `--lib=`。接一个真库通常两个都要给：

    ```
    dsh cpp v.eln --include=cJSON.h --include-dir=build/real-libs/cJSON \
        --run --link=build/cjson/cJSON.o --lib-dir=build/cjson
    ```

    ## 对拍：`--run`

    找 `g++` / `clang++` / `cl`；找到就**编译 → 链接 → 跑 → 和 `dsh run` 比**：

    - stdout **逐字节相同**（`dsh run` 末尾那行 main 返回值按
      `tools/behavior_parity.py:strip_return_line` 的口径剥掉）；
    - 返回值落在 0..255 时，退出码也要相同（更大值会被 OS 截断，只报「不参与」）；
    - 解释器末行不是整数（如 `bool main()` 打 `True`）⇒ **对拍跳过，未测量**
      （rc=0 并明说：分不清返回值与程序输出，不硬判成失败）；
    - `--compat` / `--no-check` / `--strict` / `--relax=` / `--checks=` /
      `--lib-dir=` **递给解释器**（见 `_cpp_run_passthrough`）：两边必须用同一个
      口径读同一份源码、找同一批库；
    - 任何一步失败 ⇒ rc≠0，**不冒充成功**；临时目录跑完即删。
    - **找不到编译器 ⇒ 明说「跳过，未测量」**（rc=0 是既有口径：那不是验证失败，
      而是没测量）。
    """
    from parser import parse_source as _ps

    # **第三方头 / 头目录**先解析（纯命令行，不碰模块）：头名里有引号、尖括号
    # 之外的注入字符就拒绝——这是唯一一条"能把任意 C++ 写进生成物"的通道。
    includes: List[str] = []
    include_dirs: List[str] = []
    for a in argv:
        if a.startswith("--include="):
            try:
                includes.append(_cpp_include_line(a.split("=", 1)[1]))
            except ValueError as exc:
                print(f"✗ {exc}", file=sys.stderr)
                return 2
        elif a.startswith("--include-dir="):
            _d = a.split("=", 1)[1].strip()
            if not _d:
                print("✗ `--include-dir=` 要一个目录（不能是空串）", file=sys.stderr)
                return 2
            include_dirs.append(_d)

    # **链接通道**（2026-11-14）：`--link=` / `--link-dir=` / `--lib=` 只进
    # 「自检之后」的那条编译命令 —— 生成物文本一个字节都不变，所以换台机器
    # 仍然可编；链接输入是本机路径，不该被写进 `.cpp`。
    # 校验放在这里（而不是链接失败后）：`cannot find …` / `undefined reference`
    # 离病因很远，先拦下"文件根本不存在"能让 rc≠0 的原因直接可读。
    link_files: List[str] = []
    link_dirs: List[str] = []
    libs: List[str] = []
    for a in argv:
        try:
            if a.startswith("--link-dir="):
                link_dirs.append(_cpp_link_dir(a.split("=", 1)[1]))
            elif a.startswith("--link="):
                link_files.append(_cpp_link_file(a.split("=", 1)[1]))
            elif a.startswith("--lib="):
                libs.append(_cpp_lib_name(a.split("=", 1)[1]))
        except ValueError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 2
    if (link_files or link_dirs or libs) and "--run" not in argv:
        print("  · `--link=` / `--link-dir=` / `--lib=` 只在 `--run` 时生效："
              "编译自检是 `-fsyntax-only`，不链接", file=sys.stderr)

    # **解释器侧的库搜索目录**（2026-11-14）：`--lib-dir=` 交给 `dsh run`
    # （`--run` 的对拍那一侧），**不进链接命令行**——链接找库用
    # `--link=` / `--link-dir=` / `--lib=`。两边都收是因为一条命令要同时做两件事：
    # `g++` 链接和 ctypes 加载。当场校验：搜索路径不是目录，只会让对拍报
    # 「无法对拍」，原因却离得很远。
    lib_dirs: List[str] = []
    for a in argv:
        if a.startswith("--lib-dir="):
            try:
                lib_dirs.append(_lib_dir(a.split("=", 1)[1]))
            except ValueError as exc:
                print(f"✗ {exc}", file=sys.stderr)
                return 2
    if lib_dirs and "--run" not in argv:
        print("  · `--lib-dir=` 只在 `--run` 的解释器侧生效（加载外部库用），"
              "本次没有 `--run`，它没有被使用", file=sys.stderr)

    tu_p, ch_p = _load_pair(argv)
    if tu_p is None or ch_p is None:
        print("用法: elench cpp <模块> [--output=x.cpp] [--run] "
              "[--include=<头>] [--include-dir=<目录>] "
              "[--link=<.o|.a|.lib>]… [--link-dir=<目录>]… [--lib=<名字>]… "
              "[--lib-dir=<目录>]… [--no-static]", file=sys.stderr)
        return 2
    # **静态检查必须先跑**（2026-11-13，L0 止血）：
    # 以前这里直接 `_gen_cpp`，`u64 main() { return nope; }` 也能 rc=0 并写出代码——
    # 生成器再准也补不上"没查过"这件事。与 `build` / `run` 走**同一条**路
    # （`_gate_checks` → `_diagnose_all`）：有 error ⇒ rc≠0、且**不写文件**。
    # **解析失败必须非零退出**（L-12 实测：以前语法错也 exit 0，
    # CI 会把"生成了错的 C++"当成成功）。
    _res = []
    for _p in (tu_p, ch_p):
        _r = _ps(io_read(_p), os.path.basename(_p))
        if not _r.ok:
            for _d in _r.diagnostics:
                print(f"✗ {_d.code} @{_d.line}:{_d.col}  [{os.path.basename(_p)}] {_d.message}",
                      file=sys.stderr)
        _res.append(_r)
    if any(not _r.ok for _r in _res):
        return 1
    tu_mod, ch_mod = _res[0].module, _res[1].module

    # 解析通过之后、进生成器之前：跑静态检查（有 error 就既不生成也不写文件）。
    if not _gate_checks(tu_p, ch_p, argv):
        return 1

    try:
        text = _gen_cpp(tu_mod, ch_mod, includes=includes)
    except CppUnsupported as exc:
        print(f"✗ E-Cxx-Unsupported: {exc}", file=sys.stderr)
        return 1

    out = next((a.split("=", 1)[1] for a in argv if a.startswith("--output=")), None)
    # **生成物的最后一关：真编一遍**（2026-11-13）。
    # 理由：这 5 个 bug 的共性就是"exit 0 不代表代码对"。文本断言只能防已知形状，
    # 编译器能防"没预见到"的形状。没有编译器就**明确跳过**，绝不冒充通过。
    # **先自检、后落盘**（2026-11-14）：编不过的 .cpp 不许留在输出路径上——
    # 构建脚本只看"文件在不在"，会把上一轮的旧产物当成本轮成功。
    status, detail = _cpp_compile_text(text, include_dirs=include_dirs)
    if status == "fail":
        print(f"✗ 编译自检失败：{detail[:600]}", file=sys.stderr)
        if out:
            try:
                if os.path.exists(out):
                    os.remove(out)
                    print(f"  已删除 {out}（上一轮的旧产物：留着会被当成这次成功）",
                          file=sys.stderr)
                else:
                    print(f"  未写出 {out}（自检失败的产物不落盘）", file=sys.stderr)
            except OSError as exc:                     # noqa: BLE001
                print(f"  ✗ 删不掉旧产物 {out}：{exc}（它现在**不可信**，请手动删）",
                      file=sys.stderr)
        else:
            print("  未输出源码（stdout 一个字节都没写）", file=sys.stderr)
        return 1
    if status == "skip":
        print(f"  ⚠ 跳过编译自检：{detail}", file=sys.stderr)
    else:
        print(f"  ✓ 编译自检通过（{detail}）：生成的 C++ 能被编译器接受", file=sys.stderr)
    if out:
        io.open(out, "w", encoding="utf-8", newline="\n").write(text)
        print(f"  {out}  ←  C++ 源码（{len(text.splitlines())} 行）")
    else:
        # stdout 只放源码：用户可以 `elench cpp m > m.cpp` 直接管道。
        # 编译自检的结果一律走 stderr。
        sys.stdout.write(text)
    if "--run" in argv:
        return _cpp_compare(tu_p, ch_p, text, include_dirs=include_dirs,
                            link_files=link_files, link_dirs=link_dirs,
                            libs=libs, no_static=("--no-static" in argv),
                            run_args=_cpp_run_passthrough(argv))
    return 0


class CppUnsupported(Exception):
    pass


# ---------------------------------------------------------------- 哈希容器适配器
#
# **为什么在 `dsh.py` 里做，而不是改 `cxx_map.py`**（2026-11-13，最后一个语料模块）：
# `cxx_map` 的 `UNMAPPED_TYPES` 写了两条理由——`std::unordered_map` 要求键可哈希
# （`vector<u8>` 没有 `std::hash`）、且哈希容器顺序不可复现。两条都真实存在，
# 但**后端能绕过它们**：发射一个「保持插入序、只依赖 `==`」的适配器
# （`_CPP_RUNTIME` 里的 `elench_umap`）。"换哪个容器"是后端的发射决定，
# 不是类型层的翻译决定；所以 `cxx_map.cxx_type("unordered_map<…>")` **仍然返回
# None**（那是它的契约，也有测试锁着），转换只发生在这一层。
#
# **不能简单映射成 `std::map`**：`UnorderedMapVal` 的 `keys()` 是插入序
# （`interp.py` 的 `_HashMapData` 就是 Python dict，`keys()` 按插入序返回），
# 而 `std::map` 按 `<` 排序 ⇒ 对拍必红。
_CPP_HASH_TYPES = {
    # Elench 容器名 → (C++ 适配器名, 类型参数个数)
    "unordered_map": ("elench_umap", 2),
}


def _cpp_split_type_app(text: str):
    """把 `Head<A, B>` 拆成 `("Head", ["A", "B"])`；不是应用类型返回 `(None, None)`。

    只做**形状**拆分（按顶层逗号切），不解释任何名字——内容一律交给 `cxx_map`，
    避免这里长出第二张类型表。尖括号不配平就返回 `(None, None)`，让 `cxx_map`
    报它自己那条「解析失败」的错（同一类错误只有一个出处）。
    """
    t = (text or "").strip()
    i = 0
    while i < len(t) and (t[i].isalnum() or t[i] == "_"):
        i += 1
    head, rest = t[:i], t[i:].strip()
    if not head or not rest.startswith("<") or not rest.endswith(">"):
        return None, None
    depth = 0
    for ch in rest:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
            if depth < 0:
                return None, None
    if depth != 0:
        return None, None
    args, cur, depth = [], "", 0
    for ch in rest[1:-1]:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
            continue
        cur += ch
    args.append(cur.strip())
    return head, args


def _cpp_key_has_eq(cxx_key: str) -> bool:
    """这个 C++ 键类型有没有**逐值相等**（`==`）？适配器只靠它，不需要 hash。

    白名单而不是黑名单：键会进 `std::deque<std::pair<K,V>>` 做线性扫描，
    比较的是 `K::operator==`。标准库对 `std::vector<T>` / `std::array<T,N>`
    定义了逐元素 `==`（元素可比时），标量天然可比；**用户 struct 没有相等契约**
    （本语言的 checker 也静态拒绝结构体键，这里是第二道闸），`std::map` /
    `std::optional` 之类在这里一律不放行——宁可报错，不要生成编不过的代码。
    """
    import cxx_map as _cm
    k = (cxx_key or "").strip()
    scalars = {v for v in _cm.TYPE_MAP.values() if "<" not in v}
    if k in scalars:
        return True
    return k.split("<")[0].strip() in ("std::vector", "std::array")


def _cpp_hash_type(ench_type: str, user_types=None):
    """把类型串里的 `unordered_map<K,V>` 换成插入序适配器；**没有就返回 None**。

    返回 None 表示"这串类型里没有哈希容器"，调用方照旧走 `cxx_map`——
    这样 18 个既有模块的映射路径**一个字节都不变**。
    """
    import cxx_map as _cm
    head, args = _cpp_split_type_app(ench_type)
    if head is None:
        return None                                  # 叶子：没有哈希容器
    if head not in _CPP_HASH_TYPES:
        # 非哈希容器：只有子树里真的嵌了哈希容器时才需要我们自己重写；
        # 否则整串交给 cxx_map（`array<u64, N>` 的容量、`map<K,V>` 都走原路）。
        subs = [_cpp_hash_type(a, user_types) for a in args]
        if all(s is None for s in subs):
            return None
        raise CppUnsupported(
            f"`{head}<…>` 里嵌了 `unordered_map`：哈希容器目前只支持出现在"
            f"**类型最外层**（形参 / 变量本身就是 `unordered_map<K,V>`）。"
            f"要嵌进别的容器，得先给外层容器也写一个能返回引用的适配器——"
            f"本轮报错，不猜（`{ench_type}`）")
    cxx, arity = _CPP_HASH_TYPES[head]
    if len(args) != arity:
        raise CppUnsupported(
            f"`{head}<…>` 需要 {arity} 个类型参数，实际 {len(args)} 个：`{ench_type}`")
    rendered = []
    for a in args:
        r = _cpp_hash_type(a, user_types)
        if r is None:
            r = _cm.cxx_type(a, user_types)
        if not r:
            raise CppUnsupported(
                f"`{head}<…>` 的类型参数 `{a}` 没有 C++ 映射（不猜）")
        rendered.append(r)
    if not _cpp_key_has_eq(rendered[0]):
        raise CppUnsupported(
            f"`{head}<…>` 的键类型 `{rendered[0]}` 在 C++ 侧没有逐值相等（`==`）："
            f"插入序适配器只需要 `==`、不需要 hash，但用户 struct 还没有相等契约。"
            f"请改用标量 / `vector<T>` / `array<T,N>` 做键，或改用 `map<K,V>`")
    return f"{cxx}<{', '.join(rendered)}>"


def _cxx_type_of(name: str, user_types=None) -> str:
    """Elench 类型 → C++ 类型。**认不出就报错，绝不兜底成 uint64_t**。

    2026-11-13（L0 止血）：旧实现是 `cxx_type(name) or "uint64_t"`，于是
    `f64 x = 1;` 会静默生成 `uint64_t x = 1;`——能编、能跑、结果错。
    这里改成抛 `CppUnsupported`：宁可失败，也不要生成"看起来成功"的错代码。

    `user_types`（本轮新增）只放**本次生成真的会发射定义的 struct 名**：
    `.eln` 里声明的 struct，`_gen_cpp` 会在函数前面写 `struct X { … };`，
    所以它是已知类型。**`.tu` 里声明的 struct 不登记**——那是契约，不是实现，
    生成器看不到字段，写出来就是猜（`test_cpp_gen.py` [4b] 锁死这条）。

    **哈希容器先走 `_cpp_hash_type`**（2026-11-13，08_bits_hash）：`unordered_map`
    在 `cxx_map` 里仍是"故意不映射"，但后端能发射一个插入序适配器——
    那是发射决定，不是翻译决定，所以只在这一层发生（理由见上面的长注释）。
    """
    import cxx_map as _cm
    key = (name or "").strip()
    mapped = _cpp_hash_type(key, user_types) if key else None
    if mapped is None:
        mapped = _cm.cxx_type(key, user_types) if key else ""
    if not mapped:
        raise CppUnsupported(
            f"类型 `{key or '?'}` 没有 C++ 映射（不会静默退化成 uint64_t）。"
            f"要支持它请把映射加进 `cxx_map.TYPE_MAP`（哈希是个例外："
            f"`unordered_map` 在 `_CPP_HASH_TYPES` 里换成插入序适配器，"
            f"见 `_cpp_hash_type`）；本轮 `dsh cpp` 只保证不生成错的")
    return mapped


class _CppCtx:
    """一次生成要用的上下文：认出哪些用户类型、每个 struct 的字段顺序。

    **只登记本模块（`.eln`）里真的会发射 `struct X { … };` 的名字**：
    字段顺序也在这里，`struct-literal` 按**声明顺序**发射（C++17 聚合初始化是
    位置的，源码里的 `Name{…}` 若换了书写顺序，按位置发就是静默错值）。

    2026-11-13 起还带一个**极小的类型环境**（`scopes`）：只记"这个名字的声明
    类型串是什么"（形参 / 局部变量 / struct 字段 / range-for 绑定）。它**不是**
    类型检查器，只用于「发射哪一套容器方法」的分派（例如 `insert`）；
    查不到就是 `None`，调用方必须报错，不许猜成某个默认容器。
    """

    def __init__(self):
        self.user_types = set()
        self.struct_fields = {}
        #: (struct 名, 字段名) → Elench 类型串。只登记本模块（`.eln`）的 struct：
        #: `.tu` 里的 struct 是契约，看不到字段，登记了就是猜（同 `user_types`）。
        self.struct_field_types = {}
        #: 变量名 → 声明类型串 的作用域栈（里层遮蔽外层）。
        self.scopes = [{}]
        #: 外部符号名 → 每个形参在 C 里是不是指针型（`ffi_c` 对容器编组成
        #: `const char*`）。`_cpp_call` 据此把 `std::vector<u8>` 取 `.data()`
        #: 传出去——vector 不会隐式转成指针，不转换就必然编不过。
        self.extern_params = {}
        #: 返回类型在 `ffi_c` 里是 `_CString`（C 的 `const char*`）的外部符号名。
        #: `_cpp_call` 据此把返回值包成 `elench_cstr(…, "名字")`——
        #: `std::vector<uint8_t>` 也不会从指针隐式构造（见 `_cpp_extern_vec_returns`）。
        self.extern_vec_returns = set()

    def type_of(self, name: str) -> str:
        return _cxx_type_of(name, self.user_types)

    # ---- 类型环境（供 `insert` 一类"必须先知道接收者类型"的发射用）----------
    def clear_scopes(self) -> None:
        self.scopes = [{}]

    def push_scope(self) -> None:
        self.scopes.append({})

    def pop_scope(self) -> None:
        if len(self.scopes) > 1:
            self.scopes.pop()

    def bind(self, name, type_text) -> None:
        """登记一个名字的声明类型；`type_text` 为空串表示"有声明但判不出"。"""
        if name:
            self.scopes[-1][name] = type_text or ""

    def lookup(self, name):
        """查名字的声明类型：未登记返回 `None`，登记但判不出返回 `""`。"""
        for sc in reversed(self.scopes):
            if name in sc:
                return sc[name]
        return None


#: 容器头别名 → 规范头。`string` 在 parser 里已归一成 `vector<u8>`，这里再兜一层
#: （旧 AST / 直接构造的节点也能认）；`Vec`/`Map`/`Set`/`FixedVec` 是改名期旧写法。
_CPP_HEAD_ALIASES = {
    "Vec": "vector", "string": "vector", "Text": "vector",
    "Map": "map", "Set": "set", "FixedVec": "array",
}

#: 序列容器（`insert(pos, value)` 位置语义；发射成 C++ 的同名位置插入）。
#: `array` 单独处理：`std::array` **定长、没有 insert/push_back/clear**。
_CPP_SEQ_HEADS = frozenset({"vector"})
_CPP_MAP_HEADS = frozenset({"map", "unordered_map"})
_CPP_SET_HEADS = frozenset({"set", "unordered_set"})


def _cpp_strip_type(tstr: str) -> str:
    """去掉类型串外层的 `const` / `&` / `mut`（只用于**判头**，不用于发射）。"""
    t = (tstr or "").strip()
    changed = True
    while changed:
        changed = False
        for prefix in ("const ", "mut "):
            if t.startswith(prefix):
                t = t[len(prefix):].strip()
                changed = True
        if t.endswith("&"):
            t = t[:-1].strip()
            changed = True
    return t


def _cpp_head_of(tstr) -> str:
    """`map<string, u64>` → `"map"`；`const vector<u64>&` → `"vector"`。"""
    t = _cpp_strip_type(tstr)
    if not t:
        return ""
    head = t.split("<", 1)[0].strip()
    return _CPP_HEAD_ALIASES.get(head, head)


def _cpp_type_args(tstr):
    """`map<K, V>` → `["K", "V"]`（尖括号配平的顶层逗号切分）；不是应用类型返回 `[]`。

    与 `cxx_map` 的解析器不同，这里**只切分、不校验**：结果只用于判
    "元素/值是什么容器"，判错会被下游的 `cxx_map` 映射挡住，不会静默发射。
    """
    t = _cpp_strip_type(tstr)
    lt = t.find("<")
    if lt < 0 or not t.endswith(">"):
        return []
    inner = t[lt + 1:-1]
    out, cur, depth = [], [], 0
    for ch in inner:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return out


def _cpp_elem_type(tstr):
    """容器的元素/值类型串：`vector<T>`→T、`map<K,V>`→V、`set<T>`→T；认不出 `None`。"""
    head = _cpp_head_of(tstr)
    args = _cpp_type_args(tstr)
    if head in ("vector", "array", "set", "unordered_set"):
        return args[0] if args else None
    if head in _CPP_MAP_HEADS:
        return args[1] if len(args) > 1 else None
    return None


def _cpp_expr_type(e, ctx):
    """**只认铁证**的静态类型推导：path / paren / field / index。

    不认函数调用、方法调用、二元表达式等（那些要真做类型推导）。返回
    声明类型**串**或 `None`（`None` = 判不出 ⇒ 调用方必须报错，不许猜）。
    """
    k = getattr(e, "kind", "")
    if k == "path":
        return ctx.lookup(getattr(e, "name", "") or "")
    if k == "paren":
        return _cpp_expr_type(e.children[0], ctx) if e.children else None
    if k == "field":
        base = e.children[0] if e.children else None
        bt = _cpp_expr_type(base, ctx)
        if not bt:
            return None
        return ctx.struct_field_types.get(
            (_cpp_strip_type(bt), getattr(e, "name", "") or ""))
    if k == "index":
        # `xs[i]`：序列取元素、map 取值（`interp` 的 `m[k]` 就是值）。
        bt = _cpp_expr_type(e.children[0], ctx) if e.children else None
        return _cpp_elem_type(bt) if bt else None
    if k == "ternary":
        # **三目**（2026-11-13）：只有两支的类型串**逐字节相同**才算铁证。
        # 不同（`u64` vs `u32`、`vector<u64>` vs `list<u64>`）就 `None`——
        # 那要真做类型推导，属于调用方必须报错的情形（`_cpp_need_head`）。
        a = _cpp_expr_type(e.children[1], ctx) if len(e.children) > 1 else None
        b = _cpp_expr_type(e.children[2], ctx) if len(e.children) > 2 else None
        return a if (a and b and a == b) else None
    return None


def _cpp_recv_head(e, ctx):
    """接收者的容器头（`vector`/`map`/`unordered_map`/`set`/`array`…）；判不出 `None`。"""
    t = _cpp_expr_type(e, ctx)
    if not t:
        return None
    return _cpp_head_of(t) or None


#: C++ 头文件：**一次性发全**（用户口径允许）。选哪些是按语料实际用到的
#: 容器 / 算法 / 数学函数 + 本节运行时工具的需要：`std::accumulate` 要
#: `<numeric>`，`std::min/max/sort` 要 `<algorithm>`，`std::abs` 要 `<cmath>`，
#: `{:.6f}` 的定点渲染要 `<cstdio>`，`<type_traits>` 给 `if constexpr`，
#: `elench_cstr` 的 NULL 分支要 `<cstdlib>` 的 `std::abort`。
_CPP_HEADERS = (
    "algorithm", "array", "charconv", "cmath", "cstdint", "cstdio", "cstdlib",
    "cstring", "deque", "initializer_list", "iostream", "map", "numeric",
    "optional", "set", "string", "type_traits", "utility", "vector",
)

#: 运行时小工具（前缀 `elench_`，不会撞用户符号）。**一次性发全**：
#: 少发一个，某个形状就会在编译自检里失败，而不是在生成时说清楚。
#:
#: 三条语义对齐（都能在 `interp.py` 找到出处）：
#:   * 文本是 `std::vector<uint8_t>`（与 `cxx_map` 对 `string`/`vector<u8>` 的
#:     映射一致），`print` 按字节写出去——`_text_of` 的规则；
#:   * `{:.6f}` 走 `std::snprintf("%f")`，与 C 的 `%f` 逐字节相同；
#:   * `print` 以 `\n` 结尾、`write` 不换行（`stdlib._write` 的文档句）。
_CPP_RUNTIME = r"""
// ---- elench 运行时小工具：只在生成物里用；前缀 elench_ 避开用户符号 ----
using elench_text_t = std::vector<uint8_t>;

// ---- `unordered_map<K,V>`：**插入序**适配器（不是 std::unordered_map）--------
//
// 为什么不用 std::unordered_map：键的可哈希性是硬门槛——本语言最常见的键
// `vector<u8>`（文本）在 C++ 里是 `std::vector<uint8_t>`，**没有 std::hash**，
// 映射出来必然编不过；而且 libstdc++ 的桶序**不可复现**（同一份源码换个版本
// 就能变），与解释器的可观测输出对不上。
// 为什么不用 std::map：它按 `<` 排序，迭代序变成键序 ⇒ 与解释器对拍必红。
//
// 这里用「按插入序排列的 (键,值) 线性表」：
//   · 键只需要**逐值相等**（`std::vector<uint8_t>` 的 `==` 是逐字节比较），不要 hash；
//   · 迭代序 = 插入序 = 解释器 `UnorderedMapVal._HashMapData`（Python dict）的
//     `keys()` 顺序（`interp.py` 的注释自己写明"这里返回插入序"）；
//   · 每次操作最坏 O(n)：**不超出**本语言给 `unordered_map` 承诺的上界 O(n)，
//     但也不假装平均 O(1)。语料里 n 是 3（词频），线性扫描的成本可以忽略。
// 语义逐条对齐 `interp.py` 的 UnorderedMapVal 分支：
//   · `m[k]` 缺键 ⇒ 插入 `V{}` 并返回引用（同 `_k_expr_index`）；
//   · `insert(k,v)` **覆盖**已有键、返回 0（同 interp；注意 `map` 的 `insert` 不覆盖）；
//   · `get(k)` 缺键要报错 ⇒ 发射成 `at()`（std::map / std::vector 同名），不静默补 0；
//   · `contains / remove / erase / size / empty / clear` 同名同义。
template <class K, class V>
class elench_umap {
public:
    using key_type = K;
    using mapped_type = V;
    using value_type = std::pair<K, V>;
    // 槽用 deque：`push_back` 不会让已经交出去的 `V&` 失效（vector 扩容会）。
    using storage_type = std::deque<value_type>;
    using iterator = typename storage_type::iterator;
    using const_iterator = typename storage_type::const_iterator;

    iterator begin() { return d_.begin(); }
    iterator end() { return d_.end(); }
    const_iterator begin() const { return d_.begin(); }
    const_iterator end() const { return d_.end(); }
    std::size_t size() const { return d_.size(); }
    bool empty() const { return d_.empty(); }
    void clear() { d_.clear(); }

    V& operator[](const K& k) {
        for (auto& kv : d_) if (kv.first == k) return kv.second;
        d_.emplace_back(k, V{});
        return d_.back().second;
    }

    V& at(const K& k) {
        for (auto& kv : d_) if (kv.first == k) return kv.second;
        throw std::out_of_range("elench_umap::at: key not found");
    }
    const V& at(const K& k) const {
        for (const auto& kv : d_) if (kv.first == k) return kv.second;
        throw std::out_of_range("elench_umap::at: key not found");
    }

    bool contains(const K& k) const {
        for (const auto& kv : d_) if (kv.first == k) return true;
        return false;
    }

    // `insert` 覆盖（与解释器一致）；返回 0 让它在语句/表达式两种位置都能用。
    std::size_t insert(const K& k, const V& v) {
        for (auto& kv : d_) if (kv.first == k) { kv.second = v; return 0; }
        d_.emplace_back(k, v);
        return 0;
    }

    std::size_t remove(const K& k) {
        for (auto it = d_.begin(); it != d_.end(); ++it) {
            if (it->first == k) { d_.erase(it); return 1; }
        }
        return 0;
    }
    std::size_t erase(const K& k) { return remove(k); }

private:
    storage_type d_;
};

static std::vector<uint8_t> elench_str(const char* s) {
    return std::vector<uint8_t>(reinterpret_cast<const uint8_t*>(s),
                                reinterpret_cast<const uint8_t*>(s) + std::strlen(s));
}

static void elench_put(std::vector<uint8_t>& out, const std::vector<uint8_t>& s) {
    out.insert(out.end(), s.begin(), s.end());
}

// ---- 外部函数返回 `const char*` → `Vec<u8>`（2026-11-14）--------------------
//
// `ffi_c.restype_for("vector<u8>")` 是 `_CString`：C 的 `const char*` 先收下、
// 再按 NUL 读成字节（NULL 在那里是一次明确的失败，不是 `None`）。C++ 侧没有
// 这层转换：`std::vector<uint8_t>` **不会**从 `const char*` 隐式构造，所以
// `vector<u8> v = cJSON_Version();` 以前编不过（声明对、调用点断）。生成器
// 现在在调用点显式包一层（`_cpp_call`），这里就是那一层。
//
// **NULL 怎么办**：`Vec<u8>` 表示不了 NULL（语言里没有"空指针"这个值）。
// 这里**明确失败**，绝不折叠成空 vector——把 NULL 和 `""` 混成一个值正是本
// 项目最反对的静默错值。代价（写进 `文档/语法清单.md`）：这是一次 abort，
// **退出码非 0**（Windows/UCRT 是 0xC0000409，Linux 是 134）；局部对象不析构、
// 已缓冲的 stdout 先刷出去再死；它不是语言诊断码，也不可捕获。
// 要判空请把返回类型声明成 `u64`（空指针 = 0），那条路本来就能写。
static std::vector<uint8_t> elench_cstr(const char* p, const char* fn) {
    if (p == nullptr) {
        std::fflush(stdout);
        std::fprintf(stderr,
                     "elench: 外部函数 %s 返回了空指针（NULL），但返回类型 "
                     "`Vec<u8>` 只能表示一段 NUL 结尾的字节 —— NULL 没有对应的值。"
                     "（不静默返回空 vector：那会把 NULL 和空串混成一个值；"
                     "要判空把返回类型声明成 `u64`）\n", fn);
        std::fflush(stderr);
        std::abort();
    }
    return std::vector<uint8_t>(reinterpret_cast<const uint8_t*>(p),
                                reinterpret_cast<const uint8_t*>(p) + std::strlen(p));
}

static std::vector<uint8_t> elench_u(uint64_t v) {
    const std::string s = std::to_string(v);
    return std::vector<uint8_t>(s.begin(), s.end());
}

static std::vector<uint8_t> elench_d(int64_t v) {
    const std::string s = std::to_string(v);
    return std::vector<uint8_t>(s.begin(), s.end());
}

static std::vector<uint8_t> elench_f(double v) {
    char buf[64];
    std::snprintf(buf, sizeof buf, "%f", v);
    return elench_str(buf);
}

// `print(1.5)` / `{}` 里的浮点：解释器用 `str(v)`（`200.0` 不会写成 `200`）。
// 最短往返表示 + 补一个 `.0`，和 Python 的 `repr` 对齐；没有 to_chars 的旧编译器
// 退回 `%.17g`（只是排版不同，值不丢）。
static std::vector<uint8_t> elench_repr(double v) {
#if defined(__cpp_lib_to_chars) && __cpp_lib_to_chars >= 201611L
    char buf[64];
    const auto res = std::to_chars(buf, buf + sizeof buf, v);
    std::string s(buf, res.ptr);
    if (s.find_first_of(".eEn") == std::string::npos) s += ".0";
    return std::vector<uint8_t>(s.begin(), s.end());
#else
    char buf[64];
    std::snprintf(buf, sizeof buf, "%.17g", v);
    return elench_str(buf);
#endif
}

static std::vector<uint8_t> elench_s(const std::vector<uint8_t>& s) { return s; }

// `{}`（默认洞）的渲染：文本原样、标量按解释器 `str()` 的口径。
template <class T>
static std::vector<uint8_t> elench_text(const T& v) {
    if constexpr (std::is_same_v<T, std::vector<uint8_t>>) {
        return v;
    } else if constexpr (std::is_same_v<T, bool>) {
        return elench_str(v ? "True" : "False");
    } else if constexpr (std::is_same_v<T, char>) {
        return std::vector<uint8_t>(1, static_cast<uint8_t>(v));
    } else if constexpr (std::is_same_v<T, signed char> || std::is_same_v<T, unsigned char>) {
        return elench_u(static_cast<uint64_t>(v));
    } else if constexpr (std::is_floating_point_v<T>) {
        return elench_repr(static_cast<double>(v));
    } else if constexpr (std::is_signed_v<T>) {
        return elench_d(static_cast<int64_t>(v));
    } else {
        return elench_u(static_cast<uint64_t>(v));
    }
}

static std::vector<uint8_t> elench_join(std::initializer_list<std::vector<uint8_t>> parts) {
    size_t n = 0;
    for (const auto& p : parts) n += p.size();
    std::vector<uint8_t> r;
    r.reserve(n);
    for (const auto& p : parts) r.insert(r.end(), p.begin(), p.end());
    return r;
}

// `text + text`：解释器的 `+` 对文本就是拼接（语料里 `text = text + "…"`）。
static elench_text_t elench_add(const elench_text_t& a, const elench_text_t& b) {
    elench_text_t r;
    r.reserve(a.size() + b.size());
    r.insert(r.end(), a.begin(), a.end());
    r.insert(r.end(), b.begin(), b.end());
    return r;
}

// `utf8_encode(cp)`：码点 → UTF-8 字节；非法码点 → U+FFFD（与 stdlib 同口径）。
static std::vector<uint8_t> elench_utf8(uint64_t cp) {
    std::vector<uint8_t> out;
    if (cp <= 0x7F) {
        out.push_back(static_cast<uint8_t>(cp));
    } else if (cp <= 0x7FF) {
        out.push_back(static_cast<uint8_t>(0xC0 | (cp >> 6)));
        out.push_back(static_cast<uint8_t>(0x80 | (cp & 0x3F)));
    } else if (cp <= 0xFFFF && !(cp >= 0xD800 && cp <= 0xDFFF)) {
        out.push_back(static_cast<uint8_t>(0xE0 | (cp >> 12)));
        out.push_back(static_cast<uint8_t>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<uint8_t>(0x80 | (cp & 0x3F)));
    } else if (cp <= 0x10FFFF) {
        out.push_back(static_cast<uint8_t>(0xF0 | (cp >> 18)));
        out.push_back(static_cast<uint8_t>(0x80 | ((cp >> 12) & 0x3F)));
        out.push_back(static_cast<uint8_t>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<uint8_t>(0x80 | (cp & 0x3F)));
    } else {
        out = elench_utf8(0xFFFD);
    }
    return out;
}

// `map.keys()` / `map.values()`：C++ 的 map 没有这两个方法，用快照代替。
template <class M>
static std::vector<typename M::key_type> elench_keys(const M& m) {
    std::vector<typename M::key_type> r;
    r.reserve(m.size());
    for (const auto& kv : m) r.push_back(kv.first);
    return r;
}

template <class M>
static std::vector<typename M::mapped_type> elench_values(const M& m) {
    std::vector<typename M::mapped_type> r;
    r.reserve(m.size());
    for (const auto& kv : m) r.push_back(kv.second);
    return r;
}

// `set.values()`：**元素本身**（interp 的 `SetVal.values()` 就是键序）。
// 不能复用 `elench_keys`——它遍历的是 `pair.first`，对 `std::set<T>`（元素不是
// pair）会编不过；也不能用 `elench_values`（它要 `M::mapped_type`）。
template <class S>
static std::vector<typename S::value_type> elench_elems(const S& s) {
    std::vector<typename S::value_type> r;
    r.reserve(s.size());
    for (const auto& x : s) r.push_back(x);
    return r;
}

// ---- `insert` / `put`：**按容器语义**发射（2026-11-13，L0：insert 曾按向量硬套）
//
// 同一个 `.insert` 在本语言里有多套语义，`interp.py` 是定义：
//   · `map.insert(k, v)`           **不覆盖**已有键；返回 1（插入）/ 0（键已存在）
//   · `unordered_map.insert(k, v)` **覆盖**；返回 0（适配器 `elench_umap` 自己实现）
//   · `set.insert(v)`              **插元素**；返回 0（不管原本在不在）
//   · `vector.insert(pos, v)`      **位置插入**（发射成 `.insert(begin()+pos, v)`）
// 发射器按接收者的**静态类型**选其中一条；判不出类型就报 E-Cxx-Unsupported，
// 绝不按"默认是 vector"硬套（旧实现就是这样把 `insert(k, v)` 发成位置插入的）。
template <class M, class K, class V>
static std::size_t elench_map_insert(M& m, const K& k, const V& v) {
    return m.insert(typename M::value_type(k, v)).second ? 1 : 0;
}

// `map.put(k, v)`：**覆盖写**，返回 1 = 新插入、0 = 覆盖（同 interp 的 MapVal.put）。
template <class M, class K, class V>
static std::size_t elench_map_put(M& m, const K& k, const V& v) {
    auto it = m.find(k);
    if (it != m.end()) { it->second = v; return 0; }
    m.insert(typename M::value_type(k, v));
    return 1;
}

// `set.insert(v)`：解释器的 SetVal / UnorderedSetVal `.insert` **恒返回 0**
// （不看元素原本在不在；`std::set::insert` 返回 pair，不能直接当 u64 用）。
template <class S, class V>
static std::size_t elench_set_insert(S& s, const V& v) {
    s.insert(v);
    return 0;
}

template <class T>
static void elench_out(const T& v) {
    if constexpr (std::is_same_v<T, std::vector<uint8_t>>) {
        std::cout.write(reinterpret_cast<const char*>(v.data()),
                        static_cast<std::streamsize>(v.size()));
    } else if constexpr (std::is_same_v<T, bool>) {
        std::cout << (v ? "True" : "False");
    } else if constexpr (std::is_same_v<T, char>) {
        std::cout << v;
    } else if constexpr (std::is_same_v<T, signed char> || std::is_same_v<T, unsigned char>) {
        std::cout << static_cast<int>(v);
    } else if constexpr (std::is_floating_point_v<T>) {
        elench_out(elench_repr(static_cast<double>(v)));
    } else {
        std::cout << v;
    }
}

template <class T>
static void elench_outln(const T& v) {
    elench_out(v);
    std::cout << "\n";
}
"""


def _cpp_literal_bytes(data: bytes) -> str:
    """字节串 → C++ 字符串字面量。

    非 ASCII / 控制字节一律写**三位八进制**：`\\x` 会把后面紧跟的十六进制
    字符一起吃进去（`"\\x41BC"` 是一个字节不是两个），八进制最多吃 3 位，
    所以安全。用 `elench_str("…")` 变成 `std::vector<uint8_t>`，与
    `vector<u8>` 的 C++ 表示一致。
    """
    body = []
    for b in data:
        if b == 0x0A:
            body.append("\\n")
        elif b == 0x0D:
            body.append("\\r")
        elif b == 0x09:
            body.append("\\t")
        elif b == 0x5C:
            body.append("\\\\")
        elif b == 0x22:
            body.append("\\\"")
        elif 0x20 <= b <= 0x7E:
            body.append(chr(b))
        else:
            body.append("\\%03o" % b)
    return '"' + "".join(body) + '"'


def _cpp_char_literal(text: str) -> str:
    """`lit-char` 的值就是**带引号的 token 文本**（`','` / `'\\n'`）。

    只放行"一个字符或一个 C 风格转义"的形状：认不出就报错，不猜。
    """
    t = (text or "").strip()
    if len(t) >= 3 and t[0] == "'" and t[-1] == "'":
        inner = t[1:-1]
    else:
        inner = ""
    if len(inner) == 1 and inner not in ("'", "\\"):
        return "'" + inner + "'"
    if len(inner) == 2 and inner[0] == "\\" and inner[1] in "0nrt\\'\"abfv":
        return "'" + inner + "'"
    if len(inner) in (3, 4) and inner.startswith("\\x") and all(
            c in "0123456789abcdefABCDEF" for c in inner[2:]):
        return "'" + inner + "'"
    raise CppUnsupported(f"字符字面量 {text!r} 认不出（只支持单字符与 C 风格转义）")


#: `format` / `print` 格式洞的**全集**（`stdlib.format` 的文档句里那几种里，
#: 语料实际用到的四种 + 默认洞）。别的写法一律报错：猜一个格式就是生成错值。
_CPP_FORMAT_HOLES = {
    "{}": "elench_text",
    "{:u}": "elench_u",
    "{:d}": "elench_d",
    "{:s}": "elench_s",
    "{:f}": "elench_f",
}


def _cpp_format_expr(fmt: bytes, arg_exprs: List[str]) -> str:
    """把 `format("…", a, b)` 折成 `elench_join({elench_str(…), elench_u(a), …})`。

    格式串**必须是字面量**（调用方已经检查过）：运行期拼出来的串没法在生成期
    确定洞的个数与类型，猜就是错值。
    """
    parts: List[str] = []
    i, start, used = 0, 0, 0
    while i < len(fmt):
        if fmt[i:i + 1] == b"{":
            j = fmt.find(b"}", i)
            if j < 0:
                raise CppUnsupported(f"格式串 {fmt!r} 里的 `{{` 没有闭合")
            hole = fmt[i:j + 1].decode("utf-8", "replace")
            fn = _CPP_FORMAT_HOLES.get(hole)
            if fn is None:
                raise CppUnsupported(
                    f"格式洞 `{hole}` 还没支持（支持：{', '.join(_CPP_FORMAT_HOLES)}）")
            if used >= len(arg_exprs):
                raise CppUnsupported(
                    f"格式串 {fmt!r} 的洞比实参多（第 {used + 1} 个洞没值）")
            if i > start:
                parts.append("elench_str(" + _cpp_literal_bytes(fmt[start:i]) + ")")
            parts.append(f"{fn}({arg_exprs[used]})")
            used += 1
            i = j + 1
            start = i
        elif fmt[i:i + 1] == b"}":
            raise CppUnsupported(f"格式串 {fmt!r} 里有没配对的 `}}`（要写 `{{}}`）")
        else:
            i += 1
    if used != len(arg_exprs):
        raise CppUnsupported(
            f"格式串 {fmt!r} 只有 {used} 个洞，却给了 {len(arg_exprs)} 个实参")
    if start < len(fmt):
        parts.append("elench_str(" + _cpp_literal_bytes(fmt[start:]) + ")")
    if not parts:
        return 'elench_str("")'
    if len(parts) == 1:
        return parts[0]
    return "elench_join({" + ", ".join(parts) + "})"


_METHOD_MAP = {
    "length": "size",          # 弃用别名（C++ 叫 size）
    "size": "size",
    "empty": "empty",
    "is_empty": "empty",
    "clear": "clear",
    "push": "push_back",       # 弃用别名
    "push_back": "push_back",
    "assign": "assign",
    "at": "at",
    "get": "at",               # 解释器：键不存在就报错 ⇒ C++ 的 at 同形
}

# ---------------------------------------------------------------- 容器方法审计
#
# 2026-11-13：`insert` 曾按**向量语义**无条件发射（`m.insert(m.begin()+(97), 11)`），
# 而 `map`/`unordered_map` 的 `insert(k, v)` 是**插键值对**——用户写得出来、
# 编译失败的形状。根因是 `_METHOD_MAP` 只按**方法名**映射、从不看接收者类型。
#
# 语义**以 `interp.py` 的 `_method` 为准**（它是语言的定义）。逐方法核对结果
# （`✓` = 发射正确；`✗` = 曾经错、本轮修；`⛔` = 没实现，但**明确报错**；
#   `—` = 该容器没有这个方法，`check` 期就按 METHOD_TABLE 拒绝）：
#
#   size()/length()   所有容器 → u64                          .size()              ✓
#   empty()/is_empty() 所有容器 → bool                         .empty()             ✓
#   clear()           vector/map/set/unordered_map             .clear()             ✓
#                     array                                   （std::array 定长）   ✗ 生成期报错
#   at(i)             vector/array                              .at(i)              ✓
#                     map（METHOD_TABLE 无 at，用 get）          —                  —
#   get(k)            map/unordered_map（缺键 = 报错）          .at(k)              ✓
#   put(k,v)          map（覆盖写，返回"是否新插入"）            elench_map_put      ✗ 本轮补
#                     其余容器（interp 无 put，check 拒绝）      —                  —
#   insert(k,v)       map（不覆盖，返回 1/0）                    elench_map_insert   ✗ 本轮修
#                     unordered_map（覆盖，返回 0）              .insert(k,v)(适配器) ✗ 本轮修
#                     set/unordered_set（插元素，返回 0）        elench_set_insert   ✗ 本轮修
#                     vector/string（insert(pos,value)）          .insert(begin()+p,v) ✓ 保持
#                     array                                   （std::array 无 insert）✗ 生成期报错
#   keys()            map（键序）/unordered_map（插入序）        elench_keys         ✓
#                     set（interp 报错）                        —                   —
#   values()          unordered_map → 值                        elench_values       ✓
#                     set → 元素本身（键序）                     elench_elems        ✗ 本轮修
#                       （旧版发 elench_values，要 M::mapped_type，std::set 编不过；
#                         elench_keys 取 pair.first，对 set 也编不过）
#                     map（METHOD_TABLE/interp 都没有 values）    —                  —
#   push_back()/push  vector（追加）                           .push_back          ✓
#                     array（std::array 定长）                   —                  ✗ 生成期报错
#   pop()             vector/array（返回元素、删末尾）           ⛔ 未实现
#                      (C++ pop_back 返 void；要 elench_pop 包"先 back 再 pop_back"）
#   find(v)           vector（返回下标 / npos）                 ⛔ 未实现
#   erase(pos)        vector/array（按下标删）                  ⛔ 未实现
#   erase(k)/remove(k) map/set/unordered（返回 1/0）            ⛔ 未实现
#                      (std::map/std::set 的 erase(k) 正好返回 0/1，可发；本轮没做)
#   contains(k)       map/set/unordered（返回 bool）            ⛔ 未实现
#   back()/front()    vector/array                              ⛔ 未实现
#   assign(n,x)       vector（**只能作语句**，返回 void）        .assign(n,x)        ✓
#                     值位置（`T ys = xs.assign(…)`）               ⛔ 生成期报错
#                     （C++ 的 assign 返回 void；check 侧也报 E-Type-Assign「初值是 void」）
#                     array（METHOD_TABLE 无 assign）             —                  —
#   c_str()           vector<u8>（同一段字节）                   同一个值            ✓
#   fill              ——不是方法（`fill` 是自由内建；check 拒绝 `v.fill(…)`）
#   resize            ——不是方法（METHOD_TABLE / interp 都没有）
#
# ⛔ 的每一行都是**明确报错** `E-Cxx-Unsupported`（不是发错的代码）；
# ✗ 生成期报错的 array 行，是因为 `std::array` 里根本不存在那些成员——
# 与其让用户看一句 `no member named 'assign'`，不如在生成期说清"定长容器不能改长度"。
#
# 不在表里的容器：`unordered_set<T>`（`cxx_map.UNMAPPED_TYPES` 故意不映射，到不了
# 发射器；语义同 set）、`stack` / `queue`（同上，语言层未接线）。它们**不是**漏了，
# 是类型层就明确拒绝。


def _cpp_need_head(recv_text: str, method: str, head) -> str:
    """`insert` / `put` 这类**必须先知道容器种类**的方法：判不出就报错（不猜）。"""
    if head is None:
        raise CppUnsupported(
            f"`{method}(...)` 的接收者类型判不出（`{recv_text}` 不是带声明类型的"
            f"变量/形参/struct 字段/下标）。同一个 `{method}` 在序列与 "
            f"map/set 上的语义完全不同（位置插入 vs 插键值对/元素），"
            f"不按向量硬套。请先把它存进一个**有类型声明**的局部变量再调。")
    return head


def _cpp_method_call(e, ctx, stmt_void=False) -> str:
    """通用 method-call：**按方法名映射**，并对容器语义不同的方法**按类型分派**。

    语料实测：`length` 出现 0 次，`size` / `push_back` 各 100+ 次。以前只认
    `length`，于是"语料里一个 method-call 都不过"。这里逐个方法给**同语义**的
    C++ 写法；认不出的方法仍然报错（不猜成某个同名 C 函数）。

    2026-11-13：`insert` / `put` / `values` / `assign` / `push_back` 会先问
    `_cpp_recv_head` 接收者是什么容器。查不到 ⇒ `None` ⇒ 报错（审计表见
    `_METHOD_MAP` 上面的长注释）。
    """
    recv_node = e.children[0]
    recv = _cpp_expr(recv_node, ctx)
    args = next((c for c in e.children if c.kind == "args"), None)
    arg_nodes = list(args.children if args else [])
    arg_exprs = [_cpp_expr(a, ctx) for a in arg_nodes]
    vals = ", ".join(arg_exprs)
    name = e.name or ""
    nargs = len(arg_exprs)
    # **只有需要区分容器语义的方法才查类型**（其余保持"按名字映射"，不改变既有行为）。
    head = (_cpp_recv_head(recv_node, ctx)
            if name in ("insert", "put", "values", "assign", "push_back", "push", "clear")
            else None)

    # `std::array` **定长**：没有 clear / push_back / assign / insert。
    # 解释器那边 FixedList 的 push/insert 只在容量内允许、assign 直接报错；
    # 生成期就报错，免得把一句看不懂的编译器错误丢给用户。
    if head == "array" and name in ("assign", "push_back", "push", "clear", "insert"):
        raise CppUnsupported(
            f"`array`/`FixedVec` 是**定长**容器，没有 `{name}()`：`std::array` 的"
            f"长度是类型的一部分，既不支持增长/收缩，也没有 `clear`/`assign`。"
            f"要可变长度请改用 `vector`；按位置赋值直接写 `arr[i] = v`。")

    # `assign(n, x)` **返回 void**（C++ 的 `std::vector::assign` 就是 void；
    # `interp.py` / `typecheck.py` 本轮也统一成 void）。所以它只能在**语句
    # 位置**发射（`xs.assign(n, x);` 是合法的 discarded-value expression），
    # 一旦出现在值位置（声明初值 / 实参 / return / 运算数），发出去就是
    # `conversion from 'void' to non-scalar type`——那是"能出码但编不过"，
    # 比明确报错更糟。与 `pop` / `find` / `erase` 等 ⛔ 行同一条口径：
    # 生成期报 `E-Cxx-Unsupported`，不发一个编不过的表达式。
    if name == "assign" and not stmt_void:
        raise CppUnsupported(
            f"`assign(n, x)` 返回 void（C++ 的 `std::vector::assign` 如此），"
            f"只能作**语句**：`{recv}.assign({vals});`——不能拿它的值。"
            f"要保留结果先 assign 再读同一个变量："
            f"`{recv}.assign({vals});` 之后用 `{recv}`。")

    target = _METHOD_MAP.get(name)
    if target is not None:
        return f"{recv}.{target}({vals})"
    if name in ("keys", "values"):
        # `set.values()` 是**元素序**（interp 的 `SetVal.values()` 就是键），
        # 而 `elench_keys` 取 `pair.first`、`elench_values` 要 `M::mapped_type`
        # ⇒ 两者对 `std::set<T>` 都编不过（2026-11-13 实测）。
        if head in _CPP_SET_HEADS:
            return f"elench_elems({recv})"
        return f"elench_{name}({recv})"
    if name == "c_str":
        # 本语言没有指针型字符串：`vector<u8>` 自身就是要递出去的那段字节，
        # 所以 `c_str()` 是**同一个值**（interp 的 `c_str` 分支同口径）。
        if vals:
            raise CppUnsupported("c_str 不收实参")
        return recv
    if name == "insert":
        head = _cpp_need_head(recv, "insert", head)
        if head == "map":
            # **不覆盖**已有键、返回 1/0（interp 的 MapVal 分支，审计 A2）。
            if nargs != 2:
                raise CppUnsupported(
                    f"`map.insert` 要两个实参（键, 值），给了 {nargs} 个")
            return f"elench_map_insert({recv}, {arg_exprs[0]}, {arg_exprs[1]})"
        if head == "unordered_map":
            # 适配器自己的 `insert(k, v)`：**覆盖**、返回 0（同 interp 的
            # UnorderedMapVal 分支）。注意它与有序 `map` 的语义**本来就不同**，
            # 这是解释器的定义，发射器照抄、不"统一"。
            if nargs != 2:
                raise CppUnsupported(
                    f"`unordered_map.insert` 要两个实参（键, 值），给了 {nargs} 个")
            return f"{recv}.insert({arg_exprs[0]}, {arg_exprs[1]})"
        if head in _CPP_SET_HEADS:
            # **插元素**、恒返回 0（interp 的 SetVal / UnorderedSetVal 分支）。
            if nargs != 1:
                raise CppUnsupported(
                    f"`{head}.insert` 要一个实参（元素），给了 {nargs} 个")
            return f"elench_set_insert({recv}, {arg_exprs[0]})"
        if head in _CPP_SEQ_HEADS:
            # `v.insert(pos, value)`：C++ 是 `insert(begin()+pos, value)`。
            # 只认两参：解释器对 `insert(pos, n, v)` 会**静默丢掉第三个实参**
            # （interp.py 的 `_method` 只读 args[0]/args[1]），所以不发射那种形状。
            if nargs != 2:
                raise CppUnsupported(
                    f"序列的 `insert` 只认 `insert(位置, 值)` 两个实参，给了 "
                    f"{nargs} 个（`insert(pos, n, v)` 在解释器里会静默丢掉 n，"
                    f"不发射那个形状）")
            return f"{recv}.insert({recv}.begin() + ({arg_exprs[0]}), {arg_exprs[1]})"
        raise CppUnsupported(f"`insert` 在 `{head}` 上没有可发射的语义")
    if name == "put":
        # `m.put(k, v)`：**覆盖写**、返回"是否新插入"（interp 的 MapVal.put）。
        head = _cpp_need_head(recv, "put", head)
        if head != "map":
            raise CppUnsupported(
                f"`put` 只在有序 `map` 上有定义（解释器对 `{head}` 没有 put）")
        if nargs != 2:
            raise CppUnsupported(f"`map.put` 要两个实参（键, 值），给了 {nargs} 个")
        return f"elench_map_put({recv}, {arg_exprs[0]}, {arg_exprs[1]})"
    raise CppUnsupported(f"方法 `{name}()` 还没支持（不猜成同名函数）")


def _cpp_call(e, ctx) -> str:
    args = next((c for c in e.children if c.kind == "args"), None)
    exprs = [_cpp_expr(a, ctx) for a in (args.children if args else [])]
    short = (e.name or "").split(".")[-1]
    if not short:
        # 空名 `call`：`vector<T>::filled(v, n)`（`--compat` 的 C++ 直构形状）。
        callee = next((c for c in e.children if c.kind == "field"), None)
        if callee is None or getattr(callee, "name", "") != "filled":
            raise CppUnsupported("空名构造只认 `vector<T>::filled(v, n)`")
        base = callee.children[0] if callee.children else None
        cty = ctx.type_of(_type_str(base))
        if len(exprs) != 2:
            raise CppUnsupported("filled 需要两个实参（值, 个数）")
        return f"{cty}({exprs[1]}, {exprs[0]})"
    # **外部函数的容器实参**（2026-11-14）：`ffi_c` 把 `vector` / `array`
    # 编组成 `const char*`，而 C++ 的 `std::vector` **不会隐式转成指针**——
    # 不发 `.data()` 的话，`extern XXH64(const vector<u8>&, …)` 明明声明对了，
    # 调用点仍然编不过（这正是"生成了代码"与"能接真库"之间的最后一段）。
    _flags = getattr(ctx, "extern_params", {}).get(short)
    if _flags is not None:
        if len(_flags) != len(exprs):
            raise CppUnsupported(
                f"调用外部函数 `{short}` 给了 {len(exprs)} 个实参，声明是 "
                f"{len(_flags)} 个——**不猜**（实参类型/个数要能被证出来）")
        exprs = [f"reinterpret_cast<const char*>({x}.data())" if f else x
                 for x, f in zip(exprs, _flags)]
    vals = ", ".join(exprs)
    if short in ("print", "write"):
        raise CppUnsupported(f"`{short}` 只能作语句（C++ 里是流写入，没有返回值）")
    if short == "format":
        if not exprs or (args.children[0].kind != "lit-string"):
            raise CppUnsupported("format 的格式串必须是字符串字面量（运行期拼的串不猜格式）")
        return _cpp_format_expr(args.children[0].value or b"", exprs[1:])
    if short == "sum_of":
        # 元素类型拿不到（生成器不做类型推导）：`uint64_t(0)` 对 `vector<u64>`
        # 是精确的；对别的元素类型会编译失败，**不会**静默算错。
        return f"std::accumulate({vals}.begin(), {vals}.end(), uint64_t(0))"
    if short == "utf8_encode":
        return f"elench_utf8({vals})"
    if short in ("min", "max", "abs"):
        return f"std::{short}({vals})"
    # **外部函数返回 `const char*` → `Vec<u8>`**（2026-11-14）：`ffi_c.restype_for`
    # 对 `Vec<u8>` 给的是 `_CString`（NUL 结尾字节），C 源码里的声明就是
    # `const char*`；C++ 的 `std::vector<uint8_t>` **不会**从指针隐式构造，所以
    # `vector<u8> v = cJSON_Version();` 以前编不过（声明对、调用点断）。
    # 这里显式包一层 `elench_cstr(调用, "名字")`：按 NUL 求长、逐字节拷；
    # **NULL ⇒ 明确失败**（stderr + abort，见运行时助手；不折叠成空 vector）。
    # 判据与发声明同源（`_cpp_extern_vec_returns` → `ffi_c.restype_for`），
    # 名字走 `_cpp_literal_bytes` 转义，不给调用名留一个注入位。
    if short in getattr(ctx, "extern_vec_returns", ()):
        _fn_lit = _cpp_literal_bytes(short.encode("utf-8"))
        return f"elench_cstr({short}({vals}), {_fn_lit})"
    return f"{short}({vals})"


def _cpp_is_text(e) -> bool:
    """这个表达式**一定是文本**吗？（只认两种铁证，不做类型推导）

    `lit-string` 与 `format(...)` 在 C++ 侧都是 `std::vector<uint8_t>`；`+` 遇到
    它们就是解释器里的"文本拼接"，要发 `elench_add`。其余形状（`path` / `index`）
    不猜——猜错会把数字加法变成指针/容器运算，正是"生成错代码"。
    """
    if e is None:
        return False
    if getattr(e, "kind", "") == "lit-string":
        return True
    return getattr(e, "kind", "") == "call" and \
        (getattr(e, "name", "") or "").split(".")[-1] == "format"


def _cpp_struct_literal(e, ctx) -> str:
    """`Name{a, b}` → `Name{a, b}`，但**按 struct 的声明顺序**发。

    C++17 的聚合初始化是位置的；源码里如果按别的顺序写字段，照抄就是静默错值。
    """
    name = e.name or ""
    fields = ctx.struct_fields.get(name)
    if fields is None:
        raise CppUnsupported(f"struct `{name}` 不是本模块发射的类型，不知道字段顺序")
    given = {c.name: c.children[0] for c in e.children if c.kind == "field-init"}
    unknown = [k for k in given if k not in fields]
    if unknown:
        raise CppUnsupported(f"struct `{name}` 没有字段 {unknown[0]}")
    missing = [f for f in fields if f not in given]
    if missing:
        raise CppUnsupported(
            f"struct `{name}` 的字面量缺字段 {missing[0]}（C++ 聚合初始化必须给全）")
    return name + "{" + ", ".join(_cpp_expr(given[f], ctx) for f in fields) + "}"


def _cpp_struct_order(structs):
    """struct 之间按"字段类型先定义"排序：C++ 里 `vector<Job>` 要求 Job 已定义。"""
    from ast_util import descendants
    by_name = {s.name: s for s in structs}
    deps = {
        s.name: {c.name for f in (s.children or []) if f.kind == "field"
                 for c in descendants(f, "type-name") if c.name in by_name}
        for s in structs
    }
    out, seen = [], set()

    def visit(s):
        if s.name in seen:
            return
        seen.add(s.name)
        for nm in sorted(deps.get(s.name, ())):
            visit(by_name[nm])
        out.append(s)

    for s in structs:
        visit(s)
    return out


def _order_by_calls(fns):
    """把被调函数排在调用者之前（C++ 模板要求定义先于使用）。

    只在存在泛型函数时调用——普通代码保持源码顺序，避免改变既有输出。
    """
    from ast_util import descendants
    name_of = {id(f): getattr(f, "name", "") for f in fns}
    deps = {name_of[id(f)]: {getattr(c, "name", "") for c in descendants(f, "call")}
            for f in fns}
    out, seen = [], set()

    def visit(f):
        nm = name_of[id(f)]
        if nm in seen:
            return
        seen.add(nm)
        for g in fns:
            gn = name_of[id(g)]
            if gn != nm and gn in deps.get(nm, set()):
                visit(g)
        out.append(f)

    for f in fns:
        visit(f)
    return out


#: `ffi_c` 的编组决定 → **C 源码里写的类型名**。
#:
#: **这不是第二张 `.tu` 类型表**：`.tu` 类型该映射成什么 C ABI，判据只在
#: `ffi_c`（`argtype_for` / `restype_for`）；这张表只把它的返回值（ctypes 类型）
#: 翻译成 C 声明里能写的拼法。`ffi_c` 以后扩了新类型，往这里补一行即可；
#: 补不上就报错，**绝不猜成某个整数类型**（猜错 = 按错的 ABI 调真库）。
_FFI_C_SPELLING = {
    "c_uint64": "uint64_t",
    "c_int64": "int64_t",
    "c_char_p": "const char*",
    "c_void_p": "void*",
    "c_bool": "bool",
    "c_byte": "signed char",
    "c_ubyte": "unsigned char",
    "c_short": "short",
    "c_ushort": "unsigned short",
    "c_int": "int",
    "c_uint": "unsigned int",
    "c_long": "long",
    "c_ulong": "unsigned long",
    "c_longlong": "long long",
    "c_ulonglong": "unsigned long long",
    "c_size_t": "size_t",
    "c_ssize_t": "ptrdiff_t",
    "c_float": "float",
    "c_double": "double",
}


def _ffi_c_spelling(ty: str, is_return: bool) -> str:
    """`.tu` 类型 → C 源码里的类型名；判据来自 `ffi_c`（见 `_FFI_C_SPELLING`）。

    认不出就抛 `CppUnsupported`：发一个错的 `extern "C"` 比不发更危险——
    它会**编得过**，然后按错的 ABI 调真库（静默错值），而那正是这一层要防的。

    **先按 ctypes 类型对象同一性判**（`ctypes.c_uint64 is ctypes.c_ulonglong`
    在 Windows 上为真——`__name__` 是 `c_ulonglong`，直接查名字表会得到
    `unsigned long long`，那是同一个类型但拼法不干净）。同一性判不到再退回名字表。
    """
    import ctypes as _ct
    import ffi_c as _ffi
    try:
        ct = _ffi.restype_for(ty) if is_return else _ffi.argtype_for(ty)
    except Exception as exc:                            # noqa: BLE001
        _where = "返回类型" if is_return else "形参类型"
        raise CppUnsupported(
            f"extern 的{_where} `{ty or '()'}` 没有 C ABI 映射：{exc}。"
            f"这是后端能力问题（判据在 `ffi_c.py`），不是声明写错了——"
            f"要支持它得扩 `ffi_c`；这里不会静默猜成某个整数类型") from exc
    if ct is None:
        return "void"
    # 同一性别名（Win 上 `c_uint64` 就是 `c_ulonglong`）：按更标准的拼法发。
    for _name, _spelling in (
            ("c_uint64", "uint64_t"), ("c_int64", "int64_t"),
            ("c_char_p", "const char*"), ("c_void_p", "void*"),
            ("c_bool", "bool"), ("c_size_t", "size_t")):
        _obj = getattr(_ct, _name, None)
        if _obj is not None and _obj is ct:
            return _spelling
    # **`Vec<u8>` 的返回**：`ffi_c` 用私有的 `_CString`（`c_void_p` 子类，
    # 收完返回值再按 NUL 读成字节）——C 源码里就是 `const char*`。
    if ct is getattr(_ffi, "_CString", None) or getattr(ct, "__name__", "") == "_CString":
        return "const char*"
    spelling = _FFI_C_SPELLING.get(getattr(ct, "__name__", ""))
    if spelling is None:
        raise CppUnsupported(
            f"extern 的 `{ty}` 在 `ffi_c` 里映射成 {getattr(ct, '__name__', ct)!r}，"
            f"但 `dsh` 不知道它在 C 源码里怎么拼——把拼法补进 `_FFI_C_SPELLING`")
    return spelling


def _ffi_c_ret_is_cstr(ty: str) -> bool:
    """`ffi_c` 把这个返回类型编成「指针 → NUL 结尾字节」了吗（`_CString`）？

    判据**只在 `ffi_c.restype_for`**：这里只回答"它是不是那一个映射"，
    不判断 `Vec<u8>` 的字面写法——`Vec<u8>` / `[u8]` / `array<u8, N>` 谁映射成
    指针是 `ffi_c` 的事，两处各写一套迟早会分叉。`_CString` 是私有类，
    所以按同一性 + 类名兜底（与 `_ffi_c_spelling` 同一条判法）。
    空返回类型（`-> ()`）拿到 `None`，不是 `_CString`。
    """
    import ffi_c as _ffi
    ct = _ffi.restype_for(ty)
    return ct is getattr(_ffi, "_CString", None) or getattr(ct, "__name__", "") == "_CString"


def _cpp_c_param_is_text(ty: str) -> bool:
    """这个形参在 C 里是不是指针型（`ffi_c` 对 `vector` / `array` 编组成 `c_char_p`）。

    判据与 `ffi_c.argtype_for` **同源**（同样的 `canon(head)` 判法）：
    是的话调用侧要写成 `reinterpret_cast<const char*>(x.data())`。
    """
    _head = canon((ty or "").split("<")[0].split("&")[0].strip())
    return _head in ("vector", "array") or (ty or "").startswith("[")


def _cpp_include_line(raw: str) -> str:
    """`--include=` 的实参 → 一行 `#include`（带注入检查）。

    两种写法都收：`--include=xxhash.h`（发射引号形式，配合 `--include-dir=`）
    与 `--include=<xxhash.h>`（原样尖括号形式）。头名里出现引号 / 尖括号 /
    换行就拒绝——否则**一个命令行参数能往生成物里塞任意 C++**。
    """
    h = (raw or "").strip()
    if not h:
        raise ValueError("`--include=` 要一个头文件名（不能是空串）")
    if h.startswith("<") and h.endswith(">"):
        inner = h[1:-1].strip()
        if not inner or any(ch in inner for ch in '"<>\n\r'):
            raise ValueError(f"`--include={raw}` 不是合法的尖括号头名")
        return f"#include <{inner}>"
    if any(ch in h for ch in '"<>\n\r'):
        raise ValueError(
            f"`--include={raw}` 里有引号/尖括号/换行——"
            f"头名写成 `xxhash.h` 或 `<xxhash.h>`")
    return f'#include "{h}"'


def _cpp_extern_found(tu_mod, ch_mod):
    """`.tu` / `.eln` 里声明的 external → `[(名字, [(类型, 形参名)], 返回类型, 库名)]`。

    两种写法认的是**同一件事**（`dsh header` 早就两种都认，`dsh cpp` 以前一种都不认）：

    | 写在哪 | 形状 |
    |---|---|
    | `.eln` 顶层 | `extern XXH64(const vector<u8>&, u64, u64) -> u64 from "xxhash" effects {};` |
    | `.tu` 的 spec | `spec XXH64(...) -> u64 { external "xxhash"; effects {}; }` |

    按出现顺序、**按名字去重**（`.tu` 优先）。这是**唯一一份扫描结果**：
    `_cpp_extern_decls`（发声明）与 `_cpp_extern_vec_returns`（调用点要不要包
    `elench_cstr`）都从它走，不各写一套遍历——两套遍历迟早会对不上。
    """
    def _params_of(node):
        ps = next((c for c in (getattr(node, "children", None) or [])
                   if getattr(c, "kind", "") == "params"), None)
        out = []
        for p in (getattr(ps, "children", None) or []):
            ty = next((c for c in (getattr(p, "children", None) or [])
                       if getattr(c, "kind", "") in ("type-ref", "type-name")), None)
            out.append((_type_str(ty), getattr(p, "name", "") or ""))
        return out

    def _ret_of(node):
        ty = next((c for c in (getattr(node, "children", None) or [])
                   if getattr(c, "kind", "") in ("type-ref", "type-name")), None)
        return _type_str(ty)

    def _lib_of(node):
        v = getattr(node, "value", None)
        lib = v.get("lib") if isinstance(v, dict) else None
        if lib is not None:
            return lib
        body = next((c for c in (getattr(node, "children", None) or [])
                     if getattr(c, "kind", "") == "spec-body"), None)
        for c in (getattr(body, "children", None) or []):
            if getattr(c, "kind", "") == "clause" and (getattr(c, "name", "") or "") == "external":
                cv = getattr(c, "value", None)
                if isinstance(cv, dict) and cv.get("lib") is not None:
                    return cv["lib"]
        return None

    found, seen = [], set()
    for mod in (tu_mod, ch_mod):
        for node in (getattr(mod, "children", None) or []):
            k = getattr(node, "kind", "")
            if k not in ("spec", "extern"):
                continue
            lib = _lib_of(node) if k == "spec" else (
                (node.value or {}).get("lib")
                if isinstance(getattr(node, "value", None), dict) else None)
            if k == "spec" and lib is None:
                continue          # 普通 spec：不是外部实现
            name = getattr(node, "name", "") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            found.append((name, _params_of(node), _ret_of(node), lib))
    return found


def _cpp_extern_decls(tu_mod, ch_mod):
    """外部声明的 `extern "C" …;` 行 + 调用侧实参表（扫描见 `_cpp_extern_found`）。

    返回 `(lines, params)`：

    - `lines`  —— 生成物顶部的声明行（按出现顺序、**按名字去重**；`.tu` 优先）；
    - `params` —— `{名字: [该形参在 C 里是不是指针型]}`，给 `_cpp_call` 做实参转换。

    返回类型**用 `ffi_c` 的映射**（`_ffi_c_spelling`）；认不出就抛
    `CppUnsupported`，不发一个按错 ABI 调用的声明。
    """
    lines, params = [], {}
    for name, ps, ret, lib in _cpp_extern_found(tu_mod, ch_mod):
        cps = [f"{_ffi_c_spelling(ty, False)} {nm}".strip() for ty, nm in ps]
        decl = f'extern "C" {_ffi_c_spelling(ret, True)} {name}({", ".join(cps)});'
        if lib:
            decl += f'  // from "{lib}"'
        lines.append(decl)
        params[name] = [_cpp_c_param_is_text(ty) for ty, _nm in ps]
    return lines, params


def _cpp_extern_vec_returns(tu_mod, ch_mod):
    """返回类型在 `ffi_c` 里是 `_CString`（C 的 `const char*`）的外部函数名集合。

    这些函数在 C 里返回**指针**，而 `.tu` 声明的是 `Vec<u8>`：C++ 的
    `std::vector<uint8_t>` **不会**从 `const char*` 隐式构造，所以调用点必须
    包一层 `elench_cstr(…, "名字")`（否则 `vector<u8> v = cJSON_Version();` 编不过
    ——声明对、调用点断，正是 cJSON 这类 API 的最后一段）。

    判据**只来自 `ffi_c.restype_for`**（与发 `extern "C"` 声明同一张表），
    这里不另写一套 `Vec<u8>` 判断：`ffi_c` 改了映射，两边一起变。
    """
    return {name for name, _ps, ret, _lib in _cpp_extern_found(tu_mod, ch_mod)
            if _ffi_c_ret_is_cstr(ret)}


#: 语言 `main` 的返回类型里，能直接 `(int)` 成 C++ 入口返回值的那些。
#:
#: C++ 规定入口是 `int main()`，所以语言 `main` 的返回值只能 `(int)` 转一次。
#: **只放整数/布尔**：
#:   · `(int)std::vector<u64>` 编不过（`void value not ignored…` 那一类）；
#:   · `(int)double` 能编过但会**悄悄截断**（浮点返回值 → 退出码），那是静默错值。
#: 两者都该在出码期报 `E-Cxx-Unsupported`，而不是发一份编不过 / 静默丢值的生成物。
#: `i32`/`u32` 也放进来：`--compat` 下 C++ 的 `int main()` 落到它们（`int` 在不同
#: 口径里归一成 `i64` 或 `i32`），入口照旧必须是 `int main()`。
_MAIN_RET_CASTABLE = frozenset(
    ("bool", "i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64"))


def _gen_cpp(tu_mod, ch_mod, includes=()) -> str:
    """把模块翻译成 C++17（不支持就抛 `CppUnsupported`）。

    ## 这一轮（2026-11-13）为什么是"成组"

    19 个语料模块的实测：只补语句 kind 仍 0 出码，再补 method-call/field 仍 0，
    直到字面量/构造/printf/位运算**一起**补齐才 19/19。缺任何一个 kind，整个
    模块就生成不出来（`_cpp_expr` 一路抛 `CppUnsupported`）——所以这一节是
    一整套，而不是按命中数一个个加。原则没变：**认不出就报错，不猜错值**。

    ## 第三方库（2026-11-14）

    生成物顶部现在会发两样以前完全没有的东西：

    1. **`extern "C"` 声明**（`_cpp_extern_decls`）：`.eln` 的 `extern …;` 与
       `.tu` 的 `spec … { external "lib"; }` 都会发射。以前只有 `dsh header`
       发，`dsh cpp` 一条都不发 ⇒ 生成物里调真库函数时 g++ 报
       `'XXH64' was not declared in this scope`。
    2. **第三方头**（`includes`，来自 `--include=`）：放在 `_CPP_HEADERS` 之后、
       一切代码之前。**只能从命令行来**——`.eln`/`.tu` 里的非标准 `#include`
       在词法层就被拒绝（`E-Lex-IncludeUnresolved`），那是有意的：
       "接受并忽略"会让 check 看起来通过而符号一个都不可用。

    `includes` 是**已经校验过**的 `#include` 行（见 `_cpp_include_line`）。
    """
    out: List[str] = []
    out.append("// 由 elench cpp 生成 —— 语义见 .tu；不支持就报错，不猜")
    for _h in _CPP_HEADERS:
        out.append(f"#include <{_h}>")
    # **第三方头**：标准头之后、代码之前（含 `using namespace std;` 与运行时）。
    _incs = [ln for ln in (includes or []) if ln]
    if _incs:
        out.append("")
        out.append('// ---- 第三方头：来自 `dsh cpp --include=…`（标准头之后）----')
        out.extend(_incs)
    # **外部符号声明**：C 库就是这个 ABI（`extern "C"` 关掉 C++ 的名字修饰）。
    _ext_lines, _ext_params = _cpp_extern_decls(tu_mod, ch_mod)
    if _ext_lines:
        out.append("")
        out.append('// ---- 外部符号（C ABI）：来自 .tu 的 `external "lib";` / .eln 的 `extern …;` ----')
        out.extend(_ext_lines)
    out.append("using namespace std;")
    out.append("")
    out.extend(_CPP_RUNTIME.strip("\n").splitlines())
    out.append("")

    # ---- 本模块定义的 struct：先登记再发射 --------------------------------
    # 只登记 `.eln`（实现文件）里的 struct：它们是本文件真的会写出定义的
    # 类型。`.tu` 里的 struct 是契约，看不到字段，登记了就是猜。
    ctx = _CppCtx()
    ctx.extern_params.update(_ext_params)
    # 返回 `const char*` 的外部函数：调用点要包 `elench_cstr`（判据见该函数）。
    ctx.extern_vec_returns.update(_cpp_extern_vec_returns(tu_mod, ch_mod))
    structs = [c for c in (ch_mod.children or []) if getattr(c, "kind", "") == "struct"]
    for st in structs:
        ctx.struct_fields[st.name] = [f.name for f in (st.children or [])
                                      if getattr(f, "kind", "") == "field"]
        ctx.user_types.add(st.name)
        # 字段的**声明类型**也登记：`s.v.insert(...)` 这类形状要按字段类型分派
        # （判不出就报错，不按向量硬套）。
        for f in (st.children or []):
            if getattr(f, "kind", "") != "field":
                continue
            _fty = next((c for c in (f.children or [])
                         if c.kind in ("type-ref", "type-name")), None)
            if _fty is not None:
                ctx.struct_field_types[(st.name, f.name)] = _type_str(_fty)
    for st in _cpp_struct_order(structs):
        out.append(f"struct {st.name} {{")
        for f in (st.children or []):
            if getattr(f, "kind", "") != "field":
                continue
            ty = next((c for c in (f.children or [])
                       if c.kind in ("type-ref", "type-name")), None)
            tstr = _type_str(ty)
            if not tstr.strip():
                raise CppUnsupported(f"struct `{st.name}` 的字段 `{f.name}` 没有类型")
            init = next((c for c in reversed(f.children or [])
                         if c.kind not in ("type-ref", "type-name")), None)
            tail = "" if init is None else " = " + _cpp_expr(init, ctx)
            out.append(f"    {ctx.type_of(tstr)} {f.name}{tail};")
        out.append("};")
        out.append("")

    fns = [c for c in (ch_mod.children or []) if getattr(c, "kind", "") == "fn"]
    # **始终按调用关系排序**：C++ 要求"定义先于使用"，而语料里 `main` 调
    # 后面定义的函数是常态。排序只在有依赖时改变顺序（其余保持源码顺序）。
    fns = _order_by_calls(fns)
    # **语言里的 `main` 不能直接映射成 C++ 的 `main`**：C++ 规定入口是 `int main()`，
    # 而我们允许 `func main() -> u64`，直译出来是 `uint64_t main()`——**连编译都过不了**
    # （g++ 报 `::main must return int`）。所以改名成 `elench_main`，末尾补一个合法入口。
    need_wrapper = False
    wrapper_noargs = True
    wrapper_void = False
    #: 语言 `main` 的返回类型（`.eln` 原文，未翻译成 C++）：决定入口怎么发。
    wrapper_ret = ""
    #: 同一类型的 C++ 写法：只用于报错时说清"编不过的是这个"。
    wrapper_cxx = ""

    for fn in fns:
        # 每个函数一份干净的**类型环境**：形参在下面登记，函数体内由
        # `_cpp_stmt` 的 var-decl 分支登记（块/分支各自 push/pop）。
        ctx.clear_scopes()
        _tparams = {c2.name for c in (fn.children or [])
                    if getattr(c, "kind", "") == "type-params"
                    for c2 in (c.children or [])}
        if _tparams:
            out.append("template <" + ", ".join(
                f"typename {n}" for n in sorted(_tparams)) + ">")
        ps = next((c for c in fn.children if c.kind == "params"), None)
        decls = []
        for prm in (ps.children if ps else []):
            ty = next((c for c in prm.children if c.kind in ("type-ref", "type-name")), None)
            ts = _type_str(ty)
            # **空类型只在返回位置能解释成 void**；形参/变量位置没有这个
            # "unit" 写法，空就是解析/类型推断失败——报错，不猜。
            if not ts.strip():
                raise CppUnsupported(
                    f"函数 `{fn.name}` 的形参 `{prm.name}` 类型是空的（`?`）——"
                    f"只有**返回位置**的 `()` 能发射 void，这里不猜")
            # **引用与 const 都按 `.eln` 原样映射**（2026-11-13，L0 止血）：
            # 旧实现给**所有** vector 形参加 `const ...&`，于是
            # `u64 f(vector<u64>& xs) { xs[0] = 1; }` 生成只读引用，g++ 报
            # `assignment of read-only location`。const 是**形参节点**上的标记
            # （`param.value = {"const": True}`），不在类型节点里。
            _ref = (getattr(ty, "kind", "") == "type-ref") or ts.endswith("&")
            _base = ts[:-1].strip() if ts.endswith("&") else ts
            _head = _base.split("<")[0].strip()
            # 类型参数原样输出（`T` 就是合法的 C++ 标识符）。
            # `vector<T>` 这类嵌套泛型本轮仍不支持——**报错，不退化**。
            cty = _base if _head in _tparams else ctx.type_of(_base)
            if _ref:
                _is_const = (isinstance(getattr(prm, "value", None), dict)
                             and bool(prm.value.get("const")))
                cty = ("const " if _is_const else "") + cty + "&"
            decls.append(f"{cty} {prm.name}")
            # 形参的声明类型进类型环境：`insert` 要靠它决定语义。
            ctx.bind(prm.name, _base)
        rt = next((c for c in fn.children if c.kind in ("type-ref", "type-name")), None)
        _rts = _type_str(rt)
        if not _rts.strip():
            # **返回位置的 `()`/空 ⇒ void**（L1 口径）：`.eln` 的 `-> ()` 与
            # "类型推断失败"在 AST 里同形，只有位置能分清；形参位置照样报错。
            rts, _is_void = "void", True
        else:
            rts, _is_void = _rts if _rts.split("<")[0].strip() in _tparams \
                else ctx.type_of(_rts), False
        cname = fn.name
        if cname == "main":
            cname = "elench_main"
            need_wrapper = True
            wrapper_noargs = wrapper_noargs and not decls
            wrapper_void = _is_void
            wrapper_ret = _rts
            wrapper_cxx = rts
        out.append(f"{rts} {cname}({', '.join(decls)}) {{")
        body = next((c for c in fn.children if c.kind == "block"), None)
        for st in (body.children if body else []):
            for line in _cpp_stmt(st, "    ", ctx):
                out.append(line)
        out.append("}")
        out.append("")
    if need_wrapper and wrapper_noargs:
        out.append("// C++ 入口：`int main()` 是规定，语言里的 `main` 已改名为 `elench_main`")
        if wrapper_void:
            # **`() main()` / `void main()`：`elench_main` 是 void**。
            # 发 `return (int)elench_main();` 会被 g++ 拒（`void value not ignored
            # as it ought to be`）——这里必须调完就 `return 0`。
            out.append("int main() { elench_main(); return 0; }")
        else:
            # **返回值当退出码**：只有整数/布尔能 `(int)` 一次。
            # 其余（`vector<u64>` / `f64` / struct…）**不发编不过的东西**：
            # 出码期就报 `E-Cxx-Unsupported`，别等g++（或更糟：浮点静默截断）。
            _head = canon(wrapper_ret.split("<")[0].split("&")[0].strip())
            if _head not in _MAIN_RET_CASTABLE:
                raise CppUnsupported(
                    f"语言里的 `main` 返回 `{wrapper_ret}`（C++ 里是 `{wrapper_cxx}`），"
                    "但 C++ 规定入口是 `int main()`：返回值只能 `(int)` 转一次。"
                    "`vector<…>` / struct / 浮点这类返回值要么编不过、要么被静默截断，"
                    "所以**不出码**。改成返回 `u64` / `i64` / `bool` 当退出码，"
                    "或改成 `() main()` 不返回值。")
            out.append("int main() { return (int)elench_main(); }")
        out.append("")
    elif need_wrapper:
        out.append("// 语言里的 `main` 带参数，不生成 C++ 入口（无法在无参入口里调用）")
        out.append("")
    return "\n".join(out) + "\n"


#: 复合赋值的全集（语料实测只出现 `+=` / `-=`，但 C++ 这 10 个都是**同一个语义**，
#: 逐个放行不需要推理：`x op= y` 就是 `x = x op y`，与解释器的 desugar 一致）。
_CPP_ASSIGN_OPS = frozenset(
    ("+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="))


def _cpp_print_stmt(call, ind: str, ctx) -> List[str]:
    """`print` / `write` **作语句**发射（它们在 C++ 里是流写入，没有返回值）。

    - `print(x)` → `elench_outln(x)`（带换行）；`write(x)` → `elench_out(x)`；
    - 第一个实参是含 `{` 的**字面量格式串**时按 `format` 折（`printf` 族走
      的也是这条：compat 把 `%d` 改写成 `{}`）；
    - 多个非格式实参按解释器的 `" ".join` 口径用空格分隔。
    """
    short = (call.name or "").split(".")[-1]
    args = next((c for c in call.children if c.kind == "args"), None)
    exprs = list(args.children if args else [])
    if not exprs:
        raise CppUnsupported(f"`{short}` 至少要 1 个实参")
    first = exprs[0]
    if first.kind == "lit-string" and b"{" in (first.value or b""):
        inner = _cpp_format_expr(first.value or b"",
                                 [_cpp_expr(a, ctx) for a in exprs[1:]])
    elif len(exprs) == 1:
        inner = _cpp_expr(first, ctx)
    else:
        sep = " " if short == "print" else ""
        pieces: List[str] = []
        for i, a in enumerate(exprs):
            if i and sep:
                pieces.append("elench_str(\" \")")
            pieces.append(f"elench_text({_cpp_expr(a, ctx)})")
        inner = "elench_join({" + ", ".join(pieces) + "})"
    fn = "elench_outln" if short == "print" else "elench_out"
    return [f"{ind}{fn}({inner});"]


def _cpp_stmt(st, ind: str, ctx) -> List[str]:
    k = getattr(st, "kind", "")
    if k in ("var-decl", "const-decl"):
        ty = next((c for c in st.children if c.kind in ("type-ref", "type-name")), None)
        tstr = _type_str(ty)
        # 变量位置的**空类型**没有任何合理读法（不像返回位置的 `()` = void）：
        # 猜成 uint64_t 就是 L0 止血封掉的那条路，这里照样报错。
        if not tstr.strip():
            raise CppUnsupported(
                f"变量 `{st.name}` 的类型是空的（`?`）——只有返回位置能发射 void")
        cty = ctx.type_of(tstr)
        if k == "const-decl":
            cty = "const " + cty
        # **rhs 不能排除 `path`**（2026-11-13，L0 止血）：旧实现把 `path` 也滤掉，
        # `u64 j = i;` 于是只剩 None → `_cpp_expr(None)` 兜底成 0（静默错值）。
        # 只排除类型节点；其余形状（path / call / index / binary…）都是初值。
        rhs = next((c for c in reversed(st.children)
                    if c.kind not in ("type-ref", "type-name", "type-args")), None)
        # 登记声明类型（`insert` 一类按接收者类型分派的发射要用）。
        # 只在本函数作用域内有效；同名遮蔽由 `scopes` 栈负责。
        ctx.bind(st.name, tstr)
        if rhs is None:
            # `vector<u64> tmp;` 没有初值：照原样默认构造，不补 `= 0`。
            return [f"{ind}{cty} {st.name};"]
        return [f"{ind}{cty} {st.name} = {_cpp_expr(rhs, ctx)};"]
    if k == "assign":
        return [f"{ind}{_cpp_expr(st.children[0], ctx)} = {_cpp_expr(st.children[1], ctx)};"]
    if k == "assign-op":
        op = st.value or ""
        if op not in _CPP_ASSIGN_OPS:
            raise CppUnsupported(f"复合赋值 `{op}` 还没支持")
        return [f"{ind}{_cpp_expr(st.children[0], ctx)} {op} "
                f"{_cpp_expr(st.children[1], ctx)};"]
    if k == "return":
        return [f"{ind}return {_cpp_expr(st.children[0], ctx)};"] if st.children \
            else [f"{ind}return;"]
    if k == "while":
        cond = st.children[0]
        body = next((c for c in st.children[1:] if c.kind == "block"), None)
        lines = [f"{ind}while ({_cpp_expr(cond, ctx)}) {{"]
        ctx.push_scope()
        for x in (body.children if body else []):
            lines += _cpp_stmt(x, ind + "    ", ctx)
        ctx.pop_scope()
        lines.append(f"{ind}}}")
        return lines
    if k == "if":
        return _cpp_if(st, ind, ctx)
    if k == "for":
        # range-for：语料里 15 个 `for` 全是这种（三段式 `for` 在 parser 里已经
        # 降级成 `block{var-decl; while}`，所以这里只剩 range-for）。绑定类别
        # 来自 `st.ref`：`const` → `const auto&`、`mut` → `auto&`、否则按值拷贝
        # （解释器默认就是**每轮拷贝**，`auto` 正好同语义）。
        it = st.children[0] if st.children else None
        if it is None or not st.name:
            raise CppUnsupported("for 形状认不出（要 `for (x : 容器)`）")
        body = next((c for c in st.children[1:] if c.kind == "block"), None)
        _ref = getattr(st, "ref", None)
        decl = "const auto&" if _ref == "const" else ("auto&" if _ref == "mut" else "auto")
        lines = [f"{ind}for ({decl} {st.name} : {_cpp_expr(it, ctx)}) {{"]
        # 循环变量绑定成**元素类型**（能推就推；推不出就登记成"判不出"，
        # 之后若对循环变量调 `insert` 会报错而不是猜）。
        _ity = _cpp_expr_type(it, ctx)
        _ety = _cpp_elem_type(_ity) if _ity else None
        ctx.push_scope()
        ctx.bind(st.name, _ety or "")
        for x in (body.children if body else []):
            lines += _cpp_stmt(x, ind + "    ", ctx)
        ctx.pop_scope()
        lines.append(f"{ind}}}")
        return lines
    if k == "block":
        # **取全部子语句**：嵌套 `{}` 是作用域（三段式 for 降级后的形状就是
        # `block{var-decl; while}`）。旧实现只认一个子节点，多出来的直接消失。
        lines = [f"{ind}{{"]
        ctx.push_scope()
        for x in (st.children or []):
            lines += _cpp_stmt(x, ind + "    ", ctx)
        ctx.pop_scope()
        lines.append(f"{ind}}}")
        return lines
    if k == "break":
        return [f"{ind}break;"]
    if k == "continue":
        return [f"{ind}continue;"]
    if k == "expr-stmt":
        e = st.children[0] if st.children else None
        if e is not None and getattr(e, "kind", "") == "call" and \
                (e.name or "").split(".")[-1] in ("print", "write"):
            return _cpp_print_stmt(e, ind, ctx)
        # `stmt_void=True`：整条语句的返回值被丢弃，所以 void 方法调用
        # （`assign`）在这一层合法；它的**嵌套位置**仍是值位置（默认 False）。
        return [f"{ind}{_cpp_expr(e, ctx, stmt_void=True)};"]
    raise CppUnsupported(f"语句 {k} 还没支持")


def _cpp_if(st, ind: str, ctx) -> List[str]:
    """`if` / `else` / `else if` **全部**发射（2026-11-13，L0 止血）。

    旧实现 `next(block)` 只取第一个 block、且从不出第二个子节点——`else`
    整个消失：`if (a > b) { r = a; } else { r = b; }` 只剩前半段，
    **能编、能跑、结果错**（比编译失败危险得多）。

    AST 形状（parser 实测）：`children = [条件, then-block]` 或
    `[条件, then-block, else-block | 嵌套 if]`；`else if` 是嵌套的 `if` 节点，
    没有单独的 `else` 标记节点。
    """
    cond = st.children[0]
    rest = list(st.children[1:])
    body = rest[0] if rest else None
    if body is None or getattr(body, "kind", "") != "block":
        raise CppUnsupported("if 的真分支不是 block，本轮不支持")
    else_node = rest[1] if len(rest) > 1 else None
    lines = [f"{ind}if ({_cpp_expr(cond, ctx)}) {{"]
    ctx.push_scope()
    for x in (body.children or []):
        lines += _cpp_stmt(x, ind + "    ", ctx)
    ctx.pop_scope()
    if else_node is None:
        lines.append(f"{ind}}}")
        return lines
    ek = getattr(else_node, "kind", "")
    if ek == "if":
        # `else if`：递归生成整个链，再把首行接在 `} else ` 后面。
        sub = _cpp_if(else_node, ind, ctx)
        lines.append(f"{ind}}} else " + sub[0].lstrip())
        lines += sub[1:]
        return lines
    if ek == "block":
        lines.append(f"{ind}}} else {{")
        ctx.push_scope()
        for x in (else_node.children or []):
            lines += _cpp_stmt(x, ind + "    ", ctx)
        ctx.pop_scope()
        lines.append(f"{ind}}}")
        return lines
    raise CppUnsupported(f"if 的 else 分支形状 {ek} 还没支持")


def _cpp_expr(e, ctx, stmt_void=False) -> str:
    """表达式 → C++ 源码。

    `stmt_void=True` 只由**表达式语句**（`_cpp_stmt` 的 `expr-stmt`）传进来：
    它表示"这个表达式就是整条语句，返回值被丢弃"，所以 `void` 方法调用
    （目前只有 `assign`）在这里是合法的。**嵌套位置一律默认 False**——
    `f(xs.assign(2, 0))` / `T ys = xs.assign(2, 0)` 里的 `assign` 在值位置，
    `_cpp_method_call` 会报 `E-Cxx-Unsupported`，绝不发射编不过的代码。
    """
    if e is None:
        # 不再兜底成 0：表达式缺失说明上游已经搞错了，生成一个 0 只会掩盖它。
        raise CppUnsupported("表达式缺失（AST 里没有这个子节点）")
    k = getattr(e, "kind", "")
    if k == "lit-int":
        return str(int(e.value))
    if k == "lit-float":
        # 值是词法文本（后缀已被 compat 剥掉），`1e16` / `200.0` 都是合法 C++ double。
        return f"({e.value})"
    if k == "lit-bool":
        return "true" if e.value else "false"
    if k == "lit-char":
        return _cpp_char_literal(str(e.value or ""))
    if k == "lit-string":
        # 文本在 C++ 侧是 `std::vector<uint8_t>`（与 cxx_map 对 `string` 的映射一致），
        # 所以字面量也发成同一个类型——`print` / 比较 / 当 map 键都成立。
        return "elench_str(" + _cpp_literal_bytes(e.value or b"") + ")"
    if k == "path":
        return e.name or ""
    if k == "field":
        base = e.children[0] if e.children else None
        if base is not None and getattr(base, "kind", "") in ("type-name", "type-ref"):
            raise CppUnsupported(
                f"`{_type_str(base)}::{e.name}` 只支持构造调用（`filled`），普通取值不支持")
        return f"{_cpp_expr(base, ctx)}.{e.name}"
    if k == "binary":
        # **运算符存在 `value` 里，不是 `name`**（AST 约定）
        op = {"+": "+", "-": "-", "*": "*", "/": "/", "%": "%",
              "<": "<", "<=": "<=", ">": ">", ">=": ">=", "==": "==",
              "!=": "!=", "and": "&&", "or": "||",
              # 2026-11-11：逻辑运算在 AST 里已归一成符号（`and`/`or`/`not` 退役），
              # 这里必须同时认符号，否则 `dsh cpp` 会对所有逻辑表达式报 E-Cxx-Unsupported。
              "&&": "&&", "||": "||",
              # 位运算（2026-11-13）：08_bits_hash / 16_bitset 两个模块整段用到。
              "&": "&", "|": "|", "^": "^", "<<": "<<", ">>": ">>"}.get(e.value or "")
        if op is None:
            raise CppUnsupported(f"运算符 {e.value} 还没支持")
        left, right = e.children[0], e.children[1]
        if op == "+" and (_cpp_is_text(left) or _cpp_is_text(right)):
            # 解释器的 `+` 对两个文本是**拼接**；C++ 的 vector 没有 `+`。
            return f"elench_add({_cpp_expr(left, ctx)}, {_cpp_expr(right, ctx)})"
        return f"({_cpp_expr(left, ctx)} {op} {_cpp_expr(right, ctx)})"
    if k == "ternary":
        # **三目 `c ? a : b`**（2026-11-13）：C++ 的 `?:` 与解释器**同语义**
        # ——条件照布尔求值、只求值一支。外面再套一层括号：三目的优先级比
        # 所有二元运算符都低，不套括号时 `1 + (c ? a : b)` 这类位置要靠
        # 优先级猜回来；套上就与 AST 的形状一一对应（与 `paren` / `unary`
        # 的发射口径一致）。
        #
        # 两支的类型相容由 `typecheck` 保证（`E-Type-CondBranches`），所以
        # C++ 侧的 `?:` 不会做本语言没定义过的隐式转换。
        if len(e.children) < 3:
            raise CppUnsupported("三目 `?:` 缺少分支（AST 里子节点不足 3 个）")
        return (f"({_cpp_expr(e.children[0], ctx)} ? "
                f"{_cpp_expr(e.children[1], ctx)} : "
                f"{_cpp_expr(e.children[2], ctx)})")
    if k == "paren":
        # 括号不改变 void 性：`(xs.assign(2, 0));` 仍是**值被丢弃的语句**，
        # 所以表达式语句的 `stmt_void` 要穿过去（见 `_cpp_method_call`）。
        return f"({_cpp_expr(e.children[0], ctx, stmt_void=stmt_void)})"
    if k == "unary":
        # 一元运算：`-x` / `!x` / `~x`。AST 里 `and`/`or`/`not` 已归一成符号，
        # 所以 `not` 与 `!` 都映射到 `!`（2026-11-11）。
        uop = {"-": "-", "+": "+", "!": "!", "not": "!", "~": "~"}.get(e.value or "")
        if uop is None:
            raise CppUnsupported(f"一元运算符 {e.value} 还没支持")
        return f"({uop}{_cpp_expr(e.children[0], ctx)})"
    if k == "method-call":
        return _cpp_method_call(e, ctx, stmt_void=stmt_void)
    if k == "index":
        return f"{_cpp_expr(e.children[0], ctx)}[{_cpp_expr(e.children[1], ctx)}]"
    if k == "list-init":
        return "{" + ", ".join(_cpp_expr(a, ctx) for a in (e.children or [])) + "}"
    if k == "struct-literal":
        return _cpp_struct_literal(e, ctx)
    if k == "call":
        return _cpp_call(e, ctx)
    raise CppUnsupported(f"表达式 {k} 还没支持")


def _cxx_compiler_argv():
    """找 C++ 编译器（g++ / clang++ / cl），返回 argv 前缀；没有就 `None`。

    `ELENCH_CXX` 是测试/CI 的覆盖点（值按命令行切分，允许带引号的路径）：
    它让「编译自检失败 ⇒ rc≠0」这条路径在**本机没有编译器**时也能被验证，
    不是给生成器加语法。
    """
    raw = (os.environ.get("ELENCH_CXX") or os.environ.get("ELENCH_GXX") or "").strip()
    if raw:
        import shlex
        parts = shlex.split(raw, posix=False)
        cleaned = []
        for p in parts:
            if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'":
                p = p[1:-1]
            if p:
                cleaned.append(p)
        if cleaned:
            return cleaned
    import shutil
    for c in ("g++", "clang++", "cl"):
        p = shutil.which(c)
        if p:
            return [p]
    return None


#: `--link=` 收的链接输入后缀。四种都是「把文件直接递给链接器」：
#: `.o`/`.obj` 是单目标文件，`.a`/`.lib` 是静态（或导入）库。
_CPP_LINK_FILE_EXTS = (".o", ".obj", ".a", ".lib")


def _cpp_link_file(raw: str) -> str:
    """`--link=` 实参 → 绝对路径（**后缀与存在性当场检查**）。

    链接器的报错（`cannot find …` / `undefined reference`）离病因很远：
    "文件根本不存在"在这里拦下，rc=2 的原因就是可读的那一句。
    """
    p = (raw or "").strip()
    if not p:
        raise ValueError("`--link=` 要一个 .o/.obj/.a/.lib 文件（不能是空串）")
    if any(ch in p for ch in "\r\n\x00"):
        raise ValueError(f"`--link={raw}` 里有换行/NUL——不是合法路径")
    ext = os.path.splitext(p)[1].lower()
    if ext not in _CPP_LINK_FILE_EXTS:
        raise ValueError(
            f"`--link={raw}` 的后缀是 {ext or '（无）'}，不认识；只收 "
            + " / ".join(_CPP_LINK_FILE_EXTS)
            + "（要 `-l` 用 `--lib=`，要 `-L` 用 `--link-dir=`）")
    if not os.path.isfile(p):
        raise ValueError(f"`--link={raw}` 指向的文件不存在——链接必然失败")
    return os.path.abspath(p)


def _cpp_link_dir(raw: str) -> str:
    """`--link-dir=` 实参 → 绝对路径；不是目录就拒绝（`-L` 指过去也找不到库）。"""
    d = (raw or "").strip()
    if not d:
        raise ValueError("`--link-dir=` 要一个目录（不能是空串）")
    if any(ch in d for ch in "\r\n\x00"):
        raise ValueError(f"`--link-dir={raw}` 里有换行/NUL——不是合法路径")
    if not os.path.isdir(d):
        raise ValueError(f"`--link-dir={raw}` 不是目录——`-L` 指过去链接器也找不到库")
    return os.path.abspath(d)


def _cpp_lib_name(raw: str) -> str:
    """`--lib=` 实参 → 库名（不含 `-l` 前缀）。名字直接进命令行，所以校验字符集。"""
    n = (raw or "").strip()
    if not n:
        raise ValueError("`--lib=` 要一个库名（不能是空串；`--lib=m` → `-lm`）")
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", n) or n.startswith("-"):
        raise ValueError(f"`--lib={raw}` 不是合法库名（只收字母/数字/`_`/`.`/`-`，"
                         "不能以 `-` 开头）")
    return n


def _lib_dir(raw: str) -> str:
    """`--lib-dir=` 实参 → 目录（**解释器侧**的库搜索目录；与 `--link-dir=` 不同）。

    `--link-dir=` 是给**链接器**的（`-L`），`--lib-dir=` 是给 `dsh run` 的
    `ctypes` 的（裸名 `from "cjson"` → `<目录>/cjson.dll`）。两者互不替代，
    接一个真库通常两个都要给。空串 / 不是目录当场拒绝：指过去也找不到库。
    """
    d = (raw or "").strip()
    if not d:
        raise ValueError("`--lib-dir=` 要一个目录（不能是空串）")
    if any(ch in d for ch in "\r\n\x00"):
        raise ValueError(f"`--lib-dir={raw}` 里有换行/NUL——不是合法路径")
    if not os.path.isdir(d):
        raise ValueError(f"`--lib-dir={raw}` 不是目录——搜索路径指过去也找不到任何库")
    return d


def _cpp_link_cmd(comp_argv, src, exe, include_dirs=(), link_files=(),
                  link_dirs=(), libs=(), static=None):
    """构造「编译 + 链接成可执行文件」的一条命令（**纯函数**，不碰磁盘）。

    顺序（g++ / clang++）：`-std` → `-I…` → 源文件 → `--link` 文件 → `-L` →
    `-l` → (`-static`) → `-o`。链接器从左到右解析，库必须排在**引用它的目标
    文件之后**，所以 `--link` 文件在 `-l` 之前。

    `static` 三态：`None` = 按平台/编译器默认；`True` = 强制 `-static`；
    `False` = 不加（`--no-static`）。**默认开（Windows + g++）** 的理由：
    MinGW 的 exe 默认动态依赖 `libstdc++-6.dll` / `libgcc_s_seh-1.dll` /
    `libwinpthread-1.dll`，离开编译器目录运行就 `STATUS_DLL_NOT_FOUND`
    （退出码 3221225781 / 0xC0000135）。静态链接让生成物「拷到哪里都能跑」，
    也让 `--run` 的对拍不受 PATH 影响——**那是假红的主要来源**。
    """
    comp = list(comp_argv)
    base = os.path.basename(comp[0]).lower() if comp else ""
    is_cl = base in ("cl", "cl.exe")
    _inc = [str(d) for d in (include_dirs or ()) if str(d)]
    _files = [str(p) for p in (link_files or ()) if str(p)]
    _ldirs = [str(d) for d in (link_dirs or ()) if str(d)]
    _libs = [str(n) for n in (libs or ()) if str(n)]
    if static is None:
        static = (os.name == "nt" and base in ("g++", "g++.exe"))
    if is_cl:
        # `cl` 的链接选项要跟在 `/link` 之后；`.lib` 直接给文件名（没有 `-l`）。
        cmd = comp + ["/nologo", "/std:c++17", "/EHsc"] \
            + [f"/I{d}" for d in _inc] + [src] + _files
        tail: List[str] = []
        if _ldirs:
            tail.append("/link")
            tail += [f"/LIBPATH:{d}" for d in _ldirs]
        if _libs:
            if not tail:
                tail.append("/link")
            tail += [f"{n}.lib" for n in _libs]
        return cmd + tail + [f"/OUT:{exe}"]
    cmd = comp + ["-std=c++17"] + [f"-I{d}" for d in _inc] + [src] + _files
    cmd += [f"-L{d}" for d in _ldirs]
    cmd += [f"-l{n}" for n in _libs]
    if static:
        cmd.append("-static")
    return cmd + ["-o", exe]


def _cpp_link_failure_hint(err: str) -> str:
    """链接失败时补一句「缺什么、用哪个开关」——不猜，只按链接器原话分类。"""
    e = err or ""
    hints: List[str] = []
    if "undefined reference" in e or "unresolved external symbol" in e:
        hints.append("有未定义符号：把提供它的 .o/.a 用 `--link=` 递进来，"
                     "或用 `--link-dir=` + `--lib=`")
    if "cannot find -l" in e or "LNK1181" in e:
        hints.append("链接器找不到某个 `--lib=`：检查 `--link-dir=` 是否指对目录、"
                     "库名是否对得上（`--lib=foo` → `libfoo.a` / `foo.lib`）")
    if "cannot find" in e.lower() and not hints:
        hints.append("链接器找不到输入文件：检查 `--link=` / `--link-dir=` 的路径")
    return "  " + "\n  ".join(hints) if hints else ""


def _cpp_strip_run_return_line(raw: bytes) -> bytes:
    """剥掉 `dsh run` 打印的 **main 返回值**末行（只剥「最后一行且是整数」）。

    **口径的唯一来源是 `tools/behavior_parity.py:strip_return_line`**。这里不能
    import `tools/`（`editors/vscode/python/dsh.py` 是独立分发的镜像，不带 tools），
    所以是一份**逐字等价**的实现；`tests/cases/test_cpp_run_link.py` 用同一批
    夹具断言两者逐字节一致——口径改动时这份测试会红，不会悄悄漂移。
    """
    if not raw:
        return raw
    lines = raw.splitlines(keepends=True)
    if not lines:
        return raw
    body = lines[-1].rstrip(b"\r\n")
    if re.fullmatch(rb"-?\d+", body):
        return b"".join(lines[:-1])
    return raw


def _cpp_exit_code_matches(rc: int, run_stdout: bytes):
    """生成物退出码 vs `dsh run` 打印的返回值。

    只在返回值能落进 **0..255** 时判——那是 POSIX 与 Windows 都一样的区间；
    更大的值会被 OS 截断（口径不唯一），这时**跳过**并说清原因，不硬判、
    也不假装比过。返回 `(True | False | None, 说明)`。
    """
    lines = (run_stdout or b"").splitlines()
    if not lines:
        return (None, "解释器没有打印返回值 ⇒ 退出码不参与对拍")
    try:
        val = int(lines[-1].strip())
    except ValueError:
        return (None, "解释器末行不是整数 ⇒ 退出码不参与对拍")
    if not (0 <= val <= 255):
        return (None, f"返回值 {val} 超出 0..255（会被 OS 截断）⇒ 退出码不参与对拍")
    if rc != val:
        return (False, f"退出码不同：生成物 {rc} vs 解释器 {val}")
    return (True, "")


def _cpp_compile_text(text: str, compiler=None, include_dirs=()):
    """用编译器**真编一遍**生成的 C++（只做语法/语义检查，不链接）。

    返回 `(status, detail)`：

    - `ok`   —— 编译器接受；
    - `skip` —— 本机没有编译器：**明确跳过，不冒充通过**；
    - `fail` —— 编译器拒绝（调用方必须 rc≠0）。

    `include_dirs` 来自 `--include-dir=`：第三方头在别的目录里时，编译器
    自己找不到；把它们传给编译器，自检才是在验"用户会用同一套 `-I` 编的
    那份代码"。
    """
    comp = list(compiler) if compiler is not None else _cxx_compiler_argv()
    if not comp:
        return ("skip", "找不到 C++ 编译器（g++ / clang++ / cl）——"
                        "生成物**未经编译器验证**，这不等于通过")
    import subprocess
    import tempfile
    _dirs = [str(d) for d in (include_dirs or ()) if str(d)]
    with tempfile.TemporaryDirectory(prefix="elench_cppchk_") as d:
        src = os.path.join(d, "m.cpp")
        io.open(src, "w", encoding="utf-8", newline="\n").write(text)
        base = os.path.basename(comp[0]).lower()
        if base in ("cl", "cl.exe"):
            cmd = comp + ["/nologo", "/std:c++17", "/Zs"] \
                + [f"/I{d}" for d in _dirs] + [src]
        else:
            cmd = comp + ["-std=c++17", "-fsyntax-only"] \
                + [f"-I{d}" for d in _dirs] + [src]
        # 本机实测：MinGW g++ 在系统 %TEMP% 下会 `Cannot create temporary file`
        # （Permission denied）。源文件所在目录一定是可写的，指过去即可。
        env = dict(os.environ)
        env["TMP"] = env["TEMP"] = d
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", env=env)
        except OSError as exc:                              # noqa: BLE001
            return ("skip", f"无法执行编译器 {comp[0]}：{exc}——生成物未经验证")
        if r.returncode != 0:
            return ("fail", (r.stderr or r.stdout
                             or f"编译器退出码 {r.returncode}").strip())
        return ("ok", os.path.basename(comp[0]))


def _cpp_run_passthrough(argv) -> List[str]:
    """从 `dsh cpp` 的 argv 里挑出**解释器侧也要**的开关。

    `--run` 是「生成物 vs `dsh run`」的对拍：两边必须用**同一个口径**读同一份
    源码。`--compat` 不递过去的话，`dsh cpp x.eln --compat --run` 的解释器侧
    会按原生语法读同一份 `.eln`（`using T = …` 之类直接 `E-Keyword-Unsupported`），
    只能报「无法对拍」——那不是生成物的问题。`--no-check` / `--strict` /
    `--relax=` / `--checks=` 同理：`run` 也会跑 `_gate_checks`，口径不一致
    就会一边过一个不过。

    `--lib-dir=` 也在这里（2026-11-14）：它是**解释器侧**的库搜索目录，
    不递过去的话，`from "cjson"`（裸名）在 `dsh run` 里仍然加载不了——
    一条命令就还是只能报「无法对拍」。
    """
    out: List[str] = []
    for a in argv:
        if a == "--compat" or a == "--no-check" or a == "--strict" \
                or a.startswith("--strict=") \
                or a.startswith("--relax=") or a.startswith("--checks=") \
                or a.startswith("--lib-dir="):
            out.append(a)
    return out


def _cpp_compare(tu_p: str, ch_p: str, text: str, include_dirs=(),
                 link_files=(), link_dirs=(), libs=(), no_static=False,
                 run_args=()) -> int:
    """编译（+ 链接）+ **真跑** + 与 `dsh run` 逐字节对拍。

    **2026-11-14 之前这里只做到「编译」**：没有运行、没有对拍，`--run` 名不副实。
    现在补上后半段，口径不变（`tools/cpp_parity.py` 同一套）：

      - 生成物 stdout == `dsh run` 的 stdout（**剥掉末行 main 返回值**，
        见 `_cpp_strip_run_return_line`）；
      - 返回值在 0..255 时退出码也要相同（`_cpp_exit_code_matches`）。

    解释器末行**不是整数**（比如 `bool main()` 打 `True`）时，分不清"返回值的
    渲染"和"程序自己打印的最后一行" ⇒ **对拍跳过，未测量**（rc=0，明说不冒充）。

    **没有编译器 ⇒ rc=0 但明说「跳过，未测量」**：那是没测量，不是验证失败
    （既有口径，`test_cpp.py` 按这句话判定）。**编译/链接失败 ⇒ rc≠0**，
    stderr 说清缺什么。临时目录跑完即删（失败的产物也不留）。

    **状态行一律走 stderr**：没给 `--output` 时 stdout 是生成的源码，
    `dsh cpp m --run > m.cpp` 不该被一行 `✓` 或程序输出污染。
    """
    import shutil
    import subprocess
    import tempfile
    comp_argv = _cxx_compiler_argv()
    if comp_argv is None:
        print("  ⚠ 找不到 C++ 编译器（g++ / clang++ / cl），**跳过，未测量**——"
              "源码已生成，但没有验证过它能编译、链接、运行（这不等于通过）",
              file=sys.stderr)
        return 0
    comp = comp_argv[0]
    _dirs = [str(d) for d in (include_dirs or ()) if str(d)]
    work = tempfile.mkdtemp(prefix="elench_cpprun_")
    try:
        src = os.path.join(work, "m.cpp")
        io.open(src, "w", encoding="utf-8", newline="\n").write(text)
        exe = os.path.join(work, "m.exe" if os.name == "nt" else "m.out")
        # **没有入口就只编译、不链接**：模块里没有 `main` 时链接必然失败
        # （MinGW 报 `undefined reference to WinMain`），而那并不说明生成的代码有问题。
        # 这类模块能验的只有"编不编得过"，所以用 `-c`，并如实说明不是可执行文件。
        has_entry = "int main(" in text
        if has_entry:
            cmd = _cpp_link_cmd(comp_argv, src, exe, include_dirs=_dirs,
                                link_files=link_files, link_dirs=link_dirs,
                                libs=libs, static=(False if no_static else None))
        else:
            cmd = comp_argv + ["-std=c++17"] + [f"-I{d}" for d in _dirs] \
                + ["-c", src, "-o", src + ".o"]
        # 与 `_cpp_compile_text` 同一个理由：MinGW 在系统 %TEMP% 下写不了临时文件。
        # 另外把编译器所在目录前置进 PATH：跑生成物时它可能还要找运行时 DLL
        # （`-static` 已经避免了依赖，但用户 `--no-static` 时需要这一条才不会假红）。
        env = dict(os.environ)
        env["TMP"] = env["TEMP"] = work
        if os.path.dirname(comp):
            env["PATH"] = os.path.dirname(comp) + os.pathsep + env.get("PATH", "")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", env=env)
        except OSError as exc:                              # noqa: BLE001
            print(f"  ⚠ 无法执行编译器 {comp}：{exc}——**跳过，未测量**（不是通过）",
                  file=sys.stderr)
            return 0
        if r.returncode != 0:
            # **链接失败也要说清**：rc≠0 + 链接器原话 + 「缺什么」。
            what = "编译/链接" if has_entry else "编译"
            print(f"✗ {what}失败（{comp}，rc={r.returncode}）：", file=sys.stderr)
            print((r.stderr or r.stdout or "").rstrip()[:1200], file=sys.stderr)
            hint = _cpp_link_failure_hint((r.stderr or "") + (r.stdout or ""))
            if has_entry and hint:
                print(hint, file=sys.stderr)
            return 1
        if not has_entry:
            print(f"  ✓ 用 {comp} 编译成功（该模块没有 main，只验到「能编译」，"
                  "未生成可执行文件）", file=sys.stderr)
            if link_files or link_dirs or libs:
                print("  · 该模块没有 main ⇒ 无法链接成可执行文件："
                      "`--link=` / `--link-dir=` / `--lib=` **未使用**（不是验证通过）",
                      file=sys.stderr)
            return 0
        print(f"  ✓ 用 {comp} 编译成功（临时产物，对拍后清理）：{exe}",
              file=sys.stderr)

        # ---- 真跑生成物
        try:
            got = subprocess.run([exe], capture_output=True, env=env, timeout=180)
        except OSError as exc:                              # noqa: BLE001
            print(f"✗ 生成物跑不起来（{exc}）——对拍无法完成", file=sys.stderr)
            return 1
        except subprocess.TimeoutExpired:
            print("✗ 生成物 180s 没结束——对拍无法完成", file=sys.stderr)
            return 1

        # ---- 解释器同口径输出（真跑 `dsh run`，不是"猜它应该打印什么"）
        # `--compat` 等开关一并递过去：对拍的两边必须用同一个口径读同一份源码。
        dsh_py = os.path.abspath(__file__)
        try:
            ir = subprocess.run(
                [sys.executable, "-B", dsh_py, "run"]
                + [str(a) for a in (run_args or ())] + [tu_p, ch_p],
                capture_output=True, env=env, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as exc:  # noqa: BLE001
            print(f"✗ 解释器侧起不来（{exc}）——**无法对拍**", file=sys.stderr)
            return 1
        if ir.returncode != 0:
            print(f"✗ 解释器侧失败（rc={ir.returncode}）——**无法对拍**"
                  "（这不代表生成物错，但也没有比过）", file=sys.stderr)
            print((ir.stderr or b"")[-800:].decode("utf-8", "replace"),
                  file=sys.stderr)
            return 1

        mine = got.stdout or b""
        # **把生成物的 stdout 显示出来**（走 stderr：stdout 在没给 `--output`
        # 时要留给生成的源码，否则 `dsh cpp m --run > m.cpp` 会被程序输出污染）。
        # "一条命令跑完并输出结果"是 `--run` 的意义所在。
        body = mine.decode("utf-8", "replace")
        shown = body if len(body) <= 4096 else body[:4096] + "…（已截断）"
        print(f"  · 生成物 stdout（{len(mine)} 字节）：", file=sys.stderr)
        print(shown.rstrip("\n") or "（空）", file=sys.stderr)

        # **剥末行只看「最后一行且是整数」**（`tools/behavior_parity.py` 的口径）。
        # 末行不是整数 ⇒ 分不清"main 的返回值渲染"和"程序自己打印的最后一行"，
        # 硬比会把 `bool main()`（解释器打 `True`）判成对拍失败——那是**方法**
        # 判不了，不是生成物错。所以明确「跳过，未测量」，**不冒充比过**。
        _raw_lines = (ir.stdout or b"").splitlines()
        if not _raw_lines or re.fullmatch(rb"-?\d+", _raw_lines[-1].strip()) is None:
            print("  · 解释器末行不是整数（main 的返回值不是整数，或它没有输出）——"
                  "现有口径只能剥「末行是整数」的返回值 ⇒ **对拍跳过，未测量**"
                  "（编译 / 链接 / 运行都过了，但 stdout 没有比过）", file=sys.stderr)
            return 0

        theirs = _cpp_strip_run_return_line(ir.stdout or b"")
        if mine != theirs:
            print("✗ 对拍失败：stdout 不逐字节相同", file=sys.stderr)
            n = min(len(mine), len(theirs))
            off = next((i for i in range(n) if mine[i] != theirs[i]), n)
            print(f"  长度 生成物={len(mine)}B / dsh run（剥末行）={len(theirs)}B，"
                  f"首个差异在字节 {off}", file=sys.stderr)
            print(f"  生成物：{mine[:300]!r}", file=sys.stderr)
            print(f"  dsh run：{theirs[:300]!r}", file=sys.stderr)
            return 1

        ok, note = _cpp_exit_code_matches(got.returncode, ir.stdout or b"")
        if ok is False:
            print(f"✗ 对拍失败（退出码）：{note}", file=sys.stderr)
            return 1
        if ok is None:
            print(f"  · 退出码：{note}", file=sys.stderr)
        print("  ✓ 对拍一致：生成物 stdout 与 `dsh run` **逐字节相同**"
              "（已剥 main 返回值末行）", file=sys.stderr)
        return 0
    finally:
        # **失败的产物也不留**：临时目录整个删掉。生成的 `.cpp`（`--output`）
        # 是另一回事——它已经过了自检，是本次的合法输出。
        shutil.rmtree(work, ignore_errors=True)


def cmd_header(argv: List[str]) -> int:
    """`elench header <模块>` —— 生成 C 头文件（**契约跟着代码走**）。

    只写模块名就够了：`_load_pair` 会把 `hello` 解析成 `hello.tu` + 同名 `.eln`，
    也接受 `hello.tu` / 带路径的写法；老的 `<模块.tu> <模块.eln>` 两个参数仍然可用。


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
        print("用法: elench header <模块> [--output=x.h]", file=sys.stderr)
        print("  <模块> 可以是 hello / hello.tu / 文档/入门/例子/hello；"
              "也仍然接受 `<模块.tu> <模块.eln>` 两个参数的老写法。", file=sys.stderr)
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
    ci = _cost_interp(ch_mod, tu_mod, tu_p, force_max=("--variant=max" in argv))
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
            _cty = "const char*" if canon(_ts).startswith("vector") else (_cm.cxx_type(_ts) or "uint64_t")
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
            cty = "const char*" if canon(ts).startswith("vector") else (_cm.cxx_type(ts) or "uint64_t")
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
    if getattr(n, "kind", "") == "type-const":
        # **`array<T, N>` 的容量是 `type-const` 节点**（`value` 是整数）。
        # 旧实现落到最后一行 `getattr(n, "name", "")` → 空串，于是类型串变成
        # `array<u64, >`：`cxx_map` 解析失败，`array` 类型**整体发射不出来**
        # （2026-11-13 实测；命名常量容量走 path，本来就能出）。
        if getattr(n, "name", None):
            return n.name
        return str(n.value) if getattr(n, "value", None) is not None else ""
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


def _unknown_calls(tu_mod, ch_mod) -> List[tuple]:
    """E18：复用 `checker.unknown_calls`（判据只能有一份，否则两处会漂）。"""
    from checker import unknown_calls as _uc
    return _uc(tu_mod, ch_mod)


def _cost_unsound_calls(tu_mod, ch_mod) -> List[tuple]:
    """**κ 不再是上界**的调用点：既没有实现、也没有任何契约可依。

    `checker.unknown_calls` 给出"不在函数体里、也不在 extern 登记表里"的调用。
    其中**有 spec 的**要放过一层：`cost` 对"只有 spec、没有实现"的调用按它声明
    的 `target` 记账，并在报告里标 `target_trusted`——那是**作者签过字的契约**，
    不是"记 1 个操作"。真正不成立的是剩下那一类：连 spec 都没有。

    E18 的原文说的就是这一类："记 1 个操作**不是上界**"。所以 `cost` 在这里
    拒绝，而不是给一个绿色的 κ（`--no-check` 是调试用的显式逃生门）。
    """
    specs = {(c.name or "") for c in (getattr(tu_mod, "children", None) or [])
             if getattr(c, "kind", "") in ("spec", "kernel")}
    return [(nm, ln) for nm, ln in _unknown_calls(tu_mod, ch_mod)
            if nm not in specs]


def _write_cost_artifact(path: str, ci, tu_p: str, ch_p: str, bare: bool) -> str:
    """写 `*.cost.yaml`：**成本结果 + 出处**（`src/cost_artifact.py`）。

    **它不是门禁**：provenance 只写不校验，不会产生任何 error/warning；
    也不能拿它判回归——κ 的漂移检测在 `tools/cost_baseline.py`（入库的 JSON 基线）。
    """
    from cost_check import compare as _cmp
    import cost_artifact as _ca
    rows = []
    for name in ci.fns:
        row = {"name": name}
        try:
            c = ci.cost_of(name)
            t = ci.target_cost(name)
            a = ci.alloc_of(name)
            v = _cmp(c, t, _facts_for(ci, name))
            row.update({"kappa": c.render(),
                        "target": t.render() if t is not None else None,
                        "verdict": v.kind,
                        "allocation": ("alloc <= " + a.render())
                        if a is not None else None,
                        "ratio": v.ratio})
        except Exception:                              # noqa: BLE001
            # 算不出 κ（⊤ / 内部异常）：**如实写成 unknown**，不编一个数。
            row.update({"kappa": "⊤", "target": None, "verdict": "unknown",
                        "allocation": None, "ratio": None})
        rows.append(row)
    return _ca.write(path, module=_module_name_of(tu_p, ch_p),
                     tu_path=None if bare else tu_p, eln_path=ch_p,
                     rows=rows, tool_version=VERSION)


def cmd_cost(argv: List[str]) -> int:
    argv = _expand_module_args(argv)
    _apply = _parse_apply(argv)
    # **`--out=*.yaml`**：写「结果 + 出处」（`cost_artifact`）。它在**人读输出之后**
    # 写，不改变任何已有输出——`cost` 的 stdout 一个字都不动。
    _out_yaml = next((a.split("=", 1)[1] for a in argv
                      if a.startswith("--out=")
                      and a.split("=", 1)[1].lower().endswith((".yaml", ".yml"))),
                     None)
    """静态闭环：算成本式，与 target 比，报债务。"""
    from parser import parse_source
    from cost_interp import CostInterp
    from cost_check import compare, compare_exact

    tu_p, ch_p = _load_pair(argv)
    # **`.tu` 可选**（2026-11-10，规格 §8.9）：没有 `.tu` 就没法解析契约，
    # 但**不是错误**——`cost` 仍然给出 κ，只是"没有契约可判"。
    # 先认出来，别把同一个 `.eln` 当 `.tu` 再解析一遍（否则解析诊断重复两遍）。
    _bare = _no_tu(tu_p, ch_p)
    _parse_paths = [ch_p] if _bare else [tu_p, ch_p]
    if not _abort_on_parse_errors(*_parse_paths):
        # **解析失败 = 检查失败 = 1**（2026-11-06）：以前这里返回 2，
        # 而 2 在约定里是"用法错误"，与 check/build 的 1 也不一致。
        return 1
    if tu_p is None:
        print("用法：elench cost <模块.tu> [<模块.eln>]", file=sys.stderr)
        return 2

    tu_mod = parse_source("" if _bare else io_read(tu_p),
                          os.path.basename(tu_p)).module
    ch_mod = (parse_source(io_read(ch_p), os.path.basename(ch_p)).module
              if ch_p and os.path.exists(ch_p) else None)
    if ch_mod is None:
        print("没有实现文件，无法算成本", file=sys.stderr)
        return 2

    if not _bare:
        if _recursion_gate(tu_mod, ch_mod):
            return 1
        if not _table_covers_declared(tu_mod, tu_p,
                                      strict=("--strict-coverage" in argv)):
            return 1

    # **κ 上界性质被破坏时拒绝**（2026-11-14）。
    #
    # 为什么必须卡：`check` 报 `E-Call-Undeclared` 时，E18 兜底把未知调用记成
    # 「1 个操作」——那**不是上界**。可 `cost` 以前照样打 `✓ main κ ≤ 3` 和
    # 「✓ 所有函数都在 target 之内」、rc=0，只有加 `--explain` 才承认「可能偏小」。
    # 也就是说：**同一个模块，check 说红、cost 说绿**。
    #
    # **判据是「κ 还是不是上界」，不是「check 有没有红」**——两者的差别是有意的：
    #   · `check` 里有些 error **不影响** κ 的上界性质（并行共享可变状态的语义错误、
    #     spec/实现签名不一致、target 写法解析失败），这些情况下 cost 仍要做它
    #     该做的事（后者还要把「解析失败」报出来，见 `_terr`）；
    #   · 「只有 spec、没有实现」的调用**有契约可依**：cost 按它声明的 `target`
    #     记账，并在报告里标 `target_trusted`（那是作者签过字的承诺，不是"记 1"）。
    # 所以这里只拒绝**既没有实现、也没有任何契约**的调用：E18 说的正是这一类。
    # 调试要硬看当前数：`--no-check`（与 `build` 同名开关），stderr 说清不可信。
    _machine = any(a in argv for a in ("--json", "--yaml", "--signature"))
    if "--no-check" in argv:
        print("⚠ --no-check：跳过静态检查——这次的 κ **不是可信的上界**（仅供调试）",
              file=sys.stderr)
    else:
        _unsound = _cost_unsound_calls(tu_mod, ch_mod)
        if _unsound:
            print(f"✗ 拒绝给出 κ：{len(_unsound)} 处调用**既没有实现、也没有契约**"
                  f"（E18：记 1 个操作**不是上界**）", file=sys.stderr)
            for _nm, _ln in _unsound[:5]:
                print(f"  {_nm} @{_ln or 1}:1  → 在 .tu 里补 "
                      f"`external {{ {_nm}: O(…); }};` 或 "
                      f"`spec {_nm}(…) {{ external \"lib\"; }}`", file=sys.stderr)
            if len(_unsound) > 5:
                print(f"  …（还有 {len(_unsound) - 5} 处，跑 `elench check` 看全）",
                      file=sys.stderr)
            print("  这些被调体的成本一无所知（`check` 把这类报成 "
                  "`E-Call-Undeclared`）⇒ 这里的 κ **不是可信的上界**，"
                  "「达标」不能当结论。", file=sys.stderr)
            print("  修法：先 `elench check` 修完；确要调试可加 `--no-check`"
                  "（那时输出不可信）。", file=sys.stderr)
            return 1

    # `--explain`：只读侧表，**不影响 κ**（硬约束：开关前后逐字节相同）
    _explain = "--explain" in argv
    # **机器档与规模**（2026-11-09）：**只影响 `wall_est`**（报告层），
    # MUST NOT 进 κ——κ 要能跨机器冻结（`tests/golden/kappa.json`）。
    #   --cores=<profile.json>  核数 + 每核权重（格式见 `cost_domain/par.py`）
    #   --at=<N>                把每个尺寸量都设成 N，用来给墙钟一个规模
    from cost_domain import par as _par
    _mprof = None
    _mprof_arg = next((a.split("=", 1)[1] for a in argv
                       if a.startswith("--cores=")), None)
    if _mprof_arg:
        try:
            _mprof = _par.MachineProfile.from_json(_mprof_arg)
        except ValueError as _e:                       # noqa: BLE001
            print(f"✗ 机器档不可用：{_e}", file=sys.stderr)
            return 2
    #: **注意别用 `_at` 这个名字**：下面每轮的"分配 target"局部变量也叫 `_at`
    #: （`_at = at.render()`），两者撞名会让 "--at=1024" 变成一个字符串漏进
    #: `wall_estimate`，在那里 `str > 0` 直接 TypeError（2026-11-09 踩过）。
    _at_scale = None
    _at_arg = next((a.split("=", 1)[1] for a in argv
                    if a.startswith("--at=")), None)
    if _at_arg is not None:
        try:
            _at_scale = float(_at_arg)
            if not (_at_scale > 0) or _at_scale != _at_scale:
                raise ValueError
        except ValueError:
            print(f"✗ --at 要一个正数：{_at_arg!r}", file=sys.stderr)
            return 2
    ci = _cost_interp(ch_mod, tu_mod, tu_p,
                      force_max=("--variant=max" in argv), explain=_explain)
    # **机读模式下不许有人的话**：`--json/--yaml/--signature` 的 stdout 必须
    # 能被 `json.loads()` 直接吃掉。以前这行口径说明会混在前面，agent 一解析就炸。
    _machine = any(a in argv for a in ("--json", "--yaml", "--signature"))
    # **未声明契约**（没有 spec / kernel 声明）的函数：κ 照常算，但不参与判定——
    # 不算债务、不算错误（规格 §8.9）。口径与 `check` 的 F-1 一致：
    # 没有 spec = 没有契约声明，不是"判不出来"，更不是"达标"。
    _declared_names = {(c.name or "") for c in (tu_mod.children if tu_mod else [])
                       if c.kind in ("spec", "kernel")}
    _undeclared = [n for n in ci.fns if n not in _declared_names]
    if _bare:
        print("  （本模块没有 .tu：没有契约可判；κ 照常给出，不计债务、不算错误）",
              file=(sys.stderr if _machine else sys.stdout))
    # **先把每个函数的 target 预解一遍**（2026-11-09）：
    # `target_errors` 是在 `target_cost()` 里**懒登记**的，如果等到下面的
    # 判定循环才解析，`_terr` 早就取过了（空表）——错误一条也打不出来。
    # 这一步只解析、不改 κ，放在任何输出之前。
    for _pn in ci.fns:
        for _pdim in ("time", "allocation"):
            try:
                ci.target_cost(_pn, _pdim)
            except Exception:                      # noqa: BLE001
                pass
    # **承诺口径（2026-11-02 定）**：承诺 κ = **分段上界**（piecewise）——
    # 每段用运行期真正会跑的那个候选的符号式；分段取自运行期同一份 `.dispatch.yaml`。
    # 无表 / 单候选 → 退回 `max over candidates`（sound，只是松）。
    #
    # **前提是 S2 同源**：分段必须来自运行期读的那张表，不能用 `segment_plan` 的静态交点切
    # （两次计算会给出两个分区）。实测踩过三次：
    #   `search.binary_search` 余量 -341；`gap` 逐段 402 / 实测 733；
    #   表外 n=8192 承诺 426,001 / 实测 729,109。
    # **"写了 target 但认不出"必须报错**（2026-11-09）：以前这种槽位被
    # `_find_slot` 静默跳过，这里只打印"spec 没有 time 的 target"——
    # 作者以为有契约、其实这一句被丢了。诊断走 stderr，机读 stdout 保持干净。
    _terr = list(getattr(ci, "target_errors", []) or [])
    if _terr:
        _tu_base = os.path.basename(tu_p)
        print(f"✗ {len(_terr)} 处 target 解析失败（这些 target **不参与判定**，"
              f"不要当成没写）：", file=sys.stderr)
        for _e in _terr:
            print(f"    {_tu_base}:{_e.get('line')}:{_e.get('col')}  "
                  f"{_e.get('fn')}: {_e.get('message')}", file=sys.stderr)
        print("", file=sys.stderr)
    if not _machine:
        print("  （口径：承诺 = 分段上界——每段取该段实际会跑的候选；"
              "分段与运行期同一份分档表；无表时退回 max）")

    # `--signature`：输出机器可读的成本签名（JSON），供 agent 当**预算先验**用。
    # 这是"把成本签名塞进 agent 上下文"（研究切口 1）所需要的那份产物。
    if "--signature" in argv or "--json" in argv:
        out = {"module": os.path.basename(tu_p).rsplit(".", 1)[0],
               "functions": {}}
        if _terr:
            out["target_errors"] = [
                {"function": _e.get("fn"), "dimension": _e.get("dim"),
                 "slot": _e.get("slot"), "line": _e.get("line"),
                 "col": _e.get("col"), "expression": _e.get("expr"),
                 "message": _e.get("message")}
                for _e in _terr]
        for name in ci.fns:
            # **`target_trusted` 是累计表**：取算这个函数前后两次快照的差，
            # 才是「它的 κ 里有多少项只能按被调函数声明的 target 记账」。
            _tt_before = dict(getattr(ci, "target_trusted", None) or {})
            try:
                c = ci.cost_of(name)
            except Exception as e:                   # noqa: BLE001
                out["functions"][name] = {
                    "cost": {"operator": "unknown"},
                    "verdict": "unknown",
                    "reason": str(e)}
                continue
            _tt = {}
            try:
                import cost_explain as _ce
                _tt = _ce.trusted_delta(
                    _tt_before, getattr(ci, "target_trusted", None))
            except Exception:                          # noqa: BLE001
                _tt = {}
            tgt = ci.target_cost(name)
            v = compare(c, tgt, _facts_for(ci, name))
            try:
                a = ci.alloc_of(name)
            except Exception:                          # noqa: BLE001
                a = None
            atgt = ci.target_cost(name, "allocation")
            av = compare_exact(a, atgt) if a is not None else None
            # **未声明契约**（没有 spec）在机读输出里也必须是**独立的档**：
            # 写成 `unknown` 会被下游读成"契约判不出来"，而事实是"没写契约"。
            _und = name in _undeclared
            out["functions"][name] = {
                "cost": c.to_dict(),
                "cost_expression": c.render(),
                "target": tgt.to_dict() if tgt is not None else None,
                "verdict": "undeclared" if _und else v.kind,
                "verdict_explanation": ("未声明契约（没有 spec）：不参与判定、"
                                        "不算债务、不算错误" if _und else v.render()),
                "allocation": a.to_dict() if a is not None else None,
                "allocation_expression": a.render() if a is not None else None,
                "allocation_verdict": av.kind if av is not None else "unknown",
                "allocation_explanation": av.render() if av is not None else None,
            }
            # **W-Target-Loose**（2026-11-10）：机读版也带上，但**只在有告警时才加这个键**——
            # 不为绝大多数模块改动已有的 JSON 形状（下游 `json.loads` 的使用者不用改）。
            try:
                import cost_explain as _ce
                _sv = (c.variables() or ["n"])[0] if c is not None else "n"
                _wl = _ce.target_loose_lines(ci, name, tgt, c, _sv)
            except Exception:                          # noqa: BLE001
                _wl = []
            if _wl:
                out["functions"][name]["warnings"] = [
                    {"code": "W-Target-Loose", "message": " ".join(_wl)}]
            # **被迫信任声明**（2026-11-10）：机读版逐项写明「哪一项不是推导值」。
            # **只在非空时加键**——绝大多数模块没有这种调用点，JSON 形状不变。
            if _tt:
                try:
                    import cost_explain as _ce
                    out["functions"][name]["target_trusted"] = \
                        _ce.target_trusted_entries(ci, _tt)
                except Exception:                      # noqa: BLE001
                    pass
            # **`parallel` 的结构**（只在有 `par` 时出现）：机读版的每个任务，
            # 以及**依赖 profile 的 `wall_estimate`**（标了 `profile_default`）。
            _ps = _par.summary(c, _mprof, _at_scale)
            if _ps is not None:
                out["functions"][name]["parallel"] = _ps
        if "--yaml" in argv:
            print(to_yaml(out))
        else:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        # 解析失败的 target 不算"没写"：机读模式同样要以非 0 退出，
        # 否则调用方会把"契约没生效"读成"没有契约、通过"。
        return 1 if _terr else 0

    # **成本 diff**：`--diff=<ref>` 只比承诺 κ（不比实测），见 src/cost_diff.py
    _dref = next((a.split("=", 1)[1] for a in argv if a.startswith("--diff=")), None)
    if _dref:
        import cost_diff as _cd
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _rows, _tblchg, _ctx, _err, _ctr = _cd.diff(tu_p, ch_p, _dref, _root)
        if _err:
            print(f"✗ {_err}", file=sys.stderr)
            return 2
        print(_cd.render(_rows, _dref, _tblchg, _ctx, _ctr))
        if "--deny-regress" in argv:
            # **口径不可比时不判回归**：两端表的有无不一致时，"变大"是口径差
            # 而不是代码变坏，拿它卡 CI 会因为一个文件没提交而误报。
            _bad = [r for r in _rows
                    if r.get("comparable", True)
                    and r["kind"] == "L1" and (r.get("ratio") or 0) > 1.0]
            if _bad:
                print(f"\n✗ 拒绝：{len(_bad)} 个函数的成本阶变差（--deny-regress）："
                      + ", ".join(r["function"] for r in _bad), file=sys.stderr)
                return 1
        return 0

    print(f"module {os.path.basename(tu_p).rsplit('.', 1)[0]}")
    # **G25**：并发函数的时间契约不可证伪——看见就说明白，别让人以为 κ 覆盖了并发
    try:
        _conc = [n.name for n in getattr(ch_mod, "children", []) or []
                 if getattr(n, "kind", "") in ("func", "fn")
                 and any(x.kind in ("parallel", "spawn")
                         for x in (_all_nodes(n)))]
        if _conc:
            print(f"  ⚠ 并发：{', '.join(sorted(set(_conc)))} 使用 parallel/spawn；"
                  f"v0 的 κ 按**单线程语义**计，并发下的耗时与竞争不在承诺范围内（G25）")
    except Exception:                                  # noqa: BLE001
        pass
    _uc = _unknown_calls(tu_mod, ch_mod)
    if _uc:
        _names = [n for n, _ in _uc]
        print(f"⚠ 有 {len(_uc)} 处调用落到 E18 兜底（成本记作 1+实参，**不是上界**）："
              f"{', '.join(_names)}")
        print("   被调体成本一无所知时，κ 可能偏小。修法：`external { 名字: O(…) }` 登记。")
    print()
    w = max((len(n) for n in ci.fns), default=8)
    # **`--explain` 才读实现文件**（行号要它）；循环里每个函数再读一遍是浪费，
    # 而且 `W-Target-Loose` 的"支配项在哪一行"与归因段必须是**同一份**源文本。
    _src_all: List[str] = []
    if _explain:
        try:
            _src_all = io.open(ch_p, encoding="utf-8").read().splitlines()
        except Exception:                              # noqa: BLE001
            _src_all = []
    debt = 0
    for name in ci.fns:
        # 快照见机器档同处注释：`target_trusted` 跨函数累计，差值才是本函数的。
        _tt_before = dict(getattr(ci, "target_trusted", None) or {})
        try:
            c = ci.cost_of(name)
        except Exception as e:                       # noqa: BLE001
            print(f"  {name.ljust(w)}  ⊤   ({e})")
            continue
        _tt_delta = {}
        try:
            import cost_explain as _ce
            _tt_delta = _ce.trusted_delta(
                _tt_before, getattr(ci, "target_trusted", None))
        except Exception:                              # noqa: BLE001
            _tt_delta = {}
        t = ci.target_cost(name)
        v = compare(c, t, _facts_for(ci, name))
        # **别把"解析失败"说成"没写"**：两者对读者意味着完全不同的事。
        if t is None and ci.target_failed(name, "time"):
            v.note = "target 写了但解析失败（见上面的诊断），不是没写"
        # **未声明契约**：没有 spec ⇒ 没有 target 可比。以前这里会打出
        # 「? 无法判定：spec 没有 time 的 target」，读者会以为契约坏了；
        # 规格 §8.9 的口径是「没有 spec = 没有契约声明 = 不报错、不算债务」。
        _declared = name not in _undeclared
        mark = ("·" if not _declared
                else {"ok": "✓", "debt": "▲", "unknown": "?"}[v.kind])
        debt += 1 if (_declared and v.kind == "debt") else 0
        # **E36：allocation 维度也判**（以前只解析、不判）
        try:
            a = ci.alloc_of(name)
            at = ci.target_cost(name, "allocation")
            av = compare_exact(a, at)
            if at is None and ci.target_failed(name, "allocation"):
                av.note = "target 写了但解析失败（见上面的诊断），不是没写"
        except Exception:                              # noqa: BLE001
            a, av = None, None
        amark = {"ok": "✓", "debt": "▲", "unknown": "?"}[av.kind] if av is not None else " "
        debt += 1 if (av is not None and _declared and av.kind == "debt") else 0
        print(f"  {mark} {name.ljust(w)}  κ ≤ {c.render()}")
        if not _declared:
            # 不参与判定：κ 仍然给出（它本身是有信息的），但不说"达标/未知"。
            print(f"    {'':{w}}  未声明契约（没有 spec）：不参与判定、不算债务、不算错误")
            continue
        # **「这个上界里有一段是别人的承诺」**（2026-11-10）：没有推导值的调用点
        # （被调函数只有 spec / 递归没有 `recursion depth`）只能按它声明的 target
        # 记账。以前这个标注只在程序内（`ci.target_trusted`），CLI 看不出区别——
        # 读者会把作者写的 target 当成工具算出来的上界。**只标注，不改判定。**
        if _tt_delta:
            try:
                import cost_explain as _ce
                for _ln in _ce.target_trusted_lines(ci, _tt_delta):
                    print(f"    {'':{w}}  {_ln}")
            except Exception:                          # noqa: BLE001
                pass
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
        # **W-Target-Loose**（2026-11-10，warning，**不参与判定**）：界松 ≠ 实现慢。
        # **不加 `--explain` 也打**：这条说的是"契约本身有问题"，藏在一个开关后面
        # 就等于又让读者只看到「达成：阶够」而不知道这个"够"是靠常数项撑的。
        # 判定（ok / debt / unknown）与 `compare` 的结论**一个字节都不变**。
        try:
            import cost_explain as _ce
            _sv = (c.variables() or ["n"])[0]
            _wl = _ce.target_loose_lines(ci, name, t, c, _sv,
                                         src_lines=_src_all)
        except Exception:                              # noqa: BLE001
            _wl = []
        for _ln in _wl:
            print(f"    {'':{w}}  {_ln}")
        # **⊤ 的原因要说人话**：`compare()` 只有 `Cost`，分不清「循环上界推不出」
        # 与「树递归退 ⊤」。原因由 `CostInterp._note_tree_rec` 侧记（只读）。
        # **`--explain` 时改由 `cost_explain` 在「杠杆 —」旁边转发**：
        # 归因段必须自己带上原因（不能编一个杠杆数），这里再打一遍就是重复。
        if not _explain and v.kind == "unknown" and c.is_top():
            _why = (getattr(ci, "top_reasons", None) or {}).get(name)
            if _why:
                print(f"    {'':{w}}  原因：{_why}")

        # **最大核视图**（2026-11-09）：κ 里有 `parallel` 就逐任务列出来。
        # 三件事分开写，因为它们的**性质不同**：
        #   判阶 κ  = max 口径（B-24）——**契约**，机器无关
        #   总量 Σ  = B-24 的 Σ ——**契约**，机器无关
        #   wall_est= 依赖核数/权重 ——**估计**，不是契约（B-27）
        try:
            for _ln in _par.render(c, _mprof, _at_scale, indent=""):
                print(f"    {'':{w}}  {_ln}")
        except Exception as _e:                        # noqa: BLE001
            print(f"    {'':{w}}  parallel 视图不可用：{type(_e).__name__}: {_e}")

        # **余量视图**（L-08，2026-11-06）：告诉人"还差多少就超了"。
        # **只在两边都是数时给数字**；阶比较没有数值余量——那是 big-O 的性质，
        # 硬算一个减法等于编数字（例如 O(n) 对 14n，余量是无穷，不是某个数）。
        if _explain:
            def _num_of(_c):
                try:
                    _r = _c.render().strip()
                    return int(_r) if _r.lstrip('-').isdigit() else None
                except Exception:                      # noqa: BLE001
                    return None
            for _dim, _act, _tgt in (("time", c, t), ("alloc", a, at)):
                if _act is None or _tgt is None:
                    continue
                _an, _tn = _num_of(_act), _num_of(_tgt)
                if _an is not None and _tn is not None:
                    _slack = _tn - _an
                    _tag = "余量" if _slack >= 0 else "超支"
                    print(f"    {'':{w}}  {_tag} {abs(_slack)}（target {_tn} − 实际 {_an}，{_dim}）")
                else:
                    print(f"    {'':{w}}  余量 不适用（{_dim} 是阶比较；big-O 没有数值余量）")
        # **归因**（只读）：超越时说出"是哪一段抬的、删了它能不能达标"。
        # 以前只报「超标 + 比值」，读的人知道超了但不知道从哪下手。
        # **达标也归因**（2026-11-06）：以前只在超界时输出，于是"这条为什么贵"在达标时看不到。
        if _explain:
            try:
                import cost_explain as _ce
                _sv = (c.variables() or ["n"])[0]
                for _ln in _ce.explain_function(
                        ci, name, v.kind, t, c, _sv, dimension="time",
                        src_lines=_src_all):
                    print(f"    {'':{w}}  {_ln}")
                # 跨函数链的第一级：这一层贵，是因为调了谁
                for _ln in _ce.callee_lines(ci, name, _sv):
                    print(f"    {'':{w}}  {_ln}")
            except Exception as _e:                    # noqa: BLE001
                print(f"    {'':{w}}  归因不可用：{type(_e).__name__}: {_e}")
        if _explain and av is not None and av.kind == "debt" and a is not None and at is not None:
            # 分配维度单独归因：它和 time 是两个独立的判据，混在一起会互相掩盖。
            try:
                import cost_explain as _ce
                _sv2 = (a.variables() or ["n"])[0]
                for _ln in _ce.explain_function(
                        ci, name, av.kind, at, a, _sv2, dimension="allocation"):
                    print(f"    {'':{w}}  {_ln}")
            except Exception as _e:                    # noqa: BLE001
                print(f"    {'':{w}}  分配归因不可用：{type(_e).__name__}: {_e}")
        if _explain and v.kind == "ok":
            # **"达标"两个字不够**：判决是渐近的，契约的定义域是有限的。
            # 借自 Majdoddin（arXiv 2610.41622）：asymptotic bound 不说从多大的输入才成立。
            try:
                import cost_explain as _ce
                _sv = (c.variables() or ["n"])[0]
                for _ln in _ce.threshold_lines(ci, name, t, c, _sv):
                    print(f"    {'':{w}}  {_ln}")
            except Exception as _e:                    # noqa: BLE001
                print(f"    {'':{w}}  阈值不可用：{type(_e).__name__}: {_e}")

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
    # （`_declared_names` / `_undeclared` 在算 κ 之前已经算好，见上面「未声明契约」。）
    unknown = 0
    for name in ci.fns:
        # **未声明契约的函数不进"无法判定"**（规格 §8.9）：没有 spec 就没有承诺，
        # 把它算成 unknown 等于把"没写契约"报成"契约判不了"。
        if name in _undeclared:
            continue
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
    # **未声明契约的汇总**（规格 §8.9）：不算债务、不算错误，但必须有一行，
    # 否则读者会把"没写 spec"当成"已达标"。有 `.tu` 时点名是哪些函数漏了。
    if _undeclared:
        _why = "没有 .tu" if _bare else "没有 spec"
        print(f"· {len(_undeclared)} 个函数未声明契约（{_why} ⇒ 没有契约可判；"
              f"不算债务、不算错误）")
    if not debt and not unknown and not _undeclared:
        print("✓ 所有函数都在 target 之内")
    if _out_yaml:
        # **人读输出已经打完**，这里只多一行"写到哪"。provenance 不是门禁。
        try:
            _written = _write_cost_artifact(_out_yaml, ci, tu_p, ch_p, _bare)
            print(f"成本结果（含 provenance）→ {_written}")
        except OSError as _e:                          # noqa: BLE001
            print(f"✗ 写不出 {_out_yaml}：{_e}", file=sys.stderr)
            return 2
    if _terr:
        # **"写了却认不出"不是通过**：契约没生效，退出码必须是失败。
        print(f"✗ {len(_terr)} 处 target 解析失败：修好之前，这些函数的承诺不生效",
              file=sys.stderr)
        return 1
    return 0


def cmd_dispatch(argv: List[str]) -> int:
    argv = _expand_module_args(argv)
    _choose = None
    _args = [a for a in argv if not a.startswith('--')]
    if len(_args) >= 2 and not _args[1].endswith(('.tu', '.eln')):
        if not os.path.exists(_args[1]) and not os.path.exists(_args[1] + ".tu"):
            _choose = _args[1]
            argv = [a for a in argv if a != _choose]
    """三层筛：为每个 ?! 决定要不要测、分几段。"""
    from parser import parse_source
    from cost_interp import CostInterp
    from dispatch import plan_dispatch

    tu_p, ch_p = _load_pair(argv)
    if not _abort_on_parse_errors(tu_p, ch_p):
        return 2
    if tu_p is None:
        print("用法：elench dispatch <模块.tu> [<模块.eln>]", file=sys.stderr)
        return 2

    tu_mod = parse_source(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = (parse_source(io_read(ch_p), os.path.basename(ch_p)).module
              if ch_p and os.path.exists(ch_p) else None)
    if ch_mod is None:
        print("没有实现文件，无法规划分档", file=sys.stderr)
        return 2

    ci = _cost_interp(ch_mod, tu_mod, tu_p, force_max=("--variant=max" in argv))
    sizes, lo, hi = _scale_range(tu_mod)
    if lo is None:
        print("没有 benchmodel.scale，无法确定实际输入范围", file=sys.stderr)
        return 2

    print(f"module {os.path.basename(tu_p).rsplit('.', 1)[0]}")
    print(f"实际输入范围：{lo:,} .. {hi:,}")
    print()
    from bound import _descendants
    labels = sorted({k.name for k in _descendants(ci.mod, "kernels") if k.name})
    # 位置参数 <choose名>：只处理这一个 ?!（不收 --only）
    if _choose is not None:
        if _choose not in labels:
            _opts = ', '.join(labels) or '(none)'
            print('✗ 这个模块没有 ?!%s；可选：%s' % (_choose, _opts), file=sys.stderr)
            return 2
        labels = [_choose]
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
    """读源码（**只接受 UTF-8**；失败抛 `parser.SourceEncodingError`）。"""
    from parser import read_source_text
    return read_source_text(path)



# ---------------------------------------------------------------- 声明基线
#
# **威胁模型不是"AI 有恶意"，是它顺手把 target 放宽到刚好能过。**
# 这不需要恶意，只需要它想交差。`git diff` 能"事后发现"，但发现责任在人，
# 而人正是会漏的那一环（target 改一行藏在 200 行 diff 里）。
#
# 所以加一道闸：声明的**语义指纹**变了，就必须显式确认一次（`--accept-tu-change`）。
# 指纹用**词法 token 序列**而不是文件哈希——**改注释不该触发确认**。
#
# 闸装在三条会"重新定基线"的命令上：`build`（门禁）、`measure`（落表）、
# `export`（出图与数字）。只读命令（`cost` / `check`）不拦。


def _tu_canon(tu_p: str) -> str:
    """`.tu` 的语义指纹：词法 token 序列（注释已被词法器丢掉，空白已归一）。"""
    from lexer import lex
    return "\x00".join(t.text for t in lex(io_read(tu_p)).tokens)


def _accept_path(tu_p: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(tu_p)),
                        os.path.basename(tu_p).rsplit(".", 1)[0] + ".accept.yaml")


def _snapshot_path(tu_p: str) -> str:
    """接受时留的 `.tu` **全文快照**——用来回答"上次确认的到底是什么"。"""
    return os.path.join(os.path.dirname(os.path.abspath(tu_p)),
                        os.path.basename(tu_p).rsplit(".", 1)[0] + ".accept.tu.txt")


def _write_accept(tu_p: str, sha: str) -> None:
    import time as _time
    # **留快照**：光有指纹只能说"变了"，说不出"变了什么"。
    # 而这个目录常被 gitignore（`文档/设计/`），`git diff` 也帮不上忙。
    try:
        with io.open(tu_p, encoding="utf-8") as _src, \
                io.open(_snapshot_path(tu_p), "w", encoding="utf-8", newline="\n") as _dst:
            _dst.write(_src.read())
    except OSError:
        pass
    with io.open(_accept_path(tu_p), "w", encoding="utf-8", newline="\n") as f:
        f.write("# 由 elench 生成：声明（.tu）最后一次被确认过的状态。\n")
        f.write("# 声明改了就要重新确认：elench build <模块> --accept-tu-change\n")
        f.write("# 上次确认的**全文**在旁边的 .accept.tu —— 再变就用它 diff，别指望 git。\n")
        f.write(f"module: {os.path.basename(tu_p).rsplit('.', 1)[0]}\n")
        f.write(f"tu_sha256: {sha}\n")
        f.write(f"accepted_at: {_time.strftime('%Y-%m-%dT%H:%M:%S')}\n")


#: 已经提示过「非 git 环境」的 `.tu`，避免每个模块都喊一遍
_GIT_WARNED = set()


def _in_git(path: str) -> bool:
    """模块所在目录是不是在一个 git 仓库里。"""
    import subprocess as _sp
    d = os.path.dirname(os.path.abspath(path)) or "."
    try:
        return _sp.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                       capture_output=True).returncode == 0
    except Exception:                                  # noqa: BLE001
        return False


def _check_tu_accepted(tu_p: str, argv: List[str]) -> int:
    """声明被静默改过就拦住。0=放行，1=拦下，2=算不出指纹。

    **只在 git 仓库里生效**，但**不静默**：非 git 环境下打一条提示再放行。
    连不连 git 不在语言的范围里（2026-11-04 定），所以这里**只警告、不拦**；
    但"闸没启用"这件事必须说出来，否则用户会以为声明被保护着。
    """
    import hashlib
    import re as _re
    if not (tu_p and os.path.exists(tu_p)):
        return 0
    if not _in_git(tu_p):
        if tu_p not in _GIT_WARNED:
            _GIT_WARNED.add(tu_p)
            print(f"⚠ 未检测到 git 仓库：{os.path.basename(tu_p)} 的声明防篡改闸**不启用**"
                  f"（连不连 git 不在语言范围内，故只提示不拦）", file=sys.stderr)
        return 0
    try:
        cur = hashlib.sha256(_tu_canon(tu_p).encode("utf-8")).hexdigest()
    except Exception as exc:                           # noqa: BLE001
        print(f"✗ 算不出声明的指纹：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    ap = _accept_path(tu_p)
    old = None
    if os.path.exists(ap):
        for raw in io.open(ap, encoding="utf-8"):
            m = _re.match(r"tu_sha256:\s*([0-9a-f]{64})", raw)
            if m:
                old = m.group(1)
                break
    name = os.path.basename(tu_p)
    modname = name.rsplit(".", 1)[0]
    if "--accept-tu-change" in argv:
        _write_accept(tu_p, cur)
        print(f"  ✓ 已确认当前声明：{name}（{os.path.basename(ap)}）")
        return 0
    if old is None:
        _write_accept(tu_p, cur)
        print(f"  · 记录声明基线：{name} → {os.path.basename(ap)}")
        print("    以后声明（target / effects / scale）一改，"
              "build / measure / export 都会要求 --accept-tu-change。")
        return 0
    if old != cur:
        print(f"✗ 声明变了：{name}", file=sys.stderr)
        print("  这道闸防的不是「你改」（那是你的权利），"
              "是**声明被静默放宽到刚好能过**。", file=sys.stderr)
        print(f"  先看清改了什么：  git diff -- {name}", file=sys.stderr)
        _snap = _snapshot_path(tu_p)
        if os.path.exists(_snap):
            print(f"  上次确认的全文：  {_snap}", file=sys.stderr)
            print(f"  对比改了什么：    diff {_snap} {tu_p}", file=sys.stderr)
        print(f"  确认无误再放行：  elench build {modname} --accept-tu-change",
              file=sys.stderr)
        return 1
    return 0


def cmd_measure(argv: List[str]) -> int:
    argv = _expand_module_args(argv)
    # 位置参数 <choose名>：只重测这一个 ?!（不收 --only）
    _choose = None
    _args = [a for a in argv if not a.startswith('--')]
    if len(_args) >= 2 and not _args[1].endswith(('.tu', '.eln')):
        if not os.path.exists(_args[1]) and not os.path.exists(_args[1] + '.tu'):
            _choose = _args[1]
            argv = [a for a in argv if a != _choose]
    """动态闭环：真跑候选，用实测生成分档表（§7.8 第 ③ 步）。"""
    from parser import parse_source
    from cost_interp import CostInterp
    from dispatch import plan_dispatch, table_from_measure

    tu_p, ch_p = _load_pair(argv)
    if not _abort_on_parse_errors(tu_p, ch_p):
        return 2
    if _choose is not None:
        from policy import local_table_path as _ltp0
        _t0 = _ltp0(os.path.dirname(os.path.abspath(tu_p)),
                    os.path.basename(tu_p).rsplit('.', 1)[0])
        if not os.path.exists(_t0):
            print('✗ 还没有分档表：%s' % _t0, file=sys.stderr)
            print('  增量重测要有一份已有的表；先跑一次全量：elench measure %s'
                  % _args[0], file=sys.stderr)
            return 2
    if tu_p is None:
        print("用法：elench measure <模块.tu> [<模块.eln>]", file=sys.stderr)
        return 2
    if not (ch_p and os.path.exists(ch_p)):
        print("没有实现文件，无法测量", file=sys.stderr)
        return 2

    _rc = _check_tu_accepted(tu_p, argv)
    if _rc:
        return _rc

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
    ci = _cost_interp(ch_mod, tu_mod, tu_p, force_max=("--variant=max" in argv))
    sizes_by_param, lo, hi = _scale_range(tu_mod)
    # **`--max-size`**：大样例（`count` 在 n=4096 是 1.7e7 操作）会让测量撞步数预算，
    # 直接失败、不产出表。允许把表的覆盖范围截到 N，表外按运行期回退处理。
    for _a in argv:
        if _a.startswith("--max-size="):
            _ms = int(_a.split("=", 1)[1])
            sizes_by_param = {k: [x for x in v if x <= _ms] or v[:1]
                              for k, v in sizes_by_param.items()}
            hi = min(hi, _ms)
    if not sizes_by_param:
        print("没有 benchmodel.scale，无法确定测量点", file=sys.stderr)
        return 2

    from bound import _descendants
    print(f"module {os.path.basename(tu_p).rsplit('.', 1)[0]}")
    _all = {}
    _degraded = False
    _labels = sorted({k.name for k in _descendants(ch_mod, "kernels") if k.name})
    if _choose is not None:
        if _choose not in _labels:
            print('✗ 这个模块没有 ?!%s；可选：%s' % (_choose, ', '.join(_labels) or '(none)'),
                  file=sys.stderr)
            return 2
        _labels = [_choose]
    for label in _labels:
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
        from policy import local_table_path as _ltp
        _tblp = _ltp(os.path.dirname(os.path.abspath(tu_p)), _mod)
        _degraded = _degraded or dev_cap is not None or len(pts) < len(full_pts)
        if _choose is not None:
            if not os.path.exists(_tblp):
                print('✗ 还没有分档表：%s\n  先跑一次全量 measure %s' % (_tblp, _mod),
                      file=sys.stderr)
                return 2
            _merge_dispatch_table(_tblp, _mod, label, segs, _degraded)
        else:
            _all[label] = segs
            _write_dispatch_table(_tblp, _mod, _all, _degraded)
        print(f"  分档表 → {_tblp}（{len(_all)} 个 label）")

        # **`--commit`：另写一份入库的参考表**（`tables/<模块>.table.yaml`）。
        # 为什么值得入库：新人 clone 下来不必先跑 `measure`（那是分钟级），CI 也能直接跑；
        # 代价是**实测出来的交叉点与机器有关**，换机器可能次优——所以它只是一个
        # "开箱可用"的起点，`[dispatch] source` 决定要不要用它。
        if "--commit" in argv:
            try:
                _rp = _rtp(os.path.dirname(os.path.abspath(tu_p)), _mod)
                os.makedirs(os.path.dirname(_rp), exist_ok=True)
                with open(_rp, "w", encoding="utf-8", newline="\n") as _f2:
                    _f2.write("# 由 `elench measure --commit` 生成：**入库的参考表**。\n")
                    _f2.write("# 交叉点是实测出来的，**与机器有关**：换机器可能次优。\n")
                    if _degraded:
                        _f2.write("# DEGRADED: 未覆盖声明区间，不可用于承诺\n")
                        _f2.write("degraded: true\n")
                    _f2.writelines(_body)
                print(f"  入库参考表 → {_rp}"
                      + ("（⚠ 是 DEGRADED 的，不要提交）" if _degraded else ""))
            except Exception as _e:                        # noqa: BLE001
                print(f"  ✗ 写入库表失败：{type(_e).__name__}: {_e}", file=sys.stderr)
        # **表被 .gitignore 排除时，必须当场说**：承诺是分段上界，分段来自表。
        # 表不进版本库 → 别人 clone 到的是 `max` 口径（同一份代码差近 2 倍），
        # `elench cost --diff` 也永远比不了历史。实测过 `gap`：201,553 vs 104,567。
        try:
            import subprocess as _sp
            if _sp.run(["git", "check-ignore", "-q", _tblp],
                       capture_output=True).returncode == 0:
                print("  ⚠ 这张表被 .gitignore 排除（*.dispatch.yaml），**不会进版本库**。")
                print("    但承诺是分段上界、分段来自表——表不在库里，承诺就不可复现，")
                print("    `cost --diff` 也比不了历史（会报「口径不可比」）。")
                print(f"    要让它进库：git add -f {os.path.basename(_tblp)}")
        except Exception:                                  # noqa: BLE001
            pass
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
        if not _abort_on_parse_errors(tu_p, ch_p):
            return 2
        fn, rest = argv[1], argv[2:]
    tu_p = _resolve_module_path(tu_p) if tu_p else None
    if tu_p is None:
        print(f"✗ 找不到模块 {argv[0]}", file=sys.stderr)
        return 2
    tu_mod = parse_source(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = parse_source(io_read(ch_p), os.path.basename(ch_p)).module
    # **必须传分档表**：承诺（piecewise）按这张表切；运行期不读表就会退回
    # 第一个候选 → 承诺与运行不同源 → 系统性破界（实测过 -341 / 逐段 402 实测 733）。
    it = Interp(ch_mod, tu_mod, table_path=_tbl_for(tu_p))

    # **`--vec` / `--u64` 与 `run` 同一套注入**（批次 5 第 21 条）。
    # 以前这些开关被当成普通字符串，于是 `stats sum --vec=5,5,5,5` 把
    # `--vec=5,5,5,5` 编码成 13 个字节当实参——结果与输入无关，还不报错。
    vec_args: List[List[int]] = []
    num_args: List[int] = []
    for a in rest:
        if a.startswith("--vec="):
            body = a.split("=", 1)[1].strip()
            try:
                vec_args.append([int(x) for x in body.split(",") if x.strip() != ""])
            except ValueError:
                print(f"✗ --vec 的元素必须是整数：{a}", file=sys.stderr)
                return 2
        elif a.startswith("--u64="):
            try:
                num_args.append(int(a.split("=", 1)[1]))
            except ValueError:
                print(f"✗ --u64 的值必须是整数：{a}", file=sys.stderr)
                return 2

    args = []
    if vec_args or num_args:
        spec = _spec_of(tu_mod, fn)
        if spec is None:
            print(f"✗ 找不到函数 {fn} 的 spec，无法按 `--vec` / `--u64` 装配实参",
                  file=sys.stderr)
            return 2
        args, _arg_err = _assemble_cli_args(spec, vec_args, num_args,
                                            getattr(it, "globals", {}))
        if _arg_err:
            print(f"✗ {_arg_err}", file=sys.stderr)
            return 2
    else:
        # 兼容旧写法：`stats <模块> <函数> 1 2 3`
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
    _ret = _spec_ret_name(tu_mod, fn)
    print(f"  {fn}({len(args)} 个实参) → {_display_return(val, _ret)}")
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
    # **按扩展名猜格式**：`--out=x.otel` / `.otlp` / `.otlp.json` → OTLP。
    # 以前必须记着加 `--format=otlp`，忘了就写出一份 CSV 却叫 .otel 的文件。
    if not any(_a.startswith("--format=") for _a in argv):
        for _a in argv:
            if _a.startswith("--out="):
                _low = _a.split("=", 1)[1].lower()
                if _low.endswith((".otel", ".otlp", ".otlp.json")):
                    argv = argv + ["--format=otlp"]
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
        # **`--timing` 与 `--full` 不同单位**：报告三节全按操作数讲（κ 与 ops 同单位），
        # 而 `--timing` 量的是纳秒。混起来就会出现「纳秒÷操作数」这种没意义的数。
        # 与其算个假比值，不如明确拒绝——**不许静默忽略参数**。
        if "--timing" in argv:
            print("✗ `--full` 不支持 `--timing`：报告按操作数讲（与 κ 同单位），纳秒与它不可换算。要纳秒请用 `elench export <模块> --timing`。", file=sys.stderr)
            return 2
        # **OTLP 有现成实现**（`otel.to_otlp`，非 --full 那条路在用）：
        # `--full` 只是报告形态，不该在这一步另写一份。直接交给 OTLP 路径。
        # 修前实测：`--full --out=t.otel` 不报错、也不出 .otel——
        # SVG 分支把 out_path 当基名，写出了 `t-图.svg`。
        _o0 = next((a.split("=", 1)[1] for a in argv if a.startswith("--out=")), "")
        if (_o0.lower().endswith((".otel", ".otlp", ".otlp.json"))
                or "--push" in argv):
            return cmd_export([a for a in argv if a not in ("--full", "--report")])
        _sizes = None            # None = 由模块的 benchmodel.scale 决定
        _runs = 20
        _pos = []
        _out = None
        _split = "--split" in argv
        _variant = "worst"
        # **时间戳只影响 `.html`**：`--now=` 原样写进报告（方便逐字节复现），
        # `--no-timestamp` 写成固定标记。两条都是给测试与 CI 用的。
        _now = None
        _no_ts = "--no-timestamp" in argv
        for _a in argv:
            if _a.startswith("--variant="):
                _variant = _a.split("=", 1)[1]
        for _a in argv:
            if _a.startswith("--sizes="):
                _sizes = [int(x) for x in _a.split("=", 1)[1].split(",") if x.strip()]
            elif _a.startswith("--runs="):
                _runs = int(_a.split("=", 1)[1])
            elif _a.startswith("--out="):
                _out = _a.split("=", 1)[1]
            elif _a.startswith("--now="):
                _now = _a.split("=", 1)[1].strip() or None
            elif not _a.startswith("--"):
                _pos.append(_a)
        if not _pos:
            print("用法：elench export <模块> --full [--sizes=…] [--runs=20] "
                  "[--out=图.svg|表.md|表.csv|报告.html] [--split] "
                  "[--now=时间戳] [--no-timestamp]", file=sys.stderr)
            return 2
        _tu_probe, _ = _load_pair(_pos)
        if _tu_probe:
            _rc = _check_tu_accepted(_tu_probe, argv)
            if _rc:
                return _rc
        _max = None
        _maxp = None
        for _a in argv:
            if _a.startswith("--max-size="):
                _max = int(_a.split("=", 1)[1])
            elif _a.startswith("--max-points="):
                _maxp = int(_a.split("=", 1)[1])
        return _full_report(_pos[0], _sizes, _runs, out_path=_out, split=_split,
                            max_size=_max, max_points=_maxp, variant=_variant,
                            now=_now, no_timestamp=_no_ts,
                            tool_version=VERSION)

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
        elif _ext == "html":
            # **`.html` 只在 `--full` 下生成**：不加 `--full` 会写成表格文本。
            # 与其静默写一个"叫 .html 但不是网页"的文件，不如说出来。
            print("  ！ `.html` 报告需要 `--full`："
                  "`elench export <模块> --full --out=报告.html`（本次按表格写）",
                  file=sys.stderr)

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
    _rc = _check_tu_accepted(tu_p, argv)
    if _rc:
        return _rc
    tu_mod = parse_source(io_read(tu_p), os.path.basename(tu_p)).module
    ch_mod = parse_source(io_read(ch_p), os.path.basename(ch_p)).module
    if _recursion_gate(tu_mod, ch_mod):
        return 1
    if not _table_covers_declared(tu_mod, tu_p,
                                  strict=("--strict-coverage" in argv)):
        return 1
    ci = _cost_interp(ch_mod, tu_mod, tu_p, force_max=("--variant=max" in argv))

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
    if label and _has_tbl and _size_var(cands):
        # **分段必须取自运行期那份表**（S2 同源）：静态 plan() 算的是"模型最优分段"，
        # 它和表可以不一致（真机常数不同）。用 plan() 画承诺 = 承诺与运行不同源。
        from interp import load_dispatch_table
        _tb = (load_dispatch_table(_tbl_for(tu_p)) or {}).get(label) or []
        from cost_check import evaluate as _ev2
        for _n in pts:
            for _lo, _hi, _who in _tb:
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
        if not _abort_on_parse_errors(tu_p, ch_p):
            return 2
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
    # 同上：bench 测的是「运行期真正会跑的那个候选」，必须读同一张表。
    interp = Interp(ch_mod, tu_mod, table_path=_tbl_for(tu_p))
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


def _check_opts(argv):
    """`check` / `run` / `build` 共用的检查选项：strict / overflow / relax。

    **一处解析，三处一致**：以前 `build` 不认 `--strict`，于是
    `check --strict` 报 error（E-While-NoBound）而 `build --strict` 照样
    "✓ 构建通过"——门禁的口径必须由同一个函数决定。
    """
    strict = "--strict" in argv
    overflow = "off"
    for a in argv:
        if a.startswith("--checks="):
            for part in a.split("=", 1)[1].split(","):
                if ":" in part:
                    k, v = part.split(":", 1)
                    if k.strip() == "overflow" and v.strip() in ("off", "warn", "error"):
                        overflow = v.strip()
    relax = [a.split("=", 1)[1] for a in argv if a.startswith("--relax=")]
    return strict, overflow, relax


def _gate_checks(tu_p, ch_p, argv) -> bool:
    """跑 `check_module`；有 error 就拒绝继续（`run` / `build` 共用）。

    为什么必须在这里卡：审计实测过 —— `func f() -> u64 { return true; }`
    能 `build` 出"✓ 构建通过"，还能 `run` 出 `True`。
    **一个会说"通过"的门禁，比缺功能严重得多。**

    `--strict` / `--checks=overflow:…` / `--relax=…` 与 `check` 同口径
    （2026-11-09）：以前 build 忽略 `--strict`，`check --strict` 的红灯
    在 build 里是绿的。warning **不**在这里拦（`check` 也只拦 error）。
    """
    if "--no-check" in argv:
        print("⚠ --no-check：跳过静态检查（仅供调试，输出**不可信**）")
        return True
    strict, overflow, relax = _check_opts(argv)
    r = _diagnose_all(tu_p, ch_p if (ch_p and os.path.exists(ch_p)) else None,
                      strict=strict, overflow=overflow, relax=relax)
    errs = [d for d in r.diagnostics if d.severity == "error"]
    if errs:
        print(f"✗ 未通过：{len(errs)} 条错误（先修完再 run/build）", file=sys.stderr)
        for d in errs[:5]:
            print(f"  {d.code} @{d.line}:{d.col} {d.message[:70]}", file=sys.stderr)
        return False
    return True


_UNSIGNED_TYS = ("u8", "u16", "u32", "u64")
_UNSIGNED_WIDTHS = {"u8": 8, "u16": 16, "u32": 32, "u64": 64}


def _spec_of(tu_mod, fn_name):
    """在 `.tu` 里找某个函数的 spec 节点。"""
    return next((c for c in (tu_mod.children if tu_mod else [])
                 if c.kind == "spec" and (c.name or "") == fn_name), None)


def _spec_params(spec) -> List[object]:
    if spec is None:
        return []
    for c in spec.children:
        if c.kind == "params":
            return list(c.children)
    return []


def _spec_ret_name(tu_mod, fn_name: str) -> str:
    """spec 声明的返回类型名（`u64` / `u64!` 的 `u64`）；没有 spec 就空串。"""
    from checker import _return_of, type_str
    spec = _spec_of(tu_mod, fn_name)
    if spec is None:
        return ""
    try:
        return canon(type_str(_return_of(spec)))
    except Exception:                                  # noqa: BLE001
        return ""


def _display_return(val, ret_name: str):
    """按**声明返回类型**显示值（批次 5 第 18 条）。

    2026-11-08 起 `u64` 的值在值层就是**规范无符号值**（`UInt`），所以正常
    路径不需要这里的补救；保留这一层是为了兜住"从宿主/旧路径进来的补码负数"
    （`stats` 的某些注入值），不要把 `-1` 打给用户。
    """
    if isinstance(val, int) and not isinstance(val, bool) \
            and ret_name in _UNSIGNED_TYS and val < 0:
        return val + (1 << _UNSIGNED_WIDTHS[ret_name])
    return val


def _param_type_node(prm):
    return next((c for c in prm.children
                 if c.kind in ("type", "type-fail", "type-ref", "type-name")), None)


def _assemble_cli_args(spec, vec_args, num_args, consts=None):
    """按 spec 的形参顺序装配 CLI 实参 → `(args, error)`。

    **`--vec` 必须与定长形参的长度一致**（批次 5 第 20 条）：以前给 2 个
    会在函数体里 `IndexError`，给 5 个会被 Python 列表**静默接受**（越界元素
    永远读不到）——两种都是宿主行为泄漏。`vector<T>` 没有声明长度，不查。
    """
    from checker import type_str
    from interp import _fixed_capacity

    out: List[object] = []
    vi = ni = 0
    for prm in _spec_params(spec):
        ty = _param_type_node(prm)
        tnode = ty
        if ty is not None and ty.kind == "type-ref" and ty.children:
            tnode = ty.children[0]
        head = canon((tnode.name or "") if tnode is not None else "")
        if (ty is not None and ty.kind == "type-ref") \
                or head.startswith("vector") or head.startswith("array"):
            if vi >= len(vec_args):
                return None, f"参数 {prm.name} 是向量，需要 `--vec=…`"
            vals = vec_args[vi]
            vi += 1
            if head in ("array", "fixedvec"):
                cap = _fixed_capacity(tnode, consts or {})
                if cap is not None and len(vals) != cap:
                    _ty = type_str(ty) if ty is not None else "array<…>"
                    return None, (
                        f"参数 {prm.name} 的类型是定长 `{_ty}`，"
                        f"需要 {cap} 个元素，`--vec` 给了 {len(vals)} 个")
            out.append(vals)
        else:
            if ni >= len(num_args):
                return None, f"参数 {prm.name} 是标量，需要 `--u64=…`"
            out.append(num_args[ni])
            ni += 1
    if vi < len(vec_args):
        return None, (f"多给了 {len(vec_args) - vi} 个 `--vec`："
                      f"该函数只有 {vi} 个容器形参")
    if ni < len(num_args):
        return None, (f"多给了 {len(num_args) - ni} 个 `--u64`："
                      f"该函数只有 {ni} 个标量形参")
    return out, None


def _compat_resolve_auto(tu_mod, ch_mod) -> None:
    """`--compat`：把 `auto` 推断成**具体类型**（第 3 条），就地改 AST。

    `parse_source` 已经做过一遍"只见本文件"的尽力推断；这里再来一遍**成对**
    的（`.tu` 的 `spec` 也看得见），因为 `run` / `cost` / `build` 手里的
    模块是各自解析出来的，`_diagnose_all` 里那棵改不到它们。

    推断实现只有一份（`typecheck._compat_auto_ty`）；这里吞掉异常与诊断：
    真错误由 `check` 口径统一报，`run` 不该因为"诊断没法排版"崩掉。
    """
    import compat as _compat_flag
    if not _compat_flag.enabled() or ch_mod is None:
        return
    try:
        from typecheck import resolve_auto as _resolve
        _resolve(ch_mod, tu_mod)
    except Exception:                                    # noqa: BLE001
        pass
    # **第 11 条**（2026-11-11）：可无损折叠的 `static_cast` 也在这里落成
    # 实参（`.tu` 里的类型也看得见）。`parse_source` 已经尽力折过一遍；
    # 折不动的（窄化 / 推不出）保持原节点，由 `check` 报 `E-Cast-Unsupported`。
    try:
        from typecheck import resolve_static_cast as _resolve_casts
        _resolve_casts(ch_mod, tu_mod)
    except Exception:                                    # noqa: BLE001
        pass


def cmd_run(argv: List[str]) -> int:
    """`elench run <模块>`：**跑起来**，把 `main` 的结果打出来。

    **为什么必须有这个命令**：没有它，入门文档的第一句"写个 hello 然后运行"
    就写不下去——用户只能 `check`，永远看不到程序输出。

    ## 外部库：`--lib-dir=<目录>`（可重复，2026-11-14）

    `extern cJSON_Version() -> vector<u8> from "cjson" effects {};` 里的库名
    以前**原样**丢给 `ctypes.CDLL`。Python 3.8+ 的 `CDLL` 对裸名**既不搜 cwd
    也不搜 PATH**，于是同一个 `.eln` 换成绝对路径就能跑、写裸名就报
    `Could not find module 'cjson'`——解释器侧只能「无法对拍」。

    现在按 `ffi_c.library_candidates` 的**固定顺序**解析（写进文档，别让人猜）：

    | # | 候选 |
    |---|---|
    | 1 | `from "…"` 写的是绝对路径 / 带路径分隔符 → 原样用，不补后缀 |
    | 2 | 每个 `--lib-dir`（按给出顺序）下 `<名字><平台后缀>`（`.dll` / `.so` / `.dylib`） |
    | 3 | 同一目录下 `<名字>`（不补后缀） |
    | 4 | 最后把裸名原样交给平台加载器（`ucrtbase` / `libc` 这类**系统库**只能这样找） |

    **不额外搜 cwd、不额外搜 PATH**：候选表里没有这两项；第 4 步是平台加载器
    自己的规则，排在所有 `--lib-dir` 之后，所以给了 `--lib-dir` 就一定优先。
    加载失败时错误消息**列出试过的每个路径**，并区分「找不到该文件」与
    「文件在、加载失败」（位数 / 依赖不对）。
    """
    argv = _expand_module_args(argv)
    from parser import parse_source
    from interp import Interp, Ok, Err

    # **外部库搜索目录**先解析（纯命令行，不碰模块）：空串 / 不是目录当场
    # rc=2——指过去也找不到库，让它晚一步变成「对拍无法完成」只是更难查。
    lib_dirs: List[str] = []
    for a in argv:
        if a.startswith("--lib-dir="):
            try:
                lib_dirs.append(_lib_dir(a.split("=", 1)[1]))
            except ValueError as exc:
                print(f"✗ {exc}", file=sys.stderr)
                return 2

    tu_p, ch_p = _load_pair(argv)
    if tu_p is None or not ch_p:
        print("用法：elench run <模块> [--fn=函数名] [--u64=5] [--vec=1,2,3] "
              "[--lib-dir=<目录>]…", file=sys.stderr)
        return 2
    # **没有 `.tu` 也能跑**（规格 §8.9）：`tu_mod` 给一个空模块——没有 spec
    # 就没有契约，`run` 不该因此被挡住。上面 `_gate_checks` 同口径（F-1 只 warning）。
    _bare = _no_tu(tu_p, ch_p)
    tu_mod = parse_source("" if _bare else io_read(tu_p),
                          os.path.basename(tu_p)).module
    ch_mod = parse_source(io_read(ch_p), os.path.basename(ch_p)).module
    # **第 3 条**：`auto x = …;` 在这里落成具体类型（`run` 用**这一份** AST）
    _compat_resolve_auto(tu_mod, ch_mod)
    if not _gate_checks(tu_p, ch_p, argv):
        return 1
    if _bare:
        print("  · 本模块没有 .tu：成本契约不参与判定（run 照常执行）",
              file=sys.stderr)

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
    it = Interp(ch_mod, tu_mod, parallel=_par, table_path=_tbl_for(tu_p),
                lib_dirs=lib_dirs)
    # **预算耗尽时不要只喊"死循环"**（2026-11-09）：指数回溯被 500 万步挡住时，
    # 旧文案把人指向循环变量，真正的问题是算法复杂度。这里挂一个**惰性**顾问，
    # 只有超预算才跑 `cost` 侧已有的树递归判定（`_note_tree_rec`）。
    it.budget_advisor = _tree_rec_budget_advisor(ch_mod, tu_mod, tu_p, fn_name)
    if fn_name not in it.fns:
        print(f"✗ 找不到函数 {fn_name}（模块里有：{', '.join(sorted(it.fns)) or '无'}）",
              file=sys.stderr)
        print("  提示：`--fn=名字` 可以指定入口", file=sys.stderr)
        return 2
    # **实参组装**：按声明顺序，参数是 `Vec<…>` 就吃一个 `--vec`，否则吃一个 `--u64`。
    spec = _spec_of(tu_mod, fn_name)
    if spec is None:
        # **没有 spec（没有 `.tu`）时按实现签名装配**（2026-11-10）：产品目标是
        # 「C++ 函数改文件名就能跑」，`run --fn=solve --u64=5` 不该因为缺 `.tu`
        # 被判成"多给了实参"。实现节点的 `params` 与 spec 的结构一致
        # （`_param_type_node` 两者都能读），所以直接复用装配逻辑。
        spec = it.fns.get(fn_name)
    call_args, _arg_err = _assemble_cli_args(
        spec, vec_args, num_args, getattr(it, "globals", {}))
    if _arg_err:
        print(f"✗ {_arg_err}", file=sys.stderr)
        return 2

    from interp import ExitRequest  # 局部导入：dsh 顶层不 import interp
    try:
        val = it.call(fn_name, call_args)
    except ExitRequest as _ex:
        # **`exit(code)` 是正常控制流**，不是错误：码原样交给 shell
        return _ex.code
    except RecursionError:
        # **宿主异常不许外泄**（2026-11-10）：`Interp.call` 已把最常见的
        # `RecursionError` 翻成 `E-Recursion-HostLimit`；能走到这里的只剩
        # 「崩在 `call()` 之外」（返回值渲染 / 实参装配）。仍然给语言诊断，
        # 不甩一句 `RecursionError: maximum recursion depth exceeded`。
        import interp as _ip
        print(f"✗ 运行失败：InterpError: {_ip.E_RECURSION_HOST_LIMIT}："
              f"递归深度超过**宿主上限**——宿主 Python 调用栈"
              f"（`sys.setrecursionlimit` = {sys.getrecursionlimit()} 帧）已用光。\n"
              f"  修法：① 在 spec 里写真实上界 `recursion depth <= N;`"
              f"（引擎按声明抬宿主上限，超过 N 报 "
              f"`{_ip.E_RECURSION_DEPTH_EXCEEDED}`）；② 把递归改写成迭代",
              file=sys.stderr)
        return 1
    except Exception as exc:                            # noqa: BLE001
        print(f"✗ 运行失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    _ret = _spec_ret_name(tu_mod, fn_name)
    if isinstance(val, Ok):
        _v = _display_return(val.v, _ret)
        print(_v if not isinstance(_v, (list, tuple)) else list(_v))
        return 0
    if isinstance(val, Err):
        print(f"Err({val.e})", file=sys.stderr)
        return 1
    print(_display_return(val, _ret))
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
    if tu_p is None:
        print("用法：dsh build <模块.tu> [<模块.eln>] [--out=<路径>]", file=sys.stderr)
        return 2
    # **`.tu` 可选**（2026-11-10，规格 §8.9）：没有 `.tu` 时声明源为空——
    # 不把 `.eln` 当 `.tu` 解析第二遍，`check` 阶段的 F-1 也只汇总一条。
    # 没有契约不等于构建失败：build 照常产出成本表（target 全为空）。
    _bare = _no_tu(tu_p, ch_p)
    tu_src = "" if _bare else io_read(tu_p)
    ch_src = io_read(ch_p) if (ch_p and os.path.exists(ch_p)) else None
    module = os.path.basename(tu_p).rsplit(".", 1)[0]

    # **门禁第零关**：声明有没有被静默改过（含自动链：build 会先跑 dispatch/measure）
    # 没有 `.tu` 就没有声明文件可保护，跳过这道闸（它本来也只是"防静默放宽"）。
    _rc = 0 if _bare else _check_tu_accepted(tu_p, argv)
    if _rc:
        return _rc

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
                                _tbl_for(tu_p))
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

    _strict, _overflow, _ = _check_opts(argv)
    with _otel_span("build", {"code.module": module}):
        res = check_module(tu_src, ch_src, module, strict=_strict,
                           overflow=_overflow, has_tu=not _bare)
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
        ci = _cost_interp(ch_mod, tu_mod, tu_p, force_max=("--variant=max" in argv))
        for name in ci.fns:
            try:
                c = ci.cost_of(name)
            except Exception as e:                   # noqa: BLE001
                table["functions"][name] = {"cost_expression": None,
                                            "verdict": "unknown",
                                            "reason": str(e)}
                continue
            if _bare:
                # **没有 `.tu` = 没有契约**（规格 §8.9）：不要写成"unknown"——
                # 那会被读成"契约判不出来"，而事实是"没写契约、也不该判"。
                table["functions"][name] = {
                    "cost_expression": c.render(),
                    "target": None,
                    "verdict": "undeclared",
                    "verdict_explanation": "未声明契约（本模块没有 .tu）；"
                                           "κ 照常记录，不参与判定",
                }
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
    if _bare:
        print(f"  · 本模块没有 .tu：{len(table['functions'])} 个函数未声明契约"
              f"（成本表 target 为空，不计债务、不算错误）")

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
    # **成本门禁：先算结论，再打唯一一条结论**（2026-11-09）。
    # 以前 "✓ 构建通过" 打在 deny 判定之前：`--deny-debt` / `--deny-unknown`
    # 会先打 ✓ 再打 ✗，同一份输出里两个相反结论。
    #
    # 口径（与 `check` 的 error/warning 分开）：
    #   · `check` 的 **error** → 上面 `_gate_checks` 已经 return 1；
    #   · `check` 的 **warning** → 不拦（`check` 也不拦）；
    #   · **debt**（超出 target）→ 默认只记录；`--deny-debt` 拒绝；
    #   · **unknown**（κ=⊤ / 判不出）→ 默认放行但**必须警告**；
    #     `--deny-unknown` 拒绝；`--deny-debt` 也拒绝（全函数门禁，2026-11-06）。
    #
    # **H1（2026-11-02）**：过去只比 time，`allocation: {max:0}` + 每轮分配
    # 的程序照样 exit 0。门禁必须**逐个维度**判：time 用 compare（按阶），
    # allocation 用 compare_exact（按数量）。
    _debt, _unknown = [], []
    if ch_mod is not None:
        from cost_check import compare as _cmp, compare_exact as _cmpe
        # **维度表**（P2，2026-11-06）：加第三个维度只需加一行，门禁本体不动。
        # 标签沿用历史的 `alloc`（报表里已经是这个名字）；
        # **比较策略必须逐维不同**：time 按阶、allocation 按数值——不能统一。
        _GATE_DIMS = (
            ("time", lambda _ci, _n: _ci.cost_of(_n),
             lambda _ci, _n: _ci.target_cost(_n), _cmp),
            ("alloc", lambda _ci, _n: _ci.alloc_of(_n),
             lambda _ci, _n: _ci.target_cost(_n, "allocation"), _cmpe),
        )
        _r5 = "R5" in _relax_names([a.split("=", 1)[1] for a in argv
                                    if a.startswith("--relax=")])
        for _n in ci.fns:
            for _dim, _actual, _target, _cmpf in _GATE_DIMS:
                try:
                    _tv = _target(ci, _n)
                except Exception:                      # noqa: BLE001
                    _tv = None
                # **没有该维度的 target ⇒ 不适用**（没承诺就不用守），
                # 不能算成 unknown——否则每个没写 allocation 的函数都会被拒绝。
                if _tv is None:
                    continue
                try:
                    _v = _cmpf(_actual(ci, _n), _tv)
                except Exception:                      # noqa: BLE001
                    _v = None
                if _v is None:
                    continue
                # **R5 放宽**：不要求建模分配 ⇒ 允许该维度"未知"。
                # 但已知超支（debt）仍然拒绝——放宽的是"要不要建模"，不是"能不能超"。
                if _dim == "alloc" and _r5 and _v.kind == "unknown":
                    continue
                if _v.kind == "debt":
                    _debt.append(f"{_n}[{_dim}]")
                elif _v.kind == "unknown":
                    _unknown.append(f"{_n}[{_dim}]")

    _deny_debt = "--deny-debt" in argv
    _deny_unknown = ("--deny-unknown" in argv) or _deny_debt
    if _deny_debt and _debt:
        print(f"✗ 拒绝构建：{len(_debt)} 处超出 target（--deny-debt）："
              + ", ".join(sorted(_debt)))
        _mod_b = os.path.splitext(os.path.basename(tu_p))[0]
        _print_restarts(_cost_restarts(_mod_b), indent="  ")
        return 1
    # **H2**：`--deny-unknown` 把"判不出来"变成拒绝。
    # 默认仍放行（渐进采用），但 CI 必须能要求"不许有未知"——
    # 否则"不知道"会被当成"没问题"。
    # **只用 --deny-debt 会漏掉 κ=⊤ 的程序**（unknown 不是 debt）。
    # **门禁必须是全函数**（2026-11-06）：`--deny-debt` 同样拒绝 unknown。
    if _deny_unknown and _unknown:
        _how = ("--deny-unknown" if "--deny-unknown" in argv
                else "--deny-debt 也拒绝 unknown")
        print(f"✗ 拒绝构建：{len(_unknown)} 处无法判定（{_how}）："
              + ", ".join(sorted(_unknown)))
        print("  出路：写出可判的 target（含同样的尺寸量）、或补 `benchmodel` 的 scale / `repeats`。",
              file=sys.stderr)
        return 1
    # **默认（宽松）必须把"没判"说出来**：以前 κ=⊤ 时 build 一声不吭地
    # "✓ 构建通过"，读者以为契约判过了。警告不改变退出码。
    if _unknown:
        print(f"⚠ {len(_unknown)} 处无法判定（κ=⊤）：默认放行；"
              f"发布/CI 用 --deny-unknown 拒绝：" + ", ".join(sorted(_unknown)))
    if _debt:
        print(f"⚠ {len(_debt)} 处超出 target（债务）：默认只记录、放行；"
              f"发布/CI 用 --deny-debt 拒绝：" + ", ".join(sorted(_debt)))
    print("✓ 构建通过（无不完整候选）")
    if _debt or _unknown:
        print("  注：上面是警告，退出码 0。")
    return 0


VERSION = "1.0.0"
ARGV_VERSION = ("--version", "-V", "version")


def _write_table(rows, path: str) -> int:
    """把测量行写成数据表：`.md` 出 Markdown，`.csv` 出逗号分隔。

    列固定：函数 / 规模 / κ / 实测 / 相对（κ÷实测）/ 承诺上限。
    `相对` 为空表示该点没测到——**不写 0**，因为 0 会被读成"成本为零"。
    """
    import csv as _csv
    cols = ["函数", "规模", "κ", "实测", "相对(κ÷实测)", "承诺上限"]
    body = []
    for r in rows:
        rel = (r["bound"] / r["typical"]) if r.get("typical") else None
        body.append([
            r.get("function", ""),
            r.get("size", ""),
            r.get("bound", ""),
            r.get("typical", ""),
            f"{rel:.2f}" if rel is not None else "",
            "" if r.get("target") is None else r["target"],
        ])
    if path.lower().endswith(".csv"):
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            w = _csv.writer(fh)
            w.writerow(cols)
            w.writerows(body)
    else:
        out = ["| " + " | ".join(cols) + " |",
               "|" + "|".join(["---"] * len(cols)) + "|"]
        for row in body:
            out.append("| " + " | ".join(str(c) for c in row) + " |")
        io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")
    print(f"已写出表：{path}（{len(body)} 行）")
    return 0



def _measure_curve(prog: str, sizes, runs: int):
    """**唯一的测量入口**：跑程序、拿 `(n, κ, ops, slack)`，按规模逐点。

    两个命令（`export --full` 与即将删除的 `tightness`）本来各写一遍同样的循环，
    容易漂（默认 runs 不同、跳行条件不同）。现在只此一处。

    `slack = κ / ops`；`ops <= 0` 的点直接跳过——除零没有意义，
    而且它表示"这次没测到"，不是"成本为 0"。
    """
    from kappa_vs_measured import measure_one
    out = []
    for size in (sizes or [16, 64, 256]):
        for r in measure_one(prog, size, runs):
            if r.get("error") or "max_actual" not in r:
                continue
            ops = float(r["max_actual"] or 0)
            if ops <= 0:
                continue
            kv = float(r.get("kappa_at_n") or 0)
            out.append({
                "function": r["function"],
                "n": size,
                "kappa": kv,
                "ops": ops,
                "slack": (kv / ops) if ops else None,
                "target": r.get("target_at_n"),
            })
    return out



def _full_report(module_arg: str, sizes, runs, wc_path=None, out_path=None,
                 split: bool = False, max_size=None,
                 max_points=None, variant: str = "worst", now=None,
                 no_timestamp: bool = False, tool_version: str = "") -> int:
    """**完整报告**：上界、实测、紧度**分三节**，每节一张表。

    刻意不用缩写：`typical / worst / slack / ops` 一律写全（典型、最坏、松度、操作数），
    也不把三件事挤进一张表——那会让人以为它们是同一层的量。
    """
    # `kappa_vs_measured` 就在 `src/`（与本文件同级），直接 import 即可。
    # 以前只往 sys.path 里插 `../tools`——而 `tools/` 不进任何安装产物
    # （wheel / .pyz / .vsix / 便携包），于是装好的包里 `tightness` 与
    # `export --full` 一律 ModuleNotFoundError。`../tools` 只作老布局的退路。
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tools_dir = os.path.join(root, "tools")
    try:
        from kappa_vs_measured import measure_one      # noqa: E402
    except Exception:                                  # noqa: BLE001
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        try:
            from kappa_vs_measured import measure_one  # noqa: E402
        except Exception as exc:                       # noqa: BLE001
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

    _curve = _measure_curve(prog, sizes, runs)
    rows = []
    for _c in _curve:
        rows.append({
            "function": _c["function"],
            "size": _c["n"],
            "bound": _c["kappa"],
            "target": _c["target"],
            "typical": _c["ops"],
            "alloc_bound": None,
            "alloc_target": None,
            "allocs": 0,
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
    if variant == "random":
        # **A3 消融基线**：不跑最坏搜索，紧度分母用"典型"（随机输入）。
        # 与默认（搜最坏）对照，就能量出"搜最坏"这一步值多少。
        worst = {}
        print("（--variant=random：不搜索最坏输入，紧度按**典型**算——"
              "这不是承诺的判据，仅供消融对照）")
    try:
        from worstcase import worst_for as _worst_for
        for _r in (rows if variant != "random" else []):
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
        # **无法判定就不给比值**（2026-11-08）。
        # 以前这里直接除：`bound` 是 ⊤ 的哨兵值时打印出 `2.79e37`、
        # `2572857209000975.00` 这类噪声，读者会当成"真的差这么多倍"。
        # 分母为 0（实测没跑起来）也一样。
        _undecidable = (not r.get("typical")) or (r.get("bound") or 0) >= 1e18
        slack_typical = (None if _undecidable
                         else r["bound"] / r["typical"])
        if w and w.get("ops_worst_obs") and not _undecidable:
            tight_worst = r["bound"] / float(w["ops_worst_obs"])
            tstr = f"{tight_worst:.2f}"
            recipe = str(w.get("recipe", ""))[:20]
        else:
            tstr, recipe = "（未搜索）", "—"
        _st = "—（无法判定）" if slack_typical is None else f"{slack_typical:.2f}"
        if slack_typical is None:
            tstr = "—"
        print(f"{r['function']:<24}{r['size']:>10}{_st:>12}{tstr:>12}{recipe:>22}")

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
    # **`.html` 后缀 = 静态归档报告**（2026-11-10）：数据是**同一份 rows**，
    # 渲染逻辑在 `src/html_report.py`（复用 `chart.line_chart` 画曲线）。
    # 这里只做**后缀分派**，不写第二套渲染。
    if out_path and out_path.lower().endswith(".html"):
        from html_report import write_report
        return write_report(out_path, prog, rows, sizes=sizes, runs=runs,
                            sizes_src=_src, now=now, no_timestamp=no_timestamp,
                            tool_version=tool_version)
    if now or no_timestamp:
        print("  ！ --now= / --no-timestamp 只对 `--out=*.html` 有意义，"
              "本次没有 HTML 输出，已忽略")
    try:
        # **按后缀分流**（`.md` / `.csv`）：以前只认 `.svg`，给个 `x.md` 会把 SVG 内容写进去。
        # 控制台的三节报告仍然照旧；这里只是把**同一份 rows** 另存成数据表。
        if out_path and out_path.lower().endswith((".md", ".csv")):
            return _write_table(rows, out_path)

        from chart import line_chart, stack_svgs
        _base = os.path.splitext(os.path.basename(module_arg.rstrip("/\\")))[0]
        # **不给 `--out` 时，图写在模块旁边，不写进当前目录**。
        # 以前用 `os.getcwd()`，于是仓库根目录会冒出 `pick-图.svg` / `blur-图.svg`
        # 这类"谁生成的？"的孤儿文件（2026-11-07 查出来的）。
        _out_base = (os.path.splitext(out_path)[0] if out_path
                     else os.path.join(os.path.dirname(module_arg.rstrip("/\\")) or ".", _base))
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
                [("承诺（候选取最大）", _bound)] + ([("target（阶承诺，不含常数）", _tgt)] if _tgt else [])
                + [("典型实测", _typ), ("最坏实测", _worst)],
                f"{_fn}：上界与实测", "操作数", log_y=True,
                direct_labels=True)

            _st = [(_xs[i], _rs[i]["bound"] / _rs[i]["typical"])
                   for i in range(len(_rs))]
            _sw = []
            for i in range(len(_rs)):
                _w = worst.get((_rs[i]["function"], _rs[i]["size"]))
                if _w and _w.get("ops_worst_obs"):
                    _sw.append((_xs[i], _rs[i]["bound"] / float(_w["ops_worst_obs"])))
            # 面板②：**超额倍数**（κ ÷ target）——作者承诺"至多 O(n)"，
            # 这里看"常数意义上超了几倍"。紧度在报告第三节与面板③。
            _over = []
            for _i in range(len(_rs)):
                _t = _rs[_i].get("target")
                if _t:
                    _over.append((_rs[_i]["size"], _rs[_i]["bound"] / float(_t)))
            _svg2 = line_chart(
                [("超额倍数（κ ÷ target）", _over)],
                f"{_fn}：超额倍数（>1 = 常数意义上超了）", "上界 ÷ 承诺上限",
                log_y=True, hline=1, annotate=True)

            # 面板③：各点紧度（数值标注）+ n0 竖线
            _v3 = []
            for _i in range(len(_rs)):
                _w3 = worst.get((_rs[_i]["function"], _rs[_i]["size"]))
                if _w3 and _w3.get("ops_worst_obs"):
                    _v3.append((_rs[_i]["size"],
                                _rs[_i]["bound"] / float(_w3["ops_worst_obs"])))
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

            # 面板④⑤⑥：空间（分配）——与左边时间面板一一对应
            _ab = [(z["size"], z["alloc_bound"]) for z in _rs
                   if z.get("alloc_bound") is not None]
            _at = [(z["size"], z["alloc_target"]) for z in _rs
                   if z.get("alloc_target") is not None]
            _am = [(z["size"], max(1, z.get("allocs") or 0)) for z in _rs]
            _svg4 = line_chart(
                [("分配上界 κ_alloc", _ab)]
                + ([("分配上限 target", _at)] if _at else [])
                + [("实测分配次数", _am)],
                f"{_fn}：空间（分配）", "分配次数", log_y=True)
            _aover = []
            for z in _rs:
                if z.get("alloc_target") and z.get("alloc_bound") is not None:
                    _aover.append((z["size"],
                                   z["alloc_bound"] / float(z["alloc_target"])))
            _svg5 = line_chart(
                [("超额倍数（κ_alloc ÷ target）", _aover)],
                f"{_fn}：分配超额倍数", "分配上界 ÷ 分配上限",
                log_y=True, hline=1, annotate=True,
                note="该模块的 spec 没有 `target { allocation }` 条款，无可比数据")
            _svg6 = line_chart(
                [("各点分配（实测）", _am)],
                f"{_fn}：各点分配（数值标注）", "分配次数",
                log_y=True, annotate=True)

            _dir = os.path.dirname(_out_base) or "."
            _nm = os.path.basename(_out_base)
            if split:
                _f1 = os.path.join(_dir, f"{_nm}-上界与实测.svg")
                _f2 = os.path.join(_dir, f"{_nm}-超额倍数.svg")
                io.open(_f1, "w", encoding="utf-8", newline="\n").write(_svg1)
                io.open(_f2, "w", encoding="utf-8", newline="\n").write(_svg2)
                print(f"已写出图：{_f1}")
                print(f"已写出图：{_f2}")
            else:
                # 一个文件、六张面板：左列时间、右列空间（独立坐标系）
                _f3 = (out_path if out_path and out_path.lower().endswith(".svg")
                       else os.path.join(_dir, f"{_nm}-图.svg"))
                io.open(_f3, "w", encoding="utf-8", newline="\n").write(
                    stack_svgs([_svg1, _svg4, _svg2, _svg5,
                                _svg3, _svg6], columns=2))
                print(f"已写出图：{_f3}（一个文件，左时间右空间）")
    except Exception as _exc:                          # noqa: BLE001
        print(f"（画图失败：{type(_exc).__name__}: {_exc}）", file=sys.stderr)
    return 0


_OTEL_SUB = {
    "sink": "otel_local_sink",
    "view": "otel_view",
    "dashboard": "otel_dashboard",
}


def cmd_otel(argv: List[str]) -> int:
    """`elench otel <sink|view|dashboard> [参数...]` —— 本机看 OTel 数据。

    这三个脚本原先是 `tools/` 下的独立文件，**装了包的人一个都拿不到**；
    而它们恰恰是 `export --push` 唯一的配套观察手段（没有 collector 时）。
    实现仍留在 `src/otel_*.py`（零第三方依赖），这里只做转发：
    参数原样透传，`--help` 等行为与直接跑脚本完全一致。
    """
    if not argv or argv[0] in ("-h", "--help"):
        print("用法：elench otel <sink|view|dashboard> [参数...]")
        print()
        print("  sink       起一个本机 OTLP/HTTP 接收端（默认 127.0.0.1:4318）")
        print("             elench otel sink --out otel.ndjson")
        print("  view       把收到的 NDJSON 按人看得懂的方式打印（按耗时排序、父子缩进）")
        print("             elench otel view otel.ndjson [--slowest]")
        print("  dashboard  把 NDJSON 画成一张自刷新网页")
        print("             elench otel dashboard --file otel.ndjson --port 8080")
        print()
        print("配合 `elench export <模块> --push --out=xxx.otel` 使用：")
        print("  窗口 A：elench otel sink --out otel.ndjson")
        print("  窗口 B：elench export sum --push --out=sum.otel")
        print("  然后：  elench otel view otel.ndjson   或   elench otel dashboard")
        return 0 if argv else 2
    sub, rest = argv[0], argv[1:]
    modname = _OTEL_SUB.get(sub)
    if not modname:
        print(f"✗ 未知子命令：{sub}（可选：sink / view / dashboard）", file=sys.stderr)
        return 2
    import importlib
    m = importlib.import_module(modname)
    old = sys.argv
    try:
        sys.argv = [f"elench otel {sub}"] + rest
        return int(m.main() or 0)
    finally:
        sys.argv = old


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
    except SpecNotFound as exc:
        # `// @spec` 指向的契约不存在（**正确性问题用 error 挡**）：不回退、不静默。
        # `check` 的输出风格跟它的其它错误一致（`✗ N 条错误` + `✗ 码 @行:列`）；
        # 其余子命令与机读模式走 stderr，避免污染 `--json` / `--out` 的 stdout。
        _is_check = len(argv) > 1 and argv[1] == "check"
        _machine = any(a in ("--json", "--format=json", "--signature", "--yaml")
                       for a in argv)
        _stream = sys.stdout if (_is_check and not _machine) else sys.stderr
        _lines = exc.lines()
        print("✗ 1 条错误", file=_stream)
        print(f"  ✗ {exc.code} @0:0  {_lines[0]}", file=_stream)
        for _ln in _lines[1:]:
            print(_ln, file=_stream)
        return 1
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
    except Exception as _enc:                          # noqa: BLE001
        # **非 UTF-8 源文件走诊断，不走 traceback**（批次 5 第 22 条）。
        # 放在 `main` 里而不是 `__main__`：正式入口是 `src/elench.py`，
        # 它直接调 `main()`，不会经过 `dsh.__main__`。
        from parser import SourceEncodingError
        if isinstance(_enc, SourceEncodingError):
            print(f"✗ E-File-NotUtf8    {_enc}", file=sys.stderr)
            return 2
        raise


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

    # ============================================================ P0 兼容层
    #
    # **唯一的开关点**（用户定：不要把这七条散布到七个地方）。`--compat`
    # 打开后 `parser` / `typecheck` / `checker` 都只读 `compat.enabled()`：
    #
    #   1. `.eln` 里 `struct`（`--strict` 仍拒绝）
    #   2. `using X = Y;` / `typedef X Y;`（别名表，不进 AST）
    #   3. `auto x = expr;`（编译期推断；推不出报错，不猜）
    #   4. `T{a, b}` / `vector<u64> v{1,2,3}`
    #   5. `f(xs.begin(), xs.end())` → `f(xs)`（只在配平时）
    #   6. `std::` 白名单
    #   7. 返回类型 `void` → `()`
    #
    # **将来载体可能是 `.elnc` 后缀**（用户还没定）：到时候只改这一行——
    # 把"读 argv"换成"看文件后缀"，其余一行都不用动。
    import compat as _compat_flag
    _compat_flag.set_enabled("--compat" in rest)

    # **`-D NAME[=VALUE]`**（2026-11-10，规格 §8.10）：给预处理器传宏定义。
    # `check` / `run` / `cost` / `build` 都吃；**默认空**（没有 `-D _WIN32` 时
    # `#ifdef _WIN32` 为假，走 `#else` 或整块丢弃——这正是真实 C++ 的行为）。
    # `-D NAME` 的宏体是 `1`（C++ 惯例）；也认 `-DNAME` / `-DNAME=VALUE` 粘写。
    _defs = {}
    _rest = []
    _i = 0
    while _i < len(rest):
        _a = rest[_i]
        if _a == "-D":
            if _i + 1 >= len(rest):
                print("✗ -D 后面要跟 NAME 或 NAME=VALUE", file=sys.stderr)
                return 2
            _kv = rest[_i + 1]
            _i += 2
        elif _a.startswith("-D") and len(_a) > 2:
            _kv = _a[2:]
            _i += 1
        else:
            _rest.append(_a)
            _i += 1
            continue
        _name, _sep, _val = _kv.partition("=")
        if not _name:
            print(f"✗ -D 的宏名不能为空：{_kv!r}", file=sys.stderr)
            return 2
        _defs[_name] = _val if _sep else "1"
    rest = _rest
    from preproc import set_defines as _set_defines
    _set_defines(_defs)

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
        #
        # **`--out=*.yaml` 是例外**（2026-11-11）：它是「成本结果 + 出处」
        # （`src/cost_artifact.py`），不是把终端文本换一种容器——所以不进
        # 下面这条"文本 → json/csv"的转换，直接交给 `cmd_cost` 自己写。
        _outs = [a for a in rest if a.startswith("--out=")]
        if _outs and _outs[0].split("=", 1)[1].lower().endswith((".yaml", ".yml")):
            return cmd_cost(rest)
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
    if cmd == "otel":
        return cmd_otel(rest)
    if cmd == "stats":
        return cmd_stats(rest)
    if cmd == "header":
        return cmd_header(rest)
    if cmd == "grammar":
        return cmd_grammar(rest)
    if cmd == "effects":
        return cmd_effects(rest)
    if cmd == "spec":
        return cmd_spec(rest)
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
    # 非 UTF-8 源文件的诊断兜底在 `main()` 里（正式入口 `elench.py` 也走它）。
    sys.exit(main(sys.argv))
