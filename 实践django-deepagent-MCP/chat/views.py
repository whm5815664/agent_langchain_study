import json

from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from chat.agent import stream_chat_sse


@ensure_csrf_cookie
@require_GET
def index(request):
    return render(request, "chat/index.html")


@require_POST
def chat_api(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "请求体必须是合法 JSON"}, status=400)

    message = (payload.get("message") or "").strip()
    if not message:
        return JsonResponse({"error": "消息不能为空"}, status=400)

    history = payload.get("history") or []
    if not isinstance(history, list):
        return JsonResponse({"error": "history 必须是数组"}, status=400)

    session_id = (payload.get("session_id") or "").strip()
    if not session_id:
        return JsonResponse({"error": "缺少 session_id（请保持页面 Edge MCP 已连接）"}, status=400)

    response = StreamingHttpResponse(
        stream_chat_sse(history, message, session_id=session_id),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
