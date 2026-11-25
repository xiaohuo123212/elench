#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_packaging.py —— `pyproject.toml` 的 `py-modules` 必须覆盖 `src/` 的全部模块。

## 为什么必须有这条测试（2026-11-18 审计发现）

`pyproject.toml` 用 `py-modules = [...]` **逐个列出**要装进 wheel 的模块。
它漏了 4 个：`cost_artifact` / `diag_json` / `html_report` / `typenames`。

后果不是"少个可选功能"，是**装出来的包直接不能用**：

    pip install elench
    elench check a.eln
    → ModuleNotFoundError: No module named 'typenames'

因为 `src/checker.py` 在**模块级**就 `from typenames import canon, display`，
而 `dsh.py` 一上来就 import checker。所以任何一条命令都会死。

这个 bug 在开发机上**看不见**：本机 site-packages 里有一条指向别的 checkout 的路径，
`typenames` 会从那里被找到（而且版本不对，表现为莫名其妙的 `E-Internal-TypeCheck`）。
只有干净环境才暴露。

## 这条测试查什么

1. `src/*.py` 的每个模块都在 `py-modules` 里；
2. `py-modules` 里没有 `src/` 里不存在的名字（清单过期同样有害：`pip install` 会静默跳过）；
3. `packages = [...]` 里列的每个包目录真实存在（`cost_domain`）。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
PYPROJECT = os.path.join(ROOT, "pyproject.toml")


def _load():
    import tomllib

    with open(PYPROJECT, "rb") as f:
        return tomllib.load(f)


def main() -> int:
    cfg = _load()["tool"]["setuptools"]
    listed = set(cfg.get("py-modules", []))
    packages = list(cfg.get("packages", []))

    on_disk = {f[:-3] for f in os.listdir(SRC) if f.endswith(".py")}

    missing = sorted(on_disk - listed)      # 入库了但不会被打包 → 装完就 ImportError
    stale = sorted(listed - on_disk)        # 清单里有但源码没了 → 清单过期
    bad_pkg = [p for p in packages if not os.path.isdir(os.path.join(SRC, p))]

    problems = []
    if missing:
        problems.append("py-modules 漏了 %d 个模块（装完会 ModuleNotFoundError）：%s"
                        % (len(missing), ", ".join(missing)))
    if stale:
        problems.append("py-modules 里有 %d 个源码已不存在的名字（清单过期）：%s"
                        % (len(stale), ", ".join(stale)))
    if bad_pkg:
        problems.append("packages 里列了不存在的包目录：%s" % ", ".join(bad_pkg))

    if problems:
        for p in problems:
            print("FAIL  " + p)
        print("      修法：把 `src/` 里实际的模块补进/清理 `pyproject.toml` 的 [tool.setuptools]")
        return 1

    print("check  ok    py-modules 覆盖 src/ 全部 %d 个模块；packages 存在：%s"
          % (len(on_disk), ", ".join(packages) or "(无)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
