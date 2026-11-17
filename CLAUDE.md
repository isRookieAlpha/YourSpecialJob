# YourSpecialJob

## 项目概述

求职实习作品集项目：用 LangChain 1.2 agent 读取、整理、筛选微信兼职群消息。消息经弱模型 Pydantic 结构化后存入 PostgreSQL，按用户显式要求（日期/佣金/地点）与隐性偏好（骑车半小时可达、轻度体力活优先、佣金 100 元以上优先等）推荐兼职。前端为 Vue3 Web UI：左侧历史会话边栏 + 聊天页（含右侧"心动的offer"交互面板）+ 统计页（心动的offer 工作台：列表/统计/静态地图/日程表）+ 设置页，后端 FastAPI + SSE 流式输出。M4 新增简历 RAG 与中介回复：上传简历（PDF/DOCX/TXT/MD）→ 切块 embedding 入 pgvector → agent 回答中介问题/写自我介绍时检索简历事实 → 经用户确认（Web 弹窗或微信"同意"）后通过微信发送给中介。

最高优先级路径：**微信 MCP 自动读取群消息**（已实测跑通）；手动粘贴 / 截图识别为兜底。**长期跟踪**：后台定时增量读取群消息，命中偏好时推送微信；推荐主流程结合高德骑行通勤与天气。

## 技术栈

- **Python**：conda 环境 `SggLangchain1.2Practice`（Python 3.13，langchain 1.2.12 / langgraph 1.1.2 / fastapi / loguru / psycopg）
- **数据库**：PostgreSQL 17 + pgvector 0.8.1，无 Docker —— 独立 conda 环境 `SggYourSpecialJobPG` 提供 server，数据目录 `pgdata/`（gitignored），`scripts/start_pg.py` 启动、`scripts/init_db.py` 建库
- **Agent**：`create_agent()` + 中间件栈（TodoList / Summarization@80% / ModelRetry / ModelFallback / HumanInTheLoop），短期记忆 AsyncPostgresSaver，长期记忆 AsyncPostgresStore（pgvector 索引）
- **MCP**：高德 `@amap/amap-maps`（npx stdio）；微信 `mcp_server_wechat==0.8` —— 隔离 conda 环境 `SggWechatMCP`（64 位 Python + 微信 3.9.12 32 位版），补丁脚本 `scripts/patch_pywechat.py`
- **模型**：强/弱/备用三档，OpenAI 兼容接口（.env 配置）；embedding BGE-M3 1024 维；RAG 仅用于简历知识库（pypdf/python-docx 解析 → RecursiveCharacterTextSplitter 500/50 → pgvector HNSW 余弦检索），兼职筛选走 SQL 硬过滤 + LLM 软排序
- **前端**：Vue3 + Vite + vue-router@4（hash 路由）+ Pinia@3 + marked（Markdown）；地图为后端代理的高德**静态地图**（仅用 Web 服务 key，无 JS API 依赖）

## 项目结构

```
agent/           # LangChain agent 核心包
  ├─ db/         # pool / jobs_dao / offers_dao / settings_dao / sessions_dao / tracking_dao / interaction_dao / resume_dao / schema.sql
  ├─ tools/      # ingest（微信）/ manual_ingest（粘贴）/ query_jobs（含 select_offers/add_to_offers）/ report（PDF，HITL）/ travel（天气）/ resume（简历检索）/ wechat_send（发中介，HITL）
  ├─ memory/     # struct_history / preference_extractor（长期记忆提取）/ memory_prompt（@dynamic_prompt）
  ├─ formatting.py / work_time.py / income.py   # 来源文案、时段解析、收入估算（单一真相源）
  ├─ wechat_text.py                             # 微信消息排版：Markdown→纯文本、表格转字段行、emoji、前缀预算截断
  ├─ rag.py                                     # 简历解析/切块/embedding/索引/检索（pgvector 余弦）
  ├─ screenshot.py / report_pdf.py / amap_map.py # 截图多模态、PDF 生成、高德地理编码/静态地图
  ├─ amap_travel.py / amap_district.py          # 高德天气/骑行、省市区三级区划（缓存）
  ├─ commute_service.py / commute_refresh.py    # 通勤入库、全库重算 worker（失败明细/进度）
  ├─ tracking.py / tracking_format.py / interaction.py / contact_interaction.py / preferences.py / wechat_window.py
  └─ config.py / models_factory.py / graph.py / ingest.py / manual_ingest.py / mcp_wechat.py / tool_call_repair.py / prompts/
backend/         # FastAPI 服务层（main/deps/sse/history/routers: chat settings sessions upload reports offers tracking interaction system commute resume）
frontend/        # Vue3 前端
  ├─ views/      # ChatView（聊天+OfferPanel）/ SummaryView（心动的offer 工作台）/ SettingsView（5 卡）
  ├─ components/ # SidebarSessions / OfferPanel / ChatInput（+菜单）/ HitlDialog / ContactPendingCard / offers/*
  ├─ components/settings/  # CommuteCard / PreferenceCard / WechatCard / LargeModelCard / AmapMcpCard / ResumeCard / ToggleSwitch / DateRangeDialog / ConfirmDialog
  └─ stores/session.js、router、api（http/sse）
scripts/         # start_pg / init_db / patch_pywechat / check_wechat_mcp / read_wechat_test / probe_*
tests/           # pytest 用例（503 个，独立测试库 yourspecialjob_test）
data/            # 微信 MCP 导出的聊天记录（gitignored，个人数据）
pgdata/          # PG 数据目录（gitignored）
logs/            # loguru 日志（gitignored）
backend/reports/ # 历史 PDF（gitignored；新导出默认到桌面或 preferences.export_dir）
```

## 编码规范

