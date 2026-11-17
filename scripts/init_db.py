# -*- coding: utf-8 -*-
"""
初始化数据库（幂等，可重复执行）：
1. 确保 yourspecialjob 库存在
2. 启用 pgvector 扩展
3. 建自管表（agent/db/schema.sql，表不存在时才建）

前置：先运行 scripts/start_pg.py 启动 PostgreSQL。
用法：python scripts/init_db.py
"""
import os
import subprocess
import sys
from pathlib import Path

# Windows 控制台默认 GBK，统一切换 UTF-8 输出避免乱码
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

#1.路径常量
BINDIR = Path(
    os.getenv("PG_BINDIR")
    or (Path.home() / ".conda" / "envs" / "SggYourSpecialJobPG" / "Library" / "bin")
)
ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "agent" / "db" / "schema.sql"

DB_HOST = "localhost"
DB_PORT = "5432"
DB_USER = "postgres"
DB_NAME = "yourspecialjob"


def psql(sql: str, dbname: str = "postgres") -> subprocess.CompletedProcess:
    """执行一段 SQL 并返回结果"""
    return subprocess.run(
        [str(BINDIR / "psql.exe"), "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER,
         "-d", dbname, "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def main() -> int:
    #2.建库（不存在时）
    r = psql(f"SELECT 1 FROM pg_database WHERE datname = '{DB_NAME}'")
    if r.returncode != 0:
        print("连接 PG 失败:", r.stderr)
        return 1
    if "1" not in r.stdout:
        r = psql(f"CREATE DATABASE {DB_NAME}")
        if r.returncode != 0:
            print("建库失败:", r.stderr)
            return 1
        print(f"已创建数据库 {DB_NAME}")
    else:
        print(f"数据库 {DB_NAME} 已存在")

    #3.启用 pgvector 扩展
    r = psql("CREATE EXTENSION IF NOT EXISTS vector", DB_NAME)
    if r.returncode != 0:
        print("启用 pgvector 失败:", r.stderr)
        return 1
    r = psql("SELECT extversion FROM pg_extension WHERE extname = 'vector'", DB_NAME)
    print("pgvector 版本:", r.stdout.strip())

    #4.建自管表（幂等：IF NOT EXISTS）
    if SCHEMA_FILE.exists():
        r = subprocess.run(
            [str(BINDIR / "psql.exe"), "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER,
             "-d", DB_NAME, "-v", "ON_ERROR_STOP=1", "-f", str(SCHEMA_FILE)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if r.returncode != 0:
            print("执行 schema.sql 失败:", r.stderr)
            return 1
        print("schema.sql 执行完成")
    else:
        print(f"提示：{SCHEMA_FILE} 尚不存在（M1 阶段创建），跳过建表")

    print("init_db 完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
