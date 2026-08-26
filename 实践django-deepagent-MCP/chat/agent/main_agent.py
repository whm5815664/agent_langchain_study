"""主 Agent：基于 deepagents 的简单问答助手。"""

from django.conf import settings
from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI

AGENT_ID = "main_agent"
AGENT_DISPLAY_NAME = "主智能体"

llm = ChatOpenAI(
    model=settings.LLM_MODEL,
    openai_api_key=settings.LLM_API_KEY,
    openai_api_base=settings.LLM_BASE_URL,
    temperature=settings.LLM_TEMPERATURE,
    max_tokens=4096,
    stream_usage=True,
)

main_agent = create_deep_agent(
    model=llm,
    system_prompt=(
        "你是一个友好的中文智能助手，负责回答用户问题。"
        "用简洁清晰的中文回复；不确定时如实说明，不要编造事实。"
    ),
    name=AGENT_ID,
)
