"""应用装配。

main.py 只做装配，不含任何业务逻辑 —— Agent Runtime 在 app/agent/ 里
（技术设计 §5.2：不要把 Agent 逻辑写进 route handler）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.factory import build_agent
from app.api import conversations, feedback, health, messages
from app.config.settings import Settings, load_settings
from app.db.session import dispose_engine, init_engine
from app.errors import register_exception_handlers
from app.observability.logging import configure_logging
from app.observability.middleware import RequestContextMiddleware
from app.observability.otel import setup_tracing

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    # 刻意不在启动时探测数据库：数据库短暂不可用时服务仍应能接收请求，
    # 由 /readyz 报告未就绪（技术设计 §22.4）。
    init_engine(settings.database)
    log.info("service started", port=settings.server.port, log_level=settings.log_level)
    try:
        yield
    finally:
        await dispose_engine()
        log.info("service stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="ai-copilot-api", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.agent = build_agent(settings)

    setup_tracing(app)
    app.add_middleware(RequestContextMiddleware)
    if settings.cors.allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors.allow_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Copilot-Request-Id"],
        )

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(conversations.router)
    app.include_router(messages.router)
    app.include_router(feedback.router)
    return app


app = create_app()
