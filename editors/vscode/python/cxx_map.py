#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""cxx_map.py —— Elench → C++ 的**映射表**（v0.71，后端第一批）

## 这不是代码生成器

`elench export` 导的是**成本数据**；仓库里**没有 C++ 后端**。
这张表是后端的第一块砖：**类型怎么对应、内建怎么对应、成本口径怎么写进注释**。

## 三条纪律

1. **每个内建都必须有交代**：要么给映射，要么在 `UNMAPPED` 里写明原因——
   否则测试红（`test_cxx_map.py` 会逐个查）。
2. **成本口径跟着映射走**：`std::unordered_map` 的平均 `O(1)` **不等于**我们的上界承诺，
   所以每条映射都带 `cost_note`，生成器 MUST 把它写进注释。
3. **不映射的不许静默**：`UNMAPPED` 里的名字被调用时，生成器必须报错（`E-Cxx-Unmapped`）。

## 类型层（2026-11-13，L8）

以前 `cxx_type()` 只会查表：22 条字面量，覆盖标量 + **一层** `vector<T>`。
语料里 `vector<vector<u8>>` / `map<vector<u8>, i64>` / 用户 struct / `f64`
全部认不出，被 `dsh.py` 的 `or "uint64_t"` 静默兜底。

现在 `cxx_type()` 先查字面量表（**旧值一个不变**，旧写法仍认），再按
`名字<参数, …>` **递归**解析；认不出返回 `None`，`unmapped_reason()` 给出
具体原因（未知名字 / 参数个数 / 尖括号不配平 / 故意不映射）。
原则：**宁可漏（None/报错），也不要猜错（原样输出或退化成 uint64_t）**。
"""

#: Elench 类型 → C++ 类型
#:
#: **这张表只放"字面量"映射**（含旧写法），`vector<vector<T>>` / `map<K,V>` 这类
#: 带参数的形状由下面的 `_render_type()` **递归**生成，不在这里逐个枚举——
#: 枚举两层就会漏三层，而语料里正好有 `vector<vector<u8>>`。
TYPE_MAP = {
    # ---- 标量：`typecheck.NUMERIC` / `FLOATS` / `bool` / `void` 的全部宽度。
    # 原有 5 条（u64/u32/i64/u8/bool）**保持原顺序、原值**；下面 7 条是
    # 2026-11-13（L8）新增：以前只有 u8/u32/u64/i64/bool，`f64` 在语料里出现
    # 9 次、`vector<f64>` 7 次，全部落进 `dsh.py` 的 `or "uint64_t"` 兜底
    # （浮点被静默当整数）。
    "u64": "uint64_t",
    "u32": "uint32_t",
    "i64": "int64_t",
    "u8": "uint8_t",
    "bool": "bool",
    "u16": "uint16_t",
    "i8": "int8_t",
    "i16": "int16_t",
    "i32": "int32_t",
    "f32": "float",
    "f64": "double",
    "void": "void",
    # ---- 字符串族：**不猜**。
    # 本语言的 `string` 是 `Text` 的别名，parser 已把它归一成 `vector<u8>`
    # （`parser.py:_string_type_node` 的注释写明了运行时形态）。所以：
    #   · `vector<u8>` 按字节序列映射成 `std::vector<uint8_t>`（不是 std::string）；
    #   · 直接传 `string` 进来时也给同一个结果，与 parser 归一后的 AST 保持一致。
    # "当字符串用"的语义（拼接 / 查找 / 编码）属于表达式层，不在类型层猜。
    "string": "std::vector<uint8_t>",
    # **改名期两套写法都要能映射**：语料已翻成 `vector<…>`，
    # 而旧数据里还有 `Vec<…>`。两条并存的成本是几十字节，
    # 收益是不会因为"声明用新名"就生成不出 C++。
    "vector<u8>": "std::vector<uint8_t>",
    "vector<u64>": "std::vector<uint64_t>",
    "vector<i64>": "std::vector<int64_t>",
    "vector<u32>": "std::vector<uint32_t>",
    "Vec<u8>": "std::vector<uint8_t>",
    "Vec<u64>": "std::vector<uint64_t>",
    "Vec<i64>": "std::vector<int64_t>",
    "Vec<u32>": "std::vector<uint32_t>",
    # **有序映射/集合**（B2 的口径）：成本 O(log n) 确定、顺序 = 键序。
    # 以前这里映射到 `std::unordered_map` —— 那是**哈希**，最坏 O(n) 且顺序不可复现，
    # 和我们的上界承诺冲突（映射表自己都写了这条 cost_note）。
    "map<K,V>": "std::map<K, V>",
    "set<T>": "std::set<T>",
    "Map<K,V>": "std::map<K, V>",
    "Map<u64,u64>": "std::map<uint64_t, uint64_t>",
    "Set<T>": "std::set<T>",
    "Set<u64>": "std::set<uint64_t>",
    # `Deque<T>` 未实现（语言层没有这个名字的语义），留着映射只会误导。

    "Split": "std::vector<std::vector<uint8_t>>",
    "Option": "std::optional<T>",
    "Result": "tl::expected<T, E>",
}

#: 内建 → (C++ 表达式模板, 成本注记)
BUILTIN_MAP = {
    "min": ("std::min(a, b)", "O(1)"),
    "max": ("std::max(a, b)", "O(1)"),
    "print": ("std::cout << ...", "O(1) 操作；I/O 时间不在上界里"),
    "sort": ("std::sort(v.begin(), v.end())", "O(n log n)"),
    "push_heap": ("std::push_heap(v.begin(), v.end())", "O(log n)"),
    "pop_heap": ("std::pop_heap(v.begin(), v.end()); v.pop_back()", "O(log n)"),
    "sort_by": ("std::sort(v.begin(), v.end(), cmp)", "O(n log n)"),
    # **兼容层第 6 条（2026-11-11）的 A 档目标**：`--compat` 把
    # `std::reverse` → `reverse`、`std::count` → `count_of`（迭代器实参先由
    # 第 5 条配平）。两个名字在语言侧**已弃用**（推荐手写循环），但映射
    # 语义相同；以后若从语言里移除它们，这两条 compat 映射要一起改。
    "reverse": ("std::reverse(v.begin(), v.end())", "O(n)"),
    "fill": ("std::fill(v.begin(), v.end(), x)", "O(n)"),
    "contains": ("std::find(...) != end", "O(n)"),
    "count_of": ("std::count(...)", "O(n)"),
    "index_of": ("std::find(...) - begin", "O(n)"),
    "binary_search": ("std::binary_search(...)", "O(log n)"),
    "is_sorted": ("std::is_sorted(...)", "O(n)"),
    "sum_of": ("std::accumulate(...)", "O(n)"),
    "slice": ("std::vector<T>(v.begin()+i, v.begin()+j)", "O(n)；分配 1 次"),
    "rotate": ("std::rotate(...)", "O(n)"),
    "fill_range": ("std::iota(...)", "O(n)"),
    "dedup": ("std::unique(...)", "O(n)"),
    "hmap_put": ("/* 自实现：有界 8 次探测 */", "O(1) 真上界（探测有上限）"),
    "hmap_get": ("/* 自实现：有界 8 次探测 */", "O(1)；未命中 -1"),
    "hmap_has": ("/* 自实现 */", "O(1)"),
    "map_new": ("std::unordered_map<K,V>{}", "O(1)；分配 1 次"),
    "map_put": ("m[k] = v", "摊还 O(1)：rehash 那次 O(n)——**与我们的上界不同，注释必须写清**"),
    "map_get": ("m.find(k)", "摊还 O(1)"),
    "map_has": ("m.count(k)", "摊还 O(1)"),
    "map_len": ("m.size()", "O(1)"),
    "set_new": ("std::unordered_set<T>{}", "O(1)；分配 1 次"),
    "set_add": ("s.insert(x)", "摊还 O(1)"),
    "set_has": ("s.count(x)", "摊还 O(1)"),
    "set_len": ("s.size()", "O(1)"),
    "deque_new": ("std::deque<T>{}", "O(1)；分配 1 次"),
    "deque_push_back": ("q.push_back(x)", "摊还 O(1)"),
    "deque_push_front": ("q.push_front(x)", "摊还 O(1)"),
    "deque_pop_front": ("q.front(); q.pop_front()", "摊还 O(1)"),
    "deque_pop_back": ("q.back(); q.pop_back()", "摊还 O(1)"),
    "deque_len": ("q.size()", "O(1)"),
    "rand_u64": ("/* std::mt19937_64 + ELENCH_SEED */", "O(1)"),
    "read_file": ("std::ifstream + 读全部", "ext(...)：I/O 时间静态不可知"),
    "try_read_file": ("std::ifstream + std::optional", "ext(...)"),
    "write_file": ("std::ofstream", "ext(...)"),
    "append_file": ("std::ofstream(app)", "ext(...)"),
    "file_exists": ("std::filesystem::exists", "ext(...)"),
    "file_size": ("std::filesystem::file_size", "ext(...)"),
    "read_stdin": ("std::cin", "ext(...)"),
    "parse_u64": ("std::from_chars", "O(n)"),
    "parse_hex": ("std::from_chars(..., 16)", "O(n)"),
    "format_u64": ("std::to_string", "O(1)（≤20 字节）"),
    "utf8_len": ("/* 逐字节数**起始字节** */", "O(n)"),
    "utf8_encode": ("/* 手工 4 分支 */", "O(1)"),
    "utf8_codepoint_at": ("/* 解码一个码点 */", "O(1)"),
    "utf8_char_at": ("/* 按字符索引 */", "O(n)（要扫到第 i 个字符）"),
    "utf8_byte_of_char": ("/* 同上 */", "O(n)"),
    "text_find": ("s.find(needle)", "O(n×m)"),
    "text_trim": ("/* 两端空白 */", "O(n)"),
    "text_to_lower": ("/* ASCII 小写 */", "O(n)"),
    "text_split": ("std::vector<std::vector<uint8_t>>", "O(n)"),
    "split_len": ("parts.size()", "O(1)"),
    "split_get": ("parts[i]", "O(1)"),
    "text_split_vec": ("std::vector<std::vector<uint8_t>>", "O(n)；分配 1 + n(外层)"),
    "text_join_vec": ("/* join */", "O(n)"),
    "open_read": ("std::ifstream", "ext(open_read)；分配 1 次"),
    "next_line": ("std::getline", "1 + n(ret)：这次读了多少字节"),
    "close": ("r.close()", "O(1)"),
    "open_write": ("std::ofstream", "ext(open_write)；分配 1 次"),
    "write_chunk": ("w.write(...)", "O(n)"),
    "close_write": ("w.close()", "O(1)"),
    "text_join": ("/* join */", "O(n)"),
    "text_replace": ("/* 循环 replace */", "O(n×m)"),
    "text_concat": ("a + b", "O(n)；分配 1 次"),
    "base64_encode": ("/* 自实现 */", "O(n)"),
    "base64_decode": ("/* 自实现 */", "O(n)"),
    "json_emit_str": ("/* 转义 + 引号 */", "O(n)"),
    "json_emit_u64": ("std::to_string", "O(1)"),
    "json_emit_bool": ("true / false 字面量", "O(1)"),
    "copy": ("std::vector<T>(v)", "O(n)；分配 1 次"),
    "copy_slice": ("std::vector<T>(v.begin()+lo, v.begin()+hi)", "O(n)；越界返回 Err(Range::OutOfBounds)"),
    "args": ("std::vector<std::string>(argv + 1, argv + argc)", "O(n)；分配 1 次"),
    "args_len": ("args.size()", "O(1)"),
    "args_get": ("args[i]", "O(1)；越界返回空"),
    "env_get": ("std::getenv(name)", "O(1)；不存在返回 Option::None"),
    "cwd": ("std::filesystem::current_path()", "O(1)"),
    "exit": ("std::exit(code)", "控制流；不影响成本上界"),
    "write": ("std::cout << data", "O(1)；I/O 时间不在上界里"),
    # ---- stderr 输出（2026-11-11）：C++ 侧就是 `std::cerr << …` 链。
    # `<<` 是**无分隔符**拼接，`eprintln` 的换行是显式的一个 `"\n"`（同 C++）。
    "eprint": ("std::cerr << ...", "O(1) 操作；I/O 时间不在上界里；实参无分隔符拼接"),
    "eprintln": ("std::cerr << ... << \"\\n\"", "O(1) 操作；I/O 时间不在上界里"),
    "option_some": ("std::optional<T>{v}", "O(1)"),
    "option_none": ("std::nullopt", "O(1)"),
    "option_is_some": ("o.has_value()", "O(1)"),
    "vec_push": ("v.push_back(x)", "摊还 O(1)；分配可能发生"),
    "vec_pop": ("v.back(); v.pop_back()", "O(1)"),
    "option_unwrap_or": ("o.value_or(d)", "O(1)"),
    "option_unwrap": ("*o /* None 抛异常 */", "O(1)；None 是错误，必须处理"),
    "format": ("/* 逐段拼接 */", "O(1 + sum(实参长度))"),
    "json_parse": ("/* 需递归类型；C++ 侧用 variant + shared_ptr */", "O(n(text))"),
    "json_array": ("/* 拼接 */", "O(n)"),
    "json_object": ("/* 拼接 */", "O(n)"),
    "env_set": ("setenv / _putenv_s", "O(1)"),
    "fs_rename": ("std::filesystem::rename", "ext(fs_rename)"),
    "fs_remove": ("std::filesystem::remove", "ext(fs_remove)"),
    "utf8_chars_vec": ("/* 解码成 uint64 数组 */", "O(n)"),
    "map": ("/* 具名函数：循环调用 */", "n × κ(f) + n"),
    "filter": ("/* 同上 */", "n × κ(f) + n"),
    "fold": ("/* 同上 */", "n × κ(f) + n"),
    "count_if": ("/* 同上 */", "n × κ(f) + n"),
    "for_each": ("/* 具名函数：循环调用 */", "n × κ(f) + n"),
    # ---- 数学内建（2026-11-10）：全部 O(1)，C++ 侧是 <cmath> / <bit> 的同名函数
    "sqrt": ("std::sqrt(x)", "O(1)；负数是 NaN（同 C）"),
    "log2": ("std::log2(x)", "O(1)；0 是 -inf、负数是 NaN（同 C）"),
    "floor": ("std::floor(x)", "O(1)；返回 double（不是整数）"),
    "ceil": ("std::ceil(x)", "O(1)；返回 double（不是整数）"),
    "round": ("std::round(x)", "O(1)；.5 远离零（同 C，不是银行家舍入）"),
    "fabs": ("std::fabs(x)", "O(1)"),
    "abs": ("std::abs(x)", "O(1)；整数/浮点走不同重载"),
    "popcount": ("std::popcount(x)", "O(1)；C++20 <bit>"),
    "clz": ("std::countl_zero(x)", "O(1)；C++20 <bit>；**参数为 0 是 UB**（我们报错）"),
    "ctz": ("std::countr_zero(x)", "O(1)；C++20 <bit>；**参数为 0 是 UB**（我们报错）"),
    # ---- 字符分类（2026-11-11）：O(1)、ASCII 口径。
    # **不能直接照抄**：C++ 的 `std::is*` 跟着 locale 走、返回 `int`；
    # 我们只有 ASCII 一张表、返回 `bool`（`ok` 已按 ASCII 判过）。注释里要写清。
    "isdigit": ("(std::isdigit(x) != 0)", "O(1)；**ASCII 口径**（C++ 是 locale 相关）；返回 bool"),
    "isspace": ("(std::isspace(x) != 0)", "O(1)；**ASCII 口径**（C++ 是 locale 相关）；返回 bool"),
    "isalpha": ("(std::isalpha(x) != 0)", "O(1)；**ASCII 口径**（C++ 是 locale 相关）；返回 bool"),
    "isalnum": ("(std::isalnum(x) != 0)", "O(1)；**ASCII 口径**（C++ 是 locale 相关）；返回 bool"),
    "isupper": ("(std::isupper(x) != 0)", "O(1)；**ASCII 口径**；返回 bool"),
    "islower": ("(std::islower(x) != 0)", "O(1)；**ASCII 口径**；返回 bool"),
    "toupper": ("std::toupper(x)", "O(1)；**ASCII 口径**；≥128 原样返回（C++ 可能不变、也可能按 locale 变）"),
    "tolower": ("std::tolower(x)", "O(1)；**ASCII 口径**；≥128 原样返回（C++ 可能不变、也可能按 locale 变）"),
}

#: `extern` 声明（FFI）：生成器直接写同名调用（头文件要作者自己 include）
EXTERN_NOTE = "extern：直接调用同名 C 函数；成本走 ext(name) 或 external 登记"


#: 明确**不映射**的（生成器遇到必须报错，不许静默）
UNMAPPED = {
    "exec": "不做进程（L4 已定为不做）",
    "now_ms": "不暴露时钟（只作为随机熵源）",
}


# ---------------------------------------------------------------- 类型层
#
# 这一层的职责**只有一件**：把 `.eln` / `.tu` 里的类型名翻成一个 C++17 类型名。
# 它不定义语义（`vector<u8>` 就是字节序列，不是 std::string）、不做代码生成，
# 也不替调用方兜底：认不出来就返回 None + 一条具体原因。
#
# 三条纪律（2026-11-13，L8）：
#   1. **递归**：容器按参数递归翻，任意深度。写死两层就会漏三层。
#   2. **宁可漏也不要猜错**：未知名字 / 未知容器返回 None，绝不"看起来像"
#      就原样输出或退化成 uint64_t。
#   3. **理由要具体**：`unmapped_reason()` 说清是哪一层、为什么、替代写法是什么。

#: 语料里**逐个核对过**的用户定义类型（struct / enum / class）名。
#:
#: 它们**原样透传**：`.tu` 里 `struct Ring { … }` ⇒ C++ 里也叫 `Ring`。
#: 为什么用名单而不是"看到大写就当用户类型"：后者会把拼错的类型名
#: （`Veector<u8>`、`U64`）静默变成合法的 C++ 标识符，错误要到编译期才暴露，
#: 而且指向的是生成代码而不是映射表。**新模块定义了 struct 就登记**：
#: `register_user_types("Foo")`（或直接 `USER_TYPES.add("Foo")`）。
#:
#: 依据（2026-11-13 扫描 `demo/corpus/*.tu` + `文档/样例/*.tu`）：
#:   · demo/corpus：Ring / Job / JobHeap / QueenSolver / Dsu / Bits / Entry / Group
#:   · 文档/样例：Interval / Summary / Token
USER_TYPES = {
    "Ring", "Job", "JobHeap", "QueenSolver", "Dsu", "Bits", "Entry", "Group",
    "Interval", "Summary", "Token",
}

#: 泛型函数的类型参数（`fn f<T>(x: vector<T>)`）：类型层原样透传。
#: 只在**类型位置**生效；C++ 模板里它们就是合法的 typename。
#: 缺了这条，`vector<T>` 会被判成未知类型（dsh 的泛型函数会因此退化）。
TYPE_PARAMS = frozenset({"T", "K", "V", "E", "U", "R"})

#: 递归容器：头 → (C++ 模板名, 参数个数)。
#: **只放语义一一对应的**；哈希容器和未接线的容器见 `UNMAPPED_TYPES`。
_CONTAINERS = {
    "vector": ("std::vector", 1),
    "Vec": ("std::vector", 1),                 # 改名期旧写法
    "map": ("std::map", 2),
    "Map": ("std::map", 2),
    "set": ("std::set", 1),
    "Set": ("std::set", 1),
    "optional": ("std::optional", 1),
    "Option": ("std::optional", 1),
    "expected": ("tl::expected", 2),
    "Result": ("tl::expected", 2),
}

#: 定长数组：第二个参数是**容量表达式**，不是类型（`array<u64, LCS_CAP>`）。
#: 容量允许整数或常量标识符，原样写进 `std::array<T, N>`——不去求值。
_ARRAYS = {
    "array": "std::array",
    "FixedVec": "std::array",                   # 旧名（typenames.CANON）
}

#: 认识但**故意不映射**的类型构造：名字 → 具体原因。
#: 这些名字要么在语言里有别的写法，要么在 C++ 侧不能只靠"换个名字"成立。
UNMAPPED_TYPES = {
    "unordered_map": (
        "`unordered_map<K,V>` 故意不映射：C++ 的 std::unordered_map 要求键可哈希，"
        "而本语言最常见的键 `vector<u8>` 没有 std::hash——映射出来**必然编不过**；"
        "并且哈希容器最坏 O(n)、顺序不可复现，与本语言的上界/可复现口径不同。"
        "要用请先给键一个明确的 hash 适配（这不是类型层能单独解决的），"
        "或者改用 `map<K,V>`（有序，O(log n)）。"
    ),
    "unordered_set": (
        "`unordered_set<T>` 故意不映射：同 `unordered_map`——哈希容器最坏 O(n)、"
        "顺序不可复现，且 `vector<u8>` 元素没有 std::hash。改用 `set<T>`。"
    ),
    "HashMap": (
        "`HashMap` 故意不映射：它是 stdlib `map_new()` 返回的**名义类型**，"
        "C++ 侧是 `std::unordered_map`（见 BUILTIN_MAP 的 map_new 注记），"
        "但类型层拿不到它的 K/V，也不该由类型名替内建调用决定；"
        "声明请写 `map<K,V>`。"
    ),
    "deque": (
        "`deque` 故意不映射：语言层**已登记但未接线**（checker 报 "
        "E-Type-UnimplementedContainer），没有可承诺的成本形状；"
        "映射到 std::deque 只会让人以为它已经能用。"
    ),
    "stack": (
        "`stack` 故意不映射：语言层已登记但未接线（没有语义），"
        "C++ 的 std::stack 也不是容器而是适配器；先别映射。"
    ),
    "queue": (
        "`queue` 故意不映射：语言层已登记但未接线（没有语义）；先别映射。"
    ),
    "pair": (
        "`pair` 故意不映射：本语言没有 pair 类型（parser 报 E-Type-NoPair）；"
        "两元组写 `(K, V)`，取元素用 `.0` / `.1`。"
    ),
}


class _TypeSyntaxError(Exception):
    """类型表达式解析/映射失败的**具体**原因（`unmapped_reason` 直接用它）。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _TypeParser:
    """把 `vector<vector<u8>>` / `map<K, V>` / `array<u64, N>` 解析成小树。

    只认类型名的形状：`名字` 或 `名字<参数, …>`；**不认**指针 / 引用 / 函数类型
    （这些限定符由调用方处理，见 `cxx_type` 的说明）。参数里允许整数（给 array 容量）。
    """

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.n = len(text)

    def _skip_ws(self) -> None:
        while self.pos < self.n and self.text[self.pos].isspace():
            self.pos += 1

    def _peek(self) -> str:
        self._skip_ws()
        return self.text[self.pos] if self.pos < self.n else ""

    def _ident(self) -> str:
        self._skip_ws()
        start = self.pos
        while self.pos < self.n and (self.text[self.pos].isalnum()
                                     or self.text[self.pos] == "_"):
            self.pos += 1
        if start == self.pos:
            near = self.text[self.pos:self.pos + 12]
            raise _TypeSyntaxError(
                f"类型表达式 `{self.text}` 解析失败：这里期望类型名，实际是 {near!r}")
        word = self.text[start:self.pos]
        if word[0].isdigit():
            raise _TypeSyntaxError(
                f"类型表达式 `{self.text}` 解析失败：`{word}` 以数字开头，不是类型名")
        return word

    def _type(self):
        name = self._ident()
        args = []
        if self._peek() == "<":
            self.pos += 1
            if self._peek() == ">":
                raise _TypeSyntaxError(
                    f"类型表达式 `{self.text}` 解析失败：`{name}<>` 没有类型实参")
            while True:
                args.append(self._arg())
                ch = self._peek()
                if ch == ",":
                    self.pos += 1
                    continue
                if ch == ">":
                    self.pos += 1
                    break
                near = self.text[self.pos:self.pos + 12]
                raise _TypeSyntaxError(
                    f"类型表达式 `{self.text}` 解析失败：`{name}<…>` 在参数 {len(args)} "
                    f"后期望 `,` 或 `>`，实际是 {near!r}（尖括号是否配平？）")
        return ("name", name) if not args else ("app", name, args)

    def _arg(self):
        self._skip_ws()
        if self.pos < self.n and self.text[self.pos].isdigit():
            start = self.pos
            while self.pos < self.n and self.text[self.pos].isdigit():
                self.pos += 1
            return ("int", self.text[start:self.pos])
        return self._type()

    def parse(self):
        node = self._type()
        self._skip_ws()
        if self.pos != self.n:
            rest = self.text[self.pos:]
            raise _TypeSyntaxError(
                f"类型表达式 `{self.text}` 解析失败：在位置 {self.pos} 多出 {rest!r}"
                f"（只支持 `名字` 或 `名字<参数, …>`；const / & / * 由调用方处理）")
        return node


