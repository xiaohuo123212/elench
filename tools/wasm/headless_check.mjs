// headless_check.mjs —— 用本机 Edge/Chrome 的无头模式**真的跑一遍** probe.html。
//
// 为什么需要它：Pyodide 在浏览器里到底能不能跑到 `import core_api` 并算对 κ，
// 只有浏览器能回答。人肉点一次当然也行，但那样每次改都要人记得点；
// 这个脚本把它变成一条命令，而且用的是**页面里同一条代码路径**（`window.__probe`）。
//
// 用法（需要先起服务器）：
//     python -B tools/wasm/probe.py --no-open --port 18101
//     node tools/wasm/headless_check.mjs http://127.0.0.1:18101/tools/wasm/probe.html
//
// 做法：起浏览器（--headless=new --remote-debugging-port=0）→ 读 DevTools 的
// webSocketDebuggerUrl → 用 Node 内置的 WebSocket 发 CDP 命令 →
// Runtime.evaluate 里 await window.__probe.boot() 与 .run()。
// 不装 puppeteer/playwright，只用 Node 标准能力。

import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const URL_ = process.argv[2] || "http://127.0.0.1:18101/tools/wasm/probe.html";
// 页面暴露的钩子名。探针是 __probe，工作台是 __site —— 两者形状相同
// （boot / run(tu,eln)），所以这一个脚本能验两个页面。
const HOOK = process.argv[3] || "__probe";
// 可选：--shot=out.png 截一张图（写文档/汇报用）
const SHOT = (process.argv.slice(2).find((a) => a.startsWith("--shot=")) || "").slice(7) || null;
// 可选：--dom 打印渲染出来的 DOM 摘要（证明"页面真的画出来了"，而不只是函数返回了值）
const SHOW_DOM = process.argv.slice(2).includes("--dom");
const WAIT_MS = Number(process.env.PROBE_WAIT_MS || 600000);

const CANDIDATES = [
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function log(msg) { console.log("  " + msg); }

async function findBrowser() {
  for (const p of CANDIDATES) if (existsSync(p)) return p;
  throw new Error("找不到 Edge/Chrome（试过：" + CANDIDATES.length + " 个路径）");
}

async function httpJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error("HTTP " + r.status + " " + url);
  return await r.json();
}

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); }
  static async connect(wsUrl) {
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => {
      ws.onopen = res;
      ws.onerror = () => rej(new Error("WebSocket 连接失败：" + wsUrl));
    });
    const c = new CDP(ws);
    ws.onmessage = (ev) => {
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m.id && c.pending.has(m.id)) {
        const { res, rej } = c.pending.get(m.id);
        c.pending.delete(m.id);
        m.error ? rej(new Error(m.error.message)) : res(m.result);
      }
    };
    return c;
  }
  send(method, params) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      this.ws.send(JSON.stringify({ id, method, params: params || {} }));
    });
  }
  async eval(expr, awaitPromise = true) {
    const r = await this.send("Runtime.evaluate", {
      expression: expr, awaitPromise, returnByValue: true, timeout: WAIT_MS,
    });
    if (r.exceptionDetails) {
      const d = r.exceptionDetails;
      throw new Error((d.exception && d.exception.description) || d.text || "页面内异常");
    }
    return r.result.value;
  }
  close() { try { this.ws.close(); } catch {} }
}

