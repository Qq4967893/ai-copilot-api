from __future__ import annotations

from httpx import AsyncClient


async def create(client: AsyncClient, title: str | None = None) -> str:
    resp = await client.post("/api/conversations", json={"title": title})
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_create_and_get(client: AsyncClient):
    cid = await create(client, "出金问题")
    resp = await client.get(f"/api/conversations/{cid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == cid
    assert body["title"] == "出金问题"
    assert body["status"] == "active"
    assert body["messages"] == []


async def test_get_unknown_returns_not_found(client: AsyncClient):
    resp = await client.get("/api/conversations/NOPE")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_list_is_newest_first(client: AsyncClient):
    first = await create(client, "a")
    second = await create(client, "b")
    resp = await client.get("/api/conversations")
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids == [second, first]


async def test_cursor_pagination(client: AsyncClient):
    ids = [await create(client, str(i)) for i in range(5)]
    page1 = (await client.get("/api/conversations", params={"limit": 2})).json()
    assert [i["id"] for i in page1["items"]] == ids[4:2:-1]
    assert page1["next_cursor"] == ids[3]

    page2 = (
        await client.get("/api/conversations", params={"limit": 2, "cursor": page1["next_cursor"]})
    ).json()
    assert [i["id"] for i in page2["items"]] == ids[2:0:-1]

    last = (
        await client.get("/api/conversations", params={"limit": 2, "cursor": page2["next_cursor"]})
    ).json()
    assert [i["id"] for i in last["items"]] == [ids[0]]
    assert last["next_cursor"] is None


async def test_limit_is_bounded(client: AsyncClient):
    resp = await client.get("/api/conversations", params={"limit": 1000})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_delete_is_soft_and_hides_conversation(client: AsyncClient):
    cid = await create(client, "x")
    assert (await client.delete(f"/api/conversations/{cid}")).status_code == 204
    assert (await client.get(f"/api/conversations/{cid}")).status_code == 404
    assert (await client.get("/api/conversations")).json()["items"] == []


async def test_title_is_derived_from_first_message(client: AsyncClient):
    cid = await create(client)
    await client.post(
        f"/api/conversations/{cid}/messages", json={"content": "用户 123 为什么出金失败"}
    )
    body = (await client.get(f"/api/conversations/{cid}")).json()
    assert body["title"] == "用户 123 为什么出金失败"


async def test_feedback_upsert(client: AsyncClient):
    cid = await create(client)
    await client.post(f"/api/conversations/{cid}/messages", json={"content": "hi"})
    messages = (await client.get(f"/api/conversations/{cid}")).json()["messages"]
    assistant = next(m for m in messages if m["role"] == "assistant")

    first = await client.post(
        f"/api/messages/{assistant['id']}/feedback", json={"rating": "helpful"}
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/messages/{assistant['id']}/feedback",
        json={"rating": "not_helpful", "comment": "答非所问"},
    )
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["rating"] == "not_helpful"


async def test_feedback_on_unknown_message(client: AsyncClient):
    resp = await client.post("/api/messages/NOPE/feedback", json={"rating": "helpful"})
    assert resp.status_code == 404


async def test_feedback_rating_is_validated(client: AsyncClient):
    resp = await client.post("/api/messages/X/feedback", json={"rating": "meh"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_ARGUMENT"
