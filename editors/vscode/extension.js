'use strict';
/**
 * Elench 的 VS Code 集成：**不只是高亮，而是把编译器接进来。**
 *
 * 命令都直接调 `elench.py`（同一个真相来源，不在编辑器里重写一份逻辑）：
 *
 * - `Elench: 运行当前程序`  → `elench run`
 * - `Elench: 检查当前文件`  → `elench check`，结果进 Problems
 * - `Elench: 显示成本上界`  → `elench cost`，结果进输出面板
 * - `Elench: 构建（门禁）`  → `elench build`，失败就是真的失败
 * - `Elench: 环境诊断`      → 打印 Python / 脚本路径与版本（排错用）
 *
 * **为什么用子进程而不是内嵌一套实现**：契约只能有一个真相来源。
 */
const vscode = require('vscode');
const cp = require('child_process');
const path = require('path');
const fs = require('fs');
const { parseDiagnostics } = require('./lib/parse');

let out;
let diags;
let ctx;
let chosenPython = null;

function cfg() {
  const c = vscode.workspace.getConfiguration('elench');
  return {
    python: c.get('pythonPath', ''),
    dsh: c.get('dshPath', ''),
    checkOnSave: c.get('checkOnSave', true),
  };
}

/** 依次试 `python`、`python3`、`py`，返回第一个能跑起来的。 */
function pickPython() {
  const { python } = cfg();
  if (python) return python;
  if (chosenPython) return chosenPython;
  for (const cand of ['python', 'python3', 'py']) {
    try {
      cp.execFileSync(cand, ['--version'], { stdio: 'ignore', timeout: 5000 });
      chosenPython = cand;
      return cand;
    } catch (e) { /* 试下一个 */ }
  }
  return '';
}

/**
 * 找解释器脚本，优先级：
 *   1. 用户显式设置 `elench.dshPath`
 *   2. 当前工作区里的 `src/elench.py`（开发本仓库时）
 *   3. **扩展自带的 `python/elench.py`**（拖进来就能用）
 * 旧名字 `dsh.py` 仍兼容。
 */
function scriptPath() {
  const { dsh } = cfg();
  if (dsh && fs.existsSync(dsh)) return dsh;
  const names = ['elench.py', 'dsh.py'];
  const roots = [];
  for (const f of vscode.workspace.workspaceFolders || []) {
    roots.push(path.join(f.uri.fsPath, 'src'));
  }
  if (ctx) roots.push(path.join(ctx.extensionPath, 'python'));
  for (const root of roots) {
    for (const n of names) {
      const p = path.join(root, n);
      if (fs.existsSync(p)) return p;
    }
  }
  return dsh || '';
}

/** 当前文件 → 要传给 CLI 的参数。成对用一对；只有单个也能检查。 */
function argsFor(editor) {
  if (!editor) return null;
  const file = editor.document.uri.fsPath;
  const ext = path.extname(file);
  if (ext !== '.eln' && ext !== '.tu') return null;
  const base = file.slice(0, -ext.length);
  const tu = base + '.tu';
  const eln = base + '.eln';
  if (fs.existsSync(tu) && fs.existsSync(eln)) return [tu, eln];
  if (fs.existsSync(tu)) return [tu];
  if (fs.existsSync(eln)) return [eln];
  return [file];
}

function runDsh(args, label, opts) {
  const silent = opts && opts.silent;
  return new Promise((resolve) => {
    const script = scriptPath();
    if (!script) {
      if (!silent) {
        vscode.window.showErrorMessage(
          'Elench：找不到 elench.py。请重新安装扩展，或在设置里填 elench.dshPath');
      }
      resolve({ code: -1, text: '' });
      return;
    }
    const python = pickPython();
    if (!python) {
      if (!silent) {
        vscode.window
          .showErrorMessage('Elench：找不到 Python 3.10+。装好后点「环境诊断」查看。', '环境诊断')
          .then((pick) => {
            if (pick) vscode.commands.executeCommand('elench.diagnose');
          });
      }
      resolve({ code: -1, text: '' });
      return;
    }
    if (!out) out = vscode.window.createOutputChannel('Elench');
    out.show(true);
    out.appendLine(`$ ${python} ${script} ${args.join(' ')}   (${label})`);
    const env = Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' });
    cp.execFile(python, [script, ...args], { cwd: path.dirname(script), env, maxBuffer: 16 << 20 },
      (err, stdout, stderr) => {
        const text = `${stdout || ''}${stderr || ''}`;
        out.appendLine(text.trimEnd() || '（无输出）');
        resolve({ code: err ? (err.code || 1) : 0, text });
      });
  });
}

