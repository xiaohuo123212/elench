# -*- coding: utf-8 -*-
"""`tools/ai_run.py` 的**纯逻辑**测试：不跑模型、不编译、不碰真实验目录。

## 它守的是什么

`ai_run.py` 是 KPI③ 的**账本**：它不产生分数，它决定"这个分数算不算证据"。
所以这里测的不是"能不能算"，而是四条**必须拒绝**的边界：

1. **元数据缺失 → 拒绝打分**。11-12 那 4 个实例就是"数字在、归因没了"，
   于是每个数字都得重跑；这条一松，整个 KPI 就回到噪声。
2. **材料 sha256 不符 → 报错并指名**。尺子变了还接着用，历史数字就不可比。
3. **隔离目录里没有 `参考.eln`**（也没有 `打分.py` / 历史提交 / 提示词模板）——
   否则测的是抄。
4. **"未交"不是"通过"**：`full_pass` 只数三列同时成立且真交了的题；
   `kpi_countable` 只在"元数据齐 + 干净 + 12 题"时才是 true。

`score` 的真打分不在这里测（那要跑 `打分.py` → 12 题 × 8 条命令，不在 2 秒内）：
这里测的是它**打分之前**的全部判定，以及记录/账本/`list` 的结构。

## 跑法

    python -B tests/cases/test_ai_run.py        # 约 0.5 秒

`tests/runner.py` 会自动发现它（`test_*.py` + 末行「全部通过」）。
"""
import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ai_run                                            # noqa: E402

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


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def raises(fn, *a, **kw):
    """返回 (是否抛 Refusal, 错误文本)。别的异常直接算失败。"""
    try:
        fn(*a, **kw)
    except ai_run.Refusal as e:
        return True, str(e)
    return False, ""


# ------------------------------------------------------------------ 假实验目录

PROBLEMS = (("01_甲", "alpha"), ("02_乙", "beta"))


def make_bench(root):
    """造一个**两题**的迷你实验目录：结构与真实目录同形，但不依赖 elench。"""
    bench = os.path.join(root, "bench")
    mat = os.path.join(bench, "材料")
    write(os.path.join(mat, "语法速查.md"), "# 语法速查\n\nfor 循环的写法……\n")
    write(os.path.join(mat, "样例1.tu"), "spec f(const vector<u64>& xs) -> u64 {}\n")
    write(os.path.join(mat, "样例1.eln"), "u64 f(const vector<u64>& xs) { return 0; }\n")
    write(os.path.join(mat, "样例2.tu"), "spec g(const vector<u64>& xs) -> u64 {}\n")
    write(os.path.join(mat, "样例2.eln"), "u64 g(const vector<u64>& xs) { return 1; }\n")
    write(os.path.join(mat, "样例-判定输出.txt"), "check: ok\ncost: 达成\n")
    write(os.path.join(mat, "给AI的提示词.md"),
          "# 提示词（不发给 AI）\n\n```text\n"
          "材料：<材料目录>\n题目：<题目目录>\n提交：<提交目录>\n"
          "不许读 <语言仓库>。\n```\n")
    for name, fn in PROBLEMS:
        d = os.path.join(bench, name)
        write(os.path.join(d, "题目.md"), f"# {name}\n\n求一个数。\n")
        write(os.path.join(d, "spec.tu"),
              f"spec {fn}(const vector<u64>& xs) -> u64 {{ target {{ time: {{ max: O(xs.size()) }} }} }}\n")
        write(os.path.join(d, "cases.json"),
              json.dumps({"fn": fn,
                          "cases": [{"args": [{"vec": []}], "expect": "0", "why": "空"}]},
                         ensure_ascii=False))
        write(os.path.join(d, "参考.eln"), f"u64 {fn}(const vector<u64>& xs) {{ return 0; }}\n")
    write(os.path.join(bench, "打分.py"), "# 假的打分器：这个测试不跑它\n")
    write(os.path.join(bench, "README.md"), "# 协议\n")
    write(os.path.join(bench, "结果-实测.md"), "# 上一次的分数\n")
    os.makedirs(os.path.join(bench, "提交-实例9"))
    write(os.path.join(bench, "提交-实例9", "01_甲.eln"), "u64 alpha() { return 0; }\n")
    return bench


