'use strict';
// 纯 node 跑的单元测试（不依赖 VS Code）：node test/parse.test.js
const assert = require('assert');
const { parseDiagnostics, isClean } = require('../lib/parse');

const SAMPLE = [
  '✗ 2 条错误',
  '  ✗ E-Error-DeclMissing @1:1  函数 f 用 `T!` 表示可能失败，但模块没有 `error` 声明',
  "  ✗ E-Parse-ExpectedExpr @2:13  [.eln] 期望表达式，实际是 ';'",
  'module bad',
  '',
].join('\n');

const ds = parseDiagnostics(SAMPLE);
assert.strictEqual(ds.length, 2, '应解析出 2 条诊断');
assert.strictEqual(ds[0].code, 'E-Error-DeclMissing');
assert.strictEqual(ds[0].line, 1);
assert.strictEqual(ds[0].col, 1);
assert.strictEqual(ds[0].severity, 'error');
assert.ok(ds[0].message.includes('error'), '消息应被保留');
assert.strictEqual(ds[1].line, 2);
assert.strictEqual(ds[1].col, 13);

assert.strictEqual(isClean('✗ 1 条错误\n  ✗ E-X @1:1 boom'), false);
assert.strictEqual(isClean('module sum\n\n  ✓ average  κ ≤ 10\n✓ 通过（1 个函数）'), true);
assert.strictEqual(isClean(''), false);
assert.deepStrictEqual(parseDiagnostics(''), []);

console.log('  OK parse.test.js：8 项断言通过');
