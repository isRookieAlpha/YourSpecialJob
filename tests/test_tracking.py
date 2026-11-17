# -*- coding: utf-8 -*-
"""跟踪调度器测试（mock 微信/发送/弱模型，真实测试库）。"""
import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

import agent.tracking as tracking_mod
from agent.db.tracking_dao import get_group_state, message_fingerprint
from agent.models.job import IngestStats
from agent.preferences import TrackingConfig

GROUP = "兼职1群"
DAY = date.today()
ROUND_START = datetime.now().astimezone() - timedelta(minutes=1)


def cfg(**kwargs) -> TrackingConfig:
    base = dict(
        enabled=True, interval_min=15, groups=[GROUP], push_enabled=True,
        push_limit=5, ledger_days=7, max_messages=500, push_lookback_min=1440,
        commute_origin="", amap_city="", max_commute_min=30,
        commission_min=None, prefer_light=True,
    )
    base.update(kwargs)
    return TrackingConfig(**base)


def msg(i: int, text: str, sender: str = "张姐", time: str = "10:15") -> dict:
    return {"index": i, "发送者": sender, "时间": time, "消息": text}


def fake_stats(group: str, new_inserted: int = 1) -> IngestStats:
    return IngestStats(
        group_name=group, target_date="2026-11-05", source_ref="src.json",
        total_messages=1, noise_filtered=0, batches=1, llm_valid=1,
        new_inserted=new_inserted, dup_in_run=0, dup_jsonl=0, dup_db=0, elapsed_sec=0.1,
    )


@pytest.fixture
def fake_state():
    return tracking_mod.TrackingState()


@pytest.fixture
def patch_wechat(monkeypatch):
    def setup(messages, inserted=1):
        calls = {"read": [], "ingest": [], "sent": []}

        async def fake_read(group, label, allow_empty=False, timeout=180):
            calls["read"].append((group, label))
            return messages

        async def fake_ingest(group, label, day, items, source="wechat_auto", source_ref=""):
            calls["ingest"].append(len(items))
            return fake_stats(group, inserted)

        monkeypatch.setattr(tracking_mod, "call_wechat_get_history", fake_read)
        monkeypatch.setattr(tracking_mod, "ingest_messages", fake_ingest)
        monkeypatch.setattr(tracking_mod, "find_source_ref", lambda g: "src.json")
        return calls

    return setup


async def insert_job(pool, summary="传菜", hash_suffix="a", offer_status="none",
                     work_date=None, commission_max=150) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """INSERT INTO jobs (group_name, source, source_ref, raw_text, content_hash,
                                 work_summary, work_date, commission_min, commission_max,
                                 commission_unit, location, offer_status)
               VALUES (%s, 'wechat_auto', 'src.json', %s, %s, %s, %s, 100, %s, '元/天', '静安寺', %s)
               RETURNING id""",
            (GROUP, f"原文{summary}{hash_suffix}", f"hash-{summary}-{hash_suffix}",
             summary, work_date or DAY.isoformat(), commission_max, offer_status),
        )
        return (await cur.fetchone())[0]


