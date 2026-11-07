#!/usr/bin/env node
// elench 的 npm 入口：找 python，把参数原样转给它。
//
// 为什么是"薄包装"而不是重写：elench 的核心是 Python（约 3.5MB 源码、19 个子命令）。
// 重写成 JS 等于重做一遍，而且会立刻出现两套语义。
// 这里只做三件事：找解释器、传环境、原样转发退出码。
"use strict";

const { spawnSync } = require("child_process");
const path = require("path");
const fs = require("fs");

const pkgRoot = path.join(__dirname, "..");
const srcDir = path.join(pkgRoot, "src");
const dshPy = path.join(srcDir, "dsh.py");

if (!fs.existsSync(dshPy)) {
  console.error(`elench: 包内缺少 src/dsh.py（安装可能不完整）：${dshPy}`);
  process.exit(1);
}

const candidates = [];
if (process.env.PYTHON) candidates.push(process.env.PYTHON);
candidates.push("python3", "python");

function probe(exe) {
  const r = spawnSync(exe, ["-c", "import sys;sys.exit(0 if sys.version_info>=(3,10) else 9)"], {
    stdio: "ignore",
  });
  return r.status === 0;
}

let py = null;
for (const c of candidates) {
  try {
    if (probe(c)) {
      py = c;
      break;
    }
  } catch (_e) {
    /* 试下一个 */
  }
}

if (!py) {
  console.error(
    "elench: 找不到可用的 Python（需要 >= 3.10）。\n" +
      "  已尝试：" + candidates.join(", ") + "\n" +
      "  装好 Python 后重试，或用 PYTHON=/path/to/python 指定。"
  );
  process.exit(1);
}

const env = Object.assign({}, process.env, {
  PYTHONIOENCODING: "utf-8",
  PYTHONPATH: [srcDir, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
});

const r = spawnSync(py, [dshPy].concat(process.argv.slice(2)), {
  stdio: "inherit",
  env,
  cwd: process.cwd(),
});

if (r.error) {
  console.error(`elench: 启动 Python 失败：${r.error.message}`);
  process.exit(1);
}
process.exit(r.status === null ? 1 : r.status);
