# -*- coding: utf-8 -*-
"""MCP 本地桥接启动入口（手动启动，不会随 Django 自动拉起）。"""

from .start_mcp import main

__all__ = ["main"]
