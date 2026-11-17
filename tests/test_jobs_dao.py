# -*- coding: utf-8 -*-
"""jobs_dao 测试：入库去重统计 + 四类 SQL 硬过滤边界（真实本地 PG）。"""
from datetime import date

from agent.db.jobs_dao import JobQueryFilters, insert_extracted, query_jobs
from agent.models.job import JobExtractItem, JobMessage

FALLBACK_DATE = date(2026, 11, 3)


def make_item(raw: str, **job_kwargs) -> JobExtractItem:
    """构造一条有效提取项（job 字段按需覆盖）"""
    return JobExtractItem(raw_text=raw, is_valid_job=True, job=JobMessage(**job_kwargs))


ITEMS = [
    make_item(
        "明天武大附近酒店传菜，日结110左右，加微信abc",
        work_summary="酒店传菜", physical_intensity="轻", is_physical_labor=True,
        work_date=date(2026, 11, 5), commission_min=90, commission_max=110,
        commission_unit="元/天", location="武汉大学附近", contact="微信abc",
    ),
    make_item(
        "光谷仓库搬货，200一天，重体力",
        work_summary="仓库搬货", physical_intensity="重", is_physical_labor=True,
        work_date=date(2026, 11, 10), commission_min=200, commission_max=200,
        commission_unit="元/天", location="光谷仓库",
    ),
    make_item(
        "街道发传单，速来",
        work_summary="发传单",  # 体力强度未知（不设 physical_intensity）
    ),
]


async def test_insert_and_dedup_same_batch(pool):
    """首次插入全部成功；重复插入全部被 UNIQUE 拦截"""
    new1, dup1 = await insert_extracted(pool, "兼职1群", ITEMS, "src.json", FALLBACK_DATE)
    assert (new1, dup1) == (3, 0)

    new2, dup2 = await insert_extracted(pool, "兼职1群", ITEMS, "src.json", FALLBACK_DATE)
    assert (new2, dup2) == (0, 3)

    rows = await query_jobs(pool, JobQueryFilters())
    assert len(rows) == 3


async def test_invalid_items_skipped(pool):
    """is_valid_job=False 的条目不占库"""
    noise = JobExtractItem(raw_text="动画表情", is_valid_job=False, job=None)
    new, dup = await insert_extracted(pool, "兼职1群", [noise, *ITEMS[:1]], "src.json", FALLBACK_DATE)
    assert (new, dup) == (1, 0)


async def test_work_date_fallback(pool):
    """消息无日期时回填查询日期"""
    await insert_extracted(pool, "兼职1群", ITEMS, "src.json", FALLBACK_DATE)
    rows = await query_jobs(pool, JobQueryFilters(date_start=FALLBACK_DATE, date_end=FALLBACK_DATE))
    assert len(rows) == 1
    assert rows[0]["work_summary"] == "发传单"


async def test_filter_date_range(pool):
    """日期区间过滤"""
    await insert_extracted(pool, "兼职1群", ITEMS, "src.json", FALLBACK_DATE)
    rows = await query_jobs(pool, JobQueryFilters(
        date_start=date(2026, 11, 4), date_end=date(2026, 11, 6)))
    assert [r["work_summary"] for r in rows] == ["酒店传菜"]


async def test_filter_commission_overlap_with_tolerance(pool):
    """佣金区间重叠+tolerance：期望 90~110 + 容差 30 → 招聘 90~110 命中，200 不命中，佣金属空保留"""
    await insert_extracted(pool, "兼职1群", ITEMS, "src.json", FALLBACK_DATE)
    rows = await query_jobs(pool, JobQueryFilters(
        commission_min=90, commission_max=110, tolerance=30))
    names = {r["work_summary"] for r in rows}
    assert names == {"酒店传菜", "发传单"}  # 发传单佣金 NULL：开放视为命中


async def test_filter_intensity(pool):
    """体力强度过滤：include_unknown 控制空值是否保留"""
    await insert_extracted(pool, "兼职1群", ITEMS, "src.json", FALLBACK_DATE)

    rows = await query_jobs(pool, JobQueryFilters(
        physical_intensities=["轻"], include_unknown_intensity=False))
    assert [r["work_summary"] for r in rows] == ["酒店传菜"]

    rows_unk = await query_jobs(pool, JobQueryFilters(
        physical_intensities=["轻"], include_unknown_intensity=True))
    assert {r["work_summary"] for r in rows_unk} == {"酒店传菜", "发传单"}


async def test_filter_location_keywords(pool):
    """地点关键词 ILIKE 多关键词 OR 匹配"""
    await insert_extracted(pool, "兼职1群", ITEMS, "src.json", FALLBACK_DATE)
    rows = await query_jobs(pool, JobQueryFilters(location_keywords=["武汉大学", "光谷"]))
    assert {r["work_summary"] for r in rows} == {"酒店传菜", "仓库搬货"}


async def test_empty_result(pool):
    """无匹配返回空列表不报错"""
    await insert_extracted(pool, "兼职1群", ITEMS, "src.json", FALLBACK_DATE)
    rows = await query_jobs(pool, JobQueryFilters(date_start=date(2027, 1, 1)))
    assert rows == []

async def test_query_returns_source_and_offer_fields(pool):
    """M2.5：查询行包含来源字段与 offer_status，时间已转本地 ISO 字符串"""
    await insert_extracted(pool, "兼职1群", ITEMS[:1], "src.json", FALLBACK_DATE)
    rows = await query_jobs(pool, JobQueryFilters())
    row = rows[0]
    assert row["source"] == "wechat_auto"
    assert row["source_ref"] == "src.json"
    assert row["offer_status"] == "none"
    assert isinstance(row["ingested_at"], str) and "T" in row["ingested_at"]


async def test_filter_group_and_source(pool):
    """group_name / source 精确过滤（截图批次只看本次入库数据）"""
    await insert_extracted(pool, "兼职1群", ITEMS[:1], "src.json", FALLBACK_DATE)
    await insert_extracted(
        pool, "图片上传_20261105_153012", ITEMS[1:2], "img.img", FALLBACK_DATE,
        source="manual_image",
    )

    rows = await query_jobs(pool, JobQueryFilters(group_name="图片上传_20261105_153012"))
    assert [r["work_summary"] for r in rows] == ["仓库搬货"]

    rows = await query_jobs(pool, JobQueryFilters(source="manual_image"))
    assert [r["work_summary"] for r in rows] == ["仓库搬货"]

    rows = await query_jobs(pool, JobQueryFilters(source="wechat_auto"))
    assert [r["work_summary"] for r in rows] == ["酒店传菜"]

    rows = await query_jobs(pool, JobQueryFilters(group_name="不存在的群"))
    assert rows == []


async def test_filter_ingested_time_window(pool):
    """ingested_after/before：区分本轮新数据与数据库历史数据（两段式推荐）"""
    from datetime import timedelta

    await insert_extracted(pool, "兼职1群", ITEMS[:1], "src.json", FALLBACK_DATE)
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT ingested_at FROM jobs WHERE work_summary = '酒店传菜'")
        boundary = (await cur.fetchone())[0] + timedelta(milliseconds=1)
    await insert_extracted(pool, "兼职1群", ITEMS[1:2], "src.json", FALLBACK_DATE)

    new_rows = await query_jobs(pool, JobQueryFilters(ingested_after=boundary))
    assert [r["work_summary"] for r in new_rows] == ["仓库搬货"]

    old_rows = await query_jobs(pool, JobQueryFilters(ingested_before=boundary))
    assert [r["work_summary"] for r in old_rows] == ["酒店传菜"]
