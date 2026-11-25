# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""version.py —— 工具链版本号的**唯一来源**。

## 为什么单独一个文件（2026-11-22）

`dsh.py` 里原来写死 `VERSION = "1.0.0"`。于是发 1.0.1 的时候：

    pyproject.toml     1.0.1     ← 打包用的
    elench version     1.0.0     ← 用户看到的
    pip show elench    0.69.0    ← 装出来的元数据

**三个地方三个数**。`tools/release_check.py` 因此把这一处也纳进来比：
改 `pyproject.toml` 却忘了这里，门禁直接红。

为什么不用 `importlib.metadata.version("elench")` 读：`src/` 也会被 npm 包原样带走，
而且浏览器里的 Pyodide 没有 dist-info——**必须有字面量兜底**。既然必须有，就让它只有一份。
"""

#: 与 `pyproject.toml` 的 `version` 保持一致（`tools/release_check.py` 会核对）。
VERSION = "1.0.2"
