"""会话业务逻辑。

ai-copilot-api 是 conversation 的 source of truth（技术设计 §7.1），
会话归属校验在这一层统一做：查不到或不属于当前用户，都返回 NOT_FOUND，
不区分「不存在」和「无权限」，避免泄露 id 是否存在。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity import Identity
from app.conversation.models import (
    AgentRun,
    Conversation,
    Feedback,
    Message,
    MessageRole,
    MessageStatus,
    RunStatus,
)
from app.conversation.repository import (
    AgentRunRepository,
    ConversationRepository,
    FeedbackRepository,
    MessageRepository,
)
from app.errors import NotFoundError

MAX_TITLE_LENGTH = 60
RECENT_MESSAGE_LIMIT = 20


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.feedback = FeedbackRepository(session)
        self.runs = AgentRunRepository(session)

    async def create_conversation(
        self, *, identity: Identity, title: str | None = None
    ) -> Conversation:
        return await self.conversations.create(user_id=identity.user_id, title=title)

    async def list_conversations(
        self, *, identity: Identity, limit: int, cursor: str | None
    ) -> list[Conversation]:
        return await self.conversations.list(user_id=identity.user_id, limit=limit, cursor=cursor)

    async def require_conversation(
        self, conversation_id: str, *, identity: Identity
    ) -> Conversation:
        conversation = await self.conversations.get(conversation_id, user_id=identity.user_id)
        if conversation is None:
            raise NotFoundError("conversation not found")
        return conversation

    async def list_messages(self, conversation_id: str) -> list[Message]:
        return await self.messages.list_for_conversation(conversation_id)

    async def recent_messages(self, conversation_id: str) -> list[Message]:
        return await self.messages.recent(conversation_id, RECENT_MESSAGE_LIMIT)

    async def delete_conversation(self, conversation_id: str, *, identity: Identity) -> None:
        conversation = await self.require_conversation(conversation_id, identity=identity)
        await self.conversations.soft_delete(conversation)

    async def append_user_message(self, *, conversation: Conversation, content: str) -> Message:
        if conversation.title is None:
            conversation.title = content.strip()[:MAX_TITLE_LENGTH] or None
        return await self.messages.add(
            Message(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=content,
                content_type="text",
                status=MessageStatus.COMPLETED,
            )
        )

    async def start_assistant_message(self, *, conversation_id: str) -> Message:
        """先落一条 pending 的助手消息，流结束后再改状态。

        这样客户端中途断开也能留下可追溯的记录，而不是一条凭空消失的回答。
        """
        return await self.messages.add(
            Message(
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content="",
                content_type="markdown",
                status=MessageStatus.PENDING,
            )
        )

    async def finish_assistant_message(
        self,
        message: Message,
        *,
        content: str,
        status: MessageStatus,
        payload: dict[str, Any] | None = None,
    ) -> Message:
        message.content = content
        message.status = status
        message.payload = payload
        await self.session.flush()
        return message

    async def record_run(
        self,
        *,
        identity: Identity,
        conversation_id: str,
        message_id: str,
        status: RunStatus,
        latency_ms: int,
        error_code: str | None = None,
    ) -> AgentRun:
        return await self.runs.add(
            AgentRun(
                conversation_id=conversation_id,
                message_id=message_id,
                user_id=identity.user_id,
                status=status,
                latency_ms=latency_ms,
                error_code=error_code,
            )
        )

    async def submit_feedback(
        self, *, message_id: str, identity: Identity, rating: str, comment: str | None
    ) -> Feedback:
        message = await self.messages.get(message_id)
        if message is None:
            raise NotFoundError("message not found")
        # 反馈只能针对自己会话里的消息。
        await self.require_conversation(message.conversation_id, identity=identity)

        existing = await self.feedback.get_by_message(message_id)
        if existing is not None:
            existing.rating = rating
            existing.comment = comment
            await self.session.flush()
            return existing
        return await self.feedback.add(
            Feedback(
                message_id=message_id,
                user_id=identity.user_id,
                rating=rating,
                comment=comment,
            )
        )
