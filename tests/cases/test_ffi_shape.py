#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_ffi_shape.py —— FFI 调用前的形状校验（`src/shape.py` + `src/ffi_c.py`）。

## 守什么

1. **三类调用错误**（个数 / 类型 / 取值）在**加载外部库之前**就被拦住；
2. **`E-FFI-Shape` 与 `E-FFI-Unsupported` 不混**：前者是调用者写错，后者是后端没能力；
3. **负数不许静默回绕**：`u64` 传 `-1` 以前会变成 18446744073709551615。

这些用例**都不需要真实外部库**——校验发生在 `ctypes.CDLL` 之前。
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

import sys

import ffi_c
import shape
from checker import param_list
from interp import Interp, InterpError
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


TU = """
spec strlen(const Vec<u8>& s) -> u64 {
    effects { fs };
    external "ucrtbase";
}

spec add1(u64 n) -> u64 {
    effects {};
    external "ucrtbase";
}

spec weird(float x) -> u64 {
    effects {};
    external "ucrtbase";
}
"""

ELN = """
func f() -> u64 {
    return 0;
}
"""


def make_interp():
    tu = parse_source(TU, "t.tu").module
    eln = parse_source(ELN, "t.eln").module
    return Interp(eln, tu), tu


def spec_node(tu, name):
    return next((n for n in tu.children if n.kind == "spec" and n.name == name), None)


def test_validate_units():
    print("[1] 纯校验器：个数 / 类型 / 取值")
    params = [("s", "Vec<u8>&")]
    check(shape.validate("strlen", params, [b"abc"]) == [b"abc"], "bytes 通过")
    check(shape.validate("strlen", params, ["abc"]) == ["abc"], "str 通过（后端会编码）")
    check(shape.validate("strlen", params, [[65, 66]]) == [[65, 66]], "整数列表通过")

    try:
        shape.validate("strlen", params, ["a", "b"])
        check(False, "个数多 → 应报错", "没抛")
    except shape.ShapeError as exc:
        check("E-FFI-Shape" in str(exc) and "期望 1" in str(exc), "个数多 → E-FFI-Shape", str(exc))

    try:
        shape.validate("strlen", params, [123])
        check(False, "类型错 → 应报错", "没抛")
    except shape.ShapeError as exc:
        check("E-FFI-Shape" in str(exc), "类型错 → E-FFI-Shape", str(exc))

    try:
        shape.validate("add1", [("n", "u64")], [-1])
        check(False, "负数给 u64 → 应报错", "没抛")
    except shape.ShapeError as exc:
        check("不能为负" in str(exc), "负数给 u64 → 报错并解释回绕", str(exc))

    try:
        shape.validate("add1", [("n", "u64")], [2 ** 64])
        check(False, "超范围 → 应报错", "没抛")
    except shape.ShapeError as exc:
        check("超出 u64 范围" in str(exc), "超范围 → 报错", str(exc))

    check(shape.validate("add1", [("n", "u64")], [7]) == [7], "合法 u64 通过")


def test_unsupported():
    print("[2] 后端不支持的类型 → E-FFI-Unsupported（不是 Shape）")
    try:
        ffi_c.argtype_for("float")
        check(False, "float 应报 Unsupported", "没抛")
    except shape.UnsupportedError as exc:
        check("E-FFI-Unsupported" in str(exc), "float → E-FFI-Unsupported", str(exc))
    check(ffi_c.argtype_for("u64") is not None, "u64 有映射")
    check(ffi_c.argtype_for("Vec<u8>&") is not None, "Vec 有映射")
    check(ffi_c.restype_for("") is None, "空返回类型 = void")


def test_interp_gate():
    print("[3] 解释器入口：错误在加载库之前抛出")
    it, tu = make_interp()
    spec = spec_node(tu, "strlen")
    check(param_list(spec) == [("s", "Vec<u8>&")], "param_list 认得 Vec<u8>&",
          str(param_list(spec)))

    for args, why in ([[1, 2], "个数"], [[123], "类型"]):
        try:
            it._call_ffi_spec("strlen", args)
            check(False, f"{why}错 → 应拦在加载前", "没抛")
        except InterpError as exc:
            check("E-FFI-Shape" in str(exc), f"{why}错 → E-FFI-Shape", str(exc))
        except OSError as exc:                        # 说明真去加载库了 → 校验顺序错
            check(False, f"{why}错 → 应拦在加载前", f"先撞上了加载：{exc}")

    try:
        it._call_ffi_spec("add1", [-1])
        check(False, "负 u64 → 应拦", "没抛")
    except InterpError as exc:
        check("不能为负" in str(exc), "负 u64 → 解释回绕", str(exc))


def main() -> int:
    test_validate_units()
    test_unsupported()
    test_interp_gate()
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
