"""进程内假 MCP Server。

直接实现 Agents SDK 的 MCPServer 抽象接口，返回真实的 mcp.types.CallToolResult。
这样 SDK 的 structured-content 处理路径（MCPUtil.invoke_mcp_tool 里那段
`json.dumps(structured_content)`）是被真实执行的，而不是被我们绕过 ——
envelope 到 Evidence 的整条映射链路都得到验证。

封装的 envelope 形状与 Go 侧 internal/toolruntime/envelope.go 完全一致，
包括「下游失败也是 HTTP 200 + isError 缺失 + status=ERROR」这个关键特性。
"""

from __future__ import annotations

from typing import Any

from agents.mcp import MCPServer
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool

DEFAULT_TOOLS = [
    MCPTool(
        name="admin_api.deposits.statistics",
        description=(
            "Gets successful deposit totals and per-chain amount, count, and customer "
            "statistics. Amounts are USDT."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "start_time": {"type": "string", "description": "Start date or datetime"},
                "end_time": {"type": "string", "description": "End date or datetime"},
            },
            "required": ["start_time", "end_time"],
            "additionalProperties": False,
        },
    ),
    MCPTool(
        name="admin_api.commission.statistics",
        description=(
            "Gets the number of agents paid commission and masters paid profit sharing "
            "in one period."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "start_time": {"type": "string"},
                "end_time": {"type": "string"},
            },
            "required": ["start_time", "end_time"],
            "additionalProperties": False,
        },
    ),
]

OBSERVED_AT = "2026-09-22T03:14:15.123456Z"


def success_envelope(
    data: Any, resource: str, pagination: dict[str, int] | None = None
) -> dict[str, Any]:
    return {
        "status": "SUCCESS",
        "data": data,
        "source": {
            "service": "admin-api",
            "resource": resource,
            "observed_at": OBSERVED_AT,
        },
        "pagination": pagination,
        "warnings": [],
    }


def error_envelope(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    """下游失败。注意 isError 依然不设置 —— 这正是 Go 侧的行为。"""
    return {
        "status": "ERROR",
        "data": None,
        "source": None,
        "pagination": None,
        "warnings": [],
        "error": {"code": code, "message": message, "retryable": retryable},
    }


class FakeMCPServer(MCPServer):
    """按 tool 名返回预制结果。

    results: tool 名 -> envelope dict（或 CallToolResult，用于测试异常形状）
    raise_on_connect: 模拟 MCP 连不上
    """

    def __init__(
        self,
        results: dict[str, Any] | None = None,
        *,
        tools: list[MCPTool] | None = None,
        use_structured_content: bool = True,
        raise_on_connect: Exception | None = None,
    ) -> None:
        super().__init__(use_structured_content=use_structured_content)
        self.results = results or {}
        self._tools = tools if tools is not None else DEFAULT_TOOLS
        self.raise_on_connect = raise_on_connect
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.connected = False
        self.cleaned_up = False

    @property
    def name(self) -> str:
        return "fake-mcp"

    async def connect(self) -> None:
        if self.raise_on_connect is not None:
            raise self.raise_on_connect
        self.connected = True

    async def cleanup(self) -> None:
        self.cleaned_up = True

    async def list_tools(self, *args: Any, **kwargs: Any) -> list[MCPTool]:
        return list(self._tools)

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None = None, *args: Any, **kwargs: Any
    ) -> CallToolResult:
        self.calls.append((tool_name, arguments))
        configured = self.results.get(tool_name)
        if isinstance(configured, CallToolResult):
            return configured
        if isinstance(configured, BaseException):
            raise configured
        envelope = configured if configured is not None else success_envelope({}, "unknown")
        return _result(envelope)

    async def list_prompts(self, *args: Any, **kwargs: Any) -> Any:
        from mcp.types import ListPromptsResult

        return ListPromptsResult(prompts=[])

    async def get_prompt(self, *args: Any, **kwargs: Any) -> Any:
        from mcp.types import GetPromptResult

        return GetPromptResult(messages=[])


def _result(envelope: dict[str, Any]) -> CallToolResult:
    import json

    # Go 侧同时填 structuredContent 和 content[0].text（字节相同）。
    text = json.dumps(envelope, ensure_ascii=False)
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=envelope,
    )