def prepare_args(bench, **over):
    base = dict(bench=bench, provider="p", model="m", version="v1", temperature="0",
                legacy_reason=None, run_id="t1", out=None, no_cases=False,
                no_prompt_file=True, note=None)
    base.update(over)
    return SimpleNamespace(**base)


def score_args(bench, **over):
    base = dict(bench=bench, provider=None, model=None, version=None, temperature=None,
                legacy_reason=None, run=None, sub=None, contamination=None,
                run_id=None, json=None, ledger=None, no_record=False,
                print_json=False, allow_empty=False, note=None)
    base.update(over)
    return SimpleNamespace(**base)


def quiet(fn, *a, **kw):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn(*a, **kw)
    return rc, out.getvalue() + err.getvalue()


# ------------------------------------------------------------------ [1] 元数据

def test_metadata():
    print("[1] 元数据：4 个字段缺一个就拒绝；unknown 必须带理由")
    ok, msg = raises(ai_run.make_attribution, None, "m", "v1", "0")
    check(ok and "provider" in msg, "缺 provider → 拒绝", msg[:60])
    ok, msg = raises(ai_run.make_attribution, "p", None, "v1", "0")
    check(ok and "model" in msg, "缺 model → 拒绝", msg[:60])
    ok, msg = raises(ai_run.make_attribution, "p", "m", None, "0")
    check(ok and "version" in msg, "缺 version → 拒绝", msg[:60])
    ok, msg = raises(ai_run.make_attribution, "p", "m", "v1", None)
    check(ok and "temperature" in msg, "缺 temperature → 拒绝", msg[:60])
    ok, msg = raises(ai_run.make_attribution, "p", "  ", "v1", "0")
    check(ok and "model" in msg, "空字符串不算填了", msg[:60])
    ok, msg = raises(ai_run.make_attribution, "p", "m", "v1", "warm")
    check(ok and "temperature" in msg, "温度不是数 → 拒绝", msg[:60])

    att = ai_run.make_attribution("p", "m", "v1", "0")
    check(att["status"] == "recorded" and att["unknown_fields"] == [], "4 个都给了 → recorded")
    check(att["temperature"] == 0.0 and att["temperature"] is not None,
          "temperature=0 是**合法值**，不能当成缺", repr(att["temperature"]))
    att = ai_run.make_attribution("p", "m", "v1", "0.7")
    check(att["temperature"] == 0.7, "temperature=0.7 原样记录")

    ok, msg = raises(ai_run.make_attribution, "p", "unknown", "v1", "0")
    check(ok and "legacy-reason" in msg, "unknown 不带理由 → 拒绝（不是「跳过」）", msg[:80])
    att = ai_run.make_attribution("p", "unknown", "unknown", "unknown",
                                 "旧会话没记模型名")
    check(att["status"] == "unknown", "unknown + 理由 → 接受，但 status=unknown")
    check(att["unknown_fields"] == ["model", "version", "temperature"],
          "unknown_fields 精确到字段", str(att["unknown_fields"]))
    check(att["temperature"] is None, "unknown 的温度记成 null，不是 0")
    check(att["note"] == "旧会话没记模型名", "理由进了 note")


# ------------------------------------------------------------------ [2] freeze

