"""错误信封的三条路径：AppError / 校验失败 / 未捕获异常。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.errors import AppError, ErrorCode, register_exception_handlers


@pytest.fixture
def error_app() -> FastAPI:
    application = FastAPI()
    register_exception_handlers(application)

    @application.get("/boom")
    async def boom():
        raise RuntimeError("secret internal detail")

    @application.get("/missing")
    async def missing():
        raise AppError(ErrorCode.NOT_FOUND, "conversation not found")

    @application.get("/needs-int")
    async def needs_int(n: int):
        return {"n": n}

    return application


async def _get(app: FastAPI, path: str):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.get(path)


async def test_app_error_envelope(error_app):
    resp = await _get(error_app, "/missing")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "NOT_FOUND", "message": "conversation not found"}}


async def test_validation_error_envelope(error_app):
    resp = await _get(error_app, "/needs-int?n=abc")
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "INVALID_ARGUMENT"
    # 不回显用户原始输入
    assert "abc" not in body["error"]["message"]


async def test_unhandled_exception_hides_internals(error_app):
    resp = await _get(error_app, "/boom")
    assert resp.status_code == 500
    assert resp.json() == {"error": {"code": "INTERNAL", "message": "internal error"}}
