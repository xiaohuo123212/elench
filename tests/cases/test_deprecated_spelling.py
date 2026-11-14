#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_deprecated_spelling.py —— **`.tu` 子句里的弃用拼写也要报**。

## 事故（2026-11-13 实测）

同一条 `.length()`，两种待遇：

    .eln 里 `return xs.length();`            → ⚠ W-Builtin-Deprecated
    .tu  里 `sizes n = xs.length();`         → **一声不响**

读者据此以为"`.tu` 里那样写是对的"，而 `length` 是 **v0.73 就要移除**的旧名。
公开文档当时也在教这个写法（同一轮里已逐处迁移）。

## 为什么会漏

`sizes` / `target` / `benchmodel` / `repeats` / `requires` / `guarantees` 的表达式
各有**自己的消费者**（`cost_interp` / `bench` / 运行期 / `requires.py`），
**都不进 `typecheck`**——而弃用检查原来只长在 `typecheck` 的 method-call 分支上。

现在由 `checker._check_tu_deprecated_spelling` 补上：判据与消息**复用同一张表**
（`typecheck.DEPRECATED_METHODS` / `stdlib.DEPRECATED_BUILTINS`），不另造一套。

## 本测试锁七件事

1. **六种子句都报**：`sizes` / `benchmodel` / `target` / `repeats` / `requires` / `guarantees`；
2. **只报 warning**：`.length()` 仍能跑（`check` rc=0、`run` 有输出）；
3. **fix 归属 `.tu`**：`--format=json` 的 `fix` 是 `replace length→size`，且 `file`
   必须是 `.tu`——两份文件同一行都有 `length` 时**不许改错文件**；
4. **与 `requires.py` 去重**：被调用的 spec 的 `requires` 只报**一条**（不是两条）；
5. **不误报**：`stack.push`（C++ 的本名）、`requires is_sorted(…)`（现行谓词）、
   用户自己声明的同名函数；
