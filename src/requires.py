#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""requires.py —— **调用点验证**（v0.71 第一步；2026-11-12 补 `.size()` + "认不出就报"；
2026-11-13 **变量 vs 变量**）

## 要解决的问题

`requires` 如果只在运行期断言，它和函数体里的 `if` 没有本质区别：
**都是运行时才发现、都要付检查的钱，还多一个"违约时行为未定义"**。

**只有编译器能在调用点替你验证，`requires` 才值钱。**

## 认得哪些谓词形状（这就是"检查覆盖表"）

| 谓词形状 | 从哪里得到事实 |
|---|---|
| `X.size() op N` / 反向 `N op X.size()` | `if (X.size() op N) { … }` 的**真分支**；`else` 分支取反 |
| `X.length() op N` / 反向 | 同上——**旧拼写仍然兼容**，另报 `W-Builtin-Deprecated`（建议改 `size`） |
| `is_sorted(X)` | 前面调过 `sort(X)`；或 `if (is_sorted(X)) { … }` |
| `K op N` / 反向 `N op K`（标量） | `if (K op N) { … }` 的真分支；`else` 分支取反 |
| **`T op T`（两个数值项比大小，含两个标量形参：`lo <= hi`、`a < b`、`x != y`、`hi <= xs.size()`）** | 见下面「变量 vs 变量」 |

`op ∈ {> >= < <= == !=}`；`N` MUST 是**十进整数字面量**；`X` / `K` MUST 是**裸变量名**。

"数值项"（`T`）的边界写得死死的，**不认的东西照旧报 `E-Require-NotCheckable`**：

```
T := 整数字面量 | 标量变量 | X.size() | X.length() | (T)
   | T + T | T - T | T * 整数字面量 | 整数字面量 * T
   | T / 正整数字面量 | T % 正整数字面量 | -T
```

`xs[0]`、`contains(xs, 1)`、`a && b`、`f(x)`、`T1 * T2`（两个都含变量）都**不在**这张表里。

**证明不了就报 `E-Require-Unproven`**，并在消息里给出两条修法：

1. **加 `if` 守卫**（编译器就能看见）；
2. **把这项检查放回函数体**（如果它其实是"域内结果"，按规则 1 就该在体里）。

## 认不出 ⇒ 报 `E-Require-NotCheckable`（**不许静默**）

2026-11-12 之前，`fact_of` 只匹配 `chain[1] == "length"`。于是**用现行拼写
`xs.size() > 0` 写的前提被直接丢掉**——契约看起来被检查了，其实没有（假保护）。
同类的"认不出就静默"还有：反向比较 `0 < X.size()`、括号、右界不是字面量、
`&&` 复合条件、`xs[0] > 0` 这种非长度谓词。

现在：**认得出 → 正常检查**（能证 ⇒ 通过；证不出 ⇒ `E-Require-Unproven`）；
**认不出 → 在调用点报 `E-Require-NotCheckable`**，并说清"这个前提我不会检查"+改法。
**宁可吵，不许假保护。**

> 只在**调用点**报：没有调用者的函数（入口）不在此列——它的前提由运行期断言兜底。

## 变量 vs 变量：怎么在调用点证明（2026-11-13）

`requires lo <= hi;` 这种**两边都不是字面量**的前提，以前只能报"认不出"。现在真的检查：

1. **把形参换成实参**（`_subst`）：两边都是**表达式**，不限于裸变量名。
   `merge_sort(xs, 0, xs.size())` ⇒ `0 <= n(xs)`；`merge_sort(xs, lo, mid)` ⇒ `lo <= mid`。
2. **把实参化成线性式**（`_lin`）：原子 = 标量变量 / `X.size()`；局部变量的**定义**
   （`u64 mid = lo + (hi - lo) / 2;`）在证明时展开——**只展开没被改过的定义**
   （一旦有人给 `lo` 赋值，所有提到 `lo` 的定义全失效）。
3. **事实是线性不等式**：守卫 `if (hi - lo < 2)`、本函数自己的 `requires`、
   以及内建的 `0 ≤ X.size()`，都记成 `Σ c·原子 ≤ b`。
   本函数自己的 `requires` 是**函数体的假设**（Design-by-Contract：入口成立才进得来），
   所以函数体里可以直接拿它推。**这条代掉了"归纳规则"**：
   `merge_sort` 的两个自调用点只要证明 `lo ≤ mid ≤ hi`，
   而 `lo ≤ hi` 来自它自己的 `requires`、`mid` 来自定义式 ⇒ 与递归深度无关、逐点可证。
   **不需要**"递归函数自己免检"或"靠归纳假设放过自己"——那才是假保护。
4. **判定用整数线性算术**（`_prove_rel` → `_prove_le` → `_fm`）：
   证 `D ≤ 0` 的办法是**反证** `D ≥ 1`，用 Fourier–Motzkin 消元判断该线性系统在
   **有理数上**无解。有理数无解 ⇒ 整数无解（整数点是有理点的子集），所以**只可能
   证不出，不可能证错**。消元规模超过上限一律判"证不出"（保守失败，不是放行）。
5. **除法/取模**单独处理（`_materialize`）：只有先证出被除数 `t ≥ 0`，
   才允许用 `0 ≤ t / c ≤ t`（`t % c ∈ [0, c-1]`）；证不出 `t ≥ 0` 就**不给商任何信息**
   （于是相关证明直接失败）。这是 `lo ≤ mid ≤ hi` 能成立的那一步：
   `mid = lo + (hi - lo)/2`，由 `lo ≤ hi` 得 `hi - lo ≥ 0`，于是 `lo ≤ mid` 且 `mid ≤ hi`。

**为什么不"看起来对就放行"**：实参之间没有关系时就是没有关系。同一句
`requires lo <= hi;` 在 `merge_sort(xs, mid, hi)` 上成立、在 `requires hi <= lo;`
上不成立——差别必须由**证明**给出，不能由"大概吧"给出。

