#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""build-vsix.py —— 把 editors/vscode 打成可安装的 .vsix（**不依赖 npm**）

**为什么不用 `vsce`**：`@vscode/vsce` 需要 `npm install`（联网 + 一堆间接依赖）。
而 `.vsix` 本质就是一个 zip + 两个清单文件，**用标准库就能做**——
对一个"零依赖"项目来说，工具链也该零依赖。

**用法**

    python build-vsix.py                     # 版本号与文件名取自 package.json
    code --install-extension elench-0.3.0.vsix
"""
import io
import json
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

# 打进包的条目（相对 HERE）。**只列需要的**，测试与构建脚本不进去。
ENTRIES = [
    "package.json",
    "extension.js",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "language-configuration.json",
    "lib/parse.js",
    "syntaxes/eln.tmLanguage.json",
]

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{name}" Version="{version}" Publisher="{publisher}" />
    <DisplayName>{display}</DisplayName>
    <Description xml:space="preserve">{description}</Description>
    <Tags>elench,cost,complexity,contract</Tags>
    <Categories>Programming Languages</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{engine}" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value="" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value="" />
    </Properties>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code" />
  </Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/README.md" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Content.License" Path="extension/LICENSE" Addressable="true" />
  </Assets>
</PackageManifest>
"""

CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension=".json" ContentType="application/json" />
  <Default Extension=".js" ContentType="application/javascript" />
  <Default Extension=".md" ContentType="text/markdown" />
  <Default Extension=".vsixmanifest" ContentType="text/xml" />
  <Default Extension=".xml" ContentType="text/xml" />
</Types>
"""


def main() -> int:
    pkg = json.load(io.open(os.path.join(HERE, "package.json"), encoding="utf-8"))
    name = pkg["name"]
    version = pkg["version"]
    out = os.path.join(HERE, f"{name}-{version}.vsix")

    manifest = MANIFEST.format(
        name=name, version=version,
        publisher=pkg.get("publisher", "local"),
        display=pkg.get("displayName", name),
        description=pkg.get("description", ""),
        engine=pkg.get("engines", {}).get("vscode", "^1.70.0"),
    )

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("extension.vsixmanifest", manifest)
        for rel in ENTRIES:
            src = os.path.join(HERE, rel)
            if not os.path.exists(src):
                print(f"  ! 缺文件，跳过：{rel}")
                continue
            z.write(src, f"extension/{rel}")

        # **内置解释器**：python/ 下所有 .py 一起打进去，
        # 装好后不依赖用户先 clone 仓库。
        #
        # **必须递归**：`cost_domain/` 是包（2026-11 起成本模块合并进来），
        # 早先只 `listdir` 顶层 .py，包目录被整段漏掉——装好的扩展一算成本
        # 就 ImportError，而 .vsix 里看不出来。`__pycache__` 一律跳过。
        py_dir = os.path.join(HERE, "python")
        if os.path.isdir(py_dir):
            for root, dirs, files in os.walk(py_dir):
                dirs[:] = sorted(d for d in dirs if d != "__pycache__")
                for fname in sorted(files):
                    if not fname.endswith(".py"):
                        continue
                    src = os.path.join(root, fname)
                    rel = os.path.relpath(src, py_dir).replace(os.sep, "/")
                    z.write(src, f"extension/python/{rel}")

        # **命令行启动器**：bin/ 下的 elench(.cmd)
        bin_dir = os.path.join(HERE, "bin")
        if os.path.isdir(bin_dir):
            for name in sorted(os.listdir(bin_dir)):
                z.write(os.path.join(bin_dir, name), f"extension/bin/{name}")
    size = os.path.getsize(out) / 1024
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    print(f"  生成 {os.path.basename(out)}  ({size:.1f} KB, {len(names)} 个条目)")
    for n in names:
        print("    · " + n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
