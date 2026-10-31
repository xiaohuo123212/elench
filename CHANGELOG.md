# Changelog

本文件记录**每次推送**给公开仓库带来的变化。
内部设计、规划、申请书、预印本材料**不入库**（见 `.gitignore`），只在本地维护。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [Unreleased]

### Added
- `文档/入门30分钟.md`、`文档/语法清单.md`、`文档/内建函数.md`：对外可直接阅读的入门与参考。
- `文档/样例/`：10 个可运行样例（与内部样例同源）。
- `editors/vscode/python/`：VS Code 扩展内置解释器。

### Changed
- 公开仓库不再包含 `文档/设计/`、`文档/其他/`、`文档/实验/`（内部规划与申报材料）。
- README 的"从哪里读"改为指向对外文档。

---

## [0.4.0] — 2026-11-01

### Added
- **VS Code 扩展内置解释器**：`python/` 随扩展打包（33 个模块），装好即可检查/算成本/构建/运行，不必先 clone 仓库。
- 新命令 **`Elench: 运行当前程序`**（右键菜单第一项）。
- 扩展打包脚本 `editors/vscode/build-vsix.py`：纯标准库打 `.vsix`，不依赖 npm。

### Changed
- 扩展版本 `0.3.0` → `0.4.0`；包体积 14 KB → 229 KB。
- `dshPath()` 三级回退：`elench.dshPath` → 工作区 `src/dsh.py` → 扩展自带 `python/dsh.py`。

---

## [0.3.0] — 2026-10-30

### Added
- 语法高亮（`.eln` / `.tu`，TextMate）。
- 三个命令：检查（进 Problems）、显示成本上界、构建门禁。
- 保存时自动检查（`elench.checkOnSave`）。

---

## [0.71.0] — 2026-10-31

### Added
- 语言表面：字符串字面量、可变参数 `print`、效果检查、`requires` 调用点验证、`parallel` 块、100 个内建。
- 成本模型：`--segmented` 分档、`ext(name)` 具名未知、分配精确判定。

### Fixed
- 成本解释器两处低估（循环迭代 13→14、方法调用 2→3），修复后 κ 只增不减。

---

## [0.1.0] — 2026-10-20

### Added
- 首个公开版本：两文件模型（`.tu` / `.eln`）、成本契约、`check` / `cost` / `build`、解释器与测试。
