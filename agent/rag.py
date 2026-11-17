# -*- coding: utf-8 -*-
"""简历 RAG：文档解析 → 切块 → embedding → pgvector 入库；语义检索片段。"""
import io
import re
from pathlib import Path
from uuid import uuid4

from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger

from agent.config import get_settings
from agent.db import resume_dao
from agent.db.resume_dao import RESUME_VECTOR_DIMS
from agent.models_factory import get_embeddings

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
ROOT_DIR = Path(__file__).resolve().parent.parent
RESUME_UPLOAD_DIR = ROOT_DIR / "backend" / "uploads" / "resume"


class ResumeParseError(ValueError):
    """简历解析/索引失败（对外显示中文原因）"""


def safe_filename(name: str) -> str:
    """保留中文/字母数字，去掉路径与危险字符"""
    base = Path(str(name or "")).name
    cleaned = re.sub(r"[^\w.\-一-鿿]+", "_", base)[:120]
    return cleaned or "resume"


def save_resume_file(filename: str, data: bytes) -> str:
    """落盘到 backend/uploads/resume/{uuid}_{safe_name}，返回绝对路径"""
    RESUME_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = RESUME_UPLOAD_DIR / f"{uuid4().hex}_{safe_filename(filename)}"
    target.write_bytes(data)
    return str(target)


def extract_text(filename: str, data: bytes) -> str:
    """按扩展名提取纯文本（PDF/DOCX/TXT/MD）"""
    ext = Path(str(filename or "")).suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    elif ext == ".docx":
        from docx import Document

        document = Document(io.BytesIO(data))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    elif ext in (".txt", ".md"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("gb18030", errors="replace")
    else:
        raise ResumeParseError(
            f"不支持的文件类型：{ext or '未知'}（支持 PDF/DOCX/TXT/MD）"
        )

    text = text.replace("\r\n", "\n").strip()
    if not text:
        raise ResumeParseError("未能从文件中提取到文字（可能是扫描版 PDF，请上传文字版）")
    return text


def split_text(text: str) -> list[str]:
    """切块 500 字 / 重叠 50 字"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n\n", "\n", "。", "；", "，", " "],
    )
    return [chunk.strip() for chunk in splitter.split_text(text) if chunk.strip()]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量 embedding（校验配置维度与表维度一致）"""
    dims = get_settings().embedding_dims
    if dims != RESUME_VECTOR_DIMS:
        raise ResumeParseError(
            f"简历向量表固定为 {RESUME_VECTOR_DIMS} 维，当前 embedding_dims={dims}；"
            "请到设置页把向量维度改为 1024 后重试"
        )
    vectors = await get_embeddings().aembed_documents(texts)
    if vectors and len(vectors[0]) != dims:
        raise ResumeParseError(
            f"embedding 模型返回 {len(vectors[0])} 维，配置为 {dims} 维；"
            "请到设置页统一维度后重建索引"
        )
    return vectors


async def index_resume(pool, file_id: int, text: str) -> int:
    """切块 → embedding → 替换该文件的 chunks，返回块数"""
    chunks = split_text(text)
    if not chunks:
        raise ResumeParseError("切块结果为空，请检查简历内容")
    vectors = await embed_texts(chunks)
    rows = [
        {
            "chunk_index": index,
            "page": None,
            "text": chunk,
            "embedding": vector,
            "metadata": {},
        }
        for index, (chunk, vector) in enumerate(zip(chunks, vectors))
    ]
    #1.先删旧块再写新块（重建/重复上传幂等）
    await resume_dao.delete_chunks(pool, file_id)
    count = await resume_dao.insert_chunks(pool, file_id, rows)
    await resume_dao.update_file_status(pool, file_id, status="ready", chunk_count=count)
    logger.info("简历索引完成 file_id={} 块数={}", file_id, count)
    return count


async def search_resume(pool, query: str, top_k: int = 4) -> list[dict]:
    """语义检索简历片段（score 为余弦相似度）"""
    query = str(query or "").strip()
    if not query:
        return []
    vector = await get_embeddings().aembed_query(query)
    return await resume_dao.search_chunks(pool, vector, top_k)