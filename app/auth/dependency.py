"""FastAPI 依赖。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.protocol import AgentRunner
from app.auth.identity import Identity
from app.config.settings import Settings
from app.db.session import get_session


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_current_identity(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Identity:
    """当前请求的用户身份。

    MVP：无登录，返回配置里的固定开发用户。接入真实登录后只替换这个函数体
    （解析 session / JWT / 网关头），所有调用方不需要改动。
    """
    dev_user = settings.auth.dev_user
    return Identity(
        user_id=dev_user.user_id,
        display_name=dev_user.display_name,
        scopes=list(dev_user.scopes),
    )


def get_agent(request: Request) -> AgentRunner:
    return request.app.state.agent


SettingsDep = Annotated[Settings, Depends(get_settings)]
IdentityDep = Annotated[Identity, Depends(get_current_identity)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AgentDep = Annotated[AgentRunner, Depends(get_agent)]
