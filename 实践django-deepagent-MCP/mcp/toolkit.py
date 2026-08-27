# -*- coding: utf-8 -*-
"""本地 MCP 工具编写入口（请从这里 import）。

最小示例::

    from mcp.toolkit import mcp, program, launch_program, ok

    PROGRAMS = [program("my_app", "我的程序", hint="填写 exe 路径")]

    @mcp.tool()
    def my_app_start() -> dict:
        \"\"\"启动我的程序。\"\"\"
        return launch_program("my_app", label="我的程序")

把文件放在 ``mcp/server/tools/*.py``（不要以下划线开头），保存后：
先在网页「MCP 设置」保存程序路径（如需），再运行 ``mcp/startmcpserver/start_mcp.bat``（或 ``python -m mcp.startmcpserver``）启动桥接，刷新页面即可。
"""

from mcp.server.runtime.toolkit import (  # noqa: F401
    EMPTY_SCHEMA,
    fail,
    is_process_running,
    launch_program,
    mcp,
    ok,
    program,
    require_program,
    resolve_program,
    schema_props,
)

__all__ = [
    "EMPTY_SCHEMA",
    "fail",
    "is_process_running",
    "launch_program",
    "mcp",
    "ok",
    "program",
    "require_program",
    "resolve_program",
    "schema_props",
]