- **思考过程（thinking）必须用中文展示**（与用户全局规则一致，此处再强调）；若客户端不渲染 thinking，则把关键判断依据以中文写进正文回复，确保用户可见
- 代码注释中文；代码、命令、变量名、文件路径英文
- **配置**：`load_dotenv("agent/.env", override=True)` 相对路径（所有命令在项目根目录运行）；`os.getenv("大写前缀_API_KEY")` 命名
- **模型**：`init_chat_model(model=..., model_provider='openai', api_key=..., base_url=...)`，实例命名 `llm_xxx`
- **Agent**：`from langchain.agents import create_agent`；`@tool(parse_docstring=True)` + 中文 Google docstring；工具内用 `runtime: ToolRuntime` 访问 store/state
- **中间件**：`SummarizationMiddleware(model, trigger=[("fraction", 0.8)], keep=("messages", 2))`；HITL 必配 checkpointer + `thread_id`
- **注释风格**：中文编号步骤注释（`#1.xxx` / `#2.xxx`），与 SggLangchainLearn 学习项目一致
- **开发流程**：不用 jupyter（适合学习不适合项目）；API 行为验证用 `scripts/probe_*.py` 临时脚本或 pytest
- 日志 loguru 双输出：控制台 INFO + `logs/app_{date}.log` DEBUG；外部调用全 try/except 记日志并给降级提示

## 当前开发状态

- **M0 已完成（2026-11-03）**：PG + pgvector 就绪且实测连通；微信 MCP 决策门通过；`agent/config.py`、`.env.example`、scripts 骨架就位
- **M1 已完成（2026-11-03，真实 LLM 全链路验证）**：微信自动读取（补丁后全自动翻页 10 格/0.1s，约 14 秒/天，无遗漏）→ 弱模型结构化（qwen3.5-omni-plus，source_index 血缘回填，分批 3000 字符并发 3 批）→ jobs 表三层去重入库 → query_jobs SQL 硬过滤 → SSE 流式推荐（meta/token/todos/tool/progress/done/error）；前端单页聊天（Vite 5173，Markdown 渲染 + todo 步骤条 + 微信读取按钮 + 新会话 + 45s 断线看门狗）；27 个 pytest 全绿（独立测试库 yourspecialjob_test）
- **M1 实测性能**：微信读取 14s（窗口打开固定开销 ~10s 是瓶颈）+ 结构化 ~55s（并发）+ 推荐 ~15s ≈ 全程 70~90s
- **M3 已完成（2026-11-04，含基础 sessions）**：app_settings 单行 JSONB 动态配置（DB 覆盖 .env，PUT 空串=不修改 / null=清除，GET 脱敏只回 `api_key_configured`）+ 设置保存后按固定顺序重建（cache_clear → reset_models → build_agent，失败回滚保留旧 agent）；sessions 基础 CRUD + 聊天自动建会话（标题取首条消息前 20 字）；前端引入 vue-router 三页签 + 左侧边栏布局（KeepAlive 保留各页状态）+ 设置页（三档模型 / Embedding / 偏好 / 会话管理 / 测试连通）
- **M2 已完成（2026-11-04）**：手动粘贴兜底（`ingest_text_messages`，微信复制格式/纯文本解析）+ 截图识别（弱模型多模态单路径、无 OCR；≤8MB PNG/JPEG/WebP）；多会话历史加载（`GET /api/sessions/{id}/messages` 走 checkpointer 回放，含 todo 恢复）；HITL（`HumanInTheLoopMiddleware` 仅拦 `export_job_report`）+ `POST /api/chat/resume` + 审批弹窗；PDF 报告（reportlab + msyh 中文字体）
- **M2.5 已完成（2026-11-05，UI 大改版 + 心动的offer）**：
  - 推荐来源标注（工具文本/SSE/API/PDF 统一走 `agent/formatting.py::format_source`，时间取 ingested_at）
  - 导航重构：品牌两行点击进统计页、左侧历史会话边栏（新建/切换/删除）、群名/日期移至设置页
  - 输入框：「+」菜单（选择图片→系统文件选择器 / 读取微信）；图片可拖拽/粘贴到聊天框，**点发送后自动识别入库并触发推荐**
  - HITL 侧栏 `OfferPanel`：推荐后右侧弹出三步向导（添加至心动的offer → 添加为 ToDo → 询问导出 PDF 到设置目录，默认桌面）
  - 统计页改为心动的offer 工作台：列表增删改查（手动录入 source=manual_form 直接入库）、ToDo 升降级、统计卡（佣金中值口径）、高德静态地图（后端 geocode+staticmap 代理、A-J 图例）、日程表（无时段默认 08:00-22:00）
  - 两段式推荐：本轮新入库（截图批次/粘贴/微信读取给出 `ingested_after`）先推荐，再补充历史库 1~3 条并提示"若想了解更多历史推荐请说"
  - 设置页每卡片独立保存；preferences 合并写入（避免整体替换丢键）
  - 186 个 pytest 全绿
- **M7 已完成（2026-11-05 初版，2026-11-06 修复轮定稿）**：长期跟踪 + 偏好推送 + amap 接入推荐主流程
  - 补丁修复微信发送（`delay`→`send_delay`、多好友函数名、翻页 300 次上限、**读取/发送后自动关闭微信主窗口**）；真实发送实测成功
  - 调度器 `agent/tracking.py`：固定节拍、每轮从 DB 重读 preferences、`pg_try_advisory_lock` 单实例、单群异常隔离、退出 cancel；**修复 `run_round(pool, state, cfg)` 参数顺序**（曾有 AttributeError 导致"立即执行"无反应）
  - 指纹账本 `wechat_seen_messages` + 组状态 `tracking_group_state` + 推送日志 `push_log`；**首轮基线入库后同样推送**；双层前置过滤（指纹 + content_hash），避免每轮重跑弱模型
  - 偏好命中推送 `WECHAT_SENDER_ID` 会话：**只推本轮新入库**（round_start 窗口，不补推历史）、失败重试最多 3 次、摘要复用来源/收入/通勤/天气（≤1800 字）
  - **通勤入库（高德 MCP）**：`agent/amap_mcp.py` 持久会话批量计算距离/骑行/公共交通/驾车并写入 `jobs.commute_*`；城市拼前缀 + 城市过滤解决地址歧义；0.45s 节流 + CUQPS 退避重试；单地点失败隔离；补算脚本 `scripts/backfill_commute.py`
  - 天气工具 `get_today_weather`（推荐时查询实况天气）
  - 设置页新增「长期跟踪」卡片（开关/间隔/群列表/推送/账本天数/运行状态/立即执行轮询反馈）与「通勤起点地址」
  - **发布时间时区修复**：模型误标 UTC `Z` 时以微信消息时刻覆盖，`combine_publish_time` 返回 aware 本地时间
  - **SSE 空输出兜底**：模型静默失败（无 token/工具/中断）时返回明确错误提示，不再出现空气泡
  - 278 个 pytest 全绿
