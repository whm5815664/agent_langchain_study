"""MCP：client/ = Cloud Agent，server/ = Edge + 本机桥接，tools/ = 用户工具。

编写工具::
    from mcp.toolkit import mcp, program, launch_program, ok, fail
"""

from mcp.client import EdgeMcpClient, EdgeMcpError, edge_registry, to_langchain_tools

__all__ = ["EdgeMcpClient", "EdgeMcpError", "edge_registry", "to_langchain_tools"]
