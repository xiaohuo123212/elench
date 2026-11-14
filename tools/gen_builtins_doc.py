"""gen_builtins_doc.py —— 从 `src/stdlib.py` 生成 `文档/内建函数.md`（唯一来源）。

## 两个来源，一份输出

| 内容 | 来源 |
|---|---|
| 函数名 / 参数个数 / 返回 / 成本类 | `stdlib.FNS`（+ 下面 `SPECIAL` 里的解释器直实现） |
| **备注**（空输入 / 未找到 / 失败时返回什么） | 每条登记的 **`empty=` 字段** |
| 文末「实测：三个容易误用的内建」 | 本文件的 `MEASURED` 常量（理由见下） |

## 为什么备注必须从 `empty=` 生成

2026-11-09 之前这个脚本把备注硬编码成 `""`：`stdlib.py` 里明明写着
`text_find` 的 `empty="未找到 → -1"`、`map_get` 的 `empty="未命中 → Option::None"`，
文档里却是空单元格——**约 60 条内建的"空/未找到时返回什么"就这么蒸发了**。
手抄进 md 会再丢一次，所以只能从登记表来。

`empty=` 的取值约定：`""` 与 `"—"` 都表示**没有特别说明**（表里留空），
其余照抄进备注栏（如「未找到 → -1」）。

## 为什么文末的实测节留在这个脚本里

`min`/`max` 是**二元**（不是容器）、`dedup` 只去**相邻**重复、`binary_search` 未找到返回
`len(xs)`——这三条是 2026-11-09 用 `node bin/elench.js run` **实测**出来的，
`stdlib.py` 里原本没有这些信息。能落进元数据的已经落进去了：

- `dedup` / `binary_search` → `stdlib.py` 的 `empty=`
- `min` / `max` 的元数与成本 → 本文件的 `SPECIAL`

剩下的**代码示例 + 终端原样输出**没有可机读的来源，就作为常量 `MEASURED` 放在本文件里，
这样"重新生成文档"不会把它们冲掉（也就不需要"手改过的行别删"这种约定）。
"""
import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import stdlib  # noqa: E402

RULES = [
    ("文本与 UTF-8", r"^(text_|utf8_|contains$|index_of$|slice$|split_|format)"),
    ("向量", r"^(vec_|copy|copy_slice|fill|fill_range|reverse|rotate|dedup|sum_of|count_of|is_sorted)"),
    ("排序 / 搜索 / 聚合", r"^(sort|binary_search|argmin|argmax|min|max|count_if|fold|filter|map$|for_each$)"),
    ("数学", r"^(sqrt|log2|floor|ceil|round|fabs|abs|popcount|clz|ctz)$"),
    ("字符分类", r"^(isdigit|isspace|isalpha|isalnum|isupper|islower|toupper|tolower)$"),
    ("映射表", r"^(map_|hmap_)"),
    ("集合", r"^(set_)"),
    ("双端队列", r"^(deque_)"),
    ("Option / Result", r"^(option_|ok$|err$)"),
    ("JSON", r"^json_"),
    ("编码与解析", r"^(base64_|parse_)"),
    ("文件与 IO", r"^(open_|read_|write|append_|close|next_line|file_|try_read|eprint|cwd$|fs_)"),
    ("环境与进程", r"^(env_|args|exit$|rand_|print$)"),
]

#: `empty=` 的两种"没有特别说明"写法：都渲染成空备注。
NO_NOTE = ("", "—")

#: 由解释器直接实现的内建（不在 `stdlib.FNS` 注册表里）。
#: 元数/返回/成本是**实测**过的，不能从登记表推——改实现时这里要跟着改。
SPECIAL = {
    "print": {"arity": "1", "ret": "u64", "cost": "1 + n(args)", "varargs": True,
              "note": "（解释器直接实现）；变长：至少 1 个实参，首个实参含 `{}` 时按格式串"
                      "（同 `format`，spec 全集见「花括号格式串」一节），否则空格连接"},
    "sort": {"arity": "1", "ret": "u64", "cost": "n log n", "note": "（解释器直接实现）"},
    "sort_by": {"arity": "2", "ret": "u64", "cost": "n log n × k(f)", "note": "（解释器直接实现）"},
    "map": {"arity": "2", "ret": "any", "cost": "n × k(f)", "note": "（解释器直接实现）"},
    "filter": {"arity": "2", "ret": "any", "cost": "n × k(f)", "note": "（解释器直接实现）"},
    "fold": {"arity": "3", "ret": "any", "cost": "n × k(f)", "note": "（解释器直接实现）"},
    "count_if": {"arity": "2", "ret": "u64", "cost": "n × k(f)", "note": "（解释器直接实现）"},
    # min/max 是二元整数内建：`(T, T) -> T`，成本**常数**（实参求值另计）。
    "min": {"arity": "2", "ret": "any", "cost": "1",
            "note": "`(T, T) -> T`，**不是容器**；常数成本（实参求值另计）；解释器直接实现"},
    "max": {"arity": "2", "ret": "any", "cost": "1", "note": "同上（解释器直接实现）"},
    # `for_each` 是**高阶内建**（回调必须是具名函数），以前只在解释器里、
    # 不在任何清单里 —— 文档写"遍历映射表"却没有任何一栏说明它认哪些容器。
    "for_each": {
        "arity": "2", "ret": "u64", "cost": "n × k(f)",
        "note": "（解释器直接实现）**快照**遍历，返回遍历了几个；"
                "回调**必须是具名函数**。容器 → 回调元数："
                "`Vec` / `array` / `set` / `unordered_set` → `f(x)`（一元）；"
                "`map`（键序）/ `unordered_map`（顺序未指定）/ `HashMap` → "
                "`f(k, v)`（二元）"},
}

