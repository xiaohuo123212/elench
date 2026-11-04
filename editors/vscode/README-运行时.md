# 扩展自带的 Python 运行时（`runtime/`）

> **`runtime/` 不入库**（`.gitignore` 已排除）：约 31 MB 二进制，且是第三方分发物。
> 打包 `.vsix` **之前**要先把它生成出来，否则扩展在没装 Python 的机器上跑不起来。

## 为什么需要

扩展的三个入口都优先用自带的解释器：

| 入口 | 逻辑 |
|---|---|
| `extension.js` → `pickPython()` | 先试 `<扩展>/runtime/python.exe`（Windows）/ `runtime/bin/python3`，找不到才退回 PATH 上的 `python` / `python3` / `py` |
| `bin/elench.cmd` | 先试 `%HERE%\..\runtime\python.exe`，再退回 PATH |
| `installCli` | 把 `python/`、`bin/`、`runtime/` 一起复制到 `%APPDATA%\Elench`，再把 `bin/` 加进用户 PATH |

目标：**测试者机器上没装 Python，也能在 VS Code 里直接写、直接跑。**

## 怎么生成

从本机已安装的 CPython（这里用 3.14）裁一份出来，去掉测试、文档、tkinter、site-packages：

```powershell
$py = "C:\Users\haoll\AppData\Local\Programs\Python\Python314"
$dst = "editors/vscode/runtime"
Remove-Item -Recurse -Force $dst -ErrorAction SilentlyContinue
New-Item -ItemType Directory $dst | Out-Null

Copy-Item "$py\python.exe","$py\python3.dll","$py\python314.dll",
          "$py\vcruntime140.dll","$py\vcruntime140_1.dll","$py\LICENSE.txt" $dst
Copy-Item -Recurse "$py\DLLs" (Join-Path $dst "DLLs")
Copy-Item -Recurse "$py\Lib"  (Join-Path $dst "Lib")

foreach ($d in @("site-packages","test","ensurepip","idlelib","tkinter",
                 "turtledemo","lib2to3","pydoc_data","venv","distutils")) {
  Remove-Item -Recurse -Force (Join-Path $dst "Lib/$d") -ErrorAction SilentlyContinue
}
Get-ChildItem (Join-Path $dst "Lib") -Recurse -Directory -Filter "__pycache__" |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
```

体积参考：原始安装 228.9 MB → 裁剪后 **31.6 MB**（解压态），打进 `.vsix` 约 **12 MB**。

## 验证

```powershell
.\runtime\python.exe -c "import sys; print(sys.version)"          # 应该打印 3.14.x
.\runtime\python.exe -B .\python\elench.py --version               # 应该打印 elench 1.0.0
.\bin\elench.cmd --version                                         # 走 .cmd 也应该通
```

再打一次 `.vsix` 并解压，确认里面同时有：

```
extension/runtime/python.exe
extension/python/elench.py
```

## 注意

- **裁剪必须保守**：只删测试/文档/GUI/打包工具那几个目录。
  我们的工具零第三方依赖，但标准库里 `json` / `re` / `threading` / `subprocess` /
  `argparse` / `hashlib` / `csv` 都会被用到——删多了会 ImportError，而**打包过程不会报错**。
- 换 Python 主版本时要**同步改** `extension.js` 与 `bin/elench.cmd` 里的文件名
  （`python314.dll`、`python3.dll` 是版本相关的）。
- 这台机器上 `python.org` 与 PyPI 都不可达，所以**没法用官方 embeddable 包**，
  只能从本机已装好的 CPython 裁剪——这也是上面用 `Programs\Python\Python314` 的原因。
