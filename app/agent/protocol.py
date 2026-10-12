"""Agent Runtime 接口。

真实 Agent（OpenAI Agents SDK + MCP Tools）和本轮的 EchoAgent 实现同一个
Protocol，HTTP 层只依赖这个接口。事件类型对齐技术设计 §4.6 约定的流式事件。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.auth.identity import Identity
from app.conversation.models import Message

AgentEventType = Literal[
    "message.started",
    "message.delta",
    "message.completed",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "result.data",
    "result.navigation",
    "result.evidence",
    "error",
]


class AgentEvent(BaseModel):
    type: AgentEventType
    data: dict[str, Any] = Field(default_factory=dict)


class AgentRequest(BaseModel):
    """一次 Agent Run 的输入上下文（技术设计 §7.3）。

    entity_context、permission_context 等尚未使用的字段留待后续补充
    （技术设计 §7.5 的实体上下文），补充时不需要改调用方。
    """

    model_config = {"arbitrary_types_allowed": True}

    identity: Identity
    conversation_id: str
    history: list[Message] = Field(default_factory=list)
    user_input: str
    # 贯穿到 MCP 的 X-Copilot-Request-Id，用于跨服务对齐日志（技术设计 §19.2）。
    request_id: str = ""
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"


@runtime_checkable
class AgentRunner(Protocol):
    def run(self, request: AgentRequest) -> AsyncGenerator[AgentEvent, None]:
        """流式执行一次 Agent Run。

        返回异步生成器而非裸 AsyncIterator：调用方（以及客户端断开时的
        Starlette）需要 aclose() 来触发实现里的资源清理。

        实现约定：
        - 不 yield message.started / message.completed —— 那两个事件由 HTTP 层
          负责，因为只有它知道 message_id 和最终落库结果。
        - 内部失败应 yield 一个 error 事件后正常结束，或直接抛异常由 HTTP 层兜底；
          两种都会被转成客户端可见的 error 事件。
        - Tool 失败不得表述成「无数据」（技术设计 §16.2），要发 tool.failed。
        """
        ...
