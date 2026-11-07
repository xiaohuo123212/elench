#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_grammar.py —— 从 `.tu` 导出受限语法（`src/grammar.py`）。

## 守的三件事

1. **EBNF 不漂移**：词法器里有的关键字，EBNF 里必须出现。
   漂移的语法比没有语法更危险——解码器放行、检查器拒绝。
2. **词汇表是白名单**：本模块能出现的函数/外部函数/类型/常量，一个不多一个不少。
3. **洞只挖 body**：签名由 `.tu` 决定，模型不许重写。
"""
import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import json
import os
import subprocess
import sys

import grammar as G
from lexer import KEYWORDS
from parser import parse_source

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


EXAMPLE = os.path.join(ROOT, "文档", "样例")


def load(mod):
    src = open(os.path.join(EXAMPLE, mod + ".tu"), encoding="utf-8").read()
    return parse_source(src, mod + ".tu").module


def test_ebnf_covers_keywords():
    print("[1] EBNF 覆盖词法器的所有结构关键字")
    ebnf = G.language_ebnf()
    missing = [k for k in sorted(KEYWORDS)
               if k not in G.TOKEN_KEYWORDS and f'"{k}"' not in ebnf]
    check(not missing, "每个关键字都出现在 EBNF 里", str(missing))
    check("module" in ebnf and "stmt" in ebnf and "expr" in ebnf,
          "有 module / stmt / expr 三个入口")
    check("(*" in ebnf and "*)" in ebnf, "注释用 EBNF 的 (* *) 而不是 //")


def test_vocab():
    print("[2] 词汇表 = 白名单（签名 / effects / target）")
    mod = load("count")
    v = G.module_vocab(mod)
    names = [f["name"] for f in v["functions"]]
    check("count" in names, "count 在词汇表里", str(names))
    f = next(x for x in v["functions"] if x["name"] == "count")
    check(f["params"] == [{"name": "text", "type": "vector<u8>&"}],
          "参数名与类型正确", str(f["params"]))
    check(f["returns"] == "u64", "返回类型正确", f["returns"])
    check(f["effects"] == ["alloc"], "effects 正确", str(f["effects"]))
    check(f["has_target"] is True, "标了 has_target")

    # 多函数模块：词汇表函数数 = .tu 里 spec 数
    mod2 = load("search")
    n_specs = sum(1 for n in mod2.children if n.kind == "spec")
    check(len(G.module_vocab(mod2)["functions"]) == n_specs,
          f"search 的词汇表函数数 = spec 数（{n_specs}）")


def test_extern_and_types():
    print("[3] 外部函数 / 类型 / 常量")
    src = '''
extern strlen(const vector<u8>& s) -> u64 from "ucrtbase" effects { fs };

struct Entry { key: vector<u8> }

const u64 N = 8;

spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(n) } };
}
'''
    mod = parse_source(src, "t.tu").module
    v = G.module_vocab(mod)
    check([e["name"] for e in v["externs"]] == ["strlen"], "extern 进词汇表")
    e = v["externs"][0]
    check(e["library"] == "ucrtbase" and e["effects"] == ["fs"],
          "extern 的库名与 effects 被记下", str(e))
    check([c["name"] for c in v["constants"]] == ["N"], "常量进词汇表")
    check([t["name"] for t in v["types"]] == ["Entry"], "类型进词汇表")


def test_holes():
    print("[4] 洞里只有 body")
    mod = load("sum")
    holes = G.skeletons(mod)
    check(len(holes) >= 1, "至少一个洞", str(len(holes)))
    h = holes[0]
    check(h["id"].endswith(".body") and h["hole_grammar"] == "block",
          "洞的类型是 block", str(h))
    check("func " in h["signature"] and h["returns"] in h["signature"],
          "骨架里带完整签名", h["signature"])
    check("{" not in h["signature"], "骨架**不含** body（否则就没得填了）", h["signature"])


def test_render():
    print("[5] 三种输出格式")
    mod = load("count")
    ebnf = G.render(mod, "ebnf")
    check(ebnf.strip().startswith("(*") and "module" in ebnf, "ebnf 单独输出")
    js = G.render(mod, "json")
    data = json.loads(js)
    check(set(data) == {"language", "vocabulary", "holes"}, "json 有三个顶层键", str(set(data)))
    check(data["vocabulary"]["functions"][0]["name"] == "count", "json 里带词汇表")
    both = G.render(mod, "both")
    check("module" in both and '"vocabulary"' in both, "both 里既有 EBNF 也有 JSON")
    try:
        G.render(mod, "yaml")
        check(False, "未知格式应报错", "没有抛")
    except ValueError:
        check(True, "未知格式报错")


def test_cli():
    print("[6] 命令行：能写出文件")
    out = os.path.join(ROOT, "tests", "_grammar_out.json")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, os.path.join(SRC, "elench.py"), "grammar",
                        os.path.join(EXAMPLE, "count.tu"), "--format=json",
                        f"--out={out}"],
                       capture_output=True, encoding="utf-8", errors="replace", env=env)
    check(r.returncode == 0, "退出码 0", (r.stdout or "") + (r.stderr or ""))
    ok = os.path.exists(out)
    check(ok, "文件已写出")
    if ok:
        data = json.load(open(out, encoding="utf-8"))
        check(data["vocabulary"]["functions"][0]["name"] == "count", "写出的 JSON 正确")
        os.remove(out)


def main() -> int:
    test_ebnf_covers_keywords()
    test_vocab()
    test_extern_and_types()
    test_holes()
    test_render()
    test_cli()
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
