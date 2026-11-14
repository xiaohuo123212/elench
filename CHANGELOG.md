# Changelog

记录**对外仓库**的每一次变化。内部设计、规划、申请书、预印本草稿**不入库**
（见 `.gitignore`），只在本地维护。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> **三套版本号，不要混**：
> · **公开发布** `1.0.0`（本文件主线）
> · **语言与规格** `0.71`（`文档/语法清单.md` 与预印本里的口径）
> · **VS Code 扩展** `0.6.19`（`editors/vscode/package.json`，独立制品）

---

## [Unreleased]

### Added

- **2026-11-14（真库接入：从「读不懂」到「链得上」）**。目标不是加语法，是让**外部 C/C++ 库**
  第一次能被这门语言真正调用。逐项都有真库验收（代码在 `demo/probe-libs/`，第三方源码不入库）。
  - **`dsh cpp` 会发 `extern "C"` 声明了**（`_cpp_extern_decls`，`.eln` 的 `extern … from "lib";` 与
    `.tu` 的 `spec … { external "lib"; }` 两种都认，按名去重）；**容器实参在调用点补 `.data()`**
    （`std::vector` 不会隐式转 `const char*`，不补则声明对也编不过）。
  - **新增 `--include=<头>` / `--include-dir=<目录>`**（第三方头注入；头名含引号/尖括号/换行一律拒绝）。
  - **FFI 类型集从 `u64/i64/bool/Vec<u8>` 扩到含 `f64` / `f32` / 指针返回**
    （`const char*` → NUL 结尾的 `Vec<u8>`，运行期 `elench_cstr` 拷字节；**NULL 返回会明确 abort**，
    不会静默变成空串 —— 要判空就声明成 `u64` 自己 `== 0`）。
  - **真库验收**：`xxHash` 编译+链接+运行，输出与真 C 参考程序**逐字节相同**；
    `cJSON` 的 `cJSON_Version()` 打印 `1.7.19`，与解释器侧真 dll **逐字节相同**；
    `tinyexpr` 的 `te_interp` 拿到真 `double`（`sqrt(2)+1` = `2.414213562373095`）。
- **2026-11-14（三目条件表达式 `c ? a : b`）**：解析（右结合、比二元松比赋值紧）、类型（两支双向相容，
  不相容报 `E-Type-CondBranches`）、解释器（**只求值走的那一支**）、C++ 发射全通。
  **κ 取两支的 `max` 而不是相加**（只执行一支）；有反证断言（`κ(tern) - κ(only_b)` 在 n=1/4/16/64 上恒为 2.0）。
- **2026-11-14（块注释 `/* */` 与 `typedef struct`）**：这两处是真实 C 库里 3/3 都撞到的。
  块注释跨行、不嵌套、未闭合报 `E-Lex-BlockCommentUnterminated`，**行号靠逐字符推进保住**
  （原文件与「机械剥注释版」的诊断 `(line, col, code)` 集合逐条相同）；`typedef struct` 三种写法
  都降级成同一个 `struct X` 节点。实测：cJSON 诊断 **277 → 56（-80%）**、tinyexpr 62 → 39（-37%）。
- **2026-11-14（`repeats` 认否定形态）**：`!p(x)` / `p(x) == false` / `p(x) != true` 与裸 `p(x)`
  得到**逐字节相同**的 κ（此前三种否定全是 ⊤）。语料 115 个函数 κ **0 处变化**。
- **2026-11-14（新诊断码 `E-Type-Void`）**：**`void` 只能作表达式语句**。此前 17 个形状 / 10 类位置
  `check` 放行而下游各自兜底 —— 最坏的一条是 `print(g())`（g 返回 `()`）**check/run/cpp 三边全绿但 g++ 编不过**。

- **2026-11-13（κ 可算性收官）**。语料从 18 个模块扩到 **20 个**、**115 个函数**，
  **每个模块都有 `.tu`**；**带契约时 κ 全部可算（⊤ = 0/115）**，
  **契约覆盖率 102 → 114 / 115 = 99.1%**（口径：分子只数「✓ 达成」，不数「▲ 债务」）。
  - **五种新的循环上下界判据**（都在 `bound.py`，与 `check` 的 `E-While-NoBound` 同源）：
    位宽有界（`x & (x-1)` / `x >> k` / `x / k` ⇒ ≤ 类型位宽）、
    **区间减半**（`while (lo<hi){ mid=lo+(hi-lo)/2; … }` ⇒ ≤ ⌈log2(s₀)⌉+1，无端点 ⇒ 位宽上限 65）、
    **索引追逐**（`while (C[x]!=x) x=C[x];` ⇒ ≤ n(C)，依据 `语法清单` 的「κ 只界终止执行」口径）。
  - **三种新的递归激活数判据**：visited 守卫（激活 ≤ n(标记)）、
    区间对半分（两次自调用拿互不相交的各一半 ⇒ ≤ 2×(hi-lo)）、
    **树递归 `(D+1)·B^D`**（D 取 `.tu` 的 `recursion depth`、B 取外层循环轮数之积 —— 这一条让
    N 皇后的 `place`/`solve`/`main` 从 ⊤ 变成 `n^8` 量级的可算界）。
  - **符号指数 + log 域判阶**：`Cost("pow", None, (base, exp))`（指数可以是一个尺寸量），
    `compare` 在任一侧含符号指数时把两边转到 log 域再比（`log(n^n) = n·log2 n`），
    `evaluate` 对超大值**饱和**（不再构造 `4096^4096` 那种 15000 位整数）。
  - **多尺寸量 + log 判阶**：单项式记成 `(次数向量, 同维 log 次数向量)`，
    `n·(L² + log n)` 这类形状现在判得了；单尺寸量时逐条退化成既有规则。
  - **元素长度量 `L(x) = max_i n(x[i])`**：从字面量初始化列表、`push` 字面量、`split` 派生、
    整行赋值四种来源推；`L` 还能进 target。
  - **局部容器尺寸代入**：局部容器有可证上界**且每一处写入都被增长预扫建模**时，
    把 `n(局部)` 代成上界（`n(cur) → n(s)`）；未建模写入（如 `push_heap`）会挡住代入 ——
    否则 κ 会变成下界。
  - **`requires` 支持「变量 vs 变量」**：`requires lo <= hi;` 现在被 `check` 接受，
    并在调用点用**整数线性算术 + Fourier–Motzkin 消元**证明（`mid = lo + (hi-lo)/2` ⇒ `lo ≤ mid ≤ hi`）。
  - **新门 `order_matrix`（第 11 道）**：27 对「必须判对」的判阶对照表
    （`n² vs n log n`、`n^n vs 2^n`、`n^n vs n^10`、`n/log n vs n`、`loglog` 等），
    **判反方向即退出码 1**；「构造不出来」记 supported=false，不算判错。
  - 新建 `文档/语法速查.md`（828 行，30 条过期写法按实现逐条改正、每条带 rc 证据；
    它在 `文档/` 顶层，12 个代码块自动进 `doc_snippets` 门禁）。
  - 语料新增 `16_bitset` / `17_sortkey` / `18_kahan` / `19_ring2d` 四个模块（35 个函数），
    并补齐它们与 `15_unionfind` 的 `.tu`。

 **字符分类内建：`isdigit` / `isspace` / `isalpha` / `isalnum` / `isupper` /
  `islower` / `toupper` / `tolower`（2026-11-11，循环第 3 轮）**。
  全部 `O(1)`、不分配、`effects {}`——放在循环里 κ 只随圈数走。
  **语义按 ASCII 定死**（与 C++ 的 `std::isdigit` 不同：那套跟着 locale 走）：
  `< 128` 按 ASCII 判，`>= 128` 分类一律 `false`、`toupper`/`tolower` **原样返回**；
  负数按 **u64 补码**处理（C 是 UB，这里定义好）。
  六个 `is*` 返回 **`bool`**（不是 C 的 `int`）——本语言的 `if` / `while` 条件
  只收 `bool` 或指针，所以 `if (isdigit(c))` 可以原样写。
  `--compat` 下 `std::` 白名单加上这八个名字（只改名，不补语义）；
  `static_cast<unsigned char>(x)` / `static_cast<u64>(x)` 只在**值逐位不变**时
  折成 `x`（窄化 / 有符号 / 推不出类型一律 `E-Cast-Unsupported`）。
  依据：`demo/corpus/04_tokenizer.cpp`（改名即用）——`E-Compat-UnknownStd` 与
  `E-Type-Cond` 清掉，该文件**转绿**；语料通过率 2/6 → 3/6，错误总数 60 → 58。
  文档：[内建函数](文档/内建函数.md)「字符分类」一节、[语法清单](文档/语法清单.md)
  §P0 兼容层第 11 条。新增测试见 `tests/cases/test_compat.py`。
- **P0 兼容层：`check` / `run` 的 `--compat`——只加拼写，不加含义（2026-11-11）**。
  目标：`.cpp` 改名 `.eln` 就能用。接受 C++ 的**七种写法**，**一条语义都不改**
  （剥掉语法糖之后还是同一个程序）：
  ① `.eln` 里的 `struct`（`--strict` 仍按 F-4 拒绝）；② `using X = Y;` / `typedef X Y;`
  （别名表，**不进 AST**，认不出只报 `W-Compat-Alias`）；③ `auto x = expr;`
  （**编译期**推断；推不出报 `E-Compat-AutoNoType`，不猜）；④ `T{a, b}` /
  `vector<u64> v{1,2,3}`（落到现有 `struct-literal` / `list-init`）；⑤
  `f(xs.begin(), xs.end())` → `f(xs)`（**只在实参里紧邻成对、接收者同一路径时**，
  其余迭代器用法照旧报 `E-Call-UnknownMethod`）；⑥ `std::` 白名单
  （表外报 `E-Compat-UnknownStd` 并列出已支持的名字）；⑦ 返回类型 `void` → `()`。
  **开关只有一处**（`src/compat.py` 的一个布尔，由 `dsh._main_impl` 读 `--compat`
  设置）——将来载体换成 `.elnc` 后缀时只改那一行。**关掉时行为与以前逐字节相同**。
  新增 `tests/cases/test_compat.py`（94 项）：七条各自的 **κ 逐字节相同 /
  run 输出逐字节相同 / AST 同形**，外加"不带 `--compat` 时照旧报错"。
- **`export --full --out=报告.html`：一页一模块的静态成本报告（2026-11-10）**。
  新增 `src/html_report.py`，`export --full` 的 `--out` 多一个后缀分支——
  **数据仍是同一份 rows**（上界 / 承诺上限 / 实测），曲线仍由 `src/chart.py` 画，
  没有第二套渲染逻辑。`--out` 现在按后缀分派：`.svg` / `.md` / `.csv` / `.otel` / `.html`。
  - **「证明」与「符合」分开写**：κ 标「证明」（任何 n 都成立，蓝），
    实测点标「符合」（只覆盖测过的区间，红），判决（`ok` / `debt` / `unknown`）
    只说「阶对不对」——三者**不合成一个「通过」**；
  - **实测点 < 4 时不画连线**：只画点，并写「点数不足，不作趋势判断」
    （`chart.line_chart` 新增可选参数 `dots_only`，**默认空、原有 `.svg` 输出逐字节不变**）；
  - **两个时间戳**：`κ / 判决` 是「刚才（本地静态）」，`实测` 写明来自哪份数据、
    什么时候；没有实测时**明说「未测量」**，不留空；
  - **自包含**：CSS 与 SVG 全内联，**零外链**（连 `xmlns` 都去掉）、
    只用系统字体栈，本机没外网也能打开；20 个函数量级约 100–200 KB；
  - **确定性**：同一输入两次生成逐字节相同；时间戳可用 `--now=时间戳` 固定
    或 `--no-timestamp` 抹掉（`tests/cases/test_cli_smoke.py` 按字节比对）；
  - **转义**：κ 表达式、函数名、诊断消息一律过 `_esc()`，`<` `>` `&` `"` `'` 不裸插。
- **C++ 写法补齐：`&&` / `||` / `!`、range-for、三段式 `for`（2026-11-10 晚）**：
  依据 `文档/设计/目前设计/审核-命名与语法对照.md` §2「两种都保留」——C++ 也有、
  我们也有但形状不同的写法，**不是二选一**。
  - **`a && b` / `a || b` / `!a` 直接接受**：AST 与 `a and b` / `a or b` / `not a`
    **完全同形**（同一优先级、同一个 `binary` / `unary` 节点值），κ 不变；
  - **C++ range-for** `for (const auto& x : xs)` / `auto&` / `const T&` / `T`
    四种前缀都接受，AST 与 `for (x in xs)` 同形；
  - **C 三段式 `for (init; cond; step)` 接受**：parser desugar 成
    `init; while (cond) { body; step; }`，成本**完全按 `while` 推**——推得出就是
    有限上界，推不出就是 `⊤` + 原因（`E-While-NoBound`），**不低估**；
    本循环里的 `continue` 仍执行第三段（parser 把 step 深拷贝到 `continue` 前，
    与 C++ 的 `for` 语义一致，不会"卡住不推进"）；
  - **`E-For-NoIterator` 保留**：只在三段式头里出现 `begin` / `end` / `rbegin` /
    `rend` 时报（迭代器仍然不存在）。旧写法 `for (x in xs)` / `for (i, x in xs)` /
    `for (i in 0..n)` 与 `and` / `or` / `not` **全部照旧**。
- **预处理：`#define` / `#ifdef` 一族「先展开再分析」（2026-11-10，规格 §8.10）**：
  新增 `src/preproc.py`，在词法之前展开宏、求值条件编译，AST 建在**展开后**的程序上——
  κ 因此作用在真正被编译的那份代码上。
  - **支持**：`#include` / `#pragma`（丢弃）、对象式宏 `#define`、函数式宏（参数**按记号**
    替换；`#` / `##` / `...` **明确报错**）、`#undef`、`#ifdef` / `#ifndef` / `#if` /
    `#elif` / `#else` / `#endif`（按定义集求值，**未定义的宏为假/0**）、`#error`；
    **不支持的指令（`#line` 等）明确报错**，不当注释静默删掉。
  - **`-D NAME[=VALUE]`**：`check` / `run` / `cost` / `build` 都吃，**默认空**；
    没有 `-D _WIN32` 时 `#ifdef _WIN32` 为假——这是真实 C++ 的行为。
  - **行号映射**：展开后的文本与原文**逐行对齐**（指令行/死分支换成空行、宏体定义时
    折成一行），所以 token 行号 = **原文件行号**；宏展开的记号算**来自使用处**。
    宏里出的错在诊断里补一句「由宏 `NAME`（定义在 3:9）展开」。
  - **终止条件**：展开栈环检测 + `MAX_DEPTH = 100`（`E-Lex-MacroRecursion`），不无限展开。
  - **实测**：`cpp/docnums/main.cpp` 改名 `.eln` 后 **19 条错误 → 9 条**，
    `E-Lex-Preprocessor` 10 条**清零**（剩下的 9 条是关键字/词法位置类）。
- **真实 C++ 改名 `.eln`：判据从「词形」改成「语法位置」（2026-11-10）**。四条实测误报清零：
  - **`end` / `val` / `var` / `let` / `fn` 不再按词形报错**：词法器只出 `IDENT`，
    诊断移到 parser 的**位置判据**——`end` 只在**块终结符位置**（紧跟 `}` 或后跟
    种类名 `fn`/`if`/`match`…）报 `E-End-Removed`；`val`/`var`/`let` 只在
    **语句起始 + 后跟标识符**的声明形状报 `E-Decl-OldKeyword`。于是
    `auto it = xs.end();`、`size_t end`、`u64 end = 3;`、`s[end]`、`long long val`
    全部合法（`xs.end()` 仍报 `E-Call-UnknownMethod`：本语言**没有迭代器**，
    这是另一条真实缺口，不退回「`end` 已下线」）。
  - **`public` / `private` / `protected` 可以写**：`struct` 体里的
    `public:` / `private:` / `protected:` **接受并忽略**。本语言**没有访问控制**——
    struct 字段本来就全部公开，写 `private:` **不会**改变可见性（文档 §5.5 明写）。
    顶层 `public func f()` 仍报 `E-Keyword-LongForm`（本语言写 `pub func f()`）。
    struct 体同时接受 C++ 的 `u64 x;` 字段写法（等价于 `x: u64,`）。
  - **`namespace X { … }` 接受并展平**：内部 item 直接进全局作用域（匿名 namespace 同）；
    `foo::add` **不存在**（展平后就是 `add`）；展平重名报 `E-Name-Duplicate`
    并丢掉后来者，**不静默覆盖**。
  - **C++ 原始字符串 `R"( … )"`**：原始字符串里**没有转义**，`\s` 就是字面量
    （`std::regex` 常量几乎都这么写）。普通字符串 `"\s"` **仍然**报
    `E-Lex-BadEscape`（C++ 里也非法）——**不放宽**，消息里列出支持的转义集合
    （`\n` `\t` `\\` `\"` `\xNN`）。新码 `E-Lex-BadRawString`。
  - **错误恢复必须留痕**：一次跳过 > 20 行时额外报 `E-Parse-SkippedBody`。
    起因是真实事故：`cpp/docnums/main.cpp` 改名后只报 19 条，看似"基本能吃"；
    真相是 `namespace {` 处抛错后恢复**一路吞到文件末尾**，文件体从未被解析。
    展平之后同一文件 19 条 → **46 条**：多出来的是**以前被藏起来的真实缺口**
    （`class` / 成员函数 / 嵌套 enum / 字段默认初值 / `std::regex` 类型 /
    C++ range-for `for (const auto& x : xs)` / `? :` / `&&` `||` `!` / 指针…），
    不是回归。**「错误数少」不等于「解析通过」**——这是本轮最重要的一条。
- **`.eln` 吃下 C++ 的第一屏（R1-A，2026-11-10）**：`#include` / `#pragma` 接受并忽略，
  `using namespace std;` 接受（`#define` / 条件编译交给上面的预处理器）。
  真实 C++ 文件的第一屏——`#include <vector>` 一片 + `using namespace std;` + `int main()`——
  现在零诊断。产品目标仍是「一个 C++ 程序只改文件名就能当 `.eln` 用」。
- **C++ 标量拼写映射表（方案甲：整数一律 64 位）**：`int`→`i64`、`unsigned`→`u64`、
  `short`/`long`/`long long`→`i64`、`size_t`→`u64`、`char`→`u8`、`bool`/`double`/`float` 照旧；
  **定长名按位宽精确映射**（`uint32_t`→`u32`、`int16_t`→`i16`），不放大。
  **语义差异已写进 `文档/能力边界.md`**：这里的 `int` 是 64 位，与 C++ 的 32 位
  `int` **溢出语义不同**；要 32 位回绕就用 `uint32_t` / `int32_t`。
- **`string` / `std::string` 归一为 `vector<u8>`**（= `Text` 的运行时形态）：
  `std::string s = "hi";` 能 `check` 也能 `run`（`s.length()` / `print` 都可用）。
- 新诊断码（都带改法）：`E-Lex-MacroUnsupported` / `E-Use-UnknownNamespace` /
  `E-Use-StdSingleName` / `E-Type-UnknownStdName` / `E-Type-NoPair` / `E-Fn-AutoNoReturn`；
  `E-Type-MissingUsing` 已实现但**开关关闭**（见下）。
- **指针的运行期接线**（2026-11-10）：`&x` 产出**地址**（指向环境单元 / 容器元素 / struct 字段，**不是拷贝**），`*p` 的读与写都落到那块存储，`p->f` 同一条路；`nullptr` 是假值，`p == nullptr` / `if (p)` 判空可用；解引用空指针报 **`E-Pointer-NullDeref`**（运行期，**不崩宿主**，与静态那条同名不同判据）。指针在七个拷贝点里**按标量**（拷地址、不深拷），`S b = a;` 仍是深拷贝。`E-Pointer-RuntimeUnsupported` 退役。
- **指针消歧改用 C++ 规则**：判据是「`*` 左边的名字是不是已知类型名」，不是空格。`u64 * p;` / `char * argv[]` / `MyStruct * q;` 都是**声明**；`a * b;` 仍是乘法；`a ** b` 仍是幂。
- **`walk.py` / `kinds.py` 补登记**：`deref` / `lit-null` / `type-ptr` / `array` / `val-decl` 归位，并修掉「表说没实现、实际已实现」的漂移；`ALL_KINDS` 补齐 **22 个** parser 真会产的 kind（此前它们永远不算孤儿）。新增 `tests/cases/test_drift.py` 的 parser 对账测试（用 `ast` 扫 `Node(...)`，注释骗不过去）。
- **对外文档合并**：`入门30分钟.md` + `入门/入门指南.md` → **`文档/入门.md`**；`语言介绍.md` + `项目介绍.md` + `一屏对比.md` + `教学-3分钟演示.md` → **`文档/介绍.md`**；`BUG/README.md` 并入 `文档/README.md`。入库文档 **24 → 19** 份，相对链接全部同步。

### Changed

- **2026-11-14**。**方法返回类型与解释器对齐（35 处）**：`insert`/`put`/`remove`/`erase` 返回 **`u64`**
  （1/0 计数，此前文档与静态类型都写 `bool`）；`contains` 返回 **`bool`**（谓词，这样 `if (m.contains(k))`
  才编得过）；`keys()`/`values()` 返回元素类型的 `vector`（此前落 `TY_UNKNOWN`，等于什么都放行）；
  `assign(n,x)` 统一成 **`void`**（此前 `vector<u64> ys = xs.assign(2,0)` 能过 `check` 却编不过）。
- **2026-11-14**。**第三方 `#include` 不再静默丢弃**：标准头（138 项白名单，数据在 `preproc.py`）
  照旧忽略，**非标准头报 `E-Lex-IncludeUnresolved`** —— 此前连「不存在的头」都 `exit 0`，
  让 `check` 通过成了假象。四份公开文档与 lexer 的 docstring 一并改口。
