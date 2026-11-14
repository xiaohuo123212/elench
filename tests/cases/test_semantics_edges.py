#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_semantics_edges.py —— 批次 5「语义与边界」的 8 条回归。

每条都同时验证两件事：

1. **该报的报了**（错误的输入会被拒绝，且诊断指得到病因）；
2. **正常的不误报**（相邻的合法写法仍然跑得通）。

这 8 条有一个共同点：以前它们**都不是崩溃**，而是"宿主语义泄漏"
（Python 负下标、Python 列表长度、Python 解码器）或"静默选错"
（内建压过用户函数、`let` 被当类型名）。静默比崩溃危险，所以这里
全部断言**可观察的输出**，不只断言退出码。
"""
import os
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
EL = os.path.join(ROOT, "src", "elench.py")

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(args, cwd):
    """跑一次 CLI，返回 (退出码, stdout+stderr)。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, EL] + args, cwd=cwd, env=env,
                       capture_output=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def write(d, name, text, encoding="utf-8"):
    path = os.path.join(d, name)
    with open(path, "w", encoding=encoding, newline="\n") as f:
        f.write(text)
    return path


SPEC1 = ("spec main() -> u64 {\n"
         "    effects {};\n"
         "    target { time: { max: O(1) }, };\n"
         "}\n")


def mod(d, eln, tu=SPEC1):
    write(d, "m.tu", tu)
    write(d, "m.eln", eln)


# ---------------------------------------------------------------- 18
def test_u64_underflow_and_negative_index():
    print("[18] u64 下溢回绕 + 负下标必须报错")
    d = tempfile.mkdtemp(prefix="eln_s18_")

    mod(d, "pub u64 main() {\n"
           "    u64 x = 0;\n"
           "    return x - 1;\n"
           "}\n")
    rc, out = run(["run", "m"], d)
    check("u64 的 0-1 回绕可见（18446744073709551615）",
          rc == 0 and "18446744073709551615" in out, f"rc={rc} out={out}")

    mod(d, "pub u64 main() {\n"
           "    vector<u64> xs = {1, 2, 3};\n"
           "    return xs[-1];\n"
           "}\n")
    rc, out = run(["run", "m"], d)
    check("负下标读 → 报错（不是 Python 的末尾元素）",
          rc != 0 and "E-Index-Negative" in out, f"rc={rc} out={out}")

    # 这条是批次 5 里最危险的：写负下标会**静默改坏**最后一个元素。
    mod(d, "pub u64 main() {\n"
           "    vector<u64> xs = {1, 2, 3};\n"
           "    xs[-1] = 99;\n"
           "    return xs[2];\n"
           "}\n")
    rc, out = run(["run", "m"], d)
    check("负下标写 → 报错（不写坏 xs 的最后一个元素）",
          rc != 0 and "E-Index-Negative" in out and "99" not in out,
          f"rc={rc} out={out}")

    # 正对照：合法下标照常读写。
    mod(d, "pub u64 main() {\n"
           "    vector<u64> xs = {1, 2, 3};\n"
           "    xs[1] = 99;\n"
           "    return xs[0] + xs[2];\n"
           "}\n")
    rc, out = run(["run", "m"], d)
    check("正下标读写不误报（1+3=4）", rc == 0 and out.strip() == "4",
          f"rc={rc} out={out}")


# ---------------------------------------------------------------- 19
def test_division_returns_integer():
    print("[19] 整数 `/` 返回普通整数：`7 / 2` 可直接 return；除零是运行期陷阱")
    d = tempfile.mkdtemp(prefix="eln_s19_")
    mod(d, "pub u64 main() {\n    return 7 / 2;\n}\n")
    rc, out = run(["check", "m"], d)
    check("`7 / 2` 在 `-> u64` 函数里直接通过（不再返回 Result）",
          rc == 0 and "E-Type" not in out, f"rc={rc} out={out}")
    check("不再是 E-Type-Return", "E-Type-Return" not in out, out)
    check("不再是 E-Type-OptionToScalar", "E-Type-OptionToScalar" not in out, out)
    check("诊断里不再要求 `error Arithmetic;`", "error Arithmetic;" not in out, out)
    check("诊断里不再出现 `Result<…, Arithmetic>`",
          "Result<i64, Arithmetic>" not in out
          and "Result<u64, Arithmetic>" not in out, out)

    # 正对照：可失败签名（`-> u64!`）同样通过——除法不再贡献失败。
    tu = ("error Arithmetic;\n"
          "spec main() -> u64! {\n"
          "    effects {};\n"
          "    target { time: { max: O(1) }, };\n"
          "}\n")
    mod(d, "pub u64! main() {\n    return 7 / 2;\n}\n", tu)
    rc, out = run(["check", "m"], d)
    check("改成 `-> u64!` 后也通过（不是靠解包）", rc == 0, f"rc={rc} out={out}")

    # 运行期：`7 / 2 = 3`（向零截断）；除零是**语言诊断**，不是 Err / 宿主异常。
    mod(d, "pub u64 main() {\n    return 7 / 2;\n}\n")
    rc, out = run(["run", "m"], d)
    check("运行期 `7 / 2 = 3`", rc == 0 and out.strip() == "3", f"rc={rc} out={out}")

    mod(d, "pub u64 main() {\n    u64 z = 0;\n    return 7 / z;\n}\n")
    rc, out = run(["run", "m"], d)
    check("除零 → rc=1 且报 E-Div-ByZero", rc == 1 and "E-Div-ByZero" in out,
          f"rc={rc} out={out}")
    check("除零不是宿主异常（无 ZeroDivisionError / Traceback）",
          "ZeroDivisionError" not in out and "Traceback" not in out, out)
    check("除零消息给出 `requires b != 0;` 修法", "requires b != 0" in out, out)


