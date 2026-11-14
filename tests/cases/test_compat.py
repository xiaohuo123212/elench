# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""**P0 兼容层（`--compat`）：只加拼写，不加含义。**

## 这条测试守的是什么

用户定的铁律（日程 D1）：

> 同一段逻辑写两遍（C++ 拼写 / 我们的拼写）
>   → `check` 两遍：**κ 必须逐字节相同**
>   → `run` 两遍：**输出必须逐字节相同**
> 不满足 ⇒ 那个"拼写"偷偷改了含义，退回。

所以下面每个用例都是**成对**的：`cpp`（C++ 拼写，要 `--compat`）与
`ours`（本语言拼写，**不带** `--compat`）。两边的文件名都叫 `mod`，
比较的是：

| 比什么 | 怎么比 | 强度 |
|---|---|---|
| κ | `cost --signature` 的**整份 JSON 逐字节** | 比"κ 字符串相同"更强：target / allocation / verdict 也一起比 |
| 运行输出 | `run` 的 stdout **逐字节** | 用户看到的东西 |
| AST（第 2–7 条） | `parse --json` 的 `ast`，**去掉行列号**后逐字节 | "剥掉语法糖之后还是同一个程序" |

## 还钉住了"默认不变"

`--compat` 之外的行为 MUST 与以前逐字节相同。所以另外一组用例断言：
第 1–5 条在**不带** `--compat` 时照旧报错（并给出具体诊断码）。

第 6 / 7 条**以前就不报错**，如实钉住它们的**真实**旧行为（见各函数注释）——
不编一个"报错"来凑数。

## 八个用例对应的八条

1. `.eln` 里的 `struct S { u64 a; };`
2. `using X = Y;` / `typedef X Y;`
3. `auto x = expr;`
4. `T{a, b}` / `vector<u64> v{1,2,3}`
5. `f(xs.begin(), xs.end())` → `f(xs)`
6. `std::` 白名单
7. 返回类型 `void` → `()`
8. 函数声明开头的 `static` → 跳过（不产出节点）

第 2 轮加的**第 9 / 10 条**（同样是"只加拼写"）：

9. 多声明符 `u64 a = 1, b = 2;` → **多个兄弟声明节点**（与手写多行同形）
10. 直接构造 `T x(args…)` → 单实参拷贝初始化 / `vector<T>(n, v)` →
    `vector<T>::filled(v, n)`；没有等价物的形状报 `E-Compat-CtorUnsupported`

第 3 轮加的**第 11 条** + 字符分类内建：

11. `static_cast<unsigned char>(x)` / `static_cast<u64>(x)` → **只在值逐位
    不变时**折成 `x`；窄化 / 有符号 / 类型推不出 → `E-Cast-Unsupported`
    （判据见 `typecheck._static_cast_fold_ok`，**只有这一份**）。
    同轮把 `<cctype>` 的八个名字加进 `std::` 白名单，并让它们在本语言里
    真能跑（ASCII 口径，见 `文档/内建函数.md`「字符分类」）。

第 4 轮加的**第 12 条**（三段式 `for` 的迭代器，见 `[23]` / `[24]`）：

12. `for ([T] it = xs.begin(); it != xs.end(); ++it) { *it; it->f; }` →
    **降级成普通索引循环**（`it` → 新导入的 `u64 idx`；`*it`→`xs[idx]`、
    `it->f`→`xs[idx].f`、`xs.begin()`→`0`、`xs.end()`→`xs.size()`、
    `++it`→`idx = idx + 1`）。三种拼写（① `auto` / ② 具体类型 /
    ③ `it` 已在外声明）落到**同一棵树**，所以 κ / run / AST 三样都与手写
    索引循环逐字节相同。其余形状一律拒绝（`E-For-IteratorTerm` /
    `Reverse` / `Arith` / `Mutate` / `Escape` / `Shape`），**每条都教改法**。
    **已知缺口**：降级目标是"位置下标"，对 `map<K,V>` 不成立（本语言的
    map 没有位置索引）——地图遍历 `check` 能过、`run` 不等价，见
    `文档/语法清单.md` §P0 第 12 条的"已知缺口"。

第 5 轮加的**第 13 条**之后，又有**第 15 / 16 条**（同样的只加拼写）：

15. `class S { public: … private: … };` → 与 `struct S { … };` **完全同义**
    （本语言没有访问控制）：走同一条 `parse_struct`，成员函数 / 构造函数的
    降级直接复用第 13 条；访问说明符 `public:` / `private:` / `protected:`
    接受并忽略。类型位置的 `class S* p` 就是 `S* p`。
16. 字段默认初始值 `u64 a = 3;` / `a: u64 = 3,` → **构造函数体开头的赋值**：
    没有构造函数时合成零参构造函数；有构造函数时排在初始化列表之前
    （列表里已给出的字段跳过）。默认值**不许引用其它字段** →
    `E-Compat-DefaultMemberRef`。

第 6 轮的 `std::move`（`[35]`）是**第三档**——既不是"改名"（A）也不是
"没有等价物"（C），而是 **B 档：删除式**：
`std::move(e)` → **直接落成 `e` 的 AST**（parser 不产生任何包装节点）。
本语言没有移动语义（赋值 / 传参 / 返回都是深拷贝），所以删掉它**同语义**：
`cost --signature` 与 `run` 输出与手写 `e` **逐字节相同**。形状只认
**恰好一个**实参，`std::move(a, b)` / `std::move()` / 裸 `std::move` 报
`E-Compat-StdDeleteArity`。

第 7 轮加的**第 18 / 19 条**（`[38]`–`[39b]`，同样是"只加拼写"）：

18. 整数字面量后缀 `1ULL` / `1ul` / `0xFFULL` / `0b1010U` → **同一个整数
    字面量**（本语言的整数字面量本来就是 `u64`，后缀只说明 C++ 那边的宽度，
    **值不变**）。落点在 parser 入口把 `INT` + 紧邻的 `IDENT` 合成一个 token，
    所以 AST 与手写 `1` **逐字节同形**。`f` / `F` 是**浮点**后缀（`1.5f`），
    不在整数后缀表里；`1f` 在 C++ 里也非法，照旧报错。
19. 复合赋值 `|=` `&=` `^=` `%=` `<<=` `>>=` → **与手写 `x = x <op> y`
    同形的 AST**（`assign(place, binary(op, deepcopy(place), rhs))`）。
    `+= -= *= /=` 是既有语法，本轮不动。已知边界：左侧下标表达式会**求值
    两次**（`a[f()] |= m`）——与既有的 `a[f()]++` 同一口径，因为本语言没有
    左值引用这层抽象；要"只求值一次"是语义扩展，不在"只加拼写"的范围里。

## 另外钉住的既有行为（不是兼容层新增）

`for (…; ++i)` / `i++` / `--j` / `j--` 是 **2026-11-10 起既有的语法**
（不是本轮 `--compat` 新增）；这里补成对用例，钉住它与 `i = i ± 1`
**κ / run 逐字节相同**。`a[i++]`（表达式位置带值）仍拒绝。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(ROOT, "src")
PY = sys.executable

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


# ---------------------------------------------------------------- CLI

def cli(sub, *args, cwd=None):
    """跑一次工具链，返回 `(rc, stdout, stderr)`（UTF-8，逐字节保留）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = SRC
    argv = [PY, "-B", os.path.join(SRC, "elench.py"), sub] + list(args)
    p = subprocess.run(argv, cwd=cwd or ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


class Pair:
    """一个成对用例的临时目录：`cpp/` 与 `ours/`，两边都叫 `mod.tu` / `mod.eln`。"""

    def __init__(self, root):
        self.root = root
        self.cpp = os.path.join(root, "cpp")
        self.ours = os.path.join(root, "ours")

    def write(self, side, tu, eln):
        d = self.cpp if side == "cpp" else self.ours
        os.makedirs(d, exist_ok=True)
        if tu is not None:
            io.open(os.path.join(d, "mod.tu"), "w", encoding="utf-8",
                    newline="\n").write(tu)
        io.open(os.path.join(d, "mod.eln"), "w", encoding="utf-8",
                newline="\n").write(eln)


def _strip_pos(obj):
    """去掉 AST 里的行列号：比的是**形状**，不是位置。"""
    if isinstance(obj, dict):
        return {k: _strip_pos(v) for k, v in obj.items()
                if k not in ("line", "col")}
    if isinstance(obj, list):
        return [_strip_pos(x) for x in obj]
    return obj


def ast_of(path_json_text):
    data = json.loads(path_json_text)
    return _strip_pos(data.get("ast"))
#: AST 比较走**进程内**解析：`parse` 子命令没有 `--json`（它打的是 dump 文本），
#: 而"剥掉语法糖之后是不是同一棵树"要结构化形状（去行列号）。
sys.path.insert(0, SRC)
import compat as _compat                          # noqa: E402
from parser import parse_source as _parse_source  # noqa: E402


def ast_norm(text, compat_on, name="mod.eln"):
    """解析一段源码，返回**去掉行列号**的 AST 形状。

    `compat_on=True` 表示"这段源码用的是 C++ 拼写"——走的是与 `--compat`
    **同一个布尔**（`compat.enabled()`），不是另一条解析路径。
    """
    _compat.set_enabled(bool(compat_on))
    try:
        res = _parse_source(text, name)
    finally:
        _compat.set_enabled(False)
    return _strip_pos(res.module.to_dict() if res.module is not None else None)


# ---------------------------------------------------------------- 用例

#: 用例格式：`(键, 标题, cpp 的 .tu 或 None, cpp 的 .eln,
#:             ours 的 .tu 或 None, ours 的 .eln, AST 是否应同形)`。
#: `None` 表示"没有那个文件"（裸 `.eln`，与样例目录里的用法一致）。
CASES = [
    (
        "1-struct",
        "第 1 条：`.eln` 里的 `struct S { u64 a; };`",
        None,
        # C++ 拼写：struct 和实现写在**同一个 .eln**（就是 .cpp 改名的样子）
        """struct Point { u64 x; u64 y; };

u64 add(Point p) { return p.x + p.y; }

u64 main() {
    Point p = Point { x: 3, y: 4 };
    return add(p);
}
""",
        # 我们的拼写：struct 在 .tu（分工），.eln 只有实现
        """struct Point { x: u64, y: u64, };
""",
        """u64 add(Point p) { return p.x + p.y; }

u64 main() {
    Point p = Point { x: 3, y: 4 };
    return add(p);
}
""",
        False,      # struct 在两边的**文件**不同，AST 本来就不该相等
    ),
    (
        "2-alias",
        "第 2 条：`using X = Y;` / `typedef X Y;`",
        None,
        """using U64Vec = vector<u64>;
typedef vector<u64> U64Vec2;

u64 total(U64Vec xs, U64Vec2 ys) {
    u64 s = 0;
    for (auto x : xs) { s = s + x; }
    for (auto y : ys) { s = s + y; }
    return s;
}

u64 main() {
    U64Vec xs = {1, 2, 3};
    U64Vec2 ys = {4, 5};
    return total(xs, ys);
}
""",
        None,
        """u64 total(vector<u64> xs, vector<u64> ys) {
    u64 s = 0;
    for (auto x : xs) { s = s + x; }
    for (auto y : ys) { s = s + y; }
    return s;
}

u64 main() {
    vector<u64> xs = {1, 2, 3};
    vector<u64> ys = {4, 5};
    return total(xs, ys);
}
""",
        True,
    ),
    (
        "3-auto",
        "第 3 条：`auto x = expr;` 的局部类型推断",
        None,
        """u64 twice(u64 n) { return n * 2; }

u64 main() {
    auto a = 5;
    auto b = twice(a);
    auto c = true;
    return b + a;
}
""",
        None,
        """u64 twice(u64 n) { return n * 2; }

u64 main() {
    i64 a = 5;
    u64 b = twice(a);
    bool c = true;
    return b + a;
}
""",
        True,
    ),
    (
        "4-brace",
        "第 4 条：`T{a, b}` / `vector<u64> v{1,2,3}`",
        None,
        """struct Point { u64 x; u64 y; };

u64 main() {
    vector<u64> v{1, 2, 3};
    Point p{3, 4};
    Point q = Point{5, 6};
    return v.size() + p.x + p.y + q.x + q.y;
}
""",
        """struct Point { x: u64, y: u64, };
""",
        """u64 main() {
    vector<u64> v = {1, 2, 3};
    Point p = Point { x: 3, y: 4 };
    Point q = Point { x: 5, y: 6 };
    return v.size() + p.x + p.y + q.x + q.y;
}
""",
        True,
    ),
    (
        "5-begin-end",
        "第 5 条：`f(xs.begin(), xs.end())` → `f(xs)`",
        None,
        """u64 add(u64 a, u64 b) { return a + b; }

u64 total(vector<u64> xs) {
    u64 s = 0;
    for (auto x : xs) { s = s + x; }
    return s;
}

u64 main() {
    vector<u64> xs = {1, 2, 3};
    return total(xs.begin(), xs.end()) + add(1, 2);
}
""",
        None,
        """u64 add(u64 a, u64 b) { return a + b; }

u64 total(vector<u64> xs) {
    u64 s = 0;
    for (auto x : xs) { s = s + x; }
    return s;
}

u64 main() {
    vector<u64> xs = {1, 2, 3};
    return total(xs) + add(1, 2);
}
""",
        True,
    ),
    (
        "6-std",
        "第 6 条：`std::` 前缀白名单",
        None,
        """std::size_t main() {
    std::vector<u64> xs = {3, 1, 2};
    std::sort(xs);
    std::printf("%llu\\n", xs[0]);
    std::size_t n = xs.size();
    return std::min(n, std::max(1, 2)) + std::abs(0 - 3);
}
""",
        None,
        """u64 main() {
    vector<u64> xs = {3, 1, 2};
    sort(xs);
    print("{:u}", xs[0]);
    u64 n = xs.size();
    return min(n, max(1, 2)) + abs(0 - 3);
}
""",
        True,
    ),
    (
        "7-void",
        "第 7 条：返回类型 `void` → `()`",
        """spec report(u64 n) -> void {
    effects { io };
    target { time: { max: O(1) } };
}
""",
        """void report(u64 n) {
    print(n);
}

u64 main() {
    report(7);
    return 0;
}
""",
        """spec report(u64 n) -> () {
    effects { io };
    target { time: { max: O(1) } };
}
""",
        """() report(u64 n) {
    print(n);
}

