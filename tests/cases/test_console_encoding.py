#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_console_encoding.py —— 正式入口 `elench` 在 GBK 控制台下不能崩。

## 为什么必须有这条测试（2026-11-18 审计发现）

输出里全是 `✓` `✗` `⚠` `κ` `⊤`，而 Windows 控制台默认代码页是 GBK。
`dsh.py` 末尾的 `if __name__ == "__main__":` 里有一句
`sys.stdout.reconfigure(encoding="utf-8")`，但**正式入口不是那条路**：

    elench（console script）→ src/elench.py → dsh.main()

所以 `python src/dsh.py check a.eln` 正常，而**装完之后 `elench check a.eln` 会崩**：

    UnicodeEncodeError: 'gbk' codec can't encode character '\\u26a0'

开发机上一直没发现，因为开发者的 shell 里常年有 `PYTHONIOENCODING=utf-8`。

修法：把 reconfigure 挪进 `dsh.main()`，两条入口都覆盖。

## 这条测试怎么保证能复现

不去猜当前控制台是什么编码，而是**显式把子进程的 `PYTHONIOENCODING` 设成 `gbk`**
再跑正式入口。修复前一定 `UnicodeEncodeError`，修复后 rc=0。
"""
import os
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")

SRC_TEXT = "u64 f() {\n    return 1;\n}\n"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "a.eln")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(SRC_TEXT)

        env = dict(os.environ, PYTHONIOENCODING="gbk")
        code = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "sys.argv = ['elench', 'check', %r]\n"
            "from elench import main\n"
            "sys.exit(main(sys.argv))\n" % (SRC, path)
        )
        r = subprocess.run([sys.executable, "-B", "-c", code],
                           cwd=tmp, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)

    blob = (r.stdout or "") + (r.stderr or "")
    if "UnicodeEncodeError" in blob or "codec can't encode" in blob:
        print("FAIL  正式入口在 GBK 控制台下抛 UnicodeEncodeError")
        for line in blob.strip().splitlines()[-4:]:
            print("      " + line.strip()[:160])
        print("      修法：`dsh.main()` 开头 reconfigure stdout/stderr 为 utf-8")
        return 1
    if r.returncode != 0:
        print("FAIL  `elench check` 在 PYTHONIOENCODING=gbk 下 rc=%d（应为 0）" % r.returncode)
        for line in blob.strip().splitlines()[-4:]:
            print("      " + line.strip()[:160])
        return 1

    print("check  ok    正式入口在 GBK 控制台下 rc=0，无 UnicodeEncodeError")
    return 0


if __name__ == "__main__":
    sys.exit(main())
