"""EchoAgent —— 基础架构阶段的 Agent 占位实现。

它不调用 LLM、不调用 MCP，只把用户输入分片回吐。存在的意义是让
HTTP -> 持久化 -> SSE -> 客户端 这条管道在接入真实 Agent 之前就能被完整验证：
事件顺序、增量落库、中途异常、客户端断开、反压。

接入真实 Agent 时新增一个实现同样 AgentRunner Protocol 的类，替换
create_app() 里的装配即可，HTTP 层不需要改。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from app.agent.protocol import AgentEvent, AgentRequest

DEFAULT_CHUNK_SIZE = 8


class EchoAgent:
    def __init__(self, chunk_size: int = DEFAULT_CHUNK_SIZE, delay_seconds: float = 0.0) -> None:
        self.chunk_size = chunk_size
        self.delay_seconds = delay_seconds

    async def run(self, request: AgentRequest) -> AsyncGenerator[AgentEvent, None]:
        text = f"（echo）{request.user_input}"
        for start in range(0, len(text), self.chunk_size):
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            yield AgentEvent(
                type="message.delta", data={"text": text[start : start + self.chunk_size]}
            )
