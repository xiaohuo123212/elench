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

"""

#: Elench 类型 → C++ 类型
TYPE_MAP = {
    "u64": "uint64_t",
    "u32": "uint32_t",
    "i64": "int64_t",
    "u8": "uint8_t",
    "bool": "bool",
    "Vec<u8>": "std::vector<uint8_t>",
    "Vec<u64>": "std::vector<uint64_t>",
    "Vec<i64>": "std::vector<int64_t>",
    "Vec<u32>": "std::vector<uint32_t>",
    "HashMap<K,V>": "std::unordered_map<K, V>",
    "Set<T>": "std::unordered_set<T>",
    "Deque<T>": "std::deque<T>",
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
    "sort_by": ("std::sort(v.begin(), v.end(), cmp)", "O(n log n)"),
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
    "option_some": ("std::optional<T>{v}", "O(1)"),
    "option_none": ("std::nullopt", "O(1)"),
    "option_is_some": ("o.has_value()", "O(1)"),
    "vec_push": ("v.push_back(x)", "摊还 O(1)；分配可能发生"),
    "vec_pop": ("v.back(); v.pop_back()", "O(1)"),
    "option_unwrap_or": ("o.value_or(d)", "O(1)"),
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
}

#: `extern` 声明（FFI）：生成器直接写同名调用（头文件要作者自己 include）
EXTERN_NOTE = "extern：直接调用同名 C 函数；成本走 ext(name) 或 external 登记"


#: 明确**不映射**的（生成器遇到必须报错，不许静默）
UNMAPPED = {
    "exec": "不做进程（L4 已定为不做）",
    "now_ms": "不暴露时钟（只作为随机熵源）",
}


def cxx_type(elench_type: str) -> str:
    """类型映射；未知类型返回空串（调用方决定报错）。"""
    return TYPE_MAP.get(elench_type.strip(), "")


def cxx_builtin(name: str):
    """内建映射 → (模板, 成本注记)；不映射返回 None。"""
    return BUILTIN_MAP.get(name)


def unmapped_reason(name: str) -> str:
    return UNMAPPED.get(name, "")