def test_freeze(root):
    print("[2] freeze：清单、校验、改一个字节就指名报错")
    bench = make_bench(os.path.join(root, "f"))
    v = ai_run.verify_freeze(bench)
    check(not v["ok"] and v["freeze"] is None, "没冻结时 verify 不通过（不是「全绿」）")

    rc, _ = quiet(ai_run.cmd_freeze, SimpleNamespace(bench=bench, write=True))
    check(rc == 0, "freeze --write 写出清单")
    fr_path = os.path.join(bench, ai_run.FREEZE_NAME)
    check(os.path.isfile(fr_path), "清单文件存在")
    fr = ai_run.read_json(fr_path)
    files = ai_run.material_files(bench)
    check(fr["count"] == len(files), f"清单覆盖 {len(files)} 个文件（材料+题目+参考+打分器）")
    check("参考.eln" in " ".join(fr["files"]), "参考.eln 也在冻结范围内（它是自检基准）")
    check(ai_run.SCORER in fr["files"], "打分.py 也在冻结范围内（它是三栏口径）")
    check(fr["digest"] == ai_run.manifest_digest(fr["files"]), "digest 与 files 自洽")

    v = ai_run.verify_freeze(bench)
    check(v["ok"], "没改时 verify 通过")
    check(not v["changes"]["changed"] and not v["changes"]["added"]
          and not v["changes"]["missing"], "三张 diff 表都是空的")

    # 改一个字节
    p = os.path.join(bench, "材料", "语法速查.md")
    before = read(p)
    write(p, before + " ")
    v = ai_run.verify_freeze(bench)
    check(not v["ok"], "改一个字节 → verify 不通过")
    check(v["changes"]["changed"] == ["材料/语法速查.md"],
          "diff 指出**哪个文件**变了", str(v["changes"]["changed"]))
    msg = ai_run.render_freeze_problem(v)
    check("材料/语法速查.md" in msg, "报错文本里点了名")
    write(p, before)
    check(ai_run.verify_freeze(bench)["ok"], "改回原样 → 又通过（比对是逐字节的）")

    # 新增 / 删除
    extra = os.path.join(bench, "材料", "多出来的.md")
    write(extra, "x")
    check(ai_run.verify_freeze(bench)["changes"]["added"] == ["材料/多出来的.md"],
          "多一个文件 → added 里点名")
    os.remove(extra)
    ref = os.path.join(bench, "01_甲", "参考.eln")
    saved = read(ref)
    os.remove(ref)
    check(ai_run.verify_freeze(bench)["changes"]["missing"] == ["01_甲/参考.eln"],
          "少一个文件 → missing 里点名")
    write(ref, saved)

    # 清单自己被手改
    fr["digest"] = "sha256:" + "0" * 64
    ai_run.write_json(fr_path, fr)
    v = ai_run.verify_freeze(bench)
    check(not v["ok"] and v["tampered"], "清单被手改 → tampered（不是「通过」）")
    rc, err = quiet(ai_run.cmd_freeze, SimpleNamespace(bench=bench, write=False))
    check(rc == 1, "freeze 校验失败 → 退出码 1")

    # 重新冻结：旧 epoch 进 history，并记下 diff
    rc, out = quiet(ai_run.cmd_freeze, SimpleNamespace(bench=bench, write=True))
    fr = ai_run.read_json(fr_path)
    check(rc == 0 and len(fr["history"]) == 1, "重新冻结 → history 多一条（旧 epoch 不丢）")
    check(ai_run.verify_freeze(bench)["ok"], "重新冻结后校验通过")


# ------------------------------------------------------------------ [3] 隔离

