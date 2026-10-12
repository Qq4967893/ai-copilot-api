"""HTTP 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_MESSAGE_LENGTH = 4000


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=60)


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    content: str
    content_type: str
    status: str
    payload: dict[str, Any] | None
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageOut] = Field(default_factory=list)


class ConversationPage(BaseModel):
    items: list[ConversationSummary]
    # 下一页游标；None 表示没有更多。
    next_cursor: str | None = None


class CreateMessageRequest(BaseModel):
    # 输入长度上限是第一道输入边界（技术设计 §17.1）。
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)


class CreateFeedbackRequest(BaseModel):
    rating: Literal["helpful", "not_helpful"]
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    message_id: str
    rating: str
    comment: str | None
    created_at: datetime