## 一条明写的假设：**按整数（无回绕）证明**

判定用的是 ℤ 上的线性算术，`u64` 的**回绕不在模型里**：
`requires` 只在"这些数值不溢出、不等式按数学整数成立"的假设下有意义——
这与 `cost` 侧把 `hi - lo` 当尺寸差的假设是**同一条**（写 `hi - lo` 的 target
本来就要先证 `lo <= hi`）。回绕下的 `u64` 比较不在这套证明的保证范围内。

## 为什么这一版不做"签字"

我们语言里的 `assume` **已于 2026-11-04 移除**（它是不可验证的声明），
是**成本侧的信任锚**，会被打进报告（`假设：…`）。
**拿它给 `requires` 签字会把两个语义混在一起**——所以这一版宁可报错，
等真有"确实证不出来但必须放行"的用例，再加一个**独立**的签字形式。

## 不做的

- 跨函数推理（只在本函数体内收集事实；**不**拿被调方的 requires 当自己的事实）
- 复合条件不拆：`requires a && b;` 报 `E-Require-NotCheckable`，让作者拆成两条
  `requires`（拆开后每条都能按上表检查）
- 数组内容谓词（如"所有元素 > 0"）、下标谓词（如 `xs[0] > 0`）、任意函数调用
- 非线性的数值项（`T1 * T2` 两边都含变量、`T / T`）
- 提前返回式守卫（`if (X.size() == 0) { return …; }` 之后**不再**做否定推理——
  只认"包裹调用的 `if` 真分支 / `else` 分支"）
- 循环体：进入 `while` / `for` 就把事实与局部定义**全清空**（每轮都可能改写，
  宁可证不出也不留过期事实），循环条件的调用照常检查
