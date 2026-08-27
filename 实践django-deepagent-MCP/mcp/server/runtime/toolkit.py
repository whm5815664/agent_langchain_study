# -*- coding: utf-8 -*-
"""编写本地 MCP 工具（以 @mcp.tool 为主）。

推荐::

    from mcp.toolkit import mcp, program, launch_program, ok, fail

详情见 mcp/toolkit.py 与 mcp/server/tools/_template.py。
"""

from __future__ import annotations

import inspect
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin

from .catalog import get_program_path

EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}

_TYPE_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}


def program(key: str, label: str | None = None, *, hint: str = "") -> dict[str, str]:
    """声明需在「MCP 设置」中填写的程序路径。"""
    return {"key": key, "label": label or key, "hint": hint}


def schema_props(**fields: Any) -> dict[str, Any]:
    """手动拼装 inputSchema（一般不必用，优先写函数参数注解）。"""
    required = list(fields.pop("required", []) or [])
    props = {k: v for k, v in fields.items() if isinstance(v, dict)}
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def _unwrap_annotated(annotation: Any) -> tuple[Any, str]:
    """返回 (实际类型, 描述)。支持 Annotated[str, '说明']。"""
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        if not args:
            return Any, ""
        desc = ""
        for extra in args[1:]:
            if isinstance(extra, str) and extra.strip():
                desc = extra.strip()
                break
        return args[0], desc
    return annotation, ""


def _annotation_to_json_type(annotation: Any) -> str:
    annotation, _ = _unwrap_annotated(annotation)
    if annotation is inspect.Parameter.empty or annotation is Any:
        return "string"
    origin = get_origin(annotation)
    if origin is not None:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _annotation_to_json_type(args[0])
        if origin in (list, tuple):
            return "array"
        if origin is dict:
            return "object"
        return "string"
    return _TYPE_TO_JSON.get(annotation, "string")


def _schema_from_signature(fn: Callable[..., Any]) -> dict[str, Any]:
    sig = inspect.signature(fn)
    props: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name in {"self", "cls", "args", "kwargs"}:
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if name == "args" and len(sig.parameters) == 1:
            return dict(EMPTY_SCHEMA)
        _, ann_desc = _unwrap_annotated(param.annotation)
        props[name] = {
            "type": _annotation_to_json_type(param.annotation),
            "description": ann_desc or name,
        }
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def _doc_first_line(fn: Callable[..., Any]) -> str:
    doc = inspect.getdoc(fn) or ""
    for line in doc.splitlines():
        text = line.strip()
        if text:
            return text
    return fn.__name__


def _make_invoke(fn: Callable[..., Any]) -> Callable[[dict[str, Any] | None], Any]:
    """适配为 bridge 统一的 handler(args: dict)。"""
    sig = inspect.signature(fn)
    params = [
        p
        for p in sig.parameters.values()
        if p.name not in {"self", "cls"} and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    ]

    def invoke(arguments: dict[str, Any] | None = None) -> Any:
        raw = arguments or {}
        if not params:
            return fn()
        if len(params) == 1 and params[0].name == "args":
            return fn(raw)
        kwargs: dict[str, Any] = {}
        missing: list[str] = []
        for p in params:
            if p.name in raw:
                kwargs[p.name] = raw[p.name]
            elif p.default is inspect.Parameter.empty:
                missing.append(p.name)
        if missing:
            return fail(f"缺少参数: {', '.join(missing)}")
        return fn(**kwargs)

    return invoke


def _attach_tool(
    fn: Callable[..., Any],
    *,
    name: str | None = None,
    description: str | None = None,
    input_schema: dict[str, Any] | None = None,
) -> Callable[..., Any]:
    tool_name = (name or fn.__name__).strip()
    desc = (description or _doc_first_line(fn)).strip() or tool_name
    schema = input_schema if input_schema is not None else _schema_from_signature(fn)
    meta = {
        "name": tool_name,
        "description": desc,
        "inputSchema": schema or EMPTY_SCHEMA,
        "handler": _make_invoke(fn),
    }
    setattr(fn, "_mcp_tool", meta)
    return fn


class _McpAPI:
    def tool(
        self,
        name: str | Callable[..., Any] | None = None,
        description: str | None = None,
        *,
        input_schema: dict[str, Any] | None = None,
    ) -> Any:
        """注册工具。

        - ``@mcp.tool()``：工具名=函数名，说明=docstring 首行，参数=类型注解
        - ``@mcp.tool(name="xxx", description="...")``
        - ``@mcp.tool``：无括号亦可
        """
        if callable(name) and description is None and input_schema is None:
            return _attach_tool(name)

        tool_name = name if isinstance(name, str) else None

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            return _attach_tool(
                fn,
                name=tool_name,
                description=description,
                input_schema=input_schema,
            )

        return decorator


mcp = _McpAPI()


def resolve_program(key: str) -> Path | None:
    """读取「MCP 设置」中的程序路径（绝对路径或 PATH 命令名）。"""
    configured = get_program_path(key)
    if not configured:
        return None
    path = Path(configured)
    if path.is_file():
        return path
    found = shutil.which(configured)
    return Path(found) if found else None


def require_program(key: str) -> Path | dict[str, Any]:
    """获取已配置程序路径；未配置时返回 fail(...) 字典，便于直接 return。"""
    path = resolve_program(key)
    if path is None:
        return fail(
            f"未配置「{key}」路径，或路径无效。请在网页「MCP 设置」中填写",
            program_key=key,
        )
    return path


def is_process_running(*image_names: str) -> bool:
    """Windows：按映像名判断进程是否在跑。"""
    if sys.platform != "win32" or not image_names:
        return False
    try:
        for image in image_names:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if image.lower() in out.stdout.lower():
                return True
    except (OSError, subprocess.SubprocessError):
        return False
    return False


def launch_program(
    program_key: str,
    *,
    process_names: list[str] | None = None,
    args: list[str] | None = None,
    skip_if_running: bool = True,
    label: str | None = None,
) -> dict[str, Any]:
    """按「MCP 设置」中的路径启动程序（最常用的通用能力）。"""
    display = label or program_key
    names = process_names or []
    if skip_if_running and names and is_process_running(*names):
        return ok(message=f"{display} 已在运行", already_running=True)

    exe = resolve_program(program_key)
    if not exe:
        return fail(
            f"未配置「{program_key}」路径，或路径无效。请在网页「MCP 设置」中填写",
            program_key=program_key,
        )

    cmd = [str(exe), *(args or [])]
    try:
        subprocess.Popen(cmd, shell=False)  # noqa: S603
        return ok(message=f"已启动 {display}", path=str(exe), already_running=False)
    except OSError as exc:
        return fail(f"启动失败: {exc}", path=str(exe))


def ok(data: Any = None, **extra: Any) -> dict[str, Any]:
    """成功返回（建议工具统一用它）。"""
    result: dict[str, Any] = {"ok": True}
    if isinstance(data, dict):
        result.update(data)
    elif data is not None:
        result["data"] = data
    result.update(extra)
    return result


def fail(message: str, **extra: Any) -> dict[str, Any]:
    """失败返回。"""
    return {"ok": False, "message": message, **extra}


def iter_decorated_tools(module: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for attr in dir(module):
        if attr.startswith("_"):
            continue
        obj = getattr(module, attr, None)
        meta = getattr(obj, "_mcp_tool", None) if callable(obj) else None
        if not isinstance(meta, dict) or not meta.get("name"):
            continue
        name = str(meta["name"])
        if name in seen:
            continue
        seen.add(name)
        found.append(dict(meta))
    return found
