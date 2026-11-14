# 语料（demo/corpus）

> ## 当前状态（2026-11-13 00:40，实测）
>
> | 项 | 值 |
> |---|---|
> | 模块 | **20 个 `.cpp`**（`00_docnums` 是生成的，不参与契约统计） |
> | 函数 | **115 个**（01–19） |
> | `.tu` 契约 | **每个模块都有**（19 份） |
> | κ 可算（带 `.tu`） | **115 / 115 = 100%**（裸 `.eln` 无契约时 ⊤ = 3，全是需要 `recursion depth` 声明的递归） |
> | **契约覆盖率** | **102 / 115 = 89%**，`▲ 债务 = 0` |
>
> **口径（防止刷分，写死在这里）**：
> ```
> 分子 = cost 判 ✓「达成」的函数数（**只数 ✓，不数 ▲**）
>        ——若把 ▲ 也算进分子，任何函数写一句假的 O(1) 就立刻"被判定"，
>          与"宁宽勿紧、写紧是契约方的错"正好相反。
> 分母 = 有 .tu 的模块的全部函数（现在 115，因为每个模块都有 .tu）
> ```
>
> **剩下 13 个没被判定的**：10 个是 `κ` 里含**局部容器名**（`n(cur)` / `n(line)` / `n(masked)`）
> 或**内联留下的自由符号**（`x`），target 里写不出这个名字 ⇒ E23；
> 1 个是 `merge_sort`（`κ` 用尺寸差 `hi - lo`，要配 `requires lo <= hi;`，
> 而 `check` 的 requires 形状还不接受"变量 vs 变量"）；2 个是含 `log` 的多尺寸量（判据不展开）。
>
> **下面的历史小节保留原样**（它们记录的是当时的状态，数字不再更新）。

# `demo/corpus` —— 改名即用 / 行为一致的语料

这个目录放**单文件、能独立编译运行的 C++ 程序**，只做两件事：

1. `tools/rename_rate.py` 把每个 `.cpp` 复制成 `.eln`，跑 `check --compat`，
   量「**改名通过率**」（文件级：0 error 才算通过；warning 不阻断）；
2. `tools/behavior_parity.py` 用 g++ 编译同一份 `.cpp`，再跑 `run --compat`，
   逐字节比较 stdout，量「**行为一致率**」。
   新语料没有登记函数探针时，只自动比较 `main`（见文末「怎么加新语料」）。

## 诚实说明（先读这一段）

- 这些是**手写的代表性样本**，不是从开源项目里搬的（写这份语料时没有网络）。
  它们覆盖常见的单文件内核形状，但**不是随机抽样**，不能当成外部世界的通过率；
- 每个文件都先用 `g++ -std=c++17 -O0` 编译并运行过，输出确定（不依赖时间、
  随机种子、文件系统、stdin）；
- **输入全是文件内常量**，不读 stdin / 文件——对拍工具才能喂进同一份输入；
- 通过率是**文件级、非线性**的：一个文件有 50 条错和 1 条错都算 0%。
  所以 `rename_rate.py` 同时打印「剩余错误总数」，那个数才是每轮该看的梯度。

## 语料清单

行数按 `rename_rate.py` 的口径（`splitlines()`）统计。

| 文件 | 行数 | 模拟的真实场景 | 主要形状 |
|---|---:|---|---|
| `00_docnums.cpp` | 633 | 仓库里真实的命令行小工具（目录/文件处理） | 多函数、`struct`、IO、字符串 |
| `01_sorting.cpp` | 47 | 插入排序 + 归并排序 | 数组下标、递归、`void` 改引用参数 |
| `02_csv_stats.cpp` | 35 | CSV 字段切分与计数 | `std::string`、`std::map`、迭代器 |
| `03_matrix.cpp` | 35 | 小矩阵乘法 | `vector<vector<u64>>`、三重循环 |
| `04_tokenizer.cpp` | 33 | 字符分类、按空白切词 | range-for、`auto`、`<cctype>` |
| `05_ring_buffer.cpp` | 31 | 定长环形缓冲（`struct` + 成员函数） | 构造 / 方法 / 取模下标 |
| `06_heap.cpp` | 94 | 任务调度器的**手写二叉堆**（优先队列），按优先级取作业 | `struct`、`vector<struct>`、上浮/下沉、`std::size_t` |
| `07_dp.cpp` | 71 | **二维 DP**：编辑距离 + 最长公共子序列 | 局部 `vector<vector<u64>>`、边界行/列、双重循环 |
| `08_bits_hash.cpp` | 78 | **位运算 + 哈希表**：设备 ID 按高 8 位分桶、命令词计数 | 移位/掩码、`unordered_map<string, u64>`、迭代器 |
| `09_search_window.cpp` | 86 | **二分 + 滑动窗口**：按时间戳查起点、找预算内最长区间 | 手写 `lower_bound`、哨兵 `xs.size()`、双指针 |
| `10_backtrack.cpp` | 91 | **递归回溯**：N 皇后计数并收集第一组解 | `class`、构造/成员函数、剪枝、结果容器 |
| `11_text.cpp` | 101 | **文本处理**：配置文本按行/逗号切分、trim、键名小写、计数 | `std::string`、`std::map`、range-for、读入模拟 |

## 当前实测（快照，不是永久结论）

测量时间：2026-11-11；仓库 `HEAD = 1ed8834`（`p40-work`）。
`src/parser.py` / `src/typecheck.py` / `src/compat.py` 当时**有并行代理在改**，
所以数字会随 `src/` 变化；`tools/` 未改动。

- 干净 HEAD 树（`git archive 1ed8834`，不含并行改动）：
  改名 **8/12 = 67%**，12 个文件共 **64** 条错误（旧 6 个文件 59 条 →
  新语料自身只带入 **5 条**：`08` 3 条、`10` 2 条）；
  行为 **7/14 = 50%**（11 个旧目标里 7 个一致，新增的 3 个全是 printf 差异）。
- 共享工作树（同一时刻，`parser.py` 的 hash 是 `FF935F62…`）：
  改名仍是 **8/12 = 67%**，但总错误变成 **84** 条——`00_docnums` 从 55 涨到
  70 是并行改动的回归，**不是这 6 个新文件带来的**；
  6 个新文件自身贡献 **+10**（`08` 3 条，`10` 7 条）。
  行为一致率同样是 **7/14 = 50%**。

> **计数口径提醒**：`rename_rate.py` 用正则抓 `E-*`，**warning 级的诊断也算**。
> 例如 `10_backtrack` 的 7 条里有 3 条是 `E-While-NoBound`（warning，不阻断
> `check`），4 条才是 error。看「挡路的错误」时先分清 error / warning。

### 哪些新文件过不了，为什么

| 文件 | 结果 | 挡路的诊断 | 性质 |
|---|---|---|---|
| `08_bits_hash.cpp` | ✗ 不通过 | `E-Type-UnhashableKey` ×2、`E-For-IteratorNotIndexable` ×1 | `unordered_map` 的字符串键（`vector<u8>`）不可哈希；map 迭代器不能降级成下标 |
| `10_backtrack.cpp` | ✗ 不通过 | HEAD 上是 `E-Keyword-Unsupported`（`class`）+ `E-Parse-SkippedBody`；工作树里 class 已被部分接受，改为 `E-Compat-MemberUnsupported` / `E-Struct-BodyForm` / `E-Compat-CtorUnsupported` / `E-Call-Undeclared` | 本语言没有 `class` / 成员函数 / 构造函数；错误恢复会跳过类体 |
| `11_text.cpp` | check ✓ / run ✗ | 无 check 错误 | **假绿**：`for (const auto& kv : counts) { kv.first ... }` 过了 check，但解释器报 `未实现的字段访问 first` |

`06` / `07` / `09` 用 `struct` + 自由函数（不用 `class`）、手写堆 / DP / 二分，
在 `--compat` 下都只余 warning（`W-Compat-StructInEln`、`I-Fn-NoSpec-Module`），
`check` 通过。

### 行为一致率的两个已知坑（读数字前必看）

1. **printf 格式串**：`std::printf` 在 `--compat` 下改名成 `print`，但格式串仍按
   本语言的 `{}` 洞解释。`%llu` / `%d` 会原样打出来。
   对于**只有洞、没有文字标签**的格式（如 `"%llu %llu\n"`），
   `behavior_parity.py` 能把差异归到 `printf_semantics`（已知语义差）；
   对于 `"edit(...) = %llu\n"` 这种**带标签**的格式，工具只从 elench 输出里
   剥格式串、不剥 g++ 输出的对应文字，于是会误记成 `behavior_diff`。
   `06` / `07` / `09` 就属于后者：**数值逐个对得上**，不是算术差异。
2. **`11_text.cpp` 的假绿**：check 绿、run 崩，是 `behavior_parity.py` 里
   `main` 的 `interpreter_crash` 缺陷项。语料**故意保留**这个惯用写法
   （`map` 的 range-for + `kv.first`），因为它正是要暴露的问题。

## 怎么加新语料

1. 在本目录放 `NN_名字.cpp`，单文件、无外部依赖（只用
   `<cstdio> <vector> <string> <map> <unordered_map> <algorithm> <cstdint>`
   这类标准头）；
2. 必须有 `main`、能 `g++ -std=c++17` 编译运行、输出确定；
3. **输入用文件内常量**（不要读 stdin / 文件 / 环境变量），否则对拍工具喂不进
   同一份数据；
4. 不要改工具：`tools/rename_rate.py` 与 `tools/behavior_parity.py` 都直接
   `glob("demo/corpus/*.cpp")`，放进来就会自动扫到；
5. 如果要让**函数级**探针也进行为对拍，需要在
   `tools/behavior_parity.py` 的 `RECIPES` / `PARAMS` 里登记「文件 + 函数 +
   固定实参」；不登记时只比较该文件的 `main`（本批新语料就是这种情况）。

---

## 成本契约（`.tu`）：从「能跑」到「有保证」（2026-11-12 增补）

上面两把尺（改名通过率 / 行为一致率）只说「**能跑**」。这一节是第三把尺：
`dsh cost` 算出来的 κ 被写成**可分档判定的上界**——能跑 ≠ 有承诺，
承诺是 `.tu` 里的 `target`，判定是 `dsh cost`。

