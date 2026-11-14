# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""**C++ 拼写兼容层（P0）：只加拼写，不加含义。**

## 一句话

`--compat` 打开时，`.eln` / `.tu` 里可以照抄 **C++ 的十二种写法**；关掉时行为与
2026-11-11 之前**逐字节相同**。剥掉语法糖之后必须还是**同一个程序**：

    同一段逻辑写两遍（C++ 拼写 / 本语言拼写）
      → check 两遍：κ 逐字节相同
      → run   两遍：输出逐字节相同

做不到就说明那个"拼写"偷偷改了含义 ⇒ 退回。

## 十二条（每条都只读 `enabled()`）

| # | C++ 写法 | 落到哪 |
|---|---|---|
| 1 | `.eln` 里 `struct S { u64 a; };` | 现有 `struct` 节点（`--strict` 仍拒绝） |
| 2 | `using X = Y;` / `typedef X Y;` | 别名表（**不进 AST**），认不出只 warning |
| 3 | `auto x = expr;` | 编译期推断出的**具体类型**（推断不出报错，不猜） |
| 4 | `T{a, b}` / `vector<u64> v{1,2,3}` | 现有 `struct-literal` / `list-init` 节点 |
| 5 | `f(xs.begin(), xs.end())` | `f(xs)`（**只在配平时**） |
| 6 | `std::` 前缀白名单 | `std::vector`→`vector` … 表外报 `E-Compat-UnknownStd`（**A 档**=改名即可，**C 档**=没有等价物、诊断教改法）（见 `STD_EXPR_ALIASES` / `STD_NO_EQUIV`） |
| 7 | 返回类型写 `void` | 本语言的 `()` |
| 8 | 函数声明开头的 `static` | **跳过 token**（不产出节点、不影响 κ/AST）；局部变量仍拒绝 |
| 9 | `T a = x, b = y;`（多声明符） | 展开成**多个兄弟声明节点**，与手写多行同形 |
| 10 | `T x(args…)`（直接构造） | 单实参→拷贝初始化；`vector<T>(n, v)`→`vector<T>::filled(v, n)`；其余报 `E-Compat-CtorUnsupported` |
| 11 | `static_cast<unsigned char>(x)` / `static_cast<u64>(x)` | **只认这两种目标**，且**只在"值逐位不变"时**折成 `x`；窄化 / 有符号 / 推不出类型 → `E-Cast-Unsupported` |
| 12 | 三段式 `for` 的**唯一标准迭代器形状**：`for ([T] it = xs.begin(); it != xs.end(); ++it) { *it; it->f; }` | **降级成普通索引循环**（`it` → 新导入的 `u64 idx`：`*it`→`xs[idx]`、`it->f`→`xs[idx].f`、`xs.begin()`→`0`、`xs.end()`→`xs.size()`、`++it`→`idx = idx + 1`）。三种拼写落到**同一棵** AST，所以 κ / run 与手写索引循环必然相同；其余迭代器形状一律拒绝（见下方 `FOR_ITER_*` 与 `E-For-Iterator*`） |
| 12b | 同一个形状，接收者是 **`map` / `set`**（含 `unordered_*`） | **键快照 + 下标循环**：`vector<K> ks = m.keys();`（只取一次）→ `while (idx < ks.size())`；`it->first`→`ks[idx]`、`it->second`→`m.get(ks[idx])`、`*it`→`( ks[idx], m.get(ks[idx]) )`；`set` 只有 `*it`→`ks[idx]`（`.values()` 取快照），`it->first/second` 报错。κ = **O(n log n)**（`get` 是 O(log n)）／`unordered_map` 最坏 O(n²)——比 C++ 的 O(n) **贵一档**，见 `FOR_ITER_MAP_HEADS` 那一段 |

## 开关为什么只有这一处

**最终载体可能是 `.elnc` 后缀（用户还没定）**。所以布尔只在这里，
`dsh._main_impl` 里**一行**决定（读 `--compat`）；parser / typecheck / checker
都只读 `enabled()`，没有第二处开关。将来改成"按后缀决定"时，只改那一行，
十二条特性一行都不用动。将来加到第 13 条时同样只改实现、不改开关。

## 上表之外的后几条

第 13–17 条不是"一句话的拼写映射"，判据与实现分在几处，这里只留**指针**：

| 条 | 内容 | 判据 / 实现在哪 |
|---|---|---|
| 13 | `struct` 成员函数 / 构造函数 → 自由函数 | `parser._compat_member_call` / `_compat_build_call` |
| 14 | `std::pair` → 两元组（本文件的 `PAIR_*` 表） | parser / typecheck |
| 15 | `class` = `struct` | `parser.parse_struct` |
| 16 | 字段默认值 → 构造函数体开头的赋值 | parser |
| 17 | `std::printf` 的 `%` 格式串 → 花括号格式串 | **本文件末尾** `printf_plan()` + `parser._compat_printf` |

## 不做什么

