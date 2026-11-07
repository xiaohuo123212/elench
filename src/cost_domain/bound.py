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

    **E4（2026-11-04）**：以前**循环外**的赋值（如 `i = xs.length()`）也会剔除变量，
    于是「循环次数由分支决定」的写法完全拿不到上界。现在区分开：

      - 循环**体内**的赋值：仍按老规则（只许 `j = j - const` / `j = 0`）；
      - 循环**体外**的赋值：记成「初值覆盖」，**但只在声明处初值是字面量 0 时采用**
        （`max(0, x) = x`，而 u64 计数器不可能小于 0）。初值非 0 时仍剔除——
        那种情况要取 max，而这里只能给一个节点，取 max 会失真。
    """
    from parser import Node as _N  # noqa: F401

    init: dict = {}
    for k in ("const-decl", "var-decl"):
        for d in _descendants(fn, k):
            if len(d.children) >= 2:
                init[d.name] = d.children[1]

    # **循环体内的节点集合**：体内赋值才是"推进量"，体外的是"初值"
    in_loop: set = set()
    for w in _descendants(fn, "while"):
        blk = next((c for c in (getattr(w, "children", None) or [])
                    if getattr(c, "kind", "") == "block"), None)
        stack = [blk] if blk is not None else []
        while stack:
            nd = stack.pop()
            in_loop.add(id(nd))
            stack.extend(getattr(nd, "children", None) or [])

    bad: set = set()
    override: dict = {}
    for a in _descendants(fn, "assign"):
        if not a.children or a.children[0].kind != "path":
            continue
        nm = a.children[0].name
        if nm not in init:
            continue
        rhs = a.children[1] if len(a.children) > 1 else None
        # 循环外的赋值 → 只影响"初值"，不影响"是否单调"
        if id(a) not in in_loop:
            if rhs is not None:
                override[nm] = rhs
            continue
        ok = False
        if rhs is not None and rhs.kind == "binary" and (rhs.value or "") == "-":
            left, right = (rhs.children + [None, None])[:2]
            if (left is not None and left.kind == "path" and left.name == nm
                    and right is not None and right.kind == "lit-int"):
                ok = True
        # `j = 0;` 是「提前退出」的惯用写法（插入排序就这么写）
        if rhs is not None and rhs.kind == "lit-int" and str(rhs.value) == "0":
            ok = True
        if not ok:
            bad.add(nm)

    out: dict = {}
    for nm, v in init.items():
        if nm in bad:
            continue
        # 初值是字面量 0 才用覆盖（max(0, x) = x）
        if nm in override and v is not None and v.kind == "lit-int" and str(v.value) == "0":
            out[nm] = override[nm]
        else:
            out[nm] = v
    return out

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



def _temp_halving(body: Node, var: str):
    """`var = tmp`，且 `tmp` 在循环体里**只定义一次**、形如 `(var ± c) / k`（k≥2）。

    **判据刻意写窄**（宁可判不出，不可判错）：
      1. `var = tmp` 必须是循环体的**直接子语句**（不在 if 分支里）；
      2. 循环体里对 `var` 的赋值**只能有这一处**；
      3. `tmp` 在整个循环体里**只被赋值一次**，且在被用之前；
      4. `tmp` 的右值是 `/ k`（k≥2），分子里出现 `var`。

    反例为什么会被挡住：`p = i; i = p;` 违反 3（p 赋值两次）；
    `if (c) i = i/2; else i = i-1;` 违反 1；`i = p` 而 `p` 是参数（没在体内定义）违反 3。
    """
    body_children = [c for c in (getattr(body, "children", None) or [])]
    uses = [st for st in body_children
            if getattr(st, "kind", "") == "assign"
            and getattr((st.children or [None])[0], "name", "") == var]
    if len(uses) != 1:
        return None
    st = uses[0]
    rhs = (st.children + [None, None])[1]
    if getattr(rhs, "kind", "") != "path" or not getattr(rhs, "name", None):
        return None
    tmp = rhs.name
    defs = []
    for d in body_children:
        if getattr(d, "kind", "") in ("var-decl", "const-decl") and getattr(d, "name", "") == tmp:
            defs.append(d)
        if getattr(d, "kind", "") == "assign" and getattr((d.children or [None])[0], "name", "") == tmp:
            defs.append(d)
    if len(defs) != 1:
        return None
    # 定义必须在使用之前
    idx_def = body_children.index(defs[0])
    idx_use = body_children.index(st)
    if idx_def > idx_use:
        return None
    expr = defs[0].children[-1]
    while getattr(expr, "kind", "") == "paren" and getattr(expr, "children", None):
        expr = expr.children[0]
    if getattr(expr, "kind", "") != "binary" or (expr.value or "") != "/":
        return None
    num, den = (expr.children + [None, None])[:2]
    if getattr(den, "kind", "") != "lit-int":
        return None
    try:
        if int(den.value) < 2:
            return None
    except (TypeError, ValueError):
        return None
    names = {getattr(x, "name", None) for x in _descendants(num, "path")}
    if var not in names:
        return None
    return tmp


def _log_loop(wh: Node, fn: Node = None):
    """识别「每轮 ×2 / ÷2」的循环，返回 `(Bound, 说明)` 或 None。

    **为什么需要**（L-01）：`while (i < n) { i = i * 2; }` 以前被算成 `n(n)`——
    O(n)，带 `O(log n)` target 的**正确程序会被误拒**。
    减半（`i = i / 2`）更糟：上界成了 `n(i)`，与 target 的 `n` 不是一个量 → **判不了**。

    判定只看**形状**：循环体里对计数器有 `i = i * 常数` 或 `i = i / 常数`。
    界：`<` 型取守卫另一侧，`>` 型取计数器的初值。
    """
    cond = wh.children[0] if (wh.children or []) else None
    if cond is None or getattr(cond, "kind", "") != "binary":
        return None
    op = cond.value or ""
    if op not in ("<", "<=", ">", ">="):
        return None
    left, right = (cond.children + [None, None])[:2]
    if getattr(left, "kind", "") != "path" or not getattr(left, "name", None):
        return None
    var = left.name

    body = next((c for c in wh.children[1:] if getattr(c, "kind", "") == "block"), None)
    if body is None:
        return None
    # 体里必须有 `var = var * k` / `var = var / k`
    found = None
    # **只看循环体的直接子语句**（2026-11-06 实测的反例）：以前用 `_descendants`
    # 会把 `if (c) { i = i / 2; } else { i = i - 1; }` 里的那次减半当成
    # "每轮都减半"——那是**线性**循环，报 log 就是**假上界**。
    for st in [c for c in (getattr(body, "children", None) or [])]:
        lhs, rhs = (st.children + [None, None])[:2]
        if getattr(lhs, "kind", "") != "path" or getattr(lhs, "name", "") != var:
            continue
        if getattr(rhs, "kind", "") != "binary" or (rhs.value or "") not in ("*", "/"):
            continue
        a, b = (rhs.children + [None, None])[:2]
        names = {getattr(a, "name", None), getattr(b, "name", None)}
        has_var = var in names
        has_const = any(getattr(x, "kind", "") == "lit-int" for x in (a, b) if x is not None)
        if has_var and has_const:
            found = rhs.value
            break
    if found is None:
        # **中转变量**：`i = p` 而 `p = (i - 1) / 2`（上滤/二分/快速幂的常见写法）。
        # 只走窄规则，见 `_temp_halving` 的四条判据。
        if _temp_halving(body, var) is not None:
            found = "/"
    if found is None:
        return None
    # **倍增必须检查初值**（2026-11-06 实测的反例）：`u64 i = 0; i = i * 2;`
    # 永远不动（0×2=0），是真死循环，可它**形状完全对**——以前被判成
    # `log2(n)` 并**判为达标**，那是一个假上界。这里只接受字面初值 ≥ 1。
    if found == "*":
        _init = None
        if fn is not None:
            for d in _descendants(fn, "var-decl"):
                if getattr(d, "name", "") == var and len(d.children) >= 2:
                    _init = d.children[-1]
                    break
            if _init is None:
                for d in _descendants(fn, "const-decl"):
                    if getattr(d, "name", "") == var and len(d.children) >= 2:
                        _init = d.children[-1]
                        break
        if getattr(_init, "kind", "") != "lit-int":
            return None
        try:
            if int(_init.value) < 1:
                return None
        except (TypeError, ValueError):
            return None

    if op in ("<", "<="):
        b = expr_to_bound(right)
        why = f"循环变量 {var} 每轮乘除 2，界取自 {var} {op} 的右侧"
    else:
        # `i > 1` 型：用计数器的初值（`u64 i = n;` → n）
        b = None
        if fn is not None:
            for d in _descendants(fn, "var-decl"):
                if getattr(d, "name", "") == var and len(d.children) >= 2:
                    b = expr_to_bound(d.children[-1])
                    break
        if b is None:
            # 初值不在函数体里（**参数**，如 `heapify_up(vector& h, u64 i)`）：
            # 递减型循环从 i 减半到 0，界就是 `log2(i)` 本身。
            # 它与 target 的 `n` 是否同符号，交给 requires 那条线去解决。
            b = Bound("var", value=var)
        why = f"循环变量 {var} 每轮除以 2，界取自它的初值（无初值时取它自己）"
    if b is None:
        return None
    return Bound("log2", left=b), why


def _gcd_loop(wh: Node, fn: Node = None):
    """欧几里得算法：`while (b != 0) { t = b; b = a % b; a = t; }` ⇒ 步数 ≤ 2·log2(a)。

    **为什么这条必须靠"认成语"而不是数据流**：圈数来自**数论**（Lamé 定理，1844），
    不是程序形状——每一步的值是上一次的余数，收敛速度取决于相邻斐波那契数这个最坏情况。

    证明骨架：设 a > b > 0，写 a = q·b + r。
      · b ≤ a/2 ⇒ r < b ≤ a/2      —— 一步减半
      · b > a/2 ⇒ q = 1, r = a−b < a/2 —— 两步减半
    所以每两步规模至少减半 ⇒ 步数 ≤ 2·log2(a)。

    **判据写死**（只认这一个形状，认不出就不给界）：
      · 守卫是 `b != 0` 或 `b > 0`；
      · 循环体恰好三条直接语句：`t = b; b = a % b; a = t;`
    任何一条不符就返回 None——宁可判不出，不可判错。
    """
    cond = wh.children[0] if (wh.children or []) else None
    if cond is None or getattr(cond, "kind", "") != "binary":
        return None
    if (cond.value or "") not in ("!=", ">"):
        return None
    left, right = (cond.children + [None, None])[:2]
    if getattr(left, "kind", "") != "path" or not getattr(left, "name", None):
        return None
    bvar = left.name
    body = next((c for c in wh.children[1:] if getattr(c, "kind", "") == "block"), None)
    if body is None:
        return None
    sts = [c for c in (getattr(body, "children", None) or [])]
    if len(sts) != 3:
        return None
    d0, d1, d2 = sts
    if [getattr(s, "kind", "") for s in (d0, d1, d2)] != ["var-decl", "assign", "assign"]:
        return None
    tmp = getattr(d0, "name", "")
    if len(d0.children) < 2 or getattr(d0.children[-1], "name", "") != bvar:
        return None
    lhs1, rhs1 = (d1.children + [None, None])[:2]
    if getattr(lhs1, "name", "") != bvar:
        return None
    if getattr(rhs1, "kind", "") != "binary" or (rhs1.value or "") != "%":
        return None
    m1, m2 = (rhs1.children + [None, None])[:2]
    if getattr(m2, "name", "") != bvar or getattr(m1, "kind", "") != "path":
        return None
    avar = m1.name
    if avar == bvar:
        return None
    lhs2, rhs2 = (d2.children + [None, None])[:2]
    if getattr(lhs2, "name", "") != avar or getattr(rhs2, "name", "") != tmp:
        return None
    return Bound("log2", left=Bound("var", value=avar)), \
        f"欧几里得循环（{avar} % {bvar}）：每两步减半，步数 ≤ 2·log2({avar})"


def _cyclic_loop(wh: Node, fn: Node = None):
    """`i = (i + 1) % LEN` ⇒ 圈数 ≤ LEN（环形计数器）。

    **口径**：κ 是**对终止执行的**上界。这类循环要么在 LEN 步内找到出口，
    要么走满一圈回到起点、永不终止——后者没有成本。所以不需要终止性前提。

    只认这一个形状：`(i + c) % E`，`E` 能翻译成尺寸量（`X.length()` 或一个名字）。
    """
    body = next((c for c in wh.children[1:] if getattr(c, "kind", "") == "block"), None)
    if body is None:
        return None
    for st in _descendants(body, "assign"):
        lhs, rhs = (st.children + [None, None])[:2]
        if getattr(lhs, "kind", "") != "path" or not getattr(lhs, "name", None):
            continue
        if getattr(rhs, "kind", "") != "binary" or (rhs.value or "") != "%":
            continue
        left, right = (rhs.children + [None, None])[:2]
        # `(i + 1)` 会被解析成 **paren 节点**——先剥括号再判形状。
        while getattr(left, "kind", "") == "paren" and getattr(left, "children", None):
            left = left.children[0]
        if getattr(left, "kind", "") != "binary" or (left.value or "") != "+":
            continue
        a, b = (left.children + [None, None])[:2]
        names = {getattr(a, "name", None), getattr(b, "name", None)}
        if lhs.name not in names:
            continue
        bd = expr_to_bound(right)
        if bd is None:
            continue
        return Bound(bd.kind, value=bd.value, left=bd.left, right=bd.right), \
            f"环形计数器 {lhs.name} = ({lhs.name} + 1) % …，圈数不超过模数"
    return None


def bound_of_while(wh: Node, repeats_of=None, fn: Node = None):
    """`while` 节点的上界：`(界, 原因)`。

    抽不出时，**原因里必须带出路**——只说"推不出"等于把问题丢回给生成者。
    """
    cond = wh.children[0] if wh.children else None
    zv = zero_initialised(fn) if fn is not None else frozenset()
    # **先看是不是"每轮 ×2 / ÷2"**（L-01）：这类循环的对数界要比一般推导优先。
    lg = _log_loop(wh, fn)
    if lg is not None:
        return lg
    cy = _cyclic_loop(wh, fn)
    if cy is not None:
        return cy
    gd = _gcd_loop(wh, fn)
    if gd is not None:
        return gd

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
