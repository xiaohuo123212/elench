#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Elench contributors
# -*- coding: utf-8 -*-
"""elench_tool.py —— 把 Elench 的「检查」与「算 κ」包成一个**平台无关的工具**。

## 为什么需要它

智能体平台（初发 Trufar / Coze / Dify / 百炼 这类）接工具的方式只有三种：

| 平台的形态 | 它要什么 | 这里怎么给 |
|---|---|---|
| **OpenAPI / HTTP 插件** | 一个公网可达的 URL + 一份 OpenAPI 文档 | `--http --port 8080` + `--openapi` |
| **代码沙箱 / 函数** | 能塞进沙箱跑的一段代码，靠 stdin/stdout 传 JSON | `--stdio`（整个仓库零第三方依赖，直接把 `src/` 一起塞进去就能跑） |
| **MCP / 自定义协议** | 一个能一问一答的进程 | `--stdio`，每行一个 JSON 请求、每行一个 JSON 响应 |

三种都从**同一个 `handle()`** 出去，所以行为不会因为接法不同而漂移。

## 只暴露**静态**操作（重要的安全决定）

本工具只开 `check` 和 `cost` 两个操作——它们**只读源码、不执行用户代码**。
故意**不**开 `run` / `stats` / `measure`：那三个会真的把提交的代码跑起来，
在别人家的平台上天真地开放它们等于免费提供一个执行沙箱。
要执行请另外部署 `dsh run`，并且自己加资源限制。

## 用法

```bash
python -B tools/elench_tool.py --openapi                 # 打印 OpenAPI 3.0 文档（粘到平台里）
python -B tools/elench_tool.py --http --port 8080        # 起 HTTP 服务
python -B tools/elench_tool.py --stdio                   # stdin/stdout，一行一个 JSON
```

请求（三种接法一致）：

```json
{"op": "cost", "tu": "spec f(...) -> u64 { ... }", "eln": "u64 f(...) { ... }"}
```

响应：

```json
{"ok": true, "op": "cost", "module": "f",
 "functions": {"f": {"kappa": "2 + n(xs)×7 + 2", "verdict": "ok",
                     "explanation": "达成：阶够，比值 ≈ 7.00",
                     "allocation": "0", "allocation_verdict": "ok"}},
 "diagnostics": []}
```

## 认证与限额

- `ELENCH_TOOL_TOKEN`：设了它，HTTP 侧就要求 `Authorization: Bearer <token>`；
  没设就是**裸奔**（只适合本机或内网）。响应里会明说当前是哪种模式，不假装有认证。
- 单个字段上限 `MAX_FIELD_BYTES`（默认 256 KB），总请求上限 `MAX_BODY_BYTES`（512 KB）。
- 每次调用有 `TIMEOUT_S`（默认 30 秒）——超时直接报错，不挂住平台。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

# **进程内调用，不再 fork**（2026-11-18）：`src/core_api.py` 直接调 dsh 的检查/成本函数，
# 结果与 `dsh check/cost --json` 逐字段相同（有自检兜底）。这样做的两个理由：
#   ① 每次调用省掉一次解释器启动（约 100 ms）；
#   ② 浏览器（Pyodide）里没有 subprocess —— 换成进程内调，同一份代码能在 WASM 里跑。
import core_api  # noqa: E402

TOOL_NAME = "elench-cost-contract"
TOOL_VERSION = "1.0.0"

MAX_FIELD_BYTES = 256 * 1024
MAX_BODY_BYTES = 512 * 1024
TIMEOUT_S = 30.0

#: 只开静态操作。加操作前先想清楚：会不会变成"免费代码执行"。
OPS = ("check", "cost", "version")


class ToolError(Exception):
    """给调用方看的错误（会进 JSON，不是 traceback）。"""


# ---------------------------------------------------------------- 核心


def _payload(req: Dict[str, Any]) -> Tuple[str, str, str]:
    """校验请求，落成临时目录里的 (tu_path, eln_path, module_name)。"""
    op = req.get("op") or "check"
    if op not in OPS:
        raise ToolError("不支持的 op=%r；可选：%s（故意不开放会执行代码的 run/stats/measure）"
                        % (op, ", ".join(OPS)))
    tu = req.get("tu") or ""
    eln = req.get("eln") or ""
    if not isinstance(tu, str) or not isinstance(eln, str):
        raise ToolError("tu / eln 必须是字符串")
    if not tu.strip() and not eln.strip():
        raise ToolError("tu 与 eln 至少要给一个（只给 tu 就只查契约，只给 eln 就查实现）")
    for name, text in (("tu", tu), ("eln", eln)):
        if len(text.encode("utf-8")) > MAX_FIELD_BYTES:
            raise ToolError("%s 超过上限 %d 字节" % (name, MAX_FIELD_BYTES))
    # 模块名固定成 probe：平台侧通常不关心名字，固定住能让 --json 输出稳定
    return tu, eln, "probe"


def _trim_cost(payload: Dict[str, Any]) -> Dict[str, Any]:
    """把 `cost --json` 的大符号树剪成平台真正要看的几项。

    完整符号树动辄几十层，塞进对话既看不懂也占 token。这里只留
    人/模型都能直接用的字段：**κ 的字符串、判决、判决理由、分配**。
    """
    out = {}
    for name, fn in (payload.get("functions") or {}).items():
        out[name] = {
            "kappa": fn.get("cost_expression"),
            "verdict": fn.get("verdict"),
            "explanation": fn.get("verdict_explanation"),
            "allocation": fn.get("allocation_expression"),
            "allocation_verdict": fn.get("allocation_verdict"),
            "allocation_explanation": fn.get("allocation_explanation"),
        }
    return out


def _trim_check(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": payload.get("ok"),
        "functions": [
            {"name": f.get("name"), "has_target": f.get("has_target"),
             "has_implementation": f.get("has_implementation"), "state": f.get("state")}
            for f in (payload.get("functions") or [])
        ],
        "diagnostics": [
            # `fix` / `suggestion` 一起带上：平台上的 agent 拿到"怎么改"才动得起来，
            # 只给一个诊断码它只能瞎猜。
            {"code": d.get("code"), "line": d.get("line"), "col": d.get("col"),
             "severity": d.get("severity"), "message": d.get("message"),
             "fix": d.get("fix"), "suggestion": d.get("suggestion")}
            for d in (payload.get("diagnostics") or [])
        ],
    }


#: 判决的"差"程度排序：无法判定最差（连结论都没有），其次债务，最后达成。
_VERDICT_RANK = {"unknown": 0, "debt": 1, "ok": 2}


def _worst_verdict(fns: Dict[str, Any]) -> Any:
    """把多个函数的判决收成一个：取**最差**的那个，不粉饰。"""
    vs = [v.get("verdict") for v in fns.values() if isinstance(v, dict)]
    vs = [v for v in vs if v is not None]
    if not vs:
        return None
    return min(vs, key=lambda v: _VERDICT_RANK.get(v, 0))


def handle(req: Dict[str, Any]) -> Dict[str, Any]:
    """唯一的入口：一个 dict 进，一个 dict 出。三种接法都走这里。"""
    t0 = time.time()
    try:
        if not isinstance(req, dict):
            raise ToolError("请求必须是 JSON 对象")
        op = req.get("op") or "check"

        if op == "version":
            return {"ok": True, "op": "version", "tool": TOOL_NAME,
                    "tool_version": TOOL_VERSION,
                    "python": sys.version.split()[0],
                    "root": ROOT}

        tu, eln, _mod = _payload(req)
        try:
            # 进程内调用（不 fork）：结果与 `dsh <op> --json` 逐字段相同，
            # 由 `src/core_api.py` 的自检保证。核心逻辑只有那一份。
            payload = core_api.handle({"op": op, "tu": tu, "eln": eln,
                                       "compat": bool(req.get("compat"))})
        except core_api.CoreError as e:
            raise ToolError(str(e))
        rc = int(payload.pop("_rc", 0) or 0)
        stderr_text = payload.pop("_stderr", "") or ""

        # 注意：函数名**不能**平铺进顶层——用户可以把函数叫 `ok` / `op` / `error`，
        # 平铺就会把状态字段盖掉。所以统一收进 `functions`。
        result: Dict[str, Any] = {"ok": rc == 0, "op": op, "module": payload.get("module")}
        if op == "cost":
            fns = _trim_cost(payload)
            result["functions"] = fns
            # `ok` 只表示"工具跑完了"；代码合不合格看 `verdict`（取所有函数里最差的那个）。
            # 这两个不要混：混了平台会把"契约不达标"当成"工具坏了"。
            result["verdict"] = _worst_verdict(fns)
        else:
            c = _trim_check(payload)
            result["functions"] = c["functions"]
            result["diagnostics"] = c["diagnostics"]
            result["check_ok"] = c["ok"]
        result["exit_code"] = rc
        result["elapsed_ms"] = int((time.time() - t0) * 1000)
        if rc != 0 and stderr_text.strip():
            # 不是崩溃，是"查出来有问题"；把 stderr 尾巴带上便于定位
            result["stderr_tail"] = stderr_text.strip().splitlines()[-1][:300]
        return result
    except ToolError as e:
        return {"ok": False, "op": req.get("op") if isinstance(req, dict) else None,
                "error": str(e), "elapsed_ms": int((time.time() - t0) * 1000)}


# ---------------------------------------------------------------- OpenAPI


def openapi(server: str = "http://127.0.0.1:8080") -> Dict[str, Any]:
    """生成 OpenAPI 3.0 文档。平台侧「导入插件」基本都吃这个。"""
    def schema_op(op: str, desc: str) -> Dict[str, Any]:
        return {
            "post": {
                "operationId": "elench_" + op,
                "summary": desc,
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string", "enum": [op]},
                            "tu": {"type": "string",
                                   "description": "契约文件（.tu）全文；只有实现时可省略"},
                            "eln": {"type": "string",
                                    "description": "实现文件（.eln）全文；只有契约时可省略"},
                        },
                        "required": ["op"],
                    }}},
                },
                "responses": {"200": {"description": "结果（JSON）",
                                      "content": {"application/json": {"schema": {
                                          "type": "object"}}}}},
            }
        }

    return {
        "openapi": "3.0.1",
        "info": {
            "title": "Elench 成本契约检查",
            "description": ("给一段 Elench 源码，返回静态诊断，或算出成本上界 κ 并判定"
                            "「达成 / 债务 / 无法判定」。只读源码，不执行代码。"),
            "version": TOOL_VERSION,
        },
        "servers": [{"url": server}],
        "paths": {
            "/check": schema_op("check", "静态检查：语法 / 类型 / 契约一致性，返回诊断列表"),
            "/cost": schema_op("cost", "算 κ 并与 .tu 里的 target 比，返回判决与理由"),
        },
    }


# ---------------------------------------------------------------- HTTP


def serve_http(host: str, port: int) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    token = os.environ.get("ELENCH_TOOL_TOKEN", "")

    class Handler(BaseHTTPRequestHandler):
        server_version = "elench-tool/" + TOOL_VERSION

        def log_message(self, fmt, *a):  # 安静一点，平台日志已经够多
            sys.stderr.write("[elench-tool] " + (fmt % a) + "\n")

        def _send(self, code: int, obj: Any) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):  # 浏览器预检
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.end_headers()

        def _authed(self) -> bool:
            if not token:
                return True
            got = self.headers.get("Authorization", "")
            return got.strip() == "Bearer " + token

        def do_GET(self):
            if self.path.rstrip("/") in ("/health", "/"):
                self._send(200, {"ok": True, "tool": TOOL_NAME,
                                 "auth": "bearer" if token else "none",
                                 "warning": None if token else "未设 ELENCH_TOOL_TOKEN：本端口对任何能访问到它的人开放"})
                return
            self._send(404, {"ok": False, "error": "只有 GET /health"})

        def do_POST(self):
            if not self._authed():
                self._send(401, {"ok": False, "error": "需要 Authorization: Bearer <ELENCH_TOOL_TOKEN>"})
                return
            op = self.path.rstrip("/").lstrip("/")
            if op not in ("check", "cost"):
                self._send(404, {"ok": False, "error": "只有 /check 与 /cost"})
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0 or n > MAX_BODY_BYTES:
                self._send(413, {"ok": False, "error": "请求体为空或超过 %d 字节" % MAX_BODY_BYTES})
                return
            try:
                req = json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception as e:
                self._send(400, {"ok": False, "error": "请求体不是合法 JSON：%s" % str(e)[:120]})
                return
            # 路径决定 op（平台常按路径建工具），body 里的 op 只做兜底
            if isinstance(req, dict):
                req.setdefault("op", op)
                req["op"] = op
            self._send(200, handle(req))

    srv = ThreadingHTTPServer((host, port), Handler)
    real_port = srv.server_address[1]
    print("elench-tool 已启动：http://%s:%d  （认证：%s）"
          % (host, real_port, "Bearer" if token else "无"), file=sys.stderr)
    print("  GET  /health   POST /check   POST /cost", file=sys.stderr)
    print("  OpenAPI：python -B tools/elench_tool.py --openapi --server=http://<本机可达地址>:%d"
          % real_port, file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n收到中断，退出。", file=sys.stderr)
    finally:
        srv.server_close()
    return 0


def serve_stdio() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            resp = {"ok": False, "error": "输入不是合法 JSON：%s" % str(e)[:120]}
        else:
            # **单条请求炸了不能打断整条流**：stdio 是长连接，一次意外异常
            # 会让后面所有请求都收不到响应（2026-11-18 实测踩到：4 条只回了 2 条）。
            try:
                resp = handle(req)
            except Exception as e:                     # noqa: BLE001
                resp = {"ok": False, "op": (req or {}).get("op") if isinstance(req, dict) else None,
                        "error": "内部错误：%s: %s" % (type(e).__name__, str(e)[:200])}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


# ---------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="elench_tool",
        description="把 Elench 的 check / cost 包成平台可用的工具（HTTP / stdio / OpenAPI）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--openapi", action="store_true", help="打印 OpenAPI 3.0 文档")
    g.add_argument("--http", action="store_true", help="起 HTTP 服务")
    g.add_argument("--stdio", action="store_true", help="stdin/stdout，一行一个 JSON")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--server", default=None,
                    help="--openapi 时写进 servers[].url 的地址（平台要能访问到）")
    args = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if args.openapi:
        print(json.dumps(openapi(args.server or ("http://%s:%d" % (args.host, args.port))),
                         ensure_ascii=False, indent=2))
        return 0
    if args.http:
        return serve_http(args.host, args.port)
    return serve_stdio()


if __name__ == "__main__":
    sys.exit(main())
