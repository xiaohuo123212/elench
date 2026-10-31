#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_cpp.py —— C++ 后端**最小切片**（`elench cpp`）

## 它做什么

把**一小类**程序翻译成 C++：标量循环 + `Vec<u64>` + 少量内建。
**目的不是覆盖全语言**，是**先打通一条窄路**（E37 的第一块）。

## 对拍：`--run`

找 `g++` / `clang++` / `cl`：

- **找到** → 编译；
- **找不到** → **如实说"无法对拍"**，不假装验证过。

（本机没有编译器，所以这里只验生成结果 + 诚实提示。）
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILED = []

TU = """spec sum(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""
ELN = """func sum(const Vec<u64>& xs) -> u64 {
    u64 s = 0;
    u64 i = 0;
    while (i < xs.length()) {
        s = s + xs[i];
        i = i + 1;
    }
    return s;
}
"""


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def main() -> int:
    d = tempfile.mkdtemp()
    tu = os.path.join(d, "sum.tu")
    eln = os.path.join(d, "sum.eln")
    open(tu, "w", encoding="utf-8", newline="\n").write(TU)
    open(eln, "w", encoding="utf-8", newline="\n").write(ELN)

    r = subprocess.run([sys.executable, os.path.join(HERE, "dsh.py"), "cpp", tu],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = r.stdout or ""

    print("=== 生成的 C++ ===")
    check("有函数签名", "uint64_t sum(const std::vector<uint64_t>& xs)" in out, out[:200])
    check("有 while", "while ((i < xs.size()))" in out, out[:200])
    check("有 return", "return s;" in out)
    check("没有 C++ 里不存在的内建残留", "text_split_vec" not in out)

    print("=== --run：有编译器才对拍 ===")
    r2 = subprocess.run([sys.executable, os.path.join(HERE, "dsh.py"), "cpp", tu, "--run"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace")
    both = (r2.stdout or "") + (r2.stderr or "")
    has_compiler = any(__import__("shutil").which(c) for c in ("g++", "clang++", "cl"))
    if has_compiler:
        check("有编译器 → 编译成功", "编译成功" in both, both[-200:])
    else:
        check("没编译器 → 如实说无法对拍",
              "无法对拍" in both or "找不到 C++ 编译器" in both, both[-200:])

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
