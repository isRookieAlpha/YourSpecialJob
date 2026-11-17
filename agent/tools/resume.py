# -*- coding: utf-8 -*-
"""简历检索工具：按语义检索已上传简历中的相关片段。"""
from langchain_core.tools import tool
from loguru import logger

from agent.db.pool import get_pool
from agent.rag import search_resume as rag_search


@tool(parse_docstring=True)
async def search_resume(query: str, top_k: int = 4) -> str:
    """检索已上传简历中的相关经历/技能/项目/教育信息。

    回答中介的问题、写自我介绍/报名话术、回答"你会什么/做过什么"之前必须先调用本工具；
    只使用检索到的真实内容，禁止编造。

    Args:
        query: 检索关键词或问题（如"自我介绍""活动执行经历""英语能力"）。
        top_k: 返回片段数，默认 4。

    Returns:
        相关片段（文件名/块序号/相似度/正文）；简历库为空或无命中时返回明确提示。
    """
    query = str(query or "").strip()
    if not query:
        return "检索词为空，请在调用时提供 query（如“自我介绍”）。"

    pool = await get_pool()
    try:
        hits = await rag_search(pool, query, top_k)
    except Exception as e:
        logger.warning("简历检索失败：{}", e)
        return f"简历检索失败：{type(e).__name__}，请检查设置页的 embedding 配置。"

    if not hits:
        return (
            "简历库为空或未命中相关内容。请让用户先在设置页「简历与个人资料」上传简历；"
            "不要编造简历内容。"
        )

    lines: list[str] = []
    for index, hit in enumerate(hits, 1):
        page = f"，第 {hit['page']} 页" if hit.get("page") else ""
        lines.append(
            f"[{index}] {hit.get('filename')}（块 {hit.get('chunk_index')}{page}，"
            f"相似度 {hit.get('score', 0):.2f}）\n{str(hit.get('text') or '').strip()}"
        )
    return "\n\n".join(lines)