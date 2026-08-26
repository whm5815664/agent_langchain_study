"""主 Agent：意图理解与任务分派（具体执行交给子智能体）。"""

from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI

from chat.agent.planServer import get_plan_subagent_spec
from chat.agent.tool.camera import capture_camera
from chat.agent.tool.database import DATABASE_SUBAGENT
from chat.agent.tool.weather import get_weather

AGENT_ID = "planweb_main_agent"
AGENT_DISPLAY_NAME = "主智能体"

llm = ChatOpenAI(
    model="qwen3.5-plus",  # 模型名称
    openai_api_key="sk-bvOWJIfQ8Y8IrZhzzpQZ80zSKHjWIHCkPXEgQ0H4Li5UCpUC",  # API Key
    openai_api_base="http://ai.wenmodel.com/v1",  # API 地址
    temperature=0.7,
    max_tokens=4096,
    stream_usage=True,
)

main_agent = create_deep_agent(
    model=llm,
    tools=[get_weather, capture_camera],
    subagents=[DATABASE_SUBAGENT, get_plan_subagent_spec()],
    system_prompt=(
        "你是柑橘采后生产主助手，负责意图理解与任务分派。"
        "用中文简洁回复；信息不足先追问，勿编造数据或计划状态。"
        "路由（须用 task，subagent_type 用 name）："
        "1. database"
        "   - 意图：环境数据（温湿度/气体）、库内监控图；"
        "   - 分派：description 写清库号、时间范围等已给条件；"
        "2. scheduled_plan_agent"
        "   - 意图：创建/查询/暂停/执行/终止定时或预约计划；"
        "   - 分派：description 写清标题、时间、动作等已给信息；"
        "可直接处理：闲聊、看图问答；"
        "用户上传图会暂存为 /media/temp/tempN.jpg，后续轮次可按该路径引用（刷新会话后清除）。"
    ),
    name=AGENT_ID,
)