# ---------------------------------------------------------------- 20
S4_TU = ("spec sum4(const array<u64, 4>& xs) -> u64 {\n"
         "    effects {};\n"
         "    target { time: { max: O(1) }, };\n"
         "}\n"
         "spec sum_any(const vector<u64>& xs) -> u64 {\n"
         "    effects {};\n"
         "    target { time: { max: O(xs.length()) }, };\n"
         "}\n")
S4_ELN = ("pub u64 sum4(const array<u64, 4>& xs) {\n"
          "    return xs[0] + xs[1] + xs[2] + xs[3];\n"
          "}\n\n"
          "pub u64 sum_any(const vector<u64>& xs) {\n"
          "    u64 s = 0;\n"
          "    for (auto x : xs) { s = s + x; }\n"
          "    return s;\n"
          "}\n")


def test_vec_length():
    print("[20] 定长形参的 --vec 长度必须校验")
    d = tempfile.mkdtemp(prefix="eln_s20_")
    write(d, "m.tu", S4_TU)
    write(d, "m.eln", S4_ELN)

    rc, out = run(["run", "m", "--fn=sum4", "--vec=1,2"], d)
    check("给 2 个 → 明确参数错误（不是 IndexError）",
          rc == 2 and "需要 4 个元素" in out and "IndexError" not in out,
          f"rc={rc} out={out}")

    rc, out = run(["run", "m", "--fn=sum4", "--vec=1,2,3,4,5"], d)
    check("给 5 个 → 明确参数错误（不静默截断）",
          rc == 2 and "需要 4 个元素" in out and "10" not in out,
          f"rc={rc} out={out}")

    rc, out = run(["run", "m", "--fn=sum4", "--vec=1,2,3,4"], d)
    check("正好 4 个 → 10", rc == 0 and out.strip() == "10",
          f"rc={rc} out={out}")

    # `vector<T>` 没有声明长度，不能学定长数组去卡。
    rc, out = run(["run", "m", "--fn=sum_any", "--vec=1,2"], d)
    check("vector 形参任意长度不误报（1+2=3）",
          rc == 0 and out.strip() == "3", f"rc={rc} out={out}")


# ---------------------------------------------------------------- 21
def test_stats_vec():
    print("[21] stats 支持 --vec（与 run 同一套注入）")
    d = tempfile.mkdtemp(prefix="eln_s21_")
    write(d, "m.tu", S4_TU)
    write(d, "m.eln", S4_ELN)

    rc, out = run(["stats", "m", "sum4", "--vec=5,5,5,5"], d)
    check("stats --vec=5,5,5,5 → 20",
          rc == 0 and "→ 20" in out and "13" not in out,
          f"rc={rc} out={out}")

    rc, out = run(["stats", "m", "sum4", "--vec=1,2"], d)
    check("stats 长度不符 → 参数错误（不是跑 13）",
          rc == 2 and "需要 4 个元素" in out, f"rc={rc} out={out}")

    rc, out = run(["stats", "m", "sum_any", "--vec=1,2,3,4"], d)
    check("stats --vec 对 vector 也生效（=10）",
          rc == 0 and "→ 10" in out, f"rc={rc} out={out}")


# ---------------------------------------------------------------- 22
def test_non_utf8():
    print("[22] 非 UTF-8 文件 → 诊断（不是 traceback）")
    d = tempfile.mkdtemp(prefix="eln_s22_")
    write(d, "m.tu", SPEC1)
    body = ("// 中文注释\n"
            "pub u64 main() {\n"
            "    return 1;\n"
            "}\n")
    with open(os.path.join(d, "m.eln"), "wb") as f:
        f.write(body.encode("gbk"))

    for cmd in (["check", "m"], ["run", "m"], ["fmt", "m.eln"]):
        rc, out = run(cmd, d)
        label = cmd[0]
        check(f"{label} 非 UTF-8 → 非零退出", rc != 0, f"rc={rc}")
        check(f"{label} 非 UTF-8 → 明确说「文件不是 UTF-8 编码」",
              "文件不是 UTF-8 编码" in out, out[-300:])
        check(f"{label} 非 UTF-8 → 没有 traceback",
              "Traceback" not in out, out[-300:])
        check(f"{label} 非 UTF-8 → 指出路径 m.eln", "m.eln" in out, out[-300:])

    # 正对照：同样的内容用 UTF-8 存，照常通过。
    write(d, "m.eln", body)
    rc, out = run(["check", "m"], d)
    check("同一份内容用 UTF-8 存 → 通过", rc == 0, f"rc={rc} out={out}")