- **微信互动已完成（2026-11-06）**：`agent/interaction.py` 轮询目标会话（默认 60s 可配 30~300s，微信 MCP 无监听接口只能轮询）→ agent 回复 → 回发微信；方向识别靠机器人前缀 `【YourSpecialJob` + 入站指纹（实测收发发送者为同一昵称，不可依赖）；HITL 危险操作经微信确认（"同意/拒绝" → `Command(resume=...)`，pending 存 `interaction_pending` 表 TTL 24h）；每小时回复上限、发送冷却 8s、独立 advisory lock；设置页新增互动开关/间隔/微信目标（推送与互动共用 `preferences.wechat_target`，空回退 .env）
- **M6 长期记忆闭环已完成（2026-11-06）**：`AsyncPostgresStore`（pgvector 索引，namespace `("default","insights")`）随 lifespan 创建；`@dynamic_prompt` 中间件每次模型调用注入显式偏好 + 长期记忆（profile + 首条用户消息语义检索 top4，异常降级仅显式偏好）；对话正常结束后 fire-and-forget 弱模型提取偏好/约束/事实（去重合并，冲突以显式偏好为准）；E2E 实测"优先日结、不接夜班"提取入库 2 条
- **2026-11-07 微信体验与设置页重构已完成**：
  - **微信消息纯文本化**：新增 `agent/wechat_text.py`（Markdown 清洗、**Markdown 表格转「字段：值」逐行 + emoji**、`【YourSpecialJob` 前缀预算截断）；推送摘要分段加 emoji；互动回复发送前统一转换；模型漏复述 `select_offers` 编号清单时由后端 `_with_offer_list` 自动追加
  - **设置页 4 卡**：通勤起始位置（省/市/区三级 + 详细地址；直辖市"上海城区"规范化为"上海市"）、偏好与通用（微信访问总开关、PDF 目录系统选择、佣金、每日时长、时段双滑块、骑行/公交/驾车三个通勤上限、体力、其他偏好）、微信设置（默认读取群列表、日期范围、跟踪/推送/互动、运行状态与进度条）、大模型设置（强/弱/备用 + Embedding 合并）
  - **日期范围读取**：主界面「读取微信」与 agent 工具支持最多 30 天逐日读取（前端逐群逐日显示进度）；**跟踪仍只读当天**（用户确认）
  - **通勤全库重算**：修改起点保存确认后后台重算（`commute_refresh` worker；每批计算成功才清空该批、失败批保留旧数据；失败明细最多 50 条随状态接口返回；起点 geocode 预检；60 唯一地点/批）；新增 `GET /api/system/districts`（高德区划代理 + 24h 缓存）、`POST /api/system/pick-directory`（tkinter 目录选择）、`/api/commute/refresh[/status]`
  - **微信/网页 offer 一致性**：新增 `select_offers`（`replace=True` 覆盖候选池）与 `add_to_offers`（微信回复「添加第 N 条」直接收藏）；`OfferPanel` 展示全部推荐，已收藏/ToDo 标记且不可勾选
  - **微信记忆**：推送摘要发送成功后写入 `wechat:{目标}` 短期记忆（`aupdate_state`）；互动回复成功后 fire-and-forget 提取长期记忆（微信对话也能写入 store）
  - **其他修复**：多段回复光标残留（新段开始复位旧 `streaming`、`done`/`error` 立即收尾、移除空占位）；「读取微信」遍历 `wechat_groups` 全部群；跟踪「立即执行一轮」进度（按群 `progress`）
  - 402 个 pytest 全绿
- **M4 简历 RAG + 中介回复已完成（2026-11-08，待实测验收）**：
  - **数据层**：`resume_files`（原文件名/落盘路径/sha256/chunk_count/status）与 `resume_chunks`（`embedding vector(1024)` + HNSW `vector_cosine_ops`，FK CASCADE）；`agent/db/resume_dao.py` 用 `%s::vector` 文本字面量绑定向量，检索 `1 - (embedding <=> query)`；`scripts/init_db.py` 幂等建表
  - **解析索引**：`agent/rag.py` —— PDF（pypdf）/DOCX（python-docx）/TXT/MD（utf-8→gb18030 回退）→ 切块 500/50 → 批量 `aembed_documents`；维度必须 1024，不一致明确报错；索引失败自动清理落盘文件与记录
  - **上传管理**：`POST /api/resume/upload`（≤10MB，白名单）、`GET /api/resume/files`、`DELETE /api/resume/files/{id}`、`POST /api/resume/rebuild`（换 embedding 模型后重建）；设置页第 5 卡「简历与个人资料」（ResumeCard：上传/列表/块数/状态/删除/重建）
  - **检索工具**：`search_resume(query, top_k)` 注册到 agent；system prompt 要求回答中介问题/写自我介绍前必须先检索，只引用简历事实、禁止编造
  - **中介发送**：`send_wechat_message_to_contact(contact_name, message)` —— 拒绝互动会话/监控群、纯文本化 ≤1800、持 `wechat_window_lock` 调用 MCP、失败返回中文原因+草稿；HITL `interrupt_on` 拦截（approve/edit/reject），Web `HitlDialog` 渲染联系人+正文预览，微信端审批通知显示联系人/内容并回复「同意/拒绝」
  - **多工具中断修复**：`interaction._handle_pending` 按 `action_requests` 数量生成等长 decisions
  - **联系人推断**：`query_jobs`/offers 载荷新增 `publisher`；未指定联系人时 agent 从 `contact`/`publisher` 提取候选并先向用户确认
  - 433 个 pytest 全绿；前端 `npm run build` 通过
