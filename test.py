"""
PlanService：计划清单（后台线程调度 + 可挂主 Agent 的专家子智能体）

典型流程：
    plans.register_tool(t)          # 可选 register_subagent(cfg)
    plans.build()
    agent = create_deep_agent(..., subagents=[plans.subagent_spec()])
    chat_stream(agent, llm, msg, config)
    plans.status() / plans.stop(...) / plans.keep_alive()
"""

from __future__ import annotations

import calendar
import threading
import time
import uuid
from datetime import datetime, timedelta

import requests
from deepagents import create_deep_agent
from langchain.messages import HumanMessage
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver, MemorySaver


class PlanService:

    """
    计划清单服务。方法按四类组织，调用示例：

    ---- 1. 能力注入 ----
        plans.register_tool(get_weather)
        plans.register_subagent({"name": "...", "description": "...",
                                 "system_prompt": "...", "tools": [...]})
        plans.build()                          # 注册完调用一次
        plans.build(with_manager=True)         # 额外建内置管理 Agent（少用）

    ---- 2. 子智能体初始化 ----
        plans.subagent_spec()
        plans.subagent_spec(name="x", description="...", system_prompt="...")
        create_deep_agent(..., subagents=[plans.subagent_spec()])
        plans.agent                            # 仅 with_manager=True 后可用

    ---- 3. 计划管理 ----
        plans.add("标题", "任务内容", "interval", interval_minutes=1, max_runs=3)
        plans.add("标题", "内容", "periodic", period="daily", hour=20, minute=0)
        plans.add("标题", "内容", "once", run_at="2026-07-30 08:00:00")
        plans.list_plans() / plans.list_plans(include_done=False)
        plans.get("a1b2c3d4")
        text = plans.show()
        rows = plans.status() / plans.status(plan_id="a1b2c3d4")
        plans.stop(plan_id="...") / plans.stop(title="...")
        n = plans.stop_all()

    ---- 4. 人机交互 ----
        info = plans.keep_alive()              # Ctrl+C → stop_all
        info = plans.keep_alive(until_idle=True)
    """

    ACTIVE = frozenset({"pending", "in_progress"})
    DONE = frozenset({"completed", "cancelled"})

    def __init__(self, llm):
        self.llm = llm
        self._tools: list = []
        self._subagents: list = []
        self._runner = None
        self._agent = None
        self._plans: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._checkpointer = InMemorySaver()

    # =========================================================================
    # 1. 能力注入：注册到点执行用的 tool / 子 Agent，并 build 执行器
    # =========================================================================

    def register_tool(self, t):
        self._tools.append(t)
        return t

    def register_subagent(self, cfg: dict):
        self._subagents.append(cfg)
        return cfg

    def build(self, *, with_manager: bool = False):
        self._runner = create_deep_agent(
            model=self.llm,
            tools=list(self._tools),
            subagents=list(self._subagents) or None,
            system_prompt=(
                "你是计划执行器。到点只完成本轮任务一次；用工具/子 Agent；"
                "勿讨论调度；简洁中文，勿编造数值。"
            ),
        )
        if with_manager:
            cfg = self.subagent_spec()
            self._agent = create_deep_agent(
                model=self.llm,
                tools=cfg["tools"],
                checkpointer=self._checkpointer,
                system_prompt=cfg["system_prompt"],
            )
        else:
            self._agent = None
        return self

    # =========================================================================
    # 2. 子智能体初始化：导出规格供主 Agent 挂载；可选内置管理 Agent
    # =========================================================================

    @property
    def agent(self):
        if self._agent is None:
            raise RuntimeError("请用 plans.subagent_spec() 挂主 Agent，或 build(with_manager=True)")
        return self._agent

    def subagent_spec(
        self,
        *,
        name: str = "scheduled_plan_agent",
        description: str | None = None,
        system_prompt: str | None = None,
    ) -> dict:
        return {
            "name": name,
            "description": description
            or "负责定时/预约计划的创建、查询与终止。提到定时任务或计划清单时调用。",
            "system_prompt": system_prompt
            or (
                f"你是计划调度专家。当前：{datetime.now().strftime('%Y-%m-%d %A %H:%M')}。\n"
                "按意图调用工具：\n"
                "- 创建 → create_scheduled_task"
                "（title/content；schedule_type=interval|periodic|once；"
                "interval→interval_minutes；periodic→period+hour/minute；once→run_at）\n"
                "- 查询 → list_scheduled_tasks\n"
                "- 终止 → stop_scheduled_task\n"
                "计划后台执行，建单后简短确认。"
            ),
            "tools": self._make_manage_tools(),
        }

    def _make_manage_tools(self):
        """子智能体工具：自然语言 → add / status / stop。"""
        svc = self

        @tool
        def create_scheduled_task(
            title: str,
            content: str,
            schedule_type: str,
            schedule_desc: str = "",
            interval_minutes: float = 0,
            period: str = "",
            weekday: int = -1,
            day_of_month: int = -1,
            hour: int = 0,
            minute: int = 0,
            run_at: str = "",
            max_runs: int = 0,
        ) -> str:
            """创建定时计划。schedule_type: interval|periodic|once。"""
            try:
                plan = svc.add(
                    title=title,
                    content=content,
                    schedule_type=schedule_type,
                    schedule_desc=schedule_desc,
                    interval_minutes=interval_minutes,
                    period=period,
                    weekday=weekday,
                    day_of_month=day_of_month,
                    hour=hour,
                    minute=minute,
                    run_at=run_at,
                    max_runs=max_runs,
                )
            except Exception as e:
                return f"创建失败：{e}"
            return (
                f"已启动 [{plan['id']}] {plan['title']} | "
                f"{plan.get('schedule_desc')} | {plan['content']}"
            )

        @tool
        def list_scheduled_tasks() -> str:
            """查询计划列表。"""
            rows = svc.status()
            if not rows:
                return "计划清单为空。"
            return svc._format_rows(rows, with_thread=True)

        @tool
        def stop_scheduled_task(plan_id: str = "", title: str = "") -> str:
            """终止计划（title 或 plan_id）。"""
            ok = svc.stop(plan_id=plan_id, title=title)
            key = plan_id or title
            if not ok:
                return f"未找到：{key}"
            return f"已终止：{key}\n{svc._format_rows(svc.status(), with_thread=True)}"

        return [create_scheduled_task, list_scheduled_tasks, stop_scheduled_task]

    # =========================================================================
    # 3. 计划管理：增删查、进程状态、后台调度内核
    # =========================================================================

    def add(
        self,
        title: str,
        content: str,
        schedule_type: str,
        *,
        interval_minutes: float = 0,
        period: str = "",
        weekday: int = -1,
        day_of_month: int = -1,
        hour: int = 0,
        minute: int = 0,
        run_at: str = "",
        max_runs: int = 0,
        schedule_desc: str = "",
    ) -> dict:
        st = (schedule_type or "").strip().lower()
        plan_id = uuid.uuid4().hex[:8]
        plan = {
            "id": plan_id,
            "title": title or "未命名计划",
            "content": content or title or "",
            "status": "pending",
            "schedule_type": st,
            "schedule": {
                "interval_minutes": interval_minutes,
                "period": period,
                "weekday": weekday,
                "day_of_month": day_of_month,
                "hour": hour,
                "minute": minute,
                "run_at": run_at,
            },
            "schedule_desc": schedule_desc or st,
            "max_runs": max_runs,
            "run_count": 0,
            "next_run": run_at if st == "once" and run_at else "-",
            "last_result": "",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "_stop": threading.Event(),
        }
        with self._lock:
            self._plans[plan_id] = plan
        th = threading.Thread(
            target=self._loop, args=(plan_id,), daemon=False, name=f"plan-{plan_id}"
        )
        plan["_thread"] = th
        th.start()
        return self._public(plan)

    def list_plans(self, include_done: bool = True) -> list[dict]:
        with self._lock:
            items = list(self._plans.values())
        if not include_done:
            items = [p for p in items if p["status"] in self.ACTIVE]
        return [self._public(p) for p in items]

    def get(self, plan_id: str) -> dict | None:
        with self._lock:
            p = self._plans.get(plan_id)
        return self._public(p) if p else None

    def show(self) -> str:
        """返回计划清单可读文本（不含线程信息）。"""
        return self._format_rows(self.list_plans())

    def status(self, plan_id: str = "") -> list[dict]:
        """返回计划进程数据（含 thread_alive）；不打印。"""
        with self._lock:
            raw = (
                [self._plans[plan_id]]
                if plan_id and plan_id in self._plans
                else list(self._plans.values())
            )
        rows = []
        for p in raw:
            th = p.get("_thread")
            rows.append({
                **self._public(p),
                "thread_name": th.name if th else None,
                "thread_alive": bool(th and th.is_alive()),
            })
        return rows

    def stop(self, plan_id: str = "", title: str = "") -> bool:
        if not plan_id and title:
            for p in self.list_plans():
                if title in p["title"] or title in p["content"]:
                    plan_id = p["id"]
                    break
        if not plan_id:
            return False
        with self._lock:
            p = self._plans.get(plan_id)
            if not p:
                return False
            p["_stop"].set()
            p["status"] = "cancelled"
            th = p.get("_thread")
        if th and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=2)
        return True

    def stop_all(self) -> int:
        return sum(1 for p in self.list_plans(include_done=False) if self.stop(plan_id=p["id"]))

    @staticmethod
    def _public(p: dict) -> dict:
        return {k: v for k, v in p.items() if not k.startswith("_")}

    @staticmethod
    def _format_rows(rows: list[dict], *, with_thread: bool = False) -> str:
        if not rows:
            return "（空）"
        lines = []
        for i, r in enumerate(rows, 1):
            line = (
                f"{i}. [{r['status']}] {r['title']} ({r['id']}) | "
                f"{r.get('schedule_desc') or r.get('schedule_type')} | "
                f"下次{r.get('next_run')} | 已执行{r.get('run_count', 0)} | "
                f"{r.get('content', '')}"
            )
            if with_thread:
                alive = "运行中" if r.get("thread_alive") else "已结束"
                line += f" | 线程={r.get('thread_name')}({alive})"
            lines.append(line)
        return "\n".join(lines)

    def _next_time(self, p: dict, after: datetime) -> datetime | None:
        st, s = p["schedule_type"], p["schedule"]
        try:
            if st == "interval":
                mins = float(s.get("interval_minutes") or 0)
                return after + timedelta(minutes=mins) if mins > 0 else None
            if st == "periodic":
                hour, minute = int(s.get("hour") or 0), int(s.get("minute") or 0)
                period = s.get("period") or ""
                if period == "daily":
                    t = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    return t if t > after else t + timedelta(days=1)
                if period == "weekly":
                    days = (int(s.get("weekday") or 0) - after.weekday()) % 7
                    t = (after + timedelta(days=days)).replace(
                        hour=hour, minute=minute, second=0, microsecond=0
                    )
                    return t if t > after else t + timedelta(days=7)
                if period == "monthly":
                    day = int(s.get("day_of_month") or 1)
                    y, m = after.year, after.month

                    def _at(yy, mm):
                        d = min(day, calendar.monthrange(yy, mm)[1])
                        return datetime(yy, mm, d, hour, minute)

                    t = _at(y, m)
                    if t <= after:
                        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
                        t = _at(y, m)
                    return t
            if st == "once":
                run_at = s.get("run_at") or ""
                if not run_at:
                    return None
                t = datetime.strptime(run_at, "%Y-%m-%d %H:%M:%S")
                return t if t > after else None
        except Exception:
            return None
        return None

    def _execute(self, p: dict) -> str:
        if self._runner is None:
            return "执行器未初始化，请先 plans.build()"
        result = self._runner.invoke({
            "messages": [HumanMessage(
                content=(
                    f"当前时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n"
                    f"计划标题：{p['title']}\n请完成：{p['content']}"
                )
            )]
        })
        for msg in reversed(result.get("messages", [])):
            if getattr(msg, "type", None) == "ai" and msg.content:
                return msg.content
        return ""

    def _loop(self, plan_id: str) -> None:
        with self._lock:
            p = self._plans.get(plan_id)
        if not p:
            return
        try:
            first = p["schedule_type"] == "interval"
            while not p["_stop"].is_set():
                if first:
                    first = False
                else:
                    nxt = self._next_time(p, datetime.now())
                    if nxt is None:
                        break
                    with self._lock:
                        p["next_run"] = nxt.strftime("%Y-%m-%d %H:%M:%S")
                        p["status"] = "pending"
                    wait = (nxt - datetime.now()).total_seconds()
                    if wait > 0 and p["_stop"].wait(wait):
                        break

                with self._lock:
                    if p["status"] == "cancelled":
                        break
                    p["status"] = "in_progress"
                    p["run_count"] += 1

                try:
                    answer = self._execute(p)
                    with self._lock:
                        p["last_result"] = answer
                except Exception as e:
                    with self._lock:
                        p["last_result"] = f"错误: {e}"

                done = p["schedule_type"] == "once" or (
                    p["max_runs"] > 0 and p["run_count"] >= p["max_runs"]
                )
                with self._lock:
                    if p["status"] == "cancelled":
                        break
                    p["status"] = "completed" if done else "pending"
                if done:
                    break
        finally:
            with self._lock:
                if p.get("status") not in self.DONE:
                    p["status"] = "cancelled" if p["_stop"].is_set() else "completed"
                p["_thread"] = None

    # =========================================================================
    # 4. 人机交互：脚本保活（Web/交互循环一般不需要）
    # =========================================================================

    def keep_alive(self, until_idle: bool = False) -> dict:
        """
        阻塞保活。结束时返回摘要 dict：
          {"reason": "idle"|"interrupt", "stopped": int, "plans": list}
        """
        try:
            while True:
                if until_idle and not any(
                    p["status"] in self.ACTIVE for p in self.list_plans()
                ):
                    return {"reason": "idle", "stopped": 0, "plans": self.status()}
                time.sleep(1)
        except KeyboardInterrupt:
            n = self.stop_all()
            return {"reason": "interrupt", "stopped": n, "plans": self.status()}


