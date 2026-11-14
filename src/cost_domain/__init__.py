# -*- coding: utf-8 -*-
"""成本域（符号值域）——**一个包，六个子模块**。

| 模块 | 职责 |
|---|---|
| `symbolic.py` | `Cost` 代数：加法/乘法/max/幂（含符号指数），以及打印成 `n(xs)` 这样的符号式 |
| `bound.py` | 循环与表达式上界：`bound_of_while` / `substitute` / `progresses` |
| `cost_check.py` | 把符号式**代入具体规模**求值（`evaluate`）、与 `target` 比较 |
| `logdom.py` | **log 域判阶**：`to_log`，让 `n^n` / `2^n` / `n^10` 能分开（2026-11-12） |
| `cost_interp.py` | AST 走一遍得出 κ（带分档表、递归深度、函数值绑定） |
| `par.py` | `parallel` 的**最大核视图**与**大小核墙钟估计**（`wall_est`，**不是契约**） |

## 为什么合成一个包，而不是一个 4000 行的单文件

1. **一个目录 = 一个"成本域"**：以后问「κ 在哪算的」，答案是一个地方，不是四份清单；
2. **子模块之间用相对导入**（`from .symbolic import Cost`），
   绕开兼容层 → 避免"shim 反向依赖包"的循环；
3. **不合并 `interp.py`**：它是**可信测量基线**（ops 计数器），
   和 κ 的实现放在同一个文件里，等于让裁判和被告住一间屋子——
   "两边一起错"正是检测器唯一的盲点。

## 为什么 `par.py` 是**独立**的一个模块（2026-11-09）

因为它是**报告层**，不是**契约层**：

    κ（判阶 max / 总量 Σ）   → 机器无关，可冻结进 golden   → symbolic / cost_interp
    wall_est（依赖核数与权重）→ **机器相关**，MUST NOT 进 κ → par

把 `wall_est` 放进 `cost_check` 会让"承诺"和"估计"住进同一个模块，
下次有人顺手用它判契约，κ 就悄悄依赖机器了。

## 兼容层

`src/symbolic.py` / `src/cost_check.py` / `src/bound.py` / `src/cost_interp.py`
仍然存在，内容是 `sys.modules[__name__] = 真模块`，
所以既有 150+ 处 `from cost_interp import CostInterp` 不用改，
`test_ops` 对 `cost_interp.OP_COST` 的 monkeypatch 也仍然打在真对象上。
`par.py` 是 2026-11-09 新增的，**没有**兼容层：新代码直接
`from cost_domain import par`。
"""
from . import bound, cost_check, cost_interp, symbolic  # noqa: F401
from . import logdom  # noqa: F401

from . import par  # noqa: F401  （放在后面：它 import 上面几个）

from .cost_interp import CostInterp  # noqa: F401
from .symbolic import Cost  # noqa: F401

__all__ = ["Cost", "CostInterp", "symbolic", "bound", "cost_check",
           "cost_interp", "logdom", "par"]
