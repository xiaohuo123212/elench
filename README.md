# Elench

> **语法学 C++，语义学 Rust。写起来像 C++，但每一分成本都能被机器检查。**

Elench 是一门**组件语言**：用来写算法与数据结构这类"有边界、有成本、要被人复用"的零件，
不写 UI，不写系统胶水，也不打算替代 C++。

---

## 三句话

1. **这是什么**：一门写法贴着 C++ 的语言——`u64 f(const vector<u64>& xs)`、`push_back` /
   `size` / `empty` / `find` / `insert` / `erase`、`array<T, N>(v)`、`optional` / `expected`。
2. **为什么**：AI 对 C++ 熟，但 C++ 的成本（时间、分配、谁改了谁）说不清。这里把成本写成
   **签名的一部分**（`.tu` 里的 `target`），编译器从实现**自己算一遍**（`κ`）来核对：
   **达成 / 债务 / 无法判定**，三种结论都能说出口。
3. **三分钟跑起来**：见下。

---

## 三分钟跑起来

需要 **Python ≥ 3.10**，**零第三方依赖**。

```bash
git clone https://github.com/xiaohuo123212/elench.git
cd elench

python src/elench.py run   文档/入门/例子/hello      # 真的执行：42
python src/elench.py check 文档/样例/sum             # 语法 / 类型 / 契约
python src/elench.py cost  文档/样例/max_pair_sum    # 算 κ，和 target 比
```

真实输出（`cost`，2026-11-11 实跑）：

```text
module max_pair_sum

  ✓ max_pair_sum  κ ≤ 9 + n(xs)×(4 + 4 + n(xs)×24 + 24 + 5) + 4 + 4 + n(xs)×24 + 24 + 5 + 2
                  分配 ✓ alloc ≤ 0   (target 0)
                  达成：阶够，比值 ≈ 24.00

✓ 所有函数都在 target 之内
```

那一串里的 `n(xs)×(… n(xs)×24 …)` 就是 **O(n²)**——两层循环，工具自己看出来的；
作者只在 `.tu` 里写了 `target { time: { max: O(xs.size() * xs.size()) } }`，两条对上了。

---

## 长什么样

一个模块固定两份文件：**`.tu` 写承诺**（人审、人签），**`.eln` 写实现**（可以整份重写）。

```elench
// max_pair_sum.tu —— 承诺：两数之和的最大值，O(n²)、不分配
spec max_pair_sum(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(xs.size() * xs.size()) }, allocation: { max: 0 }, };
}
```

```elench
// max_pair_sum.eln —— 实现：只有函数体，零声明
u64 max_pair_sum(const vector<u64>& xs) {
    u64 best = 0;
    u64 i = 0;
    while (i < xs.size()) {
        u64 j = i + 1;
        while (j < xs.size()) {
            best = max(best, xs.at(i) + xs.at(j));
            j = j + 1;
        }
        i = i + 1;
    }
    return best;
}
```

三个字概括这套分工：**`target` 是意图，`κ` 是事实，债务是两者的差**。

---

## 你会的 C++ 在这里长什么样

| 你平时写 | 这里怎么写 | 备注 |
|---|---|---|
| `uint64_t f(const std::vector<uint64_t>& xs)` | `u64 f(const vector<u64>& xs)` | `std::` 前缀也认；`int` 是 **64 位** |
| `xs.push_back(v)` / `xs.size()` / `xs.empty()` | 同名 | 旧名 `push` / `length` / `is_empty` 还能跑，但报 `W-Builtin-Deprecated` |
| `for (const auto& x : xs)` | 同名 | 引用不拷贝；`for (auto& x : xs)` 改得动容器 |
| `std::optional<T>` / `expected<T, E>` | `optional<T>` / `expected<T, E>` | `Option` / `Result` 已下线（`E-Type-OldName`） |
| `std::array<T, N>` | `array<T, N>` / `array<T, N>(v)` | 定长；溢出**报错**，不是 UB |
| `throw` / `try` / `catch` | `T!` + `return Err(...)` + `?` | 没有异常 |
| 注释里的 `// O(n log n)` | `.tu` 里的 `target` | 这一行会被机器核对 |

**同名不同义的坑有一批，别靠猜**：见 [C++ 程序员上手清单](文档/C++惯用法对照.md)。

---

## 与兄弟仓库 `elench-c` 的关系

同一门语言，**同一个文件形状**；`elench-c` 是受限子集（C 的写法约束更紧）。
两边**表面一样**，所以照任一仓库写的代码，在另一个里也能读：

| | 这个仓库 `elench-cpp` | `elench-c` |
|---|---|---|
| 函数声明 | `u64 f(u64 n) { }` | 同 |
| 容器方法 | `push_back` / `size` / `empty` / `find` / `insert` / `erase` / `clear` / `back` / `front` | 同 |
| 类型名 | `optional` / `expected` | 同 |
| 定长数组 | `array<T, N>(v)` | 同 |

---

## 现在的状态（诚实版）

| 项 | 数字 |
|---|---|---|
| 样例程序 | **17 个**（`文档/样例/*.tu` + `*.eln`） |
| 子命令 | **19 个**（`effects` / `header` / `spec` / `cpp` 不在 `--help` 的常用区里） |
| 测试文件 | **138 个**（`python -B tests/runner.py`） |
| 实现规模 | **59 个 Python 文件 / 约 3.6 万行代码**（`python -B tools/audit_numbers.py`） |
| 第三方依赖 | **零**（只用 Python 标准库） |
| C++ 后端 | **没有**。`elench cpp` 是最小切片（只支持 `u64`/`i64`/`vector`/`while`/`if`），`elench header` 能出带契约注释的 C 头文件 |

**它不是能拿去生产的编译器**：它是一套可检查的契约系统，加一个够跑样例的实现。
能做的：算上界、判契约、把"不知道"明确说出来。不能做的：替代 C++、做完整程序验证。

---

## 从哪读起

| 你想… | 读 |
|---|---|
| 一屏看懂这门语言在讲什么 | [文档/介绍.md](文档/介绍.md) |
| **10 分钟上手** | [文档/入门.md](文档/入门.md) |
| **把 C++ 习惯搬过来** | [文档/C++惯用法对照.md](文档/C++惯用法对照.md) |
| 查语法 | [文档/语法清单.md](文档/语法清单.md) |
| 查内建 | [文档/内建函数.md](文档/内建函数.md) |
| 知道写不了什么 | [文档/能力边界.md](文档/能力边界.md) |
| 查子命令开关 | [文档/入门/命令一览.md](文档/入门/命令一览.md) |
| 全部文档的地图 | [文档/README.md](文档/README.md) |

---

## 安装

```bash
pip install -e .        # 提供 elench 命令；零依赖
npx elench-lang --help  # 或从 npm 试（包名 elench-lang，命令名 elench）
```

不装也能用：把上面命令里的 `elench` 换成 `python src/elench.py` 即可。

**许可**：代码与文档均为 [Apache-2.0](LICENSE)；引用格式见 [CITATION.cff](CITATION.cff)。

> **一句话**：别的语言让你写"怎么做"；这门语言还让你写"要做到什么程度"，然后替你核对。
