#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""behavior_parity.py —— 「行为一致率」：同一函数，g++ 与 `run --compat` 的 stdout 逐字节比较。

## 为什么要有第二把尺

`tools/rename_rate.py` 量的是**改名通过率**（`check --compat` 0 error）。
它证明「能编译」，不证明「算得一样」。目标里还有一条**行为一致率**：
同一份 `.cpp` 改名成 `.eln` 跑 `run`，stdout 必须和 g++ 编译出的 exe **逐字节相同**。

## 怎么量

对 `demo/corpus/*.cpp`：

    ① g++ 编译（本机 MinGW 不在 PATH 里：跑之前把 mingw64\\bin 加进 PATH；
       另外 g++ 的临时目录必须是**它自己能写**的目录——系统 TEMP 会
       `Cannot create temporary file ... Permission denied`，所以把 TMP/TEMP
       指到 `build/_behavior_parity/<run>/tmp`）
    ② 同名 `.cpp` 复制成 `.eln`（再追加探针函数，见下）
    ③ 两边都跑得起来的函数：比较 stdout 的**字节**（不是「看起来一样」）
    ④ 一致 / 不一致 / 跑不了（逐条原因）+ 一致率

## 只比「两边都能跑」的

- `run` **只打印返回值**。`void` 函数改的是引用参数，外部看不到
  （`insertion_sort(xs)` 这种要在两边各加一个探针，把结果**返回**出来）。
- `map` 返回值只渲染成 `map[N]`（内容不可观测）→ 不比，避免「看起来一样」。
- 没有固定参数表项的顶层函数 → 记「跑不了：没有参数表探针」。
- 文件级失败（elench 解析/检查不过）→ 整个文件的函数都记为跑不了。

## 固定参数表

`PARAMS` 是**唯一**的固定输入；两边的代码都从它生成
（`vec_literal()` / `mat_assignments()`），所以两边喂的是同一个值。

## 确定性（这条也测）

每个目标在**两边各跑两次**；两次输出不同 → 记「跑不了（非确定性）」，并进缺陷列表。
同一输入两次跑必须逐字节相同，否则一致率不可信。

## 用法

    python -B tools/behavior_parity.py                 # 人类可读
    python -B tools/behavior_parity.py --json          # 机读
    python -B tools/behavior_parity.py --keep          # 保留 build/_behavior_parity/ 现场
    python -B tools/behavior_parity.py --fail-on-diff  # 有「不一致」就 rc=1
    python -B tools/behavior_parity.py --only 03       # 只量文件名含 03 的文件

退出码：0 = 量完了（不管一致率高低）；2 = 工具/环境错误（没有语料、没有 g++…）；
带 `--fail-on-diff` 时，有「不一致」返回 1。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# 中文 Windows 控制台默认 GBK；报告里有 `✓` / `✗` / `⊘`，不钉编码会死在
# 打印那一步（和 tools/rename_rate.py / src/sync_docs.py 同一做法）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "demo" / "corpus"
DSH = ROOT / "src" / "dsh.py"
STAGE_ROOT = ROOT / "build" / "_behavior_parity"  # build/ 已在 .gitignore
GXX_FALLBACK = Path(r"C:\Users\haoll\Documents\shake\toolchain\mingw64\bin\g++.exe")

#: 改名目标文件里 `main` 自己的命令行参数（两边喂同一份）。目前只有它需要。
MAIN_ARGS = {
    "00_docnums.cpp": ["--list"],
}

# ---------------------------------------------------------------- 固定参数表
#
# **唯一**的输入表。探针代码由它生成，所以「同一次跑」两边必然是同一个值。
PARAMS = {
    "xs": [5, 3, 9, 1, 7, 3],          # 01_sorting：一段固定的小数组
    "xs_halves": [1, 3, 9, 5, 7, 8],   # merge_halves 要求两半各自有序
    "n": 2,                            # 03_matrix：n×n
    "a": [[1, 2], [3, 4]],             # 03_matrix：矩阵 a
    "b": [[1, 0], [1, 1]],             # 03_matrix：矩阵 b
    "text": "ab 12 cd 34",             # 04_tokenizer
    "csv": "a,b,c",                    # 02_csv_stats
    "sep": 44,                         # ASCII ','
}


def vec_literal(values):
    """`[5, 3, 9]` → `{5, 3, 9}`；C++ 与 `.eln`（--compat 第 4 条）都认这个拼写。"""
    return "{" + ", ".join(str(int(v)) for v in values) + "}"


def cpp_string(s):
    """把 Python 字符串落成 C++ 字符串字面量（语料里没有引号/反斜杠，仍然转义）。"""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def char_literal(code):
    """ASCII 码 → 字符字面量（`44` → `','`）；两边都用同一个拼写。"""
    ch = chr(int(code))
    if ch == "\\":
        return "'\\\\'"
    if ch == "'":
        return "'\\''"
    return "'" + ch + "'"


