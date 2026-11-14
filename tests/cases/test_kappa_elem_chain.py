#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

# -*- coding: utf-8 -*-
"""test_kappa_elem_chain.py —— **范围 for 元素的长度**：四跳链（2026-11-13）

## 靶子（KPI② 最后一个判不了的函数）

```cpp
int main() {                                        // 11_text.cpp
    std::string text;  text = text + "…"; …         // n(text) = 常数
    std::vector<std::string> lines = split(text, '\n');
    for (const auto& line : lines) {                // line 是 lines 的元素
        std::vector<std::string> fields = split(line, ',');
        for (const auto& field : fields) {          // field 是 fields 的元素
            for (i = 0; i < field.size(); ++i) …    // 旧口径：n(field)
```

改前 `κ(main)` 里是 `… + n(field)×24 + … + 2809×n(field)×33 + …`。
`n(field)` **不属于 main 的任何形参/局部容器**（main 没有形参），target 里写不出来
⇒ `compare` 只可能落 E23 ⇒ 永远 `?`。

## 缺的那一跳

    n(field)   =  L(fields)          ← 元素 ⇒ 所属容器的**元素长度域**（本文件钉的）
    L(fields)  ≤  n(line)            ← split 的输出元素是输入的子串（已有：`_ret_elem_of`）
    n(line)    =  L(lines)           ← line 也是 lines 的元素（同一条规则，第二次用）
    L(lines)   ≤  n(text)            ← 同理（已有）
    n(text)    =  常数               ← 3 条字符串字面量拼出来（已有：`_modelled_write_locals`）

**方向（硬约束）**：`n(元素) ≤ L(容器)` 只**变大** ⇒ κ 仍是上界，不是估计值。
判据只有一份：`_elem_alias_size_bound`（复用 `_container_expr_size` 里那条
「元素别名的长度」规则，不另造界），三个出生地共用（循环上界 / 局部尺寸别名 /
容器长度表达式）。

## 五类断言（只比表达式形状不算验证）

1. **判据本体**：`n(元素) → L(容器)` 真的接上了——κ 只依赖 `text`，没有
   `n(line)` / `n(field)`；`L(lines)` / `L(fields)` 的界都是 `n(text)`。
2. **机制②（split 的输出 ≤ 输入长度）**：`split` 自己的 `L(out) = n(s)`，
   所以 `L(<split 的输出>) ≤ n(<输入>)` 是**独立于①**成立的。
3. **等价性**：同逻辑两种写法（`const auto&` / `auto&`、`.size()` / `.length()`）
   κ **逐字节相同**、返回值 / ops 逐项相同。
4. **解释器实测**：真实 `max_i n(fields[i])` ≤ 推出的界（四个规模：24 / 96 / 384 /
   语料那 3 行），且 `ops ≤ κ` —— 界是真上界，不是形状像。
5. **语料靶子 + 反向验收**：`11_text.main` 的 κ 是常数（且同模块另外四条 κ
   **逐字节不变**）；补一条 `time: O(1)` 的 target ⇒ 判 ✓，把 allocation 收紧到
   常数 `0` ⇒ 判债务（▲）。另有一个**参数化**的靶子做标准反向验收：
   target 写成 κ 的阶 ⇒ 达成，收紧一档 ⇒ 债务。
6. **拒绝形状**：元素别名同名指向两个容器 / 元素被别名改写 / 元素是**标量**
   ⇒ **不给**新界（κ 里仍是 `n(x)`，或 eb 里是 None）。

跑法：

    python -B tests/cases/test_kappa_elem_chain.py
"""

