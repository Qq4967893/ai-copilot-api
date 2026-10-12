"""身份与 MCP 头构造。"""

from __future__ import annotations

import pytest

from app.auth.identity import Identity, mcp_headers


def make_identity(user_id: str = "10001", scopes: list[str] | None = None) -> Identity:
    return Identity(
        user_id=user_id,
        display_name="dev",
        scopes=scopes if scopes is not None else ["admin.api.deposits.read"],
    )


def test_headers_match_internal_mcp_server_contract():
    headers = mcp_headers(
        identity=make_identity(),
        session_id="01H0000000000000000000000A",
        request_id="01H0000000000000000000000B",
        service_token="tok",
    )
    assert headers["Authorization"] == "Bearer tok"
    assert headers["X-Copilot-User-Id"] == "10001"
    assert headers["X-Copilot-Session-Id"] == "01H0000000000000000000000A"
    assert headers["X-Copilot-Request-Id"] == "01H0000000000000000000000B"
    assert headers["X-Copilot-Scopes"] == "admin.api.deposits.read"


def test_scopes_header_is_omitted_when_empty():
    """不传 scopes 时由 MCP 用它自己的 default_permissions。"""
    headers = mcp_headers(
        identity=make_identity(scopes=[]),
        session_id="s",
        request_id="r",
        service_token="tok",
    )
    assert "X-Copilot-Scopes" not in headers


def test_multiple_scopes_are_comma_separated():
    headers = mcp_headers(
        identity=make_identity(scopes=["a.read", "b.read"]),
        session_id="s",
        request_id="r",
        service_token="tok",
    )
    assert headers["X-Copilot-Scopes"] == "a.read,b.read"


@pytest.mark.parametrize(
    "bad",
    ["has space", "", "x" * 129, "semi;colon", "new\nline", "中文"],
)
def test_invalid_values_are_rejected_locally(bad: str):
    """不满足约束的值要在本服务就拦住，而不是换来一个语义模糊的上游 401。"""
    with pytest.raises(ValueError):
        mcp_headers(
            identity=make_identity(user_id=bad),
            session_id="s",
            request_id="r",
            service_token="tok",
        )


async def test_current_identity_comes_from_config(client):
    """身份贯穿到会话归属：配置里的 dev_user 就是会话的 owner。"""
    from sqlalchemy import select

    from app.conversation.models import Conversation
    from app.db.session import session_factory

    await client.post("/api/conversations", json={})
    async with session_factory()() as session:
        conversation = (await session.execute(select(Conversation))).scalar_one()
    assert conversation.user_id == "10001"
