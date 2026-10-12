"""Operations Copilot Agent —— 真实实现（技术设计 §6）。

实现与 EchoAgent 相同的 AgentRunner Protocol，HTTP 层完全不需要改动。

事件映射（对照 Agents SDK 的 stream_events()，均已在 SDK 源码核实）：

    raw_response_event / response.output_text.delta  -> message.delta
    run_item_stream_event / tool_called              -> tool.started
    run_item_stream_event / tool_output  SUCCESS     -> tool.completed + result.evidence
    run_item_stream_event / tool_output  ERROR       -> tool.failed + result.evidence

tool_output 事件本身不带工具名，只有 raw_item 里的 call_id；所以用
call_id -> tool_name 的映射来配对。并行 tool 调用时顺序不保证，**不能靠顺序配对**。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import structlog
from agents import Agent, Runner
from agents.exceptions import MaxTurnsExceeded
from agents.mcp import MCPServer

from app.agent import evidence as ev
from app.agent.context import build_input
from app.agent.instructions import build_instructions
from app.agent.protocol import AgentEvent, AgentRequest
from app.agent.response import Evidence, Warning
from app.config.settings import Settings
from app.errors import ErrorCode
from app.mcp.client import build_mcp_server
from app.mcp.envelope import parse_tool_output
from app.mcp.errors import (
    CREDENTIAL_EXPIRY_CODES,
    error_code_for_envelope,
    error_code_for_exception,
)
from app.model import active_profile, build_model

log = structlog.get_logger()

AGENT_NAME = "Operations Copilot"

ServerFactory = Callable[[AgentRequest, Any], MCPServer]
ModelFactory = Callable[[Any], Any]


@dataclass
class _PendingCall:
    tool_name: str
    digest: str


class OperationsCopilotAgent:
    """server_factory / model_factory 是测试接缝。

    生产路径用默认值（真实 MCP 连接 + 真实模型）；测试注入假 MCP server 和
    agents.testing.ScriptedModel，从而在不连外部服务、不烧 token 的前提下
    真实执行整条事件映射链路。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        server_factory: ServerFactory | None = None,
        model_factory: ModelFactory | None = None,
    ) -> None:
        self.settings = settings
        self._server_factory = server_factory or self._default_server
        self._model_factory = model_factory or self._default_model

    def _default_server(self, request: AgentRequest, profile: Any) -> MCPServer:
        # session_id 用 conversation_id：MCP 侧要求它匹配
        # ^[A-Za-z0-9_.:@-]{1,128}$，ULID 天然满足。
        return build_mcp_server(
            settings=self.settings,
            profile=profile,
            identity=request.identity,
            session_id=request.conversation_id,
            request_id=request.request_id or request.conversation_id,
        )

    def _default_model(self, profile: Any) -> Any:
        return build_model(self.settings.llm, profile)

    async def run(self, request: AgentRequest) -> AsyncGenerator[AgentEvent, None]:
        profile = active_profile(self.settings)
        server = self._server_factory(request, profile)

        # 用显式 connect/cleanup 而不是 `async with`：__aenter__ 只在
        # MCPServerStreamableHttp 上有，抽象基类 MCPServer 没有，用显式生命周期
        # 才能同时兼容真实实现和测试替身。connect 与 cleanup 在同一个 task 里
        # 成对出现（SDK 对此有要求）。
        connected = False
        try:
            await server.connect()
            connected = True
            async for event in self._run_with_server(server, request, profile):
                yield event
        except asyncio.CancelledError:
            raise
        except MaxTurnsExceeded:
            # 兜底：正常路径由 error handler 处理掉，走到这里说明 SDK 版本
            # 行为有变，不要让它变成一个裸 500。
            yield _warning_event(ev.max_turns_warning(profile.max_turns))
        except TimeoutError:
            # asyncio.timeout 预算超时抛的是消息为空的裸 TimeoutError，
            # error_code_for_exception 按文本匹配不到，会误标成 UPSTREAM_ERROR；
            # 必须在这里显式接住，用户才能看到「超时」而不是「服务不可用」。
            log.warning("agent run budget timed out", timeout_seconds=profile.agent_timeout_seconds)
            yield AgentEvent(
                type="error",
                data={
                    "code": str(ErrorCode.TIMEOUT),
                    "message": _public_message(ErrorCode.TIMEOUT),
                },
            )
        except Exception as exc:
            code = error_code_for_exception(exc)
            log.warning("mcp/agent run failed", error=str(exc), code=str(code))
            yield AgentEvent(
                type="error",
                data={"code": str(code), "message": _public_message(code)},
            )
        finally:
            if connected:
                # 客户端断开时这里跑在生成器 aclose 的展开过程中。允许 await，
                # 但绝不能 yield。清理失败不该盖住原始错误。
                try:
                    await server.cleanup()
                except Exception as exc:  # noqa: BLE001
                    log.warning("mcp cleanup failed", error=str(exc))

    async def _run_with_server(
        self,
        server: MCPServer,
        request: AgentRequest,
        profile: Any,
    ) -> AsyncIterator[AgentEvent]:
        max_turns = profile.max_turns
        hit_max_turns = False

        def _on_max_turns(_: Any) -> dict[str, Any]:
            # 步数用完时不抛异常：把已经拿到的内容作为回答返回，再附一条
            # warning。用户宁愿看到「我查到这些，但步数用完了」，
            # 而不是一个 INTERNAL。
            nonlocal hit_max_turns
            hit_max_turns = True
            return {"final_output": ""}

        agent = Agent(
            name=AGENT_NAME,
            instructions=build_instructions(request),
            model=self._model_factory(profile),
            mcp_servers=[server],
        )
        result = Runner.run_streamed(
            agent,
            input=build_input(request),
            max_turns=max_turns,
            error_handlers={"max_turns": _on_max_turns},
        )

        pending: dict[str, _PendingCall] = {}

        async with asyncio.timeout(profile.agent_timeout_seconds):
            async for event in result.stream_events():
                for mapped in self._map_event(event, pending):
                    yield mapped

        if hit_max_turns:
            yield _warning_event(ev.max_turns_warning(max_turns))

    def _map_event(self, event: Any, pending: dict[str, _PendingCall]) -> list[AgentEvent]:
        kind = getattr(event, "type", None)

        if kind == "raw_response_event":
            data = getattr(event, "data", None)
            if getattr(data, "type", None) == "response.output_text.delta":
                delta = getattr(data, "delta", "")
                if delta:
                    return [AgentEvent(type="message.delta", data={"text": delta})]
            return []

        if kind != "run_item_stream_event":
            return []

        name = getattr(event, "name", None)
        item = getattr(event, "item", None)

        if name == "tool_called":
            return self._on_tool_called(item, pending)
        if name == "tool_output":
            return self._on_tool_output(item, pending)
        return []

    def _on_tool_called(self, item: Any, pending: dict[str, _PendingCall]) -> list[AgentEvent]:
        raw = getattr(item, "raw_item", None)
        call_id = _attr(raw, "call_id") or _attr(raw, "id") or ""
        tool_name = _attr(raw, "name") or getattr(item, "tool_name", None) or "unknown_tool"
        arguments = _attr(raw, "arguments")
        digest = ev.args_digest(arguments)
        if call_id:
            pending[call_id] = _PendingCall(tool_name=tool_name, digest=digest)
        return [
            AgentEvent(
                type="tool.started",
                data={"tool": tool_name, "arguments": _safe_args(arguments)},
            )
        ]

    def _on_tool_output(self, item: Any, pending: dict[str, _PendingCall]) -> list[AgentEvent]:
        raw = getattr(item, "raw_item", None)
        call_id = _attr(raw, "call_id") or ""
        call = pending.pop(call_id, None)
        tool_name = call.tool_name if call else "unknown_tool"
        digest = call.digest if call else ""

        envelope = parse_tool_output(getattr(item, "output", None))

        if envelope is None:
            warning, limitation = ev.failure_for_unparseable(tool_name, digest)
            log.warning("tool result unparseable", tool=tool_name)
            return [
                AgentEvent(
                    type="tool.failed",
                    data={
                        "tool": tool_name,
                        "code": ev.UNPARSEABLE_CODE,
                        "message": warning.message,
                        "retryable": False,
                    },
                ),
                _evidence_event(limitation),
                _warning_event(warning),
            ]

        if envelope.failed:
            warning, limitation = ev.failure_for_envelope(tool_name, envelope, digest)
            detail = envelope.error
            code = detail.code if detail else ""
            log.info(
                "tool failed",
                tool=tool_name,
                code=code,
                mapped=str(error_code_for_envelope(code)),
                # session cookie 过期也是 UNAUTHENTICATED，但运维上要能和
                # 「这个用户没权限」分开看，日志里带上这个标记。
                credential_expired=code.upper() in CREDENTIAL_EXPIRY_CODES,
            )
            return [
                AgentEvent(
                    type="tool.failed",
                    data={
                        "tool": tool_name,
                        "code": detail.code if detail else "UPSTREAM_ERROR",
                        "message": detail.message if detail else "",
                        "retryable": detail.retryable if detail else False,
                    },
                ),
                _evidence_event(limitation),
                _warning_event(warning),
            ]

        source = envelope.source
        events = [
            AgentEvent(
                type="tool.completed",
                data={
                    "tool": tool_name,
                    "empty": envelope.is_empty,
                    "source": source.model_dump(mode="json") if source else None,
                    "pagination": (
                        envelope.pagination.model_dump() if envelope.pagination else None
                    ),
                },
            ),
            _evidence_event(ev.evidence_for_success(tool_name, envelope, digest)),
        ]
        events.extend(
            _warning_event(w) for w in ev.warning_for_envelope_warnings(tool_name, envelope)
        )
        return events


def _evidence_event(item: Evidence) -> AgentEvent:
    return AgentEvent(type="result.evidence", data=item.model_dump(mode="json"))


def _warning_event(item: Warning) -> AgentEvent:
    # warnings 复用 error 之外的通道：它不是终止性错误，只是对回答的限定。
    return AgentEvent(type="result.data", data={"warning": item.model_dump(mode="json")})


def _attr(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _safe_args(arguments: Any) -> Any:
    """工具参数回传前端用于展示执行状态，只做长度保护。

    参数是模型生成的查询条件（日期、user_code 等），不含凭据。
    """
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except (json.JSONDecodeError, ValueError):
            return arguments[: ev.MAX_ARG_DIGEST_CHARS]
    return arguments


def _public_message(code: ErrorCode) -> str:
    return {
        ErrorCode.RATE_LIMITED: "请求过于频繁，请稍后再试",
        ErrorCode.UNAUTHENTICATED: "服务身份校验失败，请联系管理员",
        ErrorCode.PERMISSION_DENIED: "没有访问该数据的权限",
        ErrorCode.TIMEOUT: "查询超时，请稍后再试",
    }.get(code, "查询服务暂时不可用")