#: 「两种映射表」一节：**为什么有两套 API、各自认哪个类型**。
#:
#: 2026-11-10 之前这里没有任何说明：`map_put(m, k, v)` 对语言的 `map<K,V>`
#: 调用可以过 `check`，`run` 才抛 `TypeError`（`_tier_b/structs` K2）。
#: 现在改成 **`check` 阶段明确报错**并给出方法式写法；这一节是把"为什么"
#: 写下来，免得下一个人以为是漏了。
MAP_APIS = r"""## 两种映射表：`HashMap`（自由函数）vs 语言的 `map<K,V>`（方法式）

**它们是两个类型，不是同一个**（`check` 认得出，`ret` 里分别登记成
`HashMap<?,?>` 与 `map<?,?>`）：

| | stdlib 的 `HashMap<K,V>` | 语言的 `map<K,V>` / `unordered_map<K,V>` |
|---|---|---|
| 怎么建 | `HashMap<u64, u64> h = map_new();` | `map<u64, u64> m;`（默认构造＝空表） |
| 怎么用 | **自由函数**：`map_put` / `map_get` / `map_has` / `map_len` | **方法式**：`m.insert(k, v)` / `m.get(k)` / `m.contains(k)` / `m.length()` |
| 底层 | 哈希 + **有界 8 次探测** ⇒ 标量键 `O(1)` 是**真上界**；容器键的哈希是 **O(键长)** | `map` 平衡二叉树 ⇒ `O(log n)`；`unordered_map` 哈希表 ⇒ 标量键最坏 `O(n)`、**容器键 `O(n + 键长)`**，顺序未指定 |
| 遍历 | `for_each(h, f)`，`f(k, v)` | `for_each(m, f)`，`f(k, v)`（`map` 按键序） |

**容器键**（2026-11-12）：语言的 `unordered_map<vector<u8>, u64>` —— 也就是 C++ 的
`unordered_map<string, u64>` —— 现在可以直接写。哈希按**元素逐个混入**
（FNV-1a，`stdlib.hash_key`），冲突后按**逐元素相等**比较（不是对象身份）。
`vector<T>` / `array<T,N>` 都行；`stack` / `queue` / `map` / `set` / 结构体仍被
`check` 拒绝（`E-Type-UnhashableKey`）。

**成本要如实**：容器键的哈希必须**读完整个键**，所以单次
`get/insert/remove/contains` 是 **`O(n + 键长)`**，**不是 O(1)**；κ 对
`unordered_*` 的容器键已经加上 `n(键表达式)` 项（`cost --explain` 里能看到
`n(key)`）。把它当 O(1) 是**低估**，会破上界。stdlib 的 `HashMap` 用同一份
`hash_key()`：`Vec<u8>` / 其它 `list` 键同样是 `O(键长)`，`map_put` /
`map_get` / `map_has` 的 κ 在 `log2(n(h))` 之外**也加了 `n(键)` 项**；
`hmap_*` 的键仍是整数（标量，O(1)）。

**为什么自由函数不扩到语言 `map`**：成本登记是**按名字**的
（`map_put` 登记 `O(1)`）。语言的 `map` 插入是 `O(log n)`——塞进同一个
`O(1)` 登记，`map_put(m, k, v)` 的 κ 就会**低估**（少算比多算危险）。
所以：

```elench
u64 count_pairs(map<u64, u64>& m) {
    m.insert(1, 10);            // ✓ 方法式，O(log n)
    m.insert(2, 20);
    return m.length();
}
```

把同一个 `m` 交给自由函数（`map_get(m, 1)`）会被 `check` 拒绝：
**`E-Type-ArgMismatch`**，消息里直接给出改法（`m.get(1)`）。
**这条以前是"`check` 全绿、`run` 抛 `TypeError`"**，现在编译期就挡。

**`HashMap<K,V> h;`（不带初值）没有默认值**：HashMap 的容量/探测策略由
`map_new()` 决定，凭空造一个半成品没有意义——运行时会报错并指出写法。
"""