- **2026-11-08 中介互动 + 简历卡片扩展已完成**：
  - 设置页卡片顺序调整为 通勤 → 偏好 → 微信 → **简历** → 大模型；「中介微信名」列表与「中介互动」区（开关/间隔/状态/立即检查）放在**微信设置卡**（简历卡只保留简历文件管理）
  - 中介名单 `preferences.wechat_contacts`（≤10，排除互动会话与监控群）；发送工具执行成功（HITL 批准后）自动追加，单条 SQL 原子写入并同步进程内单例
  - 新 worker `agent/contact_interaction.py`：按间隔轮转读取名单中的一个中介（最久未读优先，`search_pages=5`），过滤机器人前缀与出站账本，指纹去重；**基线轮同样草拟**（记录全部历史指纹后取最近 3 条生成草稿）；agent 基于 `search_resume` 草拟并调用发送工具，HITL 中断存 `interaction_pending`（thread_id=`wechat:contact:{中介}`，payload 含 mode/source_message/draft）
  - 确认双通道：微信互动会话收到审批通知后回复「同意/拒绝」；网页聊天页浮动 `ContactPendingCard`（10s 轮询）批准/拒绝；`claim_pending`（DELETE ... RETURNING）原子领取，重复端 404
  - 出站账本：发送成功写 `wechat_seen_messages`（`outbound:{联系人}`，不含时间），下一轮过滤自己发出的消息，避免自触发
  - 新增 API：`GET /api/interaction/contact-status`、`POST /api/interaction/contact-run`、`GET /api/interaction/contact-pending`、`POST /api/interaction/contact-pending/resume`
  - 478 个 pytest 全绿；前端 `npm run build` 通过
- **2026-11-09 稳定性修复**：
  - **工具调用参数修复**：MiniMax-M2.5 偶发输出非法 JSON 参数（中文字符串未加引号，如 `{"group_name": 兼职1群}`），LangChain 将其放入 `invalid_tool_calls` 并忽略，导致 agent 只回复一句"我来读取…"却什么都不做、页面无推荐。新增 `agent/tool_call_repair.py`（`ToolCallRepairMiddleware`）：对冒号后的裸值补引号（数字/布尔/null 保持原样）并转回 `tool_calls`；**必须注册在 HITL 内层**（`agent/graph.py` 末尾），保证修复后的发送工具调用仍被审批拦截
  - **默认读取日期滚动**：`settings_dao.reset_default_read_dates` 在 lifespan 启动时把 `default_date_start / default_date_end / default_date` 重置为当天；日期仅当次运行有效，重启即回到今日，其他偏好与模型块不受影响
  - **微信 MCP 读取补丁 #26**：`WechatClient.get_chat_history_by_date` 原逻辑在一屏内遇到早于目标日期的消息就提前放弃，当天消息少（如仅 1 条且与昨天的消息同屏）时误判"未找到"返回 0 条；补丁改为遍历完整屏后再判断（`scripts/patch_pywechat.py`，重建隔离环境后需重跑）
  - **todo 进度卡片**：system prompt 明确"多步骤任务必须调用 write_todos 工具记录进度，不要只把任务清单写在回复正文里"
  - 492 个 pytest 全绿
- **2026-11-11 设置页增强与稳定性修复**：
  - **偏好**：新增「性别」（男/女/不限制）与「年龄」（16~80，可空），作为显式偏好注入 system prompt；「期望工作时段」改为单轨道双柄滑块（00:00–24:00，15 分钟步进，两端至少间隔 30 分钟）
  - **开关与刷新**：5 个布尔开关统一为 iOS 风格拨动开关（`ToggleSwitch.vue`）、微信绿 `#07C160` 底色、15px 文案；跟踪/互动/中介互动状态区各加「刷新」按钮（只重拉状态，短暂显示"已刷新"）
  - **高德魔搭 MCP**：大模型卡新增「高德魔搭 MCP」块（`AmapMcpCard.vue`：`AMAP_MAPS_API_KEY` + `MCP URL`），保存到 `app_settings.amap`；`amap_mcp._make_client` URL 非空时走 streamable HTTP（魔搭 Remote，地址自带鉴权），留空回退本地 npx；保存 amap 不重建 agent
  - **品牌字体**：侧边栏品牌 `YourSpecialJob` 使用 Google Fonts Nabla（彩色 3D），「—>点击这里<—」使用 WDXL Lubrifont SC（`frontend/index.html` 引入）
  - **推送摘要带 id**：跟踪推送每条「工作内容」后附 `（id=xxx）`，该摘要同时写入互动短期记忆，用户按编号回复收藏时 agent 可映射准确条目
  - **多行消息时间修复**：`parse_history_text` 进入消息体后不再把正文里的「时间:」行当元数据（曾把 21:26 覆盖成非时间文本，导致发布时间保留模型误标的 UTC 值、显示 +8 小时）
  - **code review 修复**：高德 DB key 覆盖 .env；`parse_hhmm` 接受 24:00→23:59；Remote URL 文案明确 REST 仍需 key；`mask_settings` 以 server_key 判断已配置；跟踪/中介轮询等 `running` 结束再判停；刷新失败不显示"已刷新"；双柄滑块拖动时手动钳制并回写 DOM
  - 503 个 pytest 全绿；前端 `npm run build` 通过
- 计划文件：本地开发计划（M0–M8 里程碑，不随仓库公开）

## 启动服务（开发）
```bash
# 1. PG（幂等，已运行会跳过）
python scripts/start_pg.py
# 2. 后端（必须带 --reload，子进程模式自动用 SelectorEventLoop，psycopg 需要）
#    注意：必须用项目 conda 环境的 python（bash 默认 python 可能是 base 3.14，没装 fastapi）
"<你的 conda 环境路径>/python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
# 3. 前端
cd frontend && npm run dev   # http://localhost:5173
```
**启动前检查端口只有一个进程**：`netstat -ano | grep -E ":5173|:8000"`——多实例会导致请求被旧进程接走（表现为前端完全无输出），用 `taskkill /PID <pid> /T /F` 清理残留。

