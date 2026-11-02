#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_policy.py —— **口径一致性**的护栏（2026-11-01 重写：口径只剩一种）。

## 现在只有一种口径

    承诺 κ = max over candidates 的**符号式**（对所有 n 成立）
    分档表 = 只决定**运行期跑哪个候选**，不参与"承诺多少"

历史（两个坑都踩过）：
  · 默认值从"逐段最优"回退成"全候选取 max"，没人发现；
  · `cost` 用分段、`export` 却画最劣候选，图上一条平线。
  · 更狠的一次：表只覆盖 8..4096，越界后运行期退回第一个候选，
    而承诺被表钉成了常数 —— n=8192 承诺 426,001、实测 729,109（破界）。

这个测试守的是：**命令之间对"承诺"的理解必须一样**，且承诺必须真的压得住。

用法：python tests/cases/test_policy.py
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
import shutil
import subprocess
import sys
import tempfile

EXAMPLE = os.path.join(ROOT, "文档", "入门", "例子")
_passed = 0
_failed = []


def check(cond, name, detail=""):
    global _passed
    if cond:
        _passed += 1
        print(f"  ✓ {name}")
    else:
        _failed.append(name)
        print(f"  ✗ {name}   {detail}")


def run(args, cwd=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, os.path.join(HERE, "elench.py")] + args,
                       cwd=cwd or EXAMPLE, capture_output=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    print("[1] 唯一口径：承诺是**分段函数**（每段用运行期真跑的那个候选）")
    rc, out = run(["cost", "gap"])
    check(rc == 0, "cost 退出码 0", f"rc={rc}")
    check("分段" in out, "打印口径说明", out[:200])
    check("{" in out and "≤n(xs)≤" in out,
          "κ 是分段式（不是折成的常数，也不是单条 max）", out[:300])
    check("n(xs)×102" in out and "n(xs)×21" in out,
          "两段各自保留符号式", out[:300])
    check("425,990" not in out,
          "表里那个 425,990 常数不再出现在承诺里", out[:300])

    print("[2] --promise 仍可传（兼容），但不改变任何数")
    rc2, out2 = run(["cost", "gap", "--promise"])
    check(rc2 == 0, "--promise 不报错", f"rc={rc2}")
    _k1 = [l for l in out.splitlines() if "κ ≤" in l]
    _k2 = [l for l in out2.splitlines() if "κ ≤" in l]
    check(_k1 == _k2, "加不加 --promise 的 κ 完全一样", f"{_k1} vs {_k2}")

    print("[3] export 的 CSV 与 cost 口径一致：承诺 ≥ 候选")
    path = os.path.join(EXAMPLE, "csv_policy_check")
    rc3, out3 = run(["export", "crossover", "--format=csv", f"--out={path}"])
    check(rc3 == 0, "export 成功", out3[-200:])
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            head = f.readline().strip().split(",")
            for line in f:
                parts = line.strip().split(",")
                if len(parts) == len(head):
                    rows.append(dict(zip(head, parts)))
    check(len(rows) >= 2, "CSV 有数据", str(len(rows)))
    if rows:
        bad = []
        for r in rows:
            try:
                seg = float(r.get("static_bound_operations") or 0)
                cand = float(r.get("static_candidate_operations") or 0)
            except ValueError:
                continue
            if cand and seg < cand:
                bad.append((r.get("input_size"), seg, cand))
        check(not bad, "每一档：承诺 κ ≥ 候选 κ", str(bad[:3]))
        _mcol = ("measured_operations" if "measured_operations" in rows[0]
                 else "measured_nanoseconds")
        check(rows[0].get(_mcol, "") == "", "没有实测时 measured 列为空", str(rows[0]))
    os.path.exists(path) and os.remove(path)

    print("[4] 静态分段建议：交叉点由公式解出，不依赖测量")
    check("静态交点" in out, "cost 打印静态交点", out[:400])
    check("2,7" in out or "2221" in out or "2716" in out,
          "gap 的交点 ≈ 2,221/2,222", out[:400])

    print("[5] elench.toml 的旧开关不再改变口径")
    cfgdir = tempfile.mkdtemp()
    for name in ("crossover.tu", "crossover.eln"):
        shutil.copyfile(os.path.join(EXAMPLE, name), os.path.join(cfgdir, name))
    with open(os.path.join(cfgdir, "elench.toml"), "w", encoding="utf-8") as f:
        f.write("[cost]\npromise = true\n")
    rc5, out5 = run(["cost", "crossover"], cwd=cfgdir)
    _k5 = [l for l in out5.splitlines() if "κ ≤" in l]
    rc5b, out5b = run(["cost", "crossover"])
    _k5b = [l for l in out5b.splitlines() if "κ ≤" in l]
    check(rc5 == 0 and _k5 == _k5b, "配置 cost.promise 不改变 κ", f"{_k5} vs {_k5b}")

    from policy import require_measured
    check(require_measured([], EXAMPLE) is False, "默认不强制实测")
    check(require_measured(["--require-measured"], EXAMPLE) is True, "--require-measured 生效")

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
