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

// **2026-11-14**：`src/` 下曾躺着 7 个未入库的陈旧副本（parser.py.bak 等，共 213KB），
// 因为 `files: ["src/"]` 是整目录，它们被一起发进了 npm 包 ——
// 任何人 `npm i elench-lang` 都会拿到 7 份**过期的 parser / stdlib / typecheck 源码**，
// 与真正的源码同名、内容更旧。比"漏发一个文件"危险得多。
// 这里在打包前把它们挪走（不删源码树的其它东西，只处理 *.bak），并在 stderr 说一声。
const BAK_EXT = ".bak";
function scrubBak(dir) {
  let ents;
  try { ents = fs.readdirSync(dir, { withFileTypes: true }); } catch (_e) { return; }
  for (const e of ents) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) { scrubBak(p); continue; }
    if (e.name.endsWith(BAK_EXT)) {
      console.error("prepack: 移出陈旧副本（不随包发布）：" + path.relative(root, p));
      fs.rmSync(p, { force: true });
      removed++;
    }
  }
}

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
scrubBak(path.join(root, "src"));
console.log(`prepack: 清理了 ${removed} 个缓存/产物`);
