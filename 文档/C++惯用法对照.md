# C++ 程序员上手清单

> **这份文档只回答一个问题**：你手上那段 C++ 代码，搬过来要改哪几行？
>
> 每条都是**实测**的（`check` / `run`，2026-11-11，本仓库 `p40-work`）。
> 不是"设计上应该怎样"，是"今天跑出来是什么"。
>
> 一句话结论先给：**日常写法的形状全都在（函数、引用、range-for、容器方法、`struct`）；
> 危险的不是"缺什么"，而是"同名但语义不同"——第 3 节。**

---

## 0 怎么读

| 档 | 含义 | 你的感受 |
|---|---|---|
| **✅ 直接能用** | 名字和语义都同 | 不用改 |
| **🟡 换个写法** | 有替代，但要动几行 | 查一次就会 |
| **🔴 写不了** | 没有对应机制 | 换算法或换结构 |
| **⚠️ 同名不同义** | **能编译、名字一样、行为不一样** | **最容易埋雷** |

**第 3 节（⚠️）是这份文档存在的理由。** 前三档你在 [语法清单](语法清单.md) 里也能查到；
同名不同义只有踩过才知道。

---

## 1 直接能用的（同名同义）

### 1.1 函数与类型

```elench
u64 add_one(const u64& x) {
    return x + 1;
}

auto add_one2(const u64& x) -> u64 {
    return x + 1;
}
```

| C++ | 这里 | 备注 |
|---|---|---|
| `uint64_t f(const std::vector<uint64_t>& xs)` | `u64 f(const vector<u64>& xs)` | `std::` 前缀也认（同一类型） |
| `auto f(...) -> T` | 同 | 后置返回也收，**推荐前置** |
| `void f()` / `T f()` | 同 | |
| `T& x` / `const T& x` 形参 | 同 | 可变 / 只读借用；`const` 改了报 `E-Const-Mutate` |
| `std::optional<T>` | `optional<T>` | `Option` 已下线 |
| `std::expected<T, E>` | `expected<T, E>` | 也写 `T!`；`Result` 已下线 |
| `std::array<T, N>` | `array<T, N>` / `array<T, N>(v)` | `array<T, N>::filled(v)` 仍能用但报弃用 |
| `int` / `size_t` / `long` / `char` / `double` | 同 | 见 §3.1：**`int` 是 64 位** |
| `std::vector<T>` / `std::map<K,V>` / `std::set<T>` | 同 | |
| `using namespace std;` | 同 | 接受（不写也行） |
| `#include` / `#define` / `#if` | 同 | 兼容层：`#include` 的**标准头**丢弃（**非标准头报 `E-Lex-IncludeUnresolved`**）、`#define` 展开后再分析 |

### 1.2 容器方法（P4-0 起就是 C++ 的名字）

| 容器 | 方法 |
|---|---|
| `vector` / `vector<u8>` | `size` `empty` `at` `push_back` `assign` `pop` `find` `insert` `erase` `clear` `back` `front` |
| `array` / `FixedVec` | 同 `vector`，但**没有 `assign`**（定长：`assign` 会改长度，`check` 报 `E-Call-UnknownMethod`） |
| `map` | `size` `empty` `insert` `put` `get` `contains` `erase` `remove` `clear` `keys`（**没有 `values`**） |
| `unordered_map` | `size` `empty` `insert` `get` `contains` `erase` `remove` `clear` `keys` `values`（**没有 `put`**） |
| `set` | `size` `empty` `insert` `contains` `erase` `remove` `clear` `values`（**没有 `keys`**：元素就是键） |
| `unordered_set` | 同 `set`，多一个 `keys`：`size` `empty` `insert` `contains` `erase` `remove` `clear` `keys` `values` |
| `stack` / `queue` | `size` `empty` `push` `enqueue` `pop` `dequeue` `top` `peek` `front` `clear` |

**这张表的名字是实测的**（能过 `check`），不在表里的名字一律
`E-Call-UnknownMethod`，消息里列出该容器真正支持的名字。三个容易记反的点：

- `keys()` 只有 `map` / `unordered_map` / `unordered_set` 有；`values()` 只有
  `set` / `unordered_map` / `unordered_set` 有（`map.values()`、`set.keys()` 都报错）；
- `assign(n, x)`（清空后填 `n` 个 `x`，= C++ 的 `v.assign(n, x)`）只有 `vector` 有；
  它和 C++ 一样**返回 `void`**：**只能作语句**，`vector<u64> ys = xs.assign(2, 0);`
  报 `E-Type-Assign`（初值是 void），要保留结果先 `assign` 再读同一个变量；
- `unordered_map.put()` 不存在（`map.put` 是扩展名），而且 `unordered_map.insert(k, v)`
  **键已存在时会覆盖**、返回值**恒为 `0`**（`u64`）——`map.insert` 的两条 C++ 语义它都还没对齐，
  见 §3.11 与 [语法清单](语法清单.md) 的 `unordered_map` 一节。

```elench
u64 demo_ops() {
    vector<u64> xs = {1, 2, 3};
    xs.push_back(4);          // 追加
    xs.insert(1, 9);          // 在下标 1 前插入（= C++ 的 insert(begin()+1, 9)）
    xs.erase(0);              // 删下标 0
    u64 last = xs.back();
    u64 first = xs.front();
    bool none = xs.empty();
    u64 n = xs.size();
    xs.clear();
    return last + first + n;
}
```

### 1.3 遍历

```elench
u64 demo_loops(const vector<u64>& xs) {
    u64 s = 0;
    for (const auto& x : xs) {      // 只读引用：不拷贝
        s = s + x;
    }
    for (auto x : xs) {             // 按值：拷一份
        s = s + x;
    }
    for (u64 i = 0; i < xs.size(); i = i + 1) {
        if (xs.at(i) == 0) {
            continue;
        }
        if (i > 100) {
            break;
        }
    }
    return s;
}

u64 demo_mutate(vector<u64>& xs) {
    for (auto& x : xs) {            // 可变引用：改得动容器
        x = x + 1;
    }
    return xs.size();
}
```

**没有迭代器**，所以也**没有迭代器失效**这个经典坑——`push_back` 之后旧迭代器不存在。