u64 main() {
    report(7);
    return 0;
}
""",
        True,
    ),
]


# ---------------------------------------------------------------- [1] 等价性

def test_equivalence():
    print("[1] 七条各自的等价性：κ 逐字节 / run 输出逐字节 / AST 同形")
    for key, title, cpp_tu, cpp_eln, ours_tu, ours_eln, ast_equal in CASES:
        root = tempfile.mkdtemp(prefix=f"elncompat-{key}-")
        try:
            pair = Pair(root)
            pair.write("cpp", cpp_tu, cpp_eln)
            pair.write("ours", ours_tu, ours_eln)

            # ① 两边都要**能过**（C++ 拼写靠 --compat，我们的拼写不带开关）
            rc_c, out_c, err_c = cli("check", os.path.join(pair.cpp, "mod"), "--compat")
            check(rc_c == 0, f"{key}：C++ 拼写 + --compat 能过 check",
                  (out_c + err_c).strip().split("\n")[0])
            rc_o, out_o, err_o = cli("check", os.path.join(pair.ours, "mod"))
            check(rc_o == 0, f"{key}：本语言拼写（不带 --compat）能过 check",
                  (out_o + err_o).strip().split("\n")[0])

            # ② κ：`cost --signature` 的**整份 JSON 逐字节相同**
            rc1, kcpp, e1 = cli("cost", os.path.join(pair.cpp, "mod"), "--compat", "--signature")
            rc2, kours, e2 = cli("cost", os.path.join(pair.ours, "mod"), "--signature")
            check(rc1 == 0 and rc2 == 0,
                  f"{key}：两边的 cost 都退出 0", f"{rc1}/{rc2} {e1[:80]}{e2[:80]}")
            check(kcpp == kours,
                  f"{key}：κ 逐字节相同（cost --signature 整份 JSON）",
                  _diff_head(kcpp, kours))

            # ③ run：stdout 逐字节相同
            rc3, rcpp, e3 = cli("run", os.path.join(pair.cpp, "mod"), "--compat")
            rc4, rours, e4 = cli("run", os.path.join(pair.ours, "mod"))
            check(rc3 == 0 and rc4 == 0,
                  f"{key}：两边的 run 都退出 0", f"{rc3}/{rc4} {e3[:80]}{e4[:80]}")
            check(rcpp == rours, f"{key}：run 输出逐字节相同", _diff_head(rcpp, rours))

            # ④ AST（第 2–7 条）：剥掉语法糖之后是**同一棵树**（位置除外）
            if ast_equal:
                _bodies = [("mod.eln", cpp_eln, ours_eln)]
                if cpp_tu is not None and ours_tu is not None:
                    _bodies.append(("mod.tu", cpp_tu, ours_tu))
                for _nm, _c, _o in _bodies:
                    sa = ast_norm(_c, True, _nm)
                    sb = ast_norm(_o, False, _nm)
                    # 第 4 条的 C++ 侧把 `struct` 也写在 `.eln` 里（第 1 条允许），
                    # 我们那侧按分工写在 `.tu`——比 AST 时把 struct **那一项**
                    # 去掉：比较的是"聚合初始化落到同一个节点"，不是"文件分工"。
                    _drop = AST_DROP.get(key, ())
                    if _drop:
                        sa = _drop_kinds(sa, set(_drop))
                    check(sa is not None and sa == sb,
                          f"{key}：{_nm} 的 AST 同形（去行列号后逐字节）",
                          _diff_head(_jsonl(sa), _jsonl(sb)))
        finally:
            shutil.rmtree(root, ignore_errors=True)


#: 比 AST 时**故意丢掉**的 item 种类（只有"文件分工不同"的那一条用得到）。
AST_DROP = {"4-brace": ("struct",)}


def _drop_kinds(node, kinds):
    """递归删掉 `kind in kinds` 的节点（只用于 AST 比较）。"""
    if isinstance(node, dict):
        if node.get("kind") in kinds:
            return None
        out = {}
        for k, v in node.items():
            if k == "children" and isinstance(v, list):
                out[k] = [x for x in (_drop_kinds(c, kinds) for c in v)
                          if x is not None]
            else:
                out[k] = _drop_kinds(v, kinds)
        return out
    if isinstance(node, list):
        return [_drop_kinds(x, kinds) for x in node]
    return node


def _jsonl(obj):
    """AST → 一行文本（`bytes` 字面量用 repr，只为排错展示）。"""
    return json.dumps(obj, ensure_ascii=False, default=repr)


def _diff_head(a, b, n=160):
    """第一处不同附近的一小段（排错用）。"""
    if a == b:
        return ""
    m = min(len(a), len(b))
    i = next((j for j in range(m) if a[j] != b[j]), m)
    lo = max(0, i - 40)
    return f"@{i}: {a[lo:i + n]!r}  vs  {b[lo:i + n]!r}"


# ---------------------------------------------------------------- [2] 默认不变

def test_default_unchanged():
    print("[2] 不带 `--compat` 时：第 1–5 条照旧报错（默认路径一个字节不动）")
    for key, want_code, cpp_eln in (
        ("1-struct", "E-Ai-HasDecl",
         "struct Point { u64 x; };\nu64 main() { return 0; }\n"),
        ("2-using", "E-Keyword-Unsupported",
         "using U64Vec = vector<u64>;\nu64 main() { return 0; }\n"),
        ("3-auto", "E-Keyword-Unsupported",
         "u64 main() { auto a = 5; return a; }\n"),
        ("4-brace", "E-Parse-Expected",
         "u64 main() { vector<u64> v{1, 2, 3}; return v.size(); }\n"),
        ("5-begin-end", "E-Call-UnknownMethod",
         "u64 total(vector<u64> xs) { return xs.size(); }\n"
         "u64 main() { vector<u64> xs = {1}; return total(xs.begin(), xs.end()); }\n"),
        # 第 12 条：**不带 `--compat` 时三段式迭代器 for 仍报 E-For-NoIterator**
        # （钉默认路径：降级只在开关打开时发生）。
        ("12-for-iter", "E-For-NoIterator",
         "u64 main() {\n    vector<u64> xs = {1, 2, 3};\n    u64 s = 0;\n"
         "    for (auto it = xs.begin(); it != xs.end(); ++it) { s = s + *it; }\n"
         "    return s;\n}\n"),
    ):
        root = tempfile.mkdtemp(prefix=f"elncompat-default-{key}-")
        try:
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(cpp_eln)
            rc, out, err = cli("check", p)
            blob = out + err
            check(rc != 0, f"{key}：不带 --compat 时拒绝（退出码非 0）", f"rc={rc}")
            check(want_code in blob,
                  f"{key}：报的是 {want_code}（不是别的错）",
                  blob.strip().split("\n")[0])
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [3] 第 6 条

def test_std_whitelist():
    print("[3] 第 6 条：`std::` 白名单内改名 / 白名单外 E-Compat-UnknownStd")

    # 白名单外：报错，且**列出已支持的名字**（用户要求：诊断要能教改法）
    root = tempfile.mkdtemp(prefix="elncompat-std-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 main() {\n    std::cout(1);\n    return 0;\n}\n")
        rc, out, err = cli("check", p, "--compat")
        blob = out + err
        check(rc != 0, "白名单外的 `std::cout` 被拒绝", f"rc={rc}")
        check("E-Compat-UnknownStd" in blob,
              "报的是 E-Compat-UnknownStd", blob.strip().split("\n")[0])
        for name in ("std::vector", "std::sort", "std::printf", "std::size_t",
                     "std::min", "std::max", "std::abs",
                     # 第 3 轮加进白名单的 `<cctype>` 名字（清单要同步）
                     "std::isdigit", "std::isspace", "std::isalpha",
                     "std::isalnum", "std::isupper", "std::islower",
                     "std::toupper", "std::tolower"):
            check(name in blob, f"消息里列出了 {name}", blob[-200:])
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # **如实钉住旧行为**：不带 `--compat` 时 `std::sort(xs)` 不报错——
    # 它被解析成一个**点号路径调用** `std.sort`，check 跳过带 `.` 的被调名，
    # run 里那个调用**什么也不做**（sort 没发生）。所以旧行为不是"报错"，
    # 而是"静默不生效"；`--compat` 把它改成真的 `sort(xs)`。
    # 这条用例的存在，就是为了不让"默认不变"被写成一句谎话。
    root = tempfile.mkdtemp(prefix="elncompat-std-old-")
    try:
        cpp = os.path.join(root, "cpp")
        ours = os.path.join(root, "ours")
        src = ("u64 main() {\n    vector<u64> xs = {3, 1, 2};\n"
               "    std::sort(xs);\n    return xs[0];\n}\n")
        plain = ("u64 main() {\n    vector<u64> xs = {3, 1, 2};\n"
                 "    sort(xs);\n    return xs[0];\n}\n")
        for d, text in ((cpp, src), (ours, plain)):
            os.makedirs(d, exist_ok=True)
            io.open(os.path.join(d, "mod.eln"), "w", encoding="utf-8",
                    newline="\n").write(text)
        _rc, old_out, _e = cli("run", os.path.join(cpp, "mod.eln"))
        _rc, ours_out, _e = cli("run", os.path.join(ours, "mod.eln"))
        check(old_out != ours_out,
              "旧行为（不带 --compat）与 `sort(xs)` **不同**——"
              "`std::sort` 在旧路径里静默没生效",
              f"old={old_out!r} ours={ours_out!r}")
        _rc, new_out, _e = cli("run", os.path.join(cpp, "mod.eln"), "--compat")
        check(new_out == ours_out,
              "带 --compat 后与 `sort(xs)` 逐字节相同", f"{new_out!r} vs {ours_out!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [4] 第 3 条

def test_auto_no_guess():
    print("[4] 第 3 条：`auto` 推断不出就报错（不猜）")

    for label, src, want in (
        ("`auto x;`（没有初值）",
         "u64 main() {\n    auto x;\n    return 0;\n}\n",
         "E-Compat-AutoNoType"),
        ("`auto x = {1, 2};`（大括号没有类型）",
         "u64 main() {\n    auto x = {1, 2};\n    return 0;\n}\n",
         "E-Compat-AutoNoType"),
        ("`auto x = nullptr;`（空指针没有可写的类型名）",
         "u64 main() {\n    auto x = nullptr;\n    return 0;\n}\n",
         "E-Compat-AutoNoType"),
    ):
        root = tempfile.mkdtemp(prefix="elncompat-auto-")
        try:
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(src)
            rc, out, err = cli("check", p, "--compat")
            blob = out + err
            check(rc != 0, f"{label} → 失败", f"rc={rc}")
            check(want in blob, f"{label} → {want}", blob.strip().split("\n")[0])
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [5] 第 5 条

def test_iter_not_paired():
    print("[5] 第 5 条：`begin` / `end` **不配平**时仍报「本语言没有迭代器」")

    for label, expr in (
        ("只有 `begin`", "f(xs.begin())"),
        ("只有 `end`", "f(xs.end())"),
        ("两个不同容器", "f(xs.begin(), ys.end())"),
        ("顺序反过来", "f(xs.end(), xs.begin())"),
        ("不在实参里（`auto it = xs.begin();`）", "__DECL__"),
    ):
        root = tempfile.mkdtemp(prefix="elncompat-iter-")
        try:
            p = os.path.join(root, "mod.eln")
            body = ("    auto it = xs.begin();\n" if expr == "__DECL__"
                    else f"    {expr};\n")
            src = ("u64 f(vector<u64> xs) { return xs.size(); }\n"
                   "u64 main() {\n    vector<u64> xs = {1};\n"
                   "    vector<u64> ys = {2};\n" + body + "    return 0;\n}\n")
            io.open(p, "w", encoding="utf-8", newline="\n").write(src)
            rc, out, err = cli("check", p, "--compat")
            blob = out + err
            check(rc != 0, f"{label} → 拒绝", f"rc={rc}")
            check("E-Call-UnknownMethod" in blob and "没有迭代器" in blob,
                  f"{label} → E-Call-UnknownMethod「没有迭代器」",
                  blob.strip().split("\n")[0])
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [6] 第 1 条

def test_struct_needs_strict():
    print("[6] 第 1 条：`--compat` 接受 `.eln` 里的 struct，但 `--strict` 仍拒绝")
    root = tempfile.mkdtemp(prefix="elncompat-struct-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "struct Point { u64 x; };\n\n"
            "u64 get(Point p) { return p.x; }\n\n"
            "u64 main() { Point p = Point { x: 3 }; return get(p); }\n")
        rc, out, err = cli("check", p, "--compat")
        check(rc == 0, "`--compat` 下通过", (out + err).strip().split("\n")[0])
        check("E-Ai-HasDecl" not in (out + err),
              "不再报 E-Ai-HasDecl")
        rc2, out2, err2 = cli("check", p, "--compat", "--strict")
        check(rc2 != 0 and "E-Ai-HasDecl" in (out2 + err2),
              "`--compat --strict` 仍按 F-4 拒绝",
              (out2 + err2).strip().split("\n")[0])
        rc3, out3, err3 = cli("check", p)
        check(rc3 != 0 and "E-Ai-HasDecl" in (out3 + err3),
              "不带 `--compat` 时照旧拒绝（默认不变）",
              (out3 + err3).strip().split("\n")[0])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [7] 别名

def test_alias_unknown_is_warning():
    print("[7] 第 2 条：认不出的别名只 warning，不 error")
    root = tempfile.mkdtemp(prefix="elncompat-alias-")
    try:
        p = os.path.join(root, "mod.eln")
        # `Point` 声明在 .tu（这里没有 .tu）——parser 看不见，属"认不出"
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "using P = Point;\n\nu64 main() { return 0; }\n")
        rc, out, err = cli("check", p, "--compat")
        blob = out + err
        check("W-Compat-Alias" in blob, "报 W-Compat-Alias（warning）",
              blob.strip().split("\n")[0])
        # 只是警告：错误数 0（`check` 的退出码只数 error）
        check(rc == 0, "只有 warning，`check` 仍退出 0", f"rc={rc}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_alias_in_body():
    """第 2 条在**函数体里**（C++ 合法、只在块内生效）也必须只加拼写。

    `using` / `typedef` 不产出节点，所以解析器返回一个**空块**占位；
    这条用例钉住那个空块**不产生任何成本**（κ 与手写类型逐字节相同）。
    """
    print("[8] 第 2 条（函数体内）：`using` / `typedef` 不产生成本")
    root = tempfile.mkdtemp(prefix="elncompat-alias-body-")
    try:
        cpp_dir = os.path.join(root, "cpp")
        ours_dir = os.path.join(root, "ours")
        for d, text in (
            (cpp_dir,
             "u64 main() {\n    using N = u64;\n    typedef u64 M;\n"
             "    N a = 3;\n    M b = 4;\n    return a + b;\n}\n"),
            (ours_dir,
             "u64 main() {\n    u64 a = 3;\n    u64 b = 4;\n    return a + b;\n}\n"),
        ):
            os.makedirs(d, exist_ok=True)
            io.open(os.path.join(d, "mod.eln"), "w", encoding="utf-8",
                    newline="\n").write(text)
        rc1, k1, e1 = cli("cost", os.path.join(cpp_dir, "mod"), "--compat",
                          "--signature")
        rc2, k2, e2 = cli("cost", os.path.join(ours_dir, "mod"), "--signature")
        check(rc1 == 0 and rc2 == 0, "两边的 cost 都退出 0",
              f"{rc1}/{rc2} {e1[:80]}{e2[:80]}")
        check(k1 == k2, "κ 逐字节相同（体内的别名不产生成本）",
              _diff_head(k1, k2))
        rc3, r1, _e = cli("run", os.path.join(cpp_dir, "mod"), "--compat")
        rc4, r2, _e = cli("run", os.path.join(ours_dir, "mod"))
        check(rc3 == 0 and rc4 == 0 and r1 == r2, "run 输出逐字节相同",
              f"{r1!r} vs {r2!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [8] 第 7 条
def test_void_ast():
    print("[9] 第 7 条：`void` 在 `--compat` 下归一到 `()`（同一棵 AST）")
    src_void = "void f(u64 n) { print(n); }\nu64 main() { f(1); return 0; }\n"
    src_paren = "() f(u64 n) { print(n); }\nu64 main() { f(1); return 0; }\n"
    old = ast_norm(src_void, False)
    new = ast_norm(src_void, True)
    canon = ast_norm(src_paren, False)
    # 不带 --compat：`void` 还是今天的 `type-name void`（默认路径不动）
    check(_has_void_type(old),
          "不带 --compat：返回类型仍是 `type-name void`（今天的 AST）")
    # 带 --compat：与 `()` 的 AST 同形（去行列号后逐字节）
    check(new == canon,
          "带 --compat：`void` 与 `()` 的 AST 同形",
          _diff_head(_jsonl(new), _jsonl(canon)))


def _has_void_type(ast):
    """AST 里有没有 `type-name void`（第 7 条的旧形状）。"""
    if isinstance(ast, dict):
        if ast.get("kind") == "type-name" and ast.get("name") == "void":
            return True
        return any(_has_void_type(v) for v in ast.values())
    if isinstance(ast, list):
        return any(_has_void_type(x) for x in ast)
    return False


# ---------------------------------------------------------------- [10] 第 8 条

def test_static_fn_prefix():
    """第 8 条：**函数声明开头**的 `static` 是纯拼写（内部链接对我们没有语义）。

    C++ 里 `static` 在函数声明前表示「文件内可见」；本语言一个文件就是
    一个模块，函数本来就是文件内的。所以这里只跳过 token：**不产出节点**，
    `static void f(){}` 与 `void f(){}` 必须 κ / AST / run 全部逐字节相同。

    **不放开**静态局部变量（`static u64 counter = 0;`）——那是另一件事。
    """
    print("[10] 第 8 条：函数声明开头的 `static` 被跳过（不产出节点、不影响 κ/AST）")
    root = tempfile.mkdtemp(prefix="elncompat-static-")
    try:
        cpp = os.path.join(root, "cpp")
        plain = os.path.join(root, "plain")
        cpp_src = (
            "static void f(u64 n) { print(n); }\n"
            "static u64 g(u64 a, u64 b) { return a + b; }\n"
            "u64 main() { f(3); return g(4, 5); }\n")
        plain_src = (
            "void f(u64 n) { print(n); }\n"
            "u64 g(u64 a, u64 b) { return a + b; }\n"
            "u64 main() { f(3); return g(4, 5); }\n")
        for d, text in ((cpp, cpp_src), (plain, plain_src)):
            os.makedirs(d, exist_ok=True)
            io.open(os.path.join(d, "mod.eln"), "w", encoding="utf-8",
                    newline="\n").write(text)

        # ① --compat 下能过；不带 --compat 仍拒绝（默认路径逐字节不变）
        rc, out, err = cli("check", os.path.join(cpp, "mod"), "--compat")
        check(rc == 0, "`static void f(…)` / `static u64 g(…)` 在 --compat 下能过 check",
              (out + err).strip().split("\n")[0])
        check("E-Keyword-Unsupported" not in (out + err),
              "--compat 下不再报 E-Keyword-Unsupported")
        rc0, out0, err0 = cli("check", os.path.join(cpp, "mod"))
        blob0 = out0 + err0
        check(rc0 != 0 and "E-Keyword-Unsupported" in blob0,
              "不带 --compat 仍报 E-Keyword-Unsupported",
              blob0.strip().split("\n")[0])

        # ② κ：`cost --signature` 整份 JSON 逐字节相同
        rc1, k1, e1 = cli("cost", os.path.join(cpp, "mod"), "--compat", "--signature")
        rc2, k2, e2 = cli("cost", os.path.join(plain, "mod"), "--compat", "--signature")
        check(rc1 == 0 and rc2 == 0, "两边的 cost 都退出 0",
              f"{rc1}/{rc2} {e1[:80]}{e2[:80]}")
        check(k1 == k2, "`static void f(){}` 与 `void f(){}`：cost --signature 逐字节相同",
              _diff_head(k1, k2))

        # ③ run：stdout 逐字节相同
        rc3, r1, _e = cli("run", os.path.join(cpp, "mod"), "--compat")
        rc4, r2, _e = cli("run", os.path.join(plain, "mod"), "--compat")
        check(rc3 == 0 and rc4 == 0, "两边的 run 都退出 0", f"{rc3}/{rc4}")
        check(r1 == r2, "run 输出逐字节相同", f"{r1!r} vs {r2!r}")

        # ④ AST（去行列号）：`static` **不产出节点**
        a = ast_norm(cpp_src, True)
        b = ast_norm(plain_src, True)
        check(a == b, "AST 同形（static 不产出节点）",
              _diff_head(_jsonl(a), _jsonl(b)))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_static_local_still_rejected():
    """`static` **局部变量**仍然拒绝（第 8 条只放函数声明前缀）。"""
    print("[11] `static` 局部变量在 --compat 下仍然拒绝（不放开的边界）")
    root = tempfile.mkdtemp(prefix="elncompat-static-local-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 f() {\n"
            "    static u64 counter = 0;\n"
            "    counter = counter + 1;\n"
            "    return counter;\n"
            "}\n"
            "u64 main() { return f(); }\n")
        rc, out, err = cli("check", p, "--compat")
        blob = out + err
        check(rc != 0, "`static u64 counter = 0;` → 拒绝", f"rc={rc}")
        check("E-Keyword-Unsupported" in blob and "static" in blob,
              "报的仍是 E-Keyword-Unsupported（文案照旧）",
              blob.strip().split("\n")[0])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_postfix_incdec_in_expr_still_rejected():
    """`a[i++]`（表达式位置带值）**不在本轮范围**，仍必须报错。"""
    print("[12] `a[i++]` 在 --compat 下仍然报错（不在本轮范围）")
    root = tempfile.mkdtemp(prefix="elncompat-incdec-expr-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 main() {\n"
            "    vector<u64> xs = {1, 2, 3};\n"
            "    u64 i = 0;\n"
            "    u64 a = xs[i++];\n"
            "    return a;\n"
            "}\n")
        rc, out, err = cli("check", p, "--compat")
        blob = out + err
        check(rc != 0, "`a[i++]` → 拒绝", f"rc={rc}")
        check("E-Unary-Unsupported" in blob,
              "报的是 E-Unary-Unsupported（只能作独立语句 / for 第三段）",
              blob.strip().split("\n")[0])
        check("i = i + 1" in blob or "拆成两句" in blob,
              "消息里给出改法（拆成两句）", blob[-200:])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [13] for 第三段

def test_for_step_incdec_equivalence():
    """`for` 第三段的 `++i` / `i++` / `--j` / `j--` 与 `i = i ± 1` 逐字节相同。

    **如实说明**：这是 **2026-11-10 起既有的语法**（commit `7e35a82` 的
    三段式 `for` 支持），不是本轮 `--compat` 新增；这里只补成对用例，
    把"是拼写、不是新语义"钉住。
    """
    print("[13] `for` 第三段：`++i` / `i++` / `--j` / `j--` 与 `i = i ± 1` 逐字节相同")
    root = tempfile.mkdtemp(prefix="elncompat-for-step-")
    try:
        inc = ("u64 main() {\n"
               "    u64 s = 0;\n"
               "    for (u64 i = 0; i < 4; __STEP__) { s = s + i; }\n"
               "    return s;\n"
               "}\n")
        dec = ("u64 main() {\n"
               "    u64 s = 0;\n"
               "    for (i64 j = 3; j >= 0; __STEP__) { s = s + 1; }\n"
               "    return s;\n"
               "}\n")
        cases = {
            "inc-prefix": inc.replace("__STEP__", "++i"),
            "inc-postfix": inc.replace("__STEP__", "i++"),
            "inc-assign": inc.replace("__STEP__", "i = i + 1"),
            "dec-prefix": dec.replace("__STEP__", "--j"),
            "dec-postfix": dec.replace("__STEP__", "j--"),
            "dec-assign": dec.replace("__STEP__", "j = j - 1"),
        }
        paths, kappas, runs = {}, {}, {}
        for name, src in cases.items():
            d = os.path.join(root, name)
            os.makedirs(d, exist_ok=True)
            paths[name] = os.path.join(d, "mod")
            io.open(os.path.join(d, "mod.eln"), "w", encoding="utf-8",
                    newline="\n").write(src)

            rc, out, err = cli("check", paths[name], "--compat")
            check(rc == 0, f"{name}：--compat 下能过 check",
                  (out + err).strip().split("\n")[0])
            rc1, k, _e = cli("cost", paths[name], "--compat", "--signature")
            check(rc1 == 0, f"{name}：cost 退出 0", f"rc={rc1}")
            kappas[name] = k
            rc2, r, _e = cli("run", paths[name], "--compat")
            check(rc2 == 0, f"{name}：run 退出 0", f"rc={rc2}")
            runs[name] = r

        for family in ("inc", "dec"):
            names = [f"{family}-prefix", f"{family}-postfix", f"{family}-assign"]
            check(kappas[names[0]] == kappas[names[1]] == kappas[names[2]],
                  f"{family}：三种写法的 κ 逐字节相同",
                  _diff_head(kappas[names[0]], kappas[names[2]]))
            check(runs[names[0]] == runs[names[1]] == runs[names[2]],
                  f"{family}：三种写法的 run 输出逐字节相同",
                  f"{runs[names[0]]!r} / {runs[names[1]]!r} / {runs[names[2]]!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [14] 第 9 条

def _equiv_pair(label, cpp_src, ours_src, want_run=None):
    """第 9/10 条通用成对断言：check / κ / run / AST 四样都相同。

    与 `test_equivalence` 的 [1] 同一套口径，只是这两条的用例是**局部片段**
    （多声明符 / 直构），单独写一个成对助手比往 `CASES` 里塞更清楚。

    `want_run` 非 None 时还断言 C++ 侧的 stdout **就是那个值**——"两边一样"
    只能证明拼写没分叉，证明不了"值算对了"（两边可以一样地错）。
    """
    root = tempfile.mkdtemp(prefix="elncompat-pair-")
    try:
        cpp = os.path.join(root, "cpp")
        ours = os.path.join(root, "ours")
        for d, text in ((cpp, cpp_src), (ours, ours_src)):
            os.makedirs(d, exist_ok=True)
            io.open(os.path.join(d, "mod.eln"), "w", encoding="utf-8",
                    newline="\n").write(text)
        rc1, o1, e1 = cli("check", os.path.join(cpp, "mod"), "--compat")
        rc2, o2, e2 = cli("check", os.path.join(ours, "mod"))
        check(rc1 == 0 and rc2 == 0, f"{label}：两边的 check 都退出 0",
              f"{rc1}/{rc2} {(o1 + e1 + o2 + e2).strip().splitlines()[0]}")
        rc3, k1, _ = cli("cost", os.path.join(cpp, "mod"), "--compat", "--signature")
        rc4, k2, _ = cli("cost", os.path.join(ours, "mod"), "--signature")
        check(rc3 == 0 and rc4 == 0 and k1 == k2,
              f"{label}：κ 逐字节相同（cost --signature 整份 JSON）",
              _diff_head(k1, k2))
        rc5, r1, _ = cli("run", os.path.join(cpp, "mod"), "--compat")
        rc6, r2, _ = cli("run", os.path.join(ours, "mod"))
        check(rc5 == 0 and rc6 == 0 and r1 == r2,
              f"{label}：run 输出逐字节相同", f"{r1!r} vs {r2!r}")
        if want_run is not None:
            # `run` 结尾会把 `main` 的返回值也打出来（这里是 0）——预期值按
            # **归一化行尾**比，跨平台不比 CRLF 的差异。
            check(r1.replace("\r\n", "\n") == want_run,
                  f"{label}：run 输出就是 {want_run!r}（值算对了，不只是两边一样）",
                  f"{r1!r}")
        a = ast_norm(cpp_src, True)
        b = ast_norm(ours_src, False)
        check(a == b, f"{label}：AST 同形（去行列号后逐字节）",
              _diff_head(_jsonl(a), _jsonl(b)))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_multi_declarator_equiv():
    """第 9 条：`u64 i = 0, j = 1;` 与手写两行是**同一个程序**。

    落点是"解析层展开成多个声明节点"——所以 AST 里必须是**两个兄弟
    `var-decl`**，不是一个新的"多声明符"节点（下游一行都不用改）。
    """
    print("[14] 第 9 条：多声明符 `T a = x, b = y;` = 手写多条声明")

    _equiv_pair(
        "`u64 i = 0, j = 1;`",
        "u64 main() {\n    u64 i = 0, j = 1;\n    return i + j;\n}\n",
        "u64 main() {\n    u64 i = 0;\n    u64 j = 1;\n    return i + j;\n}\n")

    _equiv_pair(
        "无初始化的尾声明 `u64 a = 1, b;`",
        "u64 main() {\n    u64 a = 1, b;\n    b = 2;\n    return a + b;\n}\n",
        "u64 main() {\n    u64 a = 1;\n    u64 b;\n    b = 2;\n    return a + b;\n}\n")

    _equiv_pair(
        "`const u64 a = 1, b = 2;`（两条都 const）",
        "u64 main() {\n    const u64 a = 1, b = 2;\n    return a + b;\n}\n",
        "u64 main() {\n    const u64 a = 1;\n    const u64 b = 2;\n"
        "    return a + b;\n}\n")

    # "两个都 const"不能只靠 AST 形状：**逐个别赋值**，两个都必须被拒绝。
    for n in ("a", "b"):
        root = tempfile.mkdtemp(prefix="elncompat-const-multi-")
        try:
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(
                "u64 main() {\n    const u64 a = 1, b = 2;\n"
                f"    {n} = 3;\n    return a + b;\n}}\n")
            rc, out, err = cli("check", p, "--compat")
            blob = out + err
            check(rc != 0 and "E-Assign-Immutable" in blob,
                  f"多声明符里的 `{n}` 是 const（赋值报 E-Assign-Immutable）",
                  blob.strip().split("\n")[0])
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [15] 第 10 条

def test_direct_init_equiv():
    """第 10 条：`T x(args…)` 是**拼写**——必须落到已有的等价写法。

    · 单实参 → 拷贝初始化（`u64 x(5)` → `u64 x = 5`）；
    · `vector<T>(n, v)` → `vector<T>::filled(v, n)`（interp 里同一语义）；
    · `array<T, N>(v)` 本来就有等价表达式 → 原样保留同一个 `call`。

    四种形状都要求 κ / run / AST 三样逐字节相同。
    """
    print("[15] 第 10 条：直接构造 `T x(args…)` 落到已有等价写法")

    _equiv_pair(
        "`u64 x(5)` 与 `u64 x = 5`",
        "u64 main() {\n    u64 x(5);\n    return x;\n}\n",
        "u64 main() {\n    u64 x = 5;\n    return x;\n}\n")

    _equiv_pair(
        "`std::string s(\"abc\")` 与文本初始化",
        "u64 main() {\n    std::string s(\"abc\");\n    print(s);\n"
        "    return s.size();\n}\n",
        "u64 main() {\n    vector<u8> s = \"abc\";\n    print(s);\n"
        "    return s.size();\n}\n")

    _equiv_pair(
        "`vector<u64> v(3, 7)` 与 `vector<u64>::filled(7, 3)`",
        "u64 main() {\n    vector<u64> v(3, 7);\n"
        "    return v.size() + v[0];\n}\n",
        "u64 main() {\n    vector<u64> v = vector<u64>::filled(7, 3);\n"
        "    return v.size() + v[0];\n}\n")

    _equiv_pair(
        "`Mat m(n, std::vector<u64>(n, 0))`（嵌套直构）",
        "using Mat = std::vector<std::vector<u64>>;\n"
        "u64 main() {\n    u64 n = 2;\n"
        "    Mat m(n, std::vector<u64>(n, 0));\n"
        "    return m.size() + m[1][1];\n}\n",
        "u64 main() {\n    u64 n = 2;\n"
        "    vector<vector<u64>> m = "
        "vector<vector<u64>>::filled(vector<u64>::filled(0, n), n);\n"
        "    return m.size() + m[1][1];\n}\n")


def test_ctor_unsupported():
    """没有等价物的直构 → `E-Compat-CtorUnsupported` + 改法（**不静默通过**）。

    用户定的边界：`T(args)` 这个形状如果在本语言里本来就没有等价物（需要
    构造函数），就报一条明确诊断，**不许悄悄放过、更不许编一个语义**。
    """
    print("[16] 第 10 条：没有等价物的直构 → E-Compat-CtorUnsupported")
    for label, src, want in (
        ("结构体构造 `Ring r(3);`",
         "struct Ring { u64 cap; };\nu64 main() {\n    Ring r(3);\n"
         "    return r.cap;\n}\n",
         "先声明再赋值"),
        ("`vector<u64> v(3);`（n 个默认值，没有默认值表达式）",
         "u64 main() {\n    vector<u64> v(3);\n    return v.size();\n}\n",
         "filled(v, n)"),
    ):
        root = tempfile.mkdtemp(prefix="elncompat-ctor-")
        try:
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(src)
            rc, out, err = cli("check", p, "--compat")
            blob = out + err
            check(rc != 0, f"{label} → 拒绝（不静默通过）", f"rc={rc}")
            check("E-Compat-CtorUnsupported" in blob,
                  f"{label} → 报 E-Compat-CtorUnsupported",
                  blob.strip().split("\n")[0])
            check(want in blob, f"{label} → 消息里给出改法「{want}」", blob[-320:])
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [17] 默认不变

def test_multi_and_ctor_default_unchanged():
    """第 9 / 10 条在**不带** `--compat` 时：照旧报 `E-Parse-Expected`。

    这两条是纯新增的兼容拼写；关掉开关时不能"顺手也认了"——默认路径的
    诊断与以前逐字节相同。
    """
    print("[17] 第 9/10 条：不带 --compat 时仍照旧报错（默认路径不变）")
    for label, src, want in (
        ("多声明符 `u64 i = 0, j = 1;`",
         "u64 main() {\n    u64 i = 0, j = 1;\n    return i + j;\n}\n",
         "E-Parse-Expected"),
        ("直接构造 `u64 x(5);`",
         "u64 main() {\n    u64 x(5);\n    return x;\n}\n",
         "E-Parse-Expected"),
        # **如实钉住**：不带开关时 `vector<u64> v(3, 7);` 今天被解析成
        # 「表达式 + 调用 `v`」，报的是 `E-Call-Undeclared`——不是
        # `E-Parse-Expected`。不编一个"报错"来凑数。
        ("容器直构 `vector<u64> v(3, 7);`",
         "u64 main() {\n    vector<u64> v(3, 7);\n    return v.size();\n}\n",
         "E-Call-Undeclared"),
    ):
        root = tempfile.mkdtemp(prefix="elncompat-default-r2-")
        try:
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(src)
            rc, out, err = cli("check", p)
            blob = out + err
            check(rc != 0, f"{label}：不带 --compat 时拒绝", f"rc={rc}")
            check(want in blob and "E-Compat-CtorUnsupported" not in blob,
                  f"{label}：报的仍是 {want}（默认路径不变）",
                  blob.strip().split("\n")[0])
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [18] 字符分类（第 11 轮）

#: 真值表：`(名字, 真值输入, 假值输入)`。输入覆盖用户点名的
#: `'0'` / `'9'` / `'a'` / `'Z'` / `' '` / `'\t'` / `128` / `255`。
_CHAR_TABLE = [
    ("isdigit", [0x30, 0x39], [0x61, 0x5A, 0x20, 0x09, 128, 255]),
    ("isspace", [0x20, 0x09, 0x0A, 0x0D], [0x30, 0x61, 128, 255]),
    ("isalpha", [0x61, 0x5A], [0x30, 0x20, 0x09, 128, 255]),
    ("isalnum", [0x30, 0x61, 0x5A], [0x20, 0x09, 128, 255]),
    ("isupper", [0x41, 0x5A], [0x61, 0x30, 0x20, 128, 255]),
    ("islower", [0x61, 0x7A], [0x41, 0x30, 0x20, 128, 255]),
]

#: `toupper` / `tolower` 的**原样返回**表：`(名字, [(输入, 期望输出)])`。
_CHAR_CASE_MAP = [
    ("toupper", [(0x61, 0x41), (0x7A, 0x5A), (0x30, 0x30), (0x20, 0x20),
                 (0x09, 0x09), (128, 128), (255, 255)]),
    ("tolower", [(0x41, 0x61), (0x5A, 0x7A), (0x30, 0x30), (0x20, 0x20),
                 (0x09, 0x09), (128, 128), (255, 255)]),
]


def _run_module(src, *args, compat=True):
    """在一个临时目录里写 `mod.eln` 并 `run`，返回 `(rc, stdout, stderr)`。"""
    root = tempfile.mkdtemp(prefix="elncompat-chr-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(src)
        a = ["--compat"] if compat else []
        rc, out, err = cli("run", p, *(list(a) + list(args)))
        return rc, out, err
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_char_class_truth_table():
    """八个字符分类内建：**在语言里真的跑**，ASCII 真值表逐项对。

    覆盖用户点名的 `'0'` / `'9'` / `'a'` / `'Z'` / `' '` / `'\\t'` / `128` /
    `255`，外加**负数按无符号处理**这条文档承诺（C 是 UB，我们定义）。
    """
    print("[18] 字符分类：八个内建的 ASCII 真值表（in-language run）")
    src = """u64 main() {
    vector<u64> cs = {48, 57, 97, 90, 32, 9, 128, 255};
    for (const auto& c : cs) {
        print("{} {} {} {} {} {} {} {}\\n",
              isdigit(c), isspace(c), isalpha(c), isalnum(c),
              isupper(c), islower(c), toupper(c), tolower(c));
    }
    print("{} {}\\n", toupper(0 - 1), tolower(0 - 1));
    return 0;
}
"""
    rc, out, err = _run_module(src)
    check(rc == 0, "八个内建在 --compat 下都能跑（run 退出 0）",
          (out + err).strip()[-300:])
    bytes_ = [48, 57, 97, 90, 32, 9, 128, 255]

    def _t(v):
        return "True" if v else "False"

    want_lines = []
    for b in bytes_:
        want_lines.append(" ".join([
            _t(b == 0x30 or b == 0x39),
            _t(b in (0x20, 0x09, 0x0A, 0x0B, 0x0C, 0x0D)),
            _t(b in (0x61, 0x5A)),
            _t(b in (0x30, 0x39, 0x61, 0x5A)),
            _t(b in (0x41, 0x5A)),
            _t(b in (0x61, 0x7A)),
            str(b - 0x20 if 0x61 <= b <= 0x7A else b),
            str(b + 0x20 if 0x41 <= b <= 0x5A else b),
        ]))
    # `-1` 按 u64 补码：toupper/tolower 原样返回补码值。
    want_lines.append("18446744073709551615 18446744073709551615")
    got = [ln for ln in out.split("\n") if ln.strip()]
    # `run` 最后还会打印 `main` 的返回值（0）——那不是程序输出，去掉再比。
    if got and got[-1] == "0":
        got = got[:-1]
    check(got == want_lines, "真值表逐行相同（含 ≥128 与 -1）",
          f"got={got[:3]}… want={want_lines[:3]}…")

    # **名字/成本/分配/失败语义的登记**：四个 is* 返回 bool，toupper/tolower 返回 u64。
    import stdlib as _std
    for name, _trues, _falses in _CHAR_TABLE:
        check(_std.ret_kind(name) == "bool",
              f"{name}：ret=bool（本语言 if 只收 bool）",
              _std.ret_kind(name))
        check(_std.cost_kind(name) == "1" and _std.alloc_kind(name) == "0"
              and not _std.effects_of(name),
              f"{name}：cost=1 / alloc=0 / effects {{}}（纯函数）",
              f"{_std.cost_kind(name)}/{_std.alloc_kind(name)}/{_std.effects_of(name)}")
        check(_std.failure_mode(name) == "never", f"{name}：失败语义 never",
              _std.failure_mode(name))
    for name, _pairs in _CHAR_CASE_MAP:
        check(_std.ret_kind(name) == "u64", f"{name}：ret=u64（字符值）",
              _std.ret_kind(name))
        check(_std.cost_kind(name) == "1" and _std.alloc_kind(name) == "0"
              and not _std.effects_of(name),
              f"{name}：cost=1 / alloc=0 / effects {{}}（纯函数）", "")
        check(_std.failure_mode(name) == "never", f"{name}：失败语义 never", "")

    # **逐项直调**（不经解释器）：真值表/原样返回的每一条都过
    bad = []
    for name, trues, falses in _CHAR_TABLE:
        for b in trues:
            if _std.call(name, [b]) is not True:
                bad.append(f"{name}({b}) != True")
        for b in falses:
            if _std.call(name, [b]) is not False:
                bad.append(f"{name}({b}) != False")
    for name, pairs in _CHAR_CASE_MAP:
        for b, want in pairs:
            if _std.call(name, [b]) != want:
                bad.append(f"{name}({b}) != {want}")
    check(not bad, "八个内建的每个值都对（直调 `stdlib.call`）", "; ".join(bad[:4]))

    # **负数/超范围按 u64 无符号处理**（文档承诺；C 是 UB）
    check(_std.call("isdigit", [-1]) is False,
          "isdigit(-1) = False（按 u64 补码 2^64-1 ≥ 128）")
    check(_std.call("toupper", [-1]) == 0xFFFFFFFFFFFFFFFF,
          "toupper(-1) 原样返回 2^64-1（按无符号处理）",
          repr(_std.call("toupper", [-1])))
    check(_std.call("toupper", [0x1F600]) == 0x1F600,
          "toupper(UTF-8 之外的码点值) 原样返回（≥128 不动）")


def test_char_builtin_kappa():
    """八个内建的 κ 与 `cost="1"` 登记一致：同形同 κ，且不随字符值变。

    解释器的成本域给内建记的是**标准派发**（`1（调用）+ 1（返回）+ 实参`），
    与 `abs` / `popcount` 同一条路；这里钉住"八个名字之间没有谁偷偷更贵"。
    """
    print("[18b] 字符分类：κ 一致（各记 `cost=1`，与其它 O(1) 内建同一条派发路径）")

    def kappa(body: str) -> str:
        root = tempfile.mkdtemp(prefix="elncompat-chr-k-")
        try:
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(
                f"u64 f(u8 c) {{\n    {body}\n}}\n")
            rc, out, err = cli("cost", p, "--signature")
            check(rc == 0, f"`{body}`：cost 退出 0", (out + err)[-160:])
            return out
        finally:
            shutil.rmtree(root, ignore_errors=True)

    pred = {}
    for name in ("isdigit", "isspace", "isalpha", "isalnum", "isupper", "islower"):
        pred[name] = kappa(f"if ({name}(c)) {{ return 1; }}\n    return 0;")
    check(len(set(pred.values())) == 1,
          "六个 `is*` 在 `if` 里的 κ 逐字节相同（各记 1 个操作）",
          str({k: v[:60] for k, v in pred.items()}))
    case = {}
    for name in ("toupper", "tolower"):
        case[name] = kappa(f"return {name}(c);")
    check(len(set(case.values())) == 1,
          "`toupper` / `tolower` 的 κ 逐字节相同（各记 1 个操作）",
          str({k: v[:60] for k, v in case.items()}))


def test_char_std_equivalence():
    """`std::isdigit(static_cast<unsigned char>(c))` 与 `isdigit(c)` 是同一个程序。

    κ 逐字节 / run 输出逐字节 / AST 同形——这是兼容层第 6 + 11 条一起生效的
    组合形状（也是 `04_tokenizer.cpp` 的实际写法）。
    """
    print("[19] `std::is*` + `static_cast<unsigned char>` 与直接 `is*` 逐字节相同")
    _equiv_pair(
        "`std::isdigit(static_cast<unsigned char>(c))`",
        """u64 classify(u8 c) {
    if (std::isdigit(static_cast<unsigned char>(c))) { return 1; }
    if (std::isspace(static_cast<unsigned char>(c))) { return 2; }
    if (std::isalpha(static_cast<unsigned char>(c))) { return 3; }
    if (std::isalnum(static_cast<unsigned char>(c))) { return 4; }
    if (std::isupper(static_cast<unsigned char>(c))) { return 5; }
    if (std::islower(static_cast<unsigned char>(c))) { return 6; }
    return 0;
}

