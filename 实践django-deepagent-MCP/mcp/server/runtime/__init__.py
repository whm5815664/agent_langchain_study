"""Edge MCP 运行时。

编写工具::
    from mcp.toolkit import mcp, program, launch_program, ok, fail
"""

from .bridge import status
from .catalog import bridge_base_url, build_manifest, discover_program_fields, load_config, save_config
from .toolkit import fail, launch_program, mcp, ok, program, require_program

__all__ = [
    "bridge_base_url",
    "build_manifest",
    "discover_program_fields",
    "fail",
    "launch_program",
    "load_config",
    "mcp",
    "ok",
    "program",
    "require_program",
    "save_config",
    "status",
]
