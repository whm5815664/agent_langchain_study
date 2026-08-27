"""MCP tools/list → LangChain / deepagents Tool。"""

from __future__ import annotations

from typing import Any, Callable, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from .edge import EdgeMcpClient, EdgeMcpError

_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _schema_to_model(tool_name: str, input_schema: dict | None) -> type[BaseModel]:
    schema = input_schema if isinstance(input_schema, dict) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required") or [])
    fields: dict[str, Any] = {}
    for key, spec in props.items():
        if not isinstance(spec, dict):
            continue
        py_type = _TYPE_MAP.get(str(spec.get("type") or "string"), Any)
        desc = str(spec.get("description") or key)
        if key in required:
            fields[key] = (py_type, Field(description=desc))
        else:
            fields[key] = (Optional[py_type], Field(default=None, description=desc))
    safe = "".join(c if c.isalnum() else "_" for c in tool_name) or "tool"
    return create_model(f"Mcp_{safe}", **fields) if fields else create_model(f"Mcp_{safe}")


def to_langchain_tools(
    session_id: str,
    tools_meta: list[dict],
    *,
    on_call: Callable[[str, dict], None] | None = None,
    on_result: Callable[[str, str], None] | None = None,
) -> list[StructuredTool]:
    client = EdgeMcpClient(session_id)
    tools: list[StructuredTool] = []

    for meta in tools_meta:
        if not isinstance(meta, dict):
            continue
        name = str(meta.get("name") or "").strip()
        if not name:
            continue
        description = str(meta.get("description") or name)
        input_schema = meta.get("inputSchema") or meta.get("input_schema") or {}
        args_model = _schema_to_model(name, input_schema)

        def _make_invoke(tool_name: str):
            def _invoke(**kwargs: Any) -> str:
                arguments = {k: v for k, v in kwargs.items() if v is not None}
                if on_call:
                    on_call(tool_name, arguments)
                try:
                    result = client.call_tool(tool_name, arguments)
                except EdgeMcpError as exc:
                    result = f"Edge 调用失败: {exc}"
                if on_result:
                    on_result(tool_name, result)
                return result

            return _invoke

        tools.append(
            StructuredTool.from_function(
                func=_make_invoke(name),
                name=name,
                description=description,
                args_schema=args_model,
            )
        )
    return tools
