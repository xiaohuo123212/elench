# AI 导向编程语言

> **许可证**：代码与文档均为 [Apache-2.0](LICENSE)。
> **引用**：见 [`CITATION.cff`](CITATION.cff)（GitHub 页面右上会出现「Cite this repository」）。
> **DOI**：**尚无**。计划在 **M1 退出门槛②（双值域统一）达成后**打 tag `v0.70-m1` 并发布到 Zenodo。
> 在拿到 DOI 之前，引用请用仓库 URL + 提交号，**不要编造 DOI**。


> **把「我想让它多快」从注释里搬到编译器能查的地方。**

一门面向 AI 代码生成的编程语言：函数不只写「怎么做」，还写「要做到什么程度」——**成本由编译器算出，由人设定目标，两者的差记为债务**。

**当前状态**：设计 **v0.66**，实现 **M0 完成**（词法 / 解析 / 检查 / 格式化 / 高亮，**368 项测试**），M1 解释器未开始，实验 **未跑**。
**这不是一个能用的语言**，是一套已经想清楚的设计 + 一个刚开始的原型。

---

## 它要解决的两个问题

| # | 问题 | 现在什么样 |
|---|---|---|
| 1 | **AI 写得出来，但核不了** | 性能信息只活在注释、PR 描述、作者脑子里，编译器一个都不看 |
| 2 | **常数因子只能靠猜** | 哈希集/排序扫描/位图，选哪个取决于输入——AI 猜不出临界点，因为它本来就不可推断 |

---

## 六个机制

### 1. 意图与事实分开：`target` 由人冻结，`cost` 由编译器算出

```
spec evaluate(text: []u8) -> Result[i64, CalcError] {
    effects    {alloc};
    target {
        time      : { max: O(text.length()) },
        allocation: { max: O(text.length()) },
    };
} end spec
```

编译器算出实际成本，写进 `evaluate.cost.yaml`（YAML，可 diff，变化项置顶）：

```yaml
functions:
  evaluate:                    # ▲ 本次有变化
    time:        {min: "n",   max: "n"}
    allocation:  {min: "0",   max: "8*n"}
    debt:        {time: none, allocation: none}
```

**让 AI 写 `cost`，等于让被考的人自己报分。** 改成算出来之后，这一处谎言就不可能发生。

### 2. 声明与实现分家：`.tu` 装承诺，`.eln` 装实现

| 文件 | 谁写 | 内容 |
|---|---|---|
| `模块名.tu` | 人（AI 提议） | 全部声明，几十行，**人真的会看完** |
| `模块名.ai` | AI | 签名 + 函数体，**零声明** |
| `模块名.cost.yaml` | 编译器 | 算出的成本 |
| `模块名.dispatch.yaml` | 测量 | `?!` 选了谁 |

**结果：可撒谎的位置 7 处（全在 `.tu`），`.eln` 里 0 处。**

### 3. 算法选择外包给测量：`?!` + `choose` + `over` 名单

候选写在宿主 spec 的 `choose` 子块里（完整程序见 [`样例/search.tu`](文档/样例/search.tu)）：

<!-- @include 文档/样例/search.tu#choose -->
```
    choose search_impl(const Vec<u64>& xs, u64 key) -> u64! {
        over {
            search_linear,
            search_binary,
            search_interp pending
        }
        ;
        objective time;
    };
```
<!-- @end -->

```
val idx = ?!search_impl(xs, key);      // 只有这一步未定
```

| 硬规则 | 为什么 |
|---|---|
| `?!` 的承诺是**分段上界**：按运行期分档表逐段取「该段真正会跑的那个候选」的 κ；无表时退回「所有候选取 max」 | 承诺与运行期**同源**——跑的不是承诺里那个候选，界就不成立 |
| `?!` **不能构成整个函数体** | 否则退化成"两套实现二选一"，生成者就不必想清楚算法结构 |

### 4. 骨架先行：`pending`

下面是上一节那个 `choose` 块的关键一行（片段；完整块见上一节，权威样例见 [`样例/search.tu`](文档/样例/search.tu)）：

```
    choose search_impl(xs: []u64, key: u64) -> Result[u64, SearchError] {
        over      {search_linear, search_binary, search_interp pending};   // 声明：这个还没写
    } end choose
```

| 命令 | 含未实现时 |
|---|---|
| `elench check` | **通过**（骨架合法、可审） |
| `elench build` | **失败**（不能测量没写的候选） |

**检查器能算出"谁没写"，但算不出"这是故意的还是漏的"**——`pending` 就是让作者承认。

### 5. 每个可能出错的地方留痕

| 设计 | 规则 |
|---|---|
| 算术返回 `Result` | `(a + b)?` —— 每个可能溢出的点必须写 `?` |
| 文本是字节 | `[]u8`，一元素一字节（不是 8 字节） |
| 无隐式转换 / 无类型后缀 / 一种写法 | `42u64` 是错的；`function` 是错误，不是兼容 |
| 块必须有终止符 | `if … end if`、`match … end match`、`kernels … end fn` |

### 6. 报告告诉你「哪些数字不能全信」

每份成本报告有独立的**信任区**，列出全部被信任的声明及其影响面。
**并把「算出来的」与「派生的」分开列——一个转发点不算一次独立验证。**

---

## 与其他语言的关系

| 语言 | 它做什么 | 本设计不同在哪 |
|---|---|---|
| **C++** | 通用系统语言 | 不追求通用，不替代 C++ |
| **Rust** | 内存安全 + 零开销 | 语义跟 Rust（移动、借用），但多一层**成本契约** |
| **Dafny / F\*** | 全功能验证 | 只做**有界**的成本与效果检查，不做完整验证 |
| **Zig** | 显式优于隐式 | 同源，但把「多快」也纳入显式 |

