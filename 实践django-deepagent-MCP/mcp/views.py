import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from mcp.client import edge_registry


@require_GET
def edge_status(request):
    session_id = (request.GET.get("session_id") or "").strip()
    if not session_id:
        return JsonResponse({"error": "缺少 session_id"}, status=400)
    return JsonResponse(edge_registry.status(session_id))


def _read_json_body(request) -> dict:
    try:
        raw = request.body.decode("utf-8") if request.body else "{}"
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


@csrf_exempt
@require_http_methods(["GET", "POST"])
def mcp_control(request):
    """MCP 控制面板：保存配置 + 查询本机桥接状态（桥接请用 mcp/startmcpserver/ 手动启动）。"""
    from mcp.server.runtime import (
        bridge_base_url,
        discover_program_fields,
        load_config,
        save_config,
        status,
    )

    if request.method == "GET":
        cfg = load_config()
        st = status()
        if request.GET.get("light") == "1":
            return JsonResponse(
                {
                    "ok": True,
                    "config": {"host": cfg["host"], "port": cfg["port"]},
                    "status": st,
                    "base_url": bridge_base_url(cfg),
                }
            )
        return JsonResponse(
            {
                "ok": True,
                "config": cfg,
                "program_fields": discover_program_fields(),
                "status": st,
                "base_url": bridge_base_url(cfg),
            }
        )

    body = _read_json_body(request)
    action = str(body.get("action") or "status").strip().lower()

    if action == "save":
        cfg = save_config(
            {
                "host": body.get("host"),
                "port": body.get("port"),
                "programs": body.get("programs") if isinstance(body.get("programs"), dict) else {},
            }
        )
        return JsonResponse(
            {
                "ok": True,
                "message": "配置已保存（请用 mcp/startmcpserver 启动桥接后生效）",
                "config": cfg,
                "base_url": bridge_base_url(cfg),
                "status": status(),
                "program_fields": discover_program_fields(),
            }
        )

    if action == "status":
        cfg = load_config()
        return JsonResponse(
            {
                "ok": True,
                "config": cfg,
                "program_fields": discover_program_fields(),
                "status": status(),
                "base_url": bridge_base_url(cfg),
            }
        )

    return JsonResponse(
        {
            "ok": False,
            "message": f"未知 action: {action}（桥接请运行 mcp/startmcpserver/start_mcp.bat 手动启动）",
        },
        status=400,
    )
