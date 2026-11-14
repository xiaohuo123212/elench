#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""diag_json.py —— `check --json` 的**结构化诊断**（schema version 1）

## 为什么要有这个文件

人类输出是给眼睛看的；AI 的修复循环要的是**能被脚本直接应用**的东西：

    {"code": "E-Logic-WordOp", "severity": "error",
     "file": "x.eln", "line": 4, "col": 11,
     "end_line": 4, "end_col": 14,
     "message": "`and` 是已退役的词形运算符：……",
     "fix": {"kind": "replace", "text": "&&", "range": [4, 11, 4, 14]}}

一条 `fix` 一个替换：`range` 是**半开区间** `[起行, 起列, 止行, 止列]`
（1-based；止列不含），把 `range` 覆盖的源码换成 `text` 即可。这样 AI
不必再从"改法：`and` → `&&`"这句散文里自己找位置。

## 三条判据

1. **stdout 只有 JSON**：`jq` 直接吃；人类文案仍走原来的分支。
2. **顺序与人类输出一致**：这里**不排序**，原样按 `res.diagnostics` 走。
3. **确定性**：同一输入逐字节相同。不塞时间戳、不塞绝对路径——`file`
   是相对 cwd 的路径（越界时退回 basename），见 `_display_path`。

## 数据从哪来（**不在这里复制第二份**）

| 改法 | 唯一来源 |
|---|---|
| `and`/`or`/`not` → 符号 | `lexer.WORD_OPERATORS` |
| `Option`/`Result` → 新名 | `typenames.OLD`（没有时退回 `CANON`） |
| `push`/`length`/`is_empty` → 新名 | `typecheck.DEPRECATED_METHODS`（P4-0） |
| 弃用内建 → 新名 | `stdlib.DEPRECATED_BUILTINS[*].replacement` / `REPLACEMENTS` |
| 未知方法的最接近名字 | 诊断消息里的「支持：…」列表 |