#: 「数学内建」一节：**语义规则**（值域边界、符号、`clz(0)`）。
#:
#: 表里的 `empty=` 已经把关键值写进备注栏了，但"为什么这样选"必须写下来：
#: 数学函数是**值域最容易被猜错**的一类（`sqrt(-1)` 是 NaN 还是报错？
#: `round(2.5)` 是 2 还是 3？`clz(0)` 给 64 还是炸？）。三条都按 C 选，
#: 只有 `clz(0)`/`ctz(0)` 选了"报错"——理由见下。
MATH_NOTES = r"""## 数学内建：值域、符号、以及 `clz(0)` 为什么报错

**全部 `O(1)`、不分配（`allocation` 记 0）、不改变阶**——放在循环里，κ 只随
循环圈数走，不随数值大小走。

| 函数 | 签名 | 备注 |
|---|---|---|
| `sqrt(x)` | `(f64) -> f64` | 负数 → **NaN**（C 语义，不是错误、不是 0） |
| `log2(x)` | `(f64) -> f64` | `0.0 → -inf`、负数 → **NaN** |
| `floor(x)` / `ceil(x)` | `(f64) -> f64` | 返回 **f64**（同 C 的 `double`，**不是** `u64`）；`inf`/`nan` 原样 |
| `round(x)` | `(f64) -> f64` | **四舍五入、`.5` 远离零**：`2.5 → 3.0`、`-2.5 → -3.0`（C 的 `round`；**不是** Python 的银行家舍入） |
| `fabs(x)` | `(f64) -> f64` | `-0.0 → 0.0`；NaN 保持 NaN |
| `abs(x)` | `(T) -> T`（整数或 `f64`） | **跟实参类型走**：无符号恒等、有符号取负、`f64` 同 `fabs` |
| `popcount(x)` | `(u64) -> u64` | 按 **64 位**补码数 1 的个数；`popcount(0) = 0` |
| `clz(x)` / `ctz(x)` | `(u64) -> u64` | 前导零 / 末尾零个数，按 **64 位**；`clz(1) = 63`、`ctz(1) = 0`；**`clz(0)` / `ctz(0)` 报错** |

```elench
f64 norm(const vector<f64>& xs) {
    f64 s = 0.0;
    for (const auto& x : xs) {
        s = s + sqrt(x * x);
    }
    return s;
}
```

### `clz(0)` / `ctz(0)`：**报错**，不是返回 64

C 的 `__builtin_clz(0)` / `__builtin_ctz(0)` 是 **UB**（gcc 会给"操作数位数 − 1"
这种没意义的值），本语言**没有 UB 这个概念**，所以只有两条路：

- **返回 64**（"全是前导零"）：看起来顺手，但它等于**编了一个值**——
  `64 - clz(x)` 这种常见写法会静默变成 `64 - 64 = 0`，一个看起来完全合理的
  答案。这正是这个项目最反对的"静默错值"。
- **报错** ✅：`clz(0) 未定义 … 先判空再取`，并且消息里给出 `if (x == 0)` 的写法。

选**报错**。这是本项目里唯一允许"运行期直接炸"的一类：**前置条件违反**，
和 `pop_heap` 空堆、`map.get` 缺键同一档——不是"没实现"，是"这么问没有答案"。

### `target { max: O(sqrt(n)) }` 里的 `sqrt` 不是这个内建

同一个名字在两个位置：

- **`target` / 成本式里**：`sqrt(n)` 是**成本代数的函数符号**（`x^(1/2)`），
  它不执行、只用于判阶（[语法清单](语法清单.md) §成本代数）；
- **`.eln` 的函数体里**：`sqrt(16.0)` 是上面这个**内建**，返回 `4.0`。

两者名字相同、语义无关——写成本式时不要指望它去算值。

### 还没有的

`sin` / `cos` / `tan` / `exp` / `log`（自然对数）/ `pow` 内建（幂用运算符 `**`）/
128 位整数。**FFI 也不是出路**：C ABI 后端不支持 `f64` 参数
（`E-FFI-Unsupported`），所以这批只能进语言本体。
"""


