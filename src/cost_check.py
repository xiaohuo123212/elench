# -*- coding: utf-8 -*-
"""兼容层：真正的实现在 `cost_domain.cost_check`（2026-11-01 合并）。

**为什么用 `sys.modules` 替换，而不是把名字 `import *` 过来**：
`src/test_ops.py` 会 monkeypatch `cost_check.OP_COST` 造反例。
只有把本模块对象**换成真模块**，patch 才落在真正被读的那个全局上；
复制名字会让 patch 打在副本上，测试就变成"测了个寂寞"。
"""
import sys as _sys

from cost_domain import cost_check as _real

_sys.modules[__name__] = _real