def test_isolation(root):
    print("[3] prepare：隔离目录只有「材料 + 题目」，没有参考.eln")
    bench = make_bench(os.path.join(root, "i"))
    quiet(ai_run.cmd_freeze, SimpleNamespace(bench=bench, write=True))

    out = os.path.join(root, "i", "run1")
    rc, text = quiet(ai_run.cmd_prepare, prepare_args(bench, out=out))
    check(rc == 0, "prepare 成功")
    check(os.path.isdir(out), "运行目录建出来了")

    # 整个目录树里搜一遍
    names = []
    for d, _dirs, ns in os.walk(out):
        names += [os.path.join(d, n) for n in ns]
    check(not any(n.endswith("参考.eln") for n in names),
          "**没有 参考.eln**（这是整个工具存在的第一个理由）", str([os.path.basename(n) for n in names]))
    check(not any(os.path.basename(n) == "打分.py" for n in names),
          "没有 打分.py（评分器不出现在被测环境）")
    check(not any(os.path.basename(n).startswith(("结果-", "自检-")) for n in names),
          "没有历史结果文件")
    check(not os.path.isfile(os.path.join(out, "材料", ai_run.PROMPT_NAME)),
          "材料/给AI的提示词.md 没拷（材料里自己写明「不发给 AI」）")
    check(not os.path.isdir(os.path.join(out, "提交-实例9")),
          "历史提交（答案）没拷")
    check(os.path.isdir(os.path.join(out, "提交")) and
          not os.listdir(os.path.join(out, "提交")), "提交/ 存在且是空的")

    # 该有的一样不少
    check(len([n for n in names if n.endswith("cases.json")]) == len(PROBLEMS),
          "2 份 cases.json 都在（按冻结的提示词：给 AI 自查）")
    check(len([n for n in names if n.endswith("spec.tu")]) == len(PROBLEMS),
          "2 份 spec.tu 都在（契约必须给）")
    check(len([n for n in names if n.endswith("题目.md")]) == len(PROBLEMS),
          "2 份 题目.md 都在")
    check(len([n for n in names if os.sep + "材料" + os.sep in n]) == 6,
          "材料/ 6 个文件（提示词除外）")
    meta = ai_run.read_json(os.path.join(out, ai_run.META_NAME))
    check(meta["schema"] == ai_run.SCHEMA_META, "元数据 schema 对")
    for k in ("run_id", "created_at", "attribution", "clean", "protocol",
              "bench", "materials", "excluded", "tool"):
        check(k in meta, f"元数据有 {k}")
    check(meta["attribution"]["temperature"] == 0.0, "prepare 记下了 temperature")
    check(meta["clean"]["isolation"] == "prepared", "标了 isolation=prepared")
    check(len(meta["materials"]) == 6 + len(PROBLEMS) * 3,
          "materials 表 = 拷进去的每个文件（含 sha256）")
    check(all(h.startswith("sha256:") for h in meta["materials"].values()),
          "每个文件都有 sha256")
    fr = ai_run.read_json(os.path.join(bench, ai_run.FREEZE_NAME))
    check(all(fr["files"][k] == h for k, h in meta["materials"].items()),
          "拷进去的字节与冻结值逐个相同")
    check(meta["protocol"]["problems"] == len(PROBLEMS), "协议记下题目数")
    check(meta["protocol"]["cases_included"] is True, "默认 cases_included=true")
    check(meta["bench"]["freeze_epoch"] == fr["epoch"], "记下冻结 epoch")

    # 隔离自检本身会抓出放进来的答案
    leakdir = os.path.join(out, "01_甲")
    write(os.path.join(leakdir, "参考.eln"), "u64 alpha() { return 0; }\n")
    leaks = ai_run.scan_isolation(out)
    check("01_甲/参考.eln" in leaks, "scan_isolation 抓得到混进来的参考.eln")
    os.remove(os.path.join(leakdir, "参考.eln"))
    check(ai_run.scan_isolation(out) == [], "拿掉后干净")

    # 不覆盖已有运行
    ok, msg = raises(ai_run.cmd_prepare, prepare_args(bench, out=out))
    check(ok and "非空" in msg, "输出目录非空 → 拒绝（不覆盖已有运行）", msg[:60])

    # --no-cases：协议变了要**记下来**
    out2 = os.path.join(root, "i", "run2")
    rc, _ = quiet(ai_run.cmd_prepare, prepare_args(bench, out=out2, no_cases=True))
    meta2 = ai_run.read_json(os.path.join(out2, ai_run.META_NAME))
    check(rc == 0 and meta2["protocol"]["cases_included"] is False,
          "--no-cases → cases_included=false（与历史不可比这件事被记下来了）")
    check(not os.path.isfile(os.path.join(out2, "01_甲", "cases.json")),
          "--no-cases 真的没拷 cases.json")
    check(any("--no-cases" in e["why"] for e in meta2["excluded"]),
          "excluded 里写明了为什么没拷")

    # 元数据缺失时连目录都不该建
    out3 = os.path.join(root, "i", "run3")
    ok, _ = raises(ai_run.cmd_prepare, prepare_args(bench, out=out3, model=None))
    check(ok and not os.path.exists(out3), "元数据缺失 → 拒绝，且不留下半个目录")

    # 材料没冻结时不许 prepare（尺子都没定就开跑）
    bench2 = make_bench(os.path.join(root, "i2"))
    out4 = os.path.join(root, "i2", "r")
    rc, text = quiet(ai_run.cmd_prepare, prepare_args(bench2, out=out4))
    check(rc == 2 and "冻结" in text, "没冻结 → prepare 拒绝（退出码 2）", f"rc={rc}")
    check(not os.path.exists(out4), "拒绝时不留半个运行目录")


