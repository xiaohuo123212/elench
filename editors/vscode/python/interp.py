#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-

import stdlib
import sys

import os
import threading
import re
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
from ast_util import descendants as _descendants
from kinds import INTERP_EXPR, INTERP_STMT

DEFAULT_WIDTH = 64


# ---------------------------------------------------------------- 操作数计数器
#
# **它要回答的问题**：`κ` 是不是上界？——只有把两边换成**同一单位**才谈得上比大小。
# 这个计数器数的是「**操作数**」，不是纳秒；它与 `cost_interp` 的计费点**一一对应**：
#
# | 计 1 的东西 | 这里的计费点 | 对应的 `cost_interp` 计费点 |
# |---|---|---|
# | 一次**表达式求值** | `_eval`（每个非 `None` 节点，含叶子与 `paren` 包裹） | `_expr` 的每个节点 |
# | 一次**语句执行** | `_stmt`（每条语句每次执行都算） | `_stmt` 的每次调用 |
# | 一次**内建调用** | `_call` / `_method` 的内建分派 | `_call` 里那几个 `OP_COST` |
#
# **粒度**：进入 `_run_fn` 记下起点、返回时结算，所以 `ops_of(f)` 是
# **整棵调用树**（含嵌套调用）的操作数——这正是能和 `κ(f)`（它也含被调函数）比的那个数。
#
# **默认关闭**：`ELENCH_COUNT_OPS=1`（或编程调用 `ops_enable()`）才开。
# 热路径上只有单分支 `if _COUNT:`，不建对象、不改语义（计数只加不查）。
#
# **线程模式下不精确**：`parallel { … } threads` 里的 `_bump()
# 会**少量少计**。要做"ops ≤ κ"的判定请用顺序模式（`parallel="seq"`，默认），
# `tools/kappa_vs_ops.py` 就是这么跑的。
#
# **已知的口径差**（不要为了凑 `ops ≤ κ` 去改小计数，见 §8.5）：
#   · `min` / `max`：`cost_interp` 记 1 且**完全不计实参**，这里记 1 + 实参数 —— κ 偏小；
#   · `print`：κ 记 `1 + 实参`，这里记 `1 + 实参 + 内建 1` —— κ 少 1；
#   · `Ok(...)` / `Err(...)`：κ 记 1、不计实参，这里记 1 + 实参 —— κ 偏小；
#   · `expr-stmt`：κ 只记表达式本身，这里还有 `_stmt` 的 1 —— 每句多 1；
#   · `paren`：κ 记 0（只算里面的），这里算 1 个节点 —— 每层多 1。
# 这些差**两个方向都有**；哪一边是错的要逐条查（`min`/`max`、`Ok`/`Err` 是 κ 少算，
# `expr-stmt`/`paren` 是计数多算）。它们会不会真的破界，由
# `tools/kappa_vs_ops.py` 逐函数实测，不许猜。

#: 计数开关：默认关。**只读一次环境变量**，之后由 `ops_enable()` 改。
_COUNT = os.environ.get("ELENCH_COUNT_OPS", "") not in ("", "0", "false", "False", "off")

#: **per-thread 计数器**：每个线程只写自己的槽位，读的时候再汇总。
#:
#: 为什么不用 `_bump()
#: 两步之间别的线程可以插进来，于是两个线程各加 1、结果只加了 1（丢增量）。
#: 丢的量补不回来 ⇒ 线程模式只会**少计** ⇒ `ops ≤ κ` 变成假阳性。
#:
#: 代价：热路径多一次 `threading.local()` 属性读（无锁、无竞争）。
_LOCAL = threading.local()

#: 所有线程的槽位（`{"n": 总操作数, "by": {函数名: 操作数}}`）。
_SLOTS: List[dict] = []


def _slot() -> dict:
    s = getattr(_LOCAL, "slot", None)
    if s is None:
        s = {"n": 0, "by": {}}
        _LOCAL.slot = s
        _SLOTS.append(s)
    return s


def _bump() -> None:
    """+1（本线程）。**热路径**：不做任何跨线程写。"""
    _slot()["n"] += 1


def _bump_fn(name: str, delta: int) -> None:
    if delta:
        b = _slot()["by"]
        b[name] = b.get(name, 0) + delta


def _total() -> int:
    return sum(s["n"] for s in _SLOTS)


def _total_of(name: str) -> int:
    return sum(s["by"].get(name, 0) for s in _SLOTS)


def ops_enabled() -> bool:
    """计数开关当前是否打开。"""
    return _COUNT


def ops_enable(on: bool = True) -> None:
    """打开/关闭计数（`--ops` 与 `ELENCH_COUNT_OPS=1` 之外的编程入口）。"""
    global _COUNT
    _COUNT = bool(on)


def ops_reset() -> None:
    """清零：所有线程槽位。**每次测量前调一次**（否则数字会累加）。"""
    for s in _SLOTS:
        s["n"] = 0
        s["by"].clear()


def ops_of(fn: Optional[str] = None) -> int:
    """**实测操作数**（所有线程汇总）。

    `fn` 给名字 → 该函数整棵调用树的操作数（含嵌套调用）；
    `fn` 缺省 → 本进程自 `ops_reset()` 以来的总操作数。

    **单位是「操作」，不是纳秒**——它只和 `κ`（也是操作数）可比。
    """
    if fn is None:
        return _total()
    return _total_of(fn)


def ops_by_fn() -> Dict[str, int]:
    """每函数一份（**含嵌套调用**，因此各项之和 ≥ 总数）。"""
    out: Dict[str, int] = {}
    for s in _SLOTS:
        for k, v in s["by"].items():
            out[k] = out.get(k, 0) + v
    return out


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


