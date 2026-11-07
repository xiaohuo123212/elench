#!/usr/bin/env node
// 打包前清掉 Python 缓存与编译产物。
//
// 为什么要这一步：package.json 里用 `files` 白名单时 npm **会忽略 .npmignore**，
// 于是 src/__pycache__ 里的 .pyc 会被整包带走（实测 67 个文件、~1.3MB）。
"use strict";
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
let removed = 0;

function walk(dir) {
  let ents;
  try {
    ents = fs.readdirSync(dir, { withFileTypes: true });
  } catch (_e) {
    return;
  }
  for (const e of ents) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name === "__pycache__") {
        fs.rmSync(p, { recursive: true, force: true });
        removed++;
      } else if (e.name !== "node_modules" && e.name !== ".git") {
        walk(p);
      }
    } else if (/\.py[co]$/.test(e.name)) {
      fs.rmSync(p, { force: true });
      removed++;
    }
  }
}

walk(path.join(root, "src"));
console.log(`prepack: 清理了 ${removed} 个缓存/产物`);
