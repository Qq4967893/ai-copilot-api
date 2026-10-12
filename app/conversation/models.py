"""AI Copilot 自有数据表。

只保存 AI Copilot 自己的数据（技术设计 §20.1）：conversation / message /
feedback / agent_run。业务主数据一律不落库，实时通过 MCP Tool 查询（§20.2）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    MYSQL_TABLE_ARGS,
    ULID_LENGTH,
    Base,
    UtcDateTime,
    created_at_column,
    id_column,
    utcnow,
)


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    DELETED = "deleted"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Conversation(Base):
    __tablename__ = "conversation"
    __table_args__ = (Index("ix_conversation_user_id", "user_id"), MYSQL_TABLE_ARGS)

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(32), default=ConversationStatus.ACTIVE, nullable=False
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Message(Base):
    __tablename__ = "message"
    __table_args__ = (Index("ix_message_conversation_id", "conversation_id"), MYSQL_TABLE_ARGS)

    id: Mapped[str] = id_column()
    conversation_id: Mapped[str] = mapped_column(
        String(ULID_LENGTH), ForeignKey("conversation.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_type: Mapped[str] = mapped_column(String(32), default="markdown", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=MessageStatus.COMPLETED, nullable=False)
    # 结构化结果单独放 JSON，不硬塞进 Markdown（技术设计 §20.3）。
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = created_at_column()


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = MYSQL_TABLE_ARGS

    id: Mapped[str] = id_column()
    message_id: Mapped[str] = mapped_column(
        String(ULID_LENGTH),
        ForeignKey("message.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # helpful / not_helpful
    rating: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class AgentRun(Base):
    """Agent Run 审计元数据（技术设计 §18.1）。

    本轮只写 status / latency_ms / error_code；model_profile、prompt_version、
    token 和 cost 相关列先建出来，接入真实 Agent 后再填，避免那时再改表。
    按 §18.3，这里只存 metadata，不存未经过滤的模型输入输出。
    """

    __tablename__ = "agent_run"
    __table_args__ = (Index("ix_agent_run_conversation_id", "conversation_id"), MYSQL_TABLE_ARGS)

    id: Mapped[str] = id_column()
    conversation_id: Mapped[str] = mapped_column(String(ULID_LENGTH), nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(ULID_LENGTH))
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.RUNNING, nullable=False)
    model_profile: Mapped[str | None] = mapped_column(String(64))
    model_version: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()
