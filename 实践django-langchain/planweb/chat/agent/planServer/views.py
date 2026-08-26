"""计划清单页与 API 视图（业务委托给 planServer）。"""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from chat.agent.planServer import list_plans_payload, mutate_plan


@ensure_csrf_cookie
@xframe_options_sameorigin
@require_GET
def plan_page(request):
    """计划清单独立页面（由 index 右侧 iframe 嵌入）。"""
    return render(request, "chat/plan.html")


@require_GET
def plans_api(request):
    return JsonResponse(list_plans_payload())


def _json(result: dict):
    return JsonResponse(result, status=200 if result.get("ok") else 400)


@require_POST
def plan_pause_api(request, plan_id: str):
    return _json(mutate_plan(plan_id, "pause", "无法暂停计划"))


@require_POST
def plan_execute_api(request, plan_id: str):
    return _json(mutate_plan(plan_id, "run_now", "无法执行计划"))


@require_POST
def plan_delete_api(request, plan_id: str):
    return _json(mutate_plan(plan_id, "delete", "无法删除计划"))


@require_POST
def plan_supplement_api(request, plan_id: str):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}
    note = str(payload.get("note") or payload.get("text") or "").strip()
    return _json(mutate_plan(plan_id, "supplement", "无法补充说明", note=note))
