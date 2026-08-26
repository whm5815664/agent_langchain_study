"""计划清单服务：每条计划独立线程调度，直到用户终止。"""

from __future__ import annotations

import calendar
import threading
import time
import uuid
from datetime import datetime, timedelta

from deepagents import create_deep_agent
from langchain.tools import tool
from langchain_core.messages import HumanMessage

EXECUTOR_ID = "plan_executor"
EXECUTOR_NAME = "计划执行器"


class PlanService:
    """
    计划清单服务。

    能力注入见 capabilities.py（PLAN_TOOLS / PLAN_SUBAGENTS）。
    主 Agent 挂载：create_deep_agent(..., subagents=[plans.subagent_spec()])
    """

    DONE = frozenset({"completed", "cancelled"})

    def __init__(self, llm):
        self.llm = llm
        self._tools: list = []
        self._subagents: list = []
        self._runner = None
        self._plans: dict[str, dict] = {}
        self._lock = threading.Lock()

    # =========================================================================
    # 1. 能力注入
    # =========================================================================

    def register_tool(self, t):
        self._tools.append(t)

    def register_subagent(self, cfg: dict):
        name = (cfg or {}).get("name")
        if name:
            self._subagents = [s for s in self._subagents if s.get("name") != name]
        self._subagents.append(cfg)

    def build(self):
        self._runner = create_deep_agent(
            model=self.llm,
            tools=list(self._tools),
            subagents=list(self._subagents) or None,
            system_prompt=(
                "你是计划执行器。到点只完成本轮任务一次；用工具/子 Agent；"
                "勿讨论调度；简洁中文，勿编造数值。"
            ),
            name=EXECUTOR_ID,
        )
        return self

    # =========================================================================
    # 2. 子智能体初始化
    # =========================================================================

    def subagent_spec(
        self,
        *,
        name: str = "scheduled_plan_agent",
        display_name: str = "计划智能体",
    ) -> dict:
        return {
            "name": name,
            "display_name": display_name,
            "description": (
                "负责定时/预约计划的创建、查询、暂停、执行与终止。"
                "提到定时任务或计划清单时调用。"
            ),
            "system_prompt": (
                f"你是计划调度专家。当前：{datetime.now().strftime('%Y-%m-%d %A %H:%M')}。\n"
                "按意图调用工具：\n"
                "- 创建 → create_scheduled_task"
                "（title/content；schedule_type=interval|periodic|once；"
                "interval→interval_minutes；periodic→period+hour/minute；"
                "once→run_at，格式 YYYY-MM-DD HH:MM:SS，如 2026-07-30 16:20:00）\n"
                "- 查询 → list_scheduled_tasks\n"
                "- 暂停 → pause_scheduled_task\n"
                "- 执行/恢复 → run_scheduled_task\n"
                "- 终止/删除 → stop_scheduled_task\n"
                "计划后台独立线程执行，建单后简短确认。"
            ),
            "tools": self._make_manage_tools(),
        }

    def _make_manage_tools(self):
        """子智能体工具：自然语言 → add / status / pause / run / stop。"""
        svc = self

        def _mutate_result(ok: bool, key: str, fail_msg: str, ok_msg: str) -> str:
            if not ok:
                return f"{fail_msg}：{key}"
            return f"{ok_msg}：{key}\n{svc._format_rows(svc.status())}"

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
            """创建定时计划。schedule_type: interval|periodic|once。
            once 时 run_at 用 YYYY-MM-DD HH:MM:SS（也可 YYYY-MM-DD HH:MM）。"""
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
            return svc._format_rows(rows)

        @tool
        def pause_scheduled_task(plan_id: str = "", title: str = "") -> str:
            """暂停计划（title 或 plan_id）。"""
            key = plan_id or title
            return _mutate_result(
                svc.pause(plan_id=plan_id, title=title),
                key,
                "未找到或无法暂停",
                "已暂停",
            )

        @tool
        def run_scheduled_task(plan_id: str = "", title: str = "") -> str:
            """执行/恢复计划（title 或 plan_id）。"""
            key = plan_id or title
            pid = svc._resolve_id(plan_id=plan_id, title=title)
            if not pid:
                return f"未找到或无法执行：{key}"
            with svc._lock:
                p = svc._plans.get(pid)
                if p:
                    p["_run_finished"].clear()
            return _mutate_result(
                svc.run_now(plan_id=pid),
                key,
                "未找到或无法执行",
                "已执行/恢复",
            )

        @tool
        def stop_scheduled_task(plan_id: str = "", title: str = "") -> str:
            """终止并删除计划（title 或 plan_id）。"""
            key = plan_id or title
            return _mutate_result(
                svc.delete(plan_id=plan_id, title=title),
                key,
                "未找到",
                "已删除",
            )

        return [
            create_scheduled_task,
            list_scheduled_tasks,
            pause_scheduled_task,
            run_scheduled_task,
            stop_scheduled_task,
        ]

    # =========================================================================
    # 3. 计划管理
    # =========================================================================

    def _resolve_id(self, plan_id: str = "", title: str = "") -> str:
        plan_id = (plan_id or "").strip()
        title = (title or "").strip()
        if plan_id:
            return plan_id
        if not title:
            return ""
        with self._lock:
            items = list(self._plans.values())
        for p in items:
            if title in p["title"] or title in p["content"]:
                return p["id"]
        return ""

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
        if st not in {"interval", "periodic", "once"}:
            raise ValueError("schedule_type 须为 interval|periodic|once")
        if st == "interval" and float(interval_minutes or 0) <= 0:
            raise ValueError("interval 类型须指定 interval_minutes > 0")
        if st == "periodic" and (period or "").strip() not in {
            "daily",
            "weekly",
            "monthly",
        }:
            raise ValueError("periodic 类型须指定 period=daily|weekly|monthly")

        # once：规范化 run_at；缺省时用 hour/minute 拼今天的预约时间
        if st == "once":
            run_at = self._normalize_run_at(
                run_at, hour=hour, minute=minute
            )

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
            "schedule_desc": schedule_desc or self._default_desc(
                st,
                interval_minutes=interval_minutes,
                period=period,
                weekday=weekday,
                day_of_month=day_of_month,
                hour=hour,
                minute=minute,
                run_at=run_at,
            ),
            "max_runs": max_runs,
            "run_count": 0,
            "next_run": run_at if st == "once" and run_at else "-",
            "last_result": "",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "_stop": threading.Event(),
            "_paused": threading.Event(),
            "_run_now": threading.Event(),
            "_run_finished": threading.Event(),
            "_last_trace": [],
            "_pending_note": "",
        }
        with self._lock:
            self._plans[plan_id] = plan
        self._start_thread(plan)
        return self._public(plan)

    @staticmethod
    def _default_desc(
        st: str,
        *,
        interval_minutes: float = 0,
        period: str = "",
        weekday: int = -1,
        day_of_month: int = -1,
        hour: int = 0,
        minute: int = 0,
        run_at: str = "",
    ) -> str:
        if st == "interval":
            return f"每 {interval_minutes} 分钟"
        if st == "periodic":
            base = {"daily": "每天", "weekly": "每周", "monthly": "每月"}.get(
                period, period or "周期"
            )
            extra = ""
            if period == "weekly" and weekday >= 0:
                extra = f"周{weekday}"
            elif period == "monthly" and day_of_month > 0:
                extra = f"{day_of_month}日"
            return f"{base}{extra} {hour:02d}:{minute:02d}"
        if st == "once":
            return f"一次 {run_at}" if run_at else "一次"
        return st

    def _start_thread(self, plan: dict) -> None:
        th = threading.Thread(
            target=self._loop,
            args=(plan["id"],),
            daemon=True,
            name=f"plan-{plan['id']}",
        )
        plan["_thread"] = th
        th.start()

    def status(self) -> list[dict]:
        with self._lock:
            raw = list(self._plans.values())
        rows = []
        for p in raw:
            th = p.get("_thread")
            rows.append(
                {
                    **self._public(p),
                    "thread_name": th.name if th else None,
                    "thread_alive": bool(th and th.is_alive()),
                    "paused": bool(p.get("_paused") and p["_paused"].is_set()),
                }
            )
        return rows

    def pause(self, plan_id: str = "", title: str = "") -> bool:
        pid = self._resolve_id(plan_id, title)
        if not pid:
            return False
        with self._lock:
            p = self._plans.get(pid)
            if not p or p["status"] in self.DONE:
                return False
            p["_paused"].set()
            p["_run_now"].clear()
            if p["status"] != "in_progress":
                p["status"] = "paused"
        return True

    def run_now(self, plan_id: str = "", title: str = "") -> bool:
        """立即触发一轮执行（若已结束则重启线程）。"""
        pid = self._resolve_id(plan_id, title)
        if not pid:
            return False
        with self._lock:
            p = self._plans.get(pid)
            if not p:
                return False
            if p["status"] in self.DONE:
                return self._restart_locked(p)
            p["_paused"].clear()
            if p["status"] == "paused":
                p["status"] = "pending"
            p["_run_now"].set()
            th = p.get("_thread")
            if not th or not th.is_alive():
                p["_stop"].clear()
                self._start_thread(p)
        return True

    def supplement(self, plan_id: str = "", note: str = "", title: str = "") -> bool:
        """用户补充文字说明：写入任务内容并尽快触发下一轮执行。"""
        note = (note or "").strip()
        if not note:
            return False
        pid = self._resolve_id(plan_id, title)
        if not pid:
            return False
        with self._lock:
            p = self._plans.get(pid)
            if not p or p["status"] == "cancelled":
                return False
            base = (p.get("content") or "").rstrip()
            p["content"] = f"{base}\n\n用户补充：{note}" if base else f"用户补充：{note}"
            p["_pending_note"] = note
            if p["status"] in self.DONE:
                self._restart_locked(p, keep_result=True)
                p["_pending_note"] = note
                return True
            p["_paused"].clear()
            if p["status"] == "paused":
                p["status"] = "pending"
            p["_run_now"].set()
            th = p.get("_thread")
            if not th or not th.is_alive():
                p["_stop"].clear()
                self._start_thread(p)
        return True

    def _restart_locked(self, p: dict, *, keep_result: bool = False) -> bool:
        """在持有锁时重启已结束计划并立即执行一轮。"""
        pending = p.get("_pending_note") or ""
        last = (p.get("last_result") or "") if keep_result else ""
        content = p.get("content") or ""
        p["_stop"] = threading.Event()
        p["_paused"] = threading.Event()
        p["_run_now"] = threading.Event()
        p["_run_finished"] = threading.Event()
        p["_last_trace"] = []
        p["_pending_note"] = pending
        p["_run_now"].set()
        p["status"] = "pending"
        p["last_result"] = last
        p["content"] = content
        self._start_thread(p)
        return True

    def stop(self, plan_id: str = "", title: str = "") -> bool:
        pid = self._resolve_id(plan_id, title)
        if not pid:
            return False
        with self._lock:
            p = self._plans.get(pid)
            if not p:
                return False
            p["_stop"].set()
            p["_paused"].clear()
            p["_run_now"].set()  # 唤醒等待中的循环以便退出
            p["status"] = "cancelled"
            th = p.get("_thread")
        if th and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=2)
        return True

    def delete(self, plan_id: str = "", title: str = "") -> bool:
        pid = self._resolve_id(plan_id, title)
        if not pid:
            return False
        if not self.stop(plan_id=pid):
            with self._lock:
                if pid not in self._plans:
                    return False
        with self._lock:
            self._plans.pop(pid, None)
        return True

    @staticmethod
    def _public(p: dict) -> dict:
        return {k: v for k, v in p.items() if not k.startswith("_")}

    @staticmethod
    def _format_rows(rows: list[dict]) -> str:
        if not rows:
            return "（空）"
        lines = []
        for i, r in enumerate(rows, 1):
            alive = "运行中" if r.get("thread_alive") else "已结束"
            lines.append(
                f"{i}. [{r['status']}] {r['title']} ({r['id']}) | "
                f"{r.get('schedule_desc') or r.get('schedule_type')} | "
                f"下次{r.get('next_run')} | 已执行{r.get('run_count', 0)} | "
                f"{r.get('content', '')} | "
                f"线程={r.get('thread_name')}({alive})"
            )
        return "\n".join(lines)

    @staticmethod
    def _parse_run_at(run_at: str) -> datetime | None:
        """兼容常见 run_at 写法（缺秒、T 分隔、斜杠日期等）。"""
        s = (run_at or "").strip()
        if not s:
            return None
        s = s.replace("T", " ").replace("/", "-")
        # 去掉尾部毫秒
        if "." in s and s.count(":") >= 2:
            s = s.split(".", 1)[0]
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    def _normalize_run_at(
        self,
        run_at: str = "",
        *,
        hour: int = 0,
        minute: int = 0,
    ) -> str:
        """once 类型：解析并规范为 YYYY-MM-DD HH:MM:SS。"""
        raw = (run_at or "").strip()
        if raw:
            t = self._parse_run_at(raw)
            if t is None:
                raise ValueError(
                    f"run_at 格式无效：{raw}，请用 YYYY-MM-DD HH:MM:SS"
                )
            return t.strftime("%Y-%m-%d %H:%M:%S")
        # 未给 run_at 时，用 hour/minute 拼最近一次预约点
        if int(hour or 0) or int(minute or 0):
            now = datetime.now()
            t = now.replace(
                hour=int(hour or 0),
                minute=int(minute or 0),
                second=0,
                microsecond=0,
            )
            if t <= now:
                t += timedelta(days=1)
            return t.strftime("%Y-%m-%d %H:%M:%S")
        return ""

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
                run_at = (s.get("run_at") or "").strip()
                # 未指定或已过期：立即执行一轮（由调用方按 once 结束）
                if not run_at:
                    return after
                t = self._parse_run_at(run_at)
                if t is None:
                    return None
                return t if t > after else after
        except Exception:
            return None
        return None

    def is_run_finished(self, plan_id: str) -> bool:
        with self._lock:
            p = self._plans.get(plan_id)
        return bool(p and p.get("_run_finished") and p["_run_finished"].is_set())

    def snapshot_trace(self, plan_id: str) -> list[dict]:
        """只读当前执行轨迹（不清空，供流式拉取）。"""
        with self._lock:
            p = self._plans.get(plan_id)
            if not p:
                return []
            return list(p.get("_last_trace") or [])

    def take_trace(self, plan_id: str) -> list[dict]:
        """取出并清空最近一轮执行轨迹。"""
        with self._lock:
            p = self._plans.get(plan_id)
            if not p:
                return []
            trace = list(p.get("_last_trace") or [])
            p["_last_trace"] = []
            return trace

    def _execute(self, p: dict) -> str:
        if self._runner is None:
            return "执行器未初始化，请先 plans.build()"

        import asyncio
        import json
        import re

        # 懒导入避免与 chat_service → main_agent → planServer 循环依赖
        from chat.agent.chat_service import (
            _HARNESS_TOOLS,
            _extract_reasoning,
            _extract_tool_image_urls,
            _parse_tool_input,
        )

        think_re = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)
        known = {EXECUTOR_ID, *(str(s.get("name") or "") for s in self._subagents)}
        names = {
            EXECUTOR_ID: EXECUTOR_NAME,
            **{
                str(s.get("name")): str(s.get("display_name") or s.get("name"))
                for s in self._subagents
                if s.get("name")
            },
        }
        trace: list[dict] = []
        emitted: set[str] = set()
        pending: dict[str, dict] = {}
        run_agent: dict[str, str] = {}

        def push(ev: dict) -> None:
            trace.append(ev)
            with self._lock:
                p.setdefault("_last_trace", []).append(ev)

        with self._lock:
            p["_last_trace"] = []

        def agent_of(event: dict) -> tuple[str, str]:
            meta = event.get("metadata") or {}
            ns = str(meta.get("langgraph_checkpoint_ns") or "")
            for part in reversed(ns.split("|")):
                sid = part.strip().split(":", 1)[0]
                if sid in known:
                    return sid, names.get(sid, sid)
            name = str(
                meta.get("lc_agent_name")
                or meta.get("langgraph_node")
                or event.get("name")
                or ""
            ).strip()
            if name in known:
                return name, names.get(name, name)
            return EXECUTOR_ID, EXECUTOR_NAME

        def ensure(aid: str, parent: str | None = None):
            if aid in emitted:
                return
            emitted.add(aid)
            if parent is None:
                parent = EXECUTOR_ID if aid != EXECUTOR_ID else None
            push(
                {
                    "agent": {
                        "id": aid,
                        "name": names.get(aid, aid),
                        "parent_id": parent,
                    }
                }
            )

        ensure(EXECUTOR_ID, None)

        async def _collect():
            with self._lock:
                note = (p.pop("_pending_note", None) or "").strip()
                title = p.get("title") or ""
                content = p.get("content") or ""
                prev = (p.get("last_result") or "").strip() if note else ""

            parts = [
                f"当前时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
                f"计划标题：{title}",
                f"请完成：{content}",
            ]
            if note:
                if prev:
                    parts.append(f"你上一轮的输出：\n{prev[:3000]}")
                parts.append(
                    f"用户补充说明：\n{note}\n"
                    "请根据补充说明继续完成任务，不要再重复索要已提供的信息。"
                )

            payload = {
                "messages": [HumanMessage(content="\n".join(parts))]
            }
            final = ""
            async for event in self._runner.astream_events(payload, version="v2"):
                kind = event.get("event") or ""
                run_id = str(event.get("run_id") or "")
                parent_ids = event.get("parent_ids") or []
                aid, aname = agent_of(event)

                # task 子智能体内部事件常缺 namespace：沿 parent_ids 继承归属
                if parent_ids:
                    parent = str(parent_ids[-1])
                    if parent in run_agent and aid == EXECUTOR_ID:
                        inherited = run_agent[parent]
                        if inherited != EXECUTOR_ID:
                            aid, aname = inherited, names.get(inherited, inherited)
                if run_id:
                    run_agent[run_id] = aid

                ensure(aid)

                if kind == "on_tool_start":
                    data = event.get("data") or {}
                    meta = event.get("metadata") or {}
                    tool_name = str(event.get("name") or "tool")
                    args = _parse_tool_input(data.get("input"))
                    tool_id = str(
                        meta.get("tool_call_id")
                        or data.get("tool_call_id")
                        or run_id
                        or tool_name
                    )
                    if tool_name in _HARNESS_TOOLS:
                        pending[run_id] = {"harness": True, "id": tool_id, "name": tool_name}
                        push(
                            {
                                "thinking": f"[tool] @{tool_name} {json.dumps(args, ensure_ascii=False)}\n",
                                "agent_id": aid,
                                "agent_name": aname,
                            }
                        )
                        continue
                    payload_tc = {
                        "id": tool_id,
                        "name": tool_name,
                        "args": args,
                        "agent_id": aid,
                        "agent_name": aname,
                    }
                    if tool_name == "task":
                        sub_id = str(args.get("subagent_type") or "").strip()
                        if sub_id:
                            payload_tc["subagent_id"] = sub_id
                            payload_tc["subagent_name"] = names.get(sub_id, sub_id)
                            payload_tc["is_subagent_call"] = True
                            ensure(sub_id, parent=aid)
                            # 该 task 的子事件归属到目标子智能体
                            if run_id:
                                run_agent[run_id] = sub_id
                    pending[run_id] = payload_tc
                    push({"tool_call": payload_tc})
                    continue

                if kind == "on_tool_end":
                    data = event.get("data") or {}
                    output = data.get("output")
                    prev = pending.get(run_id) or {}
                    tool_name = str(prev.get("name") or event.get("name") or "tool")
                    content = getattr(output, "content", output)
                    if not isinstance(content, str):
                        content = str(content or "")
                    if prev.get("harness") or tool_name in _HARNESS_TOOLS:
                        push(
                            {
                                "thinking": f"结果: {content[:400]}\n",
                                "agent_id": prev.get("agent_id") or aid,
                                "agent_name": prev.get("agent_name") or aname,
                            }
                        )
                        continue
                    push(
                        {
                            "tool_result": {
                                "id": prev.get("id") or run_id,
                                "name": tool_name,
                                "content": content[:1200],
                                "image_urls": _extract_tool_image_urls(content),
                                "agent_id": prev.get("agent_id") or aid,
                                "agent_name": prev.get("agent_name") or aname,
                                "subagent_id": prev.get("subagent_id"),
                                "subagent_name": prev.get("subagent_name"),
                                "is_subagent_call": bool(prev.get("is_subagent_call")),
                            }
                        }
                    )
                    continue

                if kind == "on_chat_model_stream":
                    chunk = (event.get("data") or {}).get("chunk")
                    if chunk is None:
                        continue
                    reasoning = _extract_reasoning(chunk)
                    if reasoning:
                        push(
                            {
                                "thinking": reasoning,
                                "agent_id": aid,
                                "agent_name": aname,
                            }
                        )
                    raw = getattr(chunk, "content", None)
                    pieces: list[str] = []
                    if isinstance(raw, str):
                        pieces.append(raw)
                    elif isinstance(raw, list):
                        for part in raw:
                            if not isinstance(part, dict):
                                pieces.append(str(part))
                                continue
                            ptype = str(part.get("type") or "").lower()
                            if ptype in {"reasoning", "thinking"}:
                                continue
                            pieces.append(str(part.get("text") or ""))
                    text = "".join(pieces)
                    if text.strip():
                        for block in think_re.findall(text):
                            if block:
                                push(
                                    {
                                        "thinking": block,
                                        "agent_id": aid,
                                        "agent_name": aname,
                                    }
                                )
                        text = think_re.sub("", text)
                        if text.strip():
                            push(
                                {
                                    "thinking": text,
                                    "agent_id": aid,
                                    "agent_name": aname,
                                }
                            )
                if kind == "on_chat_model_end":
                    out = (event.get("data") or {}).get("output")
                    raw = getattr(out, "content", None) if out is not None else None
                    if isinstance(raw, str) and raw.strip() and aid == EXECUTOR_ID:
                        final = raw
            return final

        loop = asyncio.new_event_loop()
        try:
            answer = loop.run_until_complete(_collect())
        finally:
            loop.close()
        answer = answer or ""
        # 本轮工具图片若正文未包含则补进 markdown（按路径去重，忽略 ?t= 等 query）
        seen_keys: set[str] = set()
        for m in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", answer):
            k = str(m.group(1) or "").split("#", 1)[0].split("?", 1)[0].lower()
            if k:
                seen_keys.add(k)
        for ev in trace:
            tr = ev.get("tool_result")
            if not isinstance(tr, dict):
                continue
            for url in tr.get("image_urls") or []:
                u = str(url or "").strip()
                k = u.split("#", 1)[0].split("?", 1)[0].lower()
                if u and k and k not in seen_keys and k not in answer.lower():
                    seen_keys.add(k)
                    answer += ("\n\n" if answer else "") + f"![图片]({u})"
        return answer

    def _wait_interruptible(self, p: dict, seconds: float) -> bool:
        """
        可中断等待。返回 True 表示应退出循环（收到 stop）。
        等待期间支持 pause / run_now。
        """
        end = time.time() + max(0.0, seconds)
        while True:
            if p["_stop"].is_set():
                return True
            if p["_run_now"].is_set():
                p["_run_now"].clear()
                return False
            if p["_paused"].is_set():
                with self._lock:
                    if p["status"] not in self.DONE and p["status"] != "in_progress":
                        p["status"] = "paused"
                # 暂停时小步等待，便于 stop / resume / run_now 唤醒
                if p["_stop"].wait(0.3):
                    return True
                continue
            remain = end - time.time()
            if remain <= 0:
                return False
            if p["_stop"].wait(min(0.5, remain)):
                return True

    def _loop(self, plan_id: str) -> None:
        with self._lock:
            p = self._plans.get(plan_id)
        if not p:
            return
        try:
            first = p["schedule_type"] == "interval"
            while not p["_stop"].is_set():
                if p["_paused"].is_set() and not p["_run_now"].is_set():
                    with self._lock:
                        if p["status"] not in self.DONE:
                            p["status"] = "paused"
                    if self._wait_interruptible(p, 3600):
                        break
                    continue

                if first or p["_run_now"].is_set():
                    first = False
                    p["_run_now"].clear()
                else:
                    nxt = self._next_time(p, datetime.now())
                    if nxt is None:
                        # 无法计算下次时间：暂停并说明原因，切勿误标「已完成」
                        with self._lock:
                            if p["status"] not in self.DONE:
                                p["status"] = "paused"
                                p["_paused"].set()
                                if not p.get("last_result"):
                                    p["last_result"] = (
                                        "无法计算下次执行时间，请检查调度参数后重新执行"
                                    )
                                p["next_run"] = "-"
                        break
                    with self._lock:
                        p["next_run"] = nxt.strftime("%Y-%m-%d %H:%M:%S")
                        if p["status"] not in {"cancelled", "paused"}:
                            p["status"] = "pending"
                    wait = (nxt - datetime.now()).total_seconds()
                    if wait > 0 and self._wait_interruptible(p, wait):
                        break
                    if p["_paused"].is_set() and not p["_run_now"].is_set():
                        continue

                with self._lock:
                    if p["status"] == "cancelled" or p["_stop"].is_set():
                        break
                    if p["_paused"].is_set() and not p["_run_now"].is_set():
                        p["status"] = "paused"
                        continue
                    p["status"] = "in_progress"
                    p["run_count"] += 1
                    if "_run_finished" in p:
                        p["_run_finished"].clear()
                    p["_last_trace"] = []

                try:
                    answer = self._execute(p)
                    with self._lock:
                        p["last_result"] = answer
                except Exception as e:
                    with self._lock:
                        p["last_result"] = f"错误: {e}"
                finally:
                    if "_run_finished" in p:
                        p["_run_finished"].set()

                done = p["schedule_type"] == "once" or (
                    p["max_runs"] > 0 and p["run_count"] >= p["max_runs"]
                )
                with self._lock:
                    if p["status"] == "cancelled" or p["_stop"].is_set():
                        break
                    if p["_paused"].is_set():
                        p["status"] = "paused"
                    else:
                        p["status"] = "completed" if done else "pending"
                        if done:
                            p["next_run"] = "-"
                if done:
                    break
        finally:
            with self._lock:
                if p.get("status") not in self.DONE:
                    if p["_stop"].is_set():
                        p["status"] = "cancelled"
                    elif p["_paused"].is_set():
                        p["status"] = "paused"
                    else:
                        # 异常退出且未满足完成条件：暂停，避免 0 次执行却显示已完成
                        p["status"] = "paused"
                        p["_paused"].set()
                        if not p.get("last_result"):
                            p["last_result"] = (
                                "计划线程已结束但未完成执行，请检查调度参数或点击执行计划"
                            )
                p["_thread"] = None
