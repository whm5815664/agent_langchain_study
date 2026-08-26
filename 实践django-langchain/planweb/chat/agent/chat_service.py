"""消息构造与流式聊天（SSE：思维链 / 工具 / 智能体框线）。支持图片+文字多模态。"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import mimetypes
import queue
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional

from langchain_core.messages import AIMessage, HumanMessage

from .main_agent import AGENT_DISPLAY_NAME, AGENT_ID, main_agent
from .registry import SUBAGENT_IDS, is_subagent_id, resolve_agent_name

# planweb/media（chat/agent/chat_service.py → parents[2]）
_MEDIA_ROOT = Path(__file__).resolve().parents[2] / "media"
_TEMP_DIR = _MEDIA_ROOT / "temp"
_TEMP_NAME_RE = re.compile(r"^temp(\d+)\.(?:jpg|jpeg|png|webp|gif)$", re.IGNORECASE)

# deepagents 内置工具：进思考过程，不进「计划/工具」框
_HARNESS_TOOLS = frozenset(
    {"ls", "glob", "grep", "read_file", "write_file", "edit_file", "delete", "execute", "write_todos"}
)

# 会话级强制停止：仅中断当前 agent 流式执行，不触碰计划清单
_cancel_lock = threading.Lock()
_active_runs: dict[str, threading.Event] = {}


class ChatCancelled(Exception):
    """用户强制停止当前会话执行（不终止计划清单）。"""


def register_run(run_id: str) -> threading.Event:
    """注册一次会话执行，返回可被 set 的取消事件。"""
    rid = (run_id or "").strip()
    if not rid:
        raise ValueError("run_id 不能为空")
    ev = threading.Event()
    with _cancel_lock:
        old = _active_runs.get(rid)
        if old is not None:
            old.set()
        _active_runs[rid] = ev
    return ev


def request_cancel(run_id: str) -> bool:
    """请求中断指定会话执行。计划清单不受影响。"""
    rid = (run_id or "").strip()
    if not rid:
        return False
    with _cancel_lock:
        ev = _active_runs.get(rid)
    if ev is None:
        return False
    ev.set()
    return True


def unregister_run(run_id: str) -> None:
    rid = (run_id or "").strip()
    if not rid:
        return
    with _cancel_lock:
        _active_runs.pop(rid, None)


def _iter_agent_stream_events(
    agent: Any,
    payload: dict,
    *,
    cancel_event: Optional[threading.Event] = None,
) -> Iterator[dict]:
    """同步桥接 astream_events(version='v2')，支持强制停止。"""
    out: queue.Queue = queue.Queue(maxsize=64)

    async def _produce() -> None:
        try:
            async for event in agent.astream_events(payload, version="v2"):
                if cancel_event is not None and cancel_event.is_set():
                    break
                # 不可用阻塞 put：满队列时会卡死事件循环，导致取消监视器无法运行
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        return
                    try:
                        out.put(("ok", event), timeout=0.15)
                        break
                    except queue.Full:
                        await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            out.put(("err", exc))
        finally:
            with contextlib.suppress(Exception):
                out.put(("end", None), timeout=1)

    def _thread_main() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _main() -> None:
            task = asyncio.create_task(_produce())

            async def _watch() -> None:
                if cancel_event is None:
                    return
                while not task.done():
                    if cancel_event.is_set():
                        task.cancel()
                        return
                    await asyncio.sleep(0.12)

            watcher = asyncio.create_task(_watch()) if cancel_event is not None else None
            try:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            finally:
                if watcher is not None:
                    watcher.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await watcher

        try:
            loop.run_until_complete(_main())
        finally:
            loop.close()

    worker = threading.Thread(target=_thread_main, daemon=True, name="agent-stream")
    worker.start()
    try:
        while True:
            while True:
                try:
                    kind, data = out.get(timeout=0.2)
                    break
                except queue.Empty:
                    if cancel_event is not None and cancel_event.is_set():
                        raise ChatCancelled from None
                    continue
            if kind == "end":
                if cancel_event is not None and cancel_event.is_set():
                    raise ChatCancelled from None
                break
            if kind == "err":
                raise data
            yield data
    except (ChatCancelled, GeneratorExit):
        # 提前退出时唤醒生产者线程；正常结束不置位，避免误判为强制停止
        if cancel_event is not None:
            cancel_event.set()
        raise
    finally:
        worker.join(timeout=3)

def _extract_text(content: Any) -> str:
    """提取纯文本（跳过 reasoning 块；图片只记占位，不展开 base64）。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        n_images = 0
        for part in content:
            if isinstance(part, dict):
                part_type = str(part.get("type") or "").lower()
                if part_type in {"reasoning", "thinking"}:
                    continue
                if part_type in {"image_url", "image"}:
                    n_images += 1
                    continue
                text = part.get("text")
                if text:
                    parts.append(str(text))
            else:
                parts.append(str(part))
        if n_images:
            parts.append(f"[含 {n_images} 张图片]")
        return "".join(parts) if parts else (f"[含 {n_images} 张图片]" if n_images else "")
    return str(content)


