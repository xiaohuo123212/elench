# demo/probe-libs —— 手写的"库风格"探针（**不是**真库）

> 这 9 个目录里是**手写的、库风格的 C++ 程序**，用来问一个问题：
> **一个像库一样写出来的 C++ 文件，能不能 0 改动被 Elench 吃下去？**

## 别和真库探针搞混

| | 这里 `demo/probe-libs/` | 真库探针 |
|---|---|---|
是什么 | 手写的库风格程序（本目录） | **真实第三方库**：cJSON / doctest / fmt / inih / nlohmann-json / stb / tinyexpr / utf8h / xxHash |
在哪 | 入库（一次提交 `cf77cfe`，之后未改） | `build/real-libs/`，**gitignored**（只在本机） |
结论写在哪 | 本文档 | [文档/真库探针.md](../../文档/真库探针.md) |

两套都测过，**结论不能互相替代**。

## 这 9 个是什么

| 目录 | 行数 | 大致内容 |
|---|---|---|
`bitvec` | 384 | 位向量 |
`iniparse` | 248 | INI 解析 |
`lexer` | 297 | 词法扫描器 |
`matrix3` | 266 | 3×3 矩阵 |
`pool` | 76 | 对象池 |
`quantity` | 441 | 带单位的量（类型包装） |
`ring` | 85 | 环形缓冲 |
`skiplist` | 117 | 跳表 |
`strbuf` | 206 | 字符串缓冲 |

## 怎么用

```bash
# 单个：原样复制成 .eln（内容一个字不改），加 --compat
cp demo/probe-libs/ring/main.cpp /tmp/ring.eln
python -B src/dsh.py check /tmp/ring.eln --compat
```

**为什么要复制到别处**：这些文件是**输入证据**，不许原地改名或改动；
`.eln` 后缀只是"告诉工具按实现文件解析"。

## 当前结论（2026-11-23 实测）

一条命令重跑（**不修改任何原件**，脚本会复制到临时目录再跑）：

```bash
python -B tools/probe_real_libs.py --path demo/probe-libs
```

```
✗ bitvec.eln      E-Const-Mutate, E-While-NoBound
✗ iniparse.eln    E-Call-UnknownMethod, E-Parse-Expected, …
✗ lexer.eln       E-Ai-HasDecl, E-Cast-Unsupported, …
✗ matrix3.eln     E-Ai-HasDecl, E-Call-Undeclared, E-Parse-…
✗ pool.eln        E-Ai-HasDecl, E-Compat-PrintfFormat, …
✗ quantity.eln    E-Ai-HasDecl, E-Const-Mutate
✓ ring.eln
✗ skiplist.eln    E-Ai-HasDecl, E-Keyword-Unsupported, …
✗ strbuf.eln      E-Cast-Unsupported, E-While-NoBound
口径：移植（内联 include）
check --compat 通过：1/9
```

### 这 8 个卡在哪（按诊断码归类）

| 诊断码 | 库 | 是什么 |
|---|---|---|
`E-Ai-HasDecl` | lexer / matrix3 / pool / quantity / skiplist | **本语言规定 `.eln`（实现文件）里不许有声明**（`F-4`/`F-8`：类型、`const` 常量这些要放 `.tu`）。真库代码天然带声明 → 这是**设计上的硬边界**，不是兼容层能修的 |
`E-While-NoBound` | bitvec / strbuf | `while` 条件推不出圈数（**警告级**，不阻断） |
`E-Const-Mutate` | bitvec / quantity | 在 `const` 形参上调用了会改写的方法 |
`E-Cast-Unsupported` | lexer / strbuf | `static_cast<T>` 的形状不在第 11 条白名单里 |
`E-Keyword-Unsupported` | lexer / skiplist | `static` 局部变量 |
`E-Call-UnknownMethod` / `E-Call-Undeclared` | iniparse / matrix3 | 迭代器 / 未声明外部调用 |
`E-Compat-PrintfFormat` | pool | `printf` 格式串形状 |

**`1/9` 不是"兼容层不行"**，至少 5 个是 `E-Ai-HasDecl` —— 那是本语言**故意**不让实现文件
带声明。想 0 改动跑这类库，需要把声明挪到 `.tu`，那就不叫 0 改动了。

## 与真库探针的两张表

| | `demo/probe-libs/`（本目录） | 真库探针 |
|---|---|---|
通过数 | **1/9** | 移植口径 9/47 · FFI 口径 20/47 |
细节 | 本文 | [文档/真库探针.md](../../文档/真库探针.md) |
