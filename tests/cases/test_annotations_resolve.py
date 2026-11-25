#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_annotations_resolve.py —— 每个模块的**类型注解**都必须能在运行期求值。

## 为什么要有这条测试（2026-11-19，Pyodide 探针挖出来的）

`src/cost_domain/cost_interp.py` 缺 `from __future__ import annotations`，
`typing` 的 import 里也没有 `Any`，却有这样一行：

    def effect_attribution(self, fn_name: str) -> Dict[str, Any]:

在 **Python ≤ 3.13** 上，`def` 执行时注解就要被求值 ⇒ **import 这个模块直接
`NameError: name 'Any' is not defined`**。

而本机是 **Python 3.14**（PEP 649：注解惰性求值），所以**看不见**这个问题。
直到把同一份代码放进 Pyodide（CPython 3.12）才暴露出来——那意味着
`requires-python = ">=3.10"` 这个声明在 3.10–3.13 上**是假的**。

## 这条测试为什么在任何版本上都能抓

即使在 3.14 上，**主动访问** `fn.__annotations__`（或 `typing.get_type_hints`）
依然会在函数的全局命名空间里求值注解，于是同样抛 `NameError`。
所以不需要装多版本 Python，只要把每个模块里所有函数/类的注解读一遍。

**顺带抓的另一类**：注解里写了已经被删掉的类型名（重构后忘了改）。
"""
import importlib
import os
import sys
import traceback
import types

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")

#: 这些模块 import 时有副作用或依赖命令行，跳过（但不静默：在输出里点名）。
SKIP = {"elench"}          # 只是 `from dsh import main` 的转接层，没有注解内容


def module_names():
    out = []
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in sorted(filenames):
            if not f.endswith(".py") or f.startswith("__"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, f), SRC)
            name = rel[:-3].replace(os.sep, ".")
            if name in SKIP:
                continue
            out.append((name, rel))
    return sorted(out)


def touch_annotations(obj, where, problems):
    """访问 `__annotations__`，触发注解求值。"""
    try:
        ann = getattr(obj, "__annotations__", None)
        if ann:
            dict(ann)                      # 逼它一次求值（3.14 的惰性代理也会因此展开）
    except Exception as e:                 # noqa: BLE001
        problems.append((where, "%s: %s" % (type(e).__name__, e)))


def main() -> int:
    sys.path.insert(0, SRC)
    os.chdir(ROOT)

    problems = []
    imported = 0
    for name, rel in module_names():
        try:
            mod = importlib.import_module(name)
        except Exception as e:             # noqa: BLE001
            problems.append((rel, "import 失败：%s: %s" % (type(e).__name__, e)))
            continue
        imported += 1
        touch_annotations(mod, rel, problems)
        for attr in dir(mod):
            try:
                obj = getattr(mod, attr)
            except Exception:              # noqa: BLE001
                continue
            if isinstance(obj, types.FunctionType) and obj.__module__ == mod.__name__:
                touch_annotations(obj, "%s:%s()" % (rel, attr), problems)
            elif isinstance(obj, type) and getattr(obj, "__module__", "") == mod.__name__:
                touch_annotations(obj, "%s:%s" % (rel, attr), problems)
                for sub in dir(obj):
                    try:
                        f = getattr(obj, sub)
                    except Exception:      # noqa: BLE001
                        continue
                    if isinstance(f, types.FunctionType):
                        touch_annotations(f, "%s:%s.%s()" % (rel, attr, sub), problems)

    if problems:
        print("FAIL  %d 处注解在运行期求值失败（%d 个模块已检查）："
              % (len(problems), imported))
        seen = set()
        for where, why in problems:
            key = (where.split(":")[0], why)
            if key in seen:
                continue
            seen.add(key)
            print("      %-44s %s" % (where, why[:90]))
        print("      修法：注解里用到的名字要在该模块顶部 import 到；"
              "或给模块加 `from __future__ import annotations`。")
        print("      注意：Python 3.14 上**看不出来**（PEP 649 惰性求值），"
              "3.10–3.13 上会直接 NameError。")
        return 1

    print("check  ok    %d 个模块的类型注解都能在运行期求值" % imported)
    return 0


if __name__ == "__main__":
    sys.exit(main())