def _extract_reasoning(message: Any) -> str:
    """从消息/chunk 提取思维链增量（reasoning_content / thinking 块）。"""
    for source in (
        getattr(message, "additional_kwargs", None) or {},
        getattr(message, "response_metadata", None) or {},
    ):
        for key in ("reasoning_content", "reasoning", "thinking"):
            value = source.get(key)
            if value:
                if isinstance(value, str):
                    return value
                if isinstance(value, list):
                    return "".join(
                        str(
                            part.get("thinking")
                            or part.get("text")
                            or part.get("reasoning")
                            or part
                        )
                        if isinstance(part, dict)
                        else str(part)
                        for part in value
                    )
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


def _truncate(text: str, limit: int = 1200) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…（已截断）"


def _extract_tool_image_urls(content: str) -> list[str]:
    """从工具返回中提取全部可展示图片 URL（JSON / markdown / 裸链接）。"""
    text = (content or "").strip()
    if not text:
        return []
    urls: list[str] = []
    seen: set[str] = set()

    def key_of(url: str) -> str:
        u = (url or "").strip().rstrip(")，。,.>")
        if not u:
            return ""
        return u.split("#", 1)[0].split("?", 1)[0].lower()

    def add(url: str) -> None:
        u = (url or "").strip().rstrip(")，。,.>")
        k = key_of(u)
        if u and k and k not in seen:
            seen.add(k)
            urls.append(u)

    def from_obj(obj: Any) -> None:
        if isinstance(obj, dict):
            url = obj.get("image_url")
            if isinstance(url, str):
                add(url)
        elif isinstance(obj, list):
            for item in obj:
                from_obj(item)

    try:
        from_obj(json.loads(text))
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    for m in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", text):
        add(m.group(1))
    for m in re.finditer(
        r"(?:https?://|/media/)[^\s\"'<>]+\.(?:jpg|jpeg|png|gif|webp)(?:\?[^\s\"'<>]*)?",
        text,
        re.IGNORECASE,
    ):
        add(m.group(0))
    return urls


# 发给 LLM 前压缩，避免上游 nginx 413（Request Entity Too Large）
_MAX_IMAGE_EDGE = 1280
_JPEG_QUALITY = 75


