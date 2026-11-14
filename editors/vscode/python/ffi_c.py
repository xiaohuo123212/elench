#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""ffi_c.py —— FFI 的 **C ABI 后端**：只负责"合法值 → ctypes 参数"。

校验在 `shape.py`（与后端无关）。这里只做**编组**。

## 类型表（唯一一份，改这里）

| `.tu` 类型 | C 类型 | 形参 ctypes | 返回 ctypes |
|---|---|---|---|
| `u64` | `uint64_t` | `c_uint64` | `c_uint64` |
| `i64` | `int64_t` | `c_int64` | `c_int64` |
| `bool` | `int`（C 没有稳定的 `bool` ABI） | `c_uint64` | `c_uint64` |
| `f64` | `double` | `c_double` | `c_double` |
| `f32` | `float` | `c_float` | `c_float` |
| `Vec<u8>` / `[u8]` / `array<u8, N>` | `const char*`（NUL 结尾的字节串） | `c_char_p` | `_CString`（→ `Text`） |

**不认的类型抛 `E-FFI-Unsupported`**——这是后端能力问题，不是调用者写错，
所以不能报成 `E-FFI-Shape`（否则人会去改一个没写错的调用）。

## 为什么 `f64` 只改这个文件就够了

`shape.py` 早就放行了 `f64` / `f32` / `float`（`_check_one` 的浮点分支），
卡住的只是**后端这张表**。所以加 `f64` 是纯编组问题：表里一行。
`i32` / `u32` 这一档反过来——卡在 `shape.py`（那里只认 `u64`/`i64`），
本文件加映射也不会被调用到，所以**没加**，也不该在这里假装支持。

## 指针返回为什么映射成 `Vec<u8>`（而不是"不透明句柄 u64"）

两条路都试过，代价不同，**选 `Vec<u8>`**：

| | `Vec<u8>`（本文件新支持） | 不透明句柄 `u64`（今天就能写） |
|---|---|---|
| C 里是什么 | `const char*`，**NUL 结尾** | 任何指针（`cJSON*` / `void*`） |
| 拿到之后能干什么 | 打印、按字节比较、求长度、再传给别的 C 函数 | 只能**原样还给别的 C 函数**、和 `0` 比 |
| 能不能读内容 | ✅ | ❌ 语言里没有解引用 / memcpy 内建 |
| NULL 怎么办 | **报错**（没有对应的值，见 `_CString`） | `== 0` 就是空指针，可静态判断 |
| 代价 | 复制一份字节（原指针不 `free`，见下） | 读不到内容；值是个地址，`print` 出来是数字 |

理由：`cJSON_Version()` 这类返回 `const char*` 的 API，**唯一用途就是读**；
句柄路能表达它、却永远读不出那个字符串——那是"看起来支持了、其实什么也做不了"。
而句柄路真正的用途（`cJSON_Parse() -> cJSON*` 再传给 `cJSON_Print`）**今天已经能写**：
把返回类型声明成 `u64` 即可（`c_uint64` 与指针同一个寄存器，测试里实测可用）。
所以这里补的是**缺的那一条**，不是把另一条挤掉。

**代价要说清**（不藏着）：

1. **只对 NUL 结尾的字节串成立**。返回 `void*`（缓冲区）却声明 `Vec<u8>`，
   会一直读到最近的一个 0 字节——读到什么都算"合法"，那是静默错值。
2. **复制，不接管所有权**。`cJSON_Print()` 返回的是 `malloc` 出来的字符串，
   复制完原指针就丢了，**那是一次内存泄漏**。要自己释放就只有句柄路：
   把返回类型声明成 `u64`，再把句柄交给 `free`（一条 `extern`）。
3. **NULL 直接报错**，不折叠成 `""` 或 `None`：`""` 是一个合法字符串，
   把它和"没有字符串"混成一个值，正是这个项目最反对的那类静默错值。

## 形参的 `vector<T>` 一律编成 `const char*`（已知洞）

外部调用路径（`interp._load_ffi`）到这里只传**头部名** `vector`，元素类型已经丢了，
所以 `vector<u64>` 形参也会被当成 `const char*`（再按 `& 0xFF` 逐字节截断）。
这是**真的洞**，但堵不住：要堵得先让 `interp.py` 把 `vector<u8>` 完整传下来
（不在本轮改动范围内）。现在的实用规则是"**形参只写 `Vec<u8>`**"。

