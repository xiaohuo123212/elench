'use strict';
/**
 * Elench 的 VS Code 集成：**不只是高亮，而是把编译器接进来。**
 *
 * 三个命令都直接调 `dsh`（同一个真相来源，不在编辑器里重写一份逻辑）：
 *
 * - `Elench: 检查当前文件`  → `dsh check`，结果进 Problems 面板
 * - `Elench: 显示成本上界`  → `dsh cost`，结果进输出面板
 * - `Elench: 构建（门禁）`  → `dsh build`，失败就是真的失败
 *
 * **为什么用子进程而不是内嵌一套实现**：契约只能有一个真相来源。
 * 编辑器里再实现一遍，就会和 CLI 慢慢分叉——而这正是本项目要消灭的那种问题。
 */
const vscode = require('vscode');
const cp = require('child_process');
const path = require('path');
const fs = require('fs');
const { parseDiagnostics, isClean } = require('./lib/parse');

let out;
let diags;
let ctx;

function cfg() {
  const c = vscode.workspace.getConfiguration('elench');
  return {
    python: c.get('pythonPath', 'python'),
    dsh: c.get('dshPath', ''),
    checkOnSave: c.get('checkOnSave', true),
  };
}

function dshPath() {
  const { dsh } = cfg();
  if (dsh && fs.existsSync(dsh)) return dsh;
  const folders = vscode.workspace.workspaceFolders || [];
  for (const f of folders) {
    const p = path.join(f.uri.fsPath, 'src', 'dsh.py');
    if (fs.existsSync(p)) return p;
  }
  // **扩展自带一份**：拖进 VS Code 就能用，不必先 clone 仓库
  if (ctx) {
    const bundled = path.join(ctx.extensionPath, 'python', 'dsh.py');
    if (fs.existsSync(bundled)) return bundled;
  }
  return dsh || '';
}

/** 当前文件 → 一对 (声明, 实现)。`.tu` / `.eln` 同名互相配对。 */
function pairFor(editor) {
  if (!editor) return null;
  const file = editor.document.uri.fsPath;
  const ext = path.extname(file);
  if (ext !== '.eln' && ext !== '.tu') return null;
  const base = file.slice(0, -ext.length);
  const tu = base + '.tu';
  const eln = base + '.eln';
  if (!fs.existsSync(tu) || !fs.existsSync(eln)) return null;
  return { tu, eln };
}

function runDsh(args, label) {
  return new Promise((resolve) => {
    const script = dshPath();
    if (!script) {
      vscode.window.showErrorMessage('Elench：找不到 src/dsh.py，请在设置里填 elench.dshPath');
      resolve({ code: -1, text: '' });
      return;
    }
    const { python } = cfg();
    out.show(true);
    out.appendLine(`$ ${python} ${script} ${args.join(' ')}   (${label})`);
    cp.execFile(python, [script, ...args], { cwd: path.dirname(script), maxBuffer: 8 << 20 },
      (err, stdout, stderr) => {
        const text = `${stdout || ''}${stderr || ''}`;
        out.appendLine(text.trimEnd() || '（无输出）');
        resolve({ code: err ? (err.code || 1) : 0, text });
      });
  });
}

async function check(editor, silent) {
  const pr = pairFor(editor || vscode.window.activeTextEditor);
  if (!pr) {
    if (!silent) vscode.window.showWarningMessage('Elench：当前文件需要同名的 .tu 与 .eln 才能检查');
    return;
  }
  const { text } = await runDsh(['check', pr.tu, pr.eln], 'check');
  const ds = parseDiagnostics(text);
  for (const target of [pr.tu, pr.eln]) {
    const list = ds.map((d) => {
      const r = new vscode.Range(d.line - 1, d.col - 1, d.line - 1, d.col);
      const diag = new vscode.Diagnostic(r, d.message,
        d.severity === 'warning' ? vscode.DiagnosticSeverity.Warning : vscode.DiagnosticSeverity.Error);
      diag.code = d.code;
      diag.source = 'elench';
      return diag;
    });
    diags.set(vscode.Uri.file(target), list);
  }
  if (!silent) {
    if (isClean(text)) vscode.window.showInformationMessage('Elench：检查通过');
    else vscode.window.showWarningMessage(`Elench：${ds.length} 条诊断（见 Problems）`);
  }
}

function activate(context) {
  out = vscode.window.createOutputChannel('Elench');
  diags = vscode.languages.createDiagnosticCollection('elench');
  context.subscriptions.push(out, diags);

  context.subscriptions.push(
    vscode.commands.registerCommand('elench.check', () => check(null, false)),
    vscode.commands.registerCommand('elench.cost', async () => {
      const pr = pairFor(vscode.window.activeTextEditor);
      if (!pr) return vscode.window.showWarningMessage('Elench：当前文件需要同名的 .tu 与 .eln');
      await runDsh(['cost', pr.tu, pr.eln], 'cost');
    }),
    vscode.commands.registerCommand('elench.build', async () => {
      const pr = pairFor(vscode.window.activeTextEditor);
      if (!pr) return vscode.window.showWarningMessage('Elench：当前文件需要同名的 .tu 与 .eln');
      const { code } = await runDsh(['build', pr.tu, pr.eln], 'build');
      if (code === 0) vscode.window.showInformationMessage('Elench：门禁通过');
      else vscode.window.showErrorMessage('Elench：门禁未通过（这是它的职责）');
    }),
  );

  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument(() => {
      if (cfg().checkOnSave) check(null, true);
    }),
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
