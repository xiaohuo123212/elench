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
- 对外仓库**不再包含** `文档/设计/`、`文档/其他/`、`文档/实验/`、`文档/待办.md`（内部规划与申报材料）。
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
