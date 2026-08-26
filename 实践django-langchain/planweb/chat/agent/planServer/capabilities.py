"""
计划执行器能力注册表。

从 chat.agent.tool 选择要挂到 PlanService 的 tool / 子智能体。
get_plan_service() 构建时会调用 apply_capabilities(plans)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chat.agent.tool.camera import capture_camera
from chat.agent.tool.database import DATABASE_SUBAGENT
from chat.agent.tool.weather import get_weather

if TYPE_CHECKING:
    from chat.agent.planServer.planserver_subagent import PlanService

PLAN_TOOLS = [
    get_weather,
    capture_camera,
]

PLAN_SUBAGENTS = [
    DATABASE_SUBAGENT,
]


def apply_capabilities(plans: "PlanService") -> None:
    """把本文件中的 tool / 子智能体注册到 PlanService（build 之前调用）。"""

    for t in PLAN_TOOLS:
        plans.register_tool(t)
    for cfg in PLAN_SUBAGENTS:
        plans.register_subagent(cfg)