class TestProcessGroup:
    async def test_baseline_ingests_and_initializes(self, pool, patch_wechat, fake_state):
        """基线轮正常入库并标记初始化；不再标 skipped/baseline（基线也允许推送）"""
        calls = patch_wechat([msg(0, "酒店传菜日结110")])
        stats = await tracking_mod.process_group(pool, cfg(), GROUP, ROUND_START, fake_state)
        assert stats["baseline"] is True
        assert calls["ingest"] == [1]
        state = await get_group_state(pool, GROUP)
        assert state["initialized_at"] is not None

        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM push_log WHERE status = 'skipped'"
            )
            assert (await cur.fetchone())[0] == 0  # 基线不再写 skipped

    async def test_second_round_skips_seen(self, pool, patch_wechat, fake_state):
        messages = [msg(0, "酒店传菜日结110")]
        patch_wechat(messages)
        await tracking_mod.process_group(pool, cfg(), GROUP, ROUND_START, fake_state)

        calls = patch_wechat(messages)
        stats = await tracking_mod.process_group(pool, cfg(), GROUP, ROUND_START, fake_state)
        assert stats["new_messages"] == 0
        assert calls["ingest"] == []  # 已见指纹不再送模型

    async def test_known_content_recorded_without_ingest(self, pool, patch_wechat, fake_state):
        await insert_job(pool, "传菜", "k1")
        # 让该文本的 content_hash 与库中一致
        from agent.models.job import compute_content_hash
        text = "酒店传菜日结110"
        h = compute_content_hash(GROUP, text)
        async with pool.connection() as conn:
            await conn.execute("UPDATE jobs SET content_hash = %s WHERE work_summary = '传菜'", (h,))

        calls = patch_wechat([msg(0, text)])
        stats = await tracking_mod.process_group(pool, cfg(), GROUP, ROUND_START, fake_state)
        assert stats["known_content"] == 1
        assert calls["ingest"] == []

    async def test_read_failure_isolated(self, pool, monkeypatch, fake_state):
        async def failing_read(*args, **kwargs):
            raise RuntimeError("微信未登录")

        monkeypatch.setattr(tracking_mod, "call_wechat_get_history", failing_read)
        stats = await tracking_mod.process_group(pool, cfg(), GROUP, ROUND_START, fake_state)
        assert "error" in stats
        state = await get_group_state(pool, GROUP)
        assert "微信未登录" in state["last_error"]


class TestPushRound:
    async def test_no_sender(self, pool, monkeypatch, fake_state):
        monkeypatch.setattr(tracking_mod, "get_settings", lambda: type("S", (), {"wechat_sender_id": ""})())
        result = await tracking_mod.push_round(pool, cfg(), fake_state)
        assert result["pushed"] == 0

    async def test_sender_in_groups_skips(self, pool, monkeypatch, fake_state):
        monkeypatch.setattr(
            tracking_mod, "get_settings",
            lambda: type("S", (), {"wechat_sender_id": GROUP})(),
        )
        result = await tracking_mod.push_round(pool, cfg(groups=[GROUP]), fake_state)
        assert result["reason"] == "推送目标在监控群中"

    async def test_push_success_marks_sent(self, pool, monkeypatch, fake_state):
        jid = await insert_job(pool, "传菜", "p1", commission_max=150)
        sent = {}

        async def fake_send(sender, text):
            sent["text"] = text
            return {"status": "success"}

        async def fake_weather(city=None):
            return None


        monkeypatch.setattr(tracking_mod, "send_wechat_message", fake_send)
        monkeypatch.setattr(tracking_mod, "weather_today", fake_weather)
        monkeypatch.setattr(
            tracking_mod, "get_settings",
            lambda: type("S", (), {"wechat_sender_id": "Alpha"})(),
        )

        result = await tracking_mod.push_round(pool, cfg(), fake_state)
        assert result["pushed"] == 1
        assert "酒店传菜" in sent["text"] or "传菜" in sent["text"]

        async with pool.connection() as conn:
            cur = await conn.execute("SELECT status FROM push_log WHERE job_id = %s", (jid,))
            assert (await cur.fetchone())[0] == "sent"

    async def test_push_writes_short_term_memory(self, pool, monkeypatch, fake_state):
        """推送摘要写入互动线程短期记忆（agent 可引用"这条推送"）"""
        from langchain_core.messages import AIMessage

        await insert_job(pool, "传菜", "m1", commission_max=150)

        async def fake_send(sender, text):
            return {"status": "success"}

        async def fake_weather(city=None):
            return None

        class FakeAgent:
            def __init__(self):
                self.updates = []

            async def aupdate_state(self, config, values):
                self.updates.append((config, values))

        monkeypatch.setattr(tracking_mod, "send_wechat_message", fake_send)
        monkeypatch.setattr(tracking_mod, "weather_today", fake_weather)
        monkeypatch.setattr(
            tracking_mod, "get_settings",
            lambda: type("S", (), {"wechat_sender_id": "Alpha"})(),
        )

        agent = FakeAgent()
        result = await tracking_mod.push_round(pool, cfg(), fake_state, agent=agent)
        assert result["pushed"] == 1
        assert agent.updates
        config, values = agent.updates[0]
        assert config["configurable"]["thread_id"] == "wechat:Alpha"
        assert isinstance(values["messages"][0], AIMessage)
        assert "兼职跟踪" in values["messages"][0].content

    async def test_send_failure_marks_failed(self, pool, monkeypatch, fake_state):
        await insert_job(pool, "传菜", "f1", commission_max=150)

        async def failing_send(sender, text):
            raise RuntimeError("发送失败")

        async def fake_weather(city=None):
            return None


        monkeypatch.setattr(tracking_mod, "send_wechat_message", failing_send)
        monkeypatch.setattr(tracking_mod, "weather_today", fake_weather)
        monkeypatch.setattr(tracking_mod, "PUSH_RETRY_DELAY_SEC", 0)
        monkeypatch.setattr(
            tracking_mod, "get_settings",
            lambda: type("S", (), {"wechat_sender_id": "Alpha"})(),
        )

        result = await tracking_mod.push_round(pool, cfg(), fake_state)
        assert result["pushed"] == 0
        async with pool.connection() as conn:
            cur = await conn.execute("SELECT status, attempts FROM push_log WHERE channel='wechat'")
            status, attempts = await cur.fetchone()
        assert status == "failed" and attempts == 1

    async def test_excludes_non_none_offer_status(self, pool, monkeypatch, fake_state):
        await insert_job(pool, "传菜", "o1", offer_status="favorite", commission_max=150)
        monkeypatch.setattr(
            tracking_mod, "get_settings",
            lambda: type("S", (), {"wechat_sender_id": "Alpha"})(),
        )
        result = await tracking_mod.push_round(pool, cfg(), fake_state)
        assert result["pushed"] == 0

    async def test_excludes_past_work_date(self, pool, monkeypatch, fake_state):
        await insert_job(
            pool, "旧工作", "d1", work_date=(DAY - timedelta(days=2)).isoformat(), commission_max=150,
        )
        monkeypatch.setattr(
            tracking_mod, "get_settings",
            lambda: type("S", (), {"wechat_sender_id": "Alpha"})(),
        )
        result = await tracking_mod.push_round(pool, cfg(), fake_state)
        assert result["pushed"] == 0