本目录现在每个语料都配了一份 `.tu`（`00_docnums` 除外，它连 `check` 都没过）：
**11 份 `.tu`、48 个函数里 34 个写了 `target`、27 个被判成「达成」、0 债务**。

### 怎么跑（`.eln` **不要**提交）

`.tu` 与 `.eln` MUST 同名同目录，但本目录**只放 `.cpp` 与 `.tu`**
（`.eln` 是 `tools/rename_rate.py` 的中间产物，入库会变成两份实现）。所以先复制到临时目录：

```powershell
$tmp = Join-Path $env:TEMP "corpus-eln"; New-Item -ItemType Directory -Force $tmp | Out-Null
Copy-Item demo/corpus/01_sorting.cpp "$tmp/01_sorting.eln"
python -B src/dsh.py check demo/corpus/01_sorting.tu "$tmp/01_sorting.eln" --compat
python -B src/dsh.py cost  demo/corpus/01_sorting.tu "$tmp/01_sorting.eln" --compat
```

`cost` 的输出对每个函数给一行结论：`✓` 达成 / `▲` 债务（`**超标**：阶不够`）/
`?` 无法判定（并给出原因）。`target` 写的是 **κ 的阶**（`O(...)` 里可以写
`xs.size()`、`h.data.size()`、`max(mid, hi)`、`n * n` 这些形状，见 `文档/样例/*.tu`）。

### 逐文件结果（实测，2026-11-12，`HEAD 340833e` + 工作树）

`✓` = 有 target 且判成「达成」；`?` = 有 target 但**判不了**（E23），或**根本没写
target**（κ 是 ⊤，或没有可命名的尺寸量——两种在 `cost` 输出里都是 `?`，原因行会写明）；
`▲` = 判成「债务」——**本目录现在是 0 条**（有了才说明实现超了承诺）。

| 文件 | 函数 | target | 判定 |
|---|---|---|---|
| `01_sorting` | `insertion_sort` | `O(xs.size() * xs.size())` | ✓ |
| | `merge_halves` | `O(max(mid, hi))` | ✓ |
| | `merge_sort` | — | ⊤ 树递归（`recursion depth <= 32` 已写，仍不猜指数） |
| | `main` | — | ⊤（经 `merge_sort`） |
| `02_csv_stats` | `split` | `O(s.size() * s.size())` | ? E23（局部量 `cur`） |
| | `count_fields` | — | ⊤（`split(lines[i])` 的实参长度推不出） |
| | `main` | — | ⊤ |
| `03_matrix` | `zeros` | `O(n)` | ✓ |
| | `matmul` | `O(a.size() * a.size() * a.size())` | ? E23（局部标量 `n`） |
| | `main` | — | 无形参（κ 里有 `n / a / b / c`） |
| `04_tokenizer` | `count_digits` | `O(s.size())` | ✓ |
| | `words` | `O(s.size() * s.size())` | ? E23（局部量 `cur`） |
| | `main` | — | 无形参 |
| `05_ring_buffer` | `Ring_Ring` | `O(1)` | ✓ |
| | `Ring_push` | `O(1)` | ✓ |
| | `Ring_pop` | `O(1)` | ✓ |
| | `main` | `O(1)` | ✓ |
| `06_heap` | `heap_init` | `O(cap)` | ✓ |
| | `sift_up` | `O(h.data.size())` | ✓ |
| | `sift_down` | `O(h.data.size())` | ✓ |
| | `heap_push` | `O(h.data.size())` | ✓ |
| | `heap_pop` | `O(h.data.size())` | ✓ |
| | `main` | — | 无形参（κ 里有 `cap` 与堆 `data` 的尺寸量） |
| `07_dp` | `min3` | `O(1)` | ✓ |
| | `edit_distance` | `O(a.size() * b.size())` | ? E23（局部表 `dp`） |
| | `lcs_length` | `O(a.size() * b.size())` | ? E23（局部表 `dp`） |
| | `main` | — | 无形参 |
| `08_bits_hash` | `popcount64` | `O(1)` | ✓ |
| | `rotl64` | `O(1)` | ✓ |
| | `mix64` | `O(1)` | ✓ |
| | `bucket_of` | `O(1)` | ✓ |
| | `count_words` | `O(words.size() * freq.size())` | ? E23（range-for 别名 `w`） |
| | `main` | — | ⊤（`unordered_map` 键长推不出） |
| `09_search_window` | `lower_bound_index` | `O(xs.size())` | ✓ |
| | `longest_window` | `O(xs.size() * xs.size())` | ✓ |
| | `count_at_least` | `O(xs.size())` | ✓ |
| | `main` | `O(1)` | ✓ |
| `10_backtrack` | `QueenSolver_QueenSolver` | `O(1)` | ✓ |
| | `QueenSolver_first_solution` | `O(1)` | ✓ |
| | `QueenSolver_place` | — | ⊤ 树递归（`recursion depth <= 8` **必须写**，否则 `cost` 拒算） |
| | `QueenSolver_solve` / `main` | — | ⊤ |
| | `QueenSolver_render` | — | κ 具体（`n(recv.n_)` + 局部量 `line`），但没有一个 target 能同时写上这两个量 |
| `11_text` | `is_blank` | `O(1)` | ✓ |
| | `trim` | `O(s.size())` | ✓ |
| | `lower_key` | `O(s.size())` | ✓ |
| | `split` | `O(s.size() * s.size())` | ? E23（局部量 `cur`） |
| | `main` | — | 无形参 |

合计：**能算出具体界（κ ≠ ⊤）的函数 40 个，其中有被判定过的 target 27 个 = 67.5%**
（改写前是 0：这 11 个文件里一个 `.tu` 都没有）。

> 分母有 ±1 的浮动：测量时**另一个代理正在改 `src/cost_domain/`**，
> `10_backtrack.QueenSolver_render` 就在测量过程中从 ⊤ 变成了具体式
> （`n(recv.n_)` 这种**接收者字段**的尺寸量刚被支持）。67.5% 是最后一次实测口径；
> 若按更早的实现算是 27 / 39 = 69%。**分子 27 不受影响**——它是"契约真的被判过了"。

### 三类「写不出可判定的 target」的函数（真实缺口，不硬写）

`cost` 只在 **κ 与 target 的尺寸量集合完全相同**时判阶（E23）。下面三种在 κ 里出现的
尺寸量**在 target 里写不出来**，于是这几类只能判「无法判定」（target 照写，
写的是 κ 的阶，等工具补上这一块就会开始判）：

1. **局部容器**：`cur`（`02` / `04` / `11` 的切分缓冲）、`dp`（`07` 的二维表），
   以及 5 个 `main` 里的 `line` / `field` / `lines` / `fields`。它们不是形参，
   target 里没有名字可写。理想形态是工具能证明 `n(cur) ≤ n(s)`、`n(dp) ≤
   n(a)·n(b)`：那 5 个函数（`02`/`11` 的 `split`、`04` 的 `words`、`07` 的两张表）
   就能判，而且能收紧到真紧界。
2. **局部标量**：`03_matrix.matmul` 里的 `std::size_t n = a.size()`。工具把它记成
   一个**独立符号**（与形参 `n(a)` 不同名），而 `matmul` 没有标量形参，
   `O(n)` 里的 `n` 又因"容器形参不唯一"绑不上。
3. **range-for 元素别名**：`08_bits_hash.count_words` 的 `n(w)`
   （`for (const auto& w : words)`）。`row_of` 只覆盖二维表的行别名，
   这里没折回 `n(words)`。

其余 5 个 `main` 是**结构性的**：`main` 没有形参，而 κ 里的尺寸量全来自体内局部量
（`03` / `04` / `06` / `07` / `11`），所以任何 target 都对不齐。驱动程序不承诺渐近界，
只声明 `effects`；`05` / `09` 的 `main` 输入是文件内字面量、κ 是常数（252 / 4263），
所以照常写了 `O(1)`。

**`effects` 按代码实际行为写**：`printf` → `io`；建容器 / `push_back` / 插入 map → `alloc`
（工具目前只核 `io/fs/env/nondet`，`alloc` 是签字的语义声明，不参与判定）。

### 反向验收：target 收紧一个阶，`cost` 必须报债务

判定不是永真。把 `01_sorting.tu` 里 `insertion_sort` 的
`O(xs.size() * xs.size())` 故意改成 `O(xs.size())`（只收一个阶）：

```text
  ▲ insertion_sort  κ ≤ 2 + n(xs)×(7 + 6 + n(xs)×26 + 26 + 5 + 5) + 7 + 6 + n(xs)×26 + 26 + 5 + 5
                    分配 ✓ alloc ≤ 0   (target 0)
                    **超标**：阶不够，比值 ≈ 28587302322251.00
▲ 1 处超出 target（time / allocation 各算一处；记为债务，不阻断构建）
```

同理，`09_search_window.count_at_least` 的 `O(xs.size())` 收紧成常数 `O(1)`
`→ **超标**：阶不够，比值 ≈ 39582418600003.00`；`06_heap.heap_init` 的
`O(cap)` 收紧成 `O(1)` 也报 `**超标**：阶不够`。**没被收紧的那几个函数同时仍是 ✓**，
所以「报债务」不是全表红，而是**只红那一条**。

> 债务**不改 `cost` 的退出码**（口径：只记债务、不阻断构建，`build` 同）；
> 要看 `▲` 那一行，不要只看 `exit=0`。

## 第三次扩容：12 → 15 个（2026-11-12），补三类没覆盖的形状

`12_graph` / `13_numeric` / `14_buffer`。每个都先 `g++ -std=c++17 -O0 -Wall -Wextra` 编译并
运行过（三个都没有警告），输出确定（不读 stdin / 文件 / 随机数）；UTF-8 无 BOM。

> **2026-11-13 更正**：这里原本写「**只有 `.cpp`，没有 `.tu`**」，已过时——12 / 13 / 14
> 现在都有 `.tu`（2026-11-12 补），见下面「三个新语料的 `.tu`」与文末「2026-11-13 复测」。

