#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""par.py —— `parallel` 的**最大核视图**与**大小核墙钟估计**。

## 三件事，必须分开说

| 名字 | 是什么 | 依赖机器吗 | 是不是契约 |
|---|---|---|---|
| `κ`（判阶） | `max_i 阶(κ_i)`（B-24） | **不** | 是 |
| `alloc`（总量） | `Σ_i κ_i`（B-24） | **不** | 是（allocation 维度） |
| `wall_est` | `max` 任务在**这台机器**上跑完的估计 | **是**（核数+权重） | **不是** |

**为什么墙钟 MUST NOT 进 κ**：κ 是**可跨机器冻结**的承诺（`tests/golden/kappa.json`
就是冻结产物）。一旦核数/核权重进了 κ，同一份源码在两台机器上就有两个 κ，
golden 失去意义、契约不再可审计。所以：

    κ   = 机器无关的**工作量**（Σ）与**阶**（max）
    wall = 机器相关的**估计**，只出现在报告里，标"估计，不是契约"

这与规格 §13.6 B-27 一致：只有将来引入**精确成本目标**（`work: { max: 12345 }`）
时才需要讨论核数；`target` 仍按阶比较，所以 κ 里不需要 `M`。

## 分配规则（核数 < 任务数时怎么办）

**规则：LPT（longest-processing-time first）——按任务成本从大到小，逐个放到
"当前预计完成最早"的核上。**

```
任务按 value 降序（同值按下标升序）
对每个任务：放到 argmin_j (已分到核 j 的工作量 / 核 j 的权重)
           同分时取权重更大者，再同则取下标更小者（完全确定）
```

三条理由：

1. **它把"核数 ≥ 任务数"的退化情形定义对了**：一开始所有核都是空的，
   最重的任务被放到最快的核（同分时 `-weight` 胜出），于是每个任务各占一个核，
   `wall = max_i κ_i / w_i`——正是最简单的那条公式，没有特例分支。
2. **它从不空转**：只要还有任务，就一定会被放到某个核上；不会出现
   "核 A 闲着、任务排在核 B 后面"这种明显不优的估计。
3. **它对同构核有已知的质量保证**：LPT 的 makespan `≤ (4/3 − 1/(3m))·OPT`，
   所以估计**不会离谱地乐观**。**异核（uniform machines）下这个 4/3 保证不成立**
   ——这是我们**不**把它写成契约、只写成估计的第二个理由（第一个是它本来就依赖机器）。

**任务不拆**：`parallel` 的任务是语义单位（B-23：任务集合静态可见），
把一个任务切两半放到两个核上已经不是同一个程序了，所以 LPT 只做**整任务**分配。

## 输出长什么样

```
    parallel：4 任务（判阶 max / 总量 Σ）
      任务0  κ = 2 + n(xs)×7 + 2     阶 n
      任务1  κ = 2 + n(xs)×7 + 2     阶 n
      任务2  κ = 3                   阶 常数
      任务3  κ = 5                   阶 常数
      瓶颈 = 任务0（阶相同 → 取 n=2^20 处取值最大者）
      判阶 κ = 2 + n(xs)×7 + 2        ← 只取它的**阶**（B-24 的 max 口径，机器无关）
      总量   = Σ = 4 + n(xs)×14 + 5   ← B-24 的 Σ 口径（allocation 同此）
      wall_est = 7168（profile：4 核 [P×1, P×1, E×0.45, E×0.45]，at n=1024；估计，不是契约）
```

## 机器档（profile）JSON

```json
{ "note": "我的机器：2 大核 + 2 小核",
  "cores": [ {"kind": "P", "weight": 1.0},
             {"kind": "P", "weight": 1.0},
             {"kind": "E", "weight": 0.45},
             {"kind": "E", "weight": 0.45} ] }
```

- `weight` 是**速度因子**：同一份工作在权重为 `w` 的核上耗时 `1/w`。
  只有**比值**有意义（`1.0 / 0.45`），单位随便定。
