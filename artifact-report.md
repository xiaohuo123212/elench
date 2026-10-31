# 一键复现报告

- 程序数：**10**
- `check` 通过：**10/10**
- `build` 通过：**7/10**（其余为 `pending` 拦截，属设计如此）
- 有债务（κ 超 target）：**1**

| 程序 | check | cost | build | 债务 |
|---|---|---|---|---|
| `calculator` | 0 | 0 | 通过 | 否 |
| `count` | 0 | 0 | 通过 | 否 |
| `lcs` | 0 | 0 | 通过 | 否 |
| `matmul` | 0 | 0 | 通过 | 否 |
| `merge` | 0 | 0 | 通过 | 否 |
| `paren` | 0 | 0 | 通过 | 否 |
| `search` | 0 | 0 | 缺分档表（先 measure） | 是 |
| `stats` | 0 | 0 | 缺分档表（先 measure） | 否 |
| `sum` | 0 | 0 | 通过 | 否 |
| `topk` | 0 | 0 | 缺分档表（先 measure） | 否 |

复现命令：`python tools/artifact_check.py`（零第三方依赖）
