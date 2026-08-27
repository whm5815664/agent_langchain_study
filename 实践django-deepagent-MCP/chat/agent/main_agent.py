"""主 Agent：基于 deepagents，可挂载 Edge MCP 工具。"""

from __future__ import annotations

from typing import Any, Sequence

from django.conf import settings
from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI

AGENT_ID = "main_agent"
AGENT_DISPLAY_NAME = "主智能体（Cloud Agent）"

SYSTEM_PROMPT = (
    "你是运行在 Django 云端的 Cloud Agent（中文助手）。"
    "当前对话绑定了访客浏览器上的 Edge MCP Server；"
    "若已提供 Edge 工具，请在需要本机信息或本机操作时主动调用这些工具"
    "（例如本机时间、设备信息、剪贴板、定位、通知、弹窗确认、localStorage、下载文本等）。"
    "工具结果来自访客那台电脑，不要编造本机数据。"
    "Edge 未连接或工具失败时，如实说明并继续用常识回答。"
    "用简洁清晰的中文回复。"
)

llm = ChatOpenAI(
    model=settings.LLM_MODEL,
    openai_api_key=settings.LLM_API_KEY,
    openai_api_base=settings.LLM_BASE_URL,
    temperature=settings.LLM_TEMPERATURE,
    max_tokens=4096,
    stream_usage=True,
)


def build_agent(tools: Sequence[Any] | None = None):
    kwargs: dict[str, Any] = {
        "model": llm,
        "system_prompt": SYSTEM_PROMPT,
        "name": AGENT_ID,
    }
    if tools:
        kwargs["tools"] = list(tools)
    return create_deep_agent(**kwargs)


# 无 Edge 工具时的默认实例（兼容旧调用）
main_agent = build_agent()