# ------------------------------------------------------------------ [4] 计数

def _row(check_v="通过", sem="3/3", kappa="达成"):
    return {"prefix": "01", "name": "甲", "fn": "alpha", "file": "x.eln",
            "check": check_v, "sem": sem, "kappa": kappa, "codes": {}, "detail": []}


def test_summary():
    print("[4] 汇总：未交永远不算通过；KPI 计入要求「齐 + 干净 + 12 题」")
    s = ai_run.summarize([_row(), _row(check_v="未交", sem="—", kappa="—")])
    check((s["total"], s["submitted"], s["not_submitted"]) == (2, 1, 1),
          "未交单独计数", str(s))
    check(s["full_pass"] == 1 and s["pass_rate"] == 0.5,
          "未交不进 full_pass（1/2，不是 2/2）", str(s))
    check(s["check"] == 1 and s["kappa"] == 1, "check / κ 也不把未交算成通过")

    s = ai_run.summarize([_row(sem="2/3")])
    check(s["full_pass"] == 0, "语义没全对 → 不算通过")
    s = ai_run.summarize([_row(kappa="债务")])
    check(s["full_pass"] == 0, "κ 债务 → 不算通过")
    s = ai_run.summarize([_row(check_v="失败")])
    check(s["full_pass"] == 0, "check 失败 → 不算通过")
    s = ai_run.summarize([_row(sem="0/0")])
    check(s["full_pass"] == 0, "0 条用例不算「通过」（空证据）")

    att = ai_run.make_attribution("p", "m", "v1", "0")
    b = ai_run.kpi_blockers(att, "none", {"total": 12})
    check(b == [], "齐 + 干净 + 12 题 → 可计入", str(b))
    b = ai_run.kpi_blockers(att, "author-read-reference", {"total": 12})
    check(len(b) == 1 and "不干净" in b[0], "不干净 → 不可计入", str(b))
    b = ai_run.kpi_blockers(att, "none", {"total": 4})
    check(len(b) == 1 and "12" in b[0], "题目数不是 12 → 不可计入", str(b))
    att_u = ai_run.make_attribution("p", "unknown", "v1", "0", "没记")
    b = ai_run.kpi_blockers(att_u, "none", {"total": 12})
    check(len(b) == 1 and "元数据" in b[0], "元数据 unknown → 不可计入", str(b))

    # 一题都没交 ≠ 通过率 0%：那是「没跑」，默认拒绝记录
    empty = ai_run.summarize([_row(check_v="未交", sem="—", kappa="—") for _ in range(12)])
    ok, msg = raises(ai_run.check_measured, empty)
    check(ok and "没跑" in msg, "12 题全未交 → 拒绝（不是 0 分数据点）", msg[:60])
    check(not raises(ai_run.check_measured, empty, allow_empty=True)[0],
          "--allow-empty 才允许记一次 0 分")
    part = ai_run.summarize([_row(), _row(check_v="未交", sem="—", kappa="—")])
    check(not raises(ai_run.check_measured, part)[0], "交了 1 题就不拦（那是真实数据）")


