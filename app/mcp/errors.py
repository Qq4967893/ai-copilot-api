"""MCP 侧失败到本服务 ErrorCode 的映射。

internal-mcp-server 有四层互不相同的失败，必须分开处理（都已在 Go 源码核实）：

1. HTTP 非 2xx、非 JSON-RPC 的响应体
   401 service token 不对 / 缺 delegated identity 头
   429 限流（per-end-user token bucket，body 不是 JSON-RPC）
   415 / 400 / 403 / 405 / 500 来自中间件或 SDK handler
2. JSON-RPC error —— tool 授权失败、tool 未注册。numeric code 通常是 0，
   不要拿数字判断，看消息或直接当硬失败。
3. result.isError = true —— **参数不合法**，此时没有 structuredContent。
4. result 里 envelope.status = "ERROR" —— 下游业务失败，HTTP 200。

第 4 类是最需要小心的：它长得像成功（200 + result），但绝不能被表述成
「没有相关记录」（技术设计 §16.2）。
"""

from __future__ import annotations

from app.errors import ErrorCode

# envelope.error.code 的闭集，来自 Go 侧 adminapi.ErrorKind。
_ENVELOPE_CODES: dict[str, ErrorCode] = {
    "INVALID_ARGUMENT": ErrorCode.INVALID_ARGUMENT,
    "UNAUTHENTICATED": ErrorCode.PERMISSION_DENIED,
    "FORBIDDEN": ErrorCode.PERMISSION_DENIED,
    "RATE_LIMITED": ErrorCode.RATE_LIMITED,
    "UPSTREAM_ERROR": ErrorCode.UPSTREAM_ERROR,
    "UPSTREAM_TIMEOUT": ErrorCode.TIMEOUT,
}

# 下游 admin-api 的 session cookie 过期时会以 UNAUTHENTICATED 回来。
# 这在运维上是「凭据过期」而不是「这个用户没权限」，日志里要能分辨，
# 但对用户来说都是「查不到，不是没数据」。
CREDENTIAL_EXPIRY_CODES = frozenset({"UNAUTHENTICATED"})


def error_code_for_envelope(code: str) -> ErrorCode:
    return _ENVELOPE_CODES.get(code.upper(), ErrorCode.UPSTREAM_ERROR)


def error_code_for_exception(exc: BaseException) -> ErrorCode:
    """把 MCP 客户端/传输层异常映射成 ErrorCode。

    Agents SDK 会把 HTTP 状态码和连接问题都包成 UserError，原始信息只在
    消息文本里，所以这里只能按文本判断 —— 不理想，但比把 401 当成
    「没有数据」好得多。
    """
    text = str(exc).lower()
    if "429" in text or "too many requests" in text:
        return ErrorCode.RATE_LIMITED
    if "401" in text or "unauthenticated" in text:
        return ErrorCode.UNAUTHENTICATED
    if "403" in text or "forbidden" in text:
        return ErrorCode.PERMISSION_DENIED
    if "timeout" in text or "timed out" in text:
        return ErrorCode.TIMEOUT
    return ErrorCode.UPSTREAM_ERROR
