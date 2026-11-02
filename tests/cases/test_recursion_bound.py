# -*- coding: utf-8 -*-
"""`E-Recursion-Unbounded`（C11-b）：**有递归就必须声明 `recursion depth`**。

## 这条诊断要解决什么

修四类 κ 缺口时，递归那一类是这么修的：`.tu` 写了 `recursion depth <= N`
就按 `(N+1) × 一层成本` 算。问题是——**不写**的时候，κ 只按一层计：

    down(xs, 8)：κ = 33，ops = 104   →  承诺的上界**小于**真实成本

而 `elench cost` 当时会打印「✓ 所有函数都在 target 之内」。**假结论比报错更糟**，
所以这里把它变成**编译期错误**：要么写深度，要么编不过。

## 判据

- 普通调用图上的**环**（直接递归 `a→a`、互递归 `a→b→a`）算递归；
- `?!` 到候选的边**不算**（那是派发，不是递归；候选调宿主另有 B-9 管）；
- 环上**每个**成员各自需要声明——互递归时不能只声明一个。

## 四道闸

`check` / `build`（走 checker）/ `cost` / `export`（走 `_recursion_gate`）
对同一个模块给出**一致**的拒绝。
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

import io
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(ROOT, "tools"))

from checker import check_module                      # noqa: E402

_passed = 0
_failed = []

CODE = "E-Recursion-Unbounded"


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


# ---------------------------------------------------------------- 源码样本

DIRECT_ELN = """
func down(const Vec<u64>& xs, u64 i) -> u64 {
    if (i == 0) { return xs.length(); }
    return 1 + down(xs, i - 1);
}
"""

DIRECT_TU = """
spec down(const Vec<u64>& xs, u64 i) -> u64 {
    effects {};
%s
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

MUTUAL_ELN = """
func ping(const Vec<u64>& xs, u64 i) -> u64 {
    if (i == 0) { return xs.length(); }
    return pong(xs, i - 1);
}
func pong(const Vec<u64>& xs, u64 i) -> u64 {
    if (i == 0) { return xs.length(); }
    return ping(xs, i - 1);
}
"""

MUTUAL_TU = """
spec ping(const Vec<u64>& xs, u64 i) -> u64 {
    effects {};
%s
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec pong(const Vec<u64>& xs, u64 i) -> u64 {
    effects {};
%s
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""

DEPTH = "    recursion depth <= 8;"
NONE = ""


def diags_of(tu: str, eln: str):
    res = check_module(tu, eln, "t")
    return [d for d in res.diagnostics if d.code == CODE]


# ---------------------------------------------------------------- 检查器

def test_direct():
    print("[1] 直接递归")
    ds = diags_of(DIRECT_TU % NONE, DIRECT_ELN)
    check(len(ds) == 1, "无 depth → 1 条 E-Recursion-Unbounded", str(len(ds)))
    if ds:
        check(ds[0].severity == "error", "严重级是 error（不是 warning）", ds[0].severity)
        check("down → down" in ds[0].message,
              "消息里给出环：`down → down`", ds[0].message)
        check("recursion depth" in ds[0].message,
              "消息里给出出路：写 `recursion depth <= N;`", ds[0].message)
        check("C11-b" in ds[0].message, "消息里引用规格条款 C11-b", ds[0].message)
        check(ds[0].line == 2, "位置指向 spec 声明行", str(ds[0].line))

    check(diags_of(DIRECT_TU % DEPTH, DIRECT_ELN) == [],
          "写了 `recursion depth <= 8;` → 无诊断")


def test_mutual():
    print("[2] 互递归")
    ds = diags_of(MUTUAL_TU % (NONE, NONE), MUTUAL_ELN)
    names = sorted(d.message.split("`")[1] for d in ds if "`" in d.message)
    check(len(ds) == 2, "两个都不声明 → 2 条（环上每个成员各一条）", str(len(ds)))
    check(names == ["ping", "pong"], "两条分别指向 ping / pong", str(names))

    ds = diags_of(MUTUAL_TU % (DEPTH, NONE), MUTUAL_ELN)
    names = sorted(d.message.split("`")[1] for d in ds if "`" in d.message)
    check(len(ds) == 1 and names == ["pong"],
          "只声明 ping → 仍报 pong（不能只声明一个）", str(names))

    check(diags_of(MUTUAL_TU % (DEPTH, DEPTH), MUTUAL_ELN) == [],
          "两个都声明 → 无诊断")


def test_no_false_positive():
    print("[3] 不误报")
    chain = """
func a(const Vec<u64>& xs) -> u64 { return b(xs); }
func b(const Vec<u64>& xs) -> u64 { return xs.length(); }
"""
    tu = """
spec a(const Vec<u64>& xs) -> u64 { effects {}; }
spec b(const Vec<u64>& xs) -> u64 { effects {}; }
"""
    check(diags_of(tu, chain) == [], "无环的调用链 a→b → 无诊断")

    disp = """
func host(const Vec<u64>& xs) -> u64 { return ?!pick(xs); }
func by_scan(const Vec<u64>& xs) -> u64 { return xs.length(); }
func by_rev(const Vec<u64>& xs) -> u64 { return xs.length(); }
"""
    tu = """
choose pick over { by_scan, by_rev };
spec host(const Vec<u64>& xs) -> u64 { effects {}; benchmodel { n = xs.length() }; }
spec by_scan(const Vec<u64>& xs) -> u64 { effects {}; }
spec by_rev(const Vec<u64>& xs) -> u64 { effects {}; }
"""
    check(diags_of(tu, disp) == [],
          "`?!` 到候选的边不算递归（宿主与候选之间没有环）")

    # 没有 spec 的函数：不归这条诊断管（交给 E-Spec-Missing 一类），不能崩
    check(diags_of("", DIRECT_ELN) == [],
          "没有 spec 时不崩、也不报这条（不越界）")

    # 非递归函数写了 depth：无害
    tu = """
spec down(const Vec<u64>& xs, u64 i) -> u64 {
    effects {};
    recursion depth <= 8;
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
spec plain(const Vec<u64>& xs) -> u64 {
    effects {};
    recursion depth <= 4;
}
"""
    check(diags_of(tu, DIRECT_ELN + """
func plain(const Vec<u64>& xs) -> u64 { return xs.length(); }
""") == [], "非递归函数写了 depth → 无害（不报错）")


# ---------------------------------------------------------------- 四条命令行闸

def run_cli(argv, cwd):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = os.path.join(ROOT, "src")
    p = subprocess.run([sys.executable, os.path.join(ROOT, "src", "dsh.py")] + argv,
                       cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def test_cli_gates():
    print("[4] 四道闸：check / build / cost / export 一致拒绝")
    tmp = tempfile.mkdtemp(prefix="recbound_")
    try:
        tu_p = os.path.join(tmp, "r.tu")
        eln_p = os.path.join(tmp, "r.eln")
        for path, text in ((tu_p, DIRECT_TU % NONE), (eln_p, eln_p and DIRECT_ELN)):
            with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)

        rc, out = run_cli(["check", tu_p], tmp)
        check(rc == 1, "check：退出码 1（编译不过）", str(rc))
        check(CODE in out, "check：输出里有 E-Recursion-Unbounded", out[:200])

        rc, out = run_cli(["build", tu_p], tmp)
        check(rc == 1, "build：退出码 1", str(rc))
        check(CODE in out, "build：输出里有 E-Recursion-Unbounded", out[:200])

        rc, out = run_cli(["cost", tu_p], tmp)
        check(rc == 1, "cost：**拒绝算 κ**（退出码 1，不再打印假结论）", str(rc))
        check("拒绝算 κ" in out, "cost：明说拒绝算 κ", out[:200])
        check("所有函数都在 target 之内" not in out,
              "cost：不再打印「✓ 都在 target 之内」", out[:200])

        rc, out = run_cli(["export", tu_p, "--format=csv"], tmp)
        check(rc == 1, "export：退出码 1", str(rc))
        check("拒绝算 κ" in out, "export：同样拒绝", out[:200])

        rc, out = run_cli(["header", tu_p], tmp)
        check(rc == 1, "header：退出码 1（不发出带假 κ 的 .h）", str(rc))

        # 补上 depth 之后四条都必须放行
        with io.open(tu_p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(DIRECT_TU % DEPTH)
        rc, out = run_cli(["check", tu_p], tmp)
        check(rc == 0, "补 depth 后 check：退出码 0", str(rc))
        rc, out = run_cli(["build", tu_p], tmp)
        check(rc == 0, "补 depth 后 build：退出码 0", str(rc))
        rc, out = run_cli(["cost", tu_p], tmp)
        check(rc == 0, "补 depth 后 cost：退出码 0", str(rc))
        rc, out = run_cli(["export", tu_p, "--format=csv"], tmp)
        check(rc == 0, "补 depth 后 export：退出码 0", str(rc))
        rc, out = run_cli(["header", tu_p], tmp)
        check(rc == 0, "补 depth 后 header：退出码 0", str(rc))
    finally:
        for name in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


def main() -> int:
    test_direct()
    test_mutual()
    test_no_false_positive()
    test_cli_gates()

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
