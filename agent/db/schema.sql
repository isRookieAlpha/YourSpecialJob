-- jobs 兼职表（M1：唯一业务表；血缘字段 + 结构化字段 + 去重约束）
-- 由 scripts/init_db.py 幂等执行（CREATE TABLE IF NOT EXISTS，可重复跑）

CREATE TABLE IF NOT EXISTS jobs (
    id              BIGSERIAL PRIMARY KEY,
    group_name      TEXT NOT NULL,                   -- 群名
    source          TEXT NOT NULL DEFAULT 'wechat_auto',  -- 血缘：wechat_auto / manual_text / manual_image
    source_ref      TEXT,                            -- 血缘：导出 json 文件名 / 手动输入标识
    raw_text        TEXT NOT NULL,                   -- 消息原文（血缘根基）
    content_hash    TEXT NOT NULL,                   -- group_name+原文归一化 sha256 前 16 位
    is_valid_job    BOOLEAN NOT NULL DEFAULT TRUE,
    work_summary    TEXT,                            -- 工作内容摘要
    is_physical_labor BOOLEAN,
    physical_intensity TEXT CHECK (physical_intensity IN ('轻', '中', '重')),
    work_date       DATE,                            -- 工作日期（缺省回填查询日期）
    work_time       TEXT,                            -- 工作时段，如 7:30-21:00
    publish_time    TIMESTAMPTZ,                     -- 消息发布时间（时刻+查询日期合成）
    commission_text TEXT,                            -- 佣金原文表述
    commission_min  NUMERIC(10, 2),                  -- '100左右' → 90
    commission_max  NUMERIC(10, 2),                  -- '100左右' → 110
    commission_unit TEXT CHECK (commission_unit IN ('元/时', '元/天', '元/次', '元/月')),
    location        TEXT,
    deposit         NUMERIC(10, 2),                  -- 押金
    travel_cost     NUMERIC(10, 2),                  -- 路费补贴
    is_daily_settlement BOOLEAN,
    settlement_method   TEXT,
    contact         TEXT,                            -- 报名联系方式
    publisher       TEXT,                            -- 发布者
    notes           TEXT,
    commute_bike_min INTEGER,                        -- 骑行通勤分钟数（amap 回填，M1 为空）
    commute_metro_min INTEGER,
    status          TEXT NOT NULL DEFAULT 'active',  -- active / completed / expired
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT jobs_group_hash_uk UNIQUE (group_name, content_hash)  -- 同群同文去重
);

CREATE INDEX IF NOT EXISTS idx_jobs_work_date ON jobs (work_date);
CREATE INDEX IF NOT EXISTS idx_jobs_commission_min ON jobs (commission_min);

-- M2.5：心动的offer / ToDo 状态（幂等迁移，已有库自动加列）
-- offer_status: none / favorite（心动的offer）/ todo（ToDo待办offer）
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS offer_status TEXT NOT NULL DEFAULT 'none';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS offer_updated_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'jobs_offer_status_chk') THEN
        ALTER TABLE jobs ADD CONSTRAINT jobs_offer_status_chk
            CHECK (offer_status IN ('none', 'favorite', 'todo'));
    END IF;
END $$;

-- M7：入库时计算并存储的通勤数据（距离/骑行/公共交通/驾车；MCP 高德）
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS commute_distance_m INTEGER;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS commute_transit_min INTEGER;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS commute_drive_min INTEGER;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS commute_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_jobs_offer_active
    ON jobs (offer_status) WHERE offer_status <> 'none';
CREATE INDEX IF NOT EXISTS idx_jobs_offer_todo_date
    ON jobs (work_date) WHERE offer_status = 'todo';

-- app_settings 单行 JSONB（设置页覆盖值，DB 优先于 .env）
CREATE TABLE IF NOT EXISTS app_settings (
    id          INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),   -- 固定单行
    data        JSONB NOT NULL DEFAULT '{}'::jsonb,         -- 嵌套：strong/weak/backup/embedding/preferences
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- sessions 会话元数据（消息历史在 langgraph checkpointer 表，这里只存标题与时间）
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,                           -- 即 checkpointer 的 thread_id
    title       TEXT NOT NULL DEFAULT '新会话',             -- 首条消息前 20 字
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions (updated_at DESC);

-- ===== M7：长期跟踪 =====
-- 消息指纹账本：按群+日期记录已处理的消息（前置增量过滤，避免每轮重跑弱模型）
CREATE TABLE IF NOT EXISTS wechat_seen_messages (
    fingerprint   TEXT PRIMARY KEY,                    -- sha256(group|day|sender|time|text)[:32]
    group_name    TEXT NOT NULL,
    seen_date     DATE NOT NULL,
    sender        TEXT,
    msg_time      TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_seen_group_date ON wechat_seen_messages (group_name, seen_date);
CREATE INDEX IF NOT EXISTS idx_seen_date ON wechat_seen_messages (seen_date);

-- 跟踪组状态：initialized_at 为 NULL 表示尚未完成首轮基线（基线只入库不推送）
CREATE TABLE IF NOT EXISTS tracking_group_state (
    group_name     TEXT PRIMARY KEY,
    initialized_at TIMESTAMPTZ,
    last_round_at  TIMESTAMPTZ,
    last_error     TEXT
);

-- 推送日志：同一 job 只推一次（主键 job_id+channel），失败最多重试 3 次
CREATE TABLE IF NOT EXISTS push_log (
    job_id          BIGINT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    channel         TEXT NOT NULL DEFAULT 'wechat',
    status          TEXT NOT NULL CHECK (status IN ('sent', 'failed', 'skipped')),
    attempts        INT NOT NULL DEFAULT 0,
    batch_id        TEXT,
    message_preview TEXT,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ,
    PRIMARY KEY (job_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_push_log_status ON push_log (status, updated_at);

-- ===== 微信互动 =====
CREATE TABLE IF NOT EXISTS interaction_state (
    conversation   TEXT PRIMARY KEY,               -- 互动会话名（wechat_target / WECHAT_SENDER_ID）
    initialized_at TIMESTAMPTZ,                    -- 首轮基线（只记录不回复）
    last_poll_at   TIMESTAMPTZ,
    last_reply_at  TIMESTAMPTZ,                    -- 发送后冷却用
    last_error     TEXT
);

-- HITL 待审批操作（跨轮 resume）
CREATE TABLE IF NOT EXISTS interaction_pending (
    thread_id         TEXT PRIMARY KEY,            -- wechat:{conversation}
    conversation      TEXT NOT NULL,
    interrupt_payload JSONB NOT NULL,              -- action_requests / review_configs
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ NOT NULL
);

-- ===== 简历 RAG（M4）=====
CREATE TABLE IF NOT EXISTS resume_files (
    id          BIGSERIAL PRIMARY KEY,
    filename    TEXT NOT NULL,                     -- 原文件名（展示用）
    stored_path TEXT NOT NULL,                     -- backend/uploads/resume/...
    mime        TEXT NOT NULL,
    size_bytes  INT NOT NULL,
    sha256      TEXT NOT NULL,
    chunk_count INT NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'ready',     -- ready / error
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS resume_chunks (
    id          BIGSERIAL PRIMARY KEY,
    file_id     BIGINT NOT NULL REFERENCES resume_files(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    page        INT,
    text        TEXT NOT NULL,
    embedding   vector(1024) NOT NULL,             -- 维度需与 embedding_dims 一致
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_resume_chunks_file ON resume_chunks (file_id);
CREATE INDEX IF NOT EXISTS idx_resume_chunks_embedding
    ON resume_chunks USING hnsw (embedding vector_cosine_ops);