# ---------------------------------------------------------------- 23
def test_user_shadows_builtin():
    print("[23] 用户函数与内建同名 → 用户定义优先（不静默忽略）")
    d = tempfile.mkdtemp(prefix="eln_s23_")
    tu = ("spec sum_of(const vector<u64>& xs) -> u64 {\n"
          "    effects {};\n"
          "    target { time: { max: O(xs.size()) }, };\n"
          "}\n" + SPEC1)
    write(d, "m.tu", tu)
    write(d, "m.eln",
          "pub u64 sum_of(const vector<u64>& xs) {\n"
          "    return 999;\n"
          "}\n\n"
          "pub u64 main() {\n"
          "    vector<u64> xs = {1, 2, 3, 4};\n"
          "    return sum_of(xs);\n"
          "}\n")
    rc, out = run(["check", "m"], d)
    check("用户定义 sum_of 不再触发 W-Builtin-Deprecated",
          "W-Builtin-Deprecated" not in out, out)
    rc, out = run(["run", "m"], d)
    check("调用落到用户定义的 sum_of（999，不是内建的 10）",
          rc == 0 and out.strip() == "999", f"rc={rc} out={out}")

    # 正对照：没有用户实现时，内建 sum_of 照常可用（1+2+3+4=10）。
    # 注意不能给 sum_of 写 spec——spec 无实现在本语言里表示"外部实现"，
    # 会走 FFI；内建的名字本来就不需要 spec。
    write(d, "m.tu", SPEC1)
    write(d, "m.eln",
          "pub u64 main() {\n"
          "    vector<u64> xs = {1, 2, 3, 4};\n"
          "    return sum_of(xs);\n"
          "}\n")
    rc, out = run(["run", "m"], d)
    check("没有用户定义时内建 sum_of 仍然工作（10）",
          rc == 0 and out.strip() == "10", f"rc={rc} out={out}")


# ---------------------------------------------------------------- 24
def test_let_is_old_keyword():
    print("[24] `let` 被当类型名 → 改成 E-Decl-OldKeyword + 改写提示")
    d = tempfile.mkdtemp(prefix="eln_s24_")
    mod(d, "pub u64 main() {\n"
           "    let n = 7;\n"
           "    return n;\n"
           "}\n")
    rc, out = run(["check", "m"], d)
    check("报 E-Decl-OldKeyword", rc != 0 and "E-Decl-OldKeyword" in out,
          f"rc={rc} out={out}")
    check("提示改 `u64 n = 7;`", "u64 n = 7;" in out, out)
    check("不再报「声明为 let」的 E-Type-Assign",
          "E-Type-Assign" not in out, out)
    check("不再级联「实际 let」的 E-Type-Return",
          "实际 let" not in out, out)

    # 正对照：按提示改写后通过并返回 7。
    mod(d, "pub u64 main() {\n"
           "    u64 n = 7;\n"
           "    return n;\n"
           "}\n")
    rc, out = run(["run", "m"], d)
    check("`u64 n = 7;` 通过且返回 7", rc == 0 and out.strip() == "7",
          f"rc={rc} out={out}")


# ---------------------------------------------------------------- 25
def test_eln_extension_in_messages():
    print("[25] 错误文案不再用旧扩展名 .ai")
    d = tempfile.mkdtemp(prefix="eln_s25_")
    cases = [
        ("struct", "struct P { x: u64 }\n"),
        ("const", "const u64 K = 1;\n"),
        ("decl", "error MyErr;\n"),
    ]
    for label, decl in cases:
        write(d, "m.tu", SPEC1)
        write(d, "m.eln", decl + "pub u64 main() { return 0; }\n")
        rc, out = run(["check", "m"], d)
        check(f"{label} 文案用 .eln",
              rc != 0 and ".eln 中不得出现" in out and ".ai 中" not in out,
              f"rc={rc} out={out}")


def main() -> int:
    test_u64_underflow_and_negative_index()
    print()
    test_division_returns_integer()
    print()
    test_vec_length()
    print()
    test_stats_vec()
    print()
    test_non_utf8()
    print()
    test_user_shadows_builtin()
    print()
    test_let_is_old_keyword()
    print()
    test_eln_extension_in_messages()
    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