- **2026-11-13**。**成本域补齐「带数量参数的容器方法」**：三参 `insert(pos, count, v)` 与 `assign(n, v)`
  此前按「一次调用 = +1 个元素」记，**符号化的 count 让 κ 变成低估**
  （`xs.insert(0, n, 7)` 报 `κ ≤ 16` 而真实插了 n 个）。现在 count 是符号项，推不出 ⇒ 退 ⊤，不再当 1。
  连带修好一处**因低估而通过的 target**：`10_backtrack` 构造函数 `O(1) → O(n)`。
- **2026-11-13**。`dsh cpp` 从「0/19 出码」到「**19/19 出码 · 19/19 g++ 编译 · 19/19 语义一致**」：
  按接收者类型分派容器方法（此前 `insert` 一律按向量位置版发射）、`?` 类型按位置处理（返回位置发 `void`）、
  语句层（block/for/break/continue/assign-op）、表达式层（通用 method-call/field/字面量/位运算）、
  按需发射头文件。

- **2026-11-13**。`merge_sort` 的 κ 从 **O(n²) 收紧到 Θ(n log n)**：单层成本按
  `A + B×s` 分解后**按层求和**（`Σ_k 2^k·c·(s/2^k) = c·s·log2 s`），
  于是写 `target O((hi-lo)·log(hi-lo))` 判**达成**（此前会判成假债务）。
  同一个 n 上 κ 紧了 **232 倍**，`κ(2n)/κ(n) → 2.15`（二次会是 4.0）。
- **2026-11-13**。递归函数的「声明 target 会反向影响 κ」这一层被去掉 —— 以前
  `.tu` 里写了 target 会被并进自调用的下界（κ = n^(k+1)），**任何 target 都判债务**；
  现在栈顶是自己的直接自调用时不读自己的 target。
- **2026-11-13**。`dsh cost` 的尺寸量口径保持不变，但 **κ 里不再出现别的函数的形参名**：
  内联时被调方的标量形参必须换成调用方的表达式（换不出就 ⊤），
  113/115 个函数的 κ 逐字节未变。

 **`export --out=报告.html` 两处可读性打磨（2026-11-10 晚）**：只动排版与措辞，
  **数据不变、`.svg` 输出逐字节不变**。
  - **κ 分段折叠**：分段主体**逐字符相同**时折成「分段：n < 1 / 1 ≤ n ≤ 4096 /
    n > 4096」＋「主体（三段相同）：…」两行；**只要有一段不同就原样打印**
    （宁可长，不把不同的式子说成相同）。原文仍留在 `<details>` 里，可选可复制。
  - **比值带方向**：`κ = O(n)` 对 `target = O(n²)` 的比值本来就趋近 0，旧文案只印
    `比值 ≈ 0.00`，读着像算错了。现在 `< 0.01` →「κ 远低于 target（约 1/n）：
    这是好消息——界比承诺紧得多」；`0.01–1` 正常给数值；`> 1` → **红色块**明写
    「κ 超过 target」（债务/异常不许藏在一行小字里）。
- **`func` 不再必需：函数声明用 C++ 形状**（R1-A，规格 §8.2）。
  `u64 add_one(const u64& x) { … }` 与 `auto add_one(const u64& x) -> u64 { … }`
  都能用；**`func` 仍然接受**（旧代码 / 17 个样例不用改），只是不再是必需形式。
  `.tu` 里的 `spec` 不受影响（那是契约文件，不是 C++ 源码）。
- **`std::` 前缀与短名是同一个类型**（规格 §8.3 / §8.11）：
  `std::vector<u64>` ≡ `vector<u64>`，`std::map` / `std::set` / `std::unordered_map` /
  `std::unordered_set` / `std::array` / `std::optional` 同理，`std::expected<T,E>` → `Result`。
  修掉了 `.tu` 写 `vector<u64>&`、`.eln` 写 `std::vector<u64>&` 就报
  `E-Spec-SigMismatch（std.vector）` 的那个 bug（`std::` 曾被当成路径名）。
  短名**仍然不需要** `using namespace std;`：`REQUIRE_USING_FOR_SHORT_NAMES` 开关
  目前为 `False`——打开它会让 16/17 个样例与 16/17 个 `.tu` 全红（按用户定的纪律先报告）。
- **`const u64& x` 只读形参不再报 `E-Ref-Scalar`**：`const` 形参不承诺写回，
  C++ 最常用的形参写法现在照 C++ 写就能过；可写的 `u64& x`（标量）仍然拒。
- **`int` / `float` / `char` / `string` 从关键字表里移除**：它们原是**字面量 token 的
  种类名**，被误收进 `KEYWORDS`，于是 `int main()` 的 `int` 是"整数字面量"、
  `char c;` 的 `char` 是"字符字面量"。改后它们是普通标识符（类型位置由映射表处理）。
  `E-Type-NoChar` 随之退役（`char` 现在合法）；VS Code 语法文件的 keyword 表同步。

- **`.tu` 变成可选：没有 spec = 没有契约声明 = 不报错**（2026-11-10，R1-B / 规格 §8.9）。
  产品目标是「一个 C++ 程序只改文件名就能当 `.eln` 用」，而真实 C++ 程序**没有 `.tu`**；
  以前 `E-Fn-NoSpec`（F-1）是 error，连 `dsh run bare.eln` 都会被挡住。
  - **默认档**：`E-Fn-NoSpec` 从 `error` 降为 `warning`（不拦 `check` / `run` / `build`）；
    `--strict` 下**保持旧行为**（error，我们自己的仓库自检用）。
  - **没有 `.tu` 文件时**不逐条报，只给一条 `I-Fn-NoSpec-Module`：
    「本模块没有 .tu，成本契约不参与判定（N 个函数未声明契约）」。
  - `cost` 对没有 spec 的函数**照常算 κ**，但**不参与判定**（不算债务、不算错误），
    汇总里给一行「N 个函数未声明契约」；完全没 `.tu` 时明说「没有契约可判」并 **exit 0**。
  - `check` / `run` / `build` 不再把 `.eln` 同时当 `.tu` 解析一遍（以前诊断带 `[.tu]`
    前缀**重复出现**）；没有 `.tu` 时 `check` 的状态表仍列出函数（state=`unchecked`）。
  - `build` 无 `.tu` 时产出的成本表把 `verdict` 记为 `undeclared`（不是 `unknown`）。
  - 新增码 `I-Fn-NoSpec-Module`（提示，不改变退出码）；`E-Fn-NoSpec` 码名不变。

- **多尺寸量：能证明超出就给 `debt`，只有"不可比"才 `unknown`**（2026-11-10）。
  以前只要「逐项支配」不成立就一律 `unknown`，理由写「两个尺寸量的关系未知」——
  于是 `κ = a×b` 对 `target = a + b`（**可证**超出：每一项都缺一维）和
  `κ = a×b` 对 `target = a² + b`（**不可比**：`b ≫ a` 时 κ 大、`b ≪ a` 时 target 大）
  拿到同一句话，用户不知道自己该不该优化。现在三档：
  - **(A) 可证超出 → `debt`**：存在 κ 的单项式 e，对 target 的**每一项** f 都
    `e ≥ f`（逐分量）且 `e ≠ f`。此时 `x^(e−f)` 各维指数都 ≥ 0、至少一维 > 0，
    `κ/target ≥ c_e·x^e / Σ_f d_f·x^f → ∞`——不存在常数 C，是**证明**不是猜。
    文案写明**哪个尺寸量高了几次**（例：`κ 的 a×b 在 b 上的次数 1 高于 target 的
    最高次数 0`）。
  - **(B) 不可比 → `unknown`**（**只有这一种**是判不了）：有的维 κ 大、有的维
    target 大，谁增长快决定结论。文案给具体对照（`κ 的 a×b 在 b 上高于 target 的
    a²、在 a 上低于它`）。
  - **(C) 逐项支配 → `ok`**（不变）。
  - **不改 κ**（`tests/golden/kappa.json` 逐字节不变）、不改样例 target；新增
    `_multi_exceed` / `_exceed_note` / `_incomparable_note`，`Verdict` 加只给
    `debt` 用的 `detail`（为 None 时渲染逐字节不变）。判定翻转：**语料 0 个**
    （`文档/样例` / `_diff` / `demo` 全部不变），`tests/` 里 1 个（KMP 的
    `κ=text×pattern` vs `target=text+pattern`：unknown → debt，那本来就是**真的超**，
    旧判定只是"不知道"）。`tightness_compare` 与 `kappa_vs_ops --strict` 破界仍为 0。

- **步长 / 分块循环的 κ 收紧 3 倍：`while (i + 3 <= n)` 从 `n` 变成 `n / 3`**（2026-11-10）。
  `Bound` 新增 `ceildiv`（`ceil(e / c)`，`c` 是正整数字面量），`Cost` 侧用已有的 `Cost.div`
  表达成 `e / c`。C / C++ / Rust 里「每轮吃 k 个字节」的自然写法（base64 / CRC32 / hexdump）
  以前上界一律沿用 `i < n` 的 `n`——**松 3 倍**（`_tier_b/bits/base64` 的自然写法
  `120n → 114n`，比值 114 对真实圈数 `n/3`）。
  - **只在证明得出时才收紧**：`_exact_step_increment` 要求「声明处初值字面量 0 +
    全函数里对计数器的写恰好一处、且是循环体的直接子语句 `i = i + c` / `i += c`
    （与守卫里的 `c` 相同）」；步长是变量（`i += k`）、写在 `if` 里、写两次、循环外
    另赋值、按引用传出、步长与守卫 `c` 不等——**一律退回旧上界 `e`**。
  - 紧后的实测：`while (i + 3 <= n)` 的 κ 系数 19 → 6.33（`n / 3`），κ/ops 3.5× → 1.2×；
    `base64_encode` 自然写法 `114n → 38n`，κ/ops 3.3× → 1.15×（n=256）。
  - **κ 仍是上界**：真圈数是 `floor(e / c)` ≤ `e / c`，`tightness_compare` 与
    `kappa_vs_ops --strict` 破界 0。
  - `Bound` 的同步点：`render` / `variables` / `substitute` / `_mark_count`，Cost 侧
    `bound_to_cost_inst` / `_bound_to_cost_scoped` / `_substitute_size` /
    `_substitute_capacity`；断言在 `tests/cases/test_bound.py`。
- **`for (auto& x : xs)` 现在真的改得动容器**（修前被当成按值拷贝的 `in`，**静默错值**）；`const auto&` 不拷贝。κ 同步：引用迭代不加元素拷贝项；按值迭代补齐 **array / struct / owning** 三类元素的拷贝代价（原来只记 owning），owning 元素每轮另记 1 次分配。
- **`kernels` 默认内联**：发射成 `static inline` + `[[gnu::always_inline]]`；体超过约 200 个操作节点降级为普通 `inline` 并报 `W-Kernel-TooBigToInline`。

### Fixed

- **2026-11-14**。**`dsh cpp` 自检失败仍写出 `.cpp` 并打印路径** —— 构建脚本会当成成功。
  改成先自检、后落盘；失败 ⇒ 不写，`--output` 的旧产物删掉并说明。
- **2026-11-14**。**`cost` 对「既无实现又无契约」的调用给出绿色 κ**（rc=0），而那正是 E18 说的
  「记 1 个操作不是上界」。现在这类调用 ⇒ `rc=1` 并说清 κ 不可信（`--no-check` 保留调试逃生门）。
- **2026-11-14**。**`spec … { external "lib"; }` 的返回值被按整数读**：`te_interp("1+2")` 返回 **`1.0`**
  （真值 `3.0`），无诊断。改成走 `ffi_c.restype_for`。
- **2026-11-14**。**未声明的 `foo::print(...)` 静默退化并「通过」**：`::` 左边未声明时，成员名撞内建就
  被当全局调用 —— `fmt::print` 因此「check 过、输出正确」，让人以为库接进来了（其实 `#include` 被丢）。
  现在报 `E-Type-Unknown`；`std::printf` / `std::vector` / 枚举变体 / 泛型 `T::size()` 不受影响。
- **2026-11-14**。**用户定义的函数现在遮蔽内建**：用户写 `slice(...)` 撞内建原地 `slice`，
  会误报 7 条 `E-Const-Mutate`（3 个探针库中 2 个踩到）。
- **2026-11-13**。**`unordered_map.values()` 返回的是键**（`interp.py` 里 `values` 与 `keys` 共用分支）；
  用户代码能触达，修前 8 条断言直接失败。
- **2026-11-13**。**后端五个「静默生成错代码」**：变量声明丢初始化表达式、可变借用形参被加 `const`、
  **`else` 分支整段丢失**、未知类型静默变 `uint64_t`、`dsh cpp` 不跑 `check`。

- **2026-11-13**。**`requires` 用现行拼写写的前提条件曾被静默跳过**：判定内核只认已弃用的
  `.length()`，于是 `requires xs.size() > 0;` **什么都不报**（契约看起来被检查了，其实没有）。
  现在两种拼写都认；**认不出的形状报 `E-Require-NotCheckable`（认不出 ≠ 放行）**。
  顺带修掉「`if` 条件认不出时整段跳过调用」的洞。
- **2026-11-13**。**10 类「`check` 通过但 `run` 崩」的形状收口**：位置式 `struct` 初始化
  `S x = {1,2};`（`--compat` 同文件下**支持**并按字段序 desugar，跨文件则检查期明确拒绝）、
  非容器大括号初始化、非聚合取字段、`as` 的操作数、枚举值上的 `.`（以前 `run` **静默输出 0**）、
  高阶内建的容器/回调形状。新增 `test_check_run_consistency.py`，断言每个形状
  **`check` 结论 ⇔ `run` 结论**。
- **2026-11-13**。`tools/kappa_vs_ops.py` 对**递归函数**会报**假破界**：它用 `ops_of(fn)`，
  而那是"该函数各次激活的累计"，递归时把内层激活的计数重复算到外层（实测 3.3×）。
  改成 `ops_of(None)`（reset 后只跑一次调用，总数就是整棵调用树的成本）。
- **2026-11-13**。`03_matrix.main` 的**假债务**：κ 里混进了 `matmul` 的局部标量 `n`，
  配出 `O(n) vs O(1)`；修完是常数 `782`，判达成。
- **2026-11-13**。`13_numeric.stddev_milli` / `main` 的 κ 里曾有一个**自由符号 `x`**
  （`isqrt` 的形参没被实参代换），`evaluate` 对它返回 `inf`（保守，不是低估），但 target 永远写不出来。
  现在换成调用方自己的局部 `acc`，并在区间减半规则落地后**归一成常数**。

 **`<=` 循环的圈数少一圈 ⇒ n = 0 时 κ < ops（真上界破了）**（2026-11-10）。
  最自然的写法 `for (u64 i = 0; i <= n; i = i + 1)` 的圈数是 `n + 1`，而
  `src/cost_domain/bound.py` 的 `_sub_or_plain` 把 `incl` 收下却**没有 +1**
  （注释理由是"上界取松不失真"，实际正好相反）。实测
  `func fl(u64 n) { u64 s = 0; for (u64 i = 0; i <= n; i = i + 1) s = s + i; return s; }`：

  | n | ops | 修前 κ | 修后 κ |
  |---|---|---|---|
  | 0 | 22 | 20 ✗ **破界** | 34 ✓ |
  | 1 | 33 | 34 ✓ | 48 ✓ |
  | 2 | 44 | 48 ✓ | 62 ✓ |
  | 3 | 55 | 62 ✓ | 76 ✓ |
  | 8 | 110 | 132 ✓ | 146 ✓ |

  - 修法：`i <= e` → `e - i + 1`、`i >= e` → `i - e + 1`（`_plus_one` 造 `add`
    节点，`Cost.add` 化进常数项）。**`<` / `>` 那一支逐字不变**（改前改后
    κ 字符串相同，`tests/cases/test_bound.py` 新段落逐字节钉住）。
  - 同一处病另外两处一并修（都是"`<=` 少一圈"）：**递减循环** `while (j >= 1)`
    （`v0` 是 `>` 的答案，`>=` 要 `v0 + 1`，实测 i64 `while (i >= 0)` 在 n = 0
    时 κ = 20 < ops = 21）；**`_log_loop`** 的 `while (i <= n) { i = i * 2; }`
    （真圈数 `floor(log2 n) + 1`，实测 n = 1 时 κ = 13 < ops = 15，界改成
    `log2(n + 1)`，仍是 `O(log n)` 阶）。
  - **分块扫描不用改**：`i + c <= e`（初值 0、每轮恰增 c）的真圈数是
    `floor(e / c)`，`ceil(e / c)` 本来就是上界。
  - **全语料 κ 只有 10 个函数变**（逐字节比对 HEAD 与修后：607 个模块 / 1212 个
    函数，含 `文档/`、`demo/`、`tests/`、`_diff/`、`_tier_a`、`_tier_b` 与各
    `_*_probe/`），**全部是 `<=` / `>=` 守卫多一圈**；`<` / `>` 一个都没动
    （`_iplusc_probe/baselt.step3_lt`、`_stepguard_probe/p.f_lt3`、
    `_cppmatrix/loops/p02_reverse_*` 的 `<` / `>` 兄弟逐字相同）。
    `文档/样例` 里没有任何 `<=` / `>=` 循环，所以 `tests/golden/kappa.json`
    22/22 **不变**；`tools/kappa_vs_ops.py --strict` 破界仍 0，
    `tools/tightness_compare.py` 与 baseline 比 0 个点变化。
  - 断言：`tests/cases/test_bound.py` 新增 [11]（界的文本 + `κ ≥ ops` 在
    n = 0,1,2,3,8 全成立 + `<` / `>` 的 κ 里不许出现 `(n + 1)`）；
    `tests/cases/test_log_loop.py` 新增 [3]。
- **没有数学内建：`sqrt` / `floor` / `ceil` / `abs` / `log2` / `popcount` / `clz` / `ctz`
  一个都没有，而 FFI 也补不上**（2026-11-10，`_cppmatrix/misc/results/24*`）。
  实测 `f64 r = sqrt(16.0);` → `E-Call-Undeclared`；FFI 那条路
  `external spec abs(i64) -> i64` 能跑，但 `f64` 参数直接
  `E-FFI-Unsupported`（C ABI 后端不支持 `f64`）。于是"算个平方根"只能牛顿迭代 /
  二分，还要在 `.tu` 里给一个假的 `O(log n)`。
  - **新增 10 个内建**（全 `O(1)`、不分配，登记在 `stdlib` 的 `ret=` / `cost="1"` /
    `alloc=0` / `FAIL_MODE=never`）：
    `sqrt` / `log2` / `floor` / `ceil` / `round` / `fabs`（`f64 -> f64`）、
    `abs`（`T -> T`，整数与 `f64` 都要）、`popcount` / `clz` / `ctz`（`u64 -> u64`）。
  - **语义逐条按 C**：`sqrt(-1)` → NaN、`log2(0.0)` → `-inf`、`log2(-1)` → NaN、
    `floor`/`ceil` 返回 **f64**、`round` 是 **`.5` 远离零**（`2.5 → 3.0`、`-2.5 → -3.0`，
    不是 Python 的银行家舍入）、`fabs(-0.0) = 0.0`。
  - **符号**：`abs` 无符号恒等、有符号取负、`f64` 走 `fabs`；返回类型 `ret="arg0"`
    （跟实参走），`typecheck` 新增 `f64` / `arg0` 两个消费分支。
  - **`clz(0)` / `ctz(0)` 报错**（C 是 UB）。不选"返回 64"：那等于**编一个值**，
    `64 - clz(x)` 会静默变成 `0`，正是"看起来合理的错答案"。消息里给
    `if (x == 0)` 的写法。
  - 文档：[内建函数](文档/内建函数.md) 由 `stdlib` 的 `empty=` + 生成器模板重新生成
    （新增「数学」表与「数学内建：值域、符号、以及 `clz(0)` 为什么报错」一节）；
    `文档/能力边界.md` §2.2.1 从"唯一的日常必撞缺口"改成"已经有"（§0.3 的
    `91 个内建` → `102`、§1.1/§1.3/§2.2/§4.1/§5 同步）；
    `文档/语法清单.md` 的平方根段与成本代数的 `sqrt`（同名两义）写清；
    `文档/C++惯用法对照.md` §3.4 / §6 标「已做」。
  - 测试：`test_stdlib.py` +47（每个函数的 C 语义、`clz(0)`/`ctz(0)` 报错、
    `ret`/`cost`/`alloc`/失败语义登记、`f64`/`arg0` 的 typecheck 分支、
    端到端静态通过、循环里线性的 κ）。
