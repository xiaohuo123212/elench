#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""symbolic.py —— G27：符号域（`Symbolic`）的成本式与代数。

**符号域解决什么问题**：不知道输入多长时，解释器拿什么往下算。

答案：**关于尺寸 n 的一个式子**。它只有六种成分：

    Cost ::= Number(1)                   常数
           | Size("xs")                  尺寸量（xs.length()）
           | Add(a, b)                   顺序：成本相加
           | Mul(n, a)                   循环：圈数乘每圈成本
           | Max(a, b)                   分支：取较大者
           | Log2(a)                     对数

**为什么只有这六种**：成本上界只会"往上加、取更大、重复"。减法、除法、比较
都不会出现在上界里。少一种成分，就少一处可能的错。

**三条化简规则（规格 T13-c 与 §7.3 的可执行形式）**

  1. 常数能合并就合并：`Add(1, 1) = 2`
  2. 常数与 Size 能比就比：`Max(3, 5) = 5`，`Max(3, n) = Max(3, n)`（留原样）
  3. `Mul(0, a) = 0`，`Mul(1, a) = a`，`Add(0, a) = a`

**`max` 的取舍**：能比就比，比不了**保留原样，不硬猜**。
判断"谁更快"只认三条增长速度顺序：常数 < log2 < 线性。其余一律保留 `max`。
"""

from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------- 符号值

#: **哨兵段的边界**。`?!` 的承诺里用 `[-PIECE_BIG, lo-1]` 与 `[hi+1, PIECE_BIG]`
#: 两段来表达"低于下界跑第一个候选 / 高于上界跑最后一个"，
#: 这样不必给 `Cost.piece` 加第二个 fallback 参数（那要动九处）。
PIECE_BIG = 1 << 62


class Cost:
    """符号域的一个成本值。不可变。"""

    __slots__ = ("kind", "value", "args")

    def __init__(self, kind: str, value=None, args=()):
        self.kind = kind
        self.value = value
        self.args = tuple(args)

    # ---------- 构造
    @staticmethod
    def num(n: int) -> "Cost":
        return Cost("num", int(n))

    @staticmethod
    def size(name: str) -> "Cost":
        return Cost("size", name)

    @staticmethod
    def add(a: "Cost", b: "Cost") -> "Cost":
        # **⊤ 会吸收一切**：`⊤ + 1 = ⊤`。否则 "1 + ⊤ + 1" 会让上层
        # `is_top()` 判不出来（`read_file` 的成本就该是 ⊤）。
        if a.kind == "top" or b.kind == "top":
            return Cost.top()
        if a.kind == "num" and a.value == 0:
            return b
        if b.kind == "num" and b.value == 0:
            return a
        if a.kind == "num" and b.kind == "num":
            return Cost.num(a.value + b.value)
        if a.kind == "piece" or b.kind == "piece":
            if a.kind == "piece" and b.kind == "piece":
                z = _zipper(a, b, lambda ca, cb: Cost.add(ca, cb))
                if z is not None:
                    return z
                return _raw_max(a, b)
            if a.kind == "piece":
                return _map_segments(a, lambda c: Cost.add(c, b))
            return _map_segments(b, lambda c: Cost.add(a, c))
        return Cost("add", None, (a, b))

    @staticmethod
    def mul(n: "Cost", a: "Cost") -> "Cost":
        if n.kind == "num":
            if n.value == 0:
                return Cost.num(0)
            if n.value == 1:
                return a
            if a.kind == "num":
                return Cost.num(n.value * a.value)
        if a.kind == "num" and a.value == 0:
            return Cost.num(0)
        if n.kind == "piece" or a.kind == "piece":
            # 两个分段相乘：断点求并后逐段乘（乘积仍分段）
            if n.kind == "piece" and a.kind == "piece":
                z = _zipper(n, a, lambda x, y: Cost.mul(x, y))
                if z is not None:
                    return z
                return _raw("mul", n, a)
            # 分段 × 变量：每段乘的东西不同，形状保持不住 → 退回普通节点
            # （**数值仍精确**：evaluate 会递归进参数，分段照样按 n 取值）
            if n.kind == "piece":
                return _raw("mul", n, a)
            if n.kind == "num":
                return _map_segments(a, lambda c: Cost.mul(n, c))
            return _raw("mul", n, a)
        return Cost("mul", None, (n, a))

    @staticmethod
    def max(a: "Cost", b: "Cost") -> "Cost":
        # **⊤ 必须被吸收**：`max(⊤, x) = ⊤`。
        # 以前它返回 `max(⊤, x)` 节点且 `is_top()` 为 False——下游会把
        # "无法判定"当成"有界"，这是最危险的一类静默。
        if a.is_top() or b.is_top():
            return Cost.top()
        if a.kind == "num" and b.kind == "num":
            return Cost.num(max(a.value, b.value))
        # **`max(0, x) = x`**（E4）：成本恒非负，0 是这一格的底。
        # 不化简的话，`if (c) { i = xs.length() }` 合并出 `max(0, n(xs))`，
        # 而循环上界的提取器认不出 `max` 节点，会退回符号 `n(i)` → 判决 `unknown`。
        if a.kind == "num" and a.value == 0:
            return b
        if b.kind == "num" and b.value == 0:
            return a
        if _same(a, b):
            return a
        if a.kind == "piece" or b.kind == "piece":
            if a.kind == "piece" and b.kind == "piece":
                z = _zipper(a, b, lambda ca, cb: Cost.max(ca, cb))
                if z is not None:
                    return z
                return _raw_max(a, b)
            if a.kind == "piece" and b.kind != "piece":
                return _map_segments(a, lambda c: Cost.max(c, b))
            if b.kind == "piece" and a.kind != "piece":
                return _map_segments(b, lambda c: Cost.max(a, c))
            return _raw_max(a, b)
        faster: Optional[Cost] = _strictly_faster(a, b)
        if faster is not None:
            return faster
        return Cost("max", None, (a, b))

    @staticmethod
    def log2(a: "Cost") -> "Cost":
        if a.kind == "num":
            if a.value <= 1:
                return Cost.num(0)
            if a.value == 2:
                return Cost.num(1)
        if a.kind == "piece":
            # **log 不动断点**：`log2(f(n))` 在 f 的每个分段上仍是同一个分段，
            # 逐段取 log 即可（数值精确）。早先写成"退回普通节点"是过于保守。
            return _map_segments(a, Cost.log2)
        return Cost("log2", None, (a,))

    @staticmethod
    def top() -> "Cost":
        """⊤：上界未知。"""
        return Cost("top")

    @staticmethod
    def piece(var: str, segments, fallback: "Cost") -> "Cost":
        """**分段成本式**：`n(var)` 落在哪一段，就用那一段的式子。

        为什么需要它（2026-11-01）：`?!` 的承诺如果只写 `max(甲,乙)`，
        就被**最慢候选的阶**绑住（`count` 的 `O(n²)` 候选只在 n≤12 用，
        却把整条线抬成 `O(n²)`）。分段能只在该段用该候选的式子。

        代价（必须写在这里）：**分段只在"运行期真的按同一份分段选候选"时才成立**。
        表与本分段不同源时它就是**假上界**——`gap` 曾经这样破界过
        （n=8192 承诺 426,001 / 实测 729,109）。

        不变量（构造时检查）：段按 lo 升序、首尾相接、每段成本本身不含 piece。
        """
        segs = tuple((int(lo), int(hi), c) for lo, hi, c in segments)
        if not segs:
            return fallback
        for i, (lo, hi, c) in enumerate(segs):
            if lo > hi:
                raise ValueError(f"分段区间非法：[{lo},{hi}]")
            if c.kind == "piece":
                raise ValueError("分段里不许再套分段（代数运算应退回 max）")
            if i and segs[i - 1][1] + 1 != lo:
                raise ValueError(
                    f"分段不连续：上一段结束 {segs[i-1][1]}，这一段从 {lo} 开始")
        return Cost("piece", (var, segs), (fallback,))

    # ---------- 查询
    def is_top(self) -> bool:
        if self.kind == "piece":
            _, segs = self.value
            return self.args[0].is_top() or any(c.is_top() for _, _, c in segs)
        return self.kind == "top"

    def is_const(self) -> bool:
        if self.kind == "piece":
            _, segs = self.value
            return self.args[0].is_const() and all(c.is_const() for _, _, c in segs)
        return self.kind == "num"

    def variables(self) -> List[str]:
        out: List[str] = []
        _collect_vars(self, out)
        return sorted(set(out))

    # ---------- 替换（E19 / R0 共用的唯一机制）
    def substitute_sizes(self, table: Dict[str, "Cost"]) -> "Cost":
        """把成本式里的 `size(x)` 换成 `table[x]`。

        **为什么需要它**：被调函数的 κ 是用**它自己的形参名**写的，
        `while (v < E)` 体内的 κ 是用**局部变量**写的。两者在调用点/循环入口
        都要换成"调用者看得懂的尺寸"，否则成本式里会留下查不到的名字
        （求值时按 1 算 → 低估 → 上界不成立，见 E19 / E20）。

        **用构造器而不是直接 `Cost(kind, ...)`**：构造器会把常数折起来
        （`num × num` → 常数），替换后能继续化简。
        """
        if not table:
            return self
        k = self.kind
        if k == "size":
            return table.get(self.value, self)
        if k in ("num", "top"):
            return self
        if k in ("add", "mul", "max"):
            l = self.args[0].substitute_sizes(table)
            r = self.args[1].substitute_sizes(table)
            if k == "add":
                return Cost.add(l, r)
            if k == "mul":
                return Cost.mul(l, r)
            return Cost.max(l, r)
        if k == "log2":
            return Cost.log2(self.args[0].substitute_sizes(table))
        if k == "piece":
            var, segs = self.value
            return Cost.piece(var,
                              [(lo, hi, c.substitute_sizes(table)) for lo, hi, c in segs],
                              self.args[0].substitute_sizes(table))
        return self

    # ---------- 输出
    #: 内部 kind → 对外名字。**对外一律写完整单词，不写简写**——
    #: 这份产物是给人看、也给 agent 读的，`op`/`mul` 这类简写会逼人查表。
    _EXTERNAL_NAME = {
        "num": "constant",
        "size": "size_variable",
        "add": "addition",
        "mul": "multiplication",
        "max": "maximum",
        "log2": "logarithm_base_2",
        "top": "unknown",
        "piece": "piecewise",
    }

    def to_dict(self) -> dict:
        """机器可读的成本签名（喂给 agent 的预算先验用，切口 1）。

        形如：

            {"operator": "addition",
             "operands": [{"operator": "constant", "value": 4}, ...]}

        **键与取值都用完整单词**：`operator` / `operands` / `constant` /
        `multiplication` / `logarithm_base_2` / `unknown`（即 ⊤）。
        """
        name = self._EXTERNAL_NAME.get(self.kind, self.kind)
        if self.kind == "num":
            return {"operator": name, "value": self.value}
        if self.kind == "size":
            return {"operator": name, "variable": self.value}
        if self.kind == "top":
            return {"operator": name}
        if self.kind == "piece":
            var, segs = self.value
            return {"operator": name,
                    "variable": var,
                    "segments": [{"lo": lo, "hi": hi, "cost": c.to_dict()}
                                 for lo, hi, c in segs],
                    "outside": self.args[0].to_dict()}
        return {"operator": name,
                "operands": [a.to_dict() for a in self.args]}

    def render(self) -> str:
        k = self.kind
        if k == "num":
            return str(self.value)
        if k == "size":
            return f"n({self.value})"
        if k == "sym":
            # **裸符号**：`external { read_file: O(F) }` 里的 `F`。
            # 不用 `n(F)`——那个写法读作"F 的规模"，而这里 F 本身就是"未知量"。
            return str(self.value)
        if k == "ext":
            # **外部项的显式名字**：`ext(read_file)`。
            # 不用 ⊤：⊤ 说"整个上界不知道"，ext 说"除这一项，其余都算得出来"。
            return f"ext({self.value})"
        if k == "top":
            return "⊤"
        if k == "log2":
            return f"log2({self.args[0].render()})"
        if k == "add":
            return f"{self.args[0].render()} + {self.args[1].render()}"
        if k == "mul":
            n, a = self.args
            left = n.render()
            right = a.render()
            # `n × (a + b)` 要括号
            if a.kind in ("add", "max"):
                right = f"({right})"
            return f"{left}×{right}"
        if k == "max":
            return f"max({self.args[0].render()}, {self.args[1].render()})"
        if k == "piece":
            var, segs = self.value
            parts = []
            below = above = False
            for lo, hi, c in segs:
                # **哨兵段**（`PIECE_BIG`）：不印天文数字，印成 `n<…` / `n>…`。
                # 它们不是"真实区间"，而是"越界时跑哪个候选"的显式表达。
                if lo <= -PIECE_BIG // 2:
                    parts.append(f"n({var})<{hi + 1}: {c.render()}")
                    below = True
                    continue
                if hi >= PIECE_BIG // 2:
                    parts.append(f"n({var})>{lo - 1}: {c.render()}")
                    above = True
                    continue
                parts.append(f"{lo}≤n({var})≤{hi}: {c.render()}")
            tail = "" if (below and above) else f"；越界: {self.args[0].render()}"
            return "{" + "; ".join(parts) + tail + "}"
        return "?"

    def __repr__(self) -> str:
        return f"Cost({self.render()})"


#: **形状退回计数**：每次"分段保持不住、退回普通节点"都记一笔。
#: 为什么要计数：代数闭包会悄悄退化成 max（等于白做分段），
#: 所以测试要能断言"这 10 个样例的退回次数 ≤ N"。
_FALLBACKS = {"n": 0}

#: **单条 κ 最多几段**。超过就退回 `max`（sound，松）。
#:
#: 为什么要上限：代数运算会把两个分段"求并"，段数相加；再套几层就是几十段。
#: 几十段在语义上是**灾难**——分段的含义是"运行期在这里换候选"，
#: 而代数产生的断点运行期并不存在（它只是数学拼接）。段数一多，
#: 读的人会以为运行时在几十处切换；渲染、逐段比较、JSON 签名也都会膨胀。
MAX_SEGMENTS = 16


def fallback_count() -> int:
    return _FALLBACKS["n"]


def reset_fallback_count() -> None:
    _FALLBACKS["n"] = 0


def _raw(kind: str, a: Cost, b: Optional[Cost] = None) -> Cost:
    """构造一个**裸节点**（不做化简），用于"保持不住分段"的场合。

    注意：这不是"变松"——`evaluate` 会递归进参数，分段在参数里照样按 n 取值，
    所以数值上仍然精确。丢的只是"结果仍被识别为分段"这个形状。
    """
    _FALLBACKS["n"] += 1
    return Cost(kind, None, (a,) if b is None else (a, b))


def _raw_max(a: Cost, b: Cost) -> Cost:
    """两段结构不一致时的保守合并：`max(a, b)` 对两者都是上界。"""
    _FALLBACKS["n"] += 1
    return Cost("max", None, (a, b))


def _map_segments(p: Cost, f) -> Cost:
    """对每一段（含 fallback）套同一个运算。"""
    var, segs = p.value
    return Cost.piece(var, [(lo, hi, f(c)) for lo, hi, c in segs], f(p.args[0]))


def _seg_compatible(a: Cost, b: Cost) -> bool:
    """两段结构完全一致（同一个变量、同样的区间）才能逐段合并。"""
    if a.kind != "piece" or b.kind != "piece":
        return False
    va, sa = a.value
    vb, sb = b.value
    if va != vb or len(sa) != len(sb):
        return False
    return all((la, ha) == (lb, hb) for (la, ha, _), (lb, hb, _) in zip(sa, sb))


def _seg_of(segs, n: int) -> Optional[Cost]:
    """段表里覆盖 n 的那一段的成本。"""
    for lo, hi, c in segs:
        if lo <= n <= hi:
            return c
    return None


def _zipper(a: Cost, b: Cost, op) -> Optional[Cost]:
    """把两个分段**按断点求并**后逐段合并：`op(ca, cb)`。

    返回 None 表示合并不了（区间不同/有空隙/段数超过 `MAX_SEGMENTS`），
    调用方应退回 `max`（sound，只是松）。
    """
    if a.kind != "piece" or b.kind != "piece":
        return None
    va, sa = a.value
    vb, sb = b.value
    if va != vb:
        return None
    if (sa[0][0], sa[-1][1]) != (sb[0][0], sb[-1][1]):
        return None                      # 覆盖区间不同：不猜
    starts = sorted({lo for lo, _, _ in sa} | {lo for lo, _, _ in sb})
    end = sa[-1][1]
    parts = []
    for i, lo in enumerate(starts):
        hi = (starts[i + 1] - 1) if i + 1 < len(starts) else end
        if lo > hi:
            continue
        ca = _seg_of(sa, lo)
        cb = _seg_of(sb, lo)
        if ca is None or cb is None:
            return None
        parts.append((lo, hi, op(ca, cb)))
        if len(parts) > MAX_SEGMENTS:
            _FALLBACKS["n"] += 1
            return None
    if not parts:
        return None
    # 只剩一段也要保持成 piece：它的**越界行为**（fallback）与段内不同，
    # 直接返回那一段会把越界算错。
    return Cost.piece(va, parts, op(a.args[0], b.args[0]))


def _same(a: Cost, b: Cost) -> bool:
    if a.kind != b.kind:
        return False
    if a.kind in ("num", "size"):
        return a.value == b.value
    return len(a.args) == len(b.args) and all(_same(x, y) for x, y in zip(a.args, b.args))


def _rank(c: Cost) -> Optional[int]:
    """增长速度的粗略排序：常数 0 < log2 1 < 线性 2 < 乘积 3 < 其它 None。"""
    if c.kind == "num":
        return 0
    if c.kind == "log2":
        return 1
    if c.kind in ("size", "ext"):
        return 2
    if c.kind == "sym":
        # **`k(f)` 不是尺寸，是「某个函数的成本」**——它有多大完全取决于
        # 调用点传进来的是谁（可能是 O(1)，也可能是 O(n²)）。按线性算就是
        # 给了一个**假上界**：实测 `apply(xs, sq)`（sq 是 O(x²)）判 ✓ 达成。
        # 其余 `sym`（如 `external { read_file: O(F) }` 里的 F）是作者声明的
        # 尺寸类量，仍按线性处理。
        if str(c.value or "").startswith("k("):
            return None
        return 2
    if c.kind == "mul":
        return 3
    if c.kind == "piece":
        _, segs = c.value
        ranks = [_rank(t) for _, _, t in segs] + [_rank(c.args[0])]
        if any(r is None for r in ranks):
            return None
        return max(ranks)
    return None


def _strictly_faster(a: Cost, b: Cost) -> Optional[Cost]:
    """a 是不是**确定**比 b 快（增长更慢）？是则返回 b，否则 None。

    只认**三条**确定的顺序，其余一律返回 None（保留 `max`，不硬猜）：

      常数 < log2(任何尺寸)         log2 随 n 增长，最终一定超过任何常数
      log2(n) < n                  显然
      n < n × k（k > 1）           显然

    **不认「常数 < 线性」**：常数可能是 1000，而 n 可能是 3，
    此时 max 的结果由谁大决定——**留着 `max` 才诚实**。
    """
    if a.kind == "num" and b.kind == "log2":
        return b
    if a.kind == "log2" and b.kind == "num":
        return a
    if a.kind == "log2" and b.kind == "size":
        return b
    if a.kind == "size" and b.kind == "log2":
        return a
    if a.kind == "size" and b.kind == "mul":
        k, inner = b.args
        if k.kind == "num" and k.value > 1 and inner.kind == "size" and inner.value == a.value:
            return b
    if b.kind == "size" and a.kind == "mul":
        k, inner = a.args
        if k.kind == "num" and k.value > 1 and inner.kind == "size" and inner.value == b.value:
            return a
    return None


def _collect_vars(c: Cost, out: List[str]) -> None:
    if c.kind == "size":
        out.append(c.value)
    if c.kind == "piece":
        var, segs = c.value
        out.append(var)
        for _, _, s in segs:
            _collect_vars(s, out)
    for a in c.args:
        _collect_vars(a, out)


# ---------------------------------------------------------------- 便捷构造

def seq(*items: Cost) -> Cost:
    """顺序语句：全部相加。"""
    total = Cost.num(0)
    for it in items:
        total = Cost.add(total, it)
    return total


def ones(n: int) -> Cost:
    return Cost.num(n)


# ---------------------------------------------------------------- 自检

if __name__ == "__main__":
    n = Cost.size("xs")
    cases = [
        ("1 + 1", Cost.add(Cost.num(1), Cost.num(1)), "2"),
        ("0 + n", Cost.add(Cost.num(0), n), "n(xs)"),
        ("max(3, 5)", Cost.max(Cost.num(3), Cost.num(5)), "5"),
        ("max(3, n)", Cost.max(Cost.num(3), n), "max(3, n(xs))"),
        ("max(n, log2(n))", Cost.max(n, Cost.log2(n)), "n(xs)"),
        ("n × 1", Cost.mul(Cost.num(1), n), "n(xs)"),
        ("2 × n", Cost.mul(Cost.num(2), n), "2×n(xs)"),
        ("n × (a+1)", Cost.mul(Cost.size("xs"), Cost.add(Cost.size("xs"), Cost.num(1))), "n(xs)×(n(xs) + 1)"),
        ("log2(1)", Cost.log2(Cost.num(1)), "0"),
        ("log2(n)", Cost.log2(n), "log2(n(xs))"),
    ]
    bad = 0
    for label, got, want in cases:
        ok = got.render() == want
        print(f"  {'✓' if ok else '✗'} {label:18} → {got.render():28} (期望 {want})")
        bad += 0 if ok else 1
    raise SystemExit(1 if bad else 0)
