"""
MySQL 计划清单建表脚本。

修改下方 MYSQL_* 参数后运行：
    python database.py
将自动创建数据库（若不存在）及 plans / plan_runs 两张表。
依赖：pip install pymysql
"""

import uuid

import pymysql

# =============================================================================
# 连接参数（按环境修改）
# =============================================================================

MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = 3306
MYSQL_USER = "root"
MYSQL_PASSWORD = "your_password"
MYSQL_DATABASE = "agent_plans"
MYSQL_CHARSET = "utf8mb4"

# =============================================================================
# DDL
# =============================================================================

DDL_PLANS = """
CREATE TABLE IF NOT EXISTS plans (
    id              VARCHAR(32)  NOT NULL COMMENT '命名规则 plan_<16位hex>，如 plan_a1b2c3d4e5f67890',
    title           VARCHAR(255) NOT NULL DEFAULT '' COMMENT '标题',
    content         TEXT         NOT NULL COMMENT '到点执行任务正文',
    status          VARCHAR(32)  NOT NULL DEFAULT 'pending'
                    COMMENT 'pending/in_progress/completed/cancelled/paused',
    schedule_type   VARCHAR(32)  NOT NULL DEFAULT ''
                    COMMENT 'interval/periodic/once',
    schedule_json   TEXT         NULL
                    COMMENT '调度参数JSON: interval_minutes,period,weekday,day_of_month,hour,minute,run_at',
    schedule_desc   VARCHAR(255) NOT NULL DEFAULT '' COMMENT '可读调度描述',
    max_runs        INT          NOT NULL DEFAULT 0 COMMENT '最大次数，0=无限',
    run_count       INT          NOT NULL DEFAULT 0 COMMENT '已执行次数',
    next_run        VARCHAR(32)  NOT NULL DEFAULT '-' COMMENT '下次触发时间',
    last_result     MEDIUMTEXT   NULL COMMENT '最近一次结果摘要',
    enabled         TINYINT      NOT NULL DEFAULT 1 COMMENT '1可调度/0停用',
    thread_id       VARCHAR(128) NULL COMMENT '可选，对话会话 id',
    created_at      VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '创建时间',
    updated_at      VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '最后更新时间',
    PRIMARY KEY (id),
    KEY idx_plans_status (status),
    KEY idx_plans_title (title),
    KEY idx_plans_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='计划主表';
"""

DDL_PLAN_RUNS = """
CREATE TABLE IF NOT EXISTS plan_runs (
    id           VARCHAR(32)  NOT NULL COMMENT '命名规则 run_<16位hex>，如 run_9f8e7d6c5b4a3210',
    plan_id      VARCHAR(32)  NOT NULL COMMENT '关联 plans.id，同为 plan_<16位hex>',
    run_no       INT          NOT NULL COMMENT '第几次执行',
    started_at   VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '开始时间',
    finished_at  VARCHAR(32)  NOT NULL DEFAULT '' COMMENT '结束时间',
    ok           TINYINT      NOT NULL DEFAULT 1 COMMENT '1成功/0失败',
    result       MEDIUMTEXT   NULL COMMENT '本轮执行结果',
    error        TEXT         NULL COMMENT '失败信息',
    PRIMARY KEY (id),
    UNIQUE KEY uk_plan_runs_plan_no (plan_id, run_no),
    KEY idx_plan_runs_plan_id (plan_id),
    CONSTRAINT fk_plan_runs_plan
        FOREIGN KEY (plan_id) REFERENCES plans (id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='计划执行历史';
"""


def _hex16() -> str:
    return uuid.uuid4().hex[:16]


def new_plan_id() -> str:
    """计划 id：plan_<16位hex>，如 plan_a1b2c3d4e5f67890。"""
    return f"plan_{_hex16()}"


def new_run_id() -> str:
    """执行记录 id：run_<16位hex>，如 run_9f8e7d6c5b4a3210。"""
    return f"run_{_hex16()}"


def get_connection(*, with_database: bool = True):
    kwargs = dict(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        charset=MYSQL_CHARSET,
        autocommit=True,
    )
    if with_database:
        kwargs["database"] = MYSQL_DATABASE
    return pymysql.connect(**kwargs)


def ensure_database() -> None:
    conn = get_connection(with_database=False)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` "
                f"DEFAULT CHARACTER SET {MYSQL_CHARSET} "
                f"COLLATE {MYSQL_CHARSET}_unicode_ci"
            )
        print(f"[ok] database `{MYSQL_DATABASE}` ready")
    finally:
        conn.close()


def init_tables() -> None:
    conn = get_connection(with_database=True)
    try:
        with conn.cursor() as cur:
            cur.execute(DDL_PLANS)
            print("[ok] table `plans` ready")
            cur.execute(DDL_PLAN_RUNS)
            print("[ok] table `plan_runs` ready")
    finally:
        conn.close()


def init_db() -> None:
    ensure_database()
    init_tables()
    print(
        f"[done] {MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE} "
        f"— plans / plan_runs initialized"
    )


if __name__ == "__main__":
    init_db()
