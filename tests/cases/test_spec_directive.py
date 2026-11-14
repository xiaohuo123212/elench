#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_spec_directive.py —— `.eln` 的 `// @spec` 指向（2026-11-11）

验的是**四条规则**（文档：`文档/设计/目前设计/决定-目录约定与spec指向.md`）：

1. 有 `@spec` → 用该路径（相对 `.eln` 所在目录）；
2. 没有 `@spec` → 回退同名 `.tu`（17 个既有样例就是这个形态）；
3. 写了但解析不到 → `E-Spec-NotFound`，消息里有**绝对路径**，**不回退**；
4. 多个 `.eln` 可以指向同一个 `.tu`。

另外两条**实现约束**也在这里钉住：

- `@spec` 只认**头部注释块**（放在代码后面的不认）；
- 它**不进 `parser.py`**：`//` 一直是注释，`.tu`/`.eln` 语法一个字没动。
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
SAMPLE = os.path.join(ROOT, "文档", "样例")
DSH = os.path.join(SRC, "dsh.py")

ENV = dict(os.environ, PYTHONIOENCODING="utf-8",
           PYTHONPATH=SRC + os.pathsep + os.environ.get("PYTHONPATH", ""))

sys.path.insert(0, SRC)

_ok = _bad = 0


def check(cond, label, detail=""):
    global _ok, _bad
    if cond:
        _ok += 1
        print("  ✓ %s" % label)
    else:
        _bad += 1
        print("  ✗ %s%s" % (label, ("\n      " + detail) if detail else ""))


def dsh(*args):
    p = subprocess.run([sys.executable, DSH] + list(args), cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=ENV)
    return p.returncode, ((p.stdout or "") + (p.stderr or ""))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def build_probe(tmp):
    """造一份"契约与实现不同名"的探针（用 sum 样例的源码，只换文件名）。"""
    tu_src = io.open(os.path.join(SAMPLE, "sum.tu"), encoding="utf-8").read()
    eln_src = io.open(os.path.join(SAMPLE, "sum.eln"), encoding="utf-8").read()
    write(os.path.join(tmp, "specs", "impl.tu"), tu_src)
    # ① 指向 ../specs/impl.tu
    write(os.path.join(tmp, "pair", "multi.eln"),
          "// @spec ../specs/impl.tu\n" + eln_src)
    # ④ 第二个 .eln 指向同一个 .tu
    write(os.path.join(tmp, "pair", "second.eln"),
          "// second.eln：同一份契约的第二个实现文件\n// @spec ../specs/impl.tu\n"
          + eln_src)
    # ③ 指向不存在的路径
    write(os.path.join(tmp, "pair", "broken.eln"),
          "// @spec ../specs/does_not_exist.tu\n" + eln_src)
    # ② 没有 @spec → 同名回退
    write(os.path.join(tmp, "same", "impl.tu"), tu_src)
    write(os.path.join(tmp, "same", "impl.eln"), eln_src)
    # 实现约束：@spec 写在代码后面 → 不认
    write(os.path.join(tmp, "late", "impl.tu"), tu_src)
    write(os.path.join(tmp, "late", "impl.eln"),
          eln_src + "\n// @spec ../specs/impl.tu\n")
    return tmp


def test_directive_scanning(tmp):
    """`_spec_directive`：只认头部；引号里的路径去引号。"""
    import dsh as _dsh
    p = os.path.join(tmp, "scan.eln")
    write(p, '// header\n// @spec "specs/my mod.tu"\nfunc f() {}\n')
    check(_dsh._spec_directive(p) == "specs/my mod.tu",
          "头部注释里的 @spec（带引号）被读出来",
          repr(_dsh._spec_directive(p)))
    write(p, "void f() {}\n// @spec specs/late.tu\n")
    check(_dsh._spec_directive(p) is None,
          "代码之后的 @spec **不认**（只认头部）",
          repr(_dsh._spec_directive(p)))
    write(p, "// 只是提到 @spec 这个词，不是指令\n")
    check(_dsh._spec_directive(p) is None,
          "注释里提到 @spec 但不成指令时不误判")


def test_cli(tmp):
    rc, out = dsh("check", os.path.join(tmp, "pair", "multi.eln"))
    check(rc == 0 and "module impl" in out,
          "① check 用 @spec 指向的 .tu（不同名也能配上）",
          "rc=%s\n%s" % (rc, out[-800:]))

    rc, out = dsh("check", os.path.join(tmp, "pair", "second.eln"))
    check(rc == 0 and "module impl" in out,
          "④ 两个 .eln 指向同一个 .tu", "rc=%s\n%s" % (rc, out[-600:]))

    rc, out = dsh("check", os.path.join(tmp, "pair", "broken.eln"))
    abs_p = os.path.abspath(os.path.join(tmp, "specs", "does_not_exist.tu"))
    check(rc == 1 and "E-Spec-NotFound" in out,
          "③ 写了但不存在 → E-Spec-NotFound（exit 1）",
          "rc=%s\n%s" % (rc, out[-800:]))
    check(abs_p in out, "③ 消息里有**解析到的绝对路径**",
          "找不到 %s\n%s" % (abs_p, out[-800:]))
    check("删掉 @spec" in out, "③ 消息里给了「没有契约就删掉 @spec 行」的修法")

    rc, out = dsh("check", os.path.join(tmp, "same", "impl.eln"))
    check(rc == 0 and "module impl" in out,
          "② 没有 @spec → 回退同名 .tu", "rc=%s\n%s" % (rc, out[-600:]))

    rc, out = dsh("cost", os.path.join(tmp, "pair", "multi.eln"))
    check(rc == 0 and "module impl" in out and "average" in out,
          "cost 也走同一条 @spec 解析（不是只有 check）",
          "rc=%s\n%s" % (rc, out[-800:]))


def test_samples_untouched():
    """17 个样例**一个都不许**加 @spec（约定是"可以用"，不是"必须用"）。"""
    hits = []
    for name in sorted(os.listdir(SAMPLE)):
        if not name.endswith(".eln"):
            continue
        txt = io.open(os.path.join(SAMPLE, name), encoding="utf-8").read()
        if "@spec" in txt:
            hits.append(name)
    check(not hits, "文档/样例 的 17 个 .eln 仍然没有 @spec（不迁移）",
          "被改了：%s" % ", ".join(hits))
    rc, out = dsh("check", os.path.join(SAMPLE, "count.eln"))
    check(rc == 0 and "module count" in out,
          "回退：样例 count.eln 仍按同名 sidecar 配上 count.tu",
          "rc=%s\n%s" % (rc, out[-600:]))


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="elench-spec-")
    try:
        build_probe(tmp)
        print("--- @spec 的读法（头部 / 引号 / 位置）")
        test_directive_scanning(tmp)
        print("--- CLI 的四条规则")
        test_cli(tmp)
        print("--- 向后兼容（样例不迁移）")
        test_samples_untouched()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if _bad:
        print("✗ %d 项失败 / %d 项通过" % (_bad, _ok))
        return 1
    print("✓ 全部通过（%d 项）" % _ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