- **`for_each(map<K,V>, f)` 运行期崩；`map_put` / `map_get` 对语言 `map` 也是"`check` 全绿、
  `run` 抛 `TypeError`"**（2026-11-10，`_tier_b/structs` K2 + `_cppmatrix` D 类）。
  - **`for_each` 认的容器补齐**：以前只认 `Vec` 与 stdlib 的 `HashMap`，文档《语法清单》
    写着「遍历映射表 | `for_each(m, f);`」而 `m` 是语言 `map<K,V>` 时直接
    `InterpError: for_each 的第一个实参必须是 Vec 或 HashMap`。现在认
    `Vec`/`array`/`set`/`unordered_set`（一元 `f(x)`）与 `map`（键序）/`unordered_map`
    （顺序未指定）/`HashMap`（二元 `f(k, v)`）；仍是**快照**遍历，返回遍历了几个。
  - **`map_*` 自由函数选「编译期拒绝」**（二选一里的一支）：`map_new()` 的返回类型从
    `map<?,?>` 改成**独立的 `HashMap<?,?>`**，`map_put` / `map_get` / `map_has` / `map_len`
    只接受它；语言 `map`/`unordered_map` → **`E-Type-ArgMismatch`**，消息直接给方法式写法
    （`m.insert(k, v)` / `m.get(k)` / `m.contains(k)` / `m.length()`）。
    **理由**：`map_put` 登记的成本是 `O(1)`（哈希 + 有界 8 次探测），语言的 `map` 是
    平衡树 `O(log n)`——把自由函数扩过去，κ 会**低估**（少算比多算危险）；而方法式那条
    路走容器方法的 `log2(n)` 记账，成本是对的。
  - **不留默认构造的半成品**：`HashMap<K,V> h;` 没有默认值（容量/探测策略由 `map_new()`
    定），运行期报错并指出写法。
  - 文档：`文档/内建函数.md` 由 `stdlib` 的 `empty=` 重新生成（`map_new` → `HashMap<?,?>`、
    4 条自由函数的「只接受 HashMap」、新增 `for_each` 一行）；生成器新增
    「两种映射表：`HashMap` vs 语言 `map`」一节；`文档/语法清单.md` 的遍历那行写清容器
    与回调元数；`文档/C++惯用法对照.md` §4 的 D 类行改「已修」。
  - 测试：`test_foreach.py` +4（语言 map/set/unordered_map、`(k, v)` 按位置绑定）；
    `test_typecheck.py` +7（4 条自由函数逐个拒、`unordered_map` 也拒、`HashMap` 与
    方法式两种正例、`map<…> = map_new()` 的类型不符）。
- **`open_read` / `open_write` 成功值不包 `Ok`：`match { Ok(r) => … }` 永远不命中**（2026-11-10，
  `_cppmatrix/misc/` D 类）。文档与 `FAIL_MODE=err` / `ERR_ENUM` 都写 `Reader!` / `Writer!`，
  实现却在成功时返回**裸** `ReaderVal` / `WriterVal`——`next_line` / `close` 照样能用，
  所以没人发现；而文档推荐的 `match (open_read(p)) { Ok(r) => … }` **永不命中**、
  被 `_` 静默吃掉（`_cppmatrix/misc/results/06c_open_ok` 实测 `fallback arm`）。
  - **修法**：成功值改成 `Ok(Reader)` / `Ok(Writer)`（与 `Result` 语义一致）。`?` 照旧解包
    （`const Reader r = open_read(p)?;`，`tests/cases/test_stream.py` 里四段都还在用）；
    `match` 的 `Ok(r)` / `Err(e)` 两条臂都通。
  - **不留静默**：不 `?` 也不 `match` 就把 `Reader!` 交给 `next_line` / `close`
    （`Writer!` 交给 `write_chunk` / `close_write`）现在**明确报错**并给出解包写法；
    以前 `close(ok)` 会被静默忽略（返回 1 但没关句柄）。
  - 文档：`文档/内建函数.md` 的备注由 `stdlib` 的 `empty=` 重新生成（成功 → `Ok(Reader)`）；
    `文档/C++惯用法对照.md` §4 的 D 类行改成「已修」。
  - 顺带修 `tests/cases/test_doc_snippets.py` 的一处**假红**：行内片段只测**完整语句**
    （以 `{…}` 或 `;` 结尾）。`文档/C++惯用法对照.md` 里的 `while (n --> 0)` /
    `while (cin >> x)` 是**故意的反例**（C++ 写法这里没有），以前被当"必须能编译"。
- **`Option` / `Result` 能赋给 `u64`，而且比较静默为假**（2026-11-10，`_verify/report.md` K5）。
  `u64 x = vec_pop(h);` 的 `check` 全绿，`print(x)` 是 `Option.Some(9)`、`print(x == 9)` 是
  `False`——**错值而不是错类型**，比报错更难发现。`bool b = vec_pop(h)`、`const u64`、
  `-> u64` 里的 `return vec_pop(h)` 一样放行。
  - **根因**：`vec_pop` 在 `stdlib.py` 登记 `ret="any"`，类型检查器只有一条
    `any → UNKNOWN` 的分支，而 `UNKNOWN` 与**任何类型**兼容。同一个洞还盖着 16 个内建
    （`map_get` / `env_get` / `deque_pop_*` / `option_*` / `open_read` / `json_parse` …）；
    `FAIL_MODE` 里那套 `"option"` 标记**没有任何代码消费**，所以"表里写了 option、
    注册却是 any"没人发现。
  - **修法（根治）**：17 个内建全部登记**能推出具体类型**的 `ret`——`vec_pop` →
    `optional<T>`（取容器的元素类型）、`map_get` → `optional<V>`、`parse_u64` →
    `u64!`、`open_read` → `Reader!`、`json_parse` → `Json!`、`map_new` → `map<?,?>`…
    `typecheck` 逐词消费它们（`_builtin_ret_ty`）；词表外的值退回**不透明 `Any`**
    （不与标量兼容），不再退回 `UNKNOWN`。
  - **兜底 + 静默比较**：新增 `E-Type-OptionToScalar`——包装值被赋给标量、从 `-> T`
    返回、参与比较 / 算术、取下标 / 调方法时**静态报错**并给出解包写法。
    `vec_pop(h) == 9`（运行期静默 `false`）与 `vec_pop(h) + 1`（宿主 `TypeError`）
    现在都过不了 `check`。
  - **对账**：`tests/cases/test_failure_modes.py` 新增 F-4（`FAIL_MODE` 的
    `option` / `err` 必须与 `ret` 的 `option-*` / `result-*` 一致）；
    `test_stdlib.py` 断言每个 `ret` 都在 `RET_KINDS` 里、每个词都有消费分支，
    且没有内建再用 `ret="any"`；`test_typecheck.py` 新增 K5 正反例。
    顺带修掉 `utf8_chars_vec`（登记 `vec-u64` 但类型检查器漏了分支，把
    `vector<u64>` 说成 `u64` 的假阳性）。
- **`struct` 的容器字段读出来是副本：`s.v.push(...)` / `s.v[0] = 9` 静默丢失**（2026-11-10，
  `_verify/report.md` K1）。`vector<u64>` 字段三次 `push` 只剩第一个。
  - **根因**：字段读 `_field` 走 `_convert_to`，元素要从裸 `int` 重标成 `U64` 时旧实现
    `[_convert_to(x) for x in v]` **重建列表**；push 侧又只在接收者是普通变量时推导
    元素类型，字段接收者存裸 `int`——于是第二次 `push` 写在副本上（`s.v[0] = 9` 同理）。
    决定性反例：先把值装进 `u64` 变量再 push 就全生效（元素已是 `U64`，不触发复制）。
  - **修法**：`_convert_to` 对 `vector` / `array` / `FixedVec` **就地重标元素、返回同一
    对象**，不再为类型转换换列表；`_field` 遇到运行时形态变化（普通 list → `vector<u8>`
    的 `Text`）会把结果写回字段；`push` 的元素类型也按字段的声明类型推导。**七个拷贝点
    一个没动**：`S b = a;` 之后改 `b.v` 仍不影响 `a.v`。
  - 修好：`vector<u64>` / `vector<u32>` / `array<u64,N>` 字段、嵌套 struct 里的
    `vector<u64>`、经 `S&` 形参写入；`vector<u8>` / `map` / `vector<i64>` /
    `vector<vector<u64>>` 逐个复测不回归。
  - 测试：`test_interp.py` 新增 `test_struct_container_field_shared_storage`（12 项）。
    性能：`_diff/matmul`（dim=30）与 `_diff/bubble_sort`（n=200）在噪声内（±1%）；
    字段读热路径（每轮 `b.v[i]`）快约 6%——少一次整表复制。
- **`recursion depth <= N` 声明不生效，而且让 κ 小于实测（上界被越过）**（2026-11-10）。
  三份逐字节相同的 `.eln` 只改 `.tu` 的 `recursion depth`（8 / 64 / 256），最大
  可跑深度**都是 75**——上限只由宿主 `sys.setrecursionlimit` 决定，声明唯一的
  落点是 κ 里那个常数 `4 + 13N`。于是「声明 8、实跑 75 层」时 `κ=117` 而实测
  `ops=831`：**承诺的上界被越过**（`_verify/report.md` K3）。
  - **声明现在是运行期断言**：按函数统计当前帧层数（`N` 数是自调用嵌套层数，
    `N+1` 帧与 κ 的 `(N+1) × 体一次` 同口径），超过就抛
    `E-Recursion-Depth-Exceeded`（fail-fast，与 `E-Repeats-Exceeded` 同一风格）；
    开销是每层一次 dict 读 + 一次 dict 写（per-thread，线程模式不串味）。
  - **不再用 `sys.setrecursionlimit` 冒充闸门**：宿主上限是进程级一个数、
    数的是帧不是层（`chain` 13.3 帧/层、`rec_tree` 9.3），多函数互相干扰。
    引擎只在声明需要时**抬高**它（按 `Σ(N+1)`，硬顶 120,000），让语言闸门
    先于宿主栈响：声明 256 跑 300 → `E-Recursion-Depth-Exceeded`；
    声明 4000 现在真能跑 3000 层（修前 75 层就崩）。
  - **宿主异常不再外泄**：撞上宿主栈（没声明 / 声明超出硬顶）报
    `E-Recursion-HostLimit`，文案带**函数名 + 第几层 + 已声明的深度**，
    不再是 `RecursionError: maximum recursion depth exceeded`。
  - **读不出正整数的声明也报错**：`recursion depth <= 0` / `<= n` 以前被
    「有这条子句」放行，而 cost 域当成没声明（只按一层计 κ）——
    现在 `check` 报 `E-Recursion-BadDepth`，运行期报 `E-Recursion-Uncheckable`。
  - 测试：`test_interp.py` 新增 `test_recursion_depth_runtime`（声明 8 跑 20 抓、
    8 层边界不误伤、声明 256 跑 75 通过、跑 300 抓、没声明撞宿主栈给诊断、
    `<= 0` 不可校验）；`test_kappa_gaps.py` 补一条「声明 8 跑 20 层必须抓」。
  文档：[语法清单](文档/语法清单.md) 的 `recursion depth` 一节改成「断言」口径。
- **格式化层的三条「`check` 绿、`run` 绿、结果错」**（2026-11-10）。都是**静默错值**，
  现在要么给对、要么报 `E-Format-*` 并列出支持集：
  - `print("{}", 3.14159265)` 打印 `3`：`fmt.apply` 对非 list 值一律 `int(v)`，
    浮点被**静默截断**。现在 `{}` 与 `print(x)` 逐字一致（浮点最短往返
    `3.14159265`；`bool` → `True`/`False`；枚举 / `Result` 按 `repr`）。
  - `{:08x}` 打印 `      ff`（6 个空格）：`0` 被当成宽度前导字符，**零填充静默退化成
    空格填充**。现在 `0` 是零填充标志：`{:08x}` → `000000ff`、`{:08d}` → `00000042`、
    `{:08}`（负数）→ `-0000042`。
  - `{:.6f}` 报 `E-Format-BadSpec`，文案却是「不支持浮点格式（`{:.2}`）——本语言还没有
    浮点类型」：**既编造了用户没写的 `{:.2}`，又说了假话（`f64` 明明有）**。现在
    `{:.Nf}`（N=0..100，默认 6 位）真的可用，`{:.6f}` → `3.141593`；文案回显用户
    真实写的 spec，并列出支持的完整写法。
  - 连带的**静默降级**一并堵死：`{:x}` 给负值（以前打 `-1`）/ 给浮点（以前打 `3`）、
    `{:s}` 给数字、`{:d}` 给 `bool`、`{:x}` 给文本（以前抛宿主 `NameError`）——
    全部 `E-Format-ArgType`；`{:b}`/`{:o}`/`{:e}`/`{:g}`/`{:^8}`/`{:+}`/`{:.2}` 等
    全部 `E-Format-BadSpec` 且文案带支持集（以前只有一句「不认识的说明符」）。
  - **错误路径自己就是坏的**：`stdlib._format`（和 `_write_chunk`）里 `raise InterpError(...)`
    但作用域没有这个名字，于是**任何运行期格式错误都变成宿主
    `NameError: name 'InterpError' is not defined`**。补上函数内局部导入。
  测试：`test_format.py` 新增 60 项断言（现共 73 项，含端到端 `print(v)` ≡ `print("{}", v)`
  与「文档 spec 表 = 实现输出」的机器对账）。
  文档：[内建函数](文档/内建函数.md) 新增「花括号格式串」一节（spec 全集 / 报错表 / 上限），
  `format` 的备注栏来自 `stdlib` 的 `empty=`，与 `src/fmt.py` 同一份口径。
- **「`check` 全绿、结果错」的 11 处**（2026-11-10）。都是**静默错值**或
  "`run` 才炸宿主 `TypeError`"，静态判据本来就在类型里：
  - 模块常量的表达式初值不求值：`const N: u64 = 10 * 2;` 以前 `run` 打印
    `None`；现在常量在装载时按**声明顺序**求值（字面量 / 前面声明的常量 /
    算术 / 位运算 / 比较 / 标量 `as`），求不出报 `E-Const-NotConstant`，
    `10 * 2` → `20`。
  - `Color::Green as u64` 原样返回枚举：现在序号 = 变体在 `enum` 声明里的
    位置（`Green` → 1；带载荷的变体也一样）。
  - `vector<u64>& r = ys;` 其实是拷贝：本语言**没有局部引用**，现在报
    `E-Ref-Local`（引用只在形参位置）。
  - `p.sum()` / `a + b` / `a < b` / `sort(vector<struct>)` /
    `format("{}", p)` / `p.first` / 函数存进 struct 字段再调用：以前
    `check` 绿、`run` 抛宿主异常；现在分别报 `E-Type-StructMethod` /
    `E-Type-StructOp` / `E-Type-StructOp` / `E-Type-StructOp` /
    `E-Format-ArgType` / `E-Type-UnknownField` / `E-Type-FuncValueField`，
    消息里写清"能怎么做"（字段相加 / `sort_by` + 具名比较器 / 逐个字段格式化 /
    元组只有 `.0`/`.1` / 函数只能直接调用或交给内建消费者）。
  - **标量 `T&` 形参写不回调用方**（`func bump(u64& x)` 里 `x = 42`，
    调用方仍是 7）：容器 / struct 的引用是真写回，标量是不可变值。实现
    引用单元要改掉读 / 写 / 下标 / 字段 / 内核参数等全部路径，与值语义的
    七个拷贝点冲突，**代价与风险不成比例**；因此与局部 `T&` 同一口径
    **静态拒绝**（`E-Ref-Scalar`），消息给三种写回写法。容器 / struct
    引用形参照旧可用。
  测试：`test_typecheck.py` 新增 16 项、`test_interp.py` 新增 3 项；
  文档：[语法清单](文档/语法清单.md) §3 / §4 / §`struct / 函数值 / 常量 / 引用`、
  [能力边界](文档/能力边界.md) §2.3。
- **`i += 1` / `i -= 2` 现在认作循环推进**（2026-11-10）。`bound.py` 的
  `progresses()` 只扫 `i = i + k` 这一种形状，于是 `while (i < n) { …; i += 1; }`
  被判 `E-Loop-CounterUnchanged`，而且文案说「计数器 `i` 在循环体里从未被修改」
  ——写的和说的正好相反。现在 `assign-op`（`+=` / `-=`）也算推进，文案改成
  「没有被推进」并列出认得的写法。**上界不放宽也不收紧**：`i += 2` 仍给 `n`
  （真圈数 `ceil(n/2) ≤ n`，偏松不偏小；要收紧得再证步长恒定、初值为 0）。
  反过来，方向不对或零步长的复合赋值（`while (i < n) { i -= 1; }`、`i += 0`）
  仍判「推不进」并退成 ⊤——否则会拿 `n` 当死循环的上界。测试：`test_bound`。
- **`do { } while` 报 `E-Keyword-Unsupported`，不再报「期望 ';'」**（2026-11-10）。
  以前 `do` 被当成类型名，报一句通用的 `E-Parse-Expected 期望 ';'，实际是 '{'`，
  读者只会以为自己少了分号。现在明确说「本语言没有 `do { } while`」，给出改法
  `while (true) { 体; if (not (cond)) { break; } }`，并提醒 `while (true)`
  推不出上界（`check` 会给 `E-While-NoBound`、`cost` 显示 `κ ≤ ⊤`）。测试：`test_parser`。
- **`xs.end()` 不再撞「`end` 已下线」的退役关键字诊断**（2026-11-10）。`end` 是
  v0.68 下线的关键字，`xs.end()` 里的 `end` 以前无条件走 `E-End-Removed`；
  `xs.rbegin()` 又换成一句「期望 'in'」——同一个缺口两套说法。现在按**成员访问
  位置**区分（判据：前一个 token 是 `.`）：`begin`/`end`/`rbegin`/`rend` 在成员
  位置统一报 `E-Call-UnknownMethod`「本语言没有迭代器」+ 三种替代写法；
  `end` **单独出现**（块收尾）仍报「已下线」。测试：`test_lexer` / `test_parser`。
- **`@include` 生成的文档块与源不一致**（2026-11-10）。`文档/语言介绍.md`、
  `文档/一屏对比.md` 的 include 还指向已移出版本库的 `文档/设计/目前设计/样例/`；
  源解析失败后旧块被原样保留，于是 `Vec<u64>`（早改名 `vector<u64>`）和
  `repeats log(hi - lo)`（已改为 `+ 1`）一直留在对外文档里。现在 include 改指
  `文档/样例/` 并重新注入；`sync_docs.py` 跳过 `_` 开头的临时目录（`_probe/`、
  `_incoming/`），且 `--check` 对「源缺失」也返回 1。该检查已进 `tools/gates.py`
  成为第 9 道门 `doc_sync`，文档再漂移会直接红。
- **`cost --explain` 的支配项不再把标量形参（`sym`）当常数**（2026-11-10）。
  `cost_explain.deg()` 以前只认 `size` / `log2` / `mul` 等形状，`Cost` 的 `sym`
  （`blur(u64 w, u64 h)` 里的 `h` / `w`、`matmul` 的 `dim`、`topk` 的 `k(f)`）
  落到 `return 0`，于是 `blur` 的支配项被指成**常数 `4`**，而杠杆同时报 99.9% 在
  `h×(…)` 上——「支配项 / 杠杆 / 元凶」三个数字互相打脸（不产生 `nan`，所以比
  `nan%` 更难发现）。现在 `deg(sym) = 1`（与尺寸量同阶）、`deg(log2(sym)) = 0`
  （与 `log2(size)` 同档）。只影响 `--explain` 的排序与文案，**不参与任何判定**；
  样例 17 个模块 29 个函数里 3 个的 explain 文案变化（`blur` / `matmul` / `topk`），
  `merge` 逐字节不变。测试：`test_explain.py` 新增 9 项断言。
- **`cost` 报告「按声明的 target 计」的信任项**（2026-11-10）。调用点修复
  （`2a189bb`）把「被调函数只有 spec / 递归没有 `recursion depth`」时只能信任其
  `target` 的事实登记在 `ci.target_trusted`，但 CLI 看不见：用户看到
  `κ ≤ 7n + 2`，不知道其中一段是**作者写的承诺**而不是推导值。现在人读输出在 κ
  下多一行「其中 1 项按**声明的 target** 计（`inner` 只有 spec、没有实现）——
  不是推导值」；`--json/--yaml` 在该函数的对象里加
  `"target_trusted": [{"callee": "inner", "reason": "no_impl"}]`，
  **只在非空时加键**。**只标注，不改任何判定**（`ok` / `debt` / `unknown`）。
  测试：`test_explain.py` 新增 4 项断言。
- **`**` 现在是真幂运算，不再静默算成乘法**（2026-11-10）。以前 `**` 被词法拆成两个
  `*`、右操作数被读成前缀 `*`（解引用，运行期恒等），于是 `a ** b` 等价于 `a * b`：
  `2 ** 10` 得 `20`、`3 ** 4` 得 `12`、`16.0 ** 0.5` 得 `8.0`——`check` 全绿、
  `run` 跑得动、**结果全错**（"不支持"至少看得见，这个看不见）。修法：`**` 成为独立
  token（`STAR_STAR`），**右结合**（`2 ** 3 ** 2` = `512`）、层次在 `* / %` 之上
  （`a * b ** c` = `a * (b ** c)`，一元前缀更紧所以 `-2 ** 2` = `4`）；
  `f64 ** f64 → f64`（`powf`，IEEE 语义：`(-8.0) ** 0.5` = `nan`、`2.0 ** -1` = `0.5`、
  `1e308 ** 2` = `inf`，**不抛异常**）；整数幂只收**非负的字面量常量**指数
  （`2 ** 10` = `1024`），运行期指数报 `E-Type-Pow-ExponentLiteral`、负指数报
  `E-Type-Pow-NegativeExponent`——不给"静默回绕"留通道；整数溢出与 `*` 同口径按位宽
  回绕（`2 ** 64` = `0`、`0 ** 0` = `1`）。**平方根通路**：`x ** 0.5`
  （`2 ** 0.5` = `1.4142135623730951`）。文档：[语法清单](文档/语法清单.md) §5.2、
  [能力边界](文档/能力边界.md) §2.2.1 / §4.1；测试：`test_lexer` / `test_parser` /
  `test_typecheck` / `test_interp` 各加一组 `**` 断言。
- **`fmt` 把 `i++` 排成 `i + +`**：词法没有 `++` / `--` token（是两个相邻的 `+` / `-`），formatter 按二元运算符补空格，产物不再是合法 Elench。现在按「源码相邻性」还原 `i++` / `++i` / `i--` / `--i`；有空白的一对（`i + +j`）保持两个一元符号。
- **`nullptr` 的成本从 0 改成 1**：此前每个 `nullptr` 在 κ 里免费，实测出现过 **κ=5 < ops=7**（上界不成立）。
- **`export --out=*.html` 两处可读性**：κ 分段主体相同时折叠（逐字符相同才折，原文放进 `<details>` 可复制）；「比值 ≈ 0.00」改成带方向的措辞（远低于 / 不高于 / **超过 → 红色块标「这是债务」**）。
- **退役内建的改写提示**改用 C++ range-for（不再提示已删除的 `in` 写法）。