**不做什么**：不自研后端；不承诺精确时间（只承诺量级 + 结构上界）；v0 不做代码生成与契约验证。

---

## 仓库结构

```
ai-lang-design/
├── src/                     代码（Python 骨架；目标语言是 Rust）
│   ├── lexer.py             词法分析
│   ├── parser.py            解析（宽松超集，带 span 诊断）
│   ├── checker.py           契约检查（F-1…F-9、B-3…B-16、可选性、目标）
│   ├── formatter.py         elench fmt（幂等、注释保留）
│   ├── highlight.py         语法高亮（ANSI + token 分类）
│   ├── elench.py               命令行入口
│   ├── check_docs.py        文档语法漂移检查
│   └── test_*.py            368 项测试
├── editors/                 VSCode 扩展（语法高亮）
└── 文档/
    ├── 语言介绍.md            ← 从这开始
    ├── 一屏对比.md
    ├── 待办.md
    ├── 设计/
    │   ├── 目前设计/          规格 v0.66、简明语法、目标、缺口、样例
    │   ├── 未来计划/          实现路线、M0/M00 计划、挂起项
    │   └── 归档/              已完成/被取代的记录
    ├── 实验/                  A/B 实验设计、实测题目、算术代价实测
    ├── 其他/                  申报书草稿、入门长文、学习路线
    └── _internal/            内部材料（不对外）
```

---

## 从哪里读

| 你想… | 读 |
|---|---|
| 快速判断值不值得 | [`文档/语言介绍.md`](文档/语言介绍.md) |
| **一屏看差异（vs Rust）** | [`文档/一屏对比.md`](文档/一屏对比.md) |
| 看语法长什么样 | [`文档/语法清单.md`](文档/语法清单.md) |
| 看一个完整例子 | [`文档/样例/示例-三个构造.md`](文档/样例/示例-三个构造.md) |
| 参与实现 | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| **下一步做什么** | [`CHANGELOG.md`](CHANGELOG.md) |
| 看条款（给实现者） | [`文档/语法清单.md`](文档/语法清单.md) |
| 看哪些是原创判断 | [`文档/设计/目前设计/评估/创新点登记.md`](文档/设计/目前设计/评估/创新点登记.md) |

---

## 已知缺口

| 编号 | 问题 | 严重度 |
|---|---|---|
| **G1** | 高阶函数的成本契约无法表达 | P0（卡 M2） |
| G9 | 成本模型缺少"内存搬运"维度 | P1 |
| G13–G16 | 链接模型 / 属性传递 / 标准库范围 / 门禁引导期 | P2 |

**M0 与 M1 均无阻塞。**

---

## 一句话

> **别的语言让你写"怎么做"。**
> **这门语言还让你写"要做到什么程度"，然后替你核对。**

---

## 许可与参与

| 件 | 位置 |
|---|---|
| 代码许可 | `LICENSE`（**Apache-2.0**） |
| 贡献指南 | `CONTRIBUTING.md`（**四道门必须同时绿**） |
| 贡献者协议 | `CLA.md`（也接受 DCO：`git commit -s`） |
| 引用格式 | `CITATION.cff` |
| 企业侧保留区 | `ee/`（占位，说明"开源到什么程度"） |

**一句话**：核心能力（语言 / 编译器 / 检查器 / 成本推导 / 门禁）**全部开源**，不做开源阉割。

---

## 命令行：**只写一个名字就够**

```bash
elench cost  sum          # 自动配成 sum.tu + sum.eln
elench check sum          # 同上
elench cost  sum.tu       # 只给声明，自动补实现
elench cost  sum.eln      # 只给实现，顺序自动摆正
elench export count       # 八个命令都一样接受只写名字
```

> **`<模块.tu> [<模块.eln>]` 是内部约定，不该变成输入负担。**
> 用户只记得住"这个程序叫 sum"。

---

## 自动链：`export` → `build` → `dispatch + measure`

**默认全自动**，一条命令拿到全部结论：

```bash
elench export sum          # 自己先 build，build 自动跑静态 dispatch（**不跑 measure**）
elench export sum --no-auto   # 只导出，不跑前面那些（CI / 排查用）
```

关掉的方式：`--no-auto`，或环境变量 `ELENCH_AUTO=0`。

**`measure` 不进自动链**：它要真执行候选，大范围下几十秒到分钟级。
分档表单独生成：

```bash
elench measure sum           # 只跑测量、生成 .dispatch.yaml
elench build sum --measure   # 构建时顺带测
```

---

## OTel：本机不用 Docker

```bash
python tools/otel_local_sink.py --out otel.ndjson   # 一个终端
$env:OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"   # 另一个终端
elench export count --push
```

**看板**：`python tools\otel_dashboard.py --file otel.ndjson --port 8080` → <http://127.0.0.1:8080>

## 安装（Linux / macOS 一键）

```bash
git clone https://github.com/xiaohuo123212/elench.git
cd elench
./install.sh              # 先看要做什么： ./install.sh --dry-run
elench --version          # 重开终端后
```

脚本做两件事之一，**不需要 sudo、不动系统 PATH**：

| 情况 | 做法 |
|---|---|
| 有 `pip` | `python3 -m pip install --user .`，装成真正的控制台命令 |
| 没 `pip` | 在 `~/.local/bin/elench` 放一个启动器，指向仓库里的 `src/elench.py` |

卸载：`./install.sh --uninstall`。
**Windows** 用 VS Code 扩展里的 `Elench: 安装命令行（加入 PATH）`。