**`map` 的遍历元素是 `(键, 值)`**（2026-11-12，对齐 C++ 的 `pair<const K, V>`）：

```elench
u64 demo_map_iter(map<vec<u8>, u64>& m) {
    u64 s = 0;
    for (const auto& kv : m) {      // kv: (vec<u8>, u64)，**键序**
        s = s + kv.1;               // 值（C++ 的 kv.second）
        s = s + kv.0.size();        // 键（C++ 的 kv.first）
    }
    return s;
}
```

| C++ | 这里 |
|---|---|
| `for (const auto& kv : m) { kv.first; kv.second; }` | `for (const auto& kv : m) { kv.0; kv.1; }`；**`--compat` 下 `kv.first` / `kv.second` 直接用**（按静态类型映射到 `.0` / `.1`） |
| `for (auto& kv : m) { kv.second++; }` | **本语言拒绝**（`E-Assign-Immutable`）：迭代元素是每轮现造的 `(键, 值)`，字段写只会**丢写**。改表写 `m[k] = v` / `m.put(k, v)` |
| `for (auto kv : m) { … }` | 同形：**整对深拷贝**（值里的容器也拷），改副本不动表 |
| `for (auto& kv : m) { kv.second.push_back(x); }` | **放行**：值里的**容器 / struct** 与表里是**同一对象**（`kv.1.push_back(x)` / `kv.1.field = 9` 都改得到表；这一条比 C++ 宽——`const auto&` 时 C++ 会拒绝） |

⚠ `for_each(m, f)` 仍按 `f(k, v)` 逐项回调，与 range-for 的 `(键, 值)` 元素是同一套语义。

**三段式迭代器写 `map` / `set`**（`--compat`，2026-11-12）：C++ 的
`for (auto it = m.begin(); it != m.end(); ++it)` 也认，降级成**键快照 + 下标循环**
（快照只在循环外取一次）。本语言没有迭代器，所以这条只在 `--compat` 下成立；
手写等价写法就是下面第三列。

```elench
// 手写等价（与 --compat 的降级同形；κ / run 逐字节相同）
u64 map_total(map<vec<u8>, u64>& m) {
    u64 s = 0;
    {
        vector<vec<u8>> ks = m.keys();   // 快照：**循环外只取一次**
        u64 idx = 0;
        while (idx < ks.size()) {
            s = s + m.get(ks[idx]);      // 值（C++ 的 it->second）
            idx = idx + 1;
        }
    }
    return s;
}
```

| C++（`--compat`） | 降级成 | 说明 |
|---|---|---|
| `it->first` | `ks[idx]` | 键；只读快照，**不查表** |
| `it->second` | `m.get(ks[idx])` | 值；每次 `get` 是 O(log n) |
| `*it` | `( ks[idx], m.get(ks[idx]) )` | 本语言的二元组（`.0` / `.1`；`--compat` 下 `.first` / `.second`） |
| `set` 的 `*it` | `ks[idx]` | `set` 用 `.values()` 取快照；`it->first/second` **报错**（`set` 没有值） |
| `it->second = v` | **拒绝** `E-For-IteratorMutate` | 降级后 `it->second` 是一次 `get()`（读），写进去只丢写。改表写 `m[k] = v` / `m.put(k, v)` |
| 循环体改容器（`m.insert` / `m.clear` …） | **拒绝** `E-For-IteratorMutate` | C++ 里同样是 UB |

⚠ **κ 比 C++ 贵一档**（安全方向，但要知道）：C++ 的 `std::map` 遍历是 **O(n)**；
我们 `keys()` 一次 O(n) + 每轮 `get` O(log n) ⇒ **O(n log n)**（`unordered_map`
的 `get` 契约最坏 O(n) ⇒ **O(n²)**）。等价性守卫钉的是"与手写快照循环逐字节
相同"，不是"与 C++ 同阶"。

### 1.4 结构体、枚举、指针、元组

```elench
// 前两行写在 .tu 里；point_sum 写在 .eln 里
struct Point { x: u64, y: u64, }

enum Token { Num(u64), Plus, }

u64 point_sum(Point p) {
    return p.x + p.y;
}
```

| C++ | 这里 |
|---|---|
| `struct P { int x; int y; };` | `struct Point { x: u64, y: u64, }`（字段用 `:`，逗号分隔，**写在 `.tu`**） |
| `enum class E { A, B };` | `enum Token { Num(u64), Plus, }`（`enum class` 报错） |
| `Point p{1, 2};` | `Point p = Point { x: 1, y: 2 };` |
| `std::pair<A,B>` / `std::tuple` | 两元组 `(A, B)`，取 `t.0` / `t.1`（默认 `std::pair` 报 `E-Type-NoPair`；`--compat` 下 `std::pair` / `std::make_pair` / `.first`·`.second` 直接映射，见[语法清单](语法清单.md) §P0 第 14 条） |
| `T* p` / `&x` / `*p` / `p->f` / `nullptr` | 同（运行期已接线；`p.f` 报 `E-Pointer-UseArrow`） |
| `print(p)`（struct） | 同：`S { a: 1, b: 2 }` |

### 1.5 `--compat` 下的纯拼写：整数字面量后缀与复合赋值

`--compat` 打开时**直接能用**；不加开关就写右边那列（两者是**同一个程序**：
AST / κ / run 逐字节相同）。

| C++ 写法 | 等价的本语言写法 | 说明 |
|---|---|---|
| `1ULL` `1ull` `1UL` `1LL` `1L` `1U` … | `1` | 后缀只说明 C++ 那边的宽度；本语言整数字面量就是 `u64`，**值不变** |
| `0xFFULL` / `0b1010U` | `0xFF` / `0b1010` | 带前缀同理 |
| `x \|= m` | `x = x \| m` | 位或 |
| `x &= m` | `x = x & m` | 位与 |
| `x ^= m` | `x = x ^ m` | 异或 |
| `x <<= 2` / `x >>= 1` | `x = x << 2` / `x = x >> 1` | 左移 / 右移 |
| `x %= n` | `x = x % n` | 取模（`%=` 以前也没有；`+= -= *= /=` 本来就有） |

