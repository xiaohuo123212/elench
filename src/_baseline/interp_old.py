#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""interp.py —— 具体值解释器（动态闭环的执行器）。

**它做什么**：真正执行 `.eln` 里的函数，返回具体值。
**它不做什么**：不做类型检查（checker 已做）、不算成本（cost_interp 做）。

## 为什么需要它

§7.0.1 定了分工：**静态答"最多多少"（承诺），动态答"哪个候选更快"（观察）**。
"观察"必须真跑一遍——这个文件就是那台跑步机。

## 值的表示

| 语言里的 | Python 里的 |
|---|---|
| `u64` / `u8` / `i64` | `int` |
| `bool` | `bool` |
| `(a, b)` 元组 | `tuple` |
| `Vec<T>` / `FixedVec<T,N>` | `list` |
| `T!` / `Result[T, E]` | `Ok(v)` / `Err(e)` |
| 枚举值 `Token.Number(1)` | `EnumVal("Token", "Number", 1)` |

## 语义要点（规格定的，不是我发明的）

- **`+ - * <<` 按位宽回绕**（v0.67 算术全函数化）：算完 `mod 2^N`，不报错
- **`/` `%` 除数为 0 返回 `Err`**：这是唯一会失败的算术
- **`T!` 函数里 `return x`**：`x` 已经是 `Ok`/`Err` 就原样返回，否则包成 `Ok(x)`
- **`?`**：遇到 `Err` 就从当前函数返回它；否则取出值
- **`?!L(args)`**：从 `kernels L` 里挑一个候选执行。默认取 `over` 名单里第一个有实现的；
  可用 `force={标签: 候选名}` 强制指定（§7.8.2 的替换法就靠这个）
- **候选默认 inline**：候选体可以引用**宿主函数**的局部变量（`n` 就是），
  所以执行候选体时用的是宿主环境的副本