class TestRunRound:
    async def test_wechat_access_disabled_skips(self, pool, fake_state):
        result = await tracking_mod.run_round(pool, fake_state, cfg(wechat_access_enabled=False))
        assert result["skipped"] is True
        assert result["reason"] == "微信访问已关闭"
        assert fake_state.last_error == "微信访问已关闭"

    async def test_disabled_skips(self, pool, fake_state):
        result = await tracking_mod.run_round(pool, fake_state, cfg(enabled=False))
        assert result.get("skipped") is True

    async def test_weak_model_missing_skips(self, pool, monkeypatch, fake_state):
        monkeypatch.setattr(tracking_mod, "is_model_configured", lambda kind: False)
        result = await tracking_mod.run_round(pool, fake_state, cfg())
        assert result.get("skipped") is True
        assert fake_state.last_error == "弱模型未配置"

    async def test_advisory_lock_busy_skips(self, pool, monkeypatch, fake_state):
        monkeypatch.setattr(tracking_mod, "is_model_configured", lambda kind: True)

        async def locked_conn():
            raise AssertionError("不应执行到 process_group")

        # 先占用 advisory lock
        async with pool.connection() as conn:
            await conn.execute("SELECT pg_advisory_lock(%s)", (tracking_mod.ADVISORY_LOCK_KEY,))
            try:
                result = await tracking_mod.run_round(pool, fake_state, cfg())
            finally:
                await conn.execute("SELECT pg_advisory_unlock(%s)", (tracking_mod.ADVISORY_LOCK_KEY,))
        assert result.get("skipped") is True
        assert "其他实例" in result.get("reason", "")

    async def test_round_runs_groups(self, pool, monkeypatch, fake_state):
        monkeypatch.setattr(tracking_mod, "is_model_configured", lambda kind: True)

        async def fake_process(pool_, cfg_, group, round_start, state):
            return {"group": group, "new_inserted": 2}

        async def fake_push(pool_, cfg_, state, **kwargs):
            return {"pushed": 1}

        async def fake_cleanup(*args, **kwargs):
            return 0

        monkeypatch.setattr(tracking_mod, "process_group", fake_process)
        monkeypatch.setattr(tracking_mod, "push_round", fake_push)
        monkeypatch.setattr(tracking_mod, "cleanup_ledger", fake_cleanup)
        monkeypatch.setattr(tracking_mod, "cleanup_push_log", fake_cleanup)

        result = await tracking_mod.run_round(pool, fake_state, cfg())
        assert result["new_inserted"] == 2
        assert result["pushed"] == 1
        assert fake_state.last_ok is True
        # 进度：按群计数，完成后 current=total、label 清空
        assert fake_state.progress["total"] == 1
        assert fake_state.progress["current"] == 1
        assert fake_state.progress["label"] == ""

