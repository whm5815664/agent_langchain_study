# -*- coding: utf-8 -*-
"""【模板】复制本文件并去掉文件名开头的下划线后才会被自动加载。

用法：
1. 复制为 MyApp.py（不要 _ 开头）
2. 修改 PROGRAMS / @mcp.tool
3. 网页「MCP 设置」填写路径 → 启动 MCP → 刷新页面

导入统一使用::

    from mcp.toolkit import mcp, program, launch_program, ok, fail
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.toolkit import fail, launch_program, mcp, ok, program

# 出现在「MCP 设置」里的路径配置项（用户自己填）
PROGRAMS = [
    program("demo_app", "示例程序", hint="填写可执行文件完整路径，或 PATH 中的命令名"),
]


@mcp.tool()
def demo_app_start() -> dict[str, Any]:
    """启动示例程序（需先在 MCP 设置中配置 demo_app 路径）。"""
    return launch_program("demo_app", label="示例程序")


@mcp.tool()
def demo_echo(text: Annotated[str, "要回显给 Agent 的文本"]) -> dict[str, Any]:
    """把用户/模型传入的文本原样返回（带参数工具示例）。"""
    if not text:
        return fail("text 不能为空")
    return ok(echo=text)