def _extent(arg, text: str) -> str:
    """`array<T, N>` 的容量：整数或常量标识符，**原样**写进 std::array。"""
    if arg[0] == "int":
        return arg[1]
    if arg[0] == "name":
        return arg[1]
    raise _TypeSyntaxError(
        f"类型表达式 `{text}` 的数组容量不是整数/常量名（`array<T, N>` 的 N "
        f"只能是这两者之一）；不猜它的值")


def _unknown_name(name: str) -> _TypeSyntaxError:
    return _TypeSyntaxError(
        f"未知类型名 `{name}`：不是内建标量/容器，也不在已登记的用户类型里。"
        f"C++ 侧没有这个名字——**不许猜成 `uint64_t`**。"
        f"若它是本模块定义的 struct/enum，先 "
        f"`cxx_map.register_user_types(\"{name}\")`。"
    )


def _render_type(node, user_types, type_params, text: str, depth: int = 0) -> str:
    if depth > 32:
        raise _TypeSyntaxError(
            f"类型表达式 `{text}` 的嵌套超过 32 层：拒绝（多半是写错了）")

    if node[0] == "name":
        name = node[1]
        if name in TYPE_MAP:              # 标量 / 旧写法字面量
            return TYPE_MAP[name]
        if name in user_types:            # 已登记的用户 struct / enum
            return name
        if name in type_params:           # 泛型函数的 T / K / V / E
            return name
        if name in UNMAPPED_TYPES:
            raise _TypeSyntaxError(UNMAPPED_TYPES[name])
        raise _unknown_name(name)

    # node[0] == "app"
    head, args = node[1], node[2]
    if head in _ARRAYS:
        if len(args) != 2:
            raise _TypeSyntaxError(
                f"`{head}<…>` 需要 2 个参数（元素类型 + 容量），实际 {len(args)} 个："
                f"`{text}`")
        inner = _render_type(args[0], user_types, type_params, text, depth + 1)
        return f"{_ARRAYS[head]}<{inner}, {_extent(args[1], text)}>"

    if head in _CONTAINERS:
        cxx, arity = _CONTAINERS[head]
        if len(args) != arity:
            raise _TypeSyntaxError(
                f"`{head}<…>` 需要 {arity} 个类型参数，实际 {len(args)} 个：`{text}`")
        parts = [_render_type(a, user_types, type_params, text, depth + 1)
                 for a in args]
        return f"{cxx}<{', '.join(parts)}>"

    if head in UNMAPPED_TYPES:
        raise _TypeSyntaxError(UNMAPPED_TYPES[head])

    raise _TypeSyntaxError(
        f"未知类型构造 `{head}<…>`：`{head}` 不是本语言的容器名"
        f"（已登记：{', '.join(sorted(set(_CONTAINERS) | set(_ARRAYS)))}）。"
        f"用户 struct 不是泛型，不能带类型参数。"
    )


