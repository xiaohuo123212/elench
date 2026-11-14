# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors

r"""preproc.py —— **先预处理再分析**（规格 §8.10）

`#define` / `#ifdef` 一族在**词法之前**跑一遍：AST 建在**展开后**的程序上，
κ 也就算在真正被编译的那份代码上。这样 `#define N 10` 里的 `N` 与手写 `10`
对成本模型**完全等价**——不需要给宏单独定义语义。

## 对外三个入口

| 入口 | 用途 |
|---|---|
| `preprocess(src, path, defines)` | 返回 `PreprocResult`（展开后的文本 + 诊断 + 来源表） |
| `set_defines(dict)` / `get_defines()` | 命令行 `-D NAME[=VALUE]`（默认空） |
| `annotate(diags)` | 给落在**宏展开行**上的诊断补一句「由宏 `N`（定义在 3:9）展开」 |

## 行号映射方案（**硬要求**）

`preprocess()` 返回的文本与输入**逐行对齐**：

- 指令行（`#include` / `#ifdef` / `#define` …）与**死分支**整行换成空行；
- 宏体在**定义时**就把 `\` 续行折成一行，展开时不会往行里插入换行；
- 所以：**展开后第 N 行的记号，就是原文件第 N 行的记号**，诊断行号永远指对。

**列号是展开后的列**（不做逐字符映射）：宏名被替换成宏体后，同一行后面的
记号列号会平移。行号精确、列号近似——诊断里另外给出宏的**定义位置**
（`由宏 `N`（定义在 3:9）展开`），定位靠"行 + 定义处"两段信息。

**宏展开的记号算"来自使用处"**（不是定义处）：`Token.line/col` 就是它在
展开后文本里的位置，即宏名被使用的那一行。定义处通过 `annotate()` 补进
诊断消息，不塞进 token（塞进去会污染 formatter / JSON 输出）。

## 支持矩阵

| 指令 | 处理 |
|---|---|
| `#include <标准头>` | **丢弃**（白名单 `STANDARD_HEADERS` 内；我们已有内建类型与门面头） |
| `#include <其它>` / `#include "…"` | `E-Lex-IncludeUnresolved`（**不静默丢**：本语言不读头文件，这一行被忽略、库的符号一个都不可用；要用就翻译成 `.tu`）。`#ifdef` 死分支里的不报 |
| `#pragma …` | **丢弃**（编译期提示，与语义无关） |
| `#define NAME value` | 对象式宏：之后 `NAME` → `value` 的**记号序列** |
| `#define F(a, b) expr` | 函数式宏：参数**按记号**替换；**`#`/`##` 明确报错**（不静默降级） |
| `#undef NAME` | 取消定义 |
| `#ifdef` / `#ifndef` / `#if` / `#elif` / `#else` / `#endif` | 按**定义集**求值；**未定义的宏为假/0**（与 C++ 一致） |
| `#error …` | `E-Lex-PreprocessorError`（带原文） |
| 其它（`#line` / `#warning` / `#import` …） | **明确报错**（`E-Lex-Preprocessor`），**绝不当注释删掉** |

`#if` 表达式支持：`defined(X)` / `defined X`、`!`、`&&`、`||`、`==` `!=`
`<` `>` `<=` `>=`、`+ - * / %`、`<< >> & | ^ ~`、括号、十进制/`0x` 整数。
未定义的标识符按 `0` 处理（C++ 规则）。

## `#include`：标准头丢弃，其它头**必须开口**（2026-11-13）

判据不是"能不能解析"，而是"**丢掉了会不会让结论失效**"：

- **标准库头**（`STANDARD_HEADERS`：C++ 标准头（C++17 全集 + 常用 C++20/23）
  + C 兼容头）⇒ 静默丢弃。
  本语言有内建类型与门面头，`#include <vector>` 只是 C++ 文件的"第一屏"，
  删掉它不改变任何语义；
- **别的头**（第三方 `<nlohmann/json.hpp>`、项目内 `"my.h"`、平台 `<windows.h>`）
  ⇒ `E-Lex-IncludeUnresolved`。这些头里的符号本语言**一个都用不了**，静默丢掉
  等于悄悄删掉用户的依赖：`check` 打印"通过"时用户以为库接进来了，而
  **`check` 从头到尾没看过库的任何一行**。这比报错危险得多。
- `#ifdef` **死分支**里的 `#include` 不诊断（与 `#define` 一致：死分支不参与语义；
  `#ifdef _WIN32` 下的 `<windows.h>` 因此天然安静）。

白名单是**数据**（一个 `frozenset`），加一个头只改这一处；内容取自仓库语料
（`demo/corpus/*.cpp`）、探针库（`demo/probe-libs/**`）、测试里实际出现过的头，
再加上 C++17 标准头全集。

## 终止条件（**硬要求**）

- **层数上限** `MAX_DEPTH = 100`；
- **环检测**：展开栈里出现同名宏 → `E-Lex-MacroRecursion`，不在原地打转；
- 两者都报错并把该宏名原样留在展开结果里（不静默删除代码）。

**宏可以展开成任何记号序列，包括关键字**（2026-11-10 用户口径）：我们分析的是
展开后的程序，`#define IF if` 之后那个 `if` 就是一个 `if`——成本模型只看结构，
不看记号拼写，所以**不设关键字保护**。

## 已知边界（照实写）

- 函数式宏调用**不跨行**（`F(` 之后的 `)` 必须在本行）；跨行报
  `E-Lex-MacroUnterminated`。
- `#` / `##` 不支持（函数式宏定义处报错）。
- 可变参数 `...` / `__VA_ARGS__` 不支持（报错）。
- 非指令行的 `\` 续行不做拼接（只有指令行做）。
- 同一进程里若连续解析多个文件，`annotate()` 按**行号**匹配来源表；
  两个文件在同一行都有宏展开时可能多给一句来源（宁可多说，不可指错行）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

#: 宏展开层数上限：超过几乎一定是环（或病态宏），**不是**性能调优参数。
MAX_DEPTH = 100

#: **标准库头白名单**（`#include` 到这些头 = 静默丢弃）。
#:
#: 判据（2026-11-13，见模块文档「`#include`」一节）：
#: - **标准头**不影响本语言语义；本语言已有内建类型与门面头（`vector` / `map` /
#:   `string` / `array` / `optional` …），`#include <vector>` 只是 C++ 文件的"第一屏"；
#: - **其它任何头**（第三方 / 项目内 / 平台 SDK）里的符号本语言一个都用不了，
#:   静默丢掉等于悄悄删掉用户的依赖 ⇒ `E-Lex-IncludeUnresolved`。
#:
#: 内容来源（**从代码里取，不凭感觉列**）：
#: 1. 仓库语料 `demo/corpus/*.cpp`、探针库 `demo/probe-libs/**`、测试源码里
#:    **实际出现过**的 `#include` 实参（ci 脚本 + 人工清点：algorithm / array /
#:    cctype / cmath / cstddef / cstdint / cstdio / cstdlib / filesystem / fstream /
#:    iostream / map / memory_resource / regex / sstream / string / unordered_map /
#:    utility / vector）；
#: 2. 再加 C++ 标准头全集（C++17 + C++20/23 常用头 + C 兼容头），
#:    这样"以后加一个标准头"不用碰逻辑。
#:
#: **这是数据，不是一串 if**：加/删头只改这一处（`tests/cases/test_include_diag.py`
#: 会断言白名单覆盖上面那批语料头，漏一个就红）。
STANDARD_HEADERS: frozenset = frozenset({
    # -- C++ 容器 / 迭代器 --
    "array", "deque", "forward_list", "list", "map", "queue", "set", "stack",
    "unordered_map", "unordered_set", "vector",
    # -- C++ 字符串 / 工具 --
    "any", "bitset", "charconv", "functional", "initializer_list", "iterator",
    "memory", "memory_resource", "new", "optional", "scoped_allocator",
    "string", "string_view", "tuple", "type_traits", "typeindex", "typeinfo",
    "utility", "valarray", "variant",
    # -- C++ 算法 / 数值 --
    "algorithm", "bit", "cmath", "compare", "complex", "execution", "limits",
    "numeric", "numbers", "random", "ratio",
    # -- C++ 并发 / 时间 / 文件 --
    "atomic", "barrier", "chrono", "condition_variable", "filesystem", "future",
    "latch", "mutex", "semaphore", "shared_mutex", "stop_token", "syncstream",
    "system_error", "thread",
    # -- C++ I/O / 本地化 / 正则 --
    "codecvt", "fstream", "iomanip", "ios", "iosfwd", "iostream", "istream",
    "locale", "ostream", "regex", "sstream", "streambuf",
    # -- C++ 异常 / 诊断 / 杂项 --
    "cassert", "coroutine", "exception", "format", "source_location", "span",
    "stdexcept", "version",
    # -- C++20/23 --
    "concepts", "expected", "flat_map", "flat_set", "generator", "mdspan",
    "print", "ranges", "spanstream", "stacktrace", "stdfloat",
    # -- C 兼容头（`<c*>` 形式） --
    "cctype", "cerrno", "cfenv", "cfloat", "cinttypes", "ciso646", "climits",
    "clocale", "csetjmp", "csignal", "cstdalign", "cstdarg", "cstdbool",
    "cstddef", "cstdint", "cstdio", "cstdlib", "cstring", "ctime", "cuchar",
    "cwchar", "cwctype",
    # -- C 兼容头（不带 `c` 前缀的旧写法，C++ 里也合法） --
    "assert.h", "complex.h", "ctype.h", "errno.h", "fenv.h", "float.h",
    "inttypes.h", "iso646.h", "limits.h", "locale.h", "math.h", "setjmp.h",
    "signal.h", "stdalign.h", "stdarg.h", "stdatomic.h", "stdbool.h",
    "stddef.h", "stdint.h", "stdio.h", "stdlib.h", "stdnoreturn.h",
    "string.h", "tgmath.h", "threads.h", "time.h", "uchar.h", "wchar.h",
    "wctype.h",
})

#: 命令行 `-D NAME[=VALUE]`（默认空）。`dsh` 在派发子命令前 `set_defines()`。
_ACTIVE_DEFINES: Dict[str, str] = {}

#: 宏展开来源表：行号 → [「由宏 `N`（定义在 3:9）展开」…]。
#: **全局累积**（不按文件分）：`parser.parse_source()` 不把文件名传给 `Lexer`，
#: 而 `annotate()` 只拿得到诊断的行号。行号精确，跨文件同行的概率极低。
_NOTES: Dict[int, List[str]] = {}


# ---------------------------------------------------------------- 数据结构

@dataclass
class PreprocDiagnostic:
    """预处理层诊断（与 `lexer.Diagnostic` 同形，避免 import 环）。"""
    code: str
    message: str
    line: int
    col: int


@dataclass
class Macro:
    name: str
    params: Optional[List[str]]      # None = 对象式宏；list = 函数式宏的参数名
    body: str                        # 记号序列（已折行，单行文本）
    line: int = 0                    # 定义处（0 = 命令行 -D）
    col: int = 1
    origin: str = ""                 # "命令行" 等；空 = 源文件里


@dataclass
class PreprocResult:
    text: str                                       # 展开后的源码（行号与原文对齐）
    diagnostics: List[PreprocDiagnostic] = field(default_factory=list)
    notes: Dict[int, List[str]] = field(default_factory=dict)   # 本文件的来源表


@dataclass
class _Cond:
    kind: str            # `#ifdef` / `#if` / …
    parent_active: bool
    active: bool
    line: int
    taken: bool = False  # 本组里已经有一个分支为真


class _CondError(Exception):
    """`#if` 表达式语法/求值错误。"""


# ---------------------------------------------------------------- 命令行定义

def set_defines(defines: Optional[Dict[str, str]]) -> None:
    """设置命令行 `-D` 宏（`-D NAME` 的宏体是 `1`，与 C++ 一致）。"""
    global _ACTIVE_DEFINES
    out: Dict[str, str] = {}
    for k, v in (defines or {}).items():
        out[str(k)] = "" if v is None else str(v)
    _ACTIVE_DEFINES = out


def get_defines() -> Dict[str, str]:
    return dict(_ACTIVE_DEFINES)


# ---------------------------------------------------------------- 小工具

def _skip_quoted(s: str, i: int) -> int:
    """`s[i]` 是 `"` 或 `'`：返回配对引号之后的下标（未闭合就返回 `len(s)`）。"""
    q = s[i]
    j = i + 1
    n = len(s)
    while j < n:
        if s[j] == "\\":
            j += 2
            continue
        if s[j] == q:
            return j + 1
        j += 1
    return n


def _has_paste(body: str) -> bool:
    """宏体里有没有 `#` / `##`（引号内的 `#` 不算）。"""
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch in "\"'":
            i = _skip_quoted(body, i)
            continue
        if ch == "#":
            return True
        i += 1
    return False


def _note_for(mac: Macro) -> str:
    if mac.line <= 0:
        return f"由宏 `{mac.name}`（命令行 -D 定义）展开"
    return f"由宏 `{mac.name}`（定义在 {mac.line}:{mac.col}）展开"


# ---------------------------------------------------------------- 主体

class _Preproc:
    def __init__(self, src: str, path: str, defines: Optional[Dict[str, str]]):
        self.src = src
        self.path = path or "<input>"
        self.macros: Dict[str, Macro] = {}
        for name, value in (defines or {}).items():
            self.macros[name] = Macro(name, None, str(value), 0, 1, origin="命令行")
        self.diags: List[PreprocDiagnostic] = []
        self.notes: Dict[int, List[str]] = {}
        self.out: List[str] = []
        self.cond: List[_Cond] = []

    # -- 诊断 --
    def _err(self, code: str, msg: str, line: int, col: int = 1):
        self.diags.append(PreprocDiagnostic(code, msg, line, col))

    # -- 有没有生效 --
    def _active(self) -> bool:
        return all(f.active for f in self.cond)

    # -- 主循环 --
    def run(self) -> PreprocResult:
        lines = self.src.split("\n")
        i, n = 0, len(lines)
        while i < n:
            raw = lines[i].rstrip("\r")
            line_no = i + 1
            # `#` 只在**行首**（允许前导空白）才是指令
            if raw.lstrip().startswith("#"):
                j = i
                parts: List[str] = []
                while j < n:
                    cur = lines[j].rstrip("\r")
                    if cur.endswith("\\"):
                        parts.append(cur[:-1])
                        j += 1
                        continue
                    parts.append(cur)
                    j += 1
                    break
                # **物理行数照旧保留**：续行占几行就补几个空行，后面的行号不漂。
                for _ in range(i, j):
                    self.out.append("")
                self._directive(" ".join(p.strip() for p in parts), line_no, raw)
                i = j
                continue
            if self._active():
                self.out.append(self._expand_line(raw, line_no))
            else:
                self.out.append("")
            i += 1

        if self.cond:
            f = self.cond[0]
            self._err("E-Lex-Preprocessor",
                      f"`{f.kind}` 没有配对的 `#endif`（第 {f.line} 行开始的条件块一直没关）",
                      f.line)

        text = "\n".join(self.out)
        for ln, notes in self.notes.items():
            slot = _NOTES.setdefault(ln, [])
            for nt in notes:
                if nt not in slot:
                    slot.append(nt)
        return PreprocResult(text=text, diagnostics=self.diags, notes=dict(self.notes))

    # -- 指令分派 --
    def _directive(self, logical: str, line_no: int, raw0: str):
        body = logical.lstrip()
        if not body.startswith("#"):
            return
        body = body[1:].lstrip()
        if body == "":
            return                       # 空指令 `#`：C++ 里合法，无操作
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", body)
        if not m:
            self._err("E-Lex-Preprocessor",
                      f"无法解析的预处理指令：`{logical.strip()}`（`#` 后面不是指令名）",
                      line_no)
            return
        name, rest = m.group(1), body[m.end():]

        # `#pragma` 不影响本语言语义：**丢弃**（现状不变）
        if name == "pragma":
            return
        # `#include` 不再无条件丢弃（2026-11-13）：标准头白名单外必须**开口**，
        # 否则 `check` 从没见过库的任何一行，却打印"通过"——最危险的看起来通过。
        if name == "include":
            self._include(rest, line_no, logical)
            return

        if name == "define":
            self._define(rest, line_no, raw0, logical)
            return
        if name == "undef":
            self._undef(rest, line_no, logical)
            return
        if name in ("ifdef", "ifndef"):
            self._ifdef(name, rest, line_no, logical)
            return
        if name == "if":
            self._if(rest, line_no, logical)
            return
        if name == "elif":
            self._elif(rest, line_no, logical)
            return
        if name == "else":
            self._else(line_no, logical)
            return
        if name == "endif":
            self._endif(line_no, logical)
            return
        if name == "error":
            if self._active():
                self._err("E-Lex-PreprocessorError",
                          f"`{logical.strip()}`：源文件在**预处理阶段**就被拒绝了。"
                          f"这是源码作者写下的 `#error`，不是本语言报的——"
                          f"删掉它，或让条件编译别选中这一支",
                          line_no)
            return

        # **不静默吞**：有语义的指令悄悄删掉，会让不该编的分支也生效。
        self._err("E-Lex-Preprocessor",
                  f"不支持的预处理指令：`{logical.strip()}`。\n"
                  f"支持的是 `#include`（标准头丢弃、其它头报"
                  f"`E-Lex-IncludeUnresolved`）、`#pragma`（丢弃）、"
                  f"`#define`/`#undef`、"
                  f"`#if`/`#ifdef`/`#ifndef`/`#elif`/`#else`/`#endif`、`#error`；"
                  f"`#{name}` 不在其中（`#line` 之类不带语义实现，删掉或改写）",
                  line_no)

    # -- #include --
    def _include(self, rest: str, line_no: int, logical: str):
        """标准头：静默丢弃（现状）；其它头：`E-Lex-IncludeUnresolved`。

        本语言**不读头文件**。丢掉非标准头 = 悄悄删掉用户的依赖，而 `check` 照样
        打印"通过"——用户会以为库接进来了，其实 `check` 从没见过库的任何一行。
        所以白名单外一律开口（2026-11-13）。
        """
        if not self._active():
            return                  # `#ifdef` 死分支里的 include 不处理（与 C++ 一致）
        text = rest.strip()
        m = re.match(r'<([^<>\s]+)>|"([^"\n]+)"', text)
        if not m:
            self._err(
                "E-Lex-IncludeUnresolved",
                f"`{logical.strip()}`：`#include` 后面要跟 `<头文件>` 或 `\"头文件\"`。"
                f"这一行已被**整行忽略**，什么也没引入。\n"
                f"本语言不读头文件；要用里面的符号，把真正需要的声明翻译成 `.tu`："
                f"`extern strlen(const vector<u8>&) -> u64 from \"ucrtbase\";` + "
                f"`link \"ucrtbase\";`，外部调用的成本写 `external {{ strlen: O(n) }};`。",
                line_no)
            return
        header = (m.group(1) or m.group(2)).strip()
        tail = text[m.end():].strip()
        if tail and not tail.startswith("//"):
            self._err(
                "E-Lex-IncludeUnresolved",
                f"`{logical.strip()}`：`#include {m.group(0)}` 后面还有多余内容 "
                f"`{tail}`。这一行已被**整行忽略**，什么也没引入。\n"
                f"`#include` 只接受 `<头文件>` 或 `\"头文件\"` 一种写法（不支持宏展开）。",
                line_no)
            return
        if header in STANDARD_HEADERS:
            return                  # 标准库头：静默丢弃（不影响语义，现状）
        self._err(
            "E-Lex-IncludeUnresolved",
            f"`{logical.strip()}`：本语言**不读头文件**，这一行已被**整行忽略**——"
            f"`{header}` 里的符号**一个都不可用**。\n"
            f"`check` 从头到尾没看过库的任何一行，所以「check 通过」**不能说明库接进来了**"
            f"（这正是以前静默丢弃最危险的地方：不报错的「通过」）。\n"
            f"要用它，把真正需要的声明翻译成 `.tu`：`extern f(…) -> T from \"库\";` + "
            f"`link \"库\";`，外部调用成本写 `external {{ f: O(n) }};`，"
            f"然后删掉这一行 `#include`。\n"
            f"标准库头（如 `#include <vector>`）不受影响；白名单是 "
            f"`preproc.STANDARD_HEADERS`。",
            line_no)

    # -- #define / #undef --
    def _define(self, rest: str, line_no: int, raw0: str, logical: str):
        if not self._active():
            return                       # 死分支里的宏不定义（与 C++ 一致）
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", rest.lstrip())
        if not m:
            self._err("E-Lex-Preprocessor",
                      f"`{logical.strip()}`：`#define` 后面要跟宏名", line_no)
            return
        name = m.group(1)
        after = rest.lstrip()[m.end():]
        # 定义位置：指**宏名**，不是 `#define`（`由宏 M（定义在 3:9）展开`）
        _base = raw0.find("define")
        _at = raw0.find(name, _base + 6) if _base >= 0 else -1
        col = _at + 1 if _at >= 0 else 1

        if after.startswith("("):       # **紧贴 `(`** 才是函数式宏（C++ 规则）
            close = after.find(")")
            if close < 0:
                self._err("E-Lex-Preprocessor",
                          f"`{logical.strip()}`：函数式宏的参数表没有 `)`", line_no)
                return
            raw_params = after[1:close]
            params = ([p.strip() for p in raw_params.split(",")]
                      if raw_params.strip() else [])
            for p in params:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p):
                    self._err("E-Lex-MacroUnsupported",
                              f"`{logical.strip()}`：不支持的宏参数 `{p}`"
                              f"（可变参数 `...` / `__VA_ARGS__` 不支持）", line_no)
                    return
            if len(set(params)) != len(params):
                self._err("E-Lex-MacroUnsupported",
                          f"`{logical.strip()}`：宏参数名重复", line_no)
                return
            body = after[close + 1:].strip()
            if _has_paste(body):
                self._err("E-Lex-MacroUnsupported",
                          f"`{logical.strip()}`：函数式宏里的 `#`（字符串化）/ `##`"
                          f"（记号粘贴）**不支持**。\n"
                          f"改法：把这一段写成具名函数；或手工展开成普通代码。"
                          f"（不静默降级——`##` 拼出来的记号会改变类型与成本）",
                          line_no)
                return
            self.macros[name] = Macro(name, params, body, line_no, col)
            return

        self.macros[name] = Macro(name, None, after.strip(), line_no, col)

    def _undef(self, rest: str, line_no: int, logical: str):
        name = rest.strip()
        if not name:
            self._err("E-Lex-Preprocessor",
                      f"`{logical.strip()}`：`#undef` 后面要跟宏名", line_no)
            return
        if self._active():
            self.macros.pop(name, None)

    # -- 条件编译 --
    def _ifdef(self, kind: str, rest: str, line_no: int, logical: str):
        name = rest.strip()
        if not name:
            self._err("E-Lex-Preprocessor",
                      f"`{logical.strip()}`：`#{kind}` 后面要跟宏名", line_no)
            return
        defined = name in self.macros
        value = defined if kind == "ifdef" else not defined
        parent = self._active()
        self.cond.append(_Cond(kind, parent, parent and value, line_no,
                               taken=parent and value))

    def _if(self, rest: str, line_no: int, logical: str):
        parent = self._active()
        value = False
        if parent:
            try:
                value = bool(self._eval_cond(rest))
            except _CondError as e:
                self._err("E-Lex-Preprocessor",
                          f"`{logical.strip()}`：条件表达式无法求值（{e}）", line_no)
                value = False
        self.cond.append(_Cond("#if", parent, parent and value, line_no,
                               taken=parent and value))

    def _elif(self, rest: str, line_no: int, logical: str):
        if not self.cond:
            self._err("E-Lex-Preprocessor",
                      f"`{logical.strip()}`：`#elif` 没有对应的 `#if`/`#ifdef`",
                      line_no)
            return
        f = self.cond[-1]
        if not f.parent_active or f.taken:
            f.active = False
            return
        try:
            value = bool(self._eval_cond(rest))
        except _CondError as e:
            self._err("E-Lex-Preprocessor",
                      f"`{logical.strip()}`：条件表达式无法求值（{e}）", line_no)
            value = False
        f.active = value
        if value:
            f.taken = True

    def _else(self, line_no: int, logical: str):
        if not self.cond:
            self._err("E-Lex-Preprocessor",
                      f"`{logical.strip()}`：`#else` 没有对应的 `#if`/`#ifdef`",
                      line_no)
            return
        f = self.cond[-1]
        f.active = f.parent_active and not f.taken
        if f.active:
            f.taken = True

    def _endif(self, line_no: int, logical: str):
        if not self.cond:
            self._err("E-Lex-Preprocessor",
                      f"`{logical.strip()}`：`#endif` 没有对应的 `#if`/`#ifdef`",
                      line_no)
            return
        self.cond.pop()

    # -- `#if` 表达式求值 --
    def _eval_cond(self, expr: str, depth: int = 0) -> int:
        if depth > MAX_DEPTH:
            raise _CondError(f"宏展开超过 {MAX_DEPTH} 层")
        toks = _cond_tokens(expr)
        if not toks:
            raise _CondError("空表达式")
        pos = [0]

        def peek() -> Optional[str]:
            return toks[pos[0]] if pos[0] < len(toks) else None

        def take() -> str:
            t = peek()
            if t is None:
                raise _CondError("表达式意外结束")
            pos[0] += 1
            return t

        def ident_value(word: str) -> int:
            mac = self.macros.get(word)
            if mac is None or mac.params is not None:
                return 0                      # 未定义 / 函数式宏 → 0（C++ 规则）
            body = mac.body.strip()
            if body == "":
                return 0
            return 1 if self._eval_cond(body, depth + 1) else 0

        def primary() -> int:
            t = peek()
            if t is None:
                raise _CondError("表达式意外结束")
            if t == "(":
                take()
                v = or_expr()
                if peek() != ")":
                    raise _CondError("括号没闭合")
                take()
                return v
            if t == "defined":
                take()
                if peek() == "(":
                    take()
                    w = take()
                    if peek() != ")":
                        raise _CondError("`defined(` 没闭合")
                    take()
                else:
                    w = take()
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", w or ""):
                    raise _CondError(f"`defined` 后面要跟宏名，得到 {w!r}")
                return 1 if w in self.macros else 0
            take()
            if t[0].isdigit():
                return _cond_num(t)
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", t):
                return ident_value(t)
            raise _CondError(f"不认识的记号 {t!r}")

        def unary() -> int:
            t = peek()
            if t in ("!", "-", "+", "~"):
                take()
                v = unary()
                return {"!": int(not v), "-": -v, "+": v, "~": ~v}[t]
            return primary()

        def mul() -> int:
            v = unary()
            while peek() in ("*", "/", "%"):
                op = take()
                r = unary()
                if op in ("/", "%") and r == 0:
                    raise _CondError("除数为 0")
                if op == "*":
                    v = v * r
                elif op == "/":
                    v = _c_div(v, r)
                else:
                    v = v - _c_div(v, r) * r
            return v

        def add() -> int:
            v = mul()
            while peek() in ("+", "-"):
                op = take()
                r = mul()
                v = v + r if op == "+" else v - r
            return v

        def shift() -> int:
            v = add()
            while peek() in ("<<", ">>"):
                op = take()
                r = add()
                v = v << r if op == "<<" else v >> r
            return v

        def cmp() -> int:
            v = shift()
            op = peek()
            if op in ("==", "!=", "<", ">", "<=", ">="):
                take()
                r = shift()
                v = int({"==": v == r, "!=": v != r, "<": v < r,
                         ">": v > r, "<=": v <= r, ">=": v >= r}[op])
            return v

        def band() -> int:
            v = cmp()
            while peek() == "&":
                take()
                v &= cmp()
            return v

        def bxor() -> int:
            v = band()
            while peek() == "^":
                take()
                v ^= band()
            return v

        def bor() -> int:
            v = bxor()
            while peek() == "|":
                take()
                v |= bxor()
            return v

        def and_expr() -> int:
            v = bor()
            while peek() == "&&":
                take()
                r = bor()
                v = int(bool(v) and bool(r))
            return v

        def or_expr() -> int:
            v = and_expr()
            while peek() == "||":
                take()
                r = and_expr()
                v = int(bool(v) or bool(r))
            return v

        value = or_expr()
        if peek() is not None:
            raise _CondError(f"多余的记号 {peek()!r}")
        return value

    # -- 普通代码行：宏展开 --
    def _expand_line(self, raw: str, line_no: int) -> str:
        if not raw.strip():
            return raw
        text, used = self._expand(raw, line_no, 0, ())
        for mac in used:
            note = _note_for(mac)
            slot = self.notes.setdefault(line_no, [])
            if note not in slot:
                slot.append(note)
        return text

    def _expand(self, text: str, line_no: int, depth: int,
                stack: Tuple[str, ...]) -> Tuple[str, List[Macro]]:
        out: List[str] = []
        used: List[Macro] = []
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch in "\"'":                     # 字符串/字符字面量里不展开
                j = _skip_quoted(text, i)
                out.append(text[i:j])
                i = j
                continue
            if ch == "/" and i + 1 < n and text[i + 1] == "/":
                out.append(text[i:])            # 行注释里不展开
                break
            if not (ch.isalpha() or ch == "_"):
                out.append(ch)
                i += 1
                continue

            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            name = text[i:j]
            mac = self.macros.get(name)
            if mac is None:
                out.append(name)
                i = j
                continue

            if mac.params is None:
                body, nxt = mac.body, j
            else:
                # 函数式宏：**必须**紧跟 `(`（允许中间空白）；否则不展开（C++ 规则）
                k = j
                while k < n and text[k] in " \t":
                    k += 1
                if k >= n or text[k] != "(":
                    out.append(name)
                    i = j
                    continue
                parsed = self._call_args(text, k, line_no, name)
                if parsed is None:
                    out.append(name)
                    i = j
                    continue
                args, nxt = parsed
                if len(args) != len(mac.params):
                    self._err("E-Lex-MacroArgCount",
                              f"宏 `{name}` 需要 {len(mac.params)} 个参数，"
                              f"这里给了 {len(args)} 个：`{text[i:nxt]}`",
                              line_no)
                    out.append(text[i:nxt])
                    i = nxt
                    continue
                body = self._subst(mac, args)

            if name in stack:
                self._err("E-Lex-MacroRecursion",
                          f"宏 `{name}` 递归展开（环：{' → '.join(stack + (name,))}）。"
                          f"宏展开必须有终止条件，这是环检测报的",
                          line_no)
                out.append(name)
                i = nxt
                continue
            if len(stack) >= MAX_DEPTH:
                self._err("E-Lex-MacroRecursion",
                          f"宏展开超过 {MAX_DEPTH} 层（链：{' → '.join(stack + (name,))}）。"
                          f"上限是 preproc.MAX_DEPTH，防止无限展开",
                          line_no)
                out.append(body)
                i = nxt
                continue
            sub, _ = self._expand(body, line_no, depth + 1, stack + (name,))
            out.append(sub)
            if depth == 0:
                used.append(mac)
            i = nxt
        return "".join(out), used

    def _call_args(self, text: str, k: int, line_no: int,
                   name: str) -> Optional[Tuple[List[str], int]]:
        args: List[str] = []
        cur: List[str] = []
        depth = 0
        i, n = k, len(text)
        while i < n:
            ch = text[i]
            if ch in "\"'":
                j = _skip_quoted(text, i)
                cur.append(text[i:j])
                i = j
                continue
            if ch == "(":
                depth += 1
                if depth > 1:
                    cur.append(ch)
                i += 1
                continue
            if ch == ")":
                depth -= 1
                if depth == 0:
                    args.append("".join(cur).strip())
                    i += 1
                    if len(args) == 1 and args[0] == "":
                        args = []
                    return args, i
                cur.append(ch)
                i += 1
                continue
            if ch == "," and depth == 1:
                args.append("".join(cur).strip())
                cur = []
                i += 1
                continue
            cur.append(ch)
            i += 1
        self._err("E-Lex-MacroUnterminated",
                  f"宏 `{name}` 的调用缺少 `)`：`{text[k:]}`"
                  f"（本语言不支持跨行宏调用）", line_no)
        return None

    def _subst(self, mac: Macro, args: List[str]) -> str:
        """宏体里的参数名**按记号**换成实参。"""
        body = mac.body
        out: List[str] = []
        i, n = 0, len(body)
        while i < n:
            ch = body[i]
            if ch in "\"'":
                j = _skip_quoted(body, i)
                out.append(body[i:j])
                i = j
                continue
            if ch.isalpha() or ch == "_":
                j = i + 1
                while j < n and (body[j].isalnum() or body[j] == "_"):
                    j += 1
                word = body[i:j]
                if word in (mac.params or []):
                    out.append(args[(mac.params or []).index(word)])
                else:
                    out.append(word)
                i = j
                continue
            out.append(ch)
            i += 1
        return "".join(out)


# ---------------------------------------------------------------- `#if` 词法

_COND_TOKEN = re.compile(
    r"defined"
    r"|0[xX][0-9a-fA-F]+"
    r"|[0-9]+[uUlL]*"
    r"|[A-Za-z_][A-Za-z0-9_]*"
    r"|&&|\|\||==|!=|<=|>=|<<|>>"
    r"|[()!<>+\-*/%&|^~]"
)


def _cond_tokens(s: str) -> List[str]:
    toks: List[str] = []
    i, n = 0, len(s)
    while i < n:
        if s[i].isspace():
            i += 1
            continue
        m = _COND_TOKEN.match(s, i)
        if not m:
            raise _CondError(f"不认识的记号 {s[i:i + 8]!r}")
        toks.append(m.group(0))
        i = m.end()
    return toks


def _cond_num(text: str) -> int:
    t = text.rstrip("uUlL")
    if t[:2].lower() == "0x":
        return int(t, 16)
    if len(t) > 1 and t[0] == "0" and t.isdigit():
        return int(t, 8)            # C++ 里 `017` 是八进制
    return int(t)


def _c_div(a: int, b: int) -> int:
    """C 的整除：向 0 截断（Python 的 `//` 向下取整，负数会不一样）。"""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


# ---------------------------------------------------------------- 对外入口

def preprocess(src: str, path: str = "<input>",
               defines: Optional[Dict[str, str]] = None) -> PreprocResult:
    """**先预处理再分析**：返回行号不变的展开文本 + 诊断 + 来源表。

    `defines=None` 时用 `set_defines()` 设的命令行定义（默认空）。
    """
    if defines is None:
        defines = dict(_ACTIVE_DEFINES)
    return _Preproc(src, path, defines).run()


def annotate(diags) -> None:
    """给落在**宏展开行**上的诊断补一句来源（就地改 `message`）。

    形如：`由宏 `M`（定义在 12:9）展开`。已经带来源的诊断不重复追加。
    行号对不上（比如宏定义处的错误）就原样不动。
    """
    if not _NOTES:
        return
    for d in diags or []:
        line = getattr(d, "line", 0) or 0
        notes = _NOTES.get(line)
        if not notes:
            continue
        msg = getattr(d, "message", "") or ""
        if "由宏" in msg:
            continue
        sep = "" if msg.endswith("\n") else "\n"
        try:
            d.message = msg + sep + "；".join(notes) + "。"
        except Exception:                                  # noqa: BLE001
            continue
