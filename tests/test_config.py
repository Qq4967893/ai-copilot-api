"""配置加载：优先级、未知键、校验失败。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config.settings import CONFIG_FILE_ENV, Settings, load_settings

BASE_CONFIG = {
    "server": {"host": "127.0.0.1", "port": 8082},
    "database": {"dsn": "mysql+aiomysql://root:@127.0.0.1:3306/ai_copilot"},
    "mcp": {"base_url": "http://127.0.0.1:8081/mcp", "service_token": "t"},
    "auth": {"dev_user": {"user_id": "10001", "scopes": ["admin.api.ping.read"]}},
    # echo 不需要 LLM 凭据；用 operations 的用例单独构造。
    "agent": {"impl": "echo"},
    "log_level": "info",
}


def write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_loads_from_json_file(tmp_path, monkeypatch):
    monkeypatch.setenv(CONFIG_FILE_ENV, str(write_config(tmp_path, BASE_CONFIG)))
    settings = load_settings()
    assert settings.server.port == 8082
    assert settings.auth.dev_user.user_id == "10001"
    assert settings.mcp.service_token.get_secret_value() == "t"


def test_env_overrides_json_file(tmp_path, monkeypatch):
    monkeypatch.setenv(CONFIG_FILE_ENV, str(write_config(tmp_path, BASE_CONFIG)))
    monkeypatch.setenv("SERVER__PORT", "9999")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    settings = load_settings()
    assert settings.server.port == 9999
    assert settings.log_level == "debug"
    # 未被环境变量覆盖的字段仍来自文件
    assert settings.server.host == "127.0.0.1"


def test_secrets_not_in_repr(tmp_path, monkeypatch):
    monkeypatch.setenv(CONFIG_FILE_ENV, str(write_config(tmp_path, BASE_CONFIG)))
    settings = load_settings()
    dumped = repr(settings)
    assert "ai_copilot" not in dumped
    assert "**********" in dumped


def test_unknown_key_is_rejected(tmp_path, monkeypatch):
    bad = {**BASE_CONFIG, "typo_section": {"a": 1}}
    monkeypatch.setenv(CONFIG_FILE_ENV, str(write_config(tmp_path, bad)))
    with pytest.raises(SystemExit) as exc:
        load_settings()
    assert exc.value.code == 1


def test_missing_required_field_exits(tmp_path, monkeypatch):
    incomplete = {k: v for k, v in BASE_CONFIG.items() if k != "database"}
    monkeypatch.setenv(CONFIG_FILE_ENV, str(write_config(tmp_path, incomplete)))
    monkeypatch.delenv("DATABASE__DSN", raising=False)
    with pytest.raises(SystemExit):
        load_settings()


def test_out_of_range_port_exits(tmp_path, monkeypatch):
    monkeypatch.setenv(CONFIG_FILE_ENV, str(write_config(tmp_path, BASE_CONFIG)))
    monkeypatch.setenv("SERVER__PORT", "70000")
    with pytest.raises(SystemExit):
        load_settings()


def test_missing_config_file_is_not_an_error(tmp_path, monkeypatch):
    """容器部署可以完全依赖环境变量，文件缺失不该报错。"""
    monkeypatch.setenv(CONFIG_FILE_ENV, str(tmp_path / "nope.json"))
    monkeypatch.setenv("DATABASE__DSN", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("AUTH__DEV_USER", '{"user_id": "42", "scopes": []}')
    monkeypatch.setenv("AGENT__IMPL", "echo")
    settings = load_settings()
    assert settings.auth.dev_user.user_id == "42"
    assert settings.server.port == 8082  # 默认值


def test_invalid_dev_user_id_rejected(tmp_path, monkeypatch):
    """dev_user.user_id 必须满足 internal-mcp-server 的取值约束。"""
    bad = json.loads(json.dumps(BASE_CONFIG))
    bad["auth"]["dev_user"]["user_id"] = "has space"
    monkeypatch.setenv(CONFIG_FILE_ENV, str(write_config(tmp_path, bad)))
    with pytest.raises(SystemExit):
        load_settings()


def test_log_level_is_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.setenv(CONFIG_FILE_ENV, str(write_config(tmp_path, BASE_CONFIG)))
    monkeypatch.setenv("LOG_LEVEL", "WARN")
    assert load_settings().log_level == "warn"


def test_settings_is_constructible_in_code():
    """不依赖文件也能直接构造（测试固件用得到）。"""
    settings = Settings(
        database={"dsn": "sqlite+aiosqlite:///:memory:"},  # type: ignore[arg-type]
        auth={"dev_user": {"user_id": "1", "scopes": []}},  # type: ignore[arg-type]
    )
    assert settings.log_level == "info"


def test_operations_agent_requires_credentials(tmp_path, monkeypatch):
    """缺凭据时启动就失败，而不是等第一个请求打过来才 401。"""
    bad = json.loads(json.dumps(BASE_CONFIG))
    bad["agent"] = {"impl": "operations"}
    bad["llm"] = {"api_key": ""}
    monkeypatch.setenv(CONFIG_FILE_ENV, str(write_config(tmp_path, bad)))
    with pytest.raises(SystemExit):
        load_settings()


def test_operations_agent_accepts_full_credentials(tmp_path, monkeypatch):
    good = json.loads(json.dumps(BASE_CONFIG))
    good["agent"] = {"impl": "operations"}
    good["llm"] = {"base_url": "https://gw.example/v1", "api_key": "sk-test"}
    monkeypatch.setenv(CONFIG_FILE_ENV, str(write_config(tmp_path, good)))
    settings = load_settings()
    assert settings.agent.impl == "operations"
    assert settings.model.active().model == "gpt-5"


def test_unknown_active_profile_is_rejected(tmp_path, monkeypatch):
    bad = json.loads(json.dumps(BASE_CONFIG))
    bad["model"] = {"active_profile": "fast", "profiles": {"default": {"model": "gpt-5"}}}
    monkeypatch.setenv(CONFIG_FILE_ENV, str(write_config(tmp_path, bad)))
    with pytest.raises(SystemExit):
        load_settings()