#: 「字符分类」一节：**ASCII 口径**必须写死，因为 C++ 的 `std::isdigit`
#: 是 **locale 相关**的——同一段 C++ 在不同 locale 下可能给出不同答案，
#: 而本语言没有 locale。这一节也说明返回类型为什么是 `bool` 而不是 C 的 `int`。
CHAR_NOTES = r"""## 字符分类：**ASCII 口径**，返回 `bool`

八个纯函数：`isdigit` / `isspace` / `isalpha` / `isalnum` / `isupper` /
`islower` / `toupper` / `tolower`。全 `O(1)`、不分配（`allocation` 记 0）、
`effects {}`——放在循环里，κ 只随圈数走，不随字符值走。

### 三条边界（**写死在这里，不是"看实现"**）

| 输入 `c` | `is*`（分类） | `toupper` / `tolower` |
|---|---|---|
| `c < 128` | 按 ASCII 判（见上表） | 是 `'a'..'z'` / `'A'..'Z'` 就换大小写；**其它原样返回** |
| `c >= 128` | 一律 **`false`** | **原样返回**（不改、不报错） |
| `c < 0`（`i64`） | 先取 **64 位补码**（`-1` → `2^64-1` ≥ 128）→ `false` | 同上：原样返回补码值 |

**为什么 `>= 128` 一律 false / 原样返回**：UTF-8 的多字节序列里每个字节都
`>= 128`，按字节判"字母"必然错；而"按码点判"要先解码，成本就不再是 `O(1)`。
所以本语言只承诺 ASCII，剩下的交给 `utf8_*` 一族。

**为什么 `c < 0` 按无符号处理**：C++ 里把负数传给 `std::isdigit` 是 **UB**
（`glibc` 的实现会去查表、越界读）。本语言没有 UB，所以**定义**成"先转成
64 位无符号，再按上面的表判"——`isdigit(-1)` 是 `false`，`toupper(-1)` 是
`2^64-1`（原样返回）。

### 与 C++ `std::isdigit` 的两处差别

1. **不看 locale**：C++ 的 `std::isdigit` / `std::isalpha` 跟着当前 locale
   走（同一个字节在不同 locale 下可能不同）；本语言**只有 ASCII 一张表**，
   写得出来、跑得一样，才谈得上"成本可算"。
2. **返回 `bool`，不是 `int`**：本语言的 `if` / `while` 条件只收
   `bool` 或指针（`E-Type-Cond`），C++ 那套"非零即真"不存在。所以
   `if (isdigit(c))` 能原样写；但把结果**存进整数**的 C++ 代码要改：

```cpp
// C++：int 结果
int d = std::isdigit(c);
```
```elench
// 本语言：bool 结果（要整数就自己转）
u64 digit_flag(u8 c) {
    bool d = isdigit(c);
    return d as u64;
}
```

### `--compat` 下的 C++ 拼写

`--compat` 打开时，`std::isdigit` 等八个名字在白名单里，**只改名**：

```cpp
std::isdigit(static_cast<unsigned char>(c))
```
```elench
// 名字只改名；`static_cast<unsigned char>(c)` 在 c 是 u8 时折成 c 本身
u64 count_digits(const vector<u8>& s) {
    u64 n = 0;
    for (const auto& c : s) {
        if (isdigit(c)) { n = n + 1; }
    }
    return n;
}
```

`static_cast<unsigned char>(x)` 也只在"`x` 本来就是 `u8`"时折成 `x`
（C++ 里 `string` 的字符是 `char`，这里 `vector<u8>` 的字符是 `u8`，
所以这个转换在本语言里是**纯拼写**；真窄化仍报 `E-Cast-Unsupported`）。

`toupper` / `tolower` 返回 `u64`（字符值），不是 `bool`：

```elench
vector<u8> upper_first(vector<u8> s) {
    if (s.length() > 0) { s[0] = toupper(s[0]) as u8; }
    return s;
}
```

### 不是 Unicode 的大小写

`toupper` / `tolower` 只动 `'a'..'z'` / `'A'..'Z'`。`'é'`（`>= 128`）原样返回，
**不会**变成 `'É'`——要 Unicode 大小写请显式解码（`utf8_chars_vec` 等），
那就不再是 `O(1)` 了。
"""


