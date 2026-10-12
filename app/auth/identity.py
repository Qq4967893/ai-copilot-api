"""调用方身份。

MVP 阶段没有登录（项目根 README 第 4 条），身份来自配置里的固定开发用户。
Identity 是唯一的身份载体：往下传给 MCP 时用 delegated user identity 头
（技术设计 §10.3），绝不放大当前运营用户的权限。
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from app.config.settings import IDENTITY_VALUE_PATTERN

_VALUE_RE = re.compile(IDENTITY_VALUE_PATTERN)


class Identity(BaseModel):
    user_id: str
    display_name: str
    scopes: list[str]


def mcp_headers(
    *, identity: Identity, session_id: str, request_id: str, service_token: str
) -> dict[str, str]:
    """构造调用 internal-mcp-server 所需的头。

    internal-mcp-server 的 auth 中间件（internal/auth/service.go）要求这四个头
    都存在，且值匹配 ^[A-Za-z0-9_.:@-]{1,128}$，否则直接 401。这里提前断言，
    把问题暴露在本服务而不是一个语义模糊的上游 401。

    scopes 通过 X-Copilot-Scopes 传递；不传时 MCP 用它自己的 default_permissions。
    """
    for name, value in (
        ("user_id", identity.user_id),
        ("session_id", session_id),
        ("request_id", request_id),
    ):
        if not _VALUE_RE.match(value):
            raise ValueError(f"{name} 不满足 internal-mcp-server 的取值约束: {value!r}")

    headers = {
        "Authorization": f"Bearer {service_token}",
        "X-Copilot-User-Id": identity.user_id,
        "X-Copilot-Session-Id": session_id,
        "X-Copilot-Request-Id": request_id,
    }
    if identity.scopes:
        headers["X-Copilot-Scopes"] = ",".join(identity.scopes)
    return headers