**`1.5f` / `1f` 不收**：`f` 是**浮点**后缀，C++ 的 `float` 是 32 位——把它当
`f64` 收下会改变 `0.1f` 这类值，本语言不做字面量级窄化（见 §3.16）。

**一个写明的偏差**：`a[f()] |= m` 降级成 `a[f()] = a[f()] | m`，左侧下标里的
`f()` 会**跑两遍**。本语言没有"左值引用"这层抽象，既有的 `a[f()]++` 也是
同一口径；有副作用的左侧下标请拆成两句。

---

## 2 要改名的（同义不同名）

**P4-0（2026-11-11）把名字对齐了 C++，旧名还留着但报弃用。**

| 你习惯的 | 这里写 | 状态 |
|---|---|---|
| `xs.push(v)` | `xs.push_back(v)` | 旧名报 `W-Builtin-Deprecated`，v0.73 移除 |
| `xs.length()` | `xs.size()` | 同上 |
| `xs.is_empty()` | `xs.empty()` | 同上 |
| `array<T,N>::filled(v)` | `array<T,N>(v)` | 同上 |
| `xs.pop_back()` | `xs.pop()` | **不只是改名**，行为也不同 → §3.2 |
| `std::find(xs.begin(), xs.end(), v)` | `xs.find(v)` | 返回**下标**，不是迭代器 → §3.3 |
| `std::sort(v.begin(), …)` | `sort(xs)` / `sort_by(xs, cmp)` | 原地改，返回 `u64` |
| `std::cout << x` | `print(x)` | 见 §3.7（格式化写法不同） |
| `std::string::npos` | `npos` | 值相同（`2^64-1`） |

> **`.tu` 里也报同一条**（2026-11-13 起）：`target { max: O(xs.size()) }` 是**形状量**，
> 两种写法等价，但 `.length()` 现在在 `.tu` 的 `sizes` / `target` / `benchmodel` /
> `repeats` / `requires` 里同样报 `W-Builtin-Deprecated`（与 `.eln` 函数体里的方法调用
> 同一口径）。**同一个弃用拼写不许两种待遇**——所以 `.tu` 里也请写 `.size()`。

---

## 2.1 `std::` 逐个对照（按 `demo/corpus/` 实测）

**方法**：对 `demo/corpus/*.cpp`（12 个文件）用 `std::([A-Za-z_][A-Za-z_0-9]*)`
逐个匹配、按名字计数——**不是凭印象**。下表是当前语料的真实次数
（`00_docnums.cpp` 633 行、143 处；12 个文件合计 327 处）：

| 名字 | 00_docnums | 其余 11 个 | 合计 | 档 | 在本语言里长什么样 |
|---|---:|---:|---:|---|---|
| `std::string` | 82 | 50 | 132 | 类型 | `vector<u8>`（`Text` 的运行时形态）；`--compat` 已支持 |
| `std::vector` | 12 | 46 | 58 | 类型 | `vector<T>`；已支持 |
| `std::size_t` | 0 | 51 | 51 | 类型 | `u64`；已支持 |
| `std::printf` | 10 | 25 | 35 | **A** | `print(...)`；**`--compat` 下格式串也一起翻**（`%d`→`{:d}`、`%s`→`{:s}`、`%llu`→`{:u}`，见 §P0 第 17 条） |
| `std::map` | 0 | 6 | 6 | 类型 | `map<K,V>`；已支持 |
| `std::sregex_iterator` | 4 | 0 | 4 | **C** | 没有正则：`text_find` / `text_split` / `text_replace` |
| `std::pair` | 3 | 0 | 3 | 类型 | 两元组 `(A, B)` + `.0` / `.1`（§P0 第 14 条） |
| `std::move` | 3 | 0 | 3 | **B** | **删除式**：`std::move(x)` 整个删掉、直接写 `x`——`--compat` 下 AST 里**没有包装节点**（见 §2.1.1） |
| `std::cerr` | 3 | 0 | 3 | **C** | `eprint(...)` / `eprintln(...)`（`<<` 链改成实参列表） |
| `std::regex` | 3 | 0 | 3 | **C** | 没有正则：字面量匹配用 `text_find` / `text_split` / `text_replace` |
| `std::unordered_map` | 0 | 3 | 3 | 类型 | `unordered_map<K,V>`；已支持 |
| `std::ifstream` | 2 | 0 | 2 | **C** | `open_read(path)`（`Result<Reader>`）或 `read_file(path)` |
| `std::filesystem` | 2 | 0 | 2 | **C** | `cwd()` / `file_exists` / `file_size` / `fs_rename` / `fs_remove`（路径就是文本） |
| `std::stoul` | 2 | 0 | 2 | **C** | `parse_u64` / `parse_hex`（`Result`，**整串**合法；没有 `base` 实参） |
| `std::atoll` | 2 | 0 | 2 | **C** | `parse_u64`（`Result`，整串合法；不跳过空白、不收负号） |
| `std::isspace` / `std::isdigit` | 1 / 1 | 1 / 1 | 2 / 2 | **A** | 同名内建（ASCII 口径）；已在白名单 |
| `std::ios` | 1 | 0 | 1 | **C** | 删掉 `std::ios::binary`——本语言读写本来就是二进制 |
| `std::ostringstream` | 1 | 0 | 1 | **C** | `vector<u8>` + `text_concat` / `+=` |
| `std::stod` | 1 | 0 | 1 | **C** | **没有字符串→浮点**（整数定点，或 `parse_u64` 后自己拼小数） |
| `std::to_string` | 1 | 0 | 1 | **C** | `format("{}", x)`（`format_u64` 只收 `u64`） |
| `std::reverse` | 1 | 0 | 1 | **A** | `reverse(xs)`（`begin`/`end` 由第 5 条配平）；本轮新增 |
| `std::remove` | 1 | 0 | 1 | **C** | `text_replace` / `filter`（没有 `erase(first, last)`） |
| `std::stringstream` | 1 | 0 | 1 | **C** | `text_split` + `text_concat`（没有字符串流对象） |
| `std::getline` | 1 | 0 | 1 | **C** | `next_line(r)`（Reader）/ `text_split(s, sep)`（按分隔符切） |
| `std::getenv` | 1 | 0 | 1 | **C** | `option_unwrap_or(env_get("X"), 默认)`（返回 `Option`，不是 `char*`） |
| `std::isalnum` | 1 | 0 | 1 | **A** | 同名内建（ASCII 口径）；已在白名单 |
| `std::regex_replace` | 1 | 0 | 1 | **C** | `text_replace`（只能**字面量**匹配，不是正则） |
| `std::find` | 1 | 0 | 1 | **C** | `xs.find(v)`——返回**下标**（找不到 `npos`），不是迭代器 |
| `std::count` | 1 | 0 | 1 | **A** | `count_of(xs, v)`；本轮新增 |
| `std::priority_queue` | 0 | 0 | 0¹ | **C** | 只有 `06_heap.cpp` 的**注释**提到一次（代码自己用定长数组堆）；真要写就 `array` + `push_heap` / `pop_heap` |

