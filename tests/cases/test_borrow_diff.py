#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_borrow_diff.py —— 新（分支敏感）与旧（时间线近似）的**差分测试**

**为什么需要差分**

借用检查最危险的错是**漏报**——而漏报**不会让测试变红**：
程序照样编译、测试照样通过，只是心里那句"安全"不再成立。

所以这里做两件事：

1. **逐条对照**新旧两版对同一段程序的结论；
2. 对"**新报旧不报**"的每一条，**必须能说清它是不是真 UB**——
   是真 UB = 新分析抓到了漏报（好）；不是 = 新分析误报（坏）。

**另外一条硬约束（来自临时计划 §12.3）**：
**分析放行的程序，必须能用解释器真跑一遍**——放行却跑崩 = 严重缺陷。
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
import sys

from borrow import check_borrows, check_borrows_conservative
from interp import Interp
from parser import parse_source

_passed = 0
_failed = []

TU = "spec f(const vector<u64>& xs) -> u64 { effects {}; }"


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def both(code, tu=TU):
    new = check_borrows(parse_source(tu, "a.tu").module,
                        parse_source(code, "a.eln").module)
    old = check_borrows_conservative(parse_source(tu, "a.tu").module,
                                     parse_source(code, "a.eln").module)
    return new, old


def codes(ds):
    return sorted({c for c, _, _, _ in ds})


def main() -> int:
    print("[1] 回边漏报：循环里每轮先读后改——旧版看不见，新版必须看见")
    src = """u64! f(vector<u64>& xs) {
    const u64& r = xs.at(0);
    u64 s = 0;
    u64 i = 0;
    while (i < n) {
        s = s + r;
        xs.push(1);
        i = i + 1;
    }
    return s;
}"""
    new, old = both(src)
    check("E-Borrow-Conflict" in codes(new), "新版抓到了", str(codes(new)))
    check("E-Borrow-Conflict" in codes(old),
          "旧版这条也能抓（同一轮内顺序可见）——所以它不是差异点，如实记下",
          str(codes(old)))

    print("[2] 十个样例：两版都必须是 0 条（谁多报谁有 bug）")
    root = ROOT
    S = os.path.join(root, "文档", "样例")
    bad = []
    for b in ["calculator", "search", "sum", "stats", "merge",
              "paren", "count", "matmul", "lcs", "topk"]:
        tu = parse_source(io.open(os.path.join(S, b + ".tu"), encoding="utf-8").read(), b + ".tu").module
        ch = parse_source(io.open(os.path.join(S, b + ".eln"), encoding="utf-8").read(), b + ".eln").module
        n, o = len(check_borrows(tu, ch)), len(check_borrows_conservative(tu, ch))
        if n or o:
            bad.append((b, n, o))
    check(not bad, "十个样例两版都零诊断", str(bad))

    print("[3] 互斥分支：then 里改、else 里用——**不该报**（分支敏感的正确性）")
    src = """u64! f(vector<u64>& xs) {
    const u64& r = xs.at(0);
    u64 s = 0;
    if (n > 0) {
        xs.push(1);
    } else {
        s = s + r;
    }
    return s;
}"""
    new, old = both(src)
    check("E-Borrow-Conflict" not in codes(new),
          "新版不报（两支互斥：push 与读 r 永不同时发生）", str(codes(new)))
    check("E-Borrow-Conflict" in codes(old),
          "旧版会误报（按时间线把 else 的读排在 then 的改之后）", str(codes(old)))

    print("[4] 放行即可跑：新分析不报的程序，解释器必须能跑通")
    src = """u64! f(const vector<u64>& xs) {
    const u64& r = xs.at(0);
    const u64 a = r;
    return a + 1;
}"""
    new, _ = both(src)
    ok_run = True
    detail = ""
    try:
        mod = parse_source(src, "a.eln").module
        Interp(mod, None).call("f", [[11, 22, 33]])
    except Exception as exc:                       # noqa: BLE001
        ok_run = False
        detail = f"{type(exc).__name__}: {exc}"
    check(not new and ok_run, "不报且能跑", f"{codes(new)} / {detail}")

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