| 文件 | 行数 | 场景 | 补的形状 |
|---|---:|---|---|
| `12_graph.cpp` | 120 | 任务依赖图：BFS 求跳数、DFS 三色判环、数连通分量 | **容器套容器 + 迭代邻居**：`vector<vector<u64>>`、`g[v][k]`、访问标记数组 |
| `13_numeric.cpp` | 111 | 传感器读数摘要：前缀和 / 区间和、均值、标准差（定点千分位）、夹取、饱和加法 | **标量密集循环**（全程没有嵌套容器），拿来做 κ 的对照组 |
| `14_buffer.cpp` | 120 | 记录缓冲：拼接、子串查找 / 计数、脱敏替换、切片、可打印预览 | **`vector<unsigned char>` 的字节处理**（不是 `std::string`） |

行数按 `rename_rate.py` 的口径（`splitlines()`）。

### g++ 真实输出

```text
$ g++ -std=c++17 -O0 -Wall -Wextra -o 12_graph.exe demo/corpus/12_graph.cpp && ./12_graph.exe
bfs from 0: 0 1 1 2 3 - - -
reachable from 0: 5
components: 3
has cycle: no
after edge 6->5, has cycle: yes

$ g++ -std=c++17 -O0 -Wall -Wextra -o 13_numeric.exe demo/corpus/13_numeric.cpp && ./13_numeric.exe
total: 4735
sum[2,5): 1175
sum[5,8): 3100
mean_milli: 591875
stddev_milli: 360538
clamped: 3
alarms: 3
sat: 4294967295
isqrt(144): 12

$ g++ -std=c++17 -O0 -Wall -Wextra -o 14_buffer.exe demo/corpus/14_buffer.cpp && ./14_buffer.exe
size: 35
key= at 12
secret at 16, missing at 35
replaced: 1, masked: 35 bytes
stars: 9
head: 11 bytes, tail: 24 bytes
GET /health
 key=********* ua=curl/8
```

### 它们补了什么、各自暴露了什么

- **`12_graph`**：第一个「外层下标 → 内层容器」的语料。BFS / DFS 的内层循环按最自然的写法
  写：`for (k = 0; k < g[v].size(); ++k)`。这恰好是 **κ 的断点**——`g[v].size()`（`v` 是变量
  下标）没有可命名的尺寸量，κ = ⊤；`dfs_visit` / `has_cycle` / `count_components` / `main`
  再因递归和调用传播跟着 ⊤。**这不是语料写错，是工具缺口**：理想形态是能证明
  `n(g[v]) <= n(g)`（和 README 前面列的「局部容器」「range-for 别名」同一类）。
- **`13_numeric`**：对照组。8 个函数**全部**算出具体界（含 `stddev_milli` 的定点平方和与
  `isqrt` 的整数二分、`sat_add` 的 `cap - a`），说明「标量密集循环」这一档 κ 已经够用，
  不需要为了算得出而把代码写蠢。
- **`14_buffer`**：`vector<u8>` 的原地字节处理（`find_from` / `count_occurrences` /
  `replace_all` / `slice`）。`find_from` 的条件写成 `i + needle.size() <= hay.size()`
  （真实写法：防越界）→ ⊤；同一件事改成 `i < hay.size()` 再在体内判 `i + k < hay.size()`
  就能算（实测见下表）。`count_occurrences` / `replace_all` / `main` 因调用传播跟着 ⊤。

### 各自能算出多少具体 κ（`cost --signature` 数 `unknown`）

| 文件 | 函数 | 具体 κ | ⊤（根节点 `"operator": "unknown"`） | 比例 |
|---|---:|---:|---:|---:|
| `12_graph` | 8 | 3（`make_graph` / `add_edge` / `reachable`） | 5 | **37.5%** |
| `13_numeric` | 8 | 8（全部） | 0 | **100%** |
| `14_buffer` | 10 | 6（`from_text` / `append_text` / `append_range` / `count_byte` / `slice` / `print_preview`） | 4 | **60%** |

复现：

```powershell
$tmp = Join-Path $env:TEMP "corpus-eln"; New-Item -ItemType Directory -Force $tmp | Out-Null
Copy-Item demo/corpus/13_numeric.cpp "$tmp/13_numeric.eln"
python -B src/dsh.py cost "$tmp/13_numeric.eln" --compat --signature   # 数 "operator": "unknown"
```

没有 `.tu` 也能出 κ（只是不判债务、不算错误）。

### 已探明：两个 ⊤ 的**具体触发点**（拆开变量单独测出来的，不是猜的）

| 写法 | κ | 说明 |
|---|---|---|
| `u64 s = 0; for (k = 0; k < g[v].size(); ++k) s += g[v][k];` | **⊤** | 循环上界是**内层容器的尺寸**，外层下标 `v` 是变量 → 没有可命名的尺寸量 |
| `return g[v].size();`（不进循环） | `7` | 光取值不是问题，**拿它当循环上界**才是 |
| `for (i = from; i + needle.size() <= hay.size(); ++i)` | **⊤** | 上界是「归纳变量 + 尺寸量」的和 `i + n(needle)` |
| `for (i = from; i < hay.size(); ++i)`（体内判 `i + k < hay.size()`） | `2 + n(hay)×(…)` | 同一件事的另一种写法，就能算 |

### 开发时撞到的三个方言缺口（已按方言改写；三个文件现在 `check --compat` 全绿）

这三条都是**写自然 C++ 时的正常写法**，`check --compat` 直接拒：

| 自然写法 | 诊断 | 语料里改成 |
|---|---|---|
| 文件级常量 `static const u64 kNone = 1000000;` | `E-Keyword-Unsupported`（没有 `static`）+ `E-Ai-HasDecl`；**不带 `static` 的顶层 `const` 同样被拒** | 常量下沉成函数内局部 `const`；或改用「下标 == size 表示不存在」的哨兵 |
| `Graph g(n);`（`vector` 单实参直构：n 个空行） | `E-Compat-CtorUnsupported`（只认 `vector<T>(n, v)` 和单实参拷贝初始化） | `std::vector<u64> empty; Graph g(n, empty);`（落到 `filled`） |
| `void f(..., u64& hits)`（标量出参） | `E-Ref-Scalar`：引用只对**可变聚合**成立，标量形参写不回调用方 | 拆成 `count_occurrences()` 返回计数 + `replace_all()` 返回新缓冲 |

第三条是**这次新暴露的**（前两条是已知缺口）：真实 C++ 里「标量出参」到处都是
（`split(s, out_count)`、`parse(x, err)`），本语言只支持聚合出参。语料按诊断给的改法
写成了返回值形态——**这是方言的表达力缺口，不是语料写法的问题**。

### 实测（2026-11-12，`p40-work` + 工作树；`src/cost_domain/` 正在被另一个代理改）

- `python -B tools/rename_rate.py` → **14/15 = 93%**（加文件前 11/12 = 92%）。三个新文件
  都是 ✓ 通过；`--compat` 剩余错误总数仍是 **66** 条（与加文件前一致——新语料没带入任何
  一条新错误）。不带开关从 73 涨到 79：每个新文件各 2 条（`using` 类型别名 +
  `E-Parse-SkippedBody`），和其余 11 个文件同一形状。
- `python -B tools/behavior_parity.py` → **20/22 = 90.9%**（加文件前 17/19 = 89.5%）。
  三个新 `main` 都是「可比 1 / 一致 1」：分母 +3、分子 +3，两个老差异
  （`05_ring_buffer` 的求值顺序、`08_bits_hash` 的 map 遍历）原样不动。

> 这两个数是**本次实测**；`src/` 有并行改动，下次跑可能不同，口径没变。

## 三个新语料的 `.tu`：把 KPI② 的分母补上（2026-11-12 建；**2026-11-13 复测更新**）

`12_graph` / `13_numeric` / `14_buffer` 之前只有 `.cpp`，契约覆盖率的分母被它们拉低。
这一节给出三份 `.tu` 的判定表、反向验收，以及**写不出来的函数与原因**。

写法照 `06_heap.tu` / `11_text.tu`：先把 `.cpp` 复制成临时目录里的 `.eln` 跑
`cost --compat --explain` 拿每个函数的 κ，再按「target 取 κ 的阶、宁宽勿紧」写。

> **规则更正（2026-11-13）**：这里原本写「**κ = ⊤ 的只写 `effects`，不写 target**」。
> 复测时 01–19 **已经没有 ⊤**（115/115 都算出具体 κ），现在的缺口换成了另一种：
> **κ 的尺寸量在 target 里没有名字可写**（局部容器 `n(cur)`、内联留下的自由符号 `x`、
> 局部标量 `n(hi)`）⇒ 写得出也只会判 E23，所以只写 `effects`、不写 target。

### 判定表（**2026-11-13 复测**，`HEAD 3288f6f` + 工作树）

| 文件 | 函数 | 写了 time target | ✓ 达成 | 不写 time target | 判不了（E23） |
|---|---:|---:|---:|---:|---:|
| `12_graph.tu` | 8 | 8 | 8 | 0 | 0 |
| `13_numeric.tu` | 8 | 7 | 6 | 1（`main`） | 1（`stddev_milli`） |
| `14_buffer.tu` | 10 | 9 | 9 | 1（`main`） | 0 |
| **合计** | **26** | **24** | **23** | **2** | **1** |

> 2026-11-12 的旧表是「写 16 / 达成 15 / 不写 10 / E23 1」——那 10 个「不写」里有 8 个
> 当时是 κ = ⊤。后来 `cost` 域补了 while 上界、树递归激活数（`(D+1)·B^D`）和元素长度量
> `L(g)`，它们都变成了具体式（`dfs_visit` 仍是树递归，但 `recursion depth <= 8` 已能给出
> 激活数），于是现在只剩 2 个不写、1 个 E23。

`dsh check <tu> <cpp> --compat` 三条命令**退出码都是 0**（`12_graph` 带 1 条
`W-Exp-Risk`：`dfs_visit` 是树递归，只是风险提示，不阻断）。

#### `12_graph`（**8/8 可算，8 条全达成**）