¹ 名字统计里它出现 1 次，但那行是注释（`// 真实场景：调度器不使用 std::priority_queue…`），
所以**代码里的真实用法是 0**。

**三档的判据**（`src/compat.py` 是唯一来源）：

- **A 档 = 有等价物**：本语言已有同名同义的东西，`--compat` **改名**即可
  （`STD_EXPR_ALIASES`）。本轮新增两个：`std::reverse` → `reverse`、
  `std::count` → `count_of`；`cost --signature` / `run` / AST 与手写**逐字节相同**
  （`tests/cases/test_compat.py` 的 `[34]`）。只对 `xs.begin(), xs.end()`
  这种**整容器**配平成立；子区间（`xs.begin() + k`）报第 5 条的迭代器错误，
  **不会静默算错**。
  ⚠ `reverse` / `count_of` **本身已弃用**（v0.73 移除，推荐手写循环）：
  映射语义不变，但 `check` 会多一条 `W-Builtin-Deprecated`；想消警告就按它给的循环写。
- **B 档 = 删除式**：本语言的值语义让这个调用**没有可观察作用**，所以
  不是"改个名字"，而是把 `std::X(...)` **连括号带实参整个删掉**，parser
  直接返回里面那个表达式的 AST（`STD_DELETE_ALIASES`）。本轮**唯一**的名字
  是 `std::move`：`std::move(x)` → `x`。删完之后 `cost --signature`（κ 整份
  JSON）/ `run` stdout / AST 与手写 `x` **逐字节相同**（`tests/cases/test_compat.py`
  的 `[35]`）——这正是"纯删除"的证据。它不是"更快"，只是"不用改代码"（见 §2.1.1）。
- **C 档 = 真的没有**：不进白名单，仍报 `E-Compat-UnknownStd`；诊断里按
  "环境变量 / 文本↔数 / 迭代器算法 / 流 / 正则 / 文件系统 / 进程"
  分组给**逐个改法**（`STD_NO_EQUIV`）。

### 2.1.1 `std::move` 的结论：**删除式降级**（B 档）——不是"更快"，是"不用改代码"

先**实测**本语言的值语义（下面这段真的跑过，不是推测）：

```elench
u64 demo_move_is_copy() {
    vector<u64> a = {1, 2};
    vector<u64> b = a;      // 深拷贝
    b[0] = 7;               // a[0] 仍然是 1
    return a[0] * 10 + b[0];   // 17
}
```

按值形参也一样：`f(a)` 里改 `v[0]`，`a` 不受影响。**赋值 / 传参 / 返回 /
入容器都拷贝**（§3.12）——本语言**没有移动构造**。

```text
C++: std::move(x) 是把所有权搬走（O(1)）
这里: 没有移动语义 → std::move(x) 就是 x，**实参照常深拷贝**（κ 里如实记 O(n)）
      ⇒ 它不是"更快"，只是"不用改代码"
```

C++ 的 `std::move(x)` **本身不搬任何东西**：它只是把 `x` 转成右值，
真正"搬"发生在接收端的移动构造 / 移动赋值里。本语言接收端永远拷贝
⇒ `std::move(x)` 在这里**没有可以省掉的拷贝**，`std::move(x)` 与 `x`
值相同。把包装整个删掉是**同语义**的：C++ 里 moved-from 的状态是
*unspecified*，所以**不读它**的程序两边行为一致；读了它的 C++ 程序本来
就没有可移植的确定行为，本语言给出确定值并不违背等价性。

**方向也是安全的（κ 只多不少）**：C++ 里接收端拿的是移动（O(1)），
这里落成深拷贝（O(n)）——κ **更保守**，不会低估。

**它是 B 档（删除式），不是 A 档改名**：白名单只能"把名字换成另一个名字"，
表达不了"**删掉这个调用**"；映到 `copy(x)` 会多一次分配（κ 变了，
违反"逐字节相同"），映到不存在的 `move(x)` 会变成 `E-Call-Undeclared`。
所以 parser 在 `--compat` 下**不产生任何包装节点**：`std::move(e)` 直接返回
`e` 的 AST（`parser._compat_std_delete`，表在 `compat.STD_DELETE_ALIASES`）。
证据是 `cost --signature`（κ 整份 JSON）与 `run` stdout 与手写 `x`
**逐字节相同**（`tests/cases/test_compat.py` 的 `[35]`，五种表达式位置：
初始化 / 赋值右侧 / 返回 / 实参 / 下标+算术）。

**形状守卫**：只认**恰好一个**实参。`std::move(a, b)` / `std::move()` /
裸 `std::move` 报 `E-Compat-StdDeleteArity`（不猜、不静默丢掉多出来的实参）。
（`00_docnums.cpp` 里三处 `std::move` 都在**最后一次使用**的位置：
`emplace_back` / `push_back` 之后那个局部变量不再被读，所以删掉即等价。）

### 2.1.2 类型位置的 C 档走另一条消息

`std::regex` / `std::ifstream` 这类名字在**明确类型位置**
（`const std::regex r(…)`）走 parser 的 `E-Type-UnknownStdName`，那条消息
写死在 parser 里、**不含** `STD_NO_EQUIV` 的改法（已知缺口）。在表达式位置
（`std::regex_replace(...)`、`std::cerr << …`）才走 `E-Compat-UnknownStd`。
两条路都**拒绝**（不静默），改法查上面两张表即可。

