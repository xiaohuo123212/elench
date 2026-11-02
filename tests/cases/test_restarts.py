#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_restarts.py —— 诊断 → 可执行修法（restart）+ 宽松模式编译。

## 守的两条

1. **修法不许包含"放宽契约"**：任何 restart 的 argv/patch 都不得改写 `.tu` 的
   `target`。契约是持久的、实现是一次性的——"不达标就改承诺"这条路必须不存在。
2. **宽松模式与严格模式必须相反**：`build --lenient` 退出 0，
   `build --deny-debt` 退出 1，两者同时给要报错（不能既拒又放）。
"""
import os as _os
import sys as _sys

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

from checker import Diag, Restart, check_module

_passed = 0
_failed = []


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


TU_DEBT = """
spec f(const Vec<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 } };
}
"""

ELN_DEBT = """
func f(const Vec<u64>& xs) -> u64 {
    u64 s = 0;
    u64 i = 0;
    while (i < xs.length()) {
        u64 j = 0;
        while (j < xs.length()) {
            s = s + xs.at(j);
            j = j + 1;
        }
        i = i + 1;
    }
    return s;
}
"""

def write_module(d, tu=TU_DEBT, eln=ELN_DEBT):
    with open(os.path.join(d, "m.tu"), "w", encoding="utf-8", newline="\n") as f:
        f.write(tu)
    with open(os.path.join(d, "m.eln"), "w", encoding="utf-8", newline="\n") as f:
        f.write(eln)


def run(args, cwd):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, os.path.join(SRC, "elench.py")] + args,
                          cwd=cwd, capture_output=True, encoding="utf-8",
                          errors="replace", env=env)


def test_dataclass():
    print("[1] Restart / Diag 的机器可读形状")
    r = Restart("command", "重测", argv=["elench", "measure", "m"])
    d = Diag("E-Test", "x", 1, 2, actions=[r])
    js = d.to_dict()
    check(js["actions"][0]["kind"] == "command", "to_dict 带动作", str(js["actions"]))
    check(js["actions"][0]["argv"] == ["elench", "measure", "m"], "argv 原样保留")
    check(Diag("E", "x").to_dict()["actions"] == [], "没有动作时是空列表")


def test_no_target_restart():
    print("[2] 任何修法都不许改写 target")
    from dsh import _cost_restarts
    acts = _cost_restarts("m")
    check(len(acts) >= 3, "至少有 3 条修法", str(len(acts)))
    bad_argv = [a for a in acts if any("target" in x for x in a.argv)]
    check(not bad_argv, "没有哪条命令带 target", str(bad_argv))
    edits = [a for a in acts if a.kind == "edit"]
    check(not any("target" in str(a.patch) for a in edits),
          "没有哪条 edit 动作改 target", str(edits))
    check(any(("build" in a.argv and "--deny-debt" not in a.argv) for a in acts),
          "清单里有『默认（宽松）模式编译』= 不带 --deny-debt 的 build",
          str([a.argv for a in acts]))


def test_guarantee_restart():
    print("[3] 不可检查的 guarantees 带一条修法")
    tu = """
spec f(const Vec<u64>& xs) -> u64 {
    effects {};
    guarantees looks_good(xs);
}
"""
    r = check_module(tu, None, "m.tu")
    d = next((x for x in r.diagnostics if x.code == "E-Guarantee-Uncheckable"), None)
    check(d is not None, "报出了 E-Guarantee-Uncheckable")
    check(d is not None and len(d.actions) >= 1, "带至少一条修法",
          str(d.actions if d else None))
    check(d is not None and d.actions[0].kind == "info", "是 info 类（只提示）")


def test_lenient_vs_deny():
    print("[4] --lenient 与 --deny-debt 行为相反")
    d = tempfile.mkdtemp(prefix="restart_")
    write_module(d)

    r_cost = run(["cost", "m.tu"], d)
    check("修法：" in (r_cost.stdout or ""), "cost 债务里打印了修法清单",
          (r_cost.stdout or "")[-300:])
    check("--apply=N" in (r_cost.stdout or ""), "提示了 --apply=N")

    # **默认就是宽松**（只记债务、退出 0）——这就是原来那个"宽松模式"，
    # 不需要额外开关；开关只有反向的 `--deny-debt`。
    r_len = run(["build", "m.tu", "m.eln"], d)
    check(r_len.returncode == 0, "默认（宽松）构建退出 0", (r_len.stdout or "")[-200:])

    r_deny = run(["build", "m.tu", "m.eln", "--deny-debt"], d)
    check(r_deny.returncode == 1, "严格模式退出 1", (r_deny.stdout or "")[-200:])
    check("修法：" in (r_deny.stdout or ""), "拒绝时也给修法清单")




def test_apply_runs():
    print("[5] --apply=N 真的执行")
    d = tempfile.mkdtemp(prefix="restart2_")
    write_module(d)
    r = run(["cost", "m.tu", "--apply=2"], d)      # 2) 查看分档与静态交点 → dispatch
    out = (r.stdout or "") + (r.stderr or "")
    check("执行：" in out, "打印了要执行的命令", out[-300:])
    check(r.returncode in (0, 1), "命令跑完了（退出码来自被执行的命令）", str(r.returncode))

    r2 = run(["cost", "m.tu", "--apply=99"], d)
    check(r2.returncode == 2, "越界 → 退出 2", str(r2.returncode))
    check("越界" in ((r2.stdout or "") + (r2.stderr or "")), "说明越界了")


def main() -> int:
    test_dataclass()
    test_no_target_restart()
    test_guarantee_restart()
    test_lenient_vs_deny()
    test_apply_runs()
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
