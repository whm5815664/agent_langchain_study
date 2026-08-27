# -*- coding: utf-8 -*-
"""工具目录：本地配置、自动发现、manifest 生成。"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

_lock = threading.Lock()

RUNTIME_DIR = Path(__file__).resolve().parent
SERVER_DIR = RUNTIME_DIR.parent
TOOLS_DIR = SERVER_DIR / "tools"
PROJECT_ROOT = RUNTIME_DIR.parents[2]
CONFIG_FILE = PROJECT_ROOT / "mcp_local_config.json"
BROWSER_TOOLS_FILE = RUNTIME_DIR / "browser_tools.json"
MANIFEST_FILE = SERVER_DIR / "static" / "mcp" / "manifest.json"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18765
DEFAULT_CONFIG: dict[str, Any] = {
    "host": DEFAULT_HOST,
    "port": DEFAULT_PORT,
    "programs": {},
}
SKIP_MODULES = frozenset({"__init__"})

ToolHandler = Callable[[dict[str, Any]], Any]
ToolMeta = dict[str, Any]

_program_fields_cache: dict[str, Any] = {"sig": "", "fields": []}


# ---------- 配置 ----------

def _normalize(data: dict[str, Any] | None) -> dict[str, Any]:
    raw = data if isinstance(data, dict) else {}
    host = str(raw.get("host") or DEFAULT_HOST).strip() or DEFAULT_HOST
    try:
        port = int(raw.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    if not (1 <= port <= 65535):
        port = DEFAULT_PORT

    programs: dict[str, str] = {}
    programs_raw = raw.get("programs")
    if isinstance(programs_raw, dict):
        for key, value in programs_raw.items():
            k = str(key).strip()
            if k:
                programs[k] = str(value or "").strip()
    return {"host": host, "port": port, "programs": programs}


def load_config() -> dict[str, Any]:
    with _lock:
        if not CONFIG_FILE.is_file():
            return dict(DEFAULT_CONFIG)
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return dict(DEFAULT_CONFIG)
        return _normalize(data if isinstance(data, dict) else None)


def save_config(data: dict[str, Any]) -> dict[str, Any]:
    cfg = _normalize(data)
    with _lock:
        CONFIG_FILE.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return cfg


def bridge_base_url(cfg: dict[str, Any] | None = None) -> str:
    c = cfg or load_config()
    return f"http://{c['host']}:{c['port']}"


def get_program_path(key: str) -> str:
    programs = load_config().get("programs") or {}
    return str(programs.get(key) or "").strip()


# ---------- 发现 tools/*.py ----------

def _load_module(path: Path):
    name = f"mcp_edge_tool_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _resolve_handler(module, spec: dict[str, Any]) -> ToolHandler | None:
    handler = spec.get("handler")
    if callable(handler):
        return handler
    handler_name = spec.get("handler_name") or handler
    if isinstance(handler_name, str) and handler_name:
        fn = getattr(module, handler_name, None)
        if callable(fn):
            return fn
    return None


def _public_meta(spec: dict[str, Any]) -> ToolMeta:
    return {
        k: v
        for k, v in spec.items()
        if k not in {"handler", "handler_name"} and not k.startswith("_")
    }


def discover_local_tool_modules(tools_dir: Path | None = None) -> list[tuple[Any, Path]]:
    root = tools_dir or TOOLS_DIR
    modules: list[tuple[Any, Path]] = []
    if not root.is_dir():
        return modules
    for path in sorted(root.glob("*.py")):
        if path.stem in SKIP_MODULES or path.stem.startswith("_"):
            continue
        try:
            modules.append((_load_module(path), path))
        except Exception as exc:
            print(f"[catalog] 跳过 {path.name}: {exc}", file=sys.stderr)
    return modules


def discover_local_tools(tools_dir: Path | None = None) -> tuple[list[ToolMeta], dict[str, ToolHandler]]:
    from .toolkit import iter_decorated_tools

    metas: list[ToolMeta] = []
    handlers: dict[str, ToolHandler] = {}
    for module, path in discover_local_tool_modules(tools_dir):
        for raw in iter_decorated_tools(module):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if not name:
                continue
            handler = _resolve_handler(module, raw)
            if handler is None:
                print(f"[catalog] {path.name}: 工具 {name} 缺少 handler", file=sys.stderr)
                continue
            meta = _public_meta(raw)
            meta.setdefault("name", name)
            meta.setdefault("description", name)
            meta.setdefault("inputSchema", {"type": "object", "properties": {}})
            meta["runtime"] = "local"
            meta["_source"] = path.name
            metas = [m for m in metas if m.get("name") != name]
            metas.append(meta)
            handlers[name] = handler
    return metas, handlers


def _tools_mtime_sig(tools_dir: Path) -> str:
    parts: list[str] = []
    if not tools_dir.is_dir():
        return ""
    for path in sorted(tools_dir.glob("*.py")):
        if path.name.startswith("_") or path.stem == "__init__":
            continue
        try:
            st = path.stat()
            parts.append(f"{path.name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            continue
    return "|".join(parts)


def discover_program_fields(tools_dir: Path | None = None) -> list[dict[str, str]]:
    root = tools_dir or TOOLS_DIR
    sig = _tools_mtime_sig(root)
    if sig and sig == _program_fields_cache.get("sig"):
        return list(_program_fields_cache["fields"])

    fields: list[dict[str, str]] = []
    seen: set[str] = set()
    for module, path in discover_local_tool_modules(root):
        programs = getattr(module, "PROGRAMS", None)
        if not programs:
            continue
        if isinstance(programs, dict):
            programs = [programs]
        for item in programs:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            fields.append(
                {
                    "key": key,
                    "label": str(item.get("label") or key),
                    "hint": str(item.get("hint") or ""),
                    "source": path.name,
                }
            )
    _program_fields_cache["sig"] = sig
    _program_fields_cache["fields"] = fields
    return list(fields)


# ---------- manifest ----------

def _load_browser_tools() -> list[dict]:
    if not BROWSER_TOOLS_FILE.is_file():
        return []
    data = json.loads(BROWSER_TOOLS_FILE.read_text(encoding="utf-8"))
    tools = data.get("tools") if isinstance(data, dict) else data
    if not isinstance(tools, list):
        return []
    result: list[dict] = []
    for item in tools:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        meta = {k: v for k, v in item.items() if not str(k).startswith("_")}
        meta["runtime"] = "browser"
        meta.setdefault("inputSchema", {"type": "object", "properties": {}})
        result.append(meta)
    return result


def build_manifest(*, tools_dir: Path | None = None, write: bool = True) -> dict:
    root = tools_dir or TOOLS_DIR
    browser_tools = _load_browser_tools()
    local_metas, _ = discover_local_tools(root)
    local_public = [{k: v for k, v in m.items() if not str(k).startswith("_")} for m in local_metas]
    manifest = {"tools": browser_tools + local_public}
    if write:
        MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_FILE.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return manifest


def main() -> int:
    manifest = build_manifest(write=True)
    names = [t["name"] for t in manifest.get("tools", [])]
    print(f"已生成 {MANIFEST_FILE}，共 {len(names)} 个工具:")
    for name in names:
        print(f"  - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
