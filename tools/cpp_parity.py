#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""cpp_parity.py —— 「生成的 C++ vs 解释器（语义）」+「生成的 C++ vs 原 `.cpp`」（第 12 道门）

## 为什么要有它

goal 的判据 2 是两句话：「生成的 C++ **能被 g++ 编译通过**」，且「**行为与原 `.cpp`
一致**」。这两句话以前是**人手测**的（上一轮临时脚本 17/19），**没有闸**——所以它会漂。
`tools/rename_rate.py` 量的是「`check --compat` 能过」（≈ 能编译），
`tools/behavior_parity.py` 量的是「解释器 `run` 和 g++ 的 stdout 一致」。
**没有一个量「生成的 C++ 编出来跑出来」和「原 `.cpp` 编出来跑出来」是否一致**，就是这道门。

## 两个维度（2026-11-13 补第二个）

    ① 原 `.cpp`            → g++ -O0 → 运行 → stdout_A
    ② `.cpp` 复制成 `.eln` → `dsh cpp --compat` → 生成 `.cpp` → g++ -O0 → 运行 → stdout_G
    ③ 解释器 `dsh run --compat <tu> <eln>` → stdout_I（**剥掉 `run` 打印的 main 返回值**）
    ④ **语义一致** = stdout_G == stdout_I   ← 语言语义口径，**硬阈值 19/19**
    ⑤ **与原 .cpp 一致** = stdout_A == stdout_G ← 软阈值 ≥17（两处 C++ 未指定）

### 为什么「行为一致」的正确口径是「生成物 vs 解释器」

解释器是**这门语言的语义定义**；原 `.cpp` 那侧有两处 **C++ 标准明确不规定**的行为：

- `05_ring_buffer`：`std::printf("%d %d\\n", r.pop(), r.pop());` —— 实参求值顺序未指定；
- `08_bits_hash`：`std::unordered_map` 的迭代顺序未指定（libstdc++ 给桶序，
  解释器/生成物给插入序；换编译器/换版本就变）。

所以「原 `.cpp` 的 stdout」在 C++ 层面就不是唯一答案。**生成物必须与解释器逐字节一致，
一个都不许差**；与原 `.cpp` 的差异只允许是清单里那两处「本就不该有唯一答案」的。

## 剥解释器末行：**复用** `tools/behavior_parity.py`

`run` 的契约是「把 main 的结果打出来」，那不是程序 stdout。已有口径在
`tools/behavior_parity.py:strip_return_line`：**只剥最后一行且是整数**的情况——
程序自己打印的 `0` 在返回值之前，不会被误删。这里用 importlib 直接调它，
**不另写一份**（两份口径 = 迟早不一致）。

## 已知未指定清单：是**断言**，不是豁免

`KNOWN_UNSPECIFIED` 里每一条的完整含义是：

1. 允许与原 `.cpp` 不同；**且**
2. **必须仍然不同**——若哪天它变得相同，清单就过期了，留着会**掩盖**同模块的真实回归
   ⇒ 判 **rc=1**，并提示「删掉这条」。输出里同时打一行「当前与原 `.cpp` 不同/相同」，
   回答「这条现在还成不成立」。
3. 语义一致这一维**不打折**：已知未指定的模块也必须与解释器一致（计入 19）。

## 阈值（写死，理由随行）

- `EXPECTED_MODULES = 19`：语料 `01…19` 共 19 个。语料扩容时**必须同步改这些数**，
  否则这里会响亮地报「口径不一致」，而不是悄悄用旧阈值放过/误杀。
- `MIN_EMITTED = 19`：**全部**必须出码。出不了码本身就要先看见，不能用
  「别的维度还够数」把它抵消掉。（2026-11-13 之前 `08_bits_hash` 的
  `unordered_map<vector<u8>, u64>` 没有 C++ 映射，这道门曾按 18 放过。）
- `MIN_COMPILED = 19`：出了码必须**全部**编过（goal 判据 2 的第一半）。
- `MIN_SEMANTIC = 19`：**判据**。生成物是语言的产物，语义差一个就是后端错；
  这一维**不能有**「已知未指定」的余量（C++ 未指定只影响原 `.cpp` 那一侧）。
- `MIN_SAME_ORIG = 17`：19 个里 05、08 两处 C++ 未指定 ⇒ 可比 17，允许 **2** 个差异。
  两个差异**必须仍然存在**（见上面清单断言）；出现第 3 个差异就已经不像「已知未指定」，
  更像系统性回归 ⇒ 红。

## 三个真实的坑（都在这里显式处理，不靠"应该没事"）

① **g++ 不在 PATH 上。** 本机真实位置是
   `C:\\Users\\haoll\\Documents\\shake\\toolchain\\mingw64\\bin\\g++.exe`。
   查找顺序：`ELENCH_GXX`（或 `ELENCH_CXX`，覆盖点）→ `PATH` 上的 `g++` → 上面那个固定位置。
   **一个都找不到就明确报「跳过」并 rc=0**——「没测」不等于「通过」，汇总里单列。
   （`dsh cpp` 自己有编译自检，但它的 `shutil.which("g++")` 同样找不到；所以这里把
   编译器所在目录**前置进 PATH**，让子进程里的 `dsh cpp` 也能真编一遍。）

② **系统 `%TEMP%` 会被沙箱拒绝**（MinGW 报 `Cannot create temporary file …
   Permission denied`），`tempfile` 建目录也会失败。所以：所有编译/运行都在
   **工作区内**的 `build/cpp_parity_tmp/<本次运行唯一>/<模块>/` 里做，并把
   `TMP`/`TEMP` 指过去（`dsh cpp` 内部的 `tempfile` 也会跟着用这个目录）。
   跑完默认整个删掉（见下面「并发」一节：这个"唯一"是必须的，不是讲究）。

③ **单模块模式（`--only=`）的口径。** 选中的模块按「选中数」判：出码/编译/语义必须
   全中，与原 `.cpp` 的差只允许是清单里的已知未指定。若「语义一致但与原 `.cpp` 不同」
   且该模块在清单里 ⇒ **rc=0**，并写明「与解释器一致；与原 `.cpp` 的差异来自 C++ 未指定
   行为（具体哪一条）」。清单外出现这种差 ⇒ rc=1（真实回归）。