def mat_assignments(name, m, indent="    "):
    """把矩阵落成逐元素赋值语句——`.eln` 侧不支持嵌套 vector 字面量。"""
    out = []
    for i, row in enumerate(m):
        for j, v in enumerate(row):
            out.append("%s%s[%d][%d] = %d;" % (indent, name, i, j, int(v)))
    return "\n".join(out)


def fmt_params():
    """报告里的一行固定参数（矩阵用 `[[1, 2], [3, 4]]` 这种写法）。"""
    parts = []
    for k, v in PARAMS.items():
        if isinstance(v, list):
            if v and isinstance(v[0], list):
                parts.append("%s=%s" % (k, v))
            else:
                parts.append("%s=%s" % (k, vec_literal(v)))
        elif k == "sep":
            parts.append("%s=%s" % (k, char_literal(v)))
        else:
            parts.append("%s=%r" % (k, v))
    return "  ".join(parts)


# ---------------------------------------------------------------- 探针生成
#
# 每一条 = 一个「文件 + 函数 + 固定输入」的可比目标。`eln` 是追加到 `.eln` 末尾的
# 探针函数（`run --fn=probe_<fn>`），`cpp` 是 C++ driver 里对应的调用 + 打印。
# 两边的**输入表达式由同一份 PARAMS 生成**，不是各写一遍。


def _vec_probe(fn, call, xs_key="xs"):
    """把一个「改引用参数、返回 void」的函数包成「返回处理完的向量」。"""
    lit = vec_literal(PARAMS[xs_key])
    eln = (
        "vector<u64> probe_%s() {\n"
        "    vector<u64> xs%s;\n"
        "    %s;\n"
        "    return xs;\n"
        "}" % (fn, lit, call)
    )
    cpp = (
        "    std::vector<unsigned long long> xs%s;\n"
        "    %s;\n"
        "    print_vec_u64(xs);\n"
        '    std::printf("\\n");\n' % (lit, call)
    )
    return eln, cpp


def _str_vec_probe(fn, text_key, with_sep=False):
    """返回 `vector<string>` 的函数；Elench 的 string 是字节向量，两边按字节码打印。"""
    text = cpp_string(PARAMS[text_key])
    sep = ", " + char_literal(PARAMS["sep"]) if with_sep else ""
    eln = "vector<string> probe_%s() {\n    return %s(%s%s);\n}" % (fn, fn, text, sep)
    cpp = (
        "    print_vec_str(%s(std::string(%s)%s));\n"
        '    std::printf("\\n");\n' % (fn, text, sep)
    )
    return eln, cpp


def _u64_probe(fn, text_key):
    """返回标量、但实参是字符串的函数（`count_digits`）。"""
    text = cpp_string(PARAMS[text_key])
    eln = "u64 probe_%s() {\n    return %s(%s);\n}" % (fn, fn, text)
    cpp = '    std::printf("%%d\\n", %s(std::string(%s)));\n' % (fn, text)
    return eln, cpp


def _make_recipes():
    rs = []

    def add(file, fn, parts, params, note=""):
        eln, cpp = parts
        rs.append({
            "file": file, "fn": fn, "probe": "probe_" + fn,
            "eln": eln, "cpp": cpp, "params": params, "note": note,
        })

    def skip(file, fn, params, reason):
        rs.append({"file": file, "fn": fn, "skip": reason, "params": params})

    # 01_sorting：三个 void 函数都靠探针把「改完的 xs」返回出来
    add("01_sorting.cpp", "insertion_sort",
        _vec_probe("insertion_sort", "insertion_sort(xs)"),
        ["xs"], "返回排好的向量（原函数 void）")
    add("01_sorting.cpp", "merge_sort",
        _vec_probe("merge_sort", "merge_sort(xs, 0, xs.size())"),
        ["xs"], "返回排好的向量（原函数 void）")
    add("01_sorting.cpp", "merge_halves",
        _vec_probe("merge_halves", "merge_halves(xs, 0, 3, 6)", "xs_halves"),
        ["xs_halves"], "两半各自有序的输入")

    # 02_csv_stats
    add("02_csv_stats.cpp", "split",
        _str_vec_probe("split", "csv", with_sep=True),
        ["csv", "sep"], "string 是字节向量，两边按字节码打印")
    skip("02_csv_stats.cpp", "count_fields", ["a", "b"],
         "返回 map：run 只渲染成 `map[N]`（内容不可观测），"
         "逐字节比会变成「看起来一样」，不比")

    # 03_matrix
    add("03_matrix.cpp", "zeros",
        ("Mat probe_zeros() {\n    return zeros(%d);\n}" % PARAMS["n"],
         "    print_mat(zeros(%d));\n" % PARAMS["n"]),
        ["n"])
    add("03_matrix.cpp", "matmul",
        ("Mat probe_matmul() {\n"
         "    Mat a = zeros(%d);\n%s\n"
         "    Mat b = zeros(%d);\n%s\n"
         "    return matmul(a, b);\n"
         "}" % (PARAMS["n"], mat_assignments("a", PARAMS["a"]),
                PARAMS["n"], mat_assignments("b", PARAMS["b"])),
         "    Mat a = zeros(%d);\n%s\n"
         "    Mat b = zeros(%d);\n%s\n"
         "    print_mat(matmul(a, b));\n"
         % (PARAMS["n"], mat_assignments("a", PARAMS["a"]),
            PARAMS["n"], mat_assignments("b", PARAMS["b"]))),
        ["n", "a", "b"], "与 main 里同一份矩阵")

    # 04_tokenizer
    add("04_tokenizer.cpp", "count_digits", _u64_probe("count_digits", "text"),
        ["text"])
    add("04_tokenizer.cpp", "words", _str_vec_probe("words", "text"),
        ["text"], "string 是字节向量，两边按字节码打印")

    return rs


