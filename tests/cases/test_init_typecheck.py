"""大括号初始化的**类型检查**（任务 F）与**常量容量**（任务 G）。

跑法：`python tests/cases/test_brace_init.py` 同款，退出码 0 即通过。
"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DSH = os.path.join(ROOT, "src", "dsh.py")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

FAILED = []


def _run_module(eln, tu, cmd):
    d = tempfile.mkdtemp(prefix="elench-init-type-")
    for name, text in (("m.eln", eln), ("m.tu", tu)):
        with open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    r = subprocess.run([sys.executable, "-B", DSH] + cmd, cwd=d, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=ENV)
    return (r.stdout or "") + (r.stderr or "")


def _spec(fn="main", alloc=0):
    return (f"spec {fn}() -> u64 {{\n"
            f"    effects {{ alloc }};\n"
            f"    target {{ time: {{ max: O(1) }}, allocation: {{ max: {alloc} }}, }};\n"
            f"}}\n")


def check(name, ok, detail=""):
    if ok:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name}  {detail}")


def main() -> int:
    # 元素类型检查（任务 F）
    out = _run_module(
        "pub u64 main() {\n"
        "    vector<bool> a = {1, 2, 3};\n"
        "    return a.length();\n"
        "}\n", _spec("main", 1), ["check", "m"])
    check("数字进 bool 容器 → E-Init-ElemType", "E-Init-ElemType" in out, out)

    out = _run_module(
        'pub u64 main() {\n'
        '    vector<u64> v = {"a"};\n'
        '    return v[0];\n'
        '}\n', _spec("main", 1), ["check", "m"])
    check("字符串进整数容器 → E-Init-ElemType", "E-Init-ElemType" in out, out)

    out = _run_module(
        "pub u64 main() {\n"
        "    array<u64, 3> a = {1, 2, 3};\n"
        "    vector<bool> b = {true, false};\n"
        "    vector<u64> e = {};\n"
        "    return a[0] + e.length() + b.length();\n"
        "}\n", _spec("main", 1), ["check", "m"])
    check("类型正确 + 空列表 → 不报", "E-Init-ElemType" not in out, out)

    # 常量容量（任务 G）
    out = _run_module(
        "pub u64 main() {\n"
        "    array<u64, N> a = {1, 2, 3, 4};\n"
        "    array<u64, M> b = {1,2,3,4,5,6,7,8};\n"
        "    return a[0] + b[0];\n"
        "}\n",
        "const N: u64 = 4;\nconst M: u64 = N * 2;\n" + _spec("main", 0),
        ["check", "m"])
    check("常量容量（N=4, M=N*2）个数正好 → 不报",
          "E-Array-InitLen" not in out, out)

    out = _run_module(
        "pub u64 main() {\n"
        "    array<u64, N> c = {1, 2, 3};\n"
        "    return c[0];\n"
        "}\n",
        "const N: u64 = 4;\n" + _spec("main", 0), ["check", "m"])
    check("常量容量少给 → E-Array-InitLen", "E-Array-InitLen" in out, out)

    out = _run_module(
        "pub u64 main() {\n"
        "    array<u64, N> d = {1,2,3,4,5};\n"
        "    return d[0];\n"
        "}\n",
        "const N: u64 = 4;\n" + _spec("main", 0), ["check", "m"])
    check("常量容量多给 → E-Array-InitLen", "E-Array-InitLen" in out, out)

    out = _run_module(
        "pub u64 main() {\n"
        "    array<u64, NOPE> e = {1};\n"
        "    return e[0];\n"
        "}\n", _spec("main", 0), ["check", "m"])
    check("容量求不出 → W-Array-InitLen-Unknown（不阻断）",
          "W-Array-InitLen-Unknown" in out, out)


    print()
    if FAILED:
        print(f"✗ 失败 {len(FAILED)} 项：{FAILED}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
