"""rename_rate.py —— 「改名即用」的唯一度量尺。

用法：
    python -B tools/rename_rate.py                 # 人类可读
    python -B tools/rename_rate.py --json          # 机读

对 `demo/corpus/*.cpp` 逐个：复制成临时 `.eln` → `check`（可选 `--compat`）→
统计退出码与诊断码频率。**不做任何"通过率"的主观解释，只给数。**
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

# 报告里有 `✓` / `⚠`；Windows 控制台默认 GBK 会在打印时崩掉（rc=1），
# 把"度量失败"混进"被测物失败"。与 src/sync_docs.py 同一做法。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "demo" / "corpus"
DSH = ROOT / "src" / "dsh.py"
CODE = re.compile(r"(E-[A-Za-z-]+|W-[A-Za-z-]+)")


def check_file(src: Path, compat: bool, work: Path) -> dict:
    """把 .cpp 复制成 .eln 再 check。返回 {rc, codes}。"""
    eln = work / (src.stem + ".eln")
    shutil.copyfile(src, eln)
    args = [sys.executable, "-B", str(DSH), "check", str(eln)]
    if compat:
        args.append("--compat")
    r = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       # 子进程的诊断里带 `✓` / `⚠` 等非 GBK 字符；不显式指定
                       # UTF-8 时，Windows 控制台编码会让**只有 warning 的文件**
                       # 在打印时崩成 UnicodeEncodeError（rc=1），把"能过"误判成
                       # "有错"。度量尺必须比被测物稳定，所以这里钉死编码。
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    out = (r.stdout or "") + (r.stderr or "")
    codes = [c for c in CODE.findall(out) if c.startswith("E-")]
    return {"rc": r.returncode, "codes": codes, "lines": out.count("\n")}


def main() -> int:
    as_json = "--json" in sys.argv
    files = sorted(CORPUS.glob("*.cpp"))
    if not files:
        sys.exit("没有语料：%s" % CORPUS)

    rows, freq_off, freq_on = [], Counter(), Counter()
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        for f in files:
            off = check_file(f, False, work)
            on = check_file(f, True, work)
            freq_off.update(off["codes"])
            freq_on.update(on["codes"])
            rows.append({
                "file": f.name,
                "lines": len(f.read_text(encoding="utf-8", errors="replace").splitlines()),
                "rc_off": off["rc"], "n_off": len(off["codes"]),
                "rc_on": on["rc"], "n_on": len(on["codes"]),
            })

    ok = sum(1 for r in rows if r["rc_on"] == 0)
    total = len(rows)

    if as_json:
        print(json.dumps({
            "corpus": str(CORPUS.relative_to(ROOT)),
            "files": rows,
            "pass_rate": round(ok / total, 4),
            "codes_without_compat": freq_off.most_common(),
            "codes_with_compat": freq_on.most_common(),
        }, ensure_ascii=False, indent=2))
        return 0

    print("=== 改名即用 · 语料度量（%d 个文件）===" % total)
    print("  %-22s %6s %10s %10s" % ("文件", "行数", "无compat", "有compat"))
    for r in rows:
        print("  %-22s %6d %10s %10s" % (
            r["file"], r["lines"],
            "%d 错" % r["n_off"] if r["n_off"] else "✓ 通过",
            "%d 错" % r["n_on"] if r["n_on"] else "✓ 通过"))
    print("\n  改名通过率（--compat）：%d/%d = %.0f%%" % (ok, total, 100.0 * ok / total))

    # **通过率是文件级、非线性的**：一个文件 53 条错，修掉 52 条还是 0%。
    # 所以再给一个**梯度指标**：剩余错误总数。它才是每轮该看的东西。
    tot_on = sum(r["n_on"] for r in rows)
    tot_off = sum(r["n_off"] for r in rows)
    print("  剩余错误总数：--compat %d 条 / 不带开关 %d 条" % (tot_on, tot_off))
    print("  （通过率是「全绿才算」的文件级指标；本轮该看的是**错误总数下降**）")

    print("\n=== 挡路的诊断码（--compat 下，按频率）===")
    if not freq_on:
        print("  （没有）")
    for code, n in freq_on.most_common(12):
        print("  %-30s %3d" % (code, n))
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
