# -*- coding: utf-8 -*-
"""pytest 公共夹具：独立测试库 yourspecialjob_test（不碰开发库），每个测试前清空 jobs 表。"""
import asyncio
import sys
from pathlib import Path

# psycopg 3.x 不支持 Windows ProactorEventLoop，测试统一用 Selector 循环
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import psycopg
import pytest
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool

from agent.config import get_settings

TEST_DB = "yourspecialjob_test"
SCHEMA_FILE = Path(__file__).resolve().parent.parent / "agent" / "db" / "schema.sql"


def _dsn(dbname: str) -> str:
    s = get_settings()
    return f"postgresql://{s.postgres_user}@{s.postgres_host}:{s.postgres_port}/{dbname}"


def ensure_test_db() -> str:
    """确保测试库存在并执行 schema.sql（幂等），返回测试库 DSN"""
    with psycopg.connect(_dsn("postgres"), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)
        ).fetchone()
        if not exists:
            conn.execute(f"CREATE DATABASE {TEST_DB}")
    dsn = _dsn(TEST_DB)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
    return dsn


@pytest_asyncio.fixture
async def pool():
    """独立连接池夹具：指向测试库，创建 → 清空 jobs 表 → 交给测试 → 关闭"""
    dsn = ensure_test_db()
    p = AsyncConnectionPool(
        conninfo=dsn,
        min_size=1,
        max_size=2,
        kwargs={"autocommit": True},
        open=False,
    )
    await p.open(wait=True, timeout=10)
    async with p.connection() as conn:
        await conn.execute(
            "TRUNCATE TABLE resume_chunks, resume_files, "
            "interaction_pending, interaction_state, "
            "push_log, wechat_seen_messages, tracking_group_state, "
            "jobs, app_settings, sessions RESTART IDENTITY CASCADE"
        )
    yield p
    await p.close()


class FakeCheckpointer:
    """测试用 checkpointer：记录 adelete_thread 调用"""

    def __init__(self):
        self.deleted: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted.append(thread_id)


@pytest.fixture
def fake_checkpointer() -> FakeCheckpointer:
    return FakeCheckpointer()


@pytest.fixture
def api_app(pool, fake_checkpointer):
    """挂到真实 FastAPI app 的测试状态（不走 lifespan，不建真实 checkpointer）"""
    import backend.main as main

    app = main.app
    app.state.pool = pool
    app.state.agent = None
    app.state.checkpointer = fake_checkpointer
    app.state.startup_error = None
    yield app
    # 恢复全局配置状态，避免污染其他测试
    from agent.models_factory import reset_models

    get_settings.cache_clear()
    reset_models()


@pytest_asyncio.fixture
async def api_client(api_app):
    """httpx ASGI 客户端（同一事件循环内，避免跨循环对象问题）"""
    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client