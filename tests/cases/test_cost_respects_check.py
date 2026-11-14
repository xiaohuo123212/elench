#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_cost_respects_check.py —— `check` 报 error 时 `cost` **不许再给一个绿色的 κ**

## 为什么这条测试必须存在

两个代理各自独立复现过同一件事：

    $ elench check t.eln
    ✗ E-Call-Undeclared …            ← error 级
    $ elench cost t.tu
      ✓ main  κ ≤ 3
      ✓ 所有函数都在 target 之内      ← rc=0

这直接违背 E18 自己的论点：**未声明的外部调用「记 1 个操作」不是上界**。
更糟的是只有加 `--explain` 才承认"可能偏小"——默认输出把"不知道"说成了"达标"。
`build` / `run` 早就有 `_gate_checks` 拦 error；`cost` 是唯一漏掉的那条路。

## 口径（与 `build` 的 `_gate_checks` 同一个）

| 诊断级别 | `cost` 的行为 |
|---|---|
| **error** | 拒绝给 κ：rc≠0，stderr 说清"κ 不是可信的上界"并列出前几条 |
| **warning** | **不阻断**（`I-Fn-NoSpec-Module` 这类只是提示），κ 照常、rc=0 |

`--json` / `--yaml` / `--signature` 没有被改坏：模块**健康**时输出形状与以前一样
（可被 `json.loads` 直接吃掉）；有 error 时它们也拒绝（不出 κ，rc≠0）。
调试要硬看当前数：`--no-check`（与 `build` 同名开关），stderr 会说明输出不可信。

