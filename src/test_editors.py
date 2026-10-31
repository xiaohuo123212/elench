#!/usr/bin/env python3
"""test_editors.py —— 编辑器插件不许和词法分析器分叉

**为什么需要这个测试**

`editors/vscode/syntaxes/eln.tmLanguage.json` 里有一份关键词表。
它**必然**会和 `src/lexer.py` 的 `KEYWORDS` 分叉——除非有东西拦住。

分叉的后果很具体：**编辑器高亮认识的词，编译器不认识**（或者反过来）。
读者看到满屏彩色，敲下去却报错——**这比不高亮更糟**。

**这个测试就做三件事**：

1. grammar 里的关键词集合 == `lexer.KEYWORDS`（**逐字比对**）；
2. grammar 里"已下线"的词集合 ⊆ `lexer.REMOVED_KEYWORDS`；
3. `package.json` 指向的 grammar 文件存在，且 `scopeName` 与 grammar 自身一致、
   文件后缀包含 `.eln` 与 `.tu`。
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
VS = os.path.join(ROOT, "editors", "vscode")
GRAMMAR = os.path.join(VS, "syntaxes", "eln.tmLanguage.json")
sys.path.insert(0, HERE)

_passed = 0
_failed = []


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def words_in(pattern):
    """从一个 `\\b(?:a|b|c)\\b` 形状的 match 里取出词集合。"""
    body = pattern
    if "(?:" in body:
        body = body.split("(?:", 1)[1].rsplit(")", 1)[0]
    return set(w for w in body.split("|") if w)


def main() -> int:
    import lexer

    g = json.load(io.open(GRAMMAR, encoding="utf-8"))
    repo = g["repository"]

    print("[1] grammar 关键词 == lexer.KEYWORDS")
    in_grammar = words_in(repo["keyword"]["match"])
    in_lexer = set(lexer.KEYWORDS)
    missing = sorted(in_lexer - in_grammar)      # 编译认识、插件不认识
    extra = sorted(in_grammar - in_lexer)        # 插件认识、编译不认识
    check(not missing, f"没有漏掉的关键词（{len(in_lexer)} 个）", f"漏: {missing}")
    check(not extra, "没有多写的关键词", f"多: {extra}")

    print("[2] 已下线的词 ∈ lexer.REMOVED_KEYWORDS")
    removed_g = words_in(repo["removed"]["match"])
    removed_l = set(getattr(lexer, "REMOVED_KEYWORDS", {}) or {})
    check(removed_g <= removed_l, "下线的词都在词法器的下线表里",
          f"词法器没有: {sorted(removed_g - removed_l)}")

    print("[3] package.json 与 grammar 对得上")
    pkg = json.load(io.open(os.path.join(VS, "package.json"), encoding="utf-8"))
    lang = pkg["contributes"]["languages"][0]
    gram = pkg["contributes"]["grammars"][0]
    grammar_path = os.path.normpath(os.path.join(VS, gram["path"]))
    check(os.path.exists(grammar_path), "package.json 指向的 grammar 文件存在", grammar_path)
    check(gram["scopeName"] == g["scopeName"],
          "两处 scopeName 一致", f"{gram['scopeName']} vs {g['scopeName']}")
    check(set(lang["extensions"]) == {".eln", ".tu"},
          "两种后缀都注册了（.eln 实现 / .tu 声明）", str(lang["extensions"]))
    check(lang["id"] == "elench", "语言 id 是 elench", lang["id"])

    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
