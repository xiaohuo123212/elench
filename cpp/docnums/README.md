# docnums（C++17）—— 文档数字绑定检查器

> **同一个工具的两个实现。** Python 版是 [../../../tools/doc_numbers.py](../../../tools/doc_numbers.py)，
> 这里是 C++17 版：单文件、零第三方依赖，用于**没有 Python** 的环境。

## 它解决什么

> 一个数字只要能被**手抄**，它就会**过期**。

文档里的数绑定到它的来源，来源一变就报错：

```markdown
n=16 处 9 个有效函数合计 κ **27,555,185<!--dn:kappa_total_n16-->
```

`<!--dn:key-->` 是行内 HTML 注释，Markdown 渲染时不可见。
`key` 指向 [../../../tools/doc_numbers.json](../../../tools/doc_numbers.json)` 里的来源。

| type | 含义 |
|---|---|
| `field` | JSON 的点号路径（`rows.0.kappa_at_n`） |
| `sum` | `path[*].field` 求和 |
| `count` | 列表长度 |
| `const` | 字面量 |
| `audit` | 调 `tools/audit_numbers.py --json` 取字段（**这一项需要 Python**） |

## 用法

```bash
docnums --list                 # 有哪些绑定、当前值、来源
docnums --check [文档...]       # 校验（CI）；不一致 → 退出 1
docnums --scan  文档...         # 零标注模式：找没有来源的数字
docnums --check --root <仓库根> # 从别处跑
```

退出码：`0` 一致 / `1` 有不一致 / `2` 用法或环境错误。

## 构建

```bash
# 方式一：CMake
cmake -S . -B build && cmake --build build
ctest --test-dir build --output-on-failure

# 方式二：make（MinGW / MSYS / Linux）
make && make test
```

要求：C++17 编译器（g++ ≥ 8 / clang++ ≥ 7 / MSVC ≥ 2019）。**没有第三方库。**

## 与 Python 版的一致性

```bash
python parity.py build/docnums          # --list / --check 逐行对照
python parity.py build/docnums --full   # 再加 --scan
```

**为什么要两个实现**：不是炫技——**检查器自己也得被检查**。语义只有一份
（同一份清单、同一套标记），两个独立实现跑同一批文档，输出必须一致；
不一致就说明其中一个是错的，而这正是这个工具要防的那类问题。

## 已知差异（有意为之）

| 项 | Python 版 | C++ 版 |
|---|---|---|
| `audit` 类型 | 直接 import 计算 | **shell out** 到 `python tools/audit_numbers.py --json`（可用 `DOCNUMS_PYTHON` 指定解释器） |
| `--scan` 的正则边界 | 用 lookaround | 手写边界判断（`std::regex` 的 ECMAScript 方言不支持 lookaround） |
| 错误信息措辞 | 同 | 同（parity 测试逐行比对） |