# ------------------------------------------------------------------ [5] 记录/账本

def test_record(root):
    print("[5] 记录 + 账本：JSON 结构、追加、读回、无 BOM、list 判定")
    att = ai_run.make_attribution("deepseek", "m", "v1", "0")
    rows = [_row() for _ in range(12)]
    s = ai_run.summarize(rows)
    rec = ai_run.build_record(
        att, "none", "prepared", s, rows,
        run_id="r1", bench="/bench",
        material={"freeze_epoch": "e", "digest": "sha256:x", "count": 1, "verified": True},
        protocol={"problems": 12, "cases_included": True},
        sub="/sub", sub_files={"a.eln": "sha256:y"},
        elapsed_s=1.5, returncode=0)

    for k in ("schema", "recorded_at", "run_id", "attribution", "clean", "material",
              "protocol", "submission", "tool", "summary", "kpi_countable",
              "kpi_blockers", "rows"):
        check(k in rec, f"记录有 {k}")
    check(rec["schema"] == ai_run.SCHEMA_RECORD, "schema 对")
    check(rec["kpi_countable"] is True and rec["kpi_blockers"] == [],
          "12 题 + 齐 + 干净 → kpi_countable=true")
    check(rec["rows"] is rows, "rows 是打分.py 的 rows 原样搬进来（没有重算）")
    check(rec["summary"]["full_pass"] == 12, "12/12")
    check(rec["tool"]["scorer"] == ai_run.SCORER, "记录里写清了三栏来自哪个打分器")

    # 落盘：UTF-8 无 BOM、\n 换行
    jp = os.path.join(root, "rec.json")
    ai_run.write_json(jp, rec)
    raw = open(jp, "rb").read()
    check(not raw.startswith(b"\xef\xbb\xbf"), "JSON 是 UTF-8 无 BOM")
    check(b"\r" not in raw, "换行是 \\n（Windows 上也不能变 \\r\\n）")
    back = json.loads(raw.decode("utf-8"))
    check(back["run_id"] == "r1", "读回来还是那份记录")

    # 账本：一行一条，追加
    ledger = os.path.join(root, "ledger.jsonl")
    ai_run.append_record(ledger, rec)
    ai_run.append_record(ledger, {**rec, "run_id": "r2"})
    recs = ai_run.load_records(ledger)
    check(len(recs) == 2, "账本追加两行、读回两行")
    check(recs[0]["run_id"] == "r1" and recs[1]["run_id"] == "r2", "顺序就是追加顺序")
    check(b"\r" not in open(ledger, "rb").read(), "账本也是 \\n 换行")
    check(ai_run.load_records(os.path.join(root, "nope.jsonl")) == [],
          "账本不存在 → 空列表（不崩）")

    # list 的判定：只有一个不可计入的运行 → 未达成
    bench = make_bench(os.path.join(root, "l"))
    ai_run.append_record(os.path.join(bench, ai_run.RESULTS_NAME),
                         {**rec, "kpi_countable": False,
                          "kpi_blockers": ["元数据未记录（model）"]})
    rc, out = quiet(ai_run.cmd_list, SimpleNamespace(bench=bench, ledger=None,
                                                    limit=0, json=False))
    check(rc == 0 and "KPI③" in out, "list 打印 KPI③ 判定")
    check("未达成" in out and "还差 3 个模型" in out, "没有可计入的运行 → 未达成", out[-200:])

    # 三个模型各一次干净的 12 题 → 达成
    for i, model in enumerate(("m1", "m2", "m3")):
        a = ai_run.make_attribution("p", model, "v1", "0")
        r = ai_run.build_record(a, "none", "prepared", s, rows, run_id=f"ok{i}",
                                bench=bench, material={"digest": "sha256:x"},
                                protocol={"problems": 12}, sub="/s", sub_files={},
                                elapsed_s=0.1, returncode=0)
        check(r["kpi_countable"], f"模型 {model} 的 12 题干净运行可计入")
        ai_run.append_record(os.path.join(bench, ai_run.RESULTS_NAME), r)
    rc, out = quiet(ai_run.cmd_list, SimpleNamespace(bench=bench, ledger=None,
                                                    limit=0, json=False))
    check("达成" in out and "3 个模型" in out, "3 个模型 × 12 题 → 达成", out[-260:])

    rc, out = quiet(ai_run.cmd_list, SimpleNamespace(bench=bench, ledger=None,
                                                    limit=0, json=True))
    check(rc == 0 and json.loads(out)[0]["run_id"] == "r1", "list --json 输出原始账本")


