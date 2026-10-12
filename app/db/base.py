"""ORM 基类与公共列类型。

模型只使用通用类型（String / Text / JSON / UtcDateTime），这样同一份 metadata
在测试的 SQLite 和生产的 MySQL 上都能建表。唯一的 dialect 分支收敛在下面的
UtcDateTime 里。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Dialect, MetaData, String, TypeDecorator
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from ulid import ULID

# 固定命名约定，让 Alembic autogenerate 产出的约束名稳定可比。
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}

# MySQL 专用表参数；SQLite 会忽略。
MYSQL_TABLE_ARGS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}

ULID_LENGTH = 26


def new_id() -> str:
    """ULID 字符串主键。

    不用自增：会话 id 会出现在 URL 里，自增会泄露总量且可被枚举。
    ULID 自带时间单调性，列表查询直接按主键倒序即可。
    """
    return str(ULID())


def utcnow() -> datetime:
    """时间戳统一由应用侧生成 UTC aware datetime。

    不用数据库默认值：MySQL 的 DATETIME 不存时区，把生成逻辑收在应用层是
    SQLite 和 MySQL 行为一致的唯一做法。
    """
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """始终以 UTC aware datetime 进出的时间列。

    MySQL 的 DATETIME 既不存时区、默认也不存小数秒，所以 `DateTime(timezone=True)`
    在 MySQL 上读回来的是一个丢了微秒的 naive datetime —— 客户端无从判断它是不是
    UTC，渲染本地时间就会错。SQLite 上又能原样保留 tzinfo，两边行为还不一致。

    这里统一：写入时转成 UTC，落库存 naive；读出时补回 UTC tzinfo。
    MySQL 用 DATETIME(6) 保住微秒。
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "mysql":
            return dialect.type_descriptor(mysql.DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # 拒绝 naive 输入，否则无从判断它到底是哪个时区的时间。
            raise ValueError("datetime 必须是 aware 的，用 app.db.base.utcnow()")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def id_column() -> Mapped[str]:
    return mapped_column(String(ULID_LENGTH), primary_key=True, default=new_id)


def created_at_column() -> Mapped[datetime]:
    return mapped_column(UtcDateTime, default=utcnow, nullable=False)
