#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_gate_dimensions.py —— 门禁必须**逐个维度**判，且"判不出"不能当"没问题"（2026-11-02）

## 三个洞（H1/H2/H3）

| 洞 | 以前 | 现在 |
|---|---|---|
| **H1 只查 time** | `--deny-debt` 只比 `cost_of`，`allocation: {max:0}` + 每轮分配照样 exit 0 | 逐维判：time 用 `compare`（按阶），allocation 用 `compare_exact`（按数量） |
| **H2 unknown = 放行** | 没有 `--deny-unknown`，"判不出来"被当成"没问题" | `--deny-unknown`：任何一维 unknown ⇒ 拒绝构建 |
| **H3 过度 unknown** | `ci != ct` 一律 unknown；`target` 是常数时**必然**判不出 | target 是常数、κ 含尺寸量 ⇒ **debt**（随规模增长必然超阶） |

## 为什么这三条决定了"可证伪"

我们对外主张"成本界是可违反的契约"。如果 `--deny-debt` 只看 time，
那么"承诺 0 次分配、实际每轮分配"的程序能过门禁——**主张我们自己先破了**。
"""
import os
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
for _p in (SRC,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cost_check import compare, compare_exact   # noqa: E402
from cost_domain.symbolic import Cost           # noqa: E402

ELENCH = os.path.join(SRC, "elench.py")
FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


# ---------------------------------------------------------------- 直接判 H3
def test_h3_time():
    """target 是常数、κ 含尺寸量 → debt（不是 unknown）。"""
    print("=== H3-time：target 是 O(1)，κ 随 n 增长 ===")
    impl = Cost.add(Cost.num(4), Cost.mul(Cost.size("xs"), Cost.num(17)))
    tgt = Cost.num(1)
    v = compare(impl, tgt)
    check("判 debt（不是 unknown）", v.kind == "debt", v.kind + " / " + (v.note or ""))
    v2 = compare(Cost.num(5), Cost.num(1))
    check("两个常数仍判 ok（big-O 口径）", v2.kind == "ok", v2.kind)


def test_h3_alloc():
    """allocation 维度同理：target 0，分配上界 n×1 → debt。"""
    print("=== H3-alloc：target 0，分配上界 n×1 ===")
    impl = Cost.mul(Cost.size("xs"), Cost.num(1))
    v = compare_exact(impl, Cost.num(0))
    check("判 debt", v.kind == "debt", v.kind + " / " + (v.note or ""))
    v2 = compare_exact(Cost.num(0), Cost.num(0))
    check("0 vs 0 仍判 ok", v2.kind == "ok", v2.kind)


# ---------------------------------------------------------------- CLI 判 H1/H2
ALLOC_TU = """spec f(const Vec<u8>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length() * xs.length()) }, allocation: { max: 0 }, };
}
"""
ALLOC_ELN = """func f(const Vec<u8>& xs) -> u64 {
    u64 i = 0;
    u64 s = 0;
    while (i < xs.length()) {
        Vec<u8> parts = text_join(xs, 44);
        s = s + parts.length();
        i = i + 1;
    }
    return s;
}
"""

CONST_TU = """spec f(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(1) }, };
}
"""
CONST_ELN = """func f(const Vec<u64>& xs) -> u64 {
    u64 i = 0;
    u64 s = 0;
    while (i < xs.length()) {
        s = s + 1;
        i = i + 1;
    }
    return s;
}
"""

UNKNOWN_TU = """spec f(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, };
}
"""
UNKNOWN_ELN = """func f(const Vec<u64>& xs) -> u64 {
    u64 s = 0;
    bool go = true;
    while (go) {
        s = s + 1;
        if (s >= 4) {
            go = false;
        }
    }
    return s;
}
"""


def run_cli(tmp, name, *args):
    r = subprocess.run([sys.executable, ELENCH] + list(args),
                       capture_output=True, encoding="utf-8", errors="replace",
                       cwd=tmp)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def write_mod(tmp, name, tu, eln):
    with open(os.path.join(tmp, name + ".tu"), "w", encoding="utf-8") as f:
        f.write(tu)
    with open(os.path.join(tmp, name + ".eln"), "w", encoding="utf-8") as f:
        f.write(eln)


def test_cli():
    tmp = tempfile.mkdtemp(prefix="elench_gate_")
    write_mod(tmp, "alloc_debt", ALLOC_TU, ALLOC_ELN)
    write_mod(tmp, "const_target", CONST_TU, CONST_ELN)
    write_mod(tmp, "unknown", UNKNOWN_TU, UNKNOWN_ELN)

    print("=== H1：allocation 违约必须被 --deny-debt 拦住 ===")
    rc, out = run_cli(tmp, "alloc_debt", "cost", "alloc_debt")
    check("cost 把分配标成 ▲", "分配 ▲" in out, out[:200])
    rc, out = run_cli(tmp, "alloc_debt", "build", "alloc_debt", "--deny-debt")
    check("build --deny-debt 退出码 1", rc == 1, f"rc={rc}")
    check("报错里点名 allocation", "alloc" in out, out[:300])

    print("=== H2：--deny-unknown 只拒 unknown，不拒 debt ===")
    rc, out = run_cli(tmp, "unknown", "build", "unknown", "--deny-unknown")
    check("unknown + --deny-unknown → 退出码 1", rc == 1, f"rc={rc}")
    rc, out = run_cli(tmp, "const_target", "build", "const_target", "--deny-unknown")
    check("debt（常数 target）+ --deny-unknown → 退出码 0", rc == 0, f"rc={rc}")
    rc, out = run_cli(tmp, "const_target", "build", "const_target", "--deny-debt")
    check("debt + --deny-debt → 退出码 1", rc == 1, f"rc={rc}")


def test_g25_concurrency():
    """G25：并发函数的时间契约不可证伪——必须显式说出来（警告，不是静默）。"""
    print("=== G25：并发 + time target → W-Concurrency-Time ===")
    from checker import _check_concurrency_cost
    from parser import parse_source

    tu = parse_source(
        "spec f(const Vec<u64>& xs) -> u64 { effects {}; "
        "target { time: { max: O(xs.length()) } }; }", "g25.tu").module
    ch = parse_source(
        "func f(const Vec<u64>& xs) -> u64 {\n"
        "    u64 s = 0;\n"
        "    parallel {\n"
        "        { s = s + 1; }\n"
        "        { s = s + 2; }\n"
        "    }\n"
        "    return s;\n}\n", "g25.eln").module
    codes = [d.code for d in _check_concurrency_cost(tu, ch)]
    check("报 W-Concurrency-Time", "W-Concurrency-Time" in codes, str(codes))

    # 没有并发就不该报
    ch2 = parse_source("func f(const Vec<u64>& xs) -> u64 {\n"
                       "    return xs.length();\n}\n", "g25b.eln").module
    check("无并发不报", not _check_concurrency_cost(tu, ch2), "")


def main() -> int:
    test_h3_time()
    test_h3_alloc()
    test_cli()
    test_g25_concurrency()
    if FAILED:
        print(f"\n失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