## 注意事项
- 环境变量在 .env 文件中，不要提交到 Git
- `agent/.env`、`data/`（微信聊天记录导出）、`pgdata/`、`logs/`、`agent/memory/struct_history/` 均已 gitignore，勿提交
- **Windows 事件循环**：psycopg 3.x 不支持 ProactorEventLoop。uvicorn 必须带 `--reload`（子进程模式自动用 SelectorEventLoop）；probe 脚本顶部需 `set_event_loop_policy(WindowsSelectorEventLoopPolicy)`；微信 MCP 在独立线程的 Proactor 循环中运行（agent/mcp_wechat.py）
- 微信自动化前提：PC 微信 3.9.12（32 位版）保持登录，64 位 Python + 补丁即可；勿升级微信 4.x、勿改用 32 位 Python
- 微信翻页补丁（scripts/patch_pywechat.py）：聊天记录窗口 set_focus + 鼠标滚轮 `mouse.scroll(wheel_dist=10)` + 0.1s 渲染等待（实测 ~14s 读完当天消息、无遗漏；步长 30 无提速，窗口打开固定开销 ~10s 是瓶颈）；键盘 PageUp 依赖全局焦点不可靠
- 长耗时工具的进度通过 `get_stream_writer()`（stream_mode="custom"）以 `progress` SSE 事件推送到前端，避免前端长时间静默
- **SSE 前端解析必须把 `\r\n` 归一化为 `\n` 再按空行分帧**（sse-starlette 默认 `\r\n` 行尾，`indexOf('\n\n')` 永远匹配不到，会导致事件全部丢失）
- Vue3 响应式坑：`messages.value.push(obj)` 后必须返回**数组索引取出的代理对象**再修改属性，直接改 push 前的原始对象不会触发重渲染
- 读取微信历史会实际操作 PC 微信窗口（打开聊天历史翻页），执行期间不要手动操作微信
- **设置动态重建顺序固定**：`get_settings.cache_clear()` → `reset_models()`（清 5 个模型缓存）→ `build_agent()`；先校验后写库，构建失败回滚并保留旧 agent（backend/deps.py + routers/settings.py）
- **API 测试用 `httpx.AsyncClient + ASGITransport`**（不跑 lifespan、不跨事件循环——pool/checkpointer 绑定事件循环，用 TestClient 会出错）
- **uvicorn --reload 在 Windows 上会偶发卡死**：日志出现 "WatchFiles detected changes … Reloading..." 后不再出现 "Application startup complete" 时，`netstat` 找 8000 的 PID → `MSYS_NO_PATHCONV=1 taskkill /PID <pid> /T /F` 后重启（Git Bash 里不加 MSYS_NO_PATHCONV 会把 `/PID` 转成路径导致 taskkill 失败）
- **来源标识约定**（内部 group_name，不对外展示）：截图批次 = `图片上传_时间戳`；对话粘贴未指定群名 = `手动复制粘贴识别`；表单录入 = `手动录入`。`format_source()` 负责隐藏内部标识、统一展示"截图识别/手动粘贴/手动录入"
- **发布时间时区坑**：模型常把本地时间误标为 UTC（`2026-11-06T11:38:00Z`），入库后显示 +8 小时；`_reconcile_raw_text` 以微信消息的"时间"字段为准覆盖模型值，`combine_publish_time` 返回**aware 本地时间**（naive.astimezone()）
- **两段式推荐**：ingest 类通道（截图前端消息 / 粘贴与微信工具返回）都会给出 `ingested_after`；agent 先按 `query_jobs(ingested_after=…)` 推新数据，再 `query_jobs(ingested_before=…, limit=3)` 补历史
- **offers 事件**：`select_offers` 推送 `replace=True`（覆盖候选池，保证右侧=左侧推荐）；`query_jobs` 推送候选池（前端按 id 合并作为兜底）；`OfferPanel` 展示全部推荐，已收藏/ToDo 标记且不可勾选
- **心动的offer 数据模型**：jobs.offer_status（none/favorite/todo）+ offer_updated_at；列表排序 `ORDER BY id ASC`（编辑/升降级不改变位置，删除后自然前移）；`manual_form` 删除为硬删，其他来源为软删（offer_status='none'，保留血缘防重复入库）
- **PDF 导出目录**：`preferences.export_dir`（空=桌面 `Path.home()/Desktop`）；测试必须显式 `output_dir=tmp_path`，禁止写真实桌面
- **图片粘贴/拖拽**：ChatInput 常驻 `paste` 监听（仅拦截剪贴板图片），发送时逐张调 `/api/upload/screenshot`，入库后再发消息给 agent（消息里必须带 `ingested_after` 与批次 group_name）
- **微信发送补丁（M7）**：`scripts/patch_pywechat.py` 修复 `send_delay` 参数、多好友函数名、翻页 300 次上限；**读取与发送完成后自动关闭微信主窗口（close_wechat=True，用户要求）**；跑完用 `python scripts/probe_wechat_send.py` 真实发送验证（发送成功但回读可能因微信 COM 状态失败，属已知现象）
- **通勤入库（高德 MCP）**：新增兼职入库后调用 `agent/amap_mcp.py::compute_commute_batch`（持久会话、同批地点去重、`maps_geo` 的 city 参数无效 → **把城市拼进地址**（"上海西岸梦中心"）并按 `preferences.amap_city` 过滤候选），计算并写入 `jobs.commute_distance_m / commute_bike_min / commute_transit_min / commute_drive_min`；**只用 MCP、失败不兜底**（`IngestStats.commute_failed` 计数并在工具返回中提示"高德 MCP 不可用，兼职通勤暂无法计算"）；推荐理由直接读库中通勤数据，天气由 `get_today_weather` 单独查询
- **高德 MCP 限流**：各接口 QPS 上限 3 次/秒，MCP 内部无节流 → `_call` 统一 sleep 0.45s + 遇 `CUQPS_HAS_EXCEEDED_THE_LIMIT` 退避重试 2 次；单地点地理编码失败只跳过该点（不中断整批）；补算脚本 `scripts/backfill_commute.py`（独立进程运行前会应用 DB 覆盖读取通勤起点/城市）
- **调用 `run_round` 参数顺序**：`run_round(pool, state, cfg, force=)`（曾因传反导致调度器每轮 AttributeError、前端"立即执行"无反应；`tracking_loop` 已有测试覆盖）
- **微信窗口两级锁**（`agent/wechat_window.py`）：`ingest_run_lock`（整条摄入管道）→ `wechat_window_lock`（实际读写窗口）；固定获取顺序，发送只取窗口锁，避免死锁
- **跟踪调度器**：lifespan 启动、退出取消；`pg_try_advisory_lock(0x59534A4D37)` 整轮互斥（连接断开自动释放，不需要清理）；每轮从 DB 读 preferences（设置保存立即生效，不重建 agent）；未启用时 60s 巡检一次
- **指纹账本**：`sha256(group|day|sender|time|normalize(text))[:32]`；同一分钟同发送者同文本会折叠（可接受）；首轮基线入库后**同样参与推送**（用户要求，仅标记 `initialized_at`）
- **推送候选规则**：只推本轮新入库（`ingested_after` lookback）+ 失败重试（attempts<3）；排除已收藏/ToDo、过期日期、佣金未知/低于下限、已 sent/skipped、通勤超上限；推送目标在监控群中时自动禁用（防自我摄入）
- **高德出行**：骑行 `v5/direction/bicycling?show_fields=cost`（duration 秒）、天气 `v3/weather/weatherInfo`（city 需 adcode，来自 geocode 响应）；失败一律降级（fail-open，不剔除候选）；探针 `scripts/probe_amap_travel.py`、`scripts/probe_tracking_round.py --dry-run/--send`
- **跟踪相关 preferences 键**：`tracking_enabled/tracking_interval_min(5~180)/tracking_groups(数组或逗号换行)/tracking_push_enabled/tracking_push_limit(1~10)/tracking_ledger_days(1~30)/tracking_max_messages/tracking_push_lookback_min/commute_origin`；状态接口 `GET /api/tracking/status`、立即执行 `POST /api/tracking/run`
- **微信互动**：轮询模式（微信 MCP 无监听/未读接口，每次读取需操作窗口 10~15s）；`interaction_enabled/interaction_interval_sec(30~300)/wechat_target/interaction_max_replies_per_hour/interaction_pending_ttl_min`；`GET /api/interaction/status`、`POST /api/interaction/run`；读取/发送各自短持 `wechat_window_lock`，**ainvoke 期间严禁持锁**（工具会再次申请 → 死锁）；机器人推送与回复统一 `【YourSpecialJob` 前缀用于方向识别；发送后冷却 8s 再读
- **HITL 微信确认**：agent.ainvoke 遇 interrupt 返回 `{"__interrupt__": ...}` 且工具未执行；保存 `interaction_pending` 并发送审批通知，用户回复「同意/拒绝」（只匹配开头词，避免"你好"误判）后 `Command(resume={"decisions":[...]})`；pending 过期 24h 自动清理
- **M6 长期记忆**：store 表由 `AsyncPostgresStore.setup()` 自动建（store/store_vectors/store_migrations）；`get_embeddings()` 必须 `check_embedding_ctx_length=False`（DashScope 等兼容端点不接受 tiktoken token 数组，否则 embedding 全部 400）；embedding_dims 变更需重启（store_vectors 列固定）；`@dynamic_prompt` 会整体覆盖 system prompt，必须返回完整 prompt；提取为 done 后 fire-and-forget（`backend/sse.py` on_complete + 强引用 set）
- **微信消息排版（wechat_text）**：`to_wechat_plain_text` 先清洗 Markdown（**表格按表头转「字段：值」逐行**，否则微信里只剩难读的竖线行），再加段落 emoji；`fit_wechat_message` 把 `【YourSpecialJob` 前缀计入 1800 预算。不要改共享 `formatting.py`（网页/SSE/PDF 共用）
- **select_offers / add_to_offers**：`select_offers` 只声明推荐集并推送 `replace=True` 的 offers 事件（网页面板覆盖），返回编号清单；`add_to_offers` 直接 `set_offer_status(promote)` 收藏（微信回复「添加第 N 条」）。互动 worker 若模型回复未含编号（`（id=`），`_with_offer_list` 自动追加工具清单
- **设置页 5 卡**：CommuteCard / PreferenceCard / WechatCard（群/日期/跟踪/互动 + 中介名单 + 中介互动）/ ResumeCard（仅简历文件）/ LargeModelCard；`preferences` 仍是整体替换语义，前端 `preferencesRaw` 合并 + 新键双写旧键（`wechat_groups`↔`default_group`/`tracking_groups`、`default_date_start/end`↔`default_date`、`labor_level`↔`prefer_light`）；`wechat_access_enabled=false` 时隐藏微信卡、ingest 工具 403、tracking/interaction 跳过
- **直辖市区划坑**：高德 `district` 对直辖市的市级名称是"上海城区"；`amap_city` 必须存"上海市"，`agent/amap_mcp.py::_city_core` 统一规范化（城市匹配/地址拼接/公交 city 参数）；前端 CommuteCard 负责"上海市↔上海城区"显示映射
- **defineModel 同步回读坑**：`city.value = x` 只 emit 事件，**同一同步代码块内再读仍是父级旧值**；程序化恢复后立即要用的值必须用函数返回值（如 `restoreCityName()`），不能依赖 model 回读
- **通勤重算**：`commute_refresh` worker（advisory lock `0x59534A4D39`）；任务开始快照 origin/city；起点 REST geocode 预检失败不清空；**每批计算成功后清空该批旧值再写回**（失败批保留旧数据）；`compute_commute_batch(failures=[...])` 收集失败原因（地理编码失败/地址无法识别/不在当前城市/路线失败/整批 MCP 失败），status 接口返回 `failures`
- **跟踪进度**：`TrackingState.progress={current,total,label}`（按群计数，`run_round` 维护），`GET /api/tracking/status` 返回；WechatCard 运行中显示进度条（2s 轮询）
- **微信记忆读写**：`push_round` 发送成功后 `agent.aupdate_state(thread_id=wechat:{目标}, AIMessage(summary))` 写入短期记忆；`interaction._send_reply` 成功后 fire-and-forget `extract_and_store_memory`（强引用 `_memory_tasks`）。微信 thread 固定 `wechat:{conversation}`，与网页会话隔离；长期记忆 store 共享
- **读取微信的群**：`ingest` 工具不传 group_name 时遍历 `wechat_groups` 全部群（逐群逐天，单群失败继续下一个）；前端「读取微信」同样逐群逐日并显示进度
- **简历 RAG（M4）**：`resume_chunks.embedding vector(1024)` 固定维度，必须与 `settings.embedding_dims` 一致，不一致时上传/重建直接报错（不静默截断）；**换 embedding 模型后必须点「重建索引」**（旧向量与新模型不可比）；简历文件落盘 `backend/uploads/resume/`（gitignored，仅本机），删除文件级联删 chunks；`search_resume` 空库/无命中返回中文提示，prompt 要求先检索再回答；测试禁止写真实目录
- **中介发送（HITL）**：`send_wechat_message_to_contact` 在 `interrupt_on` 中；Web `HitlDialog` 只支持批准/拒绝（无 edit UI，要改文案就拒绝并说明理由让 agent 重写）；微信端 `build_interrupt_notice` 显示联系人+内容预览，`_handle_pending` 生成**等长 decisions**；工具自身做目标安全校验（互动会话 `wechat_target`/`WECHAT_SENDER_ID`、监控群 `wechat_groups` 直接拒绝）；发送失败（未登录/联系人名不匹配）返回"发送失败 + 草稿"不抛异常；消息不加 `【YourSpecialJob` 前缀，经 `to_wechat_plain_text` 清洗后 ≤1800；调用方持 `wechat_window_lock`，执行期间勿手动操作微信
- **联系人规则**：优先用户明确给出的微信备注/昵称（MCP 按名称搜索，名称不精确会发送失败）；未指定时 agent 从 `query_jobs` 的 `contact`/`publisher` 提取候选并先确认，禁止猜测直接发送
- **中介名单（wechat_contacts）**：`settings_dao.add_wechat_contact` 用单条 `INSERT ... ON CONFLICT ... jsonb_set` 原子追加（去重、≤10、保留模型块与其他 preferences 键），写后同步 `get_settings().preferences`；前端保存前重新 GET 最新 preferences，未编辑名单时沿用最新值（`contactsTouched`），避免页面旧快照覆盖自动追加
- **中介互动轮询**：`agent/contact_interaction.py`，advisory lock `0x59534A4D3A`；每轮只读一个中介（`interaction_state.last_poll_at` 最旧优先，NULL 最先）；存在待确认时整轮跳过；读取 `call_wechat_get_history(..., search_pages=5)`；每轮 `cleanup_ledger(30)`；**基线轮也草拟**（先记录全部历史指纹，再取最近 3 条生成草稿）；过期/孤儿中断用 `agent.checkpointer.adelete_thread` 清理
- **中介待确认**：`interaction_pending` 的 thread_id=`wechat:contact:{中介}`，payload 含 `mode`（hitl/draft）、`contact_name`、`source_message`、`draft`；网页与微信共用 `claim_pending`（DELETE ... RETURNING）原子领取，双通道并发只有一个成功；微信端在用户会话轮询时解析「同意/拒绝」并路由到 `resolve_contact_pending`，用户自己的 pending 优先；普通聊天不消耗中介 pending
- **出站账本**：发送给中介成功后写 `wechat_seen_messages`（`group_name=outbound:{联系人}`，指纹 = `sha256(outbound|联系人|日期|normalize(文本))`，不含发送者/时间）；中介轮询用 `filter_outbound` 过滤自己发出的消息——发给中介的消息按用户要求不加 `【YourSpecialJob` 前缀，不能靠前缀识别
- **中介互动 preferences 键**：`wechat_contacts`、`contact_interaction_enabled`、`contact_interaction_interval_sec`（30~300）；状态 `GET /api/interaction/contact-status`、立即执行 `POST /api/interaction/contact-run`、待确认 `GET /api/interaction/contact-pending` 与 `POST /api/interaction/contact-pending/resume`；网页 `ContactPendingCard` 每 10 秒轮询，KeepAlive 激活/停用启停
- **模型工具参数修复（MiniMax）**：MiniMax-M2.5 经 DashScope 兼容接口偶发输出非法 JSON 参数（中文裸值未加引号），LangChain 记入 `invalid_tool_calls` 后整个调用被忽略；`agent/tool_call_repair.py::ToolCallRepairMiddleware` 负责补引号修复，**中间件必须放在 HumanInTheLoopMiddleware 之后（最内层）**，否则修复出的发送工具会绕过审批
- **默认读取日期滚动**：`reset_default_read_dates` 在启动时把 `default_date_start/end/default_date` 写为当天；用户当次运行保存的日期有效，重启后自动回到今日
- **微信读取按日期查找（补丁 #26）**：`WechatClient.get_chat_history_by_date` 必须遍历完整屏再判断目标日期（原实现一遇到更早日期就 break，当天消息少且与昨天同屏时返回 0 条，表现为互动"消息发了但没回复"）；重建 SggWechatMCP 环境后务必重跑 `scripts/patch_pywechat.py`
- **高德 MCP 双通道**：`app_settings.amap = {api_key, url}`（PUT 空串=不修改、null=清除）；URL 非空 → `MultiServerMCPClient({"transport": "http", "url": ...})`（魔搭 Remote，**仅用于通勤 MCP，地址自带鉴权**），留空 → 本地 `npx @amap/amap-maps-mcp-server`；**省市区/天气/静态地图/通勤重算的 REST 接口仍需要 AMAP_MAPS_API_KEY**（Remote URL 不能替代）；DB 的 api_key 优先覆盖 .env（`apply_overrides` 同时写 `amap_maps_api_key` 与 `amap_server_key`），保存 amap 不重建 agent，下一次 MCP 调用生效；URL 过期时清空 URL 保存即回退 npx
- **推送摘要带 id**：`tracking_format.build_push_summary` 在每条「工作内容」后输出 `（id=xxx）`；该摘要既是微信推送文本也是互动短期记忆，用户回复「把第 N 条加入心动」时 agent 能映射准确 job id，不要移除
- **多行消息解析（parse_history_text）**：MCP 返回文本按「发送者:/时间:/消息:」分块；**消息体开始后所有行都是正文**（正文可能含「时间: 11🈷️11.12」等行），不得再当元数据解析，否则 `combine_publish_time` 拿到非法时间、保留模型误标的 UTC 值，发布时间显示 +8 小时



