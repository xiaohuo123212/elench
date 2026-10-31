import io
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import stdlib

RULES = [
    ("文本与 UTF-8", r"^(text_|utf8_|contains$|index_of$|slice$|split_|format)"),
    ("向量", r"^(vec_|copy|copy_slice|fill|fill_range|reverse|rotate|dedup|sum_of|count_of|is_sorted)"),
    ("排序 / 搜索 / 聚合", r"^(sort|binary_search|argmin|argmax|min|max|count_if|fold|filter|map$)"),
    ("映射表", r"^(map_|hmap_)"),
    ("集合", r"^(set_)"),
    ("双端队列", r"^(deque_)"),
    ("Option / Result", r"^(option_|ok$|err$)"),
    ("JSON", r"^json_"),
    ("编码与解析", r"^(base64_|parse_)"),
    ("文件与 IO", r"^(open_|read_|write|append_|close|next_line|file_|try_read|cwd$|fs_)"),
    ("环境与进程", r"^(env_|args|exit$|rand_|print$)"),
]

# 9 个由解释器直接实现的内建（不在 stdlib.FNS 注册表里）
SPECIAL = {
    "print": ("1", "u64", "1 + n(args)"),
    "sort": ("1", "u64", "n log n"),
    "sort_by": ("2", "u64", "n log n × k(f)"),
    "map": ("2", "any", "n × k(f)"),
    "filter": ("2", "any", "n × k(f)"),
    "fold": ("3", "any", "n × k(f)"),
    "count_if": ("2", "u64", "n × k(f)"),
    "min": ("1", "u64", "n"),
    "max": ("1", "u64", "n"),
}


def cat(name):
    for label, pat in RULES:
        if re.match(pat, name):
            return label
    return "其他"


groups = {}
for n in sorted(stdlib.FNS):
    d = stdlib.FNS[n]
    groups.setdefault(cat(n), []).append((n, d.get("arity"), d.get("ret"), d.get("cost"), ""))

for label, (a, r, c) in SPECIAL.items():
    groups.setdefault(cat(label), []).append((label, a, r, c, "（解释器直接实现）"))

OUT = """# 语言内建函数清单（100 个）

> 由 `src/stdlib.py` 与解释器自动导出，**不要手改这张表**——改代码后重新生成。
> 生成方式：`python tools/gen_builtins_doc.py`（本文件即其输出）。
>
> **口径**：**100 个内建 = 91 个注册在 `stdlib.FNS` + 9 个由解释器直接实现**（`print` / `sort` /
> `sort_by` / `map` / `filter` / `fold` / `count_if` / `min` / `max`）。

## 怎么读这张表

| 列 | 含义 |
|---|---|
| **参数个数** | 调用时**必须**给几个实参（我们的内建不支持可选参数） |
| **返回** | `any` 表示返回类型随输入（如 `map_get`）；`result-*` / `option-*` 是带错误/缺失的包装 |
| **成本类** | `1` 常数；`n` 线性；`nm` 两参数相乘；`log` 对数；`nret` 线性于返回值；`ext` **外部**（未知或由登记表替换） |

**两条硬规则**：

1. **`ext` 不会被当作 0**——它会在 κ 里保留为 `ext(name)`，并在报告里打印。
2. **分配量按次数精确计数**（不是渐近），`allocation` 是**精确判定**。

"""

for label in [r[0] for r in RULES]:
    if label not in groups:
        continue
    rows = groups[label]
    OUT += f"## {label}（{len(rows)}）\n\n"
    OUT += "| 函数 | 参数个数 | 返回 | 成本类 | 备注 |\n|---|---|---|---|---|\n"
    for n, a, r, c, note in rows:
        OUT += f"| `{n}` | {a} | `{r}` | {c} | {note} |\n"
    OUT += "\n"

OUT += """## 没有内建、必须自己写的东西

| 想要 | 怎么做 |
|---|---|
| 浮点运算 | **不提供**（成本模型是操作数计数，浮点会让上界不可比） |
| 正则 | **不提供**（可用 `text_find` / `text_split` 组合，成本可算） |
| 网络 | **不提供**（属于 `ext`；用 `external` 声明并登记） |
| 线程池 | 用 `parallel { … }` 块，成本取各任务之和 |
| 排序的比较器 | `sort_by(xs, f)`，`k(f)` 是函数调用的成本符号 |

## 相关文档

- 语法：[语法清单](语法清单.md)、[语法速查](语法速查.md)
- 入门：[入门](入门.md)
- 成本模型：[AI语言设计规格](AI语言设计规格.md) 第 4 章
"""

path = os.path.join(ROOT,
                    "文档", "设计", "目前设计", "内建函数.md")
io.open(path, "w", encoding="utf-8", newline="\n").write(OUT)
print("  已生成 内建函数.md：", sum(len(v) for v in groups.values()), "条")