def _builtin_print(args) -> Any:
    """`print(x)` —— 打印一行，返回 0（u64）。

    **它是"调试内建"，不是契约维度**：成本只记 1 个操作，
    不建模 IO 延迟。真正的 IO 成本该走 `effects { io }`，那是另一件事。
    """
    if not args:
        raise InterpError("print 至少要 1 个实参")
    # **插值**：`print("x = {}, y = {:>2}", x, y)` —— 第一个参数含 `{` 就当格式串。
    # 没有 `{` 就保持原来的"空格分隔"行为（老代码不受影响）。
    if len(args) >= 2:
        first = args[0]
        txt = first.text() if isinstance(first, Text) else (
            bytes(int(x) & 0xFF for x in first).decode("utf-8", "replace")
            if isinstance(first, list) else str(first))
        if "{" in txt:
            res = stdlib.FNS["format"]["impl"](args)
            try:
                sys.stdout.write(bytes(int(x) & 0xFF for x in res).decode("utf-8", "replace") + "\n")
                sys.stdout.flush()
            except (BrokenPipeError, OSError) as exc:
                raise InterpError(f"输出失败（{type(exc).__name__}）：{exc}") from None
            return 0

    parts = []
    for a in args:
        if isinstance(a, Text):
            parts.append(a.text())
        elif isinstance(a, list):
            parts.append(" ".join(str(x) for x in a))
        else:
            parts.append(str(a))
    try:
        sys.stdout.write(" ".join(parts) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, OSError) as exc:
        # **不让 Python 的 traceback 漏出来**：管道断 / 磁盘满时，
        # 用户该看到的是语言级的"输出失败"，不是实现的调用栈。
        raise InterpError(f"输出失败（{type(exc).__name__}）：{exc}") from None
    return 0


#: 内建整数二参函数。**只收这两个**——每加一个就是一个新的"可撒谎位置"，
#: 必须证明它「不改变阶」且「不引入任意代码」才收。
BUILTINS_INT2 = {
    "min": _builtin_min,
    "max": _builtin_max,
}

#: 单参内建（含 `print`）。
BUILTINS_INT1 = {
    "print": _builtin_print,
}


class Text(list):
    """字符串字面量的值：**字节序列**，但打印时按 UTF-8 文本输出。

    为什么要单独一个类型：`Vec<u8>` 和"文本"在值层面相同，
    但 `print` 得知道该打 `104 105` 还是 `hi`。
    """

    def text(self) -> str:
        return bytes(self).decode("utf-8", "replace")


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


class FnVal:
    """**函数值**：把具名函数当参数传（函数类型 slice 1）。

    **只包名字，不包代码**——本语言没有闭包，函数值就是"一个函数的名字"。
    这样：κ 能算（名字 → 声明 → κ），类型能查（签名对得上才传得进来）。
    """

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return f"FnVal({self.name})"


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


_TABLE_LINE = re.compile(r"-\s*\{lo:\s*(\d+),\s*hi:\s*(\d+),\s*candidate:\s*([A-Za-z_][\w]*)\}")


def load_dispatch_table(path: Optional[str]):
    """读 `<模块>.dispatch.yaml` → {label: [(lo, hi, 候选名), ...]}。

    **为什么解释器要读它**：分档表如果只给人和门禁看，那"按段动态取最优"
    在运行期就是一句空话——**表对不对，也没有任何运行结果能反驳它**。
    读表之后，同一个程序"按表跑"和"逐候选实测"可以互相对账，缺口才闭得上。

    无文件 / 解析失败 → 返回 `{}`（调用方退化为"第一个实现"，并打警告）。
    """
    if not path or not os.path.exists(path):
        return {}
    out = {}
    label = None
    try:
        for raw in open(path, encoding="utf-8"):
            line = raw.rstrip("\n")
            # 两种写法都收：`label: x`（measure 生成的格式）与 `- label: x`（列表格式）
            m = re.match(r"\s*(?:-\s*)?label:\s*([A-Za-z_][\w]*)", line)
            if m:
                label = m.group(1)
                out.setdefault(label, [])
                continue
            m2 = _TABLE_LINE.search(line)
            if m2 and label:
                out[label].append((int(m2.group(1)), int(m2.group(2)), m2.group(3)))
    except Exception:                                  # noqa: BLE001
        return {}
    return {k: v for k, v in out.items() if v}


def _size_of_args(args):
    """用**第一个实参的规模**当分档依据。

    | 实参 | 规模 |
    |---|---|
    | 列表 / 元组 / 字符串 | 长度 |
    | 整数 | 它本身 |
    其它 → `None`（不查表，退化为第一个实现）。
    """
    if not args:
        return None
    a = args[0]
    if isinstance(a, (list, tuple, str, bytes)):
        return len(a)
    if isinstance(a, int) and not isinstance(a, bool):
        return int(a)
    return None


class ExitRequest(Exception):
    """`exit(code)` 用：**正常的控制流终止**，不是错误。"""

    def __init__(self, code: int):
        self.code = int(code)
        super().__init__(f"exit({code})")


class InterpError(Exception):
    pass


class InterpBudgetExceeded(InterpError):
    """步数超限：多半是死循环。"""


#: **会改动接收者的方法**：对 `const T&` 形参调用它们一律拒绝。
MUTATING_METHODS = {
    "push", "pop", "set", "insert", "remove", "clear", "append",
    "extend", "sort", "sort_by", "reverse", "fill", "swap", "resize",
}


class _Break(Exception):
    """`break;` —— 只跳出一层循环（由 while / for 捕获）。"""


class _Continue(Exception):
    """`continue;` —— 结束本轮，进入下一轮。"""


class _Return(Exception):
    def __init__(self, value):
        self.value = value


# ---------------------------------------------------------------- 解释器