# =============================================================================
# 模型 + 计划服务
# =============================================================================

llm = ChatOpenAI(
    model="qwen3.5-plus",
    openai_api_key="sk-bvOWJIfQ8Y8IrZhzzpQZ80zSKHjWIHCkPXEgQ0H4Li5UCpUC",
    openai_api_base="http://ai.wenmodel.com/v1",
    temperature=0.7,
    max_tokens=1024,
)

plans = PlanService(llm)

# =============================================================================
# 【你自己写 @tool / 子 Agent，并挂载到 plans（到点执行用）】
# =============================================================================

@tool
def get_weather(longitude: str, latitude: str):
    '''
    功能:使用Open-Meteo查询指定坐标的天气情况。
    Args:
        longitude:经度，例如 "52.520011"
        latitude:纬度，例如 "13.410004"
    return:
        指定坐标未来逐小时的温度和相对湿度摘要。
    '''
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "temperature_2m,relative_humidity_2m",
        "timezone": "auto",
        "forecast_days": 3
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data
    except requests.exceptions.RequestException as e:
        return f"天气接口请求失败：{str(e)}"

plans.register_tool(get_weather)
plans.build()

weather_agent = {
    "name": "weather_agent",
    "description": "查询指定坐标天气，并总结温度、湿度变化趋势",
    "system_prompt": "你是一个气象专家，必须先调用 get_weather 获取数据，再用简洁中文总结温度、湿度范围。禁止编造数值。",
    "tools": [get_weather],
}
plans.register_subagent(weather_agent)
plans.build()  