### Added
- **`++i;` / `i++;` / `--i;` / `i--;` 作独立语句**（2026-11-10）。C++ 循环的默认
  肌肉记忆以前是纯解析错误（`E-Parse-ExpectedExpr 实际是 '+'`）。现在 parser 按
  **相邻性**识别（`i + + j` 有空格时不算），并 desugar 成 `i = i + 1` / `i = i - 1`
  ——**AST 与手写赋值同形**，解释器与成本域一行都不用改。只收**语句位置**：
  `x = i++` / `f(++i)` 报 `E-Unary-Unsupported`「只能作独立语句」，不静默变形成
  别的东西。测试：`test_parser`（AST 逐节点比对）。
- **`target` 里的「尺寸差」`hi - lo`**（2026-11-10）。递归分治最自然的上界就是
  「区间长度的平方 / 带 log」：`qsort(lo, hi)` 写 `O((hi - lo) * (hi - lo))`、
  归并写 `O((hi - lo) * log(hi - lo))`、二分写 `O(log(hi - lo))`——这些在修前
  **一个都写不出来**：`-` 只回 `max(a, b)`（把 `hi - lo` 当成 `hi`，**契约悄悄
  放宽**），带括号时更直接认不出（`_target_cost` 没有 `paren` 分支），整条
  target 报 `E-Target-BadForm`。现在的口径只有两条：能证明 `lo <= hi`
  （spec 的 `requires`，四个比较方向都归一成事实）→ `hi - lo` 是**尺寸量**，
  符号名就是 `hi - lo`（**不是** `n(hi - lo)`：差值不是容器），κ 侧的区间循环
  （`while (j < hi)` 且 `j` 从 `lo` 起只增 / `for (i in lo..hi)`）用**同一个
  尺寸量**；证明不了 → **退 ⊤ 并说清原因**（不当常数、不当正数猜）。
  `O((lo - hi) * n)` 这类方向反了的写法照旧被拒（诊断指出要证明的是
  `hi <= lo`）。`x - 0` 化简成 `x`。测试：`test_cost_orders.py` 新增
  `[8] 尺寸差 hi - lo` 一组 21 项断言。
- **`文档/能力边界.md`：公开「能写什么 / 写不了什么」的边界文档**。三类判据（设计如此 /
  还没做 / 契约表达不了），**每条都带实测报错原文或文档出处**，并给出「没有 X 时怎么写」的
  对照表（`sqrt` → 二分/牛顿、链表 → index 模拟、`throw` → `Result`…）。写作中复核出四条
  与直觉相反的实测：① **`**` 当时不是幂**（**已修**，见上面的 `### Fixed`，现在 `2 ** 10` =
  `1024`）——`16.0 ** 0.5` = `8.0`、`2 ** 10` = `20`（当时的 AST 是
  `binary '*'` + `deref`）；② **`goto done;` / `&x` / `u128` 会静默通过**（语句开头的标识符被
  当成未知类型声明，`UNKNOWN` 与任何类型兼容）；③ **字符字面量 `'a'` 其实早就可用**（`u8 c =
  'a'` → `97`，且有一整套 `E-CharLit-*`），没有的是 `char` 类型——而 `char` 是保留字；④
  **`O(n!)` 会让 `check` 崩**（`ValueError: restart 不许触碰 target`，待修）。
  同时记录 SHA-256 的解释器量级（1 KB `0.404 s` / 1 MB `564.9 s`，慢 `hashlib` 约 76 万 / 270 万倍）
  并复跑校验（1 KB `0.400 s`、1 MB `567 s`、digest 与 `hashlib` 逐字节一致）。
- **`W-Target-Loose` 告警：暴露「用户被迫写松的 target」**（`cost_explain.py::
  target_loose_report`；`cost` 里每个函数一行、`check` 里一条 warning）。
  判据比的是「κ 的阶 ≤ target 的阶」，于是 κ 一旦比真实松，**写真实的紧界反被判
  债务**。实测 `calculator.evaluate` 的 κ = `n×1447 + 27790173`：27,790,173 那个
  常数项在 n=1024 处占 **94.9%**，`κ÷ops` 因此 39 万倍，而 `compare` 只比阶、
  常数项永远推不成债务——这条 target 的「随 n 增长」其实是被常数满足的。
  告警只报**能静态证**的那一半（`(a)` 常数项松）；`(b)`「κ 的阶真的高于真实阶」
  静态证不了，由 `tools/tightness_compare.py` 新增的「界松分类」节按**比值随 n
  是否发散**区分。**不改任何判定**：`ok`/`debt`/`unknown` 与
  `tightness_compare` 的 144 行判定列前后逐字相同。
- **`repeats` 变成运行期可校验的断言**（`interp.py::_repeats_limit`、
  `checker.py::_check_repeats`）。以前 `repeats` 是**纯声明**：`check` 不看、
  `run` 不看，只有 `cost` 读——于是 `spec pending(...) { repeats 4; }` 配一个
  跑 999 圈的循环，`check` 全绿、`cost` 判「κ ≤ 199 达成」、`run` 照跑
  （实测 `_tier_a/union_find/probe_repeats`）。可它是本语言**唯一**能表达
  亚线性圈数（`log n` / `α(n)`）的通道，也就成了**唯一靠一句假话就能让契约
  变绿**的入口：想说真话，反而必须写假命题。现在：
  · **运行期**：以「带 repeats 的谓词」为条件的 `while`，进入时按运行期实际
    尺寸求一次上界（常数直接比；`log(hi - lo)` / `operators.length()` 按真实值，
    谓词形参按本次实参绑定），圈数逐圈累加，**超过上界立刻**抛
    `E-Repeats-Exceeded`（例：`repeats 4` 真跑了 5 圈）；上界求不出（未定义
    名字 / 认不出的形状）抛 `E-Repeats-Uncheckable`，**不静默跳过**。
    **严格上界：`repeats log(hi - lo) + 1`**（2026-11-10）。上一版对含 log2 的界
    **额外放宽 1 圈**，那是绕开样例 off-by-one 的权宜（二分的真圈数是
    `⌊log2 m⌋ + 1`：m=1 转 1 圈而 `log2(1)=0`；m=128 转 8 圈而
    `log2(128)=7`），不是解决。现在 `bound.expr_to_bound(allow_add=True)`
    **只对 repeats 这个入口**放开「绑定项 + 整数字面量」一种形状：
    `log(hi - lo) + 1` 收；嵌套 `+`、`log(k + 1)` 不收；`while` 比较那条入口
    仍拒收 `+`（否则 `while (a * b < 10)` 会被误当计数器比较）。
    样例 `search.tu` 改成 `repeats log(hi - lo) + 1`（target 一字未动），
    **运行期「log 型 +1 圈」那条特例删除**：常数 / 尺寸 / 对数上界同一把尺，
    `repeats 4` 跑 5 圈、`repeats log2(8)` 跑 4 圈（真值 3）照样抓；
    `search` 的二分在 m=1/2/128 上真圈数都 ≤ 声明的上界。
    κ 只在常数项上变化（`search.binary_search`：`log2(n(xs))×39` →
    `(log2(n(xs)) + 1)×39`），判阶与 `ok`/`debt`/`unknown` 一个都没变；
    `golden/kappa.json` 只此一项重新冻结。
  · **总是开**，没有开关：和 `guarantees` 同一条哲学（可关掉的断言等于默认
    关掉的断言）。开销是每圈一次自增 + 比较；**不推进** `_tick` / ops 计数，
    所以 `measure` 的 ops 基线不受影响。
  · **check**：repeats 引用未定义的名字 → `E-Repeats-UndefinedName`（以前
    静默通过，`cost` 只会给 ⊤）；repeats 落在不被任何 `while` 当条件的函数上
    → `W-Repeats-Unused`（这条上界永远不生效）。
  · **渲染**：`repeats log2(k)` 里的裸名字是**圈数（标量）**，κ 里写 `log2(k)`，
    不再写成 `n(k)`（容器尺寸的写法，求值侧按 1 算 ⇒ κ 不再是上界）。
  · 新增回归：`test_interp.test_repeats_runtime`（假声明被抓 / 真声明不误报 /
    带尺寸的 `repeats log2(k)` / **严格上界 `log2(k) + 1`** / `repeats 4` 跑 5 圈、
    `repeats log2(8)` 跑 4 圈、真样例 m=1/2/128）、
    `test_checker.test_repeats_contract`、`test_bound.test_repeats_render`、
    `test_bound.test_repeats_plus_const`（放开范围只有「绑定项 + 整数字面量」）。
- **成本式的 loglog：`O(n log log n)` 能写、能判**（`cost_domain/symbolic.py` 的
  `Cost.loglog`、`cost_domain/cost_check.py::_profile` 的三元组）。
  以前 `log(log(n))` **能 parse**，但 `_profile` 把任何 log 都记成 `log n`，
  于是 `target { time: { max: O(n * log(log(n))) } }` 被悄悄读成
  `n log n`——`n log n` 的实现对它判**达成**（承诺被放宽一档）。现在：
  · `loglog` **不新增节点**：就是两层 `log2`（`Cost.loglog(n)`），
    打印 `log2(log2(n(xs)))`、求值递归进参数；不新增 `kind`，`to_dict` 不变；
  · 判阶从二元组改成**三元组** `(次数, log, loglog)`，字典序比较：
    `loglog n → (0,0,1)`、`log n → (0,1,0)`、`n → (1,0,0)`；
    `mul` 三分量相加、`add`/`max`/`par` 取字典序最大的支配项；
  · `log(n log n) = log n + loglog n` 归约成 `(0,1,0)`（**仍**是 `log n`，
    低阶项不改变阶）；三层 `log`（`log(loglog n)`）**不折算**，判阶 → `None`；
  · 除法让 loglog 因子也可以为负：`n / loglog n → (1,0,-1)`；
  · `_profile` 内部变了、`render` 没变：既有 κ 的打印逐字相同，
    `golden/kappa.json` 不动；`par.order_of` 随公开口径变成三元组，
    `order_json` 的 loglog 分量**只在非 0 时出现**（旧 JSON 消费者不受影响）。
  文档：`文档/语法清单.md` 新一节「loglog：`n log log n`」。
- **成本式的除法：`O(n / log n)` 能写、能判**（`cost_domain/symbolic.py` 的
  `Cost.div`、`cost_domain/cost_check.py::_profile`）。数论的核心量级
  `π(x) ≈ x / log x` 以前**写不出来**（`_target_cost` 的 binary 分支只认
  `+ - *`，`/` 直接判"认不出"，契约整个不生效）。现在：
  · 新节点 `div(a, b)`，打印 `n(xs) / log2(n(xs))`，可求值（**分母 0 → `inf`，不崩**）；
  · 判阶 `(次数, log 因子数)` 里的 **log 因子现在可以为负**：`n / log n → (1, -1)`、
    `n / log²n → (1, -2)`；比较仍是字典序（同次数下 `-2 < -1 < 0 < 1`），
    于是 `n/log²n ≺ n/log n ≺ n ≺ n log n` 自动成立，**不靠采样斜率**；
  · 只认三种分母：log 的幂（`l − k`）、正常数（量级不变）、其余（**尺寸量 `n / m`、
    `⊤`、常数 ≤ 0 → 一律 ⊤，不猜**）；
  · 化简：`a/1 = a`、`num/num` 折常数（**`3/2` 是 1.5，不被整数截断**）、
    `a/a = 1`、`(x×b)/b = x`、`(a/b)×b = a`、`⊤/x = x/⊤ = ⊤`；
  · `checker._target_cost_shape_ok` 的二元运算收成白名单 `+ - * /`
    （以前对任何 binary 都放行，`%` 会"形状通过、语义拒绝"两套口径）。
  · **顺带补两个洞**：① `κ 是常数 ⇒ 任何 target 都满足` 的前提是 target 的阶
    ≥ 0，而 `O(n / m)` / `O(1 / n)` 的阶是负的——这类 target 现在判 **unknown**；
    ② 分母是尺寸量的 target **整场比较**都不走"同规模假设"的数值拟合
    （`m ≈ n` 与 `m ≈ n²` 会给出相反的判决），直接 ⊤。
  文档：`文档/语法清单.md` 新一节「除法：`n / log n` 与负 log 因子」。
- **有理次幂的成本阶**（`cost_domain/symbolic.py` 的 `Cost.pow` / `.sqrt`）：
  成本式新增 `pow(base, p/q)` 节点（`sqrt(n)` = `n^(1/2)`），可打印
  （`sqrt(n)` / `n^(2/3)` / `n^(3/2)`）、可求值、参与乘法化简
  （`sqrt(n)×sqrt(n) = n`、`sqrt(n)×n = n^(3/2)`）；`n^0` 化简成 `1`。
  **判阶次数从整数改成 `Fraction`**（`cost_domain/cost_check.py::_profile`），
  `√n ≺ n ≺ n^(3/2) ≺ n²` 这类比较直接由结构判据给出，不再依赖采样斜率。
  `.tu` 的 target 里写 `sqrt(x)` / `root(x, q)` / `pow(x, p/q)` 即可
  （`^` 不是本语言的 token）；指数只收字面量比，负指数与家族外形（`sqrt(log n)`、
  `sqrt(n log n)`）一律判不了 → `⊤`/数值兜底，**不猜**。
  文档：`文档/语法清单.md` 新一节「分数次幂」。
- **`parallel` 的「最大核计账」**（`cost_domain/par.py` + `Cost.par`）：κ 不再把
  `parallel` 压平成一个和，而是保留 `par(κ₁, …, κₙ)` ——
  判阶**直接**取 `max_i 阶(κ_i)`（B-24）、总量仍是 `Σ_i κ_i`（`allocation` 同口径），
  数值/阶与改前**逐条相同**。`cost` 因此能打印逐任务的「最大核视图」：
  每个任务的 κ 与阶、**瓶颈任务**（同阶取规模化值最大者）、判阶 κ、总量 Σ。
  `--json` 多一个 `parallel` 段（`order: maximum` / `value: sum` 写在里面）。
- **大小核墙钟估计**：`cost --cores=<机器档.json> [--at=N]` 输出 `wall_est`。
  机器档是 `{"cores": [{"kind":"P","weight":1.0}, …]}`，`weight` 是速度因子
  （只有比值有意义）。核数 < 任务数时按 **LPT**（重任务先、放到预计完成最早的核上）
  估计，任务不拆。**核数与权重 MUST NOT 进 κ**（κ 要能跨机器冻结，B-27），
  所以 `wall_est` 明确标成「估计，不是契约」。新文档：`文档/parallel-计账.md`。
- **文档：`ops`（实测操作数）的口径**（`文档/语法清单.md` 新一节）。`13e730e` 起
  `ops` 含**拷贝**，但没人在文档里写清楚「实测」数的是什么。新节逐条写明：
  哪些动作各记 1（表达式节点 / 语句 / 内建分派 / 容器方法 / 拷贝，分配只进 `allocs`）、
  拷贝的系数（`vector`/`Text`/`stack`/`queue` = `n+1` 与 1 次分配；`map`/`set`/
  `unordered_*` = `n` 与 `n` 次；`array<T,N>` = `N` 与 0；`struct` = 字段数；
  标量/`enum`/`Ok`/`Err` = 0）、七个拷贝点与各自「不拷」的情形、
  与 C++17 值类别的对应表，以及**剩余偏差**（内建回调入口的按值形参仍别名、
  `match` 载荷绑定不拷、`call` 当初始化源时两侧值类别判定不完全一致）。
  同时修正了 `语法清单.md` 里「`return p;`（返回借用）还没修」这段**已过时**的说法。
- 静态检查补四条"假绿灯"（check 过、run 才炸）：
  * `E-Call-UnknownMethod`：容器调不存在的方法（`v.size()`），报错里列出**该容器支持的方法**；
  * `E-Index-OutOfRange`：定长容器的**常量下标越界**（`array<u64,4> a; a[9]`）；
  * `E-Type-NoSuchMethod`：`Point::new(3, 4)` —— 结构体没有 `new`，以前静默造出 EnumVal；
  * `E-Target-BadForm`：target 里认不出的成本式（`O(n(xs))`），以前 check 说没事、cost 说没 target。
- `map` 支持下标读写与遍历：`m[k]` / `m[k] = v` / `for (k in m)`（遍历**键**）。
- **大括号初始化**：`vector<u64> xs = {1, 2, 3};`、`array<u64, 4> ys = {10, 20, 30, 40};`、
  空 `{}`。只在**声明初始化位置**识别，块语句的 `{` 不受影响；元素按书写顺序求值。
- **只声明不初始化**：`vector<u64> xs;`（空）、`array<u64, 4> dp;`（N 个零）、
  `u64 i;`（0）——C++ 里最普通的写法以前解析不了。
- 检查期诊断 `E-Array-InitLen`：`array`/`FixedVec` 的大括号元素个数 MUST 等于容量。

- 检查期 `E-Init-ElemType`：大括号初始化的**每个元素**与声明的 `T` 比类型
  （与普通赋值同一套 `compatible`：整数互通、整数↔浮点互通、`bool` 与整数不互通）。
- 容量支持**常量表达式**：`array<u64, N>` / `array<u64, N * 2>`（`N` 来自 `.tu` 的 `const`）；
  求不出时给 `W-Array-InitLen-Unknown` 警告，不再静默跳过。
- 检查期诊断 `E-Type-UnimplementedContainer`：`deque<T>` 在 `check` 阶段直接拒绝
  （消息里列出已实现的 8 个容器）。`stdlib` 里只有 `deque_new` / `deque_push_*`
  内建，容器构造/方法没有接线——以前 `deque<u64> d; d.push(1);` check 通过、run 才炸。
- **`sizes` 子句真的生效了**（规格 §7.2 G1-b）。以前它**能解析、但没有任何消费者**：
  `sizes n = xs.length();` 之后 target 里的 `n` 只是一个自由尺寸量，判定退化成 ⊤——
  **契约看着更精确，其实一个字没生效**。现在：`sizes name = 形状量` 并入 target 的
  成本式（`sizes table = n + 1;` 的 `O(table)` 展开成 `O(n + 1)`）；右侧只收
  形参 / 容器形参的 `.length()` / 整数常量 / `+ * min max`（**名字是 spec 侧的缩写，
  不引用实现里的局部**）；新增 `E-Sizes-NotShape` / `E-Sizes-ParamShadow` /
  `E-Sizes-Dup` / `E-Sizes-Position` / `W-Sizes-Unused`。`文档/语法清单.md` 补了
  「`sizes`」一节。

### Changed
- **`piece`（分档承诺）能判阶了**（`abeb01f` 的回归）。`_profile` 逐段归约，
  取各段阶的**字典序最大者**（与 `par` 同一口径；越界回退也进 max），
  **任一段判不了 → 整段 unknown**（跳过未知段可能低估）。`_profile_gap`
  **不再**把 `piece` 归为「没实现」：原因报到**那一段**（家族外 / 陌生节点 / ⊤）。
  恢复 3 个门面样例的判定：`count.count`、`sum.average`、`例子.gap.top`
  从 unknown 回到**达成**（与 `abeb01f` 之前相同）；`search.binary_search`
  判**债务**——分档表把 n≤127 交给线性候选，逐段比就是 `O(n) > O(log n)`
  （`tests/cases/test_cost.py` 与 `test_dogfood` 的 `KNOWN_DEBT` 本来就把
  它写成**样例里有意的债务**）。三层 log 仍是 unknown。`golden` 22/22
  逐字不变（golden 走无表 max 口径，κ 表达式未动）。
- **`calculator.evaluate` 的 κ 从 O(n²) 回到 O(n)**（`dc085a5` 留下的已知债务）。
  `tokenize` 的 `else if (is_digit)` 分支把 `read_number` 的循环算进了每轮成本；
  内层循环的真实圈数由 `digits >= MAX_DIGITS` 卡在 `MAX_DIGITS + 1 = 20`。
  修法：把 `read_number` 的循环条件 `index < total and is_digit(...)` 包成
  **单一谓词** `more_digits`，在它的 spec 上写 `repeats 20;`。
  （只给 `is_digit` 写 repeats 不生效：`and` 组合的界在成本域里取较松的一侧，
  即 `_min_cost` → `Cost.max`，结果仍是 `n`。）
  `calculator.tu` 因此多 1 个 `spec`、`.eln` 多 1 个 `fn`（`more_digits`），
  `target` **一个字没动**。`test_dogfood` 的 `KNOWN_DEBT` 删除 `calculator`，
  并新增 `test_calculator_linear`（尺寸指数 = 1、不再是债务）。golden 重冻：
  3 个 κ 变化（`evaluate` / `tokenize` / `read_number`）+ 1 个新增
  （`more_digits`），其余 18 个逐字节不变。
- `map<K,V>` / `set<T>` 的值实现由 Python `dict` / `set` 改成 **AVL 平衡二叉搜索树**
  （`OrderedTree`）：查找/插入/删除**最坏 O(log n)**，与成本域的 `log2(n)` 一致；
  键/元素只要求**全序（`<`）**，不再要求可哈希。`map<Vec<u8>, u64>` /
  `set<Vec<u8>>` 现在可用（字符串字面量按键的字典序），`keys()` / `values()`
  仍是键序 / 元素序。`unordered_map` / `unordered_set` 保持哈希实现（承诺最坏 O(n)）。
- `vector<T>::filled(v, n)`（n > 0）现在**记 1 次分配**（真实 C++ 会分配一块堆内存）；
  `array<T,N>::filled(v)` 仍记 0。全量样例重跑无契约翻转。