- 没有 profile 的默认：**同构**，核数 = 任务数，全部 `weight = 1.0`。
- 也接受裸数组 `[1.0, 1.0, 0.45, 0.45]`（数字 = 权重，`kind` 记作 `C`）。
"""
from __future__ import annotations

import json
from fractions import Fraction
from typing import Dict, List, Optional, Sequence, Tuple

from .cost_check import cost_vars, evaluate, order_profile
from .symbolic import Cost, seq

#: 同阶任务比较"谁更重"时用的参考规模。
#:
#: **为什么是 2^20 而不是 n=1**：同阶（比如都是 `n`）时要比的是**系数**
#: （`3n + 5` vs `n + 12`）。在 n=1 处比，比的是**常数项**，会选出 `n + 12`；
#: 在足够大的 n 处比，比的是主导项，选出 `3n + 5`——后者才是"更慢那个"。
#: 这是纯符号层的比较，**与机器无关**，所以不写进契约也不影响 golden。
REF_SCALE = 1 << 20


# ---------------------------------------------------------------- 结构视图

def par_tasks(c: Optional[Cost]) -> Optional[List[Cost]]:
    """`c` 是 `par` 节点就返回它的任务列表，否则 `None`。"""
    if c is None or getattr(c, "kind", "") != "par":
        return None
    return list(c.args)


def par_blocks(c: Optional[Cost], depth: int = 0) -> List[Tuple[int, List[Cost]]]:
    """把 κ 里**所有** `par` 块按前序收集成 `[(层深, 任务列表), …]`。

    为什么要递归进 `piece`：`Cost.add(par, piece)` 会走 `_map_segments`，
    于是 `par` 落在分段的每一段里（`?!` 与 `parallel` 同时出现时会这样）。
    只扫 `args` 会漏掉它们——漏掉就等于"报告里少了几个并行块"，不是错数，
    但视图会骗人。
    """
    out: List[Tuple[int, List[Cost]]] = []
    if c is None:
        return out
    if getattr(c, "kind", "") == "par":
        tasks = list(c.args)
        out.append((depth, tasks))
        for t in tasks:
            out.extend(par_blocks(t, depth + 1))
        return out
    if getattr(c, "kind", "") == "piece":
        _var, segs = c.value
        for _lo, _hi, sub in segs:
            out.extend(par_blocks(sub, depth))
        for a in c.args:
            out.extend(par_blocks(a, depth))
        return out
    for a in (getattr(c, "args", ()) or ()):
        out.extend(par_blocks(a, depth))
    return out


def primary_var(c: Optional[Cost]) -> Optional[str]:
    """这个 κ 的**主尺寸量**（排序后第一个）。

    与 `cost_check.compare` 选 `vs[0]` 的规则一致（那里是 impl∪target 的排序首项），
    所以视图里印的阶与 `compare` 判出来的阶**用的是同一套口径**。
    """
    vs = cost_vars(c)
    return vs[0] if vs else None


def order_of(c: Cost, var: Optional[str]) -> Optional[Tuple[Fraction, int, int]]:
    """一阶 `(多项式次数, log 因子数, loglog 因子数)`；判不了返回 `None`。

    次数是 `Fraction`（`sqrt(n)` → `1/2`）。**直接复用 `cost_check._profile`**
    （经公开包装 `order_profile`）——视图另写一份判阶逻辑，就会出现
    "报告说 n、判定说 n²"这种自相矛盾。
    """
    if var is None:
        # 没有尺寸量：全是常数才是常数阶；否则（`sym`/`ext` 之类）判不了。
        return ((Fraction(0), 0, 0)
                if not c.variables() and c.const_value() is not None else None)
    return order_profile(c, var)


def order_json(o: Optional[Tuple[Fraction, int, int]]):
    """把阶转成 **JSON 可序列化**的 `[次数, log 因子数]` / `[次数, log, loglog]`。

    整数次数仍然是 `int`（与旧输出逐字相同）；分数次数是 `float`
    （`1/2` → `0.5`）。**loglog 分量只在非 0 时出现**：没用到 loglog 的
    并行块输出与旧版逐字相同（外部读 `order` 的消费者不受影响），
    真用到时第三个整数把 `n loglog n` 与 `n` 区分开。
    人读的精确形式由 `order_text` 给（`n^(2/3)` / `n loglog n`）。
    """
    if o is None:
        return None
    d, l, m = o
    head = [int(d) if d.denominator == 1 else float(d), l]
    return head if m == 0 else head + [m]


def order_text(o: Optional[Tuple[Fraction, int, int]]) -> str:
    """`(d, l, m)` → `常数` / `n` / `n log n` / `n²` / `n loglog n` / `?`。

    **`l` / `m` 可以为负**（2026-11-09 的除法与 loglog）：
    `n / log n` 记 `(1, -1, 0)`、`n / loglog n` 记 `(1, 0, -1)`，打印成
    `n / log n`、`n / loglog n`。负因子**不能不印**——以前 `l <= 0` 一律
    只印 `n`，那样 `n / log n` 与 `n` 在报告里长得一模一样，而它们正是
    要区分开的两档。
    """
    if o is None:
        return "?"
    d, l, m = o
    if d == 0 and l == 0 and m == 0:
        return "常数"
    if d == 0:
        base = ""
    elif d.denominator == 1:
        n = d.numerator
        base = {1: "n", 2: "n²", 3: "n³"}.get(n, f"n^{n}")
    else:
        # **分数次数要带括号**：`n^3/2` 会被读成 `(n^3)/2`。
        base = f"n^({d.numerator}/{d.denominator})"
    # **正因子**（分子侧）与**负因子**（分母侧）分开收集：
    # 这样 `n / (log n × loglog n)` 不会印成两句互相矛盾的 " / "。
    pos: List[str] = []
    if l > 0:
        pos.append("log n" if l == 1 else f"log^{l} n")
    if m > 0:
        pos.append("loglog n" if m == 1 else f"loglog^{m} n")
    if not pos:
        text = base
    else:
        text = " ".join(([base] if base else []) + pos)
    neg: List[str] = []
    if l < 0:
        neg.append("log n" if l == -1 else f"log^{abs(l)} n")
    if m < 0:
        neg.append("loglog n" if m == -1 else f"loglog^{abs(m)} n")
    if not neg:
        return text
    den = neg[0] if len(neg) == 1 else "(" + "×".join(neg) + ")"
    return f"{text} / {den}" if text else f"1 / {den}"


def _scale_value(c: Cost, var: Optional[str], scale: float) -> float:
    """在"该尺寸量取 `scale`"处求值（其它尺寸也取 `scale`；没有尺寸就取 1）。

    只用于**排序**（同阶时比谁重），不参与任何契约判定。
    """
    env: Dict[str, float] = {}
    if var is not None:
        env = {v: scale for v in c.variables()}
    return evaluate(c, env)


def bottleneck(tasks: Sequence[Cost],
               var: Optional[str]) -> Tuple[Optional[int], str]:
    """瓶颈任务的下标与判定依据（`"order"` / `"value"` / `"unknown"`）。

    规则（逐条）：

    1. **阶不同** → 阶大的那个（B-24：整块的阶就是它的阶）。
    2. **阶相同** → 在 `REF_SCALE` 处取值大的那个（比的是系数，不是常数项）。
    3. **有任务的阶判不了**（`piece` / `k(f)` / 其它尺寸量）→ 退回**纯取值**
       比较，并在报告里说明"阶判不了，按 n=2^20 的取值取最大"。
       不选"已知阶里最大的那个"：那个判不了的分量可能更大（会低估）。
    4. 仍然分不出（取值是 ⊤ / 非有限）→ `None`，报告印 `?`。
    """
    if not tasks:
        return None, "unknown"
    profs = [order_of(t, var) for t in tasks]
    vals = [_scale_value(t, var, REF_SCALE) for t in tasks]
    finite = all(v == v and v not in (float("inf"), float("-inf")) for v in vals)

    if all(p is not None for p in profs):
        if finite:
            i = max(range(len(tasks)),
                    key=lambda k: (profs[k], vals[k], -k))
            return i, "order"
        i = max(range(len(tasks)), key=lambda k: (profs[k], -k))
        return i, "order"
    if finite:
        i = max(range(len(tasks)), key=lambda k: (vals[k], -k))
        return i, "value"
    return None, "unknown"


# ---------------------------------------------------------------- 机器档

class MachineProfile:
    """一台（或一类）机器的核表：`[(kind, weight), …]`。"""

    __slots__ = ("cores", "source")

    def __init__(self, cores: Sequence[Tuple[str, float]], source: str = ""):
        cores = tuple((str(k), float(w)) for k, w in cores)
        if not cores:
            raise ValueError("机器档的 cores 不能为空（至少要有一个核）")
        for k, w in cores:
            if not (w > 0) or w != w or w == float("inf"):
                raise ValueError(f"核 {k!r} 的 weight 必须是正的有限数：{w!r}")
        self.cores = cores
        self.source = source

    # ---------- 构造
    @classmethod
    def homogeneous(cls, n: int) -> "MachineProfile":
        """默认档：`n` 个同构核（`weight` 全 1）。"""
        return cls([("C", 1.0)] * max(1, int(n)), source="默认（同构）")

    @classmethod
    def from_json(cls, path: str) -> "MachineProfile":
        """读机器档 JSON（格式见模块文档）。**校验从严**：坏档直接报错，不猜。"""
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except OSError as e:
            raise ValueError(f"读不到机器档 {path}：{e}") from e
        except json.JSONDecodeError as e:
            raise ValueError(f"机器档不是合法 JSON：{path}：{e}") from e
        return cls.from_obj(data, source=path)

    @classmethod
    def from_obj(cls, data, source: str = "") -> "MachineProfile":
        if isinstance(data, dict):
            raw = data.get("cores")
            if raw is None:
                raise ValueError("机器档缺少 `cores`："
                                 '要写成 {"cores": [{"kind":"P","weight":1.0}, …]}')
        elif isinstance(data, list):
            raw = data
        else:
            raise ValueError('机器档必须是 {"cores":[…]} 或数组 [1.0, 0.45, …]')
        if not isinstance(raw, list) or not raw:
            raise ValueError("机器档的 `cores` 必须是非空数组")
        cores: List[Tuple[str, float]] = []
        for i, item in enumerate(raw):
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                cores.append(("C", float(item)))
            elif isinstance(item, dict):
                if "weight" not in item:
                    raise ValueError(f"cores[{i}] 缺少 `weight`")
                cores.append((str(item.get("kind", "C")), float(item["weight"])))
            else:
                raise ValueError(f"cores[{i}] 必须是 {{\"kind\",\"weight\"}} 或数字")
        return cls(cores, source=source)

    # ---------- 查询 / 输出
    @property
    def n_cores(self) -> int:
        return len(self.cores)

    @property
    def weights(self) -> List[float]:
        return [w for _k, w in self.cores]

    def render(self) -> str:
        """`4 核 [P×1, P×1, E×0.45, E×0.45]`。"""
        parts = [f"{k}×{_num(w)}" for k, w in self.cores]
        return f"{self.n_cores} 核 [" + ", ".join(parts) + "]"


# ---------------------------------------------------------------- 墙钟估计

def wall_estimate(tasks: Sequence[Cost], profile: MachineProfile,
                  at: Optional[float] = None):
    """返回 `(makespan, loads, assign, values, why)`；给不出数时 `makespan is None`。

    **这是估计，不是契约**（B-27 / `文档/设计/.../证据与实验.md` §4.4）。
    它依赖 profile（核数 + 权重），而 κ **MUST NOT** 依赖机器。

    `at`：把每个尺寸量都设成这个规模（`n = at`）。
    - 所有任务的 κ 都是常数 → `at` 可以不给；
    - 有尺寸量又没给 `at` → 返回 `None`（不去猜一个规模，猜出来的数字会被人当承诺）。
    """
    if not tasks:
        return 0.0, [0.0] * profile.n_cores, [], [], "空"
    if at is not None and not (at > 0):
        raise ValueError(f"--at 必须是正数：{at!r}")

    need_env = any(t.variables() for t in tasks)
    if need_env and at is None:
        return None, [], [], [], "κ 含尺寸量；加 --at=N 指定规模"

    env: Dict[str, float] = {}
    if at is not None:
        for t in tasks:
            for v in t.variables():
                env[v] = float(at)
    values = [evaluate(t, env) for t in tasks]
    for v in values:
        if v != v or v == float("inf"):
            return None, [], [], values, "κ 里有 ⊤ / 未解释符号，给不出数"

    # LPT：重任务先放，放到"预计完成最早"的核上。
    order = sorted(range(len(tasks)), key=lambda i: (-values[i], i))
    loads = [0.0] * profile.n_cores
    assign: List[Optional[int]] = [None] * len(tasks)
    for i in order:
        j = min(range(profile.n_cores),
                key=lambda c: (loads[c] / profile.weights[c], -profile.weights[c], c))
        loads[j] += values[i]
        assign[i] = j
    makespan = max(loads[c] / profile.weights[c] for c in range(profile.n_cores))
    return makespan, loads, assign, values, "ok"


# ---------------------------------------------------------------- 报告

def _num(x: float) -> str:
    """数字印成人读形式（整数值不印 `.0`，小数不用科学计数法）。

    为什么不用 `:.3g`：`wall_est` 常见值是 `7168/0.45 = 15928.9`，`3g` 会印成
    `1.59e+04`——读者要拿它跟 `7168` 比大小，科学计数法一眼看不出来。
    """
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    if f != f or f in (float("inf"), float("-inf")):
        return "⊤"
    if f.is_integer() and abs(f) < 1e15:
        return str(int(f))
    # **小数量要更细**：核权重常见 `0.45`，`:.1f` 会把 `E×0.45` 印成 `E×0.5`
    # ——那正好把"大小核差多少"这个唯一要看的信息抹掉了。
    if abs(f) < 10:
        return f"{f:.4f}".rstrip("0").rstrip(".")
    return f"{f:.1f}".rstrip("0").rstrip(".")


def summary(c: Optional[Cost], profile: Optional[MachineProfile] = None,
            at: Optional[float] = None) -> Optional[dict]:
    """机读版：每个 `par` 块一条。`--json` 用它。

    键与含义：

        tasks[]       每个任务的 cost_expression / order / order_text /
                      **value_at_ref_scale**（在 2^20 处的取值；只用于同阶比大小）
        occurrences   同一块在分段（`piece`）里出现几次（内容去重后只列一次）
        bottleneck    瓶颈任务下标（判不了是 null）+ 依据
        judge         判阶口径的表达式（**只取它的阶**）
        total         Σ 口径的表达式（B-24 的 allocation）
        wall_estimate 依赖 profile 的估计；没有 profile 或给不出数时 null
        profile       机器档的人读描述（没有就是 "默认（同构）"）

    注意 `value_at_ref_scale` **不是** `wall_estimate` 用的那组数：后者在
    `at` 处取（`--at=N`），前者固定在 `REF_SCALE`（2^20），因为它的用途是
    **同阶时比谁重**（`3n+5` vs `n+12`），必须与 `--at` 无关。
    """
    blocks = par_blocks(c)
    if not blocks:
        return None
    var = primary_var(c)
    # **分段里会重复**：`Cost.add(par, piece)` 走 `_map_segments`，同一个 `par`
    # 对象被复制进每一段（`?!` 与 `parallel` 同时出现时就会这样）。
    # 逐段列出来只会让人以为有 N 个并行块，所以**按内容去重**并记出现次数。
    seen: Dict[tuple, int] = {}
    unique: List[Tuple[int, List[Cost]]] = []
    for depth, tasks in blocks:
        key = (depth, tuple(t.render() for t in tasks))
        if key in seen:
            seen[key] += 1
            continue
        seen[key] = 1
        unique.append((depth, tasks))
    out = []
    for n, (depth, tasks) in enumerate(unique):
        profs = [order_of(t, var) for t in tasks]
        bi, how = bottleneck(tasks, var)
        # **阶最大者是否唯一**：决定报告里那句理由怎么写。
        # 阶不同的块（`n²` + 三个 `n`）不该写"阶相同 → 比系数"——那是另一件事。
        ties = 0
        if bi is not None and all(p is not None for p in profs):
            _top = max(profs)
            ties = sum(1 for p in profs if p == _top)
        prof = profile if profile is not None else MachineProfile.homogeneous(len(tasks))
        wall, _loads, _assign, _values, why = wall_estimate(tasks, prof, at)
        ref_vals = [_scale_value(t, var, REF_SCALE) for t in tasks]
        out.append({
            "index": n,
            "depth": depth,
            "n_tasks": len(tasks),
            "occurrences": seen[(depth, tuple(t.render() for t in tasks))],
            "needs_scale": any(t.variables() for t in tasks),
            "tasks": [{"index": i,
                       "cost_expression": t.render(),
                       "order": order_json(profs[i]),
                       "order_text": order_text(profs[i]),
                       "value_at_ref_scale": ref_vals[i]}
                      for i, t in enumerate(tasks)],
            "bottleneck": bi,
            "bottleneck_by": how,
            "bottleneck_ties": ties,
            "judge_expression": tasks[bi].render() if bi is not None else None,
            "total_expression": seq(*tasks).render(),
            "ref_scale": REF_SCALE,
            "profile": prof.render(),
            "profile_default": profile is None,
            "at": (int(at) if at is not None and float(at).is_integer() else at),
            "wall_estimate": wall,
            "wall_note": None if why == "ok" else why,
        })
    return {"size_variable": var, "blocks": out}


def render(c: Optional[Cost], profile: Optional[MachineProfile] = None,
           at: Optional[float] = None, indent: str = "    ") -> List[str]:
    """人读版：每个 `par` 块一段。返回行列表（调用方决定缩进与打印）。"""
    s = summary(c, profile=profile, at=at)
    if s is None:
        return []
    lines: List[str] = []
    for blk in s["blocks"]:
        pad = indent + "  " * blk["depth"]
        times = (f"；同一块在分段里出现 {blk['occurrences']} 次，只列一次"
                 if blk.get("occurrences", 1) > 1 else "")
        lines.append(f"{pad}parallel：{blk['n_tasks']} 任务"
                     f"（判阶 max / 总量 Σ；核数与权重**不进 κ**{times}）")
        width = max([24] + [len(t["cost_expression"]) for t in blk["tasks"]])
        for t in blk["tasks"]:
            lines.append(f"{pad}  任务{t['index']}  κ = "
                         f"{t['cost_expression'].ljust(width)}  "
                         f"阶 {t['order_text']}")
        # 瓶颈
        if blk["bottleneck"] is None:
            lines.append(f"{pad}  瓶颈 = ?（各任务的阶与取值都判不了）")
        else:
            how = blk["bottleneck_by"]
            ties = blk.get("bottleneck_ties", 0)
            if how == "order" and ties == 1:
                why = "阶最大者"
            elif how == "order":
                why = ("阶相同 → 取常数最大者" if not blk["needs_scale"]
                       else "阶相同 → 取 n=%s 处取值最大者" % _num(REF_SCALE))
            elif how == "value":
                why = "有任务的阶判不了 → 按 n=%s 的取值取最大" % _num(REF_SCALE)
            else:
                why = ""
            note = f"（{why}）" if why else ""
            lines.append(f"{pad}  瓶颈 = 任务{blk['bottleneck']}{note}")
        # 判阶口径
        if blk["judge_expression"] is not None:
            lines.append(f"{pad}  判阶 κ = {blk['judge_expression']}"
                         f"    ← 只取它的**阶**（B-24，机器无关）")
        lines.append(f"{pad}  总量   = Σ = {blk['total_expression']}"
                     f"    ← B-24 的 Σ 口径（allocation 同此）")
        # 墙钟
        if blk["wall_estimate"] is None:
            lines.append(f"{pad}  wall_est = —（{blk['wall_note']}）")
        else:
            tag = "默认同构" if blk["profile_default"] else "profile"
            scale = (f"，at n={_num(blk['at'])}" if blk["at"] is not None else "")
            lines.append(
                f"{pad}  wall_est = {_num(blk['wall_estimate'])}"
                f"（{tag}：{blk['profile']}{scale}；**估计，不是契约**）")
    return lines