def _resolve(elench_type: str, user_types=None):
    """内部：返回 (C++ 类型, 失败原因)。成功时原因是空串。"""
    if not isinstance(elench_type, str):
        return None, f"类型不是字符串（收到 {type(elench_type).__name__}）：映射表只认类型名"
    text = elench_type.strip()
    if not text:
        # `()`（unit）和"解析失败/缺失"在 AST 里都会变成空串——类型层分不清，
        # 所以**不猜** void，也不猜 uint64_t；由调用方按位置（返回 vs 参数）决定。
        return None, (
            "空类型串：`()`/unit 返回与「类型缺失/解析失败」都长这样，类型层分不清，"
            "不猜 `void` 也不猜 `uint64_t`——请调用方按位置处理（返回位置可写 void，"
            "其余位置应报错）。"
        )
    if text in TYPE_MAP:
        return TYPE_MAP[text], ""

    ut = set(USER_TYPES)
    if user_types is not None:
        ut |= {user_types} if isinstance(user_types, str) else set(user_types)
    tp = set(TYPE_PARAMS)

    try:
        node = _TypeParser(text).parse()
        return _render_type(node, ut, tp, text), ""
    except _TypeSyntaxError as exc:
        return None, exc.reason


def cxx_type(elench_type: str, user_types=None):
    """Elench 类型名 → C++17 类型名；认不出返回 **None**（不是空串）。

    - `vector<T>` / `map<K,V>` / `set<T>` **递归**映射，任意深度；
    - `array<T, N>` 的 N 是容量表达式（整数或常量名），原样保留；
    - 已登记的 struct / enum 名**原样透传**（见 `USER_TYPES`）；
    - `unordered_map` 等故意不映射（见 `UNMAPPED_TYPES`），不会退化成 uint64_t。

    `user_types` 可传入本模块新定义的 struct 名（集合或单个字符串），
    这样调用方不必改全局表。
    """
    return _resolve(elench_type, user_types)[0]


def cxx_builtin(name: str):
    """内建映射 → (模板, 成本注记)；不映射返回 None。"""
    return BUILTIN_MAP.get(name)


def register_user_types(*names: str) -> None:
    """登记本模块新定义的用户类型（struct / enum / class）名，供透传。

    只接受合法标识符——名字会原样写进 C++，含空格 / 标点的一律拒绝。
    """
    for name in names:
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError(f"用户类型名必须是合法标识符：{name!r}")
        USER_TYPES.add(name)


def unmapped_reason(name: str) -> str:
    """这个**内建**或**类型**为什么不映射；能映射时返回空串。

    对内建：查 `UNMAPPED`（`exec` / `now_ms`）。
    对类型：查 `UNMAPPED_TYPES`，再实际跑一遍映射——失败时把**具体原因**
    （未知名字 / 参数个数 / 尖括号不配平 / 故意不映射）原样给出。
    """
    if not isinstance(name, str):
        return f"名字不是字符串（收到 {type(name).__name__}）"
    name = name.strip()
    if name in UNMAPPED:
        return UNMAPPED[name]
    if name in BUILTIN_MAP:
        return ""
    return _resolve(name)[1]