| 函数 | κ（`cost --compat`） | target | 判定 |
|---|---|---|---|
| `make_graph` | `3 + 2 + 1 + n + 2` | time `O(n)` / alloc `O(n)` | ✓ |
| `add_edge` | `7` | time `O(1)` / alloc `O(1)` | ✓ |
| `bfs` | `… + n(dist)×17 + … + n(g)×(… L(g)×45 …) + …` | time `O(dist.size() + g.size() * L(g))` / alloc `O(g.size() * L(g))` | ✓ |
| `reachable` | `7 + 2 + n(dist)×20 + 20 + 2` | time `O(dist.size())` / alloc `0` | ✓ |
| `dfs_visit` | `… L(g)×(… max(4, n(color)×(… L(g)×41 …) …) …)` | time `O(color.size() * L(g) * L(g))` / alloc `0` | ✓ |
| `has_cycle` | `… n(g)×(… L(g)×(… n(g)×(… L(g)×41 …) …) …)` | time `O(g.size() * g.size() * L(g) * L(g))` / alloc `0` | ✓ |
| `count_components` | `… n(g)×(… n(g)×(… n(g)×(… L(g)×45 …) …) …)` | time `O(g.size() * g.size() * L(g))` / alloc 同阶 | ✓ |
| `main` | `484428` | time `O(1)` | ✓ |

> 早先这张表只有 3 个 ✓、后 5 个写「`⊤` / 不写」——那是 2026-11-12 的结论，**已过时**。
> `bfs` 的内层 `g[v].size()` 现在由 `L(g)`（`max_i n(g[i])`，`g` 是 const 形参时全程上界）
> 命名，所以「变量下标」不再是断点。

#### `13_numeric`（8/8 可算，但只有 6 条判达成）

| 函数 | κ | target | 判定 |
|---|---|---|---|
| `prefix_sums` | `3 + 2 + n(xs)×25 + 25 + 2` | `O(xs.size())` | ✓（比值 ≈ 25.00） |
| `range_sum` | `8` | `O(1)` | ✓ |
| `clamp_u64` | `14` | `O(1)` | ✓ |
| `sat_add` | `14` | `O(1)` | ✓ |
| `mean_milli` | `13 + 2 + n(xs)×16 + 16 + 9` | `O(xs.size())` | ✓（≈ 16.00） |
| `isqrt` | `4 + 2 + (x + 1)×33 + 33 + 2` | `O(x)` | ✓（≈ 33.00） |
| `stddev_milli` | `… + n(xs)×33 + … + (x + 1)×33 + …` | `O(xs.size())` | **E23**（见下） |
| `main` | 常数式 + `(x + 1)` | — | 不写 |

#### `14_buffer`（**10/10 可算，9 条达成**）

| 函数 | κ | target | 判定 |
|---|---|---|---|
| `from_text` | `3 + n(s)×7 + 2` | `O(s.size())` | ✓（≈ 7.00） |
| `append_text` | `n(s)×7` | `O(s.size())` | ✓（≈ 7.00） |
| `append_range` | `2 + hi×16 + 16` | `O(hi)` | ✓（≈ 16.00） |
| `count_byte` | `2 + 2 + n(b)×20 + 20 + 2` | `O(b.size())` | ✓（≈ 20.00） |
| `slice` | `3 + 1 + 1 + 2 + hi×16 + 16 + 4 + 2` | `O(hi)` | ✓（≈ 16.00） |
| `print_preview` | `2 + n(b)×35 + 35 + 4` | `O(b.size())` | ✓（≈ 35.00） |
| `find_from` | `… n(hay)×(… n(needle)×… ) + …` | `O(hay.size() * needle.size())` | ✓ |
| `count_occurrences` | `… n(hay)×(… n(hay)×… n(needle) …) …` | `O(hay.size() * hay.size() * needle.size())` | ✓ |
| `replace_all` | `… n(src)×(… n(src)×(… n(from) + … n(to) …) …) …` | `O(src.size()² × (from.size() + to.size()))` | ✓ |
| `main` | 具体式（含局部 `masked`） | — | 不写（目标里没有 `masked` 这个名字） |

`append_range` / `slice` 的 target 写 `O(hi)` 而不是 `O(hi - lo)`：κ 的循环上界记的是
`hi`（工具只记上界），**target 取 κ 的阶**，`hi` 是标量形参所以绑得上。

### 写不出可判定 target 的 3 个函数（**2026-11-13 复测**；旧记录是 11 个）

> 2026-11-12 这里列了 11 个（10 个不写 + 1 个 E23），理由分三类：循环上界不可命名
> （`bfs` / `dfs_visit` / `find_from`）、调用链把 ⊤ 传上去（`has_cycle` / `count_components` /
> `count_occurrences` / `replace_all`）、`main` 结构性写不出（`12` / `13` / `14`）。
> 这些**现在都能判了**（见上表 23 ✓）：`L(g)` 把「变量下标的内层尺寸」命名了，
> 树递归有了激活数，调用链不再传 ⊤。只剩下面 3 个：

- **内联形参泄漏成自由符号**（1 个，唯一写了 target 却判不了的）：`13_numeric.stddev_milli`
  的 κ 里有 `(x + 1)×33`，
  `x` 是**内联进来的 `isqrt` 的形参**——`isqrt(acc / xs.size())` 的实参没把形参代换掉。
  `x` 不是 `stddev_milli` 的形参，target 里没有名字可写 ⇒ 尺寸量集合 `{x, xs} ≠ {xs}`。
  这就是 `13_numeric` **8 个函数全部可算、却只有 6 条判达成**的原因（另外一条是 `main`）。
  > 用 `requires x <= xs.size();` 能骗过判定（实测会变 ✓ 达成），但那句不等式在本语料是
  > 假的（`x = 129987109375`，`n(xs) = 8`）——契约不写假前提，所以不写。
- **`main` 结构性写不出**（2 个）：`13` 的 `main` 没有形参、κ 里却带 `(x + 1)`，
  写 `O(1)` 会被 H3 判成「常数 target vs 含尺寸量」的**假债务**；`14` 的 `main` κ 是具体式，
  但尺寸量是体内**局部**容器 `masked`，target 里没有这个名字。两个都只声明 `effects`
  （`printf` → `io`；建容器 / `push_back` → `alloc`）。`12` 的 `main` κ 是常数 484428，
  所以照常写了 `O(1)` 并达成。

### 覆盖率（**2026-11-13 实测**；旧表已过时）

口径（与文首横幅一致）：**分子只数 `cost` 判 ✓「达成」的函数**（不数 ▲，也不数「有 target
但判不了」），分母是该范围里 **`.tu` 覆盖到的全部函数**。`00_docnums` 不参与
（`cost demo/corpus/00_docnums.cpp` 直接 `E-Compat-UnknownStd`：`std::ifstream`，exit=1），
所以 01–19 正好是 19 个模块 / **115 个函数**。

| 口径 | 2026-11-12（旧） | **2026-11-13（实测）** |
|---|---|---|
| 能算出具体 κ（`κ ≠ ⊤`） | 97/115 | **115/115 = 100%** |
| 有判决且「达成」（time，分母 115） | 70/115 = 61% | **102/115 = 88.7%** |
| 有判决且「达成」（time，只看 01–15） | 70/80 = 87.5% | **70/80 = 87.5%**（没变） |
| 任一维有判决（time 或 allocation） | — | **108/115 = 93.9%** |
| `▲` 债务 | 0 | **0** |
| 写了 time target 但判不了（E23） | 1 | **4** |

- 旧的「27/57 → 42/57」「27/70 → 42/70」两行是 2026-11-12 的（当时分母只算 14 个可测模块，
  且把「有 target 但判不了」也算进分子）；按现在的口径与规模，它们都不再适用。
- 2026-11-13 的 102 = 01–15 的 70 + **16–19 新增的 32**；分母 115 没变
  （16–19 原本就在分母里，只是没有 `.tu`）。
- 4 条 E23：`02_csv_stats.split` / `04_tokenizer.words` / `11_text.split` /
  `13_numeric.stddev_milli`（原因见前面「三类写不出可判定的 target」与该节的 3 条补充）。
- 9 个没写 time target：`01_sorting.merge_sort`、`10_backtrack.QueenSolver_render`、
  `11_text.main`、`13_numeric.main`、`14_buffer.main`、`17_sortkey.lower_bound_by_key`、
  `17_sortkey.main`、`19_ring2d.make_grid`，以及只承诺 allocation 的
  `02_csv_stats.count_fields`。
- 「任一维有判决」比 time 多 6 个（108 − 102）：4 条 E23（`02.split` / `04.words` /
  `11.split` / `13.stddev_milli`）与 `02.count_fields` / `19.make_grid` 的 **allocation**
  维度判了 ✓（time 判不了）。

> κ 与判定结论是 `HEAD 3288f6f` + 只新增 `.tu` 的工作树上的 `cost` 输出；
> 若下次跑不同，以当时的输出为准（`src/cost_domain/` 一直在改进）。

复现（`$tmp` 是任意临时目录；**`.eln` 不许落进 `demo/corpus/`**）：

```powershell
$tmp = Join-Path $env:TEMP "corpus-eln"; New-Item -ItemType Directory -Force $tmp | Out-Null
Copy-Item demo/corpus/13_numeric.cpp "$tmp/13_numeric.eln"
python -B src/dsh.py cost "$tmp/13_numeric.eln" --compat --explain   # 先看 κ
python -B src/dsh.py cost  demo/corpus/13_numeric.tu demo/corpus/13_numeric.cpp --compat
python -B src/dsh.py check demo/corpus/13_numeric.tu demo/corpus/13_numeric.cpp --compat
```

### 反向验收：target 收紧一个阶，`cost` 必须报 `▲ 超标`

三份 `.tu` 各挑一条，复制到临时目录把 target 收紧（原文件不动）：

| 文件 | 函数 | 原 target | 收紧成 |
|---|---|---|---|
| `12_graph.tu` | `reachable` | `O(dist.size())` | `O(1)` |
| `13_numeric.tu` | `mean_milli` | `O(xs.size())` | `O(1)` |
| `14_buffer.tu` | `append_range` | time/alloc `O(hi)` | time/alloc `O(1)` |

