#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""elench_mcp.py —— 把 Elench 的成本契约检查做成一个 **MCP server**。

## 为什么是 MCP

智能体平台（初发 Trufar 这类）现在多数支持"添加 MCP 服务"。MCP 比 OpenAPI 插件
省事的地方在于：**工具清单是运行时自描述的**——平台 `tools/list` 一次就知道有
哪些工具、参数是什么，不用人对着表单手填 schema。

本文件**不重复实现检查逻辑**：它只做协议转换，真正干活的是
`tools/elench_tool.py` 的 `handle()`。所以 HTTP 插件、stdio、MCP 三条路
出来的结果逐字节相同。

## 两种传输

| 传输 | 启动 | 平台怎么配 |
|---|---|---|
| **stdio** | `--stdio` | 填命令 `python -B <repo>/tools/elench_mcp.py --stdio`（平台能跑本地进程时用） |
| **streamable HTTP** | `--http --port 8080` | 填 URL `http://<平台可达地址>:8080/mcp`（云平台通常只能这样） |

stdio 用**换行分隔的 JSON-RPC**（MCP 的规定），不是 Content-Length 分帧。

## 支持的方法

    initialize · notifications/initialized · tools/list · tools/call · ping

只做这三件事是有意的：`resources` / `prompts` / `sampling` 对本工具没有意义，
声明了却不实现比不声明更坏。

## 安全

和 `elench_tool.py` 同一条线：**只开静态的 `elench_check` / `elench_cost`**，
不执行提交的代码。HTTP 侧可用 `ELENCH_TOOL_TOKEN` 要求 `Authorization: Bearer`。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from elench_tool import (MAX_BODY_BYTES, TOOL_VERSION, ToolError,  # noqa: E402
                         handle)

SERVER_NAME = "elench"
#: 我们实现的协议版本；客户端要别的就回它要的（只要认识），否则回最新的。
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL = PROTOCOL_VERSIONS[0]

TOOLS = [
    {
        "name": "elench_check",
        "description": (
            "对一段 Elench 源码做静态检查：语法、类型、`.tu` 与 `.eln` 的契约一致性。"
            "返回诊断列表（每条带诊断码、行列、消息、建议改法）。"
            "只读源码，不执行代码。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tu": {"type": "string",
                       "description": "契约文件（.tu）全文：spec / target / effects / requires。只查实现时可省略。"},
                "eln": {"type": "string",
                        "description": "实现文件（.eln）全文：函数体。只查契约时可省略。"},
                "compat": {"type": "boolean",
                           "description": "C++ 兼容层（等价于命令行的 --compat）："
                                          "打开后 .cpp 原样改名成 .eln 就能查——"
                                          "using / typedef / auto / std:: / void / += / 多声明符等写法被接受。"},
            },
            "required": [],
        },
    },
    {
        "name": "elench_cost",
        "description": (
            "算出 Elench 实现的**成本上界 κ**（一个操作数上界，不是墙钟时间），"
            "并与 `.tu` 里声明的 target 比较，返回判决："
            "ok（达成）/ debt（超界，是债务）/ unknown（无法判定）。"
            "多函数时 verdict 取最差的那个。只读源码，不执行代码。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tu": {"type": "string",
                       "description": "契约文件（.tu）全文；没有它就没有可比的上界，只会返回 κ。"},
                "eln": {"type": "string",
                        "description": "实现文件（.eln）全文。"},
                "compat": {"type": "boolean",
                           "description": "C++ 兼容层（等价于命令行的 --compat）："
                                          "打开后 .eln 里可以直接写 C++ 的拼写。"},
            },
            "required": ["eln"],
        },
    },
]


# ---------------------------------------------------------------- JSON-RPC