async function main() {
  const exe = await findBrowser();
  log("浏览器：" + exe);

  const profile = mkdtempSync(join(tmpdir(), "elench-probe-"));
  const port = 9300 + Math.floor(Math.random() * 500);
  const proc = spawn(exe, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--remote-debugging-port=" + port, "--user-data-dir=" + profile,
    "--allow-insecure-localhost", "about:blank",
  ], { stdio: "ignore" });

  let cdp;
  const cleanup = () => {
    try { cdp && cdp.close(); } catch {}
    try { proc.kill(); } catch {}
    try { rmSync(profile, { recursive: true, force: true }); } catch {}
  };

  try {
    // 等 DevTools 端点起来
    let targets = null;
    for (let i = 0; i < 60; i++) {
      try {
        targets = await httpJson("http://127.0.0.1:" + port + "/json/list");
        if (targets && targets.length) break;
      } catch { /* 还没起来 */ }
      await sleep(250);
    }
    if (!targets || !targets.length) throw new Error("浏览器没起来（DevTools 端口无响应）");

    const page = targets.find((t) => t.type === "page") || targets[0];
    cdp = await CDP.connect(page.webSocketDebuggerUrl);
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");

    log("打开页面：" + URL_);
    await cdp.send("Page.navigate", { url: URL_ });
    // 等 __probe 钩子出现（页面脚本执行完）
    for (let i = 0; i < 120; i++) {
      const has = await cdp.eval("!!(globalThis[" + JSON.stringify(HOOK) + "])", false);
      if (has) break;
      await sleep(250);
    }

    // 可选：--initial 只看"页面自己首屏跑成什么样"（不替它设任何输入）。
    // 这条验的是**第一印象**：打开页面就应该是「C++ + 契约 → 达成」。
    if (process.argv.slice(2).includes("--initial")) {
      for (let i = 0; i < 240; i++) {
        const pill = await cdp.eval('(document.getElementById("runtime")||{}).textContent||""', false);
        if (pill.indexOf("就绪") >= 0) break;
        await sleep(250);
      }
      await sleep(600);                      // 让自动跑的那一次渲染完
      const snap = await cdp.eval(`JSON.stringify({
        compat: (document.getElementById("compat")||{}).checked,
        tuFirst: (document.getElementById("tu").value||"").split(String.fromCharCode(10))[0],
        elnFirst: (document.getElementById("eln").value||"").split(String.fromCharCode(10))[0],
        badges: [...document.querySelectorAll(".badge")].map(b => b.textContent),
        kappa: [...document.querySelectorAll(".kappa")].map(k => k.textContent.trim()),
        pill: (document.getElementById("runtime")||{}).textContent||""
      })`);
      const s = JSON.parse(snap);
      log("首屏：compat=" + s.compat + " · 状态条「" + s.pill + "」");
      log("首屏 .tu 首行：" + s.tuFirst);
      log("首屏 .eln 首行：" + s.elnFirst);
      log("首屏 徽章 " + JSON.stringify(s.badges) + " · κ " + JSON.stringify(s.kappa));
      const bad = [];
      if (!s.compat) bad.push("C++ 模式默认没开");
      if (s.badges[0] !== "达成") bad.push("首屏判决不是「达成」，而是 " + s.badges[0]);
      if (!s.kappa.length || s.kappa[0].indexOf("n(xs)") < 0) bad.push("首屏没有 κ 表达式");
      if (bad.length) { for (const b of bad) console.log("  FAIL " + b); process.exitCode = 1; }
      else console.log("  ✓ 首屏就是「C++ + 契约 → 达成」");
      return;
    }

    log("boot()（加载运行时 + 灌 src + import core_api）…");
    const t0 = Date.now();
    const boot = await cdp.eval("globalThis[" + JSON.stringify(HOOK) + "].boot()");
    const bootMs = Date.now() - t0;
    log("boot 完成：" + bootMs + " ms · Pyodide " + boot.pyodide + " · " + boot.modules + " 个模块");

    const TU = "spec f(const vector<u64>& xs) -> u64 {\n    effects {};\n    target { time: { max: O(n) }, allocation: { max: 0 }, };\n}\n";
    const OK = "u64 f(const vector<u64>& xs) {\n    u64 s = 0;\n    for (auto x : xs) { s = s + x; }\n    return s;\n}\n";
    const BAD = "u64 f(const vector<u64>& xs) {\n    u64 s = 0;\n    for (auto x : xs) { for (auto y : xs) { s = s + y; } }\n    return s;\n}\n";

    log("run(合规)…");
    const a = await cdp.eval(`globalThis[${JSON.stringify(HOOK)}].run(${JSON.stringify(TU)}, ${JSON.stringify(OK)})`);
    if (!a || a.ok === false) {
      // 把页面自己的日志抓回来：Pyodide 里的 traceback 才是真正要看的
      const pageLog = await cdp.eval("document.getElementById('log').textContent");
      console.log("");
      log("页面日志：");
      for (const ln of String(pageLog || "").trim().split("\n").slice(-14)) console.log("      " + ln);
      if (a && a.traceback) {
        log("浏览器里的 traceback：");
        for (const ln of String(a.traceback).trim().split("\n")) console.log("      " + ln);
      }
      throw new Error("浏览器里算失败：" + ((a && a.error) || "run 返回 null"));
    }
    log("run(违约)…");
    const b = await cdp.eval(`globalThis[${JSON.stringify(HOOK)}].run(${JSON.stringify(TU)}, ${JSON.stringify(BAD)})`);
    if (!b || b.ok === false) throw new Error("违约用例失败：" + ((b && b.error) || "null"));

    const ka = a.payload.functions.f.cost_expression;
    const va = a.payload.functions.f.verdict;
    const kb = b.payload.functions.f.cost_expression;
    const vb = b.payload.functions.f.verdict;

    log("合规：κ = " + ka + "   verdict = " + va);
    log("违约：κ = " + kb + "   verdict = " + vb);
    log("算一次耗时：" + a.ms.toFixed(1) + " ms / " + b.ms.toFixed(1) + " ms");

    const fails = [];
    if (va !== "ok") fails.push("合规代码应判 ok，实际 " + va);
    if (ka !== "2 + n(xs)×7 + 2") fails.push("合规 κ 应为 2 + n(xs)×7 + 2，实际 " + ka);
    if (vb !== "debt") fails.push("违约代码应判 debt，实际 " + vb);
    if (kb !== "2 + n(xs)×(2 + n(xs)×7) + 2") fails.push("违约 κ 形状不对：" + kb);

    // 可选：--compat 额外验一遍「真 C++ 源码不改一个字」
    if (process.argv.slice(2).includes("--compat")) {
      log("run(C++ 原样, compat)…");
      const cpp = [
        "#include <vector>",
        "#include <cstdint>",
        "using namespace std;",
        "",
        "uint64_t sum_all(const vector<uint64_t>& xs) {",
        "    uint64_t s = 0;",
        "    for (size_t i = 0; i < xs.size(); i++) {",
        "        s += xs[i];",
        "    }",
        "    return s;",
        "}",
        ""
      ].join("\n");
      await cdp.eval(`globalThis[${JSON.stringify(HOOK)}].compat(true)`);
      const c = await cdp.eval(`globalThis[${JSON.stringify(HOOK)}].run(${JSON.stringify("")}, ${JSON.stringify(cpp)})`);
      await cdp.eval(`globalThis[${JSON.stringify(HOOK)}].compat(false)`);
      if (!c || c.ok === false) throw new Error("C++ 原样用例失败：" + ((c && c.error) || "null"));
      const fn = (c.payload.functions || {}).sum_all || {};
      log("C++ 原样：κ = " + fn.cost_expression + "   verdict = " + fn.verdict);
      if (!fn.cost_expression) { console.log("  FAIL C++ 原样没有算出 κ"); process.exitCode = 1; }
    }

    if (SHOW_DOM) {
      const dom = await cdp.eval(`JSON.stringify({
        cards: document.querySelectorAll(".card").length,
        badges: [...document.querySelectorAll(".badge")].map(b => b.textContent),
        kappa: [...document.querySelectorAll(".kappa")].map(k => k.textContent.trim()),
        diags: document.querySelectorAll(".diag").length,
        pill: (document.getElementById("runtime") || {}).textContent || "",
        timing: (document.getElementById("timing") || {}).textContent || "",
        textLen: (document.getElementById("out") || {}).innerHTML.length || 0
      })`);
      const d = JSON.parse(dom);
      log("DOM：卡片 " + d.cards + " · 徽章 " + JSON.stringify(d.badges) +
          " · κ " + JSON.stringify(d.kappa) + " · 诊断 " + d.diags);
      log("DOM：状态条「" + d.pill + "」· 耗时「" + d.timing + "」· 结果区 " + d.textLen + " 字符");
    }

    if (SHOT) {
      // 固定视口再截图，避免默认 800x600 把页面挤成一条
      await cdp.send("Emulation.setDeviceMetricsOverride",
                     { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
      const shot = await cdp.send("Page.captureScreenshot", { format: "png" });
      const { writeFileSync } = await import("node:fs");
      writeFileSync(SHOT, Buffer.from(shot.data, "base64"));
      log("截图已保存：" + SHOT);
    }

    console.log("");
    if (fails.length) {
      for (const f of fails) console.log("  FAIL " + f);
      process.exitCode = 1;
    } else {
      console.log("  ✓ 浏览器里的 Elench 算出的 κ 与命令行一致");
    }
  } finally {
    cleanup();
  }
}

main().catch((e) => {
  console.error("  ✗ " + (e && e.message ? e.message : e));
  process.exitCode = 1;
});
