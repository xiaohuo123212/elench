#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_interp_extra.py —— 解释器版补完的 9 个内建（v0.71）

| 内建 | 作用 | 关键点 |
|---|---|---|
| `vec_push` / `vec_pop` | 可增长/弹出 | 分配按**摊还 1 次**；空弹 → `Option::None` |
| `option_unwrap_or` | `Some(v)→v`，`None→默认` | **`?` 只对 `Result` 生效**，Option 要显式解包 |
| `json_array` / `json_object` | 结构化输出 | 成员是**已序列化**的字节；对象是**键值交替** |
| `env_set` | 设环境变量 | `env` 效果 |
| `fs_rename` / `fs_remove` | 改名/删除 | `fs` 效果；失败 → `Err(Io::*)` |
| `utf8_chars_vec` | 整串 → 码点数组 | 用现有 `vec-u64`，**不必等 `Vec<u32>`** |
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from interp import Interp             # noqa: E402
from parser import parse_source       # noqa: E402

FAILED = []
TU = "spec f(const Vec<u64>& xs) -> u64 { effects { env, fs }; }\n"


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}   {detail}")


def run(body, xs=(1, 2, 3)):
    tu = parse_source(TU, "m.tu").module
    ch = parse_source("func f(const Vec<u64>& xs) -> u64 {\n" + body + "\n}\n", "m.eln").module
    return Interp(ch, tu).call("f", [list(xs)])


def main() -> int:
    print("=== vec_push / vec_pop ===")
    check("push 两次、pop 一次 → 4",
          run("""    Vec<u64> ys = copy(xs);
    vec_push(ys, 9);
    vec_push(ys, 10);
    vec_pop(ys);
    return ys.length();""") == 4)
    check("pop 弹出的是最后压入的（10）",
          run("""    Vec<u64> ys = copy(xs);
    vec_push(ys, 10);
    return option_unwrap_or(vec_pop(ys), 0);""") == 10)
    check("空 Vec pop → None → 默认值 0",
          run("""    Vec<u64> ys = copy(xs);
    u64 i = 0;
    while (i < 3) { vec_pop(ys); i = i + 1; }
    return option_unwrap_or(vec_pop(ys), 0);""") == 0)

    print("=== JSON 输出 ===")
    check("json_array(已序列化) → [1,2]",
          run("""    Vec<Vec<u8>> it = text_split_vec("1,2", ",");
    return json_array(it).length();""") == 5)
    check("json_object 键值交替（键未加引号 → {1:2}，5 字节）",
          run("""    Vec<Vec<u8>> kv = text_split_vec("1,2", ",");
    return json_object(kv).length();""") == 5)

    print("=== env_set ===")
    run("""    env_set("ELENCH_X", "42");
    return 0;""")
    check("env_set 真的写进进程环境", os.environ.get("ELENCH_X") == "42")

    print("=== fs_rename / fs_remove ===")
    d = tempfile.mkdtemp()
    a = os.path.join(d, "a.txt")
    b = os.path.join(d, "b.txt")
    open(a, "wb").write(b"x")
    body = f"""    fs_rename({"dummy"}, {(list(repr(b).strip("'")))});
    return 0;"""
    # 直接走内建更直接（避免字符串字面量转义）
    import stdlib
    okv = stdlib.FNS["fs_rename"]["impl"]([list(a.encode()), list(b.encode())])
    check("fs_rename 成功", getattr(okv, "v", None) == 1 and os.path.exists(b))
    rmv = stdlib.FNS["fs_remove"]["impl"]([list(b.encode())])
    check("fs_remove 成功", getattr(rmv, "v", None) == 1 and not os.path.exists(b))
    miss = stdlib.FNS["fs_remove"]["impl"]([list(os.path.join(d, "nope").encode())])
    check("删除不存在的文件 → Err(Io::NotFound)",
          getattr(getattr(miss, "e", None), "variant", None) == "NotFound")

    print("=== utf8_chars_vec ===")
    check("「中文」→ 2 个码点",
          run("""    return utf8_chars_vec("中文").length();""") == 2)
    check("第一个码点是 20013",
          run("""    return utf8_chars_vec("中文")[0];""") == 20013)

    print()
    if FAILED:
        print(f"✗ {len(FAILED)} 项失败：{', '.join(FAILED)}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
