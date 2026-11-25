#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_trufar_component.py —— 平台「自定义代码」组件的四段代码必须能贴、能跑、不跑偏。

## 为什么要有这条测试

`tools/trufar_component/` 里的四段代码是**别人家的页面**上跑的：
贴上去之后如果出错，我们这边看不到任何信号 —— 门禁全绿、命令行正常，
只有平台上那个组件是坏的。所以这里钉三类东西：

1. **能贴**：四段都在、都是非空、HTML 是完整片段、无外链（内嵌场景常没有网络）。
2. **不跑偏**：`01-html.html` 里出现的 `data-role` 必须在 `02-onMount.js` 里被取到，
   否则渲染写进空气（这是最容易发生、最看不出来的一种坏）。
3. **真能跑**：装了 node 就用 `selftest.js` 的 DOM 桩跑一遍完整生命周期
   （无数据 → 违约 → 达成 → 工具失败 → 结果消失 → 卸载），16+ 条断言。
   没装 node 就**明确说跳过**，不假装通过。
"""
import json
import os
import re
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
DIR = os.path.join(ROOT, "tools", "trufar_component")

PARTS = ["01-html.html", "02-onMount.js", "03-onUpdate.js", "04-onUnmount.js"]


def _read(name):
    p = os.path.join(DIR, name)
    with open(p, encoding="utf-8") as f:
        return f.read()


def main() -> int:
    fails = []

    def check(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    # ---------- 1. 能贴 ----------
    missing = [p for p in PARTS if not os.path.isfile(os.path.join(DIR, p))]
    check(not missing, "四段代码都在：%s" % (missing or "齐全"))

    texts = {}
    for p in PARTS:
        if os.path.isfile(os.path.join(DIR, p)):
            texts[p] = _read(p)
            check(len(texts[p].strip()) > 50, "%s 不是空文件（%d 字节）" % (p, len(texts[p])))

    html = texts.get("01-html.html", "")
    check("<div" in html and "</div>" in html, "01-html.html 是合法的 HTML 片段（有开合标签）")
    check("<style>" in html, "01-html.html 带内联样式（不引外部 CSS）")

    for p, t in texts.items():
        ext = re.findall(r"https?://[^\s\"'()<>]+", t)
        ext = [u for u in ext if "127.0.0.1" not in u and "localhost" not in u]
        check(not ext, "%s 里没有外链资源（%s）" % (p, ext[:2] or "无"))

    # ---------- 2. 不跑偏：HTML 的 data-role 必须被 JS 取到 ----------
    roles = set(re.findall(r'data-role="([^"]+)"', html))
    mount = texts.get("02-onMount.js", "")
    if roles:
        # onMount 里是 q("badge") 这种拼串取法，所以按"取到了哪些 role"来比。
        got = set(re.findall(r'q\("([^"]+)"\)', mount))
        check(roles <= got, "HTML 里的 data-role %s 都在 onMount 里被取到" % sorted(roles))
        check(bool(got), "onMount 确实缓存了 DOM 引用（%d 个）" % len(got))

    check("dom.__elench" in mount, "onMount 把渲染器挂在 dom 上（onUpdate 依赖它）")
    check("ec-worse" in mount or "worse" in mount, "有「变差」的动画触发点")
    check("prevData" in texts.get("03-onUpdate.js", ""), "onUpdate 用 prevData 做对比")
    check("removeEventListener" in texts.get("04-onUnmount.js", "") or
          "无定时器" in texts.get("04-onUnmount.js", ""),
          "onUnmount 要么摘监听器、要么说清为什么不用清理")

    # ---------- 3. 真能跑（需要 node） ----------
    node = shutil.which("node")
    if not node:
        print("  SKIP 没装 node：跳过 selftest.js 的功能验证（静态检查已过）。")
        print("        要跑功能验证：装 node 后执行 node tools/trufar_component/selftest.js")
    else:
        r = subprocess.run([node, os.path.join(DIR, "selftest.js")], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        out = (r.stdout or "") + (r.stderr or "")
        oks = out.count("  ok   ")
        bad = [ln.strip() for ln in out.split("\n") if "FAIL" in ln]
        check(r.returncode == 0 and not bad,
              "selftest.js 全过（%d 条断言）%s" % (oks, ("；失败：" + bad[0][:80]) if bad else ""))

    if fails:
        print("")
        print("%d 项失败" % len(fails))
        return 1
    print("")
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
