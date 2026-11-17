# -*- coding: utf-8 -*-
"""心动的offer API 测试（真实测试库 + mock 导出/地图）。"""
import backend.routers.offers as offers_mod


def manual_payload(**kwargs) -> dict:
    base = {
        "work_summary": "酒店传菜",
        "work_date": "2026-11-05",
        "work_time": "7:30-21:00",
        "commission_min": 110,
        "commission_max": 110,
        "commission_unit": "元/天",
        "location": "静安寺",
    }
    base.update(kwargs)
    return base


async def create_offer(client, **kwargs) -> dict:
    resp = await client.post("/api/offers", json=manual_payload(**kwargs))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_list_empty(api_client):
    resp = await api_client.get("/api/offers")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["stats"] == {
        "favorite_count": 0, "todo_count": 0,
        "estimated_total_income": 0, "income_unknown_count": 0,
    }


async def test_create_and_list(api_client):
    created = await create_offer(api_client)
    assert created["source"] == "manual_form"
    assert created["offer_status"] == "favorite"
    assert created["source_label"].startswith("手动录入")

    resp = await api_client.get("/api/offers")
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["stats"]["favorite_count"] == 1


async def test_create_validation(api_client):
    resp = await api_client.post("/api/offers", json=manual_payload(commission_min=200, commission_max=100))
    assert resp.status_code == 422
    resp = await api_client.post("/api/offers", json={"work_date": "2026-11-05"})
    assert resp.status_code == 422


async def test_status_flow_and_stats(api_client):
    a = await create_offer(api_client)
    b = await create_offer(api_client, work_summary="布展", is_todo=True)

    resp = await api_client.patch("/api/offers/status", json={
        "job_ids": [a["id"]], "status": "todo", "mode": "promote",
    })
    assert resp.status_code == 200
    assert resp.json()["items"][0]["offer_status"] == "todo"

    data = (await api_client.get("/api/offers")).json()
    assert data["stats"]["todo_count"] == 2  # a + b
    assert data["stats"]["estimated_total_income"] == 220.0


async def test_promote_does_not_downgrade(api_client):
    a = await create_offer(api_client, is_todo=True)
    resp = await api_client.patch("/api/offers/status", json={
        "job_ids": [a["id"]], "status": "favorite", "mode": "promote",
    })
    data = resp.json()
    assert data["items"] == []
    assert data["skipped"][0]["id"] == a["id"]


async def test_update_offer(api_client):
    a = await create_offer(api_client)
    resp = await api_client.put(f"/api/offers/{a['id']}", json={"work_summary": "宴会传菜", "location": "人民广场"})
    assert resp.status_code == 200
    assert resp.json()["work_summary"] == "宴会传菜"
    assert resp.json()["location"] == "人民广场"

    resp = await api_client.put("/api/offers/999999", json={"work_summary": "x"})
    assert resp.status_code == 404


async def test_delete_manual_hard(api_client):
    a = await create_offer(api_client)
    resp = await api_client.delete(f"/api/offers/{a['id']}")
    assert resp.json() == {"removed": True, "mode": "hard"}
    assert (await api_client.get("/api/offers")).json()["items"] == []


async def test_export_flow(api_client, monkeypatch, tmp_path):
    """导出：mock render_jobs_pdf，断言仅导出 ToDo 且返回路径"""
    calls = {}

    def fake_render(rows, filters_summary, title=None, filename_prefix="job_report"):
        calls["rows"] = rows
        calls["filters"] = filters_summary
        calls["prefix"] = filename_prefix
        return tmp_path / "offer_todo_report_test.pdf"

    monkeypatch.setattr(offers_mod, "render_jobs_pdf", fake_render)

    a = await create_offer(api_client, work_summary="传菜", is_todo=True)
    await create_offer(api_client, work_summary="布展")  # favorite，不应导出

    resp = await api_client.post("/api/offers/export", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["filename"] == "offer_todo_report_test.pdf"
    assert calls["prefix"] == "offer_todo_report"
    assert [r["id"] for r in calls["rows"]] == [a["id"]]


async def test_export_empty_todo(api_client):
    resp = await api_client.post("/api/offers/export", json={})
    assert resp.status_code == 400
    assert "没有可导出" in resp.json()["detail"]


async def test_map_success(api_client, monkeypatch):
    import json
    from urllib.parse import unquote

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 16

    async def fake_map(addresses):
        return png, 1, ["静安寺"]

    monkeypatch.setattr(offers_mod, "build_static_map", fake_map)
    # 准备一条 ToDo 记录，图例应包含其标题
    a = await create_offer(api_client, work_summary="宴会传菜", is_todo=True, location="静安寺")

    resp = await api_client.get("/api/offers/map.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == png
    assert resp.headers["X-Map-Skipped"] == "1"
    legend = json.loads(unquote(resp.headers["X-Map-Legend"]))
    assert legend == [{"label": "A", "location": "静安寺", "titles": ["宴会传菜"]}]
    assert a["id"]


async def test_map_error_branches(api_client, monkeypatch):
    async def no_key(addresses):
        raise RuntimeError("未配置高德 Web 服务 key")

    monkeypatch.setattr(offers_mod, "build_static_map", no_key)
    assert (await api_client.get("/api/offers/map.png")).status_code == 503

    async def no_place(addresses):
        raise RuntimeError("没有可展示的 ToDo 地点")

    monkeypatch.setattr(offers_mod, "build_static_map", no_place)
    assert (await api_client.get("/api/offers/map.png")).status_code == 404

    async def broken(addresses):
        raise RuntimeError("地图服务暂不可用")

    monkeypatch.setattr(offers_mod, "build_static_map", broken)
    assert (await api_client.get("/api/offers/map.png")).status_code == 502