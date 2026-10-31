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
        return Cost("mul", None, (n, a))

    @staticmethod
    def max(a: "Cost", b: "Cost") -> "Cost":
        if a.kind == "num" and b.kind == "num":
            return Cost.num(max(a.value, b.value))
        if _same(a, b):
            return a
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
        return Cost("log2", None, (a,))

    @staticmethod
    def top() -> "Cost":
        """⊤：上界未知。"""
        return Cost("top")

    # ---------- 查询
    def is_top(self) -> bool:
        return self.kind == "top"

    def is_const(self) -> bool:
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
        return "?"

    def __repr__(self) -> str:
        return f"Cost({self.render()})"


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
    if c.kind in ("size", "ext", "sym"):
        return 2
    if c.kind == "mul":
        return 3
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
