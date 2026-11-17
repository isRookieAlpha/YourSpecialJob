# -*- coding: utf-8 -*-
"""简历 RAG 数据访问层：文件元数据 + 切块（pgvector 余弦检索）。"""
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

RESUME_VECTOR_DIMS = 1024


def _vector_literal(values: list[float]) -> str:
    """Python 列表 → pgvector 文本格式（避免依赖 register_vector 适配器）"""
    return "[" + ",".join(f"{float(v):.7f}" for v in values) + "]"


async def create_file(
    pool: AsyncConnectionPool,
    *,
    filename: str,
    stored_path: str,
    mime: str,
    size_bytes: int,
    sha256: str,
) -> int:
    """登记简历文件，返回 id（解析/索引进度由 status/chunk_count 记录）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            """INSERT INTO resume_files (filename, stored_path, mime, size_bytes, sha256)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (filename, stored_path, mime, size_bytes, sha256),
        )
        return (await cur.fetchone())[0]


async def update_file_status(
    pool: AsyncConnectionPool,
    file_id: int,
    *,
    status: str,
    chunk_count: int | None = None,
    error: str | None = None,
) -> None:
    """更新索引状态/块数（chunk_count=None 时保持原值）"""
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE resume_files SET status = %s, chunk_count = COALESCE(%s, chunk_count), "
            "error = %s WHERE id = %s",
            (status, chunk_count, error, file_id),
        )


def _row_to_file(row) -> dict:
    return {
        "id": row[0],
        "filename": row[1],
        "stored_path": row[2],
        "mime": row[3],
        "size_bytes": row[4],
        "sha256": row[5],
        "chunk_count": row[6],
        "status": row[7],
        "error": row[8],
        "created_at": row[9].isoformat() if row[9] else None,
    }


_FILE_COLUMNS = (
    "id, filename, stored_path, mime, size_bytes, sha256, chunk_count, status, error, created_at"
)


async def list_files(pool: AsyncConnectionPool) -> list[dict]:
    """全部简历文件（按 id 升序，稳定展示）"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"SELECT {_FILE_COLUMNS} FROM resume_files ORDER BY id ASC"
        )
        rows = await cur.fetchall()
    return [_row_to_file(row) for row in rows]


async def get_file(pool: AsyncConnectionPool, file_id: int) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"SELECT {_FILE_COLUMNS} FROM resume_files WHERE id = %s", (file_id,)
        )
        row = await cur.fetchone()
    return _row_to_file(row) if row else None


async def delete_file(pool: AsyncConnectionPool, file_id: int) -> str | None:
    """删除文件记录（chunks 级联删除），返回 stored_path 供调用方删磁盘文件"""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM resume_files WHERE id = %s RETURNING stored_path", (file_id,)
        )
        row = await cur.fetchone()
    return row[0] if row else None


async def delete_chunks(pool: AsyncConnectionPool, file_id: int) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM resume_chunks WHERE file_id = %s", (file_id,)
        )
        return cur.rowcount


async def insert_chunks(pool: AsyncConnectionPool, file_id: int, rows: list[dict]) -> int:
    """批量写入切块（rows: chunk_index/page/text/embedding/metadata）"""
    if not rows:
        return 0
    params = [
        (
            file_id,
            row["chunk_index"],
            row.get("page"),
            row["text"],
            _vector_literal(row["embedding"]),
            Jsonb(row.get("metadata") or {}),
        )
        for row in rows
    ]
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                """INSERT INTO resume_chunks
                       (file_id, chunk_index, page, text, embedding, metadata)
                   VALUES (%s, %s, %s, %s, %s::vector, %s)""",
                params,
            )
    return len(params)


async def search_chunks(
    pool: AsyncConnectionPool, query_embedding: list[float], top_k: int = 4
) -> list[dict]:
    """余弦相似度检索（score = 1 - 余弦距离，越大越相关）"""
    literal = _vector_literal(query_embedding)
    async with pool.connection() as conn:
        cur = await conn.execute(
            """SELECT c.text, c.page, c.chunk_index, f.filename,
                      1 - (c.embedding <=> %s::vector) AS score
               FROM resume_chunks c
               JOIN resume_files f ON f.id = c.file_id
               ORDER BY c.embedding <=> %s::vector
               LIMIT %s""",
            (literal, literal, max(1, min(int(top_k), 20))),
        )
        rows = await cur.fetchall()
    return [
        {
            "text": row[0],
            "page": row[1],
            "chunk_index": row[2],
            "filename": row[3],
            "score": float(row[4]) if row[4] is not None else 0.0,
        }
        for row in rows
    ]


async def count_chunks(pool: AsyncConnectionPool) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM resume_chunks")
        return (await cur.fetchone())[0]