u64 main() {
    print("{} {} {} {} {} {}\\n", classify(48), classify(32), classify(97),
          classify(35), classify(65), classify(122));
    print("{} {}\\n", std::toupper(97), std::tolower(90));
    return 0;
}
""",
        """u64 classify(u8 c) {
    if (isdigit(c)) { return 1; }
    if (isspace(c)) { return 2; }
    if (isalpha(c)) { return 3; }
    if (isalnum(c)) { return 4; }
    if (isupper(c)) { return 5; }
    if (islower(c)) { return 6; }
    return 0;
}

u64 main() {
    print("{} {} {} {} {} {}\\n", classify(48), classify(32), classify(97),
          classify(35), classify(65), classify(122));
    print("{} {}\\n", toupper(97), tolower(90));
    return 0;
}
""")


def test_static_cast_folds():
    """`static_cast` 的**两种可折形状**：值与 AST 都与直接写 `x` 相同。"""
    print("[20] 第 11 条：`static_cast<unsigned char|u64>(x)` 折成 `x`（值逐位不变）")
    _equiv_pair(
        "`static_cast<unsigned char>(u8)` 与直接 `c`",
        """u64 echo(u8 c) {
    return static_cast<unsigned char>(c);
}

u64 main() {
    print("{}\\n", echo(200));
    return 0;
}
""",
        """u64 echo(u8 c) {
    return c;
}

u64 main() {
    print("{}\\n", echo(200));
    return 0;
}
""")

    _equiv_pair(
        "`static_cast<u64>(u8)`（加宽）与直接 `c`",
        """u64 widen(u8 c) {
    return static_cast<u64>(c);
}

u64 main() {
    print("{}\\n", widen(255));
    return 0;
}
""",
        """u64 widen(u8 c) {
    return c;
}