async function check(editor, silent) {
  const args = argsFor(editor || vscode.window.activeTextEditor);
  if (!args) {
    if (!silent) vscode.window.showWarningMessage('Elench：请打开一个 .tu 或 .eln 文件');
    return;
  }
  const { text } = await runDsh(['check', ...args], 'check', { silent });
  const ds = parseDiagnostics(text);
  diags.clear();
  const list = ds.map((d) => {
    const r = new vscode.Range(d.line - 1, d.col - 1, d.line - 1, d.col);
    const diag = new vscode.Diagnostic(r, d.message,
      d.severity === 'warning' ? vscode.DiagnosticSeverity.Warning : vscode.DiagnosticSeverity.Error);
    diag.code = d.code;
    diag.source = 'elench';
    return diag;
  });
  diags.set(vscode.Uri.file(args[0]), list);
  if (!silent) {
    if (ds.length === 0) vscode.window.showInformationMessage('Elench：检查完成（无结构化诊断）');
    else vscode.window.showWarningMessage(`Elench：${ds.length} 条诊断（见 Problems）`);
  }
}

/** 稳定的安装目录：**不带版本号**，扩展升级后仍然有效。 */
function stableDir() {
  const base = process.platform === 'win32'
    ? (process.env.LOCALAPPDATA || path.join(process.env.USERPROFILE || '.', 'AppData', 'Local'))
    : (process.env.XDG_DATA_HOME || path.join(process.env.HOME || '.', '.local', 'share'));
  return path.join(base, 'Elench');
}

