"""分配契约与尺寸量语义（批次 2 + 批次 3）。

**批次 2**：容器增长必须计入分配，否则 `allocation: { max: 0 }` 对任何程序都通过。
**批次 3**：`O(n)` 要能用、`main` 的 target 不许静默、标量参数不是尺寸量、
无法判定时不打印比值。

跑法：`python tests/cases/test_alloc_and_sizes.py`，退出码 0 即通过。
"""

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DSH = os.path.join(ROOT, "src", "dsh.py")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

FAILED = []


def _run(eln, tu, *args):
    d = tempfile.mkdtemp(prefix="elench-alloc-")
    for name, text in (("m.eln", eln), ("m.tu", tu)):
        with open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    r = subprocess.run([sys.executable, "-B", DSH] + list(args), cwd=d,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=ENV)
    return (r.stdout or "") + (r.stderr or "")


def check(name, ok, detail=""):
    if ok:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name}  {detail[:200]}")


PUSH_LOOP = """pub u64 f(u64 n) {
    vector<u64> v;
    u64 i = 0;
    while (i < n) { v.push(i); i = i + 1; }
    return v.length();
}
"""
TU_LOOP = ("spec f(u64 n) -> u64 { effects { alloc }; "
           "target { time: { max: O(n) }, allocation: { max: 0 }, }; }\n")


