"""Edge WebSocket：JSON-RPC 收发 + 路由。"""

from __future__ import annotations

import asyncio
import json
import logging

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.urls import path

from .edge import edge_registry, is_response, notification

logger = logging.getLogger(__name__)


class EdgeMcpConsumer(AsyncWebsocketConsumer):
    session_id = ""
    _edge_session = None
    _loop = None

    async def connect(self) -> None:
        query = self.scope.get("query_string", b"").decode()
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        session_id = (params.get("session_id") or "").strip()
        if not session_id or len(session_id) > 128:
            await self.close(code=4400)
            return

        self.session_id = session_id
        await self.accept()
        self._loop = asyncio.get_running_loop()

        def send_sync(msg: dict) -> None:
            fut = asyncio.run_coroutine_threadsafe(
                self.send(text_data=json.dumps(msg, ensure_ascii=False)),
                self._loop,
            )
            fut.result(timeout=15)

        self._edge_session = await sync_to_async(edge_registry.register)(session_id, send_sync)
        await self.send(
            text_data=json.dumps(
                notification("edge/connected", {"session_id": session_id}),
                ensure_ascii=False,
            )
        )
        logger.info("Edge connected: %s", session_id)

    async def disconnect(self, code: int) -> None:
        if self.session_id and self._edge_session:
            await sync_to_async(edge_registry.unregister)(self.session_id, self._edge_session)

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        raw = text_data or (bytes_data.decode() if bytes_data else "")
        if not raw:
            return
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(msg, dict) or not is_response(msg):
            return
        req_id = msg.get("id")
        if req_id is not None and self._edge_session:
            await sync_to_async(self._edge_session.resolve)(str(req_id), msg)


websocket_urlpatterns = [
    path("ws/mcp/", EdgeMcpConsumer.as_asgi()),
]
