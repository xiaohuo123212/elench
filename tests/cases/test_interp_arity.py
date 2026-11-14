#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_interp_arity.py —— interp 与 check 的**三处口径统一**（2026-11-13）

## 修的三个缺陷（都是「解释器与静态检查说法不一」）

| # | 形状 | 改前 | 改后 |
|---|---|---|---|
| ① | `xs.insert(1, 3, 7)` | check 绿 / run **静默只插一个 3**（第三个实参 7 被丢掉——静默错值） | 实现 C++ 的三参重载：在 1 前插 **3 个 7**；check 绿 ⇔ run 绿 |
| ② | `s.push_back(1)`（`s` 是 stack / queue） | check 红（`E-Call-UnknownMethod`）/ 解释器**直接认**（绕过 check 的路径给绿灯） | 统一到静态那边：解释器也拒绝，`push` / `enqueue` 才是适配器的方法 |
| ③ | `fixedvec<u64,3>(7)`（小写） | check 绿 / run 抛 `InterpError`（只有 `array<T,N>(v)` 走构造路径） | `fixedvec` 与 `array` 是同一个类型，走同一条构造路径；check 绿 ⇔ run 绿 |

## 为什么 ① 选「实现」而不是「报错」

`typecheck.METHOD_TABLE` **只登记方法名字，不登记元数**——实测
`xs.insert(1, 3, 7)` 的 `check` 是 **rc=0**（没有任何方法元数校验）。
所以要「在 run 期报 `E-Type-ArgCount`」就必须同时让 check 也报，而
`src/typecheck.py` 不在本轮可改范围。只在 run 期拒绝会**制造**新的
「check 绿 / run 红」，正是本项目 `test_check_run_consistency.py` 总闸禁止的形状。
于是按 C++ `std::vector::insert(pos, count, value)` 的语义把三参实现出来：
check 本来就接受，run 现在给对值，两边一致。

## 触达路径

① 是用户可写的普通方法调用；③ 的构造是 `.eln` 里的表达式；② 的
`push_back` 只在**绕过 check** 的路径上可达（`run` 会先跑 check），但直接
`import interp` 的调用方（编辑器 / `stats` / 库）会走到，所以解释器层也要钉住。

## 覆盖三层

1. **端到端**（真实 `.eln` + 真实 CLI）：`check` 与 `run` 的结论、具体输出；
2. **分派层**（直接 `import interp`，不经 check）：`push_back` 必须抛
   `InterpError`（而不是被 `push` 分支接走）、小写 `fixedvec` 构造必须给值；
3. **不回归**：两参 `insert`、`push` / `enqueue`、`FixedVec` 驼峰、大括号初始化
   全部保持原行为。
