#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""test_check_json.py —— `check --json` 的结构化诊断与可机械应用的 `fix`

判据（P6-2）：

1. `--json` 时 **stdout 只有 JSON**（`json.loads` 直接吃）；
2. 诊断**顺序与人类输出一致**；
3. 高频码给出**能直接套用的 `fix`**（range 半开、1-based）；
4. 同一输入两次输出**逐字节相同**；
5. 不给 `--json` 的人类输出不受影响（这里只锁"格式没变成 JSON"，
   逐字节对比用改前快照另做——见提交说明）。
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DSH = os.path.join(ROOT, "src", "dsh.py")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8",
           PYTHONPATH=os.path.join(ROOT, "src")
           + os.pathsep + os.environ.get("PYTHONPATH", ""))

FAILS = []


def check(cond, what):
    if cond:
        print(f"  ✓ {what}")
    else:
        FAILS.append(what)
        print(f"  ✗ {what}")


def write(d, name, text):
    p = os.path.join(d, name)
    io.open(p, "w", encoding="utf-8", newline="\n").write(text)
    return p


def run(path, *flags):
    return subprocess.run([sys.executable, "-B", DSH, "check", path] + list(flags),
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", env=ENV)


def as_json(path, flag="--json"):
    r = run(path, flag)
    try:
        return r, json.loads(r.stdout)
    except Exception as exc:                            # noqa: BLE001
        print(f"    stdout={r.stdout[:300]!r}\n    stderr={r.stderr[:300]!r}")
        raise AssertionError(f"stdout 不是纯 JSON：{exc}")


def find(data, code):
    for d in data.get("diagnostics", []):
        if d.get("code") == code:
            return d
    return None


def apply_fix(src, fix):
    """把 `fix` 套到源码上：range 是 1-based 半开区间。"""
    assert fix and fix.get("kind") == "replace", fix
    line, col, end_line, end_col = fix["range"]
    lines = src.splitlines(keepends=True)
    start = sum(len(x) for x in lines[:line - 1]) + (col - 1)
    end = sum(len(x) for x in lines[:end_line - 1]) + (end_col - 1)
    return src[:start] + fix["text"] + src[end:]


def human_codes(text):
    out = []
    for ln in text.splitlines():
        m = re.match(r"^\s*[✗⚠]\s+(\S+)\s+@\d+:\d+", ln)
        if m:
            out.append(m.group(1))
    return out


def main() -> int:
    d = tempfile.mkdtemp(prefix="dsh_json_")

    word = write(d, "word.eln",
                 "u64 f(u64 a, u64 b) {\n"
                 "  if (a > 0 and b > 0) { return 1; }\n"
                 "  return 0;\n"
                 "}\n")
    val = write(d, "val.eln",
                "u64 f() {\n"
                "  val x = 1;\n"
                "  return x;\n"
                "}\n")
    typo = write(d, "typo.eln",
                 "u64 f(xs: vector<u64>) {\n"
                 "  return xs.att(0);\n"
                 "}\n")
    forin = write(d, "forin.eln",
                  "u64 f(xs: vector<u64>) {\n"
                  "  u64 s = 0;\n"
                  "  for (x in xs) { s = s + x; }\n"
                  "  return s;\n"
                  "}\n")
    unary = write(d, "unary.eln",
                  "u64 f() {\n"
                  "  u64 x = 1;\n"
                  "  u64 y = ++x;\n"
                  "  return y;\n"
                  "}\n")
    parse = write(d, "parse.eln",
                  "u64 f() {\n"
                  "  u64 x = ;\n"
                  "  return x;\n"
                  "}\n")
    oldfn = write(d, "oldfn.eln", "fn f() -> u64 {\n  return 1;\n}\n")
    dep = write(d, "dep.eln",
                "u64 f(xs: vector<u64>) {\n"
                "  return sum_of(xs);\n"
                "}\n")

    # ---- 1. stdout 只有 JSON + 基本 schema
    print("[1] --json：stdout 只有 JSON")
    r, data = as_json(word)
    check(data.get("version") == 1, "version == 1")
    check(isinstance(data.get("ok"), bool) and data["ok"] is False, "ok == false")
    check(isinstance(data.get("diagnostics"), list), "diagnostics 是数组")
    check("✗" not in r.stdout and "module " not in r.stdout,
          "stdout 里没有人类文案")

    # ---- 2. and → && 的 fix
    print("[2] E-Logic-WordOp 的 fix")
    d0 = find(data, "E-Logic-WordOp")
    check(d0 is not None, "有 E-Logic-WordOp")
    check(d0["fix"] == {"kind": "replace", "text": "&&",
                        "range": [2, 13, 2, 16]}, f"fix == &&：{d0['fix']}")
    check(d0["file"] == "word.eln", f"file 是相对路径：{d0['file']}")
    check((d0["end_line"], d0["end_col"]) == (2, 16), "end 与 fix.range 一致")
    check("[.eln]" not in d0["message"], "message 已去掉 [.eln] 前缀")
    fixed = apply_fix(io.open(word, encoding="utf-8").read(), d0["fix"])
    check("a > 0 && b > 0" in fixed, "应用 fix 后得到 &&")
    p2 = write(d, "word_fixed.eln", fixed)
    _, data2 = as_json(p2)
    check(find(data2, "E-Logic-WordOp") is None, "应用后不再报 E-Logic-WordOp")

    # ---- 3. 诊断顺序 = 人类输出顺序
    print("[3] 顺序与人类输出一致")
    rh = run(word)
    check(human_codes(rh.stdout) == [x["code"] for x in data["diagnostics"]],
          f"顺序一致：{human_codes(rh.stdout)}")

    # ---- 4. 确定性
    print("[4] 确定性")
    a = run(word, "--json")
    b = run(word, "--json")
    check(a.stdout.encode("utf-8") == b.stdout.encode("utf-8"),
          "两次 --json 逐字节相同")
    check(a.returncode == b.returncode, "退出码也相同")

    # ---- 5. --format=json 是同一个结构
    print("[5] --format=json 同一份输出")
    c = run(word, "--format=json")
    check(c.stdout == a.stdout, "--format=json 与 --json 逐字节相同")

    # ---- 6. E-Decl-OldKeyword：val → const u64
    print("[6] E-Decl-OldKeyword 的 fix")
    _, vd = as_json(val)
    dv = find(vd, "E-Decl-OldKeyword")
    check(dv is not None, "有 E-Decl-OldKeyword")
    check(dv["fix"] is not None and dv["fix"]["text"] == "const u64",
          f"fix 文本是 const u64：{dv and dv['fix']}")
    vfixed = apply_fix(io.open(val, encoding="utf-8").read(), dv["fix"])
    check("const u64 x = 1;" in vfixed, "应用后得到 const u64 x = 1;")
    pv = write(d, "val_fixed.eln", vfixed)
    _, vd2 = as_json(pv)
    check(find(vd2, "E-Decl-OldKeyword") is None, "应用后不再报旧关键字")

    # ---- 7. E-Call-UnknownMethod：无 fix，有 suggestion
    print("[7] E-Call-UnknownMethod 的 suggestion")
    _, td = as_json(typo)
    dt = find(td, "E-Call-UnknownMethod")
    check(dt is not None, "有 E-Call-UnknownMethod")
    check(dt["fix"] is None, "不给 fix（换名字不一定是本意）")
    check(dt["suggestion"] == "at",
          f"suggestion 是最近合法名（编辑距离 1）：{dt and dt['suggestion']}")

    # ---- 8. 结构性/运行期问题：不给 fix
    print("[8] 不给 fix 的码")
    for name, path, code in (("E-For-InRemoved", forin, "E-For-InRemoved"),
                             ("E-Unary-Unsupported", unary, "E-Unary-Unsupported"),
                             ("E-Parse-*", parse, "E-Parse-ExpectedExpr")):
        _, dd = as_json(path)
        item = find(dd, code)
        check(item is not None and item["fix"] is None, f"{name}：fix 为 null")

    # ---- 9. E-Func-KeywordRemoved：无 fix，有 suggestion
    print("[9] E-Func-KeywordRemoved 的 suggestion")
    _, fd = as_json(oldfn)
    df = find(fd, "E-Func-KeywordRemoved")
    check(df is not None, "有 E-Func-KeywordRemoved")
    check(df["fix"] is None, "不给 fix（要删关键字）")
    check(bool(df["suggestion"]), f"有 suggestion：{df and df['suggestion']}")

    # ---- 10. W-Builtin-Deprecated：代码片段替换 → 不给 replace
    print("[10] W-Builtin-Deprecated（代码片段型）")
    _, pd = as_json(dep)
    dp = find(pd, "W-Builtin-Deprecated")
    check(dp is not None, "有 W-Builtin-Deprecated")
    if dp["fix"] is not None:
        check(dp["fix"]["kind"] == "replace"
              and re.fullmatch(r"[A-Za-z_]\w*", dp["fix"]["text"] or ""),
              "若给 fix，一定是裸标识符替换")
    else:
        check(True, "替换是代码片段，不给 fix（正确）")

    # ---- 11. E-Type-OldName：Option → optional（结构化层）
    #
    # 这个码由 typecheck 在 P4-0 抛出；本测试直接构造诊断，保证
    # "码一出现，fix 就对"——不依赖那个改动先落地。
    print("[11] E-Type-OldName 的 fix（构造诊断）")
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from checker import CheckResult, Diag
    import diag_json
    opt = write(d, "opt.eln", "u64 f(x: Option<u64>) {\n  return 1;\n}\n")
    res = CheckResult(module="opt", diagnostics=[
        Diag("E-Type-OldName", "类型名 `Option` 已下线：改写成 `optional`", 1, 10)])
    payload = diag_json.payload(res, None, opt)
    do = payload["diagnostics"][0]
    check(do["fix"] is not None and do["fix"]["text"] == "optional",
          f"Option → optional：{do['fix']}")
    check(do["fix"]["range"] == [1, 10, 1, 16],
          f"range 覆盖 Option：[1,10,1,16] vs {do['fix']['range']}")
    ofixed = apply_fix(io.open(opt, encoding="utf-8").read(), do["fix"])
    check("x: optional<u64>" in ofixed, "应用后得到 optional")

    # ---- 12. W-Builtin-Deprecated（方法改名）：push → push_back
    #
    # P4-0 的旧名表在 `typecheck.DEPRECATED_METHODS`；本树若还没落地，
    # 这里**临时挂一份**验证"表一出现，fix 就对"，结束后恢复。
    print("[12] W-Builtin-Deprecated 方法改名（构造诊断）")
    import typecheck
    added = not hasattr(typecheck, "DEPRECATED_METHODS")
    if added:
        typecheck.DEPRECATED_METHODS = {"push": "push_back"}
    try:
        push = write(d, "push.eln",
                     "u64 f(xs: vector<u64>) {\n"
                     "  xs.push(1);\n"
                     "  return xs.length();\n"
                     "}\n")
        res2 = CheckResult(module="push", diagnostics=[
            Diag("W-Builtin-Deprecated", "`push` 已弃用（v0.73 移除）：改用 `.push_back()`",
                 2, 5, severity="warning")])
        p3 = diag_json.payload(res2, None, push)["diagnostics"][0]
        check(p3["fix"] is not None and p3["fix"]["text"] == "push_back",
              f"push → push_back：{p3['fix']}")
        check(p3["fix"]["range"] == [2, 6, 2, 10],
              f"range 覆盖方法名：[2,6,2,10] vs {p3['fix']['range']}")
        check(p3["suggestion"] == "push_back", "suggestion 同新名")
    finally:
        if added:
            del typecheck.DEPRECATED_METHODS

    # ---- 13. 人类输出没被 JSON 分支污染
    print("[13] 人类输出仍是人读文本")
    rh = run(word)
    check("E-Logic-WordOp" in rh.stdout and '"version"' not in rh.stdout,
          "人类输出里有码、没有 JSON 字段")
    check(rh.returncode == 1, "有 error 时退出码 1")

    print()
    if FAILS:
        print(f"✗ {len(FAILS)} 条失败：")
        for f in FAILS:
            print(f"    - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