---

## 3 ⚠️ 同名不同义：最危险的一类

**这一节每条都是"能编译、名字一样、跑出来的结果不一样"。**

| # | 项 | 你以为是 C++ | 实际是 | 危险 |
|---|---|---|---|---|
| 3.1 | `int` | 32 位 | **64 位** | 中 |
| 3.2 | `pop()` | 无返回 | **返回被弹出的元素**（且没有 `pop_back`） | 中 |
| 3.3 | `find()` | 返回迭代器 | **返回下标**，找不到是 `npos` | 中 |
| 3.4 | `size()`（字符串） | 字符数（在 UTF-8 下也是字节数） | **字节数**，字符数要 `utf8_len` | **高** |
| 3.5 | `m[k]` / `insert` / `put` | 缺键插入 `V{}`；`insert` 不覆盖 | **一致**（2026-11-11 起）：`m[k]` 也插入默认值、`insert` 也不覆盖；覆盖写走 `m[k] = v` / `m.put(k,v)` | 低（读 `m[k]` 有 `W-Map-SilentInsert`） |
| 3.6 | 溢出 | 有符号是 UB | **两种都有定义**：回绕 | 中 |
| 3.7 | `print` | `cout` 那套 | 数字/布尔/容器的打印格式不同 | 中 |
| 3.8 | 隐式转换 | C++ 那套 | **整数互转静默**；**浮点→整数直接拒绝** | **高** |
| 3.9 | `insert` / `erase` | 返回迭代器（`pair<it,bool>`） | `map.insert` 返回 **`u64`**（`1` = 插进去了、`0` = 键已存在；数值同 `pair.second`，类型不是 `bool`，2026-11-13 起）；`vector.insert` / `erase` 仍是 **`void`**、只收下标（没有迭代器） | 低 |
| 3.10 | `[]` 越界 | UB | **报错** | 低（更安全） |
| 3.11 | `unordered_map` | 平均 O(1) | 承诺**最坏 O(n)**，顺序未定 | 中 |
| 3.12 | 拷贝 | 可能被移动优化掉 | **每个赋值都真的拷**（值语义） | 中 |
| 3.13 | `array<T,N>` 满员 | UB / 断言 | **`push_back` 报错** | 低 |
| 3.14 | `const` | 可以 `const auto` | **`const` 必须写类型**（`E-Decl-ConstNoType`） | 低 |
| 3.15 | `func` / `fn` | C++ 没有 | **已下线**，写了报 `E-Func-KeywordRemoved` | 低 |
| 3.16 | 类型后缀 `42u64` / `1.0f` | C++ 有 | **解析错误**；`42u` / `1ULL` 在 `--compat` 下是**纯拼写**（与 `42` / `1` 同值，见 §1.5） | 低 |
| 3.17 | `struct` 成员函数 | 有 | **没有**（`E-Type-StructMethod`） | 中 |
| 3.18 | `public:` / `private:` | 真的限制 | **接受但完全忽略**（字段全公开） | 中 |
| 3.19 | `func` 泛型 | `template<typename T>` | 泛型写在名字后：`T pick<T>(T a, T b)` | 低 |
| 3.20 | 借用 | Rust 那套 | **完整借用规则还没落地**：`f(xs, xs)` 能过 | **高** |

下面逐条说清楚。

### 3.1 `int` 是 64 位

```elench
i64 demo_int() {
    int x = 2147483647;
    int y = x + 1;        // C++ 里这是溢出；这里是 2147483648
    return y;
}
```

- `int` → **`i64`**，`unsigned` → `u64`，`long` / `size_t` → 64 位；
- 要真的 32 位就用定宽名：`uint32_t` / `int32_t`（映射 `u32` / `i32`，**不放大**）。

### 3.2 `pop()` 返回元素，而且没有 `pop_back`

```elench
u64 demo_pop() {
    vector<u64> xs = {1, 2, 3};
    u64 a = xs.pop();     // 3：C++ 的 pop_back() 返回 void，这里返回被弹出的元素
    u64 b = xs.pop();     // 2
    return a + b + xs.size();
}
```

- `pop_back()` **不存在**：报 `E-Call-UnknownMethod`，消息里列出支持的方法；
- `pop()` 从**尾部**弹出（与 `pop_back` 同侧），但**返回元素**；
- 空容器上 `pop()` **报错**（C++ 是 UB）。

### 3.3 `find()` 返回下标，不是迭代器

```elench
u64 demo_find(const vector<u64>& xs) {
    if (xs.find(42) == npos) {     // 找不到 → npos（2^64-1）
        return 0;
    }
    return 1;
}
```

- `find(v)` → **下标**（`u64`）；找不到 → `npos`；
- C++ 的 `std::find` 返回迭代器、`string::find` 返回 `npos`——这里统一成后者；
- 代价：`find` 是**线性扫描**，不是 `std::map::find` 的对数。

### 3.4 字符串的 `size()` 是**字节数**

```elench
u64 demo_utf8() {
    vector<u8> s = "中文";
    return s.size() * 100 + utf8_len(s);   // 6 * 100 + 2
}
```

| 你想问 | 用 |
|---|---|
| 占几个字节（存储、协议、哈希） | `s.size()` |
| 几个字符（界面、光标、截断） | `utf8_len(s)` |
| 第 i 个字符的码点 | `utf8_codepoint_at(s, i)` |
| 第 i 个字符的字节偏移 | `utf8_byte_of_char(s, i)` |

**C++ 的 `std::string::size()` 也是字节数**——这条两边一致。
但这里的**文本就是 `vector<u8>`**，所以你更容易顺手把"长度"当字符数用。

### 3.5 `m[k]` 与 `insert`：已按 C++ 对齐（2026-11-11，**只对 `map`**）

```elench
u64 demo_map() {
    map<u64, u64> m;
    u64 a = m.insert(1, 10);      // 1
    u64 b = m.insert(1, 99);      // 0：**不覆盖**，m.get(1) 仍是 10
    m[2] = m.get(1) + 1;          // 插入键 2（存在则覆盖）
    u64 v = m[3];                 // 读缺键 → 插入 0 并返回它（= C++ 的 operator[]）
    m.put(1, 7);                  // 覆盖写（= C++ 的 m[1] = 7）
    return v + m.get(1) + m.size() + a + b;   // 0 + 7 + 3 + 1 + 0
}
```