def main() -> int:
    # ---- 批次 2：push 计入分配 ----
    out = _run(PUSH_LOOP, TU_LOOP, "cost", "m")
    check("2a push 循环 → 分配不再是 0", "alloc ≤ n" in out, out)
    check("2b push 循环 → 判为债务（▲）", "▲" in out or "超出 target" in out, out)

    out = _run("""pub u64 f() {
    map<u64, u64> m;
    m.insert(1, 1); m.insert(2, 2); m.insert(3, 3); m.insert(4, 4);
    return m.length();
}
""", "spec f() -> u64 { effects { alloc }; "
     "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n", "cost", "m")
    check("2c map insert → 分配有上界", "alloc ≤ 0" not in out.replace("target 0", ""), out)

    out = _run("""pub u64 f() {
    map<u64, u64> m;
    m.insert(1, 1); m.insert(2, 2); m.insert(3, 3); m.insert(4, 4);
    return m.length();
}
""", "spec f() -> u64 { effects { alloc }; "
     "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n", "stats", "m", "f")
    check("2d map insert 4 次 → 运行期分配 4",
          re.search(r"分配数\s+4", out) is not None, out)

    out = _run("""pub u64 f() {
    set<u64> s;
    s.insert(1); s.insert(2); s.insert(3);
    return s.length();
}
""", "spec f() -> u64 { effects { alloc }; "
     "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n", "stats", "m", "f")
    check("2e set insert 3 次 → 运行期分配 3",
          re.search(r"分配数\s+3", out) is not None, out)

    # ---- 批次 3.1：O(n) 绑定到唯一容器形参 ----
    out = _run("""pub u64 f(const vector<u64>& xs) {
    u64 s = 0;
    for (auto x : xs) { s = s + x; }
    return s;
}
""", "spec f(const vector<u64>& xs) -> u64 { effects {}; "
     "target { time: { max: O(n) }, allocation: { max: 0 }, }; }\n", "cost", "m")
    check("3.1a O(n) 能判定（不再说'没有 time 的 target'）",
          "没有 time 的 target" not in out, out)

    # ---- 批次 3.2：main 无参 + O(n) → 无法判定，不许假装达成 ----
    out = _run("pub u64 main() { return 1; }\n",
               "spec main() -> u64 { effects {}; "
               "target { time: { max: O(n) }, allocation: { max: 0 }, }; }\n", "cost", "m")
    check("3.2a main 的 O(n) 不会静默判达成", "✓ main" not in out or "无法判定" in out, out)
    check("3.2b 明确说无法判定", "无法判定" in out, out)

    # ---- 批次 3.3：标量下标参数可用 ----
    out = _run("""pub u64 f(const vector<u64>& xs, u64 lo, u64 hi) {
    u64 s = 0;
    u64 i = lo;
    while (i < hi) { s = s + xs.at(i); i = i + 1; }
    return s;
}
""", "spec f(const vector<u64>& xs, u64 lo, u64 hi) -> u64 { effects {}; "
     "target { time: { max: O(hi - lo) }, allocation: { max: 0 }, }; }\n", "cost", "m")
    check("3.3a O(hi - lo) 能判定", "没有 time 的 target" not in out, out)
    # **P1（2026-11-08）**：κ 侧的标量形参现在也渲染成裸符号（`hi`），
    # 不再是 `n(hi)`。两侧尺寸量同名，判定与显示都一致。
    check("3.3b κ 侧渲染裸符号 hi（不再是 n(hi)）", "n(hi)" not in out, out)

    # ---- 批次 4：**形参名叫 `n`** 时契约不许凭空消失 ----
    #
    # 旧洞（2026-11-09）：`_target_cost` 先判 `nm == "n"`（"唯一容器的长度"），
    # 形参恰好叫 `n` 时先命中它；没有容器形参 → 返回 None → `_find_slot`
    # 静默跳过 → `cost` 只说"spec 没有 time 的 target"，`check` 一声不吭。
    # **不是算错，是契约被丢了**：用户以为在被检查，其实这一句没了。
    N_IMPL = """pub u64 f(u64 n) {
    u64 s = 0;
    u64 i = 0;
    while (i < n) { s = s + i; i = i + 1; }
    return s;
}
"""
    N_TU = ("spec f(u64 n) -> u64 { effects {}; "
            "target { time: { max: O(n) }, allocation: { max: 0 }, }; }\n")
    out = _run(N_IMPL, N_TU, "cost", "m")
    check("4a 形参名就叫 n：O(n) 仍判出结论（契约没消失）",
          "没有 time 的 target" not in out and "达成" in out, out)
    out = _run(N_IMPL, N_TU, "check", "m")
    check("4b 形参名就叫 n：check 仍是干净的", "E-Target-BadForm" not in out, out)

    # 裸 `n` **绑不上**（两个容器形参）→ 必须报错，不许当没写
    AB_IMPL = """pub u64 f(const vector<u64>& a, const vector<u64>& b) {
    u64 s = 0;
    for (auto x : a) { s = s + x; }
    return s;
}
"""
    AB_TU = ("spec f(const vector<u64>& a, const vector<u64>& b) -> u64 { effects {}; "
             "target { time: { max: O(n) }, allocation: { max: 0 }, }; }\n")
    out = _run(AB_IMPL, AB_TU, "cost", "m")
    check("4c 裸 n 绑不上 → cost 报错（不再静默丢弃）", "target 解析失败" in out, out)
    check("4d 裸 n 绑不上 → 判决里说明是「解析失败」不是「没写」",
          "不是没写" in out, out)
    out = _run(AB_IMPL, AB_TU, "check", "m")
    check("4e 裸 n 绑不上 → check 出 E-Target-BadForm",
          "E-Target-BadForm" in out, out)

    # 形状坏（下标不是尺寸量）→ 同样报错，而不是"没有 target"
    out = _run("""pub u64 f(const vector<u64>& xs) {
    u64 s = 0;
    for (auto x : xs) { s = s + x; }
    return s;
}
""", ("spec f(const vector<u64>& xs) -> u64 { effects {}; "
      "target { time: { max: O(xs[0]) }, allocation: { max: 0 }, }; }\n"),
               "check", "m")
    check("4f O(xs[0]) → check 报 E-Target-BadForm", "E-Target-BadForm" in out, out)

    # ---- 批次 5：`sizes` 子句（G1-b）——**绑了必须真的生效** ----
    #
    # 旧洞（2026-11-09）：`sizes` 能解析、但 `src/` 里没有任何消费者。
    # `sizes table = n + 1;` 之后 target 里的 `table` 只是一个自由尺寸量
    # （渲染成 `n(table)`），判定退化成 ⊤——契约看着更精确，其实一个字没生效。
    SIZES_IMPL = """pub u64 pi_sieve(u64 n) {
    vector<u8> table;
    for (u64 i = 0; i < n; i = i + 1) { table.push(0); }
    table.push(0);
    return table.length();
}
"""
    out = _run(SIZES_IMPL,
               "spec pi_sieve(u64 n) -> u64 { effects { alloc }; "
               "sizes table = n + 1; "
               "target { time: { max: O(n * log(log(n))) }, "
               "allocation: { max: O(table) }, }; }\n", "cost", "m")
    check("5.1 sizes 绑定进了 target（`O(table)` 展开成 `n + 1`）",
          "(target n + 1)" in out and "达成" in out, out)
    check("5.1b sizes 名字不再渲染成自由尺寸量 n(table)",
          "n(table)" not in out, out)

    out = _run(SIZES_IMPL,
               "spec pi_sieve(u64 n) -> u64 { effects { alloc }; "
               "sizes table = 1; "
               "target { allocation: { max: O(table) }, }; }\n", "cost", "m")
    check("5.2 绑定量写小 → 判超标（绑定真的参与判定）",
          "(target 1)" in out and ("▲" in out or "超出 target" in out), out)

    out = _run(SIZES_IMPL,
               "spec pi_sieve(u64 n) -> u64 { effects { alloc }; "
               "sizes lin = n; "
               "target { time: { max: O(lin) }, allocation: { max: O(lin) }, }; }\n",
               "cost", "m")
    check("5.3 time / allocation 都能用绑定名，且判出结论",
          "(target n)" in out and "无法判定" not in out, out)

    out = _run(SIZES_IMPL,
               "spec pi_sieve(u64 n) -> u64 { effects { alloc }; "
               "sizes table = table.length(); "
               "target { allocation: { max: O(table) }, }; }\n", "check", "m")
    check("5.4 右侧引用实现局部 → E-Sizes-NotShape（不是形状量）",
          "E-Sizes-NotShape" in out, out)

    out = _run(SIZES_IMPL,
               "spec pi_sieve(u64 n) -> u64 { effects { alloc }; "
               "sizes table = n + 1; "
               "target { allocation: { max: O(n) }, }; }\n", "check", "m")
    check("5.5 绑了不用 → W-Sizes-Unused（绑了不消费等于没写）",
          "W-Sizes-Unused" in out, out)

    out = _run(SIZES_IMPL,
               "spec pi_sieve(u64 n) -> u64 { effects { alloc }; "
               "sizes m = n + 1; requires m > 0; "
               "target { allocation: { max: O(m) }, }; }\n", "check", "m")
    check("5.6 用在 requires（暂未接线）→ E-Sizes-Position",
          "E-Sizes-Position" in out, out)

    out = _run(SIZES_IMPL,
               "spec pi_sieve(u64 n) -> u64 { effects { alloc }; "
               "sizes n + 1; "
               "target { allocation: { max: O(n) }, }; }\n", "check", "m")
    check("5.7 旧的死语法 `sizes <expr>;` → 解析错误（不再被静默接受）",
          "E-Parse-Expected" in out, out)

    print()
    if FAILED:
        print(f"✗ 失败 {len(FAILED)} 项：{FAILED}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