# ------------------------------------------------------------------ [6] 运行目录完整性

def test_run_dir_integrity(root):
    print("[6] 运行目录被改过 → 拒绝打分（改契约迁就实现，得拦住）")
    bench = make_bench(os.path.join(root, "d"))
    quiet(ai_run.cmd_freeze, SimpleNamespace(bench=bench, write=True))
    out = os.path.join(root, "d", "run")
    quiet(ai_run.cmd_prepare, prepare_args(bench, out=out))
    meta = ai_run.load_run_meta(out)
    check(not raises(ai_run.verify_run_dir, out, meta)[0], "没改 → 通过")

    spec = os.path.join(out, "01_甲", "spec.tu")
    before = read(spec)
    write(spec, before.replace("O(xs.size())", "O(xs.size() * xs.size())"))
    ok, msg = raises(ai_run.verify_run_dir, out, meta)
    check(ok and "01_甲/spec.tu" in msg, "spec.tu 被改 → 点名拒绝", msg[:80])
    write(spec, before)
    check(not raises(ai_run.verify_run_dir, out, meta)[0], "改回 → 通过")

    ok, msg = raises(ai_run.load_run_meta, os.path.join(bench, "材料"))
    check(ok and "运行目录" in msg, "不是 prepare 出来的目录 → 拒绝", msg[:60])


# ------------------------------------------------------------------ [7] CLI

def run_cli(argv):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tools", "ai_run.py")] + argv,
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def test_cli(root):
    print("[7] CLI：真进程上，缺元数据 → 退出码 2、材料变了 → 退出码 1")
    bench = make_bench(os.path.join(root, "c"))
    quiet(ai_run.cmd_freeze, SimpleNamespace(bench=bench, write=True))
    sub = os.path.join(bench, "提交-实例9")

    rc, out = run_cli(["--bench", bench, "score", "--sub", sub,
                       "--provider", "p", "--version", "v1", "--temperature", "0"])
    check(rc == 2, "缺 --model → 退出码 2", f"rc={rc}")
    check("元数据缺失" in out and "model" in out, "提示里点名缺 model", out[:120])

    rc, out = run_cli(["--bench", bench, "score", "--sub", sub,
                       "--provider", "p", "--model", "unknown",
                       "--version", "v1", "--temperature", "0"])
    check(rc == 2 and "legacy-reason" in out, "unknown 不带理由 → 退出码 2", out[:120])

    p = os.path.join(bench, "材料", "语法速查.md")
    before = read(p)
    write(p, before + "\n多一行\n")
    rc, out = run_cli(["--bench", bench, "freeze"])
    check(rc == 1, "材料变了 → freeze 退出码 1", f"rc={rc}")
    check("材料/语法速查.md" in out, "输出点了名", out[:200])
    rc, out = run_cli(["--bench", bench, "score", "--sub", sub, "--provider", "p",
                       "--model", "m", "--version", "v1", "--temperature", "0"])
    check(rc == 2 and "材料" in out, "材料变了 → score 也拒绝（尺子变了不打分）", out[:160])
    write(p, before)


# ------------------------------------------------------------------ main

def main() -> int:
    root = tempfile.mkdtemp(prefix="ai-run-test-")
    try:
        test_metadata()
        test_freeze(root)
        test_isolation(root)
        test_summary()
        test_record(root)
        test_run_dir_integrity(root)
        test_cli(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
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
