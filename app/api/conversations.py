"""Conversation API（技术设计 §22.1）。"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.schemas import (
    ConversationDetail,
    ConversationPage,
    ConversationSummary,
    CreateConversationRequest,
    MessageOut,
)
from app.auth.dependency import IdentityDep, SessionDep
from app.conversation.service import ConversationService

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: CreateConversationRequest,
    identity: IdentityDep,
    session: SessionDep,
) -> ConversationSummary:
    service = ConversationService(session)
    conversation = await service.create_conversation(identity=identity, title=body.title)
    await session.commit()
    return ConversationSummary.model_validate(conversation)


@router.get("")
async def list_conversations(
    identity: IdentityDep,
    session: SessionDep,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None),
) -> ConversationPage:
    service = ConversationService(session)
    # 多取一条用来判断是否还有下一页，避免额外的 count 查询。
    rows = await service.list_conversations(identity=identity, limit=limit + 1, cursor=cursor)
    has_more = len(rows) > limit
    items = rows[:limit]
    return ConversationPage(
        items=[ConversationSummary.model_validate(row) for row in items],
        next_cursor=items[-1].id if has_more and items else None,
    )


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    identity: IdentityDep,
    session: SessionDep,
) -> ConversationDetail:
    service = ConversationService(session)
    conversation = await service.require_conversation(conversation_id, identity=identity)
    messages = await service.list_messages(conversation.id)
    detail = ConversationDetail.model_validate(conversation)
    detail.messages = [MessageOut.model_validate(m) for m in messages]
    return detail


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    identity: IdentityDep,
    session: SessionDep,
) -> None:
    service = ConversationService(session)
    await service.delete_conversation(conversation_id, identity=identity)
    await session.commit()