- 成本域：大括号初始化按元素逐个计价（n 个元素 ≈ 2n 次操作，κ 仍是上界）；
  无初值的定长数组按容量计 N 次（默认构造要写满 N 个零）。
- **成本域：值语义的拷贝成本**（把规格 T7「值语义默认」/§5.4.1 落到 κ）。按 C++17 的**值类别**记账：
  * **prvalue 强制省略**（`{…}` / `S{…}` / `T::make()` / 返回新容器的调用）→ 0；
  * `return` **非 `const` 局部 / 按值形参** → **隐式移动**：`vector`/`map`/`set` O(1)、
    `array<T,N>` N、`struct` 成员数，**0 分配**；
  * 其余消费（**按值传参** / 拷贝初始化 / 拷贝赋值 / 入容器 / `return` 借用值 / `for` 元素）
    → **拷贝**：`vector` `n+1` 与 1 次分配、`map`/`set` **`n` 与 n 次节点分配**
    （结构克隆是线性的；**若实现改成逐插重建，这里必须改记 `n log n`**）、
    `array<T,N>` `N` 与 0 次分配、`struct` 成员数；
  * `?!` 候选按 **kernels 头**的签名判定（不再借用宿主的返回类型）。
  样例 `target` **一个没改**；κ 变化的只有 4 个：`calculator.evaluate` / `calculator.tokenize`
  （`array<Token,4096>` 返回按移动 = 4096）、`merge.earlier`（2 个 `Interval` 按值形参 × 2 成员）、
  `merge.merge`（`n log n × 6 → × 10`）；**契约判决 0 翻转**（全仓 125 个函数重扫）。
  已知的保守/低估点写在 `src/cost_domain/cost_interp.py` 的「值语义」一节。
- **解释器改成值语义拷贝**（与上面那条成本口径对齐；以前解释器是**彻底的别名**，
  两条口径描述的是两个不同的程序，κ ÷ 实测 因此不可比）。**七个拷贝位置**全部落地，
  且只在该表达式是**左值**时拷（运行期按 AST 节点种类判：`path` / `index` / `deref`
  / `field` 是左值，`call` / 字面量 / `{…}` / `S{…}` 是 prvalue；
  `at` / `get` / `front`… 返回容器内部元素，也是左值）：
  * `T a = b;`（b 左值）→ 拷贝构造；b 是 prvalue → 不拷贝（C++17 强制省略）；
  * `a = b;`（赋值，b 左值）→ 拷贝赋值（第 2 步补上；`x += 1` 不拷）；
  * 按值形参 `f(T v)`（实参左值）→ 拷贝；实参 prvalue → 不拷贝（移动）；
  * `return <借用值>`（`const&` / `&` 形参、元素、字段、const 局部、全局）
    → **拷贝**；`return 非 const 局部 / 按值形参` 仍是**移动**、`-> T&` 仍是别名；
  * 入容器 `push` / `insert` / `vec_push` / `map_put` / `hmap_put` / `set_add` /
    `deque_push_*`，以及 `m[k] = v` 下标写（左值实参 → 拷贝；`map` 的键和值都拷）；
  * `for (x in xs)` 的元素绑定 → 每轮一次拷贝（元素是标量 → 0）；
  * `S{a}` 聚合初始化的成员（成员是容器且值是左值）→ 拷贝。
  **不拷贝**的三条同样是 C++ 语义，没有改：`return 局部变量`（移动）、
  `return T{…}`（省略）、`T&` / `const T&` 形参（别名）。
  拷贝是**深**拷贝（`Text` / `FixedList`(保留容量) / `MapVal`(AVL **结构克隆** O(n)) /
  `SetVal` / `Stack` / `Queue` / `Struct` / 嵌套容器 / `set_new` 那一侧的内建容器）；
  标量原样返回。`_alias/matrix.py` 的 80 个探针里，**24 个从「别名」变「拷贝」**
  （`return_elem` / `assign_stmt` / `for_elem` 各 8），剩 8 个 ALIAS 全是 `T&`、
  8 个 ERROR 是 `const&` 的直接写被拦。
- **拷贝计入 `ops` 与 `allocs`**（口径**逐项抄** `cost_interp._copy_cost`，不另定）：
  一次 `vector`/`Text`/`stack`/`queue` 拷贝记 `n + 1` ops、1 次分配；
  `map`/`set`/`unordered_*` 记 `n` ops、`n` 次分配；`array<T,N>` 记 `N` ops、0 分配；
  `struct` 记字段数、0 分配；**标量 / enum 记 0**（κ 的登记口径）。
  `test_ops.py::test_copy_cost` 用「同一程序只差一行拷贝」逐格钉住系数。

### Fixed
- **`interp.py` 文件头的「已知的口径差」已过时：五条都早已对齐**
  （`interp.py` 的「操作数计数器」一节）。旧注释说 `paren` κ 记 0、
  `min`/`max`/`Ok`/`Err` 不计实参、`print` κ 少 1、`expr-stmt` 多 1；
  2026-11-10 逐条实测 **κ == ops**：`(((x)))` 5/5、`min(a + b, b)` 7/7、
  `Ok(x + 1)` 5/5、`Err(E::Bad)` 3/3、`print(x)` 6/6、`print("{}", x)` 7/7、
  `x * 2; x + 1;` 10/10。注释按实测重写，并记下唯一剩下的一处差：
  `x as u64` 的类型子节点在 κ 里记 1 而解释器不进入它（κ 大 1，方向安全）。
  新增回归：`test_kappa_gaps.test_leaf_misc`（5 条）。
- **互递归的 κ=⊤ 原因被写成「树递归」：措辞错、判定没错**
  （`cost_domain/cost_interp.py::_note_tree_rec`）。互递归 `a↔b` 的
  `per_path == 0`——`ping` 的函数体里根本没有自调用点，自调用点在环的**另一侧**；
  以前两种情况共用同一句「一条执行路径上有多个自调用点」，读者会去 `ping`
  里找一个不存在的多调用点。现在：`per_path >= 2` / 自调用在循环里 → 仍是
  「树递归」（文案不变）；`per_path == 0` → 「`ping` 与 `pong` 互递归：自调用点
  在环上（按名字在 `ping` 的函数体里数不到），规模推不出 ⇒ 退 ⊤，不猜」。
  **判定不变**（两种都 ⊤），`check` 的 `W-Exp-Risk` 仍只报真树递归。
  新增回归：`test_recursion_bound.test_mutual`（4 条）。
- **步数预算耗尽被一律说成「疑似死循环」：指数回溯的人会去改错地方**
  （`interp.py::_budget_message` + `dsh.py::_tree_rec_budget_advisor`）。
  实测：`'a?'×11 + 31 个 'a'` 的回溯探针在 ~6.3 s 被 500 万步挡住，旧文案只有
  「执行超过 5000000 步……疑似死循环（检查循环变量是否在推进）」——**算法本身
  指数爆炸**，循环变量没有任何问题。现在按证据分四类：
  · ① u64 下溢（条件里的无符号减法 ≥ 2^63）——沿用 `_diag_eval` 的既有判定；
  · ② **算法本身复杂度**：**复用** `cost` 侧的树递归判定——`CostInterp` 的
    `_self_call_shape` / `_note_tree_rec` 已经记下「κ=⊤ 的原因是树递归」，
    `run` 挂一个**惰性**顾问，只在真的超预算时调用一次 `cost_of` 读
    `top_reasons`（不另写递归形状分析，正常路径不付成本分析代价）；
  · ③ **输入规模/常数太大**：`while (i < n)` 有具体数值上界、超限时计数器
    确实比进入时大（重新用 `_diag_eval` 求值，不推进计数器）；
  · ④ 都没有：仍说「疑似死循环」，但**必须**补一句「也可能是算法本身的
    复杂度/输入规模，不一定是死循环」。
  每一类都给 `ELENCH_STEP_BUDGET` 的当前值。新增回归：
  `test_interp.py::test_budget_messages`（5 条）。
- **`build` 放行了 `check` 的 error 与 κ=⊤，并且 `✓ 构建通过` 与 `✗ 拒绝构建`
  同时出现**（`dsh.py::cmd_build` / `_gate_checks` / `_check_opts`）。
  实测三个洞：
  · `check --strict` 报 `E-While-NoBound`（error），`build --strict` 仍 exit 0
    ——因为 `build` 不认 `--strict`、`_gate_checks` 恒用 `strict=False`；
  · κ=⊤（树递归）时 `build` 一声不吭地 exit 0（没有警告，读者以为判过了）；
  · `build --deny-debt` / `--deny-unknown` 先打 `✓ 构建通过` 再打 `✗ 拒绝构建`。
  现在：
  · `_check_opts` 一处解析 `--strict` / `--checks=overflow:…` / `--relax=…`，
    `check` / `run` / `build` 同口径（check 的 **error** 一律让 build exit 1；
    warning 不拦，和 check 一致）；
  · κ=⊤ 默认**放行但警告**；`--deny-unknown` 拒绝，`--deny-debt` 也拒绝
    （沿用 2026-11-06 的全函数门禁）；`--deny-unknown` **不**拒债务；
  · 先算完 debt/unknown 再打结论，`✓` / `✗` **只出现一条**。
  新增回归：`test_build.py::test_gate_rules`（14 条）、`test_gate_dimensions` /
  `test_gate_total` 全绿。
- **移位的类型会抬宽结果的位宽：同一条公式因形参类型不同而结果不同**
  （`interp.py::_shift` + `typecheck.py::_shift_result_ty`）。
  `u32 x = 1; u64 n = 0; print(x << (32 - n));` 打 **4294967296**（按 64 位算），
  把 `n` 换成 `u32 m` 则打 **0**——同一个 `x`、同一个移位值 32，只差移位量的
  类型，差 2^32 倍。根因：位运算一律按「两边最宽的无符号位宽」定结果位宽，
  那是 `+ - *` 的**算术**规则，用在移位上是错的。
  · **规则（C / Rust）**：**移位的结果位宽只看左操作数**（整型提升之后），
    移位量只提供值、不参与类型决定。`u32 << u64` 是 `u32`、`u64 << u32` 是
    `u64`、`i64 << n` 是 `i64`。`& | ^` **不变**，仍按「两边最宽」。
  · **移位量按真实数值判断**：`u32 x; u64 k = 2^32; x << k` 必须是 0
    （`k ≥ 位宽`），不能把 `k` 按 32 位回绕成 0 再当「移 0 位」——与
    「`u8` 当索引」同一类陷阱（判断大小用真值）。
  · **为什么重要**：SHA-256 的 `rotr(x,n) = (x >> n) | (x << (32 - n))` 里
    `32 - n` 的类型取决于 `n` 的形参类型，写成 `u64 n` 就直接算错；
    `_tier_b/sha256` 注释里「所有移位量写成 `u32`，若写成 `u64` 会算错」
    就是这条老 bug 的规避。现在 `u32` 形参与 `u64` 形参结果相同。
  · 新增回归：`test_interp_surface.py::test_shift_result_width`（10 条）、
    `test_typecheck.py::test_bitwise`（6 条静态类型）。`u8 200 + 100 = 44`、
    `u8 a=200; a << 1 = 144` 与 u64 无符号语义不变。
- **叶子函数的 κ 小于实测 ops：`paren` 节点在 κ 里免费**（`cost_domain/cost_interp.py::_k_expr_paren`）。
  `ops ≤ κ` 是核心不变量，而 `_k_expr_paren` 只返回**里面**的成本、括号本身记 0；
  解释器 `_eval` 却对**每个非 None 节点**记 1（含 `paren` 包裹，见
  `test_units.py` 的单位定义）——于是**每层括号让 κ 少 1**。大函数里被常数余量
  盖住，**只有把叶子单独测才看得见**（SHA-256 移植时 `stats --ops` 暴露）：
  `rotr` κ=10 < ops=13、`ch` 9<11、`maj` 12<15、`big_sigma0` 42<51、
  `small_sigma0` 32<39；调用方按被调函数的 κ 记账，缺口沿调用链传下去
  （`return rotr(x,2);` κ=14 < ops=17）。差额**恰好等于括号节点数**
  （rotr 3、ch 2、maj 3、big_sigma0 3×3、small_sigma0 2×3+1），
  用 `(x)` / `((x))` / `(((x)))` 单加减验证：κ 2/2/2 → **3/4/5**，与 ops 逐个相等。
  修法：**给括号节点补记 `OP_COST`（κ 只增不减）**。没有改解释器计数器——
  它数的就是 AST 节点，改它等于重新定义单位，且会让所有历史 ops 一起变。
  · `golden/kappa.json` 重新冻结：**11 / 22 条 κ 变了**（calculator 7、search 1、
    sum 1、stats 1、lcs 1），全部变大；`paren` / `topk` 等无括号节点的不变。
  · `kappa_vs_ops --strict`：破界 **0**（64/256/1024 三档），`tightness_compare`
    比值只升不降（最大 `_diff.fast_pow.fpow` +18.2%、`样例.lcs` +6.3%、
    `_diff.binary_search` +6.2%）。**达成 → 债务：0 个**。
  · 新增回归：`test_kappa_gaps.py::test_leaf_parens`（52 条断言）。
  · 顺带核实：`min`/`max`、`Ok`/`Err`、`print`、`expr-stmt`、一元/二元、
    下标/方法调用在修前就已是「κ = ops」，**不是**本条的范围；`as` 的类型子节点
    在 κ 里多记 1（κ ≥ ops，安全方向），**不**为了对齐把它调小。
- **十六进制字面量：`check` 绿、`run` 崩**（`parser.py::parse_prefix` + `lexer.py`）。
  根因是**值在两层之间蒸发了**：词法器一直认得 `0x10` 并算好了 `Token.value=16`，
  但 parser 把 `token.text` 原样存进 `lit-int`，解释器又 `int(text)` 按**十进制**
  重解析一次——于是 `u64 x = 0x10;` 静态检查全过、运行期抛宿主
  `ValueError: invalid literal for int() with base 10: '0x10'`（SHA-256 移植时
  K 表 64 个常量只能手写成十进制，就是这条逼的）。现在 `lit-int.value` 存**数值**：
  · 支持 `0x`/`0X` 十六进制、`0b`/`0B` 二进制、数字间的 `_` 分隔
    （`0xFFFF_FFFF_FFFF_FFFF`、`1_000_000`）；
  · `0 .. 2^64-1` 全部可用，`0xFFFFFFFFFFFFFFFF` → `18446744073709551615`，
    装不进 `i64` 的值仍按既有 `_widen_oob_literals` 规则以 `u64` 参与运算；
  · **超出 `u64` 的字面量在词法层报 `E-Lex-IntOverflow`**（三种进制同一条规则），
    **不静默按 2^64 回绕**：`0x1_0000_0000_0000_0000` 多半是打错一位，
    静默变 `0` 会让 K 表悄悄算错；`0x` / `0b` 后没有数字 → `E-Lex-BadNumber`；
  · 未引入 `0o` 八进制（没有使用场景；`0o17` 会词法成 `0` + `o17` 并由解析错误指出）。
- **`vector<u8>` 的元素直接当索引 → 误判越界**（`interp.py::_index`）。
  `u8 idx = text.at(0);`（`idx = 99`）再 `t.at(idx)`，容器长度 256 却报
  `E-Index-OutOfRange：下标 99 越界（容器长度 256）`。根因是越界判断用了
  `UInt` 的**按位宽回绕比较**：`UInt.__lt__/__le__/__ge__` 把两边都按「最宽
  无符号操作数」的位宽取模（`_u_bits`），于是 `u8 99 >= 256` 算成
  `99 % 256 >= 256 % 256` → `99 >= 0` → True（报告里的 `256 % 256 = 0`）。
  那套回绕是**无符号算术/比较的正确语义**（`u8 a = 200; a + 100 → 44` 必需），
  但**不是"下标是否越界"的语义**；先存进 `u64` 变量之所以"正常"，只是因为
  位宽变成 64 后 `256 % 2^64` 刚好还是 256，属于碰巧。现在 `_index` 先
  `int(i)` 取**真实数值**再做 `0 <= iv < len` 判断；`u8` 算术回绕、负索引
  `E-Index-Negative`、真越界 `E-Index-OutOfRange` 全部不变。
  新增 `tests/cases/test_interp_surface.py::test_int_literal_radix_run` /
  `test_u8_element_as_index`（18 条断言）、`test_lexer.py` 8 条、
  `test_parser.py::test_int_literals`（7 条）。
- **多尺寸量的比较是假的：`O(n+m)` 与 `O(n·m)` 分不出来，错契约被判「达成」**
  （`cost_domain/cost_check.py::compare` / `_ratios`）。多尺寸时跳过结构判据、
  退回数值拟合，而 `_ratios` 只把字母序第一个尺寸量设成 n、**其余走
  `env.get(..., 1.0)` 当成 1**——`κ = n×m` 对 `target = n+m` 的比值因此恒定，
  **二次实现直接过线性契约**：实测 KMP 的二次 κ 对 `O(n(text)+n(pattern))` 判
  「✓ 达成」，同一个实现换成乘积 target 也「✓」，连暴力实现也「✓」；
  `κ = n + |xs|²` 对 `O(n×|xs|)` 同样「✓」。现在：
  · 多尺寸量**逐项支配**才判「达成」：κ 与 target 都展开成**单项式指数向量
    集合**，κ 的每个单项式都要被 target 的某个单项式**逐分量**压住
    （`n×m` 不被 `n+m` 压住 → 不判）；
  · 支配不成立、或展不开（log / 分段 / 非常数分母 / ⊤ / `k(f)`）→ **`unknown`**，
    理由写「两个尺寸量的关系未知（`n` 与 `m` 谁增长快不确定）」；
  · `_ratios` 把**所有**尺寸量都取同一个 n，输出标注「按同规模假设：
    所有尺寸量 = n；比值仅供参考，不作判定依据」——**数值比值不再单独
    决定「达成」**（`SLOPE_TOL` 拟合同理，只作兜底参考）。
  单尺寸量的判定**逐条不变**（`tests/golden/kappa.json` 22/22、
  `tightness_compare --sizes 16,64,256` 破界 0）；翻转为 `unknown` 的只有
  多尺寸量的样例（KMP / 并查集两条复现，以及 `blur` 这类逐项可支配的反而
  由「求值失败」变「达成」）。新增 `tests/cases/test_cost_orders.py::
  test_multi_size_orders`（10 条断言）。
- **`repeats` 里的标量名被渲染成容器尺寸 `n(名字)`**（`cost_domain/bound.py` 的
  `COUNT_TAG` + `cost_domain/cost_interp.py::_bound_to_cost_scoped` 的兜底）：
  `repeats log2(n)` 打成 `log2(n(n))`——`n(名字)` 是 `xs.length()` 的写法，
  求值侧查不到这个名字就按 1 算，κ 于是**不再是上界**，读报告的人也会以为
  `n` 是个容器。现在 repeats 的裸名字是**圈数（标量）**，渲染成 `log2(n)`
  （局部别名 / 模块常量 / 标量形参仍按原规则优先解析，样例的
  `search.in_range` 还是 `log2(n(xs))`）。
- **`const` 别名把「尺寸表达式」折成常数 → κ 变成假上界**（`cost_domain/cost_interp.py`）：
  `const u64 total = text.length() + pattern.length(); while (steps < total)` 的 κ
  曾被算成 **23 那种小常数**，还判「✓ 达成」——真实轮数是 `n(text)+n(pattern)`。
  根因有两处：① `as_cost` 只认字面量 / 名字 / `.length()`，`+` / `*` 落到兜底
  `Cost.num(0)`，而这个返回值被当成**这个名字的值**写进 `env`；②
  `bound_of_condition_wrapper` 的常数替换表读 `env` 的 `is_const()`，于是
  `while (steps < total)` 的轮数被替换成 **0**。
  **不写 const 时反而是 ⊤**（`bound.expr_to_bound` 拒收 `+`），
  「先绑 const」比「直接写」更不保守——方向反了，所以比 ⊤ 更危险。
  修法：`as_cost` 对 `+` / `*` 按结构造 `Cost`（`-` / `/` / `%` 取左操作数，
  u64 下就是上界），**认不出的一律 ⊤（不再是 0）**；常数替换表改由**初值 AST**
  重新求（`_name_count` / `_count_of_expr`，与 `for` 轮数同一份推导），
  只有真常数才进表；新增 `_bound_to_cost_scoped`：界里的局部名按初值**结构展开**
  （`n(text)+n(pattern)`、`2·n(pattern)`、`n(xs)×n(xs)`），
  `const` 声明了但初值认不出 → **⊤**；时间 / 分配 / 增长预扫三处同口径。
  实测：`_tier_a/string_match/_probe/p_sum.eln` 的 `w_const` 由 `κ ≤ 23`（达成）
  变为 `12 + (n(text) + n(pattern))×9 + 9 + 2`；`p_twice.eln` 的 `twice_const`
  由 `κ ≤ 20` 变为 `n(pattern)×2×9 …`。`tests/golden/kappa.json` 22/22 逐字不变，
  `tightness_compare --sizes 16,64,256` 的 144 行 κ 逐行不变、破界 0；
  新增 `tests/cases/test_cost.py::test_size_alias_bound`（15 条断言，
  含解释器 `ops ≤ κ` 对账）。
- **`parallel` 里的 `allocation` 以前一律记 0**：`_alloc_stmt` 没有 `parallel`
  分支，整块落到函数末尾的 `return Cost.num(0)` —— 于是
  `parallel { { v.push(x); } }` 对 `allocation: { max: 0 }` 会判「达成」，
  而真实分配是 1。现在按 B-24 取 Σ（与时间维度同口径）。同一处还修了
  `compare_exact` 对非 `num` 节点取 `.value` 的问题：改用
  `Cost.const_value()`（`par` 取 Σ，其余非数值节点返回 `None`）。