跑法：`python -B tests/cases/test_cost_respects_check.py`
"""
import json
import os
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                            # noqa: BLE001
    pass

FAILED = []
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

#: **error 级**：`nope(1)` 是未声明调用（`E-Call-Undeclared`，E18 兜底）。
TU_BAD = """spec main() -> u64 {
    effects {};
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}
"""
ELN_BAD = """u64 main() {
    return nope(1);
}
"""

#: **健康**模块：κ 可算、target 可判。
TU_OK = """spec sum(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.length()) }, allocation: { max: 0 }, };
}
"""
ELN_OK = """u64 sum(const vector<u64>& xs) {
    u64 s = 0;
    u64 i = 0;
    while (i < xs.length()) {
        s = s + xs[i];
        i = i + 1;
    }
    return s;
}
"""

#: **只有 warning**：没有 `.tu` ⇒ `I-Fn-NoSpec-Module`（提示，不阻断）。
ELN_WARN = """u64 f(u64 x) {
    return x + 1;
}
"""


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(cmd, *files, extra=()):
    """临时目录 + 写文件 + 跑 `dsh <cmd> …`；返回 `(proc, dir)`。"""
    d = tempfile.mkdtemp(prefix="eln_costchk_")
    for fn, text in files:
        with open(os.path.join(d, fn), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    r = subprocess.run([sys.executable, "-B", os.path.join(SRC, "dsh.py"), cmd]
                       + [os.path.join(d, fn) for fn, _ in files] + list(extra),
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    return r, d


def main() -> int:
    print("[1] 改前复现的那条路：`check` 报 error ⇒ `cost` 拒绝，rc≠0")
    ck, _d1 = run("check", ("t.eln", ELN_BAD))
    check("check：rc=1（E-Call-Undeclared）",
          ck.returncode == 1 and "E-Call-Undeclared" in (ck.stdout or ""),
          ((ck.stdout or "") + (ck.stderr or ""))[-300:])
    co, _d2 = run("cost", ("t.tu", TU_BAD), ("t.eln", ELN_BAD))
    out, err = co.stdout or "", co.stderr or ""
    check("cost：rc=1（以前是 0）", co.returncode == 1, str(co.returncode))
    check("cost：stdout 里**没有** κ（不给一个绿色的假上界）",
          "κ ≤" not in out, out[:300])
    check("cost：不再说「所有函数都在 target 之内」",
          "所有函数都在 target 之内" not in out, out[:300])
    check("cost：stderr 点名「拒绝给出 κ」", "拒绝给出 κ" in err, err[-400:])
    check("cost：stderr 说清 κ 不是可信的上界", "不是可信的上界" in err, err[-400:])
    check("cost：stderr 列出具体诊断码", "E-Call-Undeclared" in err, err[-400:])
    check("cost：stderr 给出修法（先 check / --no-check）",
          "elench check" in err and "--no-check" in err, err[-400:])
    check("check 与 cost 的退出码一致（1 == 1）",
          ck.returncode == co.returncode, f"{ck.returncode} vs {co.returncode}")

    print("[2] 机器档没有被弄坏：健康模块的 --json / --signature 仍然可解析")
    rj, _d3 = run("cost", ("ok.tu", TU_OK), ("ok.eln", ELN_OK), extra=["--json"])
    check("--json：rc=0", rj.returncode == 0, (rj.stderr or "")[-300:])
    try:
        doc = json.loads(rj.stdout or "")
        ok_json = isinstance(doc, dict) and "sum" in (doc.get("functions") or {})
    except Exception as exc:                                 # noqa: BLE001
        doc, ok_json = None, False
        print(f"        json.loads 失败：{exc}")
    check("--json：stdout 是**纯 JSON**（可直接 json.loads）", ok_json,
          (rj.stdout or "")[:200])
    rs, _d4 = run("cost", ("ok.tu", TU_OK), ("ok.eln", ELN_OK),
                  extra=["--signature"])
    check("--signature：rc=0", rs.returncode == 0, (rs.stderr or "")[-300:])
    try:
        json.loads(rs.stdout or "")
        ok_sig = True
    except Exception:                                        # noqa: BLE001
        ok_sig = False
    check("--signature：stdout 仍是纯 JSON", ok_sig, (rs.stdout or "")[:200])

    print("[3] 有 error 时机器档也**不出 κ**（rc≠0，stderr 说清）")
    for mode in ("--json", "--signature", "--yaml"):
        rb, _db = run("cost", ("t.tu", TU_BAD), ("t.eln", ELN_BAD), extra=[mode])
        check(f"{mode}：rc=1", rb.returncode == 1, str(rb.returncode))
        check(f"{mode}：stdout 为空（不输出一份「看起来正常」的结果）",
              not (rb.stdout or "").strip(), (rb.stdout or "")[:200])
        check(f"{mode}：stderr 有「拒绝给出 κ」", "拒绝给出 κ" in (rb.stderr or ""),
              (rb.stderr or "")[-200:])

    print("[4] `--no-check`：显式要调试数时照给，但说清不可信")
    rn, _dn = run("cost", ("t.tu", TU_BAD), ("t.eln", ELN_BAD), extra=["--no-check"])
    check("--no-check：rc=0（显式跳过检查）", rn.returncode == 0,
          (rn.stderr or "")[-300:])
    check("--no-check：κ 照常输出", "κ ≤" in (rn.stdout or ""),
          (rn.stdout or "")[:200])
    check("--no-check：stderr 说清「不是可信的上界」",
          "不是可信的上界" in (rn.stderr or ""), (rn.stderr or "")[-300:])

    print("[5] warning **不**阻断：没有 .tu 的模块照常给 κ、rc=0")
    cw, _dw = run("check", ("f.eln", ELN_WARN))
    check("check：warning 模块 rc=0", cw.returncode == 0,
          ((cw.stdout or "") + (cw.stderr or ""))[-300:])
    check("check：确实只有提示（I-Fn-NoSpec-Module）",
          "I-Fn-NoSpec-Module" in ((cw.stdout or "") + (cw.stderr or "")),
          ((cw.stdout or "") + (cw.stderr or ""))[-300:])
    rw, _dw2 = run("cost", ("f.eln", ELN_WARN))
    check("cost：rc=0（warning 不算 error）", rw.returncode == 0,
          (rw.stderr or "")[-300:])
    check("cost：κ 照常给出", "κ ≤" in (rw.stdout or ""), (rw.stdout or "")[:200])
    check("check 与 cost 退出码一致（0 == 0）",
          cw.returncode == rw.returncode, f"{cw.returncode} vs {rw.returncode}")
    rwj, _dw3 = run("cost", ("f.eln", ELN_WARN), extra=["--json"])
    try:
        json.loads(rwj.stdout or "")
        ok_wj = rwj.returncode == 0
    except Exception:                                        # noqa: BLE001
        ok_wj = False
    check("warning 模块的 --json 仍可解析、rc=0", ok_wj, (rwj.stdout or "")[:200])

    print("[6] 健康模块：行为一个字都没变（κ / 达标 / rc=0）")
    rk, _dk = run("cost", ("ok.tu", TU_OK), ("ok.eln", ELN_OK))
    check("rc=0", rk.returncode == 0, (rk.stderr or "")[-300:])
    check("κ 照常输出", "κ ≤" in (rk.stdout or ""), (rk.stdout or "")[:200])
    check("「所有函数都在 target 之内」照旧", "所有函数都在 target 之内" in (rk.stdout or ""),
          (rk.stdout or "")[-300:])

    print("[7] 边界：**只有 spec、没有实现**的调用按声明的 target 记账（不阻断）")
    # 为什么这条必须钉住：`check` 对它报 `E-Call-Undeclared`（没标 `external`），
    # 但 cost 有**一份签过字的契约**可依（`target_trusted` 会标注出来）。
    # `test_explain` 整条用例建立在这条路上；若把它也当"不可信"阻断，
    # 那个既有能力就被打回去了。E18 说的是"连声明都没有"的那一类。
    tu_sp = """spec inner(u64 n) -> u64 {
    effects {};
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}
spec outer(u64 n) -> u64 {
    effects {};
    target { time: { max: O(1) }, allocation: { max: 0 }, };
}
"""
    eln_sp = """u64 outer(u64 n) {
    return 1 + inner(n);
}
"""
    rs2, _ds2 = run("cost", ("s.tu", tu_sp), ("s.eln", eln_sp))
    check("cost：rc=0（有 spec 可依，不阻断）", rs2.returncode == 0,
          (rs2.stderr or "")[-300:])
    check("cost：κ 照常输出", "κ ≤" in (rs2.stdout or ""), (rs2.stdout or "")[:200])
    cs2, _dc2 = run("check", ("s.eln", eln_sp))
    check("check：同一模块仍是 E-Call-Undeclared（这是有意的边界）",
          "E-Call-Undeclared" in ((cs2.stdout or "") + (cs2.stderr or "")),
          ((cs2.stdout or "") + (cs2.stderr or ""))[-300:])

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