#: 「stderr 输出；读文本 / 写文本」一节（2026-11-11，语料 `demo/corpus/00_docnums.cpp` 逼出来的）。
#:
#: **为什么单开一节**：`eprint` / `eprintln` 最容易被当成 `print` 的别名，
#: 但它们有两条与 `print` **故意不同**的语义（无分隔符、无插值），
#: 只写在表格的备注栏里一定会被忽略；读/写文本那三条则要把
#: 「失败是中止还是值」「成本为什么是 `ext`」一次说清。
IO_NOTES = r"""## stderr 输出：`eprint` / `eprintln`

`std::cerr << a << b << "\n"` 的等价物。**它不是 `print` 的别名**：

| | `eprint(x, …)` | `eprintln(x, …)` |
|---|---|---|
| 写到哪 | stderr | stderr |
| 换行 | **无** | **恰好一个 LF**（不随平台变成 CRLF） |
| 实参拼接 | **无分隔符**（`<<` 语义） | 同 |
| `{}` 插值 | **不做**（要插值写 `eprintln(format("…{}…", x))`） | 同 |
| 返回 | 写入的**字节数**（`u64`，UTF-8） | 含 LF 的字节数 |
| 效果 / 成本 | `{ io }` / 1 次调用（不建模 IO 延迟） | 同 |

**渲染规则与 `print` 完全相同**（`_text_of` 是唯一实现）：`vector<u8>` / 字符串按
UTF-8 文本、其它容器按空格分隔的数字、标量按十进制。边界：

- 非法 UTF-8 字节按 **U+FFFD** 写出（与 `print` 同口径）；要**原样字节**请用
  `write`（它只写 stdout）；
- `eprintln()` 零个实参是合法的 —— 就是一个空行（C++ 的 `cerr << "\n"`）；
- 宿主写失败（管道断 / 磁盘满）→ **直接中止**，不建模成 `Result`：
  语言层没有"写 stderr 失败"这个值。

```elench
u64 report(const vector<u8>& path) {
    eprint("检查 ", path);
    eprintln("：完成");
    return 0;
}
```

## 读文本 / 写文本：成本为什么是 `ext`

文本在本语言里**就是** `Vec<u8>`，所以没有单独的"text"名字——读文件即读文本：

| 想做的事 | 用哪个 | 失败语义 | 成本 |
|---|---|---|---|
| 读整个文件（中止） | `read_file(path)` | 直接中止 | `ext(read_file)` |
| 读整个文件（值） | `try_read_file(path)` | `Err(Io::NotFound / Denied / Failed)` | `ext(try_read_file)` |
| 覆盖写整个文件 | `write_file(path, data)` | 直接中止 | `ext(write_file)` |
| 按行读 | `open_read` / `next_line` / `close` | `Err(Io::*)` / `Option::None`（EOF） | `ext` / `nret` |
| 文件在不在 | `file_exists(path)` | 1 / 0 | `ext(file_exists)` |

**`ext` 不是 "O(1)"**：文件多大静态推不出来，所以 κ 里保留 `ext(名字)`，
`cost` 判"无法判定"；要可判就在 `.tu` 里写 `external { read_file: O(n); }`
（用登记换掉外部项，代价是**你**为这个上界负责）。也**不要**用
`file_size` + `read_file` 去"证明"读文件是 O(1)——那只是把外部项挪了个位置。

**没有 `ostringstream` / `ifstream`**：它们不是"拼字符串"就是"读整个文件"。
语料里 `ostringstream` 只当**读文件的容器**用（`ss << in.rdbuf(); out = ss.str();`），
等价于一次 `read_file`；字符串拼接用 `text_concat` / `format`，再用
`write` / `eprint` 送出去。流式**写**文件用 `open_write` / `write_chunk` / `close_write`。
"""


#: 「花括号格式串」一节：`format` / `print` 插值的 **spec 全集**。
#:
#: **为什么必须写全**：`src/fmt.py` 的解析器是唯一的实现，文档不全时用户只能
#: 靠试——2026-11-10 修的三条 bug（`{}` 把 f64 截成整数、`{:08x}` 静默补空格、
#: `{:.6f}` 报「本语言还没有浮点类型」）全是"试出来的行为与文档对不上"。
#: 这一节与 `src/fmt.py` 的模块文档、`_HINT` 必须是同一份内容。
FORMAT_SPECS = r"""## 花括号格式串：`format` / `print` 插值的**全部**写法

> **这就是全集**：没列在这里的写法一律 `E-Format-BadSpec`（编译期，`check` 就报），
> **不会**静默降级成另一种填充 / 进制 / 精度。
> `0` 是**零填充标志**，不是宽度的一部分：`{:08x}` → `000000ff`（不是 `      ff`）。

| 写法 | 收的值 | 例子 | 输出 |
|---|---|---|---|
| `{}` | 全部（与 `print(x)` **同一结果**） | `format("{}", 3.14159265)` | `3.14159265` |
| `{:d}` / `{:u}` | 整数（`{:u}` 只收非负） | `format("{:d}", 42)` | `42` |
| `{:x}` / `{:X}` | **非负**整数（无符号十六进制） | `format("{:08x}", 255)` | `000000ff` |
| `{:f}` / `{:.Nf}` | 浮点（`N` = 0..100，默认 6） | `format("{:.2f}", 3.14159265)` | `3.14` |
| `{:s}` | 文本（`vector<u8>` / 字符串） | `format("{:s}", "hi")` | `hi` |
| 宽度 `{:8}` | 全部（文本默认右对齐，同 `printf %8s`） | `format("[{:8}]", 42)` | `[      42]` |
| 零填充 `{:08}` | 数字 | `format("[{:08}]", 42)` | `[00000042]` |
| 对齐 `{:<8}` / `{:>8}` | 全部 | `format("[{:<8}]", "hi")` | `[hi      ]` |
| 组合 `{:010.2f}` | 数字 | `format("[{:010.2f}]", 3.14159)` | `[0000003.14]` |

**默认 `{}` 按值的类型渲染**（与 `print(x)` 逐字一致，这是硬规则）：

| 值 | `{}` 的输出 |
|---|---|
| 整数（`u8`…`u64` / `i8`…`i64`） | 十进制（`42`、`-42`） |
| 浮点（`f32` / `f64`） | 最短往返（`3.14159265`；**不截断成整数**） |
| `bool` | `True` / `False` |
| `vector<u8>` / 字符串 | UTF-8 文本 |
| 枚举 / `Result` | `repr`（`Arithmetic.DivisionByZero`、`Ok(7)`） |
| 其它容器（`vector<u64>` / `array` …） | 空格分隔的数字（`1 2 3`） |

**不支持的写法 = 报错，不是降级**（每条都带一句改写法）：

| 写法 | 为什么不行 |
|---|---|
| `{:b}` / `{:o}` / `{:e}` / `{:g}` | 没有二进制 / 八进制 / 科学计数法 / 通用浮点格式 |
| `{:+}` / `{:^8}` / `{:*<8}` | 不支持符号标志 / 居中 / 自定义填充字符 |
| `{:.2}` | 有精度没形态：必须写 `{:.2f}` |
| `{:0x}` / `{:<08}` | 零填充要么缺宽度、要么与左对齐冲突 |
| `{:08s}` | 文本不能零填充 |
| `{:x}` 给负值或浮点、`{:u}` 给负值、`{:f}` 给整数、`{:s}` 给数字、`{:d}` 给 `bool` | 类型不对：静默截断 / 转码会给出**看起来正常的错值** |

**上限**：宽度 ≤ 10000、精度 ≤ 100（防止 `{:999999999}` 一条格式串分配 GB 级空白）。

**编译器查什么**：洞数 vs 实参数（`E-Format-Arity`）、spec 本身（`E-Format-BadSpec`）、
`{:x}` 的实参是不是容器（`E-Format-ArgType`）；运行期再查"值能不能按这个 spec 渲染"
（同样 `E-Format-ArgType`），**任何一条都不允许静默通过**。
"""