RECIPES = _make_recipes()

#: C++ driver 的打印助手。**格式必须与 `run` 的返回值渲染逐字节一致**：
#: `[1, 2, 3]`、`[[0, 0], [0, 0]]`、`[[97, 98], [49, 50]]`。
CPP_HELPERS = r"""
static void print_elm(unsigned long long v) { std::printf("%llu", v); }

template <class V>
static void print_vec_u64(const V& v) {
    std::printf("[");
    for (std::size_t i = 0; i < v.size(); ++i) {
        if (i) std::printf(", ");
        print_elm(v[i]);
    }
    std::printf("]");
}

static void print_vec_str(const std::vector<std::string>& v) {
    std::printf("[");
    for (std::size_t i = 0; i < v.size(); ++i) {
        if (i) std::printf(", ");
        std::printf("[");
        for (std::size_t j = 0; j < v[i].size(); ++j) {
            if (j) std::printf(", ");
            std::printf("%d", static_cast<int>(static_cast<unsigned char>(v[i][j])));
        }
        std::printf("]");
    }
    std::printf("]");
}

template <class M>
static void print_mat(const M& m) {
    std::printf("[");
    for (std::size_t i = 0; i < m.size(); ++i) {
        if (i) std::printf(", ");
        print_vec_u64(m[i]);
    }
    std::printf("]\n");
}
"""


def build_eln_probe(source_text, recipes):
    """同名 `.cpp` 的文本 + 探针函数（追加在末尾）。"""
    wrappers = [r["eln"] for r in recipes if "eln" in r]
    if not wrappers:
        return source_text
    return source_text.rstrip("\n") + "\n\n" + "\n\n".join(wrappers) + "\n"


def build_cpp_driver(source_path, recipes):
    """把源文件 `#include` 进来（把 `main` 改名避开冲突），再写一个按名字分派的 driver。

    `.cpp` 里的函数是 `static`，但 `#include` 到同一个 TU 后就能直接调；
    这样不用改源文件、也不用为每个函数单独编译一次。
    """
    dispatch = []
    for r in recipes:
        if "cpp" not in r:
            continue
        dispatch.append(
            '    if (which == "%s") {\n%s        return 0;\n    }'
            % (r["probe"], r["cpp"]))
    inc = str(source_path).replace("\\", "/")
    return (
        "#include <cstdio>\n"
        "#include <cstring>\n"
        "#include <string>\n"
        "#include <vector>\n"
        "#define main elench_corpus_main\n"
        '#include "%s"\n'
        "#undef main\n"
        "%s\n"
        "int main(int argc, char** argv) {\n"
        "    if (argc < 2) {\n"
        '        std::fprintf(stderr, "probe name required\\n");\n'
        "        return 2;\n"
        "    }\n"
        "    std::string which(argv[1]);\n"
        "%s\n"
        '    std::fprintf(stderr, "unknown probe: %%s\\n", argv[1]);\n'
        "    return 2;\n"
        "}\n"
    ) % (inc, CPP_HELPERS, "\n".join(dispatch))


# ---------------------------------------------------------------- 子进程
def run_capture(cmd, cwd, env, timeout):
    """跑一条命令，按**字节**收 stdout/stderr。超时也返回（带 timeout 标记）。"""
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True,
                           timeout=timeout)
        return {
            "cmd": cmd, "rc": p.returncode, "out": p.stdout, "err": p.stderr,
            "secs": time.monotonic() - t0, "timeout": False,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "cmd": cmd, "rc": None, "out": e.stdout or b"", "err": e.stderr or b"",
            "secs": time.monotonic() - t0, "timeout": True,
        }


def resolve_gxx(explicit=None):
    cands = []
    if explicit:
        cands.append(Path(explicit))
    env = os.environ.get("ELENCH_GXX")
    if env:
        cands.append(Path(env))
    cands.append(GXX_FALLBACK)
    which = shutil.which("g++")
    if which:
        cands.append(Path(which))
    for c in cands:
        if c.is_file():
            return c
    return None


