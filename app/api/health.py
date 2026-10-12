"""健康检查。

路径和响应体与 internal-mcp-server 的 internal/httpapi 对齐。
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db.session import session_factory

router = APIRouter(tags=["health"])
log = structlog.get_logger()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """存活探针，不检查任何依赖。"""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> JSONResponse:
    """就绪探针。

    只检查自己的 MySQL。刻意不检查 internal-mcp-server —— 技术设计 §22.4：
    不要因为非关键外部系统短暂异常导致整个 API 无法接收请求。MCP 不可用时
    单次对话会失败并返回明确错误，但服务本身仍应就绪。
    """
    try:
        async with session_factory()() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        log.warning("readiness check failed", error=str(exc))
        return JSONResponse(
            content={"status": "unavailable", "error": "database is unavailable"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return JSONResponse(content={"status": "ready"}, status_code=status.HTTP_200_OK)
