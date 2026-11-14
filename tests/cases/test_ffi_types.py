#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_ffi_types.py —— C ABI 后端的**类型集**：`f64`、指针返回、以及"回调不做"

## 这一批补的是**类型**，不是写法

三个真库各撞一堵墙（2026-11-14 实测，`build/real-libs/` 下的 tinyexpr / cJSON / inih）：

| 库 | 撞的墙 | 表现 |
|---|---|---|
| tinyexpr `double te_interp(const char*, int*)` | **没有 `f64` 返回** | `E-FFI-Unsupported：C ABI 后端不支持返回类型 f64`，整个 API 死 |
| cJSON `const char* cJSON_Version(void)` | **没有指针返回** | `E-FFI-Unsupported：C ABI 后端不支持返回类型 vector` |
| inih `int ini_parse(..., int (*handler)(...), ...)` | **没有函数指针形参** | `E-FFI-Unsupported：后端不认识声明类型 ?`（类型名还打不出来） |

`shape.py` 早就放行了 `f64`（`_check_one` 的浮点分支），卡住的只是**后端这张表**，
所以 `f64` 只改 `ffi_c.py` 就够。指针返回选 `Vec<u8>`（NUL 结尾的字节串、复制成
`Text`），不选"不透明句柄 u64"——理由写在 `src/ffi_c.py` 的文件头里。
回调**不做**：本语言没有一等函数，而且回调是任意代码，κ 会变成谎话
（仓库已经因为同一个理由拒绝 `sort` 的自定义比较器）。

## 这里的断言为什么必须跑**真库**

只测 `argtype_for("f64") == c_double` 证明不了任何事：`ctypes` 那一版真能编组
吗？`to_native` 会不会把 3.5 截成 3？所以下面每一条都**真调进 C**，用
`ucrtbase`（POSIX 是 `libc` / `libm`）里名字稳定的那几个函数：

