"""大括号初始化 `{…}` 与「只声明不初始化」的用例。

覆盖：
  1. `vector<T> x = {…}` 任意长度
  2. `array<T,N> x = {…}` 正好 N 个
  3. `array<T,N> x = {…}` 个数不符 → E-Array-InitLen
  4. 空 `{}`
  5. 元素是**表达式**而不是字面量（求值顺序按书写顺序）
  6. `vector<T> xs;` / `array<T,N> a;` 只声明不初始化
  7. 分配记账：vector 非空 = 1、array = 0、空 vector = 0

跑法（与其它用例一致）：`python tests/cases/test_brace_init.py`，退出码 0 即通过。
"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DSH = os.path.join(ROOT, "src", "dsh.py")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

FAILED = []


def _run_module(eln: str, tu: str, cmd: list) -> str:
    """在临时目录写一对 `.tu`/`.eln`，跑 dsh，返回合并输出。"""
    d = tempfile.mkdtemp(prefix="elench-brace-")
    with open(os.path.join(d, "m.eln"), "w", encoding="utf-8", newline="\n") as f:
        f.write(eln)
    with open(os.path.join(d, "m.tu"), "w", encoding="utf-8", newline="\n") as f:
        f.write(tu)
    r = subprocess.run([sys.executable, "-B", DSH] + cmd,
                       cwd=d, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    return (r.stdout or "") + (r.stderr or "")


def _spec(fn: str, alloc: int = 0) -> str:
    return (f"spec {fn}() -> u64 {{\n"
            f"    effects {{ alloc }};\n"
            f"    target {{ time: {{ max: O(1) }}, allocation: {{ max: {alloc} }}, }};\n"
            f"}}\n")


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name}  {detail}")


def main() -> int:
    # 1) vector 任意长度
    out = _run_module(
        "pub u64 main() {\n"
        "    vector<u64> xs = {1, 2, 3};\n"
        "    return xs.length();\n"
        "}\n", _spec("main", 1), ["run", "m"])
    check("vector{1,2,3}.length() == 3", "3" in out.strip().splitlines()[-1], out)

    # 2) array 正好 N
    out = _run_module(
        "pub u64 main() {\n"
        "    array<u64, 4> ys = {10, 20, 30, 40};\n"
        "    return ys[3];\n"
        "}\n", _spec("main", 0), ["run", "m"])
    check("array{10,20,30,40}[3] == 40", "40" in out.strip().splitlines()[-1], out)

    # 3) array 个数不符 → 诊断
    out = _run_module(
        "pub u64 main() {\n"
        "    array<u64, 4> ys = {1, 2, 3};\n"
        "    return ys[0];\n"
        "}\n", _spec("main", 0), ["check", "m"])
    check("array 少给一个 → E-Array-InitLen", "E-Array-InitLen" in out, out)

    out = _run_module(
        "pub u64 main() {\n"
        "    array<u64, 2> ys = {1, 2, 3};\n"
        "    return ys[0];\n"
        "}\n", _spec("main", 0), ["check", "m"])
    check("array 多给一个 → E-Array-InitLen", "E-Array-InitLen" in out, out)

    # 4) 空 {}
    out = _run_module(
        "pub u64 main() {\n"
        "    vector<u64> zs = {};\n"
        "    return zs.length() + 5;\n"
        "}\n", _spec("main", 0), ["run", "m"])
    check("空 {} 长度 0", "5" in out.strip().splitlines()[-1], out)

    # 5) 元素是表达式（不是字面量）
    out = _run_module(
        "pub u64 twice(u64 v) { return v + v; }\n"
        "pub u64 main() {\n"
        "    u64 a = 3;\n"
        "    vector<u64> xs = {a + 1, twice(a), a};\n"
        "    return xs[0] + xs[1] + xs[2];\n"
        "}\n",
        "spec twice(u64 v) -> u64 { effects {}; target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n"
        + _spec("main", 1), ["run", "m"])
    check("元素是表达式（4+6+3=13）", "13" in out.strip().splitlines()[-1], out)

    # 6) 只声明不初始化
    out = _run_module(
        "pub u64 main() {\n"
        "    vector<u64> xs;\n"
        "    xs.push(7);\n"
        "    array<u64, 4> ys;\n"
        "    ys[0] = 3;\n"
        "    u64 k;\n"
        "    return xs.length() + ys[0] + k;\n"
        "}\n", _spec("main", 1), ["run", "m"])
    check("只声明不初始化（1+3+0=4）", "4" in out.strip().splitlines()[-1], out)

    # 7) 分配记账
    def alloc_of(eln: str, fn: str = "main") -> str:
        return _run_module(eln, _spec(fn, 1), ["stats", "m", fn])

    out = alloc_of("pub u64 main() {\n"
                   "    array<u64, 3> a = {1, 2, 3};\n"
                   "    return a[0];\n}\n")
    check("array 大括号 → 分配 0", "分配数   0" in out or "分配数 0" in out, out)

    out = alloc_of("pub u64 main() {\n"
                   "    vector<u64> v = {1, 2};\n"
                   "    return v[0];\n}\n")
    check("vector 大括号（非空）→ 分配 1", "分配数   1" in out or "分配数 1" in out, out)

    out = alloc_of("pub u64 main() {\n"
                   "    vector<u64> v = {};\n"
                   "    return v.length();\n}\n")
    check("vector 大括号（空）→ 分配 0", "分配数   0" in out or "分配数 0" in out, out)

    out = alloc_of("pub u64 main() {\n"
                   "    vector<u64> v = vector<u64>::filled(0, 8);\n"
                   "    return v.length();\n}\n")
    check("vector::filled(0, 8) → 分配 1（任务 C）",
          "分配数   1" in out or "分配数 1" in out, out)

    out = alloc_of("pub u64 main() {\n"
                   "    array<u64, 8> a = array<u64, 8>::filled(0);\n"
                   "    return a[0];\n}\n")
    check("array::filled → 分配 0", "分配数   0" in out or "分配数 0" in out, out)

    print()
    if FAILED:
        print(f"✗ 失败 {len(FAILED)} 项：{FAILED}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