**只读**这些模块：本文件不定义任何新旧名字对照表。另一条纪律是
`tools/check_diag_codes.py` 会把 `src/` 里 `"E-…"` 形式的字面量当成
"代码里抛的码"——所以这里只引用**文档里已有**的码；`E-Type-OldName`
由 typecheck 抛（P4-0），这里只按其**后缀**消费，不写字面量。
"""
from __future__ import annotations

import io
import os
import re
from typing import Dict, List, Optional, Tuple

#: 输出格式版本。**加字段可以，改含义要升版本**。
SCHEMA_VERSION = 1

_PREFIX_RE = re.compile(r"^\[\.(tu|eln)\]\s?")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER_RE = re.compile(r"0[xX][0-9a-fA-F_]+|0[bB][01_]+|[0-9][0-9_]*")
_WORD_BOUNDARY = r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])"

#: 旧声明关键字（parser 的判据表是 `OLD_DECL_WORDS`，这里只做**取值**，
#: 不据此判断语法位置——位置判据在 parser）。
_DECL_WORDS = ("val", "var", "let")

_OLD_TYPE_SUFFIX = "-OldName"


# ---------------------------------------------------------------- 源码视图


class _Source:
    """一个源文件的只读视图：行文本 + 按 (行, 列) 找 token。

    拿不到文件（路径为空 / 非 UTF-8）时 `ok=False`，所有查询返回 None——
    **宁可给不出 fix，也不给一个会改错位置的 fix**。
    """

    __slots__ = ("path", "text", "lines", "ok")

    def __init__(self, path: Optional[str]):
        self.path = path
        self.text = ""
        self.lines: List[str] = []
        self.ok = False
        if not path:
            return
        try:
            with io.open(path, "r", encoding="utf-8") as fh:
                self.text = fh.read()
        except (OSError, UnicodeDecodeError):
            return
        # 与 lexer 一致：BOM 不占列。
        if self.text.startswith("\ufeff"):
            self.text = self.text[1:]
        self.lines = self.text.splitlines()
        self.ok = True

    def line(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.lines):
            return self.lines[lineno - 1]
        return ""

    def token_starting_at(self, lineno: int, col: int) -> Optional[Tuple[int, int]]:
        """恰好从 `col` 开始的标识符/数字，返回 1-based 半开区间 (起, 止)。

        1-based 的**半开**止列 = 0-based `m.end() + 1`：`and` 占第 13–15 列，
        半开区间是 `[13, 16)`，与 `fix.range` 的定义一致。
        """
        s = self.line(lineno)
        i = col - 1
        if i < 0 or i >= len(s) or s[i].isspace():
            return None
        m = _IDENT_RE.match(s, i) or _NUMBER_RE.match(s, i)
        if m is None:
            return None
        return (i + 1, m.end() + 1)

    def word_span(self, lineno: int, word: str,
                  col: int, near: int = 8
                  ) -> Optional[Tuple[int, int, int]]:
        """在**诊断所在行**找 `word`，优先取覆盖 `col` 的那一处。

        返回 `(起列, 止列, 代价)`，代价越小越可信：
        0 = 正好从 col 开始；1 = col 落在词内；3.. = 同行附近（方法名在
        `.` 之后一列，所以"附近"要能覆盖这种偏移）。找不到返回 None。
        """
        if not word:
            return None
        s = self.line(lineno)
        if not s:
            return None
        pat = re.compile(_WORD_BOUNDARY % re.escape(word))
        best: Optional[Tuple[int, int, int]] = None
        for m in pat.finditer(s):
            a, b = m.start() + 1, m.end() + 1
            if a == col:
                score = 0
            elif a <= col <= b:
                score = 1
            elif a > col:
                gap = a - col
                score = 2 + gap if gap <= near else 5 + gap
            else:
                gap = col - b
                score = 2 + gap if gap <= near else 5 + gap
            if best is None or score < best[2]:
                best = (a, b, score)
        return best


def _advance(src: _Source, lineno: int, col: int, n: int) -> Tuple[int, int]:
    """从 (行, 列) 往后数 n 个字符（可跨行），返回 1-based 半开止位置。"""
    left = n
    line, c = lineno, col
    while left > 0:
        s = src.line(line)
        if not s:
            return line, c
        remain = len(s) - (c - 1)
        if left <= remain:
            return line, c + left
        left -= remain + 1          # 吃掉行尾的换行符
        line, c = line + 1, 1
    return line, c


# ---------------------------------------------------------------- 小工具


def _first_backtick(msg: str) -> Optional[str]:
    m = re.search(r"`([^`]+)`", msg or "")
    return m.group(1) if m else None


def _split_prefix(message: str) -> Tuple[Optional[str], str]:
    """`[.tu] ` / `[.eln] ` 前缀 → (`"tu"`/`"eln"`, 去掉前缀的消息)。

    前缀是 `checker.check_module` 给**哪个文件**的记号。JSON 里它变成
    独立的 `file` 字段，消息本身保持干净（人类输出仍带前缀，逐字节不变）。
    """
    m = _PREFIX_RE.match(message or "")
    if not m:
        return None, message or ""
    return m.group(1), (message or "")[m.end():]


def _display_path(path: Optional[str], cwd: str) -> str:
    """确定性的 `file`：相对 cwd；越界（探针在临时目录）退回 basename。"""
    if not path:
        return ""
    try:
        full = os.path.abspath(path)
        rel = os.path.relpath(full, cwd)
    except (OSError, ValueError):
        return os.path.basename(path).replace("\\", "/")
    if rel.startswith("..") or os.path.isabs(rel):
        rel = os.path.basename(full)
    return rel.replace("\\", "/")


def _levenshtein(a: str, b: str) -> int:
    """编辑距离（插入/删除/替换，1 次算 1）。"""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _literal_type(expr: str, depth: int = 0) -> Optional[str]:
    """从**字面量**推一个类型；推不出就 None（不猜）。

    只服务 `val x = 1;` 这类旧声明：整数 → `u64`、小数 → `f64`、
    布尔 → `bool`。字符串/字符/表达式**一律不猜**——`val s = "a";`
    给 `u64` 只会把一处错误换成另一处。
    """
    e = (expr or "").strip()
    while depth < 4 and e.startswith("(") and e.endswith(")"):
        e = e[1:-1].strip()
        depth += 1
    if e in ("true", "false"):
        return "bool"
    if re.fullmatch(r"0[xX][0-9a-fA-F_]+|0[bB][01_]+|[0-9][0-9_]*", e):
        return "u64"
    if re.fullmatch(r"(?:[0-9][0-9_]*\.[0-9_]*|\.[0-9_]+)"
                    r"(?:[eE][+-]?[0-9]+)?", e):
        return "f64"
    if re.fullmatch(r"[0-9][0-9_]*[eE][+-]?[0-9]+", e):
        return "f64"
    return None


def _decl_replacement(line: str, kw_end: int, keyword: str) -> Optional[str]:
    """`val x = 1;` → 替换关键字那一段的文本（`const u64` / `u64`）。

    返回 None 表示**推不出类型**（或形状不是旧声明），此时不给 fix。
    """
    rest = line[kw_end - 1:] if kw_end >= 1 else ""
    m = re.match(r"\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*(?:;|$)", rest)
    if not m:
        return None
    ty = _literal_type(m.group(2))
    if not ty:
        return None
    return ("const " + ty) if keyword == "val" else ty


def _cpp_signature(line: str) -> Optional[str]:
    """`func f(u64 a) -> u64 {` → `u64 f(u64 a)`；只做**签名**改写。"""
    m = re.match(
        r"^\s*(?:pub\s+)?(?:func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)"
        r"\s*\((.*?)\)\s*(?:->\s*([^{]+?))?\s*(?:\{.*)?$", line or "")
    if not m:
        return None
    name, params, ret = m.group(1), m.group(2), (m.group(3) or "void").strip()
    return f"{ret} {name}({params})"


def _is_keyword(word: str) -> bool:
    try:
        from lexer import KEYWORDS
        return word in KEYWORDS
    except Exception:                                   # noqa: BLE001
        return False


# ---------------------------------------------------------------- 名字来源


def _old_type_names() -> Dict[str, str]:
    """旧类型名 → 新名。唯一来源是 `typenames`，**不在这里再抄一份**。

    P4-0 之后用 `typenames.OLD`；在那之前（还没有 OLD）退回 `CANON` 里
    "旧名 → 新名"的那些项，这样即使诊断先落地，fix 也已经能给出。
    """
    try:
        import typenames as _tn
    except Exception:                                   # noqa: BLE001
        return {}
    old = getattr(_tn, "OLD", None)
    if isinstance(old, dict) and old:
        return dict(old)
    canon = getattr(_tn, "CANON", {})
    return {k: v for k, v in canon.items() if isinstance(k, str) and k != v}


def _rename_of(name: str) -> Optional[str]:
    """查"旧名 → 新名"，只认**裸标识符**形式的改名。

    来源按优先级：
      1. `typecheck.DEPRECATED_METHODS`（P4-0 的容器方法旧名）；
      2. `stdlib` 里同类名字的别名表（若将来加）；
      3. `stdlib.DEPRECATED_BUILTINS[*].replacement` / `REPLACEMENTS`——
         只有值是**单个标识符**时才算改名；`"for (auto x : xs) { … }"`
         这类是**代码片段**，不是 replace 能机械应用的，返回 None。
    """
    if not name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return None
    try:
        import typecheck as _tc
        table = getattr(_tc, "DEPRECATED_METHODS", None)
        if isinstance(table, dict):
            new = table.get(name)
            if isinstance(new, str) and re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_]*", new):
                return new
        for attr in ("METHOD_RENAMES", "RENAMED_METHODS", "METHOD_ALIASES"):
            table = getattr(_tc, attr, None)
            if isinstance(table, dict):
                new = table.get(name)
                if isinstance(new, str) and re.fullmatch(
                        r"[A-Za-z_][A-Za-z0-9_]*", new):
                    return new
    except Exception:                                   # noqa: BLE001
        pass
    try:
        import stdlib as _std
        for attr in ("RENAMED_BUILTINS", "BUILTIN_RENAMES", "ALIASES",
                     "REPLACEMENTS"):
            table = getattr(_std, attr, None)
            if isinstance(table, dict):
                new = table.get(name)
                if isinstance(new, str) and re.fullmatch(
                        r"[A-Za-z_][A-Za-z0-9_]*", new):
                    return new
        dep = getattr(_std, "DEPRECATED_BUILTINS", {})
        info = dep.get(name) if isinstance(dep, dict) else None
        new = (info or {}).get("replacement", "")
        if isinstance(new, str) and re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*", new):
            return new
    except Exception:                                   # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------- fix 结果


class _Info:
    """一条诊断的结构化补充：归属文件、fix、suggestion、span。"""

    __slots__ = ("file", "fix", "suggestion", "span")

    def __init__(self, file: Optional[str] = None,
                 fix: Optional[dict] = None,
                 suggestion: Optional[str] = None,
                 span: Optional[Tuple[int, int]] = None):
        self.file = file
        self.fix = fix
        self.suggestion = suggestion
        self.span = span


def _replace_fix(line: int, start: int, end: int, text: str) -> dict:
    return {"kind": "replace", "text": text,
            "range": [line, start, line, end]}


def _locate(files: List[Tuple[str, _Source]], word: str,
            line: int, col: int) -> Optional[Tuple[str, int, int]]:
    """在多份源里找 `word`；返回代价最小的一份 `(file, 起列, 止列)`。

    平手时**优先 .tu**（契约先行）；但"正好从 col 开始"永远赢过
    "同行附近"——`.eln` 里的方法名常比 `.tu` 里同名 token 偏一列。
    """
    best = None
    for idx, (path, src) in enumerate(files):
        sp = src.word_span(line, word, col)
        if sp is None:
            continue
        a, b, score = sp
        key = (score, 0 if idx == 0 else 1, idx)
        if best is None or key < best[0]:
            best = (key, path, a, b)
    if best is None:
        return None
    return best[1], best[2], best[3]


# ---------------------------------------------------------------- 各码的 fix


def _fix_logic_word(d, msg: str, files) -> _Info:
    try:
        from lexer import WORD_OPERATORS
    except Exception:                                   # noqa: BLE001
        return _Info()
    word = _first_backtick(msg)
    if word not in WORD_OPERATORS:
        word = None
        for _path, src in files:
            tok = src.token_starting_at(d.line, d.col)
            if tok:
                cand = src.line(d.line)[tok[0] - 1:tok[1] - 1]
                if cand in WORD_OPERATORS:
                    word = cand
                    break
    if not word:
        return _Info()
    loc = _locate(files, word, d.line, d.col)
    if loc is None:
        return _Info()
    path, a, b = loc
    return _Info(file=path,
                 fix=_replace_fix(d.line, a, b, WORD_OPERATORS[word][0]),
                 span=(d.line, b))


def _fix_old_type(d, msg: str, files) -> _Info:
    table = _old_type_names()
    if not table:
        return _Info()
    word = _first_backtick(msg)
    if word not in table:
        word = None
        for _path, src in files:
            tok = src.token_starting_at(d.line, d.col)
            if tok:
                cand = src.line(d.line)[tok[0] - 1:tok[1] - 1]
                if cand in table:
                    word = cand
                    break
    if not word:
        return _Info()
    loc = _locate(files, word, d.line, d.col)
    if loc is None:
        return _Info()
    path, a, b = loc
    new = table[word]
    return _Info(file=path, fix=_replace_fix(d.line, a, b, new),
                 suggestion=new, span=(d.line, b))


def _fix_decl_keyword(d, msg: str, files) -> _Info:
    for path, src in files:
        tok = src.token_starting_at(d.line, d.col)
        if not tok:
            continue
        s = src.line(d.line)
        keyword = s[tok[0] - 1:tok[1] - 1]
        if keyword not in _DECL_WORDS:
            continue
        text = _decl_replacement(s, tok[1], keyword)
        if text is None:
            return _Info(file=path)
        return _Info(file=path,
                     fix=_replace_fix(d.line, tok[0], tok[1], text),
                     span=(d.line, tok[1]))
    # 极端兜底：位置不是关键字时，用消息里的旧词在同行找
    word = _first_backtick(msg)
    if word in _DECL_WORDS:
        loc = _locate(files, word, d.line, d.col)
        if loc:
            path, a, b = loc
            s = next((src.line(d.line) for p, src in files if p == path), "")
            text = _decl_replacement(s, b, word)
            if text is not None:
                return _Info(file=path,
                             fix=_replace_fix(d.line, a, b, text),
                             span=(d.line, b))
            return _Info(file=path)
    return _Info()


def _hint_func_keyword(d, msg: str, files) -> _Info:
    """`fn` / `func` 已下线：**要删关键字**，所以不给 fix，只给 suggestion。"""
    # 老世界（HEAD）：消息直接给"函数关键字是 `func`"。
    m = re.search(r"函数关键字是\s*`([A-Za-z_][A-Za-z0-9_]*)`", msg)
    if m and _is_keyword(m.group(1)):
        return _Info(suggestion=m.group(1))
    # 新世界（P4-0）：要写成 C++ 形，按实际那一行给签名建议。
    word = _first_backtick(msg)
    path = None
    if word in ("fn", "func"):
        loc = _locate(files, word, d.line, d.col)
        if loc:
            path, _a, _b = loc
    if path is None and files:
        path = files[0][0]
    if path is not None:
        src = next(src for p, src in files if p == path)
        sig = _cpp_signature(src.line(d.line))
        if sig:
            return _Info(file=path, suggestion=sig)
    return _Info(file=path,
                 suggestion="把返回类型写在函数名前面（C++ 形：`T f(...) { … }`）")


def _hint_unknown_method(d, msg: str) -> _Info:
    """未知方法：不给 fix（换名字不一定是本意），给最接近的合法方法名。"""
    m = re.search(r"没有方法\s*`([^`]+)`", msg)
    name = m.group(1) if m else None
    # 旧名被拒时，对照表给出的一定是"最接近"的（距离可能 > 2）
    m_old = re.search(r"旧名\s*`([^`]+)`\s*不再接受", msg)
    if m_old:
        new = _rename_of(m_old.group(1))
        if new:
            return _Info(suggestion=new)
    if not name:
        return _Info()
    m_sup = re.search(r"支持：([^。\n]*)", msg)
    if not m_sup:
        return _Info()
    cands = [x.strip() for x in m_sup.group(1).split("、") if x.strip()]
    best = None
    for cand in cands:
        dist = _levenshtein(name, cand)
        if dist <= 2:
            key = (dist, cand)
            if best is None or key < best:
                best = key
    return _Info(suggestion=best[1] if best else None)


def _fix_deprecated(d, msg: str, files) -> _Info:
    """`W-Builtin-Deprecated`：只有"旧名 → 新名"才给 replace。"""
    word = _first_backtick(msg)
    if not word:
        return _Info()
    new = _rename_of(word)
    if not new:
        return _Info()
    loc = _locate(files, word, d.line, d.col)
    if loc is None:
        return _Info(suggestion=new)
    path, a, b = loc
    return _Info(file=path, fix=_replace_fix(d.line, a, b, new),
                 suggestion=new, span=(d.line, b))


# ---------------------------------------------------------------- 组装


def _files_of(tu: Optional[str], ai: Optional[str]
              ) -> List[Tuple[str, _Source]]:
    out = []
    for path in (tu, ai):
        if path and all(path != p for p, _ in out):
            out.append((path, _Source(path)))
    return out


def _mentions(msg: str, word: str) -> bool:
    """`word` 是不是消息里一个**整词**（避免 `val` 命中 `value`）。"""
    if not word:
        return False
    try:
        return re.search(_WORD_BOUNDARY % re.escape(word), msg) is not None
    except re.error:
        return word in msg


def _guess_file(d, kind: Optional[str], msg: str,
                files: List[Tuple[str, _Source]],
                tu: Optional[str], ai: Optional[str]) -> Optional[str]:
    if kind == "tu":
        return tu
    if kind == "eln":
        return ai
    if not files:
        return tu or ai
    if len(files) == 1:
        return files[0][0]
    if ".eln" in msg:
        return ai or files[-1][0]
    if ".tu" in msg:
        return tu or files[0][0]
    # 无前缀、消息里也没点名文件：位置上的 token 只在一边出现 → 那一边。
    if tu and ai and d.line > 0:
        by_path = dict(files)
        s_t, s_a = by_path.get(tu), by_path.get(ai)
        if s_t is not None and s_a is not None:
            tok_t = s_t.token_starting_at(d.line, d.col)
            tok_a = s_a.token_starting_at(d.line, d.col)
            w_t = s_t.line(d.line)[tok_t[0] - 1:tok_t[1] - 1] if tok_t else ""
            w_a = s_a.line(d.line)[tok_a[0] - 1:tok_a[1] - 1] if tok_a else ""
            if w_a and w_a != w_t and _mentions(msg, w_a):
                return ai
            if w_t and w_t != w_a and _mentions(msg, w_t):
                return tu
            if w_a and not w_t:
                return ai
            if w_t and not w_a:
                return tu
    return tu or (files[0][0] if files else ai)


def _end_pos(d, src: Optional[_Source]) -> Tuple[int, int]:
    """无 fix 时的诊断末位置：优先"从该列开始的 token"，退回来长度。"""
    if src is None or not src.ok or d.line <= 0:
        return d.line, d.col
    tok = src.token_starting_at(d.line, d.col)
    if tok:
        return d.line, tok[1]
    n = int(getattr(d, "end", 0) or 0) - int(getattr(d, "start", 0) or 0)
    if n > 0:
        return _advance(src, d.line, d.col, n)
    return d.line, d.col


def _record(d, tu: Optional[str], ai: Optional[str],
            files: List[Tuple[str, _Source]], cwd: str) -> dict:
    kind, msg = _split_prefix(d.message)
    code = d.code
    info = _Info()
    try:
        if code == "E-Logic-WordOp":
            info = _fix_logic_word(d, msg, files)
        elif code == "E-Decl-OldKeyword":
            info = _fix_decl_keyword(d, msg, files)
        elif code == "E-Func-KeywordRemoved":
            info = _hint_func_keyword(d, msg, files)
        elif code == "E-Call-UnknownMethod":
            info = _hint_unknown_method(d, msg)
        elif code == "W-Builtin-Deprecated":
            info = _fix_deprecated(d, msg, files)
        elif code.endswith(_OLD_TYPE_SUFFIX):
            info = _fix_old_type(d, msg, files)
    except Exception:                                   # noqa: BLE001
        # **诊断的附加信息不许让 check 崩**：拿不到就不给。
        info = _Info()

    file = info.file or _guess_file(d, kind, msg, files, tu, ai)
    src = next((s for p, s in files if p == file), None)
    if info.fix is not None:
        end_line, end_col = info.fix["range"][2], info.fix["range"][3]
    else:
        end_line, end_col = _end_pos(d, src)
    return {
        "code": code,
        "severity": d.severity,
        "file": _display_path(file, cwd),
        "line": int(d.line or 0),
        "col": int(d.col or 0),
        "end_line": int(end_line or 0),
        "end_col": int(end_col or 0),
        "message": msg,
        "fix": info.fix,
        "suggestion": info.suggestion,
        # 旧式修法清单保留：`--format=json` 的既有消费者（`test_actions`）
        # 靠它拿 `--apply=N` 的候选；`fix` 是新增的**可直接应用**那一条。
        "actions": [a.to_dict() for a in (getattr(d, "actions", None) or [])],
    }


def payload(res, tu_path: Optional[str] = None, ai_path: Optional[str] = None,
            cwd: Optional[str] = None) -> dict:
    """`CheckResult` → 可直接 `json.dumps` 的 dict（schema version 1）。

    `tu_path` / `ai_path` 用来给诊断找**归属文件**和源码（算 fix 的 range）。
    `ai_path` 缺省时按 `.tu` 同名找 `.eln`——与 `checker.check_files`
    的猜测规则一致，否则 `[.eln]` 的诊断会没有文件可归属。
    """
    cwd = cwd or os.getcwd()
    ai = ai_path
    if ai is None and tu_path:
        cand = os.path.splitext(tu_path)[0] + ".eln"
        if os.path.exists(cand):
            ai = cand
    files = _files_of(tu_path, ai)
    out = {
        "version": SCHEMA_VERSION,
        "ok": bool(res.ok),
        "module": res.module,
        "functions": [f.to_dict() for f in res.functions],
        "choices": res.choices,
        "labels_used": sorted(res.labels_used),
        "diagnostics": [_record(d, tu_path, ai, files, cwd)
                        for d in res.diagnostics],
    }
    # **`check --exhaustive` 的分档结论**（可选字段，schema v1 允许加字段）。
    # 没开穷举时**不出现**——既有消费者看到的 JSON 逐字节不变。
    _exh = getattr(res, "exhaustive", None)
    if _exh is not None:
        out["exhaustive"] = _exh
    return out
