"""从真实的 tool 调用推导 Evidence 和 Warning。

关键设计：Evidence 的来源是**实际发生过的工具调用及其 envelope**，
不是模型自己声称引用了什么。模型可能漏报、错报或编造引用；而 envelope 里的
service / resource / observed_at 是 internal-mcp-server 写进去的事实。
这样得到的 grounding 是可验证的。

同理，Warning 由这一层独立产出。instructions 会教模型区分「查询失败」和
「没有数据」，但那是一层软约束；这一层保证只要 envelope 是 ERROR，
warnings 里就一定有记录 —— 无论模型在回答里说了什么。
两层共同构成技术设计 §16.2 的防线。
"""

from __future__ import annotations

import json
from typing import Any

from app.agent.response import Evidence, Warning
from app.mcp.envelope import ToolEnvelope

MAX_ARG_DIGEST_CHARS = 200
MCP_SERVICE = "internal-mcp-server"

# envelope 形状无法识别时用的错误码。按失败处理而不是按成功 —— 把未知形状
# 当成「查询成功但没数据」正是 §16.2 明令禁止的错误方向。
UNPARSEABLE_CODE = "TOOL_RESULT_UNPARSEABLE"
MAX_TURNS_CODE = "MAX_TURNS_REACHED"


def args_digest(arguments: Any) -> str:
    """把 tool 参数压成一行可读摘要，用于 Evidence.summary。"""
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (json.JSONDecodeError, ValueError):
            return arguments[:MAX_ARG_DIGEST_CHARS]
    try:
        text = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(arguments)
    return text[:MAX_ARG_DIGEST_CHARS]


def evidence_for_success(tool_name: str, envelope: ToolEnvelope, digest: str) -> Evidence:
    source = envelope.source
    summary = f"{tool_name}({digest})" if digest else tool_name
    if envelope.is_empty:
        summary = f"{summary} — 查询成功，结果为空"
    return Evidence(
        type="data",
        source=source.service if source and source.service else MCP_SERVICE,
        reference=source.resource if source and source.resource else tool_name,
        observed_at=source.observed_at if source else None,
        summary=summary,
    )


def failure_for_envelope(
    tool_name: str, envelope: ToolEnvelope, digest: str
) -> tuple[Warning, Evidence]:
    detail = envelope.error
    code = detail.code if detail else "UPSTREAM_ERROR"
    message = detail.message if detail and detail.message else "工具调用失败"
    return (
        Warning(code=code, message=f"{tool_name}: {message}"),
        Evidence(
            type="limitation",
            source=MCP_SERVICE,
            reference=tool_name,
            summary=f"{code}: {message}" + (f" | 参数 {digest}" if digest else ""),
        ),
    )


def failure_for_unparseable(tool_name: str, digest: str) -> tuple[Warning, Evidence]:
    message = "工具返回的结果无法解析，本次查询视为失败"
    return (
        Warning(code=UNPARSEABLE_CODE, message=f"{tool_name}: {message}"),
        Evidence(
            type="limitation",
            source=MCP_SERVICE,
            reference=tool_name,
            summary=f"{UNPARSEABLE_CODE}: {message}" + (f" | 参数 {digest}" if digest else ""),
        ),
    )


def warning_for_envelope_warnings(tool_name: str, envelope: ToolEnvelope) -> list[Warning]:
    """转发 envelope 自带的 warnings。

    当前 Go 侧永远返回 []，但契约里有这个字段，先接上，将来它开始用了
    不用再改这里。
    """
    return [
        Warning(code="TOOL_WARNING", message=f"{tool_name}: {text}")
        for text in envelope.warnings
        if text
    ]


def max_turns_warning(max_turns: int) -> Warning:
    return Warning(
        code=MAX_TURNS_CODE,
        message=f"已达到单次回答的最大步数限制（{max_turns}），回答可能不完整。",
    )
