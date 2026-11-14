#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_hashmap_values.py —— `unordered_map.values()` 必须返回**值**，不是键

## 这条测试守的 bug（2026-11-13，返回错值）

`src/interp.py` 的 `_method` 里，`keys` 与 `values` 曾共用**一条分支**：

    if _m in ("keys", "values"):
        return list(recv.data.keys()) if _is_map else list(recv.data)

于是 `unordered_map<u8, u64> m`（`a → 11, b → 2`）的 `m.values()` 拿到的是
**键**（`97, 98`），不是值（`11, 2`）。**check 绿、run 出静默错值**——这是最
坏的一类缺陷：不报错、没有迹象，只有对拍或人眼看输出才发现。

修法只有一处：`keys` 与 `values` 分成两条分支，`values` 走
`recv.data.values()`。`_is_map` 为假（`unordered_set`）时行为**一个字不变**。

## 触达路径（为什么不是死代码）

这条分支只在 `UnorderedMapVal` / `UnorderedSetVal` 上走。有序 `map` 的
`values` 在 `typecheck.METHOD_TABLE` 里**根本不存在**（实测
`E-Call-UnknownMethod`），所以之前没人撞上；但**无序**那张表里 `values`
是明写的，于是用户**写得出来**：

    unordered_map<u8, u64> m;      // 语言自带类型，_zero_of_type 造 UnorderedMapVal
    m.insert(97, 11);              // 用户可写的 insert
    m.insert(98, 2);
    m.values();                    // METHOD_TABLE["unordered_map"] 认这个名

`tests/cases/` 里没有任何既有用例写 `unordered_map.values()`，所以 172 个文件
全绿也照样漏掉了它。

## 覆盖三层

1. **端到端**（`run`，真实 `.eln` + 真实 CLI）：输出里 `values()` 出现 `11/2`；
2. **分派层**（直接 `import interp`、手工构造 `UnorderedMapVal` 当 receiver）：
   不依赖 `.eln` 的 `insert` 也能钉住 `_method` 的行为；
3. **反向**：`keys()` 仍然返回键、`unordered_set` 的两个名不变、有序
   `map.values()` 仍然被静态拒绝（没被这次修复"顺手"放开）。

## 顺序口径

`values()` 的顺序与 `keys()` **同源**（底层是同一个 Python dict 的键序/值序），
所以第 [3] 节断言 `values()[i] == get(keys()[i])` 对每个 `i` 成立。契约仍然写
着"顺序未指定"——这条断言守的是**两者一致**，不是"顺序是某个特定值"。
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SRC = os.path.join(ROOT, "src")
PY = sys.executable

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def cli(sub, *args):
    """跑一次工具链，返回 `(rc, stdout, stderr)`（UTF-8，逐字节保留）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = SRC
    argv = [PY, "-B", os.path.join(SRC, "elench.py"), sub] + list(args)
    p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


# ---------------------------------------------------------------- ① 端到端
#
# `dsh run`（= `elench.py run`）走的是**完整**路径：parser → typecheck → interp。
# 两段循环分别打 `keys()` 与 `values()`：键是 `u8`（97/98），值是 `u64`（11/2）。
# 修前实测第二段打的是 `97 98`；修后必须是 `11 2`。
END_TO_END = """u64 main() {
    unordered_map<u8, u64> m;
    m.insert(97, 11);
    m.insert(98, 2);
    for (u64 i = 0; i < m.keys().size(); i = i + 1) {
        print(m.keys()[i]);
    }
    for (u64 i = 0; i < m.values().size(); i = i + 1) {
        print(m.values()[i]);
    }
    return 0;
}
"""

#: 修前的真实输出（2026-11-13 实跑，`unordered_map<u8,u64>`：97→11, 98→2）：
#:     97 98 97 98 0        ← 后两个数是**键**，`values()` 返回错了
#: 修后：
#:     97 98 11 2 0
WANT_LINES = ["97", "98", "11", "2", "0"]

print("[1] 端到端：`unordered_map.values()` 返回值，不是键")
_root = tempfile.mkdtemp(prefix="eln-umap-values-")
_prog = os.path.join(_root, "m.eln")
with open(_prog, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(END_TO_END)

rc, out, err = cli("run", _prog)
_lines = [x.strip() for x in out.splitlines() if x.strip()]
check("`run` 通过（rc = 0）", rc == 0, f"rc={rc} err={err.strip()[:300]}")
check("stdout 逐行是 `97/98/11/2/0`", _lines == WANT_LINES,
      f"实际 {_lines!r}（修前是 ['97','98','97','98','0']）")

if _lines == WANT_LINES:
    check("`values()` 第一项是值 11（不是键 97）", _lines[2] == "11", _lines[2])
    check("`values()` 第二项是值 2（不是键 98）", _lines[3] == "2", _lines[3])
    check("`keys()` 仍然返回键 97/98（没被修坏）",
          _lines[0:2] == ["97", "98"], repr(_lines[0:2]))

# ---------------------------------------------------------------- ② 分派层
#
# 端到端依赖 `.eln` 的 `insert` 造 `UnorderedMapVal`；这里绕过它：**直接**用
# Python 构造 receiver（与 `test_map_cost.py` 等用例一样 `import interp`），
# 再把 receiver 当实参传给解释器里的函数——这样即使将来 `insert` 那条路改了，
# `_method` 的 `keys` / `values` 分叉仍然被钉住。
print("[2] 分派层：手工构造 UnorderedMapVal 当 receiver")
sys.path.insert(0, SRC)
from parser import parse_source          # noqa: E402
from interp import Interp, UnorderedMapVal, UnorderedSetVal   # noqa: E402

DISPATCH_SRC = """u64 key_at(unordered_map<u8, u64>& m, u64 i) {
    return m.keys()[i];
}

