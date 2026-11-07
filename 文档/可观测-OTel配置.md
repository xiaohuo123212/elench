# 用户怎么配 OTel（一步一步照做）

> **一句话**：**装一个 collector（一条 docker 命令）→ 设三个环境变量 → 跑 `elench export --push`。**
> 不需要装 SDK、不需要改我们代码、不需要 Python 依赖。

---

## 第 0 步：先确认你有 Docker（或者已有 collector）

```powershell
docker --version
```

没有 Docker 也行——只要有一个能收 **OTLP/HTTP** 的端点（Jaeger、Grafana Alloy、Tempo、云厂商的 OTLP 网关都可以）。

---

## 第 1 步：起一个 collector（本机，30 秒）

`otel-collector-config.yaml`（放在任意目录）：

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318      # ← OTLP/HTTP 的端口是 4318，不是 4317
exporters:
  debug:
    verbosity: detailed
  # 想存起来看，换成下面任意一个：
  # prometheus: { endpoint: "0.0.0.0:8889" }
  # otlphttp/tempo: { endpoint: "http://tempo:4318" }
service:
  pipelines:
    metrics: { receivers: [otlp], exporters: [debug] }
    traces:  { receivers: [otlp], exporters: [debug] }
    logs:    { receivers: [otlp], exporters: [debug] }
```

```powershell
docker run --rm -p 4318:4318 -p 8889:8889 `
  -v "${PWD}\otel-collector-config.yaml:/etc/otelcol/config.yaml" `
  otel/opentelemetry-collector:latest
```

**看到 `Everything is ready. Begin running and processing data.` 就对了。**

---

## 第 2 步：设环境变量（PowerShell）

```powershell
$env:OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4318"
$env:OTEL_SERVICE_NAME           = "elench"
$env:ELENCH_OTEL_LOGS            = "1"     # 可选：把 E-* 诊断也推过去
$env:ELENCH_OTEL_TRACES          = "1"     # 可选：build/measure/?! 打 span
```

**cmd 里**：

```bat
set OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
set OTEL_SERVICE_NAME=elench
```

**永久生效**（Windows）：

```powershell
[Environment]::SetEnvironmentVariable("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318", "User")
```

---

## 第 3 步：推一次

```powershell
cd 文档\样例
elench export count --push
```

**期望输出**：

```
  OTel metrics → http://localhost:4318/v1/metrics  {'sent': True, 'status': 200, ...}
```

**collector 那边**会打出 `ResourceMetrics` / `ScopeMetrics` / `Metrics` 的 debug 日志。

---

## 第 4 步：确认三件事（常见坑）

| 现象 | 原因 | 处理 |
|---|---|---|
| `{'sent': False, 'reason': 'no metrics endpoint'}` | 环境变量没设 / 新开的窗口没继承 | 重开终端再试 |
| 连不上、超时 | **端口写成 4317**（那是 gRPC） | 改 **4318** |
| 收集端归到 `unknown_service` | 没设 `OTEL_SERVICE_NAME`，或端点用了 `_METRICS_` 专用变量但只配了通用变量 | 设 `OTEL_SERVICE_NAME` |
| 不想让 CI 推 | `OTEL_SDK_DISABLED=true` | 一个字节都不会发 |

**自检一条命令**（不发数据，只打印解析后的配置）：

```powershell
python -c "import sys; sys.path.insert(0,'src'); import otel_export as o; print(o.config())"
```

---

## 第 5 步：面板怎么看（**这一步最容易看错**）

**κ 是上界，不是观测值。** 所以面板**必须**按 `cost.semantics` 分序列：

| cost.semantics | 是什么 | 面板上怎么放 |
|---|---|---|
| `upper_bound` | 承诺（最劣候选） | 上方那条线 |
| `candidate_bound` | 分段最优 | 中间那条 |
| `measured` | 实测 | 下方那条（**看它和 candidate_bound 是否平行**） |

**如果把它们混成一条序列，看到的会是一个没有意义的平均值。**

---

## 我们支持的全部环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 通用端点（自动补 `/v1/metrics`、`/v1/traces`、`/v1/logs`） | 空（不推） |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | metrics 专用（优先于通用） | 空 |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | traces 专用 | 空 |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT` | logs 专用 | 空 |
| `OTEL_SERVICE_NAME` | `service.name` | `elench` |
| `OTEL_SERVICE_VERSION` | `service.version` | `0.70.0` |
| `OTEL_SDK_DISABLED` | `true` → 完全不发 | `false` |
| `ELENCH_OTEL_TRACES` | `1` → `build`/`measure`/`?!` 打 span | 关 |
| `ELENCH_OTEL_LOGS` | `1` → `E-*` 诊断推成 logs | 关 |
| `ELENCH_OTEL_RETRIES` | 失败重试次数（5xx/连接失败才重试） | `3` |
| `ELENCH_OTEL_TIMEOUT` | 秒 | `5` |

