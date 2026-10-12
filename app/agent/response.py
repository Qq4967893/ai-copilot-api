"""Agent Response Contract（技术设计 §23.2）。

最终回答不是一段自由文本，而是一个结构化对象。流式过程中逐步发送事件，
最终在客户端拼成完整的 AgentResponse。

本轮 EchoAgent 只填 answer，但类型先定下来，ai-copilot-web 可以照此实现渲染。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Answer(BaseModel):
    text: str = ""


class ResultBlock(BaseModel):
    """结构化结果，对应前端的表格 / key-value / timeline 渲染。"""

    kind: Literal["table", "key_value", "timeline", "list"]
    title: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class NavigationItem(BaseModel):
    """后台入口深链（技术设计 §13.4）。"""

    label: str
    url: str
    description: str | None = None


EvidenceType = Literal["data", "rule", "source_code", "navigation", "limitation"]


class Evidence(BaseModel):
    """回答依据（技术设计 §15.2）。

    事实数据、业务规则、系统当前实现和模型解释必须可区分 —— type 就是这个区分。
    """

    type: EvidenceType
    source: str
    reference: str | None = None
    version: str | None = None
    observed_at: datetime | None = None
    summary: str | None = None


class Warning(BaseModel):
    """部分失败、数据受限等提示（技术设计 §16.3）。

    Tool 超时或无权限必须通过这里告知，不能静默当成「没有记录」。
    """

    code: str
    message: str


class AgentResponse(BaseModel):
    answer: Answer = Field(default_factory=Answer)
    results: list[ResultBlock] = Field(default_factory=list)
    navigation: list[NavigationItem] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)