| 写法 | 键不存在 | 键已存在 | 返回 | 与 C++ |
|---|---|---|---|---|
| `m[k]`（读） | **插入 `V{}`** 并返回 | 返回已有的值 | `V` | ✅ 同 `operator[]` |
| `m[k] = v` | 插入 | **覆盖** | `()` | ✅ 同 `m[k] = v` |
| `m[k] += 1` / `m[k]++` | 先插入 `V{}` 再读-改-写 | 读-改-写 | `()` | ✅ 同 C++ |
| `m.insert(k, v)` | 插入，返回 `1` | **不覆盖**，返回 `0` | `u64` | ✅ 语义同 `insert` 的 `pair.second`；**类型不同**：C++ 是 `bool`，本语言是 `u64` |
| `m.put(k, v)` | 插入，返回 `1` | **覆盖**，返回 `0` | `u64` | 扩展（C++ 写 `m[k] = v`） |
| `m.get(k)` | **运行期报错** | 返回值 | `V` | 扩展（严格读，缺键不静默） |

> 上表是 **`map`** 的口径。`unordered_map` **没有**这两条：`um.insert(k, v)` 键已存在时
> **覆盖**（不是 C++ 的"不覆盖"），返回的 `u64` **恒为 `0`**（新键也是 0；`check` 按
> `u64` 收，`run` 永远读到 0）。原因是它的运行时值是薄适配层、不记 value 类型，造不出默认值——
> 详见 [语法清单](语法清单.md) 的 `unordered_map` 一节。要 C++ 语义请用 `map`。

**代价是同一个陷阱也搬过来了**：C++ 里 `if (m[k] == 0)` 一句只想读的判断会
**静默插入**（`size()` 变大、之后的遍历多一项）。所以 `check` 在**读**位置的
`m[k]` 上报 `W-Map-SilentInsert`（**warning，默认开、不阻断**）：
只想读就写 `m.get(k)`（缺键报错）或先 `m.contains(k)`（返回 `bool`，与 C++ 的
`contains` 同义，可直接写进 `if`）；确实要插入就写
`m[k] = v` / `m.insert(k, v)`。写位置的 `m[k] = v` / `m[k] += 1` / `m[k]++`
不报——那是写者本意；**同一条语句里正在写同一个容器时的回读也不报**
（`freq[w] = freq[w] + 1` 那条 RHS 是词频的标准写法，报它只是噪音）。

> 历史：2026-11-11 之前这里是反的（`m[k]` 读缺键**报错**、`insert` **覆盖**），
> 而且 `m[k] += 1` 走的是**序列下标**路径——键值 `< 条目数` 时"恰好"算对、
> `≥ 条目数` 时误报 `E-Index-OutOfRange`（对错取决于数据的偶然大小关系）。
> 三条都记在 `文档/设计/目前设计/审计-容器vsC++标准-2026-11-11.md` 的 A2 / A3 / A13。

### 3.6 溢出：两种都有定义

```elench
i8 demo_signed_wrap() {
    i8 a = 127;
    return a + 1;          // -128（回绕，不是 UB）
}

u64 demo_unsigned_wrap() {
    u64 c = 18446744073709551615;
    return c + 1;          // 0
}
```

| | C++ | 这里 |
|---|---|---|
| 无符号溢出 | 回绕 | **回绕** |
| **有符号溢出** | **UB** | **回绕**（按位宽） |
| `i8/i16/i32` 的中间表达式 | 按位宽 | **中间按 64 位算，赋值/返回/入容器时才回绕** |

**最后一行是真正的坑**：`i8 a = 127; a + 1` 在表达式里是 128，赋回 `i8` 才变 `-128`。
所以 `i8 x = 100; i8 y = 100; x + y` 得到的是 **200**（不是 -56）；
要按 8 位算，要么写 `u8`/`i8` 的存储点，要么别指望它会"先溢出再比较"。

### 3.7 `print` 的格式

| 表达式 | 打出来 | C++ 会打 |
|---|---|---|
| `print(42)` | `42` | `42` |
| `print('a')` / `print(u8 值 97)` | **`97`** | `a`（char） |
| `print(true)` | **`True`** | `1` |
| `print(3.14)` | `3.14` | `3.14` |
| `print(vector<u8>{'h','i'})` | `hi` | 不支持 |
| `print(vector<u64>{3,1,2})` | `3 1 2`（空格分隔） | 不支持 |
| `print("n={} f={}", 5, 1.5)` | `n=5 f=1.5` | —（C++ 用 `<<` / `format`） |

**字符打出来是数字**：本语言没有 `char` 的"字符语义"，`char` 就是 `u8`（一个字节）。
要打字符，把单字节转成 `vector<u8>` 再打。

### 3.8 隐式转换：整数静默、浮点拒绝

| 写法 | 结果 |
|---|---|
| `u64 x = -1;` | **静默**变成 `18446744073709551615` |
| `i64 y = 3.5;` | **拒绝**：`E-Type-FloatToInt`（隐式截断不允许） |
| `f64 z = 1;` | 接受（整数→浮点） |
| `u64 a + i64 b` | 按**无符号**算（C++ 的常规算术转换同此），负数会绕成天文数字 |
| `u8 b = 300;` | **静默**截成 `44` |

**没有类型转换函数**：`u64(x)` 会被当成"调用未声明的外部函数 `u64`"（`E-Call-Undeclared`）。
要换宽度就用**带类型的中间变量**，别写 C 风格的 cast。

> 早先的文档写过"没有隐式转换"——**那条已经过期**：整数之间的隐式转换今天是真的存在
> （而且静默）。被禁止的只有**浮点→整数**这一条。

### 3.9 `insert` / `erase` 的返回值

- **序列**：`xs.insert(pos, value)` 在 `pos` **之前**插入，`pos == size()` 等价于
  `push_back`；`xs.erase(pos)` 删掉下标 `pos`（O(n) 搬移）；两个都返回 `void`
  （C++ 返回迭代器）——`u64 n = xs.erase(0);` 会报错；