#: 文末「实测」一节：代码示例与终端原样输出，没有可机读来源，只能放这里。
#: 改它等于改公开文档的措辞——请连同实测依据一起改。
MEASURED = r"""## 实测：三个容易误用的内建

> 下面每条都在仓库根跑过 `node bin/elench.js run <模块>`（2026-11-09），
> 引用的输出是**终端原样**，不是按源码推的。`run` 最后还会打一行 `main` 的返回值
> （这些例子里恒为 `0`），下面只摘 `print` 的行。

### `min(a, b)` / `max(a, b)`：两个实参，不是容器

`min` / `max` 是**二元**整数内建：`(T, T) -> T`，两个实参类型必须相同，返回类型跟实参走
（`i64` 进、`i64` 出）。传容器不是"取整个容器的最小值"，是**类型/实参个数错误**。

```elench
u64 main() {
    print(min(3, 5));
    print(max(3, 5));
    return 0;
}
```

实测输出 `3` / `5`。把 `vector<u64>` 传进去（`min(xs)`），`run` 在门禁就停：

```
E-Type-ArgCount @3:14 min 要 2 个实参，给了 1
```

所以要容器最小值，得自己扫一遍（`best = min(best, x)`）——内建调用本身仍是常数成本，
扫描的总成本由循环决定。

### `dedup(xs)`：只去**相邻**重复，返回新长度

`dedup` 是 C++ `std::unique` 语义：**原地**把不重复的元素往前挪，**返回新的有效长度**；
容器**不变短**，**尾部留着覆盖前的残留**。所以对没排序的输入，它几乎去不掉东西。

```elench
u64 main() {
    vector<u64> xs = {1, 1, 2, 1};
    print(dedup(xs));
    print(xs);
    return 0;
}
```

实测：

```
3
1 2 1 1
```

返回的 `3` 是"前 3 个有效"，不是"剩下 3 个不同值"——`xs` 里仍有两个 `1`。
**要真正去重，先 `sort` 再 `dedup`，最后按返回值截断**：

```elench
u64 main() {
    vector<u64> xs = {1, 1, 2, 1};
    sort(xs);
    const u64 k = dedup(xs);
    slice(xs, 0, k);
    print(xs);
    return 0;
}
```

实测输出 `1 2`。（`slice` 是**原地**截断，`lo`/`hi` 越界会被静默夹取。）

### `binary_search(xs, key)`：找不到返回 `len(xs)`，不是 -1

升序表上找得到/找不到，实测：

```elench
u64 main() {
    vector<u64> xs = {1, 3, 5, 7, 9};
    print(binary_search(xs, 5));
    print(binary_search(xs, 4));
    return 0;
}
```

实测：

```
2
5
```

`5` 就是 `xs.length()`：**恰好是最后一个元素的后一位**。**把它当索引用会越界**：

```
E-Index-OutOfRange：下标 5 越界（容器长度 5，合法范围 0..4）
```

这就是 `len` 与 `-1` 的边界差别：`-1` 是"开头之前"，`len` 是"末尾之后"。两者都不是合法下标，
但 `len` 刚好能混进"看起来不大"的判断（例如写 `<= xs.length()`）。**正确用法是先判边界**：

```elench
u64 main() {
    vector<u64> xs = {1, 3, 5, 7, 9};
    const u64 key = 4;
    const u64 i = binary_search(xs, key);
    if (i < xs.length() && xs.at(i) == key) {
        print(i);
    }
    return 0;
}
```

（`&&` 短路，所以 `i == len` 时右边的 `xs.at(i)` 不会执行。）

表**必须已升序**：无序表不报错，只是结果无意义。实测 `{3, 1, 2}` 里找 `1` 返回 `3`（`len`），
找 `2` 返回 `2`（碰巧）。"""


