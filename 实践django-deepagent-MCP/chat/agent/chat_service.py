"""消息构造与流式聊天（SSE：thinking / content）。"""

from __future__ import annotations

import json
from typing import Any, Iterator

from langchain_core.messages import AIMessage, HumanMessage

from .main_agent import AGENT_DISPLAY_NAME, AGENT_ID, main_agent


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


def stream_chat_events(history: list | None, user_message: str) -> Iterator[dict]:
    messages = build_messages(history, user_message)
    think_state: dict[str, Any] = {"in_think": False, "buffer": ""}

    yield {"agent": {"id": AGENT_ID, "name": AGENT_DISPLAY_NAME}}

    for event in main_agent.stream(
        {"messages": messages},
        stream_mode="messages",
    ):
        # stream_mode=messages → (message_chunk, metadata)
        if isinstance(event, tuple) and len(event) == 2:
            chunk, _meta = event
        else:
            chunk = event

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
            # 思考标签内的文本也推到 thinking
            yield {
                "thinking": raw,
                "agent_id": AGENT_ID,
                "agent_name": AGENT_DISPLAY_NAME,
            }

        for piece in contents:
            yield {"content": piece}

    leftover = str(think_state.get("buffer") or "")
    if leftover and not think_state.get("in_think"):
        yield {"content": leftover}


def stream_chat_sse(history: list | None, user_message: str) -> Iterator[str]:
    try:
        for event in stream_chat_events(history, user_message):
            if "agent" in event:
                yield f"data: {json.dumps({'agent': event['agent']}, ensure_ascii=False)}\n\n"
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