- **`κ` 漏算整条 `else if` 链（上界被实测打破）**：`parser.parse_if` 把
  `else if` 直接挂成 `if` 的**子节点**（没有 `else` 包装），而 `_k_stmt_if`
  只收 `kind == "block"` 的分支 —— 于是 else-if 自己的 `_stmt`(+1)、条件、
  以及其下所有分支**从来没进过 κ**。`_diff/bracket_match.match_brackets`
  因此破界：κ(16)=576 < ops 609（差 33），n=64 差 179，n=256 差 303；
  κ 每轮 33 而实测（最便宜的「非括号字节」路径）每轮 37。
  现在 `block` 走 `_block`、`if` 走 `_stmt`，两支都进 `Cost.max`：
  match_brackets 的 κ 由 `33n+48` 变 `84n+99`，破界 3 行 → 0 行。
  同一条缺口在**分配侧**（`_alloc_stmt` 的 `stmt_else` 只认不存在的 `kind == "else"`，
  else / else-if 的分配一律记 0）与**增长扫描**（`_scan_branches` 的 `push`
  漏扫 else-if）一并补上：calculator 的分配上界与 merge 的 `n(n)` 因此变大（只增不减）。
  代价：`calculator.evaluate` 的 time κ 从 O(n) 变 O(n²)（`tokenize` 的
  `else if (is_digit)` 分支调 `read_number`，内含 O(n) 循环），记为已知债务 ——
  真实实现仍是 O(n)，这是**模型精度**（缺「进度关系」推理），不是实现违约。
- **`tools/kappa_vs_ops.py` 在 GBK 控制台上打印 `✅` 时崩**
  （`UnicodeEncodeError: 'gbk' codec ... '\u2705'`）—— 一行结论都看不到。
  照 `tightness_compare` / `gates` 的做法 reconfigure 成 UTF-8。
- **`cost` 在 `for (x in a..b)` 的分配行崩溃**：`_alloc_stmt` 把 **AST 节点**当 `Cost`
  传进 `Cost.mul`（`Node` 也有 `.kind`，于是被静默收下），到 `render()` 才
  `AttributeError: 'Node' object has no attribute 'render'` ——
  `cost _diff/merge_sort` 以前必崩（`msort` 的循环体里有 `push`）。
  现在时间/分配两维共用同一份轮数推导（`_for_count`），推不出 → ⊤。
- **按值容器的尺寸在调用点换不掉**（`_param_size_table` 只认 `type-ref` 与改名前的
  `Vec`/`FixedVec`）：`func g(vector<u64> xs)` 的 `n(xs)` 在 `g(v)` 调用点保留成被调方的
  形参名，求值时按 1 算 → **低估**。现在按 canon 后的类型头判定，`n(xs)` → `n(v)`。
- **两条「契约可以撒谎」的假绿灯**（`check` 通过、`run` 才炸或静默改掉调用方数据）：
  * **`E-Const-Mutate`**：`const T&` 形参（以及 `const` 绑定）被改写。
    `sort(xs)` / `sort_by` / `slice` / `reverse` / `fill` / `rotate` / `fill_range` /
    `swap` / `dedup` / `vec_push` / `vec_pop` / `hmap_put` 这一族内建是**原地**的，
    `xs[i] = v` / `m[k] = v` 是下标写，`.push` / `.pop` / `.insert` / `.remove` /
    `.enqueue` / `.dequeue` 是变异方法，把 `const` 借用的东西传给 `T&` 形参同样算。
    以前 `func clobber(const vector<u64>& xs) { sort(xs); }` 的 check 是
    「✓ 没有诊断」，run 把**调用方的向量**排了序（`{5,3,9}` → 打 `3`）；
    `.push` 只在 run 期才报 `E-Borrow-Const`，`sort` / 下标写 / `enqueue` 连 run 都不报。
    消息指出形参名与声明行。非 `const` 引用（`vector<u64>&`）的一切改写、只读操作照常放行。
  * **`E-Type-NestedContainer`**：`array<T, N>` 的 `T` 是容器（`array<array<u64, 2>, 2>`）——
    内层元素被默认构造成裸整数 `0`，`g[0][0] = 7` 在 run 期
    `TypeError: 'int' object does not support item assignment`。
    消息给出「展平成一维 + 下标换算」（`array<u64, 4> g;` + `g[r * 2 + c]`）。
    `vector<vector<u64>>` / `map<u64, vector<u64>>` / `vector<array<u64, 2>>` 实测可用，
    不误报。
  * **`E-Spec-ConstMismatch`**：`.tu` 与 `.eln` 对同一个形参写了不同的 `const`。
    `const` 记在 `param` 节点上、不进类型字符串，所以类型比对看不出差别：
    spec 写 `const vector<u64>&`、实现写 `vector<u64>&` 时 check 干净通过，
    run 改掉调用方数据（`xs.push(99)` 后长度 3 → 4）。两个方向都报。
- **`const` 的边界写清楚**：`T q = p; q.push(1);`、按值形参 `sneaky(p)`、`box.insert(1, p)`、
  `return p;`（返回 `const&` 指向的对象）这四条「转手」路径随**值语义拷贝**落地
  （见上）都变成拷贝，不再改到调用方。
  `check` 只在**直接写**与**显式传给 `T&` 形参**两处兜底。
- **`文档/样例/insertion` 的形参改为可变借用**（`const vector<u64>&` → `vector<u64>&`）：
  它是**原地**插入排序（`xs[j] = xs[j - 1]`），标成 `const` 正是上面那条假绿灯。
  `target` 一字未改。
- **`文档/样例/insertion` 的插入排序算法修正**：内层 `else { j = 0; }` 把插入位置
  强行归零，`xs[j] = key` 于是永远写到 `xs[0]`——`{5,2,9}` 排成 `9 5 9`、
  `{1,2,3}` 排成 `3 2 3`。改成 `break;` 后：`{5,2,9}` → `2 5 9`、
  `{1,2,3}` → `1 2 3`、`{5,2,9,1,5,6,0,3}` → `0 1 2 3 5 5 6 9`（κ 不变，golden 21/21）。
  **为什么一直没被发现**：样例只承诺成本、不承诺结果。`insertion.tu` 现在加了
  `guarantees is_sorted(xs);`——`run` 会校验，拿旧算法跑会直接
  `E-Guarantee-Violated: insertion 返回后不满足 is_sorted(...)`。
- **`u64` 的表达式内部改为无符号语义**（之前只有 `run`/`stats` 的返回值和 `print`
  出口转回无符号，表达式里仍是补码）：`u64 x = 0; print(x - 1)` 现在打
  `18446744073709551615`，`x - 1 > 0` 为真，`/` `%` 按无符号整除。
  `min`/`max`/`sort`/堆/AVL 键序/`range`/`.at()`/下标全部随值一起正确。
  无符号值在值层带类型标记（`UInt` 子类，载荷是规范无符号值）；`u8`/`u16`/`u32`
  也按各自位宽回绕（`u8 a = 200; a + 100` → `44`）。
  **混算规则**：只要一边无符号就按无符号做，另一边按 `mod 2^位宽` 重解释，
  位宽取最宽的无符号类型（C++ usual arithmetic conversions，无 `int` 提升）。
  `i64`/`f64`/`bool` 语义不变。`test_interp.py::test_wrapping` 的三条断言
  仍然成立（`UInt` 与补码负数的 `==` 按无符号重解释比较）。
- **批次 5「语义与边界」八条**：
  * `u64` 减法按文档语义回绕；`run` / `stats` 现在按**声明返回类型**打印
    （`0 - 1` → `18446744073709551615`，之前打内部补码 `-1`）；
  * **负下标一律 `E-Index-Negative`**（读写、复合赋值、`.at()`）；
    不再落到 Python 的 `xs[-1]`（以前写负下标会**静默改坏**最后一个元素）；
  * 整数 `/` / `%` 的 `E-Type-Return` 补修法（`-> u64!` + `.tu` 声明
    `error Arithmetic;`，或整数除法转换）并解释为何是 `i64`；
  * `run --vec` 对定长 `array<T,N>` **校验长度**（少给/多给都是参数错误）；
  * `stats` 支持 `--vec` / `--u64`（与 `run` 同一套注入）；
  * 非 UTF-8 源文件报 `E-File-NotUtf8 文件不是 UTF-8 编码：<路径>`，不再 traceback；
  * 用户函数与内建同名时**用户定义优先**（不再被 stdlib 静默压过）；
  * `let` 归入退役关键字 → `E-Decl-OldKeyword` 并提示 `u64 n = 7;`；
  * `.eln` 里 struct / const / error-decl 的文案不再写旧扩展名 `.ai`。
- `fmt` 把模板类型声明从逗号处劈成两行（`array<u64,` / `4096> dp = …`）——
  根因是 `<` `>` 不是折叠 group，泛型实参里的逗号被当成"块顶层逗号"。
  `文档/样例/lcs.eln` 该声明已恢复为一行。

### Changed
- **`src/_baseline/` 与 `tools/diff_check.py` 退役**（2026-11-07）：它们是「新旧实现逐位对拍」的遗留（主批 B 迁移期用的），而基线副本本身就是第二份实现、会和真实现一起漂移。κ 回归由 `tools/golden_check.py` 承担。
- **golden 改为与机器无关**：`golden_check` 不再读本地 `.dispatch.yaml`（那份表是测量产物、gitignore，换台机器就没有或不一样），固定 **max 口径**后重新冻结 **21 条**。理由：**只有这台机器能复现的基准不是基准**。分段口径仍由 `test_dispatch_*` 覆盖。
- **删除迁移遗留模块**：`src/archive_specs.py`、`src/migrate_tests.py`（0 处代码引用），并从 `pyproject.toml` 的 `py-modules` 移除。
- **`src/` 顶层四个转发 shim 保留**，理由写在 `src/README-兼容层.md`：它们用 `sys.modules[__name__] = _real` 换取**模块身份**，`tests/cases/test_ops.py` 的 monkeypatch 依赖这一点；删它们要改 ~200 处 import，收益 1.4 KB，**不值得**。
- **样例目录双权威合并**：`文档/样例` 是唯一源，删除内部镜像 `文档/设计/目前设计/样例/`（60 个文件，与公开侧漂移 13 个）。内部独有的 `const_loop`/`scan_until` 移到 `设计/目前设计/样例-补充/`（不属公开语料）。代码里 52 处旧路径已改。
- **三份 `内建函数.md` 收成一份**：生成器改为直接写公开的 `文档/内建函数.md`，并支持 `--check`；`设计/` 与 `评估/对外文档副本/` 的那两份改成一行指针（此前三份三个大小：6,572 / 6,925 / 7,993）。
- **内部文档断链 67 → 0**（52 条按新位置重写、8 条去链接留文字，2 条为误报）。
- **`文档/实验/` 归档**到 `文档/设计/归档/实验-2026-11/`（50 个文件），19 处工具/文档路径同步（`exp_*.py`、`ab_experiment.py`、`sync_public.py`、`AGENTS.md`）。
- **`cost --explain` 达标时也输出归因**：以前只在超界时给支配项与杠杆占比，
  于是「这条为什么贵」在达标时看不到。现在两者都出。

### Added
- **`elench spec <模块>`**：为缺 spec 的函数生成声明草案。`effects` 从实现推，
  **`target` 只给静态 κ 作参考、不自动生成**（推出来就等于自证）。

### Added
- **`unordered_map` / `unordered_set`**：按最坏情况**承诺 O(n)**（不承诺 O(1)）；
  迭代顺序**未指定**。成本模型按接收者声明类型区分：map/set 记 `log2`，unordered 记 `n`。

### Added
- **`stack<T>` / `queue<T>` 薄封装**：成本全 O(1)，空容器 pop 报错。
  与 C++ 一样是容器适配器，不是新容器。

### Added
- **堆原语 `push_heap` / `pop_heap`**：对齐 `std::push_heap` / `std::pop_heap`；
  成本每秒 **O(log₂ n)**；大顶堆固定；空堆 pop 报错。与 C++ 一样是**原语**不是容器。

### Added
- **有序 `Map<K,V>` / `Set<T>`**：查找/插入 **O(log n) 确定**，迭代顺序 = 键序/元素序，
  键只要全序、**不定义哈希函数**。`map.get` 缺键**报错**（不静默给 0）。

### Added
- **`option_unwrap(o)`**：`None` 时**报错**（不像 `option_unwrap_or` 给默认值）。
  以前想表达"一定有值"只能 `unwrap_or(o, 0)`，把漏判静默变成 0。

### Added
- **`swap(xs, i, j)`**：原地交换，O(1)、不分配；下标越界报错。
  先实现再登记——上一轮顺序做反，导致 check 通过、run 崩。

### Added
- **带下标的遍历 `for (i, x in xs)`**：`i: u64` 由语言维护，作者不碰边界条件；
  成本与 `for (x in xs)` 结构相同（`n(容器)` 乘子），`for (i in 0..n)` 不受影响。

### Added
- **新增对外文档 `文档/可观测-OTel配置.md`**：把 OTel 接入写成用户能照做的步骤（本机 sink / `--push` / 看板 / 环境变量表）。原来它躺在 `文档/设计/未来计划/` 里，而那个目录是 gitignore 的——**用户文档放在用户读不到的地方等于没有**。
- **`f32` / `f64` 落地**：浮点字面量默认 `f64`；`f32` 真按 32 位舍入；
  浮点除零按 IEEE 给出 `inf`/`nan`（不是错误，所以浮点除不返回 `Result`）。

### Fixed
- **公开文档里 17 条链接指向内部目录**（`入门30分钟.md` 9、`内建函数.md` 4、`语法清单.md` 3、`项目介绍.md` 1）：本地能解析、**公开仓库必然 404**，而本地检查器发现不了。已改指公开同源（`语法清单.md`/`内建函数.md`/`入门30分钟.md`），无公开同源的去链接留文字。
- **删除无用文件**：`文档/入门.zip`（0 引用、3 个成员过期）、`corpus/sum.grammar`（0 引用）、`otel.ndjson`（只在帮助示例里出现）、`artifact-report.md` / `pick-图.svg`（测试产物）、`src/elench.egg-info/`（构建产物）、雏雁材料的同哈希备份 docx 与 Word 锁文件。预印本 v0.1/v0.2 的 docx+pdf 移入 `预印本/历史/`（`文档/其他` 不入库，删了找不回）。
- **gitignore 补齐**：`otel.ndjson`、`pick-图.svg`、`artifact-report.md` 都是每次跑测试会重写的产物。
- **新增 \	ools/sync_mirror.py\**：把 \src/\ 镜像到 \editors/vscode/python/\（VS Code 扩展内嵌的解释器）。
  加 \--check\ 供 CI 用；**比较时忽略 CRLF/LF 差异**（Windows 上字节比会误报）。
  审计发现：**13 个文件同名但内容不同**（\dsh.py\ 少 19 行、\	ypenames.py\ 多出已删的 \Deque\），
  说明扩展里的解释器长期比 \src/\ 旧，而**没有任何机制会提醒**。现补 \	ests/cases/test_mirror_in_sync.py\。
- **删除无用文件**：\corpus/sum.grammar\（0 处引用）、\otel.ndjson\（只在帮助文本示例里出现，可再生成）、
  \rtifact-report.md\ / \pick-图.svg\（测试产物）、\src/elench.egg-info/\（构建产物）。
- **`worstcase`：0 行不再算成功**（2026-11-05）。两个问题：① 它依赖 `kappa_vs_measured.SAMPLES`，那个常量已被删 → 一跑就 `AttributeError`；② 更糟的是**产出 0 行也退出 0**，于是「最坏实测」那批数字长期不可复现却没人发现。现在自己算样例目录，且**0 行即 exit 1**（仍待修的是 `worst_for()` 本身）。
- **`tests/runner.py` 的已知失败登记清空**：`test_dispatch_consistency` / `test_invalid` 已连续通过，按规矩从 `KNOWN_FAIL` 删除。现在 **102 文件 / 102 通过 / 0 意外**。
- **高阶函数的成本不再给假上界**（G1）：κ 里出现 `k(f)` 这种未解释符号时判
  `unknown`，不再因为「κ 里没有尺寸量」就当成常数判 ok。
  被调函数的 target 若建立在 `k(f)` 上，调用方不再信任它，退回内联——
  内联时形参绑到实参的具名函数，于是 `apply(xs, sq)`（sq 是 O(n²)）
  真的算出平方项。传 O(1) 的函数则给出常数、判 ok。

### Added
- **`unsafe` 从装饰变成约束**：有 `unsafe { }` 块则 effects 必须声明 `unsafe`
  （`E-Unsafe-Undeclared`）；声明了却没有块是虚报（`E-Unsafe-Overdeclared`）。
- 前置修复：`EFFECT_VOCAB` 补上 `unsafe`，效果名允许关键字——
  以前 `effects { unsafe }` 直接报「期望效果名」，等于**要求用户做一件做不到的事**。

### Removed
- **`spawn` / `asm` / `scope` 下线**：三者都「词法认得、EBNF 写着」但用不了或没用。
  `spawn` 让 κ 依赖核数、`asm` 让操作数不可数（「可验证」降级为「可估算」）、
  `scope` 只服务于 `spawn` 且关键字一直被丢弃。现在报 `E-Spawn-Removed` /
  `E-Asm-Removed` / `E-Scope-Removed` 并给改法。`parallel { }` 保留。
- **`for (x in xs)` / `for (i, x in xs)` / `for (i in 0..n)` 与 `..` 区间**：与 C++ range-for 重合。报 `E-For-InRemoved` 并**教替代写法**（`for (auto& x : xs)` 不拷贝 / `for (auto x : xs)` 按值 / 三段式 for 加下标）。

### Changed
- **整数语义写进文档**（G35）：固定位宽 + **回绕**（`0 - 1` 得 u64 最大值），
  `+ - *` 与一元 `-` 全部 `_wrap`（v0.67 算术全函数化）。
  所以成本式永远是有限数，不存在「溢出成未知」。
- **块尾分号说明**（E27）：解析器接受但不强制，`fmt` 统一补上；
  实测强制的改动面是 9 个测试文件约 30 处内联用例，记为可推后。

### Fixed
- **测量类命令先看解析诊断**：`measure` / `bench` / `stats` / `dispatch` / `cost`
  以前不看诊断就往下跑，于是把「`benchmodel` 写错了位置」报成
  「没有 benchmodel.scale」这种二次错误——作者照它去改，改不对。
  现在有解析 error 就先原样报出来并退出。

### Changed
- **类型检查的精度边界写清楚了**（E12）：`UNKNOWN` 与任何类型兼容，
  只有两边都确定且不兼容才报错。泛型 / 闭包 / 嵌套字段给 UNKNOWN，
  **宁可漏报不误报**。要更精确需做类型推导，记为有意的边界。

### Changed
- **`allocation` 的口径写清楚了**（G33）：它计的是**容器增长事件的次数**，不是字节。
  起始容量 4、之后翻倍，每次记 1；`FixedVec` 恒 0；切片不额外分配。
  所以 `allocation: { max: 0 }` 是「不让容器增长」，不是「不占内存」。
  字节级模型是**有意的非目标**——字节随平台变，次数不变。

### Added
- **`benchmodel.data` 支持 `structs(T)`**（E14）：`pts = structs(Point);` 造 `Vec<Point>`。
  以前内置生成器只有整数序列，参数是 `Vec<结构体>` 的函数**根本测不了**。
  **只填整数字段**；字段类型是 `Vec`/字符串/结构体时直接报错——不猜，
  猜出来的数据会让测量结论失去意义。

### Removed
- **`trait` / `impl` 下线**（E11）：它们不是「约束没实现」，而是写法本身走不通——
  参数不许写 `self`、解析器不认 `impl Show for P`、trait 方法要已下线的 `fn`。
  样例与文档 0 处使用。现在写它们直接报 `E-Trait-Removed` / `E-Impl-Removed`
  并给出改法（和 `assume` 同一条理由：看起来有用、实则不存在的机制比没有更糟）。

### Fixed
- **错误类型的变体现在会被校验**（E3）：`error X { A, B };` 可以写了，
  写错变体报 `E-Variant-Unknown`（措辞是「错误类型」而不是「枚举」）。
  `error X;` 仍然开放、行为不变。之前 EBNF 写着变体列表，解析器却不收。

### Added
- **`measure` / `dispatch` 可以只处理一个 `?!`**：`elench measure <模块> <choose名>`。
  只重测那一段并**按段合并**回表里，其余 label 的段、注释、顺序逐字不变，
  所以 `git diff` 只显示这一段。表还不存在时报错，提示先跑一次全量。

### Fixed
- **分档表多 label 静默覆盖**：写入器在每个 label 的循环里 `open(..., "w")`，
  一个模块有 2 个 `?!` 时后者覆盖前者且不报错。现在一份表装多个 `label:` 段。

### Removed
- **`tightness` 子命令删除**：测量收拢到 `export --full`。
  `export --full` 现在按 `--out` 后缀出 `.svg`（曲线）/ `.md`（表）/ `.csv`（数据），
  不给 `--out` 就在控制台打三节报告（上界 / 超额倍数 / 紧度）。
  测量入口收敛成一个 `_measure_curve()`，不再有两份实现（默认 runs 曾不一致）。

### Removed
- **`assume` 语法移除**：它允许声明无法验证的上界，与「κ 是可验证上界」冲突。
  现在写 `assume` 会在解析阶段报 `E-Assume-Removed` 并给出三条改法。
  实测 15 个样例、21 个函数一个都没用过（`3369051` `55968e9` `26c7b71`）。

### Added
- **`E-Call-Undeclared`**：未声明的外部调用现在报错，而不是悄悄记 1 个操作——
  那等于把被调函数体当成 0 成本，κ 就不再是上界（`185b64a` `116ecc1`）。

### Fixed
- **实参类型检查从来没跑过**：`_diagnose_all` 只在显式传 `.eln` 时才喂类型检查器，
  `check <模块>` 传的是 `ai=None`。现在 `.eln` 自动发现（`cac7b65`）。
