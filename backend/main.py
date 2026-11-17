# -*- coding: utf-8 -*-
"""
FastAPI 入口：lifespan 管理连接池 / checkpointer / agent，挂载路由与 CORS。

事件循环约定：主循环用 SelectorEventLoop（psycopg 3.x 不支持 Proactor），
微信 MCP 的 Proactor 子进程在 agent.mcp_wechat 的独立线程里运行。
"""
import asyncio
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import date

# psycopg 3.x 不支持 Windows ProactorEventLoop：必须在创建循环前设置策略
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # noqa: E402
from langgraph.store.postgres import AsyncPostgresStore  # noqa: E402
from loguru import logger  # noqa: E402

from agent.config import get_settings, setup_logging  # noqa: E402
from agent.db.pool import build_dsn, close_pool, get_pool  # noqa: E402
from agent.db.settings_dao import (  # noqa: E402
    apply_overrides,
    get_raw_settings,
    reset_default_read_dates,
    validate_effective_settings,
)
from agent.commute_refresh import start_commute_refresh, stop_commute_refresh  # noqa: E402
from agent.contact_interaction import (  # noqa: E402
    start_contact_interaction,
    stop_contact_interaction,
)
from agent.graph import build_agent  # noqa: E402
from agent.interaction import start_interaction, stop_interaction  # noqa: E402
from agent.models_factory import get_embeddings, is_model_configured, reset_models  # noqa: E402
from agent.tracking import start_tracking, stop_tracking  # noqa: E402
from backend.routers import (  # noqa: E402
    chat, commute, ingest, interaction, offers, reports, resume, sessions, settings,
    system, tracking, upload,
)
from backend.routers.upload import cleanup_uploads  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    #1.日志 / 连接池 / agent 初始化（初始化失败不崩服务，接口降级）
    setup_logging()
    app.state.agent = None
    app.state.startup_error = None
    app.state.pool = None
    app.state.checkpointer = None
    app.state.store = None
    stack = AsyncExitStack()
    try:
        pool = await get_pool()
        app.state.pool = pool
        #2.checkpointer 生命周期归 lifespan（setup 建表一次），并暴露给设置重建使用
        checkpointer = await stack.enter_async_context(
            AsyncPostgresSaver.from_conn_string(build_dsn())
        )
        await checkpointer.setup()
        app.state.checkpointer = checkpointer

        #2.5 默认读取日期只在当次运行有效：启动时重置为今天（先写库再读覆盖，内存配置一致）
        try:
            await reset_default_read_dates(pool, date.today().isoformat())
        except Exception:
            logger.warning("默认读取日期重置失败", exc_info=True)

        #3.应用 app_settings 中的 DB 覆盖（优先于 .env；无效则回退 .env）
        try:
            data = await get_raw_settings(pool)
            if data:
                get_settings.cache_clear()
                apply_overrides(get_settings(), data)
                validate_effective_settings(get_settings())
                logger.info("已应用 app_settings 覆盖：{}", sorted(data.keys()))
        except Exception:
            logger.warning("app_settings 覆盖无效，回退 .env 配置", exc_info=True)
            get_settings.cache_clear()
            reset_models()

        #3.5 长期记忆 store（在 apply_overrides 之后创建：index 依赖 DB 覆盖后的 embedding 配置）
        try:
            index = None
            if is_model_configured("embedding"):
                index = {
                    "embed": get_embeddings(),
                    "dims": get_settings().embedding_dims,
                    "fields": ["text"],
                }
            store = await stack.enter_async_context(
                AsyncPostgresStore.from_conn_string(build_dsn(), index=index)
            )
            await store.setup()
            app.state.store = store
            logger.info("长期记忆 store 就绪（indexed={}）", bool(index))
        except Exception:
            logger.warning("长期记忆 store 初始化失败，降级为仅显式偏好", exc_info=True)

        app.state.agent = build_agent(checkpointer=checkpointer, store=app.state.store)
        logger.info("Agent 初始化完成")

        #4.清理过期上传截图（best-effort，失败不影响启动）
        try:
            cleanup_uploads(max_age_days=7)
        except Exception:
            logger.warning("清理过期上传文件失败", exc_info=True)

        #5.长期跟踪调度器（每轮从 DB 读 preferences；advisory lock 保证单实例）
        start_tracking(app)
        #6.微信互动 worker（轮询目标会话；独立开关与间隔）
        start_interaction(app)
        #6.5 中介互动 worker（轮询中介会话，草拟回复经用户确认后发送）
        start_contact_interaction(app)
        #7.通勤重算 worker（设置页修改起点后触发全库重算）
        start_commute_refresh(app)
    except Exception as e:
        logger.exception("Agent 初始化失败")
        app.state.startup_error = str(e)
    yield
    #8.清理：先停后台任务，再关 checkpointer / 连接池
    await stop_commute_refresh(app)
    await stop_contact_interaction(app)
    await stop_interaction(app)
    await stop_tracking(app)
    await stack.aclose()
    await close_pool()


app = FastAPI(title="YourSpecialJob", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(ingest.router)
app.include_router(settings.router)
app.include_router(sessions.router)
app.include_router(upload.router)
app.include_router(reports.router)
app.include_router(offers.router)
app.include_router(tracking.router)
app.include_router(interaction.router)
app.include_router(system.router)
app.include_router(commute.router)
app.include_router(resume.router)


@app.get("/api/health")
async def health():
    """健康检查：PG 连通 / 模型配置就绪 / agent 状态（不调用 LLM）"""
    db_ok = False
    try:
        pool = await get_pool()
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
        db_ok = True
    except Exception as e:
        logger.warning("health 检查 PG 失败：{}", e)

    tracking_state = getattr(app.state, "tracking_state", None)
    interaction_state = getattr(app.state, "interaction_state", None)
    contact_interaction_state = getattr(app.state, "contact_interaction_state", None)
    store = getattr(app.state, "store", None)
    return {
        "status": "ok" if app.state.agent is not None else "degraded",
        "db": db_ok,
        "models": {
            kind: is_model_configured(kind)
            for kind in ("strong", "weak", "backup", "embedding")
        },
        "tracking": tracking_state.snapshot() if tracking_state else None,
        "interaction": interaction_state.snapshot() if interaction_state else None,
        "contact_interaction": contact_interaction_state.snapshot() if contact_interaction_state else None,
        "memory": {
            "enabled": store is not None,
            "indexed": bool(getattr(store, "index_config", None)) if store else False,
        },
        "error": app.state.startup_error,
    }