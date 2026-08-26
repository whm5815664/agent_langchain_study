"""项目实际启用的智能体登记。"""

from __future__ import annotations

from chat.agent.main_agent import AGENT_DISPLAY_NAME, AGENT_ID
from chat.agent.planServer import (
    PLAN_SUBAGENT_DISPLAY_NAME,
    PLAN_SUBAGENT_NAME,
)
from chat.agent.planServer.planserver_subagent import EXECUTOR_ID, EXECUTOR_NAME
from chat.agent.tool.database import DATABASE_SUBAGENT

SUBAGENTS = (DATABASE_SUBAGENT,)
SUBAGENT_IDS = frozenset(
    {spec["name"] for spec in SUBAGENTS} | {PLAN_SUBAGENT_NAME, EXECUTOR_ID}
)

_NAMES = {
    AGENT_ID: AGENT_DISPLAY_NAME,
    PLAN_SUBAGENT_NAME: PLAN_SUBAGENT_DISPLAY_NAME,
    EXECUTOR_ID: EXECUTOR_NAME,
    **{spec["name"]: spec.get("display_name") or spec["name"] for spec in SUBAGENTS},
}


def resolve_agent_name(agent_id: str | None) -> str:
    if not agent_id:
        return AGENT_DISPLAY_NAME
    return _NAMES.get(agent_id, agent_id)


def is_subagent_id(agent_id: str | None) -> bool:
    return bool(agent_id) and agent_id in SUBAGENT_IDS