def cat(name):
    for label, pat in RULES:
        if re.match(pat, name):
            return label
    return "其他"


def note_of(reg):
    """把一条登记的 `empty=` 变成备注栏文案（`—` / 空 表示没有特别说明）。

    `->` 统一成 `→`：同一栏里一半 `→` 一半 `->`，看着像两个人写的。
    """
    e = (reg.get("empty") or "").strip()
    if e in NO_NOTE:
        return ""
    return e.replace("->", "→")


#: `ret` 词 → **人读的返回类型**（`stdlib.RET_KINDS` 的显示名）。
#:
#: 为什么要这张表：`ret` 是给机器看的（`typecheck._builtin_ret_ty` 消费），
#: 直接印 `option-elem` / `result-json` 这种词用户读不懂。**每个 `RET_KINDS`
#: 成员都必须有标签**（下面有断言），新增返回类型时不会只改一处。
#:
#: 取值是**纯文本**（生成模板会自己加反引号）：`test_doc_snippets.py` 的
#: `ROW` 正则按 `` | `名字` | 参数 | `返回` | 成本 | 备注 | `` 解析这张表，
#: 标签里混进 `|` 或反引号会让整行匹配不上（那条测试会红）。
RET_LABEL = {
    # ---- 标量
    "u64": "u64",
    "i64": "i64",
    "f64": "f64",
    # ---- 字符分类谓词（`isdigit` 一族）：本语言的 `if` 只收 bool
    "bool": "bool",
    # ---- 跟第一个实参走（`abs`）
    "arg0": "T",
    # ---- 容器
    "vec-u8": "Vec<u8>",
    "vec-u64": "Vec<u64>",
    "vec-vec-u8": "Vec<Vec<u8>>",
    "map": "map<?,?>",
    "set": "set<?>",
    "deque": "deque<?>",
    "hashmap": "HashMap<?,?>",
    "args": "Args",
    "split": "Split",
    # ---- Result
    "result-u64": "u64!",
    "result-vec-u8": "Vec<u8>!",
    "result-vec-u64": "Vec<u64>!",
    "result-reader": "Reader!",
    "result-writer": "Writer!",
    "result-json": "Json!",
    # ---- Option
    "option-vec-u8": "Option<Vec<u8>>",
    "option-elem": "Option<T>",
    "option-arg0": "Option<T>",
    "option-map-val": "Option<V>",
    "option-unknown": "Option<?>",
    "option-payload": "T",
    # ---- 兜底
    "any": "any",
}

_missing_label = sorted(set(stdlib.RET_KINDS) - set(RET_LABEL))
assert not _missing_label, f"RET_LABEL 缺这些 ret 词：{_missing_label}"


def ret_of(reg):
    """`ret` 词 → 文档里的返回类型。"""
    return RET_LABEL.get(reg.get("ret"), reg.get("ret"))


rows = []
for n in sorted(stdlib.FNS):
    d = stdlib.FNS[n]
    rows.append((n, d.get("arity"), ret_of(d), d.get("cost"), note_of(d)))
for n, d in SPECIAL.items():
    rows.append((n, d["arity"], ret_of(d), d["cost"], d["note"]))

groups = {}
for r in rows:
    groups.setdefault(cat(r[0]), []).append(r)

total = len(rows)
n_fns = len(stdlib.FNS)
deprecated = sorted(stdlib.DEPRECATED_BUILTINS)
varargs = sorted([n for n in stdlib.FNS if stdlib.FNS[n].get("varargs")]
                 + [n for n, d in SPECIAL.items() if d.get("varargs")])
with_note = sum(1 for r in rows if r[4])
#: 已废弃的名字按每行 5 个折行——一行十几个反引号名字会让头部那条引用块长得没法读。
dep_chunks = [deprecated[i:i + 5] for i in range(0, len(deprecated), 5)] or [[]]
dep_block = "".join(
    "> " + " / ".join("`%s`" % n for n in chunk)
    + ("。\n" if i == len(dep_chunks) - 1 else "\n")
    for i, chunk in enumerate(dep_chunks))

