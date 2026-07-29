"""
基于报告模板生成内容（子智能体 + 模板工具，不用中间件）。
"""

from __future__ import annotations

import requests
from deepagents import create_deep_agent
from langchain.messages import HumanMessage
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

# ---------------------------------------------------------------------------
# 1. 大模型
# ---------------------------------------------------------------------------

llm = ChatOpenAI(
    model="qwen3.5-plus",
    openai_api_key="sk-bvOWJIfQ8Y8IrZhzzpQZ80zSKHjWIHCkPXEgQ0H4Li5UCpUC",
    openai_api_base="http://ai.wenmodel.com/v1",
    temperature=0.3,
    max_tokens=4096,
    max_retries=5,
    timeout=120,
)

# ---------------------------------------------------------------------------
# 2. 工具
# ---------------------------------------------------------------------------


@tool
def get_weather(longitude: str, latitude: str):
    """
    功能:使用Open-Meteo查询指定坐标的天气情况。
    Args:
        longitude:经度，例如 "110.95745"
        latitude:纬度，例如 "30.82601"
    return:
        指定坐标未来逐小时的温度和相对湿度摘要。
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "temperature_2m,relative_humidity_2m",
        "timezone": "auto",
        "forecast_days": 3,
    }
    response = requests.get(url, params=params, timeout=10)
    return response.json()


@tool
def generate_harvest_report(
    variety: str,
    location: str,
    forecast_period: str,
    weather_summary: str,
    harvest_analysis: str,
    management_advice: str,
):
    """
    功能:
        根据农业生产技术报告格式生成柑橘采摘时间分析报告。
        只能填写各字段内容，不得改动报告章节结构与固定表述。
    Args:
        variety:柑橘品种名称
        location:果园区域或产地
        forecast_period:天气分析时间范围
        weather_summary:基于 get_weather 返回数据的气象条件分析
        harvest_analysis:采摘适宜性评价和建议采摘时间
        management_advice:采摘期间生产管理建议
    Returns:
        Markdown格式农业生产指导报告
    """
    report = f"""
# {variety}采摘期气象分析与生产建议报告


## 一、基本信息

| 项目 | 内容 |
|---|---|
| 作物 | 柑橘 |
| 品种 | {variety} |
| 生产区域 | {location} |
| 分析周期 | {forecast_period} |


## 二、气象条件分析
{forecast_period}气象条件如下：
{weather_summary}


## 三、采摘适宜性评价
{harvest_analysis}


## 四、生产管理建议
{management_advice}


## 五、结论
基于当前气象条件和果园生产需求，
{variety}建议在适宜天气窗口内开展采摘作业，
以保障果实品质和采后储运效果。

"""
    print("-------报告开始-------\n")
    print(report)
    print("-------报告结束-------\n")
    return report


# ---------------------------------------------------------------------------
# 3. 子智能体
# ---------------------------------------------------------------------------

weather_agent = {
    "name": "weather_agent",
    "description": "查询指定坐标天气，并总结温度、湿度变化趋势。",
    "system_prompt": (
        "你是气象专家。必须先调用 get_weather 获取数据，"
        "再用简洁中文总结温度、湿度范围。禁止编造数值。"
    ),
    "tools": [get_weather],
}

citrus_workflow_agent = {
    "name": "citrus_workflow_agent",
    "description": "柑橘采摘分析：必须先 get_weather，再 generate_harvest_report 按模板输出。",
    "system_prompt": """
你是一名柑橘采摘分析流程专家。

执行步骤：
1. 先调用 get_weather 获取天气。
2. 仅根据 get_weather 返回的真实数据做分析，禁止编造气温/湿度。
3. 最后调用 generate_harvest_report，只填字段。
4. 把 generate_harvest_report 的返回内容原样作为最终答案，禁止改写。
""",
    "tools": [get_weather, generate_harvest_report],
}

# ---------------------------------------------------------------------------
# 4. 主调度 Agent
# ---------------------------------------------------------------------------

checkpointer = MemorySaver()

agent = create_deep_agent(
    model=llm,
    system_prompt=(
        "你是一名柑橘产业农业智能助手。"
        "采摘/气象类任务：直接调度 citrus_workflow_agent。"
        "收到子 Agent 返回的模板报告后，必须原样输出，禁止改写。"
    ),
    subagents=[weather_agent, citrus_workflow_agent],
    checkpointer=checkpointer,
)

# ---------------------------------------------------------------------------
# 5. 通用工具函数（与业务无关）
# ---------------------------------------------------------------------------


def build_msg(text=None, image_path=None):
    """通用消息构造。image_path 预留，当前仅拼接文本。"""
    content = []
    if text:
        content.append({"type": "text", "text": text})
    return [HumanMessage(content=content or [{"type": "text", "text": ""}])]


def chat_stream(agent, model, message, config=None, subgraphs=True):
    messages = message if isinstance(message, list) else [message]
    content = messages[0].content
    has_image = isinstance(content, list) and any(x.get("type") == "image_url" for x in content)
    text = "".join(x.get("text", "") for x in content if x.get("type") == "text") if isinstance(content, list) else content

    if has_image and text:
        answer = ""
        for chunk in model.stream(messages):
            if chunk.content:
                answer += chunk.content
                print(chunk.content, end="", flush=True)
        print()

        if config:
            agent.update_state(config, {"messages": [
                {"role": "user", "content": text},
                {"role": "assistant", "content": answer}
            ]})
        return answer

    for item in agent.stream(
        {"messages": messages},
        stream_mode="updates",
        config=config,
        subgraphs=subgraphs,
    ):
        # subgraphs=True 时返回 (namespace, updates)
        if isinstance(item, tuple):
            namespace, chunk = item
        else:
            namespace, chunk = (), item

        ns = "主Agent" if not namespace else " > ".join(str(x) for x in namespace)

        for update in chunk.values():
            for msg in (update or {}).get("messages", []):
                # system提示词
                if msg.type == "system":
                   print(f"[{ns}] [{msg.type}] {msg.content}")

                # user文本内容
                if msg.type == "human":
                   print(f"[{ns}] [{msg.type}] {msg.content}")

                # ai回复
                if msg.type == "ai":
                    if msg.content:
                        print(f"[{ns}] [{msg.type}] {msg.content}")

                    if hasattr(msg, 'tool_calls') and msg.tool_calls:
                        calls = [tc['name'] for tc in msg.tool_calls]
                        print(f"[{ns}] 请求调用: {calls}")

                # tool工具回复
                if msg.type == "tool":
                    print(f"[{ns}] [调用工具 {msg.name}]")
                    print(f"[{ns}] [{msg.type}] {msg.content}")

                print('-------------------------------')


# ---------------------------------------------------------------------------
# 6. 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time

    config = {"configurable": {"thread_id": f"harvest-{int(time.time())}"}}
    msg = build_msg(text="查询秭归县近期的天气，制定夏橙的采收计划")
    chat_stream(agent, llm, msg, config)
