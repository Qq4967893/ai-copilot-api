"""数据库引擎与会话工厂。

engine 和 sessionmaker 是进程级单例，由 init_engine() 在 lifespan 里创建，
dispose_engine() 在关闭时释放。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import DatabaseSettings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_session_factory(bound: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False 是必须的：commit 之后还要读对象属性来拼响应，
    # 默认的过期行为会触发 lazy load，在 async 下直接抛 MissingGreenlet。
    return async_sessionmaker(bound, expire_on_commit=False, autoflush=False)


def init_engine(settings: DatabaseSettings) -> AsyncEngine:
    global _engine, _session_factory
    _engine = create_async_engine(
        settings.dsn.get_secret_value(),
        echo=settings.echo,
        pool_size=settings.pool_size,
        pool_recycle=settings.pool_recycle,
        pool_pre_ping=True,
    )
    _session_factory = _build_session_factory(_engine)
    return _engine


def bind_engine(bound: AsyncEngine) -> None:
    """测试用：把工厂指到另一个 engine（例如内存 SQLite）。"""
    global _engine, _session_factory
    _engine = bound
    _session_factory = _build_session_factory(bound)


def session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("database engine not initialised")
    return _session_factory


def engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("database engine not initialised")
    return _engine


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """请求作用域的 session 依赖。

    注意：不要在 StreamingResponse 的 body 生成器里用它 —— 依赖的清理发生在
    响应开始时，生成器跑起来时 session 已经关了。流式端点自己用
    `async with session_factory()() as session` 开。
    """
    async with session_factory()() as session:
        yield session
