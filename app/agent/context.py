"""组装 Agent Run 的模型输入（技术设计 §7.3）。

刻意不使用 Agents SDK 的 Session：技术设计 §7.1 明确要求不要同时维护两套
独立的长期 Memory。我们自己的数据库已经是 conversation 的 source of truth，
每轮从 DB 取最近若干条消息拼成输入即可。
"""

from __future__ import annotations

from agents.items import TResponseInputItem

from app.agent.protocol import AgentRequest
from app.conversation.models import MessageRole, MessageStatus


def build_input(request: AgentRequest) -> list[TResponseInputItem]:
    items: list[TResponseInputItem] = []
    for message in request.history:
        # 跳过失败/取消/未完成的助手消息 —— 把半截回答喂回模型只会误导它。
        if message.role == MessageRole.ASSISTANT and message.status != MessageStatus.COMPLETED:
            continue
        if not message.content.strip():
            continue
        role = "assistant" if message.role == MessageRole.ASSISTANT else "user"
        items.append({"role": role, "content": message.content})
    items.append({"role": "user", "content": request.user_input})
    return items