u64 value_at(unordered_map<u8, u64>& m, u64 i) {
    return m.values()[i];
}

u64 value_of_key(unordered_map<u8, u64>& m, u8 k) {
    return m.get(k);
}

u64 value_count(unordered_map<u8, u64>& m) {
    return m.values().size();
}
"""
_mod = parse_source(DISPATCH_SRC, "dispatch.eln").module


def _fresh_map():
    """`a → 11, b → 2` 的**无序**映射（键 97/98 是 u8 的 `a`/`b`）。"""
    m = UnorderedMapVal()
    m.data[97] = 11
    m.data[98] = 2
    return m


def _call(fn, *args):
    """每一次 `call` 都是一次独立的测量窗口（`_convert_to` 深拷贝实参）。"""
    return Interp(_mod, None).call(fn, list(args))


check("`value_at(m, 0)` = 11", int(_call("value_at", _fresh_map(), 0)) == 11,
      str(_call("value_at", _fresh_map(), 0)))
check("`value_at(m, 1)` = 2", int(_call("value_at", _fresh_map(), 1)) == 2,
      str(_call("value_at", _fresh_map(), 1)))
check("`key_at(m, 0)` = 97（反向：keys 仍是键）",
      int(_call("key_at", _fresh_map(), 0)) == 97,
      str(_call("key_at", _fresh_map(), 0)))
check("`key_at(m, 1)` = 98", int(_call("key_at", _fresh_map(), 1)) == 98,
      str(_call("key_at", _fresh_map(), 1)))
check("`values()` 与 `keys()` **不相等**（修前两者逐元素相同）",
      [int(_call("value_at", _fresh_map(), i)) for i in range(2)]
      != [int(_call("key_at", _fresh_map(), i)) for i in range(2)], "")
check("`values().size()` = 2（长度口径没变）",
      int(_call("value_count", _fresh_map())) == 2,
      str(_call("value_count", _fresh_map())))
check("`get(k)` 与 `values()` 同源：11 仍从 97 取到",
      int(_call("value_of_key", _fresh_map(), 97)) == 11,
      str(_call("value_of_key", _fresh_map(), 97)))

# ---------------------------------------------------------------- ③ 顺序
#
# 契约说"顺序未指定"，但 `keys()` 与 `values()` 必须**彼此一致**：同一个 Python
# dict 的键序/值序是同源的。若哪天有人给 `values()` 单独换了一种排序，这条会红。
print("[3] 顺序：`values()[i]` 与 `get(keys()[i])` 逐项一致")
_order_m = UnorderedMapVal()
for _k, _v in ((97, 11), (98, 2), (99, 7), (100, 3)):
    _order_m.data[_k] = _v


_keys = [int(_call("key_at", _fresh_map(), i)) for i in range(2)]
_vals = [int(_call("value_at", _fresh_map(), i)) for i in range(2)]
check("小表：keys = [97, 98]，values = [11, 2]",
      _keys == [97, 98] and _vals == [11, 2], f"keys={_keys} values={_vals}")

# 四元素表：逐项比 `values()[i]` 与 `get(keys()[i])`（同一份 receiver 的拷贝在
# 每次 `call` 里各造一次，所以用 0..3 的下标分别取）。
_PAIRS = {97: 11, 98: 2, 99: 7, 100: 3}
_ok_order = True
_seen = []
for _i in range(len(_PAIRS)):
    _k = int(_call("key_at", _order_m, _i))
    _v = int(_call("value_at", _order_m, _i))
    _seen.append((_k, _v))
    if _v != _PAIRS.get(_k):
        _ok_order = False
check("四个键值对：每个下标的值都等于该键对应的值", _ok_order, repr(_seen))
check("顺序未指定但**两边一致**（values 不是另排一遍）", _ok_order,
      f"pairs={_seen}")

# ---------------------------------------------------------------- ④ 反向
#
# 这次修复只碰 `UnorderedMapVal` 的 `values` 一条分支。下面三条钉住"别的都没动"：
#   · `unordered_set` 的 `keys()` / `values()`（`_is_map` 为假那条路）；
#   · 有序 `map.values()` **仍然**被 check 拒绝（`METHOD_TABLE` 里没有它）；
#   · 修前修后 `keys()` 在无序映射上是同一个顺序（插入序）。
print("[4] 反向：集合 / 有序 map / 其它语义一个字都没动")

SET_SRC = """u64 first(unordered_set<u64>& s, u64 i) {
    return s.values()[i];
}

