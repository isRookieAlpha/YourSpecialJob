# -*- coding: utf-8 -*-
"""
全局配置：从项目根目录 .env 读取（pydantic-settings），并提供 loguru 双输出（控制台 + 文件）。

.env 变量命名沿用大写前缀 + _API_KEY/_BASE_URL 约定（参照 SggLangchainLearn 风格）。
设置页（Tab3）修改后的值将存入 PostgreSQL app_settings 表并优先于 .env（M3 实现）。
"""
import sys
from functools import lru_cache
from pathlib import Path

from loguru import logger
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#1.项目根目录（agent/ 的上一级）
ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """全局配置项（字段名与 .env 变量名一一对应）"""

    #2.数据库（本地 PostgreSQL，conda 环境 SggYourSpecialJobPG，trust 本地认证）
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_db: str = "yourspecialjob"

    #3.强模型（主 agent 推理与推荐排序）
    strong_model: str = ""
    strong_api_key: str = ""
    strong_base_url: str = ""

    #4.弱模型（结构化提取 / 偏好提取 / 截图解析，需支持多模态）
    weak_model: str = ""
    weak_api_key: str = ""
    weak_base_url: str = ""

    #5.备用模型（主模型多次重试失败后 fallback）
    backup_model: str = ""
    backup_api_key: str = ""
    backup_base_url: str = ""

    #6.embedding（简历 RAG 与长期记忆索引，OpenAI 兼容接口）
    embedding_model: str = ""
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_dims: int = 1024

    #7.高德开放平台（两种 key 类型不同：Web服务给 MCP / Web端JS给前端地图）
    amap_server_key: str = ""        # 后端 MCP 用（AMAP_SERVER_KEY）
    amap_maps_api_key: str = ""      # 阿里云百炼/魔搭风格变量名（AMAP_MAPS_API_KEY），空则回填 server_key
    amap_url: str = ""               # 魔搭 Remote MCP 地址（streamable HTTP），空则本地 npx
    amap_js_key: str = ""
    amap_security_code: str = ""

    #8.微信 MCP
    wechat_sender_id: str = ""

    #9.偏好设置（设置页偏好卡片，M6 长期记忆/推荐使用；来自 app_settings）
    preferences: dict = Field(default_factory=dict)

    model_config = SettingsConfigDict(
        # 相对路径（约定所有命令在项目根目录运行，不写死盘符绝对路径）
        env_file="agent/.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def _merge_amap_keys(self):
        """AMAP_MAPS_API_KEY 非空时回填 amap_server_key（用户 .env 常用该变量名）"""
        if not self.amap_server_key and self.amap_maps_api_key:
            self.amap_server_key = self.amap_maps_api_key
        return self


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例（M3 设置页动态更新时清缓存重建）"""
    return Settings()


def setup_logging() -> None:
    """loguru 双输出：控制台（INFO）+ 文件（DEBUG，按天轮转，保留 7 天）"""
    # Windows 控制台默认 GBK，微信消息含 emoji 会导致 UnicodeEncodeError，统一切换 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logger.remove()  # 移除 loguru 默认 handler，避免重复输出
    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - {message}",
    )
    logger.add(
        ROOT_DIR / "logs" / "app_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="1 day",
        retention="7 days",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <7} | {name}:{line} - {message}",
    )