def _compress_image_bytes(data: bytes, mime: str | None = None) -> tuple[bytes, str]:
    """缩小长边并转 JPEG，降低多模态请求体体积。"""
    from io import BytesIO

    from PIL import Image

    try:
        img = Image.open(BytesIO(data))
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")
        w, h = img.size
        longest = max(w, h)
        if longest > _MAX_IMAGE_EDGE:
            scale = _MAX_IMAGE_EDGE / float(longest)
            img = img.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.Resampling.LANCZOS,
            )
        out = BytesIO()
        img.save(out, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        return out.getvalue(), "image/jpeg"
    except Exception:
        mt = (mime or "").split(";")[0].strip() or "image/jpeg"
        return data, mt


def _parse_data_url(ref: str) -> tuple[bytes, str] | tuple[None, None]:
    """解析 data:[mime];base64,... 为 (bytes, mime)。"""
    try:
        header, b64 = ref.split(",", 1)
        mime = "image/jpeg"
        if header.startswith("data:") and ";" in header:
            mime = header[5:].split(";", 1)[0].strip() or mime
        return base64.b64decode(b64), mime
    except Exception:
        return None, None


def _to_image_url(ref: str) -> str:
    """本地路径或 /media/...、http(s)、data: 均转为压缩后的 data URL。"""
    ref = (ref or "").strip()
    if not ref:
        return ""

    data: bytes | None = None
    mime: str | None = None
    hint = ""

    if ref.startswith("data:"):
        data, mime = _parse_data_url(ref)
        if not data:
            return ""
    elif ref.startswith(("http://", "https://")):
        # 远程 URL 先本地下载再内联，避免上游网关拉图失败（如禁 8175 端口）
        import requests

        try:
            resp = requests.get(ref, timeout=15)
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            return ""
        data, mime, hint = resp.content, resp.headers.get("Content-Type"), ref.split("?", 1)[0]
    else:
        path_str = ref.split("?", 1)[0]
        if path_str.startswith("/media/"):
            filepath = _MEDIA_ROOT / path_str[len("/media/") :].lstrip("/\\")
        else:
            filepath = Path(path_str)
        if not filepath.is_file():
            return ""
        data = filepath.read_bytes()
        mime = mimetypes.guess_type(str(filepath))[0]
        hint = str(filepath)

    if not data:
        return ""
    mt = (mime or "").split(";")[0].strip()
    if not mt or mt == "application/octet-stream":
        mt = mimetypes.guess_type(hint)[0] or "image/jpeg"
    data, mt = _compress_image_bytes(data, mt)
    return f"data:{mt};base64,{base64.b64encode(data).decode('ascii')}"


def _image_ref_from_item(item: Any) -> str:
    """从 images 列表项提取原始引用（data URL / 路径 / http）。"""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict) and item.get("type") == "image_url":
        url_obj = item.get("image_url")
        if isinstance(url_obj, dict):
            return str(url_obj.get("url") or "").strip()
        if isinstance(url_obj, str):
            return url_obj.strip()
    return ""


def _load_image_bytes(ref: str) -> tuple[bytes, str] | tuple[None, None]:
    """将 data:/http(s):/本地路径 读成 (bytes, mime)。"""
    ref = (ref or "").strip()
    if not ref:
        return None, None
    if ref.startswith("data:"):
        return _parse_data_url(ref)
    if ref.startswith(("http://", "https://")):
        import requests

        try:
            resp = requests.get(ref, timeout=15)
            resp.raise_for_status()
        except requests.exceptions.RequestException:
            return None, None
        mime = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        return resp.content, mime or "image/jpeg"
    path_str = ref.split("?", 1)[0]
    if path_str.startswith("/media/"):
        filepath = _MEDIA_ROOT / path_str[len("/media/") :].lstrip("/\\")
    else:
        filepath = Path(path_str)
    if not filepath.is_file():
        return None, None
    mime = mimetypes.guess_type(str(filepath))[0] or "image/jpeg"
    return filepath.read_bytes(), mime


