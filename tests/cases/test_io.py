#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_io.py —— L2：`read_file` / `read_stdin`（v0.71）

## 三条必须锁住的性质

1. **能读**：`read_file(path)` 返回 `vector<u8>`，内容与文件一致；
2. **效果必须声明**：用 `read_file` 而不写 `effects { fs }` → `E-Effect-Missing`；
3. **成本是 ⊤**：I/O 的时间成本静态不可知，所以调用它的函数 `cost` 判"无法判定"
   （要可判必须 `external` 登记，该机制尚未实现）。

## 已知简化（写清，别当特性）

- **I/O 失败直接中止**（`InterpError`），没有 `Result` 版本——
  要做 `Result` 得先有内置错误枚举；
- `read_stdin()` 读**全部**标准输入（不按行）。
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

import os
import subprocess
import sys
import tempfile


from checker import check_module          # noqa: E402
from cost_interp import CostInterp        # noqa: E402
from interp import Interp                 # noqa: E402
from parser import parse_source           # noqa: E402

FAILED = []
T = "target { time: { max: O(n) }, allocation: { max: 0 }, };\n"

TU = f"""spec main(const vector<u8>& path) -> u64 {{
    effects {{ fs }};
    {T}
}}
"""

CH = """func main(const vector<u8>& path) -> u64 {
    const vector<u8> data = read_file(path);
    return data.length();
}
"""


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run_program(tu_src, ch_src, args):
    tu = parse_source(tu_src, "m.tu").module
    ch = parse_source(ch_src, "m.eln").module
    return Interp(ch, tu).call("main", args)