- **`map.insert(k, v)` 返回 `u64`**（2026-11-13 起；2026-11-11 到 11-12 之间这里写的
  是 `bool`）：插进去了 = `1`，键已存在则**不覆盖**、返回 `0`。数值正好对上 C++ 的
  `pair<iterator,bool>.second`，但**类型不是 `bool`**（本语言没有迭代器，只留计数）——
  所以 `bool ok = m.insert(k, v);` 编不过，要写 `u64 ok = …;`。
  **`unordered_map.insert` 还不是这个**：它覆盖已有键、且恒返回 `0`
  （`check` 同样按 `u64` 收，见 §3.5 末注）；
- **没有** `insert(it, ...)` / `erase(first, last)` / `erase(it)` 的迭代器版本。

### 3.10 `[]` 越界**报错**

```elench
u64 demo_index(const vector<u64>& xs) {
    return xs[5];      // 长度不足时：E-Index-OutOfRange（不是 UB）
}
```

`at()` 与 `[]` 在**越界行为**上一样（都报错）；差别只在文档口径：
`at()` 是"我确认在范围内"，`[]` 是"我习惯这么写"。两者都不静默。

### 3.11 `unordered_map` 承诺最坏 O(n)

| | `map` / `set` | `unordered_map` / `unordered_set` |
|---|---|---|
| 查找/插入/删除（标量键） | **最坏 O(log n)** | **最坏 O(n)**（承诺） |
| 查找/插入/删除（容器键） | **最坏 O(log n)**（按键比较，键长不计） | **最坏 O(n + 键长)**：哈希要读完整个键，κ 里带一个 `n(键表达式)` 项 |
| 迭代顺序 / `keys()` `values()` 的顺序 | 键序 / 元素序，**可复现** | **未指定，不要依赖**（实现上是插入序，契约不保证） |
| 键的要求 | 全序（`<`） | 可哈希：定宽标量、`vector<T>` / `array<T,N>`（C++ 的 `std::string` 就是 `vector<u8>`） |

**它存在的意义是与 C++ 对齐，不是更快。** 成本契约只承诺最坏，所以哈希表在这里"更差"。

方法名的差别也记一笔：`unordered_map` **有** `values()`（`map` 没有），**没有**
`put()`（`map` 有）；`unordered_set` 的 `keys()` 与 `values()` 返回同一批元素（`set`
只有 `values()`）。两者都是**副本快照**，往里加元素不会改表。

### 3.12 拷贝是真的

```elench
u64 demo_copy() {
    vector<u64> a = {1, 2};
    vector<u64> b = a;     // 真拷一份
    b.push_back(3);        // a 不受影响
    return a.size() * 10 + b.size();
}
```

- 赋值 / 传参 / 返回 / 入容器**都拷贝**（七个拷贝点，都记成本）；
- **没有移动语义**（没有右值引用；`std::move` 在 `--compat` 下是**删除式**
  降级——`std::move(x)` 就是 `x`，实参照常深拷贝，见 §2.1.1）；
- 要避免拷贝就用**引用形参**：`const vector<u64>&`（只读）或 `vector<u64>&`（可写）。

### 3.13 `array<T,N>` 是定容

```elench
u64 demo_array() {
    array<u64, 3> a = array<u64, 3>(0);
    a[0] = 7;
    return a.size();       // 3：定长，size() 是容量
}
```

满了再 `push_back` → **运行期报错**（"定长数组不能增长"）。
C++ 的 `std::array` 没有 `push_back`，这里**有**——所以更容易误用。

### 3.14 `const` 必须写类型

```elench
u64 demo_const() {
    const u64 n = 5;       // 对
    return n;              // `const n = 5;` 报 E-Decl-ConstNoType
}
```

**本语言不做类型推断**（`const auto` / 局部 `auto` 都不行）：
契约与成本分析都按声明的类型算 κ，推断会让"承诺的上界"不可验证。
`auto` 只允许出现在**函数声明的后置返回**位置。

### 3.15 `func` / `fn` 已下线

```elench
u64 add(u64 a, u64 b) {
    return a + b;
}
```

写 `func add(u64 a, u64 b) -> u64` → `E-Func-KeywordRemoved`（error，不是警告）。
`.tu` 里的 `spec` 不是 C++ 源码，写法不变。

### 3.16 类型后缀：`42u64` / `1.0f`

`42u64` / `1.0f` 都会**解析失败**——本语言没有"类型后缀"（类型由上下文决定，
浮点字面量默认 `f64`）。

**但 C++ 的整数后缀在 `--compat` 下是纯拼写**：`42u` / `1ULL` / `0xFFul` 与
`42` / `1` / `0xFF` 是同一个字面量（值不变，见 §1.5）。`f` / `F` 仍不收——
`1.5f` 是 32 位 `float`，当 `f64` 收下会改值。

### 3.17 `struct` 没有成员函数

```elench
struct Point { x: u64, y: u64, }

u64 point_sum(const Point& p) {      // 自由函数，显式传参
    return p.x + p.y;
}
```

`p.sum()` → `E-Type-StructMethod`。**数据与行为分离**是这一版的设计选择
（也是"写出来像 C 还是像 C++"的那条线）。

### 3.18 `public:` / `private:` 被忽略

`struct` 体里写了 `public:` / `private:`：**接受，但完全忽略**——字段全部公开。
内部约定用命名（`_x`）表达。顶层 `public func f()` 报 `E-Keyword-LongForm`（写 `pub`）。

### 3.19 泛型写在名字后

```elench
T pick<T>(T a, T b) {
    return a;
}
```

没有 `template <typename T>`（报 `E-Keyword-Unsupported`）。
泛型**只到函数**，没有类模板 / 特化；调用点做统一（`pick(3, "a")` 报 `E-Type-ArgMismatch`）。

### 3.20 借用规则只落了一部分

```elench
u64 two(vector<u64>& a, vector<u64>& b) {
    a.push_back(1);
    b.push_back(2);
    return a.size() + b.size();
}
```

`two(xs, xs)` **今天能过 check、也能跑**。能查的是：
`const` 形参被改写（`E-Const-Mutate`）、借用还活着就改的一部分。