u64 main() {
    print("{}\\n", widen(255));
    return 0;
}
""")


def test_static_cast_rejected():
    """**窄化 / 有符号 / 非整数 / 别的目标一律报错**（不把真转换折成 no-op）。"""
    print("[21] 第 11 条：折不成的形状 → E-Cast-Unsupported（不猜、不放宽）")
    for label, src, want in (
        ("窄化 `static_cast<unsigned char>(u64)`（u64 → u8 丢高位）",
         "u64 main() {\n    u64 n = 300;\n"
         "    return static_cast<unsigned char>(n);\n}\n",
         "窄"),
        ("窄化 `static_cast<u8>(u64)`（目标本身就不在白名单）",
         "u64 main() {\n    u64 n = 300;\n    return static_cast<u8>(n);\n}\n",
         "两种形状"),
        ("有符号 → 无符号 `static_cast<u64>(i64)`（负数变补码大数）",
         "u64 main() {\n    i64 n = 0 - 1;\n    return static_cast<u64>(n);\n}\n",
         "有符号"),
        ("非整数 `static_cast<u64>(f64)`",
         "u64 main() {\n    f64 x = 1.5;\n    return static_cast<u64>(x);\n}\n",
         "不是整数"),
        ("`reinterpret_cast` 仍然没有对应物",
         "u64 main() {\n    u64 n = 1;\n    return reinterpret_cast<u64>(n);\n}\n",
         "reinterpret_cast"),
    ):
        root = tempfile.mkdtemp(prefix="elncompat-cast-")
        try:
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(src)
            rc, out, err = cli("check", p, "--compat")
            blob = out + err
            check(rc != 0, f"{label} → 拒绝", f"rc={rc}")
            check("E-Cast-Unsupported" in blob,
                  f"{label} → E-Cast-Unsupported", blob.strip().split("\n")[0])
            check(want in blob, f"{label} → 消息说明「{want}」", blob[-260:])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # **折叠的判据只看静态类型，不看"这个值恰好没溢出"**：`u64 0` 也不折。
    root = tempfile.mkdtemp(prefix="elncompat-cast-shape-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 main() {\n    u64 n = 0;\n"
            "    return static_cast<unsigned char>(n);\n}\n")
        rc, out, err = cli("check", p, "--compat")
        check(rc != 0 and "E-Cast-Unsupported" in (out + err),
              "`static_cast<unsigned char>(u64 0)` 仍拒绝（值恰好没溢出不是类型事实）",
              (out + err).strip().split("\n")[0])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_cast_and_char_default_unchanged():
    """不带 `--compat`：`static_cast` / `std::isdigit` 都**不会被新代码放行**。"""
    print("[22] 第 11 条 / 字符分类：不带 --compat 时默认路径不变")
    root = tempfile.mkdtemp(prefix="elncompat-cast-default-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 echo(u8 c) {\n    return static_cast<unsigned char>(c);\n}\n"
            "u64 main() { return echo(48); }\n")
        rc, out, err = cli("check", p)
        blob = out + err
        check(rc != 0, "`static_cast` 不带 --compat 仍拒绝", f"rc={rc}")
        check("E-Cast-Unsupported" in blob
              and "本语言没有 `static_cast`" in blob,
              "`static_cast` 报的是**原文案** E-Cast-Unsupported（默认不变）",
              blob.strip().split("\n")[0])
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # **如实钉住旧行为**：`std::isdigit(c)` 不带 `--compat` 时**不是**
    # `E-Compat-UnknownStd`（那条只在开关打开、且名字不在白名单时才报）。
    # 它被解析成 `std` + `::` 的路径形状，`if` 条件因此变成 `std`；
    # 如实记下来，不编一个"报错"来凑用户说的码。
    for label, src, want in (
        ("`if (std::isdigit(c))`", """u64 main() {
    u8 c = 48;
    if (std::isdigit(c)) { return 1; }
    return 0;
}
""", "E-Type-Cond"),
        ("`bool b = std::isspace(c);`", """u64 main() {
    u8 c = 48;
    bool b = std::isspace(c);
    return 0;
}
""", None),
    ):
        root = tempfile.mkdtemp(prefix="elncompat-std-default-")
        try:
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(src)
            rc, out, err = cli("check", p)
            blob = out + err
            check(rc != 0, f"{label}：不带 --compat 时仍不被接受", f"rc={rc}")
            check("E-Compat-UnknownStd" not in blob,
                  f"{label}：旧路径本来就不报 E-Compat-UnknownStd"
                  f"（那条只在 --compat 下、名字不在白名单时报）",
                  blob.strip().split("\n")[0])
            if want:
                check(want in blob, f"{label}：报的是 {want}（旧行为，如实钉住）",
                      blob.strip().split("\n")[0])
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [23] 第 12 条

#: 第 12 条的**参考循环**：手写的索引循环。三种拼写都必须落到**这一棵树**上
#: （`_equiv_pair` 会逐字节比 κ / run / AST）。
_ITER_REF = (
    "u64 total(vector<u64>& xs) {\n"
    "    u64 s = 0;\n"
    "    for (u64 idx = 0; idx < xs.size(); idx = idx + 1) {\n"
    "        s = s + xs[idx];\n"
    "        print(xs[idx]);\n"
    "    }\n"
    "    return s;\n"
    "}\n"
    "\n"
    "u64 main() {\n"
    "    vector<u64> xs = {1, 2, 3};\n"
    "    return total(xs);\n"
    "}\n"
)


def test_iter_for_three_shapes():
    """第 12 条：三种拼写（①②③）都降级成**手写索引循环**。

    `--compat` 下 `it` 变成一个**下标**（新导入的 `u64 idx`），所以整段循环
    与手写的 `for (u64 idx = 0; idx < xs.size(); idx = idx + 1)` 是**同一个
    AST**——κ 与 run 输出因此必然逐字节相同，不需要真的实现迭代器对象。
    """
    print("[23] 第 12 条：三段式 for 的唯一标准迭代器形状 → 索引循环")

    _iter_pair(
        "① `for (auto it = xs.begin(); …)`",
        "    for (auto it = xs.begin(); it != xs.end(); ++it) {\n"
        "        s = s + *it;\n"
        "        print(*it);\n"
        "    }\n")
    _iter_pair(
        "② `for (vector<u64>::iterator it = …)`（声明的类型被忽略）",
        "    for (vector<u64>::iterator it = xs.begin(); it != xs.end(); ++it) {\n"
        "        s = s + *it;\n"
        "        print(*it);\n"
        "    }\n")
    _iter_pair(
        "③ `for (it = xs.begin(); …)`（`it` 已在外声明）",
        "    u64 it = 0;\n"
        "    for (it = xs.begin(); it != xs.end(); ++it) {\n"
        "        s = s + *it;\n"
        "        print(*it);\n"
        "    }\n",
        extra_ours="    u64 it = 0;\n")

    # `it->f` / `it.f` 都降级成 `xs[idx].f`（结构体字段，AST 级对比就够：
    # 运行期由同一棵 AST 决定，前面三条已经钉住了 run）。
    a = ast_norm(
        "u64 f(vector<Point> ps) {\n"
        "    u64 s = 0;\n"
        "    for (auto it = ps.begin(); it != ps.end(); ++it) {\n"
        "        s = s + it->x;\n"
        "        s = s + it.y;\n"
        "    }\n"
        "    return s;\n}\n", True)
    b = ast_norm(
        "u64 f(vector<Point> ps) {\n"
        "    u64 s = 0;\n"
        "    for (u64 idx = 0; idx < ps.size(); idx = idx + 1) {\n"
        "        s = s + ps[idx].x;\n"
        "        s = s + ps[idx].y;\n"
        "    }\n"
        "    return s;\n}\n", False)
    check(a is not None and a == b,
          "`it->x` / `it.y` 都降级成 `ps[idx].x` / `ps[idx].y`",
          _diff_head(_jsonl(a), _jsonl(b)))

    # **不误报**：两个连续的形状③循环 + 另一个函数里同名变量 `it`。
    # 体里的 `it` 属于**本循环**（形状③在头里重新初始化），不该算"逃逸"；
    # 同名变量在**另一个函数**里也不该被上一个函数的状态牵连。
    root = tempfile.mkdtemp(prefix="elncompat-iterfor-noesc-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 f(vector<u64>& xs) {\n"
            "    u64 s = 0;\n"
            "    u64 it = 0;\n"
            "    for (it = xs.begin(); it != xs.end(); ++it) { s = s + *it; }\n"
            "    for (it = xs.begin(); it != xs.end(); ++it) { s = s + *it; }\n"
            "    return s;\n}\n\n"
            "u64 g(vector<u64>& xs) {\n"
            "    u64 it = 7;\n"
            "    print(it);\n"
            "    return it;\n}\n")
        rc, out, err = cli("check", p, "--compat")
        blob = out + err
        check(rc == 0,
              "两个连续的形状③循环 + 另一个函数里的同名 `it` → 不误报",
              blob.strip().split("\n")[0])
        check("E-For-IteratorEscape" not in blob,
              "不报 E-For-IteratorEscape（体里的 `it` 属于本循环）",
              blob.strip().split("\n")[0])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _iter_pair(label, loop_cpp, extra_ours=""):
    """第 12 条成对断言：C++ 拼写（`--compat`）vs 同一个手写索引循环。"""
    cpp = ("u64 total(vector<u64>& xs) {\n"
           "    u64 s = 0;\n" + loop_cpp +
           "    return s;\n}\n\n"
           "u64 main() {\n    vector<u64> xs = {1, 2, 3};\n"
           "    return total(xs);\n}\n")
    ours = ("u64 total(vector<u64>& xs) {\n"
            "    u64 s = 0;\n" + extra_ours +
            "    for (u64 idx = 0; idx < xs.size(); idx = idx + 1) {\n"
            "        s = s + xs[idx];\n"
            "        print(xs[idx]);\n"
            "    }\n"
            "    return s;\n}\n\n"
            "u64 main() {\n    vector<u64> xs = {1, 2, 3};\n"
            "    return total(xs);\n}\n")
    _equiv_pair(label, cpp, ours)


#: 第 12 条**必须拒绝**的形状：(诊断码, 说明, 循环体片段)。
#: 每条都要报清楚，并且消息里必须教"改写成本语言哪种 for"。
_ITER_REJECTS = (
    ("E-For-IteratorTerm", "`it < xs.end()`（不是 `!=`）",
     "    for (auto it = xs.begin(); it < xs.end(); ++it) { s = s + *it; }\n"),
    ("E-For-IteratorTerm", "`it != xs.end() - 1`（终止条件带偏移）",
     "    for (auto it = xs.begin(); it != xs.end() - 1; ++it) { s = s + *it; }\n"),
    ("E-For-IteratorReverse", "`rbegin()` / `rend()`（反向迭代）",
     "    for (auto it = xs.rbegin(); it != xs.rend(); ++it) { s = s + *it; }\n"),
    ("E-For-IteratorReverse", "`--it`（反向推进）",
     "    for (auto it = xs.begin(); it != xs.end(); --it) { s = s + *it; }\n"),
    ("E-For-IteratorArith", "`it += 2`（对迭代器做算术）",
     "    for (auto it = xs.begin(); it != xs.end(); it += 2) { s = s + *it; }\n"),
    ("E-For-IteratorArith", "体里 `it + 1`（对迭代器做算术）",
     "    for (auto it = xs.begin(); it != xs.end(); ++it) {\n"
     "        u64 k = it + 1;\n    }\n"),
    ("E-For-IteratorMutate", "体里 `xs.push_back`（改正在遍历的容器）",
     "    for (auto it = xs.begin(); it != xs.end(); ++it) { xs.push_back(9); }\n"),
    ("E-For-IteratorMutate", "体里 `sort(xs)`（重排正在遍历的容器）",
     "    for (auto it = xs.begin(); it != xs.end(); ++it) { sort(xs); }\n"),
    ("E-For-IteratorShape", "体里裸用 `it`（没有等价的降级）",
     "    for (auto it = xs.begin(); it != xs.end(); ++it) { print(it); }\n"),
    ("E-For-IteratorShape", "体里重新声明 `it`（遮蔽）",
     "    for (auto it = xs.begin(); it != xs.end(); ++it) { u64 it = 5; }\n"),
    ("E-For-IteratorShape", "`begin()` 与 `end()` 不是同一个容器",
     None),
)


def test_iter_for_rejects():
    """第 12 条：**其余迭代器形状一律拒绝**，不许硬撑。

    每一条都断言两件事：① 报的是**这个**码；② 消息里教了改法
    （本语言的 range-for 或索引循环——`文档/语法清单.md` §P0 第 12 条）。
    """
    print("[24] 第 12 条：不支持的迭代器形状各自报清楚（并教改法）")

    for code, label, body in _ITER_REJECTS:
        root = tempfile.mkdtemp(prefix="elncompat-iterfor-")
        try:
            p = os.path.join(root, "mod.eln")
            if body is None:                      # 两个不同容器
                src = ("u64 f(vector<u64>& xs, vector<u64>& ys) {\n"
                       "    u64 s = 0;\n"
                       "    for (auto it = xs.begin(); it != ys.end(); ++it) {"
                       " s = s + *it; }\n"
                       "    return s;\n}\n")
            else:
                src = ("u64 sort(vector<u64>& xs) { return 0; }\n"
                       "u64 f(vector<u64>& xs) {\n"
                       "    u64 s = 0;\n" + body + "    return s;\n}\n")
            io.open(p, "w", encoding="utf-8", newline="\n").write(src)
            rc, out, err = cli("check", p, "--compat")
            blob = out + err
            check(rc != 0, f"{label} → 拒绝", f"rc={rc}")
            check(code in blob, f"{label} → {code}",
                  blob.strip().split("\n")[0])
            teaches = ("for (auto& x : xs)" in blob
                       or "for (u64 i = 0" in blob
                       or "while (i < xs.size())" in blob)
            check("改法" in blob and teaches,
                  f"{label} → 消息教了改写成哪种 for",
                  blob.strip().split("\n")[0])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # **形状③的逃逸**：`it` 在外声明，降级后循环不再写它——循环之后读到的是
    # 旧值（C++ 里也已离开作用域），所以必须拒绝。
    root = tempfile.mkdtemp(prefix="elncompat-iterfor-esc-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 f(vector<u64>& xs) {\n"
            "    u64 s = 0;\n"
            "    u64 it = 0;\n"
            "    for (it = xs.begin(); it != xs.end(); ++it) { s = s + *it; }\n"
            "    print(it);\n"
            "    return s;\n}\n")
        rc, out, err = cli("check", p, "--compat")
        blob = out + err
        check(rc != 0, "形状③：`it` 离开循环作用域被使用 → 拒绝", f"rc={rc}")
        check("E-For-IteratorEscape" in blob,
              "形状③：报 E-For-IteratorEscape", blob.strip().split("\n")[0])
        check("改法" in blob and "while (i < xs.size())" in blob,
              "形状③：诊断教了改成自己写索引循环", blob.strip().split("\n")[0])
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # **形状①②的逃逸**：`it` 就声明在 for 里，循环之后它**根本不存在**
    # （与 C++ 同义）——如实钉住现有的 `E-Name-Undefined`，不额外造一个码。
    root = tempfile.mkdtemp(prefix="elncompat-iterfor-scope-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 f(vector<u64>& xs) {\n"
            "    u64 s = 0;\n"
            "    for (auto it = xs.begin(); it != xs.end(); ++it) { s = s + *it; }\n"
            "    print(it);\n"
            "    return s;\n}\n")
        rc, out, err = cli("check", p, "--compat")
        blob = out + err
        check(rc != 0, "形状①②：`it` 在循环之后不存在 → 拒绝", f"rc={rc}")
        check("E-Name-Undefined" in blob and "it" in blob,
              "形状①②：报 E-Name-Undefined（与 C++ 的作用域一致）",
              blob.strip().split("\n")[0])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [24b] 第 12b 条
#
# **`map` / `set` 的迭代器**（2026-11-12）。第 12 条的降级目标是 `xs[idx]`，
# 而 `map` 没有位置索引（`m[idx]` 是**按键查**、缺键还插默认值），所以
# `it->first` 拿不到东西——2026-11-11 起这里干脆**拒绝**
# （`E-For-IteratorNotIndexable`）。本轮补上正确的降级：
#
#     for (auto it = m.begin(); it != m.end(); ++it) { … }
#       →
#     {
#         vector<K> ks = m.keys();      // 快照**只在循环外**取一次（O(n)）
#         u64 idx = 0;
#         while (idx < ks.size()) {
#             it->first  → ks[idx]
#             it->second → m.get(ks[idx])
#             *it        → ( ks[idx], m.get(ks[idx]) )
#             idx = idx + 1;
#         }
#     }
#
# 降级目标就是**手写"先 keys() 再索引循环"那一棵 AST**，所以 κ / run 必然
# 逐字节相同（下面 `_equiv_pair` 四样都比）。**κ 比 C++ 贵一档**：C++ 的
# `std::map` 遍历 O(n)，这里是 `keys()` O(n) + 每轮 `get` O(log n) ⇒ O(n log n)
# （`unordered_map` 的 `get` 最坏 O(n) ⇒ O(n²)）——安全方向，文档写清了。


def _walk_nodes(obj):
    """遍历 AST JSON 里的所有节点 dict（`ast_norm` 的返回值是纯 dict/list）。"""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_nodes(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from _walk_nodes(x)


def _nodes(ast, kind, name=None):
    return [n for n in _walk_nodes(ast)
            if n.get("kind") == kind and (name is None or n.get("name") == name)]


def test_iter_for_map_set_equiv():
    """第 12b 条：`map` / `set` 的迭代器 → 键快照 + 下标循环（κ / run / AST 四样同）。"""
    print("[24b] 第 12b 条：`map` / `set` 的迭代器 → 键快照 + 下标循环")

    # ① `map`：`it->first` / `it->second` / `*it` 三种读法全覆盖。
    _equiv_pair(
        "`map`：`it->first` / `it->second` / `*it` 与手写快照循环",
        "u64 total(map<Vec<u8>, u64>& m) {\n"
        "    u64 s = 0;\n"
        "    for (auto it = m.begin(); it != m.end(); ++it) {\n"
        "        s = s + it->first.size();\n"
        "        s = s + it->second;\n"
        "        print(*it);\n"
        "    }\n"
        "    return s;\n"
        "}\n\n"
        "u64 main() {\n"
        "    map<Vec<u8>, u64> m;\n"
        "    m.insert(\"b\", 2);\n"
        "    m.insert(\"a\", 1);\n"
        "    return total(m);\n"
        "}\n",
        "u64 total(map<Vec<u8>, u64>& m) {\n"
        "    u64 s = 0;\n"
        "    {\n"
        "        vector<Vec<u8>> ks = m.keys();\n"
        "        u64 idx = 0;\n"
        "        while (idx < ks.size()) {\n"
        "            s = s + ks[idx].size();\n"
        "            s = s + m.get(ks[idx]);\n"
        "            print(( ks[idx], m.get(ks[idx]) ));\n"
        "            idx = idx + 1;\n"
        "        }\n"
        "    }\n"
        "    return s;\n"
        "}\n\n"
        "u64 main() {\n"
        "    map<Vec<u8>, u64> m;\n"
        "    m.insert(\"b\", 2);\n"
        "    m.insert(\"a\", 1);\n"
        "    return total(m);\n"
        "}\n")

    # ② `set`：只有 `*it`；快照用 `.values()`（`set` 没有 `keys()`）。
    _equiv_pair(
        "`set`：`*it` 与手写 `.values()` 快照循环",
        "u64 total_set(set<u64>& t) {\n"
        "    u64 s = 0;\n"
        "    for (auto it = t.begin(); it != t.end(); ++it) {\n"
        "        s = s + *it;\n"
        "        print(*it);\n"
        "    }\n"
        "    return s;\n"
        "}\n\n"
        "u64 main() {\n"
        "    set<u64> t;\n"
        "    t.insert(3);\n"
        "    t.insert(1);\n"
        "    return total_set(t);\n"
        "}\n",
        "u64 total_set(set<u64>& t) {\n"
        "    u64 s = 0;\n"
        "    {\n"
        "        vector<u64> ks = t.values();\n"
        "        u64 idx = 0;\n"
        "        while (idx < ks.size()) {\n"
        "            s = s + ks[idx];\n"
        "            print(ks[idx]);\n"
        "            idx = idx + 1;\n"
        "        }\n"
        "    }\n"
        "    return s;\n"
        "}\n\n"
        "u64 main() {\n"
        "    set<u64> t;\n"
        "    t.insert(3);\n"
        "    t.insert(1);\n"
        "    return total_set(t);\n"
        "}\n")

    # ③ `unordered_map`：键是标量（不依赖"容器键可哈希"那条），顺序两边都按
    #    各自的实现；这里只比 κ / run / AST，同形就必然相同。
    _equiv_pair(
        "`unordered_map`：`it->second` 与手写 `.keys()` 快照循环",
        "u64 total_u(unordered_map<u64, u64>& m) {\n"
        "    u64 s = 0;\n"
        "    for (auto it = m.begin(); it != m.end(); ++it) {\n"
        "        s = s + it->second;\n"
        "    }\n"
        "    return s;\n"
        "}\n\n"
        "u64 main() {\n"
        "    unordered_map<u64, u64> m;\n"
        "    m.insert(3, 30);\n"
        "    m.insert(1, 10);\n"
        "    return total_u(m);\n"
        "}\n",
        "u64 total_u(unordered_map<u64, u64>& m) {\n"
        "    u64 s = 0;\n"
        "    {\n"
        "        vector<u64> ks = m.keys();\n"
        "        u64 idx = 0;\n"
        "        while (idx < ks.size()) {\n"
        "            s = s + m.get(ks[idx]);\n"
        "            idx = idx + 1;\n"
        "        }\n"
        "    }\n"
        "    return s;\n"
        "}\n\n"
        "u64 main() {\n"
        "    unordered_map<u64, u64> m;\n"
        "    m.insert(3, 30);\n"
        "    m.insert(1, 10);\n"
        "    return total_u(m);\n"
        "}\n")

    # ④ 02 号语料的形状：`std::map<std::string, int>::iterator`（声明类型被忽略，
    #    `std::string` → `vector<u8>`，`int` → `i64`）。
    _equiv_pair(
        "`std::map<std::string, int>::iterator`（02_csv_stats 的形状）",
        "u64 total_std(std::map<std::string, int>& m) {\n"
        "    u64 s = 0;\n"
        "    for (std::map<std::string, int>::iterator it = m.begin();\n"
        "         it != m.end(); ++it) {\n"
        "        s = s + it->first.size();\n"
        "        s = s + it->second;\n"
        "    }\n"
        "    return s;\n"
        "}\n\n"
        "u64 main() {\n"
        "    std::map<std::string, int> m;\n"
        "    m.insert(\"b\", 2);\n"
        "    m.insert(\"a\", 1);\n"
        "    return total_std(m);\n"
        "}\n",
        "u64 total_std(map<vector<u8>, i64>& m) {\n"
        "    u64 s = 0;\n"
        "    {\n"
        "        vector<vector<u8>> ks = m.keys();\n"
        "        u64 idx = 0;\n"
        "        while (idx < ks.size()) {\n"
        "            s = s + ks[idx].size();\n"
        "            s = s + m.get(ks[idx]);\n"
        "            idx = idx + 1;\n"
        "        }\n"
        "    }\n"
        "    return s;\n"
        "}\n\n"
        "u64 main() {\n"
        "    map<vector<u8>, i64> m;\n"
        "    m.insert(\"b\", 2);\n"
        "    m.insert(\"a\", 1);\n"
        "    return total_std(m);\n"
        "}\n")

    # ⑤ 旧名 `Map` / `Set`（`typenames.CANON`）走**同一条**降级。漏掉它们就会
    #    把 `Map<u64, u64>` 的迭代器降级成 `m[idx]`——check 绿、run 崩。
    _equiv_pair(
        "旧名 `Map` / `Set`（canon 之后同一条降级）",
        "u64 f(Map<u64, u64>& m) {\n"
        "    u64 s = 0;\n"
        "    for (auto it = m.begin(); it != m.end(); ++it) {\n"
        "        s = s + it->first + it->second;\n"
        "    }\n"
        "    return s;\n"
        "}\n\n"
        "u64 main() {\n"
        "    Map<u64, u64> m;\n"
        "    m.insert(1, 10);\n"
        "    return f(m);\n"
        "}\n",
        "u64 f(Map<u64, u64>& m) {\n"
        "    u64 s = 0;\n"
        "    {\n"
        "        vector<u64> ks = m.keys();\n"
        "        u64 idx = 0;\n"
        "        while (idx < ks.size()) {\n"
        "            s = s + ks[idx] + m.get(ks[idx]);\n"
        "            idx = idx + 1;\n"
        "        }\n"
        "    }\n"
        "    return s;\n"
        "}\n\n"
        "u64 main() {\n"
        "    Map<u64, u64> m;\n"
        "    m.insert(1, 10);\n"
        "    return f(m);\n"
        "}\n")


def test_iter_for_map_set_shapes():
    """第 12b 条的**结构性**判据：快照只取一次、在循环外、名字不遮蔽用户变量。"""
    print("[24c] 第 12b 条：快照只取一次（循环外）+ 内部名避让")

    ast = ast_norm(
        "u64 f(map<u64, u64>& m) {\n"
        "    u64 s = 0;\n"
        "    for (auto it = m.begin(); it != m.end(); ++it) {\n"
        "        s = s + it->second;\n"
        "    }\n"
        "    return s;\n"
        "}\n", True)
    keys = _nodes(ast, "method-call", "keys")
    check(len(keys) == 1, "`keys()` 全函数只出现**一次**（快照只取一次）",
          f"{len(keys)} 次")
    whiles = _nodes(ast, "while")
    check(len(whiles) == 1, "降级产出恰好一个 `while`", f"{len(whiles)} 个")
    if whiles:
        inner_keys = [n for n in _walk_nodes(whiles[0])
                      if n.get("kind") == "method-call"
                      and n.get("name") == "keys"]
        check(not inner_keys, "`keys()` **不在** while 里（快照在循环外）",
              f"{len(inner_keys)} 次在循环内")
    gets = _nodes(ast, "method-call", "get")
    check(len(gets) == 1, "`it->second` 恰好变成一次 `m.get(...)`", f"{len(gets)} 次")
    tuples = _nodes(ast, "tuple")
    check(not tuples, "只有 `it->second` 时**不**造元组（`*it` 才造）",
          f"{len(tuples)} 个")

    # `*it` → 二元组 `( ks[idx], m.get(ks[idx]) )`（compat 的 `.first/.second`
    # 按静态类型映到 `.0/.1`，所以「`*it` 本身可读」）；而 `(*it).first` /
    # `(*it).second` **不**造元组（直接映到键 / `get`，少一次取值）。
    ast2 = ast_norm(
        "u64 f(map<u64, u64>& m) {\n"
        "    u64 s = 0;\n"
        "    for (auto it = m.begin(); it != m.end(); ++it) {\n"
        "        s = s + (*it).first + (*it).second;\n"
        "        print(*it);\n"
        "    }\n"
        "    return s;\n"
        "}\n", True)
    check(len(_nodes(ast2, "tuple")) == 1,
          "`*it` 造一个二元组节点；`(*it).first` / `.second` 直接映字段",
          f"{len(_nodes(ast2, 'tuple'))} 个")
    check(len(_nodes(ast2, "method-call", "get")) == 2,
          "`(*it).second` 与 `*it` 的值各一次 `get`",
          f"{len(_nodes(ast2, 'method-call', 'get'))} 次")

    # **内部名不遮蔽用户变量**：用户已经有 `ks` / `idx` 时，降级用 `ks2` / `idx2`，
    # 用户那两个名字的含义一个字节都不变（数值断言 + AST 断言）。
    root = tempfile.mkdtemp(prefix="elncompat-iterfor-mapname-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 f(map<u64, u64>& m) {\n"
            "    u64 ks = 7;\n"
            "    u64 idx = 3;\n"
            "    u64 s = 0;\n"
            "    for (auto it = m.begin(); it != m.end(); ++it) {\n"
            "        s = s + it->second;\n"
            "    }\n"
            "    return s + ks * 100 + idx;\n"
            "}\n\n"
            "u64 main() {\n"
            "    map<u64, u64> m;\n"
            "    m.insert(3, 30);\n"
            "    m.insert(1, 10);\n"
            "    return f(m);\n"
            "}\n")
        rc, out, err = cli("check", p, "--compat")
        blob = out + err
        check(rc == 0, "用户已有 `ks` / `idx` 时仍能过 check",
              blob.strip().split("\n")[0])
        rc2, run_out, run_err = cli("run", p, "--compat")
        check(rc2 == 0 and "743" in run_out,
              "降级用 `ks2` / `idx2`：用户变量不变（40 + 7*100 + 3 = 743）",
              f"rc={rc2} {run_out.strip()!r} {run_err.strip()[:80]}")

        ast3 = ast_norm(io.open(p, encoding="utf-8").read(), True)
        names = {n.get("name") for n in _nodes(ast3, "var-decl")}
        check({"ks", "idx", "ks2", "idx2"} <= names,
              "AST 里同时有用户变量 `ks` / `idx` 与降级导入的 `ks2` / `idx2`",
              str(sorted(names)))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # **`continue` 仍然推进下标**（与第 12 条同一处理：C++ 的 for 里 continue
    # 会执行第三段）。写错就是死循环，所以用带超时的 CLI 跑。
    root = tempfile.mkdtemp(prefix="elncompat-iterfor-mapcont-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 f(map<u64, u64>& m) {\n"
            "    u64 s = 0;\n"
            "    for (auto it = m.begin(); it != m.end(); ++it) {\n"
            "        if (it->first == 3) { continue; }\n"
            "        s = s + it->second;\n"
            "    }\n"
            "    return s;\n"
            "}\n\n"
            "u64 main() {\n"
            "    map<u64, u64> m;\n"
            "    m.insert(3, 30);\n"
            "    m.insert(1, 10);\n"
            "    return f(m);\n"
            "}\n")
        rc, run_out, run_err = cli("run", p, "--compat")
        check(rc == 0 and "10" in run_out,
              "体里的 `continue` 仍推进下标（跳过 30，只剩 10）",
              f"rc={rc} {run_out.strip()!r} {run_err.strip()[:80]}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


#: 第 12b 条**必须拒绝**的形状：(诊断码, 说明, 完整源码)。
_ITER_MAP_REJECTS = (
    ("E-For-IteratorShape", "`set` 的 `it->second`（`set` 没有值）",
     "u64 f(set<u64>& t) {\n"
     "    u64 s = 0;\n"
     "    for (auto it = t.begin(); it != t.end(); ++it) { s = s + it->second; }\n"
     "    return s;\n}\n"),
    ("E-For-IteratorShape", "`map` 的 `it->third`（迭代元素只有键 / 值两位）",
     "u64 f(map<u64, u64>& m) {\n"
     "    u64 s = 0;\n"
     "    for (auto it = m.begin(); it != m.end(); ++it) { s = s + it->third; }\n"
     "    return s;\n}\n"),
    ("E-For-IteratorShape", "`map m;`（没有类型实参，推不出键类型）",
     "u64 f(map m) {\n"
     "    u64 s = 0;\n"
     "    for (auto it = m.begin(); it != m.end(); ++it) { s = s + it->second; }\n"
     "    return s;\n}\n"),
    ("E-For-IteratorMutate", "`it->second = 7`（写迭代元素只丢写）",
     "u64 f(map<u64, u64>& m) {\n"
     "    for (auto it = m.begin(); it != m.end(); ++it) { it->second = 7; }\n"
     "    return 0;\n}\n"),
    ("E-For-IteratorMutate", "`*it = (1, 2)`（整体重新绑定）",
     "u64 f(map<u64, u64>& m) {\n"
     "    for (auto it = m.begin(); it != m.end(); ++it) { *it = (1, 2); }\n"
     "    return 0;\n}\n"),
    ("E-For-IteratorMutate", "`it->second.x = 7`（写值里的字段＝改表）",
     "struct P { u64 x; }\n"
     "u64 f(map<u64, P>& m) {\n"
     "    for (auto it = m.begin(); it != m.end(); ++it) { it->second.x = 7; }\n"
     "    return 0;\n}\n"),
    ("E-For-IteratorMutate", "体里 `m.clear()`（改正在遍历的容器）",
     "u64 f(map<u64, u64>& m) {\n"
     "    for (auto it = m.begin(); it != m.end(); ++it) { m.clear(); }\n"
     "    return 0;\n}\n"),
    ("E-For-IteratorEscape", "形状③：`it` 在外声明、循环之后被读",
     "u64 f(map<u64, u64>& m) {\n"
     "    u64 s = 0;\n"
     "    u64 it = 0;\n"
     "    for (it = m.begin(); it != m.end(); ++it) { s = s + it->second; }\n"
     "    return s + it;\n}\n"),
)


def test_iter_for_map_set_rejects():
    """第 12b 条：不能等价降级的形状**照旧拒绝**，且教改法（不静默）。"""
    print("[24d] 第 12b 条：不能降级的形状各自报清楚（并教改法）")

    for code, label, src in _ITER_MAP_REJECTS:
        root = tempfile.mkdtemp(prefix="elncompat-iterfor-maprej-")
        try:
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(src)
            rc, out, err = cli("check", p, "--compat")
            blob = out + err
            check(rc != 0, f"{label} → 拒绝", f"rc={rc}")
            check(code in blob, f"{label} → {code}",
                  blob.strip().split("\n")[0])
            check("改法" in blob or "写法" in blob,
                  f"{label} → 消息教了改法", blob.strip().split("\n")[0])
            check("E-For-IteratorNotIndexable" not in blob,
                  f"{label} → 不再报旧的 E-For-IteratorNotIndexable",
                  blob.strip().split("\n")[0])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # **`arr[it->second] = 1` 不拒**：位置的根是 `arr`（写的是 arr），
    # `it->second` 只是**读**出来的下标。拒了它就是把合法程序判死。
    root = tempfile.mkdtemp(prefix="elncompat-iterfor-mapidxw-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 f(map<u64, u64>& m) {\n"
            "    vector<u64> arr;\n"
            "    arr.push_back(0);\n"
            "    arr.push_back(0);\n"
            "    for (auto it = m.begin(); it != m.end(); ++it) {\n"
            "        arr[it->second] = 1;\n"
            "    }\n"
            "    return arr.size() + arr[0] + arr[1];\n"
            "}\n\n"
            "u64 main() {\n"
            "    map<u64, u64> m;\n"
            "    m.insert(1, 1);\n"
            "    return f(m);\n"
            "}\n")
        rc, run_out, run_err = cli("run", p, "--compat")
        check(rc == 0 and "3" in run_out,
              "`arr[it->second] = 1` 是**写 arr**（把值当下标读）→ 放行",
              f"rc={rc} {run_out.strip()!r} {run_err.strip()[:80]}")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # **`compat=False` 一个字节都没动**：同样的 `map` 迭代器循环，不带开关时
    # 报的是**原文案** `E-For-NoIterator`（`begin` 这个词触发），且没有新码。
    root = tempfile.mkdtemp(prefix="elncompat-iterfor-mapoff-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 f(map<u64, u64>& m) {\n"
            "    u64 s = 0;\n"
            "    for (auto it = m.begin(); it != m.end(); ++it) { s = s + it->second; }\n"
            "    return s;\n}\n")
        rc, out, err = cli("check", p)
        blob = out + err
        check(rc != 0, "不带 --compat：`map` 的迭代器循环仍被拒绝", f"rc={rc}")
        check("E-For-NoIterator" in blob,
              "不带 --compat：报的是**原文案** E-For-NoIterator（默认路径不变）",
              blob.strip().split("\n")[0])
        check("E-For-IteratorNotIndexable" not in blob and "keys()" not in blob,
              "不带 --compat：没有降级的痕迹（旧的 NotIndexable / keys() 都不出现）",
              blob.strip().split("\n")[0])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [25] 第 13 条
#
# **struct 的成员函数与构造函数**（2026-11-11，第 5 轮）。
#
# 判据与第 1–12 条同一把尺子：**剥掉语法糖之后还是同一个程序**。这一条的
# 糖是"方法与构造"，糖衣下面的东西是**自由函数 + 调用**：
#
#   | C++ 写法 | 降级成（一字不多、一字不少） |
#   |---|---|
#   | `void push(int v) { … }` | `() Ring_push(Ring& recv, i64 v) { recv.… }` |
#   | `Ring(std::size_t cap) : buf(cap, 0), head(0) {}` | `() Ring_Ring(Ring& recv, u64 cap) { recv.buf = vector<i64>::filled(0, cap); recv.head = 0; }` |
#   | `r.push(1)` | `Ring_push(r, 1)` |
#   | `Ring r(3);` | `Ring r; Ring_Ring(r, 3);` |
#   | `Ring r;`（该 struct 有 0 参构造） | `Ring r; Ring_Ring(r);` |
#   | 体里裸写字段 `buf` | `recv.buf`（接收者形参优先叫 `recv`，被占用时 `recv2`…） |
#
# 于是"与手写等价"是**构造出来的**，不是碰巧：同一份 AST ⇒ 同一份 κ、同一份
# 输出。下面 [25] 用 05 号语料的环形缓冲逐字节验证，[26] 逐个形状比 AST。

#: 第 13 条的"环缓冲"：C++ 拼写（`--compat` 下）与手写等价的本语言拼写。
_RING_CPP = """#include <vector>

