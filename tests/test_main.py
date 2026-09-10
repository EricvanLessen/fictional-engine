from __future__ import annotations

from collections.abc import Iterator

import pytest

from fictional_engine.main import main


@pytest.fixture(autouse=True)
def clear_env_main(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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


def test_main_startup_with_missing_config_no_secret_leak(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify application startup fails gracefully without leaking secrets."""
    secret_telegram_hash = "TELEGRAM_HASH_VERY_SECRET_789XYZ"
    monkeypatch.setenv("TELEGRAM_API_HASH", secret_telegram_hash)

    exit_code = main()

    assert exit_code == 1, "Expected exit code 1 for missing config"

    captured = capsys.readouterr()
    stderr = captured.err + captured.out

    assert secret_telegram_hash not in stderr, f"Secret leaked in output: {stderr}"
    assert "[CONFIGURATION ERROR]" in stderr, "Expected configuration error message"


def test_main_startup_with_live_trading_flag_no_secret_leak(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify live trading rejection at startup doesn't leak secrets."""
    secret_password = "TRADELOCKER_SECRET_PASSWORD_9999ZZZ"
    
    def set_required_env() -> None:
        monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "1234")
        monkeypatch.setenv("TELEGRAM_API_ID", "99999")
        monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
        monkeypatch.setenv("TRADELOCKER_BASE_URL", "https://demo.example.com")
        monkeypatch.setenv("TRADELOCKER_USERNAME", "user")
        monkeypatch.setenv("TRADELOCKER_PASSWORD", secret_password)
        monkeypatch.setenv("TRADELOCKER_ACCOUNT_ID", "acct")
        monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
        monkeypatch.setenv("ALLOW_LIVE_TRADING", "true")

    set_required_env()

    exit_code = main()

    assert exit_code == 1, "Expected exit code 1 for live trading attempt"

    captured = capsys.readouterr()
    stderr = captured.err + captured.out

    assert secret_password not in stderr, f"Secret leaked in output: {stderr}"
