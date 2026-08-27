from django.apps import AppConfig


class McpConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mcp"
    verbose_name = "MCP (Cloud Agent ↔ Edge)"

    def ready(self) -> None:
        # 仅生成工具清单；本地桥接需用户手动启动（见 mcp/startmcpserver/）
        try:
            from mcp.server.runtime import build_manifest

            build_manifest(write=True)
        except Exception:
            pass