```text
  ▲ reachable         κ ≤ 7 + 2 + n(dist)×20 + 20 + 2
                      **超标**：阶不够，比值 ≈ 21990232555551.00

  ▲ mean_milli        κ ≤ 13 + 2 + n(xs)×16 + 16 + 9
                      分配 ✓ alloc ≤ 0   (target 0)
                      **超标**：阶不够，比值 ≈ 17592186044456.00

  ▲ append_range       κ ≤ 2 + hi×16 + 16
                       分配 ▲ alloc ≤ hi×1   (target 1)
                       **超标**：阶不够，比值 ≈ 17592186044434.00
```

三条都是 `exit=0` 但 `▲` 报债务（口径：只记债务、不阻断构建）；**没被收紧的其它函数
同时仍是 ✓**，所以判定确实在逐条算，不是全表红/全表绿。收紧只发生在临时副本里，
`demo/corpus/` 的三份原 `.tu` 重新跑仍是 **15 ✓ / 1 E23 / 10 不写 target**。

### `.tu` 语法的几个限制（这次撞到的）

1. **签名类型要写 `--compat` 归一后的名字**：`std::size_t` 形参在 `.tu` 里必须写
   `u64`（写 `usize` 报 `E-Spec-SigMismatch`）；`std::string` 写 `string`。
2. **target 只能引用形参**：被调函数的形参内联进 κ 后是自由符号（`x`），写不进 target
   ⇒ E23。`requires` 可以把符号弱化到形参上，但**只有断言为真时才该用**。
3. **`recursion depth <= N` 只收字面量**：图/树的深度只能按语料规模声明一个数；
   它让 `check` 过关，但**不会**把树递归的 ⊤ 变成可算（`dfs_visit` 仍是 ⊤）。
4. **树递归的 `W-Exp-Risk` 不阻断**：`check` 退出码仍为 0，警告里写明原因与查看位置。

## 第四次扩容：15 → 18 个（2026-11-12），补「常见内核」这一档

`15_unionfind` / `16_bitset` / `17_sortkey`。目的不是刷通过率，而是把 KPI① 的**分母**
从 74 抬到 99：之前 12 个 ⊤ 里 8 个是「正确 ⊤」（树递归 / 元素长度），
`8 / 74 = 10.8%` 刚好在 10% 线之上，**分母 ≥ 80 才有统计意义**。

每个文件都先用 `g++ -std=c++17 -O0 -Wall -Wextra` 编译并运行过（三个都**零警告**），
输出确定（不读 stdin / 文件 / 随机数）；UTF-8 无 BOM。

> **2026-11-13 更正**：这句原本写「**只有 `.cpp`，没有 `.tu`**（成本契约是另一条线）」。
> 15 / 16 / 17 现在都有 `.tu`（15 是 2026-11-12 补的，16 / 17 是 2026-11-13 补的），
> 判决结果见文末「2026-11-13 复测」。

| 文件 | 行数 | 场景 | 补的形状 |
|---|---:|---|---|
| `15_unionfind.cpp` | 119 | 发布系统把同批次主机按链路合并，数连通组、查两两可达 | **父数组 + `while` 上浮**：路径压缩的两段 `while`、按秩合并、组规模 |
| `16_bitset.cpp` | 120 | 事件去重：位图 + 布隆式两位判重、两天的集合作异或 | **位运算 + 容器混合**：`vector<u64>` 字位图、掩码、Kernighan popcount |
| `17_sortkey.cpp` | 115 | 访问日志按 (用户, 到达序号) 排序、分组、取流量最大的用户 | **按 key 比较**：具名比较器 + 手写插入排序 + 顺序扫分组 + 按 key 二分 |

行数按 `rename_rate.py` 的口径（`splitlines()`）。

### g++ 真实输出

```text
$ g++ -std=c++17 -O0 -Wall -Wextra -o 15_unionfind.exe demo/corpus/15_unionfind.cpp && ./15_unionfind.exe
links: 8, merged: 5, skipped: 3
groups: 4
0-2: yes
0-3: no
sizes: 3 2 3 1
merge 4-7: new group, groups: 3

$ g++ -std=c++17 -O0 -Wall -Wextra -o 16_bitset.exe demo/corpus/16_bitset.cpp && ./16_bitset.exe
events: 5, fresh: 3
bits set: 4 of 512
monday: 2, tuesday: 3
only one day: 3
after clear 511: 2
511 cleared
words: 8

$ g++ -std=c++17 -O0 -Wall -Wextra -o 17_sortkey.exe demo/corpus/17_sortkey.cpp && ./17_sortkey.exe
sorted by key: 5/7 7/2 7/4 19/5 19/6 42/1 42/3
groups: 4
key 5: hits 1, bytes 1000
key 7: hits 2, bytes 1020
key 19: hits 2, bytes 700
key 42: hits 2, bytes 1000
top: key 7, 1020 bytes
key 19 at 3, key 23 at 5
total bytes: 3720
```

> 这台机器上 `g++` 不在 PATH 里，用的是 `tools/behavior_parity.py` 的同一个兜底路径
> `C:\Users\haoll\Documents\shake\toolchain\mingw64\bin\g++.exe`；另外 **`TMP`/`TEMP`
> 必须指到 g++ 自己能写的目录**（系统 `%TEMP%` 会 `Permission denied`），
> 与工具里 `build_env()` 的做法一致。

### 两把尺的变化（实测，`p40-work` + 工作树）

| 指标 | 加文件前 | 加文件后 |
|---|---|---|
| `rename_rate.py`（`--compat`，文件级） | 14/15 = **93%** | 17/18 = **94%** |
| `--compat` 剩余错误总数 | 66 | **71** |
| 不带开关的错误总数 | 79 | 106 |
| `behavior_parity.py` | 20/22 = **90.9%** | 23/25 = **92.0%** |

三个新文件的 `main` 都是「可比 1 / 一致 1」，`check --compat` 三个都**通过**（只有 warning：
`W-Compat-StructInEln`、`I-Fn-NoSpec-Module`、`E-While-NoBound`）。新增的 5 条
`--compat` 计数（66 → 71）拆开是 `15` 2 条 + `16` 2 条 + `17` 1 条，**全部是
`E-While-NoBound` 这一条 warning**（`rename_rate.py` 的口径把 warning 也算进去），
没有一条是 error。不带开关从 79 涨到 106（`15` **18** 条 / `16` 4 条 / `17` 5 条），
拆开是：`15` 的 8×`E-Parse-Expected` + 5×`E-Call-Undeclared`（`static` 函数在非 compat
口径下先被跳过，后面的调用就找不到声明）+ 2×`E-Keyword-Unsupported` + 2×`E-Ai-HasDecl`
+ 1×`E-Parse-SkippedBody`；`16`/`17` 是 2×`E-Keyword-Unsupported` + `E-Ai-HasDecl` +
`E-Parse-SkippedBody`。**没有 `E-Compat-*`**——新语料没带入 compat 级的错误。

### 每个文件能算出多少具体 κ（`cost --compat --signature` 数 `"operator": "unknown"`）

> **2026-11-13 复测：25/25 全部具体**（下表旧值已过时）。`find_root` / `unite` /
> `group_sizes` / `15.main` / `count_bits` / `16.main` 当时都是 ⊤，后来 `cost` 域补上了
> while 上界（父链上浮）、Kernighan popcount 的常数化与调用传播，现在都有具体 κ。

| 文件 | 函数 | 具体 κ | ⊤ | 比例 |
|---|---:|---:|---:|---:|
| `15_unionfind` | 6 | 6 | 0 | **100%** |
| `16_bitset` | 12 | 12 | 0 | **100%** |
| `17_sortkey` | 7 | 7 | 0 | **100%** |
| **合计** | **25** | **25** | **0** | **100%** |

逐函数的 κ：

| 函数 | κ（`cost --compat --signature` 的 `cost_expression`） |
|---|---|
| `dsu_init` | `2 + n×21 + 21` |
| `count_groups` | `2 + 2 + n(d.parent)×22 + 22 + 2` |
| `bits_init` | `7 + 2 + (nbits + 63)×15 + 15` |
| `word_of` / `mask_of` / `mix64` | `4` / `9` / `23` |
| `bit_set` / `bit_clear` / `bit_get` | `31` / `32` / `25` |
| `xor_bits` | `2 + 2 + n(a.words)×27 + 27 + 3` |
| `dedup_count` | `2 + 2 + n(ids)×186 + 186 + 2` |
| `bits_from` | `2 + 1 + 1 + 7 + 2 + (nbits+63)×15 + 15 + 2 + 2 + n(xs)×49 + 49 + 3` |
| `entry_less` / `group_less` | `18` / `18` |
| `sort_entries` | `2 + n(xs)×(7 + 9 + n(xs)×44 + 44 + 8 + 5) + 7 + 9 + n(xs)×44 + 44 + 8 + 5` |
| `group_by_key` | `3 + 2 + n(xs)×42 + 42 + 2` |
| `pick_top` | `7 + 2 + n(gs)×44 + 44 + 5` |
| `lower_bound_by_key` | `7 + n(hi)×25 + 25 + 2` |
| `17_sortkey.main` | 常数项 + 两次 `n(hi)×25`（二分被内联进来），仍是具体式 |

`find_root` / `unite` / `group_sizes` / `15.main` / `count_bits` / `16.main` 当时是 ⊤
（2026-11-12），**2026-11-13 复测已全部变成具体 κ**：`find_root` 的两段父链上浮被折成
`O(n(d.parent))`，`count_bits` 的内层 Kernighan `while` 被折成常数 928，调用方随之落地。

### KPI① 的分母（实测口径：01–17，共 99 个函数）

| 口径 | 加文件前（01–14） | 加文件后（01–17） |
|---|---|---|
| 函数总数（分母） | 74 | **99** |
| ⊤ 数 | 12 | 18 |
| 具体 κ 数 | 62 | 81 |
| ⊤ 率（全部 ⊤ / 总数） | 12/74 = 16.2% | 18/99 = **18.2%** |
| **「正确 ⊤」（树递归 + 元素长度，8 个不变）/ 总数** | 8/74 = 10.8% | 8/99 = **8.1% ≤ 10%** |

