# -*- coding: utf-8 -*-
"""手动启动 MCP 本地桥接。

用法（在项目根目录）::

    python -m mcp.startmcpserver
    python mcp/startmcpserver/start_mcp.py
    mcp\\startmcpserver\\start_mcp.bat

可选参数与 bridge 相同，例如 ``--port 18765``。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from mcp.server.runtime.bridge import main as bridge_main

    return bridge_main()


if __name__ == "__main__":
    raise SystemExit(main())
