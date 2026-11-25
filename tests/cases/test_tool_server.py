#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_tool_server.py —— `tools/elench_tool.py` 的三种接法都要能用。

## 为什么要有这条测试

这个文件是**唯一一个会被别人家平台调用**的入口：智能体平台（初发 Trufar 这类）
会往它发 HTTP，或者把它塞进沙箱用 stdin/stdout 说话。它坏了不会让本仓库任何
门禁变红 —— 只会在别处静默失败。所以这里把三种接法各钉一遍：

1. **stdio**：一次给多个请求，逐行回 JSON；包含"合格 / 违约 / 语法错 / 拒绝执行类 op"。
2. **HTTP**：`--port 0` 拿系统分配的端口，`GET /health`、`POST /cost`、未知路径 404。
3. **认证**：设了 `ELENCH_TOOL_TOKEN` 就必须带 `Authorization`，没带 401、带了 200。

同时钉住两条语义（很容易被后来的人改坏）：

- `ok` 只说"工具跑完了"，**不**代表代码合格；合格与否看 `verdict`。
- `run` / `stats` / `measure` **故意不开放**（它们会执行用户代码）。
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
TOOL = os.path.join(ROOT, "tools", "elench_tool.py")

ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

TU = """spec f(const vector<u64>& xs) -> u64 {
    effects {};
    target { time: { max: O(n) }, allocation: { max: 0 }, };
}
"""
ELN_OK = """u64 f(const vector<u64>& xs) {
    u64 s = 0;
    for (auto x : xs) { s = s + x; }
    return s;
}
"""
ELN_DEBT = """u64 f(const vector<u64>& xs) {
    u64 s = 0;
    for (auto x : xs) { for (auto y : xs) { s = s + y; } }
    return s;
}
"""
ELN_SYNTAX = "u64 f( {\n    return\n}\n"


def _stdio(reqs):
    inp = "\n".join(json.dumps(r, ensure_ascii=False) for r in reqs) + "\n"
    r = subprocess.run([sys.executable, "-B", TOOL, "--stdio"], input=inp,
                       capture_output=True, text=True, encoding="utf-8", env=ENV, timeout=300)
    lines = [ln for ln in (r.stdout or "").split("\n") if ln.strip()]
    return r, [json.loads(ln) for ln in lines]


def _http(env, use_auth):
    """起一个服务，返回 (proc, base_url)。端口用 0 让系统分配。"""
    proc = subprocess.Popen([sys.executable, "-B", TOOL, "--http", "--port", "0"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", env=env, bufsize=1)
    url = None
    deadline = time.time() + 30
    while time.time() < deadline:
        line = proc.stderr.readline()
        if not line:
            break
        if "http://" in line and "已启动" in line:
            url = line.split("http://", 1)[1].split()[0].strip().rstrip(",")
            url = "http://" + url
            break
    if not url:
        proc.kill()
        raise RuntimeError("服务没起来（等不到启动行）")
    return proc, url


def _post(url, obj, token=None):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, None


def main() -> int:
    fails = []

    def check(cond, msg):
        if cond:
            print("  ok   " + msg)
        else:
            print("  FAIL " + msg)
            fails.append(msg)

    # ---------- 1. stdio ----------
    reqs = [
        {"op": "cost", "tu": TU, "eln": ELN_OK},
        {"op": "cost", "tu": TU, "eln": ELN_DEBT},
        {"op": "check", "tu": TU, "eln": ELN_SYNTAX},
        {"op": "run", "tu": TU, "eln": ELN_OK},
        {"op": "cost", "tu": "", "eln": ""},
    ]
    r, outs = _stdio(reqs)
    check(r.returncode == 0, "stdio 进程正常退出")
    check(len(outs) == len(reqs), "stdio 一问一答：%d 个请求 → %d 行响应" % (len(reqs), len(outs)))
    if len(outs) == len(reqs):
        ok_case, debt_case, syn_case, run_case, empty_case = outs
        check(ok_case.get("verdict") == "ok" and ok_case.get("functions", {}).get("f", {}).get("verdict") == "ok",
              "合规代码：verdict=ok")
        check(debt_case.get("ok") is True and debt_case.get("verdict") == "debt",
              "违约代码：ok=true（工具跑了）但 verdict=debt（代码不达标）")
        check("n(xs)×(2 + n(xs)×7)" in (debt_case.get("functions", {}).get("f", {}).get("kappa") or ""),
              "违约代码的 κ 确实是 n² 的形状")
        check(syn_case.get("ok") is False and any(d.get("code", "").startswith("E-Parse")
                                                  for d in syn_case.get("diagnostics") or []),
              "语法错：ok=false 且带 E-Parse-* 诊断")
        check(run_case.get("ok") is False and "不支持的 op" in (run_case.get("error") or ""),
              "拒绝执行类 op（run）——不给别人当免费代码沙箱")
        check(empty_case.get("ok") is False, "空请求被拒")

    # ---------- 2. HTTP ----------
    proc, base = _http(ENV, use_auth=False)
    try:
        with urllib.request.urlopen(base + "/health", timeout=30) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        check(health.get("ok") is True, "GET /health 正常")
        check(health.get("auth") == "none", "没设 token 时如实说 auth=none（不假装有认证）")

        st, body = _post(base + "/cost", {"tu": TU, "eln": ELN_DEBT})
        check(st == 200 and body.get("verdict") == "debt", "POST /cost 返回 verdict=debt")

        st, _ = _post(base + "/run", {"eln": ELN_OK})
        check(st == 404, "POST /run 是 404（只开放静态的 check / cost）")
    finally:
        proc.kill()
        proc.wait(timeout=30)

    # ---------- 3. 认证 ----------
    env2 = dict(ENV, ELENCH_TOOL_TOKEN="s3cret")
    proc, base = _http(env2, use_auth=True)
    try:
        with urllib.request.urlopen(base + "/health", timeout=30) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        check(health.get("auth") == "bearer", "设了 token 时 health 报 auth=bearer")
        st, _ = _post(base + "/cost", {"tu": TU, "eln": ELN_OK})
        check(st == 401, "不带 Authorization → 401")
        st, body = _post(base + "/cost", {"tu": TU, "eln": ELN_OK}, token="wrong")
        check(st == 401, "token 不对 → 401")
        st, body = _post(base + "/cost", {"tu": TU, "eln": ELN_OK}, token="s3cret")
        check(st == 200 and body.get("verdict") == "ok", "token 正确 → 200 且 verdict=ok")
    finally:
        proc.kill()
        proc.wait(timeout=30)

    # ---------- 4. OpenAPI ----------
    r = subprocess.run([sys.executable, "-B", TOOL, "--openapi"], capture_output=True,
                       text=True, encoding="utf-8", env=ENV, timeout=120)
    spec = json.loads(r.stdout)
    check(spec.get("openapi", "").startswith("3."), "--openapi 输出 OpenAPI 3.x")
    check(set(spec.get("paths", {})) == {"/check", "/cost"}, "OpenAPI 只声明 /check 与 /cost")

    if fails:
        print("")
        print("%d 项失败" % len(fails))
        return 1
    print("")
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