# =============================================================================
# 主智能体（自行创建；计划能力通过 subagent_spec 挂入）
# =============================================================================

agent = create_deep_agent(
    model=llm,
    system_prompt='你是一名柑橘产业农业智能助手',
    subagents=[plans.subagent_spec()],
    checkpointer=MemorySaver()
)


# =============================================================================
# 消息构造（仿记忆 notebook）
# =============================================================================

import base64


def encode_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def build_msg(text=None, image_path=None):
    content = []
    if text:
        content.append({
            "type": "text",
            "text": text
        })

    if image_path:
        image_data = encode_image(image_path)

        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{image_data}",
                "detail": "auto"
            }
        })

    return [
        HumanMessage(content=content)
    ]


# =============================================================================
# chat_stream
# =============================================================================


def chat_stream(agent, model, message, config=None, subgraphs=True):
    messages = message if isinstance(message, list) else [message]
    content = messages[0].content
    has_image = isinstance(content, list) and any(x.get("type") == "image_url" for x in content)
    text = "".join(x.get("text", "") for x in content if x.get("type") == "text") if isinstance(content, list) else content

    # 处理图像
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

    # 处理文本
    for item in agent.stream({"messages": messages}, stream_mode="updates", config=config, subgraphs=subgraphs):
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


if __name__ == "__main__":
    config = {"configurable": {"thread_id": "demo-001"}}

    # ---- 自然语言经主 Agent 建单 / 查询 ----
    print("--------------------------------")
    text = "启动定时任务，每2分钟计算1+1结果"
    chat_stream(agent, llm, build_msg(text=text), config)

    print("--------------------------------")
    text = "启动定时任务，每1分钟查询武汉天气情况"
    chat_stream(agent, llm, build_msg(text=text), config)

    print("------------你是谁---------------")
    text = "你是谁"
    chat_stream(agent, llm, build_msg(text=text), config)

    print("------------查询计划列表---------------")
    text = "查询计划列表"
    chat_stream(agent, llm, build_msg(text=text), config)

    print("------------查询计划列表完成---------------")
    print("list_plans():", plans.list_plans())
    

    print("show:\n", plans.show())
    print("status():", plans.status())




    # print(plans.keep_alive(until_idle=True))
    print(plans.keep_alive())

