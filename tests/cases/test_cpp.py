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

（2026-11-03 更正：早先这里写着「本机没有编译器」，那时确实没有。
后来发现 WinGet 装过一套 MinGW，`g++` 在 PATH 上——所以这条用例在本机
走的是「有编译器」那一支。它同时暴露过两个真 bug：`uint64_t main()`
不合 C++ 规定、以及无入口模块被强行链接。两者都已修。）
"""
import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但**这批测试原先住在 `src/`**，里面大量
#: `os.path.join(HERE, "..", "文档")` / `os.path.join(ROOT, "tools")` 都假设
#: 「本文件在 src/、上一级是仓库根」。搬家时改 200 处路径比让 `HERE` 继续指 src 贵得多，
#: 所以保留旧语义：`HERE` = src/，`CASEDIR` = 本文件真正所在的目录。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import subprocess
import sys
import tempfile

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