"""
import math
from typing import Dict, List, NamedTuple, Optional, Tuple

from parser import Node


# ---------------------------------------------------------------- 形状

#: `requires` / 守卫里认得的比较运算符。
OPS = (">", ">=", "<", "<=", "==", "!=")

#: 反向书写（`0 < xs.size()` ≡ `xs.size() > 0`）：左值右值互换时运算符镜像。
_MIRROR = {">": "<", ">=": "<=", "<": ">", "<=": ">=", "==": "==", "!=": "!="}

#: `else` 分支的否定（只对单个比较式成立）。
_NEG = {">": "<=", ">=": "<", "<": ">=", "<=": ">", "==": "!=", "!=": "=="}

#: 长度方法：**现行拼写 `size` 在前**；`length` 是 v0.73 弃用的旧名。
LEN_METHODS = ("size", "length")

#: **会改变容器长度**的方法：调过之后 `n(接收者)` 的旧事实全部作废
#: （不让过期事实参与证明——那是"假保护"的另一种写法）。
#: `sort` / `sort_by` 不在此列：它们原地排序，**长度不变**。
_LEN_CHANGING = ("push", "push_back", "pop", "pop_back", "clear", "resize",
                 "insert", "erase", "append", "emplace", "emplace_back",
                 "shrink_to_fit", "reserve", "assign", "swap")


class Pred(NamedTuple):
    """一条被**认出**的前提。

    `kind == "sorted"`：`is_sorted(X)`；
    `kind == "rel"`：`left op right`（两边都是"数值项"，可以是同一个形参、
    两个形参、形参与 `.size()`、形参与字面量……）。
    `spell_node`：`left`/`right` 里 `.length()` 的方法调用节点（弃用警告要指到方法名上）。
    """
    kind: str
    left: Optional[Node] = None
    op: str = ""
    right: Optional[Node] = None
    spell_node: Optional[Node] = None


def _unwrap(n: Node) -> Node:
    """去掉括号：`(xs.size() > 0)` 与 `xs.size() > 0` 是同一个条件。"""
    while n is not None and getattr(n, "kind", "") == "paren" and n.children:
        n = n.children[0]
    return n


def _raw_op(n: Node) -> str:
    """取运算符的文本（兼容 `Op.>` / `{"op": …}` 两种旧表示）。"""
    v = getattr(n, "value", None)
    if isinstance(v, dict):
        v = v.get("op")
    if isinstance(v, str):
        return v[3:] if v.startswith("Op.") else v
    return ""


def _int_lit(n: Node) -> Optional[int]:
    n = _unwrap(n)
    if n is not None and n.kind == "lit-int":
        try:
            return int(n.value)
        except (TypeError, ValueError):
            return None
    return None


def _len_call(n: Node) -> Optional[Tuple[str, str, Node]]:
    """`xs.size()` / `xs.length()` → `("xs", "size"/"length", 方法调用节点)`。

    只认**裸变量名**接收者、**无实参**的调用；别的一律返回 None（→ 报认不出）。
    """
    n = _unwrap(n)
    if n is None or getattr(n, "kind", "") != "method-call":
        return None
    name = n.name or ""
    if name not in LEN_METHODS:
        return None
    recv = next((c for c in (n.children or []) if c.kind == "path"), None)
    if recv is None:
        return None
    args = next((c for c in (n.children or []) if c.kind == "args"), None)
    if args is not None and args.children:
        return None
    return (recv.name or "", name, n)


def _render(n: Node) -> str:
    """把 AST 粗糙地还原成源码样子——只用于诊断消息（`xs[0] > 0` 这类）。"""
    if n is None:
        return "…"
    k = getattr(n, "kind", "")
    if k == "path":
        return n.name or "…"
    if k == "lit-int":
        return str(n.value)
    if k == "lit-bool":
        return "true" if n.value else "false"
    if k == "lit-str":
        return f'"{n.value}"'
    if k == "method-call":
        recv = next((c for c in (n.children or []) if c.kind == "path"), None)
        args = next((c for c in (n.children or []) if c.kind == "args"), None)
        inner = ", ".join(_render(a) for a in (args.children if args is not None else []))
        return f"{(recv.name if recv is not None else '…')}.{n.name}({inner})"
    if k == "call":
        args = next((c for c in (n.children or []) if c.kind == "args"), None)
        inner = ", ".join(_render(a) for a in (args.children if args is not None else []))
        return f"{n.name or '…'}({inner})"
    if k == "binary":
        ch = n.children or []
        if len(ch) >= 2:
            return f"{_render(ch[0])} {_raw_op(n)} {_render(ch[1])}"
    if k == "unary":
        ch = n.children or []
        return f"{_raw_op(n)}{_render(ch[0] if ch else None)}"
    if k == "paren":
        ch = n.children or []
        return f"({_render(ch[0] if ch else None)})"
    if k == "index":
        ch = n.children or []
        if len(ch) >= 2:
            return f"{_render(ch[0])}[{_render(ch[1])}]"
    return f"<{k}>"


def _is_term(n: Node) -> bool:
    """这个节点是不是**数值项**（见文件头那张文法）。

    这条判据就是"认得出 / 认不出"的那条线：`False` ⇒ 调用点报
    `E-Require-NotCheckable`（**不是**放行，也不是硬说它错）。
    """
    n = _unwrap(n)
    if n is None:
        return False
    k = n.kind
    if k == "lit-int":
        return _int_lit(n) is not None
    if k == "path":
        return bool(n.name)
    if k == "method-call":
        return _len_call(n) is not None
    if k == "unary":
        ch = n.children or []
        return _raw_op(n) == "-" and len(ch) == 1 and _is_term(ch[0])
    if k == "binary":
        ch = n.children or []
        if len(ch) != 2:
            return False
        op = _raw_op(n)
        if op in ("+", "-"):
            return _is_term(ch[0]) and _is_term(ch[1])
        if op == "*":
            return ((_int_lit(ch[0]) is not None and _is_term(ch[1]))
                    or (_int_lit(ch[1]) is not None and _is_term(ch[0])))
        if op in ("/", "%"):
            d = _int_lit(ch[1])
            return d is not None and d > 0 and _is_term(ch[0])
        return False
    return False


def _deprecated_len_node(n: Node, depth: int = 0) -> Optional[Node]:
    """找出子树里第一个 `.length()` 调用（弃用警告用）。"""
    if n is None or depth > 24:
        return None
    lc = _len_call(n)
    if lc is not None and lc[1] == "length":
        return lc[2]
    for c in getattr(n, "children", []) or []:
        hit = _deprecated_len_node(c, depth + 1)
        if hit is not None:
            return hit
    return None


def _analyze(cond: Node) -> Tuple[Optional[Pred], Optional[str], str]:
    """把一个条件变成谓词。返回 `(pred, 认不出的原因, 源码样子)`。

    `pred is None` ⇒ 认不出，`原因` 说明为什么（调用点据此报 `E-Require-NotCheckable`）。
    """
    raw = _render(cond)
    n = _unwrap(cond)
    if n is None:
        return None, "条件是空的", raw
    # `is_sorted(xs)`：`sort(xs)` 的后置条件，或 `if` 的真分支。
    if n.kind == "call" and (n.name or "").split(".")[-1] == "is_sorted":
        args = next((c for c in (n.children or []) if c.kind == "args"), None)
        items = list(args.children) if args is not None else []
        if len(items) == 1 and _unwrap(items[0]).kind == "path":
            return Pred("sorted", _unwrap(items[0]), "==", None), None, raw
        return None, "`is_sorted(…)` 的参数只能是裸变量名", raw
    if n.kind != "binary" or len(n.children or []) < 2:
        return None, "这不是一个比较式（只认单个 `> >= < <= == !=` 比较）", raw
    op = _raw_op(n)
    if op not in OPS:
        return (None,
                f"这是复合/未知运算 `{op or '?'}`（只认单个比较；请拆成多条 `requires`）",
                raw)
    left, right = n.children[0], n.children[1]
    bad = [s for s, x in (("左", left), ("右", right)) if not _is_term(x)]
    if bad:
        side = "、".join(f"{s}侧 `{_render(x)}`" for s, x in
                         (("左", left), ("右", right)) if s in bad)
        return (None,
                f"{side} 不是**数值项**（数值项 = 整数字面量 / 标量变量 / `xs.size()` / "
                f"`xs.length()`，以及它们与整数字面量的 `+ - * / %`）",
                raw)
    spell = _deprecated_len_node(left) or _deprecated_len_node(right)
    return Pred("rel", left, op, right, spell), None, raw


def fact_of(cond: Node) -> Optional[Pred]:
    """把一个条件变成谓词；认不出返回 `None`（调用点会另行报认不出）。"""
    return _analyze(cond)[0]


def _shape(p: Pred) -> str:
    """把谓词渲染成消息里的样子（按原拼写）。"""
    if p.kind == "sorted":
        return f"is_sorted({_render(p.left)})"
    return f"{_render(p.left)} {p.op} {_render(p.right)}"


def _negate(p: Optional[Pred]) -> Optional[Pred]:
    """`else` 分支的否定事实（只对单个比较式；`is_sorted` 否定不了）。"""
    if p is None or p.kind != "rel":
        return None
    op = _NEG.get(p.op)
    if op is None:
        return None
    return Pred("rel", p.left, op, p.right, p.spell_node)


# ---------------------------------------------------------------- 线性式

#: 原子：`("v", 变量名)` 标量 / `("len", 容器名)` 长度 / `("t", 源码样子)` 商·余项。
Atom = Tuple[str, str]

#: 一条约束：`Σ c·原子 ≤ rhs`（常数项已经折进 `rhs`）。
_Con = Tuple[Dict[Atom, int], int]


class _Lin:
    """线性式 `Σ c·原子 + k`。"""

    __slots__ = ("c", "k")

    def __init__(self, c=None, k: int = 0):
        self.c = {a: v for a, v in (c or {}).items() if v}
        self.k = int(k)

    def __repr__(self) -> str:                     # pragma: no cover - 调试用
        return f"_Lin({self.c}, {self.k})"


def _add(a: _Lin, b: _Lin) -> _Lin:
    out = dict(a.c)
    for atom, v in b.c.items():
        out[atom] = out.get(atom, 0) + v
    return _Lin(out, a.k + b.k)


def _sub(a: _Lin, b: _Lin) -> _Lin:
    return _add(a, _scale(b, -1))


def _scale(a: _Lin, m: int) -> _Lin:
    return _Lin({x: v * m for x, v in a.c.items()}, a.k * m)


def _shift(a: _Lin, d: int) -> _Lin:
    return _Lin(dict(a.c), a.k + d)


def _lin(n: Node, ctx: "_Ctx", pend: list, depth: int = 0) -> _Lin:
    """把数值项化成线性式；`/` `%` 的商/余项变成原子并登记"待派生的约束"。"""
    n = _unwrap(n)
    if n is None:
        return _Lin({("t", "<空>"): 1})
    k = n.kind
    if k == "lit-int":
        v = _int_lit(n)
        if v is not None:
            return _Lin({}, v)
        return _Lin({("t", _render(n)): 1})
    if k == "path":
        name = n.name or ""
        d = ctx.defs.get(name)
        # **只展开没被改过的定义**：`_kill` 会把过期的定义删掉。
        if d is not None and depth < 8:
            return _lin(d, ctx, pend, depth + 1)
        return _Lin({("v", name): 1})
    if k == "method-call":
        lc = _len_call(n)
        if lc is not None:
            return _Lin({("len", lc[0]): 1})
        return _Lin({("t", _render(n)): 1})
    if k == "paren":
        ch = n.children or []
        return _lin(ch[0], ctx, pend, depth + 1) if ch else _Lin({("t", _render(n)): 1})
    if k == "unary":
        ch = n.children or []
        if _raw_op(n) == "-" and len(ch) == 1:
            return _scale(_lin(ch[0], ctx, pend, depth + 1), -1)
        return _Lin({("t", _render(n)): 1})
    if k == "binary":
        ch = n.children or []
        if len(ch) != 2:
            return _Lin({("t", _render(n)): 1})
        op = _raw_op(n)
        if op == "+":
            return _add(_lin(ch[0], ctx, pend, depth + 1),
                        _lin(ch[1], ctx, pend, depth + 1))
        if op == "-":
            return _sub(_lin(ch[0], ctx, pend, depth + 1),
                        _lin(ch[1], ctx, pend, depth + 1))
        if op == "*":
            la = _lin(ch[0], ctx, pend, depth + 1)
            lb = _lin(ch[1], ctx, pend, depth + 1)
            if not la.c:
                return _scale(lb, la.k)
            if not lb.c:
                return _scale(la, lb.k)
            return _Lin({("t", _render(n)): 1})
        if op in ("/", "%"):
            d = _int_lit(ch[1])
            if d is not None and d > 0:
                dividend = _lin(ch[0], ctx, pend, depth + 1)
                atom: Atom = ("t", _render(n))
                pend.append((atom, op, d, dividend))
                return _Lin({atom: 1})
            return _Lin({("t", _render(n)): 1})
        return _Lin({("t", _render(n)): 1})
    return _Lin({("t", _render(n)): 1})


# ---------------------------------------------------------------- 判定（整数线性算术）

#: Fourier–Motzkin 的约束条数上限：超了就判"证不出"（保守失败，**不是**放行）。
_FM_CAP = 256


def _fm(cons: List[_Con], atoms: List[Atom]) -> Optional[bool]:
    """`Σ c·原子 ≤ b` 这个系统**在有理数上**可满足吗？

    返回 `True` = 无解 / `False` = 有解 / `None` = 规模过大，不知道。

    **只用来"证否"**：无解 ⇒ 目标成立。所以 `None` 与 `False` 都只是"没证出来"。
    """
    cur: List[_Con] = []
    seen = set()
    for c, b in cons:
        cc = {a: v for a, v in c.items() if v}
        key = (tuple(sorted(cc.items())), b)
        if key in seen:
            continue
        seen.add(key)
        cur.append((cc, b))
        if len(cur) > _FM_CAP:
            return None
    for a in atoms:
        pos, neg, zero = [], [], []
        for cc, b in cur:
            v = cc.get(a, 0)
            if v > 0:
                pos.append((v, cc, b))
            elif v < 0:
                neg.append((v, cc, b))
            else:
                zero.append((cc, b))
        # 只有上界（或只有下界）⇒ 这个变量可以取任意小（大），约束不限制其他人 ⇒ 整组丢掉。
        if not pos or not neg:
            cur = zero
            continue
        nxt = list(zero)
        for cp_, cp, bp in pos:
            for cn_, cn, bn in neg:
                w1, w2 = -cn_, cp_          # 两式都是**非负**倍数
                d = {}
                for key, v in cp.items():
                    if key != a:
                        d[key] = w1 * v
                for key, v in cn.items():
                    if key != a:
                        d[key] = d.get(key, 0) + w2 * v
                d = {key: v for key, v in d.items() if v}
                rhs = w1 * bp + w2 * bn
                g = 0
                for v in list(d.values()) + [rhs]:
                    g = math.gcd(g, abs(int(v)))
                if g > 1:
                    d = {key: v // g for key, v in d.items()}
                    rhs //= g
                key = (tuple(sorted(d.items())), rhs)
                if key in seen:
                    continue
                seen.add(key)
                nxt.append((d, rhs))
                if len(nxt) > _FM_CAP:
                    return None
        cur = nxt
    for cc, b in cur:
        if any(cc.values()):
            return None                     # 还有变量没消掉（不该发生）⇒ 保守
        if b < 0:
            return True                     # `0 ≤ 负数`：无解
    return False


def _infeasible(ctx: "_Ctx", extra: List[_Con]) -> Optional[bool]:
    """`ctx` 的约束 + `extra` 有没有解（`True` = 无解）。"""
    cons: List[_Con] = list(ctx.cons) + list(ctx.dcons) + list(extra)
    atoms = set()
    for c, _b in cons:
        atoms.update(c)
    # 内建事实：长度非负（`n(x)` 是 u64）。
    for a in sorted(atoms):
        if a[0] == "len":
            cons.append(({a: -1}, 0))
    return _fm(cons, sorted(atoms))


def _prove_le(ctx: "_Ctx", d: _Lin) -> bool:
    """证 `d ≤ 0`（整数）：反证 `d ≥ 1` 无解。"""
    return _infeasible(ctx, [({a: -c for a, c in d.c.items()}, d.k - 1)]) is True


def _prove_rel(ctx: "_Ctx", d: _Lin, op: str) -> bool:
    """证 `d op 0`（`d` = 左项 − 右项）。"""
    if op == "<=":
        return _prove_le(ctx, d)
    if op == "<":                       # d ≤ -1 ⟺ d + 1 ≤ 0
        return _prove_le(ctx, _shift(d, 1))
    if op == ">=":
        return _prove_le(ctx, _scale(d, -1))
    if op == ">":                       # d ≥ 1 ⟺ -d + 1 ≤ 0
        return _prove_le(ctx, _shift(_scale(d, -1), 1))
    if op == "==":
        return _prove_le(ctx, d) and _prove_le(ctx, _scale(d, -1))
    if op == "!=":                      # d ≠ 0 ⟸ d ≤ -1 或 d ≥ 1
        return _prove_le(ctx, _shift(d, 1)) or _prove_le(ctx, _shift(_scale(d, -1), 1))
    return False


# ---------------------------------------------------------------- 上下文

class _Ctx:
    """收集到的事实：基本约束、派生约束、已排序集合、局部变量定义。"""

    __slots__ = ("cons", "dcons", "sorted", "defs", "derived")

    def __init__(self) -> None:
        self.cons: List[_Con] = []          # 来自 requires / 守卫的事实
        self.dcons: List[_Con] = []         # 由 `/` `%` 推出的**派生**约束（可重算）
        self.sorted: set = set()
        self.defs: Dict[str, Node] = {}      # 局部变量 → 定义式（只留没被改过的）
        self.derived: set = set()            # 已经派生过的商/余项原子

    def copy(self) -> "_Ctx":
        out = _Ctx()
        out.cons = list(self.cons)
        out.dcons = list(self.dcons)
        out.sorted = set(self.sorted)
        out.defs = dict(self.defs)
        out.derived = set(self.derived)
        return out


def _add_le(dst: List[_Con], lin: _Lin) -> None:
    """把 `lin ≤ 0` 记进约束表（常数折进 rhs）。"""
    dst.append((dict(lin.c), -lin.k))


def _materialize(ctx: _Ctx, pend: list) -> None:
    """给 `/` `%` 的商/余项原子补上**可证**的界。

    规则（都用到了整数除法向零截断 + 只对被除数非负的情形）：
    `t ≥ 0` ⇒ `0 ≤ t / c ≤ t`、`0 ≤ t % c ≤ c-1`。
    `t ≥ 0` 证不出 ⇒ **一条也不加**（保守），于是依赖它的证明失败。
    """
    for atom, op, d, dividend in pend:
        if atom in ctx.derived:
            continue
        if not _prove_le(ctx, _scale(dividend, -1)):     # t ≥ 0 ?
            continue
        ctx.derived.add(atom)
        q = _Lin({atom: 1})
        _add_le(ctx.dcons, _scale(q, -1))                # q ≥ 0
        if op == "/":
            _add_le(ctx.dcons, _sub(q, dividend))        # q ≤ t
        else:
            _add_le(ctx.dcons, _shift(q, -(d - 1)))      # r ≤ c-1


def _add_pred(ctx: _Ctx, pred: Optional[Pred]) -> None:
    """把一条**认出的**前提并进上下文（认不出 ⇒ 什么也不加，调用点会另行报）。"""
    if pred is None:
        return
    if pred.kind == "sorted":
        n = _unwrap(pred.left)
        if n is not None and n.kind == "path":
            ctx.sorted.add(n.name or "")
        return
    pend: list = []
    left = _lin(pred.left, ctx, pend)
    right = _lin(pred.right, ctx, pend)
    _materialize(ctx, pend)
    d = _sub(left, right)
    op = pred.op
    if op == "<=":
        _add_le(ctx.cons, d)
    elif op == "<":
        _add_le(ctx.cons, _shift(d, 1))
    elif op == ">=":
        _add_le(ctx.cons, _scale(d, -1))
    elif op == ">":
        _add_le(ctx.cons, _shift(_scale(d, -1), 1))
    elif op == "==":
        _add_le(ctx.cons, d)
        _add_le(ctx.cons, _scale(d, -1))
    elif op == "!=":
        # `d != 0` 是**析取**，只有当已知 `d ≥ 0`（⇒ d ≥ 1）或 `d ≤ 0`（⇒ d ≤ -1）
        # 时才能记成一条不等式；否则不记（保守）。
        if _prove_le(ctx, _scale(d, -1)):
            _add_le(ctx.cons, _shift(_scale(d, -1), 1))
        elif _prove_le(ctx, d):
            _add_le(ctx.cons, _shift(d, 1))


def _mentions(n: Node, name: str, depth: int = 0) -> bool:
    if n is None or depth > 24:
        return False
    if getattr(n, "kind", "") == "path" and (n.name or "") == name:
        return True
    return any(_mentions(c, name, depth + 1) for c in (getattr(n, "children", []) or []))


def _kill(ctx: _Ctx, name: str) -> None:
    """`name` 被赋值 ⇒ 关于它的事实、以及**提到它的局部定义**，全部作废。

    第二条是最容易漏的：`mid = lo + 1; lo = 7;` 之后 `mid` 的定义不再是
    `lo + 1` 的值——展开它就是在拿过期事实证明（典型的假保护）。
    """
    if not name:
        return
    ctx.defs.pop(name, None)
    for var in [v for v, e in ctx.defs.items() if _mentions(e, name)]:
        ctx.defs.pop(var, None)
    dead = {("v", name), ("len", name)}
    ctx.cons = [c for c in ctx.cons if not (dead & set(c[0]))]
    ctx.sorted.discard(name)
    # 商/余项的派生约束是**在当时那组事实下**推出来的；变量一动就不再被蕴含。
    ctx.dcons = []
    ctx.derived = set()


def _subst(n: Node, pidx: Dict[str, int], args: List[Node], depth: int = 0) -> Node:
    """把**被调方的形参名**换成**调用点的实参表达式**（结构替换）。"""
    n = _unwrap(n)
    if n is None or depth > 24:
        return n
    if n.kind == "path":
        i = pidx.get(n.name or "")
        if i is not None and 0 <= i < len(args):
            return _unwrap(args[i])
        return n
    ch = getattr(n, "children", []) or []
    if not ch:
        return n
    new = Node(kind=n.kind, line=n.line, col=n.col, name=n.name, value=n.value)
    for c in ch:
        new.children.append(_subst(c, pidx, args, depth + 1))
    return new


def _prove(ctx: _Ctx, pred: Pred, pidx: Dict[str, int], args: List[Node]) -> bool:
    """在**调用点**（`ctx` 是调用方当时知道的事实）证明被调方的前提成立。"""
    if pred.kind == "sorted":
        a = _subst(pred.left, pidx, args)
        a = _unwrap(a)
        return a is not None and a.kind == "path" and (a.name or "") in ctx.sorted
    left = _subst(pred.left, pidx, args)
    right = _subst(pred.right, pidx, args)
    pend: list = []
    ll = _lin(left, ctx, pend)
    rr = _lin(right, ctx, pend)
    _materialize(ctx, pend)
    return _prove_rel(ctx, _sub(ll, rr), pred.op)


# ---------------------------------------------------------------- 走函数体

def _calls_in(node: Node) -> List[Node]:
    out = []
    stack = [node]
    while stack:
        x = stack.pop()
        if x is None:
            continue
        if x.kind == "call":
            out.append(x)
        stack.extend(getattr(x, "children", []) or [])
    return out


def _params_of(fn: Node) -> List[Node]:
    params = next((c for c in (fn.children or []) if c.kind == "params"), None)
    return list(params.children) if params is not None else []


def _is_mut_ref(param: Node) -> bool:
    """形参是不是**可变引用**（`u64& k` / `vector<u64>& xs`）？`const vector&` 不算。"""
    if not getattr(param, "children", None):
        return False
    if param.children[0].kind != "type-ref":
        return False
    v = getattr(param, "value", None)
    return not (isinstance(v, dict) and v.get("const"))


def _kill_for_call(ctx: _Ctx, call: Node, pinfo: Dict[str, List[bool]]) -> None:
    """被调方可能改写的实参 ⇒ 它们的事实与定义作废（不留过期事实）。

    `pinfo[被调方]` 是**逐个形参**的"是不是可变引用"表（来自 `.tu` 的 spec 或本模块实现）。
    表里没有的名字 = 内建（`sort` / `min` / …）：它们按值/只读取参，不改实参。
    """
    args = next((c for c in (call.children or []) if c.kind == "args"), None)
    items = list(args.children) if args is not None else []
    if not items:
        return
    callee = (call.name or "").split(".")[-1]
    mut = pinfo.get(callee)
    if not mut:
        return
    for i, a in enumerate(items):
        a = _unwrap(a)
        if a is None or a.kind != "path" or i >= len(mut):
            continue
        if mut[i]:
            _kill(ctx, a.name or "")


def _scan_mutations(ctx: _Ctx, st: Node) -> None:
    """语句里调了**改长度**的方法 ⇒ `n(接收者)` 的旧事实作废。"""
    stack = [st]
    while stack:
        x = stack.pop()
        if x is None:
            continue
        if x.kind == "method-call" and (x.name or "") in _LEN_CHANGING:
            recv = next((c for c in (x.children or []) if c.kind == "path"), None)
            if recv is not None:
                ctx.cons = [c for c in ctx.cons if ("len", recv.name or "") not in c[0]]
                ctx.sorted.discard(recv.name or "")
                ctx.dcons = []
                ctx.derived = set()
            continue
        stack.extend(getattr(x, "children", []) or [])


#: 调用点快照：`(call 节点, 当时的事实)`。
Site = Tuple[Node, _Ctx]


def _walk(block: Node, ctx: _Ctx, out: List[Site],
          pinfo: Dict[str, List[bool]]) -> _Ctx:
    cur = ctx
    for st in getattr(block, "children", []) or []:
        cur = _walk_stmt(st, cur, out, pinfo)
    return cur


def _walk_node(n: Node, ctx: _Ctx, out: List[Site],
               pinfo: Dict[str, List[bool]]) -> _Ctx:
    if n is None:
        return ctx
    if n.kind == "block":
        return _walk(n, ctx, out, pinfo)
    return _walk_stmt(n, ctx, out, pinfo)


def _may_write(node: Node, pinfo: Dict[str, List[bool]], out=None,
               depth: int = 0) -> set:
    """这个子树**可能改写**哪些变量（赋值 / `var-decl` / 可变引用实参 / 改长度的方法）。

    用途只有一个：分支与循环**结束后**，把这些变量的事实与定义式在**外层**上下文里
    作废。少了这一步，`if (k > 0) { k = 0; }` 之后就能拿 `k > 0` 当事实用——
    与本文件最想消灭的"过期事实"是同一类错误。
    """
    if out is None:
        out = set()
    if node is None or depth > 64:
        return out
    k = getattr(node, "kind", "")
    if k in ("assign", "assign-op"):
        ch = node.children or []
        if ch:
            t = _unwrap(ch[0])
            if t is not None and t.kind == "path":
                out.add(t.name or "")
    elif k == "var-decl":
        if node.name:
            out.add(node.name)
    elif k == "call":
        args = next((c for c in (node.children or []) if c.kind == "args"), None)
        items = list(args.children) if args is not None else []
        mut = pinfo.get((node.name or "").split(".")[-1]) or []
        for i, a in enumerate(items):
            a = _unwrap(a)
            if a is not None and a.kind == "path" and i < len(mut) and mut[i]:
                out.add(a.name or "")
    elif k == "method-call" and (node.name or "") in _LEN_CHANGING:
        recv = next((c for c in (node.children or []) if c.kind == "path"), None)
        if recv is not None:
            out.add(recv.name or "")
    for c in getattr(node, "children", []) or []:
        _may_write(c, pinfo, out, depth + 1)
    return out


def _kill_written(ctx: _Ctx, st: Node, pinfo: Dict[str, List[bool]]) -> None:
    for name in _may_write(st, pinfo):
        if name:
            _kill(ctx, name)


def _walk_stmt(st: Node, ctx: _Ctx, out: List[Site],
               pinfo: Dict[str, List[bool]]) -> _Ctx:
    """处理一条语句，返回之后可用的事实。

    **2026-11-12**：以前只认得出条件时才进 `if` 体，认不出就 `continue`——
    于是 `if (某个认不出的条件) { call(...); }` 里的调用**整个被跳过**（假保护）。
    现在两条分支都走：认得出就带上事实，认不出也照走（代价是更吵，不是更松）。
    `else` 分支同样要检查，并用否定事实（`if (xs.size() == 0) { … } else { … }`）。

    **2026-11-13**：分支/循环结束后，把分支里**可能被改写的变量**在外层作废
    （`_may_write`）——否则"分支里改了 k"之后还能拿 k 的旧守卫当事实用。
    """
    kind = st.kind
    if kind == "block":
        # `for` 在 AST 里就是 `(var-decl + while)` 的 block：**逐条走**，
        # 不能像以前那样 `_calls_in` 一把抓（那会把循环体当直线代码，丢掉清空语义）。
        return _walk(st, ctx, out, pinfo)
    if kind == "if":
        ch = st.children or []
        cond = ch[0] if ch else None
        # 条件表达式里本身也可能有调用（`if (head(xs) > 0)`）——也要检查。
        for c in _calls_in(cond):
            out.append((c, ctx.copy()))
        pred = _analyze(cond)[0] if cond is not None else None
        then = ch[1] if len(ch) > 1 else None
        els = ch[2] if len(ch) > 2 else None
        if then is not None:
            tctx = ctx.copy()
            _add_pred(tctx, pred)
            _walk_node(then, tctx, out, pinfo)
        if els is not None:
            ectx = ctx.copy()
            _add_pred(ectx, _negate(pred))
            _walk_node(els, ectx, out, pinfo)
        # `if` 两分支的结论**不**外流（没有"两分支都成立"的推理）。
        _kill_written(ctx, st, pinfo)
        return ctx
    if kind == "match":
        ch = st.children or []
        for c in ch:
            if c.kind != "arm":
                for call in _calls_in(c):
                    out.append((call, ctx.copy()))
        for arm in [c for c in ch if c.kind == "arm"]:
            blk = next((c for c in (arm.children or []) if c.kind == "block"), None)
            if blk is not None:
                _walk_node(blk, ctx.copy(), out, pinfo)
        _kill_written(ctx, st, pinfo)
        return ctx
    if kind in ("var-decl", "assign", "assign-op"):
        name = ""
        target = next((c for c in (st.children or []) if _unwrap(c).kind == "path"), None)
        if target is not None:
            name = target.name or ""
        if st.kind == "var-decl":
            name = st.name or name
        init = None
        ch = st.children or []
        if st.kind in ("assign", "assign-op") and len(ch) >= 2:
            init = ch[1]
        elif st.kind == "var-decl" and len(ch) >= 2:
            v = getattr(st, "value", None)
            if not (isinstance(v, dict) and v.get("has_init") is False):
                init = ch[1]
        if name:
            _kill(ctx, name)
            # **只记简单赋值的定义**（`=`）：复合赋值（`+=`）不会是"定义式"。
            if init is not None and st.kind != "assign-op":
                ctx.defs[name] = init
        for c in _calls_in(st):
            out.append((c, ctx.copy()))
            _kill_for_call(ctx, c, pinfo)
        _scan_mutations(ctx, st)
        return ctx
    if kind in ("while", "for"):
        ch = st.children or []
        cond = ch[0] if ch else None
        for c in _calls_in(cond):
            out.append((c, ctx.copy()))
        body = next((c for c in ch if c.kind == "block"), None)
        # 循环体会改写变量：**清空**事实与定义再进去（过期事实不许参与证明）。
        if body is not None:
            _walk_node(body, _Ctx(), out, pinfo)
        _kill_written(ctx, st, pinfo)
        return ctx
    if kind == "expr-stmt" and st.children:
        c = st.children[0]
        # `sort(xs);` 之后 xs 就是有序的（内建的后置条件）
        if c.kind == "call" and (c.name or "").split(".")[-1] in ("sort", "sort_by"):
            args = next((x for x in (c.children or []) if x.kind == "args"), None)
            items = args.children if args is not None else []
            if items and _unwrap(items[0]).kind == "path":
                ctx.sorted.add(_unwrap(items[0]).name or "")
    for c in _calls_in(st):
        out.append((c, ctx.copy()))
        _kill_for_call(ctx, c, pinfo)
    _scan_mutations(ctx, st)
    return ctx


# ---------------------------------------------------------------- 入口

def _fn_requires(spec: Node) -> List[Node]:
    """一个 `spec` 里的全部 `requires` 表达式（按源码顺序）。"""
    items: List[Node] = []
    for holder in spec.children:
        if holder.kind != "spec-body":
            continue
        for cl in holder.children:
            if cl.kind != "clause" or cl.name != "requires":
                continue
            items.extend(cl.children)
    return items


def check(tu_mod, ch_mod) -> List:
    """返回 `[(code, message, line, col), …]`。"""
    out: List = []
    if ch_mod is None or tu_mod is None:
        return out
    specs = {c.name: c for c in tu_mod.children if c.kind == "spec"}
    fns = {c.name: c for c in ch_mod.children if c.kind == "fn"}

    #: 每个函数声明的 `requires` 子句：**认得的也留着，认不出的也留着**。
    clauses_by_fn: Dict[str, List[Node]] = {}
    for name, spec in specs.items():
        clauses_by_fn[name] = _fn_requires(spec)

    #: 被调方逐个形参"是不是可变引用"：来自 `.tu` 的 spec（外部函数）或本模块实现。
    #: 表里没有的名字 = 内建（`sort` / `min` / …），按值/只读取参，不改实参。
    pinfo: Dict[str, List[bool]] = {}
    for name, spec in specs.items():
        pinfo[name] = [_is_mut_ref(p) for p in _params_of(spec)]
    for name, fn in fns.items():
        pinfo[name] = [_is_mut_ref(p) for p in _params_of(fn)]

    warned = set()
    for fname, fn in fns.items():
        body = next((c for c in fn.children if c.kind == "block"), None)
        if body is None:
            continue
        ctx = _Ctx()
        # **本函数自己的 `requires` 是函数体的假设**（Design-by-Contract）：
        # 入口成立才进得来，所以体内可以直接拿它推。递归函数不需要额外的归纳规则——
        # 每个自调用点仍然要**逐个**证明（用的就是这条假设 + 定义式）。
        for expr in clauses_by_fn.get(fname) or []:
            _add_pred(ctx, _analyze(expr)[0])
        sites: List[Site] = []
        _walk(body, ctx, sites, pinfo)
        for call, cctx in sites:
            callee = (call.name or "").split(".")[-1]
            exprs = clauses_by_fn.get(callee) or []
            if not exprs:
                continue
            decl = fns.get(callee)
            if decl is None:
                # 有 spec、但实现不在本模块 ⇒ 形参位置未知，无法把 spec 名字映射到实参。
                # **不许静默**：明确说"这项前提无法检查"。
                for expr in exprs:
                    p, _why, raw = _analyze(expr)
                    what = f"`{_shape(p)}`" if p is not None else f"`{raw}`"
                    out.append((
                        "E-Require-NotCheckable",
                        f"{fname} 调用 {callee} 前必须保证 {what}，"
                        f"但 {callee} 的实现不在本模块（形参表未知），这项前提无法检查；"
                        f"修法：①把实现与 spec 放在同一次 check 里；"
                        f"②或把这项检查写进函数体",
                        call.line, call.col))
                continue
            pidx = {p.name: i for i, p in enumerate(_params_of(decl))}
            args = next((c for c in call.children if c.kind == "args"), None)
            items = list(args.children) if args is not None else []
            for expr in exprs:
                p, why, raw = _analyze(expr)
                if p is None:
                    out.append((
                        "E-Require-NotCheckable",
                        f"{fname} 调用 {callee} 前，`.tu` 里第 {getattr(expr, 'line', 0)} 行"
                        f"写的前提 `{raw}` 本工具**认不出来**（{why}），所以它没有被检查；"
                        f"修法：①改成本工具认得的形状——`xs.size()` / `xs.length()` 与"
                        f"整数字面量比较、`is_sorted(xs)`、标量变量与整数字面量比较，"
                        f"或**两个数值项之间的比较**（`lo <= hi`、`a < b`、`hi <= xs.size()`；"
                        f"数值项 = 变量 / `.size()` / 它们与整数字面量的 `+ - * / %`）；"
                        f"②或把这项检查改写进函数体。哪些形状会被检查见 "
                        f"`文档/语法清单.md` 的 `requires` 一节",
                        call.line, call.col))
                    continue
                if p.spell_node is not None:
                    key = (getattr(p.spell_node, "line", 0), getattr(p.spell_node, "col", 0))
                    if key not in warned:
                        warned.add(key)
                        out.append((
                            "W-Builtin-Deprecated",
                            "`length` 已弃用（v0.73 移除）：改用 `.size()`"
                            "（`requires` 里两种拼写检查同一件事）",
                            key[0], key[1]))
                if not _prove(cctx, p, pidx, items):
                    actuals = ""
                    if p.kind == "rel":
                        actuals = ("（调用点的实参：" +
                                   "、".join(f"`{_render(_unwrap(a))}`" for a in items) +
                                   "；这些实参之间没有被证明成立的关系）")
                    out.append((
                        "E-Require-Unproven",
                        f"{fname} 调用 {callee} 前必须保证 `{_shape(p)}`，但检查器证明不了"
                        f"{actuals}；修法：①在前面加 `if` 守卫；"
                        f"②或把这项检查放回 {callee} 的函数体",
                        call.line, call.col))
    return out
