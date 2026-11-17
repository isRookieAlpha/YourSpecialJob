# -*- coding: utf-8 -*-
"""会话 API 测试：列表 / 创建 / 删除 / checkpointer 清理联动。"""


async def test_empty_list(api_client):
    resp = await api_client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_create_and_list(api_client):
    resp = await api_client.post("/api/sessions", json={"title": "测试会话"})
    assert resp.status_code == 201
    created = resp.json()
    assert created["title"] == "测试会话"

    resp = await api_client.get("/api/sessions")
    assert [s["id"] for s in resp.json()] == [created["id"]]


async def test_delete_existing(api_client):
    created = (await api_client.post("/api/sessions", json={})).json()
    resp = await api_client.delete(f"/api/sessions/{created['id']}")
    assert resp.status_code == 204
    assert (await api_client.get("/api/sessions")).json() == []


async def test_delete_missing_404(api_client):
    resp = await api_client.delete("/api/sessions/not-exist")
    assert resp.status_code == 404


async def test_delete_calls_checkpointer(api_client, fake_checkpointer):
    created = (await api_client.post("/api/sessions", json={})).json()
    await api_client.delete(f"/api/sessions/{created['id']}")
    assert fake_checkpointer.deleted == [created["id"]]