def _rpc_error(mid, code: int, message: str, data=None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": err}


def _rpc_result(mid, result) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def dispatch(msg: dict) -> dict | None:
    """处理一条 JSON-RPC 消息。通知（没有 id）返回 None。"""
    if not isinstance(msg, dict):
        return _rpc_error(None, -32600, "Invalid Request：要 JSON-RPC 对象")
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    is_notification = mid is None

    if method == "initialize":
        want = (params or {}).get("protocolVersion")
        ver = want if want in PROTOCOL_VERSIONS else LATEST_PROTOCOL
        return _rpc_result(mid, {
            "protocolVersion": ver,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": TOOL_VERSION},
            "instructions": (
                "Elench 是一门带**成本契约**的语言：`.tu` 里写 target（要求的上界），"
                "`.eln` 里写实现。用 elench_cost 可以算出实现真实的上界 κ 并判定是否满足 target；"
                "用 elench_check 查语法/类型/契约一致性。两个工具都只读源码、不执行代码。"
            ),
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None  # 通知不需要响应

    if method == "ping":
        return _rpc_result(mid, {})

    if method == "tools/list":
        return _rpc_result(mid, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in ("elench_check", "elench_cost"):
            return _rpc_error(mid, -32602, "Unknown tool: %r（可选：elench_check, elench_cost）" % name)
        if not isinstance(args, dict):
            return _rpc_error(mid, -32602, "arguments 必须是对象")
        req = dict(args)
        req["op"] = "check" if name == "elench_check" else "cost"
        req["compat"] = bool(args.get("compat"))
        try:
            result = handle(req)
        except ToolError as e:
            result = {"ok": False, "error": str(e)}
        return _rpc_result(mid, {
            "content": [{"type": "text",
                         "text": json.dumps(result, ensure_ascii=False, indent=2)}],
            # 顺带给结构化结果：支持的客户端能直接读字段，不必再解一遍 JSON 文本
            "structuredContent": result,
            "isError": not result.get("ok", False),
        })

    if is_notification:
        return None
    return _rpc_error(mid, -32601, "Method not found: %r" % method)


# ---------------------------------------------------------------- stdio


def serve_stdio() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            resp = _rpc_error(None, -32700, "Parse error: %s" % str(e)[:120])
        else:
            resp = dispatch(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


# ---------------------------------------------------------------- HTTP


def serve_http(host: str, port: int) -> int:
    """MCP streamable HTTP：POST /mcp 收发 JSON-RPC，GET /mcp 说明用法。

    单请求单响应时直接回 `application/json`（规范允许），不做 SSE 流式——
    本工具没有"长时间分块输出"的场景，假装支持流式只会让客户端等不到东西。
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    token = os.environ.get("ELENCH_TOOL_TOKEN", "")

    class Handler(BaseHTTPRequestHandler):
        server_version = "elench-mcp/" + TOOL_VERSION

        def log_message(self, fmt, *a):
            sys.stderr.write("[elench-mcp] " + (fmt % a) + "\n")

        def _send(self, code: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, Authorization, Mcp-Session-Id, Mcp-Protocol-Version")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, Authorization, Mcp-Session-Id, Mcp-Protocol-Version")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.end_headers()

        def _authed(self) -> bool:
            if not token:
                return True
            return self.headers.get("Authorization", "").strip() == "Bearer " + token

        def do_GET(self):
            if self.path.rstrip("/") in ("/health", ""):
                self._send(200, {"ok": True, "server": SERVER_NAME, "version": TOOL_VERSION,
                                 "auth": "bearer" if token else "none",
                                 "warning": None if token else "未设 ELENCH_TOOL_TOKEN：本端口对任何能访问到它的人开放"})
                return
            if self.path.rstrip("/") == "/mcp":
                # 规范里 GET /mcp 用于开 SSE 流；我们不开流，如实说明
                self._send(200, {"ok": True, "transport": "streamable-http",
                                 "note": "本服务不做 SSE 流式：请 POST /mcp 一问一答（Content-Type: application/json）",
                                 "methods": ["initialize", "tools/list", "tools/call", "ping"]})
                return
            self._send(404, {"ok": False, "error": "只有 GET /health 与 /mcp"})

        def do_POST(self):
            if self.path.rstrip("/") != "/mcp":
                self._send(404, {"ok": False, "error": "只有 POST /mcp"})
                return
            if not self._authed():
                self._send(401, {"ok": False, "error": "需要 Authorization: Bearer <ELENCH_TOOL_TOKEN>"})
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0 or n > MAX_BODY_BYTES:
                self._send(413, {"ok": False, "error": "请求体为空或超过 %d 字节" % MAX_BODY_BYTES})
                return
            try:
                msg = json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception as e:
                self._send(400, _rpc_error(None, -32700, "Parse error: %s" % str(e)[:120]))
                return
            if isinstance(msg, list):  # 批量
                out = [r for r in (dispatch(m) for m in msg) if r is not None]
                if not out:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._send(200, out)
                return
            resp = dispatch(msg)
            if resp is None:  # 通知：按规范回 202 无正文
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send(200, resp)

    srv = ThreadingHTTPServer((host, port), Handler)
    print("elench-mcp 已启动：http://%s:%d/mcp  （认证：%s）"
          % (host, srv.server_address[1], "Bearer" if token else "无"), file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n收到中断，退出。", file=sys.stderr)
    finally:
        srv.server_close()
    return 0


# ---------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="elench_mcp",
        description="Elench 的 MCP server：暴露 elench_check / elench_cost（只读，不执行代码）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--stdio", action="store_true", help="stdio 传输（换行分隔 JSON-RPC）")
    g.add_argument("--http", action="store_true", help="streamable HTTP 传输（POST /mcp）")
    # 放在同一个互斥组里：`--list-tools` 是"只打印清单就退出"，和起服务不能同时要求，
    # 也不能单独再用一个 required=False 的开关（那样不写任何参数时会静默什么都不做）。
    g.add_argument("--list-tools", action="store_true",
                   help="只打印 tools/list 的结果就退出（调试用，配 MCP Inspector 之类）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if args.list_tools:
        print(json.dumps({"tools": TOOLS}, ensure_ascii=False, indent=2))
        return 0
    if args.http:
        return serve_http(args.host, args.port)
    return serve_stdio()


if __name__ == "__main__":
    sys.exit(main())