struct Ring {
    std::vector<int> buf;
    std::size_t head;
    std::size_t count;

    Ring(std::size_t cap) : buf(cap, 0), head(0), count(0) {}

    void push(int v) {
        buf[(head + count) % buf.size()] = v;
        if (count == buf.size()) head = (head + 1) % buf.size();
        else count += 1;
    }

    int pop() {
        int v = buf[head];
        head = (head + 1) % buf.size();
        count -= 1;
        return v;
    }
};

int main() {
    Ring r(3);
    r.push(1); r.push(2); r.push(3); r.push(4);
    print("{} {}\\n", r.pop(), r.pop());
    return 0;
}
"""

_RING_OURS_TU = """struct Ring { buf: vector<i64>, head: u64, count: u64, }
"""

_RING_OURS_ELN = """() Ring_Ring(Ring& recv, u64 cap) {
    recv.buf = vector<i64>::filled(0, cap);
    recv.head = 0;
    recv.count = 0;
}

() Ring_push(Ring& recv, i64 v) {
    recv.buf[(recv.head + recv.count) % recv.buf.size()] = v;
    if (recv.count == recv.buf.size()) recv.head = (recv.head + 1) % recv.buf.size();
    else recv.count += 1;
}

i64 Ring_pop(Ring& recv) {
    i64 v = recv.buf[recv.head];
    recv.head = (recv.head + 1) % recv.buf.size();
    recv.count -= 1;
    return v;
}

