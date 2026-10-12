"""统一错误模型。

响应体与 internal-mcp-server 逐字段一致：{"error": {"code": ..., "message": ...}}
错误码沿用同一套 SCREAMING_SNAKE 词表，Agent 和前端只消费这层稳定语义。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = structlog.get_logger()


class ErrorCode(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    TIMEOUT = "TIMEOUT"
    INTERNAL = "INTERNAL"


_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_ARGUMENT: status.HTTP_400_BAD_REQUEST,
    ErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCode.UNAUTHENTICATED: status.HTTP_401_UNAUTHORIZED,
    ErrorCode.PERMISSION_DENIED: status.HTTP_403_FORBIDDEN,
    ErrorCode.RATE_LIMITED: status.HTTP_429_TOO_MANY_REQUESTS,
    ErrorCode.UPSTREAM_ERROR: status.HTTP_502_BAD_GATEWAY,
    ErrorCode.TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
    ErrorCode.INTERNAL: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


class AppError(Exception):
    def __init__(self, code: ErrorCode, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status or _HTTP_STATUS[code]


class NotFoundError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.NOT_FOUND, message)


class InvalidArgumentError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.INVALID_ARGUMENT, message)


def error_body(code: ErrorCode | str, message: str) -> dict[str, Any]:
    return {"error": {"code": str(code), "message": message}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(content=error_body(exc.code, exc.message), status_code=exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # 只回摘要，不回显原始 body —— 避免把用户输入原样反射到响应里。
        first = exc.errors()[0] if exc.errors() else None
        detail = "request validation failed"
        if first:
            loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
            detail = f"{loc}: {first.get('msg', detail)}" if loc else str(first.get("msg", detail))
        return JSONResponse(
            content=error_body(ErrorCode.INVALID_ARGUMENT, detail),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # 对应 Go 侧的 recoverPanics：堆栈只进日志，响应里是固定文案。
        log.exception("unhandled exception", error=str(exc))
        return JSONResponse(
            content=error_body(ErrorCode.INTERNAL, "internal error"),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