## 并发：同一分钟两个实例（2026-11-13 修掉的假红）

**症状**：门禁和手工各跑一次，两边都红，报「与原 .cpp 一致 11/19」或「8/19」，
外加「文件不存在 …\\build\\cpp_parity_tmp\\06_heap.tu」；单独重跑就恢复 19/19 · 17/19。

**根因（实测确认）**：以前所有实例共用 `build/cpp_parity_tmp/` **一个**目录，
而且**开头**和**结尾**都 `rmtree` 它。于是谁先结束谁就把另一个**正在用**的目录
连窝端：对面的 `.tu`/`.eln`/`generated.cpp` 在测量中途被删，报出来却是
「出码失败」「语义不一致」「与原 .cpp 不一致」——**看着像后端回归，其实是被删了
文件**。这是互删，不是偶发误测；只要有两路并行就必然踩到。

**做法：(b) 每次运行一个唯一目录，不加跨进程锁。**

- 目录 = `build/cpp_parity_tmp/<pid>-<随机>/`（`tempfile.mkdtemp` 生成，同毫秒
  两个实例也不会撞名）。每个实例只碰自己那份，**从不删别人的**。
- 结束（成功/失败/异常，`try/finally`）删自己那份；父目录**只在空的时候**
  `os.rmdir`（非空必然失败 ⇒ 不可能误删别人的），所以最后一个退出的实例
  顺手收掉父目录，`build/` 不留空壳。
- Windows 上目录句柄可能被 Defender/索引短暂占住 ⇒ `rmtree` 后**重试** 8 次
  （约 1.2s）；仍删不掉就**报出来并判 rc=1**（"不许静默漏"）。注意只针对
  **自己的**目录：别人的目录永远不算我的失败，否则又会造出一个假红。
- 崩溃留下的目录由 `gc_stale_run_dirs()` 兜底清（TTL 6 小时，远大于一次运行的
  10–20s 和单步上限 180s），并发实例绝不会被误杀。

**为什么不用 (a) 文件锁。** 锁只能**串行化**、不能**隔离**：第一个实例拿锁、第二个
干等，总时间近似翻倍，而两边的文件写集合本来就不相交。锁还要额外处理"崩了没释放"
（陈旧锁）和"等超时了放不放行"——放行 = 回到互删，不放行 = 并发时门禁照样假红，
复杂度全是负收益。唯一目录是 O(1) 的隔离，连"等"都不需要。锁唯一能多给的
"两个实例别同时抢 CPU"不是正确性问题，`--jobs` 已经管了。

**自检**：每次跑完都看 `build/cpp_parity_tmp/` 还在不在，并区分两种"还在"：
① 里面还有**自己的**目录 ⇒ 清理失败（rc=1，并打印路径）；
② 里面只有**别人的**目录 ⇒ 另一个实例还在跑（正常，父目录等它退出时收掉）。
输出里明确写是哪一种，不留"大概是并发吧"的猜测。

## 耗时

19 个模块 ×（`dsh cpp` 自带一次编译自检 + 我们两次编译 + 两次运行 + 一次解释器 run）。
本机 4 路并发实测**约 10–20 秒**。`gates.py` 里仍然可以 `--only=cpp_parity` 单独跑。
并发两个实例各用 `--jobs=<核数>` 不会互相踩（见上面「并发」），只会各自慢一点。

## 用法

    python -B tools/cpp_parity.py                # 人类可读；两个维度
    python -B tools/cpp_parity.py --jobs=8       # 模块间并发（默认 min(4, 核数)）
    python -B tools/cpp_parity.py --only=08      # 只跑模块名含 08 的（调试；单模块口径）
    python -B tools/cpp_parity.py --keep         # 保留本次运行的临时目录现场
    python -B tools/cpp_parity.py --json         # 机器可读（给 CI / 别的工具吃）

退出码：0 = 达标（或**因缺编译器而跳过**）；1 = 未达标；2 = 用法错误 / 语料口径不符。
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:                                             # 中文 Windows 控制台默认 GBK
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "demo" / "corpus"
DSH = ROOT / "src" / "dsh.py"
SRC = ROOT / "src"
#: **父目录**：只当容器，里面每次运行一个唯一的子目录（`<pid>-<随机>/`）。
#: 并发两个实例时谁都不碰别人的子目录——这是修掉「互删造成的假红」的关键，
#: 别退化成"所有实例共用一个目录"（见文件头「并发」一节）。
STAGE = ROOT / "build" / "cpp_parity_tmp"       # build/ 在 .gitignore 里

#: g++ 不在 PATH 时的**真实位置**（本机 MinGW；见文件头坑 ①）。
GXX_FALLBACK = Path(r"C:\Users\haoll\Documents\shake\toolchain\mingw64\bin\g++.exe")


