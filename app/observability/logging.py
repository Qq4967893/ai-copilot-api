"""结构化日志。

stdout + JSON，字段命名与 internal-mcp-server 的 slog.NewJSONHandler 对齐。
stdlib logging（uvicorn / sqlalchemy / alembic）桥接到同一条 processor 链，
避免同一份日志里混着两种格式。
"""

from __future__ import annotations

import logging
import sys

import structlog

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "error": logging.ERROR,
}


def configure_logging(level: str = "info") -> None:
    log_level = _LEVELS.get(level.lower(), logging.INFO)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.EventRenamer("message"),
                structlog.processors.JSONRenderer(),
            ],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(log_level)

    # uvicorn 默认自带 handler，清掉让它走 root，否则 access log 是纯文本。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