"""

import io
import os
import shutil
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
PY = sys.executable

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def cli(sub, path):
    """跑一次工具链，返回 `(rc, 合并输出)`（UTF-8，逐字节保留）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = SRC
    p = subprocess.run([PY, "-B", os.path.join(SRC, "elench.py"), sub, path],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def payload(out):
    """程序输出行：去掉 `·` 开头的说明行（`run` 的「没有 .tu」提示等）。"""
    return [ln.strip() for ln in out.splitlines()
            if ln.strip() and not ln.strip().startswith("·")]


def _write(root, name, text):
    with io.open(os.path.join(root, name), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return os.path.join(root, name)


def _fname(label):
    """标签 → 单层文件名（Windows 上 `/` `:` 都是路径分隔符，不能进名字）。"""
    return "".join("_" if ch in "/\\:*?\"<>|" else ch for ch in label) + ".eln"


def _run_case(root, name, eln):
    path = _write(root, name, eln)
    return cli("check", path), cli("run", path)


def _green(root, label, eln, want):
    """形状必须 check 绿 ⇔ run 绿，且 run 的**程序输出**逐行等于 `want`。"""
    (cr, cout), (rr, rout) = _run_case(root, _fname(label), eln)
    check(f"{label}：check rc=0", cr == 0, cout.strip()[-300:])
    check(f"{label}：run rc=0", rr == 0, rout.strip()[-300:])
    check(f"{label}：check 绿 ⇔ run 绿", (cr == 0) == (rr == 0),
          f"check rc={cr} / run rc={rr}")
    got = payload(rout)
    check(f"{label}：输出逐行是 {want}", got == want, f"实际 {got!r}")
    return rout


def _red(root, label, eln, code):
    """形状必须 check 红 ⇔ run 红，且 run 的红是**检查挡下的**（不是 InterpError）。"""
    (cr, cout), (rr, rout) = _run_case(root, _fname(label), eln)
    check(f"{label}：check rc≠0", cr != 0, cout.strip()[-300:])
    check(f"{label}：run rc≠0", rr != 0, rout.strip()[-300:])
    check(f"{label}：check 绿 ⇔ run 绿（两边都红）", (cr == 0) == (rr == 0),
          f"check rc={cr} / run rc={rr}")
    check(f"{label}：check 给出 {code}", code in cout, cout.strip()[-300:])
    check(f"{label}：run 不是“跑起来才炸”", "InterpError" not in rout,
          rout.strip()[-300:])
    return rout


# ---------------------------------------------------------------- ① insert 三参
#
# 修前实测：`xs = [1,2]; xs.insert(1,3,7)` → `[1,3,2]`（插的是 **3**，7 被丢掉），
# size = 3。修后必须是 C++ `insert(pos, count, value)` 的语义：`[1,7,7,7,2]`。
INSERT3 = """u64 main() {
    vector<u64> xs;
    xs.push_back(1);
    xs.push_back(2);
    xs.insert(1, 3, 7);
    print(xs.size());
    for (u64 i = 0; i < xs.size(); i = i + 1) { print(xs[i]); }
    return 0;
}
"""

INSERT2 = """u64 main() {
    vector<u64> xs;
    xs.push_back(1);
    xs.push_back(2);
    xs.insert(1, 9);
    print(xs.size());
    for (u64 i = 0; i < xs.size(); i = i + 1) { print(xs[i]); }
    return 0;
}
"""

# `pos == size()` 是追加（C++ 的 `end()`）；`count == 0` 是 no-op。
INSERT_EDGE = """u64 main() {
    vector<u64> xs;
    xs.push_back(1);
    xs.push_back(2);
    xs.insert(xs.size(), 2, 7);
    xs.insert(0, 0, 9);
    print(xs.size());
    for (u64 i = 0; i < xs.size(); i = i + 1) { print(xs[i]); }
    return 0;
}
"""

# n 个副本必须**互相独立**（C++ 是 n 次拷贝构造）。
# 共享对象会让 `rows[0][0] = 9` 同时改掉 `rows[1][0]`。
INSERT_ALIAS = """u64 main() {
    vector<vector<u64>> rows;
    vector<u64> row;
    row.push_back(1);
    rows.insert(0, 2, row);
    rows[0][0] = 9;
    print(rows[0][0]);
    print(rows[1][0]);
    return 0;
}
"""


def test_insert_three_args(root):
    print("[1] `insert(pos, count, value)`：三参不再静默丢参")
    _green(root, "三参 insert(1,3,7)", INSERT3,
           ["5", "1", "7", "7", "7", "2", "0"])
    # 反向：两参语义一个字没动（不回归）
    _green(root, "两参 insert(1,9)（不回归）", INSERT2,
           ["3", "1", "9", "2", "0"])
    _green(root, "三参：pos=size 追加 / count=0 no-op", INSERT_EDGE,
           ["4", "1", "2", "7", "7", "0"])
    _green(root, "三参：n 个副本互相独立", INSERT_ALIAS, ["9", "1", "0"])


# ---------------------------------------------------------------- ② push_back
#
# `typecheck.METHOD_TABLE["stack"/"queue"]` 只有 `push` / `enqueue`：
# `s.push_back(1)` 在 check 期报 `E-Call-UnknownMethod`。解释器以前也认
# `push_back`，两边口径不一；现在解释器也拒绝（统一到静态更严的那边）。
STACK_PB = """u64 main() {
    stack<u64> s;
    s.push(1);
    s.push_back(2);
    return s.size();
}
"""

QUEUE_PB = """u64 main() {
    queue<u64> q;
    q.push(1);
    q.push_back(2);
    return q.size();
}
"""

# 正确写法必须两边都绿（收紧不能误伤）。
ADAPTER_OK = """u64 main() {
    stack<u64> s;
    s.push(1);
    s.enqueue(2);
    queue<u64> q;
    q.push(3);
    q.enqueue(4);
    print(s.size());
    print(q.size());
    return 0;
}
"""


def test_push_back(root):
    print("[2] `stack` / `queue` 的 `push_back`：解释器也拒绝（与 check 一致）")
    _red(root, "stack.push_back", STACK_PB, "E-Call-UnknownMethod")
    _red(root, "queue.push_back", QUEUE_PB, "E-Call-UnknownMethod")
    _green(root, "stack/queue 的 push / enqueue（不回归）", ADAPTER_OK,
           ["2", "2", "0"])

    # 分派层：**直接** import interp（绕过 check），`push_back` 也必须炸。
    # 以前 `_m in ("push", "enqueue", "push_back")` 把它接走了。
    sys.path.insert(0, SRC)
    from parser import parse_source                 # noqa: E402
    from interp import Interp, InterpError          # noqa: E402

    mod = parse_source(
        "u64 sp() { stack<u64> s; s.push(1); return s.size(); }\n"
        "u64 spb() { stack<u64> s; s.push(1); s.push_back(2); return s.size(); }\n"
        "u64 qpb() { queue<u64> q; q.push(1); q.push_back(2); return q.size(); }\n",
        "dispatch.eln").module
    check("分派层：`s.push(1)` 仍然可用",
          Interp(mod, None).call("sp", []) == 1,
          str(Interp(mod, None).call("sp", [])))
    for fn, hint in (("spb", ".push(…)"), ("qpb", ".enqueue(…)")):
        try:
            Interp(mod, None).call(fn, [])
            check(f"分派层：`{fn}` 抛 InterpError", False, "没有抛")
        except InterpError as exc:
            msg = str(exc)
            check(f"分派层：`{fn}` 抛 InterpError", "push_back" in msg, msg)
            check(f"分派层：`{fn}` 的错误里教正确写法 {hint}", hint in msg, msg)


# ---------------------------------------------------------------- ③ fixedvec 小写
#
# `typenames.CANON` 把驼峰 `FixedVec` 折成 `array`，而小写 `fixedvec` 是
# `typecheck` 一直认的写法（`METHOD_TABLE` / `_elem_ty_of` / `_zero_of_type`
# 都按 `("array", "fixedvec")` 成对判）。只有解释器的构造分支漏了它：
# check 绿、run 报「这种构造只支持 array<T, N>(v)」。
def _fv(decl, expr, body="return 0;"):
    return (f"u64 main() {{\n"
            f"    {decl} v = {expr};\n"
            f"    {body}\n"
            f"}}\n")


FV_LOWER = _fv("fixedvec<u64,3>", "fixedvec<u64,3>(7)",
               "print(v.size());\n    for (u64 i = 0; i < v.size(); i = i + 1) "
               "{ print(v[i]); }\n    return 0;")
FV_CAMEL = _fv("FixedVec<u64,3>", "FixedVec<u64,3>(7)",
               "print(v.size());\n    for (u64 i = 0; i < v.size(); i = i + 1) "
               "{ print(v[i]); }\n    return 0;")
FV_FILLED = _fv("fixedvec<u64,3>", "fixedvec<u64,3>::filled(7)",
                "print(v.size());\n    for (u64 i = 0; i < v.size(); i = i + 1) "
                "{ print(v[i]); }\n    return 0;")
FV_MAKE = _fv("fixedvec<u64,3>", "fixedvec<u64,3>::make()",
              "v.push_back(7);\n    print(v.size());\n    print(v[0]);\n"
              "    return 0;")
FV_BRACED = _fv("fixedvec<u64,3>", "{1, 2, 3}",
                "print(v.size());\n    for (u64 i = 0; i < v.size(); i = i + 1) "
                "{ print(v[i]); }\n    return 0;")


def test_fixedvec_lower(root):
    print("[3] 小写 `fixedvec<T,N>`：与 `array` / 驼峰 `FixedVec` 同一条构造路径")
    out_lower = _green(root, "小写 fixedvec<u64,3>(7)", FV_LOWER,
                       ["3", "7", "7", "7", "0"])
    out_camel = _green(root, "驼峰 FixedVec<u64,3>(7)（不回归）", FV_CAMEL,
                       ["3", "7", "7", "7", "0"])
    check("小写与驼峰 run 输出**逐字节相同**", out_lower == out_camel,
          f"{out_lower!r} vs {out_camel!r}")
    _green(root, "小写 fixedvec<u64,3>::filled(7)", FV_FILLED,
           ["3", "7", "7", "7", "0"])
    _green(root, "小写 fixedvec<u64,3>::make() + push_back", FV_MAKE,
           ["1", "7", "0"])
    _green(root, "小写 fixedvec<u64,3> = {1,2,3}（不回归）", FV_BRACED,
           ["3", "1", "2", "3", "0"])

    # 分派层：直接 import interp，构造必须给 [7,7,7]（不是抛异常）。
    from parser import parse_source                 # noqa: E402
    from interp import Interp                        # noqa: E402

    mod = parse_source(
        "u64 fv() { fixedvec<u64,3> v = fixedvec<u64,3>(7);\n"
        "    return v[0] * 10 + v[2]; }\n", "fv.eln").module
    check("分派层：小写 fixedvec 构造给 77（7 和 7）",
          Interp(mod, None).call("fv", []) == 77,
          str(Interp(mod, None).call("fv", [])))


# ---------------------------------------------------------------- 一致性总闸
#
# 三处各一条 `check 绿 ⇔ run 绿` 断言（与 `test_check_run_consistency.py` 同一口径）。
# 绿组与红组分开：绿 = 两边 rc=0，红 = 两边 rc≠0 且 run 的红不是 InterpError。
GREEN_GATE = (
    ("① insert(pos,count,value)", INSERT3),
    ("① insert(pos,value)（不回归）", INSERT2),
    ("② stack/queue push / enqueue", ADAPTER_OK),
    ("③ 小写 fixedvec<T,N>(v)", FV_LOWER),
    ("③ 驼峰 FixedVec<T,N>(v)", FV_CAMEL),
)
RED_GATE = (
    ("② stack.push_back", STACK_PB),
    ("② queue.push_back", QUEUE_PB),
)


def test_consistency_gate(root):
    print("[4] 总闸：三处的 `check 绿 ⇔ run 绿`")
    for name, eln in GREEN_GATE:
        (cr, _cout), (rr, rout) = _run_case(root, _fname(name), eln)
        check(f"{name}：check 绿 ⇔ run 绿", (cr == 0) == (rr == 0),
              f"check rc={cr} / run rc={rr}；{rout.strip()[-160:]}")
    for name, eln in RED_GATE:
        (cr, _cout), (rr, rout) = _run_case(root, _fname(name), eln)
        check(f"{name}：check 红 ⇔ run 红", (cr == 0) == (rr == 0),
              f"check rc={cr} / run rc={rr}；{rout.strip()[-160:]}")


def main():
    root = tempfile.mkdtemp(prefix="elench-interp-arity-")
    try:
        test_insert_three_args(root)
        test_push_back(root)
        test_fixedvec_lower(root)
        test_consistency_gate(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print()
    if FAILED:
        print(f"✗ 失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("✓ 全部通过：insert 三参 / push_back 口径 / 小写 fixedvec 三处 check⇔run 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
