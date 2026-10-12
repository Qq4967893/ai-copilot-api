from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import dispose_engine
from app.observability.middleware import REQUEST_ID_HEADER, RequestContextMiddleware


async def test_healthz(client: AsyncClient):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_readyz_ok(client: AsyncClient):
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


async def test_readyz_reports_unavailable_when_db_is_down(app):
    """数据库不可用时 /readyz 返回 503，但 /healthz 仍然 200。"""
    await dispose_engine()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ready = await ac.get("/readyz")
        live = await ac.get("/healthz")
    assert ready.status_code == 503
    assert ready.json()["status"] == "unavailable"
    assert live.status_code == 200


async def test_request_id_is_echoed(client: AsyncClient):
    resp = await client.get("/healthz", headers={REQUEST_ID_HEADER: "abc-123"})
    assert resp.headers[REQUEST_ID_HEADER] == "abc-123"


async def test_request_id_is_generated_when_absent(client: AsyncClient):
    resp = await client.get("/healthz")
    generated = resp.headers.get(REQUEST_ID_HEADER)
    assert generated and len(generated) == 26  # ULID


@pytest.mark.parametrize(
    "bad",
    ["has space", "", "x" * 129, "semi;colon"],
)
async def test_invalid_request_id_is_replaced_with_a_fresh_ulid(client: AsyncClient, bad: str):
    """不合法的值不能透传：它会被一路送进 MCP 的 X-Copilot-Request-id 头并被 401，
    把整轮 agent run 打成失败。"""
    resp = await client.get("/healthz", headers={REQUEST_ID_HEADER: bad})
    replaced = resp.headers.get(REQUEST_ID_HEADER)
    assert replaced and len(replaced) == 26  # ULID
    assert replaced != bad


async def test_invalid_request_id_is_rewritten_in_scope_for_downstream_handlers():
    """scope 里的请求头也要改写，下游（如 messages.py）读到的必须是净化后的值。"""
    captured: dict[bytes, bytes] = {}

    async def app(scope, receive, send):  # noqa: ANN001
        captured.update(dict(scope["headers"]))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message) -> None:  # noqa: ANN001
        return None

    middleware = RequestContextMiddleware(app)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-copilot-request-id", b"bad value"), (b"host", b"test")],
    }
    await middleware(scope, receive, send)

    rewritten = captured[b"x-copilot-request-id"]
    assert len(rewritten) == 26  # ULID
    # 其它头不能被动
    assert captured[b"host"] == b"test"
