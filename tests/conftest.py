"""测试固件。

统一跑内存 SQLite：单测不该依赖本机有 MySQL。模型只用通用列类型，所以同一份
metadata 在两边都能建表；两者的差异由 test_migrations.py 的漂移检查兜住。
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config.settings import (
    AgentSettings,
    AuthSettings,
    DatabaseSettings,
    DevUser,
    Settings,
)

# 必须在 create_all 之前导入模型模块，否则 Base.metadata 是空的、一张表都不会建。
# 用 from-import：`import app.conversation.models` 会绑定名字 `app`，和下面的
# app fixture 撞名。
from app.conversation import models as _models  # noqa: F401
from app.db.base import Base
from app.db.session import bind_engine, dispose_engine


def test_dsn(directory: Path) -> str:
    """文件型 SQLite，而不是 :memory:。

    :memory: 必须配 StaticPool（每条新连接都是一个空库），而 StaticPool 只有
    一条连接 —— 流式端点的收尾 session 和同时进来的请求会互相等死。用文件库
    就能像生产一样发多条连接出去。
    """
    return f"sqlite+aiosqlite:///{directory / 'test.db'}"


def build_settings(dsn: str = "sqlite+aiosqlite:///:memory:") -> Settings:
    return Settings(
        database=DatabaseSettings(dsn=SecretStr(dsn)),
        auth=AuthSettings(
            dev_user=DevUser(user_id="10001", display_name="dev", scopes=["admin.api.ping.read"])
        ),
        # 默认走 echo：这些测试验证的是 HTTP/持久化/流式管道，不该依赖 LLM。
        agent=AgentSettings(impl="echo"),
    )


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    with tempfile.TemporaryDirectory() as tmp:
        eng = create_async_engine(test_dsn(Path(tmp)))
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        bind_engine(eng)
        yield eng
        await dispose_engine()


@pytest.fixture
def app(engine: AsyncEngine) -> FastAPI:
    from app.main import create_app

    application = create_app(build_settings())
    # engine 已由 fixture 绑定好，跳过 lifespan 里的 init_engine。
    application.router.lifespan_context = _noop_lifespan
    application.state.settings = build_settings()
    return application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def _noop_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
