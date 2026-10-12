"""UtcDateTime 的往返行为。

MySQL 的 DATETIME 不存时区、默认也丢小数秒，所以时间列必须由类型层统一兜住，
否则前端拿到一个 naive datetime 根本无从判断它是哪个时区的。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.conversation.models import Conversation
from app.db.base import utcnow
from app.db.session import session_factory


async def test_timestamps_round_trip_as_utc_aware(engine):
    async with session_factory()() as session:
        session.add(Conversation(user_id="u1", title="t"))
        await session.commit()

    # 换一个 session，强制真的从库里读回来，而不是命中身份映射
    async with session_factory()() as session:
        row = (await session.execute(select(Conversation))).scalar_one()

    assert row.created_at.tzinfo is not None
    assert row.created_at.utcoffset() == timedelta(0)


async def test_microseconds_are_preserved(engine):
    stamp = datetime(2026, 9, 21, 10, 30, 0, 123456, tzinfo=UTC)
    async with session_factory()() as session:
        session.add(Conversation(user_id="u1", created_at=stamp, updated_at=stamp))
        await session.commit()

    async with session_factory()() as session:
        row = (await session.execute(select(Conversation))).scalar_one()

    assert row.created_at == stamp


async def test_non_utc_input_is_normalised(engine):
    tokyo = timezone(timedelta(hours=9))
    stamp = datetime(2026, 9, 21, 19, 30, 0, tzinfo=tokyo)
    async with session_factory()() as session:
        session.add(Conversation(user_id="u1", created_at=stamp, updated_at=stamp))
        await session.commit()

    async with session_factory()() as session:
        row = (await session.execute(select(Conversation))).scalar_one()

    assert row.created_at == stamp  # 同一时刻
    assert row.created_at.utcoffset() == timedelta(0)  # 但以 UTC 呈现
    assert row.created_at.hour == 10


async def test_naive_datetime_is_rejected(engine):
    """naive 输入直接报错，而不是悄悄按本地时区解释。"""
    naive = datetime(2026, 9, 21, 10, 30)
    async with session_factory()() as session:
        session.add(Conversation(user_id="u1", created_at=naive, updated_at=naive))
        with pytest.raises(Exception, match="aware"):
            await session.commit()


def test_utcnow_is_aware():
    assert utcnow().tzinfo is not None
