#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_hof_and_builtins.py —— 两个「check 过、run 炸」的假绿，钉住修完后的行为。

## ① `for_each` 在集合上自相矛盾（2026-11-25 修）

- **静态**判定看类型：`set<T>` → 一元回调 `f(x)`，`map` / `HashMap` → 二元 `f(k, v)`。
- **运行期**以前看对象类型：`set_new()` 返回的是 `stdlib.MapVal`（HashMap），
  于是集合也被按 `f(k, v)` 调 —— `for_each(set_new(), on1)` 在 `check` 能过、
  `run` 报「on1 需要 1 个实参，给了 2」。

修法：`set_new()` 返回 `stdlib.SetVal`（`MapVal` 的子类），`for_each` 先判它，
按**键**做一元遍历。两个方向都要钉住：集合走一元、映射走二元、Vec 走一元，
而「集合 + 二元回调」必须仍在 `check` 期被拒（不能靠运行期炸）。

## ② `text_join` 传错类型抛宿主异常（同批修）

以前 `text_join(vector<u8>, sep)` 在运行期抛
`AttributeError: 'Text' object has no attribute 'parts'` —— 那是 Python 内部细节，
不是语言诊断。现在：`check` 期 `E-Type-ArgMismatch` 直接拦（并给改法），
运行期再兜一层带改法的 `TypeError`（`any` 里捞出来的值也跑不掉）。
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


def run_value(args, cwd):
    """只取 **stdout 的最后一行非空**（stderr 上那条"没有 .tu"的提示不算）。"""
    r = subprocess.run([sys.executable, "-B", DSH] + args, cwd=cwd,
                       capture_output=True, text=True, encoding="utf-8")
    outs = [l.strip() for l in (r.stdout or "").split("\n") if l.strip()]
    return r.returncode, (outs[-1] if outs else "")


def codes(out: str):
    return sorted(set(re.findall(r"E-[A-Za-z-]+", out)))


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="elench_hof_")

    def w(name: str, text: str) -> None:
        io.open(os.path.join(tmp, name), "w", encoding="utf-8", newline="").write(text)

    # ---------------- ① for_each：集合一元 / 映射二元 / Vec 一元 ----------------
    w("fe.eln", "u64 s_on(u64 x) {\n    return x;\n}\n\n"
                "u64 m_on(u64 k, u64 v) {\n    return k + v;\n}\n\n"
                "u64 on_set() {\n    set<u64> s = set_new();\n    set_add(s, 1);\n"
                "    set_add(s, 2);\n    return for_each(s, s_on);\n}\n\n"
                "u64 on_map() {\n    HashMap<u64, u64> m = map_new();\n"
                "    map_put(m, 1, 10);\n    map_put(m, 2, 20);\n"
                "    return for_each(m, m_on);\n}\n\n"
                "u64 on_vec() {\n    vector<u64> xs = {1, 2, 3};\n"
                "    return for_each(xs, s_on);\n}\n")
    rc, out = run(["check", "fe"], tmp)
    check(rc == 0, "for_each 三种容器都能过 check", out.strip()[:70])
    for fn, want in (("on_set", "2"), ("on_map", "2"), ("on_vec", "3")):
        rc, last = run_value(["run", "fe", "--fn=" + fn], tmp)
        check(rc == 0 and last == want, "for_each/%s → %s" % (fn, want), last[:60])

    # 集合 + 二元回调：**必须**在 check 期被拒（不能推到运行期）
    w("bad.eln", "u64 on2(u64 k, u64 v) {\n    return k + v;\n}\n\n"
                 "u64 f() {\n    set<u64> s = set_new();\n    set_add(s, 1);\n"
                 "    return for_each(s, on2);\n}\n")
    rc, out = run(["check", "bad"], tmp)
    check(rc != 0 and "E-Type-HofCallback" in codes(out),
          "集合 + 二元回调 → check 期 E-Type-HofCallback", ", ".join(codes(out))[:50])

    # ---------------- ② text_join：错类型静态拦、正路可跑 ----------------
    w("tj_bad.eln", "u64 f() {\n    vector<u8> a = {65, 66};\n"
                    "    vector<u8> b = {44};\n    return text_join(a, b).size();\n}\n")
    rc, out = run(["check", "tj_bad"], tmp)
    check(rc != 0 and "E-Type-ArgMismatch" in codes(out),
          "text_join(Vec<u8>, …) → check 期 E-Type-ArgMismatch（不再是运行期 AttributeError）",
          ", ".join(codes(out))[:50])
    check("text_concat" in out or "text_split" in out, "诊断里给了改法（text_split / text_concat）",
          out.strip()[:70])

    w("tj_ok.eln", "u64 f() {\n    vector<u8> s = {65, 44, 66};\n"
                   "    vector<u8> sep = {44};\n"
                   "    return text_join(text_split(s, sep), sep).size();\n}\n")
    rc, out = run(["check", "tj_ok"], tmp)
    check(rc == 0, "text_join(text_split(…), sep) 仍然能过", out.strip()[:70])
    rc, last = run_value(["run", "tj_ok", "--fn=f"], tmp)
    check(rc == 0 and last == "3", "text_join 正路 run → 3", last[:40])

    # ---------------- ③ deque<T>：默认构造 + 方法（2026-11-25 接线） ----------------
    # 以前 `deque<T>` 被 check 直接拒绝（E-Type-UnimplementedContainer），
    # 只有 `deque_*` 内建能内联调用 —— "能调、存不进变量"的半可用状态。
    w("dq.eln", "u64 by_builtin() {\n    deque<u64> q = deque_new();\n"
                "    deque_push_back(q, 10);\n    deque_push_front(q, 5);\n"
                "    return deque_len(q);\n}\n\n"
                "u64 by_default() {\n    deque<u64> d;\n    return d.size();\n}\n\n"
                "u64 by_method() {\n    deque<u64> d;\n    d.push_back(1);\n"
                "    d.push_front(2);\n    d.push_back(3);\n    return d.size();\n}\n")
    rc, out = run(["check", "dq"], tmp)
    check(rc == 0, "deque<T> 三种写法都能过 check（内建 / 默认构造 / 方法）", out.strip()[:70])
    for fn, want in (("by_builtin", "2"), ("by_default", "0"), ("by_method", "3")):
        rc, last = run_value(["run", "dq", "--fn=" + fn], tmp)
        check(rc == 0 and last == want, "deque/%s → %s" % (fn, want), last[:50])

    print()
    print("  %s  通过 %d，失败 %d" % ("全部通过" if _failed == 0 else "有失败", _passed, _failed))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