---

## 我们**不做**的三件事（提前说清）

1. **不装 SDK**：零第三方依赖是项目承诺，OTLP/HTTP+JSON 用标准库发即可；
2. **不发 gRPC/protobuf**：collector 两种都收，JSON 够用；
3. **不替你配 collector**：上面那段 yaml 是模板，存储后端（Prometheus/Tempo/云）你自己选。

---

# 附：**不想装 Docker** —— 用我们自带的本机接收端

配 OTel 的现实门槛不是"发数据"，是"**得先有个东西收**"。
所以仓库里带了一个 200 行的小接收端（标准库，零依赖）：

```powershell
python tools/otel_local_sink.py --out otel.ndjson
```

输出：

```
OTLP sink 监听 http://127.0.0.1:4318
  /v1/metrics  /v1/traces  /v1/logs
  NDJSON → C:\...\otel.ndjson
```

另开一个终端：

```powershell
$env:OTEL_EXPORTER_OTLP_ENDPOINT = "http://localhost:4318"
$env:OTEL_SERVICE_NAME = "elench"
$env:ELENCH_OTEL_TRACES = "1"
cd 文档\样例
elench export count --push
```

接收端会实时打印摘要：

```
  [00:20:31] metrics  service=elench metrics=2 points=26 [elench.cost.nanoseconds, elench.cost.operations]
  [00:20:31] traces   spans=1 [export]
```

**它做三件事**：收 → 打印一行摘要 → 追加写 NDJSON（可 diff、可当 CI 工件）。

**它不是生产 collector**：不存储、不转发、不做背压。**只用来确认"我们发出去的东西对不对"。**

---

# 附：如果你觉得 OTel 太抽象（**这个问题问得对**）

OTel 复杂，不是因为它难，是因为它的目标是**让所有厂商的数据能互换**——
于是它必须有 resource / scope / metric / datapoint / temporality / exemplar 这一整套模型。
**你只想画两条线，却要先理解一个数据交换标准。**

按"你要什么"选，不必都用 OTel：

| 你的目的 | 更轻的替代 | 代价 |
|---|---|---|
| 本机看一眼数字 | **`--format=csv` / `--format=table`** | 不出图 |
| 出个图放进论文 | **`--format=svg`** | 不联网、不更新 |
| CI 里比对两版 | **CSV + git diff** | 没有可视化 |
| 已有 Prometheus/Grafana | **Prometheus 文本**（我们没做，但半天能加） | 不是 OTel |
| 要 traces（看哪步慢） | OTel **必需**，没有更轻的标准 | —— |
| 给云厂商/企业接入 | OTel **必需**（它是事实标准） | —— |

**我的判断**：

> **我们真正需要 OTel 的地方只有一处：`traces`（看 build/measure/?! 哪一步慢）。**
> **metrics 用 CSV/SVG 已经够——OTel 的 metrics 是给"要接统一可观测性平台"的客户准备的，不是给自己看图的。**

---

# 附：**可视化**（三条路，按重量排）

| 路线 | 一条命令 | 适合 |
|---|---|---|
| **① 静态图（最轻）** | `elench export count --out count.svg` | 论文/汇报，一张图定稿 |
| **② 本机看板（推荐）** | `python tools\otel_dashboard.py --file otel.ndjson --port 8080` | 边跑边看，自动刷新 |
| ③ Grafana | 装 Prometheus/Tempo + 配数据源 | 团队长期看板 |

## ② 本机看板的用法

```bat
:: 终端 A：接数据
python tools\otel_local_sink.py --out otel.ndjson

:: 终端 B：发数据
set OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
set ELENCH_OTEL_TRACES=1
elench export count --push
elench measure sum

:: 终端 C：看看板
python tools\otel_dashboard.py --file otel.ndjson --port 8080
:: 浏览器打开 http://127.0.0.1:8080
```

**看板上有三样东西**：

1. **成本曲线**（双对数，三条线）：承诺（蓝）/ κ分段（橙）/ 实测（红）；
2. **最慢的 span**：按耗时降序，一眼看出哪个 n、哪个候选最慢；
3. 三个计数卡片：收到多少 metrics / traces / logs 批次。

**页面每 3 秒自动刷新**——`elench measure` 还在跑的时候就能看到 span 一条条冒出来。