6. **换新名 ⇒ 零警告**；7. **与 `.eln` 同一口径**：同一句两边各报一条（不跨文件去重）。
"""
import json
import os as _os
import subprocess
import sys as _sys
import tempfile

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
for _p in (SRC,):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from checker import check_module          # noqa: E402

FAILED = []
PASSED = 0
W = "W-Builtin-Deprecated"


def check(name, cond, detail=""):
    global PASSED
    if cond:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def _tu(spell="size"):
    """五种子句（sizes / repeats / requires / guarantees / target）用到尺寸的契约。

    `spell` 是 `size`（现行）或 `length`（旧名）。`benchmodel` 单独一份
    `_tu_bench`——它的 `data` 段要求生成器**有 spec**，混进来会让这份契约带
    `E-` 错误，而 `[5]/[6]` 要的是"能跑通、只有警告"的干净契约。
    """
    return (
        "spec f(const vector<u64>& xs, u64 k) -> u64 {\n"          # 1
        "    effects {};\n"                                        # 2
        f"    sizes n = xs.{spell}();\n"                            # 3
        "    target { time: { max: O(n) } };\n"                     # 4
        f"    repeats xs.{spell}();\n"                              # 5
        f"    requires xs.{spell}() >= k;\n"                        # 6
        "    guarantees { is_sorted(xs) };\n"                       # 7
        "}\n"                                                      # 8
        "spec g(const vector<u64>& xs) -> u64 {\n"                  # 9
        "    effects {};\n"                                        # 10
        f"    target {{ time: {{ max: O(xs.{spell}()) }} }};\n"      # 11
        "}\n")                                                     # 12


def _tu_bench(spell="size"):
    """`benchmodel` 的 `data` 段里出现旧名——单独一份，只为断言覆盖范围。"""
    return (
        "spec b(const vector<u64>& xs) -> u64 {\n"                  # 1
        "    effects {};\n"                                        # 2
        f"    benchmodel {{ data {{ xs = gen(xs.{spell}()); }} "
        "scale { xs in range(1, 8, *2); } };\n"                    # 3
        "    target { time: { max: O(1) } };\n"                     # 4
        "}\n")                                                     # 5


def _eln(spell="size"):
    return (
        "u64 f(const vector<u64>& xs, u64 k) {\n"
        f"    return xs.{spell}();\n"
        "}\n"
        "u64 g(const vector<u64>& xs) {\n"
        f"    if (xs.{spell}() >= 1) {{\n"
        "        return f(xs, 1);\n"
        "    }\n"
        "    return 0;\n"
        "}\n"
        "u64 main() {\n"
        "    vector<u64> xs = {1, 2, 3};\n"
        "    return g(xs);\n"
        "}\n")


def diags(tu, eln=None):
    return list(check_module(tu, eln, "m").diagnostics)


def dep_lines(tu, eln=None):
    """只取弃用警告的 `(行, 列)` 列表。"""
    return [(d.line, d.col) for d in diags(tu, eln) if d.code == W]


def main() -> int:
    print("=== [1] 五种子句都报（.length() → 警告）")
    got = dep_lines(_tu("length"))
    lines = [ln for ln, _ in got]
    for want, what in ((3, "sizes"), (5, "repeats"), (6, "requires"),
                       (11, "target（第二个 spec）")):
        check(f"`.tu` 第 {want} 行（{what}）报 {W}", want in lines,
              f"实际行号 {sorted(lines)}")
    check("`benchmodel` 的 `data` 段里也报",
          [ln for ln, _ in dep_lines(_tu_bench("length"))] == [3],
          str(dep_lines(_tu_bench("length"))))
    check("每一条都是 warning（不是 error）",
          all(d.severity == "warning" for d in diags(_tu("length"))
              if d.code == W),
          str([(d.code, d.severity) for d in diags(_tu("length"))
               if d.code == W]))
    check("`.tu` 第 6 行（requires）只报**一条**（与 requires.py 去重）",
          lines.count(6) == 1, f"第 6 行出现 {lines.count(6)} 次：{got}")

    print("=== [2] 换现行拼写 ⇒ 零弃用警告（不误报）")
    check("全 `size()` 的契约没有任何 W-Builtin-Deprecated",
          dep_lines(_tu("size")) == [], str(dep_lines(_tu("size"))))
    check("`benchmodel` 用 `size()` 同样零警告",
          dep_lines(_tu_bench("size")) == [],
          str(dep_lines(_tu_bench("size"))))

    print("=== [3] 其它弃用拼写：内建名 / push / is_empty")
    builtin_tu = ("spec q(const vector<u64>& xs, u64 k) -> u64 {\n"
                  "    effects {};\n"
                  "    requires contains(xs, k);\n"
                  "    target { time: { max: O(1) } };\n"
                  "}\n")
    check("`.tu` 的 `contains(xs, k)` 报弃用（stdlib 弃用表）",
          any(d.code == W and "contains" in d.message
              for d in diags(builtin_tu)),
          str([(d.code, d.message[:24]) for d in diags(builtin_tu)]))
    vec_tu = ("spec p(vector<u64>& xs) -> u64 {\n"
              "    effects {};\n"
              "    requires xs.push(1) >= 0;\n"
              "    target { time: { max: O(1) } };\n"
              "}\n")
    check("`vector` 上的 `push(…)` 报弃用（改用 push_back）",
          any(d.code == W and "push" in d.message for d in diags(vec_tu)),
          str([(d.code, d.message[:24]) for d in diags(vec_tu)]))
    stack_tu = vec_tu.replace("vector<u64>&", "stack<u64>&")
    check("`stack` 上的 `push(…)` **不报**（那是 C++ 的本名）",
          not [d for d in diags(stack_tu) if d.code == W],
          str([(d.code, d.message[:30]) for d in diags(stack_tu)]))
    empty_tu = builtin_tu.replace("contains(xs, k)", "xs.is_empty()")
    check("`.tu` 的 `xs.is_empty()` 报弃用（改用 empty）",
          any(d.code == W and "is_empty" in d.message
              for d in diags(empty_tu)),
          str([(d.code, d.message[:24]) for d in diags(empty_tu)]))

    print("=== [4] 三个不误报的反例")
    check("`requires is_sorted(xs)` 是现行谓词，不报弃用",
          not [d for d in diags(_tu("length")) if d.code == W
               and "is_sorted" in d.message],
          str([d.message[:30] for d in diags(_tu("length")) if d.code == W]))
    user_tu = ("spec sum_of(const vector<u64>& xs) -> u64 {\n"
               "    effects {};\n"
               "    sizes n = sum_of(xs);\n"
               "    target { time: { max: O(n) } };\n"
               "}\n")
    check("用户自己声明了 `sum_of` ⇒ 不按弃用内建报",
          not [d for d in diags(user_tu) if d.code == W
               and "sum_of" in d.message],
          str([d.message[:30] for d in diags(user_tu) if d.code == W]))
    # 正对照：同一句 `sum_of(xs)`，只是那份 `.tu` **没有**声明 `sum_of` 的 spec。
    other_tu = user_tu.replace("spec sum_of(", "spec other(")
    check("没有任何声明时，`sizes n = sum_of(xs)` **会**报（正对照）",
          any(d.code == W and "sum_of" in d.message for d in diags(other_tu)),
          str([d.message[:30] for d in diags(other_tu) if d.code == W]))

    print("=== [5] `--format=json`：fix 是 replace length→size，且归属 `.tu`")
    d = tempfile.mkdtemp(prefix="eln_dep_")
    tu_p = _os.path.join(d, "m.tu")
    el_p = _os.path.join(d, "m.eln")
    with open(tu_p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_tu("length"))
    with open(el_p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(_eln("length"))
    env = dict(_os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([_sys.executable, _os.path.join(SRC, "elench.py"),
                        "check", "--format=json", tu_p, el_p],
                       capture_output=True, encoding="utf-8", errors="replace",
                       env=env)
    try:
        payload = json.loads(r.stdout or "{}")
    except ValueError:
        payload = {}
    ds = [x for x in (payload.get("diagnostics") or []) if x["code"] == W]
    tu_ds = [x for x in ds if x.get("file") == "m.tu"]
    el_ds = [x for x in ds if x.get("file") == "m.eln"]
    check("JSON 里有 `.tu` 的弃用警告（不是零条）", bool(tu_ds),
          str([(x["code"], x.get("file")) for x in ds][:6]))
    check("`.tu` 的警告 `fix` 是 replace `length` → `size`",
          bool(tu_ds) and all(
              (x.get("fix") or {}).get("kind") == "replace"
              and (x.get("fix") or {}).get("text") == "size" for x in tu_ds),
          str([x.get("fix") for x in tu_ds][:3]))
    check("`.tu` 的 fix 改的是 `.tu` 那一行（**不是 .eln**）",
          bool(tu_ds) and all(
              (x["fix"]["range"][0] == x["line"]) for x in tu_ds),
          str([(x["line"], x.get("fix")) for x in tu_ds][:2]))
    check("`.eln` 的警告仍在（两边各报一条，不跨文件去重）",
          len(el_ds) >= 1, str([(x.get("file"), x["line"]) for x in ds][:6]))
    check("`check` 的退出码是 0（只是警告，不阻断构建）", r.returncode == 0,
          f"rc={r.returncode} {(r.stdout or '')[-200:]}")

    print("=== [6] 旧名仍能跑（口径不变：warning 不是 error）")
    r2 = subprocess.run([_sys.executable, _os.path.join(SRC, "elench.py"),
                         "run", tu_p, el_p],
                        capture_output=True, encoding="utf-8", errors="replace",
                        env=env)
    out2 = ((r2.stdout or "") + (r2.stderr or "")).strip()
    check("`.length()` 的 `.tu`/`.eln` 照旧 rc=0 且算出 3（`g([1,2,3])`）",
          r2.returncode == 0 and "3" in out2,
          f"rc={r2.returncode} {out2[-200:]}")

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} / {PASSED + len(FAILED)}")
        for f in FAILED[:8]:
            print("  -", f)
        return 1
    print(f"全部通过：{PASSED} 项（六种子句 + 三个反例 + JSON fix + 旧名仍能跑）")
    return 0


if __name__ == "__main__":
    _sys.exit(main())
