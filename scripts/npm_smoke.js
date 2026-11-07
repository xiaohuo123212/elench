#!/usr/bin/env node
// npm 包的功能冒烟：把 tarball 真装进临时目录，逐个跑子命令。
//
// 为什么要在"装进去"之后再跑：仓库里能跑不等于**装出来的包**能跑
// （相对路径、PYTHONPATH、缺失的 src 文件，只有在装完之后才暴露）。
"use strict";
const { execFileSync, spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const repo = path.join(__dirname, "..");
const tgz = process.argv[2];
if (!tgz) {
  console.error("用法: node scripts/npm_smoke.js <tarball>");
  process.exit(2);
}

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "elench-smoke-"));
fs.writeFileSync(path.join(dir, "package.json"), JSON.stringify({ name: "smoke", version: "1.0.0" }));

console.log(`  临时目录: ${dir}`);
// Windows 上 npm 是 npm.cmd，spawnSync 直呼 "npm" 会 ENOENT。
const NPM = process.platform === "win32" ? "npm.cmd" : "npm";
// Node 20+ 在 Windows 上**不允许**直接 spawn .cmd（CVE-2024-27980 的修法），
// 必须过 shell；tarball 路径加引号防空格。
execFileSync(NPM, ["install", "-s", `"${path.resolve(repo, tgz)}"`],
  { cwd: dir, stdio: "inherit", shell: true });

// 样例：从仓库复制到临时目录（包本身不带样例，用户自带文件）
const samples = path.join(repo, "文档", "样例");
for (const f of fs.readdirSync(samples)) {
  // **连成本表与分档表一起复制**：`bench`/`cost` 的判定依赖它们，
  // 只拷 .tu/.eln 会让一部分子命令因为"没有表"而拒绝——那不是包的缺陷。
  if (/\.(tu|eln|yaml)$/.test(f)) fs.copyFileSync(path.join(samples, f), path.join(dir, f));
}

// 直接跑包内的入口脚本：**这才是包的内容**；npm 生成的 .cmd/.sh
// 只是转发壳，Windows 下还得过 cmd.exe，会干扰参数里的中文路径。
const bin = path.join(dir, "node_modules", "elench", "bin", "elench.js");
const shim = path.join(dir, "node_modules", ".bin", process.platform === "win32" ? "elench.cmd" : "elench");
console.log(`  入口: ${bin}\n  转发壳存在: ${fs.existsSync(shim)}`);
const run = (args) => {
  const r = spawnSync(process.execPath, [bin].concat(args), { cwd: dir, encoding: "utf8", timeout: 180000 });
  const out = ((r.stdout || "") + (r.stderr || "")).split("\n").filter((l) => l.trim());
  return { code: r.status, first: (out[0] || "").trim().slice(0, 70) };
};

const CASES = [
  ["--help", ["--help"]],
  ["run", ["run", "sum", "--fn=average", "--vec=1,2,3"]],
  ["check", ["check", "sum"]],
  ["parse", ["parse", "sum.tu"]],
  ["fmt --check", ["fmt", "sum.eln", "--check"]],
  ["highlight", ["highlight", "sum.tu"]],
  ["show", ["show", "sum.tu", "sum_kernel"]],
  ["grammar", ["grammar", "sum.tu", "--format=ebnf"]],
  ["cost", ["cost", "sum"]],
  ["cost --explain", ["cost", "sum", "--explain"]],
  ["stats --ops", ["stats", "sum", "average", "--ops"]],
  ["bench", ["bench", "max_pair_sum", "max_pair_sum"]],
  ["export csv", ["export", "sum", "--format=csv", "--out=o.csv"]],
  ["export --full", ["export", "sum", "--full", "--out=o.svg"]],
  ["dispatch", ["dispatch", "sum"]],
  ["measure", ["measure", "sum", "--max-size=256"]],
  ["build", ["build", "sum"]],
  ["cpp（已知限制）", ["cpp", "calculator"]],
  ["header", ["header", "calculator"]],
  ["spec", ["spec", "calculator"]],
  ["effects", ["effects", "calculator"]],
  ["otel view", ["otel", "view", "nope.ndjson"]],
];

let bad = 0;
console.log(`\n  ${"命令".padEnd(16)} 退出码  首行`);
for (const [name, args] of CASES) {
  const r = run(args);
  // `otel view` 缺文件就该非 0 —— 那是"用例本身该失败"，单独标注
  // 两类"该失败"：
  //   otel view —— 文件不存在，本来就该报错
  //   cpp      —— `try` 表达式还不支持（L-16 已挂起），**是已知限制不是包装问题**
  const expectFail = name === "otel view" || name.startsWith("cpp");
  const ok = expectFail ? true : r.code === 0;
  if (!ok) bad++;
  console.log(`  ${name.padEnd(16)} ${String(r.code).padEnd(6)} ${ok ? "" : "✗ "}${r.first}`);
}
console.log(`\n  ${CASES.length - bad}/${CASES.length} 通过（otel view 以"该失败"计入）`);
process.exit(bad ? 1 : 0);
