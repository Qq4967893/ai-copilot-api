"""Alembic 环境。

DSN 从应用配置读取（config/config.local.json + 环境变量），不从 alembic.ini 取，
这样迁移和服务永远指向同一个库。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from app.config.settings import load_settings

# 必须 import 模型模块，否则 Base.metadata 是空的，autogenerate 会生成删表迁移。
from app.conversation import models as _models  # noqa: F401
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = load_settings()
config.set_main_option("sqlalchemy.url", settings.database.dsn.get_secret_value())


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # 让 autogenerate 能发现列类型变化，否则改了长度不会生成迁移。
        compare_type=True,
        compare_server_default=True,
        # SQLite 不支持大多数 ALTER，测试里跑迁移需要 batch 模式。
        render_as_batch=connection.dialect.name == "sqlite",
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    # 测试里会把一个已有的 AsyncConnection 塞进 config.attributes，直接复用。
    existing = config.attributes.get("connection")
    if existing is not None:
        _do_run_migrations(existing)
        return
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
