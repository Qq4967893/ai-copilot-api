"""应用配置。

加载顺序（靠前的优先级高）：环境变量 -> JSON 配置文件 -> 字段默认值。
与 internal-mcp-server 的 internal/config 保持一致：配置文件里出现未知键、
或必填项缺失、或取值越界，一律在启动时报错退出，不做静默回退。

环境变量使用双下划线表示嵌套，例如 SERVER__PORT / DATABASE__DSN / MCP__BASE_URL。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

DEFAULT_CONFIG_FILE = "config/config.local.json"
CONFIG_FILE_ENV = "APP_CONFIG_FILE"

# internal-mcp-server 的 auth 中间件用这个正则校验 X-Copilot-* 头，
# 不匹配会直接返回 401。见 internal/auth/service.go。
IDENTITY_VALUE_PATTERN = r"^[A-Za-z0-9_.:@-]{1,128}$"


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8082, ge=1, le=65535)


class DatabaseSettings(BaseModel):
    # SecretStr：DSN 里含密码，避免出现在 repr() 和日志中。
    dsn: SecretStr
    echo: bool = False
    pool_size: int = Field(default=5, ge=1, le=100)
    pool_recycle: int = Field(default=1800, ge=60)


class McpSettings(BaseModel):
    base_url: str = "http://127.0.0.1:8081/mcp"
    service_token: SecretStr = SecretStr("")
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)


class DevUser(BaseModel):
    user_id: str = Field(pattern=IDENTITY_VALUE_PATTERN)
    display_name: str = "dev"
    scopes: list[str] = Field(default_factory=list)


class AuthSettings(BaseModel):
    # MVP 阶段没有登录（见项目根 README 第 4 条），所有请求都使用这个固定用户。
    dev_user: DevUser


class CorsSettings(BaseModel):
    allow_origins: list[str] = Field(default_factory=list)


class LlmSettings(BaseModel):
    """模型服务接入。

    走 OpenAI 兼容端点（自建网关 / Azure / OpenAI 官方都适用），
    只需要 base_url + api_key。
    """

    base_url: str = "https://api.openai.com/v1"
    api_key: SecretStr = SecretStr("")
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)


class ModelProfile(BaseModel):
    """一个模型档位 + 它的 Runtime Budget（技术设计 §6.5 / §6.6）。

    模型和预算都不许硬编码在业务逻辑里，全部从这里取。
    """

    model: str
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_turns: int = Field(default=8, ge=1, le=50)
    agent_timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    tool_timeout_seconds: float = Field(default=20.0, gt=0, le=300)
    max_tool_result_chars: int = Field(default=20000, ge=1000, le=500000)


class ModelSettings(BaseModel):
    active_profile: str = "default"
    profiles: dict[str, ModelProfile]

    @model_validator(mode="after")
    def _active_profile_must_exist(self) -> ModelSettings:
        if self.active_profile not in self.profiles:
            raise ValueError(
                f"active_profile {self.active_profile!r} 不在 profiles 中: {sorted(self.profiles)}"
            )
        return self

    def active(self) -> ModelProfile:
        return self.profiles[self.active_profile]


class AgentSettings(BaseModel):
    # echo: 不调 LLM 的占位实现，供前端开发和测试使用。
    # operations: 真实的 Operations Copilot Agent。
    impl: Literal["echo", "operations"] = "operations"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        extra="forbid",  # 对应 Go 侧的 json.Decoder.DisallowUnknownFields
        nested_model_default_partial_update=True,
    )

    server: ServerSettings = ServerSettings()
    database: DatabaseSettings
    mcp: McpSettings = McpSettings()
    auth: AuthSettings
    cors: CorsSettings = CorsSettings()
    llm: LlmSettings = LlmSettings()
    model: ModelSettings = ModelSettings(profiles={"default": ModelProfile(model="gpt-5")})
    agent: AgentSettings = AgentSettings()
    log_level: Literal["debug", "info", "warn", "error"] = "info"

    @model_validator(mode="after")
    def _operations_agent_needs_credentials(self) -> Settings:
        # 与其等第一个请求打过来才 401，不如启动就说清楚缺什么。
        if self.agent.impl == "operations":
            missing = [
                name
                for name, value in (
                    ("llm.api_key", self.llm.api_key.get_secret_value()),
                    ("mcp.service_token", self.mcp.service_token.get_secret_value()),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    f"agent.impl=operations 需要配置 {', '.join(missing)}"
                    "（或把 agent.impl 设为 echo）"
                )
        return self

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, v: object) -> object:
        return v.lower() if isinstance(v, str) else v

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # 元组顺序即优先级，靠前的赢。
        return (
            init_settings,
            env_settings,
            JsonConfigSettingsSource(settings_cls, json_file=config_file_path()),
        )


def config_file_path() -> Path:
    return Path(os.getenv(CONFIG_FILE_ENV, DEFAULT_CONFIG_FILE))


def load_settings() -> Settings:
    """构造 Settings；失败时打印可读错误并以非零码退出。

    配置文件不存在不算错误 —— 容器部署可以完全依赖环境变量。但必填项
    （database.dsn、auth.dev_user）缺失时仍然会在这里失败。
    """
    try:
        return Settings()  # pyright: ignore[reportCallIssue]
    except ValidationError as exc:
        path = config_file_path()
        hint = f"配置文件 {path}" if path.exists() else f"未找到配置文件 {path}，仅使用环境变量"
        print(f"配置加载失败（{hint}）：", file=sys.stderr)
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"])
            print(f"  {loc}: {err['msg']}", file=sys.stderr)
        raise SystemExit(1) from exc