def main() -> int:
    d = tempfile.mkdtemp()
    data_path = os.path.join(d, "data.txt")
    payload = b"hello elench"
    open(data_path, "wb").write(payload)
    path_bytes = list(data_path.encode("utf-8"))

    print("=== 读文件 ===")
    check("返回的文件长度正确",
          run_program(TU, CH, [path_bytes]) == len(payload),
          str(run_program(TU, CH, [path_bytes])))

    check("读到的内容正确（第 1 个字节是 'h'）",
          run_program(TU, """func main(const vector<u8>& path) -> u64 {
    const vector<u8> data = read_file(path);
    return data[0];
}""", [path_bytes]) == ord("h"))

    print("=== 效果必须声明 ===")
    tu_bad = f"""spec main(const vector<u8>& path) -> u64 {{
    effects {{}};
    {T}
}}
"""
    codes = [x.code for x in check_module(tu_bad, CH, "m").diagnostics]
    check("不写 effects { fs } → E-Effect-Missing", "E-Effect-Missing" in codes, str(codes))
    codes = [x.code for x in check_module(TU, CH, "m").diagnostics]
    check("写了 effects { fs } → 通过", codes == [], str(codes))

    print("=== 成本：外部项显式化 ===")
    tu = parse_source(TU, "m.tu").module
    ch = parse_source(CH, "m.eln").module
    k = CostInterp(ch, tu).cost_of("main")
    check("κ 里出现 ext(read_file)（其余部分照算）", "ext(read_file)" in k.render(), k.render())
    check("不再是整体 ⊤（其余部分是数字/表达式）", not k.is_top(), k.render())

    print("=== external 登记：ext 被替换成具体值 ===")
    tu_ext = f"""external {{ read_file: O(1); }}
spec main(const vector<u8>& path) -> u64 {{
    effects {{ fs }};
    {T}
}}
"""
    k2 = CostInterp(parse_source(CH, "m.eln").module,
                    parse_source(tu_ext, "m.tu").module).cost_of("main")
    check("登记后 κ 里没有 ext(...)", "ext(" not in k2.render(), k2.render())
    check("登记后 κ 是具体数字", k2.is_const(), k2.render())

    print("=== 写文件 ===")
    out_path = os.path.join(d, "out.bin")
    tu_w = f"""external {{ write_file: O(1); }}
spec main(const vector<u8>& dst) -> u64 {{
    effects {{ fs }};
    {T}
}}
"""
    ch_w = """func main(const vector<u8>& dst) -> u64 {
    const vector<u8> data = write_file(dst, dst);
    return data;
}
"""
    n = run_program(tu_w, ch_w, [list(out_path.encode("utf-8"))])
    check("write_file 返回写入字节数",
          n == len(out_path.encode("utf-8")), str(n))
    check("文件真的被写出来了", os.path.exists(out_path))

    print("=== Result 版：失败是值，不是中止 ===")
    tu_r = f"""error Io;
external {{ try_read_file: O(1); file_exists: O(1); }}
spec main(const vector<u8>& path) -> vector<u8>! {{
    effects {{ fs }};
    {T}
}}
"""
    ch_r = """func main(const vector<u8>& path) -> vector<u8>! {
    const vector<u8> data = try_read_file(path)?;
    return data;
}
"""
    ok = run_program(tu_r, ch_r, [path_bytes])
    check("存在 → 返回 vector（长度正确）",
          getattr(ok, "v", None) is not None and len(ok.v) == len(payload), repr(ok))

    miss = run_program(tu_r, ch_r, [list((d + "/nope.bin").encode("utf-8"))])
    check("不存在 → Err(Io::NotFound)",
          type(miss).__name__ == "Err" and getattr(getattr(miss, "e", None), "variant", None) == "NotFound",
          repr(miss))

    tu_e = f"""external {{ file_exists: O(1); }}
spec main(const vector<u8>& path) -> u64 {{
    effects {{ fs }};
    {T}
}}
"""
    ch_e = """func main(const vector<u8>& path) -> u64 {
    return file_exists(path);
}
"""
    check("file_exists 存在 → 1", run_program(tu_e, ch_e, [path_bytes]) == 1)
    check("file_exists 不存在 → 0",
          run_program(tu_e, ch_e, [list((d + "/nope.bin").encode("utf-8"))]) == 0)

    print("=== 内置错误枚举 ===")
    tu_b = f"""error Arithmetic;
spec main(const vector<u8>& a) -> u64! {{
    effects {{}};
    {T}
}}
"""
    ch_b = """func main(const vector<u8>& a) -> u64! {
    return Err(Arithmetic::DivisionByZero);
}
"""
    codes_b = [x.code for x in check_module(tu_b, ch_b, "m").diagnostics]
    check("Arithmetic::DivisionByZero 是合法变体",
          "E-Variant-Unknown" not in codes_b, str(codes_b))

    print("=== match on Ok / Err ===")
    ch_m = """func main(const vector<u8>& a, const vector<u8>& b) -> u64! {
    match (try_read_file(a)) {
        Ok(d) => { return d.length(); }
        Err(e) => { return 0; }
    }
    return 0;
}
"""
    tu_m = f"""error Io;
external {{ try_read_file: O(1); }}
spec main(const vector<u8>& a, const vector<u8>& b) -> u64! {{
    effects {{ fs }};
    {T}
}}
"""
    _ok = run_program(tu_m, ch_m, [path_bytes, [1]])
    check("Ok 臂命中（长度）", getattr(_ok, "v", None) == len(payload), repr(_ok))
    _er = run_program(tu_m, ch_m, [list((d + "/nope.bin").encode("utf-8")), [1]])
    check("Err 臂命中（返回 0）", getattr(_er, "v", None) == 0, repr(_er))

    print("=== 追加与大小 ===")
    ap = os.path.join(d, "ap.bin")
    tu_ap = f"""external {{ append_file: O(1); file_size: O(1); }}
spec main(const vector<u8>& path) -> u64! {{
    effects {{ fs }};
    {T}
}}
"""
    ch_ap = """func main(const vector<u8>& path) -> u64! {
    append_file(path, path);
    append_file(path, path);
    return file_size(path)?;
}
"""
    got = run_program(tu_ap, ch_ap, [list(ap.encode("utf-8"))])
    want = 2 * len(ap.encode("utf-8"))
    check("追加两次后大小正确", getattr(got, "v", got) == want, f"{got} vs {want}")

    print("=== read_stdin ===")
    tu_in = f"""spec main() -> u64 {{
    effects {{ io }};
    {T}
}}
"""
    ch_in = """func main() -> u64 {
    const vector<u8> data = read_stdin();
    return data.length();
}
"""
    r = subprocess.run([sys.executable, os.path.join(HERE, "dsh.py"), "run",
                        os.path.join(d, "in.tu"), os.path.join(d, "in.eln")],
                       input=b"abcd", capture_output=True)
    open(os.path.join(d, "in.tu"), "w", encoding="utf-8", newline="\n").write(tu_in)
    open(os.path.join(d, "in.eln"), "w", encoding="utf-8", newline="\n").write(ch_in)
    r = subprocess.run([sys.executable, os.path.join(HERE, "dsh.py"), "run",
                        os.path.join(d, "in.tu"), os.path.join(d, "in.eln")],
                       input=b"abcd", capture_output=True)
    check("read_stdin 读到 4 字节",
          b"4" in (r.stdout or b""), (r.stdout or b"").decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace"))

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
