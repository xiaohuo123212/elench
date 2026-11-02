# Elench 的 VS Code 扩展：**高亮 + 把编译器接进来**

**支持两种文件**：`.eln`（实现）与 `.tu`（声明 + `target` / `choose`）。

## 装

```bash
cd editors/vscode
npx @vscode/vsce package        # 生成 elench-0.2.0.vsix
code --install-extension elench-0.2.0.vsix
```

**或者手动**：把整个目录复制到 `~/.vscode/extensions/elench-0.2.0/`。

## 高亮什么（不是"全部染个色"）

| 记号 | 为什么单独配色 |
|---|---|
| **`?!label(...)`** | 决策点：**承诺按最慢候选算、执行按表挑最快**——本语言最特殊的一个记号 |
| **`O(...)` / `log`** | 成本表达式里的量级原子，和普通函数调用要能一眼分开 |
| **`target` / `benchmodel` / `choose` / `over` / `objective`** | **上下文关键字**：只在 `spec` 体内有含义，词法上只是普通标识符 |
| **`end` / `fn` / `val` / `var`** | **已下线**，染成 deprecated——看见就知道不能再用 |
| **`// #region` / `// #endregion`** | 折叠标记，与 `sync_docs.py` 的 `#region` 同源 |

## 一条纪律：**关键词表不许和词法分析器分叉**

`src/test_editors.py` 会把 grammar 里的关键词与 `src/lexer.py` 的 `KEYWORDS` **逐字比对**：

```
python src/test_editors.py
```

- 词法器加了关键字、插件没加 → **测试失败**
- 插件多写了词法器没有的词 → **测试失败**

**这样插件就不会再出现"高亮认识的词，编译器不认识"这种情况。**


---

## 三个命令（不是只有高亮）

| 命令 | 背后跑什么 | 结果去哪 |
|---|---|---|
| **`Elench: 检查当前文件`** | `elench check` | **Problems 面板**（带行号列号与错误码） |
| **`Elench: 显示成本上界`** | `elench cost` | 输出面板（κ 表达式 + 是否达成） |
| **`Elench: 构建（门禁）`** | `elench build` | 失败就弹错误——**这是它的职责** |

**保存时自动检查**是可选的（默认开）。

## 设置

| 设置 | 默认 | 说明 |
|---|---|---|
| `elench.pythonPath` | `python` | 跑 `elench.py` 的 Python |
| `elench.dshPath` | 空 | 留空则在工作区里自动找 `src/elench.py` |
| `elench.checkOnSave` | `true` | 保存时自动 `elench check` |

## 为什么是"调子进程"而不是"内嵌一套逻辑"

> **契约只能有一个真相来源。**
> 编辑器里再实现一遍检查逻辑，就会和 CLI 慢慢分叉——
> **而这正是本项目要消灭的那种问题。**

## 装

```bash
cd editors/vscode
npx @vscode/vsce package      # 生成 elench-0.3.0.vsix
code --install-extension elench-0.3.0.vsix
```

**不打包也能用**：把整个目录复制到 `~/.vscode/extensions/elench-0.3.0/`。

## 测试（不需要开 VS Code）

```bash
node test/parse.test.js       # 诊断解析的单元测试（8 项）
```

**为什么单独测解析**：`elench check` 的输出格式如果有天变了，
**这个测试会先红**——而不是等到用户在编辑器里发现诊断跑到了错的行。

## 安装（拖动即可）

1. 拿到 `elench-0.4.0.vsix`；
2. 在 VS Code 里打开 **扩展** 视图（Ctrl+Shift+X）；
3. 把 `.vsix` **拖进去**（或点右上角 `...` → **Install from VSIX...**）；
4. 打开任意 `.tu` / `.eln`，右键：

| 命令 | 作用 |
|---|---|
| **Elench: 运行当前程序** | 直接 `run` |
| **Elench: 检查当前文件** | `check`，结果进 Problems |
| **Elench: 显示成本上界** | `cost`，输出面板看 κ |
| **Elench: 构建（门禁）** | `build`，失败就是真的失败 |
| **Elench: 环境诊断** | 打印 Python / 脚本路径 / 版本（排错用） |

**前置**：机器上有 **Python 3.10+**（扩展不捆绑 Python 运行时）。
扩展会**自动探测** `python` → `python3` → `py`，通常不用手动设置；
要强制指定就填 `elench.pythonPath`。
Windows 上若 `python` 不在 PATH，把设置 `elench.pythonPath` 改成 `py`。

## 让终端里也能用 `elench`

装扩展**不会**自动改 PATH（这是 VS Code 的规矩，不是我们的 bug）。装完做一次：

1. `Ctrl+Shift+P` → **`Elench: 安装命令行（加入 PATH）`**
2. **重开一个终端**（PowerShell / cmd / bash 都行）
3. 输入 `elench --version` → 应显示 `elench 1.0.0`

如果显示的是别的版本（例如 conda 里装过旧版），点 **`Elench: 验证命令行`**，
它会列出 PATH 上所有同名命令，把旧的那个删掉或调后即可。