def _load_strip_return_line():
    """从 `tools/behavior_parity.py` 取**唯一**的「剥 main 返回值末行」实现。

    **不要在这里另写一份**：两份口径迟早不一致，而这一行之差会让整维语义对拍
    全红/全绿。用 importlib 按文件路径加载，不依赖调用者的 cwd / sys.path。
    """
    path = Path(__file__).resolve().parent / "behavior_parity.py"
    spec = importlib.util.spec_from_file_location(
        "_cpp_parity_behavior_parity", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.strip_return_line


#: 解释器 `run` 会在 stdout **末尾**打印 main 返回值；C++ exe 不会。
#: 口径来自 `tools/behavior_parity.py`（唯一来源，见上面的注释）。
strip_return_line = _load_strip_return_line()

#: 不参与对拍的语料：**不是后端的问题**，是输入本身不在 `--compat` 的范围内。
EXCLUDED = {
    "00_docnums": "生成物/文档检查器，用 std::filesystem + std::ifstream；"
                  "`--compat` 不支持文件流（它也不是「被后端翻译的业务代码」）",
}

#: **已知未指定清单**（**断言**，不是豁免）：这些模块的 stdout 在 C++ 层面就没有唯一定义。
#:
#: 每条同时要求两件事：
#:   ① 语义（生成物 vs 解释器）必须仍然一致；
#:   ② 与原 `.cpp` 必须仍然**不同**——若变得相同，这条清单就过期了，留着会掩盖同模块的
#:      真实回归 ⇒ `summarize()` 判 `stale_known` 并 rc=1。
#: 判据是「原 `.cpp` 自己换个编译器/换个版本就可能变」，不是「后端输出不一样」。
#: 新增条目必须写清**为什么未指定**。
KNOWN_UNSPECIFIED = {
    "05_ring_buffer":
        "源码 `std::printf(\"%d %d\\n\", r.pop(), r.pop());`：两个有副作用的 pop() "
        "挤在同一次调用里，C++ 标准规定实参求值顺序未指定（unspecified）。"
        "原 `.cpp` 与生成的 `.cpp` 谁先求值都合法，所以「原 `.cpp` 的 stdout」不唯一；"
        "语义上以解释器的插入/调用顺序为准。",
    "08_bits_hash":
        "`std::unordered_map<std::string, u64>` 的**迭代顺序**在 C++ 标准里未指定"
        "（libstdc++ 按桶序给出 del/put/get，解释器与生成物按插入序给出 get/put/del；"
        "换编译器或换版本就会变）。键值计数本身一致，差的是「遍历顺序」这个"
        "C++ 不保证的量，所以「原 `.cpp` 的 stdout」不唯一；语义上以解释器为准。",
}

#: 语料 `01…19` 共 19 个模块（00_docnums 不算）。**改语料时同步改这些数。**
EXPECTED_MODULES = 19
#: **19 = 全部出码**（goal 判据 2 的第一半）。出不了码必须单独红，不能被别的维度抵消。
MIN_EMITTED = 19
#: **19 = 出了码全部编过**（goal 判据 2 的第一半）。`dsh cpp` 有自己的编译自检，
#: 但这里用真正要跑的 g++ -O0 再编一次，编不过就是编不过。
MIN_COMPILED = 19
#: **19 = 语义一致一个都不许差**。生成物与解释器的 stdout 必须逐字节相同；
#: 解释器是语言的语义定义，C++ 的「未指定」只影响原 `.cpp` 那一侧，不构成这里的余量。
MIN_SEMANTIC = 19
#: 19 个里 05、08 两处 C++ 未指定（不可判定）⇒ 可比 17，**允许 2 个差异**。
#: 这两个差异必须仍然存在（`KNOWN_UNSPECIFIED` 的断言）；第 3 个差异 = 系统性回归。
MIN_SAME_ORIG = 17

COMPILE_TIMEOUT = 120                           # 单个 g++（-O0，小文件）
RUN_TIMEOUT = 30                                # 单个可执行文件
DSH_TIMEOUT = 180                               # 一次 `dsh cpp` / `dsh run`


# ---------------------------------------------------------------- 纯逻辑

def find_gxx(environ=None) -> str | None:
    """找 g++：`ELENCH_GXX`（或 `ELENCH_CXX`，与 `dsh cpp` 同名）→ PATH → 固定位置。"""
    env = os.environ if environ is None else environ
    cands = []
    for key in ("ELENCH_GXX", "ELENCH_CXX"):
        raw = (env.get(key) or "").strip().strip('"')
        if raw:
            cands.append(raw)
    which = shutil.which("g++", path=env.get("PATH"))
    if which:
        cands.append(which)
    cands.append(str(GXX_FALLBACK))
    for c in cands:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


def is_known(module: str) -> bool:
    """这个模块「与原 .cpp 不同」是否**本来就允许**（C++ 未指定）。"""
    return module in KNOWN_UNSPECIFIED


def judge_semantic(stdout_gen, interp_rc, stdout_interp_raw):
    """**语义维度**：生成物 stdout vs 解释器 stdout（剥掉 main 返回值末行）。

    **纯函数**，单测直接喂字节。返回 `(same, note)`。
    `interp_rc is None` = 解释器超时/起不来；非 0 = 解释器失败（都不能算一致）。
    """
    if interp_rc is None:
        return False, "解释器跑不起来（超时/启动失败）"
    if interp_rc != 0:
        return False, f"解释器退出码 {interp_rc}"
    stripped = strip_return_line(stdout_interp_raw or b"")
    if stdout_gen == stripped:
        return True, ""
    return False, ("语义不同：生成物 " + _show(stdout_gen)
                   + " / 解释器（剥末行）" + _show(stripped))


def summarize(rows, expected=EXPECTED_MODULES,
              min_emitted=MIN_EMITTED, min_compiled=MIN_COMPILED,
              min_semantic=MIN_SEMANTIC, min_same_orig=MIN_SAME_ORIG):
    """把逐模块结果表压成汇总 + 判定。**纯函数**（单测直接喂假表）。

    `rows` 每项至少含 `module` / `emit` / `compile` / `sem` / `same_orig`；
    `orig_measured` 缺省视为 False（原 `.cpp` 没量出 stdout，不能冒充一致）。
    """
    modules = [r["module"] for r in rows]
    emitted = sum(1 for r in rows if r["emit"])
    compiled = sum(1 for r in rows if r["compile"])
    known = [r["module"] for r in rows if is_known(r["module"])]

    # 语义：**全部**（含已知未指定的模块）都算分子；硬阈值 19。
    semantic = sum(1 for r in rows
                   if r["emit"] and r["compile"] and r.get("sem"))
    sem_diffs = [r["module"] for r in rows
                 if r["emit"] and r["compile"] and not r.get("sem")]

    # 与原 .cpp：已知未指定的模块**不计分子**（它们的差异本来就不该被当成证据），
    # 但必须**仍然不同**（stale_known）。
    orig_measured = sum(1 for r in rows
                        if r["emit"] and r["compile"] and r.get("orig_measured"))
    same_orig = sum(1 for r in rows
                    if r["emit"] and r["compile"] and r.get("orig_measured")
                    and r.get("same_orig") and not is_known(r["module"]))
    orig_comparable = sum(1 for r in rows
                          if r["emit"] and r["compile"] and r.get("orig_measured")
                          and not is_known(r["module"]))
    orig_diffs = [r["module"] for r in rows
                  if r["emit"] and r["compile"] and r.get("orig_measured")
                  and not r.get("same_orig") and not is_known(r["module"])]
    stale_known = [r["module"] for r in rows
                   if r["emit"] and r["compile"] and r.get("orig_measured")
                   and r.get("same_orig") and is_known(r["module"])]
    known_unverified = [r["module"] for r in rows
                        if is_known(r["module"]) and r["emit"] and r["compile"]
                        and not r.get("orig_measured")]
    orig_unmeasured = [r["module"] for r in rows
                       if r["emit"] and r["compile"] and not r.get("orig_measured")
                       and not is_known(r["module"])]

    # 明确分类，不留"其它失败"的黑箱：
    not_emitted = [r["module"] for r in rows if not r["emit"]]
    not_compiled = [r["module"] for r in rows
                    if r["emit"] and not r["compile"]]

    problems = []
    if len(rows) != expected:
        problems.append(
            f"语料是 {len(rows)} 个模块，阈值按 {expected} 写的——"
            "请同步更新 EXPECTED_MODULES / MIN_* 阈值")
    if emitted < min_emitted:
        problems.append(f"出码 {emitted}/{expected} < 阈值 {min_emitted}")
    if compiled < min_compiled:
        problems.append(f"编译 {compiled}/{expected} < 阈值 {min_compiled}")
    if semantic < min_semantic:
        problems.append(
            f"语义一致（vs 解释器）{semantic}/{expected} < 阈值 {min_semantic}"
            f"（硬口径：差一个就是后端语义错；差异：{'、'.join(sem_diffs) or '无'}）")
    if same_orig < min_same_orig:
        problems.append(
            f"与原 .cpp 一致 {same_orig}/{expected} < 阈值 {min_same_orig}"
            f"（可比 {orig_comparable} 个，已知未指定 {len(known)} 个；"
            f"差异：{'、'.join(orig_diffs) or '无'}）")
    for name in stale_known:
        problems.append(
            f"已知差异 {name} 现在与原 .cpp **相同**：KNOWN_UNSPECIFIED 已过期，"
            "留着会掩盖同模块的真实回归——请删掉这条")
    for name in known_unverified:
        problems.append(
            f"已知差异 {name} 的原 .cpp 没量出 stdout，无法验证「必须仍不同」的断言")
    if orig_unmeasured:
        problems.append(
            "原 .cpp 未能量出 stdout（不能冒充「与原 .cpp 一致」）："
            + "、".join(orig_unmeasured))
    return {
        "modules": modules,
        "emitted": emitted,
        "compiled": compiled,
        "semantic": semantic,
        "sem_diffs": sem_diffs,
        "same_orig": same_orig,
        "orig_comparable": orig_comparable,
        "orig_diffs": orig_diffs,
        "orig_unmeasured": orig_unmeasured,
        "known": known,
        "stale_known": stale_known,
        "known_unverified": known_unverified,
        "not_emitted": not_emitted,
        "not_compiled": not_compiled,
        "expected": expected,
        "min_emitted": min_emitted,
        "min_compiled": min_compiled,
        "min_semantic": min_semantic,
        "min_same_orig": min_same_orig,
        "problems": problems,
        "ok": not problems,
    }


def subset_limits(rows):
    """`--only=`（调试）的子集口径：选中几个就按几个判。

    出码/编译/语义 = 选中数（全中）；与原 `.cpp` 允许已知未指定的那几条不同。
    """
    known_n = sum(1 for r in rows if is_known(r["module"]))
    return {
        "expected": len(rows),
        "min_emitted": len(rows),
        "min_compiled": len(rows),
        "min_semantic": len(rows),
        "min_same_orig": max(0, len(rows) - known_n),
    }


def exit_code(summary) -> int:
    """汇总 → 退出码。**判据只在这里**，print 什么不影响它。"""
    return 0 if summary["ok"] else 1


# ---------------------------------------------------------------- 临时目录
#
# 这一节的每一条都对着一个**真踩过**的坑，别简化：
#   · 唯一目录  → 并发互删（两路并行时必然假红，见文件头）
#   · 重试      → Windows 上 Defender/索引短暂占住句柄，一次删不掉
#   · 父目录空才删 → 删别人的目录 = 立刻回到互删
#   · TTL 兜底  → 唯一目录的代价：崩掉的实例没人收尸

#: 崩溃留下的运行目录多久之后可以当垃圾清掉（秒）。远大于一次运行（10–20s）
#: 和单步上限（`DSH_TIMEOUT=180`），所以并发实例不可能被误杀。
STALE_TTL = 6 * 3600
#: 删自己的目录：重试次数 × 间隔（Windows 句柄）。
CLEAN_ATTEMPTS = 8
CLEAN_DELAY = 0.15


def _rel(path: Path) -> str:
    """相对仓库根的 posix 路径（打印用；不在仓库里就原样）。"""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return str(path)


def make_run_dir(stage: Path | None = None) -> Path:
    """给**本次运行**建一个唯一目录：`build/cpp_parity_tmp/<pid>-<随机>/`。

    用 `tempfile.mkdtemp` 而不是 `f"{pid}"`：同一进程的两份实例（例如门禁和
    手工都用同一个父进程？不可能，但如果有人嵌着调）也不会撞名。

    父目录**可能**在两步之间被另一个实例 `rmdir` 掉（它删完自己那份、发现父目录
    空了就顺手收了），所以 `FileNotFoundError` 要重试——否则会偶发
    「建不出临时目录」的假红。
    """
    stage = Path(stage) if stage is not None else STAGE
    last = None
    for _ in range(20):
        try:
            stage.mkdir(parents=True, exist_ok=True)
            return Path(tempfile.mkdtemp(dir=str(stage), prefix=f"{os.getpid()}-"))
        except OSError as exc:                   # noqa: BLE001
            last = exc
            time.sleep(0.02)
    raise RuntimeError(f"建不出临时目录（{stage}）：{last}")


def clean_run_dir(run_dir: Path, attempts: int = CLEAN_ATTEMPTS,
                  delay: float = CLEAN_DELAY) -> bool:
    """删掉**本次运行**的目录，返回是否真删干净了。

    **只删传进来的这一个**——绝不碰父目录下的其它实例。
    `rmtree(ignore_errors=True)` 一次不成就静默留下半个目录，所以重试；
    重试完还在就返回 False，由 `main()` 报出来（"不许静默漏"）。
    """
    run_dir = Path(run_dir)
    for i in range(attempts):
        if not run_dir.exists():
            return True
        shutil.rmtree(run_dir, ignore_errors=True)
        if not run_dir.exists():
            return True
        if i + 1 < attempts:
            time.sleep(delay)
    return not run_dir.exists()


def release_stage(stage: Path | None = None) -> bool:
    """父目录**空了才删**；返回 True = 父目录已不存在。

    `os.rmdir` 对非空目录必然失败，这就是"空才删"的实现——不需要先列目录
    （先列再删有 TOCTOU：另一个实例可能正好在这两步之间建了目录）。
    并发时另一个实例还在用 ⇒ 这里返回 False，那是**正常**的，不是错误。
    """
    stage = Path(stage) if stage is not None else STAGE
    for _ in range(4):
        if not stage.exists():
            return True
        try:
            os.rmdir(stage)
        except OSError:                          # 非空 / 被占用
            pass
        if not stage.exists():
            return True
        time.sleep(0.05)
    return not stage.exists()


def stage_residue(stage: Path | None = None) -> list[str]:
    """`build/cpp_parity_tmp/` 下还剩哪些目录名。父目录不存在 ⇒ `[]`（干净）。

    **自检入口**：跑完单实例时这里必须是空的。
    """
    stage = Path(stage) if stage is not None else STAGE
    if not stage.exists():
        return []
    try:
        return sorted(p.name for p in stage.iterdir())
    except OSError:
        return []


def gc_stale_run_dirs(stage: Path | None = None,
                      max_age: float = STALE_TTL) -> list[str]:
    """清掉**崩溃留下的**运行目录（按 mtime 超过 `max_age` 判），返回清掉的目录名。

    唯一目录的代价是"崩掉的实例不会再被下一次运行顺带删掉"，所以要有 TTL 兜底；
    否则 `build/` 会慢慢攒死目录——那和「别在 build/ 里留垃圾」是同一件事。
    """
    stage = Path(stage) if stage is not None else STAGE
    if not stage.is_dir():
        return []
    now = time.time()
    gone = []
    for entry in sorted(stage.iterdir()):
        try:
            if not entry.is_dir():
                continue
            if now - entry.stat().st_mtime < max_age:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            if not entry.exists():
                gone.append(entry.name)
        except OSError:
            continue
    return gone


def hygiene_report(run_dir: Path, cleaned: bool, keep: bool,
                   stage: Path | None = None) -> dict:
    """跑完后的**自检**：本次目录删干净了吗？父目录里剩的是谁的？

    **纯读**（只 `iterdir`），不改文件。返回
    `{"ok": bool, "mine": bool, "others": [名字], "note": str}`：
      · `mine=True`  ⇒ 自己的目录还在 = 清理失败（**真的**残留，rc 要红）；
      · `others`     ⇒ 别的实例还在跑（正常，父目录由最后一个退出的人收掉）。
    区分这两者很重要：把别人的目录算成"我的失败"，就会再造一个假红。
    """
    stage = Path(stage) if stage is not None else STAGE
    left = stage_residue(stage)
    mine = Path(run_dir).name in left
    others = [n for n in left if n != Path(run_dir).name]
    if keep:
        return {"ok": True, "mine": mine, "others": others,
                "note": "——keep：现场按请求保留", "kept": True}
    if mine or not cleaned:
        return {"ok": False, "mine": True, "others": others,
                "note": f"本次运行的临时目录没删掉（重试 {CLEAN_ATTEMPTS} 次后仍在）："
                        f"{_rel(run_dir)}/"}
    if others:
        return {"ok": True, "mine": False, "others": others,
                "note": f"并发：{_rel(stage)}/ 下还有 {len(others)} 个目录属于"
                        f"其它实例（本实例自己的已删；父目录等最后一个实例退出时收掉）"}
    if stage.exists():
        return {"ok": True, "mine": False, "others": [],
                "note": f"父目录 {_rel(stage)}/ 还在但是空的"
                        f"（另一个实例刚收掉子目录；下次运行会复用）"}
    return {"ok": True, "mine": False, "others": [],
            "note": f"自检通过：{_rel(stage)}/ 不存在（build/ 下没有残留）"}


# ---------------------------------------------------------------- 子进程

def child_env(gxx: str, tmpdir: Path):
    """给 g++ / 可执行文件 / `dsh cpp` / `dsh run` 用的环境。

    三件事：编译器目录进 PATH（DLL 与 `dsh` 的 `which("g++")` 都要）、
    TMP/TEMP 指到**工作区内**（坑 ②）、UTF-8（中文 Windows 控制台）。
    """
    env = dict(os.environ)
    bindir = str(Path(gxx).resolve().parent)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    env["TMP"] = env["TEMP"] = str(tmpdir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return env


def _run(cmd, cwd, env, timeout, binary=False):
    """跑一个子进程；**不抛异常**，返回 (rc, stdout, stderr, 耗时)。"""
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, cwd=str(cwd), env=env, timeout=timeout,
                           capture_output=True, text=not binary,
                           encoding=None if binary else "utf-8",
                           errors=None if binary else "replace")
        out, err = p.stdout, p.stderr
        if not binary:
            out = out or ""
            err = err or ""
        return p.returncode, out, err, time.perf_counter() - t0
    except subprocess.TimeoutExpired:
        return None, b"" if binary else "", f"超时（>{timeout}s）", time.perf_counter() - t0
    except OSError as exc:                       # noqa: BLE001
        return None, b"" if binary else "", f"{type(exc).__name__}: {exc}", \
            time.perf_counter() - t0


def _tail(text, n=6, width=1200):
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(lines[-n:])[-width:]


def _show(data, limit=120) -> str:
    """把 stdout 字节压成一行可读文本（超长截断，换行显式化）。"""
    if isinstance(data, bytes):
        txt = data.decode("utf-8", "replace")
    else:
        txt = str(data)
    txt = txt.replace("\r", "\\r").replace("\n", "\\n")
    return repr(txt[:limit] + (" …" if len(txt) > limit else ""))


# ---------------------------------------------------------------- 一个模块

def check_module(module: str, gxx: str, run_dir: Path) -> dict:
    """①②③④⑤ 一个模块。返回结果行；**任何一步失败都变成数据，不抛异常**。

    `run_dir` 是**本次运行唯一**的目录（`make_run_dir()`）：模块的中间产物只落在
    `run_dir/<模块>/` 下，绝不碰其它实例的东西（这就是并发不再假红的原因）。
    """
    row = {"module": module, "emit": False, "compile": False,
           "sem": False, "same_orig": False, "orig_measured": False,
           "known": is_known(module), "note": "", "secs": 0.0,
           "orig_rc": None, "gen_rc": None, "interp_rc": None}
    t0 = time.perf_counter()

    def note(msg):
        row["note"] = (row["note"] + "；" + msg) if row["note"] else msg

    moddir = Path(run_dir) / module
    tmpdir = moddir / "tmp"
    try:
        moddir.mkdir(parents=True, exist_ok=True)
        tmpdir.mkdir(parents=True, exist_ok=True)
        env = child_env(gxx, tmpdir)
        orig_cpp = CORPUS / f"{module}.cpp"
        tu = moddir / f"{module}.tu"
        eln = moddir / f"{module}.eln"
        gen_cpp = moddir / "generated.cpp"
        exe_a = moddir / "original.exe"
        exe_b = moddir / "generated.exe"

        # 契约与实现：`.tu` 原样复制；`.cpp` 复制成 `.eln`（--compat 认 C++ 拼写）。
        shutil.copyfile(CORPUS / f"{module}.tu", tu)
        shutil.copyfile(orig_cpp, eln)

        # ① 原 .cpp → g++ -O0 → 运行（这一维失败不阻断语义维度，只记 note）
        stdout_a = None
        rc, out, err, _ = _run(
            [gxx, "-std=c++17", "-O0", "-o", str(exe_a), str(orig_cpp)],
            moddir, env, COMPILE_TIMEOUT)
        if rc != 0 or not exe_a.exists():
            note("原 .cpp 编不过：" + (_tail(err or out) or f"rc={rc}"))
        else:
            rc, stdout_a, err, _ = _run([str(exe_a)], moddir, env,
                                        RUN_TIMEOUT, binary=True)
            if rc is None:
                stdout_a = None
                note("原 .cpp 跑不起来：" + (err or ""))
            else:
                row["orig_measured"] = True
                row["orig_rc"] = rc
                if rc != 0:
                    # 原程序自己非零退出也是合法输入；stdout 仍然可比。只记一笔。
                    note(f"原 .cpp 退出码 {rc}")

        # ② .cpp → .eln → `dsh cpp --compat` → 生成的 .cpp
        cmd = [sys.executable, "-B", str(DSH), "cpp", str(tu), str(eln),
               "--compat", f"--output={gen_cpp}"]
        rc, out, err, _ = _run(cmd, ROOT, env, DSH_TIMEOUT)
        emitted = gen_cpp.exists() and gen_cpp.stat().st_size > 0
        if rc != 0 or not emitted:
            note("出码失败：" + (_tail(err or out) or f"rc={rc}"))
            return _done(row, t0)
        row["emit"] = True

        # 生成的 .cpp → g++ -O0
        rc, out, err, _ = _run(
            [gxx, "-std=c++17", "-O0", "-o", str(exe_b), str(gen_cpp)],
            moddir, env, COMPILE_TIMEOUT)
        if rc != 0 or not exe_b.exists():
            note("生成的 .cpp 编不过：" + (_tail(err or out) or f"rc={rc}"))
            return _done(row, t0)
        row["compile"] = True

        # 生成物运行（语义维度与「与原 .cpp」都用它）
        rc, stdout_g, err, _ = _run([str(exe_b)], moddir, env,
                                    RUN_TIMEOUT, binary=True)
        if rc is None:
            note("生成的 .cpp 跑不起来：" + (err or ""))
            return _done(row, t0)
        row["gen_rc"] = rc

        # ⑤ 与原 .cpp 逐**字节**比较（只有原 .cpp 真跑起来才算测过）
        if row["orig_measured"]:
            if stdout_a == stdout_g:
                row["same_orig"] = True
                if rc != row["orig_rc"]:
                    note(f"stdout 与原 .cpp 一致但退出码不同"
                         f"（原 {row['orig_rc']} / 生成 {rc}）")
            else:
                note("与原 .cpp stdout 不同：原 " + _show(stdout_a)
                     + " / 生成 " + _show(stdout_g))

        # ④ 语义：生成物 vs 解释器（`dsh run --compat`；末行由 behavior_parity 剥）
        cmd = [sys.executable, "-B", str(DSH), "run", "--compat",
               str(tu), str(eln)]
        rc_i, out_i, err_i, _ = _run(cmd, ROOT, env, DSH_TIMEOUT, binary=True)
        row["interp_rc"] = rc_i
        same, why = judge_semantic(stdout_g, rc_i, out_i)
        row["sem"] = same
        if not same:
            detail = _tail(err_i.decode("utf-8", "replace")) if err_i else ""
            note(why + (("：" + detail) if detail else ""))
        return _done(row, t0)
    except Exception as exc:                     # noqa: BLE001
        note(f"测量异常：{type(exc).__name__}: {exc}")
        return _done(row, t0)


def _done(row, t0):
    row["secs"] = round(time.perf_counter() - t0, 2)
    return row


# ---------------------------------------------------------------- 语料 / 汇总

def discover_modules():
    """`demo/corpus/*.cpp` 的模块名（跳过 `EXCLUDED` 里登记的）。"""
    if not CORPUS.is_dir():
        return []
    names = sorted(p.stem for p in CORPUS.glob("*.cpp"))
    return [n for n in names if n not in EXCLUDED]


def run_all(modules, gxx, jobs, run_dir):
    """跑一批模块（模块之间互相独立，可并发）。行按模块名排序返回。"""
    if jobs <= 1:
        rows = [check_module(m, gxx, run_dir) for m in modules]
    else:
        rows = []
        with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(check_module, m, gxx, run_dir): m for m in modules}
            for fut in cf.as_completed(futs):
                rows.append(fut.result())
    rows.sort(key=lambda r: r["module"])
    return rows