OUT = f"""# 语言内建函数清单（{total} 个）

> 由 `src/stdlib.py` 与解释器自动导出，**不要手改这张表**——改代码后重新生成。
> 生成方式：`python tools/gen_builtins_doc.py`（本文件即其输出）。
>
> **口径**：**{total} 个内建 = {n_fns} 个注册在 `stdlib.FNS` + {len(SPECIAL)} 个由解释器直接实现**
> （{(" / ".join("`%s`" % n for n in SPECIAL))}）。
> 另有 {len(deprecated)} 个**已废弃**的名字不在本表：
{dep_block}> 它们仍可调用，但 `check` 会报 **`W-Builtin-Deprecated`** 并给出改写法。
>
> **实测校正**：`min` / `max`（二元、常数成本）、`dedup`（只去相邻重复）、`binary_search`
> （未找到返回 `len(xs)`）按 `node bin/elench.js run` 的**实测行为**填，依据见文末
> 「实测：三个容易误用的内建」。

## 怎么读这张表

| 列 | 含义 |
|---|---|
| **参数个数** | 调用时**至少**给几个实参（我们的内建不支持可选参数）；变长的是 {(" / ".join("`%s`" % n for n in varargs))}（`format` 每个 `{{}}` 再加一个） |
| **返回** | `u64` / `Vec<u8>` 是可直接用的值；`Option<…>` / `…!` 是**包装值**，要先 `match` / `option_unwrap` / `?` 解包才能当标量（`u64 x = vec_pop(h)` 会被 `check` 拒绝，见 `E-Type-OptionToScalar`）；`any` 是"类型推不出来"的兜底，今天没有内建用它 |
| **成本类** | `1` 常数；`n` 线性；`nm` 两参数相乘；`log` 对数；`nret` 线性于返回值；`ext` **外部**（未知或由登记表替换） |
| **备注** | 空输入 / 未找到 / 失败 / 越界时**返回什么**（如「未找到 → -1」）；`empty=` 为 `—` 的没有特别说明，留空 |

**两条硬规则**：

1. **`ext` 不会被当作 0**——它会在 κ 里保留为 `ext(name)`，并在报告里打印。
2. **分配量按次数精确计数**（不是渐近），`allocation` 是**精确判定**。

"""

for label in [r[0] for r in RULES]:
    if label not in groups:
        continue
    grow = groups[label]
    OUT += f"## {label}（{len(grow)}）\n\n"
    OUT += "| 函数 | 参数个数 | 返回 | 成本类 | 备注 |\n|---|---|---|---|---|\n"
    for n, a, r, c, note in grow:
        OUT += f"| `{n}` | {a} | `{r}` | {c} | {note} |\n"
    OUT += "\n"

OUT += MAP_APIS + "\n\n"

OUT += MATH_NOTES + "\n\n"

OUT += CHAR_NOTES + "\n\n"

OUT += IO_NOTES + "\n\n"

OUT += FORMAT_SPECS + "\n\n"

OUT += MEASURED + "\n\n"

OUT += """## 没有内建、必须自己写的东西

| 想要 | 怎么做 |
|---|---|
| 浮点**算术** | 有：`+ - * / **`（`f64`/`f32`）。`f64` 除零是 IEEE `inf`/`nan`，不进 `Result` |
| 数学函数 | 有 `sqrt` / `log2` / `floor` / `ceil` / `round` / `fabs` / `abs` / `popcount` / `clz` / `ctz`（全 `O(1)`，见「数学内建」一节）；**没有** `sin` / `cos` / `exp` / `log`，**幂用 `**`** 而不是 `pow` 内建 |
| 128 位整数 | **不提供**（成本按操作数计，`u128` 会让一条指令的成本不再是常数） |
| 正则 | **不提供**（可用 `text_find` / `text_split` 组合，成本可算） |
| 网络 | **不提供**（属于 `ext`；用 `external` 声明并登记） |
| 线程池 | 用 `parallel { … }` 块，成本取各任务之和 |
| 排序的比较器 | `sort_by(xs, f)`，`k(f)` 是函数调用的成本符号 |

## 相关文档

- 语法：[语法清单](语法清单.md)、[语法速查](设计/目前设计/语法速查.md)
- 入门：[入门](入门.md)
- 成本模型：[AI语言设计规格](设计/目前设计/AI语言设计规格.md) 第 4 章
"""

path = os.path.join(ROOT, "文档", "内建函数.md")
if "--check" in sys.argv:
    cur = io.open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if cur == OUT:
        print("  一致：", path)
        raise SystemExit(0)
    print("  ✗ 与生成结果不一致：", path)
    raise SystemExit(1)
io.open(path, "w", encoding="utf-8", newline="\n").write(OUT)
print(f"  已生成 内建函数.md：{total} 条（其中 {with_note} 条带 `empty=` 备注）")
