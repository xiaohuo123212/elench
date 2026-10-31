#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""成本表达式与 `while` 上界提取（G29）。

规格里的成本表达式（§7.2）：

    κ ::= 1 | n | x̂ | κ₁ + κ₂ | n · κ | κ₁ ⊔ κ₂ | log₂(x̂) | |x|

本模块只实现「提取上界」需要的那一小块：把条件表达式变成界表达式。

**界表达式**比成本表达式更窄——它只有三种形态：

    B ::= N            （常数或符号量）
        | x.length()   （尺寸调用）
        | x            （变量）
        | a - b        （两个符号量之差，用于 log(hi - lo) 这类）

**上界提取规则（G29 甲方案：认形状）**

| 条件 | 上界 |
|---|---|
| `i < e` | `e - i`（i 从初值往上数，圈数约等于 e - i） |
| `i <= e` | `e - i + 1` |
| `i > e` | `i - e` |
| `s.length() > 0` | `s.length()` |
| `a and b` | `min(B(a), B(b))` |
| `a or b` | `max(B(a), B(b))` |
| `P(args)` 且 P 有 repeats | P 的 repeats 表达式，实参代入 |
| 其它 | `None`（抽不出） |

**抽不出时的处理（丙方案的精神）**：**明确返回 `None` 并给出原因**，
由调用方决定报错还是记 ⊤——**绝不静默超时**。
"""

from typing import List, Optional, Tuple

from parser import Node
from ast_util import descendants as _descendants


# ---------------------------------------------------------------- 界表达式

class Bound:
    """界表达式。`kind` 取值：const / size / var / sub / min / max / log2。"""

    __slots__ = ("kind", "value", "left", "right", "args")

    def __init__(self, kind: str, value=None, left=None, right=None, args=None):
        self.kind = kind
        self.value = value
        self.left = left
        self.right = right
        self.args = args or []

    def __repr__(self) -> str:
        return f"Bound({self.render()})"

    def render(self) -> str:
        if self.kind == "const":
            return str(self.value)
        if self.kind == "size":
            return f"{self.value}.length()"
        if self.kind == "var":
            return str(self.value)
        if self.kind == "sub":
            return f"({self.left.render()} - {self.right.render()})"
        if self.kind == "min":
            return f"min({self.left.render()}, {self.right.render()})"
        if self.kind == "max":
            return f"max({self.left.render()}, {self.right.render()})"
        if self.kind == "log2":
            return f"log2({self.left.render()})"
        if self.kind == "call":
            inner = ", ".join(a.render() for a in self.args)
            return f"{self.value}({inner})"
        return "?"

    def variables(self) -> List[str]:
        """界表达式里出现的变量名（用于「上界是不是依赖了循环内被改的变量」）。"""
        if self.kind == "var":
            return [self.value]
        if self.kind == "size":
            return [self.value]
        if self.kind == "sub":
            return self.left.variables() + self.right.variables()
        if self.kind in ("min", "max", "log2"):
            return self.left.variables()
        if self.kind == "call":
            out = []
            for a in self.args:
                out.extend(a.variables())
            return out
        return []


SIZE_FNS = ("length", "size", "capacity", "len")


# ---------------------------------------------------------------- 表达式 → 界

def expr_to_bound(n: Node) -> Optional[Bound]:
    """把一个**表达式**变成界表达式；认不出返回 None。"""
    if n is None:
        return None

    if n.kind == "lit-int":
        return Bound("const", value=n.value)

    if n.kind == "path":
        return Bound("var", value=n.name)

    if n.kind in ("method-call", "call"):
        fn = (n.name or "").split(".")[-1]
        if fn in SIZE_FNS:
            # xs.length() —— 路径就是被调对象
            target = n.children[0] if n.children and n.children[0].kind == "path" else None
            return Bound("size", value=(target.name if target else "?"))
        # 其它调用：`log(hi - lo)` 之类
        if fn in ("log", "log2"):
            arg = _first_arg(n)
            inner = expr_to_bound(arg) if arg is not None else None
            if inner is not None:
                return Bound("log2", left=inner)
            return None
        return None

    # `.value` 优先是运算符
    if n.kind == "binary":
        op = n.value or ""
        a, b = (n.children + [None, None])[:2]
        if op == "-":
            la = expr_to_bound(a)
            lb = expr_to_bound(b)
            if la is not None and lb is not None:
                return Bound("sub", left=la, right=lb)
            return None
        # `+` / `*` 不做界（它们的上界需要更多算术推理，留给 M2）。
        # **注意**：`O(a.length() * b.length())` 这种 target 由 cost_interp 单独处理
        # （走 `_target_cost`），不走这里——否则 `while (a * b < 10)` 会被误当计数器比较。
        return None

    return None


def _first_arg(call: Node) -> Optional[Node]:
    for c in call.children:
        if c.kind in ("args", "arg-list"):
            for a in c.children:
                return a
            return None
    return None


# ---------------------------------------------------------------- 条件 → 上界

def bound_of_condition(cond: Node, repeats_of=None,
                       zero_vars=frozenset(), dec_vars=frozenset(),
                       init_of: Optional[dict] = None) -> Tuple[Optional[Bound], str]:
    """从 `while` 的条件推出迭代次数上界。

    返回 `(界, 原因)`；界为 None 时，原因说明为什么抽不出。
    """
    if cond is None:
        return None, "循环条件为空"

    # ---- and / or
    if cond.kind == "binary" and (cond.value or "") in ("and", "or"):
        a, b = (cond.children + [None, None])[:2]
        ba, ra = bound_of_condition(a, repeats_of, zero_vars, dec_vars, init_of)
        bb, rb = bound_of_condition(b, repeats_of, zero_vars, dec_vars, init_of)
        if ba is not None and bb is not None:
            kind = "min" if cond.value == "and" else "max"
            return Bound(kind, left=ba, right=bb), f"{cond.value} 组合"
        # and：漏掉可推的一侧只会失准；or：同理。
        # **不取"可推的那一侧"，因为那可能偏小。**
        return None, f"{ra}；{rb}"

    # ---- 谓词调用 → repeats
    if cond.kind == "call" and cond.name:
        label = cond.name.split(".")[-1]
        rep = repeats_of(label) if repeats_of else None
        if rep is not None:
            return rep, f"谓词 {label} 的 repeats"
        return None, f"谓词 {label} 没有 repeats"

    # ---- 比较
    if cond.kind == "binary" and (cond.value or "") in ("<", "<=", ">", ">="):
        op = cond.value
        a, b = (cond.children + [None, None])[:2]
        lb = expr_to_bound(b)
        la = expr_to_bound(a)

        # 归一成 `计数器 op 上界`
        zl = la is not None and la.kind == "var" and la.value in zero_vars
        zr = lb is not None and lb.kind == "var" and lb.value in zero_vars
        # **两侧都必须可推**才算数：只认出一侧就去凑数，是在猜，
        # 而猜出来的"上界"可能偏小，正是我们最怕的失真。
        if la is None and lb is None:
            return None, f"{op} 两侧都不是可推量"
        if la is None:
            return None, f"{op} 比较里左侧不可推"
        if lb is None:
            return None, f"{op} 比较里右侧不可推"
        if op in ("<", "<="):
            return _sub_or_plain(lb, la, op == "<=", zl), f"{op} 比较"

        # ---- 递减循环（甲）：`while (j > 0)` / `while (j >= 1)` 且 j 体内单调递减
        # 上界 = j 的**初值**。这不是猜：j 只减不增，从 v0 出发，最多转 v0 圈。
        if op in (">", ">=") and la is not None and la.kind == "var":
            if la.value in dec_vars:
                init = init_of.get(la.value) if init_of else None
                if init is not None:
                    inner = expr_to_bound(init)
                    if inner is not None:
                        return inner, f"递减循环：{la.value} 从初值单调递减到 0"

        return _sub_or_plain(la, lb, op == ">=", zr), f"{op} 比较"

    return None, f"条件形态 {cond.kind} 不可推"


def _sub_or_plain(high: Bound, low: Optional[Bound], incl: bool,
                  init_zero: bool = False) -> Bound:
    """`i < e` → `e - i`；若已知 `i` 的初值为 0，直接取 `e`。

    `incl`（`<=`）多转一圈，但**上界取松不失真**，故不 +1——
    T13-a 规定提前退出的循环按上界算并标注"可能偏松"。
    """
    if init_zero:
        return high
    if low is not None and low.kind in ("const", "var"):
        return Bound("sub", left=high, right=low)
    return high


# ---------------------------------------------------------------- 便利入口

def monotone_decreasing(fn: Node) -> dict:
    """本函数里「只做减法」的变量名 → 初值节点。

    判据（**保守，不是数据流分析**）：
      1. 声明处初值不是 0（`u64 j = i;`）
      2. 函数体内对它的赋值**只有** `j = j - <常量>` 这一种形态
    满足就认为它单调递减，`while (j > 0)` 的迭代次数不超过初值。

    任何一处 `j = ...` 形态不符 → **整个变量剔除**（宁可不认，不可算小）。
    """
    from parser import Node as _N  # noqa: F401

    init: dict = {}
    for k in ("const-decl", "var-decl"):
        for d in _descendants(fn, k):
            if len(d.children) >= 2:
                init[d.name] = d.children[1]

    bad: set = set()
    for a in _descendants(fn, "assign"):
        if not a.children or a.children[0].kind != "path":
            continue
        nm = a.children[0].name
        if nm not in init:
            continue
        rhs = a.children[1] if len(a.children) > 1 else None
        ok = False
        if rhs is not None and rhs.kind == "binary" and (rhs.value or "") == "-":
            left, right = (rhs.children + [None, None])[:2]
            if (left is not None and left.kind == "path" and left.name == nm
                    and right is not None and right.kind == "lit-int"):
                ok = True
        # `j = 0;` 是「提前退出」的惯用写法（插入排序就这么写）：
        # 赋 0 只会让它更快退出，不会让它多转，所以**不破坏单调递减**。
        if rhs is not None and rhs.kind == "lit-int" and str(rhs.value) == "0":
            ok = True
        if not ok:
            bad.add(nm)

    return {nm: v for nm, v in init.items() if nm not in bad}


def zero_initialised(fn: Node) -> set:
    """本函数里初值为字面量 0 的变量名。

    这不是数据流分析：只看声明处 `T i = 0;` 这一种形态。
    用途单一——`while (i < n)` 里若 i 从 0 起，上界就是 n，不必写成 `n - i`。
    """
    names = set()
    for k in ("const-decl", "var-decl"):
        for d in [d for d in _descendants(fn, k)]:
            if len(d.children) >= 2:
                val = d.children[1]
                if val.kind == "lit-int" and str(val.value) == "0":
                    names.add(d.name)
    return names
def const_bindings(fn: Node) -> dict:
    """本函数里 `const T x = <界表达式>;` 的绑定表。

    用途：把 `i < n` 的 `n` 解析回 `xs.length()`，避免上界里出现局部变量名
    （报告里写 `n` 读的人还得回去查 `n` 是什么）。
    """
    out = {}
    for d in _descendants(fn, "const-decl"):
        if len(d.children) >= 2:
            b = expr_to_bound(d.children[1])
            if b is not None:
                out[d.name] = b
    return out


def substitute(b: Optional[Bound], table: dict) -> Optional[Bound]:
    """把界表达式里的自由变量按 const 绑定表展开（只展开一层，防环）。"""
    if b is None:
        return None
    if b.kind == "var" and b.value in table:
        return table[b.value]
    if b.kind == "sub":
        return Bound("sub", left=substitute(b.left, table),
                     right=substitute(b.right, table))
    if b.kind in ("min", "max"):
        return Bound(b.kind, left=substitute(b.left, table),
                     right=substitute(b.right, table))
    if b.kind == "log2":
        return Bound("log2", left=substitute(b.left, table))
    return b


WAY_OUT = ("三条出路：① 把条件写成尺寸比较 `i < xs.length()`；"
            "② 把条件包成谓词，在它的 spec 里写 repeats；"
            "③ 改写成 for + break")


# ---------------------------------------------------------------- E21：循环必须能推进
#
# **为什么需要这个检查**：
#   `while (j > 0) { s = s + 1; }` 的循环体根本不改 `j`，是**死循环**。
#   但在加这个检查之前，`bound_of_condition` 只看守卫就给上界（`O(n)`）——
#   **上界要求 κ ≥ 真实成本，而死循环的成本是 ∞**，这是 unsound 里最糟的一类：
#   不是算小了，是**声称一个跑不完的循环会跑完**。
#
# **这一层是"必要不充分"的临时缓解**：
#   只要求「体内出现过对推进量的修改」，
#   挡不住"修改写在 `if` 里可能不执行"的情形（那要靠 `decreases` + ΔM 校验，见批次 R）。

def _counter_of(cond: Optional[Node]):
    """从守卫里找出"推进量"：('var', 名) / ('container', 名) / None。"""
    if cond is None:
        return None
    # `and` / `or`：任一分支能给出推进量就够（上界本来取 min/max）
    if cond.kind == "binary" and (cond.value or "") in ("and", "or"):
        for c in cond.children:
            got = _counter_of(c)
            if got is not None:
                return got
        return None
    if cond.kind != "binary" or (cond.value or "") not in ("<", "<=", ">", ">="):
        return None
    left = cond.children[0] if cond.children else None
    if left is None:
        return None
    if left.kind == "path" and left.name:
        return ("var", left.name)
    # `while (q.length() > 0)`：推进量是容器 q 本身
    if left.kind == "method-call" and (left.name or "").split(".")[-1] in ("length", "size"):
        recv = left.children[0] if left.children else None
        if recv is not None and recv.kind == "path" and recv.name:
            return ("container", recv.name)
    return None


def _touched_names(body: Node) -> set:
    """体内所有"可能被改变"的名字：赋值左侧、方法调用接收者、作为实参传入的名字。"""
    got = set()
    for a in _descendants(body, "assign"):
        lhs = a.children[0] if a.children else None
        if lhs is not None and lhs.kind == "path" and lhs.name:
            got.add(lhs.name)
    for mc in _descendants(body, "method-call"):
        recv = mc.children[0] if mc.children else None
        if recv is not None:
            for pth in _descendants(recv, "path"):
                if pth.name:
                    got.add(pth.name)   # q.pop() / xs.sort() / m.at(i)
    for call in _descendants(body, "call"):
        # **只有按引用传（`&x`）才可能改它**；按值传（`f(i)`）改不了。
        # 这条区分很关键：把按值传也算"碰过"，会放过真正的死循环。
        # 注意实参在 AST 里包在 `args` 节点内，所以用 descendant 找 `ref`。
        for r in _descendants(call, "ref"):
            for pth in _descendants(r, "path"):
                if pth.name:
                    got.add(pth.name)   # apply_top(& values, & operators)
    return got


def progresses(wh: Node) -> Tuple[bool, str]:
    """循环体是否真的在推进守卫里的那个量。返回 (能不能推进, 原因)。

    判不出来时**返回 True**（不阻塞）——这一层只是挡住"明显不可能推进"的，
    不做完整的数据流分析。
    """
    cond = wh.children[0] if wh.children else None
    counter = _counter_of(cond)
    if counter is None:
        return True, "条件里没有可识别的推进量，不检查"
    kind, name = counter
    body = next((c for c in wh.children[1:] if c.kind == "block"), None)
    if body is None:
        return False, f"循环体为空，{name} 不可能变化"
    touched = _touched_names(body)
    if name in touched:
        return True, f"体内修改了 {name}"
    what = "计数器" if kind == "var" else "容器"
    return False, f"{what} {name} 在循环体里从未被修改"


def bound_of_while(wh: Node, repeats_of=None, fn: Node = None):
    """`while` 节点的上界：`(界, 原因)`。

    抽不出时，**原因里必须带出路**——只说"推不出"等于把问题丢回给生成者。
    """
    cond = wh.children[0] if wh.children else None
    zv = zero_initialised(fn) if fn is not None else frozenset()
    ok, prog_why = progresses(wh)
    if not ok:
        return None, f"{prog_why}（循环不会推进 ⇒ 不给上界，E21）"
    b, why = bound_of_condition(cond, repeats_of, zv)
    if b is not None and fn is not None:
        b = substitute(b, const_bindings(fn))
    if b is None:
        why = f"{why}。{WAY_OUT}"
    return b, why


# ---------------------------------------------------------------- 演示

if __name__ == "__main__":
    import sys
    from parser import parse_source

    src = sys.stdin.read() if not sys.stdin.isatty() else ""
    if not src:
        print("用法：python bound.py < 含 while 的 .eln 文件", file=sys.stderr)
        raise SystemExit(2)
    mod = parse_source(src, "x.eln").module

    def walk(n, kind, acc):
        if n.kind == kind:
            acc.append(n)
        for c in n.children:
            walk(c, kind, acc)

    ws = []
    walk(mod, "while", ws)
    for w in ws:
        cond = w.children[0] if w.children else None
        b, why = bound_of_condition(cond)
        print(f"  L{w.line} 上界 = {b.render() if b else '⊤'}   （{why}）")