i64 main() {
    Ring r;
    Ring_Ring(r, 3);
    Ring_push(r, 1); Ring_push(r, 2); Ring_push(r, 3); Ring_push(r, 4);
    print("{} {}\\n", Ring_pop(r), Ring_pop(r));
    return 0;
}
"""


def test_member_fn_equivalence():
    """[25] 第 13 条：`05_ring_buffer.cpp` 的成员函数 / 构造函数。

    C++ 拼写（+ `--compat`）与手写等价的本语言拼写（不带开关）比：
    **check 都能过 / κ 逐字节相同 / run 输出逐字节相同 / AST 同形**
    （AST 比 `.eln` 这一侧，`struct` 那一项去掉——两边文件分工不同）。
    """
    print("[25] 第 13 条：成员函数 + 构造函数（05_ring_buffer 等价性）")
    root = tempfile.mkdtemp(prefix="elncompat-member-")
    try:
        pair = Pair(root)
        pair.write("cpp", None, _RING_CPP)
        pair.write("ours", _RING_OURS_TU, _RING_OURS_ELN)

        rc, out, err = cli("check", os.path.join(pair.cpp, "mod"), "--compat")
        check(rc == 0, "25：C++ 拼写 + --compat 能过 check",
              (out + err).strip().split("\n")[0])
        rc, out, err = cli("check", os.path.join(pair.ours, "mod"))
        check(rc == 0, "25：本语言拼写（不带开关）能过 check",
              (out + err).strip().split("\n")[0])

        r1, kcpp, e1 = cli("cost", os.path.join(pair.cpp, "mod"), "--compat",
                           "--signature")
        r2, kours, e2 = cli("cost", os.path.join(pair.ours, "mod"), "--signature")
        check(r1 == 0 and r2 == 0, "25：两边的 cost 都退出 0", f"{r1}/{r2}")
        check(kcpp == kours, "25：κ 逐字节相同（cost --signature 整份 JSON）",
              _diff_head(kcpp, kours))

        r3, ocpp, e3 = cli("run", os.path.join(pair.cpp, "mod"), "--compat")
        r4, oours, e4 = cli("run", os.path.join(pair.ours, "mod"))
        check(r3 == 0 and r4 == 0, "25：两边的 run 都退出 0", f"{r3}/{r4}")
        check(ocpp == oours, "25：run 输出逐字节相同", _diff_head(ocpp, oours))
        check(ocpp.startswith("2 3"), "25：环形缓冲的输出是 `2 3`（满时覆盖最老的）",
              repr(ocpp[:20]))

        sa = _drop_kinds(ast_norm(_RING_CPP, True), {"struct"})
        sb = ast_norm(_RING_OURS_ELN, False)
        check(sa == sb, "25：`.eln` 的 AST 同形（去掉 struct 与行列号后逐字节）",
              _diff_head(_jsonl(sa), _jsonl(sb)))
    finally:
        shutil.rmtree(root, ignore_errors=True)


#: 第 13 条的**形状表**：`(标签, C++ 拼写, 手写等价的本语言拼写)`。
#:
#: 每一对都必须是**同一棵树**（`struct` 那项除外）：降级是纯语法变换，
#: 形状覆盖越全，"与手写等价"这条承诺就越难被悄悄破坏。
_MEMBER_SHAPES = [
    (
        "字段声明在成员函数之后",
        "struct R {\n"
        "    void bump() { total = total + 1; }\n"
        "    u64 total;\n"
        "};\n"
        "u64 main() { R r; r.bump(); return r.total; }\n",
        "() R_bump(R& recv) { recv.total = recv.total + 1; }\n"
        "u64 main() { R r; R_bump(r); return r.total; }\n",
    ),
    (
        "兄弟成员函数（声明在后面）",
        "struct R {\n"
        "    void a() { b(); }\n"
        "    void b() { x = 1; }\n"
        "    u64 x;\n"
        "};\n"
        "u64 main() { R r; r.a(); return r.x; }\n",
        "() R_a(R& recv) { R_b(recv); }\n"
        "() R_b(R& recv) { recv.x = 1; }\n"
        "u64 main() { R r; R_a(r); return r.x; }\n",
    ),
    (
        "0 参构造函数 → `R r;` 也调它",
        "struct R {\n"
        "    u64 x;\n"
        "    R() : x(7) {}\n"
        "};\n"
        "u64 main() { R r; return r.x; }\n",
        "() R_R(R& recv) { recv.x = 7; }\n"
        "u64 main() { R r; R_R(r); return r.x; }\n",
    ),
    (
        "两个 struct 的同名方法（按接收者类型分派）",
        "struct A { u64 v; void set(int k) { v = k; } };\n"
        "struct B { u64 w; void set(int k) { w = k + 1; } };\n"
        "int main() { A a; B b; a.set(1); b.set(2); return 0; }\n",
        "() A_set(A& recv, i64 k) { recv.v = k; }\n"
        "() B_set(B& recv, i64 k) { recv.w = k + 1; }\n"
        "i64 main() { A a; B b; A_set(a, 1); B_set(b, 2); return 0; }\n",
    ),
    (
        "接收者是字段链 `o.inner.bump()`",
        "struct Inner { u64 v; void bump() { v = v + 1; } };\n"
        "struct Outer { Inner inner; };\n"
        "u64 main() { Outer o; o.inner.bump(); return o.inner.v; }\n",
        "() Inner_bump(Inner& recv) { recv.v = recv.v + 1; }\n"
        "u64 main() { Outer o; Inner_bump(o.inner); return o.inner.v; }\n",
    ),
    (
        "形参恰好叫 `recv`（接收者退到 `recv2`）",
        "struct R {\n"
        "    u64 v;\n"
        "    void set(int recv) { v = recv; }\n"
        "};\n"
        "u64 main() { R r; r.set(3); return r.v; }\n",
        "() R_set(R& recv2, i64 recv) { recv2.v = recv; }\n"
        "u64 main() { R r; R_set(r, 3); return r.v; }\n",
    ),
    (
        "尾随 `const` 限定符",
        "struct R {\n"
        "    u64 v;\n"
        "    u64 get() const { return v; }\n"
        "    void put(u64 k) const { v = k; }\n"
        "};\n"
        "u64 main() { R r; r.put(4); return r.get(); }\n",
        "u64 R_get(R& recv) { return recv.v; }\n"
        "() R_put(R& recv, u64 k) { recv.v = k; }\n"
        "u64 main() { R r; R_put(r, 4); return R_get(r); }\n",
    ),
    (
        "形参遮住同名字段（C++ 作用域规则）",
        "struct R {\n"
        "    u64 total;\n"
        "    u64 add(u64 total) { return total + 1; }\n"
        "};\n"
        "u64 main() { R r; return r.add(3); }\n",
        "u64 R_add(R& recv, u64 total) { return total + 1; }\n"
        "u64 main() { R r; return R_add(r, 3); }\n",
    ),
    (
        "初始化列表：容器按字段类型选 `filled`",
        "struct R {\n"
        "    std::vector<int> buf;\n"
        "    std::size_t head;\n"
        "    R(std::size_t n) : head(0), buf(n, 1) {}\n"
        "    int at0() { return buf[head]; }\n"
        "};\n"
        "int main() { R r(2); return r.at0(); }\n",
        "() R_R(R& recv, u64 n) { recv.head = 0; recv.buf = vector<i64>::filled(1, n); }\n"
        "i64 R_at0(R& recv) { return recv.buf[recv.head]; }\n"
        "i64 main() { R r; R_R(r, 2); return R_at0(r); }\n",
    ),
]


def test_member_shapes():
    """[26] 第 13 条的九种形状：降级后的 AST 与**手写**逐字节同形。"""
    print("[26] 第 13 条：成员函数 / 构造函数的各种形状 → 与手写同一棵树")
    for label, cpp, ours in _MEMBER_SHAPES:
        sa = _drop_kinds(ast_norm(cpp, True), {"struct"})
        sb = ast_norm(ours, False)
        check(sa == sb, f"26：{label} → AST 同形",
              _diff_head(_jsonl(sa), _jsonl(sb)))


#: 第 13 条**不支持**的形状：`(标签, 源码, 期望诊断码)`。
#:
#: 每条都必须"报清楚"：码对上 + 消息里有改法（`改法`）。
_MEMBER_BAD = [
    ("虚函数", "struct R {\n    u64 x;\n    virtual void f() { x = 1; }\n};\n"
               "u64 main() { return 0; }\n", "E-Compat-MemberUnsupported"),
    ("静态成员", "struct R {\n    u64 x;\n    static u64 n;\n};\n"
                 "u64 main() { return 0; }\n", "E-Compat-MemberUnsupported"),
    ("运算符重载",
     "struct R {\n    u64 x;\n"
     "    bool operator==(const R& o) { return x == o.x; }\n};\n"
     "u64 main() { return 0; }\n", "E-Compat-MemberUnsupported"),
    ("析构函数", "struct R {\n    u64 x;\n    ~R() { }\n};\n"
                 "u64 main() { return 0; }\n", "E-Compat-MemberUnsupported"),
    ("模板成员函数", "struct R {\n    u64 x;\n"
                     "    template<typename T> void f(T v) { x = 1; }\n};\n"
                     "u64 main() { return 0; }\n", "E-Compat-MemberUnsupported"),
    ("只有声明没有体", "struct R {\n    u64 x;\n    void f();\n};\n"
                       "u64 main() { return 0; }\n", "E-Compat-MemberUnsupported"),
    ("`= default`", "struct R {\n    u64 x;\n    R() = default;\n};\n"
                    "u64 main() { return 0; }\n", "E-Compat-MemberUnsupported"),
    ("`this`", "struct R {\n    u64 x;\n    void f() { this->x = 1; }\n};\n"
               "u64 main() { return 0; }\n", "E-Compat-MemberThis"),
    ("成员重载", "struct R {\n    u64 x;\n"
                 "    void f(i64 v) { x = 1; }\n    void f(f64 v) { x = 2; }\n};\n"
                 "u64 main() { return 0; }\n", "E-Compat-MemberOverload"),
    ("构造函数重载",
     "struct R {\n    u64 x;\n    R() : x(0) {}\n    R(u64 c) : x(c) {}\n};\n"
     "u64 main() { R r(3); return 0; }\n", "E-Compat-MemberOverload"),
    ("初始化列表写了非字段",
     "struct R {\n    u64 x;\n    R(u64 c) : y(c) {}\n};\n"
     "u64 main() { R r(3); return 0; }\n", "E-Compat-InitListField"),
    ("构造实参个数对不上",
     "struct R {\n    u64 x;\n    R(u64 c) : x(c) {}\n};\n"
     "u64 main() { R r(1, 2); return 0; }\n", "E-Compat-CtorArity"),
    ("嵌套 struct", "struct A { struct B { u64 z; }\n    u64 x;\n};\n"
                    "u64 main() { return 0; }\n", "E-Struct-NoNested"),
    ("继承", "struct D : B { u64 x; };\nu64 main() { return 0; }\n",
     "E-Struct-NoInheritance"),
]


def test_member_unsupported():
    """[27] 第 13 条**不支持**的形状：每条一个码 + 消息里有改法。"""
    print("[27] 第 13 条：不支持的成员形状 → 报清楚（码 + 改法）")
    root = tempfile.mkdtemp(prefix="elncompat-member-bad-")
    try:
        for label, src, code in _MEMBER_BAD:
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(src)
            rc, out, err = cli("check", p, "--compat")
            blob = out + err
            check(rc != 0, f"27：{label} → 拒绝", f"rc={rc}")
            check(code in blob, f"27：{label} → 报 {code}",
                  blob.strip().split("\n")[0])
            check("改法" in blob, f"27：{label} → 消息里有改法",
                  blob.strip().split("\n")[0][:120])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_member_default_unchanged():
    """[28] 不带 `--compat` 时：成员函数仍报**原来的** `E-Struct-BodyForm`。

    第 13 条只在开关打开时生效——默认路径的诊断文案与以前逐字节相同。
    """
    print("[28] 第 13 条：不带 `--compat` 时成员函数照旧报 E-Struct-BodyForm")
    root = tempfile.mkdtemp(prefix="elncompat-member-off-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "struct Ring {\n"
            "    std::vector<int> buf;\n"
            "    void push(int v) { buf[0] = v; }\n"
            "};\nu64 main() { return 0; }\n")
        rc, out, err = cli("check", p)
        blob = out + err
        check(rc != 0, "28：不带开关 → 拒绝", f"rc={rc}")
        check("E-Struct-BodyForm" in blob, "28：报原来的 E-Struct-BodyForm",
              blob.strip().split("\n")[0])
        check("E-Compat-MemberUnsupported" not in blob,
              "28：不出现第 13 条的码（默认路径没被改动）",
              blob.strip().split("\n")[0])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_member_cross_file():
    """[29] 第 13 条只在**同一个文件**里生效（跨 `.tu` 看不见）。

    `struct` 写在 `.tu`、调用写在 `.eln` 时，`.eln` 那个 Parser 看不见成员表，
    所以**不降级**——必须报清楚"为什么没降级 + 该怎么写"，而不是静默放过。
    """
    print("[29] 第 13 条：struct 在 .tu、调用在 .eln → 不降级，但报清楚")
    root = tempfile.mkdtemp(prefix="elncompat-member-xfile-")
    try:
        io.open(os.path.join(root, "mod.tu"), "w", encoding="utf-8",
                newline="\n").write(
            "struct Ring {\n    std::vector<int> buf;\n"
            "    void push(int v) { buf[0] = v; }\n};\n")
        io.open(os.path.join(root, "mod.eln"), "w", encoding="utf-8",
                newline="\n").write(
            "u64 main() {\n    Ring r;\n    r.push(1);\n    return 0;\n}\n")
        rc, out, err = cli("check", os.path.join(root, "mod"), "--compat")
        blob = out + err
        check(rc != 0, "29：跨文件调用成员 → 拒绝（不静默放过）", f"rc={rc}")
        check("E-Type-StructMethod" in blob, "29：报现成的 E-Type-StructMethod",
              blob.strip().split("\n")[0][:120])
        check("--compat" in blob and "Ring_push" in blob,
              "29：消息解释了 --compat 的降级边界并给出 `Ring_push(…)` 写法",
              blob.strip().split("\n")[0][:160])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [30] 第 11 条目标表

#: `static_cast<拼写>` → 本语言类型名（`compat.CAST_TARGET_TYPES` 的口径）。
_CAST_TARGET_CASES = {
    "size_t": "u64", "std::size_t": "u64", "std :: size_t": "u64",
    "long long": "i64", "long long int": "i64", "unsigned long long": "u64",
    "char": "u8", "unsigned char": "u8", "signed char": "i8",
    "int": "i32", "signed int": "i32", "unsigned": "u32", "unsigned int": "u32",
    "double": "f64", "float": "f32",
    "short": "i16", "unsigned short": "u16",
    "uint32_t": "u32", "std::uint64_t": "u64",
}

#: **故意不认**的目标：宽度随平台变 / 80 位 / 不是标量。
_CAST_TARGET_REFUSED = ("long", "unsigned long", "long int", "long double",
                        "bool", "u128", "vector<u64>")

#: `(目标, 源类型头)` → 可折。**只有"同类型 / 值不变的加宽"在这里**。
_CAST_FOLDS = [
    ("u64", "u64"), ("u64", "u8"), ("u64", "u32"),           # size_t / u64
    ("i64", "i64"), ("i64", "i8"), ("i64", "u32"),           # long long
    ("u8", "u8"), ("u32", "u8"), ("f64", "f32"),             # char / unsigned / f32→f64
]

#: `(目标, 源类型头, 理由里必须出现的词)` → 不折。
_CAST_REJECTS = [
    ("i32", "i64", "窄"),        # int：C++ 的 int 是 32 位 → i64→i32 是窄化
    ("u32", "u64", "窄"),        # unsigned：C++ 的 unsigned 是 32 位
    ("u8", "u64", "窄"),         # char：8 位目标
    ("u64", "i64", "有符号"),    # 有符号 → 无符号
    ("i64", "u64", "符号"),      # 同宽、符号不同
    ("f64", "u64", "浮点"),      # 整数 → 浮点不是逐位相同
    ("u32", "f64", "浮点"),      # 浮点 → 整数截断
    ("f32", "f64", "窄"),        # f64 → f32 丢精度
]


def test_static_cast_target_table_spec():
    """[30] 第 11 条：**目标表 + 折/不折判据**（唯一实现在 `compat.py`）。

    判据是**纯函数**，这里逐项比表（不起子进程）：

    | 目标 | 展开成 | 折？ |
    |---|---|---|
    | `size_t` / `std::size_t` | `u64` | 源是无符号整数且不更宽时（`u64`/`u8`/`u32`） |
    | `long long` | `i64` | 源是 `i8`…`i64`、或 `u32` 这类"值域被包含"的加宽 |
    | `char` / `unsigned char` | `u8` | 只有源本来就是 `u8` |
    | `int` | `i32` | **不折**：C++ 的 `int` 是 32 位，`i64` → `int` 是窄化 |
    | `unsigned` | `u32` | **不折**：`u64` → 32 位是窄化 |
    | `double` | `f64` | **不折**：整数 → 浮点不是逐位相同 |

    另外钉住"改法是真的"：`x as T` 是本语言**有定义**的截断
    （`300 as u8` = 44），所以拒绝时的改法不是空话。
    """
    print("[30] 第 11 条：目标表与折/不折判据（size_t / char / long long / int / unsigned / double）")
    import compat as _c

    for spelling, ty in _CAST_TARGET_CASES.items():
        check(_c.cast_target_type(spelling) == ty,
              f"`static_cast<{spelling}>` 展开成 `{ty}`",
              f"实际 {_c.cast_target_type(spelling)!r}")
    for spelling in _CAST_TARGET_REFUSED:
        check(_c.cast_target_type(spelling) == "",
              f"`static_cast<{spelling}>` 不在表里（宽度不猜）",
              f"实际 {_c.cast_target_type(spelling)!r}")

    for target, src in _CAST_FOLDS:
        check(_c.cast_fold_reason(target, src) is None,
              f"`{src}` → `{target}`：可折（值不变）",
              str(_c.cast_fold_reason(target, src)))
    for target, src, word in _CAST_REJECTS:
        why = _c.cast_fold_reason(target, src)
        check(why is not None and word in why,
              f"`{src}` → `{target}`：不折，理由含「{word}」",
              str(why))

    # 判据只看静态类型，不看"这个值恰好没溢出"：`u64 0` 也不折成 `char`。
    check(_c.cast_fold_reason("u8", "u64") is not None,
          "`u64 0`（值恰好没溢出）仍不折成 `char`——类型事实，不是值事实")

    # 改法是**真的**：`as` 有定义地截断/回绕。
    root = tempfile.mkdtemp(prefix="elncompat-cast-astruth-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 main() {\n    u8 b = 300 as u8;\n"
            "    i32 c = (0 - 1) as i32;\n    print(\"{} {}\\n\", b, c);\n"
            "    return 0;\n}\n")
        rc, out, err = cli("run", p)
        check(rc == 0 and out.splitlines()[:1] == ["44 -1"],
              "改法是真的：`300 as u8` = 44、`-1 as i32` = -1",
              f"rc={rc} out={out!r} {err[-120:]!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [31] 第 14 条 std::pair

def test_pair_mapping_spec():
    """[31] 第 14 条：`std::pair<A, B>` 映射到**已有**的两元组 `(A, B)`。

    先证明"靶子"真的存在（这些都用本语言拼写跑，**不依赖** `--compat`）：

    - `(A, B)` 类型 + `(a, b)` 构造 + `.0` / `.1` 位置字段；
    - `vector<(A, B)>` 可声明 / 可 `push_back` / 可下标再取位置字段；
    - **用户 struct 里叫 `first` 的字段不受影响**（配对字段改写必须按类型，
      不能按名字——这是映射安全性的关键）。

    再钉住映射表本身（`compat.PAIR_FIELD_INDEX`）与"没有等价物"的码
    （`E-Compat-NoPairEquivalent`）已登记。
    """
    print("[31] 第 14 条：`std::pair<A,B>` → 两元组 `(A,B)`（靶子 + 映射表）")
    import compat as _c

    check(_c.pair_field_index("first") == "0"
          and _c.pair_field_index("second") == "1"
          and _c.pair_field_index("third") == "",
          "`first`/`second` → 位置 `0`/`1`（其它名字不碰）",
          f"{_c.pair_field_index('first')}/{_c.pair_field_index('second')}/"
          f"{_c.pair_field_index('third')!r}")
    check(_c.PAIR_NO_EQUIV_CODE == "E-Compat-NoPairEquivalent",
          "没有等价物的码是 E-Compat-NoPairEquivalent",
          _c.PAIR_NO_EQUIV_CODE)
    check(".0" in _c.PAIR_FIX_HINT and "std::make_pair" in _c.PAIR_FIX_HINT,
          "改法文案里给出 `.0` / `.1` 与 `std::make_pair`",
          _c.PAIR_FIX_HINT[:80])

    root = tempfile.mkdtemp(prefix="elncompat-pair-target-")
    try:
        # struct **必须写在 `.tu` 里**（默认路径的 `.eln` 不接受 struct），
        # 这样测的是"用户 struct 里叫 first 的字段不被改写"，而不是 F-4。
        io.open(os.path.join(root, "mod.tu"), "w", encoding="utf-8",
                newline="\n").write("struct S { first: u64, second: u64, }\n")
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 main() {\n"
            "    (u64, u64) a = (3, 4);\n"
            "    (vector<u8>, u64) b = (\"ab\", 9);\n"
            "    vector<(u64, u64)> xs = {};\n"
            "    xs.push_back((5, 6));\n"
            "    S s = S { first: 7, second: 8 };\n"
            "    print(\"{} {} {} {}\\n\", b.0, a.0 + a.1 + xs[0].0 + xs[0].1,"
            " s.first + s.second, xs[0].0);\n"
            "    return 0;\n}\n")
        rc, out, err = cli("run", os.path.join(root, "mod"))
        check(rc == 0 and out.splitlines()[:1] == ["ab 18 15 5"],
              "两元组靶子：构造 / `.0`·`.1` / vector 元素 / struct 的 `first` 各自独立",
              f"rc={rc} out={out!r} {err[-160:]!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [32] 第 15 条 class

def _struct_pair_equiv(label, cpp_eln, ours_tu, ours_eln):
    """第 13/16 条通用成对断言：C++ 拼写（`--compat`）vs 本语言拼写。

    C++ 一侧把 `struct` / 成员 / 默认值都写在 `.eln`（就是 `.cpp` 改名的样子）；
    本语言一侧按分工写：**声明进 `.tu`、实现在 `.eln`**。四样都与手写相同：
    check 都过 / κ 逐字节相同 / run 输出逐字节相同 / `.eln` 的 AST 同形
    （去掉 `struct` 那一项——两边文件分工不同）。
    """
    root = tempfile.mkdtemp(prefix="elncompat-structpair-")
    try:
        pair = Pair(root)
        pair.write("cpp", None, cpp_eln)
        pair.write("ours", ours_tu, ours_eln)
        rc, out, err = cli("check", os.path.join(pair.cpp, "mod"), "--compat")
        check(rc == 0, f"{label}：C++ 拼写 + --compat 能过 check",
              (out + err).strip().split("\n")[0])
        rc, out, err = cli("check", os.path.join(pair.ours, "mod"))
        check(rc == 0, f"{label}：本语言拼写（不带开关）能过 check",
              (out + err).strip().split("\n")[0])
        r1, kcpp, _ = cli("cost", os.path.join(pair.cpp, "mod"), "--compat",
                          "--signature")
        r2, kours, _ = cli("cost", os.path.join(pair.ours, "mod"), "--signature")
        check(r1 == 0 and r2 == 0 and kcpp == kours,
              f"{label}：κ 逐字节相同（cost --signature 整份 JSON）",
              _diff_head(kcpp, kours))
        r3, ocpp, _ = cli("run", os.path.join(pair.cpp, "mod"), "--compat")
        r4, oours, _ = cli("run", os.path.join(pair.ours, "mod"))
        check(r3 == 0 and r4 == 0 and ocpp == oours,
              f"{label}：run 输出逐字节相同", f"{ocpp!r} vs {oours!r}")
        sa = _drop_kinds(ast_norm(cpp_eln, True), {"struct"})
        sb = ast_norm(ours_eln, False)
        check(sa == sb, f"{label}：AST 同形（去掉 struct 与行列号后逐字节）",
              _diff_head(_jsonl(sa), _jsonl(sb)))
    finally:
        shutil.rmtree(root, ignore_errors=True)


#: 同一段逻辑：一处写 `class`，一处写 `struct`（其余逐字节相同）。
_CLASS_SRC = (
    "class S {\n"
    " public:\n"
    "    u64 a;\n"
    "    u64 b;\n"
    "    S(u64 x, u64 y) : a(x), b(y) {}\n"
    "    u64 sum() const { return a + b; }\n"
    "};\n"
    "u64 main() {\n"
    "    S s(2, 3);\n"
    "    print(\"{} {}\\n\", s.sum(), s.a);\n"
    "    return 0;\n"
    "}\n")


def test_class_is_struct():
    """[32] 第 15 条：`class` 与 `struct` **完全同义**（只在 `--compat` 下）。

    本语言没有访问控制、没有默认 private，所以 `class` 不是"另一件事"——
    它走**同一条** `parse_struct` 路径（成员函数 / 构造函数的降级直接复用
    第 13 条）。等价性守卫：同一段源码只把 `class` 换成 `struct`，
    `cost --signature` 与 `run` 输出必须**逐字节相同**；`compat=False` 时
    `class` 仍报原来的 `E-Keyword-Unsupported`（默认路径一个字节不动）。
    """
    print("[32] 第 15 条：`class` = `struct`（访问说明符忽略，成员降级复用）")
    struct_src = _CLASS_SRC.replace("class S", "struct S", 1)

    check(ast_norm(_CLASS_SRC, True) == ast_norm(struct_src, True),
          "32：`class S {…}` 与 `struct S {…}` 的 AST 逐字节相同（去行列号）",
          _diff_head(_jsonl(ast_norm(_CLASS_SRC, True)),
                     _jsonl(ast_norm(struct_src, True))))

    # **类型位置**的详细类型说明符：`class S* p` 就是 `S* p`。
    _ptr_class = ("struct S { u64 x; };\n"
                  "u64 main() { S s; class S* p = &s; return p->x; }\n")
    _ptr_plain = ("struct S { u64 x; };\n"
                  "u64 main() { S s; S* p = &s; return p->x; }\n")
    check(ast_norm(_ptr_class, True) == ast_norm(_ptr_plain, True),
          "32：类型位置 `class S* p` = `S* p`", "")

    root = tempfile.mkdtemp(prefix="elncompat-class-")
    try:
        cls = os.path.join(root, "cls")
        st = os.path.join(root, "st")
        for d, text in ((cls, _CLASS_SRC), (st, struct_src)):
            os.makedirs(d, exist_ok=True)
            io.open(os.path.join(d, "mod.eln"), "w", encoding="utf-8",
                    newline="\n").write(text)
        rc, out, err = cli("check", os.path.join(cls, "mod"), "--compat")
        check(rc == 0, "32：`class` + --compat 能过 check",
              (out + err).strip().split("\n")[0])
        r1, k1, _ = cli("cost", os.path.join(cls, "mod"), "--compat",
                        "--signature")
        r2, k2, _ = cli("cost", os.path.join(st, "mod"), "--compat",
                        "--signature")
        check(r1 == 0 and r2 == 0 and k1 == k2,
              "32：`class` 与 `struct` 的 cost --signature 逐字节相同",
              _diff_head(k1, k2))
        r3, o1, _ = cli("run", os.path.join(cls, "mod"), "--compat")
        r4, o2, _ = cli("run", os.path.join(st, "mod"), "--compat")
        check(r3 == 0 and r4 == 0 and o1 == o2 == "5 2\n\n0\n",
              "32：`class` 与 `struct` 的 run 输出逐字节相同（`5 2`）",
              f"{o1!r} vs {o2!r}")

        # **默认路径不变**：不带 `--compat` 时 `class` 照旧被拒绝。
        rc, out, err = cli("check", os.path.join(cls, "mod"))
        blob = out + err
        check(rc != 0 and "E-Keyword-Unsupported" in blob,
              "32：不带开关时 `class` 仍报 E-Keyword-Unsupported",
              blob.strip().split("\n")[0][:120])
        check("E-Struct-" not in blob,
              "32：不带开关时不出现第 13/16 条的 struct 诊断（默认路径未改动）",
              blob.strip().split("\n")[0][:120])
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # `class` 的构造 + 方法，降级后与**手写**同形（第 13 条复用）。
    _struct_pair_equiv(
        "32：`class` 的构造函数 + 成员函数",
        _CLASS_SRC,
        "struct S { a: u64, b: u64, }\n",
        "() S_S(S& recv, u64 x, u64 y) { recv.a = x; recv.b = y; }\n"
        "u64 S_sum(S& recv) { return recv.a + recv.b; }\n"
        "u64 main() {\n    S s;\n    S_S(s, 2, 3);\n"
        "    print(\"{} {}\\n\", S_sum(s), s.a);\n    return 0;\n}\n")


# ---------------------------------------------------------------- [33] 第 16 条 默认值

#: 第 16 条的**形状表**：`(标签, C++ 拼写, 手写等价的本语言拼写)`。
#:
#: 与 `_MEMBER_SHAPES` 同一口径：只比 `.eln` 这一侧（`struct` 那项去掉）。
#: 合成出来的零参构造函数排在**成员函数之后**（解析顺序：先体里的成员、
#: 再补默认构造函数），所以手写那一侧也按这个顺序。
_DEFAULT_SHAPES = [
    (
        "没有构造函数：合成零参构造函数",
        "struct S {\n"
        "    u64 a = 3;\n"
        "    double v = 0.0;\n"
        "    std::vector<int> xs;\n"
        "};\n"
        "u64 main() {\n    S s;\n"
        "    print(\"{} {}\\n\", s.a, s.v);\n    return s.a;\n}\n",
        "() S_S(S& recv) {\n"
        "    recv.a = 3;\n"
        "    recv.v = 0.0;\n"
        "}\n"
        "u64 main() {\n    S s;\n    S_S(s);\n"
        "    print(\"{} {}\\n\", s.a, s.v);\n    return s.a;\n}\n",
    ),
    (
        "有构造函数：默认值在初始化列表之前，列表里的字段跳过",
        "struct S {\n"
        "    u64 a = 3;\n"
        "    u64 b = 4;\n"
        "    u64 c = 9;\n"
        "    S(u64 k, u64 m) : b(k), a(m) {}\n"
        "};\n"
        "u64 main() { S s(1, 2); return s.c * 100 + s.b * 10 + s.a; }\n",
        "() S_S(S& recv, u64 k, u64 m) {\n"
        "    recv.c = 9;\n"
        "    recv.b = k;\n"
        "    recv.a = m;\n"
        "}\n"
        "u64 main() { S s; S_S(s, 1, 2); return s.c * 100 + s.b * 10 + s.a; }\n",
    ),
    (
        "class 里的默认值（成员函数在前、合成的构造在后）",
        "class S {\n"
        "public:\n"
        "    u64 a = 3;\n"
        "    u64 sum() const { return a + 1; }\n"
        "};\n"
        "u64 main() { S s; return s.sum(); }\n",
        "u64 S_sum(S& recv) { return recv.a + 1; }\n"
        "() S_S(S& recv) { recv.a = 3; }\n"
        "u64 main() { S s; S_S(s); return S_sum(s); }\n",
    ),
    (
        "本语言字段写法带默认值（`a: u64 = 7`，compat 下同样降级）",
        "struct S { a: u64 = 7, b: f64 = 0.5, };\n"
        "u64 main() { S s; return s.a; }\n",
        "() S_S(S& recv) { recv.a = 7; recv.b = 0.5; }\n"
        "u64 main() { S s; S_S(s); return s.a; }\n",
    ),
]


def test_default_member_shapes():
    """[33] 第 16 条：字段默认值 → 构造函数体开头的赋值（与手写同形）。"""
    print("[33] 第 16 条：字段默认初始值 → 构造函数体开头的赋值")
    for label, cpp, ours in _DEFAULT_SHAPES:
        sa = _drop_kinds(ast_norm(cpp, True), {"struct"})
        sb = ast_norm(ours, False)
        check(sa == sb, f"33：{label} → AST 同形",
              _diff_head(_jsonl(sa), _jsonl(sb)))


def test_default_member_equiv():
    """[33] 第 16 条的**端到端**等价：check / κ / run 三样都与手写相同。

    `check` 是硬证据：默认值如果没变成赋值，`u64 a = 3;` 后面的读取
    只会看到零值——`run` 的 `3 0.0` 与手写的 `3 0.0` 对不上就会被抓住。
    """
    print("[33] 第 16 条：默认值的端到端等价（check / κ / run）")
    _struct_pair_equiv(
        "33：没有构造函数 → 合成零参构造函数",
        _DEFAULT_SHAPES[0][1],
        "struct S { a: u64, v: f64, xs: vector<i64>, }\n",
        _DEFAULT_SHAPES[0][2],
    )
    _struct_pair_equiv(
        "33：有构造函数 → 默认值排在初始化列表之前",
        _DEFAULT_SHAPES[1][1],
        "struct S { a: u64, b: u64, c: u64, }\n",
        _DEFAULT_SHAPES[1][2],
    )
    _struct_pair_equiv(
        "33：`class` 里的默认值",
        _DEFAULT_SHAPES[2][1],
        "struct S { a: u64, }\n",
        _DEFAULT_SHAPES[2][2],
    )


def test_default_member_rejects():
    """[33] 默认值**引用字段** → `E-Compat-DefaultMemberRef` + 改法。

    本语言没有"成员之间互相初始化"：降级后默认值是构造函数开头的赋值，
    引用另一个字段就依赖声明顺序（C++ 里同样不允许用成员初始化成员）。
    三种形状都要报清楚：引用前面的字段 / 引用后面的字段 / 引用它自己。
    """
    print("[33] 第 16 条：默认值引用字段 → E-Compat-DefaultMemberRef")
    root = tempfile.mkdtemp(prefix="elncompat-default-bad-")
    try:
        for label, src in (
            ("引用前面声明的字段",
             "struct S { u64 a = 1; u64 b = a; };\n"
             "u64 main() { S s; return s.b; }\n"),
            ("引用后面声明的字段",
             "struct S { u64 b = a; u64 a = 1; };\n"
             "u64 main() { S s; return s.b; }\n"),
            ("引用它自己",
             "struct S { u64 a = a + 1; };\n"
             "u64 main() { S s; return s.a; }\n"),
        ):
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(src)
            rc, out, err = cli("check", p, "--compat")
            blob = out + err
            check(rc != 0, f"33：{label} → 拒绝（不静默）", f"rc={rc}")
            check("E-Compat-DefaultMemberRef" in blob,
                  f"33：{label} → 报 E-Compat-DefaultMemberRef",
                  blob.strip().split("\n")[0][:120])
            check("改法" in blob, f"33：{label} → 消息里有改法",
                  blob.strip().split("\n")[0][:120])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_default_member_off_unchanged():
    """[33] 不带 `--compat` 时：字段默认值照旧报**原来的**诊断。

    * C++ 写法 `u64 a = 0;` → 原来的 `E-Struct-BodyForm`；
    * 本语言写法 `a: u64 = 0,` → 原来的 `E-Struct-DefaultMember`（文案不变）。
    两种都**不许**出现第 16 条的码（默认路径一个字节不动）。
    """
    print("[33] 第 16 条：不带 `--compat` 时默认值照旧报原来的诊断")
    root = tempfile.mkdtemp(prefix="elncompat-default-off-")
    try:
        for label, src, code in (
            ("C++ 写法", "struct S { u64 a = 0; };\n"
                         "u64 main() { return 0; }\n", "E-Struct-BodyForm"),
            ("本语言写法", "struct S { a: u64 = 0, };\n"
                           "u64 main() { return 0; }\n",
             "E-Struct-DefaultMember"),
        ):
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(src)
            rc, out, err = cli("check", p)
            blob = out + err
            check(rc != 0 and code in blob,
                  f"33：{label}（不带开关）→ 报原来的 {code}",
                  blob.strip().split("\n")[0][:120])
            check("E-Compat-DefaultMemberRef" not in blob,
                  f"33：{label}（不带开关）→ 不出现第 16 条的码", "")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [34] std:: 两档

#: C 档（**真的没有等价物**）里，用**表达式位置**就能稳定复现的名字。
#: 每个都给一段最小源码；诊断码必须仍是 `E-Compat-UnknownStd`，
#: 且消息里必须出现 `compat.STD_NO_EQUIV` 给的那句改法。
#: （`std::move` **不在**这里：2026-11-11 第 5 轮起它是 B 档"删除式"，
#: 由 `[35]` 钉等价性——见 `STD_DELETE_ALIASES`。）
_STD_NO_EQUIV_PROBES = (
    ("getenv", 'u64 main() {\n    vector<u8> v = std::getenv("X");\n'
               "    return v.size();\n}\n"),
    ("to_string", "u64 main() {\n    u64 n = 7;\n"
                  "    vector<u8> s = std::to_string(n);\n    return s.size();\n}\n"),
    ("cerr", 'u64 main() {\n    std::cerr << "x" << "\\n";\n    return 0;\n}\n'),
    ("ifstream", 'u64 main() {\n    std::ifstream in("x");\n    return 0;\n}\n'),
    ("popen", 'u64 main() {\n    std::popen("x", "r");\n    return 0;\n}\n'),
    ("regex_replace", 'u64 main() {\n    vector<u8> s = std::regex_replace("a", "a", "b");\n'
                      "    return s.size();\n}\n"),
)


def test_std_tiers():
    """[34] 第 6 条：语料里 `std::` 的**三档**分类（A 改名 / B 删除式 / C 没有）。

    依据是 `demo/corpus/` 的**实测**清点（不是凭印象）：`std::string` /
    `std::vector` / `std::size_t` / `std::printf` / 容器类型本来就走类型位置；
    表达式位置里，只有 `std::reverse` / `std::count` 有**逐个同义**的落点
    （`reverse` / `count_of`，迭代器实参由第 5 条配平），`std::move` 是
    **删除式**（本语言没有移动语义 ⇒ 它是纯删除，见 `[35]`），其余名字本语言
    **没有对应能力**（流对象 / 正则 / 进程 / 字符串转数 / 返回迭代器的算法）。

    这里钉五件事：

    1. **三档互斥**，且"已支持清单"**从表里派生**（往 `STD_EXPR_ALIASES` /
       `STD_DELETE_ALIASES` 加一个名字，清单立刻出现——不是写死的字符串）；
    2. A 档的**等价性**：`std::reverse(...)` / `std::count(...)` 与手写
       `reverse(xs)` / `count_of(xs, v)` 的 κ / run / AST 逐字节相同；
    3. B 档（删除式）在表里**恰好一个名字、一个实参**，且不在 A / C 档
       （等价性由 `[35]` 单独钉）；
    4. C 档**仍然报错**（不静默、不硬映射），且诊断里给**逐个改法**
       （`STD_NO_EQUIV` 是唯一来源）；
    5. `--compat` 关掉时这条表**根本不参与**（默认路径一个字节不动）。
    """
    print("[34] 第 6 条：`std::` A 档（有等价物）/ B 档（删除式）/ C 档（无等价物，诊断教改法）")
    import compat as _c

    # ---- ① 三档互斥 + 清单自动同步
    both = set(_c.STD_EXPR_ALIASES) & set(_c.STD_NO_EQUIV)
    check(not both, "A 档与 C 档**互斥**（同名不能既改名又无等价物）",
          f"{sorted(both)}")
    both_del = ((set(_c.STD_EXPR_ALIASES) & set(_c.STD_DELETE_ALIASES))
                | (set(_c.STD_DELETE_ALIASES) & set(_c.STD_NO_EQUIV)))
    check(not both_del, "B 档（删除式）与 A / C 档**互斥**", f"{sorted(both_del)}")
    listing = _c.std_expr_names()
    missing = [n for n in _c.STD_EXPR_ALIASES if f"`std::{n}`" not in listing]
    check(not missing, "已支持清单包含 STD_EXPR_ALIASES 里的**每一个**名字",
          f"{missing}")
    missing_del = [n for n in _c.STD_DELETE_ALIASES
                   if f"`std::{n}(" not in listing]
    check(not missing_del, "已支持清单包含 STD_DELETE_ALIASES 里的**每一个**名字",
          f"{missing_del}")
    _c.STD_EXPR_ALIASES["__eln_probe__"] = "__eln_probe__"
    try:
        probe_listing = _c.std_expr_names()
    finally:
        del _c.STD_EXPR_ALIASES["__eln_probe__"]
    check("`std::__eln_probe__`" in probe_listing,
          "已支持清单**从表里派生**（加名字就出现，不是写死的文案）",
          probe_listing[:120])
    _c.STD_DELETE_ALIASES["__eln_probe_del__"] = 1
    try:
        probe_del = _c.std_expr_names()
    finally:
        del _c.STD_DELETE_ALIASES["__eln_probe_del__"]
    check("`std::__eln_probe_del__(x)`" in probe_del,
          "删除式清单同样**从表里派生**（加名字就出现）", probe_del[:120])

    # ---- ② A 档：本轮新加的两个名字，有逐个同义的落点
    check(_c.STD_EXPR_ALIASES.get("reverse") == "reverse"
          and _c.STD_EXPR_ALIASES.get("count") == "count_of",
          "A 档：`std::reverse`→`reverse`、`std::count`→`count_of`",
          f"{_c.STD_EXPR_ALIASES.get('reverse')}/{_c.STD_EXPR_ALIASES.get('count')}")

    # ---- ③ A 档等价性：κ 整份 JSON / run stdout / AST 逐字节相同
    for key, cpp_eln, ours_eln in (
        ("reverse",
         "u64 main() {\n    vector<u64> xs = {1, 2, 3, 4};\n"
         "    std::reverse(xs.begin(), xs.end());\n"
         "    return xs[0] * 1000 + xs[3];\n}\n",
         "u64 main() {\n    vector<u64> xs = {1, 2, 3, 4};\n"
         "    reverse(xs);\n"
         "    return xs[0] * 1000 + xs[3];\n}\n"),
        ("count",
         "u64 main() {\n    vector<u64> xs = {1, 2, 1, 3, 1};\n"
         "    return std::count(xs.begin(), xs.end(), 1) * 10 + xs[0];\n}\n",
         "u64 main() {\n    vector<u64> xs = {1, 2, 1, 3, 1};\n"
         "    return count_of(xs, 1) * 10 + xs[0];\n}\n"),
    ):
        root = tempfile.mkdtemp(prefix=f"elncompat-std-{key}-")
        try:
            pair = Pair(root)
            pair.write("cpp", None, cpp_eln)
            pair.write("ours", None, ours_eln)
            rc_c, out_c, err_c = cli("check", os.path.join(pair.cpp, "mod"),
                                     "--compat")
            check(rc_c == 0, f"{key}：C++ 拼写 + --compat 能过 check",
                  (out_c + err_c).strip().split("\n")[0])
            rc1, sig_c, e1 = cli("cost", os.path.join(pair.cpp, "mod"),
                                 "--compat", "--signature")
            rc2, sig_o, e2 = cli("cost", os.path.join(pair.ours, "mod"),
                                 "--signature")
            check(rc1 == 0 and rc2 == 0 and sig_c == sig_o,
                  f"{key}：κ 逐字节相同（cost --signature 整份 JSON）",
                  _diff_head(sig_c, sig_o))
            rc3, run_c, e3 = cli("run", os.path.join(pair.cpp, "mod"), "--compat")
            rc4, run_o, e4 = cli("run", os.path.join(pair.ours, "mod"))
            check(rc3 == 0 and rc4 == 0 and run_c == run_o,
                  f"{key}：run 输出逐字节相同",
                  f"{run_c!r} vs {run_o!r}")
            sa = ast_norm(cpp_eln, True)
            sb = ast_norm(ours_eln, False)
            check(sa == sb, f"{key}：AST 同形（去掉行列号后逐字节）",
                  _diff_head(_jsonl(sa), _jsonl(sb)))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # ---- ④ C 档：逐个仍报 E-Compat-UnknownStd，且消息里有**该名字的**改法
    for name, src in _STD_NO_EQUIV_PROBES:
        root = tempfile.mkdtemp(prefix=f"elncompat-stdno-{name}-")
        try:
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(src)
            rc, out, err = cli("check", p, "--compat")
            blob = out + err
            hint = _c.std_no_equiv_hint(name)
            check(rc != 0 and "E-Compat-UnknownStd" in blob,
                  f"C 档 `std::{name}`：仍报 E-Compat-UnknownStd（不静默接受）",
                  blob.strip().split("\n")[0])
            check(bool(hint) and hint in blob,
                  f"C 档 `std::{name}`：诊断里有**它自己的**改法",
                  hint[:80])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # ---- ⑤ 类型位置另走 parser 的码（已知缺口，见注释）
    # C 档里的 `std::regex` 出现在**明确类型位置**（`const T x`）时走的是
    # parser 的 `E-Type-UnknownStdName`——那条消息在 parser 里写死，不含
    # 本表（已知缺口，等 parser 接线 `std_no_equiv_hint(name)`）。至少钉住：
    # 它**被拒绝**，而且不会静默当成用户类型名。
    root = tempfile.mkdtemp(prefix="elncompat-stdno-regex-type-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            'u64 main() {\n    const std::regex r("x");\n    return 0;\n}\n')
        rc, out, err = cli("check", p, "--compat")
        blob = out + err
        check(rc != 0 and "E-Type-UnknownStdName" in blob,
              "C 档 `std::regex` 在**类型位置**仍被拒绝（走 E-Type-UnknownStdName）",
              blob.strip().split("\n")[0])
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ---- ⑤ 默认路径：关掉 --compat 时这套表**根本不参与**
    root = tempfile.mkdtemp(prefix="elncompat-stdno-off-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 main() {\n    vector<u64> xs = {1, 2};\n"
            "    std::reverse(xs.begin(), xs.end());\n    return xs[0];\n}\n")
        rc, out, err = cli("check", p)
        blob = out + err
        check(rc != 0 and "E-Compat-UnknownStd" not in blob,
              "不带 --compat：`std::reverse` 不报 E-Compat-UnknownStd（默认路径不变）",
              blob.strip().split("\n")[0])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [35] std::move

#: `std::move(x)` 与手写 `x` 的**成对**用例：`(键, C++ 拼写, 本语言拼写)`。
#:
#: 覆盖任务要求的**每一种表达式位置**：初始化 / 赋值右侧 / 返回 / 实参，
#: 外加"包装外面还挂着后缀"（`std::move(a)[0] + 1`）——后缀链必须继续作用在
#: **里面那个表达式**上，才说明包装真的被删干净了（而不是被换成了别的节点）。
_STD_MOVE_PAIRS = (
    ("init",
     "u64 main() {\n"
     "    vector<u64> a = {1, 2};\n"
     "    vector<u64> b = std::move(a);\n"
     "    print(\"{} {}\\n\", a[0], b[0]);\n"
     "    return b[0];\n}\n",
     "u64 main() {\n"
     "    vector<u64> a = {1, 2};\n"
     "    vector<u64> b = a;\n"
     "    print(\"{} {}\\n\", a[0], b[0]);\n"
     "    return b[0];\n}\n"),
    ("assign",
     "u64 main() {\n"
     "    vector<u64> a = {3, 4};\n"
     "    vector<u64> b = {9};\n"
     "    b = std::move(a);\n"
     "    return b[1];\n}\n",
     "u64 main() {\n"
     "    vector<u64> a = {3, 4};\n"
     "    vector<u64> b = {9};\n"
     "    b = a;\n"
     "    return b[1];\n}\n"),
    ("return",
     "vector<u64> mk() {\n"
     "    vector<u64> a = {5, 6};\n"
     "    return std::move(a);\n}\n"
     "u64 main() {\n    return mk()[1];\n}\n",
     "vector<u64> mk() {\n"
     "    vector<u64> a = {5, 6};\n"
     "    return a;\n}\n"
     "u64 main() {\n    return mk()[1];\n}\n"),
    ("arg",
     "u64 take(vector<u64> v) { v[0] = 9; return v[0]; }\n"
     "u64 main() {\n"
     "    vector<u64> a = {1, 2};\n"
     "    u64 p = take(std::move(a));\n"
     "    print(\"{} {}\\n\", a[0], p);\n"
     "    return a[0];\n}\n",
     "u64 take(vector<u64> v) { v[0] = 9; return v[0]; }\n"
     "u64 main() {\n"
     "    vector<u64> a = {1, 2};\n"
     "    u64 p = take(a);\n"
     "    print(\"{} {}\\n\", a[0], p);\n"
     "    return a[0];\n}\n"),
    ("postfix",
     "u64 main() {\n"
     "    vector<u64> a = {1, 2};\n"
     "    vector<u64> b = {7};\n"
     "    b[0] = std::move(a)[0] + 1;\n"
     "    return b[0] * 10 + a[1];\n}\n",
     "u64 main() {\n"
     "    vector<u64> a = {1, 2};\n"
     "    vector<u64> b = {7};\n"
     "    b[0] = a[0] + 1;\n"
     "    return b[0] * 10 + a[1];\n}\n"),
)


def test_std_move_delete():
    """[35] 第 6 条 B 档：`std::move(x)` 降级成 **`x` 本身**（删除式，不是改名）。

    先**实测**本语言的值语义（不凭印象）：赋值与按值传参都不共享存储——
    `b = a; b[0] = 7` 之后 `a[0]` 仍是 1，`take(a)` 里改 `v[0]` 也一样。
    C++ 的 `std::move(x)` **本身不搬任何东西**（它只把 `x` 转成右值，
    真正"搬"发生在接收端的移动构造 / 移动赋值里），而本语言**没有移动
    构造**、接收端永远拷贝 ⇒ 删掉这个包装是**同语义**的。

    所以 parser 在 `--compat` 下**不产生任何节点**：`std::move(e)` 直接
    返回 `e` 的 AST（`parser._compat_std_delete`）。这条钉五件事：

    1. 值语义实测（上面那段的前提）；
    2. **等价性**：五种表达式位置的 `cost --signature`（κ 整份 JSON）与
       `run` stdout 与手写 `x` **逐字节相同**。这就是"纯删除"的证据：
       钩子若偷偷加了 `copy` / `call` 之类节点，κ 或输出必有一处不同；
    3. AST 里**没有包装节点**：去掉行列号后与手写同形，且整棵树不出现 `move`；
    4. **形状守卫**：`std::move(a, b)` / `std::move()` / 裸 `std::move` 报
       `E-Compat-StdDeleteArity`（不猜、不静默丢掉多出来的实参）；
    5. `--compat` 关掉时**行为不变**：`std::move` 照旧是 `E-Type-Assign`
       （默认路径没有被这条钩子静默接受）。

    **方向安全**（κ 只多不少）：C++ 接收端拿的是移动（O(1)），这里落成
    深拷贝（O(n)）——κ **更保守**，不会低估。它不是"更快"，只是"不用改代码"。
    """
    print("[35] `std::move` 是**删除式**降级：`std::move(x)` → `x`（B 档）")

    # ① 实测值语义：赋值 + 按值传参都是深拷贝
    root = tempfile.mkdtemp(prefix="elncompat-move-sem-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 take(vector<u64> v) { v[0] = 9; return v[0]; }\n"
            "u64 main() {\n"
            "    vector<u64> a = {1, 2, 3};\n"
            "    vector<u64> b = a;\n"
            "    b[0] = 7;\n"
            "    u64 passed = take(a);\n"
            "    print(\"{} {} {}\\n\", a[0], b[0], passed);\n"
            "    return a[0] * 100 + passed;\n"
            "}\n")
        rc, out, err = cli("run", p)
        check(rc == 0 and out.splitlines()[:1] == ["1 7 9"],
              "实测：赋值与按值传参都**不共享**存储（`a[0]` 一直是 1）",
              f"rc={rc} out={out!r} {err[-120:]!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ② 等价性：五种表达式位置，κ / run / AST 三样逐字节相同
    for key, cpp_eln, ours_eln in _STD_MOVE_PAIRS:
        root = tempfile.mkdtemp(prefix=f"elncompat-move-{key}-")
        try:
            pair = Pair(root)
            pair.write("cpp", None, cpp_eln)
            pair.write("ours", None, ours_eln)
            rc1, o1, e1 = cli("check", os.path.join(pair.cpp, "mod"), "--compat")
            check(rc1 == 0, f"{key}：`std::move(x)` 在 `--compat` 下能过 check",
                  (o1 + e1).strip().split("\n")[0])
            rc2, sig_c, _ = cli("cost", os.path.join(pair.cpp, "mod"),
                                "--compat", "--signature")
            rc3, sig_o, _ = cli("cost", os.path.join(pair.ours, "mod"),
                                "--signature")
            check(rc2 == 0 and rc3 == 0 and sig_c == sig_o,
                  f"{key}：κ 逐字节相同（`cost --signature` 整份 JSON）",
                  _diff_head(sig_c, sig_o))
            rc4, run_c, _ = cli("run", os.path.join(pair.cpp, "mod"), "--compat")
            rc5, run_o, _ = cli("run", os.path.join(pair.ours, "mod"))
            check(rc4 == 0 and rc5 == 0 and run_c == run_o,
                  f"{key}：run 输出逐字节相同（`std::move` 没改变值）",
                  f"{run_c!r} vs {run_o!r}")
            sa = ast_norm(cpp_eln, True)
            sb = ast_norm(ours_eln, False)
            check(sa == sb, f"{key}：AST 同形（去行列号后逐字节）",
                  _diff_head(_jsonl(sa), _jsonl(sb)))
            check("move" not in _jsonl(sa),
                  f"{key}：AST 里**没有任何 `move` 包装节点**",
                  _jsonl(sa)[:160])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # ③ 表：`move` 在 **B 档**（删除式），既不在 A 档也不在 C 档
    import compat as _c
    check(_c.STD_DELETE_ALIASES.get("move") == 1
          and "move" not in _c.STD_EXPR_ALIASES
          and "move" not in _c.STD_NO_EQUIV,
          "`std::move` 在 **B 档（删除式）**：不在 A 档、也不在 C 档",
          f"{_c.STD_DELETE_ALIASES.get('move')}")
    names = _c.std_expr_names()
    check("删除式" in names and "`std::move(x)`" in names,
          "E-Compat-UnknownStd 的清单**自动同步**：B 档 + `std::move(x)` 都在",
          names[-200:])

    # ④ 形状守卫：只认**恰好一个**实参
    for label, src, needle in (
        ("两个实参",
         "u64 main() {\n    vector<u64> a = {1};\n"
         "    vector<u64> b = {2};\n"
         "    vector<u64> c = std::move(a, b);\n    return c[0];\n}\n",
         "恰好 1 个实参"),
        ("零个实参",
         "u64 main() {\n    vector<u64> a = {1};\n"
         "    vector<u64> c = std::move();\n    return a[0];\n}\n",
         "恰好 1 个实参"),
        ("没有括号",
         "u64 main() {\n    auto x = std::move;\n    return 0;\n}\n",
         "必须写成"),
    ):
        root = tempfile.mkdtemp(prefix="elncompat-move-arity-")
        try:
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(src)
            rc, out, err = cli("check", p, "--compat")
            blob = out + err
            check(rc != 0 and "E-Compat-StdDeleteArity" in blob
                  and needle in blob,
                  f"`std::move` {label} → E-Compat-StdDeleteArity（报清楚、不静默）",
                  blob.strip().split("\n")[0])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # ⑤ 默认路径：关掉 --compat 时这条钩子**根本不参与**
    root = tempfile.mkdtemp(prefix="elncompat-move-off-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "u64 main() {\n    vector<u64> a = {1, 2};\n"
            "    vector<u64> b = std::move(a);\n    return b[0];\n}\n")
        rc, out, err = cli("check", p)
        blob = out + err
        check(rc != 0 and "E-Type-Assign" in blob
              and "E-Compat-StdDeleteArity" not in blob,
              "不带 --compat：`std::move` 照旧报 E-Type-Assign（没被静默接受）",
              blob.strip().split("\n")[0])
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- [37] 第 17 条

#: 每一种"必须支持"的转换说明 → `(C++ 格式串, C++ 实参表, 本语言语句)`。
#:
#: **为什么逐条列**：这张表就是"格式串翻译"的**规格**——`%c` 的落点
#: （`utf8_encode`）与"末尾 `\n` 交给 `print`"是最容易在以后被改坏的两条。
#: 注意 C++ 侧写的是**源码里的转义**（`\\n`），不是真换行。
PRINTF_PAIRS = [
    ("%d", '%d\\n', "v", 'print("{:d}", v);'),
    ("%i", '%i\\n', "v", 'print("{:d}", v);'),
    ("%u", '%u\\n', "v", 'print("{:u}", v);'),
    ("%llu", '%llu\\n', "v", 'print("{:u}", v);'),
    ("%lu", '%lu\\n', "v", 'print("{:u}", v);'),
    ("%zu", '%zu\\n', "v", 'print("{:u}", v);'),
    ("%ld", '%ld\\n', "v", 'print("{:d}", v);'),
    ("%lld", '%lld\\n', "v", 'print("{:d}", v);'),
    ("%x", '%x\\n', "v", 'print("{:x}", v);'),
    ("%X", '%X\\n', "v", 'print("{:X}", v);'),
    ("%c", '%c\\n', "v", 'print("{}", utf8_encode(v));'),
    ("%s", 'k=%s\\n', "s", 'print("k={:s}", s);'),
    ("%%", '100%%\\n', "", 'print("100%");'),
    ("字面花括号", 'a{}b\\n', "", 'print("a{{}}b");'),
    ("多个洞 + 文本", 'k=%s v=%llu\\n', "s, v", 'print("k={:s} v={:u}", s, v);'),
]


def _printf_pair(label, cpp_fmt, cpp_args, ours_stmt):
    """一对 `std::printf(...)` / 手写 `print(...)`：check / κ / run / AST 全比。"""
    call = 'std::printf("' + cpp_fmt + '"' + (", " + cpp_args if cpp_args else "") + ");"
    cpp = ("u64 main() {\n    u64 v = 42;\n    vector<u8> s = \"hi\";\n"
           "    " + call + "\n    return 0;\n}\n")
    ours = ("u64 main() {\n    u64 v = 42;\n    vector<u8> s = \"hi\";\n"
            "    " + ours_stmt + "\n    return 0;\n}\n")
    _equiv_pair(f"`{label}`", cpp, ours)


def test_printf_formats():
    """第 17 条：`%d` / `%s` / `%llu` … 逐条翻成 `{}` 洞，输出与手写一致。

    **`_equiv_pair` 同时比 check / κ / run / AST 四样**——所以"翻完之后是不是
    同一个程序"不是靠肉眼，而是逐字节比 AST。
    """
    print("[37] 第 17 条：`std::printf` 的 `%` 格式串 → 花括号格式串（逐条）")
    for label, cpp_fmt, cpp_args, ours_stmt in PRINTF_PAIRS:
        _printf_pair(label, cpp_fmt, cpp_args, ours_stmt)

    # `%f` 要**浮点实参**（给整数会被 `{:f}` 在运行期拒——C 里那本来就是 UB）
    _equiv_pair(
        "`%f`",
        'u64 main() {\n    f64 d = 3.5;\n'
        '    std::printf("d=%f\\n", d);\n    return 0;\n}\n',
        'u64 main() {\n    f64 d = 3.5;\n'
        '    print("d={:f}", d);\n    return 0;\n}\n')

    # 不以换行结尾 → `write`（`print` 会多补一个行尾）
    _equiv_pair(
        "不换行（无洞）→ `write`",
        'u64 main() {\n    std::printf("first solution:");\n'
        '    std::printf("\\n");\n    return 0;\n}\n',
        'u64 main() {\n    write("first solution:");\n'
        '    print("");\n    return 0;\n}\n')
    _equiv_pair(
        "不换行（有洞）→ `write(format(…))`",
        'u64 main() {\n    for (u64 i = 1; i < 4; i = i + 1) {\n'
        '        std::printf(" %llu", i);\n    }\n'
        '    std::printf("\\n");\n    return 0;\n}\n',
        'u64 main() {\n    for (u64 i = 1; i < 4; i = i + 1) {\n'
        '        write(format(" {:u}", i));\n    }\n'
        '    print("");\n    return 0;\n}\n')


def test_printf_diagnostics():
    """第 17 条：**改不了的格式串一律报 `E-Compat-PrintfFormat`**（不静默）。

    宽度 / 精度 / 标志是最容易"看起来改对了、其实是错值"的一类，所以逐条钉：
    每一种都要 ① 报错 ② 消息里有那个写法本身 ③ 消息里有改法/支持集。
    """
    print("[37b] 第 17 条：宽度 / 精度 / 不认识的说明符 / 个数不符 → 诊断")
    cases = [
        ("宽度", '%08llu\\n', "宽度", "改法"),
        ("精度", '%.3f\\n', "精度", "精度"),
        ("标志", '%-5s\\n', "标志", "改法"),
        ("科学计数", '%e\\n', "不是兼容层支持的转换说明", "支持"),
        ("指针", '%p\\n', "不是兼容层支持的转换说明", "支持"),
        ("宽字符", '%lc\\n', "宽字符", "改法"),
        ("末尾孤立 %", 'abc%', "孤立", "%%"),
        ("个数不符", '%d %d\\n', "转换说明", "一一对应"),
    ]
    for label, fmt, needle1, needle2 in cases:
        src = ('u64 main() {\n    u64 v = 1;\n'
               '    std::printf("' + fmt + '", v);\n    return 0;\n}\n')
        root = tempfile.mkdtemp(prefix="elncompat-printf-diag-")
        try:
            p = os.path.join(root, "mod.eln")
            io.open(p, "w", encoding="utf-8", newline="\n").write(src)
            rc, out, err = cli("check", p, "--compat")
            blob = out + err
            check(rc != 0 and "E-Compat-PrintfFormat" in blob
                  and needle1 in blob and needle2 in blob,
                  f"`{label}` → E-Compat-PrintfFormat（报清楚、给改法）",
                  blob.strip().split("\n")[0][:120])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # 非字面量格式串：`--compat` 在解析期改写，运行期拼的串**改不了**
    root = tempfile.mkdtemp(prefix="elncompat-printf-dyn-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            'u64 main() {\n    vector<u8> f = "%d\\n";\n'
            '    std::printf(f, 7);\n    return 0;\n}\n')
        rc, out, err = cli("check", p, "--compat")
        blob = out + err
        check(rc != 0 and "E-Compat-PrintfFormat" in blob
              and "字面量" in blob and "format" in blob,
              "格式串不是字面量 → E-Compat-PrintfFormat（教 `format` + `write`）",
              blob.strip().split("\n")[0][:120])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_printf_default_unchanged():
    """第 17 条只在 `--compat` 下生效：**默认路径一个字节都不动**。

    如实钉住**真实旧行为**：不带开关时 `std::printf(...)` 不会被翻译
    （它是 `std::` 之下的名字，白名单只在 `--compat` 里存在）——这里只断言
    "没有把 `%d` 翻成洞、没有开始往 stdout 打东西"，不编一个新的报错。
    """
    print("[37c] 第 17 条：不带 `--compat` 时 `std::printf` 不被翻译（默认不变）")
    root = tempfile.mkdtemp(prefix="elncompat-printf-off-")
    try:
        p = os.path.join(root, "mod.eln")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            'u64 main() {\n    std::printf("v=%d\\n", 7);\n    return 0;\n}\n')
        rc, out, err = cli("run", p)
        check(rc == 0 and out.strip().splitlines() == ["0"],
              "默认路径：`std::printf` 没有变出 `print` 的输出",
              f"rc={rc} out={out!r}")
        # 同一个源在 --compat 下**必须**打出 `v=7`
        rc2, out2, _ = cli("run", p, "--compat")
        check(rc2 == 0 and out2.startswith("v=7"),
              "--compat 下同一个源打出 `v=7`（翻译生效）", f"rc={rc2} out={out2!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_printf_spec_table():
    """`compat.PRINTF_CONV` 就是规格表：用户点名的每一种都必须在里面。

    **这条防的是"以后加一种只改了消息、没加映射"**：支持的转换说明集合只有
    `PRINTF_CONV` 一处，诊断消息里的支持集也从它派生。
    """
    print("[37d] 第 17 条：支持集 = `compat.PRINTF_CONV`（规格表逐条）")
    import compat as _c
    for conv in ("d", "i", "u", "s", "c", "x", "X", "f"):
        check(conv in _c.PRINTF_CONV, f"`%{conv}` 在支持集里",
              sorted(_c.PRINTF_CONV))
    for length in ("", "l", "ll", "z"):
        ok = True
        try:
            _c.printf_plan("%" + length + "u\n")
        except _c.PrintfFormatError:
            ok = False
        check(ok, f"长度修饰符 `{length or '（无）'}` 不影响洞",
              _c.printf_supported_text()[:60])
    plan = _c.printf_plan("%c\n")
    check(plan.wrap_byte == [True] and plan.text == "{}",
          "`%c` 的实参要包 `utf8_encode`、洞是 `{}`",
          f"{plan.text!r} {plan.wrap_byte}")
    plan = _c.printf_plan("%llu\n")
    check(plan.ends_nl and plan.text == "{:u}",
          "末尾 `\\n` 被去掉、由 `print` 补（`ends_nl=True`）", repr(plan.text))
    # 平台行尾：不走 `print` 那条路时，串里的 `\\n` 在解析期换成 os.linesep
    plan = _c.printf_plan("a\nb")
    check(not plan.ends_nl and plan.text == "a" + os.linesep + "b",
          "不换行的路径：串内 `\\n` 换成平台行尾（与 `write` 不转换行尾配套）",
          repr(plan.text))



# ------------------------------------------------ [38] 第 18 条：字面量后缀

#: 任务点名的 10 种整数字面量后缀拼写（大小写各半）。
_SUFFIX_SPELLINGS = ("U", "u", "L", "l", "LL", "ll", "UL", "ul", "ULL", "ull")

#: 一个把各种前缀 / 后缀混在一起的模块：值与后缀无关（预期输出 `8 256 10 10`）。
_SUFFIX_CPP = """u64 main() {
    u64 a = 1ULL << 3;
    u64 b = 0xFFul + 1U;
    u64 c = 0b1010U;
    u64 d = 1ll + 2LL + 3lu + 4ULL;
    print("{} {} {} {}", a, b, c, d);
    return 0;
}
"""

_SUFFIX_OURS = """u64 main() {
    u64 a = 1 << 3;
    u64 b = 0xFF + 1;
    u64 c = 0b1010;
    u64 d = 1 + 2 + 3 + 4;
    print("{} {} {} {}", a, b, c, d);
    return 0;
}
"""


def _parse_codes(text, compat_on, name="mod.eln"):
    """进程内解析一段源码，返回 `E-` 诊断码列表（默认路径钉"没变"用）。"""
    _compat.set_enabled(bool(compat_on))
    try:
        res = _parse_source(text, name)
    finally:
        _compat.set_enabled(False)
    return [d.code for d in res.diagnostics if d.code.startswith("E-")]


def test_literal_suffix_equiv():
    """第 18 条：`1ULL` / `0xFFul` / `0b1010U` → **同一个整数字面量**。

    后缀只说明 C++ 那边的宽度（`u64` / `long long`…），本语言的整数字面量
    本来就是 `u64`，所以**值由字面量本身决定，后缀不参与**。落点是 parser
    入口把 `INT` + 紧邻的 `IDENT` 合成一个 INT token，下游看到的与手写
    `1` 逐字节相同——AST 同形就是这么来的，不是"另外补一条路径"。
    """
    print("[38] 第 18 条：整数字面量后缀 = 同值字面量（AST / κ / run 逐字节）")
    for suf in _SUFFIX_SPELLINGS:
        a = ast_norm(f"u64 main() {{ return 1{suf}; }}\n", True)
        b = ast_norm("u64 main() { return 1; }\n", False)
        check(a == b, f"`1{suf}` 的 AST 与 `1` 同形",
              _diff_head(_jsonl(a), _jsonl(b)))

    # 带前缀 / 超过 i64 的值 / 多种后缀混写：**值不变**是判据
    for label, cpp_lit, ours_lit in (
            ("十六进制 + ULL", "0xFFULL", "0xFF"),
            ("十六进制 + 小写 ul", "0x10ul", "0x10"),
            ("二进制 + U", "0b1010U", "0b1010"),
            ("u64 上界 + ULL", "0xFFFFFFFFFFFFFFFFULL", "0xFFFFFFFFFFFFFFFF"),
            ("六种后缀混写", "1ll + 2LL + 3lu + 4ULL + 5llu + 6ul",
             "1 + 2 + 3 + 4 + 5 + 6")):
        a = ast_norm(f"u64 main() {{ return {cpp_lit}; }}\n", True)
        b = ast_norm(f"u64 main() {{ return {ours_lit}; }}\n", False)
        check(a == b, f"{label}：`{cpp_lit}` 与 `{ours_lit}` 同形",
              _diff_head(_jsonl(a), _jsonl(b)))

    # 整个模块：check / κ / run / AST 四样 + **输出值本身**
    _equiv_pair("后缀混合模块", _SUFFIX_CPP, _SUFFIX_OURS,
                want_run="8 256 10 10\n0\n")


def test_literal_suffix_off_unchanged():
    """第 18 条只在 `--compat` 下：不带开关时后缀照旧报语法错误。"""
    print("[38b] 第 18 条：不带 `--compat` 时 `1ULL` 仍被拒绝（默认路径不变）")
    for suf in _SUFFIX_SPELLINGS:
        codes = _parse_codes(f"u64 main() {{ return 1{suf}; }}\n", False)
        check("E-Parse-Expected" in codes,
              f"`1{suf}` 不给 `--compat` 时仍报 E-Parse-Expected", str(codes))
    # `f` / `F` 是**浮点后缀**，不是整数后缀：`1f` 在 C++ 里也非法，仍然报错。
    codes = _parse_codes("u64 main() { return 1f; }\n", True)
    check("E-Parse-Expected" in codes,
          "`1f`（f 是浮点后缀，不是整数后缀）在 --compat 下仍报错", str(codes))


# ------------------------------------------------ [39] 第 19 条：复合赋值

#: 六个运算符的混合模块：`200|7=207 → &255=207 → ^3=204 → <<2=816
#: → >>1=408 → %97=20`，预期输出 `20`。
_COMPOUND_CPP = """u64 main() {
    vector<u64> words = {0, 0, 0, 0};
    u64 i = 3;
    words[i / 64] |= 1ULL << (i & 63);
    u64 x = 200;
    x |= 7ULL;
    x &= 0xFFULL;
    x ^= 3ULL;
    x <<= 2;
    x >>= 1;
    x %= 97;
    print("{} {}", x, words[0]);
    return 0;
}
"""

_COMPOUND_OURS = """u64 main() {
    vector<u64> words = {0, 0, 0, 0};
    u64 i = 3;
    words[i / 64] = words[i / 64] | 1 << (i & 63);
    u64 x = 200;
    x = x | 7;
    x = x & 0xFF;
    x = x ^ 3;
    x = x << 2;
    x = x >> 1;
    x = x % 97;
    print("{} {}", x, words[0]);
    return 0;
}
"""


def test_compound_assign_equiv():
    """第 19 条：`x op= y` → **与 `x = x op y` 同形的 AST**。

    `|=` / `&=` / `^=` / `%=` / `<<=` / `>>=` 逐个钉；下标左侧
    （`a[i] op= m`）另钉一遍——那是 C++ 语料里最常见的形状。
    """
    print("[39] 第 19 条：复合赋值 `|= &= ^= %= <<= >>=` = 展开后的赋值")
    for op, spell in _compat.COMPOUND_ASSIGN.items():
        a = ast_norm(f"u64 f(u64 x, u64 m) {{ x {spell} m; return x; }}\n", True)
        b = ast_norm(f"u64 f(u64 x, u64 m) {{ x = x {op} m; return x; }}\n", False)
        check(a == b, f"`x {spell} m` 的 AST 与 `x = x {op} m` 同形",
              _diff_head(_jsonl(a), _jsonl(b)))
        a = ast_norm(
            f"u64 f(vector<u64> a, u64 i, u64 m) {{ a[i] {spell} m; return a[i]; }}\n",
            True)
        b = ast_norm(
            f"u64 f(vector<u64> a, u64 i, u64 m) {{ a[i] = a[i] {op} m; return a[i]; }}\n",
            False)
        check(a == b, f"`a[i] {spell} m` 的 AST 与展开同形",
              _diff_head(_jsonl(a), _jsonl(b)))

    # 三段式的步进段（`for (…; …; i >>= 1)`）走同一套降级、同样同形
    a = ast_norm(
        "u64 f(u64 n) { for (u64 i = n; i > 0; i >>= 1) { } return 0; }\n", True)
    b = ast_norm(
        "u64 f(u64 n) { for (u64 i = n; i > 0; i = i >> 1) { } return 0; }\n", False)
    check(a == b, "三段式步进段 `i >>= 1` 的 AST 与 `i = i >> 1` 同形",
          _diff_head(_jsonl(a), _jsonl(b)))

    # 整个模块（含下标左侧）：check / κ / run / AST 四样 + **输出值本身**
    _equiv_pair("六个运算符的混合模块", _COMPOUND_CPP, _COMPOUND_OURS,
                want_run="20 8\n0\n")

    # `>>=` 的词法落点是 **`>` + `>=`**（不是 `>` `>` `=`）：钉住这条，
    # 否则以后有人"顺手"把识别改成 `>` `>` `=` 会静默失效。
    from lexer import Kind as _K, lex as _lex
    toks = [t.kind for t in _lex("x >>= 1;").tokens]
    check(toks[:3] == [_K.IDENT, _K.GT, _K.GE],
          "`>>=` 在词法层是 `>` + `>=`", str(toks))


def test_compound_assign_off_unchanged():
    """第 19 条只在 `--compat` 下；`+= -= *= /=` 是**既有**语法，照旧能过。"""
    print("[39b] 第 19 条：不带 `--compat` 时新运算符仍报错；旧的四个不变")
    for spell in _compat.COMPOUND_ASSIGN.values():
        codes = _parse_codes(f"u64 f(u64 x, u64 m) {{ x {spell} m; return x; }}\n",
                             False)
        check("E-Parse-ExpectedExpr" in codes,
              f"`{spell}` 不给 `--compat` 时仍报 E-Parse-ExpectedExpr", str(codes))
    for spell in ("+=", "-=", "*=", "/="):
        codes = _parse_codes(f"u64 f(u64 x, u64 m) {{ x {spell} m; return x; }}\n",
                             False)
        check(not codes, f"`{spell}` 是既有语法，不带 --compat 也照旧能解析",
              str(codes))


def main() -> int:
    test_equivalence()
    test_default_unchanged()
    test_std_whitelist()
    test_auto_no_guess()
    test_iter_not_paired()
    test_struct_needs_strict()
    test_alias_unknown_is_warning()
    test_alias_in_body()
    test_void_ast()
    test_static_fn_prefix()
    test_static_local_still_rejected()
    test_postfix_incdec_in_expr_still_rejected()
    test_for_step_incdec_equivalence()
    test_multi_declarator_equiv()
    test_direct_init_equiv()
    test_ctor_unsupported()
    test_multi_and_ctor_default_unchanged()
    test_char_class_truth_table()
    test_char_builtin_kappa()
    test_char_std_equivalence()
    test_static_cast_folds()
    test_static_cast_rejected()
    test_cast_and_char_default_unchanged()
    test_iter_for_three_shapes()
    test_iter_for_rejects()
    test_iter_for_map_set_equiv()
    test_iter_for_map_set_shapes()
    test_iter_for_map_set_rejects()
    test_member_fn_equivalence()
    test_member_shapes()
    test_member_unsupported()
    test_member_default_unchanged()
    test_member_cross_file()
    test_static_cast_target_table_spec()
    test_pair_mapping_spec()
    test_class_is_struct()
    test_default_member_shapes()
    test_default_member_equiv()
    test_default_member_rejects()
    test_default_member_off_unchanged()
    test_std_tiers()
    test_std_move_delete()
    test_printf_formats()
    test_printf_diagnostics()
    test_printf_default_unchanged()
    test_printf_spec_table()
    test_literal_suffix_equiv()
    test_literal_suffix_off_unchanged()
    test_compound_assign_equiv()
    test_compound_assign_off_unchanged()
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