# ---------------------------------------------------------------- 输出

def print_table(rows):
    print(f"  {'模块':<18} {'出码':<5} {'编译':<5} {'语义':<5} {'原.cpp':<8} 备注")
    for r in rows:
        if r["module"] in EXCLUDED:
            print(f"  {r['module']:<18} {'-':<5} {'-':<5} {'-':<5} {'-':<8} "
                  f"跳过：{EXCLUDED[r['module']]}")
            continue
        emit = "✓" if r["emit"] else "✗"
        comp = "✓" if r["compile"] else "✗"
        sem = "✓" if r.get("sem") else "✗"
        if is_known(r["module"]):
            if r.get("same_orig"):
                orig = "=!(过期)"                 # 清单说该不同，实际相同 → 过期
            elif r.get("orig_measured"):
                orig = "≠*(按清单)"
            else:
                orig = "?"
        elif not r.get("orig_measured"):
            orig = "?"
        else:
            orig = "✓" if r.get("same_orig") else "✗"
        note = r["note"]
        if is_known(r["module"]) and not note:
            note = "已知未指定（见下方断言）"
        print(f"  {r['module']:<18} {emit:<5} {comp:<5} {sem:<5} {orig:<8} {note}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="cpp_parity —— 语义（生成物 vs 解释器）+ 行为（生成物 vs 原 .cpp）")
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 1),
                    help="模块间并发数（默认 min(4, 核数)）")
    ap.add_argument("--only", default=None,
                    help="只跑模块名里含这个子串的（调试用；子集口径）")
    ap.add_argument("--keep", action="store_true",
                    help="保留本次运行的临时目录（build/cpp_parity_tmp/<pid>-…/）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(argv)
    if args.jobs < 1:
        print("--jobs 必须 ≥ 1", file=sys.stderr)
        return 2

    print("cpp_parity —— 「生成物 vs 解释器（语义）」+「生成物 vs 原 .cpp（行为）」")
    print(f"  语料：demo/corpus/*.cpp（共 {EXPECTED_MODULES} 个模块；"
          f"跳过 {len(EXCLUDED)} 个：{'、'.join(EXCLUDED)}）")
    print("  语义口径：解释器 `dsh run --compat` 是语言的语义定义；"
          "原 .cpp 侧有 2 处 C++ 未指定（05/08）")

    gxx = find_gxx()
    if not gxx:
        # 坑 ①：**明确跳过，rc=0**，并且不许把"没测"说成"通过"。
        print()
        print("  ⚠ 跳过：本机找不到 g++（ELENCH_GXX / PATH / "
              f"{GXX_FALLBACK} 都没有）")
        print("    两个维度（语义一致 / 与原 .cpp 一致）都**未测量**——这不等于通过。")
        print("    装 MinGW 或设 ELENCH_GXX=<g++.exe> 后重跑。")
        if args.json:
            print(json.dumps({"skipped": True, "reason": "no g++",
                              "ok": True, "skipped_not_passed": True,
                              "semantic": None, "same_orig": None},
                             ensure_ascii=False))
        return 0

    print(f"  编译器：{gxx}")
    print()

    modules = discover_modules()
    if args.only:
        measurements = [m for m in modules if args.only in m]
        if not measurements:
            print(f"--only={args.only} 没有匹配的模块（有：{'、'.join(modules)}）",
                  file=sys.stderr)
            return 2
    else:
        measurements = modules
    rows = []

    # 顺手收掉上次崩溃留下的目录（TTL 兜底；并发实例的目录都还新鲜，不会误杀）。
    stale = gc_stale_run_dirs()
    if stale:
        print(f"  （清掉 {len(stale)} 个上次遗留的临时目录：{'、'.join(stale)}）")
    # **本次运行唯一**的目录：并发两个实例各写各的，互不删对方（见文件头「并发」）。
    try:
        run_dir = make_run_dir()
    except RuntimeError as exc:                  # 连建目录都建不出来：用法/环境问题
        print(f"  ✗ {exc}", file=sys.stderr)
        print("    （检查 build/ 是否可写、是否被其它进程长期占住）", file=sys.stderr)
        return 2
    print(f"  临时目录：{_rel(run_dir)}/（本次运行唯一；结束无论成败都删）")

    t0 = time.perf_counter()
    cleaned = True
    try:
        rows.extend(run_all(measurements, gxx, args.jobs, run_dir))
    finally:
        # 无论成功、失败还是异常都要走这里：临时目录绝不因为抛异常而留下。
        if args.keep:
            print(f"  --keep：保留现场 {_rel(run_dir)}/")
        else:
            cleaned = clean_run_dir(run_dir)
            release_stage()                      # 父目录空了才删（非空 = 别人还在用）
    elapsed = time.perf_counter() - t0

    # 被跳过的语料也占一行（让人看见"为什么不算分母"），但不参与汇总。
    if not args.only:
        for name in EXCLUDED:
            if not any(r["module"] == name for r in rows):
                rows.append({"module": name, "emit": False, "compile": False,
                             "sem": False, "same_orig": False,
                             "orig_measured": False, "known": False,
                             "note": "", "secs": 0.0,
                             "orig_rc": None, "gen_rc": None, "interp_rc": None})
    rows.sort(key=lambda r: r["module"])
    measured = [r for r in rows if r["module"] not in EXCLUDED]
    if args.only:
        # 坑 ③：**子集口径**（调试用）：选中几个就按几个判。
        summary = summarize(measured, **subset_limits(measured))
    else:
        summary = summarize(measured)
    expect = summary["expected"]

    print_table(rows)
    print()
    print(f"  汇总：出码 {summary['emitted']}/{expect} · "
          f"编译 {summary['compiled']}/{expect} · "
          f"语义一致（vs 解释器）{summary['semantic']}/{expect} · "
          f"与原 .cpp 一致 {summary['same_orig']}/{expect}")
    print(f"    （与原 .cpp：计入分子 {summary['same_orig']}，"
          f"非已知可比 {summary['orig_comparable']}，"
          f"已知未指定 {len(summary['known'])}）")
    print(f"  耗时：{elapsed:.1f}s（{args.jobs} 路并发）")

    if summary["known"]:
        print()
        print("  已知未指定（**断言**：必须仍与原 .cpp 不同；语义仍须与解释器一致）：")
        for name in summary["known"]:
            row = next((r for r in measured if r["module"] == name), {})
            if row.get("same_orig"):
                status = "当前与原 .cpp **相同** —— 清单过期，见失败项"
            elif row.get("orig_measured"):
                status = "当前与原 .cpp **不同**（断言成立）"
            else:
                status = "原 .cpp 未测量，断言无法验证"
            print(f"    · {name} —— {status}；{KNOWN_UNSPECIFIED[name]}")
    if summary["sem_diffs"]:
        print()
        print("  语义差异（硬阈值，必须为 0）：" + "、".join(summary["sem_diffs"]))
    if summary["orig_diffs"]:
        print()
        print("  与原 .cpp 的真实差异（计入阈值）：" + "、".join(summary["orig_diffs"]))
    if summary["not_emitted"]:
        print("  没出码的：" + "、".join(summary["not_emitted"]))
    if summary["not_compiled"]:
        print("  出了码但编不过的：" + "、".join(summary["not_compiled"]))

    if args.only:
        print()
        print(f"  --only={args.only} 结论（子集口径：出码/编译/语义 = {len(measured)}，"
              f"与原 .cpp 允许 {len(measured) - len(summary['known'])} 个已知差异）：")
        for r in measured:
            name = r["module"]
            sem = "一致" if r.get("sem") else "不一致"
            if r.get("orig_measured"):
                orig = "相同" if r.get("same_orig") else "不同"
            else:
                orig = "未测"
            print(f"    · {name}：出码 {'✓' if r['emit'] else '✗'}，"
                  f"编译 {'✓' if r['compile'] else '✗'}，"
                  f"与解释器{sem}，与原 .cpp {orig}")
            if r.get("sem") and not r.get("same_orig") and is_known(name):
                # 用户要的那句原文：语义对、与原 .cpp 的差是 C++ 未指定。
                print("      与解释器一致；与原 .cpp 的差异来自 C++ 未指定行为"
                      f"（{KNOWN_UNSPECIFIED[name]}）")
            elif r.get("sem") and not r.get("same_orig") and r.get("orig_measured"):
                print("      与解释器一致，但与原 .cpp 不同且不在 KNOWN_UNSPECIFIED "
                      "清单里 —— 这是真实回归，不许放过。")

    print()
    if args.only:
        print("  阈值（--only 子集口径）：出码/编译/语义 = 选中模块数（硬），"
              "与原 .cpp = 选中数 − 已知未指定（已知项必须仍不同）")
    else:
        print(f"  阈值（写死在代码里）：出码 ≥ {MIN_EMITTED}/{EXPECTED_MODULES}、"
              f"编译 ≥ {MIN_COMPILED}/{EXPECTED_MODULES}、"
              f"语义一致 ≥ {MIN_SEMANTIC}/{EXPECTED_MODULES}（硬）、"
              f"与原 .cpp 一致 ≥ {MIN_SAME_ORIG}/{EXPECTED_MODULES}（软）；"
              "理由见 tools/cpp_parity.py 文件头。")

    # 自检：`build/` 下有没有残留（自己的没删掉 vs 别人的还在跑，必须分清）。
    hygiene = hygiene_report(run_dir, cleaned, args.keep)
    print(f"  临时目录自检：{hygiene['note']}")
    if not hygiene["ok"]:
        print("    （判 rc=1：自己那份没删掉是**真的**残留，不是并发噪声；"
              "别人的目录永远不算到我头上）")

    rc = exit_code(summary)
    if not hygiene["ok"]:
        rc = 1                                   # 自己的目录没删掉：不许静默放过
    brief = (f"出码 {summary['emitted']}/{expect} · "
             f"编译 {summary['compiled']}/{expect} · "
             f"语义一致 {summary['semantic']}/{expect} · "
             f"与原 .cpp 一致 {summary['same_orig']}/{expect}"
             f"（已知未指定 {len(summary['known'])}）")
    if summary["not_emitted"]:
        brief += "；未出码：" + "、".join(summary["not_emitted"])
    if summary["stale_known"]:
        brief += "；清单过期：" + "、".join(summary["stale_known"])
    if not hygiene["ok"]:
        brief += "；临时目录残留（清理失败）"
    if rc == 0:
        # **最后一行带上数字**：`gates.py` 只显示末行，不能只说"通过"。
        print(f"  ✓ 通过（rc=0）：{brief}")
    else:
        print(f"  ✗ 未通过（rc=1）：{brief}")
        for p in summary["problems"]:
            print(f"    - {p}")

    if args.json:
        print(json.dumps({
            "ok": rc == 0, "rc": rc,
            "emitted": summary["emitted"],
            "compiled": summary["compiled"],
            "semantic": summary["semantic"],
            "same_orig": summary["same_orig"],
            "expected": expect,
            "known": summary["known"],
            "stale_known": summary["stale_known"],
            "sem_diffs": summary["sem_diffs"],
            "orig_diffs": summary["orig_diffs"],
            "problems": summary["problems"],
            "hygiene": {"ok": hygiene["ok"], "note": hygiene["note"],
                        "run_dir": _rel(run_dir),
                        "residue": stage_residue()},
            "rows": [{k: v for k, v in r.items() if k != "secs"} for r in rows],
        }, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
