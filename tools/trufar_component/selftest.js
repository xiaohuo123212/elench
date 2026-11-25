const fs = require("fs");
const path = require("path");
const dir = "tools/trufar_component";
const read = (f) => fs.readFileSync(path.join(dir, f), "utf8");

// --- 极简 DOM 桩：只实现组件用到的那几个成员 ---
function makeDom() {
  const roles = {};
  const el = (role) => {
    if (!roles[role]) roles[role] = { role, innerHTML: "", textContent: "", hidden: false };
    return roles[role];
  };
  ["badge","fns","heat","diags","empty","foot"].forEach(el);
  const classes = new Set();
  return {
    _roles: roles,
    querySelector: (sel) => {
      const m = /data-role="([^"]+)"/.exec(sel);
      return m ? el(m[1]) : null;
    },
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      toggle: (c, on) => { on ? classes.add(c) : classes.delete(c); },
      contains: (c) => classes.has(c),
    },
    get offsetWidth() { return 100; },
    _classes: classes,
  };
}

const html = read("01-html.html");
const mountSrc = read("02-onMount.js");
const updateSrc = read("03-onUpdate.js");
const unmountSrc = read("04-onUnmount.js");

function runMount(dom, props) {
  new Function("dom","value","onChange","props", mountSrc)(dom, null, () => {}, props);
}
function runUpdate(dom, data, prevData, props) {
  new Function("dom","data","prevData","props", updateSrc)(dom, data, prevData, props);
}

const DEBT = { elench: { ok: true, op: "cost", verdict: "debt", exit_code: 0, elapsed_ms: 118,
  functions: { sum_all: { kappa: "2 + n(xs)×(2 + n(xs)×7) + 2", verdict: "debt",
    explanation: "**超标**：阶不够", allocation: "0", allocation_verdict: "ok",
    allocation_explanation: "达成：阶够" } },
  diagnostics: [{ code: "E-Cost-OverTarget", line: 3, col: 5, message: "上界不够", fix: "改成一次遍历" }],
  lines: [{n:2,hits:1},{n:3,hits:4096},{n:4,hits:16773120},{n:5,hits:4096}] } };

const OK = { elench: { ok: true, op: "cost", verdict: "ok", exit_code: 0, elapsed_ms: 90,
  functions: { sum_all: { kappa: "2 + n(xs)×7 + 2", verdict: "ok", explanation: "达成：阶够",
    allocation: "0", allocation_verdict: "ok", allocation_explanation: "达成：阶够" } },
  diagnostics: [] } };

const ERR = { elench: { ok: false, op: "cost", error: "tu 与 eln 至少要给一个" } };

function assert(c, m) { if (!c) { console.log("  FAIL " + m); process.exitCode = 1; } else console.log("  ok   " + m); }

// 1) 挂载、无数据
let dom = makeDom();
runMount(dom, {});
assert(dom._roles.empty.hidden === false, "无数据时显示空状态");
assert(/等待工作流/.test(dom._roles.foot.textContent), "页脚提示等待数据");

// 2) onUpdate 收到违约结果
runUpdate(dom, DEBT, {}, {});
const badge = dom._roles.badge;
assert(/债务/.test(badge.textContent), "判决徽章显示「债务」");
assert(badge.className.includes("debt"), "徽章带 debt 样式");
assert(/n\(xs\)×\(2 \+ n\(xs\)×7\)/.test(dom._roles.fns.innerHTML), "κ 表达式渲染出来");
assert(/E-Cost-OverTarget/.test(dom._roles.diags.innerHTML), "诊断码渲染出来");
assert(/改成一次遍历/.test(dom._roles.diags.innerHTML), "诊断的改法渲染出来");
assert((dom._roles.heat.innerHTML.match(/<i /g) || []).length === 4, "逐行热度画了 4 根柱");
assert(dom._roles.heat.hidden === false, "有 lines 时热度条可见");
assert(dom._classes.has("ec-bad"), "变差时卡片进入 bad 状态");

// 3) 再更新成达成 —— 不应残留上一次的诊断
runUpdate(dom, OK, DEBT, {});
assert(/达成/.test(dom._roles.badge.textContent), "判决变回「达成」");
assert(dom._roles.diags.innerHTML === "", "诊断列表被清空（不残留旧结果）");
assert(dom._roles.heat.hidden === true, "没有 lines 时热度条隐藏");
assert(!dom._classes.has("ec-bad"), "bad 状态被清掉");

// 4) 工具调用失败 —— 与"代码不达标"必须区分
runUpdate(dom, ERR, OK, {});
assert(/调用失败/.test(dom._roles.badge.textContent), "工具失败显示「调用失败」而不是「债务」");
assert(/至少要给一个/.test(dom._roles.fns.innerHTML), "把 error 原文显示出来");

// 5) 结果消失 —— 必须说清"已作废"，不能留旧数字
runUpdate(dom, { elench: null }, ERR, {});
assert(/没有拿到/.test(dom._roles.fns.innerHTML), "结果消失时明说不再显示旧结果");

// 6) 卸载（平台只给 props）
new Function("props", unmountSrc)({});
assert(true, "onUnmount 在拿不到 dom 时也不抛错");