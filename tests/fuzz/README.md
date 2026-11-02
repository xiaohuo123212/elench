# tests/fuzz —— κ 的机械化 soundness 检测器

一句话：**随机生成能编译的程序，跑出真实操作数 `ops`，和编译器给的 `κ` 比；
`ops > κ` 就是反例。**

## 用法

```bash
python tests/fuzz/run.py                 # 200 个程序
python tests/fuzz/run.py --n 2000        # 大规模
python tests/fuzz/run.py --n 200 --shrink   # 反例自动缩到最小
python tests/fuzz/gen.py 8               # 只看生成器吐什么
```

## 三个零件

| 文件 | 作用 |
|---|---|
| `gen.py` | **生成器**：四个程序族 `loop` / `minmax` / `hof` / `rec`，随机拼源码 |
| `check.py` | **裁判**：`ops` vs `κ`，统计覆盖率与「测不出来」（不许算通过） |
| `shrink.py` | **缩小器**：整行删除，只要还是反例就删 |
| `run.py` | 唯一入口（转发到 `check`） |

反例落盘到 `tests/corpus/CE-####.eln`，最小复现直接可读。

## 为什么生成源码而不是 AST

1. AST 直拼要在生成器里再抄一份 `kinds.py` 的真值表——那正是漂移来源；
2. 能否编译由**真实 parser** 判定，不是生成器自说自话；
3. 反例源码本身就是最小复现，缩小器只需删行。

## 已知边界（写在 `文档/待办.md` §G）

- **共享盲点**：解释器计数错了两侧一起错，检测器抓不到；
- 只测小 `n`；非终止靠预算；符号 κ 求成 inf 的跳过；
- 「测不出来」**单独计数**——它不算通过（这一条是 v2 之后加的铁律）。

## 当前状态

- 13 个构造覆盖：`while` / `for_in` / `if` / `if_else` / `minmax` / `call` /
  `array` / `cast` / `div` / `compound` / `hof` / `rec` / **`dispatch`**
- `hof`（函数值传参）与 `dispatch`（`?!` + choose + kernels）都已可测：
  前者靠 `k(visit)` 代入，后者用"候选取 max 的 κ"对照实际跑的那个候选
- 当前 `--n 40`：编译 40、反例 0、**测不出来 0**
- `dispatch`（`?!`）族未做：仓库里没有任何 `?!` 样例，语法不能靠猜。
