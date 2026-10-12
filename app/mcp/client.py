"""MCP Client —— 连接 internal-mcp-server。

**每个请求构造一个新实例**，这不是保守，是必须的：
MCPServerStreamableHttp 的 headers 在构造时就固定进 params 里，而
X-Copilot-User-Id 是 per-end-user 的。共享一个实例会让 A 用户的提问带着
B 用户的身份下去 —— 既是越权，也让 MCP 侧的审计和限流失去意义。

Go 侧是 Stateless: true、身份完全从每个 HTTP 请求的 header 取，所以
服务端共享连接本身是安全的；约束纯粹来自客户端这一侧的 API 形状。
后续若要优化掉每轮的建连开销，见 README 里记录的 httpx_client_factory 方案。
"""

from __future__ import annotations

from agents.mcp import MCPServerStreamableHttp

from app.auth.identity import Identity, mcp_headers
from app.config.settings import ModelProfile, Settings

SERVER_NAME = "internal-mcp-server"


def build_mcp_server(
    *,
    settings: Settings,
    profile: ModelProfile,
    identity: Identity,
    session_id: str,
    request_id: str,
) -> MCPServerStreamableHttp:
    return MCPServerStreamableHttp(
        name=SERVER_NAME,
        params={
            # 注意不能带尾斜杠：Go 侧是 mux.Handle("/mcp", ...) 精确路径，
            # /mcp/ 会直接 404。
            "url": settings.mcp.base_url.rstrip("/"),
            "headers": mcp_headers(
                identity=identity,
                session_id=session_id,
                request_id=request_id,
                service_token=settings.mcp.service_token.get_secret_value(),
            ),
            "timeout": settings.mcp.timeout_seconds,
        },
        # 单次 tool 调用的读超时（Runtime Budget，技术设计 §6.5）。
        client_session_timeout_seconds=profile.tool_timeout_seconds,
        # Go 侧把 envelope 同时放进 structuredContent 和 content[0].text
        # （两份字节相同）。不开这个开关，模型会收到两份一样的 JSON。
        use_structured_content=True,
        # 只在本实例内有效，per-request 实例下收益有限，但无害。
        cache_tools_list=True,
        # 不让 SDK 盲重试：是否可重试由 envelope.retryable 表达，
        # 盲重试会把一次下游写放大成多次（当前全是只读，但别养这个习惯）。
        max_retry_attempts=0,
    )
