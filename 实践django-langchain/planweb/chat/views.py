import json

from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from chat.agent import clear_temp_images, request_cancel, stream_chat_sse


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
    images = payload.get("images") or []
    if not isinstance(images, list):
        return JsonResponse({"error": "images 必须是数组"}, status=400)
    if not message and not images:
        return JsonResponse({"error": "消息不能为空（请输入文字或上传图片）"}, status=400)

    history = payload.get("history") or []
    if not isinstance(history, list):
        return JsonResponse({"error": "history 必须是数组"}, status=400)

    run_id = payload.get("run_id")
    if run_id is not None and not isinstance(run_id, str):
        return JsonResponse({"error": "run_id 必须是字符串"}, status=400)

    response = StreamingHttpResponse(
        stream_chat_sse(history, message, images=images, run_id=run_id),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@require_POST
def chat_stop_api(request):
    """强制停止当前会话的 agent 执行；不终止计划清单。"""
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "请求体必须是合法 JSON"}, status=400)

    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return JsonResponse({"error": "run_id 不能为空"}, status=400)

    ok = request_cancel(run_id.strip())
    return JsonResponse(
        {
            "ok": ok,
            "message": "已请求停止本轮执行" if ok else "未找到进行中的执行",
        }
    )


@require_POST
def chat_refresh_api(request):
    """刷新会话：清除用户上传的暂存图片（media/temp/tempN.jpg）。"""
    deleted = clear_temp_images()
    return JsonResponse(
        {
            "ok": True,
            "deleted": deleted,
            "message": f"已清除 {deleted} 个暂存图片" if deleted else "无暂存图片",
        }
    )