完整的 Rust 式借用（独占 / 共享、NLL）**还没落地**，而且**不一定该落地**：
C++ 允许别名，强制借用会让"改名即用"直接破产。理由与代价见
[为什么界会松](为什么界会松.md) §7.4。

---

## 4 换个写法（有替代，不必硬搬）

| C++ 写法 | 这里怎么写 | 为什么 |
|---|---|---|
| `for (auto it = v.begin(); it != v.end(); ++it)` | `for (const auto& x : v)` 或三段式下标；**`--compat` 下这种形状直接认**（第 12 条） | 语言里没有迭代器；`--compat` 把它降级成索引循环，`begin` 不带开关时照旧报错（消息里给两种改法） |
| `for (auto it = m.begin(); it != m.end(); ++it)`（`map` / `set`） | `--compat` 下降级成"`m.keys()` 快照 + 下标循环"（第 12b 条，见 §1.3） | 没有迭代器；`map` 也没有位置索引，`m[idx]` 是按**键**查 |
| `switch (x) { case 1: … }` | `match (x) { 1 => { … } _ => { … } }` | 必须带 `_` 兜底 |
| `do { … } while (c);` | `while (true) { if (!c) { break; } }` | 代价：`while (true)` 推不出上界（`E-While-NoBound`） |
| `try { } catch (...) { }` | `T!` + `?` + `match` | 没有异常 |
| `auto x = f();` | `u64 x = f();` | 不推断类型 |
| `class` + 成员函数 | `struct` + 自由函数 | 见 §3.17 |
| `template <typename T>` | `T f<T>(T a)` | 见 §3.19 |
| `std::pair` / `std::tuple` | `(A, B)` + `t.0` | 没有 `pair` 类型；`--compat` 下 `std::pair` / `std::make_pair` / `.first`·`.second` 映射到两元组（见 §P0 第 14 条） |
| `static_cast<T>(x)`（T 与 x 同类型 / 只是加宽） | 直接删掉转换，或写 `x` | 值不变才是拼写；窄化 / 有符号↔无符号 / 整数↔浮点一律拒绝（判据见 §P0 第 11 条），真转换写 `x as T` |
| `new` / `delete` | 直接声明容器 | 没有手工内存 |
| `sizeof(T)` | 写常量 / 用定宽类型 | 没有 `sizeof`（`E-Call-Undeclared`） |
| `goto` | `while` / `break` / `continue` / `return` | 没有 `goto`，也没有标签 |
| `s += "x"` / `s.append("x")` | `s = s + "x"` 或 `text_concat(s, "x")` | 文本是 `vector<u8>`，`+` 是拼接 |
| `printf("%d %s %llu\n", a, s, n)` | `print("{:d} {:s} {:u}", a, s, n)` | **`--compat` 下 `std::printf` 的格式串自动翻**（§P0 第 17 条）；不带开关手写时用 [内建函数](内建函数.md) §花括号格式串 |
| `printf("%08llu", x)` / `%.3f` / `%-5s` | 手写 `print("{:08}", x)` / `print("{:.3f}", v)` / `print("{:<5}", s)` | 宽度 / 精度 / 标志**不会**从 `%` 自动翻过去（没有唯一映射）→ `E-Compat-PrintfFormat`，消息给出可照抄的写法 |
| `printf("%c", c)` | `print("{}", utf8_encode(c))` | C 写**一个字节**；ASCII 逐字节相同，128..255 写成该码点的 UTF-8（本语言文本是 UTF-8，没有裸字节——要裸字节用 `write`） |
| `printf("x")`（不以 `\n` 结尾） | `write("x")` / `write(format(" {:u}", n))` | `print` 一定补一个行尾；不换行只能用 `write`（`--compat` 下自动选） |

---

## 5 写不了（有意不做，别指望补）

| 没有 | 一句话理由 |
|---|---|
| 成员函数 / 运算符重载 / 类模板 | 数据与行为分离，这一版的选择 |
| 迭代器（`begin` / `end` / `*it` / `++it`） | 没有迭代器就没有失效问题 |
| 异常（`throw` / `try` / `catch`） | 失败用 `expected` |
| 宏系统 / 模板元编程 | 与"成本可判定"冲突 |
| `new` / `delete` / `malloc` / `sizeof` | 与值语义冲突 |
| 手工内存 / 裸内存搬运 | 同上；别名只留在指针上 |
| `goto`、标签 | 有替代写法 |
| `reinterpret_cast` / C 风格 cast | 换宽度请用带类型的中间变量 |
| `deque<T>` | `check` 直接拒绝（`E-Type-UnimplementedContainer`） |
| 真指数 `O(2^n)` 作为 target | 恒真承诺等于没有承诺 |

完整清单（含每条实测报错原文）：[能力边界](能力边界.md)。

---

## 6 移植一段 C++ 的四步

1. **改形状**：`func` 删掉、`->` 换成前置、`uint64_t` → `u64`、`std::` 可留可删；
2. **改名字**：`push` → `push_back`、`length` → `size`、`std::pair` → 两元组；
3. **过 `check`**：报错逐条改（每条消息都带改法）；弃用警告也要看（v0.73 会变红）；
4. **补 `.tu` 并写 `target`**：这是唯一需要你"想清楚"的一步，也是这门语言的全部意义。

```bash
elench check app       # 第 3 步
elench cost  app       # 第 4 步：κ vs target
```

> **别做的事**：为了消掉债务而放宽 `target`。工具不会拦你，
> 但那正是"让选手自己报分"——`target` 一旦放宽，这份契约就没有信息了。

---

## 7 三条最小记忆

1. **名字是 C++ 的，语义是可证的那一套**：`size` / `push_back` / `range-for` 都一样，
   但溢出有定义、越界报错、除零是陷阱、成本要写进 `.tu`。
2. **同名不同义只有二十来条，集中在"字符串/容器边界"上**：`size()` 是字节、
   `pop()` 返回值、`find()` 返回下标、`get` 不插入、`int` 是 64 位。
3. **编译器会替你算成本，但不会替你决定算法**：`?!` 只外包"选哪个候选"，
   选哪些候选、怎么划段，仍然是人（和测量）的事。

> **一句话**：**C++ 的写法，Rust 的语义，加上一层能被机器反驳的成本承诺。**