<!-- superpowers-zh:begin (do not edit between these markers) -->

## Superpowers-ZH 中文增强版

本项目已安装 superpowers-zh 技能框架（21 个 skills）。

### 核心规则

1. **收到任务时，先检查是否有匹配的 skill** — 哪怕只有 1% 的可能性也要检查
2. **设计先于编码** — 收到功能需求时，先用 brainstorming skill 做需求分析
3. **测试先于实现** — 写代码前先写测试（TDD）
4. **验证先于完成** — 声称完成前必须运行验证命令

### 可用 Skills

Skills 位于 `.claude/skills/` 目录，每个 skill 有独立的 `SKILL.md` 文件。

- **brainstorming**: 在任何创造性工作之前必须使用此技能——创建功能、构建组件、添加功能、修改行为，或筹划任何新东西，无论是软件还是软件之外（一场分享、一门生意、一次装修）。
- **chinese-code-review**: 中文 review 沟通参考——话术模板、分级标注（必须修复/建议修改/仅供参考）、国内团队常见反模式应对。仅在用户显式 /chinese-code-review 时调用，不要根据上下文自动触发。
- **chinese-commit-conventions**: 中文 commit 与 changelog 配置参考——Conventional Commits 中文适配、commitlint/husky/commitizen 中文模板、conventional-changelog 中文配置。仅在用户显式 /chinese-commit-conventions 时调用，不要根据上下文自动触发。
- **chinese-documentation**: 中文文档排版参考——中英文空格、全半角标点、术语保留、链接格式、中文文案排版指北约定。仅在用户显式 /chinese-documentation 时调用，不要根据上下文自动触发。
- **chinese-git-workflow**: 国内 Git 平台配置参考——Gitee、Coding.net、极狐 GitLab、CNB 的 SSH/HTTPS/凭据/CI 接入差异与镜像同步配置。仅在用户显式 /chinese-git-workflow 时调用，不要根据上下文自动触发。
- **diagnosing-superpowers**: 当一次 superpowers 会话出了问题、你的人类伙伴想知道原因时使用——重复劳动、无视计划、磕磕绊绊、结果质量差、某个技能没触发、"太慢了"、"为什么这么贵"、"它到底在干什么"——或者想给 superpowers 维护者整理一份 bug 报告；适用于当前会话，或按 id / 路径指定的过往会话，任何工具均可
- **dispatching-parallel-agents**: 当面对 2 个以上可以独立进行、无共享状态或顺序依赖的任务时使用
- **executing-plans**: 当你要在当前会话里亲自担任实现者执行一份实现计划时使用——你的人类伙伴选择了内联执行，或者没有可用的子智能体工具
- **finishing-a-development-branch**: 当实现完成、所有测试通过、需要决定如何集成这份工作时使用
- **mcp-builder**: 在构建 MCP 服务器或 MCP 工具时使用 —— 系统化的生产级 MCP 构建方法论，覆盖工具设计、错误处理、传输层选择与测试，让 AI 助手连接外部能力
- **receiving-code-review**: 收到代码审查反馈后、实施建议之前使用，尤其当反馈不明确或技术上有疑问时——需要技术严谨性和验证，而非敷衍附和或盲目执行
- **requesting-code-review**: 完成任务、实现重要功能或合并前使用，用于验证工作成果是否符合要求
- **subagent-driven-development**: 当在当前会话中执行包含独立任务的实现计划时使用
- **systematic-debugging**: 遇到任何 bug、测试失败或异常行为时使用，在提出修复方案之前执行
- **test-driven-development**: 在实现任何功能或修复 bug 时使用，在编写实现代码之前
- **using-git-worktrees**: 当需要开始与当前工作区隔离的功能开发，或在执行实现计划之前使用——通过原生工具或 git worktree 回退机制确保隔离工作区存在
- **using-superpowers**: 在开始任何对话时使用——确立如何查找和使用技能，要求在任何响应（包括澄清性问题）之前调用 Skill 工具
- **verification-before-completion**: 在宣称工作完成、已修复或测试通过之前使用，在提交或创建 PR 之前——必须运行验证命令并确认输出后才能声称成功；始终用证据支撑断言
- **workflow-runner**: 在 Claude Code / OpenClaw / Cursor 中直接运行 agency-orchestrator YAML 工作流——无需 API key，使用当前会话的 LLM 作为执行引擎。当用户提供 .yaml 工作流文件或要求多角色协作完成任务时触发。
- **writing-plans**: 当你有规格说明或需求用于多步骤任务时使用，在动手写代码之前
- **writing-skills**: 当创建新技能、编辑现有技能或在部署前验证技能是否有效时使用

### 如何使用

当任务匹配某个 skill 时，使用 `Skill` 工具加载对应 skill 并严格遵循其流程。绝不要用 Read 工具读取 SKILL.md 文件。

如果你认为哪怕只有 1% 的可能性某个 skill 适用于你正在做的事情，你必须调用该 skill 检查。
<!-- superpowers-zh:end -->