- **循环次数由分支决定时算不出上界**（E4）：`monotone_decreasing` 现在区分
  「循环体外赋值（改初值）」与「循环体内赋值（必须仍是减法）」（`95dfbac`）。
- `Cost.max(0, x) = x`（`5d426fb`）；`if` 的分支状态真的合并（`a0ca1db`）。
- 借用规则表 BR-3 的描述写错了——功能本来就在，现已改正并加测试钉住（`992d96b`）。

### Added
- **	ools/fetch_incoming.py**：把 PR / fork 分支 / 任意提交**改动的文件**导出到 _incoming/，
  不切换分支、不 merge、不动工作树；附 MANIFEST.md（状态 + 行数增减）。
  支持 --pr N、--remote alice --ref main、--path src、--dry-run。
- **`cost --explain`：超界归因。** 以前只报「超标 + 比值 ≈ 1072023837083.90」，
  读的人知道超了却不知道从哪下手。现在给四样：支配项（按尺寸指数）、
  **元凶行号 + 源码片段**、`?!` 的逐候选分解、最小修复（拿掉它 / 换候选，
  用同一套引擎重判）。归因是**只读**的——对拍 20 个模块，开关前后 κ 逐字节相同。
- **`cost --diff` 增加「契约 diff」。** `κ` 一动不动但承诺被削弱的改动现在能看见：
  删掉 `guarantees`、`effects` 从 pure 变 io、`scale` 区间缩小、`target` 放松。
  `target` 的松紧用数值判（几个规模点上都变大 = 放松），并额外警告「κ 不会替你报警」。
- **`check --apply=N`：静态检查的 fix-it。** 带 `actions` 的诊断会编号列出，
  `--apply=N` 取一条执行（`edit` 只打印 diff，不落盘）。先覆盖两条：
  `E-Guarantee-Uncheckable`（降级成 `assume`，给的是真实那一行）、
  `E-Fn-NoSpec`（从 `.eln` 签名抄出 spec 骨架）。
- **`elench.toml` 的 `[dispatch] source`**：分档表用哪一份可配
  （`local` 本机最优 / `repo` 入库参考表 / `auto` 默认）。`measure --commit`
  额外写一份 `tables/<模块>.table.yaml` 入库。
- **`--accept-tu-change`**：声明被静默改过就拦住（指纹用词法 token，
  改注释不触发；只在 git 仓库里生效）。

### Changed
- **越界规则改成「沿最近那段延伸」**：低于声明下界用第一段的候选，
  高于上界用**最后一段**的候选。以前一律退回「第一个候选」——那是书写顺序，
  与区间顶端谁更快无关（实测 `count`：按书写顺序取会让 n>4096 的承诺凭空变成 O(n²)）。
  承诺侧用两段哨兵表达，运行期同步，两边同源。
- 机读模式（`--json` / `--yaml` / `--signature`）的 stdout **只有数据**，
  不再混入中文口径说明——这样 `json.loads(stdout)` 才能直接用。

### Fixed
- `cost --json` 的 stdout 前面混着口径行，`json.loads()` 直接失败。
- `cost --diff` 历史端取不到分档表，导致 1.93× 的错误结论（现在多路径查找，
  取不到时标「口径差」并排除在 `--deny-regress` 之外）。
- `otel.report` 在「未实测、只画静态线」时 `TypeError` 崩溃。
- `cpp` 生成的 `uint64_t main()` 违反 C++ 规则；无入口的模块被强制链接。
- 两个长期红的测试：`test_assume`（spawn 裸 `dsh.py`，只在 cwd==src 时能跑）、
  `test_invalid` 的 V14（常量边界的循环本来推得出上界，报警是**假警报**）。

### Added（早期）
- **`cpp/docnums/`：文档数字绑定检查器的 C++17 实现**（单文件、零第三方依赖）。
  与 `tools/doc_numbers.py` **同语义**：同一份 `tools/doc_numbers.json`、
  同一套 `数字<!--dn:key-->` 标记、同样的退出码；`parity.py` 逐字节对照两版输出
  （`--list` / `--check` / `--scan` 全一致）。
  用途：没有 Python 的环境里也能在 CI 里查数字漂移。
  CI：`.github/workflows/docnums.yml`（ubuntu / windows / macos 三平台编译 + parity）。
  三个移植坑记在 README 里：**Windows `argv` 是 ANSI**（中文路径必炸，改用
  `CommandLineToArgvW`）、**窄字符流按 ANSI 解路径**（改用 `filesystem::u8path`）、
  **动态链接会缺 DLL**（`-static` 出单文件）。
- **tools/doc_numbers.py：文档数字绑定**（零依赖，可给别的仓库用）。
  文档里写 数字<!--dn:key--> 就把这个数绑到 tools/doc_numbers.json 的来源上
  （JSON 字段 / 求和 / 长度 / udit_numbers.py 字段）；来源一变 --check 就非零退出。
  另有**零标注**模式 --scan：把无法追溯到来源的数字列出来。
  落地当天就抓到两处漂移（测试文件 85→86、断言调用点 1,390→1,402）与一处
  附录合计 κ（27,589,793 含了输入无效的 	opk）。
- **tools/real_false_reject.py**：在**真实样例**上量误拒率（四档阶：常数/线性/n log n/平方）。
  实测 22 例：判对 17、**误拒 0、漏判 0**、判不出 5（E23 多尺寸量）。
- **tools/ablation.py**：现场生成分档表、复现「分段 vs max」那张表；
  分档表**不提交**（测量产物，不该钉成事实）。
- **`cost --diff=<git-ref>`**：**成本承诺的 diff**——比的是 κ 的**类别**，
  不是毫秒数。三档：`L1`（阶变了）/ `L3`（只有常数变了）/ `不变`；
  加 `--deny-regress` 则阶变差时退出 1（CI 用）。
  只 diff **源码版本**（两端都用当前成本模型），不比实测、也不比模型版本
  （后者归 `golden_check`）。`tests/cases/test_cost_diff.py` 覆盖。
- **`--relax=R1|R2|R3|R4|R5`**：把 `设计-自由与严谨` 的五条限制**单条放开**
  （R1 while 圈数、R2 递归 depth、R3 benchmodel/scale、R4 隐式转换［v0 无独立检查］、
  R5 分配建模）。被放开的那条从 error 降为 warning，并在消息末尾标注。
- **`tools/relax_sweep.py`**：六种配置 × 3 个故意违规语料，量「放开一条限制
  会多出多少**不可证的通过**」。实测：R5 放开产生 1 例假阳性（承诺 0 分配却分配），
  R1/R2 放开**不**产生假阳性（κ=⊤ 会被 `--deny-unknown` 接住）。
  **已修**：`--relax=R5` 现在只豁免「未知（⊤ / 未建模）」，**不豁免已知超支**——
  放宽开关的语义是"允许不知道"，不是"允许撒谎"。修后六种配置假阳性 0 例。
  同时暴露一个洞：**只加 `--deny-debt` 时，κ=⊤ 的程序会通过**——CI 需同时加
  `--deny-unknown`。
- **`tools/false_reject.py`**：生成语料上的误拒/漏判率（4 族 × 20 变体 × 3 档 target）。
  实测 55/55 判对，0 误拒 0 漏判。
- **文档：`文档/入门/成本记账单位.md`** —— 解释器把什么算作「一个操作」：
  表达式（每个节点 1 步、方法调用 `max(3, 2+实参)`、括号免费…）、
  语句、控制流（`if` 取 max、循环每轮含 1 步记账）、函数固定 2 步；
  附 κ 与实测的核对表，以及两处已知的 ±1 不对齐。
- **文档：`文档/入门/命令一览.md`** —— 16 个子命令按目的分组（检查/成本/门禁/其它），
  每个给一行用途、关键开关与例子；含 `--deny-debt`、`--deny-unknown`、`--variant=max`、
  `export --full`、`tightness`、`ELENCH_STEP_BUDGET` 等新开关。
- `elench --help` 补上遗漏的 `tightness` 子命令。
- **`ELENCH_STEP_BUDGET`**：解释器的「疑似死循环」护栏（默认 500 万步）可用环境变量
  放宽——大样例（`count` 在 n=4096 是 1.7e7 操作）会**正常**超过它。
  默认值不调大：调大就等于取消护栏。
- **`measure --max-size=N`**：把分档表的测量范围截到 N（表外按运行期回退），
  否则大样例的测量会撞步数预算、直接不产出表。
- **`export --full --variant=random`**：跳过最坏搜索、紧度按**典型**算——
  这是 A3 消融的对照臂（默认仍是搜最坏的 `worst`）。
- **`cost --variant=max`**：强制「所有候选取 max」（消融基线）。默认仍是
  **分段上界**（运行期表同源）。实测对比：`count` 在 n=4096 上
  分段 98,886 vs max 352,591,942（**3.57×10³ 倍**）；`sum.average` n=4096
  57,372 vs 139,330（2.43×）。两条都仍 ≥ 最坏实测。
- **`build --deny-unknown`**：把「无法判定」变成**拒绝构建**（CI 可用）。
  默认仍放行（渐进采用），但「不知道」不再能被当成「没问题」。
- **`W-Concurrency-Time`**（G25）：函数用了 `parallel`/`spawn` 且 spec 有 `time`
  target 时给出警告——v0 的 κ 按**单线程语义**计，并发下的耗时与竞争不在承诺内。
  `elench cost` 也会打同一行（看 cost 的人不一定看 check）。
  `tests/cases/test_gate_dimensions.py` 覆盖。
- **`elench tightness`**：多规模输出 `slack_typ`（κ ÷ 典型 ops）曲线，
  `--sizes` / `--runs` / `--out`（md/json/csv）/ `--format`；
  与 `tools/worstcase.py` 产物合用后多出 `ops_worst` / `slack_wc_ub` 两列。
- **`elench export <模块> --full`**（`--report` 为别名）：**分三节**的完整报告——
  上界（κ + 承诺上限 target）、**超额倍数**（κ ÷ target）、紧度（相对典型 /
  相对最坏 + 最坏输入配方），外加**渐近紧度（斜率比）**与**常数阈值 n0**。
  刻意不用缩写、不把三件事挤进一张表；`--sizes` / `--runs` / `--max-size` /
  `--max-points` 可调，`--out` 写出，`--split` 拆文件。
- **SVG 图（`src/chart.py`，零依赖）**：`--full` 默认写**一个文件、三张面板**
  （上界与实测 / 紧度曲线 / 各点紧度带数值标注）。支持多系列、对数或线性纵轴、
  数值标注、参照线、n0 竖线、`stack_svgs` 嵌套堆叠；重合序列合并并注明。
- **`src/emit.py`**：一种输出、三种格式（按扩展名或 `--format`），
  `tightness` / `cost` 共用；`cost --out` 改走 `--signature`，csv 是真数据。
- **fuzz 检测器扩到 13 个构造**：新增 `hof`（函数值传参）与 `dispatch`
  （`?!` + `choose` + `kernels`）两个程序族。
- **`tools/worstcase.py`**：对抗输入搜索（L1 六种形状），产出 `worstcase.json`。
- **测试**：`test_cli_smoke`（17 个命令都必须有反应）、
  `test_builtin_deprecation`（弃用内建的唯一测试库）。
- **文档**：`文档/项目介绍.md`（含「资源界 vs 契约」术语脚注）；
  `语法清单` §6.1 补真实可跑的 `?!` 骨架。

### Changed
- **承诺口径统一为「分段上界」（`piecewise`）**：`run` / `stats` / `bench` 以前**不读分档表**，
  运行期退回第一个候选——承诺按段算、跑的却不是那个候选，会系统性破界
  （实测过 `search.binary_search` 余量 −341、`gap` 逐段 402 / 实测 733）。
  现在三条命令都传 `table_path`，与 `measure` / `export` 同源。
- **`export` 的判据线与门禁同源**：有分档表时画分段式，没有时退回 `max`。
  以前无论有没有表都画 `max`，与 `cost` 的判定不是同一条线。
  同步改正 `README`、`segment_plan`、`dsh`、`otel`、`cost_interp` 里
  「承诺永远是 `max over candidates`」的旧说法。
- **文档/ 三桶分家**：目前设计/未做/（**排期中的任务**，入口 制作顺序.md）、
  未来设计/（长期设计与分析，**不是待办**）、目前设计/未做/已归档/（做完的）。
  同时刷新 文档/README.md 的目录图（结构版本 v3 → v4）并修掉两条早已失效的引用。
- **`?!` 的承诺口径统一为「分段上界」**：有分档表时按运行期同一份表逐段取
  「该段真正会跑的候选」的 κ；无表 / 单候选 → 退回「所有候选取 max」。
  分段**只从运行期表读**（`load_dispatch_table`），不用静态 `plan()` 的最优分段——
  两者不一致就重演 `-341` / `402 vs 733` / `426,001 vs 729,109` 那三次破界。
  连带：`otel.series` 的承诺线（`static_bound`）改画分段值，
  `static_promise_max_operations` 保留整函数 max 供对照；README 硬规则同步改写。
- **κ 更紧：`while` 每轮固定记账 3 → 1**。条件表达式已经**整条**计进
  `cond_cost`，那 3 个操作是 E35 时代的补偿，之后变成**重复计数**：
  实测每轮 9 步、κ 记 14 步（1.46 倍）。改后每轮 12 步。
  实测影响（κ ÷ 最坏实测）：`scan_until` 1.59/1.55/1.54/1.53 →
  **1.46/1.41/1.40/…**；`const_loop` 1.46 → 1.28。
  安全性：`kappa_vs_ops --strict` 破界 0、`kappa_vs_measured` 10/10、
  golden 21 条全部重冻（14 条变化，均变小）、`gap` 静态交点 2716 → **2222**。

### Fixed
- **`pyproject.toml` 的模块清单补全**：漏了 `chart` / `cost_diff` / `cxx_map` / `emit` /
  `ffi_c` / `fmt` / `grammar` / `guarantees` / `jsonval` / `kinds` / `otel_export` /
  `policy` / `requires` / `segment_plan` / `shape` / `stdlib` 共 16 个顶层模块，以及
  `cost_domain` 包。以前 `pip install` 出来的包一跑就 `ImportError`，而安装本身不报错。
  现已验证：安装后 `elench --version` 与 `elench cost` 均可正常执行。
- **测量器把「输入无效」当成了证据**：	opk(xs, 32) 在 n=16 上 k > n，函数立刻早退，
  量到 14 个操作——论文却把它当 3,135 引用。kappa_vs_measured 现在检查
  **benchmodel 里写成字面量的标量**是否超过规模，超过就单列「输入无效」而不是报步数。
  影响：Table 7 从 10/10 变成 **9/9 + 1 行「输入无效」**，预印本 v0.3 已同步。
