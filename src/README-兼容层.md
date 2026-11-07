# `src/` 顶层的四个"转发 shim"是**有意保留**的

`bound.py` / `cost_check.py` / `cost_interp.py` / `symbolic.py` 各约 350 B，
内容是：

```python
import sys as _sys
from cost_domain import cost_interp as _real
_sys.modules[__name__] = _real
```

真正的实现在 `src/cost_domain/`，`cost_domain/` 内部用**相对导入**（`.bound` / `.symbolic`），
是自洽的。这四个文件只做一件事：让**外部调用方**继续 `from cost_interp import CostInterp`。

## 为什么不用 `from ... import *`

因为它会把名字复制一份到 shim 模块里。而 `tests/cases/test_ops.py` 需要
**monkeypatch `cost_interp.OP_COST`** —— 只有把 `sys.modules[__name__]` 换成真模块，
patch 才落在真正被读的那个全局上。复制名字会让"测了个寂寞"。

## 什么时候可以删

当所有调用方（`src/`、`tools/`、`tests/`、`editors/vscode/python/`）都改成
`from cost_domain.cost_interp import ...`，且 monkeypatch 也改成指向
`cost_domain.cost_interp` 时。**代价约 200 处 import**，收益 1.4 KB —— 2026-11-07
审计的结论是：**不值得**，保留并在此说明。
