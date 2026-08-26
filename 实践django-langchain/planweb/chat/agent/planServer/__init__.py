"""计划服务单例、子智能体规格与 HTTP 业务接口。"""

from __future__ import annotations

import threading
from typing import Any

from chat.agent.planServer.planserver_subagent import PlanService

_lock = threading.Lock()
_plan_service: PlanService | None = None

PLAN_SUBAGENT_NAME = "scheduled_plan_agent"
PLAN_SUBAGENT_DISPLAY_NAME = "计划智能体"


def get_plan_service() -> PlanService:
    """进程内单例：保证所有计划线程共享同一清单。"""
    global _plan_service
    if _plan_service is not None:
        return _plan_service
    with _lock:
        if _plan_service is not None:
            return _plan_service
        from chat.agent.main_agent import llm
        from chat.agent.planServer.capabilities import apply_capabilities

        svc = PlanService(llm)
        apply_capabilities(svc)
        svc.build()
        _plan_service = svc
        return _plan_service


def get_plan_subagent_spec() -> dict:
    """供主 Agent 挂载的计划子智能体规格。"""
    return get_plan_service().subagent_spec(
        name=PLAN_SUBAGENT_NAME,
        display_name=PLAN_SUBAGENT_DISPLAY_NAME,
    )


def list_plans_payload() -> dict[str, Any]:
    return {"ok": True, "plans": get_plan_service().status()}


def mutate_plan(plan_id: str, method: str, err: str, *, note: str | None = None) -> dict[str, Any]:
    """统一执行计划变更：pause / run_now / delete / supplement。"""
    svc = get_plan_service()
    if method == "supplement":
        note = (note or "").strip()
        if not note:
            return {"ok": False, "error": "补充说明不能为空"}
        ok = svc.supplement(plan_id=plan_id, note=note)
    else:
        ok = getattr(svc, method)(plan_id=plan_id)
    if not ok:
        return {"ok": False, "error": f"{err}：{plan_id}"}
    return {"ok": True, "plans": svc.status()}