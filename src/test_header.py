#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_header.py —— `elench header`：把契约带出工具链（P5 方案 A）

## 它是什么

一个 `.h`：**每个函数一行 C 声明，上一行注释写着 κ 与 effects**。

## 它不是什么

**不是实现**。别人能**编译**（拿到签名与契约），但**链接不到**——
实现要等 C++ 后端（E37）。所以它是"**契约的载体**"，不是"能跑的库"。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FAILED = []
SAMPLE_TU = os.path.join(HERE, "..", "文档", "设计", "目前设计", "样例", "count.tu")
SAMPLE_ELN = os.path.join(HERE, "..", "文档", "设计", "目前设计", "样例", "count.eln")


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def header_text() -> str:
    r = subprocess.run([sys.executable, os.path.join(HERE, "dsh.py"), "header",
                        SAMPLE_TU, SAMPLE_ELN],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout or ""


def main() -> int:
    out = header_text()

    print("=== 内容 ===")
    check("有 pragma once", "#pragma once" in out)
    check("有 C 声明", "uint64_t count(" in out, out[:200])
    check("容器参数用 const &", "const std::vector<uint8_t>&" in out)
    check("注释里有 κ", "// κ:" in out)
    check("注释里有 effects", "// effects:" in out)

    check("注释里有 target（作者的要求）", "// target:" in out, out[:200])

    print("=== 单参数写法 ===")
    import os as _os
    base = _os.path.splitext(SAMPLE_TU)[0]
    r = subprocess.run([sys.executable, _os.path.join(HERE, "dsh.py"), "header", base],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    check("`header count`（不写扩展名）可用", "uint64_t count(" in (r.stdout or ""),
          (r.stdout or "")[:120])

    print("=== 它是契约，不是实现 ===")
    check("没有函数体（没有花括号块）", "{\n" not in out.replace("// ", ""), "")

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