class Interp:
    def __init__(self, impl_mod: Node, decl_mod: Optional[Node] = None,
                 budget: int = 5_000_000, table_path: Optional[str] = None,
                 parallel: str = "seq"):
        self.impl = impl_mod
        self.decl = decl_mod
        self.budget = budget
        # **运行期按表选候选**（2026-10-30 用户决定，方案甲）
        self.table_path = table_path
        self.tables = load_dispatch_table(table_path)
        # label → 实际走的候选（**per-thread**：每个线程各记各的，见 `last_candidates` 属性）
        self._lc_slot: Dict[str, str] = {}
        self.dispatch_warnings: List[str] = []
        # **并行执行模式**：`seq`（默认，顺序）或 `threads`（真线程）。
        # 注意：原型是 Python，受 GIL 限制**没有 CPU 加速**；
        # 这里实现线程是为了**验证语义**（结果必须与顺序执行完全一致）。
        self.parallel_mode = parallel
        # **FFI**：`extern` 声明（名字 → 节点）与已加载的 ctypes 句柄
        self.externs = {c.name: c for c in (impl_mod.children or [])
                        if getattr(c, "kind", "") == "extern"}
        # **spec 里的 external 子句**：`spec f(...) { external "lib"; }` → 名字 → 库名
        self.extern_specs = {}
        for _it in ((decl_mod.children if decl_mod is not None else []) or []):
            if getattr(_it, "kind", "") != "spec":
                continue
            _body = next((c for c in _it.children if c.kind == "spec-body"), None)
            for _c in (_body.children if _body else []):
                if _c.kind == "clause" and (_c.name or "") == "external":
                    _lib = (_c.value or {}).get("lib") if isinstance(_c.value, dict) else None
                    self.extern_specs[_it.name] = _lib
        # **"找不到函数体 = 外部声明"**：模块级 `link "lib";` 给出库名。
        self.link_lib = None
        for _it in ((decl_mod.children if decl_mod is not None else []) or []):
            if getattr(_it, "kind", "") == "link":
                self.link_lib = _it.name
        # **spec 名字集合**（用于"找不到函数体 = 外部声明"的懒判定）
        self._spec_names = {c.name for c in ((decl_mod.children if decl_mod is not None else []) or [])
                            if getattr(c, "kind", "") == "spec"}
        self._ffi_cache = {}
        # **随机基种子**：ELENCH_SEED 优先，否则时钟（每次运行不同）。
        self.rand_seed = stdlib.reset_base()
        self.steps = 0
        # **与 κ 同口径的操作计数**（`_eval` + `_stmt` + 内建调用，见文件头的计数器一节）。
        # 它**不是** `evaluation_steps`：后者多了「每轮循环」的 `_tick`，
        # 两者不能混用（拿 ns 去比 operations 更是单位错配）。
        # 只有 `_COUNT` 打开时才写；默认 0。
        self.ops = 0
        # 计数（M1 验收③）：调用数 / 分配数 / 迭代数
        self.calls: Dict[str, int] = {}
        # 内建调用次数（print / hmap_* …）——给「不可测量」的函数报用量
        self.builtin_calls: Dict[str, int] = {}
        self.allocs = 0
        self.iters = 0
        # 与 T11 的 κ 公式对账用：`xs.length()` / `xs.at(i)` 各算 1 次方法调用
        # （G26 暂定口径：1 次调用 = 1 次 κ；`at` 是循环体里最贵的那一项）
        self.method_calls = 0
        self._cap: Dict[int, int] = {}      # 容器 id → 当前容量（模拟扩容）
        self.fns: Dict[str, Node] = {}
        self.kernels: Dict[str, Node] = {}
        self.over: Dict[str, List[str]] = {}
        # **spec 索引**：`requires` 断言要在入口找到对应 spec。
        self.specs: Dict[str, Node] = {
            (c.name or ""): c
            for c in ((decl_mod.children if decl_mod else []))
            if c is not None and getattr(c, "kind", "") == "spec"
        }
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
                "evaluation_steps": self.steps,
                # **操作数**（本次 `call()` 的整棵调用树；计数关闭时恒为 0）
                "operations": self.ops,
                "operations_by_function": ops_by_fn(),
                "builtin_calls": dict(self.builtin_calls)}
    @property
    def last_candidates(self) -> Dict[str, str]:
        """**本线程**这一轮 `?!label` 实际走的候选。

        为什么 per-thread：`parallel { … } threads` 里所有任务共用同一个 `Interp`，
        而"选段"是按**各自实参规模**算的——线程 A 可能选 `by_iter`、线程 B 选 `by_index`。
        用一个全局槽会让后写的覆盖先写的，于是"报告说跑了 X、实际跑了 Y"。
        """
        return self._lc_slot

    @last_candidates.setter
    def last_candidates(self, val: Dict[str, str]) -> None:
        self._lc_slot = dict(val)

    def call(self, fn_name: str, args: List[Any],
             force: Optional[Dict[str, str]] = None) -> Any:
        fn = self.fns.get(fn_name)
        if fn is None:
            raise InterpError(f"找不到函数 {fn_name}")
        if not _COUNT:
            return self._run_fn(fn, args, force or {}, {})
        # 一次 `call()` = 一次测量窗口：`self.ops` 记这次调用树的操作数。
        _before = _total()
        try:
            return self._run_fn(fn, args, force or {}, {})
        finally:
            self.ops = _total() - _before

    def _check_requires(self, fn: Node, env: Dict[str, Any], force: Dict[str, str]) -> None:
        """断言 spec 里的 `requires`（若写了）。"""
        spec = self.specs.get(fn.name) if getattr(self, "specs", None) else None
        if spec is None:
            return
        for holder in spec.children:
            if getattr(holder, "kind", "") != "spec-body":
                continue
            for cl in holder.children:
                if getattr(cl, "kind", "") != "clause" or cl.name != "requires":
                    continue
                for expr in cl.children:
                    try:
                        ok = self._eval(expr, env, force)
                    except Exception as exc:                # noqa: BLE001
                        raise InterpError(
                            f"{fn.name} 的 requires 无法求值：{type(exc).__name__}: {exc}")
                    if not ok:
                        raise InterpError(f"{fn.name} 的 requires 不成立（前提被违反）")

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

        # **const 形参登记**（v0.72）：这一帧里这些名字不许被变更方法写
        _consts = set()
        for _p in (params.children if params else []):
            if isinstance(_p.value, dict) and _p.value.get("const"):
                _consts.add(_p.name)
        if not hasattr(self, "_const_stack"):
            self._const_stack = []
        self._const_stack.append(_consts)

        # **`requires` 在入口断言**（v0.71）。它是"调用方必须保证"的前提，
        # 不成立就立刻失败——**而不是让函数带着坏输入继续算**。
        self._check_requires(fn, env, force)

        block = next((c for c in fn.children if c.kind == "block"), None)
        if block is None:
            raise InterpError(f"{fn.name} 没有函数体（可能还是 pending）")
        fallible = bool((fn.value or {}).get("fallible"))
        # **进入函数记账、返回时结算**：`ops_of(f)` 于是是整棵调用树的量。
        _mark = _slot()["n"] if _COUNT else 0
        try:
            self._block(block, env, force)
            result = Ok(None) if fallible else None
        except _Return as r:
            if fallible:
                v = r.value
                result = v if isinstance(v, (Ok, Err)) else Ok(v)
            else:
                result = r.value
        finally:
            if _COUNT:
                _bump_fn(fn.name, _slot()["n"] - _mark)
            if getattr(self, "_const_stack", None):
                self._const_stack.pop()
        # **`guarantees` 在返回时断言**（v0.72）：可检查的那部分真的求值，
        # 不可检查的已在 `check` 里报 `E-Guarantee-Uncheckable`（不会到这里）。
        self._check_guarantees(fn, env, result, force)
        return result

    def _check_guarantees(self, fn: Node, env: Dict[str, Any], result: Any,
                          force: Dict[str, str]) -> None:
        """返回后校验 `guarantees`（只校验可判定的那部分）。"""
        import guarantees as _gt
        spec = self.specs.get(fn.name) if getattr(self, "specs", None) else None
        if spec is None or not _gt.has_guarantees(spec):
            return
        ok_exprs, _bad = _gt.classify(spec)
        if not ok_exprs:
            return
        e2 = dict(env)
        e2["ret"] = result
        e2["result"] = result
        for expr in ok_exprs:
            try:
                good = self._eval(expr, e2, force)
            except Exception as exc:                       # noqa: BLE001
                raise InterpError(
                    f"{fn.name} 的 guarantees 无法求值：{type(exc).__name__}: {exc}")
            if not good:
                raise InterpError(
                    f"E-Guarantee-Violated：{fn.name} 返回后不满足 "
                    f"{_gt.describe([expr])}")

    # ---------- 语句
    def _tick(self) -> None:
        self.steps += 1
        if self.steps > self.budget:
            raise InterpBudgetExceeded(
                f"执行超过 {self.budget} 步，疑似死循环（已执行 {self.steps} 步）")

    def _block(self, blk: Node, env: Dict[str, Any], force: Dict[str, str]) -> None:
        for st in blk.children:
            self._stmt(st, env, force)

    def _stmt(self, st: Node, env: Dict[str, Any], force: Dict[str, str]):
        if _COUNT:
            _bump()
        if getattr(st, "kind", None) == "assign-op":
            target = st.children[0]
            # **第三参是 `force`（dict），以前错传 `True`**（v0.72 修）
            val = self._eval(st.children[1], env, force)
            cur = self._eval(target, env, force)
            op = st.value or ""
            if op == "+=":
                new = _wrap(cur + val)
            elif op == "-=":
                new = _wrap(cur - val)
            elif op == "*=":
                new = _wrap(cur * val)
            elif op == "/=":
                new = cur // val
            elif op == "%=":
                new = cur % val
            else:
                raise InterpError(f"未知的复合赋值运算符：{op}")
            if target.kind == "path":
                env[target.name] = new
            elif target.kind == "index":
                base = self._eval(target.children[0], env, True)
                base[int(self._eval(target.children[1], env, True))] = new
            elif target.kind == "field":
                self._eval(target.children[0], env, True).fields[target.name] = new
            else:
                raise InterpError(f"复合赋值的左值不支持：{target.kind}")
            return None
        return self._core_stmt(st, env, force)

    def _core_stmt(self, st: Node, env: Dict[str, Any], force: Dict[str, str]) -> None:
        self._tick()
        k = st.kind

        _name = INTERP_STMT.get(k)
        if _name is not None:
            return getattr(self, _name)(st, env, force)
        raise InterpError(f"未实现的语句：{k}")


    def _k_stmt_const_decl(self, st, env, force):
        # children[0] 是类型，最后一个是初值
        _v = self._eval(st.children[-1], env, force)
        # **声明类型是"不可失败"时，把 `Ok(x)` 解包**：
        # `/` 与 `%` 在实现里返回 `Ok(...)`（为了表达除零），于是
        # `const u64 k = xs.at(i) % N;` 会把 `Ok(3)` 当成下标 → TypeError。
        # 类型已经声明成 `u64`（不带 `!`），说明调用方**没打算处理失败**，
        # 这里就应当解包；真出错（Err）才抛。
        _ty = st.children[0] if st.children else None
        _fallible = bool(_ty is not None and getattr(_ty, "kind", "") == "type-fail")
        if not _fallible and isinstance(_v, Ok):
            _v = _v.v
        elif not _fallible and isinstance(_v, Err):
            raise InterpError(f"{st.name} 的初值是 Err({_v.e})，但类型声明为不可失败")
        env[st.name] = _v
        return
    def _k_stmt_assign(self, st, env, force):
        val = self._eval(st.children[1], env, force)
        self._store(st.children[0], val, env, force)
        return
    def _k_stmt_expr_stmt(self, st, env, force):
        self._eval(st.children[0], env, force)
        return
    def _k_stmt_return(self, st, env, force):
        if st.children:
            raise _Return(self._eval(st.children[0], env, force))
        raise _Return(None)
    def _k_stmt_if(self, st, env, force):
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
    def _k_stmt_parallel(self, st, env, force):
        body = next((c for c in st.children if c.kind == "block"), None)
        if body is None:
            return
        tasks = [c for c in body.children if c.kind == "block"]
        if not tasks:
            return
        if self.parallel_mode != "threads" or len(tasks) == 1:
            for task in tasks:
                self._block(task, env, force)
            return
        # **真线程**：每个任务一份**自己的绑定表**，跑完把"它改过的变量"合并回来。
        # 任务之间不许共享可变状态（检查器已保证），所以浅拷贝绑定表就够——
        # 共享的容器只读，写只发生在各自的新绑定上。
        import concurrent.futures as _cf
        import threading as _th

        results: List[Dict[str, Any]] = [{} for _ in tasks]
        errors: List[BaseException] = []

        def _run(i: int, task: Node) -> None:
            local = dict(env)
            # 每个任务一条**独立且确定**的随机流：由 (基种子, 任务下标) 导出，
            # 于是同种子同程序 → 同结果，与调度无关。
            stdlib.set_task_stream(self.rand_seed, i)
            try:
                self._block(task, local, force)
            except BaseException as exc:            # noqa: BLE001
                errors.append(exc)
                return
            for kk, vv in local.items():
                if kk in env and vv is not env[kk]:
                    results[i][kk] = vv
            stdlib.clear_task_stream()

        with _cf.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futs = [pool.submit(_run, i, tsk) for i, tsk in enumerate(tasks)]
            for f in futs:
                f.result()
        if errors:
            raise errors[0]
        for piece in results:
            env.update(piece)
        return
    def _k_stmt_while(self, st, env, force):
        cond, body = st.children[0], next(
            (c for c in st.children[1:] if c.kind == "block"), None)
        while self._eval(cond, env, force):
            self._tick()
            self.iters += 1
            if body is not None:
                try:
                    self._block(body, env, force)
                except _Continue:
                    continue
                except _Break:
                    break
        return
    def _k_stmt_break(self, st, env, force):
        raise _Break()

    def _k_stmt_continue(self, st, env, force):
        raise _Continue()

    def _k_stmt_for(self, st, env, force):
        body = next((c for c in st.children if c.kind == "block"), None)
        var = st.name
        base = next((c for c in st.children if c.kind != "block"), None)
        # **区间 `for (i in a..b)`**（v0.72）：半开 `[a, b)`。
        # 以前这里只找 `kind=="path"`，区间是 `binary("..")`，于是**静默 0 次**。
        if base is not None and getattr(base, "kind", "") == "binary" \
                and (base.value or "") == ".." and len(base.children) >= 2:
            lo = int(self._eval(base.children[0], env, force))
            hi = int(self._eval(base.children[1], env, force))
            seq = list(range(lo, hi))
        else:
            seq = self._eval(base, env, force) if base is not None else []
            if not isinstance(seq, list):
                seq = list(seq) if seq is not None else []
        for item in seq:
            self._tick()
            self.iters += 1
            env[var] = item
            if body is not None:
                try:
                    self._block(body, env, force)
                except _Continue:
                    continue
                except _Break:
                    break
        return
    def _k_stmt_match(self, st, env, force):
        return self._match(st, env, force)
    def _k_stmt_block(self, st, env, force):
        self._block(st, env, force)
        return
    def _k_stmt_dispatch(self, st, env, force):
        self._eval(st, env, force)
        return


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
            if isinstance(base, StructVal):
                # `p.x = 5`（v0.72）：以前只有 `p.x += 5` 能走，`=` 报"未实现"
                base.fields[target.name] = val
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
                        ("pattern", "pattern-wildcard", "pattern-literal")), None)
            # 臂的体：可能是 block，也可能是一条单语句（`Pat => return 1,`）
            body = next((c for c in arm.children if c.kind == "block"), None)
            single = [c for c in arm.children
                      if c.kind not in ("pattern", "pattern-wildcard",
                                        "pattern-literal", "block")]
            if pat is None:
                continue
            hit = False
            if pat.kind == "pattern-wildcard":
                hit = True
            elif pat.kind == "pattern-literal":
                _lit = str(pat.value or "").strip()
                _want = True if _lit == "true" else (False if _lit == "false" else None)
                if _want is not None:
                    hit = bool(val) == _want
                else:
                    hit = (val == int(_lit)) if isinstance(val, int) else False
            else:
                want = (pat.name or "").split(".")[-1]
                if isinstance(val, EnumVal) and val.variant == want:
                    hit = True
                    binds = [c for c in pat.children if c.kind == "binding"]
                    if binds:
                        self._bind_pattern(binds[0], val.payload, env)
                elif isinstance(val, Ok) and want == "Ok":
                    # **`Ok(v)` / `Err(e)` 也是对 Result 的匹配**（v0.71）：
                    # 它们不是 EnumVal，要单独认，否则 `match` 会静默跳过所有臂。
                    hit = True
                    binds = [c for c in pat.children if c.kind == "binding"]
                    if binds:
                        self._bind_pattern(binds[0], val.v, env)
                elif isinstance(val, Err) and want == "Err":
                    hit = True
                    binds = [c for c in pat.children if c.kind == "binding"]
                    if binds:
                        self._bind_pattern(binds[0], val.e, env)
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
    def _eval(self, n: Optional[Node], env, force):
        if _COUNT and n is not None:
            _bump()
        _k = getattr(n, "kind", None)
        if _k == "lit-float":
            return float(n.value if n.value is not None else 0.0)
        if _k == "array":
            return [self._eval(c, env, force) for c in n.children]
        if _k == "deref":
            return self._eval(n.children[0], env, force)
        return self._core_eval(n, env, force)

    def _core_eval(self, n: Optional[Node], env, force) -> Any:
        if n is None:
            return None
        self._tick()
        k = n.kind

        _name = INTERP_EXPR.get(k)
        if _name is not None:
            return getattr(self, _name)(n, env, force)
        raise InterpError(f"未实现的表达式：{k}")


    def _k_expr_lit_int(self, n, env, force):
        return int(n.value)
    def _k_expr_lit_string(self, n, env, force):
        return Text(n.value or b"")
    def _k_expr_lit_char(self, n, env, force):
        t = str(n.value)
        return ord(t[1]) if len(t) >= 3 else ord(t)
    def _k_expr_lit_float(self, n, env, force):
        # **浮点字面量也要有值**：解析器能产（`parser.py:1526`），
        # 成本域早就按 1 个操作计，具体值域却抛「未实现的表达式」——
        # 这就是 `walk.py` 报的那条 expr 侧漂移。语义上仍由类型检查器
        # 拦截（`u64 y = 1.5;` → `E-Type-FloatToInt`），这里只是不再崩。
        return float(n.value)
    def _k_expr_lit_bool(self, n, env, force):
        return n.value in (True, "true")
    def _k_expr_path(self, n, env, force):
        if n.name in env:
            return env[n.name]
        # `Token::Plus` → path "Token.Plus"：类型命名空间里的变体（无载荷）
        if "." in (n.name or ""):
            ty, va = n.name.rsplit(".", 1)
            return EnumVal(ty, va)
        raise InterpError(f"未绑定的名字 {n.name}")
    def _k_expr_paren(self, n, env, force):
        return self._eval(n.children[0], env, force)
    def _k_expr_tuple(self, n, env, force):
        return tuple(self._eval(c, env, force) for c in n.children)
    def _k_expr_struct_literal(self, n, env, force):
        fields = {}
        for fi in n.children:
            if fi.kind == "field-init" and fi.children:
                fields[fi.name] = self._eval(fi.children[0], env, force)
        return StructVal(n.name or "", fields)
    def _k_expr_unary(self, n, env, force):
        v = self._eval(n.children[0], env, force)
        op = n.value or ""
        if op == "not":
            return not v
        if op == "-":
            return _wrap(-v)
        raise InterpError(f"未实现的一元运算 {op}")
    def _k_expr_binary(self, n, env, force):
        return self._binary(n, env, force)
    def _k_expr_call(self, n, env, force):
        return self._call(n, env, force)
    def _k_expr_method_call(self, n, env, force):
        return self._method(n, env, force)
    def _k_expr_field(self, n, env, force):
        return self._field(n, env, force)
    def _k_expr_cast(self, n, env, force):
        v = self._eval(n.children[0], env, force)
        v = int(v)
        # **`as` 要真的截断**（v0.72）：`300 as u8` 以前返回 300。
        ty = next((c for c in n.children[1:] if getattr(c, "kind", "").startswith("type")),
                  None)
        name = ""
        if ty is not None:
            name = getattr(ty, "name", None) or (
                ty.children[0].name if getattr(ty, "children", None) else "") or ""
        widths = {"u8": (8, False), "u16": (16, False), "u32": (32, False),
                  "u64": (64, False), "i8": (8, True), "i16": (16, True),
                  "i32": (32, True), "i64": (64, True)}
        if name in widths:
            w, signed = widths[name]
            m = 1 << w
            v %= m
            if signed and v >= (m >> 1):
                v -= m
        return v
    def _k_expr_try(self, n, env, force):
        v = self._eval(n.children[0], env, force)
        if isinstance(v, Err):
            raise _Return(v)
        if isinstance(v, Ok):
            return v.v
        return v
    def _k_expr_dispatch(self, n, env, force):
        return self._dispatch(n, env, force)
    def _k_expr_index(self, n, env, force):
        base = self._eval(n.children[0], env, force)
        idx = self._eval(n.children[1], env, force)
        return base[idx]
    def _k_expr_type_name(self, n, env, force):
        return TypeRef(n.name or "")
    def _k_expr_ref(self, n, env, force):
        # `&x` 只是"按引用传"的标记，值就是它指的东西
        return self._eval(n.children[0], env, force) if n.children else None


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
            # **除零由 `requires b != 0` 负责**（规格），不是让 `/` 返回 Result。
            # 以前返回 `Ok(q)`，于是 `return a/b` 得到 `Ok(3)`、`a%2==0` 恒假。
            if b == 0:
                raise InterpError("E-Arith-DivZero：除数为 0（请用 `requires b != 0;` 约束）")
            q = abs(a) // abs(b)
            return q if (a >= 0) == (b >= 0) else -q
        if op == "%":
            if b == 0:
                raise InterpError("E-Arith-DivZero：取模的除数为 0（请用 `requires b != 0;` 约束）")
            r = abs(a) % abs(b)
            return r if a >= 0 else -r
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

    # ---------------------------------------------------------------- FFI（第一批）
    def _ffi_prepare(self, name: str, node, args):
        """调用前的**形状校验**（与后端无关，见 shape.py）。"""
        from checker import param_list
        from shape import ShapeError, UnsupportedError, validate
        try:
            return validate(name, param_list(node), args)
        except (ShapeError, UnsupportedError) as exc:
            raise InterpError(str(exc))

    def _load_ffi(self, name: str):
        """把 `extern` 声明变成一个 ctypes 可调用对象（按需加载、缓存）。"""
        import ctypes
        decl = self.externs.get(name)
        if decl is None:
            return None
        if name in self._ffi_cache:
            return self._ffi_cache[name]
        lib_name = (decl.value or {}).get("lib") if isinstance(decl.value, dict) else None
        try:
            lib = ctypes.CDLL(lib_name) if lib_name else ctypes.CDLL(None)
        except OSError as exc:
            raise InterpError(f"加载外部库失败（{lib_name or '进程自身'}）：{exc}")
        fn = getattr(lib, name, None)
        if fn is None:
            raise InterpError(f"外部库 {lib_name or '进程自身'} 里没有符号 {name}")
        # 参数类型：u64/i64 → c_uint64/c_int64；Vec<u8> → c_char_p
        ps = next((c for c in decl.children if c.kind == "params"), None)
        argtypes = []
        for p in (ps.children if ps else []):
            ty = next((c for c in p.children if c.kind in ("type-ref", "type-name")), None)
            ts = ""
            if ty is not None:
                nm = getattr(ty, "name", None) or (ty.children[0].name if ty.children else "")
                if nm == "Vec":
                    ts = "Vec"
                else:
                    ts = nm
            from ffi_c import argtype_for
            argtypes.append(argtype_for("Vec" if ts == "Vec" else ts))
        fn.argtypes = argtypes or None
        rt = next((c for c in decl.children if c.kind in ("type-ref", "type-name")), None)
        rts = ""
        if rt is not None:
            rts = getattr(rt, "name", None) or (rt.children[0].name if rt.children else "")
        from ffi_c import restype_for
        fn.restype = restype_for(rts)
        self._ffi_cache[name] = fn
        return fn

    def _call_ffi_spec(self, name: str, args) -> Any:
        """按 `.tu` 里那条 spec 的签名调用外部符号（`external "lib";`）。"""
        import ctypes
        spec = self.specs.get(name) if hasattr(self, "specs") else None
        if spec is None:
            raise InterpError(f"{name} 没有 spec，无法按签名调用")
        # **校验必须在加载库之前**：否则参数错会先撞上 `ctypes.CDLL` 的报错，
        # 而且测试还得依赖一个真实的外部库。
        args = self._ffi_prepare(name, spec, args)
        lib_name = self.extern_specs.get(name) or getattr(self, "link_lib", None)
        try:
            lib = ctypes.CDLL(lib_name) if lib_name else ctypes.CDLL(None)
        except OSError as exc:
            raise InterpError(f"加载外部库失败（{lib_name or '进程自身'}）：{exc}")
        fn = getattr(lib, name, None)
        if fn is None:
            raise InterpError(f"外部库 {lib_name or '进程自身'} 里没有符号 {name}")
        ps = next((c for c in spec.children if c.kind == "params"), None)
        from ffi_c import argtype_for, to_native
        argtypes = []
        for p in (ps.children if ps else []):
            ty = next((c for c in p.children if c.kind in ("type-ref", "type-name")), None)
            argtypes.append(argtype_for(self._type_name(ty)))
        fn.argtypes = argtypes or None
        rt = next((c for c in spec.children if c.kind in ("type-ref", "type-name")), None)
        rts = self._type_name(rt)
        fn.restype = None if not rts else (ctypes.c_int64 if rts == "i64" else ctypes.c_uint64)
        return fn(*to_native(args))

    @staticmethod
    def _type_name(n) -> str:
        if n is None:
            return ""
        if getattr(n, "kind", "") == "type-ref":
            inner = next((c for c in n.children if c.kind in ("type-name", "type-ref")), None)
            return Interp._type_name(inner)
        if getattr(n, "kind", "") == "type-name":
            args = next((c for c in n.children if c.kind == "type-args"), None)
            base = n.name or ""
            if args is None:
                return base
            return base + "<" + ",".join(Interp._type_name(a) for a in args.children) + ">"
        return getattr(n, "name", "") or ""

    def _call_ffi(self, name: str, args) -> Any:
        decl = self.externs.get(name)
        if decl is not None:
            args = self._ffi_prepare(name, decl, args)
        fn = self._load_ffi(name)
        if fn is None:
            raise InterpError(f"未声明的外部函数 {name}")
        from ffi_c import to_native
        return fn(*to_native(args))

    def _call(self, n: Node, env, force) -> Any:
        # **调用函数值**：`g(1, 2)`，其中 `g` 是 `func(...)->R` 形参。
        # 必须放在最前面：否则会走"未定义的函数 g"。
        if n.children and n.children[0].kind == "path":
            _cv = env.get(n.children[0].name)
            if isinstance(_cv, FnVal):
                _vals = self._args(n, env, force)
                _fn = self.fns.get(_cv.name)
                if _fn is None:
                    raise InterpError(f"找不到函数 {_cv.name}")
                return self._run_fn(_fn, _vals, force, {})

        # **FFI 分派**：声明的 `extern` 名字优先走 ctypes（不是语言函数）
        _short = (n.name or "").split(".")[-1]
        if _short in self.externs:
            _args_node = next((c for c in n.children if c.kind == "args"), None)
            _vals = [self._eval(a, env, force) for a in (_args_node.children if _args_node else [])]
            return self._call_ffi(_short, _vals)
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
        # **`for_each(m, f)`**：遍历 HashMap，对每个 (k, v) 调 f。
        # **快照**遍历：回调里改容器不影响本次（slice 1 的语义，见计划 §2.4）。
        if name == "for_each":
            _an = next((c for c in n.children if c.kind == "args"), None)
            _items = _an.children if _an else []
            if len(_items) < 2:
                raise InterpError("for_each 需要 2 个实参（容器、具名函数）")
            _m = self._eval(_items[0], env, force)
            _fnode = _items[1]
            _fname = _fnode.name if getattr(_fnode, "kind", "") == "path" else None
            _fn = self.fns.get(_fname or "")
            if _fn is None:
                raise InterpError("for_each 的第二个实参必须是具名函数")
            # **Vec / FixedVec：一元回调**（v0.73）。以前只认 HashMap，
            # 于是文档里的 `for_each(xs, f)` 在 Vec 上直接报错。
            if isinstance(_m, list):
                _snap = list(_m)
                for _x in _snap:
                    self._run_fn(_fn, [_x], force, {})
                if _COUNT:
                    _bump()
                return len(_snap)
            if not isinstance(_m, stdlib.MapVal):
                raise InterpError("for_each 的第一个实参必须是 Vec 或 HashMap")
            _pairs = list(_m.items())          # 快照
            for _k, _v in _pairs:
                self._run_fn(_fn, [_k, _v], force, {})
            if _COUNT:
                _bump()
            return len(_pairs)

        # **`format` 是变参**：不能被 stdlib 的固定 arity 拦住
        if name == "format":
            args = self._args(n, env, force)
            self.builtin_calls[name] = self.builtin_calls.get(name, 0) + 1
            if _COUNT:
                _bump()
            return stdlib.FNS["format"]["impl"](args)

        # `sort(xs)` / `sort_by(xs, 具名比较器)` —— **必须在求值实参之前拦**：
        # 第二个实参是比较器的**名字**，不是值（本语言没有一等函数，
        # 直接 _eval 会报「未绑定的名字」）。
        # **spec 声明了外部实现**：按 spec 的参数类型走 ctypes
        _nm = name or ""
        _is_ext = _nm in getattr(self, "extern_specs", {}) or (
            _nm in getattr(self, "_spec_names", set()) and _nm not in self.fns)
        if _is_ext:
            _args_node = next((c for c in n.children if c.kind == "args"), None)
            _vals = [self._eval(a, env, force) for a in (_args_node.children if _args_node else [])]
            return self._call_ffi_spec(name or "", _vals)

        if name in ("sort", "sort_by"):
            if _COUNT:
                _bump()
            return self._builtin_sort(name, n, env, force)

        # **高阶内建**：第二个（fold 是第三个）实参是**具名函数**，不是值。
        # 和 `sort_by` 同一套理由：**具名函数是有限集合，成本可静态分析**；
        # 匿名函数是任意代码，`κ` 无法界定，契约会被打穿。
        if name in ("map", "filter", "fold", "count_if"):
            if _COUNT:
                _bump()
            return self._builtin_higher(name, n, env, force)

        args = self._args(n, env, force)

        if name in BUILTINS_INT2:
            if _COUNT:
                _bump()
            return BUILTINS_INT2[name](args)

        if name in BUILTINS_INT1:
            self.builtin_calls[name] = self.builtin_calls.get(name, 0) + 1
            if _COUNT:
                _bump()
            return BUILTINS_INT1[name](args)

        # **最小基本库（L1）**：容器/算法内建，见 stdlib.py 的收编标准。
        if stdlib.is_stdlib(name):
            self.builtin_calls[name] = self.builtin_calls.get(name, 0) + 1
            if _COUNT:
                _bump()
            return stdlib.call(name, args)

        if name == "Err":
            return Err(args[0] if args else None)
        if name == "Ok":
            return Ok(args[0] if args else None)

        fn = self.fns.get(name)
        if fn is None:
            raise InterpError(f"调用了未定义的函数 {name}")
        return self._run_fn(fn, args, force, {})

    def _builtin_higher(self, name: str, n: Node, env, force) -> Any:
        """`map(xs, f)` / `filter(xs, p)` / `count_if(xs, p)` / `fold(xs, init, f)`。

        **第二个（fold 是第三个）实参 MUST 是具名函数**——理由同 `sort_by`：
        **没有一等函数，就没有"任意代码"这种成本黑洞**。
        """
        args_node = next((c for c in n.children if c.kind == "args"), None)
        items = args_node.children if args_node is not None else []
        if len(items) < 2:
            raise InterpError(f"{name} 至少 2 个实参：容器 + 具名函数")

        recv = self._eval(items[0], env, force)
        if not isinstance(recv, list):
            raise InterpError(f"{name} 的第一个实参必须是容器，给的是 {type(recv).__name__}")

        cb_node = items[2] if (name == "fold" and len(items) >= 3) else items[1]
        if cb_node.kind != "path":
            raise InterpError(
                f"{name} 的函数实参 MUST 是具名函数（给的是 {cb_node.kind}）——"
                f"匿名函数是任意代码，成本不可静态界定")
        cb_name = cb_node.name or ""
        cb = self.fns.get(cb_name)
        if cb is None:
            raise InterpError(f"{name} 的函数实参 {cb_name} 不是本模块的函数")

        if name == "fold":
            if len(items) < 3:
                raise InterpError("fold 要 3 个实参：容器 + 初值 + 具名函数")
            acc = self._eval(items[1], env, force)
            for x in recv:
                acc = self._run_fn(cb, [acc, x], force, {})
            return acc

        if name == "count_if":
            n_true = 0
            for x in recv:
                if self._run_fn(cb, [x], force, {}) not in (False, 0, None):
                    n_true += 1
            return n_true

        if name == "filter":
            return [x for x in recv if self._run_fn(cb, [x], force, {}) not in (False, 0, None)]

        # map
        return [self._run_fn(cb, [x], force, {}) for x in recv]

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

    def _arg_value(self, a: Node, env, force) -> Any:
        # **函数值**：`for_each(m, visit)` —— `visit` 是**名字**，不是值。
        # 只有"没被局部变量遮蔽"时才当函数值，避免踩掉普通变量。
        if a.kind == "path" and (a.name or "") not in env \
                and (a.name or "") in self.fns:
            return FnVal(a.name)
        return self._eval(a, env, force)

    def _args(self, n: Node, env, force) -> List[Any]:
        """求实参；**具名实参按名字绑定**（v0.72）。

        以前 `named-arg` 只取里面的表达式、名字被丢掉——于是 `g(b = 1, a = 2)`
        按位置绑定成 `a=1, b=2`，得到 12 而不是 21。**静默算错**是最坏的一类。
        """
        args_node = next((c for c in n.children if c.kind == "args"), None)
        items = list(args_node.children) if args_node is not None else []
        named = [a for a in items if a.kind == "named-arg"]
        if not named:
            return [self._arg_value(a, env, force) for a in items]

        callee = (n.name or "").split(".")[-1]
        fn = self.fns.get(callee)
        pnames = []
        if fn is not None:
            ps = next((c for c in fn.children if c.kind == "params"), None)
            pnames = [p.name for p in (ps.children if ps else [])]
        vals = {}
        for a in items:
            if a.kind != "named-arg":
                raise InterpError(f"{callee}：具名实参与位置实参不能混用")
            if a.name in vals:
                raise InterpError(f"{callee}：实参 {a.name} 重复给了")
            if pnames and a.name not in pnames:
                raise InterpError(f"{callee} 没有名为 {a.name} 的参数"
                                  f"（有：{', '.join(pnames)}）")
            kid = a.children[0] if a.children else None
            if kid is None:
                raise InterpError(f"{callee}：实参 {a.name} 没有值")
            vals[a.name] = self._arg_value(kid, env, force)
        if pnames:
            missing = [p for p in pnames if p not in vals]
            if missing:
                raise InterpError(f"{callee} 缺少实参：{', '.join(missing)}")
            return [vals[p] for p in pnames]
        return list(vals.values())

    def _method(self, n: Node, env, force) -> Any:
        # **`const T&` 形参不许被变更方法写**（v0.72）。
        # 以前 `z(const Vec<u64>& xs) { xs.push(0); }` 会改掉调用方的 list。
        _recv = n.children[0] if n.children else None
        _mname = (n.name or "").split(".")[-1]
        _consts = self._const_stack[-1] if getattr(self, "_const_stack", None) else set()
        if _recv is not None and _recv.kind == "path" and _recv.name in _consts \
                and _mname in MUTATING_METHODS:
            raise InterpError(
                f"E-Borrow-Const：{_recv.name} 是 `const` 形参，不能调 `. {_mname}(…)`"
                f"（要改就把它声明成可变借用 `T&`）")
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
        if _COUNT:
            _bump()

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
            # **先查表**（方案甲，2026-10-30）：按当前实参的规模选段。
            # 表不存在 → 走原来的"第一个实现"，并留一条警告（不静默）。
            _size = _size_of_args(args)
            _segs = self.tables.get(label) or []
            _seg = None
            if _segs and _size is not None:
                for _lo, _hi, _who in _segs:
                    if _lo <= _size <= _hi:
                        _seg = _who
                        break
            cand = None
            if _seg and _seg in cands and any(c.kind == "block" for c in cands[_seg].children):
                cand = cands[_seg]
            elif _segs and _size is not None:
                _msg = (f"?!{label}: 规模 {_size} 不在分档表范围内"
                        f"（表覆盖 {_segs[0][0]}..{_segs[-1][1]}），退化为第一个实现")
                if _msg not in self.dispatch_warnings:
                    self.dispatch_warnings.append(_msg)
                    print("  ！ " + _msg, file=sys.stderr)
            # **P0 修复**：表已经选好候选时，绝不能被 `over` 的顺序循环覆盖。
            # 旧写法无条件走下面的循环，实际执行 `over` 里第一个有实现的候选，
            # 而 `last_candidates` 记的是表选的那个 —— 报告与实际不一致。
            if cand is None:
                order = self.over.get(label) or list(cands)
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
        # **唯一赋值点**：报告的就是要跑的
        self.last_candidates[label] = cand.name or ""

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
            # **常量表达式**（v0.72）：`FixedVec<u64, N + 1>` / `1 + 2`。
            # 以前这里只认字面量与常量名，表达式一律返回 None →
            # `FixedVec` 静默退化成普通 `Vec`，容量检查形同虚设。
            _v = _const_expr_int(a, consts)
            if _v is not None:
                return _v
    return None


def _const_expr_int(n, consts: Dict[str, Any]) -> Optional[int]:
    """把类型实参里的**常量表达式**求成 int（只认 + - * / 与常量名/字面量）。"""
    if n is None:
        return None
    k = getattr(n, "kind", "")
    if k in ("lit-int", "type-const"):
        try:
            return int(n.value)
        except (TypeError, ValueError):
            return None
    if k == "type-name" and (n.name or "").isdigit():
        return int(n.name)
    if k == "path":
        v = consts.get(n.name)
        return v if isinstance(v, int) else None
    if k == "binary" and len(n.children) >= 2:
        l = _const_expr_int(n.children[0], consts)
        r = _const_expr_int(n.children[1], consts)
        if l is None or r is None:
            return None
        op = n.value or ""
        if op == "+":
            return l + r
        if op == "-":
            return l - r
        if op == "*":
            return l * r
        if op == "/" and r:
            return l // r
        return None
    if k in ("paren", "ref") and n.children:
        return _const_expr_int(n.children[0], consts)
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