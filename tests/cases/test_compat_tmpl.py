#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_compat_tmpl.py —— 模板单态化（兼容层第 20 条）。

## 路线：模板 → 具体重载（用户提的那条）

`template<typename T> u64 len(const vector<T>& xs)` 在 `len(xs)`（`xs: vector<u64>`）
处**实例化**成 `len__u64(const vector<u64>&)`，调用点改写成它。C++ 编译器做的也是
这件事（instantiation），所以这不是"近似"，是同一条路。

**只做单态化**：没有偏特化 / SFINAE / 可变参数 / 非类型参数 / 模板元编程。
推断不出来**不猜**——报 `E-Type-InferFailed` 并说明怎么改。

## 为什么这条测试必须比"能过"更严

"能过"什么都证明不了：把 `T` 当 `vector<u64>` 硬算也能过。所以这里比的是
**κ 逐字符相同**（模板实例 vs 手写具体函数），以及 `run` 的真实返回值。
"""
import io
import os
import re
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
DSH = os.path.join(ROOT, "src", "dsh.py")

_passed = 0
_failed = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print("  ok   " + label)
    else:
        _failed += 1
        print("  FAIL " + label + (("  ← " + extra) if extra else ""))


def run(args, cwd):
    r = subprocess.run([sys.executable, "-B", DSH] + args, cwd=cwd,
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def kappas(out: str) -> dict:
    """从 `cost` 输出里抠出 {函数名: κ 表达式}（不受对齐空格影响）。"""
    got = {}
    for line in out.split("\n"):
        m = re.search(r"·\s*(\w+)\s+κ ≤ (.+?)\s*$", line.strip())
        if m:
            got[m.group(1)] = m.group(2).strip()
    return got


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="elench_tmpl_")

    def write(name: str, text: str) -> str:
        p = os.path.join(tmp, name)
        io.open(p, "w", encoding="utf-8", newline="").write(text)
        return p

    TMPL = ("template <typename T>\n"
            "u64 len(const vector<T>& xs) {\n"
            "    return xs.size();\n"
            "}\n"
            "u64 f(const vector<u64>& xs) {\n"
            "    return len(xs);\n"
            "}\n")
    HAND = ("u64 len(const vector<u64>& xs) {\n"
            "    return xs.size();\n"
            "}\n"
            "u64 f(const vector<u64>& xs) {\n"
            "    return len(xs);\n"
            "}\n")

    # ① C++ 拼写与原生拼写产出同样的结果
    write("t1.eln", TMPL)
    write("t2.eln", TMPL.replace("template <typename T>\n", "").replace("u64 len(", "u64 len<T>(", 1))
    rc1, o1 = run(["check", "t1.eln", "--compat"], tmp)
    rc2, o2 = run(["check", "t2.eln", "--compat"], tmp)
    check(rc1 == 0, "`template <typename T>` 前缀能过", o1.strip().split("\n")[0][:70])
    check(rc2 == 0, "原生 `u64 len<T>(…)` 也能过（两条拼写同形）", o2.strip().split("\n")[0][:70])

    # ② 等价性：κ 与手写具体函数**逐字符相同**
    write("hand.eln", HAND)
    _, ot = run(["cost", "t1.eln", "--compat"], tmp)
    _, oh = run(["cost", "hand.eln", "--compat"], tmp)
    kt, kh = kappas(ot), kappas(oh)
    check(kt.get("len__u64") == kh.get("len") and kt.get("f") == kh.get("f"),
          "实例化后的 κ 与手写具体函数逐字符相同",
          "实例 %r vs 手写 %r" % (kt.get("len__u64"), kh.get("len")))

    # ③ 真实返回值一致
    rc, out = run(["run", "t1.eln", "--fn=f", "--vec=1,2,3", "--compat"], tmp)
    # 值在 stdout，提示在 stderr（合并后最后一行可能是提示），所以按行找
    lines = [l.strip() for l in out.split("\n") if l.strip()]
    check(rc == 0 and "3" in lines, "run 返回值与手写一致（3）", " | ".join(lines[-2:])[:60])

    # ④ 多个实例：u64 与 u8 各一个，各算各的
    write("multi.eln", "template <typename T>\n"
                       "u64 len(const vector<T>& xs) {\n    return xs.size();\n}\n"
                       "u64 a(const vector<u64>& xs) { return len(xs); }\n"
                       "u64 b(const vector<u8>& ys) { return len(ys); }\n")
    _, om = run(["cost", "multi.eln", "--compat"], tmp)
    km = kappas(om)
    check("len__u64" in km and "len__u8" in km, "同一模板按不同实参实例化两次",
          ", ".join(sorted(km)))

    # ⑤ 推不出来时不猜，给能照着改的诊断
    write("nod.eln", "template <typename T>\nu64 mk() {\n    return 1;\n}\n"
                     "u64 f() { return mk(); }\n")
    rc, out = run(["check", "nod.eln", "--compat"], tmp)
    check(rc != 0 and "E-Type-InferFailed" in out,
          "推断不出类型参数时报 E-Type-InferFailed", out.strip().split("\n")[0][:70])
    check("给实参加类型标注" in out or "具体类型" in out,
          "该诊断给了可照做的改法", out.strip()[:80])

    # ⑥ 严格模式：`template` 仍然拒绝（默认行为不变）
    rc, out = run(["check", "t1.eln"], tmp)
    check(rc != 0 and "E-Keyword-Unsupported" in out,
          "严格模式仍然拒绝 `template`（不放宽默认）", out.strip().split("\n")[0][:60])

    # ⑦ 类模板：按使用点实例化成具体 struct
    write("cls.eln", "template <typename T>\nstruct Box {\n    T v;\n};\n"
                     "u64 f() {\n    Box<u64> b{1};\n    return b.v;\n}\n")
    rc, out = run(["check", "cls.eln", "--compat"], tmp)
    check(rc == 0, "类模板 `Box<u64>` 实例化成具体 struct", out.strip().split("\n")[0][:70])
    write("cls_hand.eln", "struct Box__u64 {\n    u64 v;\n};\n"
                          "u64 f() {\n    Box__u64 b{1};\n    return b.v;\n}\n")
    _, o_t = run(["cost", "cls.eln", "--compat"], tmp)
    _, o_h = run(["cost", "cls_hand.eln", "--compat"], tmp)
    check(kappas(o_t).get("f") == kappas(o_h).get("f"),
          "类模板实例的 κ 与手写具体 struct 逐字符相同",
          "%r vs %r" % (kappas(o_t).get("f"), kappas(o_h).get("f")))

    # ⑧ 类模板 + 成员函数：成员函数也要跟着实例化
    write("clsm.eln", "template <typename T>\nstruct P {\n    T x;\n    T y;\n"
                      "    u64 sum() { return x + y; }\n};\n"
                      "u64 f() {\n    P<u64> p{1, 2};\n    return p.sum();\n}\n")
    rc, out = run(["check", "clsm.eln", "--compat"], tmp)
    check(rc == 0, "类模板的成员函数也实例化（`P__u64_sum`）", out.strip().split("\n")[0][:70])
    rc, out = run(["run", "clsm.eln", "--fn=f", "--compat"], tmp)
    check(rc == 0 and "3" in [l.strip() for l in out.split("\n")],
          "成员函数实例化后 run 也对（1+2=3）", out.strip().split("\n")[-1][:40])

    # ⑨ 两个不同实参各生成一个 struct
    write("cls2.eln", "template <typename T>\nstruct Box {\n    T v;\n};\n"
                      "u64 a() {\n    Box<u64> x{1};\n    return x.v;\n}\n"
                      "u64 b() {\n    Box<u8> y{2};\n    return y.v;\n}\n")
    rc, out = run(["check", "cls2.eln", "--compat"], tmp)
    check(rc == 0, "同一类模板两个实参各实例化一次", out.strip().split("\n")[0][:70])

    print()
    print("  %s  通过 %d，失败 %d" % ("全部通过" if _failed == 0 else "有失败", _passed, _failed))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
