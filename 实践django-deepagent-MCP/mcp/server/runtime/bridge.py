# -*- coding: utf-8 -*-
"""本机桥接：HTTP 服务（执行 tools/*.py）。

请用 mcp/startmcpserver/ 中的脚本手动启动，例如::

    mcp\\startmcpserver\\start_mcp.bat
    # 或
    python -m mcp.startmcpserver

Django / 网页不会自动启动本服务；「MCP 设置」仅用于保存端口与程序路径。
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from .catalog import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    bridge_base_url,
    build_manifest,
    discover_local_tools,
    load_config,
)


# ---------- 状态探测（供网页「MCP 设置」查询） ----------

def is_listening(host: str, port: int, timeout: float = 0.15) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def health(*, host: str | None = None, port: int | None = None, timeout: float = 0.25) -> dict[str, Any] | None:
    cfg = load_config()
    h = host or str(cfg["host"])
    p = int(port if port is not None else cfg["port"])
    if not is_listening(h, p, timeout=min(0.12, timeout)):
        return None
    url = f"http://{h}:{p}/health"
    try:
        with urlopen(url, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
    except (URLError, OSError, ValueError, TimeoutError):
        return None


def status() -> dict[str, Any]:
    cfg = load_config()
    host = str(cfg["host"])
    port = int(cfg["port"])
    base = bridge_base_url(cfg)
    if not is_listening(host, port, timeout=0.12):
        return {
            "ok": False,
            "online": False,
            "host": host,
            "port": port,
            "base_url": base,
            "tools": [],
            "message": "本地桥接未启动（请运行 mcp/startmcpserver/start_mcp.bat）",
        }
    info = health(host=host, port=port, timeout=0.35) or {}
    return {
        "ok": True,
        "online": True,
        "host": host,
        "port": port,
        "base_url": base,
        "tools": info.get("tools") or [],
        "message": f"本地桥接在线 · {base}",
    }


# ---------- HTTP 服务 ----------

def _mcp_text_result(data: Any, *, is_error: bool = False) -> dict[str, Any]:
    if isinstance(data, dict) and "content" in data:
        return data
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


class LocalToolRegistry:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        _, self.handlers = discover_local_tools()
        try:
            build_manifest(write=True)
        except Exception as exc:
            print(f"[bridge] manifest 生成失败: {exc}", file=sys.stderr)

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        handler = self.handlers.get(name)
        if handler is None:
            return _mcp_text_result(f"未知本地工具: {name}", is_error=True)
        try:
            return _mcp_text_result(handler(arguments or {}))
        except Exception as exc:
            return _mcp_text_result(f"工具执行失败: {exc}", is_error=True)

    def list_tools(self) -> list[str]:
        return sorted(self.handlers.keys())


REGISTRY = LocalToolRegistry()


class _BridgeHandler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        return data if isinstance(data, dict) else {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            cfg = load_config()
            self._send(
                200,
                json.dumps(
                    {
                        "ok": True,
                        "service": "mcp-local-bridge",
                        "tools": REGISTRY.list_tools(),
                        "host": cfg["host"],
                        "port": cfg["port"],
                    },
                    ensure_ascii=False,
                ).encode("utf-8"),
            )
            return
        if path == "/tools/list":
            self._send(
                200,
                json.dumps({"tools": REGISTRY.list_tools()}, ensure_ascii=False).encode("utf-8"),
            )
            return
        self._send(404, json.dumps({"ok": False, "message": "not found"}, ensure_ascii=False).encode("utf-8"))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/tools/call":
            payload = self._read_json()
            name = str(payload.get("name") or "").strip()
            arguments = payload.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            if not name:
                self._send(
                    400,
                    json.dumps({"ok": False, "message": "缺少 name"}, ensure_ascii=False).encode("utf-8"),
                )
                return
            self._send(200, json.dumps(REGISTRY.call(name, arguments), ensure_ascii=False).encode("utf-8"))
            return
        if path == "/reload":
            REGISTRY.reload()
            self._send(
                200,
                json.dumps({"ok": True, "tools": REGISTRY.list_tools()}, ensure_ascii=False).encode("utf-8"),
            )
            return
        self._send(404, json.dumps({"ok": False, "message": "not found"}, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        sys.stderr.write("[bridge] %s - %s\n" % (self.address_string(), format % args))


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    httpd = HTTPServer((host, port), _BridgeHandler)
    tools = REGISTRY.list_tools()
    print(f"MCP 本地桥接: http://{host}:{port}", flush=True)
    print(f"已加载 {len(tools)} 个本地工具: {', '.join(tools) or '(无)'}", flush=True)
    print("  POST /tools/call   {name, arguments}", flush=True)
    print("  GET  /health", flush=True)
    print("  POST /reload", flush=True)
    print("按 Ctrl+C 停止", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止", flush=True)


def main() -> int:
    cfg = load_config()
    parser = argparse.ArgumentParser(description="MCP local bridge")
    parser.add_argument("--host", default=str(cfg.get("host") or DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(cfg.get("port") or DEFAULT_PORT))
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    if __package__ in {None, ""}:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from mcp.server.runtime.bridge import main as _main

        raise SystemExit(_main())
    raise SystemExit(main())
