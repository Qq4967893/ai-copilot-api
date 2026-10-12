"""Request ID 与访问日志中间件。

X-Copilot-Request-Id 是跨服务的关联 ID（internal-mcp-server 也读写同一个头）。
这里读入或生成，绑定到 structlog contextvars，并原样写回响应头。

客户端给的值不可信：不满足 ^[A-Za-z0-9_.:@-]{1,128}$ 的值会一路传到
MCP 的 X-Copilot-Request-Id 被 401，进而把整轮 agent run 打成失败。
所以这里统一校验，不合法就换成新生成的 ULID，并改写 scope 里的请求头，
让下游 handler（如 messages.py）读到的也是净化后的值。
"""

from __future__ import annotations

import re
import time

import structlog
from starlette.types import ASGIApp
from ulid import ULID

from app.config.settings import IDENTITY_VALUE_PATTERN

REQUEST_ID_HEADER = "X-Copilot-Request-Id"
_HEADER_NAME = REQUEST_ID_HEADER.lower().encode()
_VALUE_RE = re.compile(IDENTITY_VALUE_PATTERN)

log = structlog.get_logger()


class RequestContextMiddleware:
    """纯 ASGI 中间件。

    不用 BaseHTTPMiddleware —— 它会把响应包进一层 anyio task group，
    在 StreamingResponse 上会干扰客户端断开的取消传播。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        raw = headers.get(_HEADER_NAME)
        request_id = raw.decode("latin-1") if raw else ""
        if not _VALUE_RE.match(request_id):
            if raw is not None:
                log.info("request id replaced: client value did not pass validation")
            request_id = str(ULID())
            # ASGI 头名必须是小写字节串；scope["headers"] 可能是元组列表，
            # 重建一份再赋值。
            remaining = [(k, v) for k, v in (scope.get("headers") or []) if k != _HEADER_NAME]
            remaining.append((_HEADER_NAME, request_id.encode("latin-1")))
            scope["headers"] = remaining

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message) -> None:  # noqa: ANN001
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", [])
                message["headers"].append(
                    (REQUEST_ID_HEADER.encode(), request_id.encode("latin-1"))
                )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            log.info(
                "request",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            structlog.contextvars.clear_contextvars()


__all__ = ["REQUEST_ID_HEADER", "RequestContextMiddleware"]