def _next_temp_index() -> int:
    """扫描 media/temp，返回下一个可用序号。"""
    if not _TEMP_DIR.is_dir():
        return 1
    max_n = 0
    for p in _TEMP_DIR.iterdir():
        m = _TEMP_NAME_RE.match(p.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return max_n + 1


def persist_user_images(images: list | None) -> list[str]:
    """
    将用户上传图片暂存为 media/temp/tempN.jpg，供后续智能体按路径读取。
    返回 /media/temp/tempN.jpg 列表（按保存顺序）。
    """
    if not images:
        return []
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    idx = _next_temp_index()
    saved: list[str] = []
    for item in images:
        raw = _image_ref_from_item(item)
        if not raw:
            continue
        data, mime = _load_image_bytes(raw)
        if not data:
            continue
        data, _mt = _compress_image_bytes(data, mime)
        name = f"temp{idx}.jpg"
        (_TEMP_DIR / name).write_bytes(data)
        saved.append(f"/media/temp/{name}")
        idx += 1
    return saved


def clear_temp_images() -> int:
    """删除 media/temp 下全部 tempN.* 暂存图，返回删除数量。"""
    if not _TEMP_DIR.is_dir():
        return 0
    n = 0
    for p in list(_TEMP_DIR.iterdir()):
        if p.is_file() and _TEMP_NAME_RE.match(p.name):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n


def build_messages(
    history: list | None,
    user_message: str,
    *,
    images: list | None = None,
) -> list:
    """将前端 history + 当前输入（可选图片）构造成 LangChain 消息列表。"""
    messages: list = []
    for item in (history or [])[-20:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        text = _extract_text(item.get("content")).strip()
        if role == "user":
            # 历史图片只保留文字占位，避免多轮 base64 反复进上下文导致溢出
            hist_images = item.get("images") or []
            n_img = len(hist_images) if isinstance(hist_images, list) else 0
            if n_img and not text:
                text = f"[含 {n_img} 张图片]"
            elif n_img:
                text = f"{text}\n[含 {n_img} 张图片]"
            if not text:
                continue
            messages.append(HumanMessage(content=text))
        elif role == "assistant":
            if not text:
                continue
            messages.append(AIMessage(content=text))

    text = (user_message or "").strip()
    # 构造 HumanMessage.content：纯文本或 text + image_url
    img_parts: list[dict] = []
    for item in images or []:
        raw = _image_ref_from_item(item)
        url = _to_image_url(raw) if raw else ""
        if url:
            img_parts.append({"type": "image_url", "image_url": {"url": url}})
    if img_parts:
        content: str | list = ([{"type": "text", "text": text}] if text else []) + img_parts
    else:
        content = text
    if not content:
        raise ValueError("消息不能为空（请输入文字或上传图片）")
    messages.append(HumanMessage(content=content))
    return messages


def _emit_content_with_think_tags(
    raw: str,
    *,
    in_think_tag: bool,
    tag_buffer: str,
) -> tuple[list[str], bool, str]:
    """处理跨 chunk 的 <think> 标签，只产出可见 content（thinking 已由 emit_ai 进思维链）。"""
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
            # 保留尾部以防跨 chunk 截断结束标签；中间思考内容丢弃（已进思维链）
            if len(tag_buffer) > 16:
                tag_buffer = tag_buffer[-16:]
                lower = tag_buffer.lower()
            break

        tag_buffer = tag_buffer[end + len("</think>") :]
        lower = tag_buffer.lower()
        in_think_tag = False

    return contents, in_think_tag, tag_buffer


def _agent_from_event(event: dict) -> tuple[str, str]:
    """从事件元数据解析当前智能体（嵌套时取最深层）。"""
    meta = event.get("metadata") or {}
    known = {AGENT_ID, *SUBAGENT_IDS}
    ns = str(meta.get("langgraph_checkpoint_ns") or "")
    for part in reversed(ns.split("|")):
        sid = part.strip().split(":", 1)[0]
        if sid in known:
            return sid, resolve_agent_name(sid)
    name = str(
        meta.get("lc_agent_name") or meta.get("langgraph_node") or event.get("name") or ""
    ).strip()
    if name in known:
        return name, resolve_agent_name(name)
    return AGENT_ID, AGENT_DISPLAY_NAME


def _parse_tool_input(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {"input": raw}
    return {}


def _yield_agent(*, agent_id: str, parent_id: str | None = None) -> dict:
    return {
        "agent": {
            "id": agent_id,
            "name": resolve_agent_name(agent_id),
            "parent_id": parent_id,
        }
    }


def _think(text: str, agent_id: str, agent_name: str) -> dict:
    return {"thinking": text, "agent_id": agent_id, "agent_name": agent_name}


def _flatten_messages(raw: Any) -> list:
    if raw is None:
        return []
    if isinstance(raw, dict) and "messages" in raw:
        raw = raw["messages"]
    if isinstance(raw, list) and raw and isinstance(raw[0], list):
        out: list = []
        for group in raw:
            out.extend(group)
        return out
    if isinstance(raw, list):
        return raw
    return [raw]


def _format_message_dump(msg: Any) -> str:
    t = str(getattr(msg, "type", "") or "").lower()
    label = {
        "system": "SystemMessage",
        "human": "HumanMessage",
        "ai": "AIMessage",
        "tool": "ToolMessage",
    }.get(t) or (type(msg).__name__ or "Message")
    content = _extract_text(getattr(msg, "content", None))
    # System / 长文本截断，避免执行轨迹把前端撑卡
    content = _truncate(content, 600 if label == "SystemMessage" else 2000)
    lines = [f"[{label}]"]
    if content:
        lines.append(content)
    name = getattr(msg, "name", None)
    if name and label == "ToolMessage":
        lines.insert(1, f"name={name}")
    tool_calls = getattr(msg, "tool_calls", None) or []
    if tool_calls:
        try:
            dumped = json.dumps(tool_calls, ensure_ascii=False)
        except (TypeError, ValueError):
            dumped = str(tool_calls)
        lines.append("[tool_calls] " + _truncate(dumped, 1500))
    return "\n".join(lines) + "\n"


def _usage_from_obj(obj: Any) -> dict[str, int]:
    """从 AIMessage / chunk 提取 token 用量。"""
    usage = getattr(obj, "usage_metadata", None) or {}
    if isinstance(usage, dict) and usage:
        inp = int(usage.get("input_tokens") or usage.get("input") or 0)
        out = int(usage.get("output_tokens") or usage.get("output") or 0)
        total = int(usage.get("total_tokens") or usage.get("total") or (inp + out))
        return {"input": inp, "output": out, "total": total}
    meta = getattr(obj, "response_metadata", None) or {}
    tu = meta.get("token_usage") or meta.get("usage") or {}
    if isinstance(tu, dict) and tu:
        inp = int(tu.get("prompt_tokens") or tu.get("input_tokens") or 0)
        out = int(tu.get("completion_tokens") or tu.get("output_tokens") or 0)
        total = int(tu.get("total_tokens") or (inp + out))
        return {"input": inp, "output": out, "total": total}
    return {"input": 0, "output": 0, "total": 0}


def _emit_plan_exec_trace(
    *,
    args: dict,
    result_text: str,
    parent_id: str,
) -> Iterator[dict]:
    """等待计划首跑，并实时把执行轨迹（思维链/工具）注入对话。"""
    from chat.agent.planServer import get_plan_service
    from chat.agent.planServer.planserver_subagent import EXECUTOR_ID

    plan_id = str((args or {}).get("plan_id") or "").strip()
    if not plan_id:
        m = re.search(r"\[([0-9a-fA-F]{8})\]", result_text or "")
        plan_id = m.group(1) if m else ""
    if not plan_id:
        return

    svc = get_plan_service()
    parent_name = resolve_agent_name(parent_id)
    schedule = str((args or {}).get("schedule_type") or "").strip().lower()
    # interval 会立刻首跑；run_scheduled_task 也会立刻跑。once/periodic 预约不阻塞等待
    if schedule != "interval" and "已执行" not in (result_text or ""):
        return

    yield _think("[tool] 计划首轮执行中…\n", parent_id, parent_name)

    seen = 0
    deadline = time.time() + 90
    while True:
        done = svc.is_run_finished(plan_id) or time.time() >= deadline
        batch = svc.take_trace(plan_id) if done else svc.snapshot_trace(plan_id)
        if len(batch) > seen:
            for ev in batch[seen:]:
                if "agent" in ev:
                    ag = dict(ev["agent"])
                    if ag.get("id") == EXECUTOR_ID:
                        ag["parent_id"] = parent_id
                    yield {"agent": ag}
                elif "thinking" in ev or "tool_call" in ev or "tool_result" in ev:
                    yield ev
            seen = len(batch)
        if done:
            break
        time.sleep(0.4)


def stream_chat_events(
    history: list | None,
    user_message: str,
    *,
    images: list | None = None,
    cancel_event: Optional[threading.Event] = None,
) -> Iterator[dict]:
    """流式运行主 Agent，产出 agent / tool / thinking / content 事件。"""
    agent = main_agent

    # 用户上传图暂存为 tempN.jpg，本轮仍走多模态；路径写入正文供后续轮次引用
    saved_urls = persist_user_images(images)
    text = (user_message or "").strip()
    if saved_urls:
        note = "用户上传图片已暂存：" + "、".join(saved_urls) + "（刷新会话后清除）"
        text = f"{text}\n{note}" if text else note
    # 优先用本地暂存路径喂给视觉模型，避免重复塞入巨大 base64
    images_for_llm: list | None = saved_urls if saved_urls else images

    messages = build_messages(history, text, images=images_for_llm)

    yield _yield_agent(agent_id=AGENT_ID)

    if text or images_for_llm:
        label = (user_message or "").strip() or f"[图片 x{len(images_for_llm or [])}]"
        if saved_urls:
            label = f"{label}\n[暂存: {', '.join(saved_urls)}]"
        yield _think(f"[human] {label}\n", AGENT_ID, AGENT_DISPLAY_NAME)

    emitted_agents: set[str] = {AGENT_ID}
    emitted_tool_starts: set[str] = set()
    run_agent: dict[str, str] = {}
    pending_tools: dict[str, dict] = {}
    think_state: dict[str, Any] = {"in_think": False, "buffer": ""}
    ai_tag_opened: set[str] = set()
    token_totals = {"input": 0, "output": 0, "total": 0}
    dumped_model_inputs: set[str] = set()

    def emit_ai(piece: str, aid: str, aname: str) -> Iterator[dict]:
        if not piece:
            return
        if aid not in ai_tag_opened:
            ai_tag_opened.add(aid)
            yield _think(f"[ai] {piece}", aid, aname)
        else:
            yield _think(piece, aid, aname)

    def add_usage(obj: Any) -> None:
        u = _usage_from_obj(obj)
        if u["total"] or u["input"] or u["output"]:
            token_totals["input"] += u["input"]
            token_totals["output"] += u["output"]
            token_totals["total"] += u["total"] or (u["input"] + u["output"])

    def _raise_if_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise ChatCancelled

    for event in _iter_agent_stream_events(
        agent, {"messages": messages}, cancel_event=cancel_event
    ):
        _raise_if_cancelled()
        kind = event.get("event") or ""
        event_run_id = str(event.get("run_id") or "")
        parent_ids = event.get("parent_ids") or []
        agent_id, agent_name = _agent_from_event(event)

        if parent_ids:
            parent = str(parent_ids[-1])
            if parent in run_agent and agent_id == AGENT_ID:
                inherited = run_agent[parent]
                if inherited != AGENT_ID:
                    agent_id, agent_name = inherited, resolve_agent_name(inherited)
        if event_run_id:
            run_agent[event_run_id] = agent_id

        if agent_id not in emitted_agents:
            emitted_agents.add(agent_id)
            parent_id = AGENT_ID if is_subagent_id(agent_id) else None
            ns = str((event.get("metadata") or {}).get("langgraph_checkpoint_ns") or "")
            known = {AGENT_ID, *SUBAGENT_IDS}
            chain = [
                part.strip().split(":", 1)[0]
                for part in ns.split("|")
                if part.strip().split(":", 1)[0] in known
            ]
            if len(chain) >= 2 and chain[-1] == agent_id:
                parent_id = chain[-2]
            yield _yield_agent(agent_id=agent_id, parent_id=parent_id)

        if kind == "on_chat_model_start":
            # 输出本轮喂给模型的 SystemMessage / HumanMessage 等
            key = event_run_id or f"{agent_id}:{len(dumped_model_inputs)}"
            if key not in dumped_model_inputs:
                dumped_model_inputs.add(key)
                for msg in _flatten_messages((event.get("data") or {}).get("input")):
                    yield _think(
                        _format_message_dump(msg),
                        agent_id,
                        agent_name,
                    )
            continue

        if kind == "on_chat_model_end":
            output = (event.get("data") or {}).get("output")
            if output is not None:
                add_usage(output)
                # AIMessage 全文 + tool_calls
                yield _think(
                    _format_message_dump(output),
                    agent_id,
                    agent_name,
                )
            ai_tag_opened.discard(agent_id)
            continue

        if kind == "on_tool_start":
            data = event.get("data") or {}
            meta = event.get("metadata") or {}
            tool_name = str(event.get("name") or "unknown_tool")
            args = _parse_tool_input(data.get("input"))
            tool_id = str(
                meta.get("tool_call_id")
                or data.get("tool_call_id")
                or event_run_id
                or tool_name
            )
            dedupe = f"{tool_id}:{tool_name}"
            if dedupe in emitted_tool_starts:
                continue
            emitted_tool_starts.add(dedupe)

            payload: dict[str, Any] = {
                "id": tool_id,
                "name": tool_name,
                "args": args,
                "agent_id": agent_id,
                "agent_name": agent_name,
            }
            if tool_name == "task":
                sub_id = str(args.get("subagent_type") or "").strip()
                if sub_id:
                    payload["subagent_id"] = sub_id
                    payload["subagent_name"] = resolve_agent_name(sub_id)
                    payload["is_subagent_call"] = True
            pending_tools[event_run_id] = payload

            hint = (
                f" → {payload['subagent_name']}"
                if payload.get("is_subagent_call") and payload.get("subagent_name")
                else ""
            )
            yield _think(
                f"[tool_calls] @{tool_name}{hint} {json.dumps(args, ensure_ascii=False)}\n",
                agent_id,
                agent_name,
            )
            if tool_name in _HARNESS_TOOLS:
                continue

            sub_id = payload.get("subagent_id")
            if sub_id and sub_id not in emitted_agents:
                emitted_agents.add(sub_id)
                yield _yield_agent(agent_id=sub_id, parent_id=agent_id)
            if sub_id and event_run_id:
                run_agent[event_run_id] = sub_id
            yield {"tool_call": payload}
            continue

        if kind == "on_tool_end":
            data = event.get("data") or {}
            output = data.get("output")
            tool_name = str(event.get("name") or "unknown_tool")
            tool_id = event_run_id
            prev = pending_tools.get(event_run_id)
            if prev:
                tool_name = prev.get("name") or tool_name
                tool_id = prev.get("id") or tool_id
                agent_id = prev.get("agent_id") or agent_id
                agent_name = prev.get("agent_name") or agent_name

            content = _extract_text(
                getattr(output, "content", output) if hasattr(output, "content") else output
            )
            # ToolMessage 全文进思考过程
            yield _think(
                f"[ToolMessage] name={tool_name}\n{_truncate(content, 4000)}\n",
                agent_id,
                agent_name,
            )
            if tool_name in _HARNESS_TOOLS:
                continue

            image_urls = _extract_tool_image_urls(content)
            yield {
                "tool_result": {
                    "id": tool_id,
                    "name": tool_name,
                    "content": _truncate(content),
                    "image_urls": image_urls,
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "subagent_id": (prev or {}).get("subagent_id"),
                    "subagent_name": (prev or {}).get("subagent_name"),
                    "is_subagent_call": bool((prev or {}).get("is_subagent_call")),
                }
            }
            if tool_name in {"create_scheduled_task", "run_scheduled_task"}:
                yield from _emit_plan_exec_trace(
                    args=(prev or {}).get("args") or {},
                    result_text=content,
                    parent_id=agent_id,
                )
            continue

        if kind != "on_chat_model_stream":
            continue

        chunk = (event.get("data") or {}).get("chunk")
        if chunk is None:
            continue

        reasoning = _extract_reasoning(chunk)
        if reasoning:
            yield from emit_ai(reasoning, agent_id, agent_name)

        raw = _extract_text(getattr(chunk, "content", None))
        if not raw:
            continue

        yield from emit_ai(raw, agent_id, agent_name)

        if agent_id != AGENT_ID:
            continue

        contents, in_think, buf = _emit_content_with_think_tags(
            raw,
            in_think_tag=bool(think_state["in_think"]),
            tag_buffer=str(think_state["buffer"]),
        )
        think_state["in_think"] = in_think
        think_state["buffer"] = buf
        for piece in contents:
            yield {"content": piece}

    leftover = str(think_state.get("buffer") or "")
    if leftover and not think_state.get("in_think"):
        yield {"content": leftover}

    # 结束：总 token
    yield _think(
        f"\n[tokens] 本轮合计 input={token_totals['input']} "
        f"output={token_totals['output']} total={token_totals['total']}\n",
        AGENT_ID,
        AGENT_DISPLAY_NAME,
    )
    yield {"tokens": dict(token_totals)}


def stream_chat_sse(
    history: list | None,
    user_message: str,
    *,
    images: list | None = None,
    run_id: str | None = None,
) -> Iterator[str]:
    """产出前端可解析的 SSE 行。强制停止仅中断本轮执行，不影响计划清单。"""
    rid = (run_id or "").strip() or str(uuid.uuid4())
    cancel_event = register_run(rid)
    try:
        yield f"data: {json.dumps({'run_id': rid}, ensure_ascii=False)}\n\n"
        for event in stream_chat_events(
            history, user_message, images=images, cancel_event=cancel_event
        ):
            if cancel_event.is_set():
                raise ChatCancelled
            for key in ("agent", "tool_call", "tool_result", "tokens"):
                if key in event:
                    yield f"data: {json.dumps({key: event[key]}, ensure_ascii=False)}\n\n"
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
    except ChatCancelled:
        yield f"data: {json.dumps({'cancelled': True, 'message': '已强制停止本轮执行'}, ensure_ascii=False)}\n\n"
    except ValueError as exc:
        yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
        if "413" in err or "Request Entity Too Large" in err:
            err = "图片过大，上游接口拒绝请求（413）。已尝试压缩仍失败时请减少图片数量或换更小的图。"
        yield f"data: {json.dumps({'error': f'Agent 运行失败: {err}'}, ensure_ascii=False)}\n\n"
    finally:
        unregister_run(rid)
