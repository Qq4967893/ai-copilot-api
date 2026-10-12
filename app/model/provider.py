"""模型客户端构造。

两个反直觉但必须这么做的点，都已在 SDK 源码里核实过：

1. 必须用 OpenAIChatCompletionsModel，不能走 Responses API。
   Agents SDK 面向 OpenAI 时默认使用 Responses API，而 OpenAI 兼容网关
   （自建代理 / Azure / 各类聚合服务）通常只实现 /v1/chat/completions。

2. 必须关掉 SDK 自带的 tracing。它默认会把 Agent Run 的输入输出上传到
   OpenAI 的 traces 端点 —— 用我们网关的 key 既会失败刷日志，更重要的是
   那等于把内部业务数据发给第三方，违反技术设计 §17.5。
   系统级可观测性由 OpenTelemetry 负责（见 app/observability/otel.py）。
"""

from __future__ import annotations

from functools import lru_cache

from agents import OpenAIChatCompletionsModel, set_tracing_disabled
from openai import AsyncOpenAI

from app.config.settings import LlmSettings, ModelProfile

set_tracing_disabled(True)


@lru_cache(maxsize=4)
def _client(base_url: str, api_key: str, timeout: float) -> AsyncOpenAI:
    # 复用 client 以复用底层连接池；参数相同就是同一个实例。
    return AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)


def build_model(settings: LlmSettings, profile: ModelProfile) -> OpenAIChatCompletionsModel:
    return OpenAIChatCompletionsModel(
        model=profile.model,
        openai_client=_client(
            settings.base_url,
            settings.api_key.get_secret_value(),
            settings.timeout_seconds,
        ),
    )
