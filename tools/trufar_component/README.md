# Elench × 初发「自定义代码」组件

> 这个目录是**给平台直接粘贴的**四段代码 + 一个本地预览页。
> 组件本身**不计算**——计算在**工作流**那一步已经做完了（调 MCP 工具），
> 组件只负责把结果画出来。这是这个平台唯一能走通的分工，原因见下。

---

## 0. 为什么是"工作流算、组件画"

平台的「自定义代码」组件只给到这些：

```js
onMount(dom, value, onChange, props)      // 挂载
onUpdate(dom, data, prevData, props)      // 页面数据变了
onUnmount(props)                          // 卸载
```

**没有网络访问的前提、也没有调沙箱的桥**——它是个纯浏览器组件。
所以正确的分工是：

```
① 用户在工作流里提交 .tu / .eln
② 工作流调 MCP 工具  elench_cost / elench_check      ← 真正算 κ 的是这里
③ 工具返回 JSON，工作流把它写进一个变量（比如 result）
④ 页面数据变化 → 组件 onUpdate(dom, data, prevData)  ← 组件只拿 data 渲染
```

**好处**：HTML 里没有一行计算逻辑，所以不会出现"页面算一套、命令行算另一套"的漂移。
数字只有一个来源。

---

## 1. 数据契约（工作流要传什么）

组件读 `data.elench`（找不到就看 `props.elench`）。就是 MCP `tools/call` 的
`structuredContent` 原样，一字不改：

```json
{
  "elench": {
    "ok": true,
    "op": "cost",
    "verdict": "debt",
    "functions": {
      "sum_all": {
        "kappa": "2 + n(xs)×(2 + n(xs)×7) + 2",
        "verdict": "debt",
        "explanation": "**超标**：阶不够，比值 ≈ 7696581394434.00",
        "allocation": "0",
        "allocation_verdict": "ok",
        "allocation_explanation": "达成：阶够"
      }
    },
    "diagnostics": [
      {"code": "E-Parse-UnexpectedTop", "line": 1, "col": 1,
       "severity": "error", "message": "这里不该出现 'ident'", "fix": null}
    ],
    "exit_code": 0,
    "elapsed_ms": 111
  }
}
```

**可选**：如果 `data.elench.lines` 存在（`[{"n": 3, "hits": 42}, …]`），组件会多画一条
**逐行热度条**。没有就不画，不报错——因为那需要工作流额外跑一次带 trace 的调用。

### 三种"没有数据"的状态，组件都要能说清

| 情况 | 组件显示 |
|---|---|
| `data.elench` 不存在 | 「还没有结果：先在工作流里调 elench_cost」 |
| `elench.ok === false` | 红色：工具没跑成（`error` 字段的原文） |
| `elench.verdict === "debt"` | 橙色：**代码不达标**，但工具是好的 |

这三个**不能混**：把「代码不达标」显示成「组件坏了」，用户会去查错地方。

---

## 2. 四段代码怎么填

| 平台字段 | 贴哪个文件 |
|---|---|
| 将显示的 HTML 内容 | `01-html.html` |
| 挂载事件响应（onMount） | `02-onMount.js` |
| 页面更新事件响应（onUpdate） | `03-onUpdate.js` |
| 取消挂载事件响应（onUnmount） | `04-onUnmount.js` |

四段都是**零外链**（无 CDN、无字体、无图片），符合内嵌场景。

---

## 3. 本地怎么先看效果（不用等平台）

打开 `preview.html`：

- 它自己实现了一个极简"平台"：定义 `dom` / `value` / `onChange`，
  然后按顺序调 `onMount` → 给你一个文本框，改 JSON 再点「触发 onUpdate」
- 能验证的：渲染对不对、三种空数据状态、`prevData` 动画、`onUnmount` 清理
- 不能验证的：平台真实的数据注入时机（那个只能上平台试）

```bash
# 直接双击打开就行，不需要服务器
start tools/trufar_component/preview.html      # Windows
```

---

## 4. 动画只做两件有意义的事

组件里的"动画"不是装饰，只标两件事：

1. **判决变差**（`ok` → `debt`，或任何 → `unknown`）：卡片边框闪红，
   并显示"比上次变差了"
2. **κ 变了**：κ 那一行淡入一次

这两件事都靠 `onUpdate` 的 `prevData` 对比，不需要额外数据。
刻意**不**做"庆祝动画"那类效果——这个组件要表达的是"你的成本契约过没过"，
花哨会盖掉结论。

---

## 5. 已知边界（写在这里，也写在页面上）

| 做不了 | 为什么 |
|---|---|
| 在页面里现场算 κ | 组件是纯浏览器 JS，没有解释器；重写一遍 JS 版 κ 会产生**第二套数字**，必然漂移 |
| 逐行单步动画（trace） | 需要工作流把 trace 也传进来；数据量可能很大，暂不做 |
| 调我们的 HTTP / MCP | 组件没有网络访问通道，`fetch` 到不了沙箱 |
