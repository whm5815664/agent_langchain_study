"""消息构造与流式聊天（SSE：thinking / content / tool / edge）。"""

from __future__ import annotations

import json
from typing import Any, Iterator

from langchain_core.messages import AIMessage, HumanMessage

from mcp.client import EdgeMcpClient, EdgeMcpError, edge_registry, to_langchain_tools

from .main_agent import AGENT_DISPLAY_NAME, AGENT_ID, build_agent


def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                part_type = str(part.get("type") or "").lower()
                if part_type in {"reasoning", "thinking"}:
                    continue
                text = part.get("text")
                if text:
                    parts.append(str(text))
            else:
                parts.append(str(part))
        return "".join(parts)
    return str(content)


def _extract_reasoning(message: Any) -> str:
    for source in (
        getattr(message, "additional_kwargs", None) or {},
        getattr(message, "response_metadata", None) or {},
    ):
        for key in ("reasoning_content", "reasoning", "thinking"):
            value = source.get(key)
            if value:
                if isinstance(value, str):
                    return value
                return str(value)

    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "").lower() not in {"reasoning", "thinking"}:
            continue
        parts.append(
            str(part.get("thinking") or part.get("text") or part.get("reasoning") or "")
        )
    return "".join(parts)


def build_messages(history: list | None, user_message: str) -> list:
    messages: list = []
    for item in (history or [])[-20:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        text = _extract_text(item.get("content")).strip()
        if not text:
            continue
        if role == "user":
            messages.append(HumanMessage(content=text))
        elif role == "assistant":
            messages.append(AIMessage(content=text))

    text = (user_message or "").strip()
    if not text:
        raise ValueError("消息不能为空")
    messages.append(HumanMessage(content=text))
    return messages


def _emit_content_with_think_tags(
    raw: str,
    *,
    in_think_tag: bool,
    tag_buffer: str,
) -> tuple[list[str], bool, str]:
    contents: list[str] = []
    tag_buffer += raw
    lower = tag_buffer.lower()

    while tag_buffer:
        if not in_think_tag:
            start = lower.find("<think>")
            if start == -1:
                if "<" in tag_buffer[-10:]:
                    keep = tag_buffer.rfind("<")
                    visible = tag_buffer[:keep]
                    tag_buffer = tag_buffer[keep:]
                    lower = tag_buffer.lower()
                    if visible:
                        contents.append(visible)
                    break
                contents.append(tag_buffer)
                tag_buffer = ""
                lower = ""
                break

            visible = tag_buffer[:start]
            if visible:
                contents.append(visible)
            tag_buffer = tag_buffer[start + len("<think>") :]
            lower = tag_buffer.lower()
            in_think_tag = True
            continue

        end = lower.find("</think>")
        if end == -1:
            if len(tag_buffer) > 16:
                tag_buffer = tag_buffer[-16:]
                lower = tag_buffer.lower()
            break

        tag_buffer = tag_buffer[end + len("</think>") :]
        lower = tag_buffer.lower()
        in_think_tag = False

    return contents, in_think_tag, tag_buffer


def _load_edge_tools(
    session_id: str,
    tool_events: list[dict[str, Any]],
) -> tuple[list[Any], dict[str, Any]]:
    """Return (langchain_tools, edge_status_payload)."""
    if not session_id:
        return [], {"online": False, "tool_count": 0, "reason": "missing_session_id"}
    if not edge_registry.is_online(session_id):
        return [], {"online": False, "tool_count": 0, "reason": "offline"}

    def on_tool_call(name: str, arguments: dict[str, Any]) -> None:
        tool_events.append({"tool_call": {"name": name, "arguments": arguments}})

    def on_tool_result(name: str, text: str) -> None:
        preview = text if len(text) <= 800 else text[:800] + "…"
        tool_events.append({"tool_result": {"name": name, "content": preview}})

    try:
        client = EdgeMcpClient(session_id)
        session = edge_registry.get(session_id)
        if session and not session.tools_cache:
            try:
                client.initialize()
            except EdgeMcpError:
                pass
        tools_meta = client.list_tools(refresh=True)
        lc_tools = to_langchain_tools(
            session_id,
            tools_meta,
            on_call=on_tool_call,
            on_result=on_tool_result,
        )
        return lc_tools, {
            "online": True,
            "tool_count": len(lc_tools),
            "tools": [t.name for t in lc_tools],
        }
    except EdgeMcpError as exc:
        return [], {"online": False, "tool_count": 0, "reason": str(exc)}


def stream_chat_events(
    history: list | None,
    user_message: str,
    session_id: str | None = None,
) -> Iterator[dict]:
    messages = build_messages(history, user_message)
    think_state: dict[str, Any] = {"in_think": False, "buffer": ""}
    sid = (session_id or "").strip()
    tool_events: list[dict[str, Any]] = []

    yield {"agent": {"id": AGENT_ID, "name": AGENT_DISPLAY_NAME}}

    tools, edge_info = _load_edge_tools(sid, tool_events)
    yield {"edge": edge_info}

    agent = build_agent(tools=tools if tools else None)

    # Drain tool events periodically while streaming by wrapping tools' callbacks
    # (callbacks already append to tool_events during tool execution)

    for event in agent.stream(
        {"messages": messages},
        stream_mode="messages",
    ):
        # Flush any tool events produced between chunks
        while tool_events:
            yield tool_events.pop(0)

        if isinstance(event, tuple) and len(event) == 2:
            chunk, _meta = event
        else:
            chunk = event

        # LangChain tool call chunks may appear on AIMessage
        tool_calls = getattr(chunk, "tool_calls", None) or []
        for tc in tool_calls:
            if isinstance(tc, dict) and tc.get("name"):
                yield {
                    "tool_call": {
                        "name": tc.get("name"),
                        "arguments": tc.get("args") or {},
                    }
                }

        reasoning = _extract_reasoning(chunk)
        if reasoning:
            yield {
                "thinking": reasoning,
                "agent_id": AGENT_ID,
                "agent_name": AGENT_DISPLAY_NAME,
            }

        raw = _extract_text(getattr(chunk, "content", None))
        if not raw:
            continue

        contents, in_think, buf = _emit_content_with_think_tags(
            raw,
            in_think_tag=bool(think_state["in_think"]),
            tag_buffer=str(think_state["buffer"]),
        )
        think_state["in_think"] = in_think
        think_state["buffer"] = buf

        if in_think and raw and not contents:
            yield {
                "thinking": raw,
                "agent_id": AGENT_ID,
                "agent_name": AGENT_DISPLAY_NAME,
            }

        for piece in contents:
            yield {"content": piece}

    while tool_events:
        yield tool_events.pop(0)

    leftover = str(think_state.get("buffer") or "")
    if leftover and not think_state.get("in_think"):
        yield {"content": leftover}


def stream_chat_sse(
    history: list | None,
    user_message: str,
    session_id: str | None = None,
) -> Iterator[str]:
    try:
        for event in stream_chat_events(history, user_message, session_id=session_id):
            if "agent" in event:
                yield f"data: {json.dumps({'agent': event['agent']}, ensure_ascii=False)}\n\n"
            if "edge" in event:
                yield f"data: {json.dumps({'edge': event['edge']}, ensure_ascii=False)}\n\n"
            if event.get("tool_call"):
                yield f"data: {json.dumps({'tool_call': event['tool_call']}, ensure_ascii=False)}\n\n"
            if event.get("tool_result"):
                yield f"data: {json.dumps({'tool_result': event['tool_result']}, ensure_ascii=False)}\n\n"
            if event.get("thinking"):
                payload: dict[str, Any] = {"thinking": event["thinking"]}
                if event.get("agent_id"):
                    payload["agent_id"] = event["agent_id"]
                if event.get("agent_name"):
                    payload["agent_name"] = event["agent_name"]
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if event.get("content"):
                yield f"data: {json.dumps({'content': event['content']}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
    except ValueError as exc:
        yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
    except Exception as exc:  # noqa: BLE001
        yield f"data: {json.dumps({'error': f'Agent 运行失败: {exc}'}, ensure_ascii=False)}\n\n"
