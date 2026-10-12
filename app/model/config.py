"""Model profile 解析。

Settings 里已经定义并校验了 profile 结构（active_profile 必须存在），
这里只提供取用入口，避免业务代码到处 settings.model.profiles[...] 地摸。
"""

from __future__ import annotations

from app.config.settings import ModelProfile, Settings


def active_profile(settings: Settings) -> ModelProfile:
    return settings.model.active()
