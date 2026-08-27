# -*- coding: utf-8 -*-
"""Cloud Agent ↔ Edge：会话注册表 + JSON-RPC 客户端。"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import Future, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from typing import Any, Callable

PROTOCOL_VERSION = "2024-11-05"
TIMEOUT = 60.0

SendFn = Callable[[dict], None]


def request(method: str, params: dict | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
        **({"params": params} if params else {}),
    }


def notification(method: str, params: dict | None = None) -> dict:
    return {"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})}


def is_response(msg: dict) -> bool:
    return "id" in msg and ("result" in msg or "error" in msg) and "method" not in msg


def tool_result_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return str(result)
    if result.get("isError"):
        parts = [str(x.get("text", "")) for x in (result.get("content") or []) if isinstance(x, dict)]
        return "Error: " + (" ".join(parts) or str(result))
    content = result.get("content")
    if isinstance(content, list):
        return "\n".join(str(x.get("text", x)) for x in content if isinstance(x, dict))
    return str(result)


@dataclass
class EdgeSession:
    session_id: str
    send: SendFn
    pending: dict[str, Future] = field(default_factory=dict)
    tools_cache: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    online: bool = True

    def resolve(self, req_id: str, payload: dict) -> None:
        with self.lock:
            fut = self.pending.pop(str(req_id), None)
        if fut and not fut.done():
            fut.set_result(payload)

    def reject_all(self, exc: BaseException) -> None:
        with self.lock:
            items = list(self.pending.items())
            self.pending.clear()
            self.online = False
        for _, fut in items:
            if not fut.done():
                fut.set_exception(exc)


class EdgeRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, EdgeSession] = {}
        self._lock = threading.Lock()

    def register(self, session_id: str, send: SendFn) -> EdgeSession:
        session = EdgeSession(session_id=session_id, send=send)
        with self._lock:
            old = self._sessions.get(session_id)
            self._sessions[session_id] = session
        if old:
            old.reject_all(ConnectionError("Edge 被新连接替换"))
        return session

    def unregister(self, session_id: str, session: EdgeSession | None = None) -> None:
        with self._lock:
            current = self._sessions.get(session_id)
            if not current or (session and current is not session):
                return
            del self._sessions[session_id]
        current.reject_all(ConnectionError("Edge 已断开"))

    def get(self, session_id: str) -> EdgeSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def is_online(self, session_id: str) -> bool:
        s = self.get(session_id)
        return bool(s and s.online)

    def status(self, session_id: str) -> dict[str, Any]:
        s = self.get(session_id)
        if not s or not s.online:
            return {"online": False, "tools": [], "tool_count": 0}
        return {"online": True, "tools": list(s.tools_cache), "tool_count": len(s.tools_cache)}


edge_registry = EdgeRegistry()


class EdgeMcpError(Exception):
    pass


class EdgeMcpClient:
    def __init__(self, session_id: str, timeout: float = TIMEOUT) -> None:
        self.session_id = (session_id or "").strip()
        self.timeout = timeout

    def _session(self) -> EdgeSession:
        if not self.session_id:
            raise EdgeMcpError("缺少 session_id")
        s = edge_registry.get(self.session_id)
        if not s or not s.online:
            raise EdgeMcpError("Edge 未连接，请保持页面打开")
        return s

    def _rpc(self, method: str, params: dict | None = None) -> Any:
        session = self._session()
        req = request(method, params)
        req_id = str(req["id"])
        fut: Future = Future()
        with session.lock:
            session.pending[req_id] = fut
        try:
            session.send(req)
        except Exception as exc:
            with session.lock:
                session.pending.pop(req_id, None)
            raise EdgeMcpError(f"发送失败: {exc}") from exc

        try:
            resp = fut.result(timeout=self.timeout)
        except FuturesTimeout as exc:
            with session.lock:
                session.pending.pop(req_id, None)
            raise EdgeMcpError(f"超时: {method}") from exc

        if not isinstance(resp, dict):
            raise EdgeMcpError(f"非法响应: {resp!r}")
        if "error" in resp:
            err = resp["error"] or {}
            raise EdgeMcpError(err.get("message", "MCP 错误") if isinstance(err, dict) else str(err))
        return resp.get("result")

    def initialize(self) -> dict:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "cloud-agent", "version": "1.0"},
            },
        )
        try:
            self._session().send(notification("notifications/initialized"))
        except Exception:
            pass
        return result if isinstance(result, dict) else {}

    def list_tools(self, refresh: bool = True) -> list[dict]:
        session = self._session()
        if not refresh and session.tools_cache:
            return list(session.tools_cache)
        result = self._rpc("tools/list", {})
        tools = []
        if isinstance(result, dict):
            for t in result.get("tools") or []:
                if isinstance(t, dict) and t.get("name"):
                    tools.append(t)
        with session.lock:
            session.tools_cache = tools
        return list(tools)

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        return tool_result_text(result)
