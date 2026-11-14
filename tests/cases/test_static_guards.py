"""静态检查的"假绿灯"防线（批次 1）。

每个用例都对应一条**曾经 check 通过、run 才炸**的缺陷：

  1. 未知容器方法（`v.size()`）→ `E-Call-UnknownMethod`
  4. 常量下标越界（`a[9]`，容量 4）→ `E-Index-OutOfRange`
  5. `Point::new(3, 4)`（结构体没有 new）→ `E-Type-NoSuchMethod`
  6. 认不出的 target（`O(n(xs))`）→ `E-Target-BadForm`
  7. `deque<u64> d; d.push_back(1);`（容器只实现了内建半边）→ `E-Type-UnimplementedContainer`
  8. `unordered_map<vector<u8>, u64>`（容器键：2026-11-12 起**支持**，
     `stack` / `queue` / `map` / `set` / 结构体键仍 → `E-Type-UnhashableKey`）

  9. `const T&` 形参被改写（`sort(xs)` / `xs[0] = 1` / `xs.push_back(1)`）→ `E-Const-Mutate`
 10. `array<array<u64, 2>, 2>`（定长数组套容器）→ `E-Type-NestedContainer`

同时验证**不误报**：真实方法（`length`/`push`/`at`/`keys`/`values`）、
合法下标、结构体字面量、合法 target 都必须干净通过；
非 `const` 引用（`vector<u64>&`）的一切改写、只读操作、
`vector<vector<u64>>` / `map<u64, vector<u64>>` 也都必须干净通过。

跑法：`python tests/cases/test_static_guards.py`，退出码 0 即通过。
"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DSH = os.path.join(ROOT, "src", "dsh.py")
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

FAILED = []

SPEC = ("spec f() -> u64 { effects {}; target { time: { max: O(1) }, "
        "allocation: { max: 0 }, }; }\n")


def _check(eln: str, tu: str = SPEC) -> str:
    d = tempfile.mkdtemp(prefix="elench-guard-")
    with open(os.path.join(d, "m.eln"), "w", encoding="utf-8", newline="\n") as f:
        f.write(eln)
    with open(os.path.join(d, "m.tu"), "w", encoding="utf-8", newline="\n") as f:
        f.write(tu)
    r = subprocess.run([sys.executable, "-B", DSH, "check", "m"], cwd=d,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=ENV)
    return (r.stdout or "") + (r.stderr or "")


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name)
        print(f"  ✗ {name}  {detail[:200]}")


def main() -> int:
    # ---- 1. 未知方法 ----
    # **P4-0**：`size()` 现在是**合法方法**（`length` 的 C++ 名字）；
    # 「未知方法」改用它旁边那个瞎编的名字来测。
    out = _check("pub u64 f() { vector<u64> v; v.push_back(1); return v.size(); }\n")
    check("1a v.size() / v.push_back() 是合法方法（P4-0 改名后）",
          "E-Call-UnknownMethod" not in out, out)

    out = _check("pub u64 f() { vector<u64> v = {1}; return v.frobnicate(); }\n")
    check("1b 报错里列出支持的方法",
          "push_back" in out and "size" in out, out)
    check("1c 瞎编的方法也报", "E-Call-UnknownMethod" in out, out)

    out = _check("pub u64 f() { vector<u64> v = {1}; v.push_back(2); "
                 "return v.at(0) + v.size(); }\n")
    check("1d 真实方法不误报", "E-Call-UnknownMethod" not in out, out)

    out = _check("pub u64 f() { map<u64,u64> m; m.insert(1,2); "
                 "return m.keys().size(); }\n")
    check("1e map.keys 不误报", "E-Call-UnknownMethod" not in out, out)

    out = _check("pub u64 f() { set<u64> s; s.insert(1); "
                 "return s.values().size(); }\n")
    check("1f set.values 不误报", "E-Call-UnknownMethod" not in out, out)

    out = _check("pub u64 f() { stack<u64> s; s.push(1); "
                 "return s.top() + s.size(); }\n")
    check("1g stack.top/push 不误报", "E-Call-UnknownMethod" not in out, out)

    # ---- 4. 常量下标越界 ----
    out = _check("pub u64 f() { array<u64, 4> a; a[9] = 99; return a[0]; }\n")
    check("4a 写越界 → E-Index-OutOfRange", "E-Index-OutOfRange" in out, out)

    out = _check("pub u64 f() { array<u64, 4> a; return a[9]; }\n")
    check("4b 读越界 → E-Index-OutOfRange", "E-Index-OutOfRange" in out, out)

    out = _check("pub u64 f() { array<u64, 4> a; a[3] = 1; return a[3]; }\n")
    check("4c 合法下标不误报", "E-Index-OutOfRange" not in out, out)

    out = _check("pub u64 f() { vector<u64> v = {1}; return v[9]; }\n")
    check("4d vector 不判定（长度不是类型的一部分）",
          "E-Index-OutOfRange" not in out, out)

    # ---- 5. 结构体没有 new ----
    tu5 = ("struct Point { x: u64, y: u64, }\n" + SPEC)
    out = _check("pub u64 f() { Point p = Point::new(3, 4); return p.x; }\n", tu5)
    check("5a Point::new → E-Type-NoSuchMethod", "E-Type-NoSuchMethod" in out, out)
    check("5b 报错里给出正确写法", "Point {" in out, out)

    out = _check("pub u64 f() { Point p = Point { x: 3, y: 4 }; return p.x; }\n", tu5)
    check("5c 结构体字面量不误报", "E-Type-NoSuchMethod" not in out, out)

    # ---- 6. target 认不出 ----
    out = _check(
        "pub u64 f(const vector<u64>& xs) { return xs.size(); }\n",
        "spec f(const vector<u64>& xs) -> u64 { effects {}; "
        "target { time: { max: O(n(xs)) }, allocation: { max: 0 }, }; }\n")
    check("6a 非法 target → E-Target-BadForm", "E-Target-BadForm" in out, out)

    out = _check(
        "pub u64 f(const vector<u64>& xs) { return xs.size(); }\n",
        "spec f(const vector<u64>& xs) -> u64 { effects {}; "
        "target { time: { max: O(xs.size()) }, allocation: { max: 0 }, }; }\n")
    check("6b 合法 target 不误报", "E-Target-BadForm" not in out, out)

    out = _check(
        "pub u64 f(const vector<u64>& xs) { return xs.size(); }\n",
        "spec f(const vector<u64>& xs) -> u64 { effects {}; "
        "target { time: { max: 2 * xs.size() + 3 }, allocation: { max: 0 }, }; }\n")
    check("6c 四则运算 target 不误报", "E-Target-BadForm" not in out, out)

    # **除法**（2026-11-09）：`O(n / log(n))` 以前是"语法能过、语义层不认"，
    # `cost` 说"target 写了但解析失败"，契约整个不生效。
    out = _check(
        "pub u64 f(const vector<u64>& xs) { return xs.size(); }\n",
        "spec f(const vector<u64>& xs) -> u64 { effects {}; "
        "target { time: { max: O(n / log(n)) }, allocation: { max: 0 }, }; }\n")
    check("6d O(n / log(n)) 不误报（除法已认）", "E-Target-BadForm" not in out, out)

    out = _check(
        "pub u64 f(const vector<u64>& xs) { return xs.size(); }\n",
        "spec f(const vector<u64>& xs) -> u64 { effects {}; "
        "target { time: { max: O(n / 0) }, allocation: { max: 0 }, }; }\n")
    check("6e 分母为 0 不误报（判阶退 ⊤，不是解析失败）",
          "E-Target-BadForm" not in out, out)

    # `%` **不在** target 成本式的运算里：形状层与语义层要认同一组（`+ - * /`），
    # 否则会出现"形状放行、语义拒绝"的两套口径。
    out = _check(
        "pub u64 f(const vector<u64>& xs) { return xs.size(); }\n",
        "spec f(const vector<u64>& xs) -> u64 { effects {}; "
        "target { time: { max: O(n % 3) }, allocation: { max: 0 }, }; }\n")
    check("6f `%` 仍报 E-Target-BadForm（形状层与语义层同一组运算）",
          "E-Target-BadForm" in out, out)

    # ---- 7. 未实现的容器（deque） ----
    #
    # `deque` **不是完全没写**：stdlib 里有 `deque_new` / `deque_push_*` 一组
    # 内建（环形缓冲），但 interp 的容器构造/方法分派没有 deque。所以
    # `deque<u64> d;` 被兜底成 0，`d.push_back(1)` 在 run 期才炸。check 必须挡住。
    out = _check("pub u64 f() { deque<u64> d; d.push_back(1); return d.size(); }\n")
    check("7a deque 声明 → E-Type-UnimplementedContainer",
          "E-Type-UnimplementedContainer" in out, out)
    check("7b 报错里列出已实现的容器",
          all(c in out for c in ("vector", "array", "map", "set",
                                 "unordered_map", "unordered_set",
                                 "stack", "queue")), out)
    check("7c 报错里说明 deque 未实现", "尚未实现" in out, out)

    out = _check("pub u64 f() { deque<u64> q = deque_new(); "
                 "return deque_len(q); }\n")
    check("7d deque_new 内建路径同样被挡（不许留半实现）",
          "E-Type-UnimplementedContainer" in out, out)

    out = _check(
        "pub u64 f() { return 0; }\n",
        "spec f(deque<u64> xs) -> u64 { effects {}; }\n")
    check("7e .tu 里的 deque 形参也报", "E-Type-UnimplementedContainer" in out, out)

    out = _check("pub u64 f() { vector<u64> v; v.push_back(1); "
                 "return v.size(); }\n")
    check("7f vector 不误报", "E-Type-UnimplementedContainer" not in out, out)
    out = _check("pub u64 f() { map<u64,u64> m; m.insert(1,2); "
                 "return m.size(); }\n")
    check("7g map 不误报", "E-Type-UnimplementedContainer" not in out, out)
    out = _check("pub u64 f() { set<u64> s; s.insert(1); "
                 "return s.size(); }\n")
    check("7h set 不误报", "E-Type-UnimplementedContainer" not in out, out)
    out = _check("pub u64 f() { stack<u64> s; s.push(1); "
                 "return s.size(); }\n")
    check("7i stack 不误报", "E-Type-UnimplementedContainer" not in out, out)

    # ---- 8. unordered_* 的键：容器类键（2026-11-12 起支持） ----
    #
    # `vector<T>` / `array<T,N>`（`Text` = `vector<u8>`）现在可以做 `unordered_*`
    # 的键：哈希按元素逐个混入（`stdlib.hash_key`），冲突后按**逐元素相等**比较，
    # 所以 `unordered_map<vector<u8>, u64>`（C++ 的 `unordered_map<string, u64>`）
    # 是合法程序。`stack` / `queue` / `map` / `set` / 结构体仍拒绝。
    out = _check("pub u64 f() { unordered_map<vector<u8>, u64> m; "
                 "m.insert(\"a\", 1); return m.get(\"a\"); }\n")
    check("8a unordered_map<vector<u8>,u64> 不再报 E-Type-UnhashableKey",
          "E-Type-UnhashableKey" not in out, out)

    out = _check("pub u64 f() { unordered_set<vector<u8>> s; "
                 "s.insert(\"a\"); return s.size(); }\n")
    check("8b unordered_set<vector<u8>> 不再报 E-Type-UnhashableKey",
          "E-Type-UnhashableKey" not in out, out)

    out = _check("pub u64 f() { unordered_map<vector<u64>, u64> m; "
                 "return m.size(); }\n")
    check("8c unordered_map<vector<u64>,u64> 键也是容器类键（放行）",
          "E-Type-UnhashableKey" not in out, out)

    out = _check("pub u64 f() { unordered_map<array<u8, 2>, u64> m; "
                 "return m.size(); }\n")
    check("8d 定长 array<u8,2> 键放行", "E-Type-UnhashableKey" not in out, out)

    out = _check("pub u64 f() { unordered_set<stack<u64>> s; "
                 "return s.size(); }\n")
    check("8e stack 做键仍报（没有逐元素哈希契约）",
          "E-Type-UnhashableKey" in out, out)

    out = _check("pub u64 f() { unordered_map<u64, vector<u8>> m; "
                 "m.insert(1, \"a\"); return m.size(); }\n")
    check("8f 值类型是 vector<u8> 不误报",
          "E-Type-UnhashableKey" not in out, out)

    out = _check("pub u64 f() { unordered_map<u64, u64> m; "
                 "m.insert(1, 2); return m.get(1); }\n")
    check("8g u64 键不误报", "E-Type-UnhashableKey" not in out, out)

    out = _check("pub u64 f() { unordered_set<u64> s; s.insert(1); "
                 "return s.size(); }\n")
    check("8h unordered_set<u64> 不误报", "E-Type-UnhashableKey" not in out, out)

    tu_enum = "enum Color { Red, Green, Blue }\n" + SPEC
    out = _check("pub u64 f() { unordered_set<Color> s; "
                 "s.insert(Color::Red); return s.contains(Color::Red); }\n", tu_enum)
    check("8i unordered_set<enum> 不误报", "E-Type-UnhashableKey" not in out, out)

    out = _check("pub u64 f() { unordered_map<map<u64,u64>, u64> m; "
                 "return m.size(); }\n")
    check("8j 容器类键（map）仍报", "E-Type-UnhashableKey" in out, out)
    check("8k 容器键不说「改用 map」能解决",
          "同样不能用" in out, out)

    tu_struct = "struct Point { x: u64, y: u64, }\n" + SPEC
    out = _check("pub u64 f() { unordered_map<Point, u64> m; "
                 "return m.size(); }\n", tu_struct)
    check("8l 结构体键仍报", "E-Type-UnhashableKey" in out, out)
    check("8m 结构体键的出路是把字段编码成标量",
          "编码成标量" in out, out)

    # `.tu` 的 spec 形参也要按同一套判定：容器键放行、结构体键拒绝。
    TU_UM = ("spec f(unordered_map<vector<u8>, u64> m) -> u64 { effects {}; "
             "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n")
    out = _check("pub u64 f(unordered_map<vector<u8>, u64> m) { "
                 "return m.size(); }\n", TU_UM)
    check("8n .tu 里 spec 的容器键形参不报",
          "E-Type-UnhashableKey" not in out, out)

    # ---- 9. `const T&` 不许被改写 ----
    #
    # 以前 `const vector<u64>& xs` 只是**标注**：`sort(xs)` 的 check 是
    # 「✓ 没有诊断」，run 把**调用方的向量**排了序；`xs[0] = 1` 甚至 run 也放行。
    # 解释器只在 `.push_back()` 这类方法上 run 期拦（`E-Borrow-Const`）。
    TU_CONST_V = ("spec f(const vector<u64>& xs) -> u64 "
                  "{ effects { alloc, io }; }\n")
    TU_CONST_S = ("spec f(const stack<u64>& xs) -> u64 "
                  "{ effects { alloc, io }; }\n")

    out = _check("pub func f(const vector<u64>& xs) -> u64 "
                 "{ sort(xs); return 0; }\n", TU_CONST_V)
    check("9a const 形参 sort(xs) → E-Const-Mutate", "E-Const-Mutate" in out, out)
    check("9b 消息指出形参名与声明行", "`xs`" in out and "第 1 行" in out, out)
    check("9c 消息给出改成可变借用的写法",
          "vector<u64>&" in out and "const" in out, out)

    out = _check("pub func f(const vector<u64>& xs) -> u64 "
                 "{ xs[0] = 1; return 0; }\n", TU_CONST_V)
    check("9d const 形参下标写 → E-Const-Mutate", "E-Const-Mutate" in out, out)

    out = _check("pub func f(const vector<u64>& xs) -> u64 "
                 "{ xs.push_back(1); return 0; }\n", TU_CONST_V)
    check("9e const 形参 .push → E-Const-Mutate（以前 run 才拦）",
          "E-Const-Mutate" in out, out)

    out = _check("pub func f(const stack<u64>& xs) -> u64 "
                 "{ xs.enqueue(1); return 0; }\n", TU_CONST_S)
    check("9f const 形参 .enqueue → E-Const-Mutate（以前两边都不拦）",
          "E-Const-Mutate" in out, out)

    out = _check(
        "pub u64 g(vector<u64>& ys) { return 0; }\n"
        "pub u64 f(const vector<u64>& xs) { return g(xs); }\n",
        TU_CONST_V + "spec g(vector<u64>& ys) -> u64 { effects {}; }\n")
    check("9g const 传给可变引用形参 → E-Const-Mutate",
          "E-Const-Mutate" in out, out)

    # **链式**：`at()` / `top()` 返回的是容器**内部**那个元素（实测改得到）。
    out = _check("pub func f(const vector<vector<u64>>& xs) -> u64 "
                 "{ xs.at(0).push_back(9); return 0; }\n",
                 "spec f(const vector<vector<u64>>& xs) -> u64 "
                 "{ effects { alloc, io }; }\n")
    check("9l const 形参 xs.at(0).push_back(…) → E-Const-Mutate", "E-Const-Mutate" in out, out)

    out = _check("pub func f(const map<u64,u64>& m) -> u64 "
                 "{ m.keys().push_back(99); return m.size(); }\n",
                 "spec f(const map<u64,u64>& m) -> u64 { effects { alloc, io }; }\n")
    check("9m m.keys() 是副本，不误报", "E-Const-Mutate" not in out, out)

    # **负例**：可变引用照常可写（这是"确实要改"的正确写法）。
    out = _check("pub func f(vector<u64>& xs) -> u64 "
                 "{ sort(xs); xs.push_back(1); xs[0] = 2; return 0; }\n",
                 TU_CONST_V.replace("const ", ""))
    check("9h 非 const 引用的一切改写不误报",
          "E-Const-Mutate" not in out, out)

    out = _check("pub func f(const vector<u64>& xs) -> u64 "
                 "{ return xs.size() + xs.at(0) + xs[1]; }\n", TU_CONST_V)
    check("9i 只读操作不误报", "E-Const-Mutate" not in out, out)

    out = _check("pub u64 f() { const vector<u64> v = {1}; "
                 "sort(v); return v.size(); }\n")
    check("9j const 局部被 sort → E-Const-Mutate", "E-Const-Mutate" in out, out)

    out = _check("pub u64 f() { const vector<u64> v = {1}; "
                 "v[0] = 2; return v[0]; }\n")
    check("9k const 局部的下标写仍是 E-Assign-Immutable（不重复报）",
          "E-Assign-Immutable" in out and "E-Const-Mutate" not in out, out)

    # ---- 10. 定长数组套容器 ----
    #
    # `interp._default_value_of` 的 `array<T,N>` 默认值是
    # `FixedList(cap, [_convert_to(0, T)] * cap)`——T 是容器时元素退化成
    # **裸整数 0**。于是 `g[0][0] = 7` 在 run 期 `TypeError: 'int' object
    # does not support item assignment`，而 check 说「✓ 没有诊断」。
    out = _check("pub u64 f() { array<array<u64, 2>, 2> g; "
                 "g[0][0] = 7; return g[0][0]; }\n")
    check("10a array<array<…>> → E-Type-NestedContainer",
          "E-Type-NestedContainer" in out, out)
    check("10b 消息说明「展平」与「下标换算」",
          "展平" in out and "下标换算" in out, out)
    check("10c 消息给出具体的展平示例（array<u64, 2 * 2> + g[r * 2 + c]）",
          "array<u64, 2 * 2>" in out and "g[r * 2 + c]" in out, out)

    out = _check("pub u64 f() { array<vector<u64>, 2> a; "
                 "print(a[0].size()); return 0; }\n")
    check("10d array<vector<…>> 也报（未赋值的元素就是裸 0）",
          "E-Type-NestedContainer" in out, out)

    # **负例**：这三种嵌套实测是能用的，必须干净通过。
    out = _check("pub u64 f() { vector<vector<u64>> v; "
                 "vector<u64> t = {1}; v.push_back(t); v[0].push_back(2); "
                 "return v[0].at(0) + v.size(); }\n")
    check("10e vector<vector<u64>> 不误报",
          "E-Type-NestedContainer" not in out, out)

    out = _check("pub u64 f() { map<u64, vector<u64>> m; "
                 "vector<u64> t = {7, 8}; m.insert(1, t); print(m.get(1)); "
                 "return m.size(); }\n")
    check("10f map<u64, vector<u64>> 不误报（实测能跑）",
          "E-Type-NestedContainer" not in out, out)

    out = _check("pub u64 f() { vector<array<u64, 2>> v; "
                 "array<u64, 2> t; t[0] = 5; v.push_back(t); "
                 "return v[0].at(0); }\n")
    check("10g vector<array<u64,2>> 不误报（push 后读得到 5）",
          "E-Type-NestedContainer" not in out, out)

    out = _check("pub u64 f() { array<u64, 2> g; g[0] = 7; "
                 "return g[0]; }\n")
    check("10h 一维 array 不误报", "E-Type-NestedContainer" not in out, out)

    # ---- 11. `.tu` / `.eln` 的 `const` 限定符必须一致 ----
    #
    # `const` 记在 `param` 节点上、不在类型字符串里，所以两边签名比对看不出差别：
    # `.tu` 写 `const vector<u64>&`、`.eln` 写 `vector<u64>&` 时 check 干净通过，
    # run 把**调用方的向量**改了（`xs.push_back(99)` 后长度 3 → 4）。
    out = _check("pub func f(vector<u64>& xs) -> u64 "
                 "{ xs.push_back(1); return 0; }\n", TU_CONST_V)
    check("11a .tu const / .eln 可变 → E-Spec-ConstMismatch",
          "E-Spec-ConstMismatch" in out, out)
    check("11b 消息说出两边分别是什么",
          ".tu" in out and ".eln" in out and "`xs`" in out, out)

    out = _check("pub u64 f(const vector<u64>& xs) { return 0; }\n",
                 TU_CONST_V.replace("const ", ""))
    check("11c .tu 可变 / .eln const → E-Spec-ConstMismatch",
          "E-Spec-ConstMismatch" in out, out)

    out = _check("pub u64 f(const vector<u64>& xs) { return 0; }\n",
                 TU_CONST_V)
    check("11d 两边都 const 不误报", "E-Spec-ConstMismatch" not in out, out)

    out = _check("pub u64 f(vector<u64>& xs) { return 0; }\n",
                 TU_CONST_V.replace("const ", ""))
    check("11e 两边都可变不误报", "E-Spec-ConstMismatch" not in out, out)

    # ---- 12. `format` 是变参（`arity` 只是下限 1） ----
    #
    # 文档 §5 教的是 `format("x = {}, y = {:>2}", x, y)`：第一个实参是格式串，
    # 后面每个洞一个值。漏掉的实参由 `checker._check_format_calls` 报
    # `E-Format-Arity`；typecheck 只看下限，不该按固定 arity 把多洞写法误杀。
    TU_FMT = "spec f() -> u64 { effects { io }; }\n"

    out = _check("pub func f() -> u64 "
                 "{ print(\"{}\", format(\"x = {}, y = {:>2}\", 7, 3)); return 0; }\n",
                 TU_FMT)
    check("12a 多洞 format 不再报 E-Type-ArgCount",
          "E-Type-ArgCount" not in out, out)
    check("12b 多洞 format 也没有 E-Format-Arity 误报",
          "E-Format-Arity" not in out, out)

    for label, body in (
            ("无洞", 'print("{}", format("no holes"));'),
            ("一洞", 'print("{}", format("{}", 1));'),
            ("两洞", 'print("{}", format("{}{}", 1, 2));')):
        out = _check("pub u64 f() { " + body + " return 0; }\n", TU_FMT)
        check(f"12c {label}的 format 不误报",
              "E-Type-ArgCount" not in out and "E-Format-Arity" not in out, out)

    out = _check("pub func f() -> u64 "
                 "{ print(format(\"{}\", 1, 2)); return 0; }\n", TU_FMT)
    check("12d 洞数不符 → E-Format-Arity（由 _check_format_calls 报）",
          "E-Format-Arity" in out, out)
    check("12e 报的是洞数错，不是 format 的固定 arity 错",
          "E-Type-ArgCount" not in out
          and "格式串有 1 个洞，但给了 2 个实参" in out, out)

    out = _check("pub u64 f() { format(); return 0; }\n", TU_FMT)
    check("12f 一个实参都不给 → E-Type-ArgCount（下限 1 仍守）",
          "E-Type-ArgCount" in out, out)

    out = _check("pub u64 f() { print(1, 2, 3); return 0; }\n", TU_FMT)
    check("12g print 的多实参不误报（本来就不是固定 arity）",
          "E-Type-ArgCount" not in out, out)

    out = _check("pub u64 f() { print(); return 0; }\n", TU_FMT)
    check("12h print() 仍报 E-Type-ArgCount（下限 1）",
          "E-Type-ArgCount" in out, out)

    # ---- 13. 算法类内建的实参个数（`map` / `filter` / `fold` / `count_if` 曾完全不查）----
    #
    # 这 9 个是**解释器直接实现**的（`stdlib.NATIVE`，不在 `FNS` 注册表里），
    # 所以 `stdlib.arity_ok` 那条路**结构上覆盖不到**它们：
    # 以前 `map(xs)` / `fold(xs, 0, add, 1)` 一律 check 通过。
    # 少给的运行期才炸；多给的更糟——解释器只查下限（`< 2` / `< 3`），
    # **多余的实参被静默忽略**，写错参数的人拿到一个"看起来合理"的结果。
    # 真正该查的次数是**从 `interp.py` 的实现读出来的**（见 `typecheck.BUILTIN_ARITY`），
    # 不是从文档抄的——文档曾把 `min`/`max` 写成 1 参，没有任何一处校验去戳穿它。
    HOF_PRELUDE = (
        "u64 twice(u64 x) { return x + x; }\n"
        "bool gt10(u64 x) { return x > 10; }\n"
        "u64 add(u64 a, u64 b) { return a + b; }\n"
        "bool le(u64 a, u64 b) { return a <= b; }\n"
        "u64 visit(u64 x) { return x; }\n")

    def _hof_spec(name: str, params: str, ret: str) -> str:
        return (f"spec {name}({params}) -> {ret} {{ effects {{}}; "
                f"target {{ time: {{ max: O(1) }}, allocation: {{ max: 0 }}, }}; }}\n")

    HOF_TU = "".join([
        _hof_spec("twice", "u64 x", "u64"),
        _hof_spec("gt10", "u64 x", "bool"),
        _hof_spec("add", "u64 a, u64 b", "u64"),
        _hof_spec("le", "u64 a, u64 b", "bool"),
        _hof_spec("visit", "u64 x", "u64"),
    ])

    def _hof_vec(body: str) -> str:
        return _check(
            HOF_PRELUDE + "pub u64 f(vector<u64>& xs) {\n    "
            + body + "\n}\n",
            HOF_TU + _hof_spec("f", "vector<u64>& xs", "u64"))

    def _hof_two(body: str) -> str:
        return _check(
            HOF_PRELUDE + "pub u64 f(u64 a, u64 b) {\n    "
            + body + "\n}\n",
            HOF_TU + _hof_spec("f", "u64 a, u64 b", "u64"))

    #: `sort` / `sort_by` 的 arity 先由 `checker._check_sort_calls` 报
    #: （`E-Sort-BadArgs`），其余 7 个由 `typecheck.BUILTIN_ARITY` 报
    #: `E-Type-ArgCount`——两条路的错误码不同，这里就按各自的码断言。
    ARITY_CASES = (
        ("sort", "return sort();", "return sort(xs, 1);",
         "return sort(xs);", _hof_vec, "E-Sort-BadArgs", 0, 2),
        ("sort_by", "return sort_by(xs);", "return sort_by(xs, le, 1);",
         "return sort_by(xs, le);", _hof_vec, "E-Sort-BadArgs", 1, 3),
        ("map", "const vector<u64> m = map(xs);\n    return m.size();",
         "const vector<u64> m = map(xs, twice, 1);\n    return m.size();",
         "const vector<u64> m = map(xs, twice);\n    return m.size();",
         _hof_vec, "E-Type-ArgCount", 1, 3),
        ("filter", "const vector<u64> m = filter(xs);\n    return m.size();",
         "const vector<u64> m = filter(xs, gt10, 1);\n    return m.size();",
         "const vector<u64> m = filter(xs, gt10);\n    return m.size();",
         _hof_vec, "E-Type-ArgCount", 1, 3),
        ("fold", "return fold(xs, 0);", "return fold(xs, 0, add, 1);",
         "return fold(xs, 0, add);", _hof_vec, "E-Type-ArgCount", 2, 4),
        ("count_if", "return count_if(xs);", "return count_if(xs, gt10, 1);",
         "return count_if(xs, gt10);", _hof_vec, "E-Type-ArgCount", 1, 3),
        ("min", "return min(a);", "return min(a, b, 1);",
         "return min(a, b);", _hof_two, "E-Type-ArgCount", 1, 3),
        ("max", "return max(a);", "return max(a, b, 1);",
         "return max(a, b);", _hof_two, "E-Type-ArgCount", 1, 3),
        ("for_each", "return for_each(xs);", "return for_each(xs, visit, 1);",
         "return for_each(xs, visit);", _hof_vec, "E-Type-ArgCount", 1, 3),
    )

    for (nm, few, many, good, probe, code, n_few, n_many) in ARITY_CASES:
        out = probe(few)
        check(f"13 {nm} 少给实参 → {code}",
              code in out and f"给了 {n_few}" in out, out)
        out = probe(many)
        check(f"13 {nm} 多给实参 → {code}",
              code in out and f"给了 {n_many}" in out, out)
        # **不误报**：正确的调用（高阶内建传的是**具名函数值**）必须干净通过。
        out = probe(good)
        check(f"13 {nm} 正确调用零诊断",
              "没有诊断" in out and "E-" not in out, out)

    # 同名**用户函数**不是内建：不能按内建的 arity 误杀
    out = _check(
        "pub u64 map(vector<u64>& xs) { return xs.size(); }\n"
        "pub u64 f(vector<u64>& xs) { return map(xs); }\n",
        _hof_spec("map", "vector<u64>& xs", "u64")
        + _hof_spec("f", "vector<u64>& xs", "u64"))
    check("13 用户自定义的 map(xs) 不按内建 arity 误报",
          "E-Type-ArgCount" not in out, out)

    # ---- 14. 未知类型名（`E-Type-Unknown`）----
    #
    # 以前这里有两个洞：
    #   · `u128 x = 1;` → `E-Type-Assign 声明为 u128，初值是 i64`——
    #     **消息把人引到初值上**，类型名本身没人查；
    #   · `u128 x;` / `foo_t x;`（无初值）→ **零诊断**（更宽、更危险的那个洞）。
    # 现在两种都报「未知类型名」，且**不再**附带 `E-Type-Assign`。
    for probe, label in (
            ("u64 y = 0; u128 x = 1; return y;", "u128 有初值"),
            ("u64 y = 0; u128 x; return y;", "u128 无初值"),
            ("u64 y = 0; i128 x = 1; return y;", "i128 有初值"),
            ("u64 y = 0; int128_t x = 1; return y;", "int128_t 有初值"),
            ("u64 y = 0; foo_t x; return y;", "foo_t 无初值"),
    ):
        out = _check("pub u64 f() { %s }\n" % probe)
        check(f"14 {label} → E-Type-Unknown", "E-Type-Unknown" in out, out)
        check(f"14 {label} 不再报误导的 E-Type-Assign",
              "E-Type-Assign" not in out, out)
    # **`size_t` / `uint32_t` 不是未知类型**（R1-A，2026-11-10）：它们是 C++ 的
    # 宽度名，归一成 `u64` / `u32`。以前这里是反例，现在必须零诊断。
    for probe in ("u64 y = 0; size_t x = 1; return y;",
                  "u64 y = 0; uint32_t x = 1; return y;",
                  "u64 y = 0; int64_t x = 1; return y;"):
        out = _check("pub u64 f() { %s }\n" % probe)
        check(f"14 `{probe.split('; ')[1]}` 已归一 → 不报 E-Type-Unknown",
              "E-Type-Unknown" not in out, out)
    out = _check("pub u64 f() { f32 x = 1; return 0; }\n")
    check("14 f32 是真类型，不许报 E-Type-Unknown",
          "E-Type-Unknown" not in out, out)
    out = _check("pub u64 f(u128 n) { return 1; }\n",
                 "spec f(u128 n) -> u64 { effects {}; "
                 "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n")
    check("14 形参类型 u128 → E-Type-Unknown", "E-Type-Unknown" in out, out)
    # `deque` 是**已登记但未接线**的容器：只报 E-Type-UnimplementedContainer，
    # 不许再被未知类型检查重复报一遍（`BUILTIN_TYPE_NAMES` 里的例外）。
    out = _check("pub u64 f() { deque<u64> d; return d.size(); }\n")
    check("14 deque 只报 UnimplementedContainer，不报未知类型名",
          "E-Type-UnimplementedContainer" in out and "E-Type-Unknown" not in out,
          out)

    # ---- 15. `goto` / 标签 / 前缀 `*` / 多余的 `&`（E-Keyword / E-Unary）----
    out = _check("pub u64 f() { goto done; return 0; }\n")
    check("15 `goto done;` → E-Keyword-Unsupported（不再变成 var-decl）",
          "E-Keyword-Unsupported" in out, out)
    check("15 goto 的说明给出改法", "while" in out and "return" in out, out)
    out = _check("pub u64 f() { done: return 0; }\n")
    check("15 `done:` 标签 → E-Keyword-Unsupported",
          "E-Keyword-Unsupported" in out, out)
    _io_spec = ("spec f() -> u64 { effects { io }; "
                "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n")
    out = _check("pub u64 f() { u64 x = 1; print(*x); return 0; }\n",
                 _io_spec)
    check("15 `print(*x)` → E-Unary-Unsupported", "E-Unary-Unsupported" in out, out)
    out = _check("pub u64 f() { u64 x = 1; print(&x); return 0; }\n",
                 _io_spec)
    check("15 `print(&x)`（内建，不是引用形参）→ E-Unary-Unsupported",
          "E-Unary-Unsupported" in out, out)
    out = _check("pub u64 f() { u64 x = 1; u64 y = &x; return y; }\n")
    check("15 `u64 y = &x;` → E-Unary-Unsupported",
          "E-Unary-Unsupported" in out, out)
    out = _check(
        "pub u64 g(u64 n) { return n + 1; }\n"
        "pub u64 f() { u64 x = 1; return g(&x); }\n",
        "spec g(u64 n) -> u64 { effects {}; "
        "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n"
        "spec f() -> u64 { effects {}; "
        "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n")
    check("15 `g(&x)`（形参不是引用）→ E-Unary-Unsupported",
          "E-Unary-Unsupported" in out, out)
    # **不误报**：实参 + `T&` 形参时 `&` 是**显式借用标记**（calculator.eln 依赖）。
    out = _check(
        "pub u64 take(array<u64, 2>& a) { a[0] = 1; return a[0]; }\n"
        "pub u64 f() { array<u64, 2> a = {1, 2}; return take(&a); }\n",
        "spec take(array<u64, 2>& a) -> u64 { effects {}; "
        "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n"
        "spec f() -> u64 { effects {}; "
        "target { time: { max: O(1) }, allocation: { max: 0 }, }; }\n")
    check("15 `take(&a)`（实参 + 引用形参）是显式借用标记，不报",
          "E-Unary-Unsupported" not in out, out)
    # **乘法不许被打坏**：二元 `*` 与二元 `&` 都走 `parse_expr` 的 BINARY 表。
    out = _check("pub u64 f() { u64 a = 6; u64 b = 7; "
                 "return a * b + (a & b); }\n")
    check("15 `a * b` / `a & b`（二元）零诊断，没被前缀规则误伤",
          "E-" not in out, out)

    # ---- 16. 指针：语法/类型/运行期都已接线 → 零诊断 ----
    #
    # 口径：指针是语言的一部分，`&x` / `*p` / `p->f` / `nullptr` 全程可用；
    #      静态层只保留 `E-Pointer-NullDeref`（可证的空解引用）与
    #      `E-Pointer-NoBound`（链式遍历给不出上界）。
    #      `E-Pointer-RuntimeUnsupported` 已于 2026-11-10 退役（运行期接线完成）。
    out = _check("pub u64 f() { u64 x = 3; u64* p = &x; "
                 "u64 y = *p; return y; }\n", tu="")
    check("16 `u64* p = &x; u64 y = *p;` 类型层通过（运行期已接线，无指针诊断）",
          "E-Pointer" not in out and "E-Type-Assign" not in out
          and "E-Unary-Unsupported" not in out, out)
    out = _check("pub u64 f(int argc, char** argv) { return 0; }\n", tu="")
    check("16 `char** argv`（指向指针的指针）类型层通过",
          all(c not in out for c in ("E-Parse", "E-Type-", "E-Unary")), out)
    out = _check("pub u64 f() { u64 x = 3; return *x; }\n", tu="")
    check("16 非指针解引用 `*x` → E-Unary-Unsupported（按类型判）",
          "E-Unary-Unsupported" in out, out)
    out = _check("pub u64 f() { u64 x = 3; u64 y = &x; return y; }\n", tu="")
    check("16 `u64 y = &x;` 仍被拒绝（取地址的结果是指针）",
          "E-Unary-Unsupported" in out and "E-Type-Assign" in out, out)
    out = _check("pub u64 f(u64* p) { if (p) { return 1; } return 0; }\n",
                 tu="")
    check("16 `if (p)` 当条件不报 E-Type-Cond", "E-Type-Cond" not in out, out)
    out = _check("pub u64 f() { u64* p = nullptr; return *p; }\n", tu="")
    check("16 可证的空解引用 → E-Pointer-NullDeref（静态拦）",
          "E-Pointer-NullDeref" in out, out)
    out = _check("pub u64 f(u64* p) { while (p != nullptr) { p = p; } "
                 "return 0; }\n", tu="")
    check("16 指针遍历、条件里没有尺寸量 → E-Pointer-NoBound（明确报给不出上界）",
          "E-Pointer-NoBound" in out, out)
    out = _check("pub u64 f(S* q) { return q.x; }\n",
                 tu="struct S { x: u64, };\n")
    check("16 指针上写 `.` → E-Pointer-UseArrow", "E-Pointer-UseArrow" in out, out)
    out = _check("pub u64 f(S* q) { return q->x; }\n",
                 tu="struct S { x: u64, };\n")
    check("16 `q->x` 与 `(*q).x` 同形，只剩运行期未接",
          "E-Pointer-UseArrow" not in out
          and "E-Unary-Unsupported" not in out, out)
    # **值语义不许破**：`S* q = &s;` 之后 `q->x` 改的是 `s`（别名）。
    # 运行期未接时这条**跑不了**，但类型层必须接受这种写法（不是拷贝）。
    out = _check("pub u64 f() { S s = S { x: 1 }; S* q = &s; "
                 "u64 v = q->x; return v; }\n", tu="struct S { x: u64, };\n")
    check("16 `S* q = &s;` + `q->x` 类型层通过（别名写法，不是拷贝）",
          "E-Type-Assign" not in out and "E-Unary-Unsupported" not in out, out)

    print()
    if FAILED:
        print(f"✗ 失败 {len(FAILED)} 项：{FAILED}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
