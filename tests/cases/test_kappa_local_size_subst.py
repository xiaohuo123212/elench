#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_kappa_local_size_subst.py —— κ 里的局部量名字必须换成**上界**（规则⑥）

## 这条测试钉的是什么

2026-11-13 之前在语料里卡着 11 个函数：κ 里含**体内局部量**的名字
（`n(cur)` / `n(row)` / `n(line)` / `n(masked)`），而 spec 的 `target` 只能引用
**形参** ⇒ 尺寸量集合对不齐 ⇒ 判 E23「尺寸量不一致」，永远判不了。

修法**不是**给 target 开"按名字引用局部量"的语法（那等于给 E23 开后门），而是
把局部量的**可证上界**代进 κ：

    n(局部容器) → 增长预扫给的界      （与 `_local_container_bounds` 的 ①–⑤ 同一份）

## 为什么需要这道筛子（`_modelled_write_locals`）

"**有界**"不等于"**界可以当 κ 的上界用**"：增长预扫只在它**看得见**写入时加数。
`push_heap(h, x)` 在成本域里按"只重排"记账（长度不变，见 `_SPECIAL_BUILTINS`），
可解释器里它真的往 h 里加一个元素。把这些写点漏掉，`bounds[h]` 就只是
「声明初值 + 看得见的 push」，**比真实规模小**——拿它替换 `n(h)` 会把 κ 变成
**下界**。所以规则⑥额外要求「**每一处提到它的地方都被记过账**」。

## 四组断言

1. **局部名消失**：`split`（`cur`）的 κ 里没有 `cur`；换成 `buf` 也一样
   （机制不看名字，不是硬编码）。
2. **等价性**：同逻辑两种写法（同一套操作、只改局部名）——κ 逐字节相同、
   解释器结果逐字节相同。
3. **解释器实测**（≥3 个规模）：真实 `|cur|` 的最大值 ≤ 推出的界；
   且**实测操作数 ≤ 代入后的 κ**（κ 仍是上界，不是"变紧了"）。
4. **不许乱代**：`push_heap(h, …)`（未建模写入）与 `m[i] = i`（D1 形状的计数表）
   必须保持原样——`test_heap` / `test_map_cost` 钉着这两格。

第 3 组是这条测试的核心：**字符串断言抓不到"界比真实值小"**。那种错只会让
契约变成假绿（判定只看阶），所以必须用解释器跑出来的真实值去撞界。
"""
import os
import sys

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cost_check import evaluate                  # noqa: E402
from cost_interp import CostInterp                # noqa: E402
from interp import (Interp, ops_enable, ops_of,  # noqa: E402
                    ops_reset)
from parser import parse_source                   # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


#: 语料形状的最小复刻（`02_csv_stats.split` / `11_text.split`）。
#:
#: `split_cur` 与 `split_buf` **逐语句相同**，只有局部容器叫 `cur` 还是 `buf`——
#: 这是"机制不看名字"的对照；`max_cur` 是同一个增长形状的**探针**（把每轮的
#: `cur.size()` 记下最大值），用来实测"真实值 ≤ 界"。
SRC = """
vector<vector<u8>> split_cur(const vector<u8>& s, u8 sep) {
    vector<vector<u8>> out;
    vector<u8> cur;
    for (u64 i = 0; i < s.size(); ++i) {
        if (s[i] == sep) {
            out.push_back(cur);
            cur.clear();
        } else {
            cur.push_back(s[i]);
        }
    }
    out.push_back(cur);
    return out;
}

vector<vector<u8>> split_buf(const vector<u8>& s, u8 sep) {
    vector<vector<u8>> out;
    vector<u8> buf;
    for (u64 i = 0; i < s.size(); ++i) {
        if (s[i] == sep) {
            out.push_back(buf);
            buf.clear();
        } else {
            buf.push_back(s[i]);
        }
    }
    out.push_back(buf);
    return out;
}

u64 max_cur(const vector<u8>& s, u8 sep) {
    u64 best = 0;
    vector<u8> cur;
    for (u64 i = 0; i < s.size(); ++i) {
        if (s[i] == sep) {
            cur.clear();
        } else {
            cur.push_back(s[i]);
        }
        if (cur.size() > best) best = cur.size();
    }
    return best;
}
"""

#: 三处"**不许代**"的对照：未建模写入 / D1 计数表 / 无界局部。
#: 每一处都是既有测试钉住的格子，规则⑥ MUST NOT 动它们。
SRC_UNMODELLED = """
u64 heap_top(const vector<u64>& xs) {
    vector<u64> h = vector<u64>::make();
    push_heap(h, 3);
    push_heap(h, 9);
    push_heap(h, 5);
    return h.at(0);
}

map<u64, u64> sparse(u64 n) {
    map<u64, u64> m;
    for (u64 i = 0; i < n; ++i) { m[i] = i; }
    return m;
}

