#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""成本表达式与 `while` 上界提取（G29）。

规格里的成本表达式（§7.2）：

    κ ::= 1 | n | x̂ | κ₁ + κ₂ | n · κ | κ₁ ⊔ κ₂ | log₂(x̂) | |x|

本模块只实现「提取上界」需要的那一小块：把条件表达式变成界表达式。

**界表达式**比成本表达式更窄——它只有这几种形态：

    B ::= N            （常数或符号量）
        | x.length()   （尺寸调用）
        | L(x)         （**元素长度上界**：`x[i].length()` 当 `i` 是变量下标时用它，
                        见模块头「内层容器的尺寸」）
        | x            （变量）
        | a - b        （两个符号量之差，用于 log(hi - lo) 这类）
        | B + N        （界 + 整数字面量，**只有 repeats 入口**收，见
                        `expr_to_bound(allow_add=True)`；比较里的 `+` 仍不收）

**上界提取规则（G29 甲方案：认形状）**

| 条件 | 上界 |
|---|---|
| `i < e` | `e - i`（i 从初值往上数，圈数约等于 e - i） |
| `i <= e` | `e - i + 1`（**多一圈**，见下） |
| `i > e` | `i - e` |
| `i >= e` | `i - e + 1`（**多一圈**） |
| `i + c < e` / `e > i + c` | `e`（分块扫描；要求 `i` 初值为 0，见下） |
| `i + c <= e` / `e >= i + c` | `e`（同上，但要求 `c >= 1`） |
| `s.length() > 0` | `s.length()` |
| `a and b` | `min(B(a), B(b))` |
| `a or b` | `max(B(a), B(b))` |
| `P(args)` 且 P 有 repeats | P 的 repeats 表达式，实参代入 |
| 其它 | `None`（抽不出） |

**抽不出时的处理（丙方案的精神）**：**明确返回 `None` 并给出原因**，
由调用方决定报错还是记 ⊤——**绝不静默超时**。

## `<=` / `>=` 的「多一圈」（2026-11-10 修的真实破界）

`i <= e` 的圈数是 **`e - i0 + 1`**，比 `i < e` 多**整整一圈**。这条规则一直
写在上表里，但 `_sub_or_plain` 以前把 `incl` 收下却不 +1（注释理由是"上界取松
不失真"），后果正好相反——圈数记少一圈，κ 的常数项只预留了一份守卫/推进。
实测（`_cppcompat_probe/le_for.eln`）：

    func fl(u64 n) -> u64 {
        u64 s = 0;
        for (u64 i = 0; i <= n; i = i + 1) { s = s + i; }
        return s;
    }

| n | ops | 修前 κ = 14n + 20 | 修后 κ = 14(n+1) + 20 |
|---|---|---|---|
| 0 | 22 | 20 ✗ **破界** | 34 ✓ |
| 1 | 33 | 34 ✓ | 48 ✓ |
| 2 | 44 | 48 ✓ | 62 ✓ |
| 3 | 55 | 62 ✓ | 76 ✓ |
| 8 | 110 | 132 ✓ | 146 ✓ |

**`<` / `>` 一侧逐字不变**（它们本来就是对的那一侧）——改动只落在
`incl = True` 的两支上，`_plus_one` 造的是 `add` 节点，`Cost.add` 会把它
化进常数项。同一处病还出现在两个"认形状"的入口，一并修：

· `_log_loop`：`u64 i = 1; while (i <= n) { i = i * 2; }` 的圈数是
  `floor(log2 n) + 1`，界记成 `log2 n` 时 **n = 1 实测 κ = 13 < ops = 15**；
  现在是 `log2(n + 1)`（同样是 `O(log n)` 阶，判阶不受影响）。
· 递减循环（`while (j >= 1)`，`j = j - 1`）：`v0` 圈是 `>` 的答案，`>=` 要
  `v0 + 1`。

**分块扫描不受影响**：`i + c <= e`（初值 0、每轮恰增 c）的真圈数是
`floor(e / c)`，`ceil(e / c)` 已经是上界（`ceil ≥ floor`），不需要再加。

## 分块 / 窗口扫描：`while (i + 3 <= n)`（2026-11-10）

C / C++ / Rust 里「每轮吃 k 个字节」的自然写法是 **`i + k <= n`**（base64 /
CRC32 / hexdump 都这么写），而这条守卫**不是** `计数器 op 界` 的形状，以前
一律记 ⊤——最自然的写法反而丢掉整条契约。

**放开的确切形状**（只有这四种，`c` 是**非负整数字面量**、`e` 是老规则能推出
的界表达式）：

| 守卫 | 上界（步长能证成 `+ c`） | 退路（证不出） |
|---|---|---|
| `i + c < e` | `ceil(e / c)` | `e` |
| `e > i + c` | 同上（翻成 `i + c < e` 再判） | `e` |
| `i + c <= e` | `ceil(e / c)`（**要求 `c >= 1`**） | `e` |
| `e >= i + c` | 同上（翻成 `i + c <= e`） | `e` |

**为什么能收紧成 `ceil(e / c)`（2026-11-10 加）**：`i` 从 0 起、每轮恰增 `c`
时，`i` 的取值是 `0, c, 2c, …`，`i + c <= e` ⟺ `i <= e - c`，所以圈数
= `floor(e / c)`；`<` 型是 `floor((e - 1) / c)`。两者都 `<= ceil(e / c)`。
**这是一个等式推导，不是放缩**——但前提（初值 0、每轮恰增 c）必须**证明**，
见 `_exact_step_increment` 的四条判据；证不出就退回 `e`（旧上界，松但不失真）。

**为什么 `<=` 要求 `c >= 1`**：`i + 0 <= e` 的真实圈数是 `e + 1`（初值 0、
每轮 +1），取 `e` 会**偏小一整圈**——上界不许偏小，所以这一格明确不收
（`i + 0 < e` 仍收，它等于既有的 `i < e`；`c = 0` 也不做除法，除零没有意义）。

**为什么要求计数器初值为 0**（`zero_vars`）：初值非 0 时真正要紧的是 `e - i0`，
而 `bound_of_condition` 拿不到函数体去求静态初值；拿不到就**不猜**——`i64` 的
负初值会让 `e` 偏小。宁可 ⊤。**注意 `i0 >= 0` 的方向**：`floor((e - i0)/c)` 关于
`i0` 单调递减，所以「初值比我以为的更大」是安全的一侧；不安全的是初值**更小**
（负数），`zero_vars` + 「全函数只有声明与体内那一次 +c」把这一侧也堵死了。

**仍然拒的（反面清单）**：`*`、`/`、`-`、两侧都不是裸计数器、字面量在
**上界那一侧**（`i < n + 1`）、`i + c > e` / `i + c >= e`（计数器在高侧，
可能是死循环）、`i - c >= 0`（u64 下恒真 = 死循环）——这些仍是 ⊤。
`expr_to_bound` 本身**没有**放开 `+`，所以界表达式 / target / repeats 的入口
一个都没动。

**收紧的边界（严格）**：`ceil(e / c)` 只在 `_exact_step_increment` 证明得出
「声明处初值字面量 0 + 全函数里对计数器的写**恰好一处**、且它是循环体的直接
子语句 `i = i + c` / `i += c`（`c` 与守卫里的字面量相同）」时才用。任何一条
不满足（步长是变量 `i += k`、步长与守卫的 `c` 不等、增量写在 `if` 里、
体内出现两次、循环外另有赋值、按引用传出、当方法接收者、声明两遍）都用旧上界
`e`——**绝不给出比真实小的数**。

## 内层容器的尺寸：`L(g)`（2026-11-12）

`vector<vector<u64>>` 的内层循环最自然的写法是

    for (u64 k = 0; k < g[v].size(); ++k) sum += g[v][k];

`g[v].size()` 的被量对象是**外层容器的第 v 个元素**，`v` 是变量 ⇒
`size_receiver_name` 给不出名字，以前整条循环记 ⊤（`g[v].size()` 单独取值是
能算的 `7`——**只有"当循环上界"才断**，因为上界必须是可渲染的符号量）。

**两种不同的上界，别混**：

| 候选 | 结论 |
|---|---|
| `n(g[v]) ≤ n(g)` | **不一定成立**：`vector<vector<u64>>` 里每行可以比行数长（10 行、每行 10⁶ 个元素）。**不允许**在没有前提时用 |
| `n(g[v]) ≤ L(g)` | **按定义成立**：`L(g) := max_{0 ≤ i < n(g)} n(g[i])`（空容器取 0），`v` 合法 ⇒ `g[v]` 是其中一个元素 ⇒ 长度不超过最大值 |

所以这里只产出 **`L(g)`**（`expr_to_bound` 的 `elem` 分支）。它是一个
**符号尺寸量**（不是数字、不是猜的常数）：没有 `.tu` 时 κ 里就带着它，
有契约时 `target { time: { max: O(g.size() * L(g)) } }` 用**同一个名字**。

**健全性前提（三条，缺一即 ⊤）**：

1. 外层容器 `g` 必须是**本帧的 const 形参**（`const Graph& g`）。const 保证
   函数执行期间**元素个数与每个元素的长度都不变**——`L(g)` 才是全程上界；
   `g.push_back(…)` / `g[v].push_back(…)` / 把 `g` 以非 const 引用传出去
   都会破坏它，实现里另外还扫了一遍写点与传参（双保险）。
2. 下标只要求"合法"：`v >= n(g)` 时 `g[v]` 本身就是未定义行为，不在 κ 的
   讨论范围内（与 `xs[i]` 的既有口径一致）。
3. `L(g)` 是**数据相关**的量：它的值取决于实参，不由 `n(g)` 唯一决定。
   判阶时它与 `n(g)` 是两个独立尺寸量（`_profile` 当线性），**不许**互相
   替换——`L(g) ≤ n(g)` 只有"简单图"这种额外前提才成立，本工具不假设它。
