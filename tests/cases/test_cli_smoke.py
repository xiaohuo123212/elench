#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_cli_smoke.py —— **每个子命令都得有反应**。

## 为什么需要这条

2026-11-02 出过一次：`cmd_tightness` 被插进了 `_main_impl` 中间，父函数在那里就
结束了，后面 12 个 `if cmd == "...":` 全变成死代码。结果是：

- `py_compile` **通过**；
- 单元测试**全绿**；
- 但 `elench cost` / `build` / `export` … 全部**静默什么都不做**（rc=0、无输出）。

"没有输出"是这类 bug 唯一的共同特征。所以这条测试只认三件事：
**不崩（无 traceback）、有反应（有输出）、退出码合理。**
"""
import os as _os
import sys as _sys

CASEDIR = _os.path.dirname(_os.path.abspath(__file__))
ROOT = _os.path.abspath(_os.path.join(CASEDIR, "..", ".."))
SRC = _os.path.join(ROOT, "src")
for _p in (SRC,):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import io
import os
import re
import subprocess
import sys
import tempfile

_passed = 0
_failed = []

#: 所有子命令（跟 `_main_impl` 的分支链保持一致；新增命令要加进来）
COMMANDS = ["run", "check", "fmt", "highlight", "show", "parse", "cost",
            "dispatch", "measure", "stats", "header", "grammar", "cpp",
            "build", "export", "bench"]


def check(ok, label, detail=""):
    global _passed
    if ok:
        _passed += 1
        print(f"  OK   {label}")
    else:
        _failed.append(label)
        print(f"  FAIL {label}   {detail}")


def run(args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, os.path.join(SRC, "elench.py")] + args,
                       capture_output=True, encoding="utf-8", errors="replace",
                       env=env, timeout=180)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def test_every_command_reacts():
    print("[1] 每个子命令无参调用：不崩 + 有输出 + 退出码合理")
    for cmd in COMMANDS:
        rc, out, err = run([cmd])
        txt = out + err
        check("Traceback" not in txt, f"{cmd}：没有 traceback", txt[-120:])
        check(bool(txt.strip()), f"{cmd}：有输出（不是静默空转）", f"rc={rc}")
        check(rc in (0, 1, 2), f"{cmd}：退出码合理（{rc}）", str(rc))


def test_real_invocations():
    print("[2] 真实调用：export --full / cost 必须出内容")
    # **用公开例子，别用内部样例**：`samples/sum` 这种路径以前能过，
    # 是因为那条命令会把参数砍成 basename 再去内部样例目录里找（那是 bug）。
    # 修好之后它按给定路径找，假路径就会如实报错——所以这里改用真文件。
    ex = os.path.join(ROOT, "文档", "入门", "例子", "pick")
    rc, out, err = run(["export", ex, "--full", "--sizes=16", "--runs=2"])
    check(rc == 0, "export --full 退出 0", f"rc={rc} {err[-100:]}")
    check("紧度" in (out + err), "export --full 输出第三节「紧度」", (out + err)[:80])

    rc, out, err = run(["cost", ex])
    check("κ" in (out + err) or "=" in (out + err), "cost 有实质输出",
          (out + err)[:80])


#: 报告体积预算（与 `html_report.SIZE_BUDGET_KB` 同一个数；这里写死是为了
#: 让"预算"是外部约定，而不是测试跟着实现改）。
SIZE_BUDGET_KB = 200


def _nasty_payload():
    """合成一份「所有字段都很脏」的 payload：转义必须挡住 `< & > " '`。"""
    nasty = 'n(a<b) & "q" \'x\' > y'
    return {
        "module": 'm<&>"',
        "tool_version": "elench 1.0.0",
        "generated_at": "2026-01-01T00:00:00+08:00",
        "sizes": [4, 8],
        "sizes_src": "test",
        "runs": 1,
        "summary": {"declared": 1, "ok": 0, "debt": 0, "unknown": 1,
                    "undeclared": 0, "points": 0},
        "cards": [{
            "fn": "f<&>",
            "target_expr": nasty,
            "target_order": "",
            "kappa_expr": nasty,
            "kappa_order": "",
            "verdict": "unknown",
            "verdict_text": "无法判定：<诊断> & 消息",
            "points": [],
            "measured": False,
            "svg": None,
            "chart_note": "未测量 <np>",
            "has_target": True,
        }],
        "undeclared": [],
        "measured": {"at": "未测量", "source": "未测量"},
    }


def test_html_report():
    """`export --full --out=*.html`：静态归档页的三条硬规则 + 自包含 + 确定性。

    **为什么这些断言值钱**：这份产物是给人看、会贴进 PR 的归档件。它一旦把
    「证明（κ）」和「符合（实测）」混成一个「通过」，或者从 CDN 拉字体
    （本机没有外网 → 打开是白板），读的人不会发现，只会被误导。
    """
    import html_report

    print("[3] export --full --out=报告.html：自包含 / 确定性 / 转义 / 点数不足")
    ex = os.path.join(ROOT, "文档", "样例", "count")
    tmp = tempfile.mkdtemp(prefix="elench_html_")
    a = os.path.join(tmp, "a.html")
    b = os.path.join(tmp, "b.html")
    args = ["export", ex, "--full", "--sizes=16,64,256", "--runs=2",
            "--no-timestamp"]
    rc, _out, err = run(args + ["--out=" + a])
    check(rc == 0, "退出 0", f"rc={rc} {err[-140:]}")
    check(os.path.exists(a), "报告文件存在", a)
    rc2, _o2, _e2 = run(args + ["--out=" + b])
    check(rc2 == 0, "第二次同样退出 0", f"rc={rc2}")

    html = io.open(a, encoding="utf-8").read() if os.path.exists(a) else ""
    kb = len(html.encode("utf-8")) / 1024.0
    check(2 <= kb <= SIZE_BUDGET_KB, f"体积合理（{kb:.1f} KB）", f"{kb:.1f} KB")
    check(bool(html) and html == io.open(b, encoding="utf-8").read(),
          "同一输入两次生成逐字节相同（--no-timestamp）")

    check(not re.search(r"https?://", html), "没有 http(s) 外链")
    check("//cdn" not in html, "没有 CDN 引用")
    check("xmlns" not in html, "内联 SVG 不带 xmlns（否则那本身就是一条 http 链接）")
    check("<svg" in html, "含内联 SVG")
    check('class="stamp s1"' in html and 'class="stamp s2"' in html,
          "两个时间戳（κ/判决 + 实测）都在")
    check("（未记录：--no-timestamp）" in html, "--no-timestamp 写成固定标记")
    for word in ("证明（κ）", "符合（实测）", "不作趋势判断"):
        check(word in html, f"含「{word}」", html[:60])
    check("未测量" not in html,
          "有实测时**不写**「未测量」（该有的数字不缺，也不乱标）")
    check("κ/ops" in html and "O(" in html, "含 κ/ops 与阶的摘要")

    # ---- 规则二：实测点 < 4 只画点（一条 polyline = 只有 κ 线）
    check(html.count("<polyline") == 1,
          "3 个实测点时只有 κ 一条线（实测只有点）",
          str(html.count("<polyline")))
    k3 = [{"n": 16, "kappa": 1000.0, "ops": 900.0},
          {"n": 64, "kappa": 4000.0, "ops": 3600.0},
          {"n": 256, "kappa": 16000.0, "ops": 14000.0}]
    k4 = k3 + [{"n": 1024, "kappa": 64000.0, "ops": 56000.0}]
    svg3, note3 = html_report._chart_svg("f", k3, k3)
    svg4, note4 = html_report._chart_svg("f", k4, k4)
    check(svg3.count("<polyline") == 1, "3 点：实测不连线",
          str(svg3.count("<polyline")))
    check("不作趋势判断" in (note3 or ""), "3 点：给出「点数不足」的说明")
    check(svg4.count("<polyline") == 2, "4 点：实测才连线",
          str(svg4.count("<polyline")))
    check(note4 is None, "4 点：不再说点数不足", str(note4))
    _svg0, note0 = html_report._chart_svg("f", k3, [])
    check("未测量" in (note0 or ""), "没有实测时明说「未测量」")
    check("未测量" in html_report.render(_nasty_payload()),
          "整页层面也会写「未测量」（不留空让人误读）")

    # ---- 转义：κ 表达式 / 函数名 / 诊断消息里的 < & > 必须变成实体
    calls = html_report.render(_nasty_payload())
    check("&lt;诊断&gt;" in calls and "&amp; 消息" in calls,
          "诊断消息里的 < > & 被转义")
    check("<code>f<&>" not in calls, "函数名没有被裸插进标签")
    check('n(a<b) & "q"' not in calls, "κ 表达式没有被裸插")
    check("&lt;np&gt;" in calls, "卡片说明里的 < > 被转义")

    # ---- ① κ 分段折叠：主体**逐字符相同**才折叠；原文留在 <details>（可复制）
    from symbolic import Cost, PIECE_BIG
    check("分段：" in html and "主体（三段相同）：" in html,
          "count 的 κ 折成「分段 / 主体」两行")
    check("12 + 1 + 1 + 9 + n(text)×23 + 23 + 2 + 2 + 4626 + 2" in html,
          "折叠后主体仍逐字可读（原文片段在页面里）")
    check("原始 κ 表达式（可复制）" in html,
          "原始 κ 表达式仍在 <details> 里（能选中复制）")
    check("n(text)&lt;1: 12 + 1 + 1 + 9" in html,
          "原文整段仍在文件里（HTML 转义后；复制出来是原字符）")
    body = Cost.add(Cost.mul(Cost.num(3), Cost.size("x")), Cost.num(7))
    other = Cost.add(body, Cost.num(1))
    same = Cost.piece("x", [(-PIECE_BIG, 0, body), (1, 4096, body),
                            (4097, PIECE_BIG, body)], body)
    diff = Cost.piece("x", [(-PIECE_BIG, 0, body), (1, 4096, other),
                            (4097, PIECE_BIG, body)], body)
    part = Cost.piece("x", [(8, 2715, body), (2716, 65536, body)], body)
    check(html_report._fold_kappa(same) is not None,
          "三段主体逐字符相同 → 折叠")
    check(html_report._fold_kappa(diff) is None,
          "任一段不同 → 不折叠（宁可长，不把不同说成相同）")
    check(html_report._fold_kappa(part) is None,
          "分段没覆盖全（还有越界回退）→ 不折叠")
    check(html_report._fold_kappa(Cost.size("x")) is None, "不是分段 → 不折叠")

    # ---- ② 比值必须有方向：0.00 不是"算错"，是 κ 远低于 target
    lvl, txt = html_report._ratio_line(2.09e-11, "1/n", "ok")
    check(lvl == html_report.RATIO_FAR and "远低于" in txt and "好消息" in txt,
          "比值 < 0.01 → 说方向 + 注明「好」", txt)
    lvl, txt = html_report._ratio_line(0.35, "", "ok")
    check(lvl == html_report.RATIO_IN and "0.35" in txt,
          "比值 0.01–1 → 正常给数值", txt)
    lvl, txt = html_report._ratio_line(2.3, "", "debt")
    check(lvl == html_report.RATIO_OVER and "超过 target" in txt,
          "比值 > 1 → 明写「κ 超过 target」", txt)
    check('class="ratio over"' in html_report._ratio_html(
        {"ratio_level": html_report.RATIO_OVER, "ratio_note": "κ 超过 target"}),
        "比值 > 1 用独立红块（不是行内小字）")
    check("κ 远低于 target（约 1/n）" in html and "这是好消息" in html,
          "count 的比值那句话带方向（不是干巴巴的 0.00）")
    check("比值 ≈ 0.00" not in html,
          "不再出现没有方向的「比值 ≈ 0.00」")

    # ---- `--now=`：原样写进两个时间戳（可复现的另一个出口）
    c = os.path.join(tmp, "c.html")
    rc3, _o3, _e3 = run(["export", ex, "--full", "--sizes=16", "--runs=1",
                         "--now=2026-01-02T03:04:05+08:00", "--out=" + c])
    check(rc3 == 0, "--now= 也退出 0", f"rc={rc3}")
    txt = io.open(c, encoding="utf-8").read() if os.path.exists(c) else ""
    check(txt.count("2026-01-02T03:04:05+08:00") >= 2, "两个时间戳都用了 --now= 的值")


def main() -> int:
    test_every_command_reacts()
    test_real_invocations()
    test_html_report()
    print()
    if _failed:
        print(f"失败 {len(_failed)} / {_passed + len(_failed)}")
        for f in _failed[:10]:
            print("  -", f)
        return 1
    print(f"全部通过：{_passed} 项")
    return 0


if __name__ == "__main__":
    sys.exit(main())