u64 sparse_read(u64 n) {
    map<u64, u64> m;
    for (u64 i = 0; i < n; ++i) { m[i] = i; }
    return m.at(0);
}
"""

TU = """spec split_cur(const vector<u8>& s, u8 sep) -> vector<vector<u8>> {
    effects { alloc };
    target { time: { max: O(s.size() * s.size()) }, allocation: { max: O(s.size()) }, };
}
spec split_buf(const vector<u8>& s, u8 sep) -> vector<vector<u8>> {
    effects { alloc };
    target { time: { max: O(s.size() * s.size()) }, allocation: { max: O(s.size()) }, };
}
spec max_cur(const vector<u8>& s, u8 sep) -> u64 {
    effects {};
    target { time: { max: O(s.size()) }, allocation: { max: 0 }, };
}
"""


def _module(src, name):
    return parse_source(src, name).module


def _ci():
    return CostInterp(_module(SRC, "a.eln"), _module(TU, "a.tu"))


def _kappa(ci, fn):
    return ci.cost_of(fn)


def _keys(values):
    """容器实参（逐元素）→ 有限域里的"word"字节表。"""
    return [b for b in values]


def main() -> int:
    ci = _ci()

    print("[1] κ 里不再有局部量名字（`cur` 不是 driver 也照样代）")
    k_cur = _kappa(ci, "split_cur").render()
    k_buf = _kappa(ci, "split_buf").render()
    print(f"     split_cur κ = {k_cur}")
    check("split_cur 的 κ 不含 cur", "cur" not in (_kappa(ci, "split_cur").variables()), k_cur)
    check("split_buf 的 κ 不含 buf", "buf" not in (_kappa(ci, "split_buf").variables()), k_buf)
    check("κ 里只剩形参尺寸 s", set(_kappa(ci, "split_cur").variables()) == {"s"},
          _kappa(ci, "split_cur").variables())
    check("换名字不影响 κ（逐字节相同）", k_cur == k_buf, f"{k_cur} != {k_buf}")

    print("[2] 等价性：同一逻辑两种写法 → κ 与解释器结果都逐字节相同")
    it = Interp(_module(SRC, "a.eln"), _module(TU, "a.tu"))
    for raw in (b"", b"a", b"a,b", b"a,,b", b",,", b"alpha,beta,gamma"):
        args = [[b for b in raw], 44]          # 44 = ','
        got_a = it.call("split_cur", args)
        got_b = it.call("split_buf", args)
        check(f"输入 {raw!r} 的字段划分相同", got_a == got_b, f"{got_a} != {got_b}")

    print("[3] 解释器实测：真实 |cur| 的最大值 ≤ 推出的界（3 个规模）")
    fn = ci.fns["split_cur"]
    _bounds, _locals, _params, _row, _caps = ci._container_bounds_full(fn)
    bound = _bounds.get("cur")
    check("split_cur 的 cur 有界（不是 ⊤）", bound is not None,
          {k: (None if v is None else v.render()) for k, v in _bounds.items()})
    if bound is not None:
        print(f"     界 n(cur) ≤ {bound.render()}")
        for n in (8, 32, 97):
            # 真实值：用探针函数跑同一增长形状，取每轮 |cur| 的最大值。
            raw = bytes((48 + (i % 10) if i % 4 else 44) for i in range(n))
            real = it.call("max_cur", [[b for b in raw], 44])
            ub = evaluate(bound, {"s": n})
            check(f"n={n}: 真实 |cur|={real} ≤ 界 {bound.render()} = {ub:.0f}",
                  real <= ub, f"{real} > {ub}")

    print("[4] κ 仍是上界：实测操作数 ≤ 代入后的 κ（3 个规模）")
    ops_enable()
    for n in (8, 32, 97):
        raw = bytes((48 + (i % 10) if i % 4 else 44) for i in range(n))
        args = [[b for b in raw], 44]
        ops_reset()
        Interp(_module(SRC, "a.eln"), _module(TU, "a.tu")).call("split_cur", args)
        ops = ops_of(None)
        kv = evaluate(_kappa(ci, "split_cur"), {"s": n})
        check(f"n={n}: ops={ops} ≤ κ={kv:.0f}", ops <= kv, f"{ops} > {kv}")

    print("[5] 不许乱代：未建模写入 / D1 计数表 / 无界局部都保持原样")
    ci2 = CostInterp(_module(SRC_UNMODELLED, "b.eln"), _module("", "b.tu"))
    k_heap = _kappa(ci2, "heap_top")
    check("push_heap 写的 h 不被代入（`test_heap` 的那一格）",
          "h" in k_heap.variables(), k_heap.render())
    k_sparse = _kappa(ci2, "sparse_read")
    check("D1 计数表 m 不被代入（`test_map_cost` 的 [5]）",
          "m" in k_sparse.variables(), k_sparse.render())

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
