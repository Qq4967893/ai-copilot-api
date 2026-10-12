"""按配置选择 Agent 实现。

echo       不调 LLM 的占位实现。前端开发、CI 和大部分测试都用它 ——
           确定性、不烧 token、不依赖外部服务。
operations 真实的 Operations Copilot Agent。
"""

from __future__ import annotations

from app.agent.echo import EchoAgent
from app.agent.operations import OperationsCopilotAgent
from app.agent.protocol import AgentRunner
from app.config.settings import Settings


def build_agent(settings: Settings) -> AgentRunner:
    if settings.agent.impl == "echo":
        return EchoAgent()
    return OperationsCopilotAgent(settings)