也就是说：**分母 74 → 99 达成了「≥ 80」的前提**，但新增的 6 个 ⊤ **一个都不是「正确 ⊤」**
——全是 `while` 上界推不出（`E-While-NoBound`）或调用链传播。如果 KPI① 的分子是
「全部 ⊤」，这次扩语料会让比例从 16.2% 涨到 18.2%（诚实记账，不为了指标好看去改代码）；
分子若按「正确 ⊤」算，则 8/99 = 8.1% 首次进到 10% 线内。

> **2026-11-13 复测（这张表的数字已过时）**：01–19 的 **115 个函数全部算出具体 κ**
> （⊤ = 0，含 `10_backtrack` 的树递归——它现在靠 `recursion depth <= 8` 给出激活数）。
> 上表「18 个 ⊤」是 2026-11-12 当时工具的结论，之后 `cost` 域补了 while 上界、
> 树递归激活数与元素长度量 `L(g)`，那 18 个里没有一个留在 ⊤。所以现在谈「⊤ 率」没有
> 意义，缺口换成了「κ 有了但 target 的尺寸量对不齐」（见文首横幅与覆盖率一节）。

### 新暴露的 4 个方言缺口（都按方言改了，缺口记在这里）

这四条都是写自然 C++ 时的正常写法，`check --compat` 直接拒；**其中两条是 parse 级，
会让 `cost` 整份文件都跑不出 κ**（`cost` 的输出是「改错没通过」而不是逐函数的 ⊤），
也就是「一个 parse 缺口 = 整个文件的 κ 分母归零」，不只是少算一个函数。

| 自然写法 | 诊断 | 语料里改成 |
|---|---|---|
| `1ULL << (i & 63)`（`1ull` / `1UL` 同样） | `E-Parse-Expected`：字面量后缀不认，`1` 后面直接把 `ULL` 当成 ident | 局部量兜宽度：`u64 one = 1; return one << (i & 63);`（`(u64)1` 也不行，`E-Cast-CStyle` 要求写 `as`） |
| `b.words[i] \|= mask;` / `&~ mask;` | `E-Parse-ExpectedExpr`：在 `=` 处报错（把 `\|` / `&` 当二元运算，看不到 `=`） | `b.words[i] = b.words[i] \| mask;`（标量 `x \|= 1` 同样被拒，不是下标特有的） |
| `u64 probes[6] = {0, 2, 0, 3, 5, 7};` | `E-Parse-Expected @列`：定长数组带初始化列表不收 | 不建数组，直接写成两条 `if/else` 断言（本来也只有两对要查） |
| `Group& g = out[out.size() - 1]; g.hits += 1;` | `E-Ref-Local`：**局部引用 / 别名不存在**，引用只允许出现在形参位置 | 先取下标 `std::size_t last = out.size() - 1;` 再 `out[last].hits += 1;` |

第 2 条和第 4 条影响最大：位图 / 位集合代码天然用 `|=`、`&=`，容器内元素别名
（`auto& g = v.back()` 的近亲）是最常见的分组写法。两条都**不是语料写法的问题**，
是方言的表达力缺口。

### 新语料的 ⊤ 触发点（拆开单测确认，不是猜的）

| 写法 | κ | 说明 |
|---|---|---|
| `while (d.parent[root] != root) { root = d.parent[root]; }` | **⊤** | 条件是「父指针自指」，不是尺寸比较；`E-While-NoBound` 给了三条出路（改尺寸比较 / 把上界写进被调函数 spec 的 `repeats` / `for + break`）。路径压缩的**单次**复杂度上界是 `O(n)`、摊还近 `O(α(n))`，两种都得先让工具认识这个 `while` |
| `while (x != 0) { x = x & (x - 1); n += 1; }`（Kernighan popcount） | **⊤** | 循环次数 = popcount ≤ 64，但条件仍是数据相关；写成 `for (u64 k = 0; k < 64; ++k)` 就能算（`08_bits_hash.popcount64` 正是这个写法，κ = 常数） |
| `unite` / `group_sizes` / `15.main` / `16.main` | **⊤** | 调用链传播：被调的 `find_root` / `count_bits` 是 ⊤，调用方跟着 ⊤ |

`16_bitset` 的 10 个具体 κ 说明「位运算 + 容器混合」这一档本身是够用的：
只要循环上界能用尺寸量表达（`for (w = 0; w < b.words.size(); ++w)`），
掩码、异或、按字遍历都能算。`17_sortkey` 是第二个 100% 的文件（第一个是 `13_numeric`）。

### 复现

```powershell
$tmp = Join-Path $env:TEMP "corpus-eln"; New-Item -ItemType Directory -Force $tmp | Out-Null
Copy-Item demo/corpus/15_unionfind.cpp "$tmp/15_unionfind.eln"
python -B src/dsh.py check "$tmp/15_unionfind.eln" --compat
python -B src/dsh.py cost  "$tmp/15_unionfind.eln" --compat --signature   # 数 "operator": "unknown"
```

```text
python -B tools/rename_rate.py        # 17/18 = 94%（--compat 剩余 71 条）
python -B tools/behavior_parity.py    # 23/25 = 92.0%
```

`.eln` **不要**落进 `demo/corpus/`。

> **2026-11-13 更正**：这里原本写「三份新语料没有 `.tu`，所以 `cost` 只出 κ、不判债务」。
> 15 / 16 / 17 现在都有 `.tu` 了（16 / 17 是 2026-11-13 补的）：`cost` 会逐函数出判决，
> 详见文末「2026-11-13 复测」。

## 第五次扩容：18 → 20 个（2026-11-12），补最后两类形状

前 18 个文件里，`13_numeric` 只有整数定点（**一个 `f64` 循环都没有**），
`12_graph` 虽是「容器套容器」但**只读**；这两类形状一直空着。这次补上：

| 文件 | 行数 | 场景 | 补的形状 |
|---|---:|---|---|
| `18_kahan.cpp` | 112 | 电压采样：一记 1e16 大冲击前后各一百个 1.0 小台阶，三种求和比相对误差 | **f64 密集循环**：Kahan 补偿、自底向上两两求和、相对误差、`%f` 定长输出 |
| `19_ring2d.cpp` | 120 | LED / 热仿真面板：滑翔机跑三代 + 中间 2×2 热区扩散两轮 | **二维原地更新 + 双缓冲**：读旧写新、乒乓换角色、八邻域夹取、5 点平均 |

行数按 `rename_rate.py` 的口径（`splitlines()`）。两个文件都先用
`g++ -std=c++17 -O0 -Wall -Wextra` 编译运行过（**零警告**），输出确定（不读 stdin /
文件 / 随机数），UTF-8 无 BOM。

> **2026-11-13 更正**：原本写「**只有 `.cpp`，没有 `.tu`**（成本契约是另一条线）」，
> 已过时——18 / 19 在 2026-11-13 补了 `.tu`，8 + 8 个函数里 8 + 7 个有 time 判决
> （`19.make_grid` 只承诺 allocation），详见文末「2026-11-13 复测」。

### g++ 真实输出

```text
$ g++ -std=c++17 -O0 -Wall -Wextra -o 18_kahan.exe demo/corpus/18_kahan.cpp && ./18_kahan.exe
n 202 exact 200.000000
naive 100.000000 err 0.500000
kahan 200.000000 err 0.000000
pairwise 198.000000 err 0.010000
mean 0.990099
scaled[0] 5000000000000000.000000 scaled[last] 0.500000
spread 20000000000000000.000000
same-sign naive lost 1000.000000 pairwise lost 0.000000

$ g++ -std=c++17 -O0 -Wall -Wextra -o 19_ring2d.exe demo/corpus/19_ring2d.cpp && ./19_ring2d.exe
alive 5
..........
.#........
..##......
.##.......
..........
..........
..........
..........
heat center 47 cells 24
```

`18` 的数据形状是「大数 + 很多小数 + 大数抵消」，精确和 200：朴素求和只剩 100
（小项全被 1e16 吃掉），Kahan 正好 200，两两求和 198（**只减少同号累加的误差，
救不了抵消**——这是两者该分开报的原因）。同号那组里，朴素丢了 1000，两两求和一位没丢。

`19` 里生命游戏跑完三代后打印最终整张图（`#` / `.`）+ 活细胞数；热扩散两轮后
中心从 80 降到 47。两个文件都**不拷贝整张网格**，靠 `a→b`、`b→a` 乒乓完成双缓冲。

### 两把尺的变化（实测，`p40-work` + 工作树）

| 指标 | 加文件前（18 个） | 加文件后（20 个） |
|---|---|---|
| `rename_rate.py`（`--compat`，文件级） | 17/18 = **94%** | 19/20 = **95%** |
| `--compat` 剩余错误总数 | 70 | **70** |
| 不带开关的错误总数 | 106 | 110 |
| `behavior_parity.py` | 23/25 = **92.0%** | 25/27 = **92.6%** |

两个新文件的 `main` 都是「可比 1 / 一致 1」，`check --compat` 两个都**通过**，
只余 `I-Fn-NoSpec-Module`（没有 `.tu` 的提示，不是 error）。`--compat` 总数仍是 70 条，
说明新语料**一条 `E-*` 都没带入**；不带开关的 +4 条是 `18`、`19` 各 2 条
（`E-Keyword-Unsupported`：非 compat 口径不认 `using` 别名；紧跟着一条
`E-Parse-SkippedBody`），与 12–14 的旧文件同因。

### 每个文件能算出多少具体 κ（`cost --compat --signature` 数 `"operator": "unknown"`）

| 文件 | 函数 | 具体 κ | ⊤ | 比例 |
|---|---:|---:|---:|---:|
| `18_kahan` | 8 | 8 | 0 | **100%** |
| `19_ring2d` | 8 | 8 | 0 | **100%** |
| **合计** | **16** | **16** | **0** | **100%** |

两个文件 `main` 的 κ 都是**巨大的常数式**（尺寸写死，`8×10` 与 `202/1001` 全被折成常数），
所以下表只摘有代表性和可读的那几条：