def build_env(gxx, stage):
    """PATH 里加 mingw64\\bin（否则 exe 找不到 libstdc++ DLL，静默失败）；
    TMP/TEMP 指到 g++ **自己能写**的目录（系统 TEMP 会 Permission denied）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PATH"] = str(gxx.parent) + os.pathsep + env.get("PATH", "")
    tmp = Path(stage) / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env["TMP"] = env["TEMP"] = str(tmp)
    return env


# ---------------------------------------------------------------- 纯函数（可单测）
def strip_return_line(raw):
    """剥掉 `run` 打印的 **main 返回值** 那一行。

    `run` 的契约是「把 main 的结果打出来」，那不是程序 stdout。C++ exe 不会打
    这一行；不剥掉的话每个 main 都会恒定差一行。只剥**最后一行且是整数**的情况，
    避免把程序自己打印的 `0` 误删（程序打印的内容在返回值之前）。
    """
    lines = raw.splitlines(keepends=True)
    if not lines:
        return raw
    body = lines[-1].rstrip(b"\r\n")
    if re.fullmatch(rb"-?\d+", body):
        return b"".join(lines[:-1])
    return raw


def diff_report(a, b, limit=5):
    """逐字节比较的**人类可读**差异：字节偏移 + 前几个不同行的两边内容。"""
    if a == b:
        return []
    n = min(len(a), len(b))
    off = n
    for i in range(n):
        if a[i] != b[i]:
            off = i
            break
    lines = ["长度 g++=%dB / elench=%dB，首个差异在字节 %d" % (len(a), len(b), off)]
    al = a.decode("utf-8", "replace").splitlines()
    bl = b.decode("utf-8", "replace").splitlines()
    i = 0
    while i < min(len(al), len(bl)) and al[i] == bl[i]:
        i += 1
    shown = 0
    for k in range(i, max(len(al), len(bl))):
        if shown >= limit:
            lines.append("  …（还有更多差异，省略）")
            break
        x = al[k] if k < len(al) else "<没有这一行>"
        y = bl[k] if k < len(bl) else "<没有这一行>"
        if x == y:
            continue
        lines.append("  第 %d 行：" % (k + 1))
        lines.append("    g++    | %r" % x)
        lines.append("    elench | %r" % y)
        shown += 1
    if shown == 0 and len(al) == len(bl):
        lines.append("  （行内容相同，但字节不同：多半是行尾/编码差异）")
    return lines


def meaningful_stderr(res):
    raw = res["err"].decode("utf-8", "replace") if res.get("err") else ""
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "本模块没有 .tu" in line:  # run 的固定提示，不是失败原因
            continue
        out.append(line)
    return out


def elench_failure_reason(res, timeout=120.0):
    if res.get("timeout"):
        return "elench run 超时（>%.0fs）" % timeout
    lines = meaningful_stderr(res)
    blob = "\n".join(lines)
    if "未通过" in blob:
        diag = next((ln for ln in lines if "E-" in ln), lines[0] if lines else "")
        return "elench check 未通过：%s" % diag
    if "找不到函数" in blob:
        return "elench 里没有同名函数：%s" % (lines[0] if lines else "?")
    if "运行失败" in blob:
        return "解释器运行时异常：%s" % (lines[0] if lines else "?")
    if not lines:
        return "elench run rc=%s（无 stderr）" % res.get("rc")
    # 裸 Traceback（dsh.py 自己崩了）时，第一行没有信息，取**最后一行**异常。
    if "Traceback (most recent call last)" in blob:
        return "elench 进程崩溃（裸 Traceback）：%s" % lines[-1]
    return "elench run rc=%s：%s" % (res.get("rc"), lines[0])


def printf_gap_hint(source_text, eln_out):
    """elench 输出里是否**原样打出**了源码里的 `printf` 格式串。

    `src/compat.py` 把 `std::printf` 改名成 `print`，但格式串仍按本语言的 `{}`
    洞解释——`%llu` / `%d` 会原样打出来。这是**已知语义差**，不是新 bug；
    标出来是为了不让它淹没真正的差异。
    """
    if "printf" not in source_text:
        return False
    for m in re.finditer(r'"([^"\n]*%[^"\n]*)"', source_text):
        fmt = m.group(1).split("\\")[0]  # 去掉 `\n` 之后的部分
        if fmt and fmt.encode("utf-8") in eln_out:
            return True
    return False


def printf_equivalent(source_text, cpp_out, eln_out):
    """**只有**「格式串差」时返回 True：把 elench 输出里原样打出的格式串片段
    去掉、按空白切 token 后，和 g++ 输出相同。

    这样能把 `03_matrix::main`（格式串 + 数值都错）和 `01_sorting::main`
    （纯格式串差）分开——否则一个真 bug 会被「已知语义差」掩护过去。
    """
    if "printf" not in source_text:
        return False
    frags = []
    for m in re.finditer(r'"([^"\n]*%[^"\n]*)"', source_text):
        frag = m.group(1).split("\\")[0]
        if frag:
            frags.append(frag)
    if not frags or not any(f.encode("utf-8") in eln_out for f in frags):
        return False
    text = eln_out.decode("utf-8", "replace")
    for frag in sorted(set(frags), key=len, reverse=True):
        text = text.replace(frag, " ")
    return text.split() == cpp_out.decode("utf-8", "replace").split()


FUNC_RE = re.compile(
    r"^(?:static\s+)?[A-Za-z_][A-Za-z0-9_:<>,\s\*&]*?\b"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*\{", re.MULTILINE)
_FUNC_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "return", "else", "do", "struct", "class",
    "namespace", "template", "catch", "sizeof",
})


def top_level_functions(source_text):
    """粗粒度列出**行首**的函数定义名（顶层函数；缩进的方法/构造不算）。

    只用于「哪些函数没被固定参数表覆盖」，不参与判定，所以宁可漏、不要误报。
    """
    names = []
    for m in FUNC_RE.finditer(source_text):
        name = m.group(1)
        if name in _FUNC_KEYWORDS:
            continue
        if name not in names:
            names.append(name)
    return names


# ---------------------------------------------------------------- 度量
class Runner:
    def __init__(self, gxx, env, stage, timeout):
        self.gxx = gxx
        self.env = env
        self.stage = Path(stage)
        self.timeout = timeout

    def compile(self, source, exe):
        cmd = [str(self.gxx), "-std=c++17", "-O0", "-w", "-o", str(exe), str(source)]
        return run_capture(cmd, ROOT, self.env, self.timeout)

    def elench(self, eln, fn, extra=()):
        cmd = [sys.executable, "-B", str(DSH), "run", "--compat",
               str(eln), "--fn=" + fn] + list(extra)
        return run_capture(cmd, ROOT, self.env, self.timeout)


def sanity_check(runner):
    """量之前先确认**度量工具本身**能用（和 tools/rename_rate.py 的编码坑同理：
    把「度量失败」误报成「被测物失败」是最坏的情况）。

    用一个最小 `.eln` 走完整条 `run --compat` 链；`src/` 正在被并发修改时，
    这里会先失败，而不是把 6 个文件的 34 个目标全报成「跑不了」。
    """
    p = runner.stage / "_sanity.eln"
    p.write_text("u64 main() {\n    return 0;\n}\n", encoding="utf-8")
    res = runner.elench(p, "main")
    if res["rc"] == 0 and res["out"].strip() == b"0":
        return True, ""
    detail = "\n".join(meaningful_stderr(res)[-8:]) or "(无 stderr)"
    return False, detail


def _record(name, kind, note="", params=None):
    return {
        "fn": name, "kind": kind, "status": "not_comparable",
        "note": note, "params": list(params or []), "reason": None,
        "cpp_bytes": None, "eln_bytes": None, "diff": [], "preview": "",
        "cpp_secs": None, "eln_secs": None, "cause": None,
        "deterministic": None, "printf_format": False,
    }


def _first_line(raw):
    if not raw:
        return "<空输出>"
    txt = raw.decode("utf-8", "replace").splitlines()
    if not txt:
        return "<空输出>"
    line = txt[0]
    if len(line) > 64:
        line = line[:61] + "..."
    return line


def _judge(rec, source_text, cpp_out, eln_out):
    """两边都跑通、都确定 → 只做一件事：逐字节比。"""
    rec["diff"] = diff_report(cpp_out, eln_out)
    rec["preview"] = _first_line(eln_out)
    rec["printf_format"] = printf_gap_hint(source_text, eln_out)
    if cpp_out == eln_out:
        rec["status"] = "consistent"
        rec["cause"] = None
        return
    rec["status"] = "inconsistent"
    if printf_equivalent(source_text, cpp_out, eln_out):
        rec["cause"] = "printf_semantics"   # 已知语义差：去掉格式串后数值相同
    else:
        rec["cause"] = "behavior_diff"      # 数值/行数也不同 → 真差异


def _compare(runner, rec, cpp_cmd, eln_cmd, source_text, strip=False):
    """两边各跑两次（确定性）→ 分类。"""
    c1 = run_capture(cpp_cmd, ROOT, runner.env, runner.timeout)
    c2 = run_capture(cpp_cmd, ROOT, runner.env, runner.timeout)
    e1 = run_capture(eln_cmd, ROOT, runner.env, runner.timeout)
    e2 = run_capture(eln_cmd, ROOT, runner.env, runner.timeout)
    cpp_out = c1["out"]
    eln_out = strip_return_line(e1["out"]) if strip else e1["out"]
    rec.update({
        "cpp_bytes": len(cpp_out), "eln_bytes": len(eln_out),
        "cpp_secs": round(c1["secs"], 2), "eln_secs": round(e1["secs"], 2),
        "deterministic": (c1["out"] == c2["out"] and c1["rc"] == c2["rc"]
                          and e1["out"] == e2["out"] and e1["rc"] == e2["rc"]),
    })
    if c1["timeout"] or e1["timeout"]:
        rec["reason"] = "超时（>%.0fs）" % runner.timeout
    elif c1["rc"] != 0:
        err = c1["err"].decode("utf-8", "replace").strip().splitlines()
        rec["reason"] = "g++ 侧退出码 %s：%s" % (c1["rc"], err[0] if err else "?")
    elif e1["rc"] != 0:
        reason = elench_failure_reason(e1, runner.timeout)
        rec["reason"] = reason
        if "找不到函数" in reason:
            rec["cause"] = "no_function"      # 两边函数名不同 / 没有这个函数
        elif "check 未通过" in reason:
            rec["cause"] = "file_level"       # 文件本身不可运行（不是崩溃）
        else:
            rec["cause"] = "interpreter_crash"
    elif c1["out"] != c2["out"] or c1["rc"] != c2["rc"]:
        rec["reason"] = "非确定性：g++ 侧同一输入两次输出不同"
        rec["cause"] = "nondeterministic"
    elif e1["out"] != e2["out"] or e1["rc"] != e2["rc"]:
        rec["reason"] = "非确定性：elench 侧同一输入两次输出不同"
        rec["cause"] = "nondeterministic"
    else:
        _judge(rec, source_text, cpp_out, eln_out)


def measure_file(f, runner):
    """量一个 `.cpp`：编译 → 建 `.eln` → 逐个目标比较。"""
    source_text = f.read_text(encoding="utf-8", errors="replace")
    recipes = [r for r in RECIPES if r["file"] == f.name]
    probe_dir = runner.stage / f.stem
    probe_dir.mkdir(parents=True, exist_ok=True)
    eln_path = probe_dir / (f.stem + ".eln")
    eln_path.write_text(build_eln_probe(source_text, recipes), encoding="utf-8")

    info = {
        "file": f.name, "targets": [], "file_reason": None,
        "cpp_compile": None, "cpp_compile_secs": None, "function_count": None,
    }

    # ---- ① g++ 编译原文件（main 用这一份） ----
    exe_main = probe_dir / (f.stem + "_main.exe")
    cres = runner.compile(f, exe_main)
    info["cpp_compile"] = (cres["rc"] == 0 and not cres["timeout"])
    info["cpp_compile_secs"] = round(cres["secs"], 2)
    if not info["cpp_compile"]:
        err_lines = meaningful_stderr(cres)[:3]
        reason = "g++ 编译失败：%s" % (err_lines[0] if err_lines else "?")
        info["file_reason"] = reason
        for name in top_level_functions(source_text) or ["main"]:
            rec = _record(name, "main" if name == "main" else "function")
            rec["reason"] = reason + "（C++ 侧不可用，无法比）"
            info["targets"].append(rec)
        return info

    # ---- ② elench 侧 main：文件级解析/检查失败会挡掉该文件所有目标 ----
    main_argv = MAIN_ARGS.get(f.name, [])
    main_eln = runner.elench(eln_path, "main",
                             extra=["--arg=" + a for a in main_argv])
    file_level_fail = (main_eln["rc"] not in (0, None)
                       and "未通过" in main_eln["err"].decode("utf-8", "replace"))
    if file_level_fail:
        reason = elench_failure_reason(main_eln, runner.timeout)
        info["file_reason"] = reason
        names = top_level_functions(source_text) or ["main"]
        info["function_count"] = len(names)
        for name in names:
            rec = _record(name, "main" if name == "main" else "function")
            rec["reason"] = reason
            rec["cause"] = "file_level"
            info["targets"].append(rec)
        return info

    # ---- ③ main ----
    main_rec = _record("main", "main", params=["argv"])
    info["targets"].append(main_rec)
    # main 的 elench 侧**多打一行返回值**（run 的契约），那不是程序 stdout：
    # strip=True 把它剥掉再比。函数探针返回的就是要看的值，不剥。
    _compare(runner, main_rec,
             [str(exe_main)] + list(main_argv),
             [sys.executable, "-B", str(DSH), "run", "--compat", str(eln_path),
              "--fn=main"] + ["--arg=" + a for a in main_argv],
             source_text, strip=True)

    # ---- ④ driver（只有需要探针时才编） ----
    exe_probe = probe_dir / (f.stem + "_probe.exe")
    probe_recipes = [r for r in recipes if "cpp" in r]
    driver_ok = True
    if probe_recipes:
        drv = probe_dir / (f.stem + "_driver.cpp")
        drv.write_text(build_cpp_driver(f.resolve(), probe_recipes), encoding="utf-8")
        dres = runner.compile(drv, exe_probe)
        if dres["rc"] != 0 or dres["timeout"]:
            driver_ok = False
            err_lines = meaningful_stderr(dres)[:3]
            reason = "g++ 探针 driver 编译失败：%s" % (err_lines[0] if err_lines else "?")

    # ---- ⑤ 固定参数表里的函数目标 ----
    for r in recipes:
        rec = _record(r["fn"], "function", r.get("note", ""), r.get("params"))
        info["targets"].append(rec)
        if "skip" in r:
            rec["reason"] = r["skip"]
            rec["cause"] = "unobservable"
            continue
        if not driver_ok:
            rec["reason"] = reason
            continue
        _compare(runner, rec,
                 [str(exe_probe), r["probe"]],
                 [sys.executable, "-B", str(DSH), "run", "--compat",
                  str(eln_path), "--fn=" + r["probe"]],
                 source_text)

    # ---- ⑥ 没被参数表覆盖的顶层函数 ----
    covered = {r["fn"] for r in recipes} | {"main"}
    for name in top_level_functions(source_text):
        if name in covered:
            continue
        rec = _record(name, "function")
        rec["reason"] = ("没有固定参数表探针（实参是 string / 嵌套容器 / "
                         "N 个标量等，run CLI 不支持）")
        rec["cause"] = "no_recipe"
        info["targets"].append(rec)
    return info


# ---------------------------------------------------------------- 汇总与报告
def summarize(files):
    tot_cmp = tot_ok = tot_bad = tot_nc = 0
    det_total = det_ok = 0
    defects = []
    for info in files:
        for t in info["targets"]:
            if t["status"] == "consistent":
                tot_cmp += 1
                tot_ok += 1
            elif t["status"] == "inconsistent":
                tot_cmp += 1
                tot_bad += 1
            else:
                tot_nc += 1
            if t["deterministic"] is not None:
                det_total += 1
                det_ok += int(t["deterministic"])
            if t["cause"] in ("behavior_diff", "interpreter_crash",
                              "nondeterministic"):
                defects.append({"file": info["file"], "fn": t["fn"],
                                "cause": t["cause"], "detail": t["diff"] or
                                [t.get("reason") or ""]})
    return {
        "files": len(files),
        "comparable": tot_cmp,
        "consistent": tot_ok,
        "inconsistent": tot_bad,
        "not_comparable": tot_nc,
        "parity_rate": round(tot_ok / tot_cmp, 4) if tot_cmp else None,
        "deterministic": {"ok": det_ok, "total": det_total},
        "defects": defects,
    }


def _pct(a, b):
    return 0.0 if b == 0 else 100.0 * a / b


def print_report(files, gxx, summary):
    print("=== 行为一致率 · %s（%d 个 .cpp）===" % (
        CORPUS.relative_to(ROOT).as_posix(), len(files)))
    print("  g++     : %s" % gxx)
    print("  elench  : python -B src/dsh.py run --compat")
    print("  sanity  : ✓ elench 自身可运行（最小 .eln 走通整条 run 链）")
    print("  固定参数: %s" % fmt_params())
    print()

    file_stats = []
    for info in files:
        cmp_n = ok_n = 0
        for t in info["targets"]:
            if t["status"] in ("consistent", "inconsistent"):
                cmp_n += 1
                ok_n += int(t["status"] == "consistent")
        file_stats.append((info["file"], cmp_n, ok_n))
        if info["file_reason"]:
            print("[%s]  跑不了（文件级）：%s" % (info["file"], info["file_reason"]))
            if info.get("function_count"):
                print("  该文件 %d 个顶层函数全部不可测（逐条见下）"
                      % info["function_count"])
            print()
            continue
        if cmp_n:
            print("[%s]  可比 %d / 一致 %d" % (info["file"], cmp_n, ok_n))
        else:
            print("[%s]  无可比目标" % info["file"])
        for t in info["targets"]:
            icon = {"consistent": "✓", "inconsistent": "✗",
                    "not_comparable": "⊘"}[t["status"]]
            tag = "main" if t["kind"] == "main" else "fn"
            if t["status"] == "consistent":
                print("  %s %-6s %-24s 一致（%s B）  输出 %s" % (
                    icon, tag, t["fn"], t["cpp_bytes"], t["preview"]))
            elif t["status"] == "inconsistent":
                if t["cause"] == "printf_semantics":
                    cause = "（差异只来自 printf 格式串——已知语义差，compat 明确不补）"
                elif t["printf_format"]:
                    cause = "（含 printf 格式串差，但**去掉后数值仍不同**）"
                else:
                    cause = ""
                print("  %s %-6s %-24s 不一致（g++ %s B / elench %s B）%s" % (
                    icon, tag, t["fn"], t["cpp_bytes"], t["eln_bytes"], cause))
            else:
                reason = t["reason"] or "?"
                if len(reason) > 110:
                    reason = reason[:107] + "..."
                print("  %s %-6s %-24s 跑不了：%s" % (icon, tag, t["fn"], reason))
        print()

    print("--- 逐文件（一致/可比）---")
    print("  " + "   ".join("%s %d/%d" % (n.replace(".cpp", ""), ok, cmp)
                            for n, cmp, ok in file_stats))
    print()
    print("--- 汇总 ---")
    print("  可比目标：%d   一致 %d   不一致 %d" % (
        summary["comparable"], summary["consistent"], summary["inconsistent"]))
    print("  行为一致率 = %d/%d = %.1f%%   （只算两边都能跑的目标）" % (
        summary["consistent"], summary["comparable"],
        _pct(summary["consistent"], summary["comparable"])))
    det = summary["deterministic"]
    print("  确定性：%d/%d 个目标在**两边各跑两次**后输出逐字节相同" % (
        det["ok"], det["total"]))
    printf_only = sum(1 for i in files for t in i["targets"]
                      if t["status"] == "inconsistent"
                      and t["cause"] == "printf_semantics")
    if printf_only:
        print("  其中 %d 条不一致**只**来自 printf 格式串（已知语义差，"
              "src/compat.py 明确不补）" % printf_only)
    print("  跑不了：%d 条（文件级 %d 个文件）" % (
        summary["not_comparable"],
        sum(1 for i in files if i.get("file_reason"))))

    print()
    bad = [(i["file"], t) for i in files for t in i["targets"]
           if t["status"] == "inconsistent" and t["cause"] == "behavior_diff"]
    print("=== 不一致（意外差异 %d）· 逐条 diff ===" % len(bad))
    if not bad:
        print("  （没有意外差异；printf 格式串差异见上）")
    for fname, t in bad:
        print("  · %s :: %s" % (fname, t["fn"]))
        for line in t["diff"]:
            print("      " + line)

    print()
    print("=== 跑不了（%d）· 逐条原因 ===" % summary["not_comparable"])
    for info in files:
        if info.get("file_reason"):
            print("  [文件级] %s" % info["file"])
            print("      %s" % info["file_reason"])
            if info.get("function_count"):
                print("      该文件 %d 个顶层函数全部不可测（原因同上）"
                      % info["function_count"])
            continue
        for t in info["targets"]:
            if t["status"] != "not_comparable":
                continue
            print("  [目标级] %s :: %s" % (info["file"], t["fn"]))
            print("      %s" % (t["reason"] or "?"))

    print()
    print("=== 缺陷（崩溃 / 非确定性 / 意外差异）===")
    if not summary["defects"]:
        print("  （没有）")
    for d in summary["defects"]:
        print("  · [%s] %s :: %s" % (
            {"interpreter_crash": "崩溃", "nondeterministic": "非确定性",
             "behavior_diff": "语义差"}.get(d["cause"], d["cause"]),
            d["file"], d["fn"]))
        for line in d["detail"][:6]:
            print("      " + line)
    print()


def print_json(files, gxx, summary):
    payload = {
        "corpus": CORPUS.relative_to(ROOT).as_posix(),
        "gxx": str(gxx),
        "params": PARAMS,
        "files": files,
        "totals": {k: v for k, v in summary.items() if k != "defects"},
        "defects": summary["defects"],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="行为一致率：同一函数，g++ 与 run --compat 的 stdout 逐字节比较")
    ap.add_argument("--json", action="store_true", help="机读输出")
    ap.add_argument("--keep", action="store_true",
                    help="保留 build/_behavior_parity/ 现场")
    ap.add_argument("--fail-on-diff", action="store_true",
                    help="有「不一致」就返回 1")
    ap.add_argument("--only", default=None, help="只量文件名含这个子串的文件")
    ap.add_argument("--gxx", default=None, help="g++ 路径（默认本机 MinGW）")
    ap.add_argument("--timeout", type=float, default=120.0,
                    help="单条命令超时（秒）")
    args = ap.parse_args(argv)

    files = sorted(CORPUS.glob("*.cpp"))
    if args.only:
        files = [f for f in files if args.only in f.name]
    if not files:
        print("没有语料：%s" % CORPUS, file=sys.stderr)
        return 2

    gxx = resolve_gxx(args.gxx)
    if gxx is None:
        print("找不到 g++（试过 --gxx / $ELENCH_GXX / %s / PATH）" % GXX_FALLBACK,
              file=sys.stderr)
        return 2

    STAGE_ROOT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="run_", dir=str(STAGE_ROOT)))
    env = build_env(gxx, stage)
    runner = Runner(gxx, env, stage, args.timeout)

    try:
        ok, detail = sanity_check(runner)
        if not ok:
            print("✗ elench 自身跑不起来，度量中止（不是被测物的问题）：",
                  file=sys.stderr)
            print(detail, file=sys.stderr)
            return 2
        results = [measure_file(f, runner) for f in files]
    finally:
        if args.keep:
            print("\n（现场保留在 %s）" % stage, file=sys.stderr)
        else:
            shutil.rmtree(stage, ignore_errors=True)
            try:
                STAGE_ROOT.rmdir()
            except OSError:
                pass

    summary = summarize(results)
    if args.json:
        print_json(results, gxx, summary)
    else:
        print_report(results, gxx, summary)
    if args.fail_on_diff and summary["inconsistent"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