/** 把扩展自带的 python/ 与 bin/ 复制到稳定目录，并加进用户 PATH。 */
function installCli() {
  const srcPy = path.join(ctx.extensionPath, 'python');
  const srcBin = path.join(ctx.extensionPath, 'bin');
  if (!fs.existsSync(srcPy) || !fs.existsSync(srcBin)) {
    vscode.window.showErrorMessage('Elench：扩展里缺 python/ 或 bin/，请重新安装扩展');
    return;
  }
  const dest = stableDir();
  const destPy = path.join(dest, 'python');
  const destBin = path.join(dest, 'bin');
  try {
    fs.rmSync(dest, { recursive: true, force: true });
    fs.mkdirSync(destBin, { recursive: true });
    fs.cpSync(srcPy, destPy, { recursive: true });
    fs.cpSync(srcBin, destBin, { recursive: true });
  } catch (e) {
    vscode.window.showErrorMessage(`Elench：复制到 ${dest} 失败：${e.message}`);
    return;
  }

  const binPath = destBin;
  if (process.platform === 'win32') {
    const q = binPath.replace(/'/g, "''");
    const ps = [
      "$p=[Environment]::GetEnvironmentVariable('Path','User')",
      "if($null -eq $p){$p=''}",
      "$parts=$p.Split(';') | Where-Object { $_ -and ($_ -notlike '*.vscode\\extensions\\elench*') }",
      `if($parts -notcontains '${q}'){$parts += '${q}'}`,
      "[Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')",
    ].join('; ');
    cp.execFile('powershell', ['-NoProfile', '-Command', ps], { timeout: 30000 }, (err) => {
      if (err) {
        vscode.window.showErrorMessage(`Elench：写入 PATH 失败：${err.message}`);
        return;
      }
      verifyAt(destBin, binPath);
    });
  } else {
    const rcFiles = ['.bashrc', '.zshrc', '.profile']
      .map((f) => path.join(process.env.HOME || '', f))
      .filter((f) => fs.existsSync(f));
    const line = `\nexport PATH="${binPath}:$PATH"  # elench\n`;
    for (const f of rcFiles) {
      const cur = fs.readFileSync(f, 'utf8');
      if (!cur.includes(binPath)) fs.appendFileSync(f, line);
    }
    verifyAt(destBin, binPath);
  }
}

/** 装完立刻用稳定目录的启动器自检一次。 */
function verifyAt(destBin, binPath) {
  const exe = process.platform === 'win32' ? path.join(destBin, 'elench.cmd') : path.join(destBin, 'elench');
  try {
    const v = cp.execFileSync(exe, ['--version'], { encoding: 'utf8', timeout: 20000 });
    vscode.window.showInformationMessage(
      `Elench：命令行已安装到 ${binPath}（${String(v).trim()}）。**关掉当前终端、重新打开**，再输入 elench。`);
  } catch (e) {
    vscode.window.showErrorMessage(`Elench：已复制文件，但自检失败：${e.message}`);
  }
}

async function verifyCli() {
  const bin = path.join(ctx.extensionPath, 'bin');
  const exe = process.platform === 'win32' ? path.join(bin, 'elench.cmd') : path.join(bin, 'elench');
  if (!fs.existsSync(exe)) {
    vscode.window.showErrorMessage(`Elench：启动器不存在：${exe}`);
    return;
  }
  try {
    const v = cp.execFileSync(exe, ['--version'], { encoding: 'utf8', timeout: 15000 });
    // 同时报告 PATH 上的同名冲突（conda/pip 装过旧版时最容易踩）
    const cands = [];
    for (const dir of (process.env.PATH || '').split(path.delimiter)) {
      for (const n of (process.platform === 'win32' ? ['elench.cmd', 'elench.exe', 'elench.bat'] : ['elench'])) {
        const c = path.join(dir, n);
        try { if (fs.existsSync(c)) cands.push(c); } catch (e) { /* ignore */ }
      }
    }
    const extra = cands.length ? `\nPATH 上其它同名命令：\n${cands.join('\n')}` : '';
    vscode.window.showInformationMessage(`Elench 命令行可用：${String(v).trim()}${extra}`);
  } catch (e) {
    vscode.window.showErrorMessage(`Elench 命令行不可用：${e.message}`);
  }
}

function activate(context) {
  ctx = context;
  out = vscode.window.createOutputChannel('Elench');
  diags = vscode.languages.createDiagnosticCollection('elench');
  context.subscriptions.push(out, diags);

  const needArgs = (fn) => async () => {
    const args = argsFor(vscode.window.activeTextEditor);
    if (!args) {
      vscode.window.showWarningMessage('Elench：请打开一个 .tu 或 .eln 文件');
      return;
    }
    await fn(args);
  };

  context.subscriptions.push(
    vscode.commands.registerCommand('elench.run', needArgs(async (a) => {
      await runDsh(['run', ...a], 'run');
    })),
    vscode.commands.registerCommand('elench.check', () => check(null, false)),
    vscode.commands.registerCommand('elench.cost', needArgs(async (a) => {
      await runDsh(['cost', ...a], 'cost');
    })),
    vscode.commands.registerCommand('elench.build', needArgs(async (a) => {
      const { code } = await runDsh(['build', ...a], 'build');
      if (code === 0) vscode.window.showInformationMessage('Elench：门禁通过');
      else vscode.window.showErrorMessage('Elench：门禁未通过（这是它的职责）');
    })),
    vscode.commands.registerCommand('elench.installCli', () => installCli()),
    vscode.commands.registerCommand('elench.verifyCli', () => verifyCli()),
    vscode.commands.registerCommand('elench.diagnose', async () => {
      if (!out) out = vscode.window.createOutputChannel('Elench');
      out.show(true);
      out.appendLine('=== Elench 环境诊断 ===');
      out.appendLine(`扩展路径: ${ctx ? ctx.extensionPath : '(未知)'}`);
      out.appendLine(`脚本路径: ${scriptPath() || '(找不到)'}`);
      const p = pickPython();
      out.appendLine(`Python: ${p || '(找不到)'}`);
      for (const cand of ['python', 'python3', 'py']) {
        try {
          const v = cp.execFileSync(cand, ['--version'], { encoding: 'utf8' });
          out.appendLine(`  ${cand}: ${String(v).trim()}`);
        } catch (e) {
          out.appendLine(`  ${cand}: 不可用`);
        }
      }
      const script = scriptPath();
      if (script && p) {
        try {
          const v = cp.execFileSync(p, [script, '--version'], { encoding: 'utf8' });
          out.appendLine(`elench --version: ${String(v).trim()}`);
        } catch (e) {
          out.appendLine(`elench --version: 失败（${e.message}）`);
        }
      }
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