- 不改任何**现有**语义：κ 公式、判定、解释器求值都不动；
- 不给 C++ 的**语义**开口子（别名、UB、宏、迭代器、32 位回绕……仍按本语言）；
- `compat=False` 时连诊断文案都不改（门禁与 golden 靠这一点）。
"""

from typing import Optional

import os

#: 平台行尾。**只用在"不走 `print`"的那条改写路上**（第 17 条）：`print` 走
#: Python 文本层，运行期自己会把 `\n` 转成它；`write` 走 `.buffer` 不转，
#: 所以解析期替它转。**判据不是"猜"**：C/C++ 的文本流做的正是这件事。
_OS_LINESEP = os.linesep

#: **唯一的布尔**（默认 False）。见文件头"开关为什么只有这一处"。
_ENABLED = False


def set_enabled(on: bool) -> None:
    """设置开关。**调用点只该有一个**：`dsh._main_impl`（读 `--compat`）。"""
    global _ENABLED
    _ENABLED = bool(on)


def enabled() -> bool:
    """当前是否接受 C++ 拼写。"""
    return _ENABLED


#: `std::` 之下**表达式位置**认识的名字 → 本语言的名字（第 6 条）。
#:
#: 只做**改名**：`std::sort(xs)` 与 `sort(xs)` 产出同一个 `call` 节点。
#: 语义差（`std::sort` 的比较器、`std::printf` 的 `%d` 洞）不属于"拼写"，
#: 所以**这一张表**不补。
#:
#: **例外是 `printf`**（2026-11-12 第 17 条）：它不能只改名——`%d` / `%s` /
#: `%llu` 会被原样打进输出。所以 `std::printf(fmt, …)` **在解析期**就由
#: `parser._compat_printf` + `printf_plan()`（本文件末尾）翻成**花括号格式串**，
#: 落到手写 `print("…{}…", …)` 的同一棵树。下面这条 `"printf": "print"` 只留给
#: **不跟括号**的写法（`auto f = std::printf;` 这种，没有格式串可翻）。
#:
#: **三档**（2026-11-11 第 5 轮：`std::move` 从 C 档升到 B 档）：
#:
#: - **A 档**（本表）：本语言**已有**同义的名字，改名即可。改完之后
#:   `cost --signature` 与 `run` 必须与手写本语言名字**逐字节相同**
#:   （`tests/cases/test_compat.py` 的 `[34]`）。
#: - **B 档**（`STD_DELETE_ALIASES`）：**删除式**——本语言的值语义让这个
#:   调用**没有可观察作用**，所以 `std::move(e)` 直接落成 `e` 的 AST，
#:   **一个包装节点都不产生**（κ / run / AST 与手写 `e` 逐字节相同）。
#: - **C 档**（`STD_NO_EQUIV`）：本语言**没有这个能力**（流 / 正则 / 进程 /
#:   环境变量指针 / 迭代器算法 / 字符串转数）。**不进白名单**，
#:   继续报错，由诊断给出逐个改法。
#:
#: 所以"表外"不等于"没处理"：C 档也是**明确分类过**的。
STD_EXPR_ALIASES = {
    "sort": "sort",
    "printf": "print",
    "min": "min",
    "max": "max",
    "abs": "abs",
    # **A 档扩容（2026-11-11）**：`<algorithm>` 里本语言**已有**同名同义的
    # 内建；迭代器形参由第 5 条先配平成 `f(xs)`。判据仍是"只改名"：
    # `std::reverse(xs.begin(), xs.end())` 与 `reverse(xs)` 产出**同一个**
    # `call` 节点（κ / run 逐字节相同）。
    #   返回值形状不同但**不可能被观察**：C++ 的 `std::reverse` 返回 `void`，
    #   没人能接；`count_of` 返回 `u64`、C++ 的 `std::count` 返回 `ptrdiff_t`
    #   （非负数逐个相同）。
    #   ⚠ 这两个内建本身**已弃用**（v0.73 移除，推荐手写循环）：映射语义
    #   完全相同，但 `check` 会多一条 `W-Builtin-Deprecated`。想消警告就按
    #   它给的循环写——那不改变程序。
    "reverse": "reverse",
    "count": "count_of",
    # **第 6 条的扩展（2026-11-11，循环第 3 轮）**：`<cctype>` 的字符分类。
    # 名字与语义都跟本语言的内建**逐个相同**（ASCII 口径，见 `stdlib.py`），
    # 所以这里只改名、不补语义。
    "isdigit": "isdigit",
    "isspace": "isspace",
    "isalpha": "isalpha",
    "isalnum": "isalnum",
    "tolower": "tolower",
    "toupper": "toupper",
    "isupper": "isupper",
    "islower": "islower",
}

#: **B 档：删除式**——本语言没有移动语义，这个包装**整个删掉**。
#:
#: 与 A 档的区别是"落到什么"：A 档换成一个**名字**（`call` 节点照旧），
#: B 档换成一个**表达式**（`std::move(e)` → `e` 的 AST，**一个包装节点都
#: 不产生**）。所以它不能写进 `STD_EXPR_ALIASES`——那里只回答"改叫啥"。
#:
#: 为什么 `std::move` 可以删（2026-11-11 实测，不是推测）：
#:   · 本语言**赋值 / 传参 / 返回 / 入容器全是真深拷贝**（§3.12）；
#:   · C++ 的 `std::move(x)` **本身不搬任何东西**，它只把 `x` 转成右值，
#:     真正"搬"发生在接收端的移动构造里——本语言**没有移动构造**；
#:   · 所以 `std::move(x)` 与 `x` 值相同，删掉包装是**同语义**的。
#:     C++ 里 moved-from 的状态是 *unspecified*，不读 moved-from 的程序
#:     两边行为一致；读了它的 C++ 程序本来就没有可移植的确定行为。
#:
#: 方向也是安全的（κ 只多不少）：C++ 接收端拿的是移动（O(1)），
#: 这里是深拷贝（O(n)）——κ **更保守**，不会低估。
#:
#: 值 = 允许的实参个数（**恰好**这个数；多了少了都报
#: `E-Compat-StdDeleteArity`，不猜、不静默丢掉多出来的实参）。
STD_DELETE_ALIASES = {
    "move": 1,
}

#: **C 档：真的没有等价物**（2026-11-11，按 `demo/corpus/` 逐个 `std::` 清点）。
#:
#: 判据：本语言**没有这个能力**，而且不是"改个名字"能解决的——
#:   · 环境变量：没有 `char*`，只有 `env_get` 的 `Option<vector<u8>>`；
#:   · 文本 ↔ 数：没有 `stoi` / `stoul` / `atoll` / `stod`，只有
#:     `parse_u64` / `parse_hex`（`Result` + 整串合法）与 `format`（不是 `format_u64`）；
#:   · 迭代器算法：`find` / `remove` 返回迭代器（本语言没有迭代器），改写方法/循环；
#:   · 流 / 对象：没有 `iostream` / `sstream` / `fstream` 对象，只有函数式 I/O；
#:   · 正则：**P0 有意不做**；
#:   · 文件系统：路径就是文本，没有 `std::filesystem::path`；
#:   · 进程：**L4 已定不做**（`system` / `popen`）。
#:
#: 每条 = `(类别, 改法)`。**类别用来分组**（"分流对象 / 进程 / 正则"），
#: 改法写进 `E-Compat-UnknownStd` 的诊断正文（见 `std_expr_names()`）。
#: 表里**不允许**出现 `STD_EXPR_ALIASES` / `STD_DELETE_ALIASES` 里的名字——
#: 同一个名字不能既"改名就行"（A）/ "删掉就行"（B）又"没有等价物"（C）
#: （`tests/cases/test_compat.py` 的 `[34]` 会查）。
STD_NO_EQUIV = {
    # ---- 环境变量：没有 `char*`
    "getenv": ("环境变量", "`option_unwrap_or(env_get(\"X\"), 默认)`"
                           "（返回 Option，不是 `const char*`）"),
    # ---- 数 → 文本
    "to_string": ("数 → 文本", "`format(\"{}\", x)`"
                              "（`format_u64` 只收 u64，负数会变补码大数）"),
    # ---- 文本 → 数
    "atoll": ("文本 → 数", "`parse_u64(s)`（返回 Result，且要求**整串**合法）"),
    "stoi": ("文本 → 数", "`parse_u64(s)`（返回 Result，且要求**整串**合法）"),
    "stoul": ("文本 → 数", "`parse_u64` / `parse_hex`（返回 Result；没有 base 实参）"),
    "stod": ("文本 → 浮点", "本语言**没有字符串→浮点**（用整数定点，"
                            "或 `parse_u64` 后自己拼小数）"),
    # ---- 迭代器算法：返回迭代器 / 区间成员
    "find": ("迭代器算法", "`xs.find(v)`（返回**下标**，找不到是 `npos`，不是迭代器）"),
    "remove": ("迭代器算法", "`text_replace` / `filter`（没有 `erase(first, last)`）"),
    "swap": ("值语义", "手写临时变量（`swap(xs, i, j)` 是**容器元素**交换，3 个实参）"),
    # ---- 流 / 对象
    "cerr": ("流 / 对象", "`eprintln(...)`（`<<` 链改成实参列表）"),
    "cout": ("流 / 对象", "`print(...)`（格式串用 `{}`）"),
    "cin": ("流 / 对象", "`read_stdin()` / `open_read` + `next_line`"),
    "ifstream": ("流 / 对象", "`open_read(path)` / `read_file(path)`"),
    "ofstream": ("流 / 对象", "`open_write(path)` / `write_file(path, data)`"),
    "fstream": ("流 / 对象", "`open_read` / `open_write`"),
    "istringstream": ("流 / 对象", "`text_split(s, sep)`"),
    "ostringstream": ("流 / 对象", "`vector<u8>` + `text_concat` / `+=`"),
    "stringstream": ("流 / 对象", "`text_split` + `text_concat`（没有字符串流对象）"),
    "getline": ("流 / 对象", "`next_line(r)`（Reader）/ `text_split(s, sep)`（按分隔符切）"),
    "ios": ("流 / 对象", "删掉 `std::ios::binary`（本语言的读写本来就是二进制）"),
    # ---- 正则
    "regex": ("正则", "**没有正则**：`text_find` / `text_split` / `text_replace`"),
    "sregex_iterator": ("正则", "**没有正则**：`text_find` / `text_split` / `text_replace`"),
    "regex_replace": ("正则", "`text_replace`（只能**字面量**匹配）"),
    "regex_match": ("正则", "**没有正则**：`text_find` / `text_split`"),
    "regex_search": ("正则", "**没有正则**：`text_find` / `text_split`"),
    # ---- 文件系统
    "filesystem": ("文件系统", "`cwd()` / `file_exists` / `file_size` / `fs_rename` / `fs_remove`"
                               "（路径就是文本）"),
    "u8path": ("文件系统", "路径就是文本，删掉 `u8path`"),
    "path": ("文件系统", "路径就是 `vector<u8>` 文本"),
    # ---- 进程（L4 已定不做）
    "system": ("进程", "**本语言不做进程**（L4）：让外部工具先产出文件，再 `read_file`"),
    "popen": ("进程", "**本语言不做进程**（L4）：同上，或把逻辑搬进本语言"),
    "pclose": ("进程", "**本语言不做进程**（L4）"),
}


#: 第 2 条的"认不出"判据用得到：这些名字在本语言里是**内置类型**。
#: （parser 自己有更全的一份 `BUILTIN_TYPE_NAMES`；这里只给 compat 层的
#: 别名检查用，避免 compat → parser 的循环导入。）
ALIAS_BUILTIN_HEADS = frozenset({
    "u8", "u16", "u32", "u64", "i8", "i16", "i32", "i64", "f32", "f64",
    "bool", "void",
    "vector", "array", "fixedvec", "map", "set", "optional", "expected",
    "unordered_map", "unordered_set", "stack", "queue", "deque",
    "Vec", "FixedVec", "Map", "Set", "Option", "Result", "Text",
})


def std_expr_names() -> str:
    """给 `E-Compat-UnknownStd` 的清单：**A 档（改名）+ B 档（删除式）+ C 档（没有）**。

    **为什么要在一个函数里返回三档**：parser 的消息只调这一个钩子
    （`f"· 表达式：{_compat.std_expr_names()}。"`，见 `parser.py` 的
    `E-Compat-UnknownStd` 分支）。把一个"已支持清单"扩成"已支持 + 删除式 +
    无等价物及改法"，**三档就都自动同步**：以后往 `STD_EXPR_ALIASES` /
    `STD_DELETE_ALIASES` / `STD_NO_EQUIV` 加名字，诊断里立刻有，不会漏。

    诊断正文末尾的"改法"句由 `std_expr_fix()` 按**档**分别说（parser 不再
    写死一句"换成对应的名字"——那句只对 A 档成立）。
    """
    names = " / ".join(f"`std::{n}`" for n in STD_EXPR_ALIASES)
    parts = [names]
    if STD_DELETE_ALIASES:
        parts.append("· **删除式**（B 档：本语言**没有移动语义**，"
                     "`std::X(...)` 连括号带实参**整个删掉**，直接写里面的表达式；"
                     "删完 κ / run / AST 与手写**逐字节相同**）：\n"
                     + std_delete_text())
    if STD_NO_EQUIV:
        parts.append("· **没有等价物**（C 档：本语言没有这些能力，"
                     "**别照抄成同名调用**；按 → 后的写法重写）：\n"
                     + std_no_equiv_text())
        parts.append("  （C 档**没有**对应的本语言名字；"
                     "「换成对应的名字」只对上面「表达式」那一行成立）")
    return "\n".join(parts)


def std_delete_text() -> str:
    """B 档的名字 × 形状（从 `STD_DELETE_ALIASES` 派生，不写死）。"""
    out = []
    for name, arity in STD_DELETE_ALIASES.items():
        args = ", ".join(["x"] * arity) if arity else ""
        out.append(f"  · `std::{name}({args})` → 写 `x`"
                   f"（本语言没有移动：赋值 / 传参 / 返回都是深拷贝，"
                   f"`std::{name}(x)` 与 `x` **值相同**）")
    return "\n".join(out)


def std_expr_fix() -> str:
    """`E-Compat-UnknownStd` 正文末尾的"改法"句——**按档分别说**。

    以前 parser 写死一句「把 `std::{name}` 换成上面对应的本语言名字」，
    那句只对 A 档成立；B 档要"删掉"、C 档要"按 → 后重写"。三档都在这一处
    说清，parser 只负责把它接进消息。
    """
    return ("改法：①「表达式」那行（A 档）把 `std::X` 换成对应的本语言名字"
            "——两者语义相同；\n"
            "      ②「删除式」那行（B 档）把 `std::X(...)` **整个删掉**，"
            "直接写括号里的表达式——本语言没有移动语义，值不变；\n"
            "      ③「没有等价物」那行（C 档）按 → 后的写法重写。")


def std_no_equiv_text() -> str:
    """C 档的名字 × 改法，**按类别分组**（从 `STD_NO_EQUIV` 派生，不写死）。"""
    groups: dict = {}
    for name, (cat, fix) in STD_NO_EQUIV.items():
        groups.setdefault(cat, []).append(f"`std::{name}` → {fix}")
    return "\n".join(f"  · {cat}：" + "；".join(items)
                     for cat, items in groups.items())


def std_no_equiv_hint(name: str) -> str:
    """单个 C 档名字的改法（parser 以后可按名字取；现在整表走 `std_expr_names`）。"""
    spec = STD_NO_EQUIV.get(name or "")
    return spec[1] if spec else ""


# ---------------------------------------------------------------- 第 11 条（目标表）
#
# `static_cast<T>(x)` 的**目标拼写 → 本语言类型名**。判据只有一条：
# **转换后值与转换前逐位相同**才折成 `x`；目标表只回答"这个名字是哪个类型"，
# "能不能折"由 `cast_fold_reason` 回答（两者都在本文件，判据只有一份）。
#
# **接线点**：`parser._compat_parse_static_cast`（把 `<…>` 里的拼写交给
# `cast_target_type` 展开）与 `typecheck._static_cast_fold_ok`（拿
# `cast_fold_reason` 的理由渲染诊断）。下游只做"调用 + 渲染"，判据/表在
# 本文件——以后加目标或改判据不用再动 parser / typecheck。
#
# 三个名字**故意不进表**（认不出就报错，不许猜）：
#   · `long` / `unsigned long`：宽度随平台变（LP64 是 64 位、LLP64 是 32 位）；
#   · `long double`：≥64 位，常常是 80 位扩展精度，不是 `f64`；
#   · `bool`：不是整数标量。
CAST_TARGET_TYPES = {
    # ---- 8 位
    "unsigned char": "u8", "char": "u8", "u8": "u8",
    "uint8_t": "u8", "std::uint8_t": "u8",
    "signed char": "i8", "i8": "i8", "int8_t": "i8", "std::int8_t": "i8",
    # ---- 16 位
    "unsigned short": "u16", "unsigned short int": "u16", "u16": "u16",
    "uint16_t": "u16", "std::uint16_t": "u16",
    "short": "i16", "short int": "i16", "signed short": "i16",
    "signed short int": "i16", "i16": "i16",
    "int16_t": "i16", "std::int16_t": "i16",
    # ---- 32 位
    "unsigned": "u32", "unsigned int": "u32", "u32": "u32",
    "uint32_t": "u32", "std::uint32_t": "u32",
    "int": "i32", "signed": "i32", "signed int": "i32", "i32": "i32",
    "int32_t": "i32", "std::int32_t": "i32",
    # ---- 64 位
    "unsigned long long": "u64", "unsigned long long int": "u64",
    "u64": "u64", "uint64_t": "u64", "std::uint64_t": "u64",
    "size_t": "u64", "std::size_t": "u64",
    "long long": "i64", "long long int": "i64", "i64": "i64",
    "int64_t": "i64", "std::int64_t": "i64",
    "ssize_t": "i64", "ptrdiff_t": "i64",
    # ---- 浮点
    "float": "f32", "f32": "f32",
    "double": "f64", "f64": "f64",
}

#: 位宽（判"加宽 / 窄化"用）。
CAST_WIDTH_BITS = {
    "u8": 8, "i8": 8, "u16": 16, "i16": 16, "u32": 32, "i32": 32,
    "u64": 64, "i64": 64, "f32": 32, "f64": 64,
}

#: 无符号整数 / 有符号整数 / 浮点三组（按**本语言类型名**分）。
CAST_UNSIGNED = frozenset({"u8", "u16", "u32", "u64"})
CAST_SIGNED = frozenset({"i8", "i16", "i32", "i64"})
CAST_FLOATS = frozenset({"f32", "f64"})

#: 诊断里显示用的 C++ 拼写（每个语言类型挑一个最像 C++ 的）。
CAST_TARGET_DISPLAY = {
    "u8": "unsigned char", "i8": "signed char",
    "u16": "unsigned short", "i16": "short",
    "u32": "unsigned", "i32": "int",
    "u64": "size_t", "i64": "long long",
    "f32": "float", "f64": "double",
}


def normalize_cast_target(spelling: str) -> str:
    """把 `static_cast<…>` 里写的型别归一成一个可查表的字符串。

    - 收紧 `std::`：`std :: size_t` → `std::size_t`（parser 的 token 拼接）；
    - 去掉多余空白：`long   long` → `long long`；
    - 连写（`unsigned long long`）保持原样——表里是**整个字符串**的键。
    """
    s = (spelling or "").strip()
    s = s.replace(" :: ", "::").replace(":: ", "::").replace(" ::", "::")
    return " ".join(s.split())


def cast_target_type(spelling: str) -> str:
    """目标拼写 → 本语言类型名；表外返回空串（调用方报 `E-Cast-Unsupported`）。"""
    return CAST_TARGET_TYPES.get(normalize_cast_target(spelling), "")


def cast_target_spelling(target: str) -> str:
    """本语言类型名 → 诊断里显示的 C++ 拼写（表外原样返回）。"""
    return CAST_TARGET_DISPLAY.get(target, target)


def cast_fold_reason(target: str, src_head: str,
                     src_render: str = "") -> Optional[str]:
    """`static_cast<target>(x)` 能否**逐位无损**折成 `x`。

    `target` 是**已展开的语言类型名**（`size_t` 已变成 `u64`），`src_head` 是
    实参的标量头名（`u8` … `i64`、`f32` / `f64`）。返回 `None` = 可折；
    返回字符串 = 不能折的理由（直接进诊断正文）。

    判据（第 3 轮"两种目标"的推广，**一条都没改**）：

    | 转换 | 折？ | 依据 |
    |---|---|---|
    | 同类型（`i64` → `long long`、`u8` → `char`） | ✅ | 根本没有转换 |
    | 无符号加宽（`u8` → `u64` / `size_t`） | ✅ | 零扩展，值不变 |
    | 有符号加宽（`i8` → `i64` / `long long`） | ✅ | 符号扩展，值不变 |
    | 无符号 → 更宽的有符号（`u32` → `i64`） | ✅ | 值域被包含，值不变 |
    | 有符号 → 无符号（`i64` → `u64`） | ❌ | `-1` 会变成 `2^64-1` |
    | 窄化（`u64` → `int` / `unsigned` / `char`） | ❌ | 高位被丢掉，这是**真转换** |
    | 整数 → 浮点（`u64` → `double`） | ❌ | 超过 2^53 会舍入 |
    | `f32` → `f64` | ✅ | IEEE-754 加宽精确（值不变） |
    | `f64` → `f32` | ❌ | 精度会丢 |
    | 推不出类型 / 非标量 | ❌ | **不猜**（宁可多报一条错） |

    `src_render` 只用于把源类型写进理由，可以为空。
    """
    if not src_head or src_head not in CAST_WIDTH_BITS:
        if not src_head:
            return "操作数的静态类型推不出来 ⇒ 无法证明转换前后逐位相同（本语言不猜类型）"
        return f"操作数的类型是 {src_render or src_head}，不是整数标量"
    if target == "" or target not in CAST_WIDTH_BITS:
        return f"目标 `{target}` 不在兼容层的目标表里"
    shown_src = src_render or src_head
    if target == src_head:
        return None
    tgt_float = target in CAST_FLOATS
    src_float = src_head in CAST_FLOATS
    if tgt_float and not src_float:
        return (f"目标 `{target}` 是浮点、操作数是整数 `{shown_src}`："
                f"整数 → 浮点不是逐位相同（超过 2^53 的值会被舍入），这是**真转换**")
    if src_float and not tgt_float:
        return (f"目标 `{target}` 是整数、操作数是浮点 `{shown_src}`："
                f"浮点 → 整数会截断小数部分，值变了")
    if tgt_float and src_float:
        if CAST_WIDTH_BITS[target] > CAST_WIDTH_BITS[src_head]:
            return None                       # f32 → f64：IEEE-754 加宽精确
        return (f"目标 `{target}` 比操作数的 `{shown_src}` 窄：精度会丢"
                f"（`f64` → `f32` 是**真转换**）")
    # ---- 下面两边都是整数
    tw = CAST_WIDTH_BITS[target]
    sw = CAST_WIDTH_BITS[src_head]
    if tw > sw:
        if src_head in CAST_UNSIGNED or target in CAST_SIGNED:
            return None                       # 零扩展 / 符号扩展 / 值域包含
        return (f"操作数的 `{shown_src}` 是有符号整数、目标 `{target}` 是无符号："
                f"负值会变成补码大数（`-1` → `2^{tw}-1`），值变了")
    if src_head in CAST_SIGNED and target in CAST_UNSIGNED:
        return (f"操作数的 `{shown_src}` 是有符号整数、目标 `{target}` 是无符号："
                f"负值会变成补码大数（`-1` → `2^{tw}-1`），值变了")
    if tw == sw and (src_head in CAST_UNSIGNED) != (target in CAST_UNSIGNED):
        return (f"目标 `{target}` 与操作数的 `{shown_src}` 同宽但符号不同："
                f"最高位为 1 的值会被解释成另一个数（如 `u64` 最大值 → `i64` 的 `-1`），"
                f"值变了")
    return (f"目标 `{target}` 比操作数的 `{shown_src}` 窄（或同宽但符号不同）："
            f"高位 / 符号会被丢掉（这是**真转换**，不是拼写）")


# ---------------------------------------------------------------- 第 13 条：std::pair
#
# 本语言**有**元组 `(A, B)`（`type-tuple` / `tuple`），字段是**位置**的
# `.0` / `.1`；没有 `std::pair` 类型，也没有 `first` / `second` 字段名。
# 所以映射**存在**，而且是逐位同义的：
#
#   std::pair<A, B>        → (A, B)        （type-tuple，两个类型实参）
#   std::pair<A, B>(a, b)  → (a, b)        （tuple 表达式）
#   std::make_pair(a, b)   → (a, b)
#   p.first                → p.0           （只在 p 的静态类型是二元组时）
#   p.second               → p.1
#
# **接线点**：parser（类型位置 `pair` → `type-tuple`、构造 + `make_pair` →
# `tuple`）与 typecheck（按**静态类型**把 `.first/.second` 改写成位置字段）。
# 只在 `--compat` 下生效；**不硬造新语义**：默认构造 / 三个以上实参 /
# 结构化绑定（`auto [k, v] = …`）没有等价物 → `E-Compat-NoPairEquivalent`。
PAIR_TYPE_NAME = "pair"

#: `std::pair` 的字段名 → 元组位置下标（AST 里 `.0` 的 `value` 就是 `"0"`）。
PAIR_FIELD_INDEX = {"first": "0", "second": "1"}

#: "没有等价物"的诊断码（登记在 `文档/语法清单.md` §P0）。
PAIR_NO_EQUIV_CODE = "E-Compat-NoPairEquivalent"

#: `E-Compat-NoPairEquivalent` 的改法（一句话）。
PAIR_FIX_HINT = (
    "改法：`std::pair<A, B>` 写两元组 `(A, B)`，取元素写 `.0` / `.1`；"
    "构造写 `(a, b)`（或 `std::make_pair(a, b)`）。"
    "要名字可读就声明 `struct Pair { first: A, second: B, }`。")


def pair_field_index(name: str) -> str:
    """`first` / `second` → `"0"` / `"1"`；不是 pair 字段返回空串。"""
    return PAIR_FIELD_INDEX.get(name or "", "")


# ---------------------------------------------------------------- 第 12 条
#
# 三段式 `for` 的**唯一标准迭代器形状**（2026-11-11，循环第 4 轮）。
#
#     for ([T] it = xs.begin(); it != xs.end(); ++it) { *it; it->f; }
#
# 三种拼写**都落到同一棵树**：
#
#   | # | 写法 | init |
#   |---|---|---|
#   | ① | `for (auto it = xs.begin(); …)` | 类型 `auto`（第 3 条推断成 `u64`） |
#   | ② | `for (T it = xs.begin(); …)`    | 任意类型 `T`（**被忽略**：迭代器类型不存在） |
#   | ③ | `for (it = xs.begin(); …)`      | 无声明（`it` 已在外声明） |
#
# 降级目标 —— **与手写索引循环逐字节同形**，所以 κ / run 必然相同：
#
#     it          → idx（循环开头新导入的局部 `u64`，值 0）
#     *it         → xs[idx]
#     it->f/it.f  → xs[idx].f
#     xs.begin()  → 0
#     xs.end()    → xs.size()
#     ++it / it++ → idx = idx + 1
#
# 这样**不需要真的实现迭代器对象**，也不新增任何成本节点：`while` 的推导、
# `E-While-NoBound`、⊤ + 原因全部原样生效。
#
# 其余迭代器形状**一律拒绝**（不硬撑）：
#   · 非 `it != xs.end()` 的终止条件        → `E-For-IteratorTerm`
#   · `rbegin` / `rend` / `--it`（反向）    → `E-For-IteratorReverse`
#   · 对 `it` 做算术（`it + 1` / `it += 2`）→ `E-For-IteratorArith`
#   · 体里修改容器（`xs.push_back`，C++ 里也是 UB）→ `E-For-IteratorMutate`
#   · 迭代器离开循环作用域被使用            → `E-For-IteratorEscape`
#   · 其它头形状 / 体里裸用 `it` / 体里遮蔽 `it` → `E-For-IteratorShape`
#
# 清单与判据写在 `文档/语法清单.md` §P0 兼容层第 12 条。

#: 第 12 条：降级后**新导入的下标变量**首选名（被占用时依次 `idx2` / `idx3` …）。
#:
#: 为什么不用 `it` 本身：形状③的 `it` 是**外面声明的变量**，类型不一定是 `u64`；
#: 而且让循环外继续能读到它，就等于让一个"降级后的假迭代器"逃出循环
#: （见 `E-For-IteratorEscape`）。
FOR_ITER_INDEX_NAME = "idx"

#: 第 12 条：循环体里**修改正在遍历的容器**的方法名。
#:
#: C++ 里这些调用会让 `it` 失效（未定义行为），所以本语言**直接拒绝**，
#: 而不是给一个可能悄悄错的降级。
FOR_ITER_MUTATORS = frozenset({
    "push_back", "pop_back", "push_front", "pop_front",
    "insert", "erase", "remove", "clear", "resize", "reserve",
    "append", "assign", "shrink_to_fit", "swap",
    "emplace", "emplace_back",
})

#: 第 12 条：会**重排 / 改写整段内容**的自由函数（体里出现就是同一个 UB）。
FOR_ITER_MUTATING_CALLS = frozenset({"sort", "reverse"})


# ---------------------------------------------------------------- 第 12 条 b
#
# **`map` / `set` 的三段式迭代器**（2026-11-12）——与容器索引循环分开的一条：
# 它们没有位置索引，`m[idx]` 是按**键**查（缺键还插默认值），所以不能照抄
# `recv[idx]` 的降级。降级目标改成"**键快照 + 下标循环**"：
#
#     for (auto it = m.begin(); it != m.end(); ++it) { … }
#       →
#     {
#         vector<K> ks = m.keys();          // 快照**只取一次**：O(n)
#         u64 idx = 0;
#         while (idx < ks.size()) {
#             it->first  → ks[idx]
#             it->second → m.get(ks[idx])
#             *it        → ( ks[idx], m.get(ks[idx]) )     # 二元组，.first/.second 可映
#             idx = idx + 1;
#         }
#     }
#
# `set` 只有 `*it → ks[idx]`（用 `.values()` 取快照），`it->first/second` 报错。
#
# ## 为什么快照必须在循环外
#
# ① **语义**：C++ 里迭代中改容器是 UB，快照一次就固定了"这一轮看到的键集合"；
# ② **κ 诚实**：每轮重新 `keys()` 会把 O(n log n) 写成 O(n²) 的假象——多算也
#    是错，而且会让读者以为"我们比 C++ 贵两档"。
#
# ## κ 比 C++ **贵一档**（安全方向，但必须写明）
#
# | | C++ `std::map` | 我们的降级 |
# |---|---|---|
# | 遍历一遍 | O(n) | `keys()` O(n) + 每轮 `get` O(log n) ⇒ **O(n log n)** |
# | `unordered_map` | 平均 O(n) / 最坏 O(n) | `get` 契约是最坏 O(n) ⇒ **O(n²)** |
#
# 多算比少算安全（不会低估），但**不是同一档**：文档（`语法清单.md` §P0 第 12 条）
# 与诊断消息都写清"我们的 map 迭代比 C++ 贵一档"。

#: 第 12 条 b：走"键快照"降级的容器头名（`canon` 之后）。
FOR_ITER_MAP_HEADS = frozenset({"map", "unordered_map"})
FOR_ITER_SET_HEADS = frozenset({"set", "unordered_set"})

#: 第 12 条 b：**取快照**的方法名。`set` 没有 `keys()`，元素视图叫 `values()`
#: （`typecheck.METHOD_TABLE["set"]` 与 `interp.SetVal` 两侧都只有 `values`）。
FOR_ITER_SNAPSHOT_METHOD = {"map": "keys", "set": "values"}

#: 第 12 条 b：降级时新导入的**快照变量**首选名（被占用时依次 `ks2` / `ks3` …）。
#: 与 `FOR_ITER_INDEX_NAME` 同一套避让：整份文件的标识符 + 本轮已导入的名字。
FOR_ITER_KEYS_NAME = "ks"


# ------------------------------------------------ 第 18 条：整数字面量后缀
#
# C++ 里 `1ULL` / `0xFFul` / `0b1010U` 是**最常见的拼写**（`1ULL << (i & 63)`
# 几乎每个位图/哈希程序都有）。本语言的整数字面量**已经是 u64**，后缀只说明
# C++ 那边的宽度，**不改变值**——所以这一条是纯粹的"删掉一个词"：
#
#     1ULL  →  1        1ul → 1        0xFFULL → 0xFF      0b1010U → 0b1010
#
# 落点：`parser._compat_strip_literal_suffixes`（在 `Parser.__init__` 里把
# `INT` + 紧邻的 `IDENT` 合成**一个** INT token）。合成之后下游看到的 token
# 流与手写无后缀字面量**逐字节相同**，所以 `lit-int` / `pattern-literal` /
# 降级用的标识符集合都不需要各写一次判断。
#
# **`f` / `F` 不在整数字面量表里**（这是刻意的）：`1.5f` 是**浮点**，C++ 的
# `float` 是 32 位——把 `1.5f` 当 f64 收下会在 `0.1f` 这类值上改变数值
# （float32 舍入 vs float64）。本语言没有"字面量级窄化"这层语义，所以
# `1.5f` 仍然报语法错误，不为它编一个静默的近似。`1f` 在 C++ 里本身也非法。
LITERAL_INT_SUFFIXES = frozenset({
    "u", "l", "ll",           # 1u / 1L / 1LL
    "ul", "lu", "ull", "llu",  # 1UL / 1LU / 1ULL / 1LLU（C++ 两种顺序都合法）
})

# ------------------------------------------------ 第 19 条：复合赋值运算符
#
# C++ 最常见的读-改-写拼写：`x |= m` / `x &= ~m` / `x ^= v` / `x <<= 2` /
# `x >>= 1` / `x %= n`。以前只有 `+= -= *= /=`，其余六个在 `=` 处报
# `E-Parse-ExpectedExpr`——而**一个 parse 错误会让整份文件的 κ 归零**
# （分母归零，不是少算一个函数），所以这条缺口比看起来严重。
#
# 降级：**与手写 `x = x <op> y` 同形的 AST**（`assign(place, binary(op,
# deepcopy(place), rhs))`）。判据是"剥掉语法糖之后还是同一个程序"：
# AST（去行列号）/ κ / run 输出都逐字节相同。
#
# **左侧下标只求值一次这件事**：本语言没有"左值引用"这层抽象，
# `a[f()] |= m` 降级后 `f()` 会算两次。这不是本轮新引入的偏差——既有的
# `a[f()]++` 走的是同一个 `_desugar_incdec`（同样深拷贝一次左侧），
# 所以口径一致；要"只求值一次"就得先定义左值引用，那是语义扩展，不在
# "只加拼写"的范围里。文档（`语法清单.md` §P0 第 19 条）写了这条已知边界。
#
# 为什么表里没有 `**=`：它要求左操作数只求值一次、且本语言没有对应语义，
# `parse_expr` 已单独报 `E-Op-NoCompoundPow`（见 `parser.parse_expr`）。
COMPOUND_ASSIGN = {
    # 展开后的二元运算符 → C++ 拼写（**唯一一份**，测试与文档都从这里取）
    "|": "|=",
    "&": "&=",
    "^": "^=",
    "%": "%=",
    "<<": "<<=",
    ">>": ">>=",
}


# ---------------------------------------------------------------- 第 17 条：std::printf
#
# **这道缺口是"行为一致率"上最大的一块**（2026-11-12）：`std::printf` 以前只是
# **改名**成 `print`（A 档），格式串仍按本语言的 `{}` 洞解释——于是 `%llu` /
# `%d` / `%s` 原样打进输出，同一个程序数值全对、逐字节对拍全错。
#
# 这一条把**格式串本身**也翻译过去：
#
#     std::printf("edit(%s,%s) = %llu\n", a, b, d);
#       → print("edit({:s},{:s}) = {:u}", a, b, d);          ← 末尾的 \n 交给 print
#
#     std::printf(" %llu", col);        // 不以换行结尾
#       → write(format(" {:u}", col));  ← 不换行，只能 write 原始字节
#
# ## 为什么不是"运行期解析 `%`"
#
# 格式串在 99% 的 C++ 里是**字面量**。在**解析期**就把 `%d` 换成 `{:d}`，
# 好处是三重的：
#   ① 落到的就是手写 `print("…{}…", …)` 的**同一棵 AST**（κ / run 与手写相同）；
#   ② 洞数与实参数**在编译期**就能对上（`E-Format-Arity` 那套本来就有）；
#   ③ 不新增任何运行期机制、不新增内建、不改 `print` 的语义。
# 代价：格式串**必须是字面量**（运行期拼出来的 `%` 串报 `E-Compat-PrintfFormat`，
# 消息里给 `format` + `write` 的改法——那才是运行期拼串的正路）。
#
# ## `%c` 为什么落到 `utf8_encode`
#
# C 的 `%c` 取一个 `int`、按 `unsigned char` 写出**一个字节**。本语言的文本是
# UTF-8（`vector<u8>`），没有"非 UTF-8 的单字节字符"这种值。已有内建
# `utf8_encode(cp)` 把码点编成 UTF-8 字节：
#   · `0..127`：**恰好 1 个字节，与 C 逐字节相同**（ASCII 是语料与现实的绝大多数）；
#   · `128..255`：C 写裸字节（在 UTF-8 世界里那不是合法文本），我们写该码点的
#     UTF-8 两字节。**这是已知且写明的偏差**，不为它新增内建、也不静默——
#     文档（`语法清单.md` §P0 第 17 条）与 `C++惯用法对照.md` 都写着。
#
# ## 宽度 / 精度 / 标志：**报错，不猜**
#
# `%08llu` / `%-5s` / `%.3f` 里的宽度、精度、标志**不自动映射**。理由：本语言的
# 洞写法是另一套语法（`{:08x}` / `{:<8}` / `{:.3f}`），把 `%08llu` 猜成什么
# （`{:08}`？`{:08x}`？）没有唯一答案；猜错就是"看起来正常的错值"，比报错糟得多。
# 所以统一报 `E-Compat-PrintfFormat`，消息里给出可直接照抄的花括号写法。
#
# ## 与 C++ 的**已知语义差**（写进文档，不在本轮修）
#
#   · `printf` 的**返回值**（写了多少字节）在本语言里没有对应：`print` 返回 0、
#     `write` 返回字节数——用返回值的地方行为不同；
#   · **实参求值顺序**：C++ **未指定**（g++ 从右往左），本语言从左往右。
#     `printf("%d %d\n", r.pop(), r.pop())` 两边数字会交换——这不是格式串的问题。
#   · `%s` / `%c` 的**非法 UTF-8 字节**：`format` 按 U+FFFD 解码（`write` 才写原始
#     字节），C 写原始字节。

#: 第 17 条：`std::printf` 的 `%` 格式串改写成花括号格式串的诊断码。
PRINTF_FORMAT_CODE = "E-Compat-PrintfFormat"

#: 转换说明 → 花括号洞里的说明符（`""` = 默认 `{}`）。
#:
#: | `%` | 洞 | 为什么是这个 |
#: |---|---|---|
#: | `%d` `%i` | `{:d}` | 有符号十进制；与 `{}` 同结果，但**声明了**要整数 |
#: | `%u` | `{:u}` | 无符号十进制；负值报 `E-Format-ArgType`（不静默给补码） |
#: | `%llu` `%lu` `%zu` `%ld` `%lld` … | 同上（**长度修饰符不改变洞**） | 本语言的值没有宽度 |
#: | `%x` / `%X` | `{:x}` / `{:X}` | 十六进制（`fmt` 本来就支持，**不需要新内建**） |
#: | `%s` | `{:s}` | 文本（`vector<u8>` / `string`）；数字给 `{:s}` 会报类型错 |
#: | `%f` | `{:f}` | 定点 6 位（`fmt` 的 `{:f}` 默认就是 6 位，与 C 一致） |
#: | `%c` | `{}` | 实参先包一层 `utf8_encode`（见上面的说明） |
PRINTF_CONV = {
    "d": "d", "i": "d", "u": "u",
    "x": "x", "X": "X",
    "s": "s", "f": "f",
    "c": "",
}

#: 整数转换（可以带长度修饰符 `h` / `hh` / `l` / `ll` / `z` / `j` / `t`）。
PRINTF_INT_CONV = frozenset({"d", "i", "u", "x", "X"})

#: 整数转换允许的长度修饰符。**只有"取参宽度"的含义**，本语言的值没有宽度，
#: 所以整张表落到同一个洞；表外的（`%Ld`、`%qd`）认不出就报错，不猜。
PRINTF_INT_LENGTHS = frozenset({"", "h", "hh", "l", "ll", "z", "j", "t"})

#: `%c` 的实参要包的**已有内建**（码点 → UTF-8 字节）。ASCII 与 C 逐字节相同。
PRINTF_BYTE_WRAP = "utf8_encode"

#: `E-Compat-PrintfFormat` 消息末尾的"支持集"（**从 `PRINTF_CONV` 派生**，
#: 不写死：以后往表里加一种，消息自动跟上）。
def printf_supported_text() -> str:
    """支持集的**一句话**（诊断正文用）。"""
    return ("支持的写法只有这些："
            "`%d` / `%i`（十进制）、`%u` / `%llu` / `%lu` / `%zu` / `%ld` / `%lld`"
            "（无符号十进制，长度修饰符不影响洞）、`%x` / `%X`（十六进制）、"
            "`%s`（文本）、`%c`（一个字符）、`%f`（定点 6 位）、`%%`（一个字面 `%`）。")


class PrintfFormatError(Exception):
    """`std::printf` 的格式串**改不了**（消息就是诊断正文，直接进 `ParseError`）。

    **为什么不在这里抛 `ParseError`**：`compat.py` 是纯规则表 + 纯函数，
    它不认识 token / 行列号；位置由 parser 的调用点补（同一个错误码
    `E-Compat-PrintfFormat`，位置指向**格式串字面量**，比指向 `printf` 更好排错）。
    """

    def __init__(self, msg: str):
        self.msg = msg
        super().__init__(msg)


class PrintfPlan:
    """一次 `std::printf` 的改写方案（`printf_plan()` 的返回值）。"""

    __slots__ = ("text", "wrap_byte", "ends_nl")

    def __init__(self, text: str, wrap_byte, ends_nl: bool):
        #: 改写后的花括号格式串。`ends_nl=True` 时**末尾那个 `\n` 已经去掉**
        #: （落到 `print`，行尾由它补、它按平台转）；`ends_nl=False` 时串里
        #: 的 `\n` 已经换成了 `os.linesep`（落到 `write`，它不转换行尾）。
        self.text = text
        #: 每个洞的实参要不要包 `utf8_encode`（只有 `%c` 是 True）。
        self.wrap_byte = list(wrap_byte)
        #: 原格式串是否以换行结尾 → 落到 `print`（True）还是 `write`（False）。
        self.ends_nl = ends_nl

    @property
    def holes(self) -> int:
        return len(self.wrap_byte)


def _printf_fail(spec: str, why: str, fix: str = "") -> PrintfFormatError:
    msg = f"`std::printf` 的格式串里 `{spec}` {why}"
    if fix:
        msg += "\n" + fix
    return PrintfFormatError(msg)


def printf_plan(text: str) -> PrintfPlan:
    """`%` 格式串 → 花括号格式串（第 17 条）。**改不了就抛 `PrintfFormatError`**。

    判据（每条都**只看格式串本身**，不看实参）：

    | 输入 | 结果 |
    |---|---|
    | `%d` `%i` | `{:d}` |
    | `%u` `%llu` `%lu` `%zu` `%ld` `%lld` … | `{:u}` |
    | `%x` / `%X` | `{:x}` / `{:X}` |
    | `%s` | `{:s}` |
    | `%f` / `%lf` | `{:f}` |
    | `%c` | `{}`（实参包 `utf8_encode`） |
    | `%%` | 一个字面 `%` |
    | 字面 `{` / `}` | `{{` / `}}`（**格式串里的花括号要转义**，否则会被当成洞） |
    | `%08llu` `%-5s` `%.3f`（标志/宽度/精度） | `E-Compat-PrintfFormat`（不猜） |
    | `%e` `%g` `%o` `%p` `%n` `%*d`（没见过的） | `E-Compat-PrintfFormat` |
    | 末尾孤立的 `%` | `E-Compat-PrintfFormat` |
    """
    out = []
    wrap = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        # **字面花括号必须转义**：`printf("{}")` 在 C 里就是打两个字符 `{}`，
        # 而本语言的 `print` 会把 `{}` 当洞。不转义就会从"打两个字面括号"变成
        # "读一个实参"（`E-Format-Arity` 或错值）。
        if ch == "{":
            out.append("{{")
            i += 1
            continue
        if ch == "}":
            out.append("}}")
            i += 1
            continue
        if ch != "%":
            out.append(ch)
            i += 1
            continue
        # ---- `%` 开始了
        i += 1
        if i >= n:
            raise _printf_fail(
                "%", "是**孤立**的（格式串到头了）：C++ 里这是未定义行为，"
                     "要打一个字面 `%` 必须写 `%%`",
                "改法：把末尾这个 `%` 写成 `%%`（或删掉）。")
        if text[i] == "%":
            out.append("%")
            i += 1
            continue
        spec_start = i
        # ---- 标志（`-` `+` `空格` `#` `0`）
        flags = []
        while i < n and text[i] in "-+ #0":
            flags.append(text[i])
            i += 1
        # ---- 宽度（`*` 是"运行期给宽度"，更不可能静态改写）
        width = ""
        if i < n and text[i] == "*":
            raise _printf_fail(
                f"%*{text[i + 1:i + 2]}", "用 `*` 在运行期给宽度：兼容层只做"
                "**字面量格式串**的静态改写，运行期拼宽度改不了",
                "改法：把宽度写成常量（本语言也没有 `*` 这种实参）；"
                "或者手写 `print(\"…{:8}…\", x)`，宽度用**常量**。")
        while i < n and text[i].isdigit():
            width += text[i]
            i += 1
        # ---- 精度（`%.3f` / `%.*s`）
        prec = ""
        if i < n and text[i] == ".":
            i += 1
            if i < n and text[i] == "*":
                raise _printf_fail(
                    "%*.*s", "用 `*` 在运行期给精度：兼容层只做**字面量**改写",
                    "改法：精度写成常量；本语言的写法是 `{:.2f}`。")
            _digits = ""
            while i < n and text[i].isdigit():
                _digits += text[i]
                i += 1
            prec = "." + _digits
        # ---- 长度修饰符
        length = ""
        if i + 1 < n and text[i:i + 2] in ("hh", "ll"):
            length = text[i:i + 2]
            i += 2
        elif i < n and text[i] in "hljztL":
            length = text[i]
            i += 1
        # ---- 转换说明
        if i >= n:
            raise _printf_fail(
                "%" + text[spec_start:i],
                "后面**没有转换说明**（`%` 之后只有标志 / 宽度 / 精度 / 长度修饰符）",
                "改法：补上转换说明（如 `%llu`），或写 `%%` 打一个字面 `%`。")
        conv = text[i]
        i += 1
        spec = "%" + text[spec_start:i]
        # ---- 宽度 / 精度 / 标志：**一律报错**（不猜）
        if flags or width or prec:
            bits = []
            if flags:
                bits.append("标志 `" + "".join(flags) + "`")
            if width:
                bits.append(f"宽度 `{width}`")
            if prec:
                bits.append(f"精度 `{prec}`")
            raise _printf_fail(
                spec,
                "用了" + "、".join(bits) + "——本语言的花括号格式串是**另一套**写法"
                "（`{:08x}` / `{:<8}` / `{:.3f}`），兼容层**不替你把 `%` 连宽度一起猜**"
                "（猜错就是一个看起来正常的错值）",
                "改法：把这一处手写成花括号洞，别再写 `%` —— 例如 "
                "`print(\"…{:08}…\", x)` / `print(\"…{:<8}…\", s)` / "
                "`print(\"…{:.3f}…\", v)`；"
                "宽度/零填充/对齐/精度的完整写法见 `文档/内建函数.md`「花括号格式串」。")
        # ---- 转换说明本身
        if conv not in PRINTF_CONV:
            raise _printf_fail(
                spec, "不是兼容层支持的转换说明",
                "改法：" + printf_supported_text() +
                "\n      没有等价物的（`%e` / `%g` / `%o` / `%p` / `%n`）请按数值形态"
                "手写 `{}` / `{:x}` / `{:f}`；指针 / 进程 / 流不在本语言里。")
        if conv in PRINTF_INT_CONV:
            if length not in PRINTF_INT_LENGTHS:
                raise _printf_fail(
                    spec, f"的长度修饰符 `{length}` 不认识（支持的只有 "
                    "`h` / `hh` / `l` / `ll` / `z` / `j` / `t`）",
                    "改法：整数转换直接写 `%d` / `%llu` 即可——本语言的值没有宽度，"
                    "长度修饰符本来就不改变洞。")
        elif conv == "f":
            if length not in ("", "l"):
                raise _printf_fail(
                    spec, f"的长度修饰符 `{length}` 不支持（`%Lf` 是 `long double`，"
                    "本语言没有这个类型）",
                    "改法：写 `%f`（本语言的 `f64` 就是 `double`）。")
        elif length:
            # `%lc` / `%ls` 是**宽字符**版本：本语言的文本是 UTF-8 字节，没有宽字符。
            if length == "L":
                raise _printf_fail(
                    spec, "的长度修饰符 `L` 是 `long double`：本语言没有这个类型"
                    "（浮点只有 `f32` / `f64`）",
                    "改法：写 `%f`（本语言的 `f64` 就是 `double`）。")
            raise _printf_fail(
                spec, f"的长度修饰符 `{length}` 是**宽字符**写法（`wchar_t`），"
                "本语言的文本是 UTF-8 字节（`vector<u8>`），没有宽字符",
                "改法：`%lc` → `%c`、`%ls` → `%s`（UTF-8 文本直接按字节写）。")
        kind = PRINTF_CONV[conv]
        out.append("{" + (":" + kind if kind else "") + "}")
        wrap.append(conv == "c")
    converted = "".join(out)
    # ---- 行尾：**最后那个 `\n` 交给 `print`，其余的自己转成平台行尾**
    #
    # `print` 一定补**一个** LF，而且它走的是 Python 的**文本层**——会把 `\n`
    # 按平台转成 `os.linesep`（Windows 上是 `\r\n`），这正是 C/C++ **文本流**
    # 的行为（g++ 在 Windows 上 `printf("\n")` 写的就是 `\r\n`）。
    # `write` 走 `.buffer`，**不转换行尾**。
    #
    # 所以：
    #   · 以 `\n` 结尾 → 去掉末尾那个 `\n`，落 `print`（行尾由它转）；
    #   · 完全不换行   → 落 `write`，没有行尾要转；
    #   · 中间有 `\n`  → 落 `write`，**在解析期把它换成 `os.linesep`**
    #     （`print` 在运行期做的正是这件事；解析发生在哪台机器，就按哪台机器的
    #     文本流走——与 C 的文本流同一个判据，不是"猜一个格式"）。
    ends_nl = converted.endswith("\n")
    if ends_nl:
        converted = converted[:-1]
    elif "\n" in converted:
        converted = converted.replace("\n", _OS_LINESEP)
    return PrintfPlan(converted, wrap, ends_nl)