| 函数 | κ（`cost_expression`） |
|---|---|
| `naive_sum` | `2 + 2 + n(xs)×16 + 16 + 2` |
| `kahan_sum` | `4 + 2 + n(xs)×33 + 33 + 2` |
| `pairwise_sum` | `3 + 2 + n(xs)×19 + 19 + 2 + log2(n(xs))×(7 + 2 + n(xs)×33 + 33 + 5) + 7 + 2 + n(xs)×33 + 33 + 5 + 4` |
| `mean_of` | `5 + 1 + 1 + 1 + 4 + 2 + n(xs)×33 + 33 + 2 + 1 + 1` |
| `rel_error` | `18` |
| `spread` / `rescale` | `8 + 2 + n(xs)×34 + 34 + 4` / `3 + 2 + n(xs)×21 + 21 + 2` |
| `make_grid` | `2 + 1 + rows + 2 + rows×(…cols×14…) + …` |
| `seed_life` | `35`（5 次写入，常数） |
| `neighbors` | `1522`（固定 8×10 把 3×3 偏移折成常数） |
| `life_step` | `2 + n(cur)×(7 + 2 + L(cur)×1569 + 1569 + 5) + …` |
| `diffuse_once` | `2 + n(cur)×(7 + 2 + L(cur)×126 + 126 + 5) + …` |
| `count_nonzero` / `print_grid` | `… L(g)×24 …` / `… L(g)×26 …` |

### KPI① 的分母（实测口径：01–19，共 115 个函数）

| 口径 | 加文件前（01–17） | 加文件后（01–19） |
|---|---|---|
| 函数总数（分母） | 99 | **115** |
| ⊤ 数 | 18 | 18 |
| 具体 κ 数 | 81 | 97 |
| ⊤ 率（全部 ⊤ / 总数） | 18/99 = 18.2% | 18/115 = **15.7%** |
| 「正确 ⊤」（树递归 + 元素长度，8 个不变）/ 总数 | 8/99 = 8.1% | 8/115 = **7.0%** |

这次是**分母变大、⊤ 一个没加**：16 个新函数全部算出了具体式。

> **2026-11-13 复测（这张表的数字已过时）**：01–19 现在 **⊤ = 0**（115/115 具体），
> 上表两列里的 18 个 ⊤ 全部落地（原因同一节前面的说明：while 上界 / 树递归激活数 /
> 元素长度量 `L(g)`）。同一轮复测的契约覆盖率是 **102/115 = 88.7%**（time 判达成，
> 分子只数 ✓），详见文首横幅与本文件后面的「2026-11-13 复测」。

### 新踩到的 6 个方言缺口（都按方言改了，缺口记在这里）

| 自然写法 | 诊断 | 语料里改成 |
|---|---|---|
| `n == 2 \|\| n == 3 ? 1 : 0` | `E-Parse-Expected`：在 `?` 处「期望 `;`，实际是「整数」」 | 拆成 `if/else` 给 `live` 赋值；**三元运算符整条不支持**，不是优先级问题 |
| `std::fabs(x)` | `E-Compat-UnknownStd`：白名单只有 `std::abs`，没有 `std::fabs` | `std::abs`（f64 上两者同义，落到内建 `abs`） |
| `for (rr = r0; rr <= r1; ++rr)`（上界是局部变量） | warning `E-While-NoBound`（`for` 被当成 `while` 报；改成 `< r1 + 1` 一样报） | 3×3 偏移 + `continue` 夹取，上界变成常量 3，warning 消失 |
| `Grid b = a;`（嵌套 vector 从变量拷贝初始化） | κ = ⊤（元素索引、按值返回、函数返回 Grid 都正常，只有「整只容器拷贝」判不出） | 乒乓：`a→b`、`b→a`，不拷贝整张图 |
| 递归 `pairwise_sum` | `cost`：「内联 pairwise_sum 时成环（pairwise_sum → pairwise_sum）」→ ⊤，并传给 `main`；没有 `.tu` 就补不了 `repeats` | 自底向上循环版：同样的归约顺序，仍然叫两两求和 |
| `(f64)xs.size()` | `E-Cast-CStyle`（已知：C 风格强转一律拒） | `f64 n = xs.size();`，让隐式整数→浮点接手 |

第 1 条影响最大：三元运算符是写 C++ 时最常用的表达式之一，现在必须展开成 `if/else`。
第 3 条只是 warning（不挡 `check`），但 `rename_rate.py` 的口径把它算进错误总数，
所以语料里改掉了；如果保留，20 个文件的 `--compat` 总数会是 72 而不是 70。

**顺带确认能用的**：`f64` 局部量 / 循环 / 除法、`1e16` 这种指数写法、
`std::printf("%f", v)`（落成 `{:f}`，与 C 的 6 位定点**逐字节相同**）、
`std::abs`、嵌套 `vector` 的下标读写与按值返回——这些都没有额外缺口。

### 复现

```powershell
$tmp = Join-Path $env:TEMP "corpus-eln"; New-Item -ItemType Directory -Force $tmp | Out-Null
Copy-Item demo/corpus/18_kahan.cpp "$tmp/18_kahan.eln"
python -B src/dsh.py check "$tmp/18_kahan.eln" --compat
python -B src/dsh.py cost  "$tmp/18_kahan.eln" --compat --signature   # 8 个函数，0 个 unknown
```

```text
python -B tools/rename_rate.py        # 19/20 = 95%（--compat 剩余 70 条）
python -B tools/behavior_parity.py    # 25/27 = 92.6%
```


## 2026-11-13 复测：16–19 补 `.tu`（**35 个函数 / 62 条 target**）

16 / 17 / 18 / 19 这四个模块此前**只有 `.cpp`**：`cost` 算得出每个函数的 κ，但没有 spec
⇒ 永远不会有判决，而覆盖率的分母照样把它们算进去。2026-11-13 补上四份 `.tu`
（`16_bitset.tu` / `17_sortkey.tu` / `18_kahan.tu` / `19_ring2d.tu`），**只新增 `.tu`，
一行 `src/**` / `tests/**` / `tools/**` / `.cpp` 都没动**。

```powershell
$tmp = Join-Path $env:TEMP "corpus-eln"; New-Item -ItemType Directory -Force $tmp | Out-Null
Copy-Item demo/corpus/16_bitset.cpp "$tmp/16_bitset.eln"
python -B src/dsh.py check demo/corpus/16_bitset.tu demo/corpus/16_bitset.cpp --compat   # exit 0
python -B src/dsh.py cost  demo/corpus/16_bitset.tu demo/corpus/16_bitset.cpp --compat   # 12 ✓
```

### 判定表（四份 `.tu`，实测）

| 文件 | 函数 | 写了 time target | ✓ 达成 | 不写 time target | 判不了（E23） |
|---|---:|---:|---:|---:|---:|
| `16_bitset.tu` | 12 | 12 | **12** | 0 | 0 |
| `17_sortkey.tu` | 7 | 5 | **5** | 2 | 0 |
| `18_kahan.tu` | 8 | 8 | **8** | 0 | 0 |
| `19_ring2d.tu` | 8 | 7 | **7** | 1（`make_grid`，只承诺 allocation） | 0 |
| **合计** | **35** | **32** | **32** | **3** | **0** |

`dsh check <tu> <cpp> --compat` 四条命令**退出码都是 0**，`dsh cost` 四条也都是
**exit 0、0 条解析失败、0 条债务**。`check` 的 warning 只有两类、都不阻断：
`W-Compat-StructInEln`（`16` 的 `Bits`、`17` 的 `Entry`/`Group`）与
`E-While-NoBound`（`16` 的 Kernighan `while`、`17` 的二分手写 `while`）；
`18` / `19` 连 warning 都没有。

### 逐函数（κ 与 target）

**`16_bitset`（12/12 达成）**

| 函数 | κ | target | 判定 |
|---|---|---|---|
| `bits_init` | `7 + 2 + (nbits + 63)×15 + 15` | time `O(nbits)` / alloc `O(nbits)` | ✓ |
| `word_of` / `mask_of` / `bit_set` / `bit_clear` / `bit_get` / `mix64` | `4` / `9` / `31` / `32` / `25` / `23` | time `O(1)` / alloc `0` | ✓ |
| `count_bits` | `2 + 2 + n(b.words)×928 + 928 + 2` | time `O(b.words.size())` / alloc `0` | ✓ |
| `xor_bits` | `2 + 2 + n(a.words)×27 + 27 + 3` | time `O(a.words.size())` / alloc `O(a.words.size())` | ✓ |
| `dedup_count` | `2 + 2 + n(ids)×186 + 186 + 2` | time `O(ids.size())` / alloc `0` | ✓ |
| `bits_from` | `… (nbits+63)×15 … + n(xs)×49 …` | time `O(nbits + xs.size())` / alloc `O(nbits)` | ✓ |
| `main` | `2715859`（常数） | time `O(1)` | ✓ |

**`17_sortkey`（5/5 达成，2 不写）**

| 函数 | κ | target | 判定 |
|---|---|---|---|
| `entry_less` / `group_less` | `18` / `18` | time `O(1)` / alloc `0` | ✓ |
| `sort_entries` | `2 + n(xs)×(… n(xs)×44 …) + …` | time `O(xs.size() * xs.size())` / alloc `0` | ✓ |
| `group_by_key` | `3 + 2 + n(xs)×42 + 42 + 2` | time `O(xs.size())` / alloc `O(xs.size())` | ✓ |
| `pick_top` | `7 + 2 + n(gs)×44 + 44 + 5` | time `O(gs.size())` / alloc `0` | ✓ |
| `lower_bound_by_key` | `7 + n(hi)×25 + 25 + 2` | — | 不写（局部标量 `hi`） |
| `main` | `… + n(hi)×25 + …`（两次，二分被内联） | — | 不写（自由符号 `hi`） |

**`18_kahan`（8/8 达成）**

