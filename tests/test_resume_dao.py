# -*- coding: utf-8 -*-
"""简历 DAO 测试：文件 CRUD + 1024 维向量余弦检索（真实测试库）。"""
from agent.db.resume_dao import (
    RESUME_VECTOR_DIMS,
    count_chunks,
    create_file,
    delete_chunks,
    delete_file,
    get_file,
    insert_chunks,
    list_files,
    search_chunks,
    update_file_status,
)


def vec(index: int, value: float = 1.0) -> list[float]:
    """构造 1024 维稀疏向量（便于验证余弦排序）"""
    values = [0.0] * RESUME_VECTOR_DIMS
    values[index] = value
    return values


async def add_file(pool, name: str = "resume.pdf") -> int:
    return await create_file(
        pool,
        filename=name,
        stored_path=f"backend/uploads/resume/{name}",
        mime="application/pdf",
        size_bytes=1024,
        sha256="hash-" + name,
    )


class TestResumeFiles:
    async def test_crud(self, pool):
        file_id = await add_file(pool)
        files = await list_files(pool)
        assert len(files) == 1
        assert files[0]["filename"] == "resume.pdf"
        assert files[0]["status"] == "ready"

        await update_file_status(pool, file_id, status="ready", chunk_count=3)
        assert (await get_file(pool, file_id))["chunk_count"] == 3

        path = await delete_file(pool, file_id)
        assert path.endswith("resume.pdf")
        assert await list_files(pool) == []


class TestResumeChunks:
    async def test_insert_and_search_order(self, pool):
        f1 = await add_file(pool, "a.pdf")
        f2 = await add_file(pool, "b.pdf")
        await insert_chunks(pool, f1, [
            {"chunk_index": 0, "page": 1, "text": "自我介绍：擅长活动执行", "embedding": vec(0)},
        ])
        await insert_chunks(pool, f2, [
            {"chunk_index": 0, "page": 2, "text": "技能：英语翻译", "embedding": vec(1)},
        ])
        assert await count_chunks(pool) == 2

        hits = await search_chunks(pool, vec(0), top_k=2)
        assert len(hits) == 2
        assert hits[0]["filename"] == "a.pdf"
        assert hits[0]["score"] > hits[1]["score"]

    async def test_delete_file_cascades_chunks(self, pool):
        file_id = await add_file(pool)
        await insert_chunks(pool, file_id, [
            {"chunk_index": 0, "page": None, "text": "x", "embedding": vec(2)},
        ])
        assert await count_chunks(pool) == 1
        await delete_file(pool, file_id)
        assert await count_chunks(pool) == 0

    async def test_delete_chunks(self, pool):
        file_id = await add_file(pool)
        await insert_chunks(pool, file_id, [
            {"chunk_index": 0, "page": None, "text": "x", "embedding": vec(3)},
            {"chunk_index": 1, "page": None, "text": "y", "embedding": vec(4)},
        ])
        assert await delete_chunks(pool, file_id) == 2
        assert await count_chunks(pool) == 0