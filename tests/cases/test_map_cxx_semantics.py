#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_map_cxx_semantics.py —— `map` 的 A2 / A3 / A13 对齐 C++（2026-11-11）

依据：`文档/设计/目前设计/审计-容器vsC++标准-2026-11-11.md` §1.2。

| 编号 | C++ | 我们（以前） | 症状 |
|---|---|---|---|
| **A2** | `m.insert({k,v})` 键已存在 → **不覆盖**，返回"未插入" | **覆盖** | 静默错值 |
| **A3** | `m[k]` 缺键 → **插入 `V{}`** 并返回它 | **报错**「map 里没有键」 | check 绿、run 炸 |
| **A13** | `m[k] += 1` / `m[k]++` 先保证键存在 | 走**序列下标**路径（`_index` 判界） | **依数据决定对错** |

本文件只钉**语义**（κ 那半边在 `test_map_cost.py` / 成本域）。
三条都属于"改名即用"的硬伤：C++ 程序改个后缀就该跑出一样的结果。
"""
import os
import subprocess
import sys
import tempfile

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
DSH = os.path.join(SRC, "dsh.py")
sys.path.insert(0, SRC)

from parser import parse_source            # noqa: E402
from interp import Interp, InterpError      # noqa: E402

ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name}   {detail}")


def run_cli(args, cwd):
    r = subprocess.run([sys.executable, "-B", DSH] + args, cwd=cwd,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=ENV)
    return (r.stdout or "") + (r.stderr or ""), r.returncode


# ------------------------------------------------------------------ 运行期语义
SRC = """u64 read_missing() {
    map<u64, u64> m;
    u64 v = m[7];
    return v * 10 + m.size() * 100 + m.contains(7) * 1000;
}
u64 adj() {
    map<u64, vector<u64>> g;
    g[1].push_back(2);
    g[1].push_back(3);
    g[2].push_back(4);
    return g.get(1).size() * 1000 + g.get(1).at(1) * 100 + g.size() * 10;
}
u64 compound() {
    map<u64, u64> cnt;
    cnt.insert(0, 5);
    cnt.insert(1, 6);
    cnt.insert(2, 7);
    cnt[100] += 5;
    cnt[100]++;
    cnt[0] += 5;
    cnt[0]++;
    return cnt.get(100) * 1000 + cnt.get(0) * 10 + cnt.size();
}
bool insert_first() {
    map<u64, u64> m;
    return m.insert(10, 1);
}
bool insert_dup() {
    map<u64, u64> m;
    m.insert(10, 1);
    return m.insert(10, 99);
}
u64 insert_dup_value() {
    map<u64, u64> m;
    m.insert(10, 1);
    m.insert(10, 99);
    return m.get(10) * 100 + m.size();
}
bool put_new() {
    map<u64, u64> m;
    return m.put(7, 3);
}
bool put_over() {
    map<u64, u64> m;
    m.put(7, 3);
    return m.put(7, 99);
}
u64 put_over_value() {
    map<u64, u64> m;
    m.put(7, 3);
    m.put(7, 99);
    return m.get(7) * 100 + m.size();
}
u64 index_write() {
    map<u64, u64> m;
    m[10] = 1;
    m[10] = 99;
    return m.get(10) * 100 + m.size();
}
u64 get_still_strict() {
    map<u64, u64> m;
    m.insert(1, 1);
    return m.get(9);
}
"""

it = Interp(parse_source(SRC, "a.eln").module, None)

print("[1] A3：`m[k]` 读缺键 → **插入 `V{}`** 并返回它（C++ 的 operator[]）")
# v=0；size=1；contains(7)=1 → 0*10 + 1*100 + 1*1000 = 1100
check("m[7] 得 0，且键 7 已在表里、size==1 → 1100",
      it.call("read_missing", []) == 1100, it.call("read_missing", []))

print("[2] A3：默认值是**按值的类型**造的（容器→空容器），不是硬编码 0")
# g[1] 是空 vector → push 两个；g[2] push 一个；size==2 → 2*1000+3*100+2*10 = 2320
check("`g[u].push_back(v)` 邻接表可直接写 → 2320",
      it.call("adj", []) == 2320, it.call("adj", []))

print("[3] A13：`m[k] += 1` / `m[k]++` 在**键 >= size** 时不再误报越界")
try:
    _got = it.call("compound", [])
    _exc = None
except InterpError as e:                            # noqa: BLE001
    _got, _exc = None, str(e)
check("cnt[100] += 5 / ++ （键 100 ≥ 条目数 3）不报错",
      _exc is None, _exc)
check("cnt[100]=6、cnt[0]=11、size=4 → 6114", _got == 6114, _got)

print("[4] A2：`insert` **不覆盖**已有键，返回 bool（插入了 = true）")
check("首次 insert(10,1) → true", it.call("insert_first", []) in (1, True),
      it.call("insert_first", []))
check("重复 insert(10,99) → false（C++ 的 pair.second）",
      it.call("insert_dup", []) in (0, False), it.call("insert_dup", []))
check("重复 insert 后 get(10) 仍是 1、size 仍是 1 → 101",
      it.call("insert_dup_value", []) == 101, it.call("insert_dup_value", []))

print("[5] 覆盖写只有两条路：`m.put(k,v)`（本次新增）与 `m[k] = v`")
check("put(7,3) 首次 → true", it.call("put_new", []) in (1, True),
      it.call("put_new", []))
check("put(7,99) 覆盖 → false（键本来就存在）",
      it.call("put_over", []) in (0, False), it.call("put_over", []))
check("put 覆盖后 get(7)=99、size=1 → 9901",
      it.call("put_over_value", []) == 9901, it.call("put_over_value", []))
check("`m[k] = v` 仍是覆盖 → 9901", it.call("index_write", []) == 9901,
      it.call("index_write", []))

print("[6] `get` 仍然严格（缺键报错）——它是本语言的扩展，不是 operator[]")
try:
    it.call("get_still_strict", [])
    check("get(9) 缺键报错", False, "没有抛")
except InterpError as e:
    check("get(9) 缺键报错且提示 contains", "contains" in str(e), str(e))

# ------------------------------------------------------------------ 静态侧
#: 一个最小的 `.tu`：`struct` 声明进 `.tu`（本语言的分工），并给 spec。
TU = """struct P { x: u64, y: u64, }

