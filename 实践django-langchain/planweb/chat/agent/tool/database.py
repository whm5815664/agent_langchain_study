"""数据库查询工具与数据库子智能体配置。"""

import json

import pymysql
from langchain_core.tools import tool

DB = {
    "host": "116.62.214.146",
    "port": 3306,
    "user": "wyh22",
    "password": "wyh123456",
    "database": "pig",
    "charset": "utf8mb4",
}


def connect_db():
    """获取数据库连接。"""
    return pymysql.connect(**DB, cursorclass=pymysql.cursors.DictCursor)


@tool
def query_environment(where: str = "", limit: int = 20) -> str:
    """
    功能:查询environment_data环境数据，按collected_time倒序。
        业务说明:pig库对应柑橘褪绿库与冷藏库；pigsty_id是库编号（基地编号）。
        用户说某号库/褪绿库/冷藏库时，用pigsty_id作为条件。
        字段映射:
            id:主键
            pigsty_id:库编号（褪绿库/冷藏库）
            device_id:采集设备编号
            collected_time:数据采集时间
            updated_at:记录更新时间
            temperature:库外/环境温度(℃)
            humidity:库外/环境湿度(%RH)
            temperature_inner:库内温度(℃)
            humidity_inner:库内湿度(%RH)
            CO2:二氧化碳浓度
            O2:氧气浓度
            C2H4:乙烯浓度
            C2H5OH:乙醇浓度
            CO:一氧化碳浓度
            H2:氢气浓度
            VOC:挥发性有机物浓度
            image:关联图片路径
    Args:
        where:查询条件，可为空（where不含WHERE关键字，例如:pigsty_id = 1 AND temperature > 25）
        limit:返回条数，默认20
    return:
        环境数据记录的JSON字符串。
    """
    limit = max(1, min(int(limit), 100))
    sql = "SELECT * FROM environment_data"
    if where.strip():
        sql += f" WHERE {where.strip()}"
    sql += f" ORDER BY collected_time DESC LIMIT {limit}"
    try:
        conn = connect_db()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
            return json.dumps(rows, ensure_ascii=False, default=str)
        finally:
            conn.close()
    except Exception as e:
        return f"查询失败：{e}"


@tool
def view_monitor(where: str = "", limit: int = 5) -> str:
    """
    功能:查看库内、基地内的监控图片。从environment_data读取image，拼成可访问URL。
        用户说查看基地监控/库房监控时使用。
        主要字段映射:
            pigsty_id:库编号（褪绿库/冷藏库/基地）
            device_id:采集设备编号
            collected_time:数据采集时间
            image:关联图片路径
    Args:
        where:查询条件，可为空（不含WHERE关键字，例如:pigsty_id = 1）
        limit:最多返回几个设备的图片，默认5
    return:
        含 image_url 与 markdown 的JSON字符串。
    """
    limit = max(1, min(int(limit), 20))
    sql = (
        "SELECT id, pigsty_id, device_id, collected_time, image "
        "FROM environment_data WHERE image IS NOT NULL AND image != ''"
    )
    if where.strip():
        sql += f" AND ({where.strip()})"
    sql += " ORDER BY collected_time DESC"
    try:
        conn = connect_db()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as e:
        return f"查询监控失败：{e}"

    seen = set()
    result = []
    for row in rows:
        device_id = row["device_id"]
        if device_id in seen:
            continue
        seen.add(device_id)
        row["image_url"] = "http://116.62.214.146:8175/media/" + row["image"]
        row["markdown"] = f"![{row['pigsty_id']}号库设备{device_id}监控]({row['image_url']})"
        result.append(row)
        if len(result) >= limit:
            break
    return json.dumps(result, ensure_ascii=False, default=str)


# deepagents 子智能体：解析条件并选择查询工具
DATABASE_SUBAGENT = {
    "name": "database",
    "display_name": "数据库智能体",
    "description": "查询柑橘褪绿库/冷藏库的环境数据与监控图片。",
    "system_prompt": (
        "你是数据库查询助手。业务库 pig 对应柑橘褪绿库与冷藏库，"
        "pigsty_id 为库编号。按用户意图补全条件并选择工具，"
        "用中文简要汇总，勿编造数据，只返回数据库中已有的数据。"
        "可用工具："
        "1. query_environment"
        "   - 功能：查询 environment_data 环境数据（温湿度、气体等），按采集时间倒序；"
        "   - 参数：where（不含 WHERE，可空）；limit（默认 20）；"
        "2. view_monitor\n"
        "   - 功能：查看库内监控图片，按设备返回最新一张；"
        "   - 参数：where（不含 WHERE，可空）；limit（默认 5）；"
        "   - 注意：汇总时原样保留返回中的 markdown 图片语法；"
    ),
    "tools": [query_environment, view_monitor],
}
