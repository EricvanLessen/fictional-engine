from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from fictional_engine.config.settings import EngineSettings


@pytest.fixture(autouse=True)
def clear_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    keys = [
        "EXECUTION_MODE",
        "ALLOW_LIVE_TRADING",
        "TELEGRAM_CHANNEL_ID",
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_SESSION_STRING",
        "TRADELOCKER_BASE_URL",
        "TRADELOCKER_USERNAME",
        "TRADELOCKER_PASSWORD",
        "TRADELOCKER_ACCOUNT_ID",
        "DATABASE_URL",
        "LOG_LEVEL",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield


def set_minimum_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_ID", "99999")
    monkeypatch.setenv("TELEGRAM_API_HASH", "top-secret-hash")
    monkeypatch.setenv("TRADELOCKER_BASE_URL", "https://demo.example.com")
    monkeypatch.setenv("TRADELOCKER_USERNAME", "demo-user")
    monkeypatch.setenv("TRADELOCKER_PASSWORD", "demo-password")
    monkeypatch.setenv("TRADELOCKER_ACCOUNT_ID", "acct-1")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./local.db")


def test_defaults_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    set_minimum_required_env(monkeypatch)

    settings = EngineSettings()

    assert settings.execution_mode == "shadow"
    assert settings.allow_live_trading is False


def test_rejects_allow_live_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    set_minimum_required_env(monkeypatch)
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")

    with pytest.raises(ValidationError, match="ALLOW_LIVE_TRADING=true"):
        EngineSettings()


def test_missing_required_config_fails_without_secret_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_API_HASH", "my-very-secret-value")

    with pytest.raises(ValidationError) as exc_info:
        EngineSettings()

    errors = exc_info.value.errors()
    for error in errors:
        msg = str(error.get("msg", ""))
        assert "my-very-secret-value" not in msg, "Secret leaked in error message"
    
    assert any(e.get("type") == "missing" for e in errors)


def test_init_values_override_environment_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    set_minimum_required_env(monkeypatch)
    monkeypatch.setenv("EXECUTION_MODE", "demo")

    settings = EngineSettings(execution_mode="shadow")

    assert settings.execution_mode == "shadow"
