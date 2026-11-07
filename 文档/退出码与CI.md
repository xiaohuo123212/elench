# 退出码与 CI 用法

> 2026-11-06（L-12）。**本表每一行都是跑出来的**，不是照代码猜的。
> 复现命令见每节末尾。

## 退出码

| 值 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | **检查/生成失败**（诊断里有 error） |
| `2` | **用法错误**（参数不对、文件找不到、未知子命令） |

## 各子命令（实测）

| 子命令 | 成功 | 语法错 | 备注 |
|---|---|---|---|
| `check` | 0 | **1** | |
| `cost` | 0 | **1** | 2026-11-06 前是 **2**，已改为与 check/build 一致 |
| `cpp` | 0 | **1** | 2026-11-06 前是 **0**，会把坏代码当成功 |
| `build` | 0 | **1** | |
| `run` | 0 | **1** | |
| `fmt` | 0 | **1** | 2026-11-06 前是 **0**，报"无变化" |
| （无参数） | — | **2** | |
| （未知子命令） | — | **2** | |

### 已修：`cost` 曾用 2 表示解析失败

2026-11-06 前，`cost` 遇到解析失败返回 **2**，而同类的 `check`/`build` 返回 **1**；
而 2 在本表里约定为"用法错误"，所以那是**语义错位**。

现已改为 **1**。`cost` 仍有两个返回 2 的地方，但都是**真正的用法错误**：
缺 `.tu` 文件、缺实现文件。

## CI 样例

```yaml
name: elench
on: [push, pull_request]
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }

      # 1) 静态检查：诊断就是失败
      - name: check
        run: python src/dsh.py check 文档/样例

      # 2) 成本契约：--deny-debt 让"超出 target"变成失败
      - name: cost gate
        run: python src/dsh.py build 文档/样例 --deny-debt

      # 3) 格式：有改动就失败（不自动改文件）
      - name: fmt
        run: python src/dsh.py fmt 文档/样例 --check
```

**为什么第 2 步用 `build` 而不是 `cost`**：只有 `build` 会**拒绝**（exit 1）；
`cost` 只打印表格，不因债务失败。

## 怎么复现这张表

```bash
# 好程序 → 0
python src/dsh.py check 文档/样例/sum
# 语法错 → 1
printf 'func h() -> u64 { return (1 + ; }\n' > /tmp/syn.eln
python src/dsh.py fmt /tmp/syn.eln; echo "exit=$?"
# 用法错 → 2
python src/dsh.py check; echo "exit=$?"
```