"""

from typing import List, Optional, Tuple

from parser import Node
from ast_util import descendants as _descendants


# ---------------------------------------------------------------- 界表达式

class Bound:
    """界表达式。`kind` 取值：const / size / var / sub / add / min / max / log2 /
    call / **ceildiv**。

    `ceildiv` 是**向上取整除法**：`ceil(left / value)`，`value` 是正整数
    字面量（`>= 1`）。它只为「步长循环」存在——`while (i + 3 <= n)` 每轮吃 3
    个字节，圈数不是 `n` 而是 `ceil(n / 3)`，比 `n` 紧 3 倍。构造点只有一处
    （`_block_scan_bound`），而且**必须先证明**初值 0 与「每轮恰增 c」
    （见 `_exact_step_increment`）。

    为什么不像 `mul` 那样把 `c` 放进 `right`：`c` 是**常数**，不是界表达式；
    放进 `value` 才能保证它一定是字面量、一定能转成 `Cost.num(c)` 做分母
    （`Cost.div` 需要分母是正常数才能在 `_profile` / `_monomials` 里判阶）。
    """

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
        if self.kind == "elem":
            # **元素长度上界** `L(g)`：与 `Cost("sym", "L(g)")` 逐字同名，
            # κ 与 target 才对得上（见模块头「内层容器的尺寸」）。
            return f"L({self.value})"
        if self.kind == "sub":
            return f"({self.left.render()} - {self.right.render()})"
        if self.kind == "add":
            return f"{self.left.render()} + {self.right.render()}"
        if self.kind == "min":
            return f"min({self.left.render()}, {self.right.render()})"
        if self.kind == "max":
            return f"max({self.left.render()}, {self.right.render()})"
        if self.kind == "log2":
            return f"log2({self.left.render()})"
        if self.kind == "ceildiv":
            # 分子是 `-` / `+` / `min` / `max` 时加括号：`ceil((n - lo) / 3)`
            # 不加括号会读成 `(ceil(n - lo)) / 3`——那是另一个式子。
            inner = self.left.render() if self.left is not None else "?"
            if self.left is not None and self.left.kind in ("sub", "add", "min", "max"):
                inner = f"({inner})"
            return f"ceil({inner} / {self.value})"
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
        if self.kind == "elem":
            return [self.value]
        if self.kind in ("sub", "add"):
            return self.left.variables() + self.right.variables()
        if self.kind in ("min", "max", "log2"):
            return self.left.variables()
        if self.kind == "ceildiv":
            # 分母是字面量，不是变量；只收分子里的名字。
            return self.left.variables() if self.left is not None else []
        if self.kind == "call":
            out = []
            for a in self.args:
                out.extend(a.variables())
            return out
        return []


SIZE_FNS = ("length", "size", "capacity", "len")


def field_chain_name(n: Optional[Node]) -> Optional[str]:
    """`s.v` / `a.b.c` 这类**纯字段链**的点分名；不是字段链 → None。

    **为什么需要它**（2026-11-12）：真实 C++ 的尺寸量很少是"裸局部 / 裸形参"，
    更常见的是**结构体字段**——`while (i < s.v.size())`。以前 `expr_to_bound`
    只认 `children[0].kind == "path"`，字段接收者一律落成 `"?"`（一个假尺寸
    量），κ 于是变 ⊤。字段链的名字可以**直接渲染成尺寸符号** `n(s.v)`：它不
    需要是数字，也不该被猜成常数。

    只收 path / field（以及包着它们的 paren/try/ref/deref）：
    `f().size()` / `xs.at(i).size()` 这种"结果上的尺寸"仍然返回 None——那要
    求值才写得出来，宁可 ⊤ 不猜。
    """
    if n is None:
        return None
    k = getattr(n, "kind", "")
    if k in ("paren", "try", "ref", "deref"):
        kids = getattr(n, "children", None) or []
        return field_chain_name(kids[0]) if kids else None
    if k == "path":
        nm = getattr(n, "name", "") or ""
        return nm or None
    if k == "field":
        kids = getattr(n, "children", None) or []
        base = field_chain_name(kids[0]) if kids else None
        nm = getattr(n, "name", "") or ""
        if not base or not nm:
            return None
        return f"{base}.{nm}"
    return None


def size_receiver_name(n: Optional[Node]) -> Optional[str]:
    """`x.size()` / `s.v.length()` / `len(xs)` 的**被量对象名**；推不出 → None。

    接收者既可以是 `path` / `field`，也可以是自由函数写法的第一个实参
    （`len(xs)`）。**绝不返回 `"?"`**：`?` 会被 `cost_vars()` 当成一个普通尺寸量，
    `n(?)` 参与判定就是把"不知道"伪装成"知道"。
    """
    if n is None:
        return None
    kids = getattr(n, "children", None) or []
    if kids:
        got = field_chain_name(kids[0])
        if got:
            return got
    for c in kids:
        if getattr(c, "kind", "") in ("args", "arg-list"):
            for a in (getattr(c, "children", None) or []):
                got = field_chain_name(a)
                return got
    return None


def elem_receiver_name(n: Optional[Node]) -> Optional[str]:
    """`g[v].size()` / `g.at(v).size()` 的**外层容器名**；推不出 → None。

    只在接收者是**下标表达式**（`index`，或 `at` / `index` 方法调用）时给名字：
    `g[v]` 是 `g` 的一个元素，长度用 `L(g)` 上界（见模块头「内层容器的尺寸」）。
    `f()[i].size()` / `make().at(i).size()` 的基不是字段链 → None，宁可 ⊤。
    """
    if n is None:
        return None
    kids = getattr(n, "children", None) or []
    if not kids:
        return None
    recv = kids[0]
    while getattr(recv, "kind", "") in ("paren", "try", "ref", "deref"):
        rk = getattr(recv, "children", None) or []
        if not rk:
            return None
        recv = rk[0]
    if getattr(recv, "kind", "") == "index":
        base = (getattr(recv, "children", None) or [None])[0]
        return field_chain_name(base)
    if getattr(recv, "kind", "") in ("method-call", "call"):
        nm = (getattr(recv, "name", "") or "").split(".")[-1]
        if nm in ("at", "index"):
            base = (getattr(recv, "children", None) or [None])[0]
            return field_chain_name(base)
    return None


# ---------------------------------------------------------------- 表达式 → 界

def expr_to_bound(n: Node, allow_add: bool = False) -> Optional[Bound]:
    """把一个**表达式**变成界表达式；认不出返回 None。

    `allow_add=True` **只给 `repeats` 这一个入口用**（`cost_interp._collect_repeats`
    与 `Interp._repeats_bound`），为的是能写出**严格**的圈数上界：

        repeats log(hi - lo) + 1;      // 二分真圈数 floor(log2 m) + 1

    放开范围**只有一种形状**：`绑定项 + 整数字面量`（字面量可以在任一侧），
    且「绑定项」按 `allow_add=False` 再解析一遍——所以

      · `log(hi - lo) + 1`      → 收（`add(log2(sub(hi, lo)), 1)`）
      · `log(hi - lo) + 1 + 2`  → 不收（只放开一层，不做算术化简）
      · `(hi - lo) + 1`         → 收（绑定项是 `-`，仍是界形状）
      · `i < hi + 1` 里的比较    → 不收（比较走默认入口，`+` 仍拒收）

    最后一条是**不能全面放开**的理由：`while (a * b < 10)` 这类条件一旦允许
    把 `+`／`*` 当界，就会把「乘法比较」误判成计数器比较，给出假上界。
    """
    if n is None:
        return None

    if n.kind == "lit-int":
        return Bound("const", value=n.value)

    if n.kind == "path":
        return Bound("var", value=n.name)

    if n.kind == "field":
        # **标量成员当界**（2026-11-12）：`while (row < recv.n_)`。
        #
        # 以前这里只认 `path`，字段一律落成 `None` ⇒ `--compat` 把 `class`
        # 降级成 `QueenSolver_render(recv)` 之后，`recv.n_` 一出现整条循环就 ⊤。
        # 名字写成**字段链**（`recv.n_`）：它在 κ 里就是尺寸量
        # `n(recv.n_)`（"这个成员的取值"），调用点再把它换到实参那个对象上
        # （`_receiver_size_renames`：`n(solver.n_)`）。
        #
        # **为什么打 `FIELD_TAG`**：成员当界比"容器长度当界"多一条前提——
        # 这个字段在函数执行期间**不许被改写**（循环条件每轮重读它）。标签让
        # 成本域**只对字段这一种**界做那条判定，不牵连 `s.v.size()` 那一路
        # （容器尺寸是另一回事：`while (i < s.v.size()) { s.v.push(…) }` 由
        # 增长预扫的"条件依赖被写容器"规则处理，不是这里）。
        nm = field_chain_name(n)
        return Bound("size", value=nm, args=[FIELD_TAG]) if nm else None

    if n.kind in ("method-call", "call"):
        fn = (n.name or "").split(".")[-1]
        if fn in SIZE_FNS:
            # `xs.length()` / `s.v.size()` —— 被量对象是**路径或字段链**。
            # 推不出（`f().size()`）→ None，绝不写 `?`。
            got = size_receiver_name(n)
            if got:
                return Bound("size", value=got)
            # **元素长度当上界**：`g[v].size()` 的被量对象是下标表达式。
            # 用 `L(g)`（元素长度的最大值），**不是** `n(g)`——后者不一定成立。
            # `ELEM_TAG` 让成本域在把它翻成 Cost 之前补一条前提：外层容器在本帧
            # 是 const 形参、且没有被写/传出去（见模块头）。
            elem = elem_receiver_name(n)
            if elem:
                return Bound("elem", value=elem, args=[ELEM_TAG])
            return None
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
        if op == "+" and allow_add:
            # **只认这一种形状**：`绑定项 + 整数字面量`（见函数头）。
            # 非字面量那一侧用**默认入口**再解析：`+` 不再递归放开。
            if getattr(b, "kind", "") == "lit-int":
                term, lit = a, b
            elif getattr(a, "kind", "") == "lit-int":
                term, lit = b, a
            else:
                return None
            tb = expr_to_bound(term)
            if tb is None:
                return None
            return Bound("add", left=tb, right=Bound("const", value=lit.value))
        # `+` / `*` 默认不做界（它们的上界需要更多算术推理，留给 M2）。
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


# ---------------------------------------------------------------- repeats 的「圈数名」

#: `Bound.args` 上的标记：这个界里的裸名字是**圈数**（`repeats` 的写法），
#: 不是容器尺寸。
COUNT_TAG = "count"

#: `Bound.args` 上的标记：这个 `size` 界是**标量成员**（`recv.n_`），不是容器长度。
#: 成本域据此多判一条前提：该字段在函数里不许被改写（见 `expr_to_bound`）。
FIELD_TAG = "field-value"

#: `Bound.args` 上的标记：这个 `elem` 界是**元素长度上界**（`g[v].size()` → `L(g)`）。
#: 成本域据此多判一条前提：外层容器在本帧是 const 形参、且没有被写/传出去
#: （见模块头「内层容器的尺寸」）。
ELEM_TAG = "elem-len"


def _mark_count(b: Optional[Bound]) -> Optional[Bound]:
    """把 **repeats 表达式**里的裸名字标成「圈数符号」。

    `repeats log2(k)` 的 `k` 是**标量**（循环圈数）。成本域对未知名字的兜底是
    `Cost.size(k)`，渲染成 `n(k)` —— 那是**容器尺寸**的写法，求值时按
    `env.get("k", 1)` 查不到就按 1 算，κ 于是不再是上界（实测
    `_tier_a/union_find/probe_repeats/log2` 渲染出 `log2(n(n))`）。
    带上这个标记后，`_bound_to_cost_scoped` 的兜底改用 `Cost("sym", k)`，
    渲染成 `log2(k)`。

    只标 `var`（裸名字）。`xs.length()` 已经是 `size`，`4` 是 `const`，
    局部别名会在成本域里先按初值展开——这三类都不受影响。
    """
    if b is None:
        return None
    if b.kind == "var":
        return Bound("var", value=b.value, args=[COUNT_TAG])
    if b.kind in ("sub", "add", "min", "max"):
        return Bound(b.kind, left=_mark_count(b.left),
                     right=_mark_count(b.right))
    if b.kind == "log2":
        return Bound("log2", left=_mark_count(b.left))
    if b.kind == "ceildiv":
        # 分子里的裸名字同样是圈数符号（repeats 目前不产出 ceildiv，这一支是
        # 为「将来 rounds 也能写 ceil(...)」留的一致性；漏掉它就会渲染成 `n(名字)`）
        return Bound("ceildiv", value=b.value, left=_mark_count(b.left))
    return b


def is_count_var(b) -> bool:
    """这个界节点是不是「repeats 里的标量名」。"""
    return (getattr(b, "kind", "") == "var"
            and COUNT_TAG in (getattr(b, "args", None) or []))


# ---------------------------------------------------------------- 分块扫描形状

def _counter_plus_literal(n: Optional[Node]):
    """`i + c` → `(i, c)`；不是这个形状返回 None。

    **判据刻意写窄**（宁可判不出，不可判错）：

      · 只认 `+`，且一侧是**裸计数器路径**、另一侧是**非负整数字面量**；
      · `i + j`（另一侧是变量）、`(i + 1) + 1`（一侧是表达式）、
        `i * 3` / `i / 3` / `i - 3`（别的运算符）**全部返回 None**。

    窄的理由：`expr_to_bound` 一旦把 `+` 当界，`while (a * b < 10)` 这类
    「把算术当计数器」的条件也会跟着被认，给出**假上界**。这里只在
    `bound_of_condition` 的比较入口用，界表达式本身的入口一个都没放开。
    """
    if getattr(n, "kind", "") != "binary" or (getattr(n, "value", "") or "") != "+":
        return None
    a, b = (list(getattr(n, "children", None) or []) + [None, None])[:2]
    if (getattr(a, "kind", "") == "path" and getattr(a, "name", None)
            and getattr(b, "kind", "") == "lit-int"):
        term, lit = a, b
    elif (getattr(b, "kind", "") == "path" and getattr(b, "name", None)
            and getattr(a, "kind", "") == "lit-int"):
        term, lit = b, a
    else:
        return None
    try:
        c = int(lit.value)
    except (TypeError, ValueError):
        return None
    if c < 0:
        return None
    return term.name, c


def _counter_plus_expr(n: Optional[Node]):
    """`i + e` → `(i, e)`（`e` **不是**整数字面量）；不是这个形状返回 None。

    与 `_counter_plus_literal` 分开：字面量那一支能收紧成 `ceil(e / c)`，
    这一支（`e` 是尺寸表达式，如 `i + needle.size() <= hay.size()`）只能给
    **宽松但正确**的上界（见 `_block_scan_bound_sym`）。

    **判据同样写窄**：只认 `+`，一侧是**裸计数器路径**、另一侧**不是路径**
    （`i + j` 里 `j` 可能是任意标量，符号未知 ⇒ 不收；`i + xs[j]` 同理）。
    """
    if getattr(n, "kind", "") != "binary" or (getattr(n, "value", "") or "") != "+":
        return None
    a, b = (list(getattr(n, "children", None) or []) + [None, None])[:2]
    a_path = (getattr(a, "kind", "") == "path" and getattr(a, "name", None))
    b_path = (getattr(b, "kind", "") == "path" and getattr(b, "name", None))
    if a_path and not b_path and getattr(b, "kind", "") != "lit-int":
        return a.name, b
    if b_path and not a_path and getattr(a, "kind", "") != "lit-int":
        return b.name, a
    return None


def _offset_nonneg(b) -> bool:
    """偏移量（`i + <这个>`）能不能证成**非负**。

    容器长度 / 元素长度上界按定义 ≥ 0；常数要 ≥ 0；`a + b` 逐项判。
    **变量、差值、log 一律判否**——`i + k` 里 `k` 可能是负的（`i64`），
    负偏移会让「轮数 ≤ N」偏小（`i` 可以走得更远），那是**破界**。
    """
    if b is None:
        return False
    k = getattr(b, "kind", "")
    if k in ("size", "elem"):
        return True
    if k == "const":
        try:
            return int(b.value) >= 0
        except (TypeError, ValueError):
            return False
    if k == "add" and b.left is not None and b.right is not None:
        return _offset_nonneg(b.left) and _offset_nonneg(b.right)
    return False


def _counter_only_increases(fn: Optional[Node], wh: Optional[Node],
                            counter: str) -> bool:
    """`counter` 在全函数里**只增不减**、且循环体内至少推进一次。

    比 `progresses` 严格：后者的判据是"不阻塞"，这里要的是「`i` 每轮都变大」——
    `i + C <= N` 的轮数上界 `N`（或 `N + 1`）成立的前提就是
    「`i` 从非负起、每轮严格增大，而每轮都满足 `i <= N`」。

    判据（**宁可判不出，不可判错**）：

      · 全函数里对 `counter` 的每一次写都必须是 `i = i + c` / `i += c`，
        且 `c` 是**正整数字面量**（`i = i - 1` / `i *= 2` / `i = i + g.size()`
        → 判否：最后一种的步长可以是 0，循环可能不推进）；
      · 这些写里至少有一处在循环体内（否则循环自己不推进 → E21 那条线接管）。
    """
    if fn is None or wh is None or not counter:
        return False
    body = next((x for x in (getattr(wh, "children", None) or [])
                 if getattr(x, "kind", "") == "block"), None)
    if body is None:
        return False
    in_body = _subtree_ids(body)
    seen_in_body = False
    writes = 0
    for kind in ("assign", "assign-op"):
        for a in _descendants(fn, kind):
            lhs = (getattr(a, "children", None) or [None])[0]
            if getattr(lhs, "kind", "") != "path" or getattr(lhs, "name", "") != counter:
                continue
            writes += 1
            rhs = (getattr(a, "children", None) or [None, None])[1]
            if not _positive_step(rhs, kind, a):
                return False
            if id(a) in in_body:
                seen_in_body = True
    return writes > 0 and seen_in_body


def _positive_step(rhs, kind: str, a) -> bool:
    """`i = i + e` / `i += e` 里 `e` 是否**严格为正**。

    **只认正整数字面量**。尺寸表达式（`i = i + g.size()`）不在这里放行：
    容器长度可以是 **0**，那一步就不推进——循环可能原地打转，κ 的有限上界
    立刻失真。`+ 0` 同理。宁可在这一格退回 ⊤。
    """
    if rhs is None:
        return False
    if kind == "assign-op":
        if (getattr(a, "value", "") or "") != "+=":
            return False
        e = rhs
    else:
        if getattr(rhs, "kind", "") != "binary" or (getattr(rhs, "value", "") or "") != "+":
            return False
        x, y = (list(getattr(rhs, "children", None) or []) + [None, None])[:2]
        xs = [getattr(x, "kind", ""), getattr(y, "kind", "")]
        if "path" not in xs:
            return False
        # `i + i` 这种自加不算推进（值会翻倍，仍增大；但形状太怪，不收）。
        e = y if getattr(x, "kind", "") == "path" else x
        if getattr(e, "kind", "") == "path":
            return False
    if getattr(e, "kind", "") == "lit-int":
        try:
            return int(e.value) > 0
        except (TypeError, ValueError):
            return False
    return False


def _subtree_ids(root) -> set:
    """`root` 这棵子树上所有节点的 `id()`（判断"某节点在不在循环体里"）。"""
    out = set()
    if root is None:
        return out
    stack = [root]
    while stack:
        nd = stack.pop()
        out.add(id(nd))
        stack.extend(getattr(nd, "children", None) or [])
    return out


def _exact_step_increment(fn: Optional[Node], wh: Optional[Node],
                          counter: str, c: int) -> bool:
    """证明「`counter` 进循环时恰为 0，且每轮恰增 `c`」——收紧上界的**唯一**前提。

    `while (i + c <= e)` 的圈数不是 `e` 而是 `floor(e / c)`，但这**只**在
    `i` 从 0 起、每轮恰增 `c` 时成立。证不出就用旧上界 `e`：松一点不失真，
    收紧一旦证错就变成**下界**（这正是最怕的失真）。

    四条判据（**宁可判不出，不可判错**）：

      ① **声明唯一且初值是字面量 0**：全函数里 `counter` 只声明一次、带初值
         `= 0`，而且声明**不在**循环体里（声明在体内时，进循环那一刻的值不是 0）。
      ② **全函数里对它的写只有一处**，且这一处必须是**循环体的直接子语句**，
         形如 `counter = counter + c` 或 `counter += c`。
         — 写在 `if` / `else` / 嵌套循环里 ⇒ 不保证**每轮**都推进 ⇒ 判否
           （`while (i + 3 <= n) { if (f) i = i + 3; }` 的真圈数可以到 n）；
         — 步长是变量（`i += k`）或不是字面量 ⇒ 判否；
         — 步长与守卫里的 `c` **不等** ⇒ 判否（`i + 3 <= n` 配 `i += 2`
           的真圈数是 `n/2`，用 `n/3` 就是**偏小**）；
         — 体内写两次、循环外再赋一次 ⇒ 判否（初值/推进量都变了）。
      ③ **没有按引用传出去**（`f(& i)`）：被调函数可能把它改小——改小意味着
         圈数变多，`floor(e/c)` 就不再是上界。
      ④ **没有当方法接收者**（`i.pop()` 之类的写法），同上。

    第 ① 条与 `zero_vars`（`zero_initialised`）**不是重复**：`zero_vars`
    只读声明处的 `= 0`，管不了 `i = 5;` 这样的**循环外再赋值**。赋成更大的值
    是安全侧（圈数更少），赋成更小的值（`i64` 的负数）就是**不安全**的一侧
    （圈数变多）。两者在这里长得一模一样，分不清就判否。
    """
    if fn is None or wh is None or c < 1 or not counter:
        return False
    body = next((x for x in (getattr(wh, "children", None) or [])
                 if getattr(x, "kind", "") == "block"), None)
    if body is None:
        return False

    # ---- ① 声明唯一、初值字面量 0、不在循环体里
    decls = []
    for k in ("var-decl", "const-decl"):
        for d in _descendants(fn, k):
            if getattr(d, "name", "") == counter:
                decls.append(d)
    if len(decls) != 1:
        return False
    decl = decls[0]
    kids = getattr(decl, "children", None) or []
    if len(kids) < 2:
        return False
    init = kids[-1]
    if getattr(init, "kind", "") != "lit-int":
        return False
    try:
        if int(init.value) != 0:
            return False
    except (TypeError, ValueError):
        return False
    if id(decl) in _subtree_ids(body):
        return False

    # ---- ② 全函数里对它的写恰好一处，且是循环体的直接子语句 `+ c`
    step = None
    for kink in ("assign", "assign-op"):
        for a in _descendants(fn, kink):
            lhs = (getattr(a, "children", None) or [None])[0]
            if getattr(lhs, "kind", "") != "path" or getattr(lhs, "name", "") != counter:
                continue
            if step is not None:
                return False                     # 写了两处以上：初值/推进量不可信
            step = a
    if step is None:
        return False
    if step not in list(getattr(body, "children", None) or []):
        return False                             # 不保证每轮都执行到
    if not _is_plus_literal_step(step, counter, c):
        return False

    # ---- ③④ 别的地方可能偷偷改它
    for r in _descendants(fn, "ref"):
        for p in _descendants(r, "path"):
            if getattr(p, "name", "") == counter:
                return False
    for mc in _descendants(fn, "method-call"):
        recv = mc.children[0] if getattr(mc, "children", None) else None
        if getattr(recv, "kind", "") == "path" and getattr(recv, "name", "") == counter:
            return False
    return True


def _is_plus_literal_step(st: Node, counter: str, c: int) -> bool:
    """这条语句是不是 `counter = counter + c` / `counter += c`（`c` 是字面量）。"""
    rhs = (getattr(st, "children", None) or [None, None])[1]
    if getattr(st, "kind", "") == "assign-op":
        if (getattr(st, "value", "") or "") != "+=":
            return False
        if getattr(rhs, "kind", "") != "lit-int":
            return False                        # `i += k`（变量步长）⇒ 不收紧
        try:
            return int(rhs.value) == c
        except (TypeError, ValueError):
            return False
    if getattr(rhs, "kind", "") != "binary" or (getattr(rhs, "value", "") or "") != "+":
        return False
    a, b = (list(getattr(rhs, "children", None) or []) + [None, None])[:2]
    if getattr(a, "kind", "") == "path" and getattr(a, "name", "") == counter:
        lit = b
    elif getattr(b, "kind", "") == "path" and getattr(b, "name", "") == counter:
        lit = a
    else:
        return False
    if getattr(lit, "kind", "") != "lit-int":
        return False
    try:
        return int(lit.value) == c
    except (TypeError, ValueError):
        return False


def exact_step_prover(wh: Optional[Node], fn: Optional[Node]):
    """给 `bound_of_condition` 的**步长证明器**：`(计数器, c) -> 能不能证明每轮恰增 c`。

    只有拿到 `while` 节点与所在函数才做得了这件事（要看循环体），所以由
    `bound_of_while` / `bound_of_condition_wrapper` 构造后传下去。
    拿不到函数（`fn is None`）时返回 None——**没有证明就没有收紧**，
    上界退回旧的 `e`。
    """
    if wh is None or fn is None:
        return None

    def prove(counter: str, c: int) -> bool:
        return _exact_step_increment(fn, wh, counter, c)

    return prove


def _block_scan_bound(counter: str, c: int, op: str, limit: Optional[Bound],
                      zero_vars, step_of=None) -> Tuple[Optional[Bound], str]:
    """`i + c op e`（已翻成计数器在低侧）→ `(界, 原因)`。

    `step_of` 能证明「初值 0 且每轮恰增 c」时给 `ceil(e / c)`（`ceildiv`），
    否则沿旧口径给 `e`——**证不出就不收紧**，见模块头「分块 / 窗口扫描」。
    """
    if limit is None:
        return None, f"{op} 比较里右侧不可推"
    # `i + 0 <= e` 的真实圈数是 e + 1 —— 取 e 会**偏小一整圈**，这一格不收。
    if op == "<=" and c < 1:
        return None, (f"`{counter} + 0 <= …` 等价于 `{counter} <= …`："
                      f"`<=` 多一圈，取 e 会偏小，本形状不收 c = 0")
    if counter not in zero_vars:
        return None, (f"分块扫描：`{counter} + {c}` 里的计数器 {counter} 初值不是 0，"
                      f"拿不到静态初值 ⇒ 推不出轮数（宁可 ⊤ 不猜）")
    # `c = 0` 不做除法（除零无意义，且 `i + 0 < e` 已等价于 `i < e`）。
    if c >= 1 and step_of is not None and step_of(counter, c):
        return Bound("ceildiv", value=c, left=limit), (
            f"分块扫描：`{counter}` 初值为 0、每轮恰增 {c} ⇒ "
            f"圈数 ≤ ceil({limit.render()} / {c})")
    return limit, (f"分块扫描：`{counter} + {c} {op} …` 按计数器比较，"
                   f"上界沿用 `{counter} {op} …` 的推法"
                   f"（步长证不出「初值 0 + 每轮恰增 {c}」⇒ 不收紧）")


# ---------------------------------------------------------------- 条件 → 上界

def _block_scan_bound_sym(counter: str, offset: Optional[Bound], op: str,
                          limit: Optional[Bound], nonneg_vars,
                          fn: Optional[Node] = None,
                          wh: Optional[Node] = None) -> Tuple[Optional[Bound], str]:
    """`i + <尺寸表达式> op e` → **宽松但正确**的轮数上界（见模块头）。

    推导（`i` 从非负的 `i0` 起、每轮严格增大，`C = offset`，`N = limit`）：

    | 守卫 | 每轮都满足 | 轮数 |
    |---|---|---|
    | `i + C < N`  | `i <= N - C - 1 < N` | `≤ N` |
    | `i + C <= N` | `i <= N - C`         | `≤ N + 1` |

    `i` 每轮都满足 `i <= N`（`C ≥ 0`）、且**严格增大**、从非负起 ⇒ 至多
    `N + 1` 个取值。`<` 那一侧再少一个（`i <= N - 1`），所以 `≤ N`。

    **为什么 `<=` 不是 `N`**：`C = 0` 时 `i + 0 <= N` 的真轮数是 `N + 1`
    （`i = 0 … N`）。这里**证不出 `C ≥ 1`**（`needle.size()` 可能是 0），
    所以按 `N + 1` 给——多一个常数项，但**绝不偏小**。这正是 `<=` 一贯的
    「多一圈」口径（见 `_sub_or_plain`）。

    严格增大的证明来自 `_counter_only_increases`；计数器初值非负来自
    `nonnegative_vars`。两条缺一即 ⊤。
    """
    if limit is None:
        return None, f"{op} 比较里右侧不可推"
    if offset is None:
        return None, "偏移量不可推"
    if not _offset_nonneg(offset):
        return None, (f"偏移量 `{offset.render()}` 可能是负的（变量/差值）⇒ "
                      f"「轮数 ≤ {limit.render()}」会偏小，本形状不收")
    if counter not in nonneg_vars:
        return None, (f"`{counter} + {offset.render()} {op} …`：计数器 {counter} "
                      f"的初值证不出非负 ⇒ 轮数可能超过 {limit.render()}（宁可 ⊤）")
    if not _counter_only_increases(fn, wh, counter):
        return None, (f"`{counter} + {offset.render()} {op} …`：证不出 {counter} "
                      f"每轮严格增大（全函数里只许 `= {counter} + 正量` / "
                      f"`+= 正量`）⇒ 不给上界")
    if op == "<":
        return limit, (f"偏移上界：`{counter} + {offset.render()} < "
                       f"{limit.render()}`，{counter} 非负且每轮严格增大 ⇒ "
                       f"轮数 ≤ {limit.render()}")
    return Bound("add", left=limit, right=Bound("const", value=1)), (
        f"偏移上界：`{counter} + {offset.render()} <= {limit.render()}`，"
        f"{counter} 非负且每轮严格增大 ⇒ 轮数 ≤ {limit.render()} + 1"
        f"（偏移量 ≥ 1 证不出，`<=` 按惯例多留一圈）")


# ---------------------------------------------------------------- 条件 → 上界

def nonnegative_vars(fn: Optional[Node]) -> set:
    """本函数里**初值可证非负**的变量名。

    只认三条（都只看声明处 / 类型，不做数据流）：

      ① 声明的类型是**无符号整数**（`u8/u16/u32/u64`）——`std::size_t` 经
         `--compat` 已折成 `u64`；
      ② 初值是**非负整数字面量**；
      ③ 初值是容器长度 / 元素长度上界（`xs.size()` / `g[v].size()`）。

    用途单一：`i + C op N` 的偏移上界只在 `i0 >= 0` 时成立。**推不出就不收**。

    **同名多处声明（不同作用域遮蔽）**：只有**每一处**声明都能证非负，名字才收。
    内层遮蔽外层时只看内层会让 `i64 i = -5; { u64 i = 0; }` 把外层的 `i` 误判成
    非负（破界）。
    """
    verdict: dict = {}
    if fn is None:
        return set()
    for k in ("const-decl", "var-decl"):
        for d in _descendants(fn, k):
            nm = getattr(d, "name", None)
            if not nm:
                continue
            kids = getattr(d, "children", None) or []
            ty = kids[0] if kids else None
            ok = False
            if getattr(ty, "kind", "") == "type-name" \
                    and (getattr(ty, "name", "") or "") in ("u8", "u16", "u32", "u64"):
                ok = True
            elif len(kids) >= 2:
                init = kids[-1]
                if getattr(init, "kind", "") == "lit-int":
                    try:
                        ok = int(init.value) >= 0
                    except (TypeError, ValueError):
                        ok = False
                else:
                    b = expr_to_bound(init)
                    ok = b is not None and b.kind in ("size", "elem")
            verdict[nm] = verdict.get(nm, True) and ok
    return {nm for nm, ok in verdict.items() if ok}


def predicate_call_of(cond) -> Optional[Node]:
    """条件若**等价于一个谓词调用**，返回那个 `call` 节点；否则 `None`。

    认的形态（2026-11-13，`repeats` 的读取处）：

    | 写法 | 例 |
    |---|---|
    | 肯定 | `p(x)` / `(p(x))` |
    | 否定（三种，互为等价写法） | `!p(x)` / `p(x) == false` / `p(x) != true` |
    | 肯定对偶 | `p(x) == true` / `p(x) != false` |
    | 字面量在前 | `false == p(x)` / `true != p(x)` |
    | 多重否定 / 多层括号 | `!!p(x)` / `!((p(x)))` |

    **只认「整个条件就是这一个谓词调用（可带布尔包装）」**：`&&` / `||`
    由 `bound_of_condition` 单独组合，`<` / `>` 等比较不收——那些形状
    多取一侧就可能低估圈数。推不出（`!(i < n)`、`p(x) == 0`…）返回 `None`，
    调用方记 ⊤，**不猜**。

    返回的是**剥掉括号与布尔包装后**的 `call`：调用方据此既取 `repeats`，
    也把条件成本按裸谓词计（四种等价写法必须给出同一个 κ，见
    `cost_interp._while_inner`）。
    """
    node = _plain(cond)
    if node is None:
        return None
    # 多重否定：`!!p(x)` 与 `p(x)` 是同一个条件（奇偶都收——这里只取"界"，
    # 不关心真假极性；圈数由 repeats 定）。
    while getattr(node, "kind", "") == "unary" and (node.value or "") == "!":
        kids = getattr(node, "children", None) or []
        if not kids:
            return None
        node = _plain(kids[0])
        if node is None:
            return None
    if getattr(node, "kind", "") == "binary" and (node.value or "") in ("==", "!="):
        a, b = (list(getattr(node, "children", None) or []) + [None, None])[:2]
        a, b = _plain(a), _plain(b)
        lit = None
        other = None
        if a is not None and getattr(a, "kind", "") == "lit-bool":
            lit, other = bool(getattr(a, "value", False)), b
        elif b is not None and getattr(b, "kind", "") == "lit-bool":
            lit, other = bool(getattr(b, "value", False)), a
        if lit is None or other is None:
            return None
        other = _plain(other)
        if (other is not None and getattr(other, "kind", "") == "call"
                and getattr(other, "name", "")):
            return other
        return None
    if getattr(node, "kind", "") == "call" and getattr(node, "name", ""):
        return node
    return None


def _strip_bool_wrapper(node: Node) -> Node:
    """去掉守卫里的**布尔包装**（`!` / `== false` / `!= true`），括号留着。

    为什么括号不剥：成本域对每个 `paren` 节点记 1 个操作
    （`cost_interp._k_expr_paren`，与解释器对每个节点记 1 同口径）。剥掉
    括号会让 κ 比 ops 小 —— 上界就不成立了。这里只拆包装，不拆括号；
    `predicate_call_of` 为了**认形状**才剥括号，两件事分开。

    没拆任何东西时返回**原节点**（调用方用 `is` 判断"要不要换"）。
    """
    k = getattr(node, "kind", "")
    if k == "paren" and getattr(node, "children", None):
        inner = node.children[0]
        new_inner = _strip_bool_wrapper(inner)
        if new_inner is inner:
            return node
        return Node("paren", getattr(node, "line", 0),
                    getattr(node, "col", 0)).add(new_inner)
    if k == "unary" and (node.value or "") == "!" and node.children:
        return _strip_bool_wrapper(node.children[0])
    if k == "binary" and (node.value or "") in ("==", "!="):
        a, b = (list(node.children) + [None, None])[:2]
        if a is not None and getattr(a, "kind", "") == "lit-bool":
            return _strip_bool_wrapper(b)
        if b is not None and getattr(b, "kind", "") == "lit-bool":
            return _strip_bool_wrapper(a)
    return node


def predicate_cost_node(cond: Optional[Node]) -> Optional[Node]:
    """条件成本该按哪个节点算：**去掉布尔包装、保留括号与谓词调用**。

    四种等价写法（`p(x)` / `!p(x)` / `p(x) == false` / `p(x) != true`）
    的圈数来自同一条 `repeats`，条件成本也按**裸谓词调用**计，κ 才会逐字节
    相同；多出来的 `!` / `==` 是解释器 ops 那边的记账（每轮 1–2 个），
    由每轮固定的循环开销与成本模型里已有的余量覆盖。

    **不是谓词条件就原样返回**（`return cond`）：`i < n`、`x != 0` 这些
    形状的成本一分都不许动。括号包裹的写法保留括号成本 —— 否则
    `((p(x) == false))` 这类会**少算**括号节点、κ 低于 ops。
    """
    if cond is None:
        return None
    if predicate_call_of(cond) is None:
        return cond
    return _strip_bool_wrapper(cond)


def bound_of_condition(cond: Node, repeats_of=None,
                       zero_vars=frozenset(), dec_vars=frozenset(),
                       init_of: Optional[dict] = None,
                       step_of=None, nonneg_vars=frozenset(),
                       fn: Optional[Node] = None, wh: Optional[Node] = None
                       ) -> Tuple[Optional[Bound], str]:
    """从 `while` 的条件推出迭代次数上界。

    返回 `(界, 原因)`；界为 None 时，原因说明为什么抽不出。

    `step_of(counter, c) -> bool` 是**步长证明器**（`exact_step_prover` 造）：
    只有它能证明「初值 0 且每轮恰增 c」时，分块扫描才收紧成 `ceil(e / c)`。
    不传（`None`）⇒ 一律沿用旧上界 `e`。

    `nonneg_vars` / `fn` / `wh`：**偏移上界**（`i + 尺寸表达式 op N`）的两条前提
    ——计数器初值非负、每轮严格增大（见 `_block_scan_bound_sym`）。
    """
    if cond is None:
        return None, "循环条件为空"

    # ---- `&&` / `||`（2026-11-11 起 AST 值就是符号；词形 and/or 已退役）
    if cond.kind == "binary" and (cond.value or "") in ("&&", "||"):
        a, b = (cond.children + [None, None])[:2]
        ba, ra = bound_of_condition(a, repeats_of, zero_vars, dec_vars, init_of,
                                    step_of, nonneg_vars, fn, wh)
        bb, rb = bound_of_condition(b, repeats_of, zero_vars, dec_vars, init_of,
                                    step_of, nonneg_vars, fn, wh)
        if ba is not None and bb is not None:
            kind = "min" if cond.value == "&&" else "max"
            return Bound(kind, left=ba, right=bb), f"{cond.value} 组合"
        # **`&&`：一侧可推就够**（2026-11-12）。
        # `while (A && B)` 两边都成立才继续 ⇒ 轮数 ≤ |A| **且** ≤ |B|，
        # 所以取**可推的那一侧**仍然是**上界**，不是"偏小"。
        # 之前这里一律收 ⊤，结果 `while (i > 0 && heap[i] < heap[p])` 这类
        # **最典型的堆/排序循环全部算不出 κ**（实测语料 48 个函数里 17 个 ⊤）。
        # **`||` 仍然必须两边都有**：`A || B` 的轮数可以接近 |A|+|B|，
        # 只取一侧就会低估——那种情况继续 ⊤。
        if cond.value == "&&":
            side = ba if ba is not None else bb
            if side is not None:
                return side, "&& 组合（另一侧推不出，取可推的一侧作上界）"
        return None, f"{ra}；{rb}"

    # ---- 谓词调用 → repeats（**含否定形态**，2026-11-13）
    #
    # `while (!p(x))` / `while (p(x) == false)` / `while (p(x) != true)` 与
    # `while (p(x))` 读**同一条** repeats：`repeats` 声明的就是「这个谓词当
    # 守卫的循环最多转几圈」，与守卫写不写 `!` 无关。以前只认裸 `call`，另外
    # 三种写法直接掉到「条件形态 unary / binary 不可推」⇒ κ = ⊤——同一个谓词
    # 三种等价写法只有一个能算，正是本项目最不想要的失真。
    #
    # 判据只有一份（`predicate_call_of`）：`&&` / `||` 在上面单独组合，
    # 比较式不收；不是谓词形状就继续往下走（仍可能靠计数器比较推出界）。
    _pc = predicate_call_of(cond)
    if _pc is not None:
        label = _pc.name.split(".")[-1]
        rep = repeats_of(label) if repeats_of else None
        if rep is not None:
            # **只在这里打「圈数名」标记**：这是 repeats 的读取处。同一份
            # `Bound` 也可能被 `expr_to_bound` 直接构造（target / 尺寸比较），
            # 那些地方的裸名字仍是尺寸量，不能改渲染。
            return _mark_count(rep), f"谓词 {label} 的 repeats"
        return None, f"谓词 {label} 没有 repeats"

    # ---- 比较
    if cond.kind == "binary" and (cond.value or "") in ("<", "<=", ">", ">="):
        op = cond.value
        a, b = (cond.children + [None, None])[:2]
        lb = expr_to_bound(b)
        la = expr_to_bound(a)

        # ---- 分块 / 窗口扫描：`i + c < e` / `n >= i + c`（2026-11-10）
        #
        # 放在这里而不是放宽 `expr_to_bound`：那是界表达式（尺寸 / target /
        # repeats）的公共入口，让 `+` 变界会顺手把 `while (a * b < 10)` 认成
        # 计数器比较。这里只认「比较的一侧恰是 `计数器 + 非负字面量`，另一侧
        # 仍必须是老规则能推出的界」；`*` / `/` / `-` 仍然拒。
        if ((_counter_plus_literal(b) is not None
             or _counter_plus_expr(b) is not None)
                and _counter_plus_literal(a) is None
                and _counter_plus_expr(a) is None
                and op in (">", ">=")):
            # `e > i + c` ≡ `i + c < e`：翻成「计数器在低侧」的同一形状再判
            a, b = b, a
            la, lb = lb, la
            op = "<" if op == ">" else "<="
        _blk = _counter_plus_literal(a)
        if (_blk is not None
                and _counter_plus_literal(b) is None
                and op in ("<", "<=")):
            return _block_scan_bound(_blk[0], _blk[1], op, lb, zero_vars, step_of)
        # ---- 偏移是**尺寸表达式**：`i + needle.size() <= hay.size()`（2026-11-12）
        # 字面量那一支能收紧成 `ceil(e / c)`；这一支只能给宽松上界（≤ N / N+1），
        # 因为偏移量 `C` 的**具体值**（以及 `C ≥ 1`）都证不出。
        _sym = _counter_plus_expr(a)
        if (_sym is not None
                and _counter_plus_literal(b) is None
                and _counter_plus_expr(b) is None
                and op in ("<", "<=")):
            off = expr_to_bound(_sym[1])
            return _block_scan_bound_sym(_sym[0], off, op, lb, nonneg_vars,
                                         fn, wh)

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
        #
        # **`>=` 要多一圈**（2026-11-10，与升序 `<=` 同一处病）：`j >= 0` 从
        # `v0` 递减到 0 是 `v0 + 1` 圈（还要算 j = 0 那一圈），取 `v0` 就少了
        # 整整一圈 —— 实测 `i64 i = n; while (i >= 0) { …; i = i - 1; }` 在
        # n = 0 时 κ = 20、ops = 21。`>` 那一侧（`j > 0` ⇒ 恰 `v0` 圈）不变。
        if op in (">", ">=") and la is not None and la.kind == "var":
            if la.value in dec_vars:
                init = init_of.get(la.value) if init_of else None
                if init is not None:
                    inner = expr_to_bound(init)
                    if inner is not None:
                        got = _plus_one(inner) if op == ">=" else inner
                        return got, (f"递减循环：{la.value} 从初值单调递减"
                                     f"（`{op} 0` ⇒ 最多初值"
                                     f"{' + 1' if op == '>=' else ''} 圈）")

        return _sub_or_plain(la, lb, op == ">=", zr), f"{op} 比较"

    return None, f"条件形态 {cond.kind} 不可推"


def _plus_one(b: Bound) -> Bound:
    """`b + 1`：`<=` / `>=` 比 `<` / `>` **多一圈**（圈数是 `hi - lo + 1`）。

    构造的是 `add` 节点（不是 `sub(b, -1)`）：`-1` 不是界形状，且 `add` 在
    `_bound_to_cost_scoped` / `bound_to_cost_inst` / `substitute` / `_mark_count`
    四处都有分支，`Cost.add` 会把它化进常数项。
    """
    return Bound("add", left=b, right=Bound("const", value=1))


def _sub_or_plain(high: Bound, low: Optional[Bound], incl: bool,
                  init_zero: bool = False) -> Bound:
    """`i < e` → `e - i`；若已知 `i` 的初值为 0，直接取 `e`。

    **`incl`（`<=` / `>=`）必须 +1**（2026-11-10 修）：`i <= e` 的圈数是
    `e - i + 1`，比 `i < e` **多整整一圈**。以前这里把 `incl` 收下却不 +1
    （注释写的是"上界取松不失真"），实际后果相反——`i <= n` 的圈数记成 `n`，
    κ 的常数项只预留了「一次守卫 + 一次推进」，于是 **n = 0 时 κ < ops**
    （实测 `for (u64 i = 0; i <= n; i = i + 1)`：κ = 20、ops = 22）。
    `<=` 多出来的那一圈不只是多一次推进：守卫被求值的次数是 `L + 1`，
    而 `L = (n + 1)`，所以 `(n + 1)·每圈` 正是这里必须给的那一项。

    `<` / `>`（`incl = False`）**逐字不变**——它们本来就是对的一侧。
    """
    if init_zero:
        return _plus_one(high) if incl else high
    if low is not None and low.kind in ("const", "var"):
        b = Bound("sub", left=high, right=low)
        return _plus_one(b) if incl else b
    # `high - low <= high`：`<` 这一侧取 `high` 已经是不失真的上界；
    # `<=` 再 +1（`high + 1 >= high - low + 1`）。
    return _plus_one(high) if incl else high


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
    if b.kind == "add":
        return Bound("add", left=substitute(b.left, table),
                     right=substitute(b.right, table))
    if b.kind in ("min", "max"):
        return Bound(b.kind, left=substitute(b.left, table),
                     right=substitute(b.right, table))
    if b.kind == "log2":
        return Bound("log2", left=substitute(b.left, table))
    if b.kind == "ceildiv":
        # **分母不用换**（它是字面量）。分子必须换：`while (i + 3 <= n)` 的 `n`
        # 是 `const u64 n = xs.length();` 时，界要变成 `ceil(xs.length() / 3)`。
        return Bound("ceildiv", value=b.value, left=substitute(b.left, table))
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
    # `&&` / `||`：任一分支能给出推进量就够（上界本来取 min/max）
    if cond.kind == "binary" and (cond.value or "") in ("&&", "||"):
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
    right = cond.children[1] if len(cond.children) > 1 else None
    op = cond.value or ""
    # `while (i + 3 <= n)` / `while (n > i + 3)`（2026-11-10）：分块扫描里
    # 推进量就是那个计数器。**只认 `+ 非负字面量`**：`while (a * b < 10)` 的
    # 推进量仍然找不到（返回 None ⇒ 不检查），与以前一致。
    #
    # 这一条不只是"顺手"：E21 是「循环不推进 ⇒ 不给上界」的唯一护栏，
    # 认了形状却不在 `_counter_of` 里认推进量，`while (i + 3 <= n) { s = s + 1; }`
    # 这种死循环就会拿到 `O(n)` —— 声称跑不完的循环会跑完，是最糟的失真。
    if op in ("<", "<="):
        sh = _counter_plus_literal(left) or _counter_plus_expr(left)
        if sh is not None:
            return ("var", sh[0])
    elif ((_counter_plus_literal(right) is not None
           or _counter_plus_expr(right) is not None)
          and _counter_plus_literal(left) is None
          and _counter_plus_expr(left) is None):
        sh = _counter_plus_literal(right) or _counter_plus_expr(right)
        return ("var", sh[0])
    if left.kind == "path" and left.name:
        return ("var", left.name)
    # `while (q.length() > 0)`：推进量是容器 q 本身
    if left.kind == "method-call" and (left.name or "").split(".")[-1] in ("length", "size"):
        recv = left.children[0] if left.children else None
        if recv is not None and recv.kind == "path" and recv.name:
            return ("container", recv.name)
    return None


def _touched_names(body: Node) -> set:
    """体内所有"可能被改变"的名字：赋值左侧、方法调用接收者、作为实参传入的名字。

    **2026-11-10**：复合赋值（`i += 1` / `i -= 2`）也改了这个名字。以前只扫
    `assign`，于是 `while (i < n) { …; i += 1; }` 被判成「计数器在循环体里
    从未被修改」——**写的和说的正好相反**。`+=` / `-=` 与 `i = i + k` 是
    同一个推进动作的两种写法，这里一视同仁（放开的是"能不能推进"这一关，
    不是上界本身：见 `progresses` / `bound_of_condition`）。
    """
    got = set()
    for kink in ("assign", "assign-op"):
        for a in _descendants(body, kink):
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


def _guard_op_of(cond: Optional[Node], name: str) -> Optional[str]:
    """守卫里 `name op …` 的比较方向（`<` / `<=` / `>` / `>=`）；找不到返回 None。

    `&&` / `||` 取第一个能给出方向的分支——与 `_counter_of` 同口径。
    """
    if cond is None:
        return None
    if cond.kind == "binary" and (cond.value or "") in ("&&", "||"):
        for c in cond.children:
            got = _guard_op_of(c, name)
            if got is not None:
                return got
        return None
    if cond.kind != "binary" or (cond.value or "") not in ("<", "<=", ">", ">="):
        return None
    left = cond.children[0] if cond.children else None
    right = cond.children[1] if len(cond.children) > 1 else None
    if left is not None and left.kind == "path" and left.name == name:
        return cond.value
    # `i + c < e` / `e > i + c`（2026-11-10）：形状里的计数器与裸计数器**同向**
    # ——`i + c <= e` 要 i 增大，`e >= i + c` 也要 i 增大。方向判错就会把
    # `while (i + 3 <= n) { i -= 1; }` 这种死循环放过去。
    #
    # **翻转写法要翻回来**：`e > i + c` 等价于 `i + c < e`，推进方向是「i 变大」。
    # 直接返回 `>` 会让 `_compound_never_advances` 以为要 i 变小，从而把
    # `while (n > i + 3) { i += 3; }` 这种**合法**写法判成死循环。
    if (cond.value or "") in ("<", "<="):
        sh = _counter_plus_literal(left) or _counter_plus_expr(left)
        if sh is not None and sh[0] == name:
            return cond.value
    else:
        sh = _counter_plus_literal(right) or _counter_plus_expr(right)
        if sh is not None and sh[0] == name:
            return "<" if cond.value == ">" else "<="
    return None


def _compound_never_advances(cond: Optional[Node], body: Node, name: str) -> bool:
    """复合赋值**不可能**把计数器推向守卫要求的方向时返回 True。

    为什么需要这一层（2026-11-10）：认了 `+=` / `-=` 之后，
    `while (i < n) { i -= 1; }` 也会"碰过 i"——可它是死循环。**上界必须 ≥ 真实
    成本，而死循环的成本是 ∞**，放它过去就等于声称一个跑不完的循环会跑完。
    这里只判**方向能确定**的情形：

      · `while (i < n) { i -= 1; }`：守卫要 i 增大，复合赋值却让它减小；
      · `while (i > 0) { i += 1; }`：反过来；
      · `i += 0` / `i -= 0`：原地不动。

    判据是**必要不充分**的，且**判不出就不拦**：只要体内还有别的对 `name` 的
    修改（普通赋值、非字面量步长、`*=` / `/=`），就返回 False（沿用历史行为，
    那条路本来就不做方向分析）。`i = i + 1` 这类老写法**不受影响**。
    """
    guard = _guard_op_of(cond, name)
    if guard is None:
        return False
    # 有普通赋值 ⇒ 方向交给历史规则（不在本次改动范围内收紧）。
    for a in _descendants(body, "assign"):
        lhs = a.children[0] if a.children else None
        if lhs is not None and lhs.kind == "path" and lhs.name == name:
            return False
    moves = []
    for a in _descendants(body, "assign-op"):
        lhs = a.children[0] if a.children else None
        if lhs is None or lhs.kind != "path" or lhs.name != name:
            continue
        rhs = a.children[1] if len(a.children) > 1 else None
        if rhs is None or getattr(rhs, "kind", "") != "lit-int":
            return False                    # 步长不是字面量：方向不可判，放行
        moves.append((a.value, int(rhs.value)))
    if not moves:
        return False
    for op, step in moves:
        if step == 0:
            continue
        if op == "+=":
            incr = step > 0
        elif op == "-=":
            incr = step < 0                 # 字面量步长恒 > 0，这里恒为 False
        else:
            return False                    # `*=` / `/=`：方向不可判
        if guard in ("<", "<=") and incr:
            return False                    # 至少有一处往对的方向推
        if guard in (">", ">=") and not incr:
            return False
    return True


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
        if kind == "var" and _compound_never_advances(cond, body, name):
            return False, (f"计数器 {name} 的复合赋值推不进守卫要的方向"
                           f"（`{name} < …` 配 `{name} -= 1`、或 `{name} += 0`）")
        return True, f"体内修改了 {name}"
    # **文案别说反话**（2026-11-10）：这里只证明了"没找到对它的修改"，
    # 不能断言"你从来没改过"——`+=` 那条曾经就是这么把作者顶回去的。
    if kind == "var":
        return False, (f"计数器 {name} 在循环体里没有被推进：体内没有语句会改变它"
                       f"（认得的推进写法：`{name} = {name} + 1`、`{name} += 1`、"
                       f"`{name} -= 1`、`++{name}`）")
    return False, (f"容器 {name} 在循环体里没有被推进：体内没有调用会改变它"
                   f"（如 `{name}.pop()` / `{name}.push(…)`）")



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
        # **`<=` 同样多一圈**（2026-11-10，与 `_sub_or_plain` 同一处病）：
        # `u64 i = 1; while (i <= n) { i = i * 2; }` 的圈数是
        # `floor(log2 n) + 1`，不是 `log2 n` —— n = 1 时实测 κ = 13、ops = 15。
        # `log2(n) + 1 >= floor(log2 n) + 1`，`<` 那一侧**逐字不变**。
        if op == "<=" and b is not None:
            b = _plus_one(b)
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


# ---------------------------------------------------------------- 位宽有界循环（2026-11-12）
#
# ```cpp
# while (x != 0) { x = x & (x - 1); }   // Kernighan popcount
# while (x != 0) { x = x >> 1; }        // 每轮右移一位
# while (x != 0) { x = x / 2; }         // 无符号 ÷2（同上）
# ```
#
# 这三种形状的圈数**与数据无关地** ≤ `x` 的类型位宽：
#
# | 更新 | 每轮严格减少 | 圈数 |
# |---|---|---|
# | `x & (x - 1)` | popcount 减 **1**（清掉最低位的 1） | `popcount(x₀) ≤ W` |
# | `x >> k`（k ≥ 1） | 位长至少减 **1** | `bitlen(x₀) ≤ W` |
# | `x / k`（k ≥ 2，无符号） | 位长至少减 **1**（`x/k ≤ x/2`） | `bitlen(x₀) ≤ W` |
#
# 所以上界取**类型位宽**（`u8`→8 / `u16`→16 / `u32`→32 / `u64`→64），是一个
# 常数——与 `n(xs)` 之类的尺寸量无关，判定里直接折成 `Cost.num(位宽)`。
#
# **为什么只认无符号**：`i64 x; x >> 1` 在 C++ 里是实现定义的（算术右移），
# 负数右移永远到不了 0 ⇒ 圈数不是位宽；`i64 x; x / 2` 向零截断，`-1 / 2 == 0`
# 也不能用「位长递减」论证（补码负数的位长没有意义）。判据是**类型的符号性**，
# 不是拼写——推不出就 ⊤。
#
# **为什么必须恰好一条写法**：`while (x != 0) { x = x & (x - 1); x = x - 1; }`
# 里第二条更新会把「每轮至少少一位」打断（`x-x` 可能不减）。只要体内还有
# 别的写 `x`（含 `&x` 传出、`x.f()` 方法接收者、`++x`），一律 ⊤。
#
# **break / continue 一律 ⊤**：`continue` 可以跳过那唯一一条更新（死循环），
# `break` 让「每轮都减少」这个前提不再需要、但判据也就不成立了。宁可不给界。
#
# 条件只认 `x != 0`（以及等价的 `0 != x`）与 `x > 0`（**无符号**下它与
# `x != 0` 等价）。`x != c` / `x > c`（c ≠ 0）、`x < c` 都不收——它们不是
# 「减到 0 为止」，圈数与位宽无关。

#: 无符号整数类型 → 位宽。**没有 `usize` / `size_t`**：它们在 `--compat`
#: 下已经折成 `u64`，这里多认一个名字就等于多一条未经验证的假设。
_WIDTH_OF: dict = {"u8": 8, "u16": 16, "u32": 32, "u64": 64}


def _plain(n) -> Node:
    """剥掉括号（`(x - 1)` 在 AST 里是 `paren` 节点）。"""
    while getattr(n, "kind", "") == "paren" and getattr(n, "children", None):
        n = n.children[0]
    return n


def _is_name(n, name: str) -> bool:
    n = _plain(n)
    return getattr(n, "kind", "") == "path" and getattr(n, "name", "") == name


def _as_name(n) -> Optional[str]:
    """剥括号后是**裸变量**就返回名字；否则 None。"""
    n = _plain(n)
    if getattr(n, "kind", "") == "path" and getattr(n, "name", ""):
        return n.name
    return None


def _lit_int_of(n):
    n = _plain(n)
    if getattr(n, "kind", "") != "lit-int":
        return None
    try:
        return int(n.value)
    except (TypeError, ValueError):
        return None


def _is_var_minus_one(n, name: str) -> bool:
    """`name - 1`（剥括号后）。"""
    n = _plain(n)
    if getattr(n, "kind", "") != "binary" or (n.value or "") != "-":
        return False
    a, b = (n.children + [None, None])[:2]
    return _is_name(a, name) and _lit_int_of(b) == 1


def _type_base_name(n) -> Optional[str]:
    """声明 / 形参的类型节点 → 基础类型名（`type-ref` 剥一层）。"""
    n = _plain(n)
    if getattr(n, "kind", "") == "type-ref" and getattr(n, "children", None):
        n = n.children[0]
    if getattr(n, "kind", "") == "type-name":
        return getattr(n, "name", "") or None
    return None


def unsigned_width_of(fn: Optional[Node], name: str):
    """`name` 的**无符号位宽**；证明不了 → `(None, 原因)`。

    只认两种来源：函数的**形参**、函数里的 `var-decl` / `const-decl`。
    **同名多处声明**（不同作用域遮蔽）一律拒——内层 `u64 x` 会遮住外层
    `i64 x`，挑一处当类型就是在猜。
    """
    if fn is None:
        return None, f"没有函数体，查不到 `{name}` 的声明类型"
    seen = []
    for params in _descendants(fn, "params"):
        for pm in (getattr(params, "children", None) or []):
            if getattr(pm, "kind", "") != "param" or getattr(pm, "name", "") != name:
                continue
            kids = getattr(pm, "children", None) or []
            seen.append(_type_base_name(kids[0] if kids else None))
    for kind in ("var-decl", "const-decl"):
        for d in _descendants(fn, kind):
            if getattr(d, "name", "") != name:
                continue
            kids = getattr(d, "children", None) or []
            seen.append(_type_base_name(kids[0] if kids else None))
    if not seen:
        return None, f"找不到 `{name}` 的声明（只认形参与本函数的 var/const 声明）"
    if len(seen) != 1:
        return None, (f"`{name}` 有 {len(seen)} 处声明（不同作用域遮蔽），"
                      f"挑哪一处都是猜 ⇒ 不给位宽上界")
    ty = seen[0]
    if ty in ("i8", "i16", "i32", "i64"):
        return None, (f"`{name}` 是有符号整数 `{ty}`：右移在 C++ 里是实现定义的、"
                      f"除法的收敛性也不等价 ⇒ 不给位宽上界")
    if ty not in _WIDTH_OF:
        shown = "认不出" if ty is None else f"`{ty}`"
        return None, f"`{name}` 的类型是 {shown}，不是无符号整数 u8/u16/u32/u64"
    return _WIDTH_OF[ty], ty


def _width_step(rhs, name: str) -> Optional[str]:
    """一条更新是不是「每轮至少减少一位」；是 → 返回形状名，否则 None。"""
    rhs = _plain(rhs)
    if getattr(rhs, "kind", "") != "binary":
        return None
    op = rhs.value or ""
    a, b = (rhs.children + [None, None])[:2]
    if op == "&":
        # `x & (x - 1)` / `(x - 1) & x`：交集可交换，两种顺序都收。
        if ((_is_name(a, name) and _is_var_minus_one(b, name))
                or (_is_name(b, name) and _is_var_minus_one(a, name))):
            return "clear-lowest-bit"
        return None
    if op == ">>":
        k = _lit_int_of(b)
        if _is_name(a, name) and k is not None and k >= 1:
            return f"shift-{k}"
        return None
    if op == "/":
        k = _lit_int_of(b)
        if _is_name(a, name) and k is not None and k >= 2:
            return f"div-{k}"
        return None
    return None


def _writes_to(body: Node, name: str) -> list:
    """循环体里**所有**可能改变 `name` 的写法（不只赋值）。"""
    out = []
    for kind in ("assign", "assign-op"):
        for a in _descendants(body, kind):
            kids = getattr(a, "children", None) or []
            if kids and _is_name(kids[0], name):
                out.append(a)
    # `f(&x)`：按引用传出去，被调函数可能改它。
    for r in _descendants(body, "ref"):
        if any(_is_name(p, name) for p in _descendants(r, "path")):
            out.append(r)
    # `x.f()`：方法接收者可能就地改它（标量没有，但判据不靠这个假设）。
    for mc in _descendants(body, "method-call"):
        kids = getattr(mc, "children", None) or []
        if kids and _is_name(kids[0], name):
            out.append(mc)
    return out


def width_bounded_loop(wh: Node, fn: Node = None):
    """「每轮至少减少一位」的循环 → `(界, 原因, 是否命中条件形状)`。

    第三项把「条件根本不是这个形状」（走老路）与「条件对、但前提证不出」
    （必须给具体拒因再退 ⊤）分开——否则拒绝原因会退化成一句
    「条件形态 binary 不可推」，作者看不出是哪条前提没过。

    **命中拒因的两族不同**：
      · `x != 0`：`bound_of_condition` 里没有 `!=` 分支，**没有别的规则能兜**，
        所以证不出就必须 `(None, 原因, True)`（调用方直接 ⊤）；
      · `x > 0`：旧的「递减循环 / `x - c`」上界仍然健全（只是松），所以证不出
        时返回 `(None, "", False)`，**不许抢在它前面记 ⊤**——`while (j > 0) {
        j = j - 1; }` 就是靠那条活的。

    保守性：只有**全部**前提都证成才给界。
    """
    cond = wh.children[0] if (getattr(wh, "children", None) or []) else None
    if cond is None or getattr(cond, "kind", "") != "binary":
        return None, "", False
    op = cond.value or ""
    if op not in ("!=", ">"):
        return None, "", False
    left, right = (cond.children + [None, None])[:2]
    #: `!=` 家族没有别的兜底规则：拒因要透出去；`>` 家族有（见 docstring）。
    ne_family = (op == "!=")

    def _reject(reason):
        return (None, reason, True) if ne_family else (None, "", False)

    var = None
    if op == "!=":
        # `x != 0` / `0 != x`
        for a, b in ((left, right), (right, left)):
            nm = _as_name(a)
            if nm and _lit_int_of(b) == 0:
                var = nm
                break
        if var is None:
            # `x != c`（c 非 0）：给出**具体拒因**，别退回废诊断。
            for a, b in ((left, right), (right, left)):
                nm, c = _as_name(a), _lit_int_of(b)
                if nm and c is not None:
                    return None, (
                        f"`while ({nm} != {c})`：条件不是「减到 0 为止」"
                        f"（c ≠ 0 时圈数与类型位宽无关）⇒ 不给位宽上界"), True
            return None, "", False      # 不是 `!= 字面量` 家族：走老路
    else:
        # `x > 0`（**无符号**下与 `x != 0` 等价；符号性由下面的类型判据保证）。
        # `0 < x` 是另一种拼写，本轮不收（走老路）；`x > c`（c ≠ 0）也不收。
        nm = _as_name(left)
        if nm and _lit_int_of(right) == 0:
            var = nm
        else:
            return None, "", False

    body = next((c for c in (getattr(wh, "children", None) or [])
                 if getattr(c, "kind", "") == "block"), None)
    if body is None:
        return _reject(f"`while ({var} {op} 0)` 的循环体为空")

    # **break / continue 一律拒**：`continue` 可以跳过那唯一一条更新。
    for kind in ("break", "continue"):
        if _descendants(body, kind):
            return _reject(f"循环体里有 `{kind}`：「每轮都减少一位」不再成立"
                           f"（`continue` 甚至会跳过更新变成死循环）⇒ 不给位宽上界")

    width, tyinfo = unsigned_width_of(fn, var)
    if width is None:
        return _reject(f"`while ({var} {op} 0)`：{tyinfo}")

    writes = _writes_to(body, var)
    if len(writes) != 1:
        return _reject(f"循环体里有 {len(writes)} 处写 `{var}`（要恰好一处、"
                       f"且是循环体的直接子语句）⇒ 不给位宽上界")
    step = writes[0]
    if getattr(step, "kind", "") != "assign":
        return _reject(f"循环体里对 `{var}` 的那处写不是普通赋值"
                       f"（复合赋值 / 取引用传出 / 方法接收者）⇒ 不给位宽上界")
    direct = [c for c in (getattr(body, "children", None) or [])]
    if step not in direct:
        return _reject(f"`{var}` 的更新不在循环体的直接子语句里（写在 if / 内层循环里"
                       f"可能不执行）⇒ 不给位宽上界")
    kids = getattr(step, "children", None) or []
    shape = _width_step(kids[1] if len(kids) > 1 else None, var)
    if shape is None:
        return _reject(f"`{var}` 的更新不是「每轮至少少一位」的形状"
                       f"（只认 `{var} & ({var} - 1)`、`{var} >> k`、`{var} / k`）"
                       f"⇒ 不给位宽上界")

    why = (f"位宽有界：`while ({var} {op} 0)` 且体内 `{var} = …`（{shape}）每轮至少"
           f"减少一位，`{var}` 是 `{tyinfo}`（{width} 位）⇒ 轮数 ≤ {width}")
    return Bound("const", value=width), why, True


# ---------------------------------------------------------------- 区间减半循环（2026-11-13）
#
# ```cpp
# while (lo < hi) {                       // 或 for (step = 0; step <= x; ++step) {
#     mid = lo + (hi - lo) / 2;           //        if (lo >= hi) break;   ← 同一个守卫
#     if (…) lo = mid + 1; else hi = mid; //        mid = lo + (hi - lo) / 2; …
# }                                       //    }
# ```
#
# 与上面「位宽有界循环」**同族**：被考察的量每轮至少折半。这里被考察的量是
# **区间长度** `s = hi - lo`，`mid = lo + ⌊s/2⌋`：
#
# | 分支 | 新区间长度 | 与 s 的关系（`s ≥ 1`） |
# |---|---|---|
# | `hi = mid` | `mid - lo = ⌊s/2⌋` | `< s` 且 `≤ ⌈s/2⌉` |
# | `lo = mid + 1` | `hi - mid - 1 = ⌈s/2⌉ - 1` | `< s` 且 `≤ ⌈s/2⌉` |
#
# ⇒ `s_k ≤ ⌈s₀ / 2^k⌉`，循环在 `s < 1`（即 `lo >= hi`）时退出
# ⇒ **迭代 ≤ ⌈log2(s₀)⌉ + 1**。`s₀` 能定位成尺寸量（`hi = xs.size()`）就取
# `log2(n(xs)) + 1`；定位不到就取**类型位宽**：`s₀ ≤ 2^W - 1` ⇒ 迭代 ≤ `W + 1`
# （`u64` → 65）。取位宽而不是 `log2(s₀)+1`（`s₀` 是形参时）是有意的：形参的
# 表达式会被内联到调用点（`isqrt(acc / xs.size())`），把 `acc` 重新带进调用方的 κ。
#
# **为什么 `lo = mid` / `hi = mid + 1` 必须拒**：`s = 1` 时 `mid = lo`，
# `lo = mid` 原地不动 ⇒ **真死循环**，`⌈log2 s⌉+1` 不再是上界（是 ∞ 的上界）。
#
# **为什么 `(lo + hi) / 2` 必须拒**：u64 下 `lo + hi` 会溢出（`lo + hi ≤ 2^64-1`
# 证不出来）。只认 `lo + (hi - lo) / 2` 与 `lo + ((hi - lo) >> 1)`——两种写法
# 算术上等价（`>>1` 就是 `/2`，被除数非负时）。
#
# **位宽查询复用 `unsigned_width_of`**（上一条规则那一份，2026-11-12）：只认
# u8/u16/u32/u64、有符号拒、同名多处声明拒——类型判据**只此一份**，不另造。
#
# **break / continue 只在"break 是守卫本身"时允许**：`if (lo >= hi) break;`
# 就是那个守卫（`for` 外壳里最常见的写法），它只会**减少**迭代；而 `continue`
# 会跳过 `mid` 与那两处写 ⇒ 区间不缩 ⇒ 拒（见 `halving_interval_loop`）。
#
# **这是新的界，不是替换**（设计文档 §5）：
#
# ```
# 旧界（`x + 1` / `n(hi)`）仍然 sound，只是松；
# min(旧界, 新界) 里的"min"只有在**证得出**新界 ≤ 旧界时才生效：
#   · 旧界不可判定（含裸标量 `x` / `hi`，渲染成 `n(x)`、求值侧查不到按 ∞）
#     ⇒ min(∞, 新界) = 新界（`_judgeable_bound`）；
#   · 旧界是尺寸量界 ⇒ 保持旧界——绝不比改前松（`cost_baseline` 的判据）。
# ```

_LOOP_KINDS = ("while", "for", "do-while")

#: `n(x)` 只在**容器在函数里不动**时才是全程上界。这些方法按只读处理
#: （`size()` / `at()` …），其余方法（`push_back` / `pop` / `clear` …）一律
#: 当成"可能改长度" ⇒ 退回类型位宽。
_READONLY_METHODS = SIZE_FNS + ("at", "get", "front", "back", "count",
                                "find", "contains")


def _loop_body(wh: Node):
    """循环体（`block`）；没有 → None。"""
    if wh is None:
        return None
    return next((c for c in (getattr(wh, "children", None) or [])
                 if getattr(c, "kind", "") == "block"), None)


def _pure_interval_guard(cond: Node):
    """`lo < hi` / `lo != hi`（含 `hi > lo` / `hi != lo`）→ `(lo, hi, 描述, strict)`。

    **只认裸名字对裸名字**：`while (i < xs.size())` 不是这个形状（那是计数器
    比较，走 `bound_of_condition`），`while (lo < hi && k > 0)` 也不是
    （复合条件不在本规则里，见 §4⑥）。

    `strict` 区分 `<` 家族与 `!=` 家族：`lo < hi` 本身就能证明"循环进去时
    `lo ≤ hi`"，`lo != hi` **不能**——若初值 `lo > hi`，`hi - lo` 在无符号下
    回绕、折半论证不成立。所以 `!=` 家族额外要求 `lo` 初值是字面量 0
    （见 `halving_interval_loop`）。
    """
    if getattr(cond, "kind", "") != "binary":
        return None
    op = cond.value or ""
    if op not in ("<", "!=", ">"):
        return None
    a, b = (list(getattr(cond, "children", None) or []) + [None, None])[:2]
    na, nb = _as_name(a), _as_name(b)
    if not na or not nb or na == nb:
        return None
    if op == ">":                       # `hi > lo` ≡ `lo < hi`
        na, nb = nb, na
    return na, nb, f"`{na} {op} {nb}`", op != "!="


def _break_interval_guard(body: Node):
    """体内首层的 `if (<lo >= hi>) break;` / `if (<lo == hi>) break;`。

    这是 `for` 外壳写法里**真正**的守卫（`for (step = 0; step <= x; ++step)
    { if (lo >= hi) break; … }`）。按 §2 的论证，`break` 只会**减少**迭代：
    区间长度 `s_k < 1` 时守卫成立、循环在那一轮就退出，所以
    `⌈log2(s₀)⌉+1` 仍然是上界。

    返回 `(lo, hi, 描述, strict)`；`==` 那一支与 `!=` 同理，`strict=False`。
    """
    for st in (getattr(body, "children", None) or []):
        if getattr(st, "kind", "") != "if":
            continue
        kids = list(getattr(st, "children", None) or [])
        if len(kids) != 2:              # 带 `else` ⇒ 不是"到点就退出"的守卫
            continue
        then = list(getattr(kids[1], "children", None) or [])
        if len(then) != 1 or getattr(then[0], "kind", "") != "break":
            continue
        g = kids[0]
        if getattr(g, "kind", "") != "binary":
            continue
        op = g.value or ""
        if op not in (">=", "<=", "=="):
            continue
        a, b = (list(getattr(g, "children", None) or []) + [None, None])[:2]
        na, nb = _as_name(a), _as_name(b)
        if not na or not nb or na == nb:
            continue
        if op == "<=":                  # `hi <= lo` ≡ `lo >= hi`
            na, nb = nb, na
        return na, nb, f"体内 `if ({na} {op} {nb}) break;`", op != "=="
    return None


def _mid_shape(rhs: Node, lo: str, hi: str):
    """`mid = …` 的右值 → `(接受的形状名, 拒因)`；两者都是 None = 不是中点形状。

    **只认两种**（§3②）：

        lo + (hi - lo) / 2
        lo + ((hi - lo) >> 1)

    `+` 可交换（`(hi - lo) / 2 + lo` 是同一个和），两种顺序都收。
    """
    r = _plain(rhs)
    if getattr(r, "kind", "") != "binary" or (r.value or "") != "+":
        return None, None
    x, y = (list(getattr(r, "children", None) or []) + [None, None])[:2]
    if not _is_name(x, lo) and _is_name(y, lo):
        x, y = y, x
    if not _is_name(x, lo):
        return None, None
    h = _plain(y)
    if getattr(h, "kind", "") != "binary" or (h.value or "") not in ("/", ">>"):
        return None, None
    num, den = (list(getattr(h, "children", None) or []) + [None, None])[:2]
    np_ = _plain(num)
    if getattr(np_, "kind", "") != "binary" or (np_.value or "") != "-":
        return None, None
    na, nb = (list(getattr(np_, "children", None) or []) + [None, None])[:2]
    if not (_is_name(na, hi) and _is_name(nb, lo)):
        return None, None
    k = _lit_int_of(den)
    if k is None:
        return None, (f"`{lo} + ({hi} - {lo}) / k` 里的 k 不是整数字面量 ⇒ "
                      f"折半的步长证不出")
    if (h.value or "") == "/" and k == 2:
        return f"{lo} + ({hi} - {lo}) / 2", None
    if (h.value or "") == ">>" and k == 1:
        return f"{lo} + (({hi} - {lo}) >> 1)", None
    return None, (f"`{lo} + ({hi} - {lo}) {h.value} {k}` 的步长不是折半"
                  f"（只认 `/ 2` 与 `>> 1`）⇒ 区间不保证每轮减半")


def _looks_like_midpoint(rhs: Node, lo: str, hi: str) -> bool:
    """这个右值**像不像**中点（同时含 `lo` 与 `hi`，且有 `/字面量` 或 `>>字面量`）。

    只用来把「形状对、但写法被拒」（`(lo + hi) / 2`）与「根本不是二分」分开：
    前者要给**具体拒因**（§4④），后者必须原样不动（返回 `hit=False`）。
    """
    names = {getattr(p, "name", "") for p in _descendants(rhs, "path")}
    if lo not in names or hi not in names:
        return False
    for n in _descendants(rhs, "binary"):
        if (getattr(n, "value", "") or "") in ("/", ">>"):
            kids = list(getattr(n, "children", None) or [])
            if len(kids) > 1 and _lit_int_of(kids[1]) is not None:
                return True
    return False


def _stmt_rhs(st: Node):
    """一条语句的**右值**节点；不是赋值/声明 → None。"""
    k = getattr(st, "kind", "")
    kids = list(getattr(st, "children", None) or [])
    if k in ("var-decl", "const-decl"):
        return kids[-1] if len(kids) >= 2 else None
    if k in ("assign", "assign-op"):
        return kids[1] if len(kids) > 1 else None
    return None


def _stmt_lhs_name(st: Node):
    """一条语句**左侧**的裸名字；左侧不是裸名字（`xs[i] = …` / `p.n_ = …`）→ None。"""
    k = getattr(st, "kind", "")
    if k in ("var-decl", "const-decl"):
        return getattr(st, "name", None) or None
    if k in ("assign", "assign-op"):
        lhs = (list(getattr(st, "children", None) or []) or [None])[0]
        return _as_name(lhs) if getattr(lhs, "kind", "") == "path" else None
    return None


def _writes_detail(root: Node, names):
    """`root` 子树里对 `names` 的**所有**写法 → `[(节点, 名字, 是否在内层循环里)]`。

    三种写法都算（与 `_writes_to` 同口径）：赋值 / 复合赋值、`&名字`（传出后
    被调方可能改它）、`名字.方法()`（方法接收者可能就地改它）。
    """
    out = []

    def walk(n, in_loop):
        k = getattr(n, "kind", "")
        nl = in_loop or (k in _LOOP_KINDS)
        if k in ("assign", "assign-op"):
            kids = list(getattr(n, "children", None) or [])
            lhs = kids[0] if kids else None
            if getattr(lhs, "kind", "") == "path" and getattr(lhs, "name", "") in names:
                out.append((n, lhs.name, nl))
        elif k in ("ref", "method-call"):
            kids = list(getattr(n, "children", None) or [])
            tgt = kids[0] if kids else None
            if getattr(tgt, "kind", "") == "path" and getattr(tgt, "name", "") in names:
                out.append((n, tgt.name, nl))
        for c in (getattr(n, "children", None) or []):
            walk(c, nl)

    walk(root, False)
    return out


def _mid_plus_one(rhs: Node, mid: str) -> bool:
    """`mid + 1`（剥括号后）；`mid + 2` / `mid + mid` / `1 + 1` 都不是。"""
    r = _plain(rhs)
    if getattr(r, "kind", "") != "binary" or (r.value or "") != "+":
        return False
    a, b = (list(getattr(r, "children", None) or []) + [None, None])[:2]
    if _is_name(a, mid) and _lit_int_of(b) == 1:
        return True
    return _is_name(b, mid) and _lit_int_of(a) == 1


def _is_ref_decl(fn: Optional[Node], name: str) -> bool:
    """`name` 的声明类型是不是**引用**（`u64&`）——按值语义在这里是硬前提。"""
    if fn is None:
        return False
    for params in _descendants(fn, "params"):
        for pm in (getattr(params, "children", None) or []):
            if getattr(pm, "name", "") != name:
                continue
            kids = list(getattr(pm, "children", None) or [])
            if kids and getattr(kids[0], "kind", "") == "type-ref":
                return True
    for kind in ("var-decl", "const-decl"):
        for d in _descendants(fn, kind):
            if getattr(d, "name", "") != name:
                continue
            kids = list(getattr(d, "children", None) or [])
            if kids and getattr(kids[0], "kind", "") == "type-ref":
                return True
    return False


def _decl_init_of(fn: Optional[Node], name: str):
    """`name` 在本帧**唯一一处** `var-decl`/`const-decl` 的初值节点；否则 None。"""
    if fn is None:
        return None
    inits = []
    for kind in ("var-decl", "const-decl"):
        for d in _descendants(fn, kind):
            if getattr(d, "name", "") != name:
                continue
            kids = list(getattr(d, "children", None) or [])
            inits.append(kids[-1] if len(kids) >= 2 else None)
    return inits[0] if len(inits) == 1 else None


def _stable_size_quantity(fn: Optional[Node], name: str):
    """`name` 是**本帧 const 形参**、且函数里没被改/没被传出去 → `Bound("size")`。

    为什么需要：`hi = xs.size()` 给出 `s₀ ≤ n(xs)`，但这只在 `n(xs)` 是**全程**
    上界时成立（函数执行期间长度不变）。`push_back` / `pop` / `clear` / `&xs`
    都会破坏它——那时 `n(xs)` 是**最终**长度，用它当 `s₀` 可能偏小（破界）。
    证不出就退回类型位宽（松但不失真）。
    """
    if fn is None or not name:
        return None
    is_const_param = False
    for params in _descendants(fn, "params"):
        for pm in (getattr(params, "children", None) or []):
            if getattr(pm, "kind", "") != "param" or getattr(pm, "name", "") != name:
                continue
            v = getattr(pm, "value", None) or {}
            is_const_param = is_const_param or bool(v.get("const"))
    if not is_const_param:
        return None
    for kind in ("assign", "assign-op"):
        for a in _descendants(fn, kind):
            kids = list(getattr(a, "children", None) or [])
            lhs = kids[0] if kids else None
            if getattr(lhs, "kind", "") == "path" and getattr(lhs, "name", "") == name:
                return None
    for mc in _descendants(fn, "method-call"):
        kids = list(getattr(mc, "children", None) or [])
        recv = kids[0] if kids else None
        if getattr(recv, "kind", "") != "path" or getattr(recv, "name", "") != name:
            continue
        if (getattr(mc, "name", "") or "").split(".")[-1] not in _READONLY_METHODS:
            return None
    for r in _descendants(fn, "ref"):
        for p in _descendants(r, "path"):
            if getattr(p, "name", "") == name:
                return None
    return Bound("size", value=name)


def halving_interval_loop(wh: Node, fn: Node = None):
    """「区间减半」的循环 → `(界, 原因, 是否命中)`。

    **判据（缺一即不给新界）**：

    ```
    ① 守卫是 `lo < hi` / `lo != hi`（或体内首层的 `if (lo >= hi) break;`）；
       `lo`/`hi` 必须是**按值**标量（`u64&` / `&lo` / 方法接收者 ⇒ 拒）
    ② `mid = lo + (hi - lo) / 2` 或 `lo + ((hi - lo) >> 1)`；
       `mid` 全函数只写一次、不取地址、不传非 const 引用
    ③ 体内对 `lo`/`hi` 的写恰好是 `lo = mid + 1`（一处或零处）与
       `hi = mid`（一处或零处）；第三种写 ⇒ 拒
    ④ 写不在内层循环里（写在 `if`/`else` 两支里**可以**，每轮恰执行一支）
    ⑤ 类型都是无符号整数（`i8/i16/i32/i64` ⇒ 拒）
    ```

    ⇒ 迭代 ≤ `⌈log2(s₀)⌉ + 1`；`s₀ = hi - lo` 的初值。`hi` 的初值是**稳定容器的
    长度**（`hi = xs.size()` 且 `xs` 是 const 形参）⇒ `log2(n(xs)) + 1`；否则取
    类型位宽 `W + 1`（`u64` → 65，与 `width_bounded_loop` 同一份 `unsigned_width_of`）。

    **§4 必须拒绝的七种形状**（各有测试，且都必须仍给**旧的线性界**）：

    | # | 形状 | 拒因 |
    |---|---|---|
    | ① | `lo = mid` | `s = 1` 时原地不动 = 死循环 |
    | ② | `hi = mid + 1` | 同上 |
    | ③ | `mid` 被第二次赋值 | 两次调用的 `mid` 不同，划分不成立 |
    | ④ | `mid = (lo + hi) / 2` | u64 下 `lo + hi` 会溢出 |
    | ⑤ | `lo`/`hi` 有符号 | 负数下 `s` 的语义不同 |
    | ⑥ | `while (lo < hi && k > 0)` | 复合条件不在本规则里 |
    | ⑦ | 写在内层循环里 | 每轮可能执行多次 |

    第三项 `hit` 把「这根本不是区间减半循环」（`hit=False`，原样交给旧规则、
    **一个字都不动**）与「形状对、前提证不出」（`hit=True`，拒因可读）分开。
    **本函数从不返回 ⊤**：拒绝只是"不给新界"，旧界照旧成立（§5）。
    """
    cond = wh.children[0] if (getattr(wh, "children", None) or []) else None
    body = _loop_body(wh)
    if body is None:
        return None, "", False

    g = _pure_interval_guard(cond)
    if g is None:
        g = _break_interval_guard(body)
    if g is None:
        return None, "", False           # 不是这个家族：旧规则原样处理
    lo, hi, guard_desc, strict = g

    # ---- ⓪ `continue` 一律拒：它会跳过 `mid` 与那两处写 ⇒ 区间不缩 ⇒ 可能死循环
    #         （`break` 相反：只会**减少**迭代，见 §2，`if (lo >= hi) break;` 就是守卫）。
    if _descendants(body, "continue"):
        return None, (f"区间减半：循环体里有 `continue`——它可以跳过 `{lo} = mid + 1` "
                      f"/ `{hi} = mid`，区间不缩就会死循环 ⇒ 拒绝"), True

    # ---- ⓪-b `!=` / `==` 家族：守卫本身**证明不了** `lo <= hi`。初值若 `lo > hi`，
    #         `hi - lo` 在无符号下回绕，折半论证不成立。要求 `lo` 初值是字面量 0。
    if not strict:
        _linit = _decl_init_of(fn, lo)
        if _lit_int_of(_linit) != 0:
            return None, (f"区间减半：{guard_desc} 是**对称守卫**（`!=` / `==`），"
                          f"它证明不了进循环时 `{lo} <= {hi}`；只有 `{lo}` 初值为"
                          f"字面量 0 时才能保证不减出回绕 ⇒ 拒绝"), True

    # ---- ② 先认中点形状。**认不出就完全不动**（这是"不是二分"的信号），
    #         认得出但写法被拒才给具体拒因（§4④）。
    mid_name = None
    mid_shape = None
    mid_bad = None
    for st in (getattr(body, "children", None) or []):
        if getattr(st, "kind", "") not in ("var-decl", "const-decl",
                                           "assign", "assign-op"):
            continue
        nm = _stmt_lhs_name(st)
        rhs = _stmt_rhs(st)
        if not nm or rhs is None:
            continue
        shape, bad = _mid_shape(rhs, lo, hi)
        if shape is not None:
            mid_name, mid_shape = nm, shape
            break
        if bad is not None and mid_bad is None:
            mid_bad = f"`{nm} = …`：{bad}"
        elif _looks_like_midpoint(rhs, lo, hi) and mid_bad is None:
            mid_bad = (f"`{nm} = …`：只认 `{lo} + ({hi} - {lo}) / 2` 与 "
                       f"`{lo} + (({hi} - {lo}) >> 1)`（`({lo} + {hi}) / 2` "
                       f"在 u64 下会溢出）")
    if mid_name is None:
        if mid_bad is not None:
            return None, f"区间减半：{guard_desc}，但 {mid_bad} ⇒ 拒绝", True
        return None, "", False

    # ---- ① 按值标量：引用类型 / 取地址 / 方法接收者一律拒
    for nm in (lo, hi, mid_name):
        if _is_ref_decl(fn, nm):
            return None, (f"区间减半：`{nm}` 声明成**引用**（`T&`），"
                          f"不是本帧的按值标量 ⇒ 拒绝"), True
    recv_write = {}
    for r in _descendants(fn, "ref"):
        for p in _descendants(r, "path"):
            if getattr(p, "name", "") in (lo, hi, mid_name):
                recv_write[p.name] = True
    for mc in _descendants(fn, "method-call"):
        kids = list(getattr(mc, "children", None) or [])
        recv = kids[0] if kids else None
        if getattr(recv, "kind", "") == "path" and getattr(recv, "name", "") in (
                lo, hi, mid_name):
            recv_write[recv.name] = True
    for nm in (lo, hi, mid_name):
        if recv_write.get(nm):
            return None, (f"区间减半：`{nm}` 被取地址或当方法接收者传出去"
                          f"（`&{nm}` / `{nm}.…()`）⇒ 按值语义不成立，拒绝"), True

    # ---- ②-b `mid` 全函数只写一次（声明算一次，普通赋值算一次）
    mid_decls = 0
    for kind in ("var-decl", "const-decl"):
        for d in _descendants(fn, kind):
            if getattr(d, "name", "") == mid_name:
                mid_decls += 1
    mid_assigns = 0
    for kind in ("assign", "assign-op"):
        for a in _descendants(fn, kind):
            kids = list(getattr(a, "children", None) or [])
            lhs = kids[0] if kids else None
            if getattr(lhs, "kind", "") == "path" and getattr(lhs, "name", "") == mid_name:
                mid_assigns += 1
    if mid_decls + mid_assigns != 1:
        return None, (f"区间减半：`{mid_name}` 在整个函数里有 {mid_decls} 处声明 + "
                      f"{mid_assigns} 处赋值（要**恰好一次**：两次赋值的 `mid` 是"
                      f"两个不同的划分）⇒ 拒绝"), True

    # ---- ③④ 体内对 lo/hi 的写
    wr = _writes_detail(body, {lo, hi})
    if not wr:
        return None, (f"区间减半：{guard_desc}，但体内没有 `{lo} = {mid_name} + 1` / "
                      f"`{hi} = {mid_name}` ⇒ 区间不会缩小，拒绝"), True
    seen = {}
    for w, nm, in_loop in wr:
        if in_loop:
            return None, (f"区间减半：`{nm}` 的写落在**内层循环**里（每轮可能执行"
                          f"多次，划分不成立）⇒ 拒绝"), True
        if getattr(w, "kind", "") != "assign":
            return None, (f"区间减半：对 `{nm}` 的写不是普通赋值"
                          f"（复合赋值 / 取引用传出 / 方法接收者）⇒ 拒绝"), True
        rhs = _stmt_rhs(w)
        if nm == lo:
            if not _mid_plus_one(rhs, mid_name):
                return None, (f"区间减半：`{lo}` 的写不是 `{lo} = {mid_name} + 1`"
                              f"（`{lo} = {mid_name}` 在 s = 1 时原地不动 = "
                              f"死循环）⇒ 拒绝"), True
        else:                                              # nm == hi
            if not _is_name(_plain(rhs), mid_name):
                return None, (f"区间减半：`{hi}` 的写不是 `{hi} = {mid_name}`"
                              f"（`{hi} = {mid_name} + 1` 同样可能不缩）⇒ 拒绝"), True
        if nm in seen:
            return None, (f"区间减半：`{nm}` 在体内被写了多处"
                          f"（判据要求至多一处）⇒ 拒绝"), True
        seen[nm] = w

    # ---- ③-b 两处写都出现时，必须落在**同一个 `if` 的 then / else 两支**里：
    #         每轮恰执行一支 ⇒ 区间每轮恰缩一次。否则两支可能同一轮都执行
    #         （`hi < lo`，`!=` 家族会回绕）或都不执行（区间不缩）。
    if lo in seen and hi in seen:
        wl, wh_ = seen[lo], seen[hi]
        host = None
        for st in (getattr(body, "children", None) or []):
            if getattr(st, "kind", "") != "if":
                continue
            kids = list(getattr(st, "children", None) or [])
            if len(kids) < 3:          # 没有 else 分支
                continue
            then_ids, else_ids = _subtree_ids(kids[1]), _subtree_ids(kids[2])
            if ((id(wl) in then_ids and id(wh_) in else_ids)
                    or (id(wl) in else_ids and id(wh_) in then_ids)):
                host = st
                break
        if host is None:
            return None, (f"区间减半：`{lo} = {mid_name} + 1` 与 `{hi} = {mid_name}` "
                          f"必须落在**同一个 `if` 的 then / else 两支**里（每轮恰执行"
                          f"一支）；现在它们可能同一轮都执行（`{hi} < {lo}`）或都不"
                          f"执行（区间不缩）⇒ 拒绝"), True

    # ---- ⑤ 类型：无符号（**复用 `unsigned_width_of`，不另造一份类型判据**）
    widths = []
    for nm in (lo, hi):
        w, ty = unsigned_width_of(fn, nm)
        if w is None:
            return None, f"区间减半：{ty}", True
        widths.append((w, ty))
    width = min(w for w, _ty in widths)
    ty_txt = "/".join(ty for _w, ty in widths)

    # ---- s₀：hi 的初值是**稳定容器的长度** ⇒ log2(n(xs)) + 1；否则类型位宽
    s0 = None
    hi_init = _decl_init_of(fn, hi)
    if hi_init is not None:
        cand = expr_to_bound(hi_init)
        if cand is not None and cand.kind in ("size", "elem"):
            s0 = _stable_size_quantity(fn, cand.value)
    if s0 is not None:
        b = Bound("add", left=Bound("log2", left=s0), right=Bound("const", value=1))
        why = (f"区间减半：{guard_desc}，`{mid_name} = {mid_shape}`，`{lo} = "
               f"{mid_name} + 1` / `{hi} = {mid_name}` 使区间长度 s 每轮至少减半"
               f"（s₀ = {s0.render()}）⇒ 轮数 ≤ log2({s0.render()}) + 1")
    else:
        b = Bound("const", value=width + 1)
        why = (f"区间减半：{guard_desc}，`{mid_name} = {mid_shape}`，`{lo} = "
               f"{mid_name} + 1` / `{hi} = {mid_name}` 使区间长度 s 每轮至少减半，"
               f"`{lo}`/`{hi}` 是 `{ty_txt}`（{width} 位）⇒ 轮数 ≤ {width + 1}")
    return b, why, True


def _judgeable_bound(b) -> bool:
    """这个界里**没有裸标量名**（`x` / `hi`）——即它与 target 用同一种语言。

    `var` 节点渲染成 `n(名字)`、求值侧查不到就按 ∞ 算，所以它不是一个可与
    target 比较的界（判定会退 E23「尺寸量不一致」）。`min(∞, 新界) = 新界`。
    """
    if b is None:
        return False
    k = getattr(b, "kind", "")
    if k in ("const", "size", "elem"):
        return True
    if k in ("sub", "add", "min", "max"):
        return _judgeable_bound(b.left) and _judgeable_bound(b.right)
    if k in ("log2", "ceildiv"):
        return _judgeable_bound(b.left)
    if k == "call":
        return all(_judgeable_bound(a) for a in (b.args or []))
    return False


def halving_interval_bound(old, why: str, wh: Node, fn: Node = None):
    """把「区间减半」的新界与**旧的线性界**合起来（§5 的 `min`），→ `(界, 原因)`。

    **`min` 只在证得出更小时生效**：

      · 旧界不可判定（含裸标量 `x` / `hi`）⇒ 记为 ∞，`min(∞, 新界) = 新界`；
      · 旧界是尺寸量界 ⇒ 新界 `log2(n)+1` 与 `n` 谁小依数据而定
        （`n = 0` 时 `log2 0 + 1 = 1 > 0`），**证不出**"新界 ≤ 旧界" ⇒
        **保持旧界**——这样"具体界 → 更小的界"永远有证明，且绝不比改前松。

    没有新界时**原样返回** `(old, why)`：拒绝不是 ⊤，旧界照旧成立。
    """
    nb, hwhy, _hit = halving_interval_loop(wh, fn)
    if nb is None:
        return old, why
    if old is None or not _judgeable_bound(old):
        return nb, hwhy
    if getattr(nb, "kind", "") == "const" and getattr(old, "kind", "") == "const":
        try:
            return (nb, hwhy) if int(nb.value) <= int(old.value) else (old, why)
        except (TypeError, ValueError):
            return old, why
    return old, why


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
    # **位宽有界**（2026-11-12）：`while (x != 0) { x = x & (x - 1); }` 这类
    # 「每轮至少少一位」的循环上界 = 类型位宽。命中条件但形状证不出时把**具体
    # 拒因**透出去（`!=` 在 `bound_of_condition` 里没有分支，会退化成一句
    # 「条件形态 binary 不可推」——那是废诊断）。
    wb, wb_why, wb_hit = width_bounded_loop(wh, fn)
    if wb is not None:
        return wb, wb_why
    if wb_hit:
        return None, wb_why

    ok, prog_why = progresses(wh)
    if not ok:
        return None, f"{prog_why}（循环不会推进 ⇒ 不给上界，E21）"
    b, why = bound_of_condition(cond, repeats_of, zv,
                                step_of=exact_step_prover(wh, fn),
                                nonneg_vars=nonnegative_vars(fn),
                                fn=fn, wh=wh)
    if b is not None and fn is not None:
        b = substitute(b, const_bindings(fn))
    # **区间减半**（2026-11-13）：`while (lo < hi) { mid = lo + (hi-lo)/2; … }`
    # 的轮数是 `log2(s₀)+1`，不是旧的线性界。**取 min，且只在证得出更小时生效**
    # （旧界不可判定时记 ∞ ⇒ 取新界；旧界是尺寸量界时保持旧界，绝不比改前松）。
    b, why = halving_interval_bound(b, why, wh, fn)
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