- **tools/audit_numbers.py 的统计口径是错的**：只扫 src/ 顶层，于是报
  「测试 0 文件」「check( 15 处」「关键字 4 个」。改成递归扫 src/+	ests/、
  从 lexer 取关键字真值后：**51 实现文件 / 18.5k SLOC、85 测试文件 / 11.5k SLOC、
  1,390 处 check(、44 关键字（4 个已下线）**。论文里的数字只能从这里出。
- **只给 `--deny-debt` 会漏掉 κ=⊤ 的程序**：新增一行提示，用 `--deny-debt` 而未用 `--deny-unknown` 时报告「另有 N 处无法判定未被拒绝；CI 请加 --deny-unknown」。
- **门禁只看 time，allocation 违约放行**（H1）：`--deny-debt` 过去只比
  `cost_of` vs `target_cost`，于是「声明 `allocation: {max:0}`、每轮都分配」
  的程序 exit 0。现在**逐维判**：time 用 `compare`（按阶）、allocation 用
  `compare_exact`（按数量）。
- **常数 target 被误判成 unknown**（H3）：`target` 写成 `O(1)` 而 κ 含尺寸量时，
  过去一律判 unknown —— 于是「声明 O(1)、实现 O(n)」**必然判不出**。
  现在判 **debt**（随规模增长必然超阶）；分配维度同理（`max: 0` vs `n×1`）。
- **`--full` 不再静默截断规模点**：以前先按 `≤4096` 砍大点、再"最多 6 个"抽稀，
  于是 `benchmodel.scale` 写 `range(1, 65536, *2)` 的 **17 个点只剩 6 个**。
  现在写几个测几个；限速/抽稀必须显式（`--max-size` / `--max-points`）。
- **`--full` 接受模块路径**（`elench export 文档/入门/例子/crossover --full`），
  并区分"找不到模块"与"模块里没有带 target 的 spec"两种报错。
- **`Cost.max(⊤, x)` 不吸收 ⊤**：返回 `max(⊤, x)` 且 `is_top()` 为 False，
  下游会把「无法判定」当成「有界」。现在返回 ⊤。
- **`for_each` 只认 HashMap**：现在也接受 Vec（一元回调），并进了名字解析表。
- **`cmd_tightness` 曾被插进 `_main_impl` 中间**，导致其后 12 个子命令静默失效；
  已挪正，并加了 `test_cli_smoke` 守门。

- **诊断 → 可执行修法（restart）**。以前诊断只给一句
  `修法：降低实现开销，或（仅当确认放宽契约时）改 .tu 的 target`——既要人翻译成命令，
  又**教人改承诺**。现在债务会列出一份**编号清单**（重测分档表 / 查看静态交点 /
  以默认（宽松）模式编译 / 定位超支来源），`--apply=N` 直接执行其中一条。
  **硬规则：任何修法都不许修改 `.tu` 的 `target`**（`test_restarts.py` 有专门断言）。
  本地要"先过一遍"就用 `elench build`——**它本来就是宽松的**（只记债务、退出 0），
  门禁那档是 `--deny-debt`；没有另造开关。

- **成本表里的「副作用」分列**（Quasar 的"按 effects 记改动"改造版）。`elench cost`
  现在每个函数多一行：`副作用：声明 fs；调用点 fs×2（read_file）；外部项 ext(read_file)`。
  统计的是**可指认的**部分：函数声明了哪些 effect、每个 effect 有几处调用点、κ 里
  有哪些 `ext(…)` 项。**不把解释器操作按 effect 分摊**——一次操作不属于任何副作用，
  "40% 算 fs、60% 算 io"是编数。所以 CSV 不加固定列（effect 计数是**函数级**的，
  逐行重复会误导）。`test_effects_cost.py`（11 项）。

- **FFI 调用前的形状校验**（`src/shape.py` 校验 + `src/ffi_c.py` 编组）。以前参数对不对
  全交给 ctypes：个数/类型错给的是 Python 层的 `TypeError`，而**声明 `u64` 传 `-1`
  会静默变成 18446744073709551615**——不报错、悄悄变成另一个数。
  现在在 `ctypes.CDLL` **之前**按 `.tu` 签名检查个数/类型/取值，报 `E-FFI-Shape`；
  后端不认识的声明类型报 `E-FFI-Unsupported`（**与调用者写错分开**，否则人会去改
  一个没写错的调用）。校验器**与后端语言无关**：多语言只需加编组表，不动校验。
  `test_ffi_shape.py`（16 项，全部不需要真实外部库）。
- **`guarantees` 从"能写没人看"变成"可判定 + 真校验"**。以前它只是解析器接受、
  没人读取的子句——一个**假的承诺位**。现在分两类：
  **可检查**（`is_sorted` / `contains` / 比较式 `ret >= 0` 及其布尔组合）→
  `check` 报 `I-Guarantee-Runtime`，`run` 在函数返回后**真的求值**，
  违反抛 `E-Guarantee-Violated`；**不可检查** → `E-Guarantee-Uncheckable`（warning），
  要求改写或降级成 `assume`。返回值用 `ret`（别名 `result`）引用。
  为什么不一律检查：对任意谓词、任意实现做静态检查等于在 `check` 里跑运行时求值——
  既慢又可能不终止。**能静态说清的才进检查器，其余必须显式降级。**
  `test_guarantees.py`（14 项）。
- **`elench grammar <模块.tu>`：从 `.tu` 导出受限语法**（借鉴 Pel 的"语法即能力面"）。
  三件产物：**语言 EBNF**（`.eln` 语句/表达式产生式）、**模块词汇表 JSON**
  （本模块允许的函数/外部函数/类型/常量，含签名与 effects）、**打洞骨架**
  （每函数一个"签名齐全、body 是洞"的骨架）。`--format=ebnf|json|both`，`--out=` 写文件。
  **不做完整 AST 的 JSON Schema**：那等于把 `parser.py` 重写一遍，两份语法必然漂移，
  而漂移的语法比没有语法更危险（解码器放行、检查器拒绝）。
  `test_grammar.py`（25 项）用 `lexer.KEYWORDS` 逐字比对 EBNF，防漂移。

### Fixed
- **解释器六项语言表面修复**（审计复核里仍成立的条目）：
  **区间 `for (i in a..b)`** 以前**静默 0 次**，现在按半开区间执行；
  **`break` / `continue`** 以前词法里根本没有这个关键字（运行期报"未绑定的名字"），
  现在解析、执行、并在两域 cost 表里登记为"只会减少执行，不增加上界"；
  **`/` `%`** 以前返回 `Ok(...)`（`return a/b` 得到 `Ok(3)`、`a%2==0` 恒假），
  现在返回整数、除零抛 `E-Arith-DivZero`（规格里由 `requires b != 0` 负责）；
  **复合赋值**不回绕（`2^64-1 += 1` → 2^64），现在走 `_wrap`，并修掉把 `True` 当
  `force` 传的 bug；**结构体字段赋值** `p.x = 5` 以前报"未实现的赋值目标"，现在可写
  （与 `p.x += 5` 行为对齐）；**`as`** 以前不截断（`300 as u8` → 300），现在按目标位宽截断。
  κ 因此有 6 处**变大**（`calculator.precedence` 1 → 5 等），方向正确：golden 已重冻，
  Table 7 的 `calculator.evaluate` 25,393,728 → **28,065,102**，**10/10 仍成立**。
- **门禁不可撒谎**：`run` / `build` 现在与 `check` 走**同一个检查入口**
  （`_diagnose_all` = `check_files` + 类型检查）。以前 `build` 只调 `check_module`、
  不跑类型检查，于是 `func f() -> u64 { return true; }` 能"✓ 构建通过"、
  `run` 还能打出 `True`。现在两者都 `rc=1` 并列出诊断；`run --no-check` 才是逃生阀
  （明确打印"输出不可信"）。
- **解析失败不再产出 κ**：`parse_source` 把诊断挂到 module 上，`CostInterp` 见到诊断
  就返回 **⊤**。以前 `.eln` 里的语法错误会让坏语句被丢掉，κ 照样算出数
  （`match (n) { 0 => … }` 直接算出 **0**——一个"什么都不承诺"的上界）。
- **`match` 字面量模式 + 单语句臂计入 κ**：`0 => return 1,` 这种臂以前既不解析、
  也不进成本式（κ=0）。现在 parser 接受字面量模式，`κ(match) = 1 + κ(被匹配) + max(各臂)`。
- **循环内分配不再算 0**：`_alloc_stmt` 补上 `for` / `match` 分支，`_alloc_expr`
  把数组字面量记作 1 次分配。以前 `for (x in xs) { Vec<u64> t = [1,2]; }` 的
  `alloc = 0`，`allocation: { max: 0 }` 于是静默"达成"。
- **VSCode 扩展 0.6.15 的 `.vsix` 漏了整个 `cost_domain/` 包**：打包脚本只
  `listdir` 顶层 `.py`，包目录被跳过。装好的扩展一算成本就 `ImportError`，
  而 `.vsix` 里看不出来。0.6.16 改为**递归打包**（54 个条目，含 `cost_domain/` 5 个文件），
  并用包内 `python/` 实跑 `cost` 冒烟验证。

### Removed
- **图上那条"逐段/候选 κ"（橙色虚线）删了**。承诺现在本身就是分段式，
  再画一条同形状的线只会让人以为存在两个不同的界。图例回到两行
  （承诺 / 实测），`static_piecewise` 这一列仍保留在 CSV 与文本报表里。

### Changed
- **`elench measure` 默认测满 `benchmodel.scale` 声明的每一个点**，删掉隐式的
  `cap = 4096`。旧行为是"超出的点直接丢掉、只打一行提示、表照常产出"——
  于是 `.tu` 声明 8..65536、表只到 4096，**benchmodel 形同虚设**，
  运行期在表外退回第一个候选，承诺却按表算 → 表外破界
  （`gap` n=8192：承诺 426,001 / 实测 729,109）。
  现在：测不动就**报错**（列出失败候选与缺的点），让人去改声明或改实现；
  真要降级必须显式 `--dev-cap=N`，产出的表会写 `degraded: true`，
  标注**不可用于承诺**。`gap` 重测后覆盖 8..65,536。
- **`cost` / `build` / `export` / `header` 增加覆盖校验**：表没覆盖声明区间时
  **默认警告**（说明"表外按回退候选计，仍成立但不紧"），`--strict-coverage`
  才当错误。为什么默认不硬报错：表外并非不安全（承诺与运行期都退回同一个候选，
  实测 gap n=8192/16,384 均 OK），缺的只是紧度；而把 2²⁰ 全测满是分钟到小时级，
  硬报错会挡住所有样例。
- **分段的代数补全：断点求并 + 16 段上限**。`piece + piece` / `piece × piece` /
  `max(piece, piece)` 现在按**断点并集**逐段合并（以前只在两段区间完全一致时才合并，
  否则退回 `max`）；超过 `MAX_SEGMENTS = 16` 退回 `max` 并计数。
  上限不是为了性能，是为了**语义**：分段的含义是"运行期在这里换候选"，
  代数产生的额外断点在运行期并不存在，几十段会让读者误以为运行时在几十处切换。
  另外 `log2(piece)` 改为**保持分段**（log 不动断点；此前的"退回"过于保守）。

### Added
- **`test_segment_plan.py` 增加多候选用例**：三个候选切段、被支配的候选不出现、
  两次交叉（三次换手）、多段首尾相接。
- **`test_piecewise.py`**（上一提交）继续覆盖 S2 同源与代数回退计数。

### Fixed
- `_zipper` 在只剩一段时返回 `None`，导致 `max(piece, piece)` 退化；
  改为**保留单段 piece**（它的越界行为与段内不同，直接返回该段会把越界算错）。


- **分段上界：`Cost` 新增 `piece` 节点**——承诺从"一条 max 式"变成"按 n 分段的符号式"，
  每段用**运行期真正会跑的那个候选**的 κ。收益（都是 n=16）：
  `count.count` 7,114 → **5,594**、`sum.average` 678 → **252**；
  `gap` 在 n=8 从 220,237 → **953**（n=4096 是 314,261，表外 8192/16,384 按回退 852,089/1,704,057）。
  代价与前提写在 `文档/设计/目前设计/未做/设计-分段上界.md`：**S2 同源**——
  分段必须取自运行期同一份分档表，否则就是假上界（实测踩过：用静态交点切段、
  运行期却没有表，`search.binary_search` 余量变成 **-341**）。
  无表 / 单候选时退回 `max`（sound，松）。
  代数：`+常数`、`×常数`、`max(常数)` 保持分段；`×变量`、`log2(分段)` 退回普通节点
  （**数值仍精确**，只是不再被识别为分段），退回次数可计数（`fallback_count()`）。
- **`tests/cases/test_piecewise.py`（18 项）**：节点语义、构造不变量（空洞/重叠/嵌套必须拒绝）、
  代数闭合与退回计数、**S2 同源端到端**（测量与承诺传同一份表，逐档 `ops ≤ κ`，含表范围外）、
  以及"分区真的更紧"（`count` 回到 5,594）。

### Fixed
- **`docs` 与 `bench` 的一致性**：分段落地时发现，比较 `ops ≤ κ` 若不传同一份表、
  不取模块自己的尺寸变量（`count` 用 `text`、`sum` 用 `scores`），会得到**假破界**。
  已写进 `test_piecewise` 的 S2 用例，防止下次再"用两张表对账"。

- **[同一轮的中间步骤，已被上面的分段上界取代] 承诺先改成 `max over candidates`**。
  以前"有表就按逐段上界"：把每段候选在**段末端**折成常数再取 max。实测
  （`gap`，表只覆盖 8..4096）一次暴露三件事：
  ① 蓝线是一条**水平线**（常数压住另一候选的线性项）；
  ② 真实**拐点被抹掉**（`104n+110` 与 `23n+220,042` 在 n≈2,715 交叉，本该在图里）；
  ③ **表范围外破界**——n=8192 承诺 426,001、实测 729,109；n=16,384 承诺 596,885、
  实测 1,458,197。修后逐档重测：220,237 / 426,105 / 852,089 / 1,704,057，全部 ≥ 实测。
  表只剩一个用途：**运行期选哪个候选**（`interp.Interp`）。
  `otel.series` 的 `static_bound` 也改成**永远**是整函数 κ（以前没实测时画的是逐段值，
  这正是那条平线的来源）；逐段值仍保留在 `static_piecewise`（橙虚线）。
- **新增 `src/segment_plan.py`：静态分段建议**。`elench cost` 现在对每个 `?!` 打印
  交点与建议分段（`gap`：`?!kernel：静态交点 n=2716（heavy ↔ setup）`）。
  **不需要测量**——两条 κ 都是编译期表达式，解方程即可；带符号项/⊤ 的候选被跳过并标出。
- **golden 重冻**：2 条变化，全部**上升**（`sum.average` 252 → 678；
  `count.count` 5,594 → 7,114），原因是承诺现在必须覆盖"小 n 才用"的那个候选。

### Added
- **`tests/cases/test_comment_invariance.py`（16 项）**：同一程序加/不加噪声注释，
  `κ` 符号式、`κ(n)`、实测 `ops`、诊断集合**四项都必须逐字相同**。噪声里故意写
  `if k == "call"`、`target { time: O(n^3) }`、未闭合括号引号。附带钉住两条边界：
  `/* */` **不是**注释（必须报 `E-`，不能静默）；`//` 里的 `/*` 只是文本。
- **`tests/cases/test_doc_snippets.py`（7 项）**：把 `文档/*.md` 里每个 ```` ```elench ````
  代码块丢给解析器，**不允许出现 `E-`**；行内语句片段（`` `for (...)` `` 等）去掉 `…`
  后包进函数体同样要编译通过。纯语法模板用块首注释 `// doc-template: <理由>` 显式豁免
  （理由写在代码里，不是测试里的一张手写清单）。
  这条测试一上来就抓到一个**一直存在**的文档错误：`语法清单.md` 的遍历行写着
  `for (u64 x : xs)`，而解析器只认 `for (x in xs)`。

### Changed
- **文档纠错**（`文档/语法清单.md`、`文档/项目介绍.md`）：`for (u64 x : xs)` →
  `for (x in xs)`；`spec choose(...)` → `spec pick_impl(...)`（`choose` 是保留字，
  那个块从来编不过）；`val idx = …` → 合法函数体（`val` 是退役关键字
  `E-Decl-OldKeyword`）。
- **分派改成查表：新增 `src/kinds.py` 注册表**（kind → 两域处理器并排）。
  四条 `if k ==` 长链（interp 的 `_core_eval` / `_core_stmt`、cost 的
  `_core_expr` / `_core_stmt`）拆成 53 个 `_k_*` 方法（33 个 `(层, kind)` 条目），分派只剩
  `_name = COST_EXPR.get(k)` + `getattr`。方法名带层前缀（`_k_expr_call` /
  `_k_stmt_assign`）——同一个类里 `dispatch` 在两层都存在，不带前缀会**互相覆盖**。
  「这个 kind 在两个域里各做什么」从**两条要人肉对照的链**变成**一张能读的表**。
- **`walk.py` 不再从源码正则抽 `if k ==`**：静态抽取会被注释骗——把两条死分支
  改成注释说明之后，检测器报出过 `ref` / `try` 两个**不存在**的 kind。
  现在表是唯一事实，守门器改做四件事：两域对称、**登记即实现**（表里的方法名
  必须在类上真的存在）、清空单反向校验、无孤儿。`test_drift` 跟着重写（22 项，
  含反向控制：喂假的处理器名必须被抓到）。
- 顺手清掉两个死条目：`COST_EXPR` 里的 `not`（解析器只产 `unary value="not"`，
  从没有 `Node("not")`），以及 `struct-lit` / `record-literal`（只产 `struct-literal`）。
- **成本域合并为 `src/cost_domain/` 包**：`symbolic` / `bound` / `cost_check` /
  `cost_interp` 四个模块搬进一个目录，包内一律相对导入（`from .symbolic import Cost`），
  绕开兼容层、避免循环依赖。`src/` 顶层保留**同名兼容层**：
  `sys.modules[__name__] = 真模块`，于是 150+ 处 `from cost_interp import …`
  一个字都不用改，`test_ops` 对 `cost_interp.OP_COST` 的 monkeypatch 也仍然
  打在**真对象**上（复制名字会让 patch 打在副本上，那种"修复"等于没测）。
  `interp.py` **不合并**：它是可信测量基线（ops 计数器），和 κ 的实现放进
  同一个文件，等于让裁判和被告住一间屋子——"两边一起错"正是检测器唯一的盲点。
  行为零变化：21 条 golden 全等、`kappa_vs_ops --strict` 破界 0。
- **`editors/vscode/python/` 镜像重新同步**：此前落后 **13 个文件**
  （含 `interp.py` / `checker.py` / `dsh.py` / `bound.py` / `cost_interp.py`）——
  也就是说扩展里跑的是**旧实现**。这不是这次合并造成的，是一直没人对账；
  现在镜像与 `src/` 逐字节一致。
- **67 个测试从 `src/` 搬到 `tests/cases/`**，`tests/runner.py` 成为唯一入口
  （并行 7 秒跑完全套，一个退出码，已知失败显式登记且通过时报 `xpass`）。
  `src/` 现在只剩产品代码——扩展镜像那条"复制**非测试**的 `src/*.py`"的规矩可以退休了。
  搬家的一个细节：测试里大量 `os.path.join(HERE, "..", "文档")` 都假设
  「本文件在 `src/`、上一级是仓库根」，所以搬迁时**不逐个改 200 处路径**，
  而是让 `HERE` 继续保持旧语义（= `src/`），另加 `CASEDIR` 表示真实目录。

### Added
- **解释器操作数计数器**（`src/interp.py`）：`ops_reset()` / `ops_of(fn)`，
  在 `_eval`（表达式求值）/ `_stmt`（语句执行）/ 内建调用三处各 +1，
  **粒度为整棵调用树**（进函数记起点、返回时结算）。**默认关闭**，
  `ELENCH_COUNT_OPS=1` 或 `--ops` 才开；热路径上只有单分支 `if _COUNT:`。
  `counters()` 多出 `operations` / `operations_by_function`。
- **`elench stats <模块> <函数> --ops`**：多一列「操作数」，`--ops` 输出 JSON。
  单位与 `elench cost` 的 κ **相同**（都是操作），因此可以直接比大小；
  `evaluation_steps` 与纳秒**不能**（口径见 `src/test_units.py`）。
- **`tools/kappa_vs_ops.py`**：逐函数 × 每档 n 打 `n, κ(n), ops(n), ops≤κ?`，
  末行给结论（成立 / 破界 / 未测）。`--json` / `--strict` / `--kappa-scale`。
- **`src/test_ops.py`**：23 项——开/关计数输出一致、ops 随 n 单调、
  ≥ 8 函数 × 3 档（64/512/4096）`ops ≤ κ`、反例（monkeypatch `cost_interp.OP_COST`）能抓、
  计数开销 ≤ 3×（实测 1.08×）。

### Changed
- **κ 补上四类少算**（`src/cost_interp.py`；只增不减，`golden` 3 条变化**全部上升**）：
  · **递归**：按 `.tu` 的 `recursion depth <= N` 乘「函数体只算一次」——
    `down(xs, 8)` 的 κ **33 → 153**（实测 ops 104，缺口 71 → −49）；
  · **内建实参**：`print` / `map` / `filter` / `fold` / `count_if` / `sort` / `sort_by`
    把实参成本计入（`print` 5 → 6，缺口 1 → 0）；
  · **`min` / `max`**：补上内建派发那 1 步（4 → 5，缺口 1 → 0）；
  · **函数值**：具名函数实参绑到 `func(...)` 形参（`host_v` 6 → 8，缺口 0）；
    调用点不知道绑给谁时留 `k(g)` 符号项，**不再静默给一个数**。

### Added
- **`E-Recursion-Unbounded`（C11-b，编译期错误）**：函数有递归（直接或互递归），  `.tu` 却没写 `recursion depth <= N;` → 报错。不声明深度时 κ 只按一层计、
  **小于真实成本**，`elench cost` 还能打印「✓ 所有函数都在 target 之内」——
  宁可编不过，也不要假承诺。`check` / `build` / `cost` / `export` 四道闸一致。
- **`src/test_recursion_bound.py`**：直接递归、互递归、只声明一个、非递归不误报、
  `cost` 拒绝算 κ、`build` 退出码 1。

### Fixed
- **没有合法场景的写法不再静默变成别的东西**（2026-11-10；新增三个诊断码
  `E-Keyword-Unsupported` / `E-Unary-Unsupported` / `E-Type-Unknown`）：
  - `goto done;` 与标签 `done:` → `E-Keyword-Unsupported`。以前 `goto done;` 走声明
    解析，变成「类型 `goto`、变量 `done`」的 `var-decl`——`check` 全绿、什么也不做；
  - 前缀 `*x` → `E-Unary-Unsupported`。以前是运行期恒等的 `deref` 节点（`2 ** 10`
    曾因此静默得 `20`）；`a * b`（二元乘法）**不受影响**；
  - `&x` → `E-Unary-Unsupported`。**例外**：实参位置、且被调方该形参是 `T&` /
    `const T&`、操作数是变量/下标/字段 → 接受为**显式借用标记**（等价于不写 `&`；
    样例 `calculator.eln` 的 `apply_top(& values, & operators)` 依赖这一格）；
  - 未知类型名（`u128` / `size_t` / `foo_t`；声明、形参、结构体字段、`as` 目标）
    → `E-Type-Unknown`。以前**无初值时零诊断**（`u128 x;`），有初值时给的是误导性的
    `E-Type-Assign 声明为 u128，初值是 i64`；
  - 不受影响：`a * b`、`a & b`、`const vector<u64>& xs` 形参；17 个样例仍全绿。

---

## [0.6.1] — 2026-11-01

### Added
- **`install.sh`**：Linux / macOS 一键安装（`--dry-run` / `--uninstall`）。
  有 pip 就 `pip install --user`，没有就在 `~/.local/bin/elench` 放启动器；
  只写用户目录与 shell 配置，**不需要 sudo**。

---

## [0.6.0] — 2026-11-01 · VS Code 扩展

### Added
- **`Elench: 安装命令行（加入 PATH）`**：把扩展自带的 `bin/` 写进**用户 PATH**
  （Windows 写 HKCU 的 Path；Linux/macOS 追加到 `~/.bashrc` / `~/.zshrc`），
  **重开终端后 `elench` 即可用**——扩展装完不再只会在 VS Code 里工作。
- **`Elench: 验证命令行`**：跑 `elench --version`，并列出 PATH 上所有同名命令（防旧版抢先）。
- 扩展自带启动器：`bin/elench.cmd`（CRLF、纯 ASCII、自动找 python/python3/py）与 `bin/elench`（bash）。

### Fixed
- 之前在别的电脑上装完 vsix，终端里 `elench` 报“找不到命令”——**因为扩展不会自动改 PATH**；
  现在有显式命令，并且只动**用户级** PATH（不需要管理员）。

---

## [0.5.0] — 2026-11-01 · VS Code 扩展

### Added
- **命令名统一为 `elench`**：新增 `src/elench.py` 作为正式入口（`dsh.py` 保留为兼容实现），
  打包脚本里的内置解释器同步改名。
- 新命令 **`Elench: 环境诊断`**：打印扩展路径、脚本路径、`python/python3/py` 的版本。
- **自动探测 Python**：依次试 `python` → `python3` → `py`，不用手填 `pythonPath`。
- 单个 `.tu` 或 `.eln` 也能检查（原来必须成对）。
- 输出面板强制 UTF-8（`PYTHONIOENCODING=utf-8`），避免 Windows 控制台乱码。

### Fixed
- `--version` 现在可用（`elench --version` → `elench 1.0.0`）。
- 扩展找不到脚本/Python 时，给出可操作的中文提示，而不是静默失败。

---

## [1.0.0] — 2026-11-01

**首个对外发布版**：代码 + 对外文档，历史干净（不含内部材料）。

### Added
- `文档/入门30分钟.md`：两文件模型、第一个契约、三个动词、常见错误与修法。
- `文档/语法清单.md`：一页速查（声明、类型、语句、`?!` 分档、退役写法、诊断码前缀）。
- `文档/内建函数.md`：100 个内建（91 个注册 + 9 个解释器直实现），由脚本生成。
- `文档/样例/`：10 个可运行样例。
- `tools/sync_public.py`：把当前树同步成对外仓库 `clean` 的一个新提交（不携带开发历史）。
- `editors/vscode/python/`：VS Code 扩展内置解释器（33 个模块），装好即可用，不必先 clone 仓库。

### Changed
- 对外仓库**不再包含** `文档/设计/`、`文档/其他/`、`文档/设计/归档/实验-2026-11/`、`文档/待办.md`（内部规划与申报材料）。
- README 的“从哪里读”与目录结构改为对外布局。
- 包版本 `0.69.0` → `1.0.0`。

### Fixed
- VS Code 扩展查找顺序：`elench.dshPath` → 工作区 `src/dsh.py` → **扩展自带** `python/dsh.py`。

### Removed
- `src/elench.egg-info/`（构建产物，本不该入库）。
- 根目录误留的 `临时计划-E4双值域.md`（内部材料，移入本地未跟踪区）。

---

## [0.71.0] — 2026-10-31

**语言表面与成本模型的收口**（预印本 `v1.0-preprint` 对应的语言版本）。

### Added
- 字符串字面量（编译期常量、零分配）、可变参数 `print`、效果检查（`E-Effect-Missing`）。
- `requires` 的调用点验证（长度、`is_sorted`、标量界）。
- `parallel` 语句（成本取 Σ）。
- 标准库扩到 **100 个内建**。
- `cost --segmented`（分档上界）、`external { … }`（具名未知）、分配精确判定。

### Fixed
- 成本解释器两处低估（循环迭代 13→14、方法调用 2→3）：修复后 **κ 只增不减**。

---

## [0.4.0] — 2026-11-01 · VS Code 扩展

### Added
- **内置解释器**随扩展打包：检查 / 算成本 / 构建 / 运行，装好即用。
- 命令 **`Elench: 运行当前程序`**（右键菜单第一项）。
- `editors/vscode/build-vsix.py`：纯标准库打 `.vsix`，不依赖 npm。

### Changed
- 版本 `0.3.0` → `0.4.0`；包体积 14 KB → 229 KB。

---

## [0.3.0] — 2026-10-30 · VS Code 扩展

### Added
- 语法高亮（`.eln` / `.tu`，TextMate）。
- 三个命令：检查（进 Problems）、显示成本上界、构建门禁。
- 保存时自动检查（`elench.checkOnSave`）。

---

## [0.1.0] — 2026-10-20

**首个公开版本**：两文件模型（`.tu` / `.eln`）、成本契约、`check` / `cost` / `build`、
解释器与测试。