spec f() -> u64 {
    effects {};
    target { time: { max: O(1) }, allocation: { max: 1 }, };
}
"""


def _write_case(d, eln, tu=TU):
    with open(os.path.join(d, "m.eln"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(eln)
    with open(os.path.join(d, "m.tu"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(tu)


print("[7] W-Map-SilentInsert：**读**位置的 `m[k]` 告警（默认开、不阻断）")
d = tempfile.mkdtemp(prefix="elench-mapsem-")
_write_case(d, "u64 f() {\n    map<u64, u64> cnt;\n    u64 v = cnt[3];\n"
               "    return v;\n}\n")
out, rc = run_cli(["check", "m"], d)
check("check 打印 W-Map-SilentInsert", "W-Map-SilentInsert" in out, out[:400])
check("告警落在读那一行（@3:）", "@3:" in out, out[:400])
check("只有 warning：退出码仍是 0", rc == 0, f"rc={rc}")

print("[8] 写位置**不**告警（`m[k] = v` / `m[k] += 1` / `m[k]++` 是写者本意）")
_write_case(d, "u64 f() {\n"
               "    map<u64, u64> cnt;\n"
               "    cnt[3] = 1;\n"
               "    cnt[4] += 1;\n"
               "    cnt[5]++;\n"
               "    cnt[6] = cnt[6] + 1;\n"
               "    return cnt.get(3) + cnt.get(4) + cnt.get(5) + cnt.get(6);\n}\n")
out, rc = run_cli(["check", "m"], d)
check("四条写（含 `m[k] = m[k] + 1` 的词频写法）都没被误报",
      "W-Map-SilentInsert" not in out, out[:400])

print("[9] struct 值的默认值 = **逐字段零值**（不是整数 0）")
_write_case(d, "u64 f() {\n"
               "    map<u64, P> m;\n"
               "    P p = m[5];\n"
               "    m[5].x = 9;\n"
               "    return p.x + p.y + m.size() + m.get(5).x * 10;\n}\n")
out, rc = run_cli(["check", "m"], d)
check("P p = m[5] 能过 check（不再『未实现的赋值目标：field』）",
      "E-Type" not in out and rc == 0, out[:400])
out, rc = run_cli(["run", "m", "--fn=f"], d)
check("run：m[5] 是零值 struct，且 `m[5].x = 9` 写进表里 → 91",
      "91" in out, out[:400])

print("[10] 词频程序：check ✓ / run ✓ 且结果与 C++ 一致（C++ 形状 + --compat）")
WC = """u64 main() {
    std::vector<std::string> words = {"a", "b", "a"};
    std::map<std::string, u64> freq;
    for (auto& w : words) { freq[w] = freq[w] + 1; }
    print(freq["a"]);
    print(freq["b"]);
    print(freq.size());
    return 0;
}
"""
_write_case(d, WC, "// 无 .tu：check 会给 I-Fn-NoSpec-Module（警告，不阻断）\n")
out, rc = run_cli(["check", "--compat", "m"], d)
check("check 通过（没有 E- 前缀的错误行）", rc == 0 and "✓ 通过" in out, out[:500])
check("词频的**读**（`freq[w]` 那一次赋值不算）只报 2 条 W-Map-SilentInsert",
      out.count("W-Map-SilentInsert") == 2, str(out.count("W-Map-SilentInsert")))
out, rc = run_cli(["run", "--compat", "m"], d)
_lines = [ln for ln in out.splitlines() if ln.strip() in ("2", "1", "0")]
check("run 输出 a=2 / b=1 / size=2", _lines[:3] == ["2", "1", "2"], out[:500])

print("[11] map 的 range-for 元素是 `(键, 值)` 二元组（不是键）——2026-11-12")
#: C++ 的 `for (const auto& kv : m)` 里 `kv` 是 `pair<const K, V>`。以前本语言
#: 迭代出的是**键**：`kv.first` 在 run 期报「未实现的字段访问 first」——
#: `demo/corpus/11_text.cpp` 实测就是这一类（check 绿、run 崩）。
PAIR_SRC = """u64 pairs() {
    map<u64, u64> m;
    m[1] = 10;
    m[2] = 20;
    m[3] = 30;
    u64 acc = 0;
    for (const auto& kv : m) {
        acc = acc * 10 + kv.0;
        acc = acc * 10 + kv.1;
    }
    return acc;
}
u64 order() {
    map<u64, u64> m;
    m[5] = 1;
    m[2] = 2;
    m[9] = 3;
    u64 acc = 0;
    for (const auto& kv : m) { acc = acc * 10 + kv.0; }
    return acc;
}
u64 by_value_is_copy() {
    map<u64, vector<u64>> m;
    m[1].push_back(7);
    for (auto kv : m) { kv.1.push_back(9); }
    return m.get(1).size();
}
u64 ref_reaches_container() {
    map<u64, vector<u64>> m;
    m[1].push_back(5);
    for (auto& kv : m) { kv.1.push_back(6); }
    return m.get(1).size() * 10 + m.get(1).at(1);
}
u64 unordered_pairs() {
    unordered_map<u64, u64> m;
    m[4] = 40;
    u64 acc = 0;
    for (const auto& kv : m) { acc = kv.0 * 100 + kv.1; }
    return acc;
}
"""
_pa = Interp(parse_source(PAIR_SRC, "pair.eln").module, None)
check("`kv.0` / `kv.1` 是键与值（逐轮 1,10,2,20,3,30 → 204060）",
      _pa.call("pairs", []) == 204060, _pa.call("pairs", []))
check("元素序仍是**键序**（2,5,9 → 259）", _pa.call("order", []) == 259,
      _pa.call("order", []))
check("`for (auto kv : m)` 按值：改副本不动表（size 仍是 1）",
      _pa.call("by_value_is_copy", []) == 1, _pa.call("by_value_is_copy", []))
check("`for (auto& kv : m)` 的元素里容器与表里是**同一对象**（26）",
      _pa.call("ref_reaches_container", []) == 26,
      _pa.call("ref_reaches_container", []))
check("`unordered_map` 也是 `(键, 值)`（4*100+40 = 440）",
      _pa.call("unordered_pairs", []) == 440, _pa.call("unordered_pairs", []))

print("[12] `--compat`：`kv.first` / `kv.second` → `.0` / `.1`（struct 的 first 不改）")
#: **没有 `.tu`**：这些 case 只有实现，契约不参与判定（check 给
#: `I-Fn-NoSpec-Module` 警告，不阻断）。
_NO_TU = "// 无 .tu：check 会给 I-Fn-NoSpec-Module（警告，不阻断）\n"
COMPAT_PAIR = """u64 main() {
    map<u64, u64> m;
    m[1] = 10;
    u64 acc = 0;
    for (const auto& kv : m) { acc = kv.first * 100 + kv.second; }
    print("{}", acc);
    return 0;
}
"""
_write_case(d, COMPAT_PAIR, _NO_TU)
out, rc = run_cli(["run", "--compat", "m"], d)
check("`kv.first`/`kv.second` 在 --compat 下可读（110）",
      rc == 0 and "110" in out, out[:400])
out, rc = run_cli(["check", "m"], d)          # 不带 --compat：照旧拒绝
check("不带 --compat 时 `kv.first` 仍是错误（不自作主张）",
      rc != 0 and "E-Type-UnknownField" in out, out[:400])

#: **用户 struct 里叫 `first` / `second` 的字段不许被改名**：字段名映射只看
#: **基址的静态类型是不是二元组**，struct 走它自己的分支。
STRUCT_FIRST = """struct P { first: u64, second: u64, }
u64 main() {
    P p = P { first: 3, second: 4, };
    print("{}", p.first * 10 + p.second);
    return 0;
}
"""
_write_case(d, STRUCT_FIRST, _NO_TU)
out, rc = run_cli(["run", "--compat", "m"], d)
check("struct 自己的 `first` 字段照旧是字段（34）",
      rc == 0 and "34" in out, out[:400])

print("[13] 写位置：`kv.1 = v` / `kv.second = v` / 重新绑定 → check 就报（不许静默丢写）")
for _label, _body in (
        ("核心写法 `kv.1 = 5`",
         "u64 main() {\n    map<u64, u64> m;\n    m[1] = 1;\n"
         "    for (const auto& kv : m) { kv.1 = 5; }\n    return 0;\n}\n"),
        ("compat 写法 `kv.second = 5`",
         "u64 main() {\n    map<u64, u64> m;\n    m[1] = 1;\n"
         "    for (const auto& kv : m) { kv.second = 5; }\n    return 0;\n}\n"),
        ("引用迭代里整体重新绑定",
         "u64 main() {\n    map<u64, u64> m;\n    m[1] = 1;\n"
         "    for (auto& kv : m) { kv = (1, 2); }\n    return 0;\n}\n")):
    _write_case(d, _body, _NO_TU)
    out, rc = run_cli(["check", "--compat", "m"], d)
    check(f"{_label}：check 报 E-Assign-Immutable", rc != 0
          and "E-Assign-Immutable" in out, out[:400])

#: **运行期也必须是同一个口径**（CLI 的 run 会被 check 挡住，所以直接调解释器）：
#: 以前 `kv.second = x` 落到「未实现的赋值目标：field/None」并被静默跳过。
_rt = Interp(parse_source(
    "u64 g() {\n    map<u64, u64> m;\n    m[1] = 1;\n"
    "    for (const auto& kv : m) { kv.1 = 5; }\n    return m.get(1);\n}\n",
    "w.eln").module, None)
try:
    _rt.call("g", [])
    check("运行期写字段被拒绝（不是静默丢写）", False, "没有抛")
except InterpError as e:
    check("运行期写字段被拒绝（不是静默丢写）",
          "E-Assign-Immutable" in str(e) and "m[键] = 新值" in str(e), str(e)[:300])

print("[14] 字符字面量的**转义真值**（11_text 的按行切分靠它）")
#: 以前 `'\n'` 被求成 `ord('\\')` = 92（`parser` 存的是原文，解释器自己取
#: 第二个字符），于是 `c == '\n'` 永远不成立——静默错值。词法层本来就会
#: 解码（`lexer.ESCAPES`），这里钉住解释器侧的口径。
CHAR_SRC = """u64 nl() { return '\\n'; }
u64 tab() { return '\\t'; }
u64 hex_a() { return '\\x41'; }
u64 plain() { return 'A'; }
u64 count_nl(const vector<u8>& s) {
    u64 c = 0;
    for (u64 i = 0; i < s.size(); i = i + 1) {
        if (s[i] == '\\n') { c = c + 1; }
    }
    return c;
}
"""
_ch = Interp(parse_source(CHAR_SRC, "ch.eln").module, None)
check("`'\\n'` = 10", _ch.call("nl", []) == 10, _ch.call("nl", []))
check("`'\\t'` = 9", _ch.call("tab", []) == 9, _ch.call("tab", []))
check("`'\\x41'` = 65 = `'A'`", _ch.call("hex_a", []) == 65
      and _ch.call("plain", []) == 65, _ch.call("hex_a", []))
check("按 `'\\n'` 数换行（`\"a\\nb\\nc\"` → 2）", _ch.call("count_nl", [[97, 10, 98, 10, 99]]) == 2,
      _ch.call("count_nl", [[97, 10, 98, 10, 99]]))

print("[15] `demo/corpus/11_text.cpp` 端到端：map 遍历出 (键, 值)，数值与 g++ 一致")
#: 改前：run 崩在 `kv.first`（两处假绿的一个）。改后：3 条 `name 2 / size 2 /
#: tag 1` 与 g++ 的 stdout **数值逐字节一致**；`%s` / `%llu` 是 printf 的
#: 已知语义差（compat 明确不补），所以这里只比**去掉格式串后的 token**。
_t11 = os.path.join(ROOT, "demo", "corpus", "11_text.cpp")
with open(_t11, "r", encoding="utf-8") as fh:
    _t11_src = fh.read()
_write_case(d, _t11_src, _NO_TU)
out, rc = run_cli(["run", "--compat", "m", "--fn=main"], d)
check("11_text 的 main 能跑完（rc=0）", rc == 0, out[:500])
_toks = out.replace("%s %llu", " ").split()
check("去掉 printf 格式串后与 g++ 逐字节相同（name 2 / size 2 / tag 1）",
      _toks == ["name", "2", "size", "2", "tag", "1", "0"], _toks)
out_c, rc_c = run_cli(["check", "--compat", "m"], d)
check("11_text 的 check --compat 仍然通过", rc_c == 0 and "✓ 通过" in out_c,
      out_c[:500])

print("[16] 容器类键：`unordered_map<vector<u8>, u64>` / `unordered_set<vector<u8>>`")
#: C++ 的 `unordered_map<string, u64>` 是最常见的词频结构；本语言的 `Text` 就是
#: `vector<u8>`（`list` 子类）。2026-11-12 起键走 `stdlib.hash_key`（FNV-1a，
#: 逐元素混入，**O(键长)**）+ `interp._HashKey.__eq__`（逐元素真比较），
#: 不再是 `TypeError: unhashable type: 'Text'`。
VKEY_SRC = """u64 vk_basic() {
    unordered_map<vector<u8>, u64> m;
    m.insert("apple", 1);
    m.insert("app", 2);
    m.insert("banana", 3);
    return m.get("apple") * 100 + m.get("app") * 10 + m.get("banana");
}
u64 vk_equal_content() {
    unordered_map<vector<u8>, u64> m;
    vector<u8> a = "ab";
    vector<u8> b = "ab";
    m.insert(a, 1);
    m.insert(b, 2);
    return m.size() * 10 + m.get(b);
}
u64 vk_prefix() {
    unordered_map<vector<u8>, u64> m;
    m.insert("a", 1);
    m.insert("ab", 2);
    m.insert("abc", 3);
    return m.get("a") * 100 + m.get("ab") * 10 + m.get("abc");
}
u64 vk_empty_key() {
    unordered_map<vector<u8>, u64> m;
    vector<u8> e;
    m.insert(e, 7);
    return m.size() * 10 + m.get("");
}
u64 vk_remove() {
    unordered_map<vector<u8>, u64> m;
    m.insert("a", 1);
    m.insert("b", 2);
    u64 gone = m.remove("a");
    return m.size() * 100 + gone * 10 + m.contains("a");
}
u64 vk_vec64() {
    unordered_map<vector<u64>, u64> m;
    vector<u64> k1 = {1, 2};
    vector<u64> k2 = {1, 2};
    m.insert(k1, 7);
    m.insert(k2, 9);
    return m.size() * 10 + m.get(k1);
}
u64 vk_array() {
    unordered_map<array<u8, 2>, u64> m;
    array<u8, 2> a = {1, 2};
    array<u8, 2> b = {1, 2};
    m.insert(a, 4);
    m.insert(b, 5);
    return m.size() * 10 + m.get(a);
}
u64 vk_set() {
    unordered_set<vector<u8>> s;
    s.insert("x");
    s.insert("x");
    return s.size() * 10 + s.contains("x");
}
u64 vk_vs_map() {
    map<vector<u8>, u64> a;
    unordered_map<vector<u8>, u64> b;
    a["get"] = 1;  b["get"] = 1;
    a["put"] = 2;  b["put"] = 2;
    a["get"] = 4;  b["get"] = 4;
    u64 ok = 1;
    if (a.get("get") != b.get("get")) { ok = 0; }
    if (a.get("put") != b.get("put")) { ok = 0; }
    if (a.size() != b.size()) { ok = 0; }
    a.remove("put");  b.remove("put");
    if (a.contains("put") != b.contains("put")) { ok = 0; }
    if (a.size() != b.size()) { ok = 0; }
    return ok;
}
"""
_vk = Interp(parse_source(VKEY_SRC, "vkey.eln").module, None)
check("vector<u8> 键：insert/get 三个词 → 123", _vk.call("vk_basic", []) == 123,
      _vk.call("vk_basic", []))
check("内容相等的两个 Text 是同一个键（size=1，后写覆盖 → 12）",
      _vk.call("vk_equal_content", []) == 12, _vk.call("vk_equal_content", []))
check("前缀不同的键不串（a/ab/abc → 123）", _vk.call("vk_prefix", []) == 123,
      _vk.call("vk_prefix", []))
check("空 vector<u8> 是合法键（size=1、get(\"\")=7 → 17）",
      _vk.call("vk_empty_key", []) == 17, _vk.call("vk_empty_key", []))
check("remove 容器键：size=1、返回 1、contains=0 → 110",
      _vk.call("vk_remove", []) == 110, _vk.call("vk_remove", []))
check("vector<u64> 键同样按元素（size=1、get=9 → 19）",
      _vk.call("vk_vec64", []) == 19, _vk.call("vk_vec64", []))
check("array<u8,2> 键同样按元素（size=1、get=5 → 15）",
      _vk.call("vk_array", []) == 15, _vk.call("vk_array", []))
check("unordered_set<vector<u8>> 去重（size=1、contains=1 → 11）",
      _vk.call("vk_set", []) == 11, _vk.call("vk_set", []))
check("同一批 vector<u8> 键：unordered_map 与 map 的 get/contains/size **逐项一致**",
      _vk.call("vk_vs_map", []) == 1, _vk.call("vk_vs_map", []))

print("[16b] 哈希冲突：`_HashKey` 在**同哈希**下仍按内容比较（不是对象身份）")
#: 直接把哈希强制成常数，模拟"所有键都冲突"的最坏情况：`dict` 会退化成一串
#: 逐元素比较；只要 `__eq__` 写对，结果就仍然正确。这是"冲突后真比较内容"
#: 的直接证据（不依赖找到一个真实的 FNV 碰撞）。
import stdlib                                  # noqa: E402
from interp import _HashKey                    # noqa: E402
_old_hash = stdlib.hash_key
try:
    stdlib.hash_key = lambda _v: 0
    _k1 = _HashKey([97, 98])
    _k2 = _HashKey([97, 98])
    _k3 = _HashKey([97, 99])
    check("同内容两个键：hash 相同且 eq 为真",
          hash(_k1) == hash(_k2) and _k1 == _k2,
          f"{hash(_k1)} {hash(_k2)} {_k1 == _k2}")
    check("不同内容但 hash 相同：eq 为假（不会被当成同一个键）",
          hash(_k1) == hash(_k3) and _k1 != _k3,
          f"{hash(_k1)} {hash(_k3)} {_k1 == _k3}")
    _d = {_k1: 1}
    _d[_k2] = 2
    _d[_k3] = 3
    check("dict 在同哈希下仍分出 2 个键（同内容合并、不同内容分开）",
          len(_d) == 2 and _d[_k1] == 2 and _d[_k3] == 3, len(_d))
finally:
    stdlib.hash_key = _old_hash

print()
if FAILED:
    print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
    sys.exit(1)
print("全部通过")
