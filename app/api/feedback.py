"""Feedback API（技术设计 §22.3）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import CreateFeedbackRequest, FeedbackOut
from app.auth.dependency import IdentityDep, SessionDep
from app.conversation.service import ConversationService

router = APIRouter(prefix="/api/messages", tags=["feedback"])


@router.post("/{message_id}/feedback")
async def submit_feedback(
    message_id: str,
    body: CreateFeedbackRequest,
    identity: IdentityDep,
    session: SessionDep,
) -> FeedbackOut:
    """提交或更新反馈。

    同一条消息重复提交是 upsert —— 用户改主意是正常操作，不该报冲突。
    """
    service = ConversationService(session)
    feedback = await service.submit_feedback(
        message_id=message_id,
        identity=identity,
        rating=body.rating,
        comment=body.comment,
    )
    await session.commit()
    return FeedbackOut.model_validate(feedback)