u64 first_key(unordered_set<u64>& s, u64 i) {
    return s.keys()[i];
}
"""
_set_mod = parse_source(SET_SRC, "set.eln").module


def _call_set(fn, *args):
    return Interp(_set_mod, None).call(fn, list(args))


def _fresh_set():
    s = UnorderedSetVal()
    s.data.add(7)
    s.data.add(9)
    return s


check("`unordered_set.values()` 仍是元素（7）",
      int(_call_set("first", _fresh_set(), 0)) == 7,
      str(_call_set("first", _fresh_set(), 0)))
check("`unordered_set.keys()` 仍是元素（7）",
      int(_call_set("first_key", _fresh_set(), 0)) == 7,
      str(_call_set("first_key", _fresh_set(), 0)))

MAP_SRC = """u64 main() {
    map<u8, u64> m;
    m.insert(97, 11);
    return m.values().size();
}
"""
_mp = os.path.join(_root, "ordered_map_has_no_values.eln")
with open(_mp, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(MAP_SRC)
_rc, _o, _e = cli("run", _mp)
check("有序 `map.values()` 仍报 `E-Call-UnknownMethod`（没被顺手放开）",
      _rc != 0 and "E-Call-UnknownMethod" in (_o + _e), (_o + _e).strip()[:300])

# 插入序不变：`keys()` 的顺序与插入顺序一致（修前修后同一个口径）。
_ORDER_SRC = """u64 main() {
    unordered_map<u64, u64> m;
    m.insert(3, 30);
    m.insert(1, 10);
    m.insert(2, 20);
    for (u64 i = 0; i < m.keys().size(); i = i + 1) {
        print(m.keys()[i]);
    }
    for (u64 i = 0; i < m.values().size(); i = i + 1) {
        print(m.values()[i]);
    }
    return 0;
}
"""
_op = os.path.join(_root, "order.eln")
with open(_op, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(_ORDER_SRC)
_rc2, _o2, _e2 = cli("run", _op)
_lines2 = [x.strip() for x in _o2.splitlines() if x.strip()]
check("插入序 3/1/2 不变，值是 30/10/20（不是键）",
      _lines2 == ["3", "1", "2", "30", "10", "20", "0"],
      f"{_lines2!r}  rc={_rc2} {( _e2 or '').strip()[:200]}")

# ---------------------------------------------------------------- 汇总
shutil.rmtree(_root, ignore_errors=True)
print()
if FAILED:
    print(f"✗ 失败 {len(FAILED)} 条：")
    for _n in FAILED:
        print(f"    · {_n}")
    sys.exit(1)
print("✓ 全部通过：`values()` 返回值、`keys()` 仍返回键、其余容器未动")
sys.exit(0)