class TestTrackingLoop:
    """直接调用调度循环：覆盖参数顺序与手动唤醒（此前漏测导致 cfg/state 传反）"""

    async def test_passes_state_and_cfg_in_order(self, pool, monkeypatch):
        recorded = {}

        async def fake_load_config(pool_):
            return cfg()

        async def fake_run_round(pool_, state, cfg_, force=False, agent=None):
            recorded.update(kind=type(state).__name__, enabled=cfg_.enabled, force=force)
            raise asyncio.CancelledError  # 结束循环

        async def fake_get_pool():
            return pool

        monkeypatch.setattr(tracking_mod, "load_config", fake_load_config)
        monkeypatch.setattr(tracking_mod, "run_round", fake_run_round)
        monkeypatch.setattr(tracking_mod, "get_pool", fake_get_pool)

        app = SimpleNamespace(state=SimpleNamespace(
            tracking_state=tracking_mod.TrackingState(),
            tracking_wakeup=asyncio.Event(),
        ))
        with pytest.raises(asyncio.CancelledError):
            await tracking_mod.tracking_loop(app)

        assert recorded == {"kind": "TrackingState", "enabled": True, "force": False}

    async def test_wakeup_forces_run_even_when_disabled(self, pool, monkeypatch):
        recorded = {}

        async def fake_load_config(pool_):
            return cfg(enabled=False)

        async def fake_run_round(pool_, state, cfg_, force=False, agent=None):
            recorded["force"] = force
            raise asyncio.CancelledError

        async def fake_get_pool():
            return pool

        monkeypatch.setattr(tracking_mod, "load_config", fake_load_config)
        monkeypatch.setattr(tracking_mod, "run_round", fake_run_round)
        monkeypatch.setattr(tracking_mod, "get_pool", fake_get_pool)

        wakeup = asyncio.Event()
        wakeup.set()
        app = SimpleNamespace(state=SimpleNamespace(
            tracking_state=tracking_mod.TrackingState(),
            tracking_wakeup=wakeup,
        ))
        with pytest.raises(asyncio.CancelledError):
            await tracking_mod.tracking_loop(app)

        assert recorded["force"] is True


async def test_push_only_current_round(pool, monkeypatch, fake_state):
    """round_start 之后没有新入库时，窗口外的历史记录不会被推送"""
    await insert_job(pool, "历史记录", "old1", commission_max=150)

    async def fake_send(sender, text):
        raise AssertionError("不应发送")

    monkeypatch.setattr(tracking_mod, "send_wechat_message", fake_send)
    monkeypatch.setattr(
        tracking_mod, "get_settings",
        lambda: type("S", (), {"wechat_sender_id": "Alpha"})(),
    )

    future = datetime.now().astimezone() + timedelta(hours=1)
    result = await tracking_mod.push_round(pool, cfg(), fake_state, round_start=future)
    assert result["pushed"] == 0
