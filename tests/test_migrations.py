"""迁移与模型的一致性。

生产跑 MySQL、测试跑 SQLite，所以「改了 model 忘了生成迁移」这种漂移在本地
是没人会发现的。这条测试在 SQLite 上跑一遍 alembic upgrade head，再把结果和
Base.metadata 比对，不需要本机有 MySQL。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = {"conversation", "message", "feedback", "agent_run"}


def _upgrade_head(connection: Connection) -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    # migrations/env.py 看到这个就直接复用现成连接，不再自己建 engine。
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


def _diff_against_models(connection: Connection) -> list:
    context = MigrationContext.configure(connection)
    return compare_metadata(context, Base.metadata)


@pytest.fixture(autouse=True)
def _dsn_for_env_py(monkeypatch):
    # migrations/env.py 在 import 期就会 load_settings()；config.local.json 是
    # gitignore 的，不能指望它存在，所以这里显式给一份最小配置。
    monkeypatch.setenv("DATABASE__DSN", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("AUTH__DEV_USER", '{"user_id": "1", "scopes": []}')


async def test_migrations_match_models():
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp) / 'm.db'}")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(_upgrade_head)
                tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
                diff = await conn.run_sync(_diff_against_models)
        finally:
            await engine.dispose()

    assert tables >= EXPECTED_TABLES

    real_diff = [d for d in diff if "alembic_version" not in repr(d)]
    assert real_diff == [], (
        f'模型和迁移已经不一致，跑 `make revision m="..."` 生成新的迁移：\n{real_diff}'
    )