"""

from typing import Any, Dict, List, Optional

from parser import Node

DEFAULT_WIDTH = 64


# ---------------------------------------------------------------- 值

class Ok:
    __slots__ = ("v",)

    def __init__(self, v):
        self.v = v

    def __repr__(self):
        return f"Ok({self.v!r})"

    def __eq__(self, other):
        return isinstance(other, Ok) and self.v == other.v


class Err:
    __slots__ = ("e",)

    def __init__(self, e):
        self.e = e

    def __repr__(self):
        return f"Err({self.e!r})"

    def __eq__(self, other):
        return isinstance(other, Err) and self.e == other.e


class EnumVal:
    """枚举值：`Token.Number(1)` → EnumVal("Token", "Number", 1)。"""

    __slots__ = ("type_name", "variant", "payload")

    def __init__(self, type_name: str, variant: str, payload=None):
        self.type_name = type_name
        self.variant = variant
        self.payload = payload

    def __repr__(self):
        if self.payload is None:
            return f"{self.type_name}.{self.variant}"
        return f"{self.type_name}.{self.variant}({self.payload!r})"

    def __eq__(self, other):
        return (isinstance(other, EnumVal) and self.type_name == other.type_name
                and self.variant == other.variant and self.payload == other.payload)

    def __hash__(self):
        return hash((self.type_name, self.variant))


def _builtin_min(args) -> Any:
    """`min(a, b)` —— 两个同类型整数。成本常数，不影响阶。"""
    if len(args) != 2:
        raise InterpError(f"min 要 2 个实参，给了 {len(args)}")
    return args[0] if args[0] <= args[1] else args[1]


def _builtin_max(args) -> Any:
    """`max(a, b)` —— 两个同类型整数。成本常数，不影响阶。"""
    if len(args) != 2:
        raise InterpError(f"max 要 2 个实参，给了 {len(args)}")
    return args[0] if args[0] >= args[1] else args[1]


#: 内建整数二参函数。**只收这两个**——每加一个就是一个新的"可撒谎位置"，
#: 必须证明它「不改变阶」且「不引入任意代码」才收。
BUILTINS_INT2 = {
    "min": _builtin_min,
    "max": _builtin_max,
}


class FixedList(list):
    """`FixedVec<T, N>` 的值：**定长数组**，push 超过 N 必须报错。

    **为什么必须强制**：固定容量是 `FixedVec` 存在的全部意义。
    不检查它，`FixedVec<T,N>` 就只是 `Vec<T>` 的改名。
    """

    def __init__(self, capacity: int, iterable=()):
        super().__init__(iterable)
        self.capacity = capacity

    def __repr__(self):
        return f"FixedVec[{len(self)}/{self.capacity}]"


class StructVal:
    """结构体值：`Stats { mean: 1, variance: 2 }` → StructVal("Stats", {...})。"""

    __slots__ = ("type_name", "fields")

    def __init__(self, type_name: str, fields: Dict[str, Any]):
        self.type_name = type_name
        self.fields = dict(fields)

    def __repr__(self):
        inner = ", ".join(f"{k}: {v!r}" for k, v in self.fields.items())
        return f"{self.type_name} {{ {inner} }}"

    def __eq__(self, other):
        return (isinstance(other, StructVal) and self.type_name == other.type_name
                and self.fields == other.fields)


class TypeRef:
    """类型引用（`FixedVec<T,N>` 出现在表达式位置时）。只用于静态调用。"""

    __slots__ = ("type_name", "member")

    def __init__(self, type_name: str, member: str = ""):
        self.type_name = type_name
        self.member = member

    def __repr__(self):
        return f"{self.type_name}::{self.member}"


class InterpError(Exception):
    pass


class InterpBudgetExceeded(InterpError):
    """步数超限：多半是死循环。"""


class _Return(Exception):
    def __init__(self, value):
        self.value = value


# ---------------------------------------------------------------- 解释器

class Interp:
    def __init__(self, impl_mod: Node, decl_mod: Optional[Node] = None,
                 budget: int = 5_000_000):
        self.impl = impl_mod
        self.decl = decl_mod
        self.budget = budget
        self.steps = 0
        # 计数（M1 验收③）：调用数 / 分配数 / 迭代数
        self.calls: Dict[str, int] = {}
        self.allocs = 0
        self.iters = 0
        # 与 T11 的 κ 公式对账用：`xs.length()` / `xs.at(i)` 各算 1 次方法调用
        # （G26 暂定口径：1 次调用 = 1 次 κ；`at` 是循环体里最贵的那一项）
        self.method_calls = 0
        self._cap: Dict[int, int] = {}      # 容器 id → 当前容量（模拟扩容）
        self.fns: Dict[str, Node] = {}
        self.kernels: Dict[str, Node] = {}
        self.over: Dict[str, List[str]] = {}
        self._collect()

    # ---------- 索引
    def _collect(self) -> None:
        # 模块级常量（`const SPACE: u8 = 32;`）—— 先收，函数体要用
        self.globals: Dict[str, Any] = {}
        for mod in (self.decl, self.impl):
            if mod is None:
                continue
            for c in _descendants(mod, "const"):
                if len(c.children) >= 2:
                    self.globals[c.name] = _literal(c.children[-1])

        for fn in _descendants(self.impl, "fn"):
            self.fns[fn.name] = fn
        for k in _descendants(self.impl, "kernels"):
            self.kernels[k.name or ""] = k
        if self.decl is not None:
            for ch in _descendants(self.decl, "choose"):
                for cl in ch.children:
                    if cl.kind == "choose-clause" and cl.name == "over":
                        names = (cl.value or {}).get("names", [])
                        self.over[ch.name] = list(names)

    # ---------- 入口
    def counters(self) -> Dict[str, int]:
        """M1 验收③：每函数调用数 / 分配数 / 迭代数。"""
        return {"function_calls": dict(self.calls),
                "function_calls_total": sum(self.calls.values()),
                "method_calls": self.method_calls,
                "allocations": self.allocs,
                "iterations": self.iters,
                "evaluation_steps": self.steps}

    def call(self, fn_name: str, args: List[Any],
             force: Optional[Dict[str, str]] = None) -> Any:
        fn = self.fns.get(fn_name)
        if fn is None:
            raise InterpError(f"找不到函数 {fn_name}")
        return self._run_fn(fn, args, force or {}, {})

    def _run_fn(self, fn: Node, args: List[Any], force: Dict[str, str],
                outer: Dict[str, Any]) -> Any:
        self.calls[fn.name] = self.calls.get(fn.name, 0) + 1
        env: Dict[str, Any] = dict(self.globals)
        env.update(outer)
        params = next((c for c in fn.children if c.kind == "params"), None)
        pnames = [c.name for c in (params.children if params else [])]
        if len(args) != len(pnames):
            raise InterpError(f"{fn.name} 需要 {len(pnames)} 个实参，给了 {len(args)}")
        for nm, v in zip(pnames, args):
            env[nm] = v

        block = next((c for c in fn.children if c.kind == "block"), None)
        if block is None:
            raise InterpError(f"{fn.name} 没有函数体（可能还是 pending）")
        fallible = bool((fn.value or {}).get("fallible"))
        try:
            self._block(block, env, force)
            return Ok(None) if fallible else None
        except _Return as r:
            if fallible:
                v = r.value
                return v if isinstance(v, (Ok, Err)) else Ok(v)
            return r.value

    # ---------- 语句
    def _tick(self) -> None:
        self.steps += 1
        if self.steps > self.budget:
            raise InterpBudgetExceeded(
                f"执行超过 {self.budget} 步，疑似死循环（已执行 {self.steps} 步）")

    def _block(self, blk: Node, env: Dict[str, Any], force: Dict[str, str]) -> None:
        for st in blk.children:
            self._stmt(st, env, force)

    def _stmt(self, st: Node, env: Dict[str, Any], force: Dict[str, str]) -> None:
        self._tick()
        k = st.kind

        if k in ("const-decl", "var-decl"):
            # children[0] 是类型，最后一个是初值
            env[st.name] = self._eval(st.children[-1], env, force)
            return

        if k == "assign":
            val = self._eval(st.children[1], env, force)
            self._store(st.children[0], val, env, force)
            return

        if k == "expr-stmt":
            self._eval(st.children[0], env, force)
            return

        if k == "return":
            if st.children:
                raise _Return(self._eval(st.children[0], env, force))
            raise _Return(None)

        if k == "if":
            cond = self._eval(st.children[0], env, force)
            blocks = [c for c in st.children[1:] if c.kind == "block"]
            if cond:
                if blocks:
                    self._block(blocks[0], env, force)
                return
            # else / else if 链：`elif` 节点可能是 if 或 block
            rest = [c for c in st.children[1:] if c.kind in ("block", "if")]
            for c in rest[1:]:
                if c.kind == "block":
                    self._block(c, env, force)
                    return
                self._stmt(c, env, force)
                return
            return

        if k == "while":
            cond, body = st.children[0], next(
                (c for c in st.children[1:] if c.kind == "block"), None)
            while self._eval(cond, env, force):
                self._tick()
                self.iters += 1
                if body is not None:
                    self._block(body, env, force)
            return

        if k == "for":
            base = next((c for c in st.children if c.kind == "path"), None)
            body = next((c for c in st.children if c.kind == "block"), None)
            seq = self._eval(base, env, force) if base is not None else []
            if not isinstance(seq, list):
                seq = list(seq) if seq is not None else []
            var = st.name
            for item in seq:
                self._tick()
                self.iters += 1
                env[var] = item
                if body is not None:
                    self._block(body, env, force)
            return

        if k == "match":
            return self._match(st, env, force)

        if k == "block":
            self._block(st, env, force)
            return

        if k == "dispatch":
            self._eval(st, env, force)
            return

        raise InterpError(f"未实现的语句：{k}")

    def _store(self, target: Node, val: Any, env, force) -> None:
        if target.kind == "path":
            if target.name not in env:
                raise InterpError(f"赋值给未绑定的名字 {target.name}")
            env[target.name] = val
            return
        if target.kind == "field":
            base = self._eval(target.children[0], env, force)
            if isinstance(base, list) and target.name is not None and target.name.isdigit():
                base[int(target.name)] = val
                return
            raise InterpError(f"未实现的赋值目标：{target.kind}/{target.name}")
        if target.kind == "index":
            base = self._eval(target.children[0], env, force)
            idx = self._eval(target.children[1], env, force)
            base[idx] = val
            return
        raise InterpError(f"未实现的赋值目标：{target.kind}")

    def _match(self, st: Node, env, force) -> None:
        val = self._eval(st.children[0], env, force)
        arms = [c for c in st.children[1:] if c.kind == "arm"]
        for arm in arms:
            pat = next((c for c in arm.children if c.kind in
                        ("pattern", "pattern-wildcard")), None)
            # 臂的体：可能是 block，也可能是一条单语句（`Pat => return 1,`）
            body = next((c for c in arm.children if c.kind == "block"), None)
            single = [c for c in arm.children
                      if c.kind not in ("pattern", "pattern-wildcard", "block")]
            if pat is None:
                continue
            hit = False
            if pat.kind == "pattern-wildcard":
                hit = True
            else:
                want = (pat.name or "").split(".")[-1]
                if isinstance(val, EnumVal) and val.variant == want:
                    hit = True
                    binds = [c for c in pat.children if c.kind == "binding"]
                    if binds:
                        self._bind_pattern(binds[0], val.payload, env)
            if not hit:
                continue
            if body is not None:
                self._block(body, env, force)
            elif single:
                self._stmt(single[0], env, force)
            return
        # 没有匹配的臂：不报错，静默跳过（类型检查器该保证穷尽）

    def _bind_pattern(self, b: Node, payload: Any, env) -> None:
        if b.name is None:
            return
        if isinstance(payload, tuple):
            env[b.name] = payload[0]
        else:
            env[b.name] = payload

    # ---------- 表达式
    def _eval(self, n: Optional[Node], env, force) -> Any:
        if n is None:
            return None
        self._tick()
        k = n.kind

        if k == "lit-int":
            return int(n.value)
        if k == "lit-char":
            t = str(n.value)
            return ord(t[1]) if len(t) >= 3 else ord(t)
        if k == "lit-bool":
            return n.value in (True, "true")
        if k == "path":
            if n.name in env:
                return env[n.name]
            # `Token::Plus` → path "Token.Plus"：类型命名空间里的变体（无载荷）
            if "." in (n.name or ""):
                ty, va = n.name.rsplit(".", 1)
                return EnumVal(ty, va)
            raise InterpError(f"未绑定的名字 {n.name}")
        if k == "paren":
            return self._eval(n.children[0], env, force)
        if k == "tuple":
            return tuple(self._eval(c, env, force) for c in n.children)
        if k == "struct-literal":
            fields = {}
            for fi in n.children:
                if fi.kind == "field-init" and fi.children:
                    fields[fi.name] = self._eval(fi.children[0], env, force)
            return StructVal(n.name or "", fields)
        if k == "unary":
            v = self._eval(n.children[0], env, force)
            op = n.value or ""
            if op == "not":
                return not v
            if op == "-":
                return _wrap(-v)
            raise InterpError(f"未实现的一元运算 {op}")
        if k == "binary":
            return self._binary(n, env, force)
        if k == "call":
            return self._call(n, env, force)
        if k == "method-call":
            return self._method(n, env, force)
        if k == "field":
            return self._field(n, env, force)
        if k == "cast":
            v = self._eval(n.children[0], env, force)
            return int(v) if not isinstance(v, bool) else int(v)
        if k == "try":
            v = self._eval(n.children[0], env, force)
            if isinstance(v, Err):
                raise _Return(v)
            if isinstance(v, Ok):
                return v.v
            return v
        if k == "dispatch":
            return self._dispatch(n, env, force)
        if k == "index":
            base = self._eval(n.children[0], env, force)
            idx = self._eval(n.children[1], env, force)
            return base[idx]

        if k == "type-name":
            return TypeRef(n.name or "")
        if k == "ref":
            # `&x` 只是"按引用传"的标记，值就是它指的东西
            return self._eval(n.children[0], env, force) if n.children else None

        raise InterpError(f"未实现的表达式：{k}")

    def _binary(self, n: Node, env, force) -> Any:
        op = n.value or ""
        a = self._eval(n.children[0], env, force)
        # **短路**：`and` / `or` 的右侧可能越界（`i < n and xs.at(i) > 0`），
        # 必须先看左侧再决定要不要算右侧。
        if op == "and":
            return bool(a) and bool(self._eval(n.children[1], env, force))
        if op == "or":
            return bool(a) or bool(self._eval(n.children[1], env, force))
        b = self._eval(n.children[1], env, force)
        if op == "+":
            return _wrap(a + b)
        if op == "-":
            return _wrap(a - b)
        if op == "*":
            return _wrap(a * b)
        if op == "<<":
            return _wrap(a << b)
        if op == "/":
            if b == 0:
                return Err(EnumVal("Arithmetic", "DivisionByZero"))
            # 整数除法向零截断（与 C 一致）
            q = abs(a) // abs(b)
            return Ok(q if (a >= 0) == (b >= 0) else -q)
        if op == "%":
            if b == 0:
                return Err(EnumVal("Arithmetic", "DivisionByZero"))
            r = abs(a) % abs(b)
            return Ok(r if a >= 0 else -r)
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        raise InterpError(f"未实现的二元运算 {op}")

    def _call(self, n: Node, env, force) -> Any:
        name = n.name or ""
        # `Token::Number(1)` → call name "Token.Number"：带载荷的枚举构造
        if "." in name:
            ty, va = name.rsplit(".", 1)
            args = self._args(n, env, force)
            payload = None
            if len(args) == 1:
                payload = args[0]
            elif len(args) > 1:
                payload = tuple(args)
            return EnumVal(ty, va, payload)
        if name == "":
            # `FixedVec<T, N>::make()` —— 静态构造
            callee_node = n.children[0]
            args = self._args(n, env, force)
            if callee_node.kind == "field" and callee_node.children \
                    and callee_node.children[0].kind == "type-name" \
                    and callee_node.name in ("make", "filled"):
                tn = callee_node.children[0]
                cap = _fixed_capacity(tn, self.globals)

                # `FixedVec<T, N>::make()`   → **空**（栈语义：靠 push 长到 N）
                # `FixedVec<T, N>::filled(v)` → **N 个 v**（表语义：一建好就能下标写）
                #
                # 两个构造子对应两种意图。只给 make 的话，
                # 「建一张表」就得先写 push 循环——而 C 的 `u64 dp[N]` 是直接建好的。
                if tn.name == "FixedVec" and cap is not None:
                    if callee_node.name == "filled":
                        fill = args[0] if args else 0
                        return FixedList(cap, [fill] * cap)
                    return FixedList(cap)
                if callee_node.name == "filled":
                    fill = args[0] if args else 0
                    n = args[1] if len(args) > 1 else 0
                    return [fill] * int(n)
                return []
            # `Token.Number(1)` 这类：callee 不是 path，而是 field（枚举变体）
            callee = self._eval(callee_node, env, force)
            args = self._args(n, env, force)
            if isinstance(callee, EnumVal):
                payload = None
                if len(args) == 1:
                    payload = args[0]
                elif len(args) > 1:
                    payload = tuple(args)
                return EnumVal(callee.type_name, callee.variant, payload)
            raise InterpError(f"不可调用的表达式：{n.children[0].kind}")
        # `sort(xs)` / `sort_by(xs, 具名比较器)` —— **必须在求值实参之前拦**：
        # 第二个实参是比较器的**名字**，不是值（本语言没有一等函数，
        # 直接 _eval 会报「未绑定的名字」）。
        if name in ("sort", "sort_by"):
            return self._builtin_sort(name, n, env, force)

        args = self._args(n, env, force)

        if name in BUILTINS_INT2:
            return BUILTINS_INT2[name](args)

        if name == "Err":
            return Err(args[0] if args else None)
        if name == "Ok":
            return Ok(args[0] if args else None)

        fn = self.fns.get(name)
        if fn is None:
            raise InterpError(f"调用了未定义的函数 {name}")
        return self._run_fn(fn, args, force, {})

    def _builtin_sort(self, name: str, n: Node, env, force) -> Any:
        """`sort(xs)` 原地稳定升序；`sort_by(xs, cmp)` 用**具名**比较器。

        ## 为什么比较器必须是具名函数

        C 的 `qsort` 拿函数指针，比较器是**任意代码**——那样排序的成本
        `O(n log n × κ(cmp))` 里的 `κ(cmp)` 就无法静态界定，成本契约会被打穿。

        **具名函数是有限集合，成本可静态分析。** 这不是限制，是把契约守住。

        ## 为什么强制稳定

        我们已经吃过一次亏：v0.69 的程序 5 因为「递减循环推不出上界」
        被迫把插入排序改成选择排序，**丢了稳定性**。
        语言自己给排序，就该给稳定的那一种。
        """
        if n.children:
            args_node = next((c for c in n.children if c.kind == "args"), None)
        else:
            args_node = None
        items = args_node.children if args_node is not None else []
        if not items:
            raise InterpError(f"{name} 至少要 1 个实参（容器）")

        recv = self._eval(items[0], env, force)
        if not isinstance(recv, list):
            raise InterpError(f"{name} 的第一个实参必须是容器，给的是 {type(recv).__name__}")

        if name == "sort":
            # Python 的 list.sort 是稳定排序
            try:
                recv.sort()
            except TypeError as e:
                raise InterpError(f"sort 无法比较容器里的元素：{e}") from None
            return len(recv)

        # ---- sort_by
        if len(items) < 2:
            raise InterpError("sort_by 要 2 个实参：容器 + 具名比较器")
        cmp_node = items[1]
        if cmp_node.kind != "path":
            raise InterpError(
                "sort_by 的比较器 MUST 是具名函数 "
                f"（给的是 {cmp_node.kind}）——匿名比较器是任意代码，成本不可静态界定")
        cmp_name = cmp_node.name or ""
        cmp_fn = self.fns.get(cmp_name)
        if cmp_fn is None:
            raise InterpError(f"sort_by 的比较器 {cmp_name} 不是本模块的函数")

        def _cmp(a, b):
            # 比较器返回「a 应排在 b 前」
            if self._run_fn(cmp_fn, [a, b], force, {}) in (True, 1):
                return -1
            if self._run_fn(cmp_fn, [b, a], force, {}) in (True, 1):
                return 1
            return 0

        import functools
        try:
            recv.sort(key=functools.cmp_to_key(_cmp))
        except TypeError as e:
            raise InterpError(f"sort_by 无法比较：{e}") from None
        return len(recv)

    def _args(self, n: Node, env, force) -> List[Any]:
        out = []
        for c in n.children:
            if c.kind == "args":
                for a in c.children:
                    if a.kind == "named-arg":
                        # `Arith(cause = ...)`：解释器只关心值，名字留给检查器
                        a = a.children[0] if a.children else None
                        if a is None:
                            continue
                    out.append(self._eval(a, env, force))
        return out

    def _method(self, n: Node, env, force) -> Any:
        name = n.name or ""
        recv_node = n.children[0]
        recv = self._eval(recv_node, env, force)
        args = []
        for c in n.children[1:]:
            if c.kind == "args":
                for a in c.children:
                    if a.kind == "named-arg":
                        a = a.children[0] if a.children else None
                        if a is None:
                            continue
                    args.append(self._eval(a, env, force))

        self.method_calls += 1

        if name == "length":
            return len(recv)
        if name == "at":
            return recv[args[0]]
        if name == "push":
            if isinstance(recv, FixedList) and len(recv) >= recv.capacity:
                raise InterpError(
                    f"FixedVec 容量溢出：容量 {recv.capacity}，已经装了 {len(recv)} 个。"
                    f"定长数组不能增长——要么调大 N，要么先用 `requires` 挡住")
            if not isinstance(recv, FixedList):
                cap = self._cap.get(id(recv), 0)
                if len(recv) >= cap:
                    self._cap[id(recv)] = max(4, cap * 2)
                    self.allocs += 1
            recv.append(args[0])
            return recv
        if name == "pop":
            # T12（前置条件优先）：调用点已保证非空，所以直接返回值，不返回 Result
            if not recv:
                raise InterpError("pop 在空容器上调用（调用点应已由 requires 保证非空）")
            return recv.pop()
        if name == "is_empty":
            return len(recv) == 0
        raise InterpError(f"未实现的方法 {name}")

    def _field(self, n: Node, env, force) -> Any:
        # `Type::member`：类型名本身不是值，返回一个类型引用标记
        if n.children and n.children[0].kind == "type-name":
            return TypeRef(n.children[0].name or "", n.name or "")
        # 1) 枚举变体：Token.Number / CalcError.Empty
        if n.children and n.children[0].kind == "path":
            base_name = n.children[0].name or ""
            if base_name not in env:
                # 当枚举类型名处理
                return EnumVal(base_name, n.name or "")
        base = self._eval(n.children[0], env, force)
        nm = n.name if n.name is not None else str(n.value if n.value is not None else "")
        if nm.isdigit():
            return base[int(nm)]
        if isinstance(base, StructVal):
            if nm not in base.fields:
                raise InterpError(f"{base.type_name} 没有字段 {nm}"
                                  f"（有：{', '.join(base.fields)}）")
            return base.fields[nm]
        if isinstance(base, EnumVal):
            return EnumVal(base.type_name, base.variant, base.payload)
        raise InterpError(f"未实现的字段访问 {nm}")

    def _dispatch(self, n: Node, env, force) -> Any:
        label = n.name or ""
        args = self._args(n, env, force)
        kern = self.kernels.get(label)
        if kern is None:
            raise InterpError(f"?!{label} 没有对应的 kernels 块")

        cands = {c.name: c for c in kern.children if c.kind == "candidate"}
        want = force.get(label)
        if want is not None:
            cand = cands.get(want)
            if cand is None:
                raise InterpError(f"?!{label}: 没有候选 {want}")
        else:
            order = self.over.get(label) or list(cands)
            cand = None
            for nm in order:
                if nm in cands and any(c.kind == "block" for c in cands[nm].children):
                    cand = cands[nm]
                    break
            if cand is None:
                for nm, c in cands.items():
                    if any(x.kind == "block" for x in c.children):
                        cand = c
                        break
        if cand is None:
            raise InterpError(f"?!{label}: 没有已实现的候选")

        # 候选 inline：在宿主环境的副本上执行（所以能读到 `n`）
        cenv = dict(self.globals)
        cenv.update(env)
        params = next((c for c in kern.children if c.kind == "params"), None)
        pnames = [c.name for c in (params.children if params else [])]
        for nm, v in zip(pnames, args):
            cenv[nm] = v
        body = next((c for c in cand.children if c.kind == "block"), None)
        try:
            self._block(body, cenv, force)
            return None
        except _Return as r:
            return r.value


# ---------------------------------------------------------------- 工具

def _fixed_capacity(tn: Node, consts: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """从 `FixedVec<T, N>` 的 type-name 节点里取出 N。

    **N 可以是字面量，也可以是模块级常量名**（`FixedVec<u64, STACK_CAP>`）。
    只认字面量会让「用常量名写的 FixedVec」**静默退化成普通 Vec**——
    定长性丢了，`push` 不再报错，是整个语言最不该有的静默。
    """
    consts = consts or {}
    for c in tn.children:
        if c.kind != "type-args":
            continue
        for a in c.children:
            if a.kind == "type-const":
                try:
                    return int(a.value)
                except (TypeError, ValueError):
                    return None
            if a.kind == "type-name":
                nm = a.name or ""
                if nm.isdigit():
                    return int(nm)
                v = consts.get(nm)
                if isinstance(v, int):
                    return v
    return None


def _literal(n: Optional[Node]) -> Any:
    """把常量初值（字面量）变 Python 值；非字面量给 None。"""
    if n is None:
        return None
    if n.kind == "lit-int":
        return int(n.value)
    if n.kind == "lit-bool":
        return n.value in (True, "true")
    if n.kind == "lit-char":
        s = str(n.value)
        return ord(s[1]) if len(s) >= 3 else ord(s)
    return None


def _wrap(v: int, width: int = DEFAULT_WIDTH) -> int:
    """按位宽回绕（v0.67 算术全函数化）。"""
    if not isinstance(v, int) or isinstance(v, bool):
        return v
    m = 1 << width
    return v % m


def _descendants(n: Optional[Node], kind: str, acc=None) -> List[Node]:
    if acc is None:
        acc = []
    if n is None:
        return acc
    if n.kind == kind:
        acc.append(n)
    for c in n.children:
        _descendants(c, kind, acc)
    return acc