## 函数指针形参：**不做**

`ini_parse(..., int (*handler)(...), ...)` 这类回调没有映射，
`shape.py` 会在到达本文件之前就报 `E-FFI-Unsupported`。为什么不做，见
`文档/内建函数.md` 的「FFI 类型表」一节：① 本语言没有一等函数，
形参只能传**具名函数**，而 C 需要的是一个**能被 C 调用的代码地址**；
② 真要做，得在 `interp` 里注册 `ctypes.CFUNCTYPE` 桩并在 C 回调进来时
重新进入解释器——那是 `interp.py` 的改动，不是编组表的改动；
③ 更根本的是**成本**：回调是任意代码，κ 会变成一句谎话。仓库已经因为
同一个理由拒绝了 `sort` 的自定义比较器（比较器是任意代码，成本不可静态界定）。

## 多语言怎么办

**加一个后端文件，不动校验器**：`ffi_py.py` / `ffi_wasm.py` 各提供同样的
`argtype_for` / `restype_for` / `to_native`。设计见
`文档/设计/目前设计/未做/挂起/挂起-多语言FFI编组.md`。
"""
from __future__ import annotations

from typenames import canon, display
import ctypes
import os
import sys
from typing import Any, List, Sequence, Tuple

from shape import UnsupportedError


# ---------------------------------------------------------------- 动态库解析
#
# ## 为什么需要这一层（2026-11-14）
#
# `ctypes.CDLL("cjson")` 在 Python 3.8+ 的 Windows 上**不会**去 cwd / PATH 里找
# （`CDLL` 走的是 `LoadLibraryEx` 的受限搜索口径），所以 `extern … from "cjson"`
# 里的**裸名**永远加载不了：同一个 `.eln`，`dsh cpp --run` 那条路能跑，
# 解释器侧只能报「无法对拍」。`dsh run --lib-dir=<目录>` 就是给裸名一条
# **确定、可复现**的路。
#
# ## 搜索顺序（写进文档，别让人猜）
#
# 1. **绝对路径 / 带路径分隔符的写法**：原样用，**不补后缀**（路径已经写全了）；
# 2. **裸名**：按 `--lib-dir=` 的给出顺序，每个目录下先试 `<名字><平台后缀>`
#    （Windows `.dll` / Linux `.so` / macOS `.dylib`），再试 `<名字>`；
# 3. 最后**原样**把裸名交给平台加载器——`ucrtbase` / `libc` 这类**系统库**
#    只能这样找，而且这是加 `--lib-dir` 之前唯一的旧行为。
#
# **不额外搜 cwd、不额外搜 PATH**：本模块不把这两者拼进候选表。它们的出现只可能
# 来自第 3 步那个平台加载器自己；因为第 3 步排在所有 `--lib-dir` 之后，
# **给了 `--lib-dir` 就一定优先**（"跑的是哪个库"可控）。
#
# ## 为什么失败时要列候选
#
# 旧文案只有 ctypes 的一句 `Could not find module 'cjson'`——用户不知道 dsh 到底
# 去哪儿找过。现在把**试过的每个路径**按顺序列出来，并区分「文件不存在」
# 与「文件在、加载失败（位数/依赖不对）」。


def lib_suffixes() -> Tuple[str, ...]:
    """平台动态库后缀（**顺序就是尝试顺序**；macOS 再退 `.so` 是 Homebrew 习惯）。"""
    if os.name == "nt":
        return (".dll",)
    if sys.platform == "darwin":
        return (".dylib", ".so")
    return (".so",)


def _has_lib_suffix(name: str) -> bool:
    low = (name or "").lower()
    return any(low.endswith(s) for s in (".dll", ".so", ".dylib"))


def library_candidates(name: str, lib_dirs: Sequence[str] = ()) -> List[str]:
    """`from "名字"` + `--lib-dir=` → **按顺序**要试的路径（纯函数，好测）。

    `name` 为空串表示「进程自身的符号」（`CDLL(None)`），返回空列表。
    """
    name = name or ""
    if not name:
        return []
    # 1. 路径写法：原样，不补后缀
    if os.path.isabs(name) or os.sep in name or (os.altsep and os.altsep in name):
        return [name]
    # 2. 每个 --lib-dir：先加后缀，再不加后缀
    out: List[str] = []
    for d in (lib_dirs or ()):
        d = str(d or "")
        if not d:
            continue
        if _has_lib_suffix(name):
            out.append(os.path.join(d, name))
        else:
            for suf in lib_suffixes():
                out.append(os.path.join(d, name + suf))
            out.append(os.path.join(d, name))
    # 3. 原样交给平台加载器（系统库 / 旧脚本）
    out.append(name)
    seen = set()
    uniq: List[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def _why_failed(cand: str, name: str, exc: OSError) -> str:
    """一条候选失败的原因：能判「文件不存在」就直说，否则给加载器原话。"""
    looks_path = os.path.isabs(cand) or os.sep in cand \
        or bool(os.altsep and os.altsep in cand)
    if looks_path and not os.path.exists(cand):
        return "找不到该文件"
    msg = str(exc).strip().replace("\r", " ").replace("\n", " ")
    if cand == name:
        return "平台加载器：" + msg
    return msg


def _load_failure(name: str, tried) -> str:
    lines = [f"找不到外部库 `{name}`：按顺序试过 {len(tried)} 个路径，都不行："]
    for i, (cand, why) in enumerate(tried, 1):
        lines.append(f"  {i}. {cand} —— {why}")
    lines.append("修法：① 用 `--lib-dir=<目录>`（可重复）指定库所在目录；"
                 "② 或把 `from \"…\"` 写成绝对路径。")
    lines.append("（顺序固定：绝对路径/带路径的写法 → 每个 --lib-dir 下先 `<名字><后缀>` "
                 "再 `<名字>` → 最后裸名交给平台加载器；不额外搜 cwd、不额外搜 PATH）")
    return "\n".join(lines)


def load_library(name: str, lib_dirs: Sequence[str] = ()):
    """按 `library_candidates` 的顺序加载；全失败抛 `OSError`（消息含每个候选）。

    `name` 为空 → `ctypes.CDLL(None)`（进程自身符号），与旧行为一致。
    """
    cands = library_candidates(name, lib_dirs)
    if not cands:
        return ctypes.CDLL(None)
    tried = []
    for cand in cands:
        try:
            return ctypes.CDLL(cand)
        except OSError as exc:
            tried.append((cand, _why_failed(cand, name, exc)))
    raise OSError(_load_failure(name, tried))


#: 标量：`.tu` 类型名 → ctypes 类型。**形参与返回共用一张表**，
#: 所以加减一个标量只改这里一处（`bool` 走 `c_uint64` 是 C 侧没有稳定 bool ABI 的历史选择）。
#:
#: 没有 `float` / `i32` / `u32`：
#:   · `float` 是 C 的拼写，语言的类型名是 `f64`；`argtype_for("float")` 必须继续报
#:     `E-FFI-Unsupported`（`tests/cases/test_ffi_shape.py` 钉住了这条）；
#:   · `i32`/`u32` 卡在 `shape.py`（只认 `u64`/`i64`），这里加映射也到不了。
_SCALAR = {
    "u64": ctypes.c_uint64,
    "i64": ctypes.c_int64,
    "bool": ctypes.c_uint64,
    "f64": ctypes.c_double,
    "f32": ctypes.c_float,
}


def _is_bytes_ptr(ty: str) -> bool:
    """`Vec<u8>` / `[u8]` / `array<u8, N>`（以及只有头部名的 `vector`）→ `const char*`。

    **名字后面可能跟着 `<…>` 和 `&`**（`Vec<u8>&`），所以不能直接 `canon(t)`：
    只认整词会漏掉带修饰的写法。先取头部再规范化（与 `shape._is_vec` 同一口径）。
    """
    t = ty or ""
    _head = t.split("<")[0].split("&")[0].strip()
    return canon(_head) in ("vector", "array") or t.startswith("[")


def argtype_for(ty: str):
    """`.tu` 声明类型 → ctypes 参数类型。"""
    if _is_bytes_ptr(ty):
        return ctypes.c_char_p
    head = (ty or "").split("<")[0].split("&")[0].strip()
    if head in _SCALAR:
        return _SCALAR[head]
    raise UnsupportedError(f"C ABI 后端不支持参数类型 {ty}")


def restype_for(ty: str):
    """`.tu` 返回类型 → ctypes 返回类型（空表示 void）。

    `Vec<u8>` 走 `_CString`：它先按指针收下返回值，再在 `_check_retval_` 里
    读成 NUL 结尾的字节。**不用 `c_char_p`**——那样 NULL 会变成一个
    Python `None` 一路流进语言里，打印成 `None`，静默错值。
    """
    if not ty:
        return None
    if _is_bytes_ptr(ty):
        return _CString
    head = (ty or "").split("<")[0].split("&")[0].strip()
    if head in _SCALAR:
        return _SCALAR[head]
    raise UnsupportedError(f"C ABI 后端不支持返回类型 {ty}")


def _as_text(raw: bytes):
    """字节 → `vector<u8>` 的运行时形态。

    `Text` 在 `interp.py` 里（这里不能顶层 import：`interp` 会在函数内部
    import 本模块，顶层互相 import 会撞上半初始化的模块）。拿不到就退回
    `bytes`——打印结果一样（`_text_of` 两种都按 UTF-8 文本渲染），只是
    `push` 这类原地修改会失败。
    """
    try:
        from interp import Text
    except Exception:                                   # noqa: BLE001
        return raw
    return Text(raw)


class _CString(ctypes.c_void_p):
    """C 的 `const char*`（NUL 结尾）——**收完返回值再转成字节**。

    为什么用 `ctypes` 的 `_check_retval_` 钩子而不是直接 `restype = c_char_p`：
    后者把 NULL 变成 `None`，而 `None` 在语言里没有对应值，会一路走成
    `print(None)`。这里把"指针为空"变成**一次明确的失败**：

        E-FFI-Unsupported：外部函数返回了空指针（NULL）……

    把它归到 `E-FFI-Unsupported` 而不是 `E-FFI-Shape`：调用点没写错，
    是**返回值映射表达不了 NULL**，跟"这个类型映射不了"同一档。

    `_check_retval_` 是 ctypes 在**调用返回之后**调的钩子（收的是 `c_void_p`
    实例或 `None`，不是 32 位截断后的 int——`restype=` 直接给一个 Python
    函数时会被截成 32 位，所以那条路走不通）。`tests/cases/test_ffi_types.py`
    钉住了这个行为，换 Python 版本若它变了，测试会红。
    """

    @classmethod
    def _check_retval_(cls, value):
        addr = 0
        if value is not None:
            try:
                addr = int(value.value or 0)
            except (TypeError, ValueError):             # 拿不到地址就当空指针
                addr = 0
        if not addr:
            raise UnsupportedError(
                "外部函数返回了空指针（NULL），但返回类型 `Vec<u8>` 只能表示"
                "「一段 NUL 结尾的字节」——NULL 没有对应的值。"
                "这是**返回值映射**的问题（不是调用写错）：要判空就把返回类型"
                "声明成 `u64`（空指针 = 0），要读内容就换一个会返回非空指针的调用")
        return _as_text(ctypes.string_at(addr))


def to_native(args: List[Any]) -> List[Any]:
    """把**已通过校验**的值编组成 ctypes 能吃的形状。"""
    out = []
    for a in args:
        if isinstance(a, (bytes, bytearray)):
            out.append(bytes(a))
        elif isinstance(a, str):
            out.append(a.encode("utf-8"))
        elif isinstance(a, (list, tuple)):
            # `vector<u8>` 的运行时形态 `Text` 也是 list 子类，走同一条路。
            out.append(bytes(int(x) & 0xFF for x in a))
        elif isinstance(a, bool):
            out.append(int(a))
        elif isinstance(a, float):
            # **浮点必须原样过**：以前统一 `int(a)`，`f64` 形参会被截断成整数
            # （3.5 → 3），正是"看起来能传、其实传错了"。
            out.append(a)
        else:
            out.append(int(a))
    return out
