'use strict';
/**
 * 把 `dsh check` 的文本输出解析成结构化诊断。
 *
 * CLI 的真实格式（一行一条）：
 *   ✗ E-Error-DeclMissing @1:1  函数 f 用 `T!` 表示可能失败，但模块没有 `error` 声明
 *
 * 为什么要单独一个模块：**这样它能被单元测试覆盖**，
 * 而不是"只能在 VS Code 里点一下才知道对不对"。
 */

const DIAG = /[✗✔✓]\s+([EW]-[A-Za-z0-9_-]+)\s+@(\d+):(\d+)\s+(.*)$/;

function parseDiagnostics(text) {
  const out = [];
  if (!text) return out;
  for (const raw of String(text).split(/\r?\n/)) {
    const line = raw.replace(/\s+$/, '');
    const m = DIAG.exec(line);
    if (!m) continue;
    const code = m[1];
    out.push({
      code,
      line: Math.max(1, parseInt(m[2], 10)),
      col: Math.max(1, parseInt(m[3], 10)),
      message: m[4].trim(),
      severity: code.startsWith('W-') ? 'warning' : 'error',
    });
  }
  return out;
}

/** `dsh check` 是否报告了"全部通过"。 */
function isClean(text) {
  const s = String(text || '');
  if (/✗\s*\d+\s*条错误/.test(s)) return false;
  return /✓\s*通过/.test(s);
}

module.exports = { parseDiagnostics, isClean };