import io
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
for _p in (SRC, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                       # noqa: BLE001
    pass

import compat                                            # noqa: E402
from cost_check import compare, compare_exact, evaluate as kappa_at  # noqa: E402
from cost_interp import CostInterp                       # noqa: E402
from interp import Interp, ops_enable, ops_of, ops_reset  # noqa: E402
from parser import parse_source                          # noqa: E402

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


# ---------------------------------------------------------------- 受试模块
#
# `split` 与语料 `11_text.cpp` 的那份**同形**（用户函数，不是内建）：
# 四跳链的每一跳都要走 `_ret_elem_of` / `_elem_bounds_full`，用同形的 split
# 才能证明"语料里能过"不是靠某个例外分支。
SRC_ALL = r"""
vector<vector<u8>> split(const vector<u8>& s, u8 sep) {
    vector<vector<u8>> out;
    vector<u8> cur;
    for (auto c : s) {
        if (c == sep) {
            out.push_back(cur);
            cur.clear();
        } else {
            cur.push_back(c);
        }
    }
    out.push_back(cur);
    return out;
}

// 四跳链本体：`line` / `field` 都是 range-for 的元素。
u64 a_constref(const vector<u8>& text) {
    vector<vector<u8>> lines = split(text, '\n');
    u64 best = 0;
    for (const auto& line : lines) {
        vector<vector<u8>> fields = split(line, ',');
        for (const auto& field : fields) {
            if (field.size() > best) { best = field.size(); }
        }
    }
    return best;
}

// 同逻辑的另一种写法：`auto&` + `.length()`。
u64 b_ref(const vector<u8>& text) {
    vector<vector<u8>> lines = split(text, '\n');
    u64 best = 0;
    for (auto& line : lines) {
        vector<vector<u8>> fields = split(line, ',');
        for (auto& field : fields) {
            if (field.length() > best) { best = field.length(); }
        }
    }
    return best;
}

// 机制②的独立靶子：元素用 range-for 取（所以走的是①的判据），
// 但**唯一的 L 来源**是「split 的输出元素 ≤ 输入长度」这一条（②）：
// `L(parts)` 既不是初始化列表，也没有任何行内 push。
u64 c_split_only(const vector<u8>& s) {
    vector<vector<u8>> parts = split(s, ',');
    u64 t = 0;
    for (const auto& p : parts) {
        for (u64 j = 0; j < p.size(); ++j) { t = t + 1; }
    }
    return t;
}

// 拒绝①：同名 `x` 在不同 for 里指向不同容器 ⇒ 判不出该用哪个 L。
u64 r_dup_alias(const vector<vector<u8>>& a, const vector<vector<u8>>& b) {
    u64 t = 0;
    for (const auto& x : a) {
        for (u64 i = 0; i < x.size(); ++i) { t = t + 1; }
    }
    for (const auto& x : b) {
        for (u64 i = 0; i < x.size(); ++i) { t = t + 1; }
    }
    return t;
}

// 拒绝②：元素被别名改写（`row.push_back`）⇒ `L(h)` 不再是上界。
u64 r_written_alias(const vector<vector<u8>>& g) {
    vector<vector<u8>> h = g;
    u64 t = 0;
    for (auto& row : h) {
        row.push_back(1);
        for (u64 i = 0; i < row.size(); ++i) { t = t + 1; }
    }
    return t;
}

// 拒绝③：元素是**标量**（`vector<u64>`）⇒ 没有"元素长度"这回事。
u64 r_scalar_elems(const vector<u64>& xs) {
    u64 t = 0;
    for (const auto& x : xs) {
        for (u64 i = 0; i < x; ++i) { t = t + 1; }
    }
    return t;
}

// 接受形状（不是本任务的靶子，但必须是同一条规则的另一半）：
// const 嵌套容器**形参**的元素别名 → `L(形参)`，target 里能写 `L(a)`。
u64 d_param_elem(const vector<vector<u8>>& a) {
    u64 t = 0;
    for (const auto& row : a) {
        for (u64 i = 0; i < row.size(); ++i) { t = t + 1; }
    }
    return t;
}
"""


def _parse(src, name):
    prev = compat.enabled()
    compat.set_enabled(True)
    try:
        return parse_source(src, name).module
    finally:
        compat.set_enabled(prev)


def _mods():
    mod = _parse(SRC_ALL, "elem_chain.eln")
    return mod, CostInterp(mod, None)


def _fn(mod, name):
    return next((c for c in mod.children
                 if getattr(c, "kind", "") == "fn" and getattr(c, "name", "") == name),
                None)


def _eb(ci, mod, name):
    """`(eb, 局部名集合)` —— 元素长度表（`L(x)` 的推导体）。"""
    return ci._elem_bounds_full(_fn(mod, name))


def _run(mod, name, args):
    """跑一次 → `(返回值, ops)`。"""
    ops_enable(True)
    try:
        ops_reset()
        it = Interp(mod, None)
        val = it.call(name, args)
        return val, ops_of(name)
    finally:
        ops_enable(False)


# ---------------------------------------------------------------- ① 判据本体
def test_mechanism_1():
    print("[1] 判据本体：`n(元素)` → `L(容器)`（四跳链接上了）")
    mod, ci = _mods()
    k = ci.cost_of("a_constref")
    r = k.render()
    check(k.variables() == ["text"],
          "a_constref：κ 只依赖 `text`（`line` / `field` 都不见了）",
          str(k.variables()))
    check("n(line)" not in r, "a_constref：κ 里没有 `n(line)`", r)
    check("n(field)" not in r, "a_constref：κ 里没有 `n(field)`", r)
    eb, _ln = _eb(ci, mod, "a_constref")
    check(eb.get("lines") is not None and eb["lines"].render() == "n(text)",
          "L(lines) = n(text)（`lines = split(text, '\\n')`）",
          None if eb.get("lines") is None else eb["lines"].render())
    check(eb.get("fields") is not None and eb["fields"].render() == "n(text)",
          "L(fields) = n(text)（`fields = split(line, ',')` + n(line) ≤ L(lines)）",
          None if eb.get("fields") is None else eb["fields"].render())
    check(not k.is_top(), "a_constref：κ 不是 ⊤", r)


# ---------------------------------------------------------------- ② 机制②
def test_mechanism_2():
    print("[2] 机制②：split 的输出元素 ≤ 输入长度（独立于①成立）")
    mod, ci = _mods()
    eb_split, _ln = _eb(ci, mod, "split")
    check(eb_split.get("out") is not None and eb_split["out"].render() == "n(s)",
          "split：`L(out) = n(s)`（`cur` 每轮至多 +1 个字符 ⇒ 每段 ≤ n(s)）",
          None if eb_split.get("out") is None else eb_split["out"].render())
    eb, _ln = _eb(ci, mod, "c_split_only")
    check(eb.get("parts") is not None and eb["parts"].render() == "n(s)",
          "c_split_only：`L(parts) = n(s)`（没有 range-for，只有 split 这一跳）",
          None if eb.get("parts") is None else eb["parts"].render())
    k = ci.cost_of("c_split_only")
    check(k.variables() == ["s"] and "n(p)" not in k.render()
          and not k.is_top(),
          "c_split_only：κ 只依赖 `s`（`p.size()` 换成了 L(parts) = n(s)）",
          k.render())


# ---------------------------------------------------------------- ③ 等价性
def test_equivalence():
    print("[3] 等价性：同逻辑两种写法 κ 逐字节相同、run 逐项相同")
    mod, ci = _mods()
    ka, kb = ci.cost_of("a_constref"), ci.cost_of("b_ref")
    check(ka.render() == kb.render(),
          "`const auto& field` + `.size()` 与 `auto& field` + `.length()` 的 κ 逐字节相同",
          f"{ka.render()} vs {kb.render()}")
    same, detail = True, ""
    for n in (0, 12, 40, 96):
        text = _make_text(n)
        va, oa = _run(mod, "a_constref", [list(text)])
        vb, ob = _run(mod, "b_ref", [list(text)])
        if (va, oa) != (vb, ob):
            same = False
            detail = f"n={len(text)}: {(va, oa)} vs {(vb, ob)}"
            break
    check(same, "0 / 12 / 40 / 96 四个规模：返回值与 ops 逐项相同", detail)


# ---------------------------------------------------------------- ④ 实测
def _make_text(n):
    """造一段总长 ≈ `n` 的文本：每行 3 个字段，字段长度递增。"""
    if n <= 0:
        return []
    parts, total, k = [], 0, 1
    while total + k + 6 <= n:
        line = b"a" * k + b"," + b"bb" + b"," + b"c" * (k // 2)
        parts.append(line)
        total += len(line) + 1
        k += 1
    return list(b"\n".join(parts) + b"\n")


def _ref_max_field(text):
    """参考语义：`max_i n(fields[i])`（按行、按逗号切，含空段）。"""
    best = 0
    for line in bytes(text).split(b"\n"):
        for field in line.split(b","):
            best = max(best, len(field))
    return best


def test_measured():
    print("[4] 解释器实测：真实 max_i n(fields[i]) ≤ 界，且 ops ≤ κ（四个规模）")
    mod, ci = _mods()
    k = ci.cost_of("a_constref")
    eb, _ln = _eb(ci, mod, "a_constref")
    bound_cost = eb["fields"]
    for n in (24, 96, 384):
        text = _make_text(n)
        val, ops = _run(mod, "a_constref", [list(text)])
        want = _ref_max_field(text)
        bound = kappa_at(bound_cost, {"text": len(text)})
        kv = kappa_at(k, {"text": len(text)})
        check(val == want,
              f"n(text)={len(text)}：真实 max_i n(fields[i]) = {want}（= 参考实现）",
              f"got {val}")
        check(val <= bound,
              f"n(text)={len(text)}：真实 {want} ≤ 推出的界 L(fields) = {bound:g}",
              f"{val} > {bound}")
        check(val <= len(text),
              f"n(text)={len(text)}：真实 {want} ≤ n(text) = {len(text)}（机制②的结论）",
              f"{val} > {len(text)}")
        check(ops <= kv,
              f"n(text)={len(text)}：ops {ops} ≤ κ {kv:.3g}",
              f"ops={ops} κ={kv}")
    # 语料那 3 行（`11_text.main` 的真实数据）：同一份界也要罩得住。
    corpus_text = list("  Name = alpha , size=3\n"
                       "name=beta, SIZE = 5\n"
                       "  tag=x\n".encode())
    val, ops = _run(mod, "a_constref", [list(corpus_text)])
    want = _ref_max_field(corpus_text)
    check(val == want == 15,
          f"语料那 3 行：真实 max_i n(fields[i]) = {want}（最长的是 '  Name = alpha '）",
          f"got {val}")
    check(val <= len(corpus_text),
          f"语料那 3 行：{want} ≤ n(text) = {len(corpus_text)}", "")


# ---------------------------------------------------------------- ⑤ 语料靶子
CORPUS_CPP = os.path.join(ROOT, "demo", "corpus", "11_text.cpp")
CORPUS_TU = os.path.join(ROOT, "demo", "corpus", "11_text.tu")

#: 同模块另外四条 κ —— **逐字节钉住**（"既有 ✓ 一个都不许翻"的局部版）。
PINNED = {
    "is_blank": "12",
    "trim": "7 + 2 + n(s)×33 + 33 + 2 + n(s)×35 + 35 + 3 + 2 + n(s)×27 + 27 + 2",
    "lower_key": "3 + n(s)×21 + 2",
    "split": ("2 + 1 + n(s) + 1 + 3 + n(s)×(2 + 4 + max(1 + 4 + n(s) + 1 + 5, 5))"
              " + 1 + 4 + n(s) + 1 + 2"),
}

#: 改后的 κ（常数）：`main` 没有形参、数据全是字面量 ⇒ 一切尺寸量都折成数。
MAIN_CONST = ("3945 + 53×(2 + 3770 + 53×(2 + 16946516 + 2 + 3 + log2(2809)"
              " + 3 + log2(2809) + 1)) + 36517 + 2")

#: 改前（HEAD `dc7e62c`）的 κ 片段：`n(field)` 出现 4 次；判不了（E23）。
MAIN_BEFORE_NEEDLE = "n(field)"


def _corpus():
    mod = _parse(io.open(CORPUS_CPP, encoding="utf-8").read(), "11_text.cpp")
    tum = _parse(io.open(CORPUS_TU, encoding="utf-8").read(), "11_text.tu")
    return mod, tum


def test_corpus():
    print("[5a] 语料靶子：`11_text.main` 的 κ 里没有 `n(field)`，且是常数")
    mod, tum = _corpus()
    ci = CostInterp(mod, tum)
    k = ci.cost_of("main")
    r = k.render()
    check(MAIN_BEFORE_NEEDLE not in r,
          f"main：κ 里没有 `{MAIN_BEFORE_NEEDLE}`（改前出现 4 次）", r)
    check("n(line)" not in r and "n(fields)" not in r and "n(lines)" not in r
          and "n(text)" not in r,
          "main：κ 里没有任何局部容器的尺寸量", r)
    check(k.variables() == [], "main：κ 的尺寸量集合为空（是常数）", str(k.variables()))
    check(r == MAIN_CONST, "main：κ = 改后那个常数（逐字节）", r)
    for name, want in PINNED.items():
        got = ci.cost_of(name).render()
        check(got == want, f"{name}：κ 与改前逐字节相同（既有 ✓ 没被翻）", got)


#: 只声明 `main` 的 `.tu`（**不读语料的 .tu**）：反向验收要控的是**这一条**
#: target，语料那份 `.tu` 是别人在维护的（KPI 数字会随它变），不拿它当输入。
_TU_MAIN = """\
spec main() -> i64 {
    effects { io, alloc };
    target { time: { max: O(1) },%s };
}
"""


def _verdict_with(extra: str):
    """用**内存里**的 `.tu`（只含 main）算判决：`((time, alloc), ci)`。

    两个维度**分别用各自的判据**（与 `dsh cost` 完全同一条路）：
    time 走 `compare`（big-O），allocation 走 `compare_exact`（常数按**数值**比
    ——`allocation: { max: 0 }` 是"数个数"，不是"只要常数就算达成"）。
    """
    mod = _parse(io.open(CORPUS_CPP, encoding="utf-8").read(), "11_text.cpp")
    tum = _parse(_TU_MAIN % extra, "11_text.tu")
    ci = CostInterp(mod, tum)
    return (compare(ci.cost_of("main"), ci.target_cost("main", "time")),
            compare_exact(ci.alloc_of("main"),
                          ci.target_cost("main", "allocation"))), ci


def test_reverse_corpus():
    print("[5b] 反向验收：给 main 补一条 `time: O(1)` ⇒ ✓；收紧 allocation ⇒ ▲")
    (vt, _va), ci = _verdict_with("")
    check(vt.kind == "ok",
          "补一条 `target { time: { max: O(1) } }`：判**达成**（改前永远判不了）",
          getattr(vt, "note", vt))
    check(ci is not None and ci.cost_of("main").variables() == [],
          "同一个 κ：没有尺寸量 ⇒ O(1) 是对得上的目标", "")
    (vt2, va2), _ci2 = _verdict_with(" allocation: { max: 0 },")
    check(vt2.kind == "ok", "time 那一档仍是达成", getattr(vt2, "note", vt2))
    check(va2.kind == "debt",
          "再把 allocation 收紧到常数 `0`：判**债务**（▲，判决不是空转）",
          getattr(va2, "note", va2))


_PROBE_TU = """\
spec split(const vector<u8>& s, u8 sep) -> vector<vector<u8>> {
    effects { alloc };
    target { time: { max: O(s.size()) }, allocation: { max: O(s.size()) }, };
}

spec longest_field(const vector<u8>& text) -> u64 {
    effects {};
    target { time: { max: %s }, allocation: { max: 0 }, };
}
"""

_PROBE_ELN = r"""
vector<vector<u8>> split(const vector<u8>& s, u8 sep) {
    vector<vector<u8>> out;
    vector<u8> cur;
    for (auto c : s) {
        if (c == sep) { out.push_back(cur); cur.clear(); }
        else { cur.push_back(c); }
    }
    out.push_back(cur);
    return out;
}

u64 longest_field(const vector<u8>& text) {
    vector<vector<u8>> lines = split(text, '\n');
    u64 best = 0;
    for (const auto& line : lines) {
        vector<vector<u8>> fields = split(line, ',');
        for (const auto& field : fields) {
            if (field.size() > best) { best = field.size(); }
        }
    }
    return best;
}
"""


def _probe_verdict(target):
    mod = _parse(_PROBE_ELN, "chain_probe.eln")
    tum = _parse(_PROBE_TU % target, "chain_probe.tu")
    ci = CostInterp(mod, tum)
    return compare(ci.cost_of("longest_field"),
                   ci.target_cost("longest_field")), ci


def test_reverse_probe():
    print("[5c] 反向验收（参数化靶子）：target = κ 的阶 ⇒ ✓，收紧一档 ⇒ ▲")
    v, ci = _probe_verdict("O(text.size() * text.size() * text.size())")
    check(v.kind == "ok", "target 写成 κ 的阶（n³）：判达成", getattr(v, "note", v))
    check("n(field)" not in ci.cost_of("longest_field").render(),
          "同一个 κ 里没有 `n(field)`（元素别名已换成 L）", "")
    v2, _c = _probe_verdict("O(text.size() * text.size())")
    check(v2.kind == "debt", "收紧一档（n³ → n²）：判债务 ▲", getattr(v2, "note", v2))
    v3, _c = _probe_verdict("O(1)")
    check(v3.kind == "debt", "收紧到常数 O(1)：判债务 ▲", getattr(v3, "note", v3))


# ---------------------------------------------------------------- ⑥ 拒绝形状
def test_rejects():
    print("[6] 拒绝形状：判据不成立时**不给**新界（不猜）")
    mod, ci = _mods()

    k = ci.cost_of("r_dup_alias")
    check("n(x)" in k.render(),
          "同名 `x` 指向两个容器 ⇒ 仍写 `n(x)`（不挑一个 L）", k.render())
    eb, _ln = _eb(ci, mod, "r_dup_alias")
    check(not eb, "同名别名：eb 是空的（没有可替换的 L）", str(eb))

    k = ci.cost_of("r_written_alias")
    eb, _ln = _eb(ci, mod, "r_written_alias")
    check(eb.get("h") is None,
          "`row.push_back(1)` 改写过元素 ⇒ L(h) 判**推不出**（None）",
          str(None if eb.get("h") is None else eb["h"].render()))
    check("L(g)" not in k.render(),
          "r_written_alias：κ 里**没有** L(g)（不会拿被改过的行当上界）", k.render())

    k = ci.cost_of("r_scalar_elems")
    check("n(x)" in k.render(),
          "元素是标量（`vector<u64>`）⇒ 仍写 `n(x)`（标量没有长度）", k.render())
    eb, _ln = _eb(ci, mod, "r_scalar_elems")
    check(not eb, "标量元素：eb 是空的（没有 L 这回事）", str(eb))

    k = ci.cost_of("d_param_elem")
    check("L(a)" in k.render(),
          "const 嵌套容器形参 ⇒ 元素别名换成 `L(a)`（target 里写得出这个名字）",
          k.render())


def main() -> int:
    test_mechanism_1()
    test_mechanism_2()
    test_equivalence()
    test_measured()
    test_corpus()
    test_reverse_corpus()
    test_reverse_probe()
    test_rejects()

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
