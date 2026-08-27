# -*- coding: utf-8 -*-
"""本机 QQ 工具：启动 QQ、读取联系人（Windows）。"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from mcp.toolkit import fail, launch_program, mcp, ok, program

PROGRAMS = [
    program(
        "qq_exe",
        "QQ 可执行文件",
        hint="请填写本机 QQ.exe / QQNT 完整路径",
    ),
]


@mcp.tool(name="qq_start")
def start_qq() -> dict[str, Any]:
    """启动本机 QQ 客户端（需在 MCP 设置中填写 QQ 路径并启动本地桥接）。"""
    return launch_program(
        "qq_exe",
        process_names=["QQ.exe", "QQScLauncher.exe"],
        label="QQ",
    )


@mcp.tool(name="qq_get_contacts")
def get_contacts() -> dict[str, Any]:
    """获取本机 QQ 联系人列表（uin、昵称、备注；需 QQ 已登录且本地桥接已启动）。"""
    root = Path.home() / "Documents" / "Tencent Files"
    if not root.is_dir():
        return fail(
            f"未找到 Tencent Files 目录: {root}",
            hint="请先在本机登录 QQ，并在网页 MCP 设置中启动本地桥接",
        )

    dbs = _iter_contact_dbs(root)
    if not dbs:
        return fail(
            "未找到 QQ 联系人数据库",
            hint="请确保 QQ 已登录；新版 QQNT 可能加密数据库，经典 PC 版支持更好",
        )

    all_contacts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    used_dbs: list[str] = []

    for db in dbs[:8]:
        batch = _read_contacts_from_db(db)
        if not batch:
            continue
        used_dbs.append(str(db))
        for c in batch:
            key = (c.get("uin", ""), c.get("nick", ""))
            if key in seen:
                continue
            seen.add(key)
            all_contacts.append(c)

    if not all_contacts:
        return fail(
            "找到了数据库文件但未能解析联系人",
            scanned_dbs=[str(p) for p in dbs[:8]],
            hint="QQNT 新版可能无法直接读取；可尝试经典 QQ 或确认已登录",
        )

    return ok(count=len(all_contacts), contacts=all_contacts, source_dbs=used_dbs)


def _iter_contact_dbs(root: Path) -> list[Path]:
    names = ("Msg3.0.db", "Msg3.0index.db", "buddy.db", "Friends.db", "contact.db")
    found: list[Path] = []
    for path in root.rglob("*.db"):
        if path.name in names or "msg" in path.name.lower() or "friend" in path.name.lower():
            found.append(path)
    found.sort(key=lambda p: (p.name != "Msg3.0.db", len(str(p))))
    return found


def _open_sqlite(path: Path) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        pass
    try:
        tmp = Path(tempfile.gettempdir()) / f"mcp_qq_{path.name}"
        shutil.copy2(path, tmp)
        return sqlite3.connect(str(tmp))
    except OSError:
        return None


def _rows_to_contacts(rows: list[tuple], columns: list[str]) -> list[dict[str, Any]]:
    contacts: list[dict[str, Any]] = []
    for row in rows:
        item = dict(zip(columns, row))
        uin = item.get("QQUin") or item.get("uin") or item.get("qq") or item.get("QQ")
        nick = item.get("Nick") or item.get("nick") or item.get("name") or item.get("nickname")
        remark = item.get("Remark") or item.get("remark") or item.get("memo")
        if uin or nick or remark:
            contacts.append(
                {
                    "uin": str(uin) if uin is not None else "",
                    "nick": str(nick or ""),
                    "remark": str(remark or ""),
                }
            )
    return contacts


def _read_contacts_from_db(db_path: Path) -> list[dict[str, Any]]:
    conn = _open_sqlite(db_path)
    if not conn:
        return []
    contacts: list[dict[str, Any]] = []
    try:
        cur = conn.cursor()
        tables = [
            r[0]
            for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        for table in tables:
            tl = table.lower()
            if tl not in {"friend", "friends", "buddy", "buddys", "contact", "contacts"} and "friend" not in tl:
                continue
            try:
                cur.execute(f"SELECT * FROM [{table}] LIMIT 500")  # noqa: S608
                rows = cur.fetchall()
                if not rows:
                    continue
                columns = [d[0] for d in cur.description or []]
                contacts.extend(_rows_to_contacts(rows, columns))
            except sqlite3.Error:
                continue
    finally:
        conn.close()
    return contacts
