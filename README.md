# Elench

> **语法学 C++，语义学 Rust。写起来像 C++，但每一分成本都能被机器检查。**

Elench 是一门**组件语言**：用来写算法与数据结构这类"有边界、有成本、要被人复用"的零件，
不写 UI，不写系统胶水，也不打算替代 C++。

---

## 三句话

1. **这是什么**：**你现有的 `.cpp` 不用改就能被检查**（`--compat`）——`#include`、`using`、
   `struct`、`auto`、`int`、`for (auto it = xs.begin(); …)`、`static_cast` 都认。
   新写的代码用本语言的写法（`u64 f(const vector<u64>& xs)`、`push_back` / `array<T, N>(v)`、
   `optional` / `expected`）。
2. **为什么**：AI 对 C++ 熟，但 C++ 的成本（时间、分配、谁改了谁）说不清。这里把成本写成
   **签名的一部分**（`.tu` 里的 `target`），编译器从实现**自己算一遍**（`κ`）来核对：
   **达成 / 债务 / 无法判定**，三种结论都能说出口。
3. **三分钟跑起来**：见下。

---

## 三分钟跑起来

需要 **Python ≥ 3.10**，**零第三方依赖**。

**先拿你自己的代码试**：把任意一个 `.cpp` 改名成 `.eln`（内容不动），然后

```bash
git clone https://github.com/xiaohuo123212/elench.git
cd elench
pip install -e .                                  # 提供 elench 命令

elench cost 你的文件.eln --compat                  # κ 直接从源码算出来，不运行程序
```

真实输出（`文档/入门.md` 里的 `count_above`，2026-11-22 实跑）：

```text
  · count_above  κ ≤ 2 + 2 + n(xs)×20 + 20 + 2
                 未声明契约（没有 spec）：不参与判定、不算债务、不算错误
```

**`κ` 不是估计，是上界**：`n(xs)×20` 是"每轮循环最多 20 个操作"。

想让它有**判决**，加一份同名 `.tu`（实现还是不动）：

```bash
elench cost count_above --compat
```

```text
  ✓ count_above  κ ≤ 2 + 2 + n(xs)×20 + 20 + 2
                 达成：阶够，比值 ≈ 20.00
```

仓库自带的例子也能直接跑：

```bash
elench run  文档/入门/例子/hello      # 真的执行：42
elench cost 文档/样例/max_pair_sum    # κ ≤ 9 + n(xs)×(…) —— 那是 O(n²)
```

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
**哪些写法要 `--compat`、哪些本来就认**：[入门 §5 实测矩阵](文档/入门.md)。

一次设开兼容层（之后不用每条命令加 `--compat`）：

```bash
export ELENCH_COMPAT=1        # Windows: set ELENCH_COMPAT=1
```

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
| 样例程序 | **17<!--dn:samples-->** 个（`文档/样例/*.tu` + `*.eln`） |
| 子命令 | **19 个**（`effects` / `header` / `spec` / `cpp` 不在 `--help` 的常用区里） |
| 测试文件 | **207<!--dn:test_files-->** 个（`python -B tests/runner.py`） |
| 实现规模 | **61<!--dn:impl_files-->** 个 Python 文件 / **52,141<!--dn:impl_sloc-->** 行代码（`python -B tools/audit_numbers.py`） |
| 第三方依赖 | **零**（只用 Python 标准库） |
| C++ 后端 | **有，但只覆盖一个切片**。`elench cpp` 出可编译、可链接的 C++：语料 19 个模块**出码 19/19、编译 19/19、语义与解释器逐字节一致 19/19**（与手写原版一致 17/19，差的两例是原版自己依赖 C++ 未指定行为）。能发 `extern "C"`、接 `--include` / `--link` / `--lib-dir`，`--run` 会真编译、真链接、真跑再对拍。**仍不支持** `?!`（`choose`）与函数指针 |

**它不是能拿去生产的编译器**：它是一套可检查的契约系统，加一个够跑样例的实现。
能做的：算上界、判契约、把"不知道"明确说出来。不能做的：替代 C++、做完整程序验证。

---

## 从哪读起

| 你想… | 读 |
|---|---|
| **不装任何东西，直接在浏览器里试** | [文档/工作台.md](文档/工作台.md) |
| 一屏看懂这门语言在讲什么 | [文档/介绍.md](文档/介绍.md) |
| **10 分钟上手** | [文档/入门.md](文档/入门.md) |
| **把 C++ 习惯搬过来** | [文档/C++惯用法对照.md](文档/C++惯用法对照.md) |
| 查语法 | [文档/语法清单.md](文档/语法清单.md) |
| 查内建 | [文档/内建函数.md](文档/内建函数.md) |
| 知道写不了什么 | [文档/能力边界.md](文档/能力边界.md) |
| 查子命令开关 | [文档/入门/命令一览.md](文档/入门/命令一览.md) |
| **接到智能体平台**（初发 / Coze / Dify） | [文档/接智能体平台.md](文档/接智能体平台.md) |
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
