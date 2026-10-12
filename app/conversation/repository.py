"""数据访问层。

只做查询和写入，不含业务判断 —— 业务规则放 service.py。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversation.models import (
    AgentRun,
    Conversation,
    ConversationStatus,
    Feedback,
    Message,
)


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, user_id: str, title: str | None) -> Conversation:
        conversation = Conversation(user_id=user_id, title=title)
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def get(self, conversation_id: str, *, user_id: str) -> Conversation | None:
        stmt = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.status == ConversationStatus.ACTIVE,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list(self, *, user_id: str, limit: int, cursor: str | None) -> list[Conversation]:
        # ULID 主键单调递增，按 id 倒序等价于按创建时间倒序，游标分页不需要额外列。
        stmt = (
            select(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.status == ConversationStatus.ACTIVE,
            )
            .order_by(Conversation.id.desc())
            .limit(limit)
        )
        if cursor:
            stmt = stmt.where(Conversation.id < cursor)
        return list((await self.session.execute(stmt)).scalars())

    async def soft_delete(self, conversation: Conversation) -> None:
        conversation.status = ConversationStatus.DELETED


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, message: Message) -> Message:
        self.session.add(message)
        await self.session.flush()
        return message

    async def list_for_conversation(self, conversation_id: str) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars())

    async def recent(self, conversation_id: str, limit: int) -> list[Message]:
        """最近 N 条，按时间正序返回。

        将来做 Context 压缩时（技术设计 §7.4），这里是「recent messages」的来源，
        summary 和结构化事实另外组装。
        """
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars())
        return list(reversed(rows))

    async def get(self, message_id: str) -> Message | None:
        stmt = select(Message).where(Message.id == message_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()


class FeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_message(self, message_id: str) -> Feedback | None:
        stmt = select(Feedback).where(Feedback.message_id == message_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, feedback: Feedback) -> Feedback:
        self.session.add(feedback)
        await self.session.flush()
        return feedback


class AgentRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, run: AgentRun) -> AgentRun:
        self.session.add(run)
        await self.session.flush()
        return run
