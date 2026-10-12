"""OpenTelemetry 接入点（MVP 阶段仅占位）。

按项目根 README 第 3 条，MVP 不实现 OTel / Metric 的具体代码，这里只保留
将来接入的位置和约定，避免以后到处找地方插桩。

接入时需要做的事（技术设计 §19）：

1. 依赖：opentelemetry-sdk、opentelemetry-exporter-otlp、
   opentelemetry-instrumentation-fastapi、-sqlalchemy、-httpx。

2. 在 create_app() 里、注册路由之前调用 setup_tracing(app)：

       FastAPIInstrumentor.instrument_app(app)
       SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
       HTTPXClientInstrumentor().instrument()

3. 关联 ID（§19.2）挂到 span 属性上，与 structlog contextvars 里的同名字段一致：
       trace_id / conversation_id / message_id / agent_run_id / tool_call_id

4. 调 MCP 时把 traceparent 注入 HTTP 头，让 internal-mcp-server 续上同一条 trace。

5. Agent 指标（§19.4）：requests / success_rate / answer_latency / model_latency /
   tool_calls_per_run / tool_error_rate / tokens / cost / max_turn_reached_rate。

注意：OpenAI Agents SDK 自带的 tracing 面向 Agent 调试，不替代这里的系统级链路。
"""

from __future__ import annotations


def setup_tracing(app: object) -> None:
    """MVP 阶段为空实现，保留调用点。"""
    return None
