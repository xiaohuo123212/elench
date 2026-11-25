#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
"""test_mcp_server.py —— `tools/elench_mcp.py` 的协议行为必须对得上 MCP 规范。

## 为什么要有这条测试

MCP 是**别人家的客户端**来握手的：平台按 `initialize` 的返回值决定后面怎么调。
这里错一个字段（比如 `capabilities.tools` 拼错、通知被当成请求回了一个响应、
未知方法不报 -32601），平台那边只会显示"连接失败"，没有任何线索指向我们。

所以逐条钉死：

1. `initialize` → 回 `protocolVersion` / `capabilities.tools` / `serverInfo`，且**回客户端要的版本**（认识的话）；
2. **通知不响应**（`notifications/initialized` 没有 id，回了就违反 JSON-RPC）；
3. `tools/list` → 恰好两个工具，且每个都有 `inputSchema`；
4. `tools/call` → `content[0].type == "text"`，并且带上 `structuredContent`（能直接读字段）；
5. 未知工具 → `-32602`；未知方法 → `-32601`；
6. **HTTP 传输**：`POST /mcp` 一问一答、通知回 202 无正文、批量返回数组、`GET /mcp` 如实说明不做 SSE；
7. 只开放静态工具：`tools/list` 里**不能**出现 run / stats / measure。
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

CASEDIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(CASEDIR, "..", ".."))
SERVER = os.path.join(ROOT, "tools", "elench_mcp.py")

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


def _stdio(msgs):
    inp = "\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\n"
    r = subprocess.run([sys.executable, "-B", SERVER, "--stdio"], input=inp,
                       capture_output=True, text=True, encoding="utf-8",
                       env=ENV, timeout=300)
    outs = [json.loads(ln) for ln in (r.stdout or "").split("\n") if ln.strip()]
    return r, outs


def _start_http():
    proc = subprocess.Popen([sys.executable, "-B", SERVER, "--http", "--port", "0"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", env=ENV, bufsize=1)
    url, deadline = None, time.time() + 30
    while time.time() < deadline:
        line = proc.stderr.readline()
        if not line:
            break
        if "http://" in line and "/mcp" in line:
            url = "http://" + line.split("http://", 1)[1].split()[0].strip().rstrip(",")
            break
    if not url:
        proc.kill()
        raise RuntimeError("MCP HTTP 服务没起来")
    return proc, url


def _post(url, obj):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return r.status, (json.loads(raw.decode("utf-8")) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw.decode("utf-8")) if raw else None)


def main() -> int:
    fails = []

    def check(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    # ---------------- stdio ----------------
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "elench_cost", "arguments": {"tu": TU, "eln": ELN_OK}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "elench_cost", "arguments": {"tu": TU, "eln": ELN_DEBT}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "nope", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 6, "method": "ping"},
        {"jsonrpc": "2.0", "id": 7, "method": "bogus/method"},
    ]
    r, outs = _stdio(msgs)
    check(r.returncode == 0, "stdio 进程正常退出")
    check(len(outs) == 7, "7 条请求里有 1 条是通知 ⇒ 只回 7 个响应（得到 %d）" % len(outs))

    by_id = {o.get("id"): o for o in outs if "id" in o}

    init = (by_id.get(1) or {}).get("result") or {}
    check(init.get("protocolVersion") == "2025-03-26",
          "initialize 回客户端要的协议版本（2025-03-26）")
    check(isinstance((init.get("capabilities") or {}).get("tools"), dict),
          "capabilities.tools 存在（平台靠它判断能不能调工具）")
    check((init.get("serverInfo") or {}).get("name") == "elench", "serverInfo.name = elench")
    check(bool(init.get("instructions")), "initialize 带 instructions（告诉模型怎么用）")

    tools = ((by_id.get(2) or {}).get("result") or {}).get("tools") or []
    names = sorted(t.get("name") for t in tools)
    check(names == ["elench_check", "elench_cost"], "tools/list = %s" % names)
    check(all(isinstance(t.get("inputSchema"), dict) for t in tools), "每个工具都带 inputSchema")
    check(not {"elench_run", "elench_stats", "elench_measure"} & set(names),
          "没有开放会执行代码的工具")

    ok_call = (by_id.get(3) or {}).get("result") or {}
    kappa = ((ok_call.get("structuredContent") or {}).get("functions") or {}).get("f", {}).get("kappa")
    check(kappa == "2 + n(xs)×7 + 2", "合规代码的 κ = 2 + n(xs)×7 + 2")
    check(ok_call.get("isError") is False, "合规代码 isError=false")
    check((ok_call.get("content") or [{}])[0].get("type") == "text", "content[0].type = text")

    debt_call = (by_id.get(4) or {}).get("result") or {}
    check((debt_call.get("structuredContent") or {}).get("verdict") == "debt",
          "违约代码 verdict=debt")

    check((by_id.get(5) or {}).get("error", {}).get("code") == -32602, "未知工具 → -32602")
    check((by_id.get(6) or {}).get("result") == {}, "ping → {}")
    check((by_id.get(7) or {}).get("error", {}).get("code") == -32601, "未知方法 → -32601")

    # ---------------- HTTP ----------------
    proc, url = _start_http()
    try:
        st, d = _post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18"}})
        check(st == 200 and d["result"]["protocolVersion"] == "2025-06-18",
              "HTTP initialize 回 2025-06-18")

        st, d = _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        check(st == 202 and d is None, "HTTP 通知 → 202 且无正文")

        st, d = _post(url, [{"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                            {"jsonrpc": "2.0", "id": 3, "method": "ping"}])
        check(st == 200 and isinstance(d, list) and len(d) == 2, "HTTP 批量 → 数组回 2 条")

        with urllib.request.urlopen(url, timeout=30) as resp:
            g = json.loads(resp.read().decode("utf-8"))
        check(g.get("transport") == "streamable-http" and "SSE" in (g.get("note") or ""),
              "GET /mcp 如实说明不做 SSE 流式")
    finally:
        proc.kill()
        proc.wait(timeout=30)

    # ---------------- `--list-tools` 单独可用 ----------------
    # 曾经是个 bug：`--list-tools` 不在互斥组里，而组是 required=True，
    # 于是单用它直接被 argparse 拒掉（rc=2、stdout 空）。这个开关最常见的用法
    # 恰恰就是"只想看一眼清单"，所以必须能单独跑。
    r2 = subprocess.run([sys.executable, "-B", SERVER, "--list-tools"],
                        capture_output=True, text=True, encoding="utf-8",
                        env=ENV, timeout=120)
    check(r2.returncode == 0, "`--list-tools` 能单独跑（rc=0）")
    try:
        listed = json.loads(r2.stdout)["tools"]
        have = sorted(t["name"] for t in listed)
    except Exception:
        have = []
    check(have == ["elench_check", "elench_cost"],
          "`--list-tools` 输出与 tools/list 一致：%s" % have)

    # ---------------- 内嵌网页的静态检查 ----------------
    html_path = os.path.join(ROOT, "tools", "elench_playground.html")
    if os.path.isfile(html_path):
        with open(html_path, encoding="utf-8") as f:
            html = f.read()
        # 内嵌场景常禁止外链：除了用户自己填的地址，不能出现外部资源
        externals = [u for u in ("http://", "https://", "//cdn", "unpkg", "jsdelivr")
                     if u in html and u != "http://"]
        check(not externals, "内嵌网页没有外链资源（否则平台里加载不出来）")
        check("/cost" in html and "/check" in html, "内嵌网页会调 /cost 与 /check")
        check(html.lstrip().startswith("<!DOCTYPE html>"), "内嵌网页是完整 HTML 文档")
    else:
        check(False, "找不到 tools/elench_playground.html")

    if fails:
        print("")
        print("%d 项失败" % len(fails))
        return 1
    print("")
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