| 用来验的 C 函数 | 证明什么 |
|---|---|
| `pow(2.5, 2.0) == 6.25` | `f64` 形参**没被截成整数**（截了就是 `pow(2,2)=4`） |
| `sqrtf(9.0) == 3.0` | `f32` 形参与返回 |
| `getenv(name)` → `vector<u8>` | 指针返回读出了 NUL 结尾的字符串 |
| `getenv(不存在的名字)` | 空指针 → **明确失败**，不许静默变成 `None` / `""` |
| `getenv(name)` → `u64` | 同一函数的"不透明句柄"路：空指针可判（`== 0`） |
| `ini_parse` 的 `func(...)` 形参 | 仍然**明说**不支持，没有假装支持 |
"""
import os as _os
import sys as _sys

#: 文件在 `tests/cases/`，但**这批测试原先住在 `src/`**，里面大量
#: `os.path.join(HERE, "..", "文档")` / `os.path.join(ROOT, "tools")` 都假设
#: 「本文件在 src/、上一级是仓库根」。搬家时改 200 处路径比让 `HERE` 继续指 src 贵得多，
#: 所以保留旧语义：`HERE` = src/，`CASEDIR` = 本文件真正所在的目录。
CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
HERE = SRC
for _p in (SRC, HERE):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import ctypes
import io
import os
import sys

import ffi_c
import shape
from interp import Interp, Text
from parser import parse_source

FAILED = []

#: 与 `test_ffi.py` 同一口径：Windows 走 `ucrtbase`，POSIX 走 `libc`。
#: `pow` / `sqrtf` 在 POSIX 上属于 `libm`（glibc 里 libc.so 并不导出它们）。
LIB = "ucrtbase" if sys.platform == "win32" else "libc"
MATH_LIB = LIB if sys.platform == "win32" else "libm"

#: 真库测试要一个**确定非空**的环境变量（值里带空格，顺便验字节没被切开）。
ENV_NAME = "ELENCH_FFI_TYPES_PROBE"
ENV_VAL = "ffi types ok"


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(tu_src: str, eln_src: str, fn: str, args=None):
    tu = parse_source(tu_src, "t.tu").module
    eln = parse_source(eln_src, "t.eln").module
    return Interp(eln, tu).call(fn, list(args or []))


def raises(fn, *a):
    """跑 fn(*a)，返回异常（没抛就返回 None）。"""
    try:
        fn(*a)
    except BaseException as exc:                      # noqa: BLE001
        return exc
    return None


def extern(name_and_params: str, ret: str, lib: str = None) -> str:
    l = lib or LIB
    return f'extern {name_and_params} -> {ret} from "{l}" effects {{}};\n'


# --------------------------------------------------------------------- 类型表
def test_argtable():
    print("[1] 形参类型表：加的是 f64/f32，没动的照样在")
    check("u64 → c_uint64", ffi_c.argtype_for("u64") is ctypes.c_uint64)
    check("i64 → c_int64", ffi_c.argtype_for("i64") is ctypes.c_int64)
    check("bool → c_uint64（C 没有稳定 bool ABI）",
          ffi_c.argtype_for("bool") is ctypes.c_uint64)
    check("Vec<u8>& → c_char_p", ffi_c.argtype_for("vector<u8>&") is ctypes.c_char_p)
    check("array<u8,4> → c_char_p", ffi_c.argtype_for("array<u8, 4>") is ctypes.c_char_p)
    check("f64 → c_double", ffi_c.argtype_for("f64") is ctypes.c_double)
    check("f32 → c_float", ffi_c.argtype_for("f32") is ctypes.c_float)

    e = raises(ffi_c.argtype_for, "float")
    check("`float` 仍报 E-FFI-Unsupported（C 的拼写不是语言类型；f64 才是）",
          isinstance(e, shape.UnsupportedError) and "E-FFI-Unsupported" in str(e), str(e))
    e = raises(ffi_c.argtype_for, "map<u64, u64>")
    check("map → E-FFI-Unsupported", isinstance(e, shape.UnsupportedError), str(e))
    # `i32` 卡在 shape.py（只认 u64/i64），后端加映射也到不了，所以**不能**在这里假装有
    e = raises(shape.validate, "g", [("x", "i32")], [1])
    check("i32 形参在 shape.py 就报 E-FFI-Unsupported（不是后端能补的）",
          isinstance(e, shape.UnsupportedError), str(e))


def test_rettype():
    print("[2] 返回类型表：指针返回不能退化成 c_char_p（NULL 会变 None）")
    check("空返回 = void", ffi_c.restype_for("") is None)
    check("u64 → c_uint64", ffi_c.restype_for("u64") is ctypes.c_uint64)
    check("i64 → c_int64", ffi_c.restype_for("i64") is ctypes.c_int64)
    check("f64 → c_double", ffi_c.restype_for("f64") is ctypes.c_double)
    check("f32 → c_float", ffi_c.restype_for("f32") is ctypes.c_float)
    check("Vec<u8> → _CString", ffi_c.restype_for("vector<u8>") is ffi_c._CString)
    # 外部调用路径（`interp._load_ffi`）到这里只剩头部名 `vector`
    check("头部名 vector 也认（extern 路径只传头部名）",
          ffi_c.restype_for("vector") is ffi_c._CString)
    check("[u8] 也认", ffi_c.restype_for("[u8]") is ffi_c._CString)
    check("**不是** c_char_p（那样 NULL 会静默变成 None）",
          ffi_c.restype_for("vector<u8>") is not ctypes.c_char_p)
    e = raises(ffi_c.restype_for, "map<u64, u64>")
    check("不认识 → E-FFI-Unsupported", isinstance(e, shape.UnsupportedError), str(e))


def test_to_native():
    print("[3] 编组：浮点必须原样过（以前统一 int(a)，3.5 → 3）")
    check("3.5 → 3.5", ffi_c.to_native([3.5]) == [3.5])
    check("-0.5 → -0.5", ffi_c.to_native([-0.5]) == [-0.5])
    check("7 → 7（整数不变成浮点）", ffi_c.to_native([7]) == [7])
    check("True → 1", ffi_c.to_native([True]) == [1])
    check("b'hi' → b'hi'", ffi_c.to_native([b"hi"]) == [b"hi"])
    check("'hi' → b'hi'", ffi_c.to_native(["hi"]) == [b"hi"])
    check("Text → 字节", ffi_c.to_native([Text(b"hi")]) == [b"hi"])
    check("[104, 105] → b'hi'", ffi_c.to_native([[104, 105]]) == [b"hi"])


# --------------------------------------------------------------------- 真库
def test_f64_real():
    print("[4] 真库：f64 / f32 形参与返回（截断就会露馅）")
    tu = ("spec h() -> f64 { effects {}; }\n"
          "spec s() -> f32 { effects {}; }\n")
    eln = (extern("pow(f64 x, f64 y)", "f64", MATH_LIB)
           + extern("sqrtf(f32 x)", "f32", MATH_LIB)
           + "f64 h() { unsafe { return pow(2.5, 2.0); } }\n"
           + "f32 s() { unsafe { return sqrtf(9.0); } }\n")
    got = run(tu, eln, "h")
    check("pow(2.5, 2.0) == 6.25（形参被截成整数就会得到 4.0）", got == 6.25, str(got))
    check("返回值真的是 float（不是 int）", isinstance(got, float), type(got).__name__)
    got = run(tu, eln, "s")
    check("sqrtf(9.0) == 3.0（f32 形参与返回）", got == 3.0, str(got))


def _env_tu(extra: str = "") -> str:
    return ("spec f() -> vector<u8> { effects {}; }\n"
            "spec g() -> u64 { effects {}; }\n"
            "spec k() -> u64 { effects {}; }\n" + extra)


def test_ptr_return_real():
    print("[5] 真库：指针返回 = NUL 结尾的字节串（getenv）")
    os.environ[ENV_NAME] = ENV_VAL
    eln = (extern(f"getenv(const vector<u8>& name)", "vector<u8>")
           + f'vector<u8> f() {{ unsafe {{ return getenv("{ENV_NAME}"); }} }}\n')
    got = run(_env_tu(), eln, "f")
    check("拿到的是 Text（vector<u8> 的运行时形态）", isinstance(got, Text), type(got).__name__)
    check(f'内容 == "{ENV_VAL}"', bytes(got or b"").decode("utf-8") == ENV_VAL,
          repr(got))
    check("长度对（没把结尾的 0 字节算进来）", len(got) == len(ENV_VAL), str(len(got)))

    print("[6] 真库：返回的 Text 能当 vector<u8> 用（push / 比较 / 打印）")
    eln2 = (extern("getenv(const vector<u8>& name)", "vector<u8>")
            + f'u64 g() {{\n    unsafe {{\n'
              f'        vector<u8> v = getenv("{ENV_NAME}");\n'
              f'        u64 a = v.length();\n'
              f'        v.push(33);\n'
              f'        print(v);\n'
              f'        return a * 1000 + v.length();\n'
              f'    }}\n}}\n')
    buf = io.StringIO()
    _old = sys.stdout
    sys.stdout = buf
    try:
        got2 = run(_env_tu(), eln2, "g")
    finally:
        sys.stdout = _old
    check("push 生效（长度 +1）", got2 == len(ENV_VAL) * 1000 + len(ENV_VAL) + 1, str(got2))
    check("print 按文本打（不是 [102, 102, ...]）",
          buf.getvalue().strip() == ENV_VAL + "!", repr(buf.getvalue()))

    eln3 = (extern("getenv(const vector<u8>& name)", "vector<u8>")
            + f'u64 g() {{\n    unsafe {{\n'
              f'        vector<u8> v = getenv("{ENV_NAME}");\n'
              f'        if (v == "{ENV_VAL}") {{ return 1; }}\n'
              f'    }}\n    return 0;\n}}\n')
    check("能和字符串字面量比较（bytes 就不行：bytes != list）",
          run(_env_tu(), eln3, "g") == 1)


def test_null_real():
    print("[7] 真库：空指针 → 明确失败（不许静默变成 None / 空串）")
    eln = (extern("getenv(const vector<u8>& name)", "vector<u8>")
           + 'vector<u8> f() { unsafe { return getenv("ELENCH_NO_SUCH_VAR_ZZZ"); } }\n')
    e = raises(run, _env_tu(), eln, "f")
    check("抛出来（不是返回 None / b''）", e is not None, "竟然没抛")
    check("是 E-FFI-Unsupported（返回值映射表达不了 NULL，不是调用写错）",
          "E-FFI-Unsupported" in str(e), str(e)[:120])
    check("消息教了两条出路（u64 判空 / 换调用）",
          "u64" in str(e) and "空指针" in str(e), str(e)[:160])

    print("[8] 真库：同一个函数走「不透明句柄 u64」路 → 空指针可判（== 0）")
    eln2 = (extern("getenv(const vector<u8>& name)", "u64")
            + 'u64 k() { unsafe { return getenv("ELENCH_NO_SUCH_VAR_ZZZ"); } }\n')
    check("不存在的变量 → 0", run(_env_tu(), eln2, "k") == 0)
    eln3 = (extern("getenv(const vector<u8>& name)", "u64")
            + f'u64 k() {{ unsafe {{ u64 h = getenv("{ENV_NAME}"); '
              f'if (h == 0) {{ return 0; }} return 1; }} }}\n')
    check("存在的变量 → 非 0（句柄路能表达「有指针」）", run(_env_tu(), eln3, "k") == 1)


def test_callback_not_done():
    print("[9] 函数指针形参：仍然明说「不支持」，没有假装支持")
    tu = ("spec f() -> i64 { effects {}; }\n"
          "spec cb(u64 user) -> i64 { effects {}; }\n")
    eln = (extern("ini_like(const vector<u8>& p, func(u64) -> i64 handler, u64 user)", "i64")
           + 'i64 f() { unsafe { return ini_like("x.ini", 0, 0); } }\n'
           + "i64 cb(u64 user) { return 0; }\n")
    e = raises(run, tu, eln, "f")
    check("抛 E-FFI-Unsupported", e is not None and "E-FFI-Unsupported" in str(e),
          str(e)[:160])

    # 传**具名函数**也到不了 C：外部调用这条路上，名字不会被变成函数值
    eln2 = (extern("ini_like(const vector<u8>& p, func(u64) -> i64 handler, u64 user)", "i64")
            + 'i64 f() { unsafe { return ini_like("x.ini", cb, 0); } }\n'
            + "i64 cb(u64 user) { return 0; }\n")
    e2 = raises(run, tu, eln2, "f")
    check("传具名函数也不被静默接受（这条路没有「函数值」）",
          e2 is not None, "竟然没抛")


def main() -> int:
    test_argtable()
    test_rettype()
    test_to_native()
    test_f64_real()
    test_ptr_return_real()
    test_null_real()
    test_callback_not_done()
    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