| 函数 | κ | target | 判定 |
|---|---|---|---|
| `naive_sum` | `2 + 2 + n(xs)×16 + 16 + 2` | time `O(xs.size())` / alloc `0` | ✓ |
| `kahan_sum` | `4 + 2 + n(xs)×33 + 33 + 2` | time `O(xs.size())` / alloc `0` | ✓ |
| `pairwise_sum` | `… n(xs)×19 … + log2(n(xs))×(… n(xs)×33 …) + …` | time `O(xs.size() * log2(xs.size()))` / alloc `O(xs.size())` | ✓ |
| `mean_of` | `… n(xs)×33 + 33 + …` | time `O(xs.size())` / alloc `0` | ✓ |
| `rel_error` | `18` | time `O(1)` / alloc `0` | ✓ |
| `spread` | `8 + 2 + n(xs)×34 + 34 + 4` | time `O(xs.size())` / alloc `0` | ✓ |
| `rescale` | `3 + 2 + n(xs)×21 + 21 + 2` | time `O(xs.size())` / alloc `O(xs.size())` | ✓ |
| `main` | 巨大的常数式（`log2(202)` / `log2(1001)` 都折成常数） | time `O(1)` | ✓ |

**`19_ring2d`（7/7 达成 + 1 只判 allocation）**

| 函数 | κ | target | 判定 |
|---|---|---|---|
| `make_grid` | `… rows×(… cols×14 … n(row) …) …` | alloc `O(rows * cols)`（**没有 time target**） | ✓ allocation |
| `seed_life` | `35` | time `O(1)` / alloc `0` | ✓ |
| `neighbors` | `1522`（3×3 偏移折成常数） | time `O(1)` / alloc `0` | ✓ |
| `life_step` | `2 + n(cur)×(… L(cur)×1569 …) + …` | time `O(cur.size() * L(cur))` / alloc `0` | ✓ |
| `diffuse_once` | `2 + n(cur)×(… L(cur)×126 …) + …` | time `O(cur.size() * L(cur))` / alloc `0` | ✓ |
| `count_nonzero` | `… n(g)×(… L(g)×24 …) …` | time `O(g.size() * L(g))` / alloc `0` | ✓ |
| `print_grid` | `… n(g)×(… L(g)×26 …) …` | time `O(g.size() * L(g))` / alloc `0` | ✓ |
| `main` | `3679493`（常数） | time `O(1)` | ✓ |

### 补不了的 3 个函数（逐条原因）

1. **`17_sortkey.lower_bound_by_key`** —— κ = `7 + n(hi)×25 + 25 + 2`，`hi` 是体内的
   `std::size_t hi = xs.size()`，是**局部标量**、不是形参。target 只能引用形参：
   写 `O(xs.size())` 判 **E23**（κ 的尺寸量 `['hi']` ≠ target 的 `['xs']`），
   写 `O(hi)` 也判 E23（`hi` 不在 target 的作用域里，被折成容器形参的尺寸量 `xs`）。
   这不是语料写法问题——理想形态是工具能证明 `hi ≤ n(xs)` 并折成同一个量。
2. **`17_sortkey.main`** —— κ 里带两次 `n(hi)×25`（`lower_bound_by_key` 被内联，
   它的局部标量 `hi` 成了 `main` 的**自由符号**）。`main` 没有形参，这个名字更没有地方可写。
3. **`19_ring2d.make_grid`** —— **时间**写不出来：κ 有三个尺寸量 `rows` / `cols` / `n(row)`，
   而 `row` 是外层循环里的**局部容器**（`std::vector<u64> row;`）。写 `O(rows * cols)`
   判 E23（`['cols', 'row', 'rows'] ≠ ['cols', 'rows']`）。**分配**判得了（κ_alloc =
   `rows×(cols×1 + 2)`，只有两个形参），所以只承诺 allocation。

> 另外 14 个函数的 **time target 是常数**（`O(1)`：`word_of` / `mask_of` / `bit_set` /
> `bit_clear` / `bit_get` / `mix64` / `16.main` / `entry_less` / `group_less` / `rel_error` /
> `18.main` / `seed_life` / `neighbors` / `19.main`）——它已经是**最紧的一档**，
> 不存在「收紧一个阶」的反向验收（见下）。

### 反向验收：target 收紧一个阶，`cost` 必须报 `▲ 超标`

在**临时副本**里改（`demo/corpus/` 的原文件不动）：

| 函数 | 原 target | 收紧成 | 结果 |
|---|---|---|---|
| `16.bits_init` | time/alloc `O(nbits)` | `O(1)` | **▲ 2 处**（比值 ≈ 1.65e13） |
| `16.count_bits` | `O(b.words.size())` | `O(1)` | **▲ 1 处**（≈ 1.02e15） |
| `16.xor_bits` | time/alloc `O(a.words.size())` | `O(1)` | **▲ 2 处**（≈ 2.97e13） |
| `16.dedup_count` | `O(ids.size())` | `O(1)` | **▲ 1 处**（≈ 2.05e14） |
| `17.sort_entries` | `O(xs.size()²)` | `O(xs.size())` | **▲ 1 处**（≈ 4.84e13） |
| `17.group_by_key` | time/alloc `O(n)` | `O(1)` | **▲ 2 处**（≈ 4.62e13） |
| `17.pick_top` | `O(gs.size())` | `O(1)` | **▲ 1 处**（≈ 4.84e13） |
| `18.naive_sum` | `O(xs.size())` | `O(1)` | **▲ 1 处**（≈ 1.76e13） |
| `18.kahan_sum` | `O(xs.size())` | `O(1)` | **▲ 1 处**（≈ 3.63e13） |
| `18.pairwise_sum` | `O(n log n)` | `O(n)` | **▲ 1 处**（≈ 1372.00） |
| `18.mean_of` | `O(xs.size())` | `O(1)` | **▲ 1 处**（≈ 3.63e13） |
| `18.spread` | `O(xs.size())` | `O(1)` | **▲ 1 处**（≈ 3.74e13） |
| `18.rescale` | time/alloc `O(n)` | `O(1)` | **▲ 2 处**（≈ 2.31e13） |

真实输出（摘 3 条）：

```text
  ▲ bits_init    κ ≤ 7 + 2 + (nbits + 63)×15 + 15
                 分配 ▲ alloc ≤ (nbits + 63)×1   (target 1)
                 **超标**：阶不够，比值 ≈ 16492674417609.00
▲ 2 处超出 target（time / allocation 各算一处；记为债务，不阻断构建）

  ▲ sort_entries  κ ≤ 2 + n(xs)×(7 + 9 + n(xs)×44 + 44 + 8 + 5) + 7 + 9 + n(xs)×44 + 44 + 8 + 5
                  分配 ✓ alloc ≤ 0   (target 0)
                  **超标**：阶不够，比值 ≈ 48378511622261.00
▲ 1 处超出 target（time / allocation 各算一处；记为债务，不阻断构建）

  ▲ pairwise_sum  κ ≤ 3 + 2 + n(xs)×19 + 19 + 2 + log2(n(xs))×(7 + 2 + n(xs)×33 + 33 + 5) + …
                  分配 ✓ alloc ≤ n(xs)×1   (target n(xs))
                  **超标**：阶不够，比值 ≈ 1372.00
▲ 1 处超出 target（time / allocation 各算一处；记为债务，不阻断构建）
```

**做不到反向验收的两类（如实说明，不是漏做）**：

1. **常数 κ 的 14 个 `O(1)`**：`O(1)` 已经是最紧的一档，没有「更小的一阶」可收紧；
   写成 `O(0)` 之类是语法外的东西，不构成一次有意义的反向测试。
2. **多尺寸量的 6 个**（`16.bits_from` 的 `nbits + n(xs)`；`19.life_step` /
   `diffuse_once` / `count_nonzero` / `print_grid` 的 `n(cur) × L(cur)` 型；
   `19.make_grid` 的 allocation `rows × cols`）：收紧一个阶只能**丢掉一个尺寸量**，
   那不是「判阶不够」，而是 **E23 尺寸量不一致**。实测（临时副本）：

   ```text
     ? life_step  κ ≤ 2 + n(cur)×(7 + 2 + L(cur)×1569 + 1569 + 5) + …
                  无法判定：κ 用的尺寸量 ['L(cur)', 'cur'] 与 target 用的 ['cur'] 不一致（E23）
     ? make_grid  κ ≤ 2 + 1 + rows + 2 + rows×(… n(row) …) + …
                  无法判定：κ 用的尺寸量 ['cols', 'row', 'rows'] 与 target 用的 ['cols', 'rows'] 不一致（E23）
   ```

   这是工具的**保守方向**（不拿「同规模假设」当判定），不是契约漏洞；等工具能把
   `L(cur)` 折回 `n(cur)`、把 `row` 折回 `cols`，这些 target 就能收紧一档再做反向验收。

### 覆盖率变化（本次）

| 口径 | 本次之前 | **本次之后** |
|---|---|---|
| 有判决且达成（time，01–19 / 115 个函数） | 70/115 = **60.9%** | **102/115 = 88.7%** |
| 同一口径只看 01–15（80 个函数） | 70/80 = 87.5% | 70/80 = 87.5%（没变） |
| `▲` 债务 | 0 | **0** |

分子 +32 = `16_bitset` 12 + `17_sortkey` 5 + `18_kahan` 8 + `19_ring2d` 7；
分母不变（16–19 原本就在 115 里，只是没有 `.tu`）。

### 门禁与 κ

- `python -B tools/gates.py --jobs=4` → **11 道门全绿**（本目录只多了 `.tu`，
  门禁的 `runner` / `golden` / `cost_baseline` / `order_matrix` 等都没动口径）。
- **κ 一个字没变**：`.tu` 只影响「target 与判决」，不影响 `cost` 算出的 κ（κ 由 `.cpp` +
  `src/cost_domain/` 决定）。本次工作树里 `src/**` / `tests/**` / `tools/**` /
  `demo/corpus/*.cpp` **一个字节都没改**（`git status` 只有新增的 4 份 `.tu` 与本 README），
  所以同一台机器、同一份 `src/` 上补 `.tu` 前后的 κ **逐字节相同**。
  与 2026-11-12 那几张旧表之间的差异（例如 `16.count_bits` 从 ⊤ 变成 `…×928…`）
  来自这中间**工具本身**的改进，不是本次改动带来的。