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


def test_missing_required_config_fails_without_secret_leak(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # Use distinctive secret values unlikely to appear elsewhere
    secret_api_hash = "TELEGRAM_HASH_SECRET_aBcDeF123456"
    monkeypatch.setenv("TELEGRAM_API_HASH", secret_api_hash)

    with pytest.raises(ValidationError) as exc_info:
        EngineSettings()

    # Check that the secret does not appear in:
    # 1. The error message strings
    errors = exc_info.value.errors()
    for error in errors:
        msg = str(error.get("msg", ""))
        assert secret_api_hash not in msg, f"Secret leaked in error message: {msg}"
    
    # 2. The string representation of the exception
    exc_str = str(exc_info.value)
    assert secret_api_hash not in exc_str, f"Secret leaked in exception str(): {exc_str}"
    
    # 3. Captured stdout/stderr during exception handling
    captured = capsys.readouterr()
    assert secret_api_hash not in captured.out, f"Secret leaked in stdout: {captured.out}"
    assert secret_api_hash not in captured.err, f"Secret leaked in stderr: {captured.err}"
    
    assert any(e.get("type") == "missing" for e in errors)


def test_init_values_override_environment_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    set_minimum_required_env(monkeypatch)
    monkeypatch.setenv("EXECUTION_MODE", "demo")

    settings = EngineSettings(execution_mode="shadow")

    assert settings.execution_mode == "shadow"


def test_live_trading_rejection_without_secret_leak(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify ALLOW_LIVE_TRADING=true rejection does not leak secrets."""
    secret_password = "TRADELOCKER_PASSWORD_XyZ987654321"
    
    set_minimum_required_env(monkeypatch)
    monkeypatch.setenv("TRADELOCKER_PASSWORD", secret_password)
    monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")

    with pytest.raises(ValidationError) as exc_info:
        EngineSettings()

    # Verify secret does not leak in any error surface
    errors = exc_info.value.errors()
    for error in errors:
        msg = str(error.get("msg", ""))
        assert secret_password not in msg, f"Secret leaked in error: {msg}"
    
    exc_str = str(exc_info.value)
    assert secret_password not in exc_str, f"Secret leaked in exception str(): {exc_str}"
    
    captured = capsys.readouterr()
    assert secret_password not in captured.out, f"Secret leaked in stdout"
    assert secret_password not in captured.err, f"Secret leaked in stderr"


def test_database_url_redacted_in_redacted_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify database credentials are redacted in redacted_dict()."""
    set_minimum_required_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret_db_password@localhost/engine")

    settings = EngineSettings()
    redacted = settings.redacted_dict()

    assert redacted["database_url"] == "***"
    assert "secret_db_password" not in str(redacted)


def test_telegram_session_redacted_in_redacted_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Telegram session is redacted in redacted_dict()."""
    set_minimum_required_env(monkeypatch)
    sensitive_session = "1234567890:AABBCCDDEE_FFGGHHJJ_KKLLMMNNOO"
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", sensitive_session)

    settings = EngineSettings()
    redacted = settings.redacted_dict()

    assert redacted["telegram_session_string"] == "***"
    assert sensitive_session not in str(redacted)
